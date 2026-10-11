# Contributing to BaleContext

Thank you for helping make agent context more useful and efficient.

Install from source with `python -m pip install ".[code]"` and run
`python -m unittest discover -s tests -v` before proposing a change.
Use Python 3.11-compatible syntax and APIs. Keep the core free of runtime network
calls and model dependencies. Optional parsers must degrade to clearly labeled
text coverage when absent.

For a bug report, include the version, OS, a minimal non-sensitive source
fixture, the command, expected behavior, and actual behavior. Do not attach a
real project's local SQLite index or graph export without reviewing it for
private information.

For new language parsers, test named and nested symbols, imports, aliases,
ambiguous references, syntax errors, and source-line accuracy. Prefer unresolved
links to confident-looking guesses. Keep uncertainty visible to the agent.

For token or quality improvements, provide a reproducible comparison following
`benchmarks/README.md`. Context reduction alone is not total model-token savings.
Include failures, retries, model settings and quality regressions.

Contributions are accepted under the project's MIT license. Do not submit code,
assets, or documentation you do not have permission to license accordingly.
