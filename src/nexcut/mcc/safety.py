"""Safety gate in front of the MCC100 transaction client (PORT-PLAN §8.2, 11-static-findings §3).

Three pieces:

* :class:`ArmingStateMachine` - ``DISARMED`` -> ``MOTION_ARMED`` -> ``LASER_ARMED`` with the
  rules of PORT-PLAN §8.2: laser arming only with an explicit confirmation *and* a running
  job token; auto-disarm of the laser on stop / job end; full disarm on alarm, comm loss
  and E-stop; E-stop latched until acknowledged (``mp124``, A1 §4.4).
* :func:`classify_write` / :func:`classify_read` - re-derive, from the raw words, which arming
  level a register access needs, or the deny reason (11 §3.1-§3.3). Builders' metadata is
  not trusted.
* :class:`SafeMccClient` - wraps any object with ``read(addr, count)`` and
  ``write(addr, words)`` (normally :class:`nexcut.mcc.transaction.McTransaction`). Every write
  is checked **before framing**, logged with its frame bytes, and refused writes raise
  :class:`SafetyViolation`. FIFO frames (0x66) have laser records stripped unless
  ``LASER_ARMED`` (PORT-PLAN §8.2 dry run: duty 0, no DO9 / laser-gate record, no
  ``9999[3/0x11]``). FIFO items must match the item grammar of 11 §5.2 exactly.

The wrapper also carries the run-time guards of PORT-PLAN §8.2 that need time or card
status: the continuous-jog **deadman** (stop after 200 ms without a key event), the
**comm-loss watchdog** (no streaming, stopFifo + disarm when block 1000 is > 1 s stale),
**card alarms** (11 §4.2/§4.3: stop + disarm, bit 30 latches the E-stop, 1031 == 1 refuses
motion) and **PC-side soft limits** on absolute targets and on relative jogs from a known
position. A supervisor thread services the deadman/watchdog while something needs it;
:meth:`SafeMccClient.service` does the same work synchronously for a poll loop or tests.
"""

from __future__ import annotations

import array
import contextlib
import heapq
import logging
import secrets
import threading
import time
import zlib
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol

from nexcut.mcc import commands as C
from nexcut.mcc.commands import CommandVector, MachineParams, Policy
from nexcut.mcc.dissector import FifoFrame, FifoParseError, parse_fifo_words
from nexcut.mcc.framing import (
    FUNC_WRITE,
    REG_COMMAND,
    REG_FIFO_CONTROL,
    REG_FIFO_DATA,
    encode_vector,
)
from nexcut.mcc.registers import (
    HARD_PARAM_BASE,
    HARD_PARAM_BLOCK_WORDS,
    HARD_PARAM_STRIDE,
    LICENCE_FIRST,
    LICENCE_LAST,
    PARAM_STATUS_RESTART_REQUIRED,
    Status,
    fifo_program_running,
)
from nexcut.mcc.transaction import CardRefused

__all__ = [
    "ArmState",
    "ArmingError",
    "ArmingStateMachine",
    "Decision",
    "FifoDigest",
    "McClient",
    "SafeMccClient",
    "SafetyConfig",
    "SafetyViolation",
    "StopOutcome",
    "WriteLog",
    "WriteLogLimits",
    "WriteRecord",
    "classify_read",
    "classify_write",
    "strip_laser_records",
]

log = logging.getLogger("nexcut.mcc.safety")


class McClient(Protocol):
    """The part of :class:`~nexcut.mcc.transaction.McTransaction` the gate uses."""

    def read(self, addr: int, count: int) -> list[int]: ...

    def write(self, addr: int, words: Sequence[int]) -> None: ...


# ================================================================================================
# Arming state machine (PORT-PLAN §8.2)
# ================================================================================================


class ArmState(StrEnum):
    """Arming level. Order matters: each level includes the rights of the lower ones."""

    DISARMED = "DISARMED"
    MOTION_ARMED = "MOTION_ARMED"
    LASER_ARMED = "LASER_ARMED"


_RANK = {ArmState.DISARMED: 0, ArmState.MOTION_ARMED: 1, ArmState.LASER_ARMED: 2}


class ArmingError(RuntimeError):
    """An arming transition that the rules do not permit."""


@dataclass(frozen=True, slots=True)
class Transition:
    """One recorded state change."""

    t: float
    old: ArmState
    new: ArmState
    event: str


class ArmingStateMachine:
    """DISARMED / MOTION_ARMED / LASER_ARMED (PORT-PLAN §8.2). Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = ArmState.DISARMED
        self._job_token: str | None = None
        self._estop_latched = False
        self.history: list[Transition] = []

    # -- queries ---------------------------------------------------------------------------------

    @property
    def state(self) -> ArmState:
        """Current state."""
        return self._state

    @property
    def estop_latched(self) -> bool:
        """True from :meth:`estop` until :meth:`acknowledge_estop`."""
        return self._estop_latched

    @property
    def job_token(self) -> str | None:
        """Token of the running job, if any."""
        return self._job_token

    def allows(self, policy: Policy) -> bool:
        """Whether ``policy`` (ignoring the homed condition) is permitted now."""
        if policy in (Policy.ALWAYS, Policy.CONNECT):
            return True
        if policy is Policy.DENY or self._estop_latched:
            return False
        need = ArmState.LASER_ARMED if policy is Policy.LASER else ArmState.MOTION_ARMED
        return _RANK[self._state] >= _RANK[need]

    # -- transitions -----------------------------------------------------------------------------

    def _set(self, new: ArmState, event: str) -> None:
        old = self._state
        self._state = new
        self.history.append(Transition(time.monotonic(), old, new, event))
        if old is not new:
            log.warning("arming %s -> %s (%s)", old, new, event)

    def arm_motion(self) -> None:
        """DISARMED -> MOTION_ARMED. Refused while the E-stop latch is set."""
        with self._lock:
            if self._estop_latched:
                raise ArmingError("E-stop latched: acknowledge before arming")
            if self._state is ArmState.DISARMED:
                self._set(ArmState.MOTION_ARMED, "arm_motion")

    def begin_job(self) -> str:
        """Create the "job running" token needed for laser arming (requires MOTION_ARMED)."""
        with self._lock:
            if self._estop_latched or self._state is ArmState.DISARMED:
                raise ArmingError("begin_job needs MOTION_ARMED")
            if self._job_token is not None:
                raise ArmingError("a job is already running")
            self._job_token = secrets.token_hex(8)
            return self._job_token

    def arm_laser(self, *, confirmed: bool, job_token: str | None) -> None:
        """MOTION_ARMED -> LASER_ARMED: only with UI confirmation and the current job token."""
        with self._lock:
            if self._estop_latched:
                raise ArmingError("E-stop latched")
            if self._state is not ArmState.MOTION_ARMED and self._state is not ArmState.LASER_ARMED:
                raise ArmingError("arm_laser needs MOTION_ARMED")
            if not confirmed:
                raise ArmingError("laser arming needs an explicit operator confirmation")
            if self._job_token is None or job_token != self._job_token:
                raise ArmingError("laser arming needs the running job's token")
            self._set(ArmState.LASER_ARMED, "arm_laser")

    def end_job(self, job_token: str | None = None) -> None:
        """Job finished: drop the token; LASER_ARMED -> MOTION_ARMED."""
        with self._lock:
            if job_token is not None and job_token != self._job_token:
                raise ArmingError("unknown job token")
            self._job_token = None
            if self._state is ArmState.LASER_ARMED:
                self._set(ArmState.MOTION_ARMED, "end_job")

    def on_stop(self) -> None:
        """Stop / pause: the laser auto-disarms (PORT-PLAN §8.2); motion stays armed."""
        with self._lock:
            self._job_token = None
            if self._state is ArmState.LASER_ARMED:
                self._set(ArmState.MOTION_ARMED, "stop")

    def disarm(self, event: str = "disarm") -> None:
        """Any state -> DISARMED (alarm, comm loss, operator)."""
        with self._lock:
            self._job_token = None
            if self._state is not ArmState.DISARMED:
                self._set(ArmState.DISARMED, event)

    def on_alarm(self, reason: str = "alarm") -> None:
        """Card alarm word != 0 -> stop + disarm (PORT-PLAN §8.2 watchdog)."""
        self.disarm(f"alarm: {reason}")

    def on_comm_loss(self) -> None:
        """Status poll failing > 1 s -> disarm (PORT-PLAN §8.2)."""
        self.disarm("comm loss")

    def estop(self) -> None:
        """E-stop (UI or alarm_1 bit 30): disarm and latch (A1 §4.4, 11 §4.2)."""
        with self._lock:
            self._estop_latched = True
            self.disarm("estop")

    def acknowledge_estop(self) -> None:
        """Operator acknowledged ``mp124``; the machine stays DISARMED."""
        with self._lock:
            self._estop_latched = False
            self.history.append(
                Transition(time.monotonic(), self._state, self._state, "estop acknowledged")
            )


# ================================================================================================
# Classification (11 §3)
# ================================================================================================


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    """Per-machine safety knobs (defaults: this CF1390 / BkHardPara.xml, 11 §2/§3)."""

    laser_do_ports: frozenset[int] = frozenset({5, 6, 9})
    """DO5 fibre gate, DO6 red pointer, DO9 CO2 laser (A5 §5, A3 §6). ON needs LASER_ARMED.
    DO6 is included conservatively (a laser pointer), UNVERIFIED as a hazard class."""
    motion_do_ports: frozenset[int] = frozenset({1, 2, 3, 7})
    """DO1 AlarmSignal lamp, DO2 HighN2, DO3 HighAir, DO7 LowO2: ON needs MOTION_ARMED."""
    jog_axis_indices: frozenset[int] = frozenset({0, 1})
    home_slots: frozenset[int] = frozenset({0, 1})
    goto_axis_mask: int = 0x3
    max_speed_word: int = 3000 * 1000
    """FCP.MaxSpeed x K (11 §2 header)."""
    max_acc: int = 20000
    """FCP.MaxAcc."""
    max_jog_distance_word: int = 4_000_000
    """|d| limit for sub-cmd 3 (20 x JogFastSpeed x K, the vendor's continuous-jog length).
    UNVERIFIED as a physical bound; the real bound is the PC-side soft limit."""
    stop_vd_range: tuple[int, int] = (2000, 100000)
    """vd bounds of every vendor stop builder (A1 §4.1)."""
    allow_nonzero_da: bool = False
    """V12 DENY in M1 (gas pressure DA2 comes in M5)."""
    laser_da_channel_words: frozenset[int] = frozenset({0})
    """0x65 DA channel words that drive laser power: ``[9999,4,ch-1,mV]`` (VM 0x1003c760,
    A3 §6) with LGP.CO2LaserDAPort = 1 -> word 0. Non-zero needs LASER_ARMED."""
    fifo_laser_da_channel_words: frozenset[int] = frozenset({0, 1})
    """In-stream ``9999[4, ch, value]`` channel words stripped in dry run. Both words:
    the in-stream channel base is UNVERIFIED (A3 §6 verifier, 11 §5.2), so either could be
    the laser DA."""
    pwm_freq_default: int = 1234
    soft_limits_word: tuple[tuple[int, int], ...] = ((0, 1_371_000), (0, 950_000))
    """Per axis-list index (0 X, 1 Y): inclusive card-word bounds. hi = MAC/MAC_1
    SoftLimitMaxLen 1371/950 mm x K (01 §2.2 table), lo = 0 because GoOriginalDirection
    = 0 (MainApp loader 0x4b2508-0x4b25b3, 01 §2.2). UNVERIFIED: K = 1000 (reg 50017 must
    be re-read, 11 §3.2) and that the card's absolute origin is the home corner (11 §7
    capture step 4)."""
    deadman_timeout_s: float = 0.2
    """Continuous-jog key staleness before the per-axis stop is sent (PORT-PLAN §8.2)."""
    comm_loss_timeout_s: float = 1.0
    """Block-1000 status poll age that counts as comm loss (PORT-PLAN §8.2 watchdog)."""
    supervisor_period_s: float = 0.02
    """Supervisor thread period. UNVERIFIED choice (well inside the 200 ms deadman)."""


@dataclass(frozen=True, slots=True)
class Decision:
    """Outcome of a classification: ``policy`` required, or ``Policy.DENY`` with a reason."""

    policy: Policy
    reason: str
    needs_homed: bool = False


def _deny(reason: str) -> Decision:
    return Decision(Policy.DENY, reason)


def _do_policy(on_bits: int, cfg: SafetyConfig) -> Decision:
    """Policy for turning ``on_bits`` (DO word bits, port = bit+1) on."""
    if on_bits == 0:
        return Decision(Policy.ALWAYS, "outputs off")
    ports = {b + 1 for b in range(16) if (on_bits >> b) & 1}
    unknown = ports - cfg.laser_do_ports - cfg.motion_do_ports
    if unknown:
        return _deny(f"DO port(s) {sorted(unknown)} not in the allow-list")
    if ports & cfg.laser_do_ports:
        return Decision(Policy.LASER, f"laser DO {sorted(ports & cfg.laser_do_ports)} on")
    return Decision(Policy.MOTION, f"DO {sorted(ports)} on")


def _classify_command(w: Sequence[int], cfg: SafetyConfig, do_word: int | None) -> Decision:
    n = len(w)
    if n == 0:
        return _deny("empty command vector")
    sub = w[0]
    if sub == C.CMD_STOP:
        if n == 2 and w[1] == C.AXIS_MASK_ALL:
            return Decision(Policy.ALWAYS, "V5 stop all")
        lo, hi = cfg.stop_vd_range
        if (
            n == 5
            and 0 < w[1] <= C.AXIS_MASK_ALL
            and w[2] == 2
            and lo <= w[3] <= hi
            and w[4] == 10 * w[3]
        ):
            return Decision(Policy.ALWAYS, "V3/V4 stop")
        return _deny("malformed stop vector (only [1,0x1F] or [1,mask,2,vd,10vd])")
    if sub == C.CMD_HOME:
        if n == 3 and w[2] == 0 and w[1] in {1 << s for s in cfg.home_slots}:
            return Decision(Policy.MOTION, "V6 home one axis")
        return _deny("system / multi-axis / lift-table home not in M1 (V7/V10)")
    if sub == C.CMD_JOG:
        if n != 6:
            return _deny("jog vector must have 6 words")
        idx = w[1] & 0x7FFFFFFF
        absolute = bool(w[1] & C.ABSOLUTE_BIT)
        if idx not in cfg.jog_axis_indices or w[1] & 0x7FFFFFFF != idx:
            return _deny(f"jog axis word {w[1]:#x} not allowed (lift table / roll feeder DENY)")
        if not 0 < w[2] <= cfg.max_speed_word:
            return _deny(f"jog speed word {w[2]} outside (0, {cfg.max_speed_word}]")
        if not 0 < w[3] <= cfg.max_acc or w[4] != 10 * w[3]:
            return _deny("jog acc/jerk out of range or jerk != 10*acc")
        d = _s32(w[5])
        if not absolute and abs(d) > cfg.max_jog_distance_word:
            return _deny(f"jog distance {d} beyond {cfg.max_jog_distance_word}")
        if absolute:
            bad = _soft_limit_violation(idx, d, cfg)
            if bad:
                return _deny(bad)
            return Decision(Policy.MOTION_HOMED, "absolute single-axis move", needs_homed=True)
        return Decision(Policy.MOTION, "V1/V2 jog")
    if sub == C.CMD_GOTO:
        if (
            n == 9
            and w[1] == (cfg.goto_axis_mask | C.ABSOLUTE_BIT)
            and 0 < w[2] <= cfg.max_speed_word
            and 0 < w[3] <= cfg.max_acc
            and w[4] == 10 * w[3]
            and w[7] == 0
            and w[8] == 0
        ):
            bad = _soft_limit_violation(0, _s32(w[5]), cfg) or _soft_limit_violation(
                1, _s32(w[6]), cfg
            )
            if bad:
                return _deny(bad)
            return Decision(Policy.MOTION_HOMED, "V8 go-to", needs_homed=True)
        return _deny("go-to vector not of the M1 form [5,0x80000003,v,a,10a,x,y,0,0]")
    if sub == C.CMD_ZF_STOP:
        return Decision(Policy.ALWAYS, "V14 ZF stop") if n == 1 else _deny("[101] takes no args")
    if sub == C.CMD_9999 and n >= 2:
        misc = w[1]
        if misc == C.MISC_PROLOGUE and list(w) == [9999, 5, 0, 0]:
            return Decision(Policy.CONNECT, "V0 connect prologue")
        if misc == C.MISC_DO and n == 4:
            mask, value = w[2], w[3]
            if value & ~mask:
                return _deny("DO value has bits outside the mask")
            if mask == 0xFFFF:
                known = do_word if do_word is not None else 0
                newly_on = value & ~known & 0xFFFF
                # Outputs the port may drive (laser and gas/lamp) need their arming level
                # even when "kept": the DO word may be stale, and DISARMED means no DO
                # (PORT-PLAN §8.2). Unknown ports that are already on may be kept.
                allowlisted = value & _port_mask(cfg.laser_do_ports | cfg.motion_do_ports)
                d = _do_policy(newly_on | allowlisted, cfg)
                return (
                    d if d.policy is not Policy.ALWAYS else Decision(Policy.ALWAYS, "DO bulk off")
                )
            if mask & (mask - 1) or not 0 < mask <= 0x200:
                return _deny("DO write must address one port 1..10")
            return _do_policy(value, cfg)
        if misc == C.MISC_DA and n == 4:
            if w[2] not in (0, 1):
                return _deny("DA channel word must be 0 or 1")
            if w[3] == 0:
                return Decision(Policy.ALWAYS, "DA 0")
            if cfg.allow_nonzero_da and w[3] <= 10000:
                if w[2] in cfg.laser_da_channel_words:
                    return Decision(Policy.LASER, "laser DA set (LGP.CO2LaserDAPort, A3 §6)")
                return Decision(Policy.MOTION, "DA set")
            return _deny("non-zero DA not allowed in M1 (V12)")
        if misc in (C.MISC_PWM, C.MISC_PWM_5V) and n in (4, 5):
            if all(x == 0 for x in w[3:]):
                return Decision(Policy.ALWAYS, "PWM off")
            return Decision(Policy.LASER, "PWM duty > 0 (V13)")
        return _deny(f"9999 sub-command {misc} not allowed (13/16/1/9/... DENY, 11 §3.3)")
    return _deny(f"0x65 sub-command {sub} not allowed (102/107/117/118/103/104/109/7/4 DENY)")


def _s32(word: int) -> int:
    """u32 wire word -> int32."""
    return word - (1 << 32) if word & 0x80000000 else word


def _soft_limit_violation(axis_index: int, target_word: int, cfg: SafetyConfig) -> str | None:
    """Reason if ``target_word`` lies outside the PC-side soft limit of the axis (PORT-PLAN §8.2)."""
    if axis_index >= len(cfg.soft_limits_word):
        return f"no soft limit configured for axis index {axis_index}"
    lo, hi = cfg.soft_limits_word[axis_index]
    if not lo <= target_word <= hi:
        return f"target {target_word} on axis {axis_index} outside soft limit [{lo}, {hi}]"
    return None


def _port_mask(ports: Iterable[int]) -> int:
    m = 0
    for p in ports:
        if 1 <= p <= 16:
            m |= 1 << (p - 1)
    return m


def classify_write(
    addr: int,
    words: Sequence[int],
    cfg: SafetyConfig | None = None,
    *,
    do_word: int | None = None,
) -> Decision:
    """Required arming level for ``WRITE addr <- words``, or DENY (11 §3.1/§3.3).

    ``do_word`` is the last DO word read from reg 1005 (used to decide whether a DO bulk
    write turns anything on).
    """
    cfg = cfg or SafetyConfig()
    w = [int(x) & 0xFFFFFFFF for x in words]
    if addr == REG_COMMAND:
        return _classify_command(w, cfg, do_word)
    if addr == REG_FIFO_CONTROL:
        if w == [C.FIFO_STOP]:
            return Decision(Policy.ALWAYS, "FIFO stop")
        if w in ([C.FIFO_CLEAR], [C.FIFO_START]):
            return Decision(Policy.MOTION, "FIFO clear/start")
        return _deny("0x67 accepts only [1], [2], [3]")
    if addr == REG_FIFO_DATA:
        return Decision(Policy.MOTION, "FIFO frame") if w else _deny("empty FIFO frame")
    if addr in (150, 151):
        return _deny("150/151: FTC factory reset / commit, edge-seek buffer (A4 §1, C1)")
    if LICENCE_FIRST <= addr <= LICENCE_LAST:
        return _deny("licence / RTC area 59500-59599 (A4 §2-3)")
    if addr >= HARD_PARAM_BASE:
        return _deny("hardware-parameter area: read-compare only (A4 §5)")
    if 11000 <= addr < 11000 + 2 * 39:
        return _deny("FTC property write (A5 §2.2)")
    if addr == 100:
        return _deny("100 <- 9999 restart/apply: meaning unproven (A2 §2 row 31)")
    if 5000 <= addr <= 5012:
        return _deny("block 5000 e-stop/safety-decel writes (A2 §4)")
    if addr in (200, 201):
        return _deny("FTC test-data window (A5 §2.4)")
    return _deny(f"register {addr} is not in the write allow-list (PORT-PLAN §1)")


_READ_ALLOW: tuple[tuple[int, int, str], ...] = (
    (1000, 36, "status"),
    (1050, 3, "version/time"),
    (2000, 50, "axis RO"),
    (5000, 9, "block 5000 diagnostic"),
    (10000, 18, "ZF status"),
    (10028, 2, "ZF test-data count"),
    (11000, 39, "ZF properties"),
    (12002, 800, "FTC calibration table"),
    (50000, 26, "system RW"),
    (50200, 100, "axis RW"),
    (60001, 144, "fast combined read"),  # UNVERIFIED upper bound: fast reader uses words 0-143
)


def classify_read(addr: int, count: int) -> Decision:
    """Whether ``READ addr/count`` is allowed (11 §3.2) - returns ALWAYS or DENY."""
    if count <= 0:
        return _deny("count must be positive")
    end = addr + count
    if addr <= LICENCE_LAST and end > LICENCE_FIRST:
        return _deny("licence / RTC area 59500-59599: reads denied too (A4 §2-3)")
    if addr <= 151 and end > 151:
        return _deny("reg 151 edge-seek buffer: reads denied by default (11 §3.3)")
    if addr <= 200 and end > 200:
        return _deny("reg 200 test-data window (11 §3.3)")
    for start, n, name in _READ_ALLOW:
        if start <= addr and end <= start + n:
            return Decision(Policy.ALWAYS, name)
    if addr >= HARD_PARAM_BASE:
        off = addr - HARD_PARAM_BASE
        i, rel = divmod(off, HARD_PARAM_STRIDE)
        if i <= 31 and rel + count <= HARD_PARAM_BLOCK_WORDS:
            return Decision(Policy.ALWAYS, f"hardware-parameter block {i} (read-compare)")
    return _deny(f"READ {addr}/{count} is not in the read allow-list")


# ================================================================================================
# FIFO laser stripping
# ================================================================================================

_TICK = 3000
_FIFO_ARG_WORDS: dict[int, frozenset[int]] = {
    3000: frozenset({2}),
    3001: frozenset({0}),
    3002: frozenset({1}),
    9999: frozenset({3}),
    103: frozenset({2}),
    109: frozenset({2}),
    104: frozenset({2, 8}),
    105: frozenset({4}),
    106: frozenset({6}),
    108: frozenset({4}),
    118: frozenset({3}),
    2001: frozenset({2}),
}
"""Payload words per opcode, from the headers of 11 §5.2 (A3 §5). Any other length is
refused: the card's handling of an oversized item is unknown, and a length mismatch
would otherwise hide a laser record from the stripper."""
_ALLOWED_FIFO_OPCODES = frozenset(_FIFO_ARG_WORDS)
_LASER_ONLY_OPCODES = frozenset({2002, 2004, 3})
"""Fibre-only records (A3 §5); never emitted in this configuration. Lengths unknown."""
_FIFO_9999_SUBS = frozenset({C.MISC_DO, C.MISC_PWM, C.MISC_DA, C.MISC_DO_EXT, C.MISC_PWM_5V})
"""In-stream 9999 sub-records of 11 §5.2; others (e.g. 5, 16, 1) are refused."""


def _parse_fifo(words: Sequence[int]) -> FifoFrame:
    try:
        return parse_fifo_words(words, strict=True)
    except FifoParseError as exc:
        raise SafetyViolation(REG_FIFO_DATA, words, f"unparseable FIFO frame: {exc}") from exc


def _check_fifo_grammar(
    frame: FifoFrame, words: Sequence[int], cfg: SafetyConfig, *, laser_ok: bool
) -> None:
    """Refuse items outside the 11 §5.2 grammar (opcode, payload length, 9999 sub, DO port)."""
    allowed_do = _port_mask(cfg.laser_do_ports | cfg.motion_do_ports)
    for item in frame.items:
        op, args = item.opcode, item.args
        if op in _LASER_ONLY_OPCODES:
            if laser_ok:
                continue
            raise SafetyViolation(REG_FIFO_DATA, words, f"FIFO opcode {op} not allowed")
        if op not in _ALLOWED_FIFO_OPCODES:
            raise SafetyViolation(REG_FIFO_DATA, words, f"FIFO opcode {op} not allowed")
        if len(args) not in _FIFO_ARG_WORDS[op]:
            raise SafetyViolation(
                REG_FIFO_DATA, words, f"FIFO opcode {op} with {len(args)} payload words (11 §5.2)"
            )
        if op == 9999:
            sub = args[0]
            if sub not in _FIFO_9999_SUBS:
                raise SafetyViolation(
                    REG_FIFO_DATA, words, f"FIFO 9999 sub-record {sub} not allowed"
                )
            if sub == C.MISC_DO and (args[1] | args[2]) & ~allowed_do & 0xFFFFFFFF:
                raise SafetyViolation(
                    REG_FIFO_DATA, words, f"FIFO DO record {args} addresses a port not allow-listed"
                )
            if sub == C.MISC_DO_EXT and args[2]:
                raise SafetyViolation(REG_FIFO_DATA, words, "FIFO extended DO on (11 §3.3)")


def strip_laser_records(
    words: Sequence[int], cfg: SafetyConfig | None = None, *, laser_ok: bool = False
) -> tuple[list[int], int]:
    """Neutralise a FIFO frame for dry run (PORT-PLAN §8.2, 11 §3.1 row 0x66).

    ``words`` = ``[frame_id, items...]``. Returns the new words and the number of changed
    items. Ticks keep their motion and frequency but get duty 0; ``9999[2, mask, value]``
    records lose their laser DO bits (dropped when nothing else is left); ``9999[3/0x11, ...]``
    PWM records are removed; non-zero DA records on possible laser channels are removed.
    Items outside the 11 §5.2 grammar raise :class:`SafetyViolation`; fibre-only opcodes
    raise unless ``laser_ok`` (then they are dropped and counted, which lets the caller
    count the laser items of a frame sent unmodified while LASER_ARMED).
    """
    cfg = cfg or SafetyConfig()
    frame = _parse_fifo(words)
    _check_fifo_grammar(frame, words, cfg, laser_ok=laser_ok)
    laser_mask = _port_mask(cfg.laser_do_ports)
    out = [frame.frame_id]
    changed = 0
    for item in frame.items:
        op, args = item.opcode, list(item.args)
        if op in _LASER_ONLY_OPCODES:
            changed += 1
            continue
        if op == _TICK:
            if args[1] & 0xFFFF:
                args[1] &= 0xFFFF0000
                changed += 1
        elif op == 9999:
            sub = args[0]
            if sub == C.MISC_DO and args[2] & laser_mask:
                # Laser bits go, even if outside the mask: only "off" may remain.
                args[1] &= ~laser_mask & 0xFFFFFFFF
                args[2] &= ~laser_mask & 0xFFFFFFFF
                changed += 1
                if not args[1]:
                    continue
            elif sub in (C.MISC_PWM, C.MISC_PWM_5V):
                changed += 1
                continue
            elif sub == C.MISC_DA and args[1] in cfg.fifo_laser_da_channel_words and args[2]:
                changed += 1
                continue
        out.append(((len(args) * 4) << 16) | op)
        out.extend(args)
    return out, changed


# ================================================================================================
# The wrapper
# ================================================================================================


class SafetyViolation(PermissionError):
    """A register access the safety gate refused."""

    def __init__(self, addr: int, words: Sequence[int], reason: str) -> None:
        self.addr = addr
        self.words = tuple(int(x) & 0xFFFFFFFF for x in words)
        self.reason = reason
        super().__init__(f"refused write/read at {addr:#x}: {reason}")


@dataclass(frozen=True, slots=True)
class WriteRecord:
    """One logged write attempt (sent or refused)."""

    t: float
    addr: int
    words: tuple[int, ...]
    frame: bytes
    """Encoded request with the sequence number the client was about to use (b"" if refused)."""
    state: ArmState
    decision: str
    """``sent``, ``refused`` or ``failed`` (transport error after the gate passed)."""
    reason: str
    stripped: int = 0
    """Laser items neutralised in a FIFO frame (dry run, not ``LASER_ARMED``)."""
    laser_items: int = 0
    """Laser items a FIFO frame carried to the card unmodified while ``LASER_ARMED`` (D13 §4)."""
    digest: FifoDigest | None = None
    """Set when :class:`WriteLog` compacted an old FIFO record; ``words`` and ``frame`` are
    then empty and this carries what identifies the frame (STATUS §5 task 9)."""


@dataclass(frozen=True, slots=True)
class FifoDigest:
    """What is kept of a ``0x66`` frame once it is older than the full-record window."""

    frame_id: int
    """``words[0]`` - the frame id the card acknowledges in reg 1015."""
    n_words: int
    words_crc32: int
    """``zlib.crc32`` of the words as sent (after dry-run stripping), little-endian u32."""
    frame_bytes: int
    """Length of the encoded request (1 206 for a full 298-word frame)."""
    frame_crc32: int
    """``zlib.crc32`` of the encoded request bytes (sequence number included)."""
    first_opcodes: tuple[int, ...]
    """Opcodes of the first (up to) three items, from the item headers."""


def _u32_bytes(words: Sequence[int]) -> bytes:
    try:
        return array.array("I", words).tobytes()
    except (OverflowError, TypeError):
        return array.array("I", (int(w) & 0xFFFFFFFF for w in words)).tobytes()


def fifo_digest(words: Sequence[int], frame: bytes) -> FifoDigest:
    """Compact identity of a ``0x66`` payload ``[frame_id, items...]`` (11 §5.2)."""
    ops: list[int] = []
    i = 1
    while i < len(words) and len(ops) < 3:
        h = int(words[i]) & 0xFFFFFFFF
        ops.append(h & 0xFFFF)
        i += 1 + (h >> 16) // 4
    return FifoDigest(
        frame_id=int(words[0]) & 0xFFFFFFFF if words else 0,
        n_words=len(words),
        words_crc32=zlib.crc32(_u32_bytes(words)),
        frame_bytes=len(frame),
        frame_crc32=zlib.crc32(frame),
        first_opcodes=tuple(ops),
    )


@dataclass(frozen=True, slots=True)
class WriteLogLimits:
    """Caps of :class:`WriteLog`. Defaults bound the log at roughly 10 MB (measured in
    ``tests/test_bounded_logs.py``)."""

    fifo_full_keep: int = 32
    """The most recent ``0x66`` frames kept in full (words and frame bytes)."""
    max_fifo_digests: int = 20_000
    """Older ``0x66`` frames kept as a :class:`FifoDigest` (~8 min at the 250 µs tick)."""
    max_records: int = 10_000
    """Every other record - all non-FIFO writes and every refused FIFO frame, in full."""

    def __post_init__(self) -> None:
        if min(self.fifo_full_keep, self.max_fifo_digests, self.max_records) < 1:
            raise ValueError(f"write-log limits must be >= 1: {self}")


class WriteLog:
    """Bounded, ordered audit log of :class:`SafeMccClient` writes (STATUS §5 task 9, D13 §4).

    * Every write that is not a sent/failed ``0x66`` frame - commands, register writes,
      ``0x67`` FIFO control, refusals of anything including FIFO frames - is kept **in full**,
      the newest ``max_records`` of them.
    * The newest ``fifo_full_keep`` sent/failed ``0x66`` frames are kept in full; older ones
      are replaced by a record with empty ``words``/``frame`` and a :class:`FifoDigest`; the
      newest ``max_fifo_digests`` of those are kept.
    * Iteration, indexing and slicing see one sequence in write order. What fell off either
      end is counted (``dropped_fifo``, ``dropped_other``, ``compacted``) and reported by
      :meth:`summary`, so a review can tell an empty stretch from a trimmed one.

    Thread-safe: readers get a snapshot, so a test or the daemon can iterate while a feeder
    thread appends.
    """

    def __init__(self, limits: WriteLogLimits | None = None, **kw: int) -> None:
        self.limits = limits or WriteLogLimits(**kw)
        self._lock = threading.Lock()
        self._n = 0
        self._other: deque[tuple[int, WriteRecord]] = deque()
        self._fifo_full: deque[tuple[int, WriteRecord]] = deque()
        self._fifo_digest: deque[tuple[int, WriteRecord]] = deque()
        self.compacted = 0
        self.dropped_fifo = 0
        self.dropped_other = 0

    @property
    def dropped(self) -> int:
        return self.dropped_fifo + self.dropped_other

    def append(self, rec: WriteRecord) -> None:
        lim = self.limits
        with self._lock:
            entry = (self._n, rec)
            self._n += 1
            if rec.addr == REG_FIFO_DATA and rec.decision != "refused" and rec.digest is None:
                self._fifo_full.append(entry)
                if len(self._fifo_full) > lim.fifo_full_keep:
                    idx, old = self._fifo_full.popleft()
                    small = replace(
                        old, words=(), frame=b"", digest=fifo_digest(old.words, old.frame)
                    )
                    self.compacted += 1
                    self._fifo_digest.append((idx, small))
                    if len(self._fifo_digest) > lim.max_fifo_digests:
                        self._fifo_digest.popleft()
                        self.dropped_fifo += 1
            elif rec.digest is not None:
                self._fifo_digest.append(entry)
                if len(self._fifo_digest) > lim.max_fifo_digests:
                    self._fifo_digest.popleft()
                    self.dropped_fifo += 1
            else:
                self._other.append(entry)
                if len(self._other) > lim.max_records:
                    self._other.popleft()
                    self.dropped_other += 1

    def _snapshot(self) -> list[WriteRecord]:
        with self._lock:
            parts = (list(self._other), list(self._fifo_digest), list(self._fifo_full))
        return [rec for _, rec in heapq.merge(*parts, key=lambda e: e[0])]

    def __iter__(self) -> Iterator[WriteRecord]:
        return iter(self._snapshot())

    def __len__(self) -> int:
        with self._lock:
            return len(self._other) + len(self._fifo_digest) + len(self._fifo_full)

    def __getitem__(self, i: int | slice) -> WriteRecord | list[WriteRecord]:  # type: ignore[override]
        if i == -1:
            with self._lock:
                tails = [d[-1] for d in (self._other, self._fifo_digest, self._fifo_full) if d]
            if not tails:
                raise IndexError("write log is empty")
            return max(tails, key=lambda e: e[0])[1]
        return self._snapshot()[i]

    def clear(self) -> None:
        with self._lock:
            self._other.clear()
            self._fifo_full.clear()
            self._fifo_digest.clear()

    def summary(self) -> dict[str, int]:
        """Counts for a status snapshot or an incident review."""
        with self._lock:
            return {
                "records": len(self._other) + len(self._fifo_digest) + len(self._fifo_full),
                "written": self._n,
                "fifo_full": len(self._fifo_full),
                "fifo_digests": len(self._fifo_digest),
                "compacted": self.compacted,
                "dropped_fifo": self.dropped_fifo,
                "dropped_other": self.dropped_other,
            }


@dataclass(slots=True)
class StopOutcome:
    """Result of :meth:`SafeMccClient.stop` (best effort: every vector is tried)."""

    sent: list[CommandVector] = field(default_factory=list)
    failed: list[tuple[CommandVector, Exception]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True if every vector reached the card."""
        return not self.failed


class SafeMccClient:
    """Allow-list/deny-list gate around a transaction client (11 §3, PORT-PLAN §8.2).

    Not a security boundary against code in the same process: ``client`` stays reachable.
    The enforcement boundary is the mccd process owning the socket (PORT-PLAN §2.3).
    """

    def __init__(
        self,
        client: McClient,
        arming: ArmingStateMachine | None = None,
        config: SafetyConfig | None = None,
        *,
        params: MachineParams | None = None,
        on_write: Callable[[WriteRecord], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        supervise: bool = True,
        log_limits: WriteLogLimits | Mapping[str, int] | None = None,
    ) -> None:
        self.client = client
        self.arming = arming or ArmingStateMachine()
        self.config = config or SafetyConfig()
        self.params = params or MachineParams()
        self.on_write = on_write
        if log_limits is not None and not isinstance(log_limits, WriteLogLimits):
            log_limits = WriteLogLimits(**log_limits)
        self.write_log = WriteLog(log_limits)
        """Bounded audit log of every write attempt (:class:`WriteLog`, STATUS §5 task 9)."""
        self.homed = False
        """Set by the caller from the axis-status homed bits (A2 §3.1) - gates V8."""
        self.positions_word: dict[int, int] = {}
        """Axis-list index -> current position in card words (µm at K=1000), set by the poll
        loop once homed. Used to check relative jog targets against the soft limits
        (PORT-PLAN §8.2). Empty = unknown (relative jogs are then not limit-checked)."""
        self.fifo_running = False
        """Tracked from 0x67 writes and reg 1019 reads (A2 §2 row 19)."""
        self.card_fifo_has_laser = False
        """A frame with laser items went to the card FIFO while LASER_ARMED and the FIFO has
        not been cleared since: 0x67 <- [2] then needs LASER_ARMED."""
        self.last_do_word: int | None = None
        """Reg 1005 from the last status read through this wrapper (A2 §2 row 5)."""
        self.last_status_ok: float | None = None
        """Clock time of the last successful block-1000 read covering 1006/1007."""
        self.machine_fault: str | None = None
        """Active card alarm / restart-required condition (11 §4.2, §4.3, §4.1 row 31).
        While set, only ALWAYS/CONNECT writes pass."""
        self._last_alarm: tuple[int, int] = (0, 0)
        self._leases: dict[int, tuple[float, int]] = {}
        """Deadman leases: axis index -> (deadline, jog speed word)."""
        self._clock = clock
        self._supervise = supervise
        self._lock = threading.RLock()
        self._sup_lock = threading.Lock()
        self._sup_thread: threading.Thread | None = None
        self._sup_stop = threading.Event()

    # -- reads -----------------------------------------------------------------------------------

    def read(self, addr: int, count: int) -> list[int]:
        """READ with the read allow-list; tracks DO word, FIFO state, alarms, poll freshness."""
        d = classify_read(addr, count)
        if d.policy is Policy.DENY:
            log.warning("REFUSED READ %d/%d: %s", addr, count, d.reason)
            raise SafetyViolation(addr, (), d.reason)
        words = self.client.read(addr, count)
        if len(words) != count:
            return words

        def covers(reg: Status) -> bool:
            return addr <= reg.addr < addr + count

        def word(reg: Status) -> int:
            return int(words[reg.addr - addr]) & 0xFFFFFFFF

        if covers(Status.DO):
            self.last_do_word = word(Status.DO) & 0xFFFF
        if covers(Status.PROCESSING_STATUS):
            self.fifo_running = fifo_program_running(word(Status.PROCESSING_STATUS))
        if covers(Status.ALARM_1) and covers(Status.ALARM_2):
            self.last_status_ok = self._clock()
            param = word(Status.PARAM_STATUS) if covers(Status.PARAM_STATUS) else 0
            self._on_status(word(Status.ALARM_1), word(Status.ALARM_2), param)
        return words

    def _on_status(self, alarm1: int, alarm2: int, param_status: int) -> None:
        """Alarm reaction (11 §4.2/§4.3/§4.7, PORT-PLAN §8.2 watchdog)."""
        cutting = self.fifo_running or self.arming.state is ArmState.LASER_ARMED
        # 11 §4.2 bit 24: word == 0x01000000 alone is not an alarm; treat it as one while
        # cutting until capture G. UNVERIFIED.
        a1 = 0 if alarm1 == 0x01000000 and not cutting else alarm1
        faults = []
        if a1:
            faults.append(f"card alarm_1 {alarm1:#010x}")
        if alarm2:
            faults.append(f"card alarm_2 {alarm2:#010x}")
        if param_status == PARAM_STATUS_RESTART_REQUIRED:
            faults.append("reg 1031 == 1: card restart required")
        self.machine_fault = "; ".join(faults) or None
        alarm = (a1, alarm2)
        if a1 & (1 << 30):
            if not self.arming.estop_latched:
                log.error("card E-stop (1006 bit 30): latching")
                self.estop()
        elif (a1 or alarm2) and (
            alarm != self._last_alarm
            or self.arming.state is not ArmState.DISARMED
            or self.fifo_running
        ):
            log.error("card alarm %s: stop + disarm", self.machine_fault)
            self.arming.on_alarm(self.machine_fault or "alarm")
            self._stop_now()
        self._last_alarm = alarm

    def _comm_ok(self, now: float) -> bool:
        return (
            self.last_status_ok is not None
            and now - self.last_status_ok <= self.config.comm_loss_timeout_s
        )

    # -- writes ----------------------------------------------------------------------------------

    def _record(self, rec: WriteRecord) -> None:
        self.write_log.append(rec)
        if rec.decision == "sent":
            # Formatting the words and the frame bytes of a 298-word FIFO frame costs more
            # than sending it, and the arguments would be built even with INFO switched off
            # (pytest's log capture enables it): a job streams tens of frames per second.
            if log.isEnabledFor(logging.INFO):
                log.info(
                    "WRITE %#x %s frame=%s state=%s (%s)",
                    rec.addr,
                    " ".join(f"{x:x}" for x in rec.words),
                    rec.frame.hex(" "),
                    rec.state,
                    rec.reason,
                )
        else:
            log.warning(
                "%s WRITE %#x %s state=%s: %s",
                rec.decision.upper(),
                rec.addr,
                " ".join(f"{x:x}" for x in rec.words),
                rec.state,
                rec.reason,
            )
        if self.on_write is not None:
            self.on_write(rec)

    def _refuse(self, addr: int, w: Sequence[int], state: ArmState, reason: str) -> SafetyViolation:
        self._record(WriteRecord(self._clock(), addr, tuple(w), b"", state, "refused", reason))
        return SafetyViolation(addr, w, reason)

    def write(self, addr: int, words: Sequence[int]) -> None:
        """Checked WRITE; raises :class:`SafetyViolation` when refused."""
        w = [int(x) & 0xFFFFFFFF for x in words]
        now = self._clock()
        with self._lock:
            state = self.arming.state
            d = classify_write(addr, w, self.config, do_word=self.last_do_word)
            reason = d.reason
            refuse: str | None = None
            comm_lost = False
            is_jog = addr == REG_COMMAND and len(w) == 6 and w[0] == C.CMD_JOG
            relative_jog = is_jog and not w[1] & C.ABSOLUTE_BIT
            if d.policy is Policy.DENY:
                refuse = d.reason
            elif not self.arming.allows(d.policy):
                refuse = f"{d.reason}: needs {d.policy} (state {state}" + (
                    ", E-stop latched)" if self.arming.estop_latched else ")"
                )
            elif d.needs_homed and not self.homed:
                refuse = f"{d.reason}: axes not homed"
            elif d.policy not in (Policy.ALWAYS, Policy.CONNECT) and self.machine_fault:
                refuse = f"{d.reason}: refused while {self.machine_fault}"
            elif relative_jog and self.homed and w[1] in self.positions_word:
                refuse = _soft_limit_violation(
                    w[1], self.positions_word[w[1]] + _s32(w[5]), self.config
                )
            stream = addr == REG_FIFO_DATA or (addr == REG_FIFO_CONTROL and w == [C.FIFO_START])
            if refuse is None and stream and not self._comm_ok(now):
                refuse = "status poll stale > comm_loss_timeout_s: no streaming (PORT-PLAN §8.2)"
                comm_lost = self.fifo_running or self.arming.job_token is not None
            if (
                refuse is None
                and addr == REG_FIFO_CONTROL
                and w == [C.FIFO_START]
                and self.card_fifo_has_laser
                and state is not ArmState.LASER_ARMED
            ):
                refuse = (
                    "card FIFO holds frames queued while LASER_ARMED: clear it (0x67 <- [1]) first"
                )
            stripped = 0
            laser_items = 0
            if refuse is None and addr == REG_FIFO_DATA:
                try:
                    if state is ArmState.LASER_ARMED:
                        _, laser_items = strip_laser_records(w, self.config, laser_ok=True)
                    else:
                        w, stripped = strip_laser_records(w, self.config)
                except SafetyViolation as exc:
                    refuse = exc.reason
                if stripped:
                    reason = f"{reason}; {stripped} laser item(s) neutralised"
            if refuse is not None:
                if comm_lost:
                    self._on_comm_loss()
                raise self._refuse(addr, w, state, refuse)
            try:
                self._send(addr, w, state, reason, stripped, d.policy, laser_items)
            except SafetyViolation:
                raise
            except Exception:
                # Transport error: the card may still have executed the jog (lost reply),
                # so the deadman lease is registered anyway.
                self._register_lease(w, now, relative_jog)
                self._ensure_supervisor()
                raise
            self._register_lease(w, now, relative_jog)
        self._ensure_supervisor()

    def _register_lease(self, w: Sequence[int], now: float, relative_jog: bool) -> None:
        """Deadman lease for a relative jog still moving after ``deadman_timeout_s`` (PORT-PLAN §8.2)."""
        if not relative_jog:
            return
        v, dist = w[2], abs(_s32(w[5]))
        if dist > v * self.config.deadman_timeout_s:
            self._leases[w[1]] = (now + self.config.deadman_timeout_s, v)
        else:
            self._leases.pop(w[1], None)

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
        lock = getattr(self.client, "_lock", None)
        with lock if lock is not None else contextlib.nullcontext():
            # Re-check under the transport lock: an E-stop / disarm from another thread may
            # have landed after the decision (TOCTOU). The residual window is the write call.
            now_state = self.arming.state
            if not self.arming.allows(policy) or (
                laser_items and now_state is not ArmState.LASER_ARMED
            ):
                raise self._refuse(
                    addr, w, now_state, f"{reason}: arming changed to {now_state} before send"
                )
            seq = int(getattr(self.client, "next_seq", 0))
            frame = encode_vector([FUNC_WRITE, addr, len(w), *w], seq)
            try:
                self.client.write(addr, w)
            except Exception as exc:
                self._record(
                    WriteRecord(
                        self._clock(),
                        addr,
                        tuple(w),
                        frame,
                        state,
                        "failed",
                        f"{reason}; {exc}",
                        stripped,
                        laser_items,
                    )
                )
                raise
        self._record(
            WriteRecord(
                self._clock(), addr, tuple(w), frame, state, "sent", reason, stripped, laser_items
            )
        )
        if addr == REG_FIFO_DATA and laser_items:
            self.card_fifo_has_laser = True
        if addr == REG_FIFO_CONTROL and w:
            if w[0] == C.FIFO_START:
                self.fifo_running = True
            elif w[0] in (C.FIFO_STOP, C.FIFO_CLEAR):
                self.fifo_running = False
            if w[0] == C.FIFO_CLEAR:
                # UNVERIFIED that [1] discards queued frames (04 §3.5 clearFifo).
                self.card_fifo_has_laser = False
        if addr == REG_COMMAND and w and w[0] == C.CMD_STOP and len(w) >= 2:
            for idx in list(self._leases):
                if w[1] & (1 << idx):  # M1: axis-list index i drives slot i (01 §2.2 list)
                    del self._leases[idx]

    def send(self, cmd: CommandVector) -> None:
        """Send a builder result through the gate (its metadata is not trusted)."""
        self.write(cmd.register, cmd.words)

    # -- safety sequences -------------------------------------------------------------------------

    def _best_effort(self, seq: Iterable[CommandVector]) -> StopOutcome:
        out = StopOutcome()
        for cmd in seq:
            try:
                self.send(cmd)
                out.sent.append(cmd)
            except Exception as exc:  # keep going: a stop must try every step
                out.failed.append((cmd, exc))
        return out

    def _stop_now(self, *, v_last_word: int = 0, connected: bool = True) -> StopOutcome:
        return self._best_effort(
            C.stop_sequence(
                self.params,
                fifo_running=self.fifo_running,
                connected=connected,
                current_do_word=self.last_do_word or 0,
                v_last_word=v_last_word,
            )
        )

    def stop(self, *, v_last_word: int = 0, connected: bool = True) -> StopOutcome:
        """Stop / pause (11 §2 V4, A1 §4.2-§4.3): stop motion, outputs off, ZF stop.

        The laser auto-disarms (PORT-PLAN §8.2). Every vector is attempted even if one fails.
        """
        self.arming.on_stop()
        return self._stop_now(v_last_word=v_last_word, connected=connected)

    def estop(self, *, v_last_word: int = 0, connected: bool = True) -> StopOutcome:
        """UI E-stop: latch + disarm first, then the stop sequence (A1 §4.4, PORT-PLAN §8.2)."""
        self.arming.estop()
        seq = C.estop_sequence(
            self.params,
            fifo_running=self.fifo_running,
            connected=connected,
            current_do_word=self.last_do_word or 0,
            v_last_word=v_last_word,
        )
        return self._best_effort(seq)

    def jog_release(self, slots: Iterable[int], v_last_word: int) -> None:
        """Deadman / key release stop ``[1, mask, 2, vd, 10vd]`` (PORT-PLAN §8.2, A1 §4.1)."""
        self.send(C.jog_release_stop(slots, v_last_word, self.params))

    def jog_keepalive(self, axis_index: int) -> bool:
        """Key still held: extend the deadman lease of ``axis_index`` (PORT-PLAN §8.2).

        Returns False when there is no live lease (the jog was already stopped).
        """
        with self._lock:
            lease = self._leases.get(axis_index)
            if lease is None:
                return False
            self._leases[axis_index] = (self._clock() + self.config.deadman_timeout_s, lease[1])
            return True

    def _on_comm_loss(self) -> None:
        """Watchdog: stopFifo + disarm (PORT-PLAN §8.2), plus stops for held jogs."""
        log.error("status poll stale > %.2f s: comm loss", self.config.comm_loss_timeout_s)
        self.arming.on_comm_loss()
        seq: list[CommandVector] = [C.fifo_stop()] if self.fifo_running else []
        seq += [C.jog_release_stop([i], v, self.params) for i, (_, v) in self._leases.items()]
        self._best_effort(seq)

    def service(self, now: float | None = None) -> None:
        """Run the deadman and comm-loss checks once (supervisor thread or poll loop)."""
        now = self._clock() if now is None else now
        with self._lock:
            if (self.fifo_running or self.arming.job_token is not None) and not self._comm_ok(now):
                self._on_comm_loss()
            for idx, (deadline, v) in list(self._leases.items()):
                if now > deadline:
                    log.warning("deadman: axis %d key event stale, stopping", idx)
                    out = self._best_effort([C.jog_release_stop([idx], v, self.params)])
                    # An exception reply (e.g. 3 "already stopped", 08 §4.5 INFERENCE) proves the
                    # card received the stop: re-sending it every period would flood the card.
                    if out.ok or all(isinstance(e, CardRefused) for _, e in out.failed):
                        self._leases.pop(idx, None)

    def _needs_supervision(self) -> bool:
        return bool(self._leases) or self.fifo_running or self.arming.job_token is not None

    def _ensure_supervisor(self) -> None:
        if not self._supervise or not self._needs_supervision():
            return
        with self._sup_lock:
            if self._sup_thread is not None and self._sup_thread.is_alive():
                return
            self._sup_stop.clear()
            self._sup_thread = threading.Thread(
                target=self._supervisor_loop, name="nexcut-safety-supervisor", daemon=True
            )
            self._sup_thread.start()

    def _supervisor_loop(self) -> None:
        while not self._sup_stop.wait(self.config.supervisor_period_s):
            try:
                self.service()
            except Exception:  # pragma: no cover - never let the supervisor die silently
                log.exception("safety supervisor")
            with self._sup_lock:
                if not self._needs_supervision():
                    self._sup_thread = None
                    return

    def close(self) -> None:
        """Stop the supervisor thread (does not send anything)."""
        self._sup_stop.set()
        t = self._sup_thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=1.0)
