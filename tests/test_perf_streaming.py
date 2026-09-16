"""PORT-PLAN §8.3 streaming jitter gate - **simulator only**, never a real card.

The gate (R8, "GC/GIL jitter starving the FIFO"): while the process is busy, the producer
must keep the card FIFO above ``FifoAlarmNum`` = 30 items and must not stall - p99 frame
interval < 100 ms, max < 500 ms - over a 10-minute job, at a card tick of 250 us and of 1 ms.

What this file runs by default and why it is shorter than 10 minutes
--------------------------------------------------------------------
The gate has two knobs that fight each other: the job's *machine time* and the wall clock.
The frame cadence a real job needs is fixed by the tick period - one 297-word frame is 99
ticks, so 40.4 frames/s at 250 us and 10.1 frames/s at 1 ms - and this stack (safety strip +
CRC + UDP round trip + the simulator decoding every item) sustains about **660 frames/s** on
the development laptop, so a 10-minute job cannot be compressed by more than a few times
before the *simulator*, not the daemon, becomes the bottleneck; on a CI runner several times
slower, even less. The default run therefore keeps the frame cadence exactly realistic (no
compression) and shortens the job: ``NEXCUT_JITTER_SECONDS`` (default 8) is the job's machine
time in seconds, and the full 10-minute gate is one environment variable away::

    NEXCUT_JITTER_SECONDS=600 pytest -q -s tests/test_perf_streaming.py

Every run prints its measured numbers; the 10-minute numbers are recorded in
``docs/STATUS.md`` §0 (marker ``JITTER-GATE``).

Simulator caveat: ``fifo_starvation_alarm`` is off. The card reports "FIFO empty" at the
instant the last item is consumed, so ``0x67 <- [3]`` always arrives after the FIFO ran dry
and a card that alarmed there would fault at the end of every vendor cut; what a real card
does is UNVERIFIED (11 §7 step 8/9). The gate measures the queue depth instead, which is what
PORT-PLAN §8.3 asks for.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from conftest import speed_factor
from nexcut.mcc.fifo import PackedFrame, item_header
from nexcut.mcc.simulator import CardSimulator, SimConfig
from nexcut.mccd.daemon import Link, MccDaemon
from nexcut.mccd.ipc import MccdClient

FIFO_ALARM_NUM = 30
"""``ipAdd.ini FifoAlarmNum`` (01 §2.3): the queue-depth floor of the gate."""
P99_LIMIT_S = 0.100
MAX_LIMIT_S = 0.500


def limits_ms() -> tuple[float, float]:
    """``(p99, max)`` limits in ms, scaled to how fast this machine is running right now.

    The 100 ms / 500 ms of PORT-PLAN §8.3 describe a producer keeping up with a card on a
    machine of the review laptop's speed. A CI runner is slower and shares its cores, and the
    project's CI-stability rule is that a timing assertion must hold on a machine 3x slower:
    an unscaled percentile there measures the runner's scheduler, not the port. Only the two
    *time* limits are scaled (:func:`tests.conftest.speed_factor`, never below 1.0). The
    criteria that say the card was actually fed - DONE, every tick consumed, no re-send, no
    starvation alarm and a queue that never fell below ``FifoAlarmNum`` - are asserted
    unscaled, because they are facts about the stream and not about the clock. The unscaled
    10-minute numbers are in ``docs/STATUS.md`` §0.
    """
    f = speed_factor()
    return 1000 * P99_LIMIT_S * f, 1000 * MAX_LIMIT_S * f
TICKS_PER_FRAME = 99
"""297 data words / 3 words per tick: the 300-word flush rule (11 §5.1)."""


def job_seconds() -> float:
    """Machine time of the gate job; ``NEXCUT_JITTER_SECONDS`` (default 8 s, gate asks 600)."""
    try:
        return max(1.0, float(os.environ.get("NEXCUT_JITTER_SECONDS", "8")))
    except ValueError:  # pragma: no cover - operator typo
        return 8.0


def tick_frame() -> PackedFrame:
    """One full frame of 99 dry-run ticks (duty 0, 11 §5.2)."""
    data: tuple[int, ...] = ()
    for _ in range(TICKS_PER_FRAME):
        data += (item_header(3000, 2), 0x00010001, 5000 << 16)
    return PackedFrame(data, 0, TICKS_PER_FRAME)


class _Load:
    """A second thread that makes the process busy: CPU, allocation and GC pressure.

    PORT-PLAN §8.3 says "while the UI is stress-rendering a 50 k-entity drawing"; the daemon
    test process has no Qt, so this is the same kind of load without it - a 50 k-point array
    transformed in a loop plus a churn of short-lived objects.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="jitter-load", daemon=True)

    def __enter__(self) -> _Load:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=10.0)

    def _run(self) -> None:
        pts = np.random.default_rng(7).random((50_000, 2))
        junk: list[list[int]] = []
        while not self._stop.is_set():
            np.hypot(pts[:, 0] - 0.5, pts[:, 1] - 0.5).sum()
            junk.append(list(range(500)))
            if len(junk) > 400:
                junk.clear()


@dataclass(slots=True)
class GateResult:
    """Measured numbers of one gate run."""

    tick_us: float
    cadence_ms: float
    """Machine time of one frame = 99 ticks: how often the card *demands* a frame."""
    job_seconds: float
    frames: int
    state: str
    error: str | None
    wall_s: float
    card_low_water_items: int
    pc_low_water_items: int
    starvation_events: int
    resends: int
    ticks_consumed: int
    p50_ms: float
    p90_ms: float
    p99_ms: float
    max_ms: float

    def line(self) -> str:
        """One line for the test output / docs/STATUS.md §0."""
        return (
            f"jitter gate tick={self.tick_us:g} us (cadence {self.cadence_ms:.1f} ms/frame) "
            f"job={self.job_seconds:g} s machine time "
            f"({self.frames} frames, {self.wall_s:.1f} s wall): {self.state}"
            f"{'' if self.error is None else ' ' + self.error}; queue low water "
            f"{self.card_low_water_items} items (card) / {self.pc_low_water_items} (reg 1016); "
            f"starvation {self.starvation_events}; resends {self.resends}; frame interval "
            f"p50 {self.p50_ms:.1f} ms p90 {self.p90_ms:.1f} p99 {self.p99_ms:.1f} "
            f"max {self.max_ms:.1f}"
        )


def run_gate(tick_s: float, seconds: float) -> GateResult:
    """Stream a job of ``seconds`` machine time against the simulator while the process is busy."""
    frames = max(4, int(round(seconds / (TICKS_PER_FRAME * tick_s))))
    frame = tick_frame()
    sim = CardSimulator(config=SimConfig(tick_s=tick_s, fifo_starvation_alarm=False)).start()
    d = Path(tempfile.mkdtemp(prefix="nxgate"))
    daemon = MccDaemon(card_addr=sim.address, socket_path=d / "mccd.sock").start()
    t0 = time.monotonic()
    try:
        assert daemon.wait_for_link(Link.CONNECTED, 15.0), daemon.last_error
        # A link is not enough: 'start_job' goes through _require_ready, which needs one axis
        # poll (2000/50, ~90 ms) to have landed. On a busy machine the first poll can be later
        # than the link, and the job was then refused with "busy: axis status not read yet".
        deadline = time.monotonic() + 15.0
        while daemon.axis_ro is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert daemon.axis_ro is not None, "no axis status poll within 15 s"
        with MccdClient(daemon.socket_path, timeout=60.0) as client, _Load():
            client.call("arm_motion")
            feeder = daemon.load_job_frames(
                (frame for _ in range(frames)), name="jitter gate", total_frames=frames
            )
            client.call("start_job", token=feeder.token)
            sim.reset_flow_stats()
            low = 10**9
            js: dict[str, Any] = {}
            deadline = time.monotonic() + seconds * 4 + 120.0
            while time.monotonic() < deadline:
                js = client.call("job_status")
                if js["stats"]["frames_sent"] < frames:
                    # Only while frames are still going out: the end-of-job drain empties the
                    # queue by design and would otherwise dominate the low-water mark.
                    seen = sim.queue_low_water
                    if seen is not None:
                        low = min(low, seen)
                if js["state"] in ("DONE", "STOPPED", "FAILED"):
                    break
                time.sleep(0.05)
            wall = time.monotonic() - t0
            st = js["stats"]
            iv = st["frame_interval_s"]
            snap = sim.snapshot()
            return GateResult(
                tick_us=tick_s * 1e6,
                cadence_ms=1000 * TICKS_PER_FRAME * tick_s,
                job_seconds=seconds,
                frames=frames,
                state=js["state"],
                error=js["error"],
                wall_s=wall,
                card_low_water_items=low if low < 10**9 else -1,
                pc_low_water_items=-1 if st["min_queue_items"] is None else st["min_queue_items"],
                starvation_events=st["starvation_events"],
                resends=st["resends"],
                ticks_consumed=snap["ticks_consumed"],
                p50_ms=1000 * iv["p50"],
                p90_ms=1000 * iv["p90"],
                p99_ms=1000 * iv["p99"],
                max_ms=1000 * iv["max"],
            )
    finally:
        daemon.close()
        sim.stop()
        shutil.rmtree(d, ignore_errors=True)


def check(res: GateResult) -> None:
    """The PORT-PLAN §8.3 criteria.

    The interval bounds measure a *producer stall*, so they are read against the cadence the
    card demands: a frame is 99 ticks, i.e. 24.75 ms of machine time at a 250 us tick but
    99 ms at a 1 ms tick, and a producer that is never late still shows ~99 ms intervals
    there. "p99 frame interval < 100 ms" therefore becomes "the producer adds less than
    100 ms to the cadence" (and 500 ms for the worst case). Where the cadence is small
    enough for PORT-PLAN's absolute numbers to mean what they say - the 250 us tick, the
    real card period - they are asserted literally as well.
    """
    p99_limit, max_limit = limits_ms()
    print(f"\n{res.line()}; limits p99 < {p99_limit:.0f} ms, max < {max_limit:.0f} ms")
    # -- the stream itself: never scaled
    assert res.state == "DONE", res.error
    assert res.ticks_consumed == res.frames * TICKS_PER_FRAME
    assert res.resends == 0
    assert res.starvation_events == 0, res.line()
    assert res.card_low_water_items >= FIFO_ALARM_NUM, res.line()
    assert res.pc_low_water_items >= FIFO_ALARM_NUM, res.line()
    # -- the producer stall: scaled to this machine
    assert res.p99_ms - res.cadence_ms < p99_limit, res.line()
    assert res.max_ms - res.cadence_ms < max_limit, res.line()
    if res.cadence_ms <= 50:
        assert res.p99_ms < p99_limit, res.line()
        assert res.max_ms < max_limit, res.line()


@pytest.mark.parametrize("tick_us", [250, 1000])
def test_streaming_jitter_gate(tick_us: int) -> None:
    """PORT-PLAN §8.3, simulator only: queue never below 30 items, p99 < 100 ms, max < 500 ms.

    Default job length is ``NEXCUT_JITTER_SECONDS`` (8 s of machine time); the gate's
    10-minute job is the same test with ``NEXCUT_JITTER_SECONDS=600`` (see the module
    docstring and docs/STATUS.md §0).
    """
    check(run_gate(tick_us / 1e6, job_seconds()))
