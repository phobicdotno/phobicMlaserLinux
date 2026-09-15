"""MCC100 register map: named blocks, word offsets, bit tables, polling schedule.

Sources (every constant cites one of them):

* ``docs/analysis/04-controller-protocol.md`` §3.5 (block list, PC-side reader VAs).
* ``docs/analysis/11-static-findings.md`` §3 (read/write allow/deny), §4 (bit tables),
  §5.1 (FIFO frame id / margin).
* ``docs/analysis/11-static/A2-status-words.md`` §1-§7 (reader VAs, word maps, alarm codes).
* ``docs/analysis/11-static/A1-motion-commands.md`` §2 (K = reg 50017).

Conventions: *word i of block B* is register ``B + i``; words are u32. ``getReg(group, i)``
in the vendor code indexes the same words (A2 §1 table "getReg group").

Only facts marked EVIDENCE in the analysis are stated as plain constants. Everything else
carries ``UNVERIFIED`` in a comment and appears in :data:`UNVERIFIED_NOTES`.

This module performs no I/O and reads nothing at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag, StrEnum

from nexcut.mcc.framing import REG_COMMAND, REG_FIFO_CONTROL, REG_FIFO_DATA

__all__ = [
    "ALARM1_AXIS_DETAIL_SLOTS",
    "ALARM1_RESERVED_MASK",
    "AXIS_RO_WORDS",
    "AXIS_RW_WORDS_ON_WIRE",
    "AXIS_RW_WORDS_KEPT",
    "AXIS_SLOTS",
    "BLOCKS",
    "FIFO_MARGIN_EMPTY",
    "FIFO_SEND_HEADROOM_BYTES",
    "HARD_PARAM_BASE",
    "HARD_PARAM_BLOCK_WORDS",
    "HARD_PARAM_STRIDE",
    "LICENCE_FIRST",
    "LICENCE_LAST",
    "PARAM_STATUS_RESTART_REQUIRED",
    "POLL_PROFILE_FAST",
    "POLL_PROFILE_NORMAL",
    "PORT_POLL_SCHEDULE",
    "REG_COMMAND",
    "REG_FIFO_CONTROL",
    "REG_FIFO_DATA",
    "SLOW_TRANSACTION_LOG_MS",
    "STATUS_POLL_FAILURE_WATCHDOG_S",
    "UNVERIFIED_NOTES",
    "VENDOR_MCCORE_MS",
    "VENDOR_MC_UPDATE_FACTOR",
    "VENDOR_READ_DROPPED",
    "VENDOR_WRITE_DROPPED",
    "VENDOR_ZFCORE_MS",
    "VENDOR_ZF_UPDATE_FACTOR",
    "Access",
    "Alarm1",
    "Alarm2",
    "AlarmCode",
    "AxisCommandType",
    "AxisRO",
    "AxisRW",
    "AxisStatus",
    "Block",
    "MachineState",
    "PollEntry",
    "Status",
    "SystemRW",
    "ZFAlarm",
    "ZFState",
    "alarm_codes",
    "axis_busy",
    "axis_command_type",
    "axis_ro_addr",
    "axis_rw_addr",
    "derive_machine_state",
    "derive_zf_state",
    "di_active",
    "di_bit",
    "do_bit",
    "do_is_on",
    "fifo_program_running",
    "homed_mask",
    "next_frame_id",
    "vendor_would_send",
]

# ================================================================================================
# Blocks (04 §3.5, A2 §1, 11 §3.2)
# ================================================================================================


class Access(StrEnum):
    """How the port may touch a block (11 §3)."""

    READ = "read"  # read allowed, writes denied
    DIAGNOSTIC = "diagnostic"  # read allowed only as a one-off diagnostic (block 5000)
    COMMAND = "command"  # write-only command/FIFO register (0x65/0x66/0x67)
    DENY = "deny"  # neither read nor write


@dataclass(frozen=True, slots=True)
class Block:
    """A register block as the vendor addresses it."""

    name: str
    start: int
    count: int
    access: Access
    reader_va: str
    """NCModule function that issues ``[0x30, start, count]`` (A2 §1), or ``""``."""
    getreg_group: int | None
    """``getReg(group, i)`` group that mirrors the block (A2 §1), if any."""
    doc: str

    @property
    def end(self) -> int:
        """One past the last register."""
        return self.start + self.count

    def contains(self, addr: int, count: int = 1) -> bool:
        """True if ``[addr, addr+count)`` lies inside the block."""
        return self.start <= addr and addr + count <= self.end

    def addr(self, index: int) -> int:
        """Register address of word ``index`` (bounds-checked)."""
        if not 0 <= index < self.count:
            raise IndexError(f"{self.name}: word {index} outside 0..{self.count - 1}")
        return self.start + index


AXIS_SLOTS = 5
"""Axis slots 0 X, 1 Y, 2 Y2, 3 height axis, 4 W/lifting table (11 §0 O7, A2 §2.3 table 0x991138)."""
AXIS_RO_WORDS = 10
"""Per-slot stride of block 2000 (A2 §1: reply stride 0x28 bytes)."""
AXIS_RW_WORDS_ON_WIRE = 20
"""Per-slot stride of block 50200 on the wire (reply stride 0x50 bytes, A2 §1)."""
AXIS_RW_WORDS_KEPT = 14
"""Words per slot the vendor copies (``mov edx,0xe`` @0x10051de7, A2 §1)."""

HARD_PARAM_BASE = 59600
HARD_PARAM_STRIDE = 0xD0
"""Hardware-parameter blocks ``59600 + 0xD0*i`` (04 §3.5 ``readParamFromCard`` 0x10050830)."""
HARD_PARAM_BLOCK_WORDS = 52
LICENCE_FIRST = 59500
LICENCE_LAST = 59599
"""Licence data / card RTC area, reads and writes denied (11 §3.3, A4 §2-3)."""

BLOCKS: dict[str, Block] = {
    b.name: b
    for b in (
        Block(
            "status_version",
            1000,
            2,
            Access.READ,
            "0x1004e3d0 checkMCStatus",
            0,
            "program id + version gate (A2 §1)",
        ),
        Block(
            "status",
            1000,
            36,
            Access.READ,
            "0x100516b0 updateMCStatusRO",
            0,
            "read-only status (A2 §2, 11 §4.1)",
        ),
        Block(
            "version_time",
            1050,
            3,
            Access.READ,
            "",
            None,
            "version/time triple, seen in logs only (04 §3.5)",
        ),
        Block(
            "axis_ro",
            2000,
            AXIS_SLOTS * AXIS_RO_WORDS,
            Access.READ,
            "0x100518c0 updateMCStatusAxisRO",
            3,
            "axis RO 5x10 (A2 §3)",
        ),
        Block(
            "rw_5000",
            5000,
            9,
            Access.DIAGNOSTIC,
            "0x10051b00 updateMCStatusRW (no caller)",
            1,
            "e-stop port / safety decel; vendor never reads, wire window 5000..5008 (A2 §4)",
        ),
        Block(
            "zf_status",
            10000,
            18,
            Access.READ,
            "0x1004ea50 updateZFStatus",
            7,
            "on-board follower status, only if ZFType != 0 (A5 §2.1)",
        ),
        Block(
            "zf_prop",
            11000,
            39,
            Access.READ,
            "0x1004ed90 updateZFProp",
            8,
            "follower properties; writes denied (A5 §2.2, 11 §3.3)",
        ),
        Block(
            "system_rw",
            50000,
            26,
            Access.READ,
            "0x10051f20 updateMCStatusSystemRW",
            5,
            "system RW; card is authoritative for words 5/6/17 (11 §3.2)",
        ),
        Block(
            "axis_rw",
            50200,
            AXIS_SLOTS * AXIS_RW_WORDS_ON_WIRE,
            Access.READ,
            "0x10051d00 updateMCStatusAxisRW",
            4,
            "axis RW 5x20, vendor keeps 14 (A2 §1)",
        ),
        Block(
            "fast",
            60001,
            120,
            Access.READ,
            "0x10052130 updateMCStatusFast",
            3,
            "2000 block + compacted 50200 block in one read (A2 §1)",
        ),
        Block(
            "licence",
            LICENCE_FIRST,
            LICENCE_LAST - LICENCE_FIRST + 1,
            Access.DENY,
            "",
            None,
            "licence 59500-59511 + RTC 59502/59503; never touch (11 §3.3)",
        ),
        Block("command", REG_COMMAND, 1, Access.COMMAND, "", None, "0x65 command vectors (11 §2)"),
        Block("fifo_data", REG_FIFO_DATA, 1, Access.COMMAND, "", None, "0x66 FIFO frames (11 §5)"),
        Block(
            "fifo_control",
            REG_FIFO_CONTROL,
            1,
            Access.COMMAND,
            "",
            None,
            "0x67 1 clear / 2 start / 3 stop (04 §3.5)",
        ),
    )
}

# PC-side address filters of CMCHalAPI (A2 §4 "HAL address filter", 11 §3 intro). The vendor
# silently drops these addresses (zero-filled reply / fake success), so they never reach the card.
VENDOR_READ_DROPPED: tuple[tuple[int, int], ...] = (
    (104, 1000),
    (1100, 2000),
    (3000, 5000),
    (5008, 6000),
    (51000, 59000),
)
"""Open intervals ``(lo, hi)`` dropped by ``readReg`` 0x100225e0 (150/151 excepted)."""
VENDOR_WRITE_DROPPED: tuple[tuple[int, int], ...] = ((104, 5000),) + VENDOR_READ_DROPPED
"""``writeReg`` 0x10022760 additionally drops everything in 104..5000 except 150/151."""


def vendor_would_send(addr: int, *, write: bool) -> bool:
    """True if the Windows HAL would put a read/write of ``addr`` on the wire (A2 §4).

    UNVERIFIED: whether the intervals are open or closed at their ends was not stated
    in the analysis; open intervals (``lo < addr < hi``) are assumed, which matches
    "5000..5008 inside the sent window".
    """
    if addr in (150, 151):
        return True
    table = VENDOR_WRITE_DROPPED if write else VENDOR_READ_DROPPED
    return not any(lo < addr < hi for lo, hi in table)


# ================================================================================================
# Block 1000 word map (A2 §2, 11 §4.1)
# ================================================================================================


class Status(IntEnum):
    """Word index inside block 1000 (``RORegName_(i+1)``, A2 §2 EVIDENCE for the order)."""

    PROGRAM_ID = 0
    PROGRAM_VERSION = 1
    DATE = 2
    TIME = 3
    DI = 4
    DO = 5
    ALARM_1 = 6
    ALARM_2 = 7
    RUN_STATUS = 8  # no consumer in the vendor code; meaning unknown (A2 §2)
    AD_SAMPLE = 9
    DA1 = 10
    DA2 = 11
    DA3 = 12
    PWM_FREQ = 13
    PWM_DUTY = 14
    FIFO_FRAME_ID = 15
    FIFO_SPACE_MARGIN = 16
    FIFO_INTERP_CONFIG = 17
    SLAVE_DEVICE_INFO = 18
    PROCESSING_STATUS = 19
    PROCESSING_POSITION = 20
    EXT_DI = 21
    EXT_DO = 22
    CONTOUR_STATUS = 23
    CONTOUR_INDEX = 24
    POWER_ON_TIME = 25
    COMM_TIME = 26
    LASER_ON_TIME = 27
    DUAL_DRIVE_DEVIATION = 28
    SAMPLING_ENCODER_CFG = 29
    SAMPLING_ENCODER_LEN = 30
    PARAM_STATUS = 31

    @property
    def addr(self) -> int:
        """Absolute register address (1000 + i)."""
        return 1000 + int(self)


FIFO_MARGIN_EMPTY = 60000
"""Reg 1016 == 60000 means FIFO empty (``cmp [esi+0xd0],0xea60`` @0x10057f2d, A2 §2)."""
FIFO_SEND_HEADROOM_BYTES = 2000
"""Vendor sends a frame only if ``frameBytes + 2000 <= reg 1016`` (11 §5.1, A3 §7)."""
PARAM_STATUS_RESTART_REQUIRED = 1
"""Reg 1031 == 1: card needs restart after a parameter write; refuse motion (A2 §2 row 31)."""


def next_frame_id(frame_id_word: int) -> int:
    """Next FIFO frame id = reg 1015 + 1 (``mov eax,[esi+0xcc]; inc eax`` @0x100523cc, A2 §2)."""
    return (frame_id_word + 1) & 0xFFFFFFFF


def fifo_program_running(processing_status_word: int) -> bool:
    """Reg 1019 low byte == 1 means the FIFO program runs (``cmp al,1`` VM 0x100390d0, A2 §2)."""
    return (processing_status_word & 0xFF) == 1


def di_bit(port: int) -> tuple[Status, int] | None:
    """Word and bit for DI ``port`` (NC slot 84 ``0x1002b8d0``, A2 §2.1).

    Ports 1..12 -> word 4 bit ``port-1``; 13..28 -> word 21 bit ``port-13`` (only with the
    extension flag). Port 0 (unassigned) returns ``None``: the port treats it as disabled,
    unlike the vendor which reports it active for NC inputs (A2 §2.1 port-0 pitfall).
    """
    if 1 <= port <= 12:
        return Status.DI, port - 1
    if 13 <= port <= 28:
        return Status.EXT_DI, port - 13
    return None


def di_active(status: list[int] | tuple[int, ...], port: int, normally_closed: bool) -> bool:
    """Apply the PC-side NO/NC rule: ``type 1`` (NC) inverts the bit (A2 §2.1).

    ``status`` is the 36-word block-1000 read. Port 0 is always inactive (deviation, see
    :func:`di_bit`). UNVERIFIED: whether reg 1004 is the raw electrical level or already
    inverted by the card's own input-type register 5000 (A2 §2.1, bench: READ 5000/9).
    """
    loc = di_bit(port)
    if loc is None:
        return False
    word, bit = loc
    mask = 0xFFFFFF if word is Status.DI else 0xFFFF
    level = bool(((status[word] & mask) >> bit) & 1)
    return not level if normally_closed else level


def do_bit(port: int) -> tuple[Status, int] | None:
    """Word and bit for DO ``port`` (NC slot 82 ``0x1002b810``, A2 §2.2).

    Ports 1..10 -> word 5 bit ``port-1``; 11..26 -> word 22 bit ``port-11``; other -> None.
    """
    if 1 <= port <= 10:
        return Status.DO, port - 1
    if 11 <= port <= 26:
        return Status.EXT_DO, port - 11
    return None


def do_is_on(status: list[int] | tuple[int, ...], port: int) -> bool:
    """``isDOOn(port)`` from a 36-word block-1000 read (A2 §2.2)."""
    loc = do_bit(port)
    if loc is None:
        return False
    word, bit = loc
    return bool(((status[word] & 0xFFFF) >> bit) & 1)


# ---- alarm_1 / alarm_2 (A2 §2.3/§2.4, 11 §4.2/§4.3) ----------------------------------------------


class Alarm1(IntFlag):
    """Reg 1006 bits, 1 = active (A2 §2.3 EVIDENCE: decoder loop 0x56a82d-0x56aaec)."""

    AXIS_X = 1 << 0
    AXIS_Y = 1 << 1
    AXIS_Y2 = 1 << 2
    AXIS_HEIGHT = 1 << 3
    AXIS_W = 1 << 4
    AXIS_GENERIC = 0x00FFFFE0
    """Bits 5-23: "axis n fault" with no status word (C22)."""
    FOLLOWER_AXIS = 1 << 24
    """INFERENCE low-medium: on-board Z-follower axis flag (A2 §2.3 row 24)."""
    BUS_FAULT = 1 << 25
    OUTPUT_FAULT = 1 << 26
    ESTOP = 1 << 30


ALARM1_RESERVED_MASK = (1 << 27) | (1 << 28) | (1 << 29) | (1 << 31)
"""Bits with empty lang strings; the port treats any as an unknown alarm (11 §4.2)."""
ALARM1_AXIS_DETAIL_SLOTS = range(5)
"""Only slots 0-4 have a status word; bits >= 5 must not be decoded per axis (C22)."""


class Alarm2(IntFlag):
    """Reg 1007 bits 0-5 (A2 §2.4, loop 0x56aaf1-0x56ab6d); bits 6-31 unexamined."""

    ILLEGAL_COMMAND = 1 << 0
    INTERP_LENGTH = 1 << 1
    AXIS_COMMAND = 1 << 2
    FTC_COMMAND = 1 << 3
    PLC_COMMAND = 1 << 4
    FIFO_STARVATION = 1 << 5


# ---- block 2000 axis RO and the axis status word (A2 §3, 11 §4.4) ----------------------------------


class AxisRO(IntEnum):
    """Word ``k`` of an axis slot in block 2000 (A2 §3). Names of 3..9 are LIKELY (table order)."""

    STATUS = 0
    SPEED = 1  # 0.001 mm/s (C5)
    PULSE_POSITION = 2
    ENCODER_POSITION = 3  # UNVERIFIED name (table order, A2 §3)
    STOP_PULSE = 4  # UNVERIFIED name
    ENCODER_AT_STOP = 5  # UNVERIFIED name
    TOTAL_MILEAGE = 6  # UNVERIFIED name
    CALC_CUMULATIVE_PULSES = 7  # index confirmed, name UNVERIFIED
    CALC_CUMULATIVE_ENCODER = 8  # UNVERIFIED name
    CUMULATIVE_PULSES = 9  # UNVERIFIED name


def axis_ro_addr(slot: int, word: AxisRO | int) -> int:
    """Register of axis-RO word ``word`` of ``slot``: ``2000 + 10*slot + k`` (A2 §3)."""
    if not 0 <= slot < AXIS_SLOTS or not 0 <= int(word) < AXIS_RO_WORDS:
        raise IndexError(f"axis slot {slot} / word {word} out of range")
    return 2000 + AXIS_RO_WORDS * slot + int(word)


class AxisStatus(IntFlag):
    """Axis status word bits (A2 §3.1; 1 = fault, bit 15 1 = homed)."""

    HARD_LIMIT_POS = 1 << 0  # which physical switch = O8 bench
    HARD_LIMIT_NEG = 1 << 1
    SOFT_LIMIT_POS = 1 << 2
    SOFT_LIMIT_NEG = 1 << 3
    SERVO_ALARM = 1 << 4
    DUAL_DRIVE_ALARM = 1 << 5
    HOMED = 1 << 15  # INFERENCE high (A2 §3.1 verifier)


class AxisCommandType(IntEnum):
    """Byte 3 of the axis status word (VM 0x100390d0 compares, A2 §3.1)."""

    NONE = 0
    HOME = 2
    JOG = 3  # 3/4/5 = jog / move variants; which is which is UNVERIFIED
    JOG_4 = 4
    JOG_5 = 5


def axis_busy(status_word: int) -> bool:
    """Byte 2 != 0 means a command is executing (``sar 0x10; and 0xff; test``, A2 §3.1)."""
    return ((status_word >> 16) & 0xFF) != 0


def axis_command_type(status_word: int) -> int:
    """Byte 3 = current command type (A2 §3.1)."""
    return (status_word >> 24) & 0xFF


def homed_mask(axis_ro: list[int] | tuple[int, ...]) -> int:
    """5-bit mask of homed slots from a 50-word block-2000 read (VM slot 126 0x10039160)."""
    mask = 0
    for slot in range(AXIS_SLOTS):
        if (axis_ro[AXIS_RO_WORDS * slot] >> 15) & 1:
            mask |= 1 << slot
    return mask


class MachineState(IntEnum):
    """Derived machine state (VM 0x100390d0 values 0/1/2/4; MainApp 3 Stop, 5 Alarm; 11 §4.5)."""

    READY = 0
    ORIGIN = 1
    JOG = 2
    STOP = 3
    PROCESS = 4
    ALARM = 5


def derive_machine_state(
    status: list[int] | tuple[int, ...], axis_ro: list[int] | tuple[int, ...]
) -> MachineState:
    """Reproduce ``VM 0x100390d0`` (A2 §3.2): Process > Origin > Jog > Ready.

    States 3 (Stop) and 5 (Alarm) are set by MainApp, not derived here.
    """
    if fifo_program_running(status[Status.PROCESSING_STATUS]):
        return MachineState.PROCESS
    for slot in range(AXIS_SLOTS):
        word = axis_ro[AXIS_RO_WORDS * slot]
        if not axis_busy(word):
            continue
        t = axis_command_type(word)
        if t == AxisCommandType.HOME:
            return MachineState.ORIGIN
        if t in (3, 4, 5):
            return MachineState.JOG
    return MachineState.READY


# ---- alarm codes -> lang ids (A2 §5, 11 §0 O15) ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class AlarmCode:
    """One raised alarm: the vendor numeric code and the ``lang.txt`` key (A2 §5)."""

    code: int
    lang_id: str
    slot: int | None = None
    """Axis slot for ``EtherCATAxisErrorInfo_k`` codes."""


def alarm_codes(
    alarm1: int, alarm2: int, axis_ro: list[int] | tuple[int, ...] | None = None
) -> list[AlarmCode]:
    """Codes MainApp's poll routine 0x568a50 would raise for the two alarm words (A2 §2.3-§5).

    * alarm_1 bit b in 0..4: per set bit k in 0..5 of axis status word b ->
      ``8100 + b*32 + k`` / ``EtherCATAxisErrorInfo_k`` (needs ``axis_ro``). Without detail
      (or for bits 5..24, C22) the port reports ``8000 + b`` as a generic "axis fault".
    * bits 25/26/30 -> ``8000 + bit`` / ``EtherCATErrorInfo_1_<bit>``.
    * reserved bits 27-29/31 -> ``8000 + bit`` / ``"unknown"``.
    * alarm_2 bits 0..5 -> ``9000 + bit`` / ``EtherCATErrorInfo_2_0<bit>``.

    Deviation: the vendor treats alarm_1 == 0x01000000 alone as "no alarm" under two unnamed
    parameter gates; this function reports it (11 §4.2 row 24: "treat as alarm while cutting").
    """
    out: list[AlarmCode] = []
    for bit in range(32):
        if not (alarm1 >> bit) & 1:
            continue
        if bit <= 24:
            detail = False
            if bit in ALARM1_AXIS_DETAIL_SLOTS and axis_ro is not None:
                word = axis_ro[AXIS_RO_WORDS * bit]
                for k in range(6):
                    if (word >> k) & 1:
                        out.append(
                            AlarmCode(8100 + bit * 32 + k, f"EtherCATAxisErrorInfo_{k}", bit)
                        )
                        detail = True
            if not detail:
                out.append(AlarmCode(8000 + bit, "axis_fault", bit if bit < AXIS_SLOTS else None))
        elif bit in (25, 26, 30):
            out.append(AlarmCode(8000 + bit, f"EtherCATErrorInfo_1_{bit}"))
        else:
            out.append(AlarmCode(8000 + bit, "unknown"))
    for bit in range(6):
        if (alarm2 >> bit) & 1:
            out.append(AlarmCode(9000 + bit, f"EtherCATErrorInfo_2_{bit:02d}"))
    if alarm2 & ~0x3F & 0xFFFFFFFF:
        out.append(AlarmCode(9000 + 99, "unknown"))  # port convention: bits 6-31 (11 §4.3)
    return out


# ================================================================================================
# SystemRW 50000 and AxisRW 50200 (A1 §2, A3 V3, 04 §3.5, 11 §3.2)
# ================================================================================================


class SystemRW(IntEnum):
    """Words of block 50000 the PC uses (EVIDENCE: copy method VM slot 130 0x10039ae0)."""

    BUS_CYCLE = 5
    """Overwrites ``AX.InterpolationCycle`` (µs; XML 250) - tick period (A1 §6, 11 §5.3)."""
    ZF_TYPE = 6
    """Overwrites ``ZF.ZFType`` (A1 §6)."""
    K = 17
    """Card units per mm, 1000 here (``mov [edx+0xbab0],ecx`` @0x10039c36, A1 §2)."""

    @property
    def addr(self) -> int:
        """Absolute register address."""
        return 50000 + int(self)


class AxisRW(IntEnum):
    """Per-slot words of block 50200 (names from ``AxisRWRegName_1..14`` key order, 04 §3.5).

    UNVERIFIED: the name order is INFERENCE; only words 9/10 (lead / command pulses) have
    code evidence (11 §3.2, A3 V1).
    """

    CONFIG = 0
    SOFT_LIMIT_POS = 1
    SOFT_LIMIT_NEG = 2
    ACCEL = 3
    JERK = 4
    HOME_COARSE_SPEED = 5
    HOME_FINE_SPEED = 6
    ORIGIN_OFFSET = 7
    SPARE = 8
    LEAD = 9
    CMD_PULSES_PER_UNIT = 10
    ENC_PULSES_PER_UNIT = 11
    INPUT_CFG = 12
    OUTPUT_CFG = 13


def axis_rw_addr(slot: int, word: AxisRW | int) -> int:
    """Register of AxisRW ``word`` for ``slot``: ``50200 + 20*slot + k`` (A2 §1 stride 0x50)."""
    if not 0 <= slot < AXIS_SLOTS or not 0 <= int(word) < AXIS_RW_WORDS_ON_WIRE:
        raise IndexError(f"axis slot {slot} / word {word} out of range")
    return 50200 + AXIS_RW_WORDS_ON_WIRE * slot + int(word)


# ================================================================================================
# ZF follower words (A2 §6-§6.1; only used when ZFType != 0)
# ================================================================================================


class ZFAlarm(IntFlag):
    """ZF alarm word = ``getReg(7,1) & 0xffff`` (block 10000 word 1, A2 §6 table)."""

    Z_HARD_UP = 1 << 0  # gp33
    Z_HARD_DOWN = 1 << 1  # gp34
    Z_SOFT_UP = 1 << 2  # gp35
    Z_SOFT_DOWN = 1 << 3  # gp36
    SERVO_INPUT = 1 << 4  # gp37
    TOUCH_PLATE = 1 << 5  # gp38
    ENCODER = 1 << 6  # gp39
    SIGNAL_SMALL = 1 << 7  # gp40
    FOLLOW_ERROR = 1 << 8  # gp41
    CAPACITANCE_SMALL = 1 << 9  # gp42
    SIGNAL_LARGE = 1 << 10  # gp43
    FPGA_NOT_LOADED = 1 << 11  # gp141
    AXIS_VALUE = 1 << 12  # gp217
    SIGNAL_ZERO = 1 << 13  # gp218
    FTC_ALARM = 1 << 15  # gp59


class ZFState(IntEnum):
    """ZF run state from VM 0x10038fe0 (A2 §6.1)."""

    READY = 0
    FOLLOW = 1
    DRILL = 2
    JOG_UP = 3
    JOG_DOWN = 4
    ESTOP = 5
    STATE_6 = 6
    CALIBRATION = 7
    STATE_8 = 8


def derive_zf_state(zf_status: list[int] | tuple[int, ...]) -> ZFState:
    """Reproduce VM 0x10038fe0 from block-10000 words 2 (status) and 3 (run command)."""
    s, c = zf_status[2] & 0xFFFFFFFF, zf_status[3]
    if not s & 0x80000000:
        return ZFState.ESTOP
    if (s & 0xFF) == 4:
        return ZFState.DRILL
    return {
        102: ZFState.STATE_6,
        103: ZFState.JOG_DOWN,
        104: ZFState.FOLLOW,
        105: ZFState.READY,
        106: ZFState.STATE_8,
        107: ZFState.CALIBRATION,
    }.get(c, ZFState.READY)


# ================================================================================================
# Polling schedule
# ================================================================================================

VENDOR_MCCORE_MS = 30
"""``ipAdd.ini MCCore=30`` - core loop period (04 §1; "core loop" meaning INFERENCE)."""
VENDOR_MC_UPDATE_FACTOR = 1
"""``MCUpdateFactor=1`` - status poll every N core cycles (04 §1, INFERENCE)."""
VENDOR_ZFCORE_MS = 500
VENDOR_ZF_UPDATE_FACTOR = 20
"""``ZFCore=500``, ``ZFUpdateFactor=20`` (ipAdd.ini); 10000/18 cadence LIKELY 10 s (00 §9 row 6)."""
SLOW_TRANSACTION_LOG_MS = 500
"""Vendor logs a status poll taking > 500 ms (``cmp eax,0x1f4`` @0x1004e202/0x1004e313)."""
STATUS_POLL_FAILURE_WATCHDOG_S = 1.0
"""Status poll of block 1000 failing > 1 s during a job -> stopFifo + disarm (PORT-PLAN §8.2)."""


@dataclass(frozen=True, slots=True)
class PollEntry:
    """One read in a polling schedule."""

    block: str
    """Key into :data:`BLOCKS`."""
    period_ms: int
    queue: str
    """``fast`` (never blocked by slow reads) or ``slow`` (background, PORT-PLAN §3.4 row)."""
    condition: str = ""
    """Human-readable precondition (e.g. ``ZFType != 0``)."""

    @property
    def vector(self) -> tuple[int, int, int]:
        """The READ request vector ``[0x30, start, count]``."""
        b = BLOCKS[self.block]
        return (0x30, b.start, b.count)


# EVIDENCE (NCModule status poll 0x1004e120, re-read for this module): when connected,
# fast mode (VM+0x11fc > 0) calls VM slots 115/119/120 (vtable +0x1cc/+0x1dc/+0x1e0 =
# 0x100516b0 1000/36, 0x10051f20 50000/26, 0x10052130 60001/n); otherwise slots
# 115/116/118/119 (+0x1cc/+0x1d0/+0x1d8/+0x1dc = 1000/36, 2000/50, 50200/100, 50000/26).
# Slot addresses from the VM vtable bytes at 0x1008bd90..0x1008bda7.
POLL_PROFILE_NORMAL: tuple[str, ...] = ("status", "axis_ro", "axis_rw", "system_rw")
POLL_PROFILE_FAST: tuple[str, ...] = ("status", "system_rw", "fast")

PORT_POLL_SCHEDULE: tuple[PollEntry, ...] = (
    PollEntry("status", VENDOR_MCCORE_MS, "fast"),
    # UNVERIFIED period: the vendor reads 2000/50 in the same cycle as 1000/36; the port
    # keeps it on the fast queue at 3 x MCCore to halve the traffic while jogging.
    PollEntry("axis_ro", 3 * VENDOR_MCCORE_MS, "fast"),
    # UNVERIFIED periods below: vendor reads 50000/50200 every cycle; they are configuration
    # blocks, so the port reads them on the slow queue.
    PollEntry("system_rw", 1000, "slow"),
    PollEntry("axis_rw", 1000, "slow"),
    PollEntry("zf_status", VENDOR_ZFCORE_MS * VENDOR_ZF_UPDATE_FACTOR, "slow", "ZFType != 0"),
)
"""Port schedule (PORT-PLAN §3.4 row "Register map + polling schedule")."""


UNVERIFIED_NOTES: tuple[str, ...] = (
    "vendor_would_send: open-interval ends of the HAL address filters",
    "di_active: whether reg 1004 is raw level or card-inverted via reg 5000",
    "AxisRO words 3..9 names (table order only)",
    "AxisRW word names other than 9/10 (lang key order)",
    "AxisCommandType 3/4/5 split between jog and move",
    "Alarm1 bit 24 = on-board follower axis (INFERENCE low-medium)",
    "PORT_POLL_SCHEDULE periods other than 1000/36 @ 30 ms",
    "zf_status cadence 10 s (ZFCore x ZFUpdateFactor, LIKELY)",
)
