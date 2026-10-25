"""Bounded syntax-only string recovery; never evaluate captured Python code.

Only straight-line module statements supply new bindings. Branch/loop/function
bodies do not contribute values. Existing v3 literal extraction is independent.
"""
import ast
import posixpath

MAX_STRING = 1_000_000
DB_MODULES = {'psycopg2', 'psycopg', 'sqlite3', 'duckdb', 'pymysql',
              'mysql.connector', 'mariadb', 'pg8000'}


def dotted(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = dotted(node.value)
        return prefix + '.' + node.attr if prefix else None
    return None


def string_value(node, bindings, imports, depth=0):
    """Return a known string or None; a Call is only recognized path.join syntax."""
    if depth > 32:
        return None
    def val(n):
        return string_value(n, bindings, imports, depth + 1)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        result = node.value
    elif isinstance(node, ast.Name):
        result = bindings.get(node.id)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = val(node.left), val(node.right)
        result = a + b if isinstance(a, str) and isinstance(b, str) and len(a) + len(b) <= MAX_STRING else None
    elif isinstance(node, ast.JoinedStr):
        parts = []
        for item in node.values:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                parts.append(item.value)
            elif isinstance(item, ast.FormattedValue) and item.conversion == -1 and item.format_spec is None:
                value = val(item.value)
                if not isinstance(value, str):
                    return None
                parts.append(value)
            else:
                return None
        result = ''.join(parts) if sum(map(len, parts)) <= MAX_STRING else None
    elif isinstance(node, ast.Call) and not node.keywords and node.args:
        name = imported_name(node.func, imports)
        parts = [val(n) for n in node.args]
        result = posixpath.join(*parts) if name in {'os.path.join', 'posixpath.join'} and all(isinstance(p, str) for p in parts) else None
    else:
        result = None
    return result if isinstance(result, str) and len(result) <= MAX_STRING else None


def imported_name(node, imports):
    name = dotted(node)
    if not name:
        return None
    root, *tail = name.split('.')
    base = imports.get(root)
    return '.'.join([base, *tail]) if base else None


def imports_from(statement):
    if isinstance(statement, ast.Import):
        return {a.asname or a.name.split('.')[0]: a.name if a.asname else a.name.split('.')[0] for a in statement.names}
    if isinstance(statement, ast.ImportFrom) and statement.module:
        return {a.asname or a.name: statement.module + '.' + a.name for a in statement.names if a.name != '*'}
    return {}


def assigned_names(node):
    """Over-invalidate unsupported control flow rather than speculate its branch."""
    names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        names.add(node.name)
    return names


def direct_calls(node):
    """Skip deferred/local scopes, comprehensions, and expressions with walrus."""
    if any(isinstance(n, ast.NamedExpr) for n in ast.walk(node)):
        return
    def walk(n):
        if isinstance(n, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
                          ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return
        if isinstance(n, ast.Call):
            yield n
        for child in ast.iter_child_nodes(n):
            yield from walk(child)
    yield from walk(node)


def call_string_environments(tree):
    """Associate each eligible call with bindings before that statement executes."""
    bindings, imports, environments = {}, {}, {}
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for key, value in imports_from(node).items():
                bindings.pop(key, None)
                imports[key] = value
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)):
            calls = list(direct_calls(node))
            reflective = any(dotted(c.func) in {'exec', 'eval', 'globals', 'locals', 'vars'} for c in calls)
            if not reflective:
                for call in calls:
                    environments[id(call)] = (bindings.copy(), imports.copy())
            new_value = string_value(node.value, bindings, imports) if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value else None
            if reflective:
                bindings.clear()
                imports.clear()
            if isinstance(node, ast.Assign):
                for name in assigned_names(node):
                    bindings.pop(name, None)
                    imports.pop(name, None)
                if all(isinstance(t, ast.Name) for t in node.targets):
                    for target in node.targets:
                        if new_value is not None:
                            bindings[target.id] = new_value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                bindings.pop(node.target.id, None)
                imports.pop(node.target.id, None)
                if new_value is not None:
                    bindings[node.target.id] = new_value
        else:
            for name in assigned_names(node):
                bindings.pop(name, None)
                imports.pop(name, None)
            # A branch may redefine an imported module even without a Name(Store).
            for child in ast.walk(node):
                for name in imports_from(child):
                    bindings.pop(name, None)
                    imports.pop(name, None)
    return environments


class DatabaseCalls:
    """Recognize imported DB API spellings, without asserting import/execution.

    Local parameters/assignments and ambiguous module aliases disable recognition.
    This supports lexical calls inside functions and branches as syntax only.
    """
    def __init__(self, tree):
        self.parents = {id(c): p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        self.module = tree
        imports, ambiguous = {}, set()
        for node in tree.body:
            for key, value in imports_from(node).items():
                if key in imports and imports[key] != value:
                    ambiguous.add(key)
                imports[key] = value
            if not isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                ambiguous.update(assigned_names(node))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                ambiguous.add(node.name)
        self.ambiguous = ambiguous
        self.imports = {k: v for k, v in imports.items() if k not in ambiguous}

    def driver(self, call):
        imports = self.imports.copy()
        blocked = self.ambiguous.copy()
        parent = self.parents.get(id(call))
        while parent is not None and parent is not self.module:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                args = parent.args
                local = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
                if args.vararg: local.add(args.vararg.arg)
                if args.kwarg: local.add(args.kwarg.arg)
                local.update(assigned_names(parent))
                blocked.update(local)
                for name in local: imports.pop(name, None)
            parent = self.parents.get(id(parent))
        name = imported_name(call.func, imports)
        if name and name.endswith('.connect') and name[:-8] in DB_MODULES:
            return name[:-8]
        # An unresolved canonical driver name is still visible source syntax.
        # Record that spelling with engine_verified=false; it may raise NameError.
        raw = dotted(call.func)
        if raw and raw.endswith('.connect') and raw[:-8] in DB_MODULES:
            root = raw.split('.')[0]
            if root not in blocked and root not in imports:
                return raw[:-8]
        # Literal __import__('driver').connect is recorded syntax, never executed.
        if isinstance(call.func, ast.Attribute) and call.func.attr == 'connect':
            receiver = call.func.value
            if (isinstance(receiver, ast.Call) and isinstance(receiver.func, ast.Name)
                    and receiver.func.id == '__import__' and len(receiver.args) == 1
                    and isinstance(receiver.args[0], ast.Constant) and receiver.args[0].value in DB_MODULES
                    and '__import__' not in {n.id for n in ast.walk(self.module) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}):
                return receiver.args[0].value
        return None
