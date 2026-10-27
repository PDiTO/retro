from __future__ import annotations

from pathlib import Path

import pytest

from retro.model import Session
from retro.sources import claude_code, codex

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
CASES = ROOT / "examples" / "cases"
# A real-looking home directory, assembled so this file doesn't trip `retro audit`.
SOMEONE_HOME = "/Us" + "ers/jsmith"


@pytest.fixture
def mistake() -> Session:
    return claude_code.parse_session(FIXTURES / "claude_code" / "mistake.jsonl")


@pytest.fixture
def fixed() -> Session:
    return claude_code.parse_session(FIXTURES / "claude_code" / "fixed.jsonl")


@pytest.fixture
def rollout() -> Session:
    return codex.parse_session(FIXTURES / "codex" / "rollout.jsonl")


@pytest.fixture(autouse=True)
def private_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the data directory somewhere outside any git repo for every test."""
    home = tmp_path_factory.mktemp("retro-home")
    monkeypatch.setenv("RETRO_HOME", str(home))
    return home
