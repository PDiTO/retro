from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from retro import skills
from retro.agents import Agent, Reply
from retro.cases import Case, Check
from retro.climb import Replay
from retro.model import Session
from retro.replay import AgentProposer, ReplayError, WorktreeRunner, proposer_prompt


def write_skill(root: Path, name: str, description: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n")
    return path


def test_inventory_dedupes_symlinked_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shared = tmp_path / "shared"
    write_skill(shared, "git-ship", "Ship a branch.")
    claude = tmp_path / "claude"
    claude.mkdir()
    (claude / "git-ship").symlink_to(shared / "git-ship")
    write_skill(claude / ".trash", "old", "gone")
    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("\n# House rules\n- run tests\n")
    project = tmp_path / "proj"
    write_skill(project / ".claude" / "skills", "deploy", "Deploy it.")
    (project / "AGENTS.md").write_text("Project rules\n")

    monkeypatch.setattr(skills, "SKILL_DIRS", {"claude": [claude], "shared": [shared]})
    monkeypatch.setattr(skills, "INSTRUCTION_FILES", {"claude": [claude_md]})
    items = skills.inventory([project])

    assert [(i.kind, i.name, i.agents) for i in items] == [
        ("file", "CLAUDE.md", ("claude",)),
        ("file", "proj/AGENTS.md", ("project",)),
        ("skill", "deploy", ("project",)),
        ("skill", "git-ship", ("claude", "shared")),
    ]
    assert items[0].description == "House rules"
    assert items[3].description == "Ship a branch."


def test_front_matter_edge_cases(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text("no front matter\n")
    assert skills.front_matter(path) == {}
    path.write_text('---\nname: "quoted"\ndescription: a: b\n  nested: x\n---\n')
    assert skills.front_matter(path) == {"name": "quoted", "description": "a: b"}
    assert skills.front_matter(tmp_path / "missing.md") == {}


class WritingAgent(Agent):
    """Pretends to be a headless agent: records the task, writes a file in the workdir."""

    def __init__(self) -> None:
        super().__init__(name="claude")
        self.tasks: list[tuple[str, Path]] = []

    def run_task(self, prompt: str, workdir: Path, *, timeout: float = 1800) -> Reply:
        self.tasks.append((prompt, workdir))
        (workdir / "touched.txt").write_text("hi")
        return Reply("ok", None, 0.0, session_id="sess-1")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t" + "@" + "example.org"],
        ["config", "user.name", "t"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True)
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)
    return path


def test_worktree_runner(repo: Path, tmp_path: Path, fixed: Session) -> None:
    instructions = tmp_path / "instructions"
    (instructions / ".claude" / "skills" / "x").mkdir(parents=True)
    (instructions / "CLAUDE.md").write_text("rules\n")
    (instructions / ".claude" / "skills" / "x" / "SKILL.md").write_text("skill\n")
    seen: list[tuple[str, str | None]] = []

    def locate(agent: str, session_id: str | None, workdir: Path, since: datetime) -> Session:
        seen.append((agent, session_id))
        return fixed

    agent = WritingAgent()
    runner = WorktreeRunner(agent, repo, locate=locate)
    case = Case("c", "t", "Do the task", [Check("max_errors")])
    replay = runner.run(case, instructions, seed=0)

    assert replay.session is fixed
    assert replay.workdir is not None
    assert (replay.workdir / "README.md").read_text() == "hello\n"
    assert (replay.workdir / "CLAUDE.md").read_text() == "rules\n"
    assert (replay.workdir / ".claude" / "skills" / "x" / "SKILL.md").exists()
    assert agent.tasks == [("Do the task", replay.workdir)]
    assert seen == [("claude", "sess-1")]

    runner.cleanup()
    assert not replay.workdir.exists()
    assert (repo / "README.md").exists()


def test_runner_needs_a_repo(tmp_path: Path) -> None:
    runner = WorktreeRunner(WritingAgent())
    with pytest.raises(ReplayError, match="has no repo"):
        runner.run(Case("c", "t", "p", [Check("max_errors")]), tmp_path, 0)


def test_proposer_copies_and_prompts(tmp_path: Path, mistake: Session) -> None:
    instructions = tmp_path / "instructions"
    instructions.mkdir()
    (instructions / "CLAUDE.md").write_text("rules\n")
    agent = WritingAgent()
    case = Case(
        "tests-first",
        "Commits before tests",
        "p",
        [Check("require_tool", pattern="pytest", before="git commit")],
    )
    candidate = AgentProposer(agent).propose(instructions, [(case, Replay(mistake))])

    assert candidate != instructions
    assert (candidate / "CLAUDE.md").read_text() == "rules\n"
    assert (candidate / "touched.txt").exists()
    assert not (instructions / "touched.txt").exists()
    prompt, workdir = agent.tasks[0]
    assert workdir == candidate
    assert "Case tests-first: Commits before tests" in prompt
    assert "failed: require_tool pytest before git commit" in prompt


def test_proposer_prompt_dedupes_cases(mistake: Session) -> None:
    case = Case("a", "A", "p", [Check("max_errors", limit=0)])
    prompt = proposer_prompt([(case, Replay(mistake)), (case, Replay(mistake))])
    assert prompt.count("Case a: A") == 1
