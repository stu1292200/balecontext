# Changelog

## Unreleased — ranking and evidence packs

- Symbol-first ranking: identifiers in the query (`form.is_valid`, `BaseForm full_clean`)
  are matched by name and class qualifier before any body-text match; instruction words
  such as "cite", "files" and "line numbers" are ignored; implementation code ranks
  above tests and docs unless the question asks for them.
- `balecontext_context` returns an evidence pack: map, entry point plus the definitions
  it calls in call order, and a COMPLETE/PARTIAL coverage verdict. The first response
  is capped at 4,000 estimated tokens; long docstrings are collapsed and marked.
- New `balecontext_expand` tool fetches full definitions by id.
- The MCP server remembers which source ranges it already sent in a session.
- Edges record the call-site line; `self.attr` property uses and same-class local
  object calls are resolved (INFERRED). The index rebuilds automatically on schema change.
- Refresh skips reading files whose size and modification time are unchanged (with a
  2-second racily-clean guard), cutting an unchanged refresh of Django from about 4.2 s
  to about 0.25 s.
- Offline relevance benchmark (`benchmarks/relevance.py`) with gold answers for
  Django, requests, Express (JavaScript) and Hono (TypeScript).
- The entry point is the outermost matching function: the match whose call chain
  reaches the other matches leads (`is_valid` before `full_clean`, `process_view`
  before `_check_token`).
- JavaScript and TypeScript: prototype-style members (`res.send = function`,
  `View.prototype.lookup`, `exports.x`), assignment aliases, class field arrow
  functions and `#private` members are definitions. Dotted calls defined once in the
  project (`app.render`) resolve across files, and the entry's direct calls into other
  modules are followed. A bare call inside a method no longer resolves to that method.
- Calls on `self.`/`this.` chains that cannot be followed are recorded, and any shown
  definition with one makes the verdict PARTIAL ("calls not followed: ...") instead of
  claiming COMPLETE. Type declarations rank below behaviour. Index schema 4.
- Packs follow the chain where it leaves the entry's file: calls into other modules,
  calls on objects whose type is unknown when at most three project methods carry the
  name (`field._clean_bound_field(bf)`, labelled "receiver type not known" and stored
  as CANDIDATE edges that the main walk, graph and paths ignore), and direct subclass
  overrides (`BaseModelForm._post_clean`). Those within three calls of the entry or
  named like the question are shown at full length when budget is left, and otherwise
  named, with path and lines, in a PARTIAL verdict; the rest are listed as NOT FOLLOWED
  without blocking a COMPLETE verdict. A candidate whose class is named like the
  receiver (`self.query.chain()` → `Query.chain`) wins over the others. The entry's own
  chain is shown at full length first. Index schema 8.
- When budget remains, a last rebuild shows long definitions whole (up to 160
  lines), and an alias sharing a shown range no longer counts as missing.
- When the hook's pack is PARTIAL, a larger pack's remaining definitions are saved to one
  file under `.balecontext/hook/` and the pack names it, so the model reads one file
  instead of searching file by file (the last 20 files are kept). That larger pack
  (12,000-token budget) shows every followed call in full before any of their helpers,
  and its last line marks what is still missing as deeper detail to read only if needed.
- Calling a class follows its constructor (`Request(...)` → `Request.__init__`); a class
  without one is left out instead of showing its header. Parameter lists of eight or more
  lines collapse to one line naming the parameters.
- Unknown-receiver calls match the class named like the receiver even for common names
  (`adapter.send()` → `HTTPAdapter.send`), prefer a real implementation over an abstract
  stub and a base class over its subclasses, and never come from an imported module
  (`re.compile`). `kwargs.get()` inside a class is no longer that class's `get`. Followed
  calls named like the question come first.
- The prompt hook sends a compact pack (short source headings, shared indentation
  dropped, no query echo or id map) and starts from a larger budget, so more source fits
  under the hook limit.
  `benchmarks/relevance.py --hook` measures what the hook would send.
- The paired test ends each task's prompt with a different number so later runs cannot
  reuse an earlier run's cached conversation (`--same-prompt` restores the old behaviour).
- `benchmarks/claude_pair.py` and [docs/claude-code-test.md](docs/claude-code-test.md):
  a paired Claude Code test with and without BaleContext, recording every tool call.
- `balecontext hook claude` and `install claude --hook`: a Claude Code prompt hook that
  hands the model BaleContext's evidence before its first turn when the prompt names
  project code (kept under Claude Code's 10,000-character hook limit). It never indexes
  a folder that was not already indexed and never blocks a prompt.
- Start local background indexing with an initialized agent MCP connection.
  Handle edits, additions, renames, deletions and new exclusions without a query.
  Add `mcp --watch-interval` and `--no-watch`; per-request refresh remains enabled.
- Keep retrieval reads on one SQLite snapshot and serialize discovery with
  index writes to prevent out-of-order scans from concurrent agent processes.
- Flag individual benchmark task regressions even when overall pass counts tie.
  Support optional elapsed-time comparisons including local setup time.
- Document a reproducible first evaluation plan. Real agent token and quality
  results remain unmeasured.

## 0.1.0 — initial alpha

Original local project-knowledge implementation by Muhammad Baleegh: incremental
indexing, exact-source retrieval, static relationship graphs, source-linked
notes, MCP tools, project client setup, offline graph export and regression
tests. No end-to-end token-savings result has been established yet.
