"""Drawing canvas: ``QGraphicsView`` in a Y-up millimetre frame (PORT-PLAN §3.1 ``COpenGLView`` row).

* Frame: scene units are mm; the view transform is ``scale(s, -s)`` so +Y is up
  (03 §10).  The bed outline is ``(0, 0)..(bed_x, bed_y)``; the size comes from
  the hardware XML soft limits when given (``MAC.SoftLimitMaxLen`` /
  ``MAC_1.SoftLimitMaxLen``, 01 §2.2) or :data:`nexcut.ui.render.DEFAULT_BED`
  (1300 x 900, UNVERIFIED).  Placing the bed at the origin is UNVERIFIED.
* Batching: contours are grouped per ``(layer, scan path)`` into
  :class:`ContourBatchItem` s of at most :data:`BATCH_POINTS` vertices, so a
  drawing of 10^4 glyphs is a few dozen items; markers (start points, arrows,
  index numbers - ``GRP.IsShowStartPt/IsShowDirArrow/IsShowNO``, 01 §1.2) are one
  :class:`MarkerItem` painting in device pixels; picking is done on the numpy
  arrays of :class:`nexcut.ui.scene.SceneData`, not with per-glyph items.
* Interaction: wheel zooms around the cursor, middle-drag pans, left click
  selects (Ctrl toggles), left drag box-selects contours fully inside, the
  measure tool (``mf116``) reports dX/dY/length in the ``mv17`` format.
* Rulers (``IGP.EnableRuler``, ``pd485``): :class:`RulerWidget` on the top and
  left of :class:`CanvasWidget`.

No machine control here (PORT-PLAN §8): the canvas only displays geometry.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QStyleOptionGraphicsItem,
    QWidget,
)

from nexcut.model.graph import ChfDocument
from nexcut.ui.render import (
    DEFAULT_BED,
    PaintStyle,
    ViewFlags,
    add_polylines,
    paint_markers,
    paint_scene_lines,
    polygon_from_array,
)
from nexcut.ui.scene import SceneData, build_scene

__all__ = [
    "BATCH_POINTS",
    "CanvasView",
    "CanvasWidget",
    "ContourBatchItem",
    "MarkerItem",
    "RulerWidget",
    "bed_from_hardware",
    "canvas_style",
    "colorref_to_hex",
]

BATCH_POINTS = 50_000
"""Maximum flattened vertices per batch item (port tuning value, not vendor evidence)."""

MIN_SCALE = 1e-3
MAX_SCALE = 5e3
"""Zoom limits in pixels per mm (port choice)."""

PICK_TOLERANCE_PX = 5.0
"""Click distance for selecting a contour (port choice)."""


def colorref_to_hex(value: int) -> str:
    """Win32 ``COLORREF`` ``0x00BBGGRR`` (descriptor type 14, 01 §1) -> ``#rrggbb``."""
    v = int(value) & 0xFFFFFF
    return f"#{v & 0xFF:02x}{(v >> 8) & 0xFF:02x}{(v >> 16) & 0xFF:02x}"


def canvas_style(manu: object | None = None) -> PaintStyle:
    """Canvas colours: background from ``SP.DrawblkColor`` (``A250419_3``, COLORREF, shipped 0 = black).

    UNVERIFIED that the vendor paints exactly this value (``pd361/pd362`` White/Black
    labels suggest a two-choice setting).  Selection is drawn white on dark and
    black on light backgrounds.
    """
    value = 0
    if manu is not None:
        value = int(manu.get("PSoftParam", "SP", "DrawblkColor"))  # type: ignore[attr-defined]
    bg = colorref_to_hex(value)
    light = QColor(bg).lightness() > 128
    return PaintStyle(
        background=bg,
        selection_color="#000000" if light else "#ffffff",
        bed_color="#8c8c8c",
    )


def bed_from_hardware(hard: object | None) -> tuple[float, float]:
    """Bed size from a hard ParamDocument (``MAC``/``MAC_1.SoftLimitMaxLen``, 01 §2.2), else the default."""
    if hard is None:
        return DEFAULT_BED
    x = float(hard.get("PMachineAxisConfig_0", "MAC", "SoftLimitMaxLen"))  # type: ignore[attr-defined]
    y = float(hard.get("PMachineAxisConfig_1", "MAC_1", "SoftLimitMaxLen"))  # type: ignore[attr-defined]
    if x <= 0 or y <= 0:
        return DEFAULT_BED
    return (x, y)


class ContourBatchItem(QGraphicsItem):
    """A batch of contours of one layer drawn as a single path with a cosmetic pen."""

    def __init__(
        self,
        scene_data: SceneData,
        indices: Sequence[int],
        layer: int,
        scan: bool,
        style: PaintStyle,
    ):
        super().__init__()
        self.indices = list(indices)
        self.layer = layer
        self.scan = scan
        self.style = style
        path = QPainterPath()
        for i in self.indices:
            add_polylines(path, scene_data.contours[i].polylines)
        self.path = path
        b = scene_data.boxes[self.indices]
        self._rect = (
            QRectF(
                float(b[:, 0].min()),
                float(b[:, 1].min()),
                float(b[:, 2].max() - b[:, 0].min()),
                float(b[:, 3].max() - b[:, 1].min()),
            )
            if len(b)
            else QRectF()
        )

    def boundingRect(self) -> QRectF:
        """Union of the contour bboxes (mm), slightly padded for the cosmetic pen."""
        return self._rect.adjusted(-1e-3, -1e-3, 1e-3, 1e-3)

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None
    ) -> None:
        """Stroke the batch path."""
        paint_scene_lines(painter, {(self.layer, self.scan): self.path}, self.style)


class MarkerItem(QGraphicsItem):
    """Point glyphs, start points, arrows and index numbers painted in device pixels."""

    def __init__(self, view: CanvasView):
        super().__init__()
        self.view = view
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)

    def boundingRect(self) -> QRectF:
        """The whole scene rect: markers can sit anywhere and have a pixel size."""
        return self.view.sceneRect()

    def paint(
        self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None
    ) -> None:
        """Delegate to :func:`nexcut.ui.render.paint_markers` for visible contours."""
        v = self.view
        if v.scene_data is None:
            return
        clip = QRectF(widget.rect()) if widget is not None else None
        paint_markers(
            painter,
            v.scene_data,
            painter.worldTransform(),
            v.flags,
            v.style,
            clip=clip,
            hidden_layers=v.hidden_layers,
        )


class CanvasView(QGraphicsView):
    """The canvas view (module docstring)."""

    cursorMoved = Signal(float, float)
    selectionChanged = Signal(list)
    measured = Signal(float, float, float)
    viewChanged = Signal()

    TOOLS = ("select", "measure")

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        style: PaintStyle | None = None,
        flags: ViewFlags | None = None,
        bed: tuple[float, float] | None = DEFAULT_BED,
    ) -> None:
        super().__init__(parent)
        self.style = style or canvas_style()
        self.flags = flags or ViewFlags()
        self.bed = bed
        self.scene_data: SceneData | None = None
        self.hidden_layers: set[int] = set()
        self.selected: list[int] = []
        self.tool = "select"
        self._batches: list[ContourBatchItem] = []
        self._press: QPoint | None = None
        self._press_scene: QPointF | None = None
        self._pan_last: QPoint | None = None
        self._scale = 1.0

        gs = QGraphicsScene(self)
        gs.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)
        self.setScene(gs)
        self.setBackgroundBrush(QBrush(QColor(self.style.background)))
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState, True)

        self._bed_item = QGraphicsRectItem()
        pen = QPen(QColor(self.style.bed_color), 0, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self._bed_item.setPen(pen)
        self._bed_item.setZValue(-10)
        gs.addItem(self._bed_item)

        self._selection_item = gs.addPath(QPainterPath())
        sel_pen = QPen(QColor(self.style.selection_color), 2)
        sel_pen.setCosmetic(True)
        self._selection_item.setPen(sel_pen)
        self._selection_item.setZValue(5)

        self._markers = MarkerItem(self)
        self._markers.setZValue(10)
        gs.addItem(self._markers)

        self._measure_line = QGraphicsLineItem()
        m_pen = QPen(QColor("#00e0ff"), 1)
        m_pen.setCosmetic(True)
        self._measure_line.setPen(m_pen)
        self._measure_line.setZValue(20)
        self._measure_line.setVisible(False)
        gs.addItem(self._measure_line)

        self._band = QGraphicsRectItem()
        b_pen = QPen(QColor("#4da3ff"), 0, Qt.PenStyle.DashLine)
        b_pen.setCosmetic(True)
        self._band.setPen(b_pen)
        self._band.setZValue(20)
        self._band.setVisible(False)
        gs.addItem(self._band)

        self._apply_bed()
        self._update_scene_rect()
        self.setTransform(QTransform(1.0, 0.0, 0.0, -1.0, 0.0, 0.0))

    # ----------------------------------------------------------------- content
    def set_document(self, doc: ChfDocument | None) -> SceneData | None:
        """Show ``doc`` (``None`` clears); returns the built scene data."""
        self.set_scene_data(build_scene(doc) if doc is not None else None)
        return self.scene_data

    def set_scene_data(self, data: SceneData | None) -> None:
        """Replace the displayed geometry and rebuild the batch items."""
        gs = self.scene()
        for item in self._batches:
            gs.removeItem(item)
        self._batches = []
        self.scene_data = data
        self.selected = []
        self._selection_item.setPath(QPainterPath())
        if data is not None:
            groups: dict[tuple[int, bool], list[int]] = {}
            for cv in data.contours:
                if cv.bbox[0] <= cv.bbox[2]:
                    groups.setdefault((cv.layer, cv.is_scan_path), []).append(cv.index)
            for (layer, scan), idxs in groups.items():
                chunk: list[int] = []
                count = 0
                for i in idxs:
                    chunk.append(i)
                    count += data.contours[i].point_count
                    if count >= BATCH_POINTS:
                        self._add_batch(data, chunk, layer, scan)
                        chunk, count = [], 0
                if chunk:
                    self._add_batch(data, chunk, layer, scan)
        self._update_scene_rect()
        self.selectionChanged.emit([])
        self.viewport().update()

    def _add_batch(self, data: SceneData, idxs: list[int], layer: int, scan: bool) -> None:
        item = ContourBatchItem(data, idxs, layer, scan, self.style)
        item.setVisible(layer not in self.hidden_layers)
        self.scene().addItem(item)
        self._batches.append(item)

    @property
    def batch_count(self) -> int:
        """Number of batch items (for the responsiveness test)."""
        return len(self._batches)

    def set_bed(self, bed: tuple[float, float] | None) -> None:
        """Set (or hide with ``None``) the bed outline size in mm."""
        self.bed = bed
        self._apply_bed()
        self._update_scene_rect()

    def _apply_bed(self) -> None:
        if self.bed is None:
            self._bed_item.setVisible(False)
            return
        self._bed_item.setRect(QRectF(0.0, 0.0, self.bed[0], self.bed[1]))
        self._bed_item.setVisible(self.flags.show_bed)

    def set_flags(self, **changes: bool) -> None:
        """Change view toggles (``show_start``, ``show_arrows``, ``show_index``, ``show_bed``, ``show_rulers``)."""
        for k, v in changes.items():
            if not hasattr(self.flags, k):
                raise AttributeError(f"unknown view flag {k!r}")
            setattr(self.flags, k, bool(v))
        self._apply_bed()
        self.viewport().update()

    def set_layer_visible(self, layer: int, visible: bool) -> None:
        """Show/hide every contour of ``layer``."""
        if visible:
            self.hidden_layers.discard(layer)
        else:
            self.hidden_layers.add(layer)
        for item in self._batches:
            if item.layer == layer:
                item.setVisible(visible)
        self.viewport().update()

    def content_rect(self) -> QRectF:
        """Bounding rect of the geometry, else the bed, else 100 mm around the origin."""
        if self.scene_data is not None and self.scene_data.bbox is not None:
            x0, y0, x1, y1 = self.scene_data.bbox
            return QRectF(x0, y0, max(x1 - x0, 1e-3), max(y1 - y0, 1e-3))
        if self.bed is not None:
            return QRectF(0.0, 0.0, self.bed[0], self.bed[1])
        return QRectF(0.0, 0.0, 100.0, 100.0)

    def _update_scene_rect(self) -> None:
        r = self.content_rect()
        if self.bed is not None:
            r = r.united(QRectF(0.0, 0.0, self.bed[0], self.bed[1]))
        margin = max(r.width(), r.height(), 100.0) * 20.0
        margin = min(margin, 2.0e5)
        self.setSceneRect(r.adjusted(-margin, -margin, margin, margin))

    # -------------------------------------------------------------------- view
    def scale_factor(self) -> float:
        """Current zoom in device pixels per mm."""
        return self._scale

    def _set_scale(self, s: float) -> None:
        s = min(max(s, MIN_SCALE), MAX_SCALE)
        self._scale = s
        self.setTransform(QTransform(s, 0.0, 0.0, -s, 0.0, 0.0))

    def zoom_to_fit(self, rect: QRectF | None = None, margin: float = 0.05) -> None:
        """Fit ``rect`` (default: the geometry, ``mv1`` "Reset View") into the viewport."""
        r = rect or self.content_rect()
        vw = max(self.viewport().width(), 1)
        vh = max(self.viewport().height(), 1)
        w = max(r.width(), 1e-6) * (1 + 2 * margin)
        h = max(r.height(), 1e-6) * (1 + 2 * margin)
        self._set_scale(min(vw / w, vh / h))
        self.centerOn(r.center())
        self.viewChanged.emit()
        self.viewport().update()

    def zoom_at(self, factor: float, view_pos: QPointF | QPoint | None = None) -> None:
        """Zoom by ``factor`` keeping the scene point under ``view_pos`` (default: centre) fixed."""
        vp = self.viewport().rect()
        pos = QPointF(view_pos) if view_pos is not None else QPointF(vp.center())
        before = self.map_to_scene_f(pos)
        self._set_scale(self._scale * factor)
        after = self.map_to_scene_f(pos)
        centre = self.map_to_scene_f(QPointF(vp.center()))
        self.centerOn(centre + (before - after))
        self.viewChanged.emit()
        self.viewport().update()

    def pan_pixels(self, dx: float, dy: float) -> None:
        """Move the content by ``(dx, dy)`` device pixels (middle-drag)."""
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(h.value() - round(dx))
        v.setValue(v.value() - round(dy))
        self.viewChanged.emit()

    def map_to_scene_f(self, pos: QPointF | QPoint) -> QPointF:
        """Sub-pixel ``mapToScene`` (Qt's overload takes integer points)."""
        inv, ok = self.viewportTransform().inverted()
        return inv.map(QPointF(pos)) if ok else QPointF()

    def map_from_scene_f(self, pos: QPointF) -> QPointF:
        """Scene mm -> viewport pixels, sub-pixel."""
        return self.viewportTransform().map(pos)

    def visible_scene_rect(self) -> QRectF:
        """Scene rect currently covered by the viewport (normalised, Y-up)."""
        vp = self.viewport().rect()
        a = self.map_to_scene_f(QPointF(vp.topLeft()))
        b = self.map_to_scene_f(QPointF(vp.bottomRight()) + QPointF(1, 1))
        return QRectF(a, b).normalized()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        """Keep rulers in sync with scrolling."""
        super().scrollContentsBy(dx, dy)
        self.viewChanged.emit()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Keep rulers in sync with resizing."""
        super().resizeEvent(event)
        self.viewChanged.emit()

    # --------------------------------------------------------------- selection
    def set_tool(self, tool: str) -> None:
        """``select`` or ``measure`` (``mf116``)."""
        if tool not in self.TOOLS:
            raise ValueError(f"unknown tool {tool!r}")
        self.tool = tool
        self._measure_line.setVisible(False)
        self.setCursor(
            Qt.CursorShape.CrossCursor if tool == "measure" else Qt.CursorShape.ArrowCursor
        )

    def select(self, indices: Iterable[int], *, add: bool = False) -> None:
        """Set (or extend) the selection and redraw the highlight."""
        n = len(self.scene_data.contours) if self.scene_data is not None else 0
        new = [i for i in indices if 0 <= i < n]
        sel = list(dict.fromkeys([*self.selected, *new])) if add else list(dict.fromkeys(new))
        self.selected = sel
        path = QPainterPath()
        if self.scene_data is not None:
            for i in sel:
                for pl in self.scene_data.contours[i].polylines:
                    if len(pl) >= 2:
                        path.addPolygon(polygon_from_array(pl))
                    else:
                        path.addEllipse(QPointF(float(pl[0, 0]), float(pl[0, 1])), 0.2, 0.2)
        self._selection_item.setPath(path)
        self.selectionChanged.emit(list(sel))
        self.viewport().update()

    def toggle(self, index: int) -> None:
        """Ctrl-click behaviour: add or remove one contour."""
        if index in self.selected:
            self.select([i for i in self.selected if i != index])
        else:
            self.select([index], add=True)

    def clear_selection(self) -> None:
        """Deselect everything."""
        self.select([])

    def pick_at(self, view_pos: QPointF | QPoint) -> int | None:
        """Contour under a viewport position (tolerance :data:`PICK_TOLERANCE_PX`)."""
        if self.scene_data is None:
            return None
        p = self.map_to_scene_f(QPointF(view_pos))
        idx = self.scene_data.pick(p.x(), p.y(), PICK_TOLERANCE_PX / self._scale)
        if idx is not None and self.scene_data.contours[idx].layer in self.hidden_layers:
            return None
        return idx

    # ------------------------------------------------------------------ events
    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom around the cursor: 1.25x per wheel notch (port choice)."""
        steps = event.angleDelta().y() / 120.0
        if steps:
            self.zoom_at(1.25**steps, event.position())
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Middle: start pan; left: start click/box select or measurement."""
        pos = event.position()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_last = pos.toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = pos.toPoint()
            self._press_scene = self.map_to_scene_f(pos)
            if self.tool == "measure":
                p = self._press_scene
                self._measure_line.setLine(p.x(), p.y(), p.x(), p.y())
                self._measure_line.setVisible(True)
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton and self.tool == "measure":
            self.set_tool("select")
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Report the cursor in mm; update pan, rubber band or measurement."""
        pos = event.position()
        sp = self.map_to_scene_f(pos)
        self.cursorMoved.emit(sp.x(), sp.y())
        if self._pan_last is not None:
            cur = pos.toPoint()
            d = cur - self._pan_last
            self._pan_last = cur
            self.pan_pixels(d.x(), d.y())
            event.accept()
            return
        if self._press is not None and self._press_scene is not None:
            a = self._press_scene
            if self.tool == "measure":
                self._measure_line.setLine(a.x(), a.y(), sp.x(), sp.y())
                self._emit_measure(a, sp)
            elif (pos.toPoint() - self._press).manhattanLength() > 3:
                self._band.setRect(QRectF(a, sp).normalized())
                self._band.setVisible(True)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Finish pan, click selection, box selection or measurement."""
        pos = event.position()
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_last is not None:
            self._pan_last = None
            self.set_tool(self.tool)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None:
            a = self._press_scene or QPointF()
            b = self.map_to_scene_f(pos)
            moved = (pos.toPoint() - self._press).manhattanLength() > 3
            ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self._press = None
            self._press_scene = None
            if self.tool == "measure":
                self._emit_measure(a, b)
            elif moved:
                self._band.setVisible(False)
                if self.scene_data is not None:
                    hits = [
                        i
                        for i in self.scene_data.in_rect(a.x(), a.y(), b.x(), b.y())
                        if self.scene_data.contours[i].layer not in self.hidden_layers
                    ]
                    self.select(hits, add=ctrl)
            else:
                idx = self.pick_at(pos)
                if idx is None:
                    if not ctrl:
                        self.clear_selection()
                elif ctrl:
                    self.toggle(idx)
                else:
                    self.select([idx])
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _emit_measure(self, a: QPointF, b: QPointF) -> None:
        dx, dy = abs(b.x() - a.x()), abs(b.y() - a.y())
        self.measured.emit(dx, dy, math.hypot(dx, dy))


def _nice_step(min_mm: float) -> float:
    """Smallest 1/2/5 x 10^k step >= ``min_mm``."""
    if min_mm <= 0 or not math.isfinite(min_mm):
        return 1.0
    k = math.floor(math.log10(min_mm))
    for m in (1.0, 2.0, 5.0, 10.0):
        step = m * 10.0**k
        if step >= min_mm:
            return step
    return 10.0 ** (k + 1)


class RulerWidget(QWidget):
    """mm ruler along the top (horizontal) or left (vertical) edge of the canvas (``pd485``)."""

    THICKNESS = 22
    LABEL_SPACING_PX = 70.0

    def __init__(
        self, view: CanvasView, orientation: Qt.Orientation, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.view = view
        self.orientation = orientation
        view.viewChanged.connect(self.update)
        view.cursorMoved.connect(self._cursor)
        self._cursor_mm: float | None = None
        if orientation == Qt.Orientation.Horizontal:
            self.setFixedHeight(self.THICKNESS)
        else:
            self.setFixedWidth(self.THICKNESS)

    def _cursor(self, x: float, y: float) -> None:
        self._cursor_mm = x if self.orientation == Qt.Orientation.Horizontal else y
        self.update()

    def sizeHint(self) -> QSize:
        """Fixed thickness."""
        return QSize(self.THICKNESS, self.THICKNESS)

    def ticks(self) -> list[tuple[float, float, bool]]:
        """``(pixel, mm, major)`` for the visible range (used by the paint and the tests)."""
        v = self.view
        s = v.scale_factor()
        horizontal = self.orientation == Qt.Orientation.Horizontal
        length = v.viewport().width() if horizontal else v.viewport().height()
        major = _nice_step(self.LABEL_SPACING_PX / s)
        minor = major / 5.0 if major / 5.0 * s >= 6 else major / 2.0
        p0 = v.map_to_scene_f(QPointF(0, 0))
        p1 = v.map_to_scene_f(QPointF(length, length))
        lo, hi = (p0.x(), p1.x()) if horizontal else (p1.y(), p0.y())
        out: list[tuple[float, float, bool]] = []
        k = math.floor(lo / minor)
        limit = 2000
        while k * minor <= hi and limit:
            mm = k * minor
            pt = v.map_from_scene_f(QPointF(mm, 0.0) if horizontal else QPointF(0.0, mm))
            px = pt.x() if horizontal else pt.y()
            is_major = abs(mm / major - round(mm / major)) < 1e-6
            out.append((px, mm, is_major))
            k += 1
            limit -= 1
        return out

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw ticks, labels and the cursor mark."""
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#2b2b2b"))
        p.setPen(QColor("#c8c8c8"))
        font = QFont()
        font.setPixelSize(9)
        p.setFont(font)
        horizontal = self.orientation == Qt.Orientation.Horizontal
        t = self.THICKNESS
        for px, mm, major in self.ticks():
            ln = t * (0.5 if major else 0.25)
            if horizontal:
                p.drawLine(round(px), t, round(px), round(t - ln))
                if major:
                    p.drawText(round(px) + 2, 10, f"{mm:g}")
            else:
                p.drawLine(t, round(px), round(t - ln), round(px))
                if major:
                    p.save()
                    p.translate(10, round(px) - 2)
                    p.rotate(-90)
                    p.drawText(0, 0, f"{mm:g}")
                    p.restore()
        if self._cursor_mm is not None:
            v = self.view
            pt = v.map_from_scene_f(
                QPointF(self._cursor_mm, 0.0) if horizontal else QPointF(0.0, self._cursor_mm)
            )
            p.setPen(QColor("#ff5050"))
            if horizontal:
                p.drawLine(round(pt.x()), 0, round(pt.x()), t)
            else:
                p.drawLine(0, round(pt.y()), t, round(pt.y()))
        p.end()


class CanvasWidget(QWidget):
    """Canvas view framed by the two rulers."""

    def __init__(self, parent: QWidget | None = None, **view_kwargs: object) -> None:
        super().__init__(parent)
        self.view = CanvasView(self, **view_kwargs)  # type: ignore[arg-type]
        self.h_ruler = RulerWidget(self.view, Qt.Orientation.Horizontal, self)
        self.v_ruler = RulerWidget(self.view, Qt.Orientation.Vertical, self)
        self.corner = QWidget(self)
        self.corner.setFixedSize(RulerWidget.THICKNESS, RulerWidget.THICKNESS)
        self.corner.setStyleSheet("background:#2b2b2b")
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self.corner, 0, 0)
        grid.addWidget(self.h_ruler, 0, 1)
        grid.addWidget(self.v_ruler, 1, 0)
        grid.addWidget(self.view, 1, 1)
        self.set_rulers_visible(self.view.flags.show_rulers)

    def set_rulers_visible(self, visible: bool) -> None:
        """``IGP.EnableRuler`` toggle."""
        self.view.flags.show_rulers = bool(visible)
        for w in (self.h_ruler, self.v_ruler, self.corner):
            w.setVisible(bool(visible))
