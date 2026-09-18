"""STATUS §5 task 9: the audit log and the simulator's request list stay bounded.

``SafeMccClient.write_log`` used to keep every write in full - for a FIFO frame a 298-word
tuple plus the 1 206 encoded bytes, ~12 kB - and was trimmed only opportunistically by the
daemon watchdog; ``CardSimulator.requests`` grew the same way. D13 §4 needs a log that can be
kept for an arming-incident review: every non-FIFO write in full, the order of all writes, and
a visible count of what was compacted or dropped.
"""

from __future__ import annotations

import array
import gc
import tracemalloc
import zlib
from collections.abc import Sequence
from dataclasses import replace

import pytest

from nexcut.mcc import commands as C
from nexcut.mcc.commands import MachineParams
from nexcut.mcc.framing import REG_FIFO_DATA
from nexcut.mcc.safety import (
    ArmState,
    FifoDigest,
    SafeMccClient,
    SafetyViolation,
    WriteLog,
    WriteRecord,
)
from nexcut.mcc.simulator import CardSimulator, SimConfig
from nexcut.mcc.transaction import McTransaction, RetryPolicy

TICK_HEADER = 0x00080BB8  # opcode 3000, 8 payload bytes


class FakeClient:
    def __init__(self) -> None:
        self.writes = 0
        self.next_seq = 0

    def read(self, addr: int, count: int) -> list[int]:
        return [0] * count

    def write(self, addr: int, words: Sequence[int]) -> None:
        self.writes += 1
        self.next_seq = (self.next_seq + 1) & 0xFFFF


def frame_words(frame_id: int, duty: int = 0) -> list[int]:
    """``[frame_id, 99 ticks]`` - the 298-word 0x66 payload of a full frame."""
    out = [frame_id]
    for _ in range(99):
        out += [TICK_HEADER, (1 << 16) | 1, (5000 << 16) | duty]
    return out


def motion_gate(**log_limits: int) -> tuple[SafeMccClient, FakeClient]:
    fake = FakeClient()
    gate = SafeMccClient(fake, supervise=False, log_limits=log_limits or None)
    gate.arming.arm_motion()
    gate.read(1000, 36)  # streaming needs a fresh status poll
    return gate, fake


def crc_of(words: Sequence[int]) -> int:
    return zlib.crc32(array.array("I", words).tobytes())


# ---- the write log ------------------------------------------------------------------------------


def test_fifo_frames_older_than_the_window_are_compacted_to_a_digest() -> None:
    gate, _ = motion_gate(fifo_full_keep=4)
    for fid in range(1, 21):
        gate.write(REG_FIFO_DATA, frame_words(fid, duty=30))
    fifo = [r for r in gate.write_log if r.addr == REG_FIFO_DATA]
    assert len(fifo) == 20
    full, compact = fifo[-4:], fifo[:-4]
    assert all(r.digest is None and len(r.words) == 298 and r.frame for r in full)
    for fid, r in enumerate(compact, start=1):
        assert r.words == () and r.frame == b""
        d = r.digest
        assert isinstance(d, FifoDigest)
        assert d.frame_id == fid and d.n_words == 298
        # the digest is of what went on the wire: the duty was stripped (dry run)
        assert d.words_crc32 == crc_of(frame_words(fid, duty=0))
        assert d.first_opcodes == (3000, 3000, 3000)
        assert d.frame_bytes == 1206
        assert r.decision == "sent" and r.stripped == 99 and r.state is ArmState.MOTION_ARMED
    assert gate.write_log.compacted == 16 and gate.write_log.dropped == 0


def test_non_fifo_writes_are_kept_in_full_and_the_order_is_preserved() -> None:
    gate, _ = motion_gate(fifo_full_keep=2, max_fifo_digests=5)
    expected: list[tuple[int, tuple[int, ...]]] = []
    for fid in range(1, 31):
        words = frame_words(fid)
        gate.write(REG_FIFO_DATA, words)
        expected.append((REG_FIFO_DATA, tuple(words)))
        if fid % 3 == 0:
            cmd = C.jog_step(0, 0.1 * fid, replace(MachineParams(), is_fast_mode=False))
            gate.send(cmd)
            expected.append((cmd.register, tuple(cmd.words)))
    log = list(gate.write_log)
    # 10 jogs in full, 5 FIFO digests + 2 full FIFO frames; 23 FIFO records dropped
    assert [r.addr for r in log if r.addr != REG_FIFO_DATA] == [C.jog_step(0, 1.0).register] * 10
    assert all(r.frame and r.words and r.digest is None for r in log if r.addr != REG_FIFO_DATA)
    assert gate.write_log.dropped_fifo == 23 and gate.write_log.dropped_other == 0
    assert gate.write_log.dropped == 23
    # what is left is in the order it was written
    kept_ids = [r.digest.frame_id if r.digest else r.words[0] for r in log if r.addr == 0x66]
    assert kept_ids == [24, 25, 26, 27, 28, 29, 30]
    t = [r.t for r in log]
    assert t == sorted(t)
    positions = {e: i for i, e in enumerate(expected)}
    order = [
        positions[(r.addr, r.words)] if r.digest is None else
        positions[(REG_FIFO_DATA, tuple(frame_words(r.digest.frame_id)))]
        for r in log
    ]  # fmt: skip
    assert order == sorted(order)
    assert gate.write_log[-1] is log[-1] and gate.write_log[0] is log[0]


def test_a_refused_fifo_frame_is_kept_in_full() -> None:
    gate, _ = motion_gate(fifo_full_keep=1)
    bad = [7, 0x0004_1234, 4242]  # opcode 0x1234 is outside the 11 §5.2 grammar
    with pytest.raises(SafetyViolation):
        gate.write(REG_FIFO_DATA, bad)
    for fid in range(8, 20):
        gate.write(REG_FIFO_DATA, frame_words(fid))
    refused = [r for r in gate.write_log if r.decision == "refused"]
    assert len(refused) == 1 and refused[0].words == tuple(bad) and refused[0].digest is None


def test_the_other_cap_drops_the_oldest_and_counts_it() -> None:
    log = WriteLog(max_records=3)
    for i in range(10):
        log.append(WriteRecord(float(i), 0x65, (i,), b"x", ArmState.DISARMED, "refused", "r"))
    assert [r.words[0] for r in log] == [7, 8, 9]
    assert log.dropped_other == 7 and log.dropped == 7 and len(log) == 3
    assert log[-1].words == (9,) and [r.words[0] for r in log[:2]] == [7, 8]
    s = log.summary()
    assert s["dropped_other"] == 7 and s["records"] == 3


def test_laser_items_are_counted_on_the_laser_armed_path() -> None:
    """D13 §4: 'how many FIFO frames carried a laser record while it was held'."""
    fake = FakeClient()
    gate = SafeMccClient(fake, supervise=False, log_limits={"fifo_full_keep": 1})
    gate.arming.arm_motion()
    gate.arming.arm_laser(confirmed=True, job_token=gate.arming.begin_job())
    gate.read(1000, 36)
    gate.write(REG_FIFO_DATA, frame_words(1, duty=40))
    gate.write(REG_FIFO_DATA, frame_words(2, duty=40))
    first, second = [r for r in gate.write_log if r.addr == REG_FIFO_DATA]
    assert first.digest is not None and first.digest.words_crc32 == crc_of(frame_words(1, 40))
    assert second.stripped == 0 and first.stripped == 0
    assert first.laser_items == 99 and second.laser_items == 99
    assert second.digest is None and len(second.words) == 298


def _log_bytes(log: WriteLog) -> int:
    gc.collect()
    return tracemalloc.get_traced_memory()[0]


def test_write_log_memory_is_flat_over_a_long_stream_with_the_default_caps() -> None:
    """48 000 frames = 20 min at the 250 µs tick. The log's footprint must stop growing."""
    words = tuple(frame_words(0))
    frame = bytes(1206)
    tracemalloc.start()
    try:
        log = WriteLog()
        base = _log_bytes(log)
        at: dict[int, int] = {}
        for i in range(48_000):
            w = (i, *words[1:])
            log.append(
                WriteRecord(float(i), REG_FIFO_DATA, w, frame, ArmState.MOTION_ARMED, "sent", "f")
            )
            if i + 1 in (24_000, 48_000):
                at[i + 1] = _log_bytes(log) - base
    finally:
        tracemalloc.stop()
    print(f"write log: {at[24_000] / 1e6:.2f} MB at 24k frames, {at[48_000] / 1e6:.2f} MB at 48k")
    assert log.dropped_fifo == 48_000 - log.limits.max_fifo_digests - log.limits.fifo_full_keep
    assert at[48_000] < 16e6  # a list of full records would be ~580 MB
    assert at[48_000] - at[24_000] < 0.05 * at[24_000]


def test_default_limits_are_finite() -> None:
    lim = WriteLog().limits
    assert 0 < lim.fifo_full_keep <= 256 and 0 < lim.max_fifo_digests and 0 < lim.max_records


def test_daemon_no_longer_needs_to_trim_the_write_log() -> None:
    import inspect

    from nexcut.mccd import daemon

    assert "del self.gate.write_log" not in inspect.getsource(daemon)


# ---- the simulator's request list ---------------------------------------------------------------


def _client(sim: CardSimulator) -> McTransaction:
    return McTransaction(sim.address, policy=RetryPolicy(timeout_ms=200, send_times=2))


def test_simulator_request_list_is_a_ring_with_a_configurable_cap() -> None:
    with CardSimulator(config=SimConfig(request_log_limit=50)) as sim, _client(sim) as client:
        for _ in range(200):
            client.read(1000, 36)
        assert len(sim.requests) == 50
        assert sim.requests.dropped == 150
        seqs = [r.seq for r in sim.requests]
        assert seqs == list(range(150, 200))
        assert sim.requests[-1].seq == 199 and [r.seq for r in sim.requests[:2]] == [150, 151]


def test_simulator_request_list_default_is_bounded() -> None:
    assert SimConfig().request_log_limit is not None and SimConfig().request_log_limit > 0
    with CardSimulator(config=SimConfig(request_log_limit=None)) as sim, _client(sim) as client:
        for _ in range(30):
            client.read(1000, 36)
        assert len(sim.requests) == 30 and sim.requests.dropped == 0


def test_simulator_request_memory_is_flat_over_a_long_fifo_stream() -> None:
    raw = bytes(1206)
    vec = tuple(range(302))
    sim = CardSimulator(config=SimConfig(request_log_limit=1000))
    try:
        tracemalloc.start()
        try:
            at = {}
            for i in range(5000):
                sim._record(float(i), i & 0xFFFF, vec, raw, "replied")
                if i + 1 in (2000, 5000):
                    gc.collect()
                    at[i + 1] = tracemalloc.get_traced_memory()[0]
        finally:
            tracemalloc.stop()
    finally:
        sim._sock.close()
    assert len(sim.requests) == 1000 and sim.requests.dropped == 4000
    assert at[5000] - at[2000] < 0.05 * at[2000]
