"""Say how each recorded request differs from the one before it, as the prompt cache sees it.

Reads the folder `wire_log.py` wrote and prints, per request, the usage and the shape of its
messages (role, content blocks, sizes), then the first point where the request stops matching
the previous one. No source text is printed, only what differs and where, so the report can be
shared. Usage: py benchmarks\\wire_report.py results\\wire [--out report.md]
"""
from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path


def blocks(message: dict) -> list[tuple[str, str, int]]:
    """(role, block type, characters) for each content block of a message."""
    content = message.get("content")
    if isinstance(content, str):
        return [(message["role"], "string", len(content))]
    out = []
    for block in content or []:
        kind = block.get("type", "?")
        size = len(block.get("text", "")) if kind == "text" else len(json.dumps(block.get(kind), ensure_ascii=False))
        out.append((message["role"], kind + ("+cache" if "cache_control" in block else ""), size))
    return out


def head(message: dict) -> str:
    content = message.get("content")
    text = content if isinstance(content, str) else " | ".join((b.get("text") or json.dumps(b.get(b.get("type"), ""), ensure_ascii=False))[:60] for b in content or [])
    return text[:120].replace("\n", " ")


def flat(message: dict) -> str:
    """The message's text as the model reads it, whatever shape it was sent in."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") if b.get("type") == "text" else json.dumps(normal(b), sort_keys=True) for b in content or [])


def normal(value):
    """The message without cache markers, since the cache ignores where its breakpoints sit."""
    if isinstance(value, dict):
        return {k: normal(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [normal(v) for v in value]
    return value


def compare(previous: dict, current: dict) -> list[str]:
    lines = []
    for key in ("model", "system", "tools", "thinking", "output_config", "context_management", "metadata"):
        if normal(previous.get(key)) != normal(current.get(key)):
            lines.append(f"- `{key}` differs from the previous request.")
    before, after = previous.get("messages", []), current.get("messages", [])
    for index in range(min(len(before), len(after))):
        a, b = normal(before[index]), normal(after[index])
        if a == b:
            continue
        lines.append(f"- message {index} ({a.get('role')}) differs; the cache can match nothing after it.")
        if json.dumps(a.get("content"), sort_keys=True) == json.dumps(b.get("content"), sort_keys=True):
            lines.append("  Same content, different keys: " + ", ".join(sorted(set(a) ^ set(b))))
        elif flat(a) == flat(b):
            lines.append(f"  Same text, sent in another shape: before {blocks(before[index])}, now {blocks(after[index])}.")
        else:
            ta, tb = json.dumps(a, indent=0, ensure_ascii=False).splitlines(), json.dumps(b, indent=0, ensure_ascii=False).splitlines()
            shape_a, shape_b = blocks(before[index]), blocks(after[index])
            lines.append(f"  shape before: {shape_a}")
            lines.append(f"  shape now:    {shape_b}")
            changed = [line for line in difflib.unified_diff(ta, tb, lineterm="", n=0) if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
            lines.append(f"  {len(changed)} changed lines; the first: " + " / ".join(c[:100].replace("\n", " ") for c in changed[:4]))
        break
    else:
        if len(after) > len(before):
            lines.append(f"- the first {len(before)} messages match the previous request; {len(after) - len(before)} new.")
        else:
            lines.append(f"- all {len(after)} messages match the previous request.")
    return lines


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        print(__doc__)
        return 1
    folder = Path(argv[1])
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else None
    files = sorted(p for p in folder.glob("*.json") if "messages" in p.name and "count_tokens" not in p.name)
    report = [f"# Wire report: {folder}", ""]
    previous = None
    for path in files:
        record = json.loads(path.read_text(encoding="utf-8"))
        request, usage = record["request"], record.get("usage") or {}
        report.append(f"## {path.name}")
        report.append(f"usage: write {usage.get('cache_creation_input_tokens', 0):,}, read {usage.get('cache_read_input_tokens', 0):,}, "
                      f"input {usage.get('input_tokens', 0):,}, output {usage.get('output_tokens', 0):,}")
        system = request.get("system")
        report.append(f"system: {len(json.dumps(system, ensure_ascii=False)) if system else 0:,} chars; tools: {len(request.get('tools') or [])}; messages: {len(request.get('messages', []))}")
        for index, message in enumerate(request.get("messages", [])):
            report.append(f"- {index} {blocks(message)}  {head(message)!r}")
        if previous is not None:
            report += compare(previous, request)
        report.append("")
        previous = request
    text = "\n".join(report)
    if out:
        out.write_text(text, encoding="utf-8")
        print(f"Wrote {out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
