"""Codex rollouts: one JSONL file per session under ~/.codex/sessions/YYYY/MM/DD/.

Each line is `{"timestamp", "type", "payload"}`. retro reads:

- `session_meta`: session id and working directory.
- `response_item` with payload type `message` (user and assistant text),
  `function_call` / `custom_tool_call` and their `*_output` results.
- `event_msg` with payload type `turn_aborted`, which is how an interrupt shows up.

Codex puts AGENTS.md and environment context into user-role messages. Those start with
`<` or `# AGENTS.md` and are not counted as prompts.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from retro.model import Event, EventKind, Session

DEFAULT_ROOT = Path.home() / ".codex" / "sessions"

_INJECTED_PREFIXES = ("<", "# AGENTS.md")
_EXIT_CODE = re.compile(r"(?i)\bexit(?:ed with)? code:?\s*(-?\d+)")


def find_sessions(root: Path = DEFAULT_ROOT, since: datetime | None = None) -> Iterator[Path]:
    files = [p for p in root.rglob("*.jsonl") if p.is_file()]
    if since is not None:
        cutoff = since.timestamp()
        files = [p for p in files if p.stat().st_mtime >= cutoff]
    yield from sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def parse_session(path: Path) -> Session:
    session = Session(id=path.stem, source="codex", path=path)
    tool_names: dict[str, str] = {}

    def add(kind: EventKind, **kwargs: Any) -> None:
        session.events.append(Event(kind=kind, index=len(session.events), **kwargs))

    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = record.get("payload") if isinstance(record, dict) else None
            if not isinstance(payload, dict):
                continue
            ts = _timestamp(record.get("timestamp"))
            rtype, ptype = record.get("type"), payload.get("type")

            if rtype == "session_meta":
                session.id = payload.get("id") or session.id
                session.cwd = payload.get("cwd") or session.cwd
                session.entrypoint = payload.get("originator") or session.entrypoint
            elif rtype == "event_msg" and ptype == "turn_aborted":
                add(EventKind.INTERRUPT, timestamp=ts, detail=payload.get("reason"))
            elif rtype != "response_item":
                continue
            elif ptype == "message":
                role = payload.get("role")
                for part in payload.get("content") or []:
                    text = (part.get("text") or "").strip() if isinstance(part, dict) else ""
                    if not text:
                        continue
                    if role == "user" and not text.startswith(_INJECTED_PREFIXES):
                        add(EventKind.PROMPT, timestamp=ts, text=text)
                    elif role == "assistant":
                        add(EventKind.ASSISTANT, timestamp=ts, text=text)
            elif ptype in ("function_call", "custom_tool_call"):
                call_id = payload.get("call_id")
                name = payload.get("name")
                tool_names[call_id or ""] = name or ""
                add(
                    EventKind.TOOL_CALL,
                    timestamp=ts,
                    tool=name,
                    tool_input=_arguments(payload.get("arguments", payload.get("input"))),
                    call_id=call_id,
                )
            elif ptype in ("function_call_output", "custom_tool_call_output"):
                call_id = payload.get("call_id")
                text = _flatten(payload.get("output"))
                add(
                    EventKind.TOOL_RESULT,
                    timestamp=ts,
                    text=text,
                    tool=tool_names.get(call_id or ""),
                    call_id=call_id,
                    is_error=_looks_failed(text),
                )
    return session


def _arguments(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"input": raw}
        return parsed if isinstance(parsed, dict) else {"input": parsed}
    return {}


def _flatten(output: object) -> str:
    if isinstance(output, str):
        try:
            nested = json.loads(output)
        except json.JSONDecodeError:
            return output
        if isinstance(nested, dict) and isinstance(nested.get("output"), str):
            return str(nested["output"])
        return output
    if isinstance(output, list):
        return "\n".join(p.get("text", "") for p in output if isinstance(p, dict)).strip()
    return ""


def _looks_failed(text: str) -> bool:
    match = _EXIT_CODE.search(text[:500])
    return bool(match and int(match.group(1)) != 0)


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
