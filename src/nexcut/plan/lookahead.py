"""Node list, slow start/end and whole-contour look-ahead (analysis 05 §7.1, §7.2, §7.4).

``CVelocityPlanning::plan`` (MotionCtrl ``0x10016720`` / CADModule ``0x100fffe0``):

1. node builder A (``0x100169b0``, 05 §7.2) - four passes over the piece list producing per node
   ``len (+0x00)``, ``v_limit (+0x08)``, ``v_feed (+0x10)``, ``A (+0x18)``, ``s (+0x20)``;
   first and last node forced to rest;
2. slow start (``0x10017270``, 05 §7.1) with node insertion at ``s = P6``;
3. the look-ahead core (``0x100114d0``, 05 §7.4): backward then forward pass over the *whole*
   contour with the jerk-limited reachable-speed solver, then one 7-phase profile per segment.

Node indexing (port convention): node 0 is the contour start (``s = 0``) and node ``i >= 1`` is
the end of piece ``i-1``.  UNVERIFIED whether the vendor list carries a leading ``s = 0``
record; the behaviour "starts and ends at rest" (``0x10016f1f-0x10016f4b``) is the same.

Two rule sets are selectable through ``strict``:

* ``strict=False`` reproduces the documented vendor node rules literally (05 §7.2: no ``min``
  with the neighbouring piece, the cruise cap of a segment is ``min(v_feed, Vmax)``, and the
  profile generator raises ``vmax`` to ``max(vs, ve)``);
* ``strict=True`` (default, safety, PORT-PLAN §8) additionally limits a node by the curvature
  limit of the *following* piece, limits the cruise speed inside a curved piece by its own
  curvature limit, and never lets a node exceed the cruise cap of either adjacent segment.
  The look-ahead core itself was not re-traced by the verifier (05 §7.4 "plausible"), so the
  passes below are a classic two-pass planner with the vendor's constraints, not a bit-exact
  port.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from nexcut.plan.contour_fit import PieceList
from nexcut.plan.junction import JunctionModel
from nexcut.plan.params import PlannerParams
from nexcut.plan.scurve import (
    PROFILE_SCURVE,
    Profile,
    _reach_scalar,
    clamp_acc_time,
    jerk_from_acc_time,
    plan_segment,
)

__all__ = [
    "CADMODULE_NODE_FACTOR",
    "FEED_FILL_EPS",
    "SLOW_START_ACTIVE",
    "NodeList",
    "VelocityPlan",
    "apply_slow_end",
    "apply_slow_start",
    "build_nodes",
    "format_veldecc",
    "lookahead",
    "plan_velocity",
]

FloatArray = NDArray[np.float64]

SLOW_START_ACTIVE = 0.1
"""Slow start runs only when ``P6 >= 0.1`` mm; speed is ``max(0.1, P7)`` (05 §7.1)."""
SLOW_START_WALK_EPS = 0.01
"""Nodes with ``s + 0.01 <= P6`` are clamped (05 §7.1, ``0x10017270``)."""
SLOW_START_SNAP = 0.05
"""No node is inserted when an existing node lies within 0.05 mm of ``P6`` (05 §7.1)."""
FEED_FILL_EPS = 0.01
"""Pass 3: nodes whose ``v_feed <= 0.01`` inherit a neighbour's value (05 §7.2)."""
FEED_FILL_ROUNDS = 4
"""Pass 3 is unrolled x4 in the binary (05 §7.2)."""
CADMODULE_NODE_FACTOR = 0.99
"""The ``VelDecc.txt`` logging threshold of CADModule's node builder copy
(``.rdata:0x101116d0``, read at ``0x100ff866``).

**It multiplies nothing** (05 §7.2 correction, A9 §1 re-trace 2026-09-16): the value is compared
against the per-piece speed factor (``piece+0x40``) and only a piece that was actually slowed -
factor below 0.99 - gets a ``NodeID:%d V:%f mm/s`` line in the log.  Kept as a named constant
because :func:`format_veldecc` reproduces that log; applying it to node speeds would be wrong."""


@dataclass(slots=True)
class NodeList:
    """Node records (0x28 bytes, 05 §7.2) as parallel arrays; see module docstring for indexing.

    ``curv_limit`` (port extension) is the curvature/factor/floor part of ``v_limit`` for each
    node's piece (before cap/feed and before forcing the ends to rest); ``strict`` planning uses
    it to protect the next piece's entry.
    """

    length: FloatArray
    v_limit: FloatArray
    v_feed: FloatArray
    acc: FloatArray
    s: FloatArray
    curv_limit: FloatArray

    def __len__(self) -> int:
        return int(self.s.size)

    def copy(self) -> NodeList:
        """Deep copy."""
        return NodeList(
            *(
                np.array(a, copy=True)
                for a in (self.length, self.v_limit, self.v_feed, self.acc, self.s, self.curv_limit)
            )
        )

    def insert(self, index: int, s: float, v_limit: float, v_feed: float, curv: float) -> None:
        """Insert a node at path position ``s`` before ``index`` (helper ``0x10017830``)."""
        prev_s = float(self.s[index - 1])
        next_len = float(self.s[index] - s)
        self.length = np.insert(self.length, index, s - prev_s)
        self.length[index + 1] = next_len
        self.v_limit = np.insert(self.v_limit, index, v_limit)
        self.v_feed = np.insert(self.v_feed, index, v_feed)
        self.acc = np.insert(self.acc, index, self.acc[index])
        self.s = np.insert(self.s, index, s)
        self.curv_limit = np.insert(self.curv_limit, index, curv)


def build_nodes(
    pieces: PieceList, params: PlannerParams, model: JunctionModel | None = None
) -> NodeList:
    """Node builder A (``0x100169b0``, 05 §7.2), passes 1-4, vectorised.

    * pass 1: ``f_i = f(radius_i, 0.5/Ta, P2)`` (05 §7.3; ``Ta`` clamped as in ``plan``),
      ``v_feed = feed_i``, ``A = P0``, ``s = cum_length_i``;
    * pass 2: ``len_i``; ``v_feed_i = min(max(f_{i-1}, f_i), feed_i)`` (raw radius limits);
    * pass 3: ``v_feed <= 0.01`` inherits the previous node (node 1: the next one if larger),
      four rounds;
    * pass 4: ``v = min(f_i, Vmax); v *= factor_i; v = max(v, P4); v = min(v, cap_i);
      v = min(v, feed_i)``; first and last node = 0.

    Port extension: the radius fed to ``f`` is ``min(radius, corner_radius)`` so an unblended
    corner at the piece end limits its node (``corner_radius`` is ``inf`` for vendor-like input).
    """
    model = model or JunctionModel()
    n = len(pieces)
    ta = clamp_acc_time(params.acc_time)
    radius = np.minimum(pieces.radius, pieces.corner_radius)
    f = model.limit(radius, ta, params.precision, params.acc) if n else np.empty(0)
    feed = pieces.feed
    # pass 2
    f_prev = np.concatenate((f[:1], f[:-1])) if n else f
    v_feed = np.minimum(np.maximum(f_prev, f), feed)
    # pass 3 (zero-feed fill-in)
    for _ in range(FEED_FILL_ROUNDS):
        if n == 0:
            break
        low = v_feed <= FEED_FILL_EPS
        if not low.any():
            break
        if low[0] and n > 1 and v_feed[1] > v_feed[0]:
            v_feed[0] = v_feed[1]
        for i in np.flatnonzero(low[1:]) + 1:
            v_feed[i] = v_feed[i - 1]
    # pass 4
    curv = np.maximum(np.minimum(f, params.vmax) * pieces.factor, params.min_speed)
    v = np.minimum(np.minimum(curv, pieces.cap), feed)
    nodes = NodeList(
        length=np.concatenate(([0.0], pieces.lengths)),
        v_limit=np.concatenate(([0.0], v)),
        v_feed=np.concatenate(([0.0], v_feed)),
        acc=np.full(n + 1, params.acc),
        s=np.concatenate(([0.0], pieces.cum_length)),
        curv_limit=np.concatenate(([0.0], curv)),
    )
    if n + 1 >= 1:
        nodes.v_limit[0] = 0.0
        nodes.v_limit[-1] = 0.0
    return nodes


def apply_slow_start(nodes: NodeList, length: float, speed: float, floor: float = 0.0) -> NodeList:
    """Slow-start clamp (``0x10017270``, 05 §7.1) - returns a new node list.

    Active only when ``length >= 0.1``; ``v = max(0.1, max(speed, floor))`` (``P7 = max(P7, P4)``
    then ``max(0.1, P7)``).  Walking nodes ``i >= 1``: while ``s_i + 0.01 <= length`` both
    ``v_limit`` and ``v_feed`` are clamped to ``<= v``; at the first node beyond, if
    ``|s_i - length| <= 0.05`` it is clamped too, otherwise a node is inserted at ``s = length``
    (limits ``min(v, feed of the split piece)``).
    """
    out = nodes.copy()
    if length < SLOW_START_ACTIVE:
        return out
    v = max(SLOW_START_ACTIVE, max(speed, floor))
    for i in range(1, len(out)):
        si = float(out.s[i])
        if si + SLOW_START_WALK_EPS <= length:
            out.v_limit[i] = min(out.v_limit[i], v)
            out.v_feed[i] = min(out.v_feed[i], v)
            continue
        if abs(si - length) <= SLOW_START_SNAP:
            out.v_limit[i] = min(out.v_limit[i], v)
            out.v_feed[i] = min(out.v_feed[i], v)
        else:
            lim = min(v, float(out.v_feed[i]))
            out.insert(i, length, lim, lim, float(out.curv_limit[i]))
        break
    return out


def apply_slow_end(nodes: NodeList, length: float, speed: float) -> NodeList:
    """Mirror of the slow start over the last ``length`` mm (``P8``/``P9``).

    UNVERIFIED: A6 §1.5 names ``P8``/``P9`` the 收刀 "end work segment" length/speed, but
    ``plan`` reads only ``P0..P7`` (05 §7.1) and the consumer was not traced.  Implemented as the
    exact mirror of :func:`apply_slow_start` (a slower end is never less safe).
    """
    out = nodes.copy()
    if length < SLOW_START_ACTIVE or len(out) < 2:
        return out
    v = max(SLOW_START_ACTIVE, speed)
    boundary = float(out.s[-1]) - length
    for i in range(len(out) - 1, 0, -1):
        s_prev = float(out.s[i - 1])
        if s_prev - SLOW_START_WALK_EPS >= boundary or abs(s_prev - boundary) <= SLOW_START_SNAP:
            # segment (i-1 -> i) lies in the end zone: clamp its cruise and its start node
            out.v_feed[i] = min(out.v_feed[i], v)
            out.v_limit[i - 1] = min(out.v_limit[i - 1], v)
            if s_prev - SLOW_START_WALK_EPS >= boundary:
                continue
            break
        feed = float(out.v_feed[i])
        out.insert(i, boundary, min(v, feed), feed, float(out.curv_limit[i]))
        out.v_feed[i + 1] = min(feed, v)
        break
    return out


@dataclass(slots=True)
class VelocityPlan:
    """Look-ahead result: node speeds, one :class:`Profile` per segment and timing.

    ``position``/``velocity`` evaluate the whole contour through one global phase table
    (7 rows per segment, numpy ``searchsorted``), so sampling at the 250 µs cycle is vectorised.
    """

    nodes: NodeList
    speed: FloatArray
    """Planned speed at every node [mm/s] (0 at both ends)."""
    profiles: list[Profile]
    jerk: float
    start_times: FloatArray = field(default_factory=lambda: np.empty(0))
    _phases: FloatArray | None = field(default=None, repr=False, compare=False)

    @property
    def total_time(self) -> float:
        """Sum of the segment durations [s]."""
        return float(sum(p.total_time for p in self.profiles))

    def _phase_table(self) -> FloatArray:
        """Rows ``(t_start, jerk, acc0, v0, s0)`` over all segments, ``s0`` in contour coordinates."""
        if self._phases is None:
            rows: list[tuple[float, float, float, float, float]] = []
            t0 = 0.0
            for seg, prof in enumerate(self.profiles):
                base = float(self.nodes.s[seg])
                for dur, jk, a0, v0, s0 in prof.phase_rows():
                    rows.append((t0, jk, a0, v0, base + s0))
                    t0 += dur
            self._phases = np.asarray(rows, dtype=np.float64).reshape(-1, 5)
        return self._phases

    def state(self, time: FloatArray | float) -> tuple[FloatArray, FloatArray, FloatArray]:
        """``(s, v, a)`` over the whole contour; times clamped to ``[0, total_time]``."""
        t = np.atleast_1d(np.asarray(time, dtype=np.float64))
        if not self.profiles:
            z = np.zeros_like(t)
            return z, z.copy(), z.copy()
        tab = self._phase_table()
        tt = np.clip(t, 0.0, self.total_time)
        k = np.clip(np.searchsorted(tab[:, 0], tt, side="right") - 1, 0, len(tab) - 1)
        tau = tt - tab[k, 0]
        j, a0, v0, s0 = tab[k, 1], tab[k, 2], tab[k, 3], tab[k, 4]
        s = s0 + v0 * tau + a0 * tau * tau / 2.0 + j * tau**3 / 6.0
        v = v0 + a0 * tau + j * tau * tau / 2.0
        return s, v, a0 + j * tau

    def position(self, time: FloatArray | float) -> FloatArray:
        """Path position ``s(t)`` [mm] over the whole contour (vectorised over ``time``)."""
        return self.state(time)[0]

    def velocity(self, time: FloatArray | float) -> FloatArray:
        """Path speed ``v(t)`` [mm/s] (vectorised)."""
        return self.state(time)[1]


def lookahead(
    nodes: NodeList,
    vmax: float,
    acc: float,
    jerk: float,
    *,
    strict: bool = True,
    profile_type: int = PROFILE_SCURVE,
) -> VelocityPlan:
    """Whole-contour backward/forward pass + per-segment profile (05 §7.4, §7.5).

    Junction limit ``j_i = v_limit_i`` (``strict``: also ``<= curv_limit`` of the next piece and
    ``<=`` both adjacent cruise caps).  Backward pass ``v_i = min(j_i, reach(v_{i+1}, len_{i+1}))``
    from ``v_last = 0``; forward pass ``v_i = min(v_i, reach(v_{i-1}, len_i))`` from ``v_0 = 0``.
    ``reach`` is the closed-form jerk-limited solver (Cardano, 05 §7.4).  Each segment then gets
    ``plan_segment(len_i, v_{i-1}, cap_i, v_i)``; because every speed change fits its segment by
    construction no profile is ``adjusted`` (tested).
    """
    n = len(nodes)
    v = np.array(nodes.v_limit, dtype=np.float64)
    if n == 0:
        return VelocityPlan(nodes, v, [], jerk)
    cap = np.minimum(nodes.v_feed, vmax)  # cruise cap of the segment ending at node i
    if strict:
        cap = np.minimum(cap, nodes.curv_limit)
        nxt_curv = np.concatenate((nodes.curv_limit[1:], [0.0]))
        v = np.minimum(v, nxt_curv)
        cap_next = np.concatenate((cap[1:], [0.0]))
        v[1:] = np.minimum(v[1:], cap[1:])
        v = np.minimum(v, np.where(np.arange(n) < n - 1, cap_next, 0.0))
    v[0] = 0.0
    v[-1] = 0.0
    v = np.maximum(v, 0.0)
    length = nodes.length
    for i in range(n - 2, -1, -1):  # backward
        r = _reach_scalar(float(v[i + 1]), float(length[i + 1]), acc, jerk)
        if r < v[i]:
            v[i] = r
    for i in range(1, n):  # forward
        r = _reach_scalar(float(v[i - 1]), float(length[i]), acc, jerk)
        if r < v[i]:
            v[i] = r
    profiles = [
        plan_segment(
            float(length[i]), float(v[i - 1]), float(cap[i]), float(v[i]), acc, jerk, profile_type
        )
        for i in range(1, n)
    ]
    durations = np.array([p.total_time for p in profiles], dtype=np.float64)
    starts = np.concatenate(([0.0], np.cumsum(durations)[:-1])) if profiles else np.empty(0)
    return VelocityPlan(nodes, v, profiles, jerk, starts)


def plan_velocity(
    pieces: PieceList,
    params: PlannerParams,
    *,
    model: JunctionModel | None = None,
    strict: bool = True,
    profile_type: int = PROFILE_SCURVE,
    apply_end_segment: bool = True,
) -> VelocityPlan:
    """``CVelocityPlanning::plan(contour, block, mode=1)`` end to end (05 §7.1-7.5).

    ``Ta`` is clamped to ``[0.06, 0.25]`` s and ``J`` derived from ``(A, Ta, Vmax)`` (05 §7.1);
    ``P7 = max(P7, P4)``; slow start from ``P6/P7``; the end segment from ``P8/P9`` when
    ``apply_end_segment`` (UNVERIFIED, see :func:`apply_slow_end`).  Only node builder A exists
    here: CADModule never selects builder B (A6 §1.9).
    """
    ta = clamp_acc_time(params.acc_time)
    jerk = jerk_from_acc_time(params.acc, ta, params.vmax)
    nodes = build_nodes(pieces, params, model)
    nodes = apply_slow_start(
        nodes, params.slow_start_len, params.slow_start_speed, params.min_speed
    )
    if apply_end_segment:
        nodes = apply_slow_end(nodes, params.slow_end_len, params.slow_end_speed)
    return lookahead(nodes, params.vmax, params.acc, jerk, strict=strict, profile_type=profile_type)


def format_veldecc(speeds: FloatArray | list[float]) -> bytes:
    """``Log\\VelDecc.txt`` body: ``"NodeID:%d V:%f mm/s\\n"`` per node (CADModule string at
    file offset ``0x11393c``, opened ``"at+"`` = append, text mode -> CRLF; 05 §3, §7.2).

    The package file is 0 bytes (the log flag was off, 05 §7.2), which an empty list reproduces.
    UNVERIFIED which speed the vendor logs (node limit vs planned speed).

    **Known fidelity gap** (A9 §1, 05 §7.2 correction): the vendor writes a line only for a node
    whose piece speed factor is below :data:`CADMODULE_NODE_FACTOR`, i.e. only for a piece that
    the look-ahead actually slowed down; this helper writes every node it is given. Filtering
    needs the per-piece factor, which the caller has and this signature does not.
    """
    return "".join(f"NodeID:{i} V:{float(v):f} mm/s\r\n" for i, v in enumerate(speeds)).encode(
        "ascii"
    )
