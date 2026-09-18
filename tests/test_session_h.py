"""Consumers of the Wine session H artefacts (``docs/WINE-SESSION-H.md``, STATUS §5 task 10).

Written before the session, so its output has somewhere to land. Every test that reads an
artefact takes the ``session_h_dir`` fixture (``NEXCUT_SESSION_H``) and skips when the
directory - or the one file it needs - is absent, so CI and a partly-run session stay green.
The file layout these tests expect is ``docs/WINE-SESSION-H.md`` §2.1.

* **H-1, the `SortType` goldens**: the graph order of each vendor-sorted ``h1-sort/*.chf``,
  matched back to ``h1-00-unsorted.chf``, must equal ``sort_graphs(unsorted, SortType.N)``.
* **H-1/H-2/H-4, byte identity**: every file the vendor wrote must survive the port's
  read -> write unchanged (the ninth and later ``.chf`` samples, the re-saved ``Bk*.xml`` and
  the technology preset - whose attribute set is the ``CutFreq`` verdict).
* **H-3, the X7 verdict**: the port keeps an unknown XML attribute exactly when the vendor does.

The consumers are plain functions, exercised on synthetic artefacts below, so they are known
to work before the first real file exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from nexcut.io import chf
from nexcut.io.params import (
    default_document,
    parse_params,
    parse_technology,
    preset_from_layer,
    serialize_params,
    serialize_technology,
)
from nexcut.model import ChfDocument, LwPolylineGlyph, LwPolyVertex, Vec2
from nexcut.ops import sort as sortmod
from nexcut.ops.import_gates import build_contour
from nexcut.ops.sort import SortType, sort_graphs

UNSORTED = "h1-sort/h1-00-unsorted.chf"
SORT_FILES: dict[SortType, str] = {
    SortType.LEFT_TO_RIGHT: "h1-sort/h1-00-left-to-right.chf",
    SortType.RIGHT_TO_LEFT: "h1-sort/h1-01-right-to-left.chf",
    SortType.BOTTOM_TO_TOP: "h1-sort/h1-02-bottom-to-top.chf",
    SortType.TOP_TO_BOTTOM: "h1-sort/h1-03-top-to-bottom.chf",
    SortType.NEAREST: "h1-sort/h1-04-nearest.chf",
    SortType.INSIDE_TO_OUTSIDE: "h1-sort/h1-05-inside-to-outside.chf",
    SortType.OUTSIDE_TO_INSIDE: "h1-sort/h1-06-outside-to-inside.chf",
    SortType.SMALL_FIRST: "h1-sort/h1-07-small-first.chf",
}
"""``docs/WINE-SESSION-H.md`` H-1 step 7, one file per ``SortType``."""

RESAVED_PARAMS: dict[str, str] = {
    "h2-m3-gate/h2-vendor-resaved-BkLayerPara.xml": "layer",
}
RESAVED_TECHNOLOGY = "h2-m3-gate/h2-vendor-resaved-technology-CO2.xml"
RESAVED_CHF = ("h1-sort/h1-00-unsorted.chf", "h2-m3-gate/h2-vendor-resaved-autosave.chf")
CHF_GLOBS = ("h1-sort/*.chf", "h4-crafts/*.chf")
X7_TAMPERED, X7_AFTER = "h3-x7/h3-tampered.xml", "h3-x7/h3-after.xml"
X7_PROBE = b'NexcutProbe="42"'


def _need(root: Path, rel: str) -> Path:
    path = root / rel
    if not path.is_file():
        pytest.skip(f"session H artefact {rel} not present under {root}")
    return path


# ---- the consumers --------------------------------------------------------------------------


def _key(g: object) -> tuple[float, ...]:
    lo, hi = sortmod._bbox(g)  # type: ignore[arg-type]
    return (round(lo[0], 3), round(lo[1], 3), round(hi[0], 3), round(hi[1], 3))


def vendor_order(unsorted: Sequence[object], sorted_: Sequence[object]) -> list[int]:
    """Index into ``unsorted`` of each graph of ``sorted_``, matched by bounding box.

    A bounding box does not change when a sort reverses an open contour or moves the start
    point of a closed one, which is all a sort may do to a graph. Equal boxes are matched in
    document order.
    """
    pool: dict[tuple[float, ...], list[int]] = {}
    for i, g in enumerate(unsorted):
        pool.setdefault(_key(g), []).append(i)
    out = []
    for g in sorted_:
        idx = pool.get(_key(g))
        assert idx, f"a graph with bbox {_key(g)} is not in the unsorted file"
        out.append(idx.pop(0))
    assert not any(pool.values()), "the sorted file lost graphs of the unsorted one"
    return out


def check_sort_golden(unsorted_path: Path, vendor_path: Path, sort_type: SortType) -> None:
    unsorted = chf.load_chf(unsorted_path)
    vendor = chf.load_chf(vendor_path)
    want = vendor_order(unsorted.graphs, vendor.graphs)
    got = sort_graphs(unsorted.graphs, sort_type).order
    assert got == want, f"SortType {sort_type.name}: port {got} != vendor {want}"


def check_chf_bytes(path: Path) -> None:
    data = path.read_bytes()
    assert chf.write_chf(chf.read_chf(data)) == data, f"{path.name} is not re-written unchanged"


def check_params_bytes(path: Path, kind: str) -> None:
    data = path.read_bytes()
    assert serialize_params(parse_params(data, kind)) == data, path.name


def check_technology_bytes(path: Path) -> None:
    """The ``CutFreq`` probe of H-2 step 3: the port writes every descriptor attribute, so a
    vendor re-save that drops ``CutFreq`` (or adds anything) fails here, by name."""
    data = path.read_bytes()
    assert serialize_technology(parse_technology(data)) == data, path.name


def x7_verdict(tampered: bytes, after: bytes) -> tuple[bool, bool]:
    """``(vendor keeps the probe, port keeps the probe)`` for the H-3 round trip."""
    assert X7_PROBE in tampered, "h3-tampered.xml does not carry the probe attribute"
    port = serialize_params(parse_params(tampered, "layer"))
    return X7_PROBE in after, X7_PROBE in port


# ---- the real artefacts (skipped when absent) -----------------------------------------------


@pytest.mark.parametrize("sort_type", list(SORT_FILES), ids=lambda s: s.name)
def test_vendor_sort_golden(session_h_dir: Path, sort_type: SortType) -> None:
    """H-1: replaces the unmeetable ``ManuContour.dat`` reference of the M2 gate (STATUS §1.3)."""
    check_sort_golden(
        _need(session_h_dir, UNSORTED), _need(session_h_dir, SORT_FILES[sort_type]), sort_type
    )


@pytest.mark.parametrize("rel", RESAVED_CHF)
def test_vendor_written_chf_is_rewritten_byte_identically(session_h_dir: Path, rel: str) -> None:
    check_chf_bytes(_need(session_h_dir, rel))


def test_every_session_h_chf_is_rewritten_byte_identically(session_h_dir: Path) -> None:
    """H-1 and H-4: every ``.chf`` the vendor wrote (the named samples plus whatever else)."""
    paths = sorted(p for g in CHF_GLOBS for p in session_h_dir.glob(g))
    if not paths:
        pytest.skip("no h1-sort/*.chf or h4-crafts/*.chf yet")
    failed = []
    for p in paths:
        try:
            check_chf_bytes(p)
        except (AssertionError, ValueError) as exc:
            failed.append(f"{p.relative_to(session_h_dir)}: {exc}")
    assert not failed, "\n".join(failed)


@pytest.mark.parametrize("rel", list(RESAVED_PARAMS))
def test_vendor_resaved_params_round_trip(session_h_dir: Path, rel: str) -> None:
    """H-2 step 2: the vendor's own re-save of a port-written ``BkLayerPara.xml``."""
    check_params_bytes(_need(session_h_dir, rel), RESAVED_PARAMS[rel])


def test_technology_preset_matches_vendor_attribute_set(session_h_dir: Path) -> None:
    """H-2 step 3, the ``CutFreq`` verdict (STATUS §1.4)."""
    check_technology_bytes(_need(session_h_dir, RESAVED_TECHNOLOGY))


def test_x7_port_keeps_unknown_attributes_exactly_when_the_vendor_does(
    session_h_dir: Path,
) -> None:
    """H-3. Today the port drops them (strict xfail X7,
    ``test_io_fidelity_review.py::test_unknown_attribute_survives_rewrite``). If the vendor
    drops them too, this passes, and X7 is fidelity rather than a defect: the strict xfail is
    then rewritten as a passing test citing ``h3-after.xml``. If the vendor keeps them, this
    fails, naming the verdict, until ``io/params`` keeps unknown attributes."""
    tampered = _need(session_h_dir, X7_TAMPERED).read_bytes()
    after = _need(session_h_dir, X7_AFTER).read_bytes()
    vendor_keeps, port_keeps = x7_verdict(tampered, after)
    assert port_keeps == vendor_keeps, (
        f"X7 verdict: the vendor {'keeps' if vendor_keeps else 'drops'} an unknown attribute, "
        f"the port {'keeps' if port_keeps else 'drops'} it"
    )


# ---- the consumers on synthetic artefacts (always run) ----------------------------------------


def _square(x: float, y: float, s: float) -> object:
    pts = [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]
    return build_contour([LwPolylineGlyph(1, [LwPolyVertex(Vec2(*p)) for p in pts])])


def _scrambled() -> ChfDocument:
    cells = [(2, 1), (0, 0), (1, 1), (2, 0), (0, 1), (1, 0)]
    return ChfDocument(graphs=[_square(10 * x, 10 * y, 1 + 0.1 * i) for i, (x, y) in enumerate(cells)])


def test_session_h_fixture_follows_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from conftest import DEFAULT_SESSION_H, session_h_path

    monkeypatch.delenv("NEXCUT_SESSION_H", raising=False)
    assert session_h_path() == DEFAULT_SESSION_H
    monkeypatch.setenv("NEXCUT_SESSION_H", str(tmp_path))
    assert session_h_path() == tmp_path


def test_sort_golden_consumer_on_a_synthetic_session(tmp_path: Path) -> None:
    doc = _scrambled()
    (tmp_path / "h1-sort").mkdir()
    unsorted = tmp_path / UNSORTED
    unsorted.write_bytes(chf.write_chf(doc))
    for st in (SortType.LEFT_TO_RIGHT, SortType.NEAREST, SortType.SMALL_FIRST):
        vendor = tmp_path / SORT_FILES[st]
        vendor.write_bytes(chf.write_chf(ChfDocument(graphs=sort_graphs(doc.graphs, st).graphs)))
        check_sort_golden(unsorted, vendor, st)
    # a vendor order the port does not produce is reported
    wrong = sort_graphs(doc.graphs, SortType.LEFT_TO_RIGHT).graphs[::-1]
    vendor = tmp_path / SORT_FILES[SortType.LEFT_TO_RIGHT]
    vendor.write_bytes(chf.write_chf(ChfDocument(graphs=wrong)))
    with pytest.raises(AssertionError, match="LEFT_TO_RIGHT"):
        check_sort_golden(unsorted, vendor, SortType.LEFT_TO_RIGHT)
    # and a file that lost a graph is not silently matched
    vendor.write_bytes(chf.write_chf(ChfDocument(graphs=doc.graphs[:-1])))
    with pytest.raises(AssertionError, match="lost graphs"):
        check_sort_golden(unsorted, vendor, SortType.LEFT_TO_RIGHT)


def test_byte_identity_consumers_on_port_written_files(tmp_path: Path) -> None:
    p = tmp_path / "a.chf"
    p.write_bytes(chf.write_chf(_scrambled()))
    check_chf_bytes(p)
    layer = default_document("layer")
    x = tmp_path / "BkLayerPara.xml"
    x.write_bytes(serialize_params(layer))
    check_params_bytes(x, "layer")
    t = tmp_path / "tech.xml"
    t.write_bytes(serialize_technology(preset_from_layer(layer, "co2", 2)))
    check_technology_bytes(t)
    # a vendor preset without CutFreq is the failing case the H-2 probe is looking for
    t.write_bytes(t.read_bytes().replace(b' CutFreq="5000"', b"", 1))
    assert b"CutFreq" not in t.read_bytes()
    with pytest.raises(AssertionError):
        check_technology_bytes(t)


def test_x7_verdict_on_synthetic_round_trips() -> None:
    raw = serialize_params(default_document("layer"))
    i = raw.index(b"<GP ")
    tampered = raw[: i + 4] + X7_PROBE + b" " + raw[i + 4 :]
    assert x7_verdict(tampered, tampered) == (True, False)  # vendor keeps, port drops: X7
    assert x7_verdict(tampered, raw) == (False, False)  # both drop: X7 is fidelity
