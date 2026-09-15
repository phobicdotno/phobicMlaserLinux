"""mccd/gate.py: priority bus, bus client policies, F8 un-homed jog rule (DECISIONS D1/D4)."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence

import pytest

from nexcut.mcc import commands as C
from nexcut.mcc.safety import SafetyViolation
from nexcut.mcc.transaction import RetryPolicy
from nexcut.mccd.gate import BusClient, MccdGate, MotionRules, Priority, PriorityBus


class Fake:
    def __init__(self) -> None:
        self.log: list[tuple[str, object]] = []
        self.policy: RetryPolicy | None = None
        self.next_seq = 0

    def set_policy(self, p: RetryPolicy) -> None:
        self.policy = p

    def read(self, addr: int, count: int) -> list[int]:
        self.log.append(("read", self.policy))
        return [0] * count

    def write(self, addr: int, words: Sequence[int]) -> None:
        self.log.append(("write", self.policy))


def test_priority_bus_serves_highest_priority_first() -> None:
    bus = PriorityBus()
    order: list[str] = []
    started = threading.Event()

    def holder() -> None:
        with bus.hold(Priority.SLOW):
            started.set()
            time.sleep(0.15)

    def waiter(name: str, prio: Priority) -> None:
        with bus.hold(prio):
            order.append(name)

    t0 = threading.Thread(target=holder)
    t0.start()
    started.wait()
    threads = [
        threading.Thread(target=waiter, args=("slow", Priority.SLOW)),
        threading.Thread(target=waiter, args=("command", Priority.COMMAND)),
        threading.Thread(target=waiter, args=("fast", Priority.FAST)),
        threading.Thread(target=waiter, args=("urgent", Priority.URGENT)),
    ]
    for t in threads:
        t.start()
        time.sleep(0.02)
    for t in [t0, *threads]:
        t.join(2)
    assert order == ["urgent", "fast", "command", "slow"]


def test_priority_bus_is_reentrant() -> None:
    bus = PriorityBus()
    with bus.hold(Priority.FAST), bus.hold(Priority.SLOW):
        pass
    with bus.hold():
        pass


def test_bus_client_policy_per_queue() -> None:
    fake, bus = Fake(), PriorityBus()
    poll, slow, idle = RetryPolicy(150, 1, 1), RetryPolicy(151, 1, 1), RetryPolicy.idle()
    client = BusClient(fake, bus, poll_policy=poll, read_policy=slow, write_policy=idle)
    with bus.priority(Priority.FAST):
        client.read(1000, 36)
    with bus.priority(Priority.SLOW):
        client.read(2000, 50)
    client.read(59600, 1)  # COMMAND (IPC read_block)
    client.write(0x65, [101])
    assert fake.log == [("read", poll), ("read", slow), ("read", slow), ("write", idle)]
    with client._lock:  # the gate's TOCTOU re-check holds the bus around the write
        client.write(0x65, [101])


def make_gate(**kw: object) -> tuple[MccdGate, Fake]:
    fake = Fake()
    gate = MccdGate(fake, supervise=False, **kw)  # type: ignore[arg-type]
    gate.arming.arm_motion()
    gate.read(1000, 36)
    return gate, fake


def test_unhomed_step_bound() -> None:
    gate, fake = make_gate()
    gate.send(C.jog_step(0, 10.0))
    gate.send(C.jog_step(1, -10.0))
    with pytest.raises(SafetyViolation, match="D1"):
        gate.send(C.jog_step(0, 10.001))
    assert len([x for x in fake.log if x[0] == "write"]) == 2
    assert gate.write_log[-1].decision == "refused"


def test_unhomed_continuous_only_slow_and_under_deadman() -> None:
    gate, _ = make_gate()
    with pytest.raises(SafetyViolation, match="D1"):
        gate.send(C.jog_continuous(0, True))  # 200 mm/s, 4000 mm
    slow = C.MachineParams(jog_fast_speed=20.0)
    gate.send(C.jog_continuous(0, True, slow))
    assert gate.has_lease(0) and gate.leases() == {0: 20000}
    fast_speed = C.MachineParams(jog_fast_speed=20.001)
    with pytest.raises(SafetyViolation):
        gate.send(C.jog_continuous(1, True, fast_speed))


def test_rules_configurable() -> None:
    gate, _ = make_gate(rules=MotionRules.from_mm(2.0, 5.0))
    with pytest.raises(SafetyViolation):
        gate.send(C.jog_step(0, 3.0))
    gate.send(C.jog_continuous(1, False, C.MachineParams(jog_fast_speed=5.0)))


def test_homed_axis_uses_soft_limits() -> None:
    gate, _ = make_gate()
    gate.homed_slots = {0}
    gate.positions_word = {0: 1_300_000}
    gate.send(C.jog_step(0, 71.0))  # 1371 mm: on the limit
    with pytest.raises(SafetyViolation, match="soft limit"):
        gate.send(C.jog_step(0, 72.0))
    gate.send(C.jog_continuous(0, False, soft_limit_remaining_mm=1300.0))  # 200 mm/s is fine now
    # homed but position not trusted (D2): the un-homed rule stays
    gate.homed_slots = {0, 1}
    with pytest.raises(SafetyViolation, match="D1"):
        gate.send(C.jog_step(1, 50.0))
