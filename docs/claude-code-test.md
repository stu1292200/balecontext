# Testing BaleContext in Claude Code

Two parts: using BaleContext day to day in Claude Code, and a paired test that
measures whole-task tokens on the same question with and without it.

## Use it in a project

```
py -m pip install -e ".[code]"        # in the balecontext folder (python3 on macOS/Linux)
balecontext doctor                    # javascript_typescript should be true
cd C:\path\to\your\project
balecontext install claude            # adds .mcp.json and a short CLAUDE.md block, with backups
```

Restart Claude Code in that folder, approve the `balecontext` server when asked,
and run `/mcp`: it should list five BaleContext tools.

## Paired test (uses your Claude plan limits)

The script asks Claude Code the same question in the same Django folder, once
with BaleContext and once without, and repeats that for each run. Both sides use
the same model and the same read-only tools (Read, Grep, Glob; no edits, no
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

   Three runs means six Claude Code tasks. Use `--runs 1` for a first look.
4. The script prints a table and the saving, and saves everything in
   `benchmarks\claude-pair-results\<time>\`: `summary.json`, each answer as
   `runN-with.answer.md` / `runN-without.answer.md`, and the raw results.
5. Read the paired answers. The saving only counts if the "with" answers are
   as correct and complete as the "without" ones.

The results folder contains your local paths, so it is ignored by git; share
`summary.json` and the answers by hand.
