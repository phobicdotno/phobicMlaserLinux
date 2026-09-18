"""The streaming DXF reader against the ezdxf reader (STATUS §5 task 7, strict xfail X11).

:mod:`nexcut.io.dxf_stream` parses the ``ENTITIES`` section tag by tag and never
builds an ezdxf document; :mod:`nexcut.io.dxf` keeps the ezdxf reader as the
complete path.  The two are only allowed to exist side by side because they agree,
so every test below reads the same bytes twice - once with ``reader="stream"``,
once with ``reader="ezdxf"`` - and compares the glyph model, the per-entity layer
assignment and the counters.

Agreement tolerance: **zero**.  Both readers feed the same decoded ``float``
values into the same pure geometry helpers of :mod:`nexcut.io.dxf`
(:func:`~nexcut.io.dxf.arc_glyph`, :func:`~nexcut.io.dxf.ellipse_glyph`,
:func:`~nexcut.io.dxf.lwpoly_glyph`, :func:`~nexcut.io.dxf.solid_glyph`,
:func:`~nexcut.io.dxf.spline_glyphs`), so the resulting :class:`ChfDocument`
objects compare equal field by field, not merely within a geometric tolerance.
:func:`test_vendor_dxf_stream_curve_distance` states the geometric tolerance the
zero-difference claim implies (0 mm chordal distance on the vendor drawing).
"""

from __future__ import annotations

import io
import math
import random
from pathlib import Path

import ezdxf
import numpy as np
import pytest
from ezdxf.math import BSpline

from nexcut.io import dxf as dxfmod
from nexcut.io import dxf_stream
from nexcut.io.dxf import (
    SUPPORTED_TYPES,
    DxfImportError,
    DxfImportOptions,
    DxfImportResult,
    read_dxf,
)
from nexcut.model.flatten import flatten_glyph
from nexcut.model.glyph import SplineGlyph
from nexcut.model.graph import iter_contours

VENDOR_DXF_NAME = "Testfile SS1mm 2.0s F+1 N2.dxf"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _both(data: bytes, **opts: object) -> tuple[DxfImportResult, DxfImportResult]:
    """``(ezdxf result, stream result)`` for the same bytes and options."""
    options = DxfImportOptions(**opts)  # type: ignore[arg-type]
    return (
        read_dxf(data, options, reader="ezdxf"),
        read_dxf(data, options, reader="stream"),
    )


def _assert_same(a: DxfImportResult, b: DxfImportResult) -> None:
    """The two readers produced the same model, the same layers and the same counters."""
    assert b.reader == "stream" and a.reader == "ezdxf"
    assert len(a.document.graphs) == len(b.document.graphs)
    for i, (ga, gb) in enumerate(zip(a.document.graphs, b.document.graphs, strict=True)):
        assert ga == gb, f"graph {i}"
    assert a.document == b.document
    assert a.entity_counts == b.entity_counts
    assert a.imported_counts == b.imported_counts
    assert a.ignored_counts == b.ignored_counts
    assert a.entity_layers == b.entity_layers
    # ezdxf fills in the standard layers of its own R12 template for a file that has
    # none; the streaming reader reports the file's LAYER records, in file order.
    assert b.layers == [n for n in a.layers if n in set(b.layers)]
    assert set(a.layers) - set(b.layers) <= {"0", "Defpoints"}
    assert sorted(a.warnings) == sorted(b.warnings)
    assert a.encoding == b.encoding
    assert (a.acadver, a.codepage, a.insunits) == (b.acadver, b.codepage, b.insunits)
    assert [c.layer for g in a.document.graphs for c, _ in iter_contours(g)] == [
        c.layer for g in b.document.graphs for c, _ in iter_contours(g)
    ]


def _dxf_bytes(dwg: ezdxf.document.Drawing, encoding: str = "cp1252") -> bytes:
    buf = io.StringIO()
    dwg.write(buf)
    return buf.getvalue().encode(encoding, "replace")


def _pts(doc: object) -> np.ndarray:
    """Every contour of a document flattened to points (splines via ezdxf's evaluator)."""
    out: list[tuple[float, float]] = []
    for g in doc.graphs:  # type: ignore[attr-defined]
        for c, _ in iter_contours(g):
            for el in c.elements:
                gl = el.glyph
                if isinstance(gl, SplineGlyph):
                    bs = BSpline([(p[0], p[1]) for p in gl.control_points], order=4, knots=gl.knots)
                    lo, hi = gl.knots[3], gl.knots[len(gl.control_points)]
                    out.extend((v.x, v.y) for v in bs.points(np.linspace(lo, hi, 400)))
                else:
                    out.extend(p for part in flatten_glyph(gl, 0.01) for p in part)
    return np.asarray(out)


# ---------------------------------------------------------------------------
# the vendor drawing
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def vendor_dxf(src_dir: Path) -> Path:
    p = src_dir.parent / VENDOR_DXF_NAME
    if not p.is_file():
        pytest.skip(f"vendor test DXF not available at {p}")
    return p


def test_vendor_dxf_readers_agree(vendor_dxf: Path) -> None:
    """The ANSI_936 / AC1015 vendor drawing reads identically on both paths."""
    a, b = _both(vendor_dxf.read_bytes())
    _assert_same(a, b)
    assert (b.acadver, b.codepage, b.encoding, b.insunits) == ("AC1015", "ANSI_936", "cp936", 1)
    assert b.layers == ["0", "ар 1", "图层1", "Defpoints"]
    assert b.entity_layers == {"图层1": 9}
    assert dict(b.imported_counts) == {"ARC": 4, "SPLINE": 3, "CIRCLE": 1, "LWPOLYLINE": 1}


def test_vendor_dxf_stream_curve_distance(vendor_dxf: Path) -> None:
    """The stated geometric tolerance: the two readers' curves coincide exactly (0 mm)."""
    a, b = _both(vendor_dxf.read_bytes())
    pa, pb = _pts(a.document), _pts(b.document)
    assert pa.shape == pb.shape
    assert float(np.abs(pa - pb).max()) == 0.0


def test_vendor_dxf_is_read_by_the_fast_path(vendor_dxf: Path) -> None:
    """``reader="auto"`` (what the UI loader uses) picks the streaming reader for it."""
    res = read_dxf(vendor_dxf)
    assert res.reader == "stream" and res.fallback_reason == ""


# ---------------------------------------------------------------------------
# the whole supported entity set, per DXF version
# ---------------------------------------------------------------------------


def _zoo(msp: object, version: str) -> None:
    """Every entity the importer supports that the given DXF version can hold."""
    m = msp
    m.add_line((1, 2), (5, -3))  # type: ignore[attr-defined]
    m.add_line((0, 0), (1, 0), dxfattribs={"layer": "L2", "color": 5})  # type: ignore[attr-defined]
    m.add_arc((5, 5), 2, 30, 300)  # type: ignore[attr-defined]
    m.add_arc((5, 5), 2, 300, 30)  # type: ignore[attr-defined]
    m.add_arc((5, 5), 2, 30, 120, dxfattribs={"extrusion": (0, 0, -1)})  # type: ignore[attr-defined]
    m.add_circle((1, 1), 3)  # type: ignore[attr-defined]
    m.add_circle((4, 1), 3, dxfattribs={"extrusion": (0, 0, -1)})  # type: ignore[attr-defined]
    m.add_point((7, 8))  # type: ignore[attr-defined]
    m.add_solid([(0, 0), (1, 0), (0, 1), (1, 1)])  # type: ignore[attr-defined]
    m.add_trace([(0, 0), (1, 0), (0, 1), (1, 1)])  # type: ignore[attr-defined]
    m.add_3dface([(0, 0), (1, 0), (0, 1)])  # type: ignore[attr-defined]
    m.add_polyline2d(  # type: ignore[attr-defined]
        [(0, 0, 0, 0, 1.0), (1, 1, 0, 0, 0), (2, 0)], format="xyseb"
    )
    m.add_polyline2d(  # type: ignore[attr-defined]
        [(0, 0, 0, 0, 0.4), (3, 0, 0, 0, 0)],
        format="xyseb",
        close=True,
        dxfattribs={"extrusion": (0, 0, -1)},
    )
    m.add_polyline3d([(0, 0, 1), (1, 1, 2), (2, 0, 3)])  # type: ignore[attr-defined]
    m.add_polymesh(size=(2, 2))  # type: ignore[attr-defined]
    if version == "R12":
        return
    m.add_ellipse(  # type: ignore[attr-defined]
        (20, 20), major_axis=(4, 1, 0), ratio=0.5, start_param=0.3, end_param=2.0
    )
    m.add_ellipse(  # type: ignore[attr-defined]
        (20, 20),
        major_axis=(4, 1, 0),
        ratio=0.5,
        start_param=0.3,
        end_param=2.0,
        dxfattribs={"extrusion": (0, 0, -1)},
    )
    m.add_lwpolyline(  # type: ignore[attr-defined]
        [(0, 0, 0, 0, 0.5), (5, 0, 0, 0, -0.3), (5, 5, 0, 0, 0)], format="xyseb", close=True
    )
    m.add_lwpolyline(  # type: ignore[attr-defined]
        [(0, 0, 0, 0, 0.5), (5, 0, 0, 0, -0.3), (5, 5, 0, 0, 0)],
        format="xyseb",
        dxfattribs={"extrusion": (0, 0, -1)},
    )
    m.add_open_spline([(0, 0), (1, 2), (3, 3), (4, 0), (6, 1)])  # type: ignore[attr-defined]
    m.add_spline(fit_points=[(0, 0), (1, 2), (3, 3), (4, 0)])  # type: ignore[attr-defined]
    m.add_open_spline([(0, 0), (1, 2), (3, 3), (4, 0)], degree=2)  # type: ignore[attr-defined]
    m.add_rational_spline(  # type: ignore[attr-defined]
        [(0, 0), (1, 2), (3, 3), (4, 0)], weights=[1, 3, 1, 1], degree=3
    )
    m.add_xline((0, 0), (1, 1))  # type: ignore[attr-defined]
    m.add_ray((0, 0), (1, 1))  # type: ignore[attr-defined]
    hatch = m.add_hatch()  # type: ignore[attr-defined]
    hatch.paths.add_polyline_path([(0, 0), (1, 0), (1, 1)])
    m.add_linear_dim(base=(0, 3), p1=(0, 0), p2=(3, 0)).render()  # type: ignore[attr-defined]
    m.add_leader([(0, 0), (1, 1)])  # type: ignore[attr-defined]


def _zoo_bytes(version: str) -> bytes:
    dwg = ezdxf.new(version)
    dwg.layers.add("L2", color=5)
    _zoo(dwg.modelspace(), version)
    dwg.layout().add_circle((90, 90), 5)  # the first paper-space layout
    return _dxf_bytes(dwg, "utf-8" if dwg.dxfversion >= "AC1021" else "cp1252")


@pytest.mark.parametrize("version", ["R12", "R2000", "R2018"])
@pytest.mark.parametrize("read_color", [False, True])
def test_entity_zoo_readers_agree(version: str, read_color: bool) -> None:
    """Every supported entity, both extrusion signs, both ``IGP.EnableReadGraphColor`` states."""
    a, b = _both(_zoo_bytes(version), read_color=read_color)
    _assert_same(a, b)
    assert b.imported_counts
    # HATCH/DIMENSION/LEADER/XLINE/RAY only exist from R2000 on
    assert bool(b.ignored_counts) == (version != "R12")


@pytest.mark.parametrize("version", ["R2000", "R2018"])
def test_paper_space_entities_reach_neither_reader(version: str) -> None:
    """The paper-space CIRCLE at (90, 90) is in ENTITIES but not in the modelspace."""
    data = _zoo_bytes(version)
    assert b"\n90.0\n" in data or b"90.0" in data
    a, b = _both(data)
    _assert_same(a, b)
    boxes = [c.bbox_max for g in b.document.graphs for c, _ in iter_contours(g)]
    assert boxes and max(p[0] for p in boxes) < 80.0


def test_3dface_is_imported_by_both_readers() -> None:
    """Finding (fixed): ``3DFACE`` never reached ``_solid``.

    ``_Converter.convert`` derived the handler name as ``"_" + t.lower().replace("3d", "d3")``
    = ``_d3face`` while the alias was spelled ``_d3dface``, so ``getattr`` returned None and
    the ``assert handler is not None`` fired - a bare ``AssertionError`` out of ``import_dxf``,
    which ``MainWindow.open_path`` does not catch.  ``_Converter.HANDLERS`` is now a table.
    """
    dwg = ezdxf.new("R2000")
    dwg.modelspace().add_3dface([(0, 0), (2, 0), (2, 1), (0, 1)])
    a, b = _both(_dxf_bytes(dwg))
    _assert_same(a, b)
    assert a.imported_counts["3DFACE"] == 1
    assert [tuple(v.pt) for v in a.document.graphs[0].elements[0].glyph.vertices] == [  # type: ignore[union-attr]
        (0, 0),
        (2, 0),
        (2, 1),
        (0, 1),
    ]


def test_single_vertex_open_polyline_is_dropped_by_both_readers() -> None:
    """Finding (fixed): an open one-vertex LWPOLYLINE/POLYLINE raised IndexError.

    ``glyph_extent`` has no closed form for a polyline with no span, so
    ``refresh_contour`` fell through to ``measure_contour``, whose flattening of that
    glyph is empty, and ``import_dxf`` raised ``IndexError`` - which
    ``MainWindow.open_path`` does not catch (it catches ``LoadError`` only).  Both
    readers now drop the entity and warn; a *closed* one-vertex polyline, the
    zero-length loop the model does measure, is still imported.
    """
    dwg = ezdxf.new("R2000")
    msp = dwg.modelspace()
    msp.add_line((0, 0), (1, 1))
    msp.add_lwpolyline([(1, 2)], format="xy")
    msp.add_polyline2d([(3, 4)])
    a, b = _both(_dxf_bytes(dwg))
    _assert_same(a, b)
    assert len(b.document.graphs) == 1
    assert sum("single vertex" in w for w in b.warnings) == 2

    closed = ezdxf.new("R2000")
    closed.modelspace().add_lwpolyline([(1, 2)], format="xy", close=True)
    ca, cb = _both(_dxf_bytes(closed))
    _assert_same(ca, cb)
    assert len(cb.document.graphs) == 1 and cb.document.graphs[0].length == 0.0


def test_handler_table_covers_every_supported_type() -> None:
    """The dispatch table and ``SUPPORTED_TYPES`` cannot drift apart again."""
    handlers = dxfmod._Converter.HANDLERS
    assert set(handlers) == set(SUPPORTED_TYPES)
    for name in set(handlers.values()):
        assert callable(getattr(dxfmod._Converter, name))
    # the streaming reader either converts a supported type or hands the file to ezdxf
    assert dxf_stream.STREAM_TYPES | dxf_stream._DELEGATED_TYPES | dxf_stream._TEXT_TYPES == set(
        SUPPORTED_TYPES
    )
    assert not dxf_stream.STREAM_TYPES & (dxf_stream._DELEGATED_TYPES | dxf_stream._TEXT_TYPES)


# ---------------------------------------------------------------------------
# randomised cross-check
# ---------------------------------------------------------------------------


def _random_r12_dxf(rnd: random.Random, count: int) -> bytes:
    """A hand-written AC1009 DXF of random R12 entities - not produced by ezdxf's writer.

    R12 has no ``100`` subclass markers and no handles, so this also covers the
    older-file shape the ``$ACADVER``/owner-handle logic has to survive.
    """
    layers = ["0", "cut", "\\U+0430\\U+0440 1", "图层1"]
    out = [
        "  0\nSECTION\n  2\nHEADER\n",
        "  9\n$ACADVER\n  1\nAC1009\n  9\n$DWGCODEPAGE\n  3\nANSI_936\n  9\n$INSUNITS\n 70\n4\n",
        "  0\nENDSEC\n  0\nSECTION\n  2\nTABLES\n  0\nTABLE\n  2\nLAYER\n 70\n4\n",
    ]
    for i, name in enumerate(layers):
        out.append(f"  0\nLAYER\n  2\n{name}\n 70\n0\n 62\n{i * 3 + 1}\n  6\nCONTINUOUS\n")
    out.append("  0\nENDTAB\n  0\nENDSEC\n  0\nSECTION\n  2\nENTITIES\n")

    def xy(base: int, p: tuple[float, float]) -> str:
        return f"{base:3d}\n{p[0]:.6f}\n{base + 10:3d}\n{p[1]:.6f}\n{base + 20:3d}\n0.0\n"

    def pt() -> tuple[float, float]:
        return (rnd.uniform(-200, 200), rnd.uniform(-200, 200))

    for _ in range(count):
        layer = rnd.choice(layers)
        color = rnd.choice(["", " 62\n1\n", " 62\n7\n", " 62\n256\n", " 62\n-3\n"])
        ext = rnd.choice(["", "210\n0.0\n220\n0.0\n230\n-1.0\n", "210\n0.0\n220\n0.0\n230\n1.0\n"])
        common = f"  8\n{layer}\n{color}"
        kind = rnd.choice(
            ["LINE", "LINE", "POINT", "ARC", "CIRCLE", "POLYLINE", "SOLID", "TRACE", "3DFACE"]
        )
        if kind == "LINE":
            out.append(f"  0\nLINE\n{common}{xy(10, pt())}{xy(11, pt())}")
        elif kind == "POINT":
            out.append(f"  0\nPOINT\n{common}{xy(10, pt())}")
        elif kind == "ARC":
            out.append(
                f"  0\nARC\n{common}{xy(10, pt())} 40\n{rnd.uniform(0.5, 40):.6f}\n{ext}"
                f" 50\n{rnd.uniform(0, 360):.4f}\n 51\n{rnd.uniform(0, 360):.4f}\n"
            )
        elif kind == "CIRCLE":
            out.append(f"  0\nCIRCLE\n{common}{xy(10, pt())} 40\n{rnd.uniform(0.5, 40):.6f}\n{ext}")
        elif kind == "POLYLINE":
            n = rnd.randint(2, 5)
            flags = rnd.choice([0, 1, 8, 9])
            body = "".join(
                f"  0\nVERTEX\n  8\n{layer}\n{xy(10, pv)}"
                f" 42\n{rnd.choice([0.0, 0.6, -0.2]):.5f}\n 70\n{rnd.choice([0, 0, 16])}\n"
                for pv in (pt() for _ in range(n))
            )
            out.append(
                f"  0\nPOLYLINE\n{common} 66\n1\n{xy(10, (0.0, 0.0))} 70\n{flags}\n{ext}"
                f"{body}  0\nSEQEND\n  8\n{layer}\n"
            )
        else:
            corners = "".join(xy(10 + k, pt()) for k in range(4))
            out.append(f"  0\n{kind}\n{common}{corners}{ext}")
    out.append("  0\nENDSEC\n  0\nEOF\n")
    return "".join(out).encode("cp936")


def _random_r2000_dxf(rnd: random.Random, count: int) -> bytes:
    """Random LWPOLYLINE/ELLIPSE/SPLINE/ARC entities written by ezdxf (R2000 with subclasses)."""
    dwg = ezdxf.new("R2000")
    for i, name in enumerate(("cut", "mark", "图层1")):
        dwg.layers.add(name, color=i * 5 + 2)
    msp = dwg.modelspace()

    def pt() -> tuple[float, float]:
        return (rnd.uniform(-200, 200), rnd.uniform(-200, 200))

    for _ in range(count):
        attribs = {
            "layer": rnd.choice(["0", "cut", "mark", "图层1"]),
            "color": rnd.choice([1, 7, 256, 44]),
            "extrusion": rnd.choice([(0, 0, 1), (0, 0, -1)]),
        }
        kind = rnd.choice(["lwpolyline", "ellipse", "spline", "arc", "line", "solid"])
        if kind == "lwpolyline":
            pts = [
                (*pt(), 0.0, 0.0, rnd.choice([0.0, 0.8, -0.5, 1.0]))
                for _ in range(rnd.randint(2, 6))
            ]
            msp.add_lwpolyline(
                pts, format="xyseb", close=bool(rnd.getrandbits(1)), dxfattribs=attribs
            )
        elif kind == "ellipse":
            msp.add_ellipse(
                pt(),
                major_axis=(rnd.uniform(1, 9), rnd.uniform(-9, 9), 0),
                ratio=rnd.uniform(0.05, 1.0),
                start_param=rnd.uniform(0, 3),
                end_param=rnd.uniform(3, 6),
                dxfattribs=attribs,
            )
        elif kind == "spline":
            n = rnd.randint(4, 7)
            if rnd.getrandbits(1):
                msp.add_open_spline(
                    [pt() for _ in range(n)], degree=rnd.choice([2, 3]), dxfattribs=attribs
                )
            else:
                msp.add_rational_spline(
                    [pt() for _ in range(n)],
                    weights=[rnd.uniform(0.5, 3) for _ in range(n)],
                    degree=3,
                    dxfattribs=attribs,
                )
        elif kind == "arc":
            msp.add_arc(
                pt(),
                rnd.uniform(0.5, 40),
                rnd.uniform(0, 360),
                rnd.uniform(0, 360),
                dxfattribs=attribs,
            )
        elif kind == "line":
            msp.add_line(pt(), pt(), dxfattribs=attribs)
        else:
            msp.add_solid([pt() for _ in range(4)], dxfattribs=attribs)
    return _dxf_bytes(dwg)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6, 7, 8])
def test_random_r12_dxf_readers_agree(seed: int) -> None:
    """Property: on a random hand-written R12 DXF the two readers build the same model."""
    data = _random_r12_dxf(random.Random(20260916 + seed), 60)
    a, b = _both(data, read_color=bool(seed % 2))
    _assert_same(a, b)
    assert len(b.document.graphs) > 20


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_random_r2000_dxf_readers_agree(seed: int) -> None:
    """Property: random bulge polylines, ellipses and splines read identically both ways."""
    data = _random_r2000_dxf(random.Random(770 + seed), 45)
    a, b = _both(data, read_color=bool(seed % 2))
    _assert_same(a, b)
    assert len(b.document.graphs) >= 40


def test_random_dxf_line_fast_path_matches_the_generic_tag_path() -> None:
    """``_StreamReader._line_entity`` is a shortcut, not a second implementation."""
    for data in (
        _random_r12_dxf(random.Random(4242), 120),
        _random_r2000_dxf(random.Random(9), 60),
    ):
        for opts in (DxfImportOptions(), DxfImportOptions(read_color=True)):
            fast = dxf_stream.read_stream(data, opts)
            generic = dxf_stream.read_stream(data, opts, fast_line=False)
            assert fast.document == generic.document
            assert fast.entity_counts == generic.entity_counts
            assert fast.entity_layers == generic.entity_layers
            assert fast.imported_counts == generic.imported_counts


# ---------------------------------------------------------------------------
# what the fast path declines
# ---------------------------------------------------------------------------


def _insert_bytes() -> bytes:
    dwg = ezdxf.new("R2000")
    blk = dwg.blocks.new("B1")
    blk.add_line((0, 0), (1, 0))
    blk.add_circle((0, 0), 1)
    dwg.modelspace().add_blockref("B1", (100, 100), dxfattribs={"xscale": 2, "rotation": 90})
    return _dxf_bytes(dwg)


def _text_bytes() -> bytes:
    dwg = ezdxf.new("R2000")
    dwg.modelspace().add_line((0, 0), (1, 1))
    dwg.modelspace().add_text("Hi", dxfattribs={"height": 5, "insert": (30, 30)})
    return _dxf_bytes(dwg)


def _tilted_bytes() -> bytes:
    dwg = ezdxf.new("R2000")
    dwg.modelspace().add_circle((1, 1), 3, dxfattribs={"extrusion": (0.3, 0.4, 0.86)})
    return _dxf_bytes(dwg)


def _binary_bytes(tmp_path: Path) -> bytes:
    dwg = ezdxf.new("R2000")
    dwg.modelspace().add_line((0, 0), (4, 4))
    p = tmp_path / "bin.dxf"
    dwg.saveas(p, fmt="bin")
    return p.read_bytes()


DECLINED = {
    "insert": ("block table", _insert_bytes),
    "text": ("text-to-curves", _text_bytes),
    "tilted": ("tilted extrusion", _tilted_bytes),
}


@pytest.mark.parametrize("case", sorted(DECLINED))
def test_declined_files_fall_back_to_ezdxf(case: str) -> None:
    """``reader="auto"`` reads them through ezdxf and records why."""
    fragment, build = DECLINED[case]
    data = build()
    res = read_dxf(data)
    assert res.reader == "ezdxf"
    assert fragment in res.fallback_reason
    assert res.document.graphs
    with pytest.raises(DxfImportError, match="streaming reader declined"):
        read_dxf(data, reader="stream")


def test_binary_dxf_falls_back(tmp_path: Path) -> None:
    data = _binary_bytes(tmp_path)
    res = read_dxf(data)
    assert res.reader == "ezdxf" and res.fallback_reason == "binary DXF"
    assert res.encoding == "binary" and len(res.document.graphs) == 1


def test_text_without_curves_stays_on_the_fast_path() -> None:
    """With ``pd374`` off TEXT is only counted and warned about, which the fast path can do."""
    a, b = _both(_text_bytes(), text_to_curves=False)
    _assert_same(a, b)
    assert b.reader == "stream"
    assert b.ignored_counts["TEXT"] == 1
    assert any("pd374" in w for w in b.warnings)


def test_paper_space_layout_falls_back() -> None:
    dwg = ezdxf.new("R2000")
    dwg.layout("Layout1").add_line((0, 0), (1, 1))
    res = read_dxf(_dxf_bytes(dwg), DxfImportOptions(layout="Layout1"))
    assert res.reader == "ezdxf" and "modelspace" in res.fallback_reason
    assert len(res.document.graphs) == 1


@pytest.mark.parametrize(
    "text",
    [
        "  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC1015\n  0\nENDSEC\n  0\nEOF\n",
        "  0\nSECTION\n  2\nENTITIES\n  0\nLINE\n  8\n0\n 10\n0\n 11\n1\n  0\nENDSEC\n  0\nEOF\n",
        "  0\nSECTION\n  2\nENTITIES\n  0\nLINE\n  x\n0\n  0\nENDSEC\n  0\nEOF\n",
        "  0\nSECTION\n  2\nENTITIES\n  0\nLINE\n  8\n0\n 10\n0\n 20\n0\n",
        "  0\nSECTION\n  2\nENTITIES\n  0\nVERTEX\n  8\n0\n  0\nENDSEC\n  0\nEOF\n",
        "  0\nSECTION\n  2\nWHATEVER\n  0\nENDSEC\n  0\nEOF\n",
    ],
    ids=[
        "no-entities",
        "y-partner-missing",
        "bad-code",
        "truncated",
        "stray-vertex",
        "unknown-section",
    ],
)
def test_structural_anomalies_are_declined_not_guessed(text: str) -> None:
    """Anything the parser cannot vouch for goes to ezdxf, which reads or rejects it."""
    with pytest.raises(dxf_stream.StreamUnsupported):
        dxf_stream.read_stream(text.encode("cp1252"), DxfImportOptions())


def test_integer_overflow_in_the_header_still_raises(tmp_path: Path) -> None:
    """X-review regression: ``$ACADMAINTVER -1e999`` must stay a DxfImportError, not a fast read.

    The fast path validates every HEADER and TABLES group value with ezdxf's own
    ``TYPE_TABLE``, declines, and lets the ezdxf reader produce the error.
    """
    dwg = ezdxf.new("R2000")
    dwg.modelspace().add_line((0, 0), (1, 1))
    text = _dxf_bytes(dwg).decode("cp1252").replace("\n 70\n6\n", "\n 70\n-1e999\n", 1)
    with pytest.raises(DxfImportError):
        read_dxf(text.encode("cp1252"))


def test_read_color_with_a_missing_layer_falls_back() -> None:
    """``IGP.EnableReadGraphColor`` resolves BYLAYER through the LAYER table, or declines."""
    text = (
        "  0\nSECTION\n  2\nENTITIES\n"
        "  0\nLINE\n  8\nghost\n 10\n0\n 20\n0\n 30\n0\n 11\n1\n 21\n1\n 31\n0\n"
        "  0\nENDSEC\n  0\nEOF\n"
    )
    res = read_dxf(text.encode("cp1252"), DxfImportOptions(read_color=True))
    assert res.reader == "ezdxf" and "LAYER table" in res.fallback_reason


# ---------------------------------------------------------------------------
# damaged input
# ---------------------------------------------------------------------------


def test_non_finite_coordinates_are_skipped_by_both_readers() -> None:
    dwg = ezdxf.new("R2000")
    dwg.modelspace().add_line((0, 0), (10, 10))
    dwg.modelspace().add_circle((5, 5), 2)
    text = _dxf_bytes(dwg).decode("cp1252")
    text = text.replace("AcDbLine\n 10\n0.0", "AcDbLine\n 10\nnan")
    text = text.replace("AcDbCircle\n 10\n5.0", "AcDbCircle\n 10\ninf")
    a, b = _both(text.encode("cp1252"))
    _assert_same(a, b)
    assert b.document.graphs == []
    assert b.ignored_counts == {"LINE": 1, "CIRCLE": 1}
    assert any("non-finite" in w for w in b.warnings)


def test_fuzzed_files_never_leave_the_fast_path_with_broken_geometry() -> None:
    """Whatever the fast path accepts from a damaged file is finite and equals the ezdxf model.

    The reader is allowed to *accept* a file ezdxf rejects (it does not validate the
    sections after ENTITIES); it is never allowed to build different geometry.
    """
    from nexcut.ops.import_gates import glyph_is_finite

    dwg = ezdxf.new("R2000")
    m = dwg.modelspace()
    m.add_line((0, 0), (10, 10))
    m.add_arc((5, 5), 3, 10, 200)
    m.add_lwpolyline([(0, 0, 0.3), (5, 0, 0), (5, 5, -0.2)], format="xyb")
    base = _dxf_bytes(dwg)
    rnd = random.Random(20260916)
    lines = base.split(b"\n")
    accepted = 0
    for i in range(200):
        cut = list(lines)
        cut[rnd.randrange(len(cut))] = rnd.choice(
            [b"nan", b"inf", b"-1e999", b"x", b"", b"999999999999999999999", b"  0"]
        )
        data = b"\n".join(cut[: len(cut) - (i % 9)])
        try:
            fast = dxf_stream.read_stream(data, DxfImportOptions())
        except dxf_stream.StreamUnsupported:
            continue
        accepted += 1
        for g in fast.document.graphs:
            for c, _ in iter_contours(g):
                assert all(glyph_is_finite(el.glyph) for el in c.elements)
        try:
            slow = read_dxf(data, reader="ezdxf")
        except DxfImportError:
            continue
        assert fast.document == slow.document, data[:120]
    assert accepted > 20, accepted


# ---------------------------------------------------------------------------
# performance (informational, with a generous bound)
# ---------------------------------------------------------------------------


def _many_lines(n: int) -> bytes:
    cols = int(math.isqrt(n)) or 1
    parts = ["  0\nSECTION\n  2\nENTITIES\n"]
    for i in range(n):
        r, c = divmod(i, cols)
        x, y = c * 3.0, r * 3.0
        parts.append(
            f"  0\nLINE\n  8\n0\n 10\n{x}\n 20\n{y}\n 30\n0\n"
            f" 11\n{x + 2.0}\n 21\n{y + 1.0}\n 31\n0\n"
        )
    parts.append("  0\nENDSEC\n  0\nEOF\n")
    return "".join(parts).encode("cp1252")


def test_streaming_reader_is_much_faster_than_ezdxf() -> None:
    """X11: the fast path must be a different order of cost, not a tuned constant.

    Informational numbers are printed; the assertion only demands a 2x margin, which
    is far below the 5-6x measured, so it holds on a loaded or slower runner.
    """
    import time

    data = _many_lines(20_000)
    t = time.perf_counter()
    fast = read_dxf(data, reader="stream")
    t_fast = time.perf_counter() - t
    t = time.perf_counter()
    slow = read_dxf(data, reader="ezdxf")
    t_slow = time.perf_counter() - t
    print(f"20k LINEs: stream {t_fast:.2f} s, ezdxf {t_slow:.2f} s ({t_slow / t_fast:.1f}x)")
    assert len(fast.document.graphs) == 20_000
    assert fast.document == slow.document
    assert t_fast * 2.0 < t_slow
