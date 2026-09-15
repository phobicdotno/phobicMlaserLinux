"""Laser toggles, compensation tables and power/frequency curves (05 §5.2, A6 §2.5, 02 §3.4)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from nexcut.plan.pwm_schedule import (
    CompensationTable,
    CurveNodes,
    LayerLaser,
    close_pwm_ratios,
    format_ratio_dump,
    laser_mask,
    pwm_pair_ratios,
    read_compensation_file,
    toggle_positions,
)

# reference tables (parent of SRC; A7 §8) and the XML strings they are compared with
PWM_TXT = [(100, 0.364), (200, 0.56), (300, 0.8), (400, 1.02), (500, 1.3), (600, 1.52)]
CO2_TXT = [(100, 0.18), (200, 0.27), (300, 0.48), (400, 0.58), (500, 0.74), (600, 0.86)]
BACKUP_FIBRE = "100#0.34,200#0.56,300#0.8,400#1.02,500#1.3,600#1.52"  # 1390backup.xml
BACKUP_CO2 = "100#0.18,200#0.27,300#0.48,400#0.58,500#0.74,600#0.86"
LIVE_CO2 = "100#0.22,200#0.3,300#0.57,400#0.71,500#0.85,600#0.94"  # BkManuPara.xml
LIVE_FIBRE = "100#0.35,200#0.59,300#0.82,400#1.02,500#1.26,600#1.48"


def test_toggle_positions_of_a_small_path() -> None:
    lengths = [25, 3, 25, 60, 2.5, 60, 25]
    on = [True, False, True, False, False, False, True]
    assert toggle_positions(lengths, on) == [25, 28, 53, 25 + 3 + 25 + 60 + 2.5 + 60]
    assert close_pwm_ratios(lengths, on)[0] == pytest.approx(25 / sum(lengths))
    assert toggle_positions([5.0], [True]) == []
    assert close_pwm_ratios([], []) == []
    with pytest.raises(ValueError):
        toggle_positions([1.0], [])
    assert format_ratio_dump([0.0165935, 0.135177]) == b"0.0165935\r\n0.135177\r\n"


def test_pwm_pair_ratios() -> None:
    assert pwm_pair_ratios([0.5, 0.25], [10.0, 4.0], 100.0) == pytest.approx(
        [0.45, 0.55, 0.23, 0.27]
    )
    with pytest.raises(ValueError):
        pwm_pair_ratios([0.5], [1.0], 0.0)


def test_compensation_tables_parse_and_interpolate() -> None:
    txt = CompensationTable.parse("".join(f"{a},{b}\n" for a, b in PWM_TXT))
    assert txt.speeds == (100, 200, 300, 400, 500, 600) and txt.leads[0] == 0.364
    co2_txt = CompensationTable.parse("\r\n".join(f"{a},{b}" for a, b in CO2_TXT))
    assert co2_txt == CompensationTable.parse(BACKUP_CO2)  # 6/6 identical (A7 §8)
    fibre = CompensationTable.parse(BACKUP_FIBRE)
    assert sum(a == b for a, b in zip(txt.leads, fibre.leads, strict=True)) == 5  # 100 differs
    live = CompensationTable.parse(LIVE_CO2)
    assert live.format_xml() == LIVE_CO2
    assert float(live.lead(250.0)) == pytest.approx(0.435)
    assert float(live.lead(50.0)) == pytest.approx(0.22)  # clamped (UNVERIFIED rule)
    assert float(live.lead(900.0)) == pytest.approx(0.94)
    assert CompensationTable.parse(LIVE_FIBRE).leads[-1] == 1.48
    assert float(CompensationTable((), ()).lead(10.0)) == 0.0
    with pytest.raises(ValueError):
        CompensationTable.parse("100;0.2\n")


def test_reference_files(src_dir: Path) -> None:
    parent = src_dir.parent
    txt = read_compensation_file(parent / "pwmCompensation.txt")
    co2 = read_compensation_file(parent / "Co2_pwmCompensation.txt")
    assert list(zip(txt.speeds, txt.leads, strict=True)) == PWM_TXT
    assert list(zip(co2.speeds, co2.leads, strict=True)) == CO2_TXT
    backup = (parent / "1390backup.xml").read_text(encoding="utf-8", errors="replace")
    assert BACKUP_CO2 in backup and BACKUP_FIBRE in backup
    manu = (src_dir / "File" / "BkManuPara.xml").read_text(encoding="utf-8", errors="replace")
    assert LIVE_CO2 in manu and LIVE_FIBRE in manu


def test_laser_mask_toggles_offsets_and_compensation() -> None:
    s = np.arange(0.0, 11.0, 1.0)  # 10 intervals of 1 mm
    v = np.full_like(s, 100.0)
    mask = laser_mask(s, v, [3.0, 7.0])
    assert mask.tolist() == [True] * 3 + [False] * 4 + [True] * 3  # interval ending at 3 keeps on
    early = laser_mask(s, v, [3.0, 7.0], open_forward_cycles=-1, close_forward_cycles=-2)
    assert early.tolist() == [True] + [False] * 5 + [True] * 4
    comp = CompensationTable((100.0,), (1.0,))
    assert laser_mask(s, v, [3.0], compensation=comp).tolist() == [True] * 2 + [False] * 8
    assert laser_mask(s, v, [], start_on=False).tolist() == [False] * 10
    assert laser_mask([0.0], [0.0], [1.0]).size == 0


def test_curve_nodes_and_layer_pwm() -> None:
    c = CurveNodes.parse("0,48,22,66,50,87,100,100")
    assert c.x == (0, 22, 50, 100) and c.y == (48, 66, 87, 100)
    # 50 % duty base at CutSpeed 100: v = 22 -> 66 % of 50 = 33; v = 36 -> 76.5 % -> 38.25 -> 38
    out = c.evaluate([22.0, 36.0, 0.0, 100.0, 150.0], 100.0, 50)
    assert out.tolist() == [33, 38, 24, 50, 50]
    assert CurveNodes.parse("0,0,100,100").evaluate([12.5], 100.0, 4).tolist() == [1]  # 0.5 -> 1
    assert CurveNodes.parse("").y == (100.0, 100.0)
    with pytest.raises(ValueError):
        CurveNodes.parse("0,1,2")
    layer2 = {
        "PowerAdjustWithSpeed": 0,
        "FreqAdjustWithSpeed": 0,
        "CutSpeed": 50.0,
        "LaserOnDelay": 0,
        "CutDuty": 4,
        "CutGasType": 3,
        "PWMCurveNodes": "0,100,100,100",
        "FreqCurveNodes": "0,100,100,100",
        "CutFreq": 5000,
    }  # fmt: skip  (BkLayerPara.xml PCO2LayerParam2 = the leaked 5000 Hz / 4 % frames)
    ll = LayerLaser.from_layer(layer2)
    assert (ll.freq, ll.duty, ll.gas_type, ll.cut_speed) == (5000, 4, 3, 50.0)
    freq, duty = ll.tick_pwm([10.0, 50.0, 50.0], [True, True, False])
    assert freq.tolist() == [5000] * 3 and duty.tolist() == [4, 4, 0]
    dyn = LayerLaser.from_layer({**layer2, "PowerAdjustWithSpeed": 1, "CutDuty": 20,
                                 "PWMCurveNodes": "0,48,22,66,50,87,100,100"})  # fmt: skip
    assert dyn.tick_pwm([0.0, 50.0])[1].tolist() == [10, 20]  # 48 % of 20 = 9.6 -> 10
    fibre = LayerLaser.from_layer({"CutSpeed": 11.0, "CutPower": 88, "CutFreq": 2000})
    assert fibre.duty == 88 and fibre.freq == 2000
