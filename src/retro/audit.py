"""Check a directory for things that must never be published.

`retro audit` runs in CI and is meant to run before every commit. It flags:

- secrets, using the same rules as `retro.redact`
- personal home directories (`/home/<name>`, `/Users/<name>`) other than placeholder names
- agent transcripts: JSON lines shaped like Claude Code or Codex session records. The only
  exception is a file whose first line is the synthetic-fixture marker,
  `{"retro_fixture": "synthetic"}`, which the hand-written test fixtures carry.
- SQLite databases, by extension or by header. Cursor and OpenCode keep whole sessions in
  them, so test fixtures for those are built at test time instead.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from retro.redact import find_secrets

FIXTURE_MARKER = {"retro_fixture": "synthetic"}
PLACEHOLDER_USERS = {"dev", "user", "you", "me", "example", "runner", "name"}
_HOME = re.compile(r"(?<![\w./\-~])(?:/home|/Users)/([A-Za-z0-9._\-]+)")
_SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}
_MAX_BYTES = 5_000_000
_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
_SQLITE_MAGIC = b"SQLite format 3\x00"


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    rule: str


def audit(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in _files(root):
        try:
            if _is_sqlite(path):
                findings.append(Finding(path, 0, "sqlite-database"))
                continue
            if path.stat().st_size > _MAX_BYTES:
                findings.append(Finding(path, 0, "file-too-large-to-audit"))
                continue
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary, e.g. the logo
        findings.extend(_audit_text(path, text))
    return findings


def _is_sqlite(path: Path) -> bool:
    if path.suffix.lower() in _SQLITE_SUFFIXES:
        return True
    with path.open("rb") as f:
        return f.read(len(_SQLITE_MAGIC)) == _SQLITE_MAGIC


def _audit_text(path: Path, text: str) -> Iterator[Finding]:
    lines = text.splitlines()
    synthetic = bool(lines) and _json(lines[0]) == FIXTURE_MARKER
    for number, line in enumerate(lines, start=1):
        for secret in find_secrets(line):
            yield Finding(path, number, secret.rule)
        for m in _HOME.finditer(line):
            if m.group(1) not in PLACEHOLDER_USERS:
                yield Finding(path, number, "home-path")
        if not synthetic and _looks_like_transcript(line):
            yield Finding(path, number, "transcript-record")
            return  # one is enough; the whole file is the problem


def _looks_like_transcript(line: str) -> bool:
    record = _json(line)
    if not isinstance(record, dict):
        return False
    claude = "sessionId" in record and ("message" in record or "uuid" in record)
    codex = (
        record.get("type") in ("session_meta", "response_item", "event_msg") and "payload" in record
    )
    return claude or codex


def _json(line: str) -> object:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _files(root: Path) -> Iterator[Path]:
    """Tracked plus untracked-but-not-ignored files in a git repo, else every file."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        for path in sorted(root.rglob("*")):
            if path.is_file() and not _SKIP_DIRS.intersection(path.relative_to(root).parts):
                yield path
        return
    for name in sorted(filter(None, out.split("\0"))):
        path = root / name
        if path.is_file():
            yield path
