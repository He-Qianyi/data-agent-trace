"""Conservative static code extraction; never execute captured shell/Python/SQL."""
import ast
import hashlib
import json
import re
import shlex
import warnings

from .sources import child
from .recovery import python_script_writes
from .constants import call_string_environments, string_value, DatabaseCalls

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
    if isinstance(node, ast.JoinedStr) and all(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in node.values):
        return "".join(v.value for v in node.values)
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
    contexts = call_string_environments(tree)
    databases = DatabaseCalls(tree)
    ops = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        api = spelling(node.func)
        name = api.rsplit(".", 1)[-1]
        available = {k: v for k, v in constants.items() if binding_lines[k] < node.lineno}
        value = literal(node.args[0], available) if node.args else None
        kwargs = {k.arg: literal(k.value, available) for k in node.keywords if k.arg}
        context = contexts.get(id(node))
        recovered = string_value(node.args[0], *context) if node.args and context else None
        driver = databases.driver(node)
        op = {"api_spelling": api, "expression": ast.get_source_segment(code, node),
              "code_line_start": node.lineno, "code_line_end": node.end_lineno,
              "evidence_type": "python_ast_syntax", "statement_execution_verified": False}
        if name == "open" and isinstance(value, str):
            mode = literal(node.args[1], available) if len(node.args) > 1 else kwargs.get("mode", "r")
            op.update(operation="data_write_api" if isinstance(mode, str) and any(c in mode for c in "wax+") else "file_open_api",
                      path_literal=value, mode_literal=mode, options_literal=kwargs)
        elif name in ("read_csv", "read_excel", "read_parquet", "read_json", "read_feather", "read_table", "loadtxt", "genfromtxt"):
            op.update(operation="data_read_api", path_literal=value if isinstance(value, str) else recovered,
                      options_literal=kwargs)
        elif name in ("to_csv", "to_excel", "to_parquet", "to_json", "savefig"):
            op.update(operation="data_write_api", path_literal=value if isinstance(value, str) else recovered,
                      options_literal=kwargs)
        elif (name == "connect" and isinstance(value, str)) or driver is not None:
            if isinstance(value, str):
                op.update(operation="connection_api", path_literal=value, options_literal=kwargs, engine_verified=False)
            else:
                # Preserve dynamic keyword syntax and unknown values. A DB name is
                # not a file path or a fabricated DSN. File-backed drivers may use
                # their documented database keyword as a path argument.
                raw_keywords = {k.arg: ast.get_source_segment(code, k.value) for k in node.keywords if k.arg}
                options = {k.arg: literal(k.value, {}) for k in node.keywords if k.arg}
                if context:
                    for k in node.keywords:
                        if k.arg and options.get(k.arg) is None:
                            options[k.arg] = string_value(k.value, *context)
                # Positional database/DSN has precedence. Do not fall back to a
                # keyword when that positional expression remains unknown.
                path = recovered if node.args else (options.get("database") if driver in {"sqlite3", "duckdb"} else options.get("dsn"))
                op.update(operation="connection_api", path_literal=path if isinstance(path, str) else None,
                          options_literal=options, engine_verified=False,
                          connection_driver_as_written=driver, connection_parameters_as_written=raw_keywords,
                          connection_identity_scope="lexical_driver_spelling_not_runtime_binding")
                if any(k.arg is None for k in node.keywords):
                    op["expanded_keyword_arguments_as_written"] = [ast.get_source_segment(code, k.value) for k in node.keywords if k.arg is None]
        elif name in ("execute", "executemany", "executescript", "sql", "read_sql", "read_sql_query", "read_sql_table"):
            if isinstance(value, str) and not SQL_START.match(value) and name not in ("read_sql_table",):
                continue
            op.update(operation="sql_api", sql_text=value if isinstance(value, str) else recovered,
                      sql_expression=ast.get_source_segment(code, node.args[0]) if node.args else None,
                      parameters_expression=ast.get_source_segment(code, node.args[1]) if len(node.args) > 1 else None,
                      parameter_values_evaluated=False, individual_result_binding="not_recorded_separately")
        elif name in ("groupby", "merge", "join", "dropna", "fillna", "pivot_table", "agg", "aggregate", "sort_values", "drop_duplicates", "quantile", "hist"):
            op.update(operation="data_transform_api", first_argument_literal=value, options_literal=kwargs)
        else:
            continue
        if recovered is not None and not isinstance(value, str):
            field = "sql_text" if op["operation"] == "sql_api" else "path_literal" if op["operation"] in {"data_read_api", "data_write_api", "connection_api"} else None
            if field and op.get(field) == recovered:
                op["constant_resolution"] = {field: {"method": "bounded_source_order_strings",
                                                      "expression": ast.get_source_segment(code, node.args[0])}}
        ops.append(op)
    return sorted(ops, key=lambda x: (x["code_line_start"], x["code_line_end"])), {"status": "parsed", "syntax_warning_count": len(syntax_warnings)}


def _shell_operators(text):
    """Locate unquoted shell syntax, without evaluating any captured command.

    Keep syntax separate from decoded words: a quoted '\n\n' or ';' argument
    must never become a command boundary after shlex removes its quotes.
    This is a conservative simple-command scanner, not a shell interpreter.
    """
    i, quote, word_start = 0, None, True
    while i < len(text):
        c = text[i]
        if c == "\\" and quote != "'":
            if i + 1 < len(text) and text[i + 1] == "\n":
                yield "continuation", i, i + 2
            i += 2
            word_start = False
            continue
        if quote:
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"`":
            quote, word_start = c, False
            i += 1
        elif c == "#" and word_start:
            end = text.find("\n", i)
            if end < 0:
                return
            i = end
        elif c == "\n" or c in ";&|":
            # Each real separator is recognized, including adjacent newlines.
            end = i + 1
            if c in ";&|" and end < len(text) and text[end] == c:
                end += 1
            yield "boundary", i, end
            i, word_start = end, True
        elif text.startswith("<<", i):
            yield "heredoc", i, i + 2
            i += 2
            word_start = True
        else:
            word_start = c in " \t\r"
            i += 1


def _shell_commands(text, with_boundaries=False):
    """Decode words only after actual shell command boundaries are known."""
    operators = list(_shell_operators(text))
    commands, pieces, start = [], [], 0
    for kind, left, right in operators:
        if kind == "heredoc":
            continue
        pieces.append(text[start:left])
        if kind == "boundary":
            # comments=True retains the legacy parser's comment convention.
            words = shlex.split("".join(pieces), comments=True, posix=True)
            if words:
                commands.append((words, text[left:right]))
            pieces = []
        # A backslash-newline is removed only in the lexical view, not sources.
        start = right
    pieces.append(text[start:])
    words = shlex.split("".join(pieces), comments=True, posix=True)
    if words:
        commands.append((words, None))
    return commands if with_boundaries else [words for words, _ in commands]


def shell_facts(command, ref):
    units, files, runs = [], [], []
    masked = list(command)
    for match in HEREDOC.finditer(command):
        operator = command.find("<<", match.start(), match.end())
        if not any(kind == "heredoc" and left == operator
                   for kind, left, _ in _shell_operators("".join(masked)[:operator + 2])):
            # A heredoc-like string inside quotes/comments is data, not a write.
            continue
        delimiter = match.group("sq") or match.group("dq") or match.group("bare")
        end = re.search(r"(?m)^" + (r"\t*" if match.group("dash") else "") + re.escape(delimiter) + r"[ \t]*\r?$", command[match.end():])
        if not end:
            continue
        start, stop = match.end(), match.end() + end.start()
        body = command[start:stop]
        head = match.group("head")
        try:
            lexer = shlex.shlex(command[match.start():match.end()], posix=True, punctuation_chars="<>&|;")
            lexer.whitespace_split = True
            tokens = list(lexer)
            path = next((tokens[i + 1] for i, token in enumerate(tokens[:-1]) if token == ">"), None)
        except ValueError:
            path = None
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
    # Split on source-level syntax before decoding quotes. Never split decoded
    # arguments on newline/semicolon strings: those may be literal program data.
    try:
        commands = _shell_commands("".join(masked), with_boundaries=True)
    except ValueError:
        return units, files, runs, "unparsed_shell_quoting"
    command_cwd = None
    for tokens, boundary in commands:
        i = 0
        while i < len(tokens) and re.match(r"^[A-Za-z_]\w*=", tokens[i]):
            i += 1
        if i == len(tokens):
            continue
        token = tokens[i]
        exe = token.rsplit("/", 1)[-1]
        tail = tokens[i+1:]
        if exe == "cd":
            if boundary == "&&" and len(tail) == 1 and not any(c in tail[0] for c in "$`~"):
                import posixpath
                command_cwd = posixpath.normpath(posixpath.join(command_cwd or "", tail[0]))
            else:
                command_cwd = None
        if re.fullmatch(r"python(?:3(?:\.\d+)?)?", exe):
            if tail and tail[0] == "-c" and len(tail) >= 2:
                units.append({"language": "python", "code": tail[1], "source_ref": ref,
                              "context": "submitted_inline_program", "extraction": "shell_quoted_argument_decoding"})
            elif tail and not tail[0].startswith("-"):
                runs.append({"kind": "python_script_invocation", "path": tail[0], "source_ref": ref,
                             "evidence_type": "shell_token_sequence"})
                if command_cwd is not None:
                    runs[-1].update(command_cwd_as_written=command_cwd, cwd_binding_basis="preceding_cd_guarded_by_and")
        if exe in ("sqlite3", "duckdb"):
            sql = next((x for x in tail if SQL_START.match(x)), None)
            units.append({"language": "sql", "code": sql, "source_ref": ref, "context": "submitted_cli_sql",
                          "client_as_written": exe, "command_arguments_as_written": tail,
                          "extraction": "shell_quoted_argument_decoding", "individual_result_binding": "enclosing_tool_result_only"})
        if boundary not in ("&&", None):
            command_cwd = None
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
            if unit.get("context") == "submitted_inline_program":
                for file in python_script_writes(unit["code"], unit["source_ref"], literal):
                    file["code_ref"] = unit["code_id"]
                    result["file_actions"].append(file)
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
