# Roadmap

## v0.1 alpha — current implementation

- Local incremental SQLite index and source-backed retrieval.
- Python AST; optional JavaScript/TypeScript tree-sitter structure.
- Imports, inferred calls, bounded relationship and shortest-path queries.
- Explicit notes invalidated by source hashes.
- CLI, tools-only MCP stdio server and project configuration adapters.
- Offline graph viewer, JSON graph and coverage report.
- Regression tests and a protocol for measuring actual total model tokens.

## Before claiming a broadly supported release

- Unreleased source: connection-scoped background polling, consistent read
  snapshots, per-task benchmark regression checks and optional elapsed timings.

- Run live Claude Code and Codex integration checks; then Gemini CLI and Cursor.
- Measure ordinary agent workflows against the same workflows with BaleContext.
- Publish success rates, individual regressions, token totals and run variance.
- Review secret exclusions, filesystem race boundaries and large-repository behavior.
- Complete brand/trademark review before investing in permanent public branding.
- Publish a signed/checksummed release and, once ready, a PyPI package.

## Later, driven by evidence

- Accurate additional-language parsers and project-aware import resolution.
- Better test/caller selection, relevance ranking and optional local semantic search.
- Efficient file-event watching to replace polling, and large-monorepo indexing.
- More verified agent adapters, including the requested Antigravity and Hermes clients.
- Text extraction for PDFs and office documents, then optional media workflows.

The guiding target remains premium-quality outcomes with much less total model
usage. A universal 90–95% reduction is not promised; quality comes first.
