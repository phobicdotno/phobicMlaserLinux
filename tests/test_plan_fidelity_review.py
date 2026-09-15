"""Adversarial planner-fidelity review of ``plan/*``, ``mcc/fifo.py`` and the job stream.

Lens: 05-motion-pipeline, 11-static-findings §5, A3, A6.  Golden numbers are re-derived from the
22 leaked FIFO frames (``tests/data/mcc/fifo_frames_2025-07.txt``, copied verbatim from
``Log/2025-07-1[37].log``; the raw logs are re-checked when SRC is available) and the CF1390
XML values (``File/BkManuPara.xml``, ``BkHardPara.xml``, ``BkLayerPara.xml`` CO2 layer 2), which
are copied into this file.

Findings that are fixed are pinned by ordinary tests; open fidelity gaps are strict xfails whose
reason names the evidence.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np
import pytest

from nexcut.mcc.dissector import FifoItem, parse_fifo_words
from nexcut.mcc.fifo import FifoFeeder, FramePacker, PackedFrame, parse_frame_line
from nexcut.mcc.framing import MAX_FRAME_LEN, encode_vector
from nexcut.plan.contour_fit import PieceList, line_glyph, process
from nexcut.plan.items import (
    AxisScale,
    ContourLaser,
    IoSub,
    ItemBuilder,
    JobStreamBuilder,
    Record,
    RecordType,
    TickQuantizer,
    tick_item,
)
from nexcut.plan.lookahead import plan_velocity
from nexcut.plan.params import PlannerParams, RapidParams, velocity_ceiling
from nexcut.plan.pwm_schedule import LayerLaser
from nexcut.plan.sampler import MAX_JOINT_GAP, PathGeometry, plan_line, sample_plan
from nexcut.plan.scurve import jerk_from_acc_time

DT = 250e-6
LEAKED = Path(__file__).parent / "data" / "mcc" / "fifo_frames_2025-07.txt"

# CF1390 values (BkManuPara.xml MC / GC, BkHardPara.xml FCP / MAC, BkLayerPara.xml PCO2LayerParam2)
MANU_ACC = 5999.9999999999991  # MC.ManuAcc
ACC_TIME_MS = 200  # MC.AccTime
SPLINE_ACC = 0.02  # MC.SplineAccuracyRate
FCP_MAX_SPEED = 3000.0  # FCP.MaxSpeed
GAS_DELAY_MS = 100.0  # GC.GasDelay
LAYER2 = {  # the layer of the leaked CO2 job: 4 % duty, 5000 Hz, CutGasType 3 = HighAir = DO3
    "CutSpeed": 50,
    "CutDuty": 4,
    "CutFreq": 5000,
    "LaserOnDelay": 0,
    "CutGasType": 3,
}
LAYER2_PARAMS = PlannerParams(
    MANU_ACC,
    ACC_TIME_MS * 0.001,
    SPLINE_ACC,
    min(LAYER2["CutSpeed"], FCP_MAX_SPEED, velocity_ceiling(1000)),
)


def _frames() -> dict[int, list[FifoItem]]:
    out: dict[int, list[FifoItem]] = {}
    for line in LEAKED.read_text(encoding="utf-8").splitlines():
        parsed = parse_frame_line(line)
        if parsed is not None:
            fid, data = parsed
            out[fid] = list(parse_fifo_words([fid, *data]).items)
    return out


FRAMES = _frames()


def _ticks(items: list[FifoItem]) -> list[tuple[int, int, int, int]]:
    return [it.tick for it in items if it.tick is not None]


# ============================================================================ golden re-derivation
def test_raw_logs_decode_to_the_committed_frames(src_dir: Path) -> None:
    """The 22 committed frames are exactly the distinct ``40 66`` frames of the raw logs (A3 §8)."""
    raw: dict[int, list[int]] = {}
    for log in sorted((src_dir / "Log").glob("2025-07-1*.log")):
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"DataEx:\s*(40 66 .*)$", line)
            if m:
                parsed = parse_frame_line(m.group(1))
                assert parsed is not None
                raw.setdefault(parsed[0], parsed[1])
    assert len(raw) == 22
    for fid, items in FRAMES.items():
        assert list(parse_fifo_words([fid, *raw[fid]]).items) == items
        assert len(raw[fid]) == 297  # every leaked frame closed by the 300-word rule


def test_prologue_dwell_is_14_stationary_ticks_in_frames_0x39_0x3a() -> None:
    """Frame 0x3a directly follows 0x39: after ``DO9 on`` exactly 14 stationary 4 % ticks, then
    the cut moves.  (0x1f8 has the same prologue but its successor frame was not logged.)"""
    items = FRAMES[0x39] + FRAMES[0x3A]
    i_on = next(
        i for i, it in enumerate(items) if it.opcode == 9999 and tuple(it.args) == (2, 0x100, 0x100)
    )
    after = _ticks(items[i_on + 1 :])
    first_move = next(k for k, t in enumerate(after) if t[0] or t[1])
    assert first_move == 14
    assert all(t[2:] == (5000, 4) for t in after)


def _vendor_cut_start() -> np.ndarray:
    items = FRAMES[0x39] + FRAMES[0x3A]
    i_on = max(i for i, it in enumerate(items) if it.opcode != 3000)
    dx = [-t[0] for t in _ticks(items[i_on + 1 :])]
    return np.cumsum(dx)


def test_vendor_cut_start_kinematics_frame_0x3a() -> None:
    """Quadratic fit of the 99 moving-frame ticks: v0 ~ 6.9 mm/s, a ~ 570 mm/s² (residual below
    one pulse) - the cut does not leave the pierce point from rest (see the xfail below)."""
    c = _vendor_cut_start()[13:]  # cumulative pulses at the end of each tick of frame 0x3a
    assert len(c) == 99 and c[-1] == 90
    k = np.arange(1, 100) * DT
    ppm = AxisScale().x_per_mm
    basis = np.column_stack((np.ones_like(k), k, k * k))
    coef = np.linalg.lstsq(basis, c / ppm, rcond=None)[0]
    assert coef[1] == pytest.approx(6.9, abs=0.3)  # mm/s at the first moving tick
    assert 2.0 * coef[2] == pytest.approx(570.0, abs=60.0)  # mm/s²
    assert float(np.max(np.abs(basis @ coef * ppm - c))) < 1.0


def test_cut_end_tail_frame_0x279_matches_jerk_rule() -> None:
    """Frame 0x279: 38 stationary ticks, one pulse, 16 stationary ticks, then the epilogue.
    A jerk-limited stop has ``r(tau) = J tau³/6`` left; one crossing in the last 55 ticks needs
    ``r(55 dt) < 2`` pulses.  ``J = 4 Vmax/Ta²`` = 5000 (05 §7.1, layer 2: Vmax 50, Ta 0.2) fits,
    ``2A/Ta`` = 60000 would put ~7 pulses there."""
    ticks = _ticks(FRAMES[0x279])[:55]
    assert [k for k, t in enumerate(ticks) if t[0] or t[1]] == [38]
    ppm = AxisScale().x_per_mm
    j = jerk_from_acc_time(MANU_ACC, 0.2, 50.0)
    assert j == pytest.approx(5000.0)
    r55 = j * (55 * DT) ** 3 / 6.0 * ppm
    assert r55 < 2.0
    assert 2.0 * MANU_ACC / 0.2 * (55 * DT) ** 3 / 6.0 * ppm > 6.0
    # the port's stop: at most one moving tick in the last 55, whatever the persistent carry
    plan, geom = plan_line((100.0, 0.0), (0.0, 0.0), LAYER2_PARAMS)
    xy = sample_plan(plan, geom).xy
    for carry in (0.0, -0.3, -0.7, -0.999):
        dx, _ = TickQuantizer(AxisScale(), (carry, 0.0)).quantize(xy)
        assert sum(1 for d in dx[-55:] if d) <= 1


@pytest.mark.xfail(
    strict=True,
    reason=(
        "fidelity gap: __main__.build_job dwells LaserOnDelay + GC.GasDelay = 0 + 100 ms = 400 "
        "ticks after DO9 on; the leaked CO2 layer-2 job (frames 0x39/0x3a) shows 14 stationary "
        "ticks. The source of the 14 (3.5 ms, not an integer-ms dwell) is not traced (A3 V dwell "
        "builder 0x4427e0); GasDelay as a tick dwell is contradicted"
    ),
)
def test_port_pierce_dwell_matches_leaked_frames() -> None:
    ll = LayerLaser.from_layer(LAYER2)
    laser = ContourLaser(
        gas_port=3, laser_port=9, pierce_dwell_ms=ll.laser_on_delay_ms + GAS_DELAY_MS
    )
    b = JobStreamBuilder(laser_records=True)
    b.prologue(laser, ll.freq, ll.duty)
    i_on = max(i for i, r in enumerate(b.records) if r.type == RecordType.IO)
    assert len(b.records) - i_on - 1 == 14


@pytest.mark.xfail(
    strict=True,
    reason=(
        "fidelity gap: the port starts every contour at rest with zero acceleration (05 §7.2 node "
        "0 = 0, jerk-limited S-curve, J = 5000 for layer 2) and covers < 6 pulses in the first "
        "113 ticks; vendor frames 0x39/0x3a cover 90 pulses in the same 113 ticks after DO9 on "
        "(v0 ~ 6.9 mm/s, a ~ 570 mm/s²). The look-ahead core 0x100114d0 was not re-traced (05 §7.4)"
    ),
)
def test_port_cut_start_matches_leaked_frames() -> None:
    vendor = int(_vendor_cut_start()[-1])
    assert vendor == 90
    plan, geom = plan_line((0.0, 0.0), (-100.0, 0.0), LAYER2_PARAMS)
    dx, _ = TickQuantizer().quantize(sample_plan(plan, geom).xy)
    port = -sum(dx[:113])  # even if all 14 vendor dwell ticks counted as motion
    assert port >= vendor - 10


def test_rapid_start_frame_0x279_reproduced_tick_for_tick() -> None:
    """After the epilogue of frame 0x279 the rapid leaves from rest: 21 stationary ticks, then
    single +X pulses 5, 3, 2, 2 ticks apart.  The port's rapid (A6 §1.4 rapid block: Vmax =
    XFastMoveSpeed 500 x EmptyMoveSpeedFactor 1.1, A = 6000 x 1.5, Ta = EmptyMoveAccTime 125 ms
    x 0.001, J = 4 Vmax/Ta² = 140 800) reproduces all 38 ticks with a start carry in
    [-0.007, 0].  A jerk scan with free carry matches only J in ~[140 000, 148 500]; the cut-block
    Ta (0.2 s, J = 55 000) or the unscaled block (500/6000, J = 96 000) do not match."""
    items = FRAMES[0x279]
    last_ctl = max(k for k, it in enumerate(items) if it.opcode != 3000)
    vendor = [t[0] for t in _ticks(items[last_ctl + 1 :])]
    assert len(vendor) == 38 and [k for k, d in enumerate(vendor) if d] == [21, 27, 31, 34, 37]
    rapid = RapidParams(500.0 * 1.1, 5999.9999999999991 * 1.5, 125 * 0.001, 0.25)
    plan, geom = plan_line((0.0, 0.0), (300.0, 0.0), rapid)
    assert plan.jerk == pytest.approx(140800.0)
    xy = sample_plan(plan, geom).xy
    dx, _ = TickQuantizer(PPM, (0.0, 0.0)).quantize(xy)
    assert dx[:38] == vendor
    for wrong in (RapidParams(550.0, 9000.0, 0.2, 0.25), RapidParams(500.0, 6000.0, 0.125, 0.25)):
        p2, g2 = plan_line((0.0, 0.0), (300.0, 0.0), wrong)
        xy2 = sample_plan(p2, g2).xy
        assert all(
            TickQuantizer(PPM, (c, 0.0)).quantize(xy2)[0][:38] != vendor
            for c in np.linspace(0.0, -0.999, 200)
        )


# ======================================================================== quantiser / drift
PPM = AxisScale()


@pytest.mark.parametrize("end", [(1000.0, 0.0), (0.0, -1000.0), (707.1, 707.1), (-600.0, 800.0)])
def test_one_metre_line_final_pulse_count(end: tuple[float, float]) -> None:
    """1 m move: the tick sum is ``trunc(total·p/mm)`` toward zero (``_ftol2`` with carry, A3 §3),
    never more than one pulse from the exact value.  NB: *not* ``round`` - 1000 mm on X is
    258039.54 pulses and the vendor quantiser leaves 258039 (the residual stays in the carry)."""
    plan, geom = plan_line((0.0, 0.0), end, PlannerParams(MANU_ACC, 0.2, 0.02, 250.0))
    motion = sample_plan(plan, geom)
    dx, dy = TickQuantizer(PPM).quantize(motion.xy)
    ex, ey = end[0] * PPM.x_per_mm, end[1] * PPM.y_per_mm
    assert sum(dx) == math.trunc(ex) and sum(dy) == math.trunc(ey)
    if end == (1000.0, 0.0):
        assert sum(dx) == 258039 != round(ex)
    assert abs(motion.v[-1]) < 1e-9 and max(map(abs, dx)) <= math.ceil(250.0 * DT * PPM.x_per_mm)


def test_ten_thousand_segment_polyline_has_no_drift() -> None:
    """10 000-segment polyline through the whole pipeline: at *every* tick the streamed position
    is within one pulse of the exact sampled position (no accumulated error), and the end count
    is within one pulse of the exact total."""
    rng = np.random.default_rng(1)
    ang = np.cumsum(rng.normal(0.0, 0.02, 10_000))
    pts = np.vstack(([0.0, 0.0], np.cumsum(np.column_stack((np.cos(ang), np.sin(ang))) * 0.1, 0)))
    fit = process([line_glyph(pts[i], pts[i + 1]) for i in range(10_000)], 0.02, 100.0)
    assert fit.pieces is not None
    plan = plan_velocity(fit.pieces, PlannerParams(MANU_ACC, 0.2, 0.02, 100.0))
    motion = sample_plan(plan, PathGeometry.from_glyphs(fit.smoothed))
    assert motion.xy[-1].tolist() == pytest.approx(pts[-1].tolist(), abs=1e-9)
    dx, dy = TickQuantizer(PPM).quantize(motion.xy)
    exact_x = (motion.xy[1:, 0] - motion.xy[0, 0]) * PPM.x_per_mm
    exact_y = (motion.xy[1:, 1] - motion.xy[0, 1]) * PPM.y_per_mm
    assert float(np.max(np.abs(np.cumsum(dx) - exact_x))) < 1.0 + 1e-6
    assert float(np.max(np.abs(np.cumsum(dy) - exact_y))) < 1.0 + 1e-6
    assert abs(sum(dx) - pts[-1, 0] * PPM.x_per_mm) < 1.0
    assert abs(sum(dy) - pts[-1, 1] * PPM.y_per_mm) < 1.0


def test_carry_persists_across_contours_and_rapids() -> None:
    """Contour, rapid, contour through one :class:`JobStreamBuilder`: the job's pulse sum equals
    trunc of the net displacement (11 §5.3 carry kept across contours)."""
    b = JobStreamBuilder(quantizer=TickQuantizer(PPM))
    params = PlannerParams(MANU_ACC, 0.2, 0.02, 80.0)
    rapid = RapidParams(550.0, 9000.0, 0.125, 0.25)
    pos = np.zeros(2)
    for k in range(40):
        a = np.array([k * 3.3331, (k % 7) * 1.77771])
        rp, rg = plan_line(pos, a, rapid)
        b.add_motion(sample_plan(rp, rg).xy)
        cp, cg = plan_line(a, a + (12.3457, -4.4441), params)
        cm = sample_plan(cp, cg)
        b.add_contour(cm.xy, 5000, 4)
        pos = cm.xy[-1]
    sx = sum(r.cell0 for r in b.records if r.type == RecordType.TICK)
    sy = sum(r.cell1 for r in b.records if r.type == RecordType.TICK)
    assert abs(sx - pos[0] * PPM.x_per_mm) < 1.0 and abs(sy - pos[1] * PPM.y_per_mm) < 1.0


# ================================================================================ int16 range
def test_int16_headroom_at_the_card_rate_ceiling() -> None:
    """The fastest path speed the planner can emit is ``750000/K`` = 750 mm/s (A6 §1.3): 48.4
    pulses per 250 µs tick, far inside int16.  The packer refuses what does not fit (A3 V)."""
    vmax = velocity_ceiling(1000)
    assert vmax * DT * PPM.x_per_mm < 49.0
    plan, geom = plan_line((0.0, 0.0), (-1500.0, 900.0), PlannerParams(20000.0, 0.06, 0.02, vmax))
    dx, dy = TickQuantizer(PPM).quantize(sample_plan(plan, geom).xy)
    assert max(map(abs, dx + dy)) <= 49
    # a mis-read card cycle (reg 50005) of 1 s at the ceiling would need 193 530 pulses per tick
    with pytest.raises(ValueError, match="int16"):
        tick_item(int(vmax * 1.0 * PPM.x_per_mm), 0, 5000, 0)


# ============================================================ sampler: unplanned one-tick jumps
def test_run_of_dropped_segments_is_not_collapsed_into_one_tick() -> None:
    """FIXED: 1000 pieces of 0.01 mm at 100 mm/s are each dropped by the ``segInterp`` rule
    (05 §7.6); the per-segment sampler used to land all 10 mm in one tick (2587 pulses)."""
    lens = [100.0] + [0.01] * 1000 + [100.0]
    plan = plan_velocity(
        PieceList.from_arrays(lens, feed=100.0), PlannerParams(MANU_ACC, 0.2, 0.02, 100.0)
    )
    total = sum(lens)
    geom = PathGeometry(
        np.array([0.0, total]), np.array([[0.0, 0.0], [total, 0.0]]), np.array([True])
    )
    for per_segment in (True, False):
        m = sample_plan(plan, geom, per_segment=per_segment)
        step = np.diff(m.xy[:, 0])
        assert float(step.max()) <= 100.0 * DT * (1.0 + 1e-9)
        assert m.xy[-1, 0] == pytest.approx(total)
    assert sample_plan(plan, geom).dropped_segments == 1000


def test_gap_between_glyphs_is_refused() -> None:
    """FIXED: a 50 mm gap inside a contour became one 12 909-pulse tick (inside int16, so the
    packer did not catch it) with the laser on; the geometry now refuses non-joining glyphs."""
    ok = [
        line_glyph((0.0, 0.0), (10.0, 0.0)),
        line_glyph((10.0 + MAX_JOINT_GAP / 2, 0.0), (20.0, 0.0)),
    ]
    PathGeometry.from_glyphs(process(ok, 0.02, 50.0).smoothed)
    bad = [line_glyph((0.0, 0.0), (10.0, 0.0)), line_glyph((60.0, 0.0), (80.0, 0.0))]
    with pytest.raises(ValueError, match="do not join"):
        PathGeometry.from_glyphs(process(bad, 0.02, 50.0).smoothed)


def test_per_tick_speed_bounded_on_a_polygon() -> None:
    """Per-segment sampling (vendor structure) may stretch one tick by half a cycle at a node
    (UNVERIFIED stitching, 05 §7.6), never more: every tick stays below 1.5 x the planned speed."""
    ring = [(20.0 * math.cos(t), 20.0 * math.sin(t)) for t in np.linspace(0.0, 2 * math.pi, 37)]
    fit = process([line_glyph(ring[i], ring[i + 1]) for i in range(36)], 0.02, 250.0)
    assert fit.pieces is not None
    plan = plan_velocity(fit.pieces, PlannerParams(MANU_ACC, 0.2, 0.02, 250.0))
    m = sample_plan(plan, PathGeometry.from_glyphs(fit.smoothed))
    tick_speed = np.hypot(*np.diff(m.xy, axis=0).T) / DT
    vt = plan.velocity(np.linspace(0.0, plan.total_time, 20_000))
    assert float(tick_speed.max()) <= 1.5 * float(vt.max()) + 1e-6


# ======================================================================== S-curve / look-ahead
def test_scurve_jerk_and_acceleration_continuity_over_a_contour() -> None:
    """Whole-contour profile: |a| <= A, |da/dt| <= J, acceleration continuous across node
    boundaries (every segment starts and ends at a = 0, 05 §7.5), ends at rest."""
    glyphs = [
        line_glyph((0.0, 0.0), (40.0, 0.0)),
        line_glyph((40.0, 0.0), (40.0, 30.0)),
        line_glyph((40.0, 30.0), (0.0, 30.0)),
        line_glyph((0.0, 30.0), (0.0, 0.0)),
    ]
    fit = process(glyphs, 0.02, 120.0)
    assert fit.pieces is not None
    params = PlannerParams(MANU_ACC, 0.2, 0.02, 120.0)
    plan = plan_velocity(fit.pieces, params)
    t = np.linspace(0.0, plan.total_time, 400_001)
    s, v, a = plan.state(t)
    h = t[1] - t[0]
    assert float(np.abs(a).max()) <= params.acc * (1 + 1e-9)
    assert float(np.abs(np.diff(a)).max()) <= plan.jerk * h * (1 + 1e-6) + 1e-9
    assert float(np.abs(np.diff(v)).max()) <= params.acc * h * (1 + 1e-6) + 1e-9
    assert v.min() >= -1e-9 and np.all(np.diff(s) >= -1e-12)
    assert v[0] == 0.0 and abs(v[-1]) < 1e-9 and abs(a[-1]) < 1e-6
    assert s[-1] == pytest.approx(fit.pieces.total_length, abs=1e-9)
    for p in plan.profiles:
        _, _, ab = p.state(np.array([0.0, p.total_time]))
        assert np.all(np.abs(ab) < 1e-6)


# ============================================================================ laser records
def _stream_audit(frames: list[PackedFrame]) -> tuple[int, int]:
    """(duty>0 ticks while DO9 is off, duty>0 ticks total)."""
    gate = False
    outside = lit = 0
    for i, f in enumerate(frames):
        for it in parse_fifo_words([i + 1, *f.data]).items:
            if it.opcode == 9999 and it.args[0] == 2 and it.args[1] == 0x100:
                gate = it.args[2] != 0
            t = it.tick
            if t is not None and t[3] > 0:
                lit += 1
                outside += 0 if gate else 1
    return outside, lit


def test_no_pwm_ticks_outside_the_laser_gate_synthetic() -> None:
    b = JobStreamBuilder(quantizer=TickQuantizer(PPM), laser_records=True)
    rapid = RapidParams(550.0, 9000.0, 0.125, 0.25)
    params = PlannerParams(MANU_ACC, 0.2, 0.02, 50.0)
    pos = np.zeros(2)
    for k in range(3):
        a = np.array([10.0 * k, 5.0])
        rp, rg = plan_line(pos, a, rapid)
        b.add_motion(sample_plan(rp, rg).xy)  # rapids: duty defaults to 0
        cp, cg = plan_line(a, a + (5.0, 0.0), params)
        cm = sample_plan(cp, cg)
        b.add_contour(cm.xy, 5000, 4, ContourLaser(pierce_dwell_ms=5))
        pos = cm.xy[-1]
    b.finish()
    outside, lit = _stream_audit(b.frames())
    assert lit > 0 and outside == 0


def test_no_pwm_ticks_outside_the_laser_gate_vendor_jobs(src_dir: Path) -> None:
    from nexcut.io.chf import load_chf
    from nexcut.io.params import read_params
    from nexcut.plan.__main__ import build_job

    f = src_dir / "File"
    manu, hard = (
        read_params(f / "BkManuPara.xml", "manu"),
        read_params(f / "BkHardPara.xml", "hard"),
    )
    layer = read_params(f / "BkLayerPara.xml", "layer")
    for chf in (src_dir / "Graph" / "Work1" / "2.chf", f / "Temp" / "tempGraph.chf"):
        job = build_job(load_chf(chf), manu, hard, layer, laser_records=True)
        outside, lit = _stream_audit(job.frames)
        assert lit > 0 and outside == 0, chf
        assert max(fr.vector_words for fr in job.frames) <= 310


# ============================================================================== frame packing
def test_largest_supported_record_bounds_the_frame() -> None:
    """The flush test runs after a whole record (A3 §4.1), so a frame is 299 words + the largest
    record.  Largest record this builder emits = type 8 after a tick: 3001 + 109 + 2001 + 118 =
    11 words -> 310-word vector (306 data words), which still fits the 1448-byte UDP buffer."""
    builder = ItemBuilder()
    records = [
        Record.tick(1, 1, 5000, 4),
        Record(RecordType.IO, IoSub.PWM_1, 0, 4, 5000),
        Record(RecordType.IO, IoSub.DA, 0, value=50),
        Record.do(9, True),
        Record(RecordType.IO, IoSub.WAIT_MS, value=10),
        Record(RecordType.IO, IoSub.ZF_BOOK),
        Record.mode(5),
        Record(RecordType.WAIT_CONDITION, 3, value=2),
        Record(RecordType.ZF_LIFT),
        Record(RecordType.ZF_DOCK),
        Record(RecordType.BOUNDARY),
        Record(RecordType.ZF_CUT_HEIGHT),
        Record(RecordType.ZF_WAIT),
    ]
    widest = 0
    for rec in records:
        builder.new_batch()
        builder.build(Record.tick(0, 0))
        widest = max(widest, sum(len(it.words()) for it in builder.build(rec)))
    assert widest == 11
    packer = FramePacker()
    for _ in range(98):
        packer.add_record([tick_item(1, 0, 5000, 0).words()])  # 298 words, not flushed
    packer.add_record(
        [[0x0BB9], [0x0008006D, 1000, 0], [0x000807D1, 0x03000002, 20000], [0x000C0076, 4, 0, 35]]
    )
    frame = packer.frames[0]
    assert frame.vector_words == 298 + 11 and len(frame.data) == 305
    assert len(encode_vector(frame.vector(1), seq=1)) <= MAX_FRAME_LEN


def test_reg_1016_byte_accounting_boundary() -> None:
    """``bytes + 2000 <= reg 1016`` with bytes = 4 x vector words incl. ``0x40, 0x66`` (A3 §7):
    a 99-tick frame is 1204 bytes; 3204 free sends it, 3203 does not; space drops by 1204."""
    frame = PackedFrame(tuple(tick_item(1, 0, 5000, 0).words()) * 99)
    assert frame.byte_size == 1204 and frame.count == 0x12A
    sent: list[int] = []
    feeder = FifoFeeder([frame, frame])
    res = feeder.fill(0x38, 3203, lambda fid, words: sent.append(fid))
    assert res.stopped_by == "space" and not sent
    res = feeder.fill(0x38, 3204 + 1204, lambda fid, words: sent.append(fid))
    assert sent == [0x39, 0x3A] and res.space_left == 3204 + 1204 - 2 * 1204
