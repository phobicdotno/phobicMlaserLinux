"""Schema-driven property grid (``CBCGPPropList``-style pages, 06 §8; PORT-PLAN §4 M3).

06 §8: the vendor builds its parameter pages programmatically from BCG property
lists, not from resource templates - "parameter pages are pure ``Group.Item``
label lists".  This module reproduces that shape from the port's own descriptor
schema (:mod:`nexcut.core.schema`): every row is one
:class:`~nexcut.core.schema.Descriptor`, rows are grouped by the ``Group`` half
of their ``lang.txt`` label, and the editor for a row follows the descriptor's
type code, ``min``/``max`` and enum option ids.

What comes from where:

* **grouping** - ``lang.txt`` label text ``"Cut Gas.Gas Pressure"`` splits into
  group ``Cut Gas`` and item ``Gas Pressure`` (06 §2 Readme note, the same rule
  as :meth:`nexcut.ui.i18n.Translator.item`).  A label without a dot goes into
  the fallback group (``pd51`` "Others").
* **units** - the stored value is always in the descriptor's own unit (01 §1135:
  internal SI).  The *display* unit is chosen by ``PManuParam/UN.SpeedUnit``,
  ``UN.AccUnit`` and ``UN.GasPressureUnit`` (01 §938, 02 §2.4), applied through
  :class:`UnitPolicy` by the descriptor's ``unit_class``.
* **enums** - option labels are the descriptor's ``enum_ids`` resolved through
  ``lang.txt`` (02 §2.5); a ``bool`` descriptor is a checkbox (02 §2.4).
* **validation** - :meth:`nexcut.core.schema.Descriptor.validate` (min/max and
  enum index), plus the cross-field rules a page adds with
  :meth:`PropertyGrid.set_cross_check` - for the layer page that is ``lp19``
  (02 §3.2: only ``lp19`` is enforced by this build).
* **tooltips** - the full ``Group.Item`` label in both languages, the
  ``Element.Attribute`` key, the unit and the range.

SAFETY (PORT-PLAN §8): this is a file/parameter editor.  It owns no connection,
never moves an axis and never arms a laser.  Rows whose meaning the analysis
marks UNVERIFIED are shown read-only (``read_only_keys``), so a value nobody has
confirmed cannot be changed and written back to a machine file.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from nexcut.core.schema import Descriptor, TypeCode, Value
from nexcut.ui.i18n import Translator, get_translator

__all__ = [
    "FALLBACK_GROUP_ID",
    "UNIT_CLASS_QUANTITY",
    "UNVERIFIED",
    "PropertyGrid",
    "PropertyRow",
    "UnitDisplay",
    "UnitPolicy",
    "enum_labels",
    "label_parts",
]

FALLBACK_GROUP_ID = "pd51"
"""``lang.txt`` id ("其它" / "Others") used as the group of a label with no ``Group.`` half."""

UNVERIFIED: tuple[str, ...] = (
    "unit_class -> physical quantity: the mapping below is read off the descriptor "
    "table's own unit strings (class 9/10 = mm/s, 12 = mm/s2, 14 = V/bar, ...); the "
    "vendor's own UN.* dispatch was not traced",
    "gas pressure is stored with descriptor unit V but holds bar (02 §2.4, "
    "DAMaxPressure=10 on a 0-10 V DA makes 1 bar = 1 V numerically); MPa display "
    "divides by 10",
    "UN.SpeedUnit option order 0=mm/s 1=m/min 2=inch/s 3=inch/min and UN.AccUnit "
    "0=mm/s2 1=G 2=inch/s2 (02 §2.4 VERIFIER, INFERENCE medium-high)",
    "decimals shown per quantity are a port choice; the vendor's per-item precision "
    "is not traced",
)

# unit_class -> the quantity the value carries.  "" means "show the descriptor unit".
UNIT_CLASS_QUANTITY: dict[int, str] = {
    1: "length",
    2: "angle",
    3: "frequency",
    4: "percent",
    5: "pulse",
    6: "time",
    7: "second",
    9: "speed",
    10: "speed",
    12: "acceleration",
    14: "pressure",
    19: "power",
    20: "microsecond",
}

_G = 9806.65  # mm/s^2 per g (02 §2.4 UN.AccUnit option pd55 "G")
_INCH = 25.4  # mm per inch


@dataclass(frozen=True, slots=True)
class UnitDisplay:
    """How one descriptor's value is shown: ``display = stored * factor``."""

    suffix: str
    factor: float = 1.0
    decimals: int = 3

    def to_display(self, stored: float) -> float:
        return stored * self.factor

    def to_stored(self, shown: float) -> float:
        return shown / self.factor


_FIXED: dict[str, UnitDisplay] = {
    "length": UnitDisplay("mm", 1.0, 3),
    "angle": UnitDisplay("°", 1.0, 3),
    "frequency": UnitDisplay("Hz", 1.0, 0),
    "percent": UnitDisplay("%", 1.0, 1),
    "pulse": UnitDisplay("p/mm", 1.0, 4),
    "time": UnitDisplay("ms", 1.0, 0),
    "second": UnitDisplay("s", 1.0, 1),
    "power": UnitDisplay("W", 1.0, 0),
    "microsecond": UnitDisplay("µs", 1.0, 0),
}

SPEED_UNITS: tuple[UnitDisplay, ...] = (
    UnitDisplay("mm/s", 1.0, 3),
    UnitDisplay("m/min", 0.06, 4),
    UnitDisplay("inch/s", 1.0 / _INCH, 4),
    UnitDisplay("inch/min", 60.0 / _INCH, 3),
)
"""``UN.SpeedUnit`` 0..3 (option ids ``pd52``, ``pd53``, ``pd53-1``, ``pd53-2``; 02 §2.4)."""

ACC_UNITS: tuple[UnitDisplay, ...] = (
    UnitDisplay("mm/s²", 1.0, 1),
    UnitDisplay("G", 1.0 / _G, 4),
    UnitDisplay("inch/s²", 1.0 / _INCH, 3),
)
"""``UN.AccUnit`` 0..2 (option ids ``pd54``, ``pd55``, ``pd55-1``; 01 §938)."""

PRESSURE_UNITS: tuple[UnitDisplay, ...] = (
    UnitDisplay("bar", 1.0, 2),
    UnitDisplay("MPa", 0.1, 3),
)
"""``UN.GasPressureUnit`` 0..1 (option ids ``pd900`` bar, ``pd901`` MPa; 02 §2.4)."""


@dataclass(frozen=True, slots=True)
class UnitPolicy:
    """Display units selected by ``PManuParam/UN.*`` (01 §938; values stay SI)."""

    speed: int = 0
    acceleration: int = 0
    pressure: int = 0

    @classmethod
    def from_manu(cls, manu: object | None) -> UnitPolicy:
        """Read ``UN.SpeedUnit``/``UN.AccUnit``/``UN.GasPressureUnit`` from a ``manu`` document.

        Anything that is not a ``manu`` :class:`~nexcut.io.params.ParamDocument`
        (including ``None``) gives the SI defaults, which is what the descriptor
        defaults say too (all three default to 0).
        """
        if manu is None:
            return cls()
        try:
            speed = int(manu.get("PManuParam", "UN", "SpeedUnit"))  # type: ignore[attr-defined]
            acc = int(manu.get("PManuParam", "UN", "AccUnit"))  # type: ignore[attr-defined]
            pressure = int(manu.get("PManuParam", "UN", "GasPressureUnit"))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - any foreign object falls back to SI
            return cls()
        return cls(_clamp(speed, SPEED_UNITS), _clamp(acc, ACC_UNITS), _clamp(pressure, PRESSURE_UNITS))

    def display(self, descriptor: Descriptor) -> UnitDisplay:
        """Display unit and conversion for one descriptor (UNVERIFIED mapping, see module)."""
        quantity = UNIT_CLASS_QUANTITY.get(descriptor.unit_class, "")
        if quantity == "speed":
            return SPEED_UNITS[self.speed]
        if quantity == "acceleration":
            return ACC_UNITS[self.acceleration]
        if quantity == "pressure":
            return PRESSURE_UNITS[self.pressure]
        fixed = _FIXED.get(quantity)
        if fixed is not None:
            return fixed
        # No unit class: keep whatever string the descriptor carries (often empty).
        return UnitDisplay(descriptor.unit.replace(" ", ""), 1.0, 3)


def _clamp(value: int, table: Sequence[UnitDisplay]) -> int:
    return value if 0 <= value < len(table) else 0


def label_parts(descriptor: Descriptor, translator: Translator) -> tuple[str, str]:
    """``(group, item)`` of a descriptor's ``Group.Item`` label (06 §2 Readme note).

    A label with no dot keeps the whole text as the item and lands in the
    fallback group; a missing label id falls back to the attribute name.
    """
    text = translator.tr(descriptor.label_id, "").strip()
    if not text or text == descriptor.label_id:
        return translator.tr(FALLBACK_GROUP_ID, "Others"), descriptor.attribute
    group, dot, item = text.partition(".")
    if not dot:
        return translator.tr(FALLBACK_GROUP_ID, "Others"), text
    return group.strip(), item.strip().rstrip(":")


def enum_labels(descriptor: Descriptor, translator: Translator) -> list[str]:
    """Option labels of an enum descriptor, in index order (02 §2.5).

    ``bool`` descriptors (type code 5) carry no option ids and are drawn as a
    checkbox, so they return an empty list.
    """
    if descriptor.type_code == TypeCode.BOOL or not descriptor.enum_ids:
        return []
    return [translator.tr(i, i) for i in descriptor.enum_ids]


@dataclass(frozen=True, slots=True)
class PropertyRow:
    """One editable row: a descriptor plus where its value lives."""

    descriptor: Descriptor
    key: str
    """Caller-chosen identity of the row (``"Elem.Attr"`` by default)."""
    group: str
    item: str
    unit: UnitDisplay
    read_only: bool = False

    @property
    def is_bool(self) -> bool:
        """02 §2.4: ``0``/``1`` ints are drawn as a checkbox."""
        return self.descriptor.type_code == TypeCode.BOOL

    @property
    def is_enum(self) -> bool:
        return self.descriptor.type_code == TypeCode.ENUM


_ROW_ROLE = Qt.ItemDataRole.UserRole
_VALUE_COLUMN = 1

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
"""Range a ``QSpinBox`` can hold; a wider descriptor range is clipped to it (see
:meth:`PropertyGrid.editor_range`)."""

_WIDE_RANGE = 1e9
"""Editor bound for a descriptor whose ``min``/``max`` are ``0/0`` (02 §2.1: unbounded)."""

_QUANTISE_STEPS = 4
"""How far :meth:`PropertyGrid.editor_range` may step a bound inward before giving up."""

_EPS = 1e-9


def _snap(value: float, decimals: int, *, up: bool) -> float:
    """Round ``value`` to ``decimals``, never away from the range it bounds."""
    out = round(value, decimals)
    step = 10.0 ** -decimals
    if up and out < value - _EPS:
        return round(out + step, decimals)
    if not up and out > value + _EPS:
        return round(out - step, decimals)
    return out


_OPENED_AT = "nexcutOpenedAt"
"""Qt property holding the value an editor was opened with (see ``_Delegate.setModelData``)."""


def _editor_value(editor: QWidget) -> object:
    """The editor's current value, whatever kind of editor it is."""
    if isinstance(editor, QComboBox):
        return editor.currentIndex()
    if isinstance(editor, QLineEdit):
        return editor.text()
    if isinstance(editor, QSpinBox | QDoubleSpinBox):
        return editor.value()
    return None


class _Delegate(QStyledItemDelegate):
    """Editor per row type: combo for enum/bool, spin box for int/double, line edit for string."""

    def __init__(self, grid: PropertyGrid) -> None:
        super().__init__(grid)
        self.grid = grid

    def createEditor(
        self, parent: QWidget, option: QStyleOptionViewItem, index: object
    ) -> QWidget | None:
        if index.column() != _VALUE_COLUMN:  # type: ignore[attr-defined]
            return None
        row = self.grid.row_at(index)  # type: ignore[arg-type]
        if row is None or row.read_only or row.is_bool:
            return None
        d = row.descriptor
        if row.is_enum:
            box = QComboBox(parent)
            for text in self.grid.option_texts(row):
                box.addItem(text)
            return box
        if d.storage == "string":
            return QLineEdit(parent)
        lo, hi = self.grid.editor_range(row)
        if self.grid.uses_int_spin(row):
            spin = QSpinBox(parent)
            spin.setRange(int(lo), int(hi))
            spin.setSuffix(f" {row.unit.suffix}" if row.unit.suffix else "")
            return spin
        dspin = QDoubleSpinBox(parent)
        dspin.setDecimals(row.unit.decimals)
        dspin.setRange(lo, hi)
        dspin.setSuffix(f" {row.unit.suffix}" if row.unit.suffix else "")
        return dspin

    def setEditorData(self, editor: QWidget, index: object) -> None:
        row = self.grid.row_at(index)  # type: ignore[arg-type]
        if row is None:
            return
        value = self.grid.value(row.key)
        if isinstance(editor, QComboBox):
            editor.setCurrentIndex(max(0, min(int(value), editor.count() - 1)))
        elif isinstance(editor, QLineEdit):
            editor.setText(str(value))
        elif isinstance(editor, QSpinBox):
            editor.setValue(int(round(row.unit.to_display(float(value)))))
        elif isinstance(editor, QDoubleSpinBox):
            editor.setValue(row.unit.to_display(float(value)))
        if isinstance(editor, QSpinBox | QDoubleSpinBox | QComboBox | QLineEdit):
            editor.setProperty(_OPENED_AT, _editor_value(editor))

    def setModelData(self, editor: QWidget, model: object, index: object) -> None:
        row = self.grid.row_at(index)  # type: ignore[arg-type]
        if row is None:
            return
        # An editor that was opened and closed without a change writes nothing.  It is not
        # an optimisation: display units round (20 000 mm/s2 is 2.0394 G at four decimals,
        # back 19 999.68) and a bound can differ from the stored value, so writing back an
        # untouched editor silently edits the file - measured on the vendor BkHardPara /
        # BkManuPara: 15 rows moved, FCP.MaxAcc by 2 % and GRP.EdgeSeekSensitivity 20 -> 17.
        opened_at = editor.property(_OPENED_AT)
        if opened_at is not None and _editor_value(editor) == opened_at:
            return
        if isinstance(editor, QComboBox):
            self.grid.set_value(row.key, editor.currentIndex())
        elif isinstance(editor, QLineEdit):
            self.grid.set_value(row.key, editor.text())
        elif isinstance(editor, QSpinBox):
            stored = int(round(row.unit.to_stored(editor.value())))
            self.grid.set_value(row.key, self.grid.clamp_stored(row, stored))
        elif isinstance(editor, QDoubleSpinBox):
            stored = row.unit.to_stored(editor.value())
            self.grid.set_value(row.key, self.grid.clamp_stored(row, stored))


class PropertyGrid(QTreeWidget):
    """A ``Group.Item`` property page over a ``{key: value}`` mapping (module docstring).

    The grid does not own the values: :meth:`set_source` installs a getter and a
    setter, so the same widget edits a
    :class:`~nexcut.io.params.ParamDocument` layer slot, a
    :class:`~nexcut.io.params.TechnologyPreset` or a plain dict.
    """

    valueChanged = Signal(str, object)
    """``(key, stored value)`` after an edit that the source accepted."""

    validationChanged = Signal(list)
    """Current problem strings (empty list = the page is valid)."""

    def __init__(
        self,
        *,
        translator: Translator | None = None,
        units: UnitPolicy | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.t = translator or get_translator()
        self.units = units or UnitPolicy()
        self.rows: dict[str, PropertyRow] = {}
        self._items: dict[str, QTreeWidgetItem] = {}
        self._groups: dict[str, QTreeWidgetItem] = {}
        self._get: Callable[[str], Value] = lambda key: 0
        self._set: Callable[[str, Value], None] = lambda key, value: None
        self._cross: Callable[[], list[str]] = list
        self._option_overrides: dict[str, list[str]] = {}
        self._problems: list[str] = []
        self._updating = False
        self.setColumnCount(2)
        # Port choice: lang.txt has no "Parameter"/"Value" column captions (the vendor's
        # BCG property list draws its own), so these two strings are plain English.
        self.setHeaderLabels(["Parameter", "Value"])
        self.setRootIsDecorated(True)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setEditTriggers(
            QTreeWidget.EditTrigger.DoubleClicked | QTreeWidget.EditTrigger.SelectedClicked
        )
        self.setItemDelegateForColumn(_VALUE_COLUMN, _Delegate(self))
        self.itemChanged.connect(self._on_item_changed)

    # ------------------------------------------------------------------ build
    def build(
        self,
        descriptors: Iterable[Descriptor],
        *,
        keys: Callable[[Descriptor], str] | None = None,
        read_only_keys: Iterable[str] = (),
        group_order: Sequence[str] | None = None,
    ) -> None:
        """(Re)build the rows from ``descriptors``, keeping the source and the units.

        ``keys`` maps a descriptor to the identity used with the source (default
        ``Element.Attribute``).  Groups appear in first-occurrence order unless
        ``group_order`` names them; an unknown name in ``group_order`` is ignored.
        """
        key_of = keys or (lambda d: d.key)
        read_only = set(read_only_keys)
        self._updating = True
        self.clear()
        self.rows.clear()
        self._items.clear()
        self._groups.clear()
        ordered: list[PropertyRow] = []
        for d in descriptors:
            group, item = label_parts(d, self.t)
            ordered.append(
                PropertyRow(
                    descriptor=d,
                    key=key_of(d),
                    group=group,
                    item=item,
                    unit=self.units.display(d),
                    read_only=key_of(d) in read_only,
                )
            )
        ordered = _disambiguate(_merge_group_spellings(ordered))
        names = [g for g in (group_order or ()) if any(r.group == g for r in ordered)]
        names += [r.group for r in ordered if r.group not in names]
        seen: list[str] = []
        for name in names:
            if name not in seen:
                seen.append(name)
        for name in seen:
            parent = QTreeWidgetItem([name, ""])
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.addTopLevelItem(parent)
            self._groups[name] = parent
        for row in ordered:
            self.rows[row.key] = row
            item = QTreeWidgetItem([row.item, ""])
            item.setData(0, _ROW_ROLE, row.key)
            item.setData(_VALUE_COLUMN, _ROW_ROLE, row.key)
            item.setToolTip(0, self.tooltip(row))
            item.setToolTip(_VALUE_COLUMN, self.tooltip(row))
            flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
            if not row.read_only:
                flags |= Qt.ItemFlag.ItemIsUserCheckable if row.is_bool else Qt.ItemFlag.ItemIsEditable
            item.setFlags(flags)
            if row.read_only:
                item.setForeground(_VALUE_COLUMN, QBrush(QColor(0x80, 0x80, 0x80)))
            self._groups[row.group].addChild(item)
            self._items[row.key] = item
        self.expandAll()
        self._updating = False
        self.refresh()

    def set_source(
        self, getter: Callable[[str], Value], setter: Callable[[str, Value], None]
    ) -> None:
        """Bind the grid to a value store and redisplay."""
        self._get = getter
        self._set = setter
        self.refresh()

    def set_cross_check(self, check: Callable[[], list[str]] | None) -> None:
        """Install a page-level rule run after every edit (the layer page uses ``lp19``)."""
        self._cross = check or list
        self.refresh()

    def set_units(self, units: UnitPolicy) -> None:
        """Change the display units; the stored values are untouched."""
        self.units = units
        for key, row in list(self.rows.items()):
            self.rows[key] = replace(row, unit=units.display(row.descriptor))
        for key, item in self._items.items():
            item.setToolTip(0, self.tooltip(self.rows[key]))
            item.setToolTip(_VALUE_COLUMN, self.tooltip(self.rows[key]))
        self.refresh()

    # ----------------------------------------------------------------- values
    def value(self, key: str) -> Value:
        """Stored value of a row (descriptor unit, not display unit)."""
        return self._get(key)

    def set_value(self, key: str, value: Value) -> list[str]:
        """Write a value through the source, redisplay and return the current problems."""
        row = self.rows.get(key)
        if row is None or row.read_only:
            return self.problems
        d = row.descriptor
        if d.storage == "int" and not isinstance(value, str):
            value = int(round(float(value)))
        elif d.storage == "double" and not isinstance(value, str):
            value = float(value)
        elif d.storage == "string":
            value = str(value)
        self._set(key, value)
        self.refresh()
        self.valueChanged.emit(key, value)
        return self.problems

    def display_text(self, key: str) -> str:
        """The text shown in the value column for a row."""
        row = self.rows[key]
        d = row.descriptor
        value = self._get(key)
        if row.is_bool:
            return ""  # the checkbox is the value (02 §2.4)
        if row.is_enum:
            texts = self.option_texts(row)
            index = int(value)
            return texts[index] if 0 <= index < len(texts) else f"{index} (?)"
        if d.storage == "string":
            return str(value)
        shown = row.unit.to_display(float(value))
        if d.storage == "int" and row.unit.factor == 1.0:
            text = str(int(value))
        else:
            text = f"{shown:.{row.unit.decimals}f}".rstrip("0").rstrip(".") or "0"
        return f"{text} {row.unit.suffix}".strip()

    def option_texts(self, row: PropertyRow) -> list[str]:
        """Combo-box entries of an enum row (02 §2.5); bools use a checkbox instead.

        An override installed with :meth:`set_option_texts` wins over the
        descriptor's ``lang.txt`` option ids - the fibre layer page needs it for
        ``ManuType``, whose descriptor still carries the legacy four-option list
        while the vendor dialog writes eight codes (A6 §3).
        """
        override = self._option_overrides.get(row.key)
        if override is not None:
            return list(override)
        labels = enum_labels(row.descriptor, self.t)
        return labels or [str(i) for i in range(int(row.descriptor.max) + 1)]

    def set_option_texts(self, key: str, texts: Sequence[str] | None) -> None:
        """Override the combo entries of one enum row (``None`` restores the descriptor's).

        Overrides are keyed by row key and survive :meth:`build`, so a page may
        install them once in its constructor.
        """
        if texts is None:
            self._option_overrides.pop(key, None)
        else:
            self._option_overrides[key] = list(texts)
        if key in self._items:
            self.refresh()

    def tooltip(self, row: PropertyRow) -> str:
        """Full label (both languages), key, unit, range and default of one row."""
        d = row.descriptor
        zh = self.t.catalog.get(d.label_id, "zh") or ""
        en = self.t.catalog.get(d.label_id, "en") or ""
        lines = [f"{d.key}  [{d.label_id}]"]
        if en:
            lines.append(en)
        if zh and zh != en:
            lines.append(zh)
        lines.append(f"type {d.type_name}, stored in {d.unit or '-'}")
        if row.unit.suffix and row.unit.suffix != d.unit.replace(" ", ""):
            lines.append(f"shown in {row.unit.suffix}")
        if d.has_range:
            lines.append(f"range {d.min:g} .. {d.max:g} ({d.unit or 'raw'})")
        lines.append(f"default {d.default_text!r}")
        if row.read_only:
            lines.append("read-only: UNVERIFIED in the analysis")
        return "\n".join(lines)

    # ------------------------------------------------------------- validation
    @property
    def problems(self) -> list[str]:
        """Problems found by the last :meth:`refresh` (warnings, never a rejection)."""
        return list(self._problems)

    def validate(self) -> list[str]:
        """Descriptor range/enum problems of every row plus the page's cross-check."""
        out: list[str] = []
        for key, row in self.rows.items():
            out.extend(row.descriptor.validate(self._get(key)))
        out.extend(self._cross())
        return out

    def refresh(self) -> None:
        """Redisplay every row and re-run the validation."""
        was, self._updating = self._updating, True
        for key, item in self._items.items():
            item.setText(_VALUE_COLUMN, self.display_text(key))
            row = self.rows[key]
            if row.is_bool:
                state = Qt.CheckState.Checked if int(self._get(key)) else Qt.CheckState.Unchecked
                item.setCheckState(_VALUE_COLUMN, state)
        self._updating = was
        problems = self.validate()
        bad = {p.split(":", 1)[0] for p in problems}
        for key, item in self._items.items():
            offending = self.rows[key].descriptor.key in bad
            item.setBackground(
                _VALUE_COLUMN,
                QBrush(QColor(0xFF, 0xD5, 0xD5)) if offending else QBrush(),
            )
        if problems != self._problems:
            self._problems = problems
            self.validationChanged.emit(list(problems))

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Write back a checkbox toggle (bool rows, 02 §2.4)."""
        if self._updating or column != _VALUE_COLUMN:
            return
        key = item.data(_VALUE_COLUMN, _ROW_ROLE)
        row = self.rows.get(key) if isinstance(key, str) else None
        if row is None or not row.is_bool or row.read_only:
            return
        new = 1 if item.checkState(_VALUE_COLUMN) == Qt.CheckState.Checked else 0
        # Compare with what the checkbox shows, not the raw value: a stored 2 is drawn
        # checked, and re-drawing it must not rewrite it to 1 (D15).
        if new != (1 if int(self._get(key)) else 0):
            self.set_value(key, new)

    # ---------------------------------------------------------------- helpers
    def row_at(self, index: object) -> PropertyRow | None:
        """Row behind a model index, or ``None`` for a group header."""
        key = index.data(_ROW_ROLE) if hasattr(index, "data") else None  # type: ignore[union-attr]
        return self.rows.get(key) if isinstance(key, str) else None

    def item_for(self, key: str) -> QTreeWidgetItem | None:
        """The tree item of a row (tests and pages use it to read what is displayed)."""
        return self._items.get(key)

    def stored_bounds(self, row: PropertyRow) -> tuple[float, float]:
        """The range an edit of this row may land in, in the descriptor's own unit.

        The descriptor's ``min``/``max`` when it has a usable range, widened to contain
        whatever the row holds **now**.  The widening is what keeps a vendor file intact:
        :meth:`nexcut.core.schema.Descriptor.validate` says in as many words that vendor
        data is not guaranteed to satisfy min/max, and this machine's own
        ``BkManuPara.xml`` proves it (``GRP.EdgeBoardSizeX`` = 5 against a minimum of 50).
        A bound that excluded the stored value would make merely opening an editor on such
        a row rewrite it, because ``QAbstractSpinBox`` clamps what it is shown.
        """
        d = row.descriptor
        lo, hi = (d.min, d.max) if d.has_range else (-_WIDE_RANGE, _WIDE_RANGE)
        if lo > hi:
            lo, hi = hi, lo
        current = self._number(row.key)
        if current is not None:
            lo, hi = min(lo, current), max(hi, current)
        return lo, hi

    def clamp_stored(self, row: PropertyRow, value: Value) -> Value:
        """Pull an edited value back into :meth:`stored_bounds` (never a rejection).

        The delegate applies it after converting the widget's display value back, because
        the conversion itself can leave the range: ``9999 mm/s`` shown as ``599.94 m/min``
        comes back as ``9999.000000000002``, and an ``int`` spin box in a non-SI unit
        truncates a minimum of ``1`` to ``0``.  Neither is a value the operator asked for.
        """
        if isinstance(value, str) or isinstance(value, bool):
            return value
        lo, hi = self.stored_bounds(row)
        out = min(max(float(value), lo), hi)
        return int(round(out)) if row.descriptor.storage == "int" else out

    def _number(self, key: str) -> float | None:
        """The row's current value as a float, or ``None`` when it is not a number."""
        try:
            value = self._get(key)
        except Exception:  # noqa: BLE001 - a foreign source must not break the editor
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value) if math.isfinite(float(value)) else None

    def uses_int_spin(self, row: PropertyRow) -> bool:
        """Whether this row gets a ``QSpinBox`` rather than a ``QDoubleSpinBox``.

        Only an ``int`` row shown in its *own* unit does.  An int shown in a converted
        unit cannot be typed as a whole number - ``FCP.MaxAcc`` = 20 000 mm/s2 is 2.0394 G,
        and an integer spin box can only offer 2, which stores 19 613 - so it gets the
        double spin box and :meth:`set_value` rounds the result back to an int.  This is
        the same rule :meth:`display_text` already uses to decide how to print the value.
        """
        return row.descriptor.storage == "int" and row.unit.factor == 1.0

    def editor_range(self, row: PropertyRow) -> tuple[float, float]:
        """Spin-box range in *display* units, quantised so its end stops are storable.

        Three things beyond converting min/max, each of which was a defect first:

        * the bounds are stepped **inward** to the editor's own resolution and re-checked
          through :meth:`UnitDisplay.to_stored`, so the value the widget clamps a typed
          number to is itself inside :meth:`stored_bounds` (02 §2.4: only the *display* is
          in the operator's unit, the file always keeps SI);
        * an ``int`` row is clipped to the signed 32-bit window a ``QSpinBox`` can hold -
          ten descriptors (``SP.MachineID``, ``SOP.JoystickID1..5``, ...) declare a u32
          range, and ``setRange(0, 4294967295)`` raises ``OverflowError`` out of the
          delegate.  UNVERIFIED port choice: the vendor writes these with ``%d`` of a
          signed int (``SOP.JoystickID2 = -684904636`` in this machine's ``BkHardPara.xml``),
          so the int32 window is the range the file actually uses;
        * the range always contains the current value (:meth:`stored_bounds`).
        """
        lo, hi = self.stored_bounds(row)
        dlo, dhi = row.unit.to_display(lo), row.unit.to_display(hi)
        if dlo > dhi:
            dlo, dhi = dhi, dlo
        if self.uses_int_spin(row):
            ilo = max(math.ceil(dlo - _EPS), INT32_MIN)
            ihi = min(math.floor(dhi + _EPS), INT32_MAX)
            if ilo > ihi:
                ilo = ihi
            # the inverse conversion has to land inside too (a non-SI unit rounds)
            for _ in range(_QUANTISE_STEPS):
                if lo - _EPS <= round(row.unit.to_stored(ilo)) or ilo >= ihi:
                    break
                ilo += 1
            for _ in range(_QUANTISE_STEPS):
                if round(row.unit.to_stored(ihi)) <= hi + _EPS or ihi <= ilo:
                    break
                ihi -= 1
            return float(ilo), float(ihi)
        step = 10.0 ** -row.unit.decimals
        dlo, dhi = _snap(dlo, row.unit.decimals, up=True), _snap(dhi, row.unit.decimals, up=False)
        for _ in range(_QUANTISE_STEPS):
            if row.unit.to_stored(dlo) >= lo or dlo >= dhi:
                break
            dlo = round(dlo + step, row.unit.decimals)
        for _ in range(_QUANTISE_STEPS):
            if row.unit.to_stored(dhi) <= hi or dhi <= dlo:
                break
            dhi = round(dhi - step, row.unit.decimals)
        return (dlo, dhi) if dlo <= dhi else (dhi, dlo)

    def group_names(self) -> list[str]:
        """Group headers in display order."""
        return [self.topLevelItem(i).text(0) for i in range(self.topLevelItemCount())]


def _disambiguate(rows: list[PropertyRow]) -> list[PropertyRow]:
    """Append the attribute name where two rows of a group share an item label.

    ``lang.txt`` binds ``LayerFileName`` to ``pd154`` "Misc.Power Curves", the same
    id as ``PWMCurveNodes`` (02 §7 "the ``FocusGradualTime2``/``pd934`` caption
    bug" family), so a page would otherwise show two identical names.
    """
    seen: dict[tuple[str, str], int] = {}
    for r in rows:
        seen[(r.group, r.item)] = seen.get((r.group, r.item), 0) + 1
    return [
        r if seen[(r.group, r.item)] == 1 else replace(r, item=f"{r.item} ({r.descriptor.attribute})")
        for r in rows
    ]


def _merge_group_spellings(rows: list[PropertyRow]) -> list[PropertyRow]:
    """Fold group names that differ only in case onto their first spelling.

    ``lang.txt`` writes the same page section both ways: ``pd130``/``pd131`` say
    "Cut Gas" while ``A241025_1``/``A241025_2`` say "Cut gas" (02 §2.3 table).
    Port choice: one section per case-insensitive name, keeping the spelling of
    the first descriptor that used it.
    """
    canonical: dict[str, str] = {}
    out: list[PropertyRow] = []
    for r in rows:
        name = canonical.setdefault(r.group.casefold(), r.group)
        out.append(r if name == r.group else replace(r, group=name))
    return out
