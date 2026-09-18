"""Frozen copy of the per-item IGP gates and nearest sort, as they were before STATUS §5 task 7.

``tests/test_ops_gates_equivalence.py`` runs this module and the vectorised
``nexcut.ops.import_gates`` / ``nexcut.ops.sort`` side by side and requires identical output
(graphs, order, reversal flags, counts, notes).  The functions below are the pre-task-7 source,
verbatim except for the ``_ref`` renames that keep them wired to each other rather than to the
new implementations.  Helpers that task 7 did not touch (``_glyph_info``, ``_duplicate``,
``_split_runs``, ``refresh_contour``, ``element_tangents``, ...) are imported from the live
module.  Do not "fix" or speed up anything here: this file is the definition the fast path is
checked against.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace

import numpy as np
from scipy.spatial import cKDTree

from nexcut.model.flatten import flatten_glyph
from nexcut.model.glyph import PointGlyph, Vec2
from nexcut.model.graph import (
    ChfDocument,
    Contour,
    ContourElement,
    Graph,
    Group,
    Scan,
)
from nexcut.ops.import_gates import (
    GateParams,
    GateReport,
    MergeType,
    _duplicate,
    _glyph_info,
    _GlyphInfo,
    _is_point_contour,
    _split_runs,
    contour_endpoints,
    element_tangents,
    filter_micro_graphs,
    refresh_contour,
    refresh_group,
)
from nexcut.ops.sort import (
    ORIGIN,
    AutoSortType,
    SortResult,
    SortType,
    _bbox,
    _ends,
    _points_in_polygon,
    _probe_point,
)


def is_closed_ref(contour: Contour, tol: float | None = None) -> bool:
    """True when the contour's start and end coincide within ``tol`` (default: its ``precision``).

    ``precision`` is "used e.g. to decide closure (start==end within this)" (03 §6.1 line 1).
    """
    if not contour.elements:
        return False
    if all(isinstance(e.glyph, PointGlyph) for e in contour.elements):
        return False
    s, e = contour_endpoints(contour)
    return math.dist(s, e) <= (contour.precision if tol is None else tol)


def reverse_contour_ref(contour: Contour) -> Contour:
    """Return a copy traversed backwards: element order reversed, direction flags negated.

    Geometry is untouched; only the ``{glyph, dir}`` list of 03 §6.1 line 10 changes.
    """
    els = [
        ContourElement(e.glyph, -1 if e.direction != -1 else 1) for e in reversed(contour.elements)
    ]
    return replace(contour, elements=els, start=contour.end, end=contour.start)


def remove_overlaps_ref(graphs: Sequence[Graph], gate: float) -> tuple[list[Graph], int]:
    """Remove glyphs that duplicate an earlier glyph within ``gate`` mm (``IGP.OverlapGate``).

    Scope: top-level contours and group children; a contour losing a middle
    glyph is split into runs.  Direction is irrelevant (a reversed copy is a
    duplicate).  Returns ``(graphs, removed_glyph_count)``.  UNVERIFIED algorithm.
    """
    step = max(gate, 0.005)
    cell = max(gate * 4.0, 1e-6)
    grid: dict[tuple[int, int], list[_GlyphInfo]] = {}
    removed = 0

    def seen_before(info: _GlyphInfo) -> bool:
        kx, ky = int(math.floor(info.lo[0] / cell)), int(math.floor(info.lo[1] / cell))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in grid.get((kx + dx, ky + dy), ()):
                    if _duplicate(info, other, gate):
                        return True
        grid.setdefault((kx, ky), []).append(info)
        return False

    def filter_contour(c: Contour) -> list[Contour]:
        nonlocal removed
        keep = []
        for el in c.elements:
            dup = seen_before(_glyph_info(el.glyph, step))
            keep.append(not dup)
            removed += dup
        if all(keep):
            return [c]
        return _split_runs(c, keep)

    out: list[Graph] = []
    for g in graphs:
        if isinstance(g, Contour):
            out.extend(filter_contour(g))
        elif isinstance(g, Group) and not isinstance(g, Scan):
            kids = [p for k in g.children for p in filter_contour(k)]
            if not kids:
                continue
            if kids != g.children:
                g = replace(g, children=kids)
                refresh_group(g)
            out.append(g)
        else:
            out.append(g)
    return out, removed


def _turn(t_from: Vec2, t_to: Vec2) -> float:
    dot = max(-1.0, min(1.0, t_from[0] * t_to[0] + t_from[1] * t_to[1]))
    return math.acos(dot)


def merge_connected_ref(
    graphs: Sequence[Graph], gate: float, merge_type: int = MergeType.DIRECTION_FIRST
) -> tuple[list[Graph], int]:
    """Chain open contours whose endpoints lie within ``gate`` mm (``IGP.ConnectGate``, 01 §1.2).

    Every open top-level contour seeds a chain in document order; the chain is
    extended at its end, then at its start, choosing among touching candidates by
    ``merge_type`` (1 smallest tangent turn, 2 longest, 3 smallest gap; ties by
    document order).  Candidates are reversed with :func:`reverse_contour` when
    needed.  A chain stops growing once its ends meet.  The merged contour keeps
    the seed's crafts and takes the seed's place.  Returns ``(graphs, merges)``.
    UNVERIFIED algorithm (module docstring).
    """
    if merge_type == MergeType.NONE:
        return list(graphs), 0
    cell = max(gate, 1e-6)
    open_ids = [
        i
        for i, g in enumerate(graphs)
        if isinstance(g, Contour)
        and g.elements
        and not _is_point_contour(g)
        and not is_closed_ref(g, gate)
    ]
    ends: dict[int, tuple[Vec2, Vec2]] = {}
    grid: dict[tuple[int, int], set[int]] = {}

    def key(p: Vec2) -> tuple[int, int]:
        return int(math.floor(p[0] / cell)), int(math.floor(p[1] / cell))

    for i in open_ids:
        c = graphs[i]
        assert isinstance(c, Contour)
        ends[i] = contour_endpoints(c)
        for p in ends[i]:
            grid.setdefault(key(p), set()).add(i)

    used: set[int] = set()

    def candidates(p: Vec2, exclude: set[int], layer: int) -> list[tuple[int, bool, float]]:
        """(index, attach_at_its_start, gap) of unused open contours of ``layer`` touching ``p``."""
        kx, ky = key(p)
        found: dict[int, tuple[int, bool, float]] = {}
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((kx + dx, ky + dy), ()):
                    if j in used or j in exclude or graphs[j].layer != layer:
                        continue
                    s, e = ends[j]
                    for at_start, q in ((True, s), (False, e)):
                        d = math.dist(p, q)
                        if d <= gate and (j not in found or d < found[j][2]):
                            found[j] = (j, at_start, d)
        return sorted(found.values())

    def pick(
        opts: list[tuple[int, bool, float]], tangent: Vec2, forward: bool
    ) -> tuple[int, bool, float]:
        if merge_type == MergeType.LENGTH_FIRST:
            return max(opts, key=lambda o: (graphs[o[0]].length, -o[0]))  # type: ignore[union-attr]
        if merge_type == MergeType.DISTANCE_FIRST:
            return min(opts, key=lambda o: (o[2], o[0]))

        def turn(o: tuple[int, bool, float]) -> float:
            c = graphs[o[0]]
            assert isinstance(c, Contour)
            if forward:
                el = c.elements[0] if o[1] else reverse_contour_ref(c).elements[0]
                t = element_tangents(el)[0]
                return _turn(tangent, t)
            el = c.elements[-1] if not o[1] else reverse_contour_ref(c).elements[-1]
            t = element_tangents(el)[1]
            return _turn(t, tangent)

        return min(opts, key=lambda o: (turn(o), o[2], o[0]))

    merged: dict[int, Contour] = {}
    absorbed: set[int] = set()
    merges = 0
    for seed in open_ids:
        if seed in used:
            continue
        used.add(seed)
        base = graphs[seed]
        assert isinstance(base, Contour)
        elements = list(base.elements)
        start, end = ends[seed]
        members = {seed}
        for forward in (True, False):
            while math.dist(start, end) > gate:
                p = end if forward else start
                opts = candidates(p, members, base.layer)
                if not opts:
                    break
                tan = (
                    element_tangents(elements[-1])[1]
                    if forward
                    else element_tangents(elements[0])[0]
                )
                j, at_start, _ = pick(opts, tan, forward)
                cj = graphs[j]
                assert isinstance(cj, Contour)
                used.add(j)
                members.add(j)
                merges += 1
                if forward:
                    piece = cj if at_start else reverse_contour_ref(cj)
                    elements.extend(piece.elements)
                    end = contour_endpoints(piece)[1]
                else:
                    piece = cj if not at_start else reverse_contour_ref(cj)
                    elements[:0] = piece.elements
                    start = contour_endpoints(piece)[0]
        if len(members) > 1:
            merged[seed] = refresh_contour(replace(base, elements=elements))
            absorbed.update(members - {seed})

    out: list[Graph] = []
    for i, g in enumerate(graphs):
        if i not in absorbed:
            out.append(merged.get(i, g))
    return out, merges


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def apply_import_gates_ref(doc: ChfDocument, params: GateParams | None = None) -> GateReport:
    """Run the ``IGP`` gates on an imported document in place (05 §4, 01 §1.2).

    Order (UNVERIFIED): duplicate lines, combine near lines, minimal graphics,
    then ``IGP.AutoSortType`` through :func:`nexcut.ops.sort.sort_document`.
    """
    p = params or GateParams()
    report = GateReport()
    graphs: list[Graph] = list(doc.graphs)
    if p.filter_overlap:
        graphs, report.overlaps_removed = remove_overlaps_ref(graphs, p.overlap_gate)
    if p.merge_type != MergeType.NONE:
        graphs, report.contours_merged = merge_connected_ref(graphs, p.connect_gate, p.merge_type)
    if p.filter_micro:
        graphs, report.micro_removed = filter_micro_graphs(graphs, p.micro_gate)
    doc.graphs = graphs
    if p.auto_sort_type != AutoSortType.NONE:
        result = sort_graphs_ref(doc.graphs, AutoSortType(p.auto_sort_type).to_sort_type())
        doc.graphs = result.graphs
        report.sorted_by = p.auto_sort_type
        report.notes.extend(result.notes)
    return report


def _is_open_contour_ref(g: Graph) -> bool:
    return isinstance(g, Contour) and bool(g.elements) and not is_closed_ref(g)


def _outline_ref(g: Graph) -> list[tuple[float, float]] | None:
    """Flattened polygon of a single closed contour (None when not a closed contour)."""
    if not isinstance(g, Contour) or not is_closed_ref(g):
        return None
    pts: list[tuple[float, float]] = []
    for el in g.elements:
        for pl in flatten_glyph(el.glyph, 0.5):
            pts.extend(pl[::-1] if el.direction == -1 else pl)
    return pts if len(pts) >= 3 else None


def containment_depths_ref(graphs: Sequence[Graph]) -> tuple[list[int], list[set[int]]]:
    """Nesting depth of every graph and the set of graphs directly or indirectly inside it.

    A graph is inside a closed contour when its bbox lies within the contour's bbox
    and its first contour start point is inside the flattened outline (UNVERIFIED).
    Candidates are found with a KD-tree on bbox centres, so a drawing of 10^4-10^5
    graphs is not quadratic (import-ui fidelity review: the pairwise loop took
    minutes at 5 000 graphs).
    """
    n = len(graphs)
    depth = [0] * n
    inside: list[set[int]] = [set() for _ in graphs]
    outlines = {j: poly for j, g in enumerate(graphs) if (poly := _outline_ref(g)) is not None}
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


def _reverse_ref(g: Graph) -> Graph:
    assert isinstance(g, Contour)
    return reverse_contour_ref(g)


def _nearest_ref(
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
    """
    order: list[int] = []
    flags: list[bool] = []
    if not candidates:
        return order, flags, pos
    if open_flags is None:
        open_flags = [_is_open_contour_ref(g) for g in graphs]
    ends = {idx: _ends(graphs[idx]) for idx in candidates}
    owner: list[int] = []
    rev: list[bool] = []
    coords: list[tuple[float, float]] = []
    for idx in candidates:
        s, e = ends[idx]
        owner.append(idx)
        rev.append(False)
        coords.append((s[0], s[1]))
        if allow_reverse and open_flags[idx]:
            owner.append(idx)
            rev.append(True)
            coords.append((e[0], e[1]))
    rows_of: dict[int, list[int]] = {}
    for r, idx in enumerate(owner):
        rows_of.setdefault(idx, []).append(r)
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
    all_xy = np.asarray(coords, dtype=np.float64).reshape(-1, 2)
    tree_rows_list = list(range(len(owner)))
    tree = cKDTree(all_xy)
    dead_in_tree = 0

    while remaining_set:
        if len(tree_rows_list) > 64 and dead_in_tree * 2 > len(tree_rows_list):
            tree_rows_list = [r for r in tree_rows_list if alive[r]]
            tree = cKDTree(all_xy[tree_rows_list])
            dead_in_tree = 0
        total = len(tree_rows_list)
        k = min(8, total)
        best: tuple[float, int, bool] | None = None
        check_blocked = blocked_count > 0
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
        for r in rows_of[idx]:
            alive[r] = 0
            dead_in_tree += 1
        for c in containers.get(idx, ()):
            pending[c] -= 1
            if pending[c] == 0:
                blocked_count -= 1
        order.append(idx)
        flags.append(rv)
        s, e = ends[idx]
        pos = s if rv else e
    return order, flags, pos


def sort_graphs_ref(
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
        open_flags = [_is_open_contour_ref(g) for g in graphs]
        blockers = containment_depths_ref(graphs)[1] if inner_first else None
        idx, flags, _ = _nearest_ref(
            graphs,
            list(range(n)),
            start,
            allow_reverse=allow_reverse,
            blockers=blockers,
            done=set(),
            open_flags=open_flags,
        )
    else:
        open_flags = [_is_open_contour_ref(g) for g in graphs]
        depth, _ = containment_depths_ref(graphs)
        levels = sorted(set(depth), reverse=st is SortType.INSIDE_TO_OUTSIDE)
        idx, flags = [], []
        pos = start
        done: set[int] = set()
        for lvl in levels:
            members = [i for i in range(n) if depth[i] == lvl]
            o, f, pos = _nearest_ref(
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
    out = [_reverse_ref(graphs[i]) if r else graphs[i] for i, r in zip(idx, flags, strict=True)]
    if any(flags):
        notes.append(f"{sum(flags)} open contour(s) reversed by the nearest sort")
    return SortResult(out, idx, flags, notes)
