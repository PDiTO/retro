"""Headless coding-agent CLIs, used as the model behind retro's prompts.

retro doesn't call any model API itself. It shells out to whichever agent CLIs are
installed and logged in, so it runs on the user's existing subscription or key, and the
same tool that produced the sessions can judge them.

Each backend knows how to do two things:

- `ask(prompt, schema)`: a one-shot, read-only question with a JSON answer. Used to label
  friction, group it into patterns, propose changes and grade `judge` checks. Tools are
  turned off or the agent is put in a read-only mode, so a prompt built from transcript
  excerpts can't make it act on the machine.
- `run_task(prompt, workdir)`: a full agent run in a throwaway worktree, used to replay a
  case. This one is allowed to edit files and run commands, inside `workdir` only.

`claude` and `codex` take a JSON Schema and return typed output. The others get the
schema in the prompt and retro pulls the first JSON object out of the reply.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

Exec = Callable[[list[str], Path | None, float], "Completed"]


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


def _subprocess(args: list[str], cwd: Path | None, timeout: float) -> Completed:
    proc = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return Completed(proc.returncode, proc.stdout, proc.stderr)


class AgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class Reply:
    text: str
    data: dict[str, Any] | None
    seconds: float
    cost_usd: float | None = None
    session_id: str | None = None


@dataclass
class Agent:
    """Base class. Subclasses fill in `binaries`, `_ask_args`, `_task_args` and `_parse`."""

    name: str = ""
    label: str = ""
    binaries: tuple[str, ...] = ()
    tested: bool = False  # verified end to end against the real CLI
    native_schema: bool = False
    model: str | None = None
    exec: Exec = field(default=_subprocess, repr=False)
    path: str | None = None

    def ask(
        self, prompt: str, schema: dict[str, Any] | None = None, *, timeout: float = 600
    ) -> Reply:
        if schema is not None and not self.native_schema:
            prompt = f"{prompt}\n\n{_schema_instructions(schema)}"
        with tempfile.TemporaryDirectory(prefix="retro-ask-") as tmp:
            scratch = Path(tmp)
            args = self._ask_args(prompt, schema, scratch)
            reply = self._call(args, scratch, timeout)
        if schema is not None and reply.data is None:
            raise AgentError(f"{self.name} did not return JSON matching the schema")
        if schema is not None and reply.data is not None:
            missing = [k for k in schema.get("required", []) if k not in reply.data]
            if missing:
                raise AgentError(f"{self.name} reply is missing {missing}")
        return reply

    def run_task(self, prompt: str, workdir: Path, *, timeout: float = 1800) -> Reply:
        return self._call(self._task_args(prompt, workdir), workdir, timeout)

    def _call(self, args: list[str], cwd: Path | None, timeout: float) -> Reply:
        binary = self.path or self.binaries[0]
        start = time.monotonic()
        try:
            done = self.exec([binary, *args], cwd, timeout)
        except subprocess.TimeoutExpired as e:
            raise AgentError(f"{self.name} timed out after {timeout:.0f}s") from e
        except FileNotFoundError as e:
            raise AgentError(f"{binary} is not installed") from e
        if done.returncode != 0:
            tail = (done.stderr or done.stdout).strip().splitlines()[-3:]
            raise AgentError(f"{self.name} exited {done.returncode}: {' | '.join(tail)}")
        reply = self._parse(done.stdout, cwd)
        return Reply(
            reply.text, reply.data, time.monotonic() - start, reply.cost_usd, reply.session_id
        )

    def _ask_args(self, prompt: str, schema: dict[str, Any] | None, scratch: Path) -> list[str]:
        raise NotImplementedError

    def _task_args(self, prompt: str, workdir: Path) -> list[str]:
        raise NotImplementedError

    def _parse(self, stdout: str, cwd: Path | None) -> Reply:
        text = stdout.strip()
        return Reply(text, extract_json(text), 0.0)


@dataclass
class Claude(Agent):
    name: str = "claude"
    label: str = "Claude Code"
    binaries: tuple[str, ...] = ("claude",)
    tested: bool = True
    native_schema: bool = True

    def _ask_args(self, prompt: str, schema: dict[str, Any] | None, scratch: Path) -> list[str]:
        args = ["-p", prompt, "--output-format", "json", "--no-session-persistence"]
        args += ["--tools", "", "--strict-mcp-config"]
        if schema is not None:
            args += ["--json-schema", json.dumps(schema)]
        return args + self._model()

    def _task_args(self, prompt: str, workdir: Path) -> list[str]:
        # Only ever pointed at a throwaway worktree, so skipping permission prompts is safe.
        return [
            "-p",
            prompt,
            "--output-format",
            "json",
            "--permission-mode",
            "bypassPermissions",
            *self._model(),
        ]

    def _parse(self, stdout: str, cwd: Path | None) -> Reply:
        out = _json_object(stdout)
        if out.get("is_error"):
            raise AgentError(f"claude reported an error: {str(out.get('result'))[:200]}")
        text = str(out.get("result") or "")
        data = out.get("structured_output")
        return Reply(
            text,
            data if isinstance(data, dict) else extract_json(text),
            0.0,
            cost_usd=out.get("total_cost_usd"),
            session_id=out.get("session_id"),
        )

    def _model(self) -> list[str]:
        return ["--model", self.model] if self.model else []


@dataclass
class Codex(Agent):
    name: str = "codex"
    label: str = "Codex"
    binaries: tuple[str, ...] = ("codex",)
    tested: bool = True
    native_schema: bool = True
    effort: str = "low"

    def _ask_args(self, prompt: str, schema: dict[str, Any] | None, scratch: Path) -> list[str]:
        args = [
            "exec",
            "--skip-git-repo-check",
            "--ephemeral",
            "-s",
            "read-only",
            "-C",
            str(scratch),
        ]
        args += ["-c", f"model_reasoning_effort={self.effort}", "-o", str(scratch / "last.txt")]
        if schema is not None:
            (scratch / "schema.json").write_text(json.dumps(schema))
            args += ["--output-schema", str(scratch / "schema.json")]
        return [*args, *self._model(), prompt]

    def _task_args(self, prompt: str, workdir: Path) -> list[str]:
        return [
            "exec",
            "--json",
            "-s",
            "workspace-write",
            "-C",
            str(workdir),
            *self._model(),
            prompt,
        ]

    def _parse(self, stdout: str, cwd: Path | None) -> Reply:
        last = cwd / "last.txt" if cwd else None
        if last and last.exists():
            text = last.read_text(encoding="utf-8").strip()
            return Reply(text, extract_json(text), 0.0)
        session_id = None
        texts = []
        for line in stdout.splitlines():
            event = _maybe_json(line)
            if not isinstance(event, dict):
                continue
            session_id = session_id or event.get("thread_id") or event.get("session_id")
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                texts.append(str(item.get("text", "")))
        text = "\n".join(texts).strip() or stdout.strip()
        return Reply(text, extract_json(text), 0.0, session_id=session_id)

    def _model(self) -> list[str]:
        return ["-m", self.model] if self.model else []


@dataclass
class Cursor(Agent):
    name: str = "cursor"
    label: str = "Cursor Agent"
    binaries: tuple[str, ...] = ("cursor-agent", "agent")
    tested: bool = True

    def _ask_args(self, prompt: str, schema: dict[str, Any] | None, scratch: Path) -> list[str]:
        return [
            "-p",
            "--output-format",
            "json",
            "--trust",
            "--mode",
            "ask",
            "--workspace",
            str(scratch),
            *self._model(),
            prompt,
        ]

    def _task_args(self, prompt: str, workdir: Path) -> list[str]:
        return [
            "-p",
            "--output-format",
            "json",
            "--trust",
            "--force",
            "--workspace",
            str(workdir),
            *self._model(),
            prompt,
        ]

    def _parse(self, stdout: str, cwd: Path | None) -> Reply:
        out = _json_object(stdout)
        if out.get("is_error"):
            raise AgentError(f"cursor reported an error: {str(out.get('result'))[:200]}")
        text = str(out.get("result") or "")
        return Reply(text, extract_json(text), 0.0, session_id=out.get("session_id"))

    def _model(self) -> list[str]:
        return ["--model", self.model] if self.model else []


@dataclass
class OpenCode(Agent):
    name: str = "opencode"
    label: str = "OpenCode"
    binaries: tuple[str, ...] = ("opencode",)
    tested: bool = True

    def _ask_args(self, prompt: str, schema: dict[str, Any] | None, scratch: Path) -> list[str]:
        return [
            "run",
            "--format",
            "json",
            "--agent",
            "plan",
            "--dir",
            str(scratch),
            *self._model(),
            prompt,
        ]

    def _task_args(self, prompt: str, workdir: Path) -> list[str]:
        return ["run", "--format", "json", "--dir", str(workdir), *self._model(), prompt]

    def _parse(self, stdout: str, cwd: Path | None) -> Reply:
        texts, cost, session_id = [], 0.0, None
        for line in stdout.splitlines():
            event = _maybe_json(line)
            if not isinstance(event, dict):
                continue
            session_id = session_id or event.get("sessionID")
            part = event.get("part") or {}
            if event.get("type") == "text":
                texts.append(str(part.get("text", "")))
            elif event.get("type") == "step_finish":
                cost += float(part.get("cost") or 0)
        text = "".join(texts).strip()
        return Reply(text, extract_json(text), 0.0, cost_usd=cost, session_id=session_id)

    def _model(self) -> list[str]:
        return ["-m", self.model] if self.model else []


@dataclass
class Gemini(Agent):
    name: str = "gemini"
    label: str = "Gemini CLI"
    binaries: tuple[str, ...] = ("gemini",)

    def _ask_args(self, prompt: str, schema: dict[str, Any] | None, scratch: Path) -> list[str]:
        return ["-p", prompt, "--output-format", "json", *self._model()]

    def _task_args(self, prompt: str, workdir: Path) -> list[str]:
        return ["-p", prompt, "--output-format", "json", "--yolo", *self._model()]

    def _parse(self, stdout: str, cwd: Path | None) -> Reply:
        out = _maybe_json(stdout.strip())
        text = str(out.get("response", "")) if isinstance(out, dict) else stdout.strip()
        return Reply(text, extract_json(text), 0.0)

    def _model(self) -> list[str]:
        return ["-m", self.model] if self.model else []


@dataclass
class Grok(Agent):
    name: str = "grok"
    label: str = "Grok CLI"
    binaries: tuple[str, ...] = ("grok",)

    def _ask_args(self, prompt: str, schema: dict[str, Any] | None, scratch: Path) -> list[str]:
        return ["-p", prompt, *self._model()]

    def _task_args(self, prompt: str, workdir: Path) -> list[str]:
        return ["-p", prompt, "-d", str(workdir), *self._model()]

    def _model(self) -> list[str]:
        return ["-m", self.model] if self.model else []


BACKENDS: dict[str, type[Agent]] = {
    "claude": Claude,
    "codex": Codex,
    "cursor": Cursor,
    "opencode": OpenCode,
    "gemini": Gemini,
    "grok": Grok,
}


@dataclass(frozen=True)
class Detected:
    name: str
    label: str
    path: str
    version: str
    tested: bool


def detect(
    which: Callable[[str], str | None] = shutil.which, exec: Exec = _subprocess
) -> list[Detected]:
    """Agent CLIs on PATH, in BACKENDS order."""
    found = []
    for name, cls in BACKENDS.items():
        agent = cls()
        path = next((p for b in agent.binaries if (p := which(b))), None)
        if path is None:
            continue
        try:
            out = exec([path, "--version"], None, 15)
            version = (
                (out.stdout or out.stderr).strip().splitlines()[0] if out.returncode == 0 else "?"
            )
        except (subprocess.TimeoutExpired, OSError, IndexError):
            version = "?"
        found.append(Detected(name, agent.label, path, version, agent.tested))
    return found


def get(name: str, *, model: str | None = None, exec: Exec = _subprocess) -> Agent:
    try:
        cls = BACKENDS[name]
    except KeyError:
        raise AgentError(f"unknown agent {name!r}; choose from {', '.join(BACKENDS)}") from None
    agent = cls(model=model, exec=exec)
    agent.path = next((p for b in agent.binaries if (p := shutil.which(b))), None)
    return agent


def extract_json(text: str) -> dict[str, Any] | None:
    """The first JSON object in a reply, fenced or bare."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates: Sequence[str] = [fenced.group(1)] if fenced else []
    for candidate in [*candidates, text]:
        start = candidate.find("{")
        while start != -1:
            try:
                value, _ = json.JSONDecoder().raw_decode(candidate[start:])
            except json.JSONDecodeError:
                start = candidate.find("{", start + 1)
                continue
            if isinstance(value, dict):
                return value
            start = candidate.find("{", start + 1)
    return None


def _schema_instructions(schema: dict[str, Any]) -> str:
    return (
        "Reply with a single JSON object and nothing else, no prose and no code fence. "
        f"It must validate against this JSON Schema:\n{json.dumps(schema)}"
    )


def _maybe_json(line: str) -> Any:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _json_object(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.strip().splitlines()):
        value = _maybe_json(line)
        if isinstance(value, dict):
            return value
    value = _maybe_json(stdout)
    if isinstance(value, dict):
        return value
    raise AgentError("expected a JSON object on stdout")
