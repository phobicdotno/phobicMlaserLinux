"""ui/property_grid.py: the schema-driven ``Group.Item`` grid (06 §8, PORT-PLAN §4 M3).

Built from the real ``core/schema.json``, so the rows, units, enums and ranges
are the vendor descriptor table's own.  Offscreen Qt only; nothing here touches
a machine (PORT-PLAN §8).
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QModelIndex, Qt  # noqa: E402
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox  # noqa: E402

from nexcut.core.schema import Descriptor, Schema, TypeCode, load_schema  # noqa: E402
from nexcut.io.params import default_document  # noqa: E402
from nexcut.ui.i18n import LangCatalog, Translator  # noqa: E402
from nexcut.ui.property_grid import (  # noqa: E402
    ACC_UNITS,
    PRESSURE_UNITS,
    SPEED_UNITS,
    UNIT_CLASS_QUANTITY,
    PropertyGrid,
    UnitPolicy,
    enum_labels,
    label_parts,
)


@pytest.fixture(scope="module", autouse=True)
def qapp() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def schema() -> Schema:
    return load_schema()


def _catalog(**entries: str) -> LangCatalog:
    """A stand-in ``lang.txt`` so grouping/enum tests do not need the vendor package."""
    return LangCatalog(entries={k: (v, v) for k, v in entries.items()})


def _co2_descriptors(schema: Schema) -> list[Descriptor]:
    return list(schema.layout("technology_co2").groups[0].elements[0].attributes)


def _grid(
    descriptors: list[Descriptor],
    values: dict[str, object],
    *,
    translator: Translator | None = None,
    units: UnitPolicy | None = None,
    read_only: tuple[str, ...] = (),
) -> PropertyGrid:
    grid = PropertyGrid(translator=translator or Translator(LangCatalog()), units=units)
    grid.set_source(lambda k: values[k], lambda k, v: values.__setitem__(k, v))
    grid.build(descriptors, keys=lambda d: d.attribute, read_only_keys=read_only)
    return grid


@pytest.fixture()
def co2(schema: Schema) -> Iterator[tuple[PropertyGrid, dict[str, object]]]:
    ds = _co2_descriptors(schema)
    values: dict[str, object] = {d.attribute: d.default for d in ds}
    t = Translator(
        _catalog(
            pd125="Cut Basic.Cut Speed",
            pd127="Cut Laser.Cut Power",
            pd128="Cut Laser.Cut Freq",
            pd130="Cut Gas.Gas Type",
            pd131="Cut Gas.Gas Pressure",
            pd133="Adv Parameters.Slow Start",
            pd153="Misc.Notes",
            pd68="Low Air",
            pd69="Low O2",
            pd70="Low N2",
            pd71="High Air",
            pd72="High O2",
            pd73="High N2",
            pd51="Others",
        )
    )
    grid = _grid(ds, values, translator=t)
    yield grid, values
    grid.deleteLater()


# ============================================================================ build


def test_grid_is_built_from_the_real_schema(co2: tuple[PropertyGrid, dict]) -> None:
    """One row per CO2 layer descriptor (02 §2.3 lists 21), grouped by the label's Group half."""
    grid, _ = co2
    assert len(grid.rows) == 21
    assert grid.rows["CutSpeed"].group == "Cut Basic"
    assert grid.rows["CutSpeed"].item == "Cut Speed"
    assert grid.rows["CutFreq"].group == "Cut Laser"
    # a label with no dot, and an id missing from the catalog, land in pd51 "Others"
    assert grid.rows["PowerAdjustWithSpeed"].group == "Others"
    assert grid.group_names()[0] in {"Others", "Cut Basic"}
    assert set(grid.group_names()) == {r.group for r in grid.rows.values()}
    total = sum(grid.topLevelItem(i).childCount() for i in range(grid.topLevelItemCount()))
    assert total == len(grid.rows)


def test_group_order_and_case_folding(schema: Schema) -> None:
    """``group_order`` leads; sections differing only in case merge onto the first spelling."""
    ds = _co2_descriptors(schema)
    t = Translator(
        _catalog(pd130="Cut Gas.Gas Type", A241025_1="Cut gas.No gas for short distance")
    )
    grid = _grid(ds, {d.attribute: d.default for d in ds}, translator=t)
    assert grid.rows["CutGasType"].group == "Cut Gas"
    assert grid.rows["ShortDistGasKeepOn"].group == "Cut Gas"  # "Cut gas" folded
    grid2 = PropertyGrid(translator=t)
    grid2.set_source(lambda k: 0, lambda k, v: None)
    grid2.build(ds, keys=lambda d: d.attribute, group_order=("Cut Gas",))
    assert grid2.group_names()[0] == "Cut Gas"
    grid.deleteLater()
    grid2.deleteLater()


def test_duplicate_item_labels_are_disambiguated(schema: Schema) -> None:
    """``LayerFileName`` and ``PWMCurveNodes`` share id ``pd154`` (02 §7 caption bug)."""
    ds = _co2_descriptors(schema)
    t = Translator(_catalog(pd154="Misc.Power Curves"))
    grid = _grid(ds, {d.attribute: d.default for d in ds}, translator=t)
    assert grid.rows["PWMCurveNodes"].item == "Power Curves (PWMCurveNodes)"
    assert grid.rows["LayerFileName"].item == "Power Curves (LayerFileName)"
    grid.deleteLater()


def test_label_parts_falls_back_to_the_attribute_name(schema: Schema) -> None:
    d = schema.layout("technology_co2").groups[0].elements[0].attribute("CutSpeed")
    empty = Translator(LangCatalog())
    assert label_parts(d, empty) == ("Others", "CutSpeed")
    named = Translator(_catalog(pd125="Cut Basic.Cut Speed", pd51="其它"))
    assert label_parts(d, named) == ("Cut Basic", "Cut Speed")


# ============================================================================ rendering


def test_enum_row_shows_the_option_label(co2: tuple[PropertyGrid, dict]) -> None:
    """Gas type options come from the descriptor's enum ids (02 §2.5; here pd68..pd73)."""
    grid, values = co2
    row = grid.rows["CutGasType"]
    assert row.is_enum and not row.is_bool
    assert row.descriptor.enum_ids == ("pd68", "pd69", "pd70", "pd71", "pd72", "pd73")
    assert enum_labels(row.descriptor, grid.t) == [
        "Low Air",
        "Low O2",
        "Low N2",
        "High Air",
        "High O2",
        "High N2",
    ]
    values["CutGasType"] = 5
    grid.refresh()
    assert grid.item_for("CutGasType").text(1) == "High N2"


def test_bool_row_is_a_checkbox(co2: tuple[PropertyGrid, dict]) -> None:
    """02 §2.4: ``0``/``1`` ints are drawn as a checkbox, and toggling writes back."""
    grid, values = co2
    item = grid.item_for("SlowStart")
    assert grid.rows["SlowStart"].is_bool
    assert item.checkState(1) == Qt.CheckState.Unchecked
    assert item.text(1) == ""
    item.setCheckState(1, Qt.CheckState.Checked)
    assert values["SlowStart"] == 1
    values["SlowStart"] = 0
    grid.refresh()
    assert item.checkState(1) == Qt.CheckState.Unchecked


def test_string_and_int_rendering(co2: tuple[PropertyGrid, dict]) -> None:
    grid, values = co2
    values["Note"] = "acrylic 24 mm"
    values["CutFreq"] = 5000
    values["CutDuty"] = 88
    grid.refresh()
    assert grid.item_for("Note").text(1) == "acrylic 24 mm"
    assert grid.item_for("CutFreq").text(1) == "5000 Hz"
    assert grid.item_for("CutDuty").text(1) == "88 %"


def test_tooltip_carries_key_label_unit_and_range(co2: tuple[PropertyGrid, dict]) -> None:
    grid, _ = co2
    tip = grid.tooltip(grid.rows["CutSpeed"])
    assert "GP.CutSpeed" in tip and "[pd125]" in tip
    assert "Cut Basic.Cut Speed" in tip
    assert "range 0.01 .. 1000" in tip
    assert "default '100'" in tip


# ============================================================================ units


UNIT_OF_CLASS = {
    1: "mm",
    2: "°",
    3: "Hz",
    4: "%",
    5: "p/mm",
    6: "ms",
    9: "mm/s",
    10: "mm/s",
    12: "mm/s2",
    14: "V",
    19: "W",
    20: "us",
}
"""Unit string the descriptor table itself gives each class (see the test below)."""

UNIT_CLASS_OUTLIERS = {
    "GRP.AlphaMinEdgeLen": (1, "°"),
    "GRP.AlphaLen": (1, "°"),
    "GRP.CleanMoveSpeed": (9, "mm"),
}
"""The three descriptors whose unit string disagrees with their class (vendor data)."""


def test_unit_class_table_matches_the_descriptor_units(schema: Schema) -> None:
    """The class -> quantity map is read off the descriptor table's own unit strings.

    Exactly three of the 1 001 bound descriptors disagree with their class; they
    are pinned here so a schema regeneration that changes the mapping is noticed.
    """
    quantity_of_unit = {
        "mm": "length",
        "°": "angle",
        "Hz": "frequency",
        "%": "percent",
        "p/mm": "pulse",
        "ms": "time",
        "mm/s": "speed",
        "mm/s2": "acceleration",
        "V": "pressure",
        "W": "power",
        "us": "microsecond",
    }
    for cls, unit in UNIT_OF_CLASS.items():
        assert UNIT_CLASS_QUANTITY[cls] == quantity_of_unit[unit], cls
    found: dict[str, tuple[int, str]] = {}
    for d in schema.descriptors:
        unit = d.unit.replace(" ", "")
        if unit and d.unit_class in UNIT_OF_CLASS and unit != UNIT_OF_CLASS[d.unit_class]:
            found[d.key] = (d.unit_class, unit)
    assert found == UNIT_CLASS_OUTLIERS


def test_speed_display_follows_un_speedunit(schema: Schema) -> None:
    """01 §938 / 02 §2.4: stored mm/s, displayed per ``UN.SpeedUnit`` (11 mm/s = 0.66 m/min)."""
    ds = _co2_descriptors(schema)
    values: dict[str, object] = {d.attribute: d.default for d in ds}
    values["CutSpeed"] = 11.000000000000002  # the Carbon-10 mm preset value (02 §2.4)
    grid = _grid(ds, values, units=UnitPolicy(speed=1))
    assert grid.item_for("CutSpeed").text(1) == "0.66 m/min"
    grid.set_units(UnitPolicy(speed=0))
    assert grid.item_for("CutSpeed").text(1) == "11 mm/s"
    grid.set_units(UnitPolicy(speed=2))
    assert grid.rows["CutSpeed"].unit.suffix == "inch/s"
    assert values["CutSpeed"] == 11.000000000000002  # display only: nothing was rewritten
    grid.deleteLater()


def test_pressure_display_follows_un_gaspressureunit(schema: Schema) -> None:
    """02 §2.4: the descriptor unit is V but the value is bar; MPa divides by 10."""
    ds = _co2_descriptors(schema)
    values: dict[str, object] = {d.attribute: d.default for d in ds}
    values["CutAirPressure"] = 0.6
    grid = _grid(ds, values, units=UnitPolicy(pressure=0))
    assert grid.item_for("CutAirPressure").text(1) == "0.6 bar"
    grid.set_units(UnitPolicy(pressure=1))
    assert grid.item_for("CutAirPressure").text(1) == "0.06 MPa"
    grid.deleteLater()


def test_unit_policy_from_manu_reads_the_un_element() -> None:
    """``PManuParam/UN.*`` selects the display units; a foreign object falls back to SI."""
    manu = default_document("manu")
    assert UnitPolicy.from_manu(manu) == UnitPolicy(0, 0, 0)  # descriptor defaults
    manu.set("PManuParam", "UN", "SpeedUnit", 1)
    manu.set("PManuParam", "UN", "AccUnit", 1)
    manu.set("PManuParam", "UN", "GasPressureUnit", 1)
    assert UnitPolicy.from_manu(manu) == UnitPolicy(1, 1, 1)
    assert UnitPolicy.from_manu(None) == UnitPolicy()
    assert UnitPolicy.from_manu(object()) == UnitPolicy()
    manu.set("PManuParam", "UN", "SpeedUnit", 99, check=False)
    assert UnitPolicy.from_manu(manu).speed == 0  # out of range -> mm/s


def test_unit_conversions_round_trip() -> None:
    for table in (SPEED_UNITS, ACC_UNITS, PRESSURE_UNITS):
        for unit in table:
            assert unit.to_stored(unit.to_display(37.5)) == pytest.approx(37.5)
    assert SPEED_UNITS[1].to_display(250.0) == pytest.approx(15.0)  # 250 mm/s = 15 m/min
    assert ACC_UNITS[1].to_display(9806.65) == pytest.approx(1.0)  # 1 G
    assert PRESSURE_UNITS[1].to_display(10.0) == pytest.approx(1.0)  # 10 bar = 1 MPa


# ============================================================================ editing


def test_set_value_writes_through_and_coerces(co2: tuple[PropertyGrid, dict]) -> None:
    grid, values = co2
    seen: list[tuple[str, object]] = []
    grid.valueChanged.connect(lambda k, v: seen.append((k, v)))
    assert grid.set_value("CutSpeed", 250) == []
    assert values["CutSpeed"] == 250.0 and isinstance(values["CutSpeed"], float)
    assert grid.set_value("CutFreq", 5000.0) == []
    assert values["CutFreq"] == 5000 and isinstance(values["CutFreq"], int)
    grid.set_value("Note", 7)
    assert values["Note"] == "7"
    assert [k for k, _ in seen] == ["CutSpeed", "CutFreq", "Note"]


def test_out_of_range_value_is_reported_not_rejected(co2: tuple[PropertyGrid, dict]) -> None:
    """Vendor data may violate its own min/max, so the grid warns (core/schema ``validate``)."""
    grid, values = co2
    problems: list[list[str]] = []
    grid.validationChanged.connect(lambda p: problems.append(list(p)))
    out = grid.set_value("CutSpeed", 5000.0)
    assert values["CutSpeed"] == 5000.0
    assert out == ["GP.CutSpeed: 5000.0 outside [0.01, 1000]"]
    assert problems and problems[-1] == out
    assert grid.item_for("CutSpeed").background(1).color().name() == "#ffd5d5"
    assert grid.set_value("CutSpeed", 250.0) == []
    assert grid.item_for("CutSpeed").background(1).style() == Qt.BrushStyle.NoBrush


def test_cross_check_is_run_after_every_edit(co2: tuple[PropertyGrid, dict]) -> None:
    grid, _ = co2
    calls = {"n": 0}

    def rule() -> list[str]:
        calls["n"] += 1
        return ["lp19 sample"]

    grid.set_cross_check(rule)
    assert grid.problems == ["lp19 sample"]
    before = calls["n"]
    grid.set_value("CutDuty", 50)
    assert calls["n"] > before
    grid.set_cross_check(None)
    assert grid.problems == []


def test_read_only_rows_cannot_be_edited(schema: Schema) -> None:
    """Rows the analysis marks UNVERIFIED are shown but never written (PORT-PLAN §8)."""
    ds = _co2_descriptors(schema)
    values: dict[str, object] = {d.attribute: d.default for d in ds}
    grid = _grid(ds, values, read_only=("CutAirPressure",))
    item = grid.item_for("CutAirPressure")
    assert not item.flags() & Qt.ItemFlag.ItemIsEditable
    before = values["CutAirPressure"]
    assert grid.set_value("CutAirPressure", 3.0) == grid.problems
    assert values["CutAirPressure"] == before
    assert _editor(grid, "CutAirPressure") is None
    assert "read-only" in grid.tooltip(grid.rows["CutAirPressure"])
    grid.deleteLater()


# ============================================================================ editors


def _editor(grid: PropertyGrid, key: str) -> object:
    item = grid.item_for(key)
    index = grid.indexFromItem(item, 1)
    assert isinstance(index, QModelIndex)
    delegate = grid.itemDelegateForColumn(1)
    editor = delegate.createEditor(grid, QtWidgets.QStyleOptionViewItem(), index)
    if editor is not None:
        delegate.setEditorData(editor, index)
    return editor


def test_editor_types_and_ranges_follow_the_descriptor(co2: tuple[PropertyGrid, dict]) -> None:
    grid, values = co2
    values["CutSpeed"] = 100.0
    values["CutFreq"] = 5000
    values["CutGasType"] = 2
    values["Note"] = "hello"
    grid.refresh()

    speed = _editor(grid, "CutSpeed")
    assert isinstance(speed, QDoubleSpinBox)
    assert (speed.minimum(), speed.maximum()) == pytest.approx((0.01, 1000.0))
    assert speed.value() == pytest.approx(100.0)
    assert speed.suffix().strip() == "mm/s"

    freq = _editor(grid, "CutFreq")
    assert isinstance(freq, QSpinBox)
    assert (freq.minimum(), freq.maximum()) == (1, 50000)
    assert freq.value() == 5000

    gas = _editor(grid, "CutGasType")
    assert isinstance(gas, QComboBox)
    assert gas.count() == 6 and gas.currentIndex() == 2

    note = _editor(grid, "Note")
    assert isinstance(note, QLineEdit) and note.text() == "hello"

    assert _editor(grid, "SlowStart") is None  # bools use the checkbox


def test_editor_writes_back_in_display_units(schema: Schema) -> None:
    """A spin box shows m/min and stores mm/s (01 §1135: the file keeps SI)."""
    ds = _co2_descriptors(schema)
    values: dict[str, object] = {d.attribute: d.default for d in ds}
    grid = _grid(ds, values, units=UnitPolicy(speed=1))
    item = grid.item_for("CutSpeed")
    index = grid.indexFromItem(item, 1)
    delegate = grid.itemDelegateForColumn(1)
    editor = delegate.createEditor(grid, QtWidgets.QStyleOptionViewItem(), index)
    delegate.setEditorData(editor, index)
    assert editor.value() == pytest.approx(6.0)  # 100 mm/s
    editor.setValue(0.66)
    delegate.setModelData(editor, grid.model(), index)
    assert values["CutSpeed"] == pytest.approx(11.0)
    assert grid.item_for("CutSpeed").text(1) == "0.66 m/min"
    grid.deleteLater()


def test_editor_is_refused_on_the_name_column(co2: tuple[PropertyGrid, dict]) -> None:
    grid, _ = co2
    index = grid.indexFromItem(grid.item_for("CutSpeed"), 0)
    delegate = grid.itemDelegateForColumn(1)
    assert delegate.createEditor(grid, QtWidgets.QStyleOptionViewItem(), index) is None
    header = grid.indexFromItem(grid.topLevelItem(0), 1)
    assert delegate.createEditor(grid, QtWidgets.QStyleOptionViewItem(), header) is None


# ============================================================================ whole schema


def test_every_bound_descriptor_can_be_placed(schema: Schema) -> None:
    """The grid must survive every layout in the schema, not just the CO2 page."""
    t = Translator(LangCatalog())
    for kind in schema.layout_kinds:
        doc = default_document(kind)
        layout = doc.layout
        group = layout.groups[0]
        element = group.elements[0]
        values = {d.key: doc.get(group.name, element.name, d.attribute) for d in element.attributes}
        grid = PropertyGrid(translator=t)
        grid.set_source(values.__getitem__, values.__setitem__)
        grid.build(element.attributes)
        assert len(grid.rows) == len(element.attributes)
        for key in grid.rows:
            assert isinstance(grid.display_text(key), str)
            assert grid.editor_range(grid.rows[key])[0] <= grid.editor_range(grid.rows[key])[1]
        grid.deleteLater()


def test_non_finite_and_enum_edge_values_render(schema: Schema) -> None:
    """A value outside the enum range or a NaN double must not crash the display."""
    ds = _co2_descriptors(schema)
    values: dict[str, object] = {d.attribute: d.default for d in ds}
    grid = _grid(ds, values)
    values["CutGasType"] = 9
    values["CutSpeed"] = math.nan
    grid.refresh()
    assert grid.item_for("CutGasType").text(1) == "9 (?)"
    assert "nan" in grid.item_for("CutSpeed").text(1).lower()
    assert any("CutGasType" in p for p in grid.problems)
    grid.deleteLater()


def test_type_codes_used_by_the_co2_page(schema: Schema) -> None:
    """Sanity: the page exercises the four editor families of the grid."""
    codes = {d.type_code for d in _co2_descriptors(schema)}
    assert TypeCode.DOUBLE in codes and TypeCode.INT in codes
    assert TypeCode.ENUM in codes and TypeCode.BOOL in codes and TypeCode.STRING in codes
