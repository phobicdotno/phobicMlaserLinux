"""The vectorised IGP gates and nearest sort give exactly what the per-item code gave (STATUS §5 task 7).

X11 (50 000 separate DXF ``LINE`` entities over the 3 s open budget) was closed by replacing the
per-item Python paths of ``nexcut.ops.import_gates`` (``remove_overlaps``, ``merge_connected``)
and ``nexcut.ops.sort`` (``_nearest``) with array passes.  None of those algorithms is verified
against the original (they are UNVERIFIED reconstructions), so the port's own previous output is
the definition: :mod:`gates_reference` is the pre-task-7 source, frozen, and every test here runs
both on the same input and requires

* the same graphs in the same order (dataclass equality, so every float is bit-identical),
* the same *identity* pattern (an untouched graph is passed through as the same object),
* the same removal / merge counts, sort order, reversal flags and notes.

Inputs: seeded random drawings built to hit the edge cases (endpoints on a coarse lattice so that
distances tie exactly, gaps of exactly the gate, reversed and shifted duplicates, nested closed
contours, points, arcs, bulge polylines, ellipse arcs, groups, scans, text outlines, two layers,
cached start/end that disagree with the elements), the 50 000-``LINE`` drawing of X11 itself, and
the eight vendor ``.chf`` samples when ``NEXCUT_SRC`` is available.
"""

from __future__ import annotations

import copy
import math
import random
from pathlib import Path

import pytest

# Sibling test modules (tests/ is on sys.path, see tests/test_plan_vectorised_equivalence.py).
import gates_reference as ref
from nexcut.io.chf import load_chf
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
from nexcut.model.glyph import EllipseArcGlyph
from nexcut.model.graph import ChfDocument, Graph, Scan, Text
from nexcut.ops import import_gates as ig
from nexcut.ops import sort as sortmod
from nexcut.ops.import_gates import GateParams, MergeType, build_contour
from nexcut.ops.sort import SortType

VENDOR_CHF = (
    "File/autosave.chf",
    "File/Temp/tempGraph.chf",
    "Graph/Work1/1.chf",
    "Graph/Work1/2.chf",
    "Graph/Work1/3.chf",
    "Graph/Work2/1.chf",
    "Graph/Work2/2.chf",
    "Graph/Work2/3.chf",
)


# ================================================================ random drawings
def _pt(rng: random.Random, lattice: float, span: int, jitter: float) -> Vec2:
    x = rng.randrange(span) * lattice
    y = rng.randrange(span) * lattice
    if jitter and rng.random() < 0.5:
        x += rng.choice((-1.0, 1.0)) * jitter * rng.choice((0.5, 1.0, 1.0, 1.5, 3.0))
    return Vec2(x, y)


def _square(x: float, y: float, s: float, closed: bool = True) -> LwPolylineGlyph:
    pts = [(x, y), (x + s, y), (x + s, y + s), (x, y + s)]
    return LwPolylineGlyph(1 if closed else 0, [LwPolyVertex(Vec2(*p)) for p in pts])


def _contour(rng: random.Random, lattice: float, span: int, jitter: float) -> Contour:
    r = rng.random()
    layer = rng.choice((0, 0, 0, 1))
    if r < 0.45:
        glyphs: list = [
            SegmentGlyph(_pt(rng, lattice, span, jitter), _pt(rng, lattice, span, jitter))
        ]
    elif r < 0.55:  # a chain of 2-3 segments
        pts = [_pt(rng, lattice, span, jitter) for _ in range(rng.choice((3, 4)))]
        glyphs = [SegmentGlyph(a, b) for a, b in zip(pts, pts[1:], strict=False)]
    elif r < 0.63:
        a0 = rng.randrange(8) * math.pi / 4
        glyphs = [
            ArcGlyph(
                _pt(rng, lattice, span, 0.0),
                lattice * rng.choice((0.5, 1.0, 2.0)),
                a0,
                a0 + rng.choice((-1, 1)) * rng.randrange(1, 8) * math.pi / 4,
            )
        ]
    elif r < 0.68:
        glyphs = [CircleGlyph(_pt(rng, lattice, span, 0.0), lattice * rng.choice((0.25, 1.0, 3.0)))]
    elif r < 0.76:
        c = _pt(rng, lattice, span, 0.0)
        s = lattice * rng.choice((1.0, 2.0, 4.0, 8.0))
        glyphs = [_square(c[0] - s / 2, c[1] - s / 2, s, closed=rng.random() < 0.8)]
    elif r < 0.81:
        p = _pt(rng, lattice, span, 0.0)
        q = _pt(rng, lattice, span, jitter)
        glyphs = [
            LwPolylineGlyph(
                rng.choice((0, 1)),
                [
                    LwPolyVertex(p, rng.choice((0.0, 0.5, -1.0))),
                    LwPolyVertex(q, 0.3),
                    LwPolyVertex(Vec2(q[0], p[1])),
                ],
            )
        ]
    elif r < 0.85:
        glyphs = [PointGlyph(_pt(rng, lattice, span, 0.0))]
    elif r < 0.88:
        glyphs = [
            EllipseArcGlyph(
                _pt(rng, lattice, span, 0.0),
                Vec2(lattice, 0.0),
                0.5,
                0.0,
                rng.choice((math.pi, 2 * math.pi)),
            )
        ]
    else:  # a segment and its neighbour touching within / at / beyond the gate
        a = _pt(rng, lattice, span, 0.0)
        b = _pt(rng, lattice, span, 0.0)
        glyphs = [SegmentGlyph(a, b)]
    c = build_contour(glyphs, layer=layer, precision=rng.choice((0.01, 0.01, 0.1)))
    if rng.random() < 0.15:  # flip an element: cached start/end now disagree with the elements
        el = c.elements[0]
        c.elements[0] = ContourElement(el.glyph, -el.direction)
    return c


def random_graphs(
    seed: int, n: int, *, lattice: float = 1.0, span: int = 12, jitter: float = 0.01
) -> list[Graph]:
    """A drawing that exercises every branch of the gates and the sorts (module docstring)."""
    rng = random.Random(seed)
    out: list[Graph] = []
    for _ in range(n):
        r = rng.random()
        if out and r < 0.12:  # duplicate an earlier contour: same, reversed, or shifted
            src = rng.choice([g for g in out if isinstance(g, Contour)] or [None])
            if src is not None:
                dup = copy.deepcopy(src)
                if rng.random() < 0.5:
                    dup = ig.reverse_contour(dup)
                if rng.random() < 0.3:
                    d = rng.choice((0.004, 0.01, 0.02))
                    dup = build_contour(
                        [
                            SegmentGlyph(
                                Vec2(e.glyph.p0[0] + d, e.glyph.p0[1]),
                                Vec2(e.glyph.p1[0] + d, e.glyph.p1[1]),
                            )
                            if isinstance(e.glyph, SegmentGlyph)
                            else e.glyph
                            for e in dup.elements
                        ],
                        layer=dup.layer,
                    )
                out.append(dup)
                continue
        if r < 0.17:
            kids = [_contour(rng, lattice, span, jitter) for _ in range(rng.randrange(0, 4))]
            g = Scan(children=kids) if rng.random() < 0.3 else Group(children=kids)
            out.append(ig.refresh_group(g))
        elif r < 0.19:
            outline = ig.refresh_group(Group(children=[_contour(rng, lattice, span, jitter)]))
            out.append(
                Text(
                    position=_pt(rng, lattice, span, 0.0),
                    outline=outline if rng.random() < 0.8 else None,
                )
            )
        elif r < 0.22:  # a nested pair of closed squares
            x, y = _pt(rng, lattice, span, 0.0)
            out.append(build_contour([_square(x, y, 4 * lattice)]))
            out.append(build_contour([_square(x + lattice, y + lattice, lattice)]))
        else:
            out.append(_contour(rng, lattice, span, jitter))
    return out


def segment_grid(n: int, cols: int = 250) -> list[Graph]:
    """The X11 drawing: ``tests/test_import_ui_fidelity_review._segment_grid``."""
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


# ================================================================ comparison
def _identity(out: list, inp: list) -> list[int]:
    pos = {id(g): i for i, g in enumerate(inp)}
    return [pos.get(id(g), -1) for g in out]


def _run_both(fn_new, fn_ref, graphs: list[Graph], *args, **kwargs):  # type: ignore[no-untyped-def]
    a_in = copy.deepcopy(graphs)
    b_in = copy.deepcopy(graphs)
    a = fn_new(a_in, *args, **kwargs)
    b = fn_ref(b_in, *args, **kwargs)
    return a, a_in, b, b_in


def assert_same_gate(fn_new, fn_ref, graphs: list[Graph], *args) -> None:  # type: ignore[no-untyped-def]
    (a_out, a_n), a_in, (b_out, b_n), b_in = _run_both(fn_new, fn_ref, graphs, *args)
    assert a_n == b_n
    assert len(a_out) == len(b_out)
    assert _identity(a_out, a_in) == _identity(b_out, b_in)
    assert a_out == b_out
    assert a_in == b_in  # neither mutated its input differently


def assert_same_sort(graphs: list[Graph], st: int, **kwargs: object) -> None:
    a, a_in, b, b_in = _run_both(sortmod.sort_graphs, ref.sort_graphs_ref, graphs, st, **kwargs)
    assert a.order == b.order
    assert a.reversed == b.reversed
    assert a.notes == b.notes
    assert _identity(a.graphs, a_in) == _identity(b.graphs, b_in)
    assert a.graphs == b.graphs


def assert_same_pipeline(graphs: list[Graph], params: GateParams | None) -> None:
    da = ChfDocument(graphs=copy.deepcopy(graphs))
    db = ChfDocument(graphs=copy.deepcopy(graphs))
    ra = ig.apply_import_gates(da, params)
    rb = ref.apply_import_gates_ref(db, params)
    assert ra == rb
    assert da.graphs == db.graphs


def assert_same_everything(
    graphs: list[Graph], gates: tuple[float, ...] = (0.01, 0.1, 1.0)
) -> None:
    for gate in gates:
        assert_same_gate(ig.remove_overlaps, ref.remove_overlaps_ref, graphs, gate)
        for mt in (
            MergeType.NONE,
            MergeType.DIRECTION_FIRST,
            MergeType.LENGTH_FIRST,
            MergeType.DISTANCE_FIRST,
        ):
            assert_same_gate(ig.merge_connected, ref.merge_connected_ref, graphs, gate, mt)
    for st in SortType:
        assert_same_sort(graphs, st)
    assert_same_sort(graphs, SortType.NEAREST, allow_reverse=False)
    assert_same_sort(graphs, SortType.NEAREST, inner_first=False)
    assert_same_sort(graphs, SortType.NEAREST, start=Vec2(5.5, 5.0))
    assert_same_sort(graphs, SortType.INSIDE_TO_OUTSIDE, allow_reverse=False, start=Vec2(3.0, 3.0))
    assert_same_pipeline(graphs, None)
    for mt in (MergeType.LENGTH_FIRST, MergeType.DISTANCE_FIRST):
        for auto in (0, 5, 6, 7):
            assert_same_pipeline(
                graphs,
                GateParams(
                    merge_type=mt,
                    auto_sort_type=auto,
                    overlap_gate=0.1,
                    connect_gate=0.1,
                    micro_gate=0.5,
                ),
            )


# ================================================================ tests
def test_reference_is_the_frozen_pre_task_7_code() -> None:
    """The reference must not be wired to the new code it checks."""
    assert ref.sort_graphs_ref is not sortmod.sort_graphs
    assert ref.merge_connected_ref is not ig.merge_connected
    assert ref.remove_overlaps_ref is not ig.remove_overlaps


@pytest.mark.parametrize("seed", range(40))
def test_random_small_drawings(seed: int) -> None:
    graphs = random_graphs(seed, 5 + seed * 2, jitter=(0.0, 0.01, 0.005, 0.1)[seed % 4])
    assert_same_everything(graphs)


@pytest.mark.parametrize("seed", range(3))
def test_random_dense_drawings_with_exact_ties(seed: int) -> None:
    """Hundreds of graphs on a 6 x 6 lattice: exact distance ties, many duplicates, long chains."""
    graphs = random_graphs(1000 + seed, 400, span=6, jitter=(0.0, 0.01)[seed % 2])
    assert_same_everything(graphs, gates=(0.01, 0.5))


@pytest.mark.parametrize("seed", range(2))
def test_random_large_drawings(seed: int) -> None:
    """Thousands of graphs: the nearest sort exhausts its precomputed neighbours and falls back."""
    graphs = random_graphs(2000 + seed, 2000, span=50, jitter=0.01)
    for gate in (0.01, 0.1):
        assert_same_gate(ig.remove_overlaps, ref.remove_overlaps_ref, graphs, gate)
        assert_same_gate(
            ig.merge_connected, ref.merge_connected_ref, graphs, gate, MergeType.DIRECTION_FIRST
        )
    for st in (SortType.NEAREST, SortType.INSIDE_TO_OUTSIDE, SortType.OUTSIDE_TO_INSIDE):
        assert_same_sort(graphs, st)
    assert_same_sort(graphs, SortType.NEAREST, allow_reverse=False)
    assert_same_pipeline(graphs, None)


def test_point_cloud_with_exact_ties() -> None:
    """Single-point contours on a unit lattice: every step of the nearest sort is a tie."""
    graphs: list[Graph] = [
        build_contour([PointGlyph(Vec2(float(i % 40), float(i // 40)))]) for i in range(1600)
    ]
    random.Random(7).shuffle(graphs)
    assert_same_sort(graphs, SortType.NEAREST)
    assert_same_sort(graphs, SortType.NEAREST, start=Vec2(19.5, 19.5))


def test_segments_touching_exactly_at_the_gate() -> None:
    gate = 0.01
    graphs: list[Graph] = []
    for i in range(300):
        x = i * (1.0 + gate)  # gap of exactly the gate (up to rounding) between neighbours
        graphs.append(build_contour([SegmentGlyph(Vec2(x, 0.0), Vec2(x + 1.0, 0.0))]))
        graphs.append(build_contour([SegmentGlyph(Vec2(x + 1.0, 5.0), Vec2(x, 5.0))]))
    assert_same_everything(graphs, gates=(gate, gate * (1 - 1e-12), gate * (1 + 1e-12)))


def test_the_x11_drawing() -> None:
    """The 50 000 separate ``LINE``s of X11: gates + nearest sort, old against new."""
    da = ChfDocument(graphs=segment_grid(50_000))
    db = ChfDocument(graphs=segment_grid(50_000))
    ra = ig.apply_import_gates(da)
    rb = ref.apply_import_gates_ref(db)
    assert ra == rb
    assert da.graphs == db.graphs
    # the same drawing with every other line reversed and a duplicate of every 7th
    graphs = segment_grid(10_000)
    graphs = [ig.reverse_contour(g) if i % 2 else g for i, g in enumerate(graphs)]  # type: ignore[arg-type]
    graphs += [ig.reverse_contour(g) for g in graphs[::7]]  # type: ignore[arg-type]
    assert_same_pipeline(graphs, None)
    assert_same_pipeline(graphs, GateParams(merge_type=MergeType.DISTANCE_FIRST, connect_gate=2.0))


@pytest.mark.parametrize("rel", VENDOR_CHF)
def test_vendor_samples(src_dir: Path, rel: str) -> None:
    doc = load_chf(src_dir / rel)
    assert_same_everything(list(doc.graphs))
