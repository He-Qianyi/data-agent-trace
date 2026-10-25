"""Read-only recovery rules: explicit recording text and constant script writes."""
import ast
import posixpath
import re
from .sources import child


def recorded_cwd(records):
    """Only trust the provider's initial instructions field, never task/tool text."""
    row, ref = records[0]
    instructions = row.get("model_input", {}).get("instructions")
    if not isinstance(instructions, str):
        return None, None
    matches = list(re.finditer(r"(?m)^The current working directory is `([^`\n]+)`", instructions))
    if len(matches) != 1 or not matches[0].group(1).startswith("/"):
        return None, None
    match = matches[0]
    return match.group(1), {**child(ref, "/model_input/instructions"),
                            "char_start": match.start(1), "char_end": match.end(1)}


def resolve_directory(path, base):
    if not isinstance(path, str) or any(c in path for c in "$`~\n"):
        return None
    if path.startswith("/"):
        return posixpath.normpath(path)
    return posixpath.normpath(posixpath.join(base, path)) if base else None


def invocation_cwd(step, invocation, trace_cwd):
    args = step["action"].get("arguments")
    args = args if isinstance(args, dict) else {}
    base = args.get("workdir", args.get("cwd", trace_cwd))
    base = resolve_directory(base, trace_cwd) if isinstance(base, str) else trace_cwd
    if "command_cwd_as_written" in invocation:
        return resolve_directory(invocation["command_cwd_as_written"], base)
    return base


def python_script_writes(code, source_ref, literal):
    """Top-level constant writes only; no branches, loops, functions or execution.

    Each with-open block must have exactly one direct write and no other effects.
    Assignments are interpreted in source order and reassignment invalidates an
    unknown value. Only .py/.sql definitions are promoted into version tracking.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError):
        return []
    constants, writes = {}, []

    def value(node):
        return literal(node, constants)

    def opened(call):
        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "open":
            return None
        keywords = {k.arg: k.value for k in call.keywords if k.arg}
        path_node = call.args[0] if call.args else keywords.get("file")
        mode_node = call.args[1] if len(call.args) > 1 else keywords.get("mode")
        path = value(path_node) if path_node else None
        mode = value(mode_node) if mode_node else "r"
        return path if isinstance(path, str) and mode in ("w", "wt") else None

    def record(path, call):
        if not isinstance(path, str) or not path.endswith((".py", ".sql")) or not call.args:
            return
        content = value(call.args[0])
        if isinstance(content, str):
            writes.append({"kind": "write", "path": path, "content": content,
                           "source_ref": source_ref, "evidence_type": "python_constant_script_write",
                           "content_expression": ast.get_source_segment(code, call.args[0]),
                           "code_line_start": call.lineno, "statement_execution_verified": False})

    for node in tree.body:
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                constants[node.targets[0].id] = value(node.value)
            else:
                for target in node.targets:
                    for item in ast.walk(target):
                        if isinstance(item, ast.Name):
                            constants.pop(item.id, None)
        elif isinstance(node, ast.With) and len(node.items) == 1 and len(node.body) == 1:
            item, statement = node.items[0], node.body[0]
            call = statement.value if isinstance(statement, ast.Expr) else None
            if (isinstance(item.optional_vars, ast.Name) and isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute) and call.func.attr == "write"
                    and isinstance(call.func.value, ast.Name) and call.func.value.id == item.optional_vars.id):
                record(opened(item.context_expr), call)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if not isinstance(call.func, ast.Attribute):
                continue
            receiver = call.func.value
            if call.func.attr == "write":
                record(opened(receiver), call)
            elif (call.func.attr == "write_text" and isinstance(receiver, ast.Call)
                  and isinstance(receiver.func, ast.Name) and receiver.func.id == "Path" and len(receiver.args) == 1):
                record(value(receiver.args[0]), call)
        else:
            # Do not carry bindings across unsupported control flow or mutations.
            for item in ast.walk(node):
                if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)):
                    constants.pop(item.id, None)
    return writes
