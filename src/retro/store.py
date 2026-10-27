"""Where retro keeps what it learns about your sessions.

Everything derived from real transcripts (friction, cases, GitHub cache) goes in one
directory: `$RETRO_HOME`, else `$XDG_DATA_HOME/retro`, else `~/.local/share/retro`.
retro refuses to use a directory inside a git work tree, so private data can't end up
staged next to code by accident.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

from retro.friction import Friction


class UnsafeDataDir(RuntimeError):
    pass


def data_dir() -> Path:
    if env := os.environ.get("RETRO_HOME"):
        path = Path(env).expanduser()
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        path = base / "retro"
    ensure_private(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_private(path: Path) -> None:
    for parent in [path.resolve(), *path.resolve().parents]:
        if (parent / ".git").exists():
            raise UnsafeDataDir(
                f"{path} is inside the git work tree at {parent}. retro stores excerpts of "
                "private transcripts there; set RETRO_HOME to a directory outside any repo."
            )


def friction_path(root: Path) -> Path:
    return root / "friction.jsonl"


def save_friction(root: Path, items: Iterable[Friction]) -> Path:
    path = friction_path(root)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item.to_json()) + "\n")
    return path


def load_friction(root: Path) -> list[Friction]:
    path = friction_path(root)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [Friction.from_json(json.loads(line)) for line in f if line.strip()]


def insights_path(root: Path) -> Path:
    return root / "insights.json"
