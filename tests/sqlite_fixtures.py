"""Build synthetic Cursor and OpenCode stores at test time.

Both agents keep sessions in SQLite, and `retro audit` refuses committed databases, so
these builders create them under `tmp_path` instead. Every row is made up. The tables,
JSON field names and nesting copy the real formats, none of the content.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

CWD = "/home/dev/src/widget"
T0 = 1_790_000_000_000  # 2026-09-21, in milliseconds


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(number: int, value: bytes | int) -> bytes:
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    return _varint(number << 3 | 2) + _varint(len(value)) + value


class CursorChat:
    """One chat directory: meta.json plus a content-addressed store.db."""

    def __init__(
        self,
        root: Path,
        chat_id: str = "c0750400-0000-4000-8000-000000000001",
        *,
        workspace: str = "0123456789abcdef0123456789abcdef",
        cwd: str = CWD,
        branch: str | None = "feat/verbose",
        created_ms: int = T0,
        updated_ms: int = T0 + 600_000,
    ) -> None:
        self.dir = root / workspace / chat_id
        self.chat_id = chat_id
        self.cwd = cwd
        self.branch = branch
        self.created_ms = created_ms
        self.updated_ms = updated_ms
        self.messages: list[dict[str, Any]] = []

    def raw(self, message: dict[str, Any]) -> None:
        self.messages.append(message)

    def system(self) -> None:
        self.raw({"role": "system", "content": "You are a coding agent."})

    def context(self) -> None:
        text = f"<user_info>\nOS: linux\nWorkspace: {self.cwd}\n</user_info>\n<rules>\n</rules>"
        self.raw({"role": "user", "content": text})

    def prompt(self, text: str, when: str = "Thu, Oct 1, 2026, 12:00\u202fPM (UTC+2)") -> None:
        body = f"<timestamp>{when}</timestamp>\n<user_query>\n{text}\n</user_query>"
        self.raw({"role": "user", "content": [{"type": "text", "text": body}]})

    def notification(self, text: str, when: str = "Thu, Oct 1, 2026, 12:30 PM (UTC+2)") -> None:
        body = f"<timestamp>{when}</timestamp>\n<system_notification>{text}</system_notification>"
        self.raw({"role": "user", "content": body})

    def summary(self) -> None:
        self.raw(
            {
                "role": "user",
                "content": "<conversation_summary>Earlier work.</conversation_summary>",
                "providerOptions": {"cursor": {"isSummary": True}},
            }
        )

    def say(self, text: str) -> None:
        reasoning = {
            "type": "reasoning",
            "text": "thinking it over",
            "signature": "sig",
            "providerOptions": {"cursor": {"modelName": "example-model"}},
        }
        self.raw({"role": "assistant", "content": [reasoning, {"type": "text", "text": text}]})

    def call(self, call_id: str, name: str, **args: Any) -> None:
        part = {"type": "tool-call", "toolCallId": call_id, "toolName": name, "args": args}
        self.raw({"role": "assistant", "content": [part]})

    def result(
        self, call_id: str, name: str, text: str, output: dict[str, Any], *, error: bool = False
    ) -> None:
        part = {
            "type": "tool-result",
            "toolCallId": call_id,
            "toolName": name,
            "result": text,
            "experimental_content": [{"type": "text", "text": text}],
        }
        outcome = {"isError": error, "output": output}
        self.raw(
            {
                "role": "tool",
                "content": [part],
                "providerOptions": {"cursor": {"highLevelToolCallResult": outcome}},
            }
        )

    def write(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        info = {
            "schemaVersion": 1,
            "createdAtMs": self.created_ms,
            "hasConversation": True,
            "updatedAtMs": self.updated_ms,
            "cwd": self.cwd,
        }
        (self.dir / "meta.json").write_text(json.dumps(info), encoding="utf-8")
        blobs: dict[str, bytes] = {}

        def put(data: bytes) -> bytes:
            digest = hashlib.sha256(data).digest()
            blobs[digest.hex()] = data
            return digest

        ids = [put(json.dumps(m).encode()) for m in self.messages]
        put(b'{"name": "widget", "private": true}')  # file contents live in the store too
        workspace = _field(1, self.cwd.encode())
        if self.branch:
            workspace += _field(2, self.branch.encode())
        tail = _field(21, workspace) + _field(26, self.created_ms)
        put(b"".join(_field(1, i) for i in ids[:1]) + tail)  # an older root
        root = put(b"".join(_field(1, i) for i in ids) + tail)
        meta = {
            "agentId": self.chat_id,
            "latestRootBlobId": root.hex(),
            "name": "Widget flag",
            "mode": "default",
            "isRunEverything": False,
            "createdAt": self.created_ms,
            "blobEncryptionKey": "00" * 32,
        }
        store = self.dir / "store.db"
        with closing(sqlite3.connect(store)) as db:
            db.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
            db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
            db.executemany("INSERT INTO blobs VALUES (?, ?)", blobs.items())
            db.execute("INSERT INTO meta VALUES ('0', ?)", (json.dumps(meta).encode().hex(),))
            db.commit()
        return store


_OPENCODE_SCHEMA = """
CREATE TABLE project (id TEXT PRIMARY KEY, worktree TEXT NOT NULL, vcs TEXT,
    time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL);
CREATE TABLE workspace (id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT DEFAULT '' NOT NULL,
    branch TEXT, directory TEXT, extra TEXT, project_id TEXT NOT NULL,
    time_used INTEGER NOT NULL);
CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workspace_id TEXT,
    parent_id TEXT, slug TEXT NOT NULL, directory TEXT NOT NULL, title TEXT NOT NULL,
    version TEXT NOT NULL, agent TEXT, model TEXT, time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL, time_archived INTEGER);
CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT NOT NULL, session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    type TEXT NOT NULL, seq INTEGER NOT NULL, time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL, data TEXT NOT NULL);
CREATE INDEX message_session_time_created_id_idx ON message (session_id, time_created, id);
CREATE INDEX part_session_idx ON part (session_id);
"""


class OpenCodeDB:
    """An opencode.db with the session, message, part and workspace tables."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.executescript(_OPENCODE_SCHEMA)
        self.db.execute("INSERT INTO project VALUES ('prj_1', ?, 'git', ?, ?)", (CWD, T0, T0))
        self.clock = T0
        self.counter = 0

    def _id(self, prefix: str) -> str:
        self.counter += 1
        return f"{prefix}_{self.counter:06d}"

    def tick(self, ms: int = 1000) -> int:
        self.clock += ms
        return self.clock

    def session(
        self,
        session_id: str,
        *,
        parent: str | None = None,
        branch: str | None = None,
        updated: int | None = None,
    ) -> str:
        workspace = None
        if branch:
            workspace = self._id("wrk")
            self.db.execute(
                "INSERT INTO workspace (id, type, branch, directory, project_id, time_used)"
                " VALUES (?, 'worktree', ?, ?, 'prj_1', ?)",
                (workspace, branch, CWD, self.clock),
            )
        self.db.execute(
            "INSERT INTO session (id, project_id, workspace_id, parent_id, slug, directory, title,"
            " version, agent, model, time_created, time_updated)"
            " VALUES (?, 'prj_1', ?, ?, 'brave-widget', ?, 'Widget flag', '1.0.0', 'build',"
            " ?, ?, ?)",
            (
                session_id,
                workspace,
                parent,
                CWD,
                json.dumps({"id": "example-model", "providerID": "example"}),
                self.clock,
                updated or self.clock,
            ),
        )
        return session_id

    def message(self, session_id: str, role: str, parts: list[dict[str, Any]], **data: Any) -> str:
        created = self.tick()
        message_id = self._id("msg")
        info: dict[str, Any] = {"role": role, "time": {"created": created}, **data}
        if role == "assistant":
            info |= {
                "modelID": "example-model",
                "providerID": "example",
                "path": {"cwd": CWD, "root": CWD},
            }
        self.db.execute(
            "INSERT INTO message VALUES (?, ?, ?, ?, ?)",
            (message_id, session_id, created, created, json.dumps(info)),
        )
        for part in parts:
            self.db.execute(
                "INSERT INTO part VALUES (?, ?, ?, ?, ?, ?)",
                (self._id("prt"), message_id, session_id, created, created, json.dumps(part)),
            )
        return message_id

    def prompt(self, session_id: str, text: str, *extra: dict[str, Any]) -> None:
        self.message(session_id, "user", [{"type": "text", "text": text}, *extra])

    def close(self) -> Path:
        self.db.commit()
        self.db.close()
        return self.path


def text(value: str, **extra: Any) -> dict[str, Any]:
    return {"type": "text", "text": value, **extra}


def tool(
    call_id: str, name: str, status: str, tool_input: dict[str, Any], **state: Any
) -> dict[str, Any]:
    return {
        "type": "tool",
        "callID": call_id,
        "tool": name,
        "state": {
            "status": status,
            "input": tool_input,
            "time": {"start": T0 + 10_000, "end": T0 + 11_000},
            **state,
        },
    }
