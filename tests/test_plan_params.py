"""Planner block P0..P9 from the vendor settings (A6 §1.2-§1.8, §4.1; 05 §7.1)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from nexcut.io.params import default_document, read_params
from nexcut.plan.params import (
    CARD_RATE_CEILING,
    JumpAddTime,
    PlannerParams,
    document_value,
    interp_extras,
    laser_from_manu,
    parse_jump_add_time,
    planner_params,
    planner_params_from_values,
    rapid_params,
    rapid_params_from_values,
    read_jump_add_time,
    velocity_ceiling,
)

G = {
    "MC.ManuAcc": 6000.0,
    "MC.AccTime": 200.0,
    "MC.SplineAccuracyRate": 0.02,
    "MC.CornerAccuracyRate": 0.05,
    "MC.ForwardBackwardSpeed": 10.0,
    "MC.XFastMoveSpeed": 500.0,
    "MC.XFastMoveAcc": 6000.0,
    "MC.EmptyMoveAccTime": 125.0,
    "FCP.MaxSpeed": 3000,
    "AX.InterpolationCycle": 250,
    "MP.EmptyMoveSpeedFactor": 1.1,
    "MP.EmptyMoveAccFactor": 1.5,
}
LAYER = {
    "CutSpeed": 250.0,
    "UD_UpEnable": 1,
    "UD_UpLen": 5.0,
    "UD_UpSpeed": 8.0,
    "UD_DownEnable": 1,
    "UD_DownLen": 3.0,
    "UD_DownSpeed": 100.0,
    "SlowStart": 1,
    "SlowStartLength": 1.0,
    "SlowStartSpeed": 10.0,
}


def test_cut_flow_mapping() -> None:
    p = planner_params_from_values(G, LAYER)
    assert p.block() == (6000.0, 0.2, 0.02, 250.0, 0.0, 0.25, 5.0, 8.0, 3.0, 100.0)
    assert p.corner_precision == 0.05 and p.start_index == 0
    assert p.acc_time == pytest.approx(200 * 0.001)  # MC.AccTime * 0.001 in MainApp (A6 §1.7)


def test_vmax_is_min_of_cut_speed_max_speed_and_card_ceiling() -> None:
    assert CARD_RATE_CEILING == 750000.0
    assert velocity_ceiling(1000) == 750.0
    assert planner_params_from_values(G, {**LAYER, "CutSpeed": 900.0}).vmax == 750.0
    assert planner_params_from_values(G, {**LAYER, "CutSpeed": 900.0}, k=500).vmax == 900.0
    assert (
        planner_params_from_values({**G, "FCP.MaxSpeed": 300}, {**LAYER, "CutSpeed": 900.0}).vmax
        == 300.0
    )
    with pytest.raises(ValueError):
        velocity_ceiling(0)


def test_up_down_segment_conditions_are_strict() -> None:
    p = planner_params_from_values(G, {**LAYER, "CutSpeed": 100.0, "UD_UpSpeed": 100.0})
    assert p.slow_start_len == 0.0 and p.slow_start_speed == 100.0  # P3 > P7 is strict
    assert p.slow_end_len == 0.0  # P3 = 100 not > P9 = 100
    p = planner_params_from_values(
        G, {**LAYER, "CutSpeed": 100.0, "UD_UpSpeed": 99.9, "UD_DownSpeed": 99.0}
    )
    assert p.slow_start_len == 5.0 and p.slow_end_len == 3.0
    p = planner_params_from_values(G, {**LAYER, "UD_UpEnable": 0, "UD_DownEnable": 0})
    assert p.slow_start_len == 0.0 and p.slow_end_len == 0.0
    # CO2 tables carry no UD_* descriptors: treated as disabled
    co2 = {k: v for k, v in LAYER.items() if not k.startswith("UD_")}
    p = planner_params_from_values(G, co2)
    assert p.slow_start_len == 0.0 and p.slow_end_len == 0.0


def test_forward_backward_run_mode() -> None:
    assert planner_params_from_values(G, LAYER, run_mode=2).vmax == 10.0
    assert planner_params_from_values(G, LAYER, run_mode=1).vmax == 250.0


def test_simulate_flow() -> None:
    p = planner_params_from_values({**G, "FCP.MaxSpeed": 100}, LAYER, flow="simulate")
    assert p.vmax == 250.0  # no MaxSpeed clamp in 0x43a5c0
    assert (p.slow_start_len, p.slow_start_speed) == (1.0, 10.0)  # GP.SlowStart*
    assert (p.slow_end_len, p.slow_end_speed) == (0.0, 200.0)
    p = planner_params_from_values(G, {**LAYER, "SlowStart": 0}, flow="simulate")
    assert p.slow_start_len == 0.0


def test_block_round_trip_and_defaults() -> None:
    p = planner_params_from_values(G, LAYER)
    assert PlannerParams.from_block(p.block(), 0.05) == p
    assert PlannerParams.motionctrl_defaults().block() == (
        2000.0,
        0.125,
        0.05,
        200.0,
        0.0,
        1.0,
        0.0,
        10.0,
        0.0,
        200.0,
    )
    assert PlannerParams.cadmodule_defaults().block() == (
        2000.0,
        0.125,
        0.05,
        200.0,
        0.0,
        1.0,
        0.0,
        200.0,
        0.0,
        200.0,
    )
    m = PlannerParams.mainapp_defaults()
    assert m.block() == (80000.0, 0.125, 0.05, 999.0, 0.0, 0.25, 0.0, 200.0, 0.0, 200.0)
    assert m.corner_precision == 0.1
    with pytest.raises(ValueError):
        PlannerParams.from_block([1.0, 2.0])
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.vmax = 1.0  # type: ignore[misc]


def test_jerk_and_acc_time_clamp() -> None:
    p = planner_params_from_values(G, LAYER)
    assert p.jerk == pytest.approx(4 * 250 / 0.2**2)  # A*Ta/2 = 600 >= Vmax 250 -> 4 Vmax/Ta^2
    assert p.mainapp_jerk_field == pytest.approx(60000.0)
    fast = dataclasses.replace(p, vmax=750.0)
    assert fast.jerk == pytest.approx(2 * 6000 / 0.2)
    assert dataclasses.replace(p, acc_time=0.01).effective_acc_time == 0.06
    assert dataclasses.replace(p, acc_time=0.4).effective_acc_time == 0.25


def test_rapid_block() -> None:
    r = rapid_params_from_values(G)
    assert r.vmax == pytest.approx(550.0)
    assert r.acc == pytest.approx(9000.0)
    assert r.acc_time == pytest.approx(0.125)
    assert r.interp_period == pytest.approx(0.25)
    assert r.jerk_field == pytest.approx(2 * 9000 / 0.125)
    assert rapid_params_from_values({**G, "MC.XFastMoveSpeed": 1000.0}).vmax == 750.0


def test_default_documents_without_package() -> None:
    manu, hard, layer = (
        default_document("manu"),
        default_document("hard"),
        default_document("layer"),
    )
    p = planner_params(manu, hard, layer, 0, laser="fiber")
    assert p.acc == document_value(manu, "MC.ManuAcc")
    assert p.acc_time == pytest.approx(float(document_value(manu, "MC.AccTime")) * 0.001)
    assert p.interp_period == pytest.approx(
        float(document_value(hard, "AX.InterpolationCycle")) * 0.001
    )
    with pytest.raises(KeyError):
        document_value(manu, "MC.NoSuchThing")
    # layer index clamp [0, 10]
    assert planner_params(manu, hard, layer, 99, laser="fiber") == planner_params(
        manu, hard, layer, 10, laser="fiber"
    )


def test_vendor_settings(src_dir: Path) -> None:
    """A6 §1.6 'this machine' column from ``File/Bk*Para.xml``."""
    manu = read_params(src_dir / "File" / "BkManuPara.xml", "manu")
    hard = read_params(src_dir / "File" / "BkHardPara.xml", "hard")
    layer = read_params(src_dir / "File" / "BkLayerPara.xml", "layer")
    assert laser_from_manu(manu) == "co2"  # SP.m_iEnableLaserType="1"
    p = planner_params(manu, hard, layer, 1, laser="fiber")
    assert p.acc == pytest.approx(6000.0)
    assert p.acc_time == pytest.approx(0.2)
    assert p.precision == 0.02
    assert p.vmax == 250.0  # fibre layer 1: CutSpeed 250
    assert p.min_speed == 0.0
    assert p.interp_period == pytest.approx(0.25)
    assert (p.slow_start_len, p.slow_end_len) == (0.0, 0.0)
    assert p.corner_precision == 0.05
    co2 = [planner_params(manu, hard, layer, i).vmax for i in (0, 1, 10)]
    assert co2 == [600.0, 50.0, 10.0]
    assert planner_params(manu, hard, layer, 0, flow="simulate").slow_start_len == 0.0
    r = rapid_params(manu, hard)
    assert (r.vmax, r.acc_time) == (pytest.approx(550.0), pytest.approx(0.125))
    x = interp_extras(manu, hard)
    assert x.small_circle_ratio == 0.6 and not x.small_circle_limit
    assert x.flycut_line_open_forward_cycle == -3.0 and x.flycut_line_close_forward_cycle == -3.0
    assert x.micro_link_slow_speed == 10.0


def test_jump_add_time_parser_rules(tmp_path: Path) -> None:
    text = "[jump]\r\naddtime = 150\r\n[Arc2SegVelK]\r\nK_X=50abc\r\nK_Y=\r\n; c\r\n"
    j = parse_jump_add_time(text)
    assert j.add_time_ms == 150
    assert j.k_x == pytest.approx(0.5)
    assert (
        j.k_y == 1.0
    )  # empty value -> default (Wine GetPrivateProfileIntW; UNVERIFIED on Windows)
    assert parse_jump_add_time("[Jump]\nAddTime=abc").add_time_ms == 0  # non-numeric -> 0
    assert read_jump_add_time(tmp_path / "missing.txt") == JumpAddTime()
    assert parse_jump_add_time(b"[Arc2SegVelK]\nK_X=100") == JumpAddTime()
