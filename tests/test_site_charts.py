"""Inline-SVG charts: scales, formatting, escaping, and the table view."""

import pytest

from scripts import site_charts as charts


@pytest.mark.parametrize(
    ("top", "expected"),
    [
        (11_711, [0, 5_000, 10_000, 15_000]),
        (100, [0, 25, 50, 75, 100]),
        (7, [0, 2, 4, 6, 8]),
        (0, [0.0, 1.0]),
    ],
)
def test_nice_ticks_cover_the_top_in_round_steps(top: float, expected: list[float]) -> None:
    assert charts.nice_ticks(top) == expected


@pytest.mark.parametrize(
    ("n", "shown"), [(0, "0"), (950, "950"), (1_000, "1K"), (1_500, "1.5K"), (12_000, "12K")]
)
def test_compact_axis_numbers(n: float, shown: str) -> None:
    assert charts.fmt_compact(n) == shown


def test_formatters() -> None:
    assert charts.fmt_int(11711.4) == "11,711"
    assert charts.fmt_pct(0.4219) == "42%"


def _points() -> list[charts.Point]:
    return [
        charts.Point(x=0, y=100, label="day 0", tip="a"),
        charts.Point(x=1, y=50, label="day 1", tip="b"),
        charts.Point(x=2, y=200, label="day 2", tip="<c>"),
    ]


def test_line_chart_draws_a_hover_band_per_point_and_labels_the_end() -> None:
    svg = charts.line_chart(_points(), title="T & U")
    assert svg.count('class="hit"') == 3
    assert 'aria-label="T &amp; U"' in svg
    assert "<title>&lt;c&gt;</title>" in svg  # hover text is escaped
    assert ">200</text>" in svg  # end value, as text, not in the data colour
    assert 'viewBox="0 0 940 260"' in svg


def test_step_chart_holds_each_value_until_the_next_point() -> None:
    plain = charts.line_chart(_points(), title="t")
    step = charts.line_chart(_points(), title="t", step=True)
    # A step path adds a horizontal segment before every point after the first.
    assert (
        step.split('class="line" d="')[1].count("L")
        == plain.split('class="line" d="')[1].count("L") + 2
    )


def test_percentage_chart_uses_a_fixed_zero_to_one_axis() -> None:
    pts = [
        charts.Point(x=0, y=1.0, label="a", tip="a"),
        charts.Point(x=5, y=0.4, label="b", tip="b"),
    ]
    svg = charts.line_chart(pts, title="t", y_format="pct", width=charts.HALF)
    assert ">100%</text>" in svg and ">0%</text>" in svg and ">40%</text>" in svg
    assert 'viewBox="0 0 440 260"' in svg


def test_marker_is_drawn_with_its_label() -> None:
    pts = _points()
    svg = charts.line_chart(pts, title="t", marker=pts[1], marker_label="median 1 day")
    assert 'class="guide"' in svg and "median 1 day" in svg


def test_empty_series_render_nothing() -> None:
    assert charts.line_chart([], title="t") == ""
    assert charts.bar_chart([], title="t") == ""


def test_bars_scale_to_the_largest_and_print_the_value_at_the_tip() -> None:
    bars = [
        charts.Bar(label="SQL", value=0.7, shown="70%", tip="SQL tip"),
        charts.Bar(label="<dbt>", value=0.35, shown="35%", tip="dbt tip"),
    ]
    svg = charts.bar_chart(bars, title="t", label_width=100)
    assert svg.count('class="mark"') == 2
    assert "&lt;dbt&gt;" in svg and ">70%</text>" in svg
    # the longest bar spans the whole track: 440 - 100 label - 56 value column
    assert f"H{100 + 284 - 4:.1f}" in svg  # minus the 4px rounded end


def test_bar_path_is_square_at_the_baseline_and_rounded_at_the_end() -> None:
    path = charts._bar_path(10, 0, 100, 14)
    assert path.startswith("M10.0,0.0 H106.0") and path.endswith("H10.0 Z")
    assert path.count("Q") == 2


def test_dot_is_a_closed_two_arc_path_centred_on_the_point() -> None:
    dot = charts._dot(100, 50, r=4)
    assert dot.startswith('<path class="dot" d="M96.0,50.0 ')
    assert dot.count(" a4,4 0 1,0 ") == 2 and dot.endswith('Z"/>')


def test_responsive_draws_the_chart_at_panel_and_phone_width() -> None:
    out = charts.responsive(lambda w: charts.line_chart(_points(), title="t", width=w), charts.FULL)
    wide, narrow = out.split('<div class="chart-narrow">')
    assert wide.startswith('<div class="chart-wide">') and 'viewBox="0 0 940 260"' in wide
    assert f'viewBox="0 0 {charts.NARROW} 260"' in narrow


def test_table_view_escapes_cells() -> None:
    out = charts.table(["A"], [["<x>"]])
    assert "<details" in out and "<td>&lt;x&gt;</td>" in out
