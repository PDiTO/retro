from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from retro.cases import Case, Check, CheckResult, Verdict, admit, grade, passed, written_text
from retro.model import Session
from tests.conftest import CASES


def load(name: str) -> Case:
    return Case.load(CASES / f"{name}.toml")


@pytest.mark.parametrize("name", ["tests-before-commit", "no-force-push", "no-em-dashes"])
def test_example_cases_are_red_before_and_green_after(
    name: str, mistake: Session, fixed: Session
) -> None:
    case = load(name)
    admitted, _ = admit(case, mistake)
    assert admitted, "the case should catch the mistake it was written for"
    assert not passed(grade(case, mistake))
    assert passed(grade(case, fixed))
    assert not admit(case, fixed)[0]


def test_require_tool_reports_the_trigger(mistake: Session) -> None:
    [result] = grade(load("tests-before-commit"), mistake)
    assert result.verdict is Verdict.FAIL
    assert result.reason == "no matching call before event 4"


def test_require_tool_is_vacuous_without_the_trigger(fixed: Session) -> None:
    case = Case("c", "t", "p", [Check("require_tool", pattern="cargo test", before="git push")])
    [result] = grade(case, fixed)
    assert result.verdict is Verdict.PASS
    assert "never ran" in result.reason


def test_require_tool_without_before(fixed: Session) -> None:
    case = Case("c", "t", "p", [Check("require_tool", tool="Bash", pattern="cargo")])
    assert grade(case, fixed)[0].verdict is Verdict.FAIL


def test_max_errors(mistake: Session, fixed: Session) -> None:
    case = Case("c", "t", "p", [Check("max_errors", limit=2)])
    assert grade(case, mistake)[0].verdict is Verdict.FAIL
    assert grade(case, fixed)[0].verdict is Verdict.PASS


def test_forbid_text_where(mistake: Session) -> None:
    said = Case("c", "t", "p", [Check("forbid_text", pattern="Committed", where="assistant")])
    wrote = Case("c", "t", "p", [Check("forbid_text", pattern="Committed", where="writes")])
    assert grade(said, mistake)[0].verdict is Verdict.FAIL
    assert grade(wrote, mistake)[0].verdict is Verdict.PASS


def test_command_needs_a_workdir(mistake: Session, tmp_path: Path) -> None:
    case = load("no-em-dashes")
    command = case.checks[1]
    assert grade(case, mistake)[1].verdict is Verdict.SKIP
    (tmp_path / "cli.py").write_text("print('ok')\n")
    assert grade(case, mistake, workdir=tmp_path)[1].verdict is Verdict.PASS
    (tmp_path / "cli.py").write_text("# louder — output\n")
    result = grade(case, mistake, workdir=tmp_path)[1]
    assert result.check is command
    assert result.verdict is Verdict.FAIL


def test_judge(fixed: Session) -> None:
    case = Case("c", "t", "p", [Check("judge", rubric="explains the change")])
    assert grade(case, fixed)[0].verdict is Verdict.SKIP
    seen: list[str] = []

    def judge(rubric: str, session: Session) -> tuple[bool, str]:
        seen.append(rubric)
        return False, "no explanation given"

    [result] = grade(case, fixed, judge=judge)
    assert seen == ["explains the change"]
    assert (result.verdict, result.reason) == (Verdict.FAIL, "no explanation given")


def test_passed_needs_something_to_have_run() -> None:
    check = Check("judge", rubric="x")
    assert not passed([CheckResult(check, Verdict.SKIP, "")])
    assert passed([CheckResult(check, Verdict.SKIP, ""), CheckResult(check, Verdict.PASS, "")])
    assert not passed([CheckResult(check, Verdict.PASS, ""), CheckResult(check, Verdict.FAIL, "")])


def test_written_text_reads_codex_patches(rollout: Session) -> None:
    patch = rollout.tool_calls[0]
    assert written_text(patch) == "    if n < 0:\n        raise ValueError('negative')"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"type": "nope"}, "unknown check type"),
        ({"type": "command"}, "needs a value"),
        ({"type": "forbid_text"}, "needs a value"),
        ({"type": "forbid_text", "pattern": "x", "where": "elsewhere"}, "where must be"),
    ],
)
def test_check_validation(kwargs: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Check(**kwargs)


def test_case_needs_checks(tmp_path: Path) -> None:
    path = tmp_path / "empty.toml"
    path.write_text('id = "x"\ntitle = "x"\nprompt = "x"\n')
    with pytest.raises(ValueError, match="at least one"):
        Case.load(path)


def test_labels() -> None:
    assert Check("forbid_tool", tool="Bash", pattern="rm -rf").label == "forbid_tool Bash rm -rf"
    assert Check("require_tool", pattern="test", before="commit").label == (
        "require_tool test before commit"
    )
    assert Check("max_errors", description="few errors").label == "few errors"
