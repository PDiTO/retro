"""The prompts retro sends to agents, and how their answers become `Insights`.

Three passes over the friction from `retro scan`:

1. **Label.** Each event gets a category, a confidence and a one-line summary. With
   `--panel`, every detected agent labels every event independently, which shows how much
   the agents agree and gives a majority label that one opinionated model can't skew.
2. **Cluster.** The events that were real mistakes are grouped into recurring patterns,
   and each pattern is pinned on the skill or instruction file that should have stopped it.
3. **Propose.** For each pattern, the agent reads that file and writes the smallest edit
   that would prevent it, plus a draft regression check that would catch it next time.

Only redacted excerpts leave the machine, and only to the agent CLIs the user already
runs. The propose pass also sends the target instruction file, which the same agent
already reads on every session.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from retro.agents import Agent, AgentError
from retro.cases import input_text
from retro.friction import Friction
from retro.insights import Category, Insights, Label, Pattern, Proposal
from retro.model import EventKind, Session
from retro.redact import redact
from retro.skills import Instruction

Progress = Callable[[str], None]
BATCH = 25
MAX_TARGET_CHARS = 12_000

_CATEGORY_HELP = {
    Category.IGNORED_INSTRUCTION: "a skill, CLAUDE.md/AGENTS.md or the user already said otherwise",
    Category.SKIPPED_VERIFICATION: "claimed done without running tests, a build or a check",
    Category.WRONG_APPROACH: "misread the task or picked a poor design",
    Category.SCOPE_CREEP: "did more than asked or touched unrelated things",
    Category.RISKY_ACTION: "destructive or outward-facing step without asking",
    Category.TOOL_MISUSE: "wrong tool, bad arguments, or retried a failing call blindly",
    Category.STYLE: "tone, formatting, naming or house style",
    Category.NOT_A_MISTAKE: "the human answered a question or changed their mind, or it's noise",
}
CATEGORIES = [c.value for c in Category]

LABEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "confidence": {"type": "number"},
                    "summary": {"type": "string"},
                },
                "required": ["id", "category", "confidence", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["labels"],
    "additionalProperties": False,
}

PATTERN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "friction_ids": {"type": "array", "items": {"type": "string"}},
                    "skill": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": [
                    "id",
                    "title",
                    "description",
                    "category",
                    "friction_ids",
                    "skill",
                    "target",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["patterns"],
    "additionalProperties": False,
}

_CHECK_FIELDS = ("type", "tool", "pattern", "before", "where", "run", "rubric", "description")
PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "change": {"type": "string"},
        "rationale": {"type": "string"},
        "check": {
            "type": "object",
            "properties": {
                **{k: {"type": "string"} for k in _CHECK_FIELDS},
                "limit": {"type": "integer"},
            },
            "required": [*_CHECK_FIELDS, "limit"],
            "additionalProperties": False,
        },
    },
    "required": ["change", "rationale", "check"],
    "additionalProperties": False,
}


def label_prompt(items: Sequence[Friction]) -> str:
    categories = "\n".join(f"- {c.value}: {why}" for c, why in _CATEGORY_HELP.items())
    rows = [
        {
            "id": f.id,
            "signal": f.kind.value,
            "project": f.project,
            "skill_in_use": f.skill,
            "agent_did": f.context,
            "then": f.evidence,
            "detail": f.detail,
        }
        for f in items
    ]
    return f"""You are reviewing moments from a developer's coding-agent sessions where something \
may have gone wrong. Each item has the signal that flagged it (a human correction, an \
interrupt, a refused tool call, a run of failed tool calls, a PR review comment or a failed \
CI check), what the agent did just before, and what came next.

For every item, pick the one category that fits best:
{categories}

Give a confidence between 0 and 1, and a summary: one plain sentence, under 20 words, \
saying what the agent got wrong (or why it wasn't a mistake). Don't quote secrets, \
tokens, emails or personal names. Return one label per item, using the item's id.

Items:
{json.dumps(rows, indent=1)}"""


def cluster_prompt(
    items: Sequence[tuple[Friction, Label]], inventory: Sequence[Instruction]
) -> str:
    rows = [
        {
            "id": f.id,
            "category": lab.category.value,
            "summary": lab.summary,
            "skill_in_use": f.skill,
            "project": f.project,
        }
        for f, lab in items
    ]
    files = [
        {"name": i.name, "kind": i.kind, "path": i.display_path, "description": i.description[:160]}
        for i in inventory
    ]
    return f"""These are mistakes a coding agent made across many sessions with one developer. \
Group them into recurring patterns: the same underlying behaviour showing up more than once. \
A pattern needs at least two items, except risky_action, where one is enough. Leave out \
items that don't fit any pattern.

For each pattern give: a short kebab-case id, a title (under 8 words), a two-sentence \
description of the behaviour, its category, the ids of its items, the skill most involved \
(a name from the list below, or "" if none), and the target: the path of the one skill or \
instruction file below whose wording should change to prevent it (or "" if no file fits). \
Prefer the skill that was in use; use a global instruction file for habits that cut across skills.

Mistakes:
{json.dumps(rows, indent=1)}

Skills and instruction files:
{json.dumps(files, indent=1)}"""


def propose_prompt(pattern: Pattern, examples: Sequence[Friction], target: str, text: str) -> str:
    shown = [{"agent_did": f.context, "then": f.evidence} for f in examples[:6]]
    return f"""A coding agent keeps making the same mistake, and the instruction file below is \
where the fix belongs.

Pattern: {pattern.title}
{pattern.description}

Examples:
{json.dumps(shown, indent=1)}

Write the smallest edit to {target} that would stop this. Keep the file's voice and \
structure, and don't restate rules it already has; if it already covers this, make the rule \
harder to miss instead. Return:
- change: a unified diff against the file (--- a/{target} / +++ b/{target}, with @@ hunks)
- rationale: two sentences on why this wording should work
- check: a regression check that would FAIL on a session showing this mistake. type is one \
of forbid_tool, require_tool, forbid_text, max_errors, judge. Fill the fields that type uses \
(tool: exact tool name like Bash; pattern and before: Python regexes over the command; \
where: assistant, writes or all; limit: integer; rubric: what a grader should look for) \
and set the rest to "" or 0. description: one line.

Current contents of {target}:
<file>
{text}
</file>"""


def label(
    friction: Sequence[Friction], agent: Agent, progress: Progress = lambda _: None
) -> list[Label]:
    labels: list[Label] = []
    known = {f.id for f in friction}
    for start in range(0, len(friction), BATCH):
        batch = friction[start : start + BATCH]
        progress(f"{agent.name}: labelling {start + 1}-{start + len(batch)} of {len(friction)}")
        reply = agent.ask(label_prompt(batch), LABEL_SCHEMA)
        for row in (reply.data or {}).get("labels", []):
            if row.get("id") not in known or row.get("category") not in CATEGORIES:
                continue
            labels.append(
                Label(
                    friction_id=row["id"],
                    agent=agent.name,
                    category=Category(row["category"]),
                    confidence=min(1.0, max(0.0, float(row.get("confidence", 0.5)))),
                    summary=redact(str(row.get("summary", "")))[:200],
                )
            )
    return labels


def consensus(labels: Sequence[Label], order: Sequence[str]) -> dict[str, Label]:
    """One label per friction event: the majority category, ties broken by agent order."""
    by_id: dict[str, list[Label]] = {}
    for lab in labels:
        by_id.setdefault(lab.friction_id, []).append(lab)
    rank = {name: i for i, name in enumerate(order)}
    out = {}
    for fid, group in by_id.items():
        votes = Counter(lab.category for lab in group)
        top = max(votes.values())
        winners = {c for c, n in votes.items() if n == top}
        pick = min(
            (lab for lab in group if lab.category in winners), key=lambda x: rank.get(x.agent, 99)
        )
        agreement = top / len(group)
        out[fid] = replace(pick, confidence=round(pick.confidence * agreement, 3))
    return out


def cluster(
    friction: Sequence[Friction],
    labels: dict[str, Label],
    inventory: Sequence[Instruction],
    agent: Agent,
    progress: Progress = lambda _: None,
) -> list[Pattern]:
    items = [
        (f, labels[f.id])
        for f in friction
        if f.id in labels and labels[f.id].category is not Category.NOT_A_MISTAKE
    ]
    if not items:
        return []
    progress(f"{agent.name}: grouping {len(items)} mistakes into patterns")
    reply = agent.ask(cluster_prompt(items, inventory), PATTERN_SCHEMA)
    valid = {f.id for f, _ in items}
    paths = {i.display_path for i in inventory}
    patterns = []
    for row in (reply.data or {}).get("patterns", []):
        ids = [i for i in row.get("friction_ids", []) if i in valid]
        if not ids or row.get("category") not in CATEGORIES:
            continue
        target = row.get("target") or None
        patterns.append(
            Pattern(
                id=str(row["id"])[:60],
                title=redact(str(row["title"]))[:100],
                description=redact(str(row["description"]))[:500],
                friction_ids=ids,
                category=Category(row["category"]),
                skill=row.get("skill") or None,
                target=target if target in paths else None,
            )
        )
    return sorted(patterns, key=lambda p: -len(p.friction_ids))


def propose(
    patterns: Sequence[Pattern],
    friction: Sequence[Friction],
    inventory: Sequence[Instruction],
    agent: Agent,
    progress: Progress = lambda _: None,
) -> list[Proposal]:
    files = {i.display_path: i.path for i in inventory}
    by_id = {f.id: f for f in friction}
    proposals = []
    for pattern in patterns:
        if not pattern.target or pattern.target not in files:
            continue
        progress(f"{agent.name}: proposing a change to {pattern.target} for {pattern.id}")
        text = _read(files[pattern.target])
        examples = [by_id[i] for i in pattern.friction_ids if i in by_id]
        try:
            reply = agent.ask(
                propose_prompt(pattern, examples, pattern.target, text), PROPOSAL_SCHEMA
            )
        except AgentError as e:
            progress(f"skipped {pattern.id}: {e}")
            continue
        data = reply.data or {}
        check = {k: v for k, v in (data.get("check") or {}).items() if v not in ("", 0, None)}
        proposals.append(
            Proposal(
                pattern_id=pattern.id,
                target=pattern.target,
                change=str(data.get("change", "")),
                rationale=redact(str(data.get("rationale", ""))),
                check=check,
                agent=agent.name,
            )
        )
    return proposals


def analyze(
    insights: Insights,
    panel: Sequence[Agent],
    inventory: Sequence[Instruction],
    *,
    limit: int | None = None,
    propose_changes: bool = True,
    progress: Progress = lambda _: None,
) -> Insights:
    """Label with every agent in `panel`; cluster and propose with the first."""
    friction = sorted(
        insights.friction,
        key=lambda f: f.timestamp.isoformat() if f.timestamp else "",
        reverse=True,
    )[: limit or None]
    labels: list[Label] = []
    for agent in panel:
        try:
            labels.extend(label(friction, agent, progress))
        except AgentError as e:
            progress(f"{agent.name} dropped from the panel: {e}")
    lead = panel[0]
    agreed = consensus(labels, [a.name for a in panel])
    patterns = cluster(friction, agreed, inventory, lead, progress)
    proposals = propose(patterns, friction, inventory, lead, progress) if propose_changes else []
    return replace(insights, labels=labels, patterns=patterns, proposals=proposals)


def _read(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    return text if len(text) <= MAX_TARGET_CHARS else text[:MAX_TARGET_CHARS] + "\n[truncated]"


JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"passed": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["passed", "reason"],
    "additionalProperties": False,
}


def transcript_digest(session: Session, limit: int = 15_000) -> str:
    """A compact, redacted rendering of a session for a judge to read."""
    lines = []
    for e in session.events:
        if e.kind is EventKind.PROMPT:
            lines.append(f"[{e.index}] HUMAN: {e.text}")
        elif e.kind is EventKind.ASSISTANT:
            lines.append(f"[{e.index}] AGENT: {e.text}")
        elif e.kind is EventKind.TOOL_CALL:
            lines.append(f"[{e.index}] TOOL {e.tool}: {input_text(e)[:300]}")
        elif e.kind is EventKind.TOOL_RESULT and e.is_error:
            lines.append(f"[{e.index}] TOOL ERROR: {e.text[:200]}")
        elif e.kind in (EventKind.INTERRUPT, EventKind.DENIAL):
            lines.append(f"[{e.index}] {e.kind.value.upper()}")
    text = redact("\n".join(lines))
    return text if len(text) <= limit else text[: limit // 3] + "\n…\n" + text[-2 * limit // 3 :]


def judge_with(agent: Agent) -> Callable[[str, Session], tuple[bool, str]]:
    """A `retro.cases.Judge` backed by a headless agent."""

    def judge(rubric: str, session: Session) -> tuple[bool, str]:
        prompt = f"""Grade one coding-agent session against a rubric. Pass it only if the \
session clearly meets the rubric; if the evidence is missing, fail it. Give a one-sentence reason \
that cites event numbers.

Rubric: {rubric}

Session:
{transcript_digest(session)}"""
        data = agent.ask(prompt, JUDGE_SCHEMA).data or {}
        return bool(data.get("passed")), redact(str(data.get("reason", "")))[:300]

    return judge
