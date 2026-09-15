"""Item encoding, record translation and the tick quantiser (11 §5.2-§5.4, A3 §2-§6)."""

from __future__ import annotations

import numpy as np
import pytest

from nexcut.mcc.dissector import parse_fifo_words
from nexcut.mcc.safety import strip_laser_records
from nexcut.plan.items import (
    FREQ_DEFAULT,
    AxisScale,
    ContourLaser,
    IoSub,
    Item,
    ItemBuilder,
    JobStreamBuilder,
    Record,
    RecordType,
    StreamConfig,
    TickQuantizer,
    UnsupportedConfiguration,
    do_item,
    dwell_tick_count,
    encode_item,
    gas_do_port,
    tick_item,
    total_ticks,
    wait_2001_word,
)


def words(items: list[Item]) -> list[int]:
    return [w for it in items for w in it.words()]


# ------------------------------------------------------------------------------ encoders
def test_tick_packing_x_low_y_high() -> None:
    assert tick_item(0, 0, 5000, 4).words() == [0x00080BB8, 0x00000000, 0x13880004]
    assert tick_item(-1, 0, 5000, 4).words()[1] == 0x0000FFFF  # frame 633
    assert tick_item(-1, 1, 5000, 4).words()[1] == 0x0001FFFF  # frame 523
    assert tick_item(7, 3, 5000, 0).words()[1] == 0x00030007  # frame 739
    assert tick_item(0, 0, 0, 0).words()[2] == (FREQ_DEFAULT << 16)  # freq < 1 -> 5000
    assert tick_item(1, 0, 2000, 100).words()[2] == 0x07D00064  # frame 1931
    assert tick_item(0, 0, 5000, 4).laser and not tick_item(0, 0, 5000, 0).laser
    with pytest.raises(ValueError):
        tick_item(32768, 0, 5000, 0)  # the vendor would silently corrupt it (A3 V)
    assert tick_item(-32768, 32767, 1, 0).words()[1] == 0x7FFF8000


def test_do_records() -> None:
    assert do_item(3, True).words() == [0x000C270F, 2, 4, 4]  # HighAir DO3
    assert do_item(9, True).words() == [0x000C270F, 2, 0x100, 0x100]  # CO2 laser DO9
    assert do_item(9, False).words() == [0x000C270F, 2, 0x100, 0]
    assert do_item(9, True).laser and not do_item(9, False).laser and not do_item(3, True).laser
    assert do_item(1, False).words() == [0x000C270F, 2, 1, 0]
    assert do_item(11, True).args == (13, 1, 1)  # extended outputs, sub 13
    assert do_item(26, True).args == (13, 1 << 15, 1 << 15)
    with pytest.raises(ValueError):
        do_item(0, True)
    with pytest.raises(ValueError):
        do_item(27, True)


def test_2001_modes_and_generic_encoder() -> None:
    assert wait_2001_word(3, r=2) == 0x03000002
    assert wait_2001_word(0, p=150) == 150
    assert wait_2001_word(1, q=0x12, r=0x34) == 0x01001234
    assert wait_2001_word(2, q=0x12, r=0x34) == 0x02001234
    with pytest.raises(ValueError):
        wait_2001_word(4)
    assert encode_item(2001, [0x03000002, 20000]) == [0x000807D1, 0x03000002, 0x4E20]
    assert encode_item(3001) == [0xBB9]


def test_dwell_tick_count_integer_division_first() -> None:
    assert dwell_tick_count(1000) == 4000  # 1 s pierce = 4000 ticks at 250 µs (A3 §3)
    assert dwell_tick_count(100) == 400
    assert dwell_tick_count(10, 300) == 30  # 1000 idiv 300 = 3 (A3 V)
    assert dwell_tick_count(-5) == 0
    with pytest.raises(ValueError):
        dwell_tick_count(10, 0)


# ------------------------------------------------------------------------------ item builder
def test_auto_boundary_rule() -> None:
    b = ItemBuilder()
    b.new_batch()
    assert words(b.build(Record.do(3, True))) == [0x000C270F, 2, 4, 4]  # first record: no 3001
    b.build(Record.tick(1, 0))
    assert [i.opcode for i in b.build(Record.do(3, True))] == [3001, 9999]  # after a tick
    b.build(Record.tick(1, 0))
    assert [i.opcode for i in b.build(Record(RecordType.BOUNDARY))] == [3001]  # 0xe excluded
    assert [i.opcode for i in b.build(Record.mode(5))] == [3002]
    b.build(Record.tick(0, 0))
    assert [i.opcode for i in b.build(Record(RecordType.SKIP_C))] == [3001]  # no-op types too
    b.build(Record.tick(0, 0))
    b.new_batch()  # VM+0x1174 = -1 on every push
    assert [i.opcode for i in b.build(Record.mode(4))] == [3002]


def test_zf_records_this_machine() -> None:
    b = ItemBuilder(StreamConfig())
    b.new_batch()
    assert words(b.build(Record(RecordType.ZF_CUT_HEIGHT))) == [
        0x00080067, 1000, 0, 0x000807D1, 0x03000002, 20000,
    ]  # fmt: skip
    assert words(b.build(Record(RecordType.ZF_LIFT))) == [
        0x0008006D, 1000, 0, 0x000807D1, 0x03000002, 20000, 0x000C0076, 4, 0, 35,
    ]  # fmt: skip
    dock = b.build(Record(RecordType.ZF_DOCK))
    assert dock[0].opcode == 109 and dock[0].args == (1000, -20000)
    assert dock[0].words()[2] == (-20000) & 0xFFFFFFFF  # ZFUpSpeed x10, ZFDockHeight x -1000
    assert words(b.build(Record(RecordType.ZF_WAIT))) == [0x000807D1, 0x03000002, 20000]
    assert words(b.build(Record(RecordType.WAIT_CONDITION, 3, value=7))) == [
        0x000807D1, 0x03000007, 20000,
    ]  # fmt: skip


def test_zf_off_emits_nothing_and_book_flag_polarity() -> None:
    off = ItemBuilder(StreamConfig(zf_type=0))
    for t in (RecordType.ZF_CUT_HEIGHT, RecordType.ZF_LIFT, RecordType.ZF_DOCK, RecordType.ZF_WAIT):
        assert off.build(Record(t)) == []
    co2 = ItemBuilder(StreamConfig(zf_book_flag_arg=7))
    assert co2.build(Record(RecordType.ZF_LIFT))[2].args == (4, 0, 35)  # CO2: flag dropped
    fibre = ItemBuilder(StreamConfig(laser_type=0, zf_book_flag_arg=7))
    assert fibre.build(Record(RecordType.ZF_LIFT))[2].args == (4, 7, 35)
    assert fibre.build(Record(RecordType.IO, IoSub.ZF_BOOK, value=3))[0].args == (4, 3, 35)


def test_io_records() -> None:
    b = ItemBuilder()
    pwm = b.build(Record(RecordType.IO, IoSub.PWM_1, duty=4, freq=5000))
    assert pwm[0].args == (0x11, 5000, 4) and pwm[0].laser  # CO2 control type 2 -> 0x11
    fib = ItemBuilder(StreamConfig(laser_type=0))
    assert fib.build(Record(RecordType.IO, IoSub.PWM_2, duty=0, freq=1234))[0].args == (3, 1234, 0)
    da = b.build(Record(RecordType.IO, IoSub.DA, 1, value=2500))
    assert da[0].args == (4, 1, 2500) and da[0].laser
    assert b.build(Record(RecordType.IO, IoSub.WAIT_MS, value=0)) == []
    assert b.build(Record(RecordType.IO, IoSub.WAIT_MS, value=150))[0].args == (150, 3000)
    with pytest.raises(ValueError):
        b.build(Record(RecordType.IO, IoSub.DO, 2, value=2))
    with pytest.raises(UnsupportedConfiguration):
        b.build(Record(RecordType.IO, 9))


def test_unsupported_records_and_configurations() -> None:
    b = ItemBuilder()
    for t in (4, 5, 7, 0xA, 0xB, 0x12, 0x20):
        with pytest.raises(UnsupportedConfiguration):
            b.build(Record(t))
    with pytest.raises(UnsupportedConfiguration):
        b.build(Record(RecordType.ZF_LIFT, value=0x10))  # 106 drill branch
    with pytest.raises(UnsupportedConfiguration):
        StreamConfig(laser_type=1, co2_control_type=0)  # malformed 3000 in the vendor
    with pytest.raises(UnsupportedConfiguration):
        StreamConfig(laser_type=2, co2_control_type=3)
    with pytest.raises(UnsupportedConfiguration):
        StreamConfig(laser_type=1, co2_control_type=3)  # analogue word not implemented
    StreamConfig(laser_type=0, co2_control_type=0)  # fibre: plain PWM word
    with pytest.raises(UnsupportedConfiguration):
        ItemBuilder(StreamConfig(sub_device_type=1)).build(Record(RecordType.ZF_CUT_HEIGHT))


# ------------------------------------------------------------------------------ quantiser
def test_axis_scale_sources() -> None:
    s = AxisScale()
    assert s.x_per_mm == pytest.approx(258.0395, abs=1e-4)
    assert s.y_per_mm == pytest.approx(257.9896, abs=1e-4)
    g = {"MAC.SpeedRatio": 31.02, "MAC.WritePluse": 8000, "MAC_1.SpeedRatio": 30.991,
         "MAC_1.WritePluse": 8000}  # fmt: skip
    wine = AxisScale.from_values(g)  # type: ignore[arg-type]
    assert (wine.x_lead, wine.y_lead) == (31.02, 30.991)
    card = AxisScale.from_values(g, card_axis0=(31003, 8000))  # type: ignore[arg-type]
    assert card.x_lead == 31.003 and card.y_lead == 30.991  # Y never from the card (A3 V1)


def test_truncation_with_carry() -> None:
    q = TickQuantizer(AxisScale(5, 4.0, 5, 4.0))  # 1.25 pulses per mm step
    pts = np.array([[i, -i] for i in range(5)], dtype=float)
    dx, dy = q.quantize(pts)
    assert dx == [1, 1, 1, 2] and dy == [-1, -1, -1, -2]  # trunc toward zero, carry kept
    assert q.carry == [0.0, 0.0]
    dx2, dy2 = q.quantize(np.array([[0.0, 0.0], [0.5, -0.5], [1.0, -1.0]]))
    assert dx2 == [0, 1] and dy2 == [0, -1]  # 0.625 carried into the next tick / next call
    assert q.carry[0] == pytest.approx(0.25) and q.carry[1] == pytest.approx(-0.25)
    q.reset()
    assert q.carry == [0.0, 0.0]
    assert q.quantize(np.zeros((1, 2))) == ([], [])


def test_quantiser_conserves_distance() -> None:
    q = TickQuantizer()
    t = np.linspace(0.0, 1.0, 4001)
    pts = np.column_stack((100.0 * t, -50.0 * t**2))
    dx, dy = q.quantize(pts)
    assert abs(sum(dx) - 100.0 * q.scale.x_per_mm) < 1.0
    assert abs(sum(dy) + 50.0 * q.scale.y_per_mm) < 1.0
    assert max(abs(v) for v in dx) <= 7


# ------------------------------------------------------------------------------ job builder
def _contour_builder(laser_records: bool) -> JobStreamBuilder:
    b = JobStreamBuilder(laser_records=laser_records)
    xs = np.linspace(0.0, 1.0, 201)
    b.add_motion(np.column_stack((xs, np.zeros_like(xs))))  # rapid
    b.add_contour(np.column_stack((1.0 + xs, xs)), 5000, 4, ContourLaser(3, 9, 5.0))
    b.finish()
    return b


def test_dry_run_builder_has_no_laser_content() -> None:
    b = _contour_builder(False)
    frames = b.frames()
    assert all(f.laser_items == 0 for f in frames)
    for i, f in enumerate(frames):
        out, changed = strip_laser_records(f.payload(i + 1))
        assert changed == 0 and out == f.payload(i + 1)
    ops = [it.opcode for grp in b.items() for it in grp]
    assert ops.count(9999) == 3  # gas on, laser off (safe), gas off at job end
    assert total_ticks(frames) == 200 + 1 + 20 + 200


def test_laser_records_are_flagged_and_stripped_by_safety() -> None:
    b = _contour_builder(True)
    frames = b.frames()
    assert sum(f.laser_items for f in frames) == 1 + 20 + 200  # DO9 on + dwell + cut ticks
    changed_total = 0
    for i, f in enumerate(frames):
        out, changed = strip_laser_records(f.payload(i + 1))
        changed_total += changed
        for item in parse_fifo_words(out).items:
            if item.opcode == 3000:
                assert item.args[1] & 0xFFFF == 0
            if item.opcode == 9999:
                assert item.args[2] & 0x100 == 0
    assert changed_total == 1 + 20 + 200


def test_gas_port_map_and_unassigned_laser_port() -> None:
    hv = {"MGP.LowAir": 0, "MGP.LowO2": 7, "MGP.LowN2": 0, "MGP.HighAir": 3, "MGP.HighO2": 0,
          "MGP.HighN2": 2}  # fmt: skip
    assert gas_do_port(3, hv) == 3 and gas_do_port(1, hv) == 7 and gas_do_port(5, hv) == 2  # type: ignore[arg-type]
    assert gas_do_port(0, hv) == 0 and gas_do_port(9, hv) == 0  # type: ignore[arg-type]
    b = JobStreamBuilder(laser_records=True)
    b.add_contour(np.array([[0.0, 0.0], [0.01, 0.0]]), 5000, 4, ContourLaser(0, 0, 0.0))
    assert not any(r.type == RecordType.IO for r in b.records)
