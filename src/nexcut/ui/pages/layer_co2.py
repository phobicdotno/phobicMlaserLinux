"""CO2 layer parameter page (``CO2LayerPropDlg`` / ``CLayerPropPanel``; 02, A6 §3).

The page edits one slot of a ``layer`` parameter document
(:class:`nexcut.io.params.ParamDocument`, kind ``layer``) and can exchange that
slot with the vendor technology library (``\\Technology\\CO2\\*.xml``, 02 §6.1)
through :mod:`nexcut.io.params`.

What is editable and what is not:

* **editable** - the 21 attributes of the ``CO2LayerParam`` descriptor table
  (02 §2.3): cut speed, PWM duty and frequency, gas type and pressure, the
  laser-on delay, slow start, the power/frequency curve strings and their smooth
  types, and the housekeeping strings.  These are the attributes the vendor
  actually stores for a CO2 layer and that the port round-trips byte for byte.
* **read-only** - the process type / pierce stages and the lead-line and cool-point
  settings.  ``CO2LayerParam`` has no ``ManuType``, no ``Drill*`` block and no
  ``LeadLineParam_Enable``: A6 §3 shows the vendor's layer dialog always writes
  ``ManuType`` into the *fibre* table (``g+0x4df0+idx*0x480``) even when the CO2
  accessor would have switched to ``g+0x7f38``, and whether the CO2 process then
  consumes it is UNVERIFIED.  The page therefore *shows* those values, read from
  the fibre slot of the same document and from the loaded job's contour crafts
  (03 §6.1.1), and refuses to edit them.

``lp19`` is the only cross-field rule this build enforces (02 §3.2 VERIFIER: the
check at ``0x54cfd5-0x54d065`` loops layer indices 0..9 and compares each
``CutHeight`` with the film layer's; ``lp16``-``lp18`` exist in ``lang.txt`` but
are not referenced by ``MainApp.exe``).  :func:`lp19_problems` implements it and
the page installs it as the grid's cross-check.

SAFETY (PORT-PLAN §8): this page reads and writes parameter files.  It has no
machine connection, cannot move an axis and cannot arm a laser.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
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
from nexcut.model.graph import ChfDocument, Contour, Crafts, Group, Text
from nexcut.ui.i18n import Translator, get_translator
from nexcut.ui.property_grid import PropertyGrid, UnitPolicy

__all__ = [
    "CO2_GROUP_ORDER",
    "FILM_SLOT",
    "MANU_TYPE_LABELS",
    "MANU_TYPE_STAGES",
    "UNVERIFIED",
    "Co2LayerPage",
    "crafts_summary",
    "lp19_problems",
    "manu_type_text",
]

FILM_SLOT = 11
"""Layer slot that carries ``WithFilm`` (02 §4: only slot 11; the ``lp19`` reference)."""

MANU_TYPE_STAGES: dict[int, int] = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 0, 6: 4, 7: 5}
"""``ManuType`` -> pierce stages (A6 §3 consolidated table)."""

MANU_TYPE_LABELS: dict[int, tuple[str, str]] = {
    0: ("newLang50", "Standard cutting"),
    1: ("pd811", "Fix Height Cut"),
    2: ("newLang51", "First level perforation"),
    3: ("newLang52", "Secondary perforation"),
    4: ("newLang53", "Three-level perforation"),
    5: ("pd811-1", "Adv Fix Height Cut"),
    6: ("", "4-stage pierce (no UI item in this build)"),
    7: ("", "5-stage pierce (no UI item in this build)"),
}
"""``ManuType`` -> the ``lang.txt`` id the vendor dialog binds to it (A6 §3 handlers A/B).

Codes 6 and 7 exist only in handler ``0x54b9c0``; ids ``0x4e92``/``0x4e93`` are
never created, so no label is bound to them - the English default says so.
"""

CO2_GROUP_ORDER: tuple[str, ...] = (
    "Cut Basic",
    "Cut Laser",
    "Cutting laser",
    "Cut Gas",
    "Adv Fix Height",
    "Adv Parameters",
    "Misc",
    "Others",
)
"""Section order of the CO2 page: the ``Group`` halves of 02 §2.3 in reading order.

Names that ``lang.txt`` does not use on this page are skipped by the grid, so the
list may safely carry both spellings of a section.
"""

UNVERIFIED: tuple[str, ...] = (
    "the CO2 layer table has no ManuType/Drill*/LeadLineParam_Enable; the vendor "
    "dialog writes ManuType into the fibre table even in CO2 mode (A6 §3) and it is "
    "not traced whether the CO2 process reads it - shown read-only here",
    "MANU_TYPE_LABELS 6/7 have no label bound in this build (A6 §3: ids 0x4e92/0x4e93 "
    "are never created)",
    "the lead-line and cool-point summary is read from the loaded job's contour crafts "
    "(03 §6.1.1); which of them a CO2 layer overrides is not traced",
    "lp19 is applied to the fibre layer slots, because CutHeight exists only there "
    "(02 §3.2 gives the loop over layer indices 0..9 against the film layer)",
)


def manu_type_text(code: int, translator: Translator) -> str:
    """Human label of a ``ManuType`` code plus its pierce-stage count (A6 §3)."""
    key, default = MANU_TYPE_LABELS.get(code, ("", f"unknown code {code}"))
    label = translator.tr(key, default) if key else default
    stages = MANU_TYPE_STAGES.get(code, 0)
    return f"{label} ({code}, {stages} pierce stage{'' if stages == 1 else 's'})"


def lp19_problems(doc: ParamDocument, translator: Translator | None = None) -> list[str]:
    """``lp19``: no layer's ``CutHeight`` may exceed the film layer's (02 §3.2).

    EVIDENCE: the check at ``0x54cfd5-0x54d065`` loops layer indices 0..9 and
    ``fcomp``s ``[base+0x4e08 + i*0x480]`` against the film layer at
    ``[base+0x7b08]``.  Those offsets are the fibre layer table, which is where
    ``CutHeight`` lives; a ``layer`` document without a fibre table yields no
    problems.  The message text is the ``lp19`` record itself.
    """
    t = translator or get_translator()
    text = t.tr(
        "lp19",
        "Layer %d 【Cut Height】 is larger than 【With Film Cut Height】, please reset",
    )
    try:
        film = float(doc.get(f"PLayerParam{FILM_SLOT}", "GP", "CutHeight"))
    except Exception:  # noqa: BLE001 - a document without the fibre table has no rule
        return []
    problems: list[str] = []
    for slot in range(1, FILM_SLOT):  # indices 0..9 of the vendor loop
        try:
            height = float(doc.get(f"PLayerParam{slot}", "GP", "CutHeight"))
        except Exception:  # noqa: BLE001
            continue
        if height > film:
            problems.append(text.replace("%d", str(slot)))
    return problems


def crafts_summary(document: ChfDocument | None, translator: Translator | None = None) -> dict[str, str]:
    """Lead-line and cool-point summary of a loaded job (03 §6.1.1), as display strings.

    Read-only: the ``.chf`` crafts are per contour, and which of them a CO2 layer
    parameter would override is not traced (UNVERIFIED).
    """
    t = translator or get_translator()
    none = t.tr("pd351", "None")
    out = {
        "lead": none,
        "cool": none,
        "contours": "0",
    }
    if document is None:
        return out
    crafts: list[Crafts] = []
    for graph in document.graphs:
        crafts.extend(_contour_crafts(graph))
    out["contours"] = str(len(crafts))
    if not crafts:
        return out
    leads = {
        (c.lead_line.type, round(c.lead_line.angle_deg, 3), round(c.lead_line.length, 3))
        for c in crafts
    }
    if len(leads) == 1:
        kind, angle, length = next(iter(leads))
        out["lead"] = f"type {kind}, {angle:g}°, {length:g} mm"
    else:
        out["lead"] = f"{len(leads)} different settings"
    counts = {len(c.cool_pos or ()) for c in crafts}
    out["cool"] = f"{min(counts)}..{max(counts)} points" if len(counts) > 1 else f"{counts.pop()} points"
    return out


def _contour_crafts(graph: object) -> list[Crafts]:
    """Crafts of every contour under a graph (``Group``/``ContourEx``/``Scan``/``Text``, 03 §6)."""
    if isinstance(graph, Contour):
        return [graph.crafts]
    if isinstance(graph, Group):  # ContourEx and Scan are Groups too (03 §6.4, §6.5)
        out: list[Crafts] = []
        for child in graph.children:
            out.extend(_contour_crafts(child))
        return out
    if isinstance(graph, Text):
        return _contour_crafts(graph.outline) if graph.outline is not None else []
    return []


def _as_layer_document(document: object | None) -> ParamDocument:
    """``None`` gives a descriptor-default ``layer`` document; a wrong kind is refused."""
    if document is None:
        return default_document("layer")
    if not isinstance(document, ParamDocument) or document.kind != "layer":
        kind = getattr(document, "kind", type(document).__name__)
        raise ValueError(f"expected a 'layer' ParamDocument, got {kind!r}")
    return document


class Co2LayerPage(QWidget):
    """Editor for one CO2 layer slot, with the technology-library exchange (module docstring)."""

    slotChanged = Signal(int)
    """Emitted with the 1-based slot after the slot selector moves."""

    valueChanged = Signal(str, object)
    """``(attribute, stored value)`` after an edit (relayed from the grid)."""

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
        self.units = UnitPolicy.from_manu(manu)
        self.slot = min(max(slot, 1), LAYER_SLOTS)
        self.job: ChfDocument | None = None
        self._ask_path: Callable[[bool], str | None] | None = None

        outer = QVBoxLayout(self)
        outer.addLayout(self._build_header())
        self.grid = PropertyGrid(translator=self.t, units=self.units)
        self.grid.valueChanged.connect(self.valueChanged)
        self.grid.validationChanged.connect(self._show_problems)
        outer.addWidget(self.grid, 1)
        outer.addWidget(self._build_info())
        self.problem_label = QLabel("")
        self.problem_label.setWordWrap(True)
        self.problem_label.setStyleSheet("color: #a00;")
        outer.addWidget(self.problem_label)

        self.grid.set_source(self._get, self._set)
        self.grid.set_cross_check(lambda: lp19_problems(self.document, self.t))
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
        self.preset_label = QLabel("")
        self.preset_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.preset_label, 1)
        self.load_button = QPushButton(self.t.tr("mf9", "Open"))
        self.load_button.setToolTip("Load a Technology/CO2 preset XML into this layer (02 §6.1)")
        self.load_button.clicked.connect(lambda: self._exchange(save=False))
        self.save_button = QPushButton(self.t.tr("mf10", "Save"))
        self.save_button.setToolTip("Write this layer to a technology preset XML (02 §6.1)")
        self.save_button.clicked.connect(lambda: self._exchange(save=True))
        row.addWidget(self.load_button)
        row.addWidget(self.save_button)
        return row

    def _build_info(self) -> QGroupBox:
        box = QGroupBox(self.t.tr("pd815", "General Process.Process Type").split(".")[0])
        form = QFormLayout(box)
        self.info: dict[str, QLabel] = {}
        rows = (
            ("manu_type", self.t.item("pd815", "Process Type")),
            ("lead", self.t.tr("newLang40", "Lead Process")),
            ("cool", self.t.item("pd946", "Cool Down Gas")),
            ("contours", self.t.tr("gp76", "ContourNum")),
        )
        for key, text in rows:
            label = QLabel("-")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.info[key] = label
            form.addRow(text, label)
        note = QLabel(
            "Read-only: the CO2 layer table has no ManuType/pierce/lead-line attributes; "
            "these come from the fibre slot and the job's contour crafts (A6 §3, 03 §6.1.1)."
        )
        note.setWordWrap(True)
        form.addRow(note)
        box.setToolTip("\n".join(UNVERIFIED))
        return box

    # ------------------------------------------------------------------ state
    def descriptors(self) -> Sequence[Descriptor]:
        """The CO2 layer descriptors of the current slot, in file order (02 §2.3)."""
        return self.document.layout.group(self.group_name).element("GP").attributes

    @property
    def group_name(self) -> str:
        """``PCO2LayerParam<slot>`` of the current slot."""
        return f"PCO2LayerParam{self.slot}"

    def set_document(self, document: ParamDocument, manu: object | None = None) -> None:
        """Bind a ``layer`` document (and optionally the ``manu`` document for the units)."""
        self.document = _as_layer_document(document)
        if manu is not None:
            self.units = UnitPolicy.from_manu(manu)
            self.grid.set_units(self.units)
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

    def set_job(self, document: ChfDocument | None) -> None:
        """Show the lead-line / cool-point summary of a loaded job (read-only)."""
        self.job = document
        self._refresh_info()

    def _rebuild(self) -> None:
        self.grid.build(self.descriptors(), keys=lambda d: d.attribute, group_order=CO2_GROUP_ORDER)
        self._refresh_info()

    def _get(self, attribute: str) -> Value:
        return self.document.get(self.group_name, "GP", attribute)

    def _set(self, attribute: str, value: Value) -> None:
        self.document.set(self.group_name, "GP", attribute, value, check=False)

    def _refresh_info(self) -> None:
        t = self.t
        fibre = f"PLayerParam{self.slot}"
        try:
            code = int(self.document.get(fibre, "GP", "ManuType"))
            self.info["manu_type"].setText(manu_type_text(code, t))
        except Exception:  # noqa: BLE001 - a document without the fibre table shows nothing
            self.info["manu_type"].setText("-")
        summary = crafts_summary(self.job, t)
        try:
            enabled = int(self.document.get(fibre, "GP", "LeadLineParam_Enable"))
            prefix = f"{'on' if enabled else 'off'} - "
        except Exception:  # noqa: BLE001
            prefix = ""
        self.info["lead"].setText(prefix + summary["lead"])
        self.info["cool"].setText(summary["cool"])
        self.info["contours"].setText(summary["contours"])
        name = self._get("LayerFileName")
        self.preset_label.setText(str(name) if name else "")

    def _show_problems(self, problems: list[str]) -> None:
        self.problem_label.setText("\n".join(problems))

    # ------------------------------------------------- technology library I/O
    def preset(self) -> TechnologyPreset:
        """The current slot as a CO2 technology preset (02 §6.1 "export to library")."""
        return preset_from_layer(self.document, "co2", self.slot)

    def apply_preset_file(self, path: str | Path) -> TechnologyPreset:
        """Read a technology XML and apply it to the current slot (02 §6.1)."""
        preset = read_technology(path)
        if preset.laser != "co2":
            raise ParamFileError(f"{path}: this is a {preset.laser} preset, not a CO2 one")
        apply_preset(self.document, preset, self.slot)
        self.grid.refresh()
        self._refresh_info()
        self.presetLoaded.emit(str(path))
        return preset

    def save_preset_file(self, path: str | Path) -> None:
        """Write the current slot as a technology XML (byte-identical to a vendor preset)."""
        write_technology(path, self.preset())

    def set_path_chooser(self, chooser: Callable[[bool], str | None] | None) -> None:
        """Install the callable the Load/Save buttons use to pick a path.

        The default is a ``QFileDialog``; tests replace it so nothing modal opens.
        ``chooser(save)`` returns a path or ``None``.
        """
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
        if save:
            path, _ = QFileDialog.getSaveFileName(self, title, "", "Technology (*.xml)")
        else:
            path, _ = QFileDialog.getOpenFileName(self, title, "", "Technology (*.xml)")
        return path or None
