"""Hill climbing on agent instructions, scored by regression cases.

The loop:

1. Split admitted cases three ways by a hash of their id: train, val and test.
2. Replay the train cases with the current instructions (skills, CLAUDE.md, AGENTS.md)
   and hand the failures to a proposer, which writes a candidate edit.
3. Replay the val cases with the candidate. Accept it only if the mean pass rate goes up
   and no val case that used to pass now fails more often than `max_drop` allows.
4. Repeat from the accepted instructions.
5. Score test once, at the end. Nothing in the loop ever sees it, so it is the honest
   number for whether the edits generalise or just fit the cases they were made from.

Replays are noisy, so every case runs `seeds` times and scores are pass rates.

The parts that touch the outside world are protocols. A `Runner` replays one case with a
given instructions directory (for real: a git worktree at `case.base` and a headless
`claude -p` or `codex exec`). A `Proposer` writes a candidate instructions directory from a
list of failures (for real: an LLM editing skill files). Neither exists yet. The decision
logic below is the part worth getting right first, and it is tested with fakes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from retro.cases import Case, grade, passed
from retro.model import Session


@dataclass(frozen=True)
class Replay:
    session: Session
    workdir: Path | None = None


class Runner(Protocol):
    def run(self, case: Case, instructions: Path, seed: int) -> Replay: ...


class Proposer(Protocol):
    def propose(self, instructions: Path, failures: Sequence[tuple[Case, Replay]]) -> Path: ...


@dataclass(frozen=True)
class Split:
    train: list[Case]
    val: list[Case]
    test: list[Case]


def split(cases: Sequence[Case], val: float = 0.25, test: float = 0.25) -> Split:
    """Deterministic by case id, so adding cases never moves existing ones between splits."""
    out = Split([], [], [])
    for case in cases:
        u = int(hashlib.sha256(case.id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
        bucket = out.test if u < test else out.val if u < test + val else out.train
        bucket.append(case)
    return out


Scores = dict[str, float]


def evaluate(
    runner: Runner, cases: Sequence[Case], instructions: Path, seeds: int
) -> tuple[Scores, list[tuple[Case, Replay]]]:
    """Pass rate per case over `seeds` replays, plus every failing replay."""
    scores: Scores = {}
    failures: list[tuple[Case, Replay]] = []
    for case in cases:
        wins = 0
        for seed in range(seeds):
            replay = runner.run(case, instructions, seed)
            if passed(grade(case, replay.session, workdir=replay.workdir)):
                wins += 1
            else:
                failures.append((case, replay))
        scores[case.id] = wins / seeds
    return scores, failures


@dataclass(frozen=True)
class Decision:
    accept: bool
    baseline: float
    candidate: float
    fixed: list[str]
    regressed: list[str]
    reason: str


# Pass rates are means of small fractions, so compare with a little slack: a candidate
# whose mean differs only by float rounding is not an improvement.
EPS = 1e-9


def compare(baseline: Scores, candidate: Scores, max_drop: float = 0.0) -> Decision:
    ids = sorted(baseline)
    base_mean = _mean(baseline[i] for i in ids)
    cand_mean = _mean(candidate.get(i, 0.0) for i in ids)
    fixed = [i for i in ids if candidate.get(i, 0.0) > baseline[i]]
    regressed = [i for i in ids if candidate.get(i, 0.0) < baseline[i] - max_drop - EPS]
    if regressed:
        reason = f"regressed {len(regressed)} case(s)"
    elif cand_mean <= base_mean + EPS:
        reason = "no improvement"
    else:
        reason = f"pass rate {base_mean:.2f} -> {cand_mean:.2f}"
    return Decision(
        not regressed and cand_mean > base_mean + EPS,
        base_mean,
        cand_mean,
        fixed,
        regressed,
        reason,
    )


@dataclass
class ClimbResult:
    instructions: Path
    steps: list[Decision] = field(default_factory=list)
    train: list[float] = field(default_factory=list)  # train pass rate going into each step
    val_before: float = 0.0  # val pass rate of the starting instructions
    splits: dict[str, str] = field(default_factory=dict)  # case id -> "train", "val" or "test"
    test_before: Scores = field(default_factory=dict)
    test_after: Scores = field(default_factory=dict)


def climb(
    runner: Runner,
    proposer: Proposer,
    cases: Sequence[Case],
    instructions: Path,
    *,
    steps: int = 5,
    seeds: int = 3,
    max_drop: float = 0.0,
) -> ClimbResult:
    parts = split(cases)
    result = ClimbResult(instructions)
    current = instructions
    val_scores, _ = evaluate(runner, parts.val, current, seeds)
    result.val_before = _mean(val_scores.values())
    for name in ("train", "val", "test"):
        result.splits.update({c.id: name for c in getattr(parts, name)})

    for _ in range(steps):
        train_scores, failures = evaluate(runner, parts.train, current, seeds)
        if not failures:
            break
        result.train.append(_mean(train_scores.values()))
        candidate = proposer.propose(current, failures)
        cand_scores, _ = evaluate(runner, parts.val, candidate, seeds)
        decision = compare(val_scores, cand_scores, max_drop)
        result.steps.append(decision)
        if decision.accept:
            current, val_scores = candidate, cand_scores

    result.instructions = current
    result.test_before, _ = evaluate(runner, parts.test, instructions, seeds)
    result.test_after, _ = evaluate(runner, parts.test, current, seeds)
    return result


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0
