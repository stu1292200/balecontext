"""Workspace boundaries, exclusions and deterministic text discovery."""
from __future__ import annotations

import fnmatch
import os
import re
import stat
import subprocess
import time
from pathlib import Path

EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".md", ".mdx", ".rst", ".txt", ".toml", ".json", ".yaml", ".yml",
    ".go", ".rs", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".rb",
    ".php", ".swift", ".kt", ".sh", ".ps1", ".sql", ".html", ".css",
    ".vue", ".svelte", ".graphql", ".proto",
}
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".balecontext", "node_modules", ".venv", "venv",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".next", "dist", "build",
    "coverage", ".aws", ".ssh", ".gnupg", ".idea", ".vscode", "vendor",
    ".codex", ".claude", ".gemini", ".cursor", ".opencode",
}
SPECIAL = {"Dockerfile", "Makefile", "LICENSE", "NOTICE", "AGENTS.md"}
SECRET_NAMES = {"credentials", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml", "id_rsa", "id_ed25519", ".npmrc", ".pypirc", ".mcp.json", "service-account.json"}
SECRET_CONTENT = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|\bgh[pousr]_[A-Za-z0-9]{30,}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{30,}\b"
    r"|\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}\b"
)
MAX_FILE_BYTES = 1_000_000


def safe_path(root: Path, relative: str) -> Path:
    """Refuse absolute paths, traversal and symlinks (including parent symlinks)."""
    part = Path(relative)
    if part.is_absolute() or part.drive or ".." in part.parts or not part.parts:
        raise ValueError("Provide a relative path inside the workspace.")
    candidate = root / part
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("Path escapes the workspace.")
    current = root
    for item in part.parts:
        current = current / item
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("Symlinks and directory junctions are excluded.")
    return candidate


def _sensitive(path: Path) -> bool:
    name = path.name.lower()
    return (
        name in SECRET_NAMES or name == ".env" or name.startswith(".env.")
        or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".keystore"}
        or name.endswith((".lock", "-lock.json", ".min.js", ".min.css", ".map"))
        or "secret" in name or "credential" in name
    )


def _custom_ignored(relative: str, patterns: list[str]) -> bool:
    # Deliberately simple positive globs; Git's own engine handles .gitignore.
    return any(
        fnmatch.fnmatchcase(relative, pattern)
        or fnmatch.fnmatchcase(relative, pattern.rstrip("/") + "/**")
        or ("/" not in pattern and any(fnmatch.fnmatchcase(p, pattern) for p in Path(relative).parts))
        for pattern in patterns
    )


# A stat match is trusted only when the file was read at least this long after its
# modification time, so edits within the same coarse filesystem tick are not missed.
RACY_NS = 2_000_000_000


def discover(root: Path) -> tuple[dict[str, str], dict[str, int]]:
    """Return eligible UTF-8 text; never follow directory links."""
    found, _, _, skipped = scan(root)
    return found, skipped


def scan(root: Path, known: dict[str, tuple[int, int, int]] | None = None):
    """Like discover, but skips reading files whose size and mtime match `known`.

    Returns (texts of files that were read, unchanged paths, {path: (size, mtime_ns, seen_ns)}, skipped).
    """
    known = known or {}
    if not root.is_dir():
        raise ValueError("Workspace must be an existing directory.")
    patterns: list[str] = []
    ignore = root / ".balecontextignore"
    if ignore.is_file() and not ignore.is_symlink():
        patterns = [s.strip() for s in ignore.read_text(encoding="utf-8").splitlines() if s.strip() and not s.lstrip().startswith("#")]
    candidates: list[str] = []
    skipped = {"excluded": 0, "unreadable": 0, "secret_content": 0}
    is_junction = getattr(os.path, "isjunction", lambda _: False)
    base = str(root)
    for directory, folders, names in os.walk(base, followlinks=False):
        folders[:] = sorted(d for d in folders if d not in SKIP_DIRS and not os.path.islink(os.path.join(directory, d)) and not is_junction(os.path.join(directory, d)))
        prefix = os.path.relpath(directory, base).replace(os.sep, "/")
        prefix = "" if prefix == "." else prefix + "/"
        for name in sorted(names):
            relative = prefix + name
            # String checks first; Path objects are costly across thousands of files.
            if os.path.islink(os.path.join(directory, name)) or _sensitive(Path(name)) or (patterns and _custom_ignored(relative, patterns)):
                skipped["excluded"] += 1
                continue
            if os.path.splitext(name)[1].lower() not in EXTENSIONS and name not in SPECIAL:
                continue
            candidates.append(relative)
    # Preserve nested .gitignore rules, negation and Git's matching semantics.
    if candidates:
        try:
            result = subprocess.run(
                ["git", "-C", str(root), "check-ignore", "--no-index", "-z", "--stdin"],
                input="\0".join(candidates) + "\0", capture_output=True, text=True,
                encoding="utf-8", timeout=15, check=False,
            )
            if result.returncode in (0, 1):
                ignored = set(result.stdout.split("\0"))
                skipped["excluded"] += sum(p in ignored for p in candidates)
                candidates = [p for p in candidates if p not in ignored]
        except (OSError, subprocess.TimeoutExpired):
            pass
    found: dict[str, str] = {}
    unchanged: set[str] = set()
    stats: dict[str, tuple[int, int, int]] = {}
    for relative in candidates:
        try:
            info = os.lstat(os.path.join(root, *relative.split("/")))
            if stat.S_ISLNK(info.st_mode):
                skipped["excluded"] += 1
                continue
            if info.st_size > MAX_FILE_BYTES:
                skipped["excluded"] += 1
                continue
            previous = known.get(relative)
            if previous and previous[0] == info.st_size and previous[1] == info.st_mtime_ns and info.st_mtime_ns + RACY_NS <= previous[2]:
                unchanged.add(relative)
                stats[relative] = previous
                continue
            seen = time.time_ns()
            path = safe_path(root, relative)
            data = path.read_bytes()
            if b"\0" in data:
                continue
            text = data.decode("utf-8-sig")
            if SECRET_CONTENT.search(text):
                skipped["secret_content"] += 1
                continue
            found[relative] = text
            stats[relative] = (info.st_size, info.st_mtime_ns, seen)
        except (OSError, UnicodeError, ValueError):
            skipped["unreadable"] += 1
    return found, unchanged, stats, skipped
