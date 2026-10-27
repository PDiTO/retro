from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from retro.friction import FrictionKind, detect
from retro.model import EventKind, Session
from retro.sources import SOURCES, cursor, load_session
from tests.sqlite_fixtures import CWD, T0, CursorChat


def kinds(session: Session) -> list[str]:
    return [e.kind.value for e in session.events]


def build(root: Path) -> CursorChat:
    """Edits a file, runs failing tests, has calls refused, then gets stopped and corrected."""
    chat = CursorChat(root)
    chat.system()
    chat.context()
    chat.prompt("Add a --verbose flag to the widget CLI")
    chat.say("I'll add the flag to the argument parser.")
    chat.call("call-1", "StrReplace", path="widget/cli.py", old_string="a", new_string="b")
    chat.result("call-1", "StrReplace", "Updated widget/cli.py", {"success": {"linesAdded": 1}})
    chat.call("call-2", "Shell", command="pytest", description="Run tests")
    chat.result("call-2", "Shell", "1 failed", {"failure": {"command": "pytest", "exitCode": 1}})
    chat.call("call-3", "Shell", command="git push --force", description="Push")
    chat.result(
        "call-3", "Shell", "", {"rejected": {"command": "git push --force", "reason": "no"}}
    )
    chat.call("call-4", "Write", path="/etc/hosts", contents="x")
    chat.result("call-4", "Write", "", {"permissionDenied": {"clientVisibleError": "blocked"}})
    chat.call("call-5", "Read", path="missing.py")
    chat.result(
        "call-5", "Read", "File not found", {"error": {"error": "File not found"}}, error=True
    )
    chat.notification("Background shell finished")
    chat.summary()
    chat.call("call-6", "Shell", command="sleep 600", description="Wait")
    chat.prompt("stop, you didn't run the linter", when="Thu, Oct 1, 2026, 1:05 PM (UTC+2)")
    chat.say("Running it now.")
    chat.write()
    return chat


@pytest.fixture
def chat(tmp_path: Path) -> Session:
    return cursor.parse_session(build(tmp_path).dir / "store.db")


def test_metadata(chat: Session) -> None:
    assert chat.source == "cursor"
    assert chat.id == "c0750400-0000-4000-8000-000000000001"
    assert chat.cwd == CWD
    assert chat.project == "widget"
    assert chat.branch == "feat/verbose"
    assert chat.path.name == "store.db"


def test_events_in_order(chat: Session) -> None:
    assert kinds(chat) == [
        "prompt",
        "assistant",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "tool_call",
        "denial",
        "tool_call",
        "denial",
        "tool_call",
        "tool_result",
        "tool_call",
        "interrupt",
        "prompt",
        "assistant",
    ]
    assert [e.index for e in chat.events] == list(range(len(chat.events)))


def test_prompts_exclude_injected_context(chat: Session) -> None:
    assert [e.text for e in chat.prompts] == [
        "Add a --verbose flag to the widget CLI",
        "stop, you didn't run the linter",
    ]


def test_prompt_timestamps_come_from_the_timestamp_tag(chat: Session) -> None:
    first, second = chat.prompts
    assert first.timestamp == datetime(2026, 10, 1, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    assert second.timestamp == datetime(2026, 10, 1, 13, 5, tzinfo=timezone(timedelta(hours=2)))
    assert chat.started_at == first.timestamp
    assert chat.ended_at == datetime.fromtimestamp((T0 + 600_000) / 1000, tz=UTC)


def test_tool_calls_and_results(chat: Session) -> None:
    edit = chat.tool_calls[0]
    assert edit.tool == "StrReplace"
    assert edit.call_id == "call-1"
    assert edit.tool_input == {"path": "widget/cli.py", "old_string": "a", "new_string": "b"}
    results = [e for e in chat.events if e.kind is EventKind.TOOL_RESULT]
    assert [(e.tool, e.is_error) for e in results] == [
        ("StrReplace", False),
        ("Shell", True),  # `failure`: non-zero exit
        ("Read", True),  # `error`
    ]
    assert results[0].text == "Updated widget/cli.py"


def test_denials(chat: Session) -> None:
    denials = [e for e in chat.events if e.kind is EventKind.DENIAL]
    assert [(e.tool, e.detail, e.text) for e in denials] == [
        ("Shell", "user-rejected", "no"),
        ("Write", "permission-denied", "blocked"),
    ]


def test_unanswered_call_before_next_prompt_is_an_interrupt(chat: Session) -> None:
    interrupt = next(e for e in chat.events if e.kind is EventKind.INTERRUPT)
    assert interrupt.tool == "Shell"
    assert interrupt.call_id == "call-6"


def test_friction_detected(chat: Session) -> None:
    found = {f.kind for f in detect(chat)}
    assert {FrictionKind.DENIAL, FrictionKind.INTERRUPT} <= found


def test_reads_only_the_latest_root(tmp_path: Path) -> None:
    chat = CursorChat(tmp_path)
    chat.prompt("first")
    chat.prompt("second")
    session = cursor.parse_session(chat.write())
    assert [e.text for e in session.prompts] == ["first", "second"]


def test_plain_user_text_counts_as_a_prompt(tmp_path: Path) -> None:
    chat = CursorChat(tmp_path, branch=None)
    chat.raw({"role": "user", "content": "just typed"})
    session = cursor.parse_session(chat.write())
    assert [e.text for e in session.prompts] == ["just typed"]
    assert session.branch is None
    assert session.started_at == datetime.fromtimestamp(T0 / 1000, tz=UTC)


def test_find_sessions_newest_first_and_since(tmp_path: Path) -> None:
    old = CursorChat(tmp_path, "c0750400-0000-4000-8000-00000000000a").write()
    new = CursorChat(tmp_path, "c0750400-0000-4000-8000-00000000000b").write()
    week_ago = (datetime.now(UTC) - timedelta(days=7)).timestamp()
    for path in old.parent.iterdir():
        os.utime(path, (week_ago, week_ago))
    empty = tmp_path / "0123456789abcdef0123456789abcdef" / "c0750400-0000-4000-8000-00000000000c"
    empty.mkdir()
    (empty / "meta.json").write_text("{}")

    assert list(cursor.find_sessions(tmp_path)) == [new, old]
    since = datetime.now(UTC) - timedelta(days=1)
    assert list(cursor.find_sessions(tmp_path, since)) == [new]


def test_iter_sessions_skips_unreadable_stores(tmp_path: Path) -> None:
    good = build(tmp_path)
    bad = CursorChat(tmp_path, "c0750400-0000-4000-8000-00000000000d").write()
    with closing(sqlite3.connect(bad)) as db:
        db.execute("DELETE FROM meta")
        db.commit()
    with pytest.raises(ValueError, match="meta"):
        cursor.parse_session(bad)
    assert [s.id for s in cursor.iter_sessions(tmp_path)] == [good.chat_id]


def test_opens_read_only(tmp_path: Path) -> None:
    store = build(tmp_path).dir / "store.db"
    with closing(cursor.connect(store)) as db, pytest.raises(sqlite3.OperationalError):
        db.execute("DELETE FROM blobs")


def test_protobuf_reader_rejects_truncated_input() -> None:
    assert list(cursor._fields(b"\x08\x96\x01")) == [(1, 150)]
    with pytest.raises(ValueError, match="truncated"):
        list(cursor._fields(b"\x0a\x05ab"))
    with pytest.raises(ValueError, match="varint"):
        list(cursor._fields(b"\x08\x96"))


def test_registered_and_loadable(chat: Session) -> None:
    assert "cursor" in [s.name for s in SOURCES]
    assert load_session(chat.path).events == chat.events
