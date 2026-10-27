"""Source-neutral view of an agent session.

Every source adapter (Claude Code, Codex) turns its own log format into a `Session`: an
ordered list of `Event`s plus the metadata needed to join it to git and GitHub. Everything
downstream, friction detection and case grading included, only sees these types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class EventKind(StrEnum):
    PROMPT = "prompt"  # something the human typed
    ASSISTANT = "assistant"  # text the agent showed the human
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    INTERRUPT = "interrupt"  # the human stopped the agent mid-turn
    DENIAL = "denial"  # a tool call was refused, by the human or a permission classifier


@dataclass(frozen=True)
class Event:
    kind: EventKind
    index: int
    timestamp: datetime | None = None
    text: str = ""
    tool: str | None = None
    tool_input: dict[str, Any] | None = None
    call_id: str | None = None
    is_error: bool = False
    skill: str | None = None  # skill the agent attributed this step to, if the source records it
    detail: str | None = None  # source-specific qualifier, e.g. the kind of denial


@dataclass
class Session:
    id: str
    source: str  # "claude-code" or "codex"
    path: Path
    cwd: str | None = None
    branch: str | None = None
    entrypoint: str | None = None
    events: list[Event] = field(default_factory=list)
    pull_requests: list[str] = field(default_factory=list)

    @property
    def started_at(self) -> datetime | None:
        return next((e.timestamp for e in self.events if e.timestamp), None)

    @property
    def ended_at(self) -> datetime | None:
        return next((e.timestamp for e in reversed(self.events) if e.timestamp), None)

    @property
    def prompts(self) -> list[Event]:
        return [e for e in self.events if e.kind is EventKind.PROMPT]

    @property
    def tool_calls(self) -> list[Event]:
        return [e for e in self.events if e.kind is EventKind.TOOL_CALL]

    @property
    def project(self) -> str | None:
        return Path(self.cwd).name if self.cwd else None

    def skill_at(self, index: int) -> str | None:
        """The skill the agent was using in the turn that led up to event `index`.

        Looks back to the previous prompt and no further, so a skill used three tasks ago
        doesn't get blamed for this one.
        """
        if 0 <= index < len(self.events) and self.events[index].skill:
            return self.events[index].skill
        for event in reversed(self.events[:index]):
            if event.skill:
                return event.skill
            if event.kind is EventKind.PROMPT:
                return None
        return None
