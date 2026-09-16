"""Native FIFO job streaming for ``nexcut-mccd`` (11 §5, A3 §7, PORT-PLAN §8.2/§8.3).

One :class:`JobFeeder` owns one job.  It pulls packed frames from a *source* (a lazy
iterator, so a planned job never has to fit in memory at once), keeps the card FIFO full
through :class:`nexcut.mcc.fifo.FifoFeeder` and stops the program cleanly when the job ends,
is paused, is stopped, or anything goes wrong.

Card protocol (vendor ``fillFifo``, VM slot 122 ``0x10052390``, A3 §7):

* ``0x67 <- [1]`` clears the FIFO before the first frame (a stale program from an aborted
  job must never be restarted, 04 §3.5; UNVERIFIED that ``[1]`` discards queued frames);
* frames go to ``0x66`` with ``frame_id = reg 1015 + 1``, +1 per frame, at most 50 per fill
  pass, and only while ``frame_bytes + 2000 <= reg 1016`` (11 C2 byte accounting).  Two
  details follow from this feeder reading the daemon's 30 ms status poll instead of doing its
  own read the way the vendor does: the frame id is seeded from reg 1015 once and then
  carried by the PC (re-deriving it from a stale reg 1015 would re-use ids, and the card
  acknowledges a repeated id without queuing it, 08 §4.4), and the bytes of frames sent since
  that poll are subtracted from reg 1016 before the space test (otherwise a fill would
  over-fill the card by a whole poll period's worth of frames);
* ``0x67 <- [2]`` starts the program once the first batch is in the card - the first fill uses
  a status poll taken *after* the clear, and a job whose first fill put nothing into the card
  fails instead of starting an empty program (that would be the port starving its own card);
* the job is over when the last frame was sent and reg 1016 is back to 60000 (``FIFO_MARGIN_EMPTY``,
  ``cmp [esi+0xd0],0xea60`` @``0x10057f2d``); the feeder then sends ``0x67 <- [3]`` and ``0x67 <- [1]``.

Port deviations, all safety-driven (PORT-PLAN §8.2):

* **Dry run only.**  Laser records are stripped from every frame *before* it is queued
  (:func:`nexcut.mcc.safety.strip_laser_records`: tick duty forced to 0, laser DO bits and
  ``9999[3/0x11]`` PWM records removed) and the gate strips them again on the way out,
  because the gate - not this module - is the enforcement point.  Streaming is refused
  outright while the machine is ``LASER_ARMED`` (docs/DECISIONS.md D5: laser arming has no
  IPC path in this phase and needs its own decision entry before M5).
* **Unacknowledged frame**: the vendor drops a frame whose retries failed and streams a hole
  into the path.  Here every frame stays in a window until reg 1015 reports its id or a later
  one; a frame that is still in the window ``unacked_timeout_s`` after it was sent is re-sent
  (the card acknowledges a repeated frame id without queuing it twice, 08 §4.4) at most
  ``max_resends`` times, then the job is aborted.  The timeout is measured from the *oldest*
  unacknowledged frame, so later fill passes cannot re-arm it and hide a stalled reg 1015.
  A transport failure raises :class:`~nexcut.mcc.fifo.FifoFrameLost` and aborts at once.
* **Starvation**: reg 1016 below ``FifoAlarmNum`` items (``ipAdd.ini FifoAlarmNum = 30``,
  01 §2.3 ``RegName101``) while frames are still pending is counted and logged; the card's own
  alarm (reg 1007 bit 5 ``EtherCATErrorInfo_2_05``, A2 §2.4) is a hard error - and the safety
  gate reacts to it on its own status read with stop + disarm.
* **Card abort**: reg 1019 losing the "FIFO program running" bit while frames are still to
  come (A2 §2 row 19) fails the job instead of leaving the feeder topping up a frozen card.
* **Over-long frame**: a frame that does not fit the card's 1448-byte datagram (04 §3.2) is
  refused when the job is read, not halfway through it.
* **Clean stop**: ``0x67 <- [3]`` then ``0x67 <- [1]`` (FIFO cleared) *before* anything disarms,
  so no queued program survives the job.  A stop or a watchdog trip that disarms first only
  gets the ``[3]`` (``[1]`` needs ``MOTION_ARMED``); the next job's mandatory clear removes
  the leftovers.

Nothing here decides *what* is streamed: the frames come from :mod:`nexcut.plan`.  Nothing
here talks to a socket either - every write goes through the daemon's gate.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nexcut.mcc import commands as C
from nexcut.mcc.fifo import (
    MAX_FRAMES_PER_FILL,
    PREFIX_WORDS,
    FifoFeeder,
    FifoFrameLost,
    PackedFrame,
    fifo_queued_items,
)
from nexcut.mcc.framing import MAX_FRAME_LEN, REG_FIFO_DATA
from nexcut.mcc.registers import (
    FIFO_MARGIN_EMPTY,
    Alarm2,
    Status,
    fifo_program_running,
    next_frame_id,
)
from nexcut.mcc.safety import ArmState, SafetyConfig, SafetyViolation, strip_laser_records

__all__ = [
    "OP_TICK",
    "FeedStats",
    "FeederConfig",
    "JobError",
    "JobFeeder",
    "JobState",
    "frames_from_words",
    "scan_items",
]

log = logging.getLogger("nexcut.mccd.feeder")

OP_TICK = 3000
"""Interpolation tick opcode (11 §5.2); one per card cycle."""
_DATAGRAM_OVERHEAD_BYTES = 14
"""Bytes ``framing.encode_vector`` adds around the vector words (a 298-word frame is the
1206-byte datagram of 04 §3.7)."""

StatusFn = Callable[[], tuple[Sequence[int], float] | None]
"""Returns the newest block 1000/36 and the monotonic time it was read, or None."""


class JobState(StrEnum):
    """Life cycle of one loaded job."""

    LOADED = "LOADED"
    """Frames accepted, token issued, nothing sent."""
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    DONE = "DONE"
    """The card drained the last frame."""
    STOPPED = "STOPPED"
    """Stopped by the operator."""
    FAILED = "FAILED"


_FINAL = frozenset({JobState.DONE, JobState.STOPPED, JobState.FAILED})


class JobError(RuntimeError):
    """The job cannot run (or cannot continue)."""


class _Stopped(Exception):
    """Internal: :meth:`JobFeeder.request_stop` was called."""


@dataclass(frozen=True, slots=True)
class FeederConfig:
    """Streaming knobs.  Values without a doc reference are port choices (UNVERIFIED)."""

    alarm_items: int = 30
    """``ipAdd.ini FifoAlarmNum`` (01 §2.3): queue depth below this is starvation."""
    poll_period_s: float = 0.005
    """How often the feeder looks for a newer status poll.  Port choice, well inside the
    30 ms ``MCCore`` poll period it consumes (04 §1)."""
    buffer_frames: int = 128
    """Frames pulled from the source into the ring ahead of the card.  Port choice: >= one
    fill pass (50 frames, A3 §7) so a fill is never source-limited, small enough that a job
    of any length streams in constant memory."""
    unacked_timeout_s: float = 0.6
    """``ipAdd.ini FifoTimeout`` = 600 ms (01 §2.3): reg 1015 must catch up within this."""
    max_resends: int = 2
    """Re-sends of one unacknowledged frame before the job is aborted.  Port choice
    (the vendor drops the frame instead, A3 §7)."""
    max_frames_per_fill: int = MAX_FRAMES_PER_FILL
    drain_timeout_s: float = 30.0
    """After the last frame: how long the card may take to report an empty FIFO.  Port choice."""
    status_timeout_s: float = 5.0
    """How long a fresh block-1000 poll may take before the job fails.  Port choice: the
    daemon polls every ``MCCore`` = 30 ms and its watchdog calls the link dead after 1 s
    (PORT-PLAN §8.2), so this only has to be comfortably longer than that."""


@dataclass(slots=True)
class FeedStats:
    """What the jitter gate of PORT-PLAN §8.3 measures (simulator only, see the tests)."""

    frames_sent: int = 0
    items_sent: int = 0
    ticks_sent: int = 0
    frames_loaded: int = 0
    resends: int = 0
    starvation_events: int = 0
    """Status polls that showed fewer than ``alarm_items`` items queued while frames were
    still pending."""
    drain_starvation_alarm: bool = False
    """The card raised reg 1007 bit 5 while the last frame drained (expected; see
    :meth:`JobFeeder._note_depth`)."""
    min_queue_items: int | None = None
    """Lowest queue depth (items, estimated from reg 1016) seen while frames were pending."""
    min_margin: int | None = None
    """Lowest reg 1016 seen."""
    polls: int = 0
    send_times: list[float] = field(default_factory=list)
    """``time.monotonic()`` of every frame write that the card acknowledged."""

    def intervals(self) -> list[float]:
        """Gaps between consecutive frame writes, in seconds."""
        t = self.send_times
        return [b - a for a, b in zip(t, t[1:], strict=False)]

    def interval_stats(self) -> dict[str, float]:
        """``count/p50/p90/p99/max`` of :meth:`intervals` (empty job -> zeros)."""
        gaps = sorted(self.intervals())
        if not gaps:
            return {"count": 0.0, "p50": 0.0, "p90": 0.0, "p99": 0.0, "max": 0.0}

        def q(p: float) -> float:
            return gaps[min(len(gaps) - 1, int(p * len(gaps)))]

        return {
            "count": float(len(gaps)),
            "p50": q(0.50),
            "p90": q(0.90),
            "p99": q(0.99),
            "max": gaps[-1],
        }

    def to_json(self) -> dict[str, Any]:
        """JSON-serialisable summary (the ``send_times`` list is replaced by its quantiles)."""
        out: dict[str, Any] = {
            "frames_sent": self.frames_sent,
            "frames_loaded": self.frames_loaded,
            "items_sent": self.items_sent,
            "ticks_sent": self.ticks_sent,
            "resends": self.resends,
            "starvation_events": self.starvation_events,
            "drain_starvation_alarm": self.drain_starvation_alarm,
            "min_queue_items": self.min_queue_items,
            "min_margin": self.min_margin,
            "polls": self.polls,
        }
        out["frame_interval_s"] = self.interval_stats()
        return out


def scan_items(data: Sequence[int]) -> tuple[int, int]:
    """``(items, ticks)`` of one frame's item words (header scan, 11 §5.2)."""
    items = ticks = 0
    i = 0
    n = len(data)
    while i < n:
        h = int(data[i]) & 0xFFFFFFFF
        items += 1
        if h & 0xFFFF == OP_TICK:
            ticks += 1
        step = 1 + (h >> 16) // 4
        if step <= 0:  # pragma: no cover - parse_fifo_words rejects this first
            break
        i += step
    return items, ticks


def frames_from_words(
    source: Iterable[tuple[int, Sequence[int]]],
    config: SafetyConfig | None = None,
    *,
    laser_ok: bool = False,
) -> Iterator[PackedFrame]:
    """Frame words (e.g. :func:`nexcut.mcc.fifo.iter_frame_file`) -> dry-run packed frames.

    Every frame goes through :func:`~nexcut.mcc.safety.strip_laser_records`, which both
    validates it against the item grammar of 11 §5.2 (a malformed frame raises
    :class:`~nexcut.mcc.safety.SafetyViolation` here, not halfway through the job) and
    neutralises the laser (PORT-PLAN §8.2).  The frame id in the file is ignored: ids are
    assigned from reg 1015 at stream time (A3 §7).
    """
    cfg = config or SafetyConfig()
    for fid, data in source:
        out, _changed = strip_laser_records([0, *data], cfg, laser_ok=laser_ok)
        words = tuple(int(w) & 0xFFFFFFFF for w in out[1:])
        frame = PackedFrame(words, 0, scan_items(words)[0])
        # The vendor closes a frame at 300 words (11 §5.1) and its datagram buffer is 0x5a8
        # bytes (04 §3.2); how the card answers an over-long frame is UNVERIFIED (11 §7 step
        # 8), so one is refused here rather than halfway through the job at encode time.
        if frame.byte_size + _DATAGRAM_OVERHEAD_BYTES > MAX_FRAME_LEN:
            raise SafetyViolation(
                REG_FIFO_DATA,
                words,
                f"frame {fid} is {frame.vector_words} words: it does not fit the card's "
                f"{MAX_FRAME_LEN}-byte datagram buffer (04 §3.2)",
            )
        yield frame


class JobFeeder:
    """Streams one job into the card FIFO on its own thread (module docstring).

    ``gate`` is the daemon's :class:`~nexcut.mccd.gate.MccdGate`; ``status`` returns the
    newest block 1000/36 the daemon polled (the feeder never polls the card itself, so it
    cannot push the 30 ms status poll off the bus).
    """

    def __init__(
        self,
        gate: Any,
        status: StatusFn,
        frames: Iterable[PackedFrame],
        *,
        token: str,
        name: str = "",
        total_frames: int | None = None,
        config: FeederConfig | None = None,
        bus: Any = None,
        priority: Any = None,
        on_finish: Callable[[JobFeeder], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.gate = gate
        self.token = token
        self.name = name
        self.total_frames = total_frames
        self.config = config or FeederConfig()
        self.stats = FeedStats()
        self.error: str | None = None
        self._status = status
        self._source = iter(frames)
        self._source_done = False
        self._fifo = FifoFeeder()
        self._bus = bus
        self._priority = priority
        self._on_finish = on_finish
        self._clock = clock
        self._state = JobState.LOADED
        self._lock = threading.RLock()
        self._stop_req = threading.Event()
        self._pause_req = threading.Event()
        self._finished = threading.Event()
        self._thread: threading.Thread | None = None
        self._sent_window: list[tuple[float, int, list[int]]] = []
        """``(send time, frame id, vector words)`` of every frame reg 1015 has **not** confirmed
        yet, oldest first.  It accumulates across fill passes and is trimmed by the id the card
        reports: keeping only the last pass (what this used to do) re-armed the ``FifoTimeout``
        of :meth:`_check_ack` on every 30 ms poll, so a card whose reg 1015 stopped advancing
        while it kept taking frames was never noticed (A3 §7 deviation)."""
        self._inflight: deque[tuple[float, int]] = deque()
        """``(send time, vector bytes)`` of frames the card accepted but that may not be in
        the reg-1016 value of the newest status poll yet. The poll is up to ``MCCore`` = 30 ms
        old (04 §1), and in that window a fill can put dozens of frames into the card: without
        subtracting them the space test of 11 C2 would over-fill and the card would answer
        exception 3."""
        self._next_id: int | None = None
        """Next frame id. Seeded from reg 1015 + 1 once per job and then advanced by the PC:
        the status poll this feeder reads is up to 30 ms old, and re-deriving the id from a
        stale reg 1015 would re-use ids the card has already taken (see ``FifoFeeder.fill``)."""
        self._resend_of: int | None = None
        self._resend_count = 0
        self._seen_running = False
        """Reg 1019 confirmed the FIFO program once; if it stops afterwards with frames still
        to come, the card aborted the job (11 §4.1 row 19, A2 §2)."""
        self._started_at: float | None = None
        self._ended_at: float | None = None
        self._epoch: int | None = None
        """Motion epoch of the ``start_job`` request (docs/DECISIONS.md D10), captured in
        :meth:`start` on the caller's thread. Reading it on the streaming thread instead
        adopts whatever epoch a stop had just installed, so the job would stream past a stop
        that arrived while the thread was being created (safety review R17)."""

    # -- state -----------------------------------------------------------------------------

    @property
    def state(self) -> JobState:
        """Current :class:`JobState`."""
        return self._state

    @property
    def finished(self) -> bool:
        """True once the job reached a final state (DONE / STOPPED / FAILED).

        The state is set *before* the thread's clean stop runs, so this is "the producer has
        no more work", not "the feeder has let go of the card". Use :attr:`closed` for that.
        """
        return self._state in _FINAL

    @property
    def closed(self) -> bool:
        """True once the feeder thread has left: the final ``0x67 <- [3]`` / ``0x67 <- [1]``
        are on the wire and the job token has been given back (:meth:`_clean_stop`).

        Between :attr:`finished` and this, the feeder still owns the card's FIFO program: a
        second job started in that window would have its queue cleared by the first one's
        clean stop.
        """
        return self._finished.is_set()

    def _set_state(self, state: JobState, error: str | None = None) -> None:
        with self._lock:
            if error is not None and self.error is None:
                self.error = error
            if self._state is not state:
                log.info(
                    "job %s: %s -> %s%s",
                    self.token[:8],
                    self._state,
                    state,
                    f" ({error})" if error else "",
                )
            self._state = state

    def to_json(self) -> dict[str, Any]:
        """``job_status`` payload."""
        now = self._clock()
        started, ended = self._started_at, self._ended_at
        return {
            "token": self.token,
            "name": self.name,
            "state": str(self._state),
            "error": self.error,
            "frames_total": self.total_frames,
            "frames_pending": self._fifo.pending,
            "source_exhausted": self._source_done,
            "elapsed_s": None if started is None else (ended or now) - started,
            "stats": self.stats.to_json(),
        }

    # -- life cycle ------------------------------------------------------------------------

    def start(self) -> JobFeeder:
        """Start the streaming thread (idempotent while it runs)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if self._pause_req.is_set():
                    self._pause_req.clear()
                return self
            if self._state in _FINAL:
                raise JobError(f"job {self.token[:8]} already {self._state}")
            if self._stop_req.is_set():  # pragma: no cover - request_stop finalises instead
                raise JobError(f"job {self.token[:8]} has been stopped")
            self._preflight()
            guard = getattr(self.gate, "guarded_epoch", lambda: None)()
            self._epoch = guard if guard is not None else getattr(self.gate, "motion_epoch", 0)
            self._finished.clear()
            self._thread = threading.Thread(
                target=self._run, name=f"mccd-feeder-{self.token[:8]}", daemon=True
            )
            self._thread.start()
        return self

    def pause(self) -> None:
        """Stop the FIFO program, keep the queue (resume with :meth:`start`).

        How the vendor resumes a paused job is **UNVERIFIED** (11 N2, open after 11 §7 step 9);
        this is a plain ``0x67 <- [2]`` on the frames the card still holds.
        """
        self._pause_req.set()

    def request_stop(self, reason: str = "stopped") -> None:
        """Ask the feeder to stop and clean up; never blocks (callers may hold the bus).

        A job that was never started is finished here and now (safety review R17). Before,
        it stayed ``LOADED`` with a latent stop request: a later :meth:`start` cleared the
        FIFO, streamed the frames and sent ``0x67 <- [2]`` before the streaming loop looked
        at the flag, so the card started a program the operator had already stopped - and
        because the job never reached a final state, the daemon refused every later
        ``load_job`` with "a job is already loaded".
        """
        with self._lock:
            if self.error is None and self._state not in _FINAL:
                self.error = reason
            never_ran = self._thread is None and self._state not in _FINAL
            if never_ran:
                self._set_state(JobState.STOPPED)
        self._stop_req.set()
        self._pause_req.clear()
        if never_ran:
            # Nothing was ever sent, so the feeder holds no FIFO program: it is closed, and
            # the job token goes back (only _clean_stop did that, and it never runs here -
            # the daemon would keep refusing load_job with "a job is already running").
            self._release_token()
            self._finished.set()

    def join(self, timeout: float | None = None) -> bool:
        """Wait for the streaming thread; True when it has finished."""
        t = self._thread
        if t is None:
            return True
        t.join(timeout)
        return not t.is_alive()

    def stop_and_join(self, reason: str = "stopped", timeout: float = 10.0) -> bool:
        """:meth:`request_stop` then :meth:`join`."""
        self.request_stop(reason)
        return self.join(timeout)

    # -- checks ----------------------------------------------------------------------------

    def _preflight(self) -> None:
        """Refuse to stream unless the machine is armed for motion and not laser-armed."""
        arming = self.gate.arming
        if arming.state is ArmState.LASER_ARMED:
            raise JobError(
                "streaming is refused while LASER_ARMED: this phase runs dry runs only "
                "(docs/DECISIONS.md D5)"
            )
        if not arming.allows(C.Policy.MOTION):
            raise JobError(
                f"streaming needs MOTION_ARMED (state {arming.state}"
                + (", E-stop latched)" if arming.estop_latched else ")")
            )
        if arming.job_token != self.token:
            raise JobError("the job token is no longer the daemon's running job")

    def _token_alive(self) -> str | None:
        """Reason the job may not continue, or None."""
        arming = self.gate.arming
        if arming.job_token != self.token:
            return "job token dropped (stop / disarm / alarm)"
        if arming.state is ArmState.LASER_ARMED:
            return "machine became LASER_ARMED (D5)"
        if not arming.allows(C.Policy.MOTION):
            return f"machine is {arming.state}"
        return None

    # -- card writes -----------------------------------------------------------------------

    def _control(self, cmd: Any, *, required: bool) -> bool:
        """Send a 0x67 control vector; ``required=False`` logs instead of raising."""
        try:
            self.gate.send(cmd)
            return True
        except Exception as exc:
            if required:
                raise JobError(f"{cmd.name} refused: {exc}") from exc
            log.warning("job %s: %s failed: %s", self.token[:8], cmd.name, exc)
            return False

    def _send_frame(self, frame_id: int, words: list[int]) -> None:
        self.gate.write(REG_FIFO_DATA, words)
        now = self._clock()
        self.stats.send_times.append(now)
        self._inflight.append((now, (len(words) + PREFIX_WORDS - 1) * 4))
        self._sent_window.append((now, frame_id, list(words)))

    def unacknowledged_ids(self) -> list[int]:
        """Frame ids sent but not yet confirmed by reg 1015, oldest first (A3 §7)."""
        return [fid for _t, fid, _w in self._sent_window]

    def _effective_margin(self, margin: int, status_t: float) -> int:
        """Reg 1016 minus the frames sent after that status read (see :attr:`_inflight`)."""
        while self._inflight and self._inflight[0][0] <= status_t:
            self._inflight.popleft()
        return margin - sum(b for _, b in self._inflight)

    def _fill(self, frame_id_reg: int, margin: int) -> None:
        if self._next_id is None:
            self._next_id = next_frame_id(frame_id_reg)
        res = self._fifo.fill(
            frame_id_reg,
            margin,
            self._send_frame,
            max_frames=self.config.max_frames_per_fill,
            first_frame_id=self._next_id,
        )
        if not res.sent:
            return
        self._next_id = (res.sent[-1][0] + 1) & 0xFFFFFFFF
        for _fid, frame in res.sent:
            self.stats.frames_sent += 1
            self.stats.items_sent += frame.items
            self.stats.ticks_sent += scan_items(frame.data)[1]

    # -- flow control ----------------------------------------------------------------------

    def _refill_ring(self) -> None:
        while not self._source_done and self._fifo.pending < self.config.buffer_frames:
            try:
                frame = next(self._source)
            except StopIteration:
                self._source_done = True
                self._fifo.push((), last=True)
                return
            except SafetyViolation as exc:
                raise JobError(f"job source refused by the safety gate: {exc.reason}") from exc
            except Exception as exc:
                raise JobError(f"job source failed: {exc}") from exc
            self._fifo.push((frame,))
            self.stats.frames_loaded += 1

    def _check_ack(self, frame_id_reg: int, now: float) -> None:
        """Re-send frames reg 1015 never showed; abort after ``max_resends`` (A3 §7 deviation).

        Reg 1015 is the id of the last frame the card accepted (A3 §0), so an id inside the
        window confirms it and everything before it: that prefix leaves the window.  What is
        left is overdue once the **oldest** of it has been outstanding for ``unacked_timeout_s``
        (``ipAdd.ini FifoTimeout`` = 600 ms).  Measuring the timeout from the oldest frame - and
        not restarting it whenever a later frame goes out - is what makes a stalled reg 1015
        visible during continuous streaming.
        """
        window = self._sent_window
        if not window:
            return
        # Ids increase within a pass, so everything after the one the card reports is missing;
        # an id the card never showed at all means the whole window has to go again.
        seen = [i for i, (_t, fid, _w) in enumerate(window) if fid == frame_id_reg]
        if seen:
            del window[: seen[-1] + 1]
            self._resend_of = None
            self._resend_count = 0
            if not window:
                return
        if now - window[0][0] < self.config.unacked_timeout_s:
            return
        missing = list(window)
        first = missing[0][1]
        if self._resend_of != first:
            self._resend_of = first
            self._resend_count = 0
        self._resend_count += 1
        if self._resend_count > self.config.max_resends:
            raise JobError(
                f"frame {first} not acknowledged by reg 1015 after "
                f"{self.config.max_resends} re-sends (card reports {frame_id_reg})"
            )
        log.warning(
            "job %s: reg 1015 = %d, re-sending %d frame(s) from %d",
            self.token[:8],
            frame_id_reg,
            len(missing),
            first,
        )
        window.clear()  # _send_frame re-queues each one with a fresh send time
        for _t, fid, words in missing:
            self.stats.resends += 1
            self._send_frame(fid, list(words))

    def _check_program(self, processing_status: int) -> None:
        """The card must keep running the program while frames are still to come (A2 §2 row 19)."""
        if fifo_program_running(processing_status):
            self._seen_running = True
            return
        if self._seen_running and (self._fifo.pending or not self._source_done):
            raise JobError(
                "the card stopped the FIFO program while frames were still pending "
                f"(reg 1019 = {processing_status:#x}): starvation or a card abort (11 §4.1)"
            )

    def _note_depth(self, margin: int, alarm_2: int, *, draining: bool = False) -> None:
        """Queue-depth bookkeeping and the card's own starvation alarm (A2 §2.4).

        The alarm is a hard error *while frames are still pending*. During the final drain it
        is expected and not an error: the card reports "FIFO empty" at the instant it consumes
        the last item, so ``0x67 <- [3]`` can never arrive before that, and a card that alarms
        there would fault at the end of every job (see ``SimConfig.fifo_starvation_alarm``;
        UNVERIFIED, 11 §7 step 8/9). A drain that is *not* complete still fails, because
        :meth:`_drain` only returns once reg 1016 is back to 60000.
        """
        self.stats.polls += 1
        depth = fifo_queued_items(margin)
        s = self.stats
        s.min_margin = margin if s.min_margin is None else min(s.min_margin, margin)
        pending = bool(self._fifo.pending) or not self._source_done
        if pending:
            s.min_queue_items = (
                depth if s.min_queue_items is None else min(s.min_queue_items, depth)
            )
            if depth < self.config.alarm_items:
                s.starvation_events += 1
        if alarm_2 & int(Alarm2.FIFO_STARVATION):
            if draining:
                s.drain_starvation_alarm = True
                return
            raise JobError(
                "card reports FIFO starvation (reg 1007 bit 5, EtherCATErrorInfo_2_05, A2 §2.4)"
            )

    # -- the thread ------------------------------------------------------------------------

    def _fresh_status(self, after: float | None) -> tuple[Sequence[int], float] | None:
        st = self._status()
        if st is None:
            return None
        _block, t = st
        if after is not None and t <= after:
            return None
        return st

    def _wait_status(self, after: float | None, timeout: float) -> tuple[Sequence[int], float]:
        deadline = self._clock() + timeout
        while True:
            st = self._fresh_status(after)
            if st is not None:
                return st
            if self._clock() > deadline:
                raise JobError(f"no status poll newer than {timeout:.1f} s: cannot stream safely")
            if self._stop_req.wait(self.config.poll_period_s):
                raise _Stopped

    def _run(self) -> None:
        self._started_at = self._clock()
        ctx = (
            self._bus.priority(self._priority)
            if self._bus is not None and self._priority is not None
            else _NullCtx()
        )
        epoch = self._epoch if self._epoch is not None else self.gate.motion_epoch
        try:
            with ctx, self.gate.motion_guard(epoch):
                self._stream()
        except _Stopped:
            self._set_state(JobState.STOPPED)
        except JobError as exc:
            self._set_state(JobState.FAILED, str(exc))
            log.error("job %s failed: %s", self.token[:8], exc)
        except (FifoFrameLost, SafetyViolation) as exc:
            self._set_state(JobState.FAILED, str(exc))
            log.error("job %s failed: %s", self.token[:8], exc)
        except Exception as exc:  # pragma: no cover - must not leave the card streaming
            self._set_state(JobState.FAILED, f"unexpected: {exc}")
            log.exception("job %s", self.token[:8])
        finally:
            self._ended_at = self._clock()
            try:
                self._clean_stop()
            except Exception:  # pragma: no cover
                log.exception("job %s clean stop", self.token[:8])
            self._finished.set()
            if self._on_finish is not None:
                try:
                    self._on_finish(self)
                except Exception:  # pragma: no cover
                    log.exception("job %s on_finish", self.token[:8])

    def _stream(self) -> None:
        cfg = self.config
        if self._stop_req.is_set():  # a stop that landed while the thread was starting
            raise _Stopped
        self._preflight()
        _block, seen_t = self._wait_status(None, cfg.status_timeout_s)
        self._control(C.fifo_clear(), required=True)
        self._inflight.clear()
        # The margin of a poll taken *before* the clear still counts a program the clear just
        # discarded, so the first fill could send nothing and the port would start an empty
        # program - the A2 §2.4 starvation case, caused by the port itself.  Wait for a poll
        # that saw the cleared FIFO instead.
        block, seen_t = self._wait_status(seen_t, cfg.status_timeout_s)
        self._refill_ring()
        self._fill(int(block[Status.FIFO_FRAME_ID]), int(block[Status.FIFO_SPACE_MARGIN]))
        if self.stats.frames_sent == 0:
            raise JobError(
                "the job has no frames"
                if self._source_done
                else "no frame fits the card FIFO after 0x67 <- [1] "
                f"(reg 1016 = {int(block[Status.FIFO_SPACE_MARGIN])})"
            )
        if self._stop_req.is_set():  # never start a program the operator has already stopped
            raise _Stopped
        self._control(C.fifo_start(), required=True)
        # RUNNING means the program is started with frames in the card, not "the thread ran":
        # ``MccDaemon._cmd_start_job`` waits for this state to tell the client whether the job
        # took off, so reporting it before the clear/fill/start would answer RUNNING for a job
        # that is about to fail (a bad frame, no space after the clear, a refused 0x67).
        self._set_state(JobState.RUNNING)
        running = True
        while not self._stop_req.is_set():
            reason = self._token_alive()
            if reason is not None:
                raise JobError(reason)
            block, seen_t = self._wait_status(seen_t, cfg.status_timeout_s)
            now = self._clock()
            margin = self._effective_margin(int(block[Status.FIFO_SPACE_MARGIN]), seen_t)
            frame_id = int(block[Status.FIFO_FRAME_ID])
            self._note_depth(margin, int(block[Status.ALARM_2]))
            if self._pause_req.is_set():
                if running:
                    self._control(C.fifo_stop(), required=False)
                    running = False
                    self._seen_running = False
                    self._set_state(JobState.PAUSED)
                continue
            if not running:
                self._inflight.clear()
                self._control(C.fifo_start(), required=True)
                running = True
                self._set_state(JobState.RUNNING)
            self._check_program(int(block[Status.PROCESSING_STATUS]))
            self._check_ack(frame_id, now)
            self._refill_ring()
            self._fill(frame_id, margin)
            if not self._fifo.pending and self._source_done:
                self._drain()
                self._set_state(JobState.DONE)
                return
        raise _Stopped

    def _drain(self) -> None:
        """Everything is in the card: wait for reg 1016 to come back to 60000 (A3 §7 job end).

        This phase reads block 1000 itself instead of consuming the daemon's 30 ms poll: the
        card's FIFO is emptying, and the sooner ``0x67 <- [3]`` follows the last item the
        smaller the window in which the card sees an empty running FIFO (see the note on
        ``SimConfig.fifo_starvation_alarm`` - what a real card does at a clean drain is
        UNVERIFIED, 11 §7 step 8).
        """
        cfg = self.config
        deadline = self._clock() + cfg.drain_timeout_s
        while not self._stop_req.is_set():
            block = self.gate.read(1000, 36)
            margin = self._effective_margin(int(block[Status.FIFO_SPACE_MARGIN]), self._clock())
            self._note_depth(margin, int(block[Status.ALARM_2]), draining=True)
            self._check_ack(int(block[Status.FIFO_FRAME_ID]), self._clock())
            if margin >= FIFO_MARGIN_EMPTY and not self._sent_window:
                return
            if self._clock() > deadline:
                raise JobError(
                    f"the card did not drain the last frame within {cfg.drain_timeout_s:.0f} s "
                    f"(reg 1016 = {margin})"
                )
            if self._stop_req.wait(cfg.poll_period_s):
                raise _Stopped
        raise _Stopped

    def _clean_stop(self) -> None:
        """``0x67 <- [3]`` then ``0x67 <- [1]``: never leave a program queued in the card."""
        if self._started_at is None:
            return
        if self._state not in _FINAL:  # pragma: no cover - every exit path sets one
            self._set_state(JobState.STOPPED if self._stop_req.is_set() else JobState.FAILED)
        self._control(C.fifo_stop(), required=False)
        self._control(C.fifo_clear(), required=False)
        self._release_token()

    def _release_token(self) -> None:
        """Give the job token back to the arming state machine (idempotent)."""
        try:
            self.gate.arming.end_job(self.token)
        except Exception:  # the token may already have been dropped by a stop / disarm
            log.debug("job %s: token already gone", self.token[:8])


class _NullCtx:
    """``with`` that does nothing (no bus priority given)."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None
