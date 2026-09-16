"""MCC100 card simulator v0: a UDP server speaking the ``CExtModbus`` framing (04 §3).

Purpose: exercise :mod:`nexcut.mcc.transaction` and later the FIFO streamer without the
machine (PORT-PLAN §5 item 2). It is **not** a model of the real firmware -- only the
wire format, the function codes, the register addresses and the observed failure modes
(08 §2-§4, §7) are grounded in evidence. Everything the simulator *decides* (register
contents, run-status values, FIFO capacity, which exception code a given misuse returns,
alarm bit positions, DO channel -> bit mapping, FIFO consumption rate) is marked
UNVERIFIED below.

What it does:

* READ (0x30) of the blocks the driver polls: 1000 n=2/36 (status, 04 §3.5, A2 §2),
  2000 n=50 (axis RO), 5000 n=9, 10000 n=18 (ZF status), 11000 n=39, 50000 n=26 (system
  RW), 50200 n=100 (axis RW), 60001 n=120; any sub-range of a configured block is served,
  anything else gets exception 2 (UNVERIFIED: the real card's answer to an unmapped
  address is unknown).
* WRITE (0x40) to 0x65 with the M1 command vocabulary of 11-static-findings §2 (built by
  :mod:`nexcut.mcc.commands`): sub-cmd 1 STOP, 2 HOME (mask), 3 jog (relative, bit 31
  absolute), 5 go-to, ``[101]``, ``[9999,5,0,0]``, ``[9999,2,mask,value]`` DO,
  ``[9999,13,...]`` ext DO, ``[9999,4,ch,mV]`` DA, ``[9999,3|0x11,freq,duty(,0)]`` PWM;
  ``[102]`` is refused with exception 2 as in the logs (C10). Every accepted vector is
  echoed into :attr:`CardSimulator.commands` / :attr:`CardSimulator.command_log`.
* 0x66 (FIFO frame, 08 §4.4 TLV items) and 0x67 (FIFO run control 1 clear / 2 start /
  3 stop, 04 §3.5), and plain writes into configured blocks.
* Status words follow A2: DO word bit n = DO n+1 (A3 §6), alarm_2 bit 5 = FIFO starvation
  (A2 §2.4), processing status 1019 low byte 1 while the FIFO runs, axis status word
  byte 2 busy / byte 3 command type / bit 15 homed (A2 §3.1).
* Exception 2 while "not ready" after a (re)boot (08 §3.3, §7) and exception 3 when a
  motion command arrives while the card is busy (08 §4.5).
* FIFO items are consumed at a configurable tick (``SimConfig.tick_s``); opcode 3000 costs
  one tick, every other item is executed between ticks. ``consumed_log_limit`` keeps the
  executed items so a test can diff the stream the card ran against the planned one, and
  :attr:`CardSimulator.queue_low_water` records the smallest queue depth seen while the
  program ran (the PORT-PLAN §8.3 jitter gate).
* Reg 1016 (FIFO space margin) is reported in *bytes free*, 60000 when the FIFO is empty,
  because that is how the PC side uses it (11 C2, §4.1 row 16; :mod:`nexcut.mcc.fifo`).
  ``SimConfig(fifo_space_unit="items")`` restores the older free-item-slot reading (A2 §2).
* Fault injection: drop N requests, drop N replies, delay replies, stale replies,
  arbitrary reply override, deaf addresses (08 §4.3), reboot (silent + not-ready window).

Replies are encoded with the card's lo-first CRC order (EVIDENCE 08 §2.2); success
reply layouts follow the PC decoder and are UNVERIFIED (see ``framing.encode_reply_vector``).
"""

from __future__ import annotations

import heapq
import itertools
import select
import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from nexcut.mcc.commands import (
    ABSOLUTE_BIT,
    CMD_9999,
    CMD_GOTO,
    CMD_HOME,
    CMD_JOG,
    CMD_STOP,
    CMD_ZF_STOP,
    FIFO_CLEAR,
    FIFO_START,
    FIFO_STOP,
    MISC_DA,
    MISC_DO,
    MISC_DO_EXT,
    MISC_PROLOGUE,
    MISC_PWM,
    MISC_PWM_5V,
)
from nexcut.mcc.crc import CrcOrder
from nexcut.mcc.dissector import FifoItem, FifoParseError, parse_fifo_words
from nexcut.mcc.framing import (
    FUNC_READ,
    FUNC_WRITE,
    REG_COMMAND,
    REG_FIFO_CONTROL,
    REG_FIFO_DATA,
    DecodeError,
    decode_request_vector,
    exception_reply,
    parse_raw_frame,
    read_reply,
    write_reply,
)
from nexcut.mcc.registers import (
    AXIS_RO_WORDS,
    AXIS_SLOTS,
    FIFO_MARGIN_EMPTY,
    Alarm2,
    AxisRO,
    Status,
    SystemRW,
)

__all__ = [
    "ALARM2_FIFO_STARVATION",
    "EXC_BUSY",
    "EXC_NOT_READY",
    "RO_ALARM1",
    "RO_ALARM2",
    "RO_PROCESSING_STATUS",
    "RO_FIFO_FRAME_ID",
    "RO_FIFO_SPACE",
    "RO_INPUTS",
    "RO_OUTPUTS",
    "RO_PROGRAM_ID",
    "RO_PROGRAM_VERSION",
    "RO_RUN_STATUS",
    "RUN_FIFO",
    "RUN_IDLE",
    "RUN_MOVING",
    "CardSimulator",
    "RequestRecord",
    "SimConfig",
]

# --- register addresses in block 1000 (A2 §2: word order EVIDENCE from the consumers) --------------
RO_PROGRAM_ID = Status.PROGRAM_ID.addr
RO_PROGRAM_VERSION = Status.PROGRAM_VERSION.addr  # compared against MinHardwareVer=20152 (04 §1)
RO_INPUTS = Status.DI.addr
RO_OUTPUTS = Status.DO.addr
RO_ALARM1 = Status.ALARM_1.addr
RO_ALARM2 = Status.ALARM_2.addr
RO_RUN_STATUS = Status.RUN_STATUS.addr  # no vendor consumer (A2 §2): simulator convention below
RO_FIFO_FRAME_ID = Status.FIFO_FRAME_ID.addr
RO_FIFO_SPACE = Status.FIFO_SPACE_MARGIN.addr
RO_PROCESSING_STATUS = Status.PROCESSING_STATUS.addr

RUN_IDLE = 0  # UNVERIFIED value (simulator convention for reg 1008)
RUN_MOVING = 1  # UNVERIFIED value (simulator convention)
RUN_FIFO = 2  # UNVERIFIED value (simulator convention)

ALARM2_FIFO_STARVATION = int(Alarm2.FIFO_STARVATION)
"""Reg 1007 bit 5 ``EtherCATErrorInfo_2_05`` FIFO starvation (A2 §2.4, EVIDENCE)."""

EXC_NOT_READY = 2
"""Exception 2: returned for every block right after a card reset (08 §3.3/§7, INFERENCE medium)."""
EXC_BUSY = 3
"""Exception 3: jog/home refused while moving (08 §4.5, INFERENCE medium)."""

# Sub-command numbers are shared with the builders in :mod:`nexcut.mcc.commands` (11 §8
# correction: sub-cmd 1 = STOP, 2 = HOME) so the simulator and the driver cannot drift apart.
CMD_MOVE = CMD_GOTO  # 5, go-to (A1 row 13)
CMD_MISC = CMD_9999
CMD_RESYNC = 102  # 08 §3.3 [102], refused with exception 2 in the logs (C10)
_MOTION_COMMANDS = frozenset({CMD_HOME, CMD_JOG, CMD_MOVE})
_ABS = ABSOLUTE_BIT
OP_TICK = 3000  # 08 §4.4 interpolation tick


@dataclass(slots=True)
class SimConfig:
    """Simulator knobs; defaults are simulator choices unless a doc section is cited."""

    tick_s: float = 0.001
    """Seconds per interpolation tick. UNVERIFIED (1 ms bus cycle is an estimate, 04 §3.7)."""
    fifo_capacity_items: int = 4000
    """FIFO depth in items. UNVERIFIED."""
    fifo_space_unit: Literal["bytes", "items"] = "bytes"
    """Unit of reg 1016: ``"bytes"`` = ``fifo_margin_empty`` minus 4 bytes per queued item word
    (11 C2: the PC tests ``frame_bytes + 2000 <= margin`` and 60000 = empty); ``"items"`` =
    ``fifo_capacity_items`` minus queued items (A2 §2 reading). The card-side accounting
    (whether frame prefixes count, how an item is charged) is UNVERIFIED (11 §7 step 8)."""
    fifo_margin_empty: int = FIFO_MARGIN_EMPTY
    """Reg 1016 with an empty FIFO in ``"bytes"`` mode (11 §4.1 row 16)."""
    fifo_starvation_alarm: bool = True
    """Raise reg 1007 bit 5 when the running FIFO drains (A2 §2.4).

    **The end of a job looks exactly like starvation and the PC cannot tell the card apart.**
    The card reports "FIFO empty" (reg 1016 == 60000) at the same instant the last item is
    consumed, so however fast the PC polls, ``0x67 <- [3]`` always arrives after the FIFO ran
    dry; if the real card alarmed there, every vendor cut would end in a fault. Either the
    card does not alarm at a clean drain, or the alarm needs some idle time first - which one
    is **UNVERIFIED** (11 §7 step 8/9 settles it by watching reg 1007 at the end of a vendor
    dry run). The conservative reading is kept as the default; the streaming tests of
    :mod:`nexcut.mccd.feeder` set this to False and measure the queue depth instead
    (:attr:`CardSimulator.queue_low_water`), which is what PORT-PLAN §8.3 asks for."""
    consumed_log_limit: int = 0
    """Keep this many consumed items in :attr:`CardSimulator.consumed_items` so a test can
    diff the *executed* stream against what the planner emitted.  0 = keep none (default:
    a 10-minute job is millions of items)."""
    program_id: int = 100
    """UNVERIFIED placeholder."""
    program_version: int = 20152
    """Default = ``MinHardwareVer`` from ``ipAdd.ini`` (04 §1) so a version gate passes."""
    motion_time_s: float = 0.3
    """How long a home/jog/move keeps the card busy. UNVERIFIED."""
    blocks: dict[int, int] = field(
        default_factory=lambda: {
            1000: 36,
            2000: 50,
            5000: 9,
            10000: 18,
            11000: 39,
            50000: 26,
            50200: 100,
            60001: 120,
        }
    )
    """Readable/writable register blocks ``{start: count}`` (04 §3.5, 08 §3.1, 11 §3.2)."""
    system_rw: dict[int, int] = field(
        default_factory=lambda: {SystemRW.BUS_CYCLE: 250, SystemRW.ZF_TYPE: 1, SystemRW.K: 1000}
    )
    """SystemRW words at power-on. UNVERIFIED card values: XML InterpolationCycle 250,
    ZFType 1, and K = 1000 from the logs (11 §7 step 1 reads them)."""
    stop_while_idle_exception: bool = False
    """Reply exception 3 to a stop when nothing moves (08 §4.5 INFERENCE "already stopped");
    off by default. UNVERIFIED."""
    busy_while_streaming: bool = True
    """Refuse motion commands with exception 3 while the FIFO runs. UNVERIFIED."""
    check_crc: bool = True
    """Ignore requests whose CRC is not hi-first CRC-16/MODBUS. UNVERIFIED card behaviour."""

    def __post_init__(self) -> None:
        if self.fifo_space_unit not in ("bytes", "items"):
            raise ValueError(
                f"fifo_space_unit must be 'bytes' or 'items', not {self.fifo_space_unit!r}"
            )


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """One datagram as seen by the simulator."""

    t: float
    """``time.monotonic()`` at reception."""
    seq: int | None
    vector: tuple[int, ...] | None
    raw: bytes
    action: str
    """``replied``, ``exception``, ``dropped``, ``no-reply``, ``silent``, ``bad-crc``, ``undecodable``."""


class CardSimulator:
    """Threaded UDP server that answers like an MCC100 card (see module docstring)."""

    def __init__(
        self, host: str = "127.0.0.1", port: int = 0, config: SimConfig | None = None
    ) -> None:
        self.config = config or SimConfig()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind((host, port))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._outbox: list[tuple[float, int, bytes, tuple[str, int]]] = []
        self._counter = itertools.count()
        self.requests: list[RequestRecord] = []
        # fault injection state
        self._drop_requests = 0
        self._drop_replies = 0
        self._delay_s = 0.0
        self._delay_count: int | None = None
        self._stale_before_next = 0
        self._override: deque[Callable[[int], bytes]] = deque()
        self.deaf_addresses: set[int] = set()
        """READ start addresses that never get a reply (the "deaf 10000 block", 08 §4.3)."""
        self._silent_until = 0.0
        self._not_ready_until = 0.0
        self._power_on()

    # -- lifecycle -------------------------------------------------------------------------------

    @property
    def address(self) -> tuple[str, int]:
        """``(host, port)`` the simulator listens on."""
        host, port = self._sock.getsockname()[:2]
        return host, port

    def start(self) -> CardSimulator:
        """Start the server thread."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._serve, name="mcc-sim", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        """Stop the thread and close the socket."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._sock.close()

    def __enter__(self) -> CardSimulator:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _power_on(self) -> None:
        cfg = self.config
        self.registers: dict[int, int] = {}
        for start, count in cfg.blocks.items():
            for a in range(start, start + count):
                self.registers[a] = 0
        self.registers[RO_PROGRAM_ID] = cfg.program_id
        self.registers[RO_PROGRAM_VERSION] = cfg.program_version
        for word, value in cfg.system_rw.items():
            if 50000 in cfg.blocks:
                self.registers[50000 + int(word)] = value
        self.axis_pos = [0] * AXIS_SLOTS
        """Commanded position per slot in card units (µm), written to axis RO word 2.
        UNVERIFIED: the card reports *pulses* there (A2 §3); the simulator uses µm."""
        self.homed = [False] * AXIS_SLOTS
        self._axis_motion: dict[int, tuple[float, int, int]] = {}
        """slot -> (end time, command type byte, target µm)."""
        self.command_log: list[tuple[str, tuple[int, ...]]] = []
        """``(name, words)`` of every 0x65 vector accepted (echo for tests/UI)."""
        self.fifo: deque[FifoItem] = deque()
        self.fifo_running = False
        self.last_frame_id: int | None = None
        self.frames_accepted = 0
        self.items_consumed = 0
        self.ticks_consumed = 0
        self.underruns = 0
        self.consumed_items: list[FifoItem] = []
        """Items the FIFO executed, oldest first, capped by ``SimConfig.consumed_log_limit``."""
        self.consumed_overflow = 0
        """Items dropped from :attr:`consumed_items` because the cap was reached."""
        self.queue_low_water: int | None = None
        """Lowest queue depth in items seen while the program ran (``None`` = never ran).
        Reset with :meth:`reset_flow_stats`; the PORT-PLAN §8.3 jitter gate reads it."""
        self.position = [0, 0]
        """Accumulated ``(dX, dY)`` of consumed 3000 ticks (low int16 = X, per dissector A3 §5)."""
        self.laser_samples: list[tuple[int, int]] = []
        """``(freq, duty)`` of each consumed tick (kept for the last 10 000 ticks)."""
        self._moving_until = 0.0
        self._last_advance = time.monotonic()
        self.commands: list[tuple[int, ...]] = []
        self._update_status(time.monotonic())

    # -- fault injection / external stimuli --------------------------------------------------------

    def drop_requests(self, n: int) -> None:
        """Ignore the next ``n`` requests entirely (no processing, no reply)."""
        with self._lock:
            self._drop_requests += n

    def drop_replies(self, n: int) -> None:
        """Process the next ``n`` requests but do not send their replies."""
        with self._lock:
            self._drop_replies += n

    def delay_replies(self, seconds: float, count: int | None = None) -> None:
        """Delay replies by ``seconds``; ``count=None`` = until changed, else next ``count``."""
        with self._lock:
            self._delay_s = seconds
            self._delay_count = count

    def send_stale(self, n: int = 1) -> None:
        """Before each of the next ``n`` replies send a copy with ``seq - 1`` (stale datagram)."""
        with self._lock:
            self._stale_before_next += n

    def override_next_reply(self, make: Callable[[int], bytes]) -> None:
        """Replace the next reply by ``make(seq)`` raw bytes (protocol-error tests)."""
        with self._lock:
            self._override.append(make)

    def reboot(self, silent_s: float = 1.0, not_ready_s: float = 1.0) -> None:
        """Power-cycle: volatile state lost, no replies for ``silent_s``, then exception 2
        for ``not_ready_s`` (08 §3.3, §7). Durations are UNVERIFIED."""
        with self._lock:
            now = time.monotonic()
            di = self.registers.get(RO_INPUTS, 0)
            self._power_on()
            self.registers[RO_INPUTS] = di
            self._silent_until = now + silent_s
            self._not_ready_until = now + silent_s + not_ready_s

    def set_inputs(self, word: int) -> None:
        """Set the digital-input word (block 1000 + 4, UNVERIFIED offset)."""
        with self._lock:
            self.registers[RO_INPUTS] = word & 0xFFFFFFFF

    def inject_alarm(self, alarm1: int | None = None, alarm2: int | None = None) -> None:
        """Set the alarm words (block 1000 + 6 / + 7, UNVERIFIED offsets and bit meanings)."""
        with self._lock:
            if alarm1 is not None:
                self.registers[RO_ALARM1] = alarm1 & 0xFFFFFFFF
            if alarm2 is not None:
                self.registers[RO_ALARM2] = alarm2 & 0xFFFFFFFF

    @property
    def outputs(self) -> int:
        """Digital-output word (block 1000 + 5, UNVERIFIED offset)."""
        with self._lock:
            return self.registers.get(RO_OUTPUTS, 0)

    def snapshot(self) -> dict[str, int]:
        """Thread-safe view of the FIFO counters (advances the simulation first)."""
        with self._lock:
            self._advance(time.monotonic())
            return {
                "queued": len(self.fifo),
                "items_consumed": self.items_consumed,
                "ticks_consumed": self.ticks_consumed,
                "frames_accepted": self.frames_accepted,
                "underruns": self.underruns,
                "running": int(self.fifo_running),
                "x": self.position[0],
                "y": self.position[1],
                "low_water": -1 if self.queue_low_water is None else self.queue_low_water,
            }

    def reset_flow_stats(self) -> None:
        """Forget :attr:`queue_low_water` (call it once the job is streaming)."""
        with self._lock:
            self.queue_low_water = None

    def consumed_words(self) -> list[int]:
        """Flat item words of :attr:`consumed_items` (``header, args…`` per item, 11 §5.2).

        The word stream a test can compare against the planned frames' data, which is what
        "the card executed exactly what the planner emitted" means (PORT-PLAN §4 M4 gate 1).
        """
        with self._lock:
            out: list[int] = []
            for item in self.consumed_items:
                out.append(((len(item.args) * 4) << 16) | item.opcode)
                out.extend(int(a) & 0xFFFFFFFF for a in item.args)
            return out

    # -- simulation --------------------------------------------------------------------------------

    def _advance(self, now: float) -> None:
        """Consume FIFO items for the ticks elapsed since the last call."""
        tick = self.config.tick_s
        if not self.fifo_running:
            self._last_advance = now
        else:
            budget = int((now - self._last_advance) / tick)
            if budget > 0:
                self._last_advance += budget * tick
                while self.fifo and (budget > 0 or self.fifo[0].opcode != OP_TICK):
                    item = self.fifo.popleft()
                    self.items_consumed += 1
                    self._log_consumed(item)
                    self._execute_item(item)
                    if item.opcode == OP_TICK:
                        budget -= 1
                depth = len(self.fifo)
                self.queue_low_water = (
                    depth if self.queue_low_water is None else min(self.queue_low_water, depth)
                )
                if not self.fifo and budget > 0:
                    # Ran dry while running: FIFO starvation (04 §3.7 EtherCATErrorInfo_2_05).
                    self.underruns += 1
                    self.fifo_running = False
                    if self.config.fifo_starvation_alarm:
                        self.registers[RO_ALARM2] = (
                            self.registers.get(RO_ALARM2, 0) | ALARM2_FIFO_STARVATION
                        )
        self._update_status(now)

    def _log_consumed(self, item: FifoItem) -> None:
        """Keep the executed item for a stream diff (``SimConfig.consumed_log_limit``)."""
        limit = self.config.consumed_log_limit
        if limit <= 0:
            return
        if len(self.consumed_items) >= limit:
            self.consumed_overflow += 1
            return
        self.consumed_items.append(item)

    def _execute_item(self, item: FifoItem) -> None:
        t = item.tick
        if t is not None:
            dx, dy, freq, duty = t
            self.position[0] += dx
            self.position[1] += dy
            self.ticks_consumed += 1
            self.laser_samples.append((freq, duty))
            if len(self.laser_samples) > 10_000:
                del self.laser_samples[:5_000]
        elif item.opcode == CMD_MISC and len(item.args) >= 3 and item.args[0] == MISC_DO:
            self._set_outputs(item.args[1], item.args[2])  # 08 §4.4: 9999[2, mask, value]

    def _set_outputs(self, mask: int, value: int) -> None:
        out = self.registers.get(RO_OUTPUTS, 0)
        self.registers[RO_OUTPUTS] = ((out & ~mask) | (value & mask)) & 0xFFFFFFFF

    def _fifo_overflows(self, new_items: Sequence[FifoItem]) -> bool:
        """Whether the frame does not fit, in the same unit reg 1016 is reported in.

        In ``"bytes"`` mode the depth that reg 1016 advertises (``fifo_margin_empty``) *is*
        the capacity; counting items as well would let the card refuse a frame the PC's
        space test (11 C2) said would fit. UNVERIFIED either way (11 §7 step 8)."""
        if self.config.fifo_space_unit == "items":
            return len(self.fifo) + len(new_items) > self.config.fifo_capacity_items
        extra = 4 * sum(1 + len(item.args) for item in new_items)
        return 4 * sum(1 + len(i.args) for i in self.fifo) + extra > self.config.fifo_margin_empty

    def _fifo_space(self) -> int:
        """Reg 1016 in the configured unit (:attr:`SimConfig.fifo_space_unit`, 11 C2)."""
        if self.config.fifo_space_unit == "items":
            return max(0, self.config.fifo_capacity_items - len(self.fifo))
        queued_bytes = 4 * sum(1 + len(item.args) for item in self.fifo)
        return max(0, self.config.fifo_margin_empty - queued_bytes)

    def _update_status(self, now: float) -> None:
        self.registers[RO_FIFO_SPACE] = self._fifo_space()
        for slot, (end, cmd_type, target) in list(self._axis_motion.items()):
            if now >= end:
                del self._axis_motion[slot]
                self.axis_pos[slot] = target
                if cmd_type == CMD_HOME:
                    self.homed[slot] = True
        if self._axis_motion:
            self._moving_until = max(
                self._moving_until, *(m[0] for m in self._axis_motion.values())
            )
        if self.fifo_running:
            self.registers[RO_RUN_STATUS] = RUN_FIFO
        elif now < self._moving_until:
            self.registers[RO_RUN_STATUS] = RUN_MOVING
        else:
            self.registers[RO_RUN_STATUS] = RUN_IDLE
        # A2 §2 row 19: processing status low byte 1 = FIFO program running
        self.registers[RO_PROCESSING_STATUS] = 1 if self.fifo_running else 0
        if 2000 in self.config.blocks:
            for slot in range(AXIS_SLOTS):
                word = 0x8000 if self.homed[slot] else 0  # bit 15 homed (A2 §3.1)
                motion = self._axis_motion.get(slot)
                if motion is not None:
                    word |= (1 << 16) | (motion[1] << 24)  # byte 2 busy, byte 3 command type
                base = 2000 + AXIS_RO_WORDS * slot
                self.registers[base + AxisRO.STATUS] = word
                self.registers[base + AxisRO.PULSE_POSITION] = self.axis_pos[slot] & 0xFFFFFFFF

    # -- request handling --------------------------------------------------------------------------

    def _handle(self, vector: list[int], seq: int, now: float) -> bytes:
        func = vector[0]
        if now < self._not_ready_until:
            return exception_reply(seq, func, EXC_NOT_READY)
        if func == FUNC_READ:
            _, addr, count = vector[:3]
            words = self._read(addr, count)
            if words is None:
                return exception_reply(seq, func, EXC_NOT_READY)  # UNVERIFIED code
            return read_reply(seq, addr, words)
        if func == FUNC_WRITE:
            _, addr, count, *data = vector
            code = self._write(addr, data, now)
            if code:
                return exception_reply(seq, func, code)
            return write_reply(seq, addr, count)
        return exception_reply(seq, func, 1)  # UNVERIFIED: unsupported function

    def _read(self, addr: int, count: int) -> list[int] | None:
        for start, n in self.config.blocks.items():
            if start <= addr and addr + count <= start + n:
                return [self.registers.get(a, 0) for a in range(addr, addr + count)]
        return None

    def _write(self, addr: int, data: list[int], now: float) -> int:
        if addr == REG_COMMAND:
            return self._command(data, now)
        if addr == REG_FIFO_DATA:
            return self._fifo_frame(data)
        if addr == REG_FIFO_CONTROL:
            if len(data) != 1 or data[0] not in (FIFO_CLEAR, FIFO_START, FIFO_STOP):
                return EXC_BUSY  # UNVERIFIED code
            if data[0] == FIFO_CLEAR:
                self.fifo.clear()
                self.fifo_running = False
                self.last_frame_id = None
                self.registers[RO_ALARM2] = (
                    self.registers.get(RO_ALARM2, 0) & ~ALARM2_FIFO_STARVATION
                )
            elif data[0] == FIFO_START:
                self.fifo_running = True
                self._last_advance = now
            else:
                self.fifo_running = False
            self._update_status(now)
            return 0
        if self._read(addr, len(data)) is None:
            return EXC_NOT_READY  # UNVERIFIED code
        for i, w in enumerate(data):
            self.registers[addr + i] = w & 0xFFFFFFFF
        return 0

    def _command(self, data: list[int], now: float) -> int:
        """0x65 vocabulary of 11-static-findings §2 (see module docstring)."""
        if not data:
            return EXC_BUSY  # UNVERIFIED
        self.commands.append(tuple(data))
        name, code = self._dispatch_command(data, now)
        if code == 0:
            self.command_log.append((name, tuple(data)))
        self._update_status(now)
        return code

    def _start_motion(self, slot: int, cmd_type: int, target: int, now: float) -> None:
        end = now + self.config.motion_time_s
        self._axis_motion[slot] = (end, cmd_type, target)
        self._moving_until = max(self._moving_until, end)

    def _dispatch_command(self, d: list[int], now: float) -> tuple[str, int]:
        sub, n = d[0], len(d)
        if sub == CMD_STOP:
            if not (n == 2 or (n == 5 and d[2] == 2)):
                return "stop?", EXC_NOT_READY  # UNVERIFIED code for a malformed stop
            mask = d[1]
            moving = [s for s in self._axis_motion if (mask >> s) & 1]
            if not moving and self.config.stop_while_idle_exception:
                return "stop", EXC_BUSY
            for s in moving:
                del self._axis_motion[s]  # UNVERIFIED: stops dead at the start position
            self._moving_until = max((m[0] for m in self._axis_motion.values()), default=now)
            return "stop", 0
        if sub in _MOTION_COMMANDS:
            busy = now < self._moving_until or (
                self.config.busy_while_streaming and self.fifo_running
            )
            if busy:
                return "motion", EXC_BUSY  # 08 §4.5: 567 rejections, INFERENCE medium
            if sub == CMD_HOME and n == 3:
                for s in range(AXIS_SLOTS):
                    if (d[1] >> s) & 1:
                        self.homed[s] = False
                        self._start_motion(s, CMD_HOME, 0, now)
                return "home", 0
            if sub == CMD_JOG and n == 6:
                slot = d[1] & 0x7FFFFFFF  # axis-list index == slot on this build (C8)
                if slot >= AXIS_SLOTS:
                    return "jog?", EXC_NOT_READY  # UNVERIFIED (roll feeder 16 etc.)
                dist = d[5] - (1 << 32) if d[5] & 0x80000000 else d[5]
                target = dist if d[1] & _ABS else self.axis_pos[slot] + dist
                self._start_motion(slot, CMD_JOG, target, now)
                return "jog", 0
            if sub == CMD_MOVE and n == 9:
                vals = [w - (1 << 32) if w & 0x80000000 else w for w in d[5:9]]
                for i, s in enumerate(b for b in range(4) if (d[1] >> b) & 1):
                    target = vals[i] if d[1] & _ABS else self.axis_pos[s] + vals[i]
                    self._start_motion(s, CMD_MOVE, target, now)
                return "go_to", 0
            return "motion?", EXC_NOT_READY  # UNVERIFIED code for a malformed motion vector
        if sub == CMD_ZF_STOP and n == 1:
            return "zf_stop", 0
        if sub == CMD_RESYNC:
            return "resync", EXC_NOT_READY  # logs: [102] rejected with exception 2 (C10)
        if sub == CMD_MISC and n >= 2:
            misc = d[1]
            if misc == MISC_DO and n == 4:
                self._set_outputs(d[2] & 0xFFFF, d[3])  # A3 §6: mask/value, bit = port-1
                return "do", 0
            if misc == MISC_DO_EXT and n == 4:
                reg = Status.EXT_DO.addr
                cur = self.registers.get(reg, 0)
                self.registers[reg] = ((cur & ~d[2]) | (d[3] & d[2])) & 0xFFFF
                return "do_ext", 0
            if misc == MISC_DA and n == 4 and d[2] in (0, 1):
                self.registers[Status.DA1.addr + d[2]] = d[3]  # UNVERIFIED read-back units
                return "da", 0
            if misc in (MISC_PWM, MISC_PWM_5V) and n in (4, 5):
                self.registers[Status.PWM_FREQ.addr] = d[2]
                self.registers[Status.PWM_DUTY.addr] = d[3]
                return "pwm", 0
            if misc == MISC_PROLOGUE and d == [CMD_MISC, MISC_PROLOGUE, 0, 0]:
                return "connect_prologue", 0
            if misc == 1:
                self.fifo.clear()  # 04 §3.6: [9999, 1, ...] older FIFO clear path
                self.fifo_running = False
                return "fifo_clear_legacy", 0
        return "unknown", EXC_NOT_READY  # UNVERIFIED: card answer to unknown vectors

    def _fifo_frame(self, data: list[int]) -> int:
        try:
            frame = parse_fifo_words(data, strict=True)
        except FifoParseError:
            return EXC_BUSY  # UNVERIFIED (cf. EtherCATErrorInfo_2_01 "interpolation data length")
        if frame.frame_id == self.last_frame_id:
            return 0  # UNVERIFIED: a re-sent frame id (08 §4.4) is acknowledged, not re-queued
        if self._fifo_overflows(frame.items):
            return EXC_BUSY  # UNVERIFIED
        self.fifo.extend(frame.items)
        self.last_frame_id = frame.frame_id
        self.frames_accepted += 1
        self.registers[RO_FIFO_FRAME_ID] = frame.frame_id & 0xFFFFFFFF
        self._update_status(time.monotonic())
        return 0

    # -- datagram loop -----------------------------------------------------------------------------

    def _record(
        self, now: float, seq: int | None, vec: Sequence[int] | None, raw: bytes, a: str
    ) -> None:
        self.requests.append(RequestRecord(now, seq, tuple(vec) if vec else None, raw, a))

    def _queue(self, due: float, data: bytes, addr: tuple[str, int]) -> None:
        heapq.heappush(self._outbox, (due, next(self._counter), data, addr))

    def _on_datagram(self, raw: bytes, src: tuple[str, int], now: float) -> None:
        with self._lock:
            self._advance(now)
            seq = (raw[0] << 8) | raw[1] if len(raw) >= 2 else None
            if self._drop_requests > 0:
                self._drop_requests -= 1
                self._record(now, seq, None, raw, "dropped")
                return
            if now < self._silent_until:
                self._record(now, seq, None, raw, "silent")
                return
            if self.config.check_crc:
                rf = parse_raw_frame(raw)
                if len(raw) < 8 or rf.crc_order is not CrcOrder.HI_FIRST or not rf.length_ok:
                    self._record(now, seq, None, raw, "bad-crc")
                    return
            try:
                vector = decode_request_vector(raw)
            except DecodeError:
                self._record(now, seq, None, raw, "undecodable")
                return
            assert seq is not None
            if vector[0] == FUNC_READ and vector[1] in self.deaf_addresses:
                self._record(now, seq, vector, raw, "no-reply")
                return
            reply = self._handle(vector, seq, now)
            if self._override:
                reply = self._override.popleft()(seq)
            action = "exception" if len(reply) >= 8 and reply[7] & 0x80 else "replied"
            if self._drop_replies > 0:
                self._drop_replies -= 1
                self._record(now, seq, vector, raw, "no-reply")
                return
            self._record(now, seq, vector, raw, action)
            due = now
            if self._delay_s > 0 and (self._delay_count is None or self._delay_count > 0):
                due = now + self._delay_s
                if self._delay_count is not None:
                    self._delay_count -= 1
            if self._stale_before_next > 0:
                self._stale_before_next -= 1
                stale = bytes(((seq - 1) >> 8 & 0xFF, (seq - 1) & 0xFF)) + reply[2:]
                self._queue(due, stale, src)
            self._queue(due, reply, src)

    def _serve(self) -> None:
        sock = self._sock
        while not self._stop.is_set():
            now = time.monotonic()
            timeout = 0.005
            with self._lock:
                while self._outbox and self._outbox[0][0] <= now:
                    _, _, data, addr = heapq.heappop(self._outbox)
                    try:
                        sock.sendto(data, addr)
                    except OSError:
                        pass
                if self._outbox:
                    timeout = max(0.0, min(timeout, self._outbox[0][0] - now))
                self._advance(now)
            try:
                ready, _, _ = select.select([sock], [], [], timeout)
            except (OSError, ValueError):
                return
            if ready:
                try:
                    raw, src = sock.recvfrom(65535)
                except OSError:
                    continue
                self._on_datagram(raw, src, time.monotonic())
