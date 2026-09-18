"""Interpolation-cycle sampling of planned contours: ``s(t)`` per tick -> XY (05 §7.6, §8).

The vendor sampler (``0x1000f730``) evaluates each segment profile analytically once per
interpolation cycle and pushes ``s(t_k)/L``; ``segInterp`` drops a segment whose length is below
one cycle at its entry/exit speed (``L < 0.00025·min(vs, ve)``, validator ``0x10010090``);
``calcGraphCtInterpPt`` (``0x100f6f10``) maps the normalised abscissa onto the curve.  The PC-side
cycle is ``AX.InterpolationCycle`` = 250 µs as refreshed from card reg 50005 (A3 V3); the period
the **card** executes one item in is UNVERIFIED (11 O1, §7 step 8).

Two sampling modes:

* ``per_segment=True`` (default, vendor structure): each look-ahead segment is sampled on its
  own clock ``t_k = k·dt, k = 0..N, N = int(T/dt + 0.5)``; skipped segments contribute no ticks
  (their displacement lands in the next tick); ``k = 0`` of every segment after the first is not
  repeated (UNVERIFIED: whether the vendor emits the duplicated boundary sample is not traced).
* ``per_segment=False``: one global clock over the whole plan (time-exact alternative).

In both modes the last sample is forced onto the path end so no displacement is lost (port rule).

Port deviations (safety, PORT-PLAN §8; planner-fidelity review):

* A *run* of skipped segments (dropped by the ``segInterp`` rule, or too short to own a sample on
  their own clock) is not collapsed into the next tick: once the run lasts at least one cycle it
  is filled with samples of the global clock.  Without this, ``m`` consecutive dropped pieces at
  speed ``v`` land in a single tick of ``m·v·dt`` (1000 pieces of 0.01 mm at 100 mm/s -> one
  10 mm / 2587-pulse tick).  Plans without such runs sample exactly as before.  How the vendor
  stitches segment vectors is UNVERIFIED (05 §7.6 documents the per-segment sampler only).
* :meth:`PathGeometry.from_glyphs` refuses glyphs that do not join (gap > :data:`MAX_JOINT_GAP`):
  the planner's abscissa skips the gap, so the sampler would put the whole gap into one tick
  (a 50 mm gap = one 12 909-pulse tick, inside int16 and therefore not caught by the packer).
The geometry is taken from the smoothed glyph list of :mod:`nexcut.plan.contour_fit`; curves are
dense polylines there (sagitta 1 µm), which bounds the XY error of this mapping (UNVERIFIED vs.
the vendor's NURBS evaluation).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from nexcut.plan.contour_fit import FitGlyph, PieceList
from nexcut.plan.lookahead import VelocityPlan, plan_velocity
from nexcut.plan.params import PlannerParams, RapidParams
from nexcut.plan.scurve import CYCLE_S, MIN_LENGTH

__all__ = [
    "CYCLE_US_DEFAULT",
    "MAX_JOINT_GAP",
    "PathGeometry",
    "SampledMotion",
    "cycle_seconds",
    "plan_line",
    "sample_plan",
    "segment_dropped",
]

FloatArray = NDArray[np.float64]

CYCLE_US_DEFAULT = 250
"""``AX.InterpolationCycle`` [µs] (BkHardPara.xml, 05 §10). UNVERIFIED card-side period."""
_EPS = 1e-9
MAX_JOINT_GAP = 0.01
"""Largest distance [mm] between the end of one glyph and the start of the next that
:meth:`PathGeometry.from_glyphs` accepts.  Value = ``IGP.ConnectGate`` (0.01 mm, 05 §10), the
import gate below which the vendor joins glyphs.  UNVERIFIED as a planner limit (port safety
rule): at 258 p/mm a 0.01 mm joint error is at most ~3 pulses in one tick."""


def cycle_seconds(cycle_us: float = CYCLE_US_DEFAULT) -> float:
    """Cycle in seconds (P5 = cycle·0.001 ms, A6 §1.4)."""
    if cycle_us <= 0:
        raise ValueError("interpolation cycle must be > 0")
    return float(cycle_us) * 1e-6


def segment_dropped(length: float, vs: float, ve: float, cycle_s: float = CYCLE_S) -> bool:
    """``L < 1e-4`` or ``L < cycle·min(vs, ve)`` -> ``segInterp`` returns an empty vector (05 §7.6).

    The vendor constant is 0.00025 s regardless of the configured cycle (``.rdata`` literal).
    """
    return length < MIN_LENGTH or length < cycle_s * min(vs, ve)


@dataclass(slots=True)
class PathGeometry:
    """Arc-length parameterised polyline of a contour (glyph order = cut order).

    ``s`` holds the nominal abscissa of every vertex: inside a glyph the polyline length is
    scaled to the glyph's ``length`` (which may be an exact arc/spline length), so ``s`` agrees
    with the piece list the planner used.
    """

    s: FloatArray
    xy: FloatArray
    laser_on: NDArray[np.bool_]
    """Laser state of the glyph each vertex interval belongs to (``len = len(s) - 1``)."""

    @property
    def length(self) -> float:
        """Total nominal length [mm]."""
        return float(self.s[-1]) if self.s.size else 0.0

    @classmethod
    def from_glyphs(
        cls, glyphs: Sequence[FitGlyph], max_gap: float = MAX_JOINT_GAP
    ) -> PathGeometry:
        """Concatenate glyph polylines (duplicated joint vertices are kept; ``np.interp`` copes).

        Raises ``ValueError`` when consecutive glyphs are more than ``max_gap`` mm apart (module
        docstring: the gap would become one unplanned tick).
        """
        s_parts: list[FloatArray] = []
        xy_parts: list[FloatArray] = []
        on_parts: list[NDArray[np.bool_]] = []
        base = 0.0
        for g in glyphs:
            pts = np.asarray(g.points, dtype=np.float64)
            if len(pts) < 2:
                continue
            if xy_parts:
                gap = float(np.hypot(*(pts[0] - xy_parts[-1][-1])))
                if gap > max_gap:
                    raise ValueError(
                        f"contour glyphs do not join: {gap:.6g} mm gap at s = {base:.6g} mm "
                        f"(> {max_gap:g} mm); the sampler would jump it in one tick"
                    )
            d = np.hypot(*np.diff(pts, axis=0).T)
            poly = float(d.sum())
            local = np.concatenate(([0.0], np.cumsum(d)))
            scale = g.length / poly if poly > 0 else 0.0
            s_parts.append(base + local * scale)
            xy_parts.append(pts)
            on_parts.append(np.full(len(pts) - 1, bool(g.laser_on)))
            base += g.length
        if not s_parts:
            return cls(np.zeros(1), np.zeros((1, 2)), np.zeros(0, dtype=bool))
        on = [on_parts[0]]
        for part in on_parts[1:]:
            on.append(np.concatenate(([part[0]], part)))  # zero-length interval at the joint
        return cls(np.concatenate(s_parts), np.vstack(xy_parts), np.concatenate(on))

    def point_at(self, s: FloatArray | float) -> FloatArray:
        """XY at abscissa ``s`` (clamped to the path), ``n x 2``."""
        ss = np.clip(np.atleast_1d(np.asarray(s, dtype=np.float64)), 0.0, self.length)
        # Written into one output array rather than column_stack'ed: this runs once per contour
        # and once per rapid of a job, and PORT-PLAN §8.3 counts every array call (STATUS §5.6).
        out = np.empty((ss.size, 2), dtype=np.float64)
        out[:, 0] = np.interp(ss, self.s, self.xy[:, 0])
        out[:, 1] = np.interp(ss, self.s, self.xy[:, 1])
        return out


@dataclass(slots=True)
class SampledMotion:
    """One point per interpolation instant: time, abscissa, speed and XY.

    ``xy[0]`` is the start point; ``len(xy) - 1`` ticks follow (one per interval).
    """

    t: FloatArray
    s: FloatArray
    v: FloatArray
    xy: FloatArray
    dropped_segments: int = 0

    @property
    def ticks(self) -> int:
        """Number of intervals = FIFO ticks."""
        return max(len(self.s) - 1, 0)

    def interval_speed(self) -> FloatArray:
        """Speed per interval (mean of both ends) - input of the power/frequency curves."""
        if len(self.v) < 2:
            return np.zeros(0)
        return 0.5 * (self.v[1:] + self.v[:-1])


def sample_plan(
    plan: VelocityPlan,
    geometry: PathGeometry | None = None,
    cycle_us: float = CYCLE_US_DEFAULT,
    *,
    per_segment: bool = True,
) -> SampledMotion:
    """Sample a :class:`VelocityPlan` every cycle and map the abscissa to XY (05 §7.6, §8).

    ``geometry=None`` returns ``xy`` of zeros (abscissa only).  The plan's total length is
    rescaled to the geometry length when they differ by rounding.
    """
    dt = cycle_seconds(cycle_us)
    nodes_s = plan.nodes.s
    total = float(nodes_s[-1]) if len(nodes_s) else 0.0
    t_parts: list[FloatArray] = []
    s_parts: list[FloatArray] = []
    v_parts: list[FloatArray] = []
    dropped = 0
    if not plan.profiles:
        t = np.zeros(1)
        s = np.zeros(1)
        v = np.zeros(1)
    elif per_segment:
        t0 = 0.0
        first = True
        seg_start: FloatArray | None = None  # built on demand: most plans skip no segment
        run_start = -1.0  # global start time of the current run of skipped segments
        run_time = 0.0

        def fill_run() -> None:
            # port deviation (module docstring): a skipped run of >= 1 cycle gets global samples
            nonlocal t0, run_start, run_time
            m = int(run_time / dt + _EPS) if run_start >= 0.0 and not first else 0
            if m > 0:
                tg = run_start + dt * np.arange(1, m + 1, dtype=np.float64)
                sg, vg, _ = plan.state(tg)
                t_parts.append(t0 + dt * np.arange(1, m + 1, dtype=np.float64))
                s_parts.append(sg)
                v_parts.append(vg)
                t0 += m * dt
            run_start, run_time = -1.0, 0.0

        for i, prof in enumerate(plan.profiles):
            base = float(nodes_s[i])
            is_dropped = segment_dropped(prof.length, prof.vs, prof.ve)
            n = int(prof.total_time / dt + 0.5)
            if is_dropped or (n == 0 and not first):
                dropped += int(is_dropped)
                if run_start < 0.0:
                    if seg_start is None:
                        seg_start = np.concatenate(
                            ([0.0], np.cumsum([p.total_time for p in plan.profiles]))
                        )
                    run_start = float(seg_start[i])
                run_time += prof.total_time
                continue
            fill_run()
            k = np.arange(0 if first else 1, n + 1, dtype=np.float64)
            tk = k * dt
            sk, vk, _ = prof.state(tk)
            t_parts.append(t0 + tk)
            s_parts.append(base + sk)
            v_parts.append(vk)
            t0 += n * dt
            first = False
        fill_run()
        if not s_parts:
            t_parts, s_parts, v_parts = [np.zeros(1)], [np.zeros(1)], [np.zeros(1)]
        if len(s_parts) == 1:  # one profile (every rapid, and most single-glyph contours)
            t, s, v = t_parts[0], s_parts[0], v_parts[0]
        else:
            t = np.concatenate(t_parts)
            s = np.concatenate(s_parts)
            v = np.concatenate(v_parts)
    else:
        n = int(np.ceil(plan.total_time / dt - _EPS))
        t = np.arange(n + 1, dtype=np.float64) * dt
        s, v, _ = plan.state(t)
    if total > 0.0 and s[-1] < total - 1e-9:
        t = np.append(t, t[-1] + dt)
        s = np.append(s, total)
        v = np.append(v, 0.0)
    s = np.minimum(s, total)
    if geometry is not None and total > 0.0:
        xy = geometry.point_at(s * (geometry.length / total))
    else:
        xy = np.zeros((len(s), 2))
    return SampledMotion(t, s, v, xy, dropped)


def plan_line(
    p0: Sequence[float],
    p1: Sequence[float],
    params: PlannerParams | RapidParams,
    *,
    feed: float | None = None,
) -> tuple[VelocityPlan, PathGeometry]:
    """Plan a straight move (rapid or cut) as a one-piece contour starting and ending at rest.

    A :class:`RapidParams` block becomes ``PlannerParams(acc, acc_time, 0.05, vmax)``
    (A6 §1.4 rapid block; the rapid planner call itself was not traced, UNVERIFIED).
    """
    a = np.asarray(p0, dtype=np.float64)
    b = np.asarray(p1, dtype=np.float64)
    length = float(np.hypot(*(b - a)))
    if isinstance(params, RapidParams):
        pp = PlannerParams(params.acc, params.acc_time, 0.05, params.vmax)
    else:
        pp = params
    speed = pp.vmax if feed is None else min(feed, pp.vmax)
    pieces = PieceList.from_arrays([length] if length > 0 else [], feed=speed)
    plan = plan_velocity(pieces, pp)
    geom = PathGeometry(np.array([0.0, length]), np.vstack((a, b)), np.array([False]))
    return plan, geom
