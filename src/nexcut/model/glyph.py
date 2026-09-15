"""Glyph (``IGlyph``) records of the ``.chf`` model, type ids 1-7.

Every class mirrors one ``CEditable*`` class of ``Module/CADModule.dll`` and
keeps exactly the fields its ``Read``/``Write`` pair serialises, in file order
(analysis 03 §7).  Geometry is in millimetres, angles in radians, frame Y-up
(03 §10; the Y-up choice is INFERENCE medium).

The per-glyph direction flag is *not* a glyph field: the original stores it in
the owning contour's element list (03 §6.1 line 10, 03 §7 last paragraph), so it
lives in :class:`nexcut.model.graph.ContourElement`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import NamedTuple


class Vec2(NamedTuple):
    """A 2-D point/vector in mm, serialised by ``WritePoint`` as ``%f,%f`` (03 §4.1)."""

    x: float
    y: float


class GlyphType(IntEnum):
    """Glyph type ids written as ``[glyph+0x8]`` (03 §7, factory jump table ``0x10059d50``)."""

    POINT = 1
    SEGMENT = 2
    ARC = 3
    CIRCLE = 4
    ELLIPSE_ARC = 5
    LWPOLYLINE = 6
    SPLINE = 7


class Direction(IntEnum):
    """Traversal flag of a glyph inside a contour (03 §6.1 line 10).

    ``1`` = natural direction, ``-1`` = reversed (``0x1005b438``: the curve
    parameter is mirrored ``t = 1 - t`` when the flag is ``-1``).
    """

    FORWARD = 1
    REVERSED = -1


@dataclass(slots=True)
class PointGlyph:
    """``CEditablePoint`` (type 1): one point ``[+0x60]`` - drill/pierce (03 §7)."""

    pt: Vec2

    TYPE = GlyphType.POINT


@dataclass(slots=True)
class SegmentGlyph:
    """``CEditableSegment`` (type 2): straight line ``p0 [+0x60]`` -> ``p1 [+0x70]`` (03 §7)."""

    p0: Vec2
    p1: Vec2

    TYPE = GlyphType.SEGMENT


@dataclass(slots=True)
class ArcGlyph:
    """``CEditableArc`` (type 3): circular arc (03 §7).

    ``center [+0x60]``, ``radius [+0x70]``, ``start_angle [+0x78]``,
    ``end_angle [+0x80]`` in radians CCW from +X; sweep = ``end - start``
    (positive = CCW is INFERENCE high; no sample contains an arc).
    """

    center: Vec2
    radius: float
    start_angle: float
    end_angle: float

    TYPE = GlyphType.ARC


@dataclass(slots=True)
class CircleGlyph:
    """``CEditableCircle`` (type 4): full circle, starts at angle 0 and runs CCW (03 §7)."""

    center: Vec2
    radius: float

    TYPE = GlyphType.CIRCLE


@dataclass(slots=True)
class EllipseArcGlyph:
    """``CEditableEllipsArc`` (type 5): DXF-style ellipse arc (03 §7).

    ``major_axis`` is relative to ``center``; ``ratio = b/a``; parameters in
    radians.  ``P(t) = c + cos t * M + sin t * ratio * (-My, Mx)``.
    """

    center: Vec2
    major_axis: Vec2
    ratio: float
    start_param: float
    end_param: float

    TYPE = GlyphType.ELLIPSE_ARC


@dataclass(slots=True)
class LwPolyVertex:
    """One 24-byte ``CEditableLwpoly`` vertex; written point first, bulge second (03 §7).

    ``bulge = tan(theta/4)`` of the arc from this vertex to the next (DXF
    convention; the last vertex's bulge applies to the closing segment).
    """

    pt: Vec2
    bulge: float = 0.0


@dataclass(slots=True)
class LwPolylineGlyph:
    """``CEditableLwpoly`` (type 6): lightweight polyline with bulges (03 §7).

    ``closed`` is kept as the raw int (``1`` = closed) so any value round-trips.
    """

    closed: int
    vertices: list[LwPolyVertex] = field(default_factory=list)

    TYPE = GlyphType.LWPOLYLINE


@dataclass(slots=True)
class SplineGlyph:
    """``CEditableSpline`` (type 7): cubic B-spline without weights (03 §7).

    ``int1 [+0xb4]`` and ``int2 [+0xb0]`` are unknown flags kept opaque
    (03 §12).  The reader requires ``len(control_points) >= 4`` and
    ``len(knots) == len(control_points) + 4`` (``0x1008f30a``), i.e. degree 3.
    """

    int1: int
    int2: int
    control_points: list[Vec2] = field(default_factory=list)
    knots: list[float] = field(default_factory=list)

    TYPE = GlyphType.SPLINE
    DEGREE = 3


Glyph = PointGlyph | SegmentGlyph | ArcGlyph | CircleGlyph | EllipseArcGlyph | LwPolylineGlyph | SplineGlyph

GLYPH_CLASSES: dict[GlyphType, type[Glyph]] = {
    GlyphType.POINT: PointGlyph,
    GlyphType.SEGMENT: SegmentGlyph,
    GlyphType.ARC: ArcGlyph,
    GlyphType.CIRCLE: CircleGlyph,
    GlyphType.ELLIPSE_ARC: EllipseArcGlyph,
    GlyphType.LWPOLYLINE: LwPolylineGlyph,
    GlyphType.SPLINE: SplineGlyph,
}
"""Glyph factory table (03 §7, ``CreateGlyph`` ``0x10059b90``)."""

GLYPH_NAMES: dict[GlyphType, str] = {
    GlyphType.POINT: "point",
    GlyphType.SEGMENT: "segment",
    GlyphType.ARC: "arc",
    GlyphType.CIRCLE: "circle",
    GlyphType.ELLIPSE_ARC: "ellipse_arc",
    GlyphType.LWPOLYLINE: "lwpolyline",
    GlyphType.SPLINE: "spline",
}
"""Names used by ``tools/chf_parse.py`` JSON dumps (03 §11)."""


def glyph_type(glyph: Glyph) -> GlyphType:
    """Return the type id a glyph is written with (03 §7)."""
    return glyph.TYPE
