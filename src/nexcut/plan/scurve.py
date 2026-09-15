"""Per-segment velocity profiles and sampling (analysis 05 §7.1, §7.5, §7.6).

* :func:`jerk_from_acc_time` - ``J = 2A/Ta`` or ``J = 4 Vmax/Ta^2`` (05 §7.1, ``plan``
  ``0x1001682c-0x1001685d``), with the ``Ta`` clamp ``[0.06, 0.25]`` s (``0x1001675b``).
* :class:`Profile` / :func:`plan_segment` - the 7-phase jerk-limited S-curve of the profile
  generator ``0x1000eeb0`` (type 2) and the plain trapezoid of ``0x1000f5a0`` (any other type):
  ``{L, vs, vmax, ve, A, J}`` -> ``t1..t7``; the cruise speed is reduced when the acceleration
  and deceleration distances do not fit into ``L`` (05 §7.5).
* :func:`transition_distance` / :func:`reachable_speed` - the distance of a rest-free speed
  change and its inverse (closed form: Cardano cubic / quadratic, 05 §7.4 "closed-form cubic").
* :func:`seg_interp`, :func:`seg_interp_time`, :func:`arc_interp` - the three ``MotionCtrl.dll``
  exports with the exact validator thresholds and return-value semantics of 05 §7.6.

Units: mm, mm/s, mm/s^2, mm/s^3, seconds internally, milliseconds at the ``*_interp`` API
boundary (05 §7.6 "dt supplied in ms, converted with 0.001; times reported x1000").

Everything that evaluates a profile over many instants is numpy-vectorised.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = [
    "ACC_TIME_MAX",
    "ACC_TIME_MIN",
    "PROFILE_SCURVE",
    "Profile",
    "arc_interp",
    "clamp_acc_time",
    "jerk_from_acc_time",
    "plan_segment",
    "reachable_speed",
    "sample_profile",
    "seg_interp",
    "seg_interp_time",
    "seg_interp_validate",
    "transition_distance",
    "transition_time",
]

FloatArray = NDArray[np.float64]

ACC_TIME_MIN = 0.06
"""Lower clamp of ``Ta`` [s] in ``plan`` (05 §7.1, ``.rdata:0x100199b0``)."""
ACC_TIME_MAX = 0.25
"""Upper clamp of ``Ta`` [s] in ``plan`` (05 §7.1, ``.rdata:0x10019800``)."""
PROFILE_SCURVE = 2
"""``type`` value selecting the 7-phase S-curve in ``segInterp`` (05 §7.5/7.6); others = trapezoid."""

# segInterp validator thresholds, 05 §7.6 (validator 0x10010090, verifier re-traced)
MIN_LENGTH = 1e-4
VMAX_MIN = 0.001
VMAX_MAX = 10000.0
CYCLE_S = 0.00025
MAX_LENGTH = 1e6
ACC_MIN = 5.0
ARC_JERK_MIN = 2.0
ARC_DT_MIN_MS = 0.01

_REL_TOL = 1e-9


def clamp_acc_time(acc_time: float) -> float:
    """Clamp ``Ta`` to ``[0.06, 0.25]`` s as ``plan`` does (05 §7.1)."""
    return min(max(acc_time, ACC_TIME_MIN), ACC_TIME_MAX)


def jerk_from_acc_time(acc: float, acc_time: float, vmax: float) -> float:
    """Jerk used by the planner core (05 §7.1, ``0x10016836-0x1001685d``).

    ``if A*Ta/2 < Vmax: J = 2A/Ta else J = 4 Vmax/Ta^2`` (at equality both coincide).
    ``acc_time`` is used as given; call :func:`clamp_acc_time` first to mirror ``plan``.
    """
    if acc_time <= 0.0:
        raise ValueError("acc_time must be > 0")
    if acc * acc_time / 2.0 < vmax:
        return 2.0 * acc / acc_time
    return 4.0 * vmax / (acc_time * acc_time)


# --------------------------------------------------------------------------- transitions
def transition_time(dv: ArrayLike, acc: float, jerk: float) -> FloatArray:
    """Duration of a jerk-limited speed change by ``|dv|`` starting and ending at zero acceleration.

    05 §7.5: ``if dv <= A^2/J: t1 = t3 = sqrt(dv/J), t2 = 0 else t1 = t3 = A/J,
    t2 = (dv - J t1^2)/A``; total ``2 t1 + t2``.  ``jerk = inf`` gives the trapezoid ``dv/A``.
    """
    d = np.abs(np.asarray(dv, dtype=np.float64))
    if math.isinf(jerk):
        return d / acc
    thr = acc * acc / jerk
    return np.where(d <= thr, 2.0 * np.sqrt(d / jerk), d / acc + acc / jerk)


def transition_distance(v0: ArrayLike, v1: ArrayLike, acc: float, jerk: float) -> FloatArray:
    """Distance travelled while changing speed from ``v0`` to ``v1`` (05 §7.5, ``0x1000ecc0``).

    The acceleration pulse is symmetric, so the mean speed is ``(v0+v1)/2`` and the distance is
    ``(v0+v1)/2 * transition_time``.  Vectorised.
    """
    a = np.asarray(v0, dtype=np.float64)
    b = np.asarray(v1, dtype=np.float64)
    return 0.5 * (a + b) * transition_time(b - a, acc, jerk)


def _reach_scalar(v0: float, length: float, acc: float, jerk: float) -> float:
    if length <= 0.0:
        return v0
    if math.isinf(jerk):
        return math.sqrt(v0 * v0 + 2.0 * acc * length)
    dstar = acc * acc / jerk
    lstar = (2.0 * v0 + dstar) * acc / jerk
    if length <= lstar:
        # x = sqrt(dv): x^3 + 2 v0 x - L sqrt(J) = 0 (depressed cubic, one real root; Cardano)
        p = 2.0 * v0
        q = -length * math.sqrt(jerk)
        disc = math.sqrt((q / 2.0) ** 2 + (p / 3.0) ** 3)
        u = -q / 2.0 + disc
        cu = u ** (1.0 / 3.0)
        x = cu - p / (3.0 * cu) if cu > 0.0 else 0.0
        for _ in range(3):  # Newton polish against cancellation when v0 >> L
            f = x * x * x + p * x + q
            fp = 3.0 * x * x + p
            if fp <= 0.0:
                break
            x -= f / fp
        x = max(x, 0.0)
        return v0 + x * x
    # (2 v0 + dv)(dv/A + A/J) = 2 L  ->  dv^2/A + dv (A/J + 2 v0/A) + (2 v0 A/J - 2 L) = 0
    bq = acc / jerk + 2.0 * v0 / acc
    cq = 2.0 * v0 * acc / jerk - 2.0 * length
    disc = bq * bq - 4.0 * cq / acc
    dv = (-2.0 * cq) / (bq + math.sqrt(max(disc, 0.0)))
    return v0 + max(dv, 0.0)


def reachable_speed(
    v0: ArrayLike, length: ArrayLike, acc: float, jerk: float
) -> FloatArray | float:
    """Highest speed reachable from ``v0`` within ``length`` (also: highest entry speed that can
    still slow down to ``v0``) - the closed-form solver of the look-ahead core (05 §7.4,
    ``0x10012220/0x10012490``: Cardano constants 1/3, 27, 0.25, 0.5).  Scalars -> float."""
    v = np.asarray(v0, dtype=np.float64)
    L = np.asarray(length, dtype=np.float64)
    if v.ndim == 0 and L.ndim == 0:
        return _reach_scalar(float(v), float(L), acc, jerk)
    vb, Lb = np.broadcast_arrays(v, L)
    out = np.empty(vb.shape, dtype=np.float64)
    for idx in np.ndindex(vb.shape):
        out[idx] = _reach_scalar(float(vb[idx]), float(Lb[idx]), acc, jerk)
    return out


# ------------------------------------------------------------------------------ profile
@dataclass(slots=True)
class Profile:
    """Profile descriptor of 05 §7.5 (``esi`` block): ``{L, vs, vmax, ve, A, J, t1..t7}``.

    ``kind="scurve"`` is type 2 (phase jerks ``+J, 0, -J, 0, -J, 0, +J``); ``kind="trapezoid"``
    uses ``jerk = inf`` with ``t1 = t3 = t5 = t7 = 0`` (05 §7.5 ``0x1000f5a0``).
    ``vmax`` holds the *effective* cruise speed after the in-place reduction.
    ``adjusted`` is True when ``vs``/``ve`` had to be changed because ``L`` cannot hold the
    speed change at all (not a vendor path; UNVERIFIED how ``0x1000eeb0`` behaves then - the
    look-ahead never produces such a segment).
    """

    length: float
    vs: float
    vmax: float
    ve: float
    acc: float
    jerk: float
    t: tuple[float, float, float, float, float, float, float]
    kind: Literal["scurve", "trapezoid"] = "scurve"
    adjusted: bool = False
    _tab: tuple[FloatArray, FloatArray, FloatArray, FloatArray, FloatArray, FloatArray] | None = (
        field(default=None, repr=False, compare=False)
    )

    @property
    def total_time(self) -> float:
        """``t1 + ... + t7`` [s] (``segInterp_time`` reports this x1000, 05 §7.6)."""
        return float(sum(self.t))

    def phase_rows(self) -> list[tuple[float, float, float, float, float]]:
        """Per phase ``(duration, jerk, acc0, v0, s0)`` - the analytic state at each phase start."""
        if self.kind == "scurve":
            j = self.jerk
            jerks = (j, 0.0, -j, 0.0, -j, 0.0, j)
        else:
            jerks = (0.0,) * 7
            fixed = (0.0, self.acc, 0.0, 0.0, 0.0, -self.acc, 0.0)
        rows: list[tuple[float, float, float, float, float]] = []
        v, pos, a = self.vs, 0.0, 0.0
        for k in range(7):
            a_k = a if self.kind == "scurve" else fixed[k]
            dt = self.t[k]
            jk = jerks[k]
            rows.append((dt, jk, a_k, v, pos))
            pos = pos + v * dt + a_k * dt * dt / 2.0 + jk * dt**3 / 6.0
            v = v + a_k * dt + jk * dt * dt / 2.0
            a = a_k + jk * dt
        return rows

    def _table(
        self,
    ) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, FloatArray, FloatArray]:
        if self._tab is not None:
            return self._tab
        rows = np.asarray(self.phase_rows(), dtype=np.float64)
        dur = rows[:, 0]
        starts = np.concatenate(([0.0], np.cumsum(dur)[:-1]))
        self._tab = (starts, dur, rows[:, 1], rows[:, 2], rows[:, 3], rows[:, 4])
        return self._tab

    def state(self, time: ArrayLike) -> tuple[FloatArray, FloatArray, FloatArray]:
        """``(s, v, a)`` at the given instants [s], evaluated analytically per phase
        (``v t + a t^2/2 + j t^3/6``, sampler ``0x1000f730``).  Times are clamped to
        ``[0, total_time]``."""
        starts, dur, jerks, a0, v0, s0 = self._table()
        tt = np.clip(np.asarray(time, dtype=np.float64), 0.0, self.total_time)
        k = np.clip(np.searchsorted(starts, tt, side="right") - 1, 0, 6)
        # skip zero-length phases at a boundary: move to the last phase starting <= t
        tau = tt - starts[k]
        j = jerks[k]
        a = a0[k]
        s = s0[k] + v0[k] * tau + a * tau * tau / 2.0 + j * tau**3 / 6.0
        v = v0[k] + a * tau + j * tau * tau / 2.0
        acc = a + j * tau
        return s, v, acc

    def position(self, time: ArrayLike) -> FloatArray:
        """Path position ``s(t)`` [mm] within the segment."""
        return self.state(time)[0]

    def end_position(self) -> float:
        """``s(T)`` - equals ``length`` up to rounding (tested)."""
        return float(self.position(self.total_time))


def _dist(v0: float, v1: float, acc: float, jerk: float) -> tuple[float, float]:
    """Scalar transition distance ``d(v0 -> v1)`` and ``dd/dv1`` (math only, hot path)."""
    dv = abs(v1 - v0)
    sgn = 1.0 if v1 >= v0 else -1.0
    if math.isinf(jerk):
        t, dt = dv / acc, 1.0 / acc
    elif dv <= acc * acc / jerk:
        t = 2.0 * math.sqrt(dv / jerk)
        dt = 1.0 / math.sqrt(jerk * dv) if dv > 0.0 else math.inf
    else:
        t, dt = dv / acc + acc / jerk, 1.0 / acc
    return 0.5 * (v0 + v1) * t, 0.5 * t + 0.5 * (v0 + v1) * dt * sgn


def _phases(
    vs: float, vm: float, ve: float, L: float, acc: float, jerk: float
) -> tuple[float, float, float, float, float, float, float]:
    def side(dv: float) -> tuple[float, float]:
        dv = max(dv, 0.0)
        if math.isinf(jerk):
            return 0.0, dv / acc
        if dv <= acc * acc / jerk:
            return math.sqrt(dv / jerk), 0.0
        t1 = acc / jerk
        return t1, (dv - jerk * t1 * t1) / acc

    t1, t2 = side(vm - vs)
    t5, t6 = side(vm - ve)
    cruise = L - _dist(vs, vm, acc, jerk)[0] - _dist(vm, ve, acc, jerk)[0]
    t4 = cruise / vm if (vm > 0.0 and cruise > 0.0) else 0.0
    return (t1, t2, t1, t4, t5, t6, t5)


def _cruise_speed(
    vs: float, ve: float, vmax: float, length: float, acc: float, jerk: float
) -> float:
    """Largest ``vm`` in ``[max(vs, ve), vmax]`` with ``d(vs, vm) + d(vm, ve) <= L``.

    Safeguarded Newton in ``u = sqrt(vm - lo)`` (bisection fallback); returns a feasible speed
    within ``1e-11 L`` of the distance budget.
    """
    lo = max(vs, ve)
    hi = max(vmax, lo)

    def g(v: float) -> tuple[float, float]:
        d1, s1 = _dist(vs, v, acc, jerk)
        d2, s2 = _dist(ve, v, acc, jerk)  # symmetric: d(v -> ve) == d(ve -> v)
        return d1 + d2 - length, s1 + s2

    ghi, dhi = g(hi)
    if ghi <= 0.0:
        return hi
    if g(lo)[0] >= 0.0:  # even the lowest cruise does not fit (caller adjusts vs/ve first)
        return lo
    if vs == ve:  # symmetric: closed form d(v, vm) = L/2
        return min(hi, _reach_scalar(vs, 0.5 * length, acc, jerk))
    # Newton in u = sqrt(vm - lo): removes the square-root singularity of the jerk branch at lo
    ua, ub = 0.0, math.sqrt(hi - lo)
    u, gx, dx = ub, ghi, dhi * 2.0 * ub
    best = lo  # largest speed known to fit
    gtol = 1e-11 * max(length, 1e-6)
    for _ in range(100):
        if gx > 0.0:
            ub = u
        else:
            ua, best = u, lo + u * u
            if -gx <= gtol:
                break
        if ub - ua <= 1e-15 * max(1.0, ub):
            break
        if dx > 0.0 and math.isfinite(dx):
            step = gx / dx
            if gx > 0.0 and step <= 1e-12 * max(1.0, u):
                step = max(4.0 * step, 1e-12 * max(1.0, u))  # converged from above: step inside
            nxt = u - step
        else:
            nxt = 0.5 * (ua + ub)
        if not (ua < nxt < ub):
            nxt = 0.5 * (ua + ub)
        u = nxt
        gx, dv = g(lo + u * u)
        dx = dv * 2.0 * u
    return best


def plan_segment(
    length: float,
    vs: float,
    vmax: float,
    ve: float,
    acc: float,
    jerk: float,
    profile_type: int = PROFILE_SCURVE,
) -> Profile:
    """Build the profile of one segment (05 §7.5, generator ``0x1000eeb0`` / ``0x1000f5a0``).

    1. ``vmax`` is raised to ``max(vs, ve)`` if lower (``0x1000eee3-0x1000eeed``);
    2. accel/decel phase times from the ``(vmax - v) <= A^2/J`` test;
    3. if ``d_acc + d_dec > L`` the cruise speed is reduced (branch ``0x1000f0be``) - here solved
       by safeguarded Newton to ``1e-12`` relative, tighter than the vendor's 0.001/0.01 tolerances;
    4. ``t4 = (L - d_acc - d_dec) / vmax``.

    ``profile_type != 2`` -> trapezoid with ``J = inf`` (``v^2 = vs^2 + 2 a L``).
    If even ``vs -> ve`` does not fit into ``L`` the end speed (accelerating) or start speed
    (decelerating) is lowered to the reachable value and ``adjusted`` is set (UNVERIFIED, see
    :class:`Profile`).
    """
    if length < 0 or acc <= 0 or jerk <= 0:
        raise ValueError("length must be >= 0, acc and jerk > 0")
    kind: Literal["scurve", "trapezoid"] = (
        "scurve" if profile_type == PROFILE_SCURVE else "trapezoid"
    )
    j = jerk if kind == "scurve" else math.inf
    vs = max(vs, 0.0)
    ve = max(ve, 0.0)
    adjusted = False
    need = _dist(vs, ve, acc, j)[0]
    if need > length * (1.0 + _REL_TOL) + 1e-12:
        adjusted = True
        if ve > vs:
            ve = min(ve, _reach_scalar(vs, length, acc, j))
        else:
            vs = min(vs, _reach_scalar(ve, length, acc, j))
    vm = _cruise_speed(vs, ve, vmax, length, acc, j)
    t = _phases(vs, vm, ve, length, acc, j)
    return Profile(length, vs, vm, ve, acc, j, t, kind, adjusted)


# ------------------------------------------------------------------------------ sampling
def sample_profile(profile: Profile, dt_ms: float) -> FloatArray:
    """Sampler ``0x1000f730``: ``N = int(T/dt*1000 + 0.5)``, then ``s(t_k)/L`` for ``k = 0..N``
    (05 §7.6).  One double per interpolation cycle; ``t_k = k*dt``; instants past ``T`` are
    evaluated at ``T`` (UNVERIFIED: the vendor keeps evaluating the last phase; the difference is
    at most half a cycle)."""
    dt = dt_ms / 1000.0
    n = int(profile.total_time / dt + 0.5)
    t = np.arange(n + 1, dtype=np.float64) * dt
    if profile.length <= 0.0:
        return np.ones(n + 1)
    return profile.position(t) / profile.length


def seg_interp_validate(
    length: float, vmax: float, vs: float, ve: float, acc: float
) -> tuple[int, float, float]:
    """Validator ``0x10010090`` (05 §7.6, order as re-traced by the verifier).

    Returns ``(code, vs, ve)`` with code ``1`` = skip, ``-2`` = parameter error, ``0`` = ok;
    on ``0`` ``vs``/``ve`` are clamped to ``vmax``.
    """
    if length < MIN_LENGTH:
        return 1, vs, ve
    if vmax < VMAX_MIN or vmax > VMAX_MAX:
        return -2, vs, ve
    if length < CYCLE_S * min(vs, ve):
        return 1, vs, ve
    if vs < 0.0 or ve < 0.0:
        return -2, vs, ve
    if length > MAX_LENGTH:
        return -2, vs, ve
    if acc < ACC_MIN:
        return -2, vs, ve
    return 0, min(vs, vmax), min(ve, vmax)


def seg_interp(
    length: float,
    vmax: float,
    vs: float,
    ve: float,
    acc: float,
    jerk: float,
    dt_ms: float,
    profile_type: int = PROFILE_SCURVE,
) -> tuple[int, FloatArray]:
    """``int segInterp(vector<double>*, L, vmax, vs, ve, acc, jerk, dt_ms, type)`` (05 §7.6).

    Returns ``(0, samples)`` on success, ``(0, empty)`` when the validator says *skip* (the
    output vector was cleared first, ``0x100101e0``) and ``(-2, empty)`` on a parameter error.
    ``dt`` is not validated (EVIDENCE) - a non-positive ``dt_ms`` raises ``ValueError`` here
    instead of looping forever (deviation, safety).
    """
    code, vs, ve = seg_interp_validate(length, vmax, vs, ve, acc)
    empty = np.empty(0, dtype=np.float64)
    if code == 1:
        return 0, empty
    if code != 0:
        return -2, empty
    if dt_ms <= 0.0:
        raise ValueError("dt_ms must be > 0")
    prof = plan_segment(length, vs, vmax, ve, acc, jerk, profile_type)
    return 0, sample_profile(prof, dt_ms)


def seg_interp_time(
    length: float,
    vmax: float,
    vs: float,
    ve: float,
    acc: float,
    jerk: float,
    profile_type: int = PROFILE_SCURVE,
) -> tuple[int, float]:
    """``int segInterp_time(double* t_ms, ...)`` (``0x10010260``): always returns 0; ``t_ms`` is
    0 unless the validator passes, then ``(t1+...+t7)*1000`` (05 §7.6)."""
    code, vs, ve = seg_interp_validate(length, vmax, vs, ve, acc)
    if code != 0:
        return 0, 0.0
    prof = plan_segment(length, vs, vmax, ve, acc, jerk, profile_type)
    return 0, prof.total_time * 1000.0


def arc_interp(
    theta0: float,
    theta1: float,
    radius: float,
    vmax: float,
    vs: float,
    ve: float,
    acc_x: float,
    acc_y: float,
    jerk_x: float,
    jerk_y: float,
    dt_ms: float,
    profile_type: int = PROFILE_SCURVE,
) -> tuple[int, FloatArray]:
    """``arcInterp`` (``0x10010320``, 05 §7.6): ``L = |theta1 - theta0| * r``,
    ``acc = min(accX, accY)``, ``jerk = min(jerkX, jerkY)``; additionally requires
    ``jerkX, jerkY >= 2``, ``dt >= 0.01`` ms and ``r >= 0``.  Returns the **sample count**
    (``(end-begin)>>3``) and the samples; ``-2`` on a parameter error.  UNVERIFIED: the order of
    the extra checks relative to the common validator, and that *skip* yields count 0.
    """
    empty = np.empty(0, dtype=np.float64)
    if jerk_x < ARC_JERK_MIN or jerk_y < ARC_JERK_MIN or dt_ms < ARC_DT_MIN_MS or radius < 0.0:
        return -2, empty
    length = abs(theta1 - theta0) * radius
    acc = min(acc_x, acc_y)
    jerk = min(jerk_x, jerk_y)
    code, vs, ve = seg_interp_validate(length, vmax, vs, ve, acc)
    if code == 1:
        return 0, empty
    if code != 0:
        return -2, empty
    prof = plan_segment(length, vs, vmax, ve, acc, jerk, profile_type)
    out = sample_profile(prof, dt_ms)
    return int(out.size), out
