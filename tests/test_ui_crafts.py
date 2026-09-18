"""ui/pages/crafts.py: the per-contour lead-in/out, cool-point and micro-joint editor.

The point of the editor is that the ``<Crafts>`` block is *opaque* (03 §6.1.1, §14):
several of its scalars are still INFERENCE, so the editor writes only what the
operator changed and every other field - including the ones nobody has explained -
round-trips.  The tests therefore check the byte identity of real ``.chf`` files as
much as the widget behaviour.

Offscreen Qt; nothing here touches a machine (PORT-PLAN §8).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from nexcut.io import chf  # noqa: E402
from nexcut.model.glyph import SegmentGlyph, Vec2  # noqa: E402
from nexcut.model.graph import (  # noqa: E402
    ChfDocument,
    Contour,
    ContourElement,
    Crafts,
    Group,
    LeadLine,
    Scan,
)
from nexcut.ui.pages.crafts import CraftsEditor, close_ratios, iter_contours  # noqa: E402

VENDOR_CHF = ("File/autosave.chf", "Graph/Work1/1.chf", "Graph/Work2/2.chf")


@pytest.fixture(scope="module", autouse=True)
def qapp() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _contour(length: float = 100.0, layer: int = 0, crafts: Crafts | None = None) -> Contour:
    glyph = SegmentGlyph(Vec2(0.0, 0.0), Vec2(length, 0.0))
    return Contour(
        length=length,
        start=Vec2(0.0, 0.0),
        end=Vec2(length, 0.0),
        bbox_max=Vec2(length, 0.0),
        elements=[ContourElement(glyph)],
        layer=layer,
        crafts=crafts if crafts is not None else Crafts(),
    )


def _document(*contours: Contour) -> ChfDocument:
    return ChfDocument(graphs=list(contours))


@pytest.fixture()
def editor() -> Iterator[CraftsEditor]:
    widget = CraftsEditor()
    yield widget
    widget.deleteLater()


# ============================================================================ walking


def test_iter_contours_walks_groups_scan_paths_and_text() -> None:
    """``ContourEx`` and ``Scan`` are ``Group`` subclasses, and a ``Scan`` also owns
    generated fly-cut paths that carry crafts of their own (03 §6.4/§6.5)."""
    a, b, c, d = (_contour(layer=i) for i in range(4))
    group = Group(children=[b, c])
    scan = Scan(children=[d], paths=[_contour(layer=9)])
    document = _document(a, group, scan)
    layers = [x.layer for x in iter_contours(document)]
    assert layers == [0, 1, 2, 3, 9]
    assert list(iter_contours(None)) == []


def test_the_editor_lists_every_contour(editor: CraftsEditor) -> None:
    editor.set_document(_document(_contour(layer=1), _contour(layer=2)))
    assert len(editor.entries) == 2
    assert editor.contour_box.count() == 2
    assert editor.entries[0].label.startswith("#0  layer 1")
    assert editor.index == 0


def test_an_empty_document_leaves_the_editor_blank(editor: CraftsEditor) -> None:
    editor.set_document(None)
    assert editor.entries == []
    assert editor.contour is None and editor.crafts is None
    assert editor.cool_table.rowCount() == 0
    assert not editor.lead_type.isEnabled()


def test_selecting_a_contour_emits_and_shows_it(editor: CraftsEditor) -> None:
    editor.set_document(_document(_contour(layer=1), _contour(layer=2)))
    seen: list[int] = []
    editor.contourChanged.connect(seen.append)
    editor.set_contour(1)
    assert seen == [1]
    assert editor.contour is editor.entries[1].contour
    editor.set_contour(99)  # clamped
    assert editor.index == 1


# ========================================================================== lead line


def test_the_lead_line_fields_show_and_write_the_guide_curve() -> None:
    editor = CraftsEditor()
    try:
        crafts = Crafts(lead_line=LeadLine(type=2, angle_deg=45.0, length=8.0, arc_radius=2.0))
        editor.set_document(_document(_contour(crafts=crafts)))
        assert editor.lead_type.currentIndex() == 2
        assert editor.lead_angle.value() == pytest.approx(45.0)
        assert editor.lead_length.value() == pytest.approx(8.0)
        assert editor.lead_radius.value() == pytest.approx(2.0)
        seen: list[int] = []
        editor.craftsChanged.connect(seen.append)
        editor.lead_type.setCurrentIndex(3)
        editor.lead_length.setValue(12.5)
        editor.lead_flag.setChecked(True)
        assert crafts.lead_line.type == 3
        assert crafts.lead_line.length == pytest.approx(12.5)
        assert crafts.lead_line.flag is True
        assert seen == [0, 0, 0]
    finally:
        editor.deleteLater()


def test_a_version_1_contour_has_no_arc_radius_field(editor: CraftsEditor) -> None:
    """``arc_radius`` is ``None`` only for version-1 files, where the line is absent (03 §9)."""
    crafts = Crafts(lead_line=LeadLine(arc_radius=None))
    editor.set_document(_document(_contour(crafts=crafts)))
    assert not editor.lead_radius.isEnabled()
    editor.lead_radius.setValue(5.0)
    assert crafts.lead_line.arc_radius is None


# ======================================================================== cool points


def test_cool_points_are_listed_and_edited_as_ratios(editor: CraftsEditor) -> None:
    """A6 §2.4 (EVIDENCE): the entries are ratios 0..1 of the contour length."""
    crafts = Crafts(cool_pos=[0.25, 0.75])
    editor.set_document(_document(_contour(crafts=crafts)))
    assert editor.cool_table.rowCount() == 2
    assert editor.cool_table.item(0, 0).text() == "0.25"
    editor.cool_table.item(1, 0).setText("0.5")
    assert crafts.cool_pos == [0.25, 0.5]


def test_a_cool_point_outside_zero_to_one_is_clamped(editor: CraftsEditor) -> None:
    crafts = Crafts(cool_pos=[0.25])
    editor.set_document(_document(_contour(crafts=crafts)))
    editor.cool_table.item(0, 0).setText("4")
    assert crafts.cool_pos == [1.0]


def test_add_and_remove_cool_point(editor: CraftsEditor) -> None:
    crafts = Crafts(cool_pos=[])
    editor.set_document(_document(_contour(crafts=crafts)))
    editor.add_cool_point()
    assert crafts.cool_pos == [0.5]
    editor.add_cool_point()
    assert crafts.cool_pos == [0.5, 0.75]
    editor.cool_table.setCurrentCell(0, 0)
    editor.remove_cool_point()
    assert crafts.cool_pos == [0.75]


def test_a_file_without_a_cool_block_is_left_alone(editor: CraftsEditor) -> None:
    """``cool_pos`` is ``None`` for version <= 2 (03 §9): the editor must not invent a list."""
    crafts = Crafts(cool_pos=None)
    editor.set_document(_document(_contour(crafts=crafts)))
    editor.add_cool_point()
    assert crafts.cool_pos is None
    assert "version <= 2" in editor.cool_note.text()


# ======================================================================= micro joints


def test_close_ratios_follow_the_derivation_of_a6() -> None:
    """``close[2i] = a - 0.5*b/L``, ``close[2i+1] = a + 0.5*b/L`` (A6 §4.2)."""
    assert close_ratios([(0.5, 2.0)], 100.0) == [0.49, 0.51]
    assert close_ratios([(0.25, 10.0), (0.75, 5.0)], 100.0) == [0.2, 0.3, 0.725, 0.775]
    assert close_ratios([], 10.0) == []
    with pytest.raises(ValueError, match="zero length"):
        close_ratios([(0.5, 1.0)], 0.0)


def test_editing_a_micro_joint_recomputes_the_close_ratios(editor: CraftsEditor) -> None:
    crafts = Crafts(pwm_nodes=[(0.5, 2.0)], pwm_close_pos_ratios=[0.0, 0.0])
    editor.set_document(_document(_contour(length=100.0, crafts=crafts)))
    editor.joint_table.item(0, 1).setText("4")
    assert crafts.pwm_nodes == [(0.5, 4.0)]
    assert crafts.pwm_close_pos_ratios == [0.48, 0.52]


def test_add_and_remove_micro_joint(editor: CraftsEditor) -> None:
    crafts = Crafts()
    editor.set_document(_document(_contour(length=200.0, crafts=crafts)))
    editor.add_micro_joint()
    assert crafts.pwm_nodes == [(0.5, 1.0)]
    assert crafts.pwm_close_pos_ratios == [0.4975, 0.5025]
    editor.joint_table.setCurrentCell(0, 0)
    editor.remove_micro_joint()
    assert crafts.pwm_nodes == []
    assert crafts.pwm_close_pos_ratios == []


def test_a_zero_length_contour_keeps_the_close_ratios_it_had(editor: CraftsEditor) -> None:
    """Nothing can be derived without a length, so the file's own list survives (A6 §4.2)."""
    crafts = Crafts(pwm_nodes=[(0.5, 1.0)], pwm_close_pos_ratios=[0.1, 0.9])
    editor.set_document(_document(_contour(length=0.0, crafts=crafts)))
    assert editor.recompute_close_ratios() is False
    editor.add_micro_joint()
    assert crafts.pwm_close_pos_ratios == [0.1, 0.9]


# ====================================================== the opaque fields round-trip


def test_the_unknown_scalars_are_shown_read_only_and_never_written(
    editor: CraftsEditor,
) -> None:
    """``compensate_*``, ``pwm_enable``, ``double170`` and ``double188`` are INFERENCE
    (03 §6.1.1, A6 §2.2/§2.5): the editor displays them and keeps them."""
    crafts = Crafts(
        compensate_type=3,
        compensate_width=0.15,
        pwm_enable=2,  # a mode int, not a boolean (verifier V10)
        double170=0.42,
        double188=1.5,
        cool_pos=[0.1],
    )
    kept = replace(crafts)
    editor.set_document(_document(_contour(crafts=crafts)))
    assert editor.opaque["compensate_type"].text() == "3"
    assert editor.opaque["pwm_enable"].text() == "2"
    assert editor.opaque["double170"].text() == "0.42"
    assert editor.opaque["double188"].text() == "1.5"
    editor.lead_length.setValue(3.0)
    editor.add_cool_point()
    editor.add_micro_joint()
    for field in ("compensate_type", "compensate_width", "pwm_enable", "double170", "double188"):
        assert getattr(crafts, field) == getattr(kept, field)


def test_legacy_reserved_lines_are_counted_not_touched(editor: CraftsEditor) -> None:
    """v2-v4 files carry reserved lines the port keeps verbatim (03 §9)."""
    crafts = Crafts()
    if not hasattr(crafts, "legacy_reserved"):  # pragma: no cover - model without the slot
        pytest.skip("this model build has no legacy_reserved slot")
    crafts.legacy_reserved = ["", "", ""]
    editor.set_document(_document(_contour(crafts=crafts)))
    assert editor.opaque["reserved"].text() == "3 line(s) kept verbatim"
    editor.add_micro_joint()
    assert crafts.legacy_reserved == ["", "", ""]


# ========================================================== real .chf files, end to end


@pytest.mark.parametrize("rel", VENDOR_CHF)
def test_a_vendor_chf_survives_a_tour_of_the_editor(src_dir: Path, rel: str) -> None:
    """Opening every contour in the editor must not change one byte of the file."""
    path = src_dir / rel
    if not path.is_file():
        pytest.skip(f"{rel} not in the package")
    original = path.read_bytes()
    document = chf.read_chf(original)
    editor = CraftsEditor()
    try:
        editor.set_document(document)
        assert editor.entries, f"{rel} holds no contour"
        for i in range(len(editor.entries)):
            editor.set_contour(i)
        assert chf.write_chf(document) == original
    finally:
        editor.deleteLater()


def test_an_edited_vendor_chf_changes_only_what_was_edited(src_dir: Path) -> None:
    """One lead-line length changes; everything else - including the unexplained scalars -
    comes back identical through the writer."""
    path = src_dir / "File" / "autosave.chf"
    if not path.is_file():
        pytest.skip("autosave.chf not in the package")
    original = path.read_bytes()
    document = chf.read_chf(original)
    reference = chf.read_chf(original)
    editor = CraftsEditor()
    try:
        editor.set_document(document)
        editor.set_contour(3)
        crafts = editor.crafts
        assert crafts is not None
        editor.lead_length.setValue(crafts.lead_line.length + 1.0)
        edited = chf.write_chf(document)
        assert edited != original
        # put it back and the bytes return
        editor.lead_length.setValue(reference.graphs[3].crafts.lead_line.length)  # type: ignore[union-attr]
        assert chf.write_chf(document) == original
    finally:
        editor.deleteLater()
