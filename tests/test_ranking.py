from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

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

    def run_hook(self, prompt: str, cwd: Path) -> str:
        import io
        import json
        from balecontext.hook import run_claude
        output = io.StringIO()
        self.assertEqual(run_claude(io.StringIO(json.dumps({"prompt": prompt, "cwd": str(cwd)})), output), 0)
        return output.getvalue()

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

    def test_installer_adds_the_hook_once_and_keeps_other_settings(self):
        import json
        from balecontext.integrations import install
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
        self.assertEqual(len(data["hooks"]["UserPromptSubmit"]), 1)
        self.assertEqual(data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["args"][-2:], ["hook", "claude"])
        with self.assertRaises(ValueError):
            install(project, "codex", hook=True)
        self.assertFalse((self.root / ".codex").exists())


if __name__ == "__main__":
    unittest.main()
