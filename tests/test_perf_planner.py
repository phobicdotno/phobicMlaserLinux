"""PORT-PLAN §8.3 planner throughput gate: a 100 000-contour job must plan in < 30 s.

**Result on this laptop (2026-09-16): not met, by a factor of ~16.** The gate is recorded as
a strict xfail below, in the same way as the M2 open-file budgets X11/X12, with the measured
numbers and what dominates:

* The test plans a synthetic job of ``NEXCUT_PLAN_CONTOURS`` (default 250) one-segment
  contours on a grid and extrapolates linearly to 100 000. Cost is linear in the number of
  *ticks*, and the tick count per contour is a property of the geometry, not of the job size,
  so the extrapolation is sound (measured 2026-09-16 at 62 / 250 / 1000 contours:
  5.00 / 4.78 / 5.07 ms per contour, 675 ticks per contour throughout).
* Measured: **4.8-5.1 ms per contour** end to end -> **478-507 s** for 100 000 contours,
  16x over the 30 s budget.
* What dominates: the per-tick item and frame construction in ``plan/items.py`` +
  ``mcc/fifo.py`` - one ``Record``, one ``Item``, one word list and one
  ``FramePacker.add_record`` call per 250 us tick, **7.08 us/tick** measured (67.5 M ticks
  for this job: 100 000 contours of a 1 mm cut at 100 mm/s are ~675 ticks each once the
  jerk-limited profile, the inter-contour rapid and the pierce dwell are counted; that is
  4.7 hours of machine time). Geometry alone (``contour_fit`` + ``lookahead`` + ``sampler``
  + PWM, all numpy but with ~40 small numpy calls per contour) is 1.35 ms/contour = ~135 s
  for 100 000 - also over the 30 s budget on its own.
* A second, harder limit: the frames of such a job cannot be held in memory at all. 67.5 M
  ticks are 202 M words; as ``PackedFrame`` tuples of Python ints that is several GB. The
  fix is therefore not a constant factor but a **streaming planner**: a generator of frames
  feeding :class:`nexcut.mccd.feeder.JobFeeder` (which already consumes one), with the tick
  -> item -> word path vectorised in numpy. ``plan/items.py`` is outside the scope of this
  task, so this file measures and records rather than fixes.

Nothing here touches a card: the planner writes no socket (``nexcut.plan.__main__``).
"""

from __future__ import annotations

import os
import time

import pytest

from nexcut.io.params import ParamDocument, default_document
from nexcut.model.glyph import SegmentGlyph, Vec2
from nexcut.model.graph import ChfDocument, Contour, ContourElement
from nexcut.plan.__main__ import JobResult, build_job

GATE_CONTOURS = 100_000
"""PORT-PLAN §8.3: "a 100 k-contour synthetic DXF plans in < 30 s"."""
GATE_SECONDS = 30.0


def sample_contours() -> int:
    """How many contours to plan for the measurement (``NEXCUT_PLAN_CONTOURS``, default 250)."""
    try:
        return max(20, int(os.environ.get("NEXCUT_PLAN_CONTOURS", "250")))
    except ValueError:  # pragma: no cover - operator typo
        return 250


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
    """Plan ``n`` contours; returns the wall time and the result."""
    doc = synthetic_job(n)
    manu, hard, layer = _documents()
    t = time.perf_counter()
    job = build_job(doc, manu, hard, layer)
    return time.perf_counter() - t, job


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


@pytest.mark.xfail(
    strict=True,
    reason="PORT-PLAN §8.3 planner gate is not met: 478-507 s for 100 k contours (measured "
    "2026-09-16), dominated by the per-tick item/frame construction in plan/items.py "
    "(7.08 us/tick). See the module docstring; fixing it needs a streaming, numpy-vectorised "
    "item path - a job that size cannot be materialised as frames in memory either.",
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
