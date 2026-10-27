"""Write the synthetic session fixtures under tests/fixtures/.

Every record here is made up. They copy the shape of real Claude Code and Codex logs,
field names and nesting included, but none of the content. Each file starts with the
`{"retro_fixture": "synthetic"}` marker so `retro audit` lets it through.

    uv run python scripts/make_fixtures.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
MARKER = {"retro_fixture": "synthetic"}
SESSION = "5e55i0n0-0000-4000-8000-000000000001"
CWD = "/home/dev/src/widget"


def _ts(second: int) -> str:
    return f"2026-10-01T12:{second // 60:02d}:{second % 60:02d}.000Z"


class Claude:
    def __init__(self, session: str = SESSION) -> None:
        self.session = session
        self.records: list[dict[str, Any]] = [MARKER]
        self.clock = 0

    def _base(self, kind: str, **extra: Any) -> dict[str, Any]:
        self.clock += 5
        return {
            "parentUuid": None,
            "isSidechain": False,
            "type": kind,
            "uuid": f"u-{len(self.records)}",
            "timestamp": _ts(self.clock),
            "sessionId": self.session,
            "cwd": CWD,
            "gitBranch": "feat/verbose",
            "entrypoint": "cli",
            **extra,
        }

    def prompt(self, text: str, **extra: Any) -> None:
        self.records.append(self._base("user", message={"role": "user", "content": text}, **extra))

    def say(self, text: str, skill: str | None = None) -> None:
        msg = {"role": "assistant", "content": [{"type": "text", "text": text}]}
        self.records.append(self._base("assistant", message=msg, attributionSkill=skill))

    def call(self, call_id: str, name: str, skill: str | None = None, **tool_input: Any) -> None:
        part = {"type": "tool_use", "id": call_id, "name": name, "input": tool_input}
        msg = {"role": "assistant", "content": [part]}
        self.records.append(self._base("assistant", message=msg, attributionSkill=skill))

    def result(self, call_id: str, text: str, *, error: bool = False, **extra: Any) -> None:
        part = {"type": "tool_result", "tool_use_id": call_id, "content": text, "is_error": error}
        self.records.append(
            self._base("user", message={"role": "user", "content": [part]}, **extra)
        )

    def raw(self, record: dict[str, Any]) -> None:
        self.records.append(record)

    def write(self, path: Path, trailing: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(r) for r in self.records]
        path.write_text("\n".join(lines) + "\n" + trailing, encoding="utf-8")


def claude_mistake() -> None:
    """Commits before testing, gets corrected, hits an error streak, tries a force push."""
    s = Claude()
    s.raw({"type": "queue-operation", "operation": "enqueue", "sessionId": SESSION})
    s.prompt("Add a --verbose flag to the widget CLI and commit it")
    s.say("I'll add the flag to the argument parser.")
    s.call(
        "t1",
        "Edit",
        file_path=f"{CWD}/cli.py",
        old_string="args = p.parse_args()",
        new_string=(
            "p.add_argument('--verbose')  # louder output — for debugging\nargs = p.parse_args()"
        ),
    )
    s.result("t1", "The file was updated.")
    s.call("t2", "Bash", skill="git-stage", command="git commit -am 'Add --verbose flag'")
    s.result("t2", "[feat/verbose 1a2b3c4] Add --verbose flag")
    s.say("Committed. The flag is in.", skill="git-stage")
    s.prompt("<command-name>/status</command-name>", isMeta=True)
    s.prompt("no, you didn't run the tests before committing")
    s.call("t3", "Bash", command="uv run pytest")
    s.result("t3", "ModuleNotFoundError: No module named 'widget'", error=True)
    s.call("t4", "Bash", command="uv run pytest -x")
    s.result("t4", "ModuleNotFoundError: No module named 'widget'", error=True)
    s.call("t5", "Bash", command="uv sync && uv run pytest")
    s.result("t5", "error: Failed to build `widget`", error=True)
    s.call("t6", "Bash", skill="git-stage", command="git push --force origin feat/verbose")
    s.result(
        "t6",
        "The user doesn't want to proceed with this tool use.",
        toolDenialKind="user-rejected",
        permissionDecision={"decision": "reject", "source": "user"},
    )
    s.prompt("[Request interrupted by user]")
    s.raw(
        {
            "type": "pr-link",
            "sessionId": SESSION,
            "prNumber": 7,
            "prUrl": "https://github.com/example/widget/pull/7",
            "prRepository": "example/widget",
        }
    )
    s.raw(
        {
            **s._base("user", message={"role": "user", "content": "no, wrong file"}),
            "isSidechain": True,
        }
    )
    s.raw({"type": "api-request-blob", "sessionId": SESSION, "blob": "x" * 200})
    s.write(ROOT / "claude_code" / "mistake.jsonl", trailing='{"type":"assistant","mess')


def claude_fixed() -> None:
    """The same task done properly: tests before the commit, no force push."""
    s = Claude("5e55i0n0-0000-4000-8000-000000000002")
    s.prompt("Add a --verbose flag to the widget CLI and commit it")
    s.say("Should the flag also raise the log level?")
    s.prompt("no, just print more")
    s.call(
        "t1",
        "Edit",
        file_path=f"{CWD}/cli.py",
        old_string="args = p.parse_args()",
        new_string="p.add_argument('--verbose')\nargs = p.parse_args()",
    )
    s.result("t1", "The file was updated.")
    s.call("t2", "Bash", command="uv run pytest")
    s.result("t2", "12 passed in 0.31s")
    s.call("t3", "Bash", skill="git-stage", command="git commit -am 'Add --verbose flag'")
    s.result("t3", "[feat/verbose 5d6e7f8] Add --verbose flag")
    s.say("Tests pass and the change is committed.", skill="git-stage")
    s.write(ROOT / "claude_code" / "fixed.jsonl")


def codex_session() -> None:
    def rec(kind: str, second: int, payload: dict[str, Any]) -> dict[str, Any]:
        return {"timestamp": _ts(second), "type": kind, "payload": payload}

    def msg(role: str, text: str) -> dict[str, Any]:
        kind = "input_text" if role == "user" else "output_text"
        return {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}

    records: list[dict[str, Any]] = [
        MARKER,
        rec(
            "session_meta",
            0,
            {
                "id": "c0dex000-0000-4000-8000-000000000003",
                "cwd": CWD,
                "originator": "codex_cli_rs",
            },
        ),
        rec("response_item", 1, msg("user", "# AGENTS.md instructions for the widget repo")),
        rec("response_item", 1, msg("user", "<environment_context>\n  <cwd>/home/dev</cwd>")),
        rec("response_item", 2, msg("user", "Make the parser reject negative sizes")),
        rec("response_item", 3, msg("assistant", "Adding a check in parse_size.")),
        rec(
            "response_item",
            4,
            {
                "type": "custom_tool_call",
                "call_id": "c1",
                "name": "apply_patch",
                "input": (
                    "*** Begin Patch\n*** Update File: size.py\n"
                    "+    if n < 0:\n+        raise ValueError('negative')\n*** End Patch"
                ),
            },
        ),
        rec(
            "response_item",
            5,
            {
                "type": "custom_tool_call_output",
                "call_id": "c1",
                "output": json.dumps({"output": "Success. Updated size.py"}),
            },
        ),
        rec(
            "response_item",
            6,
            {
                "type": "function_call",
                "call_id": "c2",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "cargo test"}),
            },
        ),
        rec(
            "response_item",
            7,
            {
                "type": "function_call_output",
                "call_id": "c2",
                "output": "Exit code: 101\nerror[E0425]: cannot find value",
            },
        ),
        rec("event_msg", 8, {"type": "turn_aborted", "reason": "interrupted"}),
        rec("response_item", 9, msg("user", "why did you skip the lints?")),
    ]
    path = ROOT / "codex" / "rollout.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def github_pr() -> None:
    pr = {
        "number": 7,
        "state": "MERGED",
        "mergedAt": "2026-10-02T09:00:00Z",
        "author": {"login": "dev"},
        "reviews": [
            {
                "author": {"login": "dev"},
                "state": "COMMENTED",
                "body": "self note",
                "submittedAt": "2026-10-01T13:00:00Z",
            },
            {
                "author": {"login": "reviewer-bot"},
                "state": "COMMENTED",
                "body": "P1: --verbose is parsed but never read",
                "submittedAt": "2026-10-01T13:05:00Z",
            },
            {
                "author": {"login": "alice"},
                "state": "APPROVED",
                "body": "",
                "submittedAt": "2026-10-01T14:00:00Z",
            },
        ],
        "statusCheckRollup": [
            {
                "__typename": "CheckRun",
                "name": "test",
                "conclusion": "FAILURE",
                "completedAt": "2026-10-01T13:10:00Z",
            },
            {
                "__typename": "CheckRun",
                "name": "lint",
                "conclusion": "SUCCESS",
                "completedAt": "2026-10-01T13:09:00Z",
            },
            {"__typename": "StatusContext", "context": "deploy", "state": "PENDING"},
        ],
    }
    (ROOT / "github").mkdir(parents=True, exist_ok=True)
    (ROOT / "github" / "pr7.json").write_text(json.dumps(pr, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    claude_mistake()
    claude_fixed()
    codex_session()
    github_pr()
    print(f"wrote fixtures under {ROOT}")
