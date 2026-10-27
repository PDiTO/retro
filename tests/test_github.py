from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from retro.friction import FrictionKind
from retro.model import Session
from retro.sources import github
from tests.conftest import FIXTURES

URL = "https://github.com/example/widget/pull/7"


def pr() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "github" / "pr7.json").read_text())
    return data


def test_review_and_check_friction(mistake: Session) -> None:
    found = github.pr_friction(mistake, URL, pr())
    assert [(f.kind, f.ref) for f in found] == [
        (FrictionKind.REVIEW_COMMENT, "pr7.review2"),
        (FrictionKind.CHECK_FAILURE, "pr7.check1"),
    ]
    review, check = found
    assert review.id == "5e55i0n0:pr7.review2"
    assert review.evidence == "P1: --verbose is parsed but never read"
    assert review.detail == f"{URL} commented by reviewer-bot"
    assert review.timestamp is not None
    assert check.evidence == "test: failure"


def test_fetch_uses_runner_and_caches_finished_prs(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        calls.append(args)
        return json.dumps(pr())

    first = github.fetch_pr(URL, run=run, cache_dir=tmp_path)
    second = github.fetch_pr(URL, run=run, cache_dir=tmp_path)
    assert first == second == pr()
    assert calls == [["pr", "view", URL, "--json", github.FIELDS]]


def test_open_prs_are_not_cached(tmp_path: Path) -> None:
    calls = 0

    def run(args: list[str]) -> str:
        nonlocal calls
        calls += 1
        return json.dumps({**pr(), "state": "OPEN"})

    github.fetch_pr(URL, run=run, cache_dir=tmp_path)
    github.fetch_pr(URL, run=run, cache_dir=tmp_path)
    assert calls == 2
