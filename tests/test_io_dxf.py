"""Tests of nexcut.io.dxf (09 §3.2, §7) on the vendor test drawing and synthetic DXF.

Golden facts of ``Testfile SS1mm 2.0s F+1 N2.dxf`` (parent directory of SRC,
99-gaps §1.2): AC1015, ``$DWGCODEPAGE ANSI_936``, modelspace entities CIRCLE 1,
ARC 4, SPLINE 3, LWPOLYLINE 1.  99-gaps also says "on 15 layers"; that is not
reproducible - the LAYER table has 4 records (``0``, ``\\U+0430\\U+0440 1``,
``图层1``, ``Defpoints``) and the group-code-8 values in the whole file are
``图层1``/``0``/``Defpoints``/``hp`` (every modelspace entity is on ``图层1``).
The 15 names listed there (``9, 43, 66, ...``) look like handle values read
with a mis-aligned group/value pairing.  The bbox golden values are the file's
own ``$EXTMIN``/``$EXTMAX``.
"""

from __future__ import annotations

import io
import math
from pathlib import Path

import ezdxf
import numpy as np
import pytest
from ezdxf import path as ezpath
from ezdxf.math import BSpline

from nexcut.io import chf
from nexcut.io import dxf as dxfmod
from nexcut.io.dxf import DxfImportError, DxfImportOptions, read_dxf, write_dxf
from nexcut.model import (
    ArcGlyph,
    CircleGlyph,
    Contour,
    EllipseArcGlyph,
    Group,
    LwPolylineGlyph,
    PointGlyph,
    SegmentGlyph,
    SplineGlyph,
    iter_contours,
)
from nexcut.model.flatten import check_contour, flatten_glyph
from nexcut.ops.import_gates import GateParams, apply_import_gates, is_closed

VENDOR_DXF_NAME = "Testfile SS1mm 2.0s F+1 N2.dxf"
EXTMIN = (210.0401298715092, -22.74565816706447)
EXTMAX = (241.3728577912229, 67.21628119867513)


@pytest.fixture(scope="module")
def vendor_dxf(src_dir: Path) -> Path:
    p = src_dir.parent / VENDOR_DXF_NAME
    if not p.is_file():
        pytest.skip(f"vendor test DXF not available at {p}")
    return p


def _all_contours(doc: chf.ChfDocument) -> list[Contour]:
    return [c for g in doc.graphs for c, _ in iter_contours(g)]


def _pts(c: Contour, step: float = 0.01) -> np.ndarray:
    """Dense points of a contour; splines are sampled finely with ezdxf's B-spline evaluator.

    (``flatten_glyph`` samples a spline at a fixed 8 points per control point, too coarse
    for mm-level comparisons; its evaluation itself is checked against ezdxf separately.)
    """
    out: list[tuple[float, float]] = []
    for el in c.elements:
        g = el.glyph
        if isinstance(g, SplineGlyph):
            bs = BSpline([(p[0], p[1]) for p in g.control_points], order=4, knots=g.knots)
            lo, hi = g.knots[3], g.knots[len(g.control_points)]
            pl = [(v.x, v.y) for v in bs.points(np.linspace(lo, hi, 600))]
        else:
            pl = [p for part in flatten_glyph(g, step) for p in part]
        out.extend(pl[::-1] if el.direction == -1 else pl)
    return np.asarray(out)


def _to_polyline(points: np.ndarray, poly: np.ndarray) -> float:
    """Largest distance from ``points`` to the polyline through ``poly``."""
    if len(poly) == 1:
        return float(np.linalg.norm(points - poly[0], axis=1).max())
    a = poly[:-1]
    ab = poly[1:] - a
    ab2 = np.maximum((ab * ab).sum(axis=1), 1e-30)
    ap = points[:, None, :] - a[None, :, :]
    t = np.clip((ap * ab[None]).sum(axis=2) / ab2[None], 0.0, 1.0)
    d = ap - t[:, :, None] * ab[None]
    return float(np.sqrt((d * d).sum(axis=2)).min(axis=1).max())


def _hausdorff(a: np.ndarray, b: np.ndarray) -> float:
    """Symmetric curve distance of two flattened curves (point-to-polyline both ways)."""
    return max(_to_polyline(a, b), _to_polyline(b, a))


def _dxf_bytes(doc: ezdxf.document.Drawing, encoding: str = "cp1252") -> bytes:
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode(encoding)


# ---------------------------------------------------------------------------
# Vendor golden file
# ---------------------------------------------------------------------------


def test_vendor_header_counts_layers(vendor_dxf: Path) -> None:
    r = read_dxf(vendor_dxf)
    assert r.acadver == "AC1015"
    assert r.codepage == "ANSI_936"
    assert r.encoding == "cp936"
    assert r.insunits == 1  # declared inches, geometry is mm (module docstring)
    assert dict(r.entity_counts) == {"CIRCLE": 1, "ARC": 4, "SPLINE": 3, "LWPOLYLINE": 1}
    assert dict(r.imported_counts) == {"CIRCLE": 1, "ARC": 4, "SPLINE": 3, "LWPOLYLINE": 1}
    assert not r.ignored_counts
    assert r.warnings == []
    assert r.layers == ["0", "ар 1", "图层1", "Defpoints"]
    assert dict(r.entity_layers) == {"图层1": 9}
    assert len(r.document.graphs) == 9
    kinds = sorted(type(c.elements[0].glyph).__name__ for c in _all_contours(r.document))
    assert kinds == ["ArcGlyph"] * 4 + ["CircleGlyph", "LwPolylineGlyph"] + ["SplineGlyph"] * 3


def test_vendor_raw_layer_group_codes(vendor_dxf: Path) -> None:
    """The layer facts behind the 99-gaps discrepancy note (module docstring)."""
    lines = vendor_dxf.read_bytes().decode("gbk").split("\r\n")
    names = {lines[i + 1] for i in range(0, len(lines) - 1, 2) if lines[i].strip() == "8"}
    assert names == {"图层1", "0", "Defpoints", "hp"}
    assert len(lines) - 1 == 24364  # 99-gaps §1.2 "24 364 lines"


def test_vendor_bbox_and_cached_geometry(vendor_dxf: Path) -> None:
    r = read_dxf(vendor_dxf)
    bbox = r.bbox
    assert bbox is not None
    # $EXTMIN/$EXTMAX are the CAD program's own extents.  Since model/flatten began
    # to honour the chord step (STATUS X10) the port samples the first spline finely
    # enough to reach its true leftmost point at x = 210.0390009 (200 001-sample ezdxf
    # evaluation), 1.1e-3 mm left of the file's own $EXTMIN.  The other three
    # components still match the header exactly.
    assert bbox[0].x == pytest.approx(210.0390009, abs=1e-5)
    assert bbox[0].y == pytest.approx(EXTMIN[1], abs=1e-6)
    assert bbox[1] == pytest.approx(EXTMAX, abs=1e-6)
    for c in _all_contours(r.document):
        assert check_contour(c) == []
        assert c.precision == 0.01
        assert c.layer == 0
    circle = r.document.graphs[0].elements[0].glyph
    assert isinstance(circle, CircleGlyph)
    assert circle.center == pytest.approx((225.7059417097319, -15.74365448159151))
    assert circle.radius == pytest.approx(3.056268104130133)


def test_vendor_splines_match_ezdxf_evaluation(vendor_dxf: Path) -> None:
    """The model's de Boor evaluation (03 §7 type 7) agrees with ezdxf's NURBS evaluator."""
    drawing = ezdxf.readfile(vendor_dxf)
    splines = list(drawing.modelspace().query("SPLINE"))
    r = read_dxf(vendor_dxf)
    ours = [
        c.elements[0].glyph
        for c in _all_contours(r.document)
        if isinstance(c.elements[0].glyph, SplineGlyph)
    ]
    assert [len(g.control_points) for g in ours] == [16, 11, 11]
    for sp, g in zip(splines, ours, strict=True):
        assert len(g.knots) == len(g.control_points) + 4
        assert (g.int1, g.int2) == (0, 0)
        bs = sp.construction_tool()
        pl = np.asarray(flatten_glyph(g)[0])
        ref = np.asarray([(p.x, p.y) for p in bs.points(np.linspace(0.0, bs.max_t, len(pl)))])
        assert np.abs(pl[:, :2] - ref).max() < 1e-9


def test_vendor_gates_give_three_closed_contours(vendor_dxf: Path) -> None:
    r = read_dxf(vendor_dxf)
    report = apply_import_gates(r.document, GateParams())
    assert report.overlaps_removed == 0
    assert report.contours_merged == 6
    assert report.micro_removed == 0
    graphs = r.document.graphs
    assert len(graphs) == 3
    assert all(isinstance(g, Contour) and is_closed(g) for g in graphs)
    lengths = [g.length for g in graphs]  # type: ignore[union-attr]
    # nearest sort with inner-first: the two holes before the outline
    # converged values: identical at flatten step 0.002, 0.0005 and 0.0001.  They were
    # 220.3435 / 69.2444 while spline flattening ignored the step (STATUS X10).
    assert lengths[-1] == pytest.approx(220.3503, abs=2e-3)
    assert sorted(lengths[:2]) == pytest.approx([19.2031, 69.2492], abs=2e-3)
    assert [type(e.glyph).__name__ for e in graphs[-1].elements] == [
        "SplineGlyph",
        "LwPolylineGlyph",
    ]  # type: ignore[union-attr]
    for g in graphs:
        assert check_contour(g) == []  # type: ignore[arg-type]


def _assert_same_geometry(a: chf.ChfDocument, b: chf.ChfDocument, tol: float) -> None:
    ca, cb = _all_contours(a), _all_contours(b)
    assert len(ca) == len(cb)
    for x, y in zip(ca, cb, strict=True):
        assert [type(e.glyph) for e in x.elements] == [type(e.glyph) for e in y.elements]
        assert [e.direction for e in x.elements] == [e.direction for e in y.elements]
        assert x.length == pytest.approx(y.length, abs=tol * 10)
        assert _hausdorff(_pts(x, 0.05), _pts(y, 0.05)) < tol


def test_vendor_roundtrip_dxf_model_chf_model(vendor_dxf: Path) -> None:
    r = read_dxf(vendor_dxf)
    data = chf.write_chf(r.document)
    back = chf.read_chf(data)
    assert chf.check_document(back) == []
    _assert_same_geometry(r.document, back, 1e-5)
    # a second cycle is byte-stable
    assert chf.write_chf(back) == data


def test_vendor_roundtrip_after_gates(vendor_dxf: Path) -> None:
    r = read_dxf(vendor_dxf)
    apply_import_gates(r.document)
    back = chf.read_chf(chf.write_chf(r.document))
    assert chf.check_document(back) == []
    _assert_same_geometry(r.document, back, 1e-5)


def test_vendor_dxf_export_reimport(vendor_dxf: Path, tmp_path: Path) -> None:
    r = read_dxf(vendor_dxf)
    out = tmp_path / "export.dxf"
    write_dxf(r.document, out)
    raw = out.read_bytes()
    assert b"ANSI_936" in raw and b"AC1015" in raw
    again = read_dxf(out)
    assert dict(again.imported_counts) == dict(r.imported_counts)
    _assert_same_geometry(r.document, again.document, 1e-6)


# ---------------------------------------------------------------------------
# Synthetic entities
# ---------------------------------------------------------------------------


def _import_one(add: object, **opts: object) -> tuple[Contour, ezdxf.entities.DXFGraphic]:
    doc = ezdxf.new("R2000")
    ent = add(doc.modelspace())  # type: ignore[operator]
    r = dxfmod.import_dxf(doc, DxfImportOptions(**opts))  # type: ignore[arg-type]
    assert len(r.document.graphs) == 1, r.warnings
    g = r.document.graphs[0]
    assert isinstance(g, Contour)
    assert check_contour(g) == []
    return g, ent


def _ezdxf_points(ent: ezdxf.entities.DXFGraphic) -> np.ndarray:
    if ent.dxftype() == "SPLINE":  # exact NURBS evaluation (make_path approximates rational curves)
        bs = ent.construction_tool()
        return np.asarray([(v.x, v.y) for v in bs.points(np.linspace(0.0, bs.max_t, 3000))])
    p = ezpath.make_path(ent)
    return np.asarray([(v.x, v.y) for v in p.flattening(0.0001)])


ENTITY_CASES = {
    "line": lambda m: m.add_line((1, 2), (5, -3)),
    "arc": lambda m: m.add_arc((5, 5), 2, 30, 300),
    "arc_wrap": lambda m: m.add_arc((5, 5), 2, 300, 30),
    "arc_neg_extrusion": lambda m: m.add_arc(
        (5, 5), 2, 30, 120, dxfattribs={"extrusion": (0, 0, -1)}
    ),
    "circle": lambda m: m.add_circle((1, 1), 3),
    "circle_neg_extrusion": lambda m: m.add_circle((4, 1), 3, dxfattribs={"extrusion": (0, 0, -1)}),
    "ellipse": lambda m: m.add_ellipse(
        (20, 20), major_axis=(4, 1, 0), ratio=0.5, start_param=0.3, end_param=2.0
    ),
    "ellipse_neg_extrusion": lambda m: m.add_ellipse(
        (20, 20),
        major_axis=(4, 1, 0),
        ratio=0.5,
        start_param=0.3,
        end_param=2.0,
        dxfattribs={"extrusion": (0, 0, -1)},
    ),
    "lwpolyline_bulge": lambda m: m.add_lwpolyline(
        [(0, 0, 0, 0, 0.5), (5, 0, 0, 0, -0.3), (5, 5, 0, 0, 0)], format="xyseb", close=True
    ),
    "lwpolyline_neg_extrusion": lambda m: m.add_lwpolyline(
        [(0, 0, 0, 0, 0.5), (5, 0, 0, 0, -0.3), (5, 5, 0, 0, 0)],
        format="xyseb",
        dxfattribs={"extrusion": (0, 0, -1)},
    ),
    "polyline2d": lambda m: m.add_polyline2d(
        [(0, 0, 0, 0, 1.0), (1, 1, 0, 0, 0), (2, 0)], format="xyseb"
    ),
    "polyline3d": lambda m: m.add_polyline3d([(0, 0, 1), (1, 1, 2), (2, 0, 3)]),
    "spline": lambda m: m.add_open_spline([(0, 0), (1, 2), (3, 3), (4, 0), (6, 1)]),
    "spline_fit": lambda m: m.add_spline(fit_points=[(0, 0), (1, 2), (3, 3), (4, 0)]),
    "spline_degree2": lambda m: m.add_open_spline([(0, 0), (1, 2), (3, 3), (4, 0)], degree=2),
}


@pytest.mark.parametrize("case", sorted(ENTITY_CASES))
def test_entity_geometry_matches_ezdxf(case: str) -> None:
    contour, ent = _import_one(ENTITY_CASES[case])
    ours = _pts(contour, 0.005)
    ref = _ezdxf_points(ent)
    assert _hausdorff(ours, ref) < 2e-3


def test_entity_glyph_types() -> None:
    expect = {
        "line": SegmentGlyph,
        "arc": ArcGlyph,
        "circle": CircleGlyph,
        "ellipse": EllipseArcGlyph,
        "lwpolyline_bulge": LwPolylineGlyph,
        "polyline2d": LwPolylineGlyph,
        "polyline3d": LwPolylineGlyph,
        "spline": SplineGlyph,
        "spline_fit": SplineGlyph,
        "spline_degree2": SplineGlyph,
    }
    for case, cls in expect.items():
        contour, _ = _import_one(ENTITY_CASES[case])
        assert isinstance(contour.elements[0].glyph, cls), case
    arc, _ = _import_one(ENTITY_CASES["arc_wrap"])
    g = arc.elements[0].glyph
    assert isinstance(g, ArcGlyph)
    assert g.end_angle - g.start_angle == pytest.approx(math.radians(90))
    assert g.start_angle == pytest.approx(math.radians(300))


def test_point_and_solid() -> None:
    c, _ = _import_one(lambda m: m.add_point((7, 8)))
    assert isinstance(c.elements[0].glyph, PointGlyph)
    c, _ = _import_one(lambda m: m.add_solid([(0, 0), (1, 0), (0, 1), (1, 1)]))
    g = c.elements[0].glyph
    assert isinstance(g, LwPolylineGlyph) and g.closed == 1
    assert [tuple(v.pt) for v in g.vertices] == [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert c.length == pytest.approx(4.0)


def test_rational_spline_is_approximated_with_warning() -> None:
    doc = ezdxf.new("R2000")
    sp = doc.modelspace().add_rational_spline(
        [(0, 0), (1, 2), (3, 3), (4, 0)], weights=[1, 3, 1, 1], degree=3
    )
    r = dxfmod.import_dxf(doc)
    assert any("rational" in w for w in r.warnings)
    c = r.document.graphs[0]
    assert isinstance(c, Contour)
    assert _hausdorff(_pts(c, 0.005), _ezdxf_points(sp)) < 1e-3


def test_ignored_and_infinite_entities() -> None:
    doc = ezdxf.new("R2000")
    m = doc.modelspace()
    m.add_line((0, 0), (1, 0))
    h = m.add_hatch()
    h.paths.add_polyline_path([(0, 0), (1, 0), (1, 1)])
    m.add_xline((0, 0), (1, 1))
    m.add_ray((0, 0), (1, 1))
    m.add_linear_dim(base=(0, 3), p1=(0, 0), p2=(3, 0)).render()
    m.add_leader([(0, 0), (1, 1)])
    r = dxfmod.import_dxf(doc)
    assert len(r.document.graphs) == 1
    assert r.ignored_counts["HATCH"] == 1
    assert r.ignored_counts["DIMENSION"] == 1
    assert r.ignored_counts["LEADER"] == 1
    assert r.ignored_counts["XLINE"] == 1 and r.ignored_counts["RAY"] == 1


def _block_doc() -> ezdxf.document.Drawing:
    doc = ezdxf.new("R2000")
    blk = doc.blocks.new("B1")
    blk.add_line((0, 0), (1, 0))
    blk.add_circle((0, 0), 1)
    inner = doc.blocks.new("B2")
    inner.add_arc((0, 0), 1, 0, 90)
    blk.add_blockref("B2", (3, 0))
    m = doc.modelspace()
    m.add_blockref("B1", (100, 100), dxfattribs={"xscale": 2, "yscale": 2, "rotation": 90})
    mi = m.add_blockref("B1", (0, 0))
    mi.grid(size=(2, 3), spacing=(10, 20))
    return doc


def test_insert_exploded_pd373() -> None:
    r = dxfmod.import_dxf(_block_doc())
    # 1 + 6 (MINSERT 2x3) references x (line, circle, nested arc)
    assert len(r.document.graphs) == 21
    assert r.imported_counts["INSERT"] == 9  # 7 references of B1, each with a nested B2
    first = r.document.graphs[:3]
    seg = first[0].elements[0].glyph  # type: ignore[union-attr]
    assert isinstance(seg, SegmentGlyph)
    assert seg.p0 == pytest.approx((100, 100)) and seg.p1 == pytest.approx((100, 102))
    circ = first[1].elements[0].glyph  # type: ignore[union-attr]
    assert isinstance(circ, CircleGlyph) and circ.radius == pytest.approx(2)
    arc_c = first[2]
    assert isinstance(arc_c, Contour)
    assert arc_c.length == pytest.approx(math.pi, abs=1e-4)  # r=2, 90 deg
    assert arc_c.bbox_min == pytest.approx((98, 106), abs=1e-6)


def test_insert_not_exploded_becomes_group() -> None:
    r = dxfmod.import_dxf(_block_doc(), DxfImportOptions(explode_blocks=False))
    assert len(r.document.graphs) == 7
    assert all(isinstance(g, Group) and len(g.children) == 3 for g in r.document.graphs)
    g0 = r.document.graphs[0]
    assert isinstance(g0, Group)
    assert g0.length == pytest.approx(sum(k.length for k in g0.children))
    back = chf.read_chf(chf.write_chf(r.document))
    assert chf.check_document(back) == []


def test_text_to_curves_pd374() -> None:
    doc = ezdxf.new("R2000")
    doc.modelspace().add_text("Hi", dxfattribs={"height": 5, "insert": (30, 30)})
    r = dxfmod.import_dxf(doc)
    assert len(r.document.graphs) == 1
    grp = r.document.graphs[0]
    assert isinstance(grp, Group) and grp.children
    assert grp.bbox_min[0] >= 29.0 and grp.bbox_min[1] >= 29.0
    assert grp.bbox_max[1] <= 36.0
    for k in grp.children:
        assert check_contour(k) == []
    assert chf.check_document(chf.read_chf(chf.write_chf(r.document))) == []


def test_mtext_to_curves() -> None:
    doc = ezdxf.new("R2000")
    doc.modelspace().add_mtext("X\\PY", dxfattribs={"char_height": 3, "insert": (40, 40)})
    r = dxfmod.import_dxf(doc)
    assert len(r.document.graphs) == 1
    grp = r.document.graphs[0]
    assert isinstance(grp, Group) and len(grp.children) >= 2
    assert r.imported_counts["MTEXT"] == 1


def test_text_skipped_when_disabled_or_no_fonttools(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = ezdxf.new("R2000")
    doc.modelspace().add_text("Hi")
    r = dxfmod.import_dxf(doc, DxfImportOptions(text_to_curves=False))
    assert r.document.graphs == [] and r.ignored_counts["TEXT"] == 1
    assert any("pd374" in w for w in r.warnings)
    monkeypatch.setattr(dxfmod, "fonttools_available", lambda: False)
    r = dxfmod.import_dxf(doc)
    assert r.document.graphs == []
    assert any("fontTools" in w for w in r.warnings)


def test_unclamped_spline_knots_are_kept() -> None:
    knots = [float(k) for k in range(11)]
    doc = ezdxf.new("R2000")
    doc.modelspace().add_open_spline(
        [(0, 0), (4, 0), (4, 4), (0, 4), (0, 0), (4, 0), (4, 4)], degree=3, knots=knots
    )
    c = dxfmod.import_dxf(doc).document.graphs[0]
    g = c.elements[0].glyph  # type: ignore[union-attr]
    assert isinstance(g, SplineGlyph) and g.knots == knots
    assert check_contour(c) == []  # type: ignore[arg-type]
    # uniform cubic B-spline: the curve starts at (P0 + 4 P1 + P2) / 6
    assert c.start == pytest.approx((20 / 6, 4 / 6))  # type: ignore[union-attr]


def test_bezier_text_glyph_is_exact_bspline() -> None:
    """A cubic Bezier stored as knots 0,0,0,0,1,1,1,1 evaluates to the Bezier (03 §7 type 7)."""
    ctrl = [(0.0, 0.0), (1.0, 2.0), (3.0, 2.0), (4.0, 0.0)]
    g = SplineGlyph(0, 0, [chf.Vec2(*p) for p in ctrl], [0.0] * 4 + [1.0] * 4)
    pl = flatten_glyph(g)[0]
    for i, (x, y) in enumerate(pl):
        t = i / (len(pl) - 1)
        bx = sum(math.comb(3, k) * (1 - t) ** (3 - k) * t**k * ctrl[k][0] for k in range(4))
        by = sum(math.comb(3, k) * (1 - t) ** (3 - k) * t**k * ctrl[k][1] for k in range(4))
        assert (x, y) == pytest.approx((bx, by), abs=1e-9)


def test_cp936_codepage_and_utf8() -> None:
    doc = ezdxf.new("R2000")
    doc.encoding = "gbk"
    doc.layers.add("层A€")
    doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "层A€"})
    text = io.StringIO()
    doc.write(text)
    from nexcut.io import cp936

    data = cp936.encode(text.getvalue())
    assert b"\x80" in data  # euro sign: only the Windows table has it (cp936 module docstring)
    r = read_dxf(data)
    assert r.encoding == "cp936"
    assert "层A€" in r.layers
    assert r.entity_layers["层A€"] == 1

    doc7 = ezdxf.new("R2010")
    doc7.layers.add("层B")
    doc7.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "层B"})
    r7 = read_dxf(_dxf_bytes(doc7, "utf-8"))
    assert r7.encoding == "utf-8" and "层B" in r7.layers


def test_decode_dxf_bytes_default_codepage() -> None:
    text, enc = dxfmod.decode_dxf_bytes(
        b"  0\r\nSECTION\r\n  2\r\nHEADER\r\n  0\r\nENDSEC\r\n  0\r\nEOF\r\n"
    )
    assert enc == "cp1252" and "HEADER" in text


def test_binary_dxf(tmp_path: Path) -> None:
    doc = ezdxf.new("R2000")
    doc.modelspace().add_circle((1, 2), 3)
    p = tmp_path / "b.dxf"
    doc.saveas(p, fmt="bin")
    r = read_dxf(p)
    assert r.encoding == "binary"
    g = r.document.graphs[0].elements[0].glyph  # type: ignore[union-attr]
    assert isinstance(g, CircleGlyph) and g.radius == pytest.approx(3)


def test_bad_input() -> None:
    with pytest.raises(DxfImportError):
        read_dxf(b"")
    with pytest.raises(DxfImportError):
        read_dxf(b"this is not a dxf file\n")
    with pytest.raises(DxfImportError):
        read_dxf(Path("/nonexistent/x.dxf"))


def test_read_color_maps_aci_to_layer() -> None:
    doc = ezdxf.new("R2000")
    doc.layers.add("red", color=1)
    m = doc.modelspace()
    m.add_line((0, 0), (1, 0), dxfattribs={"color": 5})
    m.add_line((0, 1), (1, 1), dxfattribs={"layer": "red"})
    assert [g.layer for g in dxfmod.import_dxf(doc).document.graphs] == [0, 0]
    r = dxfmod.import_dxf(doc, DxfImportOptions(read_color=True))
    assert [g.layer for g in r.document.graphs] == [4, 0]


def test_export_normalises_arcs_and_ellipses() -> None:
    doc = chf.ChfDocument()
    from nexcut.ops.import_gates import build_contour

    cw = ArcGlyph(chf.Vec2(0, 0), 2.0, math.radians(90), math.radians(10))
    ell = EllipseArcGlyph(chf.Vec2(5, 5), chf.Vec2(1, 0), 3.0, 0.0, math.pi)
    spl = SplineGlyph(
        0,
        0,
        [chf.Vec2(0, 0), chf.Vec2(1, 1), chf.Vec2(2, 0), chf.Vec2(3, 1)],
        [0, 0, 0, 0, 1, 1, 1, 1],
    )
    for g in (cw, ell, spl, PointGlyph(chf.Vec2(1, 1))):
        doc.graphs.append(build_contour([g], layer=2))
    buf = io.BytesIO()
    write_dxf(doc, buf)
    back = read_dxf(buf.getvalue(), DxfImportOptions(read_color=True))
    assert [g.layer for g in back.document.graphs] == [2, 2, 2, 2]
    for a, b in zip(_all_contours(doc), _all_contours(back.document), strict=True):
        assert _hausdorff(_pts(a, 0.004), _pts(b, 0.004)) < 1e-4
    ell_back = back.document.graphs[1].elements[0].glyph  # type: ignore[union-attr]
    assert isinstance(ell_back, EllipseArcGlyph) and ell_back.ratio == pytest.approx(1 / 3)


def test_insert_non_uniform_scale_and_attribs() -> None:
    """ezdxf explodes bulges/ellipses under non-uniform scaling; visible ATTRIBs become text curves."""
    doc = ezdxf.new("R2000")
    blk = doc.blocks.new("B")
    blk.add_lwpolyline([(0, 0, 0, 0, 0.5), (5, 0, 0, 0, 0), (5, 5)], format="xyseb")
    blk.add_ellipse((0, 0), (2, 0, 0), 0.5)
    blk.add_attdef("TAG", (0, 0), dxfattribs={"height": 1})
    ins = doc.modelspace().add_blockref("B", (10, 10), dxfattribs={"xscale": 2, "rotation": 30})
    ins.add_auto_attribs({"TAG": "V1"})
    r = dxfmod.import_dxf(doc)
    assert r.imported_counts["INSERT"] == 1 and r.imported_counts["ATTRIB"] == 1
    assert r.warnings == []
    assert isinstance(r.document.graphs[-1], Group)  # the attribute text
    for c in _all_contours(r.document):
        assert check_contour(c) == []
