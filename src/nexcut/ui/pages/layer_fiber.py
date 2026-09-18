"""Fibre layer parameter page: 164 attributes, real pierce stages, curves (02, A6 §3).

This is the full ``CLayerPropDlg`` / ``CLayerPropPanel`` layer record - the one the
vendor's layer dialog always writes, whichever laser family is selected (A6 §3:
handlers A ``0x54b7e0`` and B ``0x54b9c0`` store into ``g+0x4df0+idx*0x480``, the
**fibre** table, even though the accessor ``0x437910`` switches to ``g+0x7f38``
when ``SP.m_iEnableLaserType != 0``).  The CO2 record next to it
(:mod:`nexcut.ui.pages.layer_co2`) holds 21 attributes and no pierce block at all.

What this page adds over the generic grid:

* **``ManuType`` as the vendor's own enum.** The descriptor still carries the
  legacy four-option list ``pd810..pd813`` with ``max = 3``, while the dialog
  writes **eight** codes and the shipped ``BkLayerPara.xml`` contains a ``4``
  (slot 1, three-stage pierce).  The page therefore edits ``ManuType`` through a
  descriptor corrected to the A6 §3 consolidated table (0..7, option labels from
  :data:`~nexcut.ui.pages.layer_co2.MANU_TYPE_LABELS`), so a vendor value neither
  reads as out of range nor gets clamped on the way back out.  The stored value
  and the bytes written are untouched by the correction.
* **Pierce stages.** ``ManuType`` selects how many of the five stage blocks run
  (``{2:1, 3:2, 4:3, 6:4, 7:5}``, A6 §3).  The stage box shows one block -
  :data:`STAGE_ATTRIBUTES` plus the "lightning pierce" trio of 02 §3.3 - and says
  whether the selected stage is one the current ``ManuType`` executes.  Every
  stage stays **editable** regardless: all five blocks exist in the file, the
  vendor keeps the values of stages it is not running, and a layer is normally
  set up stage by stage before its ``ManuType`` is raised.
* **The power / frequency curves** (``PWMCurveNodes`` / ``FreqCurveNodes``,
  02 §3.4) through :class:`nexcut.ui.curve_editor.CurveEditor`, with their
  ``*CurveSmoothType`` and ``*AdjustWithSpeed`` switches beside them.
* **The ``A250607_*`` laser-family warning.**  ``SP.m_iHardwareModel = 224`` is
  the universal MCC100 whose family is chosen in software; MainApp shows
  ``A250607_2`` / ``A250607_3`` ("currently <fibre> / <CO2> laser ... please
  confirm the settings") at start-up for exactly that model (01 §862, 07 §300).
  When the bound ``manu`` document says the machine is *not* in fibre mode, this
  page says so above the grid - it is still the table the dialog writes, but it is
  not the table this machine cuts with.

``lp19`` (02 §3.2) is the one cross-field rule this build enforces; it lives in
:func:`nexcut.ui.pages.layer_co2.lp19_problems` and is installed here too, because
``CutHeight`` exists only in *this* table.

SAFETY (PORT-PLAN §8): a file editor.  No connection, no motion, no laser.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from nexcut.core.schema import Descriptor, Value
from nexcut.io.params import (
    LAYER_SLOTS,
    ParamDocument,
    ParamFileError,
    TechnologyPreset,
    apply_preset,
    default_document,
    preset_from_layer,
    read_technology,
    write_technology,
)
from nexcut.ui.curve_editor import SMOOTH_TYPE_IDS, CurveEditor
from nexcut.ui.i18n import Translator, get_translator
from nexcut.ui.pages.layer_co2 import (
    MANU_TYPE_LABELS,
    MANU_TYPE_STAGES,
    lp19_problems,
    manu_type_text,
)
from nexcut.ui.property_grid import PropertyGrid, UnitPolicy

__all__ = [
    "FIBER_GROUP_ORDER",
    "LASER_FAMILY_LABELS",
    "MAX_STAGES",
    "STAGE_ATTRIBUTES",
    "STAGE_BOLT_ATTRIBUTES",
    "UNVERIFIED",
    "FiberLayerPage",
    "family_warning",
    "manu_type_descriptor",
    "manu_type_options",
    "stage_attributes",
]

MAX_STAGES = 5
"""Five identical pierce blocks, suffix 0..4 = 一级..五级穿孔 (02 §3.3)."""

STAGE_ATTRIBUTES: tuple[str, ...] = (
    "DrillHeight{k}",
    "DrillFocusPos{k}",
    "EnableFocusGradual{k}",
    "FocusGradualEndPos{k}",
    "FocusGradualTime{k}",
    "DrillPower{k}",
    "DrillFreq{k}",
    "DrillPeakCurrent{k}",
    "DrillGasType{k}",
    "DrillGasPressure{k}",
    "DrillDelay{k}",
    "EnableGradualDrill{k}",
    "GradualTime{k}",
    "BeforeLaserOffDelay{k}",
    "AfterLaserOffDelay{k}",
)
"""The 15 attributes of one pierce stage, in the order of the 02 §3.3 table."""

STAGE_BOLT_ATTRIBUTES: tuple[str, ...] = (
    "BoltDrill_Enable_{n}",
    "BoltDrill_Power_{n}",
    "BoltDrill_Freq_{n}",
)
""""Lightning pierce" (闪电穿孔, ``pd2705-2707``), numbered 1..5 against stages 0..4.

INFERENCE (high, 02 §3.3): the numbering is off by one against the stage suffix and the
attributes are interleaved with the stages in the descriptor table, so ``_1`` belongs to
stage 0.  Nothing in the binary was traced to confirm the pairing."""

FIBER_GROUP_ORDER: tuple[str, ...] = (
    "Cut Basic",
    "Cut Laser",
    "Cutting laser",
    "Cut Gas",
    "Cut Process",
    "Advanced Process",
    "Adv Fix Height",
    "PreDrill Process",
    "First Drill Basic",
    "Second Drill Basic",
    "Third Drill Basic",
    "Smooth Pierce",
    "Bolt Pierce",
    "Clean Residue",
    "Start Work Segment",
    "End work Segment",
    "Lead Process",
    "Graph Shift",
    "ZF Vibration Abatement",
    "Misc",
    "Others",
)
"""Reading order of the ``Group`` halves of 02 §2.3 / 06 §4.4.

Names ``lang.txt`` does not use on this page are skipped by the grid, so both
spellings of a section may safely appear."""

LASER_FAMILY_LABELS: dict[int, tuple[str, str]] = {
    0: ("A241024_0", "Fiber laser"),
    1: ("A241024_1", "CO2 laser"),
    2: ("A241024_2", "Blue laser"),
}
"""``SP.m_iEnableLaserType`` -> the ``lang.txt`` family name (01 §1163)."""

_FAMILY_WARNING_IDS: dict[int, str] = {0: "A250607_2", 1: "A250607_3"}
""""Currently <fibre>/<CO2> laser, please confirm the settings" (07 §300, 01 §862)."""

UNVERIFIED: tuple[str, ...] = (
    "ManuType codes 6 and 7 (4- and 5-stage pierce) exist only in handler 0x54b9c0; "
    "ids 0x4e92/0x4e93 are never created, so no lang.txt label is bound to them and they "
    "can only reach a file by hand-editing (A6 §3)",
    "the descriptor for ManuType carries the legacy 4-option list pd810..pd813 with max 3; "
    "the page edits it against the A6 §3 table (0..7) instead, which is a documented "
    "correction, not an observed descriptor",
    "BoltDrill_*_{n} is paired with stage n-1 by the numbering and the descriptor-table "
    "interleave only (02 §3.3, INFERENCE high)",
    "whether the firmware runs the stages in the order 0,1,2,... is INFERENCE medium "
    "(02 §3.3); the page shows them in that order",
    "the A250607_* warning is shown whenever the bound manu document is not in fibre mode; "
    "the vendor shows it at start-up for hardware model 224 only (01 §862)",
)


def stage_attributes(stage: int) -> tuple[str, ...]:
    """Attribute names of pierce stage ``stage`` (0..4), stage block then bolt trio."""
    if not 0 <= stage < MAX_STAGES:
        raise ValueError(f"stage must be 0..{MAX_STAGES - 1}, got {stage}")
    return tuple(a.format(k=stage) for a in STAGE_ATTRIBUTES) + tuple(
        a.format(n=stage + 1) for a in STAGE_BOLT_ATTRIBUTES
    )


def manu_type_options(translator: Translator | None = None) -> list[str]:
    """Combo entries for ``ManuType`` 0..7 (A6 §3 consolidated table)."""
    t = translator or get_translator()
    return [manu_type_text(code, t) for code in range(len(MANU_TYPE_LABELS))]


def manu_type_descriptor(descriptor: Descriptor) -> Descriptor:
    """``ManuType`` with the A6 §3 range and no legacy option list (module docstring).

    ``enum_ids`` is cleared so :meth:`Descriptor.validate` stops checking the value
    against the four ``pd810..pd813`` labels; the page supplies the eight option
    texts through :meth:`~nexcut.ui.property_grid.PropertyGrid.set_option_texts`.
    """
    return replace(descriptor, min=0.0, max=float(len(MANU_TYPE_LABELS) - 1), enum_ids=None)


def family_warning(manu: object | None, translator: Translator | None = None) -> str:
    """The ``A250607_*`` line to show above a fibre layer page, or ``""``.

    Empty when the bound ``manu`` document selects the fibre family (or when there
    is no such document).  ``SP.m_iEnableLaserType``: 0 fibre, 1 CO2, 2 blue
    (01 §1163; ``plan.items`` reads the same word).
    """
    t = translator or get_translator()
    if manu is None:
        return ""
    try:
        family = int(manu.get("PSoftParam", "SP", "m_iEnableLaserType"))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - anything that is not a manu document says nothing
        return ""
    if family == 0:
        return ""
    key, default = LASER_FAMILY_LABELS.get(family, ("", f"laser type {family}"))
    name = t.tr(key, default) if key else default
    warning = t.tr(
        _FAMILY_WARNING_IDS.get(family, "A250607_3"),
        "Currently <%s>, please confirm the relevant settings before processing",
    )
    text = warning.replace("%s", name) if "%s" in warning else f"{warning} ({name})"
    return (
        f"{text}  -  this machine is not in fibre mode, so these 164 attributes are not the "
        "ones it cuts with; the vendor dialog writes them anyway (A6 §3)."
    )


def _as_layer_document(document: object | None) -> ParamDocument:
    """``None`` gives a descriptor-default ``layer`` document; a wrong kind is refused."""
    if document is None:
        return default_document("layer")
    if not isinstance(document, ParamDocument) or document.kind != "layer":
        kind = getattr(document, "kind", type(document).__name__)
        raise ValueError(f"expected a 'layer' ParamDocument, got {kind!r}")
    return document


class FiberLayerPage(QWidget):
    """Editor for one fibre layer slot: grid, pierce stages and curves (module docstring)."""

    slotChanged = Signal(int)
    """Emitted with the 1-based slot after the slot selector moves."""

    valueChanged = Signal(str, object)
    """``(attribute, stored value)`` after an edit (grid, stage box or curve)."""

    presetLoaded = Signal(str)
    """Path of a technology file that was applied to the current slot."""

    def __init__(
        self,
        *,
        translator: Translator | None = None,
        document: ParamDocument | None = None,
        manu: object | None = None,
        slot: int = 1,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.t = translator or get_translator()
        self.document = _as_layer_document(document)
        self.manu = manu
        self.units = UnitPolicy.from_manu(manu)
        self.slot = min(max(slot, 1), LAYER_SLOTS)
        self.stage = 0
        self._ask_path: Callable[[bool], str | None] | None = None
        self._updating = False

        outer = QVBoxLayout(self)
        outer.addLayout(self._build_header())
        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet("color: #8a5000; font-weight: bold;")
        outer.addWidget(self.warning_label)

        self.tabs = QTabWidget()
        self.grid = PropertyGrid(translator=self.t, units=self.units)
        self.grid.valueChanged.connect(self._on_grid_value)
        self.grid.validationChanged.connect(self._show_problems)
        self.tabs.addTab(self.grid, self.t.tr("lp0", "Layer Parameters"))
        self.tabs.addTab(self._build_stage_tab(), self.t.tr("pd814", "Pierce"))
        self.tabs.addTab(self._build_curve_tab(), self.t.item("pd154", "Power Curves"))
        outer.addWidget(self.tabs, 1)

        self.problem_label = QLabel("")
        self.problem_label.setWordWrap(True)
        self.problem_label.setStyleSheet("color: #a00;")
        outer.addWidget(self.problem_label)

        self.grid.set_source(self._get, self._set)
        self.grid.set_cross_check(lambda: lp19_problems(self.document, self.t))
        self.stage_grid.set_source(self._get, self._set)
        self.grid.set_option_texts("ManuType", manu_type_options(self.t))
        self.stage_grid.set_option_texts("ManuType", manu_type_options(self.t))
        self._rebuild()

    # ------------------------------------------------------------------ build
    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(self.t.tr("gp83", "Layer")))
        self.slot_box = QComboBox()
        for i in range(1, LAYER_SLOTS + 1):
            self.slot_box.addItem(self.t.tr("lp17", "Layer %d").replace("%d", str(i)), i)
        self.slot_box.setCurrentIndex(self.slot - 1)
        self.slot_box.currentIndexChanged.connect(lambda i: self.set_slot(i + 1))
        row.addWidget(self.slot_box)
        row.addWidget(QLabel(self.t.item("pd815", "Process Type")))
        self.manu_type_box = QComboBox()
        for text in manu_type_options(self.t):
            self.manu_type_box.addItem(text)
        self.manu_type_box.currentIndexChanged.connect(self._on_manu_type_box)
        row.addWidget(self.manu_type_box, 1)
        self.preset_label = QLabel("")
        self.preset_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.preset_label, 1)
        self.load_button = QPushButton(self.t.tr("mf9", "Open"))
        self.load_button.setToolTip("Load a Technology/Fiber preset XML into this layer (02 §6.1)")
        self.load_button.clicked.connect(lambda: self._exchange(save=False))
        self.save_button = QPushButton(self.t.tr("mf10", "Save"))
        self.save_button.setToolTip("Write this layer to a technology preset XML (02 §6.1)")
        self.save_button.clicked.connect(lambda: self._exchange(save=True))
        row.addWidget(self.load_button)
        row.addWidget(self.save_button)
        return row

    def _build_stage_tab(self) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        head = QHBoxLayout()
        head.addWidget(QLabel(self.t.tr("pd814", "Pierce")))
        self.stage_box = QComboBox()
        for k in range(MAX_STAGES):
            self.stage_box.addItem(f"{k + 1}", k)
        self.stage_box.currentIndexChanged.connect(lambda i: self.set_stage(i))
        head.addWidget(self.stage_box)
        self.stage_state = QLabel("")
        self.stage_state.setWordWrap(True)
        head.addWidget(self.stage_state, 1)
        lay.addLayout(head)
        self.stage_grid = PropertyGrid(translator=self.t, units=self.units)
        self.stage_grid.valueChanged.connect(self._on_grid_value)
        lay.addWidget(self.stage_grid, 1)
        return box

    def _build_curve_tab(self) -> QWidget:
        box = QWidget()
        lay = QHBoxLayout(box)
        self.curves: dict[str, CurveEditor] = {}
        self.curve_switches: dict[str, tuple[QComboBox, QComboBox]] = {}
        for attribute, enable, smooth, title_id, default in (
            ("PWMCurveNodes", "PowerAdjustWithSpeed", "PowerCurveSmoothType", "lp2", "Power"),
            ("FreqCurveNodes", "FreqAdjustWithSpeed", "FreqCurveSmoothType", "lp3", "Frequency"),
        ):
            column = QGroupBox(self.t.tr(title_id, default))
            inner = QVBoxLayout(column)
            editor = CurveEditor()
            editor.curveChanged.connect(
                lambda text, a=attribute: self._set_from_widget(a, text)
            )
            self.curves[attribute] = editor
            inner.addWidget(editor, 1)
            form = QFormLayout()
            enable_box = QComboBox()
            enable_box.addItems([self.t.tr("pd351", "None"), self.t.tr("pd350", "Enable")])
            enable_box.currentIndexChanged.connect(
                lambda index, a=enable: self._set_from_widget(a, index)
            )
            smooth_box = QComboBox()
            for option in SMOOTH_TYPE_IDS:
                smooth_box.addItem(self.t.tr(option, option))
            smooth_box.currentIndexChanged.connect(
                lambda index, a=smooth: self._set_from_widget(a, index)
            )
            form.addRow(self.t.item("pd124", "Dynamic"), enable_box)
            form.addRow(self.t.item("A241012_4", "Smooth Type"), smooth_box)
            self.curve_switches[attribute] = (enable_box, smooth_box)
            inner.addLayout(form)
            note = QLabel(
                "The preview is the planner's own linear interpolation; the smooth type is "
                "stored but not applied (02 §3.4 gives only the option names)."
            )
            note.setWordWrap(True)
            inner.addWidget(note)
            lay.addWidget(column, 1)
        return box

    # ------------------------------------------------------------------ state
    @property
    def group_name(self) -> str:
        """``PLayerParam<slot>`` of the current slot."""
        return f"PLayerParam{self.slot}"

    def descriptors(self) -> Sequence[Descriptor]:
        """The 164 fibre layer descriptors of the current slot, in file order (02 §2.3)."""
        return tuple(
            manu_type_descriptor(d) if d.attribute == "ManuType" else d
            for d in self.document.layout.group(self.group_name).element("GP").attributes
        )

    def stage_descriptors(self) -> Sequence[Descriptor]:
        """Descriptors of the selected pierce stage, in the 02 §3.3 order."""
        element = self.document.layout.group(self.group_name).element("GP")
        wanted = stage_attributes(self.stage)
        by_name = {d.attribute: d for d in element.attributes}
        return tuple(by_name[a] for a in wanted if a in by_name)

    @property
    def manu_type(self) -> int:
        """``ManuType`` of the current slot (A6 §3 codes 0..7)."""
        return int(self._get("ManuType"))

    @property
    def active_stages(self) -> int:
        """How many pierce stages the current ``ManuType`` runs (A6 §3)."""
        return MANU_TYPE_STAGES.get(self.manu_type, 0)

    def set_document(self, document: ParamDocument, manu: object | None = None) -> None:
        """Bind a ``layer`` document (and optionally the ``manu`` document for units/warning)."""
        self.document = _as_layer_document(document)
        if manu is not None:
            self.manu = manu
            self.units = UnitPolicy.from_manu(manu)
            self.grid.set_units(self.units)
            self.stage_grid.set_units(self.units)
        self._rebuild()

    def set_slot(self, slot: int) -> None:
        """Show layer ``slot`` (1..11)."""
        slot = min(max(slot, 1), LAYER_SLOTS)
        if slot != self.slot:
            self.slot = slot
            if self.slot_box.currentIndex() != slot - 1:
                self.slot_box.setCurrentIndex(slot - 1)
            self._rebuild()
            self.slotChanged.emit(slot)

    def set_stage(self, stage: int) -> None:
        """Show pierce stage ``stage`` (0..4)."""
        stage = min(max(stage, 0), MAX_STAGES - 1)
        self.stage = stage
        if self.stage_box.currentIndex() != stage:
            self.stage_box.setCurrentIndex(stage)
        self._rebuild_stage()

    def set_manu_type(self, code: int) -> None:
        """Write ``ManuType`` (A6 §3 codes 0..7) and refresh the stage view."""
        self._set("ManuType", int(code))
        self._rebuild()
        self.valueChanged.emit("ManuType", int(code))

    # ---------------------------------------------------------------- refresh
    def _rebuild(self) -> None:
        self.grid.build(
            self.descriptors(), keys=lambda d: d.attribute, group_order=FIBER_GROUP_ORDER
        )
        self._rebuild_stage()
        self._refresh_widgets()

    def _rebuild_stage(self) -> None:
        self.stage_grid.build(self.stage_descriptors(), keys=lambda d: d.attribute)
        self._refresh_stage_state()

    def _refresh_stage_state(self) -> None:
        active = self.active_stages
        if self.stage < active:
            text = f"stage {self.stage + 1} of {active} runs with {manu_type_text(self.manu_type, self.t)}"
            style = ""
        else:
            text = (
                f"stage {self.stage + 1} is stored but not executed: "
                f"{manu_type_text(self.manu_type, self.t)}"
            )
            style = "color: #666;"
        self.stage_state.setText(text)
        self.stage_state.setStyleSheet(style)

    def _refresh_widgets(self) -> None:
        was, self._updating = self._updating, True
        try:
            self.manu_type_box.setCurrentIndex(
                min(max(self.manu_type, 0), self.manu_type_box.count() - 1)
            )
            for attribute, editor in self.curves.items():
                editor.set_text(str(self._get(attribute)))
            for attribute, (enable_box, smooth_box) in self.curve_switches.items():
                enable = "PowerAdjustWithSpeed" if attribute == "PWMCurveNodes" else "FreqAdjustWithSpeed"
                smooth = "PowerCurveSmoothType" if attribute == "PWMCurveNodes" else "FreqCurveSmoothType"
                enable_box.setCurrentIndex(1 if int(self._get(enable)) else 0)
                smooth_box.setCurrentIndex(
                    min(max(int(self._get(smooth)), 0), smooth_box.count() - 1)
                )
            name = self._get("LayerFileName")
            self.preset_label.setText(str(name) if name else "")
            self.warning_label.setText(family_warning(self.manu, self.t))
            self.warning_label.setVisible(bool(self.warning_label.text()))
        finally:
            self._updating = was

    # ----------------------------------------------------------------- values
    def _get(self, attribute: str) -> Value:
        return self.document.get(self.group_name, "GP", attribute)

    def _set(self, attribute: str, value: Value) -> None:
        self.document.set(self.group_name, "GP", attribute, value, check=False)

    def _set_from_widget(self, attribute: str, value: Value) -> None:
        """Write a value coming from one of the page's own widgets (not from a grid)."""
        if self._updating:
            return
        self._set(attribute, value)
        self.grid.refresh()
        self.valueChanged.emit(attribute, value)

    def _on_manu_type_box(self, index: int) -> None:
        if self._updating or index < 0 or index == self.manu_type:
            return
        self.set_manu_type(index)

    def _on_grid_value(self, key: str, value: object) -> None:
        if key in ("ManuType", "PWMCurveNodes", "FreqCurveNodes") or key.startswith(
            ("PowerAdjust", "FreqAdjust", "PowerCurve", "FreqCurve")
        ):
            self._refresh_widgets()
        if key == "ManuType":
            self._refresh_stage_state()
        self.grid.refresh()
        self.stage_grid.refresh()
        self.valueChanged.emit(key, value)

    def _show_problems(self, problems: list[str]) -> None:
        self.problem_label.setText("\n".join(problems))

    def problems(self) -> list[str]:
        """Grid, stage-grid and curve problems of the current slot."""
        out = list(self.grid.problems)
        for attribute, editor in self.curves.items():
            out.extend(f"{attribute}: {p}" for p in editor.problems)
        return out

    # ------------------------------------------------- technology library I/O
    def preset(self) -> TechnologyPreset:
        """The current slot as a fibre technology preset (02 §6.1)."""
        return preset_from_layer(self.document, "fiber", self.slot)

    def apply_preset_file(self, path: str | Path) -> TechnologyPreset:
        """Read a technology XML and apply it to the current slot (02 §6.1)."""
        preset = read_technology(path)
        if preset.laser != "fiber":
            raise ParamFileError(f"{path}: this is a {preset.laser} preset, not a fibre one")
        apply_preset(self.document, preset, self.slot)
        self._rebuild()
        self.presetLoaded.emit(str(path))
        return preset

    def save_preset_file(self, path: str | Path) -> None:
        """Write the current slot as a technology XML (byte-identical to a vendor preset)."""
        write_technology(path, self.preset())

    def set_path_chooser(self, chooser: Callable[[bool], str | None] | None) -> None:
        """Install the callable the Load/Save buttons use to pick a path (tests replace it)."""
        self._ask_path = chooser

    def _exchange(self, *, save: bool) -> None:
        path = self._ask_path(save) if self._ask_path is not None else self._file_dialog(save)
        if not path:
            return
        try:
            if save:
                self.save_preset_file(path)
            else:
                self.apply_preset_file(path)
        except (ParamFileError, OSError) as exc:
            self.problem_label.setText(str(exc))

    def _file_dialog(self, save: bool) -> str | None:  # pragma: no cover - needs a desktop
        from PySide6.QtWidgets import QFileDialog

        title = self.t.tr("mf152" if save else "mf150", "Save as" if save else "Open File")
        picker = QFileDialog.getSaveFileName if save else QFileDialog.getOpenFileName
        path, _ = picker(self, title, "", "Technology (*.xml)")
        return path or None
