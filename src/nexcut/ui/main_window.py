"""Main window: canvas, layer/property/machine docks, menus, status bar (PORT-PLAN §3.1, §4 M2).

Labels come from ``lang.txt`` through :mod:`nexcut.ui.i18n` (ids from 06 §4):
File ``mf7`` with Open ``mf9`` (dialog title ``mf150``, filter ``mf149``), Save
``mf10``, Save as ``mf11`` (dialog ``mf152``); View ``mf26`` with Show path start
``mf41``, Show path direction ``mf42``, Show index ``mf40``, Enable Ruler
``pd485``, Reset View ``mv1``, Measurement ``mf116`` (result ``mv17``); layer
names 02 §4; property labels ``gp61-76``; object kinds ``gmsg0-3``; untitled
``mf650``.

View toggles start from the ``manu`` parameters when given
(``GRP.IsShowStartPt/IsShowDirArrow/IsShowNO``, ``IGP.EnableRuler``, 01 §1.2),
else from the descriptor defaults.  The recent-file list is kept in
``QSettings`` (10 entries - port choice; the vendor keeps ``RecentFile`` per
layer and ``MSC.LatestFilePath``, whose use is not traced).

SAFETY (PORT-PLAN §8): this window has no machine control.  The "Machine" dock
is a disabled placeholder for the later ``mccd`` IPC client; nothing here opens
a socket or talks to the card.
"""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QColor, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nexcut.model.graph import ChfDocument, Contour, ContourEx, Group, Scan, Text
from nexcut.ops.import_gates import GateParams
from nexcut.ui.canvas import CanvasWidget, bed_from_hardware, canvas_style
from nexcut.ui.i18n import Translator, get_translator
from nexcut.ui.layers import LAYER_COUNT, layer_color, layer_name
from nexcut.ui.loader import LoadedDocument, LoadError, load_document, save_document
from nexcut.ui.render import ViewFlags
from nexcut.ui.scene import SceneData

__all__ = ["MAX_RECENT", "MainWindow"]

MAX_RECENT = 10
"""Recent-file entries kept (port choice)."""

RECENT_KEY = "file/recent"


def _kind_label(t: Translator, graph: object, scan_path: bool) -> str:
    """Object kind (``gmsg0-3``: Scan cut, Text, Group, Contour)."""
    if scan_path or isinstance(graph, Scan):
        return t.tr("gmsg0", "Scan cut")
    if isinstance(graph, Text):
        return t.tr("gmsg1", "Text")
    if isinstance(graph, (Group, ContourEx)):
        return t.tr("gmsg2", "Group")
    if isinstance(graph, Contour):
        return t.tr("gmsg3", "Contour")
    return "?"


class PropertyPanel(QWidget):
    """Read-only property stub for the selection (``CGlyphPropPanel`` labels gp61-76, 06 §4.2)."""

    FIELDS = ("kind", "index", "layer", "count", "x", "y", "width", "height", "length", "start")

    def __init__(self, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        t = translator
        self.t = t
        form = QFormLayout(self)
        labels = {
            "kind": "Object",
            "index": t.tr("gp75", "Index"),
            "layer": t.tr("gp83", "Layer"),
            "count": t.tr("gp76", "ContourNum"),
            "x": t.tr("gp61", "X-coord"),
            "y": t.tr("gp62", "Y-coord"),
            "width": t.tr("gp64", "Width"),
            "height": t.tr("gp63", "Height"),
            "length": "Length (mm)",
            "start": "Start (mm)",
        }
        self.values: dict[str, QLabel] = {}
        for key in self.FIELDS:
            lab = QLabel("-")
            lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.values[key] = lab
            form.addRow(labels[key], lab)

    def show_selection(self, scene: SceneData | None, indices: list[int]) -> None:
        """Fill the fields for the selected contours (bbox centre as X/Y)."""
        for lab in self.values.values():
            lab.setText("-")
        if scene is None or not indices:
            return
        cvs = [scene.contours[i] for i in indices]
        x0 = min(c.bbox[0] for c in cvs)
        y0 = min(c.bbox[1] for c in cvs)
        x1 = max(c.bbox[2] for c in cvs)
        y1 = max(c.bbox[3] for c in cvs)
        v = self.values
        v["count"].setText(str(len(cvs)))
        v["x"].setText(f"{(x0 + x1) / 2:.3f}")
        v["y"].setText(f"{(y0 + y1) / 2:.3f}")
        v["width"].setText(f"{x1 - x0:.3f}")
        v["height"].setText(f"{y1 - y0:.3f}")
        v["length"].setText(f"{sum(c.length for c in cvs):.3f}")
        if len(cvs) == 1:
            c = cvs[0]
            v["kind"].setText(_kind_label(self.t, c.graph, c.is_scan_path))
            v["index"].setText(str(c.graph_index))
            v["layer"].setText(layer_name(c.layer, self.t))
            if c.start is not None:
                v["start"].setText(f"{c.start[0]:.3f}, {c.start[1]:.3f}")


class MainWindow(QMainWindow):
    """The application main window (module docstring)."""

    documentChanged = Signal()

    def __init__(
        self,
        *,
        translator: Translator | None = None,
        manu: object | None = None,
        hard: object | None = None,
        settings: QSettings | None = None,
        interactive: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.t = translator or get_translator()
        self.manu = manu
        self.interactive = interactive
        self.settings = settings or QSettings("nexcut", "nexcut")
        self.document: ChfDocument | None = None
        self.path: Path | None = None
        self.loaded: LoadedDocument | None = None
        self.gate_params = GateParams.from_params(manu) if manu is not None else GateParams()  # type: ignore[arg-type]

        flags = ViewFlags.from_manu(manu) if manu is not None else ViewFlags()
        self.canvas_widget = CanvasWidget(
            self, style=canvas_style(manu), flags=flags, bed=bed_from_hardware(hard)
        )
        self.canvas = self.canvas_widget.view
        self.setCentralWidget(self.canvas_widget)

        self._build_status_bar()
        self._build_docks()
        self._build_menus()
        self.canvas.cursorMoved.connect(self._on_cursor)
        self.canvas.selectionChanged.connect(self._on_selection)
        self.canvas.measured.connect(self._on_measured)
        self.resize(1280, 800)
        self._update_title()
        self.canvas.zoom_to_fit()

    # ------------------------------------------------------------------- build
    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.cursor_label = QLabel("X: -  Y: -")
        self.cursor_label.setMinimumWidth(220)
        self.selection_label = QLabel("")
        sb.addPermanentWidget(self.selection_label)
        sb.addPermanentWidget(self.cursor_label)

    def _build_docks(self) -> None:
        t = self.t
        self.layer_tree = QTreeWidget()
        self.layer_tree.setHeaderLabels([t.tr("gp83", "Layer"), t.tr("gp76", "ContourNum")])
        self.layer_tree.setRootIsDecorated(False)
        self.layer_items: list[QTreeWidgetItem] = []
        for i in range(LAYER_COUNT):
            item = QTreeWidgetItem([layer_name(i, t), "0"])
            pm = QPixmap(14, 14)
            pm.fill(QColor(layer_color(i, self.canvas.style.palette)))
            item.setIcon(0, QIcon(pm))
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
            self.layer_tree.addTopLevelItem(item)
            self.layer_items.append(item)
        self.layer_tree.itemChanged.connect(self._on_layer_item_changed)
        self.layer_dock = QDockWidget(t.tr("lp0", "Layer Parameters"), self)
        self.layer_dock.setObjectName("layerDock")
        self.layer_dock.setWidget(self.layer_tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.layer_dock)

        self.property_panel = PropertyPanel(t)
        self.property_dock = QDockWidget("Properties", self)
        self.property_dock.setObjectName("propertyDock")
        self.property_dock.setWidget(self.property_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.property_dock)

        machine = QWidget()
        lay = QVBoxLayout(machine)
        note = QLabel(
            "Machine control is not available in this build.\nIt will connect to nexcut-mccd over local IPC."
        )
        note.setWordWrap(True)
        lay.addWidget(note)
        for key, default in (("mf15", "Go Origin"), ("mf6", "Start")):
            b = QPushButton(t.tr(key, default))
            b.setEnabled(False)
            lay.addWidget(b)
        lay.addStretch(1)
        machine.setEnabled(False)
        self.machine_dock = QDockWidget("Machine", self)
        self.machine_dock.setObjectName("machineDock")
        self.machine_dock.setWidget(machine)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.machine_dock)

    def _build_menus(self) -> None:
        t = self.t
        mb = self.menuBar()
        file_menu = mb.addMenu(t.tr("mf7", "File"))
        self.action_open = QAction(t.tr("mf9", "Open"), self)
        self.action_open.setShortcut(QKeySequence.StandardKey.Open)
        self.action_open.triggered.connect(self.open_dialog)
        self.action_save = QAction(t.tr("mf10", "Save"), self)
        self.action_save.setShortcut(QKeySequence.StandardKey.Save)
        self.action_save.triggered.connect(self.save)
        self.action_save_as = QAction(t.tr("mf11", "Save as"), self)
        self.action_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.action_save_as.triggered.connect(self.save_as_dialog)
        self.recent_menu = QMenu("Recent", self)
        self.action_quit = QAction(t.tr("mp118", "Exit"), self)
        self.action_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.action_quit.triggered.connect(self.close)
        file_menu.addAction(self.action_open)
        file_menu.addMenu(self.recent_menu)
        file_menu.addSeparator()
        file_menu.addAction(self.action_save)
        file_menu.addAction(self.action_save_as)
        file_menu.addSeparator()
        file_menu.addAction(self.action_quit)
        self._refresh_recent_menu()

        view_menu = mb.addMenu(t.tr("mf26", "View"))
        f = self.canvas.flags
        self.action_show_start = self._toggle(
            view_menu,
            t.tr("mf41", "Show path start"),
            f.show_start,
            lambda on: self.canvas.set_flags(show_start=on),
        )
        self.action_show_arrows = self._toggle(
            view_menu,
            t.tr("mf42", "Show path direction"),
            f.show_arrows,
            lambda on: self.canvas.set_flags(show_arrows=on),
        )
        self.action_show_index = self._toggle(
            view_menu,
            t.tr("mf40", "Show index"),
            f.show_index,
            lambda on: self.canvas.set_flags(show_index=on),
        )
        self.action_rulers = self._toggle(
            view_menu,
            t.item("pd485", "Misc.Enable Ruler"),
            f.show_rulers,
            self.canvas_widget.set_rulers_visible,
        )
        self.action_show_bed = self._toggle(
            view_menu, "Show bed outline", f.show_bed, lambda on: self.canvas.set_flags(show_bed=on)
        )
        view_menu.addSeparator()
        self.action_fit = QAction(t.tr("mv1", "Reset View"), self)
        self.action_fit.setShortcut(QKeySequence("F"))
        self.action_fit.triggered.connect(lambda: self.canvas.zoom_to_fit())
        view_menu.addAction(self.action_fit)
        tools = QActionGroup(self)
        self.action_select_tool = QAction(t.tr("mf32", "Select"), self, checkable=True)
        self.action_select_tool.setChecked(True)
        self.action_select_tool.triggered.connect(lambda: self.canvas.set_tool("select"))
        self.action_measure = QAction(t.tr("mf116", "Measurement"), self, checkable=True)
        self.action_measure.setShortcut(QKeySequence("M"))
        self.action_measure.triggered.connect(lambda: self.canvas.set_tool("measure"))
        for a in (self.action_select_tool, self.action_measure):
            tools.addAction(a)
            view_menu.addAction(a)
        view_menu.addSeparator()
        for dock in (self.layer_dock, self.property_dock, self.machine_dock):
            view_menu.addAction(dock.toggleViewAction())

    def _toggle(self, menu: QMenu, text: str, checked: bool, slot: object) -> QAction:
        action = QAction(text, self, checkable=True)
        action.setChecked(checked)
        action.toggled.connect(slot)  # type: ignore[arg-type]
        menu.addAction(action)
        return action

    # ----------------------------------------------------------------- files
    def open_dialog(self) -> None:
        """File > Open (``mf150`` title, ``mf149`` filter)."""
        name_filter = self.t.file_filter(
            "mf149",
            "Supported File Types(*.dxf;*.chf;*.nc;*.txt;*.cnc;*.g;*plt)|*.dxf;*.chf;*.nc;*.txt;*.cnc;*.g;*.plt||",
        )
        path, _ = QFileDialog.getOpenFileName(
            self, self.t.tr("mf150", "Open File"), self._dialog_dir(), name_filter
        )
        if path:
            self.open_path(path)

    def open_path(self, path: str | os.PathLike[str]) -> bool:
        """Load a file into the canvas; errors go to a message box (or the status bar when non-interactive)."""
        try:
            loaded = load_document(path, gate_params=self.gate_params)
        except LoadError as exc:
            self._error(
                self.t.tr("mv30", "SC file load failed!")
                if Path(path).suffix.lower() == ".chf"
                else str(exc),
                str(exc),
            )
            return False
        self.loaded = loaded
        self.document = loaded.document
        self.path = loaded.path if loaded.kind == "chf" else None
        scene = self.canvas.set_document(loaded.document)
        self._update_layers(scene)
        self.canvas.zoom_to_fit()
        self._add_recent(loaded.path)
        self._update_title()
        msg = f"{loaded.path.name}: {len(loaded.document.graphs)} graphs"
        if loaded.warnings:
            msg += f", {len(loaded.warnings)} warnings"
        self.statusBar().showMessage(msg, 10000)
        self.documentChanged.emit()
        return True

    def save(self) -> bool:
        """File > Save: to the current ``.chf`` path, else Save as."""
        if self.document is None:
            return False
        if self.path is None:
            return self.save_as_dialog()
        return self.save_to(self.path)

    def save_as_dialog(self) -> bool:
        """File > Save as (``mf152``)."""
        if self.document is None:
            return False
        suggestion = self._dialog_dir()
        if self.loaded is not None:
            suggestion = str(Path(suggestion) / (self.loaded.path.stem + ".chf"))
        path, _ = QFileDialog.getSaveFileName(
            self, self.t.tr("mf152", "Save File"), suggestion, "CHF (*.chf)"
        )
        return bool(path) and self.save_to(path)

    def save_to(self, path: str | os.PathLike[str]) -> bool:
        """Write the document as ``.chf`` and make it the current file."""
        if self.document is None:
            return False
        try:
            target = save_document(self.document, path)
        except (OSError, ValueError, TypeError) as exc:
            self._error("Save failed", str(exc))
            return False
        self.path = target
        self._add_recent(target)
        self._update_title()
        self.statusBar().showMessage(self.t.tr("np_FileSaveDone", "File Save Done!"), 5000)
        return True

    def recent_files(self) -> list[str]:
        """Recent paths, newest first."""
        raw = self.settings.value(RECENT_KEY, [])
        if isinstance(raw, str):
            raw = [raw]
        return [str(p) for p in (raw or [])]

    def _add_recent(self, path: Path) -> None:
        p = str(Path(path).resolve())
        items = [p, *(x for x in self.recent_files() if x != p)][:MAX_RECENT]
        self.settings.setValue(RECENT_KEY, items)
        self.settings.sync()
        self._refresh_recent_menu()

    def _refresh_recent_menu(self) -> None:
        self.recent_menu.clear()
        files = self.recent_files()
        for i, p in enumerate(files, 1):
            act = self.recent_menu.addAction(f"&{i % 10} {Path(p).name}")
            act.setToolTip(p)
            act.triggered.connect(lambda _=False, path=p: self.open_path(path))
        self.recent_menu.setEnabled(bool(files))

    def _dialog_dir(self) -> str:
        files = self.recent_files()
        return str(Path(files[0]).parent) if files else str(Path.home())

    # ----------------------------------------------------------------- slots
    def _on_cursor(self, x: float, y: float) -> None:
        self.cursor_label.setText(f"X: {x:.3f}  Y: {y:.3f}")

    def _on_selection(self, indices: list[int]) -> None:
        self.property_panel.show_selection(self.canvas.scene_data, indices)
        self.selection_label.setText(f"{len(indices)} selected" if indices else "")

    def _on_measured(self, dx: float, dy: float, length: float) -> None:
        fmt = self.t.tr("mv17", "Measurement Result  X: %0.3fmm  Y: %0.3fmm  Length: %0.3fmm")
        try:
            text = fmt % (dx, dy, length)
        except (TypeError, ValueError):
            text = f"X: {dx:.3f}mm  Y: {dy:.3f}mm  Length: {length:.3f}mm"
        self.statusBar().showMessage(text)

    def _on_layer_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        layer = int(item.data(0, Qt.ItemDataRole.UserRole))
        self.canvas.set_layer_visible(layer, item.checkState(0) == Qt.CheckState.Checked)

    def _update_layers(self, scene: SceneData | None) -> None:
        counts = scene.layers_used() if scene is not None else {}
        self.layer_tree.blockSignals(True)
        for i, item in enumerate(self.layer_items):
            item.setText(1, str(counts.get(i, 0)))
        self.layer_tree.blockSignals(False)

    def _update_title(self) -> None:
        if self.loaded is None:
            name = self.t.tr("mf650", "Untitled-")
        else:
            name = (self.path or self.loaded.path).name
        self.setWindowTitle(f"{name} - NexCut (Linux)")

    def _error(self, title: str, text: str) -> None:
        self.statusBar().showMessage(f"{title}: {text}" if title != text else text, 10000)
        if self.interactive:
            QMessageBox.warning(self, title, text)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Persist settings on close."""
        self.settings.sync()
        super().closeEvent(event)
