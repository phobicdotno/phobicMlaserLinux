"""Simulator FIFO behaviour (04 §3.5 reg 0x67, §3.7; 08 §4.4) driven through the client."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from nexcut.mcc.framing import encode_vector
from nexcut.mcc.simulator import (
    ALARM2_FIFO_STARVATION,
    RO_ALARM2,
    RO_FIFO_FRAME_ID,
    RO_FIFO_SPACE,
    RO_OUTPUTS,
    RO_RUN_STATUS,
    RUN_FIFO,
    CardSimulator,
    SimConfig,
)
from nexcut.mcc.transaction import CardBusy, CardSilent, McTransaction, RetryPolicy

POLICY = RetryPolicy(timeout_ms=100, send_times=3, recv_times=3, send_interval_ms=1)


def tick(dx: int, dy: int, freq: int = 5000, duty: int = 4) -> list[int]:
    """Opcode 3000 item: header 0x00080BB8, (dY<<16 | dX), (freq<<16 | duty) (08 §4.4)."""
    return [0x00080BB8, ((dy & 0xFFFF) << 16) | (dx & 0xFFFF), (freq << 16) | duty]


def frame(frame_id: int, n_ticks: int) -> list[int]:
    words = [frame_id]
    for _ in range(n_ticks):
        words += tick(1, -1)
    return words


@pytest.fixture
def rig() -> Iterator[tuple[CardSimulator, McTransaction]]:
    cfg = SimConfig(tick_s=0.002, fifo_capacity_items=500, fifo_space_unit="items")
    with CardSimulator(config=cfg) as sim, McTransaction(sim.address, policy=POLICY) as client:
        yield sim, client


def test_logged_fifo_frame_shape_is_accepted(rig: tuple[CardSimulator, McTransaction]) -> None:
    sim, client = rig
    words = frame(0x3C, 99)  # 298 words = frame id + 99 x 3 (04 §3.7)
    assert len(words) == 0x12A
    assert len(encode_vector([0x40, 0x66, len(words), *words], 0)) == 1206  # 04 §3.7 datagram
    client.write(0x67, [1])
    client.write(0x66, words)
    status = client.read(1000, 36)
    assert status[RO_FIFO_FRAME_ID - 1000] == 0x3C
    assert status[RO_FIFO_SPACE - 1000] == 500 - 99


def test_fifo_margin_in_bytes_by_default() -> None:
    """Reg 1016 = bytes free, 60000 when empty (11 C2, §4.1 row 16): default simulator unit."""
    cfg = SimConfig(tick_s=0.002)
    assert cfg.fifo_space_unit == "bytes"
    with CardSimulator(config=cfg) as sim, McTransaction(sim.address, policy=POLICY) as client:
        client.write(0x67, [1])
        assert client.read(1000, 36)[RO_FIFO_SPACE - 1000] == 60000
        client.write(0x66, frame(0x3C, 99))
        assert client.read(1000, 36)[RO_FIFO_SPACE - 1000] == 60000 - 99 * 12
        client.write(0x67, [1])
        assert client.read(1000, 36)[RO_FIFO_SPACE - 1000] == 60000
    with pytest.raises(ValueError):
        SimConfig(fifo_space_unit="words")  # type: ignore[arg-type]


def test_consumption_at_tick_and_starvation(rig: tuple[CardSimulator, McTransaction]) -> None:
    sim, client = rig
    client.write(0x67, [1])
    prologue = [1, 0x000C270F, 2, 0x100, 0x100]  # frame id 1, 9999[2, 0x100, 0x100] (08 §4.4)
    client.write(0x66, prologue)
    client.write(0x66, frame(2, 50))
    client.write(0x67, [2])
    assert client.read(1000, 36)[RO_RUN_STATUS - 1000] == RUN_FIFO
    time.sleep(0.05)
    mid = sim.snapshot()
    assert 0 < mid["ticks_consumed"] < 50
    time.sleep(0.25)
    snap = sim.snapshot()
    assert snap["ticks_consumed"] == 50 and snap["x"] == 50 and snap["y"] == -50
    assert sim.registers[RO_OUTPUTS] & 0x100
    assert snap["underruns"] == 1 and snap["running"] == 0
    # FIFO starvation = alarm_2 (reg 1007) bit 5 (A2 §2.4)
    assert client.read(1000, 36)[RO_ALARM2 - 1000] & ALARM2_FIFO_STARVATION
    client.write(0x67, [1])  # clear resets the starvation alarm (simulator convention)
    assert not client.read(1000, 36)[RO_ALARM2 - 1000] & ALARM2_FIFO_STARVATION


def test_resent_frame_id_not_queued_twice(rig: tuple[CardSimulator, McTransaction]) -> None:
    sim, client = rig
    client.write(0x67, [1])
    sim.drop_replies(1)  # ack lost -> the client re-sends the same datagram (same frame id)
    client.write(0x66, frame(7, 10))
    assert sim.snapshot()["queued"] == 10
    assert sim.snapshot()["frames_accepted"] == 1


def test_overflow_and_bad_tlv_refused(rig: tuple[CardSimulator, McTransaction]) -> None:
    sim, client = rig
    client.write(0x67, [1])
    with pytest.raises(CardBusy):
        client.write(0x66, [9, 0x00080BB8, 0])  # item needs 2 args, only 1 present
    for fid in range(1, 6):
        client.write(0x66, frame(fid, 99))
    with pytest.raises(CardBusy):
        client.write(0x66, frame(6, 99))  # 594 > 500 capacity


def test_motion_refused_while_streaming(rig: tuple[CardSimulator, McTransaction]) -> None:
    sim, client = rig
    client.write(0x67, [1])
    client.write(0x66, frame(1, 99))
    client.write(0x67, [2])
    with pytest.raises(CardBusy):
        client.write(0x65, [3, 0, 50000, 5999, 59990, 5000])  # jog X +5 mm (11 §2 V2)
    client.write(0x65, [1, 0x1F, 2, 2000, 20000])  # stop-all is sub-cmd 1 (11 §8), accepted
    client.write(0x67, [3])
    client.write(0x65, [3, 0, 50000, 5999, 59990, 5000])


def test_reboot_loses_fifo(rig: tuple[CardSimulator, McTransaction]) -> None:
    sim, client = rig
    client.write(0x67, [1])
    client.write(0x66, frame(1, 20))
    sim.reboot(silent_s=0.15, not_ready_s=0.0)
    client.set_policy(RetryPolicy(timeout_ms=50, send_times=1, recv_times=1))
    with pytest.raises(CardSilent):
        client.read(1000, 2)
    time.sleep(0.2)
    assert client.read(1000, 36)[RO_FIFO_SPACE - 1000] == 500
    assert sim.snapshot()["queued"] == 0


def test_bad_crc_request_is_ignored(rig: tuple[CardSimulator, McTransaction]) -> None:
    import socket

    sim, _ = rig
    good = encode_vector([0x30, 1000, 2], 5)
    bad = good[:2] + bytes((good[3], good[2])) + good[4:]  # lo-first CRC = not what a PC sends
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(0.2)
        s.sendto(bad, sim.address)
        with pytest.raises(TimeoutError):
            s.recvfrom(1500)
        s.sendto(good, sim.address)
        reply, _ = s.recvfrom(1500)
    assert reply[:2] == b"\x00\x05"
    assert [r.action for r in sim.requests] == ["bad-crc", "replied"]
