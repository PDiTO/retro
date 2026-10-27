from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from retro.friction import FrictionKind, detect
from retro.model import EventKind, Session
from retro.sources import SOURCES, load_session, opencode
from tests.sqlite_fixtures import CWD, T0, OpenCodeDB, text, tool

MAIN = "ses_0000000000000000000000001"
CHILD = "ses_0000000000000000000000002"
OLDER = "ses_0000000000000000000000003"
REJECTED = "The user rejected permission to use this specific tool call."
RULE = "The user has specified a rule which prevents you from using this specific tool call."


def kinds(session: Session) -> list[str]:
    return [e.kind.value for e in session.events]


def build(tmp_path: Path) -> Path:
    """Edits, a failing test run, refused calls, a stopped tool, then a correction."""
    db = OpenCodeDB(tmp_path / "opencode.db")
    db.session(OLDER, updated=T0 - 86_400_000)
    db.prompt(OLDER, "an older task")
    db.session(MAIN, branch="feat/verbose", updated=T0 + 3_600_000)
    db.prompt(
        MAIN,
        "Add a --verbose flag to the widget CLI",
        text("Called the Read tool with the following input: {}", synthetic=True),
        {"type": "file", "mime": "text/plain", "filename": "cli.py", "url": "file:///x"},
    )
    db.message(
        MAIN,
        "assistant",
        [
            {"type": "step-start"},
            {"type": "reasoning", "text": "thinking", "time": {"start": T0, "end": T0}},
            text("I'll add the flag.", time={"start": T0 + 5_000, "end": T0 + 6_000}),
            tool("call_1", "edit", "completed", {"filePath": "cli.py"}, output="Edit applied."),
            tool(
                "call_2",
                "bash",
                "completed",
                {"command": "pytest"},
                output="1 failed",
                metadata={"exit": 1, "output": "1 failed", "truncated": False},
            ),
            tool("call_3", "read", "error", {"filePath": "missing.py"}, error="File not found"),
            tool("call_4", "bash", "error", {"command": "git push --force"}, error=REJECTED),
            tool("call_5", "bash", "error", {"command": "rm -rf build"}, error=RULE),
            tool("call_6", "task", "running", {"prompt": "look around"}),
            {"type": "step-finish", "reason": "tool-calls", "cost": 0.0},
        ],
    )
    db.message(
        MAIN,
        "assistant",
        [tool("call_7", "bash", "error", {"command": "sleep 600"}, error="Tool execution aborted")],
        error={"name": "MessageAbortedError", "data": {"message": "Aborted"}},
    )
    db.prompt(MAIN, "no, you didn't run the linter")
    db.message(MAIN, "assistant", [], error={"name": "MessageAbortedError", "data": {}})
    db.session(CHILD, parent=MAIN, updated=T0 + 7_200_000)
    db.prompt(CHILD, "subagent task")
    return db.close()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return build(tmp_path)


@pytest.fixture
def main(db_path: Path) -> Session:
    return opencode.parse_session(Path(f"{db_path}#{MAIN}"))


def test_metadata(main: Session, db_path: Path) -> None:
    assert main.source == "opencode"
    assert main.id == MAIN
    assert main.path == Path(f"{db_path}#{MAIN}")
    assert main.cwd == CWD
    assert main.project == "widget"
    assert main.branch == "feat/verbose"
    assert main.started_at is not None
    assert main.ended_at is not None
    assert main.started_at < main.ended_at


def test_events_in_order(main: Session) -> None:
    assert kinds(main) == [
        "prompt",
        "assistant",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "tool_call",
        "denial",
        "tool_call",
        "denial",
        "tool_call",  # still running: no outcome
        "tool_call",
        "interrupt",  # the aborted tool, not again for its message
        "prompt",
        "interrupt",  # an aborted message with no tool running
    ]
    assert [e.index for e in main.events] == list(range(len(main.events)))


def test_prompts_skip_synthetic_text(main: Session) -> None:
    assert [e.text for e in main.prompts] == [
        "Add a --verbose flag to the widget CLI",
        "no, you didn't run the linter",
    ]


def test_tool_results(main: Session) -> None:
    edit = main.tool_calls[0]
    assert (edit.tool, edit.call_id, edit.tool_input) == ("edit", "call_1", {"filePath": "cli.py"})
    assert edit.timestamp == datetime.fromtimestamp((T0 + 10_000) / 1000, tz=UTC)
    results = [e for e in main.events if e.kind is EventKind.TOOL_RESULT]
    assert [(e.tool, e.text, e.is_error) for e in results] == [
        ("edit", "Edit applied.", False),
        ("bash", "1 failed", True),  # non-zero exit
        ("read", "File not found", True),
    ]


def test_denials_and_interrupts(main: Session) -> None:
    denials = [e for e in main.events if e.kind is EventKind.DENIAL]
    assert [(e.tool, e.detail) for e in denials] == [
        ("bash", "user-rejected"),
        ("bash", "rule-denied"),
    ]
    interrupts = [e for e in main.events if e.kind is EventKind.INTERRUPT]
    assert [(e.tool, e.call_id) for e in interrupts] == [("bash", "call_7"), (None, None)]
    found = {f.kind for f in detect(main)}
    assert {FrictionKind.DENIAL, FrictionKind.INTERRUPT} <= found


def test_iter_sessions_newest_first_without_subagents(db_path: Path) -> None:
    sessions = list(opencode.iter_sessions(db_path.parent))
    assert [s.id for s in sessions] == [MAIN, OLDER]
    assert [s.id for s in opencode.iter_sessions(db_path)] == [MAIN, OLDER]
    since = datetime.fromtimestamp(T0 / 1000, tz=UTC) - timedelta(hours=1)
    assert [s.id for s in opencode.iter_sessions(db_path, since)] == [MAIN]
    assert list(opencode.iter_sessions(db_path.parent / "elsewhere")) == []


def test_bad_references(db_path: Path) -> None:
    with pytest.raises(ValueError, match="expected"):
        opencode.parse_session(db_path)
    with pytest.raises(ValueError, match="no such session"):
        opencode.parse_session(Path(f"{db_path}#ses_missing"))
    with pytest.raises(FileNotFoundError):
        opencode.parse_session(Path(f"{db_path.parent / 'nope.db'}#{MAIN}"))


def test_tolerates_older_schema_and_bad_json(tmp_path: Path) -> None:
    path = tmp_path / "opencode.db"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(
            """
            CREATE TABLE session (id TEXT, parent_id TEXT, directory TEXT, time_updated INTEGER);
            CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT);
            CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT, data TEXT);
            """
        )
        db.execute("INSERT INTO session VALUES ('ses_a', NULL, '', ?)", (T0,))
        db.execute("INSERT INTO message VALUES ('msg_1', 'ses_a', ?, '{\"role\":\"user\"}')", (T0,))
        db.execute("INSERT INTO message VALUES ('msg_2', 'ses_a', ?, 'not json')", (T0,))
        hello = json.dumps({"type": "text", "text": "hi"})
        db.execute("INSERT INTO part VALUES ('prt_1', 'msg_1', 'ses_a', ?)", (hello,))
        db.execute("INSERT INTO part VALUES ('prt_2', 'msg_1', 'ses_a', '[]')")
        db.commit()
    (session,) = opencode.iter_sessions(path)
    assert session.cwd is None
    assert session.branch is None
    assert [e.text for e in session.prompts] == ["hi"]
    assert session.prompts[0].timestamp == datetime.fromtimestamp(T0 / 1000, tz=UTC)


def test_opens_read_only(db_path: Path) -> None:
    with closing(opencode.connect(db_path)) as db, pytest.raises(sqlite3.OperationalError):
        db.execute("DELETE FROM session")


def test_registered_and_loadable(main: Session) -> None:
    assert "opencode" in [s.name for s in SOURCES]
    assert load_session(main.path).events == main.events
