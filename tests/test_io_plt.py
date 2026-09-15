"""Tests of nexcut.io.plt (HPGL/2 import, 09 §3.7) on synthetic fixtures.

No vendor PLT file exists (09 §8 q.7), so every fixture is hand-written and the
expected geometry follows the HP-GL/2 reference with 40 plu/mm (UNVERIFIED).
"""

from __future__ import annotations

import math

import pytest

from nexcut.io import chf
from nexcut.io.plt import MNEMONICS, PltError, PltImportOptions, decode_pe, read_plt, tokenize
from nexcut.model import (
    ArcGlyph,
    CircleGlyph,
    Contour,
    LwPolylineGlyph,
    SplineGlyph,
)
from nexcut.model.flatten import check_contour
from nexcut.ops.import_gates import is_closed


def _contours(data: bytes, **opts: object) -> list[Contour]:
    r = read_plt(data, PltImportOptions(**opts))  # type: ignore[arg-type]
    out = [g for g in r.document.graphs if isinstance(g, Contour)]
    assert len(out) == len(r.document.graphs)
    for c in out:
        assert check_contour(c) == []
    return out


def test_mnemonic_set_is_the_cadmodule_table() -> None:
    assert len(MNEMONICS) == 37
    assert "SI" not in MNEMONICS and "FT" not in MNEMONICS  # 09 §3.7 verifier correction


def test_tokenizer() -> None:
    toks = list(tokenize('in;sp1;PU0,0PD10 20;LBhi there\x03DT*;LBx*CO"a;b";\x1b.(;PA1,2'))
    assert toks == [
        ("IN", ""),
        ("SP", "1"),
        ("PU", "0,0"),
        ("PD", "10 20"),
        ("LB", "hi there"),
        ("DT", "*"),
        ("LB", "x"),
        ("CO", "a;b"),
        ("PA", "1,2"),
    ]


def test_polyline_absolute_and_relative_units() -> None:
    cs = _contours(b"IN;PU0,0;PD400,0,400,400;PR-400,0;PD0,-400;PU;")
    assert len(cs) == 1
    g = cs[0].elements[0].glyph
    assert isinstance(g, LwPolylineGlyph)
    assert [tuple(v.pt) for v in g.vertices] == [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    assert cs[0].length == pytest.approx(40.0)
    assert is_closed(cs[0])


def test_pen_up_splits_contours_and_pen_to_layer() -> None:
    data = b"IN;SP2;PU0,0;PD40,0;PU80,0;PD120,0;SP3;PU0,40;PD40,40;"
    cs = _contours(data)
    assert len(cs) == 3 and {c.layer for c in cs} == {0}
    cs = _contours(data, pen_to_layer=True)
    assert [c.layer for c in cs] == [1, 1, 2]
    assert _contours(data, plu_per_mm=1.0)[0].length == pytest.approx(40.0)


def test_arc_absolute_relative_and_cw_direction() -> None:
    cs = _contours(b"IN;PU400,0;PD;AA0,0,90;AR0,-400,-90;PU;")
    assert len(cs) == 1
    els = cs[0].elements
    a1, a2 = els[0], els[1]
    assert isinstance(a1.glyph, ArcGlyph) and a1.direction == 1
    assert a1.glyph.radius == pytest.approx(10.0)
    assert (a1.glyph.start_angle, a1.glyph.end_angle) == pytest.approx((0.0, math.pi / 2))
    # AR: centre (0,10)-(0,-10)... from (0,10) with centre relative (0,-400) = (0,0): CW 90 deg
    assert isinstance(a2.glyph, ArcGlyph) and a2.direction == -1
    assert cs[0].end == pytest.approx((10.0, 0.0), abs=1e-9)
    assert cs[0].length == pytest.approx(10 * math.pi, abs=1e-6)


def test_three_point_arc_at_rt() -> None:
    cs = _contours(b"IN;PU400,0;PD;AT0,400,-400,0;PU;")
    g = cs[0].elements[0]
    assert isinstance(g.glyph, ArcGlyph) and g.direction == 1
    assert g.glyph.center == pytest.approx((0.0, 0.0), abs=1e-9)
    assert cs[0].length == pytest.approx(10 * math.pi, abs=1e-6)
    cs = _contours(b"IN;PU400,0;PD;RT-400,-400,-800,0;PU;")  # clockwise through (0,-10)
    g = cs[0].elements[0]
    assert g.direction == -1 and cs[0].end == pytest.approx((-10.0, 0.0), abs=1e-9)
    # collinear points degrade to lines
    cs = _contours(b"IN;PU0,0;PD;AT40,0,80,0;PU;")
    assert isinstance(cs[0].elements[0].glyph, LwPolylineGlyph)


def test_circle_is_own_contour_and_keeps_pen_state() -> None:
    cs = _contours(b"IN;PU200,200;CI80;PD240,200;PU;")
    assert len(cs) == 2
    c = cs[0].elements[0].glyph
    assert isinstance(c, CircleGlyph)
    assert c.center == (5.0, 5.0) and c.radius == pytest.approx(2.0)
    assert isinstance(cs[1].elements[0].glyph, LwPolylineGlyph)


def test_rectangles_and_wedges() -> None:
    cs = _contours(b"IN;PA40,40;EA120,80;ER-40,-40;RA0,0;RR40,40;WG40,0,90;EW40,180,-90;")
    assert len(cs) == 6
    assert all(is_closed(c) for c in cs)
    assert cs[0].bbox_min == (1.0, 1.0) and cs[0].bbox_max == (3.0, 2.0)
    assert cs[0].length == pytest.approx(6.0)
    assert cs[1].bbox_min == (0.0, 0.0) and cs[1].bbox_max == (1.0, 1.0)
    wedge = cs[4]
    assert wedge.length == pytest.approx(2 + math.pi / 2, abs=1e-5)  # chord-flattened
    assert [type(e.glyph) for e in wedge.elements] == [LwPolylineGlyph, ArcGlyph, LwPolylineGlyph]
    assert cs[5].elements[1].direction == -1


def test_bezier_absolute_and_relative() -> None:
    cs = _contours(b"IN;PU0,0;PD;BZ0,400,400,400,400,0;BR0,-400,400,-400,400,0;PU;")
    els = cs[0].elements
    assert len(els) == 2 and all(isinstance(e.glyph, SplineGlyph) for e in els)
    s2 = els[1].glyph
    assert isinstance(s2, SplineGlyph)
    assert [tuple(p) for p in s2.control_points] == [(10, 0), (10, -10), (20, -10), (20, 0)]
    assert s2.knots == [0.0] * 4 + [1.0] * 4
    assert cs[0].end == pytest.approx((20.0, 0.0), abs=1e-9)


def _pe_number(v: float, base32: bool = False) -> str:
    z = int(2 * v) if v >= 0 else int(2 * -v + 1)
    base = 32 if base32 else 64
    out = []
    while True:
        d = z % base
        z //= base
        if z == 0:
            out.append(chr(d + (95 if base32 else 191)))
            return "".join(out)
        out.append(chr(d + 63))


def test_pe_decoding() -> None:
    raw = (
        ":"
        + _pe_number(2)
        + "<="
        + _pe_number(400)
        + _pe_number(0)
        + _pe_number(400)
        + _pe_number(-1000)
    )
    assert list(decode_pe(raw)) == [
        ("pen", 2.0),
        ("pt", (400.0, 0.0, True, True)),
        ("pt", (400.0, -1000.0, False, False)),
    ]
    raw7 = "7" + _pe_number(12345, True) + _pe_number(-7, True)
    assert list(decode_pe(raw7)) == [("pt", (12345.0, -7.0, False, False))]
    rawf = ">" + _pe_number(2) + _pe_number(10) + _pe_number(-6)
    assert list(decode_pe(rawf)) == [("pt", (2.5, -1.5, False, False))]
    with pytest.raises(PltError):
        list(decode_pe("?"))


def test_pe_draws_polyline() -> None:
    body = (
        "<="
        + _pe_number(0)
        + _pe_number(0)
        + _pe_number(400)
        + _pe_number(0)
        + _pe_number(0)
        + _pe_number(400)
    )
    data = b"IN;PE" + body.encode("latin-1") + b";"
    cs = _contours(data)
    assert len(cs) == 1
    g = cs[0].elements[0].glyph
    assert isinstance(g, LwPolylineGlyph)
    assert [tuple(v.pt) for v in g.vertices] == [(0, 0), (10, 0), (10, 10)]


def test_scaling_sc_ip() -> None:
    # anisotropic: user 0..100 x 0..10 onto P1(0,0)-P2(4000,400) -> 40 plu/unit x, 40 plu/unit y
    cs = _contours(b"IN;IP0,0,4000,400;SC0,100,0,10;PU0,0;PD100,10;PU;")
    assert cs[0].end == pytest.approx((100.0, 10.0))
    # point factor: xmin=10, 80 plu per unit -> user 11 = 80 plu = 2 mm
    cs = _contours(b"IN;SC10,80,0,40,2;PU10,0;PD11,1;PU;")
    assert cs[0].start == pytest.approx((0.0, 0.0)) and cs[0].end == pytest.approx((2.0, 1.0))
    # isotropic: 0..10 x 0..20 into 10000x10000 -> k = 500, x centred (left 50%)
    cs = _contours(b"IN;SC0,10,0,20,1;PU0,0;PD10,20;PU;")
    assert cs[0].start == pytest.approx((2500 / 40, 0.0)) and cs[0].end == pytest.approx(
        (7500 / 40, 250.0)
    )
    # SC; turns scaling off, PR honours the scale
    cs = _contours(b"IN;SC0,1,0,1;PU0,0;PD;PR0.01,0;SC;PD40,0;PU;")
    assert cs[0].length == pytest.approx(2.5 + 1.0)


def test_polygon_mode_and_ep() -> None:
    cs = _contours(b"IN;PU0,0;PM0;PD40,0,40,40,0,0;PM1;CI20;PM2;EP;")
    assert len(cs) == 2
    assert isinstance(cs[1].elements[0].glyph, CircleGlyph)
    assert _contours(b"IN;PU0,0;PM0;PD40,0,40,40,0,0;PM2;") == []


def test_labels_unknown_and_queries() -> None:
    r = read_plt(b"IN;PU40,40;LBtext\x03XX1,2;OI;OS;KL;SM*;PA0,0;")
    assert r.labels == [((1.0, 1.0), "text")]
    assert r.ignored_counts["XX"] == 1
    assert r.command_counts["OI"] == 1 and r.command_counts["KL"] == 1
    assert any("LB" in w for w in r.warnings)
    assert r.document.graphs == []


def test_roundtrip_through_chf() -> None:
    r = read_plt(b"IN;PU0,0;PD400,0;AA400,400,90;BZ800,800,400,1200,0,800;PU;CI100;EA400,400;")
    back = chf.read_chf(chf.write_chf(r.document))
    assert chf.check_document(back) == []
    assert [len(g.elements) for g in back.graphs] == [len(g.elements) for g in r.document.graphs]  # type: ignore[union-attr]


def test_bad_number() -> None:
    with pytest.raises(PltError):
        read_plt(b"IN;PA1,2.3.4;")
