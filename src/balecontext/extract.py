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
        self._definition(node, "method" if self.class_stack else "function")

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._definition(node, "class")

    def _ref(self, name: str, relation: str, source: str, line: int) -> None:
        self.result.refs.append({
            "source": source, "name": name, "relation": relation, "line": line,
            "scope": ".".join(self.scopes), "class_name": self.class_stack[-1] if self.class_stack else "",
        })

    def visit_Call(self, node: ast.Call) -> None:
        self._ref(ast.unparse(node.func), "calls", self.stack[-1].id, node.lineno)
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
    edges: list[dict] = []
    for ref in refs:
        if ref["relation"] != "imports":
            continue
        path = by_id[ref["source"]]["path"]
        target = target_module(path, ref["module"])
        if not target and ref.get("symbol"):
            target = target_module(path, ref["module"] + "." + ref["symbol"])
            imported_symbol = ""
        else:
            imported_symbol = ref.get("symbol", "")
        if target:
            destination = symbol(target, imported_symbol) if imported_symbol else files[target]["id"]
            if destination:
                edges.append(edge(ref["source"], destination, "imports", line=ref.get("line")))
            bindings[(path, ref.get("scope", ""), ref["name"])] = (target, imported_symbol)
    for ref in refs:
        if ref["relation"] == "imports":
            continue
        path = by_id[ref["source"]]["path"]
        name = ref["name"]
        scope_parts = ref.get("scope", "").split(".") if ref.get("scope") else []
        destination = None
        if name.startswith(("self.", "cls.", "this.")) and ref.get("class_name"):
            destination = symbol(path, ref["class_name"] + "." + name.split(".", 1)[1])
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
                destination = symbol(target, qualified)
        if not destination and "." in name and not name.startswith(("self.", "cls.", "this.")):
            # `app.render` from response.js: a dotted name defined exactly once in the project's code.
            hits = global_names.get(name, [])
            if len(hits) == 1:
                destination = hits[0]
        if not destination and ref.get("class_name") and name.count(".") == 1 and not name.startswith(("self.", "cls.", "this.")):
            # `clone._filter_or_exclude_inplace()` inside QuerySet: a local object calling a method
            # that only this class defines. Labelled INFERRED like every resolved call.
            head, attr = name.split(".")
            if not any(bindings.get((path, ".".join(scope_parts[:length]), head)) for length in range(len(scope_parts), -1, -1)):
                destination = symbol(path, ref["class_name"] + "." + attr)
                if destination == ref["source"]:
                    destination = None
        if destination:
            edges.append(edge(ref["source"], destination, ref["relation"], "INFERRED", ref.get("line")))
        elif ref["relation"] == "calls" and name.startswith(("self.", "cls.", "this.")) and name.count(".") >= 2 and name.rsplit(".", 1)[1] in project_leaves:
            # `self.query.add_q()`, `this.#node.search()`: a call into project code that could not
            # be followed. Kept so a response never claims the chain is complete.
            edges.append(edge(ref["source"], "unresolved:" + name, "calls", "UNRESOLVED", ref.get("line")))
    return list({(e["source"], e["target"], e["relation"]): e for e in edges}.values())
