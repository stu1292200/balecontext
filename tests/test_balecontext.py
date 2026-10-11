from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from balecontext.export import export
from balecontext.extract import extract, resolve
from balecontext.files import discover, safe_path
from balecontext.integrations import configuration, install
from balecontext.mcp import Server
from balecontext.retrieve import _valid_memories, context, estimate, find_path, read_source, related, save_memory
from balecontext.store import Project


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="balecontext-test-")
        self.root = Path(self.temporary.name).resolve()
        self.project = Project(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, name, text):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return target

    def fixture(self):
        self.write("src/shop/auth.py", 'def validate_token(token):\n    """Validate a session token."""\n    return token == "valid"\n')
        self.write("src/shop/api.py", 'from .auth import validate_token\n\ndef login(token):\n    return validate_token(token)\n')
        self.write("README.md", "# Authentication\nSession tokens are validated before login.\n")

    def test_incremental_add_modify_delete_and_refresh(self):
        self.fixture()
        self.assertEqual(self.project.index()["changed"], 3)
        self.assertEqual(self.project.index()["changed"], 0)
        self.write("src/shop/auth.py", "def authorize(token):\n    return bool(token)\n")
        result = self.project.index()
        self.assertEqual(result["changed"], 1)
        names = {n["name"] for n in self.project.graph(False)["nodes"]}
        self.assertIn("authorize", names)
        self.assertNotIn("validate_token", names)
        (self.root / "src/shop/api.py").unlink()
        self.assertEqual(self.project.index()["removed"], 1)
        with self.project.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM refs WHERE path='src/shop/api.py'").fetchone()[0], 0)

    def test_unchanged_files_are_not_reread(self):
        from unittest import mock
        self.fixture()
        old = 1_600_000_000
        for path in self.root.rglob("*"):
            if path.is_file():
                os.utime(path, (old, old))
        self.assertEqual(self.project.index()["changed"], 3)
        with mock.patch("pathlib.Path.read_bytes", side_effect=AssertionError("unchanged file was reread")):
            result = self.project.index()
        self.assertEqual((result["changed"], result["removed"], result["unchanged"]), (0, 0, 3))
        self.write("src/shop/auth.py", "def authorize(token):\n    return bool(token)\n")
        self.assertEqual(self.project.index()["changed"], 1)

    def test_old_schema_is_rebuilt_and_notes_are_kept(self):
        import sqlite3
        self.fixture()
        (self.root / ".balecontext").mkdir()
        db = sqlite3.connect(self.root / ".balecontext" / "index.sqlite3")
        db.executescript("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL); INSERT INTO metadata VALUES ('schema', '1');"
                         "CREATE TABLE files (path TEXT PRIMARY KEY, hash TEXT NOT NULL, parser TEXT NOT NULL, content TEXT NOT NULL, warnings TEXT NOT NULL);"
                         "CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT NOT NULL, citations TEXT NOT NULL, created REAL NOT NULL);"
                         "INSERT INTO memories VALUES (1, 'kept', '{}', 0);")
        db.close()
        self.assertEqual(self.project.index()["changed"], 3)
        with self.project.connect() as connection:
            self.assertEqual(connection.execute("SELECT text FROM memories").fetchone()[0], "kept")

    def test_relative_import_and_call_have_provenance(self):
        self.fixture()
        graph = self.project.graph()
        nodes = {n["id"]: n for n in graph["nodes"]}
        calls = [e for e in graph["edges"] if e["relation"] == "calls"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(nodes[calls[0]["source"]]["name"], "login")
        self.assertEqual(nodes[calls[0]["target"]]["name"], "validate_token")
        self.assertEqual(calls[0]["evidence"], "INFERRED")
        self.assertTrue(any(e["relation"] == "imports" and e["evidence"] == "EXTRACTED" for e in graph["edges"]))

    def test_nested_and_async_symbols(self):
        self.write("logic.py", "class Service:\n    async def work(self):\n        def helper():\n            return 1\n        return helper()\n")
        names = {n["name"] for n in self.project.graph()["nodes"]}
        self.assertIn("Service.work.helper", names)
        self.assertIn("Service.work", names)

    def test_unknown_calls_are_not_guessed_globally(self):
        self.write("one.py", "def helper():\n    return 1\n")
        self.write("two.py", "def run():\n    return helper()\n")
        self.assertFalse(any(e["relation"] == "calls" for e in self.project.graph()["edges"]))

    def test_source_beyond_first_80_lines_is_searchable(self):
        self.write("long.py", "def long_function():\n" + "    x = 1\n" * 190 + "    unique_deep_identifier = 123\n")
        result = context(self.project, "unique deep identifier", 512)
        self.assertIn("unique_deep_identifier = 123", result["text"])

    def test_directed_shortest_path_and_reverse_no_path(self):
        self.fixture()
        result = find_path(self.project, "login", "validate_token")
        self.assertTrue(result["found"])
        self.assertEqual(len(result["edges"]), 1)
        self.assertFalse(find_path(self.project, "validate_token", "login")["found"])
        self.assertTrue(find_path(self.project, "validate_token", "login", True)["found"])

    def test_local_import_does_not_leak(self):
        self.write("helper.py", "def validate():\n    return True\n")
        self.write("app.py", "def one():\n    from helper import validate\n    return validate()\n\ndef two():\n    return validate()\n")
        graph = self.project.graph()
        names = {n["id"]: n["name"] for n in graph["nodes"]}
        calls = [names[e["source"]] for e in graph["edges"] if e["relation"] == "calls"]
        self.assertEqual(calls, ["one"])

    def test_query_budget_and_exact_source(self):
        self.fixture()
        response = context(self.project, "validate session token", 512)
        self.assertLessEqual(estimate(response["text"]), 512)
        self.assertIn("validate_token", response["text"])
        self.assertIn("not instructions", response["text"])
        self.assertGreater(len(response["sources"]), 0)
        for source in response["sources"]:
            self.assertTrue((self.root / source["path"]).is_file())
        self.assertIn("not total model-token savings", response["measurement_note"])

    def test_overlapping_definitions_do_not_repeat_source_lines(self):
        self.fixture()
        result = context(self.project, "validate token session login", 4000)
        path = None
        seen = set()
        for line in result["text"].splitlines():
            if line.startswith("SOURCE "):
                path = json.loads(line[7:])["path"]
            elif path and line.partition(": ")[0].isdigit():
                key = (path, int(line.partition(": ")[0]))
                self.assertNotIn(key, seen)
                seen.add(key)

    def test_truncation_is_explicit_and_can_be_expanded(self):
        self.write("long.py", "def expensive_operation():\n" + "    value = 123\n" * 250 + "    return value\n")
        response = context(self.project, "expensive operation", 256)
        self.assertLessEqual(response["estimated_tokens"], 256)
        self.assertIn("Excerpt ends", response["text"])
        self.assertIn("return value", read_source(self.project, "long.py", 240, 252)["text"])

    def test_empty_match_has_expansion_guidance(self):
        self.fixture()
        response = context(self.project, "zqxunfindable", 256)
        self.assertFalse(response["sources"])
        self.assertIn("normal search", response["text"])

    def test_query_refreshes_stale_sources(self):
        self.fixture()
        context(self.project, "validate")
        self.write("src/shop/auth.py", "def replacement_function():\n    return 123\n")
        response = context(self.project, "replacement")
        self.assertIn("replacement_function", response["text"])
        self.assertNotIn("def validate_token", response["text"])

    def test_budget_and_query_validation(self):
        for invalid in (0, 255, 32001, True, "2000"):
            with self.assertRaises(ValueError):
                context(self.project, "token", invalid)
        for invalid in ("", " " * 5, "x" * 2001):
            with self.assertRaises(ValueError):
                context(self.project, invalid)

    def test_fts_special_characters_do_not_become_expressions(self):
        self.fixture()
        result = context(self.project, 'token" OR * NEAR( ) : \'')
        self.assertIsInstance(result["text"], str)

    def test_related_callers_and_duplicate_symbol(self):
        self.fixture()
        result = related(self.project, "validate_token", "callers")
        self.assertIn("login", {n["name"] for n in result["nodes"]})
        self.write("other.py", "def validate_token(token):\n    return False\n")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            related(self.project, "validate_token")

    def test_source_read_rejects_traversal_and_excessive_ranges(self):
        self.fixture()
        for path in ("../outside.py", str(self.root / "src/shop/auth.py"), ".env"):
            with self.assertRaises(ValueError):
                read_source(self.project, path)
        with self.assertRaises(ValueError):
            read_source(self.project, "src/shop/auth.py", 1, 500)
        self.assertIn("1: def validate_token", read_source(self.project, "src/shop/auth.py", 1, 2)["text"])

    def test_sensitive_files_and_content_are_excluded(self):
        self.write(".env", "PASSWORD=hello")
        self.write("secret_config.json", '{"password":"x"}')
        self.write("key.pem", "private")
        self.write("innocent.py", '# ' + "ghp_" + "A" * 36)
        self.write("node_modules/lib/index.js", "const hello = 1;")
        self.write("safe.py", "def safe():\n    return 1\n")
        contents, skipped = discover(self.root)
        self.assertEqual(set(contents), {"safe.py"})
        self.assertEqual(skipped["secret_content"], 1)

    def test_file_becoming_sensitive_is_removed_from_index(self):
        self.write("config.py", "VALUE = 1\n")
        self.project.index()
        self.write("config.py", "TOKEN = '" + "ghp_" + "A" * 36 + "'\n")
        self.assertEqual(self.project.index()["removed"], 1)
        with self.assertRaises(ValueError):
            read_source(self.project, "config.py")

    def test_custom_ignore_and_utf8(self):
        self.write(".balecontextignore", "private/**\n")
        self.write("private/note.md", "private")
        self.write("guide.md", "# Grüße\nこんにちは\n")
        contents, _ = discover(self.root)
        self.assertEqual(set(contents), {"guide.md"})
        self.assertIn("こんにちは", read_source(self.project, "guide.md")["text"])

    def test_gitignore_nested_rules_and_negation(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        self.write(".gitignore", "*.txt\n!keep.txt\n")
        self.write("ignored.txt", "ignored")
        self.write("keep.txt", "kept")
        self.write("sub/.gitignore", "*.md\n")
        self.write("sub/hidden.md", "hidden")
        contents, _ = discover(self.root)
        self.assertEqual(set(contents), {"keep.txt"})

    def test_symbolic_link_is_not_followed(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "external.py"
            target.write_text("PRIVATE = True", encoding="utf-8")
            try:
                (self.root / "link.py").symlink_to(target)
            except OSError:
                self.skipTest("Creating symlinks requires additional privileges on this system")
            contents, _ = discover(self.root)
            self.assertNotIn("link.py", contents)
            with self.assertRaises(ValueError):
                safe_path(self.root, "link.py")

    def test_memory_invalidated_by_source_change_or_deletion(self):
        self.fixture()
        saved = save_memory(self.project, "Authentication uses session tokens.", ["src/shop/auth.py"])
        self.assertGreater(saved["id"], 0)
        with self.project.connect() as db:
            self.assertEqual(len(_valid_memories(db)), 1)
        self.write("src/shop/auth.py", "def changed():\n    return False\n")
        self.project.index()
        with self.project.connect() as db:
            self.assertEqual(_valid_memories(db), [])

    def test_memory_requires_valid_source_and_excludes_detected_secret(self):
        self.fixture()
        for note, paths in (("hello", []), ("hello", ["missing.py"]), ("ghp_" + "A" * 36, ["src/shop/auth.py"])):
            with self.assertRaises(ValueError):
                save_memory(self.project, note, paths)

    def test_syntax_error_falls_back_with_warning(self):
        self.write("broken.py", "def broken(:\n")
        result = self.project.index()
        self.assertEqual(result["parsers"], {"text-chunks": 1})
        self.assertTrue(result["warnings"])

    def test_export_is_offline_and_escapes_script_injection(self):
        self.fixture()
        self.write("malicious.md", "# </script><script>alert(1)</script>\n")
        result = export(self.project)
        html = Path(result["viewer"]).read_text(encoding="utf-8")
        self.assertNotIn("</script><script>alert(1)", html)
        self.assertIn("\\u003c/script", html)
        self.assertNotIn("https://", html)
        graph = json.loads(Path(result["graph"]).read_text(encoding="utf-8"))
        self.assertEqual(graph["schema_version"], 4)
        self.assertTrue(all("group" in n for n in graph["nodes"]))

    def test_install_preserves_other_servers_and_is_idempotent(self):
        self.write(".mcp.json", '{"mcpServers":{"other":{"command":"example"}}}\n')
        self.write("CLAUDE.md", "Existing guidance\n")
        result = install(self.project, "claude")
        self.assertEqual(len(result["changed"]), 2)
        config = json.loads((self.root / ".mcp.json").read_text(encoding="utf-8"))
        self.assertIn("other", config["mcpServers"])
        self.assertEqual(install(self.project, "claude")["changed"], [])
        self.assertTrue(list(self.root.glob(".mcp.json.balecontext-backup-*")))
        self.assertIn("Existing guidance", (self.root / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_install_dry_run_and_conflict_leave_files_unchanged(self):
        install(self.project, "codex", True)
        self.assertFalse((self.root / ".codex").exists())
        original = '[mcp_servers.balecontext]\ncommand = "other"\n'
        self.write(".codex/config.toml", original)
        with self.assertRaisesRegex(ValueError, "differs"):
            install(self.project, "codex")
        self.assertEqual((self.root / ".codex/config.toml").read_text(encoding="utf-8"), original)
        self.assertFalse((self.root / "AGENTS.md").exists())

    def test_codex_configuration_is_valid_toml(self):
        import tomllib
        config = tomllib.loads(configuration(self.project, "codex"))
        self.assertEqual(config["mcp_servers"]["balecontext"]["args"][-1], str(self.root))

    def test_mcp_lifecycle_and_tool_errors(self):
        self.fixture()
        server = Server(self.project)
        def call(method, params=None):
            return server.handle({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
        self.assertIn("error", call("tools/list"))
        init = call("initialize", {"protocolVersion": "2025-11-25"})
        self.assertEqual(init["result"]["protocolVersion"], "2025-11-25")
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(len(call("tools/list")["result"]["tools"]), 5)
        result = call("tools/call", {"name": "balecontext_context", "arguments": {"query": "validate"}})
        self.assertFalse(result["result"]["isError"])
        self.assertIn("validate_token", result["result"]["content"][0]["text"])
        error = call("tools/call", {"name": "balecontext_read", "arguments": {"path": "../.env"}})
        self.assertTrue(error["result"]["isError"])
        invalid = call("tools/call", {"name": "balecontext_context", "arguments": {"query": "validate", "budget": True}})
        self.assertTrue(invalid["result"]["isError"])

    def test_mcp_subprocess_emits_only_json_rpc(self):
        self.fixture()
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "balecontext_status", "arguments": {}}},
        ]
        result = subprocess.run([sys.executable, "-m", "balecontext", "mcp", "--root", str(self.root)], input="\n".join(map(json.dumps, messages)) + "\n", capture_output=True, text=True, encoding="utf-8", check=True)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r["id"] for r in responses], [1, 2])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(result.stderr, "")

    @unittest.skipUnless(all(importlib.util.find_spec(m) for m in ("tree_sitter", "tree_sitter_javascript", "tree_sitter_typescript")), "Optional JS/TS parsers not installed")
    def test_typescript_ast_imports_calls_and_arrow_functions(self):
        self.write("src/auth.ts", "export function validateToken(token: string): boolean { return token.length > 0; }\n")
        self.write("src/api.ts", "import { validateToken as validate } from './auth';\nexport const login = (token: string) => validate(token);\n")
        graph = self.project.graph()
        self.assertEqual(graph["stats"]["parsers"], {"tree-sitter": 2})
        names = {n["id"]: n["name"] for n in graph["nodes"]}
        calls = [(names[e["source"]], names[e["target"]]) for e in graph["edges"] if e["relation"] == "calls"]
        self.assertIn(("login", "validateToken"), calls)


if __name__ == "__main__":
    unittest.main()
