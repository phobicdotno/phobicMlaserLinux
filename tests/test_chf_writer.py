"""Unit tests for nexcut.io.chf and nexcut.model that need no vendor files (analysis 03)."""

from __future__ import annotations

import hashlib
import math

import pytest

from nexcut.io import chf
from nexcut.model import (
    ArcGlyph,
    ChfDocument,
    CircleGlyph,
    Contour,
    ContourElement,
    ContourEx,
    Crafts,
    Direction,
    EllipseArcGlyph,
    Group,
    LeadLine,
    LinkInfo,
    LwPolylineGlyph,
    LwPolyVertex,
    PointGlyph,
    Scan,
    SegmentGlyph,
    SplineGlyph,
    Text,
    Vec2,
)
from nexcut.model.flatten import check_contour, measure_contour

V = Vec2

# Golden md5s of vendor samples (03 §2), copied here so these tests run without SRC.
MD5_TEMPGRAPH = "d8017d9eb5ff9f344a7c7a1b0bc82d4f"  # File/Temp/tempGraph.chf, v5, 500 bytes
MD5_WORK1_3 = "12aa1212ef473b03390e9d1cea21fd82"  # Graph/Work1/3.chf, v4, 718 bytes


# --- number formatting (03 §4.1) -------------------------------------------------------


@pytest.mark.parametrize(
    ("x", "expected"),
    [
        (0.0, "0.0"),
        (-0.0, "0.0"),
        (9.9e-11, "0.0"),
        (1e-10, "0.0000000001"),  # boundary goes to %12.10f (x87 compare, 0x100d9b87)
        (5e-7, "0.0000005000"),
        (-5e-7, "-0.0000005000"),
        (1e-6, "0.000001"),
        (284.266045, "284.266045"),
        (-0.414214, "-0.414214"),
        (90.0, "90.000000"),
        (1247.12, "1247.120000"),
    ],
)
def test_format_double(x: float, expected: str) -> None:
    assert chf.format_double(x) == expected


def test_format_double_buffered_zero() -> None:
    assert chf.format_double(0.0, "buffered") == "0"
    assert chf.format_double(1.5, "buffered") == "1.500000"


def test_format_double_rejects_nonfinite() -> None:
    for x in (math.nan, math.inf, -math.inf):
        with pytest.raises(ValueError):
            chf.format_double(x)


def test_msvc_rounding_emulation() -> None:
    # UNVERIFIED CRT emulation: half-up on exact ties (Python's %f gives 0.007812).
    assert chf.msvc_fixed(0.0078125, 6) == "0.007813"
    assert chf.msvc_fixed(-0.0078125, 6) == "-0.007813"
    assert chf.msvc_fixed(0.5, 0) == "1"


def test_format_point_has_no_zero_special_case() -> None:
    assert chf.format_point((0.0, 0.0)) == "0.000000,0.000000"
    assert chf.format_point(V(395.33, 302.907)) == "395.330000,302.907000"


# --- constructed documents reproduce vendor bytes ---------------------------------------


def _tempgraph_doc() -> ChfDocument:
    c = Contour(
        precision=0.1,
        length=297.967353,
        bbox_min=V(0.018, 0.018),
        bbox_max=V(94.863954, 94.863954),
        start=V(94.863954, 47.440977),
        end=V(94.863954, 47.440977),
        elements=[ContourElement(CircleGlyph(V(47.440977, 47.440977), 47.422977), Direction.FORWARD)],
    )
    return ChfDocument(graphs=[c])


def test_constructed_v5_equals_tempgraph_sample() -> None:
    data = chf.write_chf(_tempgraph_doc())
    assert len(data) == 500
    assert hashlib.md5(data).hexdigest() == MD5_TEMPGRAPH
    assert data.startswith(b"scFlie\r\n5\r\n<Begin Graphs>\r\n1\r\n####graph NO:1\r\n8\r\n0.100000\r\n")
    assert data.endswith(b"<End Graphs>\r\n0\r\n0.0\r\n0.000000,0.000000\r\n0.000000,0.000000\r\neof\r\n")


def test_constructed_v4_equals_work1_3_sample() -> None:
    verts = [
        LwPolyVertex(V(10.200143, 9.472922), 0.0),
        LwPolyVertex(V(11.790190, 9.472922), -0.414214),
        LwPolyVertex(V(12.823721, 8.439391), -0.414214),
        LwPolyVertex(V(11.790190, 7.405861), 0.0),
        LwPolyVertex(V(10.200143, 7.405861), -0.414214),
        LwPolyVertex(V(9.166612, 8.439391), -0.414214),
    ]
    c = Contour(
        precision=0.1,
        length=9.673958,
        bbox_min=V(9.166612, 7.405861),
        bbox_max=V(12.823721, 9.472922),
        start=V(10.200143, 9.472922),
        end=V(10.200143, 9.472922),
        elements=[ContourElement(LwPolylineGlyph(1, verts))],
    )
    data = chf.write_chf(ChfDocument(version=4, graphs=[c]))
    assert hashlib.md5(data).hexdigest() == MD5_WORK1_3
    assert check_contour(c) == []  # stadium length/bbox/start/end recomputed (03 §11)


# --- synthetic documents exercising every record type -----------------------------------


def _contour(glyph: object, direction: int = 1, **kw: object) -> Contour:
    return Contour(precision=0.01, length=1.5, bbox_min=V(0, 0), bbox_max=V(1, 1),
                   start=V(0, 0), end=V(1, 1), elements=[ContourElement(glyph, direction)],
                   **kw)  # type: ignore[arg-type]  # fmt: skip


def _full_doc(version: int = 5) -> ChfDocument:
    crafts = Crafts(
        compensate_type=3,
        compensate_width=0.15,
        pwm_enable=0,
        pwm_nodes=[(0.25, 80.0), (0.75, 5e-8)],
        pwm_close_pos_ratios=[0.016594, 0.983406],
        double170=0.5,
        double188=1.25,
        lead_line=LeadLine(type=2, angle_deg=45.0, length=8.0, arc_radius=2.0 if version > 1 else None,
                           flag=True),  # fmt: skip
        cool_pos=[0.1, 0.9] if version > 2 else None,
    )
    glyphs = [
        PointGlyph(V(1.0, 2.0)),
        SegmentGlyph(V(-1.5, 0.0), V(3.25, -4.0)),
        ArcGlyph(V(10.0, 10.0), 5.0, 0.0, -1.570796),  # %f keeps 6 decimals
        CircleGlyph(V(0.0, 0.0), 1e-7),
        EllipseArcGlyph(V(5.0, 5.0), V(3.0, 1.0), 0.5, 0.0, 3.141593),
        LwPolylineGlyph(0, [LwPolyVertex(V(0.5, 0.25), 0.0), LwPolyVertex(V(1.5, 0.75), 1.0)]),
        SplineGlyph(1, 0, [V(0, 0), V(1, 2), V(3, 2), V(4, 0)], [0, 0, 0, 0, 1, 1, 1, 1]),
    ]
    contour = Contour(
        precision=0.01, length=123.456789, bbox_min=V(-1.5, -4.0), bbox_max=V(15.0, 10.0),
        start=V(1.0, 2.0), end=V(4.0, 0.0),
        elements=[ContourElement(g, d) for g, d in zip(glyphs, [1, -1, 1, -1, 1, 1, -1], strict=True)],
        layer=3, int58=2, crafts=crafts,
    )  # fmt: skip
    child = _contour(SegmentGlyph(V(0, 0), V(1, 1)), crafts=Crafts(cool_pos=[] if version > 2 else None,
                     lead_line=LeadLine(arc_radius=0.0 if version > 1 else None)))  # fmt: skip
    group = Group(length=1.5, bbox_min=V(0, 0), bbox_max=V(1, 1), start=V(0, 0), end=V(1, 1),
                  children=[child], layer=1, int58=1)  # fmt: skip
    cex = ContourEx(length=2.0, children=[child], layer=2, int58=0, links=[
        LinkInfo(1, 0, 0.1, 0.2, 0.3, 0.4, V(1.0, 2.0), V(3.0, 4.0)),
        LinkInfo(0, 1, -1.0, 0.0, 7.0, 8.0, V(5.0, 6.0), V(7.0, 8.0)),
    ])  # fmt: skip
    scan = Scan(length=3.0, children=[child], paths=[child, child])
    text = Text(position=V(12.0, 34.0), d130=5.0, d138=1.0, d140=0.0, text="激光ABC", font="宋体",
                outline=Group(children=[child], layer=4, int58=1) if version >= 4 else None,
                layer=4, int58=1)  # fmt: skip
    return ChfDocument(version=version, graphs=[contour, group, text, scan, cex], trailer_bool=True,
                       trailer_double=2.5, trailer_pt1=V(10.0, 20.0), trailer_pt2=V(-1.0, 0.0))  # fmt: skip


def _strip_tokens(doc: ChfDocument) -> ChfDocument:
    for g in doc.graphs:
        if isinstance(g, ContourEx):
            for e in g.links:
                e.pt18_token = e.pt28_token = None
    return doc


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5])
def test_round_trip_all_types(version: int) -> None:
    doc = _full_doc(version)
    data = chf.write_chf(doc)
    back = chf.read_chf(data)
    assert _strip_tokens(back) == doc
    assert chf.write_chf(back) == data
    assert data.endswith(b"eof\r\n") and b"\n" not in data.replace(b"\r\n", b"")


def test_v5_layout_details() -> None:
    lines = chf.write_chf(_full_doc(5)).split(b"\r\n")
    assert b"####Gly: 1" in lines and b"####Group elem NO:1" in lines
    assert b"####ContourEx link info:2" in lines and b"####Path:2" in lines
    assert b"0.0000000500" in lines  # %12.10f branch
    assert b"\xbc\xa4\xb9\xe2ABC" in lines  # 激光ABC in GBK
    assert b"\xcb\xce\xcc\xe5" in lines  # 宋体 in GBK
    assert b"" not in lines[:-1]  # v5 has no reserved blank lines
    # lwpolyline vertex order: point first, bulge second (0x10083e70)
    i = lines.index(b"0.500000,0.250000")
    assert lines[i - 2 : i + 4] == [b"0", b"2", b"0.500000,0.250000", b"0.0", b"1.500000,0.750000", b"1.000000"]


def test_v4_reserved_blank_lines() -> None:
    doc = ChfDocument(version=4, graphs=[_contour(PointGlyph(V(0, 0)))])
    lines = chf.write_chf(doc).split(b"\r\n")
    assert lines[:9] == [b"scFlie", b"4", b"<Begin Graphs>", b"", b"", b"", b"", b"", b"1"]
    glyphs = lines.index(b"<Glyphs>")
    assert lines[glyphs + 1 : glyphs + 12] == [b""] * 10 + [b"1.500000"]
    crafts = lines.index(b"<Crafts>")
    assert lines[crafts + 1 : crafts + 22] == [b""] * 20 + [b"-1"]


def test_version_conversion_v4_to_v5() -> None:
    doc4 = _full_doc(4)
    doc5 = chf.read_chf(chf.write_chf(doc4, version=5))
    assert doc5.version == 5
    doc4.version = 5
    assert _strip_tokens(doc5) == doc4


def test_writer_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        chf.write_chf(ChfDocument(graphs=[Contour()]))  # no glyphs
    with pytest.raises(ValueError):
        chf.write_chf(ChfDocument(graphs=[_contour(SplineGlyph(0, 0, [V(0, 0)] * 4, [0.0] * 7))]))
    with pytest.raises(ValueError):
        chf.write_chf(ChfDocument(graphs=[Text(text="a\r\nb")]))
    with pytest.raises(ValueError):
        chf.write_chf(ChfDocument(version=6))


def test_unmappable_character_becomes_question_mark() -> None:
    # U+0E01 has neither a cp936 code nor a best fit -> DefaultChar "?" (03 §4.1, io/cp936.py).
    data = chf.write_chf(ChfDocument(graphs=[Text(text="a\u0e01b")]))
    assert b"\r\na?b\r\n" in data
    # "€" is 0x80 in Windows cp936 (review fix; Python's gbk codec lacks it).
    data = chf.write_chf(ChfDocument(graphs=[Text(text="a€b")]))
    assert b"\r\na\x80b\r\n" in data


# --- reader behaviour (03 §4.2, §5) -----------------------------------------------------


def _mini(body: bytes = b"0.0\r\n") -> bytes:
    return b"scFlie\r\n5\r\n<Begin Graphs>\r\n0\r\n<End Graphs>\r\n0\r\n" + body + b"1.0,2.0\r\n0,0\r\neof\r\n"


def test_reader_tokenizer_rules() -> None:
    doc = chf.read_chf(_mini(b" 1 2 . 5\t\r\n").replace(b"\r\n0\r\n<End", b"\r\n\r\n<End"))
    assert doc.trailer_double == 12.5  # spaces/tabs dropped anywhere
    assert doc.trailer_pt1 == (1.0, 2.0)
    # mixed LF / CR terminators
    assert chf.read_chf(_mini().replace(b"\r\n", b"\n")).trailer_pt1 == (1.0, 2.0)


def test_reader_empty_tokens_and_atoi() -> None:
    data = b"scFlie\r\n5\r\n<Begin Graphs>\r\n\r\n<End Graphs>\r\n\r\n\r\n,\r\n1-2,-\r\neof\r\n"
    doc = chf.read_chf(data)
    assert doc.graphs == [] and doc.trailer_bool is False and doc.trailer_double == 0.0
    assert doc.trailer_pt1 == (0.0, 0.0) and doc.trailer_pt2 == (1.0, 0.0)


def test_reader_point_half_longer_than_30_is_zero() -> None:
    doc = chf.read_chf(_mini().replace(b"1.0,2.0", b"1" * 31 + b",2.5"))
    assert doc.trailer_pt1 == (0.0, 2.5)


def test_reader_errors() -> None:
    with pytest.raises(chf.ChfError) as e:
        chf.read_chf(b"scFile\r\n5\r\neof\r\n")
    assert e.value.code == 5
    with pytest.raises(chf.ChfError) as e:
        chf.read_chf(_mini()[:-5])
    assert e.value.code == 4
    with pytest.raises(chf.ChfError) as e:
        chf.read_chf(_mini(b"1e5\r\n"))
    assert e.value.code == 3
    with pytest.raises(chf.ChfError) as e:
        chf.read_chf(b"")
    assert e.value.code == 2


def test_reader_eof_tail_skip_is_signed() -> None:
    # bytes <= 0x0d and >= 0x80 after "eof" are skipped by the tail check (0x100da2f8)
    assert chf.read_chf(_mini() + b"\x00\xff\r\n", strict=False).trailer_pt1 == (1.0, 2.0)


def test_strict_vs_lenient_markers() -> None:
    data = chf.write_chf(_tempgraph_doc()).replace(b"<Crafts>", b"<Kraft>")
    with pytest.raises(chf.ChfError):
        chf.read_chf(data)
    assert chf.read_chf(data, strict=False) == _tempgraph_doc()


def test_drop_degenerate_filter() -> None:
    tiny = _contour(SegmentGlyph(V(0, 0), V(0.001, 0)))
    tiny.length = 0.001
    point = _contour(PointGlyph(V(5, 5)))
    point.length = 0.0
    exact = _contour(SegmentGlyph(V(0, 0), V(0.01, 0)))
    exact.length = 0.01  # strict "<": kept
    grp = Group(length=0.0, children=[point])
    doc = ChfDocument(graphs=[tiny, point, exact, grp])
    data = chf.write_chf(doc)
    assert len(chf.read_chf(data).graphs) == 4
    kept = chf.read_chf(data, drop_degenerate=True).graphs
    assert [type(g).__name__ for g in kept] == ["Contour", "Contour", "Group"]
    assert kept[0] == point and kept[1] == exact


def test_link_point_tokens_kept_for_json() -> None:
    doc = chf.read_chf(chf.write_chf(_full_doc(5)))
    cex = doc.graphs[4]
    assert isinstance(cex, ContourEx)
    d = chf.to_tool_dict(doc)["graphs"][4]["links"]
    assert d[0]["pt18"] == [1.0, 2.0] and d[0]["pt28"] == "3.000000,4.000000"
    assert d[1]["pt18"] == "5.000000,6.000000" and d[1]["pt28"] == [7.0, 8.0]


# --- geometry ---------------------------------------------------------------------------


def test_measure_reversed_segment() -> None:
    c = _contour(SegmentGlyph(V(0, 0), V(3, 4)), direction=-1)
    m = measure_contour(c)
    assert m is not None
    assert m.length == pytest.approx(5.0)
    assert m.start == (3.0, 4.0) and m.end == (0.0, 0.0)


def test_measure_circle_starts_at_angle_zero() -> None:
    c = _contour(CircleGlyph(V(1, 1), 2.0))
    m = measure_contour(c)
    assert m is not None
    assert m.length == pytest.approx(4 * math.pi, rel=1e-6)
    assert m.start[0] == pytest.approx(3.0) and m.start[1] == pytest.approx(1.0)
