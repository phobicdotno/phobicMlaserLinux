"""Glyph flattening and recomputation of a contour's cached geometry (analysis 03 §7, §11).

This is the geometry of ``tools/chf_parse.py`` (``flatten_glyph``/``--check``)
with the global chord step turned into a parameter; the numeric results for a
given step are identical.  The conventions it encodes are proven only as far as
the samples go (segment, circle, closed bulge polyline; 03 §11, 99-gaps §1):
arc/ellipse/spline evaluation is from disassembly (03 §7) and the positive-sweep
= CCW rule is INFERENCE high.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

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
from nexcut.model.graph import Contour

DEFAULT_STEP = 0.2
"""Chord step (mm) used for display flattening, as in ``tools/chf_parse.py``."""

CHECK_STEP = 0.002
"""Chord step (mm) used by the self-check (03 §11)."""

Polyline = list[tuple[float, float]]


def _arc_pts(c: Vec2 | tuple[float, float], r: float, a0: float, a1: float, step: float) -> Polyline:
    """Sample a circular arc from ``a0`` to ``a1`` (radians; sign of sweep = direction, 03 §7)."""
    sweep = a1 - a0
    n = max(8, int(abs(sweep) * max(r, 1.0) / step) + 1)
    n = min(n, 20000)
    return [
        (c[0] + r * math.cos(a0 + sweep * i / n), c[1] + r * math.sin(a0 + sweep * i / n))
        for i in range(n + 1)
    ]


def _bulge_pts(p0: Vec2, p1: Vec2, bulge: float, step: float) -> Polyline:
    """Sample one lwpolyline span with DXF bulge ``tan(theta/4)`` (03 §7, type 6)."""
    if abs(bulge) < 1e-12:
        return [(p0[0], p0[1]), (p1[0], p1[1])]
    theta = 4.0 * math.atan(bulge)
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    chord = math.hypot(dx, dy)
    if chord < 1e-12:
        return [(p0[0], p0[1]), (p1[0], p1[1])]
    r = chord / (2.0 * math.sin(abs(theta) / 2.0))
    mx, my = (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0
    d = math.sqrt(max(r * r - (chord / 2.0) ** 2, 0.0))
    nx, ny = -dy / chord, dx / chord  # centre left of the chord for CCW (positive bulge)
    if bulge > 0:
        cx, cy = mx + nx * d, my + ny * d
    else:
        cx, cy = mx - nx * d, my - ny * d
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    return _arc_pts((cx, cy), r, a0, a0 + theta, step)


def _ellipse_pts(g: EllipseArcGlyph, step: float) -> Polyline:
    """Sample a DXF ellipse arc ``c + cos t*M + sin t*ratio*(-My, Mx)`` (03 §7, type 5).

    An end parameter not greater than the start wraps by 2*pi (as the reference
    tool does; UNVERIFIED against the DLL - no sample contains an ellipse).
    """
    c, major, ratio, t0, t1 = g.center, g.major_axis, g.ratio, g.start_param, g.end_param
    if t1 <= t0 + 1e-12:
        t1 += 2 * math.pi
    a = math.hypot(major[0], major[1])
    n = max(16, int((t1 - t0) * max(a, 1.0) / step) + 1)
    n = min(n, 20000)
    px, py = -major[1] * ratio, major[0] * ratio
    out: Polyline = []
    for i in range(n + 1):
        t = t0 + (t1 - t0) * i / n
        out.append(
            (
                c[0] + math.cos(t) * major[0] + math.sin(t) * px,
                c[1] + math.cos(t) * major[1] + math.sin(t) * py,
            )
        )
    return out


def _bspline_pts(ctrl: list[Vec2], knots: list[float], degree: int = 3) -> Polyline:
    """Sample a non-rational B-spline by de Boor over ``[knots[p], knots[n]]`` (03 §7, type 7)."""
    m = len(ctrl)
    n = max(24, 8 * m)
    lo, hi = knots[degree], knots[m]
    out: Polyline = []
    for i in range(n + 1):
        u = lo + (hi - lo) * i / n
        if i == n:
            u = hi - 1e-12 * max(abs(hi), 1)
        k = degree
        while k < m - 1 and u >= knots[k + 1]:
            k += 1
        d = [(ctrl[j][0], ctrl[j][1]) for j in range(k - degree, k + 1)]
        for r in range(1, degree + 1):
            for j in range(degree, r - 1, -1):
                i0 = k - degree + j
                den = knots[i0 + degree - r + 1] - knots[i0]
                alpha = 0.0 if den == 0 else (u - knots[i0]) / den
                d[j] = (
                    (1 - alpha) * d[j - 1][0] + alpha * d[j][0],
                    (1 - alpha) * d[j - 1][1] + alpha * d[j][1],
                )
        out.append(d[degree])
    return out


def flatten_glyph(glyph: Glyph, step: float = DEFAULT_STEP) -> list[Polyline]:
    """Approximate a glyph by polylines in its *stored* direction (direction flag not applied).

    Behaviour per glyph type follows 03 §7; a point glyph yields a 1-point polyline.
    """
    match glyph:
        case PointGlyph():
            return [[(glyph.pt[0], glyph.pt[1])]]
        case SegmentGlyph():
            return [[(glyph.p0[0], glyph.p0[1]), (glyph.p1[0], glyph.p1[1])]]
        case ArcGlyph():
            return [_arc_pts(glyph.center, glyph.radius, glyph.start_angle, glyph.end_angle, step)]
        case CircleGlyph():
            return [_arc_pts(glyph.center, glyph.radius, 0.0, 2 * math.pi, step)]
        case EllipseArcGlyph():
            return [_ellipse_pts(glyph, step)]
        case LwPolylineGlyph():
            v = glyph.vertices
            pts: Polyline = []
            segs = len(v) if glyph.closed else len(v) - 1
            for i in range(segs):
                seg = _bulge_pts(v[i].pt, v[(i + 1) % len(v)].pt, v[i].bulge, step)
                if pts:
                    seg = seg[1:]
                pts.extend(seg)
            return [pts]
        case SplineGlyph():
            return [_bspline_pts(glyph.control_points, glyph.knots, SplineGlyph.DEGREE)]
    raise TypeError(f"not a glyph: {glyph!r}")


@dataclass(slots=True)
class ContourMeasure:
    """Geometry recomputed from a contour's glyphs (the cached fields of 03 §6.1 lines 3-7)."""

    length: float
    bbox_min: Vec2
    bbox_max: Vec2
    start: Vec2
    end: Vec2


def measure_contour(contour: Contour, step: float = CHECK_STEP) -> ContourMeasure | None:
    """Recompute length, bbox, start and end with direction flags applied (03 §6.1, §11).

    A glyph whose direction is ``-1`` is traversed reversed.  Returns ``None``
    for a contour without geometry.  Accuracy is limited by ``step`` (chord
    error of flattened curves).
    """
    total = 0.0
    pts: Polyline = []
    first: tuple[float, float] | None = None
    last: tuple[float, float] | None = None
    for el in contour.elements:
        for pl in flatten_glyph(el.glyph, step):
            if el.direction == -1:
                pl = pl[::-1]
            total += sum(math.dist(a, b) for a, b in zip(pl, pl[1:], strict=False))
            pts.extend(pl)
            if first is None:
                first = pl[0]
            last = pl[-1]
    if not pts or first is None or last is None:
        return None
    return ContourMeasure(
        length=total,
        bbox_min=Vec2(min(p[0] for p in pts), min(p[1] for p in pts)),
        bbox_max=Vec2(max(p[0] for p in pts), max(p[1] for p in pts)),
        start=Vec2(*first),
        end=Vec2(*last),
    )


def check_contour(contour: Contour, step: float = CHECK_STEP) -> list[str]:
    """Compare cached contour geometry with a recomputation; return problem strings.

    Tolerances are those of the reference ``--check`` (03 §11): 1e-3 * max(1, L)
    for length/start/end, 5e-3 * max(1, L) for the bbox corners.
    """
    m = measure_contour(contour, step)
    if m is None:
        return []
    tol = 1e-3 * max(1.0, m.length)
    btol = 5e-3 * max(1.0, m.length)
    problems: list[str] = []
    if abs(m.length - contour.length) > tol:
        problems.append(f"length file={contour.length:.6f} computed={m.length:.6f}")
    if math.dist(m.bbox_min, contour.bbox_min) > btol or math.dist(m.bbox_max, contour.bbox_max) > btol:
        problems.append(
            f"bbox file={list(contour.bbox_min)},{list(contour.bbox_max)} "
            f"computed={[list(m.bbox_min), list(m.bbox_max)]}"
        )
    if math.dist(m.start, contour.start) > tol:
        problems.append(f"start file={list(contour.start)} computed={list(m.start)}")
    if math.dist(m.end, contour.end) > tol:
        problems.append(f"end file={list(contour.end)} computed={list(m.end)}")
    return problems
