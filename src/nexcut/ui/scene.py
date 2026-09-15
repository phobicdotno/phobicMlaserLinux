"""Display geometry of a document: flattened contours, markers and picking (Qt-free).

The canvas (:mod:`nexcut.ui.canvas`) and the offscreen renderer
(:mod:`nexcut.ui.render`) both draw a :class:`SceneData`, so what the tests
compare is what the window shows.

* Flattening uses the display chord step of ``tools/chf_parse.py``
  (:data:`nexcut.model.flatten.DEFAULT_STEP`, 0.2 mm; 03 §11).  Segments,
  arcs and circles are sampled here with numpy using exactly the sample count
  formula of :func:`nexcut.model.flatten._arc_pts`; other glyph types go
  through :func:`nexcut.model.flatten.flatten_glyph`.
* Frame: millimetres, Y up (03 §10).
* Contour order is document order: graphs in cut order (03 §5), children of
  groups/scans/text as :func:`nexcut.model.graph.iter_contours` yields them.
* Start point and direction use the per-element direction flags (03 §6.1
  line 10).  Where the direction arrow sits (here: halfway along the path) and
  which number "Show index" (``mf40``) prints (here: the 1-based graph number,
  as the reference SVG labels) are UNVERIFIED port choices.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field

import numpy as np

from nexcut.model.flatten import DEFAULT_STEP, flatten_glyph
from nexcut.model.glyph import ArcGlyph, CircleGlyph, Glyph, PointGlyph, SegmentGlyph, SplineGlyph
from nexcut.model.graph import ChfDocument, Contour, ContourKind, Graph, iter_contours

__all__ = ["ContourView", "SceneData", "build_scene", "flatten_glyph_np"]

_MAX_ARC_SAMPLES = 20000  # cap of model.flatten._arc_pts


def _arc_np(cx: float, cy: float, r: float, a0: float, a1: float, step: float) -> np.ndarray:
    """Vectorised twin of ``model.flatten._arc_pts`` (same ``n``, same parameters)."""
    sweep = a1 - a0
    n = max(8, int(abs(sweep) * max(r, 1.0) / step) + 1)
    n = min(n, _MAX_ARC_SAMPLES)
    t = a0 + sweep * np.arange(n + 1, dtype=np.float64) / n
    return np.column_stack((cx + r * np.cos(t), cy + r * np.sin(t)))


def _spline_np(glyph: SplineGlyph, step: float) -> np.ndarray | None:
    """Dense spline samples when the model's ``8 * n_ctrl`` rule is coarser than ``step``.

    :func:`nexcut.model.flatten._bspline_pts` ignores the chord step (24 or 8 samples
    per control point), which left 0.5 mm chord error on a 1 m, 4-point spline at the
    0.2 mm display step (import-ui fidelity review).  Here the sample count follows
    the control-polygon length / ``step`` (same domain ``[t_3, t_n]``, 03 §7 type 7);
    ``None`` means the model sampling is already fine enough (and is kept verbatim).
    """
    ctrl = np.asarray(glyph.control_points, dtype=np.float64).reshape(-1, 2)
    m = len(ctrl)
    if m < 4 or len(glyph.knots) != m + 4:
        return None
    poly_len = float(np.hypot(*np.diff(ctrl, axis=0).T).sum())
    n = min(_MAX_ARC_SAMPLES, int(poly_len / max(step, 1e-9)) + 1)
    if n <= max(24, 8 * m):
        return None
    knots = np.asarray(glyph.knots, dtype=np.float64)
    lo, hi = knots[3], knots[m]
    if not (np.all(np.diff(knots) >= 0) and hi > lo):
        return None
    from scipy.interpolate import BSpline

    u = np.linspace(lo, hi, n + 1)
    return np.asarray(BSpline(knots, ctrl, 3, extrapolate=False)(u), dtype=np.float64)


def flatten_glyph_np(glyph: Glyph, step: float = DEFAULT_STEP) -> list[np.ndarray]:
    """Polylines ``(n, 2)`` of a glyph in its stored direction (03 §7)."""
    if isinstance(glyph, SegmentGlyph):
        return [
            np.array([[glyph.p0[0], glyph.p0[1]], [glyph.p1[0], glyph.p1[1]]], dtype=np.float64)
        ]
    if isinstance(glyph, ArcGlyph):
        return [
            _arc_np(
                glyph.center[0],
                glyph.center[1],
                glyph.radius,
                glyph.start_angle,
                glyph.end_angle,
                step,
            )
        ]
    if isinstance(glyph, CircleGlyph):
        return [_arc_np(glyph.center[0], glyph.center[1], glyph.radius, 0.0, 2 * math.pi, step)]
    if isinstance(glyph, SplineGlyph):
        dense = _spline_np(glyph, step)
        if dense is not None:
            return [dense]
    return [np.asarray(pl, dtype=np.float64).reshape(-1, 2) for pl in flatten_glyph(glyph, step)]


@dataclass(slots=True)
class ContourView:
    """One contour as displayed."""

    index: int
    """0-based position in :attr:`SceneData.contours` (document order)."""
    graph_index: int
    """1-based number of the owning graph (``####graph NO:``, 03 §5)."""
    kind: ContourKind
    contour: Contour
    graph: Graph
    polylines: list[np.ndarray]
    """Flattened glyphs, each ``(n, 2)``; a point glyph is a ``(1, 2)`` array."""
    bbox: tuple[float, float, float, float]
    """``(minx, miny, maxx, maxy)`` of the flattened geometry."""
    start: tuple[float, float] | None
    """First point along the cut direction (direction flags applied)."""
    arrow: tuple[float, float, float, float] | None
    """``(x, y, ux, uy)``: point halfway along the path and the unit travel direction there."""
    length: float

    @property
    def layer(self) -> int:
        """Layer index of the contour (03 §6.1 line 14)."""
        return self.contour.layer

    @property
    def is_scan_path(self) -> bool:
        """True for generated scan paths (drawn grey, 03 §6.4)."""
        return self.kind == "scanpath"

    @property
    def point_count(self) -> int:
        """Number of flattened vertices."""
        return sum(len(p) for p in self.polylines)


@dataclass(slots=True)
class SceneData:
    """All display geometry of a document plus picking arrays."""

    contours: list[ContourView] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None
    seg_start: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    seg_end: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    seg_owner: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    points: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    point_owner: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    boxes: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))
    starts: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    """Per contour start point (NaN when none) - vectorised marker painting."""
    arrows: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))
    """Per contour ``(x, y, ux, uy)`` direction arrow (NaN when none)."""
    layers: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    scan: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    graph_numbers: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))

    @property
    def glyph_count(self) -> int:
        """Total number of glyphs of all contours."""
        return sum(len(cv.contour.elements) for cv in self.contours)

    def layers_used(self) -> dict[int, int]:
        """``{layer: contour count}`` over non-scan-path contours."""
        out: dict[int, int] = {}
        for cv in self.contours:
            if not cv.is_scan_path:
                out[cv.layer] = out.get(cv.layer, 0) + 1
        return dict(sorted(out.items()))

    def pick(self, x: float, y: float, tol: float) -> int | None:
        """Index of the contour closest to ``(x, y)`` within ``tol`` mm, else ``None``."""
        if not self.contours:
            return None
        b = self.boxes
        near = (
            (b[:, 0] - tol <= x)
            & (x <= b[:, 2] + tol)
            & (b[:, 1] - tol <= y)
            & (y <= b[:, 3] + tol)
        )
        if not near.any():
            return None
        best: tuple[float, int] | None = None
        cand = np.flatnonzero(near)
        mask = np.isin(self.seg_owner, cand)
        if mask.any():
            a = self.seg_start[mask]
            d = self.seg_end[mask] - a
            p = np.array([x, y]) - a
            ll = np.einsum("ij,ij->i", d, d)
            t = np.where(ll > 0, np.einsum("ij,ij->i", p, d) / np.where(ll > 0, ll, 1.0), 0.0)
            t = np.clip(t, 0.0, 1.0)
            diff = p - d * t[:, None]
            dist = np.hypot(diff[:, 0], diff[:, 1])
            k = int(np.argmin(dist))
            best = (float(dist[k]), int(self.seg_owner[mask][k]))
        pmask = np.isin(self.point_owner, cand)
        if pmask.any():
            pts = self.points[pmask]
            dist = np.hypot(pts[:, 0] - x, pts[:, 1] - y)
            k = int(np.argmin(dist))
            if best is None or dist[k] < best[0]:
                best = (float(dist[k]), int(self.point_owner[pmask][k]))
        if best is None or best[0] > tol:
            return None
        return best[1]

    def in_rect(
        self, x0: float, y0: float, x1: float, y1: float, *, contained: bool = True
    ) -> list[int]:
        """Contours whose bbox lies inside (or, ``contained=False``, touches) the rectangle."""
        if not self.contours:
            return []
        lx, hx = min(x0, x1), max(x0, x1)
        ly, hy = min(y0, y1), max(y0, y1)
        b = self.boxes
        if contained:
            m = (b[:, 0] >= lx) & (b[:, 2] <= hx) & (b[:, 1] >= ly) & (b[:, 3] <= hy)
        else:
            m = (b[:, 2] >= lx) & (b[:, 0] <= hx) & (b[:, 3] >= ly) & (b[:, 1] <= hy)
        return [int(i) for i in np.flatnonzero(m)]


def _travel(contour: Contour, polys: list[list[np.ndarray]]) -> np.ndarray:
    """Concatenate element polylines along the cut direction (03 §6.1 line 10)."""
    parts: list[np.ndarray] = []
    for el, pls in zip(contour.elements, polys, strict=True):
        for pl in pls:
            parts.append(pl[::-1] if el.direction == -1 else pl)
    if not parts:
        return np.zeros((0, 2))
    return np.concatenate(parts, axis=0)


def _arrow(path: np.ndarray) -> tuple[tuple[float, float, float, float] | None, float]:
    if len(path) < 2:
        return None, 0.0
    d = np.diff(path, axis=0)
    seg = np.hypot(d[:, 0], d[:, 1])
    total = float(seg.sum())
    if total <= 0.0:
        return None, 0.0
    cum = np.cumsum(seg)
    half = total / 2.0
    k = int(np.searchsorted(cum, half))
    k = min(k, len(seg) - 1)
    while k < len(seg) - 1 and seg[k] == 0.0:
        k += 1
    before = cum[k] - seg[k]
    f = (half - before) / seg[k] if seg[k] > 0 else 0.0
    px, py = path[k] + d[k] * f
    ux, uy = d[k] / seg[k] if seg[k] > 0 else (1.0, 0.0)
    return (float(px), float(py), float(ux), float(uy)), total


SMALL_PATH = 64
"""Contours with at most this many flattened vertices are measured in pure Python.

Port performance choice (import-ui fidelity review): per-contour numpy calls cost
~50 µs, which made a 50 000-segment drawing take 2.7 s to build.
"""


def _arrow_small(path: list[list[float]]) -> tuple[tuple[float, float, float, float] | None, float]:
    """Pure-Python twin of :func:`_arrow` for short paths (same rule, same results to ~1 ulp)."""
    n = len(path)
    if n < 2:
        return None, 0.0
    segs: list[float] = []
    cum: list[float] = []
    acc = 0.0
    for i in range(n - 1):
        ln = math.hypot(path[i + 1][0] - path[i][0], path[i + 1][1] - path[i][1])
        segs.append(ln)
        acc += ln
        cum.append(acc)
    total = acc
    if total <= 0.0:
        return None, 0.0
    half = total / 2.0
    k = bisect_left(cum, half)
    k = min(k, len(segs) - 1)
    while k < len(segs) - 1 and segs[k] == 0.0:
        k += 1
    sk = segs[k]
    x0, y0 = path[k]
    dx, dy = path[k + 1][0] - x0, path[k + 1][1] - y0
    if sk > 0:
        f = (half - (cum[k] - sk)) / sk
        return (x0 + dx * f, y0 + dy * f, dx / sk, dy / sk), total
    return (x0, y0, 1.0, 0.0), total


def build_scene(doc: ChfDocument, step: float = DEFAULT_STEP) -> SceneData:
    """Flatten every contour of ``doc`` into a :class:`SceneData` (module docstring)."""
    scene = SceneData()
    polys_all: list[np.ndarray] = []
    poly_owner: list[int] = []
    p_pts: list[np.ndarray] = []
    p_owner: list[int] = []
    boxes: list[tuple[float, float, float, float]] = []
    starts: list[tuple[float, float]] = []
    arrows: list[tuple[float, float, float, float]] = []
    nan2 = (math.nan, math.nan)
    nan4 = (math.nan, math.nan, math.nan, math.nan)
    for gi, g in enumerate(doc.graphs, 1):
        for contour, kind in iter_contours(g):
            idx = len(scene.contours)
            per_el = [flatten_glyph_np(el.glyph, step) for el in contour.elements]
            polylines = [pl for pls in per_el for pl in pls]
            npts = sum(len(pl) for pl in polylines)
            start: tuple[float, float] | None
            if 0 < npts <= SMALL_PATH:
                travel: list[list[float]] = []
                for el, pls in zip(contour.elements, per_el, strict=True):
                    for pl in pls:
                        lst = pl.tolist()
                        travel.extend(lst[::-1] if el.direction == -1 else lst)
                xs = [q[0] for q in travel]
                ys = [q[1] for q in travel]
                bbox = (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))
                start = (float(travel[0][0]), float(travel[0][1]))
                arrow, length = _arrow_small(travel)
            else:
                if polylines:
                    allp = np.concatenate(polylines, axis=0)
                    bbox = (
                        float(allp[:, 0].min()),
                        float(allp[:, 1].min()),
                        float(allp[:, 0].max()),
                        float(allp[:, 1].max()),
                    )
                else:
                    bbox = (math.inf, math.inf, -math.inf, -math.inf)
                path = _travel(contour, per_el)
                start = (float(path[0, 0]), float(path[0, 1])) if len(path) else None
                arrow, length = _arrow(path)
            for el, pls in zip(contour.elements, per_el, strict=True):
                for pl in pls:
                    if len(pl) >= 2:
                        polys_all.append(pl)
                        poly_owner.append(idx)
                    elif isinstance(el.glyph, PointGlyph) or len(pl) == 1:
                        p_pts.append(pl[:1])
                        p_owner.append(idx)
            scene.contours.append(
                ContourView(idx, gi, kind, contour, g, polylines, bbox, start, arrow, length)
            )
            boxes.append(bbox)
            starts.append(nan2 if start is None else start)
            arrows.append(nan4 if arrow is None else arrow)
    if polys_all:
        pts = np.concatenate(polys_all, axis=0)
        lens = np.fromiter((len(pl) for pl in polys_all), dtype=np.int64, count=len(polys_all))
        valid = np.ones(len(pts) - 1, dtype=bool)
        valid[np.cumsum(lens)[:-1] - 1] = False
        scene.seg_start = pts[:-1][valid]
        scene.seg_end = pts[1:][valid]
        scene.seg_owner = np.repeat(np.asarray(poly_owner, dtype=np.int64), lens)[:-1][valid]
    if p_pts:
        scene.points = np.concatenate(p_pts)
        scene.point_owner = np.asarray(p_owner, dtype=np.int64)
    if boxes:
        scene.boxes = np.asarray(boxes, dtype=np.float64)
        scene.starts = np.asarray(starts, dtype=np.float64).reshape(-1, 2)
        scene.arrows = np.asarray(arrows, dtype=np.float64).reshape(-1, 4)
        scene.layers = np.fromiter((cv.layer for cv in scene.contours), np.int64, len(boxes))
        scene.scan = np.fromiter((cv.is_scan_path for cv in scene.contours), bool, len(boxes))
        scene.graph_numbers = np.fromiter(
            (cv.graph_index for cv in scene.contours), np.int64, len(boxes)
        )
        finite = scene.boxes[scene.boxes[:, 0] <= scene.boxes[:, 2]]
        if len(finite):
            scene.bbox = (
                float(finite[:, 0].min()),
                float(finite[:, 1].min()),
                float(finite[:, 2].max()),
                float(finite[:, 3].max()),
            )
    return scene
