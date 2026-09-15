"""Transaction engine vs. simulator over loopback (04 §3.1-§3.3, 08 §2.1-§2.3, §10)."""

from __future__ import annotations

import errno
import socket
import threading
import time
from collections.abc import Iterator

import pytest

from nexcut.mcc.dissector import parse_log_line
from nexcut.mcc.framing import FUNC_READ, build_frame, read_reply
from nexcut.mcc.simulator import (
    RO_INPUTS,
    RO_PROGRAM_VERSION,
    CardSimulator,
)
from nexcut.mcc.transaction import (
    CardBusy,
    CardNotReady,
    CardSilent,
    CardUnreachable,
    LinkState,
    LogEvent,
    McTransaction,
    ReplyDecodeFailed,
    RequestEncodeFailed,
    RetryPolicy,
    SocketInvalid,
    StaleRepliesOnly,
    UnexpectedReply,
    error_for,
    format_log_line,
)

FAST = RetryPolicy(timeout_ms=80, send_times=3, recv_times=3, send_interval_ms=1)
"""The shipped ladder shape (3 sends x 3 recv tries) with a short timeout for speed."""
FULL_LADDER = [(1, 1), (1, 2), (2, 2), (3, 2), (1, 3), (2, 3), (3, 3)]
"""08 §2.3: Try-Times(R/S) labels of every complete timeout ladder (138 of 138)."""


@pytest.fixture
def sim() -> Iterator[CardSimulator]:
    with CardSimulator() as s:
        yield s


class Recorder:
    def __init__(self) -> None:
        self.events: list[LogEvent] = []

    def __call__(self, ev: LogEvent) -> None:
        self.events.append(ev)

    def labels(self, stage: str | None = None) -> list[tuple[int, int]]:
        return [(e.recv_try, e.send_try) for e in self.events if stage in (None, e.stage)]


def make_client(
    sim: CardSimulator, policy: RetryPolicy = FAST, **kw: object
) -> tuple[McTransaction, Recorder]:
    rec = Recorder()
    client = McTransaction(sim.address, policy=policy, on_log=rec, **kw)  # type: ignore[arg-type]
    return client, rec


# --- policy (setTimeout 0x10006570) --------------------------------------------------------------


def test_policy_defaults_follow_shipped_ini() -> None:
    idle = RetryPolicy.idle()
    assert (idle.timeout_ms, idle.send_times, idle.recv_times, idle.send_interval_ms) == (
        500,
        3,
        3,
        1,
    )
    # 7 select timeouts x 500 ms + 3 x Sleep(1) -> the logged 3.5 s ladder (08 §2.3)
    assert idle.worst_case_s == pytest.approx(3.503)
    st = RetryPolicy.streaming()
    assert (st.timeout_ms, st.send_times, st.recv_times) == (600, 1, 1)
    # send_times = MCFifoTime // timeout: 1600 // 600 = 2
    assert RetryPolicy.idle(mc_timeout_ms=600).send_times == 2
    with pytest.raises(ValueError):
        RetryPolicy(timeout_ms=0)


# --- happy path -----------------------------------------------------------------------------------


def test_startup_reads_and_seq_counter(sim: CardSimulator) -> None:
    client, rec = make_client(sim)
    with client:
        assert client.next_seq == 0
        words = client.read(1000, 2)
        assert words[RO_PROGRAM_VERSION - 1000] == 20152
        assert len(client.read(1000, 36)) == 36
        assert len(client.read(50000, 26)) == 26
        assert len(client.read(10000, 18)) == 18
        assert len(client.read(60001, 120)) == 120
        assert client.next_seq == 5
    assert [r.seq for r in sim.requests] == [0, 1, 2, 3, 4]
    # first datagram of a session is byte-identical to the logged one (08 §2.2)
    assert sim.requests[0].raw.hex(" ") == "00 00 8c 75 00 08 00 30 e8 03 00 00 02 00"
    assert rec.events == []


def test_reopen_resets_seq(sim: CardSimulator) -> None:
    client, _ = make_client(sim)
    with client:
        client.read(1000, 2)
        client.read(1000, 2)
        client.open()
        client.read(1000, 2)
    assert [r.seq for r in sim.requests] == [0, 1, 0]


# --- ladder ---------------------------------------------------------------------------------------


def test_full_silent_ladder_real_timing(sim: CardSimulator) -> None:
    """Shipped policy: 7 x 500 ms select timeouts, 3 datagrams, then 10060 (08 §2.3)."""
    sim.drop_requests(100)
    client, rec = make_client(sim, RetryPolicy.idle())
    t0 = time.monotonic()
    with client, pytest.raises(CardSilent) as ei:
        client.read(1000, 2)
    elapsed = time.monotonic() - t0
    assert 3.45 <= elapsed <= 4.3
    assert rec.labels("RecvErr_selectFunc") == FULL_LADDER
    assert all(e.errcode == 10060 for e in rec.events)
    assert ei.value.errcode == 10060 and ei.value.state is LinkState.SILENT
    assert (ei.value.recv_try, ei.value.send_try) == (3, 3)
    assert [r.action for r in sim.requests] == ["dropped"] * 3
    assert len({r.raw for r in sim.requests}) == 1  # resends are the same buffer
    gaps = [b.monotonic - a.monotonic for a, b in zip(rec.events, rec.events[1:], strict=False)]
    assert all(0.49 <= g <= 0.75 for g in gaps)


def test_ladder_gaps_scale_with_timeout(sim: CardSimulator) -> None:
    sim.drop_requests(100)
    client, rec = make_client(sim)
    with client, pytest.raises(CardSilent):
        client.write(0x65, [9999, 2, 1, 1])
    assert rec.labels() == FULL_LADDER
    gaps = [b.monotonic - a.monotonic for a, b in zip(rec.events, rec.events[1:], strict=False)]
    assert all(0.075 <= g <= 0.3 for g in gaps)
    # the request vector is carried in DataEx like the Windows log
    assert rec.events[0].dataex == (0x40, 0x65, 4, 9999, 2, 1, 1)


@pytest.mark.parametrize(
    ("drops", "labels"),
    [
        (1, [(1, 1)]),
        (2, [(1, 1), (1, 2), (2, 2), (3, 2)]),  # 08 §2.3 "x4 then success"
    ],
)
def test_success_on_later_send(
    sim: CardSimulator, drops: int, labels: list[tuple[int, int]]
) -> None:
    sim.drop_requests(drops)
    client, rec = make_client(sim)
    with client:
        assert len(client.read(1000, 36)) == 36
    assert rec.labels() == labels
    assert [r.action for r in sim.requests] == ["dropped"] * drops + ["replied"]


def test_lost_reply_resend_is_same_seq(sim: CardSimulator) -> None:
    sim.drop_replies(1)
    client, rec = make_client(sim)
    with client:
        client.read(1000, 2)
    assert [r.seq for r in sim.requests] == [0, 0]
    assert rec.labels() == [(1, 1)]


def test_streaming_mode_single_try_then_restore(sim: CardSimulator) -> None:
    sim.drop_requests(100)
    client, rec = make_client(
        sim, streaming_policy=RetryPolicy(timeout_ms=120, send_times=1, recv_times=1)
    )
    with client:
        with client.streaming():
            t0 = time.monotonic()
            with pytest.raises(CardSilent):
                client.read(1000, 36)
            assert 0.11 <= time.monotonic() - t0 <= 0.4
        assert rec.labels() == [(1, 1)]  # 08 §2.3: 43 single-try bursts
        assert client.policy == FAST
        rec.events.clear()
        with pytest.raises(CardSilent):
            client.read(1000, 36)
        assert rec.labels() == FULL_LADDER
    assert len(sim.requests) == 1 + 3


# --- stale sequence (decodeWithSeq rc 4) ----------------------------------------------------------


def test_late_reply_of_previous_transaction_is_skipped(sim: CardSimulator) -> None:
    client, rec = make_client(
        sim, streaming_policy=RetryPolicy(timeout_ms=60, send_times=1, recv_times=1)
    )
    with client:
        sim.delay_replies(0.15, count=1)
        with client.streaming(), pytest.raises(CardSilent):
            client.read(1000, 2)  # seq 0, reply arrives late
        time.sleep(0.2)  # the late reply is now queued in the client's socket
        # seq 1: the pre-transaction flush (0x10006280) discards the queued seq-0 reply,
        # so nothing stale is decoded (see test_mcc_protocol_fidelity F1).
        words = client.read(1000, 36)
        assert len(words) == 36
    stages = [(e.stage, e.errcode, e.recv_try, e.send_try) for e in rec.events]
    assert stages == [("RecvErr_selectFunc", 10060, 1, 1)]


def test_injected_stale_datagram_consumes_a_recv_try(sim: CardSimulator) -> None:
    sim.send_stale(1)
    client, rec = make_client(sim)
    with client:
        client.read(1000, 2)
    assert rec.labels("DecodeRecvData") == [(1, 1)]


def test_only_stale_replies_is_silent_2004(sim: CardSimulator) -> None:
    sim.override_next_reply(lambda seq: read_reply(seq + 7, 1000, [0, 0]))
    client, rec = make_client(sim, RetryPolicy(timeout_ms=60, send_times=1, recv_times=1))
    with client, pytest.raises(StaleRepliesOnly) as ei:
        client.read(1000, 2)
    assert ei.value.errcode == 2004 and isinstance(ei.value, CardSilent)


def test_foreign_source_is_ignored(sim: CardSimulator) -> None:
    client, rec = make_client(sim, RetryPolicy(timeout_ms=400, send_times=1, recv_times=3))
    with client:
        client.read(1000, 2)  # binds the ephemeral port
        port = client._sock.getsockname()[1]  # type: ignore[union-attr]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as intruder:
            seq = client.next_seq
            # The intruder must arrive *during* the transaction: a datagram queued before
            # it is removed by the pre-transaction flush (0x10006280).
            sim.delay_replies(0.15, count=1)
            t = threading.Timer(
                0.03,
                lambda: intruder.sendto(read_reply(seq, 1000, [1, 2]), ("127.0.0.1", port)),
            )
            t.start()
            words = client.read(1000, 2)
            t.join()
    assert words[1] == 20152  # the real card answer, not the intruder's [1, 2]
    assert rec.labels("DecodeRecvData") == [(1, 1)]


# --- exceptions -----------------------------------------------------------------------------------


def test_not_ready_after_reboot(sim: CardSimulator) -> None:
    client, rec = make_client(sim)
    with client:
        t0 = time.monotonic()
        sim.reboot(silent_s=0.7, not_ready_s=0.6)
        with pytest.raises(CardSilent):
            client.read(1000, 36)  # full 7-rung ladder = 0.56 s, all inside the silent window
        time.sleep(max(0.0, t0 + 0.8 - time.monotonic()))
        with pytest.raises(CardNotReady) as ei:
            client.read(1000, 2)
        assert ei.value.errcode == 502 and ei.value.exception_code == 2
        assert ei.value.state is LinkState.REFUSING
        ev = rec.events[-1]
        assert ev.stage == "RecvDataErr" and (ev.recv_try, ev.send_try) == (1, 1)
        assert ev.data == (0xB0, 2)  # 08 §2.1: Data:000000b0 00000002
        assert ev.buffer[2:].hex(" ") == "d0 45 00 03 00 b0 02"  # 08 §2.2 reply frame
        time.sleep(max(0.0, t0 + 1.4 - time.monotonic()))
        assert client.read(1000, 2)[1] == 20152


def test_exception_mid_ladder_terminates(sim: CardSimulator) -> None:
    """08 §2.1: 502 logged at 1/2 or 1/3 ends the transaction at that rung."""
    sim.reboot(silent_s=0.0, not_ready_s=5.0)
    sim.drop_requests(1)
    client, rec = make_client(sim)
    with client, pytest.raises(CardNotReady) as ei:
        client.read(1000, 2)
    assert rec.labels() == [(1, 1), (1, 2)]
    assert (ei.value.recv_try, ei.value.send_try) == (1, 2)
    assert len(sim.requests) == 2


def test_jog_while_moving_is_busy(sim: CardSimulator) -> None:
    client, rec = make_client(sim)
    jog = [3, 1, 50000, 5999, 59990, 4000000]  # 04 §3.6 logged jog vector
    with client:
        client.write(0x65, jog)
        with pytest.raises(CardBusy) as ei:
            client.write(0x65, jog)
    assert ei.value.errcode == 503
    assert rec.events[-1].data == (0xC0, 3)
    assert rec.events[-1].buffer[2:].hex(" ") == "34 45 00 03 00 c0 03"  # 08 §2.2


def test_unexpected_reply_603(sim: CardSimulator) -> None:
    sim.override_next_reply(lambda seq: read_reply(seq, 2000, [0, 0]))
    client, _ = make_client(sim)
    with client, pytest.raises(UnexpectedReply) as ei:
        client.read(1000, 2)
    assert ei.value.errcode == 603 and ei.value.state is LinkState.PROTOCOL


def test_short_or_inconsistent_reply_aborts(sim: CardSimulator) -> None:
    sim.override_next_reply(lambda seq: bytes((seq >> 8, seq & 0xFF, 0, 0, 0)))
    client, _ = make_client(sim)
    with client:
        with pytest.raises(ReplyDecodeFailed) as ei:
            client.read(1000, 2)
        assert ei.value.errcode == 2002
        sim.override_next_reply(
            lambda seq: build_frame(seq, FUNC_READ, bytes(6) + b"\x01\x00\x00")  # count 0, 3 bytes
        )
        with pytest.raises(ReplyDecodeFailed) as ei:
            client.read(1000, 2)
        assert ei.value.errcode == 2005
    assert len(sim.requests) == 2  # no retries


def test_encode_error() -> None:
    rec = Recorder()
    with McTransaction(("127.0.0.1", 9), on_log=rec) as client:
        with pytest.raises(RequestEncodeFailed) as ei:
            client.transact([0x40, 0x65, 3, 1])  # count word 3 but one data word
    assert ei.value.errcode == 1002
    assert rec.events[0].stage == "Encode"


class _FailingSock:
    """Wraps a real socket; ``sendto`` raises the queued errors first."""

    def __init__(self, real: socket.socket, errors: list[int]) -> None:
        self.real = real
        self.errors = errors
        self.sends = 0

    def sendto(self, data: bytes, addr: tuple[str, int]) -> int:
        self.sends += 1
        if self.errors:
            raise OSError(self.errors.pop(0), "injected")
        return self.real.sendto(data, addr)

    def __getattr__(self, name: str) -> object:
        return getattr(self.real, name)


def test_unreachable_is_not_retried(sim: CardSimulator) -> None:
    client, rec = make_client(sim)
    with client:
        fake = _FailingSock(client._sock, [errno.EHOSTUNREACH] * 5)  # type: ignore[arg-type]
        client._sock = fake  # type: ignore[assignment]
        with pytest.raises(CardUnreachable) as ei:
            client.read(1000, 2)
        client._sock = fake.real
    assert fake.sends == 1
    assert ei.value.errcode == 10065 and ei.value.state is LinkState.UNREACHABLE
    ev = rec.events[0]
    assert (ev.stage, ev.recv_try, ev.send_try) == ("Sendto", 0, 1)  # 08 §2.1 template 4
    assert ev.data == (0x30, 1000, 2)
    line = format_log_line(ev)
    assert line == (
        "MC-Sendto ErrCode:10065 Try-Times(R/S):0/1 Data:00000030 000003e8 00000002 "
        "Buffer:00 00 8c 75 00 08 00 30 e8 03 00 00 02 00"
    )


def test_transient_send_error_retries_without_recv(sim: CardSimulator) -> None:
    client, rec = make_client(sim)
    with client:
        fake = _FailingSock(client._sock, [errno.ECONNRESET])  # type: ignore[arg-type]
        client._sock = fake  # type: ignore[assignment]
        assert client.read(1000, 2)[1] == 20152
    assert fake.sends == 2
    assert [(e.stage, e.errcode, e.recv_try, e.send_try) for e in rec.events] == [
        ("Sendto", 10054, 0, 1)
    ]


def test_socket_closed_during_ladder(sim: CardSimulator) -> None:
    sim.drop_requests(100)
    client, rec = make_client(sim)
    threading.Timer(0.12, client.close).start()
    with pytest.raises(SocketInvalid) as ei:
        client.read(1000, 2)
    assert ei.value.errcode == 10038 and ei.value.stage == "Sendto"


def test_error_taxonomy() -> None:
    cases = {
        10065: (CardUnreachable, LinkState.UNREACHABLE),
        10051: (CardUnreachable, LinkState.UNREACHABLE),
        10060: (CardSilent, LinkState.SILENT),
        502: (CardNotReady, LinkState.REFUSING),
        503: (CardBusy, LinkState.REFUSING),
        603: (UnexpectedReply, LinkState.PROTOCOL),
        10038: (SocketInvalid, LinkState.LOCAL),
    }
    for code, (cls, state) in cases.items():
        err = error_for(code, "X")
        assert type(err) is cls and err.state is state and err.errcode == code
    assert error_for(9999, "RecvErr_selectFunc").state is LinkState.LOCAL


def test_log_line_round_trips_through_dissector() -> None:
    ev = LogEvent(
        "RecvDataErr",
        503,
        1,
        1,
        data=(0xC0, 3),
        buffer=bytes.fromhex("1d3f3445000300c003"),
        dataex=(0x40, 0x65, 6, 3, 1, 50000, 5999, 59990, 4000000),
    )
    line = format_log_line(ev)
    assert line.endswith("DataEx:40 65 6 3 1 c350 176f ea56 3d0900")
    rec = parse_log_line(f"2025-07-07 09:13:10.000 [Info] -> {line}")
    assert rec is not None and rec.kind == "mc"
    assert (rec.stage, rec.errcode, rec.recv_try, rec.send_try) == ("RecvDataErr", 503, 1, 1)
    assert rec.data == (0xC0, 3) and rec.dataex == ev.dataex and rec.buffer == ev.buffer


# --- simulator state -------------------------------------------------------------------------------


def test_deaf_block_does_not_affect_status_poll(sim: CardSimulator) -> None:
    sim.deaf_addresses.add(10000)  # 08 §4.3
    client, _ = make_client(sim, RetryPolicy(timeout_ms=50, send_times=1, recv_times=1))
    with client:
        with pytest.raises(CardSilent):
            client.read(10000, 18)
        assert len(client.read(1000, 36)) == 36


def test_inputs_outputs_alarms(sim: CardSimulator) -> None:
    client, _ = make_client(sim)
    with client:
        sim.set_inputs(0b1010)
        sim.inject_alarm(alarm1=0x80, alarm2=0x1)
        words = client.read(1000, 36)
        assert words[RO_INPUTS - 1000] == 0b1010
        assert words[6:8] == [0x80, 0x1]
        client.write(0x65, [9999, 2, 1, 1])  # 08 §4.2 set output 1 on
        assert sim.outputs == 1 << 0  # [9999,2,mask,value]: mask 1 = DO1 = bit 0 (A3 §6)
        client.write(0x65, [9999, 2, 1, 0])
        assert sim.outputs == 0


def test_unknown_block_read_is_refused(sim: CardSimulator) -> None:
    client, _ = make_client(sim)
    with client, pytest.raises(CardNotReady):
        client.read(1050, 3)
