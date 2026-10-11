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
  to about 0.25 s. Index schema 3.
- Offline relevance benchmark (`benchmarks/relevance.py`) with gold answers for
  Django and requests.

## 0.1.0 — initial alpha

Original local project-knowledge implementation by Muhammad Baleegh: incremental
indexing, exact-source retrieval, static relationship graphs, source-linked
notes, MCP tools, project client setup, offline graph export and regression
tests. No end-to-end token-savings result has been established yet.
