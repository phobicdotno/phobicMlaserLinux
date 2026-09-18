"""Power / frequency curve editor: the ``PWMCurveNodes`` / ``FreqCurveNodes`` strings (02 §3.4).

A layer stores its speed-dependent power and frequency ramps as one flat string
of comma-separated numbers, an even number of them, read as ``(speed %, output %)``
pairs: ``"0,0,15,35,37,69,59,92,76,100,100,100"`` is six nodes.  The vendor's own
editor is the ``lp2`` "功率曲线 / Power" page with a ``Value/Property`` node grid and
``lcv0`` "delete curve point" (06 §4.4); ``CLaserCurveView`` is the RTTI name of the
window that draws it (06 §8).

What this module provides:

* :func:`parse_curve_text` / :func:`format_curve_text` - the string codec.  The
  round trip is **exact** for every string in the vendor's ``BkLayerPara.xml``,
  including the empty string that eight of the eleven slots carry: nodes are kept
  in file order (never re-sorted on load) and an integral value is written back
  without a decimal point, which is the only spelling the vendor files use.
* :func:`curve_problems` - the shape rules of 02 §3.4 as warnings.
* :func:`evaluate` / :func:`sample` - the node math, delegated to
  :class:`nexcut.plan.pwm_schedule.CurveNodes` so the preview a user sees is
  computed by the same code the planner runs (``np.interp`` on the node table,
  clamped to the node range, ``trunc(y + 0.5)``).
* :class:`CurveEditor` - a small XY node editor: a two-column table, add/remove,
  a painted preview, and the text of the parameter as its value.

UNVERIFIED:

* the smoothing selected by ``PowerCurveSmoothType`` / ``FreqCurveSmoothType``
  (``A241012_1/2/3`` Default / Linear / Smooth) is **not** applied.  Only the
  option *names* are evidence; what "Default" and "Smooth" compute is not traced,
  and the planner interpolates linearly (:class:`~nexcut.plan.pwm_schedule.CurveNodes`).
  The editor shows the selected type and draws the linear curve, and says so.
* whether MainApp itself enforces "first x is 0" and "last pair is 100,100", or
  whether those simply hold in every shipped preset (02 §3.4 reads them off the
  data).  They are warnings here, never a rejection, because a vendor file that
  breaks them must still load and re-save byte for byte.

SAFETY (PORT-PLAN §8): a text editor for one XML attribute.  No connection, no
motion, no laser.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

__all__ = [
    "AXIS_MAX",
    "SMOOTH_TYPE_IDS",
    "UNVERIFIED",
    "CurveError",
    "CurveEditor",
    "CurvePreview",
    "curve_problems",
    "evaluate",
    "format_curve_text",
    "format_value",
    "parse_curve_text",
    "sample",
]

Node = tuple[float, float]

AXIS_MAX = 100.0
"""Both axes are percentages (02 §3.4: ``x`` % of ``CutSpeed``, ``y`` % of ``CutPower``/``CutFreq``)."""

SMOOTH_TYPE_IDS: tuple[str, ...] = ("A241012_1", "A241012_2", "A241012_3")
"""``PowerCurveSmoothType`` / ``FreqCurveSmoothType`` options 0..2 (Default / Linear / Smooth)."""

UNVERIFIED: tuple[str, ...] = (
    "the curve is drawn and evaluated linearly for every PowerCurveSmoothType / "
    "FreqCurveSmoothType; what options 0 (Default) and 2 (Smooth) compute is not traced "
    "(02 §3.4 gives only the option names A241012_1/2/3)",
    "'first x is 0' and 'last pair is 100,100' are read off the shipped presets (02 §3.4), "
    "not off code - they are warnings here, never a rejection",
    "a node's y may exceed 100 % in principle; the editor bounds both axes at 0..100 because "
    "every vendor value does (port choice)",
)


class CurveError(ValueError):
    """The attribute text is not a curve (odd number of values, or a value that is not a number)."""


def format_value(value: float) -> str:
    """One node coordinate as the vendor writes it (``35``, not ``35.0``; 02 §3.4)."""
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


def parse_curve_text(text: str) -> list[Node]:
    """``"0,0,15,35"`` -> ``[(0.0, 0.0), (15.0, 35.0)]``; ``""`` -> ``[]``.

    Nodes keep their file order - they are **not** sorted - so a file that stores
    them unsorted survives a load/save with its bytes intact.  ``;`` is accepted as
    a separator the way :meth:`nexcut.plan.pwm_schedule.CurveNodes.parse` accepts it.
    """
    tokens = [t.strip() for t in str(text).replace(";", ",").split(",")]
    values: list[float] = []
    for token in tokens:
        if not token:
            continue
        try:
            values.append(float(token))
        except ValueError:
            raise CurveError(f"{token!r} is not a number in curve {text!r}") from None
    if len(values) % 2:
        raise CurveError(f"odd number of values ({len(values)}) in curve {text!r}")
    return list(zip(values[0::2], values[1::2], strict=True))


def format_curve_text(nodes: Iterable[Node]) -> str:
    """Nodes -> the attribute text; an empty node list gives the empty string."""
    parts: list[str] = []
    for x, y in nodes:
        parts.append(format_value(x))
        parts.append(format_value(y))
    return ",".join(parts)


def curve_problems(nodes: Sequence[Node]) -> list[str]:
    """Shape problems of a node list (02 §3.4), as warnings.

    An empty list is fine: eight of the eleven slots of the vendor
    ``BkLayerPara.xml`` carry an empty curve, and the planner reads that as the
    flat 100 % curve (:meth:`~nexcut.plan.pwm_schedule.CurveNodes.parse`).
    """
    if not nodes:
        return []
    out: list[str] = []
    if len(nodes) < 2:
        out.append("curve: a curve needs at least two nodes")
    for i, (x, y) in enumerate(nodes):
        if not (0.0 <= x <= AXIS_MAX):
            out.append(f"curve: node {i} speed {x:g} % outside [0, {AXIS_MAX:g}]")
        if not (0.0 <= y <= AXIS_MAX):
            out.append(f"curve: node {i} output {y:g} % outside [0, {AXIS_MAX:g}]")
    for i in range(1, len(nodes)):
        if nodes[i][0] <= nodes[i - 1][0]:
            out.append(
                f"curve: node {i} speed {nodes[i][0]:g} % is not above node {i - 1} "
                f"({nodes[i - 1][0]:g} %)"
            )
    if nodes[0][0] != 0.0:
        out.append(f"curve: the first node starts at {nodes[0][0]:g} %, the vendor presets start at 0 %")
    if nodes[-1] != (AXIS_MAX, AXIS_MAX):
        out.append(
            f"curve: the last node is {nodes[-1][0]:g},{nodes[-1][1]:g}; "
            f"every vendor preset ends at {AXIS_MAX:g},{AXIS_MAX:g}"
        )
    return out


def evaluate(nodes: Sequence[Node], speed_percent: float) -> float:
    """Output % at ``speed_percent`` of the nominal speed (linear, clamped to the node range).

    Delegates to :class:`nexcut.plan.pwm_schedule.CurveNodes` with a nominal speed
    and a base of 100 %, so the editor cannot disagree with the planner.  An empty
    node list is the flat 100 % curve, exactly as ``CurveNodes.parse("")`` is.
    """
    from nexcut.plan.pwm_schedule import CurveNodes

    curve = CurveNodes.parse(format_curve_text(nodes))
    return float(curve.evaluate([float(speed_percent)], AXIS_MAX, AXIS_MAX)[0])


def sample(nodes: Sequence[Node], count: int = 101) -> list[Node]:
    """``count`` evenly spaced ``(speed %, output %)`` samples for the preview."""
    if count < 2:
        raise ValueError("count must be at least 2")
    step = AXIS_MAX / (count - 1)
    return [(i * step, evaluate(nodes, i * step)) for i in range(count)]


class CurvePreview(QWidget):
    """Paints the sampled curve and its nodes in a 0..100 x 0..100 box."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.nodes: list[Node] = []
        self.setMinimumHeight(110)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_nodes(self, nodes: Sequence[Node]) -> None:
        """Show ``nodes`` (a copy is kept)."""
        self.nodes = list(nodes)
        self.update()

    def paintEvent(self, event: object) -> None:  # noqa: N802 - Qt name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(6.0, 6.0, -6.0, -6.0)
        painter.fillRect(box, QColor(0xFA, 0xFA, 0xFA))
        painter.setPen(QPen(QColor(0xC0, 0xC0, 0xC0)))
        painter.drawRect(box)
        if box.width() <= 0 or box.height() <= 0:
            return

        def point(x: float, y: float) -> tuple[float, float]:
            return (
                box.left() + box.width() * min(max(x, 0.0), AXIS_MAX) / AXIS_MAX,
                box.bottom() - box.height() * min(max(y, 0.0), AXIS_MAX) / AXIS_MAX,
            )

        path = QPainterPath()
        for i, (x, y) in enumerate(sample(self.nodes, 51)):
            px, py = point(x, y)
            (path.moveTo if i == 0 else path.lineTo)(px, py)
        painter.setPen(QPen(QColor(0x18, 0x60, 0xC0), 1.6))
        painter.drawPath(path)
        painter.setPen(QPen(QColor(0xC0, 0x30, 0x30), 1.0))
        painter.setBrush(QColor(0xC0, 0x30, 0x30))
        for x, y in self.nodes:
            px, py = point(x, y)
            painter.drawEllipse(QRectF(px - 2.5, py - 2.5, 5.0, 5.0))
        painter.end()


class CurveEditor(QWidget):
    """XY node editor for one curve attribute (module docstring).

    The widget's value is the *attribute text*: :meth:`text` returns what belongs
    in ``PWMCurveNodes`` / ``FreqCurveNodes``, and :meth:`set_text` accepts what is
    in the file.  Text the codec cannot read is kept verbatim and the editor turns
    itself read-only, so an unreadable curve can never be silently rewritten.
    """

    curveChanged = Signal(str)
    """Emitted with the new attribute text after an edit."""

    validationChanged = Signal(list)
    """Current :func:`curve_problems` output (empty list = the curve is well shaped)."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.nodes: list[Node] = []
        self._raw: str | None = None
        """Set to the file text when it could not be parsed; the editor then never writes."""
        self._problems: list[str] = []
        self._updating = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        if title:
            heading = QLabel(title)
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            outer.addWidget(heading)
        self.preview = CurvePreview()
        outer.addWidget(self.preview)

        self.table = QTableWidget(0, 2)
        # Port choice: lang.txt has no captions for the two columns (the vendor grid
        # shows "Value/Property", 02 §3.4), so these are plain English.
        self.table.setHorizontalHeaderLabels(["Speed %", "Output %"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        outer.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.add_button = QPushButton("+")
        self.add_button.setToolTip("Add a node after the selected one")
        self.add_button.clicked.connect(self.add_node)
        self.remove_button = QPushButton("-")
        self.remove_button.setToolTip("Delete the selected node (lcv0)")
        self.remove_button.clicked.connect(self.remove_selected)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        buttons.addWidget(self.status, 1)
        outer.addLayout(buttons)

    # ------------------------------------------------------------------- value
    def text(self) -> str:
        """The attribute text (unparsable input is returned unchanged)."""
        return self._raw if self._raw is not None else format_curve_text(self.nodes)

    def set_text(self, text: str) -> None:
        """Load an attribute value; unreadable text puts the editor in read-only mode."""
        try:
            nodes = parse_curve_text(text)
        except CurveError as exc:
            self._raw = str(text)
            self.nodes = []
            self._rebuild()
            self._set_problems([f"curve: {exc}"])
            return
        self._raw = None
        self.nodes = nodes
        self._rebuild()
        self._set_problems(curve_problems(self.nodes))

    def set_nodes(self, nodes: Sequence[Node]) -> None:
        """Replace the node list and emit the new text."""
        self._raw = None
        self.nodes = list(nodes)
        self._rebuild()
        self._commit()

    @property
    def read_only(self) -> bool:
        """True while the loaded text could not be parsed (module docstring)."""
        return self._raw is not None

    @property
    def problems(self) -> list[str]:
        """Problems of the current curve (:func:`curve_problems`)."""
        return list(self._problems)

    # ------------------------------------------------------------------ edits
    def add_node(self) -> None:
        """Insert a node after the selected row, midway to the next one."""
        if self.read_only:
            return
        row = self.table.currentRow()
        if not self.nodes:
            self.set_nodes([(0.0, 0.0), (AXIS_MAX, AXIS_MAX)])
            return
        if row < 0 or row >= len(self.nodes) - 1:
            row = len(self.nodes) - 2 if len(self.nodes) >= 2 else 0
        left = self.nodes[row]
        right = self.nodes[min(row + 1, len(self.nodes) - 1)]
        new = ((left[0] + right[0]) / 2.0, (left[1] + right[1]) / 2.0)
        nodes = list(self.nodes)
        nodes.insert(row + 1, new)
        self.set_nodes(nodes)
        self.table.setCurrentCell(row + 1, 0)

    def remove_selected(self) -> None:
        """Delete the selected node (``lcv0`` "delete curve point", 06 §4.4)."""
        if self.read_only:
            return
        row = self.table.currentRow()
        if 0 <= row < len(self.nodes):
            nodes = list(self.nodes)
            del nodes[row]
            self.set_nodes(nodes)

    def sort_nodes(self) -> None:
        """Order the nodes by speed (only ever called from an edit, never on load)."""
        if self.read_only:
            return
        self.set_nodes(sorted(self.nodes))

    # ---------------------------------------------------------------- internals
    def _rebuild(self) -> None:
        was, self._updating = self._updating, True
        self.table.setRowCount(len(self.nodes))
        for row, (x, y) in enumerate(self.nodes):
            for column, value in ((0, x), (1, y)):
                item = self.table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    self.table.setItem(row, column, item)
                item.setText(format_value(value))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                if not self.read_only:
                    flags |= Qt.ItemFlag.ItemIsEditable
                item.setFlags(flags)
        self.add_button.setEnabled(not self.read_only)
        self.remove_button.setEnabled(not self.read_only)
        self.preview.set_nodes(self.nodes)
        self._updating = was

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating or self.read_only:
            return
        row, column = item.row(), item.column()
        if not (0 <= row < len(self.nodes)):
            return
        try:
            value = float(item.text())
        except ValueError:
            self._rebuild()  # put the old text back
            self._set_problems([*self._problems, f"curve: {item.text()!r} is not a number"])
            return
        value = min(max(value, 0.0), AXIS_MAX)  # both axes are percentages
        x, y = self.nodes[row]
        self.nodes[row] = (value, y) if column == 0 else (x, value)
        self._rebuild()
        self._commit()

    def _commit(self) -> None:
        self._set_problems(curve_problems(self.nodes))
        self.curveChanged.emit(self.text())

    def _set_problems(self, problems: list[str]) -> None:
        self.status.setText("\n".join(problems))
        self.status.setStyleSheet("color: #a00;" if problems else "")
        if problems != self._problems:
            self._problems = problems
            self.validationChanged.emit(list(problems))


def _spin(value: float) -> QDoubleSpinBox:  # pragma: no cover - helper kept for pages
    """A 0..100 % spin box with the editor's own bounds."""
    box = QDoubleSpinBox()
    box.setRange(0.0, AXIS_MAX)
    box.setDecimals(3)
    box.setValue(value)
    return box
