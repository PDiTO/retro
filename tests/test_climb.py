from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from retro.cases import Case, Check
from retro.climb import Replay, climb, compare, evaluate, split
from retro.model import Event, EventKind, Session


def case(i: int) -> Case:
    return Case(f"case-{i}", "t", "p", [Check("forbid_tool", pattern=f"bad-{i}")])


def session(*commands: str) -> Session:
    s = Session(id="replay", source="fake", path=Path("replay.jsonl"))
    for command in commands:
        s.events.append(
            Event(EventKind.TOOL_CALL, len(s.events), tool="Bash", tool_input={"command": command})
        )
    return s


class FakeRunner:
    """Instructions are a directory name listing the cases they fix, e.g. `fix-1-4`.

    `flaky` cases pass on even seeds only, whatever the instructions.
    """

    def __init__(self, flaky: Sequence[str] = ()) -> None:
        self.flaky = set(flaky)
        self.runs = 0

    def run(self, case: Case, instructions: Path, seed: int) -> Replay:
        self.runs += 1
        number = case.id.removeprefix("case-")
        fixed = number in instructions.name.split("-")[1:]
        if case.id in self.flaky:
            fixed = seed % 2 == 0
        return Replay(session("fine" if fixed else f"bad-{number}"))


class FixEverythingSeen:
    """Proposes instructions that fix exactly the train cases it was shown failing."""

    def __init__(self) -> None:
        self.shown: list[list[str]] = []

    def propose(self, instructions: Path, failures: Sequence[tuple[Case, Replay]]) -> Path:
        ids = sorted({c.id.removeprefix("case-") for c, _ in failures})
        self.shown.append(ids)
        current = instructions.name.split("-")[1:]
        return instructions.with_name("-".join(["fix", *sorted(set(current) | set(ids))]))


def test_split_is_deterministic_and_stable() -> None:
    cases = [case(i) for i in range(200)]
    first = split(cases)
    again = split(list(reversed(cases)))
    assert {c.id for c in first.test} == {c.id for c in again.test}
    assert len(first.train) + len(first.val) + len(first.test) == 200
    assert 30 < len(first.test) < 70
    assert 30 < len(first.val) < 70
    # Adding cases never moves an existing one.
    bigger = split([*cases, *(case(i) for i in range(200, 300))])
    assert {c.id for c in first.test} <= {c.id for c in bigger.test}


def test_evaluate_counts_pass_rate_over_seeds() -> None:
    runner = FakeRunner(flaky=["case-2"])
    scores, failures = evaluate(runner, [case(1), case(2)], Path("fix-1"), seeds=4)
    assert scores == {"case-1": 1.0, "case-2": 0.5}
    assert [c.id for c, _ in failures] == ["case-2", "case-2"]
    assert runner.runs == 8


def test_compare() -> None:
    base = {"a": 1.0, "b": 0.0, "c": 0.5}
    accept = compare(base, {"a": 1.0, "b": 1.0, "c": 0.5})
    assert accept.accept
    assert accept.fixed == ["b"]
    assert accept.reason == "pass rate 0.50 -> 0.83"

    regress = compare(base, {"a": 0.0, "b": 1.0, "c": 1.0})
    assert not regress.accept
    assert regress.regressed == ["a"]

    tolerant = compare(base, {"a": 0.75, "b": 1.0, "c": 0.5}, max_drop=0.25)
    assert tolerant.accept

    flat = compare(base, dict(base))
    assert (flat.accept, flat.reason) == (False, "no improvement")


def test_climb_accepts_on_val_and_reports_on_untouched_test() -> None:
    cases = [case(i) for i in range(40)]
    proposer = FixEverythingSeen()
    result = climb(FakeRunner(), proposer, cases, Path("fix"), steps=3, seeds=2)

    parts = split(cases)
    train_ids = {c.id.removeprefix("case-") for c in parts.train}
    # The proposer only ever saw train failures.
    assert set(proposer.shown[0]) == train_ids
    # Fixing train cases doesn't help val in this fake, so the edit is rejected...
    assert result.steps[0].accept is False
    assert result.instructions == Path("fix")
    # ...and test is scored on the starting instructions both times.
    assert result.test_before == result.test_after
    assert set(result.test_before) == {c.id for c in parts.test}


def test_climb_stops_when_train_is_clean() -> None:
    cases = [case(i) for i in range(10)]
    everything = Path("fix-" + "-".join(str(i) for i in range(10)))
    proposer = FixEverythingSeen()
    result = climb(FakeRunner(), proposer, cases, everything, steps=3, seeds=1)
    assert result.steps == []
    assert proposer.shown == []
    assert all(v == 1.0 for v in result.test_after.values())
