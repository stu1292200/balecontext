# Raw results of the paired Claude Code tests

Each file is the `share.md` that `benchmarks/claude_pair.py` wrote for one
test: the questions, the summary JSON, each question's own tokens and cost,
every model call and tool call for run 2, and both arms' full answers side by
side. The only change from the original files is that local folder paths were
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

Earlier runs (11 October and the first runs of 12 October) were recorded only
as console summaries; their numbers are in [docs/benchmarks.md](../../docs/benchmarks.md).
