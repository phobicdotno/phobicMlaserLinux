"""Adversarial review of the vectorised, streaming planner (``docs/STATUS.md`` §5 task 6, X13).

Lens: **silent numerical drift**.  The phase replaced the per-tick object path of
``plan/items.py`` + ``mcc/fifo.py`` with numpy array work and a generator that yields frames,
and nothing about that change is visible in the frames it produces - a wrong carry, a record
that moves past its ticks or a frame that closes one tick early all look like a perfectly
ordinary job.  Every claim below is therefore re-derived here from the documented rule rather
than taken from the implementation:

* **11 §5.3, the truncate-with-carry quantiser.**  An independent differential fuzz -
  different generator, different seeds and different shape families from
  ``tests/test_plan_vectorised_equivalence.py`` - runs :func:`~nexcut.plan.items.carry_cells`
  and :func:`~nexcut.plan.items.carry_cells_scalar` over more than 10**6 ticks of reversals,
  zero-length runs, sub-pulse moves, exact half-integers and int16-edge magnitudes, with the
  residual carry chained across calls the way a job chains it across contours.  Then the
  totals: a 1 m line, a 10 000-segment polyline and a 10**6-tick job must each emit exactly
  ``trunc(net_mm · pulses_per_mm)`` pulses, with the residual carry equal to the fraction that
  was held back - no drift, however the job is cut up.
* **11 §5.1, the 300-word flush rule**, for every record width and for partial laser masks,
  against :class:`~nexcut.mcc.fifo.FramePacker`.
* **11 §5.4, the record grammar.**  The order of the prologue/epilogue records around the cut
  ticks, and the 22 leaked vendor frames re-encoded through the *production* path
  (:class:`~nexcut.plan.items.JobFrameStream`) - ``tests/test_plan_items_golden.py`` pins them
  through the scalar :class:`~nexcut.plan.items.JobStreamBuilder` only, so a divergence in the
  streaming path could not reach a vendor byte before this file.

Findings of the review, each with the test that failed first:

============ ============================================================================
``R-V1``     :meth:`JobFrameStream.motion` emitted the records the wrapped builder had
             queued **after** the ticks instead of before them, so a prologue written
             before a move put its ``3002`` mode, the gas/laser ``9999`` DO records, the
             ZF ``103`` cut-height move and its ``2001`` follow wait behind the cut ticks
             they have to precede.  ``test_a_record_queued_before_a_move_precedes_its_ticks``
             and the three leaked-frame tests.
``R-V2``     :func:`~nexcut.plan.items.tick_words` named a different tick and a different
             axis from :func:`~nexcut.plan.items.tick_item` for the same overflowing run
             (it scanned dX over the whole array first; the scalar path raises on the first
             offending *tick* and tests dY before dX inside it).  Diagnostic only - both
             raise ``ValueError`` - but "which tick overflowed" must not depend on which
             path ran.  ``test_an_int16_overflow_names_the_same_tick_on_both_paths``.
``R-V3``     :meth:`~nexcut.mcc.fifo.FrameStream.push_uniform` accepted a laser mask of the
             wrong length: too short raised ``IndexError`` from inside the cumulative sum,
             too long silently mis-charged :attr:`PackedFrame.laser_items`.
             ``test_push_uniform_refuses_a_laser_mask_of_the_wrong_length``.
============ ============================================================================

Nothing here touches a card (PORT-PLAN §8): it is all offline planning.
"""

from __future__ import annotations

import random

import numpy as np
import pytest

from nexcut.mcc.fifo import (
    FLUSH_WORDS,
    PREFIX_WORDS,
    FramePacker,
    FrameStream,
    format_frame_line,
    item_header,
)
from nexcut.plan.items import (
    OP_TICK,
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

# Sibling test modules (see tests/test_stream_plan_fidelity_review.py on the import mode).
from test_plan_items_cli import job_doc, machine_docs  # noqa: I001
from test_plan_items_golden import FRAMES  # noqa: I001

PPM = AxisScale()
"""This CF1390: 258.03954456 / 257.98961592 pulses per mm (11 §5.3)."""

MAX_FRAME_BYTES = 1448
"""The largest FIFO vector the port is willing to put on the wire (11 §5.1, A3 §7)."""


# ============================================================ 11 §5.3: the carry quantiser
def _increments(points_mm: np.ndarray) -> np.ndarray:
    """``(Xs_i - Xs_{i-1})·f`` per axis, re-derived here from 11 §5.3 rather than imported."""
    scaled = np.asarray(points_mm, dtype=np.float64) * 10000.0
    return (scaled[1:] - scaled[:-1]) * np.array([PPM.x_factor, PPM.y_factor])


def _shape(rng: np.random.Generator, kind: int, n: int) -> np.ndarray:
    """One family of per-tick increments, in pulses (the units the quantiser works in)."""
    if kind == 0:  # ordinary cut ticks: a few tens of pulses each
        return rng.standard_normal(n) * 40.0
    if kind == 1:  # a reversal: monotone up, then monotone down through zero
        half = n // 2 + 1
        return np.concatenate([np.linspace(0.0, 30.0, half), np.linspace(30.0, -30.0, n)])[:n]
    if kind == 2:  # zero-length segments: nothing moves at all
        return np.zeros(n)
    if kind == 3:  # sub-pulse moves: a thousand ticks to one pulse
        return np.full(n, 1e-3) * rng.choice([-1.0, 1.0])
    if kind == 4:  # exact half-integers: trunc and floor disagree on every other tick
        return rng.choice([-1.5, -0.5, 0.0, 0.5, 1.5], size=n)
    if kind == 5:  # int16 edges: the largest step the packer can still hold
        return rng.choice([32767.4, -32767.4, 32766.9999, -32766.9999], size=n)
    if kind == 6:  # sign flip on every tick
        return np.where(np.arange(n) % 2 == 0, 0.7, -0.7)
    if kind == 7:  # a hair either side of an integer
        return np.rint(rng.standard_normal(n) * 9.0) + rng.choice(
            [-(2.0**-40), 0.0, 2.0**-40, -1e-15, 1e-15], size=n
        )
    if kind == 8:  # mostly still, with rare rapid-speed spikes
        v = np.zeros(n)
        idx = rng.integers(0, n, max(1, n // 40))
        v[idx] = rng.standard_normal(len(idx)) * 32000.0
        return v
    return rng.standard_normal(n) * float(10.0 ** rng.integers(-6, 5))


def _carry_bound(increments: np.ndarray) -> float:
    """Largest residual difference :func:`carry_cells` may show against the scalar loop.

    It rounds each increment onto a grid of ``2**-bits`` with ``bits = 47 - exp`` and
    ``max|increment| < 2**exp`` (see the function), i.e. a grid no coarser than
    ``max|increment| · 2**-46``.  Half a grid unit per tick is the worst case, so ``n`` ticks
    can move the residual by at most ``n · max|increment| · 2**-47``.
    """
    n = int(np.asarray(increments).shape[0])
    maxabs = float(np.abs(increments).max()) if n else 0.0
    # + one re-gridding of the incoming carry, which is rounded onto the same grid on entry
    return n * maxabs * 2.0**-47 + max(2.0**-48, maxabs * 2.0**-48)


def test_an_independent_fuzz_of_more_than_a_million_ticks_finds_no_divergence() -> None:
    """The gate the review sets: > 10**6 ticks over ten shape families, cell for cell, with
    the residual carry chained from call to call exactly as a job chains it from contour to
    contour (the vendor never resets it, 11 §5.3).

    Independent of ``tests/test_plan_vectorised_equivalence.py``: different generator,
    different seeds, and the awkward families (reversals, zero-length runs, sub-pulse moves,
    exact half-integers, int16 edges) are drawn deliberately rather than by chance.
    """
    rng = np.random.default_rng(0xD1F7)
    total = 0
    carry = vector_carry = 0.375
    allowance = 0.0
    runs = 0
    while total < 1_050_000:
        kind = runs % 10
        n = int(rng.integers(1, 5000))
        v = _shape(rng, kind, n)
        expect, carry = carry_cells_scalar(v, carry)
        cells, vector_carry = carry_cells(v, vector_carry)
        assert cells.tolist() == expect, f"shape family {kind}, run of {n} ticks"
        # The grid is relative to the run's largest increment (2**-47 of it, :func:`carry_cells`),
        # so the residual may move by at most one grid unit per tick, and the two chains keep
        # whatever they have already separated by.  Stated as the bound the implementation
        # guarantees rather than a constant, which would only hold for the magnitudes this
        # particular generator happens to draw (family 5 is 32 767 pulses a tick: a grid unit
        # there is 2**-32, ten orders coarser than on a 6-pulse cut tick).
        allowance += _carry_bound(v)
        assert abs(vector_carry - carry) <= allowance, (
            f"carry drifted past the grid bound on shape family {kind}"
        )
        total += n
        runs += 1
    assert total > 1_000_000 and runs >= 10  # every family was drawn at least once
    # and in absolute terms the two chains are still the same place, a millionth of a pulse apart
    assert abs(vector_carry - carry) < 1e-6


@pytest.mark.parametrize("gap", [0, 4, 8, 14, 20])
def test_one_shared_grid_for_two_axes_does_not_coarsen_the_slower_one(gap: int) -> None:
    """:func:`carry_cells` picks its fixed-point grid from the largest increment of **all**
    axes at once.  A job whose X races and whose Y crawls therefore quantises Y on a grid
    chosen for X; per axis the cells still have to be the scalar loop's, or a shallow slope
    would lose pulses that a steep one keeps.
    """
    rng = np.random.default_rng(1000 + gap)
    n = 4000
    v = np.column_stack((rng.standard_normal(n) * 32.0, rng.standard_normal(n) * 32.0 * 2.0**-gap))
    carry_in = [0.4, -0.6]
    cells, carry = carry_cells(v, carry_in)
    for axis in (0, 1):
        expect, expect_carry = carry_cells_scalar(v[:, axis], carry_in[axis])
        assert cells[:, axis].tolist() == expect, f"axis {axis}"
        assert abs(carry[axis] - expect_carry) < 1e-9


def _pulse_total(points_mm: np.ndarray) -> tuple[int, int, float, float, list[float]]:
    """``(sum dX, sum dY, exact X, exact Y, residual carry)`` for one point list."""
    q = TickQuantizer(PPM)
    dx, dy = q.quantize_arrays(points_mm)
    net = np.asarray(points_mm)[-1] - np.asarray(points_mm)[0]
    return int(dx.sum()), int(dy.sum()), net[0] * PPM.x_per_mm, net[1] * PPM.y_per_mm, q.carry


@pytest.mark.parametrize(
    ("name", "points"),
    [
        (
            "1 m line",  # 4 000 mm/s... no: 1 000 mm sampled into 40 000 ticks
            np.column_stack((np.linspace(0.0, 1000.0, 40_001), np.zeros(40_001))),
        ),
        (
            "10 000-segment polyline",  # a zigzag, so every segment reverses Y
            np.column_stack(
                (
                    np.arange(10_001) * 0.1,
                    np.where(np.arange(10_001) % 2, 0.05, 0.0),
                )
            ),
        ),
        (
            "10**6 ticks on a diagonal",
            np.column_stack(
                (np.linspace(0.0, 700.0, 1_000_001), np.linspace(0.0, 400.0, 1_000_001))
            ),
        ),
    ],
    ids=["1m-line", "10k-polyline", "1e6-ticks"],
)
def test_a_long_move_emits_exactly_the_truncated_exact_pulse_count(
    name: str, points: np.ndarray
) -> None:
    """11 §5.3: ``cell = trunc(increment + carry)`` and ``carry = t - cell``, so after ``n``
    ticks the emitted pulses plus the residual carry are the exact displacement **to the last
    bit of the arithmetic** - the carry is the only place a fraction can hide, and it never
    exceeds one pulse.  On a move that never reverses the carry also never changes sign, which
    makes the total exactly ``trunc(net · pulses/mm)``.

    This is the no-drift statement the vectorised path has to keep at job scale: the residual
    is re-derived from the int64 running sum at every internal chunk boundary, so an error
    there would show up as a total that is off by a pulse or a carry that walks away.
    """
    sx, sy, ex, ey, carry = _pulse_total(points)
    assert sx == int(np.trunc(ex)) and sy == int(np.trunc(ey))
    assert sx + carry[0] == pytest.approx(ex, abs=1e-6)
    assert sy + carry[1] == pytest.approx(ey, abs=1e-6)
    assert abs(carry[0]) < 1.0 and abs(carry[1]) < 1.0


def test_cutting_a_move_into_ten_thousand_contours_emits_the_same_pulses() -> None:
    """The carry persists across contours (11 §5.3, static array ``0x9495b0``), so how a job
    is divided cannot change what it emits.  One 10 000-interval move against the same move
    quantised one interval at a time - 10 000 separate :func:`carry_cells` calls, every one
    of them short enough to take the scalar branch - must give identical pulses.
    """
    pts = np.column_stack(
        (np.linspace(0.0, 137.0, 10_001), np.cumsum(np.full(10_001, 0.0013)) - 0.0013)
    )
    whole = TickQuantizer(PPM)
    dx, dy = whole.quantize_arrays(pts)

    piece = TickQuantizer(PPM)
    px: list[int] = []
    py: list[int] = []
    for i in range(10_000):
        a, b = piece.quantize_arrays(pts[i : i + 2])
        px += a.tolist()
        py += b.tolist()
    assert px == dx.tolist() and py == dy.tolist()
    # 10 000 separate float accumulations against one: the cells are identical, the residual
    # agrees to well under the pulse that would matter.
    assert piece.carry == pytest.approx(whole.carry, abs=1e-9)
    assert sum(px) == int(np.trunc((pts[-1, 0] - pts[0, 0]) * PPM.x_per_mm))
    assert sum(py) == int(np.trunc((pts[-1, 1] - pts[0, 1]) * PPM.y_per_mm))


@pytest.mark.parametrize(
    ("name", "points"),
    [
        ("1 m line", np.column_stack((np.linspace(0.0, 1000.0, 40_001), np.zeros(40_001)))),
        (
            "10**6 ticks on a diagonal",
            np.column_stack(
                (np.linspace(0.0, 700.0, 1_000_001), np.linspace(0.0, 400.0, 1_000_001))
            ),
        ),
    ],
    ids=["1m-line", "1e6-ticks"],
)
def test_a_constant_speed_line_never_drifts_more_than_one_pulse_from_the_scalar_loop(
    name: str, points: np.ndarray
) -> None:
    """**R-V4.**  :func:`carry_cells` is *not* cell-for-cell identical to
    :func:`carry_cells_scalar` on a constant-speed straight line, which is the most ordinary
    geometry a job has.

    Why the existing fuzz cannot see it: :func:`carry_cells` rounds every increment onto a
    fixed-point grid before the recurrence, and the scalar loop does not.  With *random*
    increments those roundings are random too and the running sum wanders as ``sqrt(n)``; with
    a **constant** increment every tick gets the identical rounding, so the sums separate
    linearly in ``n``.  Once the gap reaches the distance from the running sum to an integer,
    one pulse moves one tick.  Measured on the review machine: 2 cells of 40 000 on the 1 m
    line, 372 of 1 000 000 on the diagonal.

    What is actually guaranteed, and what this test pins:

    * the **position** never differs by more than one pulse (3.9 µm) at any tick - a pulse is
      displaced by one 250 µs tick, never dropped or duplicated;
    * the **totals** are exactly equal, so nothing accumulates over a job of any length;
    * the **residual carry** stays inside the grid bound, so the next contour starts from the
      same place.

    The claim to read as "the two paths agree frame for frame" (``plan/items.py`` module
    docstring, ``docs/STATUS.md`` §1.5 "pinned cell for cell") is therefore true only up to
    this bound; it is below the vendor's own x87-vs-IEEE difference, so it is not worth paying
    planner throughput for, but it must not be allowed to grow.
    """
    inc = _increments(points)
    for axis in (0, 1):
        v = inc[:, axis]
        expect, expect_carry = carry_cells_scalar(v, 0.0)
        cells, carry = carry_cells(v, 0.0)
        got = cells.tolist()
        assert sum(got) == sum(expect), f"axis {axis}: a pulse was gained or lost"
        walk = np.cumsum(got) - np.cumsum(expect)
        assert int(np.abs(walk).max()) <= 1, f"axis {axis}: more than one pulse of position"
        assert abs(carry - expect_carry) <= _carry_bound(v)
        differing = int(np.count_nonzero(np.asarray(got) != np.asarray(expect)))
        # informational bound: a regression that coarsened the grid would blow straight past it
        assert differing <= max(8, len(v) // 1000), f"axis {axis}: {differing} cells differ"


# ================================================================ A3 §4.2: the 3000 word pair
def test_an_int16_overflow_names_the_same_tick_on_both_paths() -> None:
    """**R-V2.**  ``tick_item`` raises on the first offending *tick* and evaluates dY before dX
    inside it (the ``w1`` expression of A3 §4.2).  The vectorised path used to scan dX over the
    whole run first, so the same job reported a different tick and a different axis depending
    on which path had produced it - the one number an operator would use to find the contour.
    """

    def scalar(dx: list[int], dy: list[int]) -> str:
        with pytest.raises(ValueError) as exc:
            for x, y in zip(dx, dy, strict=True):
                tick_item(x, y, 0, 0)
        return str(exc.value)

    def vector(dx: list[int], dy: list[int]) -> str:
        with pytest.raises(ValueError) as exc:
            tick_words(dx, dy)
        return str(exc.value)

    cases = [
        ([40000], [-40000]),  # both axes bad in the same tick: dY is the one reported
        ([1, 1, 1, 1, 1, 40000], [1, 50000, 1, 1, 1, 1]),  # dY overflows four ticks earlier
        ([1, -40000, 1], [1, 1, 1]),
        ([0] * 300 + [33000], [0] * 301),  # past the scalar/vector switch
    ]
    for dx, dy in cases:
        assert vector(dx, dy) == scalar(dx, dy)


# ======================================================= 11 §5.1: the 300-word flush rule
_MODE_ITEM = [item_header(3002, 1), 5]


def _uniform_run(rng: random.Random, per_record: int, k: int) -> tuple[np.ndarray, np.ndarray]:
    words: list[int] = []
    for _ in range(k):
        words.append(item_header(OP_TICK, per_record - 1))
        words += [rng.getrandbits(32) for _ in range(per_record - 1)]
    laser = np.array([rng.random() < 0.3 for _ in range(k)], dtype=bool)
    return np.array(words, dtype=np.uint32), laser


@pytest.mark.parametrize("per_record", [1, 2, 3, 4, 7, 16, 99, 148, 149, 296, 297, 300])
def test_push_uniform_closes_frames_where_the_packer_does_for_every_record_width(
    per_record: int,
) -> None:
    """``tests/test_plan_vectorised_equivalence.py`` checks the 3-word tick with every record
    lit; the rule is ``PREFIX_WORDS + data >= FLUSH_WORDS`` after *any* record, so it has to
    hold for a record of any width and for a laser mask that is only partly set (the counts
    land in :attr:`PackedFrame.laser_items`, which is what a frame is audited by).
    """
    rng = random.Random(1000 + per_record)
    for _ in range(25):
        packer, stream = FramePacker(), FrameStream()
        streamed: list[object] = []
        for _step in range(rng.randint(1, 10)):
            if rng.random() < 0.25:
                packer.add_record([_MODE_ITEM], 0)
                streamed += stream.push_record([_MODE_ITEM], 0)
                continue
            k = rng.randint(0, 220)
            words, laser = _uniform_run(rng, per_record, k)
            for j in range(k):
                packer.add_record(
                    [words[j * per_record : (j + 1) * per_record].tolist()], int(laser[j])
                )
            streamed += list(stream.push_uniform(words, per_record, laser))
        packer.end_batch()
        streamed += stream.flush()
        assert streamed == packer.frames


def test_push_uniform_refuses_a_laser_mask_of_the_wrong_length() -> None:
    """**R-V3.**  One flag per record (PORT-PLAN §8.2).  A short mask used to raise
    ``IndexError`` out of the cumulative sum; a long one silently charged the frames a laser
    count taken from the wrong records."""
    words = np.array([item_header(OP_TICK, 2), 0, 0] * 5, dtype=np.uint32)
    for size in (0, 4, 6):
        with pytest.raises(ValueError, match="laser flags for 5 records"):
            list(FrameStream().push_uniform(words, 3, np.ones(size, dtype=bool)))
    assert len(list(FrameStream().push_uniform(words, 3, np.ones(5, dtype=bool)))) == 0


def test_every_frame_of_a_planned_job_holds_whole_items_and_obeys_the_flush_rule() -> None:
    """Frame grammar end to end (11 §5.1, §5.2): a frame never splits an item, every frame but
    the last is closed by the 300-word rule, and no vector the port would put on the wire is
    larger than one packet - a record group is appended whole, so a frame can overshoot 296
    data words by the longest record (the ZF epilogue's ``3001 109 2001 118`` = 11 words).
    """
    from nexcut.plan.__main__ import stream_job

    manu, hard, layer = machine_docs()
    frames = list(stream_job(job_doc(), manu, hard, layer, laser_records=True))
    assert len(frames) > 2
    for n, frame in enumerate(frames):
        i = 0
        while i < len(frame.data):
            header = frame.data[i]
            assert header & 0xFFFF in (3000, 3001, 3002, 9999, 103, 109, 118, 2001)
            i += 1 + (header >> 16) // 4
        assert i == len(frame.data), f"frame {n} ends inside an item"
        assert frame.byte_size <= MAX_FRAME_BYTES
        assert frame.count == 1 + len(frame.data)
        if n < len(frames) - 1:
            assert PREFIX_WORDS + len(frame.data) >= FLUSH_WORDS
            assert len(frame.data) <= FLUSH_WORDS - PREFIX_WORDS + 11


# ================================================= 11 §5.4: the record grammar of the stream
def _stream(laser_records: bool = True) -> tuple[JobStreamBuilder, JobFrameStream]:
    builder = JobStreamBuilder(
        config=StreamConfig(), quantizer=TickQuantizer(PPM), laser_records=laser_records
    )
    return builder, JobFrameStream(builder)


def _opcodes(frames: list) -> list[int]:
    out: list[int] = []
    for frame in frames:
        i = 0
        while i < len(frame.data):
            header = frame.data[i]
            out.append(header & 0xFFFF)
            i += 1 + (header >> 16) // 4
    return out


def test_a_record_queued_before_a_move_precedes_its_ticks() -> None:
    """**R-V1.**  :meth:`JobFrameStream.motion` is documented as the vectorised twin of
    :meth:`JobStreamBuilder.add_motion`, and the class drains the wrapped builder's record
    list "after every step" - so a caller may write the prologue and then ask for the move.
    It used to emit those records *after* the ticks: the ``3002`` mode, the gas and laser
    ``9999`` DO records, the ZF ``103`` cut-height move and its ``2001`` follow wait all
    landed behind the cut they have to precede (11 §5.4).  With the laser records kept, that
    is the laser being switched on after the contour has been cut.
    """
    points = np.column_stack((np.linspace(0.0, 5.0, 60), np.zeros(60)))
    laser = ContourLaser()

    scalar_builder = JobStreamBuilder(
        config=StreamConfig(), quantizer=TickQuantizer(PPM), laser_records=True
    )
    scalar_builder.prologue(laser)
    scalar_builder.add_motion(points)
    scalar_builder.finish()
    expect = scalar_builder.frames()

    builder, stream = _stream()
    builder.prologue(laser)
    got = list(stream.motion(points)) + list(stream.finish())
    assert _opcodes(got)[:8] == [3001, 3002, 3000, 3001, 9999, 103, 2001, 9999]
    assert got == expect


@pytest.mark.parametrize("fid", [504, 57, 633])
def test_a_leaked_vendor_frame_re_encodes_through_the_streaming_path(fid: int) -> None:
    """The leaked CO2 frames, byte for byte, out of the **production** path.

    ``tests/test_plan_items_golden.py`` re-encodes frames 0x1f8/0x39 (prologue) and 0x279
    (epilogue) through :class:`JobStreamBuilder` + :class:`~nexcut.mcc.fifo.FramePacker` - one
    ``Record`` and one ``Item`` per tick.  That is the *definition* path; what a job actually
    streams is :class:`JobFrameStream` + :meth:`~nexcut.mcc.fifo.FrameStream.push_uniform`, and
    until this test no vendor byte was ever compared against it.  The ticks around the control
    records come from the frame itself (the vendor's ramp depends on job geometry that is not
    recorded, A3 §8), fed through a quantiser that returns them unchanged - the record grammar,
    the automatic 3001 and the 300-word boundary are the stream's own.
    """
    text, items = FRAMES[fid]
    first = next(i for i, it in enumerate(items) if it.opcode != OP_TICK)
    last = max(i for i, it in enumerate(items) if it.opcode != OP_TICK)

    def ticks(part: list) -> tuple[list[int], list[int], list[int], list[int]]:
        cells = [it.tick for it in part]
        assert all(c is not None for c in cells)
        # a rapid tick carries freq 0 in the record; the packer turns it into 5000 (A3 §4.2)
        return (
            [c[0] for c in cells],
            [c[1] for c in cells],
            [0 if c[3] == 0 else c[2] for c in cells],
            [c[3] for c in cells],
        )

    head, tail = ticks(items[:first]), ticks(items[last + 1 :])

    class _Given(TickQuantizer):
        """A quantiser that hands back pulses already decoded from the frame."""

        def __init__(self, runs: list[tuple[list[int], list[int]]]) -> None:
            super().__init__(PPM)
            self.runs = runs

        def quantize(self, points_mm: object) -> tuple[list[int], list[int]]:
            return self.runs.pop(0)

    runs = [(head[0], head[1]), (tail[0], tail[1])]
    if fid != 633:  # the prologue frames close on ticks that belong to the next frame
        runs.append(([0] * 120, [0] * 120))
    builder = JobStreamBuilder(quantizer=_Given(runs), laser_records=True)
    stream = JobFrameStream(builder)

    def move(part: tuple[list[int], list[int], list[int], list[int]]) -> list:
        pts = np.zeros((len(part[0]) + 1, 2))
        return list(stream.motion(pts, np.array(part[2]), np.array(part[3])))

    frames = move(head)
    # CO2 layer 2 of the leaked job: HighAir DO3, CO2DOLaser DO9, LaserOnDelay 0, gas already on
    contour = ContourLaser(gas_port=3, laser_port=9)
    if fid == 633:
        builder.epilogue(contour)
    else:
        builder.prologue(contour)
    frames += move(tail)
    if fid != 633:
        frames += move(([0] * 120, [0] * 120, [5000] * 120, [4] * 120))
    frames += list(stream.finish())
    assert format_frame_line(fid, frames[0]) == text


def _job_shapes(rnd: random.Random) -> list[tuple[np.ndarray, object, object]]:
    """Moves a real job cannot produce on purpose but a damaged file can."""
    out = []
    for _ in range(rnd.randint(1, 5)):
        n = rnd.randint(2, 300)
        t = np.linspace(0.0, 1.0, n)
        kind = rnd.randrange(8)
        if kind == 0:
            pts = np.column_stack((9.0 * t + 0.3 * np.sin(13 * t), 4.0 * np.cos(7 * t)))
        elif kind == 1:  # reversal: out and back along the same line
            u = np.concatenate([t, t[::-1]])
            pts = np.column_stack((17.0 * u, np.zeros(len(u))))
        elif kind == 2:  # zero length: every sample on the same point
            pts = np.tile(np.array([3.25, -1.5]), (n, 1))
        elif kind == 3:  # sub-pulse: a thousandth of a pulse per tick
            step = 1.0 / PPM.x_per_mm / 1000.0
            pts = np.column_stack((np.arange(n) * step, np.zeros(n)))
        elif kind == 4:  # rapid at the int16 edge the packer can still hold
            step = 32760.0 / PPM.x_per_mm
            pts = np.column_stack((np.arange(n) * step, np.zeros(n)))
        elif kind == 5:  # one interval only
            pts = np.array([[0.0, 0.0], [rnd.uniform(-60, 60), rnd.uniform(-60, 60)]])
        elif kind == 6:  # a single point: no interval at all
            pts = np.array([[1.0, 2.0]])
        else:
            step = np.random.default_rng(rnd.randrange(1 << 30)).standard_normal((n, 2)) * 0.03
            pts = np.cumsum(step, axis=0)
        m = len(pts) - 1
        if m <= 0 or rnd.random() < 0.3:
            out.append((pts, None, None))
            continue
        if rnd.random() < 0.5:
            out.append(
                (
                    pts,
                    np.array([rnd.randrange(-3, 70000) for _ in range(m)]),
                    np.array([rnd.randrange(0, 300) for _ in range(m)]),
                )
            )
        else:
            out.append((pts, rnd.randrange(-2, 70000), rnd.randrange(0, 300)))
    return out


def test_the_streamed_job_is_the_scalar_job_over_adversarial_geometry() -> None:
    """200 random jobs of reversals, zero-length runs, sub-pulse moves, int16-edge rapids,
    single-interval and single-point contours, with per-tick and scalar PWM and both arming
    states - frame for frame, laser count for laser count, against the record path."""
    rnd = random.Random(0x5EED)
    ticks = 0
    for _ in range(200):
        moves = _job_shapes(rnd)
        laser_records = rnd.random() < 0.5
        contour = ContourLaser(
            gas_port=rnd.choice([0, 3]),
            laser_port=rnd.choice([0, 9]),
            pierce_dwell_ms=rnd.choice([0.0, 0.0, 7.0]),
            gas_delay_ms=rnd.choice([0.0, 100.0]),
            laser_off_before_ms=rnd.choice([0.0, 3.0]),
            laser_off_after_ms=rnd.choice([0.0, 2.0]),
        )
        scalar = JobStreamBuilder(
            config=StreamConfig(), quantizer=TickQuantizer(PPM), laser_records=laser_records
        )
        for pts, freq, duty in moves:
            if freq is None:
                scalar.add_motion(pts)
            else:
                scalar.add_contour(pts, freq, duty, contour)
        scalar.finish()
        expect = scalar.frames()

        builder, stream = _stream(laser_records)
        got: list = []
        for pts, freq, duty in moves:
            if freq is None:
                got += list(stream.motion(pts))
            else:
                got += list(stream.contour(pts, freq, duty, contour))
        got += list(stream.finish())
        assert got == expect
        assert stream.frames == len(expect)
        assert stream.ticks == total_ticks(expect)
        ticks += stream.ticks
    assert ticks > 20_000
