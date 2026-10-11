# Architecture

```text
Local repository
  → boundary checks, exclusions, Git ignore rules, UTF-8 decoding
  → content hashes: unchanged files are not reparsed
  → Python AST / optional JS+TS tree-sitter / exact text chunks
  → SQLite nodes, references, edges, full-text index, cited notes
  → ranked lexical matches + bounded neighboring dependencies
  → source excerpts with line numbers and provenance
  → CLI / MCP stdio / offline graph export
```

No model calls, embeddings, external servers, or network requests occur in the
index/retrieval runtime. Git, if present, is called with a fixed argument list
only to apply its ignore semantics. Arbitrary commands from project data are
never executed.

Index updates run in a SQLite transaction with WAL and a write lock. File content
hashes drive invalidation. Deletions and new exclusions remove stale nodes,
references and search entries. Cross-file edges are resolved again after a
change. This makes small edits cheap to parse, though rebuilding all edges and
rescanning file contents still have repository-size cost. Large-monorepo
performance is not yet benchmarked. Discovery occurs under the write lock so
concurrent indexers cannot overwrite a newer scan with an older one. Retrieval
uses a SQLite read transaction to keep its statements on one consistent index
snapshot while background updates commit.

Source files are snapshots from the last refresh. A concurrent source edit after
that refresh can still happen; returned file hashes identify the snapshot.
Queries refresh before reading. Re-run the query after editing.

In the unreleased source, the MCP server starts a background polling thread after
its initialization response. It refreshes immediately, then waits two seconds
between completed scans. Rapid edits are collected by the next scan; a single
scan is not an atomic filesystem snapshot. There is no AI summarization or
automatic source rewriting. The watcher retries transient failures and exposes
its state through `balecontext_status`, without logging to protocol stdout.
Foreground tool calls always perform their own refresh. Disconnecting stops
scheduling scans; a daemon scan already running may finish until the process
exits. `mcp --no-watch` disables background work, and `--watch-interval` controls
the interval. No operating-system service or standalone background app is
installed. Exported viewers remain static snapshots.

Symbol IDs derive from relative path, qualified name, kind and start line. They
are deterministic within a snapshot but can change when code moves. Line-based
containment and imported-symbol resolution are static approximations. Calls are
always labeled INFERRED. There is no claim of complete data flow or formal
verification.

FTS5 provides lexical ranking with name/path weights. Graph expansion follows
resolved imports and calls around the first few matches. Exact excerpts are
packed under an approximate text-size budget; long definitions are truncated
explicitly, and all source remains independently searchable in chunks. Missing
evidence calls for normal search or additional reads, not a fabricated answer.

Notes are explicit user-supplied local data with citations to file hashes.
Changing or removing any citation invalidates the note for retrieval. Stale
records remain in the local database for audit; there is no automatic model
summary or self-reinforcing answer ingestion.

The MCP server uses newline-delimited UTF-8 JSON-RPC on stdio. It advertises
four bounded tools and emits no non-protocol stdout. Workspaces are pinned at
process startup. Tool arguments cannot select a different root or execute code.

The HTML viewer embeds escaped JSON, uses textContent for project-controlled
labels, and loads no third-party assets. Exports omit source bodies but still
contain private filenames and symbol names. Generated report Markdown should be
treated as untrusted project data when displayed in other applications.
