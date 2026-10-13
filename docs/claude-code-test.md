# Testing BaleContext in Claude Code

Two parts: using BaleContext day to day in Claude Code, and a paired test that
measures whole-task tokens on the same question with and without it.

## Use it in a project

```
py -m pip install -e ".[code]"        # in the balecontext folder (python3 on macOS/Linux)
balecontext doctor                    # javascript_typescript should be true
cd C:\path\to\your\project
balecontext install claude --hook     # adds .mcp.json, a short CLAUDE.md block and the prompt hook, with backups
```

`--hook` adds a Claude Code prompt hook to `.claude/settings.json`. When your
message names code in the project, BaleContext hands Claude the evidence before
its first turn, so a well-covered question can be answered without any search.
Messages that name no project code get nothing added. Claude Code keeps up to
10,000 characters from each hook, so the hook is registered five times
(`--part 2` to `--part 5` beside the first): Claude Code runs them side by side,
and evidence longer than one hook's limit arrives in up to five parts with the
message. Within one conversation, source already sent for an earlier message is
named rather than sent again; the hook forgets that when Claude Code compacts the
conversation. Each prompt is logged (time and sizes only) in
`.balecontext/hook-log.jsonl`.

Restart Claude Code in that folder, approve the `balecontext` server when asked,
and run `/mcp`: it should list five BaleContext tools.

## Paired test (uses your Claude plan limits)

The script asks Claude Code the same question in the same Django folder in
each setup ("arm"), and repeats that for each run. The arms are `without`
(plain Claude Code), `with` (BaleContext's tools) and `hook` (only the prompt
hook, so Claude Code's own instructions and tools stay identical to `without`
and its cached-prompt pricing is comparable). The default is `without,hook`; choose with
`--arms without,with,hook`. All arms use the same model and the same read-only tools (Read, Grep, Glob; no edits, no
shell). Token totals come from Claude Code's own result for the whole task,
every model call included. Nothing in the Django folder is changed.

1. Use a Django folder with no BaleContext setup in it (no `.mcp.json` or
   `CLAUDE.md` mentioning BaleContext). The script stops if it finds one,
   because the "without" side would not be clean.
2. Check Claude Code works: `claude --version`, and that you are signed in.
3. From the balecontext folder, run:

   ```
   py benchmarks\claude_pair.py C:\path\to\django --question-file benchmarks\questions\django-form-is-valid.txt --runs 3 --model sonnet
   ```

   Three runs means six Claude Code tasks. Use `--runs 1` for a first look,
   but trust only three or more: Claude Code caches its instructions between
   tasks, so a single run's cost depends on what ran just before it. Each
   task's prompt ends with a different number so no task reuses another
   task's cached conversation; `--same-prompt` turns that off (results before
   this change used identical prompts, which flattered the hook's later runs).
4. The script prints a table and the saving, and saves everything in
   `benchmarks\claude-pair-results\<time>\`: `summary.json`, each answer as
   `runN-<arm>.answer.md`, every tool call in `runN-<arm>.tools.txt`, and the
   raw results. If the hook arm prints a warning that the hook did not run,
   tell me: that Claude Code version handles hooks differently.
5. Read the answers. The saving only counts if the BaleContext answers are
   as correct and complete as the "without" ones.

### More questions

The same script works on other projects. Each question below matches a set in
`benchmarks/relevance/`, pinned to the commit the offline checks used:

```
git clone https://github.com/expressjs/express.git express-test
git -C express-test checkout a419aa7
py benchmarks\claude_pair.py C:\path\to\express-test --question-file benchmarks\questions\express-res-json.txt --runs 3

git clone https://github.com/psf/requests.git requests-test
git -C requests-test checkout 611c616
py benchmarks\claude_pair.py C:\path\to\requests-test --question-file benchmarks\questions\requests-session-request.txt --runs 3
```

Offline, the hook sends a COMPLETE pack for the Express question and a PARTIAL
one for the requests trace, which touches more of the library than fits.

### Two questions in one conversation

People usually ask several questions in one Claude Code conversation. With
`--followup-file`, each task asks a second question in the same conversation, and the
totals cover both questions; `summary.json` also gives the second question alone
(`median_followup_tokens`, `followup_*_saving_percent`). Every question goes to one
Claude Code session, each sent once the previous answer is in, as when you type them
one after another. `--resume-each` instead starts Claude Code again for each later
question (`claude -p --resume`), as when you come back to a saved conversation.
The hook remembers, per conversation, which source it already sent and names it
instead of sending it again, until Claude Code compacts the conversation. When the
second question starts from code already sent, the hook adds only the new definitions
it names, with no extra parts.

```
py benchmarks\claude_pair.py C:\path\to\requests-test --question-file benchmarks\questions\requests-session-request.txt --followup-file benchmarks\questions\requests-session-send-followup.txt --runs 3
```

Each answer to the second question is saved as `runN-<arm>.followup.answer.md`.

For a longer conversation, put several follow-up questions in one file, separated by a
line of three dashes (`---`). The answers are then saved as `runN-<arm>.followup1.answer.md`,
`followup2`, and so on, and a table shows each question's own tokens and cost, so you can
see how the difference grows as the conversation goes on:

```
py benchmarks\claude_pair.py C:\path\to\requests-test --question-file benchmarks\questions\requests-session-request.txt --followup-file benchmarks\questions\requests-chat-followups.txt --runs 3
py benchmarks\claude_pair.py C:\path\to\django-test --question-file benchmarks\questions\django-form-is-valid.txt --followup-file benchmarks\questions\django-chat-followups.txt --runs 3
```

Harder questions, each spanning several modules (a request through the middleware chain and
URL resolution, a management command from the command line to `handle()`, and the `extends`
and `block` template tags), are in one Django conversation:

```
py benchmarks\claude_pair.py C:\path\to\django-test --question-file benchmarks\questions\django-request-handling.txt --followup-file benchmarks\questions\django-hard-followups.txt --runs 3
```

### Bigger tasks: find a bug, add a feature

Small questions stop at about 70 to 80% saved, because Claude Code's own setup costs the
same on both sides at every step. Bigger tasks, where plain Claude Code needs ten or more
steps, are where the difference grows. `benchmarks\tasks\` holds two planted bugs and two
features for the Django and requests folders, with notes on what a correct result looks like
(`benchmarks\tasks\README.md`). They run with `--allow-edits`, which lets Claude edit files
(still no shell), saves each run's changes as `runN-<arm>.diff` and in `share.md`, and puts the
folder back as it was. `--setup-patch` plants the bug before every run and removes it at the
end. The folder must be clean in git, or the script refuses to start.

```
py benchmarks\claude_pair.py C:\path\to\django-test --question-file benchmarks\tasks\django-bug-process-view-order.txt --allow-edits --setup-patch benchmarks\tasks\django-bug-process-view-order.patch --runs 1
```

The results folder contains your local paths, so it is ignored by git. At the end of
each test, `claude_pair.py` writes `share.md` in that folder and prints its path: the
questions, the summary, each question's tokens and cost, every model call's usage for
run 2, and run 2's tool calls and answers for both arms. Attach that one file to share
the test. `py benchmarks\share_results.py RESULTS_FOLDER` writes it again for an older
results folder.

When a later question costs more than expected, run
`py benchmarks\session_report.py RESULTS_FOLDER --run 2` as well. It reads the session files
Claude Code saved for that run (in your user folder's `.claude\projects`) and writes
`sessions.md`: the kind and size of every saved record, the prompt settings Claude Code
saved, and each model call's cache numbers. It contains no prompt, code or answer text.
