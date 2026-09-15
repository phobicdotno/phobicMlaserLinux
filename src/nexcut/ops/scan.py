"""Serpentine scan fill and fly-line linking (05 §5.1, §5.2; CADModule ``0x100a0e70``, ``0x10096fc0``).

Stages:

1. :func:`hatch_polygons` - scan lines at ``pitch`` across closed polygons (even-odd), dropping
   pieces shorter than ``GRP.minScanLineLength`` (0.01 mm, a constant of ``0x100a0e70``).  Rows
   start on the lower boundary and include the upper one (``segments.txt``: 8 rows at 1 mm over
   7 mm tall regions).  Row placement and the ``scanDirection`` codes other than 0 are UNVERIFIED
   (the ±0.707107 constants show a 45° option exists, 05 §5.1).
2. :func:`serpentine` - rows alternate direction (row 1 left->right, row 2 right->left …, 05 §5.1).
   :func:`format_segments_dump` writes ``segments.txt`` (``"(%g, %g)   (%g, %g)"`` CRLF, segments
   listed in serpentine order but with their original orientation, EVIDENCE: package file).
3. :func:`fly_line_path` - the link builder ``0x10096fc0`` (EVIDENCE, disassembly re-read for this
   module).  For consecutive segments with end ``S``/tangent ``T0`` and start ``E``/tangent ``T1``,
   ``D = |S - E|`` and ``D' = D`` if ``D >= 0.5`` else 2.0:

   * ``|T0 - T1| <= 0.005`` per component and ``|(E - S) x T1| < 0.035·D'`` -> straight link
     (``0x100971b3-0x10097272``);
   * ``T0·T1 >= 0`` or ``|T0y·T1x - T0x·T1y| >= 0.14`` -> cubic Bézier ``S, S + c·T0, E - c·T1, E``
     with ``c = min(0.5·D', 2)`` (4.0 when ``D' > 30``), knots ``[0,0,0,0,1,1,1,1]``
     (``0x100975f6-0x100976ba``);
   * otherwise (reversal) -> clamped cubic B-spline through six control points
     ``S, S + hs·T0, S + (hs+m)·T0, E - (m+he)·T1, E - he·T1, E`` with ``m = min(D', 2)``,
     ``a = min(0.4·m, 2)``, ``hs = 0.5·a``, ``he = a``, knots ``[0,0,0,0,0.05,0.95,1,1,1,1]``;
     when ``r = |(E-S)·T1| - |T1y·dx - T1x·dy| > 0`` the start side (``(E-S)·T0 > 0``) or the end
     side grows by ``r`` (``0x1009727e-0x10097487``; constants ``.rdata`` 0.5, 2.0, 0.005, 0.035,
     0.14, 0.4, 30, 4, 0.05, 0.95).

   For the 1 mm raster this gives a type-7 connector of exact length 2.66069 mm =
   ``setDataWithoutReFit_segs.txt``.  The glyph length the vendor caches (2.65861 in
   ``linkFlyLine_pathGlys.txt``) comes from the openNURBS analyser's sampled polyline
   (``0x1008d040``/``0x1008ef40`` via ``splineAnalyerVc100.dll``) and is not reproduced:
   UNVERIFIED sampling rule (``vendor_connector_length`` lets a caller inject it).

   Port rules (UNVERIFIED, marked): a reversal link gets ``GRP.scanSideLineLength`` run-out and
   run-in lines (60 mm) so the U-turn happens outside the part (05 §5.2 counts 24/16/14/7: no side
   line before the first or after the last row); the second link-builder variant selected by
   ``[ebp+0x10] == 4`` (``0x10097ac5``) is not implemented; a gap longer than
   ``GRP.LineFlyMaxLen`` inside a row still becomes a straight laser-off link but is reported in
   :attr:`ScanPath.long_links`.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "BEZIER_KNOTS",
    "MIN_SCAN_LINE_LENGTH",
    "SIDE_LINE_LENGTH",
    "UTURN_KNOTS",
    "LinkKind",
    "ScanGlyph",
    "ScanPath",
    "bspline_length",
    "bspline_points",
    "fly_line_path",
    "format_path_glys",
    "format_segments_dump",
    "hatch_polygons",
    "link_control_points",
    "orient_serpentine",
    "parse_path_glys",
    "parse_segments_dump",
    "scan_fill",
    "serpentine",
]

FloatArray = NDArray[np.float64]
Segment = tuple[float, float, float, float]

MIN_SCAN_LINE_LENGTH = 0.01
"""``GRP.minScanLineLength`` (05 §5.1, constant in ``0x100a0e70``)."""
SIDE_LINE_LENGTH = 60.0
"""``GRP.scanSideLineLength`` (05 §5.2, pd719_3)."""
LINE_FLY_MAX_LEN = 40.0
"""``GRP.LineFlyMaxLen`` (pd718)."""
TANGENT_EQUAL = 0.005
COLLINEAR_REL = 0.035
REVERSAL_CROSS = 0.14
SHORT_LINK = 0.5
SHORT_LINK_SUBST = 2.0
UTURN_SPAN = 0.4
BEZIER_HANDLE = 0.5
BEZIER_LONG = 30.0
BEZIER_LONG_HANDLE = 4.0
UTURN_KNOTS = (0.0, 0.0, 0.0, 0.0, 0.05, 0.95, 1.0, 1.0, 1.0, 1.0)
"""Knot vector pushed at ``0x10097490-0x100975eb``."""
BEZIER_KNOTS = (0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
DEGREE = 3
SPLINE_SAMPLES = 256
"""Polyline samples per connector for the planner geometry (port choice)."""


class LinkKind(IntEnum):
    """Glyph type codes written to ``linkFlyLine_pathGlys.txt`` (05 §5.2: 2 line, 7 spline)."""

    LINE = 2
    SPLINE = 7


@dataclass(slots=True)
class ScanGlyph:
    """One glyph of a fly-cut path; ``control``/``knots`` are set for splines."""

    kind: LinkKind
    points: FloatArray
    length: float
    laser_on: bool
    control: FloatArray | None = None
    knots: tuple[float, ...] = ()

    @property
    def start(self) -> FloatArray:
        """First point."""
        return self.points[0]

    @property
    def end(self) -> FloatArray:
        """Last point."""
        return self.points[-1]


@dataclass(slots=True)
class ScanPath:
    """A linked fly-cut path (the whole raster is one contour, 05 §12)."""

    glyphs: list[ScanGlyph] = field(default_factory=list)
    long_links: list[int] = field(default_factory=list)
    """Indices of straight links longer than ``LineFlyMaxLen`` (port report)."""

    @property
    def lengths(self) -> list[float]:
        """Glyph lengths in order."""
        return [g.length for g in self.glyphs]

    @property
    def laser_on(self) -> list[bool]:
        """Glyph laser states in order."""
        return [g.laser_on for g in self.glyphs]

    @property
    def total_length(self) -> float:
        """Sum of glyph lengths."""
        return math.fsum(self.lengths)

    def counts(self) -> dict[tuple[int, float], int]:
        """``(type, rounded length) -> count`` like the 24/16/14/7 census of 05 §5.2."""
        out: dict[tuple[int, float], int] = {}
        for g in self.glyphs:
            key = (int(g.kind), round(g.length, 5))
            out[key] = out.get(key, 0) + 1
        return out

    def to_fit_glyphs(self, feed: float | None = None) -> list[object]:
        """Glyphs for :func:`nexcut.plan.contour_fit.process` (lines stay lines, splines become
        curves with their exact length)."""
        from nexcut.plan.contour_fit import curve_glyph, line_glyph

        out: list[object] = []
        for g in self.glyphs:
            if g.kind is LinkKind.LINE:
                out.append(line_glyph(g.start, g.end, laser_on=g.laser_on, feed=feed))
            else:
                out.append(curve_glyph(g.points, length=g.length, laser_on=g.laser_on, feed=feed))
        return out


# ------------------------------------------------------------------------------ B-splines
def _eval(ctrl: FloatArray, knots: Sequence[float], u: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Points and first derivatives of a clamped cubic B-spline (vectorised de Boor)."""
    kn = np.asarray(knots, dtype=np.float64)
    c = np.asarray(ctrl, dtype=np.float64)
    n = len(c)
    uu = np.clip(np.asarray(u, dtype=np.float64), kn[DEGREE], kn[n])
    span = np.clip(np.searchsorted(kn, uu, side="right") - 1, DEGREE, n - 1)
    pts = [c[span - DEGREE + j] for j in range(DEGREE + 1)]
    for r in range(1, DEGREE + 1):
        for j in range(DEGREE, r - 1, -1):
            i = span - DEGREE + j
            lo, hi = kn[i], kn[i + DEGREE + 1 - r]
            den = hi - lo
            alpha = np.where(den > 0, (uu - lo) / np.where(den > 0, den, 1.0), 0.0)[:, None]
            pts[j] = (1.0 - alpha) * pts[j - 1] + alpha * pts[j]
    ders = []
    for j in range(DEGREE):
        i = span - DEGREE + j + 1
        den = kn[i + DEGREE] - kn[i]
        scale = np.where(den > 0, DEGREE / np.where(den > 0, den, 1.0), 0.0)[:, None]
        ders.append(scale * (c[i] - c[i - 1]))
    for r in range(1, DEGREE):
        for j in range(DEGREE - 1, r - 1, -1):
            i = span - DEGREE + j + 1
            lo, hi = kn[i], kn[i + DEGREE - r]
            den = hi - lo
            alpha = np.where(den > 0, (uu - lo) / np.where(den > 0, den, 1.0), 0.0)[:, None]
            ders[j] = (1.0 - alpha) * ders[j - 1] + alpha * ders[j]
    return pts[DEGREE], ders[DEGREE - 1]


def bspline_points(ctrl: FloatArray, knots: Sequence[float], n: int = SPLINE_SAMPLES) -> FloatArray:
    """``n + 1`` points at uniform parameter steps."""
    return _eval(ctrl, knots, np.linspace(0.0, 1.0, n + 1))[0]


_GL_X, _GL_W = np.polynomial.legendre.leggauss(16)


def bspline_length(ctrl: FloatArray, knots: Sequence[float], subdivisions: int = 16) -> float:
    """Arc length by 16-point Gauss-Legendre on ``subdivisions`` pieces of every knot span."""
    spans = sorted({float(k) for k in knots})
    us: list[FloatArray] = []
    ws: list[FloatArray] = []
    for a, b in zip(spans[:-1], spans[1:], strict=True):
        edges = np.linspace(a, b, subdivisions + 1)
        half = 0.5 * np.diff(edges)
        mid = 0.5 * (edges[1:] + edges[:-1])
        us.append((mid[:, None] + half[:, None] * _GL_X[None, :]).ravel())
        ws.append((half[:, None] * _GL_W[None, :]).ravel())
    u = np.concatenate(us)
    _, d = _eval(ctrl, knots, u)
    return float(np.sum(np.concatenate(ws) * np.hypot(d[:, 0], d[:, 1])))


# ------------------------------------------------------------------------------ link rules
def _unit(v: FloatArray) -> FloatArray:
    n = float(np.hypot(*v))
    return v / n if n > 0 else np.array([1.0, 0.0])


def link_control_points(
    s: Sequence[float], t0: Sequence[float], e: Sequence[float], t1: Sequence[float]
) -> tuple[LinkKind, FloatArray, tuple[float, ...]]:
    """Link geometry of ``0x10096fc0`` (module docstring): ``(kind, control points, knots)``.

    For a straight link the two end points are returned with empty knots.
    """
    S, E = np.asarray(s, float), np.asarray(e, float)
    T0, T1 = np.asarray(t0, float), np.asarray(t1, float)
    dist = float(np.hypot(*(S - E)))
    dd = dist if dist >= SHORT_LINK else SHORT_LINK_SUBST
    delta = E - S
    if abs(T0[0] - T1[0]) <= TANGENT_EQUAL and abs(T0[1] - T1[1]) <= TANGENT_EQUAL:
        cross = abs(delta[1] * T1[0] - delta[0] * T1[1])
        if not COLLINEAR_REL * dd <= cross:
            return LinkKind.LINE, np.vstack((S, E)), ()
    dot = float(T0 @ T1)
    cr = abs(T0[1] * T1[0] - T0[0] * T1[1])
    if dot >= 0.0 or cr >= REVERSAL_CROSS:
        c = min(BEZIER_HANDLE * dd, SHORT_LINK_SUBST)
        if dd > BEZIER_LONG:
            c = BEZIER_LONG_HANDLE
        ctrl = np.vstack((S, S + c * T0, E - c * T1, E))
        return LinkKind.SPLINE, ctrl, BEZIER_KNOTS
    m = min(dd, SHORT_LINK_SUBST)
    a = min(UTURN_SPAN * m, SHORT_LINK_SUBST)
    hs, he = BEZIER_HANDLE * a, a
    p1 = abs(float(delta @ T1))
    q = abs(T1[1] * delta[0] - T1[0] * delta[1])
    r = p1 - q
    if r > 0.0:
        if float(delta @ T0) > 0.0:
            hs += r
        else:
            he += r
    ctrl = np.vstack((S, S + hs * T0, S + (hs + m) * T0, E - (m + he) * T1, E - he * T1, E))
    return LinkKind.SPLINE, ctrl, UTURN_KNOTS


def _link_glyph(s: FloatArray, t0: FloatArray, e: FloatArray, t1: FloatArray) -> ScanGlyph:
    kind, ctrl, knots = link_control_points(s, t0, e, t1)
    if kind is LinkKind.LINE:
        return ScanGlyph(kind, ctrl, float(np.hypot(*(ctrl[1] - ctrl[0]))), False)
    return ScanGlyph(
        kind, bspline_points(ctrl, knots), bspline_length(ctrl, knots), False, ctrl, knots
    )


def _line(a: FloatArray, b: FloatArray, on: bool) -> ScanGlyph:
    return ScanGlyph(LinkKind.LINE, np.vstack((a, b)), float(np.hypot(*(b - a))), on)


def fly_line_path(
    segments: Iterable[Segment],
    *,
    side_line_length: float = SIDE_LINE_LENGTH,
    line_fly_max_len: float = LINE_FLY_MAX_LEN,
    vendor_connector_length: float | None = None,
) -> ScanPath:
    """Link oriented cut segments ``(x0, y0, x1, y1)`` into one fly-cut path (``0x10096fc0``).

    ``vendor_connector_length`` replaces the exact length of every reversal connector (to compare
    with the vendor's cached value; module docstring).
    """
    path = ScanPath()
    prev: ScanGlyph | None = None
    for seg in segments:
        a = np.array(seg[:2], dtype=np.float64)
        b = np.array(seg[2:], dtype=np.float64)
        cut = _line(a, b, True)
        if cut.length <= 0.0:
            continue
        if prev is not None:
            t0 = _unit(prev.end - prev.start)
            t1 = _unit(b - a)
            kind, _, knots = link_control_points(prev.end, t0, a, t1)
            if kind is LinkKind.SPLINE and knots == UTURN_KNOTS and side_line_length > 0.0:
                out_end = prev.end + side_line_length * t0
                in_start = a - side_line_length * t1
                path.glyphs.append(_line(prev.end, out_end, False))
                link = _link_glyph(out_end, t0, in_start, t1)
                if vendor_connector_length is not None:
                    link.length = float(vendor_connector_length)
                path.glyphs.append(link)
                path.glyphs.append(_line(in_start, a, False))
            else:
                link = _link_glyph(prev.end, t0, a, t1)
                if link.kind is LinkKind.LINE and link.length > line_fly_max_len:
                    path.long_links.append(len(path.glyphs))
                if link.length > 0.0:
                    path.glyphs.append(link)
        path.glyphs.append(cut)
        prev = cut
    return path


# ------------------------------------------------------------------------------ hatching
def _rot(points: FloatArray, angle: float) -> FloatArray:
    c, s = math.cos(angle), math.sin(angle)
    return points @ np.array([[c, -s], [s, c]]).T


def hatch_polygons(
    polygons: Iterable[Sequence[Sequence[float]]],
    pitch: float,
    *,
    angle_deg: float = 0.0,
    min_length: float = MIN_SCAN_LINE_LENGTH,
) -> list[list[Segment]]:
    """Scan rows (bottom to top in the scan frame) of left-to-right segments inside the polygons.

    Even-odd filling over all polygons together; the lowest row lies on the lowest vertex and the
    highest row on or below the highest one (UNVERIFIED placement, module docstring).
    """
    if pitch <= 0.0:
        raise ValueError("pitch must be > 0")
    ang = math.radians(angle_deg)
    rings = [_rot(np.asarray(p, dtype=np.float64).reshape(-1, 2), -ang) for p in polygons]
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        return []
    ymin = min(float(r[:, 1].min()) for r in rings)
    ymax = max(float(r[:, 1].max()) for r in rings)
    eps = 1e-9 * max(1.0, abs(ymax) + abs(ymin))
    n_rows = int(math.floor((ymax - ymin) / pitch + 1e-9)) + 1
    rows: list[list[Segment]] = []
    for k in range(n_rows):
        y = ymin + k * pitch
        yc = min(max(y, ymin + eps), ymax - eps)
        xs: list[float] = []
        for r in rings:
            p0 = r
            p1 = np.roll(r, -1, axis=0)
            y0, y1 = p0[:, 1], p1[:, 1]
            hit = ((y0 <= yc) & (y1 > yc)) | ((y1 <= yc) & (y0 > yc))
            if hit.any():
                t = (yc - y0[hit]) / (y1[hit] - y0[hit])
                xs.extend((p0[hit, 0] + t * (p1[hit, 0] - p0[hit, 0])).tolist())
        xs.sort()
        row: list[Segment] = []
        for x0, x1 in zip(xs[0::2], xs[1::2], strict=False):
            if x1 - x0 >= min_length:
                pts = _rot(np.array([[x0, y], [x1, y]]), ang)
                row.append((float(pts[0, 0]), float(pts[0, 1]), float(pts[1, 0]), float(pts[1, 1])))
        if row:
            rows.append(row)
    return rows


def serpentine(rows: Sequence[Sequence[Segment]]) -> list[tuple[Segment, bool]]:
    """Row order bottom->top, odd rows reversed: ``(segment, reversed)`` pairs (05 §5.1)."""
    out: list[tuple[Segment, bool]] = []
    for i, row in enumerate(rows):
        if i % 2 == 0:
            out.extend((s, False) for s in row)
        else:
            out.extend((s, True) for s in reversed(row))
    return out


def _oriented(seg: Segment, rev: bool) -> Segment:
    return (seg[2], seg[3], seg[0], seg[1]) if rev else seg


def orient_serpentine(segments: Sequence[Segment], tol: float = 1e-6) -> list[Segment]:
    """Recover travel direction from a ``segments.txt`` listing: consecutive segments on the same
    scan line form a row; a row whose segments are listed right-to-left is traversed reversed."""
    rows: list[list[Segment]] = []
    for s in segments:
        if rows and abs(rows[-1][0][1] - s[1]) <= tol and abs(rows[-1][0][3] - s[3]) <= tol:
            rows[-1].append(s)
        else:
            rows.append([s])
    out: list[Segment] = []
    for i, row in enumerate(rows):
        if len(row) > 1:
            rev = row[1][0] < row[0][0]
        else:
            rev = i % 2 == 1
        out.extend(_oriented(s, rev) for s in row)
    return out


def scan_fill(
    polygons: Iterable[Sequence[Sequence[float]]],
    pitch: float,
    *,
    angle_deg: float = 0.0,
    min_length: float = MIN_SCAN_LINE_LENGTH,
    side_line_length: float = SIDE_LINE_LENGTH,
    line_fly_max_len: float = LINE_FLY_MAX_LEN,
) -> tuple[list[Segment], ScanPath]:
    """Hatch + serpentine + fly-line linking; returns the ``segments.txt`` list and the path."""
    order = serpentine(hatch_polygons(polygons, pitch, angle_deg=angle_deg, min_length=min_length))
    listing = [s for s, _ in order]
    path = fly_line_path(
        (_oriented(s, r) for s, r in order),
        side_line_length=side_line_length,
        line_fly_max_len=line_fly_max_len,
    )
    return listing, path


# ------------------------------------------------------------------------------ dump files
_SEG_RX = re.compile(r"\(([-+\deE.]+), ([-+\deE.]+)\)\s+\(([-+\deE.]+), ([-+\deE.]+)\)")


def format_segments_dump(segments: Iterable[Segment]) -> bytes:
    """``segments.txt``: ``(x0, y0)   (x1, y1)`` with ``ostream`` default precision, CRLF."""
    return "".join(f"({s[0]:g}, {s[1]:g})   ({s[2]:g}, {s[3]:g})\r\n" for s in segments).encode(
        "ascii"
    )


def parse_segments_dump(data: bytes | str) -> list[Segment]:
    """Inverse of :func:`format_segments_dump`."""
    text = data.decode("ascii") if isinstance(data, bytes) else data
    return [tuple(map(float, m.groups())) for m in _SEG_RX.finditer(text)]  # type: ignore[misc]


def format_path_glys(path: ScanPath | Sequence[tuple[int, float]]) -> bytes:
    """``linkFlyLine_pathGlys.txt``: ``<type>, <length>`` per glyph, CRLF (05 §5.2)."""
    rows = (
        [(int(g.kind), g.length) for g in path.glyphs] if isinstance(path, ScanPath) else list(path)
    )
    return "".join(f"{k}, {v:g}\r\n" for k, v in rows).encode("ascii")


def parse_path_glys(data: bytes | str) -> list[tuple[int, float]]:
    """Inverse of :func:`format_path_glys`."""
    text = data.decode("ascii") if isinstance(data, bytes) else data
    out: list[tuple[int, float]] = []
    for line in text.splitlines():
        if line.strip():
            a, b = line.split(",")
            out.append((int(a), float(b)))
    return out
