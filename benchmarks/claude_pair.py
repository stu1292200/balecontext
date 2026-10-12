"""Paired Claude Code test: the same question with and without BaleContext.

Usage (from any folder, with Claude Code and BaleContext installed):
    python benchmarks/claude_pair.py PATH/TO/CHECKOUT --question-file question.txt [--runs 3] [--model sonnet]

Each run calls `claude -p` once per arm in the same checkout, rotating which arm
goes first. All arms get the same model, the same read-only tools and no other
MCP servers. Arms: "without" (plain Claude Code), "with" (BaleContext server and
its instructions) and "hook" (only the prompt hook, which hands the model
BaleContext's evidence before its first turn; system prompt and tools stay the
same as "without", so cached-prompt pricing is comparable). Default: without,hook.
Prompt caching makes a single run noisy: use --runs 3 or more. Each task's prompt
ends with a different number (BaleContext ignores it), so no task reuses another's
cached conversation: a repeated identical prompt would make the deterministic hook
arm look cheaper than a new question really is. --same-prompt turns that off.
Token counts come from Claude Code's own JSON result for the whole task, every
model call included. Answers are saved side by side so a person can judge
quality; this script never decides that an answer is correct.

This uses your Claude plan limits. Nothing in the checkout is modified.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from balecontext.integrations import INSTRUCTIONS, hook_config  # noqa: E402
from balecontext.store import Project  # noqa: E402

READ_TOOLS = ["Read", "Grep", "Glob", "LS"]
WRITE_TOOLS = ["Edit", "MultiEdit", "Write", "NotebookEdit", "Bash"]
USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def instructions() -> str:
    # One line: Windows passes `claude.cmd` arguments through cmd.exe, which breaks on newlines.
    return " ".join(line.strip() for line in re.sub(r"<!--.*?-->", "", INSTRUCTIONS).splitlines() if line.strip())


def refuse_contaminated(checkout: Path) -> None:
    """A BaleContext block already in the project would leak into the 'without' arm."""
    for name in ("CLAUDE.md", ".mcp.json", ".claude/settings.json", ".claude/settings.local.json"):
        path = checkout / name
        if path.is_file() and "balecontext" in path.read_text(encoding="utf-8", errors="replace").lower():
            raise SystemExit(f"{path} mentions BaleContext, so the 'without' run would not be clean. "
                             "Use a checkout without it (or remove it for the test) and run again.")


def command(arm: str, args, mcp_config: Path, hook_config: Path | None = None) -> list[str]:
    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("The `claude` command was not found. Install Claude Code and sign in first.")
    line = [claude, "-p", "--output-format", "stream-json", "--verbose", "--model", args.model, "--max-turns", str(args.max_turns),
            "--strict-mcp-config", "--disallowedTools", *WRITE_TOOLS]
    allowed = list(READ_TOOLS)
    if arm == "with":
        line += ["--mcp-config", str(mcp_config), "--append-system-prompt", instructions()]
        allowed.append("mcp__balecontext")
    if arm == "hook":
        # Hook only: the system prompt and tools stay identical to "without", so both arms
        # share Claude Code's cached prefix and only the evidence itself is new input.
        line += ["--settings", str(hook_config)]
    return line + ["--allowedTools", *allowed]


def invoke(arm: str, args, prompt: str, mcp_config: Path, out: Path, name: str, resume: str | None = None) -> tuple[dict, list[str], dict]:
    """One `claude -p` call; returns its result event, its tool calls and its own model-call usage."""
    line = command(arm, args, mcp_config, out / "balecontext-hook-settings.json")
    if resume:
        line[1:1] = ["--resume", resume]
    completed = subprocess.run(line, input=prompt, capture_output=True, text=True, encoding="utf-8", cwd=args.checkout, timeout=args.timeout)
    raw = completed.stdout.strip()
    (out / f"{name}.jsonl").write_text(raw + "\n", encoding="utf-8")
    events = []
    for text in raw.splitlines():
        try:
            events.append(json.loads(text))
        except json.JSONDecodeError:
            continue
    data = next((event for event in reversed(events) if event.get("type") == "result"), {})
    # Every tool call in order, so a run explains its own turns.
    calls = []
    messages: dict[str, dict] = {}
    for event in events:
        if event.get("type") == "assistant":
            message = event.get("message") or {}
            if message.get("id") and isinstance(message.get("usage"), dict):
                messages[message["id"]] = message["usage"]
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    calls.append(f"{block.get('name')} {json.dumps(block.get('input'), ensure_ascii=False)[:300]}")
        elif event.get("type") == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    content = block.get("content")
                    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
                    calls.append(f"  -> result {len(text):,} chars" + (" (COVERAGE: " + text.split("COVERAGE: ", 1)[1][:8].split(".")[0] + ")" if "COVERAGE: " in text else ""))
    if not data or completed.returncode != 0 and not data.get("usage"):
        (out / f"{name}.stderr.txt").write_text(completed.stderr, encoding="utf-8")
        raise SystemExit(f"{name} failed; see {out}. First error line: {(completed.stderr or raw).strip()[:300]}")
    own = {field: sum(int(usage.get(field) or 0) for usage in messages.values()) for field in USAGE_FIELDS}
    (out / f"{name}.answer.md").write_text(str(data.get("result", "")), encoding="utf-8")
    return data, calls, own


def run_arm(arm: str, args, question: str, mcp_config: Path, out: Path, number: int, followup: str | None = None) -> dict:
    started = time.monotonic()
    hook_log = args.checkout / ".balecontext" / "hook-log.jsonl"
    hook_lines = len(hook_log.read_text(encoding="utf-8").splitlines()) if hook_log.exists() else 0
    data, calls, _ = invoke(arm, args, question, mcp_config, out, f"run{number}-{arm}")
    usage = {field: int((data.get("usage") or {}).get(field) or 0) for field in USAGE_FIELDS}
    cost, turns = data.get("total_cost_usd"), data.get("num_turns")
    first = {"tokens": sum(usage.values()), "cost": cost}
    first_usage, first_turns = dict(usage), turns
    if followup:
        # A second question in the same conversation (`--resume`), as people use Claude Code.
        later, later_calls, own = invoke(arm, args, followup, mcp_config, out, f"run{number}-{arm}.followup", resume=data.get("session_id"))
        calls += ["--- follow-up question ---"] + later_calls
        reported = {field: int((later.get("usage") or {}).get(field) or 0) for field in USAGE_FIELDS}
        # Claude Code documents a resumed run's totals as the whole conversation's; check against
        # the follow-up's own model calls rather than assume it.
        cumulative = abs(sum(reported.values()) - sum(usage.values()) - sum(own.values())) < abs(sum(reported.values()) - sum(own.values()))
        usage = reported if cumulative else {field: usage[field] + reported[field] for field in USAGE_FIELDS}
        later_cost = later.get("total_cost_usd")
        if isinstance(cost, (int, float)) and isinstance(later_cost, (int, float)):
            cost = later_cost if cumulative else cost + later_cost
        turns = later.get("num_turns") if cumulative else (turns or 0) + (later.get("num_turns") or 0)
    seconds = round(time.monotonic() - started, 1)
    hook_ran = None
    if arm == "hook":
        new_lines = hook_log.read_text(encoding="utf-8").splitlines()[hook_lines:] if hook_log.exists() else []
        hook_ran = bool(new_lines)
        if not hook_ran:
            print("  WARNING: the prompt hook did not run in this Claude Code version; this arm equals 'without'.")
        elif not any(json.loads(line).get("context_chars") for line in new_lines):
            print("  Note: the hook ran but found nothing to add for this question.")
    (out / f"run{number}-{arm}.tools.txt").write_text("\n".join(calls) + "\n", encoding="utf-8")
    row = dict(usage)
    row.update({
        "arm": arm, "run": number,
        "total_tokens": sum(usage[field] for field in USAGE_FIELDS),
        "cost_usd_estimate": cost,
        "turns": turns,
        "seconds": seconds,
        "hook_ran": hook_ran,
        "tool_calls": sum(not c.startswith(("  ->", "---")) for c in calls),
        "error": data.get("is_error", False) or data.get("subtype") not in (None, "success"),
    })
    if followup:
        # Raw figures, so the totals can be recomputed if Claude Code reports resumed runs differently.
        row["first_question"] = first | {"usage": first_usage, "turns": first_turns}
        row["followup_reported"] = {"usage": reported, "cost_usd_estimate": later_cost, "turns": later.get("num_turns"), "own_calls": own, "cumulative": cumulative}
        row["followup_tokens"] = row["total_tokens"] - first["tokens"]
        row["followup_cost_usd_estimate"] = cost - first["cost"] if isinstance(cost, (int, float)) and isinstance(first["cost"], (int, float)) else None
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--question-file", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3, help="Pairs to run (default 3)")
    parser.add_argument("--model", default="sonnet", help="Same model for both arms (default sonnet)")
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--timeout", type=int, default=1200, help="Seconds per run")
    parser.add_argument("--arms", default="without,hook", help="Comma-separated arms: without, with, hook (default without,hook)")
    parser.add_argument("--same-prompt", action="store_true", help="Send the identical prompt every time (lets later runs reuse earlier runs' cache)")
    parser.add_argument("--followup-file", type=Path, help="A second question asked in the same conversation; totals then cover both questions")
    parser.add_argument("--out", type=Path, help="Results folder (default: claude-pair-results/<time> next to this script)")
    args = parser.parse_args(argv)
    args.checkout = args.checkout.resolve()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    if len(arms) < 2 or len(set(arms)) != len(arms) or set(arms) - {"without", "with", "hook"} or "without" not in arms:
        raise SystemExit("--arms needs 'without' and at least one of 'with' or 'hook', each once.")
    question = args.question_file.read_text(encoding="utf-8").strip()
    if not question:
        raise SystemExit("The question file is empty.")
    followup = args.followup_file.read_text(encoding="utf-8").strip() if args.followup_file else None
    if args.followup_file and not followup:
        raise SystemExit("The follow-up question file is empty.")
    refuse_contaminated(args.checkout)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (args.out or Path(__file__).resolve().parent / "claude-pair-results" / stamp).resolve()
    out.mkdir(parents=True, exist_ok=True)
    print("Indexing the checkout once before the runs (no model calls)...")
    Project(args.checkout).index()
    mcp_config = out / "balecontext-mcp.json"
    mcp_config.write_text(json.dumps({"mcpServers": {"balecontext": {
        "command": sys.executable, "args": ["-m", "balecontext", "mcp", "--root", str(args.checkout)]}}}, indent=2), encoding="utf-8")
    (out / "balecontext-hook-settings.json").write_text(json.dumps(hook_config(), indent=2), encoding="utf-8")
    (out / "question.txt").write_text(question + "\n" + (f"\nFollow-up:\n{followup}\n" if followup else ""), encoding="utf-8")
    rows = []
    for number in range(1, args.runs + 1):
        shift = (number - 1) % len(arms)
        for arm in arms[shift:] + arms[:shift]:
            print(f"Run {number}: {arm}...")
            # A number BaleContext's query parser ignores; it only stops cross-task cache reuse.
            prompt = question if args.same_prompt else f"{question}\n\n#{random.randrange(10**5, 10**6)}"
            later = followup if args.same_prompt or not followup else f"{followup}\n\n#{random.randrange(10**5, 10**6)}"
            rows.append(run_arm(arm, args, prompt, mcp_config, out, number, later))
            row = rows[-1]
            print(f"  {row['total_tokens']:,} tokens, {row['turns']} turns, {row['seconds']}s"
                  + (f" (follow-up alone: {row['followup_tokens']:,} tokens)" if followup else ""))
    summary = {}
    for arm in arms:
        mine = [row for row in rows if row["arm"] == arm]
        costs = [row["cost_usd_estimate"] for row in mine if isinstance(row["cost_usd_estimate"], (int, float))]
        summary[arm] = {
            "median_total_tokens": statistics.median(row["total_tokens"] for row in mine),
            "median_cost_usd_estimate": statistics.median(costs) if costs else None,
            "median_turns": statistics.median(row["turns"] for row in mine) if all(isinstance(row["turns"], int) for row in mine) else None,
            "errors": sum(bool(row["error"]) for row in mine),
        }
        if followup:
            later = [row["followup_cost_usd_estimate"] for row in mine if isinstance(row.get("followup_cost_usd_estimate"), (int, float))]
            summary[arm]["median_followup_tokens"] = statistics.median(row["followup_tokens"] for row in mine)
            summary[arm]["median_followup_cost_usd_estimate"] = statistics.median(later) if later else None
    base = summary["without"]
    for arm in arms:
        if arm == "without":
            continue
        assisted = summary[arm]
        assisted["token_saving_percent"] = round(100 * (1 - assisted["median_total_tokens"] / base["median_total_tokens"]), 1) if base["median_total_tokens"] else None
        if base["median_cost_usd_estimate"] and assisted["median_cost_usd_estimate"] is not None:
            assisted["cost_saving_percent"] = round(100 * (1 - assisted["median_cost_usd_estimate"] / base["median_cost_usd_estimate"]), 1)
        if followup and base.get("median_followup_tokens"):
            assisted["followup_token_saving_percent"] = round(100 * (1 - assisted["median_followup_tokens"] / base["median_followup_tokens"]), 1)
            if base.get("median_followup_cost_usd_estimate") and assisted.get("median_followup_cost_usd_estimate") is not None:
                assisted["followup_cost_saving_percent"] = round(100 * (1 - assisted["median_followup_cost_usd_estimate"] / base["median_followup_cost_usd_estimate"]), 1)
    summary["note"] = ("Whole-task totals from Claude Code's JSON result" + (", both questions together; 'followup' figures are the second question alone" if followup else "") + ". Cost is Claude Code's own estimate, the closest "
                       "proxy for plan limits. Judge answer quality by reading the saved answers; savings mean nothing "
                       "if the BaleContext answers are worse.")
    (out / "summary.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(f"\n{'run':>3} {'arm':8} {'total':>10} {'input':>8} {'cache_wr':>9} {'cache_rd':>10} {'output':>7} {'turns':>5} {'tools':>5} {'cost$':>7}")
    for row in rows:
        cost = f"{row['cost_usd_estimate']:.3f}" if isinstance(row["cost_usd_estimate"], (int, float)) else "-"
        print(f"{row['run']:>3} {row['arm']:8} {row['total_tokens']:>10,} {row['input_tokens']:>8,} {row['cache_creation_input_tokens']:>9,} "
              f"{row['cache_read_input_tokens']:>10,} {row['output_tokens']:>7,} {row['turns'] if row['turns'] is not None else '-':>5} {row['tool_calls']:>5} {cost:>7}")
    print(json.dumps(summary, indent=2))
    print(f"\nAnswers and raw results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
