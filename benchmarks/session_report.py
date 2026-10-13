"""Show what Claude Code saved for each paired-test conversation, to explain cache misses.

    python benchmarks/session_report.py benchmarks/claude-pair-results/20261012T121326Z --run 2

Reads only Claude Code's own session files (in ~/.claude/projects, or CLAUDE_CONFIG_DIR) and
makes no Claude calls. It prints the kind and size of each saved record, the prompt-rendering
settings Claude Code saved, and each model call's cache numbers, but no prompt, code or answer
text, so the file it writes is safe to share. A resumed question re-sends the saved
conversation; if Claude Code renders it differently from the first time, the cache misses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

QUIET = {"queue-operation", "atis-latch", "cost-state", "last-prompt", "mode", "summary"}


def _events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _short_hash(value) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:10]


def _size(value) -> int:
    return len(value) if isinstance(value, str) else len(json.dumps(value, ensure_ascii=False))


def _time(record: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(record.get("timestamp", "")).replace("Z", "+00:00"))
    except ValueError:
        return None


def describe(transcript: Path) -> list[str]:
    lines, start, seen = [], None, {}
    for record in _events(transcript):
        kind = record.get("type")
        if kind in QUIET:
            continue
        moment = _time(record)
        start = start or moment
        clock = f"{max(0.0, (moment - start).total_seconds()):7.1f}s" if moment and start else "        "
        if kind == "attachment" and isinstance(record.get("attachment"), dict):
            attachment = record["attachment"]
            if attachment.get("type") == "prompt_snapshot":
                settings = {key: value for key, value in attachment.items() if key not in {"type", "systemPrompt", "tools", "cliPrefix"}}
                parts = [f"system {_short_hash(attachment.get('systemPrompt'))}"]
                if "tools" in attachment:
                    parts.append(f"tools {_short_hash(attachment['tools'])}")
                lines.append(f"{clock}  snapshot  {', '.join(parts)}  {json.dumps(settings, sort_keys=True)}")
            else:
                body = attachment.get("content", attachment.get("text", attachment))
                rendered = record.get("rendered")
                extra = f", rendered {_size(rendered):,} chars {_short_hash(rendered)}" if rendered is not None else ""
                lines.append(f"{clock}  attachment {attachment.get('type')} ({_size(body):,} chars{extra})")
        elif kind == "user":
            content = (record.get("message") or {}).get("content")
            if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                lines.append(f"{clock}  tool result ({_size(content):,} chars)")
            else:
                lines.append(f"{clock}  user message ({_size(content):,} chars{', meta' if record.get('isMeta') else ''})")
        elif kind == "assistant":
            message = record.get("message") or {}
            blocks = [b.get("type") for b in message.get("content") or [] if isinstance(b, dict)]
            identifier = message.get("id") or record.get("uuid")
            if identifier in seen:
                # Claude Code saves one record per content block; show the call once.
                seen[identifier][1].extend(blocks)
                lines[seen[identifier][0]] = _call_line(seen[identifier][2], message, seen[identifier][1])
                continue
            seen[identifier] = (len(lines), list(blocks), clock)
            lines.append(_call_line(clock, message, blocks))
        else:
            lines.append(f"{clock}  {kind}")
    return lines


def _call_line(clock: str, message: dict, blocks: list[str]) -> str:
    usage = message.get("usage") or {}
    counts = ", ".join(f"{blocks.count(b)} {b}" for b in dict.fromkeys(blocks))
    return (f"{clock}  model call: cache_write {int(usage.get('cache_creation_input_tokens') or 0):,}, "
            f"cache_read {int(usage.get('cache_read_input_tokens') or 0):,}, input {int(usage.get('input_tokens') or 0):,}  [{counts}]")


def sessions(folder: Path, run: int | None) -> list[tuple[str, str, str]]:
    """(conversation, session id, Claude Code version) for each paired conversation."""
    pattern = re.compile(r"run(\d+)-([a-z]+)(?:\.followup(\d*))?\.jsonl$")
    found: dict[tuple[str, str], tuple[str, str, str]] = {}
    for path in sorted(folder.glob("run*.jsonl")):
        match = pattern.match(path.name)
        if not match or run is not None and int(match.group(1)) != run:
            continue
        events = _events(path)
        init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
        result = next((e for e in reversed(events) if e.get("type") == "result"), {})
        session = result.get("session_id") or init.get("session_id")
        if session:
            # A resumed question normally keeps the session id, so each conversation is one file.
            found.setdefault((f"run{match.group(1)}-{match.group(2)}", session), (f"run{match.group(1)}-{match.group(2)}", session, str(init.get("claude_code_version", "unknown"))))
    return list(found.values())


def transcript_path(session: str) -> Path | None:
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    matches = sorted(root.glob(f"projects/*/{session}.jsonl"))
    return matches[0] if matches else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("results", type=Path)
    parser.add_argument("--run", type=int, help="only this run number")
    args = parser.parse_args()
    out = [f"# Saved sessions for {args.results.name}", ""]
    for name, session, version in sessions(args.results, args.run):
        out += [f"## {name} (Claude Code {version})", ""]
        path = transcript_path(session)
        if not path:
            out += [f"Session file for {session} not found under ~/.claude/projects.", ""]
            continue
        out += ["```", *describe(path), "```", ""]
    target = args.results / "sessions.md"
    target.write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
