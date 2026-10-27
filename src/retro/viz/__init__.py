"""Charts and a report for `Insights`, as standalone SVG and one offline HTML page.

Pure standard library: no plotting package, no JavaScript, nothing fetched at view time.
Every chart is a function from `Insights` to an SVG string (see `retro.viz.charts`), and
`render_html` stitches them into a report with a table view under each.
"""

from __future__ import annotations

from pathlib import Path

from retro.insights import Insights
from retro.viz.charts import CHARTS
from retro.viz.report import render_html

__all__ = ["CHARTS", "export", "render_html"]


def export(insights: Insights, out_dir: Path) -> list[Path]:
    """Write report.html and one SVG per chart into `out_dir`. Returns the paths written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    report = out_dir / "report.html"
    report.write_text(render_html(insights), encoding="utf-8")
    written.append(report)
    for name, chart in CHARTS.items():
        path = out_dir / f"{name}.svg"
        path.write_text(chart(insights) + "\n", encoding="utf-8")
        written.append(path)
    return written
