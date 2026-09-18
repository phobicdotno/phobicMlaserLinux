"""The vectorised planner path produces exactly what the scalar one produced (STATUS §5 task 6).

``docs/STATUS.md`` §5 task 6 / strict xfail X13 replaced the per-tick object path of
``plan/items.py`` + ``mcc/fifo.py`` with a streaming, numpy-vectorised one.  The old path is not
deleted - it is the *definition* of the rule and the vendor goldens still run through it - so
every claim of this phase is a claim about two implementations agreeing:

* :func:`nexcut.plan.items.carry_cells` vs :func:`~nexcut.plan.items.carry_cells_scalar` - the
  truncate-with-carry quantiser of 11 §5.3, over more than a million random ticks and on the
  awkward cases (sign changes, exact integers, zeros, a carry that never resets, arrays longer
  than the internal chunk);
* :func:`nexcut.plan.items.tick_words` vs :func:`~nexcut.plan.items.tick_item` - the opcode-3000
  word pair of A3 §4.2, including the ``freq``/``duty`` masking corners;
* :class:`nexcut.mcc.fifo.FrameStream` vs :class:`~nexcut.mcc.fifo.FramePacker` - the 300-word
  flush rule of 11 §5.1, frame for frame;
* :class:`nexcut.plan.items.JobFrameStream` vs :meth:`~nexcut.plan.items.JobStreamBuilder.frames`
  and :func:`nexcut.plan.__main__.stream_job` vs
  :func:`~nexcut.plan.__main__.build_job` - the whole job, frame for frame, on the same drawing
  the CLI tests use.

Nothing here touches a card (PORT-PLAN §8): it is all offline planning.
"""

from __future__ import annotations

import numpy as np
import pytest

from nexcut.mcc.fifo import FramePacker, FrameStream, item_header
from nexcut.plan.__main__ import JobResult, build_job, stream_job
from nexcut.plan.items import (
    _CARRY_CHUNK,
    _CARRY_SCALAR_MAX,
    AxisScale,
    ContourLaser,
    JobFrameStream,
    JobStreamBuilder,
    StreamConfig,
    TickQuantizer,
    carry_cells,
    carry_cells_scalar,
    tick_item,
    tick_words,
    total_ticks,
)

# Sibling test module (see tests/test_stream_plan_fidelity_review.py on the import mode).
from test_plan_items_cli import job_doc, machine_docs  # noqa: I001

PPM = AxisScale()
"""This CF1390: 258.04 / 257.99 pulses per mm (11 §5.3)."""


# ================================================================ 11 §5.3: the carry quantiser
def _chain(increments: list[np.ndarray], carry: float) -> tuple[list[list[int]], float]:
    """Run the scalar rule over a sequence of segments, carrying the residual (11 §5.3)."""
    out = []
    for v in increments:
        cells, carry = carry_cells_scalar(v, carry)
        out.append(cells)
    return out, carry


def test_a_million_random_ticks_quantise_identically() -> None:
    """The gate the task sets: > 10**6 ticks, cell for cell, with the carry chained across
    segments exactly as a job chains it across contours (the vendor never resets it)."""
    rng = np.random.default_rng(20260916)
    segments: list[np.ndarray] = []
    total = 0
    while total < 1_100_000:
        n = int(rng.integers(1, 4000))
        scale = float(10.0 ** rng.integers(-3, 2))  # 1e-3 .. 10 pulses per tick
        v = rng.standard_normal(n) * scale
        kind = int(rng.integers(0, 4))
        if kind == 1:  # a move in one direction: all increments of one sign
            v = np.abs(v)
        elif kind == 2:
            v = -np.abs(v)
        elif kind == 3:  # near-integer increments: the truncation boundary itself
            v = np.rint(v) + rng.choice([-1e-12, 0.0, 1e-12], size=n)
        segments.append(v)
        total += n
    scalar, scalar_carry = _chain(segments, 0.25)
    carry = 0.25
    for v, expect in zip(segments, scalar, strict=True):
        cells, carry = carry_cells(v, carry)
        assert cells.tolist() == expect
    assert abs(carry - scalar_carry) < 1e-9, "the residual carry drifted out of the grid"
    assert total > 1_000_000


def test_gridded_increments_are_bit_identical_including_the_carry() -> None:
    """The exactness argument of :func:`carry_cells`: on increments that already lie on the
    fixed-point grid the scalar loop does no rounding at all, so both paths evaluate the same
    exact rational recurrence - cells *and* residual, to the last bit."""
    rng = np.random.default_rng(4)
    v = np.rint(rng.uniform(-1.0, 1.0, 50_000) * 100.0 * 2**40) / 2**40
    _, exp = np.frexp(np.abs(v).max())  # the grid carry_cells picks for this magnitude
    assert 47 - int(exp) == 40, "the test data must lie on the grid the implementation chooses"
    for carry in (0.0, 0.5, -0.5, 0.25):
        cells, out = carry_cells(v, carry)
        expect, expect_carry = carry_cells_scalar(v, carry)
        assert cells.tolist() == expect
        assert out == expect_carry


@pytest.mark.parametrize(
    "values",
    [
        [0.0] * 8,
        [-0.5] * 8,  # trunc, not floor: 0, -1, 0, -1 … (floor would emit -1, 0, -1, 0)
        [0.5] * 8,
        [1e-9] * 100,
        [-1.0, 1.0, -1.0, 1.0],
        [0.6, -0.7, 0.6, -0.7, 0.6],
        [3.0, -3.0, 0.0, 2.9999999999],
        [32767.4, -32767.4],
        [1e-18, 1e18],  # the fallback: no grid holds both
        [float("inf")],
    ],
)
def test_awkward_increment_patterns_match_the_scalar_loop(values: list[float]) -> None:
    """Sign changes, exact integers, zeros and magnitudes no grid can hold together."""
    v = np.array(values, dtype=np.float64)
    for carry in (0.0, 0.75, -0.75):
        try:
            expect, expect_carry = carry_cells_scalar(v, carry)
        except (ValueError, OverflowError) as exc:  # int(inf) - the vectorised path must agree
            with pytest.raises(type(exc)):
                carry_cells(v, carry)
            continue
        cells, out = carry_cells(v, carry)
        assert cells.tolist() == expect
        assert abs(out - expect_carry) < 1e-9


def test_an_array_longer_than_one_chunk_carries_across_the_chunk_boundary() -> None:
    """:func:`carry_cells` sums in int64 blocks; the residual has to cross a block exactly."""
    rng = np.random.default_rng(9)
    v = rng.standard_normal(_CARRY_CHUNK * 2 + 37) * 7.0
    cells, out = carry_cells(v, -0.4)
    expect, expect_carry = carry_cells_scalar(v, -0.4)
    assert cells.tolist() == expect
    assert abs(out - expect_carry) < 1e-9


@pytest.mark.parametrize("n", [1, _CARRY_SCALAR_MAX - 1, _CARRY_SCALAR_MAX, _CARRY_SCALAR_MAX + 1])
def test_the_short_run_branch_and_the_array_branch_agree_across_the_threshold(n: int) -> None:
    """A short run takes the scalar loop (it is faster there); both sides of the switch have to
    produce the same cells *and* the same residual, or a job would change with its geometry."""
    rng = np.random.default_rng(n)
    v = rng.standard_normal(n) * 6.0
    cells, out = carry_cells(v, 0.1)
    expect, expect_carry = carry_cells_scalar(v, 0.1)
    assert cells.tolist() == expect
    assert abs(out - expect_carry) < 1e-9


def test_both_axes_at_once_equal_one_axis_at_a_time() -> None:
    """The planner quantises X and Y in one array pass; per axis the answer is unchanged."""
    rng = np.random.default_rng(12)
    v = rng.standard_normal((5000, 2)) * np.array([13.0, 0.002])
    cells, carry = carry_cells(v, [0.3, -0.8])
    for axis, c0 in enumerate((0.3, -0.8)):
        expect, expect_carry = carry_cells_scalar(v[:, axis], c0)
        assert cells[:, axis].tolist() == expect
        assert abs(carry[axis] - expect_carry) < 1e-9


def test_tick_quantizer_keeps_its_list_contract_and_its_carry() -> None:
    """``quantize`` still returns lists and ``quantize_arrays`` the same numbers as arrays."""
    pts = np.column_stack((np.linspace(0, 12.3, 900), np.linspace(0, -4.5, 900)))
    a, b = TickQuantizer(PPM, (0.1, -0.2)), TickQuantizer(PPM, (0.1, -0.2))
    dx, dy = a.quantize(pts)
    ax, ay = b.quantize_arrays(pts)
    assert isinstance(dx, list) and dx == ax.tolist() and dy == ay.tolist()
    assert a.carry == b.carry
    assert a.quantize(np.zeros((1, 2))) == ([], [])


# ================================================================== A3 §4.2: the 3000 word pair
def test_tick_words_matches_tick_item_over_random_ticks() -> None:
    """Word for word, laser flag included, against the scalar :func:`tick_item`."""
    rng = np.random.default_rng(31)
    dx = rng.integers(-0x8000, 0x8000, 500)
    dy = rng.integers(-0x8000, 0x8000, 500)
    freq = rng.integers(-3, 70000, 500)
    duty = rng.integers(0, 300, 500)
    words, laser = tick_words(dx, dy, freq, duty)
    expect: list[int] = []
    for i in range(500):
        item = tick_item(int(dx[i]), int(dy[i]), int(freq[i]), int(duty[i]))
        expect += item.words()
        assert bool(laser[i]) is item.laser
    assert words.tolist() == expect


@pytest.mark.parametrize("freq", [0, 1, -1, 5000, 0x10000, 0xFFFF])
@pytest.mark.parametrize("duty", [0, 4, 255, 256])
def test_tick_words_scalar_freq_and_duty_follow_the_same_masking(freq: int, duty: int) -> None:
    """The u16 mask runs *before* the ``freq < 1`` test (A3 §4.2); the scalar path is the truth."""
    words, laser = tick_words([5, -5], [0, 7], freq, duty)
    expect = tick_item(5, 0, freq, duty).words() + tick_item(-5, 7, freq, duty).words()
    assert words.tolist() == expect
    assert list(map(bool, laser)) == [tick_item(5, 0, freq, duty).laser] * 2


def test_tick_words_refuses_the_same_out_of_range_step_as_the_scalar_path() -> None:
    with pytest.raises(ValueError, match="dX 40000 outside int16"):
        tick_words([1, 40000], [0, 0])
    with pytest.raises(ValueError, match="dY -40000 outside int16"):
        tick_words([0, 0], [1, -40000])


# =============================================================== 11 §5.1: the 300-word flush rule
_TICK = [item_header(3000, 2), 0x0001_0001, 0x1388_0000]
_MODE = [item_header(3002, 1), 5]
_BOUNDARY = [item_header(3001, 0)]


def test_frame_stream_closes_frames_exactly_where_frame_packer_does() -> None:
    """Random mixes of bulk ticks and control records; the frames must be identical objects."""
    rng = np.random.default_rng(77)
    for _ in range(40):
        plan = [int(rng.integers(0, 260)) if rng.random() < 0.7 else -1 for _ in range(12)]
        packer, stream = FramePacker(), FrameStream()
        streamed = []
        for step in plan:
            if step < 0:
                packer.add_record([_MODE], 0)
                streamed += stream.push_record([_MODE], 0)
                continue
            for _tick in range(step):
                packer.add_record([_TICK], 1)
            words = np.array(_TICK * step, dtype=np.uint32)
            laser = np.ones(step, dtype=bool)
            streamed += list(stream.push_uniform(words, 3, laser))
        packer.end_batch()
        streamed += stream.flush()
        assert streamed == packer.frames


def test_frame_stream_rejects_a_malformed_uniform_block() -> None:
    stream = FrameStream()
    with pytest.raises(ValueError, match="not a whole number"):
        list(stream.push_uniform(np.array(_TICK + [0], dtype=np.uint32), 3))
    with pytest.raises(ValueError, match="payload words"):
        list(stream.push_uniform(np.array(_TICK + _TICK, dtype=np.uint32), 2))


# ================================================================ the whole job, frame for frame
def _scalar_job(laser_records: bool) -> list:
    """The record path of ``JobStreamBuilder``: one Record and one Item per tick."""
    builder = JobStreamBuilder(
        config=StreamConfig(), quantizer=TickQuantizer(PPM), laser_records=laser_records
    )
    for points, freq, duty in _job_moves():
        if freq is None:
            builder.add_motion(points)
        else:
            builder.add_contour(points, freq, duty, ContourLaser())
    builder.finish()
    return builder.frames()


def _streamed_job(laser_records: bool) -> tuple[list, JobFrameStream]:
    builder = JobStreamBuilder(
        config=StreamConfig(), quantizer=TickQuantizer(PPM), laser_records=laser_records
    )
    stream = JobFrameStream(builder)
    frames = []
    for points, freq, duty in _job_moves():
        if freq is None:
            frames += list(stream.motion(points))
        else:
            frames += list(stream.contour(points, freq, duty, ContourLaser()))
    frames += list(stream.finish())
    return frames, stream


def _job_moves() -> list[tuple[np.ndarray, np.ndarray | None, np.ndarray | None]]:
    """A rapid and two contours of very different length, with a per-tick PWM schedule."""
    rng = np.random.default_rng(5)
    moves = []
    for n in (37, 900, 140):
        t = np.linspace(0, 1, n)
        pts = np.column_stack((8.0 * t + 0.3 * np.sin(9 * t), 5.0 * np.cos(3 * t)))
        if n == 37:
            moves.append((pts, None, None))
            continue
        freq = rng.integers(1000, 20000, n - 1)
        duty = rng.integers(0, 60, n - 1)
        moves.append((pts, freq, duty))
    return moves


@pytest.mark.parametrize("laser_records", [False, True])
def test_the_streamed_job_is_the_scalar_job_frame_for_frame(laser_records: bool) -> None:
    """Same frames, same laser counts, same item counts - and the tick count the stream keeps
    while it runs equals what :func:`total_ticks` reads back out of the packed words."""
    expect = _scalar_job(laser_records)
    frames, stream = _streamed_job(laser_records)
    assert frames == expect
    assert stream.frames == len(expect)
    assert stream.ticks == total_ticks(expect)


@pytest.mark.parametrize("laser_records", [False, True])
def test_stream_job_and_build_job_plan_the_same_frames(laser_records: bool) -> None:
    """``build_job`` is ``stream_job`` collected into a list: the CLI and the daemon see the
    same job the in-memory callers do, and the statistics are filled in either way."""
    manu, hard, layer = machine_docs()
    job = build_job(job_doc(), manu, hard, layer, laser_records=laser_records)
    stats = JobResult()
    streamed = list(stream_job(job_doc(), manu, hard, layer, laser_records=laser_records, stats=stats))
    assert streamed == job.frames
    assert (stats.contours, stats.ticks, stats.frame_count) == (
        job.contours,
        job.ticks,
        len(job.frames),
    )
    assert stats.ticks == total_ticks(job.frames)
    assert stats.cut_length_mm == pytest.approx(job.cut_length_mm)
    assert stats.cycle_us == job.cycle_us


def test_stream_job_is_lazy_and_holds_no_frame_list() -> None:
    """The point of the change: asking for one frame must not plan the whole job."""
    manu, hard, layer = machine_docs()
    stats = JobResult()
    frames = stream_job(job_doc(), manu, hard, layer, stats=stats)
    assert stats.frame_count == 0 and stats.contours == 0  # nothing planned yet
    first = next(iter(frames))
    assert first.items > 0
    # The first frame closes long before the first contour is finished, so the statistics -
    # which are written per contour - are still empty: nothing downstream of that frame has run.
    assert stats.contours == 0
    rest = sum(1 for _ in frames)
    assert stats.contours == 2 and stats.frame_count == rest + 1
