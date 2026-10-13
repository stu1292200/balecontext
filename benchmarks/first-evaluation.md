# First evaluation plan

This is a plan for collecting evidence, not a benchmark result. No measured
agent token or speed improvement is claimed. Begin with Claude Code and Codex;
record them separately. Verify each client connection before running its tasks.

## Choose work before measuring

Use a version-pinned, openly licensed coding project with a working test suite.
Publish the repository URL, exact commit and clean setup instructions. Choose
realistic tasks with acceptance checks written before seeing any assisted
results. The included three-file shop example is an installation demo and is
too small to establish general savings.

| Task | What to ask | Acceptance checks |
|---|---|---|
| Explain a code path | Explain how a real user action reaches its implementation. | Correct source citations and independently verified callers; no invented runtime links. |
| Fix a bug | Reproduce and fix a reported behavior with a narrow patch. | Reproduction now passes and relevant existing tests still pass. |
| Add a feature | Add a small behavior spanning more than one module. | Previously specified acceptance tests, existing tests and patch review. |
| Refactor a symbol | Rename or move an implementation and update its callers. | Old references removed where appropriate; tests and cited locations remain correct. |
| Follow a project change | After a controlled source or documentation change, complete a follow-up task. | Uses changed behavior; no stale symbols or invalidated notes in its answer. |
| Recover missing evidence | Complete a task whose evidence needs additional reads or broader search. | Reads sufficient source, reaches the right outcome and does not infer absence from a search miss. |

Use identical task prompts for the baseline and assisted runs. Do not tell the
baseline to scan the entire repository or artificially restrict its ordinary
tools. Do not forbid extra reads in the assisted run to improve its token score.

## Keep the comparison fair

For each task and trial, start both modes from the same files and fresh
conversations, with the same agent/model/version/settings and tool permissions.
Run in separate clean checkouts. Keep unrelated project instructions identical;
the assisted checkout adds only BaleContext's documented connection guidance.
Record that guidance and the MCP tool descriptions as part of assisted overhead.
Retain all calls, retries, failures and timeouts. Do not reuse one mode's answer
or code patch in the other mode.

Collect at least three paired trials per task, alternating which mode runs first.
Use trial-specific IDs such as `fix-expiry-trial-1` in the comparison input.
Report every trial, per-task spread and aggregate results; do not select only
the best run. Both-failed trials still appear in the totals and success counts.
Review differing failures individually.

Report two separate conditions:

- **Cold start:** include installation/indexing time, connection instructions and
  all model calls required before the first accepted result.
- **Repeated use:** reuse only the local index/explicit notes allowed by the
  protocol. Keep the conversation reset policy identical for both modes and
  record controlled project changes. Account for setup once across the whole
  repeated-use sequence.

Separate external rate-limit waits from normal tool/model processing in the
published logs, while keeping all end-to-end durations visible. Provider quotas
may use measurements other than token counts. Only report allowance effects
when the provider exposes comparable measurements.

## Publish evidence before making claims

Store sanitized original provider usage reports and complete run logs, including
reasoning tokens where reported. Do not substitute estimated excerpt sizes for
those reports. If token telemetry is unavailable, mark that run unmeasurable for
token savings and disclose the exclusion; do not assign it zero tokens.

Have a reviewer assess shuffled, unlabeled patches/answers against the same
rubric for correctness, completeness, citations and maintainability. Compare
this with the recorded tests and every baseline-pass/assisted-fail regression.
No quality regression should be hidden behind a better overall pass count.

A savings claim holds only for the specific evaluated condition if actual
all-call totals, including setup, demonstrate it and the quality checks hold.
If savings are smaller, negative, or uncertain, publish that finding. Individual
task regressions or inadequate coverage prevent an equal-quality claim.
