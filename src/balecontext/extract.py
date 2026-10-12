"""Deterministic parsers. Inferred call targets are never presented as runtime facts."""
from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath


@dataclass
class Node:
    id: str
    path: str
    name: str
    kind: str
    start: int
    end: int
    snippet: str

    def dict(self) -> dict:
        return asdict(self)


@dataclass
class Extraction:
    nodes: list[Node] = field(default_factory=list)
    refs: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    parser: str = "text-chunks"
    warnings: list[str] = field(default_factory=list)


def make_node(path: str, name: str, kind: str, start: int, end: int, lines: list[str]) -> Node:
    identity = hashlib.sha256(f"{path}\0{name}\0{kind}\0{start}".encode()).hexdigest()[:24]
    return Node(identity, path, name, kind, start, max(start, end), "\n".join(lines[start - 1:min(end, start + 79)]))


def file_id(path: str) -> str:
    return hashlib.sha256(f"{path}\0{path}\0file\0{1}".encode()).hexdigest()[:24]


def edge(source: str, target: str, relation: str, evidence: str = "EXTRACTED", line: int | None = None) -> dict:
    return {"source": source, "target": target, "relation": relation, "evidence": evidence, "line": line}


def _base(path: str, text: str) -> Extraction:
    lines = text.splitlines() or [""]
    return Extraction(nodes=[make_node(path, path, "file", 1, len(lines), lines)])


def _constructed(function: ast.AST) -> dict[str, str]:
    """Local names bound once, to a class call: `utility = ManagementUtility(argv)` -> ManagementUtility.

    A name assigned again, deleted, used as a loop target or taken as a parameter is left out.
    """
    stores: dict[str, int] = {}
    built: dict[str, str] = {}
    arguments = function.args
    for argument in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs, arguments.vararg, arguments.kwarg]:
        if argument is not None:
            stores[argument.arg] = 1
    queue = list(function.body)
    while queue:
        child = queue.pop()
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue  # nested scopes bind their own names
        if isinstance(child, ast.Name) and not isinstance(child.ctx, ast.Load):
            stores[child.id] = stores.get(child.id, 0) + 1
        elif isinstance(child, ast.Assign) and len(child.targets) == 1 and isinstance(child.targets[0], ast.Name) and isinstance(child.value, ast.Call):
            callee = ast.unparse(child.value.func)
            if re.fullmatch(r"(?:[A-Za-z_]\w*\.)*_?[A-Z]\w*", callee) and not callee.rsplit(".", 1)[-1].isupper():
                built[child.targets[0].id] = callee
        queue.extend(ast.iter_child_nodes(child))
    return {name: callee for name, callee in built.items() if stores.get(name) == 1}


def text_extract(path: str, text: str) -> Extraction:
    result = _base(path, text)
    lines = text.splitlines() or [""]
    for start in range(1, len(lines) + 1, 60):
        end = min(start + 59, len(lines))
        heading = next((s.lstrip("# ") for s in lines[start - 1:end] if s.startswith("#")), f"lines {start}-{end}")
        node = make_node(path, heading[:120], "chunk", start, end, lines)
        result.nodes.append(node)
        result.edges.append(edge(result.nodes[0].id, node.id, "contains"))
    return result


class PythonExtractor(ast.NodeVisitor):
    def __init__(self, path: str, text: str):
        self.path = path
        self.lines = text.splitlines() or [""]
        self.result = _base(path, text)
        self.result.parser = "python-ast"
        self.stack = [self.result.nodes[0]]
        self.scopes: list[str] = []
        self.aliases: dict[str, tuple[str, str]] = {}
        self.class_stack: list[str] = []
        # Per function: locals bound once to a class call, so `utility.execute()` names its class,
        # and parameter names (`self.get_response = get_response` only keeps what was passed in).
        self.constructed: list[dict[str, str]] = [{}]
        self.parameters: list[set[str]] = [set()]

    def _definition(self, node: ast.AST, kind: str) -> None:
        name = ".".join(self.scopes + [node.name])
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        item = make_node(self.path, name, kind, start, node.end_lineno, self.lines)
        self.result.nodes.append(item)
        self.result.edges.append(edge(self.stack[-1].id, item.id, "defines"))
        if kind == "class":
            for base in node.bases:
                self._ref(ast.unparse(base), "inherits", item.id, node.lineno)
            self.class_stack.append(name)
        self.stack.append(item)
        self.scopes.append(node.name)
        self.generic_visit(node)
        self.scopes.pop()
        self.stack.pop()
        if kind == "class":
            self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        arguments = node.args
        self.constructed.append(_constructed(node))
        self.parameters.append({a.arg for a in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs, arguments.vararg, arguments.kwarg] if a})
        self._definition(node, "method" if self.class_stack else "function")
        self.constructed.pop()
        self.parameters.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.constructed.append({})
        self.parameters.append(set())
        self._definition(node, "class")
        self.constructed.pop()
        self.parameters.pop()

    def _stores(self, targets: list[ast.AST], value: ast.AST, line: int) -> None:
        if self.class_stack and self.stack[-1].kind == "class" and isinstance(value, (ast.Name, ast.Attribute)):
            # `request_class = WSGIRequest` in a class body: `self.request_class(environ)` builds a WSGIRequest.
            for target in targets:
                if isinstance(target, ast.Name):
                    self._ref(ast.unparse(value), "binds", self.stack[-1].id, line)
                    self.result.refs[-1]["attr"] = target.id
        elif self.class_stack and not (isinstance(value, ast.Name) and value.id in self.parameters[-1]):
            # `self._middleware_chain = handler`: a later `self._middleware_chain(request)` runs what this
            # method built. Storing an argument as it came (`self.get_response = get_response`) says nothing more.
            for target in targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id in {"self", "cls"}:
                    self._ref(f"{target.value.id}.{target.attr}", "sets", self.stack[-1].id, line)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._stores(node.targets, node.value, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._stores([node.target], node.value, node.lineno)
        self.generic_visit(node)

    def _ref(self, name: str, relation: str, source: str, line: int) -> None:
        self.result.refs.append({
            "source": source, "name": name, "relation": relation, "line": line,
            "scope": ".".join(self.scopes), "class_name": self.class_stack[-1] if self.class_stack else "",
        })

    def visit_Call(self, node: ast.Call) -> None:
        name = ast.unparse(node.func)
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id in self.constructed[-1]:
            # `utility.execute()` after `utility = ManagementUtility(argv)` calls ManagementUtility.execute.
            name = self.constructed[-1][node.func.value.id] + "." + node.func.attr
        self._ref(name, "calls", self.stack[-1].id, node.lineno)
        if isinstance(node.func, ast.Attribute):
            # The callee attribute is already recorded as a call, not as a property use.
            self.visit(node.func.value)
        else:
            self.visit(node.func)
        for child in [*node.args, *node.keywords]:
            self.visit(child)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # `self.errors` on a property runs code; resolution keeps it only when the
        # attribute is a method or property defined on the same class.
        if isinstance(node.ctx, ast.Load) and isinstance(node.value, ast.Name) and node.value.id in {"self", "cls"} and self.class_stack:
            self._ref(f"{node.value.id}.{node.attr}", "uses", self.stack[-1].id, node.lineno)
        self.generic_visit(node)

    def _import(self, module: str, symbol: str, alias: str, line: int) -> None:
        # Local imports remain scope-specific; they must not leak into other functions.
        self.result.refs.append({"source": self.stack[-1].id, "name": alias, "relation": "imports", "module": module, "symbol": symbol, "scope": ".".join(self.scopes), "line": line})

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._import(alias.name, "", alias.asname or alias.name.split(".")[0], node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self._import("." * node.level + (node.module or ""), alias.name, alias.asname or alias.name, node.lineno)


def javascript_extract(path: str, text: str) -> Extraction:
    try:
        from tree_sitter import Language, Parser
        if path.endswith((".ts", ".tsx")):
            import tree_sitter_typescript as grammar
            language = grammar.language_tsx() if path.endswith(".tsx") else grammar.language_typescript()
        else:
            import tree_sitter_javascript as grammar
            language = grammar.language()
    except ImportError:
        result = text_extract(path, text)
        result.warnings.append("Install balecontext[code] for JavaScript/TypeScript structural parsing.")
        return result
    tree = Parser(Language(language)).parse(text.encode("utf-8"))
    result = _base(path, text)
    result.parser = "tree-sitter"
    if tree.root_node.has_error:
        result.warnings.append("Parser found syntax errors; structural results may be incomplete.")
    lines = text.splitlines() or [""]

    def value(node) -> str:
        return node.text.decode("utf-8") if node else ""

    def walk(node, owner: Node, scope: list[str], class_name: str = "") -> None:
        kind = {
            "function_declaration": "function", "generator_function_declaration": "function",
            "class_declaration": "class", "method_definition": "method",
            "interface_declaration": "interface", "type_alias_declaration": "type",
        }.get(node.type)
        functions = {"arrow_function", "function_expression", "function"}
        name_node = node.child_by_field_name("name")
        if node.type == "variable_declarator":
            body = node.child_by_field_name("value")
            if body and body.type in functions:
                kind = "function"
        elif node.type in {"public_field_definition", "field_definition"}:
            # `fetch = (request) => ...` inside a class body.
            body = node.child_by_field_name("value")
            name_node = name_node or node.child_by_field_name("property")
            if body and body.type in functions and class_name:
                kind = "method"
        elif node.type == "assignment_expression" and not scope:
            # `res.send = function send(body) {...}`, `View.prototype.lookup = ...`,
            # `exports.setCharset = ...` and chains such as `res.set = res.header = function`.
            targets, right = [node.child_by_field_name("left")], node.child_by_field_name("right")
            while right is not None and right.type == "assignment_expression":
                targets.append(right.child_by_field_name("left"))
                right = right.child_by_field_name("right")
            if right is not None and right.type in functions:
                statement = node.parent if node.parent is not None and node.parent.type == "expression_statement" else node
                names = []
                for target in targets:
                    if target is None or target.type not in {"member_expression", "identifier"}:
                        continue
                    parts = [p for p in value(target).split(".") if p != "prototype"]
                    while parts and parts[0] in {"module", "exports"} and len(parts) > 1:
                        parts = parts[1:]
                    if parts and all(re.fullmatch(r"[A-Za-z_$#][A-Za-z0-9_$]*", p) for p in parts):
                        names.append(".".join(parts))
                if names:
                    # The innermost target holds the function; outer targets are aliases.
                    for alias in names[:-1]:
                        result.nodes.append(make_node(path, alias, "method" if "." in alias else "function", statement.start_point.row + 1, statement.end_point.row + 1, lines))
                        result.edges.append(edge(owner.id, result.nodes[-1].id, "defines"))
                    name = names[-1]
                    item = make_node(path, name, "method" if "." in name else "function", statement.start_point.row + 1, statement.end_point.row + 1, lines)
                    result.nodes.append(item)
                    result.edges.append(edge(owner.id, item.id, "defines"))
                    walk(right, item, name.split("."), name.rsplit(".", 1)[0] if "." in name else "")
                    return
        if kind and name_node:
            name = value(name_node)
            qualified = ".".join(scope + [name])
            item = make_node(path, qualified, kind, node.start_point.row + 1, node.end_point.row + 1, lines)
            result.nodes.append(item)
            result.edges.append(edge(owner.id, item.id, "defines"))
            owner, scope = item, scope + [name]
            if kind == "class":
                class_name = qualified
        if node.type == "import_statement":
            module = value(node.child_by_field_name("source")).strip("\"'")
            result.refs.append({"source": owner.id, "name": module, "module": module, "symbol": "", "relation": "imports", "scope": ".".join(scope), "line": node.start_point.row + 1})
            def bindings(child):
                if child.type == "import_specifier":
                    imported = value(child.child_by_field_name("name"))
                    alias = value(child.child_by_field_name("alias")) or imported
                    result.refs.append({"source": owner.id, "name": alias, "module": module, "symbol": imported, "relation": "imports", "scope": ".".join(scope), "line": child.start_point.row + 1})
                for descendant in child.named_children:
                    bindings(descendant)
            bindings(node)
        if node.type == "call_expression":
            result.refs.append({"source": owner.id, "name": value(node.child_by_field_name("function")), "relation": "calls", "scope": ".".join(scope), "class_name": class_name, "line": node.start_point.row + 1})
        for child in node.named_children:
            walk(child, owner, scope, class_name)
    walk(tree.root_node, result.nodes[0], [])
    return result


def extract(path: str, text: str) -> Extraction:
    if path.endswith((".py", ".pyi")):
        try:
            visitor = PythonExtractor(path, text)
            visitor.visit(ast.parse(text))
            return _complete_text_coverage(visitor.result, path, text)
        except (SyntaxError, RecursionError) as exc:
            result = text_extract(path, text)
            result.warnings.append(f"Python parse failed ({type(exc).__name__}); indexed as text.")
            return result
    if path.endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")):
        return _complete_text_coverage(javascript_extract(path, text), path, text)
    return text_extract(path, text)


def _complete_text_coverage(result: Extraction, path: str, text: str) -> Extraction:
    # Long definitions and top-level statements must remain searchable beyond the
    # 80-line symbol excerpt. These chunks are exact source, not summaries.
    lines = text.splitlines()
    if result.parser != "text-chunks" and len(lines) > 80:
        for start in range(1, len(lines) + 1, 60):
            end = min(start + 59, len(lines))
            result.nodes.append(make_node(path, f"{path}:lines {start}-{end}", "chunk", start, end, lines))
    return result


COMMON_METHODS = {
    "get", "set", "items", "keys", "values", "append", "extend", "insert", "pop", "remove", "update", "add", "clear",
    "copy", "discard", "setdefault", "join", "split", "strip", "format", "replace", "startswith", "endswith", "lower",
    "upper", "encode", "decode", "read", "write", "close", "open", "push", "shift", "slice", "splice", "map", "filter",
    "forEach", "reduce", "find", "some", "every", "includes", "indexOf", "has", "delete", "then", "catch", "toString",
    "call", "apply", "bind", "emit", "on", "send", "next", "log", "debug", "info", "warning", "error", "exception",
    "intersection", "union", "difference", "symmetric_difference", "issubset", "issuperset", "count", "index", "sort",
    "reverse",
}


def _test_path(path: str) -> bool:
    parts = PurePosixPath(path.lower()).parts
    return bool(set(parts[:-1]) & {"tests", "test", "__tests__", "spec", "specs", "testing"}) or parts[-1].startswith("test_") or ".test." in parts[-1] or ".spec." in parts[-1]


def resolve(nodes: list[dict], refs: list[dict]) -> list[dict]:
    """Resolve only unambiguous local modules and lexical symbols; preserve uncertainty."""
    files = {n["path"]: n for n in nodes if n["kind"] == "file"}
    by_id = {n["id"]: n for n in nodes}
    symbols: dict[tuple[str, str], list[str]] = {}
    modules: dict[str, set[str]] = {}
    global_names: dict[str, list[str]] = {}
    # Method names the project itself defines, minus container and string methods that
    # attribute chains mostly reach (`self.fields.items()`, `this.headers.get()`).
    project_leaves = {n["name"].rsplit(".", 1)[-1] for n in nodes if n["kind"] in {"method", "function"} and not _test_path(n["path"])} - COMMON_METHODS
    methods_by_leaf: dict[str, list[str]] = {}
    for node in nodes:
        if node["kind"] == "method" and not _test_path(node["path"]):
            methods_by_leaf.setdefault(node["name"].rsplit(".", 1)[-1], []).append(node["id"])
    for node in nodes:
        symbols.setdefault((node["path"], node["name"]), []).append(node["id"])
        if node["kind"] not in {"file", "chunk"} and "." in node["name"] and not _test_path(node["path"]):
            global_names.setdefault(node["name"], []).append(node["id"])
    for path in files:
        if not path.endswith((".py", ".pyi")):
            continue
        module = path.rsplit(".", 1)[0].replace("/", ".")
        if module.endswith(".__init__"):
            module = module[:-9]
        modules.setdefault(module, set()).add(path)
        if module.startswith(("src.", "lib.")):
            modules.setdefault(module.split(".", 1)[1], set()).add(path)

    def target_module(path: str, module: str) -> str | None:
        if path.endswith((".py", ".pyi")):
            if module.startswith("."):
                level = len(module) - len(module.lstrip("."))
                parent = PurePosixPath(path).parent.parts
                prefix = parent[:len(parent) - level + 1] if level <= len(parent) + 1 else ()
                module = ".".join((*prefix, module.lstrip("."))).strip(".")
            targets = modules.get(module, set())
            return next(iter(targets)) if len(targets) == 1 else None
        if not module.startswith("."):
            return None  # package imports and tsconfig aliases are not guessed
        import posixpath
        stem = posixpath.normpath(posixpath.join(str(PurePosixPath(path).parent), module))
        candidates = [stem] + [stem + ext for ext in (".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js")]
        if stem.endswith(".js"):
            candidates.append(stem[:-3] + ".ts")
        hits = [p for p in candidates if p in files]
        return hits[0] if hits else None

    def symbol(path: str, name: str) -> str | None:
        hits = symbols.get((path, name), [])
        return hits[0] if len(hits) == 1 else None

    bindings: dict[tuple[str, str, str], tuple[str, str]] = {}
    # Names a file imports, resolved or not: `re.compile()` is a module call, never a project method.
    imported_names = {(by_id[r["source"]]["path"], r["name"]) for r in refs if r["relation"] == "imports"}
    imports = [ref for ref in refs if ref["relation"] == "imports"]
    for ref in imports:
        path = by_id[ref["source"]]["path"]
        target = target_module(path, ref["module"])
        imported_symbol = ref.get("symbol", "")
        if imported_symbol and path.endswith((".py", ".pyi")) and not (target and symbol(target, imported_symbol)):
            # `from django.core.handlers import base` and `from . import sessions` name a submodule,
            # unless the package itself defines that name.
            submodule = target_module(path, ref["module"] + ("" if ref["module"].endswith(".") else ".") + imported_symbol)
            if submodule:
                target, imported_symbol = submodule, ""
        if target:
            bindings[(path, ref.get("scope", ""), ref["name"])] = (target, imported_symbol)

    def exported(path: str, name: str) -> str | None:
        """A name a module defines or re-exports: `from .base import Model` in a package's
        `__init__.py` makes `models.Model` the class in base.py (followed through four modules)."""
        for _ in range(4):
            found = symbol(path, name)
            if found or not name:
                return found
            head, _, rest = name.partition(".")
            binding = bindings.get((path, "", head))
            if not binding:
                return None
            path, name = binding[0], ".".join(filter(None, [binding[1], rest]))
        return None

    edges: list[dict] = []
    for ref in imports:
        path = by_id[ref["source"]]["path"]
        binding = bindings.get((path, ref.get("scope", ""), ref["name"]))
        if binding:
            destination = exported(*binding) if binding[1] else files[binding[0]]["id"]
            if destination:
                edges.append(edge(ref["source"], destination, "imports", line=ref.get("line")))

    def lexical(ref: dict) -> str | None:
        """A name defined in this file's enclosing scopes or bound by an import."""
        path = by_id[ref["source"]]["path"]
        scope_parts = ref.get("scope", "").split(".") if ref.get("scope") else []
        head, _, suffix = ref["name"].partition(".")
        for length in range(len(scope_parts), -1, -1):
            scope = ".".join(scope_parts[:length])
            found = symbol(path, ".".join(filter(None, [scope, ref["name"]])))
            binding = bindings.get((path, scope, head))
            if not found and binding:
                found = exported(binding[0], ".".join(filter(None, [binding[1], suffix])))
            if found:
                return found
        return None

    # Project base classes of each class, in declaration order, for `self.render()` defined on a base.
    bases: dict[str, list[str]] = {}
    for ref in refs:
        if ref["relation"] == "inherits":
            base = lexical(ref)
            if base and by_id[base]["kind"] == "class" and base != ref["source"]:
                bases.setdefault(ref["source"], []).append(base)

    def inherited(class_id: str | None, attr: str) -> str | None:
        queue, seen = list(bases.get(class_id, [])) if class_id else [], set()
        while queue:
            base = queue.pop(0)
            if base in seen:
                continue
            seen.add(base)
            found = symbol(by_id[base]["path"], by_id[base]["name"] + "." + attr)
            if found:
                return found
            queue += bases.get(base, [])
        return None

    def lineage(class_id: str | None) -> list[str]:
        """The class, then its project base classes nearest first."""
        order, queue = [], [class_id] if class_id else []
        while queue:
            current = queue.pop(0)
            if current not in order:
                order.append(current)
                queue += bases.get(current, [])
        return order

    # `request_class = WSGIRequest` in a class body, by (class, attribute).
    class_values: dict[tuple[str, str], str] = {}
    # Methods that store `self._middleware_chain = handler`, by (class, attribute).
    setters: dict[tuple[str, str], list[str]] = {}
    for ref in refs:
        if ref["relation"] == "binds":
            value = lexical(ref)
            if value and value != ref["source"] and by_id[value]["kind"] in {"class", "function", "method"}:
                class_values[(ref["source"], ref["attr"])] = value
        elif ref["relation"] == "sets" and ref.get("class_name"):
            owner = symbol(by_id[ref["source"]]["path"], ref["class_name"])
            methods = setters.setdefault((owner, ref["name"].split(".", 1)[1]), []) if owner else []
            if owner and ref["source"] not in methods and by_id[ref["source"]]["kind"] == "method":
                methods.append(ref["source"])

    for ref in refs:
        if ref["relation"] in {"imports", "binds", "sets"}:
            continue
        path = by_id[ref["source"]]["path"]
        name = ref["name"]
        scope_parts = ref.get("scope", "").split(".") if ref.get("scope") else []
        destination = None
        assigned: list[str] = []
        if name.startswith(("self.", "cls.", "this.")) and ref.get("class_name"):
            attr = name.split(".", 1)[1]
            destination = symbol(path, ref["class_name"] + "." + attr)
            if not destination and "." not in attr:
                # A method the class inherits from a project base class (`self.render()` in a mixin).
                classes = lineage(symbol(path, ref["class_name"]))
                destination = inherited(classes[0] if classes else None, attr)
                if not destination and ref["relation"] == "calls":
                    # `self.request_class(environ)` where a class body says `request_class = WSGIRequest`.
                    destination = next((class_values[(c, attr)] for c in classes if (c, attr) in class_values), None)
                if not destination and ref["relation"] == "calls":
                    # `self._middleware_chain(request)` runs what `load_middleware` stored there, so the
                    # call is linked to the methods that set the attribute (ASSIGNED, not a direct call).
                    assigned = [m for m in next((setters[(c, attr)] for c in classes if (c, attr) in setters), []) if m != ref["source"]]
        for length in range(len(scope_parts), -1, -1):
            scope = ".".join(scope_parts[:length])
            if destination:
                break
            destination = symbol(path, ".".join(filter(None, [scope, name])))
            if destination and length and "." not in name and by_id[destination]["kind"] == "method":
                # A bare call inside a method never means a method of the class (that needs self./this.).
                destination = None
            head, _, suffix = name.partition(".")
            binding = bindings.get((path, scope, head))
            if not destination and binding:
                target, imported = binding
                qualified = ".".join(filter(None, [imported, suffix]))
                destination = exported(target, qualified)
        if not destination and name.count(".") == 1 and not name.startswith(("self.", "cls.", "this.")):
            # `ManagementUtility.execute` (from `utility = ManagementUtility(argv)`) defined on a base class.
            owner_class = lexical(ref | {"name": name.split(".")[0]})
            if owner_class and by_id[owner_class]["kind"] == "class":
                destination = inherited(owner_class, name.split(".")[1])
        if not destination and "." in name and not name.startswith(("self.", "cls.", "this.")):
            # `app.render` from response.js: a dotted name defined exactly once in the project's code.
            hits = global_names.get(name, [])
            if len(hits) == 1:
                destination = hits[0]
        if not destination and ref.get("class_name") and name.count(".") == 1 and not name.startswith(("self.", "cls.", "this.")):
            # `clone._filter_or_exclude_inplace()` inside QuerySet: a local object calling a method
            # that only this class defines. Labelled INFERRED like every resolved call.
            head, attr = name.split(".")
            # `kwargs.get("stream")` inside Session is a dict call, not Session.get.
            if attr not in COMMON_METHODS and not any(bindings.get((path, ".".join(scope_parts[:length]), head)) for length in range(len(scope_parts), -1, -1)):
                destination = symbol(path, ref["class_name"] + "." + attr)
                if destination == ref["source"]:
                    destination = None
        leaf = name.rsplit(".", 1)[-1]
        candidates: list[str] = []
        if "." in name and not destination and (path, name.split(".", 1)[0]) not in imported_names:
            # `self.query.chain()` means Query.chain, not RawQuery.chain; `adapter.send()` means an
            # *Adapter class's send even though `send` is a common name. Otherwise only a name few
            # project methods share counts.
            defined = [c for c in methods_by_leaf.get(leaf, []) if c != ref["source"]]
            receiver = name.rsplit(".", 2)[-2].lstrip("#_").lower()
            owner = {c: by_id[c]["name"].rsplit(".", 1)[0].rsplit(".", 1)[-1].lower() for c in defined}
            exact = [c for c in defined if owner[c] == receiver]
            suffix = [c for c in defined if len(receiver) >= 4 and owner[c].endswith(receiver)]
            candidates = exact or suffix or (defined if leaf in project_leaves else [])
        if destination:
            edges.append(edge(ref["source"], destination, ref["relation"], "INFERRED", ref.get("line")))
        elif 1 <= len(assigned) <= 3:
            for setter in assigned:
                edges.append(edge(ref["source"], setter, "calls", "ASSIGNED", ref.get("line")))
        elif assigned:
            # Set in many places: the call cannot be followed, and a response must not claim it was.
            edges.append(edge(ref["source"], "unresolved:" + name, "calls", "UNRESOLVED", ref.get("line")))
        elif ref["relation"] == "calls" and 1 <= len(candidates) <= 3:
            # `field._clean_bound_field(bf)`: the receiver's type is unknown, but only a few project
            # methods carry this name. Kept as CANDIDATE edges, never walked as a known call.
            for candidate in candidates:
                edges.append(edge(ref["source"], candidate, "calls", "CANDIDATE", ref.get("line")))
        elif ref["relation"] == "calls" and name.startswith(("self.", "cls.", "this.")) and name.count(".") >= 2 and leaf in project_leaves:
            # `self.query.add_q()`, `this.#node.search()`: a call into project code that could not
            # be followed. Kept so a response never claims the chain is complete.
            edges.append(edge(ref["source"], "unresolved:" + name, "calls", "UNRESOLVED", ref.get("line")))
    return list({(e["source"], e["target"], e["relation"]): e for e in edges}.values())
