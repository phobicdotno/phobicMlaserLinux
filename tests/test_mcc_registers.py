"""Register map, bit tables and derived state (A2 §1-§6, 11 §4)."""

from __future__ import annotations

from nexcut.mcc import registers as R
from nexcut.mcc.registers import Alarm1, Alarm2, AxisStatus, MachineState, Status, ZFState


def status_words(**kw: int) -> list[int]:
    words = [0] * 36
    for name, value in kw.items():
        words[Status[name]] = value
    return words


def test_block_addresses() -> None:
    assert Status.FIFO_FRAME_ID.addr == 1015 and Status.FIFO_SPACE_MARGIN.addr == 1016
    assert Status.PROCESSING_STATUS.addr == 1019 and Status.PARAM_STATUS.addr == 1031
    assert R.BLOCKS["status"].count == 36 and R.BLOCKS["axis_ro"].count == 50
    assert R.BLOCKS["axis_rw"].count == 100 and R.BLOCKS["system_rw"].count == 26
    assert R.BLOCKS["licence"].access is R.Access.DENY
    assert R.axis_ro_addr(1, R.AxisRO.PULSE_POSITION) == 2012
    assert R.axis_rw_addr(0, R.AxisRW.LEAD) == 50209  # axis-0 word 9 (11 §3.2)
    assert R.SystemRW.K.addr == 50017 and R.SystemRW.BUS_CYCLE.addr == 50005


def test_poll_profiles_from_vtable() -> None:
    normal = [(R.BLOCKS[b].start, R.BLOCKS[b].count) for b in R.POLL_PROFILE_NORMAL]
    assert normal == [(1000, 36), (2000, 50), (50200, 100), (50000, 26)]
    fast = [(R.BLOCKS[b].start, R.BLOCKS[b].count) for b in R.POLL_PROFILE_FAST]
    assert fast == [(1000, 36), (50000, 26), (60001, 120)]
    first = R.PORT_POLL_SCHEDULE[0]
    assert first.vector == (0x30, 1000, 36) and first.period_ms == 30 and first.queue == "fast"


def test_vendor_address_filter() -> None:
    assert R.vendor_would_send(5001, write=True) and R.vendor_would_send(5008, write=True)
    assert not R.vendor_would_send(5012, write=True)  # swallowed PC-side (A2 §4)
    assert R.vendor_would_send(150, write=True) and not R.vendor_would_send(1000, write=True)
    assert R.vendor_would_send(1000, write=False)


def test_di_do_bits() -> None:
    s = status_words(DI=0b1000_0000_0001, DO=0x104, EXT_DI=0x1)
    assert R.di_active(s, 1, normally_closed=False)
    assert not R.di_active(s, 2, normally_closed=False)
    assert R.di_active(s, 2, normally_closed=True)  # NC: bit clear -> active
    assert R.di_active(s, 12, normally_closed=False)
    assert R.di_active(s, 13, normally_closed=False)
    assert not R.di_active(s, 0, normally_closed=True)  # port 0 disabled (vendor: active)
    assert R.do_is_on(s, 3) and R.do_is_on(s, 9) and not R.do_is_on(s, 1)
    assert R.do_bit(11) == (Status.EXT_DO, 0) and R.do_bit(0) is None


def test_fifo_helpers() -> None:
    assert R.next_frame_id(0x3B) == 0x3C
    assert R.fifo_program_running(0x0301) and not R.fifo_program_running(2)
    assert R.FIFO_MARGIN_EMPTY == 60000


def test_axis_status_and_machine_state() -> None:
    axis = [0] * 50
    assert R.derive_machine_state(status_words(), axis) is MachineState.READY
    axis[10] = (2 << 24) | (1 << 16)  # Y homing, busy
    assert R.derive_machine_state(status_words(), axis) is MachineState.ORIGIN
    axis[10] = (3 << 24) | (1 << 16)
    assert R.derive_machine_state(status_words(), axis) is MachineState.JOG
    axis[10] = 3 << 24  # type set but not busy
    assert R.derive_machine_state(status_words(), axis) is MachineState.READY
    assert R.derive_machine_state(status_words(PROCESSING_STATUS=1), axis) is MachineState.PROCESS
    axis[0] = int(AxisStatus.HOMED)
    axis[40] = int(AxisStatus.HOMED)
    assert R.homed_mask(axis) == 0b10001
    assert R.axis_busy(1 << 16) and R.axis_command_type(0x02010000) == 2


def test_alarm_codes() -> None:
    axis = [0] * 50
    axis[0] = int(AxisStatus.HARD_LIMIT_NEG | AxisStatus.SERVO_ALARM)
    codes = R.alarm_codes(int(Alarm1.AXIS_X | Alarm1.ESTOP | Alarm1.BUS_FAULT), 0, axis)
    assert [(c.code, c.lang_id) for c in codes] == [
        (8101, "EtherCATAxisErrorInfo_1"),
        (8104, "EtherCATAxisErrorInfo_4"),
        (8025, "EtherCATErrorInfo_1_25"),
        (8030, "EtherCATErrorInfo_1_30"),
    ]
    y = R.alarm_codes(int(Alarm1.AXIS_Y), 0, [0] * 50)
    assert [(c.code, c.slot) for c in y] == [(8001, 1)]
    axis[10] = int(AxisStatus.SOFT_LIMIT_POS)
    assert R.alarm_codes(int(Alarm1.AXIS_Y), 0, axis)[0].code == 8100 + 32 + 2
    assert [c.code for c in R.alarm_codes(1 << 7, 0, axis)] == [8007]  # no detail for b >= 5 (C22)
    assert [c.lang_id for c in R.alarm_codes(0, int(Alarm2.FIFO_STARVATION))] == [
        "EtherCATErrorInfo_2_05"
    ]
    assert R.alarm_codes(1 << 28, 0)[0].lang_id == "unknown"


def test_zf_state() -> None:
    assert R.derive_zf_state([0, 0, 0, 105]) is ZFState.ESTOP  # bit 31 clear
    assert R.derive_zf_state([0, 0, 0x80000004, 105]) is ZFState.DRILL
    assert R.derive_zf_state([0, 0, 0x80000000, 104]) is ZFState.FOLLOW
    assert R.derive_zf_state([0, 0, 0x80000000, 107]) is ZFState.CALIBRATION
    assert R.ZFAlarm.FTC_ALARM == 0x8000
