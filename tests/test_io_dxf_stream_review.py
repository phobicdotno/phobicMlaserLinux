"""Adversarial review of the streaming DXF reader (``docs/STATUS.md`` §5 task 7, X11).

Lens: **silent divergence between the two readers**.  ``io/dxf_stream.py`` parses the
``ENTITIES`` section tag by tag and never builds an ezdxf document; ``read_dxf(reader="auto")``
prefers it and falls back to ezdxf only when it declines.  Two ways that can go wrong and never
be noticed:

* the fast path reads a file it should have declined and builds *different* geometry or a
  different layer assignment - a job that cuts the wrong shape, with no error anywhere;
* the fast path declines a file it could have read, which costs nothing but correctness and
  everything the X11 budget was about.

``tests/test_io_dxf_stream.py`` already cross-checks the vendor drawing, the entity zoo at
R12/R2000/R2018 and 14 randomised corpora.  This file adds the cases that review named and
that those generators cannot produce: **cp936 layer names resolved through the LAYER table**,
**nested and block-local ``INSERT``** (both sides of the fallback boundary), the **spline
families** the generators do not write (closed/periodic, fit points with tangents, an explicit
knot tolerance), **bulge extremes**, and a hostile-tag corpus - repeated group codes, a leading
bulge, a wrong vertex count, colour values outside 1..255 - where the answer is either
"identical model" or "decline", never "different model".

No finding of substance: on every case here the two readers build equal
:class:`~nexcut.model.graph.ChfDocument` objects and the fallback fires exactly where the module
docstring says it does.  The tests stay as the regression net for the next change to either
reader.
"""

from __future__ import annotations

import ezdxf
import pytest

from nexcut.io import dxf_stream
from nexcut.io.dxf import DxfImportError, DxfImportOptions, read_dxf
from nexcut.model.graph import iter_contours

# Sibling test module (see tests/test_stream_plan_fidelity_review.py on the import mode).
from test_io_dxf_stream import _assert_same, _both, _dxf_bytes  # noqa: I001


def _layers_of(result: object) -> list[int]:
    """Process layer of every contour, in document order (what the cut actually uses)."""
    return [c.layer for g in result.document.graphs for c, _ in iter_contours(g)]  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# cp936 layer names (03 §4.1, 09 §3.2)
# ---------------------------------------------------------------------------

CJK = "图层一"
"""A layer name that is three double-byte cp936 characters and not valid cp1252 text."""


def _cp936_file(entity_color: str) -> bytes:
    """A hand-written AC1009 file whose LAYER names are raw GBK bytes under ANSI_936."""
    return (
        "  0\nSECTION\n  2\nHEADER\n"
        "  9\n$ACADVER\n  1\nAC1009\n  9\n$DWGCODEPAGE\n  3\nANSI_936\n  0\nENDSEC\n"
        "  0\nSECTION\n  2\nTABLES\n  0\nTABLE\n  2\nLAYER\n 70\n2\n"
        f"  0\nLAYER\n  2\n{CJK}\n 70\n0\n 62\n9\n  6\nCONTINUOUS\n"
        "  0\nLAYER\n  2\n0\n 70\n0\n 62\n7\n  6\nCONTINUOUS\n"
        "  0\nENDTAB\n  0\nENDSEC\n"
        "  0\nSECTION\n  2\nENTITIES\n"
        f"  0\nLINE\n  8\n{CJK}\n{entity_color} 10\n0\n 20\n0\n 30\n0\n 11\n10\n 21\n10\n 31\n0\n"
        f"  0\nCIRCLE\n  8\n{CJK}\n 10\n5\n 20\n5\n 30\n0\n 40\n2\n"
        "  0\nENDSEC\n  0\nEOF\n"
    ).encode("gbk")


@pytest.mark.parametrize(
    ("what", "color", "expect"),
    [("bylayer", " 62\n256\n", 8), ("explicit", " 62\n4\n", 3), ("absent", "", 8)],
)
def test_a_cp936_layer_name_resolves_the_same_process_layer_on_both_readers(
    what: str, color: str, expect: int
) -> None:
    """``IGP.EnableReadGraphColor`` maps ACI *n* to process layer *n-1* (01 §1.2), and a
    BYLAYER entity has to find its colour through the ``LAYER`` table.

    The streaming reader keys that table on the **raw, undecoded** name (it only decodes for
    the reported layer list), so a name that is two bytes per character under ANSI_936 is the
    case where a decode-then-match reader and a match-then-decode reader could disagree - and
    a mismatch is not an error, it is a contour silently cut on layer 0 with layer 0's power.
    """
    data = _cp936_file(color)
    a, b = _both(data, read_color=True)
    _assert_same(a, b)
    assert b.encoding == "cp936"
    assert _layers_of(a) == _layers_of(b) == [expect, 8]
    assert b.entity_layers[CJK] == 2  # decoded for display, matched raw
    assert CJK in b.layers


def test_a_cp936_drawing_ignores_colours_when_the_gate_is_off() -> None:
    """With ``read_color`` off every contour is layer 0 on both readers, and the decoded layer
    names still have to come out of the cp936 bytes."""
    a, b = _both(_cp936_file(" 62\n256\n"))
    _assert_same(a, b)
    assert _layers_of(b) == [0, 0] and b.entity_layers[CJK] == 2


# ---------------------------------------------------------------------------
# the fallback boundary: INSERT
# ---------------------------------------------------------------------------


def _nested_insert() -> bytes:
    dwg = ezdxf.new("R2000")
    inner = dwg.blocks.new("IN")
    inner.add_circle((0, 0), 1)
    outer = dwg.blocks.new("OUT")
    outer.add_blockref("IN", (2, 2))
    outer.add_line((0, 0), (3, 3))
    dwg.modelspace().add_blockref("OUT", (10, 10), dxfattribs={"xscale": 2, "rotation": 30})
    return _dxf_bytes(dwg)


def _block_local_insert() -> bytes:
    """An ``INSERT`` that exists only inside ``BLOCKS`` - the modelspace has none."""
    dwg = ezdxf.new("R2000")
    leaf = dwg.blocks.new("LEAF")
    leaf.add_line((0, 0), (1, 1))
    holder = dwg.blocks.new("HOLDER")
    holder.add_blockref("LEAF", (0, 0))
    dwg.modelspace().add_line((0, 0), (9, 9))
    dwg.modelspace().add_arc((2, 2), 3, 0, 90)
    return _dxf_bytes(dwg)


def test_a_nested_insert_falls_back_and_is_still_exploded() -> None:
    """A block reference needs the whole block table, which the streaming reader never builds;
    it must decline, and ``auto`` must then produce the ezdxf model - the nesting is the case
    where a half-hearted "just read the BLOCKS section" shortcut would go wrong."""
    data = _nested_insert()
    res = read_dxf(data)
    assert res.reader == "ezdxf" and "INSERT needs the block table" in res.fallback_reason
    assert res.document == read_dxf(data, reader="ezdxf").document
    assert len(res.document.graphs) >= 2  # the line and the nested circle both arrive
    with pytest.raises(DxfImportError, match="streaming reader declined"):
        read_dxf(data, reader="stream")


def test_an_insert_that_never_reaches_the_modelspace_does_not_cost_the_fast_path() -> None:
    """The other side of the boundary: ``BLOCKS`` is skipped wholesale, so a block that
    references another block is irrelevant unless the modelspace instantiates it.  Declining
    here would be safe but would hand every drawing with an unused block library back to
    ezdxf - which is the whole X11 budget."""
    data = _block_local_insert()
    a, b = _both(data)
    _assert_same(a, b)
    assert read_dxf(data).reader == "stream"
    assert len(b.document.graphs) == 2


# ---------------------------------------------------------------------------
# splines the randomised generators do not write
# ---------------------------------------------------------------------------


def _spline_zoo() -> bytes:
    dwg = ezdxf.new("R2000")
    msp = dwg.modelspace()
    msp.add_spline([(0, 0), (2, 3), (5, -1), (8, 4)])  # fit points, no tangents
    msp.add_spline(  # fit points with start/end tangents (codes 12/13 + 22/23)
        [(20, 0), (22, 3), (25, -1)],
        dxfattribs={"start_tangent": (1, 0, 0), "end_tangent": (0, 1, 0), "flags": 1024},
    )
    closed = msp.add_spline(dxfattribs={"degree": 3})
    closed.set_closed([(40, 0, 0), (43, 4, 0), (46, 0, 0), (43, -4, 0)])
    knotted = msp.add_open_spline([(60, 0), (62, 3), (65, -1), (68, 4), (70, 0)], degree=3)
    knotted.dxf.knot_tolerance = 1e-6  # group 42, which rounds the knot vector
    msp.add_rational_spline(
        [(80, 0), (82, 5), (86, 5), (88, 0)], weights=[1.0, 2.0, 0.5, 1.0], degree=3
    )
    return _dxf_bytes(dwg)


def test_every_spline_flavour_reads_identically() -> None:
    """``_bspline`` rebuilds ``Spline.construction_tool`` from raw tags: control points with
    knots and weights, or fit points with tangents.  The randomised corpora only write the
    first two; a closed/periodic spline, a fit-point spline with tangents and an explicit knot
    tolerance are the branches left."""
    a, b = _both(_spline_zoo())
    _assert_same(a, b)
    assert len(b.document.graphs) == 5


# ---------------------------------------------------------------------------
# bulges and hostile tags
# ---------------------------------------------------------------------------


def test_bulge_extremes_read_identically() -> None:
    """Bulge 1 is a semicircle, |bulge| > 1 a major arc, 1e-12 a straight line that must not
    divide by zero, and a bulge on the last vertex of an *open* polyline is ignored."""
    dwg = ezdxf.new("R2000")
    msp = dwg.modelspace()
    msp.add_lwpolyline(
        [(0, 0, 1.0), (10, 0, -1.0), (10, 10, 0.0), (0, 10, 0.5)], format="xyb", close=True
    )
    msp.add_lwpolyline([(0, 0, 1e-12), (1, 0, 4.0), (2, 0, -4.0)], format="xyb")
    msp.add_lwpolyline([(0, 20, 0.0), (5, 20, 0.9)], format="xyb")  # bulge on the last vertex
    poly = msp.add_polyline2d([(0, 30), (5, 30), (5, 35)], close=True)
    poly[0].dxf.bulge = 0.7
    poly[1].dxf.bulge = -2.5
    a, b = _both(_dxf_bytes(dwg))
    _assert_same(a, b)
    assert len(b.document.graphs) == 4


_HOSTILE = {
    # a repeated group code: the later tag wins on both readers
    "repeated-code": "  0\nLINE\n  8\n0\n 10\n0\n 20\n0\n 10\n3\n 20\n4\n 11\n9\n 21\n9\n",
    # a bulge tag before the first vertex belongs to no vertex
    "leading-bulge": (
        "  0\nLWPOLYLINE\n100\nAcDbEntity\n  8\n0\n100\nAcDbPolyline\n 90\n2\n 70\n0\n"
        " 42\n0.5\n 10\n0.0\n 20\n0.0\n 10\n5.0\n 20\n0.0\n"
    ),
    # the declared vertex count disagrees with the vertices present
    "wrong-count": (
        "  0\nLWPOLYLINE\n100\nAcDbEntity\n  8\n0\n100\nAcDbPolyline\n 90\n7\n 70\n0\n"
        " 10\n0.0\n 20\n0.0\n 10\n5.0\n 20\n0.0\n 10\n5.0\n 20\n5.0\n"
    ),
    # colour values outside 1..255: BYBLOCK, a negative (layer off) and one past the table
    "colour-edges": (
        "  0\nLINE\n  8\n0\n 62\n0\n 10\n0\n 20\n0\n 11\n1\n 21\n1\n"
        "  0\nLINE\n  8\n0\n 62\n-3\n 10\n0\n 20\n2\n 11\n1\n 21\n3\n"
        "  0\nLINE\n  8\n0\n 62\n257\n 10\n0\n 20\n4\n 11\n1\n 21\n5\n"
    ),
    # an infinite line is counted and dropped, not turned into geometry
    "xline-ray": "  0\nXLINE\n  8\n0\n 10\n0\n 20\n0\n 11\n1\n 21\n0\n  0\nRAY\n  8\n0\n 10\n0\n 20\n0\n 11\n0\n 21\n1\n",
    # a zero-area SOLID and a 3DFACE, which reach different helpers
    "degenerate-solid": (
        "  0\nSOLID\n  8\n0\n 10\n0\n 20\n0\n 30\n0\n 11\n0\n 21\n0\n 31\n0\n"
        " 12\n0\n 22\n0\n 32\n0\n 13\n0\n 23\n0\n 33\n0\n"
        "  0\n3DFACE\n  8\n0\n 10\n0\n 20\n0\n 30\n0\n 11\n1\n 21\n0\n 31\n0\n"
        " 12\n1\n 22\n1\n 32\n0\n 13\n0\n 23\n1\n 33\n0\n"
    ),
}


@pytest.mark.parametrize("case", sorted(_HOSTILE))
@pytest.mark.parametrize("read_color", [False, True])
def test_hostile_tags_read_identically_or_are_declined(case: str, read_color: bool) -> None:
    """Whatever the fast path accepts it has to build the ezdxf model out of; the only other
    allowed answer is :class:`~nexcut.io.dxf_stream.StreamUnsupported`."""
    text = (
        "  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC1015\n  0\nENDSEC\n"
        "  0\nSECTION\n  2\nTABLES\n  0\nTABLE\n  2\nLAYER\n"
        "  0\nLAYER\n  2\n0\n 70\n0\n 62\n7\n  0\nENDTAB\n  0\nENDSEC\n"
        "  0\nSECTION\n  2\nENTITIES\n" + _HOSTILE[case] + "  0\nENDSEC\n  0\nEOF\n"
    )
    data = text.encode("cp1252")
    options = DxfImportOptions(read_color=read_color)
    try:
        b = read_dxf(data, options, reader="stream")
    except DxfImportError:
        assert read_dxf(data, options).reader == "ezdxf"
        return
    a = read_dxf(data, options, reader="ezdxf")
    _assert_same(a, b)
    assert _layers_of(a) == _layers_of(b)


def test_the_fast_path_and_the_generic_tag_path_agree_on_these_files() -> None:
    """``_line_entity`` is a shortcut for LINE, not a second implementation; the cp936 file and
    the hostile corpus have to come out the same with it switched off."""
    files = [_cp936_file(" 62\n256\n"), _block_local_insert(), _spline_zoo()]
    for case in sorted(_HOSTILE):
        files.append(
            ("  0\nSECTION\n  2\nENTITIES\n" + _HOSTILE[case] + "  0\nENDSEC\n  0\nEOF\n").encode(
                "cp1252"
            )
        )
    for data in files:
        for options in (DxfImportOptions(), DxfImportOptions(read_color=True)):
            try:
                fast = dxf_stream.read_stream(data, options)
            except dxf_stream.StreamUnsupported:
                continue
            generic = dxf_stream.read_stream(data, options, fast_line=False)
            assert fast.document == generic.document
            assert fast.entity_counts == generic.entity_counts
            assert fast.entity_layers == generic.entity_layers
