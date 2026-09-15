"""Job records -> FIFO items for register 0x66 (11 §5.2-§5.4, A3 §2-§6, §8).

Three layers, mirroring the vendor split (A3 §1):

1. :class:`TickQuantizer` - MainApp ``0x437500`` / ``0x450130``: XY millimetres per interpolation
   cycle -> motor pulses per axis, ``Δ = trunc(ΔXs·f + carry)`` with ``Xs = X·10000`` and
   ``f = WritePluse / SpeedRatio / 10000``; the per-axis residual carry persists across contours
   and jobs (11 §5.3).  The task text says "round-with-carry"; the binary truncates (``_ftol2``,
   A3 verifier) and that is what is implemented.
2. :class:`Record` + :class:`ItemBuilder` - the 28-byte job record (A3 §2) and the NCModule item
   builder VM slot 121 (A3 §4): opcode 3000 ticks (X low int16, Y high int16), the 3001 auto
   boundary, 3002 mode, ``9999`` DO/DA/PWM, and the ZF family ``103/109/118/2001`` which CO2 jobs
   on this machine emit because ``ZF.ZFType = 1`` (11 C6).
3. :class:`JobStreamBuilder` - the MainApp contour sequencer (``0x4409a0``, only partly traced):
   rapid ticks, the per-contour prologue/epilogue records observed in the leaked CO2 frames
   (11 §5.4, frames 504/57 and 633) and the cut ticks with per-tick PWM.

Laser safety (PORT-PLAN §8.2): every item that can emit light carries ``laser=True`` (a DO record
switching a laser port on, a PWM-set record with duty > 0, a tick with duty > 0, a non-zero DA).
:class:`JobStreamBuilder` defaults to ``laser_records=False`` (dry run: duty 0 and no laser DO /
PWM-set records at all); even with ``laser_records=True`` the frames still pass through
:func:`nexcut.mcc.safety.strip_laser_records` unless the machine is LASER_ARMED.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum

import numpy as np
from numpy.typing import ArrayLike

from nexcut.mcc.commands import MISC_DA, MISC_DO, MISC_DO_EXT, MISC_PWM, MISC_PWM_5V
from nexcut.mcc.fifo import FramePacker, PackedFrame, item_header

__all__ = [
    "FREQ_DEFAULT",
    "MODE_AFTER_LASER_OFF",
    "MODE_BEFORE_LASER_ON",
    "OP_BOUNDARY",
    "OP_MISC",
    "OP_MODE",
    "OP_TICK",
    "OP_WAIT",
    "OP_ZF_BOOK",
    "OP_ZF_LIFT",
    "OP_ZF_MOVE",
    "AxisScale",
    "IoSub",
    "Item",
    "ItemBuilder",
    "JobStreamBuilder",
    "Record",
    "RecordType",
    "StreamConfig",
    "TickQuantizer",
    "UnsupportedConfiguration",
    "do_item",
    "dwell_tick_count",
    "encode_item",
    "gas_do_port",
    "tick_item",
    "wait_2001_word",
]

OP_TICK = 3000
OP_BOUNDARY = 3001
OP_MODE = 3002
OP_MISC = 9999
OP_ZF_MOVE = 103
OP_ZF_LIFT = 109
OP_ZF_BOOK = 118
OP_WAIT = 2001

# 9999 sub-records share their numbers with the 0x65 builders (11 §5.2, A3 §6): take them from
# mcc.commands so the planner, the simulator and the safety gate cannot drift apart.
SUB_DO = MISC_DO
SUB_PWM = MISC_PWM
SUB_DA = MISC_DA
SUB_DO_EXT = MISC_DO_EXT
SUB_PWM_5V = MISC_PWM_5V

FREQ_DEFAULT = 5000
"""Tick frequency word when the record's freq < 1 (``0x10045356-0x10045384``, A3 §4.2)."""
MODE_BEFORE_LASER_ON = 5
"""3002 value before laser-on in the leaked CO2 job (A3 §5; meaning INFERENCE)."""
MODE_AFTER_LASER_OFF = 4
"""3002 value after laser-off (A3 §5)."""
WAIT_TIME_DEFAULT_MS = 3000
"""Second word of ``2001[ms, 3000]`` (record type 1 sub 5, A3 §4.4)."""
ZF_CONDITION_FOLLOW = 2
"""Mode-3 condition word r of ``2001[r | 0x03000000, FollowOvertime]`` (A3 §4.4, INFERENCE)."""
INTERP_CYCLE_US = 250
"""``AX.InterpolationCycle`` default [µs] (BkHardPara.xml); the card refreshes it from reg 50005
(A3 V3).  UNVERIFIED card-side period (11 §7 step 8)."""
GAS_TYPE_DO = ("MGP.LowAir", "MGP.LowO2", "MGP.LowN2", "MGP.HighAir", "MGP.HighO2", "MGP.HighN2")
"""``CutGasType`` 0..5 -> hardware DO descriptor (02 §2.5, INFERENCE high)."""


class UnsupportedConfiguration(ValueError):
    """A configuration the port refuses to stream (11 §5.2 "Refuse" row, fibre-only paths)."""


# ================================================================================================
# Items
# ================================================================================================


@dataclass(frozen=True, slots=True)
class Item:
    """One FIFO item ``header, args…`` (11 §5.2) plus the port's laser flag."""

    opcode: int
    args: tuple[int, ...] = ()
    laser: bool = False

    @property
    def header(self) -> int:
        """``(payload_bytes << 16) | opcode``."""
        return item_header(self.opcode, len(self.args))

    def words(self) -> list[int]:
        """Header followed by the payload words (u32)."""
        return [self.header, *(a & 0xFFFFFFFF for a in self.args)]


def encode_item(opcode: int, args: Sequence[int] = ()) -> list[int]:
    """``[header, args…]`` (A3 §9 ``encode_item``)."""
    return Item(opcode, tuple(int(a) for a in args)).words()


def _u16(v: int, what: str) -> int:
    if not -0x8000 <= v <= 0x7FFF:
        raise ValueError(f"{what} {v} outside int16: the packer would corrupt it (A3 V)")
    return v & 0xFFFF


def tick_item(dx: int, dy: int, freq: int, duty: int) -> Item:
    """Opcode 3000 ``[(u16 dY << 16) | u16 dX, (freq << 16) | duty]`` (A3 §4.2).

    ``freq < 1`` becomes 5000; duty is the record's u8, freq its u16.
    """
    f = int(freq) & 0xFFFF
    if f < 1:
        f = FREQ_DEFAULT
    d = int(duty) & 0xFF
    w1 = (_u16(int(dy), "dY") << 16) | _u16(int(dx), "dX")
    return Item(OP_TICK, (w1, (f << 16) | d), laser=d > 0)


def do_item(xml_port: int, on: bool, laser_ports: Iterable[int] = (5, 9)) -> Item:
    """DO record: ``9999[2, 1<<p, v<<p]`` for XML ports 1..10, ``9999[13, …]`` for 11..26,
    ``p = port - 1`` (A3 §4.4 type 1 sub 4, §6)."""
    p = int(xml_port) - 1
    v = 1 if on else 0
    if 0 <= p < 10:
        args = (SUB_DO, 1 << p, v << p)
    elif 10 <= p < 26:
        args = (SUB_DO_EXT, 1 << (p - 10), v << (p - 10))
    else:
        raise ValueError(f"DO port {xml_port} outside 1..26")
    return Item(OP_MISC, args, laser=bool(on) and int(xml_port) in set(laser_ports))


def wait_2001_word(mode: int, p: int = 0, q: int = 0, r: int = 0) -> int:
    """First word of ``2001`` per mode (emit2001 ``0x1003e5a0``, A3 §5): 0 ``p``;
    1 ``((q|0x10000)<<8)|r``; 2 ``((q|0x20000)<<8)|r``; 3 ``r|0x03000000``."""
    if mode == 0:
        return p & 0xFFFFFFFF
    if mode == 1:
        return (((q | 0x10000) << 8) | r) & 0xFFFFFFFF
    if mode == 2:
        return (((q | 0x20000) << 8) | r) & 0xFFFFFFFF
    if mode == 3:
        return (r | 0x03000000) & 0xFFFFFFFF
    raise ValueError("emit2001 emits nothing for mode > 3")


def dwell_tick_count(ms: float, cycle_us: int = INTERP_CYCLE_US) -> int:
    """``(1000 idiv cycle) · ms`` stationary ticks (``0x4373ec-0x4373f8``, A3 §3 / V: integer
    division first).  ``ms`` is truncated to an int as the vendor argument is an int."""
    if cycle_us <= 0:
        raise ValueError("cycle must be > 0")
    return (1000 // int(cycle_us)) * max(int(ms), 0)


# ================================================================================================
# Tick quantiser (MainApp 0x437500 / 0x450130)
# ================================================================================================


@dataclass(frozen=True, slots=True)
class AxisScale:
    """Pulses per mm per axis = ``WritePluse / SpeedRatio`` (11 §5.3, A3 §3 V1).

    X: card axis-0 AxisRW words 9/10 (lead, command pulses) when refreshed, else XML ``MAC``;
    Y: always XML ``MAC_1`` (the card refresh loop skips slot 1).  This machine:
    8000/31.003 = 258.04 p/mm (X), 8000/31.009 = 257.99 p/mm (Y).
    """

    x_pulses: int = 8000
    x_lead: float = 31.003
    y_pulses: int = 8000
    y_lead: float = 31.009

    @property
    def x_factor(self) -> float:
        """``fild pulses; fdiv lead; fdiv 10000.0`` (``0x4378d0``) - per 0.1 µm."""
        return float(self.x_pulses) / float(self.x_lead) / 10000.0

    @property
    def y_factor(self) -> float:
        """Y factor (``0x4378d0`` for slot 1)."""
        return float(self.y_pulses) / float(self.y_lead) / 10000.0

    @property
    def x_per_mm(self) -> float:
        """Pulses per mm on X."""
        return self.x_factor * 10000.0

    @property
    def y_per_mm(self) -> float:
        """Pulses per mm on Y."""
        return self.y_factor * 10000.0

    @classmethod
    def from_values(
        cls,
        g: dict[str, float | int | str],
        card_axis0: tuple[int, int] | None = None,
        k: int = 1000,
    ) -> AxisScale:
        """From ``{"MAC.SpeedRatio", "MAC.WritePluse", "MAC_1.SpeedRatio", "MAC_1.WritePluse"}``.

        ``card_axis0 = (word9, word10)`` of AxisRW block 50200 replaces the X pair with
        ``word9 / K`` mm and ``word10`` pulses (``0x10039ed5-0x10039ef8``).
        """
        xl, xp = float(g["MAC.SpeedRatio"]), int(float(g["MAC.WritePluse"]))
        if card_axis0 is not None:
            xl, xp = card_axis0[0] / float(k), int(card_axis0[1])
        return cls(xp, xl, int(float(g["MAC_1.WritePluse"])), float(g["MAC_1.SpeedRatio"]))


class TickQuantizer:
    """Truncate-with-carry pulse quantiser (MainApp ``0x437500``, ``0x450130``; 11 §5.3).

    For a point list ``P_0..P_n`` (mm) it returns ``n`` increments per axis:
    ``t = (Xs_i - Xs_{i-1})·f + carry``, ``cell = trunc(t)``, ``carry = t - cell``.  The carry is
    never reset by the vendor (static array ``0x9495b0``); :meth:`reset` exists for tests.
    Arithmetic is IEEE double here, x87 80-bit in the vendor (differences are far below a pulse).
    """

    def __init__(self, scale: AxisScale | None = None, carry: tuple[float, float] = (0.0, 0.0)):
        self.scale = scale or AxisScale()
        self.carry = [float(carry[0]), float(carry[1])]

    def reset(self, carry: tuple[float, float] = (0.0, 0.0)) -> None:
        """Set the residual carry (the vendor never does this)."""
        self.carry = [float(carry[0]), float(carry[1])]

    def quantize(self, points_mm: ArrayLike) -> tuple[list[int], list[int]]:
        """Increments for consecutive points (``n x 2`` mm) -> ``(dx, dy)`` pulse lists."""
        pts = np.asarray(points_mm, dtype=np.float64).reshape(-1, 2)
        if len(pts) < 2:
            return [], []
        out = []
        for axis, f in ((0, self.scale.x_factor), (1, self.scale.y_factor)):
            scaled = pts[:, axis] * 10000.0
            d = (np.diff(scaled) * f).tolist()
            c = self.carry[axis]
            cells: list[int] = []
            append = cells.append
            for v in d:
                t = v + c
                cell = int(t)  # truncation toward zero (_ftol2)
                c = t - cell
                append(cell)
            self.carry[axis] = c
            out.append(cells)
        return out[0], out[1]


# ================================================================================================
# Records and the NCModule item builder (VM slot 121)
# ================================================================================================


class RecordType(IntEnum):
    """Record type byte ``rec[+0x16]`` (A3 §2, §4.4)."""

    TICK = 0
    IO = 1
    MODE = 3
    FIBRE_LASER_ON = 4
    FIBRE_LASER_OFF = 5
    WAIT_CONDITION = 6
    ZF_SECTION = 7
    ZF_LIFT = 8
    ZF_DOCK = 9
    ZF_CALIBRATE = 0xA
    ANALOG_POWER = 0xB
    SKIP_C = 0xC
    SKIP_D = 0xD
    BOUNDARY = 0xE
    SKIP_F = 0xF
    SKIP_10 = 0x10
    ZF_CUT_HEIGHT = 0x11
    FIBRE_POWER = 0x12
    ZF_WAIT = 0x13


class IoSub(IntEnum):
    """Type-1 sub-codes ``rec[+0]`` (jump table ``0x100454c8``, A3 §4.4)."""

    PWM_1 = 1
    PWM_2 = 2
    DA = 3
    DO = 4
    WAIT_MS = 5
    ZF_BOOK = 6


@dataclass(slots=True)
class Record:
    """The 28-byte job record (A3 §2) with named cells.

    ``cell0`` = X increment or sub-code, ``cell1`` = Y increment or port index (XML port − 1) /
    DA channel, ``duty`` (+0x12, u8), ``freq`` (+0x14, u16), ``type`` (+0x16), ``value`` (+0x18).
    """

    type: int
    cell0: int = 0
    cell1: int = 0
    duty: int = 0
    freq: int = 0
    value: int = 0

    @classmethod
    def tick(cls, dx: int, dy: int, freq: int = 0, duty: int = 0) -> Record:
        """Motion tick record (type 0)."""
        return cls(RecordType.TICK, dx, dy, duty, freq)

    @classmethod
    def do(cls, xml_port: int, on: bool) -> Record:
        """DO record (type 1 sub 4, ``cell1 = port - 1``, MainApp ``0x4426c2``)."""
        return cls(RecordType.IO, IoSub.DO, int(xml_port) - 1, value=1 if on else 0)

    @classmethod
    def mode(cls, value: int) -> Record:
        """3002 record (type 3)."""
        return cls(RecordType.MODE, value=value)


_AUTO_BOUNDARY_TYPES = frozenset(t for t in range(1, 0x14) if t != RecordType.BOUNDARY)
"""``1 <= t < 0x14 && t != 0xe`` (``0x10043c03-0x10043c2a``)."""
_NOTHING_TYPES = frozenset({0xC, 0xD, 0xF, 0x10})


@dataclass(frozen=True, slots=True)
class StreamConfig:
    """Machine values the item builder reads from ``g`` / ``VM`` (A3 §4.4 parameter names).

    Defaults = this CF1390 as evidenced by the leaked frames and the XML files.
    """

    laser_type: int = 1
    """``SP.m_iEnableLaserType`` (``g+0x46d8``): 0 fibre, 1 CO2, 2 blue."""
    co2_control_type: int = 2
    """``LGP.CO2LaserControlType`` (``g+0x4940``)."""
    zf_type: int = 1
    """``ZF.ZFType`` (``g+0x490c``); card SystemRW word 6 is authoritative (A3 V3)."""
    zf_move_speed: float = 100.0
    """``g+0x49c8`` (unnamed); 100.0 derived from ``103[1000, 0]`` = ``int(v·10)`` (A3 §8).
    UNVERIFIED descriptor (ZFFollowSpeed = ZFUpSpeed = 100 here, so either could be it)."""
    zf_up_speed: float = 100.0
    """``ZF.ZFUpSpeed`` (``g+0x49d0``)."""
    zf_dock_height: float = 20.0
    """``ZF.ZFDockHeight`` (``g+0x49e8``)."""
    zf_cut_height: float = 0.0
    """``ZF+0x58`` target of the prologue 103 (0 for the CO2 layer, A3 §4.4 row 0x11)."""
    zf_lift_height: float = 0.0
    """``ZF+0x68`` target of the epilogue 109 (0 in the leaked frame)."""
    zf_book_value: float = 35.0
    """``g+0x4d28`` (unnamed) - third word of ``118[4, 0, 35]``; UNVERIFIED descriptor."""
    zf_book_flag_arg: float = 0.0
    """``g+0x4d30`` - 118 flag argument in record 8; kept only for fibre (A3 §5 row 118 V)."""
    zf_book_enabled: bool = True
    """``g+0x4d24`` byte gate of 118 (set here, the leaked epilogue carries 118)."""
    follow_overtime_ms: int = 20000
    """``ipAdd.ini [Soft] FollowOvertime`` (``VM+0x858``; default 3000, this machine 20000)."""
    section_drill_overtime_ms: int = 20000
    """``ipAdd.ini [Soft] SectionDrillOvertime`` (``VM+0x85c``)."""
    sub_device_type: int = 2
    """``g+0x4a20`` - the "tail" after 2001 adds nothing for 2/3, which matches the frames.
    UNVERIFIED actual value (any of 2/3 reproduces the evidence)."""
    laser_do_ports: frozenset[int] = frozenset({5, 9})
    """DO ports whose ON record is a laser record (DO5 fibre gate, DO9 CO2, A3 §6)."""

    def __post_init__(self) -> None:
        lt, ctl = self.laser_type, self.co2_control_type
        if lt != 0 and ctl not in (1, 2) and not (lt == 1 and ctl == 3):
            raise UnsupportedConfiguration(
                f"LaserType={lt}, CO2LaserControlType={ctl}: the vendor packer emits a malformed "
                "3000 item here (A3 §4.3 verifier, 11 §5.2 Refuse)"
            )
        if lt == 1 and ctl == 3:
            raise UnsupportedConfiguration(
                "CO2 analogue PWM word (A3 §4.3, 14-bit wrap) is not implemented; not used on "
                "this machine (CO2LaserControlType = 2)"
            )

    @property
    def pwm_sub(self) -> int:
        """``0x11`` when CO2 with control type 2 (this machine), else ``3`` (A3 §4.4 type 1)."""
        return SUB_PWM_5V if (self.laser_type == 1 and self.co2_control_type == 2) else SUB_PWM

    @property
    def zf_on(self) -> bool:
        """``g+0x490c != 0``."""
        return self.zf_type != 0


class ItemBuilder:
    """NCModule record -> item translation (VM slot 121 ``0x100437d0``, A3 §4.4).

    Implemented: types 0, 1 (subs 1-6), 3, 6 (sub 3), 8 (non-drill branch), 9, 0xe, 0x11, 0x13 and
    the no-op types 0xc/0xd/0xf/0x10.  The fibre / ZF-section paths (4, 5, 7, 0xa, 0xb, 0x12 and
    record 8's ``v & 0x10`` drill) raise :class:`UnsupportedConfiguration` - not traced to the
    field level and never emitted for the CO2 job (11 §5.4).
    """

    def __init__(self, config: StreamConfig | None = None) -> None:
        self.config = config or StreamConfig()
        self.prev_type = -1

    def new_batch(self) -> None:
        """``VM+0x1174 = -1`` on every record push (``0x10047880``, A3 V): no auto 3001 for the
        first control record of a pushed batch."""
        self.prev_type = -1

    def _tail(self) -> list[Item]:
        """``0x1003ede0`` dispatch on ``g+0x4a20``: 2/3 add nothing (this machine)."""
        if self.config.sub_device_type in (2, 3):
            return []
        raise UnsupportedConfiguration(
            f"sub-device type {self.config.sub_device_type}: 2004/2001 tail not implemented (A3 §4.4)"
        )

    def _wait(self, word: int, timeout: int) -> Item:
        return Item(OP_WAIT, (word & 0xFFFFFFFF, int(timeout)))

    def build(self, rec: Record) -> list[Item]:
        """Items for one record, including the automatic 3001 in front."""
        cfg = self.config
        t = int(rec.type)
        out: list[Item] = []
        if t in _AUTO_BOUNDARY_TYPES and self.prev_type == RecordType.TICK:
            out.append(Item(OP_BOUNDARY))
        if t == RecordType.TICK:
            out.append(tick_item(rec.cell0, rec.cell1, rec.freq, rec.duty))
        elif t == RecordType.IO:
            out.extend(self._io(rec))
        elif t == RecordType.MODE:
            out.append(Item(OP_MODE, (rec.value & 0xFFFFFFFF,)))
        elif t == RecordType.WAIT_CONDITION:
            if rec.cell0 == 3:
                out.append(self._wait(rec.value | 0x03000000, cfg.follow_overtime_ms))
        elif t == RecordType.ZF_LIFT:
            if cfg.zf_on:
                if rec.value & 0x10 and cfg.zf_type != 5:
                    raise UnsupportedConfiguration("record 8 drill branch (106) is fibre-only")
                out.append(
                    Item(
                        OP_ZF_LIFT,
                        (int(cfg.zf_move_speed * 10.0), int(cfg.zf_lift_height * -1000.0)),
                    )
                )
                out.append(
                    self._wait(wait_2001_word(3, r=ZF_CONDITION_FOLLOW), cfg.follow_overtime_ms)
                )
                if cfg.zf_book_enabled:
                    out.append(self._book(int(cfg.zf_book_flag_arg)))
        elif t == RecordType.ZF_DOCK:
            if cfg.zf_on:
                out.append(
                    Item(
                        OP_ZF_LIFT, (int(cfg.zf_up_speed * 10.0), int(cfg.zf_dock_height * -1000.0))
                    )
                )
                out.append(
                    self._wait(wait_2001_word(3, r=ZF_CONDITION_FOLLOW), cfg.follow_overtime_ms)
                )
        elif t == RecordType.BOUNDARY:
            out.append(Item(OP_BOUNDARY))
        elif t == RecordType.ZF_CUT_HEIGHT:
            if cfg.zf_on:
                out.append(
                    Item(
                        OP_ZF_MOVE, (int(cfg.zf_move_speed * 10.0), int(cfg.zf_cut_height * 1000.0))
                    )
                )
                out.append(
                    self._wait(wait_2001_word(3, r=ZF_CONDITION_FOLLOW), cfg.follow_overtime_ms)
                )
                out.extend(self._tail())
        elif t == RecordType.ZF_WAIT:
            if cfg.zf_on:
                out.append(
                    self._wait(
                        wait_2001_word(3, r=ZF_CONDITION_FOLLOW), cfg.section_drill_overtime_ms
                    )
                )
                out.extend(self._tail())
        elif t in _NOTHING_TYPES:
            pass
        else:
            raise UnsupportedConfiguration(f"record type {t:#x} is not supported by the port")
        self.prev_type = t
        return out

    def _book(self, arg: int) -> Item:
        flag = arg if (arg != 0 and self.config.laser_type == 0) else 0
        return Item(OP_ZF_BOOK, (4, flag & 0xFFFFFFFF, int(self.config.zf_book_value)))

    def _io(self, rec: Record) -> list[Item]:
        cfg = self.config
        sub, p, v = int(rec.cell0), int(rec.cell1), int(rec.value)
        if sub in (IoSub.PWM_1, IoSub.PWM_2):
            duty = rec.duty & 0xFF
            return [Item(OP_MISC, (cfg.pwm_sub, rec.freq & 0xFFFF, duty), laser=duty > 0)]
        if sub == IoSub.DA:
            return [Item(OP_MISC, (SUB_DA, p & 0xFFFFFFFF, v & 0xFFFFFFFF), laser=v != 0)]
        if sub == IoSub.DO:
            if v not in (0, 1):
                raise ValueError("DO record value must be 0 or 1")
            return [do_item(p + 1, bool(v), cfg.laser_do_ports)]
        if sub == IoSub.WAIT_MS:
            return [self._wait(v, WAIT_TIME_DEFAULT_MS)] if v > 0 else []
        if sub == IoSub.ZF_BOOK:
            return [self._book(v)]
        raise UnsupportedConfiguration(f"IO sub-code {sub} not in jump table 0x100454c8")

    def build_all(self, records: Iterable[Record]) -> list[list[Item]]:
        """Items per record for one pushed batch (resets the 3001 state first)."""
        self.new_batch()
        return [self.build(r) for r in records]


# ================================================================================================
# Contour sequencer (MainApp side, partly INFERENCE)
# ================================================================================================


def gas_do_port(gas_type: int, hard_values: dict[str, float | int | str]) -> int:
    """XML DO port of ``CutGasType`` (02 §2.5): 3 = ``MGP.HighAir`` = DO3 here.  0 = unassigned."""
    if not 0 <= gas_type < len(GAS_TYPE_DO):
        return 0
    return int(float(hard_values.get(GAS_TYPE_DO[gas_type], 0)))


@dataclass(slots=True)
class ContourLaser:
    """Laser/gas settings of one contour (from the layer record, 02 §2.3 CO2 table)."""

    gas_port: int = 3
    """XML DO of the cut gas (``CutGasType`` 3 -> ``MGP.HighAir`` = 3)."""
    laser_port: int = 9
    """``LGP.CO2DOLaser``."""
    pierce_dwell_ms: float = 100.0
    """Stationary laser-on ticks after the laser DO (frame 504 shows >= 13). UNVERIFIED source:
    the CO2 layer has ``LaserOnDelay = 0``; ``GC.GasDelay = 100`` ms is used as the default."""


@dataclass(slots=True)
class JobStreamBuilder:
    """Assemble a job's record list and pack it into FIFO frames.

    Per contour (11 §5.4, A3 §8 frames 504/57/633):

    * prologue records ``0xe; 3[5]; tick(0,0); DO gas on; 0x11; DO laser on`` -> items
      ``3001; 3002[5]; tick; 3001; 9999[2,4,4]; 103[1000,0]; 2001[0x03000002,20000];
      9999[2,0x100,0x100]`` (the leading 3001 is the explicit type-0xe marker, no auto-marker
      because 0xe is excluded; the one-tick ``(0,0)`` record between 3002 and the DO is
      reproduced as observed, role UNVERIFIED);
    * dwell ticks at the cut PWM, then the cut ticks;
    * epilogue records ``DO laser off; 8; 0xe; 3[4]`` -> ``3001; 9999[2,0x100,0]; 109[1000,0];
      2001[…]; 118[4,0,35]; 3001; 3002[4]``.

    Gas is switched off once at job end (UNVERIFIED: frame 633 shows no gas-off in the epilogue).
    ``laser_records=False`` (default, dry run PORT-PLAN §8.2): ticks carry duty 0 and the laser DO
    records are not emitted at all.
    """

    config: StreamConfig = field(default_factory=StreamConfig)
    quantizer: TickQuantizer = field(default_factory=TickQuantizer)
    laser_records: bool = False
    records: list[Record] = field(default_factory=list)
    _gas_on: set[int] = field(default_factory=set)

    def _ticks(
        self, dx: Sequence[int], dy: Sequence[int], freq: ArrayLike, duty: ArrayLike
    ) -> None:
        n = len(dx)
        f = np.broadcast_to(np.asarray(freq, dtype=np.int64), (n,)).tolist()
        d = np.broadcast_to(np.asarray(duty, dtype=np.int64), (n,)).tolist()
        if not self.laser_records:
            d = [0] * n
        tick = RecordType.TICK
        self.records.extend(
            Record(tick, x, y, dd, ff) for x, y, ff, dd in zip(dx, dy, f, d, strict=True)
        )

    def add_motion(self, points_mm: ArrayLike, freq: ArrayLike = 0, duty: ArrayLike = 0) -> int:
        """Quantise a sampled point list and append one tick record per interval.

        ``freq``/``duty`` are scalars or per-interval arrays (length ``n - 1``).  Returns the
        number of ticks.
        """
        dx, dy = self.quantizer.quantize(points_mm)
        self._ticks(dx, dy, freq, duty)
        return len(dx)

    def add_dwell(
        self, ms: float, freq: int = 0, duty: int = 0, cycle_us: int = INTERP_CYCLE_US
    ) -> int:
        """Stationary ticks ``(1000 idiv cycle)·ms`` (dwell builder ``0x4427e0``, A3 V)."""
        n = dwell_tick_count(ms, cycle_us)
        self._ticks([0] * n, [0] * n, freq, duty)
        return n

    def prologue(
        self, laser: ContourLaser, freq: int, duty: int, cycle_us: int = INTERP_CYCLE_US
    ) -> None:
        """Contour start records (11 §5.4)."""
        self.records.append(Record(RecordType.BOUNDARY))
        self.records.append(Record.mode(MODE_BEFORE_LASER_ON))
        self.records.append(Record.tick(0, 0))
        if laser.gas_port > 0:
            self.records.append(Record.do(laser.gas_port, True))
            self._gas_on.add(laser.gas_port)
        self.records.append(Record(RecordType.ZF_CUT_HEIGHT))
        if self.laser_records and laser.laser_port > 0:
            self.records.append(Record.do(laser.laser_port, True))
        self.add_dwell(laser.pierce_dwell_ms, freq, duty, cycle_us)

    def epilogue(self, laser: ContourLaser) -> None:
        """Contour end records (11 §5.4)."""
        if laser.laser_port > 0:  # port <= 0 = unassigned: nothing is written (A3 §6)
            self.records.append(Record.do(laser.laser_port, False))
        self.records.append(Record(RecordType.ZF_LIFT))
        self.records.append(Record(RecordType.BOUNDARY))
        self.records.append(Record.mode(MODE_AFTER_LASER_OFF))

    def add_contour(
        self,
        points_mm: ArrayLike,
        freq: ArrayLike,
        duty: ArrayLike,
        laser: ContourLaser | None = None,
        cycle_us: int = INTERP_CYCLE_US,
    ) -> int:
        """Prologue + cut ticks + epilogue.  ``freq``/``duty`` per interval or scalar; the dwell
        uses the first interval's PWM."""
        laser = laser or ContourLaser()
        f0 = int(np.atleast_1d(np.asarray(freq))[0]) if np.size(freq) else 0
        d0 = int(np.atleast_1d(np.asarray(duty))[0]) if np.size(duty) else 0
        self.prologue(laser, f0, d0, cycle_us)
        n = self.add_motion(points_mm, freq, duty)
        self.epilogue(laser)
        return n

    def finish(self) -> None:
        """Job end: every gas DO switched on by a prologue goes off (UNVERIFIED placement)."""
        for port in sorted(self._gas_on):
            self.records.append(Record.do(port, False))
        self._gas_on.clear()

    def items(self) -> list[list[Item]]:
        """Items per record (one pushed batch)."""
        return ItemBuilder(self.config).build_all(self.records)

    def frames(self) -> list[PackedFrame]:
        """Pack every record into frames (flush at 300 words and at the end of the batch)."""
        packer = FramePacker()
        for group in self.items():
            packer.add_record([it.words() for it in group], sum(1 for it in group if it.laser))
        packer.end_batch()
        return packer.frames


def items_duration_s(items: Iterable[Item], cycle_us: int = INTERP_CYCLE_US) -> float:
    """Tick count x cycle (the card-side period is UNVERIFIED, 11 O1)."""
    return sum(1 for it in items if it.opcode == OP_TICK) * cycle_us * 1e-6


def total_ticks(frames: Iterable[PackedFrame]) -> int:
    """Number of 3000 items in packed frames (header scan)."""
    n = 0
    for f in frames:
        i = 0
        data = f.data
        while i < len(data):
            h = data[i]
            if h & 0xFFFF == OP_TICK:
                n += 1
            i += 1 + (h >> 16) // 4
    return n
