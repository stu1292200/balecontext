# Raw results of the paired Claude Code tests

Each file is the `share.md` that `benchmarks/claude_pair.py` wrote for one
test: the questions, the summary JSON, each question's own tokens and cost,
every model call and tool call for run 2, both arms' full answers side by side
and, for editing tasks, both arms' code changes. The only change from the original files is that local folder paths were
replaced with `<checkout>`.

| File | BaleContext version | Test |
|---|---|---|
| [20261012T115817Z.md](20261012T115817Z.md) | c09c8d1 | requests, 4 questions |
| [20261012T143438Z.md](20261012T143438Z.md) | 6542285 | Django, 4 questions |
| [20261012T153206Z.md](20261012T153206Z.md) | 962455c | Django, 4 questions, one session |
| [20261012T154024Z.md](20261012T154024Z.md) | 962455c | requests, 4 questions, one session |
| [20261012T190840Z.md](20261012T190840Z.md) | 9b1da7b | requests, 4 questions |
| [20261012T191349Z.md](20261012T191349Z.md) | 9b1da7b | Django, 3 hard multi-file questions |
| [20261012T205020Z.md](20261012T205020Z.md) | 8a5c61a | Django, 3 hard multi-file questions |
| [20261012T211518Z.md](20261012T211518Z.md) | 8a5c61a | Django, 3 "who calls / what breaks" questions |
| [20261012T222717Z.md](20261012T222717Z.md) | 93b4002 | Django, 3 hard multi-file questions |
| [20261012T225947Z.md](20261012T225947Z.md) | 1d33647 | Django, 3 "who calls" questions |
| [20261012T232728Z.md](20261012T232728Z.md) | 1d33647 | Django, 3 "who calls" questions, every request logged (1 run) |
| [20261013T114230Z.md](20261013T114230Z.md) | d543f80 | Django, fix the middleware-order bug (edits) |
| [20261013T114524Z.md](20261013T114524Z.md) | d543f80 | requests, fix the `merge_setting` bug (edits) |
| [20261013T114752Z.md](20261013T114752Z.md) | d543f80 | Django, add an option to `listurls` (edits) |
| [20261013T115024Z.md](20261013T115024Z.md) | d543f80 | requests, add `Session.timeout` (edits) |
| [20261013T115226Z.md](20261013T115226Z.md) | d543f80 | Django, 8-question conversation |
| [20261013T122703Z.md](20261013T122703Z.md) | ec5381a | Django, add an option to `listurls`, pointer pack (edits) |
| [20261013T123701Z.md](20261013T123701Z.md) | ec5381a | Django, fix the middleware-order bug, pointer pack (edits) |

Earlier runs (11 October and the first runs of 12 October) were recorded only
as console summaries; their numbers are in [docs/benchmarks.md](../../docs/benchmarks.md).
