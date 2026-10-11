"""Compare actual all-call provider token totals; never derive them from excerpts."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def total(run: dict) -> int:
    calls = run.get("calls")
    if not isinstance(calls, list) or not calls:
        raise ValueError("Each agent run must list every model call, including retries and helpers.")
    for call in calls:
        value = call.get("total_tokens")
        if type(value) is not int or value < 0:
            raise ValueError("Each call needs a non-negative, provider-reported total_tokens.")
    if type(run.get("passed")) is not bool:
        raise ValueError("Each run needs a passed boolean from the same acceptance checks.")
    return sum(call["total_tokens"] for call in calls)


def compare(data: dict) -> dict:
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Provide a non-empty tasks list.")
    baseline_total = assisted_total = baseline_pass = assisted_pass = 0
    percentages = []
    names = set()
    setup = data.get("assisted_setup_model_tokens")
    if type(setup) is not int or setup < 0:
        raise ValueError("Explicitly provide assisted_setup_model_tokens, including preprocessing models if any (zero for local indexing).")
    for task in tasks:
        identifier = task.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in names:
            raise ValueError("Each task needs a unique non-empty ID.")
        names.add(identifier)
        baseline = total(task["baseline"])
        assisted = total(task["assisted"])
        if baseline == 0:
            raise ValueError("Baseline token total must be positive.")
        baseline_total += baseline
        assisted_total += assisted
        baseline_pass += task["baseline"]["passed"]
        assisted_pass += task["assisted"]["passed"]
        percentages.append((1 - assisted / baseline) * 100)
    assisted_total += setup
    return {"tasks": len(tasks), "baseline_total_model_tokens": baseline_total, "assisted_total_model_tokens_including_setup": assisted_total, "aggregate_reduction_percent": round((1 - assisted_total / baseline_total) * 100, 2), "median_task_reduction_percent_excluding_setup": round(statistics.median(percentages), 2), "baseline_passed": baseline_pass, "assisted_passed": assisted_pass, "quality_gate": "pass-rate-not-lower" if assisted_pass >= baseline_pass else "FAILED", "limitation": "Pass rate does not prove equivalent quality. Review regressions, test coverage, blinded qualitative scores and repeated runs. Provider totals must include reasoning where reported; do not count reasoning twice."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("measurements", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(compare(json.loads(args.measurements.read_text(encoding="utf-8"))), indent=2))
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
