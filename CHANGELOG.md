# Changelog

## Unreleased

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
