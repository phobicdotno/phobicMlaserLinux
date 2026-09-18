"""Decoded status model published by ``nexcut-mccd`` (11 §4, A2 §2-§7).

Pure functions over raw register blocks (no I/O): block 1000/36 (11 §4.1), block 2000/50
axis RO (11 §4.4), block 50000/26 system RW (11 §3.2) and block 10000/18 ZF status
(11 §4.6). Bit tables come from :mod:`nexcut.mcc.registers`; this module adds the
operator-facing text and the JSON shape of the IPC ``status`` reply and subscription.

Alarm texts: the vendor raises numeric codes that map to ``Lang/lang.txt`` ids (11 §0 O15,
A2 §5). :data:`LANG_EN` holds the English column of those ids copied from the vendor
``lang.txt`` (lines 3125-3139, 953); :func:`load_lang_table` reads a full ``lang.txt`` when
one is available. Codes without a lang id (generic "axis n fault" for alarm_1 bits 5-23,
C22; reserved bits) get port texts marked in :data:`PORT_TEXT`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from nexcut.mcc.registers import (
    AXIS_RO_WORDS,
    AXIS_SLOTS,
    FIFO_MARGIN_EMPTY,
    PARAM_STATUS_RESTART_REQUIRED,
    AxisRO,
    AxisStatus,
    MachineState,
    Status,
    SystemRW,
    ZFAlarm,
    alarm_codes,
    axis_busy,
    axis_command_type,
    derive_machine_state,
    derive_zf_state,
    di_active,
    fifo_program_running,
)

__all__ = [
    "AXIS_NAMES",
    "LANG_EN",
    "PORT_TEXT",
    "AlarmInfo",
    "AxisState",
    "StatusSnapshot",
    "alarm_text",
    "axis_limit_blocks",
    "decode_alarms",
    "decode_axes",
    "load_lang_table",
    "watchdog_reasons",
]

AXIS_NAMES: tuple[str, ...] = ("X", "Y", "Y2", "Height", "W")
"""Slot names: 0 X, 1 Y, 2 Y2, 3 height axis (调高轴), 4 W lifting table (11 §0 O7)."""

LANG_EN: dict[str, str] = {
    "EtherCATErrorInfo_1_25": "Bus Fault",
    "EtherCATErrorInfo_1_26": "Output Fault",
    "EtherCATErrorInfo_1_30": "Emergency stop alarm",
    "EtherCATErrorInfo_2_00": "Illegal command",
    "EtherCATErrorInfo_2_01": "The interpolation data content length is abnormal",
    "EtherCATErrorInfo_2_02": "Axis control command execution exception",
    "EtherCATErrorInfo_2_03": "FTC order execution exception",
    "EtherCATErrorInfo_2_04": "PLC command execution exception",
    "EtherCATErrorInfo_2_05": "FIFO starvation",
    "EtherCATAxisErrorInfo_0": "%s hard positive limit alarm",
    "EtherCATAxisErrorInfo_1": "%s hard negative limit alarm",
    "EtherCATAxisErrorInfo_2": "%s soft positive limit alarm",
    "EtherCATAxisErrorInfo_3": "%s soft negative limit alarm",
    "EtherCATAxisErrorInfo_4": "%s servo input alarm",
    "EtherCATAxisErrorInfo_5": "%s dual drive alarm",
    "mp124": "Please press the OK button",
}
"""English texts of the alarm lang ids, copied from SRC ``Lang/lang.txt`` (UTF-16,
``id#zh#en``). ``mp124`` = "system is in E-stop, press OK to release" (A1 §4.4); its English
column is the vendor's (truncated) translation."""

PORT_TEXT: dict[str, str] = {
    "axis_fault": "%s axis fault",
    "unknown": "Unknown card alarm",
}
"""Port texts for codes without a vendor lang id (C22 bits 5-23, reserved bits 27-29/31,
alarm_2 bits 6-31). Not vendor strings."""

_AXIS_FAULT_NAMES: tuple[str, ...] = (
    "hard_limit_pos",
    "hard_limit_neg",
    "soft_limit_pos",
    "soft_limit_neg",
    "servo_alarm",
    "dual_drive_alarm",
)
"""Axis status bits 0-5 (11 §4.4, A2 §3.1)."""


def load_lang_table(path: Path | str) -> dict[str, tuple[str, str]]:
    """Read a vendor ``lang.txt`` (UTF-16 with BOM, CRLF, ``id#zh#en``) -> ``{id: (zh, en)}``."""
    raw = Path(path).read_bytes()
    text = raw.decode("utf-16")
    out: dict[str, tuple[str, str]] = {}
    for line in text.splitlines():
        parts = line.split("#")
        if len(parts) >= 3 and parts[0]:
            out[parts[0].strip()] = (parts[1], parts[2])
    return out


def alarm_text(
    lang_id: str, slot: int | None = None, table: Mapping[str, str] | None = None
) -> str:
    """Operator text for a lang id, with ``%s`` replaced by the axis name (A2 §5)."""
    tmpl = (table or LANG_EN).get(lang_id) or LANG_EN.get(lang_id) or PORT_TEXT.get(lang_id)
    if tmpl is None:
        tmpl = PORT_TEXT["unknown"]
    if "%s" in tmpl:
        name = AXIS_NAMES[slot] if slot is not None and 0 <= slot < AXIS_SLOTS else "axis"
        tmpl = tmpl.replace("%s", name)
    return tmpl


@dataclass(frozen=True, slots=True)
class AlarmInfo:
    """One raised alarm: vendor code, lang id, operator text (A2 §5)."""

    code: int
    lang_id: str
    text: str
    slot: int | None = None


@dataclass(frozen=True, slots=True)
class AxisState:
    """One axis slot decoded from block 2000 (11 §4.4)."""

    slot: int
    name: str
    status_word: int
    homed: bool
    """Bit 15 (INFERENCE high, A2 §3.1)."""
    busy: bool
    """Byte 2 != 0."""
    command_type: int
    """Byte 3: 2 go-origin, 3/4/5 jog/move variants."""
    faults: tuple[str, ...]
    """Names of set bits 0-5."""
    speed_mm_s: float
    """Word +1 in 0.001 mm/s at K = 1000 (C5), scaled by K."""
    position_counts: int
    """Word +2 as int32 (motor pulses on the card, µm in the simulator; A2 §3)."""


def _s32(w: int) -> int:
    w &= 0xFFFFFFFF
    return w - (1 << 32) if w & 0x80000000 else w


def decode_axes(axis_ro: Sequence[int], k: int = 1000) -> tuple[AxisState, ...]:
    """Decode the 50-word block 2000 into five :class:`AxisState` (11 §4.4)."""
    out = []
    for slot in range(AXIS_SLOTS):
        base = AXIS_RO_WORDS * slot
        word = int(axis_ro[base + AxisRO.STATUS]) & 0xFFFFFFFF
        faults = tuple(n for i, n in enumerate(_AXIS_FAULT_NAMES) if (word >> i) & 1)
        out.append(
            AxisState(
                slot=slot,
                name=AXIS_NAMES[slot],
                status_word=word,
                homed=bool(word & AxisStatus.HOMED),
                busy=axis_busy(word),
                command_type=axis_command_type(word),
                faults=faults,
                speed_mm_s=_s32(int(axis_ro[base + AxisRO.SPEED])) / float(k or 1000),
                position_counts=_s32(int(axis_ro[base + AxisRO.PULSE_POSITION])),
            )
        )
    return tuple(out)


def decode_alarms(
    alarm1: int, alarm2: int, axis_ro: Sequence[int] | None = None
) -> tuple[AlarmInfo, ...]:
    """alarm_1 / alarm_2 -> codes and texts (11 §4.2/§4.3, :func:`registers.alarm_codes`)."""
    return tuple(
        AlarmInfo(c.code, c.lang_id, alarm_text(c.lang_id, c.slot), c.slot)
        for c in alarm_codes(alarm1, alarm2, list(axis_ro) if axis_ro is not None else None)
    )


def axis_limit_blocks(axis_ro: Sequence[int], slot: int) -> tuple[bool, bool]:
    """``(block_positive, block_negative)`` jog directions from bits 0-3 (11 §4.7 row 5).

    Bits 0/2 = hard/soft + limit, 1/3 = hard/soft - limit (A2 §3.1). Which physical switch
    raises which bit is the O8 bench step (UNVERIFIED mapping to DI pairs).
    """
    word = int(axis_ro[AXIS_RO_WORDS * slot]) & 0xFFFFFFFF
    return bool(word & 0b0101), bool(word & 0b1010)


def watchdog_reasons(
    block1000: Sequence[int],
    axis_ro: Sequence[int] | None,
    *,
    jogging: bool,
    di_alarms: Sequence[tuple[int, bool]] = (),
    alarm_words: bool = True,
) -> list[str]:
    """Watchdog conditions in the priority order of 11 §4.7 (A2 §7 table), as texts.

    Rows 1-4 (alarm words) and row 8 (1031) are acted on by the safety gate on every
    status read; this function lists them plus row 5 (axis limit/fault bits while jogging)
    and row 6 (configured DI alarms through the NO/NC map, port 0 = disabled).
    ``alarm_words=False`` omits the rows the gate already handles (1-4, 8).
    """
    reasons: list[str] = []
    a1 = int(block1000[Status.ALARM_1]) & 0xFFFFFFFF if alarm_words else 0
    a2 = int(block1000[Status.ALARM_2]) & 0xFFFFFFFF if alarm_words else 0
    if a1 & (1 << 30):
        reasons.append("E-stop active (1006 bit 30)")
    if a1 & ((1 << 25) | (1 << 26)):
        reasons.append(f"bus/output fault (1006 {a1:#010x})")
    if a1 & 0x00FFFFFF:
        reasons.append(f"axis fault (1006 {a1:#010x})")
    if a2 & 0x3F:
        reasons.append(f"card command/FIFO alarm (1007 {a2:#010x})")
    if jogging and axis_ro is not None:
        for slot in range(AXIS_SLOTS):
            word = int(axis_ro[AXIS_RO_WORDS * slot]) & 0x3F
            if word:
                reasons.append(f"axis {AXIS_NAMES[slot]} fault bits {word:#04x} while jogging")
    for port, nc in di_alarms:
        if di_active(list(block1000), port, nc):
            reasons.append(f"DI{port} alarm input active")
    if alarm_words and int(block1000[Status.PARAM_STATUS]) == PARAM_STATUS_RESTART_REQUIRED:
        reasons.append("card restart required (1031 == 1)")
    return reasons


@dataclass(slots=True)
class StatusSnapshot:
    """What ``status`` returns and the subscription streams (JSON via :meth:`to_json`)."""

    t: float
    link: str
    arm_state: str
    estop_latched: bool
    poll_age_s: float | None
    connected: bool = False
    arm_owner: int | None = None
    """IPC connection id that holds the arming, ``None`` when nothing is armed (D9).

    With the D9/R12 amendment this is not decoration: ``jog_step``,
    ``jog_continuous_start``, ``home``, ``load_job`` and ``start_job`` are accepted **only**
    on that connection, so the id decides whether the reader of this snapshot can move at
    all.  Ids are handed out by :class:`~nexcut.mccd.ipc.IpcServer` and are unique for the
    life of one daemon process (they restart at 1 when the daemon does).
    """
    arm_owner_is_self: bool | None = None
    """Whether :attr:`arm_owner` is the connection this snapshot was built for.

    ``None`` when the snapshot was not built for a connection (an in-process caller, a test
    harness); ``False`` also when nothing is armed.  A client that armed on a *different*
    connection than the one it reads status on - the TUI, whose status stream is its own
    connection - must compare :attr:`arm_owner` with the ``arm_owner`` its ``arm_motion``
    reply returned instead.
    """
    program_version: int | None = None
    di_word: int = 0
    di_ports_high: tuple[int, ...] = ()
    """Raw DI levels, ports 1..24 (bit n = DI n+1). NO/NC is not applied (A2 §2.1)."""
    do_word: int = 0
    do_ports_on: tuple[int, ...] = ()
    alarm_1: int = 0
    alarm_2: int = 0
    alarms: tuple[AlarmInfo, ...] = ()
    axes: tuple[AxisState, ...] = ()
    axis_ro_age_s: float | None = None
    homed_slots: tuple[int, ...] = ()
    fifo_frame_id: int = 0
    fifo_margin: int = 0
    fifo_empty: bool = False
    fifo_running: bool = False
    restart_required: bool = False
    machine_state: str = "UNKNOWN"
    k: int = 1000
    bus_cycle_us: int | None = None
    zf_type: int | None = None
    zf_alarm: tuple[str, ...] = ()
    zf_state: str | None = None
    machine_fault: str | None = None
    watchdog: tuple[str, ...] = ()
    homing: tuple[int, ...] = ()
    jogs: dict[str, int] = field(default_factory=dict)
    """Axis index -> speed word of continuous jogs under the deadman."""
    last_error: str | None = None

    def to_json(self) -> dict[str, Any]:
        """JSON-serialisable dict (IPC payload)."""
        return asdict(self)

    @classmethod
    def build(
        cls,
        *,
        t: float,
        link: str,
        arm_state: str,
        estop_latched: bool,
        poll_age_s: float | None,
        block1000: Sequence[int] | None,
        arm_owner: int | None = None,
        arm_owner_is_self: bool | None = None,
        axis_ro: Sequence[int] | None,
        axis_ro_age_s: float | None = None,
        system_rw: Sequence[int] | None = None,
        zf_status: Sequence[int] | None = None,
        fifo_running: bool | None = None,
        machine_fault: str | None = None,
        watchdog: Sequence[str] = (),
        homing: Sequence[int] = (),
        jogs: Mapping[int, int] | None = None,
        last_error: str | None = None,
    ) -> StatusSnapshot:
        """Decode raw blocks into a snapshot (11 §4.1-§4.6)."""
        snap = cls(
            t=t,
            link=link,
            arm_state=arm_state,
            estop_latched=estop_latched,
            poll_age_s=poll_age_s,
            connected=link == "CONNECTED",
            arm_owner=arm_owner,
            arm_owner_is_self=arm_owner_is_self,
            machine_fault=machine_fault,
            watchdog=tuple(watchdog),
            homing=tuple(homing),
            jogs={str(i): v for i, v in (jogs or {}).items()},
            last_error=last_error,
        )
        if system_rw is not None and len(system_rw) > SystemRW.K:
            snap.k = int(system_rw[SystemRW.K]) or 1000
            snap.bus_cycle_us = int(system_rw[SystemRW.BUS_CYCLE])
            snap.zf_type = int(system_rw[SystemRW.ZF_TYPE])
        if axis_ro is not None and len(axis_ro) >= AXIS_SLOTS * AXIS_RO_WORDS:
            snap.axes = decode_axes(axis_ro, snap.k)
            snap.axis_ro_age_s = axis_ro_age_s
            snap.homed_slots = tuple(a.slot for a in snap.axes if a.homed)
        if block1000 is not None and len(block1000) >= 36:
            b = [int(x) & 0xFFFFFFFF for x in block1000]
            snap.program_version = b[Status.PROGRAM_VERSION]
            snap.di_word = b[Status.DI] & 0xFFFFFF
            snap.di_ports_high = tuple(n + 1 for n in range(24) if (snap.di_word >> n) & 1)
            snap.do_word = b[Status.DO] & 0xFFFF
            snap.do_ports_on = tuple(n + 1 for n in range(16) if (snap.do_word >> n) & 1)
            snap.alarm_1, snap.alarm_2 = b[Status.ALARM_1], b[Status.ALARM_2]
            snap.alarms = decode_alarms(snap.alarm_1, snap.alarm_2, axis_ro)
            snap.fifo_frame_id = b[Status.FIFO_FRAME_ID]
            snap.fifo_margin = b[Status.FIFO_SPACE_MARGIN]
            snap.fifo_empty = snap.fifo_margin == FIFO_MARGIN_EMPTY
            snap.fifo_running = fifo_program_running(b[Status.PROCESSING_STATUS])
            snap.restart_required = b[Status.PARAM_STATUS] == PARAM_STATUS_RESTART_REQUIRED
            if axis_ro is not None and len(axis_ro) >= AXIS_SLOTS * AXIS_RO_WORDS:
                state = derive_machine_state(b, list(axis_ro))
            else:
                state = MachineState.PROCESS if snap.fifo_running else None
            # 11 §4.5: MainApp's state 5 Alarm on any watchdog condition / E-stop latch.
            # alarm_1 == 0x01000000 alone is not an alarm while idle (11 §4.2 row 24).
            follower_only = snap.alarm_1 == 0x01000000 and not snap.alarm_2
            alarmed = bool(snap.alarms) and not (follower_only and not snap.fifo_running)
            if alarmed or estop_latched or snap.restart_required or watchdog:
                state = MachineState.ALARM
            snap.machine_state = state.name if state is not None else "UNKNOWN"
        if fifo_running is not None:
            snap.fifo_running = snap.fifo_running or fifo_running
        if zf_status is not None and len(zf_status) >= 4 and snap.zf_type:
            zf_word = int(zf_status[1]) & 0xFFFF
            snap.zf_alarm = tuple(
                f.name for f in ZFAlarm if f.name is not None and zf_word & int(f)
            )
            snap.zf_state = derive_zf_state(list(zf_status)).name
        return snap
