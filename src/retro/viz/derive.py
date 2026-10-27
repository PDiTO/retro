"""Numbers the charts draw, computed from `Insights` and nothing else.

Kept apart from the drawing code so each figure's arithmetic can be read, and tested, on
its own.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

from retro.friction import Friction, FrictionKind
from retro.insights import Category, ClimbRun, Insights, SessionStats
from retro.viz.theme import CATEGORY_ORDER

Z95 = 1.959964


def window(insights: Insights) -> tuple[date, date]:
    """First and last day of the window, inclusive.

    `retro scan` keeps every session touched in the window, and a long-running session can
    have started, and gathered friction, before it. The window stretches back to cover them.
    """
    end = insights.generated_at.date()
    start = end - timedelta(days=max(insights.window_days, 1) - 1)
    seen = [f.timestamp.date() for f in insights.friction if f.timestamp]
    seen += [s.started_at.date() for s in insights.sessions if s.started_at]
    return min([start, *seen]), max([end, *seen])


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def daily(insights: Insights) -> dict[date, tuple[int, int]]:
    """Day -> (friction events, sessions started), for every day in the window."""
    start, end = window(insights)
    friction = Counter(f.timestamp.date() for f in insights.friction if f.timestamp)
    sessions = Counter(s.started_at.date() for s in insights.sessions if s.started_at)
    days = {}
    day = start
    while day <= end:
        days[day] = (friction[day], sessions[day])
        day += timedelta(days=1)
    return days


@dataclass(frozen=True)
class Week:
    start: date
    kinds: Counter[FrictionKind]
    sessions: int
    days: int  # days of this week inside the window

    @property
    def total(self) -> int:
        return sum(self.kinds.values())


def weekly(insights: Insights) -> list[Week]:
    start, end = window(insights)
    first, last = week_start(start), week_start(end)
    kinds: dict[date, Counter[FrictionKind]] = defaultdict(Counter)
    sessions: Counter[date] = Counter()
    for f in insights.friction:
        if f.timestamp and start <= f.timestamp.date() <= end:
            kinds[week_start(f.timestamp.date())][f.kind] += 1
    for s in insights.sessions:
        if s.started_at and start <= s.started_at.date() <= end:
            sessions[week_start(s.started_at.date())] += 1
    weeks = []
    monday = first
    while monday <= last:
        inside = sum(1 for i in range(7) if start <= monday + timedelta(days=i) <= end)
        weeks.append(Week(monday, kinds[monday], sessions[monday], inside))
        monday += timedelta(days=7)
    return weeks


def consensus(insights: Insights) -> dict[str, Category]:
    """Friction id -> the category most agents chose. Ties go to the more confident side."""
    votes: dict[str, Counter[Category]] = defaultdict(Counter)
    weight: dict[str, dict[Category, float]] = defaultdict(lambda: defaultdict(float))
    for label in insights.labels:
        votes[label.friction_id][label.category] += 1
        weight[label.friction_id][label.category] += label.confidence
    out = {}
    for fid, counts in votes.items():
        out[fid] = max(
            counts,
            key=lambda c: (counts[c], weight[fid][c], -CATEGORY_ORDER.index(c)),
        )
    return out


def wilson(hits: int, trials: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a proportion. Behaves at 0 hits and small samples."""
    if trials <= 0:
        return 0.0, 1.0
    p = hits / trials
    denom = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denom
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class SkillRate:
    skill: str
    turns: int
    sessions: int
    friction: int
    lo: float
    hi: float

    @property
    def rate(self) -> float:
        return self.friction / self.turns if self.turns else 0.0


def skill_rates(insights: Insights) -> tuple[list[SkillRate], float, int]:
    """Friction per agent turn for each skill, with a 95% interval.

    Friction the labelling panel judged "not a mistake" is left out. Returns the rows
    sorted worst first, the rate across all skills, and how many events were left out.
    """
    turns: Counter[str] = Counter()
    used: Counter[str] = Counter()
    for s in insights.sessions:
        for skill, n in s.skills.items():
            turns[skill] += n
            used[skill] += 1
    verdict = consensus(insights)
    dropped = 0
    events: Counter[str] = Counter()
    for f in insights.friction:
        if not f.skill:
            continue
        if verdict.get(f.id) is Category.NOT_A_MISTAKE:
            dropped += 1
            continue
        events[f.skill] += 1
    rows = []
    for skill in set(turns) | set(events):
        n = max(turns[skill], events[skill])  # a turn can't hold more than one event here
        lo, hi = wilson(events[skill], n)
        rows.append(SkillRate(skill, turns[skill], used[skill], events[skill], lo, hi))
    rows.sort(key=lambda r: (-r.rate, r.skill))
    total_turns = sum(max(r.turns, r.friction) for r in rows)
    overall = sum(r.friction for r in rows) / total_turns if total_turns else 0.0
    return rows, overall, dropped


def session_length(stats: SessionStats | None, friction: list[Friction]) -> int:
    """Approximate event count. SessionStats has no length, so estimate one from its counts.

    A session is roughly: each prompt, an agent reply per prompt, and a call plus a result
    per tool call. Friction indexes are real, so the estimate never falls short of them.
    """
    furthest = max((f.event_index for f in friction), default=0) + 1
    if stats is None:
        return furthest
    if stats.events:
        return max(furthest, stats.events)
    return max(furthest, 2 * stats.prompts + 2 * stats.tool_calls + stats.prompts)


def top_sessions(
    insights: Insights, n: int = 12
) -> list[tuple[SessionStats | None, str, list[Friction]]]:
    by_session: dict[str, list[Friction]] = defaultdict(list)
    for f in insights.friction:
        by_session[f.session_id].append(f)
    stats = {s.id: s for s in insights.sessions}
    ranked = sorted(by_session.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:n]
    return [(stats.get(sid), sid, sorted(fs, key=lambda f: f.event_index)) for sid, fs in ranked]


UNCLUSTERED = "(no pattern)"
NO_TARGET = "(no target yet)"


@dataclass(frozen=True)
class Flow:
    left: list[tuple[Category, int]]
    middle: list[tuple[str, int, Category | None]]  # pattern id, events, category
    right: list[tuple[str, int]]
    links_lm: dict[tuple[Category, str], int]
    links_mr: dict[tuple[str, str], int]
    titles: dict[str, str]


def flow(insights: Insights) -> Flow:
    """Friction events from the category the panel gave them, to a pattern, to a target."""
    verdict = consensus(insights)
    pattern_of: dict[str, str] = {}
    for p in insights.patterns:
        for fid in p.friction_ids:
            pattern_of.setdefault(fid, p.id)
    by_id = {p.id: p for p in insights.patterns}
    lm: Counter[tuple[Category, str]] = Counter()
    mr: Counter[tuple[str, str]] = Counter()
    for f in insights.friction:
        pid = pattern_of.get(f.id)
        category = verdict.get(f.id) or (by_id[pid].category if pid else None)
        if category is None:
            continue
        lm[(category, pid or UNCLUSTERED)] += 1
        if pid:
            p = by_id[pid]
            mr[(pid, p.target or p.skill or NO_TARGET)] += 1
    left_counts: Counter[Category] = Counter()
    mid_counts: Counter[str] = Counter()
    for (c, m), n in lm.items():
        left_counts[c] += n
        mid_counts[m] += n
    left = [(c, left_counts[c]) for c in CATEGORY_ORDER if left_counts[c]]

    def mid_key(m: str) -> tuple[int, int, str]:
        if m == UNCLUSTERED:
            return (99, 0, m)
        return (CATEGORY_ORDER.index(by_id[m].category), -mid_counts[m], m)

    middle = [
        (m, mid_counts[m], by_id[m].category if m in by_id else None)
        for m in sorted(mid_counts, key=mid_key)
    ]
    # Order targets by where their patterns sit, which keeps links from crossing.
    position = {m: i for i, (m, _, _) in enumerate(middle)}
    right_counts: Counter[str] = Counter()
    pull: dict[str, float] = defaultdict(float)
    for (m, t), n in mr.items():
        right_counts[t] += n
        pull[t] += position[m] * n
    right = sorted(right_counts.items(), key=lambda kv: (pull[kv[0]] / kv[1], kv[0]))
    titles = {p.id: p.title for p in insights.patterns}
    return Flow(left, middle, right, dict(lm), dict(mr), titles)


@dataclass(frozen=True)
class Agreement:
    agents: list[str]
    counts: dict[str, int]
    pairs: dict[tuple[str, str], tuple[float, float, int]]  # (agreement, kappa, n shared)
    mix: dict[str, Counter[Category]]
    unanimous: float  # share of events labelled by 2+ agents where all agreed
    majority: float  # share where a strict majority agreed but not all
    multi: int  # events labelled by 2+ agents


def agreement(insights: Insights) -> Agreement:
    by_agent: dict[str, dict[str, Category]] = defaultdict(dict)
    for label in insights.labels:
        by_agent[label.agent][label.friction_id] = label.category
    agents = sorted(by_agent, key=lambda a: (-len(by_agent[a]), a))
    pairs = {}
    for a in agents:
        for b in agents:
            if a == b:
                continue
            shared = sorted(set(by_agent[a]) & set(by_agent[b]))
            if not shared:
                pairs[(a, b)] = (0.0, 0.0, 0)
                continue
            same = sum(by_agent[a][f] == by_agent[b][f] for f in shared) / len(shared)
            pa = Counter(by_agent[a][f] for f in shared)
            pb = Counter(by_agent[b][f] for f in shared)
            chance = sum(pa[c] * pb[c] for c in pa) / (len(shared) ** 2)
            kappa = (same - chance) / (1 - chance) if chance < 1 else 1.0
            pairs[(a, b)] = (same, kappa, len(shared))
    per_event: dict[str, list[Category]] = defaultdict(list)
    for agent in agents:
        for fid, c in by_agent[agent].items():
            per_event[fid].append(c)
    multi = [cs for cs in per_event.values() if len(cs) >= 2]
    unanimous = sum(len(set(cs)) == 1 for cs in multi)
    majority = sum(
        len(set(cs)) > 1 and Counter(cs).most_common(1)[0][1] * 2 > len(cs) for cs in multi
    )
    n = len(multi)
    return Agreement(
        agents=agents,
        counts={a: len(by_agent[a]) for a in agents},
        pairs=pairs,
        mix={a: Counter(by_agent[a].values()) for a in agents},
        unanimous=unanimous / n if n else 0.0,
        majority=majority / n if n else 0.0,
        multi=n,
    )


_REASON = re.compile(r"pass rate ([0-9.]+) -> ([0-9.]+)")


def climb_baseline(run: ClimbRun) -> float | None:
    """The val pass rate before the first step.

    `ClimbStep` stores only the candidate's scores. `retro.climb.compare` writes the
    baseline into the reason of an accepted step ("pass rate 0.30 -> 0.60"), and every step
    before the first acceptance was judged against that same baseline.
    """
    if run.val_before is not None:
        return run.val_before
    for step in run.steps:
        m = _REASON.search(step.reason)
        if step.accepted and m:
            return float(m.group(1))
    return None


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


GRID_ORDER = ("original", "baseline", "candidate")


def grid_columns(run: ClimbRun) -> list[str]:
    seen: list[str] = []
    for row in run.grid.values():
        for col in row:
            if col not in seen:
                seen.append(col)
    known = [c for c in GRID_ORDER if c in seen]
    return known + [c for c in seen if c not in known]
