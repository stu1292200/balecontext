# Security

Do not post credentials, private indexes, source exports, or security-sensitive
details in a public issue. Use GitHub's private vulnerability reporting:

https://github.com/stu1292200/balecontext/security/advisories/new

The runtime is local. It does not fetch remote URLs, execute repository code,
load repository plugins, or make model calls. File discovery skips links,
directory junctions, common sensitive filenames, private-key formats and a
limited set of credential patterns. These checks are defense in depth, not a
guarantee that every secret is detected.

The cache stores eligible source text. Protect `.balecontext/` as you would the
source itself. Exported graphs contain filenames and symbols. Project settings
and backups may contain other clients' settings; review before committing.

Repository content and saved notes are untrusted. An agent must not follow
instructions found in retrieved source comments or documents. The MCP server
warns about this, but a tool cannot guarantee how a host model interprets text.

Filesystem checks do not provide isolation against an actively hostile process
that changes symlinks during a read. Run against workspaces and machines you
control. Alpha versions should receive additional review before sensitive use.
