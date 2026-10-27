"""Replay a case with real agents, and let an agent propose instruction edits.

`WorktreeRunner` is the `climb.Runner` for real runs:

1. `git worktree add --detach` a throwaway checkout of the case's repo at `case.base`.
2. Copy the candidate instructions directory over it. The directory mirrors a project
   root, so `CLAUDE.md`, `AGENTS.md` or `.claude/skills/<name>/SKILL.md` inside it land
   where the agent will load them as project instructions.
3. Run the agent headless on `case.prompt` in that worktree.
4. Find the transcript the agent just wrote and parse it, so the case's checks grade the
   replay exactly the way they graded the original session.

`AgentProposer` is the `climb.Proposer`: it copies the instructions directory, shows an
agent the failing replays, and lets it edit the copy.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from retro.agents import Agent, AgentError
from retro.cases import Case, grade
from retro.climb import Replay
from retro.friction import detect
from retro.model import Session
from retro.redact import excerpt
from retro.sources import SOURCES


class ReplayError(RuntimeError):
    pass


class WorktreeRunner:
    def __init__(
        self,
        agent: Agent,
        repo: Path | None = None,
        keep: bool = False,
        locate: Callable[[str, str | None, Path, datetime], Session] | None = None,
    ) -> None:
        self.agent = agent
        self.repo = repo
        self.keep = keep
        self.locate = locate or find_session
        self.worktrees: list[tuple[Path, Path]] = []  # (repo, worktree)

    def run(self, case: Case, instructions: Path, seed: int) -> Replay:
        repo = Path(case.repo).expanduser() if case.repo else self.repo
        if repo is None:
            raise ReplayError(f"case {case.id} has no repo; set `repo` in the case or pass --repo")
        workdir = Path(tempfile.mkdtemp(prefix=f"retro-{case.id}-{seed}-"))
        workdir.rmdir()
        _git(repo, "worktree", "add", "--detach", str(workdir), case.base or "HEAD")
        self.worktrees.append((repo, workdir))
        if instructions.is_dir():
            shutil.copytree(instructions, workdir, dirs_exist_ok=True)
        started = datetime.now(UTC)
        reply = self.agent.run_task(case.prompt, workdir)
        session = self.locate(self.agent.name, reply.session_id, workdir, started)
        return Replay(session, workdir)

    def cleanup(self) -> None:
        if self.keep:
            return
        for repo, workdir in self.worktrees:
            _git(repo, "worktree", "remove", "--force", str(workdir), check=False)
        self.worktrees.clear()


def find_session(agent: str, session_id: str | None, workdir: Path, since: datetime) -> Session:
    """The transcript a headless run just wrote, by session id, else by cwd and time."""
    source_name = {"claude": "claude-code"}.get(agent, agent)
    source = next((s for s in SOURCES if s.name == source_name), None)
    if source is None:
        raise ReplayError(f"retro can't read {agent} sessions yet, so it can't grade the replay")
    for session in source.iter_sessions(source.default_root, since):
        if session_id and (session.id == session_id or session_id in str(session.path)):
            return session
        if session.cwd and Path(session.cwd).resolve() == workdir.resolve():
            return session
    raise ReplayError(f"couldn't find the {agent} transcript for the replay in {workdir}")


class AgentProposer:
    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.candidates: list[Path] = []

    def propose(self, instructions: Path, failures: Sequence[tuple[Case, Replay]]) -> Path:
        candidate = Path(tempfile.mkdtemp(prefix="retro-candidate-"))
        shutil.copytree(instructions, candidate, dirs_exist_ok=True)
        self.candidates.append(candidate)
        self.agent.run_task(proposer_prompt(failures), candidate)
        return candidate


def proposer_prompt(failures: Sequence[tuple[Case, Replay]]) -> str:
    seen: dict[str, list[str]] = {}
    for case, replay in failures:
        notes = seen.setdefault(case.id, [f"Case {case.id}: {case.title}"])
        for result in grade(case, replay.session, workdir=replay.workdir):
            if result.verdict.value == "fail":
                notes.append(f"  failed: {result.check.label} ({result.reason})")
        for f in detect(replay.session)[:3]:
            notes.append(f"  friction: {excerpt(f.evidence, 160)}")
    body = "\n".join(line for notes in seen.values() for line in notes[:8])
    return f"""This directory holds the instructions a coding agent works from (CLAUDE.md, \
AGENTS.md and skills under .claude/skills/). Replays of past tasks show the agent still \
making these mistakes:

{body}

Edit the files in this directory so the agent stops making them. Make the smallest change \
that works: tighten or add a rule where the agent will see it at the moment it matters, and \
keep each file's voice. Don't delete existing rules, don't add files outside this \
directory, and don't touch anything that isn't an instruction file. When you're done, \
reply with one line per file you changed."""


def _git(repo: Path, *args: str, check: bool = True) -> None:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if check and proc.returncode != 0:
        raise ReplayError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()[:200]}")


__all__ = ["AgentError", "AgentProposer", "ReplayError", "WorktreeRunner", "find_session"]
