"""Compare actual all-call provider token totals; never derive them from excerpts."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path


def total(run: dict) -> int:
    if not isinstance(run, dict):
        raise ValueError("Each agent run must be an object.")
    calls = run.get("calls")
    if not isinstance(calls, list) or not calls:
        raise ValueError("Each agent run must list every model call, including retries and helpers.")
    for call in calls:
        value = call.get("total_tokens") if isinstance(call, dict) else None
        if type(value) is not int or value < 0:
            raise ValueError("Each call needs a non-negative, provider-reported total_tokens.")
    if type(run.get("passed")) is not bool:
        raise ValueError("Each run needs a passed boolean from the same acceptance checks.")
    return sum(call["total_tokens"] for call in calls)


def seconds(value, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be a finite, non-negative number of elapsed seconds.")
    return float(value)


def compare_timing(data: dict, tasks: list[dict]) -> dict:
    recorded = "assisted_setup_elapsed_seconds" in data or any(
        "elapsed_seconds" in task[mode] for task in tasks for mode in ("baseline", "assisted")
    )
    if not recorded:
        return {"status": "not-recorded"}
    setup = seconds(data.get("assisted_setup_elapsed_seconds"), "assisted_setup_elapsed_seconds")
    baseline_total = assisted_total = 0.0
    reductions = []
    for task in tasks:
        baseline = seconds(task["baseline"].get("elapsed_seconds"), "baseline elapsed_seconds")
        assisted = seconds(task["assisted"].get("elapsed_seconds"), "assisted elapsed_seconds")
        if baseline == 0:
            raise ValueError("Baseline elapsed_seconds must be positive when timing is recorded.")
        baseline_total += baseline
        assisted_total += assisted
        reductions.append((1 - assisted / baseline) * 100)
    assisted_total += setup
    return {
        "status": "recorded",
        "baseline_elapsed_seconds": round(baseline_total, 3),
        "assisted_elapsed_seconds_including_setup": round(assisted_total, 3),
        "aggregate_reduction_percent": round((1 - assisted_total / baseline_total) * 100, 2),
        "median_task_reduction_percent_excluding_setup": round(statistics.median(reductions), 2),
    }


def compare(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Measurements must be an object.")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("Provide a non-empty tasks list.")
    baseline_total = assisted_total = baseline_pass = assisted_pass = 0
    percentages = []
    regressions = []
    improvements = []
    names = set()
    setup = data.get("assisted_setup_model_tokens")
    if type(setup) is not int or setup < 0:
        raise ValueError("Explicitly provide assisted_setup_model_tokens, including preprocessing models if any (zero for local indexing).")
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("Each task must be an object.")
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
        if task["baseline"]["passed"] and not task["assisted"]["passed"]:
            regressions.append(identifier)
        elif task["assisted"]["passed"] and not task["baseline"]["passed"]:
            improvements.append(identifier)
        percentages.append((1 - assisted / baseline) * 100)
    assisted_total += setup
    return {
        "tasks": len(tasks),
        "baseline_total_model_tokens": baseline_total,
        "assisted_total_model_tokens_including_setup": assisted_total,
        "aggregate_reduction_percent": round((1 - assisted_total / baseline_total) * 100, 2),
        "median_task_reduction_percent_excluding_setup": round(statistics.median(percentages), 2),
        "baseline_passed": baseline_pass,
        "assisted_passed": assisted_pass,
        "regressed_task_ids": regressions,
        "improved_task_ids": improvements,
        "quality_gate": "FAILED" if regressions else "no-task-regressions",
        "timing": compare_timing(data, tasks),
        "limitation": "Passing acceptance checks does not prove equivalent quality. Review test coverage, blinded qualitative scores and repeated runs. Provider totals must include reasoning where reported; do not count reasoning twice.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("measurements", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(compare(json.loads(args.measurements.read_text(encoding="utf-8"))), indent=2))
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
