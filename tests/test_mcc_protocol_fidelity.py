"""Adversarial protocol-fidelity review of ``nexcut.mcc`` (lens: bytes on the wire).

Each test pins one deviation found by comparing ``src/nexcut/mcc/*`` with the vendor
driver ``Module/NCModule.dll`` / ``MainApp.exe`` (disassembly in ``.scratch/asm``) and
with 04 §3, 08 §2-4 and 11-static-findings. Golden values are copied from the binaries;
nothing under SRC is read here.
"""

from __future__ import annotations

import select
import socket
import time
from collections.abc import Iterator

import pytest

from nexcut.mcc.commands import MachineParams, lift_table_jog, pwm_set, round_half_away
from nexcut.mcc.framing import encode_vector
from nexcut.mcc.simulator import CardSimulator
from nexcut.mcc.transaction import (
    CardSilent,
    McTransaction,
    RequestSuppressed,
    RetryPolicy,
    StaleRepliesOnly,
    hal_suppresses,
)

SINGLE = RetryPolicy(timeout_ms=60, send_times=1, recv_times=1, send_interval_ms=1)


@pytest.fixture
def sim() -> Iterator[CardSimulator]:
    with CardSimulator() as s:
        yield s


# --- F1: pre-transaction flush (NCModule 0x1001d6d9 -> 0x10006280) ---------------------------------
# 0x10006280: select(1, {sock}, 0, 0, {0,0}); if readable: recv(sock, buf, 100, 0) -- exactly one
# datagram is discarded inside the critical section, before the seq number is taken.


def test_queued_late_reply_is_flushed_before_send(sim: CardSimulator) -> None:
    """A late reply sitting in the socket must not eat the single receive try (streaming)."""
    with McTransaction(sim.address, policy=SINGLE) as client:
        sim.delay_replies(0.15, count=1)
        with pytest.raises(CardSilent):
            client.read(1000, 2)  # seq 0; reply arrives after the ladder
        time.sleep(0.25)  # the seq-0 reply is now queued
        # DLL: flush drops it, seq 1 is answered on the first (only) recv try.
        assert len(client.read(1000, 36)) == 36


def test_flush_discards_only_one_datagram(sim: CardSimulator) -> None:
    """The vendor flush is a single recv(); a second queued datagram still costs a try."""
    with McTransaction(sim.address, policy=SINGLE) as client:
        sim.delay_replies(0.15, count=2)
        for _ in range(2):
            with pytest.raises(CardSilent):
                client.read(1000, 2)
        time.sleep(0.35)  # seq-0 and seq-1 replies queued
        with pytest.raises(StaleRepliesOnly):
            client.read(1000, 36)


# --- F2: HAL address windows (readReg 0x100225e0, writeReg 0x10022760) -----------------------------


@pytest.mark.parametrize(
    ("func", "addr", "suppressed"),
    [
        (0x30, 104, False),
        (0x30, 105, True),
        (0x30, 150, False),
        (0x30, 151, False),
        (0x30, 999, True),
        (0x30, 1000, False),
        (0x30, 1100, False),
        (0x30, 1101, True),
        (0x30, 2000, False),
        (0x30, 3001, True),
        (0x30, 5008, False),
        (0x30, 5009, True),
        (0x30, 6000, False),
        (0x30, 51001, True),
        (0x30, 59000, False),
        (0x40, 0x65, False),
        (0x40, 0x67, False),
        (0x40, 104, False),
        (0x40, 105, True),
        (0x40, 150, False),
        (0x40, 1000, True),
        (0x40, 4999, True),
        (0x40, 5000, False),
        (0x40, 5009, True),
        (0x40, 6000, False),
        (0x40, 51001, True),
        (0x40, 59000, False),
        (0x26, 1000, False),
    ],
)
def test_hal_window_table(func: int, addr: int, suppressed: bool) -> None:
    assert hal_suppresses(func, addr) is suppressed


def test_suppressed_read_never_reaches_the_wire() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as card:
        card.bind(("127.0.0.1", 0))
        with McTransaction(card.getsockname(), policy=SINGLE) as client:
            with pytest.raises(RequestSuppressed):
                client.read(1500, 1)  # vendor readReg returns zeros without sending
            with pytest.raises(RequestSuppressed):
                client.write(1000, [1])  # vendor writeReg returns success without sending
            assert client.next_seq == 0  # no sequence number consumed (filter precedes 0x1001d6a0)
        ready, _, _ = select.select([card], [], [], 0.1)
        assert not ready


# --- F3: encoder dispatch on the full function word (encodeWithSeq 0x100282b0..0x100282cb) ----------
# edi = v[0] - 0x20; cmp edi, 0x20; ja default  -> the whole int selects the case, not its low byte.


@pytest.mark.parametrize("func", [0x130, 0x140, -0xD0, 0x7FFF_FF30])
def test_out_of_range_func_word_takes_default_case(func: int) -> None:
    assert encode_vector([func, 1000, 2], 0x1234) == b"\x12\x34\x00\x00"


# --- F4: MainApp rounding helper 0x4511d0 = trunc(x + 0.5*sign(x)) (lift-table jog, A1 §3.2) --------


@pytest.mark.parametrize(
    ("x", "expected"),
    [(2.5, 3), (-2.5, -3), (0.5, 1), (1.5, 2), (2.4999, 2), (0.0, 0), (1_000_000.0, 1_000_000)],
)
def test_round_half_away(x: float, expected: int) -> None:
    assert round_half_away(x) == expected


def test_lift_table_step_uses_half_away_rounding() -> None:
    # StepLength 0.0025 mm * K 1000 = 2.5 um: MainApp sends 3 (Python round() would give 2).
    p = MachineParams(is_step_move=True, step_length=0.0025)
    assert lift_table_jog(True, p).words[5] == 3
    assert lift_table_jog(False, p).signed_words[5] == -3
    # speed: min(50.0005, 100) * 1000 = 50000.5 -> 50001
    p2 = MachineParams(is_fast_mode=False, jog_slow_speed=50.0005)
    assert lift_table_jog(True, p2).words[2] == 50001


# --- F5: 0x65 PWM set form is evidenced (NC 0x100303a0..0x10030529) ---------------------------------


def test_pwm_set_four_word_form_is_evidenced() -> None:
    cmd = pwm_set(5000, 4)
    assert cmd.words == (9999, 0x11, 5000, 4)
    assert not any("not seen" in u for u in cmd.unverified)
