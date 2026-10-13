# Bigger tasks for the paired test

Small questions are answered in one or two steps, so Claude Code's own setup (about
19,000 tokens per model call) caps what any evidence can save at about 70 to 80%.
These tasks take plain Claude Code ten or more steps: finding a planted bug, adding a
feature across several files, and a long conversation. They are run with
`benchmarks/claude_pair.py --allow-edits`, which lets Claude edit files (no shell),
saves each run's changes as `runN-<arm>.diff` next to the answer, and puts the checkout
back as it was. `--setup-patch` plants a bug before every run and removes it at the end.
The checkout must be clean in git (apart from BaleContext's own `.balecontext` folder),
or the script refuses to start.

Judge a run by its diff and its answer, not by the token count: a cheap wrong fix is a
failed run. What a correct result looks like is written below so anyone can check it.

## django-bug-process-view-order (Django, planted bug)

Prompt: `django-bug-process-view-order.txt`. Patch: `django-bug-process-view-order.patch`.

The patch changes `BaseHandler.load_middleware` in `django/core/handlers/base.py`
(around line 80) from `self._view_middleware.insert(0, mw_instance.process_view)` to
`self._view_middleware.append(...)`. Middleware is loaded from the last to the first in
`MIDDLEWARE`, so `insert(0, ...)` is what keeps `process_view` hooks in `MIDDLEWARE` order;
`append` reverses them, while `process_request` (none in modern Django), `process_response`
and `process_exception` keep their order.

Correct fix: restore `insert(0, ...)`. A good test goes in `tests/middleware_exceptions/`
(the existing `MiddlewareTests` there check hook order) and asserts that two middlewares'
`process_view` hooks run in `MIDDLEWARE` order. Cause and fix cited with file and line.

## requests-bug-none-setting (requests, planted bug)

Prompt: `requests-bug-none-setting.txt`. Patch: `requests-bug-none-setting.patch`.

The patch removes the loop at the end of `merge_setting` in `src/requests/sessions.py`
(around line 98) that deletes keys whose value is `None` from the merged setting. With it
gone, `s.get(url, headers={"Accept-Encoding": None})` no longer removes the session's
header; the same holds for `params` and `proxies`.

Correct fix: put the `None`-key removal back in `merge_setting` (not in each caller). The
existing test around `tests/test_requests.py:499` (`ses.headers["Accept-Encoding"] = None`)
covers part of it; a test that sets a request-level header to `None` and checks the sent
request has no such header completes it.

## django-feature-listurls-exclude (Django, feature)

Prompt: `django-feature-listurls-exclude.txt`. No patch.

Add `--exclude` / `-x` to the `listurls` management command
(`django/core/management/commands/listurls.py`): one or more route prefixes whose matching
patterns are dropped, applied after `--prefix`. A complete change also documents the option in
`docs/ref/django-admin.txt` under `listurls` (around line 518), adds a line to
`docs/releases/6.2.txt` under Minor features, and adds tests next to the existing `listurls`
tests in `tests/admin_scripts/tests.py` (around line 3365). Every file cited with lines.

## requests-feature-session-timeout (requests, feature)

Prompt: `requests-feature-session-timeout.txt`. No patch.

Add `Session.timeout` (default `None`) to `src/requests/sessions.py`: set in `__init__`,
listed in `__attrs__` so pickling keeps it, and used by `Session.request()` when the call
passes no timeout (a timeout given to the call still wins). Document it in
`docs/user/advanced.rst` (Session Objects, with a mention under Timeouts) and add tests in
`tests/test_requests.py` beside the other `Session` tests.

## Long conversation (Django, 8 questions)

`benchmarks/questions/django-request-handling.txt` followed by
`benchmarks/questions/django-long-followups.txt`: the request path, a management command,
template tags, callers of `get_resolver`, what breaks if `get_response` changes, where
`WSGIRequest` is used, `form.is_valid()`, and `Field.clean()`. Run without `--allow-edits`.

## Running them

From the balecontext folder, one run of each (both arms):

```
py benchmarks\claude_pair.py C:\path\to\django --question-file benchmarks\tasks\django-bug-process-view-order.txt --allow-edits --setup-patch benchmarks\tasks\django-bug-process-view-order.patch --runs 1
py benchmarks\claude_pair.py C:\path\to\requests --question-file benchmarks\tasks\requests-bug-none-setting.txt --allow-edits --setup-patch benchmarks\tasks\requests-bug-none-setting.patch --runs 1
py benchmarks\claude_pair.py C:\path\to\django --question-file benchmarks\tasks\django-feature-listurls-exclude.txt --allow-edits --runs 1
py benchmarks\claude_pair.py C:\path\to\requests --question-file benchmarks\tasks\requests-feature-session-timeout.txt --allow-edits --runs 1
py benchmarks\claude_pair.py C:\path\to\django --question-file benchmarks\questions\django-request-handling.txt --followup-file benchmarks\questions\django-long-followups.txt --runs 1
```

`share.md` in each results folder carries the diffs under `## Changes`. Report small
questions, bigger tasks and long conversations separately; their savings differ.
