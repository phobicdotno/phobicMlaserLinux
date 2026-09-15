"""Tests of nexcut.ops.sort (GRP.SortType / IGP.AutoSortType) and nexcut.ops.import_gates.

Golden data: ``File/autosave.chf`` (24 segments) and ``File/ManuContour.dat``
(``24, 0..23``) - 01 §6.2, A7 §4.1.  They show that the job was cut in the
array-copy order, which no sort strategy reproduces (documented GAP in
``nexcut.ops.sort``); the test pins that finding so a future capture of a
sorted job can replace it.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from nexcut.io import chf
from nexcut.io.params import default_document
from nexcut.model import (
    ArcGlyph,
    CircleGlyph,
    Contour,
    ContourElement,
    Group,
    LwPolylineGlyph,
    LwPolyVertex,
    PointGlyph,
    SegmentGlyph,
    Vec2,
)
from nexcut.model.flatten import check_contour
from nexcut.ops import sort as sortmod
from nexcut.ops.import_gates import (
    GateParams,
    MergeType,
    apply_import_gates,
    build_contour,
    contour_endpoints,
    element_tangents,
    filter_micro_graphs,
    glyph_tangents,
    is_closed,
    merge_connected,
    remove_overlaps,
    reverse_contour,
)
from nexcut.ops.sort import AutoSortType, SortType, sort_document, sort_graphs


def _seg(x0: float, y0: float, x1: float, y1: float, layer: int = 0) -> Contour:
    return build_contour([SegmentGlyph(Vec2(x0, y0), Vec2(x1, y1))], layer=layer)


def _square(x: float, y: float, s: float) -> Contour:
    pts = [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]
    return build_contour([LwPolylineGlyph(1, [LwPolyVertex(Vec2(*p)) for p in pts])])


def _travel(graphs: list, start: tuple[float, float] = (0.0, 0.0)) -> float:
    pos, total = start, 0.0
    for g in graphs:
        total += math.dist(pos, g.start)
        pos = g.end
    return total


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_enum_values_01_1_2() -> None:
    assert [s.value for s in SortType] == list(range(8))
    assert SortType.NEAREST == 4 and SortType.SMALL_FIRST == 7
    assert AutoSortType.NEAREST.to_sort_type() is SortType.NEAREST
    assert AutoSortType.OUTSIDE_TO_INSIDE.to_sort_type() is SortType.OUTSIDE_TO_INSIDE
    assert AutoSortType.LEFT_TO_RIGHT.to_sort_type() is SortType.LEFT_TO_RIGHT
    with pytest.raises(ValueError):
        AutoSortType.NONE.to_sort_type()


# ---------------------------------------------------------------------------
# Golden: autosave.chf / ManuContour.dat
# ---------------------------------------------------------------------------


def _scflie_ints(data: bytes) -> list[int]:
    lines = data.decode("ascii").split("\r\n")
    assert lines[0] == "scFlie" and lines[-2] == "eof"
    return [int(v) for v in lines[1:-2]]


@pytest.fixture(scope="module")
def autosave(src_dir: Path) -> tuple[chf.ChfDocument, list[int]]:
    doc = chf.load_chf(src_dir / "File" / "autosave.chf")
    manu = _scflie_ints((src_dir / "File" / "ManuContour.dat").read_bytes())
    return doc, manu


def test_manu_contour_is_document_order_of_the_array(
    autosave: tuple[chf.ChfDocument, list[int]],
) -> None:
    doc, manu = autosave
    assert manu[0] == 24 and manu[1:] == list(range(24))
    assert len(doc.graphs) == 24
    # row-serpentine array (GRP.ArrayRowNum=8, ArrayColNum=3, RowDir up, ColDir right)
    cols = sorted({round(g.bbox_min[0], 3) for g in doc.graphs})
    assert len(cols) == 3
    for i, g in enumerate(doc.graphs):
        row, pos = divmod(i, 3)
        col = pos if row % 2 == 0 else 2 - pos
        assert round(g.bbox_min[0], 3) == cols[col]
        assert g.bbox_min[1] == pytest.approx(302.907 + row * 37.377906, abs=1e-5)


def test_nearest_sort_does_not_reproduce_manu_contour(
    autosave: tuple[chf.ChfDocument, list[int]],
) -> None:
    """GAP (nexcut.ops.sort.GAPS): SortType 4 gives another order; pinned as regression."""
    doc, manu = autosave
    r = sort_graphs(doc.graphs, SortType.NEAREST)
    assert r.order != manu[1:]
    assert r.order == [
        0,
        4,
        8,
        9,
        7,
        5,
        6,
        10,
        14,
        15,
        13,
        11,
        12,
        16,
        20,
        21,
        19,
        17,
        18,
        22,
        3,
        2,
        1,
        23,
    ]
    assert r.reversed == [False, False, False, True, True, True] * 4
    fixed = sort_graphs(doc.graphs, SortType.NEAREST, allow_reverse=False)
    assert fixed.order != manu[1:] and not any(fixed.reversed)
    assert _travel(doc.graphs) == pytest.approx(7307.7485, abs=1e-3)
    assert _travel(r.graphs) == pytest.approx(1395.9919, abs=1e-3)
    assert sortmod.GAPS


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


def _grid() -> list[Contour]:
    # 3 x 2 grid of unit squares, listed in scrambled order
    cells = [(2, 1), (0, 0), (1, 1), (2, 0), (0, 1), (1, 0)]
    return [_square(10 * x, 10 * y, 1) for x, y in cells]


def _cells(graphs: list) -> list[tuple[int, int]]:
    return [(round(g.bbox_min[0] / 10), round(g.bbox_min[1] / 10)) for g in graphs]


def test_directional_sorts() -> None:
    g = _grid()
    assert _cells(sort_graphs(g, SortType.LEFT_TO_RIGHT).graphs) == [
        (0, 0),
        (0, 1),
        (1, 0),
        (1, 1),
        (2, 0),
        (2, 1),
    ]
    assert _cells(sort_graphs(g, SortType.RIGHT_TO_LEFT).graphs) == [
        (2, 0),
        (2, 1),
        (1, 0),
        (1, 1),
        (0, 0),
        (0, 1),
    ]
    assert _cells(sort_graphs(g, SortType.BOTTOM_TO_TOP).graphs) == [
        (0, 0),
        (1, 0),
        (2, 0),
        (0, 1),
        (1, 1),
        (2, 1),
    ]
    assert _cells(sort_graphs(g, SortType.TOP_TO_BOTTOM).graphs) == [
        (0, 1),
        (1, 1),
        (2, 1),
        (0, 0),
        (1, 0),
        (2, 0),
    ]


def test_nearest_open_contour_reversal() -> None:
    a = _seg(0, 0, 10, 0)
    b = _seg(20, 0, 10.5, 0)  # its end is next to a's end
    c = _seg(0, 5, 20, 5)
    r = sort_graphs([c, b, a], SortType.NEAREST)
    assert r.order == [2, 1, 0]
    assert r.reversed == [False, True, True]  # b entered at (10.5, 0), c at (20, 5)
    assert r.graphs[1].start == (10.5, 0.0)
    r = sort_graphs([a, b, c], SortType.NEAREST, start=Vec2(20, 0))
    assert r.order[0] == 1 and not r.reversed[0]
    rev = sort_graphs([b], SortType.NEAREST, start=Vec2(10, 0))
    assert rev.reversed == [True]
    assert rev.graphs[0].start == pytest.approx((10.5, 0.0))
    assert rev.graphs[0].elements[0].direction == -1  # geometry kept, flag flipped (03 §6.1)
    assert rev.notes


def test_nearest_inner_first_and_containment() -> None:
    outer = _square(0, 0, 100)
    hole1 = _square(80, 80, 5)
    hole2 = build_contour([CircleGlyph(Vec2(10, 10), 3)])
    far = _square(300, 0, 10)
    graphs = [outer, far, hole1, hole2]
    depth, inside = sortmod.containment_depths(graphs)
    assert depth == [0, 0, 1, 1]
    assert inside[0] == {2, 3}
    r = sort_graphs(graphs, SortType.NEAREST)
    assert r.order.index(0) > r.order.index(2) and r.order.index(0) > r.order.index(3)
    free = sort_graphs(graphs, SortType.NEAREST, inner_first=False)
    assert free.order[0] == 0
    assert sort_graphs(graphs, SortType.INSIDE_TO_OUTSIDE).order[:2] in ([2, 3], [3, 2])
    assert sort_graphs(graphs, SortType.OUTSIDE_TO_INSIDE).order[2:] in ([2, 3], [3, 2])


def test_small_first_and_document_sort() -> None:
    graphs = [_square(0, 0, 5), _square(10, 0, 1), _square(20, 0, 3)]
    assert sort_graphs(graphs, SortType.SMALL_FIRST).order == [1, 2, 0]
    doc = chf.ChfDocument(graphs=list(graphs))
    res = sort_document(doc, SortType.SMALL_FIRST)
    assert doc.graphs == res.graphs and doc.graphs[0] is graphs[1]


def test_sort_handles_groups() -> None:
    grp = Group(
        children=[_square(50, 50, 2)],
        bbox_min=Vec2(50, 50),
        bbox_max=Vec2(52, 52),
        start=Vec2(50, 50),
        end=Vec2(50, 50),
    )
    r = sort_graphs([grp, _square(0, 0, 1)], SortType.NEAREST)
    assert r.order == [1, 0] and r.reversed == [False, False]


# ---------------------------------------------------------------------------
# Geometry helpers of the importers
# ---------------------------------------------------------------------------


def test_tangents_and_reverse() -> None:
    arc = ArcGlyph(Vec2(0, 0), 1.0, 0.0, math.pi / 2)
    t0, t1 = glyph_tangents(arc)
    assert t0 == pytest.approx((0, 1)) and t1 == pytest.approx((-1, 0))
    poly = LwPolylineGlyph(0, [LwPolyVertex(Vec2(0, 0), 1.0), LwPolyVertex(Vec2(2, 0))])
    t0, t1 = glyph_tangents(poly)  # CCW semicircle below the chord
    assert t0 == pytest.approx((0, -1)) and t1 == pytest.approx((0, 1))
    c = build_contour([SegmentGlyph(Vec2(0, 0), Vec2(1, 0)), arc])
    rc = reverse_contour(c)
    s0, e0 = contour_endpoints(rc)
    assert s0 == pytest.approx((0.0, 1.0)) and e0 == pytest.approx((0.0, 0.0))
    assert element_tangents(rc.elements[0])[0] == pytest.approx((1, 0))
    assert check_contour(rc) == []


def test_is_closed_uses_precision() -> None:
    c = _seg(0, 0, 0.005, 0)
    assert is_closed(c)  # precision 0.01 (03 §6.1 line 1)
    assert not is_closed(_seg(0, 0, 1, 0))
    assert not is_closed(build_contour([PointGlyph(Vec2(1, 1))]))


# ---------------------------------------------------------------------------
# Import gates (IGP.*)
# ---------------------------------------------------------------------------


def test_gate_params_from_documents() -> None:
    p = GateParams()
    assert (p.micro_gate, p.overlap_gate, p.connect_gate) == (0.01, 0.01, 0.01)  # BkManuPara.xml
    assert (p.merge_type, p.auto_sort_type) == (1, 5)
    q = GateParams.from_params(default_document("manu"))
    assert (q.micro_gate, q.overlap_gate, q.connect_gate) == (0.1, 0.1, 0.1)  # descriptor defaults
    assert q.filter_micro and q.filter_overlap and q.merge_type == 1 and q.auto_sort_type == 5
    m = GateParams.from_params({"IGP.MegerConnectGraphType": "3", "IGP.IsFilteOverlapGraph": 0})
    assert m.merge_type == 3 and not m.filter_overlap and m.connect_gate == 0.01


def test_remove_overlaps() -> None:
    a = _seg(0, 0, 10, 0)
    dup_reversed = _seg(10.004, 0.003, 0, 0)
    near_miss = _seg(0, 0.05, 10, 0.05)
    circ = build_contour([CircleGlyph(Vec2(50, 50), 5)])
    # circle again, written as a closed 2-vertex bulge polyline
    circ_poly = build_contour(
        [LwPolylineGlyph(1, [LwPolyVertex(Vec2(55, 50), 1.0), LwPolyVertex(Vec2(45, 50), 1.0)])]
    )
    chain = build_contour(
        [
            SegmentGlyph(Vec2(20, 0), Vec2(30, 0)),
            SegmentGlyph(Vec2(0, 0), Vec2(10, 0)),
            SegmentGlyph(Vec2(30, 0), Vec2(40, 0)),
        ]
    )
    out, removed = remove_overlaps([a, dup_reversed, near_miss, circ, circ_poly, chain], 0.01)
    assert removed == 3
    assert out[0] is a and out[1] is near_miss and out[2] is circ
    # the chain lost its middle segment and was split into two runs
    assert [(c.start, c.end) for c in out[3:]] == [((20, 0), (30, 0)), ((30, 0), (40, 0))]
    for c in out:
        assert check_contour(c) == []


def _star() -> list[Contour]:
    """Three segments meeting the end of ``base`` at (10, 0)."""
    base = _seg(0, 0, 10, 0)
    straight_short = _seg(10, 0, 15, 0.2)  # smallest turn
    long_turn = _seg(10.004, 0, 10, 40)  # longest, 90 deg
    close_turn = _seg(10, 0, 12, -3)  # exact gap 0, 56 deg
    return [base, long_turn, close_turn, straight_short]


def test_merge_direction_length_distance() -> None:
    out, merges = merge_connected(_star(), 0.01, MergeType.DIRECTION_FIRST)
    assert merges == 2 and len(out) == 2  # the two left-over segments touch each other too
    assert out[0].end == pytest.approx((15, 0.2))
    out, _ = merge_connected(_star(), 0.01, MergeType.LENGTH_FIRST)
    assert out[0].end == pytest.approx((10, 40))
    out, _ = merge_connected(_star(), 0.01, MergeType.DISTANCE_FIRST)
    assert out[0].end == pytest.approx((12, -3))
    out, merges = merge_connected(_star(), 0.01, MergeType.NONE)
    assert merges == 0 and len(out) == 4


def test_merge_reverses_and_closes_and_respects_layers() -> None:
    parts = [_seg(0, 0, 10, 0), _seg(10, 10, 10, 0), _seg(0, 10, 10, 10), _seg(0, 0, 0, 10)]
    out, merges = merge_connected(parts, 0.01)
    assert merges == 3 and len(out) == 1
    loop = out[0]
    assert is_closed(loop)
    assert loop.length == pytest.approx(40.0)
    assert [e.direction for e in loop.elements] == [1, -1, -1, -1]
    assert check_contour(loop) == []
    other_layer = [_seg(0, 0, 10, 0), _seg(10, 0, 20, 0, layer=1)]
    assert merge_connected(other_layer, 0.01)[1] == 0
    closed = [_square(0, 0, 1), _seg(0, 0, -5, 0)]
    assert merge_connected(closed, 0.01)[1] == 0
    backwards = [_seg(5, 0, 10, 0), _seg(0, 0, 5, 0)]
    out, merges = merge_connected(backwards, 0.01)
    assert merges == 1 and out[0].start == (0.0, 0.0) and out[0].end == (10.0, 0.0)


def test_filter_micro_graphs() -> None:
    tiny = _seg(0, 0, 0.005, 0)
    point = build_contour([PointGlyph(Vec2(3, 3))])
    ok = _seg(0, 0, 1, 0)
    grp = Group(children=[_seg(5, 5, 5.001, 5), _seg(6, 6, 7, 6)])
    empty_grp = Group(children=[_seg(8, 8, 8.001, 8)])
    out, removed = filter_micro_graphs([tiny, point, ok, grp, empty_grp], 0.01)
    assert removed == 3
    assert out[0] is point and out[1] is ok
    assert isinstance(out[2], Group) and len(out[2].children) == 1
    assert out[2].length == pytest.approx(1.0)


def test_apply_import_gates_pipeline() -> None:
    parts = [
        _seg(0, 0, 10, 0),
        _seg(10, 0, 10, 10),
        _seg(10, 0, 10, 10),  # duplicate
        _seg(10, 10, 0, 10),
        _seg(0, 10, 0, 0),
        _seg(50, 50, 50.001, 50),  # minimal graphic
        build_contour([CircleGlyph(Vec2(5, 5), 1)]),  # hole inside the merged square
    ]
    doc = chf.ChfDocument(graphs=parts)
    report = apply_import_gates(doc, GateParams())
    assert (report.overlaps_removed, report.contours_merged, report.micro_removed) == (1, 3, 1)
    assert report.sorted_by == 5
    assert len(doc.graphs) == 2
    assert isinstance(doc.graphs[0].elements[0].glyph, CircleGlyph)  # inner first
    assert is_closed(doc.graphs[1]) and doc.graphs[1].length == pytest.approx(40.0)
    doc2 = chf.ChfDocument(graphs=list(parts))
    r2 = apply_import_gates(
        doc2, GateParams(filter_overlap=False, merge_type=0, filter_micro=False, auto_sort_type=0)
    )
    assert r2 == type(r2)() and doc2.graphs == parts
    back = chf.read_chf(chf.write_chf(doc))
    assert chf.check_document(back) == []


def test_contour_element_direction_roundtrip() -> None:
    c = build_contour([ContourElement(ArcGlyph(Vec2(0, 0), 2, 0, math.pi), -1)])
    assert c.start == pytest.approx((-2, 0)) and c.end == pytest.approx((2, 0))
