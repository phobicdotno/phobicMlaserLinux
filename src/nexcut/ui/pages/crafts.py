"""Per-contour crafts editor: lead-in/out, cool points, micro joints (03 §6.1.1, A6 §2).

A ``.chf`` contour carries a ``<Crafts>`` block that the port keeps as a typed but
**opaque** struct (:class:`nexcut.model.graph.Crafts`): several of its scalars are
still INFERENCE, and the rule of 03 §14 is that whatever they mean, a file read and
written again must keep them.  This editor is built on that rule - it writes only
the fields the operator actually changed, and shows the rest read-only, so every
unknown scalar (and every ``legacy_reserved`` line) survives a round trip.

What is editable, and what it means (A6 §2.3-2.5, §4.2):

* **lead line** ``<GuideCurve Para>``: ``type`` 0 None / 1 Line / 2 Arc / 3 Line+Arc
  (option table ``0xa60578``, builders ``0x795aa7..0x795ae3``), ``angle_deg``,
  ``length`` mm, ``arc_radius`` mm and the orientation ``flag``.  ``arc_radius`` is
  ``None`` in version-1 files, where the line does not exist; the field is then
  disabled rather than invented.
* **cool points** ``<coolPos Para>``: path **ratios 0..1** measured from the
  contour's geometric start (EVIDENCE ``0x10060730`` compares each entry with
  ``[+0x170]`` and subtracts it; the split-and-stop uses ``s = r*totalLength``).
* **micro joints** ``<PWM Control>``: the laser-off segments along the path, stored
  as ``(centre ratio, segment length mm)`` pairs (verifier V9: ``a_i`` = centre
  ratio, ``b_i`` = length in mm).  The ``pwm_close_pos_ratios`` list is
  **derived** - ``close[2i] = a_i - 0.5*b_i/L``, ``close[2i+1] = a_i + 0.5*b_i/L``
  with ``L`` the contour length - and A6 §4.2 says to recompute it rather than
  trust it.  This editor recomputes it *only after an edit*: a file that is merely
  opened keeps the list its writer produced, byte for byte.

Read-only, and preserved: ``compensate_type`` / ``compensate_width`` (kerf,
INFERENCE medium/high), ``pwm_enable`` (``[+0x104]`` is a *mode* int - CADModule
writes 2 and 3 as well as 1, verifier V10), ``double170`` (lead start position
ratio) and ``double188`` (over-cut mm).  Editing them needs evidence this build
does not have.

SAFETY (PORT-PLAN §8): edits an in-memory document.  No connection, no motion, no
laser.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nexcut.model.graph import ChfDocument, Contour, Crafts, Graph, Group, Text

__all__ = [
    "LEAD_TYPE_IDS",
    "UNVERIFIED",
    "ContourEntry",
    "CraftsEditor",
    "close_ratios",
    "iter_contours",
]

LEAD_TYPE_IDS: tuple[str, ...] = ("pd297", "pd298", "pd299", "pd300")
"""``GRP.GuideLineType`` options 0..3: None / Line / Arc / Line+Arc (A6 §2.3)."""

UNVERIFIED: tuple[str, ...] = (
    "cool_pos is a ratio 0..1 of the contour length (A6 §2.4 EVIDENCE); the unit of the "
    "list as written by other producers is not otherwise constrained",
    "pwm_enable ([+0x104]) is a mode int, not a boolean (verifier V10: CADModule writes "
    "2 and 3); the editor shows it and never changes it",
    "compensate_type -1/2/3 and compensate_width mm are INFERENCE (03 §6.1.1, A6 §2.2); "
    "read-only here",
    "lead.flag is 'orientation XOR positive' (03); shown as a checkbox with no further "
    "meaning attached",
)


def iter_contours(graph: Graph | ChfDocument | None) -> Iterator[Contour]:
    """Yield every :class:`~nexcut.model.graph.Contour` under a document or a graph.

    ``ContourEx`` and ``Scan`` are ``Group`` subclasses (03 §6.4/§6.5) and a
    ``Scan`` also owns generated fly-cut paths, which carry crafts of their own.
    """
    if graph is None:
        return
    if isinstance(graph, ChfDocument):
        for g in graph.graphs:
            yield from iter_contours(g)
        return
    if isinstance(graph, Contour):
        yield graph
        return
    if isinstance(graph, Text):
        if graph.outline is not None:
            yield from iter_contours(graph.outline)
        return
    if isinstance(graph, Group):
        for child in graph.children:
            yield from iter_contours(child)
        for path in getattr(graph, "paths", ()):  # Scan (03 §6.4)
            yield from iter_contours(path)


def close_ratios(nodes: Sequence[tuple[float, float]], length: float) -> list[float]:
    """Derive ``pwm_close_pos_ratios`` from the micro-joint nodes (A6 §4.2).

    ``close[2i] = a_i - 0.5*b_i/L``, ``close[2i+1] = a_i + 0.5*b_i/L``
    (``0x100654f4-0x1006553d``, ``L = [+0x48]``).  A contour with no length has no
    derivable list, so the caller keeps whatever was read.
    """
    if length <= 0.0:
        raise ValueError("a contour of zero length has no close ratios")
    out: list[float] = []
    for centre, segment in nodes:
        half = 0.5 * segment / length
        out.append(centre - half)
        out.append(centre + half)
    return out


@dataclass(frozen=True, slots=True)
class ContourEntry:
    """One selectable contour: its position in the document and the object itself."""

    index: int
    contour: Contour

    @property
    def label(self) -> str:
        """``"#3  layer 1  12.500 mm"`` - index, layer and cached path length (03 §6.1)."""
        c = self.contour
        return f"#{self.index}  layer {c.layer}  {c.length:.3f} mm"


class CraftsEditor(QWidget):
    """Editor for one contour's ``<Crafts>`` block (module docstring)."""

    craftsChanged = Signal(int)
    """Index of the contour whose crafts were changed."""

    contourChanged = Signal(int)
    """Index of the contour now shown."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entries: list[ContourEntry] = []
        self.index = -1
        self._updating = False

        outer = QVBoxLayout(self)
        head = QHBoxLayout()
        head.addWidget(QLabel("Contour"))
        self.contour_box = QComboBox()
        self.contour_box.currentIndexChanged.connect(self._on_contour_index)
        head.addWidget(self.contour_box, 1)
        outer.addLayout(head)

        outer.addWidget(self._build_lead_box())
        tables = QHBoxLayout()
        tables.addWidget(self._build_cool_box(), 1)
        tables.addWidget(self._build_joint_box(), 1)
        outer.addLayout(tables)
        outer.addWidget(self._build_opaque_box())
        outer.addStretch(1)
        self.set_document(None)

    # ------------------------------------------------------------------ build
    def _build_lead_box(self) -> QGroupBox:
        box = QGroupBox("Lead line (GuideCurve Para)")
        form = QFormLayout(box)
        self.lead_type = QComboBox()
        for text in ("0 None", "1 Line", "2 Arc", "3 Line+Arc"):
            self.lead_type.addItem(text)
        self.lead_type.currentIndexChanged.connect(lambda v: self._edit_lead("type", int(v)))
        form.addRow("Type", self.lead_type)
        self.lead_angle = self._spin(-360.0, 360.0, 3, "°")
        self.lead_angle.valueChanged.connect(lambda v: self._edit_lead("angle_deg", float(v)))
        form.addRow("Angle", self.lead_angle)
        self.lead_length = self._spin(0.0, 10000.0, 3, " mm")
        self.lead_length.valueChanged.connect(lambda v: self._edit_lead("length", float(v)))
        form.addRow("Length", self.lead_length)
        self.lead_radius = self._spin(0.0, 10000.0, 3, " mm")
        self.lead_radius.valueChanged.connect(lambda v: self._edit_lead("arc_radius", float(v)))
        form.addRow("Arc radius", self.lead_radius)
        self.lead_flag = QCheckBox("flag (orientation XOR positive, 03)")
        self.lead_flag.toggled.connect(lambda v: self._edit_lead("flag", bool(v)))
        form.addRow("", self.lead_flag)
        return box

    def _build_cool_box(self) -> QGroupBox:
        box = QGroupBox("Cool points (ratio of the contour, A6 §2.4)")
        lay = QVBoxLayout(box)
        self.cool_table = self._table(["Position (0..1)"])
        self.cool_table.itemChanged.connect(self._on_cool_item)
        lay.addWidget(self.cool_table, 1)
        lay.addLayout(self._table_buttons(self.add_cool_point, self.remove_cool_point))
        self.cool_note = QLabel("")
        self.cool_note.setWordWrap(True)
        lay.addWidget(self.cool_note)
        return box

    def _build_joint_box(self) -> QGroupBox:
        box = QGroupBox("Micro joints / PWM off segments (A6 §2.5)")
        lay = QVBoxLayout(box)
        self.joint_table = self._table(["Centre (0..1)", "Length (mm)"])
        self.joint_table.itemChanged.connect(self._on_joint_item)
        lay.addWidget(self.joint_table, 1)
        lay.addLayout(self._table_buttons(self.add_micro_joint, self.remove_micro_joint))
        self.joint_note = QLabel("")
        self.joint_note.setWordWrap(True)
        lay.addWidget(self.joint_note)
        return box

    def _build_opaque_box(self) -> QGroupBox:
        box = QGroupBox("Preserved unchanged (03 §14)")
        form = QFormLayout(box)
        self.opaque: dict[str, QLabel] = {}
        for key, text in (
            ("compensate_type", "Kerf compensation type"),
            ("compensate_width", "Kerf compensation width"),
            ("pwm_enable", "PWM mode [+0x104]"),
            ("double170", "Lead start ratio [+0x170]"),
            ("double188", "Over-cut [+0x188]"),
            ("close", "Derived close ratios [+0x12c]"),
            ("reserved", "Legacy reserved lines"),
        ):
            label = QLabel("-")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.opaque[key] = label
            form.addRow(text, label)
        box.setToolTip("\n".join(UNVERIFIED))
        return box

    @staticmethod
    def _spin(low: float, high: float, decimals: int, suffix: str) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(low, high)
        box.setDecimals(decimals)
        box.setSuffix(suffix)
        return box

    @staticmethod
    def _table(headers: Sequence[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(list(headers))
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        return table

    @staticmethod
    def _table_buttons(add: object, remove: object) -> QHBoxLayout:
        row = QHBoxLayout()
        plus = QPushButton("+")
        plus.clicked.connect(add)  # type: ignore[arg-type]
        minus = QPushButton("-")
        minus.clicked.connect(remove)  # type: ignore[arg-type]
        row.addWidget(plus)
        row.addWidget(minus)
        row.addStretch(1)
        return row

    # ------------------------------------------------------------------ state
    @property
    def contour(self) -> Contour | None:
        """The contour currently shown."""
        if 0 <= self.index < len(self.entries):
            return self.entries[self.index].contour
        return None

    @property
    def crafts(self) -> Crafts | None:
        """The crafts block currently shown."""
        contour = self.contour
        return contour.crafts if contour is not None else None

    def set_document(self, document: ChfDocument | None) -> None:
        """Show the contours of ``document`` (``None`` empties the editor)."""
        self.entries = [ContourEntry(i, c) for i, c in enumerate(iter_contours(document))]
        was, self._updating = self._updating, True
        self.contour_box.clear()
        for entry in self.entries:
            self.contour_box.addItem(entry.label, entry.index)
        self._updating = was
        self.index = 0 if self.entries else -1
        if self.entries:
            self.contour_box.setCurrentIndex(0)
        self._refresh()

    def set_contour(self, index: int) -> None:
        """Show contour ``index`` of the document."""
        if not self.entries:
            return
        index = min(max(index, 0), len(self.entries) - 1)
        if index != self.index:
            self.index = index
            if self.contour_box.currentIndex() != index:
                self.contour_box.setCurrentIndex(index)
            self._refresh()
            self.contourChanged.emit(index)

    def _on_contour_index(self, index: int) -> None:
        if self._updating or index < 0:
            return
        self.set_contour(index)

    # ------------------------------------------------------------------ edits
    def _edit_lead(self, field: str, value: object) -> None:
        crafts = self.crafts
        if self._updating or crafts is None:
            return
        if field == "arc_radius" and crafts.lead_line.arc_radius is None:
            return  # version-1 file: the line does not exist (03 §9)
        if getattr(crafts.lead_line, field) == value:
            return
        setattr(crafts.lead_line, field, value)
        self._changed()

    def add_cool_point(self) -> None:
        """Append a cool point at the midpoint of the remaining path (A6 §2.4)."""
        crafts = self.crafts
        if crafts is None:
            return
        if crafts.cool_pos is None:
            return  # version <= 2: the block does not exist (03 §9)
        last = crafts.cool_pos[-1] if crafts.cool_pos else 0.0
        crafts.cool_pos.append(min(1.0, (last + 1.0) / 2.0))
        self._changed()

    def remove_cool_point(self) -> None:
        """Delete the selected cool point."""
        crafts = self.crafts
        if crafts is None or not crafts.cool_pos:
            return
        row = self.cool_table.currentRow()
        if 0 <= row < len(crafts.cool_pos):
            del crafts.cool_pos[row]
            self._changed()

    def add_micro_joint(self) -> None:
        """Append a micro joint of 1 mm at the middle of the path (A6 §2.5)."""
        crafts = self.crafts
        if crafts is None:
            return
        crafts.pwm_nodes.append((0.5, 1.0))
        self._changed()

    def remove_micro_joint(self) -> None:
        """Delete the selected micro joint (``0x1006a41e``: a click inside one removes it)."""
        crafts = self.crafts
        if crafts is None or not crafts.pwm_nodes:
            return
        row = self.joint_table.currentRow()
        if 0 <= row < len(crafts.pwm_nodes):
            del crafts.pwm_nodes[row]
            self._changed()

    def _on_cool_item(self, item: QTableWidgetItem) -> None:
        crafts = self.crafts
        if self._updating or crafts is None or not crafts.cool_pos:
            return
        row = item.row()
        if not 0 <= row < len(crafts.cool_pos):
            return
        try:
            value = float(item.text())
        except ValueError:
            self._refresh()
            return
        value = min(max(value, 0.0), 1.0)
        if value != crafts.cool_pos[row]:
            crafts.cool_pos[row] = value
            self._changed()
        else:
            self._refresh()

    def _on_joint_item(self, item: QTableWidgetItem) -> None:
        crafts = self.crafts
        if self._updating or crafts is None:
            return
        row, column = item.row(), item.column()
        if not 0 <= row < len(crafts.pwm_nodes):
            return
        try:
            value = float(item.text())
        except ValueError:
            self._refresh()
            return
        centre, segment = crafts.pwm_nodes[row]
        new = (min(max(value, 0.0), 1.0), segment) if column == 0 else (centre, max(value, 0.0))
        if new != crafts.pwm_nodes[row]:
            crafts.pwm_nodes[row] = new
            self._changed()
        else:
            self._refresh()

    def _changed(self) -> None:
        """Re-derive what an edit invalidates, redisplay and announce the change."""
        self.recompute_close_ratios()
        self._refresh()
        self.craftsChanged.emit(self.index)

    def recompute_close_ratios(self) -> bool:
        """Re-derive ``pwm_close_pos_ratios`` from the nodes (A6 §4.2); False if not possible."""
        contour, crafts = self.contour, self.crafts
        if contour is None or crafts is None:
            return False
        try:
            crafts.pwm_close_pos_ratios = close_ratios(crafts.pwm_nodes, contour.length)
        except ValueError:
            return False  # zero-length contour: keep what the file had
        return True

    # ---------------------------------------------------------------- display
    def _refresh(self) -> None:
        was, self._updating = self._updating, True
        try:
            self._refresh_widgets()
        finally:
            self._updating = was

    def _refresh_widgets(self) -> None:
        crafts = self.crafts
        contour = self.contour
        enabled = crafts is not None
        for widget in (
            self.lead_type,
            self.lead_angle,
            self.lead_length,
            self.lead_flag,
            self.cool_table,
            self.joint_table,
        ):
            widget.setEnabled(enabled)
        if crafts is None or contour is None:
            self.cool_table.setRowCount(0)
            self.joint_table.setRowCount(0)
            for label in self.opaque.values():
                label.setText("-")
            self.lead_radius.setEnabled(False)
            self.cool_note.setText("")
            self.joint_note.setText("")
            return

        lead = crafts.lead_line
        self.lead_type.setCurrentIndex(lead.type if 0 <= lead.type < self.lead_type.count() else 0)
        self.lead_angle.setValue(lead.angle_deg)
        self.lead_length.setValue(lead.length)
        self.lead_radius.setEnabled(lead.arc_radius is not None)
        self.lead_radius.setValue(lead.arc_radius if lead.arc_radius is not None else 0.0)
        self.lead_flag.setChecked(bool(lead.flag))

        cool = crafts.cool_pos
        self.cool_table.setRowCount(0 if cool is None else len(cool))
        for row, value in enumerate(cool or ()):
            self._set_cell(self.cool_table, row, 0, value)
        self.cool_note.setText(
            "version <= 2: this file has no <coolPos Para> block (03 §9)"
            if cool is None
            else f"{len(cool)} point(s), ratios of {contour.length:.3f} mm"
        )

        self.joint_table.setRowCount(len(crafts.pwm_nodes))
        for row, (centre, segment) in enumerate(crafts.pwm_nodes):
            self._set_cell(self.joint_table, row, 0, centre)
            self._set_cell(self.joint_table, row, 1, segment)
        self.joint_note.setText(
            f"{len(crafts.pwm_nodes)} joint(s); the close ratios below are derived from them"
        )

        self.opaque["compensate_type"].setText(str(crafts.compensate_type))
        self.opaque["compensate_width"].setText(f"{crafts.compensate_width:g} mm")
        self.opaque["pwm_enable"].setText(str(crafts.pwm_enable))
        self.opaque["double170"].setText(f"{crafts.double170:g}")
        self.opaque["double188"].setText(f"{crafts.double188:g}")
        self.opaque["close"].setText(
            ", ".join(f"{r:g}" for r in crafts.pwm_close_pos_ratios) or "(none)"
        )
        reserved = len(getattr(crafts, "legacy_reserved", ()) or ())
        self.opaque["reserved"].setText(f"{reserved} line(s) kept verbatim")

    @staticmethod
    def _set_cell(table: QTableWidget, row: int, column: int, value: float) -> None:
        item = table.item(row, column)
        if item is None:
            item = QTableWidgetItem()
            table.setItem(row, column, item)
        item.setText(f"{value:.6g}")
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
