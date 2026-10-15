"""BaleContext command-line interface."""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path

from . import __version__
from .export import export
from .integrations import configuration, install
from .mcp import serve
from .retrieve import context, find_path, read_source, related, save_memory
from .store import Project


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="balecontext", description="Local project knowledge for AI coding agents. No API keys. No cloud.")
    result.add_argument("--version", action="version", version=f"BaleContext {__version__}")
    commands = result.add_subparsers(dest="command", required=True)
    for command, help_text in {
        "index": "Build or incrementally refresh the project map",
        "status": "Show current index and parser coverage",
        "query": "Retrieve source evidence for a task",
        "related": "Trace local callers and dependencies",
        "path": "Find a shortest relationship path between symbols",
        "read": "Read exact lines from an indexed file",
        "export": "Export an offline graph viewer and JSON",
        "mcp": "Run the agent MCP server over stdio",
        "config": "Print a client connection configuration",
        "install": "Connect a client at project scope, preserving existing settings",
        "remember": "Save a note tied to current source hashes",
        "watch": "Refresh the index when source files change",
        "doctor": "Check parser and SQLite capabilities",
        "hook": "Prompt-time hook for a client (reads the client's hook JSON on stdin)",
    }.items():
        sub = commands.add_parser(command, help=help_text)
        sub.add_argument("--root", default=".", help="Project directory (default: current directory)")
        if command != "mcp":
            sub.add_argument("--json", action="store_true", help="Machine-readable JSON output")
        if command == "mcp":
            sub.add_argument("--no-watch", action="store_true", help="Disable background refresh; tool calls still refresh before reading")
            sub.add_argument("--watch-interval", type=float, default=2.0, help="Seconds between background scans (default: 2, minimum: 0.2)")
        elif command == "query":
            sub.add_argument("query")
            sub.add_argument("--budget", type=int, default=3000, help="Approximate text tokens (UTF-8 bytes / 4); not exact provider tokens")
        elif command == "related":
            sub.add_argument("name")
            sub.add_argument("--direction", choices=["both", "callers", "dependencies"], default="both")
            sub.add_argument("--depth", type=int, default=1)
        elif command == "read":
            sub.add_argument("path")
            sub.add_argument("--start", type=int, default=1)
            sub.add_argument("--end", type=int)
        elif command == "path":
            sub.add_argument("source")
            sub.add_argument("target")
            sub.add_argument("--undirected", action="store_true", help="Allow traversing a relationship in either direction")
        elif command in {"config", "install"}:
            sub.add_argument("agent", choices=["claude", "codex", "gemini", "cursor", "generic"] if command == "config" else ["claude", "codex", "gemini", "cursor"])
            if command == "install":
                sub.add_argument("--dry-run", action="store_true")
                sub.add_argument("--hook", action="store_true", help="Claude Code and Codex: also hand BaleContext evidence to the model on each prompt")
        elif command == "hook":
            sub.add_argument("client", choices=["claude", "codex"])
            sub.add_argument("--part", type=int, default=1, help="Claude Code: which piece of the evidence to print; Claude Code runs the parts side by side")
        elif command == "remember":
            sub.add_argument("text")
            sub.add_argument("--source", action="append", required=True, dest="sources")
        elif command == "export":
            sub.add_argument("--output", type=Path)
        elif command == "watch":
            sub.add_argument("--interval", type=float, default=2.0)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "hook":
        from .hook import run_claude
        # Never index a new folder or fail the prompt from a hook. Codex takes the whole pack from one hook.
        code = run_claude(part=1, whole=True) if args.client == "codex" else run_claude(part=args.part)
        # Claude Code waits for the hook to exit, and adds hook context in the order hooks exit.
        # Skipping the interpreter's teardown of what retrieval loaded saves a third of a second
        # and lets part 1 finish before the parts that follow it.
        sys.stdout.flush()
        os._exit(code)
    try:
        project = Project(args.root)
        if args.command == "mcp":
            serve(project, watch_interval=None if args.no_watch else args.watch_interval)
            return 0
        if args.command in {"index", "status"}:
            result = project.index()
        elif args.command == "query":
            result = context(project, args.query, args.budget)
            if not args.json:
                print(result["text"])
                print(f"\n~{result['estimated_tokens']} text tokens / {result['budget']} budget (estimate only).", file=sys.stderr)
                return 0
        elif args.command == "related":
            result = related(project, args.name, args.direction, args.depth)
        elif args.command == "read":
            result = read_source(project, args.path, args.start, args.end)
            if not args.json:
                print(result["text"])
                return 0
        elif args.command == "path":
            result = find_path(project, args.source, args.target, args.undirected)
        elif args.command == "export":
            result = export(project, args.output)
        elif args.command == "config":
            print(configuration(project, args.agent))
            return 0
        elif args.command == "install":
            result = install(project, args.agent, args.dry_run, args.hook)
        elif args.command == "remember":
            result = save_memory(project, args.text, args.sources)
        elif args.command == "watch":
            if not math.isfinite(args.interval) or args.interval < 0.2:
                raise ValueError("Watch interval must be finite and at least 0.2 seconds.")
            while True:
                result = project.index()
                if result["changed"] or result["removed"]:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                time.sleep(args.interval)
        elif args.command == "doctor":
            import importlib.util
            import sqlite3
            with project.connect() as db:
                db.execute("SELECT count(*) FROM search")
            result = {"version": __version__, "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version, "fts5": True, "python_ast": True, "javascript_typescript": all(importlib.util.find_spec(m) for m in ("tree_sitter", "tree_sitter_javascript", "tree_sitter_typescript")), "runtime_network": "none", "note": "If optional parsers are absent, install balecontext[code]. This is an alpha release; live client tests are separate."}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        return 130
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"BaleContext: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
