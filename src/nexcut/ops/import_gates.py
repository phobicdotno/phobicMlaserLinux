"""Import clean-up gates (``PImportGraphParam`` / ``IGP.*``) and contour helpers for importers.

The original runs a clean-up step between the file importers and the glyph model
(05 §4 pipeline: "IGP.* clean-up: MicoGraphGate, OverlapGate,
ConnectGate/MegerConnectGraphType ..."; 06 §4.8 "Graphics optimize pd363-371").
The parameters and their enums are those of ``File/BkManuPara.xml`` group
``PImportGraphParam`` (01 §1.2):

==============================  ======  ===============================================
key                             value   meaning (lang label)
==============================  ======  ===============================================
``IGP.IsAutoFilterMicoGraph``   1       小图形.自动去除极小图形 remove minimal graphics
``IGP.MicoGraphGate``           0.01    小图形.最小图形长度 smallest length (mm)
``IGP.IsFilteOverlapGraph``     1       重复线.自动去除重复线 remove duplicate lines
``IGP.OverlapGate``             0.01    重复线.重复线检测精度 duplicate limit (mm)
``IGP.MegerConnectGraphType``   1       相连线.自动合并相连线 0 none / 1 direction first /
                                        2 length first / 3 distance first
``IGP.ConnectGate``             0.01    相连线.相连线检测精度 combine precision (mm)
``IGP.AutoSortType``            5       自动排序 0 none / 1 L->R / 2 R->L / 3 B->T /
                                        4 T->B / 5 nearest / 6 in->out / 7 out->in
==============================  ======  ===============================================

Only the parameter names, ranges and enum labels are evidence.  The DLL routines
that implement the gates were not disassembled, so every *algorithm* here is a
reconstruction from the labels and is UNVERIFIED (see :data:`UNVERIFIED`):

* the gate order (overlap -> connect -> minimal -> sort),
* what "length" the minimal-graphics gate measures (contour path length here),
* duplicate detection at whole-glyph level (partial overlaps are not split),
* the tie-break rules of the three merge strategies, and that gaps up to the
  gate are bridged without moving geometry.

The module also hosts the geometry helpers every importer uses to build a
:class:`~nexcut.model.graph.Contour` with its cached fields (03 §6.1 lines 2-7).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from itertools import chain
from typing import Any, Protocol

import numpy as np
from scipy.spatial import cKDTree

from nexcut.model.flatten import CHECK_STEP, flatten_glyph, measure_contour
from nexcut.model.glyph import (
    ArcGlyph,
    CircleGlyph,
    EllipseArcGlyph,
    Glyph,
    LwPolylineGlyph,
    PointGlyph,
    SegmentGlyph,
    SplineGlyph,
    Vec2,
)
from nexcut.model.graph import (
    ChfDocument,
    Contour,
    ContourElement,
    Graph,
    Group,
    Scan,
    Text,
)

__all__ = [
    "DEFAULT_PRECISION",
    "UNVERIFIED",
    "GateParams",
    "GateReport",
    "MergeType",
    "apply_import_gates",
    "build_contour",
    "contour_endpoints",
    "element_endpoints",
    "element_tangents",
    "filter_micro_graphs",
    "glyph_endpoints",
    "glyph_extent",
    "glyph_is_finite",
    "glyph_tangents",
    "graph_endpoints",
    "is_closed",
    "merge_connected",
    "refresh_contour",
    "refresh_group",
    "remove_overlaps",
    "reverse_contour",
]

DEFAULT_PRECISION = 0.01
"""Contour ``precision`` given to imported contours: the value of ``File/autosave.chf`` (03 §6.1 line 1).

UNVERIFIED: the constructor argument is caller-chosen (0.01 in autosave, 0.1 in
``tempGraph.chf``); which one the importers pass is not traced.
"""

UNVERIFIED: tuple[str, ...] = (
    "import gate order overlap -> connect -> minimal graphics -> auto sort (UI lists them "
    "minimal/overlap/connect, 06 §4.8; execution order not traced)",
    "IGP.MicoGraphGate compares the contour path length; single-point contours are kept "
    "(mirrors the .chf ReadGraphs degenerate rule, 03 §5)",
    "IGP.OverlapGate removes whole glyphs whose geometry lies within the gate of an earlier "
    "glyph (symmetric sampled Hausdorff distance); partial overlaps are not split",
    "IGP.MegerConnectGraphType chaining: only open top-level contours of the same layer, "
    "gaps <= ConnectGate bridged without moving geometry; direction first = smallest tangent "
    "turn, length first = longest candidate, distance first = smallest gap",
    "imported contour precision 0.01 (autosave.chf value)",
    "group cached geometry = union bbox, summed length, first child start, last child end",
)


# ---------------------------------------------------------------------------
# Glyph / contour geometry helpers
# ---------------------------------------------------------------------------


def _v(x: float, y: float) -> Vec2:
    return Vec2(float(x), float(y))


def _unit(dx: float, dy: float) -> Vec2:
    n = math.hypot(dx, dy)
    if n < 1e-15:
        return Vec2(0.0, 0.0)
    return Vec2(dx / n, dy / n)


def glyph_endpoints(glyph: Glyph) -> tuple[Vec2, Vec2]:
    """Start and end point of a glyph in its natural (stored) direction (03 §7).

    Arcs run CCW from ``start_angle`` to ``end_angle``; circles start at angle 0;
    a closed lwpolyline ends where it starts; ellipse arcs and splines are
    evaluated with :func:`nexcut.model.flatten.flatten_glyph`.
    """
    match glyph:
        case PointGlyph():
            return glyph.pt, glyph.pt
        case SegmentGlyph():
            return glyph.p0, glyph.p1
        case ArcGlyph():
            c, r = glyph.center, glyph.radius
            return (
                _v(c[0] + r * math.cos(glyph.start_angle), c[1] + r * math.sin(glyph.start_angle)),
                _v(c[0] + r * math.cos(glyph.end_angle), c[1] + r * math.sin(glyph.end_angle)),
            )
        case CircleGlyph():
            p = _v(glyph.center[0] + glyph.radius, glyph.center[1])
            return p, p
        case LwPolylineGlyph():
            if not glyph.vertices:
                return Vec2(0.0, 0.0), Vec2(0.0, 0.0)
            first = glyph.vertices[0].pt
            return first, first if glyph.closed else glyph.vertices[-1].pt
        case EllipseArcGlyph() | SplineGlyph():
            pl = flatten_glyph(glyph)[0]
            return _v(*pl[0]), _v(*pl[-1])
    raise TypeError(f"not a glyph: {glyph!r}")


def glyph_tangents(glyph: Glyph) -> tuple[Vec2, Vec2]:
    """Unit tangent at the start and at the end, natural direction (zero vector for a point).

    Exact for segments, arcs, circles and bulge polylines (bulge ``tan(theta/4)``:
    the start tangent is the chord turned by ``-theta/2``, the end tangent by
    ``+theta/2``, 03 §7 type 6); chord approximations for ellipse arcs and splines.
    """
    match glyph:
        case PointGlyph():
            return Vec2(0.0, 0.0), Vec2(0.0, 0.0)
        case SegmentGlyph():
            t = _unit(glyph.p1[0] - glyph.p0[0], glyph.p1[1] - glyph.p0[1])
            return t, t
        case ArcGlyph():
            sgn = 1.0 if glyph.end_angle >= glyph.start_angle else -1.0
            a0, a1 = glyph.start_angle, glyph.end_angle
            return (
                _v(-math.sin(a0) * sgn, math.cos(a0) * sgn),
                _v(-math.sin(a1) * sgn, math.cos(a1) * sgn),
            )
        case CircleGlyph():
            return Vec2(0.0, 1.0), Vec2(0.0, 1.0)
        case LwPolylineGlyph():
            v = glyph.vertices
            if len(v) < 2:
                return Vec2(0.0, 0.0), Vec2(0.0, 0.0)
            n_seg = len(v) if glyph.closed else len(v) - 1

            def seg_tangent(i: int, at_end: bool) -> Vec2:
                p0, p1 = v[i].pt, v[(i + 1) % len(v)].pt
                chord = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
                half = 2.0 * math.atan(v[i].bulge)
                a = chord + half if at_end else chord - half
                return _v(math.cos(a), math.sin(a))

            return seg_tangent(0, False), seg_tangent(n_seg - 1, True)
        case EllipseArcGlyph() | SplineGlyph():
            pl = flatten_glyph(glyph, 0.01)[0]
            return (
                _unit(pl[1][0] - pl[0][0], pl[1][1] - pl[0][1]),
                _unit(pl[-1][0] - pl[-2][0], pl[-1][1] - pl[-2][1]),
            )
    raise TypeError(f"not a glyph: {glyph!r}")


def _finite_values(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, int):
        return True
    if isinstance(value, (tuple, list)):
        return all(_finite_values(v) for v in value)
    slots = getattr(type(value), "__slots__", None)
    if slots is not None:
        return all(_finite_values(getattr(value, name)) for name in slots)
    return True


def glyph_is_finite(glyph: Glyph) -> bool:
    """True when every coordinate, radius, angle, bulge, ratio and knot of ``glyph`` is finite.

    Importers drop entities that fail this check: a NaN/inf coordinate read from a
    damaged file would otherwise reach the cached bbox, the canvas framing and the
    motion planner (import-ui fidelity review; the original's behaviour is untraced).
    """
    return _finite_values(glyph)


def element_endpoints(element: ContourElement) -> tuple[Vec2, Vec2]:
    """Endpoints of a contour element with its direction flag applied (03 §6.1 line 10)."""
    a, b = glyph_endpoints(element.glyph)
    return (b, a) if element.direction == -1 else (a, b)


def element_tangents(element: ContourElement) -> tuple[Vec2, Vec2]:
    """Tangents of a contour element in traversal direction (reversed and negated for ``-1``)."""
    a, b = glyph_tangents(element.glyph)
    if element.direction == -1:
        return Vec2(-b[0], -b[1]), Vec2(-a[0], -a[1])
    return a, b


def contour_endpoints(contour: Contour) -> tuple[Vec2, Vec2]:
    """Start of the first and end of the last element (computed, not the cached fields)."""
    els = contour.elements
    if not els:
        return contour.start, contour.end
    first, last = els[0], els[-1]
    # Inline form of element_endpoints for plain segments (the bulk of any import).
    g = first.glyph
    if type(g) is SegmentGlyph:
        s = g.p1 if first.direction == -1 else g.p0
    else:
        s = element_endpoints(first)[0]
    g = last.glyph
    if type(g) is SegmentGlyph:
        e = g.p0 if last.direction == -1 else g.p1
    else:
        e = element_endpoints(last)[1]
    return s, e


_Box = tuple[float, float, float, float]


def _arc_extent(cx: float, cy: float, r: float, a0: float, a1: float) -> _Box:
    """Exact bbox of the circular arc ``a0 -> a1`` (either sweep sign, any length)."""
    lo, hi = min(a0, a1), max(a0, a1)
    if hi - lo >= 2 * math.pi:
        return cx - r, cy - r, cx + r, cy + r
    xs = [cx + r * math.cos(a0), cx + r * math.cos(a1)]
    ys = [cy + r * math.sin(a0), cy + r * math.sin(a1)]
    half_pi = math.pi / 2
    for k in range(math.ceil(lo / half_pi), math.floor(hi / half_pi) + 1):
        xs.append(cx + r * math.cos(k * half_pi))
        ys.append(cy + r * math.sin(k * half_pi))
    return min(xs), min(ys), max(xs), max(ys)


def _union(a: _Box | None, b: _Box) -> _Box:
    if a is None:
        return b
    return min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])


def glyph_extent(glyph: Glyph) -> tuple[float, _Box] | None:
    """Exact ``(length, (minx, miny, maxx, maxy))`` of a point/segment/arc/circle/bulge polyline.

    Same geometry conventions as :func:`nexcut.model.flatten.flatten_glyph` (03 §7:
    signed sweep, bulge ``tan(theta/4)`` with the 1e-12 straight-span rules), but
    closed-form.  ``None`` for ellipse arcs, splines and degenerate polylines, which
    must be sampled.  Import-ui fidelity review: sampling every arc at
    ``CHECK_STEP`` (2 um, up to 20 000 points per glyph in pure Python) made a DXF
    of 1 000 circles take 15 s to import.
    """
    match glyph:
        case PointGlyph():
            x, y = glyph.pt
            return 0.0, (x, y, x, y)
        case SegmentGlyph():
            (x0, y0), (x1, y1) = glyph.p0, glyph.p1
            return math.dist(glyph.p0, glyph.p1), (
                min(x0, x1),
                min(y0, y1),
                max(x0, x1),
                max(y0, y1),
            )
        case ArcGlyph():
            c, r = glyph.center, glyph.radius
            sweep = glyph.end_angle - glyph.start_angle
            box = _arc_extent(c[0], c[1], r, glyph.start_angle, glyph.end_angle)
            return abs(sweep) * r, box
        case CircleGlyph():
            c, r = glyph.center, glyph.radius
            return 2 * math.pi * r, _arc_extent(c[0], c[1], r, 0.0, 2 * math.pi)
        case LwPolylineGlyph():
            v = glyph.vertices
            segs = len(v) if glyph.closed else len(v) - 1
            if segs < 1 or not v:
                return None
            total = 0.0
            box: _Box | None = None
            for i in range(segs):
                p0, p1 = v[i].pt, v[(i + 1) % len(v)].pt
                bulge = v[i].bulge
                dx, dy = p1[0] - p0[0], p1[1] - p0[1]
                chord = math.hypot(dx, dy)
                straight = (p0[0], p0[1], p0[0], p0[1])
                box = _union(_union(box, straight), (p1[0], p1[1], p1[0], p1[1]))
                if abs(bulge) < 1e-12 or chord < 1e-12:
                    total += chord
                    continue
                theta = 4.0 * math.atan(bulge)
                r = chord / (2.0 * math.sin(abs(theta) / 2.0))
                d = math.sqrt(max(r * r - (chord / 2.0) ** 2, 0.0))
                nx, ny = -dy / chord, dx / chord
                mx, my = (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0
                sgn = 1.0 if (bulge > 0) == (abs(theta) < math.pi) else -1.0
                cx, cy = mx + sgn * nx * d, my + sgn * ny * d
                a0 = math.atan2(p0[1] - cy, p0[0] - cx)
                total += r * abs(theta)
                box = _union(box, _arc_extent(cx, cy, r, a0, a0 + theta))
            assert box is not None
            return total, box
    return None


def _exact_measure(contour: Contour) -> tuple[float, _Box] | None:
    total = 0.0
    box: _Box | None = None
    for el in contour.elements:
        ext = glyph_extent(el.glyph)
        if ext is None:
            return None
        total += ext[0]
        box = _union(box, ext[1])
    if box is None:
        return None
    return total, box


def refresh_contour(contour: Contour, step: float = CHECK_STEP) -> Contour:
    """Recompute the cached length/bbox/start/end of ``contour`` in place (03 §6.1, §11).

    With the default ``step`` contours made only of points, segments, arcs, circles
    and bulge polylines are measured in closed form (:func:`glyph_extent`, exact -
    within the :func:`nexcut.model.flatten.check_contour` tolerances of the sampled
    value); anything else, or an explicit ``step``, uses
    :func:`nexcut.model.flatten.measure_contour`.  Returns the same object.
    """
    exact = _exact_measure(contour) if step == CHECK_STEP else None
    if exact is not None:
        contour.length = exact[0]
        b = exact[1]
        contour.bbox_min, contour.bbox_max = Vec2(b[0], b[1]), Vec2(b[2], b[3])
        contour.start = element_endpoints(contour.elements[0])[0]
        contour.end = element_endpoints(contour.elements[-1])[1]
        return contour
    m = measure_contour(contour, step)
    if m is None:
        contour.length = 0.0
        return contour
    contour.length = m.length
    contour.bbox_min, contour.bbox_max = m.bbox_min, m.bbox_max
    contour.start, contour.end = m.start, m.end
    return contour


def refresh_group(group: Group) -> Group:
    """Recompute a group's cached fields from its (already refreshed) children.

    UNVERIFIED rule: summed length, union bbox, first child's start, last child's end.
    """
    kids = group.children
    if not kids:
        group.length = 0.0
        return group
    group.length = sum(k.length for k in kids)
    group.bbox_min = _v(min(k.bbox_min[0] for k in kids), min(k.bbox_min[1] for k in kids))
    group.bbox_max = _v(max(k.bbox_max[0] for k in kids), max(k.bbox_max[1] for k in kids))
    group.start = kids[0].start
    group.end = kids[-1].end
    return group


def build_contour(
    elements: Iterable[ContourElement | Glyph],
    *,
    layer: int = 0,
    precision: float = DEFAULT_PRECISION,
) -> Contour:
    """Create a :class:`Contour` from glyphs/elements and fill its cached geometry (03 §6.1)."""
    els = [e if isinstance(e, ContourElement) else ContourElement(e, 1) for e in elements]
    c = Contour(precision=precision, elements=els, layer=layer)
    return refresh_contour(c)


def is_closed(contour: Contour, tol: float | None = None) -> bool:
    """True when the contour's start and end coincide within ``tol`` (default: its ``precision``).

    ``precision`` is "used e.g. to decide closure (start==end within this)" (03 §6.1 line 1).
    """
    els = contour.elements
    if not els:
        return False
    if isinstance(els[0].glyph, PointGlyph) and all(isinstance(e.glyph, PointGlyph) for e in els):
        return False
    s, e = contour_endpoints(contour)
    return math.dist(s, e) <= (contour.precision if tol is None else tol)


def reverse_contour(contour: Contour) -> Contour:
    """Return a copy traversed backwards: element order reversed, direction flags negated.

    Geometry is untouched; only the ``{glyph, dir}`` list of 03 §6.1 line 10 changes.
    """
    els = [
        ContourElement(e.glyph, -1 if e.direction != -1 else 1) for e in reversed(contour.elements)
    ]
    if _FAST_CONTOUR_COPY and type(contour) is Contour:
        # == dataclasses.replace(contour, ...), without its per-field introspection
        # (25 000 reversals in the nearest sort of X11 spent ~0.2 s there).
        c = contour
        return Contour(
            c.precision,
            c.length,
            c.bbox_min,
            c.bbox_max,
            c.end,
            c.start,
            els,
            c.layer,
            c.int58,
            c.crafts,
            c.legacy_reserved_glyphs,
        )
    return replace(contour, elements=els, start=contour.end, end=contour.start)


_FAST_CONTOUR_COPY = tuple(f.name for f in fields(Contour)) == (
    "precision",
    "length",
    "bbox_min",
    "bbox_max",
    "start",
    "end",
    "elements",
    "layer",
    "int58",
    "crafts",
    "legacy_reserved_glyphs",
) and all(f.init for f in fields(Contour))
"""The positional constructor call in :func:`reverse_contour` matches ``Contour``'s fields.

If a field is added to :class:`~nexcut.model.graph.Contour` this turns false and
:func:`reverse_contour` falls back to :func:`dataclasses.replace` rather than dropping it.
"""


def graph_endpoints(graph: Graph) -> tuple[Vec2, Vec2]:
    """Cached start/end of any graph (text uses its outline group, 03 §6.3)."""
    if isinstance(graph, Text):
        if graph.outline is None:
            return graph.position, graph.position
        return graph.outline.start, graph.outline.end
    return graph.start, graph.end


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


class MergeType:
    """``IGP.MegerConnectGraphType`` enum (01 §1.2)."""

    NONE = 0
    DIRECTION_FIRST = 1
    LENGTH_FIRST = 2
    DISTANCE_FIRST = 3


class _ParamSource(Protocol):
    def get(self, group: str, element: str, attribute: str) -> Any: ...


@dataclass(slots=True)
class GateParams:
    """The ``PImportGraphParam/IGP`` values used by the gates.

    Defaults are the shipped ``File/BkManuPara.xml`` values (01 §1.2); the
    descriptor defaults differ (0.1 mm for the three gates).
    """

    filter_micro: bool = True
    micro_gate: float = 0.01
    filter_overlap: bool = True
    overlap_gate: float = 0.01
    merge_type: int = MergeType.DIRECTION_FIRST
    connect_gate: float = 0.01
    auto_sort_type: int = 5

    KEYS = {
        "filter_micro": "IsAutoFilterMicoGraph",
        "micro_gate": "MicoGraphGate",
        "filter_overlap": "IsFilteOverlapGraph",
        "overlap_gate": "OverlapGate",
        "merge_type": "MegerConnectGraphType",
        "connect_gate": "ConnectGate",
        "auto_sort_type": "AutoSortType",
    }

    @classmethod
    def from_params(cls, source: _ParamSource | Mapping[str, Any]) -> GateParams:
        """Read from a ``ParamDocument`` (``manu`` kind) or a ``{"IGP.Key": value}`` mapping."""
        out = cls()
        for attr, key in cls.KEYS.items():
            if isinstance(source, Mapping):
                if f"IGP.{key}" not in source:
                    continue
                raw = source[f"IGP.{key}"]
            else:
                raw = source.get("PImportGraphParam", "IGP", key)
            default = getattr(out, attr)
            if isinstance(default, bool):
                setattr(out, attr, bool(int(raw)))
            elif isinstance(default, int):
                setattr(out, attr, int(raw))
            else:
                setattr(out, attr, float(raw))
        return out


@dataclass(slots=True)
class GateReport:
    """What the gates changed (counts for the import summary)."""

    overlaps_removed: int = 0
    contours_merged: int = 0
    micro_removed: int = 0
    sorted_by: int | None = None
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Minimal graphics (IGP.IsAutoFilterMicoGraph / MicoGraphGate)
# ---------------------------------------------------------------------------


def _is_point_contour(c: Contour) -> bool:
    els = c.elements
    # The first-element test is the all() below short-circuited by hand (no generator
    # for the usual non-point contour).
    return (
        bool(els)
        and isinstance(els[0].glyph, PointGlyph)
        and all(isinstance(e.glyph, PointGlyph) for e in els)
    )


def filter_micro_graphs(graphs: Sequence[Graph], gate: float) -> tuple[list[Graph], int]:
    """Drop contours shorter than ``gate`` mm (``IGP.MicoGraphGate``, 01 §1.2).

    Group children are filtered too and empty groups removed.  Text and scan
    objects are kept unchanged.  Returns ``(graphs, removed_count)``.
    UNVERIFIED: length criterion and point-contour exemption (module docstring).
    """
    out: list[Graph] = []
    removed = 0
    for g in graphs:
        if isinstance(g, Contour):
            if g.length < gate and not _is_point_contour(g):
                removed += 1
                continue
            out.append(g)
        elif isinstance(g, Group) and not isinstance(g, Scan):
            kids = [k for k in g.children if k.length >= gate or _is_point_contour(k)]
            removed += len(g.children) - len(kids)
            if not kids:
                continue
            if len(kids) != len(g.children):
                g = replace(g, children=kids)
                refresh_group(g)
            out.append(g)
        else:
            out.append(g)
    return out, removed


# ---------------------------------------------------------------------------
# Duplicate lines (IGP.IsFilteOverlapGraph / OverlapGate)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _GlyphInfo:
    lo: np.ndarray
    hi: np.ndarray
    glyph: Glyph
    step: float
    _pts: np.ndarray | None = None

    @property
    def pts(self) -> np.ndarray:
        """Sampled points, computed only when a bbox-compatible candidate needs them."""
        if self._pts is None:
            self._pts = _sample(self.glyph, self.step)
        return self._pts


def _sample(glyph: Glyph, step: float) -> np.ndarray:
    pl: list[tuple[float, float]] = []
    for part in flatten_glyph(glyph, step):
        pl.extend(part)
    return np.asarray(pl, dtype=float).reshape(-1, 2)


def _glyph_info(glyph: Glyph, step: float) -> _GlyphInfo:
    ext = glyph_extent(glyph)
    if ext is not None:
        b = ext[1]
        return _GlyphInfo(np.array([b[0], b[1]]), np.array([b[2], b[3]]), glyph, step)
    pts = _sample(glyph, step)
    return _GlyphInfo(pts.min(axis=0), pts.max(axis=0), glyph, step, pts)


def _max_dist_to_polyline(points: np.ndarray, poly: np.ndarray) -> float:
    """Largest distance from any of ``points`` to the polyline ``poly`` (vectorised)."""
    if len(poly) == 1:
        return float(np.max(np.linalg.norm(points - poly[0], axis=1)))
    a = poly[:-1]
    ab = poly[1:] - a
    ab2 = np.maximum((ab * ab).sum(axis=1), 1e-30)
    worst = 0.0
    for chunk in np.array_split(points, max(1, len(points) // 512 + 1)):
        ap = chunk[:, None, :] - a[None, :, :]
        t = np.clip((ap * ab[None, :, :]).sum(axis=2) / ab2[None, :], 0.0, 1.0)
        d = ap - t[:, :, None] * ab[None, :, :]
        worst = max(worst, float(np.sqrt((d * d).sum(axis=2)).min(axis=1).max()))
    return worst


def _within_gate(points: np.ndarray, poly: np.ndarray, gate: float) -> bool:
    """``_max_dist_to_polyline(points, poly) <= gate`` without the ``n x m`` distance matrix.

    Nearest vertices come from a KD-tree; only points farther than ``gate`` from every
    vertex are checked exactly against the segments that can still be within ``gate``
    (an endpoint within ``gate + longest segment``).  Import-ui fidelity review: two
    coincident r = 50 mm circles (20 000 samples each) took ~70 s in the pairwise form.
    """
    if len(poly) == 1:
        return bool(np.all(np.linalg.norm(points - poly[0], axis=1) <= gate))
    tree = cKDTree(poly)
    d, _ = tree.query(points)
    far = points[d > gate]
    if len(far) == 0:
        return True
    a = poly[:-1]
    ab = poly[1:] - a
    ab2 = np.maximum((ab * ab).sum(axis=1), 1e-30)
    reach = gate + float(np.sqrt(ab2.max()))
    nseg = len(a)
    for p, near in zip(far, tree.query_ball_point(far, reach), strict=True):
        if not near:
            return False
        idx = np.asarray(near, dtype=np.int64)
        segs = np.unique(np.clip(np.concatenate((idx - 1, idx)), 0, nseg - 1))
        ap = p - a[segs]
        t = np.clip((ap * ab[segs]).sum(axis=1) / ab2[segs], 0.0, 1.0)
        diff = ap - t[:, None] * ab[segs]
        if float(np.sqrt((diff * diff).sum(axis=1)).min()) > gate:
            return False
    return True


def _duplicate(a: _GlyphInfo, b: _GlyphInfo, gate: float) -> bool:
    if np.any(np.abs(a.lo - b.lo) > gate) or np.any(np.abs(a.hi - b.hi) > gate):
        return False
    return _within_gate(a.pts, b.pts, gate) and _within_gate(b.pts, a.pts, gate)


def _split_runs(c: Contour, keep: list[bool]) -> list[Contour]:
    runs: list[list[ContourElement]] = []
    cur: list[ContourElement] = []
    for el, k in zip(c.elements, keep, strict=True):
        if k:
            cur.append(el)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    out = []
    for run in runs:
        piece = replace(c, elements=run)
        out.append(refresh_contour(piece))
    return out


_PREFILTER_MIN = 64
"""Below this many items the gates skip the KD-tree prefilter (it would cost more than it saves)."""

_PREFILTER_SLACK = 1e-6
"""Relative widening of the prefilter radius: it must be a superset of the exact per-item test."""


def _prefilter_radius(gate: float) -> float:
    return max(gate, 0.0) * (1.0 + _PREFILTER_SLACK) + 1e-9


def remove_overlaps(graphs: Sequence[Graph], gate: float) -> tuple[list[Graph], int]:
    """Remove glyphs that duplicate an earlier glyph within ``gate`` mm (``IGP.OverlapGate``).

    Scope: top-level contours and group children; a contour losing a middle
    glyph is split into runs.  Direction is irrelevant (a reversed copy is a
    duplicate).  Returns ``(graphs, removed_glyph_count)``.  UNVERIFIED algorithm.

    A glyph can only be a duplicate of, or make a duplicate of, a glyph whose bbox
    corners are all within ``gate`` of its own (the first test of ``_duplicate``).
    One KD-tree pass over the ``(lo, hi)`` corners (Chebyshev metric, radius
    widened by :data:`_PREFILTER_SLACK`) finds every glyph that has such a partner;
    only those go through the per-glyph grid below, in document order, and every
    other glyph is kept without being looked at again (STATUS §5 task 7 / X11: the
    per-glyph path cost ~0.4 s on 50 000 separate ``LINE`` s).  Same result as
    running every glyph through the grid (``tests/test_ops_gates_equivalence.py``).
    """
    step = max(gate, 0.005)
    cell = max(gate * 4.0, 1e-6)
    grid: dict[tuple[int, int], list[_GlyphInfo]] = {}
    removed = 0

    units: list[Contour] = []
    for g in graphs:
        if isinstance(g, Contour):
            units.append(g)
        elif isinstance(g, Group) and not isinstance(g, Scan):
            units.extend(g.children)
    boxes: list[tuple[float, float, float, float]] = []
    sampled: dict[int, _GlyphInfo] = {}
    for c in units:
        for el in c.elements:
            gl = el.glyph
            if type(gl) is SegmentGlyph:  # == glyph_extent(gl)[1], inline for the bulk case
                (x0, y0), (x1, y1) = gl.p0, gl.p1
                boxes.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
                continue
            ext = glyph_extent(gl)
            if ext is None:
                info = _glyph_info(el.glyph, step)
                sampled[len(boxes)] = info
                boxes.append((info.lo[0], info.lo[1], info.hi[0], info.hi[1]))
            else:
                boxes.append(ext[1])
    n_glyphs = len(boxes)
    candidate: list[bool] = [True] * n_glyphs
    if n_glyphs >= _PREFILTER_MIN:
        corners = np.fromiter(chain.from_iterable(boxes), np.float64, 4 * n_glyphs).reshape(-1, 4)
        if bool(np.isfinite(corners).all()):
            mask = np.zeros(n_glyphs, dtype=bool)
            if gate >= 0.0:
                pairs = cKDTree(corners, balanced_tree=False, compact_nodes=False).query_pairs(
                    _prefilter_radius(gate), p=np.inf, output_type="ndarray"
                )
                mask[pairs.ravel()] = True
            candidate = mask.tolist()
    cursor = 0

    def seen_before(info: _GlyphInfo) -> bool:
        kx, ky = int(math.floor(info.lo[0] / cell)), int(math.floor(info.lo[1] / cell))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in grid.get((kx + dx, ky + dy), ()):
                    if _duplicate(info, other, gate):
                        return True
        grid.setdefault((kx, ky), []).append(info)
        return False

    def filter_contour(c: Contour) -> list[Contour]:
        nonlocal removed, cursor
        first = cursor
        cursor += len(c.elements)
        if cursor - first == 1:
            if not candidate[first]:
                return [c]
        elif not any(candidate[first:cursor]):
            return [c]
        keep = []
        for k, el in enumerate(c.elements, first):
            if not candidate[k]:
                keep.append(True)
                continue
            info = sampled.get(k)
            if info is None:
                b = boxes[k]
                info = _GlyphInfo(np.array([b[0], b[1]]), np.array([b[2], b[3]]), el.glyph, step)
            dup = seen_before(info)
            keep.append(not dup)
            removed += dup
        if all(keep):
            return [c]
        return _split_runs(c, keep)

    out: list[Graph] = []
    for g in graphs:
        if isinstance(g, Contour):
            out.extend(filter_contour(g))
        elif isinstance(g, Group) and not isinstance(g, Scan):
            kids = [p for k in g.children for p in filter_contour(k)]
            if not kids:
                continue
            if kids != g.children:
                g = replace(g, children=kids)
                refresh_group(g)
            out.append(g)
        else:
            out.append(g)
    return out, removed


# ---------------------------------------------------------------------------
# Combine near lines (IGP.MegerConnectGraphType / ConnectGate)
# ---------------------------------------------------------------------------


def _turn(t_from: Vec2, t_to: Vec2) -> float:
    dot = max(-1.0, min(1.0, t_from[0] * t_to[0] + t_from[1] * t_to[1]))
    return math.acos(dot)


def merge_connected(
    graphs: Sequence[Graph], gate: float, merge_type: int = MergeType.DIRECTION_FIRST
) -> tuple[list[Graph], int]:
    """Chain open contours whose endpoints lie within ``gate`` mm (``IGP.ConnectGate``, 01 §1.2).

    Every open top-level contour seeds a chain in document order; the chain is
    extended at its end, then at its start, choosing among touching candidates by
    ``merge_type`` (1 smallest tangent turn, 2 longest, 3 smallest gap; ties by
    document order).  Candidates are reversed with :func:`reverse_contour` when
    needed.  A chain stops growing once its ends meet.  The merged contour keeps
    the seed's crafts and takes the seed's place.  Returns ``(graphs, merges)``.
    UNVERIFIED algorithm (module docstring).

    Only a contour with an endpoint within ``gate`` of an endpoint of *another* open
    contour can seed a merge or be merged, so one KD-tree pass over all endpoints
    (radius widened by :data:`_PREFILTER_SLACK`) picks those out and the chaining
    below runs over them alone, in document order (STATUS §5 task 7 / X11: the
    per-contour grid and candidate search cost ~0.9 s on 50 000 separate
    ``LINE`` s that merge nothing).  Same result as chaining over every open contour
    (``tests/test_ops_gates_equivalence.py``).
    """
    if merge_type == MergeType.NONE:
        return list(graphs), 0
    cell = max(gate, 1e-6)
    open_ids: list[int] = []
    ends: dict[int, tuple[Vec2, Vec2]] = {}
    for i, g in enumerate(graphs):
        # == isinstance(g, Contour) and g.elements and not _is_point_contour(g)
        #    and not is_closed(g, gate), with the endpoints computed once.
        if not isinstance(g, Contour) or not g.elements or _is_point_contour(g):
            continue
        se = contour_endpoints(g)
        if math.dist(se[0], se[1]) <= gate:
            continue
        open_ids.append(i)
        ends[i] = se
    if len(open_ids) >= _PREFILTER_MIN:
        flat = chain.from_iterable(chain.from_iterable(ends[i] for i in open_ids))
        pts = np.fromiter(flat, np.float64, 4 * len(open_ids)).reshape(-1, 2)
        if bool(np.isfinite(pts).all()):
            tree = cKDTree(pts, balanced_tree=False, compact_nodes=False)
            pairs = tree.query_pairs(_prefilter_radius(gate), output_type="ndarray")
            owners = pairs // 2
            owners = owners[owners[:, 0] != owners[:, 1]]
            touching = np.zeros(len(open_ids), dtype=bool)
            touching[owners.ravel()] = True
            open_ids = [i for i, t in zip(open_ids, touching.tolist(), strict=True) if t]
    grid: dict[tuple[int, int], set[int]] = {}

    def key(p: Vec2) -> tuple[int, int]:
        return int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell))

    for i in open_ids:
        for p in ends[i]:
            grid.setdefault(key(p), set()).add(i)

    used: set[int] = set()

    def candidates(p: Vec2, exclude: set[int], layer: int) -> list[tuple[int, bool, float]]:
        """(index, attach_at_its_start, gap) of unused open contours of ``layer`` touching ``p``."""
        kx, ky = key(p)
        found: dict[int, tuple[int, bool, float]] = {}
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((kx + dx, ky + dy), ()):
                    if j in used or j in exclude or graphs[j].layer != layer:
                        continue
                    s, e = ends[j]
                    for at_start, q in ((True, s), (False, e)):
                        d = math.dist(p, q)
                        if d <= gate and (j not in found or d < found[j][2]):
                            found[j] = (j, at_start, d)
        return sorted(found.values())

    def pick(
        opts: list[tuple[int, bool, float]], tangent: Vec2, forward: bool
    ) -> tuple[int, bool, float]:
        if merge_type == MergeType.LENGTH_FIRST:
            return max(opts, key=lambda o: (graphs[o[0]].length, -o[0]))  # type: ignore[union-attr]
        if merge_type == MergeType.DISTANCE_FIRST:
            return min(opts, key=lambda o: (o[2], o[0]))

        def turn(o: tuple[int, bool, float]) -> float:
            c = graphs[o[0]]
            assert isinstance(c, Contour)
            if forward:
                el = c.elements[0] if o[1] else reverse_contour(c).elements[0]
                t = element_tangents(el)[0]
                return _turn(tangent, t)
            el = c.elements[-1] if not o[1] else reverse_contour(c).elements[-1]
            t = element_tangents(el)[1]
            return _turn(t, tangent)

        return min(opts, key=lambda o: (turn(o), o[2], o[0]))

    merged: dict[int, Contour] = {}
    absorbed: set[int] = set()
    merges = 0
    for seed in open_ids:
        if seed in used:
            continue
        used.add(seed)
        base = graphs[seed]
        assert isinstance(base, Contour)
        elements = list(base.elements)
        start, end = ends[seed]
        members = {seed}
        for forward in (True, False):
            while math.dist(start, end) > gate:
                p = end if forward else start
                opts = candidates(p, members, base.layer)
                if not opts:
                    break
                tan = (
                    element_tangents(elements[-1])[1]
                    if forward
                    else element_tangents(elements[0])[0]
                )
                j, at_start, _ = pick(opts, tan, forward)
                cj = graphs[j]
                assert isinstance(cj, Contour)
                used.add(j)
                members.add(j)
                merges += 1
                if forward:
                    piece = cj if at_start else reverse_contour(cj)
                    elements.extend(piece.elements)
                    end = contour_endpoints(piece)[1]
                else:
                    piece = cj if not at_start else reverse_contour(cj)
                    elements[:0] = piece.elements
                    start = contour_endpoints(piece)[0]
        if len(members) > 1:
            merged[seed] = refresh_contour(replace(base, elements=elements))
            absorbed.update(members - {seed})

    out: list[Graph] = []
    for i, g in enumerate(graphs):
        if i not in absorbed:
            out.append(merged.get(i, g))
    return out, merges


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def apply_import_gates(doc: ChfDocument, params: GateParams | None = None) -> GateReport:
    """Run the ``IGP`` gates on an imported document in place (05 §4, 01 §1.2).

    Order (UNVERIFIED): duplicate lines, combine near lines, minimal graphics,
    then ``IGP.AutoSortType`` through :func:`nexcut.ops.sort.sort_document`.
    """
    from nexcut.ops.sort import AutoSortType, sort_document

    p = params or GateParams()
    report = GateReport()
    graphs: list[Graph] = list(doc.graphs)
    if p.filter_overlap:
        graphs, report.overlaps_removed = remove_overlaps(graphs, p.overlap_gate)
    if p.merge_type != MergeType.NONE:
        graphs, report.contours_merged = merge_connected(graphs, p.connect_gate, p.merge_type)
    if p.filter_micro:
        graphs, report.micro_removed = filter_micro_graphs(graphs, p.micro_gate)
    doc.graphs = graphs
    if p.auto_sort_type != AutoSortType.NONE:
        result = sort_document(doc, AutoSortType(p.auto_sort_type).to_sort_type())
        report.sorted_by = p.auto_sort_type
        report.notes.extend(result.notes)
    return report
