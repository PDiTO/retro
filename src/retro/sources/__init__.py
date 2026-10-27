"""Adapters from each agent's log format to `retro.model.Session`.

Every adapter is registered as a `Source`. `iter_sessions` yields parsed sessions from a
root directory, newest first, and `load` re-opens one session from the reference stored
in `Session.path`. For one-file-per-session formats the reference is the file path.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from retro.model import Session
from retro.sources import claude_code, codex


@dataclass(frozen=True)
class Source:
    name: str  # matches Session.source
    default_root: Path
    iter_sessions: Callable[[Path, datetime | None], Iterator[Session]]
    load: Callable[[Path], Session]


def _files(
    find: Callable[[Path, datetime | None], Iterator[Path]], parse: Callable[[Path], Session]
) -> Callable[[Path, datetime | None], Iterator[Session]]:
    def iterate(root: Path, since: datetime | None) -> Iterator[Session]:
        for path in find(root, since):
            yield parse(path)

    return iterate


SOURCES: list[Source] = [
    Source(
        "claude-code",
        claude_code.DEFAULT_ROOT,
        _files(claude_code.find_sessions, claude_code.parse_session),
        claude_code.parse_session,
    ),
    Source(
        "codex",
        codex.DEFAULT_ROOT,
        _files(codex.find_sessions, codex.parse_session),
        codex.parse_session,
    ),
]


def detected_sources() -> list[Source]:
    """Sources whose default root exists on this machine."""
    return [s for s in SOURCES if s.default_root.exists()]


def load_session(path: Path) -> Session:
    """Re-open a session from its reference, telling the formats apart by shape."""
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and "payload" in record and "type" in record:
                return codex.parse_session(path)
            if isinstance(record, dict) and "type" in record:
                break
    return claude_code.parse_session(path)


__all__ = [
    "SOURCES",
    "Source",
    "claude_code",
    "codex",
    "detected_sources",
    "load_session",
]
