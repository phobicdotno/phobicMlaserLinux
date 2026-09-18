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

**Two paths through those three layers, and they agree to within one pulse.**  The layers above
are the *definition*: one :class:`Record`, one :class:`Item` and one word list per 250 µs tick,
which is what the vendor goldens are re-encoded through.  The grammar is identical on both paths and
the pulse totals are exactly equal; the *placement* of a pulse can differ by one 250 µs cycle on
a constant-speed run (review finding R-V4 - the bound and the measured numbers are at
:func:`carry_cells`, and in ``docs/STATUS.md`` §1.5).  Production uses :class:`JobFrameStream`
instead, which keeps the identical grammar but does the tick -> item -> word -> frame path in
numpy array operations (:func:`carry_cells`, :func:`tick_words`,
:meth:`nexcut.mcc.fifo.FrameStream.push_uniform`) and *yields* frames as they close - a
100 000-contour job is 67.5 M ticks and 202 M words and can be neither built nor held one object
at a time (PORT-PLAN §8.3, ``docs/STATUS.md`` §5 task 6).  Only the handful of control records
per contour still goes through :class:`ItemBuilder`, so the record grammar - the automatic 3001
after a run of ticks included - is written down exactly once.
``tests/test_plan_vectorised_equivalence.py`` holds the two paths together.

Laser safety (PORT-PLAN §8.2): every item that can emit light carries ``laser=True`` (a DO record
switching a laser port on, a PWM-set record with duty > 0, a tick with duty > 0, a non-zero DA).
:class:`JobStreamBuilder` defaults to ``laser_records=False`` (dry run: duty 0 and no laser DO /
PWM-set records at all); even with ``laser_records=True`` the frames still pass through
:func:`nexcut.mcc.safety.strip_laser_records` unless the machine is LASER_ARMED.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from enum import IntEnum

import numpy as np
from numpy.typing import ArrayLike, NDArray

from nexcut.mcc.commands import MISC_DA, MISC_DO, MISC_DO_EXT, MISC_PWM, MISC_PWM_5V
from nexcut.mcc.fifo import FramePacker, FrameStream, PackedFrame, item_header

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
    "JobFrameStream",
    "JobStreamBuilder",
    "Record",
    "RecordType",
    "StreamConfig",
    "TickQuantizer",
    "UnsupportedConfiguration",
    "carry_cells",
    "carry_cells_scalar",
    "do_item",
    "dwell_tick_count",
    "encode_item",
    "gas_do_port",
    "tick_item",
    "tick_words",
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


def tick_words(
    dx: ArrayLike, dy: ArrayLike, freq: ArrayLike = 0, duty: ArrayLike = 0
) -> tuple[NDArray[np.uint32], NDArray[np.bool_]]:
    """Vectorised :func:`tick_item`: the ``[header, w1, w2]`` words of ``n`` opcode-3000 ticks.

    Returns the flat ``3n`` word array (u32, ready for
    :meth:`nexcut.mcc.fifo.FrameStream.push_uniform`) and the per-tick laser flag ``duty > 0``
    (PORT-PLAN §8.2).  ``freq``/``duty`` are scalars or per-tick arrays and follow the scalar
    rule of :func:`tick_item` exactly: the u16 mask is applied *before* the ``freq < 1`` test
    (A3 §4.2), so ``freq = -1`` stays ``0xffff`` and ``freq = 0x10000`` becomes
    :data:`FREQ_DEFAULT`.
    """
    x = np.asarray(dx, dtype=np.int64)
    y = np.asarray(dy, dtype=np.int64)
    if x.ndim != 1:
        x = x.reshape(-1)
    if y.ndim != 1:
        y = y.reshape(-1)
    if x.size != y.size:
        raise ValueError(f"dX has {x.size} ticks, dY has {y.size}")
    n = int(x.size)
    if n and (
        int(x.min()) < -0x8000
        or int(x.max()) > 0x7FFF
        or int(y.min()) < -0x8000
        or int(y.max()) > 0x7FFF
    ):
        # Same exception, same message and the same *tick* as the scalar path: `tick_item`
        # raises on the first offending tick and evaluates dY before dX inside it (the ``w1``
        # expression), so a report of "which tick overflowed" cannot depend on which path ran.
        k = int(np.argmax((x < -0x8000) | (x > 0x7FFF) | (y < -0x8000) | (y > 0x7FFF)))
        _u16(int(y[k]), "dY")
        _u16(int(x[k]), "dX")
    words = np.empty((n, 3), dtype=np.uint32)
    words[:, 0] = item_header(OP_TICK, 2)
    np.bitwise_or((y & 0xFFFF) << 16, x & 0xFFFF, out=words[:, 1], casting="unsafe")
    if np.ndim(freq) == 0 and np.ndim(duty) == 0:  # the common case: one PWM point per contour
        f = int(freq) & 0xFFFF
        d = int(duty) & 0xFF
        words[:, 2] = ((FREQ_DEFAULT if f < 1 else f) << 16) | d
        laser = np.broadcast_to(np.bool_(d > 0), (n,))
    else:
        f_arr = np.broadcast_to(np.asarray(freq, dtype=np.int64), (n,)) & 0xFFFF
        np.copyto(f_arr, FREQ_DEFAULT, where=f_arr < 1)
        d_arr = np.broadcast_to(np.asarray(duty, dtype=np.int64), (n,)) & 0xFF
        np.bitwise_or(f_arr << 16, d_arr, out=words[:, 2], casting="unsafe")
        laser = d_arr > 0
    return words.reshape(-1), laser


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
    """``(1000 idiv cycle) · ms`` stationary ticks (dwell builder ``0x4427e0``, A9 §2).

    Integer division first (``mov eax,0x3e8; cdq; idiv [g+0x4890]; imul eax,ms``); ``ms`` is
    truncated to an int as the vendor argument is an int.  **This branch is unreachable for a CO2
    cut**: it runs only when the builder's fourth argument is true, nine of the ten MainApp call
    sites push 0, and the tenth sits inside the multi-stage pierce emitter that only a layer with
    ``ManuType != 0`` enters (A9 §2.2).  Kept because the branch exists in the binary;
    :meth:`JobStreamBuilder.prologue` uses the wait-record branch instead.
    """
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


_CARRY_GRID_TARGET_BITS = 47
"""``|increment| · 2**bits <= 2**47``: the fixed-point grid :func:`carry_cells` rounds the
per-tick increments onto.  See that function for why 47 and not more."""
_CARRY_CHUNK = 1 << 15
"""Increments per ``cumsum`` block.  ``2**15 · 2**47 = 2**62`` keeps the running sum inside
int64 whatever the data."""
_CARRY_MAX_INCREMENT = float(1 << 40)
"""Above this the grid would need more than 52 bits; such an increment is ~10**10 pulses in one
250 µs tick, far past the int16 the packer writes, and falls back to :func:`carry_cells_scalar`."""
_CARRY_SCALAR_MAX = 256
"""Runs at most this long take the scalar loop: it is both *faster* (the vectorised path costs
~90 µs of numpy dispatch whatever its length, the loop ~0.15 µs per tick per axis, so they cross
at ~300 ticks on the review laptop) and exactly the reference by definition.  Every rapid between
two contours is well under it."""


def carry_cells_scalar(increments: ArrayLike, carry: float) -> tuple[list[int], float]:
    """The reference truncate-with-carry loop (MainApp ``0x437500``; 11 §5.3).

    ``t = increment + carry``, ``cell = trunc(t)`` (``_ftol2``), ``carry = t - cell``, in IEEE
    double.  Kept as the definition of the rule; :func:`carry_cells` is the vectorised form that
    production uses, and ``tests/test_plan_vectorised_equivalence.py`` holds the two together.
    """
    c = float(carry)
    cells: list[int] = []
    append = cells.append
    for v in np.asarray(increments, dtype=np.float64).tolist():
        t = v + c
        cell = int(t)  # truncation toward zero (_ftol2)
        c = t - cell
        append(cell)
    return cells, c


def carry_cells(
    increments: ArrayLike, carry: float | Sequence[float]
) -> tuple[NDArray[np.int64], float | NDArray[np.float64]]:
    """Vectorised :func:`carry_cells_scalar` - same cells, in numpy array operations.

    ``increments`` is one axis (``n``) with a scalar ``carry``, or several axes at once
    (``n x k``) with one carry per axis; the axes are done in the same array operations, which
    is where most of the speed comes from (the planner quantises X and Y together).

    **Why a prefix sum is not enough, and what makes this exact.**  The scalar rule truncates
    *toward zero*, so the residual carries the sign of ``t``; with ``floor`` the cells would be
    the plain difference of ``floor`` of the running sum, but with ``trunc`` a negative excursion
    shifts a pulse by one tick (``v = -0.5`` repeated gives ``0, -1, 0, -1`` truncating and
    ``-1, 0, -1, 0`` flooring).  The exact rule is recovered by carrying one *bit* of state:
    write ``A_k`` for the running sum, ``F_k`` for its floor, ``R_k`` for the remainder and
    ``Q_k`` for the pulses emitted so far, then ``Q_k = F_k + D_k`` with ``D_k`` in ``{0, 1}``,
    and ``D`` obeys a set/clear/hold machine (``m_k = F_k - F_{k-1}``):

    * ``R_k > 0 and m_k < 0``  -> ``D_k = 1``
    * ``R_k == 0 or m_k >= 1`` -> ``D_k = 0``
    * otherwise                 -> ``D_k = D_{k-1}``

    "the last set/clear wins" is two ``maximum.accumulate`` passes (the index of the last set
    against the index of the last clear), so the whole recurrence is data-parallel.
    ``cell_k = Q_k - Q_{k-1}`` and the residual is ``A_n - Q_n``.

    **Arithmetic.**  The recurrence runs in int64 on the fixed-point grid ``2**-bits``, with
    ``bits`` chosen so every increment is at most ``2**47`` grid units.  Rounding the increments
    onto that grid is what makes the result *exactly* the scalar loop's: a multiple of
    ``2**-bits`` below ``2**(48-bits)`` in magnitude needs 48 mantissa bits, so on gridded input
    the scalar ``t = v + c`` is computed without any rounding at all and both paths evaluate the
    same exact rational recurrence.  The only difference from the *ungridded* scalar loop is that
    each increment is first rounded to ~1 ulp (``2**-47`` of its own magnitude, ~4e-13 pulses on
    a 50-pulse tick), which can only change a cell if the scalar ``t`` lands within that distance
    of an integer; ``tests/test_plan_vectorised_equivalence.py`` holds the two together over
    millions of random ticks and proves the identity on gridded input.

    **The limit of that agreement** (review finding R-V4,
    ``tests/test_plan_vectorisation_review.py``).  With *random* increments the grid roundings
    are random too and the running sum wanders as ``sqrt(n)``, so the two paths agree cell for
    cell over millions of ticks.  With a **constant** increment - a straight line at constant
    speed, the commonest geometry there is - every tick gets the identical rounding and the sums
    separate linearly in ``n``: on a 1 m line 2 cells of 40 000 differ, on a 10**6-tick diagonal
    372.  What is guaranteed, and pinned by that file, is the bound rather than identity: the
    emitted position never differs from the scalar loop by more than **one pulse** at any tick
    (a pulse displaced by one 250 µs cycle, never dropped or duplicated), the totals are exactly
    equal, and the residual carry stays inside ``n · max|increment| · 2**-47``.  That is below
    the difference between this float64 model and the vendor's own x87 80-bit arithmetic, so it
    is not worth the ~3 % of planner throughput a finer grid would cost (the chunk length, and
    with it :data:`_CARRY_CHUNK`, has to shrink 64x to make the increments exact).
    """
    v = np.asarray(increments, dtype=np.float64)
    flat = v.ndim == 1
    # Internally one row per axis: the running sums and the two scans then walk memory
    # contiguously, which is worth more than the transpose costs.
    values = v.reshape(1, -1) if flat else v.T
    carry_in = np.atleast_1d(np.asarray(carry, dtype=np.float64)).reshape(-1)
    axes, n = values.shape
    if carry_in.size != axes:
        raise ValueError(f"{carry_in.size} carries for {axes} axes")
    if n == 0:
        return np.zeros((0,) if flat else (0, axes), dtype=np.int64), carry
    if n <= _CARRY_SCALAR_MAX:
        return _carry_cells_rows(values, carry_in, flat)
    maxabs = max(float(values.max()), -float(values.min()))
    if not np.isfinite(maxabs) or maxabs >= _CARRY_MAX_INCREMENT:
        return _carry_cells_rows(values, carry_in, flat)
    _, exp = np.frexp(maxabs)  # maxabs < 2**exp
    bits = int(min(52, max(0, _CARRY_GRID_TARGET_BITS - int(exp))))
    unit = 1 << bits
    c = np.clip(np.rint(carry_in * float(unit)), -unit + 1, unit - 1).astype(np.int64)
    c = c.reshape(-1, 1)
    # order="C": `values` is a transposed view for the 2-D case, and the running sums below
    # are much cheaper on rows that are contiguous in memory.
    scaled = np.rint(values * float(unit)).astype(np.int64, order="C")
    out = np.empty((axes, n), dtype=np.int64)
    index = np.arange(min(n, _CARRY_CHUNK), dtype=np.int64).reshape(1, -1)
    for lo in range(0, n, _CARRY_CHUNK):
        block = scaled[:, lo : lo + _CARRY_CHUNK]
        rows = block.shape[1]
        idx = index[:, :rows]
        a = np.cumsum(block, axis=1)
        a += c
        f = a >> bits  # floor: arithmetic shift
        r = a & (unit - 1)  # a - (f << bits), in [0, unit)
        f_prev = c >> bits  # 0 for a non-negative carry, -1 for a negative one
        m = np.empty_like(f)
        np.subtract(f[:, 1:], f[:, :-1], out=m[:, 1:])
        m[:, 0] = f[:, 0] - f_prev[:, 0]
        positive = r > 0
        rise = positive & (m < 0)
        clear = ~positive | (m >= 1)
        # D starts at -f_prev (1 for a negative carry); seed the two scans so that state wins
        # until the first real event.
        started = -f_prev  # 0 or 1 per axis
        last_rise = np.where(rise, idx, started - 2)
        last_clear = np.where(clear, idx, -1 - started)
        np.maximum.accumulate(last_rise, axis=1, out=last_rise)
        np.maximum.accumulate(last_clear, axis=1, out=last_clear)
        q = f
        q += last_rise > last_clear  # Q = F + D, in place on f
        np.subtract(q[:, 1:], q[:, :-1], out=out[:, lo + 1 : lo + rows])
        out[:, lo] = q[:, 0]  # Q of the previous tick is 0 at the start of every chunk
        c = a[:, -1:] - (q[:, -1:] << bits)
    carry_out = c.reshape(-1) / float(unit)
    if flat:
        return out[0], float(carry_out[0])
    return out.T, carry_out


def _carry_cells_rows(
    values: NDArray[np.float64], carry_in: NDArray[np.float64], flat: bool
) -> tuple[NDArray[np.int64], float | NDArray[np.float64]]:
    """:func:`carry_cells_scalar` per axis, on the ``k x n`` (row per axis) layout.

    Taken for a short run (:data:`_CARRY_SCALAR_MAX`, where the loop is the faster of the two)
    and for increments no fixed-point grid can hold - non-finite, or beyond
    :data:`_CARRY_MAX_INCREMENT` (> 2**40 pulses in one 250 µs tick).
    """
    cells = np.empty(values.shape, dtype=np.int64)
    out = np.empty(values.shape[0], dtype=np.float64)
    for axis in range(values.shape[0]):
        row, out[axis] = carry_cells_scalar(values[axis], float(carry_in[axis]))
        cells[axis] = row
    if flat:
        return cells[0], float(out[0])
    return cells.T, out


class TickQuantizer:
    """Truncate-with-carry pulse quantiser (MainApp ``0x437500``, ``0x450130``; 11 §5.3).

    For a point list ``P_0..P_n`` (mm) it returns ``n`` increments per axis:
    ``t = (Xs_i - Xs_{i-1})·f + carry``, ``cell = trunc(t)``, ``carry = t - cell``.  The carry is
    never reset by the vendor (static array ``0x9495b0``); :meth:`reset` exists for tests.
    Arithmetic is IEEE double here, x87 80-bit in the vendor (differences are far below a pulse).

    :meth:`quantize_arrays` is the vectorised path (:func:`carry_cells`); :meth:`quantize` is the
    list-returning wrapper the older callers use and keeps the arrays in :attr:`last_cells`, so a
    streaming caller that has to go through ``quantize`` (subclasses override it) pays no list
    round trip.
    """

    def __init__(self, scale: AxisScale | None = None, carry: tuple[float, float] = (0.0, 0.0)):
        self.scale = scale or AxisScale()
        self.carry = [float(carry[0]), float(carry[1])]
        self.last_cells: tuple[NDArray[np.int64], NDArray[np.int64]] | None = None
        """Cells of the last :meth:`quantize_arrays` call (see the class docstring)."""

    def reset(self, carry: tuple[float, float] = (0.0, 0.0)) -> None:
        """Set the residual carry (the vendor never does this)."""
        self.carry = [float(carry[0]), float(carry[1])]

    def quantize(self, points_mm: ArrayLike) -> tuple[list[int], list[int]]:
        """Increments for consecutive points (``n x 2`` mm) -> ``(dx, dy)`` pulse lists."""
        dx, dy = self.quantize_arrays(points_mm)
        return dx.tolist(), dy.tolist()

    def quantize_arrays(self, points_mm: ArrayLike) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
        """:meth:`quantize` without the list conversion; both axes in one pass (11 §5.3)."""
        pts = np.asarray(points_mm, dtype=np.float64).reshape(-1, 2)
        if len(pts) < 2:
            empty = np.zeros(0, dtype=np.int64)
            self.last_cells = (empty, empty)
            return empty, empty
        scaled = pts * 10000.0
        increments = scaled[1:] - scaled[:-1]
        increments *= (self.scale.x_factor, self.scale.y_factor)
        cells, carry = carry_cells(increments, self.carry)
        self.carry = [float(carry[0]), float(carry[1])]  # type: ignore[index]
        self.last_cells = (cells[:, 0], cells[:, 1])
        return self.last_cells


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
    pierce_dwell_ms: float = 0.0
    """``layer.LaserOnDelay`` (pd126 / CO2 ``A241025_3``) in ms: the dwell **after** the laser DO
    and before the cut ticks.  Emitted as one ``2001[ms, 3000]`` wait record, never as stationary
    ticks (dwell builder ``0x4427e0`` call site ``0x443259``, A9 §2).  0 emits nothing, which is
    what this machine's CO2 layer 2 does and what the leaked frames 0x39/0x1f8 show."""
    gas_delay_ms: float = 0.0
    """Remaining gas delay in ms when this contour switches the gas DO on: ``2001[ms, 3000]``
    between the gas DO and the ZF/laser records (gas-wait builder ``0x4424e0``, A9 §2.3).
    UNVERIFIED which of ``GC.GasDelay`` / ``DirectGasDelay`` / ``ChangeGasDelay`` the vendor sums
    for a given contour and how much elapsed travel time it subtracts."""
    laser_off_before_ms: float = 0.0
    """``layer.LaserOffBeforeDelay`` (pd137): wait record before the laser DO goes off
    (``0x4432e7``, A9 §2.2).  0 on this machine."""
    laser_off_after_ms: float = 0.0
    """``layer.LaserOffAfterDelay`` (pd138): wait record after the laser DO goes off
    (``0x443326``, A9 §2.2).  0 on this machine."""


@dataclass(slots=True)
class JobStreamBuilder:
    """Assemble a job's record list and pack it into FIFO frames.

    Per contour (11 §5.4, A3 §8 frames 504/57/633):

    * prologue records ``0xe; 3[5]; tick(0,0); DO gas on; 0x11; DO laser on`` -> items
      ``3001; 3002[5]; tick; 3001; 9999[2,4,4]; 103[1000,0]; 2001[0x03000002,20000];
      9999[2,0x100,0x100]`` (the leading 3001 is the explicit type-0xe marker, no auto-marker
      because 0xe is excluded; the one-tick ``(0,0)`` record between 3002 and the DO is
      reproduced as observed, role UNVERIFIED);
    * the ``LaserOnDelay`` wait record (``2001[ms, 3000]``, nothing when 0 - A9 §2), then the
      cut ticks;
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
        """Stationary ticks ``(1000 idiv cycle)·ms`` - the tick branch of the vendor dwell
        builder ``0x4427e0``.  No CO2 call site selects it (only the fibre pierce stages can,
        A9 §2.2), so :meth:`prologue` does not use it; kept because the branch exists."""
        n = dwell_tick_count(ms, cycle_us)
        self._ticks([0] * n, [0] * n, freq, duty)
        return n

    def add_wait(self, ms: float) -> bool:
        """Append one ``2001[ms, 3000]`` card-side wait record (A9 §2).

        The vendor writes a type-1 sub-5 record with ``value = int(ms)``; NCModule drops it when
        the value is not positive (A3 §4.4 type 1 sub 5), so a zero delay emits nothing.  Returns
        whether a record was appended.
        """
        v = int(ms)
        if v <= 0:
            return False
        self.records.append(Record(RecordType.IO, IoSub.WAIT_MS, value=v))
        return True

    def prologue(
        self, laser: ContourLaser, freq: int = 0, duty: int = 0, cycle_us: int = INTERP_CYCLE_US
    ) -> None:
        """Contour start records (11 §5.4, A9 §2).

        ``freq``/``duty``/``cycle_us`` are accepted for call compatibility; since the dwells are
        wait records and not stationary ticks they no longer influence the prologue.
        """
        self.records.append(Record(RecordType.BOUNDARY))
        self.records.append(Record.mode(MODE_BEFORE_LASER_ON))
        self.records.append(Record.tick(0, 0))
        if laser.gas_port > 0:
            self.records.append(Record.do(laser.gas_port, True))
            if laser.gas_port not in self._gas_on:
                self._gas_on.add(laser.gas_port)
                self.add_wait(laser.gas_delay_ms)  # 0x4424e0: gas delay, not a tick dwell
        self.records.append(Record(RecordType.ZF_CUT_HEIGHT))
        if self.laser_records and laser.laser_port > 0:
            self.records.append(Record.do(laser.laser_port, True))
        self.add_wait(laser.pierce_dwell_ms)  # 0x443259: layer.LaserOnDelay, 0 here

    def epilogue(self, laser: ContourLaser) -> None:
        """Contour end records (11 §5.4, A9 §2.2 for the two laser-off waits)."""
        self.add_wait(laser.laser_off_before_ms)
        if laser.laser_port > 0:  # port <= 0 = unassigned: nothing is written (A3 §6)
            self.records.append(Record.do(laser.laser_port, False))
        self.add_wait(laser.laser_off_after_ms)
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
        """Prologue + cut ticks + epilogue.  ``freq``/``duty`` per interval or scalar."""
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


class JobFrameStream:
    """Streaming, vectorised counterpart of :meth:`JobStreamBuilder.frames` (STATUS §5 task 6).

    :meth:`JobStreamBuilder.frames` builds one :class:`Record`, one :class:`Item` and one word
    list per 250 µs tick and returns the whole job as a list of frames.  That is ~7 µs and a few
    hundred bytes per tick; PORT-PLAN §8.3's 100 000-contour job is 67.5 M ticks, so it costs
    minutes and cannot be held in memory at all (202 M words).

    This class emits the *same* frames - the same records, in the same order, packed by the same
    300-word rule - but hands each one back as it closes and never builds a Python object per
    tick: a run of ticks goes straight from the quantiser's pulse arrays through
    :func:`tick_words` into :meth:`nexcut.mcc.fifo.FrameStream.push_uniform`.  Only the handful
    of control records per contour (the prologue/epilogue of 11 §5.4) still take the scalar
    :class:`ItemBuilder` path, which is where the grammar - the automatic 3001 after a run of
    ticks included - stays defined exactly once.

    The record grammar itself is not duplicated: the wrapped :class:`JobStreamBuilder` appends
    the prologue/epilogue records to :attr:`JobStreamBuilder.records` and this class drains that
    list after every step.  ``tests/test_plan_vectorised_equivalence.py`` pins frame-for-frame
    identity with the scalar path.
    """

    def __init__(self, builder: JobStreamBuilder | None = None) -> None:
        self.builder = builder or JobStreamBuilder()
        self.items = ItemBuilder(self.builder.config)
        self.items.new_batch()
        self.stream = FrameStream()
        self.ticks = 0
        """Opcode-3000 items emitted so far (what :func:`total_ticks` would count)."""
        self.frames = 0
        """Frames yielded so far."""

    def _drain(self) -> Iterator[PackedFrame]:
        """Records the wrapped builder has appended -> items -> frames."""
        records = self.builder.records
        for rec in records:
            group = self.items.build(rec)
            self.ticks += sum(1 for it in group if it.opcode == OP_TICK)
            closed = self.stream.push_record(
                [it.words() for it in group], sum(1 for it in group if it.laser)
            )
            for frame in closed:
                self.frames += 1
                yield frame
        records.clear()

    def motion(
        self, points_mm: ArrayLike, freq: ArrayLike = 0, duty: ArrayLike = 0
    ) -> Iterator[PackedFrame]:
        """Quantise a sampled point list and stream one opcode-3000 tick per interval.

        The vectorised twin of :meth:`JobStreamBuilder.add_motion`; ``freq``/``duty`` are scalars
        or per-interval arrays, and a dry run (``laser_records=False``) forces duty 0 exactly as
        the scalar path does.

        Records the wrapped builder has queued but not yet drained go out **first**, exactly
        where :meth:`JobStreamBuilder.add_motion` would leave them: the scalar path appends
        every record to one list, so a prologue written before a move precedes its ticks.  The
        drain is a no-op in :meth:`contour`, which drains after every step anyway; it is what
        keeps a laser / PWM / ZF record from landing *after* the ticks it has to precede when
        ``motion`` is driven directly (11 §5.4).
        """
        yield from self._drain()
        quantizer = self.builder.quantizer
        if type(quantizer) is TickQuantizer:
            dx, dy = quantizer.quantize_arrays(points_mm)
        else:
            # A subclass may override `quantize` (tests hook the point lists there), so call it
            # and take the arrays back out of `last_cells` rather than re-converting the lists.
            quantizer.last_cells = None
            listed = quantizer.quantize(points_mm)
            cells = quantizer.last_cells
            if cells is None:  # an override that did not call through: pay the conversion
                cells = (
                    np.asarray(listed[0], dtype=np.int64),
                    np.asarray(listed[1], dtype=np.int64),
                )
            dx, dy = cells
        if dx.size == 0:
            return
        words, laser = tick_words(dx, dy, freq, duty if self.builder.laser_records else 0)
        self.ticks += int(dx.size)
        self.items.prev_type = RecordType.TICK  # the next control record gets its 3001
        for frame in self.stream.push_uniform(words, 3, laser):
            self.frames += 1
            yield frame

    def contour(
        self,
        points_mm: ArrayLike,
        freq: ArrayLike,
        duty: ArrayLike,
        laser: ContourLaser | None = None,
    ) -> Iterator[PackedFrame]:
        """Prologue + cut ticks + epilogue (:meth:`JobStreamBuilder.add_contour`)."""
        laser = laser or ContourLaser()
        self.builder.prologue(laser)
        yield from self._drain()
        yield from self.motion(points_mm, freq, duty)
        self.builder.epilogue(laser)
        yield from self._drain()

    def finish(self) -> Iterator[PackedFrame]:
        """Job end: the gas-off records and the last, partly filled frame."""
        self.builder.finish()
        yield from self._drain()
        for frame in self.stream.flush():
            self.frames += 1
            yield frame


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
