# Testing BaleContext in Codex

Two parts: using BaleContext day to day in Codex CLI, and a paired test that
measures whole-task tokens on the same question with and without it.

## How Codex gets the evidence

Codex CLI (0.162 and later) runs hooks the same way Claude Code does: on each
prompt it runs a `UserPromptSubmit` command, gives it the prompt as JSON, and
adds what the command prints to the conversation before the model's first call.
`balecontext hook codex` is that command. When the prompt names code in the
project, it prints the evidence pack; for chat it prints nothing.

Differences from Claude Code, all checked against Codex 0.162.1 with a local
stand-in for the model server (no paid calls):

- Codex keeps about 2,500 tokens of a hook's text and cuts the middle out of the
  rest. The BaleContext hook sets `additionalContextLimit` to 16,000 tokens, so
  the whole pack (up to about 45,000 characters) arrives in one piece. Claude
  Code needs five hooks side by side for the same pack.
- The evidence arrives as a developer message right after your prompt. Codex's
  own instructions and tools before it are unchanged, so the cached start of the
  conversation is the same with and without BaleContext.
- A hook is one command string (no separate arguments). The installer writes the
  Python path without quotes; on Windows a path with spaces is written in its
  short form.
- Codex runs a new hook only after you review it once.

The MCP tools alone are not enough: in the first Codex test (11 October) the
agent called BaleContext and then read the same files anyway, and in Claude Code
the tools without the hook saved nothing.

## Use it in a project

```
py -m pip install -e ".[code]"        # in the balecontext folder
cd C:\path\to\your\project
balecontext install codex --hook      # adds .codex/config.toml, an AGENTS.md block and .codex/hooks.json, with backups
```

Then start `codex` in that folder, type `/hooks`, and approve the BaleContext
hook. Codex reads project `.codex/` settings only in a trusted project. Each
prompt is logged (time and sizes only) in `.balecontext/hook-log.jsonl`.

## Paired test (uses your Codex plan limits)

`benchmarks/codex_pair.py` asks Codex the same question in the same folder in
each setup ("arm"), and repeats that for each run:

- `without`: plain Codex.
- `hook`: the BaleContext hook, passed to that one run on the command line
  (`-c hooks...`), so nothing is written to your Codex settings or the project.
- `prompt`: the same evidence written into the question by the script. Use it
  only if your Codex does not run the hook.

The default is `without,hook`. Both arms use the same model, reasoning effort and
read-only sandbox, and your own Codex settings (a Windows sandbox setting, say).
Both run with web search and connectors off, so both answer from the local
folder: in the first run, plain Codex could not run commands and fetched the
upstream source from GitHub instead, which is not a fair baseline. The script
now stops when plain Codex ran no command in the folder that succeeded. Skills you
installed yourself (in `%USERPROFILE%\.agents\skills`, Codex's `skills` folder or the
project) are switched off in both arms too: in one test plain Codex read a 70,000-character
code-graph skill at the start of every task. Codex's built-in skills stay on. Token totals are Codex's own for the whole conversation,
every model call included (`turn.completed` in `codex exec --json`, checked
against each call's count in Codex's saved session file). `total` is input
(cached input included) plus output (reasoning included); `uncached` leaves the
cached input out. The script never judges answers: they are saved side by side.

1. Update Codex and check you are signed in: `npm install -g @openai/codex@latest`,
   then `codex --version` (0.162 or later) and `codex login status`.
2. Use a folder with no BaleContext setup in it (no `AGENTS.md`, `.codex/config.toml`
   or `.codex/hooks.json` mentioning BaleContext, here or in `%USERPROFILE%\.codex`).
   The script stops if it finds one. A BaleContext MCP server in your own
   `config.toml` is fine: both arms run with it switched off (`enabled=false`).
3. From the balecontext folder:

   ```
   py benchmarks\codex_pair.py "D:\path\to\django" --question-file benchmarks\questions\django-form-is-valid.txt --runs 2
   ```

   Two runs means four Codex tasks. Run 1 starts with the BaleContext arm: if
   Codex does not run the hook, the script stops before spending a plain run.
4. Results go to `codex-test\<time>-<question>\` in the folder you run from:
   `summary.json`, `share.md` (one file to share, with local paths replaced),
   each answer as `runN-<arm>.answer.md`, every command Codex ran in
   `runN-<arm>.tools.txt`, the raw output, and Codex's session files.
5. Read the answers. The saving only counts if the BaleContext answers are as
   correct and complete as the plain ones.

Options: `--model` and `--effort` fix the model and reasoning effort for both arms
(default: your Codex defaults; the model used is recorded either way),
`--followup-file` asks later questions in the same conversation (`codex exec
resume`), `--allow-edits` and `--setup-patch` work as in the Claude Code test.
