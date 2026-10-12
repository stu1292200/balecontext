"""Bundle one paired test's results into a single Markdown file to share, with no model calls.

Usage:
    python benchmarks/share_results.py RESULTS_FOLDER [--run 2]

Writes RESULTS_FOLDER/share.md: the questions, the summary, each question's tokens and cost
for every run, every model call's usage for one run, and that run's tool calls and answers
for both arms. Attach that one file instead of pasting several.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from usage_by_call import FIELDS, SHORT, calls  # noqa: E402


def _order(path: Path) -> tuple[str, int]:
    """run2-hook.jsonl, run2-hook.followup.jsonl, run2-hook.followup2.jsonl -> (arm, question number)."""
    match = re.match(r"run\d+-([a-z]+)(?:\.followup(\d*))?\.", path.name)
    if not match:
        return path.name, 0
    return match.group(1), (int(match.group(2) or 1) if ".followup" in path.name else 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--run", type=int, default=2, help="Run whose model calls, tool calls and answers to include (default 2)")
    args = parser.parse_args()
    print(f"Wrote {write_share(args.folder, args.run)}")
    return 0


def write_share(folder: Path, run: int = 2) -> Path:
    """Write folder/share.md and return its path."""
    summary_file = folder / "summary.json"
    if not summary_file.exists():
        raise SystemExit(f"No summary.json in {folder}; is this a finished claude_pair.py results folder?")
    data = json.loads(summary_file.read_text(encoding="utf-8"))
    out = [f"# Paired results {folder.name}", ""]
    question = folder / "question.txt"
    if question.exists():
        out += ["## Questions", "", "```", question.read_text(encoding="utf-8").strip(), "```", ""]
    out += ["## Summary", "", "```json", json.dumps(data["summary"], indent=2), "```", ""]
    rows = data.get("rows", [])
    if any("tokens_by_question" in row for row in rows):
        out += ["## Each question's own tokens and cost", "", "| run | arm | question | tokens | cost $ |", "|---|---|---|---|---|"]
        for row in rows:
            for number, (tokens, cost) in enumerate(zip(row.get("tokens_by_question", []), row.get("cost_by_question", [])), 1):
                out.append(f"| {row['run']} | {row['arm']} | {number} | {tokens:,} | {cost if cost is not None else '-'} |")
        out.append("")
    files = sorted(folder.glob(f"run{run}-*.jsonl"), key=_order)
    out += [f"## Model calls, run {run}", "", "```", f"{'':29}" + "".join(f"{s:>10}" for s in SHORT)]
    for path in files:
        found, result = calls(path)
        out.append(f"{path.name}: {len(found)} model calls, num_turns={result.get('num_turns')}, total_cost_usd={result.get('total_cost_usd')}")
        for number, (model, usage) in enumerate(found, 1):
            out.append(f"  call {number:<3} {model[:18]:18}" + "".join(f"{int(usage.get(f) or 0):>10,}" for f in FIELDS))
        out.append(f"  {'result reports':27}" + "".join(f"{int((result.get('usage') or {}).get(f) or 0):>10,}" for f in FIELDS))
    out += ["```", ""]
    for tools in sorted(folder.glob(f"run{run}-*.tools.txt")):
        out += [f"## Tool calls: {tools.name}", "", "```", tools.read_text(encoding="utf-8").strip(), "```", ""]
    for answer in sorted(folder.glob(f"run{run}-*.answer.md"), key=_order):
        out += [f"## Answer: {answer.name}", "", answer.read_text(encoding="utf-8").strip(), ""]
    target = folder / "share.md"
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    raise SystemExit(main())
