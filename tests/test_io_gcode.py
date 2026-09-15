"""Tests of nexcut.io.gcode (09 §3.7 grammar and error strings) on synthetic programs."""

from __future__ import annotations

import math

import pytest

from nexcut.io import chf, cp936
from nexcut.io import gcode as g
from nexcut.io.gcode import GCodeError, GCodeImportOptions, parse_line, read_gcode
from nexcut.model import ArcGlyph, Contour, LwPolylineGlyph
from nexcut.model.flatten import check_contour
from nexcut.ops.import_gates import is_closed


def _run(text: str, strict: bool = False) -> list[Contour]:
    r = read_gcode(text.encode("ascii"), GCodeImportOptions(strict=strict))
    out = [c for c in r.document.graphs if isinstance(c, Contour)]
    for c in out:
        assert check_contour(c) == []
    return out


# ---------------------------------------------------------------------------
# Grammar (strict = the original's productions)
# ---------------------------------------------------------------------------

VALID_STRICT = [
    "",
    "N10",
    "N10 G01 X1.5 Y-2 F100 S20",
    "G2 X1 Y1 I0.5 J0.5",
    "G2 X1 I1 K0",
    "G03 X1 Y1 R-1.",
    "X.5 Y+3",
    "Z1 A1 B2 C3",
    "X1 U2",
    "F100",
    "F100.5 S3",
    "S20",
    "G4 P500",
    "G0 T1",
    "G90",
    "Q3",
    "M02",
    "LP12",
    "L 3 2",
    "D1",
    "G1 X1 Y1 Z1 A1 B1 C1 F1 S1   ; comment",
    "X1 Y2 Z3 I4 J5 K6",
]

INVALID_STRICT = [
    "G1 F100 X1",  # <g> <fs> is not a production
    "Y1 X1",  # xyz order
    "G1 X1 J1",  # <ijkr> needs I first or R
    "G1 I1",  # I alone
    "G1 G90",  # one <g> per line
    "g1 x1",  # lexer is case sensitive
    "M1.5",  # M takes DecLiteral
    "L 3",  # L needs two DecLiterals
    "G1 X1 (comment)",  # parenthesis comments are not in the grammar
    "N1 N2",
    "F-1",
]


@pytest.mark.parametrize("line", VALID_STRICT)
def test_strict_grammar_accepts(line: str) -> None:
    parse_line(line, strict=True)


@pytest.mark.parametrize("line", INVALID_STRICT)
def test_strict_grammar_rejects(line: str) -> None:
    with pytest.raises(GCodeError) as exc:
        parse_line(line, strict=True)
    assert exc.value.kind in ("syntax", "lexical")


def test_parsed_words() -> None:
    ln = parse_line("N7 G02 X-1.5 Y2 I+0.5 J.25 F300", strict=True)
    assert ln.n == 7
    assert ln.words == [("G", 2.0), ("X", -1.5), ("Y", 2.0), ("I", 0.5), ("J", 0.25), ("F", 300.0)]
    assert parse_line("L 4 3", strict=True).l_call == (4, 3)
    assert parse_line("LP4", strict=True).get("LP") == 4.0


def test_lenient_grammar() -> None:
    ln = parse_line("n5 g90 g1 f100 y2 x1 (move) ; tail")
    assert ln.n == 5
    assert ln.all("G") == [90.0, 1.0]
    assert ln.get("X") == 1.0 and ln.get("Y") == 2.0
    assert parse_line("%").is_empty
    assert parse_line("O1000").is_empty
    assert parse_line("/G1 X1").is_empty
    with pytest.raises(GCodeError) as exc:
        parse_line("G1 (unterminated")
    assert exc.value.message == g.ERR_COMMENT


# ---------------------------------------------------------------------------
# Motion
# ---------------------------------------------------------------------------


def test_lines_and_rapids() -> None:
    cs = _run("G90\nG0 X10 Y10\nG1 X20 Y10\nX20 Y20\nG0 X50\nG1 Y30\nM02\n")
    assert len(cs) == 2
    g0 = cs[0].elements[0].glyph
    assert isinstance(g0, LwPolylineGlyph)
    assert [tuple(v.pt) for v in g0.vertices] == [(10, 10), (20, 10), (20, 20)]
    assert cs[1].start == (50.0, 20.0) and cs[1].end == (50.0, 30.0)


def test_arcs_ij_and_r() -> None:
    cs = _run(
        "G0 X10 Y0\n"
        "G3 X0 Y10 I-10 J0\n"  # CCW quarter, centre (0,0)
        "G2 X-10 Y0 I0 J-10\n"  # CW from (0,10) around (0,0) to (-10,0): 270 deg
        "G2 X0 Y-10 R10\n"  # CW minor
        "G3 X10 Y0 R-10\n"  # CCW major (270 deg)
    )
    assert len(cs) == 1
    els = cs[0].elements
    assert [e.direction for e in els] == [1, -1, -1, 1]
    a0, a1, a2, a3 = (e.glyph for e in els)
    assert isinstance(a0, ArcGlyph) and a0.center == pytest.approx((0, 0))
    assert a0.end_angle - a0.start_angle == pytest.approx(math.pi / 2)
    assert isinstance(a1, ArcGlyph) and a1.end_angle - a1.start_angle == pytest.approx(
        3 * math.pi / 2
    )
    assert isinstance(a2, ArcGlyph) and a2.end_angle - a2.start_angle == pytest.approx(math.pi / 2)
    assert isinstance(a3, ArcGlyph) and a3.end_angle - a3.start_angle == pytest.approx(
        3 * math.pi / 2
    )
    assert cs[0].end == pytest.approx((10.0, 0.0), abs=1e-9)
    expected = 10 * (math.pi / 2 + 3 * math.pi / 2 + math.pi / 2 + 3 * math.pi / 2)
    assert cs[0].length == pytest.approx(expected, rel=1e-5)


def test_full_circle_incremental_and_units() -> None:
    cs = _run("G0 X5 Y0\nG2 I-5 J0\n")
    assert len(cs) == 1 and is_closed(cs[0])
    assert cs[0].length == pytest.approx(10 * math.pi, rel=1e-5)
    cs = _run("G91\nG0 X1 Y1\nG1 X2\nG1 Y2\nG90\nG1 X0 Y0\n")
    assert cs[0].start == (1.0, 1.0) and cs[0].end == (0.0, 0.0)
    assert cs[0].length == pytest.approx(4 + math.hypot(3, 3))
    cs = _run("G20\nG1 X1\n")
    assert cs[0].end == pytest.approx((25.4, 0.0))
    cs = _run("G92 X100 Y100\nG1 X110\n")
    assert cs[0].start == (100.0, 100.0) and cs[0].end == (110.0, 100.0)


def test_non_motion_words_are_ignored() -> None:
    r = read_gcode(b"G4 P100\nM07\nG1 X1 Z5 F100 S300\nM08\nT1\n")
    assert r.m_counts == {7: 1, 8: 1}
    assert len(r.document.graphs) == 1
    r = read_gcode(b"G65 X1\nG1 X2\n")
    assert any("G65" in w for w in r.warnings)
    with pytest.raises(GCodeError) as exc:
        read_gcode(b"G65 X1\n", GCodeImportOptions(strict=True))
    assert exc.value.message == g.ERR_FORMAT


# ---------------------------------------------------------------------------
# Subprograms and semantic errors (CADModule strings, 09 §3.7)
# ---------------------------------------------------------------------------


def test_subprogram_calls() -> None:
    prog = (
        "G0 X0 Y0\n"
        "L 1 3\n"
        "G0 X100 Y0\n"
        "L 2 1\n"
        "M02\n"
        "LP1\n"
        "G91\nG1 X10\nG0 X5\nG90\n"
        "M17\n"
        "; comment between functions\n"
        "LP2\n"
        "G1 Y10\n"
        "L 1 1\n"
        "M17\n"
    )
    r = read_gcode(prog.encode(), GCodeImportOptions(strict=True))
    assert r.subprograms == {1: 6, 2: 13}
    ends = [(c.start, c.end) for c in r.document.graphs]  # type: ignore[union-attr]
    assert ends[:3] == [((0, 0), (10, 0)), ((15, 0), (25, 0)), ((30, 0), (40, 0))]
    # LP2 cuts Y10 and then (no rapid in between) the X10 of LP1: one contour
    assert ends[3:] == [((100, 0), (110, 10))]


ERRORS = [
    ("M17\n", g.ERR_M17_IN_MAIN, 1),
    ("G1 X1\nLP1\nM17\nM02\n", g.ERR_LP_IN_MAIN, 2),
    ("M02\nG1 X1\n", g.ERR_NOT_IN_FUNCTION, 2),
    ("M02\nLP1\nM02\n", g.ERR_M02_IN_SUB, 3),
    ("M02\nLP1\nLP2\n", g.ERR_LP_IN_SUB, 3),
    ("M02\nLP1\nM17\nLP1\nM17\n", g.ERR_SUB_REDEFINED, 4),
    ("L 5 1\nM02\n", g.ERR_SUB_NOT_FOUND, 1),
    ("G2 X10 Y0 R2\n", g.ERR_ARC_RADIUS, 1),
    ("G2 X0 Y0 R2\n", g.ERR_ARC_CHORD, 1),
    ("G2 X10 Y0 I1 J1\n", g.ERR_ARC_RADIUS, 1),
]


@pytest.mark.parametrize(("prog", "message", "line"), ERRORS)
def test_semantic_errors(prog: str, message: str, line: int) -> None:
    with pytest.raises(GCodeError) as exc:
        read_gcode(prog.encode(), GCodeImportOptions(strict=True))
    assert exc.value.message == message
    assert exc.value.line == line


def test_error_strings_are_verbatim() -> None:
    assert g.ERR_FORMAT == "G code format is error!"
    assert g.ERR_ARC_CHORD == "Arc chord is too small!"
    assert g.ERR_ARC_RADIUS == "Arc radius is error!"
    assert g.ERR_SUB_NOT_FOUND == "sub-functions can not be found"


def test_recursion_guard() -> None:
    with pytest.raises(GCodeError):
        read_gcode(b"L 1 1\nM02\nLP1\nL 1 1\nM17\n")


def test_cp936_comments_and_chf_roundtrip(tmp_path: pytest.TempPathFactory) -> None:
    text = "; 金威刻logo\nG0 X0 Y0\nG1 X10 Y0\nG3 X10 Y10 I0 J5\nG1 X0 Y0\nM30\n"
    r = read_gcode(cp936.encode(text))
    assert len(r.document.graphs) == 1
    back = chf.read_chf(chf.write_chf(r.document))
    assert chf.check_document(back) == []
    c = back.graphs[0]
    assert isinstance(c, Contour) and [e.direction for e in c.elements] == [1, 1, 1]
