"""Inline-SVG charts for the landing page's market section, rendered at build time.

No chart library and no JavaScript: the page is static, the data changes twice
a day, and five single-series charts do not justify a runtime. Rendering here
also means the charts are testable like any other function.

Conventions (shared by every chart):
  * one series, one colour -- the `mark` class; colour lives in landing.css;
  * 2px lines, bars capped at 14px with a rounded data end, hairline solid grid;
  * text never wears the data colour: labels and values use text classes;
  * every value is reachable without hover: each chart ships a table view, and
    hover (an SVG <title>) only repeats what the table says.
"""

from __future__ import annotations

import html
import math
from collections.abc import Sequence
from dataclasses import dataclass

# viewBox widths match the rendered widths of a full and a half-width panel,
# so 11px text in the SVG renders at about 11px on the page.
FULL, HALF = 940, 440
PAD_L, PAD_R, PAD_T, PAD_B = 44, 72, 16, 30


def fmt_int(n: float) -> str:
    return f"{round(n):,}"


def fmt_compact(n: float) -> str:
    """Axis ticks: 0 / 950 / 1.5K / 12K."""
    if abs(n) >= 10_000:
        return f"{n / 1000:.0f}K"
    if abs(n) >= 1_000:
        return f"{n / 1000:.1f}K".replace(".0K", "K")
    return f"{n:.0f}"


def fmt_pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def nice_ticks(top: float, count: int = 4) -> list[float]:
    """0 and evenly spaced round steps whose last tick covers `top`."""
    if top <= 0:
        return [0.0, 1.0]
    raw = top / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    n = math.ceil(top / step - 1e-9)
    return [round(i * step, 10) for i in range(n + 1)]


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    label: str  # the x value as a reader would say it
    tip: str  # the hover text


def line_chart(
    points: Sequence[Point],
    *,
    title: str,
    y_max: float | None = None,
    y_format: str = "int",
    step: bool = False,
    marker: Point | None = None,
    marker_label: str = "",
    width: int = FULL,
    height: int = 260,
) -> str:
    """A single-series line (or step) chart with an area wash and an end label."""
    if not points:
        return ""
    fmt = fmt_pct if y_format == "pct" else fmt_compact
    top = y_max if y_max is not None else max(p.y for p in points)
    ticks = [0, 0.25, 0.5, 0.75, 1.0] if y_format == "pct" else nice_ticks(top)
    y_hi = ticks[-1] or 1
    x_lo, x_hi = points[0].x, points[-1].x
    span = (x_hi - x_lo) or 1
    W, H = width, height
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def sx(x: float) -> float:
        return PAD_L + (x - x_lo) / span * pw

    def sy(y: float) -> float:
        return PAD_T + ph - y / y_hi * ph

    coords: list[tuple[float, float]] = []
    for i, p in enumerate(points):
        if step and i:
            coords.append((sx(p.x), sy(points[i - 1].y)))
        coords.append((sx(p.x), sy(p.y)))
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(coords))
    base = sy(0)
    area = f"{path} L{coords[-1][0]:.1f},{base:.1f} L{coords[0][0]:.1f},{base:.1f} Z"

    parts = [
        f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{_esc(title)}">',
    ]
    for t in ticks:
        y = sy(t)
        parts.append(
            f'<line class="grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>'
            f'<text class="tick" x="{PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end">{fmt(t)}</text>'
        )
    for p in (points[0], points[-1]):
        anchor = "start" if p is points[0] else "end"
        parts.append(
            f'<text class="tick" x="{sx(p.x):.1f}" y="{H - 10}" text-anchor="{anchor}">'
            f"{_esc(p.label)}</text>"
        )
    parts.append(f'<path class="area" d="{area}"/>')
    parts.append(f'<path class="line" d="{path}"/>')
    if marker is not None:
        mx, my = sx(marker.x), sy(marker.y)
        parts.append(
            f'<line class="guide" x1="{mx:.1f}" x2="{mx:.1f}" y1="{my:.1f}" y2="{base:.1f}"/>'
            f"{_dot(mx, my)}"
            f'<text class="value" x="{mx + 8:.1f}" y="{my - 8:.1f}">{_esc(marker_label)}</text>'
        )
    last = points[-1]
    end_value = fmt_pct(last.y) if y_format == "pct" else fmt_int(last.y)
    parts.append(
        f"{_dot(sx(last.x), sy(last.y))}"
        f'<text class="value" x="{sx(last.x) + 9:.1f}" y="{sy(last.y) + 4:.1f}">'
        f"{end_value}</text>"
    )
    # Hover: one full-height band per point, so the target is a column, not a dot.
    for i, p in enumerate(points):
        left = sx(points[i - 1].x) if i else sx(p.x)
        right = sx(points[i + 1].x) if i + 1 < len(points) else sx(p.x)
        x0, x1 = (left + sx(p.x)) / 2, (sx(p.x) + right) / 2
        parts.append(
            f'<rect class="hit" x="{x0:.1f}" y="{PAD_T}" width="{max(x1 - x0, 1):.1f}" '
            f'height="{ph}"><title>{_esc(p.tip)}</title></rect>'
        )
    parts.append("</svg>")
    return "".join(parts)


@dataclass(frozen=True)
class Bar:
    label: str
    value: float
    shown: str  # the value as printed at the bar's tip
    tip: str


def bar_chart(bars: Sequence[Bar], *, title: str, label_width: int = 130, width: int = HALF) -> str:
    """Horizontal single-series bars, longest first as given, value at the tip."""
    if not bars:
        return ""
    W = width
    row, bar_h, value_w = 26, 14, 56
    height = row * len(bars) + 8
    top = max(b.value for b in bars) or 1
    track = W - label_width - value_w
    parts = [
        f'<svg class="chart bars" viewBox="0 0 {W} {height}" role="img" aria-label="{_esc(title)}">'
    ]
    for i, b in enumerate(bars):
        y = 4 + i * row
        length = max(b.value / top * track, 2)
        x0 = label_width
        parts.append(
            f'<g class="bar"><title>{_esc(b.tip)}</title>'
            f'<rect class="hit" x="0" y="{y}" width="{W}" height="{row}"/>'
            f'<text class="label" x="{label_width - 10}" y="{y + row / 2 + 4:.1f}" '
            f'text-anchor="end">{_esc(b.label)}</text>'
            f'<path class="mark" d="{_bar_path(x0, y + (row - bar_h) / 2, length, bar_h)}"/>'
            f'<text class="value" x="{x0 + length + 8:.1f}" y="{y + row / 2 + 4:.1f}">'
            f"{_esc(b.shown)}</text></g>"
        )
    parts.append("</svg>")
    return "".join(parts)


def _dot(cx: float, cy: float, r: float = 4.5) -> str:
    """A filled dot drawn as two arcs, ringed in the surface colour by CSS."""
    return (
        f'<path class="dot" d="M{cx - r:.1f},{cy:.1f} a{r},{r} 0 1,0 {2 * r},0 '
        f'a{r},{r} 0 1,0 {-2 * r},0 Z"/>'
    )


def _bar_path(x: float, y: float, length: float, h: float) -> str:
    """A bar square at the baseline with a 4px rounded data end."""
    r = min(4.0, length / 2, h / 2)
    return (
        f"M{x:.1f},{y:.1f} H{x + length - r:.1f} "
        f"Q{x + length:.1f},{y:.1f} {x + length:.1f},{y + r:.1f} "
        f"V{y + h - r:.1f} Q{x + length:.1f},{y + h:.1f} {x + length - r:.1f},{y + h:.1f} "
        f"H{x:.1f} Z"
    )


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """The table view every chart carries, in a closed <details>."""
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return (
        f'<details class="table-view"><summary>Table view</summary>'
        f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></details>"
    )
