from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from balecontext import rank
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


if __name__ == "__main__":
    unittest.main()
