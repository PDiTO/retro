"""Colours, type and a tiny SVG writer shared by every chart.

Each chart is a standalone SVG with its own `<style>`, so it renders the same in a README
`<img>`, opened directly, or inlined in the HTML report. Colours are CSS custom properties
on the `.rv` root, with dark-mode values selected under `prefers-color-scheme: dark`
(each dark step was chosen for the dark surface, not inverted from the light one). Marks
refer to colours by class, never by hex, so the swap happens in one place.

The palette is the dataviz skill's reference palette. Its categorical order is validated
for colour-vision deficiency on adjacent pairs in both modes. Three light-mode slots sit
below 3:1 against the surface, so every chart that uses them carries a legend with text
and the report adds a table view.
"""

from __future__ import annotations

from collections.abc import Iterable
from html import escape

from retro.friction import FrictionKind
from retro.insights import Category

FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
MONO = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace'

# Categorical slots 1..8, light and dark.
SERIES_LIGHT = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#6250d6",
    "#e34948",
)
SERIES_DARK = (
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#008300",
    "#9085e9",
    "#e66767",
)
# Blue sequential ramp, low to high. Dark mode runs from near the surface up to light blue.
SEQ_LIGHT = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")
SEQ_DARK = tuple(reversed(SEQ_LIGHT))
# Text that sits inside a sequential cell, per step.
SEQ_TEXT_LIGHT = ("#0b0b0b",) * 4 + ("#ffffff",) * 3
SEQ_TEXT_DARK = ("#f0efec",) * 3 + ("#0b0b0b",) * 4

LIGHT = {
    "surface": "#fcfcfb",
    "page": "#f9f9f7",
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    "zero": "#f0efec",
    "border": "rgba(11,11,11,0.10)",
    "neutral": "#c3c2b7",
}
DARK = {
    "surface": "#1a1a19",
    "page": "#0d0d0d",
    "ink": "#f0efec",
    "ink2": "#c3c2b7",
    "muted": "#898781",
    "grid": "#2c2c2a",
    "axis": "#383835",
    "zero": "#2c2c2a",
    "border": "rgba(255,255,255,0.10)",
    "neutral": "#52514e",
}

KIND_ORDER = (
    FrictionKind.CORRECTION,
    FrictionKind.INTERRUPT,
    FrictionKind.DENIAL,
    FrictionKind.ERROR_STREAK,
    FrictionKind.REVIEW_COMMENT,
    FrictionKind.CHECK_FAILURE,
)
KIND_SLOT = {kind: i + 1 for i, kind in enumerate(KIND_ORDER)}
KIND_NAME = {
    FrictionKind.CORRECTION: "correction",
    FrictionKind.INTERRUPT: "interrupt",
    FrictionKind.DENIAL: "denial",
    FrictionKind.ERROR_STREAK: "error streak",
    FrictionKind.REVIEW_COMMENT: "review comment",
    FrictionKind.CHECK_FAILURE: "check failure",
}

CATEGORY_ORDER = (
    Category.IGNORED_INSTRUCTION,
    Category.SKIPPED_VERIFICATION,
    Category.WRONG_APPROACH,
    Category.SCOPE_CREEP,
    Category.RISKY_ACTION,
    Category.TOOL_MISUSE,
    Category.STYLE,
    Category.NOT_A_MISTAKE,
)
# "Not a mistake" is the absence of a category, so it wears neutral gray, not a hue.
CATEGORY_SLOT = {
    c: (i + 1 if c is not Category.NOT_A_MISTAKE else 0) for i, c in enumerate(CATEGORY_ORDER)
}
CATEGORY_NAME = {
    Category.IGNORED_INSTRUCTION: "Ignored instruction",
    Category.SKIPPED_VERIFICATION: "Skipped verification",
    Category.WRONG_APPROACH: "Wrong approach",
    Category.SCOPE_CREEP: "Scope creep",
    Category.RISKY_ACTION: "Risky action",
    Category.TOOL_MISUSE: "Tool misuse",
    Category.STYLE: "Style",
    Category.NOT_A_MISTAKE: "Not a mistake",
}


def _vars(
    tokens: dict[str, str], series: Iterable[str], seq: Iterable[str], seq_text: Iterable[str]
) -> str:
    out = [f"--{k}:{v}" for k, v in tokens.items()]
    out += [f"--c{i}:{v}" for i, v in enumerate(series, start=1)]
    out += [f"--q{i}:{v}" for i, v in enumerate(seq)]
    out += [f"--qt{i}:{v}" for i, v in enumerate(seq_text)]
    return ";".join(out)


def css() -> str:
    """The stylesheet every chart embeds. Identical across charts, so inlining many is fine."""
    rules = [
        f".rv{{{_vars(LIGHT, SERIES_LIGHT, SEQ_LIGHT, SEQ_TEXT_LIGHT)}}}",
        "@media (prefers-color-scheme: dark){"
        f".rv{{{_vars(DARK, SERIES_DARK, SEQ_DARK, SEQ_TEXT_DARK)}}}}}",
        f".rv text{{font-family:{FONT};font-size:12px;fill:var(--ink2)}}",
        ".rv .bg{fill:var(--surface);stroke:var(--border)}",
        ".rv .tt{font-size:17px;font-weight:600;fill:var(--ink)}",
        ".rv .ts{font-size:13px;fill:var(--ink2)}",
        ".rv .tm{font-size:11px;fill:var(--muted)}",
        ".rv .tl{fill:var(--ink2)}",
        ".rv .ti{fill:var(--ink)}",
        ".rv .tb{font-weight:600;fill:var(--ink)}",
        ".rv .tbig{font-size:30px;font-weight:600;fill:var(--ink)}",
        ".rv .mono{font-family:" + MONO + ";font-size:11.5px}",
        ".rv .num{font-variant-numeric:tabular-nums}",
        ".rv .end{text-anchor:end}",
        ".rv .mid{text-anchor:middle}",
        ".rv .halo{paint-order:stroke;stroke:var(--surface);stroke-width:4px;"
        "stroke-linejoin:round}",
        ".rv .grid{stroke:var(--grid);stroke-width:1;fill:none}",
        ".rv .axis{stroke:var(--axis);stroke-width:1;fill:none}",
        ".rv .zero{fill:var(--zero)}",
        ".rv .void{fill:none;stroke:var(--axis);stroke-width:1}",
        ".rv .neutral{fill:var(--neutral)}",
        ".rv .ring{stroke:var(--surface);stroke-width:2}",
        ".rv .hollow{fill:var(--surface);stroke-width:2}",
        ".rv .line{fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}",
        ".rv .link{fill-opacity:0.32}",
        ".rv .link:hover{fill-opacity:0.6}",
        ".rv .hit{fill:transparent}",
        ".rv .mark:hover{opacity:0.82}",
        ".rv .c0{fill:var(--neutral)}",
        ".rv .s0{stroke:var(--neutral)}",
    ]
    rules += [f".rv .c{i}{{fill:var(--c{i})}}" for i in range(1, 9)]
    rules += [f".rv .s{i}{{stroke:var(--c{i})}}" for i in range(1, 9)]
    rules += [f".rv .q{i}{{fill:var(--q{i})}}" for i in range(7)]
    rules += [f".rv .qt{i}{{fill:var(--qt{i})}}" for i in range(7)]
    return "".join(rules)


def esc(value: object) -> str:
    return escape(str(value), quote=True)


def num(value: float, digits: int = 0) -> str:
    """1234 -> 1,234; 0.5 with digits=1 -> 0.5."""
    return f"{value:,.{digits}f}"


def pct(value: float) -> str:
    return f"{round(value * 100):d}%"


class Svg:
    """Accumulates SVG elements as strings. Every attribute and text node is escaped."""

    def __init__(
        self, width: int, height: int, title: str, desc: str = "", slug: str = "chart"
    ) -> None:
        self.slug = slug
        self.width = width
        self.height = height
        self.title = title
        self.desc = desc
        self.parts: list[str] = []

    def add(self, markup: str) -> None:
        self.parts.append(markup)

    def el(self, tag: str, tip: str | None = None, **attrs: object) -> None:
        body = f"<title>{esc(tip)}</title>" if tip else ""
        if body:
            self.parts.append(f"<{tag}{_attrs(attrs)}>{body}</{tag}>")
        else:
            self.parts.append(f"<{tag}{_attrs(attrs)}/>")

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        cls: str,
        *,
        tip: str | None = None,
        rx: float = 0,
    ) -> None:
        self.el(
            "rect",
            tip,
            x=_f(x),
            y=_f(y),
            width=_f(max(w, 0)),
            height=_f(max(h, 0)),
            rx=_f(rx) if rx else None,
            **{"class": cls},
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, cls: str) -> None:
        self.el("line", None, x1=_f(x1), y1=_f(y1), x2=_f(x2), y2=_f(y2), **{"class": cls})

    def circle(self, cx: float, cy: float, r: float, cls: str, *, tip: str | None = None) -> None:
        self.el("circle", tip, cx=_f(cx), cy=_f(cy), r=_f(r), **{"class": cls})

    def path(self, d: str, cls: str, *, tip: str | None = None) -> None:
        self.el("path", tip, d=d, **{"class": cls})

    def text(
        self, x: float, y: float, value: object, cls: str = "tl", *, tip: str | None = None
    ) -> None:
        inner = esc(value) + (f"<title>{esc(tip)}</title>" if tip else "")
        self.parts.append(f'<text x="{_f(x)}" y="{_f(y)}" class="{esc(cls)}">{inner}</text>')

    def spans(self, x: float, y: float, parts: list[tuple[str, str]], cls: str = "tl") -> None:
        """One line of text in several styles, e.g. a label followed by a muted count."""
        inner = "".join(f'<tspan class="{esc(c)}">{esc(v)}</tspan>' for v, c in parts)
        self.parts.append(f'<text x="{_f(x)}" y="{_f(y)}" class="{esc(cls)}">{inner}</text>')

    def open(self, tip: str | None = None, cls: str | None = None) -> None:
        self.parts.append(
            f"<g{_attrs({'class': cls})}>" + (f"<title>{esc(tip)}</title>" if tip else "")
        )

    def close(self) -> None:
        self.parts.append("</g>")

    def header(self, title: str, subtitle: str = "") -> None:
        self.text(28, 40, title, "tt")
        if subtitle:
            self.text(28, 62, subtitle, "ts")

    def legend(
        self, items: list[tuple[str, str]], x: float, y: float, *, shape: str = "rect"
    ) -> float:
        """Swatch-and-label pairs in a row. Returns the x after the last item."""
        for cls, label in items:
            if shape == "line":
                self.line(x, y - 4, x + 14, y - 4, f"line {cls}")
            elif shape == "dot":
                self.circle(x + 5, y - 4, 4.5, cls)
            else:
                self.rect(x, y - 9, 10, 10, cls, rx=2)
            self.text(x + (20 if shape == "line" else 16), y, label, "tl")
            x += (20 if shape == "line" else 16) + text_width(label) + 18
        return x

    def empty(self, message: str, hint: str = "") -> None:
        cy = self.height / 2 + 14
        self.text(self.width / 2, cy, message, "ts mid")
        if hint:
            self.text(self.width / 2, cy + 20, hint, "tm mid")

    def render(self) -> str:
        w, h, ids = self.width, self.height, f"rv-{self.slug}"
        head = (
            f'<svg xmlns="http://www.w3.org/2000/svg" class="rv" role="img" '
            f'width="{w}" height="{h}" viewBox="0 0 {w} {h}" aria-labelledby="{ids}-t {ids}-d">'
            f'<title id="{ids}-t">{esc(self.title)}</title>'
            f'<desc id="{ids}-d">{esc(self.desc)}</desc>'
            f"<style>{css()}</style>"
            f'<rect class="bg" x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="12"/>'
        )
        return head + "".join(self.parts) + "</svg>"


def rounded_top(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """A bar path with rounded corners at the data end (top) and square at the baseline."""
    r = max(0.0, min(r, w / 2, h))
    return (
        f"M{_f(x)},{_f(y + h)}V{_f(y + r)}Q{_f(x)},{_f(y)} {_f(x + r)},{_f(y)}"
        f"H{_f(x + w - r)}Q{_f(x + w)},{_f(y)} {_f(x + w)},{_f(y + r)}V{_f(y + h)}Z"
    )


def rounded_right(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """A horizontal bar path, rounded at the right (data) end."""
    r = max(0.0, min(r, h / 2, w))
    return (
        f"M{_f(x)},{_f(y)}H{_f(x + w - r)}Q{_f(x + w)},{_f(y)} {_f(x + w)},{_f(y + r)}"
        f"V{_f(y + h - r)}Q{_f(x + w)},{_f(y + h)} {_f(x + w - r)},{_f(y + h)}H{_f(x)}Z"
    )


def text_width(text: str, size: float = 12) -> float:
    """Rough rendered width of system-ui text, enough to lay out legends and labels."""
    wide = sum(1 for ch in text if ch in "mwMW@%")
    narrow = sum(1 for ch in text if ch in "ilIjtf.,:;'|!() ")
    return size * (0.56 * len(text) + 0.3 * wide - 0.27 * narrow)


def nice_ticks(top: float, count: int = 4) -> list[float]:
    """Round tick values from 0 up to at least `top`."""
    if top <= 0:
        return [0.0, 1.0]
    raw = top / count
    magnitude = _mag(raw)
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    ticks = [0.0]
    while ticks[-1] < top - 1e-9:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def _mag(raw: float) -> float:
    m = 1.0
    while m * 10 <= raw:
        m *= 10
    while m > raw:
        m /= 10
    return m


def _f(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


def _attrs(attrs: dict[str, object]) -> str:
    return "".join(f' {k.replace("_", "-")}="{esc(v)}"' for k, v in attrs.items() if v is not None)
