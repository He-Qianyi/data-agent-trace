"""Conservative static code extraction; never execute captured shell/Python/SQL."""
import ast
import hashlib
import json
import re
import shlex
import warnings

from .sources import child

HEREDOC = re.compile(r"(?m)^(?P<head>[^\n]*?)<<(?P<dash>-?)[ \t]*(?:'(?P<sq>[^']+)'|\"(?P<dq>[^\"]+)\"|(?P<bare>[A-Za-z_][\w]*))[^\n]*\n")
SQL_START = re.compile(r"^\s*(?:--[^\n]*\n\s*)*(SELECT|WITH|PRAGMA|SHOW|DESCRIBE|EXPLAIN|CREATE|INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|COPY)\b", re.I)


def argument_ref(call, key, start=None, end=None):
    ref = call["arguments_ref"]
    if isinstance(call["arguments_raw"], str):
        ref = {**ref, "decode_json_string": True, "decoded_pointer": "/" + key}
    else:
        ref = child(ref, "/" + key)
    if start is not None:
        ref = {**ref, "char_start": start, "char_end": end}
    return ref


def sha_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def literal(node, constants):
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = literal(node.left, constants), literal(node.right, constants)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        return None
    try:
        value = ast.literal_eval(node)
        json.dumps(value, allow_nan=False)
        return value
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return None


def spelling(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return spelling(node.value) + "." + node.attr
    return "<expression>"


def python_facts(code):
    """Lexical API facts, not claims that each statement reached execution."""
    try:
        with warnings.catch_warnings(record=True) as syntax_warnings:
            warnings.simplefilter("always", SyntaxWarning)
            tree = ast.parse(code)
    except (SyntaxError, ValueError, RecursionError) as exc:
        return [], {"status": "unparsed", "reason": type(exc).__name__}
    constants, binding_lines = {}, {}
    stores = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            stores[n.id] = stores.get(n.id, 0) + 1
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if stores.get(name) == 1:
                constants[name] = literal(node.value, constants)
                binding_lines[name] = node.lineno
    ops = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        api = spelling(node.func)
        name = api.rsplit(".", 1)[-1]
        available = {k: v for k, v in constants.items() if binding_lines[k] < node.lineno}
        value = literal(node.args[0], available) if node.args else None
        kwargs = {k.arg: literal(k.value, available) for k in node.keywords if k.arg}
        op = {"api_spelling": api, "expression": ast.get_source_segment(code, node),
              "code_line_start": node.lineno, "code_line_end": node.end_lineno,
              "evidence_type": "python_ast_syntax", "statement_execution_verified": False}
        if name == "open" and isinstance(value, str):
            mode = literal(node.args[1], available) if len(node.args) > 1 else kwargs.get("mode", "r")
            op.update(operation="data_write_api" if isinstance(mode, str) and any(c in mode for c in "wax+") else "file_open_api",
                      path_literal=value, mode_literal=mode, options_literal=kwargs)
        elif name in ("read_csv", "read_excel", "read_parquet", "read_json", "read_feather", "read_table", "loadtxt", "genfromtxt"):
            op.update(operation="data_read_api", path_literal=value if isinstance(value, str) else None,
                      options_literal=kwargs)
        elif name in ("to_csv", "to_excel", "to_parquet", "to_json", "savefig"):
            op.update(operation="data_write_api", path_literal=value if isinstance(value, str) else None,
                      options_literal=kwargs)
        elif name == "connect" and isinstance(value, str):
            op.update(operation="connection_api", path_literal=value, options_literal=kwargs,
                      engine_verified=False)
        elif name in ("execute", "executemany", "executescript", "sql", "read_sql", "read_sql_query", "read_sql_table"):
            if isinstance(value, str) and not SQL_START.match(value) and name not in ("read_sql_table",):
                continue
            op.update(operation="sql_api", sql_text=value if isinstance(value, str) else None,
                      sql_expression=ast.get_source_segment(code, node.args[0]) if node.args else None,
                      parameters_expression=ast.get_source_segment(code, node.args[1]) if len(node.args) > 1 else None,
                      parameter_values_evaluated=False, individual_result_binding="not_recorded_separately")
        elif name in ("groupby", "merge", "join", "dropna", "fillna", "pivot_table", "agg", "aggregate", "sort_values", "drop_duplicates", "quantile", "hist"):
            op.update(operation="data_transform_api", first_argument_literal=value, options_literal=kwargs)
        else:
            continue
        ops.append(op)
    return sorted(ops, key=lambda x: (x["code_line_start"], x["code_line_end"])), {"status": "parsed", "syntax_warning_count": len(syntax_warnings)}


def shell_facts(command, ref):
    units, files, runs = [], [], []
    masked = list(command)
    for match in HEREDOC.finditer(command):
        delimiter = match.group("sq") or match.group("dq") or match.group("bare")
        end = re.search(r"(?m)^" + (r"\t*" if match.group("dash") else "") + re.escape(delimiter) + r"[ \t]*\r?$", command[match.end():])
        if not end:
            continue
        start, stop = match.end(), match.end() + end.start()
        body = command[start:stop]
        head = match.group("head")
        target = re.search(r"(?<!>)>(?!>)[ \t]*(?:'([^']+)'|\"([^\"]+)\"|([^\s;]+))", head)
        # Many commands place the redirection after the delimiter token.
        if target is None:
            target = re.search(r"(?<!>)>(?!>)[ \t]*(?:'([^']+)'|\"([^\"]+)\"|([^\s;]+))", command[match.start():match.end()])
        path = next((g for g in target.groups() if g is not None), None) if target else None
        language = "python" if re.search(r"\bpython(?:3(?:\.\d+)?)?\b", head) or (path and path.endswith(".py")) else "shell_heredoc_text"
        context = "file_definition" if path else "submitted_inline_program"
        unit = {"language": language, "code": body, "source_ref": {**ref, "char_start": start, "char_end": stop},
                "context": context, "path_as_written": path}
        units.append(unit)
        if path:
            files.append({"kind": "write", "path": path, "content": body, "source_ref": unit["source_ref"],
                          "evidence_type": "shell_heredoc_redirection", "shell_expansion_possible": bool(match.group("bare"))})
        for i in range(match.start(), match.end() + end.end()):
            masked[i] = " " if command[i] != "\n" else "\n"
    # shlex respects quoted -c programs; splitting arbitrary text with regex would invent invocations.
    try:
        lexer = shlex.shlex("".join(masked), posix=True, punctuation_chars=";&|\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return units, files, runs, "unparsed_shell_quoting"
    boundaries = {";", "&&", "||", "|", "\n", "&"}
    for i, token in enumerate(tokens):
        if not (i == 0 or tokens[i-1] in boundaries or (i > 0 and re.match(r"^[A-Za-z_]\w*=", tokens[i-1]))):
            continue
        exe = token.rsplit("/", 1)[-1]
        tail = []
        for x in tokens[i+1:]:
            if x in boundaries:
                break
            tail.append(x)
        if re.fullmatch(r"python(?:3(?:\.\d+)?)?", exe):
            if tail and tail[0] == "-c" and len(tail) >= 2:
                units.append({"language": "python", "code": tail[1], "source_ref": ref,
                              "context": "submitted_inline_program", "extraction": "shell_quoted_argument_decoding"})
            elif tail and not tail[0].startswith("-"):
                runs.append({"kind": "python_script_invocation", "path": tail[0], "source_ref": ref,
                             "evidence_type": "shell_token_sequence"})
        if exe in ("sqlite3", "duckdb"):
            sql = next((x for x in tail if SQL_START.match(x)), None)
            units.append({"language": "sql", "code": sql, "source_ref": ref, "context": "submitted_cli_sql",
                          "client_as_written": exe, "command_arguments_as_written": tail,
                          "extraction": "shell_quoted_argument_decoding", "individual_result_binding": "enclosing_tool_result_only"})
    return units, files, runs, "parsed_conservative_shell_patterns"


def extract_action(call):
    args = call["arguments"]
    result = {"code_units": [], "file_actions": [], "script_invocations": [], "data_operations": []}
    if not isinstance(args, dict):
        result["extraction_status"] = "arguments_not_a_parsed_object"
        return result
    name = call["tool_name"]
    if name in ("Bash", "background", "exec_command"):
        key = "cmd" if name == "exec_command" else "command"
        cmd = args.get(key)
        if isinstance(cmd, str):
            result["code_units"].append({"language": "shell", "code": cmd, "source_ref": argument_ref(call, key),
                                         "context": "submitted_command"})
            units, files, runs, status = shell_facts(cmd, argument_ref(call, key))
            result["code_units"].extend(units)
            result["file_actions"].extend(files)
            result["script_invocations"].extend(runs)
            result["extraction_status"] = status
    elif name in ("Write", "Edit", "Read"):
        path = args.get("file_path", args.get("path"))
        if isinstance(path, str):
            file = {"kind": name.lower(), "path": path, "source_ref": call["arguments_ref"], "evidence_type": "explicit_tool_arguments"}
            if name == "Write":
                key = "content" if "content" in args else "new_string"
                if isinstance(args.get(key), str):
                    file["content"] = args[key]
                    if path.endswith((".py", ".sql")):
                        result["code_units"].append({"language": "python" if path.endswith(".py") else "sql", "code": args[key],
                                                     "source_ref": argument_ref(call, key), "context": "file_definition", "path_as_written": path})
            if name == "Edit":
                file.update({k: args.get(k) for k in ("old_string", "new_string", "replace_all")})
            result["file_actions"].append(file)
    elif name == "write_stdin":
        # Input may target any interactive process. Do not invent a Python or shell language.
        if isinstance(args.get("chars"), str):
            result["code_units"].append({"language": "unknown_interactive_input", "code": args["chars"],
                                         "source_ref": argument_ref(call, "chars"), "context": "submitted_stdin"})
    for i, unit in enumerate(result["code_units"], 1):
        unit["code_id"] = f"C{i:02d}"
        if unit["language"] == "python":
            operations, status = python_facts(unit["code"])
            unit["static_parse"] = status
            for op in operations:
                op.update(code_ref=unit["code_id"], code_context=unit["context"], result_scope="enclosing_tool_result_only")
            result["data_operations"].extend(operations)
        elif unit["language"] == "sql":
            result["data_operations"].append({"operation": "sql_source", "sql_text": unit["code"], "code_ref": unit["code_id"],
                                               "code_context": unit["context"], "statement_execution_verified": False,
                                               "result_scope": "enclosing_tool_result_only"})
    result.setdefault("extraction_status", "explicit_arguments_only")
    return result
