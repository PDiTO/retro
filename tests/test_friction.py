from __future__ import annotations

import pytest

from retro.friction import Friction, FrictionKind, detect, is_correction
from retro.model import Session


def test_detects_each_kind_in_the_mistake_session(mistake: Session) -> None:
    found = {f.id: f for f in detect(mistake)}
    assert {f.kind for f in found.values()} == {
        FrictionKind.CORRECTION,
        FrictionKind.ERROR_STREAK,
        FrictionKind.DENIAL,
        FrictionKind.INTERRUPT,
    }

    correction = found["5e55i0n0:7"]
    assert correction.kind is FrictionKind.CORRECTION
    assert correction.skill == "git-stage"
    assert correction.project == "widget"
    assert correction.context == "Committed. The flag is in."
    assert correction.session_path == str(mistake.path)

    streak = found["5e55i0n0:9"]
    assert streak.detail == "3+ failed calls: Bash"

    denial = found["5e55i0n0:15"]
    assert denial.detail == "user-rejected"
    assert "git push --force" in denial.context


def test_clean_session_has_no_friction(fixed: Session) -> None:
    # Includes "no, just print more", which answers a question rather than pushing back.
    assert detect(fixed) == []


def test_codex_friction(rollout: Session) -> None:
    assert [(f.kind, f.event_index) for f in detect(rollout)] == [
        (FrictionKind.INTERRUPT, 6),
        (FrictionKind.CORRECTION, 7),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "no, use the existing helper",
        "No. That's the wrong file",
        "that's not what I asked for",
        "you didn't run the tests",
        "You forgot the changelog",
        "why did you delete that?",
        "I said Rust, not Python",
        "don't add comments everywhere",
        "stop",
        "revert that",
        "ok but as I said, keep it short",
        "read the skill before doing that",
    ],
)
def test_corrections(text: str) -> None:
    assert is_correction(text)


@pytest.mark.parametrize(
    "text",
    [
        "no worries, thanks",
        "no problem",
        "great, now add a test",
        "also handle the empty case",
        "nothing else, ship it",
        "donut chart please",
    ],
)
def test_not_corrections(text: str) -> None:
    assert not is_correction(text)


def test_no_after_a_question_is_an_answer() -> None:
    assert not is_correction("no, keep it", "Should I also update the docs?")
    assert is_correction("no, keep it", "I've updated the docs.")
    # Explicit pushback still counts after a question.
    assert is_correction("I told you not to touch the docs", "Want me to update them?")


def test_json_round_trip(mistake: Session) -> None:
    for item in detect(mistake):
        assert Friction.from_json(item.to_json()) == item
