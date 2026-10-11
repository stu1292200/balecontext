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
- `benchmarks/claude_pair.py` and [docs/claude-code-test.md](docs/claude-code-test.md):
  a paired Claude Code test with and without BaleContext.

## 0.1.0 — initial alpha

Original local project-knowledge implementation by Muhammad Baleegh: incremental
indexing, exact-source retrieval, static relationship graphs, source-linked
notes, MCP tools, project client setup, offline graph export and regression
tests. No end-to-end token-savings result has been established yet.
