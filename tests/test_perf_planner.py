"""PORT-PLAN §8.3 planner throughput gate: a 100 000-contour job must plan in < 30 s.

**Result on this laptop (2026-09-16, after STATUS §5 task 6): still not met, by ~7x** - but the
job now *streams*, which was the other half of the problem, and it is 2.4x faster end to end
(5.4x on the item path this task owns). The gate stays a strict xfail with the new numbers;
what changed and what is left:

* The test plans a synthetic job of ``NEXCUT_PLAN_CONTOURS`` (default 250) one-segment contours
  on a grid and extrapolates linearly to 100 000. Cost is linear in the number of *ticks*, and
  the tick count per contour is a property of the geometry, not of the job size, so the
  extrapolation is sound (675 ticks per contour at every job size measured).
* **Before** the vectorised path, as ``docs/STATUS.md`` §0 recorded it: 4.61 ms/contour,
  **6.82 µs/tick** -> 461 s; re-measured on this laptop through the old scalar record path that
  ``JobStreamBuilder.frames`` still is, 5.2 ms/contour (``speed_factor``-normalised) -> 520 s.
  The frames of such a job (218 M words) could not be held in memory at all.
* **After**: ``stream_job`` **yields** frames (``plan/__main__``), the tick -> item -> word ->
  frame path is numpy array work (``plan.items.carry_cells`` / ``tick_words`` +
  ``mcc.fifo.FrameStream.push_uniform``), and nothing accumulates. Measured on the same laptop:
  **2.0-2.3 ms/contour, 3.0-3.4 µs/tick -> 196-232 s** once the CPU is warm (a cold run on the
  schedutil governor reads up to 2.8 ms/contour = 280 s, which is the runner and not the code).
  That is **2.4x** end to end; the item path alone went from 4.07 to 0.75 ms/contour (**5.4x**),
  and the geometry stages are untouched apart from two small sampler changes.
* **What still dominates**, measured per stage at 250 contours on the idle laptop
  (ms/contour -> projected seconds for 100 000):

  | stage | ms/contour | 100 k |
  |---|---|---|
  | rapid plan (``plan_line`` + ``sample_plan`` for the inter-contour move) | 0.55 | 55 s |
  | contour stream (quantiser + tick words + frame packing) | 0.47 | 47 s |
  | ``lookahead.plan_velocity`` (junction + S-curve) | 0.30 | 30 s |
  | rapid stream | 0.25 | 25 s |
  | ``sampler.sample_plan`` | 0.18 | 18 s |
  | ``contour_fit.process``, PWM, geometry, params, glyphs | 0.24 | 24 s |

  Every one of those is now *per-contour numpy call overhead*, not per-tick Python work: a
  one-segment contour is ~675 ticks and each stage makes tens of small array calls on it, each
  ~1-3 µs whatever its length. The next factor therefore has to come from planning **many
  contours in one array pass** - which means ``plan/lookahead.py``, ``plan/junction.py`` and
  ``plan/scurve.py`` (the rapid plan and ``plan_velocity`` together are 85 of the 212 s), files
  this task does not own - and not from tuning the item path further.

Nothing here touches a card: the planner writes no socket (``nexcut.plan.__main__``).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from nexcut.io.params import ParamDocument, default_document
from nexcut.model.glyph import SegmentGlyph, Vec2
from nexcut.model.graph import ChfDocument, Contour, ContourElement
from nexcut.plan.__main__ import JobResult, build_job, stream_job

GATE_CONTOURS = 100_000
"""PORT-PLAN §8.3: "a 100 k-contour synthetic DXF plans in < 30 s"."""
GATE_SECONDS = 30.0
MEMORY_BOUND_MB = 64.0
"""Peak-RSS growth a *streamed* job of any length is allowed (see the memory test)."""


def sample_contours() -> int:
    """How many contours to plan for the measurement (``NEXCUT_PLAN_CONTOURS``, default 250)."""
    try:
        return max(20, int(os.environ.get("NEXCUT_PLAN_CONTOURS", "250")))
    except ValueError:  # pragma: no cover - operator typo
        return 250


def memory_contours() -> int:
    """Contours for the memory test (``NEXCUT_PLAN_MEM_CONTOURS``, default 300).

    The default keeps the test a few seconds long on a slow runner; the 100 000-contour run the
    module docstring quotes is the same code with this variable set.
    """
    try:
        return max(20, int(os.environ.get("NEXCUT_PLAN_MEM_CONTOURS", "300")))
    except ValueError:  # pragma: no cover - operator typo
        return 300


def _put(doc: ParamDocument, key: str, value: float | int | str) -> None:
    elem, attr = key.split(".", 1)
    group = next(g for g, e in doc.values.items() if elem in e and attr in e[elem])
    doc.set(group, elem, attr, value)


def _documents() -> tuple[ParamDocument, ParamDocument, ParamDocument]:
    """A CO2 machine like this CF1390 (01 §2.2, 02 §2.3), with the height follower off."""
    manu, hard, layer = (default_document(k) for k in ("manu", "hard", "layer"))
    _put(manu, "SP.m_iEnableLaserType", 1)
    for key, val in {
        "ZF.ZFType": 0, "LGP.CO2DOLaser": 9, "LGP.CO2LaserControlType": 2, "MGP.HighAir": 3,
        "MAC.SpeedRatio": 31.003, "MAC.WritePluse": 8000, "MAC_1.SpeedRatio": 31.009,
        "MAC_1.WritePluse": 8000, "AX.InterpolationCycle": 250,
    }.items():  # fmt: skip
        _put(hard, key, val)
    group = next(g for g in layer.values if g.endswith("CO2LayerParam2"))
    for attr, val in {"CutSpeed": 100.0, "CutDuty": 4, "CutFreq": 5000, "CutGasType": 3}.items():
        layer.set(group, "GP", attr, val)
    return manu, hard, layer


def synthetic_job(n: int) -> ChfDocument:
    """``n`` one-segment 1 mm contours on a 1.2 mm grid: the cheapest realistic contour."""
    graphs = []
    for i in range(n):
        x, y = (i % 300) * 1.2, (i // 300) * 1.2
        graphs.append(
            Contour(elements=[ContourElement(SegmentGlyph(Vec2(x, y), Vec2(x + 1.0, y)))], layer=1)
        )
    return ChfDocument(graphs=graphs)


def measure(n: int) -> tuple[float, JobResult]:
    """Plan ``n`` contours the way production does - streaming, keeping no frame.

    Returns the wall time and the statistics; the frames are counted and dropped, which is what
    :class:`nexcut.mccd.feeder.JobFeeder` and the ``nexcut-plan`` CLI do with them.
    """
    doc = synthetic_job(n)
    manu, hard, layer = _documents()
    stats = JobResult()
    t = time.perf_counter()
    for _frame in stream_job(doc, manu, hard, layer, stats=stats):
        pass
    return time.perf_counter() - t, stats


_MEMORY_PROBE = textwrap.dedent(
    """
    import gc, json, sys
    sys.path.insert(0, sys.argv[2])
    from test_perf_planner import synthetic_job, _documents
    from nexcut.plan.__main__ import JobResult, build_job, stream_job

    def rss_mb():
        with open("/proc/self/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
        return 0.0

    n = int(sys.argv[1])
    doc = synthetic_job(n)
    manu, hard, layer = _documents()
    list(stream_job(synthetic_job(4), manu, hard, layer))   # warm numpy up
    gc.collect()
    base = peak = rss_mb()
    stats, words = JobResult(), 0
    for i, frame in enumerate(stream_job(doc, manu, hard, layer, stats=stats)):
        words += len(frame.data)
        if not i % 128:                                     # sample while the job streams past
            peak = max(peak, rss_mb())
    streamed = max(peak, rss_mb()) - base
    job = build_job(doc, manu, hard, layer)                 # the same job, all of it in memory
    print(json.dumps({
        "contours": stats.contours, "ticks": stats.ticks, "frames": stats.frame_count,
        "words": words, "document_mb": base, "streamed_mb": streamed,
        "materialised_mb": rss_mb() - base, "frames_built": len(job.frames),
    }))
    """
)
"""Measured in a fresh interpreter on purpose.

RSS is a property of the *process*: inside the full suite the allocator is already holding
arenas that earlier tests freed, so both a streaming job and a materialised one can be served
without the resident size moving at all, and every delta reads as zero.  A subprocess starts
with a clean heap, which is the only way to see what a job actually costs.
"""


def _memory_probe(contours: int) -> dict[str, float]:
    """Run :data:`_MEMORY_PROBE` for ``contours`` contours and return its numbers."""
    out = subprocess.run(
        [sys.executable, "-c", _MEMORY_PROBE, str(contours), str(Path(__file__).parent)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_planner_is_linear_in_the_contour_count() -> None:
    """The extrapolation the gate below relies on: cost per contour is flat with job size."""
    n = sample_contours()
    small, _ = measure(n // 4)
    large, job = measure(n)
    per_small, per_large = small / (n // 4), large / n
    print(
        f"\nplanner: {per_small * 1e3:.2f} ms/contour at {n // 4}, "
        f"{per_large * 1e3:.2f} ms/contour at {n}, {job.ticks / n:.0f} ticks/contour"
    )
    assert job.contours == n and not job.skipped
    # Generous: the point is that it does not grow super-linearly, not the exact ratio.
    assert per_large < per_small * 3.0


def test_streaming_and_materialising_plan_the_same_job() -> None:
    """The gate measures ``stream_job``; ``build_job`` is the same work with the frames kept."""
    doc = synthetic_job(30)
    manu, hard, layer = _documents()
    stats = JobResult()
    streamed = list(stream_job(doc, manu, hard, layer, stats=stats))
    job = build_job(doc, manu, hard, layer)
    assert streamed == job.frames
    assert (stats.contours, stats.ticks) == (job.contours, job.ticks)


def test_a_streamed_job_does_not_grow_with_its_length() -> None:
    """A production job must not have to fit in memory as frames (PORT-PLAN §8.3, STATUS §5.6).

    Measured 2026-09-16 on the review laptop, the whole 100 000-contour job planned by
    ``stream_job`` and written by ``write_frame_file`` to ``/dev/null`` in one process:
    **72.0 M ticks, 733 756 frames, 218 M words** at a **peak RSS of 140.3 MB**, of which
    135.3 MB is the ``.chf`` document itself (100 000 contour objects, built before the timer
    starts) - the planning and writing added **4.9 MB**, and it does not grow with the job.
    The same job as a list of :class:`~nexcut.mcc.fifo.PackedFrame` would be ~8 GB of Python
    ints, which is what ``build_job`` used to demand.

    Here the same measurement runs at ``memory_contours()`` contours (``NEXCUT_PLAN_MEM_CONTOURS``
    reproduces the 100 000 run) and is compared against materialising that job, which is the
    behaviour this task replaced.
    """
    if not Path("/proc/self/status").exists():  # pragma: no cover - not Linux
        pytest.skip("RSS is read from /proc/self/status")
    n = memory_contours()
    probe = _memory_probe(n)
    print(
        f"\nmemory: {probe['contours']} contours, {probe['ticks']} ticks, "
        f"{probe['frames']} frames, {probe['words']} words -> streaming peak "
        f"+{probe['streamed_mb']:.1f} MB RSS, the frame list holds "
        f"+{probe['materialised_mb']:.1f} MB (document {probe['document_mb']:.0f} MB, "
        f"bound {MEMORY_BOUND_MB:g} MB)"
    )
    assert probe["contours"] == n and probe["frames_built"] == probe["frames"]
    assert probe["streamed_mb"] < MEMORY_BOUND_MB, (
        f"a streamed job grew the RSS by {probe['streamed_mb']:.1f} MB at {n} contours; "
        "the whole point is that it does not grow with the job"
    )
    # The frame list is ~36 bytes per word of live Python objects; streaming must be far below
    # it, and the gap widens with every contour (at 100 000 it is gigabytes against ~1 MB).
    assert probe["materialised_mb"] > 2.0 * max(probe["streamed_mb"], 1.0), (
        f"streaming {probe['streamed_mb']:.1f} MB against {probe['materialised_mb']:.1f} MB "
        "materialised: no memory was saved"
    )


@pytest.mark.xfail(
    strict=True,
    reason="PORT-PLAN §8.3 planner gate is not met: ~200-280 s projected for 100 k contours "
    "(measured 2026-09-16 after the streaming/vectorised item path; it was 461-520 s before). "
    "What is left is per-contour numpy call overhead in the geometry stages - the rapid plan, "
    "plan_velocity and sample_plan - which needs contours planned in batches, not a faster item "
    "path. See the module docstring for the per-stage numbers.",
)
def test_100k_contour_job_plans_in_under_30_s() -> None:
    """PORT-PLAN §8.3 planner throughput gate (measured by extrapolation, see the module docstring)."""
    n = sample_contours()
    seconds, job = measure(n)
    per_contour = seconds / n
    projected = per_contour * GATE_CONTOURS
    print(
        f"\nplanner gate: {n} contours in {seconds:.2f} s "
        f"({per_contour * 1e3:.2f} ms/contour, {job.ticks} ticks, "
        f"{seconds / max(1, job.ticks) * 1e6:.2f} us/tick) -> "
        f"{GATE_CONTOURS} contours in {projected:.0f} s (budget {GATE_SECONDS:g} s)"
    )
    assert projected < GATE_SECONDS, (
        f"{projected:.0f} s projected for {GATE_CONTOURS} contours, budget {GATE_SECONDS:g} s"
    )
