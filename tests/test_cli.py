from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from retro import store
from retro.cases import Case
from retro.cli import main
from tests.conftest import CASES, FIXTURES, SOMEONE_HOME


@pytest.fixture
def claude_dir(tmp_path: Path) -> Path:
    project = tmp_path / "claude" / "-home-dev-src-widget"
    project.mkdir(parents=True)
    for name in ("mistake.jsonl", "fixed.jsonl"):
        shutil.copy(FIXTURES / "claude_code" / name, project / name)
    return tmp_path / "claude"


def scan(claude_dir: Path, *extra: str) -> int:
    root = f"claude-code={claude_dir}"
    return main(["scan", "--source", "claude-code", "--root", root, "--days", "3650", *extra])


def test_scan_saves_and_reports(
    claude_dir: Path, private_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert scan(claude_dir) == 0
    out = capsys.readouterr().out
    assert "4 friction events in 1 of 2 sessions" in out
    assert "no, you didn't run the tests before committing" in out
    saved = store.load_friction(private_home)
    assert len(saved) == 4

    assert main(["report", "--kind", "denial"]) == 0
    assert "1 friction events" in capsys.readouterr().out


def test_scan_exclude(claude_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert scan(claude_dir, "--exclude", "/widget$") == 0
    assert "0 friction events in 0 of 0 sessions" in capsys.readouterr().out


def test_report_before_scan(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["report"]) == 1
    assert "run retro scan first" in capsys.readouterr().out


def test_case_new_then_check(
    claude_dir: Path, private_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scan(claude_dir)
    assert main(["case", "new", "5e55i0n0:7"]) == 0
    path = private_home / "cases" / "5e55i0n0-7.toml"
    draft = Case.load(path)
    assert draft.friction == "5e55i0n0:7"
    assert draft.prompt == "Add a --verbose flag to the widget CLI and commit it"
    assert "no, you didn't run the tests" in path.read_text()
    assert main(["case", "new", "5e55i0n0:7"]) == 1  # won't overwrite
    assert main(["case", "new", "nope:1"]) == 1


def test_case_check_admit(capsys: pytest.CaptureFixture[str]) -> None:
    case = str(CASES / "tests-before-commit.toml")
    mistake = str(FIXTURES / "claude_code" / "mistake.jsonl")
    fixed = str(FIXTURES / "claude_code" / "fixed.jsonl")
    assert main(["case", "check", case, mistake, "--admit"]) == 0
    assert "admitted" in capsys.readouterr().out
    assert main(["case", "check", case, fixed, "--admit"]) == 1
    assert main(["case", "check", case, fixed]) == 0
    assert main(["case", "check", case, mistake]) == 1


def test_audit_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["audit", str(tmp_path)]) == 0
    (tmp_path / "x.md").write_text(SOMEONE_HOME + "\n")
    assert main(["audit", str(tmp_path)]) == 1
    assert "home-path" in capsys.readouterr().out


def test_refuses_data_dir_inside_a_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.setenv("RETRO_HOME", str(tmp_path / "data"))
    assert main(["report"]) == 2
    assert "inside the git work tree" in " ".join(capsys.readouterr().out.split())
    assert not (tmp_path / "data").exists()


def test_default_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RETRO_HOME")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert store.data_dir() == tmp_path / "retro"
