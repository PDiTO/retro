from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from retro.demo import build_demo
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
from retro.viz import CHARTS, derive, export, render_html
from retro.viz.report import SECTIONS

NOW = datetime(2026, 9, 30, 18, 0, tzinfo=UTC)
HOSTILE = '<script>alert("x")</script>'


@pytest.fixture(scope="module")
def demo() -> Insights:
    return build_demo()


def empty() -> Insights:
    return Insights(generated_at=NOW, window_days=30, agents_detected=[], sessions=[], friction=[])


def hostile() -> Insights:
    """Every free-text field carries markup, as a transcript quote might."""
    sid = "ab12cd34" + "0" * 24
    session = SessionStats(
        sid, HOSTILE, HOSTILE, NOW - timedelta(days=1), 3, 12, {HOSTILE: 4, "git-ship": 2}
    )
    friction = Friction(
        kind=FrictionKind.CORRECTION,
        session_id=sid,
        source=HOSTILE,
        event_index=7,
        timestamp=NOW - timedelta(days=1),
        project=HOSTILE,
        skill=HOSTILE,
        evidence=HOSTILE,
        context=HOSTILE,
    )
    return Insights(
        generated_at=NOW,
        window_days=30,
        agents_detected=[HOSTILE],
        sessions=[session],
        friction=[friction],
        labels=[
            Label(friction.id, HOSTILE, Category.STYLE, 0.9, HOSTILE),
            Label(friction.id, "codex", Category.STYLE, 0.8, HOSTILE),
        ],
        patterns=[
            Pattern(HOSTILE, HOSTILE, HOSTILE, [friction.id], Category.STYLE, HOSTILE, HOSTILE)
        ],
        proposals=[
            Proposal(
                HOSTILE,
                HOSTILE,
                f"--- a\n+++ b\n@@ -1 +1 @@\n-{HOSTILE}\n+{HOSTILE}\n",
                HOSTILE,
                {"type": "forbid_text", "pattern": HOSTILE},
                HOSTILE,
            )
        ],
        climbs=[
            ClimbRun(
                HOSTILE,
                [ClimbStep(1, True, 0.5, 0.6, "pass rate 0.20 -> 0.60")],
                {HOSTILE: 0.2},
                {HOSTILE: 0.8},
                {HOSTILE: {"original": 0.0, "baseline": 0.2, "candidate": 0.8}},
            )
        ],
    )


@pytest.mark.parametrize("name", list(CHARTS))
@pytest.mark.parametrize("make", [build_demo, empty, hostile], ids=["demo", "empty", "hostile"])
def test_every_chart_is_well_formed_svg(name: str, make: Callable[[], Insights]) -> None:
    insights = make()
    svg = CHARTS[name](insights)
    root = ET.fromstring(svg)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert "prefers-color-scheme: dark" in svg
    assert "<script" not in svg
    assert root.find("{http://www.w3.org/2000/svg}title") is not None


@pytest.mark.parametrize("name", list(CHARTS))
def test_empty_charts_say_so(name: str) -> None:
    svg = CHARTS[name](empty())
    texts = " ".join(
        t.text or "" for t in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")
    )
    assert re.search(r"\bNo\b", texts), texts


def test_report_has_every_section(demo: Insights) -> None:
    html = render_html(demo)
    for sid, _ in SECTIONS:
        assert f'<section id="{sid}">' in html
    assert html.count("<svg") == len(CHARTS)
    assert html.count('<details class="tableview">') == len(CHARTS)
    assert "Synthetic demo data" in html
    assert html.count('<article class="proposal">') == len(demo.proposals)


def test_report_is_offline(demo: Insights) -> None:
    html = render_html(demo)
    assert "<script" not in html
    assert "<link" not in html
    urls = set(re.findall(r"https?://[^\s\"'<>)]+", html))
    assert urls <= {"http://www.w3.org/2000/svg"}


def test_report_escapes_text_from_data() -> None:
    html = render_html(hostile())
    assert "<script" not in html
    assert "&lt;script&gt;" in html
    for svg in (chart(hostile()) for chart in CHARTS.values()):
        assert "<script" not in svg


def test_diff_lines_are_coloured(demo: Insights) -> None:
    html = render_html(demo)
    assert '<span class="add">+' in html
    assert '<span class="del">-' in html
    assert '<span class="hunk">@@' in html
    assert '<span class="hunk">[[checks]]</span>' in html


def test_real_data_has_no_banner(demo: Insights) -> None:
    demo_copy = Insights(**{**demo.__dict__, "synthetic": False})
    assert "Synthetic demo data" not in render_html(demo_copy)


def test_empty_report_renders_empty_states() -> None:
    html = render_html(empty())
    for sid, _ in SECTIONS:
        assert f'<section id="{sid}">' in html
    assert "No patterns yet" in html
    assert "No proposals yet" in html
    assert "Nothing yet." in html
    assert "Synthetic" not in html


def test_export_writes_report_and_every_chart(tmp_path: Path, demo: Insights) -> None:
    paths = export(demo, tmp_path / "out")
    names = sorted(p.name for p in paths)
    assert names == sorted(["report.html", *(f"{n}.svg" for n in CHARTS)])
    for path in paths:
        assert path.read_text(encoding="utf-8")
        if path.suffix == ".svg":
            ET.parse(path)


def test_wilson_interval_behaves_at_the_edges() -> None:
    lo, hi = derive.wilson(0, 10)
    assert lo == 0.0
    assert 0.2 < hi < 0.35
    lo, hi = derive.wilson(5, 10)
    assert lo < 0.5 < hi
    assert derive.wilson(0, 0) == (0.0, 1.0)
    # More evidence, narrower interval.
    a, b = derive.wilson(10, 100), derive.wilson(100, 1000)
    assert b[1] - b[0] < a[1] - a[0]


def test_consensus_takes_the_majority_then_confidence() -> None:
    ins = empty()
    ins.labels = [
        Label("a:1", "claude", Category.STYLE, 0.9, ""),
        Label("a:1", "codex", Category.STYLE, 0.5, ""),
        Label("a:1", "cursor", Category.SCOPE_CREEP, 0.99, ""),
        Label("b:2", "claude", Category.STYLE, 0.4, ""),
        Label("b:2", "codex", Category.RISKY_ACTION, 0.8, ""),
    ]
    assert derive.consensus(ins) == {"a:1": Category.STYLE, "b:2": Category.RISKY_ACTION}


def test_skill_rates_normalise_by_turns_and_drop_false_positives() -> None:
    sid = "s" * 32
    ins = empty()
    ins.sessions = [SessionStats(sid, "codex", "p", NOW, 1, 400, {"busy": 300, "rare": 10})]

    def f(i: int, skill: str) -> Friction:
        return Friction(FrictionKind.CORRECTION, sid, "codex", i, NOW, "p", skill, "no")

    ins.friction = [f(i, "busy") for i in range(6)] + [f(100 + i, "rare") for i in range(2)]
    ins.labels = [Label(ins.friction[0].id, "claude", Category.NOT_A_MISTAKE, 0.9, "")]
    rows, overall, dropped = derive.skill_rates(ins)
    by = {r.skill: r for r in rows}
    assert dropped == 1
    assert by["busy"].friction == 5
    assert by["rare"].rate > by["busy"].rate  # fewer events, but far fewer turns
    assert rows[0].skill == "rare"
    assert overall == pytest.approx(7 / 310)


def test_climb_baseline_comes_from_the_first_accepted_reason() -> None:
    run = ClimbRun(
        "x",
        [
            ClimbStep(1, False, 0.4, 0.2, "regressed 1 case(s)"),
            ClimbStep(2, True, 0.5, 0.6, "pass rate 0.30 -> 0.60"),
        ],
        {},
        {},
    )
    assert derive.climb_baseline(run) == pytest.approx(0.3)
    assert derive.climb_baseline(ClimbRun("x", [], {}, {})) is None


def test_agreement_counts_pairs_and_kappa(demo: Insights) -> None:
    ag = derive.agreement(demo)
    assert set(ag.agents) == {"claude", "codex", "cursor"}
    same, kappa, shared = ag.pairs[("claude", "codex")]
    assert ag.pairs[("codex", "claude")] == (same, kappa, shared)
    assert 0.3 < same < 0.95
    assert kappa < same
    assert 0 < ag.unanimous < 1


def test_weeks_cover_the_window(demo: Insights) -> None:
    weeks = derive.weekly(demo)
    assert sum(w.total for w in weeks) == len(demo.friction)
    assert sum(w.days for w in weeks) == demo.window_days
    assert all(w.start.weekday() == 0 for w in weeks)


def test_flow_conserves_events(demo: Insights) -> None:
    fl = derive.flow(demo)
    assert sum(n for _, n in fl.left) == sum(n for _, n, _ in fl.middle) == len(demo.friction)
    grouped = sum(len(p.friction_ids) for p in demo.patterns)
    assert sum(n for _, n in fl.right) == grouped
