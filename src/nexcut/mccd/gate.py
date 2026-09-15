"""Transport arbitration and the daemon's motion rules on top of the safety gate.

* :class:`PriorityBus` - one card transaction at a time, granted to the highest-priority
  waiter: stop/E-stop first, then the 30 ms status poll, then IPC commands, then the slow
  queue (PORT-PLAN §3.4: "block 1000 every 30 ms on its own queue; slow blocks on a
  background queue, never blocking the fast poll"; 11 §3.2).
* :class:`BusClient` - the ``McClient`` handed to :class:`~nexcut.mcc.safety.SafeMccClient`:
  every read/write holds the bus and uses the retry policy of its queue (docs/DECISIONS.md
  D4). The bus is always the innermost lock (gate lock -> bus), so the poll thread and IPC
  threads cannot deadlock.
* :class:`MotionRules` / :class:`MccdGate` - the F8 decision (docs/DECISIONS.md D1):

  **Before an axis is homed (or while its read-back position is not trusted, D2) the PC
  has no position, so soft limits cannot be checked.** A relative jog on such an axis is
  allowed only if ``|distance| <= UNHOMED_MAX_STEP_MM`` (10 mm) *or* its speed is
  ``<= UNHOMED_MAX_JOG_SPEED_MM_S`` (20 mm/s) - a slow jog is always longer than
  ``speed x deadman`` and therefore always runs under the 200 ms deadman lease of the
  safety gate (PORT-PLAN §8.2), so it stops within ~4 mm plus deceleration of the last
  refresh. Once the axis is homed and its position is trusted, the PC-side soft limits
  (01 §2.2 ``SoftLimitMaxLen``) are applied to every relative jog target. The vendor allows
  un-homed jogs without any bound (A1 §1 gates); the bound is a port decision. Both
  constants come from ``[motion]`` in ``config.toml``.

  **Step rate (D1 amendment).** Steps allowed only by the 10 mm branch (speed above the
  un-homed jog speed) draw on a per-axis token bucket: capacity ``UNHOMED_MAX_STEP_MM``,
  refilled at ``UNHOMED_MAX_JOG_SPEED_MM_S``. Chained steps therefore cannot move an
  un-homed axis faster than the continuous-jog bound (plus one 10 mm burst).
* **Motion epoch** (docs/DECISIONS.md D10): ``stop`` / ``estop`` / ``disarm`` / watchdog trips
  bump :attr:`MccdGate.motion_epoch`; a motion write made under :meth:`MccdGate.motion_guard`
  with an older epoch is refused *under the bus*, so a jog or home that was queued behind
  a stop can never reach the card after it.
* **Deadman leases** can only be refreshed while the machine is armed and not E-stopped
  (PORT-PLAN §8.2): after a trip or alarm whose stop did not get through, the lease
  expires and the gate keeps re-sending the per-axis stop.
"""

from __future__ import annotations

import contextlib
import heapq
import itertools
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from nexcut.mcc import commands as C
from nexcut.mcc.commands import Policy
from nexcut.mcc.framing import REG_COMMAND
from nexcut.mcc.safety import (
    ArmState,
    McClient,
    SafeMccClient,
    SafetyViolation,
    _soft_limit_violation,
)
from nexcut.mcc.transaction import RetryPolicy

__all__ = [
    "UNHOMED_MAX_JOG_SPEED_MM_S",
    "UNHOMED_MAX_STEP_MM",
    "BusClient",
    "MccdGate",
    "MotionRules",
    "Priority",
    "PriorityBus",
]

UNHOMED_MAX_STEP_MM = 10.0
"""Default of ``motion.unhomed_max_step_mm`` (docs/DECISIONS.md D1)."""
UNHOMED_MAX_JOG_SPEED_MM_S = 20.0
"""Default of ``motion.unhomed_max_jog_speed_mm_s`` (docs/DECISIONS.md D1)."""


class Priority(IntEnum):
    """Bus priority; lower value wins."""

    URGENT = 0
    """Stop, E-stop, deadman and watchdog stops."""
    FAST = 1
    """Block 1000/36 poll and the start-up sequence (11 §3.2)."""
    COMMAND = 2
    """IPC commands."""
    SLOW = 3
    """2000/60001/50000/10000 background reads (11 §3.2, PORT-PLAN §3.4)."""


class PriorityBus:
    """Re-entrant mutex that hands the card transport to the highest-priority waiter.

    Waiters of equal priority are served FIFO. A holder is never pre-empted: a slow read in
    progress keeps the bus for at most its (short, single-try) timeout, docs/DECISIONS.md D4.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._owner: int | None = None
        self._depth = 0
        self._waiters: list[tuple[int, int, int]] = []
        self._counter = itertools.count()
        self._tls = threading.local()

    @contextmanager
    def priority(self, prio: Priority) -> Iterator[None]:
        """Set the calling thread's default priority for :meth:`hold` (restored on exit)."""
        old = getattr(self._tls, "prio", None)
        self._tls.prio = prio
        try:
            yield
        finally:
            self._tls.prio = old

    def current_priority(self) -> Priority:
        """The calling thread's priority (default COMMAND)."""
        p = getattr(self._tls, "prio", None)
        return Priority.COMMAND if p is None else p

    @contextmanager
    def hold(self, prio: Priority | None = None) -> Iterator[None]:
        """Hold the bus (re-entrant for the owning thread)."""
        me = threading.get_ident()
        p = int(self.current_priority() if prio is None else prio)
        with self._cond:
            if self._owner == me:
                self._depth += 1
            else:
                entry = (p, next(self._counter), me)
                heapq.heappush(self._waiters, entry)
                try:
                    while self._owner is not None or self._waiters[0] != entry:
                        self._cond.wait()
                except BaseException:
                    self._waiters.remove(entry)
                    heapq.heapify(self._waiters)
                    self._cond.notify_all()
                    raise
                heapq.heappop(self._waiters)
                self._owner = me
                self._depth = 1
        try:
            yield
        finally:
            with self._cond:
                self._depth -= 1
                if self._depth == 0:
                    self._owner = None
                    self._cond.notify_all()


class _BusLockView:
    """Context-manager view of the bus at the caller's priority (``SafeMccClient._send``)."""

    def __init__(self, bus: PriorityBus) -> None:
        self._bus = bus
        self._tls = threading.local()

    def __enter__(self) -> None:
        cm = self._bus.hold()
        cm.__enter__()
        stack = getattr(self._tls, "stack", None)
        if stack is None:
            stack = self._tls.stack = []
        stack.append(cm)

    def __exit__(self, *exc: object) -> None:
        self._tls.stack.pop().__exit__(None, None, None)


class BusClient:
    """``McClient`` that serialises transactions on a :class:`PriorityBus` (docs/DECISIONS.md D4).

    Policies: the FAST queue reads with ``poll_policy``; every other read (slow queue, IPC
    ``read_block``) with ``read_policy``; writes with ``write_policy`` (the vendor idle
    ladder, 04 §1 / transaction.py). The policy is applied under the bus, so it cannot
    leak into another queue's transaction. Transports without ``set_policy`` (test fakes)
    are used as they are.
    """

    def __init__(
        self,
        transport: McClient,
        bus: PriorityBus,
        *,
        poll_policy: RetryPolicy | None = None,
        read_policy: RetryPolicy | None = None,
        write_policy: RetryPolicy | None = None,
        on_read: Callable[[int, int, list[int]], None] | None = None,
    ) -> None:
        self._transport = transport
        self.on_read = on_read
        """Called with the raw words of every successful read, before the gate reacts to
        them (so a published status already shows the alarm the gate is stopping for).
        Runs under the bus: must be quick and must not take the gate lock."""
        self.bus = bus
        self.poll_policy = poll_policy
        self.read_policy = read_policy
        self.write_policy = write_policy
        self._lock = _BusLockView(bus)
        """Consulted by ``SafeMccClient._send`` to re-check arming under the transport lock."""

    @property
    def next_seq(self) -> int:
        """Sequence number of the next transaction (for the gate's frame log)."""
        return int(getattr(self._transport, "next_seq", 0))

    def _apply(self, policy: RetryPolicy | None) -> None:
        setter: Callable[[RetryPolicy], None] | None = getattr(self._transport, "set_policy", None)
        if policy is not None and setter is not None:
            setter(policy)

    def read(self, addr: int, count: int) -> list[int]:
        """READ under the bus."""
        with self.bus.hold():
            fast = self.bus.current_priority() is Priority.FAST
            self._apply(self.poll_policy if fast else self.read_policy)
            words = self._transport.read(addr, count)
            if self.on_read is not None and len(words) == count:
                self.on_read(addr, count, list(words))
            return words

    def write(self, addr: int, words: Sequence[int]) -> None:
        """WRITE under the bus."""
        with self.bus.hold():
            self._apply(self.write_policy)
            self._transport.write(addr, words)

    def close(self) -> None:
        """Close the underlying transport if it can be closed."""
        close = getattr(self._transport, "close", None)
        if close is not None:
            close()


@dataclass(frozen=True, slots=True)
class MotionRules:
    """F8 rule parameters (docs/DECISIONS.md D1), in card words (K = card units/mm, A1 §2)."""

    unhomed_max_step_word: int = int(UNHOMED_MAX_STEP_MM * 1000)
    unhomed_max_jog_speed_word: int = int(UNHOMED_MAX_JOG_SPEED_MM_S * 1000)

    @classmethod
    def from_mm(cls, max_step_mm: float, max_speed_mm_s: float, k: int = 1000) -> MotionRules:
        """Build from ``[motion]`` values; truncation toward zero like the builders (A1 §2)."""
        return cls(C.trunc_int(max_step_mm * k), C.trunc_int(max_speed_mm_s * k))


def _s32(word: int) -> int:
    word &= 0xFFFFFFFF
    return word - (1 << 32) if word & 0x80000000 else word


class MccdGate(SafeMccClient):
    """:class:`SafeMccClient` plus the daemon's un-homed jog rule (module docstring, D1).

    ``homed_slots`` and ``positions_word`` are maintained by the daemon from block 2000.
    A relative jog on an axis-list index that is not in ``homed_slots`` or has no trusted
    position in ``positions_word`` is bounded by :class:`MotionRules`; otherwise its
    target is checked against the soft limits of ``config.soft_limits_word``.
    """

    def __init__(
        self, client: McClient, *args: Any, rules: MotionRules | None = None, **kw: Any
    ) -> None:
        super().__init__(client, *args, **kw)
        self.rules = rules or MotionRules()
        self.homed_slots: set[int] = set()
        self.motion_epoch = 0
        """Bumped by every stop / E-stop / disarm / trip (docs/DECISIONS.md D10)."""
        self._guard = threading.local()
        self._step_bucket: dict[int, tuple[float, float]] = {}
        """Axis index -> (tokens in card words, clock time) of the D1 step-rate bucket."""

    # -- motion epoch (D10) ------------------------------------------------------------------

    def invalidate_motion(self) -> int:
        """A stop / disarm happened: motion writes guarded by an older epoch are refused."""
        self.motion_epoch += 1  # a lost concurrent increment still changes the value
        return self.motion_epoch

    @contextmanager
    def motion_guard(self, epoch: int) -> Iterator[None]:
        """Motion writes of the calling thread must still see ``epoch`` when they hit the bus."""
        old = getattr(self._guard, "epoch", None)
        self._guard.epoch = epoch
        try:
            yield
        finally:
            self._guard.epoch = old

    def guarded_epoch(self) -> int | None:
        """Epoch of the calling thread's :meth:`motion_guard`, if any."""
        return getattr(self._guard, "epoch", None)

    def _send(
        self,
        addr: int,
        w: list[int],
        state: ArmState,
        reason: str,
        stripped: int,
        policy: Policy = Policy.ALWAYS,
        laser_items: int = 0,
    ) -> None:
        """Base send, plus the motion-epoch check under the transport lock (D10)."""
        expected = self.guarded_epoch()
        if expected is None or policy in (Policy.ALWAYS, Policy.CONNECT):
            super()._send(addr, w, state, reason, stripped, policy, laser_items)
            return
        lock = getattr(self.client, "_lock", None)
        with lock if lock is not None else contextlib.nullcontext():
            if expected != self.motion_epoch:
                raise self._refuse(
                    addr,
                    w,
                    self.arming.state,
                    f"{reason}: a stop / disarm arrived before this motion was sent "
                    "(DECISIONS D10)",
                )
            super()._send(addr, w, state, reason, stripped, policy, laser_items)

    # -- D1 ------------------------------------------------------------------------------------

    def jog_rule_violation(self, words: Sequence[int]) -> str | None:
        """Reason a relative ``[3, i, v, a, 10a, d]`` jog breaks D1 / the soft limits, else None."""
        idx, v, d = words[1], words[2], _s32(words[5])
        pos = self.positions_word.get(idx) if idx in self.homed_slots else None
        if pos is None:
            if 0 < v <= self.rules.unhomed_max_jog_speed_word:
                # Either the jog ends within the deadman time by itself (|d| <= v x deadman),
                # or the base gate registers a deadman lease for it
                # (SafeMccClient._register_lease): it stops unless refreshed.
                return None
            if abs(d) <= self.rules.unhomed_max_step_word:
                return None
            return (
                f"axis {idx} not homed / position unknown: relative jog {d} beyond "
                f"{self.rules.unhomed_max_step_word} needs speed <= "
                f"{self.rules.unhomed_max_jog_speed_word} under the deadman (DECISIONS D1)"
            )
        return _soft_limit_violation(idx, pos + d, self.config)

    def _rate_limited(self, words: Sequence[int]) -> bool:
        """True for un-homed jogs allowed only by the 10 mm step branch (D1 step rate)."""
        idx, v = words[1], words[2]
        pos = self.positions_word.get(idx) if idx in self.homed_slots else None
        return pos is None and not 0 < v <= self.rules.unhomed_max_jog_speed_word

    def _step_tokens(self, idx: int, now: float) -> float:
        cap = float(self.rules.unhomed_max_step_word)
        tokens, t = self._step_bucket.get(idx, (cap, now))
        return min(cap, tokens + max(0.0, now - t) * self.rules.unhomed_max_jog_speed_word)

    def step_rate_violation(self, words: Sequence[int], now: float | None = None) -> str | None:
        """Reason a fast un-homed step exceeds the D1 step-rate bucket, else None.

        UNVERIFIED port choice (docs/DECISIONS.md D1 amendment): capacity = one maximal step,
        refill = the un-homed continuous-jog speed.
        """
        if not self._rate_limited(words):
            return None
        now = self._clock() if now is None else now
        idx, d = words[1], abs(_s32(words[5]))
        tokens = self._step_tokens(idx, now)
        if d <= tokens + 1e-6:
            return None
        wait = (d - tokens) / max(1, self.rules.unhomed_max_jog_speed_word)
        return (
            f"axis {idx} not homed: steps above {self.rules.unhomed_max_jog_speed_word} "
            f"words/s are limited to an average of that speed; next {d}-word step in "
            f"{wait:.2f} s (DECISIONS D1 step rate)"
        )

    def _consume_step(self, words: Sequence[int], now: float) -> None:
        if self._rate_limited(words):
            idx = words[1]
            tokens = self._step_tokens(idx, now) - abs(_s32(words[5]))
            self._step_bucket[idx] = (max(0.0, tokens), now)

    # -- leases --------------------------------------------------------------------------------

    def has_lease(self, axis_index: int) -> bool:
        """True while a deadman lease exists for ``axis_index`` (PORT-PLAN §8.2).

        Lock-free on purpose (a single dict lookup): status queries must not wait behind a
        write that holds the gate lock for a whole retry ladder.
        """
        return axis_index in self._leases

    def leases(self) -> dict[int, int]:
        """Axis index -> jog speed word of the live deadman leases (lock-free snapshot)."""
        return {i: v for i, (_, v) in self._leases.copy().items()}

    def jog_keepalive(self, axis_index: int) -> bool:
        """Extend a deadman lease - only while armed and not E-stopped (PORT-PLAN §8.2).

        A disarmed machine must not move: if the stop of a trip / alarm / disarm did not
        reach the card, the lease is left to expire so :meth:`service` re-sends the
        per-axis stop, whatever the client keeps refreshing.
        """
        if self.arming.state is ArmState.DISARMED or self.arming.estop_latched:
            return False
        return super().jog_keepalive(axis_index)

    def write(self, addr: int, words: Sequence[int]) -> None:
        """Checked WRITE: the D1 rule and step rate for relative jogs, then the base gate (11 §3)."""
        w = [int(x) & 0xFFFFFFFF for x in words]
        if addr == REG_COMMAND and len(w) == 6 and w[0] == C.CMD_JOG and not w[1] & C.ABSOLUTE_BIT:
            with self._lock:
                now = self._clock()
                reason = self.jog_rule_violation(w) or self.step_rate_violation(w, now)
                if reason is not None:
                    raise self._refuse(addr, w, self.arming.state, reason)
                try:
                    super().write(addr, w)
                except SafetyViolation:
                    raise
                except Exception:
                    self._consume_step(w, now)  # the card may have executed it (lost reply)
                    raise
                self._consume_step(w, now)
            return
        super().write(addr, w)
