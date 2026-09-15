"""Offscreen rendering of a document to an image, shared painting code of the canvas.

Used by the M2 gate tests (PORT-PLAN §4 M2: files "render identically to
``tools/out/*.png``") and by ``nexcut render <file> -o out.png``.

Framing reproduces ``tools/chf_parse.py`` ``to_png`` (the reference renders):
the bbox of all flattened points, ``scale = (width - 2*pad) / max(w, h)``,
image ``width x (int(h*scale) + 2*pad)``, pixel ``round(pad + (x-minx)*scale)``
/ ``round(H - pad - (y-miny)*scale)`` (Y up, 03 §10), 1-px aliased lines on
white, scan paths grey, point glyphs as 5-px crosses.  Markers (start points,
direction arrows, index numbers - ``GRP.IsShowStartPt/IsShowDirArrow/IsShowNO``,
01 §1.2) are drawn in device pixels so they keep their size at any zoom; their
look is UNVERIFIED (no vendor screenshot).

Requires a ``QGuiApplication`` only for text (index numbers);
:func:`ensure_gui_app` creates an offscreen one when none exists.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTransform,
)

from nexcut.model.graph import ChfDocument
from nexcut.ui.layers import LAYER_COLORS, SCANPATH_COLOR, layer_color
from nexcut.ui.scene import SceneData, build_scene

__all__ = [
    "DEFAULT_BED",
    "Frame",
    "PaintStyle",
    "RenderOptions",
    "ViewFlags",
    "add_polylines",
    "ensure_gui_app",
    "layer_paths",
    "main",
    "paint_markers",
    "paint_scene_lines",
    "polygon_from_array",
    "reference_frame",
    "render_document",
    "render_file",
    "render_scene",
]

DEFAULT_BED = (1300.0, 900.0)
"""Bed outline ``(x, y)`` mm used when no hardware XML is given - CF1390 nameplate
size, UNVERIFIED as a vendor parameter (the soft limits ``MAC.SoftLimitMaxLen`` /
``MAC_1.SoftLimitMaxLen`` in ``BkHardPara.xml`` are 1371 / 950, 01 §2.2)."""


def ensure_gui_app() -> QGuiApplication:
    """Return the running Qt application, creating an offscreen one if needed.

    A ``QApplication`` (widgets) is created when ``QtWidgets`` is importable, so
    the main window can still be built later in the same process.
    """
    app = QGuiApplication.instance()
    if app is None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PySide6.QtWidgets import QApplication

            app = QApplication(sys.argv[:1])
        except ImportError:  # pragma: no cover - QtWidgets ships with PySide6
            app = QGuiApplication(sys.argv[:1])
    return app  # type: ignore[return-value]


_FAST_POLY: bool | None = None

try:  # imported once: a function-local import costs ~10 µs per call under PySide6's import hook
    import shiboken6
except ImportError:  # pragma: no cover - ships with PySide6
    shiboken6 = None  # type: ignore[assignment]


def add_polylines(path: QPainterPath, polylines: Iterable[np.ndarray]) -> None:
    """Append polylines (``(n, 2)`` arrays, n >= 2) to ``path`` as separate sub-paths.

    Two-point polylines (segments) use ``moveTo``/``lineTo`` - much cheaper than
    building a ``QPolygonF`` per segment (50 000-segment review benchmark).
    """
    for pl in polylines:
        n = len(pl)
        if n == 2:
            (x0, y0), (x1, y1) = pl.tolist()
            path.moveTo(x0, y0)
            path.lineTo(x1, y1)
        elif n > 2:
            path.addPolygon(polygon_from_array(pl))


def polygon_from_array(arr: np.ndarray) -> QPolygonF:
    """Build a ``QPolygonF`` from an ``(n, 2)`` float array.

    Fast path: copy the doubles straight into the polygon buffer (qreal is
    double on every desktop Qt 6 build); verified once against the slow path.
    """
    global _FAST_POLY
    a = np.ascontiguousarray(arr, dtype=np.float64)
    n = len(a)
    if _FAST_POLY is not False and n and shiboken6 is not None:
        try:
            poly = QPolygonF()
            poly.resize(n)
            addr = shiboken6.getCppPointer(poly.data())[0]
            ctypes.memmove(addr, a.ctypes.data, a.nbytes)
            if _FAST_POLY is None:
                last = poly.at(n - 1)
                _FAST_POLY = (last.x(), last.y()) == (float(a[-1, 0]), float(a[-1, 1]))
            if _FAST_POLY:
                return poly
        except (ImportError, TypeError, ValueError, AttributeError):
            _FAST_POLY = False
    return QPolygonF([QPointF(float(x), float(y)) for x, y in a])


@dataclass(slots=True)
class ViewFlags:
    """View toggles (``GRP.IsShow*``, ``IGP.EnableRuler``, 01 §1.2 rows 669-674/846).

    Defaults are the descriptor defaults: start point 1, arrows 1, index 0,
    rulers 1.  ``show_bed`` is a port addition.
    """

    show_start: bool = True
    show_arrows: bool = True
    show_index: bool = False
    show_rulers: bool = True
    show_bed: bool = True

    @classmethod
    def from_manu(cls, doc: object) -> ViewFlags:
        """Read ``GRP.IsShowStartPt/IsShowDirArrow/IsShowNO`` and ``IGP.EnableRuler`` from a manu ParamDocument."""
        get = doc.get  # type: ignore[attr-defined]
        return cls(
            show_start=bool(get("PGraphParam", "GRP", "IsShowStartPt")),
            show_arrows=bool(get("PGraphParam", "GRP", "IsShowDirArrow")),
            show_index=bool(get("PGraphParam", "GRP", "IsShowNO")),
            show_rulers=bool(get("PImportGraphParam", "IGP", "EnableRuler")),
        )


@dataclass(slots=True)
class PaintStyle:
    """Colours and marker sizes (all UNVERIFIED look, see module docstring)."""

    palette: list[str] = field(default_factory=lambda: list(LAYER_COLORS))
    scan_color: str = SCANPATH_COLOR
    background: str = "#ffffff"
    bed_color: str = "#8c8c8c"
    selection_color: str = "#ffffff"
    start_color: str = "#00c000"
    arrow_color: str = "#e0e000"
    index_color: str = "#0090ff"
    start_radius_px: float = 3.0
    arrow_px: float = 9.0
    point_cross_px: int = 2
    font_px: int = 11

    def layer_qcolor(self, layer: int, scan: bool = False) -> QColor:
        """Pen colour of a contour."""
        return QColor(self.scan_color if scan else layer_color(layer, self.palette))


def layer_paths(
    scene: SceneData, indices: Iterable[int] | None = None
) -> dict[tuple[int, bool], QPainterPath]:
    """One ``QPainterPath`` per ``(layer, is_scan_path)`` holding every polyline (>= 2 points)."""
    paths: dict[tuple[int, bool], QPainterPath] = {}
    sel = range(len(scene.contours)) if indices is None else indices
    for i in sel:
        cv = scene.contours[i]
        key = (cv.layer, cv.is_scan_path)
        path = paths.get(key)
        if path is None:
            path = paths[key] = QPainterPath()
        add_polylines(path, cv.polylines)
    return paths


def paint_scene_lines(
    painter: QPainter,
    paths: dict[tuple[int, bool], QPainterPath],
    style: PaintStyle,
    *,
    color: QColor | None = None,
    width: float = 0.0,
) -> None:
    """Stroke prepared layer paths with cosmetic pens in the painter's current (mm) transform."""
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for (layer, scan), path in paths.items():
        pen = QPen(color if color is not None else style.layer_qcolor(layer, scan))
        pen.setCosmetic(True)
        pen.setWidthF(width)
        painter.setPen(pen)
        painter.drawPath(path)


def _map_xy(world: QTransform, xy: np.ndarray) -> np.ndarray:
    """Map ``(n, 2)`` scene points to device pixels with ``world`` (affine or projective)."""
    x, y = xy[:, 0], xy[:, 1]
    px = world.m11() * x + world.m21() * y + world.m31()
    py = world.m12() * x + world.m22() * y + world.m32()
    if not world.isAffine():
        w = world.m13() * x + world.m23() * y + world.m33()
        px, py = px / w, py / w
    return np.column_stack((px, py))


def _clip_mask(q: np.ndarray, clip: QRectF | None, margin: float) -> np.ndarray:
    ok = np.isfinite(q).all(axis=1)
    if clip is not None:
        ok &= (
            (q[:, 0] >= clip.left() - margin)
            & (q[:, 0] <= clip.right() + margin)
            & (q[:, 1] >= clip.top() - margin)
            & (q[:, 1] <= clip.bottom() + margin)
        )
    return ok


def paint_markers(
    painter: QPainter,
    scene: SceneData,
    world: QTransform,
    flags: ViewFlags,
    style: PaintStyle,
    *,
    clip: QRectF | None = None,
    indices: Sequence[int] | None = None,
    hidden_layers: set[int] | None = None,
) -> None:
    """Draw point glyphs, start points, direction arrows and index numbers in device pixels.

    ``world`` maps scene mm to device pixels; ``clip`` (device rect) skips
    off-screen markers.  The painter transform is reset for the duration.
    Vectorised over :class:`SceneData` arrays and drawn with one call per marker
    kind (import-ui fidelity review: the per-contour loop took 2 s for 50 000
    contours).
    """
    n = len(scene.contours)
    if n == 0:
        return
    painter.save()
    painter.resetTransform()
    try:
        sel = np.zeros(n, dtype=bool)
        if indices is None:
            sel[:] = True
        else:
            idx = np.fromiter((int(i) for i in indices), dtype=np.int64)
            sel[idx[(idx >= 0) & (idx < n)]] = True
        if hidden_layers:
            sel &= ~np.isin(scene.layers, np.fromiter(hidden_layers, dtype=np.int64))
        c = style.point_cross_px
        if len(scene.points):
            own = scene.point_owner
            keep = sel[own]
            q = _map_xy(world, scene.points[keep])
            vis = _clip_mask(q, clip, c + 1)
            owners = own[keep][vis]
            q = np.round(q[vis] - 0.5)
            for key in sorted({(int(scene.layers[o]), bool(scene.scan[o])) for o in owners}):
                m = (scene.layers[owners] == key[0]) & (scene.scan[owners] == key[1])
                painter.setPen(QPen(style.layer_qcolor(*key), 0))
                lines: list[QLineF] = []
                for x, y in q[m].tolist():
                    lines.append(QLineF(x - c, y, x + c, y))
                    lines.append(QLineF(x, y - c, x, y + c))
                painter.drawLines(lines)
        marked = sel & ~scene.scan
        if flags.show_start:
            r = style.start_radius_px
            q = _map_xy(world, scene.starts[marked])
            q = q[_clip_mask(q, clip, r + 1)]
            if len(q):
                pen = QPen(QColor(style.start_color), 2.0 * r + 1.0)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawPoints(polygon_from_array(q))
        if flags.show_arrows:
            arr = scene.arrows[marked]
            q = _map_xy(world, arr[:, :2])
            tipd = _map_xy(world, arr[:, :2] + arr[:, 2:]) - q
            norm = np.hypot(tipd[:, 0], tipd[:, 1])
            ok = _clip_mask(q, clip, style.arrow_px) & (norm > 0)
            if ok.any():
                q, tipd, norm = q[ok], tipd[ok], norm[ok]
                dx, dy = tipd[:, 0] / norm, tipd[:, 1] / norm
                a = style.arrow_px
                tx, ty = q[:, 0] + dx * a / 2, q[:, 1] + dy * a / 2
                bx, by = q[:, 0] - dx * a / 2, q[:, 1] - dy * a / 2
                lx, ly = bx - dy * a * 0.4, by + dx * a * 0.4
                rx, ry = bx + dy * a * 0.4, by - dx * a * 0.4
                pts = np.column_stack((lx, ly, tx, ty, rx, ry, tx, ty)).tolist()
                lines = []
                for v in pts:
                    lines.append(QLineF(v[0], v[1], v[2], v[3]))
                    lines.append(QLineF(v[4], v[5], v[6], v[7]))
                painter.setPen(QPen(QColor(style.arrow_color), 0))
                painter.drawLines(lines)
        if flags.show_index:
            font = QFont()
            font.setPixelSize(style.font_px)
            painter.setFont(font)
            painter.setPen(QColor(style.index_color))
            has_start = marked & np.isfinite(scene.starts).all(axis=1)
            cand = np.flatnonzero(has_start)
            # first contour of each graph that has a start point labels the graph
            _, first = np.unique(scene.graph_numbers[cand], return_index=True)
            cand = cand[np.sort(first)]
            q = _map_xy(world, scene.starts[cand])
            vis = _clip_mask(q, clip, 40)
            for (x, y), gnum in zip(
                q[vis].tolist(), scene.graph_numbers[cand][vis].tolist(), strict=True
            ):
                painter.drawText(QPointF(x + 4, y - 4), str(gnum))
    finally:
        painter.restore()


@dataclass(slots=True)
class Frame:
    """Image size and mm -> pixel mapping of a reference-style render."""

    width: int
    height: int
    minx: float
    miny: float
    scale: float
    pad: int

    def transform(self) -> QTransform:
        """mm -> pixel; the +0.5 makes Qt's aliased rasteriser round like the reference tool."""
        s = self.scale
        return QTransform(
            s,
            0.0,
            0.0,
            -s,
            self.pad - self.minx * s + 0.5,
            self.height - self.pad + self.miny * s + 0.5,
        )


def reference_frame(
    bbox: tuple[float, float, float, float] | None, width: int = 800, pad: int = 24
) -> Frame:
    """Framing of ``tools/chf_parse.py`` ``to_png`` (module docstring)."""
    if bbox is None:
        bbox = (0.0, 0.0, 10.0, 10.0)
    minx, miny, maxx, maxy = bbox
    w, h = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
    scale = (width - 2 * pad) / max(w, h)
    return Frame(width, int(h * scale) + 2 * pad, minx, miny, scale, pad)


@dataclass(slots=True)
class RenderOptions:
    """Options of :func:`render_scene`.  Defaults reproduce the reference renders."""

    width: int = 800
    pad: int = 24
    flags: ViewFlags = field(
        default_factory=lambda: ViewFlags(
            show_start=False, show_arrows=False, show_index=False, show_bed=False
        )
    )
    style: PaintStyle = field(default_factory=PaintStyle)
    bed: tuple[float, float] | None = None
    """Bed size to outline (origin at 0,0) when ``flags.show_bed``; included in the framing."""
    antialias: bool = False
    selected: Sequence[int] = ()


def render_scene(scene: SceneData, options: RenderOptions | None = None) -> QImage:
    """Render a scene into a new RGB32 image (module docstring)."""
    opts = options or RenderOptions()
    bbox = scene.bbox
    if opts.flags.show_bed and opts.bed is not None:
        bx, by = opts.bed
        bbox = (
            (0.0, 0.0, bx, by)
            if bbox is None
            else (min(bbox[0], 0.0), min(bbox[1], 0.0), max(bbox[2], bx), max(bbox[3], by))
        )
    frame = reference_frame(bbox, opts.width, opts.pad)
    if opts.flags.show_index:
        ensure_gui_app()
    img = QImage(frame.width, frame.height, QImage.Format.Format_RGB32)
    img.fill(QColor(opts.style.background))
    painter = QPainter(img)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, opts.antialias)
        world = frame.transform()
        painter.setTransform(world)
        if opts.flags.show_bed and opts.bed is not None:
            pen = QPen(QColor(opts.style.bed_color))
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawRect(QRectF(0.0, 0.0, opts.bed[0], opts.bed[1]))
        paint_scene_lines(painter, layer_paths(scene), opts.style)
        if opts.selected:
            paint_scene_lines(
                painter,
                layer_paths(scene, opts.selected),
                opts.style,
                color=QColor(opts.style.selection_color),
                width=2.0,
            )
        paint_markers(painter, scene, world, opts.flags, opts.style)
    finally:
        painter.end()
    return img


def render_document(doc: ChfDocument, options: RenderOptions | None = None) -> QImage:
    """:func:`build_scene` + :func:`render_scene`."""
    return render_scene(build_scene(doc), options)


def render_file(
    path: str | os.PathLike[str],
    out: str | os.PathLike[str],
    options: RenderOptions | None = None,
    *,
    apply_gates: bool = True,
) -> Path:
    """Open any supported file (:func:`nexcut.ui.loader.load_document`) and write a PNG."""
    from nexcut.ui.loader import load_document

    loaded = load_document(path, apply_gates=apply_gates)
    img = render_document(loaded.document, options)
    target = Path(out)
    if not img.save(str(target), "PNG"):
        raise OSError(f"cannot write {target}")
    return target


def build_parser(prog: str = "nexcut render") -> argparse.ArgumentParser:
    """Argument parser of the ``render`` sub-command."""
    p = argparse.ArgumentParser(
        prog=prog, description="Render a job file (.chf/.dxf/.plt/.nc) to PNG, offscreen."
    )
    p.add_argument("file", type=Path)
    p.add_argument("-o", "--output", type=Path, required=True, help="output PNG path")
    p.add_argument("--width", type=int, default=800)
    p.add_argument("--pad", type=int, default=24)
    p.add_argument("--start", action="store_true", help="draw start points (GRP.IsShowStartPt)")
    p.add_argument(
        "--arrows", action="store_true", help="draw direction arrows (GRP.IsShowDirArrow)"
    )
    p.add_argument("--index", action="store_true", help="draw index numbers (GRP.IsShowNO)")
    p.add_argument("--bed", action="store_true", help="draw the 1300x900 bed outline")
    p.add_argument("--background", default="#ffffff")
    p.add_argument("--antialias", action="store_true")
    p.add_argument(
        "--no-gates", action="store_true", help="skip the IGP import clean-up for DXF/PLT/G-code"
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """``nexcut render FILE -o OUT.png``; returns a process exit code."""
    args = build_parser().parse_args(argv)
    ensure_gui_app()
    opts = RenderOptions(
        width=args.width,
        pad=args.pad,
        flags=ViewFlags(
            show_start=args.start,
            show_arrows=args.arrows,
            show_index=args.index,
            show_rulers=False,
            show_bed=args.bed,
        ),
        style=PaintStyle(background=args.background),
        bed=DEFAULT_BED if args.bed else None,
        antialias=args.antialias,
    )
    from nexcut.ui.loader import LoadError

    try:
        out = render_file(args.file, args.output, opts, apply_gates=not args.no_gates)
    except (LoadError, OSError) as exc:
        print(f"nexcut render: {exc}", file=sys.stderr)
        return 1
    print(out)
    return 0


# ---------------------------------------------------------------------------
# Image comparison (M2 gate metric)
# ---------------------------------------------------------------------------


def image_array(img: QImage) -> np.ndarray:
    """``(h, w, 3)`` uint8 RGB copy of a ``QImage``."""
    im = img.convertToFormat(QImage.Format.Format_RGB32)
    w, h, bpl = im.width(), im.height(), im.bytesPerLine()
    buf = np.frombuffer(im.constBits(), dtype=np.uint8, count=bpl * h).reshape(h, bpl)
    bgra = buf[:, : w * 4].reshape(h, w, 4)
    return bgra[:, :, 2::-1].copy()


def ink_mask(img: QImage, background: str = "#ffffff", threshold: int = 16) -> np.ndarray:
    """Boolean mask of pixels differing from ``background`` by more than ``threshold`` in any channel."""
    arr = image_array(img).astype(np.int16)
    bg = QColor(background)
    ref = np.array([bg.red(), bg.green(), bg.blue()], dtype=np.int16)
    return np.abs(arr - ref).max(axis=2) > threshold


def _grid(mask: np.ndarray, gw: int, gh: int) -> np.ndarray:
    ys, xs = np.nonzero(mask)
    out = np.zeros((gh, gw), dtype=bool)
    if len(xs) == 0:
        return out
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    cx = np.minimum(((xs - x0) * gw) // max(x1 - x0 + 1, 1), gw - 1)
    cy = np.minimum(((ys - y0) * gh) // max(y1 - y0 + 1, 1), gh - 1)
    out[cy, cx] = True
    return out


def aligned_iou(a: np.ndarray, b: np.ndarray, cells: int = 64) -> float:
    """IoU of two ink masks after aligning their ink bounding boxes and binarised downscaling.

    Each mask is cropped to its ink bbox and mapped onto the same grid
    (``cells`` on the longer side of ``b``, aspect from ``b``); a cell is set when
    any pixel in it has ink.  Two empty masks score 1.0.
    """
    ys, xs = np.nonzero(b)
    if len(xs) == 0:
        return 1.0 if not a.any() else 0.0
    bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    if bw >= bh:
        gw, gh = cells, max(1, round(cells * bh / bw))
    else:
        gw, gh = max(1, round(cells * bw / bh)), cells
    ga, gb = _grid(a, gw, gh), _grid(b, gw, gh)
    union = np.logical_or(ga, gb).sum()
    return float(np.logical_and(ga, gb).sum() / union) if union else 1.0


def chamfer_score(a: np.ndarray, b: np.ndarray, tol_px: float = 2.0) -> float:
    """Symmetric ink agreement of two masks in the *same* pixel frame (no alignment).

    ``min(recall, precision)``: the fraction of ``b``'s ink within ``tol_px`` of
    ``a``'s ink and vice versa.  Unlike :func:`aligned_iou` (bbox-aligned, 64-cell
    binarised grid) it is sensitive to mirroring, rotation and offsets, so a render
    in a Y-down frame fails against an asymmetric reference (import-ui fidelity
    review: every vendor sample except ``autosave.chf`` is mirror-symmetric and
    ``aligned_iou`` accepts a 180-degree-rotated ``autosave`` render).  Masks of
    different sizes are compared on their common top-left-anchored canvas.  Two
    empty masks score 1.0.
    """
    from scipy import ndimage

    h, w = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
    pa = np.zeros((h, w), dtype=bool)
    pb = np.zeros((h, w), dtype=bool)
    pa[: a.shape[0], : a.shape[1]] = a
    pb[: b.shape[0], : b.shape[1]] = b
    if not pa.any() or not pb.any():
        return 1.0 if not pa.any() and not pb.any() else 0.0
    da = ndimage.distance_transform_edt(~pa)
    db = ndimage.distance_transform_edt(~pb)
    recall = float((da[pb] <= tol_px).mean())
    precision = float((db[pa] <= tol_px).mean())
    return min(recall, precision)
