"""Incremental, transactional SQLite index with file-backed provenance."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from .extract import extract, resolve
from .files import scan

SCHEMA = 11


class Project:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("Workspace must be an existing directory.")
        self.state = self.root / ".balecontext"
        if self.state.is_symlink() or getattr(self.state, "is_junction", lambda: False)():
            raise ValueError("The index directory must not be a symlink or junction.")
        self.db_path = self.state / "index.sqlite3"
        if self.db_path.is_symlink():
            raise ValueError("The index database must not be a symlink.")

    @contextmanager
    def connect(self, *, snapshot: bool = False):
        self.state.mkdir(exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            version = db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
            if version and int(version[0]) != SCHEMA:
                # Derived tables are rebuilt from source; saved notes are kept.
                db.executescript("""
                    DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS nodes; DROP TABLE IF EXISTS refs;
                    DROP TABLE IF EXISTS edges; DROP TABLE IF EXISTS search;
                    DELETE FROM metadata WHERE key='schema';
                """)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY, hash TEXT NOT NULL, parser TEXT NOT NULL,
                    content TEXT NOT NULL, warnings TEXT NOT NULL,
                    size INTEGER, mtime_ns INTEGER, seen_ns INTEGER);
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY, path TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
                    start INTEGER NOT NULL, end INTEGER NOT NULL, snippet TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS nodes_path ON nodes(path);
                CREATE TABLE IF NOT EXISTS refs (path TEXT NOT NULL, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS edges (
                    source TEXT NOT NULL, target TEXT NOT NULL, relation TEXT NOT NULL, evidence TEXT NOT NULL,
                    line INTEGER, PRIMARY KEY(source, target, relation));
                CREATE INDEX IF NOT EXISTS edges_source ON edges(source);
                CREATE INDEX IF NOT EXISTS edges_target ON edges(target);
                CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(id UNINDEXED, name, path, snippet);
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY, text TEXT NOT NULL, citations TEXT NOT NULL, created REAL NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('schema', ?)", (str(SCHEMA),))
            db.commit()
            if snapshot:
                # Keep all statements in a retrieval on the same index version,
                # even when the background watcher commits an update in between.
                db.execute("BEGIN")
            yield db
        finally:
            db.close()

    def index(self) -> dict:
        started = time.monotonic()
        with self.connect() as db:
            with db:
                db.execute("BEGIN IMMEDIATE")
                # Scan after taking the write lock. Two agent processes must not
                # commit an older scan after a newer one has already been indexed.
                known = {row["path"]: (row["size"], row["mtime_ns"], row["seen_ns"]) for row in db.execute("SELECT path, size, mtime_ns, seen_ns FROM files WHERE size IS NOT NULL")}
                # Unchanged files (same size and mtime, read safely after the mtime) are not reopened.
                contents, unchanged, file_stats, skipped = scan(self.root, known)
                previous = {row["path"]: row["hash"] for row in db.execute("SELECT path, hash FROM files")}
                unchanged = {p for p in unchanged if p in previous}
                hashes = {p: hashlib.sha256(t.encode("utf-8")).hexdigest() for p, t in contents.items()}
                hashes.update({p: previous[p] for p in unchanged})
                # Installing an optional parser upgrades existing text indexes on next refresh.
                import importlib.util
                has_js = all(importlib.util.find_spec(m) is not None for m in ("tree_sitter", "tree_sitter_javascript", "tree_sitter_typescript"))
                upgrades = {r[0] for r in db.execute("SELECT path FROM files WHERE parser='text-chunks'") if has_js and r[0].endswith((".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"))}
                changed = [p for p, digest in hashes.items() if previous.get(p) != digest or p in upgrades]
                for path in changed:
                    if path not in contents:
                        contents[path] = db.execute("SELECT content FROM files WHERE path=?", (path,)).fetchone()[0]
                removed = sorted(set(previous) - set(hashes))
                for path in changed + removed:
                    db.execute("DELETE FROM search WHERE id IN (SELECT id FROM nodes WHERE path=?)", (path,))
                    db.execute("DELETE FROM nodes WHERE path=?", (path,))
                    db.execute("DELETE FROM refs WHERE path=?", (path,))
                    db.execute("DELETE FROM files WHERE path=?", (path,))
                for path in changed:
                    result = extract(path, contents[path])
                    size, mtime_ns, seen_ns = file_stats.get(path, (None, None, None))
                    db.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (path, hashes[path], result.parser, contents[path], json.dumps(result.warnings), size, mtime_ns, seen_ns))
                    for node in result.nodes:
                        db.execute("INSERT INTO nodes VALUES (?, ?, ?, ?, ?, ?, ?)", (node.id, node.path, node.name, node.kind, node.start, node.end, node.snippet))
                        db.execute("INSERT INTO search VALUES (?, ?, ?, ?)", (node.id, node.name, node.path, node.snippet))
                    db.executemany("INSERT INTO refs VALUES (?, ?)", [(path, json.dumps(ref)) for ref in result.refs])
                if changed or removed:
                    nodes = [dict(r) for r in db.execute("SELECT * FROM nodes")]
                    refs = [json.loads(r[0]) for r in db.execute("SELECT payload FROM refs")]
                    edges = resolve(nodes, refs)
                    # Reconstruct containment using source ranges so unchanged files retain edges.
                    by_file: dict[str, list[dict]] = {}
                    for node in nodes:
                        by_file.setdefault(node["path"], []).append(node)
                    for members in by_file.values():
                        file_node = next(n for n in members if n["kind"] == "file")
                        for node in members:
                            if node["kind"] == "file":
                                continue
                            parents = [n for n in members if n["id"] != node["id"] and n["kind"] not in {"chunk", "file"} and n["start"] <= node["start"] and n["end"] >= node["end"] and (n["start"], n["end"]) != (node["start"], node["end"])]
                            parent = min(parents, key=lambda n: n["end"] - n["start"]) if parents else file_node
                            edges.append({"source": parent["id"], "target": node["id"], "relation": "contains" if node["kind"] == "chunk" else "defines", "evidence": "EXTRACTED"})
                    db.execute("DELETE FROM edges")
                    db.executemany("INSERT OR IGNORE INTO edges VALUES (?, ?, ?, ?, ?)", [(e["source"], e["target"], e["relation"], e["evidence"], e.get("line")) for e in edges])
                # Re-read but identical files (touched, checked out) only refresh their stat record.
                db.executemany("UPDATE files SET size=?, mtime_ns=?, seen_ns=? WHERE path=?", [(*file_stats[p], p) for p in contents if p not in changed and p in file_stats])
                if changed or removed or not db.execute("SELECT 1 FROM metadata WHERE key='generation'").fetchone():
                    db.execute("INSERT OR REPLACE INTO metadata VALUES ('generation', ?)", (str(time.time_ns()),))
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('last_index', ?)", (str(time.time()),))
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('skipped', ?)", (json.dumps(skipped),))
            db.execute("BEGIN")
            stats = self._stats(db)
        return {**stats, "changed": len(changed), "removed": len(removed), "unchanged": len(hashes) - len(changed), "elapsed_ms": round((time.monotonic() - started) * 1000)}

    def _stats(self, db) -> dict:
        return {
            "files": db.execute("SELECT count(*) FROM files").fetchone()[0],
            "nodes": db.execute("SELECT count(*) FROM nodes").fetchone()[0],
            "edges": db.execute("SELECT count(*) FROM edges WHERE evidence NOT IN ('UNRESOLVED', 'CANDIDATE')").fetchone()[0],
            "source_bytes": sum(len(r[0].encode("utf-8")) for r in db.execute("SELECT content FROM files")),
            "parsers": {r[0]: r[1] for r in db.execute("SELECT parser, count(*) FROM files GROUP BY parser")},
            "warnings": [{"path": r[0], "messages": json.loads(r[1])} for r in db.execute("SELECT path, warnings FROM files WHERE warnings != '[]'")],
            "skipped": json.loads((db.execute("SELECT value FROM metadata WHERE key='skipped'").fetchone() or ['{}'])[0]),
        }

    def stats(self, refresh: bool = True) -> dict:
        if refresh:
            return self.index()
        with self.connect(snapshot=True) as db:
            return self._stats(db)

    def graph(self, refresh: bool = True) -> dict:
        if refresh:
            self.index()
        with self.connect(snapshot=True) as db:
            nodes = [dict(r) for r in db.execute("SELECT id,path,name,kind,start,end FROM nodes ORDER BY path,start,id")]
            edges = [dict(r) for r in db.execute("SELECT * FROM edges WHERE evidence NOT IN ('UNRESOLVED', 'CANDIDATE') ORDER BY source,target,relation")]
            # Weak components are groups, not claims of semantic community detection.
            adjacency = {n["id"]: [] for n in nodes}
            for e in edges:
                adjacency[e["source"]].append(e["target"])
                adjacency[e["target"]].append(e["source"])
            groups: dict[str, int] = {}
            group_count = 0
            for node in nodes:
                if node["id"] in groups:
                    continue
                group = group_count
                group_count += 1
                queue = [node["id"]]
                groups[node["id"]] = group
                for current in queue:
                    for neighbor in adjacency[current]:
                        if neighbor not in groups:
                            groups[neighbor] = group
                            queue.append(neighbor)
            for node in nodes:
                node["group"] = groups[node["id"]]
            return {"schema_version": SCHEMA, "project": self.root.name, "nodes": nodes, "edges": edges, "stats": self._stats(db)}
