"""Claude Code transcripts: one JSONL file per session under ~/.claude/projects/<project>/.

Each line is a record with a top-level `type`. The ones retro reads:

- `user`: either a typed prompt (string content, or a list with `text` parts) or tool
  results (a list with `tool_result` parts). Denied tool calls carry `toolDenialKind`.
- `assistant`: one content block per record (`text`, `tool_use` or `thinking`), with
  `attributionSkill` naming the skill in play, when there is one.
- `pr-link`: a PR the session opened or pushed to.

Everything else (API request blobs, titles, cost snapshots, queue operations) is skipped.
Subagent records (`isSidechain`) are skipped too: friction is about the main conversation.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from retro.model import Event, EventKind, Session

DEFAULT_ROOT = Path.home() / ".claude" / "projects"

# User-role text that the harness injected rather than the human typed.
_INJECTED_PREFIXES = ("<", "Caveat:", "This session is being continued")
_INTERRUPT_PREFIX = "[Request interrupted"
# Cheap pre-filter: these record types are large and never needed.
_SKIP_TYPES = ('"type":"api-request', '"type":"attachment"', '"type":"atis-latch"')


def find_sessions(root: Path = DEFAULT_ROOT, since: datetime | None = None) -> Iterator[Path]:
    """Top-level session files, newest first. Subagent transcripts live deeper and are skipped."""
    files = [p for p in root.glob("*/*.jsonl") if p.is_file()]
    if since is not None:
        cutoff = since.timestamp()
        files = [p for p in files if p.stat().st_mtime >= cutoff]
    yield from sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def parse_session(path: Path) -> Session:
    session = Session(id=path.stem, source="claude-code", path=path)
    tool_names: dict[str, str] = {}

    def add(kind: EventKind, **kwargs: Any) -> None:
        session.events.append(Event(kind=kind, index=len(session.events), **kwargs))

    with path.open(encoding="utf-8") as f:
        for line in f:
            if any(marker in line[:300] for marker in _SKIP_TYPES):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # a session still being written can end in a partial line
            if not isinstance(record, dict) or record.get("isSidechain"):
                continue

            kind = record.get("type")
            if kind == "pr-link" and isinstance(record.get("prUrl"), str):
                if record["prUrl"] not in session.pull_requests:
                    session.pull_requests.append(record["prUrl"])
                continue
            if kind not in ("user", "assistant"):
                continue

            session.id = record.get("sessionId") or session.id
            session.cwd = session.cwd or record.get("cwd")
            session.branch = session.branch or record.get("gitBranch")
            session.entrypoint = session.entrypoint or record.get("entrypoint")
            ts = _timestamp(record.get("timestamp"))
            message = record.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")

            if kind == "assistant":
                skill = record.get("attributionSkill") or None
                for part in content if isinstance(content, list) else []:
                    if part.get("type") == "text" and part.get("text", "").strip():
                        add(EventKind.ASSISTANT, timestamp=ts, text=part["text"], skill=skill)
                    elif part.get("type") == "tool_use":
                        tool_names[part.get("id", "")] = part.get("name", "")
                        add(
                            EventKind.TOOL_CALL,
                            timestamp=ts,
                            tool=part.get("name"),
                            tool_input=part.get("input") or {},
                            call_id=part.get("id"),
                            skill=skill,
                        )
                continue

            # user record
            if record.get("isMeta"):
                continue
            denial = record.get("toolDenialKind")
            if denial is None and _rejected(record.get("permissionDecision")):
                denial = "rejected"
            if isinstance(content, str):
                _add_user_text(add, content, ts)
                continue
            for part in content if isinstance(content, list) else []:
                if part.get("type") == "text":
                    _add_user_text(add, part.get("text", ""), ts)
                elif part.get("type") == "tool_result":
                    call_id = part.get("tool_use_id")
                    text = _flatten(part.get("content"))
                    tool = tool_names.get(call_id or "")
                    if denial == "interrupted":
                        add(EventKind.INTERRUPT, timestamp=ts, tool=tool, call_id=call_id)
                    elif denial:
                        add(
                            EventKind.DENIAL,
                            timestamp=ts,
                            text=text,
                            tool=tool,
                            call_id=call_id,
                            detail=str(denial),
                        )
                    else:
                        add(
                            EventKind.TOOL_RESULT,
                            timestamp=ts,
                            text=text,
                            tool=tool,
                            call_id=call_id,
                            is_error=bool(part.get("is_error")),
                        )
    return session


def _add_user_text(add: Any, text: str, ts: datetime | None) -> None:
    stripped = text.strip()
    if not stripped:
        return
    if stripped.startswith(_INTERRUPT_PREFIX):
        add(EventKind.INTERRUPT, timestamp=ts, text=stripped)
    elif not stripped.startswith(_INJECTED_PREFIXES):
        add(EventKind.PROMPT, timestamp=ts, text=stripped)


def _rejected(decision: object) -> bool:
    return isinstance(decision, dict) and decision.get("decision") == "reject"


def _flatten(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict)).strip()
    return ""


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
