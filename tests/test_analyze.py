from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from retro import analyze
from retro.agents import Agent, AgentError, Reply
from retro.friction import detect
from retro.insights import Category, Insights, Label
from retro.model import Session
from retro.skills import Instruction


class ScriptedAgent(Agent):
    """Answers each prompt by kind, recording what it was asked."""

    def __init__(self, name: str, labels: dict[str, str], fail: bool = False) -> None:
        super().__init__(name=name)
        self.labels = labels
        self.fail = fail
        self.prompts: list[str] = []

    def ask(
        self, prompt: str, schema: dict[str, Any] | None = None, *, timeout: float = 600
    ) -> Reply:
        self.prompts.append(prompt)
        if self.fail:
            raise AgentError(f"{self.name} is down")
        if schema is analyze.LABEL_SCHEMA:
            rows = [
                {"id": fid, "category": cat, "confidence": 0.8, "summary": f"{fid} went wrong"}
                for fid, cat in self.labels.items()
                if fid in prompt
            ]
            rows.append({"id": "made-up:1", "category": "style", "confidence": 1, "summary": "x"})
            return Reply("", {"labels": rows}, 0.0)
        if schema is analyze.PATTERN_SCHEMA:
            ids = [fid for fid, cat in self.labels.items() if cat != "not_a_mistake"]
            pattern = {
                "id": "commits-before-tests",
                "title": "Commits before testing",
                "description": "Runs git commit without the tests.",
                "category": "skipped_verification",
                "friction_ids": [*ids, "unknown:9"],
                "skill": "git-stage",
                "target": "~/skills/git-stage/SKILL.md",
            }
            stray = {**pattern, "id": "nothing", "friction_ids": ["unknown:9"]}
            return Reply("", {"patterns": [pattern, stray]}, 0.0)
        if schema is analyze.PROPOSAL_SCHEMA:
            check = {
                "type": "require_tool",
                "tool": "Bash",
                "pattern": "pytest",
                "before": "git commit",
                "where": "",
                "run": "",
                "rubric": "",
                "description": "tests first",
                "limit": 0,
            }
            return Reply(
                "",
                {
                    "change": "+Run the tests before committing.",
                    "rationale": "Because.",
                    "check": check,
                },
                0.0,
            )
        if schema is analyze.JUDGE_SCHEMA:
            return Reply("", {"passed": "--force" not in prompt, "reason": "saw event 5"}, 0.0)
        raise AssertionError("unexpected schema")


@pytest.fixture
def insights(mistake: Session) -> Insights:
    return Insights(datetime.now(UTC), 30, ["claude"], [], detect(mistake))


@pytest.fixture
def inventory(tmp_path: Path) -> list[Instruction]:
    path = tmp_path / "SKILL.md"
    path.write_text("---\nname: git-stage\n---\nCommit and push.\n")
    return [Instruction("skill", "git-stage", "commit things", path, ("claude",))]


def patch_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        Instruction, "display_path", property(lambda self: "~/skills/git-stage/SKILL.md")
    )


LABELS = {
    "5e55i0n0:7": "skipped_verification",
    "5e55i0n0:9": "tool_misuse",
    "5e55i0n0:15": "risky_action",
    "5e55i0n0:16": "not_a_mistake",
}


def test_full_pass(
    insights: Insights, inventory: list[Instruction], monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_paths(monkeypatch)
    lead = ScriptedAgent("claude", LABELS)
    result = analyze.analyze(insights, [lead], inventory)

    assert {lab.friction_id for lab in result.labels} == set(LABELS)  # made-up id dropped
    [pattern] = result.patterns  # the pattern with only unknown ids is dropped
    assert pattern.friction_ids == ["5e55i0n0:7", "5e55i0n0:9", "5e55i0n0:15"]
    assert pattern.target == "~/skills/git-stage/SKILL.md"
    [proposal] = result.proposals
    assert proposal.check == {
        "type": "require_tool",
        "tool": "Bash",
        "pattern": "pytest",
        "before": "git commit",
        "description": "tests first",
    }
    # The propose prompt includes the target file's current text.
    assert "Commit and push." in lead.prompts[-1]
    # The label prompt carries redacted excerpts, never the session path.
    assert "fixtures" not in lead.prompts[0]


def test_panel_and_consensus(
    insights: Insights, inventory: list[Instruction], monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_paths(monkeypatch)
    second = ScriptedAgent("codex", {**LABELS, "5e55i0n0:7": "wrong_approach"})
    third = ScriptedAgent("cursor", {**LABELS, "5e55i0n0:7": "wrong_approach"})
    down = ScriptedAgent("opencode", LABELS, fail=True)
    lead = ScriptedAgent("claude", LABELS)
    messages: list[str] = []
    result = analyze.analyze(
        insights,
        [lead, second, third, down],
        inventory,
        propose_changes=False,
        progress=messages.append,
    )
    assert {lab.agent for lab in result.labels} == {"claude", "codex", "cursor"}
    assert any("opencode dropped" in m for m in messages)
    assert result.proposals == []
    agreed = analyze.consensus(result.labels, ["claude", "codex", "cursor"])
    assert agreed["5e55i0n0:7"].category is Category.WRONG_APPROACH  # 2 of 3
    assert agreed["5e55i0n0:7"].confidence == pytest.approx(0.8 * 2 / 3, abs=1e-3)
    assert agreed["5e55i0n0:9"].agent == "claude"  # unanimous, lead wins the tie-break


def test_consensus_tie_goes_to_lead() -> None:
    labels = [
        Label("a:1", "codex", Category.STYLE, 1.0, ""),
        Label("a:1", "claude", Category.SCOPE_CREEP, 1.0, ""),
    ]
    assert analyze.consensus(labels, ["claude", "codex"])["a:1"].category is Category.SCOPE_CREEP


def test_nothing_to_cluster(insights: Insights, inventory: list[Instruction]) -> None:
    lead = ScriptedAgent("claude", dict.fromkeys(LABELS, "not_a_mistake"))
    result = analyze.analyze(insights, [lead], inventory)
    assert result.patterns == []
    assert len(lead.prompts) == 1  # labelling only


def test_judge(mistake: Session, fixed: Session) -> None:
    judge = analyze.judge_with(ScriptedAgent("claude", {}))
    assert judge("ran tests", fixed) == (True, "saw event 5")
    assert judge("ran tests", mistake)[0] is False


def test_transcript_digest_is_redacted_and_capped(mistake: Session) -> None:
    digest = analyze.transcript_digest(mistake)
    assert "[0] HUMAN: Add a --verbose flag" in digest
    assert "TOOL Bash: git push --force" in digest
    assert "/home/dev" not in digest
    assert len(analyze.transcript_digest(mistake, limit=300)) < 400
