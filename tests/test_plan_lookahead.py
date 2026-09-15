"""Node builder, slow start/end and look-ahead (analysis 05 §7.1, §7.2, §7.4)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from nexcut.plan.contour_fit import PieceList, line_glyph, process
from nexcut.plan.junction import JunctionModel, vendor_arc_speed
from nexcut.plan.lookahead import (
    CADMODULE_NODE_FACTOR,
    NodeList,
    apply_slow_end,
    apply_slow_start,
    build_nodes,
    format_veldecc,
    lookahead,
    plan_velocity,
)
from nexcut.plan.params import PlannerParams
from nexcut.plan.scurve import reachable_speed, transition_distance
from test_plan_golden import scan_path

CF = PlannerParams(acc=6000.0, acc_time=0.2, precision=0.02, vmax=80.0)


def pieces(**kw: object) -> PieceList:
    base: dict[str, object] = {
        "lengths": [10.0, 2.0, 10.0],
        "radius": [math.inf, 1.0, math.inf],
        "feed": 100.0,
    }
    base.update(kw)
    return PieceList.from_arrays(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------- node builder A
def test_node_layout_and_ends_at_rest() -> None:
    n = build_nodes(pieces(), CF)
    assert len(n) == 4
    assert n.s.tolist() == [0.0, 10.0, 12.0, 22.0]
    assert n.length.tolist() == [0.0, 10.0, 2.0, 10.0]
    assert np.all(n.acc == 6000.0)
    f1 = vendor_arc_speed(1.0, 2.5, 0.02)
    assert f1 == pytest.approx(14.0)
    assert n.v_limit.tolist() == pytest.approx([0.0, 80.0, f1, 0.0])  # min(f, Vmax); ends 0
    # pass 2: min(max(f_prev, f), feed) - uses raw limits, not Vmax
    assert n.v_feed.tolist() == pytest.approx([0.0, 100.0, 100.0, 100.0])


def test_pass4_order_factor_floor_cap_feed() -> None:
    p = pieces(
        lengths=[5.0, 5.0, 5.0, 5.0],
        radius=[math.inf, 1.0, 1.0, math.inf],
        factor=[1.0, 0.5, 1.0, 1.0],
        cap=[math.inf, math.inf, 5.0, math.inf],
        feed=[100, 100, 100, 100],
    )
    params = PlannerParams(acc=6000.0, acc_time=0.2, precision=0.02, vmax=80.0, min_speed=10.0)
    n = build_nodes(p, params)
    # piece 1: min(14, 80) * 0.5 = 7 -> max(7, P4=10) = 10 ; piece 2: 14 -> cap 5
    assert n.v_limit[2] == pytest.approx(10.0)
    assert n.v_limit[3] == pytest.approx(5.0)
    p2 = pieces(feed=[100.0, 3.0, 100.0])
    assert build_nodes(p2, CF).v_limit[2] == pytest.approx(3.0)  # min with feed last


def test_pass2_uses_neighbour_max() -> None:
    p = pieces(lengths=[5, 5, 5], radius=[0.5, 50.0, 0.5], feed=[1000.0, 1000.0, 1000.0])
    n = build_nodes(p, CF)
    f = vendor_arc_speed(np.array([0.5, 50.0, 0.5]), 2.5, 0.02)
    assert n.v_feed[1:].tolist() == pytest.approx([f[0], max(f[0], f[1]), max(f[1], f[2])])


def test_pass3_zero_feed_fill_in() -> None:
    p = pieces(lengths=[1, 1, 1, 1], radius=math.inf, feed=[0.0, 50.0, 0.005, 0.0])
    n = build_nodes(p, CF)
    assert n.v_feed[1:].tolist() == pytest.approx([50.0, 50.0, 50.0, 50.0])


def test_physical_junction_model_option() -> None:
    n = build_nodes(pieces(), CF, JunctionModel("physical", normal_acc=400.0))
    assert n.v_limit[2] == pytest.approx(20.0)


def test_corner_radius_extension_limits_node() -> None:
    p = pieces(lengths=[10.0, 10.0], radius=[math.inf, math.inf], corner_radius=[1.0, math.inf])
    assert build_nodes(p, CF).v_limit[1] == pytest.approx(14.0)


def test_cadmodule_factor_constant() -> None:
    assert CADMODULE_NODE_FACTOR == 0.99


# --------------------------------------------------------------------------- slow start
def slow_nodes() -> NodeList:
    return build_nodes(PieceList.from_arrays([0.5, 0.5, 5.0, 10.0], feed=100.0), CF)


def test_slow_start_inserts_node() -> None:
    n = apply_slow_start(slow_nodes(), 3.0, 10.0)
    assert n.s.tolist() == [0.0, 0.5, 1.0, 3.0, 6.0, 16.0]
    assert n.length.tolist() == pytest.approx([0.0, 0.5, 0.5, 2.0, 3.0, 10.0])
    assert n.v_limit[1:4].tolist() == [10.0, 10.0, 10.0]
    assert n.v_feed[1:4].tolist() == [10.0, 10.0, 10.0]
    assert n.v_feed[4] == 100.0 and n.v_limit[4] == 80.0


def test_slow_start_snaps_to_existing_node() -> None:
    n = apply_slow_start(slow_nodes(), 0.97, 10.0)  # |1.0 - 0.97| <= 0.05, 1.0 + 0.01 > 0.97
    assert len(n) == 5
    assert n.v_limit[2] == 10.0 and n.v_limit[3] == 80.0
    n = apply_slow_start(slow_nodes(), 1.01, 10.0)  # 1.0 + 0.01 <= 1.01 -> clamped by the walk
    assert n.v_limit[2] == 10.0 and len(n) == 6 and n.s[3] == pytest.approx(1.01)


def test_slow_start_activation_and_speed_rules() -> None:
    base = slow_nodes()
    same = apply_slow_start(base, 0.09, 10.0)
    assert same.s.tolist() == base.s.tolist() and same.v_limit.tolist() == base.v_limit.tolist()
    assert apply_slow_start(base, 3.0, 0.01).v_limit[1] == 0.1  # max(0.1, P7)
    assert apply_slow_start(base, 3.0, 10.0, floor=20.0).v_limit[1] == 20.0  # P7 = max(P7, P4)


def test_slow_end_mirror() -> None:
    n = apply_slow_end(slow_nodes(), 4.0, 10.0)  # boundary at 12
    assert n.s.tolist() == [0.0, 0.5, 1.0, 6.0, 12.0, 16.0]
    assert n.v_feed[5] == 10.0 and n.v_limit[4] == 10.0
    assert n.v_feed[4] == 100.0 and n.v_limit[3] == 80.0
    same = apply_slow_end(slow_nodes(), 0.05, 10.0)
    assert len(same) == 5


# ----------------------------------------------------------------------------- look-ahead
def _check_plan(plan, nodes: NodeList, strict: bool = True) -> None:
    v = plan.speed
    assert v[0] == 0.0 and v[-1] == 0.0
    assert np.all(v >= 0.0)
    assert np.all(v <= nodes.v_limit + 1e-9)
    for i in range(1, len(v)):
        dv_ok = transition_distance(
            min(v[i - 1], v[i]), max(v[i - 1], v[i]), plan.profiles[0].acc, plan.jerk
        )
        assert float(dv_ok) <= nodes.length[i] * (1 + 1e-9) + 1e-9
    assert not any(p.adjusted for p in plan.profiles)
    total = sum(p.end_position() for p in plan.profiles)
    assert total == pytest.approx(float(nodes.s[-1]), rel=1e-9)
    t = np.linspace(0.0, plan.total_time, 20001)
    s = plan.position(t)
    assert s[0] == pytest.approx(0.0) and s[-1] == pytest.approx(float(nodes.s[-1]))
    assert np.all(np.diff(s) >= -1e-9)
    vel = plan.velocity(t)
    acc = np.diff(vel) / np.diff(t)
    assert np.max(np.abs(acc)) <= plan.profiles[0].acc * 1.001 + 1e-6


def test_lookahead_simple_contour() -> None:
    p = pieces()
    plan = plan_velocity(p, CF)
    nodes = build_nodes(p, CF)
    _check_plan(plan, nodes)
    assert plan.speed[1] <= 14.0 + 1e-9  # strict: entry of the r = 1 mm arc protected
    assert max(pr.vmax for pr in plan.profiles) <= 80.0 + 1e-9
    assert plan.jerk == pytest.approx(4 * 80.0 / 0.2**2)  # A*Ta/2 = 600 >= Vmax -> 4 Vmax/Ta^2


def test_vendor_rule_set_does_not_protect_next_piece() -> None:
    p = pieces()
    nodes = build_nodes(p, CF)
    plan = lookahead(nodes, CF.vmax, CF.acc, 60000.0, strict=False)
    assert plan.speed[1] > 14.0  # vendor node limit uses only the piece ending at the node
    assert plan.speed[2] <= 14.0 + 1e-9


def test_short_pieces_limit_by_reachability() -> None:
    p = PieceList.from_arrays([0.2] * 10, feed=500.0)
    params = PlannerParams(acc=6000.0, acc_time=0.2, precision=0.02, vmax=500.0)
    plan = plan_velocity(p, params)
    nodes = build_nodes(p, params)
    _check_plan(plan, nodes)
    # every segment profile starts and ends at zero acceleration, so the reachable speed is
    # the per-segment solver iterated, not the one-shot value over the summed length
    v = 0.0
    for _ in range(5):
        v = reachable_speed(v, 0.2, 6000.0, plan.jerk)
    assert plan.speed[5] == pytest.approx(v, rel=1e-9)
    assert v < reachable_speed(0.0, 1.0, 6000.0, plan.jerk)


def test_forced_stop_cap() -> None:
    p = PieceList.from_arrays([20.0, 20.0], feed=100.0, cap=[0.0, math.inf])
    plan = plan_velocity(p, CF)
    assert plan.speed.tolist() == [0.0, 0.0, 0.0]
    _check_plan(plan, build_nodes(p, CF))


def test_plan_with_slow_start_and_end() -> None:
    p = PieceList.from_arrays([50.0], feed=100.0)
    params = PlannerParams(
        acc=6000.0,
        acc_time=0.2,
        precision=0.02,
        vmax=100.0,
        slow_start_len=5.0,
        slow_start_speed=10.0,
        slow_end_len=5.0,
        slow_end_speed=20.0,
    )
    plan = plan_velocity(p, params)
    assert plan.nodes.s.tolist() == [0.0, 5.0, 45.0, 50.0]
    assert plan.speed[1] <= 10.0 and plan.speed[2] <= 20.0
    assert plan.profiles[0].vmax <= 10.0 + 1e-9 and plan.profiles[2].vmax <= 20.0 + 1e-9
    assert plan.profiles[1].vmax == pytest.approx(100.0)
    no_end = plan_velocity(p, params, apply_end_segment=False)
    assert no_end.nodes.s.tolist() == [0.0, 5.0, 50.0]


def test_trapezoid_plan() -> None:
    p = pieces()
    plan = plan_velocity(p, CF, profile_type=1)
    assert all(pr.kind == "trapezoid" for pr in plan.profiles)
    assert plan.total_time < plan_velocity(p, CF).total_time


def test_empty_and_square_contours() -> None:
    sq = [(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)]
    res = process([line_glyph(sq[i], sq[i + 1]) for i in range(4)], 0.02, 250.0)
    assert res.pieces is not None
    params = PlannerParams(acc=6000.0, acc_time=0.2, precision=0.02, vmax=250.0)
    plan = plan_velocity(res.pieces, params)
    _check_plan(plan, build_nodes(res.pieces, params))
    corner = vendor_arc_speed(res.pieces.radius[1], 2.5, 0.02)
    assert np.allclose(plan.speed[1:-1], corner)
    assert max(pr.vmax for pr in plan.profiles) == pytest.approx(250.0)


def test_scan_path_plan(src_dir: Path) -> None:
    res = process(scan_path(src_dir), 0.02, 250.0)
    assert res.pieces is not None
    params = PlannerParams(acc=6000.0, acc_time=0.2, precision=0.02, vmax=250.0)
    plan = plan_velocity(res.pieces, params)
    _check_plan(plan, build_nodes(res.pieces, params))
    v_connector = vendor_arc_speed(0.5, 2.5, 0.02)
    assert v_connector == pytest.approx(7.0)
    assert np.all(plan.speed[1:-1] <= v_connector + 1e-9)  # every node touches a connector
    assert plan.total_time > 1506.62 / 250.0


# ------------------------------------------------------------------------------ VelDecc
def test_veldecc_format() -> None:
    assert (
        format_veldecc([0.0, 12.5]) == b"NodeID:0 V:0.000000 mm/s\r\nNodeID:1 V:12.500000 mm/s\r\n"
    )
    assert format_veldecc([]) == b""
