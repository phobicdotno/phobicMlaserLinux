"""Builders for the MCC100 command vectors of the M1 table (11-static-findings §2).

Every builder returns a :class:`CommandVector`: the register, the exact u32 word list that
follows ``[0x40, reg, n]`` on the wire, the M1 vector id (``V0`` ... ``V16``), the policy the
safety layer applies, the evidence, and any ``unverified`` notes that travel with the vector.

Units (A1 §2, 11 §2 header): speeds x K (0.001 mm/s), distances x K (µm), acceleration is
a plain integer mm/s² (no K), jerk / second decel word = 10 x first. K = reg 50017 = 1000 on
this machine. NCModule truncates toward zero (``_ftol2`` = ``cvttsd2si`` 0x10060c80), which
:func:`trunc_int` reproduces.

Corrections applied (11 §8): sub-cmd **1 = STOP**, **2 = HOME**, jog targets are
**relative**; ``[9999,13,0xFFFF,...]`` is **not a handshake** (extended-DO bulk off inside
stop-manu, 11 §2.1) - the connect write is ``[9999,5,0,0]``. There is **no resume
primitive** (N2 OPEN): :func:`resume` raises.

No I/O here; :mod:`nexcut.mcc.safety` decides whether a vector may be sent.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from nexcut.mcc.framing import FUNC_WRITE, REG_COMMAND, REG_FIFO_CONTROL
from nexcut.mcc.registers import AXIS_SLOTS, Status, SystemRW, do_bit

__all__ = [
    "AXIS_MASK_ALL",
    "CMD_9999",
    "CMD_GOTO",
    "CMD_HOME",
    "CMD_JOG",
    "CMD_STOP",
    "CMD_ZF_STOP",
    "FIFO_CLEAR",
    "FIFO_START",
    "FIFO_STOP",
    "K_REGISTER",
    "MISC_DA",
    "MISC_DO",
    "MISC_DO_EXT",
    "MISC_PROLOGUE",
    "MISC_PWM",
    "MISC_PWM_5V",
    "ABSOLUTE_BIT",
    "LASER_DO_PORTS_DEFAULT",
    "CommandVector",
    "MachineParams",
    "Policy",
    "StopVariant",
    "UnresolvedCommandError",
    "connect_prologue",
    "da_set",
    "do_bulk_off",
    "do_set",
    "ext_do_bulk_off",
    "estop_sequence",
    "fifo_clear",
    "fifo_start",
    "fifo_stop",
    "go_to",
    "home_axis",
    "home_system",
    "jog_continuous",
    "jog_release_stop",
    "jog_step",
    "lift_table_home",
    "lift_table_jog",
    "move_axis_absolute",
    "pause_sequence",
    "pwm_off",
    "pwm_set",
    "resume",
    "round_half_away",
    "stop_all",
    "stop_all_no_decel",
    "stop_decel_word",
    "stop_sequence",
    "trunc_int",
    "with_policy",
    "zf_move",
    "zf_stop",
]

# --- sub-command numbers (A1 §0) ------------------------------------------------------------------
CMD_STOP = 1
CMD_HOME = 2
CMD_JOG = 3
CMD_GOTO = 5
CMD_ZF_STOP = 101
CMD_9999 = 9999
MISC_DO = 2
MISC_PWM = 3
MISC_DA = 4
MISC_PROLOGUE = 5
MISC_DO_EXT = 13
MISC_PWM_5V = 0x11
FIFO_CLEAR, FIFO_START, FIFO_STOP = 1, 2, 3  # reg 0x67 (04 §3.5 verifier)
AXIS_MASK_ALL = (1 << AXIS_SLOTS) - 1  # 0x1F, all five slots (A1 row 4, registers.AXIS_SLOTS)
ABSOLUTE_BIT = 0x80000000  # bit 31 on the axis word / mask (A1 §3.3, INFERENCE medium-high)


class Policy(StrEnum):
    """Arming requirement recorded with each vector (11 §2 "M1 status", PORT-PLAN §8.2).

    The safety layer re-derives the policy from the raw words; this is documentation that
    travels with the builder output.
    """

    ALWAYS = "always"  # safety primitives: stop, ZF stop, outputs off, FIFO stop
    CONNECT = "connect"  # connect prologue, any arming state
    MOTION = "motion"  # needs MOTION_ARMED
    MOTION_HOMED = "motion_homed"  # needs MOTION_ARMED and homed axes
    LASER = "laser"  # needs LASER_ARMED
    DENY = "deny"  # not allowed in M1


K_REGISTER = SystemRW.K.addr
"""Register holding K (card units per mm), 50017 (A1 §2, registers.SystemRW.K)."""


def _check_slot(slot: int) -> None:
    """Axis slot range from the register map: 0..AXIS_SLOTS-1 (11 §0 O7, A2 §2.3)."""
    if not 0 <= slot < AXIS_SLOTS:
        raise ValueError(f"slot {slot} outside 0..{AXIS_SLOTS - 1}")


def _is_base_do(port: int) -> bool:
    """True for DO ports carried in status word DO (reg 1005, ports 1..10, A2 §2.2)."""
    loc = do_bit(port)
    return loc is not None and loc[0] is Status.DO


class UnresolvedCommandError(NotImplementedError):
    """Raised for actions whose card vector is not established (e.g. resume, N2)."""


@dataclass(frozen=True, slots=True)
class CommandVector:
    """One write: ``[0x40, register, len(words), *words]``."""

    register: int
    words: tuple[int, ...]
    """u32 words exactly as on the wire (negative values in two's complement)."""
    name: str
    vector_id: str
    """M1 table id from 11 §2 (``V0``..``V16``) or ``""``."""
    policy: Policy
    evidence: str
    unverified: tuple[str, ...] = field(default=())

    @property
    def request(self) -> list[int]:
        """Transaction vector ``[func, addr, count, data...]`` (04 §3.2)."""
        return [FUNC_WRITE, self.register, len(self.words), *self.words]

    @property
    def signed_words(self) -> tuple[int, ...]:
        """Words reinterpreted as int32 (for display: ``-4000000`` instead of ``0xffc2f700``)."""
        return tuple(w - (1 << 32) if w & 0x80000000 else w for w in self.words)

    @property
    def is_verified(self) -> bool:
        """True if no UNVERIFIED note is attached."""
        return not self.unverified


def _vec(
    register: int,
    words: Iterable[int],
    name: str,
    vector_id: str,
    policy: Policy,
    evidence: str,
    unverified: Sequence[str] = (),
) -> CommandVector:
    return CommandVector(
        register,
        tuple(int(w) & 0xFFFFFFFF for w in words),
        name,
        vector_id,
        policy,
        evidence,
        tuple(unverified),
    )


def trunc_int(x: float) -> int:
    """Truncate toward zero like MSVC ``_ftol2`` / ``cvttsd2si`` (A1 §0 row 1 verifier, A5 V8)."""
    if not math.isfinite(x):
        raise ValueError(f"cannot truncate {x!r}")
    return math.trunc(x)


def round_half_away(x: float) -> int:
    """MainApp rounding helper ``0x4511d0``: ``trunc(x + 0.5 * sign(x))`` (A1 §3.2, 11 §2 header).

    EVIDENCE: ``0x451210(x, 0.0)`` returns the sign (+1 / 0 / -1), ``fild; fmul
    [0x7c4708] (= 0.5); fadd [x]`` and ``call 0x606c10`` (``cvttsd2si``). Half-way cases go
    away from zero (2.5 -> 3, -2.5 -> -3), unlike Python's ``round`` (banker's rounding).
    The sum is formed in double precision, so ``0.49999999999999994`` rounds to 1 exactly as
    in the vendor (UNVERIFIED: assumes the MSVC default 53-bit x87 precision control).
    """
    if not math.isfinite(x):
        raise ValueError(f"cannot round {x!r}")
    sign = 1 if x > 0.0 else (-1 if x < 0.0 else 0)
    return math.trunc(x + 0.5 * sign)


@dataclass(frozen=True, slots=True)
class MachineParams:
    """Parameters the builders read; defaults = this machine (11 §2 header, A1 §6, BkHardPara.xml).

    ``k`` must be refreshed from reg 50017 (:attr:`SystemRW.K`) after connect (card is
    authoritative, 11 §3.2); see :data:`K_REGISTER`.
    """

    k: int = 1000
    jog_fast_speed: float = 200.0  # MC.JogFastSpeed mm/s
    jog_slow_speed: float = 50.0  # MC.JogSlowSpeed
    is_fast_mode: bool = True  # MC.IsFastMode
    is_step_move: bool = False  # MC.IsStepMove
    step_length: float = 1.0  # MC.StepLength mm
    x_fast_move_acc: float = 5999.99  # MC.XFastMoveAcc mm/s²
    x_fast_move_speed: float = 500.0  # MC.XFastMoveSpeed mm/s
    fcp_max_speed: float = 3000.0  # FCP.MaxSpeed mm/s
    fcp_max_acc: int = 20000  # FCP.MaxAcc mm/s²
    jog_stop_dcc_factor: int = 1  # MP.JogStopDccFactor
    enable_soft_limit: bool = False  # MS.EnableSoftLimit
    empty_move_speed_factor: float = 1.1000000000000001  # MP.EmptyMoveSpeedFactor (XML text)
    empty_move_acc_factor: float = 1.5  # MP.EmptyMoveAccFactor
    hpa3_acc: float = 4000.0  # HPA3.Acc (lift table)
    platform_exchange_speed: float = 100.0  # MP.PlatformExchangeSpeed
    mac2_soft_limit_max_len: float = 1000.0  # MAC_2.SoftLimitMaxLen (lift-jog distance, A1 §3.2)
    zf_type: int = 1  # ZF.ZFType (card word 50006 authoritative)
    pt_laser_freq: int = 1234  # LC.PtLaserFreq (BkManuPara.xml)
    co2_do_laser: int = 9  # LGP.CO2DOLaser (XML port, 1-based)
    co2_do_laser_gate: int = 0  # LGP.CO2DOLaserGate (0 = unassigned -> nothing sent)
    co2_laser_da_port: int = 1  # LGP.CO2LaserDAPort
    gas_da_ports: tuple[int, ...] = (2,)  # MGP.RatioAir/RatioO2/RatioH2 != 0 -> here RatioO2=2
    do_ports_off_on_stop: tuple[int, ...] = (1, 2, 3, 5, 6, 7, 9)
    """Configured DO ports cleared by the stop-manu DO bulk write. UNVERIFIED: the vendor
    list has 27 fixed offsets + a vector (A1 §4.2); this default is this machine's non-zero
    DO assignments from BkHardPara.xml (AlarmSignal 1, HighN2 2, HighAir 3, DOLaserGate 5,
    DORedLight 6, LowO2 7, CO2DOLaser 9)."""


# ================================================================================================
# V0 connect prologue
# ================================================================================================


def connect_prologue() -> CommandVector:
    """V0 ``0x65 <- [9999, 5, 0, 0]`` once connected (NC 0x10056c6b-0x10056cd6, 11 §2 / C7)."""
    return _vec(
        REG_COMMAND,
        [CMD_9999, MISC_PROLOGUE, 0, 0],
        "connect_prologue",
        "V0",
        Policy.CONNECT,
        "NC 0x10056c6b (lead re-read), 11 §2 V0",
        ["meaning of [9999,5,0,0] unknown; capture A confirms it is needed"],
    )


def ext_do_bulk_off(keep_hi: int) -> CommandVector:
    """``[9999, 13, 0xFFFF, keep>>16]``: the old "handshake", really extended-DO bulk off (11 §2.1).

    Only sent by the vendor when ``g+0x4d58`` (extension IO) is set - not on this machine.
    DENY in M1 (11 §3.3).
    """
    return _vec(
        REG_COMMAND,
        [CMD_9999, MISC_DO_EXT, 0xFFFF, keep_hi & 0xFFFF],
        "ext_do_bulk_off",
        "",
        Policy.DENY,
        "NC 0x100575d3 (11 §2.1, C7)",
        ["no 9999/13 vector in any log"],
    )


# ================================================================================================
# Jog (sub-command 3) V1/V2, lift table V9
# ================================================================================================


def _jog_speed_acc(p: MachineParams) -> tuple[int, int]:
    v = trunc_int((p.jog_fast_speed if p.is_fast_mode else p.jog_slow_speed) * p.k)
    v = min(v, trunc_int(p.fcp_max_speed * p.k))
    a = min(trunc_int(p.x_fast_move_acc), p.fcp_max_acc)
    return v, a


def _check_axis_index(axis_index: int) -> None:
    if axis_index not in (0, 1):
        raise ValueError("M1 jog is limited to axis-list index 0 (X) or 1 (Y) (11 §3.1)")


def jog_continuous(
    axis_index: int,
    positive: bool,
    params: MachineParams | None = None,
    *,
    soft_limit_remaining_mm: float | None = None,
) -> CommandVector:
    """V1 held jog: ``[3, i, v, a, 10a, ±d]`` (CNCModule slot 21 ``0x1002f750``, A1 §3.1).

    ``d = trunc(20 * JogFastSpeed * K)`` (``ds:0x10089c70 = 20.0``), relative. When
    ``soft_limit_remaining_mm`` is given the distance is clipped to it (the vendor does
    that only with ``MS.EnableSoftLimit``; the port always passes it, PORT-PLAN §8.2).
    """
    p = params or MachineParams()
    _check_axis_index(axis_index)
    v, a = _jog_speed_acc(p)
    dist = 20.0 * p.jog_fast_speed
    if soft_limit_remaining_mm is not None:
        dist = max(0.0, min(dist, soft_limit_remaining_mm))
    d = trunc_int(dist * p.k)
    return _vec(
        REG_COMMAND,
        [CMD_JOG, axis_index, v, a, 10 * a, d if positive else -d],
        "jog_continuous",
        "V1",
        Policy.MOTION,
        "A1 row 1 / §3.1; logs `03 01 30d40 176f ea56 3d0900`",
    )


def jog_step(
    axis_index: int,
    distance_mm: float,
    params: MachineParams | None = None,
) -> CommandVector:
    """V2 step jog / M1 test move: ``[3, i, v, a, 10a, trunc(dist*K)]``, relative (A1 rows 1-2).

    ``distance_mm`` is signed (the vendor negates with ``fchs`` before truncating, so
    truncation is symmetric). Use ``params.step_length`` for the UI step.
    """
    p = params or MachineParams()
    _check_axis_index(axis_index)
    v, a = _jog_speed_acc(p)
    return _vec(
        REG_COMMAND,
        [CMD_JOG, axis_index, v, a, 10 * a, trunc_int(distance_mm * p.k)],
        "jog_step",
        "V2",
        Policy.MOTION,
        "A1 rows 1-2, 11 §2 V2",
    )


def move_axis_absolute(
    axis_index: int, target_mm: float, params: MachineParams | None = None
) -> CommandVector:
    """``[3, i | 0x80000000, v, a, 10a, target]`` single-axis absolute move (A1 §3.3).

    Capture step 4 of 11 §7 uses exactly this form to prove bit 31 = absolute.
    """
    p = params or MachineParams()
    _check_axis_index(axis_index)
    v, a = _jog_speed_acc(p)
    return _vec(
        REG_COMMAND,
        [CMD_JOG, axis_index | ABSOLUTE_BIT, v, a, 10 * a, trunc_int(target_mm * p.k)],
        "move_axis_absolute",
        "",
        Policy.MOTION_HOMED,
        "A1 §3.3 0x1003221f",
        ["bit 31 = absolute target (INFERENCE medium-high; capture step 4)"],
    )


def lift_table_jog(up: bool, params: MachineParams | None = None) -> CommandVector:
    """V9 lift table: ``[3, 4, round(min(v,100)*K), trunc(HPA3.Acc), 10a, ±d]`` (A1 §3.2 branch C).

    DENY in M1. d = (IsStepMove ? StepLength : MAC_2.SoftLimitMaxLen) * K, no soft-limit clip.
    """
    p = params or MachineParams()
    speed = p.jog_fast_speed if p.is_fast_mode else p.jog_slow_speed
    v = round_half_away(min(speed, p.platform_exchange_speed) * p.k)
    a = trunc_int(p.hpa3_acc)
    dist = p.step_length if p.is_step_move else p.mac2_soft_limit_max_len
    d = round_half_away(dist * p.k)
    return _vec(
        REG_COMMAND,
        [CMD_JOG, 4, v, a, 10 * a, d if up else -d],
        "lift_table_jog",
        "V9",
        Policy.DENY,
        "A1 row 10 / §3.2; log `03 04 c350 fa0 9c40 f4240`",
        ["x87 precision control of the MainApp rounding (53-bit assumed)"],
    )


# ================================================================================================
# Stop family (sub-command 1) V3/V4/V5, ZF stop V14
# ================================================================================================


class StopVariant(StrEnum):
    """Which decel clamp a stop builder uses (A1 §4.1 three MainApp variants + VM builder)."""

    MAINAPP_NO_SOFT_LIMIT = "mainapp"  # [2000, trunc(0.4*FCP.MaxAcc)] 0x57af9b-0x57b17a
    MAINAPP_SOFT_LIMIT = "mainapp_softlimit"  # [5000, trunc(0.4*FCP.MaxAcc)] 0x57aaba-0x57af96
    PENDANT_OR_VM = "vm"  # [2000, 100000] PHBX 0x59807b, VM 0x10053df0


def stop_decel_word(
    v_last_word: int,
    params: MachineParams | None = None,
    variant: StopVariant = StopVariant.PENDANT_OR_VM,
) -> int:
    """``vd = clamp(F*100000 // (v_last // K), lo, hi)`` (A1 §4.1, VM 0x10053df0).

    ``v_last_word`` is the last commanded jog speed word (``g+0x499c``); ``<= 0`` means
    100000 (vendor default). Integer divisions as in the vendor. Deviation: when
    ``v_last // K == 0`` the vendor divides by zero; the port uses the upper clamp.
    """
    p = params or MachineParams()
    lo = 5000 if variant is StopVariant.MAINAPP_SOFT_LIMIT else 2000
    hi = 100000 if variant is StopVariant.PENDANT_OR_VM else trunc_int(0.4 * p.fcp_max_acc)
    if v_last_word <= 0:
        v_last_word = 100000
    q = v_last_word // p.k
    vd = hi if q == 0 else (p.jog_stop_dcc_factor * 100000) // q
    return max(lo, min(vd, hi))


def jog_release_stop(
    slots: Iterable[int],
    v_last_word: int,
    params: MachineParams | None = None,
    variant: StopVariant = StopVariant.MAINAPP_NO_SOFT_LIMIT,
) -> CommandVector:
    """V3 jog key released: ``[1, OR(1<<slot), 2, vd, 10*vd]`` (A1 row 3, §4.1).

    Not to be sent for step moves (vendor skips it when ``MC.IsStepMove``).
    """
    mask = 0
    for s in slots:
        _check_slot(s)
        mask |= 1 << s
    if not mask:
        raise ValueError("no axis to stop")
    vd = stop_decel_word(v_last_word, params, variant)
    return _vec(
        REG_COMMAND,
        [CMD_STOP, mask, 2, vd, 10 * vd],
        "jog_release_stop",
        "V3",
        Policy.ALWAYS,
        "A1 §4.1; logs `01 01 02 7d0 4e20`, `01 02 02 7d0 4e20`",
        [
            "vd is a deceleration in mm/s² (INFERENCE medium-high, capture step 5)",
            "word 2 = 2 constant, meaning unknown",
        ],
    )


def stop_all(v_last_word: int = 0, params: MachineParams | None = None) -> CommandVector:
    """V4 idle stop-all ``[1, 0x1F, 2, vd, 10*vd]`` (VM builder 0x10053df0, clamp [2000,100000])."""
    vd = stop_decel_word(v_last_word, params, StopVariant.PENDANT_OR_VM)
    return _vec(
        REG_COMMAND,
        [CMD_STOP, AXIS_MASK_ALL, 2, vd, 10 * vd],
        "stop_all",
        "V4",
        Policy.ALWAYS,
        "A1 row 4 / §4.2; log `01 1f 02 7d0 4e20`",
        ["vd physical meaning (capture step 5)"],
    )


def stop_all_no_decel() -> CommandVector:
    """V5 ``[1, 0x1F]`` (edge-seek routines, CNCModule slot 25 0x1002fab0, A1 row 16)."""
    return _vec(
        REG_COMMAND,
        [CMD_STOP, AXIS_MASK_ALL],
        "stop_all_no_decel",
        "V5",
        Policy.ALWAYS,
        "A1 row 16",
        ["card default decel (INFERENCE)"],
    )


def zf_stop() -> CommandVector:
    """V14 ZF stop ``0x65 <- [101]`` (NC slot 58 0x1002c510, A1 row 17, A5 §3)."""
    return _vec(
        REG_COMMAND,
        [CMD_ZF_STOP],
        "zf_stop",
        "V14",
        Policy.ALWAYS,
        "A1 row 17, A5 §3",
        ["'ZF stop' meaning INFERENCE medium-high"],
    )


# ================================================================================================
# FIFO control V16
# ================================================================================================


def fifo_clear() -> CommandVector:
    """V16 ``0x67 <- [1]`` (clearFifo 0x100510f0, 04 §3.5)."""
    return _vec(REG_FIFO_CONTROL, [FIFO_CLEAR], "fifo_clear", "V16", Policy.MOTION, "04 §3.5")


def fifo_start() -> CommandVector:
    """V16 ``0x67 <- [2]`` (startFifo 0x10050af0). Dry run only in M1 (laser records stripped)."""
    return _vec(REG_FIFO_CONTROL, [FIFO_START], "fifo_start", "V16", Policy.MOTION, "04 §3.5")


def fifo_stop() -> CommandVector:
    """V16 ``0x67 <- [3]`` (stopFifo 0x10050dc0); always allowed (11 §3.1)."""
    return _vec(REG_FIFO_CONTROL, [FIFO_STOP], "fifo_stop", "V16", Policy.ALWAYS, "04 §3.5")


# ================================================================================================
# Home (sub-command 2) V6/V7/V10, go-to (sub-command 5) V8
# ================================================================================================


def home_axis(slot: int) -> CommandVector:
    """V6 ``[2, 1<<slot, 0]`` (CNCModule slot 23 0x10032350, ``shl edx,cl``; A1 §5).

    M1 allows slots 0 (X) and 1 (Y) one at a time; other slots are built but DENY.
    """
    _check_slot(slot)
    policy = Policy.MOTION if slot in (0, 1) else Policy.DENY
    vid = "V10" if slot == 4 else "V6"
    return _vec(
        REG_COMMAND,
        [CMD_HOME, 1 << slot, 0],
        "home_axis",
        vid,
        policy,
        "A1 row 8 / §5",
        ["homing speeds/back-off held on the card (AxisRW +5/+6, INFERENCE high)"],
    )


def lift_table_home() -> CommandVector:
    """V10 ``[2, 0x10, 0]`` (lift dialog 0x2713 ``push 4`` -> slot 23, A1 row 12). DENY in M1."""
    return home_axis(4)


_SYSTEM_HOME_MASK = {1: 0x1, 2: 0x2, 3: 0x3, 4: 0x10003, 8: 0x10000}


def home_system(axis_config_type: int, z_slot: int = 3) -> CommandVector:
    """V7 system home ``[2, mask(g+0xba30), 0]`` (jump table 0x10032604, A1 row 9). DENY in M1.

    Types 6/7/other send ``[2, 0, 0]``. Type 5 = X|Y|(1<<z_slot).
    """
    mask = (
        (0x3 | (1 << z_slot))
        if axis_config_type == 5
        else _SYSTEM_HOME_MASK.get(axis_config_type, 0)
    )
    return _vec(
        REG_COMMAND,
        [CMD_HOME, mask, 0],
        "home_system",
        "V7",
        Policy.DENY,
        "A1 row 9",
        ["mask bit 0x10000 meaning (INFERENCE low)"],
    )


def go_to(x_mm: float, y_mm: float, params: MachineParams | None = None) -> CommandVector:
    """V8 absolute go-to ``[5, 0x80000003, v, a, 10a, x*K, y*K, 0, 0]`` (slot 20 0x10031f10).

    v = trunc(XFastMoveSpeed * EmptyMoveSpeedFactor * K), a = min(trunc(XFastMoveAcc *
    EmptyMoveAccFactor), FCP.MaxAcc); reproduces the logged
    ``05 80000003 86470 2327 15f86 69062 2a811 0 0``.
    """
    p = params or MachineParams()
    v = min(
        trunc_int(p.x_fast_move_speed * p.empty_move_speed_factor * p.k),
        trunc_int(p.fcp_max_speed * p.k),
    )
    a = min(trunc_int(p.x_fast_move_acc * p.empty_move_acc_factor), p.fcp_max_acc)
    return _vec(
        REG_COMMAND,
        [
            CMD_GOTO,
            0x3 | ABSOLUTE_BIT,
            v,
            a,
            10 * a,
            trunc_int(x_mm * p.k),
            trunc_int(y_mm * p.k),
            0,
            0,
        ],
        "go_to",
        "V8",
        Policy.MOTION_HOMED,
        "A1 row 13; log 2025-07-08 13:24:33.743",
        [
            "v/a derivation from caller 0x58c130 not re-derived (values match the log)",
            "coordinate rounding in MainApp 0x58bf10 (trunc assumed)",
            "bit 31 = absolute (INFERENCE high)",
        ],
    )


# ================================================================================================
# DO / DA / PWM (9999 family) V11/V12/V13, ZF move V15
# ================================================================================================


LASER_DO_PORTS_DEFAULT = frozenset({5, 9})
"""DO5 fibre laser gate (LGP.DOLaserGate), DO9 CO2 laser enable (LGP.CO2DOLaser) (A5 §5, A3 §6)."""


def do_set(
    port: int, on: bool, *, laser_ports: Iterable[int] = LASER_DO_PORTS_DEFAULT
) -> CommandVector:
    """V11 ``[9999, 2, 1<<(port-1), on<<(port-1)]`` for ports 1..10 (VM 0x1003c860, A3 §6).

    Ports 11..26 use sub 13 and are DENY (``g+0x4d58`` = 0 here). Laser ports ON need
    LASER_ARMED; any port OFF is a safety action.
    """
    loc = do_bit(port)  # A2 §2.2: 1..10 -> reg 1005, 11..26 -> reg 1022
    if loc is None:
        raise ValueError(f"DO port {port} outside 1..26 (port 0 = unassigned, nothing sent)")
    word, bit = loc
    if word is Status.EXT_DO:
        return _vec(
            REG_COMMAND,
            [CMD_9999, MISC_DO_EXT, 1 << bit, int(on) << bit],
            "do_set_ext",
            "V11",
            Policy.DENY,
            "A3 §6 0x1003c996",
        )
    if not on:
        policy = Policy.ALWAYS
    elif port in set(laser_ports):
        policy = Policy.LASER
    else:
        policy = Policy.MOTION
    return _vec(
        REG_COMMAND,
        [CMD_9999, MISC_DO, 1 << bit, int(on) << bit],
        "do_set",
        "V11",
        policy,
        "A3 §6 0x1003c8d2; logs `270f 02 01 00/01`",
    )


def do_bulk_off(current_do_word: int, ports_off: Iterable[int]) -> CommandVector:
    """Stop-manu DO bulk write ``[9999, 2, 0xFFFF, DO & ~mask(ports) & 0xFFFF]`` (11 §2.1).

    ``current_do_word`` = reg 1005 (:attr:`Status.DO`). The outputs in ``ports_off`` go off, others keep state.
    """
    mask = 0
    for p in ports_off:
        if 1 <= p <= 16:
            mask |= 1 << (p - 1)
    keep = current_do_word & ~mask & 0xFFFF
    return _vec(
        REG_COMMAND,
        [CMD_9999, MISC_DO, 0xFFFF, keep],
        "do_bulk_off",
        "",
        Policy.ALWAYS,
        "NC 0x100574d6-0x100575d3 (11 §2.1 lead re-read)",
        ["vendor clears 27 fixed DO params + a vector; port list is per machine"],
    )


def da_set(channel: int, volts: float) -> CommandVector:
    """V12 ``[9999, 4, channel-1, mV]`` (VM 0x1003c760, A3 §6).

    mV = trunc(V*1000); 1..49 -> 50; > 10000 -> 10000. Negative volts are refused here
    (the vendor does not clamp them). Value 0 = safety action; non-zero DENY in M1.
    """
    if channel not in (1, 2):
        raise ValueError("DA channel must be 1 or 2 (vendor refuses others)")
    mv = trunc_int(volts * 1000)
    if mv < 0:
        raise ValueError("negative DA voltage refused")
    if 1 <= mv <= 49:
        mv = 50
    mv = min(mv, 10000)
    policy = Policy.ALWAYS if mv == 0 else Policy.DENY
    return _vec(
        REG_COMMAND,
        [CMD_9999, MISC_DA, channel - 1, mv],
        "da_set",
        "V12",
        policy,
        "A3 §6 0x1003c760",
    )


def pwm_off(freq: int, sub: int = MISC_PWM_5V) -> CommandVector:
    """PWM off as in stop-manu: ``[9999, 3|0x11, PtLaserFreq, 0, 0]`` (0x100570a2-0x10057198)."""
    if sub not in (MISC_PWM, MISC_PWM_5V):
        raise ValueError("PWM sub-command must be 3 or 0x11")
    return _vec(
        REG_COMMAND,
        [CMD_9999, sub, freq, 0, 0],
        "pwm_off",
        "V13",
        Policy.ALWAYS,
        "A1 §4.2 0x100570a2-0x10057198",
        ["role of the fifth word (0) unknown"],
    )


def pwm_set(freq: int, duty: int, sub: int = MISC_PWM_5V) -> CommandVector:
    """V13 PWM without motion ``[9999, 0x11|3, freq, duty]`` (11 §2 V13). LASER_ARMED.

    EVIDENCE for the 4-word 0x65 form: CNCModule method ``0x100303a0`` writes
    ``0x65 <- [9999, 3, g+0x483c (LC.PtLaserFreq), g+0x4798]`` (fibre, or CO2 with
    ``CO2LaserControlType`` 1, ``0x1003040b..0x10030465``) and ``[9999, 0x11, g+0x483c,
    g+0x4798]`` (CO2 with ``CO2LaserControlType`` 2, ``0x100304cf..0x10030529``); nothing
    is sent when ``PtLaserFreq <= 0``. Stop-manu's 5-word ``[9999, 3|0x11, freq, 0, 0]``
    (:func:`pwm_off`) is a different write.
    """
    if sub not in (MISC_PWM, MISC_PWM_5V):
        raise ValueError("PWM sub-command must be 3 or 0x11")
    policy = Policy.ALWAYS if duty == 0 else Policy.LASER
    return _vec(
        REG_COMMAND,
        [CMD_9999, sub, freq, duty],
        "pwm_set",
        "V13",
        policy,
        "11 §2 V13",
        ["word 3 = g+0x4798 is the duty field (INFERENCE: no XML descriptor traced)"],
    )


def zf_move(speed_mm_s: float, height_mm: float) -> CommandVector:
    """V15 ZF move ``[103, trunc(v*10), trunc(h*1000)]`` (NC slot 66 0x100315a0, A5 §3). DENY."""
    return _vec(
        REG_COMMAND,
        [103, trunc_int(speed_mm_s * 10), trunc_int(height_mm * 1000)],
        "zf_move",
        "V15",
        Policy.DENY,
        "A5 §3 (truncation V8: 2.01 mm -> 2009)",
    )


# ================================================================================================
# Sequences: stop / pause / e-stop / resume
# ================================================================================================


def stop_sequence(
    params: MachineParams | None = None,
    *,
    fifo_running: bool = False,
    connected: bool = True,
    current_do_word: int = 0,
    v_last_word: int = 0,
    outputs_off: bool = True,
) -> list[CommandVector]:
    """V4 stop-manu as the port sends it (VM handler 0x10056e8d, A1 §4.2, 11 §2 V4).

    Order of the vendor's running/faulted branch:
      1. ``0x67 <- [3]`` if the FIFO runs, else ``[1, 0x1F, 2, vd, 10vd]``;
      2. CO2 laser DO off (``LGP.CO2DOLaser``), CO2 DA 0;
      3. ``[9999, 3, PtLaserFreq, 0, 0]`` and ``[9999, 0x11, PtLaserFreq, 0, 0]``;
      4. gas DAs 0; 5. DO bulk off; 6. ``[101]`` if connected and ZFType != 0.

    Deviation (11 §2 V4): the vendor's idle branch skips steps 2-5; the port always sends
    them unless ``outputs_off=False``. The conditional ZF lift ``[103, ...]`` and the fibre
    ``[118, 5, 0]`` are not emitted (DENY in M1, CO2 machine).
    """
    p = params or MachineParams()
    seq = [fifo_stop() if fifo_running else stop_all(v_last_word, p)]
    if outputs_off:
        if _is_base_do(p.co2_do_laser):
            seq.append(do_set(p.co2_do_laser, False))
        if p.co2_laser_da_port in (1, 2):
            seq.append(da_set(p.co2_laser_da_port, 0.0))
        seq.append(pwm_off(p.pt_laser_freq, MISC_PWM))
        seq.append(pwm_off(p.pt_laser_freq, MISC_PWM_5V))
        for ch in p.gas_da_ports:
            if ch in (1, 2):
                seq.append(da_set(ch, 0.0))
        seq.append(do_bulk_off(current_do_word, p.do_ports_off_on_stop))
    if connected and p.zf_type != 0:
        seq.append(zf_stop())
    return seq


def pause_sequence(params: MachineParams | None = None, **kwargs: object) -> list[CommandVector]:
    """Pause == Stop: VM slot 19 0x10045f70 equals slot 18 byte-for-byte (A1 §4.3, 11 N1)."""
    return stop_sequence(params, **kwargs)  # type: ignore[arg-type]


def estop_sequence(params: MachineParams | None = None, **kwargs: object) -> list[CommandVector]:
    """UI E-stop = stop-manu + laser gate off, then latch (0x58fbe0 -> 0x5884b0, A1 §4.4).

    The gate DO is ``LGP.CO2DOLaserGate`` (CNCModule slot 98 0x1002dda0); port 0 (this
    machine) sends nothing, exactly as the vendor. The latch lives in :mod:`safety`.
    """
    p = params or MachineParams()
    seq = stop_sequence(p, **kwargs)  # type: ignore[arg-type]
    if _is_base_do(p.co2_do_laser_gate):
        seq.insert(1, do_set(p.co2_do_laser_gate, False))
    return seq


def resume() -> CommandVector:
    """Resume/Continue after pause: **not established** (A1 row 6, 11 N2 OPEN).

    One capture settles it: 11 §7 step 9 (dry job in the Windows tool: Pause, Continue, Stop).
    """
    raise UnresolvedCommandError(
        "no resume primitive is known (11-static-findings N2); capture step 9 needed"
    )


def with_policy(cmd: CommandVector, policy: Policy) -> CommandVector:
    """Copy of ``cmd`` with another policy label (tests / tooling)."""
    return replace(cmd, policy=policy)
