# Agent compatibility

Do not confuse valid configuration output or protocol tests with a verified live
agent integration. The status below is the honest v0.1.0 state.

| Client | Adapter | Automated checks | Live app test |
|---|---|---|---|
| Claude Code | Project `.mcp.json` + `CLAUDE.md` | JSON merge, backup, idempotence | Pending |
| Codex | Project `.codex/config.toml` + `AGENTS.md` | TOML parse, conflict refusal | Pending |
| Gemini CLI | Project `.gemini/settings.json` + `GEMINI.md` | Shared JSON adapter | Pending |
| Cursor | Project `.cursor/mcp.json` + `AGENTS.md` | Shared JSON adapter | Pending |
| Antigravity | Generic stdio MCP settings | Server protocol tested | Not verified |
| Hermes | Generic stdio MCP settings | Server protocol tested | Not verified |
| Perplexity Computer | Requires access to a local stdio process | No dedicated adapter | Not verified |
| Other MCP clients | `balecontext config generic` | Newline-delimited JSON-RPC tested | Client-specific |

Installation requires an installed Python environment containing BaleContext.
The config uses its exact interpreter path. The server supports MCP protocol
versions 2024-11-05, 2025-03-26, 2025-06-18 and 2025-11-25 for a tools-only stdio
subset. It does not provide HTTP transport, remote access, resources, sampling,
prompts, authentication, or agent orchestration.

To verify a client:

1. Install the package and run `balecontext doctor`.
2. Run the project installer or copy `config generic` into that client's supported settings.
3. Restart the client and complete its project trust/server approval flow.
4. Confirm it lists all five BaleContext tools.
5. Ask for a known symbol; verify the cited path and lines.
6. Modify the source, query again, and verify the updated excerpt.
7. Ask a question with no matching index evidence; confirm the agent expands its search.
8. Record the client version, OS and date before marking the integration verified.

Configurations can contain private local paths. Keep them and automatically
created backups out of public commits unless deliberately reviewed.
