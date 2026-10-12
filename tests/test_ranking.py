from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from balecontext import rank
from balecontext.extract import extract, resolve
from balecontext.mcp import Server
from balecontext.retrieve import context, estimate, expand
from balecontext.store import Project

FORMS = '''class BaseForm:
    @property
    def errors(self):
        """Return the errors for the data provided.

        Runs full cleaning on first access.
        """
        if self._errors is None:
            self.full_clean()
        return self._errors

    def is_valid(self):
        """Return True if the form has no errors."""
        return self.is_bound and not self.errors

    def full_clean(self):
        self._errors = {}
        self.cleaned_data = {}
        self._clean_fields()
        self._clean_form()
        self._post_clean()

    def _clean_fields(self):
        for name, field in self.fields.items():
            self.cleaned_data[name] = field.clean(self.data.get(name))

    def _clean_form(self):
        self.cleaned_data = self.clean()

    def _post_clean(self):
        pass

    def clean(self):
        return self.cleaned_data
'''

FORMSETS = '''class BaseFormSet:
    @property
    def errors(self):
        if self._errors is None:
            self.full_clean()
        return self._errors

    def is_valid(self):
        """Validate every form in the set: field validation, error handling, cleaning."""
        return all(form.is_valid() for form in self.forms) and not self.errors

    def full_clean(self):
        self._errors = []
        for form in self.forms:
            self._errors.append(form.errors)
'''

MODELS = '''class Model:
    def full_clean(self, exclude=None):
        """Model validation: clean fields, clean, validate unique. Validation error handling."""
        errors = {}
        self.clean_fields(exclude)
        self.clean()
        return errors

    def clean_fields(self, exclude=None):
        pass

    def clean(self):
        pass
'''

TESTS = '''class FormValidationTests:
    def test_validation_error_field_handling_cleaning(self):
        """Field validation, error handling and form-wide cleaning: validation error raised."""
        form = object()
        assert form

    def test_validation_error_non_field_cleaning(self):
        """Explain field validation error handling cleaning validation error."""
        assert True
'''

DOCS = "Form and field validation\n" + "Field validation, error handling and form-wide cleaning happen when is_valid is called.\n" * 20

PROSE = ("Explain what happens when Django's form.is_valid() is called. Trace field validation, error handling, "
         "and form-wide cleaning. Cite the relevant source files and line numbers. Do not modify any files.")


class RankingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="balecontext-rank-")
        self.root = Path(self.temporary.name).resolve()
        for name, text in {
            "django/forms/forms.py": FORMS, "django/forms/formsets.py": FORMSETS,
            "django/db/models/base.py": MODELS, "tests/forms_tests/test_forms.py": TESTS,
            "docs/ref/forms/validation.txt": DOCS,
        }.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.project = Project(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def names(self, response):
        return [(source["name"], source["path"]) for source in response["sources"]]

    def test_query_parsing_keeps_identifiers_and_drops_instructions(self):
        parsed = rank.parse(PROSE)
        self.assertEqual([i.text for i in parsed.identifiers], ["form.is_valid"])
        for dropped in ("cite", "relevant", "source", "line", "number", "modify", "explain", "s"):
            self.assertNotIn(dropped, parsed.words)
        self.assertIn("valid", parsed.words)
        self.assertEqual(rank.parse("How does the URLResolver match? POST HTTP").identifiers[0].text, "URLResolver")
        self.assertEqual(len(rank.parse("POST HTTP request").identifiers), 0)

    def test_entry_point_and_call_chain_beat_shared_words(self):
        response = context(self.project, PROSE)
        names = self.names(response)
        self.assertEqual(names[0], ("BaseForm.is_valid", "django/forms/forms.py"))
        for method in ("errors", "full_clean", "_clean_fields", "_clean_form", "_post_clean"):
            self.assertIn((f"BaseForm.{method}", "django/forms/forms.py"), names)
        self.assertFalse([n for n in names if not n[1].startswith("django/forms/forms.py")])
        self.assertEqual(response["verdict"], "COMPLETE")
        # Execution order: is_valid -> errors -> full_clean -> its callees in call order.
        order = [n[0] for n in names]
        self.assertLess(order.index("BaseForm.full_clean"), order.index("BaseForm._clean_fields"))
        self.assertLess(order.index("BaseForm._clean_fields"), order.index("BaseForm._clean_form"))
        self.assertIn("called by BaseForm.full_clean at line", response["text"])
        self.assertIn("[docstring lines", response["text"])

    def test_class_qualifier_selects_the_right_method(self):
        response = context(self.project, "BaseForm full_clean")
        self.assertEqual(self.names(response)[0], ("BaseForm.full_clean", "django/forms/forms.py"))
        response = context(self.project, "Model.full_clean")
        self.assertEqual(self.names(response)[0], ("Model.full_clean", "django/db/models/base.py"))
        response = context(self.project, "formset is_valid")
        self.assertEqual(self.names(response)[0], ("BaseFormSet.is_valid", "django/forms/formsets.py"))

    def test_tests_and_docs_rank_below_code_unless_asked(self):
        response = context(self.project, "field validation error handling cleaning")
        self.assertNotEqual(response["sources"][0]["path"].split("/")[0], "tests")
        asked = context(self.project, "tests for field validation error handling cleaning")
        self.assertTrue(any(s["path"].startswith("tests/") for s in asked["sources"]))

    def test_a_test_named_like_the_question_is_not_the_entry(self):
        (self.root / "netlib").mkdir()
        (self.root / "netlib/utils.py").write_text(
            "def get_environ_proxies(url, no_proxy=None):\n    if should_bypass_proxies(url, no_proxy=no_proxy):\n        return {}\n    return getproxies()\n\n\n"
            "def should_bypass_proxies(url, no_proxy):\n    return False\n", encoding="utf-8")
        (self.root / "tests/test_utils.py").write_text("class TestGetEnvironProxies:\n    def no_proxy(self):\n        return 'example.com'\n", encoding="utf-8")
        response = context(self.project, "How does it pick environment proxies, including no_proxy?")
        self.assertEqual(response["sources"][0]["name"], "get_environ_proxies")
        asked = context(self.project, "Which tests cover no_proxy?")
        self.assertEqual(asked["sources"][0]["path"], "tests/test_utils.py")

    def test_a_plain_word_after_a_determiner_is_not_a_function_name(self):
        self.assertEqual(rank.parse("How are cookies attached to the next request?").words, ["cooky", "attach", "request"])
        self.assertIn("next", rank.parse("How does next run the middleware?").prose_words)
        self.assertNotIn("session", rank.parse("How are cookies saved on the Session?").prose_words)
        self.assertIn("compose", rank.parse("How does the compose function run middleware?").prose_words)

    def test_words_of_one_family_share_a_long_stem(self):
        # "URL resolution" names URLResolver.resolve; a short shared start ("req", "form") does not count.
        self.assertTrue(rank.same_family(rank.stem("resolution"), rank.stem("resolve")))
        self.assertTrue(rank.same_family(rank.stem("validators"), rank.stem("valid")))
        self.assertFalse(rank.same_family(rank.stem("request"), rank.stem("require")))
        self.assertFalse(rank.same_family(rank.stem("format"), rank.stem("form")))

    def test_a_call_on_self_reaches_a_method_inherited_from_a_base_class(self):
        (self.root / "widgets").mkdir()
        (self.root / "widgets/utils.py").write_text(
            "class RenderableMixin:\n    def render(self, template_name=None):\n        return self.renderer.render(template_name, self.get_context())\n\n"
            "    def get_context(self):\n        return {'errors': self}\n\n\n"
            "class ErrorListMixin(RenderableMixin):\n    def as_ul(self):\n        return self.render(self.template_name_ul)\n", encoding="utf-8")
        text = context(self.project, "How does ErrorListMixin.as_ul() produce its HTML?")["text"]
        self.assertIn('"name": "RenderableMixin.render", "kind": "method", "role": "called by ErrorListMixin.as_ul at line 11"', text)
        self.assertIn('"name": "RenderableMixin.get_context", "kind": "method", "role": "called by RenderableMixin.render at line 3"', text)

    def test_an_override_in_a_class_the_question_names_is_followed(self):
        (self.root / "shop").mkdir()
        (self.root / "shop/forms.py").write_text(
            "class OrderBase:\n    def is_complete(self):\n        self._finish()\n        return True\n\n    def _finish(self):\n        pass\n\n\n"
            "class PaidOrderBase(OrderBase):\n    def _finish(self):\n        settle_invoice(self)\n\n\n"
            "def settle_invoice(order):\n    order.paid = True\n\n\n"
            "class PaidOrder(PaidOrderBase):\n    pass\n", encoding="utf-8")
        names = [s["name"] for s in context(self.project, "What happens when a PaidOrder runs is_complete()?")["sources"]]
        self.assertIn("PaidOrderBase._finish", names)
        self.assertIn("settle_invoice", names)
        # A question that names no subclass only lists the override, without following it.
        plain = [s["name"] for s in context(self.project, "What happens in OrderBase.is_complete()?")["sources"]]
        self.assertNotIn("settle_invoice", plain)

    def test_first_response_is_capped_and_expand_returns_full_source(self):
        response = context(self.project, PROSE, 8000)
        self.assertLessEqual(response["estimated_tokens"], 4000)
        entry = response["sources"][0]
        expanded = expand(self.project, [entry["id"], "missing-id"])
        self.assertIn("Return True if the form has no errors.", expanded["text"])
        self.assertIn("missing-id", expanded["text"])
        with self.assertRaises(ValueError):
            expand(self.project, [])

    def test_session_does_not_resend_source(self):
        session: dict = {}
        first = context(self.project, PROSE, session=session)
        second = context(self.project, PROSE, session=session)
        self.assertIn("def is_valid", first["text"])
        self.assertNotIn("def is_valid", second["text"])
        self.assertIn("ALREADY SENT IN THIS SESSION", second["text"])
        self.assertLess(estimate(second["text"]), estimate(first["text"]))
        # After an edit the changed file is sent again, never reported as already sent.
        forms = self.root / "django/forms/forms.py"
        forms.write_text(forms.read_text(encoding="utf-8").replace("return self.is_bound and not self.errors", "return bool(self.is_bound) and not self.errors"), encoding="utf-8")
        third = context(self.project, PROSE, session=session)
        self.assertIn("return bool(self.is_bound)", third["text"])

    def test_mcp_expand_tool_and_list_validation(self):
        server = Server(self.project)

        def call(method, params=None):
            return server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

        call("initialize", {"protocolVersion": "2025-11-25"})
        packed = call("tools/call", {"name": "balecontext_context", "arguments": {"query": "BaseForm is_valid"}})
        self.assertIn("COVERAGE:", packed["result"]["content"][0]["text"])
        node_id = context(self.project, "BaseForm is_valid")["sources"][0]["id"]
        expanded = call("tools/call", {"name": "balecontext_expand", "arguments": {"ids": [node_id]}})
        self.assertFalse(expanded["result"]["isError"])
        self.assertIn("def is_valid", expanded["result"]["content"][0]["text"])
        for bad in ([], [1], "id"):
            error = call("tools/call", {"name": "balecontext_expand", "arguments": {"ids": bad}})
            self.assertTrue(error["result"]["isError"])


RESPONSE_JS = """var res = Object.create(http.ServerResponse.prototype);

res.send = function send(body) {
  this.set('Content-Type', 'text/html');
  return this.end(body);
};

res.set =
res.header = function header(field, val) {
  this.setHeader(field, val);
  return this;
};

res.render = function render(view, options) {
  var app = this.req.app;
  app.render(view, options);
};

View.prototype.lookup = function lookup(name) {
  return name;
};
"""

APPLICATION_JS = """var app = exports = module.exports = {};

app.render = function render(name, options) {
  return tryRender(name, options);
};

function tryRender(view, options) {
  return view;
}
"""

ROUTER_TS = """export class Router {
  #node: Node = new Node()
  fetch = (request: Request): Response => {
    return this.#dispatch(request)
  }
  #dispatch(request: Request): Response {
    return this.#node.search(request)
  }
}
"""

JS_PARSERS = all(importlib.util.find_spec(m) for m in ("tree_sitter", "tree_sitter_javascript", "tree_sitter_typescript"))


@unittest.skipUnless(JS_PARSERS, "Optional JS/TS parsers not installed")
class JavaScriptTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="balecontext-js-")
        self.root = Path(self.temporary.name).resolve()
        for name, text in {"lib/response.js": RESPONSE_JS, "lib/application.js": APPLICATION_JS, "src/router.ts": ROUTER_TS,
                           "src/node.ts": "export class Node {\n  search(request: Request): Response {\n    return new Response()\n  }\n}\n"}.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.project = Project(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_prototype_assignments_and_class_fields_are_definitions(self):
        names = {node.name for node in extract("lib/response.js", RESPONSE_JS).nodes}
        self.assertTrue({"res.send", "res.header", "res.set", "res.render", "View.lookup"} <= names)
        names = {node.name for node in extract("lib/application.js", APPLICATION_JS).nodes}
        self.assertTrue({"app.render", "tryRender"} <= names)
        names = {node.name for node in extract("src/router.ts", ROUTER_TS).nodes}
        self.assertTrue({"Router", "Router.fetch", "Router.#dispatch"} <= names)

    def test_calls_resolve_through_this_and_across_files(self):
        response = context(self.project, "How does res.render render a view?")
        names = [source["name"] for source in response["sources"]]
        self.assertEqual(names[0], "res.render")
        self.assertIn("app.render", names)
        response = context(self.project, "How does res.send set the header?")
        self.assertEqual(response["sources"][0]["name"], "res.send")
        self.assertTrue({"res.set", "res.header"} & {source["name"] for source in response["sources"]})

    def test_unknown_receiver_calls_are_followed_by_name(self):
        # `this.#node.search()`: only one project method is named `search`, so it is shown, labelled.
        response = context(self.project, "How does Router fetch dispatch a request?")
        names = [source["name"] for source in response["sources"]]
        self.assertEqual(names, ["Router.fetch", "Router.#dispatch", "Node.search"])
        self.assertIn("receiver type not known", response["text"])


class ResolveTest(unittest.TestCase):
    def test_bare_call_inside_a_method_is_not_the_method(self):
        source = "def parse(text):\n    return text\n\nclass Request:\n    def parse(self):\n        return parse(self)\n"
        result = extract("request.py", source)
        nodes = [node.dict() for node in result.nodes]
        edges = resolve(nodes, result.refs)
        by_id = {node["id"]: node for node in nodes}
        calls = {(by_id[e["source"]]["name"], by_id[e["target"]]["name"]) for e in edges if e["relation"] == "calls" and e["target"] in by_id}
        self.assertIn(("Request.parse", "parse"), calls)
        self.assertNotIn(("Request.parse", "Request.parse"), calls)

    def test_unknown_receiver_with_many_candidates_stays_unresolved(self):
        classes = "".join(f"class Step{i}:\n    def advance(self):\n        return {i}\n\n" for i in range(4))
        source = classes + "class Runner:\n    def go(self):\n        return self.step.advance()\n\ndef drive(task):\n    return task.go()\n"
        result = extract("runner.py", source)
        nodes = [node.dict() for node in result.nodes]
        edges = resolve(nodes, result.refs)
        by_id = {node["id"]: node for node in nodes}
        labelled = {(by_id[e["source"]]["name"], by_id.get(e["target"], {"name": e["target"]})["name"], e["evidence"]) for e in edges if e["relation"] == "calls"}
        self.assertIn(("Runner.go", "unresolved:self.step.advance", "UNRESOLVED"), labelled)
        self.assertIn(("drive", "Runner.go", "CANDIDATE"), labelled)

    def test_unknown_receiver_prefers_the_class_named_like_it(self):
        source = ("class RawQuery:\n    def chain(self):\n        return 1\n\n"
                  "class Query:\n    def chain(self):\n        return 2\n\n"
                  "class QuerySet:\n    def _chain(self):\n        return self.query.chain()\n")
        result = extract("query.py", source)
        nodes = [node.dict() for node in result.nodes]
        by_id = {node["id"]: node for node in nodes}
        targets = {by_id[e["target"]]["name"] for e in resolve(nodes, result.refs) if e["evidence"] == "CANDIDATE"}
        self.assertEqual(targets, {"Query.chain"})

    @staticmethod
    def _calls(files: dict[str, str]) -> set[tuple[str, str, str]]:
        results = [extract(path, text) for path, text in files.items()]
        nodes = [node.dict() for result in results for node in result.nodes]
        by_id = {node["id"]: node for node in nodes}
        edges = resolve(nodes, [ref for result in results for ref in result.refs])
        return {(by_id[e["source"]]["name"], by_id.get(e["target"], {"name": e["target"]})["name"], e["relation"] + " " + e["evidence"]) for e in edges}

    def test_package_imports_reach_submodules_and_re_exports(self):
        calls = self._calls({
            "pkg/__init__.py": "from .base import Model\n",
            "pkg/base.py": "class Model:\n    def save(self):\n        return 1\n",
            "pkg/handlers/__init__.py": "",
            "pkg/handlers/base.py": "class BaseHandler:\n    def get_response(self, request):\n        return request\n",
            "app.py": ("from pkg.handlers import base\nfrom pkg import Model\n\n"
                       "class WSGIHandler(base.BaseHandler):\n    def __call__(self, environ):\n        return self.get_response(environ)\n\n"
                       "def make():\n    model = Model()\n    return model.save()\n"),
        })
        self.assertIn(("WSGIHandler", "BaseHandler", "inherits INFERRED"), calls)
        self.assertIn(("WSGIHandler.__call__", "BaseHandler.get_response", "calls INFERRED"), calls)
        self.assertIn(("make", "Model.save", "calls INFERRED"), calls)

    def test_a_local_built_from_a_class_names_its_methods(self):
        calls = self._calls({"cli.py": (
            "class Utility:\n    def execute(self):\n        return 1\n\n"
            "class Step:\n    def execute(self):\n        return 2\n\n"
            "def main(argv):\n    utility = Utility(argv)\n    return utility.execute()\n\n"
            "def other(flag):\n    runner = Utility()\n    if flag:\n        runner = Step()\n    return runner.execute()\n")})
        self.assertIn(("main", "Utility.execute", "calls INFERRED"), calls)
        # Bound twice: the class is not known, so both stay candidates.
        self.assertNotIn(("other", "Utility.execute", "calls INFERRED"), calls)

    def test_a_call_on_a_stored_attribute_reaches_the_method_that_built_it(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-assigned-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = ("def wrap(function):\n    return function\n\n"
                  "class Request:\n    def __init__(self, environ):\n        self.environ = environ\n\n"
                  "class Handler:\n    request_class = Request\n\n"
                  "    def __init__(self, get_response):\n        self.get_response = get_response\n\n"
                  "    def load_chain(self):\n        handler = wrap(self._respond)\n        self._chain = handler\n\n"
                  "    def handle(self, environ):\n        request = self.request_class(environ)\n"
                  "        return self._chain(request) or self.get_response(request)\n\n"
                  "    def _respond(self, request):\n        return request\n")
        (root / "handler.py").write_text(source, encoding="utf-8")
        calls = self._calls({"handler.py": source})
        self.assertIn(("Handler.handle", "Handler.load_chain", "calls ASSIGNED"), calls)
        self.assertIn(("Handler.handle", "Request", "calls INFERRED"), calls)
        # Storing an argument as it came says nothing about what it is.
        self.assertNotIn(("Handler.handle", "Handler.__init__", "calls ASSIGNED"), calls)
        response = context(Project(root), "How does Handler.handle run the chain?")
        names = [source["name"] for source in response["sources"]]
        self.assertEqual(names[0], "Handler.handle")
        for name in ("Handler.load_chain", "Handler._respond", "Request.__init__"):
            self.assertIn(name, names)
        self.assertIn("sets self._chain, which Handler.handle calls at line 20", response["text"])

    def test_calls_inside_a_returned_inner_function_and_past_a_stored_chain_are_followed(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-inner-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "urls.py").write_text("def get_resolver(urlconf=None):\n    return _cached(urlconf)\n\n\ndef _cached(urlconf):\n    return urlconf\n", encoding="utf-8")
        (root / "handler.py").write_text(
            "from urls import get_resolver\n\n\n"
            "def response_for(request, exc):\n    return str(exc)\n\n\n"
            "def convert(get_response):\n    def inner(request):\n        try:\n            return get_response(request)\n"
            "        except Exception as exc:\n            return response_for(request, exc)\n    return inner\n\n\n"
            "class Handler:\n    def load_chain(self):\n        self._chain = convert(self._respond)\n\n"
            "    def handle(self, request):\n        return self._chain(request)\n\n"
            "    def _respond(self, request):\n        return self._dispatch(request)\n\n"
            "    def _dispatch(self, request):\n        return self.resolve(request)\n\n"
            "    def resolve(self, request):\n        return get_resolver().match(request)\n", encoding="utf-8")
        response = context(Project(root), "How does Handler.handle run the chain?")
        names = [source["name"] for source in response["sources"]]
        self.assertEqual(names[0], "Handler.handle")
        # What the returned `inner` calls is the wrapper's own call.
        self.assertIn("response_for", names)
        self.assertNotIn("convert.inner", names)
        # The method that stores the chain is no step of its own, so the chain's calls stay near.
        self.assertIn("get_resolver", names)

    def test_a_method_brings_what_its_class_computes_for_it_and_reads_from_it(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-state-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "loader_tags.py").write_text(
            "class BlockNode:\n    def __init__(self, name, nodelist):\n        self.name = name\n        self.nodelist = nodelist\n\n"
            "    def render(self, context):\n        block = type(self)(self.name, self.nodelist)\n        block.context = context\n"
            "        context['block'] = block\n        return block.nodelist.render(context)\n\n"
            "    def super(self):\n        if not hasattr(self, 'context'):\n            raise ValueError('no context')\n"
            "        return self.context.get('parent', '')\n\n\n"
            "class ExtendsNode:\n    def __init__(self, nodelist, parent_name):\n        self.nodelist = nodelist\n        self.parent_name = parent_name\n"
            "        self.blocks = {n.name: n for n in nodelist}\n\n"
            "    def render(self, context):\n        context.render_context['blocks'] = self.blocks\n        return self.nodelist.render(context)\n", encoding="utf-8")
        response = context(Project(root), "How does the template engine render the extends and block tags?")
        names = [source["name"] for source in response["sources"]]
        self.assertIn("BlockNode.super", names)
        self.assertIn("reads self.context, which BlockNode.render sets at line 8", response["text"])
        self.assertIn("ExtendsNode.__init__", names)
        self.assertIn("constructor of ExtendsNode; sets blocks, read by ExtendsNode.render", response["text"])
        # A constructor that only stores its arguments says nothing new.
        self.assertNotIn("BlockNode.__init__", names)

    def test_the_base_class_a_question_word_begins_shows_what_calling_its_objects_runs(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-callable-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "middleware.py").write_text(
            "class MiddlewareMixin:\n    def __init__(self, get_response):\n        self.get_response = get_response\n\n"
            "    def __call__(self, request):\n        response = self.process_request(request)\n"
            "        response = response or self.get_response(request)\n        return self.process_response(request, response)\n\n\n"
            + "".join(f"class {name}Middleware(MiddlewareMixin):\n    def process_request(self, request):\n        return None\n\n\n" for name in ("Security", "Session", "Common")), encoding="utf-8")
        (root / "validators.py").write_text("class ChainValidator:\n    def __call__(self, value):\n        return value\n", encoding="utf-8")
        (root / "handler.py").write_text(
            "def import_string(path):\n    return path\n\n\n"
            "class BaseHandler:\n    def load_middleware(self, paths):\n        handler = self._respond\n        for path in paths:\n"
            "            middleware = import_string(path)\n            handler = middleware(handler)\n        self._middleware_chain = handler\n\n"
            "    def get_response(self, request):\n        return self._middleware_chain(request)\n\n"
            "    def _respond(self, request):\n        return request\n", encoding="utf-8")
        response = context(Project(root), "How does BaseHandler.get_response run the middleware chain?")
        names = [source["name"] for source in response["sources"]]
        self.assertIn("MiddlewareMixin.__call__", names)
        self.assertIn("matches middleware; runs when such an object is called", response["text"])
        # A callable class no project class extends is not what the chain runs.
        self.assertNotIn("ChainValidator.__call__", names)

    def test_what_runs_after_the_response_and_the_errors_its_handlers_catch_are_shown(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-after-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "signals.py").write_text(
            "class Signal:\n    def __init__(self):\n        self.receivers = []\n\n"
            "    def connect(self, receiver):\n        self.receivers.append(receiver)\n\n"
            "    def send(self, sender):\n        return [receiver(sender) for receiver in self.receivers]\n\n\n"
            "request_started = Signal()\nrequest_finished = Signal()\n", encoding="utf-8")
        (root / "response.py").write_text(
            "from signals import request_finished\n\n\nclass Http404(Exception):\n    pass\n\n\n"
            "class HttpResponse:\n    def __init__(self, content):\n        self.content = content\n        self._handler_class = None\n\n"
            "    def close(self):\n        request_finished.send(sender=self._handler_class)\n", encoding="utf-8")
        (root / "resolver.py").write_text(
            "from response import Http404\n\n\nclass Resolver404(Http404):\n    pass\n\n\n"
            "def resolve(path):\n    if path != '/':\n        raise Resolver404(path)\n    return 'index'\n", encoding="utf-8")
        (root / "handler.py").write_text(
            "from signals import request_started, request_finished\nfrom response import HttpResponse, Http404\nfrom resolver import resolve\n\n\n"
            "def reset_urlconf(sender):\n    return None\n\n\nrequest_finished.connect(reset_urlconf)\n\n\n"
            "class WSGIHandler:\n    def __call__(self, environ):\n        request_started.send(sender=self.__class__)\n"
            "        response = self.get_response(environ)\n        response._handler_class = self.__class__\n        return response\n\n"
            "    def get_response(self, environ):\n        try:\n            view = resolve(environ['PATH_INFO'])\n        except Exception as exc:\n"
            "            if isinstance(exc, Http404):\n                return HttpResponse('not found')\n            raise\n        return HttpResponse(view)\n", encoding="utf-8")
        response = context(Project(root), "How does WSGIHandler.__call__ handle a request?")
        # The server closes the response once it is sent, and close reads what __call__ set on it.
        self.assertIn("reads self._handler_class, which WSGIHandler.__call__ sets on response at line 17", response["text"])
        # A function connected to the signal close sends runs then.
        self.assertIn("runs on request_finished, which HttpResponse.close sends at line 14", response["text"])
        # get_response answers "not found" for Resolver404 only because it is an Http404.
        self.assertIn("raised by resolve at line 10; a subclass of Http404", response["text"])
        self.assertEqual([source["name"] for source in response["sources"]][-3:], ["HttpResponse.close", "reset_urlconf", "Resolver404"])

    def test_a_question_about_callers_gets_every_place_that_names_the_definition(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-callers-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "tests").mkdir()
        (root / "docs").mkdir()
        (root / "settings.py").write_text(
            "def merge_setting(request_setting, session_setting):\n    if session_setting is None:\n        return request_setting\n"
            "    return {**session_setting, **request_setting}\n", encoding="utf-8")
        (root / "sessions.py").write_text(
            "from settings import merge_setting\n\n\nclass Session:\n    def request(self, method, url, headers=None, params=None):\n"
            "        merged = merge_setting(headers, self.headers)\n        query = merge_setting(params, self.params)\n"
            "        return self.send(method, url, merged, query)\n\n    def send(self, method, url, headers, params):\n"
            "        return (method, url, headers, params)\n\n\ndef merge_environment(proxies, env):\n"
            "    # merge_setting drops None values\n    return merge_setting(proxies, env)\n", encoding="utf-8")
        (root / "tests/test_settings.py").write_text(
            "from settings import merge_setting\n\n\ndef test_merge():\n    assert merge_setting({'a': 1}, None) == {'a': 1}\n", encoding="utf-8")
        (root / "docs/api.md").write_text("# API\n\nmerge_setting merges two dicts.\n", encoding="utf-8")
        response = context(Project(root), "Who calls merge_setting?")
        text = response["text"]
        self.assertEqual(response["kind"], "callers")
        self.assertEqual([source["name"] for source in response["sources"]], ["merge_setting", "Session.request", "merge_environment"])
        self.assertIn('"role": "calls merge_setting at lines 6, 7"', text)
        self.assertIn("sessions.py:1: from settings import merge_setting  [imports it]", text)
        # A test that calls it is listed before one that only imports it.
        self.assertLess(text.index("tests/test_settings.py:5 test_merge:"), text.index("tests/test_settings.py:1:"))
        self.assertEqual(response["verdict"], "COMPLETE")
        self.assertIn("2 definitions in the code call it (3 call lines); 1 other line in the code names it; 2 lines in tests; 1 line in docs (not listed)", text)

    def test_a_long_caller_is_shown_around_its_call_and_an_unknown_receiver_is_listed(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-callsite-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        long_job = ("def long_job(items):\n    runner = Runner()\n" + "".join(f"    total_{i} = len(items) + {i}\n" for i in range(20))
                    + "    result = runner.go()\n" + "".join(f"    total_{i} += {i}\n" for i in range(20)) + "    return result\n")
        (root / "runner.py").write_text("class Runner:\n    def go(self):\n        return 1\n\n\ndef drive(task):\n    return task.go()\n\n\n" + long_job, encoding="utf-8")
        response = context(Project(root), "What would break if I changed Runner.go?")
        text = response["text"]
        self.assertIn('"role": "calls go at line 32"', text)
        self.assertIn("32:     result = runner.go()", text)
        self.assertNotIn("total_10 = len(items) + 10", text)
        self.assertIn("[Excerpt: lines of long_job away from its calls to go, and from what it does with their result, are not shown.]", text)
        self.assertIn("=== POSSIBLE CALLS of go (receiver type not known; one line each)\nrunner.py:7 drive: return task.go()", text)
        self.assertEqual(response["verdict"], "COMPLETE")

    def test_a_call_of_the_method_a_mixin_declares_reaches_the_class_that_defines_it(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-mixin-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "sessions.py").write_text(
            "class RedirectMixin:\n    def send(self, request): ...\n\n    def follow(self, request):\n        return self.send(request)\n\n\n"
            "class Session(RedirectMixin):\n    def send(self, request):\n        return request\n\n    def request(self, url):\n        return self.send(url)\n", encoding="utf-8")
        response = context(Project(root), "Who calls Session.send?")
        self.assertEqual([source["name"] for source in response["sources"]], ["Session.send", "RedirectMixin.follow", "Session.request"])
        self.assertIn('"role": "calls send at line 5 through RedirectMixin.send', response["text"])
        self.assertIn("[declared in RedirectMixin; Session.send defines it]", response["text"])

    def test_another_class_using_the_same_name_on_self_is_counted_not_listed(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-ownname-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "tests").mkdir()
        (root / "handler.py").write_text(
            "class BaseHandler:\n    def get_response(self, request):\n        return request\n\n\n"
            "class WSGIHandler(BaseHandler):\n    def __call__(self, environ):\n        return self.get_response(environ)\n", encoding="utf-8")
        (root / "middleware.py").write_text(
            "class Middleware:\n    def __init__(self, get_response):\n        self.get_response = get_response\n\n"
            "    def __call__(self, request):\n        return self.get_response(request)\n", encoding="utf-8")
        (root / "tests/test_middleware.py").write_text(
            "class MiddlewareTests:\n    def test_call(self):\n        def get_response(request):\n            return request\n        return get_response\n", encoding="utf-8")
        response = context(Project(root), "Who calls BaseHandler.get_response?")
        self.assertIn('"role": "calls get_response at line 8"', response["text"])
        # Each middleware's get_response is the next handler it was given, not this method.
        self.assertNotIn("middleware.py", response["text"])
        self.assertIn("3 lines name another class's or test's own get_response (not listed)", response["text"])

    def test_only_a_question_about_callers_reads_as_one(self):
        from balecontext.retrieve import CALLERS_QUESTION
        for question in ("Who calls merge_setting?", "Where is WSGIRequest used?", "What would break if I changed Session.send?",
                         "Which functions call app.handle?", "Find the call sites of compose", "What depends on get_resolver?",
                         "Where is Session.send called from?"):
            self.assertTrue(CALLERS_QUESTION.search(question), question)
        for question in ("What calls does Session.send make?", "How does Session.send work?", "Trace how Django handles a request",
                         "What does resolve_redirects call?"):
            self.assertFalse(CALLERS_QUESTION.search(question), question)

    def test_a_long_caller_shows_what_it_does_with_the_result(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-result-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        handle = ("def handle(environ):\n    handler = Handler()\n" + "".join(f"    step_{i} = {i}\n" for i in range(20))
                  + "    response = handler.get_response(environ)\n    log(response.status_code)\n" + "".join(f"    step_{i} += 1\n" for i in range(8))
                  + "    response.close()\n    return response\n")
        (root / "handlers.py").write_text("class Handler:\n    def get_response(self, environ):\n        return Response(environ)\n\n\n" + handle, encoding="utf-8")
        response = context(Project(root), "What would break if I changed what Handler.get_response returns?")
        text = response["text"]
        self.assertIn("28:     response = handler.get_response(environ)", text)
        # The lines soon after the call that use its result are shown; the rest of the long caller is not.
        self.assertIn("38:     response.close()", text)
        self.assertIn("39:     return response", text)
        self.assertNotIn("step_5 += 1", text)
        self.assertNotIn("step_10 = 10", text)

    def test_the_async_counterpart_of_a_method_is_covered_by_a_callers_question(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-twin-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "handlers.py").write_text(
            "class Handler:\n    def get_response(self, request):\n        return self.chain(request)\n\n"
            "    async def get_response_async(self, request):\n        return await self.chain(request)\n\n\n"
            "class WSGIHandler(Handler):\n    def __call__(self, environ):\n        return self.get_response(environ)\n\n\n"
            "class ASGIHandler(Handler):\n    async def run(self, scope):\n        response = await self.get_response_async(scope)\n        return response\n",
            encoding="utf-8")
        response = context(Project(root), "What would break if I changed what Handler.get_response returns?")
        text = response["text"]
        self.assertIn('"name": "Handler.get_response_async", "kind": "method", "role": "counterpart of get_response in Handler (the same method for the other of sync and async)"', text)
        self.assertIn("=== LINES NAMING get_response_async, the counterpart of get_response (one line each)\n"
                      "handlers.py:16 ASGIHandler.run: response = await self.get_response_async(scope)", text)
        self.assertIn("1 line names its counterpart get_response_async", text)
        self.assertEqual(response["verdict"], "COMPLETE")

    def test_a_library_test_client_counts_as_code_a_change_would_break(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-support-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "pkg" / "test").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (root / "pkg" / "handlers.py").write_text("class Handler:\n    def get_response(self, request):\n        return request\n", encoding="utf-8")
        (root / "pkg" / "test" / "client.py").write_text(
            "from pkg.handlers import Handler\n\n\nclass ClientHandler(Handler):\n    def __call__(self, environ):\n"
            "        response = self.get_response(environ)\n        response.wsgi_request = environ\n        return response\n", encoding="utf-8")
        (root / "tests" / "test_client.py").write_text(
            "from pkg.handlers import Handler\n\n\ndef test_get_response():\n    assert Handler().get_response(1) == 1\n", encoding="utf-8")
        response = context(Project(root), "Who calls Handler.get_response?")
        text = response["text"]
        # The test client ships with the library, so it is shown as a caller; the test tree stays one line each.
        self.assertIn('"path": "pkg/test/client.py", "lines": [5, 8], "name": "ClientHandler.__call__", "kind": "method", "role": "calls get_response at line 6"', text)
        self.assertIn("=== TESTS naming get_response (one line each)\ntests/test_client.py:5 test_get_response:", text)
        self.assertIn("1 definition in the code calls it (1 call line)", text)

    def test_a_constructor_that_runs_a_shown_method_is_sent_with_the_entry(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-running-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "handlers.py").write_text(
            "class BaseHandler:\n    def load_middleware(self):\n        chain = self._get_response\n        for layer in reversed(self.layers):\n"
            "            chain = layer(chain)\n        self._middleware_chain = chain\n\n"
            "    def get_response(self, request):\n        return self._middleware_chain(request)\n\n"
            "    def _get_response(self, request):\n        return request.view(request)\n\n\n"
            "class WSGIHandler(BaseHandler):\n    request_class = dict\n\n    def __init__(self, layers):\n        super().__init__()\n"
            "        self.layers = layers\n        self.load_middleware()\n\n"
            "    def __call__(self, environ):\n        request = self.request_class(environ)\n        return self.get_response(request)\n", encoding="utf-8")
        response = context(Project(root), "Trace how WSGIHandler.__call__ handles a request", budget=6000)
        text = response["text"]
        self.assertIn("BaseHandler.load_middleware", text)
        self.assertIn('"name": "WSGIHandler.__init__", "kind": "method", "role": "constructor of WSGIHandler; calls load_middleware when the object is made"', text)

    def test_tags_the_question_names_lead_and_calls_resolve_near_the_caller(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-tags-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "template").mkdir()
        (root / "forms").mkdir()
        (root / "template" / "loader_tags.py").write_text(
            "class BlockContext:\n    def __init__(self):\n        self.blocks = {}\n\n"
            "    def pop(self, name):\n        return self.blocks.pop(name, None)\n\n\n"
            "class BlockNode:\n    def render(self, context):\n        block_context = context.render_context.get('block_context')\n"
            "        block = block_context.pop(self.name)\n        return block.nodelist.render(context)\n\n\n"
            "class ExtendsNode:\n    def render(self, context):\n        compiled_parent = self.get_parent(context)\n"
            "        context.render_context.setdefault('block_context', BlockContext())\n        return compiled_parent._render(context)\n\n"
            "    def get_parent(self, context):\n        return context.template.engine.get_template(self.parent_name)\n", encoding="utf-8")
        (root / "template" / "base.py").write_text(
            "class Parser:\n    def invalid_block_tag(self, token, command, parse_until=None):\n        raise ValueError(command)\n\n"
            "    def unclosed_block_tag(self, parse_until):\n        raise ValueError(parse_until)\n\n\n"
            "class Template:\n    def _render(self, context):\n        return self.nodelist.render(context)\n\n\n"
            "class QueryDict:\n    def pop(self, key):\n        return key\n", encoding="utf-8")
        (root / "template" / "defaulttags.py").write_text(
            "class TemplateTagNode:\n    def render(self, context):\n        return self.mapping.get(self.tagtype, '')\n", encoding="utf-8")
        (root / "forms" / "widgets.py").write_text(
            "class Widget:\n    def _render(self, template_name, context, renderer=None):\n        return renderer.render(template_name, context)\n\n\n"
            "class Holder:\n    def clear(self):\n        self.__dict__.pop('cache', None)\n", encoding="utf-8")
        response = context(Project(root), "How does the template engine render the extends and block tags?")
        names = [source["name"] for source in response["sources"]]
        self.assertEqual(names[0], "ExtendsNode.render")
        # "block" names BlockNode as "extends" names ExtendsNode; error helpers named "block tag" are not the answer.
        self.assertIn("BlockNode.render", names)
        self.assertFalse({"Parser.invalid_block_tag", "Parser.unclosed_block_tag"} & set(names))
        # `compiled_parent._render` from template/ is the template's, not a form widget's; `block_context`
        # names BlockContext; `self.__dict__.pop` names no project class.
        self.assertIn("Template._render", names)
        self.assertNotIn("Widget._render", names)
        self.assertIn("BlockContext.pop", names)
        calls = self._calls({path: (root / path).read_text(encoding="utf-8") for path in ("forms/widgets.py", "template/base.py")})
        self.assertFalse([call for call in calls if call[0] == "Holder.clear" and call[1] == "QueryDict.pop"])

    def test_path_between_two_named_ends_comes_before_the_far_end_internals(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-command-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "core" / "management" / "commands").mkdir(parents=True)
        (root / "core" / "migrations").mkdir()
        (root / "core" / "management" / "__init__.py").write_text(
            "def load_command_class(name):\n    module = __import__('core.management.commands.' + name)\n    return module.Command()\n\n\n"
            "class ManagementUtility:\n    def __init__(self, argv=None):\n        self.argv = argv\n\n"
            "    def fetch_command(self, subcommand):\n        return load_command_class(subcommand)\n\n"
            "    def execute(self):\n        self.fetch_command(self.argv[1]).run_from_argv(self.argv)\n\n\n"
            "def execute_from_command_line(argv=None):\n    utility = ManagementUtility(argv)\n    utility.execute()\n", encoding="utf-8")
        (root / "core" / "management" / "base.py").write_text(
            "class BaseCommand:\n    def run_from_argv(self, argv):\n        self.execute(args=argv[2:])\n\n"
            "    def execute(self, **options):\n        return self.handle(**options)\n\n"
            "    def handle(self, **options):\n        raise NotImplementedError('subclasses must provide handle()')\n", encoding="utf-8")
        (root / "core" / "management" / "commands" / "migrate.py").write_text(
            "from core.management.base import BaseCommand\nfrom core.migrations.signals import emit_pre_migrate_signal, emit_post_migrate_signal\n"
            "from core.migrations.executor import MigrationExecutor\n\n\n"
            "class Command(BaseCommand):\n    def handle(self, **options):\n        emit_pre_migrate_signal(options)\n"
            "        executor = MigrationExecutor(options)\n        executor.migrate(options)\n        emit_post_migrate_signal(options)\n", encoding="utf-8")
        (root / "core" / "migrations" / "signals.py").write_text(
            "def emit_pre_migrate_signal(options):\n    return options\n\n\ndef emit_post_migrate_signal(options):\n    return options\n", encoding="utf-8")
        (root / "core" / "migrations" / "executor.py").write_text(
            "class MigrationExecutor:\n    def __init__(self, options):\n        self.options = options\n\n"
            "    def migrate(self, targets):\n        return [self.apply_migration(t) for t in targets]\n\n"
            "    def apply_migration(self, migration):\n        return migration\n", encoding="utf-8")
        response = context(Project(root), "How does the project run a management command such as migrate, from execute_from_command_line to the command's handle() method?", 1200, focus=True)
        order = [item["name"] for item in response["map"]]
        self.assertEqual(order[:2], ["execute_from_command_line", "ManagementUtility.execute"])
        # The path to handle() (run_from_argv, execute) before what migrate's own handle() calls.
        self.assertLess(order.index("BaseCommand.run_from_argv"), order.index("emit_pre_migrate_signal"))
        self.assertLess(order.index("BaseCommand.execute"), order.index("MigrationExecutor.migrate"))

    def test_a_question_in_words_shows_where_its_objects_are_built(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-tags-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "template").mkdir()
        (root / "template" / "loader_tags.py").write_text(
            "class BlockContext:\n    def __init__(self):\n        self.blocks = {}\n\n"
            "    def add_blocks(self, blocks):\n        self.blocks.update(blocks)\n\n"
            "    def pop(self, name):\n        return self.blocks.pop(name, None)\n\n"
            "    def push(self, name, block):\n        self.blocks[name] = block\n\n\n"
            "class BlockNode:\n    def __init__(self, name, nodelist):\n        self.name = name\n        self.nodelist = nodelist\n\n"
            "    def render(self, context):\n        block_context = context.render_context.get('block_context')\n"
            "        block = block_context.pop(self.name)\n        result = block.nodelist.render(context)\n"
            "        block_context.push(self.name, block)\n        return result\n\n\n"
            "class ExtendsNode:\n    def __init__(self, nodelist, parent_name):\n        self.nodelist = nodelist\n        self.parent_name = parent_name\n\n"
            "    def render(self, context):\n        block_context = BlockContext()\n        block_context.add_blocks(self.nodelist)\n"
            "        return context.template.engine.find_template(self.parent_name).render(context)\n\n\n"
            "def do_block(parser, token):\n    nodelist = parser.parse(('endblock',))\n    return BlockNode(token, nodelist)\n\n\n"
            "def do_extends(parser, token):\n    nodelist = parser.parse()\n    return ExtendsNode(nodelist, token)\n", encoding="utf-8")
        (root / "template" / "base.py").write_text(
            "class NodeList(list):\n    def render(self, context):\n        return ''.join(node.render(context) for node in self)\n\n\n"
            "class Parser:\n    def parse(self, until=()):\n        return NodeList()\n", encoding="utf-8")
        (root / "template" / "context.py").write_text("class RenderContext:\n    def get(self, key):\n        return None\n", encoding="utf-8")
        response = context(Project(root), "How does the template engine render the extends and block tags?", 1500, focus=True)
        roles = {item["name"]: item["role"] for item in response["map"]}
        order = [item["name"] for item in response["map"]]
        # do_block parses the block tag into the BlockNode whose render() matched the question.
        self.assertEqual(roles.get("do_block"), "builds BlockNode at line 41")
        # The block context's own methods, beside the entry, before a call into another module.
        self.assertLess(order.index("BlockContext.pop"), order.index("RenderContext.get"))
        # A question naming the definition asks about it, not about where its object is made.
        named = context(Project(root), "How does BlockNode.render work?", 1500, focus=True)
        self.assertFalse([item for item in named["map"] if item["role"].startswith("builds")])

    def test_entry_is_the_outermost_matching_function(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-root-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "csrf.py").write_text(
            "class CsrfMiddleware:\n"
            "    def _check_token(self, request):\n        return self._get_token(request)\n\n"
            "    def _get_token(self, request):\n        return request\n\n"
            "    def process_view(self, request):\n        if request.method == 'POST':\n            return self._check_token(request)\n",
            encoding="utf-8")
        response = context(Project(root), "How does CsrfMiddleware check the token on a POST request?")
        self.assertEqual(response["sources"][0]["name"], "CsrfMiddleware.process_view")

    def test_pack_follows_the_chain_out_of_the_entry_file(self):
        # `field._clean_bound_field(bf)` has no known receiver type; `_post_clean` is overridden.
        temporary = tempfile.TemporaryDirectory(prefix="balecontext-out-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "forms.py").write_text(
            "class BaseForm:\n"
            "    def is_valid(self):\n        return self.full_clean()\n\n"
            "    def full_clean(self):\n        for field in self.fields:\n            field._clean_bound_field(self)\n        self._post_clean()\n\n"
            "    def _post_clean(self):\n        pass\n", encoding="utf-8")
        (root / "fields.py").write_text(
            "class Field:\n"
            "    def _clean_bound_field(self, form):\n        return self.clean(form)\n\n"
            "    def clean(self, value):\n        return value\n", encoding="utf-8")
        (root / "models.py").write_text(
            "from forms import BaseForm\n\n"
            "class BaseModelForm(BaseForm):\n"
            "    def _post_clean(self):\n        return self.instance\n", encoding="utf-8")
        response = context(Project(root), "What happens when BaseForm.is_valid() is called?")
        names = [source["name"] for source in response["sources"]]
        self.assertEqual(names[0], "BaseForm.is_valid")
        for name in ("Field._clean_bound_field", "Field.clean", "BaseModelForm._post_clean"):
            self.assertIn(name, names)
        self.assertIn("receiver type not known", response["text"])
        self.assertIn("overrides BaseForm._post_clean in subclass BaseModelForm", response["text"])


class HookTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="balecontext-hook-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        target = self.root / "django/forms/forms.py"
        target.parent.mkdir(parents=True)
        target.write_text(FORMS, encoding="utf-8")

    def run_hook(self, prompt: str, cwd: Path, **event) -> str:
        import io
        import json
        from balecontext.hook import run_claude
        output = io.StringIO()
        self.assertEqual(run_claude(io.StringIO(json.dumps({"prompt": prompt, "cwd": str(cwd), **event})), output), 0)
        return output.getvalue()

    def test_follow_up_prompt_names_source_already_sent_until_compaction(self):
        import json
        Project(self.root).index()
        def context(**event):
            return json.loads(self.run_hook(PROSE, self.root, **event))["hookSpecificOutput"]["additionalContext"]
        first = context(session_id="conversation-1", hook_event_name="UserPromptSubmit")
        self.assertIn("def is_valid", first)
        again = context(session_id="conversation-1", hook_event_name="UserPromptSubmit")
        self.assertNotIn("def is_valid", again)
        self.assertRegex(again, r"(?m)^ALREADY SENT: BaseForm.is_valid django/forms/forms.py:\d+-\d+; BaseForm.errors ")
        self.assertIn("re-read those lines only if they are no longer in your context", again)
        self.assertLess(len(again), len(first) * 0.7)
        # Another conversation, and the same one after compaction, get the source again.
        self.assertIn("def is_valid", context(session_id="conversation-2", hook_event_name="UserPromptSubmit"))
        self.assertEqual(self.run_hook("", self.root, session_id="conversation-1", hook_event_name="PreCompact"), "")
        self.assertIn("def is_valid", context(session_id="conversation-1", hook_event_name="UserPromptSubmit"))

    def test_unindexed_folder_is_never_indexed_by_the_hook(self):
        self.assertEqual(self.run_hook(PROSE, self.root), "")
        self.assertFalse((self.root / ".balecontext").exists())

    def test_code_question_gets_evidence_and_chat_gets_nothing(self):
        import json
        Project(self.root).index()
        output = json.loads(self.run_hook(PROSE, self.root / "django" / "forms"))["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "UserPromptSubmit")
        self.assertIn("def is_valid", output["additionalContext"])
        self.assertIn("COVERAGE: COMPLETE", output["additionalContext"])
        # Compact: short headings, shared indentation dropped, no id map.
        self.assertIn("=== django/forms/forms.py:", output["additionalContext"])
        self.assertRegex(output["additionalContext"], r"\n\d+: def is_valid")
        self.assertNotIn("MAP (", output["additionalContext"])
        self.assertNotIn('SOURCE {"id"', output["additionalContext"])
        self.assertLessEqual(len(output["additionalContext"]), 9500)
        for chat in ("thanks, looks good", "/clear", "please run the build again"):
            self.assertEqual(self.run_hook(chat, self.root), "")
        log = (self.root / ".balecontext" / "hook-log.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 4)

    def test_partial_pack_saves_the_rest_to_one_file(self):
        helpers = "".join(f"def step_{i}(order):\n" + "".join(f"    order.total += {j}  # adjust the order total\n" for j in range(40)) + "    return order\n\n" for i in range(8))
        entry = "def checkout_order(order):\n" + "".join(f"    order = step_{i}(order)\n" for i in range(8)) + "    return order\n"
        (self.root / "shop.py").write_text(helpers + entry, encoding="utf-8")
        from balecontext import hook
        project = Project(self.root)
        project.index()
        text, response = hook.prompt_pack(project, "How does checkout_order work through each step?")
        self.assertLessEqual(len(text), hook.MAX_CHARS)
        saved = Path(response["overflow_path"])
        self.assertIn(str(saved), text)
        rest = saved.read_text(encoding="utf-8")
        self.assertIn("=== shop.py:", rest)
        self.assertIn("COVERAGE: ", rest)
        # The entry's own chain stays whole in the file; blank lines are dropped.
        self.assertIn("order.total += 39  # adjust the order total", rest)
        self.assertNotRegex(rest, r"(?m)^\d+: *$")

    def test_partial_pack_sends_the_rest_as_parts_each_within_the_limit(self):
        helpers = "".join(f"def step_{i}(order):\n" + "".join(f"    order.total += {j}  # adjust the order total\n" for j in range(40)) + "    return order\n\n" for i in range(8))
        entry = "def checkout_order(order):\n" + "".join(f"    order = step_{i}(order)\n" for i in range(8)) + "    return order\n"
        (self.root / "shop.py").write_text(helpers + entry, encoding="utf-8")
        from balecontext import hook
        project = Project(self.root)
        project.index()
        text, response = hook.prompt_pack(project, "How does checkout_order work through each step?")
        parts = response["parts"]
        self.assertTrue(1 <= len(parts) <= hook.EXTRA_PARTS)
        for number, part in enumerate(parts, 2):
            self.assertLessEqual(len(part), hook.MAX_CHARS)
            self.assertTrue(part.startswith(f"BaleContext evidence, part {number} of {len(parts) + 1} for the question above"))
        # The pack points at the parts, and at the saved file only in case a part did not arrive.
        self.assertIn("follows in BaleContext part", text)
        self.assertIn(f"Only if a part is missing, read {response['overflow_path']} instead.", text)
        rest = "\n".join(parts)
        self.assertIn("order.total += 39  # adjust the order total", rest)
        self.assertTrue(parts[-1].rstrip().endswith("Read a deeper helper or an outlined range only if the answer needs that detail."))
        self.assertEqual(rest.count("COVERAGE: "), 1)

    def test_a_long_block_is_cut_between_lines_into_parts_that_fit(self):
        from balecontext.hook import _split
        block = "=== big.py:1-400 load (entry point)\n" + "\n".join(f"{n}: value_{n} = compute(value_{n - 1})" for n in range(2, 400))
        groups = _split(["=== small.py:1-2 tiny (helper)\n1: pass", block, "DEEPER HELPERS (bodies not shown):\nx.py:1-9 helper"], 2000)
        self.assertTrue(all(len("\n\n".join(group)) <= 2000 for group in groups))
        joined = "\n".join("\n".join(group) for group in groups)
        self.assertIn("=== big.py:1-400 load (continued)\n", joined)
        lines = [line for line in joined.splitlines() if line.split(":")[0].isdigit()]
        self.assertEqual(lines, ["1: pass"] + [f"{n}: value_{n} = compute(value_{n - 1})" for n in range(2, 400)])

    def test_parts_print_what_part_one_hands_over(self):
        import io
        import json
        import time
        from balecontext import hook
        helpers = "".join(f"def step_{i}(order):\n" + "".join(f"    order.total += {j}  # adjust the order total\n" for j in range(40)) + "    return order\n\n" for i in range(8))
        entry = "def checkout_order(order):\n" + "".join(f"    order = step_{i}(order)\n" for i in range(8)) + "    return order\n"
        (self.root / "shop.py").write_text(helpers + entry, encoding="utf-8")
        Project(self.root).index()

        def run(prompt: str, part: int, session: str) -> str:
            output = io.StringIO()
            event = {"prompt": prompt, "cwd": str(self.root), "session_id": session, "hook_event_name": "UserPromptSubmit"}
            # Claude Code starts the parts with part 1; here they start after it, however slow the machine.
            with mock.patch.object(hook, "PART_SKEW", 600):
                self.assertEqual(hook.run_claude(io.StringIO(json.dumps(event)), output, part=part), 0)
            return output.getvalue()

        question = "How does checkout_order work through each step?"
        main = json.loads(run(question, 1, "conversation-1"))["hookSpecificOutput"]["additionalContext"]
        count = int(re.search(r"BaleContext parts? 2(?: to (\d))?", main).group(1) or 2) - 1
        pieces = [run(question, part, "conversation-1") for part in range(2, hook.EXTRA_PARTS + 2)]
        for number, piece in enumerate(pieces, 2):
            if number - 2 < count:
                self.assertTrue(json.loads(piece)["hookSpecificOutput"]["additionalContext"].startswith(f"BaleContext evidence, part {number} of {count + 1}"))
            else:
                self.assertEqual(piece, "")
        log = json.loads((self.root / ".balecontext" / "hook-log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(len(log["part_chars"]), count)
        # Chat gets nothing from any part, and a part whose part 1 never ran stops waiting soon.
        self.assertEqual(run("thanks, that was really helpful", 1, "conversation-1"), "")
        self.assertEqual(run("thanks, that was really helpful", 2, "conversation-1"), "")
        started = time.monotonic()
        with mock.patch.object(hook, "PART_START", 0.2):
            self.assertEqual(run("How does checkout_order work in another conversation?", 2, "conversation-2"), "")
        self.assertLess(time.monotonic() - started, 3)

    def test_saved_file_outlines_calls_outside_the_chain_and_names_their_helpers(self):
        steps = "".join(f"def step_{i}(order):\n" + "".join(f"    order.total += {j}  # adjust the order total\n" for j in range(40)) + "    return order\n\n" for i in range(8))
        entry = "def checkout_order(order, gateway):\n" + "".join(f"    order = step_{i}(order)\n" for i in range(8)) + "    gateway.settle_account(order)\n    return order\n"
        (self.root / "shop.py").write_text(steps + entry, encoding="utf-8")
        (self.root / "billing.py").write_text(
            "class PaymentGateway:\n"
            '    def audit_ledger(self, order):\n        """Write the order to the audit ledger."""\n' + "".join(f"        order.audit += {j}\n" for j in range(5)) + "        return order\n\n"
            "    def settle_account(self, order):\n        # settle_marker() is only a comment\n        balance = order.total * 2\n" + "".join(f"        balance += {j}\n" for j in range(35))
            + "        if balance > 100:\n            self.audit_ledger(order)\n        return balance\n", encoding="utf-8")
        from balecontext import hook
        project = Project(self.root)
        project.index()
        text, response = hook.prompt_pack(project, "How does checkout_order work?")
        rest = Path(response["overflow_path"]).read_text(encoding="utf-8")
        outline = rest[rest.index("=== billing.py:11-51 PaymentGateway.settle_account"):]
        outline = outline[:outline.index("\n\n")]
        # A long call followed outside the chain keeps its signature, branches, calls and return.
        for kept in ("11: def settle_account(self, order):", "49:     if balance > 100:", "50:         self.audit_ledger(order)", "51:     return balance", "[Outline of lines 11-51"):
            self.assertIn(kept, outline)
        for dropped in ("balance += 3", "settle_marker"):
            self.assertNotIn(dropped, outline)
        # Its own helper is named in one line with the start of its docstring, not shown in full.
        self.assertIn("billing.py:2-9 PaymentGateway.audit_ledger (called by PaymentGateway.settle_account at line 50): Write the order to the audit ledger.", rest)
        self.assertNotIn("order.audit += 3", rest)
        self.assertEqual([s["name"] for s in response["overflow_sources"] if s["path"] == "billing.py"], ["PaymentGateway.settle_account"])

    def test_a_callers_list_cut_short_goes_on_in_the_parts(self):
        (self.root / "settings.py").write_text("def merge_setting(request_setting, session_setting):\n    return request_setting or session_setting\n", encoding="utf-8")
        (self.root / "tests").mkdir()
        (self.root / "tests/test_settings.py").write_text(
            "from settings import merge_setting\n\n\n" + "".join(f"def test_merge_{i}():\n    assert merge_setting({i}, None) == {i}\n\n\n" for i in range(60)), encoding="utf-8")
        from balecontext import hook
        project = Project(self.root)
        project.index()
        text, response = hook.prompt_pack(project, "Who calls merge_setting?")
        self.assertIn("[and 21 more lines]", text)
        self.assertIn("the rest of the evidence (the rest of TESTS naming merge_setting) follows in BaleContext part 2 below", text)
        rest = "\n".join(response["parts"])
        self.assertIn("=== TESTS naming merge_setting (continued from the pack above)", rest)
        # Each test line arrives once, in the pack or in the parts, which end with the count.
        for i in range(60):
            self.assertEqual((text + rest).count(f"assert merge_setting({i}, None) == {i}"), 1)
        self.assertIn("COVERAGE: COMPLETE. Every line in the project that names merge_setting is shown or listed above", rest)

    def test_a_callers_question_may_name_a_plain_word(self):
        (self.root / "compose.py").write_text("def compose(middleware):\n    return middleware\n\n\ndef route(app):\n    return compose(app.middleware)\n", encoding="utf-8")
        from balecontext import hook
        project = Project(self.root)
        project.index()
        found = hook.prompt_pack(project, "Who calls compose?")
        self.assertIsNotNone(found)
        self.assertIn("route (calls compose at line 6)", found[0])
        # A word the question does not write as a name gets no pack.
        self.assertIsNone(hook.prompt_pack(project, "Where is the compose step used?"))

    def test_an_outline_covers_the_whole_definition_behind_a_cut_block(self):
        body = "".join(f"    if kind == {i}:\n        return handle_{i}(request)\n" for i in range(60))
        (self.root / "dispatch.py").write_text("def dispatch(request, kind):\n" + body + "    return None\n", encoding="utf-8")
        from balecontext import hook
        project = Project(self.root)
        project.index()
        with project.connect() as db:
            item_id = db.execute("SELECT id FROM nodes WHERE name='dispatch'").fetchone()[0]
        block = ("=== dispatch.py:1-122 dispatch (called by route at line 3)\n" + "\n".join(f"{n}: pass" for n in range(1, 31))
                 + "\n[Excerpt ends at line 30 of 122; read the rest from the file.]")
        outline = hook._outline(hook._whole(project, block, item_id))
        # The last cases are the ones a cut excerpt hid.
        self.assertIn("120:     if kind == 59:", outline)
        self.assertIn("121:         return handle_59(request)", outline)
        self.assertNotIn("[Excerpt ends", outline)

    def test_parts_that_would_not_fit_outline_definitions_the_question_does_not_name(self):
        steps = "".join(f"def step_{i}(order):\n" + "".join(f"    order.total += {j}  # adjust the order total\n" for j in range(40)) + "    return order\n\n" for i in range(8))
        entry = "def checkout_order(order):\n" + "".join(f"    order = step_{i}(order)\n" for i in range(8)) + "    return order\n"
        (self.root / "shop.py").write_text(steps + entry, encoding="utf-8")
        from balecontext import hook
        project = Project(self.root)
        project.index()
        self.assertGreater(len(hook.prompt_pack(project, "How does checkout_order work?")[1]["parts"]), 1)
        with mock.patch.object(hook, "EXTRA_PARTS", 1):
            text, response = hook.prompt_pack(project, "How does checkout_order work?")
        # One part instead of a file to read in a second call.
        self.assertEqual(len(response["parts"]), 1)
        self.assertIn("as an outline of calls, branches and returns", response["parts"][0])
        self.assertIn("follows in BaleContext part 2 below", text)

    def test_follow_up_about_sent_code_gets_only_new_source_it_names(self):
        steps = "".join(f"def step_{i}(order):\n" + "".join(f"    order.total += {j}  # adjust the order total\n" for j in range(40)) + "    return order\n\n" for i in range(8))
        entry = "def checkout_order(order, gateway):\n" + "".join(f"    order = step_{i}(order)\n" for i in range(8)) + "    gateway.settle_account(order)\n    return order\n"
        (self.root / "shop.py").write_text(steps + entry, encoding="utf-8")
        (self.root / "billing.py").write_text(
            "class PaymentGateway:\n"
            '    def audit_ledger(self, order):\n        """Write the order to the audit ledger."""\n' + "".join(f"        order.audit += {j}\n" for j in range(5)) + "        return order\n\n"
            "    def settle_account(self, order):\n        balance = order.total * 2\n" + "".join(f"        balance += {j}\n" for j in range(35))
            + "        if balance > 100:\n            self.audit_ledger(order)\n        return balance\n", encoding="utf-8")
        from balecontext import hook
        project = Project(self.root)
        project.index()
        session = {}
        hook.prompt_pack(project, "How does checkout_order work?", session=session)
        text, response = hook.prompt_pack(project, "When checkout_order settles the account, how is the audit ledger written?", session=session)
        self.assertTrue(response["followup"])
        # The steps went out with the first question; only the calls this question names are new.
        self.assertRegex(text, r"(?m)^ALREADY SENT: checkout_order shop.py:\d+-\d+; step_0 ")
        self.assertNotIn("order.total += 39", text)
        self.assertIn("balance += 34", text)
        self.assertIn("order.audit += 4", text)
        self.assertNotIn("overflow_path", response)

    def test_project_name_in_a_question_is_not_a_definition_name(self):
        # Every code file is under django/, so "Django's" names the project, not GeoType.django.
        (self.root / "django/gis").mkdir(parents=True)
        (self.root / "django/gis/geo.py").write_text(
            "class GeoType:\n    @property\n    def django(self):\n        return self.name\n", encoding="utf-8")
        (self.root / "django/urls").mkdir(parents=True)
        (self.root / "django/urls/resolvers.py").write_text(
            "class Resolver:\n    def resolve(self, path):\n        for pattern in self.patterns:\n"
            "            if pattern.match(path):\n                return pattern\n        return None\n", encoding="utf-8")
        response = context(Project(self.root), "How does Django's URL resolver match a request path?", 2000)
        names = [source["name"] for source in response["sources"]]
        self.assertEqual(names[0], "Resolver.resolve")
        self.assertNotIn("GeoType.django", names)

    def test_a_describing_word_is_not_taken_as_a_function_name(self):
        (self.root / "django/template").mkdir(parents=True)
        (self.root / "django/template/utils.py").write_text(
            "class EngineHandler:\n    @property\n    def templates(self):\n        return self._templates\n", encoding="utf-8")
        (self.root / "django/template/loader_tags.py").write_text(
            "class ExtendsNode:\n    def render(self, context):\n        parent = self.get_parent(context)\n"
            "        return parent.render(context)\n\n\nclass BlockNode:\n    def render(self, context):\n"
            "        return self.nodelist.render(context)\n", encoding="utf-8")
        response = context(Project(self.root), "How does Django's template engine render the extends and block tags?", 2000)
        names = [source["name"] for source in response["sources"]]
        # "template" describes the engine here; the tags' own render methods are the answer.
        self.assertIn(names[0], ("ExtendsNode.render", "BlockNode.render"))
        self.assertNotIn("EngineHandler.templates", names[:2])

    def test_follow_up_shows_the_definition_it_asks_about_whole(self):
        helpers = "".join(f"def stamp_{i}(req):\n" + "".join(f"    req.headers[{j}] = {j}  # stamp a header on the request\n" for j in range(24)) + "    return req\n\n" for i in range(4))
        redirects = "def follow_redirects(resp, req):\n" + "".join(f"    resp.hops += {j}  # count one redirect hop\n" for j in range(90)) + "    return resp\n\n"
        entry = "def send_request(req):\n" + "".join(f"    req = stamp_{i}(req)\n" for i in range(4)) + "    resp = follow_redirects(req, req)\n    return resp\n"
        (self.root / "client.py").write_text(helpers + redirects + entry, encoding="utf-8")
        project = Project(self.root)
        project.index()
        session = {}
        context(project, "How does send_request work?", 400, session=session, focus=True)
        response = context(project, "How does send_request handle redirects?", 2400, session=session, focus=True)
        self.assertTrue(response["followup"])
        # The redirect loop is what the follow-up asks about: all of it, not its first lines.
        self.assertIn("resp.hops += 89", response["text"])
        self.assertNotRegex(response["text"], r"cut short: [^;)]*follow_redirects")

    def test_entry_that_calls_nothing_shows_where_it_is_used(self):
        (self.root / "utils.py").write_text(
            "def select_route(url, routes):\n    for key in (url, 'all'):\n        if key in routes:\n            return routes[key]\n    return None\n", encoding="utf-8")
        (self.root / "client.py").write_text(
            "from utils import select_route\n\n\nclass Client:\n"
            '    def connect(self, url, routes):\n        """Deprecated: use open_pool."""\n        return select_route(url, routes)\n\n'
            "    def open_pool(self, url, routes):\n        route = select_route(url, routes)\n        return self.pool_for(route)\n\n"
            "    def target_url(self, url, routes):\n        route = select_route(url, routes)\n        return url if route else '/'\n", encoding="utf-8")
        response = context(Project(self.root), "How does it choose the route for a url?", 2000)
        names = [source["name"] for source in response["sources"]]
        # "choose" reads as select; the callers show how the choice is used, current code first.
        self.assertEqual(names[0], "select_route")
        self.assertIn("Client.open_pool", names)
        self.assertIn("Client.target_url", names)
        self.assertNotIn("Client.connect", names)

    def test_abstract_call_shows_the_implementations_that_run_and_their_templates(self):
        (self.root / "render.py").write_text(
            "class Renderable:\n"
            "    def get_context(self):\n        raise NotImplementedError(\n            'subclasses provide get_context()'\n        )\n\n"
            "    def render(self, template_name=None):\n        template = template_name or self.template_name\n"
            "        return self.renderer.render(template, self.get_context())\n\n    __str__ = render\n\n\n"
            "class RenderableError(Renderable):\n    def as_ul(self):\n        return self.render(self.template_name_ul)\n\n\n"
            "class ErrorList(list, RenderableError):\n"
            '    template_name = "app/errors/default.html"\n    template_name_ul = "app/errors/ul.html"\n\n'
            "    def get_context(self):\n        return {'errors': self}\n\n\n"
            "class Form(Renderable):\n    def get_context(self):\n        return {'form': self}\n\n"
            "    @property\n    def errors(self):\n        return ErrorList()\n\n\n"
            "class BoundField:\n    @property\n    def errors(self):\n        return self.form.errors.get(self.name, ErrorList())\n",
            encoding="utf-8")
        for name, body in {"default.html": '{% include "app/errors/ul.html" %}', "ul.html": "<ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul>"}.items():
            for root in ("templates", "jinja2"):
                path = self.root / root / "app/errors" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding="utf-8")
        response = context(Project(self.root), "How are a field's errors rendered in a template, for example {{ form.name.errors }} or form.errors.as_ul()?", 3000)
        names = [source["name"] for source in response["sources"]]
        paths = [source["path"] for source in response["sources"]]
        # `self.get_context()` reached from RenderableError.as_ul runs ErrorList's, not Form's.
        self.assertIn("ErrorList.get_context", names)
        self.assertNotIn("Form.get_context", names)
        self.assertNotIn("Renderable.get_context", names)
        # Its class header sets the template names the shown code reads; the templates follow.
        self.assertIn("ErrorList", names)
        self.assertIn("templates/app/errors/ul.html", paths)
        self.assertIn("templates/app/errors/default.html", paths)
        self.assertNotIn("jinja2/app/errors/ul.html", paths)
        # "a field's errors" is BoundField.errors as well as Form.errors.
        self.assertIn("BoundField.errors", names)
        self.assertIn("__str__ = render", response["text"])
        self.assertNotIn("Renderable.get_context", response["text"].split("COVERAGE")[-1])

    def test_installer_adds_the_hook_once_and_keeps_other_settings(self):
        import json
        from balecontext.integrations import HOOK_TIMEOUT, install
        settings = self.root / ".claude/settings.json"
        settings.parent.mkdir()
        settings.write_text(json.dumps({"permissions": {"allow": ["Read"]}}), encoding="utf-8")
        project = Project(self.root)
        first = install(project, "claude", hook=True)
        self.assertIn(".claude/settings.json", first["changed"])
        second = install(project, "claude", hook=True)
        self.assertNotIn(".claude/settings.json", second["changed"])
        data = json.loads(settings.read_text(encoding="utf-8"))
        self.assertEqual(data["permissions"], {"allow": ["Read"]})
        for event in ("UserPromptSubmit", "PreCompact"):
            self.assertEqual(len(data["hooks"][event]), 1)
            self.assertEqual(data["hooks"][event][0]["hooks"][0]["args"][-2:], ["hook", "claude"])
        # Part 1 builds the evidence; the other parts carry what does not fit in one hook's context.
        self.assertEqual([h["args"][4:] for h in data["hooks"]["UserPromptSubmit"][0]["hooks"]], [[]] + [["--part", str(n)] for n in range(2, 6)])
        self.assertEqual(len(data["hooks"]["PreCompact"][0]["hooks"]), 1)
        # An earlier install with part 1 alone gets the other parts added once.
        data["hooks"]["UserPromptSubmit"][0]["hooks"] = data["hooks"]["UserPromptSubmit"][0]["hooks"][:1]
        data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["timeout"] = 30
        settings.write_text(json.dumps(data), encoding="utf-8")
        self.assertIn(".claude/settings.json", install(project, "claude", hook=True)["changed"])
        self.assertNotIn(".claude/settings.json", install(project, "claude", hook=True)["changed"])
        data = json.loads(settings.read_text(encoding="utf-8"))
        parts = [h["args"][4:] for group in data["hooks"]["UserPromptSubmit"] for h in group["hooks"]]
        self.assertEqual(sorted(parts), sorted([[]] + [["--part", str(n)] for n in range(2, 6)]))
        # An earlier install's shorter time limit is raised too.
        self.assertEqual({h["timeout"] for group in data["hooks"]["UserPromptSubmit"] for h in group["hooks"]}, {HOOK_TIMEOUT})
        with self.assertRaises(ValueError):
            install(project, "codex", hook=True)
        self.assertFalse((self.root / ".codex").exists())


if __name__ == "__main__":
    unittest.main()
