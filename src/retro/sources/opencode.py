"""OpenCode sessions: every session in one SQLite database, ~/.local/share/opencode/opencode.db.

retro reads three tables (and `workspace`, for the branch, when it exists):

- `session`: `id`, `directory` (the cwd), `parent_id` (set on subagent sessions, which are
  skipped) and `time_updated` in milliseconds.
- `message`: one row per turn, `data` is JSON with `role`, `time.created`, the model and,
  on an assistant message that failed, `error.name` (`MessageAbortedError` when the human
  stopped it).
- `part`: the pieces of each message in id order. `text` parts flagged `synthetic` or
  `ignored` were injected rather than typed. A `tool` part holds `callID`, `tool` and a
  `state` with `status`, `input`, `output` or `error`, and `time.start`/`time.end`.

A tool that was stopped ends in status `error` with `metadata.interrupted` or the error
"Tool execution aborted". A refused permission is also an `error`, with OpenCode's fixed
rejection text. A shell command that exits non-zero still completes, with
`metadata.exit` set. OpenCode also writes `session_message` and `event` tables. They mirror
the same conversation and are not read.

Many sessions share the database, so `Session.path` is `<db path>#<session id>`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from retro.model import Event, EventKind, Session

DEFAULT_ROOT = Path.home() / ".local" / "share" / "opencode"
DB_NAME = "opencode.db"

_ABORTED_TOOL = "Tool execution aborted"
_ABORTED_MESSAGE = "MessageAbortedError"
# Error text OpenCode returns to the model when a tool call is refused.
_DENIALS = (
    ("The user rejected permission", "user-rejected"),
    ("The user has specified a rule which prevents", "rule-denied"),
    ("The user dismissed this question", "user-dismissed"),
)


def connect(path: Path) -> sqlite3.Connection:
    """Open the database read-only. OpenCode may have it open, in WAL mode."""
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def database(root: Path) -> Path:
    """The database file, given either it or the directory holding it."""
    return root if root.suffix == ".db" else root / DB_NAME


def iter_sessions(root: Path = DEFAULT_ROOT, since: datetime | None = None) -> Iterator[Session]:
    """Top-level sessions, newest first. Subagent sessions have a parent and are skipped."""
    path = database(root)
    if not path.is_file():
        return
    cutoff = int(since.timestamp() * 1000) if since is not None else 0
    with closing(connect(path)) as db:
        ids = [
            row[0]
            for row in db.execute(
                "SELECT id FROM session WHERE parent_id IS NULL AND time_updated >= ?"
                " ORDER BY time_updated DESC",
                (cutoff,),
            )
        ]
        for session_id in ids:
            session = _read(db, path, session_id)
            if session is not None:
                yield session


def parse_session(ref: Path) -> Session:
    """Re-open one session from its `<db path>#<session id>` reference."""
    db_part, sep, session_id = str(ref).rpartition("#")
    if not sep or not session_id:
        raise ValueError(f"{ref}: expected <db path>#<session id>")
    path = Path(db_part)
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(connect(path)) as db:
        session = _read(db, path, session_id)
    if session is None:
        raise ValueError(f"{ref}: no such session")
    return session


def _read(db: sqlite3.Connection, path: Path, session_id: str) -> Session | None:
    row = db.execute("SELECT * FROM session WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        return None
    columns = row.keys()
    session = Session(
        id=session_id,
        source="opencode",
        path=Path(f"{path}#{session_id}"),
        cwd=row["directory"] or None,
        branch=_branch(db, row["workspace_id"] if "workspace_id" in columns else None),
    )

    def add(kind: EventKind, **kwargs: Any) -> None:
        session.events.append(Event(kind=kind, index=len(session.events), **kwargs))

    parts: dict[str, list[dict[str, Any]]] = {}
    for message_id, data in db.execute(
        "SELECT message_id, data FROM part WHERE session_id = ? ORDER BY id", (session_id,)
    ):
        part = _json(data)
        if part:
            parts.setdefault(message_id, []).append(part)

    for message_id, created, data in db.execute(
        "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created, id",
        (session_id,),
    ):
        message = _json(data)
        role = message.get("role")
        times = _dict(message.get("time"))
        ts = _ms(times.get("created")) or _ms(created)
        stopped = False

        for part in parts.get(message_id, []):
            ptype = part.get("type")
            if ptype == "text":
                text = str(part.get("text") or "").strip()
                if not text or part.get("synthetic") or part.get("ignored"):
                    continue
                when = _ms(_dict(part.get("time")).get("start")) or ts
                if role == "user":
                    add(EventKind.PROMPT, timestamp=when, text=text)
                elif role == "assistant":
                    add(EventKind.ASSISTANT, timestamp=when, text=text)
            elif ptype == "tool" and role == "assistant":
                stopped = _add_tool(add, part, ts) or stopped

        error = _dict(message.get("error"))
        if role == "assistant" and error.get("name") == _ABORTED_MESSAGE and not stopped:
            add(
                EventKind.INTERRUPT,
                timestamp=_ms(times.get("completed")) or ts,
                text=str(_dict(error.get("data")).get("message") or ""),
            )
    return session


def _add_tool(add: Any, part: dict[str, Any], ts: datetime | None) -> bool:
    """Add a tool part's call and its outcome. True if the human stopped it."""
    state = _dict(part.get("state"))
    times = _dict(state.get("time"))
    call_id = part.get("callID") if isinstance(part.get("callID"), str) else None
    tool = part.get("tool") if isinstance(part.get("tool"), str) else None
    tool_input = state.get("input")
    add(
        EventKind.TOOL_CALL,
        timestamp=_ms(times.get("start")) or ts,
        tool=tool,
        tool_input=tool_input if isinstance(tool_input, dict) else {"input": tool_input},
        call_id=call_id,
    )
    status = state.get("status")
    when = _ms(times.get("end")) or _ms(times.get("start")) or ts
    metadata = _dict(state.get("metadata"))
    if status == "completed":
        exit_code = metadata.get("exit")
        add(
            EventKind.TOOL_RESULT,
            timestamp=when,
            text=str(state.get("output") or ""),
            tool=tool,
            call_id=call_id,
            is_error=isinstance(exit_code, int) and exit_code != 0,
        )
    elif status == "error":
        error = str(state.get("error") or "")
        if metadata.get("interrupted") is True or error.startswith(_ABORTED_TOOL):
            add(EventKind.INTERRUPT, timestamp=when, tool=tool, call_id=call_id)
            return True
        denial = next((kind for prefix, kind in _DENIALS if error.startswith(prefix)), None)
        if denial:
            add(
                EventKind.DENIAL,
                timestamp=when,
                text=error,
                tool=tool,
                call_id=call_id,
                detail=denial,
            )
        else:
            add(
                EventKind.TOOL_RESULT,
                timestamp=when,
                text=error,
                tool=tool,
                call_id=call_id,
                is_error=True,
            )
    # "pending" and "running" have no outcome yet
    return False


def _branch(db: sqlite3.Connection, workspace_id: str | None) -> str | None:
    if not workspace_id:
        return None
    try:
        row = db.execute("SELECT branch FROM workspace WHERE id = ?", (workspace_id,)).fetchone()
    except sqlite3.OperationalError:
        return None  # older databases have no workspace table
    return row[0] if row and row[0] else None


def _json(data: object) -> dict[str, Any]:
    if not isinstance(data, str | bytes):
        return {}
    try:
        parsed = json.loads(data)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _ms(value: object) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    return None
