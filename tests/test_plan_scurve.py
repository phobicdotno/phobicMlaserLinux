"""7-phase S-curve, trapezoid, reachable speed and the segInterp exports (analysis 05 §7.1, §7.5, §7.6)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from nexcut.plan.scurve import (
    ACC_TIME_MAX,
    ACC_TIME_MIN,
    arc_interp,
    clamp_acc_time,
    jerk_from_acc_time,
    plan_segment,
    reachable_speed,
    sample_profile,
    seg_interp,
    seg_interp_time,
    seg_interp_validate,
    transition_distance,
    transition_time,
)

A, J = 6000.0, 60000.0  # CF1390 cut acc; J = 2A/Ta with Ta = 0.2 s


# ------------------------------------------------------------------ jerk / Ta (05 §7.1)
def test_acc_time_clamp() -> None:
    assert (ACC_TIME_MIN, ACC_TIME_MAX) == (0.06, 0.25)
    assert clamp_acc_time(0.01) == 0.06
    assert clamp_acc_time(0.2) == 0.2
    assert clamp_acc_time(1.0) == 0.25


def test_jerk_branches() -> None:
    # A*Ta/2 = 600 < Vmax 750 -> 2A/Ta
    assert jerk_from_acc_time(6000, 0.2, 750) == pytest.approx(60000)
    # A*Ta/2 = 600 >= Vmax 100 -> 4 Vmax / Ta^2
    assert jerk_from_acc_time(6000, 0.2, 100) == pytest.approx(10000)
    # equality: both coincide
    assert jerk_from_acc_time(6000, 0.2, 600) == pytest.approx(2 * 6000 / 0.2)
    assert jerk_from_acc_time(2000, 0.125, 200) == pytest.approx(32000)  # MotionCtrl defaults
    with pytest.raises(ValueError):
        jerk_from_acc_time(1, 0, 1)


def test_jerk_semantics_match_lang_description() -> None:
    """lang de2/de4: Ta = time from standstill to max speed (05 §7.1)."""
    # second branch: a triangular acceleration profile reaches Vmax exactly at Ta
    vmax, ta = 100.0, 0.2
    j = jerk_from_acc_time(6000, ta, vmax)
    assert transition_time(vmax, 1e12, j) == pytest.approx(ta)
    # first branch: A is reached at Ta/2
    j = jerk_from_acc_time(6000, ta, 750)
    assert 6000 / j == pytest.approx(ta / 2)


# -------------------------------------------------------------------- transitions (§7.5)
def test_transition_time_regimes() -> None:
    thr = A * A / J  # 600
    assert transition_time(150.0, A, J) == pytest.approx(2 * math.sqrt(150 / J))
    assert transition_time(thr, A, J) == pytest.approx(2 * A / J)
    assert transition_time(1000.0, A, J) == pytest.approx(1000 / A + A / J)
    assert transition_time(-1000.0, A, J) == pytest.approx(1000 / A + A / J)
    assert transition_time(300.0, A, math.inf) == pytest.approx(300 / A)


def test_transition_distance_is_mean_speed_times_time() -> None:
    for v0, v1 in ((0, 100), (20, 700), (500, 10), (0, 0)):
        d = float(transition_distance(v0, v1, A, J))
        p = plan_segment(max(d, 1e-9) * (1 + 1e-12), v0, max(v0, v1), v1, A, J)
        assert p.end_position() == pytest.approx(max(d, 1e-9), rel=1e-9, abs=1e-9)
        assert d == pytest.approx((v0 + v1) / 2 * float(transition_time(v1 - v0, A, J)))


@pytest.mark.parametrize("v0", [0.0, 3.0, 50.0, 400.0])
@pytest.mark.parametrize("length", [1e-4, 0.05, 1.0, 17.0, 250.0, 5000.0])
@pytest.mark.parametrize("jerk", [J, 10000.0, math.inf])
def test_reachable_speed_inverts_transition_distance(v0: float, length: float, jerk: float) -> None:
    v1 = reachable_speed(v0, length, A, jerk)
    assert v1 >= v0
    # abs term: v1 - v0 loses digits when the speed change is tiny (ill-conditioned round trip)
    assert float(transition_distance(v0, v1, A, jerk)) == pytest.approx(length, rel=1e-9, abs=1e-8)


def test_reachable_speed_vectorised() -> None:
    v = reachable_speed(np.array([0.0, 10.0]), np.array([1.0, 2.0]), A, J)
    assert v.shape == (2,)
    assert v[1] == pytest.approx(reachable_speed(10.0, 2.0, A, J))
    assert reachable_speed(5.0, 0.0, A, J) == 5.0


# ------------------------------------------------------------------------ profiles (§7.5)
def _check_profile(p, vmax_cap: float) -> None:
    t = np.linspace(0.0, p.total_time, 4001)
    s, v, a = p.state(t)
    assert p.end_position() == pytest.approx(p.length, rel=1e-9, abs=1e-9)
    assert s[0] == 0.0
    assert v[0] == pytest.approx(p.vs, abs=1e-9)
    assert v[-1] == pytest.approx(p.ve, abs=1e-6)
    assert np.all(np.diff(s) >= -1e-12)
    assert np.max(v) <= max(vmax_cap, p.vs, p.ve) + 1e-6
    assert np.max(np.abs(a)) <= p.acc + 1e-6
    # velocity continuous (no jumps larger than a*dt)
    dt = t[1] - t[0]
    assert np.max(np.abs(np.diff(v))) <= p.acc * dt * 1.01 + 1e-9


def test_full_seven_phase_profile() -> None:
    p = plan_segment(500.0, 0.0, 750.0, 0.0, A, J)
    t1, t2, t3, t4, t5, t6, t7 = p.t
    assert t1 == t3 == pytest.approx(A / J)
    assert t2 == pytest.approx((750 - J * t1 * t1) / A)
    assert (t5, t6, t7) == (t1, t2, t3)
    assert t4 > 0
    assert p.vmax == 750.0
    _check_profile(p, 750.0)


def test_non_saturating_acceleration_branch() -> None:
    p = plan_segment(100.0, 0.0, 200.0, 50.0, A, J)
    assert p.t[1] == 0.0 and p.t[0] == pytest.approx(math.sqrt(200 / J))
    assert p.t[4] == pytest.approx(math.sqrt(150 / J)) and p.t[5] == 0.0
    _check_profile(p, 200.0)


def test_cruise_reduced_when_segment_short() -> None:
    p = plan_segment(1.0, 0.0, 750.0, 0.0, A, J)
    assert p.vmax < 750.0
    assert p.t[3] == pytest.approx(0.0, abs=1e-9)
    assert float(transition_distance(0, p.vmax, A, J)) * 2 == pytest.approx(1.0, rel=1e-9)
    _check_profile(p, 750.0)


def test_vmax_raised_to_boundary_speeds() -> None:
    """``vmax`` below ``max(vs, ve)`` is raised (``0x1000eee3-0x1000eeed``)."""
    p = plan_segment(50.0, 80.0, 40.0, 60.0, A, J)
    assert p.vmax == pytest.approx(80.0)
    _check_profile(p, 80.0)


def test_infeasible_speed_change_is_adjusted() -> None:
    p = plan_segment(1.0, 50.0, 200.0, 10.0, A, J)
    assert p.adjusted
    assert float(transition_distance(p.vs, p.ve, A, J)) <= 1.0 + 1e-9
    p = plan_segment(0.1, 0.0, 200.0, 100.0, A, J)
    assert p.adjusted and p.ve < 100.0
    _check_profile(p, 200.0)


def test_trapezoid_fallback() -> None:
    p = plan_segment(10.0, 0.0, 200.0, 0.0, A, J, profile_type=1)
    assert p.kind == "trapezoid"
    assert p.t[0] == p.t[2] == p.t[4] == p.t[6] == 0.0
    assert p.t[1] == pytest.approx(200 / A)
    _check_profile(p, 200.0)
    short = plan_segment(1.0, 0.0, 200.0, 0.0, A, J, profile_type=0)
    assert short.vmax == pytest.approx(math.sqrt(A * 1.0))  # v^2 = vs^2 + 2 a (L/2)


def test_invalid_arguments() -> None:
    with pytest.raises(ValueError):
        plan_segment(-1, 0, 1, 0, A, J)
    with pytest.raises(ValueError):
        plan_segment(1, 0, 1, 0, 0, J)


# -------------------------------------------------------------- segInterp family (§7.6)
def test_validator_thresholds() -> None:
    ok = (10.0, 100.0, 0.0, 0.0, 6000.0)
    assert seg_interp_validate(*ok)[0] == 0
    assert seg_interp_validate(0.99e-4, 100, 0, 0, 6000)[0] == 1  # L < 1e-4 -> skip
    assert seg_interp_validate(1e-4, 100, 0, 0, 6000)[0] == 0
    assert seg_interp_validate(10, 0.00099, 0, 0, 6000)[0] == -2  # vmax < 0.001
    assert seg_interp_validate(10, 0.001, 0, 0, 6000)[0] == 0
    assert seg_interp_validate(10, 10000.0, 0, 0, 6000)[0] == 0
    assert seg_interp_validate(10, 10000.1, 0, 0, 6000)[0] == -2  # vmax > 10000
    # shorter than one 250 us cycle at the entry speed -> dropped
    assert seg_interp_validate(0.0249, 1000, 100, 200, 6000)[0] == 1
    assert seg_interp_validate(0.025, 1000, 100, 200, 6000)[0] == 0
    assert seg_interp_validate(0.02, 1000, 200, 100, 6000)[0] == 1  # min(vs, ve)
    assert seg_interp_validate(10, 100, -1, 0, 6000)[0] == -2
    assert seg_interp_validate(10, 100, 0, -0.1, 6000)[0] == -2
    assert seg_interp_validate(1e6, 100, 0, 0, 6000)[0] == 0
    assert seg_interp_validate(1e6 + 1, 100, 0, 0, 6000)[0] == -2
    assert seg_interp_validate(10, 100, 0, 0, 4.999)[0] == -2  # acc < 5
    assert seg_interp_validate(10, 100, 0, 0, 5.0)[0] == 0  # not <=
    # order: the skip on L < 1e-4 wins over a bad vmax; bad vmax wins over short-cycle skip
    assert seg_interp_validate(1e-5, 0, 0, 0, 0)[0] == 1
    assert seg_interp_validate(0.001, 0, 100, 100, 6000)[0] == -2
    # vs / ve clamped to vmax
    assert seg_interp_validate(10, 50, 80, 90, 6000) == (0, 50, 50)


def test_seg_interp_samples_and_returns() -> None:
    code, out = seg_interp(10.0, 100.0, 0.0, 0.0, A, J, 0.25)
    assert code == 0
    p = plan_segment(10.0, 0.0, 100.0, 0.0, A, J)
    n = int(p.total_time / 0.25 * 1000 + 0.5)
    assert out.size == n + 1
    assert out[0] == 0.0
    assert out[-1] == pytest.approx(1.0)
    assert np.all(np.diff(out) >= -1e-15)
    assert np.allclose(out, sample_profile(p, 0.25))
    # skip -> 0 with an empty vector; error -> -2
    assert (
        seg_interp(5e-5, 100, 0, 0, A, J, 0.25)[0] == 0
        and seg_interp(5e-5, 100, 0, 0, A, J, 0.25)[1].size == 0
    )
    assert seg_interp(10, 100, 0, 0, 4, J, 0.25)[0] == -2
    with pytest.raises(ValueError):
        seg_interp(10, 100, 0, 0, A, J, 0.0)


def test_sampler_count_rounding() -> None:
    p = plan_segment(10.0, 0.0, 100.0, 0.0, A, J)
    for dt in (0.25, 1.0, 0.1, 7.3):
        assert sample_profile(p, dt).size == int(p.total_time / dt * 1000 + 0.5) + 1


def test_seg_interp_time() -> None:
    code, t = seg_interp_time(500.0, 750.0, 0.0, 0.0, A, J)
    assert code == 0
    assert t == pytest.approx(plan_segment(500.0, 0.0, 750.0, 0.0, A, J).total_time * 1000)
    # validator failure: t_ms stays 0, return value still 0
    assert seg_interp_time(10, 100, 0, 0, 1, J) == (0, 0.0)
    assert seg_interp_time(1e-5, 100, 0, 0, A, J) == (0, 0.0)
    # rest-to-rest with cruise: (7 phases) closed form
    t1 = A / J
    t2 = (750 - J * t1 * t1) / A
    d = 750 / 2 * (2 * t1 + t2) * 2
    assert t / 1000 == pytest.approx(2 * (2 * t1 + t2) + (500 - d) / 750)


def test_arc_interp() -> None:
    r, th0, th1 = 20.0, 0.0, math.pi / 2
    n, out = arc_interp(th0, th1, r, 100.0, 0.0, 0.0, 6000, 8000, 60000, 50000, 0.25)
    assert n == out.size > 0
    _, ref = seg_interp(abs(th1 - th0) * r, 100.0, 0.0, 0.0, 6000, 50000, 0.25)
    assert np.allclose(out, ref)  # acc = min, jerk = min, L = |dtheta| r
    assert arc_interp(0, 1, 10, 100, 0, 0, A, A, 1.9, J, 0.25)[0] == -2
    assert arc_interp(0, 1, 10, 100, 0, 0, A, A, J, J, 0.009)[0] == -2
    assert arc_interp(0, 1, -1, 100, 0, 0, A, A, J, J, 0.25)[0] == -2
    assert arc_interp(0, 1e-6, 10, 100, 0, 0, A, A, J, J, 0.25)[0] == 0


# ------------------------------------------------------------------ ruckig cross-check
CASES = [
    (100.0, 0.0, 750.0, 0.0, 6000.0, 60000.0),
    (1.0, 0.0, 750.0, 0.0, 6000.0, 60000.0),
    (10.0, 0.0, 200.0, 0.0, 6000.0, 60000.0),
    (50.0, 20.0, 150.0, 5.0, 2000.0, 40000.0),
    (0.5, 3.0, 100.0, 3.0, 6000.0, 60000.0),
    (300.0, 100.0, 500.0, 0.0, 9000.0, 72000.0),
    (25.0, 0.0, 250.0, 0.0, 6000.0, 60000.0),
]


@pytest.mark.parametrize("case", CASES)
def test_cross_check_against_ruckig(case: tuple[float, ...]) -> None:
    """Time-optimal 1-DoF ruckig trajectory with the same limits has the same duration."""
    ruckig = pytest.importorskip("ruckig")
    length, vs, vmax, ve, acc, jerk = case
    inp = ruckig.InputParameter(1)
    inp.current_position, inp.current_velocity, inp.current_acceleration = [0.0], [vs], [0.0]
    inp.target_position, inp.target_velocity, inp.target_acceleration = [length], [ve], [0.0]
    inp.max_velocity, inp.max_acceleration, inp.max_jerk = [vmax], [acc], [jerk]
    traj = ruckig.Trajectory(1)
    assert ruckig.Ruckig(1).calculate(inp, traj) == ruckig.Result.Working
    prof = plan_segment(length, vs, vmax, ve, acc, jerk)
    assert prof.total_time == pytest.approx(traj.duration, rel=1e-9, abs=1e-12)
    for t in np.linspace(0.0, traj.duration, 9):
        pos, vel, _ = traj.at_time(float(t))
        s, v, _ = prof.state(float(t))
        assert float(s) == pytest.approx(pos[0], abs=1e-7)
        assert float(v) == pytest.approx(vel[0], abs=1e-5)
