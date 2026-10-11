from __future__ import annotations

import json
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from balecontext.files import discover
from balecontext.retrieve import _valid_memories, save_memory
from balecontext.store import Project
from balecontext.watch import IndexWatcher


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="balecontext-watch-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = Project(self.root)
        self.source = self.root / "auth.py"
        self.source.write_text("def authorize():\n    return 1\n", encoding="utf-8")

    def wait_for(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.025)
        self.fail("Timed out waiting for a background index update.")

    def indexed(self):
        # Read SQLite directly: polling this assertion must not trigger indexing.
        if not self.project.db_path.exists():
            return {}
        try:
            with closing(sqlite3.connect(self.project.db_path, timeout=0.1)) as db:
                return {path: content.replace("\r\n", "\n") for path, content in db.execute("SELECT path,content FROM files")}
        except sqlite3.OperationalError:
            return {}

    def test_updates_edits_renames_deletions_and_invalidates_notes_without_queries(self):
        save_memory(self.project, "authorize returns one", ["auth.py"])
        watcher = IndexWatcher(self.project, 0.2)
        try:
            watcher.start()
            watcher.start()  # Starting an already running connection is harmless.
            self.source.write_text("def authorize():\n    return 2\n", encoding="utf-8")
            moved = self.root / "access.py"
            self.source.rename(moved)
            guide = self.root / "guide.md"
            guide.write_text("Use authorize for access.\n", encoding="utf-8")
            self.wait_for(lambda: self.indexed() == {
                "access.py": "def authorize():\n    return 2\n",
                "guide.md": "Use authorize for access.\n",
            })
            with self.project.connect(snapshot=True) as db:
                self.assertEqual(_valid_memories(db), [])
                self.assertEqual(db.execute("SELECT count(*) FROM nodes WHERE path='auth.py'").fetchone()[0], 0)
            guide.unlink()
            self.wait_for(lambda: set(self.indexed()) == {"access.py"})
            self.assertTrue(watcher.status()["active"])
        finally:
            watcher.stop()
        self.assertFalse(watcher.status()["active"])

    def test_transient_background_failure_recovers(self):
        allow_success = threading.Event()
        succeeded = threading.Event()

        def index():
            if not allow_success.is_set():
                raise OSError("Private path must not appear in diagnostics")
            succeeded.set()

        watcher = IndexWatcher(SimpleNamespace(index=index), 0.2)
        try:
            watcher.start()
            self.wait_for(lambda: watcher.last_error is not None)
            self.assertNotIn("Private path", watcher.last_error)
            allow_success.set()
            self.assertTrue(succeeded.wait(10))
            self.wait_for(lambda: watcher.last_error is None)
        finally:
            watcher.stop()

    def test_watch_interval_rejects_busy_loops_and_nonfinite_values(self):
        for interval in (0, -1, 0.1, float("nan"), float("inf")):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                IndexWatcher(self.project, interval)

    def test_reader_retains_one_snapshot_during_concurrent_update(self):
        self.project.index()
        errors = []

        def update():
            try:
                Project(self.root).index()
            except Exception as exc:
                errors.append(exc)

        with self.project.connect(snapshot=True) as reader:
            before = reader.execute("SELECT content FROM files WHERE path='auth.py'").fetchone()[0]
            self.source.write_text("def replacement():\n    return 2\n", encoding="utf-8")
            thread = threading.Thread(target=update, daemon=True)
            thread.start()
            thread.join(10)
            self.assertFalse(thread.is_alive(), "WAL reader blocked the update")
            self.assertEqual(errors, [])
            self.assertEqual(reader.execute("SELECT content FROM files WHERE path='auth.py'").fetchone()[0], before)
            self.assertIn("authorize", {r[0] for r in reader.execute("SELECT name FROM nodes")})
        self.assertIn("replacement", self.indexed()["auth.py"])

    def test_two_indexers_cannot_commit_scans_out_of_order(self):
        self.project.index()
        older_scanned = threading.Event()
        release_older = threading.Event()
        newer_started = threading.Event()
        newer_scanned = threading.Event()
        errors = []

        def controlled_discover(root):
            result = discover(root)
            if threading.current_thread().name == "older-scan":
                older_scanned.set()
                if not release_older.wait(10):
                    raise TimeoutError("Older scan was never released")
            else:
                newer_scanned.set()
            return result

        def update(newer=False):
            if newer:
                newer_started.set()
            try:
                Project(self.root).index()
            except Exception as exc:
                errors.append(exc)

        older = threading.Thread(target=update, name="older-scan", daemon=True)
        newer = threading.Thread(target=update, args=(True,), name="newer-scan", daemon=True)
        with patch("balecontext.store.discover", controlled_discover):
            try:
                older.start()
                self.assertTrue(older_scanned.wait(10))
                self.source.write_text("def latest():\n    return 3\n", encoding="utf-8")
                newer.start()
                self.assertTrue(newer_started.wait(10))
                self.assertFalse(newer_scanned.wait(0.2), "Newer scan bypassed the index write lock")
            finally:
                release_older.set()
                older.join(10)
                if newer.ident is not None:
                    newer.join(10)
        self.assertFalse(older.is_alive())
        self.assertFalse(newer.is_alive())
        self.assertEqual(errors, [])
        self.assertIn("latest", self.indexed()["auth.py"])

    def test_mcp_starts_background_refresh_and_stops_on_disconnect(self):
        process = subprocess.Popen(
            [sys.executable, "-m", "balecontext", "mcp", "--root", str(self.root), "--watch-interval", "0.2"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        replies = queue.Queue()
        reader = threading.Thread(target=lambda: replies.put(process.stdout.readline()), daemon=True)
        try:
            initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}}
            process.stdin.write(json.dumps(initialize) + "\n")
            process.stdin.flush()
            reader.start()
            self.assertEqual(json.loads(replies.get(timeout=10))["id"], 1)
            self.wait_for(lambda: "auth.py" in self.indexed())
            # Do not send any query: the open connection alone must update the map.
            self.source.write_text("def changed_while_connected():\n    return 4\n", encoding="utf-8")
            self.wait_for(lambda: "changed_while_connected" in self.indexed().get("auth.py", ""))
            status = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "balecontext_status", "arguments": {}}}
            output, error = process.communicate(json.dumps(status) + "\n", timeout=10)
            responses = [json.loads(line) for line in output.splitlines()]
            self.assertEqual([r["id"] for r in responses], [2])
            details = json.loads(responses[0]["result"]["content"][0]["text"])
            self.assertTrue(details["background_refresh"]["active"])
            self.assertEqual(error, "")
            self.assertEqual(process.returncode, 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
            reader.join(timeout=1)

    def test_no_watch_option_preserves_foreground_refresh(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "balecontext_status"}},
        ]
        result = subprocess.run(
            [sys.executable, "-m", "balecontext", "mcp", "--root", str(self.root), "--no-watch"],
            input="\n".join(map(json.dumps, messages)) + "\n", capture_output=True,
            text=True, encoding="utf-8", check=True, timeout=10,
        )
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        details = json.loads(responses[1]["result"]["content"][0]["text"])
        self.assertEqual(details["files"], 1)
        self.assertFalse(details["background_refresh"]["active"])
        self.assertEqual(result.stderr, "")
