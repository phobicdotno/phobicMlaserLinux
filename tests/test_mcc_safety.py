"""Safety gate: allow/deny lists (11 §3), laser stripping, arming state machine (PORT-PLAN §8.2)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import replace

import pytest

from nexcut.mcc import commands as C
from nexcut.mcc.commands import MachineParams, Policy
from nexcut.mcc.dissector import parse_fifo_words
from nexcut.mcc.framing import encode_vector
from nexcut.mcc.safety import (
    ArmingError,
    ArmingStateMachine,
    ArmState,
    SafeMccClient,
    SafetyViolation,
    classify_read,
    classify_write,
    strip_laser_records,
)
from nexcut.mcc.simulator import CardSimulator, SimConfig
from nexcut.mcc.transaction import McTransaction, RetryPolicy


class FakeClient:
    """Records what reaches the transport."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, tuple[int, ...]]] = []
        self.reads: list[tuple[int, int]] = []
        self.next_seq = 7
        self.status = [0] * 36

    def read(self, addr: int, count: int) -> list[int]:
        self.reads.append((addr, count))
        if addr == 1000:
            return self.status[:count]
        return [0] * count

    def write(self, addr: int, words: Sequence[int]) -> None:
        self.writes.append((addr, tuple(words)))
        self.next_seq += 1


def armed(state: ArmState) -> tuple[SafeMccClient, FakeClient]:
    fake = FakeClient()
    gate = SafeMccClient(fake, supervise=False)
    if state is not ArmState.DISARMED:
        gate.arming.arm_motion()
    if state is ArmState.LASER_ARMED:
        token = gate.arming.begin_job()
        gate.arming.arm_laser(confirmed=True, job_token=token)
    return gate, fake


# ---- deny list (11 §3.3) -----------------------------------------------------------------------------

DENIED_WRITES: list[tuple[int, list[int]]] = [
    (150, [5555]),  # FTC factory reset
    (150, [9999]),
    (151, [1]),
    (59500, [1]),  # licence verify
    (59502, [0]),  # card RTC
    (59511, [1]),
    (59600, [1]),  # hardware parameters
    (59600 + 0xD0 * 3, [1]),
    (11000, [5]),  # FTC property
    (11002, [5]),
    (100, [9999]),
    (5001, [9999, 9, 0xFFFF, 0]),
    (5008, [3]),
    (5012, [2]),
    (200, [0]),
    (201, [0]),
    (0x65, [102]),
    (0x65, [107]),
    (0x65, [117, 1, 2, 3, 4, 5, 6, 7, 8]),
    (0x65, [118, 4, 0, 35]),
    (0x65, [118, 5, 0]),
    (0x65, [103, 1000, 0]),
    (0x65, [104, 1000, 2000]),
    (0x65, [109, 1000, 0]),
    (0x65, [9999, 13, 0xFFFF, 0]),  # old "handshake" (11 §2.1)
    (0x65, [9999, 16]),
    (0x65, [9999, 1, 0, 0]),
    (0x65, [9999, 9, 0xFFFF, 0]),
    (0x65, [7]),
    (0x65, [4, 1]),
    (0x65, [1, 8]),
    (0x65, [2, 0x1F, 0]),  # system home
    (0x65, [2, 0x10, 0]),  # lift-table home
    (0x65, [2, 3, 0]),
    (0x65, [3, 4, 50000, 4000, 40000, 1000000]),  # lift table jog
    (0x65, [3, 0x80000010, 100000, 4000, 40000, 0]),  # roll feeder
    (0x65, [3, 0, 50000, 5999, 59990, 5_000_000]),  # beyond the jog length bound
    (0x65, [3, 0, 50000, 5999, 50000, 5000]),  # jerk != 10 a
    (0x65, [1, 0x1F, 2, 1, 10]),  # vd outside [2000, 100000]
    (0x65, [9999, 4, 1, 3000]),  # non-zero DA in M1
    (0x65, [9999, 2, 0x8, 0x8]),  # DO4 not in the allow-list
    (0x65, [9999, 2, 0x3, 0x3]),  # two ports at once
    (0x67, [4]),
    (0x1234, [0]),
]


@pytest.mark.parametrize(("addr", "words"), DENIED_WRITES)
def test_deny_listed_writes_refused_even_laser_armed(addr: int, words: list[int]) -> None:
    gate, fake = armed(ArmState.LASER_ARMED)
    gate.homed = True
    with pytest.raises(SafetyViolation):
        gate.write(addr, words)
    assert fake.writes == []
    assert gate.write_log[-1].decision == "refused" and gate.write_log[-1].frame == b""


@pytest.mark.parametrize(
    ("addr", "count"),
    [(59500, 12), (59590, 20), (59490, 20), (151, 1), (150, 4), (200, 1), (3000, 1), (1000, 37)],
)
def test_denied_reads(addr: int, count: int) -> None:
    gate, fake = armed(ArmState.LASER_ARMED)
    with pytest.raises(SafetyViolation):
        gate.read(addr, count)
    assert fake.reads == []


@pytest.mark.parametrize(
    ("addr", "count"),
    [
        (1000, 2),
        (1000, 36),
        (1050, 3),
        (2000, 50),
        (5000, 9),
        (10000, 18),
        (11000, 39),
        (50000, 26),
        (50200, 100),
        (60001, 120),
        (59600, 52),
        (59600 + 0xD0 * 31, 52),
        (12002, 800),
    ],
)
def test_allowed_reads(addr: int, count: int) -> None:
    assert classify_read(addr, count).policy is Policy.ALWAYS


# ---- allow list per arming state ------------------------------------------------------------------------


def test_disarmed_allows_only_safety_primitives() -> None:
    gate, fake = armed(ArmState.DISARMED)
    for cmd in (
        C.stop_all(200000),
        C.stop_all_no_decel(),
        C.jog_release_stop([0], 200000),
        C.zf_stop(),
        C.fifo_stop(),
        C.connect_prologue(),
        C.do_set(9, False),
        C.da_set(1, 0.0),
        C.pwm_off(1234),
    ):
        gate.send(cmd)
    assert len(fake.writes) == 9
    for cmd in (
        C.jog_step(0, 5.0),
        C.home_axis(0),
        C.fifo_clear(),
        C.fifo_start(),
        C.do_set(1, True),
        C.do_set(9, True),
    ):
        with pytest.raises(SafetyViolation):
            gate.send(cmd)
    with pytest.raises(SafetyViolation):
        gate.write(0x66, [1, 0x00080BB8, 0, 0])
    assert len(fake.writes) == 9


def test_motion_armed_allows_jog_home_gas_but_not_laser() -> None:
    gate, fake = armed(ArmState.MOTION_ARMED)
    gate.send(C.jog_continuous(0, True))
    gate.send(C.jog_step(1, -5.0))
    gate.send(C.home_axis(0))
    gate.send(C.do_set(3, True))
    gate.send(C.fifo_clear())
    with pytest.raises(SafetyViolation, match="needs laser"):
        gate.send(C.do_set(9, True))
    with pytest.raises(SafetyViolation):
        gate.send(C.pwm_set(5000, 4))
    with pytest.raises(SafetyViolation, match="not homed"):
        gate.send(C.go_to(10.0, 10.0))
    gate.homed = True
    gate.send(C.go_to(10.0, 10.0))
    assert [w[1][0] for w in fake.writes] == [3, 3, 2, 9999, 1, 5]


def test_laser_armed_allows_laser_do_and_pwm() -> None:
    gate, fake = armed(ArmState.LASER_ARMED)
    gate.send(C.do_set(9, True))
    gate.send(C.pwm_set(5000, 4))
    assert fake.writes[-2:] == [(0x65, (9999, 2, 0x100, 0x100)), (0x65, (9999, 0x11, 5000, 4))]


def test_do_bulk_write_uses_last_do_word() -> None:
    gate, fake = armed(ArmState.DISARMED)
    fake.status[5] = 0x108  # DO4 and DO9 on
    gate.read(1000, 36)
    assert gate.last_do_word == 0x108
    gate.write(0x65, [9999, 2, 0xFFFF, 0x8])  # keeps DO4, clears DO9: allowed
    with pytest.raises(SafetyViolation):
        gate.write(0x65, [9999, 2, 0xFFFF, 0x108])  # would keep the laser on
    with pytest.raises(SafetyViolation):
        gate.write(0x65, [9999, 2, 0xFFFF, 0x0C])  # turns DO3 on while disarmed


def test_classify_is_independent_of_builder_metadata() -> None:
    lying = C.with_policy(C.do_set(9, True), Policy.ALWAYS)
    assert classify_write(lying.register, lying.words).policy is Policy.LASER


def test_write_log_carries_frame_bytes() -> None:
    gate, fake = armed(ArmState.MOTION_ARMED)
    records = []
    gate.on_write = records.append
    cmd = C.jog_step(0, 5.0)
    gate.send(cmd)
    rec = gate.write_log[-1]
    assert rec.decision == "sent" and records == [rec]
    assert rec.frame == encode_vector(cmd.request, 7)
    assert rec.state is ArmState.MOTION_ARMED


# ---- FIFO stripping ---------------------------------------------------------------------------------------


def tick(dx: int, dy: int, freq: int, duty: int) -> list[int]:
    return [0x00080BB8, ((dy & 0xFFFF) << 16) | (dx & 0xFFFF), (freq << 16) | duty]


PROLOGUE_FRAME = [
    60,
    0x00000BB9,  # 3001
    0x00040BBA,
    5,  # 3002[5]
    0x000C270F,
    2,
    4,
    4,  # DO3 high air on
    0x00080067,
    1000,
    0,  # 103[1000,0]
    0x000807D1,
    0x03000002,
    20000,  # 2001
    0x000C270F,
    2,
    0x100,
    0x100,  # DO9 CO2 laser on
    *tick(0, 0, 5000, 4),
    *tick(3, -2, 5000, 4),
    0x000C270F,
    0x11,
    5000,
    4,  # PWM set record
    0x000C270F,
    2,
    0x100,
    0,  # DO9 off
]


def test_strip_laser_records() -> None:
    words, changed = strip_laser_records(PROLOGUE_FRAME)
    frame = parse_fifo_words(words)
    ops = [(i.opcode, i.args) for i in frame.items]
    assert (9999, (2, 0x100, 0x100)) not in ops
    assert (9999, (0x11, 5000, 4)) not in ops
    assert (9999, (2, 4, 4)) in ops and (9999, (2, 0x100, 0)) in ops
    assert [i.tick for i in frame.items if i.tick] == [(0, 0, 5000, 0), (3, -2, 5000, 0)]
    assert changed == 4 and frame.frame_id == 60 and not frame.remainder


def test_fifo_frame_stripped_unless_laser_armed() -> None:
    gate, fake = armed(ArmState.MOTION_ARMED)
    gate.read(1000, 36)  # streaming needs a fresh status poll (PORT-PLAN §8.2 watchdog)
    gate.write(0x66, PROLOGUE_FRAME)
    assert fake.writes[-1][1] != tuple(PROLOGUE_FRAME)
    assert gate.write_log[-1].stripped == 4
    gate2, fake2 = armed(ArmState.LASER_ARMED)
    gate2.read(1000, 36)
    gate2.write(0x66, PROLOGUE_FRAME)
    assert fake2.writes[-1][1] == tuple(PROLOGUE_FRAME)


def test_fifo_frame_with_unknown_opcode_refused() -> None:
    gate, fake = armed(ArmState.MOTION_ARMED)
    with pytest.raises(SafetyViolation):
        gate.write(0x66, [1, 0x00041234, 0])
    with pytest.raises(SafetyViolation):
        gate.write(0x66, [1, 0x00080BB8, 0])  # truncated tick
    assert fake.writes == []


# ---- arming state machine ---------------------------------------------------------------------------------


def test_state_machine_transitions() -> None:
    sm = ArmingStateMachine()
    assert sm.state is ArmState.DISARMED
    with pytest.raises(ArmingError):
        sm.begin_job()
    with pytest.raises(ArmingError):
        sm.arm_laser(confirmed=True, job_token=None)
    sm.arm_motion()
    assert sm.state is ArmState.MOTION_ARMED
    with pytest.raises(ArmingError):
        sm.arm_laser(confirmed=True, job_token=None)  # no job running
    token = sm.begin_job()
    with pytest.raises(ArmingError):
        sm.arm_laser(confirmed=False, job_token=token)  # no confirmation
    with pytest.raises(ArmingError):
        sm.arm_laser(confirmed=True, job_token="forged")
    sm.arm_laser(confirmed=True, job_token=token)
    assert sm.state is ArmState.LASER_ARMED
    sm.end_job(token)
    assert sm.state is ArmState.MOTION_ARMED and sm.job_token is None
    token = sm.begin_job()
    sm.arm_laser(confirmed=True, job_token=token)
    sm.on_stop()
    assert sm.state is ArmState.MOTION_ARMED
    token = sm.begin_job()
    sm.arm_laser(confirmed=True, job_token=token)
    sm.on_alarm("1006 bit 25")
    assert sm.state is ArmState.DISARMED
    sm.arm_motion()
    sm.on_comm_loss()
    assert sm.state is ArmState.DISARMED
    assert [t.new for t in sm.history if t.old is not t.new][-1] is ArmState.DISARMED


def test_estop_latch() -> None:
    sm = ArmingStateMachine()
    sm.arm_motion()
    sm.estop()
    assert sm.state is ArmState.DISARMED and sm.estop_latched
    with pytest.raises(ArmingError):
        sm.arm_motion()
    assert sm.allows(Policy.ALWAYS) and not sm.allows(Policy.MOTION)
    sm.acknowledge_estop()
    assert sm.state is ArmState.DISARMED  # acknowledging does not re-arm
    sm.arm_motion()
    assert sm.state is ArmState.MOTION_ARMED


def test_stop_disarms_laser_and_sends_sequence() -> None:
    gate, fake = armed(ArmState.LASER_ARMED)
    out = gate.stop(v_last_word=200000)
    assert out.ok and gate.arming.state is ArmState.MOTION_ARMED
    assert fake.writes[0] == (0x65, (1, 0x1F, 2, 2000, 20000))
    assert fake.writes[-1] == (0x65, (101,))


def test_estop_from_disarmed_still_sends_everything() -> None:
    gate, fake = armed(ArmState.MOTION_ARMED)
    gate.read(1000, 36)  # streaming needs a fresh status poll (PORT-PLAN §8.2 watchdog)
    gate.write(0x67, [2])
    assert gate.fifo_running
    out = gate.estop()
    assert out.ok and gate.arming.estop_latched
    assert fake.writes[1] == (0x67, (3,))
    assert (0x65, (9999, 2, 0x100, 0)) in fake.writes
    with pytest.raises(SafetyViolation):
        gate.send(C.jog_step(0, 1.0))


# ---- end to end through the simulator ------------------------------------------------------------------------


@pytest.fixture
def rig() -> Iterator[tuple[CardSimulator, SafeMccClient]]:
    cfg = SimConfig(motion_time_s=0.05)
    policy = RetryPolicy(timeout_ms=100, send_times=2, recv_times=2)
    with CardSimulator(config=cfg) as sim, McTransaction(sim.address, policy=policy) as client:
        yield sim, SafeMccClient(client, params=replace(MachineParams(), is_fast_mode=False))


def test_safe_client_against_simulator(rig: tuple[CardSimulator, SafeMccClient]) -> None:
    sim, gate = rig
    gate.send(C.connect_prologue())
    with pytest.raises(SafetyViolation):
        gate.send(C.jog_step(0, 5.0))
    assert [n for n, _ in sim.command_log] == ["connect_prologue"]
    gate.arming.arm_motion()
    gate.send(C.jog_step(0, 5.0, gate.params))
    rec = gate.write_log[-1]
    assert sim.requests[-1].raw == rec.frame  # logged bytes are the bytes on the wire
    out = gate.stop(v_last_word=50000)
    assert out.ok, out.failed
    names = [n for n, _ in sim.command_log]
    assert names[:3] == ["connect_prologue", "jog", "stop"]
    assert names[-1] == "zf_stop" and "pwm" in names and "da" in names
    status = gate.read(1000, 36)
    assert status[5] & 0x100 == 0
    with pytest.raises(SafetyViolation):
        gate.write(150, [5555])
    assert all(r.vector is None or r.vector[1] != 150 for r in sim.requests)
