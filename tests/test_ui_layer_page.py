"""ui/pages/layer_co2.py: the CO2 layer page and its technology-library exchange (02, A6 §3).

Covers the schema-built page, the read-only process/lead-line box, the ``lp19``
cross-check (02 §3.2), a byte-identical round trip of a vendor technology file
through the editor, and the main-window dock smoke test.  Offscreen Qt; nothing
here touches a machine (PORT-PLAN §8).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QSettings, Qt  # noqa: E402

from nexcut.io import chf  # noqa: E402
from nexcut.io.params import (  # noqa: E402
    ParamFileError,
    default_document,
    parse_technology,
    read_params,
    serialize_technology,
)
from nexcut.model.glyph import SegmentGlyph, Vec2  # noqa: E402
from nexcut.model.graph import ChfDocument, Contour, ContourElement, Group  # noqa: E402
from nexcut.ui.i18n import LangCatalog, Translator  # noqa: E402
from nexcut.ui.pages.layer_co2 import (  # noqa: E402
    CO2_GROUP_ORDER,
    FILM_SLOT,
    MANU_TYPE_STAGES,
    Co2LayerPage,
    crafts_summary,
    lp19_problems,
    manu_type_text,
)
from nexcut.ui.property_grid import UnitPolicy  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qapp() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _translator() -> Translator:
    """A small stand-in catalog: the vendor package is not in CI (tests/conftest.py)."""
    return Translator(
        LangCatalog(
            entries={
                k: (v, v)
                for k, v in {
                    "pd125": "Cut Basic.Cut Speed",
                    "pd127": "Cut Laser.Cut Power",
                    "pd128": "Cut Laser.Cut Freq",
                    "pd130": "Cut Gas.Gas Type",
                    "pd131": "Cut Gas.Gas Pressure",
                    "pd153": "Misc.Notes",
                    "pd51": "Others",
                    "lp19": "Layer %d 【Cut Height】 is larger than 【With Film Cut Height】",
                    "newLang50": "Standard cutting",
                    "newLang51": "First level perforation",
                    "newLang53": "Three-level perforation",
                    "pd811": "Fix Height Cut",
                    "pd811-1": "Adv Fix Height Cut",
                    "pd351": "None",
                    "gp83": "Layer",
                    "lp17": "Layer %d",
                }.items()
            }
        )
    )


@pytest.fixture()
def page() -> Iterator[Co2LayerPage]:
    p = Co2LayerPage(translator=_translator(), document=default_document("layer"))
    yield p
    p.deleteLater()


# ============================================================================ build


def test_page_edits_the_co2_slot_of_a_layer_document(page: Co2LayerPage) -> None:
    """The grid holds the 21 ``CO2LayerParam`` attributes of the selected slot (02 §2.3)."""
    assert page.slot == 1 and page.group_name == "PCO2LayerParam1"
    assert len(page.grid.rows) == 21
    assert "CutSpeed" in page.grid.rows and "ManuType" not in page.grid.rows
    page.grid.set_value("CutSpeed", 11.0)
    assert page.document.get("PCO2LayerParam1", "GP", "CutSpeed") == 11.0
    # the fibre slot of the same document is untouched
    assert page.document.get("PLayerParam1", "GP", "CutSpeed") != 11.0


def test_slot_selector_switches_groups(page: Co2LayerPage) -> None:
    page.grid.set_value("CutDuty", 42)
    changed: list[int] = []
    page.slotChanged.connect(changed.append)
    page.set_slot(3)
    assert page.group_name == "PCO2LayerParam3" and changed == [3]
    assert page.grid.value("CutDuty") != 42
    page.set_slot(1)
    assert page.grid.value("CutDuty") == 42
    page.set_slot(99)  # clamped to the 11 slots of 02 §4
    assert page.slot == 11
    page.set_slot(0)
    assert page.slot == 1


def test_section_order_follows_the_page_list(page: Co2LayerPage) -> None:
    names = page.grid.group_names()
    ordered = [n for n in CO2_GROUP_ORDER if n in names]
    assert names[: len(ordered)] == ordered


def test_units_come_from_the_manu_document() -> None:
    """01 §938: ``UN.SpeedUnit`` picks the display unit; the stored value stays mm/s."""
    manu = default_document("manu")
    manu.set("PManuParam", "UN", "SpeedUnit", 1)
    layer = default_document("layer")
    layer.set("PCO2LayerParam1", "GP", "CutSpeed", 11.0)
    p = Co2LayerPage(translator=_translator(), document=layer, manu=manu)
    assert p.units == UnitPolicy(speed=1)
    assert p.grid.item_for("CutSpeed").text(1) == "0.66 m/min"
    assert layer.get("PCO2LayerParam1", "GP", "CutSpeed") == 11.0
    p.deleteLater()


def test_a_wrong_document_kind_is_refused(page: Co2LayerPage) -> None:
    """Only a ``layer`` document has the ``PCO2LayerParam<n>`` groups this page edits."""
    with pytest.raises(ValueError, match="layer"):
        page.set_document(default_document("manu"))
    with pytest.raises(ValueError, match="layer"):
        Co2LayerPage(translator=_translator(), document=default_document("hard"))
    with pytest.raises(ValueError, match="layer"):
        Co2LayerPage(translator=_translator(), document=object())
    fresh = Co2LayerPage(translator=_translator())
    assert fresh.document.kind == "layer" and fresh.document.is_default()
    fresh.deleteLater()


# ============================================================================ validation


def test_lp19_is_the_only_cross_check(page: Co2LayerPage) -> None:
    """02 §3.2: only ``lp19`` is enforced by this build (``lp16``-``lp18`` are dead ids)."""
    doc = page.document
    doc.set(f"PLayerParam{FILM_SLOT}", "GP", "CutHeight", 2.0)
    for slot in range(1, FILM_SLOT):
        doc.set(f"PLayerParam{slot}", "GP", "CutHeight", 1.0)
    assert lp19_problems(doc, page.t) == []
    doc.set("PLayerParam4", "GP", "CutHeight", 3.0)
    problems = lp19_problems(doc, page.t)
    assert len(problems) == 1 and "Layer 4" in problems[0]
    page.grid.refresh()
    assert page.grid.problems == problems
    assert "Layer 4" in page.problem_label.text()


def test_lp19_ignores_a_document_without_the_fibre_table(page: Co2LayerPage) -> None:
    assert lp19_problems(default_document("manu"), page.t) == []


def test_out_of_range_edit_is_reported(page: Co2LayerPage) -> None:
    problems = page.grid.set_value("CutFreq", 999_999)
    assert any("CutFreq" in p for p in problems)
    assert "CutFreq" in page.problem_label.text()


# ============================================================================ read-only box


def test_process_type_box_is_read_only_and_uses_the_fibre_slot(page: Co2LayerPage) -> None:
    """A6 §3: the vendor dialog writes ``ManuType`` into the fibre table even in CO2 mode."""
    page.document.set("PLayerParam1", "GP", "ManuType", 4)
    page._refresh_info()
    assert "Three-level perforation" in page.info["manu_type"].text()
    assert "3 pierce stages" in page.info["manu_type"].text()
    assert "ManuType" not in page.grid.rows  # not editable on this page


def test_manu_type_mapping_matches_a6() -> None:
    """A6 §3 consolidated table, including the two codes with no UI item in this build."""
    assert MANU_TYPE_STAGES == {0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 0, 6: 4, 7: 5}
    t = _translator()
    assert manu_type_text(0, t).startswith("Standard cutting (0, 0 pierce stages)")
    assert manu_type_text(2, t).startswith("First level perforation (2, 1 pierce stage)")
    assert manu_type_text(5, t).startswith("Adv Fix Height Cut (5, 0 pierce stages)")
    assert "no UI item" in manu_type_text(7, t)
    assert "unknown code 9" in manu_type_text(9, t)


def _job() -> ChfDocument:
    def contour(x: float) -> Contour:
        c = Contour(elements=[ContourElement(SegmentGlyph(Vec2(x, 0), Vec2(x + 1, 1)), 1)])
        c.crafts.lead_line.type = 1
        c.crafts.lead_line.angle_deg = 30.0
        c.crafts.lead_line.length = 2.0
        c.crafts.cool_pos = [0.25, 0.75]
        return c

    return ChfDocument(graphs=[contour(0.0), Group(children=[contour(10.0)])])


def test_lead_and_cool_summary_comes_from_the_job(page: Co2LayerPage) -> None:
    """03 §6.1.1: lead line and cool points are per-contour crafts, shown read-only."""
    t = page.t
    assert crafts_summary(None, t) == {"lead": "None", "cool": "None", "contours": "0"}
    page.set_job(_job())
    assert page.info["contours"].text() == "2"  # the group's child counts too
    assert "type 1, 30°, 2 mm" in page.info["lead"].text()
    assert page.info["cool"].text() == "2 points"
    mixed = _job()
    mixed.graphs[0].crafts.lead_line.length = 5.0  # type: ignore[union-attr]
    mixed.graphs[0].crafts.cool_pos = []  # type: ignore[union-attr]
    page.set_job(mixed)
    assert "2 different settings" in page.info["lead"].text()
    assert page.info["cool"].text() == "0..2 points"
    page.set_job(None)
    assert page.info["contours"].text() == "0"


def test_lead_summary_shows_the_fibre_enable_flag(page: Co2LayerPage) -> None:
    page.document.set("PLayerParam1", "GP", "LeadLineParam_Enable", 1)
    page.set_job(_job())
    assert page.info["lead"].text().startswith("on - ")
    page.document.set("PLayerParam1", "GP", "LeadLineParam_Enable", 0)
    page.set_job(_job())
    assert page.info["lead"].text().startswith("off - ")


# ============================================================================ technology library


def test_preset_round_trip_through_the_editor(tmp_path: Path) -> None:
    """A preset written from an untouched slot re-reads identically (02 §6.1/§6.2)."""
    layer = default_document("layer")
    layer.set("PCO2LayerParam2", "GP", "LayerFileName", "acrylic 24 mm")
    layer.set("PCO2LayerParam2", "GP", "CutSpeed", 11.000000000000002)
    p = Co2LayerPage(translator=_translator(), document=layer, slot=2)
    out = tmp_path / "acrylic.xml"
    p.save_preset_file(out)
    preset = parse_technology(out.read_bytes())
    assert preset.laser == "co2" and preset.source_group == "PCO2LayerParam11"
    assert preset.values["CutSpeed"] == 11.000000000000002
    assert preset.layer_file_name == "acrylic 24 mm"
    # apply it to another slot and export again: identical bytes
    p.set_slot(5)
    p.apply_preset_file(out)
    again = tmp_path / "again.xml"
    p.save_preset_file(again)
    assert again.read_bytes() == out.read_bytes()
    assert p.grid.item_for("CutSpeed").text(1) == "11 mm/s"
    assert p.preset_label.text() == "acrylic 24 mm"
    p.deleteLater()


def _vendor_co2_presets(src_dir: Path) -> list[Path]:
    """CO2 technology files shipped with the machine.

    The runtime library lives in ``%LOCALAPPDATA%\\NexCut\\Technology\\CO2``
    (:func:`nexcut.io.params.list_technology`, 02 §6.1); the package instead ships
    them under ``Cutting parameters/CO2``, so they are found by content.
    """
    out = []
    for f in sorted(src_dir.parent.rglob("*.xml")):
        if "Pc_Software" in f.parts or f.name.startswith(("Bk", "Second")) or f.name.endswith(
            "backup.xml"
        ):
            continue
        try:
            if parse_technology(f.read_bytes()).laser == "co2":
                out.append(f)
        except ParamFileError:
            continue
    return out


def test_vendor_technology_file_round_trips_through_the_editor(src_dir: Path) -> None:
    """Every vendor CO2 preset survives load -> edit-nothing -> save byte for byte.

    The presets that lack ``CutFreq`` gain it with the descriptor default, which is
    the documented difference of ``test_io_fidelity_review`` (02 §6.2).
    """
    files = _vendor_co2_presets(src_dir)
    if not files:
        pytest.skip("vendor technology library not present")
    p = Co2LayerPage(translator=_translator(), document=default_document("layer"), slot=4)
    same = added = 0
    for f in files:
        raw = f.read_bytes()
        p.apply_preset_file(f)
        out = serialize_technology(p.preset())
        if out == raw:
            same += 1
        else:
            assert out == raw.replace(b'"/>\r\n</PCO2', b'" CutFreq="5000"/>\r\n</PCO2'), f
            added += 1
        assert p.preset_label.text() == parse_technology(raw).layer_file_name
    # 02 §6.2: all 13 shipped CO2 presets predate CutFreq, so every one of them gains
    # the descriptor default; nothing else changes.
    assert (same, added) == (0, 13)
    p.deleteLater()


def test_a_fibre_preset_is_refused(tmp_path: Path, page: Co2LayerPage) -> None:
    from nexcut.io.params import preset_from_layer, write_technology

    fibre = tmp_path / "fibre.xml"
    write_technology(fibre, preset_from_layer(default_document("layer"), "fiber", 1))
    with pytest.raises(ParamFileError, match="fiber"):
        page.apply_preset_file(fibre)


def test_buttons_use_the_installed_path_chooser(tmp_path: Path, page: Co2LayerPage) -> None:
    """The Load/Save buttons never open a modal dialog in tests."""
    target = tmp_path / "from-button.xml"
    page.set_path_chooser(lambda save: str(target))
    page.grid.set_value("CutDuty", 77)
    page.save_button.click()
    assert target.is_file()
    page.grid.set_value("CutDuty", 10)
    loaded: list[str] = []
    page.presetLoaded.connect(loaded.append)
    page.load_button.click()
    assert page.grid.value("CutDuty") == 77 and loaded == [str(target)]
    page.set_path_chooser(lambda save: None)  # cancel
    page.load_button.click()
    assert page.grid.value("CutDuty") == 77


def test_a_broken_preset_file_is_reported_not_raised(tmp_path: Path, page: Co2LayerPage) -> None:
    bad = tmp_path / "bad.xml"
    bad.write_bytes(b"<NotParameterRoot/>")
    page.set_path_chooser(lambda save: str(bad))
    page.load_button.click()
    assert page.problem_label.text()


# ============================================================================ main window


def test_main_window_hosts_the_layer_dock(tmp_path: Path, qapp: QtWidgets.QApplication) -> None:
    """The dock is present, edits the layer document and still has no machine control."""
    from nexcut.ui.main_window import MainWindow

    layer = default_document("layer")
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    win = MainWindow(
        settings=settings, interactive=False, translator=_translator(), layer=layer
    )
    win.resize(1100, 760)
    win.show()
    qapp.processEvents()
    try:
        assert win.layer_param_dock.widget() is win.layer_page
        assert win.layer_page.document is layer
        win.layer_page.grid.set_value("CutDuty", 55)
        assert layer.get("PCO2LayerParam1", "GP", "CutDuty") == 55
        # the layer list drives the page's slot (list index 0 = slot 1)
        win.layer_tree.setCurrentItem(win.layer_items[2])
        qapp.processEvents()
        assert win.layer_page.slot == 3
        # opening a job fills the read-only crafts box
        job = tmp_path / "job.chf"
        chf.save_chf(_job(), job)
        assert win.open_path(job)
        assert win.layer_page.info["contours"].text() == "2"
        assert not win.machine_dock.widget().isEnabled()
        assert win.layer_param_dock.toggleViewAction() is not None
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_main_window_without_a_layer_document_uses_defaults(
    tmp_path: Path, qapp: QtWidgets.QApplication
) -> None:
    from nexcut.ui.main_window import MainWindow

    settings = QSettings(str(tmp_path / "s2.ini"), QSettings.Format.IniFormat)
    win = MainWindow(settings=settings, interactive=False, translator=_translator())
    try:
        assert win.layer_page.document.kind == "layer"
        assert win.layer_page.document.is_default()
        assert win.layer_page.grid.item_for("CutFreq").text(1) == "5000 Hz"
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_vendor_layer_file_loads_into_the_page(src_dir: Path) -> None:
    """The real ``BkLayerPara.xml`` drives the page, units included (02 §4)."""
    layer = read_params(src_dir / "File/BkLayerPara.xml", "layer")
    manu = read_params(src_dir / "File/BkManuPara.xml", "manu")
    p = Co2LayerPage(translator=_translator(), document=layer, manu=manu)
    assert p.units.speed == 1  # BkManuPara.xml has UN.SpeedUnit="1" (02 §2.4)
    assert p.grid.item_for("CutSpeed").text(1).endswith("m/min")
    assert p.info["manu_type"].text() != "-"
    checkbox = p.grid.item_for("SlowStart")
    assert checkbox.checkState(1) in (Qt.CheckState.Checked, Qt.CheckState.Unchecked)
    p.deleteLater()
