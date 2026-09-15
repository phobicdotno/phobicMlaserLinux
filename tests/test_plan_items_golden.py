"""Golden tests against the 22 leaked FIFO frames (tests/data/mcc/fifo_frames_2025-07.txt).

What is reproduced (11 §5.4, A3 §8):

* every frame re-encodes word for word from its decoded items through :class:`Item` and the
  300-word packer (grammar and flush rule);
* the CO2 contour **prologue** of frames 504 and 57 and the **epilogue** of frame 633 come out of
  :class:`JobStreamBuilder` records exactly (ticks around them are taken from the frame: the
  vendor's rapid/decel tails depend on planner details that are not bit-exact);
* the constant-velocity run of frame 1931 (2025-07-13) is reproduced tick for tick from a planned
  straight line sampled at 250 µs and quantised with truncate-with-carry.  The speed/direction
  (19.93 mm/s, -0.79°) and the two initial carries are *fitted* to the frame - the job that
  produced it is not recorded (UNVERIFIED); the fit only proves the quantiser model.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from nexcut.mcc.dissector import FifoItem, parse_fifo_words
from nexcut.mcc.fifo import FramePacker, format_frame_line, parse_frame_line
from nexcut.mcc.safety import strip_laser_records
from nexcut.plan.items import (
    AxisScale,
    ContourLaser,
    Item,
    ItemBuilder,
    JobStreamBuilder,
    Record,
    TickQuantizer,
)
from nexcut.plan.params import PlannerParams
from nexcut.plan.sampler import plan_line, sample_plan

LEAKED = Path(__file__).parent / "data" / "mcc" / "fifo_frames_2025-07.txt"


def leaked() -> dict[int, tuple[str, list[FifoItem]]]:
    out: dict[int, tuple[str, list[FifoItem]]] = {}
    for line in LEAKED.read_text(encoding="utf-8").splitlines():
        parsed = parse_frame_line(line)
        if parsed is None:
            continue
        fid, data = parsed
        out[fid] = (line.split("DataEx:")[1].strip(), list(parse_fifo_words([fid, *data]).items))
    return out


FRAMES = leaked()


def tick_records(items: list[FifoItem]) -> list[Record]:
    recs = []
    for it in items:
        t = it.tick
        assert t is not None
        # rapid ticks carry freq 0 in the record; the packer turns it into 5000 (A3 §4.2)
        recs.append(Record.tick(t[0], t[1], 0 if t[3] == 0 else t[2], t[3]))
    return recs


def pack(builder: JobStreamBuilder) -> list[list[int]]:
    packer = FramePacker()
    for group in builder.items():
        packer.add_record([it.words() for it in group])
    packer.end_batch()
    return [list(f.data) for f in packer.frames]


def test_census_matches_a3() -> None:
    assert len(FRAMES) == 22
    census: dict[int, int] = {}
    for _, items in FRAMES.values():
        for it in items:
            census[it.opcode] = census.get(it.opcode, 0) + 1
    assert census == {3000: 2160, 3001: 6, 9999: 5, 3002: 3, 2001: 3, 103: 2, 109: 1, 118: 1}


def test_all_frames_reencode_through_item() -> None:
    for fid, (text, items) in FRAMES.items():
        packer = FramePacker()
        for it in items:
            packer.add_record([Item(it.opcode, it.args).words()])
        packer.end_batch()
        assert len(packer.frames) == 1
        assert format_frame_line(fid, packer.frames[0]) == text


@pytest.mark.parametrize("fid", [504, 57])
def test_prologue_frames_504_and_57(fid: int) -> None:
    text, items = FRAMES[fid]
    first_ctrl = next(i for i, it in enumerate(items) if it.opcode != 3000)
    b = JobStreamBuilder(laser_records=True)
    b.records.extend(tick_records(items[:first_ctrl]))
    # CO2 layer 2: 5000 Hz / 4 %, HighAir DO3, CO2DOLaser DO9; dwell long enough to fill the frame
    b.prologue(ContourLaser(gas_port=3, laser_port=9, pierce_dwell_ms=100.0), 5000, 4)
    data = pack(b)[0]
    expected = [w for it in items for w in Item(it.opcode, it.args).words()]
    assert data[: len(expected)] == expected
    assert b.frames()[0].count == 0x12A


def test_epilogue_frame_633_whole_frame() -> None:
    text, items = FRAMES[633]
    first_ctrl = next(i for i, it in enumerate(items) if it.opcode != 3000)
    last_ctrl = max(i for i, it in enumerate(items) if it.opcode != 3000)
    b = JobStreamBuilder(laser_records=True)
    b.records.extend(tick_records(items[:first_ctrl]))
    b.epilogue(ContourLaser(gas_port=3, laser_port=9))
    b.records.extend(tick_records(items[last_ctrl + 1 :]))
    frames = b.frames()
    assert len(frames) == 1
    assert format_frame_line(633, frames[0]) == text  # byte-identical frame 0x279


def test_epilogue_items_decoded() -> None:
    b = ItemBuilder()
    b.new_batch()
    b.build(Record.tick(0, 0, 5000, 4))
    jb = JobStreamBuilder()
    jb.epilogue(ContourLaser())
    got = [it for r in jb.records for it in b.build(r)]
    assert [(it.opcode, it.args) for it in got] == [
        (3001, ()),
        (9999, (2, 0x100, 0)),
        (109, (1000, 0)),
        (2001, (0x03000002, 20000)),
        (118, (4, 0, 35)),
        (3001, ()),
        (3002, (4,)),
    ]


def test_leaked_laser_frames_are_neutralised_by_safety() -> None:
    _, items = FRAMES[504]
    words = [504] + [w for it in items for w in Item(it.opcode, it.args).words()]
    out, changed = strip_laser_records(words)
    frame = parse_fifo_words(out)
    assert changed == 13 + 1  # 13 ticks at 4 % and the DO9-on record
    assert all(i.tick is None or i.tick[3] == 0 for i in frame.items)
    assert not any(i.opcode == 9999 and i.args[2] & 0x100 for i in frame.items)
    assert any(i.opcode == 9999 and i.args == (2, 4, 4) for i in frame.items)  # gas stays


def _carry_interval(inc: np.ndarray, out: list[int], positive: bool) -> tuple[float, float]:
    r = np.cumsum(inc)
    o = np.cumsum(np.asarray(out, dtype=np.float64))
    if positive:  # O_k = floor(R_k + c0), c0 in [0, 1)
        return float(max(0.0, np.max(o - r))), float(min(1.0, np.min(o + 1.0 - r)))
    return float(max(-1.0, np.max(o - 1.0 - r))), float(min(0.0, np.min(o - r)))  # ceil


def test_constant_velocity_run_frame_1931() -> None:
    _, items = FRAMES[0x78B]
    ticks = [it.tick for it in items]
    assert all(t is not None and t[2:] == (2000, 100) for t in ticks)
    dx = [t[0] for t in ticks]  # type: ignore[index]
    dy = [t[1] for t in ticks]  # type: ignore[index]
    scale = AxisScale()  # 8000/31.003 X, 8000/31.009 Y (BkHardPara.xml)
    # fitted increments (pulses/tick) inside the feasible set of the 99 ticks
    fx, fy = 9.0 / 7.0, -0.0178
    vx = fx / scale.x_per_mm / 0.00025
    vy = fy / scale.y_per_mm / 0.00025
    speed = math.hypot(vx, vy)
    assert speed == pytest.approx(19.93, abs=0.01)
    direction = np.array([vx, vy]) / speed
    plan, geom = plan_line(
        (0.0, 0.0), tuple(200.0 * direction), PlannerParams(6000, 0.2, 0.02, speed)
    )
    motion = sample_plan(plan, geom)
    window = motion.xy[20000:20100]  # cruise
    inc_x = np.diff(window[:, 0] * 10000.0) * scale.x_factor
    inc_y = np.diff(window[:, 1] * 10000.0) * scale.y_factor
    lo_x, hi_x = _carry_interval(inc_x, dx, True)
    lo_y, hi_y = _carry_interval(inc_y, dy, False)
    assert lo_x < hi_x and lo_y < hi_y  # a constant-velocity explanation exists
    q = TickQuantizer(scale, ((lo_x + hi_x) / 2.0, (lo_y + hi_y) / 2.0))
    qx, qy = q.quantize(window)
    assert qx == dx and qy == dy
    b = JobStreamBuilder(quantizer=TickQuantizer(scale, ((lo_x + hi_x) / 2, (lo_y + hi_y) / 2)))
    b.laser_records = True
    b.add_motion(window, 2000, 100)
    frame = b.frames()[0]
    assert format_frame_line(0x78B, frame) == FRAMES[0x78B][0]
