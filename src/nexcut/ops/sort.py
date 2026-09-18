"""Cut-order sorting of the graphs of a document (``GRP.SortType`` / ``IGP.AutoSortType``).

Enums (EVIDENCE, 01 §1.2 parameter tables, labels from ``Lang/lang.txt``):

* ``GRP.SortType`` (排序参数.排序策略, value 4 on this machine): 0 left to right,
  1 right to left, 2 bottom to top, 3 top to bottom, 4 nearest (局部最短路径 "local
  shortest path"), 5 inside to outside, 6 outside to inside, 7 small graphics first.
* ``IGP.AutoSortType`` (自动排序, value 5): 0 none, then the same strategies shifted
  by one (1 left to right ... 5 nearest, 6 in->out, 7 out->in; no "small first").

The implementing classes are ``COpAutoSortCmd/CSSort/CRingSort/CPathLinkerPlan``
(09 §3.7) - none was disassembled, so the algorithms below are reconstructions
(UNVERIFIED, see :data:`UNVERIFIED`).

The one piece of golden data is not enough to validate any strategy (GAP).
``File/ManuContour.dat`` is ``24, 0..23`` (01 §6.2, A7 §4.1: the index list
written by "SaveIndex" at job start = document order at Start) for the 24
segments of ``File/autosave.chf``.  That document order is the row-serpentine
order of the array copy that made the job (``GRP.ArrayRowNum=8``,
``ArrayColNum=3``, ``RowGapVct=1``, ``ColGapVct=3``, ``RowDir=1`` up,
``ColDir=1`` right in ``BkManuPara.xml``; 03 §10 "array copy stored as
independent graphs"), not the result of ``GRP.SortType=4``: every nearest
variant tried here gives a different order (the end of segment 0 is 3.2 mm from
the start of segment 4 but 36.5 mm from segment 1), and the vendor order's
rapid travel from the origin (7 308 mm) is five times that of the nearest order
with reversal (1 396 mm).  So the job was cut unsorted and the sample says
nothing about the vendor's nearest algorithm - see :data:`GAPS`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from itertools import chain

import numpy as np
from scipy.spatial import cKDTree

from nexcut.model.flatten import flatten_glyph
from nexcut.model.glyph import Vec2
from nexcut.model.graph import ChfDocument, Contour, Graph, Text, iter_contours
from nexcut.ops.import_gates import is_closed, reverse_contour

ORIGIN = Vec2(0.0, 0.0)
"""Default tool position the nearest search starts from (UNVERIFIED: machine origin)."""

__all__ = [
    "GAPS",
    "ORIGIN",
    "UNVERIFIED",
    "AutoSortType",
    "SortResult",
    "SortType",
    "containment_depths",
    "sort_document",
    "sort_graphs",
]

UNVERIFIED: tuple[str, ...] = (
    "directional sorts key on the bbox minimum corner of the sort direction "
    "(left edge for L->R, right edge for R->L, ...), ties by the other axis",
    "nearest: greedy nearest neighbour from the machine origin (0,0) to each graph's start "
    "point; open contours may be entered from either end (reversed); a graph that encloses "
    "others becomes eligible only after them (inner first)",
    "inside/outside: nesting depth from closed-contour containment (bbox + point-in-polygon "
    "of the flattened outline), nearest-neighbour order inside one depth",
    "small first: ascending bbox area",
    "GRP.SortIsSmallFirst is not applied (semantics next to SortType 7 unknown)",
)

GAPS: tuple[str, ...] = (
    "no sample of a sorted job exists: ManuContour.dat (0..23) equals the array-copy order of "
    "autosave.chf (row serpentine, GRP.Array* params), which GRP.SortType=4 does not produce; "
    "the nearest/directional/containment algorithms cannot be checked against the original "
    "until a capture of a sorted job (ManuContour.dat + autosave.chf after Sort) is taken",
)


class SortType(IntEnum):
    """``GRP.SortType`` (01 §1.2)."""

    LEFT_TO_RIGHT = 0
    RIGHT_TO_LEFT = 1
    BOTTOM_TO_TOP = 2
    TOP_TO_BOTTOM = 3
    NEAREST = 4
    INSIDE_TO_OUTSIDE = 5
    OUTSIDE_TO_INSIDE = 6
    SMALL_FIRST = 7


class AutoSortType(IntEnum):
    """``IGP.AutoSortType`` (01 §1.2): 0 = none, else ``SortType`` + 1 (no small-first)."""

    NONE = 0
    LEFT_TO_RIGHT = 1
    RIGHT_TO_LEFT = 2
    BOTTOM_TO_TOP = 3
    TOP_TO_BOTTOM = 4
    NEAREST = 5
    INSIDE_TO_OUTSIDE = 6
    OUTSIDE_TO_INSIDE = 7

    def to_sort_type(self) -> SortType:
        """The equivalent ``GRP.SortType``; ``NONE`` has none."""
        if self is AutoSortType.NONE:
            raise ValueError("AutoSortType.NONE has no sort strategy")
        return SortType(int(self) - 1)


@dataclass(slots=True)
class SortResult:
    """Outcome of a sort: new graph list, the source index of each, reversal flags."""

    graphs: list[Graph]
    order: list[int]
    reversed: list[bool]
    notes: list[str] = field(default_factory=list)


def _bbox(g: Graph) -> tuple[Vec2, Vec2]:
    if isinstance(g, Text):
        if g.outline is None:
            return g.position, g.position
        return g.outline.bbox_min, g.outline.bbox_max
    return g.bbox_min, g.bbox_max


def _ends(g: Graph) -> tuple[Vec2, Vec2]:
    if isinstance(g, Text):
        if g.outline is None:
            return g.position, g.position
        return g.outline.start, g.outline.end
    return g.start, g.end


def _is_open_contour(g: Graph) -> bool:
    return isinstance(g, Contour) and bool(g.elements) and not is_closed(g)


def _outline(g: Graph) -> list[tuple[float, float]] | None:
    """Flattened polygon of a single closed contour (None when not a closed contour)."""
    if not isinstance(g, Contour) or not is_closed(g):
        return None
    return _closed_outline(g)


def _closed_outline(g: Contour) -> list[tuple[float, float]] | None:
    pts: list[tuple[float, float]] = []
    for el in g.elements:
        for pl in flatten_glyph(el.glyph, 0.5):
            pts.extend(pl[::-1] if el.direction == -1 else pl)
    return pts if len(pts) >= 3 else None


def _point_in_polygon(p: tuple[float, float], poly: Sequence[tuple[float, float]]) -> bool:
    x, y = p
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-300) + xi:
            inside = not inside
        j = i
    return inside


def _points_in_polygon(probes: np.ndarray, poly: Sequence[tuple[float, float]]) -> np.ndarray:
    """Vectorised :func:`_point_in_polygon` for ``(k, 2)`` probes (same float operations, same result)."""
    pa = np.asarray(poly, dtype=np.float64)
    xi, yi = pa[:, 0], pa[:, 1]
    xj, yj = np.roll(xi, 1), np.roll(yi, 1)
    dy = yj - yi
    den = np.where(dy == 0.0, 1e-300, dy)
    inside = np.zeros(len(probes), dtype=bool)
    step = max(1, 4_000_000 // max(len(pa), 1))
    for k in range(0, len(probes), step):
        x = probes[k : k + step, 0:1]
        y = probes[k : k + step, 1:2]
        cross = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / den + xi)
        inside[k : k + step] = (cross.sum(axis=1) % 2) == 1
    return inside


def _probe_point(g: Graph) -> tuple[float, float]:
    for c, _ in iter_contours(g):
        return (c.start[0], c.start[1])
    s, _ = _ends(g)
    return (s[0], s[1])


def containment_depths(graphs: Sequence[Graph]) -> tuple[list[int], list[set[int]]]:
    """Nesting depth of every graph and the set of graphs directly or indirectly inside it.

    A graph is inside a closed contour when its bbox lies within the contour's bbox
    and its first contour start point is inside the flattened outline (UNVERIFIED).
    Candidates are found with a KD-tree on bbox centres, so a drawing of 10^4-10^5
    graphs is not quadratic (import-ui fidelity review: the pairwise loop took
    minutes at 5 000 graphs).
    """
    return _containment_depths(graphs, None)


def _containment_depths(
    graphs: Sequence[Graph], open_flags: Sequence[bool] | None
) -> tuple[list[int], list[set[int]]]:
    """:func:`containment_depths`, reusing ``_is_open_contour`` flags the caller already has.

    For a contour with elements ``is_closed`` is exactly ``not open``; without
    elements it is ``False``; so the flags decide :func:`_outline` without a second
    ``is_closed`` pass over every graph.
    """
    n = len(graphs)
    depth = [0] * n
    inside: list[set[int]] = [set() for _ in graphs]
    if open_flags is None:
        outlines = {j: poly for j, g in enumerate(graphs) if (poly := _outline(g)) is not None}
    else:
        outlines = {
            j: poly
            for j, g in enumerate(graphs)
            if isinstance(g, Contour)
            and g.elements
            and not open_flags[j]
            and (poly := _closed_outline(g)) is not None
        }
    if not outlines or n == 0:
        return depth, inside
    boxes = [_bbox(g) for g in graphs]
    lo = np.array([b[0] for b in boxes], dtype=np.float64).reshape(n, 2)
    hi = np.array([b[1] for b in boxes], dtype=np.float64).reshape(n, 2)
    probes = np.array([_probe_point(g) for g in graphs], dtype=np.float64).reshape(n, 2)
    tree = cKDTree((lo + hi) / 2.0)
    for j, poly in outlines.items():
        olo, ohi = lo[j], hi[j]
        centre = (olo + ohi) / 2.0
        radius = float(np.max(ohi - olo)) / 2.0
        cand = np.asarray(tree.query_ball_point(centre, radius * (1 + 1e-9) + 1e-9, p=np.inf))
        if len(cand) == 0:
            continue
        cand = cand[cand != j]
        ok = (
            (olo[0] <= lo[cand, 0])
            & (olo[1] <= lo[cand, 1])
            & (hi[cand, 0] <= ohi[0])
            & (hi[cand, 1] <= ohi[1])
        )
        same = np.all(lo[cand] == olo, axis=1) & np.all(hi[cand] == ohi, axis=1)
        cand = cand[ok & ~same]
        if len(cand) == 0:
            continue
        hit = cand[_points_in_polygon(probes[cand], poly)]
        for i in hit.tolist():
            depth[i] += 1
            inside[j].add(i)
    return depth, inside


def _reverse(g: Graph) -> Graph:
    assert isinstance(g, Contour)
    return reverse_contour(g)


_PRE_K = 8
"""Neighbours precomputed per exit point by :func:`_nearest` (the live query's first ``k`` too)."""

_PRE_MIN_ROWS = 64
"""Below this many entry rows the live query alone is cheaper than the batched precompute."""


def _nearest(
    graphs: Sequence[Graph],
    candidates: list[int],
    pos: Vec2,
    *,
    allow_reverse: bool,
    blockers: list[set[int]] | None,
    done: set[int],
    open_flags: Sequence[bool] | None = None,
) -> tuple[list[int], list[bool], Vec2]:
    """Greedy nearest neighbour; picks the minimum of ``(distance, index, reversed)``.

    Equivalent to the pairwise scan (ties by index, a start beats an equally far
    end of the same graph; an all-blocked set takes the first remaining
    candidate) but uses a KD-tree over the entry points, rebuilt as rows are
    consumed, so it is ~O(n log n) instead of O(n^2) (review finding).

    Every step starts where the previous graph was left, and that exit point is known
    in advance (the other end of the chosen entry row), so the ``_PRE_K`` nearest entry
    rows of every possible exit point come from one batched KD-tree query up front
    (STATUS §5 task 7 / X11).  A step first scans that precomputed list with exactly the
    rule of the live query below; only when the list cannot prove its answer (every row
    in it consumed or blocked, or the tie window reaching its end) does the step fall
    back to the live KD-tree query.  The chosen row is the same either way:
    ``tests/test_ops_gates_equivalence.py`` pins it against the per-step code.
    """
    order: list[int] = []
    flags: list[bool] = []
    if not candidates:
        return order, flags, pos
    if open_flags is None:
        open_flags = [_is_open_contour(g) for g in graphs]
    ends = {idx: _ends(graphs[idx]) for idx in candidates}
    # One entry row per graph (its start), plus one at its end when it may be entered
    # reversed; ``exits[r]`` is where the tool is left after cutting through row ``r``.
    # ``candidates`` are distinct graph indices (both callers pass a partition of range(n)).
    owner: list[int] = []
    rev: list[bool] = []
    coords: list[Vec2] = []
    exits: list[Vec2] = []
    rows_of: dict[int, list[int]] = {}
    for idx in candidates:
        s, e = ends[idx]
        r0 = len(owner)
        owner.append(idx)
        rev.append(False)
        coords.append(s)
        exits.append(e)
        if allow_reverse and open_flags[idx]:
            owner.append(idx)
            rev.append(True)
            coords.append(e)
            exits.append(s)
            rows_of[idx] = [r0, r0 + 1]
        else:
            rows_of[idx] = [r0]
    alive = bytearray(b"\x01") * len(owner)
    pending: dict[int, int] = dict.fromkeys(candidates, 0)
    blocked_count = 0
    containers: dict[int, list[int]] = {}
    if blockers is not None:
        for idx in candidates:
            waiting = blockers[idx] - done
            pending[idx] = len(waiting)
            blocked_count += bool(waiting)
            for b in waiting:
                containers.setdefault(b, []).append(idx)
    remaining = list(candidates)
    remaining_set = set(candidates)
    n_rows = len(owner)
    all_xy = np.fromiter(chain.from_iterable(coords), np.float64, 2 * n_rows).reshape(-1, 2)
    tree_rows_list = list(range(n_rows))
    tree = cKDTree(all_xy, balanced_tree=False, compact_nodes=False)
    dead_in_tree = 0

    # Precomputed neighbour lists of every exit point (docstring).  Only worth it, and
    # only used, for finite coordinates; with fewer rows than _PRE_K the list is the
    # whole set and the "list covers every row" branch below applies.
    pre_k = min(_PRE_K, n_rows)
    pre_d: list[list[float]] = []
    pre_i: list[list[int]] = []
    if n_rows >= _PRE_MIN_ROWS and bool(np.isfinite(all_xy).all()):
        exit_xy = np.fromiter(chain.from_iterable(exits), np.float64, 2 * n_rows).reshape(-1, 2)
        if bool(np.isfinite(exit_xy).all()):
            dq_all, lq_all = tree.query(exit_xy, k=pre_k, workers=-1)
            pre_d = dq_all.reshape(n_rows, pre_k).tolist()
            pre_i = lq_all.reshape(n_rows, pre_k).tolist()
    pre_covers_all = pre_k >= n_rows
    use_pre = bool(pre_d)
    last_row = -1

    while remaining_set:
        check_blocked = blocked_count > 0
        best: tuple[float, int, bool] | None = None
        if use_pre and last_row >= 0:
            ds = pre_d[last_row]
            limit = -1.0
            for d_tree, r in zip(ds, pre_i[last_row], strict=True):
                if limit >= 0.0:
                    if d_tree > limit:
                        break
                    if not alive[r] or (check_blocked and pending[owner[r]]):
                        continue
                    key = (math.dist(pos, coords[r]), owner[r], rev[r])
                    if key < best:  # type: ignore[operator]
                        best = key
                elif alive[r] and not (check_blocked and pending[owner[r]]):
                    limit = d_tree * (1.0 + 1e-9) + 1e-12
                    best = (math.dist(pos, coords[r]), owner[r], rev[r])
            if best is not None and not (pre_covers_all or ds[-1] > limit):
                best = None  # the tie window may reach past the list: ask the tree
        if best is None:
            if len(tree_rows_list) > 64 and dead_in_tree * 2 > len(tree_rows_list):
                tree_rows_list = [r for r in tree_rows_list if alive[r]]
                tree = cKDTree(all_xy[tree_rows_list])
                dead_in_tree = 0
            total = len(tree_rows_list)
            k = min(8, total)
            while True:
                dq, lq = tree.query((pos[0], pos[1]), k=k)
                dists = [float(dq)] if k == 1 else dq.tolist()
                locs = [int(lq)] if k == 1 else lq.tolist()
                limit = -1.0
                for d_tree, loc in zip(dists, locs, strict=True):
                    if limit >= 0.0 and d_tree > limit:
                        break
                    r = tree_rows_list[loc]
                    if not alive[r] or (check_blocked and pending[owner[r]]):
                        continue
                    if limit < 0.0:
                        limit = d_tree * (1.0 + 1e-9) + 1e-12
                    key = (math.dist(pos, coords[r]), owner[r], rev[r])
                    if best is None or key < best:
                        best = key
                if best is not None and (k >= total or dists[-1] > limit):
                    break
                if k >= total:
                    break
                best = None
                k = min(total, k * 4)
        if best is None:  # every remaining graph is blocked (containment cycle)
            while remaining[0] not in remaining_set:
                remaining.pop(0)
            best = (0.0, remaining[0], False)
        _, idx, rv = best
        remaining_set.discard(idx)
        done.add(idx)
        rows = rows_of[idx]
        for r in rows:
            alive[r] = 0
        dead_in_tree += len(rows)
        for c in containers.get(idx, ()):
            pending[c] -= 1
            if pending[c] == 0:
                blocked_count -= 1
        order.append(idx)
        flags.append(rv)
        s, e = ends[idx]
        pos = s if rv else e
        last_row = rows[1] if rv else rows[0]
    return order, flags, pos


def sort_graphs(
    graphs: Sequence[Graph],
    sort_type: SortType | int,
    *,
    start: Vec2 = ORIGIN,
    allow_reverse: bool = True,
    inner_first: bool = True,
) -> SortResult:
    """Order ``graphs`` by ``GRP.SortType`` (01 §1.2); returns the new list and its mapping.

    ``start`` is the tool position the nearest search starts from (UNVERIFIED:
    machine origin).  ``allow_reverse`` lets the nearest strategies enter an open
    contour from its end (the contour is then reversed with direction flags,
    geometry unchanged).  ``inner_first`` holds back a closed contour until every
    graph inside it is cut (nearest strategy only).
    """
    st = SortType(int(sort_type))
    n = len(graphs)
    idx = list(range(n))
    flags = [False] * n
    notes: list[str] = []
    if st in (
        SortType.LEFT_TO_RIGHT,
        SortType.RIGHT_TO_LEFT,
        SortType.BOTTOM_TO_TOP,
        SortType.TOP_TO_BOTTOM,
    ):
        boxes = [_bbox(g) for g in graphs]
        if st is SortType.LEFT_TO_RIGHT:
            idx.sort(key=lambda i: (boxes[i][0][0], boxes[i][0][1], i))
        elif st is SortType.RIGHT_TO_LEFT:
            idx.sort(key=lambda i: (-boxes[i][1][0], boxes[i][0][1], i))
        elif st is SortType.BOTTOM_TO_TOP:
            idx.sort(key=lambda i: (boxes[i][0][1], boxes[i][0][0], i))
        else:
            idx.sort(key=lambda i: (-boxes[i][1][1], boxes[i][0][0], i))
    elif st is SortType.SMALL_FIRST:
        boxes = [_bbox(g) for g in graphs]
        idx.sort(
            key=lambda i: ((boxes[i][1][0] - boxes[i][0][0]) * (boxes[i][1][1] - boxes[i][0][1]), i)
        )
    elif st is SortType.NEAREST:
        open_flags = [_is_open_contour(g) for g in graphs]
        blockers = _containment_depths(graphs, open_flags)[1] if inner_first else None
        idx, flags, _ = _nearest(
            graphs,
            list(range(n)),
            start,
            allow_reverse=allow_reverse,
            blockers=blockers,
            done=set(),
            open_flags=open_flags,
        )
    else:
        open_flags = [_is_open_contour(g) for g in graphs]
        depth, _ = _containment_depths(graphs, open_flags)
        levels = sorted(set(depth), reverse=st is SortType.INSIDE_TO_OUTSIDE)
        idx, flags = [], []
        pos = start
        done: set[int] = set()
        for lvl in levels:
            members = [i for i in range(n) if depth[i] == lvl]
            o, f, pos = _nearest(
                graphs,
                members,
                pos,
                allow_reverse=allow_reverse,
                blockers=None,
                done=done,
                open_flags=open_flags,
            )
            idx.extend(o)
            flags.extend(f)
    if len(flags) != len(idx):
        flags = [False] * len(idx)
    out = [_reverse(graphs[i]) if r else graphs[i] for i, r in zip(idx, flags, strict=True)]
    if any(flags):
        notes.append(f"{sum(flags)} open contour(s) reversed by the nearest sort")
    return SortResult(out, idx, flags, notes)


def sort_document(doc: ChfDocument, sort_type: SortType | int, **kwargs: object) -> SortResult:
    """Sort ``doc.graphs`` in place with :func:`sort_graphs`; returns the result."""
    result = sort_graphs(doc.graphs, sort_type, **kwargs)  # type: ignore[arg-type]
    doc.graphs = result.graphs
    return result
