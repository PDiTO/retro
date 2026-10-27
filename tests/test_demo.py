from __future__ import annotations

import builtins
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from retro.cases import Check
from retro.demo import build_demo
from retro.insights import Insights
from retro.redact import find_secrets


def test_same_seed_same_data() -> None:
    assert build_demo(7).to_json() == build_demo(7).to_json()
    assert build_demo(7).to_json() != build_demo(8).to_json()


def test_round_trips_through_json() -> None:
    demo = build_demo()
    assert Insights.from_json(json.loads(json.dumps(demo.to_json()))).to_json() == demo.to_json()


def test_shape() -> None:
    demo = build_demo()
    assert demo.synthetic
    assert demo.window_days == 90
    assert len(demo.sessions) == 400
    assert {s.source for s in demo.sessions} == {"claude-code", "codex", "cursor", "opencode"}
    assert len({k for s in demo.sessions for k in s.skills}) == 10
    assert 150 < len(demo.friction) < 500
    assert len({f.id for f in demo.friction}) == len(demo.friction)
    assert all(re.fullmatch(r"[0-9a-f]{8}:\d+", f.id) for f in demo.friction)
    assert {lb.agent for lb in demo.labels} == {"claude", "codex", "cursor"}
    assert 1 <= len(demo.climbs) <= 2


def test_window_holds_every_session_and_event() -> None:
    demo = build_demo()
    first = demo.generated_at.date().toordinal() - demo.window_days + 1
    for when in [s.started_at for s in demo.sessions] + [f.timestamp for f in demo.friction]:
        assert when is not None
        assert first <= when.date().toordinal() <= demo.generated_at.date().toordinal()
        assert when <= demo.generated_at


def test_references_hold_together() -> None:
    demo = build_demo()
    ids = {f.id for f in demo.friction}
    patterns = {p.id for p in demo.patterns}
    assert all(set(p.friction_ids) <= ids for p in demo.patterns)
    assert all(lb.friction_id in ids for lb in demo.labels)
    assert all(p.pattern_id in patterns for p in demo.proposals)
    for proposal in demo.proposals:
        Check(**proposal.check)  # the draft check is a valid case check
        assert proposal.change.startswith("--- ")
        assert "\n+" in proposal.change


def test_labels_agree_imperfectly() -> None:
    demo = build_demo()
    by_event: dict[str, set[str]] = {}
    for lb in demo.labels:
        by_event.setdefault(lb.friction_id, set()).add(lb.category.value)
    split = Counter(len(cats) for cats in by_event.values())
    assert split[1] > split[2] > 0  # mostly agree, often not


def test_climbs_are_consistent() -> None:
    for run in build_demo().climbs:
        assert run.steps
        assert any(s.accepted for s in run.steps)
        assert all(row["original"] == 0.0 for row in run.grid.values())
        assert set(run.test_before) <= set(run.grid)
        assert all(0.0 <= v <= 1.0 for v in run.test_after.values())


def test_publishable() -> None:
    text = json.dumps(build_demo().to_json(), ensure_ascii=False)
    assert not find_secrets(text)
    assert not re.search(r"/(home|Users)/", text)


def test_never_touches_the_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError(f"build_demo touched the filesystem: {args}")

    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(Path, "home", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(os, "listdir", refuse)
    monkeypatch.setattr(os, "scandir", refuse)
    monkeypatch.setattr(os.path, "expanduser", refuse)
    assert build_demo(3).synthetic
