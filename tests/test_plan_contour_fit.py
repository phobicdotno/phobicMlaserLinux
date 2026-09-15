"""CContoutSmooth port: refit / mergeLinearGly / smoothGly / setDataWithoutReFit (analysis 05 §6)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from nexcut.model.glyph import (
    ArcGlyph,
    CircleGlyph,
    LwPolylineGlyph,
    LwPolyVertex,
    SegmentGlyph,
    Vec2,
)
from nexcut.model.graph import Contour, ContourElement
from nexcut.plan.contour_fit import (
    BLEND_ANGLE,
    COLLINEAR_EPS,
    DUMP_FILES,
    REVERSAL_COS,
    GlyphKind,
    PieceList,
    arc_glyph,
    build_pieces,
    clamp_refit_tolerance,
    curve_glyph,
    format_length_dump,
    glyphs_from_contour,
    line_glyph,
    merge_linear,
    process,
    refit,
    smooth,
)
from test_plan_golden import CONNECTOR_LENGTH, read_segments, scan_path

# golden numbers copied from the vendor dumps (SRC root, 05 §6.3) so the logic is also checked
# without the package
NURBS_PIECES = [141.0] + [2.66069, 201.0] * 6 + [2.66069, 141.0]
PWM_SEGMENTS = (
    [25, 3, 25, 3, 25, 60, 2.66069]
    + [60, 25, 3, 25, 3, 25, 60, 2.66069] * 6
    + [60, 25, 3, 25, 3, 25]
)


def polyline(points: list[tuple[float, float]], **kw: object) -> list:
    return [line_glyph(points[i], points[i + 1], **kw) for i in range(len(points) - 1)]


# ------------------------------------------------------------------------------ constants
def test_constants_from_binary() -> None:
    assert COLLINEAR_EPS == 0.001
    assert BLEND_ANGLE == pytest.approx(0.523599, abs=1e-6)
    assert REVERSAL_COS == -0.984375
    assert DUMP_FILES == (
        "before_mergeLinearGly.txt",
        "after_mergeLinearGly.txt",
        "after_smoothGly.txt",
        "setDataWithoutReFit_segs.txt",
        "after_setDataWithoutReFit.txt",
    )


def test_refit_tolerance_clamp() -> None:
    assert clamp_refit_tolerance(0.001) == 0.01
    assert clamp_refit_tolerance(0.02) == 0.02
    assert clamp_refit_tolerance(0.3) == 0.3
    assert clamp_refit_tolerance(5.0) == 0.3


# ----------------------------------------------------------------------------- merge 0.001
def test_merge_collinear_within_tolerance() -> None:
    merged = merge_linear(polyline([(0, 0), (10, 0.0009 * 2), (20, 0)]))
    # vertex offset 0.0018 -> distance from the chord 0.0018 > 0.001: not merged
    assert len(merged) == 2
    merged = merge_linear(polyline([(0, 0), (10, 0.0009), (20, 0)]))
    assert len(merged) == 1
    assert merged[0].length == pytest.approx(20.0)
    assert len(merge_linear(polyline([(0, 0), (10, 0.0011), (20, 0)]))) == 2
    assert len(merge_linear(polyline([(0, 0), (10, 0), (20, 0), (35, 0)]))) == 1


def test_merge_keeps_reversal_and_state_changes() -> None:
    assert len(merge_linear(polyline([(0, 0), (10, 0), (5, 0)]))) == 2  # back-tracking
    a = line_glyph((0, 0), (25, 0), laser_on=True)
    b = line_glyph((25, 0), (28, 0), laser_on=False)
    assert len(merge_linear([a, b])) == 2
    c = line_glyph((25, 0), (28, 0), feed=10.0)
    assert len(merge_linear([a, c])) == 2
    stop = line_glyph((0, 0), (25, 0), cap=0.0)
    assert len(merge_linear([stop, line_glyph((25, 0), (30, 0))])) == 2


# ------------------------------------------------------------------------------- corners
def turn_path(angle_deg: float, leg: float = 10.0) -> list:
    phi = math.radians(angle_deg)
    p1 = (leg, 0.0)
    p2 = (leg + leg * math.cos(phi), leg * math.sin(phi))
    return polyline([(0.0, 0.0), p1, p2])


def test_sharp_corner_blended_by_smooth() -> None:
    out = smooth(turn_path(90.0))
    kinds = [g.kind for g in out]
    assert kinds == [GlyphKind.LINE, GlyphKind.CURVE, GlyphKind.LINE]
    r = 0.1 * math.cos(math.pi / 4) / (1 - math.cos(math.pi / 4))
    assert out[1].radius == pytest.approx(r)
    t = r * math.tan(math.pi / 4)
    assert out[0].length == pytest.approx(10 - t) and out[2].length == pytest.approx(10 - t)
    # G1: the arc is tangent to both legs and connects them
    assert np.allclose(out[0].end, out[1].start, atol=1e-9)
    assert np.allclose(out[1].end, out[2].start, atol=1e-9)
    assert np.allclose(out[1].start_tangent, out[0].end_tangent, atol=2e-3)
    assert np.allclose(out[1].end_tangent, out[2].start_tangent, atol=2e-3)
    # apex deviation = 0.1 mm
    apex = np.array([10.0, 0.0])
    assert np.min(np.hypot(*(out[1].points - apex).T)) == pytest.approx(0.1, abs=2e-3)


def test_radius_capped_at_two_mm() -> None:
    out = smooth(turn_path(31.0, leg=100.0))
    assert out[1].radius == pytest.approx(2.0)


def test_small_corner_not_touched_by_smooth_but_by_refit() -> None:
    path = turn_path(20.0)
    assert len(smooth(path)) == 2
    out = refit(path, 0.02)
    assert [g.kind for g in out] == [GlyphKind.LINE, GlyphKind.CURVE, GlyphKind.LINE]
    phi = math.radians(20)
    assert out[1].radius == pytest.approx(0.02 * math.cos(phi / 2) / (1 - math.cos(phi / 2)))


def test_reversal_is_a_forced_stop_not_blended() -> None:
    # 171 deg turn: cos = -0.9877 < -0.984375 -> reversal
    out = smooth(turn_path(171.0))
    assert len(out) == 2 and out[0].cap == 0.0
    # 169 deg: cos = -0.9816 >= -0.984375 -> blended
    out = smooth(turn_path(169.0))
    assert len(out) == 3 and all(g.cap == math.inf for g in out)


def test_blend_limited_to_half_of_short_legs() -> None:
    out = smooth(turn_path(90.0, leg=0.2))
    assert out[0].length == pytest.approx(0.1) and out[2].length == pytest.approx(0.1)
    assert out[1].radius == pytest.approx(0.1)


def test_corner_next_to_curve_gets_corner_radius() -> None:
    arc = arc_glyph((10.0, 5.0), 5.0, -math.pi / 2, 0.0)  # starts at (10,0) heading +x
    line_in = line_glyph((10.0, -10.0), (10.0, 0.0))  # heading +y -> 90 deg corner
    out = smooth([line_in, arc])
    assert len(out) == 2
    assert out[0].corner_radius == pytest.approx(
        0.1 * math.cos(math.pi / 4) / (1 - math.cos(math.pi / 4)), rel=1e-4
    )


def test_flattened_circle_recovers_radius_and_is_one_curve() -> None:
    radius = 10.0
    ang = np.linspace(0, 2 * np.pi, 721)
    pts = list(zip(radius * np.cos(ang), radius * np.sin(ang), strict=True))
    out = refit(polyline(pts), 0.02)
    curves = [g for g in out if g.kind is GlyphKind.CURVE]
    assert len(curves) == 1
    assert curves[0].radius == pytest.approx(radius, rel=1e-3)
    assert sum(g.length for g in out) == pytest.approx(2 * math.pi * radius, rel=1e-4)


def test_curve_glyph_numeric_radius() -> None:
    ang = np.linspace(0, math.pi, 200)
    g = curve_glyph(np.column_stack((3 * np.cos(ang), 3 * np.sin(ang))))
    assert g.radius == pytest.approx(3.0, rel=1e-3)
    assert curve_glyph([(0, 0), (1, 0), (2, 0)]).radius == math.inf


# ------------------------------------------------------------------------------ model input
def test_glyphs_from_contour_directions_and_bulges() -> None:
    square = LwPolylineGlyph(
        closed=1,
        vertices=[
            LwPolyVertex(Vec2(0, 0)),
            LwPolyVertex(Vec2(10, 0), 1.0),
            LwPolyVertex(Vec2(10, 10)),
            LwPolyVertex(Vec2(0, 10)),
        ],
    )
    c = Contour(elements=[ContourElement(square, 1)])
    gl = glyphs_from_contour(c)
    assert [g.kind for g in gl] == [GlyphKind.LINE, GlyphKind.CURVE, GlyphKind.LINE, GlyphKind.LINE]
    assert gl[1].radius == pytest.approx(5.0) and gl[1].length == pytest.approx(5 * math.pi)
    assert np.allclose(gl[1].start, (10, 0)) and np.allclose(gl[1].end, (10, 10), atol=1e-9)
    rev = glyphs_from_contour(
        Contour(elements=[ContourElement(SegmentGlyph(Vec2(0, 0), Vec2(5, 0)), -1)])
    )
    assert np.allclose(rev[0].start, (5, 0))
    arc = glyphs_from_contour(
        Contour(elements=[ContourElement(ArcGlyph(Vec2(0, 0), 2.0, 0.0, math.pi), 1)])
    )
    assert arc[0].length == pytest.approx(2 * math.pi)


# ------------------------------------------------------------------------------ pieces
def test_pieces_merge_collinear_across_laser_state() -> None:
    gl = [
        line_glyph((0, 0), (25, 0), laser_on=True),
        line_glyph((25, 0), (28, 0), laser_on=False),
        line_glyph((28, 0), (53, 0), laser_on=True),
        arc_glyph((53, 1), 1.0, -math.pi / 2, math.pi / 2, laser_on=False),
        line_glyph((53, 2), (0, 2), laser_on=False),
    ]
    pieces = build_pieces(gl, feed=100.0)
    assert np.allclose(pieces.lengths, [53.0, math.pi, 53.0])
    assert np.allclose(pieces.pwm_segments, [25, 3, 25, math.pi, 53])
    assert pieces.pwm_laser_on.tolist() == [True, False, True, False, False]
    assert np.allclose(pieces.radius, [math.inf, 1.0, math.inf])
    assert np.allclose(pieces.feed, 100.0) and np.allclose(pieces.factor, 1.0)
    assert pieces.total_length == pytest.approx(106 + math.pi)
    assert pieces.cum_length[-1] == pytest.approx(pieces.total_length)


def test_pieces_do_not_merge_across_forced_stop_or_feed_change() -> None:
    gl = [
        line_glyph((0, 0), (5, 0), cap=0.0),
        line_glyph((5, 0), (10, 0)),
        line_glyph((10, 0), (15, 0), feed=7.0),
    ]
    pieces = build_pieces(gl, feed=100.0)
    assert len(pieces) == 3
    assert pieces.cap.tolist() == [0.0, math.inf, math.inf]
    assert pieces.feed.tolist() == [100.0, 100.0, 7.0]


def test_piece_list_from_arrays() -> None:
    p = PieceList.from_arrays([1.0, 2.0, 3.0], radius=[math.inf, 0.5, math.inf], feed=50.0)
    assert p.cum_length.tolist() == [1.0, 3.0, 6.0]
    assert p.kind.tolist() == [int(GlyphKind.LINE), int(GlyphKind.CURVE), int(GlyphKind.LINE)]


def test_dump_format() -> None:
    assert format_length_dump([284.266], "total: ") == b"284.266\r\ntotal: 284.266\r\n"
    assert format_length_dump(NURBS_PIECES, "totalNurbsLength: ").endswith(
        b"totalNurbsLength: 1506.62\r\n"
    )
    assert format_length_dump([141.00000000000003], "x") == b"141\r\nx141\r\n"


def test_golden_lengths_without_package() -> None:
    """Sum rules of the decoded dumps (05 §6.3): 141/201 = merged straight PWM segments."""
    assert sum(PWM_SEGMENTS) == pytest.approx(sum(NURBS_PIECES))
    assert len(PWM_SEGMENTS) == 61 and len(NURBS_PIECES) == 15
    assert sum([25, 3, 25, 3, 25, 60]) == 141 and sum([60, 25, 3, 25, 3, 25, 60]) == 201
    assert f"{sum(PWM_SEGMENTS):g}" == "1506.62"


# ------------------------------------------------------------------------- vendor golden
def test_segments_txt_raster(src_dir: Path) -> None:
    segs = read_segments(src_dir)
    assert len(segs) == 24
    assert all(x1 - x0 == pytest.approx(25.0) and y0 == y1 for x0, y0, x1, y1 in segs)
    ys = sorted({s[1] for s in segs})
    assert ys[0] == pytest.approx(363.416) and ys[-1] == pytest.approx(370.416)
    assert len(ys) == 8 and np.allclose(np.diff(ys), 1.0)
    assert sorted({s[0] for s in segs}) == pytest.approx([252.152, 280.152, 308.152])
    # serpentine: row order alternates left->right / right->left
    for r in range(8):
        xs = [segs[3 * r + k][0] for k in range(3)]
        assert xs == (sorted(xs) if r % 2 == 0 else sorted(xs, reverse=True))


def test_path_glys_matches_scan_path(src_dir: Path) -> None:
    lines = (src_dir / "linkFlyLine_pathGlys.txt").read_text(encoding="ascii").split()
    entries = [(int(a.rstrip(",")), float(b)) for a, b in zip(lines[::2], lines[1::2], strict=True)]
    path = scan_path(src_dir)
    assert len(entries) == len(path) == 61
    # 05 §5.2 decoded counts: 24 cuts, 16 gaps, 14 side lines, 7 spline U-turns; 1506.6 mm
    assert entries.count((2, 25.0)) == 24 and entries.count((2, 3.0)) == 16
    assert entries.count((2, 60.0)) == 14 and entries.count((7, 2.65861)) == 7
    assert sum(length for _, length in entries) == pytest.approx(1506.61, abs=0.01)
    for (typ, length), g in zip(entries, path, strict=True):
        assert typ == int(g.kind)
        if typ == 2:
            assert g.length == pytest.approx(length, abs=5e-6)
        else:
            assert length == 2.65861 and g.length == CONNECTOR_LENGTH  # re-fitted length


def test_golden_set_data_dumps_byte_identical(src_dir: Path) -> None:
    """``setDataWithoutReFit_segs.txt`` and ``after_setDataWithoutReFit.txt`` reproduced byte for byte."""
    result = process(scan_path(src_dir), tol=0.02, feed=100.0)
    for name in ("setDataWithoutReFit_segs.txt", "after_setDataWithoutReFit.txt"):
        assert result.dumps[name] == (src_dir / name).read_bytes(), name
    assert result.pieces is not None
    assert result.pieces.lengths == pytest.approx(NURBS_PIECES, abs=5e-6)
    assert result.pieces.pwm_segments == pytest.approx(PWM_SEGMENTS, abs=5e-6)
    assert result.pieces.total_length == pytest.approx(1506.62483, abs=5e-6)
    # straight pieces stay straight and exact; the connectors are the only curves
    assert np.isinf(result.pieces.radius[::2]).all() and (result.pieces.radius[1::2] == 0.5).all()


def test_golden_contour_dumps_byte_identical(src_dir: Path) -> None:
    """The 284.266 mm closed contour: one glyph through refit/merge/smooth, set-data skipped (05 §6.3)."""
    circle = Contour(
        elements=[ContourElement(CircleGlyph(Vec2(0.0, 0.0), 284.266 / (2 * math.pi)), 1)]
    )
    result = process(glyphs_from_contour(circle), tol=0.02, feed=100.0, skip_set_data=True)
    assert set(result.dumps) == set(DUMP_FILES[:3])
    assert result.pieces is None
    for name in DUMP_FILES[:3]:
        assert result.dumps[name] == (src_dir / name).read_bytes(), name
