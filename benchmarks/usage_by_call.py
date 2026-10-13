"""Per-model-call token usage from a paired test's raw results, with no source text.

Usage:
    python benchmarks/usage_by_call.py RESULTS_FOLDER [--run 1]

For each `runN-*.jsonl` file (first question and follow-up, each arm) it prints every
model call's usage, their sum, and what Claude Code's final result reports, so you can
see whether a resumed conversation reports its own calls or the whole conversation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
SHORT = ("input", "cache_wr", "cache_rd", "output")


def calls(path: Path) -> tuple[list[tuple[str, dict]], dict]:
    messages: dict[str, tuple[str, dict]] = {}
    result: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            message = event.get("message") or {}
            if message.get("id"):
                messages[message["id"]] = (str(message.get("model", "?")), message.get("usage") or {})
        elif event.get("type") == "result":
            result = event
    return list(messages.values()), result


def row(label: str, usage: dict) -> str:
    return f"  {label:22}" + "".join(f"{int(usage.get(f) or 0):>10,}" for f in FIELDS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--run", type=int, default=1)
    args = parser.parse_args()
    files = sorted(args.folder.glob(f"run{args.run}-*.jsonl"))
    if not files:
        raise SystemExit(f"No run{args.run}-*.jsonl files in {args.folder}")
    for path in files:
        found, result = calls(path)
        print(f"\n{path.name}: {len(found)} model calls")
        print(f"  {'':22}" + "".join(f"{s:>10}" for s in SHORT))
        for number, (model, usage) in enumerate(found, 1):
            print(row(f"call {number} {model[:12]}", usage))
        print(row("sum of calls", {f: sum(int(u.get(f) or 0) for _, u in found) for f in FIELDS}))
        print(row("result reports", result.get("usage") or {}))
        print(f"  result: num_turns={result.get('num_turns')} total_cost_usd={result.get('total_cost_usd')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
