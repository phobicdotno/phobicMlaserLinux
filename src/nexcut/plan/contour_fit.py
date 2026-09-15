"""Contour -> piece list: the port of ``CContoutSmooth::process`` (analysis 05 §6, 05 §14 item 1).

Pipeline of ``process`` (05 §6.2, CADModule ``0x100f0410``), reproduced stage by stage:

1. **refit** (``0x100ed4f0``) with the tolerance clamped to ``[0.01, 0.3]`` mm
   (``.rdata:0x1010f7e8`` / ``0x101119d0``) -> dump ``before_mergeLinearGly.txt``;
2. **mergeLinearGly** (``0x100ef340``, collinearity 0.001 mm) -> ``after_mergeLinearGly.txt``;
3. **smoothGly** (``0x100efef0(this, contour, 0.1, 2.0)``, corner threshold 30 deg, reversal when
   the tangent dot product is below ``-0.984375``) -> ``after_smoothGly.txt``;
4. **setDataWithoutReFit** (``0x100ec5e0``): piece list ``{cumulative length, radius, feed,
   cap, factor}`` (the 0x48-byte ``CNurbsContour`` record, 05 §6.4) plus the PWM segment
   lengths -> ``setDataWithoutReFit_segs.txt`` and ``after_setDataWithoutReFit.txt``; skipped
   when ``skip_set_data`` is set (05 §6.3 verifier finding, ``[ebp+0x14]`` test at
   ``0x100f0ce8``).

What is EVIDENCE and what is a port design (05 §12 rates the blend geometry *medium*):

* EVIDENCE: the clamp, the 0.001 mm collinear merge, the 30 deg / -0.984375 thresholds, the
  stage order and dump files, that straight pieces stay exactly straight and that consecutive
  collinear straight PWM segments are merged into one piece *regardless of laser state* while
  the PWM segment list keeps one entry per glyph (``after_setDataWithoutReFit.txt`` 141 =
  25+3+25+3+25+60 vs ``setDataWithoutReFit_segs.txt``, 05 §6.3), and that a smooth closed curve
  is one glyph after refit (``before_mergeLinearGly.txt`` = one 284.266 mm glyph).
* UNVERIFIED (port design, marked at each constant): the vendor fits degree-3 openNURBS curves;
  this port keeps curves as dense polylines with an analytic or numerically estimated minimum
  radius of curvature, rounds corners <= 30 deg with a tangent arc whose deviation equals the
  refit tolerance (the effect of a chord-tolerance NURBS fit), blends corners > 30 deg with an
  arc of deviation 0.1 mm capped at radius 2.0 mm (the two ``smoothGly`` arguments), forces a
  stop at reversals (piece cap 0, the same mechanism as the split-and-stop routine 05 §8), and
  merges collinear lines in ``mergeLinearGly`` only when their laser state and feed agree.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import IntEnum

import numpy as np
from numpy.typing import NDArray

from nexcut.model.flatten import flatten_glyph
from nexcut.model.glyph import (
    ArcGlyph,
    CircleGlyph,
    EllipseArcGlyph,
    Glyph,
    LwPolylineGlyph,
    PointGlyph,
    SegmentGlyph,
    SplineGlyph,
)
from nexcut.model.graph import Contour

__all__ = [
    "BLEND_ANGLE",
    "COLLINEAR_EPS",
    "DUMP_FILES",
    "REFIT_TOL_MAX",
    "REFIT_TOL_MIN",
    "REVERSAL_COS",
    "SMOOTH_DEVIATION",
    "SMOOTH_MAX_RADIUS",
    "ContourFitResult",
    "FitGlyph",
    "GlyphKind",
    "PieceList",
    "arc_glyph",
    "build_pieces",
    "clamp_refit_tolerance",
    "curve_glyph",
    "format_length_dump",
    "glyphs_from_contour",
    "glyphs_from_model",
    "line_glyph",
    "merge_linear",
    "process",
    "refit",
    "smooth",
]

FloatArray = NDArray[np.float64]

REFIT_TOL_MIN = 0.01
"""Refit tolerance lower clamp [mm] (05 §6.2 step 1, ``.rdata:0x1010f7e8``)."""
REFIT_TOL_MAX = 0.3
"""Refit tolerance upper clamp [mm] (05 §6.2 step 1, ``.rdata:0x101119d0``)."""
COLLINEAR_EPS = 0.001
"""mergeLinearGly collinearity distance [mm] (05 §6.2 step 3)."""
BLEND_ANGLE = math.radians(30.0)
"""Corner angle threshold: ``30*(pi/180)`` in smoothGly and refit (05 §6.2 steps 1 and 5)."""
REVERSAL_COS = -0.984375
"""Tangent dot product below which a corner is a reversal, not blended (05 §6.2, ``.rdata:0x100199d8``)."""
SMOOTH_DEVIATION = 0.1
"""First smoothGly argument (05 §6.2 step 5). UNVERIFIED meaning: used here as blend deviation [mm]."""
SMOOTH_MAX_RADIUS = 2.0
"""Second smoothGly argument (05 §6.2 step 5). UNVERIFIED meaning: used here as maximum blend radius [mm]."""
MIN_GLYPH_LENGTH = 1e-5
"""Glyphs shorter than this are dropped after trimming. UNVERIFIED: the 1e-5 constant of refit (05 §6.2)."""
TANGENT_JOIN = 0.005
"""Max tangent change [rad] for two curves to be one glyph. UNVERIFIED: the 0.005 constant of refit (05 §6.2)."""
CURVE_SAGITTA = 1e-3
"""Sagitta [mm] used to sample analytic arcs into polylines (port choice)."""

DUMP_FILES = (
    "before_mergeLinearGly.txt",
    "after_mergeLinearGly.txt",
    "after_smoothGly.txt",
    "setDataWithoutReFit_segs.txt",
    "after_setDataWithoutReFit.txt",
)
"""Dump files written by ``process`` in this order (05 §6.2)."""


class GlyphKind(IntEnum):
    """Straight line or curve (the vendor keeps lines exactly straight, 05 §6.2 INFERENCE high)."""

    LINE = 2
    CURVE = 7


@dataclass(slots=True)
class FitGlyph:
    """One glyph of the smoother's working list.

    ``points`` (n x 2) is the geometry in traversal direction (2 points for a line).
    ``radius`` = minimum radius of curvature [mm] (``inf`` for a line).  ``length`` is the
    exact length when known (arcs, or a value taken from a vendor dump), else the polyline
    length.  ``laser_on`` is the PWM state of the segment (05 §5.2), ``feed`` the programmed
    feed (None = contour feed), ``cap`` the speed cap at the glyph end (``0`` = forced stop,
    piece ``+0x18``), ``factor`` the speed factor (piece ``+0x40``).  ``corner_radius`` is a port
    extension: the equivalent radius of an unblended corner at the glyph end (``inf`` = none).
    """

    kind: GlyphKind
    points: FloatArray
    radius: float = math.inf
    length: float = -1.0
    laser_on: bool = True
    feed: float | None = None
    cap: float = math.inf
    factor: float = 1.0
    corner_radius: float = math.inf

    def __post_init__(self) -> None:
        self.points = np.asarray(self.points, dtype=np.float64).reshape(-1, 2)
        if self.length < 0.0:
            self.length = _polyline_length(self.points)

    @property
    def start(self) -> FloatArray:
        """First point."""
        return self.points[0]

    @property
    def end(self) -> FloatArray:
        """Last point."""
        return self.points[-1]

    @property
    def start_tangent(self) -> FloatArray:
        """Unit tangent at the start (first non-degenerate chord)."""
        return _first_direction(self.points)

    @property
    def end_tangent(self) -> FloatArray:
        """Unit tangent at the end (last non-degenerate chord)."""
        return _first_direction(self.points[::-1]) * -1.0


def _polyline_length(points: FloatArray) -> float:
    if len(points) < 2:
        return 0.0
    d = np.diff(points, axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def _first_direction(points: FloatArray) -> FloatArray:
    """Unit tangent at ``points[0]``; second-order one-sided difference when the first two chords
    have similar length (sampled curves), else the first non-degenerate chord."""
    m = len(points)
    for i in range(m - 1):
        dx0 = float(points[i + 1, 0] - points[i, 0])
        dy0 = float(points[i + 1, 1] - points[i, 1])
        h0 = math.hypot(dx0, dy0)
        if h0 <= 1e-12:
            continue
        vx, vy = dx0 / h0, dy0 / h0
        if i == 0 and m >= 3:
            dx1 = float(points[2, 0] - points[1, 0])
            dy1 = float(points[2, 1] - points[1, 1])
            h1 = math.hypot(dx1, dy1)
            if h1 > 1e-12 and 0.5 <= h1 / h0 <= 2.0:
                # derivative of the quadratic through p0, p1, p2 at p0 (non-uniform spacing)
                c0 = (2.0 * h0 + h1) / (h0 + h1) / h0
                c1 = h0 / (h0 + h1) / h1
                wx, wy = dx0 * c0 - dx1 * c1, dy0 * c0 - dy1 * c1
                ln = math.hypot(wx, wy)
                if ln > 1e-12:
                    vx, vy = wx / ln, wy / ln
        return np.array([vx, vy])
    return np.array([1.0, 0.0])


def line_glyph(p0: Sequence[float], p1: Sequence[float], **kw: object) -> FitGlyph:
    """Straight glyph from ``p0`` to ``p1``."""
    return FitGlyph(GlyphKind.LINE, np.array([p0, p1], dtype=np.float64), **kw)  # type: ignore[arg-type]


def arc_glyph(
    center: Sequence[float], radius: float, a0: float, a1: float, **kw: object
) -> FitGlyph:
    """Circular arc from angle ``a0`` to ``a1`` [rad] (sweep sign = direction, 03 §7), exact length."""
    sweep = a1 - a0
    if radius <= 0.0 or sweep == 0.0:
        n = 1
    else:
        step = 2.0 * math.acos(max(1.0 - CURVE_SAGITTA / radius, -1.0))
        n = int(min(max(math.ceil(abs(sweep) / max(step, 1e-9)), 16), 20000))
    ang = a0 + sweep * np.linspace(0.0, 1.0, n + 1)
    pts = np.column_stack((center[0] + radius * np.cos(ang), center[1] + radius * np.sin(ang)))
    return FitGlyph(GlyphKind.CURVE, pts, radius=radius, length=abs(sweep) * radius, **kw)  # type: ignore[arg-type]


def curve_glyph(points: Sequence[Sequence[float]] | FloatArray, **kw: object) -> FitGlyph:
    """Free-form curve from sampled points; radius = numerically estimated minimum radius."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    radius = kw.pop("radius", None)
    r = _polyline_min_radius(pts) if radius is None else float(radius)  # type: ignore[arg-type]
    return FitGlyph(GlyphKind.CURVE, pts, radius=r, **kw)  # type: ignore[arg-type]


def _polyline_min_radius(points: FloatArray) -> float:
    """Minimum circumradius over triplets of an arc-length resampling (port estimate, UNVERIFIED)."""
    if len(points) < 3:
        return math.inf
    d = np.diff(points, axis=0)
    seg = np.hypot(d[:, 0], d[:, 1])
    s = np.concatenate(([0.0], np.cumsum(seg)))
    total = s[-1]
    if total <= 1e-9:
        return math.inf
    h = min(max(total / 64.0, 0.02), 1.0)
    n = max(int(total / h), 2)
    u = np.linspace(0.0, total, n + 1)
    idx = np.unique(np.clip(np.searchsorted(s, u), 0, len(points) - 1))
    if idx.size < 3:
        idx = np.arange(len(points))
    p = points[idx]  # original samples (they lie on the curve), about h apart
    a = p[1:-1] - p[:-2]
    b = p[2:] - p[1:-1]
    c = p[2:] - p[:-2]
    cross = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
    la = np.hypot(a[:, 0], a[:, 1])
    lb = np.hypot(b[:, 0], b[:, 1])
    lc = np.hypot(c[:, 0], c[:, 1])
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(cross > 1e-15, la * lb * lc / (2.0 * cross), np.inf)
    return float(np.min(r)) if r.size else math.inf


# ---------------------------------------------------------------------------- model input
def glyphs_from_model(glyph: Glyph, direction: int = 1, **kw: object) -> list[FitGlyph]:
    """Convert one ``nexcut.model`` glyph (03 §7) into fit glyphs in traversal direction.

    Segments -> line; arcs/circles and bulge spans -> exact arcs; ellipse arcs and splines ->
    sampled curves (``flatten_glyph`` with a 0.05 mm step).  ``direction == -1`` reverses
    (03 §6.1 line 10).  Point glyphs yield nothing.
    """
    out: list[FitGlyph] = []
    match glyph:
        case PointGlyph():
            return []
        case SegmentGlyph():
            out = [line_glyph(glyph.p0, glyph.p1, **kw)]
        case ArcGlyph():
            out = [arc_glyph(glyph.center, glyph.radius, glyph.start_angle, glyph.end_angle, **kw)]
        case CircleGlyph():
            out = [arc_glyph(glyph.center, glyph.radius, 0.0, 2.0 * math.pi, **kw)]
        case LwPolylineGlyph():
            v = glyph.vertices
            spans = len(v) if glyph.closed else len(v) - 1
            for i in range(spans):
                p0, p1, bulge = v[i].pt, v[(i + 1) % len(v)].pt, v[i].bulge
                if abs(bulge) < 1e-12:
                    out.append(line_glyph(p0, p1, **kw))
                    continue
                theta = 4.0 * math.atan(bulge)
                chord = math.dist(p0, p1)
                if chord < 1e-12:
                    continue
                r = chord / (2.0 * math.sin(abs(theta) / 2.0))
                mx, my = (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0
                dd = math.sqrt(max(r * r - (chord / 2.0) ** 2, 0.0))
                nx, ny = -(p1[1] - p0[1]) / chord, (p1[0] - p0[0]) / chord
                sgn = 1.0 if (bulge > 0) == (abs(theta) < math.pi) else -1.0
                cx, cy = mx + sgn * nx * dd, my + sgn * ny * dd
                a0 = math.atan2(p0[1] - cy, p0[0] - cx)
                out.append(arc_glyph((cx, cy), r, a0, a0 + theta, **kw))
        case EllipseArcGlyph() | SplineGlyph():
            out = [curve_glyph(pl, **kw) for pl in flatten_glyph(glyph, 0.05) if len(pl) >= 2]
    if direction == -1:
        out = [_reversed(g) for g in reversed(out)]
    return out


def _reversed(g: FitGlyph) -> FitGlyph:
    return replace(g, points=g.points[::-1].copy())


def glyphs_from_contour(contour: Contour, **kw: object) -> list[FitGlyph]:
    """All elements of a ``CGlyContour`` (03 §6.1) as fit glyphs, direction flags applied."""
    out: list[FitGlyph] = []
    for el in contour.elements:
        out.extend(glyphs_from_model(el.glyph, el.direction, **kw))
    return out


# ------------------------------------------------------------------------------ geometry
def clamp_refit_tolerance(tol: float) -> float:
    """``[0.01, 0.3]`` mm clamp of the refit tolerance (05 §6.2 step 1, EVIDENCE)."""
    return min(max(tol, REFIT_TOL_MIN), REFIT_TOL_MAX)


def _turn(t1: FloatArray, t2: FloatArray) -> tuple[float, float, float]:
    """``(angle, cross, dot)`` of the direction change from ``t1`` to ``t2``."""
    dot = float(np.clip(t1[0] * t2[0] + t1[1] * t2[1], -1.0, 1.0))
    cross = float(t1[0] * t2[1] - t1[1] * t2[0])
    return math.atan2(abs(cross), dot), cross, dot


def _deviation_radius(phi: float, deviation: float) -> float:
    half = math.cos(phi / 2.0)
    if half >= 1.0:
        return math.inf
    return deviation * half / (1.0 - half)


def _compatible(a: FitGlyph, b: FitGlyph) -> bool:
    return a.laser_on == b.laser_on and a.feed == b.feed and a.factor == b.factor


def _blend(
    glyphs: list[FitGlyph],
    select: float,
    upper: float | None,
    deviation: float,
    max_radius: float,
) -> list[FitGlyph]:
    """Round the corners whose turn angle lies in ``(select, upper]`` with tangent arcs.

    Line-line corners get an arc of deviation ``deviation`` (radius capped at ``max_radius``),
    shrunk so that no line loses more than half its length.  Corners touching a curve are not
    moved; the equivalent radius is stored in ``corner_radius`` of the incoming glyph.  A
    reversal (``dot < REVERSAL_COS``) is never blended; the incoming glyph gets ``cap = 0``.
    Port design; see module docstring (UNVERIFIED geometry).
    """
    n = len(glyphs)
    if n < 2:
        return list(glyphs)
    work = [replace(g) for g in glyphs]
    tangent = [0.0] * (n - 1)
    corner: list[tuple[float, float] | None] = [None] * (n - 1)
    for i in range(n - 1):
        a, b = work[i], work[i + 1]
        if float(np.hypot(*(a.end - b.start))) > COLLINEAR_EPS:
            continue  # not connected: nothing to blend
        phi, cross, dot = _turn(a.end_tangent, b.start_tangent)
        if phi <= select or (upper is not None and phi > upper):
            continue
        if dot < REVERSAL_COS:
            a.cap = 0.0
            continue
        r = min(_deviation_radius(phi, deviation), max_radius)
        if a.kind is GlyphKind.LINE and b.kind is GlyphKind.LINE and a.cap > 0.0:
            t = r * math.tan(phi / 2.0)
            t = min(t, 0.5 * a.length, 0.5 * b.length)
            if t <= MIN_GLYPH_LENGTH:
                a.corner_radius = min(a.corner_radius, t / math.tan(phi / 2.0))
                continue
            tangent[i] = t
            corner[i] = (t / math.tan(phi / 2.0), math.copysign(phi, cross))
        elif phi > TANGENT_JOIN:
            a.corner_radius = min(a.corner_radius, r)
    out: list[FitGlyph] = []
    for i, g in enumerate(work):
        appended: FitGlyph | None = None
        if g.kind is GlyphKind.LINE:
            t0 = tangent[i - 1] if i > 0 else 0.0
            t1 = tangent[i] if i < n - 1 else 0.0
            if t0 or t1:
                d = g.end - g.start
                ln = float(np.hypot(*d))
                u = d / ln if ln > 0 else np.array([1.0, 0.0])
                p0, p1 = g.start + u * t0, g.end - u * t1
                g = replace(g, points=np.array([p0, p1]), length=float(np.hypot(*(p1 - p0))))
            if g.length > MIN_GLYPH_LENGTH:
                out.append(g)
                appended = g
        else:
            out.append(g)
            appended = g
        blend = corner[i] if i < n - 1 else None
        if blend is not None:
            r, signed = blend
            a = work[i]
            u = a.end_tangent
            p = a.end - u * tangent[i]
            normal = np.array([-u[1], u[0]]) * (1.0 if signed > 0 else -1.0)
            c = p + normal * r
            start_ang = math.atan2(p[1] - c[1], p[0] - c[0])
            arc = arc_glyph(
                (float(c[0]), float(c[1])),
                r,
                start_ang,
                start_ang + signed,
                laser_on=a.laser_on,
                feed=a.feed,
                factor=a.factor,
            )
            # the end-of-glyph attributes move to the end of the inserted arc
            arc.cap, arc.corner_radius = a.cap, a.corner_radius
            if appended is not None:
                appended.cap, appended.corner_radius = math.inf, math.inf
            out.append(arc)
    return out


def _merge_tangent_curves(glyphs: list[FitGlyph], tol: float) -> list[FitGlyph]:
    """Join tangent-continuous curves (and short connecting lines) into one curve glyph."""
    out: list[FitGlyph] = []
    run: list[FitGlyph] = []

    def flush() -> None:
        if not run:
            return
        if len(run) == 1:
            out.append(run[0])
        else:
            first, last = run[0], run[-1]
            pts = np.vstack([first.points] + [g.points[1:] for g in run[1:]])
            out.append(
                FitGlyph(
                    GlyphKind.CURVE,
                    pts,
                    radius=min(g.radius for g in run),
                    length=math.fsum(g.length for g in run),
                    laser_on=first.laser_on,
                    feed=first.feed,
                    cap=last.cap,
                    factor=first.factor,
                    corner_radius=last.corner_radius,
                )
            )
        run.clear()

    for g in glyphs:
        if run:
            prev = run[-1]
            joinable = (
                run[0].kind is GlyphKind.CURVE
                and prev.cap == math.inf
                and _compatible(prev, g)
                and float(np.hypot(*(prev.end - g.start))) <= COLLINEAR_EPS
                and _turn(prev.end_tangent, g.start_tangent)[0] <= TANGENT_JOIN
                and (g.kind is GlyphKind.CURVE or g.length <= tol)
            )
            if joinable:
                run.append(g)
                continue
            flush()
        run.append(g)
    flush()
    return out


def refit(glyphs: Iterable[FitGlyph], tol: float) -> list[FitGlyph]:
    """Stage 1 - ``0x100ed4f0`` (05 §6.2): tolerance clamped to ``[0.01, 0.3]``.

    Port behaviour (UNVERIFIED, replaces the openNURBS fit): corners of at most 30 deg are
    rounded with a tangent arc whose deviation is the tolerance (for a flattened curve the arcs
    meet and recover the curve's radius), zero-length glyphs are dropped and tangent-continuous
    curves become one glyph.  Straight glyphs and exact arcs keep their geometry and length.
    """
    t = clamp_refit_tolerance(tol)
    work = [g for g in glyphs if g.length > MIN_GLYPH_LENGTH]
    work = _blend(work, 1e-9, BLEND_ANGLE, t, math.inf)
    return _merge_tangent_curves(work, t)


def _collinear(a: FitGlyph, b: FitGlyph, eps: float) -> bool:
    if a.kind is not GlyphKind.LINE or b.kind is not GlyphKind.LINE:
        return False
    if float(np.hypot(*(a.end - b.start))) > eps:
        return False
    d = b.end - a.start
    ln = float(np.hypot(*d))
    if ln <= 0.0:
        return False
    if float(np.dot(a.end - a.start, b.end - b.start)) <= 0.0:
        return False
    dist = abs(float(d[0] * (a.end[1] - a.start[1]) - d[1] * (a.end[0] - a.start[0]))) / ln
    return dist <= eps


def merge_linear(glyphs: Iterable[FitGlyph], eps: float = COLLINEAR_EPS) -> list[FitGlyph]:
    """Stage 2 - mergeLinearGly (``0x100ef340``, 0.001 mm, 05 §6.2 step 3).

    Consecutive straight glyphs whose shared vertex lies within ``eps`` of the line through the
    outer endpoints are merged.  Port rule (UNVERIFIED): only glyphs with equal laser state, feed
    and factor, and no forced stop in between, are merged, so PWM boundaries survive (consistent
    with ``setDataWithoutReFit_segs.txt`` still listing 25/3/60 separately, 05 §6.3).
    """
    out: list[FitGlyph] = []
    for g in glyphs:
        if (
            out
            and _collinear(out[-1], g, eps)
            and _compatible(out[-1], g)
            and out[-1].cap == math.inf
        ):
            prev = out[-1]
            out[-1] = replace(
                prev,
                points=np.array([prev.start, g.end]),
                length=-1.0,
                cap=g.cap,
                corner_radius=g.corner_radius,
            )
            out[-1].length = float(np.hypot(*(g.end - prev.start)))
            continue
        out.append(replace(g))
    return out


def smooth(
    glyphs: Iterable[FitGlyph],
    deviation: float = SMOOTH_DEVIATION,
    max_radius: float = SMOOTH_MAX_RADIUS,
) -> list[FitGlyph]:
    """Stage 3 - smoothGly (``0x100efef0(this, contour, 0.1, 2.0)``, 05 §6.2 step 5).

    Corners sharper than 30 deg are blended unless they are reversals (tangent dot product
    ``< -0.984375`` -> forced stop).  Blend geometry UNVERIFIED (see :func:`_blend`).
    """
    return _blend(list(glyphs), BLEND_ANGLE, None, deviation, max_radius)


# ------------------------------------------------------------------------------ pieces
@dataclass(slots=True)
class PieceList:
    """``CNurbsContour`` piece records (0x48 bytes each, 05 §6.4) as parallel arrays.

    ``cum_length[i]`` = cumulative arc length at the *end* of piece ``i`` (``+0x00``);
    ``radius`` (``+0x08``); ``feed`` (``+0x10``); ``cap`` (``+0x18``, 0 = forced stop, ``inf`` =
    none); ``factor`` (``+0x40``).  Port extensions: ``corner_radius`` (equivalent radius of an
    unblended corner at the piece end), ``kind`` (``GlyphKind`` value) and ``pwm_segments`` (one
    length per glyph, ``setDataWithoutReFit_segs.txt``) with ``pwm_laser_on``.
    """

    cum_length: FloatArray
    radius: FloatArray
    feed: FloatArray
    cap: FloatArray
    factor: FloatArray
    corner_radius: FloatArray
    kind: NDArray[np.int64]
    pwm_segments: FloatArray = field(default_factory=lambda: np.empty(0))
    pwm_laser_on: NDArray[np.bool_] = field(default_factory=lambda: np.empty(0, dtype=bool))

    def __len__(self) -> int:
        return int(self.cum_length.size)

    @property
    def lengths(self) -> FloatArray:
        """Piece lengths (differences of ``cum_length``)."""
        return np.diff(self.cum_length, prepend=0.0)

    @property
    def total_length(self) -> float:
        """Total path length (``totalNurbsLength``)."""
        return float(self.cum_length[-1]) if self.cum_length.size else 0.0

    @classmethod
    def from_arrays(
        cls,
        lengths: Sequence[float] | FloatArray,
        radius: Sequence[float] | FloatArray | float = math.inf,
        feed: Sequence[float] | FloatArray | float = 0.0,
        cap: Sequence[float] | FloatArray | float = math.inf,
        factor: Sequence[float] | FloatArray | float = 1.0,
        corner_radius: Sequence[float] | FloatArray | float = math.inf,
    ) -> PieceList:
        """Build a piece list from per-piece lengths (test/helper constructor)."""
        ln = np.asarray(lengths, dtype=np.float64)
        n = ln.size

        def arr(v: Sequence[float] | FloatArray | float) -> FloatArray:
            return np.broadcast_to(np.asarray(v, dtype=np.float64), (n,)).copy()

        return cls(
            np.cumsum(ln),
            arr(radius),
            arr(feed),
            arr(cap),
            arr(factor),
            arr(corner_radius),
            np.where(np.isinf(arr(radius)), int(GlyphKind.LINE), int(GlyphKind.CURVE)).astype(
                np.int64
            ),
            ln.copy(),
            np.ones(n, dtype=bool),
        )


def build_pieces(glyphs: Sequence[FitGlyph], feed: float, eps: float = COLLINEAR_EPS) -> PieceList:
    """Stage 4 - setDataWithoutReFit (``0x100ec5e0``, 05 §6.2 step 7, §6.3, §6.4).

    One PWM segment per glyph; one piece per curve glyph; consecutive collinear straight glyphs
    are merged into one piece **regardless of laser state** (EVIDENCE: 141 = 25+3+25+3+25+60)
    as long as feed/factor agree and no forced stop lies in between (port rule).
    ``feed`` is used for glyphs whose ``feed`` is None.
    """
    lengths: list[float] = []
    radius: list[float] = []
    feeds: list[float] = []
    caps: list[float] = []
    factors: list[float] = []
    corners: list[float] = []
    kinds: list[int] = []
    cur: FitGlyph | None = None  # geometry of the current straight piece
    for g in glyphs:
        gfeed = feed if g.feed is None else g.feed
        if (
            cur is not None
            and _collinear(cur, g, eps)
            and caps[-1] == math.inf
            and feeds[-1] == gfeed
            and factors[-1] == g.factor
        ):
            cur = line_glyph(cur.start, g.end)
            lengths[-1] = cur.length
            caps[-1], corners[-1] = g.cap, g.corner_radius
            continue
        lengths.append(g.length)
        radius.append(g.radius if g.kind is GlyphKind.CURVE else math.inf)
        feeds.append(gfeed)
        caps.append(g.cap)
        factors.append(g.factor)
        corners.append(g.corner_radius)
        kinds.append(int(g.kind))
        cur = g if g.kind is GlyphKind.LINE else None
    seg = np.array([g.length for g in glyphs], dtype=np.float64)
    return PieceList(
        np.cumsum(np.array(lengths, dtype=np.float64)),
        np.array(radius, dtype=np.float64),
        np.array(feeds, dtype=np.float64),
        np.array(caps, dtype=np.float64),
        np.array(factors, dtype=np.float64),
        np.array(corners, dtype=np.float64),
        np.array(kinds, dtype=np.int64),
        seg,
        np.array([g.laser_on for g in glyphs], dtype=bool),
    )


def format_length_dump(lengths: Iterable[float], total_label: str) -> bytes:
    """Dump file body as ``process`` writes it (05 §6.2/§6.3).

    One ``ostream << double`` per line (default precision 6 = ``%g``), then
    ``<total_label><total>``; the stream is a text-mode ``ofstream`` on Windows, so every ``\\n``
    becomes CRLF (EVIDENCE: all five package dumps end each line with CRLF).  Labels
    (CADModule ``.rdata``): ``"total: "``, ``"totalPwmSegments: "``, ``"totalNurbsLength: "``.
    """
    vals = [float(v) for v in lengths]
    lines = [f"{v:g}" for v in vals]
    lines.append(f"{total_label}{math.fsum(vals):g}")
    return ("\r\n".join(lines) + "\r\n").encode("ascii")


@dataclass(slots=True)
class ContourFitResult:
    """Result of :func:`process`: glyph lists per stage, the piece list and the dump bodies."""

    refitted: list[FitGlyph]
    merged: list[FitGlyph]
    smoothed: list[FitGlyph]
    pieces: PieceList | None
    dumps: dict[str, bytes]


def process(
    glyphs: Iterable[FitGlyph],
    tol: float,
    feed: float,
    *,
    skip_set_data: bool = False,
    smooth_deviation: float = SMOOTH_DEVIATION,
    smooth_max_radius: float = SMOOTH_MAX_RADIUS,
) -> ContourFitResult:
    """``CContoutSmooth::process`` (05 §6.2): refit -> merge -> smooth -> setDataWithoutReFit.

    Produces the dump bodies of all five files (only the first three when ``skip_set_data``,
    05 §6.3 verifier finding).  Nothing is written to disk here.
    """
    r = refit(glyphs, tol)
    dumps = {DUMP_FILES[0]: format_length_dump((g.length for g in r), "total: ")}
    m = merge_linear(r)
    dumps[DUMP_FILES[1]] = format_length_dump((g.length for g in m), "total: ")
    s = smooth(m, smooth_deviation, smooth_max_radius)
    dumps[DUMP_FILES[2]] = format_length_dump((g.length for g in s), "total: ")
    pieces: PieceList | None = None
    if not skip_set_data:
        pieces = build_pieces(s, feed)
        dumps[DUMP_FILES[3]] = format_length_dump(pieces.pwm_segments, "totalPwmSegments: ")
        dumps[DUMP_FILES[4]] = format_length_dump(pieces.lengths, "totalNurbsLength: ")
    return ContourFitResult(r, m, s, pieces, dumps)
