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

Create an external JSON file with this shape (numbers here are illustrative):

```json
{
  "assisted_setup_model_tokens": 0,
  "tasks": [
    {
      "id": "fix-session-expiry",
      "baseline": {"passed": true, "calls": [{"total_tokens": 10000}]},
      "assisted": {"passed": true, "calls": [{"total_tokens": 8000}]}
    }
  ]
}
```

```sh
python benchmarks/compare_usage.py path/to/measurements.json
```

The script includes setup tokens in aggregate savings. It reports both success
counts and token reductions; a lower success count fails its basic quality gate.
That gate is necessary but insufficient. Also check every previously passing
task, regression tests, source citation correctness, and blinded review of the
patches/answers. Repeat runs to estimate variance. Publish the corpus, tasks,
model settings, logs with secrets removed, denominators and exclusions.

`query --json` reports the size of retrieved text using UTF-8 bytes divided by
four. This is a rough context-size estimate. Comparing it with the entire corpus
does **not** measure end-to-end token savings, agent subscription allowances, or
answer quality. The JSON envelope and MCP tool metadata also consume tokens.
