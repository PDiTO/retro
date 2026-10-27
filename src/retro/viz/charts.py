"""The charts, one function each. Every function takes `Insights` and returns an SVG string.

Each SVG stands alone: it carries its own stylesheet with light and dark colours, uses
system fonts, has no script, and shows an empty state rather than failing when there is
nothing to draw. Marks carry `<title>` elements, so hovering in a browser shows the
values. The HTML report pairs every chart with a table of the same numbers.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from datetime import date, timedelta
from itertools import pairwise

from retro.insights import Category, ClimbRun, Insights
from retro.viz import derive
from retro.viz.theme import (
    CATEGORY_NAME,
    CATEGORY_ORDER,
    CATEGORY_SLOT,
    KIND_NAME,
    KIND_ORDER,
    KIND_SLOT,
    Svg,
    nice_ticks,
    num,
    pct,
    rounded_right,
    rounded_top,
    text_width,
)

W = 960
LEFT = 28
RIGHT = W - 28


def calendar(insights: Insights) -> str:
    """Friction per day, laid out like a contributions calendar, with sessions per week above."""
    days = derive.daily(insights)
    total = sum(f for f, _ in days.values())
    start, end = derive.window(insights)
    svg = Svg(
        W, 470, "Friction per day", f"Friction events per day from {start} to {end}.", "calendar"
    )
    svg.header(
        "Friction per day",
        "Each square is a day, shaded by friction events. The bars above count sessions per week",
    )
    if not insights.sessions and not insights.friction:
        svg.empty("No sessions in this window", "Run retro scan to read some.")
        return svg.render()

    cell, pitch = 28, 33
    x0, bars_top, bars_h = 74, 96, 44
    grid_top = bars_top + bars_h + 34
    first = derive.week_start(start)
    weeks = (derive.week_start(end) - first).days // 7 + 1
    nonzero = sorted(f for f, _ in days.values() if f)
    edges = _bins(nonzero)

    # Sessions per week, as context above the calendar.
    per_week = Counter[int]()
    for day, (_, s) in days.items():
        per_week[(day - first).days // 7] += s
    top = max(per_week.values(), default=0) or 1
    svg.text(x0 - 10, bars_top + 10, "sessions", "tm end")
    svg.text(x0 - 10, bars_top + 23, "per week", "tm end")
    for w in range(weeks):
        h = bars_h * per_week[w] / top
        x = x0 + w * pitch + (cell - 16) / 2
        if h > 0:
            svg.path(
                rounded_top(x, bars_top + bars_h - h, 16, h, 3),
                "neutral mark",
                tip=f"week of {_day(first + timedelta(days=7 * w))}: {per_week[w]} sessions",
            )
    peak = max(range(weeks), key=lambda w: per_week[w])
    svg.text(x0 + peak * pitch + cell / 2, bars_top - 6, str(per_week[peak]), "tm mid num")
    svg.line(
        x0 - 2,
        bars_top + bars_h + 0.5,
        x0 + weeks * pitch - pitch + cell + 2,
        bars_top + bars_h + 0.5,
        "axis",
    )

    # Month labels and weekday labels.
    last_month = None
    for w in range(weeks):
        monday = first + timedelta(days=7 * w)
        visible = [
            monday + timedelta(days=i)
            for i in range(7)
            if start <= monday + timedelta(days=i) <= end
        ]
        label_day = next((d for d in visible if d.day <= 7), visible[0] if w == 0 else None)
        if label_day and label_day.month != last_month:
            svg.text(x0 + w * pitch, grid_top - 10, f"{label_day:%b}", "tm")
            last_month = label_day.month
    for i, name in ((0, "Mon"), (2, "Wed"), (4, "Fri"), (6, "Sun")):
        svg.text(x0 - 10, grid_top + i * pitch + cell / 2 + 4, name, "tm end")

    for day, (f, s) in days.items():
        w, row = (day - first).days // 7, day.weekday()
        x, y = x0 + w * pitch, grid_top + row * pitch
        tip = f"{_wday(day)}: {f} friction in {s} session{'s' if s != 1 else ''}"
        if f:
            level = sum(f >= e for e in edges)
            svg.rect(x, y, cell, cell, f"q{_CAL_STEPS[level - 1]} mark", tip=tip, rx=5)
        elif s:
            svg.rect(x, y, cell, cell, "zero mark", tip=tip, rx=5)
        else:
            svg.rect(x + 0.5, y + 0.5, cell - 1, cell - 1, "void", tip=tip, rx=5)

    # Key, GitHub style.
    ky = grid_top + 7 * pitch + 22
    svg.text(x0, ky + 11, "no sessions", "tm")
    kx = x0 + 70.0
    svg.rect(kx + 0.5, ky + 0.5, 13, 13, "void", rx=3)
    kx += 30
    svg.text(kx, ky + 11, "none", "tm")
    svg.rect(kx + 32, ky, 14, 14, "zero", rx=3)
    kx += 62.0
    for i, e in enumerate(edges):
        upper = edges[i + 1] - 1 if i + 1 < len(edges) else None
        label = f"{e}" if upper == e else f"{e}\u2013{upper}" if upper else f"{e}+"
        svg.rect(kx, ky, 14, 14, f"q{_CAL_STEPS[i]}", rx=3)
        svg.text(kx + 19, ky + 11, label, "tm num")
        kx += 19 + text_width(label, 11) + 14

    # The numbers worth reading, to the right.
    px = max(x0 + weeks * pitch + 56, kx + 40)
    active = sum(1 for f, _ in days.values() if f)
    with_sessions = sum(1 for _, s in days.values() if s)
    worst_day = max(days, key=lambda d: (days[d][0], d))
    rate = total / len(insights.sessions) if insights.sessions else 0.0
    streak = _longest_quiet(days)
    stats = [
        (num(total), "friction events"),
        (f"{active} of {with_sessions}", "working days had friction"),
        (f"{rate:.2f}", "events per session"),
        (
            f"{days[worst_day][0]}",
            f"on {_wday(worst_day)}, the worst day ({days[worst_day][1]} sessions)",
        ),
        (f"{streak}", "working days in a row without friction, the longest run"),
    ]
    y = grid_top + 18
    for value, label in stats:
        svg.text(px, y, value, "tbig")
        svg.text(px, y + 20, label, "tl")
        y += 62 if value else 40
    return svg.render()


# Calendar levels use these ramp steps, so the lightest level still reads against "none".
_CAL_STEPS = (1, 3, 4, 6)


def _bins(values: list[int]) -> list[int]:
    """Four lower bounds for the calendar levels, from the spread of non-zero days."""
    if not values:
        return [1, 2, 3, 4]
    if values[-1] <= 4:
        return [1, 2, 3, 4]
    qs = [values[min(len(values) - 1, int(len(values) * q))] for q in (0.4, 0.7, 0.9)]
    edges = [1]
    for q in qs:
        edges.append(max(q, edges[-1] + 1))
    return edges


def _longest_quiet(days: dict[date, tuple[int, int]]) -> int:
    best = run = 0
    for day in sorted(days):
        f, s = days[day]
        if s and not f:
            run += 1
            best = max(best, run)
        elif f:
            run = 0
    return best


def kinds_over_time(insights: Insights) -> str:
    """Weekly friction stacked by kind, with the rate per 100 sessions underneath."""
    weeks = derive.weekly(insights)
    svg = Svg(
        W, 470, "Friction by kind, per week", "Weekly friction events stacked by kind.", "kinds"
    )
    totals = Counter(f.kind for f in insights.friction)
    svg.header(
        "Friction by kind, per week", "Events per week by detector, and the same per 100 sessions"
    )
    if not insights.friction:
        svg.empty("No friction in this window", "Nothing to break down by kind.")
        return svg.render()
    kinds = [k for k in KIND_ORDER if totals[k]]
    svg.legend([(f"c{KIND_SLOT[k]}", f"{KIND_NAME[k]} {totals[k]}") for k in kinds], LEFT, 92)

    x0, x1 = 64, RIGHT - 8
    top, base = 116, 316
    peak = max((w.total for w in weeks), default=0)
    ticks = nice_ticks(peak)
    scale = (base - top) / ticks[-1]
    for t in ticks:
        y = base - t * scale
        svg.line(x0, y + 0.5, x1, y + 0.5, "grid" if t else "axis")
        svg.text(x0 - 10, y + 4, num(t), "tm end num")
    slot = (x1 - x0) / max(len(weeks), 1)
    bar = min(24.0, slot * 0.6)
    for i, week in enumerate(weeks):
        cx = x0 + slot * (i + 0.5)
        y = float(base)
        stack = [k for k in kinds if week.kinds[k]]
        for j, kind in enumerate(stack):
            h = week.kinds[kind] * scale
            gap = 2 if j else 0
            tip = f"week of {_day(week.start)}: {week.kinds[kind]} {KIND_NAME[kind]}"
            if j == len(stack) - 1:
                svg.path(
                    rounded_top(cx - bar / 2, y - h, bar, h - gap),
                    f"c{KIND_SLOT[kind]} mark",
                    tip=tip,
                )
            else:
                svg.rect(cx - bar / 2, y - h, bar, h - gap, f"c{KIND_SLOT[kind]} mark", tip=tip)
            y -= h
        if week.total and (week.total == peak or i == len(weeks) - 1):
            svg.text(cx, y - 7, str(week.total), "tb mid num")
        if i % 2 == 0 or len(weeks) <= 8:
            svg.text(cx, base + 18, _day(week.start), "tm mid")
    # The rate panel: its own axis, sharing only the weeks.
    r_top, r_base = 372, 438
    svg.text(LEFT, r_top - 12, "per 100 sessions", "tl")
    rates = [(i, 100 * w.total / w.sessions) for i, w in enumerate(weeks) if w.sessions]
    if rates:
        r_ticks = nice_ticks(max(r for _, r in rates), 3)
        r_scale = (r_base - r_top) / r_ticks[-1]
        for t in r_ticks:
            y = r_base - t * r_scale
            svg.line(x0, y + 0.5, x1, y + 0.5, "grid" if t else "axis")
            svg.text(x0 - 10, y + 4, num(t), "tm end num")
        pts = [(x0 + slot * (i + 0.5), r_base - r * r_scale) for i, r in rates]
        svg.path(_polyline(pts), "line s1")
        for (x, y), (i, r) in zip(pts, rates, strict=True):
            svg.circle(
                x,
                y,
                4,
                "c1 ring",
                tip=(
                    f"week of {_day(weeks[i].start)}: {r:.0f} per 100 sessions "
                    f"({weeks[i].sessions} sessions)"
                ),
            )
        lx, ly = pts[-1]
        svg.text(lx + 9, ly + 4, f"{rates[-1][1]:.0f}", "tb num")
    partial = [w for w in weeks if w.days < 7]
    if partial:
        svg.text(
            RIGHT, r_base + 24, "first and last weeks are partial, the rate allows for it", "tm end"
        )
    return svg.render()


def skill_scorecard(insights: Insights) -> str:
    """Friction per 100 agent turns in each skill, with 95% intervals, against the overall rate."""
    rows, overall, dropped = derive.skill_rates(insights)
    rows = [r for r in rows if r.turns or r.friction]
    height = 190 + 34 * max(len(rows), 3)
    svg = Svg(
        W, height, "Friction rate by skill", "Friction per 100 agent turns per skill.", "skills"
    )
    if not rows:
        svg.header(
            "Friction rate by skill", "Friction per 100 agent turns while each skill was in use"
        )
        svg.empty(
            "No skill usage recorded", "Sessions from sources that record skills will show here."
        )
        return svg.render()
    busiest = max(rows, key=lambda r: (r.friction, r.turns))
    worst_rank = rows.index(busiest) + 1
    sub = (
        f"Per 100 agent turns while the skill was in use, 95% interval. {busiest.skill} has the "
        f"most events ({busiest.friction}) but ranks {_ordinal(worst_rank)} by rate."
        if worst_rank > 1
        else "Per 100 agent turns while the skill was in use, with a 95% interval"
    )
    svg.header("Friction rate by skill", sub)
    svg.legend(
        [("c2", "worse than overall"), ("c1", "better than overall"), ("c0", "can't tell yet")],
        LEFT,
        92,
        shape="dot",
    )

    name_x, use_x, use_w = LEFT, 168, 120
    plot_x0, plot_x1 = 372, 800
    top = 132
    svg.text(use_x, top - 12, "agent turns", "tm")
    svg.text(plot_x0, top - 12, "friction per 100 turns", "tm")
    svg.text(RIGHT, top - 12, "events / turns", "tm end")
    hi = max(r.hi for r in rows) * 100
    ticks = nice_ticks(min(hi, 100.0), 4)
    scale = (plot_x1 - plot_x0) / ticks[-1]
    bottom = top + 34 * len(rows)
    for t in ticks:
        x = plot_x0 + t * scale
        svg.line(x + 0.5, top - 4, x + 0.5, bottom, "grid" if t else "axis")
        svg.text(x, bottom + 16, num(t, 0 if t >= 1 or t == 0 else 1), "tm mid num")
    ox = plot_x0 + overall * 100 * scale
    svg.line(ox, top - 4, ox, bottom, "line s0")
    svg.text(ox, bottom + 32, f"all skills {overall * 100:.1f}", "tb mid num")
    most_turns = max(r.turns for r in rows) or 1
    for i, r in enumerate(rows):
        cy = top + 34 * i + 17
        cls = "c2" if r.lo > overall else "c1" if r.hi < overall else "c0"
        stroke = cls.replace("c", "s")
        svg.text(name_x, cy + 4, r.skill, "ti")
        w = use_w * r.turns / most_turns
        if w:
            svg.path(rounded_right(use_x, cy - 6, max(w, 2), 12, 3), "neutral")
        svg.text(use_x + w + 6, cy + 4, num(r.turns), "tm num")
        tip = (
            f"{r.skill}: {r.friction} events in {r.turns} turns, {r.rate * 100:.1f} per 100 "
            f"(95% interval {r.lo * 100:.1f} to {r.hi * 100:.1f}), {r.sessions} sessions"
        )
        svg.open(tip)
        svg.rect(plot_x0, cy - 12, plot_x1 - plot_x0, 24, "hit")
        svg.line(
            plot_x0 + r.lo * 100 * scale,
            cy,
            plot_x0 + min(r.hi * 100, ticks[-1]) * scale,
            cy,
            f"line {stroke}",
        )
        svg.circle(plot_x0 + r.rate * 100 * scale, cy, 5.5, f"{cls} ring")
        svg.close()
        svg.text(RIGHT, cy + 4, f"{r.friction} / {num(r.turns)}", "tl end num")
    if dropped:
        svg.text(
            LEFT,
            bottom + 32,
            f"{dropped} events the labelling panel judged not a mistake are left out",
            "tm",
        )
    return svg.render()


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def session_barcode(insights: Insights, n: int = 12) -> str:
    """The sessions with the most friction, one row each, a tick per event along the session."""
    top = derive.top_sessions(insights, n)
    height = 140 + 36 * max(len(top), 3)
    svg = Svg(
        W,
        height,
        "Sessions with the most friction",
        "One row per session, a tick per friction event.",
        "barcode",
    )
    svg.header(
        "Where friction lands inside a session",
        f"The {len(top)} sessions with the most friction. Each tick is one event, "
        "placed by where it happened in the session",
    )
    if not top:
        svg.empty("No friction in this window", "Sessions with friction will line up here.")
        return svg.render()
    kinds = sorted({f.kind for _, _, fs in top for f in fs}, key=KIND_ORDER.index)
    svg.legend([(f"c{KIND_SLOT[k]}", KIND_NAME[k]) for k in kinds], LEFT, 92)
    track_x0, track_x1 = 300, RIGHT - 56
    y0 = 124
    for i, (stats, sid, fs) in enumerate(top):
        cy = y0 + 36 * i + 18
        project = (stats.project if stats else None) or "unknown project"
        source = (stats.source if stats else None) or fs[0].source
        svg.text(LEFT, cy - 2, sid[:8], "ti mono")
        svg.text(LEFT + 76, cy - 2, project, "tl")
        when = f"{_day(stats.started_at)}" if stats and stats.started_at else ""
        svg.text(LEFT, cy + 13, " · ".join(x for x in (source, when) if x), "tm")
        length = derive.session_length(stats, fs)
        svg.line(track_x0, cy + 0.5, track_x1, cy + 0.5, "grid")
        for f in fs:
            x = track_x0 + (track_x1 - track_x0) * f.event_index / max(length - 1, 1)
            tip = (
                f"{f.id} {KIND_NAME[f.kind]}"
                + (f" in {f.skill}" if f.skill else "")
                + f": {f.evidence}"
            )
            svg.open(tip, "mark")
            svg.rect(x - 6, cy - 14, 12, 28, "hit")
            svg.rect(x - 1.5, cy - 11, 3, 22, f"c{KIND_SLOT[f.kind]}", rx=1.5)
            svg.close()
        svg.text(RIGHT, cy + 4, str(len(fs)), "tb end num")
    yb = y0 + 36 * len(top) + 10
    svg.text(track_x0, yb, "session start", "tm")
    svg.text(track_x1, yb, "end", "tm end")
    svg.text(RIGHT, y0 - 2, "events", "tm end")
    return svg.render()


def friction_flow(insights: Insights) -> str:
    """From the category the panel agreed on, to the pattern, to the file that should change."""
    fl = derive.flow(insights)
    total = sum(n for _, n in fl.left)
    rows = max(len(fl.left), len(fl.middle), len(fl.right), 1)
    height = max(420, 130 + rows * 44)
    svg = Svg(
        W,
        height,
        "From friction to the file that should change",
        "Sankey of category to pattern to target.",
        "flow",
    )
    svg.header(
        "From friction to the file that should change",
        f"{num(total)} events by the category the labelling panel agreed on, the pattern they "
        "were grouped into, and the file each pattern points at",
    )
    if not total:
        svg.empty("No labelled friction yet", "Run retro analyze to label and group friction.")
        return svg.render()
    top, bottom = 104, height - 28
    node_w, gap = 10, 10
    cols = (190, 420, 704)
    scale = min(
        (bottom - top - gap * (len(col) - 1)) / total
        for col in (fl.left, fl.middle, fl.right)
        if col
    )
    for x, label in ((cols[0], "category"), (cols[1], "pattern"), (cols[2], "target")):
        svg.text(x + node_w / 2, top - 10, label, "tm mid")

    def layout(items: list[tuple[str, int]]) -> dict[str, tuple[float, float]]:
        used = sum(n for _, n in items) * scale + gap * (len(items) - 1)
        y = top + (bottom - top - used) / 2
        out = {}
        for key, n in items:
            out[key] = (y, n * scale)
            y += n * scale + gap
        return out

    left = layout([(c.value, n) for c, n in fl.left])
    mid = layout([(m, n) for m, n, _ in fl.middle])
    right = layout(fl.right)

    # Links first, so nodes and labels sit on top.
    out_y = {k: v[0] for k, v in left.items()}
    in_y = {k: v[0] for k, v in mid.items()}
    for c in CATEGORY_ORDER:
        for m, _, _ in fl.middle:
            n = fl.links_lm.get((c, m), 0)
            if not n:
                continue
            h = n * scale
            ya, yb = out_y[c.value], in_y[m]
            out_y[c.value] += h
            in_y[m] += h
            tip = f"{CATEGORY_NAME[c]} → {fl.titles.get(m, m)}: {n} events"
            svg.path(
                _ribbon(cols[0] + node_w, ya, cols[1], yb, h), f"c{CATEGORY_SLOT[c]} link", tip=tip
            )
    out_y = {k: v[0] for k, v in mid.items()}
    in_y = {k: v[0] for k, v in right.items()}
    for m, _, cat in fl.middle:
        for t, _ in fl.right:
            n = fl.links_mr.get((m, t), 0)
            if not n:
                continue
            h = n * scale
            ya, yb = out_y[m], in_y[t]
            out_y[m] += h
            in_y[t] += h
            slot = CATEGORY_SLOT[cat] if cat else 0
            svg.path(
                _ribbon(cols[1] + node_w, ya, cols[2], yb, h),
                f"c{slot} link",
                tip=f"{fl.titles.get(m, m)} → {t}: {n} events",
            )

    for c, n in fl.left:
        y, h = left[c.value]
        svg.rect(
            cols[0], y, node_w, h, f"c{CATEGORY_SLOT[c]}", tip=f"{CATEGORY_NAME[c]}: {n}", rx=2
        )
        svg.spans(
            cols[0] - 8, y + h / 2 + 4, [(CATEGORY_NAME[c], "ti"), (f"  {n}", "tm num")], "end"
        )
    for m, n, cat in fl.middle:
        y, h = mid[m]
        cls = f"c{CATEGORY_SLOT[cat]}" if cat else "c0"
        title = fl.titles.get(m, m)
        svg.rect(cols[1], y, node_w, h, cls, tip=f"{title}: {n}", rx=2)
        svg.spans(
            cols[1] + node_w + 8,
            y + h / 2 + 4,
            [(_clip(title, 40), "ti"), (f"  {n}", "tm num")],
            "halo",
        )
    for t, n in fl.right:
        y, h = right[t]
        svg.rect(cols[2], y, node_w, h, "c0", tip=f"{t}: {n}", rx=2)
        svg.spans(
            cols[2] + node_w + 8,
            y + h / 2 + 4,
            [(_clip(_target_label(t), 30), "ti mono"), (f"  {n}", "tm num")],
        )
    return svg.render()


def _target_label(target: str) -> str:
    """`~/.claude/skills/git-ship/SKILL.md` -> `git-ship/SKILL.md`; other paths as they are."""
    return target.split("/skills/", 1)[1] if "/skills/" in target else target


def _ribbon(x0: float, y0: float, x1: float, y1: float, h: float) -> str:
    mx = (x0 + x1) / 2
    return (
        f"M{x0:.1f},{y0:.1f}C{mx:.1f},{y0:.1f} {mx:.1f},{y1:.1f} {x1:.1f},{y1:.1f}"
        f"V{y1 + h:.1f}C{mx:.1f},{y1 + h:.1f} {mx:.1f},{y0 + h:.1f} {x0:.1f},{y0 + h:.1f}Z"
    )


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def agent_agreement(insights: Insights) -> str:
    """How often each pair of labelling agents chose the same category, and each one's mix."""
    ag = derive.agreement(insights)
    n = len(ag.agents)
    cell = 76 if n <= 4 else max(36, 300 // max(n, 1))
    height = max(360, 170 + cell * n)
    svg = Svg(
        W,
        height,
        "Labelling panel agreement",
        "Pairwise category agreement between labelling agents.",
        "agreement",
    )
    if not ag.agents:
        svg.header("Labelling panel agreement", "How often agents agree on what went wrong")
        svg.empty("No labels yet", "Run retro analyze to have agents label friction.")
        return svg.render()
    sub = (
        f"All {n} agree on {pct(ag.unanimous)} of events, a majority on another {pct(ag.majority)}"
        if n > 2
        else f"Both agents agree on {pct(ag.unanimous)} of the events they both labelled"
        if n == 2
        else "Only one agent has labelled friction so far"
    )
    svg.header("Labelling panel agreement", sub)
    mx, my = LEFT + 70.0, 128.0
    svg.text(LEFT, my - 22, "same category, pairwise (Cohen's κ)", "tm")
    for i, a in enumerate(ag.agents):
        svg.text(mx - 10, my + i * cell + cell / 2 + 4, a, "ti end")
        svg.text(mx + i * cell + cell / 2, my + n * cell + 18, a, "ti mid")
        for j, b in enumerate(ag.agents):
            x, y = mx + j * cell, my + i * cell
            if a == b:
                svg.rect(
                    x + 1,
                    y + 1,
                    cell - 2,
                    cell - 2,
                    "zero",
                    tip=f"{a}: {ag.counts[a]} labels",
                    rx=4,
                )
                svg.text(x + cell / 2, y + cell / 2 + 4, f"{ag.counts[a]}", "tm mid num")
                continue
            same, kappa, shared = ag.pairs[(a, b)]
            step = min(6, max(0, round(same * 6)))
            tip = f"{a} and {b} agree on {pct(same)} of {shared} shared events, κ {kappa:.2f}"
            svg.rect(x + 1, y + 1, cell - 2, cell - 2, f"q{step} mark", tip=tip, rx=4)
            svg.text(x + cell / 2, y + cell / 2 + 1, pct(same), f"qt{step} mid num tb")
            if cell >= 60:
                svg.text(x + cell / 2, y + cell / 2 + 17, f"κ {kappa:.2f}", f"qt{step} mid num")
    svg.text(mx + n * cell / 2, my + n * cell + 38, "diagonal: labels per agent", "tm mid")

    bx0, bx1 = mx + n * cell + 110, RIGHT
    svg.text(bx0, my - 22, "category mix per agent", "tm")
    present = [c for c in CATEGORY_ORDER if any(ag.mix[a][c] for a in ag.agents)]
    bar_h = 20.0
    row = float(max(34, min(cell, 48)))
    for i, a in enumerate(ag.agents):
        y = my + i * row + 6
        svg.text(bx0 - 10, y + bar_h / 2 + 4, a, "ti end")
        total = sum(ag.mix[a].values()) or 1
        x = float(bx0)
        width = bx1 - bx0
        segs = [c for c in present if ag.mix[a][c]]
        for k, c in enumerate(segs):
            w = width * ag.mix[a][c] / total
            gap = 2 if k < len(segs) - 1 else 0
            tip = f"{a}: {CATEGORY_NAME[c]} {ag.mix[a][c]} ({pct(ag.mix[a][c] / total)})"
            if k == len(segs) - 1:
                svg.path(rounded_right(x, y, w, bar_h, 4), f"c{CATEGORY_SLOT[c]} mark", tip=tip)
            else:
                svg.rect(x, y, w - gap, bar_h, f"c{CATEGORY_SLOT[c]} mark", tip=tip)
            x += w
    ly = my + len(ag.agents) * row + 24
    lx = float(bx0)
    for c in present:
        label = CATEGORY_NAME[c]
        advance = 16 + text_width(label) + 18
        if lx + advance > RIGHT:
            lx, ly = float(bx0), ly + 20
        svg.legend([(f"c{CATEGORY_SLOT[c]}", label)], lx, ly)
        lx += advance
    return svg.render()


def case_grid(insights: Insights) -> str:
    """Pass rate of every case on the original session, a baseline replay and a candidate replay."""
    runs = [r for r in insights.climbs if r.grid]
    tallest = max((len(r.grid) for r in runs), default=0)
    per_row = 2
    blocks = math.ceil(len(runs) / per_row) if runs else 1
    block_h = 80 + 26 * tallest
    height = max(300, 110 + blocks * block_h)
    svg = Svg(
        W, height, "Regression cases, before and after", "Pass rate per case and replay.", "cases"
    )
    svg.header(
        "Regression cases, before and after",
        "Pass rate over replay seeds. Every case fails on the session it came from, "
        "or it isn't admitted",
    )
    if not runs:
        svg.empty(
            "No climb runs yet", "Run retro climb to replay cases against candidate instructions."
        )
        return svg.render()
    panel_w = (RIGHT - LEFT - 40) / per_row
    for b, run in enumerate(runs):
        px = LEFT + (b % per_row) * (panel_w + 40)
        py = 104 + (b // per_row) * block_h
        _grid_panel(svg, run, px, py, panel_w)
    return svg.render()


_GRID_LABEL = {"original": "original", "baseline": "baseline", "candidate": "candidate"}


def _grid_panel(svg: Svg, run: ClimbRun, x0: float, y0: float, width: float) -> None:
    cols = derive.grid_columns(run)
    prefix = _common_prefix(list(run.grid))
    cell_w, cell_h = 62.0, 22.0
    name_w = width - cell_w * len(cols) - 54
    svg.text(x0, y0 + 8, _short_target(run.target), "tb mono")
    hy = y0 + 34
    for j, col in enumerate(cols):
        svg.text(x0 + name_w + cell_w * j + cell_w / 2, hy, _GRID_LABEL.get(col, col), "tm mid")
    svg.text(x0 + width, hy, "change", "tm end")
    last = cols[-1]
    first = "baseline" if "baseline" in cols else cols[0]
    ordered = sorted(
        run.grid.items(), key=lambda kv: (-kv[1].get(last, 0), -kv[1].get(first, 0), kv[0])
    )
    for i, (cid, row) in enumerate(ordered):
        y = hy + 10 + i * (cell_h + 4)
        name = cid[len(prefix) :] if prefix else cid
        svg.text(x0, y + 15, _clip(name, int(name_w / 6.6)), "ti")
        if cid in run.test_before:
            svg.text(x0 + name_w - 8, y + 15, "test", "tm end")
        for j, col in enumerate(cols):
            if col not in row:
                continue
            v = row[col]
            step = min(6, max(0, round(v * 6)))
            x = x0 + name_w + cell_w * j
            # A case that never passes gets the neutral cell, so improvement is what shows.
            cls, ink = (f"q{step}", f"qt{step}") if v > 0 else ("zero", "tm")
            tip = f"{cid} {col}: {pct(v)}"
            svg.rect(x + 1, y, cell_w - 2, cell_h, f"{cls} mark", tip=tip, rx=3)
            svg.text(x + cell_w / 2, y + 15, pct(v), f"{ink} mid num")
        delta = row.get(last, 0.0) - row.get(first, 0.0)
        mark = "▲" if delta > 0 else "▼" if delta < 0 else "\u2013"
        svg.text(
            x0 + width, y + 15, f"{mark} {abs(round(delta * 100))}" if delta else mark, "tl end num"
        )


def _common_prefix(ids: list[str]) -> str:
    if len(ids) < 2:
        return ""
    first = ids[0]
    cut = first.rfind(".") + 1
    prefix = first[:cut]
    return prefix if prefix and all(i.startswith(prefix) for i in ids) else ""


def _short_target(target: str) -> str:
    parts = target.split("/")
    return "/".join(parts[-3:]) if len(parts) > 3 else target


def climb_chart(insights: Insights) -> str:
    """Train and val pass rate per step of each climb, and the held-out test before and after."""
    runs = insights.climbs
    panel_h = 290
    height = max(300, 100 + panel_h * max(len(runs), 1))
    svg = Svg(
        W, height, "Hill climbing", "Pass rate per climb step and test before and after.", "climb"
    )
    svg.header(
        "Hill climbing the instructions",
        "Each step proposes an edit. It is kept only if val improves and no val case gets worse",
    )
    if not runs:
        svg.empty("No climb runs yet", "Run retro climb to see the loop at work.")
        return svg.render()
    lx = svg.legend(
        [("s1", "val, current instructions"), ("s2", "train, each candidate")],
        LEFT,
        92,
        shape="line",
    )
    lx = svg.legend([("c1", "kept")], lx + 8, 92, shape="dot")
    svg.circle(lx + 5, 88, 4.5, "hollow s1")
    lx += 16
    svg.text(lx, 92, "rejected", "tl")
    lx += text_width("rejected") + 26
    svg.legend([("c0", "test before"), ("c1", "test after")], lx, 92, shape="dot")
    for i, run in enumerate(runs):
        _climb_panel(svg, run, 112 + i * panel_h)
    return svg.render()


def _climb_panel(svg: Svg, run: ClimbRun, y0: float) -> None:
    svg.text(LEFT, y0 + 20, _short_target(run.target), "tb mono")
    x0, x1 = 72.0, 520.0
    top, base = y0 + 44, y0 + 224
    for t in (0.0, 0.25, 0.5, 0.75, 1.0):
        y = base - t * (base - top)
        svg.line(x0, y + 0.5, x1, y + 0.5, "grid" if t else "axis")
        svg.text(x0 - 10, y + 4, pct(t), "tm end num")
    steps = run.steps
    n = max(len(steps), 1)

    def sx(step: int) -> float:
        return x0 + 12 + (x1 - x0 - 24) * step / n

    def sy(v: float) -> float:
        return base - v * (base - top)

    for i in range(n + 1):
        svg.text(sx(i), base + 18, "start" if i == 0 else str(i), "tm mid num")
    svg.text((x0 + x1) / 2, base + 36, "step", "tm mid")

    ends: list[tuple[float, str, str]] = []
    if steps:
        train = [(sx(s.step), sy(s.train)) for s in steps]
        svg.path(_polyline(train), "line s2")
        for s, (x, y) in zip(steps, train, strict=True):
            svg.circle(x, y, 3.5, "c2 ring", tip=f"step {s.step} train {pct(s.train)}")
        ends.append((train[-1][1], pct(steps[-1].train), "tl num"))

    start = derive.climb_baseline(run)
    if start is not None:
        current = start
        pts = [(sx(0), sy(current))]
        for s in steps:
            if s.accepted:
                current = s.val
            pts.append((sx(s.step), sy(current)))
        svg.path(_steps(pts), "line s1")
        svg.circle(pts[0][0], pts[0][1], 4.5, "c1 ring", tip=f"starting val {pct(start)}")
        ends.append((pts[-1][1], pct(current), "tb num"))
    for s in steps:
        verdict = "kept" if s.accepted else "rejected"
        tip = f"step {s.step}: val {pct(s.val)}, train {pct(s.train)}, {verdict} ({s.reason})"
        svg.open(tip)
        svg.circle(sx(s.step), sy(s.val), 12, "hit")
        svg.circle(sx(s.step), sy(s.val), 5, "c1 ring" if s.accepted else "hollow s1")
        svg.close()
    # End labels: if the two lines finish close together, spread the labels apart.
    if len(ends) == 2 and abs(ends[0][0] - ends[1][0]) < 15:
        mid = (ends[0][0] + ends[1][0]) / 2
        lower, upper = sorted(range(2), key=lambda k: ends[k][0], reverse=True)
        ends[lower] = (mid + 7.5, *ends[lower][1:])
        ends[upper] = (mid - 7.5, *ends[upper][1:])
    for y, label, cls in ends:
        svg.text(x1 + 4, y + 4, label, cls)

    # Test split: never seen by the loop, scored once at each end.
    tx0, tx1 = 716.0, RIGHT - 12.0
    before, after = run.test_before, run.test_after
    ids = sorted(
        set(before) | set(after),
        key=lambda c: (after.get(c, 0) - before.get(c, 0), c),
        reverse=True,
    )
    svg.text(tx0 - 140, y0 + 20, "held-out test cases", "tb")
    mb, ma = derive.mean(list(before.values())), derive.mean(list(after.values()))
    svg.text(
        tx0 - 140, y0 + 40, f"never seen by the loop, mean {pct(mb)} \u2192 {pct(ma)}", "tl num"
    )
    if not ids:
        svg.text(tx0 - 140, y0 + 80, "no test cases", "tm")
        return
    for t in (0.0, 0.5, 1.0):
        x = tx0 + t * (tx1 - tx0)
        svg.line(x + 0.5, top, x + 0.5, base, "grid")
        svg.text(x, base + 18, pct(t), "tm mid num")
    row = min(28.0, (base - top - 12) / len(ids))
    prefix = _common_prefix(ids)
    for k, cid in enumerate(ids):
        y = top + 14 + k * row
        b, a = before.get(cid, 0.0), after.get(cid, 0.0)
        xb, xa = tx0 + b * (tx1 - tx0), tx0 + a * (tx1 - tx0)
        svg.open(f"{cid}: {pct(b)} before, {pct(a)} after")
        svg.rect(tx0, y - row / 2, tx1 - tx0, row, "hit")
        if a != b:
            svg.line(xb, y, xa, y, "line s1")
        svg.circle(xb, y, 4.5, "c0 ring")
        svg.circle(xa, y, 5, "c1 ring")
        svg.close()
        name = cid[len(prefix) :] if prefix else cid
        svg.text(tx0 - 12, y + 4, _clip(name, 20), "tl end", tip=cid)


def _day(day: date) -> str:
    return f"{day.day} {day:%b}"


def _wday(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def _polyline(points: list[tuple[float, float]]) -> str:
    return "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in points)


def _steps(points: list[tuple[float, float]]) -> str:
    """A step line: holds each value until the next point, then jumps."""
    d = f"M{points[0][0]:.1f},{points[0][1]:.1f}"
    for (_, y0), (x1, y1) in pairwise(points):
        d += f"H{x1:.1f}V{y1:.1f}" if y1 != y0 else f"H{x1:.1f}"
    return d


CHARTS: dict[str, Callable[[Insights], str]] = {
    "friction-calendar": calendar,
    "friction-by-kind": kinds_over_time,
    "skill-scorecard": skill_scorecard,
    "session-barcode": session_barcode,
    "friction-flow": friction_flow,
    "agent-agreement": agent_agreement,
    "case-grid": case_grid,
    "climb": climb_chart,
}


def category_slot(category: Category) -> int:
    return CATEGORY_SLOT[category]
