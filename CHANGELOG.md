# Changelog

## Unreleased — ranking and evidence packs

- Codex prompt hook and paired test: `balecontext install codex --hook` adds
  `.codex/hooks.json`, which runs `balecontext hook codex` on each prompt. Codex (0.162 and
  later) reads the same hook input and output as Claude Code; the hook allows 16,000 tokens
  of context (Codex keeps about 2,500 by default and cuts the middle out of longer text), so
  the whole pack and its parts arrive at once. `benchmarks/codex_pair.py` asks Codex the same
  questions with and without the hook, with whole-task tokens from Codex's own counts, and
  writes `share.md` with local paths replaced. Steps: `docs/codex-test.md`.
- New logo (a sheaf: many stalks tied into one small bundle) in light and dark versions, a
  share image, and a plain-language README. Its quick start now includes `--hook`, which the
  measured savings need, and installs from GitHub because the v0.1.0 download has no hook.
- Pointer pack for tasks that change code: a prompt written as an instruction to fix, add,
  implement or rename (not a question, and not one that says "do not modify") gets about
  1.5 to 2.5k characters of pointers instead of source: the definitions an evidence pack would
  show as `path:lines name`, the constructor of a class it names, and the test and doc files
  naming what the prompt names, with line numbers. Claude reads the lines it edits in any case,
  so source sent up front was written to the cache for nothing (measured: 7 to 41% higher cost
  than plain Claude Code on four editing tasks). The hook log records `mode: pointers`.
- Bigger tasks for the paired test: `claude_pair.py --allow-edits` lets Claude edit files
  and saves each run's changes as a diff (also in `share.md`); `--setup-patch` plants a bug
  before every run and undoes it. `benchmarks/tasks/` holds two planted bugs and two
  features for Django and requests, with notes on what a correct result looks like, and
  `benchmarks/questions/django-long-followups.txt` makes an eight-question conversation.
- Ranking for task prompts: a hook that many classes define (`process_view`) leads with the
  place that runs it by name (`BaseHandler.load_middleware`) when the question does not say
  whose; a word that is a file's whole name and no symbol's (`listurls`) finds that file's
  code; a name written as the question writes it (`Session`, not `session()`) wins, and two
  spellings of one symbol pick it once; "the caller" as a noun is no longer a callers
  question; the project's own names (its folder and the name in package.json or
  pyproject.toml) are not search words unless a class carries one.
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
- Calls are followed through more of Python's own ways of reaching code (index schema 10):
  `from django.core.handlers import base` binds the submodule, so `WSGIHandler(base.BaseHandler)`
  inherits `get_response`; names a package re-exports (`from django.urls import set_urlconf`) reach
  the module that defines them; `utility = ManagementUtility(argv)` makes `utility.execute()`
  `ManagementUtility.execute`; `request_class = WSGIRequest` in a class body makes
  `self.request_class(environ)` build a `WSGIRequest`; and a call on an attribute a method stored
  (`self._middleware_chain(request)`) shows that method, labelled "sets self._middleware_chain, which
  BaseHandler.get_response calls at line 142". A method the object inherits counts as its own code
  when the walk orders calls. Django's request trace from `WSGIHandler.__call__` now reaches the
  middleware chain, `_get_response`, URL resolution and the view call (it stopped at `get_response`
  and claimed COMPLETE); the 31 gold questions, relevance and both paid-test conversations are
  unchanged or gain only correct calls.
- A class name the file's path does not already say counts like the method's own name when that
  name matches too, and a second method of the same name joins when the question names its class:
  "How does Django's template engine render the extends and block tags?" now leads with
  `ExtendsNode.render` and `BlockNode.render` instead of the parser's error helpers for bad block
  tags. Calls on an object of unknown type prefer the class named like it with underscores ignored
  (`block_context.pop` is `BlockContext.pop`; `self.__dict__` names no class), then the definition
  nearest the caller (`compiled_parent._render` in `django/template` is `Template._render`, not a form
  widget's). Calls leaving the entry's own chain are followed before those leaving another match, and
  what another match calls counts as its helpers: "from execute_from_command_line to the command's
  handle()" follows `run_from_argv` and `BaseCommand.execute` before migrate's internals (index
  schema 11). The 31 gold questions, relevance and both paid-test conversations are unchanged
  apart from order.
- A question asked in words shows where its object is built when one function in the project's
  code builds it: "render the extends and block tags" now includes `do_extends` and `do_block`,
  which parse each tag into its node, and "How is HTTP basic auth added to a request?" includes
  `PreparedRequest.prepare_auth`. A question that names its definition (`Response.json()`) gets
  no builder. Among calls the question does not name, calls into the entry's own module come
  first (`block_context.pop` beside `BlockNode.render`), and words sharing a long stem count as
  named ("URL resolution" names `URLResolver.resolve`). `benchmarks/relevance/django.json` gains
  three harder multi-file questions (request handling, management commands, extends and block
  tags); the hook shows every expected definition for all three. Of the 31 earlier gold questions
  only basic auth changes (+1,256 characters, still COMPLETE).
- The rest of a PARTIAL pack now reaches the model with the question, instead of in a saved
  file it reads in a second model call that re-reads the whole conversation. Claude Code keeps
  up to 10,000 characters from each hook and runs a prompt's hooks side by side, so
  `install claude --hook` registers the hook for `UserPromptSubmit` five times (`--part 2` to
  `--part 5` beside part 1; an earlier install gets the missing parts added). Part 1 builds the
  pack as before and hands what does not fit to the parts, which print one piece each, in order.
  The saved file is still written and named, for the model to read only if a part is missing
  (an older settings file). Leftovers that were too small for a saved file are sent as a part
  too, since a part costs no extra step. In a local Claude Code replay, the hard Django
  question's pack and three parts (38,000 characters) all reach the first model request in
  full. The hook also exits without the interpreter's teardown, a third of a second sooner.
- The evidence now covers what the answer check of the hard Django conversation found missing.
  Calls inside a function's own inner function count as its calls (`convert_exception_to_response`
  leads to `response_for_exception`, Express's `createApplication` to `app.handle`). The method that
  stores a chain on `self` (`load_middleware` setting `_middleware_chain`) is no longer a step of
  its own, so what the chain runs stays near the question. A method the question is about brings
  the short constructor that computes what it reads (`ExtendsNode.__init__` collects `self.blocks`)
  and the other methods of its class that read what it sets (`BlockNode.super` reads the `context`
  that `BlockNode.render` sets; Hono's `#handleError` reads the handler `onError` stores). "The
  middleware chain" brings `MiddlewareMixin.__call__`: the `__call__` of the one project base class,
  with three or more subclasses, that a question word begins, which runs when each object in the
  chain is called although no call names its class. These come after everything the chain calls.
  The hard Django conversation's evidence grows from 79,239 to 82,992 characters and still fits in
  the hook's five pieces; the requests and four-question Django conversations are unchanged. Of
  the 34 gold questions, Hono's error handling shows 2 of 3 expected definitions instead of 1, four
  other hook packs grow slightly, and none loses an expected definition.
- The answer checks of the newest paired tests found the hook's Django request answer missing
  part of the exception handling and the clean-up after the response; that evidence is now sent.
  In the hook's extra parts, an outline of a definition the larger pack cut short covers the whole
  definition, so `response_for_exception` shows every exception it maps instead of its first thirty
  lines. After the question's path, and ahead of the deep helpers, come: the one method of another
  class that reads what the entry sets on an object it holds (`HttpResponseBase.close`, which the
  server calls once the response is sent, reads the `_handler_class` that `WSGIHandler.__call__`
  sets); the functions connected to a project signal sent on the path (`reset_urlconf`,
  `close_old_connections` and `close_caches` run on `request_finished`); and an exception class
  raised on the path whose project base class the shown code catches (`Resolver404`, which
  `response_for_exception` turns into a 404 because it is an `Http404`). Each extra part is now
  filled before the next one starts, and evidence that would still need more than the four parts
  outlines its longest definitions the question does not name, instead of leaving it all to a file
  read in a second call. The hard Django conversation's evidence grows from 82,992 to 87,163
  characters and fits in the parts; the requests conversation grows by 652 characters and the
  four-question Django conversation is unchanged, as are the offline benchmark's results. The hook
  now refreshes the index once per prompt instead of once for each pack it builds, and definitions
  are indexed by name, so with the new checks the hard Django question's hook takes 3.1 to 3.8
  seconds, against 3.9 before.
- A question about who calls a definition, where it is used, or what would break if it changed
  ("Who calls get_resolver?", "Where is Session.send called from?", "What would break if I changed
  BaseHandler.get_response?") gets every place that names the definition, instead of the calls the
  definition makes. The definitions that call it are shown, whole when short and otherwise around
  each call, with what calls them in turn. Calls whose receiver type is not known, imports,
  overrides and other lines that name it follow one line each, then the tests, calls first. A call
  of the method a base class only declares counts (`self.send` in `SessionRedirectMixin` runs
  `Session.send`); `self.get_response` in a class of its own (each middleware, a test case) is that
  class's own attribute, so it is counted, not listed. The verdict counts what was found and says
  that a call made through another name (an alias, a callback, getattr) shows only where the name
  is written. A plain word names the definition where the question writes it as one ("who calls
  compose"), and in the hook a list cut short goes on in the extra parts. Django's `get_resolver`
  gets 11 callers and 21 call lines in an 8,827-character pack plus one 3,794-character part;
  requests' `merge_setting` gets its 3 callers and 8 call lines in 3,952 characters, where the trace
  pack it got before missed `merge_environment_settings` yet said COMPLETE. None of the 48 existing
  benchmark and conversation questions reads as a callers question. New paired-test conversations
  `requests-callers` and `django-callers` measure this kind of task.
- What the paired `django-callers` and hard Django runs found missing: a long caller shown around
  a call now also shows the lines soon after it that use the call's result (`response =
  self.get_response(request)`, then `response.close()`), which is what a change to the result would
  break; the sync or async counterpart of the method asked about (`get_response_async` beside
  `get_response`) is shown with the lines that name it; library code in a `test` package of the
  project (`django/test/client.py`, the test client) counts as code that calls the definition, not
  as a test line; and a short constructor that runs a method the pack shows (`WSGIHandler.__init__`
  calls `load_middleware`) is sent with the entry. Django's `get_response` question grows from
  3,813 to 5,680 characters, the hard Django conversation by 190 characters; the requests
  conversations and the offline benchmark are unchanged.
- The hook's time limit in Claude Code's settings is 120 seconds instead of 30, and the extra parts
  wait up to 110 seconds for the pack. Baleegh's session reports showed the hook cancelled at 30
  seconds on the first prompt of a hard Django run (Claude then searched with 8 tool calls, 102,745
  tokens against 35,354 in the run where the hook finished), and 8 to 25 seconds per prompt on his
  Windows PC against 2 to 5 here. `balecontext install --hook` raises the limit in an existing
  settings file. Each line of `.balecontext/hook-log.jsonl` now also records how long the hook took
  and how much of that was refreshing the index, so a slow machine can be understood from its log.
- A callers pack shows up to ten documentation lines that name the definition, one each
  (a release note can say what a change breaks for users); more are counted. Documents are
  read as prose, so a `*` bullet or `#` heading line is kept, and a docs chapter about testing
  (`docs/topics/testing/tools.txt`) counts as documentation, not as a test.
- `benchmarks/wire_log.py` records every request Claude Code sends to the model, with the
  usage the model reported, by standing in for ANTHROPIC_BASE_URL; `benchmarks/wire_report.py`
  shows where each request stops matching the one before it, as the prompt cache sees it.
- The hook ranks a prompt once: body matches, the ranking and each file's lines and docstring
  ranges are kept for the process, so packing the prompt at several budgets and building the
  extra parts no longer repeat them. The packs are the same; the hook takes about 40% less time
  (Django question 1 here: 2.9 s instead of 5.0 s, index refresh included).
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
