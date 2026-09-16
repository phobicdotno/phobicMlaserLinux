"""Adversarial file-fidelity review of nexcut.io.chf / nexcut.io.params (analysis 03, 01 §0, 02 §6).

Each test pins one finding of the review.  Tests marked ``xfail(strict=True)`` document a
divergence whose fix needs a design decision (or lives in a module owned elsewhere); they
start failing loudly the day the behaviour changes.  Vendor files are read (read-only)
through the ``src_dir`` fixture and skip when absent; golden numbers are copied here.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from nexcut.core.schema import format_double, parse_double, parse_int
from nexcut.io import chf, cp936
from nexcut.io.params import (
    default_document,
    escape_attribute,
    parse_params,
    parse_technology,
    serialize_params,
    serialize_technology,
)
from nexcut.model import (
    ChfDocument,
    Contour,
    ContourElement,
    ContourEx,
    LinkInfo,
    PointGlyph,
    SegmentGlyph,
    Text,
    Vec2,
)

V = Vec2


def _contour() -> Contour:
    return Contour(
        precision=0.01,
        length=1.0,
        bbox_max=V(1.0, 0.0),
        end=V(1.0, 0.0),
        elements=[ContourElement(SegmentGlyph(V(0.0, 0.0), V(1.0, 0.0)))],
    )


# ----------------------------------------------------------------------------- .chf


def test_read_int_saturates_like_msvcr100_atoi() -> None:
    """ReadInt -> msvcr100 atoi -> atol -> strtol(s, NULL, 10): saturates (chf._atoi docstring)."""
    data = chf.write_chf(ChfDocument(graphs=[_contour()]))
    data = data.replace(b"\r\n0\r\n1\r\n<Crafts>", b"\r\n3000000000\r\n-9999999999\r\n<Crafts>")
    doc = chf.read_chf(data)
    c = doc.graphs[0]
    assert isinstance(c, Contour)
    assert (c.layer, c.int58) == (2**31 - 1, -(2**31))
    # before the fix the value grew unbounded and write_chf raised ValueError
    assert b"\r\n2147483647\r\n-2147483648\r\n<Crafts>" in chf.write_chf(doc)


@pytest.mark.parametrize(
    ("text", "encoded"),
    [
        ("€", b"\x80"),  # cp936 single byte 0x80; Python's gbk codec rejects it
        ("É", b"\xa8\xa6"),  # best fit -> é (WideCharToMultiByte dwFlags=0, 0x100da097)
        ("²", b"2"),  # best fit
        ("£", b"\xa1\xea"),  # best fit -> fullwidth pound
        ("", b"\xaa\xa1"),  # user-defined area
        ("激光ABC", "激光ABC".encode("gbk")),
        ("aกb", b"a?b"),  # no mapping -> DefaultChar
    ],
)
def test_cp936_matches_windows_table(text: str, encoded: bytes) -> None:
    """Golden cells copied from Wine's c_936.nls (io/cp936.py provenance)."""
    assert cp936.encode(text) == encoded


def test_cp936_decode_extras_and_raw_bytes_round_trip() -> None:
    raw = b"\x80\xaa\xa1\xff\x81\x0dA"
    text = cp936.decode(raw)
    assert text.startswith("€")
    assert cp936.encode(text) == raw


def test_text_with_euro_survives_chf_round_trip() -> None:
    doc = ChfDocument(graphs=[Text(text="价格€5", font="宋体", outline=None)])
    data = chf.write_chf(doc, version=3)
    back = chf.read_chf(data)
    assert isinstance(back.graphs[0], Text) and back.graphs[0].text == "价格€5"
    assert chf.write_chf(back) == data


def test_link_point_raw_token_kept_on_rewrite() -> None:
    """03 §6.5: a point line the DLL does not parse (int0 != 1) is re-emitted verbatim."""
    grp = ContourEx(children=[_contour()], links=[LinkInfo(0, 1, 0.0, 0.0, 0.0, 0.0, V(0, 0), V(1, 2))])
    data = chf.write_chf(ChfDocument(graphs=[grp]))
    assert b"\r\n0.000000,0.000000\r\n1.000000,2.000000\r\n<End Graphs>" in data
    garbage = b"-92559631349317831000000000000000000000.000000,1.#QNAN0"
    data = data.replace(b"\r\n0.000000,0.000000\r\n1.000000,2.000000\r\n", b"\r\n" + garbage + b"\r\n1.0,2.0\r\n")
    back = chf.read_chf(data)
    assert chf.write_chf(back) == data
    # a changed point is written with WritePoint again
    back.graphs[0].links[0].pt28 = V(3.0, 4.0)  # type: ignore[union-attr]
    assert b"\r\n3.000000,4.000000\r\n<End Graphs>" in chf.write_chf(back)


@pytest.mark.xfail(
    strict=True,
    reason="design: v2-v4 reserved lines are skipped unread (03 §9); keeping non-empty ones "
    "needs a model slot or a strict-mode rejection",
)
def test_legacy_reserved_lines_with_content_are_not_lost() -> None:
    data = chf.write_chf(ChfDocument(version=4, graphs=[_contour()]))
    marker = b"<Begin Graphs>\r\n\r\n"
    assert marker in data
    tampered = data.replace(marker, b"<Begin Graphs>\r\nRESERVED\r\n", 1)
    assert chf.write_chf(chf.read_chf(tampered)) == tampered


def test_point_only_contour_negative_zero_point_round_trips() -> None:
    """WritePoint has no zero special case (03 §4.1); -0.0 survives a read/write cycle.

    UNVERIFIED: that MSVCR100 ``printf("%f", -0.0)`` prints the sign (not disassembled).
    """
    c = Contour(length=0.0, elements=[ContourElement(PointGlyph(V(-0.0, 0.0)))])
    data = chf.write_chf(ChfDocument(graphs=[c]))
    assert b"\r\n-0.000000,0.000000\r\n" in data
    assert chf.write_chf(chf.read_chf(data)) == data


def test_all_vendor_chf_files_rewrite_byte_identical(src_dir: Path) -> None:
    files = sorted(src_dir.rglob("*.chf"))
    assert len(files) == 8  # 03 §2
    for f in files:
        raw = f.read_bytes()
        assert chf.write_chf(chf.read_chf(raw)) == raw, f
        assert chf.write_chf(chf.read_chf(raw, strict=False)) == raw, f


@pytest.mark.parametrize(
    "name", ["Testfile SS1mm 2.0s F+1 N2.dxf", "pwmCompensation.txt", "Co2_pwmCompensation.txt"]
)
def test_non_chf_neighbours_raise_chf_error(src_dir: Path, name: str) -> None:
    path = src_dir.parent / name
    if not path.is_file():
        pytest.skip(f"{name} not present")
    for strict in (True, False):
        with pytest.raises(chf.ChfError):
            chf.read_chf(path.read_bytes(), strict=strict)


# ----------------------------------------------------------------------------- XML


def test_apostrophe_is_escaped_as_apos() -> None:
    """ParaModule x_EscapeText find-set <&>'\" (0x1001a870) -> &apos; (0x1001a8a0)."""
    assert escape_attribute("Tom's <1&2> \"x\"") == "Tom&apos;s &lt;1&amp;2&gt; &quot;x&quot;"
    doc = default_document("layer")
    doc.set("PLayerParam2", "GP", "LayerFileName", "SS 3' plate")
    out = serialize_params(doc)
    assert b'LayerFileName="SS 3&apos; plate"' in out
    assert parse_params(out, "layer") == doc


def test_raw_control_whitespace_written_raw_and_read_back() -> None:
    doc = default_document("layer")
    doc.set("PLayerParam2", "GP", "Note", "a\tb\r\nc")
    out = serialize_params(doc)
    assert b'Note="a\tb\r\nc"' in out
    assert parse_params(out, "layer").get("PLayerParam2", "GP", "Note") == "a\tb\r\nc"


def test_ansi_gbk_preset_loads_instead_of_failing() -> None:
    """CMarkup falls back to the ANSI code page for non-UTF-8 text (params._decode_xml)."""
    utf8 = serialize_technology(parse_technology(_preset_bytes("亚克力8mm")))
    gbk = utf8.decode("utf-8").encode("gbk")
    assert gbk != utf8
    assert parse_technology(gbk).layer_file_name == "亚克力8mm"
    declared = b'<?xml version="1.0" encoding="GB2312"?>\r\n' + gbk
    assert parse_technology(declared).layer_file_name == "亚克力8mm"
    bom = b"\xef\xbb\xbf" + utf8
    assert parse_technology(bom).layer_file_name == "亚克力8mm"


def _preset_bytes(label: str) -> bytes:
    doc = default_document("layer")
    doc.set("PCO2LayerParam11", "GP", "LayerFileName", label)
    from nexcut.io.params import preset_from_layer

    return serialize_technology(preset_from_layer(doc, "co2", 11))


@pytest.mark.xfail(
    strict=True,
    reason="design: unknown attributes are dropped on rewrite; ParaModule keeps a CMarkup DOM "
    "(set-value path 0x10010cdf FindElem/AddElem/SetAttrib) and may preserve them - UNVERIFIED",
)
def test_unknown_attribute_survives_rewrite() -> None:
    raw = serialize_params(default_document("hard")).replace(b"<AX ", b'<AX Future="7" ', 1)
    assert b'Future="7"' in serialize_params(parse_params(raw, "hard"))


def test_non_finite_doubles_use_boost_spelling() -> None:
    """core/schema.py (not io): ParaModule formats doubles via ``boost::lexical_cast``.

    ``put_inf_nan`` at ``0x1000ade0`` copies 3 wide characters from ``L"nan"``
    (``0x1001af24``) or ``L"infinity"`` (``0x1001af2c``) after an optional ``-``,
    before the ``swprintf("%.*g", 17)`` at ``0x1000ed37``; it never reaches the
    MSVCR100 ``1.#QNAN``/``1.#INF`` spellings this used to write (STATUS X8).
    """
    assert format_double(math.nan) == "nan"
    assert format_double(-math.nan) == "-nan"
    assert format_double(math.inf) == "inf"
    assert format_double(-math.inf) == "-inf"
    # and they read back, because parse_inf_nan accepts exactly these spellings
    for text in ("nan", "-nan", "inf", "-inf", "INFINITY", "+Inf", "nan(ind)"):
        assert parse_double(text)[1] is True, text
    assert math.isnan(parse_double("nan")[0]) and parse_double("-inf")[0] == -math.inf


def test_underscore_number_is_not_exact() -> None:
    """core/schema.py: only whole C++ float literals are ``exact`` (STATUS X9).

    Python ``float``/``int`` also accept ``1_0``, ``" 5"`` and a trailing NBSP;
    ``boost::lexical_cast`` (``0x1000d2c0``) extracts with ``skipws`` cleared and
    then demands WEOF (``0x1000bee3``), so any leftover character makes it throw.
    """
    assert parse_double("1_0")[1] is False
    assert parse_double("1_0")[0] == 1.0  # UNVERIFIED: the vendor throws bad_lexical_cast
    assert parse_int("1_0") == (1, False)
    for text in (" 5", "5 ", "5\u00a0", "0x10", "1.0E", "", "12abc"):
        assert parse_double(text)[1] is False, text
    for text in ("5", "-5", "+5", "0.5", ".5", "5.", "1e-020", "-1.5e+300"):
        assert parse_double(text)[1] is True, text
    # 'infinity' IS accepted here: parse_inf_nan_impl (0x1000af70) takes the
    # 8-character branch when exactly 8 characters remain after the sign.
    assert parse_double("infinity") == (math.inf, True)


def test_every_vendor_xml_round_trips(src_dir: Path) -> None:
    """All vendor XML: byte-identical, except 13 CO2 presets that gain CutFreq (02 §6.2)."""
    pkg = src_dir.parent
    kinds = {
        "BkHardPara.xml": "hard",
        "BkManuPara.xml": "manu",
        "SecondBkManuPara.xml": "manu",
        "BkLayerPara.xml": "layer",
        "1390backup.xml": "system_backup",
    }
    counted = {"params": 0, "same": 0, "cutfreq": 0}
    for f in sorted(pkg.rglob("*.xml")):
        if "Pc_Software" in f.parts:
            continue
        raw = f.read_bytes()
        if f.name in kinds:
            out = serialize_params(parse_params(raw, kinds[f.name]))
            assert out == raw, f
            counted["params"] += 1
            continue
        preset = parse_technology(raw)
        out = serialize_technology(preset)
        if out == raw:
            counted["same"] += 1
        else:
            assert preset.report.missing == ["PCO2LayerParam11/GP.CutFreq"], f
            assert out == raw.replace(b'"/>\r\n</PCO2', b'" CutFreq="5000"/>\r\n</PCO2'), f
            counted["cutfreq"] += 1
    if counted["cutfreq"] == 0:
        pytest.skip("vendor technology library not present")
    assert counted == {"params": 5, "same": 42, "cutfreq": 13}
