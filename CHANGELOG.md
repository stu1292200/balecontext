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
- The saved file is smaller, because it is written into the model's context on every new
  question: the entry's own chain stays whole, a long call followed outside it that the
  question does not name is shown as an outline (signature, calls, branches, returns),
  helpers of followed calls are named in one line with the start of their docstring, and
  a definition cut short in the pack continues from where it stopped instead of repeating.
  Blank lines are dropped from all hook text (line numbers stay exact). The requests trace
  file drops from about 34,000 to 21,000 characters.
- The hook remembers, per Claude Code conversation, which source it already sent and
  names it on one `ALREADY SENT:` line instead of sending it again (an edited file is sent
  again). The installer also adds the hook for `PreCompact`, which clears that memory,
  because compaction summarises the evidence away. On a requests follow-up question the
  saved file drops from about 19,000 to 9,600 characters.
- A follow-up whose entry point was already sent in the conversation gets only new source:
  the entry's direct calls and the definitions the question names beyond the entry, with
  no saved file. The requests follow-up drops from about 9,000 characters plus a 9,600
  character file to 8,900 characters (`resolve_redirects` and `build_response` in full),
  and the Django one to 1,300 characters, since everything it asks about was already sent.
- Helpers the question does not name (the chain's own deep helpers and the helpers of calls
  followed elsewhere) come after every call it does name, so a question about redirects
  gets `resolve_redirects` before proxy-bypass internals.
- `benchmarks/claude_pair.py --followup-file`: a second question in the same conversation,
  with totals for both questions and for the second one alone. A resumed run reports its
  own calls' usage but the whole conversation's cost; each is checked against the run's
  own model calls, so the first question's cost is no longer counted twice.
- `--followup-file` takes several questions (one file each, or one file with questions
  separated by `---`) for a longer conversation, and reports each question's own tokens
  and cost. Four-question conversations for requests and Django are in `benchmarks/questions/`.
- `benchmarks/session_report.py` summarises the session files Claude Code saved for a
  paired run (record kinds and sizes, saved prompt settings, each call's cache numbers),
  to explain a resumed question that re-writes the cache instead of reading it.
- The hook builds its symbol table about four times faster (each file path is split
  once; one compiled pattern splits identifiers), so a Django prompt waits about 2.3 s
  instead of 4.1 s for the same evidence.
- Fewer extra steps after the hook's evidence. "Choose" and "pick" in a question also
  match code named `select` (`select_proxy`). An entry that calls no project code and
  is used in at most three places is shown with two of them, current code before code
  marked deprecated. The hook no longer reserves room in its pack for the map it drops. A helper up to half again the line limit is shown whole instead of cut
  a few lines short, and a saved file that would hold under 2,000 characters of source
  is not written: the pack names those lines instead. Across the 31 gold questions the
  hook writes 6 saved files instead of 10 (each one costs Claude a step to read), with
  relevance unchanged on all four projects.
- An abstract method (one that raises NotImplementedError) is followed to the methods
  that run in its place: those of subclasses of the most derived class on the shown path
  (`self.get_context()` reached from the errors mixin shows `ErrorList.get_context` and
  `ErrorDict.get_context`, not the form's), each found through its class's bases in order
  (`DjangoTemplates` gets `get_template` from `EngineMixin`). A class header that sets
  attributes the shown code reads (`template_name_ul`) comes before its method, and for a
  question about templates or rendering, the small template files named there follow,
  with the files they include (the `templates` copy before a `jinja2` one). Aliases
  right after a method (`__str__ = render`) are shown with it. "A field's errors" adds
  `BoundField.errors` beside `BaseForm.errors`. Source sent for an earlier question is
  no longer listed as missing. Django's error-rendering follow-up now carries everything
  its answer cited in 5,500 characters, with no saved file; the 31 gold questions and
  relevance are unchanged.
- `benchmarks/claude_pair.py` asks a conversation's questions in one Claude Code session,
  each once the previous answer is in, as people type them; `--resume-each` keeps the
  old way (a new `claude -p --resume` per question). Resuming after a one-step first
  answer made Claude Code re-write about 14,000 cached tokens on the next question in
  every Django hook run. A one-session run shows the same re-write, so it comes from
  Claude Code's own requests, not from resuming or from the hook's text.
- In a follow-up, a definition the question asks about beyond the entry is shown whole
  instead of stopping at 60 lines. The requests redirects follow-up now
  carries all of `resolve_redirects` (9,350 characters, no saved file), where before
  Claude read its last 34 lines in an extra step; the 31 gold questions, relevance and
  the Django conversation are unchanged.
- The project's own name in a question ("Django's URL resolver") no longer names a
  definition: the deepest folder holding at least 80% of the code (`django`, `requests`)
  is read as the project, so `OGRGeomType.django` no longer leads a URL-resolver pack.
- A plain word counts as a function's name only where a question puts one: right after
  "does" or "is" ("How does compose run middleware?") or before "function". "Django's
  template engine render the extends and block tags" now leads with `ExtendsNode.render`
  instead of `EngineHandler.templates`, `EngineMixin.engine` and `Message.tags`. The 31 gold
  questions and both paid-test conversations are unchanged.
- `benchmarks/claude_pair.py` writes `share.md` itself at the end of a test.
- `benchmarks/share_results.py` bundles one paired test (summary, per-question figures,
  per-call usage, tool calls and answers) into a single `share.md`.
- A call on `self` reaches a method inherited from a project base class (`self.render()` in
  an errors mixin finds `RenderableMixin.render`); the index rebuilds once (schema 9).
- When the question names a class, overrides in that class or its project bases are followed
  like any other call ("How does a ModelForm validate its instance" reaches
  `BaseModelForm._post_clean` and `construct_instance`). When the entry is a class header, the
  first function match gets the full call chain.
- In the hook's saved file, a long call two steps from the entry, or a medium one three or
  more steps away, that the question does not name is outlined (the requests trace keeps the
  redirect loop whole and outlines its auth and proxy helpers).
- A test or doc is never the entry point unless the question asks for tests or docs, even
  when it is named exactly like an identifier in the question (a test called `no_proxy`).
- After "the", "a" and similar words, a plain word is not taken as a function's name
  ("the Session", "the next request"), and generic words such as "next" and "same" are
  dropped there. A question that names an identifier gets the hook when the lead
  definition matches one of its words (`compose` in "How does compose run middleware and
  what happens if next() is called twice?").
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
