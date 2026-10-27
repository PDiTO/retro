"""Terminal summary of friction across sessions."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from retro.friction import Friction, FrictionKind
from retro.insights import Insights


def render(
    friction: Sequence[Friction],
    sessions: int,
    console: Console,
    *,
    examples: int = 3,
    top: int = 10,
) -> None:
    affected = len({f.session_id for f in friction})
    console.print(
        f"[bold]{len(friction)}[/] friction events in [bold]{affected}[/] of "
        f"[bold]{sessions}[/] sessions"
    )
    if not friction:
        return

    console.print(_counts("kind", Counter(f.kind.value for f in friction), top))
    console.print(_counts("skill", Counter(f.skill or "(none)" for f in friction), top))
    console.print(_counts("project", Counter(f.project or "?" for f in friction), top))

    if examples <= 0:
        return
    for kind in FrictionKind:
        items = [f for f in friction if f.kind is kind]
        if not items:
            continue
        console.rule(f"{kind.value} ({len(items)})", align="left")
        latest = sorted(items, key=lambda f: f.timestamp.isoformat() if f.timestamp else "")
        for f in reversed(latest[-examples:]):
            where = " · ".join(x for x in (f.project, f.skill, f.detail) if x)
            console.print(f"[cyan]{f.id}[/] [dim]{escape(where)}[/]")
            if f.context:
                console.print(f"  [dim]agent:[/] {escape(f.context)}", highlight=False)
            console.print(f"  [bold]→[/] {escape(f.evidence)}", highlight=False)


def _counts(title: str, counts: Counter[str], top: int) -> Table:
    table = Table(title=f"by {title}", title_justify="left", show_header=False, box=None)
    table.add_column(justify="left")
    table.add_column(justify="right")
    for name, n in counts.most_common(top):
        table.add_row(name, str(n))
    if len(counts) > top:
        table.add_row(f"… {len(counts) - top} more", "")
    return table


def render_patterns(insights: Insights, console: Console) -> None:
    if not insights.patterns:
        console.print("no recurring patterns found")
        return
    proposals = {p.pattern_id: p for p in insights.proposals}
    console.rule("patterns", align="left")
    for p in insights.patterns:
        where = " · ".join(x for x in (p.category.value, p.skill, p.target) if x)
        console.print(
            f"[bold]{escape(p.title)}[/] [dim]({len(p.friction_ids)} events · {escape(where)})[/]"
        )
        console.print(f"  {escape(p.description)}", highlight=False)
        if proposal := proposals.get(p.id):
            added = [
                line[1:]
                for line in proposal.change.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            ]
            for line in added[:4]:
                console.print(f"  [green]+ {escape(line)}[/]", highlight=False)
            if len(added) > 4:
                console.print(f"  [dim]… {len(added) - 4} more lines[/]")
