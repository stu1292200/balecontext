"""Paired Codex test: the same question with and without BaleContext.

Usage (from any folder, with Codex CLI and BaleContext installed):
    python benchmarks/codex_pair.py PATH/TO/CHECKOUT --question-file question.txt [--runs 2] [--model MODEL]

Each run calls `codex exec` once per arm in the same checkout, rotating which arm goes
first; follow-up questions go to the same Codex conversation with `codex exec resume`,
each once the previous answer is in. Both arms get the same model, the same settings and
the same read-only sandbox. Arms: "without" (plain Codex), "hook" (BaleContext's prompt
hook, which hands Codex the evidence before its first model call, as `balecontext install
codex --hook` does; Codex's own instructions and tools stay the same as "without") and
"prompt" (the same evidence written into the question by this script, for a Codex
version whose hooks do not run). Default: without,hook.

Token counts are Codex's own, for the whole conversation and every model call: the
`turn.completed` usage Codex prints, checked against the per-call counts in Codex's saved
session file. Answers are saved side by side so a person can judge quality; this script
never decides that an answer is correct. Each prompt ends with a different number
(BaleContext ignores it), so no task reuses another's cached conversation.

This uses your Codex plan limits. Nothing in the checkout is modified unless --allow-edits
is given: then Codex may edit files (workspace-write sandbox), the checkout must be a clean
git checkout, each run's changes are saved as runN-<arm>.diff and undone before the next
run. --setup-patch FILE plants a change (a bug to find) before every run; it is undone at
the end. Nothing is written to your Codex settings: the hook is passed to each hook-arm
run on the command line.
"""
from __future__ import annotations

import argparse
import json
import os
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from balecontext.hook import prompt_pack  # noqa: E402
from balecontext.integrations import CODEX_EVENTS, codex_hook_settings  # noqa: E402
from balecontext.store import Project  # noqa: E402
from claude_pair import collect_changes, git, prepare_checkout, refuse_dirty, reset_checkout  # noqa: E402

ARMS = ("without", "hook", "prompt")
USAGE = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
# Codex prints this for every run given --dangerously-bypass-hook-trust; it is not a failure.
TRUST_WARNING = "--dangerously-bypass-hook-trust"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def find_codex() -> str:
    codex = shutil.which("codex")
    if not codex:
        raise SystemExit("The `codex` command was not found. Install Codex CLI (npm install -g @openai/codex) and sign in first.")
    shown = subprocess.run([codex, "exec", "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    if "--dangerously-bypass-hook-trust" not in shown or "--json" not in shown:
        raise SystemExit("This Codex version has no hooks for `codex exec`. Update it (npm install -g @openai/codex@latest) and run again.")
    return codex


def refuse_contaminated(checkout: Path, clean_config: bool = False) -> None:
    """BaleContext set up in the project or in Codex's own settings would leak into the 'without' arm."""
    places = []
    folder = checkout
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=checkout, capture_output=True, text=True).stdout.strip()
    # Codex reads AGENTS.md from the repository root down to the working folder.
    while True:
        places += [folder / name for name in ("AGENTS.md", "AGENTS.override.md", ".codex/config.toml", ".codex/hooks.json")]
        if not top or folder == Path(top).resolve() or folder.parent == folder:
            break
        folder = folder.parent
    places += [codex_home() / name for name in ("hooks.json", "AGENTS.md", "AGENTS.override.md") + (() if clean_config else ("config.toml",))]
    for path in places:
        if path.is_file() and "balecontext" in path.read_text(encoding="utf-8", errors="replace").lower():
            hint = (" Or add --clean-config, so both arms run without your config.toml (your sign-in still works)."
                    if path.name == "config.toml" and path.parent == codex_home() else "")
            raise SystemExit(f"{path} mentions BaleContext, so the 'without' run would not be clean. "
                             f"Remove BaleContext from it for the test (keep a copy) and run again.{hint}")


def toml_inline(value) -> str:
    if isinstance(value, dict):
        return "{" + ",".join(f"{key}={toml_inline(item)}" for key, item in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ",".join(toml_inline(item) for item in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    # A JSON string is a valid TOML basic string.
    return json.dumps(str(value))


def hook_overrides() -> list[str]:
    """The prompt hook as `-c` settings for one run, so nothing is written to Codex's settings files."""
    words = []
    for event in CODEX_EVENTS:
        words += ["-c", f"hooks.{event}=" + toml_inline([{"hooks": [codex_hook_settings(event)]}])]
    return words


def command(codex: str, arm: str, args, resume: str | None = None) -> list[str]:
    line = [codex, "exec"] + (["resume"] if resume else []) + ["--json", "--skip-git-repo-check",
            # Both arms, so any hook of your own runs (or not) the same way in each.
            "--dangerously-bypass-hook-trust",
            "-c", f'sandbox_mode="{"workspace-write" if args.allow_edits else "read-only"}"', "-c", 'approval_policy="never"']
    if args.clean_config:
        line.append("--ignore-user-config")
    if args.model:
        line += ["-m", args.model]
    if args.effort:
        line += ["-c", f'model_reasoning_effort="{args.effort}"']
    for setting in args.config or []:
        line += ["-c", setting]
    if arm == "hook":
        line += hook_overrides()
    if resume:
        return line + [resume, "-"]
    return line + ["-C", str(args.checkout), "-"]


def rollout(thread: str) -> Path | None:
    """Codex's saved session file for a conversation: every model call's own token counts."""
    found = sorted((codex_home() / "sessions").glob(f"**/rollout-*{thread}.jsonl"), key=lambda p: p.stat().st_mtime) if thread else []
    return found[-1] if found else None


def read_rollout(path: Path | None) -> dict:
    calls, total, model, effort, limits = [], {}, None, None, None
    if not path:
        return {}
    for text in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        payload = event.get("payload") or {}
        if event.get("type") == "turn_context":
            model = payload.get("model") or model
            effort = ((payload.get("collaboration_mode") or {}).get("settings") or {}).get("reasoning_effort") or payload.get("effort") or effort
        elif payload.get("type") == "token_count":
            info = payload.get("info") or {}
            if payload.get("rate_limits"):
                limits = payload["rate_limits"]
            # A repeated count (same running total) is the same model call reported again.
            if info.get("last_token_usage") and info.get("total_token_usage") != total:
                calls.append({field: int(info["last_token_usage"].get(field) or 0) for field in USAGE})
                total = info.get("total_token_usage") or {}
    return {"calls": calls, "total": {field: int(total.get(field) or 0) for field in USAGE}, "model": model, "effort": effort, "rate_limits": limits}


def invoke(codex: str, arm: str, args, prompt: str, out: Path, name: str, resume: str | None = None) -> dict:
    """One `codex exec` (or `codex exec resume`) call; returns what it printed, parsed."""
    started = time.monotonic()
    completed = subprocess.run(command(codex, arm, args, resume), input=prompt, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", cwd=args.checkout, timeout=args.timeout)
    raw = completed.stdout.strip()
    (out / f"{name}.jsonl").write_text(raw + "\n", encoding="utf-8")
    thread, usage, answers, calls, problems = resume, None, [], [], []
    for text in raw.splitlines():
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        kind, item = event.get("type"), event.get("item") or {}
        if kind == "thread.started":
            thread = event.get("thread_id") or thread
        elif kind == "turn.completed":
            usage = {field: int((event.get("usage") or {}).get(field) or 0) for field in USAGE}
        elif kind in ("turn.failed", "error"):
            problems.append(json.dumps(event, ensure_ascii=False)[:400])
        elif kind == "item.completed":
            what = item.get("type")
            if what == "agent_message":
                answers.append(str(item.get("text", "")))
            elif what == "command_execution":
                calls.append(f"shell {str(item.get('command'))[:300]}")
                calls.append(f"  -> result {len(str(item.get('aggregated_output') or '')):,} chars, exit {item.get('exit_code')}")
            elif what == "mcp_tool_call":
                calls.append(f"mcp {item.get('server')}.{item.get('tool')} {json.dumps(item.get('arguments'), ensure_ascii=False)[:300]}")
            elif what == "file_change":
                calls.append("edit " + ", ".join(f"{c.get('kind')} {c.get('path')}" for c in item.get("changes") or []))
            elif what == "web_search":
                calls.append(f"web_search {str(item.get('query'))[:200]}")
            elif what == "error" and TRUST_WARNING not in str(item.get("message")):
                problems.append(str(item.get("message"))[:400])
    if usage is None:
        (out / f"{name}.stderr.txt").write_text(completed.stderr, encoding="utf-8")
        first = next((line for line in (completed.stderr or raw).splitlines() if line.strip() and " INFO " not in line), "")
        raise SystemExit(f"{name} failed; see {out}. First error line: {first.strip()[:300]}")
    # The last message is the answer; earlier ones are progress notes.
    (out / f"{name}.answer.md").write_text(answers[-1] if answers else "", encoding="utf-8")
    return {"thread": thread, "usage": usage, "calls": calls, "problems": problems, "seconds": time.monotonic() - started}


def evidence(project: Project, question: str, session: dict) -> str:
    """What the hook would hand Codex for this question, for the 'prompt' arm."""
    found = prompt_pack(project, question, session=session)
    if not found:
        return ""
    text, response = found
    return "\n\n".join([text] + response.get("parts", []))


def total(usage: dict) -> int:
    # Codex's own total: input (cached input included) plus output (reasoning included).
    return usage["input_tokens"] + usage["output_tokens"]


def uncached(usage: dict) -> int:
    return usage["input_tokens"] - usage["cached_input_tokens"] + usage["output_tokens"]


def run_arm(codex: str, arm: str, args, prompts: list[str], out: Path, number: int) -> dict:
    prepare_checkout(args)
    hook_log = args.checkout / ".balecontext" / "hook-log.jsonl"
    before = len(hook_log.read_text(encoding="utf-8").splitlines()) if hook_log.exists() else 0
    names = [f"run{number}-{arm}"] + [f"run{number}-{arm}.followup" + (str(n) if len(prompts) > 2 else "") for n in range(1, len(prompts))]
    project, session = (Project(args.checkout), {}) if arm == "prompt" else (None, None)
    thread, calls, reported, problems, seconds = None, [], [], [], 0.0
    for position, (prompt, name) in enumerate(zip(prompts, names)):
        if position:
            calls.append(f"--- follow-up question {position} ---")
        if arm == "prompt":
            added = evidence(project, prompt, session)
            calls.append("[prompt] " + (f"evidence {len(added):,} chars" if added else "nothing added"))
            prompt = prompt + ("\n\n" + added if added else "")
        result = invoke(codex, arm, args, prompt, out, name, resume=thread)
        if not result["thread"]:
            raise SystemExit(f"{name}: Codex printed no conversation id, so the follow-up cannot continue it; see {out}.")
        thread = result["thread"]
        calls += result["calls"]
        reported.append(result["usage"])
        problems += result["problems"]
        seconds += result["seconds"]
    changed = collect_changes(args, out, names[0])
    session_file = rollout(thread)
    saved = read_rollout(session_file)
    if session_file:
        shutil.copy2(session_file, out / f"run{number}-{arm}.session.jsonl")
    # Codex reports the conversation's running total after each question, so the last report is
    # the whole task. Its session file has every model call's own count: when both are there they
    # must agree, and the per-call sum wins if they do not.
    usage, note = reported[-1], None
    by_question = [total(now) - (total(previous) if previous else 0) for previous, now in zip([None] + reported[:-1], reported)]
    if saved.get("calls"):
        summed = {field: sum(call[field] for call in saved["calls"]) for field in USAGE}
        if summed == {field: sum(report[field] for report in reported) for field in USAGE} != usage:
            # A Codex version that reports each question's own calls rather than the running total.
            usage, by_question = summed, [total(report) for report in reported]
        elif summed != usage:
            note = f"turn.completed reported {total(usage):,} tokens, the session file's model calls add up to {total(summed):,}; the session file is used"
            print(f"  Note: {note}.")
            usage = summed
    hook_ran = None
    if arm == "hook":
        new_lines = hook_log.read_text(encoding="utf-8").splitlines()[before:] if hook_log.exists() else []
        hook_ran = bool(new_lines)
        added = [json.loads(line) for line in new_lines]
        starts = [0] + [index + 1 for index, call in enumerate(calls) if call.startswith("--- follow-up question")]
        for start, line in reversed(list(zip(starts, added))):
            calls.insert(start, "[hook] " + (f"evidence {line['context_chars']:,} chars" if line.get("context_chars") else "nothing added"))
        if new_lines and not any(line.get("context_chars") for line in added):
            print("  Note: the hook ran but found nothing to add for this question.")
    (out / f"run{number}-{arm}.tools.txt").write_text("\n".join(calls) + "\n", encoding="utf-8")
    row = dict(usage)
    row.update({
        "arm": arm, "run": number,
        "total_tokens": total(usage),
        "uncached_tokens": uncached(usage),
        "model_calls": len(saved.get("calls") or []) or None,
        "tool_calls": sum(not c.startswith(("  ->", "---", "[hook]", "[prompt]")) for c in calls),
        "seconds": round(seconds, 1),
        "model": saved.get("model") or args.model,
        "effort": saved.get("effort") or args.effort,
        "hook_ran": hook_ran,
        "error": bool(problems),
    })
    if problems:
        row["problems"] = problems
    if note:
        row["note"] = note
    if len(prompts) > 1:
        row["tokens_by_question"] = by_question
        row["followup_tokens"] = row["total_tokens"] - by_question[0]
    if args.allow_edits:
        row["changed_files"] = changed
    if saved.get("rate_limits"):
        row["rate_limits_after"] = saved["rate_limits"]
    return row


def saving(base: float | None, assisted: float | None) -> float | None:
    return round(100 * (1 - assisted / base), 1) if base and assisted is not None else None


def summarise(rows: list[dict], arms: list[str], questions: int) -> dict:
    summary = {}
    for arm in arms:
        mine = [row for row in rows if row["arm"] == arm]
        calls = [row["model_calls"] for row in mine if row["model_calls"]]
        summary[arm] = {
            "median_total_tokens": statistics.median(row["total_tokens"] for row in mine),
            "median_uncached_tokens": statistics.median(row["uncached_tokens"] for row in mine),
            "median_output_tokens": statistics.median(row["output_tokens"] for row in mine),
            "median_model_calls": statistics.median(calls) if calls else None,
            "median_tool_calls": statistics.median(row["tool_calls"] for row in mine),
            "models": sorted({str(row["model"]) for row in mine}),
            "errors": sum(bool(row["error"]) for row in mine),
        }
        if questions > 1:
            summary[arm]["median_followup_tokens"] = statistics.median(row["followup_tokens"] for row in mine)
            summary[arm]["median_tokens_by_question"] = [statistics.median(row["tokens_by_question"][i] for row in mine) for i in range(questions)]
    base = summary["without"]
    for arm in arms:
        if arm != "without":
            mine = summary[arm]
            mine["token_saving_percent"] = saving(base["median_total_tokens"], mine["median_total_tokens"])
            mine["uncached_token_saving_percent"] = saving(base["median_uncached_tokens"], mine["median_uncached_tokens"])
            if questions > 1:
                mine["followup_token_saving_percent"] = saving(base["median_followup_tokens"], mine["median_followup_tokens"])
    models = {model for arm in arms for model in summary[arm]["models"]}
    summary["same_model"] = len(models) == 1
    summary["note"] = ("Whole-task totals from Codex's own counts, every model call included" + (f", all {questions} questions of one conversation together" if questions > 1 else "")
                       + ". total = input (cached input included) + output (reasoning included); uncached = total minus cached input. "
                       "Judge answer quality by reading the saved answers; savings mean nothing if the BaleContext answers are worse.")
    return summary


def write_share(out: Path, rows: list[dict], summary: dict, checkout: Path, run: int) -> Path:
    """One Markdown file to share: questions, summary, every run's numbers, and one run's tool calls and answers.
    Local paths are replaced, so it can be posted as it is."""
    replacements = [(str(checkout), "<checkout>"), (checkout.as_posix(), "<checkout>"), (str(codex_home()), "<codex-home>"), (str(Path.home()), "<home>")]

    def clean(text: str) -> str:
        for old, new in replacements:
            text = text.replace(old, new).replace(old.replace("\\", "\\\\"), new)
        return text

    lines = [f"# Codex paired results {out.name}", "", "## Questions", "", "```", (out / "question.txt").read_text(encoding="utf-8").strip(), "```", "",
             "## Summary", "", "```json", json.dumps(summary, indent=2), "```", "", "## Every run", "",
             "| run | arm | total | input | cached | output | reasoning | uncached | model calls | tools | seconds | model |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['run']} | {row['arm']} | {row['total_tokens']:,} | {row['input_tokens']:,} | {row['cached_input_tokens']:,} | {row['output_tokens']:,} | "
                     f"{row['reasoning_output_tokens']:,} | {row['uncached_tokens']:,} | {row['model_calls'] or '-'} | {row['tool_calls']} | {row['seconds']} | {row['model']} |")
    lines.append("")
    if any("tokens_by_question" in row for row in rows):
        lines += ["## Each question's own tokens", "", "| run | arm | question | tokens |", "|---|---|---|---|"]
        lines += [f"| {row['run']} | {row['arm']} | {n} | {tokens:,} |" for row in rows for n, tokens in enumerate(row.get("tokens_by_question", []), 1)]
        lines.append("")
    if any(row.get("problems") or row.get("note") for row in rows):
        lines += ["## Problems reported", ""] + [f"- run {row['run']} {row['arm']}: {clean(p)}" for row in rows for p in row.get("problems", []) + ([row["note"]] if row.get("note") else [])] + [""]
    for path in sorted(out.glob(f"run{run}-*.tools.txt")):
        lines += [f"## Tool calls: {path.name}", "", "```", clean(path.read_text(encoding="utf-8").strip()), "```", ""]
    for path in sorted(out.glob(f"run{run}-*.answer.md")):
        lines += [f"## Answer: {path.name}", "", clean(path.read_text(encoding="utf-8").strip()), ""]
    for path in sorted(out.glob(f"run{run}-*.diff")):
        lines += [f"## Changes: {path.name}", "", "```diff", clean(path.read_text(encoding="utf-8").strip()) or "(no files changed)", "```", ""]
    target = out / "share.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", type=Path)
    parser.add_argument("--question-file", type=Path, required=True)
    parser.add_argument("--followup-file", type=Path, action="append",
                        help="Later questions in the same conversation, one per file or several separated by a line of ---")
    parser.add_argument("--runs", type=int, default=2, help="Pairs to run (default 2)")
    parser.add_argument("--model", help="Same model for both arms (default: your Codex default)")
    parser.add_argument("--effort", help="Same reasoning effort for both arms, such as low, medium or high (default: your Codex default)")
    parser.add_argument("--config", action="append", help="Any other Codex setting for both arms, as KEY=VALUE (passed as -c)")
    parser.add_argument("--clean-config", action="store_true", help="Run both arms without your ~/.codex/config.toml (--ignore-user-config); sign-in still works")
    parser.add_argument("--arms", default="without,hook", help="Comma-separated arms: without, hook, prompt (default without,hook)")
    parser.add_argument("--timeout", type=int, default=1800, help="Seconds per question")
    parser.add_argument("--same-prompt", action="store_true", help="Send the identical prompt every time (lets later runs reuse earlier runs' cache)")
    parser.add_argument("--out", type=Path, help="Results folder (default: codex-test/<time> in the current folder)")
    parser.add_argument("--allow-edits", action="store_true", help="Let Codex edit files. Needs a clean git checkout; each run's changes are saved as a diff and undone.")
    parser.add_argument("--setup-patch", type=Path, help="A git patch applied before every run (a planted bug to find) and undone at the end.")
    args = parser.parse_args(argv)
    args.checkout = args.checkout.resolve()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    if len(arms) < 2 or len(set(arms)) != len(arms) or set(arms) - set(ARMS) or "without" not in arms:
        raise SystemExit("--arms needs 'without' and at least one of 'hook' or 'prompt', each once.")
    question = args.question_file.read_text(encoding="utf-8").strip()
    followups = [part.strip() for path in args.followup_file or [] for part in re.split(r"(?m)^---\s*$", path.read_text(encoding="utf-8"))]
    if not question or (args.followup_file and not all(followups)):
        raise SystemExit("A question file is empty.")
    codex = find_codex()
    refuse_contaminated(args.checkout, args.clean_config)
    if args.allow_edits or args.setup_patch:
        refuse_dirty(args.checkout)
        if args.setup_patch:
            args.setup_patch = args.setup_patch.resolve()
            git(args.checkout, "apply", "--check", str(args.setup_patch))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = (args.out or Path.cwd() / "codex-test" / f"{stamp}-{args.question_file.stem}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    print("Indexing the checkout once before the runs (no model calls)...")
    Project(args.checkout).index()
    (out / "question.txt").write_text(question + "\n" + "".join(f"\nFollow-up {n}:\n{text}\n" for n, text in enumerate(followups, 1)), encoding="utf-8")
    (out / "settings.json").write_text(json.dumps({"arms": arms, "model": args.model, "effort": args.effort, "config": args.config or [], "clean_config": args.clean_config,
                                                   "hook": {event: codex_hook_settings(event) for event in CODEX_EVENTS},
                                                   "codex_version": subprocess.run([codex, "--version"], capture_output=True, text=True).stdout.strip()}, indent=2), encoding="utf-8")
    rows = []
    # BaleContext's arm goes first in run 1, so a Codex that does not run the hook stops the test
    # before a baseline run is spent; later runs rotate the order.
    order = [arm for arm in arms if arm != "without"] + ["without"]
    try:
        for number in range(1, args.runs + 1):
            shift = (number - 1) % len(order)
            for arm in order[shift:] + order[:shift]:
                print(f"Run {number}: {arm}...")
                # A number BaleContext's query parser ignores; it only stops cross-task cache reuse.
                prompts = [text if args.same_prompt else f"{text}\n\n#{random.randrange(10**5, 10**6)}" for text in [question] + followups]
                row = run_arm(codex, arm, args, prompts, out, number)
                rows.append(row)
                print(f"  {row['total_tokens']:,} tokens ({row['uncached_tokens']:,} not cached), {row['model_calls'] or '?'} model calls, "
                      f"{row['tool_calls']} tool calls, {row['seconds']}s" + (f"; changed {len(row['changed_files'])} file(s)" if "changed_files" in row else ""))
                if row.get("problems"):
                    print("  Codex reported: " + " | ".join(row["problems"])[:600])
                if arm == "hook" and not row["hook_ran"]:
                    raise SystemExit("Codex did not run BaleContext's hook, so this arm would equal 'without'. Stopped to save your limits.\n"
                                     "Run again with --arms without,prompt, which writes the same evidence into the question, and tell Claude.")
    finally:
        if args.allow_edits or args.setup_patch:
            reset_checkout(args)
        if rows:
            (out / "summary.json").write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
    done = [arm for arm in arms if any(row["arm"] == arm for row in rows)]
    summary = summarise(rows, done, 1 + len(followups))
    (out / "summary.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(f"\n{'run':>3} {'arm':8} {'total':>10} {'input':>10} {'cached':>10} {'output':>8} {'uncached':>9} {'calls':>5} {'tools':>5}")
    for row in rows:
        print(f"{row['run']:>3} {row['arm']:8} {row['total_tokens']:>10,} {row['input_tokens']:>10,} {row['cached_input_tokens']:>10,} "
              f"{row['output_tokens']:>8,} {row['uncached_tokens']:>9,} {row['model_calls'] or '-':>5} {row['tool_calls']:>5}")
    print(json.dumps(summary, indent=2))
    if not summary["same_model"]:
        print("WARNING: the arms did not all use the same model; the comparison is not fair.")
    print(f"\nAnswers and raw results: {out}")
    print(f"To share: {write_share(out, rows, summary, args.checkout, min(2, args.runs))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
