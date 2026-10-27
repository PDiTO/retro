from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from retro import agents
from retro.agents import AgentError, Completed

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "integer"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class FakeExec:
    """Records argv and returns canned stdout; can also write files into the scratch dir."""

    def __init__(self, stdout: str = "", returncode: int = 0, files: dict[str, str] | None = None):
        self.stdout = stdout
        self.returncode = returncode
        self.files = files or {}
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(self, args: list[str], cwd: Path | None, timeout: float) -> Completed:
        self.calls.append((args, cwd))
        for name, text in self.files.items():
            assert cwd is not None
            (cwd / name).write_text(text)
        return Completed(self.returncode, self.stdout, "boom" if self.returncode else "")


def make(name: str, fake: FakeExec, model: str | None = None) -> agents.Agent:
    agent = agents.BACKENDS[name](model=model, exec=fake)
    agent.path = name
    return agent


def test_claude_uses_native_schema_and_no_tools() -> None:
    out = {
        "result": '{"answer": 42}',
        "structured_output": {"answer": 42},
        "total_cost_usd": 0.01,
        "session_id": "s1",
        "is_error": False,
    }
    fake = FakeExec(json.dumps(out))
    reply = make("claude", fake, model="sonnet").ask("6*7?", SCHEMA)
    assert reply.data == {"answer": 42}
    assert reply.cost_usd == 0.01
    assert reply.session_id == "s1"
    args, _ = fake.calls[0]
    assert args[:3] == ["claude", "-p", "6*7?"]
    assert args[args.index("--tools") + 1] == ""
    assert json.loads(args[args.index("--json-schema") + 1]) == SCHEMA
    assert args[-2:] == ["--model", "sonnet"]


def test_claude_error_result() -> None:
    fake = FakeExec(json.dumps({"is_error": True, "result": "Not logged in"}))
    with pytest.raises(AgentError, match="Not logged in"):
        make("claude", fake).ask("x")


def test_codex_reads_last_message_file() -> None:
    fake = FakeExec("noise", files={"last.txt": '{"answer": 42}'})
    reply = make("codex", fake).ask("6*7?", SCHEMA)
    assert reply.data == {"answer": 42}
    args, cwd = fake.calls[0]
    assert args[:2] == ["codex", "exec"]
    assert "--output-schema" in args
    assert args[args.index("-s") + 1] == "read-only"
    assert args[args.index("-C") + 1] == str(cwd)
    assert args[-1] == "6*7?"


def test_codex_task_stream() -> None:
    lines = [
        {"type": "thread.started", "thread_id": "t-9"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
    ]
    fake = FakeExec("\n".join(json.dumps(x) for x in lines))
    reply = make("codex", fake).run_task("fix it", Path("/tmp"))
    assert (reply.text, reply.session_id) == ("done", "t-9")
    args, _ = fake.calls[0]
    assert args[args.index("-s") + 1] == "workspace-write"


def test_schema_goes_in_the_prompt_for_other_agents() -> None:
    out = {"type": "result", "is_error": False, "result": 'Sure! {"answer": 42}', "session_id": "c"}
    fake = FakeExec(json.dumps(out))
    reply = make("cursor", fake).ask("6*7?", SCHEMA)
    assert reply.data == {"answer": 42}
    args, _ = fake.calls[0]
    assert "--mode" in args
    assert args[args.index("--mode") + 1] == "ask"
    assert "JSON Schema" in args[-1]


def test_opencode_joins_text_events_and_sums_cost() -> None:
    events = [
        {"type": "step_start", "sessionID": "ses_1", "part": {}},
        {"type": "text", "sessionID": "ses_1", "part": {"text": '{"answer":'}},
        {"type": "text", "sessionID": "ses_1", "part": {"text": " 42}"}},
        {"type": "step_finish", "sessionID": "ses_1", "part": {"cost": 0.002}},
    ]
    fake = FakeExec("\n".join(json.dumps(e) for e in events))
    reply = make("opencode", fake).ask("6*7?", SCHEMA)
    assert reply.data == {"answer": 42}
    assert reply.cost_usd == 0.002
    assert reply.session_id == "ses_1"


def test_missing_required_key_is_an_error() -> None:
    fake = FakeExec(json.dumps({"type": "result", "result": '{"other": 1}'}))
    with pytest.raises(AgentError, match="missing"):
        make("cursor", fake).ask("x", SCHEMA)


def test_no_json_is_an_error() -> None:
    fake = FakeExec(json.dumps({"type": "result", "result": "I can't do that"}))
    with pytest.raises(AgentError, match="did not return JSON"):
        make("cursor", fake).ask("x", SCHEMA)


def test_nonzero_exit() -> None:
    with pytest.raises(AgentError, match="exited 1: boom"):
        make("grok", FakeExec(returncode=1)).ask("x")


def test_timeout_and_missing_binary() -> None:
    def slow(args: list[str], cwd: Path | None, timeout: float) -> Completed:
        raise subprocess.TimeoutExpired(args, timeout)

    def gone(args: list[str], cwd: Path | None, timeout: float) -> Completed:
        raise FileNotFoundError(args[0])

    with pytest.raises(AgentError, match="timed out"):
        agents.Gemini(exec=slow).ask("x", timeout=1)
    with pytest.raises(AgentError, match="not installed"):
        agents.Gemini(exec=gone).ask("x")


def test_detect() -> None:
    paths = {"claude": "/bin/claude", "agent": "/bin/agent"}

    def fake(args: list[str], cwd: Path | None, timeout: float) -> Completed:
        return Completed(0, f"{Path(args[0]).name} 1.0\n", "")

    found = agents.detect(which=paths.get, exec=fake)
    assert [(d.name, d.path, d.version, d.tested) for d in found] == [
        ("claude", "/bin/claude", "claude 1.0", True),
        ("cursor", "/bin/agent", "agent 1.0", True),
    ]


def test_get_unknown() -> None:
    with pytest.raises(AgentError, match="unknown agent"):
        agents.get("clippy")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('Here you go:\n```json\n{"a": 1}\n```', {"a": 1}),
        ('noise {not json} then {"a": {"b": 2}} tail', {"a": {"b": 2}}),
        ("[1, 2]", None),
        ("nothing", None),
    ],
)
def test_extract_json(text: str, expected: dict[str, Any] | None) -> None:
    assert agents.extract_json(text) == expected
