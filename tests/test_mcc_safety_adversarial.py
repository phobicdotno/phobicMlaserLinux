"""Adversarial safety review of ``nexcut.mcc.safety`` (PORT-PLAN §8.2, 11-static-findings §3/§4/§5).

Each test is one attack from the review: a path from the public API to the transport that
would write a deny-listed register, assert a laser output / PWM duty while not LASER_ARMED,
jog without the deadman, leave the soft limits, or keep streaming after comm loss.
Tests marked ``xfail(strict=True)`` document a gap whose fix needs a design decision.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace

import pytest

from nexcut.mcc import commands as C
from nexcut.mcc.commands import Policy
from nexcut.mcc.dissector import parse_fifo_words
from nexcut.mcc.safety import (
    ArmState,
    SafeMccClient,
    SafetyConfig,
    SafetyViolation,
    classify_write,
)

LASER_DO_MASK = 0x100 | 0x10 | 0x20  # DO9, DO5, DO6 (SafetyConfig.laser_do_ports)


class Clock:
    """Manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class FakeClient:
    """Records what reaches the transport; block 1000 comes from ``status``."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, tuple[int, ...]]] = []
        self.next_seq = 1
        self.status = [0] * 36
        self.fail_reads = False

    def read(self, addr: int, count: int) -> list[int]:
        if self.fail_reads:
            raise TimeoutError("card silent")
        if addr == 1000:
            return self.status[:count]
        return [0] * count

    def write(self, addr: int, words: Sequence[int]) -> None:
        self.writes.append((addr, tuple(words)))
        self.next_seq += 1


def make(
    state: ArmState, cfg: SafetyConfig | None = None, **kw: object
) -> tuple[SafeMccClient, FakeClient, Clock]:
    fake, clock = FakeClient(), Clock()
    gate = SafeMccClient(fake, config=cfg, clock=clock, supervise=False, **kw)  # type: ignore[arg-type]
    if state is not ArmState.DISARMED:
        gate.arming.arm_motion()
    if state is ArmState.LASER_ARMED:
        gate.arming.arm_laser(confirmed=True, job_token=gate.arming.begin_job())
    gate.read(1000, 36)  # fresh status poll
    return gate, fake, clock


def laser_on_sent(fake: FakeClient) -> list[str]:
    """Every laser DO / PWM duty / tick duty that reached the transport."""
    hits: list[str] = []
    for addr, words in fake.writes:
        if addr == 0x65 and len(words) >= 4 and words[0] == 9999:
            if words[1] == 2 and words[3] & LASER_DO_MASK:
                hits.append(f"0x65 DO {words}")
            if words[1] in (3, 0x11) and any(words[3:]):
                hits.append(f"0x65 PWM {words}")
        if addr == 0x66:
            for item in parse_fifo_words(words, strict=False).items:
                a = item.args
                if item.opcode == 3000 and len(a) >= 2 and a[1] & 0xFFFF:
                    hits.append(f"tick duty {a}")
                if item.opcode == 9999 and a:
                    if a[0] == 2 and any(x & LASER_DO_MASK for x in a[2:]):
                        hits.append(f"FIFO DO {a}")
                    if a[0] in (3, 0x11) and any(a[2:]):
                        hits.append(f"FIFO PWM {a}")
    return hits


# ---- F1: FIFO dry-run stripping can be bypassed (11 §5.2 item grammar) ---------------------------------


@pytest.mark.parametrize(
    "items",
    [
        # DO9 on with a 4-word payload: len != 3 skipped the strip
        [0x0010270F, 2, 0x100, 0x100, 0],
        [0x000C270F, 2, 0, 0x100],  # DO9 value outside the mask
        [0x0010270F, 2, 0x104, 0x104, 0],  # DO3 + DO9 in one oversized record
        [0x000C0BB8, 0, (5000 << 16) | 50, 0],  # 3-arg tick: extra word the card may take as duty
    ],
)
def test_f1_dry_run_frame_cannot_carry_laser(items: list[int]) -> None:
    gate, fake, _ = make(ArmState.MOTION_ARMED)
    try:
        gate.write(0x66, [1, *items])
    except SafetyViolation:
        pass
    assert laser_on_sent(fake) == []
    for addr, words in fake.writes:  # anything sent must be canonical (11 §5.2)
        if addr == 0x66:
            for item in parse_fifo_words(words).items:
                assert not (item.opcode == 3000 and len(item.args) != 2)
                assert not (item.opcode == 9999 and len(item.args) != 3)


@pytest.mark.parametrize(
    "items",
    [
        [0x00000BB8],  # tick without payload -> IndexError instead of a refusal
        [0x00040BB8, 0],
        [0x000C270F, 16, 0, 0],  # 9999/16 (N6, DENY on 0x65) smuggled in-stream
        [0x000C270F, 5, 0, 0],
        [0x000C270F, 2, 0x8, 0x8],  # DO4 not in the allow-list (DENY on 0x65)
        [0x000C270F, 13, 0x1, 0x1],  # extended DO on (DENY on 0x65, 11 §3.3)
    ],
)
def test_f1_non_grammar_fifo_items_refused_and_logged(items: list[int]) -> None:
    gate, fake, _ = make(ArmState.MOTION_ARMED)
    with pytest.raises(SafetyViolation):
        gate.write(0x66, [1, *items])
    assert [a for a, _ in fake.writes] == []
    assert gate.write_log[-1].decision == "refused"


def test_f1_laser_armed_frame_errors_are_logged() -> None:
    gate, fake, _ = make(ArmState.LASER_ARMED)
    with pytest.raises(SafetyViolation):
        gate.write(0x66, [1, 0x00041234, 0])
    assert fake.writes == [] and gate.write_log[-1].decision == "refused"


def test_f1_in_stream_da_on_either_channel_word_stripped() -> None:
    """In-stream DA channel base is UNVERIFIED (A3 §6): both words count as laser power."""
    gate, fake, _ = make(ArmState.MOTION_ARMED)
    gate.write(0x66, [1, 0x000C270F, 4, 1, 5000, 0x000C270F, 4, 0, 5000])
    items = parse_fifo_words(fake.writes[-1][1]).items
    assert [i for i in items if i.opcode == 9999] == []


# ---- F2: TOCTOU between the gate decision and the socket ------------------------------------------------


class RacingLock:
    """Transport lock whose acquisition coincides with an E-stop from another thread."""

    def __init__(self, gate: SafeMccClient) -> None:
        self.gate = gate
        self.fired = False

    def __enter__(self) -> None:
        if not self.fired:
            self.fired = True
            self.gate.arming.estop()

    def __exit__(self, *exc: object) -> None:
        return None


def test_f2_estop_between_decision_and_send_blocks_laser_frame() -> None:
    gate, fake, _ = make(ArmState.LASER_ARMED)
    fake._lock = RacingLock(gate)  # type: ignore[attr-defined]
    frame = [1, 0x000C270F, 2, 0x100, 0x100, 0x00080BB8, 0, (5000 << 16) | 4]
    with pytest.raises(SafetyViolation):
        gate.write(0x66, frame)
    assert laser_on_sent(fake) == []


def test_f2_estop_between_decision_and_send_blocks_laser_do() -> None:
    gate, fake, _ = make(ArmState.LASER_ARMED)
    fake._lock = RacingLock(gate)  # type: ignore[attr-defined]
    with pytest.raises(SafetyViolation):
        gate.send(C.do_set(9, True))
    assert laser_on_sent(fake) == []


# ---- F3: laser frames queued in the card survive the laser disarm ---------------------------------------


def test_f3_fifo_start_after_stop_would_replay_queued_laser_frames() -> None:
    gate, fake, _ = make(ArmState.LASER_ARMED)
    gate.write(0x66, [1, 0x000C270F, 2, 0x100, 0x100, 0x00080BB8, 0, (5000 << 16) | 4])
    gate.write(0x67, [2])
    gate.stop()
    assert gate.arming.state is ArmState.MOTION_ARMED
    with pytest.raises(SafetyViolation, match="clear"):
        gate.write(0x67, [2])  # would resume the unstripped frames while only MOTION_ARMED
    gate.write(0x67, [1])  # clear
    gate.write(0x67, [2])


# ---- F4: laser DA channel would need only MOTION_ARMED once non-zero DA is enabled ---------------------


def test_f4_laser_da_channel_needs_laser_armed() -> None:
    cfg = replace(SafetyConfig(), allow_nonzero_da=True)
    assert classify_write(0x65, [9999, 4, 0, 5000], cfg).policy is Policy.LASER
    assert classify_write(0x65, [9999, 4, 1, 5000], cfg).policy is Policy.MOTION


# ---- F5: DO bulk write re-asserts gas outputs while DISARMED from a stale DO word -----------------------


def test_f5_bulk_do_cannot_keep_motion_outputs_on_while_disarmed() -> None:
    gate, fake, _ = make(ArmState.DISARMED)
    fake.status[5] = 0x4  # DO3 (high air) was on at the last poll
    gate.read(1000, 36)
    with pytest.raises(SafetyViolation):
        gate.write(0x65, [9999, 2, 0xFFFF, 0x4])
    gate.send(C.do_bulk_off(0x4, C.MachineParams().do_ports_off_on_stop))  # the stop form passes


# ---- F6/F7/F8: soft limits (PORT-PLAN §8.2; 01 §2.2 MAC/MAC_1.SoftLimitMaxLen 1371/950 mm) ------------


@pytest.mark.parametrize(
    "cmd",
    [
        C.go_to(2000.0, 10.0),
        C.go_to(10.0, 951.0),
        C.go_to(-1.0, 10.0),
        C.move_axis_absolute(0, 1372.0),
        C.move_axis_absolute(1, -5.0),
    ],
)
def test_f6_absolute_targets_outside_soft_limits_refused(cmd: C.CommandVector) -> None:
    gate, fake, _ = make(ArmState.MOTION_ARMED)
    gate.homed = True
    with pytest.raises(SafetyViolation, match="soft limit"):
        gate.send(cmd)
    assert fake.writes == []


def test_f6_absolute_targets_inside_soft_limits_pass() -> None:
    gate, fake, _ = make(ArmState.MOTION_ARMED)
    gate.homed = True
    gate.send(C.go_to(1371.0, 950.0))
    gate.send(C.move_axis_absolute(0, 0.0))
    assert len(fake.writes) == 2


def test_f7_relative_jog_beyond_soft_limit_refused_when_position_known() -> None:
    gate, fake, _ = make(ArmState.MOTION_ARMED)
    gate.homed = True
    gate.positions_word = {0: 1_300_000, 1: 10_000}
    with pytest.raises(SafetyViolation, match="soft limit"):
        gate.send(C.jog_step(0, 100.0))  # 1300 + 100 > 1371
    with pytest.raises(SafetyViolation, match="soft limit"):
        gate.send(C.jog_continuous(1, False))  # 10 - 4000 < 0
    gate.send(C.jog_continuous(0, True, soft_limit_remaining_mm=71.0))
    assert len(fake.writes) == 1


@pytest.mark.xfail(
    strict=True,
    reason="design decision: before homing the PC has no position, so a relative jog cannot "
    "be checked against SoftLimitMaxLen; the vendor allows un-homed jogs (needed to leave a "
    "limit switch). Options: refuse jogs longer than N mm until homed, or rely on hard limits.",
)
def test_f8_unhomed_continuous_jog_is_bounded_by_soft_limits() -> None:
    gate, fake, _ = make(ArmState.MOTION_ARMED)
    with pytest.raises(SafetyViolation):
        gate.send(C.jog_continuous(0, True))  # 4000 mm relative, position unknown


# ---- F9: deadman for continuous jog (PORT-PLAN §8.2: stop when the key event is > 200 ms stale) --------


def test_f9_continuous_jog_without_keepalive_is_stopped() -> None:
    gate, fake, clock = make(ArmState.MOTION_ARMED)
    gate.send(C.jog_continuous(0, True))
    clock.t += 0.15
    assert gate.jog_keepalive(0)
    clock.t += 0.15
    gate.read(1000, 36)
    gate.service()
    assert len(fake.writes) == 1 and fake.writes[0][1][0] == 3  # only the jog so far
    clock.t += 0.25
    gate.read(1000, 36)
    gate.service()
    assert fake.writes[-1][1][:2] == (1, 0x1)  # per-axis stop [1, 1<<slot, 2, vd, 10vd]
    assert not gate.jog_keepalive(0)


class FailingWriteClient(FakeClient):
    """Transport that raises after the frame left (reply lost)."""

    def write(self, addr: int, words: Sequence[int]) -> None:
        super().write(addr, words)
        if addr == 0x65 and words[0] == 3:
            raise TimeoutError("reply lost")


def test_f9_lease_registered_even_when_the_jog_reply_is_lost() -> None:
    fake, clock = FailingWriteClient(), Clock()
    gate = SafeMccClient(fake, clock=clock, supervise=False)
    gate.arming.arm_motion()
    with pytest.raises(TimeoutError):
        gate.send(C.jog_continuous(0, True))
    clock.t += 0.3
    gate.service()
    assert fake.writes[-1][1][:2] == (1, 0x1)


def test_f9_short_step_jog_needs_no_deadman() -> None:
    gate, fake, clock = make(ArmState.MOTION_ARMED)
    gate.send(C.jog_step(1, 5.0))  # 5 mm at 200 mm/s = 25 ms < 200 ms
    clock.t += 0.5
    gate.service()
    assert len(fake.writes) == 1


def test_f9_deadman_fires_from_the_supervisor_thread() -> None:
    fake = FakeClient()
    cfg = replace(SafetyConfig(), deadman_timeout_s=0.05)
    gate = SafeMccClient(fake, config=cfg)
    try:
        gate.arming.arm_motion()
        gate.read(1000, 36)
        gate.send(C.jog_continuous(0, False))
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(fake.writes) < 2:
            time.sleep(0.01)
        assert len(fake.writes) >= 2 and fake.writes[1][1][:2] == (1, 0x1)
    finally:
        gate.close()


# ---- F10: comm loss > 1 s (PORT-PLAN §8.2 watchdog) -----------------------------------------------------


def test_f10_no_streaming_after_status_poll_stale() -> None:
    gate, fake, clock = make(ArmState.LASER_ARMED)
    gate.write(0x67, [2])
    gate.write(0x66, [1, 0x00080BB8, 0, (5000 << 16) | 4])
    fake.fail_reads = True
    clock.t += 1.2
    with pytest.raises(TimeoutError):
        gate.read(1000, 36)
    n = len(fake.writes)
    with pytest.raises(SafetyViolation, match="stale"):
        gate.write(0x66, [2, 0x00080BB8, 0, (5000 << 16) | 4])
    assert all(a != 0x66 for a, _ in fake.writes[n:])
    assert gate.arming.state is ArmState.DISARMED
    assert (0x67, (3,)) in fake.writes[n:]


def test_f10_service_stops_fifo_and_disarms_on_comm_loss() -> None:
    gate, fake, clock = make(ArmState.LASER_ARMED)
    gate.write(0x67, [2])
    clock.t += 1.05
    gate.service()
    assert fake.writes[-1] == (0x67, (3,))
    assert gate.arming.state is ArmState.DISARMED and not gate.fifo_running


def test_f10_stream_refused_without_any_status_poll() -> None:
    fake = FakeClient()
    gate = SafeMccClient(fake, supervise=False)
    gate.arming.arm_motion()
    with pytest.raises(SafetyViolation, match="stale"):
        gate.write(0x67, [2])
    assert fake.writes == []


# ---- F11: card alarms (11 §4.2/§4.3/§4.7) ---------------------------------------------------------------


def test_f11_alarm_word_disarms_stops_and_blocks_motion() -> None:
    gate, fake, _ = make(ArmState.MOTION_ARMED)
    fake.status[6] = 1 << 25  # bus fault
    gate.read(1000, 36)
    assert gate.arming.state is ArmState.DISARMED
    assert fake.writes and fake.writes[0][1][:2] == (1, 0x1F)
    gate.arming.arm_motion()  # operator re-arms while the alarm is still active
    with pytest.raises(SafetyViolation, match="alarm"):
        gate.send(C.jog_step(0, 1.0))
    gate.send(C.stop_all())  # safety primitives still pass


def test_f11_estop_bit_latches() -> None:
    gate, fake, _ = make(ArmState.LASER_ARMED)
    fake.status[6] = 1 << 30
    gate.read(1000, 36)
    assert gate.arming.estop_latched and gate.arming.state is ArmState.DISARMED


def test_f11_restart_required_blocks_motion() -> None:
    gate, fake, _ = make(ArmState.MOTION_ARMED)
    fake.status[31] = 1
    gate.read(1000, 36)
    with pytest.raises(SafetyViolation, match="restart"):
        gate.send(C.home_axis(0))


def test_f11_follower_flag_alone_is_not_an_alarm_when_idle() -> None:
    gate, fake, _ = make(ArmState.MOTION_ARMED)
    fake.status[6] = 0x01000000
    gate.read(1000, 36)
    assert gate.arming.state is ArmState.MOTION_ARMED
    gate.send(C.jog_step(0, 1.0))


# ---- F12: the gate is bypassable in-process -------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="design decision: SafeMccClient.client (and McTransaction.transact, which can send "
    "func 0x26 or any register) is reachable from the same process; the enforcement boundary "
    "must be the mccd process owning the socket with the UI talking IPC (PORT-PLAN §2.3).",
)
def test_f12_raw_transport_not_reachable_through_the_gate() -> None:
    gate, fake, _ = make(ArmState.DISARMED)
    raw = getattr(gate, "client", None)
    if raw is not None:
        raw.write(150, [5555])
    assert all(a != 150 for a, _ in fake.writes)
