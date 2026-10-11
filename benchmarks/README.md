# Measuring the actual goal

The target is 90–95% fewer **total model tokens** with equal or better outcomes.
No result supporting that target has been measured for BaleContext yet.

Use the same agent version, model, reasoning settings, repository commit, task,
test environment, and starting conversation for the baseline and assisted runs.
Baseline: the agent's ordinary search/read workflow, without BaleContext.
Assisted: the same workflow with BaleContext available. Allow normal reads when
retrieval misses evidence. Include failed runs, retries, preprocessing, helper
models, and every model call. Report cold-start and repeated-use results separately.

Record provider-reported total tokens, with reasoning included wherever the
provider counts it. Cached input is still tokens; do not subtract it from total
tokens or confuse billing discounts with token reductions. If telemetry is
unavailable, report that limitation rather than inventing a savings percentage.

Create an external JSON file with this shape (numbers here are illustrative,
not BaleContext measurements):

```json
{
  "assisted_setup_model_tokens": 0,
  "assisted_setup_elapsed_seconds": 3,
  "tasks": [
    {
      "id": "fix-session-expiry",
      "baseline": {"passed": true, "elapsed_seconds": 100, "calls": [{"total_tokens": 10000}]},
      "assisted": {"passed": true, "elapsed_seconds": 80, "calls": [{"total_tokens": 8000}]}
    }
  ]
}
```

```sh
python benchmarks/compare_usage.py path/to/measurements.json
```

The script includes setup tokens in aggregate savings. It reports both success
counts and token reductions. Any task that passed in the baseline but failed
with BaleContext fails the basic quality gate, even when a different task
improves and the total pass counts tie. It lists both regressed and improved
task IDs. `no-task-regressions` means only that the recorded acceptance checks
did not regress; it does not prove equivalent or premium quality.

Timing is optional. Either omit all elapsed-time fields, or record
`elapsed_seconds` on both runs of every task and explicitly provide
`assisted_setup_elapsed_seconds`. Include indexing/setup time even though local
indexing uses no model tokens. Measure end-to-end wall-clock duration from
starting a run to its accepted result or failure, including tool execution and
retries. If the run finishes without an accepted result, retain it as a failed
run. Do not mix inference-time estimates with measured wall time. Aggregate
timings are sums of per-run durations plus setup, not the wall time of parallel
experiments. Token-only inputs report timing as `not-recorded`.

Also check regression tests, source citation correctness, and blinded review of
the patches/answers. Repeat runs to estimate variance. Publish the corpus, tasks,
model settings, logs with secrets removed, denominators and exclusions. See the
[first evaluation plan](first-evaluation.md) for the proposed task set and release
criteria. No real agent evaluation has been run yet.

`query --json` reports the size of retrieved text using UTF-8 bytes divided by
four. This is a rough context-size estimate. Comparing it with the entire corpus
does **not** measure end-to-end token savings, agent subscription allowances, or
answer quality. The JSON envelope and MCP tool metadata also consume tokens.
