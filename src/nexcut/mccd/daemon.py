"""``nexcut-mccd``: the driver daemon, the only owner of the MCC100 card socket.

Enforcement boundary (PORT-PLAN §2.3, docs/DECISIONS.md D5): this process owns the
:class:`~nexcut.mcc.transaction.McTransaction` and the safety gate
(:class:`~nexcut.mccd.gate.MccdGate`, a :class:`~nexcut.mcc.safety.SafeMccClient`); clients
only get the JSON-lines IPC of :mod:`nexcut.mccd.ipc`, whose command set (:data:`IPC_COMMANDS`)
has no raw register write.

Threads (all blocking I/O; transactions are serialised by :class:`~nexcut.mccd.gate.PriorityBus`):

* **fast** - start-up sequence while not connected, then ``READ 1000/36`` every
  ``MCCore x MCUpdateFactor`` = 30 ms (11 §3.2, 04 §1). Start-up = 11 §7 step 1 / 11 §2 V0:
  ``READ 1000/2`` (version gate, reg 1001 >= ``MinHardwareVer``, 11 §4.1), ``READ 1000/36``,
  ``READ 50000/26`` (card authoritative for K / bus cycle / ZFType, 11 §3.2),
  ``READ 50200/100``, then the connect write ``0x65 <- [9999,5,0,0]`` (11 N4).
* **slow** - ``2000/50``, ``60001/120``, ``50000/26`` and (ZFType != 0) ``10000/18`` on their own
  periods with a short single-try policy, so they never hold the transport away from the
  fast poll for longer than one short timeout (PORT-PLAN §3.4, docs/DECISIONS.md D4). A failed
  10000 read is not treated as link loss (11 §3.2 row 10000, A5 V4).
* **watchdog** (20 ms) - PORT-PLAN §8.2: status poll older than 1 s -> link lost, disarm and
  stop; deadman leases of continuous jogs (``SafeMccClient.service``: per-axis stop
  ``[1, 1<<slot, 2, vd, 10vd]``, A1 §4.1, 11 N1); axis fault/limit bits while jogging and
  configured DI alarms (11 §4.7 rows 5/6) -> stop + disarm; input sources silent or lost ->
  per-axis stop of the jogs they started (11 N7, generalised). Card alarm words (rows 1-4,
  8) are handled by the gate on every status read (stop + disarm, bit 30 latches E-stop).
* **publisher** - status subscription stream.
* **homing** - one job at a time, axes one after the other (11 §2 V6 "one axis at a time").

Jog rules (docs/DECISIONS.md D1, F8): before homing, relative steps <= 10 mm per command,
continuous jog only at <= 20 mm/s under the deadman; after homing (with a trusted position
scale, D2) PC-side soft limits. Card-side gates mirrored (A1 §1): jogs only in machine
state READY, homing in READY; jogs toward an active limit bit refused (11 §4.7 row 5).

Concurrency (docs/DECISIONS.md D8/D10): one motion command at a time (``_motion_lock``,
a second one gets ``busy``); jogs are refused while a homing job exists; every motion write
carries the motion epoch of its request, so a stop / E-stop / disarm / trip that arrives
while the write is queued makes the gate refuse it under the bus. A stop that a trip could
not deliver (dead link) is re-sent after the start-up sequence, before the link is
reported CONNECTED again (PORT-PLAN §8.2).

Laser: there is no ``arm_laser`` command in this phase; laser DO ports can only be switched
off over IPC (PORT-PLAN §8.2).
"""

from __future__ import annotations

import contextlib
import logging
import math
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from nexcut import __version__
from nexcut.core.config import NexcutConfig, default_socket_path
from nexcut.mcc import commands as C
from nexcut.mcc.commands import MachineParams, Policy
from nexcut.mcc.registers import (
    AXIS_RO_WORDS,
    MachineState,
    Status,
    SystemRW,
    derive_machine_state,
)
from nexcut.mcc.safety import (
    ArmingError,
    ArmState,
    SafetyConfig,
    SafetyViolation,
    StopOutcome,
    classify_read,
)
from nexcut.mcc.transaction import CardBusy, CardRefused, McError, McTransaction, RetryPolicy
from nexcut.mccd.gate import BusClient, MccdGate, MotionRules, Priority, PriorityBus
from nexcut.mccd.ipc import PROTOCOL_VERSION, Connection, IpcError, IpcServer
from nexcut.mccd.status import StatusSnapshot, axis_limit_blocks, watchdog_reasons

__all__ = [
    "IPC_COMMANDS",
    "STARTUP_READS",
    "InputSource",
    "Link",
    "MccDaemon",
]

log = logging.getLogger("nexcut.mccd")

STARTUP_READS: tuple[tuple[int, int], ...] = ((1000, 2), (1000, 36), (50000, 26), (50200, 100))
"""Reads before the connect write, in order (11 §7 step 1)."""

IPC_COMMANDS: tuple[str, ...] = (
    "ping",
    "status",
    "subscribe",
    "unsubscribe",
    "arm_motion",
    "disarm",
    "jog_step",
    "jog_continuous_start",
    "jog_refresh",
    "jog_continuous_stop",
    "home",
    "stop",
    "estop",
    "ack_estop",
    "read_block",
    "set_do",
)
"""The complete IPC vocabulary. No raw write / transact / firmware command exists (F12)."""

_WATCHDOG_PERIOD_S = 0.02
"""Watchdog/deadman service period. UNVERIFIED choice (well inside the 200 ms deadman)."""
_PUBLISH_TICK_S = 0.01
_LOG_KEEP = 2000
"""Gate write-log / arming-history entries kept in memory."""


class Link(StrEnum):
    """Card link state."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    VERSION_REFUSED = "VERSION_REFUSED"
    LINK_LOST = "LINK_LOST"


class InputSource:
    """A motion input (IPC connection, pendant, ...) whose loss must stop its jogs (11 N7).

    ``touch()`` on every sign of life; ``claim(slot)`` when a jog it started is running;
    ``lost(reason)`` on read error / device removal / disconnect. With a
    ``silence_timeout_s`` the watchdog treats silence longer than that as lost (pendant:
    1.04 s, A8 §7 step 5).
    """

    def __init__(
        self, daemon: MccDaemon, name: str, silence_timeout_s: float | None = None
    ) -> None:
        self._daemon = daemon
        self.name = name
        self.silence_timeout_s = silence_timeout_s
        self.last_seen = time.monotonic()
        self._slots: set[int] = set()
        self._lock = threading.Lock()

    @property
    def slots(self) -> frozenset[int]:
        """Axis indices with a jog started by this source."""
        with self._lock:
            return frozenset(self._slots)

    def touch(self) -> None:
        """Mark the source alive."""
        self.last_seen = time.monotonic()

    def claim(self, slot: int) -> None:
        """Record a running jog started by this source."""
        with self._lock:
            self._slots.add(slot)
        self.touch()

    def release(self, slot: int) -> None:
        """The jog on ``slot`` ended or was stopped."""
        with self._lock:
            self._slots.discard(slot)

    def take_slots(self) -> set[int]:
        """Remove and return all claimed slots."""
        with self._lock:
            s, self._slots = self._slots, set()
            return s

    def lost(self, reason: str = "input source lost") -> None:
        """Input lost: stop every jog this source started (per-axis stop, A1 §4.1)."""
        self._daemon.stop_source(self, reason)


@dataclass(slots=True)
class _SlowEntry:
    addr: int
    count: int
    period_s: float
    needs_zf: bool = False
    next_t: float = 0.0


class _HomingJob:
    def __init__(self, slots: Sequence[int], epoch: int = 0) -> None:
        self.slots = tuple(slots)
        self.epoch = epoch
        """Motion epoch of the ``home`` request (docs/DECISIONS.md D10)."""
        self.cancel = threading.Event()
        self.current: int | None = None
        self.done: list[int] = []
        self.error: str | None = None
        self.thread: threading.Thread | None = None


def _s32(word: int) -> int:
    word &= 0xFFFFFFFF
    return word - (1 << 32) if word & 0x80000000 else word


class MccDaemon:
    """The driver daemon (module docstring). ``start()`` it, ``close()`` it."""

    def __init__(
        self,
        config: NexcutConfig | None = None,
        *,
        card_addr: tuple[str, int] | None = None,
        transport: Any | None = None,
        socket_path: Path | str | None = None,
        serve_ipc: bool = True,
    ) -> None:
        self.config = cfg = config or NexcutConfig()
        self._lock = threading.RLock()
        mc, d = cfg.mc, cfg.mccd
        idle = RetryPolicy.idle(
            mc_timeout_ms=mc.mc_timeout_ms,
            mc_fifo_time_ms=mc.mc_fifo_time_ms,
            mc_max_recv_time=mc.mc_max_recv_time,
            mc_send_interval_ms=mc.mc_send_interval_ms,
        )
        self._owns_transport = transport is None
        if transport is None:
            addr = card_addr or (cfg.card.ip, cfg.card.port)
            transport = McTransaction(addr, policy=idle)
        self.card_addr = card_addr or (cfg.card.ip, cfg.card.port)
        self.bus = PriorityBus()
        self._bus_client = BusClient(
            transport,
            self.bus,
            poll_policy=RetryPolicy(d.poll_timeout_ms, 1, 1, mc.mc_send_interval_ms),
            read_policy=RetryPolicy(d.slow_timeout_ms, 1, 1, mc.mc_send_interval_ms),
            write_policy=idle,
            on_read=self._on_raw_read,
        )
        self.params = MachineParams()
        self.gate = MccdGate(
            self._bus_client,
            config=self._safety_config(self.params.k),
            params=self.params,
            supervise=False,
            rules=MotionRules.from_mm(
                cfg.motion.unhomed_max_step_mm, cfg.motion.unhomed_max_jog_speed_mm_s
            ),
        )
        self.socket_path = Path(socket_path or d.socket_path or default_socket_path())
        self._serve_ipc = serve_ipc
        self.server: IpcServer | None = None

        self.link = Link.DISCONNECTED
        self.last_error: str | None = None
        self.program_version: int | None = None
        self.block1000: list[int] | None = None
        self.axis_ro: list[int] | None = None
        self.axis_ro_t: float | None = None
        self.system_rw: list[int] | None = None
        self.axis_rw: list[int] | None = None
        self.fast_combined: list[int] | None = None
        self.zf_status: list[int] | None = None
        self.connected_at: float | None = None
        self._ever_connected = False
        self._jog_speed: dict[int, int] = {}
        self._last_motion_t = -math.inf
        """Time of the last motion command sent; READY needs a 2000/50 read taken after it."""
        self._sources: list[InputSource] = []
        self._homing: _HomingJob | None = None
        self._motion_lock = threading.Lock()
        """Held from the READY check to the end of the send of one motion command (D8)."""
        self._stop_pending = 0
        """Generation of an undelivered watchdog stop (0 = none); re-sent at start-up."""
        self._stop_gen = 0
        self._trip_key: str | None = None
        self._stop_evt = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False
        self._closed = False
        self._commands: dict[str, Callable[[Connection | None, dict[str, Any]], Any]] = {
            name: getattr(self, f"_cmd_{name}") for name in IPC_COMMANDS
        }

    # ------------------------------------------------------------------------------ config

    def _safety_config(self, k: int) -> SafetyConfig:
        m, d = self.config.motion, self.config.mccd
        return replace(
            SafetyConfig(),
            soft_limits_word=(
                (C.trunc_int(m.soft_limit_x_mm[0] * k), C.trunc_int(m.soft_limit_x_mm[1] * k)),
                (C.trunc_int(m.soft_limit_y_mm[0] * k), C.trunc_int(m.soft_limit_y_mm[1] * k)),
            ),
            deadman_timeout_s=d.deadman_timeout_ms / 1000.0,
            comm_loss_timeout_s=d.watchdog_timeout_ms / 1000.0,
        )

    def _apply_system_rw(self, words: Sequence[int]) -> None:
        """Card-authoritative K / ZFType (11 §3.2, A1 §2)."""
        k = int(words[SystemRW.K])
        zf = int(words[SystemRW.ZF_TYPE])
        if k <= 0:
            log.warning("reg 50017 K = %d ignored (keeping %d)", k, self.params.k)
            k = self.params.k
        if k != self.params.k or zf != self.params.zf_type:
            self.params = replace(self.params, k=k, zf_type=zf)
            with self.gate._lock:
                self.gate.params = self.params
                self.gate.config = self._safety_config(k)
                self.gate.rules = MotionRules.from_mm(
                    self.config.motion.unhomed_max_step_mm,
                    self.config.motion.unhomed_max_jog_speed_mm_s,
                    k,
                )
            log.info("card K=%d ZFType=%d", k, zf)

    # ----------------------------------------------------------------------------- lifecycle

    def start(self) -> MccDaemon:
        """Start IPC and the worker threads (the card is contacted by the fast thread)."""
        if self._started:
            return self
        self._started = True
        if self._serve_ipc:
            self.server = IpcServer(
                self.socket_path, self.handle, on_open=self._on_open, on_close=self._on_close
            ).start()
        for name, target in (
            ("mccd-fast", self._fast_loop),
            ("mccd-slow", self._slow_loop),
            ("mccd-watchdog", self._watchdog_loop),
            ("mccd-publish", self._publish_loop),
        ):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def close(self) -> None:
        """Shut down: close IPC (stops client jogs), stop threads, stop motion, close socket."""
        if self._closed:
            return
        self._closed = True
        self.gate.invalidate_motion()
        self._cancel_homing("daemon shutdown")
        if self.server is not None:
            self.server.close()
        self._stop_evt.set()
        for t in self._threads:
            t.join(timeout=3.0)
        with self.bus.priority(Priority.URGENT):
            try:
                if self.gate.leases() or self.gate.arming.state is not ArmState.DISARMED:
                    self.gate.arming.disarm("daemon shutdown")
                    if self.link is Link.CONNECTED:
                        self.gate.stop()
            except Exception:  # pragma: no cover - best effort
                log.exception("shutdown stop")
        if self._owns_transport:
            self._bus_client.close()

    def __enter__(self) -> MccDaemon:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.close()

    def wait_for_link(self, state: Link = Link.CONNECTED, timeout: float = 5.0) -> bool:
        """Block until ``link`` equals ``state`` (tests, CLI)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.link is state:
                return True
            time.sleep(0.01)
        return self.link is state

    def _set_link(self, state: Link, error: str | None = None) -> None:
        with self._lock:
            if self.link is not state:
                log.warning("link %s -> %s%s", self.link, state, f" ({error})" if error else "")
            self.link = state
            if error is not None:
                self.last_error = error

    def _on_raw_read(self, addr: int, count: int, words: list[int]) -> None:
        """Store block 1000 before the gate's alarm reaction (BusClient.on_read)."""
        if addr == 1000 and count == 36:
            with self._lock:
                self.block1000 = words

    def _note_error(self, where: str, exc: BaseException) -> None:
        with self._lock:
            self.last_error = f"{where}: {exc}"
        log.debug("%s failed: %s", where, exc)

    # --------------------------------------------------------------------------- fast queue

    def _startup(self) -> bool:
        """Start-up sequence of 11 §7 step 1 + V0 connect write (11 §2, N4)."""
        self._set_link(Link.CONNECTING)
        try:
            version = self.gate.read(*STARTUP_READS[0])
            self.program_version = int(version[Status.PROGRAM_VERSION])
            if self.program_version < self.config.mc.min_hardware_ver:
                self._set_link(
                    Link.VERSION_REFUSED,
                    f"card version {self.program_version} < MinHardwareVer "
                    f"{self.config.mc.min_hardware_ver} (11 §4.1)",
                )
                return False
            self.gate.read(*STARTUP_READS[1])  # stored by _on_raw_read
            now = time.monotonic()
            sysrw = self.gate.read(*STARTUP_READS[2])
            with self._lock:
                self.system_rw = list(sysrw)
            self._apply_system_rw(sysrw)
            axrw = self.gate.read(*STARTUP_READS[3])
            with self._lock:
                self.axis_rw = list(axrw)
            self.gate.send(C.connect_prologue())
            self._send_pending_stop()
        except (McError, OSError, SafetyViolation) as exc:
            self._set_link(Link.LINK_LOST if self._ever_connected else Link.DISCONNECTED)
            self._note_error("start-up", exc)
            return False
        with self._lock:
            self.connected_at = now
            self._ever_connected = True
            self.link = Link.CONNECTED
        log.warning("card connected, version %s", self.program_version)
        return True

    def _send_pending_stop(self) -> None:
        """Re-send a watchdog stop that did not reach the card (PORT-PLAN §8.2).

        Called at start-up after the connect write: the link is not CONNECTED (so nothing can
        be armed) until the card acknowledged the stop. Raises on failure.
        """
        gen = self._stop_pending
        if not gen:
            return
        log.warning("re-sending the undelivered watchdog stop after reconnection")
        with self.bus.priority(Priority.URGENT):
            cmds = ([C.fifo_stop()] if self.gate.fifo_running else []) + [
                C.stop_all(0, self.params)
            ]
            for cmd in cmds:
                try:
                    self.gate.send(cmd)
                except CardRefused:
                    pass  # an exception reply still proves the card got it (08 §4.5)
        self._clear_pending_stop(gen)

    def _clear_pending_stop(self, gen: int) -> None:
        with self._lock:
            if self._stop_pending == gen:
                self._stop_pending = 0

    def _fast_loop(self) -> None:
        cfg = self.config
        period = cfg.mc.mc_core_ms * cfg.mc.mc_update_factor / 1000.0
        reconnect = cfg.mccd.reconnect_interval_ms / 1000.0
        last_try = -math.inf
        with self.bus.priority(Priority.FAST):
            next_t = time.monotonic()
            while not self._stop_evt.is_set():
                now = time.monotonic()
                if self.link is not Link.CONNECTED:
                    if self.link is not Link.VERSION_REFUSED and now - last_try >= reconnect:
                        last_try = now
                        try:
                            self._startup()
                        except Exception:  # pragma: no cover - keep polling
                            log.exception("start-up")
                else:
                    try:
                        self.gate.read(1000, 36)  # stored by _on_raw_read
                    except Exception as exc:
                        self._note_error("status poll 1000/36", exc)
                next_t += period
                delay = next_t - time.monotonic()
                if delay < 0:
                    next_t = time.monotonic()
                    delay = 0.0
                self._stop_evt.wait(delay)

    # --------------------------------------------------------------------------- slow queue

    def _slow_entries(self) -> list[_SlowEntry]:
        d = self.config.mccd
        entries = [_SlowEntry(2000, 50, d.axis_ro_period_ms / 1000.0)]
        if d.fast_combined_period_ms > 0:
            entries.append(_SlowEntry(60001, 120, d.fast_combined_period_ms / 1000.0))
        entries.append(_SlowEntry(50000, 26, d.system_rw_period_ms / 1000.0))
        entries.append(_SlowEntry(10000, 18, d.zf_status_period_ms / 1000.0, needs_zf=True))
        return entries

    def _slow_loop(self) -> None:
        entries = self._slow_entries()
        with self.bus.priority(Priority.SLOW):
            while not self._stop_evt.is_set():
                if self.link is Link.CONNECTED:
                    for e in entries:
                        now = time.monotonic()
                        if now < e.next_t or self._stop_evt.is_set():
                            continue
                        e.next_t = now + e.period_s
                        if e.needs_zf and not self.params.zf_type:
                            continue
                        try:
                            words = self.gate.read(e.addr, e.count)
                        except Exception as exc:
                            # 11 §3.2: a failed 10000 read is not link loss; neither are the
                            # other slow blocks - only the 1000/36 poll age counts.
                            self._note_error(f"slow read {e.addr}/{e.count}", exc)
                            continue
                        self._store_slow(e.addr, list(words), time.monotonic())
                self._stop_evt.wait(0.01)

    def _store_slow(self, addr: int, words: list[int], t: float) -> None:
        if addr == 2000:
            with self._lock:
                self.axis_ro = words
                self.axis_ro_t = t
            self._update_positions(words)
        elif addr == 50000:
            with self._lock:
                self.system_rw = words
            self._apply_system_rw(words)
        elif addr == 60001:
            with self._lock:
                self.fast_combined = words
        elif addr == 10000:
            with self._lock:
                self.zf_status = words

    def _update_positions(self, axis_ro: Sequence[int]) -> None:
        """Homed bits and trusted positions for the gate (A2 §3.1, docs/DECISIONS.md D1/D2)."""
        m = self.config.motion
        homed = {
            s for s in self.gate.config.jog_axis_indices if axis_ro[AXIS_RO_WORDS * s] & 0x8000
        }
        positions: dict[int, int] = {}
        if m.position_scale_verified:
            for s in homed:
                counts = _s32(int(axis_ro[AXIS_RO_WORDS * s + 2]))
                positions[s] = C.trunc_int(counts / m.position_counts_per_mm[s] * self.params.k)
        with self.gate._lock:
            self.gate.homed_slots = homed
            self.gate.homed = self.gate.config.jog_axis_indices <= homed
            self.gate.positions_word = positions

    # ----------------------------------------------------------------------------- watchdog

    def _machine_state(self) -> MachineState | None:
        with self._lock:
            b, ro = self.block1000, self.axis_ro
        if b is None or ro is None:
            return None
        return derive_machine_state(b, ro)

    def _moving_possible(self) -> bool:
        state = self._machine_state()
        return (
            bool(self.gate.leases())
            or self._homing is not None
            or self.gate.arming.state is not ArmState.DISARMED
            or (state is not None and state is not MachineState.READY)
        )

    def _watchdog_loop(self) -> None:
        with self.bus.priority(Priority.URGENT):
            while not self._stop_evt.wait(_WATCHDOG_PERIOD_S):
                try:
                    self._watchdog_once(time.monotonic())
                except Exception:  # pragma: no cover - never let the watchdog die
                    log.exception("watchdog")

    def _watchdog_once(self, now: float) -> None:
        d = self.config.mccd
        # 1. comm loss (PORT-PLAN §8.2): the 1000/36 poll is the link.
        if self.link is Link.CONNECTED:
            last = self.gate.last_status_ok or self.connected_at or now
            age = now - last
            if age > d.watchdog_timeout_ms / 1000.0:
                self._set_link(Link.LINK_LOST, f"status poll stale {age:.2f} s")
                self._trip("comm loss: status poll stale", comm_loss=True)
        # 2. disarm by the gate (card alarm / E-stop) aborts homing.
        if self._homing is not None and self.gate.arming.state is ArmState.DISARMED:
            self._cancel_homing("disarmed")
        # 3. axis fault bits while jogging, DI alarms (11 §4.7 rows 5/6).
        if self.link is Link.CONNECTED:
            with self._lock:
                b, ro = self.block1000, self.axis_ro
            if b is not None:
                state = derive_machine_state(b, ro) if ro is not None else None
                jogging = bool(self.gate.leases()) or state is MachineState.JOG
                reasons = watchdog_reasons(
                    b, ro, jogging=jogging, di_alarms=d.watchdog_di_alarms, alarm_words=False
                )
                key = "; ".join(reasons) or None
                if key is not None and key != self._trip_key and self._moving_possible():
                    self._trip(key)
                self._trip_key = key
        # 4. input sources: silence (11 N7).
        for src in list(self._sources):
            if (
                src.silence_timeout_s is not None
                and src.slots
                and now - src.last_seen > src.silence_timeout_s
            ):
                self.stop_source(src, f"{src.name} silent > {src.silence_timeout_s:.2f} s")
        # 5. deadman leases / FIFO comm loss (SafeMccClient.service, PORT-PLAN §8.2). Last:
        # it takes the gate lock, which a write may hold for a whole retry ladder.
        before = self.gate.leases()
        self.gate.service(now)
        for idx in set(before) - set(self.gate.leases()):
            self._jog_speed.pop(idx, None)
            for src in list(self._sources):
                src.release(idx)
        # housekeeping (never wait for the gate lock here)
        if len(self.gate.write_log) > 2 * _LOG_KEEP and self.gate._lock.acquire(blocking=False):
            try:
                del self.gate.write_log[:-_LOG_KEEP]
            finally:
                self.gate._lock.release()
        if len(self.gate.arming.history) > 2 * _LOG_KEEP:
            del self.gate.arming.history[:-_LOG_KEEP]

    def _trip(self, reason: str, *, comm_loss: bool = False) -> None:
        """Watchdog reaction: disarm now, stop (PORT-PLAN §8.2), abort homing and jogs."""
        log.error("watchdog: %s -> stop + disarm", reason)
        moving = self._moving_possible()
        self.gate.invalidate_motion()
        self._cancel_homing(reason)
        self.gate.arming.disarm(f"watchdog: {reason}")
        for src in list(self._sources):
            src.take_slots()
        self._jog_speed.clear()
        if not moving:
            return
        with self._lock:
            self._stop_gen += 1
            gen = self._stop_pending = self._stop_gen

        def send() -> None:
            ok = True
            with self.bus.priority(Priority.URGENT):
                if comm_loss:
                    # Minimal set: each vector may run the full retry ladder on a dead link.
                    seq = [C.fifo_stop()] if self.gate.fifo_running else []
                    seq.append(C.stop_all(0, self.params))
                    for cmd in seq:
                        try:
                            self.gate.send(cmd)
                        except Exception as exc:
                            ok = ok and isinstance(exc, CardRefused)
                            self._note_error(f"watchdog {cmd.name}", exc)
                else:
                    out = self.gate.stop()
                    ok = all(isinstance(e, CardRefused) for _, e in out.failed)
                    for cmd, exc in out.failed:
                        self._note_error(f"watchdog {cmd.name}", exc)
            if ok:
                self._clear_pending_stop(gen)

        threading.Thread(target=send, name="mccd-watchdog-stop", daemon=True).start()

    # ------------------------------------------------------------------------ input sources

    def register_input_source(
        self, name: str, silence_timeout_s: float | None = None
    ) -> InputSource:
        """New input source (pendant: pass ``config.mccd.input_silence_timeout_ms / 1000``)."""
        src = InputSource(self, name, silence_timeout_s)
        with self._lock:
            self._sources.append(src)
        return src

    def unregister_input_source(self, src: InputSource) -> None:
        """Forget a source (its jogs are stopped first)."""
        self.stop_source(src, f"{src.name} unregistered")
        with self._lock:
            with contextlib.suppress(ValueError):
                self._sources.remove(src)

    def stop_source(self, src: InputSource, reason: str) -> None:
        """Per-axis stop of every jog ``src`` started (11 N7, A1 §4.1)."""
        slots = src.take_slots()
        if not slots:
            return
        log.warning("input source lost (%s): stopping axes %s", reason, sorted(slots))
        with self.bus.priority(Priority.URGENT):
            for slot in sorted(slots):
                v = self._jog_speed.pop(slot, 0)
                try:
                    self.gate.jog_release([slot], v)
                except Exception as exc:
                    self._note_error(f"stop {src.name} axis {slot}", exc)

    def _on_open(self, conn: Connection) -> None:
        conn.context["source"] = self.register_input_source(f"ipc#{conn.id}")

    def _on_close(self, conn: Connection) -> None:
        src = conn.context.get("source")
        if isinstance(src, InputSource):
            self.unregister_input_source(src)

    # ------------------------------------------------------------------------------- status

    def snapshot(self) -> StatusSnapshot:
        """Current decoded status (11 §4)."""
        now = time.monotonic()
        with self._lock:
            b = list(self.block1000) if self.block1000 is not None else None
            ro = list(self.axis_ro) if self.axis_ro is not None else None
            ro_t, sysrw, zf = self.axis_ro_t, self.system_rw, self.zf_status
            link, err = self.link, self.last_error
        leases = self.gate.leases()
        last = self.gate.last_status_ok
        reasons: list[str] = []
        if b is not None:
            state = derive_machine_state(b, ro) if ro is not None else None
            reasons = watchdog_reasons(
                b,
                ro,
                jogging=bool(leases) or state is MachineState.JOG,
                di_alarms=self.config.mccd.watchdog_di_alarms,
            )
        homing = self._homing
        return StatusSnapshot.build(
            t=time.time(),
            link=str(link),
            arm_state=str(self.gate.arming.state),
            estop_latched=self.gate.arming.estop_latched,
            poll_age_s=None if last is None else now - last,
            block1000=b,
            axis_ro=ro,
            axis_ro_age_s=None if ro_t is None else now - ro_t,
            system_rw=sysrw,
            zf_status=zf,
            fifo_running=self.gate.fifo_running,
            machine_fault=self.gate.machine_fault,
            watchdog=reasons,
            homing=() if homing is None else homing.slots,
            jogs=leases,
            last_error=err,
        )

    def _publish_loop(self) -> None:
        while not self._stop_evt.wait(_PUBLISH_TICK_S):
            server = self.server
            if server is None:
                continue
            due = server.subscribers_due(time.monotonic())
            if not due:
                continue
            try:
                data = self.snapshot().to_json()
            except Exception:  # pragma: no cover
                log.exception("snapshot")
                continue
            for conn in due:
                conn.push_event("status", data)

    # ---------------------------------------------------------------------------- homing

    def _cancel_homing(self, reason: str) -> None:
        job = self._homing
        if job is not None:
            job.error = job.error or f"cancelled: {reason}"
            job.cancel.set()
            self._homing = None

    def _home_run(self, job: _HomingJob) -> None:
        timeout = self.config.mccd.home_timeout_ms / 1000.0
        try:
            with self.bus.priority(Priority.COMMAND), self.gate.motion_guard(job.epoch):
                for slot in job.slots:
                    if job.cancel.is_set():
                        return
                    job.current = slot
                    with self._motion_lock:
                        t_send = time.monotonic()
                        self._send_motion(C.home_axis(slot))
                    seen_moving = False
                    while True:
                        if job.cancel.wait(0.02):
                            return
                        with self._lock:
                            ro, t = self.axis_ro, self.axis_ro_t
                        if ro is not None and t is not None and t > t_send:
                            word = int(ro[AXIS_RO_WORDS * slot])
                            busy = bool((word >> 16) & 0xFF)
                            homed = bool(word & 0x8000)
                            if busy or not homed:
                                seen_moving = True
                            elif seen_moving:
                                break
                        if time.monotonic() - t_send > timeout:
                            job.error = f"homing axis {slot} timed out"
                            log.error(job.error)
                            with self.bus.priority(Priority.URGENT):
                                self.gate.stop()
                            return
                    job.done.append(slot)
        except Exception as exc:
            job.error = f"homing failed: {exc}"
            log.error(job.error)
        finally:
            job.current = None
            if self._homing is job:
                self._homing = None

    # ------------------------------------------------------------------------------ IPC

    def handle(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        """Dispatch one IPC request (see :mod:`nexcut.mccd.ipc` for the wire format)."""
        cmd = req.get("cmd")
        fn = self._commands.get(cmd) if isinstance(cmd, str) else None
        if fn is None:
            raise IpcError("unknown_command", f"unknown command {cmd!r}")
        if conn is not None and isinstance(conn.context.get("source"), InputSource):
            conn.context["source"].touch()
        if cmd not in ("status", "ping", "jog_refresh", "subscribe", "unsubscribe"):
            log.info("IPC %s %s", cmd, {k: v for k, v in req.items() if k not in ("cmd", "id")})
        prio = (
            Priority.URGENT
            if cmd in ("stop", "estop", "disarm", "jog_continuous_stop")
            else Priority.COMMAND
        )
        epoch = self.gate.motion_epoch
        with self.bus.priority(prio), self.gate.motion_guard(epoch):
            try:
                return fn(conn, req)
            except SafetyViolation as exc:
                raise IpcError("refused", exc.reason) from exc
            except ArmingError as exc:
                raise IpcError("arming", str(exc)) from exc
            except CardBusy as exc:
                raise IpcError(
                    "busy", f"card refused while busy (exception 3, 08 §4.5): {exc}"
                ) from exc
            except McError as exc:
                raise IpcError("card", str(exc)) from exc

    # -- argument helpers

    @staticmethod
    def _arg_int(req: dict[str, Any], name: str, lo: int, hi: int) -> int:
        v = req.get(name)
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            raise IpcError("bad_request", f"'{name}' must be an integer in [{lo}, {hi}]")
        return v

    @staticmethod
    def _arg_float(req: dict[str, Any], name: str) -> float:
        v = req.get(name)
        if isinstance(v, bool) or not isinstance(v, int | float) or not math.isfinite(v):
            raise IpcError("bad_request", f"'{name}' must be a finite number")
        return float(v)

    @staticmethod
    def _arg_bool(req: dict[str, Any], name: str) -> bool:
        v = req.get(name)
        if not isinstance(v, bool):
            raise IpcError("bad_request", f"'{name}' must be true or false")
        return v

    def _arg_slot(self, req: dict[str, Any]) -> int:
        slot = self._arg_int(req, "slot", 0, 31)
        if slot not in self.gate.config.jog_axis_indices:
            raise IpcError("refused", f"axis {slot} is not jog-enabled (11 §3.1: slots 0/1)")
        return slot

    def _arg_speed(self, req: dict[str, Any]) -> float:
        speed = self._arg_float(req, "speed")
        limit = self.config.motion.max_jog_speed_mm_s
        if not 0 < speed <= limit:
            raise IpcError("bad_request", f"'speed' must be in (0, {limit}] mm/s")
        return speed

    def _source(self, conn: Connection | None) -> InputSource | None:
        src = None if conn is None else conn.context.get("source")
        return src if isinstance(src, InputSource) else None

    def _require_connected(self) -> None:
        if self.link is not Link.CONNECTED:
            raise IpcError("not_connected", f"card link is {self.link}")

    def _require_ready(self, slot: int | None = None, positive: bool | None = None) -> None:
        """Card-side gates mirrored PC-side (A1 §1: jog/home need runStatus 0)."""
        self._require_connected()
        with self._lock:
            b, ro = self.block1000, self.axis_ro
            ro_t = self.axis_ro_t
        if b is None or ro is None or ro_t is None:
            raise IpcError("busy", "axis status not read yet")
        if self._homing is not None:
            raise IpcError("busy", "homing job running (11 §2 V6: one motion at a time)")
        if ro_t <= self._last_motion_t:
            raise IpcError("busy", "axis status not refreshed since the last motion command")
        state = derive_machine_state(b, ro)
        if state is not MachineState.READY:
            raise IpcError("busy", f"machine state {state.name} (jog/home need READY, A1 §1)")
        if slot is not None and positive is not None:
            block_pos, block_neg = axis_limit_blocks(ro, slot)
            if (positive and block_pos) or (not positive and block_neg):
                raise IpcError(
                    "refused",
                    f"axis {slot} limit active in the {'+' if positive else '-'} direction "
                    "(11 §4.7 row 5)",
                )

    @contextlib.contextmanager
    def _one_motion(self) -> Iterator[None]:
        """Serialise READY check + send of motion commands (D8); a concurrent one is ``busy``."""
        if not self._motion_lock.acquire(blocking=False):
            raise IpcError("busy", "another motion command is being sent")
        try:
            yield
        finally:
            self._motion_lock.release()

    def _send_motion(self, cmd: C.CommandVector) -> None:
        """Send a motion vector through the gate and remember when (see ``_require_ready``)."""
        try:
            self.gate.send(cmd)
        except SafetyViolation:
            raise  # refused before the wire: nothing moves
        except BaseException:
            self._last_motion_t = time.monotonic()  # the card may have executed it
            raise
        self._last_motion_t = time.monotonic()

    def _jog_params(self, speed: float) -> MachineParams:
        return replace(self.params, jog_fast_speed=speed, jog_slow_speed=speed, is_fast_mode=True)

    # -- commands

    def _cmd_ping(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        return {
            "version": __version__,
            "protocol": PROTOCOL_VERSION,
            "commands": list(IPC_COMMANDS),
        }

    def _cmd_status(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        return self.snapshot().to_json()

    def _cmd_subscribe(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        if conn is None:
            raise IpcError("bad_request", "subscribe needs a connection")
        interval = req.get("interval_ms", 100)
        if (
            isinstance(interval, bool)
            or not isinstance(interval, int)
            or not 10 <= interval <= 60000
        ):
            raise IpcError("bad_request", "'interval_ms' must be an integer in [10, 60000]")
        conn.next_push = time.monotonic()
        conn.subscribe_interval_s = interval / 1000.0
        return {"interval_ms": interval}

    def _cmd_unsubscribe(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        if conn is not None:
            conn.subscribe_interval_s = None
        return {}

    def _cmd_arm_motion(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        self._require_connected()
        self.gate.arming.arm_motion()
        return {"arm_state": str(self.gate.arming.state)}

    def _cmd_disarm(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        self.gate.invalidate_motion()
        moving = self._moving_possible()
        self._cancel_homing("operator disarm")
        self.gate.arming.disarm("operator")
        sent: list[str] = []
        if moving and self.link is Link.CONNECTED:
            out = self._stop_all_tracked(estop=False)
            sent = [c.name for c in out.sent]
        return {"arm_state": str(self.gate.arming.state), "stop_sent": sent}

    def _cmd_jog_step(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        slot = self._arg_slot(req)
        mm = self._arg_float(req, "mm")
        speed = self._arg_speed(req)
        if mm == 0 or abs(mm) > self.config.motion.max_step_mm:
            raise IpcError(
                "bad_request", f"'mm' must be non-zero and |mm| <= {self.config.motion.max_step_mm}"
            )
        with self._one_motion():
            self._require_ready(slot, mm > 0)
            cmd = C.jog_step(slot, mm, self._jog_params(speed))
            self._send_motion(cmd)
        deadman = self.gate.has_lease(slot)
        if deadman:
            self._jog_speed[slot] = cmd.words[2]
            src = self._source(conn)
            if src is not None:
                src.claim(slot)
        return {"words": list(cmd.signed_words), "deadman": deadman}

    def _cmd_jog_continuous_start(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        slot = self._arg_slot(req)
        positive = self._arg_bool(req, "positive")
        speed = self._arg_speed(req)
        with self._one_motion():
            return self._jog_continuous_start(conn, slot, positive, speed)

    def _jog_continuous_start(
        self, conn: Connection | None, slot: int, positive: bool, speed: float
    ) -> Any:
        if self.gate.has_lease(slot):
            raise IpcError("busy", f"axis {slot} already jogging")
        self._require_ready(slot, positive)
        k = self.params.k
        remaining: float | None = None
        with self.gate._lock:
            pos = self.gate.positions_word.get(slot) if slot in self.gate.homed_slots else None
            lo, hi = self.gate.config.soft_limits_word[slot]
        if pos is not None:
            remaining = ((hi - pos) if positive else (pos - lo)) / k
            if remaining <= 0:
                raise IpcError("refused", f"axis {slot} at its soft limit (PORT-PLAN §8.2)")
        elif speed > self.config.motion.unhomed_max_jog_speed_mm_s:
            raise IpcError(
                "refused",
                f"axis {slot} not homed: continuous jog only at <= "
                f"{self.config.motion.unhomed_max_jog_speed_mm_s} mm/s (DECISIONS D1)",
            )
        cmd = C.jog_continuous(
            slot, positive, self._jog_params(speed), soft_limit_remaining_mm=remaining
        )
        if cmd.words[5] == 0:
            raise IpcError("refused", "jog distance truncates to 0")
        self._send_motion(cmd)
        self._jog_speed[slot] = cmd.words[2]
        src = self._source(conn)
        if src is not None:
            src.claim(slot)
        return {
            "words": list(cmd.signed_words),
            "deadman_ms": self.config.mccd.deadman_timeout_ms,
        }

    def _cmd_jog_refresh(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        slot = self._arg_slot(req)
        alive = self.gate.jog_keepalive(slot)
        src = self._source(conn)
        if not alive:
            self._jog_speed.pop(slot, None)
            if src is not None:
                src.release(slot)
        return {"alive": alive}

    def _cmd_jog_continuous_stop(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        slot = self._arg_slot(req)
        self._require_connected()
        v = self._jog_speed.pop(slot, 0)
        self.gate.jog_release([slot], v)
        for src in list(self._sources):
            src.release(slot)
        return {"stopped": slot}

    def _cmd_home(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        slots = req.get("slots")
        if (
            not isinstance(slots, list)
            or not slots
            or any(isinstance(s, bool) or not isinstance(s, int) for s in slots)
            or len(set(slots)) != len(slots)
        ):
            raise IpcError("bad_request", "'slots' must be a non-empty list of distinct integers")
        bad = [s for s in slots if s not in self.gate.config.home_slots]
        if bad:
            raise IpcError("refused", f"home of slots {bad} not allowed (11 §2 V6/V7/V10)")
        with self._one_motion():
            if self._homing is not None:
                raise IpcError("busy", "homing already running")
            self._require_ready()
            if not self.gate.arming.allows(Policy.MOTION):
                raise IpcError(
                    "arming", f"home needs MOTION_ARMED (state {self.gate.arming.state})"
                )
            epoch = self.gate.guarded_epoch()
            job = _HomingJob(slots, self.gate.motion_epoch if epoch is None else epoch)
            self._homing = job
        job.thread = threading.Thread(
            target=self._home_run, args=(job,), name="mccd-home", daemon=True
        )
        job.thread.start()
        return {"homing": list(job.slots)}

    def _stop_all_tracked(self, *, estop: bool) -> StopOutcome:
        v_last = max(self._jog_speed.copy().values(), default=0)
        out = self.gate.estop(v_last_word=v_last) if estop else self.gate.stop(v_last_word=v_last)
        self._jog_speed.clear()
        for src in list(self._sources):
            src.take_slots()
        return out

    def _cmd_stop(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        self.gate.invalidate_motion()
        self._cancel_homing("stop")
        out = self._stop_all_tracked(estop=False)
        return {
            "sent": [c.name for c in out.sent],
            "failed": [[c.name, str(e)] for c, e in out.failed],
        }

    def _cmd_estop(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        self.gate.invalidate_motion()
        self._cancel_homing("estop")
        out = self._stop_all_tracked(estop=True)
        return {
            "estop_latched": self.gate.arming.estop_latched,
            "sent": [c.name for c in out.sent],
            "failed": [[c.name, str(e)] for c, e in out.failed],
        }

    def _cmd_ack_estop(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        # The release must be seen on a live status read (A1 §4.4 mp124, PORT-PLAN §8.2):
        # a block 1000 from before a link loss says nothing about the E-stop now.
        self._require_connected()
        last = self.gate.last_status_ok
        if last is None or time.monotonic() - last > self.config.mccd.watchdog_timeout_ms / 1000.0:
            raise IpcError("not_connected", "no fresh status read: cannot verify the E-stop input")
        with self._lock:
            b = self.block1000
        if b is None:
            raise IpcError("not_connected", "no status block read yet")
        if int(b[Status.ALARM_1]) & (1 << 30):
            raise IpcError("refused", "card E-stop still active (1006 bit 30): release it first")
        self.gate.arming.acknowledge_estop()
        return {
            "estop_latched": self.gate.arming.estop_latched,
            "arm_state": str(self.gate.arming.state),
        }

    def _cmd_read_block(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        addr = self._arg_int(req, "addr", 0, 0xFFFFFFFF)
        n = self._arg_int(req, "n", 1, 1000)
        d = classify_read(addr, n)
        if d.policy is Policy.DENY:
            raise IpcError("refused", d.reason)
        self._require_connected()
        words = self.gate.read(addr, n)
        return {"addr": addr, "words": [int(w) for w in words]}

    def _cmd_set_do(self, conn: Connection | None, req: dict[str, Any]) -> Any:
        port = self._arg_int(req, "port", 1, 26)
        on = self._arg_bool(req, "on")
        cfg = self.gate.config
        if port not in cfg.laser_do_ports | cfg.motion_do_ports:
            raise IpcError("refused", f"DO{port} is not in the allow-list (11 §2 V11)")
        if on and port in cfg.laser_do_ports:
            raise IpcError(
                "refused", f"DO{port} is a laser output: LASER_ARMED is not reachable over IPC"
            )
        self._require_connected()
        self.gate.send(C.do_set(port, on, laser_ports=cfg.laser_do_ports))
        return {"port": port, "on": on}
