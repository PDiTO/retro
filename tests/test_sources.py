from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from retro.model import EventKind, Session
from retro.sources import claude_code, codex, load_session
from tests.conftest import FIXTURES


def kinds(session: Session) -> list[str]:
    return [e.kind.value for e in session.events]


def test_claude_metadata(mistake: Session) -> None:
    assert mistake.id == "5e55i0n0-0000-4000-8000-000000000001"
    assert mistake.source == "claude-code"
    assert mistake.cwd == "/home/dev/src/widget"
    assert mistake.project == "widget"
    assert mistake.branch == "feat/verbose"
    assert mistake.entrypoint == "cli"
    assert mistake.pull_requests == ["https://github.com/example/widget/pull/7"]
    assert mistake.started_at is not None
    assert mistake.ended_at is not None
    assert mistake.started_at < mistake.ended_at


def test_claude_events(mistake: Session) -> None:
    assert kinds(mistake) == [
        "prompt",
        "assistant",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "assistant",
        "prompt",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "tool_call",
        "denial",
        "interrupt",
    ]
    assert [e.index for e in mistake.events] == list(range(len(mistake.events)))


def test_claude_skips_injected_meta_and_sidechain(mistake: Session) -> None:
    texts = [e.text for e in mistake.prompts]
    assert texts == [
        "Add a --verbose flag to the widget CLI and commit it",
        "no, you didn't run the tests before committing",
    ]


def test_claude_tool_results_carry_tool_name_and_error(mistake: Session) -> None:
    results = [e for e in mistake.events if e.kind is EventKind.TOOL_RESULT]
    assert {e.tool for e in results} == {"Edit", "Bash"}
    assert [e.is_error for e in results] == [False, False, True, True, True]


def test_claude_denial(mistake: Session) -> None:
    denial = next(e for e in mistake.events if e.kind is EventKind.DENIAL)
    assert denial.tool == "Bash"
    assert denial.detail == "user-rejected"


def test_claude_skill_attribution(mistake: Session) -> None:
    commit = mistake.tool_calls[1]
    assert commit.skill == "git-stage"
    assert mistake.skill_at(commit.index) == "git-stage"
    assert mistake.skill_at(0) is None
    # The skill used before a prompt doesn't carry over into the next turn.
    first_test_run = mistake.tool_calls[2]
    assert mistake.skill_at(first_test_run.index) is None


def test_codex_session(rollout: Session) -> None:
    assert rollout.source == "codex"
    assert rollout.id == "c0dex000-0000-4000-8000-000000000003"
    assert rollout.cwd == "/home/dev/src/widget"
    assert kinds(rollout) == [
        "prompt",
        "assistant",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "interrupt",
        "prompt",
    ]
    patch, cmd = rollout.tool_calls
    assert patch.tool == "apply_patch"
    assert "*** Begin Patch" in str((patch.tool_input or {}).get("input"))
    assert cmd.tool_input == {"cmd": "cargo test"}
    results = [e for e in rollout.events if e.kind is EventKind.TOOL_RESULT]
    assert results[0].text == "Success. Updated size.py"
    assert [e.is_error for e in results] == [False, True]


def test_load_session_detects_source(mistake: Session, rollout: Session) -> None:
    assert load_session(mistake.path).source == "claude-code"
    assert load_session(rollout.path).source == "codex"


def test_find_sessions_filters_by_mtime(tmp_path: Path) -> None:
    project = tmp_path / "-home-dev-src-widget"
    project.mkdir()
    old, new = project / "old.jsonl", project / "new.jsonl"
    for p in (old, new):
        shutil.copy(FIXTURES / "claude_code" / "fixed.jsonl", p)
    week_ago = (datetime.now(UTC) - timedelta(days=7)).timestamp()
    os.utime(old, (week_ago, week_ago))
    (project / "sub").mkdir()
    shutil.copy(FIXTURES / "claude_code" / "fixed.jsonl", project / "sub" / "agent.jsonl")

    assert list(claude_code.find_sessions(tmp_path)) == [new, old]
    since = datetime.now(UTC) - timedelta(days=1)
    assert list(claude_code.find_sessions(tmp_path, since)) == [new]


def test_codex_find_sessions_recurses(tmp_path: Path) -> None:
    day = tmp_path / "2026" / "10" / "01"
    day.mkdir(parents=True)
    shutil.copy(FIXTURES / "codex" / "rollout.jsonl", day / "rollout.jsonl")
    assert list(codex.find_sessions(tmp_path)) == [day / "rollout.jsonl"]
