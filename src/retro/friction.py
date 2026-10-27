"""Find the moments in a session where the agent fell short.

A friction event is a pointer into a session plus a redacted excerpt. The detectors here
use only the transcript, so they are cheap and deterministic, and deliberately simple.
Each one has an obvious failure mode, noted beside it. The plan is to measure their
precision against a hand-labelled sample before trusting any counts built on them.

GitHub-side friction (review comments, failed checks) comes from `retro.sources.github`
and uses the same `Friction` type.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from retro.model import Event, EventKind, Session
from retro.redact import excerpt


class FrictionKind(StrEnum):
    CORRECTION = "correction"  # the human pushed back on what the agent did
    INTERRUPT = "interrupt"  # the human stopped the agent mid-turn
    DENIAL = "denial"  # a tool call was refused by the human or a permission classifier
    ERROR_STREAK = "error_streak"  # several tool calls in a row failed
    REVIEW_COMMENT = "review_comment"  # a reviewer asked for changes on the PR
    CHECK_FAILURE = "check_failure"  # CI failed on the PR


@dataclass(frozen=True)
class Friction:
    kind: FrictionKind
    session_id: str
    source: str
    event_index: int
    timestamp: datetime | None
    project: str | None
    skill: str | None
    evidence: str  # redacted excerpt, safe to print
    context: str = ""  # redacted excerpt of what the agent did just before, if relevant
    detail: str | None = None
    session_path: str | None = None  # local only; lets `retro case new` find the prompt
    ref: str | None = None  # stable name for friction not tied to one event, e.g. "pr12.review1"

    @property
    def id(self) -> str:
        return f"{self.session_id[:8]}:{self.ref or self.event_index}"

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["timestamp"] = self.timestamp.isoformat() if self.timestamp else None
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Friction:
        data = dict(data)
        data["kind"] = FrictionKind(data["kind"])
        ts = data.get("timestamp")
        data["timestamp"] = datetime.fromisoformat(ts) if ts else None
        return cls(**data)


# Pushback usually comes first in the message. False positives: "no worries", "wait for CI",
# "actually, also add X" (a follow-up, not a correction). False negatives: polite or
# indirect corrections ("could you use the existing helper instead?").
_CORRECTION = re.compile(
    r"""(?ix)^\W*(
        no\b(?!\s+(worries|problem|rush|need))
      | nope\b | wrong\b | stop\b | undo\b | revert\b
      | that'?s\s+(not|wrong|incorrect)
      | this\s+is\s+(not|wrong)
      | (you|u)\s+(didn'?t|did\s+not|forgot|missed|broke|ignored|shouldn'?t)
      | why\s+(did|would|are)\s+you
      | i\s+(said|asked|told\s+you|meant)
      | don'?t\b | do\s+not\b
    )"""
)
_CORRECTION_ANYWHERE = re.compile(
    r"(?i)\b(i told you|as i said|i already said|i asked you to|read the (skill|instructions))\b"
)
ERROR_STREAK_LENGTH = 3


_ANSWER = re.compile(r"(?i)^\W*(no|nope|yes|yeah|yep)\b")


def is_correction(text: str, previous_agent_text: str = "") -> bool:
    if _CORRECTION_ANYWHERE.search(text):
        return True
    # "no" straight after the agent asked a question is an answer, not pushback.
    if _ANSWER.search(text) and "?" in previous_agent_text[-300:]:
        return False
    return bool(_CORRECTION.search(text))


def detect(session: Session) -> list[Friction]:
    found: list[Friction] = []
    seen_agent_turn = False
    streak: list[Event] = []

    def emit(kind: FrictionKind, event: Event, evidence: str, **extra: Any) -> None:
        found.append(
            Friction(
                kind=kind,
                session_id=session.id,
                source=session.source,
                event_index=event.index,
                timestamp=event.timestamp,
                project=session.project,
                skill=session.skill_at(event.index),
                evidence=excerpt(evidence),
                context=excerpt(_previous_agent_text(session, event.index)),
                session_path=str(session.path),
                **extra,
            )
        )

    for event in session.events:
        if event.kind is EventKind.TOOL_RESULT:
            if event.is_error:
                streak.append(event)
                if len(streak) == ERROR_STREAK_LENGTH:
                    tools = sorted({e.tool or "?" for e in streak})
                    emit(
                        FrictionKind.ERROR_STREAK,
                        streak[0],
                        event.text,
                        detail=f"{ERROR_STREAK_LENGTH}+ failed calls: {', '.join(tools)}",
                    )
            else:
                streak = []
            continue

        if event.kind in (EventKind.ASSISTANT, EventKind.TOOL_CALL):
            seen_agent_turn = True
        elif event.kind is EventKind.PROMPT:
            # The first prompt sets the task. Only later prompts can push back.
            asked = _previous_agent_text(session, event.index, text_only=True)
            if seen_agent_turn and is_correction(event.text, asked):
                emit(FrictionKind.CORRECTION, event, event.text)
        elif event.kind is EventKind.INTERRUPT:
            emit(FrictionKind.INTERRUPT, event, event.text or "(interrupted)", detail=event.tool)
        elif event.kind is EventKind.DENIAL:
            emit(FrictionKind.DENIAL, event, event.text, detail=event.detail)
    return found


def _previous_agent_text(session: Session, index: int, *, text_only: bool = False) -> str:
    for event in reversed(session.events[:index]):
        if event.kind is EventKind.ASSISTANT:
            return event.text
        if event.kind is EventKind.TOOL_CALL and not text_only:
            return f"[{event.tool}] {_summarize_input(event.tool_input)}"
    return ""


def _summarize_input(tool_input: dict[str, Any] | None) -> str:
    if not tool_input:
        return ""
    for key in ("command", "cmd", "file_path", "path", "pattern", "url", "input"):
        if key in tool_input:
            return str(tool_input[key])
    return str(tool_input)
