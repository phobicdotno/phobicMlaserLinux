"""ui/pages/param_pages.py: the hardware, machining, software and graph-rule pages (06 §8).

Every page is built from the real ``core/schema.json``, so the sections, rows,
units, enums and ranges are the vendor descriptor table's own.  Covered: the
four pages partition ``BkHardPara.xml`` and ``BkManuPara.xml`` exactly once each,
every section of every page builds, descriptor validation warns about an
out-of-range value and the spin-box range refuses one, a vendor file survives a
tour of all the editors byte for byte, and the backup-then-rename write of
:func:`nexcut.io.params.write_params`.  The last section is the main-window smoke
test with every dock built.

Offscreen Qt; nothing here touches a machine (PORT-PLAN §8).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QModelIndex, QSettings  # noqa: E402
from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox  # noqa: E402

from nexcut.core.schema import load_schema  # noqa: E402
from nexcut.io.params import (  # noqa: E402
    BACKUP_SUFFIX,
    default_document,
    read_params,
    serialize_params,
    write_params,
)
from nexcut.ui.i18n import LangCatalog, Translator  # noqa: E402
from nexcut.ui.main_window import MainWindow  # noqa: E402
from nexcut.ui.pages.param_pages import (  # noqa: E402
    GRAPH_RULES,
    HARDWARE,
    MACHINING,
    PAGE_SPECS,
    SOFTWARE,
    GraphRulePage,
    HardwarePage,
    MachiningPage,
    ParamPage,
    SoftwarePage,
    section_key,
)

PAGE_CLASSES = (HardwarePage, MachiningPage, SoftwarePage, GraphRulePage)


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
                    "hp2": "Hardware Parameters",
                    "mf10": "Save",
                    "mf13": "Reload",
                    "pd3": "Axis basic parameters.Pulse Equivalent",
                }.items()
            }
        )
    )


def _page(page_class: type[ParamPage], **kwargs: object) -> ParamPage:
    return page_class(translator=_translator(), **kwargs)  # type: ignore[arg-type]


@pytest.fixture()
def pages() -> Iterator[dict[str, ParamPage]]:
    built = {c.spec.page_id: _page(c) for c in PAGE_CLASSES}
    yield built
    for page in built.values():
        page.deleteLater()


# ============================================================================== build


def test_the_four_pages_cover_the_two_files_exactly_once(pages: dict[str, ParamPage]) -> None:
    """Hardware + machining + software + graph rules = every attribute of hard and manu."""
    schema = load_schema()
    expected: set[str] = set()
    for kind in ("hard", "manu"):
        for group, element, descriptor in schema.layout(kind).iter_attributes():
            expected.add(section_key(group.name, element.name, descriptor.attribute))
    seen: list[str] = []
    for page in pages.values():
        seen.extend(page.keys())
    assert len(seen) == len(set(seen)), "a page attribute is claimed twice"
    assert set(seen) == expected
    assert len(seen) == schema.layout("hard").attribute_count + schema.layout("manu").attribute_count


@pytest.mark.parametrize("page_class", PAGE_CLASSES)
def test_every_section_of_every_page_builds_from_the_real_schema(
    page_class: type[ParamPage],
) -> None:
    page = _page(page_class)
    try:
        assert page.sections(), f"{page.spec.page_id} has no section"
        for group, element in page.sections():
            page.set_section(group, element)
            descriptors = page.document.layout.group(group).element(element).attributes
            assert len(page.grid.rows) == len(descriptors)
            assert page.section == (group, element)
            # every row displays something and carries its own tooltip
            for key, row in page.grid.rows.items():
                assert page.grid.item_for(key) is not None
                assert page.grid.tooltip(row)
    finally:
        page.deleteLater()


def test_the_section_label_names_the_file_element(pages: dict[str, ParamPage]) -> None:
    """Port choice (module docstring): ``AxisParam / A0``, not a guessed vendor tab name."""
    page = pages["hardware"]
    assert page.section_label("PAxisParam", "A0") == "AxisParam / A0"
    assert page.section_box.itemText(0) == "AxisParam / A0"


def test_the_page_specs_are_the_four_pages() -> None:
    assert set(PAGE_SPECS) == {"hardware", "machining", "software", "graph_rules"}
    assert HARDWARE.kind == "hard"
    assert MACHINING.kind == SOFTWARE.kind == GRAPH_RULES.kind == "manu"


def test_a_document_of_the_wrong_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="needs a 'hard' document"):
        _page(HardwarePage, document=default_document("manu"))


def test_an_unknown_section_is_refused(pages: dict[str, ParamPage]) -> None:
    with pytest.raises(KeyError):
        pages["machining"].set_section("PGraphParam", "GRP")


def test_the_graph_rule_page_holds_the_166_attribute_grp_element(
    pages: dict[str, ParamPage],
) -> None:
    page = pages["graph_rules"]
    page.set_section("PGraphParam", "GRP")
    assert len(page.grid.rows) == 166


def test_the_software_page_shows_the_device_counters_read_only(
    pages: dict[str, ParamPage],
) -> None:
    """``SOP.Device*`` are written by the controller, not by the operator (01 §5)."""
    page = pages["software"]
    page.set_section("PSoftParam", "SOP")
    counters = [k for k in page.grid.rows if ".Device" in k]
    assert counters, "the SOP element should carry the device counters"
    assert all(page.grid.rows[k].read_only for k in counters)
    before = page.grid.value(counters[0])
    page.grid.set_value(counters[0], 12345)
    assert page.grid.value(counters[0]) == before


# ========================================================================= editing


def test_editing_a_row_writes_through_to_the_document(pages: dict[str, ParamPage]) -> None:
    page = pages["machining"]
    page.set_section("PManuParam", "MC")
    key = section_key("PManuParam", "MC", "ManuAcc")
    seen: list[tuple[str, object]] = []
    page.valueChanged.connect(lambda k, v: seen.append((k, v)))
    page.grid.set_value(key, 4321.0)
    assert page.document.get("PManuParam", "MC", "ManuAcc") == 4321.0
    assert seen == [(key, 4321.0)]


def test_validation_warns_about_a_value_outside_the_descriptor_range(
    pages: dict[str, ParamPage],
) -> None:
    """Descriptor min/max are warnings, never a rejection - vendor data may violate them."""
    page = pages["machining"]
    page.set_section("PManuParam", "MC")
    key = section_key("PManuParam", "MC", "CornerAccuracyRate")
    descriptor = page.grid.rows[key].descriptor
    assert descriptor.has_range
    problems = page.grid.set_value(key, descriptor.max + 10.0)
    assert any(descriptor.key in p and "outside" in p for p in problems)
    assert page.problem_label.text()


def test_the_spin_box_of_a_bounded_row_refuses_an_out_of_range_value(
    pages: dict[str, ParamPage],
) -> None:
    """The editor widget itself clamps, so a typed value can never leave the range."""
    page = pages["machining"]
    page.set_section("PManuParam", "MC")
    key = section_key("PManuParam", "MC", "CornerAccuracyRate")
    row = page.grid.rows[key]
    item = page.grid.item_for(key)
    assert item is not None
    index = page.grid.indexFromItem(item, 1)
    assert isinstance(index, QModelIndex)
    delegate = page.grid.itemDelegateForColumn(1)
    editor = delegate.createEditor(page.grid, None, index)
    assert isinstance(editor, (QSpinBox, QDoubleSpinBox))
    low, high = page.grid.editor_range(row)
    editor.setValue(high + 1000.0)
    assert editor.value() == pytest.approx(high)
    editor.setValue(low - 1000.0)
    assert editor.value() == pytest.approx(low)
    editor.deleteLater()


# ============================================================================== I/O


def test_save_writes_the_vendor_byte_format_and_keeps_a_backup(tmp_path: Path) -> None:
    page = _page(MachiningPage)
    try:
        target = tmp_path / "BkManuPara.xml"
        write_params(target, default_document("manu"))
        original = target.read_bytes()
        page.set_path(target)
        page.set_section("PManuParam", "MC")
        page.grid.set_value(section_key("PManuParam", "MC", "ManuAcc"), 1234.0)
        saved: list[str] = []
        page.documentSaved.connect(saved.append)
        assert page.save() == target
        assert saved == [str(target)]
        backup = target.with_name(target.name + BACKUP_SUFFIX)
        assert backup.read_bytes() == original
        assert target.read_bytes() == serialize_params(page.document)
        assert target.read_bytes() != original
    finally:
        page.deleteLater()


def test_reload_discards_the_edits(tmp_path: Path) -> None:
    page = _page(MachiningPage)
    try:
        target = tmp_path / "BkManuPara.xml"
        write_params(target, default_document("manu"))
        page.set_path(target)
        page.set_section("PManuParam", "MC")
        key = section_key("PManuParam", "MC", "ManuAcc")
        page.grid.set_value(key, 999.0)
        assert page.reload() is True
        page.set_section("PManuParam", "MC")
        assert page.grid.value(key) != 999.0
    finally:
        page.deleteLater()


def test_reload_without_a_path_does_nothing() -> None:
    page = _page(MachiningPage)
    try:
        assert page.reload() is False
    finally:
        page.deleteLater()


def test_save_without_a_path_asks_the_chooser(tmp_path: Path) -> None:
    page = _page(SoftwarePage)
    try:
        target = tmp_path / "chosen.xml"
        page.set_path_chooser(lambda save: str(target) if save else None)
        assert page.save() == target
        assert page.path == target
        assert target.is_file()
    finally:
        page.deleteLater()


def test_a_write_failure_is_reported_in_the_page_not_raised(tmp_path: Path) -> None:
    page = _page(SoftwarePage)
    try:
        page.set_path(tmp_path / "missing-dir" / "x" / "\0bad")
        page.save_button.click()
        assert page.problem_label.text()
    finally:
        page.deleteLater()


# ===================================================================== vendor files


@pytest.mark.parametrize(
    ("page_class", "kind", "name"),
    [
        (HardwarePage, "hard", "BkHardPara.xml"),
        (MachiningPage, "manu", "BkManuPara.xml"),
        (SoftwarePage, "manu", "BkManuPara.xml"),
        (GraphRulePage, "manu", "BkManuPara.xml"),
    ],
)
def test_a_vendor_file_survives_a_tour_of_every_section_byte_for_byte(
    src_dir: Path, tmp_path: Path, page_class: type[ParamPage], kind: str, name: str
) -> None:
    """Selecting every section must not touch a single value (02 §2.1 byte format)."""
    source = src_dir / "File" / name
    original = source.read_bytes()
    document = read_params(source, kind)
    page = _page(page_class, document=document)
    try:
        for group, element in page.sections():
            page.set_section(group, element)
            page.grid.refresh()
        target = tmp_path / name
        assert page.save(target) == target
        assert target.read_bytes() == original
    finally:
        page.deleteLater()


# ============================================================ main window, all docks


@pytest.fixture()
def window(tmp_path: Path, qapp: QtWidgets.QApplication) -> Iterator[MainWindow]:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    win = MainWindow(
        settings=settings,
        interactive=False,
        translator=Translator(LangCatalog()),
        manu=default_document("manu"),
        hard=default_document("hard"),
        layer=default_document("layer"),
        param_paths={"layer": tmp_path / "BkLayerPara.xml"},
    )
    win.resize(1200, 800)
    win.show()
    qapp.processEvents()
    yield win
    win.close()
    win.deleteLater()
    qapp.processEvents()


def test_the_window_builds_every_parameter_dock(window: MainWindow) -> None:
    assert set(window.param_docks) == set(PAGE_SPECS)
    assert window.crafts_dock is not None
    assert window.layer_tabs.count() == 2
    for page_id, dock in window.param_docks.items():
        assert dock.widget() is window.param_pages[page_id]


def test_every_dock_has_a_view_menu_entry(window: MainWindow) -> None:
    """A dock that starts hidden must still be reachable (the four parameter panes are)."""
    texts = {a.text() for menu in window.menuBar().actions() for a in (menu.menu() or []).actions()}
    for dock in (
        window.layer_dock,
        window.property_dock,
        window.layer_param_dock,
        *window.param_docks.values(),
        window.crafts_dock,
        window.machine_dock,
    ):
        assert dock.toggleViewAction().text() in texts


def test_showing_every_dock_renders(window: MainWindow, qapp: QtWidgets.QApplication) -> None:
    from PySide6.QtGui import QImage

    for dock in (*window.param_docks.values(), window.crafts_dock):
        dock.show()
    qapp.processEvents()
    image = QImage(window.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    window.render(image)
    qapp.processEvents()
    assert image.width() > 0


def test_the_machine_dock_is_still_disabled(window: MainWindow) -> None:
    """PORT-PLAN §8: no machine control exists in the UI, whatever else was added."""
    assert not window.machine_dock.widget().isEnabled()


def test_the_layer_dock_writes_the_layer_file_back(window: MainWindow, tmp_path: Path) -> None:
    """STATUS §1.4's missing piece: the dock saves ``BkLayerPara.xml``, not only a preset."""
    target = tmp_path / "BkLayerPara.xml"
    assert window.layer_file_bar.path == target
    window.layer_page.grid.set_value("CutSpeed", 42.0)
    assert window.layer_file_bar.save() == target
    assert target.read_bytes() == serialize_params(window.layer_page.document)
    reloaded = read_params(target, "layer")
    assert reloaded.get("PCO2LayerParam1", "GP", "CutSpeed") == 42.0
    # a second save keeps the first generation beside it (01 §0.3)
    window.layer_page.grid.set_value("CutSpeed", 43.0)
    window.layer_file_bar.save()
    assert target.with_name(target.name + BACKUP_SUFFIX).read_bytes() == serialize_params(reloaded)


def test_reloading_the_layer_file_rebinds_both_layer_pages(
    window: MainWindow, tmp_path: Path
) -> None:
    target = tmp_path / "BkLayerPara.xml"
    window.layer_file_bar.save()
    window.layer_page.grid.set_value("CutSpeed", 77.0)
    fresh = window.layer_file_bar.reload()
    assert fresh is not None
    assert window.layer_page.document is fresh
    assert window.fiber_layer_page.document is fresh
    assert window.layer_params is fresh
    assert window.layer_page.grid.value("CutSpeed") != 77.0
    assert target.is_file()


def test_the_parameter_pages_share_the_window_documents(window: MainWindow) -> None:
    assert window.param_pages["hardware"].document is window.hard_params
    assert window.param_pages["machining"].document is window.manu
    assert window.param_pages["software"].document is window.manu
    assert window.param_pages["graph_rules"].document is window.manu
    assert window.layer_page.document is window.fiber_layer_page.document
