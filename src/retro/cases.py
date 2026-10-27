"""Regression cases: a past mistake, written down as checks that can be run again.

A case is a small TOML file. It names the friction it came from, the prompt that started
the task, and one or more checks that say what "the mistake didn't happen" means:

    id = "tests-before-commit"
    title = "Ran git commit without running the test suite first"
    friction = "0f554ae0:412"
    prompt = "Add the through guard to the up/down strategy"

    [[checks]]
    type = "require_tool"
    pattern = "cargo test"
    before = "git commit"

Most checks read only the transcript, so they can be graded on any session, old or new.
`command` checks need a workspace to run in, and `judge` checks need an LLM grader.
Graded without those, they come back as SKIP.

The rule that makes a case worth keeping: **it must fail on the session where the mistake
happened.** `admit` enforces that. A case that passes on the original transcript doesn't
capture the mistake, the same way a regression test has to be red before the fix. Once
admitted, a case can score any later session or replay, and a change to a skill or prompt
counts as a fix only if the case goes green.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from retro.model import Event, EventKind, Session

CHECK_TYPES = ("forbid_tool", "require_tool", "forbid_text", "max_errors", "command", "judge")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch")


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass(frozen=True)
class Check:
    type: str
    tool: str | None = None  # exact tool name; None means any tool
    pattern: str | None = None  # regex over the tool input or text
    before: str | None = None  # require_tool: must happen before a call matching this regex
    where: str = "all"  # forbid_text: "assistant", "writes" or "all"
    limit: int = 0  # max_errors
    run: str | None = None  # command: shell command, exit 0 means pass
    rubric: str | None = None  # judge: what a grader should look for
    description: str = ""

    def __post_init__(self) -> None:
        if self.type not in CHECK_TYPES:
            raise ValueError(f"unknown check type {self.type!r}, expected one of {CHECK_TYPES}")
        needs = {"command": self.run, "judge": self.rubric, "forbid_text": self.pattern}
        if self.type in needs and not needs[self.type]:
            raise ValueError(f"{self.type} check needs a value for its main field")
        if self.where not in ("assistant", "writes", "all"):
            raise ValueError(
                f"forbid_text where must be assistant, writes or all, not {self.where!r}"
            )

    @property
    def label(self) -> str:
        if self.description:
            return self.description
        parts = [self.type, self.tool or "", self.pattern or "", self.run or ""]
        if self.before:
            parts.append(f"before {self.before}")
        return " ".join(p for p in parts if p)


@dataclass
class Case:
    id: str
    title: str
    prompt: str
    checks: list[Check]
    friction: str | None = None
    repo: str | None = None
    base: str | None = None  # commit to replay from
    notes: str = ""
    path: Path | None = field(default=None, compare=False)

    @classmethod
    def load(cls, path: Path) -> Case:
        with path.open("rb") as f:
            data = tomllib.load(f)
        checks = [Check(**c) for c in data.pop("checks", [])]
        if not checks:
            raise ValueError(f"{path}: a case needs at least one [[checks]] entry")
        return cls(checks=checks, path=path, **data)


@dataclass(frozen=True)
class CheckResult:
    check: Check
    verdict: Verdict
    reason: str


Judge = Callable[[str, Session], tuple[bool, str]]


def grade(
    case: Case, session: Session, workdir: Path | None = None, judge: Judge | None = None
) -> list[CheckResult]:
    return [_grade_one(c, session, workdir, judge) for c in case.checks]


def passed(results: list[CheckResult]) -> bool:
    """A case passes when nothing failed and at least one check actually ran."""
    ran = [r for r in results if r.verdict is not Verdict.SKIP]
    return bool(ran) and all(r.verdict is Verdict.PASS for r in ran)


def admit(case: Case, original: Session) -> tuple[bool, list[CheckResult]]:
    """A case is admitted only if it catches the mistake in the session it came from."""
    results = grade(case, original)
    return any(r.verdict is Verdict.FAIL for r in results), results


def _grade_one(
    check: Check, session: Session, workdir: Path | None, judge: Judge | None
) -> CheckResult:
    calls = [e for e in session.tool_calls if check.tool is None or e.tool == check.tool]

    if check.type == "forbid_tool":
        for call in calls:
            if _matches(check.pattern, input_text(call)):
                return CheckResult(check, Verdict.FAIL, f"event {call.index}: {_short(call)}")
        return CheckResult(check, Verdict.PASS, "no matching call")

    if check.type == "require_tool":
        trigger = next(
            (
                e
                for e in session.tool_calls
                if check.before and _matches(check.before, input_text(e))
            ),
            None,
        )
        window = [c for c in calls if trigger is None or c.index < trigger.index]
        hit = next((c for c in window if _matches(check.pattern, input_text(c))), None)
        if hit:
            return CheckResult(check, Verdict.PASS, f"event {hit.index}: {_short(hit)}")
        if check.before and trigger is None:
            return CheckResult(check, Verdict.PASS, f"never ran anything matching {check.before!r}")
        where = f" before event {trigger.index}" if trigger else ""
        return CheckResult(check, Verdict.FAIL, f"no matching call{where}")

    if check.type == "forbid_text":
        assert check.pattern is not None
        for index, text in _texts(session, check.where):
            if re.search(check.pattern, text):
                return CheckResult(check, Verdict.FAIL, f"event {index} matches {check.pattern!r}")
        return CheckResult(check, Verdict.PASS, "no match")

    if check.type == "max_errors":
        errors = [e for e in session.events if e.kind is EventKind.TOOL_RESULT and e.is_error]
        verdict = Verdict.PASS if len(errors) <= check.limit else Verdict.FAIL
        return CheckResult(check, verdict, f"{len(errors)} failed tool calls, limit {check.limit}")

    if check.type == "command":
        if workdir is None:
            return CheckResult(check, Verdict.SKIP, "needs a replay workspace")
        assert check.run is not None
        proc = subprocess.run(check.run, shell=True, cwd=workdir, capture_output=True, check=False)
        verdict = Verdict.PASS if proc.returncode == 0 else Verdict.FAIL
        return CheckResult(check, verdict, f"exit {proc.returncode}")

    # judge
    if judge is None:
        return CheckResult(check, Verdict.SKIP, "no judge configured")
    assert check.rubric is not None
    ok, why = judge(check.rubric, session)
    return CheckResult(check, Verdict.PASS if ok else Verdict.FAIL, why)


def input_text(event: Event) -> str:
    """The part of a tool call's input a pattern should match: the command, else all of it."""
    data = event.tool_input or {}
    for key in ("command", "cmd"):
        value = data.get(key)
        if isinstance(value, list):
            return " ".join(str(v) for v in value)
        if isinstance(value, str):
            return value
    return json.dumps(data, ensure_ascii=False)


def written_text(event: Event) -> str:
    """Text a write-type tool call puts into files."""
    data: dict[str, Any] = event.tool_input or {}
    if event.tool == "apply_patch" or "*** Begin Patch" in str(data.get("input", "")):
        patch = str(data.get("input", ""))
        return "\n".join(line[1:] for line in patch.splitlines() if line.startswith("+"))
    chunks = [data.get("content"), data.get("new_string"), data.get("new_source")]
    chunks += [e.get("new_string") for e in data.get("edits") or [] if isinstance(e, dict)]
    return "\n".join(c for c in chunks if isinstance(c, str))


def _texts(session: Session, where: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for e in session.events:
        if where in ("assistant", "all") and e.kind is EventKind.ASSISTANT:
            out.append((e.index, e.text))
        if where in ("writes", "all") and e.kind is EventKind.TOOL_CALL and e.tool in WRITE_TOOLS:
            out.append((e.index, written_text(e)))
    return out


def _matches(pattern: str | None, text: str) -> bool:
    return pattern is None or re.search(pattern, text) is not None


def _short(event: Event) -> str:
    text = " ".join(input_text(event).split())
    return f"[{event.tool}] {text[:100]}"
