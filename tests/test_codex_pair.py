"""The paired Codex test, run end to end against a stand-in `codex` command (no model calls)."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"
sys.path.insert(0, str(BENCHMARKS))
spec = importlib.util.spec_from_file_location("codex_pair", BENCHMARKS / "codex_pair.py")
pair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pair)

# Behaves like `codex exec --json` for what the paired test reads: reads the prompt from stdin,
# runs the UserPromptSubmit hook given with -c through the shell, prints thread.started,
# item.completed and turn.completed (the conversation's running total), and saves each model
# call's count in a session file under CODEX_HOME. Without evidence it "searches" in two calls.
FAKE_CODEX = r'''
import json, os, subprocess, sys, tomllib, uuid
from pathlib import Path
home = Path(os.environ["CODEX_HOME"])
argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("codex-cli 0.0.0-test"); sys.exit(0)
if argv[:2] == ["exec", "--help"]:
    print("--json --dangerously-bypass-hook-trust"); sys.exit(0)
resume = argv[1] == "resume"
settings, cwd, words, index = {}, os.getcwd(), [], 2 if resume else 1
while index < len(argv):
    word = argv[index]
    if word == "-c":
        key, value = argv[index + 1].split("=", 1)
        settings[key] = tomllib.loads("v = " + value)["v"]
        index += 2
    elif word in ("-m", "-C"):
        cwd = argv[index + 1] if word == "-C" else cwd
        index += 2
    elif word.startswith("-") and word != "-":
        index += 1
    else:
        words.append(word)
        index += 1
assert words[-1] == "-", words
prompt = sys.stdin.read()
thread = words[0] if resume else str(uuid.uuid4())
state = home / (thread + ".json")
used = json.loads(state.read_text()) if resume else dict.fromkeys(("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"), 0)
context = ""
for group in settings.get("hooks.UserPromptSubmit", []):
    for hook in group["hooks"]:
        done = subprocess.run(hook["command"], shell=True, capture_output=True, text=True, encoding="utf-8", cwd=cwd,
                              input=json.dumps({"session_id": thread, "cwd": cwd, "hook_event_name": "UserPromptSubmit", "prompt": prompt}))
        if done.stdout.strip():
            context += json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]
calls = [1000 + (len(prompt) + len(context)) // 4] if context else [1000 + len(prompt) // 4, 3000 + len(prompt) // 4]
log = home / "sessions" / "2026" / ("rollout-2026-test-" + thread + ".jsonl")
log.parent.mkdir(parents=True, exist_ok=True)
with log.open("a", encoding="utf-8") as out:
    out.write(json.dumps({"type": "turn_context", "payload": {"model": settings.get("model", "test-model")}}) + "\n")
    for size in calls:
        last = {"input_tokens": size, "cached_input_tokens": 500, "output_tokens": 100, "reasoning_output_tokens": 40}
        used = {k: used[k] + last[k] for k in used}
        out.write(json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": used, "last_token_usage": last}}}) + "\n")
state.write_text(json.dumps(used))
print(json.dumps({"type": "thread.started", "thread_id": thread}))
if not context:
    print(json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "rg checkout_order " + cwd, "aggregated_output": "shop.py", "exit_code": 0}}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Answer from " + cwd + " with evidence chars " + str(len(context))}}))
print(json.dumps({"type": "turn.completed", "usage": used}))
'''

SHOP = "".join(f"def step_{i}(order):\n    order.total += {i}\n    return order\n\n" for i in range(3)) + \
    "def checkout_order(order):\n" + "".join(f"    order = step_{i}(order)\n" for i in range(3)) + "    return order\n"


class CodexPairTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="balecontext codex ")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.checkout = self.base / "shop project"
        self.checkout.mkdir()
        (self.checkout / "shop.py").write_text(SHOP, encoding="utf-8")
        tools = self.base / "bin"
        tools.mkdir()
        (tools / "fake_codex.py").write_text(FAKE_CODEX, encoding="utf-8")
        if os.name == "nt":
            # The real Codex is installed by npm as codex.cmd, so the -c settings pass through cmd.exe.
            (tools / "codex.cmd").write_text(f'@echo off\r\n"{sys.executable}" "%~dp0fake_codex.py" %*\r\n', encoding="utf-8")
        else:
            (tools / "codex").write_text(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/fake_codex.py" "$@"\n', encoding="utf-8")
            (tools / "codex").chmod(0o755)
        (self.base / "home").mkdir()
        self.environment = {"CODEX_HOME": str(self.base / "home"), "PATH": str(tools) + os.pathsep + os.environ.get("PATH", "")}
        (self.base / "q.txt").write_text("How does checkout_order work through each step? Do not modify any files.", encoding="utf-8")
        (self.base / "f.txt").write_text("What does step_1 add to the order total?", encoding="utf-8")

    def run_pair(self, *extra: str) -> tuple[dict, Path]:
        out = self.base / "results"
        with mock.patch.dict(os.environ, self.environment), mock.patch("sys.stdout"):
            self.assertEqual(pair.main([str(self.checkout), "--question-file", str(self.base / "q.txt"), "--runs", "2", "--out", str(out), *extra]), 0)
        return json.loads((out / "summary.json").read_text(encoding="utf-8")), out

    def test_hook_arm_gets_the_evidence_and_whole_task_tokens_are_compared(self):
        data, out = self.run_pair("--followup-file", str(self.base / "f.txt"))
        rows = {(row["run"], row["arm"]): row for row in data["rows"]}
        self.assertEqual(len(rows), 4)
        for run in (1, 2):
            hook, plain = rows[(run, "hook")], rows[(run, "without")]
            self.assertTrue(hook["hook_ran"])
            # The fake answers in one model call with evidence and searches in two without it.
            self.assertEqual((hook["model_calls"], plain["model_calls"]), (2, 4))
            self.assertEqual(hook["total_tokens"], sum(hook["tokens_by_question"]))
            self.assertEqual(hook["total_tokens"], hook["input_tokens"] + hook["output_tokens"])
            self.assertEqual(hook["uncached_tokens"], hook["total_tokens"] - hook["cached_input_tokens"])
            self.assertNotIn("note", hook)
        self.assertGreater(data["summary"]["hook"]["token_saving_percent"], 0)
        self.assertTrue(data["summary"]["same_model"])
        answer = (out / "run1-hook.answer.md").read_text(encoding="utf-8")
        self.assertNotIn("evidence chars 0", answer)
        self.assertIn("evidence chars 0", (out / "run1-without.answer.md").read_text(encoding="utf-8"))
        self.assertTrue((out / "run1-hook.followup.answer.md").exists())
        self.assertTrue((out / "run1-hook.session.jsonl").exists())
        tools = (out / "run1-hook.tools.txt").read_text(encoding="utf-8").splitlines()
        self.assertTrue(tools[0].startswith("[hook] evidence "))
        self.assertEqual(tools[1], "--- follow-up question 1 ---")
        # The file to share names no local folder.
        share = (out / "share.md").read_text(encoding="utf-8")
        self.assertNotIn(str(self.checkout), share)
        self.assertNotIn(str(self.base), share)
        self.assertIn("<checkout>", share)

    def test_prompt_arm_writes_the_same_evidence_into_the_question(self):
        data, out = self.run_pair("--arms", "without,prompt")
        self.assertEqual(sorted(row["arm"] for row in data["rows"]), ["prompt", "prompt", "without", "without"])
        first = (out / "run1-prompt.tools.txt").read_text(encoding="utf-8").splitlines()[0]
        self.assertRegex(first, r"^\[prompt\] evidence [\d,]+ chars$")
        # No hook is configured in this arm, so the stand-in saw no hook context.
        self.assertIn("evidence chars 0", (out / "run1-prompt.answer.md").read_text(encoding="utf-8"))

    def test_balecontext_already_set_up_for_codex_stops_the_test(self):
        (self.checkout / "AGENTS.md").write_text("<!-- balecontext:start -->\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as stopped:
            self.run_pair()
        self.assertIn("AGENTS.md mentions BaleContext", str(stopped.exception))
        (self.checkout / "AGENTS.md").unlink()
        (self.base / "home" / "config.toml").write_text("[mcp_servers.balecontext]\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as stopped:
            self.run_pair()
        self.assertIn("--clean-config", str(stopped.exception))

    def test_hook_settings_survive_the_command_line_as_toml(self):
        settings = {"type": "command", "command": r'C:\Program Files\Python\python.exe -m balecontext hook codex', "timeout": 120, "additionalContextLimit": 16000}
        self.assertEqual(tomllib.loads("v = " + pair.toml_inline([{"hooks": [settings]}]))["v"], [{"hooks": [settings]}])
        words = pair.hook_overrides()
        self.assertEqual([w.split("=", 1)[0] for w in words[1::2]], ["hooks.UserPromptSubmit", "hooks.PreCompact"])


if __name__ == "__main__":
    unittest.main()
