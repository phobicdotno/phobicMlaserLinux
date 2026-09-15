"""ui/scene.py and ui/loader.py: display geometry, picking, file opening (Qt-free)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from nexcut.io import chf
from nexcut.model.flatten import flatten_glyph
from nexcut.model.glyph import (
    ArcGlyph,
    CircleGlyph,
    EllipseArcGlyph,
    LwPolylineGlyph,
    LwPolyVertex,
    PointGlyph,
    SegmentGlyph,
    Vec2,
)
from nexcut.model.graph import ChfDocument, ContourElement, Group, Scan
from nexcut.ops.import_gates import build_contour
from nexcut.ui import loader
from nexcut.ui.scene import build_scene, flatten_glyph_np

SAMPLES = [
    "File/autosave.chf",
    "File/Temp/tempGraph.chf",
    "Graph/Work1/1.chf",
    "Graph/Work1/2.chf",
    "Graph/Work1/3.chf",
    "Graph/Work2/1.chf",
    "Graph/Work2/2.chf",
    "Graph/Work2/3.chf",
]


@pytest.mark.parametrize(
    "glyph",
    [
        SegmentGlyph(Vec2(0, 0), Vec2(3, 4)),
        ArcGlyph(Vec2(1, 2), 5.0, 0.3, 2.5),
        ArcGlyph(Vec2(1, 2), 0.4, 2.5, -1.0),
        CircleGlyph(Vec2(-3, 7), 12.5),
        EllipseArcGlyph(Vec2(0, 0), Vec2(4, 1), 0.5, 0.0, 3.0),
        LwPolylineGlyph(
            1, [LwPolyVertex(Vec2(0, 0), 0.5), LwPolyVertex(Vec2(10, 0)), LwPolyVertex(Vec2(10, 5))]
        ),
        PointGlyph(Vec2(2, 2)),
    ],
)
def test_numpy_flatten_matches_model(glyph: object) -> None:
    ours = flatten_glyph_np(glyph)  # type: ignore[arg-type]
    ref = flatten_glyph(glyph)  # type: ignore[arg-type]
    assert len(ours) == len(ref)
    for a, b in zip(ours, ref, strict=True):
        assert a.shape == (len(b), 2)
        assert np.allclose(a, np.asarray(b), atol=1e-9)


def _doc(*graphs: object) -> ChfDocument:
    return ChfDocument(graphs=list(graphs))  # type: ignore[arg-type]


def test_start_and_arrow_follow_direction_flags() -> None:
    seg = SegmentGlyph(Vec2(0, 0), Vec2(10, 0))
    fwd = build_contour([seg])
    rev = build_contour([ContourElement(seg, -1)])
    scene = build_scene(_doc(fwd, rev))
    a, b = scene.contours
    assert a.start == (0.0, 0.0) and b.start == (10.0, 0.0)
    assert a.arrow == pytest.approx((5.0, 0.0, 1.0, 0.0))
    assert b.arrow == pytest.approx((5.0, 0.0, -1.0, 0.0))
    assert a.length == pytest.approx(10.0)
    assert scene.bbox == (0.0, 0.0, 10.0, 0.0)
    assert [c.graph_index for c in scene.contours] == [1, 2]


def test_group_scan_and_layers() -> None:
    c1 = build_contour([SegmentGlyph(Vec2(0, 0), Vec2(1, 1))], layer=2)
    c2 = build_contour([CircleGlyph(Vec2(5, 5), 1.0)], layer=3)
    p = build_contour([SegmentGlyph(Vec2(4, 5), Vec2(6, 5))])
    scene = build_scene(_doc(Group(children=[c1, c2]), Scan(children=[c2], paths=[p])))
    assert [c.kind for c in scene.contours] == ["child", "child", "child", "scanpath"]
    assert [c.graph_index for c in scene.contours] == [1, 1, 2, 2]
    assert scene.contours[3].is_scan_path
    assert scene.layers_used() == {2: 1, 3: 2}
    assert scene.glyph_count == 4


def test_pick_and_rect_selection() -> None:
    square = build_contour(
        [
            SegmentGlyph(Vec2(0, 0), Vec2(10, 0)),
            SegmentGlyph(Vec2(10, 0), Vec2(10, 10)),
            SegmentGlyph(Vec2(10, 10), Vec2(0, 10)),
            SegmentGlyph(Vec2(0, 10), Vec2(0, 0)),
        ]
    )
    circle = build_contour([CircleGlyph(Vec2(30, 5), 4.0)])
    dot = build_contour([PointGlyph(Vec2(50, 50))])
    scene = build_scene(_doc(square, circle, dot))
    assert scene.pick(5.0, 0.3, 0.5) == 0
    assert scene.pick(5.0, 5.0, 0.5) is None  # inside the square, far from its edges
    assert scene.pick(34.1, 5.0, 0.2) == 1
    assert scene.pick(50.1, 50.0, 0.2) == 2
    assert scene.pick(100.0, 100.0, 1.0) is None
    assert scene.in_rect(-1, -1, 11, 11) == [0]
    assert scene.in_rect(-1, -1, 40, 11) == [0, 1]
    assert scene.in_rect(5, 5, 28, 6, contained=False) == [0, 1]
    empty = build_scene(ChfDocument())
    assert empty.pick(0, 0, 1) is None and empty.in_rect(0, 0, 1, 1) == [] and empty.bbox is None


@pytest.mark.parametrize("rel", SAMPLES)
def test_sample_scene_bbox_matches_cached_geometry(src_dir: Path, rel: str) -> None:
    doc = chf.load_chf(src_dir / rel)
    scene = build_scene(doc)
    assert scene.contours
    for cv in scene.contours:
        c = cv.contour
        tol = 0.05 * max(1.0, c.length)  # 0.2 mm display chords vs the cached 0.002 mm geometry
        assert math.dist(cv.bbox[:2], c.bbox_min) <= tol
        assert math.dist(cv.bbox[2:], c.bbox_max) <= tol
        assert cv.start is not None and math.dist(cv.start, c.start) <= 1e-6 + 1e-3 * max(
            1.0, c.length
        )


def test_loader_suffixes_and_errors(tmp_path: Path) -> None:
    assert loader.file_kind("a.CHF") == "chf"
    assert loader.file_kind("b.dxf") == "dxf"
    assert loader.file_kind("c.plt") == "plt"
    for s in (".nc", ".txt", ".cnc", ".g"):
        assert loader.file_kind("x" + s) == "gcode"
    with pytest.raises(loader.LoadError):
        loader.file_kind("x.svg")
    with pytest.raises(loader.LoadError):
        loader.load_document(tmp_path / "missing.chf")
    bad = tmp_path / "bad.chf"
    bad.write_bytes(b"not a chf file")
    with pytest.raises(loader.LoadError):
        loader.load_document(bad)
    broken = tmp_path / "bad.dxf"
    broken.write_bytes(b"")
    with pytest.raises(loader.LoadError):
        loader.load_document(broken)


def test_loader_gcode_and_plt(tmp_path: Path) -> None:
    g = tmp_path / "job.nc"
    g.write_bytes(b"G90\nG0 X0 Y0\nG1 X10 Y0\nG1 X10 Y10\nG1 X0 Y10\nG1 X0 Y0\nM02\n")
    res = loader.load_document(g)
    assert res.kind == "gcode" and res.gate_report is not None
    scene = build_scene(res.document)
    assert scene.bbox == pytest.approx((0.0, 0.0, 10.0, 10.0))
    p = tmp_path / "job.plt"
    p.write_bytes(b"IN;SP1;PU0,0;PD400,0,400,400,0,400,0,0;PU;")
    res = loader.load_document(p, apply_gates=False)
    assert res.kind == "plt" and res.gate_report is None
    assert build_scene(res.document).bbox == pytest.approx((0.0, 0.0, 10.0, 10.0))


@pytest.mark.parametrize("rel", SAMPLES)
def test_loader_chf_resave_is_byte_identical(src_dir: Path, tmp_path: Path, rel: str) -> None:
    src = src_dir / rel
    res = loader.load_document(src)
    assert res.kind == "chf" and res.gate_report is None
    out = loader.save_document(res.document, tmp_path / "copy")
    assert out.suffix == ".chf"
    assert out.read_bytes() == src.read_bytes()


def test_loader_vendor_dxf_runs_gates(src_dir: Path) -> None:
    p = src_dir.parent / "Testfile SS1mm 2.0s F+1 N2.dxf"
    if not p.is_file():
        pytest.skip("vendor DXF not available")
    res = loader.load_document(p)
    assert res.kind == "dxf"
    assert res.gate_report is not None and res.gate_report.contours_merged == 6
    assert len(res.document.graphs) == 3
    raw = loader.load_document(p, apply_gates=False)
    assert len(raw.document.graphs) == 9
