"""Write the README's example charts and report from synthetic demo data.

    uv run python scripts/make_readme_images.py

Builds `retro.demo.build_demo()` (seeded, invented, never read from this machine) and
writes docs/img/<chart>.svg plus docs/img/report.html. Run it again after changing a chart.
"""

from __future__ import annotations

import sys
from pathlib import Path

from retro.demo import build_demo
from retro.viz import export

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "img"


def main() -> int:
    insights = build_demo()
    for path in export(insights, OUT):
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
