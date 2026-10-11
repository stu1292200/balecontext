"""Offline relevance benchmark: no model calls, only BaleContext retrieval.

Usage:
    python benchmarks/relevance.py PATH/TO/CHECKOUT benchmarks/relevance/django.json [--budget 2000] [--json out.json]

For each question it records where the expected entry point ranks, how much of
the gold evidence appears as source in the first response, how much is at least
listed by name, the response size, and whether the response claims to be
complete. This checks retrieval quality before any paid agent comparison.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from balecontext.retrieve import context  # noqa: E402
from balecontext.store import Project  # noqa: E402


def _key(item: dict) -> tuple[str, str]:
    return item["name"], item["path"]


def evaluate(project: Project, question: dict, budget: int) -> dict:
    started = time.monotonic()
    response = context(project, question["query"], budget)
    elapsed = time.monotonic() - started
    shown = [_key(source) for source in response["sources"]]
    listed = {_key(item) for item in response.get("map", [])} | set(shown)
    # Aliases (`res.set = res.header = function ...`) share one range: the same code was shown.
    with project.connect() as db:
        ranges = {(r["name"], r["path"]): (r["start"], r["end"]) for r in db.execute("SELECT name,path,start,end FROM nodes")}

    def same(a: tuple[str, str], b: tuple[str, str]) -> bool:
        return a == b or (a[1] == b[1] and a in ranges and ranges.get(a) == ranges.get(b))

    entry = _key(question["entry"])
    gold = [_key(item) for item in question["gold"]]
    entry_rank = next((index + 1 for index, key in enumerate(shown) if same(key, entry)), None)
    return {
        "id": question["id"],
        "entry_rank": entry_rank,
        "gold_shown": sum(any(same(item, key) for key in shown) for item in gold) / len(gold),
        "gold_listed": sum(any(same(item, key) for key in listed) for item in gold) / len(gold),
        "missing": [f"{path}:{name}" for name, path in gold if not any(same((name, path), key) for key in listed)],
        "estimated_tokens": response["estimated_tokens"],
        "verdict": response.get("verdict", "n/a"),
        "seconds": round(elapsed, 2),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout")
    parser.add_argument("questions")
    parser.add_argument("--budget", type=int, default=3000)
    parser.add_argument("--json", help="Write the full results to this file")
    args = parser.parse_args(argv)
    spec = json.loads(Path(args.questions).read_text(encoding="utf-8"))
    project = Project(args.checkout)
    project.index()
    rows = [evaluate(project, question, args.budget) for question in spec["questions"]]
    print(f"{'question':28} {'entry':>5} {'shown':>6} {'listed':>6} {'tokens':>6} {'verdict':>8}")
    for row in rows:
        rank = row["entry_rank"] or "-"
        print(f"{row['id'][:28]:28} {rank!s:>5} {row['gold_shown']:>6.0%} {row['gold_listed']:>6.0%} {row['estimated_tokens']:>6} {row['verdict']:>8}")
    summary = {
        "questions": len(rows),
        "entry_first": sum(row["entry_rank"] == 1 for row in rows) / len(rows),
        "gold_shown_mean": statistics.mean(row["gold_shown"] for row in rows),
        "gold_listed_mean": statistics.mean(row["gold_listed"] for row in rows),
        "median_tokens": statistics.median(row["estimated_tokens"] for row in rows),
        "max_tokens": max(row["estimated_tokens"] for row in rows),
    }
    print(json.dumps(summary, indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
