"""mccd/status.py: decoded status model (11 §4, A2 §2-§7)."""

from __future__ import annotations

from pathlib import Path

from nexcut.mccd.status import (
    LANG_EN,
    StatusSnapshot,
    alarm_text,
    axis_limit_blocks,
    decode_alarms,
    decode_axes,
    load_lang_table,
    watchdog_reasons,
)


def blocks() -> tuple[list[int], list[int]]:
    b = [0] * 36
    b[1] = 20152
    b[16] = 60000
    return b, [0] * 50


def test_idle_snapshot_is_ready() -> None:
    b, ro = blocks()
    b[4] = 0b1001  # DI1, DI4 high
    b[5] = 0x104  # DO3, DO9
    s = StatusSnapshot.build(
        t=1.0,
        link="CONNECTED",
        arm_state="DISARMED",
        estop_latched=False,
        poll_age_s=0.01,
        block1000=b,
        axis_ro=ro,
        system_rw=[0] * 5 + [250, 1] + [0] * 10 + [1000] + [0] * 8,
    )
    assert s.machine_state == "READY" and s.connected
    assert s.di_ports_high == (1, 4) and s.do_ports_on == (3, 9)
    assert s.fifo_empty and s.fifo_margin == 60000 and not s.fifo_running
    assert (s.k, s.bus_cycle_us, s.zf_type) == (1000, 250, 1)
    assert s.alarms == () and s.watchdog == ()
    d = s.to_json()
    assert d["axes"][0]["name"] == "X" and d["axes"][3]["name"] == "Height"


def test_axis_decode_and_machine_state() -> None:
    b, ro = blocks()
    ro[0] = 0x8000 | (1 << 16) | (3 << 24)  # X homed, busy, jog
    ro[10] = (1 << 16) | (2 << 24) | 0b10  # Y busy homing, hard - limit
    ro[12] = 0xFFFFFC18  # Y pulse position -1000
    ro[11] = 20000  # 20 mm/s at K=1000 (C5)
    axes = decode_axes(ro)
    assert axes[0].homed and axes[0].busy and axes[0].command_type == 3
    assert axes[1].faults == ("hard_limit_neg",) and axes[1].position_counts == -1000
    assert axes[1].speed_mm_s == 20.0
    s = StatusSnapshot.build(
        t=0, link="CONNECTED", arm_state="MOTION_ARMED", estop_latched=False,
        poll_age_s=0.0, block1000=b, axis_ro=ro,
    )  # fmt: skip
    assert s.machine_state == "JOG"  # first busy slot decides (registers.derive_machine_state)
    ro[0] = 0x8000
    s = StatusSnapshot.build(
        t=0, link="CONNECTED", arm_state="MOTION_ARMED", estop_latched=False,
        poll_age_s=0.0, block1000=b, axis_ro=ro,
    )  # fmt: skip
    assert s.machine_state == "ORIGIN"  # 11 §4.5
    assert s.homed_slots == (0,)
    assert axis_limit_blocks(ro, 1) == (False, True)
    b[19] = 1
    s = StatusSnapshot.build(
        t=0, link="CONNECTED", arm_state="MOTION_ARMED", estop_latched=False,
        poll_age_s=0.0, block1000=b, axis_ro=ro,
    )  # fmt: skip
    assert s.machine_state == "PROCESS"


def test_alarm_texts_from_lang_ids() -> None:
    ro = [0] * 50
    ro[10] = 0b10001  # Y hard + limit, servo alarm
    alarms = decode_alarms((1 << 1) | (1 << 25) | (1 << 30) | (1 << 7), 1 << 5, ro)
    texts = {a.code: a.text for a in alarms}
    assert texts[8100 + 32 + 0] == "Y hard positive limit alarm"
    assert texts[8100 + 32 + 4] == "Y servo input alarm"
    assert texts[8025] == "Bus Fault"
    assert texts[8030] == "Emergency stop alarm"
    assert texts[8007] == "axis axis fault"  # bit 7: no status word (C22), port text
    assert texts[9005] == "FIFO starvation"
    assert alarm_text("EtherCATAxisErrorInfo_3", 4) == "W soft negative limit alarm"
    assert alarm_text("nonexistent") == "Unknown card alarm"


def test_estop_and_alarm_force_alarm_state() -> None:
    b, ro = blocks()
    b[6] = 1 << 30
    s = StatusSnapshot.build(
        t=0, link="CONNECTED", arm_state="DISARMED", estop_latched=True,
        poll_age_s=0.0, block1000=b, axis_ro=ro,
    )  # fmt: skip
    assert s.machine_state == "ALARM"
    b[6] = 0x01000000  # follower flag alone, idle: not an alarm (11 §4.2 row 24)
    s = StatusSnapshot.build(
        t=0, link="CONNECTED", arm_state="DISARMED", estop_latched=False,
        poll_age_s=0.0, block1000=b, axis_ro=ro,
    )  # fmt: skip
    assert s.machine_state == "READY"


def test_watchdog_reasons_priority_and_filters() -> None:
    b, ro = blocks()
    b[6] = (1 << 30) | (1 << 25) | 1
    b[7] = 1 << 5
    b[31] = 1
    ro[0] = 0b1
    b[4] = 0  # DI11 low: an NC input reads active
    r = watchdog_reasons(b, ro, jogging=True, di_alarms=[(11, True), (4, False)])
    assert r[0].startswith("E-stop") and r[1].startswith("bus/output")
    assert any("axis X fault bits" in x for x in r)
    assert any(x == "DI11 alarm input active" for x in r)
    assert not any(x.startswith("DI4") for x in r)
    assert r[-1].startswith("card restart required")
    only_daemon = watchdog_reasons(b, ro, jogging=False, alarm_words=False)
    assert only_daemon == []


def test_embedded_lang_texts_match_vendor_lang_txt(src_dir: Path) -> None:
    table = load_lang_table(src_dir / "Lang" / "lang.txt")
    for lang_id, en in LANG_EN.items():
        assert table[lang_id][1] == en, lang_id
