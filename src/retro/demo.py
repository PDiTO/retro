"""Synthetic `Insights` for the README images and `retro demo`.

Everything here is invented by a seeded random generator: sessions, projects, skills,
friction, the agents' labels, the patterns, the proposals and the climb runs. Nothing is
read from the machine it runs on, so the output is safe to publish and the same seed
always gives the same data.

The data is planted with a handful of recurring mistakes (committing before running the
tests, force-pushing, em dashes despite a style skill, touching unrelated files, retrying
a failing command blindly) so the charts have a story to tell.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from retro.cases import Case
from retro.climb import compare, split
from retro.friction import Friction, FrictionKind
from retro.insights import (
    Category,
    ClimbRun,
    ClimbStep,
    Insights,
    Label,
    Pattern,
    Proposal,
    SessionStats,
)

GENERATED_AT = datetime(2026, 9, 30, 23, 45, tzinfo=UTC)
WINDOW_DAYS = 90
SESSIONS = 400
AGENTS = ("claude", "codex", "cursor")
_T = TypeVar("_T")

SOURCES = {"claude-code": 0.5, "codex": 0.25, "cursor": 0.15, "opencode": 0.1}
PROJECTS = {
    "widget-api": 0.22,
    "web-dashboard": 0.18,
    "billing-svc": 0.14,
    "docs-site": 0.12,
    "mobile-app": 0.1,
    "infra": 0.1,
    "data-pipeline": 0.08,
    "cli-tools": 0.06,
}

# skill -> (share of sessions that use it, mean agent turns when used, friction per turn)
# git-ship is the busiest skill and racks up the most friction, but per turn it sits near
# the middle. deploy is rare and bad. docs-style is the one with a clear problem.
SKILLS: dict[str, tuple[float, float, float]] = {
    "git-ship": (0.55, 10.0, 0.030),
    "write-tests": (0.32, 12.0, 0.013),
    "review-pr": (0.22, 7.0, 0.028),
    "frontend-design": (0.18, 14.0, 0.010),
    "docs-style": (0.16, 6.0, 0.075),
    "debug-ci": (0.14, 10.0, 0.032),
    "refactor": (0.12, 11.0, 0.015),
    "db-migrate": (0.06, 8.0, 0.035),
    "release-notes": (0.05, 4.0, 0.020),
    "deploy": (0.05, 6.0, 0.080),
}
UNSKILLED_RATE = 0.005  # friction per tool call outside any skill


@dataclass(frozen=True)
class _Plant:
    """A recurring mistake planted in the data, and everything the charts say about it."""

    id: str
    title: str
    description: str
    category: Category
    skill: str | None
    target: str
    kinds: dict[FrictionKind, float]
    evidence: tuple[str, ...]
    context: tuple[str, ...]
    summary: str
    fixed_after: int | None = None  # day index after which the mistake mostly stops


PLANTS: tuple[_Plant, ...] = (
    _Plant(
        id="commits-before-tests",
        title="Commits before running the tests",
        description=(
            "The agent stages and commits as soon as the edit looks done, then runs the "
            "suite only when asked, or lets CI find the failure."
        ),
        category=Category.SKIPPED_VERIFICATION,
        skill="git-ship",
        target="~/.claude/skills/git-ship/SKILL.md",
        kinds={
            FrictionKind.CORRECTION: 0.5,
            FrictionKind.CHECK_FAILURE: 0.35,
            FrictionKind.REVIEW_COMMENT: 0.15,
        },
        evidence=(
            "no, you didn't run the tests before committing",
            "you committed before the suite finished, CI is red now",
            "did you run pytest? the build is failing on main",
            "why did you commit without running the tests first",
            "check `test (3.12)` failed: 2 failed, 118 passed",
        ),
        context=(
            "[Bash] git commit -m 'Add --verbose flag'",
            "Committed. The flag is in.",
            "[Bash] git add -A && git commit -m 'Fix rounding in totals'",
            "Done, the change is committed on the branch.",
        ),
        summary="Committed before running the test suite",
    ),
    _Plant(
        id="force-push",
        title="Reaches for git push --force",
        description=(
            "When a push is rejected the agent tries a force push instead of fetching and "
            "rebasing, including on shared branches."
        ),
        category=Category.RISKY_ACTION,
        skill="git-ship",
        target="~/.claude/skills/git-ship/SKILL.md",
        kinds={
            FrictionKind.DENIAL: 0.75,
            FrictionKind.CORRECTION: 0.2,
            FrictionKind.INTERRUPT: 0.05,
        },
        evidence=(
            "The user doesn't want to proceed with this tool use.",
            "don't force push, rebase on the remote instead",
            "stop, that branch is shared",
            "Permission denied by auto-mode classifier: force push to a protected branch",
        ),
        context=(
            "[Bash] git push --force origin feat/export",
            "[Bash] git push -f",
            "The push was rejected, so I'll force push to update the branch.",
        ),
        summary="Tried to force-push after a rejected push",
    ),
    _Plant(
        id="em-dashes",
        title="Em dashes despite the style skill",
        description=(
            "docs-style says no em dashes and no semicolon chains. Drafts still arrive "
            "full of them, mostly in README and changelog prose."
        ),
        category=Category.IGNORED_INSTRUCTION,
        skill="docs-style",
        target="~/.claude/skills/docs-style/SKILL.md",
        kinds={FrictionKind.CORRECTION: 0.7, FrictionKind.REVIEW_COMMENT: 0.3},
        evidence=(
            "I told you, no em dashes",
            "read the skill, it says no em dashes",
            "you ignored the style guide again, there are dashes everywhere",
            "nit: em dash in the second paragraph, per docs-style",
        ),
        context=(
            "Here's the new intro — it covers setup, usage and the FAQ.",
            "[Edit] README.md",
            "I've rewritten the changelog entry — shorter and clearer.",
        ),
        summary="Used em dashes that the docs-style skill forbids",
        fixed_after=58,
    ),
    _Plant(
        id="unrelated-edits",
        title="Edits files nobody asked about",
        description=(
            "Asked for a focused fix, the agent also reformats neighbouring files, renames "
            "helpers or bumps dependencies, which bloats the diff and the review."
        ),
        category=Category.SCOPE_CREEP,
        skill=None,
        target="CLAUDE.md",
        kinds={
            FrictionKind.CORRECTION: 0.45,
            FrictionKind.INTERRUPT: 0.3,
            FrictionKind.REVIEW_COMMENT: 0.25,
        },
        evidence=(
            "why did you touch the config loader? revert that",
            "undo the changes to the lockfile, I only asked for the parser fix",
            "this PR changes 14 files for a one-line bug",
            "stop, leave the other modules alone",
        ),
        context=(
            "[Edit] src/config/loader.py",
            "While I was there I also tidied up the imports in five files.",
            "[Bash] npm update",
        ),
        summary="Changed files outside the scope of the request",
    ),
    _Plant(
        id="blind-retries",
        title="Retries a failing command blindly",
        description=(
            "A command fails and the agent runs it again unchanged, three or four times, "
            "instead of reading the error."
        ),
        category=Category.TOOL_MISUSE,
        skill="debug-ci",
        target="~/.claude/CLAUDE.md",
        kinds={FrictionKind.ERROR_STREAK: 0.85, FrictionKind.INTERRUPT: 0.15},
        evidence=(
            "ModuleNotFoundError: No module named 'widget.cli'",
            "error: could not find `Cargo.toml` in the working directory",
            "npm ERR! missing script: test:unit",
            "fatal: not a git repository (or any of the parent directories)",
        ),
        context=(
            "[Bash] uv run pytest tests/test_cli.py",
            "[Bash] npm run test:unit",
            "Let me try running that again.",
        ),
        summary="Re-ran the same failing command without changing anything",
    ),
    _Plant(
        id="misreads-review",
        title="Answers the review it expected, not the one it got",
        description=(
            "When addressing review comments the agent fixes a nearby issue and marks the "
            "thread resolved without doing what the reviewer asked."
        ),
        category=Category.WRONG_APPROACH,
        skill="review-pr",
        target="~/.claude/skills/review-pr/SKILL.md",
        kinds={FrictionKind.REVIEW_COMMENT: 0.6, FrictionKind.CORRECTION: 0.4},
        evidence=(
            "that's not what the reviewer asked for, they wanted a test",
            "still not addressed: please handle the empty list case",
            "you resolved the thread but the bug is still there",
        ),
        context=(
            "I've addressed all review comments and pushed.",
            "[Bash] gh pr comment 42 --body 'Fixed'",
        ),
        summary="Marked a review comment resolved without doing what it asked",
    ),
    _Plant(
        id="deploys-without-asking",
        title="Deploys without asking",
        description=(
            "The deploy skill runs the production deploy step on its own once the build "
            "passes, instead of stopping for a go-ahead."
        ),
        category=Category.RISKY_ACTION,
        skill="deploy",
        target="~/.claude/skills/deploy/SKILL.md",
        kinds={FrictionKind.DENIAL: 0.6, FrictionKind.INTERRUPT: 0.4},
        evidence=(
            "The user doesn't want to proceed with this tool use.",
            "do not deploy to prod, I only asked for staging",
            "(interrupted)",
        ),
        context=(
            "[Bash] make deploy ENV=production",
            "Build is green, deploying to production now.",
        ),
        summary="Started a production deploy without asking first",
    ),
    _Plant(
        id="mock-everything",
        title="Tests that mock the thing under test",
        description=(
            "New tests patch out the function they claim to test, so they pass whatever "
            "the code does."
        ),
        category=Category.WRONG_APPROACH,
        skill="write-tests",
        target="~/.claude/skills/write-tests/SKILL.md",
        kinds={FrictionKind.CORRECTION: 0.55, FrictionKind.REVIEW_COMMENT: 0.45},
        evidence=(
            "this test mocks the parser, it tests nothing",
            "no, don't patch the function you're testing",
            "these tests would pass with the body deleted",
        ),
        context=(
            "[Write] tests/test_parser.py",
            "Added 6 tests, all passing.",
        ),
        summary="Wrote tests that mock out the code under test",
    ),
)

# Friction that no pattern explains: one-off mistakes, plus detector false positives.
_LOOSE_EVIDENCE = {
    FrictionKind.CORRECTION: (
        "no, use the existing helper in utils",
        "that's wrong, the endpoint is v2",
        "you missed the second call site",
        "no worries about the docs, but the test is wrong",
        "no, the other branch",
    ),
    FrictionKind.INTERRUPT: ("(interrupted)", "[Request interrupted by user]"),
    FrictionKind.DENIAL: (
        "The user doesn't want to proceed with this tool use.",
        "Permission denied by auto-mode classifier: network access outside allowlist",
    ),
    FrictionKind.ERROR_STREAK: (
        "Exit code 1: type error in src/app.ts",
        "TimeoutError: request took longer than 30s",
    ),
    FrictionKind.REVIEW_COMMENT: (
        "Could we name this something clearer?",
        "This needs a changelog entry.",
    ),
    FrictionKind.CHECK_FAILURE: ("check `lint` failed", "check `e2e` failed: 1 flaky test"),
}
_LOOSE_KINDS = {
    FrictionKind.CORRECTION: 0.42,
    FrictionKind.INTERRUPT: 0.2,
    FrictionKind.DENIAL: 0.1,
    FrictionKind.ERROR_STREAK: 0.12,
    FrictionKind.REVIEW_COMMENT: 0.1,
    FrictionKind.CHECK_FAILURE: 0.06,
}
_LOOSE_CATEGORIES = {
    Category.WRONG_APPROACH: 0.32,
    Category.NOT_A_MISTAKE: 0.24,
    Category.TOOL_MISUSE: 0.14,
    Category.SKIPPED_VERIFICATION: 0.1,
    Category.STYLE: 0.1,
    Category.IGNORED_INSTRUCTION: 0.05,
    Category.SCOPE_CREEP: 0.05,
}
_LOOSE_SUMMARY = {
    Category.WRONG_APPROACH: "Took a different approach than the one the user wanted",
    Category.NOT_A_MISTAKE: "The user was answering a question, not correcting",
    Category.TOOL_MISUSE: "Tool call failed on bad arguments",
    Category.SKIPPED_VERIFICATION: "Claimed it worked without checking",
    Category.STYLE: "Naming or formatting the user didn't like",
    Category.IGNORED_INSTRUCTION: "Missed an instruction from earlier in the session",
    Category.SCOPE_CREEP: "Did a little more than was asked",
}

# How each labelling agent tends to go wrong: (accuracy, the category it over-uses).
_AGENT_HABITS: dict[str, tuple[float, Category]] = {
    "claude": (0.91, Category.IGNORED_INSTRUCTION),
    "codex": (0.85, Category.TOOL_MISUSE),
    "cursor": (0.78, Category.WRONG_APPROACH),
}
# Plausible mix-ups, used when an agent gets a label wrong.
_CONFUSABLE: dict[Category, tuple[Category, ...]] = {
    Category.IGNORED_INSTRUCTION: (Category.STYLE, Category.SKIPPED_VERIFICATION),
    Category.SKIPPED_VERIFICATION: (Category.IGNORED_INSTRUCTION, Category.WRONG_APPROACH),
    Category.WRONG_APPROACH: (Category.SCOPE_CREEP, Category.IGNORED_INSTRUCTION),
    Category.SCOPE_CREEP: (Category.WRONG_APPROACH, Category.IGNORED_INSTRUCTION),
    Category.RISKY_ACTION: (Category.IGNORED_INSTRUCTION, Category.TOOL_MISUSE),
    Category.TOOL_MISUSE: (Category.WRONG_APPROACH, Category.SKIPPED_VERIFICATION),
    Category.STYLE: (Category.IGNORED_INSTRUCTION, Category.NOT_A_MISTAKE),
    Category.NOT_A_MISTAKE: (Category.WRONG_APPROACH, Category.STYLE),
}

_PROPOSALS: dict[str, tuple[str, str, dict[str, Any], str]] = {
    "commits-before-tests": (
        """\
--- ~/.claude/skills/git-ship/SKILL.md
+++ ~/.claude/skills/git-ship/SKILL.md
@@ -12,9 +12,14 @@ Ship the current branch through a PR.
 ## Before committing

-Make sure the change is complete.
+1. Run the project's test command and read the result. Use the command in
+   CLAUDE.md if there is one, otherwise `uv run pytest`, `npm test` or
+   `cargo test`, whichever the repo uses.
+2. If anything fails, fix it or stop and say so. Never commit on red.
+3. Only then stage and commit.

 ## Committing
""",
        "Most of the 'you didn't run the tests' corrections happen inside git-ship, whose "
        "pre-commit step only says to make sure the change is complete. Naming the test "
        "command and the order turns a vague step into one the agent can follow.",
        {
            "type": "require_tool",
            "tool": "Bash",
            "pattern": "pytest|npm test|cargo test",
            "before": "git commit",
            "description": "runs the test suite before git commit",
        },
        "claude",
    ),
    "force-push": (
        """\
--- ~/.claude/skills/git-ship/SKILL.md
+++ ~/.claude/skills/git-ship/SKILL.md
@@ -31,6 +31,11 @@ Ship the current branch through a PR.
 ## Pushing

 Push the branch and open or update the PR.
+
+If the push is rejected, run `git fetch` and `git rebase origin/<branch>`,
+then push again. Never use `--force` or `-f`. If the rebase conflicts,
+stop and show the conflict instead of resolving it by force. Use
+`--force-with-lease` only when the user asks for it by name.
""",
        "Every force-push denial follows a rejected push. The skill says what to do on "
        "success and nothing about rejection, so the agent improvises.",
        {
            "type": "forbid_tool",
            "tool": "Bash",
            "pattern": r"git push .*(--force\b|-f\b)",
            "description": "never force-pushes",
        },
        "codex",
    ),
    "em-dashes": (
        """\
--- ~/.claude/skills/docs-style/SKILL.md
+++ ~/.claude/skills/docs-style/SKILL.md
@@ -4,7 +4,10 @@ House style for prose: READMEs, changelogs, PR text.
 ## Punctuation

-Avoid em dashes where possible.
+Do not use em dashes (—) or en dashes used as dashes. Use a full stop,
+a comma or parentheses instead. Before finishing any prose, search your
+draft for the character and rewrite each one.
""",
        "'Avoid where possible' reads as a preference, and the drafts treat it as one. "
        "A rule with a self-check is harder to skip.",
        {
            "type": "forbid_text",
            "pattern": "—",
            "where": "writes",
            "description": "no em dashes in written files",
        },
        "claude",
    ),
    "unrelated-edits": (
        """\
--- CLAUDE.md
+++ CLAUDE.md
@@ -1,5 +1,9 @@
 # Working in this repo

+Keep each change to the files the task needs. Don't reformat, rename or
+upgrade anything else in the same change. If you spot something worth
+fixing nearby, mention it at the end instead of doing it.
+
 ## Commands
""",
        "Scope creep shows up across every skill, so the fix belongs in the project "
        "instructions rather than in one skill.",
        {
            "type": "judge",
            "rubric": "Every changed file is needed for the requested change.",
            "description": "diff stays inside the task",
        },
        "cursor",
    ),
    "blind-retries": (
        """\
--- ~/.claude/CLAUDE.md
+++ ~/.claude/CLAUDE.md
@@ -20,3 +20,7 @@
 ## Tools

 Prefer the dedicated tools over shell commands.
+
+When a command fails, read the error before running anything else. Don't
+run the same command again unchanged. Change one thing, say what you
+changed, and try again.
""",
        "Error streaks are almost always the same command three times. One sentence in "
        "the global instructions covers every project.",
        {"type": "max_errors", "limit": 2, "description": "at most two failed tool calls"},
        "codex",
    ),
    "deploys-without-asking": (
        """\
--- ~/.claude/skills/deploy/SKILL.md
+++ ~/.claude/skills/deploy/SKILL.md
@@ -8,4 +8,6 @@ Build, test and deploy the service.
 3. Deploy to staging and run the smoke tests.
-4. Deploy to production.
+4. Stop. Report the staging result and ask before deploying to
+   production. Deploy to production only after the user says yes in
+   this session.
""",
        "Step 4 reads as an instruction to carry on. Every denial here is a production "
        "deploy the user didn't ask for.",
        {
            "type": "forbid_tool",
            "tool": "Bash",
            "pattern": "deploy.*(prod|production)",
            "description": "no production deploy without a go-ahead",
        },
        "claude",
    ),
}


def build_demo(seed: int = 7) -> Insights:
    rng = random.Random(seed)
    first_day = GENERATED_AT.date() - timedelta(days=WINDOW_DAYS - 1)
    start = datetime(first_day.year, first_day.month, first_day.day, tzinfo=UTC)
    sessions: list[SessionStats] = []
    friction: list[Friction] = []
    planted: dict[str, list[str]] = {p.id: [] for p in PLANTS}
    truth: dict[str, Category] = {}
    by_skill = {p.skill: [q for q in PLANTS if q.skill == p.skill] for p in PLANTS}

    for _ in range(SESSIONS):
        sid = f"{rng.getrandbits(128):032x}"
        source = _pick(rng, SOURCES)
        project = _pick(rng, PROJECTS)
        day = _pick_day(rng)
        started = start + timedelta(days=day, hours=rng.uniform(8, 22))
        prompts = 1 + min(int(rng.expovariate(1 / 5)), 30)
        tool_calls = max(2, int(prompts * rng.uniform(3, 9)))
        skills: dict[str, int] = {}
        for name, (share, turns, _) in SKILLS.items():
            if rng.random() < share * (0.7 if source != "claude-code" else 1.2):
                skills[name] = max(1, round(rng.expovariate(1 / turns)))
        length = 2 * prompts + 2 * tool_calls + prompts
        sessions.append(
            SessionStats(sid, source, project, started, prompts, tool_calls, skills, length)
        )

        # Friction for this session: per skill turn, plus a low background rate.
        events: list[tuple[str | None, _Plant | None]] = []
        for name, turns in skills.items():
            for _ in range(_poisson(rng, turns * SKILLS[name][2])):
                plants = by_skill.get(name, [])
                if plants and rng.random() < 0.72:
                    events.append((name, rng.choice(plants)))
                else:
                    events.append((name, None))
        unskilled = max(0, tool_calls - sum(skills.values()))
        for _ in range(_poisson(rng, unskilled * UNSKILLED_RATE)):
            plants = by_skill[None]
            events.append((None, rng.choice(plants) if rng.random() < 0.4 else None))
        # A few sessions go badly wrong and pile up friction.
        if rng.random() < 0.05:
            bad: str | None = rng.choice(list(skills) or [None])
            plants = by_skill.get(bad, []) or list(by_skill[None])
            events.extend((bad, rng.choice(plants)) for _ in range(rng.randint(5, 9)))

        used: set[int] = set()
        for skill, plant in events:
            fixed = plant is not None and plant.fixed_after is not None and day > plant.fixed_after
            if fixed and rng.random() < 0.8:
                continue
            index = rng.randrange(3, length)
            while index in used:
                index = rng.randrange(3, length + len(used) + 1)
            used.add(index)
            if plant:
                kind = _pick(rng, plant.kinds)
                evidence, context = rng.choice(plant.evidence), rng.choice(plant.context)
                category = plant.category
            else:
                kind = _pick(rng, _LOOSE_KINDS)
                evidence, context = rng.choice(_LOOSE_EVIDENCE[kind]), ""
                category = _pick(rng, _LOOSE_CATEGORIES)
            ts = min(GENERATED_AT, started + timedelta(minutes=index * rng.uniform(0.3, 1.2)))
            f = Friction(
                kind=kind,
                session_id=sid,
                source=source,
                event_index=index,
                timestamp=ts,
                project=project,
                skill=skill,
                evidence=evidence,
                context=context,
                detail=_detail(kind),
            )
            friction.append(f)
            truth[f.id] = category
            if plant:
                planted[plant.id].append(f.id)

    friction.sort(key=lambda f: (f.timestamp or GENERATED_AT, f.id))
    sessions.sort(key=lambda s: s.started_at or GENERATED_AT)
    summaries = {p.id: p.summary for p in PLANTS}
    plant_of = {fid: pid for pid, ids in planted.items() for fid in ids}
    labels = _labels(rng, friction, truth, plant_of, summaries)
    patterns = [
        Pattern(
            id=p.id,
            title=p.title,
            description=p.description,
            friction_ids=planted[p.id],
            category=p.category,
            skill=p.skill,
            target=p.target,
        )
        for p in PLANTS
        if planted[p.id]
    ]
    patterns.sort(key=lambda p: -len(p.friction_ids))
    proposals = [
        Proposal(pattern_id=pid, target=_target(pid), change=c, rationale=r, check=k, agent=a)
        for pid, (c, r, k, a) in _PROPOSALS.items()
    ]
    # Each climb gets its own generator, so tuning the sessions above never changes them.
    climbs = [
        _climb(
            random.Random(seed * 7919 + 1), "~/.claude/skills/git-ship/SKILL.md", "git-ship", 16, 8
        ),
        _climb(
            random.Random(seed * 7919 + 2),
            "~/.claude/skills/docs-style/SKILL.md",
            "docs-style",
            11,
            5,
        ),
    ]
    return Insights(
        generated_at=GENERATED_AT,
        window_days=WINDOW_DAYS,
        agents_detected=list(SOURCES),
        sessions=sessions,
        friction=friction,
        labels=labels,
        patterns=patterns,
        proposals=proposals,
        climbs=climbs,
        synthetic=True,
    )


def _labels(
    rng: random.Random,
    friction: list[Friction],
    truth: dict[str, Category],
    plant_of: dict[str, str],
    summaries: dict[str, str],
) -> list[Label]:
    labels: list[Label] = []
    for f in friction:
        actual = truth[f.id]
        # Some events are just ambiguous, and every agent is less sure about them.
        hard = rng.random() < 0.18
        for agent in AGENTS:
            if agent == "cursor" and rng.random() < 0.12:
                continue  # cursor timed out on a few
            accuracy, habit = _AGENT_HABITS[agent]
            if hard:
                accuracy -= 0.25
            if rng.random() < accuracy:
                category = actual
                confidence = rng.uniform(0.7, 0.97)
            else:
                pool = (*_CONFUSABLE[actual], habit)
                category = rng.choice([c for c in pool if c != actual] or [habit])
                confidence = rng.uniform(0.35, 0.75)
            pid = plant_of.get(f.id)
            summary = (
                summaries[pid]
                if pid and category == actual
                else _LOOSE_SUMMARY.get(category, "Unclear what went wrong")
            )
            labels.append(Label(f.id, agent, category, round(confidence, 2), summary))
    return labels


def _climb(rng: random.Random, target: str, prefix: str, n_cases: int, n_steps: int) -> ClimbRun:
    """Simulate a hill climb with the real split and accept/reject rule from retro.climb."""
    seeds = 5
    names = _CASE_NAMES[prefix]
    cases = [Case(f"{prefix}.{n}", n, "", []) for n in rng.sample(names, min(n_cases, len(names)))]
    parts = split(cases)
    p0 = {c.id: rng.choice((0.0, 0.0, 0.0, 0.2, 0.2, 0.4)) for c in cases}

    def measure(ids: list[str], probs: dict[str, float]) -> dict[str, float]:
        return {i: sum(rng.random() < probs[i] for _ in range(seeds)) / seeds for i in ids}

    train = [c.id for c in parts.train]
    val = [c.id for c in parts.val]
    test = [c.id for c in parts.test]
    current = dict(p0)
    val_scores = measure(val, current)
    first_val = val_scores
    steps: list[ClimbStep] = []
    for step in range(1, n_steps + 1):
        candidate = dict(current)
        # Each edit helps about half the cases. Regressions come from replay noise, the
        # same way they do for real.
        for cid, p in current.items():
            if rng.random() < 0.5:
                candidate[cid] = min(1.0, p + rng.uniform(0.25, 0.6))
        cand_val = measure(val, candidate)
        decision = compare(val_scores, cand_val, max_drop=1 / seeds)
        train_mean = _mean(measure(train, candidate).values())
        steps.append(
            ClimbStep(
                step,
                decision.accept,
                round(train_mean, 3),
                round(decision.candidate, 3),
                decision.reason,
            )
        )
        if decision.accept:
            current, val_scores = candidate, cand_val

    test_before = measure(test, p0)
    test_after = measure(test, current)
    baseline = {**measure(train + val, p0), **test_before}
    final = {**measure(train + val, current), **test_after}
    grid = {
        c.id: {"original": 0.0, "baseline": baseline[c.id], "candidate": final[c.id]} for c in cases
    }
    splits = {c.id: name for name in ("train", "val", "test") for c in getattr(parts, name)}
    return ClimbRun(target, steps, test_before, test_after, grid, _mean(first_val.values()), splits)


_CASE_NAMES = {
    "git-ship": (
        "tests-before-commit",
        "no-force-push",
        "rebase-on-reject",
        "reads-ci-output",
        "pr-body-template",
        "small-commits",
        "no-amend-shared",
        "branch-naming",
        "keeps-lockfile",
        "asks-before-tag",
        "changelog-entry",
        "no-skip-hooks",
        "pushes-once",
        "links-issue",
        "squash-fixups",
        "no-wip-commits",
        "checks-ci-green",
    ),
    "docs-style": (
        "no-em-dash-readme",
        "no-em-dash-changelog",
        "plain-headings",
        "short-sentences",
        "no-semicolon-chains",
        "links-not-urls",
        "sentence-case",
        "no-filler-intro",
        "code-in-backticks",
        "one-idea-per-para",
        "no-exclamation",
    ),
}


def _target(pattern_id: str) -> str:
    return next(p.target for p in PLANTS if p.id == pattern_id)


def _detail(kind: FrictionKind) -> str | None:
    return {
        FrictionKind.DENIAL: "user-rejected",
        FrictionKind.ERROR_STREAK: "3+ failed calls: Bash",
    }.get(kind)


def _pick_day(rng: random.Random) -> int:
    """A day in the window, busier on weekdays and a little busier as the window goes on."""
    while True:
        day = rng.randrange(WINDOW_DAYS)
        weekday = (GENERATED_AT - timedelta(days=WINDOW_DAYS - 1 - day)).weekday()
        weight = (0.25 if weekday >= 5 else 1.0) * (0.75 + 0.5 * day / WINDOW_DAYS)
        if rng.random() < weight:
            return day


def _pick(rng: random.Random, weights: dict[_T, float]) -> _T:
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _poisson(rng: random.Random, lam: float) -> int:
    threshold, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= threshold:
            return k
        k += 1


def _mean(values: Any) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0
