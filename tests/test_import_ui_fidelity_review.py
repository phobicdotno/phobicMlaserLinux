"""Adversarial import/UI fidelity review (io/dxf, io/plt, io/gcode, ops/import_gates, ops/sort, ui/*).

Each test pins one finding.  Oracles are independent of the code under test:
ezdxf's path module and scipy's B-spline evaluation for DXF geometry, the stdlib
rasteriser of ``tools/chf_parse.py`` for renders, and a verbatim copy of the old
pairwise sort for the KD-tree rewrite.  ``xfail(strict=True)`` marks divergences
that live in modules owned elsewhere.  Vendor files are read through ``src_dir``
(skips when absent); golden numbers are copied here.
"""

from __future__ import annotations

import importlib.util
import math
import os
import random
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import ezdxf
import numpy as np
import pytest
from scipy.interpolate import BSpline as SciBSpline
from scipy.spatial import cKDTree

from conftest import speed_factor
from nexcut.io import chf
from nexcut.io.dxf import DxfImportError, import_dxf, read_dxf
from nexcut.io.gcode import ERR_FORMAT, GCodeError, read_gcode
from nexcut.io.plt import PltError, read_plt
from nexcut.model.flatten import flatten_glyph
from nexcut.model.glyph import (
    CircleGlyph,
    LwPolylineGlyph,
    LwPolyVertex,
    SegmentGlyph,
    SplineGlyph,
    Vec2,
)
from nexcut.model.graph import ChfDocument, Contour, ContourElement, Graph, iter_contours
from nexcut.ops import sort as sortmod
from nexcut.ops.import_gates import (
    build_contour,
    element_endpoints,
    glyph_is_finite,
    is_closed,
)
from nexcut.ui.i18n import (
    LANGUAGE_FILES,
    Translator,
    parse_lang_txt,
    parse_translation_txt,
)

REPO = Path(__file__).resolve().parents[1]
VENDOR_DXF = "Testfile SS1mm 2.0s F+1 N2.dxf"


# ============================================================================ DXF geometry oracle


def _densify(pl: np.ndarray, h: float = 0.01) -> np.ndarray:
    out = [pl[:1]]
    for a, b in zip(pl[:-1], pl[1:], strict=True):
        n = max(1, int(math.hypot(*(b - a)) / h))
        t = (np.arange(1, n + 1) / n)[:, None]
        out.append(a + (b - a) * t)
    return np.concatenate(out)


def _model_points(doc: ChfDocument) -> np.ndarray:
    """Dense points of every imported glyph; splines evaluated exactly (scipy) on [t3, tn]."""
    parts: list[np.ndarray] = []
    for g in doc.graphs:
        for c, _ in iter_contours(g):
            for el in c.elements:
                gl = el.glyph
                if isinstance(gl, SplineGlyph):
                    k = np.asarray(gl.knots)
                    m = len(gl.control_points)
                    u = np.linspace(k[3], k[m], 20001)
                    parts.append(_densify(SciBSpline(k, np.asarray(gl.control_points), 3)(u)))
                    continue
                for pl in flatten_glyph(gl, 0.01):
                    parts.append(_densify(np.asarray(pl, dtype=float).reshape(-1, 2)))
    return np.concatenate(parts) if parts else np.zeros((0, 2))


def _reference_points(msp: object) -> np.ndarray:
    """ezdxf's own WCS geometry: recursive INSERT decomposition + path flattening."""
    from ezdxf import path as ezpath
    from ezdxf.disassemble import recursive_decompose

    parts: list[np.ndarray] = []
    for e in recursive_decompose(list(msp)):  # type: ignore[call-overload]
        if e.dxftype() == "SPLINE":
            bs = e.construction_tool()
            knots = np.asarray(bs.knots())
            if bs.is_rational:  # clamped rational: ezdxf's evaluation is exact
                u = np.linspace(0.0, bs.max_t, 40001)
                parts.append(_densify(np.array([(p.x, p.y) for p in bs.points(u)])))
            else:  # scipy honours the valid domain of unclamped (periodic) knot vectors
                ctrl = np.array([(p.x, p.y) for p in bs.control_points])
                p = bs.degree
                u = np.linspace(knots[p], knots[len(ctrl)], 40001)
                parts.append(_densify(SciBSpline(knots, ctrl, p)(u)))
            continue
        try:
            path = ezpath.make_path(e)
        except TypeError:
            continue
        for sub in path.sub_paths():
            v = np.array([(q.x, q.y) for q in sub.flattening(0.0005, segments=64)])
            if len(v):
                parts.append(_densify(v))
    return np.concatenate(parts) if parts else np.zeros((0, 2))


def _hausdorff(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) == 0 or len(b) == 0:
        return math.inf
    return float(max(cKDTree(b).query(a)[0].max(), cKDTree(a).query(b)[0].max()))


NEG_Z = {"extrusion": (0, 0, -1)}
W = math.sqrt(0.5)


def _block(dwg: object) -> None:
    b = dwg.blocks.new("B", base_point=(5, 2))  # type: ignore[attr-defined]
    b.add_arc((10, 0), 5, 10, 100)
    b.add_circle((0, 10), 3)
    b.add_lwpolyline([(0, 0, 0, 0, 0.5), (10, 0, 0, 0, -0.3), (10, 10, 0, 0, 0)], format="xyseb")
    b.add_ellipse((20, 0), major_axis=(4, 1, 0), ratio=0.4, start_param=0.3, end_param=2.5)
    b.add_line((0, 0), (3, 7))


def _insert(**attribs: object) -> Callable[[object, object], None]:
    def make(dwg: object, msp: object) -> None:
        _block(dwg)
        msp.add_blockref("B", (100, 50), dxfattribs=attribs)  # type: ignore[attr-defined]

    return make


def _nested(dwg: object, msp: object) -> None:
    _block(dwg)
    c = dwg.blocks.new("C")  # type: ignore[attr-defined]
    c.add_blockref("B", (0, 0), dxfattribs={"xscale": -1, "rotation": 90})
    msp.add_blockref("C", (10, 10), dxfattribs={"xscale": 2})  # type: ignore[attr-defined]


def _minsert(dwg: object, msp: object) -> None:
    _block(dwg)
    ref = msp.add_blockref("B", (0, 0), dxfattribs={"rotation": 10})  # type: ignore[attr-defined]
    ref.grid(size=(2, 3), spacing=(40, 50))


def _closed_spline(dwg: object, msp: object) -> None:
    sp = msp.add_spline()  # type: ignore[attr-defined]
    sp.set_closed([(0, 0, 0), (3, 5, 0), (6, -2, 0), (9, 4, 0), (12, 0, 0), (6, -6, 0)], degree=3)


CIRCLE_CTRL = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0)]
CIRCLE_KNOTS = [0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 4]

GEOMETRY_CASES: dict[str, Callable[[object, object], None]] = {
    "arc +Z": lambda d, m: m.add_arc((10, 5), 4, 20, 250),
    "arc -Z": lambda d, m: m.add_arc((10, 5), 4, 20, 250, dxfattribs=NEG_Z),
    "arc -Z wrapping 0deg": lambda d, m: m.add_arc((10, 5), 4, 300, 40, dxfattribs=NEG_Z),
    "circle -Z": lambda d, m: m.add_circle((10, 5), 4, dxfattribs=NEG_Z),
    "ellipse -Z": lambda d, m: m.add_ellipse(
        (10, 5), major_axis=(3, 2, 0), ratio=0.5, start_param=0.2, end_param=2.0, dxfattribs=NEG_Z
    ),
    "ellipse param wrap": lambda d, m: m.add_ellipse(
        (10, 5), major_axis=(3, 2, 0), ratio=0.5, start_param=5.0, end_param=1.0
    ),
    "lwpolyline bulges -Z": lambda d, m: m.add_lwpolyline(
        [(0, 0, 0, 0, 0.5), (10, 0, 0, 0, -1), (10, 10, 0, 0, 0.2)],
        format="xyseb",
        close=True,
        dxfattribs=NEG_Z,
    ),
    "polyline2d bulges -Z": lambda d, m: m.add_polyline2d(
        [(0, 0, 0, 0, 0.5), (10, 0, 0, 0, -1), (10, 10, 0, 0, 0.2)],
        format="xyseb",
        dxfattribs=NEG_Z,
    ),
    "lwpolyline major-arc bulges": lambda d, m: m.add_lwpolyline(
        [(0, 0, 2.5), (10, 0, -1.8), (10, 10, 0.3), (0, 10, 4.0)], format="xyb", close=True
    ),
    "solid -Z": lambda d, m: m.add_solid([(0, 0), (5, 0), (0, 5), (5, 5)], dxfattribs=NEG_Z),
    "spline degree 3": lambda d, m: m.add_open_spline([(0, 0), (3, 5), (6, -2), (9, 4), (12, 0)]),
    "spline degree 2": lambda d, m: m.add_open_spline([(0, 0), (3, 5), (6, -2), (9, 4)], degree=2),
    "spline fit points": lambda d, m: m.add_spline([(0, 0), (3, 5), (6, -2), (9, 4), (12, 0)]),
    "spline degree 5": lambda d, m: m.add_open_spline(
        [(0, 0), (3, 5), (6, -2), (9, 4), (12, 0), (15, 3), (18, -1)], degree=5
    ),
    "spline rational circle R500": lambda d, m: m.add_rational_spline(
        [(500 * x, 500 * y) for x, y in CIRCLE_CTRL],
        [1, W, 1, W, 1, W, 1, W, 1],
        degree=2,
        knots=CIRCLE_KNOTS,
    ),
    "spline closed periodic": _closed_spline,
    "insert rot30 scale2": _insert(rotation=30, xscale=2, yscale=2),
    "insert mirrored x": _insert(xscale=-1),
    "insert mirrored y rot45": _insert(xscale=1.5, yscale=-1.5, rotation=45),
    "insert non-uniform scale": _insert(xscale=2, yscale=0.5, rotation=20),
    "insert extrusion -Z": _insert(extrusion=(0, 0, -1), rotation=15),
    "insert nested mirrored": _nested,
    "minsert grid": _minsert,
}


@pytest.mark.parametrize("name", sorted(GEOMETRY_CASES))
def test_dxf_import_matches_ezdxf_wcs_geometry(name: str) -> None:
    """Bulge sign, arc direction, OCS -Z, ellipse params, spline domains, INSERT transforms.

    Finding (fixed in model/flatten, shared): a bulge span with ``|bulge| > 1``
    (arc > 180 deg) put the centre on the minor-arc side, so the flattened span did
    not end at its next vertex - 11.2 mm off for a 10 mm chord with bulge 2.  The
    planner (plan/contour_fit) already used the correct rule, so display, cached
    length/bbox and IGP gates disagreed with the cut path.  Every other case
    (OCS -Z, ellipse params, splines, INSERT transforms) agreed with the oracle.
    """
    dwg = ezdxf.new("R2000")
    msp = dwg.modelspace()
    GEOMETRY_CASES[name](dwg, msp)
    res = import_dxf(dwg)
    h = _hausdorff(_model_points(res.document), _reference_points(msp))
    assert h < 0.02, f"{name}: Hausdorff {h:.4f} mm"


def test_dxf_oracle_rejects_mirrored_geometry() -> None:
    """Negative control: flipping bulge / OCS handling must fail the oracle."""
    dwg = ezdxf.new("R2000")
    msp = dwg.modelspace()
    GEOMETRY_CASES["lwpolyline bulges -Z"](dwg, msp)
    res = import_dxf(dwg)
    ref = _reference_points(msp)
    c = res.document.graphs[0]
    assert isinstance(c, Contour)
    gl = c.elements[0].glyph
    assert isinstance(gl, LwPolylineGlyph)
    wrong = LwPolylineGlyph(gl.closed, [LwPolyVertex(v.pt, -v.bulge) for v in gl.vertices])
    assert _hausdorff(_model_points(ChfDocument(graphs=[build_contour([wrong])])), ref) > 1.0
    mirrored = LwPolylineGlyph(
        gl.closed, [LwPolyVertex(Vec2(-v.pt[0], v.pt[1]), v.bulge) for v in gl.vertices]
    )
    assert _hausdorff(_model_points(ChfDocument(graphs=[build_contour([mirrored])])), ref) > 1.0


def test_vendor_dxf_import_facts(src_dir: Path) -> None:
    """$DWGCODEPAGE ANSI_936 layer 图层1, $INSUNITS=1 ignored, 9 entities, 3 closed contours."""
    path = src_dir.parent / VENDOR_DXF
    if not path.is_file():
        pytest.skip("vendor DXF not available")
    res = read_dxf(path)
    assert (res.acadver, res.codepage, res.encoding, res.insunits) == (
        "AC1015",
        "ANSI_936",
        "cp936",
        1,
    )
    assert res.layers == ["0", "ар 1", "图层1", "Defpoints"]
    assert res.entity_layers == {"图层1": 9}
    assert dict(res.imported_counts) == {"ARC": 4, "SPLINE": 3, "CIRCLE": 1, "LWPOLYLINE": 1}
    dwg = ezdxf.readfile(path)
    h = _hausdorff(_model_points(res.document), _reference_points(dwg.modelspace()))
    assert h < 0.02
    from nexcut.ui.loader import load_document

    doc = load_document(path).document
    contours = [c for g in doc.graphs for c, _ in iter_contours(g)]
    assert len(contours) == 3 and all(is_closed(c) for c in contours)
    # The two spline contours grew by 0.005/0.007 mm when model/flatten started to
    # honour the chord step (STATUS X10): the lengths below are the converged ones
    # (identical at step 0.002, 0.0005 and 0.0001), the old ones were under-sampled.
    assert sorted(c.length for c in contours) == pytest.approx(
        [19.2031, 69.2492, 220.3503], abs=2e-4
    )


# ============================================================================ DXF robustness


def _r2000_text(build: Callable[[object], None]) -> str:
    import io

    dwg = ezdxf.new("R2000")
    build(dwg.modelspace())
    buf = io.StringIO()
    dwg.write(buf)
    return buf.getvalue()


def test_dxf_integer_overflow_is_an_import_error(tmp_path: Path) -> None:
    """Finding (fixed): ``1e999`` in an integer group raised OverflowError out of read_dxf.

    Evidence: fuzzing the vendor DXF (1 500 mutations) - 18 OverflowErrors from
    ezdxf's tag compiler ``int(float(value))``; MainWindow.open_path only catches
    LoadError, so File > Open crashed the slot.
    """
    text = _r2000_text(lambda m: m.add_line((0, 0), (1, 1)))
    bad = re.sub(r"(\n 70\n)\s*\d+", r"\1-1e999", text, count=1)
    assert bad != text
    with pytest.raises(DxfImportError):
        read_dxf(bad.encode())
    from nexcut.ui.loader import LoadError, load_document

    p = tmp_path / "bad.dxf"
    p.write_bytes(bad.encode())
    with pytest.raises(LoadError):
        load_document(p)


def test_dxf_non_finite_coordinates_are_skipped() -> None:
    """Finding (fixed): ``nan``/``inf`` coordinates were imported (NaN bbox, inf length)."""
    text = _r2000_text(lambda m: (m.add_line((0, 0), (10, 10)), m.add_circle((5, 5), 2)))
    text = re.sub(r"(AcDbLine\n 10\n)0\.0", r"\1nan", text)
    text = re.sub(r"(AcDbCircle\n 10\n)5\.0", r"\1inf", text)
    res = read_dxf(text.encode())
    assert res.document.graphs == []
    assert res.ignored_counts == {"LINE": 1, "CIRCLE": 1}
    assert any("non-finite" in w for w in res.warnings)
    assert glyph_is_finite(SegmentGlyph(Vec2(0, 0), Vec2(1, 1)))
    assert not glyph_is_finite(LwPolylineGlyph(0, [LwPolyVertex(Vec2(0, 0), math.nan)]))


def test_dxf_entity_layer_names_are_decoded() -> None:
    """Finding (fixed): ``entity_layers`` kept ``\\U+XXXX`` escapes while ``layers`` decoded them."""
    dwg = ezdxf.new("R2000")
    dwg.layers.add("\\U+0430\\U+0440 1")
    dwg.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "\\U+0430\\U+0440 1"})
    res = import_dxf(dwg)
    assert "ар 1" in res.layers
    assert res.entity_layers == {"ар 1": 1}


def _mutations(data: bytes, rnd: random.Random, alphabet: bytes, count: int) -> list[bytes]:
    out = []
    for _ in range(count):
        d = bytearray(data)
        for _ in range(rnd.randint(1, 4)):
            d[rnd.randrange(len(d))] = rnd.choice(alphabet)
        out.append(bytes(d))
    return out


def test_loader_raises_only_load_error_on_damaged_files(tmp_path: Path) -> None:
    """Import robustness: truncated / mutated DXF, G-code and PLT never escape as other errors."""
    from nexcut.ui.loader import LoadError, load_document

    rnd = random.Random(20260916)
    dxf = _r2000_text(
        lambda m: (
            m.add_line((0, 0), (10, 10)),
            m.add_arc((5, 5), 3, 10, 200),
            m.add_lwpolyline([(0, 0, 0.3), (5, 0, 0), (5, 5, -0.2)], format="xyb"),
        )
    ).encode()
    lines = dxf.split(b"\n")
    samples: list[tuple[str, bytes]] = []
    for i in range(60):
        cut = list(lines)
        j = rnd.randrange(len(cut))
        cut[j] = rnd.choice([b"nan", b"inf", b"-1e999", b"x", b"", b"999999999999999999999"])
        samples.append(("dxf", b"\n".join(cut[: len(cut) - (i % 7)])))
    gcode = b"G90 G91.1\nG0 X0 Y0\nG2 X10 Y0 I5 J0\nG3 X0 Y0 R5\nG1 X1\nM02\n"
    samples += [
        ("nc", d) for d in _mutations(gcode, rnd, b"GXYIJRL-+.;()%0123456789 \nMNP\xff", 150)
    ]
    plt = b"IN;SP1;PU0,0;PD100,100;AA50,50,90;CI10;PE<=?_;EA10,10;SC0,100,100,0;AR5,5,-45;LBhi\x03;"
    samples += [
        ("plt", d) for d in _mutations(plt, rnd, b"PUDAECISL;,-0123456789.<=>:7?_\x03\xff", 150)
    ]
    samples += [("plt", b"IN;PD" + b"9" * 400 + b",1;"), ("nc", b"G1 X" + b"9" * 400 + b"\n")]
    for n, (suffix, data) in enumerate(samples):
        p = tmp_path / f"f{n}.{suffix}"
        p.write_bytes(data)
        try:
            loaded = load_document(p)
        except LoadError:
            continue
        for g in loaded.document.graphs:
            for c, _ in iter_contours(g):
                assert all(glyph_is_finite(el.glyph) for el in c.elements), (suffix, data[:80])


def test_exact_contour_measure_matches_sampling() -> None:
    """``refresh_contour`` closed form == ``measure_contour`` sampling (check_contour clean)."""
    from nexcut.model.flatten import check_contour, measure_contour
    from nexcut.model.glyph import ArcGlyph, PointGlyph

    rnd = random.Random(7)
    checked = 0
    for _ in range(400):
        els = []
        for _ in range(rnd.randint(1, 3)):
            x, y, k = rnd.uniform(-50, 50), rnd.uniform(-50, 50), rnd.random()
            if k < 0.2:
                g: object = SegmentGlyph(
                    Vec2(x, y), Vec2(x + rnd.uniform(-5, 5), y + rnd.uniform(-5, 5))
                )
            elif k < 0.45:
                a = rnd.uniform(-10, 10)
                g = ArcGlyph(Vec2(x, y), rnd.uniform(0.01, 3), a, a + rnd.uniform(-8, 8))
            elif k < 0.55:
                g = CircleGlyph(Vec2(x, y), rnd.uniform(0.01, 2))
            elif k < 0.6:
                g = PointGlyph(Vec2(x, y))
            else:
                bulges = [0.0, rnd.uniform(-3, 3), 1e-13, 1.0, -1.0, rnd.uniform(-50, 50)]
                verts = [
                    LwPolyVertex(
                        Vec2(x + rnd.uniform(-2, 2), y + rnd.uniform(-2, 2)), rnd.choice(bulges)
                    )
                    for _ in range(rnd.randint(2, 4))
                ]
                g = LwPolylineGlyph(rnd.randint(0, 1), verts)
            els.append(ContourElement(g, rnd.choice([1, -1])))  # type: ignore[arg-type]
        c = Contour(elements=els)
        m = measure_contour(c)
        assert m is not None
        from nexcut.ops.import_gates import refresh_contour

        refresh_contour(c)
        assert check_contour(c) == []
        assert c.length == pytest.approx(m.length, rel=1e-5, abs=1e-9)
        assert math.dist(c.start, m.start) < 1e-9 and math.dist(c.end, m.end) < 1e-9
        assert math.dist(c.bbox_min, m.bbox_min) < 1e-4 and math.dist(c.bbox_max, m.bbox_max) < 1e-4
        checked += 1
    assert checked == 400


def test_import_of_many_circles_is_fast() -> None:
    """Finding (fixed): arcs/circles were sampled at 2 um in Python to fill the contour cache.

    Evidence: a DXF with 1 000 circles r = 50 mm took 15.0 s in import_dxf
    (build_contour -> measure_contour at CHECK_STEP, 20 000 points per circle) and
    15.8 s more in the overlap gate (10 um sampling of every glyph); a 1 000-hole
    nest could not be opened interactively.  Now: closed-form extents, lazy sampling.
    """
    from nexcut.ops.import_gates import apply_import_gates

    dwg = ezdxf.new("R2000")
    msp = dwg.modelspace()
    for i in range(1000):
        msp.add_circle(((i % 50) * 120.0, (i // 50) * 120.0), 50)
    msp.add_circle((0.0, 0.0), 50)  # an exact duplicate must still be removed
    t = time.perf_counter()
    res = import_dxf(dwg)
    report = apply_import_gates(res.document)
    elapsed = time.perf_counter() - t
    print(f"\n1000 circles: import + gates {elapsed:.2f} s (budget {budget_s():.2f} s)")
    assert elapsed < budget_s(), elapsed
    assert report.overlaps_removed == 1 and len(res.document.graphs) == 1000


# ============================================================================ G-code / PLT


def test_gcode_g91_1_does_not_switch_to_incremental_distance_mode() -> None:
    """Finding (fixed): lenient ``G91.1`` was truncated to ``G91`` (incremental XY).

    Evidence: the Fusion 360 Mach3 post-processor header ``G90 G94 G91.1 G40 G49 G17``
    made every following coordinate relative - the arc below raised "Arc radius is
    error!", and arc-free programs imported silently displaced.
    """
    src = b"G90 G94 G91.1 G40 G49 G17\nG0 X10 Y10\nG1 X20 Y10\nG2 X30 Y10 I5 J0\nG1 X40 Y10\nM30\n"
    res = read_gcode(src)
    (c,) = res.document.graphs
    assert isinstance(c, Contour)
    assert (c.bbox_min, c.bbox_max) == (Vec2(10.0, 10.0), Vec2(40.0, 15.0))
    assert res.g_counts[91.1] == 1 and res.g_counts[91] == 0
    # G90.1: absolute arc centres
    absolute = read_gcode(b"G90.1\nG0 X10 Y10\nG2 X30 Y10 I20 J10\nM30\n").document.graphs[0]
    assert isinstance(absolute, Contour)
    arc = absolute.elements[0].glyph
    assert arc.center == Vec2(20.0, 10.0) and arc.radius == 10.0  # type: ignore[union-attr]
    assert "G93.5 ignored" in read_gcode(b"G93.5\nG1 X1\n").warnings


def test_gcode_and_plt_reject_overflowing_numbers() -> None:
    """Finding (fixed): a 400-digit literal became ``inf`` geometry (G-code) or OverflowError."""
    with pytest.raises(GCodeError) as exc:
        read_gcode(b"G1 X10 Y10\nG1 X" + b"9" * 400 + b".0\n")
    assert exc.value.message == ERR_FORMAT
    with pytest.raises(GCodeError):
        read_gcode(b"G" + b"9" * 400 + b" X1\n")
    with pytest.raises(PltError):
        read_plt(b"IN;PU0,0;PD" + b"9" * 400 + b",0;")
    with pytest.raises(PltError):
        read_plt(b"IN;PE" + b"~" * 300 + b"\xbf\xbf;")


def plt_mm(sc: str, x: float, y: float) -> tuple[float, float]:
    """User units -> mm for ``IN;SC...`` with the default P1/P2 = (0,0)/(10000,10000) plu."""
    x0, x1, y0, y1 = (float(v) for v in sc.split(","))
    return ((x - x0) / (x1 - x0) * 10000 / 40, (y - y0) / (y1 - y0) * 10000 / 40)


@pytest.mark.parametrize("sc", ["0,100,0,100", "0,100,100,0", "100,0,0,100", "100,0,100,0"])
def test_plt_arc_sweep_follows_user_units_under_reflecting_scale(sc: str) -> None:
    """Finding (fixed): with a reflecting ``SC`` the ``AA`` sweep was applied in plotter units.

    The arc then ended mirrored about its centre, 50 mm away from the user-unit end
    point (0, 60) for ``SC0,100,100,0``.  HP-GL/2: centre, start
    and sweep are user-unit quantities; a reflection reverses the plotter-space sweep.
    """
    res = read_plt(f"IN;SC{sc};PU10,50;PD;AA0,50,90;PA0,70;PU0,60;EW5,0,90;PU;".encode())
    arc_el, line_el = [e for g in res.document.graphs[:1] for e in g.elements]  # type: ignore[union-attr]
    assert math.dist(element_endpoints(arc_el)[0], plt_mm(sc, 10, 50)) < 1e-9
    assert math.dist(element_endpoints(arc_el)[1], plt_mm(sc, 0, 60)) < 1e-9
    wedge = res.document.graphs[-1]
    assert isinstance(wedge, Contour)
    ends = [element_endpoints(e) for e in wedge.elements]
    centre = ends[0][0]
    assert math.dist(element_endpoints(line_el)[1], plt_mm(sc, 0, 70)) < 1e-9
    # EW5,0,90 at user (0,60): arc from user angle 0 (x+) to 90 (y+), radius 5 user units
    ux = 1.0 if sc.startswith("0,") else -1.0
    uy = 1.0 if sc.split(",")[2] == "0" else -1.0
    assert ends[0][1] == pytest.approx((centre[0] + ux * 12.5, centre[1]), abs=1e-9)
    assert ends[1][1] == pytest.approx((centre[0], centre[1] + uy * 12.5), abs=1e-9)


# ============================================================================ sort (KD-tree rewrite)


def _ref_nearest(
    graphs: Sequence[Graph],
    candidates: list[int],
    pos: Vec2,
    allow_reverse: bool,
    blockers: list[set[int]] | None,
    done: set[int],
) -> tuple[list[int], list[bool], Vec2]:
    """Verbatim copy of the pre-review pairwise nearest search (O(n^2))."""
    order: list[int] = []
    flags: list[bool] = []
    remaining = list(candidates)
    while remaining:
        best: tuple[float, int, bool] | None = None
        for idx in remaining:
            if blockers is not None and not blockers[idx] <= done:
                continue
            s, e = graphs[idx].start, graphs[idx].end  # type: ignore[union-attr]
            d = math.dist(pos, s)
            if best is None or (d, idx) < (best[0], best[1]):
                best = (d, idx, False)
            g = graphs[idx]
            if allow_reverse and isinstance(g, Contour) and g.elements and not is_closed(g):
                d = math.dist(pos, e)
                if (d, idx) < (best[0], best[1]):
                    best = (d, idx, True)
        if best is None:
            best = (0.0, remaining[0], False)
        _, idx, rev = best
        remaining.remove(idx)
        done.add(idx)
        order.append(idx)
        flags.append(rev)
        s, e = graphs[idx].start, graphs[idx].end  # type: ignore[union-attr]
        pos = s if rev else e
    return order, flags, pos


def _ref_containment(graphs: Sequence[Graph]) -> tuple[list[int], list[set[int]]]:
    """Verbatim copy of the pre-review pairwise containment test."""
    outlines = [sortmod._outline(g) for g in graphs]
    boxes = [(g.bbox_min, g.bbox_max) for g in graphs]  # type: ignore[union-attr]
    depth = [0] * len(graphs)
    inside: list[set[int]] = [set() for _ in graphs]
    for i, g in enumerate(graphs):
        lo, hi = boxes[i]
        probe = sortmod._probe_point(g)
        for j, poly in enumerate(outlines):
            if j == i or poly is None:
                continue
            olo, ohi = boxes[j]
            if not (olo[0] <= lo[0] and olo[1] <= lo[1] and hi[0] <= ohi[0] and hi[1] <= ohi[1]):
                continue
            if (lo, hi) == (olo, ohi):
                continue
            if sortmod._point_in_polygon(probe, poly):
                depth[i] += 1
                inside[j].add(i)
    return depth, inside


def _random_graphs(rnd: random.Random, n: int) -> list[Graph]:
    out: list[Graph] = []
    for _ in range(n):
        x, y = rnd.randint(0, 20), rnd.randint(0, 20)  # integer grid: forces distance ties
        k = rnd.random()
        if k < 0.5:
            g = SegmentGlyph(Vec2(x, y), Vec2(x + rnd.randint(-3, 3), y + rnd.randint(-3, 3)))
            out.append(build_contour([g]))
        elif k < 0.75:
            out.append(build_contour([CircleGlyph(Vec2(x, y), rnd.choice([1, 2, 5, 8]))]))
        else:
            w, h = rnd.randint(1, 10), rnd.randint(1, 10)
            pts = [Vec2(x, y), Vec2(x + w, y), Vec2(x + w, y + h), Vec2(x, y + h)]
            out.append(build_contour([LwPolylineGlyph(1, [LwPolyVertex(p) for p in pts])]))
    return out


def test_kdtree_sort_equals_pairwise_reference() -> None:
    """The O(n log n) rewrite keeps the exact order, reversal flags and tie rules."""
    rnd = random.Random(3)
    for trial in range(120):
        graphs = _random_graphs(rnd, rnd.choice([0, 1, 2, 5, 17, 40]))
        depth, inside = _ref_containment(graphs)
        assert sortmod.containment_depths(graphs) == (depth, inside), trial
        for allow_reverse in (True, False):
            for inner_first in (True, False):
                blockers = inside if inner_first else None
                ref = _ref_nearest(
                    graphs, list(range(len(graphs))), Vec2(0, 0), allow_reverse, blockers, set()
                )
                got = sortmod.sort_graphs(
                    graphs,
                    sortmod.SortType.NEAREST,
                    allow_reverse=allow_reverse,
                    inner_first=inner_first,
                )
                assert (got.order, got.reversed) == (ref[0], ref[1]), (
                    trial,
                    allow_reverse,
                    inner_first,
                )
        for st in (sortmod.SortType.INSIDE_TO_OUTSIDE, sortmod.SortType.OUTSIDE_TO_INSIDE):
            levels = sorted(set(depth), reverse=st is sortmod.SortType.INSIDE_TO_OUTSIDE)
            order: list[int] = []
            pos, done = Vec2(0, 0), set()
            for lvl in levels:
                o, _, pos = _ref_nearest(
                    graphs,
                    [i for i in range(len(graphs)) if depth[i] == lvl],
                    pos,
                    True,
                    None,
                    done,
                )
                order.extend(o)
            assert sortmod.sort_graphs(graphs, st).order == order, (trial, st)


def _segment_grid(n: int, cols: int = 250) -> list[Graph]:
    out: list[Graph] = []
    for i in range(n):
        r, c = divmod(i, cols)
        p0, p1 = Vec2(c * 4.0, r * 4.0), Vec2(c * 4.0 + 3.0, r * 4.0 + 1.5)
        out.append(
            Contour(
                length=math.dist(p0, p1),
                bbox_min=p0,
                bbox_max=p1,
                start=p0,
                end=p1,
                elements=[ContourElement(SegmentGlyph(p0, p1))],
            )
        )
    return out


def test_nearest_sort_is_not_quadratic() -> None:
    """Finding (fixed): IGP.AutoSortType=5 made DXF import quadratic.

    Evidence (i5-5350U): opening a DXF of 2 000 separate LINEs took 12.8 s, 4 000
    took 53.6 s (pairwise ``_nearest`` + a PySide6-hooked local import per
    candidate); after the rewrite 4 000 lines open in 1.9 s.
    """
    graphs = _segment_grid(10_000)
    t = time.perf_counter()
    res = sortmod.sort_graphs(graphs, sortmod.SortType.NEAREST)
    elapsed = time.perf_counter() - t
    print(f"\nnearest sort of 10 000 segments: {elapsed:.2f} s (budget {budget_s():.2f} s)")
    assert elapsed < budget_s(), elapsed
    assert sorted(res.order) == list(range(10_000))
    circles: list[Graph] = [
        build_contour([CircleGlyph(Vec2((i % 100) * 4.0, (i // 100) * 4.0), 1.5)])
        for i in range(5_000)
    ]
    circles.append(
        build_contour(
            [
                LwPolylineGlyph(
                    1,
                    [
                        LwPolyVertex(Vec2(-5, -5)),
                        LwPolyVertex(Vec2(500, -5)),
                        LwPolyVertex(Vec2(500, 500)),
                        LwPolyVertex(Vec2(-5, 500)),
                    ],
                )
            ]
        )
    )
    t = time.perf_counter()
    depth, inside = sortmod.containment_depths(circles)
    elapsed = time.perf_counter() - t
    print(f"\ncontainment depth of 5 000 circles: {elapsed:.2f} s (budget {budget_s():.2f} s)")
    assert elapsed < budget_s(), elapsed
    assert depth[:-1] == [1] * 5_000 and len(inside[-1]) == 5_000


# ============================================================================ display flattening


def _long_spline() -> SplineGlyph:
    ctrl = [Vec2(0, 0), Vec2(300, 500), Vec2(600, -200), Vec2(900, 400)]
    return SplineGlyph(0, 0, ctrl, [0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0])


def _max_chord_error(poly: np.ndarray, g: SplineGlyph) -> float:
    u = np.linspace(0.0, 1.0, 400_001)
    exact = SciBSpline(np.asarray(g.knots), np.asarray(g.control_points), 3)(u)
    mids = (poly[1:] + poly[:-1]) / 2.0
    return float(cKDTree(exact).query(mids)[0].max())


def test_canvas_spline_flattening_honours_display_step() -> None:
    """Finding (fixed in ui/scene, then in model/flatten): spline samples ignored the step.

    Both used ``max(24, 8 * n_ctrl)`` samples, which drew a 1 m, 4-point spline
    with 0.51 mm chord error at the 0.2 mm display step (visible when zoomed).
    The evidence for that rule is ``tools/chf_parse.py``, the reference tool, which
    still carries it; ``model.flatten`` now scales the count with the
    control-polygon length over the step (STATUS X10), as ``ui.scene`` already did.
    """
    from nexcut.ui.scene import flatten_glyph_np

    g = _long_spline()
    tool = _chf_parse_module()
    fixed = np.asarray(
        tool._bspline_pts([list(p) for p in g.control_points], list(g.knots)),  # type: ignore[attr-defined]
        dtype=float,
    )
    assert len(fixed) == 8 * len(g.control_points) + 1  # the max(24, 8 * n_ctrl) rule
    assert _max_chord_error(fixed, g) > 0.4  # the sampling this replaced (evidence)
    coarse = np.asarray(flatten_glyph(g)[0])
    assert _max_chord_error(coarse, g) < 0.01
    (dense,) = flatten_glyph_np(g)
    assert _max_chord_error(dense, g) < 0.01
    assert tuple(dense[0]) == (0.0, 0.0) and tuple(dense[-1]) == pytest.approx((900.0, 400.0))
    small = SplineGlyph(0, 0, [Vec2(0, 0), Vec2(0.5, 1), Vec2(1, -0.5), Vec2(1.5, 0)], g.knots)
    np.testing.assert_array_equal(flatten_glyph_np(small)[0], np.asarray(flatten_glyph(small)[0]))


def test_cached_spline_length_is_accurate() -> None:
    """model/flatten: the cached length of a 1 m spline is within 0.05 mm (STATUS X10).

    It was 0.36 mm short, because ``_bspline_pts`` ignored the chord step even at
    ``CHECK_STEP`` 0.002.
    """
    g = _long_spline()
    u = np.linspace(0.0, 1.0, 400_001)
    exact = SciBSpline(np.asarray(g.knots), np.asarray(g.control_points), 3)(u)
    length = float(np.hypot(*np.diff(exact, axis=0).T).sum())
    assert abs(build_contour([g]).length - length) < 0.05


# ============================================================================ render metric / Y-up

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp() -> object:
    qtw = pytest.importorskip("PySide6.QtWidgets")
    return qtw.QApplication.instance() or qtw.QApplication([])


def _chf_parse_module() -> object:
    spec = importlib.util.spec_from_file_location("chf_parse_tool", REPO / "tools" / "chf_parse.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _asymmetric_document() -> ChfDocument:
    """An 'F' with an arc, a bulge and a spline: no mirror or rotation symmetry."""
    from nexcut.model.glyph import ArcGlyph, EllipseArcGlyph

    return ChfDocument(
        graphs=[
            build_contour(
                [
                    LwPolylineGlyph(
                        0,
                        [
                            LwPolyVertex(Vec2(0, 0)),
                            LwPolyVertex(Vec2(0, 100)),
                            LwPolyVertex(Vec2(60, 100)),
                        ],
                    )
                ]
            ),
            build_contour([SegmentGlyph(Vec2(0, 50), Vec2(40, 50))]),
            build_contour([ArcGlyph(Vec2(80, 20), 15, 0.0, 4.0)]),
            build_contour(
                [LwPolylineGlyph(0, [LwPolyVertex(Vec2(50, 70), 0.6), LwPolyVertex(Vec2(90, 70))])]
            ),
            build_contour(
                [
                    SplineGlyph(
                        0,
                        0,
                        [Vec2(20, 5), Vec2(30, 40), Vec2(45, -10), Vec2(55, 30)],
                        [0, 0, 0, 0, 1, 1, 1, 1.0],
                    )
                ]
            ),
            build_contour([EllipseArcGlyph(Vec2(90, 90), Vec2(10, 3), 0.5, 0.3, 2.2)]),
        ]
    )


def _png_mask(data: bytes) -> np.ndarray:
    from PySide6.QtGui import QImage

    from nexcut.ui.render import ink_mask

    img = QImage()
    assert img.loadFromData(data, "PNG")
    return ink_mask(img)


def test_render_gate_has_an_asymmetric_reference(qapp: object, tmp_path: Path) -> None:
    """Finding (fixed by this test): the M2 render gate could not see a mirrored frame.

    Evidence: every vendor sample is symmetric - 7 of 8 score the same IoU flipped
    in X or Y as unflipped, and ``File/autosave.chf`` (the gate's only negative
    control) is invariant under a 180-degree rotation - so a Y-down *and* X-mirrored
    canvas passed ``test_ui_render``.  Here an asymmetric document is rendered and
    compared with ``tools/chf_parse.py``'s own rasteriser; every mirror and the
    rotation fail, as do a flipped bulge sign and a complemented arc.
    """
    from nexcut.model.glyph import ArcGlyph
    from nexcut.ui.render import aligned_iou, chamfer_score, ink_mask, render_document

    tool = _chf_parse_module()
    doc = _asymmetric_document()
    ref = _png_mask(tool.to_png(tool.parse_chf(chf.write_chf(doc))))  # type: ignore[attr-defined]
    mask = ink_mask(render_document(doc))
    assert mask.shape == ref.shape
    assert chamfer_score(mask, ref) >= 0.99 and aligned_iou(mask, ref) >= 0.9
    for name, bad in (
        ("flipY", mask[::-1]),
        ("flipX", mask[:, ::-1]),
        ("rot180", mask[::-1, ::-1]),
    ):
        assert chamfer_score(bad, ref) < 0.5, name
        assert aligned_iou(bad, ref) < 0.5, name
    arc = doc.graphs[2].elements[0].glyph  # type: ignore[union-attr]
    assert isinstance(arc, ArcGlyph)
    wrong_arc = ArcGlyph(arc.center, arc.radius, arc.end_angle, arc.start_angle + 2 * math.pi)
    poly = doc.graphs[3].elements[0].glyph  # type: ignore[union-attr]
    assert isinstance(poly, LwPolylineGlyph)
    wrong_bulge = LwPolylineGlyph(0, [LwPolyVertex(v.pt, -v.bulge) for v in poly.vertices])
    for i, glyph in ((2, wrong_arc), (3, wrong_bulge)):
        graphs = list(doc.graphs)
        graphs[i] = build_contour([glyph])
        assert chamfer_score(ink_mask(render_document(ChfDocument(graphs=graphs))), ref) < 0.9


SAMPLE_RENDERS = {
    "File/autosave.chf": "File_autosave.png",
    "File/Temp/tempGraph.chf": "Temp_tempGraph.png",
    "Graph/Work1/1.chf": "Work1_1.png",
    "Graph/Work1/2.chf": "Work1_2.png",
    "Graph/Work1/3.chf": "Work1_3.png",
    "Graph/Work2/1.chf": "Work2_1.png",
    "Graph/Work2/2.chf": "Work2_2.png",
    "Graph/Work2/3.chf": "Work2_3.png",
}


def test_vendor_samples_pass_strict_metric(qapp: object, src_dir: Path) -> None:
    """Finding (fixed): ``aligned_iou >= 0.9`` accepts a render with a contour missing.

    Evidence: ``autosave.chf`` without one of its 24 segments scores 0.907 against
    ``File_autosave.png`` (correct renders score 0.944-1.0).  ``chamfer_score`` gives
    1.000 for all 8 correct renders, so a 0.99 gate has margin and rejects it (0.958).
    """
    from PySide6.QtGui import QImage

    from nexcut.ui.render import aligned_iou, chamfer_score, ink_mask, render_document

    for rel, png in SAMPLE_RENDERS.items():
        ref_path = REPO / "tools" / "out" / png
        if not ref_path.is_file():
            pytest.skip("reference renders missing")
        ref = ink_mask(QImage(str(ref_path)))
        mask = ink_mask(render_document(chf.load_chf(src_dir / rel)))
        assert chamfer_score(mask, ref) >= 0.99, rel
    doc = chf.load_chf(src_dir / "File/autosave.chf")
    ref = ink_mask(QImage(str(REPO / "tools" / "out" / "File_autosave.png")))
    missing = ChfDocument(version=doc.version, graphs=doc.graphs[:5] + doc.graphs[6:])
    mask = ink_mask(render_document(missing))
    assert aligned_iou(mask, ref) >= 0.9  # the M2 gate metric passes it (evidence)
    assert chamfer_score(mask, ref) < 0.99


def test_canvas_and_render_are_y_up(qapp: object) -> None:
    """A point at the top-left of the drawing is drawn top-left (03 §10 Y-up)."""
    from PySide6.QtCore import QPointF

    from nexcut.ui.canvas import CanvasView
    from nexcut.ui.render import ink_mask, render_document

    doc = ChfDocument(
        graphs=[
            build_contour([SegmentGlyph(Vec2(0, 0), Vec2(100, 0))]),
            build_contour([SegmentGlyph(Vec2(0, 0), Vec2(0, 60))]),
            build_contour([CircleGlyph(Vec2(5, 55), 4)]),
        ]
    )
    with_circle = ink_mask(render_document(doc))
    frame_only = ink_mask(render_document(ChfDocument(graphs=doc.graphs[:2])))
    assert with_circle.shape == frame_only.shape
    ys, xs = np.nonzero(with_circle & ~frame_only)
    h, w = with_circle.shape
    assert len(ys) > 20 and ys.mean() < 0.2 * h and xs.mean() < 0.2 * w
    view = CanvasView()
    view.resize(800, 600)
    view.set_document(doc)
    view.zoom_to_fit()
    top = view.map_from_scene_f(QPointF(0.0, 60.0))
    bottom = view.map_from_scene_f(QPointF(0.0, 0.0))
    right = view.map_from_scene_f(QPointF(100.0, 0.0))
    assert top.y() < bottom.y() and right.x() > bottom.x()
    view.close()


# ============================================================================ canvas performance

BUDGET_S = 3.0
"""PORT-PLAN §8.3: a 50 000-segment file opens, fits and paints in < 3 s."""


def budget_s(budget: float = BUDGET_S) -> float:
    """``budget`` scaled to how fast this machine is running right now.

    The 3 s came from the review laptop. A CI runner is slower and usually shares its cores,
    and the project's CI-stability rule is that a timing assertion must hold on a machine 3x
    slower; an unscaled wall-clock budget there measures the runner, not the port. See
    :func:`tests.conftest.speed_factor` - the factor is never below 1.0, so the gate stays
    exact on a machine of the review machine's speed. Every test below also prints the raw
    seconds, and ``docs/STATUS.md`` records the unscaled laptop numbers.
    """
    return budget * speed_factor()


def _wave(i: int) -> tuple[float, float]:
    return i * 0.05, 20.0 * math.sin(i * 0.002)


def _write_big(kind: str, n: int, tmp: Path) -> Path:
    if kind == "dxf-lwpolyline":  # minimal R2000 text, written directly (ezdxf saveas is slow)
        p = tmp / "big.dxf"
        pts = "".join(f" 10\n{x:.6f}\n 20\n{y:.6f}\n" for x, y in map(_wave, range(n + 1)))
        head = (
            "  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC1015\n  0\nENDSEC\n"
            "  0\nSECTION\n  2\nENTITIES\n  0\nLWPOLYLINE\n  5\n100\n100\nAcDbEntity\n  8\n0\n"
            f"100\nAcDbPolyline\n 90\n{n + 1}\n 70\n0\n"
        )
        p.write_text(head + pts + "  0\nENDSEC\n  0\nEOF\n")
    elif kind == "gcode":
        p = tmp / "big.nc"
        body = "\n".join(f"G1 X{x:.4f} Y{y:.4f}" for x, y in map(_wave, range(1, n + 1)))
        p.write_text("G0 X0 Y0\n" + body + "\nM02\n")
    elif kind == "plt":
        p = tmp / "big.plt"
        body = ",".join(f"{round(x * 40)},{round(y * 40)}" for x, y in map(_wave, range(1, n + 1)))
        p.write_text("IN;PU0,0;PD" + body + ";PU;")
    elif kind == "chf":
        p = tmp / "big.chf"
        glyphs = [SegmentGlyph(Vec2(*_wave(i)), Vec2(*_wave(i + 1))) for i in range(n)]
        chf.save_chf(ChfDocument(graphs=[build_contour(glyphs)]), p)
    elif kind == "dxf-lines":
        p = tmp / "lines.dxf"
        parts = ["0\nSECTION\n2\nENTITIES\n"]
        for g in _segment_grid(n):
            s, e = g.start, g.end  # type: ignore[union-attr]
            parts.append(
                f"0\nLINE\n8\n0\n10\n{s[0]}\n20\n{s[1]}\n30\n0\n11\n{e[0]}\n21\n{e[1]}\n31\n0\n"
            )
        parts.append("0\nENDSEC\n0\nEOF\n")
        p.write_text("".join(parts))
    elif kind == "chf-contours":
        p = tmp / "contours.chf"
        chf.save_chf(ChfDocument(graphs=_segment_grid(n)), p)
    else:  # pragma: no cover
        raise ValueError(kind)
    return p


def _open_fit_paint(qapp: object, path: Path, tmp: Path) -> tuple[float, int]:
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QImage, QPainter

    from nexcut.ui.i18n import LangCatalog
    from nexcut.ui.main_window import MainWindow

    win = MainWindow(
        settings=QSettings(str(tmp / "s.ini"), QSettings.Format.IniFormat),
        interactive=False,
        translator=Translator(LangCatalog()),
    )
    win.resize(1100, 760)
    win.show()
    qapp.processEvents()  # type: ignore[attr-defined]
    t = time.perf_counter()
    assert win.open_path(path)
    win.canvas.zoom_to_fit()
    img = QImage(win.canvas.viewport().size(), QImage.Format.Format_RGB32)
    painter = QPainter(img)
    win.canvas.render(painter)
    painter.end()
    elapsed = time.perf_counter() - t
    data = win.canvas.scene_data
    segs = 0 if data is None else len(data.seg_start)
    win.close()
    win.deleteLater()
    qapp.processEvents()  # type: ignore[attr-defined]
    return elapsed, segs


@pytest.mark.parametrize("kind", ["dxf-lwpolyline", "gcode", "plt", "chf"])
def test_open_and_fit_50k_segment_path(qapp: object, tmp_path: Path, kind: str) -> None:
    """A 50 000-segment path opens, fits and paints offscreen in < 3 s (measured 0.7-1.5 s)."""
    path = _write_big(kind, 50_000, tmp_path)
    elapsed, segs = _open_fit_paint(qapp, path, tmp_path)
    assert segs >= 50_000
    print(f"{kind}: open+fit+paint {elapsed:.2f} s (budget {budget_s():.2f} s)")
    assert elapsed < budget_s(), f"{kind}: {elapsed:.2f} s"


def test_canvas_50k_separate_segments(qapp: object) -> None:
    """Finding (fixed): 50 000 one-segment contours needed 5.5 s in the canvas alone.

    Evidence: build_scene 2.7 s (per-contour numpy calls), markers 2.05 s
    (per-contour QTransform.map + drawEllipse), batch paths 0.7 s.  Now ~0.9 s.
    """
    from PySide6.QtGui import QImage, QPainter

    from nexcut.ui.canvas import CanvasView
    from nexcut.ui.render import ViewFlags

    doc = ChfDocument(graphs=_segment_grid(50_000))
    view = CanvasView(flags=ViewFlags(show_start=True, show_arrows=True))
    view.resize(1000, 700)
    view.show()
    qapp.processEvents()  # type: ignore[attr-defined]
    t = time.perf_counter()
    data = view.set_document(doc)
    view.zoom_to_fit()
    img = QImage(view.viewport().size(), QImage.Format.Format_RGB32)
    painter = QPainter(img)
    view.render(painter)
    painter.end()
    elapsed = time.perf_counter() - t
    assert data is not None and len(data.seg_start) == 50_000
    print(f"canvas 50k separate segments: {elapsed:.2f} s (budget {budget_s():.2f} s)")
    assert elapsed < budget_s(), elapsed
    view.close()


def test_50k_separate_dxf_lines_import_is_no_longer_the_cost(tmp_path: Path) -> None:
    """X11, part 1 (closed): reading 50 000 separate DXF ``LINE`` entities.

    The ezdxf reader spent ~4.5 s of the ~8.2 s open building a
    :class:`ezdxf.document.Drawing` the importer then threw away.
    ``nexcut.io.dxf_stream`` walks the ENTITIES section tag by tag instead
    (STATUS §5 task 7); ``read_dxf`` now picks it automatically and says so.

    Measured 2026-09-16 on the owner's machine, idle, ``speed_factor`` 1.01 - i.e. at
    the speed the 3 s budgets were written for, cross-checked by the 50 k-contour
    ``.chf`` case reproducing its recorded 1.84-2.10 s: streaming read
    **0.57 / 0.63 / 0.64 s** against ezdxf **3.22 / 3.63 / 3.41 s**, a 5.3-5.8x cut,
    while the IGP clean-up over the resulting 50 000 one-segment contours costs
    **3.64-3.68 s** and is now the dominant term by far.

    The two assertions below are the durable ones: the parse fits several times over
    into a *one second* share of the budget, and it is smaller than the clean-up.
    """
    from nexcut.ops.import_gates import apply_import_gates

    path = _write_big("dxf-lines", 50_000, tmp_path)
    t = time.perf_counter()
    res = read_dxf(path)
    read_s = time.perf_counter() - t
    t = time.perf_counter()
    apply_import_gates(res.document)
    gates_s = time.perf_counter() - t
    assert res.reader == "stream" and res.fallback_reason == ""
    assert len(res.document.graphs) == 50_000
    print(
        f"50k separate DXF LINEs: read {read_s:.2f} s (stream), IGP gates {gates_s:.2f} s "
        f"(1 s share of the budget: {budget_s(1.0):.2f} s)"
    )
    assert read_s < budget_s(1.0), read_s
    assert read_s < gates_s, (read_s, gates_s)


@pytest.mark.xfail(
    strict=True,
    reason="X11 (still open, cause moved): 50 000 separate DXF LINE entities open, fit "
    "and paint in 5.04 / 5.42 / 5.03 s against the 3 s budget - was ~8.2 s.  The "
    "streaming DXF reader of STATUS §5 task 7 took the parse from 3.2-3.6 s to "
    "0.57-0.64 s (5.3-5.8x, test above), which is the whole of that 3.1 s; what is "
    "left is NOT the DXF reader: ops.import_gates + ops.sort over 50 000 one-segment "
    "contours 3.64-3.68 s (~70 %), the canvas build/fit/paint ~0.9 s (~18 %), the "
    "parse ~0.6 s (~12 %).  Measured 2026-09-16 on the owner's machine, idle, "
    "speed_factor 1.01, with the 50 k-contour .chf case reproducing its recorded "
    "1.84-2.10 s as the cross-check that the machine is at budget speed.  Closing it "
    "needs the IGP clean-up to get what the reader just had - the same vectorise-the-"
    "per-item-Python-path work as X13 - not more DXF work.",
)
def test_open_50k_separate_dxf_lines(qapp: object, tmp_path: Path) -> None:
    path = _write_big("dxf-lines", 50_000, tmp_path)
    elapsed, _ = _open_fit_paint(qapp, path, tmp_path)
    print(f"50k separate DXF LINEs: {elapsed:.2f} s (budget {budget_s():.2f} s)")
    assert elapsed < budget_s(), elapsed


def test_open_50k_contour_chf(qapp: object, tmp_path: Path) -> None:
    """50 000 one-segment contours open, fit and paint in < 3 s (STATUS X12).

    The token reader needed ~7 s of that alone (1.95 M per-byte ``tok()`` calls);
    it now tokenises the buffer in one pass and memoises decoded values, which
    brings the read to ~0.9 s and the whole path to ~1.9 s on the review machine.
    """
    path = _write_big("chf-contours", 50_000, tmp_path)
    elapsed, _ = _open_fit_paint(qapp, path, tmp_path)
    print(f"50k-contour .chf: {elapsed:.2f} s (budget {budget_s():.2f} s)")
    assert elapsed < budget_s(), elapsed


# ============================================================================ i18n (06 §1.5)


def _utf16(lines: list[str]) -> bytes:
    return "\r\n".join(lines).encode("utf-16")


MASTER = [
    "mf7#文件#File",
    "mf248#升级成功#Upgrade OK",
    "mf249#自动调焦#Auto Focus",
    "mp19#系统恢复完成#System recovery is done",
    "lp17#图层%d#Layer %d",
    "mf439#成功#Success",
    "ab2#系统#SC laser system",
    "pd604#冷却点#Cool Point",
    "ec12#PWM#PWM",
    "gp100#调焦#Focus",
]


def test_translation_file_defects_are_tolerated() -> None:
    """Finding (fixed): secondary language files were unusable.

    Evidence: ``parse_lang_txt`` (the only loader) expects ``ID#zh#en``; on the shipped
    ``French.txt`` (``ID#text``, Readme) it keeps 5 of 3 284 records - exactly the
    3-field defects - so every non-zh/en UI would show raw ids.  PORT-PLAN §3
    requires a tolerant parser for the 06 §1.5 defects.
    """
    master = parse_lang_txt(_utf16(MASTER))
    data = _utf16(
        [
            "mf7#Fichier",
            "",
            "mf248#Nâng cấp thành công phần mềmmf249#Tự động lấy tiêu cự ",  # lost CRLF
            "mf249#Lấy nét điện",  # standalone record wins over the recovered fragment
            "mp19#System recovery is done#Odzyskiwanie systemu",  # English + translation
            "lp17##Couche %d",  # empty second field
            "ec12#PWM#PWM",
            "mf439#Atualizou # Por favor reinicie",  # genuine '#' in the text
            "pd604Cool Point.Cool Lead Position",  # lost '#'
            "ab2 #SC lazer kesim sistemi",  # id with trailing blank
            "gp100#Focus A\npd999#obsolete",  # lone LF
            "gp100#Focus B",  # duplicate: first wins
            "mv24, ,##",
        ]
    )
    assert len(parse_lang_txt(data)) == 6  # the old loader: only lines with two "#"
    cat = parse_translation_txt(data, master)
    assert cat.entries["mf7"] == "Fichier"
    assert cat.entries["mf248"] == "Nâng cấp thành công phần mềm"
    assert cat.entries["mf249"] == "Lấy nét điện"
    assert cat.entries["mp19"] == "Odzyskiwanie systemu"
    assert cat.entries["lp17"] == "Couche %d"
    assert cat.entries["ec12"] == "PWM"
    assert cat.entries["mf439"] == "Atualizou # Por favor reinicie"
    assert "pd604" not in cat.entries
    assert cat.entries["ab2"] == "SC lazer kesim sistemi"
    assert cat.entries["gp100"] == "Focus A" and cat.duplicates == ["gp100"]
    assert cat.unknown_ids == ["pd999", "mv24, ,"]
    kinds = sorted({d.kind for d in cat.defects})
    assert kinds == ["id-whitespace", "merged", "no-separator", "three-field"]
    t = Translator(master, "en", cat)
    assert t.tr("mf7") == "Fichier"
    assert t.tr("pd604") == "Cool Point"  # English column fallback (UNVERIFIED port choice)
    assert t.tr("zz", "dflt") == "dflt" and t.tr("zz") == "zz"
    from nexcut.ui.app import build_parser

    assert build_parser().parse_args(["--lang", "Vietnamese"]).lang == "Vietnamese"


def test_vendor_translation_files(
    src_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unique record counts agree with the 06 §1.5 table (defective lines excluded)."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    lang = src_dir / "Lang"
    master = parse_lang_txt((lang / "lang.txt").read_bytes())
    golden = {
        "Russian.txt": 3283,
        "German.txt": 3275,
        "Spanish.txt": 3266,
        "Portuguese.txt": 3279,  # 06: 3 280 incl. the record without '#'
        "French.txt": 3284,
        "Italian.txt": 3280,  # 06: 3 281 incl. 'pd604Cool Point...'
        "Polish.txt": 3283,
        "Vietnamese.txt": 3279,
        "Turkdili.txt": 3276,
    }
    assert set(golden) == set(LANGUAGE_FILES.values())
    for name, count in golden.items():
        data = (lang / name).read_bytes()
        assert len(parse_lang_txt(data)) < 10  # the old loader: ids everywhere
        cat = parse_translation_txt(data, master)
        assert len(cat) == count, name
        assert "pd640_4th" in cat.duplicates, name
    viet = parse_translation_txt((lang / "Vietnamese.txt").read_bytes(), master)
    assert sum(d.kind == "merged" for d in viet.defects) == 4
    assert viet.entries["mf248"].endswith("phần mềm")
    assert {"Large", "multi-select)"} <= set(viet.unknown_ids)
    pl = Translator.for_language(lang, "Polish")
    assert pl.tr("mp19") == "Odzyskiwanie systemu zostalo zakonczone"
    fr = Translator.for_language(lang, "French")
    assert fr.tr("lp17").startswith("Layer %d 【Hauteur de coupe】")
    assert fr.tr("A250616_0") == "Reverse clearance compensation"  # 2025 id: English fallback
    assert Translator.for_language(lang, "简体中文").tr("gp81") == "背景图层"
    with pytest.raises(ValueError):
        Translator.for_language(lang, "Klingon")
