"""What retro concludes about a set of sessions, in a form the report can draw.

`retro scan` produces `Friction` and `SessionStats`. `retro analyze` asks one or more
agents to label each friction event, group the labels into recurring patterns, pin each
pattern on a skill or instruction file, and propose a change. `retro climb` produces
`ClimbResult`s. All of it lands in one `Insights` value, saved as JSON in the data
directory, which is what `retro.viz` renders.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from retro.friction import Friction
from retro.model import EventKind, Session


class Category(StrEnum):
    """What kind of mistake a friction event was, as judged by an agent."""

    IGNORED_INSTRUCTION = "ignored_instruction"  # a skill or CLAUDE.md said otherwise
    SKIPPED_VERIFICATION = (
        "skipped_verification"  # didn't test, build or check before claiming done
    )
    WRONG_APPROACH = "wrong_approach"  # misunderstood the task or picked a bad design
    SCOPE_CREEP = "scope_creep"  # did more than asked, touched unrelated things
    RISKY_ACTION = "risky_action"  # destructive or outward-facing step without asking
    TOOL_MISUSE = "tool_misuse"  # wrong tool, bad arguments, repeated failing calls
    STYLE = "style"  # tone, formatting, naming, house style
    NOT_A_MISTAKE = "not_a_mistake"  # the detector fired but nothing went wrong


@dataclass(frozen=True)
class SessionStats:
    """Per-session numbers the charts need, without any session content."""

    id: str
    source: str  # "claude-code", "codex", "cursor", "opencode", ...
    project: str | None
    started_at: datetime | None
    prompts: int
    tool_calls: int
    skills: dict[str, int] = field(default_factory=dict)  # skill name -> agent turns using it
    events: int = 0  # total events, so charts can place friction along the session


@dataclass(frozen=True)
class Label:
    friction_id: str
    agent: str  # which agent produced this label, e.g. "claude" or "codex"
    category: Category
    confidence: float  # 0..1, the agent's own estimate
    summary: str  # one line, already redacted


@dataclass(frozen=True)
class Pattern:
    id: str  # short slug, e.g. "commits-before-tests"
    title: str
    description: str
    friction_ids: list[str]
    category: Category
    skill: str | None = None  # skill most responsible, if any
    target: str | None = None  # instruction file that should change, e.g. "~/.claude/CLAUDE.md"


@dataclass(frozen=True)
class Proposal:
    pattern_id: str
    target: str  # file to change
    change: str  # unified diff, or the exact text to add
    rationale: str
    check: dict[str, Any]  # a draft `[[checks]]` entry that would catch the pattern
    agent: str


@dataclass(frozen=True)
class ClimbStep:
    step: int
    accepted: bool
    train: float  # mean pass rate on train cases with the instructions going into this step
    val: float  # mean pass rate on val cases with the candidate
    reason: str


@dataclass(frozen=True)
class ClimbRun:
    target: str
    steps: list[ClimbStep]
    test_before: dict[str, float]  # case id -> pass rate, starting instructions
    test_after: dict[str, float]  # case id -> pass rate, final instructions
    grid: dict[str, dict[str, float]] = field(default_factory=dict)  # case id -> column -> rate
    val_before: float | None = None  # val pass rate of the starting instructions
    splits: dict[str, str] = field(default_factory=dict)  # case id -> "train", "val" or "test"


@dataclass
class Insights:
    generated_at: datetime
    window_days: int
    agents_detected: list[str]
    sessions: list[SessionStats]
    friction: list[Friction]
    labels: list[Label] = field(default_factory=list)
    patterns: list[Pattern] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    climbs: list[ClimbRun] = field(default_factory=list)
    synthetic: bool = False  # true for `retro demo` output; the report says so

    def to_json(self) -> dict[str, Any]:
        def encode(value: Any) -> Any:
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, StrEnum):
                return value.value
            if isinstance(value, dict):
                return {k: encode(v) for k, v in value.items()}
            if isinstance(value, list | tuple):
                return [encode(v) for v in value]
            return value

        data = encode(asdict(self))
        data["friction"] = [f.to_json() for f in self.friction]
        return data  # type: ignore[no-any-return]

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Insights:
        def ts(value: str | None) -> datetime | None:
            return datetime.fromisoformat(value) if value else None

        return cls(
            generated_at=datetime.fromisoformat(data["generated_at"]),
            window_days=data["window_days"],
            agents_detected=list(data["agents_detected"]),
            sessions=[
                SessionStats(**{**s, "started_at": ts(s.get("started_at"))})
                for s in data["sessions"]
            ],
            friction=[Friction.from_json(f) for f in data["friction"]],
            labels=[Label(**{**x, "category": Category(x["category"])}) for x in data["labels"]],
            patterns=[
                Pattern(**{**p, "category": Category(p["category"])}) for p in data["patterns"]
            ],
            proposals=[Proposal(**p) for p in data["proposals"]],
            climbs=[
                ClimbRun(**{**c, "steps": [ClimbStep(**s) for s in c["steps"]]})
                for c in data["climbs"]
            ],
            synthetic=bool(data.get("synthetic", False)),
        )

    def save(self, path: Path) -> Path:
        path.write_text(json.dumps(self.to_json(), indent=1), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> Insights:
        return cls.from_json(json.loads(path.read_text(encoding="utf-8")))


def session_stats(session: Session) -> SessionStats:
    """Counts only, nothing from the session's content."""
    skills: dict[str, int] = {}
    for event in session.events:
        if event.kind is EventKind.TOOL_CALL and event.skill:
            skills[event.skill] = skills.get(event.skill, 0) + 1
    return SessionStats(
        id=session.id,
        source=session.source,
        project=session.project,
        started_at=session.started_at,
        prompts=len(session.prompts),
        tool_calls=len(session.tool_calls),
        skills=skills,
        events=len(session.events),
    )
