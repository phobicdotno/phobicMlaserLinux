"""Scan fill and fly-line linking against the vendor dumps (05 §5.1, §5.2; CADModule 0x10096fc0)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from nexcut.ops.scan import (
    BEZIER_KNOTS,
    UTURN_KNOTS,
    LinkKind,
    ScanPath,
    bspline_length,
    bspline_points,
    fly_line_path,
    format_path_glys,
    format_segments_dump,
    hatch_polygons,
    link_control_points,
    orient_serpentine,
    parse_path_glys,
    parse_segments_dump,
    scan_fill,
    serpentine,
)
from nexcut.plan.contour_fit import process
from nexcut.plan.pwm_schedule import close_pwm_ratios, format_ratio_dump, parse_ratio_dump

# closePwmPosRatios.txt (46 values, copied from the package root, 05 §5.2)
RATIOS = [
    0.0165935, 0.0185848, 0.0351783, 0.0371695, 0.0537631, 0.135177, 0.15177, 0.153761,
    0.170355, 0.172346, 0.18894, 0.270353, 0.286947, 0.288938, 0.305532, 0.307523, 0.324116,
    0.40553, 0.422124, 0.424115, 0.440708, 0.4427, 0.459293, 0.540707, 0.5573, 0.559292,
    0.575885, 0.577876, 0.59447, 0.675884, 0.692477, 0.694468, 0.711062, 0.713053, 0.729647,
    0.81106, 0.827654, 0.829645, 0.846239, 0.84823, 0.864823, 0.946237, 0.96283, 0.964822,
    0.981415, 0.983406,
]  # fmt: skip
VENDOR_CONNECTOR = 2.65861
"""Cached type-7 glyph length in linkFlyLine_pathGlys.txt (analyser polyline, not reproduced)."""
RECTS = [
    [(x, 363.416), (x + 25.0, 363.416), (x + 25.0, 370.416), (x, 370.416)]
    for x in (252.152, 280.152, 308.152)
]
"""Three 25 x 7 mm rectangles whose 1 mm raster is ``segments.txt`` (05 §5.1 INFERENCE high)."""


def raster() -> tuple[list[tuple[float, float, float, float]], ScanPath]:
    return scan_fill(RECTS, 1.0)


def test_hatch_rows_and_serpentine_listing() -> None:
    rows = hatch_polygons(RECTS, 1.0)
    assert len(rows) == 8 and all(len(r) == 3 for r in rows)
    assert [r[0][1] for r in rows] == pytest.approx([363.416 + k for k in range(8)])
    order = serpentine(rows)
    assert [rev for _, rev in order[:6]] == [False] * 3 + [True] * 3
    listing, path = raster()
    assert listing[3] == pytest.approx((308.152, 364.416, 333.152, 364.416))  # original orientation
    text = format_segments_dump(listing)
    assert text.startswith(b"(252.152, 363.416)   (277.152, 363.416)\r\n")
    assert parse_segments_dump(text) == pytest.approx(listing)
    assert orient_serpentine(listing)[3] == pytest.approx((333.152, 364.416, 308.152, 364.416))


def test_hatch_drops_short_lines_and_rotates() -> None:
    tri = [[(0.0, 0.0), (10.0, 0.0), (0.0, 10.0)]]
    rows = hatch_polygons(tri, 1.0)
    assert all(s[2] - s[0] >= 0.01 for row in rows for s in row)
    assert len(rows) == 10  # the apex row (width 0) is dropped
    diag = hatch_polygons([[(0, 0), (10, 0), (10, 10), (0, 10)]], 2.0, angle_deg=45.0)
    seg = diag[len(diag) // 2][0]
    assert math.degrees(math.atan2(seg[3] - seg[1], seg[2] - seg[0])) == pytest.approx(45.0)
    with pytest.raises(ValueError):
        hatch_polygons(tri, 0.0)
    assert hatch_polygons([[(0, 0), (1, 1)]], 1.0) == []


def test_link_rules_from_disassembly() -> None:
    # collinear, same direction -> straight gap line
    kind, ctrl, knots = link_control_points((0, 0), (1, 0), (3, 0), (1, 0))
    assert kind is LinkKind.LINE and knots == () and ctrl.tolist() == [[0, 0], [3, 0]]
    # reversal 1 mm apart -> 6-point cubic B-spline, hs = 0.2, m = 1, he = 0.4
    kind, ctrl, knots = link_control_points((0, 0), (1, 0), (0, 1), (-1, 0))
    assert kind is LinkKind.SPLINE and knots == UTURN_KNOTS
    expected = [[0, 0], [0.2, 0], [1.2, 0], [1.4, 1], [0.4, 1], [0, 1]]
    assert np.allclose(ctrl, expected)
    assert f"{bspline_length(ctrl, knots):g}" == "2.66069"  # setDataWithoutReFit_segs.txt
    # short reversal (D < 0.5 -> D' = 2): m = 2, a = 0.8
    _, ctrl, _ = link_control_points((0, 0), (1, 0), (0, 0.2), (-1, 0))
    assert ctrl[2].tolist() == pytest.approx([0.4 + 2.0, 0.0])
    # D = sqrt(5): m = 2, a = 0.8; r = |dx| - |dy| = 1 > 0 and (E - S).T0 > 0 -> hs = 0.4 + 1
    _, ctrl, _ = link_control_points((0, 0), (1, 0), (2, 1), (-1, 0))
    assert np.allclose(ctrl, [[0, 0], [1.4, 0], [3.4, 0], [4.8, 1], [2.8, 1], [2, 1]])
    # (E - S).T0 <= 0 -> the end side grows instead
    _, ctrl, _ = link_control_points((0, 0), (1, 0), (-2, 1), (-1, 0))
    assert np.allclose(ctrl[1], [0.4, 0]) and np.allclose(ctrl[4], [-2 + 1.8, 1])
    # perpendicular -> Bezier with c = min(0.5 D', 2)
    kind, ctrl, knots = link_control_points((0, 0), (1, 0), (5, 5), (0, 1))
    assert kind is LinkKind.SPLINE and knots == BEZIER_KNOTS
    assert np.allclose(ctrl, [[0, 0], [2, 0], [5, 3], [5, 5]])
    _, ctrl, _ = link_control_points((0, 0), (1, 0), (40, 40), (0, 1))
    assert ctrl[1].tolist() == pytest.approx([4.0, 0.0])  # D' > 30 -> 4.0


def test_bspline_evaluation_endpoints() -> None:
    _, ctrl, knots = link_control_points((0, 0), (1, 0), (0, 1), (-1, 0))
    pts = bspline_points(ctrl, knots, 32)
    assert pts[0].tolist() == pytest.approx([0, 0]) and pts[-1].tolist() == pytest.approx([0, 1])
    assert float(np.max(pts[:, 0])) < 1.4
    chord = float(np.hypot(*np.diff(bspline_points(ctrl, knots, 4000), axis=0).T).sum())
    assert chord == pytest.approx(bspline_length(ctrl, knots), abs=1e-6)


def test_raster_path_counts_and_total() -> None:
    _, path = raster()
    counts = path.counts()
    assert counts[(2, 25.0)] == 24 and counts[(2, 3.0)] == 16 and counts[(2, 60.0)] == 14
    assert counts[(7, 2.66069)] == 7
    assert f"{path.total_length:g}" == "1506.62"
    kinds = [int(g.kind) for g in path.glyphs[:9]]
    assert kinds == [2, 2, 2, 2, 2, 2, 7, 2, 2]


def test_ratios_numbers_without_package() -> None:
    _, path = raster()
    vendorlike = fly_line_path(
        orient_serpentine(raster()[0]), vendor_connector_length=VENDOR_CONNECTOR
    )
    ratios = close_pwm_ratios(vendorlike.lengths, vendorlike.laser_on)
    assert len(ratios) == 46
    assert [float(f"{r:g}") for r in ratios] == RATIOS
    total = vendorlike.total_length
    assert [round(r * total, 2) for r in ratios[:6]] == [25.0, 28.0, 53.0, 56.0, 81.0, 203.66]
    exact = close_pwm_ratios(path.lengths, path.laser_on)
    assert exact == pytest.approx(RATIOS, abs=2e-6)
    assert parse_ratio_dump(format_ratio_dump(ratios)) == RATIOS


def test_path_glys_format_roundtrip() -> None:
    _, path = raster()
    text = format_path_glys(path)
    assert text.startswith(b"2, 25\r\n2, 3\r\n2, 25\r\n")
    assert parse_path_glys(text)[6] == (7, 2.66069)


def test_long_links_reported() -> None:
    path = fly_line_path([(0, 0, 10, 0), (60, 0, 70, 0)], line_fly_max_len=40.0)
    assert path.long_links == [1] and path.glyphs[1].length == pytest.approx(50.0)


# --------------------------------------------------------------------------- package goldens
def test_segments_txt_byte_identical(src_dir: Path) -> None:
    listing, _ = raster()
    assert format_segments_dump(listing) == (src_dir / "segments.txt").read_bytes()


def test_link_fly_line_dumps_byte_identical(src_dir: Path) -> None:
    segs = parse_segments_dump((src_dir / "segments.txt").read_bytes())
    path = fly_line_path(orient_serpentine(segs), vendor_connector_length=VENDOR_CONNECTOR)
    assert format_path_glys(path) == (src_dir / "linkFlyLine_pathGlys.txt").read_bytes()
    ratios = close_pwm_ratios(path.lengths, path.laser_on)
    assert format_ratio_dump(ratios) == (src_dir / "closePwmPosRatios.txt").read_bytes()


def test_refit_dumps_from_real_connector_geometry(src_dir: Path) -> None:
    segs = parse_segments_dump((src_dir / "segments.txt").read_bytes())
    path = fly_line_path(orient_serpentine(segs))  # exact spline, no injected length
    result = process(path.to_fit_glyphs(), 0.02, 600.0)
    for name in ("setDataWithoutReFit_segs.txt", "after_setDataWithoutReFit.txt"):
        assert result.dumps[name] == (src_dir / name).read_bytes(), name
