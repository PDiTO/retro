"""Cursor CLI (cursor-agent) chats: one directory per chat under ~/.cursor/chats/<workspace>/.

Each chat directory holds `meta.json` (`cwd`, `createdAtMs`, `updatedAtMs`) and `store.db`,
a content-addressed SQLite store:

- `meta`: one row, key `"0"`, whose value is hex-encoded JSON with `agentId` (the chat id)
  and `latestRootBlobId`.
- `blobs(id, data)`: `id` is the SHA-256 of `data`. The root blob is protobuf. Its field 1
  repeats the ids of the conversation's messages, in order, and field 21 is the workspace
  (`1`: path, `2`: git branch). Every message blob is JSON in the Vercel AI SDK shape,
  `{"role", "content", "providerOptions"}`.

retro reads `user` messages (the typed text is wrapped in `<user_query>`; messages that
are only `<user_info>`, `<system_notification>` or a `<conversation_summary>` were
injected), `assistant` text and `tool-call` parts, and `tool` messages. A tool message's
outcome is in `providerOptions.cursor.highLevelToolCallResult`: `isError` plus an `output`
keyed by its case, one of `success`, `error`, `failure` (a non-zero exit), `rejected` (the
human declined the call) or `permissionDenied` (blocked by policy).

Cursor records no interrupt. A tool call still without a result when the next prompt
arrives is taken to mean the human stopped the agent. Messages carry no timestamps either:
prompts get the time from their `<timestamp>` tag (minute precision) and the chat's last
event gets `updatedAtMs`.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from retro.model import Event, EventKind, Session

DEFAULT_ROOT = Path.home() / ".cursor" / "chats"

_ROOT_MESSAGE = 1  # root blob field: id of one message, repeated in conversation order
_ROOT_WORKSPACE = 21  # root blob field: {1: path, 2: branch}
_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)
# e.g. "Fri, Oct 2, 2026, 3:04 PM (UTC+2)". Month names may be spelled out, and JavaScript
# puts U+202F before "PM", which `\s` matches.
_TIMESTAMP = re.compile(
    r"<timestamp>\s*(?:\w+,\s*)?([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4}),?\s*"
    r"(\d{1,2}):(\d{2})\s*([AP]M)(?:\s*\(UTC(?:([+-])(\d{1,2})(?::?(\d{2}))?)?\))?"
)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_DENIALS = {"rejected": "user-rejected", "permissionDenied": "permission-denied"}
_ERRORS = {"error", "failure"}
_BATCH = 500  # SQLite caps the number of bound parameters per statement


def find_sessions(root: Path = DEFAULT_ROOT, since: datetime | None = None) -> Iterator[Path]:
    """Each chat's store.db, newest first. Chats that never got a message have no store."""
    stores = [(p, _mtime(p)) for p in root.glob("*/*/store.db") if p.is_file()]
    if since is not None:
        cutoff = since.timestamp()
        stores = [(p, m) for p, m in stores if m >= cutoff]
    yield from (p for p, _ in sorted(stores, key=lambda s: s[1], reverse=True))


def iter_sessions(root: Path = DEFAULT_ROOT, since: datetime | None = None) -> Iterator[Session]:
    for path in find_sessions(root, since):
        try:
            yield parse_session(path)
        except (ValueError, sqlite3.DatabaseError):
            continue  # a store mid-write or from a format version this doesn't know


def parse_session(path: Path) -> Session:
    chat = path.parent
    info = _read_json(chat / "meta.json")
    session = Session(id=chat.name, source="cursor", path=path, cwd=_str(info.get("cwd")))
    tool_names: dict[str, str] = {}
    unanswered: list[str] = []  # call ids still waiting for a result, in call order

    def add(kind: EventKind, **kwargs: Any) -> None:
        session.events.append(Event(kind=kind, index=len(session.events), **kwargs))

    with closing(connect(path)) as db:
        meta = _meta(db)
        session.id = _str(meta.get("agentId")) or session.id
        root_id = str(meta.get("latestRootBlobId", ""))
        root = _blobs(db, [root_id]).get(root_id)
        if root is None:
            raise ValueError(f"{path}: root blob missing")
        ids: list[str] = []
        for number, value in _fields(root):
            if number == _ROOT_MESSAGE and isinstance(value, bytes):
                ids.append(value.hex())
            elif number == _ROOT_WORKSPACE and isinstance(value, bytes):
                workspace = {n: v for n, v in _fields(value) if isinstance(v, bytes)}
                session.cwd = session.cwd or _decode(workspace.get(1))
                session.branch = _decode(workspace.get(2))
        blobs = _blobs(db, ids)

    for blob_id in ids:
        message = _json(blobs.get(blob_id))
        if not isinstance(message, dict):
            continue
        role, content = message.get("role"), message.get("content")
        options = _dict(_dict(message.get("providerOptions")).get("cursor"))

        if role == "user":
            if options.get("isSummary"):
                continue
            texts = [content] if isinstance(content, str) else _texts(content)
            for text in texts:
                ts = _prompt_time(text)
                for prompt in _typed(text):
                    if unanswered:
                        stopped = unanswered[0]
                        tool = tool_names.get(stopped)
                        add(EventKind.INTERRUPT, timestamp=ts, tool=tool, call_id=stopped)
                        unanswered.clear()
                    add(EventKind.PROMPT, timestamp=ts, text=prompt)
        elif role == "assistant":
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and str(part.get("text", "")).strip():
                    add(EventKind.ASSISTANT, text=part["text"])
                elif part.get("type") == "tool-call":
                    call_id = _str(part.get("toolCallId"))
                    name = _str(part.get("toolName"))
                    args = part.get("args")
                    tool_names[call_id or ""] = name or ""
                    unanswered.append(call_id or "")
                    add(
                        EventKind.TOOL_CALL,
                        tool=name,
                        tool_input=args if isinstance(args, dict) else {"input": args},
                        call_id=call_id,
                    )
        elif role == "tool":
            outcome = _dict(options.get("highLevelToolCallResult"))
            output = _dict(outcome.get("output"))
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict) or part.get("type") != "tool-result":
                    continue
                call_id = _str(part.get("toolCallId"))
                if call_id in unanswered:
                    unanswered.remove(call_id)
                tool = _str(part.get("toolName")) or tool_names.get(call_id or "")
                case = next((c for c in _DENIALS if c in output), None)
                if case:
                    add(
                        EventKind.DENIAL,
                        text=_reason(output[case]) or _result_text(part),
                        tool=tool,
                        call_id=call_id,
                        detail=_DENIALS[case],
                    )
                    continue
                add(
                    EventKind.TOOL_RESULT,
                    text=_result_text(part),
                    tool=tool,
                    call_id=call_id,
                    is_error=bool(outcome.get("isError")) or not _ERRORS.isdisjoint(output),
                )

    _stamp_ends(session, _ms(info.get("createdAtMs")), _ms(info.get("updatedAtMs")))
    return session


def connect(path: Path) -> sqlite3.Connection:
    """Open a SQLite file read-only. The agent may have it open, in WAL mode."""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _meta(db: sqlite3.Connection) -> dict[str, Any]:
    row = db.execute("SELECT value FROM meta WHERE key = '0'").fetchone()
    if row is None:
        raise ValueError("store has no meta row")
    try:
        meta = json.loads(bytes.fromhex(row[0]))
    except (ValueError, TypeError) as e:
        raise ValueError(f"unreadable meta: {e}") from e
    if not isinstance(meta, dict):
        raise ValueError("meta is not an object")
    return meta


def _blobs(db: sqlite3.Connection, ids: list[str]) -> dict[str, bytes]:
    """Fetch the named blobs only. The store also keeps file contents we never need."""
    found: dict[str, bytes] = {}
    unique = list(dict.fromkeys(ids))
    for start in range(0, len(unique), _BATCH):
        chunk = unique[start : start + _BATCH]
        marks = ",".join("?" * len(chunk))
        for blob_id, data in db.execute(f"SELECT id, data FROM blobs WHERE id IN ({marks})", chunk):
            found[blob_id] = bytes(data)
    return found


def _fields(data: bytes) -> Iterator[tuple[int, int | bytes]]:
    """Top-level fields of a protobuf message, without a schema: (field number, value)."""
    i = 0
    while i < len(data):
        key, i = _varint(data, i)
        number, wire = key >> 3, key & 7
        value: int | bytes
        if wire == 0:
            value, i = _varint(data, i)
        elif wire == 2:
            size, i = _varint(data, i)
            value, i = data[i : i + size], i + size
        elif wire == 1:
            value, i = data[i : i + 8], i + 8
        elif wire == 5:
            value, i = data[i : i + 4], i + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        if i > len(data):
            raise ValueError("truncated protobuf message")
        yield number, value


def _varint(data: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while i < len(data):
        byte = data[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7
    raise ValueError("truncated protobuf varint")


def _typed(text: str) -> list[str]:
    """What the human typed in one user message, without the context Cursor wraps it in."""
    queries = [q for q in _QUERY.findall(text) if q.strip()]
    if queries or text.lstrip().startswith("<"):
        return queries
    return [text.strip()] if text.strip() else []


def _prompt_time(text: str) -> datetime | None:
    m = _TIMESTAMP.search(text)
    if not m or m.group(1).lower() not in _MONTHS:
        return None
    month, day, year, hour, minute, meridiem, sign, off_h, off_m = m.groups()
    offset = timedelta(hours=int(off_h or 0), minutes=int(off_m or 0))
    try:
        return datetime(
            int(year),
            _MONTHS.index(month.lower()) + 1,
            int(day),
            int(hour) % 12 + (12 if meridiem == "PM" else 0),
            int(minute),
            tzinfo=timezone(-offset if sign == "-" else offset),
        )
    except ValueError:
        return None


def _stamp_ends(session: Session, created: datetime | None, updated: datetime | None) -> None:
    events = session.events
    if events and created and events[0].timestamp is None:
        events[0] = replace(events[0], timestamp=created)
    if len(events) > 1 and updated and events[-1].timestamp is None:
        events[-1] = replace(events[-1], timestamp=updated)


def _result_text(part: dict[str, Any]) -> str:
    result = part.get("result")
    if isinstance(result, str):
        return result
    return "\n".join(_texts(part.get("experimental_content"))).strip()


def _reason(value: object) -> str:
    if isinstance(value, dict):
        for key in ("reason", "clientVisibleError", "error"):
            if isinstance(value.get(key), str) and value[key]:
                return str(value[key])
    return ""


def _texts(content: object) -> list[str]:
    if not isinstance(content, list):
        return []
    return [
        str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text"
    ]


def _mtime(store: Path) -> float:
    """Last write to the chat: the WAL and meta.json change before store.db is checkpointed."""
    paths = (store, store.with_name("store.db-wal"), store.with_name("meta.json"))
    return max(p.stat().st_mtime for p in paths if p.exists())


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _json(data: bytes | None) -> object:
    if not data or data[:1] != b"{":
        return None
    try:
        return json.loads(data)
    except ValueError:
        return None


def _decode(value: bytes | None) -> str | None:
    if not value:
        return None
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _ms(value: object) -> datetime | None:
    if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    return None
