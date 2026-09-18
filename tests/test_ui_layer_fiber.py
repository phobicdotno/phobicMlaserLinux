"""ui/pages/layer_fiber.py + ui/pages/layer_file.py: the 164-attribute fibre layer page.

Covers the full attribute set, the ``ManuType`` correction and the pierce stages of
A6 §3, the power/frequency curve tab, the ``A250607_*`` fibre/CO2 selector warning,
the technology-preset exchange, and writing ``BkLayerPara.xml`` back through
:class:`~nexcut.ui.pages.layer_file.LayerFileBar` with the backup-then-rename rule.

Offscreen Qt; nothing here touches a machine (PORT-PLAN §8).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from nexcut.io.params import (  # noqa: E402
    BACKUP_SUFFIX,
    ParamFileError,
    default_document,
    parse_technology,
    read_params,
    serialize_params,
    serialize_technology,
    write_params,
)
from nexcut.ui.curve_editor import parse_curve_text  # noqa: E402
from nexcut.ui.i18n import LangCatalog, Translator  # noqa: E402
from nexcut.ui.pages.layer_co2 import MANU_TYPE_STAGES  # noqa: E402
from nexcut.ui.pages.layer_fiber import (  # noqa: E402
    MAX_STAGES,
    STAGE_ATTRIBUTES,
    FiberLayerPage,
    family_warning,
    manu_type_descriptor,
    manu_type_options,
    stage_attributes,
)
from nexcut.ui.pages.layer_file import LayerFileBar  # noqa: E402

FIBER_ATTRIBUTE_COUNT = 164
"""``PLayerParam<n>/GP`` of ``BkLayerPara.xml`` (02 §2.3, STATUS §1.4)."""


@pytest.fixture(scope="module", autouse=True)
def qapp() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _translator() -> Translator:
    """A stand-in catalog: the vendor package is not in CI (tests/conftest.py)."""
    return Translator(
        LangCatalog(
            entries={
                k: (v, v)
                for k, v in {
                    "pd51": "Others",
                    "pd125": "Cut Basic.Cut Speed",
                    "pd129": "Cut Basic.Cut Height",
                    "pd816": "First Drill Basic.Drill Height",
                    "newLang50": "Standard cutting",
                    "newLang51": "First level perforation",
                    "newLang52": "Secondary perforation",
                    "newLang53": "Three-level perforation",
                    "pd811": "Fix Height Cut",
                    "pd811-1": "Adv Fix Height Cut",
                    "A241024_0": "Fiber laser",
                    "A241024_1": "CO2 laser",
                    "A250607_3": "Currently <%s>, please confirm the settings",
                    "lp19": "Layer %d cut height is larger than the with-film cut height",
                }.items()
            }
        )
    )


@pytest.fixture(scope="module")
def pkg_dir(src_dir: Path) -> Path:
    """The package's parent, which is where ``Cutting parameters`` sits (tests/test_io_params)."""
    return src_dir.parent


def _presets(pkg_dir: Path, laser: str) -> list[Path]:
    """Vendor technology presets of one family (02 §6.1: fibre under the wattage folders)."""
    root = pkg_dir / "Cutting parameters"
    if not root.is_dir():
        return []
    files = sorted(root.rglob("*.xml"))
    wanted = "PCO2LayerParam" if laser == "co2" else "PLayerParam"
    out: list[Path] = []
    for f in files:
        head = f.read_bytes()[:400]
        if b"<PCO2LayerParam" in head:
            if wanted == "PCO2LayerParam":
                out.append(f)
        elif b"<PLayerParam" in head and wanted == "PLayerParam":
            out.append(f)
    return out


@pytest.fixture()
def page() -> Iterator[FiberLayerPage]:
    widget = FiberLayerPage(translator=_translator(), document=default_document("layer"))
    yield widget
    widget.deleteLater()


# ============================================================================== build


def test_the_page_holds_all_164_fibre_attributes(page: FiberLayerPage) -> None:
    assert page.slot == 1
    assert page.group_name == "PLayerParam1"
    assert len(page.grid.rows) == FIBER_ATTRIBUTE_COUNT
    assert len(page.descriptors()) == FIBER_ATTRIBUTE_COUNT


def test_the_slot_selector_moves_the_whole_page(page: FiberLayerPage) -> None:
    seen: list[int] = []
    page.slotChanged.connect(seen.append)
    page.set_slot(7)
    assert page.group_name == "PLayerParam7"
    assert seen == [7]
    page.set_slot(99)  # clamped to the 11 slots of 02 §4
    assert page.slot == 11


def test_a_document_of_the_wrong_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="'layer' ParamDocument"):
        FiberLayerPage(translator=_translator(), document=default_document("manu"))


# =========================================================== ManuType and the stages


def test_manu_type_is_edited_against_the_a6_table_not_the_legacy_descriptor(
    page: FiberLayerPage,
) -> None:
    """The descriptor still says max 3 with four ``pd810..pd813`` labels (A6 §3)."""
    raw = page.document.descriptor("PLayerParam1", "GP", "ManuType")
    assert raw.max == 3.0
    assert raw.enum_ids == ("pd810", "pd811", "pd812", "pd813")
    fixed = manu_type_descriptor(raw)
    assert (fixed.min, fixed.max, fixed.enum_ids) == (0.0, 7.0, None)
    assert fixed.validate(4) == []  # the vendor's own slot 1 value
    assert page.grid.rows["ManuType"].descriptor.max == 7.0
    assert page.grid.option_texts(page.grid.rows["ManuType"]) == manu_type_options(page.t)


def test_every_manu_type_code_selects_its_pierce_stage_count(page: FiberLayerPage) -> None:
    for code, stages in MANU_TYPE_STAGES.items():
        page.set_manu_type(code)
        assert page.manu_type == code
        assert page.active_stages == stages
        assert page.manu_type_box.currentIndex() == code


def test_the_stage_box_shows_the_15_stage_attributes_plus_the_bolt_trio(
    page: FiberLayerPage,
) -> None:
    for stage in range(MAX_STAGES):
        page.set_stage(stage)
        expected = stage_attributes(stage)
        assert len(expected) == len(STAGE_ATTRIBUTES) + 3
        assert list(page.stage_grid.rows) == list(expected)
        assert f"DrillHeight{stage}" in page.stage_grid.rows
        assert f"BoltDrill_Enable_{stage + 1}" in page.stage_grid.rows


def test_stage_attributes_refuses_a_stage_outside_zero_to_four() -> None:
    with pytest.raises(ValueError, match="stage must be"):
        stage_attributes(MAX_STAGES)


def test_the_stage_box_says_whether_the_stage_runs(page: FiberLayerPage) -> None:
    page.set_manu_type(3)  # two-stage pierce
    page.set_stage(0)
    assert "runs" in page.stage_state.text()
    page.set_stage(4)
    assert "not executed" in page.stage_state.text()


def test_an_inactive_stage_is_still_editable(page: FiberLayerPage) -> None:
    """All five blocks exist in the file and the operator sets a stage up before raising
    ManuType (module docstring)."""
    page.set_manu_type(0)  # no pierce at all
    page.set_stage(4)
    page.stage_grid.set_value("DrillPower4", 42)
    assert page.document.get("PLayerParam1", "GP", "DrillPower4") == 42


def test_editing_a_stage_value_reaches_the_main_grid(page: FiberLayerPage) -> None:
    page.set_stage(1)
    page.stage_grid.set_value("DrillHeight1", 7.5)
    assert page.grid.value("DrillHeight1") == 7.5
    assert page.grid.item_for("DrillHeight1").text(1).startswith("7.5")


# ================================================================ curves on the page


def test_the_curve_tab_shows_the_stored_curve_and_writes_it_back(page: FiberLayerPage) -> None:
    page.document.set("PLayerParam1", "GP", "PWMCurveNodes", "0,0,50,50,100,100", check=False)
    page.set_slot(2)
    page.set_slot(1)
    editor = page.curves["PWMCurveNodes"]
    assert parse_curve_text(editor.text()) == [(0.0, 0.0), (50.0, 50.0), (100.0, 100.0)]
    editor.table.setCurrentCell(0, 0)
    editor.remove_selected()
    assert page.document.get("PLayerParam1", "GP", "PWMCurveNodes") == "50,50,100,100"


def test_the_curve_switches_write_the_enable_and_smooth_attributes(page: FiberLayerPage) -> None:
    enable_box, smooth_box = page.curve_switches["FreqCurveNodes"]
    enable_box.setCurrentIndex(1)
    smooth_box.setCurrentIndex(2)
    assert page.document.get("PLayerParam1", "GP", "FreqAdjustWithSpeed") == 1
    assert page.document.get("PLayerParam1", "GP", "FreqCurveSmoothType") == 2


# ========================================================== the A250607 laser warning


def test_no_warning_without_a_manu_document(page: FiberLayerPage) -> None:
    assert family_warning(None, page.t) == ""
    assert page.warning_label.text() == ""


def test_no_warning_while_the_machine_is_in_fibre_mode() -> None:
    manu = default_document("manu")
    manu.set("PSoftParam", "SP", "m_iEnableLaserType", 0)
    assert family_warning(manu, _translator()) == ""


def test_the_a250607_warning_appears_when_the_machine_is_not_a_fibre_one() -> None:
    """``SP.m_iEnableLaserType`` 1 = CO2 (01 §1163); A250607_3 is the "currently CO2" text."""
    manu = default_document("manu")
    manu.set("PSoftParam", "SP", "m_iEnableLaserType", 1)
    text = family_warning(manu, _translator())
    assert "CO2 laser" in text
    assert "not in fibre mode" in text
    widget = FiberLayerPage(
        translator=_translator(), document=default_document("layer"), manu=manu
    )
    try:
        assert widget.warning_label.text() == text
        assert widget.warning_label.isVisibleTo(widget)
    finally:
        widget.deleteLater()


def test_a_foreign_object_in_place_of_the_manu_document_says_nothing() -> None:
    assert family_warning(object(), _translator()) == ""


# ================================================================== the lp19 rule


def test_lp19_is_installed_as_the_page_cross_check(page: FiberLayerPage) -> None:
    """``CutHeight`` lives only in this table, so the rule belongs on this page (02 §3.2)."""
    page.document.set("PLayerParam11", "GP", "GP.CutHeight".split(".")[1], 1.0, check=False)
    page.document.set("PLayerParam3", "GP", "CutHeight", 4.0, check=False)
    page.grid.refresh()
    assert any("Layer 3" in p for p in page.grid.problems)
    assert "Layer 3" in page.problem_label.text()


# ============================================================ technology presets


def test_a_fibre_preset_round_trips_through_the_page(pkg_dir: Path, tmp_path: Path) -> None:
    """A vendor fibre preset survives load -> page -> save byte for byte (02 §6.2)."""
    presets = _presets(pkg_dir, "fiber")
    if not presets:
        pytest.skip("vendor technology library not present")
    assert len(presets) >= 10
    page = FiberLayerPage(translator=_translator(), document=default_document("layer"))
    try:
        for source in presets:
            original = source.read_bytes()
            page.apply_preset_file(source)
            out = tmp_path / source.name
            page.save_preset_file(out)
            assert out.read_bytes() == serialize_technology(parse_technology(original))
    finally:
        page.deleteLater()


def test_a_co2_preset_is_refused_by_the_fibre_page(pkg_dir: Path) -> None:
    presets = _presets(pkg_dir, "co2")
    if not presets:
        pytest.skip("vendor technology library not present")
    page = FiberLayerPage(translator=_translator(), document=default_document("layer"))
    try:
        with pytest.raises(ParamFileError, match="co2 preset"):
            page.apply_preset_file(presets[0])
    finally:
        page.deleteLater()


def test_a_preset_failure_from_the_button_lands_in_the_problem_label(tmp_path: Path) -> None:
    page = FiberLayerPage(translator=_translator(), document=default_document("layer"))
    try:
        page.set_path_chooser(lambda save: str(tmp_path / "nothing-here.xml"))
        page.load_button.click()
        assert page.problem_label.text()
    finally:
        page.deleteLater()


# ============================================== the vendor layer file, end to end


def test_the_vendor_layer_file_round_trips_through_both_layer_editors(
    src_dir: Path, tmp_path: Path
) -> None:
    """Tour every slot and every pierce stage of ``BkLayerPara.xml``; the bytes must not move."""
    from nexcut.ui.pages.layer_co2 import Co2LayerPage

    source = src_dir / "File" / "BkLayerPara.xml"
    original = source.read_bytes()
    document = read_params(source, "layer")
    manu = read_params(src_dir / "File" / "BkManuPara.xml", "manu")
    fiber = FiberLayerPage(translator=_translator(), document=document, manu=manu)
    co2 = Co2LayerPage(translator=_translator(), document=document, manu=manu)
    bar = LayerFileBar(document, translator=_translator(), path=tmp_path / "BkLayerPara.xml")
    try:
        for slot in range(1, 12):
            fiber.set_slot(slot)
            co2.set_slot(slot)
            for stage in range(MAX_STAGES):
                fiber.set_stage(stage)
            fiber.grid.refresh()
            co2.grid.refresh()
        assert serialize_params(document) == original
        assert bar.save() == tmp_path / "BkLayerPara.xml"
        assert (tmp_path / "BkLayerPara.xml").read_bytes() == original
    finally:
        for widget in (fiber, co2, bar):
            widget.deleteLater()


def test_the_vendor_file_drives_the_page_with_its_own_values(src_dir: Path) -> None:
    """Slot 1 of this machine is a 3-stage pierce with a six-node power curve (02 §3.3/§3.4)."""
    document = read_params(src_dir / "File" / "BkLayerPara.xml", "layer")
    page = FiberLayerPage(translator=_translator(), document=document)
    try:
        assert page.manu_type == 4
        assert page.active_stages == 3
        assert len(parse_curve_text(page.curves["PWMCurveNodes"].text())) == 6
        assert page.preset_label.text() == "Carbon 10.0mm  4.0D F+12 O2"
    finally:
        page.deleteLater()


# ============================================================ the layer file bar


def test_the_bar_writes_the_layer_file_and_keeps_the_previous_generation(
    tmp_path: Path,
) -> None:
    target = tmp_path / "BkLayerPara.xml"
    write_params(target, default_document("layer"))
    original = target.read_bytes()
    document = read_params(target, "layer")
    bar = LayerFileBar(document, translator=_translator(), path=target)
    try:
        document.set("PLayerParam1", "GP", "CutSpeed", 12.5, check=False)
        saved: list[str] = []
        bar.documentSaved.connect(saved.append)
        assert bar.save() == target
        assert saved == [str(target)]
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        assert backup.read_bytes() == original
        assert target.read_bytes() == serialize_params(document)
        assert bar.last_backup == backup
        assert backup.name in bar.status_label.text()
    finally:
        bar.deleteLater()


def test_the_bar_reloads_and_emits_the_new_document(tmp_path: Path) -> None:
    target = tmp_path / "BkLayerPara.xml"
    write_params(target, default_document("layer"))
    document = read_params(target, "layer")
    bar = LayerFileBar(document, translator=_translator(), path=target)
    try:
        document.set("PLayerParam1", "GP", "CutSpeed", 99.0, check=False)
        seen: list[object] = []
        bar.documentReloaded.connect(seen.append)
        fresh = bar.reload()
        assert fresh is not None and fresh is not document
        assert fresh.get("PLayerParam1", "GP", "CutSpeed") != 99.0
        assert seen == [fresh]
    finally:
        bar.deleteLater()


def test_the_bar_reports_what_the_reader_had_to_do(tmp_path: Path) -> None:
    target = tmp_path / "partial.xml"
    target.write_bytes(
        b"<ParameterRoot>\r\n<PLayerParam1>\r\n<GP CutSpeed=\"5\"/>\r\n</PLayerParam1>\r\n"
        b"</ParameterRoot>\r\n"
    )
    document = read_params(target, "layer")
    bar = LayerFileBar(document, translator=_translator(), path=target)
    try:
        assert not document.report.clean
        assert "defaulted" in bar.report_text()
    finally:
        bar.deleteLater()


def test_the_bar_needs_a_layer_document() -> None:
    bar = LayerFileBar(default_document("layer"), translator=_translator())
    try:
        with pytest.raises(ValueError, match="'layer' document"):
            bar.set_document(default_document("manu"))
    finally:
        bar.deleteLater()


def test_save_as_asks_the_chooser_and_remembers_the_path(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.xml"
    bar = LayerFileBar(default_document("layer"), translator=_translator())
    try:
        bar.set_path_chooser(lambda save: str(target) if save else None)
        assert bar.save_as() == target
        assert bar.path == target
        assert target.is_file()
        assert bar.last_backup is None  # nothing was there before
    finally:
        bar.deleteLater()


def test_a_write_failure_is_shown_in_the_bar_not_raised(tmp_path: Path) -> None:
    bar = LayerFileBar(default_document("layer"), translator=_translator())
    try:
        bar.set_path(tmp_path / "x" / "\0bad")
        bar.save_button.click()
        assert bar.status_label.text()
    finally:
        bar.deleteLater()
