"""Simulator accepts and echoes the M1 command vectors (11 §2) and reflects them in status words."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from nexcut.mcc import commands as C
from nexcut.mcc.registers import AxisStatus, Status
from nexcut.mcc.simulator import CardSimulator, SimConfig
from nexcut.mcc.transaction import CardBusy, CardNotReady, McTransaction, RetryPolicy

POLICY = RetryPolicy(timeout_ms=100, send_times=2, recv_times=2, send_interval_ms=1)


@pytest.fixture
def rig() -> Iterator[tuple[CardSimulator, McTransaction]]:
    with (
        CardSimulator(config=SimConfig(motion_time_s=0.05)) as sim,
        McTransaction(sim.address, policy=POLICY) as client,
    ):
        yield sim, client


def send(client: McTransaction, cmd: C.CommandVector) -> None:
    client.write(cmd.register, cmd.words)


def test_every_allowed_builder_is_accepted_and_echoed(
    rig: tuple[CardSimulator, McTransaction],
) -> None:
    sim, client = rig
    cmds = [
        C.connect_prologue(),
        C.do_set(1, True),
        C.do_set(1, False),
        C.da_set(2, 0.0),
        C.pwm_off(1234),
        C.zf_stop(),
        C.stop_all(200000),
        C.stop_all_no_decel(),
        C.jog_release_stop([0, 1], 200000),
        C.fifo_clear(),
        C.fifo_stop(),
    ]
    for cmd in cmds:
        send(client, cmd)
    for cmd in (C.home_axis(0), C.jog_step(1, 5.0), C.go_to(10.0, 20.0)):
        send(client, cmd)
        time.sleep(0.08)
    echoed = [w for _, w in sim.command_log]
    expected = [c.words for c in cmds if c.register == 0x65]
    expected += [C.home_axis(0).words, C.jog_step(1, 5.0).words, C.go_to(10.0, 20.0).words]
    assert echoed == expected


def test_home_jog_goto_status(rig: tuple[CardSimulator, McTransaction]) -> None:
    sim, client = rig
    send(client, C.home_axis(0))
    axis = client.read(2000, 50)
    assert axis[0] >> 24 == 2 and (axis[0] >> 16) & 0xFF == 1  # homing, busy (A2 §3.1)
    with pytest.raises(CardBusy):
        send(client, C.jog_step(0, 5.0))  # exception 3 while moving (08 §4.5)
    time.sleep(0.08)
    axis = client.read(2000, 50)
    assert axis[0] & AxisStatus.HOMED and not (axis[0] >> 16) & 0xFF
    send(client, C.jog_step(0, 5.0))
    time.sleep(0.08)
    send(client, C.jog_step(0, -2.0))
    time.sleep(0.08)
    assert client.read(2002, 1) == [3000]  # relative moves
    send(client, C.move_axis_absolute(0, 100.0))
    time.sleep(0.08)
    assert client.read(2002, 1) == [100000]  # bit 31 = absolute (simulator reading)
    send(client, C.go_to(10.0, 20.0))
    time.sleep(0.08)
    assert client.read(2002, 1) == [10000] and client.read(2012, 1) == [20000]


def test_stop_cancels_motion(rig: tuple[CardSimulator, McTransaction]) -> None:
    sim, client = rig
    send(client, C.jog_continuous(0, True))
    send(client, C.jog_release_stop([0], 200000))
    axis = client.read(2000, 50)
    assert not (axis[0] >> 16) & 0xFF
    send(client, C.jog_step(0, 1.0))  # immediately accepted again


def test_outputs_da_pwm_reflected(rig: tuple[CardSimulator, McTransaction]) -> None:
    sim, client = rig
    send(client, C.do_set(9, True))
    send(client, C.do_set(3, True))
    status = client.read(1000, 36)
    assert status[Status.DO] == 0x104
    send(client, C.do_bulk_off(status[Status.DO] | 0x8, [1, 2, 3, 5, 6, 7, 9]))
    assert client.read(1000, 36)[Status.DO] == 0x8
    send(client, C.da_set(1, 5.0))
    send(client, C.pwm_set(5000, 4))
    status = client.read(1000, 36)
    assert status[Status.DA1] == 5000
    assert (status[Status.PWM_FREQ], status[Status.PWM_DUTY]) == (5000, 4)


def test_system_rw_and_resync_refused(rig: tuple[CardSimulator, McTransaction]) -> None:
    sim, client = rig
    sysrw = client.read(50000, 26)
    assert (sysrw[5], sysrw[6], sysrw[17]) == (250, 1, 1000)
    with pytest.raises(CardNotReady):
        client.write(0x65, [102])  # logged exception 2 (C10)
    with pytest.raises(CardNotReady):
        client.write(0x65, [9999, 16])


def test_processing_status_follows_fifo(rig: tuple[CardSimulator, McTransaction]) -> None:
    sim, client = rig
    send(client, C.fifo_clear())
    client.write(0x66, [1] + [0x00080BB8, 0, 5000 << 16] * 50)
    send(client, C.fifo_start())
    assert client.read(1000, 36)[Status.PROCESSING_STATUS] & 0xFF == 1
    send(client, C.fifo_stop())
    assert client.read(1000, 36)[Status.PROCESSING_STATUS] & 0xFF == 0
