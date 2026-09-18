"""ui/curve_editor.py: the ``PWMCurveNodes`` / ``FreqCurveNodes`` node editor (02 §3.4).

Codec round trip on the strings the vendor's own ``BkLayerPara.xml`` carries, the
shape rules as warnings, the node math against
:class:`nexcut.plan.pwm_schedule.CurveNodes` (the planner's own interpolation),
and the widget's add/remove/edit behaviour.  Offscreen Qt; nothing here touches a
machine (PORT-PLAN §8).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from nexcut.io.params import read_params  # noqa: E402
from nexcut.plan.pwm_schedule import CurveNodes  # noqa: E402
from nexcut.ui.curve_editor import (  # noqa: E402
    AXIS_MAX,
    CurveEditor,
    CurveError,
    curve_problems,
    evaluate,
    format_curve_text,
    format_value,
    parse_curve_text,
    sample,
)

VENDOR_CURVES = (
    "0,0,15,35,37,69,59,92,76,100,100,100",
    "0,21,11,59,40,88,60,100,100,100",
    "0,48,22,66,50,87,100,100",
    "0,100,100,100",
    "",
)
"""The ``PWMCurveNodes`` / ``FreqCurveNodes`` spellings quoted by 02 §3.4."""


@pytest.fixture(scope="module", autouse=True)
def qapp() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture()
def editor() -> Iterator[CurveEditor]:
    widget = CurveEditor("Power")
    yield widget
    widget.deleteLater()


# ============================================================================= codec


@pytest.mark.parametrize("text", VENDOR_CURVES)
def test_vendor_curve_strings_round_trip_exactly(text: str) -> None:
    """The codec is byte-exact on every curve spelling of 02 §3.4, empty string included."""
    assert format_curve_text(parse_curve_text(text)) == text


def test_parse_reads_pairs_in_file_order_without_sorting() -> None:
    """Nodes keep the order the file gives them (a re-sort would change the bytes)."""
    assert parse_curve_text("40,88,0,21") == [(40.0, 88.0), (0.0, 21.0)]
    assert format_curve_text([(40.0, 88.0), (0.0, 21.0)]) == "40,88,0,21"


def test_parse_accepts_the_semicolon_separator_pwm_schedule_accepts() -> None:
    assert parse_curve_text("0;0;100;100") == [(0.0, 0.0), (100.0, 100.0)]


def test_parse_refuses_an_odd_number_of_values() -> None:
    with pytest.raises(CurveError, match="odd number"):
        parse_curve_text("0,0,100")


def test_parse_refuses_text_that_is_not_a_number() -> None:
    with pytest.raises(CurveError, match="not a number"):
        parse_curve_text("0,0,a,100")


def test_format_value_writes_integers_without_a_decimal_point() -> None:
    """Every value in the vendor files is an integer spelling (02 §3.4)."""
    assert format_value(35.0) == "35"
    assert format_value(-0.0) == "0"
    assert format_value(12.5) == "12.5"


# ======================================================================== validation


def test_an_empty_curve_has_no_problems() -> None:
    """Eight of the eleven slots of the vendor BkLayerPara.xml carry an empty curve."""
    assert curve_problems([]) == []


@pytest.mark.parametrize("text", [t for t in VENDOR_CURVES if t])
def test_every_vendor_curve_satisfies_the_shape_rules(text: str) -> None:
    assert curve_problems(parse_curve_text(text)) == []


def test_validation_reports_a_value_outside_the_percent_range() -> None:
    problems = curve_problems([(0.0, 0.0), (50.0, 140.0), (100.0, 100.0)])
    assert any("140" in p and "outside" in p for p in problems)


def test_validation_reports_a_speed_that_does_not_increase() -> None:
    problems = curve_problems([(0.0, 0.0), (50.0, 30.0), (50.0, 60.0), (100.0, 100.0)])
    assert any("not above" in p for p in problems)


def test_validation_reports_a_curve_that_does_not_start_at_zero_or_end_at_100() -> None:
    problems = curve_problems([(10.0, 0.0), (90.0, 90.0)])
    assert any("start at 0" in p for p in problems)
    assert any("ends at 100" in p for p in problems)


def test_a_single_node_is_reported_as_too_short() -> None:
    assert any("at least two" in p for p in curve_problems([(0.0, 0.0)]))


# ======================================================================== node math


def test_evaluate_matches_the_planner_curve() -> None:
    """The preview must not disagree with :class:`CurveNodes`, which plans the real duty."""
    text = VENDOR_CURVES[0]
    nodes = parse_curve_text(text)
    planner = CurveNodes.parse(text)
    for speed in (0.0, 7.5, 15.0, 37.0, 50.0, 76.0, 100.0):
        assert evaluate(nodes, speed) == float(planner.evaluate([speed], AXIS_MAX, AXIS_MAX)[0])


def test_evaluate_interpolates_between_two_nodes() -> None:
    """``0,0 -> 100,100`` is the identity; the planner rounds to whole percent."""
    nodes = parse_curve_text("0,0,100,100")
    assert evaluate(nodes, 0.0) == 0.0
    assert evaluate(nodes, 50.0) == 50.0
    assert evaluate(nodes, 100.0) == 100.0


def test_evaluate_clamps_below_the_first_node() -> None:
    nodes = parse_curve_text("20,40,80,90")
    assert evaluate(nodes, 0.0) == evaluate(nodes, 20.0)
    assert evaluate(nodes, 90.0) == evaluate(nodes, 80.0)


def test_the_nominal_speed_always_gives_the_full_output() -> None:
    """``CurveNodes.evaluate`` overrides ``v == nominal`` with the base value; so does the
    preview, because it is the same code."""
    nodes = parse_curve_text("20,40,80,90")
    assert evaluate(nodes, AXIS_MAX) == AXIS_MAX


def test_an_empty_curve_evaluates_flat_at_100_percent() -> None:
    """``CurveNodes.parse("")`` is the flat curve; the editor must agree (02 §3.4)."""
    assert evaluate([], 0.0) == 100.0
    assert evaluate([], 55.0) == 100.0


def test_sample_spans_the_whole_axis() -> None:
    points = sample(parse_curve_text("0,0,100,100"), 5)
    assert [p[0] for p in points] == [0.0, 25.0, 50.0, 75.0, 100.0]
    assert points[-1][1] == 100.0


def test_sample_needs_at_least_two_points() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        sample([], 1)


# =========================================================================== widget


def test_the_editor_shows_one_row_per_node(editor: CurveEditor) -> None:
    editor.set_text(VENDOR_CURVES[0])
    assert editor.table.rowCount() == 6
    assert editor.table.item(1, 0).text() == "15"
    assert editor.table.item(1, 1).text() == "35"
    assert editor.text() == VENDOR_CURVES[0]


def test_editing_a_cell_writes_back_through_the_text(editor: CurveEditor) -> None:
    editor.set_text("0,0,100,100")
    seen: list[str] = []
    editor.curveChanged.connect(seen.append)
    editor.table.item(0, 1).setText("20")
    assert editor.nodes[0] == (0.0, 20.0)
    assert editor.text() == "0,20,100,100"
    assert seen == ["0,20,100,100"]


def test_a_cell_value_above_100_is_clamped_not_stored(editor: CurveEditor) -> None:
    """Both axes are percentages; the editor bounds them rather than writing 140 to the file."""
    editor.set_text("0,0,100,100")
    editor.table.item(0, 1).setText("140")
    assert editor.nodes[0] == (0.0, AXIS_MAX)


def test_a_cell_that_is_not_a_number_is_rejected_and_the_old_text_comes_back(
    editor: CurveEditor,
) -> None:
    editor.set_text("0,0,100,100")
    editor.table.item(0, 1).setText("nonsense")
    assert editor.nodes[0] == (0.0, 0.0)
    assert editor.table.item(0, 1).text() == "0"


def test_add_node_inserts_the_midpoint_of_the_selected_segment(editor: CurveEditor) -> None:
    editor.set_text("0,0,100,100")
    editor.table.setCurrentCell(0, 0)
    editor.add_node()
    assert editor.nodes == [(0.0, 0.0), (50.0, 50.0), (100.0, 100.0)]


def test_add_node_on_an_empty_curve_creates_the_two_end_nodes(editor: CurveEditor) -> None:
    editor.set_text("")
    editor.add_node()
    assert editor.text() == "0,0,100,100"


def test_remove_node_deletes_the_selected_row(editor: CurveEditor) -> None:
    editor.set_text("0,0,50,50,100,100")
    editor.table.setCurrentCell(1, 0)
    editor.remove_selected()
    assert editor.text() == "0,0,100,100"


def test_sort_nodes_orders_by_speed_only_when_asked(editor: CurveEditor) -> None:
    editor.set_text("40,88,0,21")
    assert editor.text() == "40,88,0,21"  # untouched on load
    editor.sort_nodes()
    assert editor.text() == "0,21,40,88"


def test_unparsable_text_is_kept_verbatim_and_the_editor_goes_read_only(
    editor: CurveEditor,
) -> None:
    """A curve nobody can read must never be silently rewritten (module docstring)."""
    editor.set_text("0,0,100")
    assert editor.read_only
    assert editor.text() == "0,0,100"
    assert editor.problems and "odd number" in editor.problems[0]
    editor.add_node()
    editor.remove_selected()
    assert editor.text() == "0,0,100"


def test_the_preview_paints_without_a_window(editor: CurveEditor) -> None:
    """The preview is painted offscreen in the smoke tests; it must not need a screen."""
    from PySide6.QtGui import QImage

    editor.set_text(VENDOR_CURVES[0])
    editor.preview.resize(200, 120)
    image = QImage(200, 120, QImage.Format.Format_ARGB32)
    image.fill(0)
    editor.preview.render(image)
    assert image.constBits() is not None


# ==================================================================== vendor file


def test_every_curve_in_the_vendor_layer_file_round_trips(src_dir: Path) -> None:
    """All 22 curve strings of ``BkLayerPara.xml`` survive the codec unchanged."""
    document = read_params(src_dir / "File" / "BkLayerPara.xml", "layer")
    seen = 0
    for slot in range(1, 12):
        for attribute in ("PWMCurveNodes", "FreqCurveNodes"):
            text = str(document.get(f"PLayerParam{slot}", "GP", attribute))
            assert format_curve_text(parse_curve_text(text)) == text
            seen += 1
    assert seen == 22
