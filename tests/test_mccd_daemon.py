"""nexcut-mccd against the card simulator over loopback (11 §2/§3/§4.7/§7, PORT-PLAN §8.2).

Timing bounds are deliberately loose (CI machines are slow); every wait polls a condition.
"""

from __future__ import annotations

import shutil
import statistics
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nexcut.core.config import NexcutConfig
from nexcut.mcc.framing import encode_vector
from nexcut.mcc.safety import ArmState
from nexcut.mcc.simulator import CardSimulator, SimConfig
from nexcut.mccd.daemon import IPC_COMMANDS, Link, MccDaemon
from nexcut.mccd.ipc import IpcError, MccdClient


def wait_for(cond: Callable[[], object], timeout: float = 3.0, period: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(period)
    return bool(cond())


@contextmanager
def running(
    *, mccd: dict[str, Any] | None = None, motion: dict[str, Any] | None = None,
    sim_config: SimConfig | None = None,
) -> Iterator[tuple[MccDaemon, CardSimulator, Path]]:  # fmt: skip
    base = NexcutConfig()
    cfg = replace(
        base,
        mccd=replace(base.mccd, **(mccd or {})),
        motion=replace(base.motion, **(motion or {})),
    )
    sim = CardSimulator(config=sim_config).start()
    d = Path(tempfile.mkdtemp(prefix="nxmccd"))
    daemon = MccDaemon(cfg, card_addr=sim.address, socket_path=d / "mccd.sock").start()
    try:
        assert daemon.wait_for_link(Link.CONNECTED, 5.0), daemon.last_error
        assert wait_for(lambda: daemon.axis_ro is not None)
        yield daemon, sim, daemon.socket_path
    finally:
        daemon.close()
        sim.stop()
        shutil.rmtree(d, ignore_errors=True)


def commands(sim: CardSimulator) -> list[tuple[int, ...]]:
    with sim._lock:
        return list(sim.commands)


def vectors(sim: CardSimulator) -> list[tuple[int, ...]]:
    with sim._lock:
        return [r.vector for r in sim.requests if r.vector is not None]


def is_axis_stop(v: tuple[int, ...], slot: int) -> bool:
    return len(v) == 5 and v[0] == 1 and v[1] == 1 << slot and v[2] == 2 and v[4] == 10 * v[3]


def is_stop_all(v: tuple[int, ...]) -> bool:
    return len(v) == 5 and v[:3] == (1, 0x1F, 2)


def wait_ready(daemon: MccDaemon) -> None:
    def ready() -> bool:
        fresh = (daemon.axis_ro_t or 0.0) > daemon._last_motion_t
        return fresh and daemon.snapshot().machine_state == "READY"

    assert wait_for(ready, 3.0)


# ---- start-up sequence (11 §7 step 1, 11 §2 V0 / N4) ---------------------------------------------


def test_startup_sequence_bytes() -> None:
    with running() as (daemon, sim, _):
        with sim._lock:
            first = sim.requests[:5]
        expected = [
            [0x30, 1000, 2],
            [0x30, 1000, 36],
            [0x30, 50000, 26],
            [0x30, 50200, 100],
            [0x40, 0x65, 4, 9999, 5, 0, 0],
        ]
        assert [list(r.vector or ()) for r in first] == expected
        assert [r.raw for r in first] == [encode_vector(v, i) for i, v in enumerate(expected)]
        assert first[0].raw.hex().startswith("00008c75")  # CRC of READ 1000/2 seq 0 (04 §3.2)
        assert daemon.program_version == 20152 and daemon.params.k == 1000
        assert commands(sim)[0] == (9999, 5, 0, 0)
        assert daemon.gate.arming.state is ArmState.DISARMED  # connecting never arms


def test_version_gate_refuses_old_card() -> None:
    sim = CardSimulator(config=SimConfig(program_version=20100)).start()
    d = Path(tempfile.mkdtemp(prefix="nxmccd"))
    daemon = MccDaemon(card_addr=sim.address, socket_path=d / "s.sock").start()
    try:
        assert daemon.wait_for_link(Link.VERSION_REFUSED, 3.0)
        time.sleep(0.2)
        assert (9999, 5, 0, 0) not in commands(sim)
        with MccdClient(daemon.socket_path) as c, pytest.raises(IpcError) as exc:
            c.call("arm_motion")
        assert exc.value.code == "not_connected"
    finally:
        daemon.close()
        sim.stop()
        shutil.rmtree(d, ignore_errors=True)


# ---- poll cadence (11 §3.2, PORT-PLAN §3.4) -----------------------------------------------------


def test_poll_cadence_fast_and_slow_queues() -> None:
    with running(mccd={"system_rw_period_ms": 300, "fast_combined_period_ms": 400}) as (
        _daemon,
        sim,
        _,
    ):
        t0 = time.monotonic()
        time.sleep(1.5)
        with sim._lock:
            reqs = [r for r in sim.requests if r.vector is not None and r.t >= t0]
        fast = [r.t for r in reqs if r.vector[:3] == (0x30, 1000, 36)]
        span = reqs[-1].t - reqs[0].t
        rate = len(fast) / span
        assert 15 <= rate <= 45, rate  # nominal 33/s (MCCore 30 ms)
        gaps = [b - a for a, b in zip(fast, fast[1:], strict=False)]
        assert 0.015 <= statistics.median(gaps) <= 0.07
        count = lambda v: sum(1 for r in reqs if r.vector[:3] == v)  # noqa: E731
        assert count((0x30, 2000, 50)) >= 5  # ~11/s at 90 ms
        assert count((0x30, 50000, 26)) >= 2
        assert count((0x30, 60001, 120)) >= 2
        assert count((0x30, 10000, 18)) >= 1  # ZFType = 1 in the simulator


def test_deaf_slow_block_never_blocks_the_fast_poll() -> None:
    with running(mccd={"zf_status_period_ms": 100}) as (daemon, sim, _):
        sim.deaf_addresses.add(10000)  # the "deaf 10000 block" (08 §4.3)
        t0 = time.monotonic()
        time.sleep(1.5)
        with sim._lock:
            fast = [
                r.t for r in sim.requests
                if r.vector is not None and r.vector[:3] == (0x30, 1000, 36) and r.t >= t0
            ]  # fmt: skip
            deaf = [r for r in sim.requests if r.action == "no-reply" and r.t >= t0]
        assert len(deaf) >= 5
        gaps = [b - a for a, b in zip(fast, fast[1:], strict=False)]
        assert max(gaps) < 0.4, max(gaps)  # one short slow timeout (150 ms) at most
        assert daemon.link is Link.CONNECTED


# ---- IPC round trips and the command boundary (F12) ---------------------------------------------


def test_ipc_status_arm_disarm_round_trip() -> None:
    with running() as (daemon, sim, path), MccdClient(path) as c:
        pong = c.call("ping")
        assert pong["commands"] == list(IPC_COMMANDS)
        st = c.call("status")
        assert st["link"] == "CONNECTED" and st["arm_state"] == "DISARMED"
        assert st["machine_state"] == "READY" and st["program_version"] == 20152
        assert st["poll_age_s"] is not None and st["poll_age_s"] < 0.5
        assert c.call("arm_motion") == {"arm_state": "MOTION_ARMED"}
        wait_ready(daemon)
        res = c.call("jog_step", slot=0, mm=5.0, speed=50.0)
        assert res["words"] == [3, 0, 50000, 5999, 59990, 5000]  # 11 §2 V2
        assert wait_for(lambda: (3, 0, 50000, 5999, 59990, 5000) in commands(sim))
        assert c.call("disarm")["arm_state"] == "DISARMED"
        with pytest.raises(IpcError) as exc:
            c.call("jog_step", slot=0, mm=1.0, speed=50.0)
        assert exc.value.code in ("refused", "busy")


def test_ipc_has_no_raw_register_path() -> None:
    with running() as (daemon, sim, path), MccdClient(path) as c:
        for cmd, args in [
            ("write", {"addr": 150, "words": [5555]}),
            ("write_register", {"addr": 150, "words": [5555]}),
            ("transact", {"request": [0x40, 150, 1, 5555]}),
            ("send", {"addr": 101, "words": [102]}),
            ("firmware", {}),
        ]:
            with pytest.raises(IpcError) as exc:
                c.call(cmd, **args)
            assert exc.value.code == "unknown_command"
        for addr, n in [(59500, 1), (59502, 2), (151, 1), (200, 1), (100, 1), (1000, 37)]:
            with pytest.raises(IpcError) as exc:
                c.call("read_block", addr=addr, n=n)
            assert exc.value.code == "refused", (addr, n)
        assert c.call("read_block", addr=1000, n=2)["words"][1] == 20152
        c.call("arm_motion")
        for port, on in [(9, True), (5, True), (6, True), (4, True), (11, True), (4, False)]:
            with pytest.raises(IpcError) as exc:
                c.call("set_do", port=port, on=on)
            assert exc.value.code == "refused", port
        assert c.call("set_do", port=3, on=True) == {"port": 3, "on": True}
        assert wait_for(lambda: sim.outputs & 0x4)
        c.call("set_do", port=9, on=False)  # laser output off is always allowed
        with pytest.raises(IpcError) as exc:
            c.call("home", slots=[4])  # lift table (V10 DENY)
        assert exc.value.code == "refused"
        time.sleep(0.1)
        vs = vectors(sim)
        assert all(v[1] not in (150, 151, 200, 201, 100) for v in vs if v[0] == 0x40)
        assert all(not 59500 <= v[1] <= 59599 for v in vs)
        assert not any(v[0] == 0x26 for v in vs)
        refused = [r for r in daemon.gate.write_log if r.decision == "refused"]
        assert refused == []  # IPC refused before the gate for these; nothing reached it


# ---- F8 rules over IPC (DECISIONS D1) ------------------------------------------------------------


def test_unhomed_then_homed_jog_rules() -> None:
    with (
        running(motion={"position_scale_verified": True}) as (daemon, sim, path),
        MccdClient(path) as c,
    ):
        c.call("arm_motion")
        for args in (
            {"cmd": "jog_step", "slot": 0, "mm": 10.5, "speed": 200.0},
            {"cmd": "jog_continuous_start", "slot": 1, "positive": True, "speed": 50.0},
        ):
            cmd = args.pop("cmd")
            with pytest.raises(IpcError) as exc:
                c.call(cmd, **args)
            assert exc.value.code == "refused" and "D1" in exc.value.message
        wait_ready(daemon)
        c.call("jog_step", slot=0, mm=10.0, speed=200.0)
        wait_ready(daemon)
        assert c.call("home", slots=[0, 1]) == {"homing": [0, 1]}
        assert wait_for(lambda: daemon.snapshot().homed_slots == (0, 1), 5.0)
        assert wait_for(lambda: daemon._homing is None, 3.0)
        assert (2, 1, 0) in commands(sim) and (2, 2, 0) in commands(sim)
        assert commands(sim).index((2, 1, 0)) < commands(sim).index((2, 2, 0))
        wait_ready(daemon)
        assert wait_for(lambda: daemon.gate.positions_word == {0: 0, 1: 0})
        c.call("jog_step", slot=0, mm=100.0, speed=200.0)  # homed: soft limits instead
        wait_ready(daemon)
        with pytest.raises(IpcError) as exc:
            c.call("jog_step", slot=1, mm=-1.0, speed=50.0)  # below the Y soft limit 0
        assert "soft limit" in exc.value.message
        res = c.call("jog_continuous_start", slot=1, positive=True, speed=200.0)
        assert res["words"][5] == 950_000  # clipped to the remaining 950 mm
        c.call("jog_continuous_stop", slot=1)


# ---- deadman and input-source loss (PORT-PLAN §8.2, 11 N7) ---------------------------------------


def test_deadman_expiry_sends_per_axis_stop() -> None:
    with running() as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        t0 = time.monotonic()
        c.call("jog_continuous_start", slot=1, positive=False, speed=20.0)
        assert (3, 1, 20000, 5999, 59990, 0xFFFFFFFF - 400000 + 1) in commands(sim)
        assert wait_for(lambda: any(is_axis_stop(v, 1) for v in commands(sim)), 1.0)
        elapsed = time.monotonic() - t0
        assert 0.15 <= elapsed <= 0.8, elapsed
        stop = next(v for v in commands(sim) if is_axis_stop(v, 1))
        assert stop == (1, 2, 2, 5000, 50000)  # vd = 100000 / 20 (A1 §4.1)
        assert c.call("jog_refresh", slot=1) == {"alive": False}


def test_refreshed_jog_keeps_running_until_stop() -> None:
    with running() as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        c.call("jog_continuous_start", slot=0, positive=True, speed=20.0)
        for _ in range(8):
            time.sleep(0.08)
            assert c.call("jog_refresh", slot=0) == {"alive": True}
        assert not any(is_axis_stop(v, 0) for v in commands(sim))
        assert c.call("status")["jogs"] == {"0": 20000}
        c.call("jog_continuous_stop", slot=0)
        assert any(is_axis_stop(v, 0) for v in commands(sim))


def test_client_disconnect_stops_its_jog() -> None:
    with running(mccd={"deadman_timeout_ms": 5000}) as (daemon, sim, path):
        c = MccdClient(path)
        c.call("arm_motion")
        wait_ready(daemon)
        c.call("jog_continuous_start", slot=0, positive=True, speed=20.0)
        time.sleep(0.3)
        assert not any(is_axis_stop(v, 0) for v in commands(sim))
        c.close()
        assert wait_for(lambda: any(is_axis_stop(v, 0) for v in commands(sim)), 1.0)


def test_input_source_silence_hook_stops_jog() -> None:
    with running(mccd={"deadman_timeout_ms": 5000}) as (daemon, sim, _):
        pendant = daemon.register_input_source("pendant", silence_timeout_s=0.3)
        daemon.gate.arming.arm_motion()
        wait_ready(daemon)
        from nexcut.mcc import commands as C

        daemon.gate.send(C.jog_continuous(0, True, C.MachineParams(jog_fast_speed=20.0)))
        pendant.claim(0)
        time.sleep(0.15)
        assert not any(is_axis_stop(v, 0) for v in commands(sim))
        assert wait_for(lambda: any(is_axis_stop(v, 0) for v in commands(sim)), 1.0)
        assert pendant.slots == frozenset()
        pendant.claim(1)  # nothing running on Y: lost() still sends the per-axis stop
        pendant.lost("hidraw removed")
        assert any(is_axis_stop(v, 1) for v in commands(sim))


# ---- watchdog (PORT-PLAN §8.2, 11 §4.7) ----------------------------------------------------------


def test_watchdog_simulator_drop_disarms_and_stops() -> None:
    with running(mccd={"reconnect_interval_ms": 300}) as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        t0 = time.monotonic()
        sim.drop_requests(10**9)
        assert wait_for(lambda: daemon.link is Link.LINK_LOST, 3.0)
        assert 0.9 <= time.monotonic() - t0 <= 2.5
        assert daemon.gate.arming.state is ArmState.DISARMED
        t1 = time.monotonic()
        st = c.call("status")  # must not wait behind the stop's retry ladder
        assert time.monotonic() - t1 < 0.5
        assert st["link"] == "LINK_LOST" and st["poll_age_s"] > 1.0
        with sim._lock:
            sim._drop_requests = 0  # link back: the stop's retry ladder gets through
        assert wait_for(lambda: any(is_stop_all(v) for v in commands(sim)), 5.0)
        assert daemon.wait_for_link(Link.CONNECTED, 5.0)  # start-up sequence re-run
        assert commands(sim).count((9999, 5, 0, 0)) == 2
        assert daemon.gate.arming.state is ArmState.DISARMED  # never re-armed automatically


def test_watchdog_alarm_injection_stops_and_disarms() -> None:
    with running() as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        n = len(commands(sim))
        sim.inject_alarm(alarm1=1 << 25)  # bus fault (11 §4.2)
        assert wait_for(lambda: daemon.gate.arming.state is ArmState.DISARMED, 1.0)
        assert wait_for(lambda: any(is_stop_all(v) for v in commands(sim)[n:]), 2.0)
        st = c.call("status")
        assert st["machine_state"] == "ALARM"
        assert any(a["text"] == "Bus Fault" and a["code"] == 8025 for a in st["alarms"])
        c.call("arm_motion")
        with pytest.raises(IpcError) as exc:
            c.call("jog_step", slot=0, mm=1.0, speed=50.0)
        assert exc.value.code in ("refused", "busy") and "alarm" in exc.value.message.lower()


def test_watchdog_axis_fault_while_jogging() -> None:
    with running(mccd={"deadman_timeout_ms": 5000}) as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        c.call("jog_continuous_start", slot=0, positive=True, speed=20.0)
        with sim._lock:  # simulator cannot model limits: patch the axis status word
            sim.config.blocks[2000] = 50
            orig = sim._update_status

            def with_limit(now: float) -> None:
                orig(now)
                sim.registers[2000] |= 0b1  # X hard + limit

            sim._update_status = with_limit  # type: ignore[method-assign]
        assert wait_for(lambda: daemon.gate.arming.state is ArmState.DISARMED, 2.0)
        assert wait_for(lambda: any(is_stop_all(v) for v in commands(sim)), 2.0)
        c.call("arm_motion")
        wait_ready(daemon)
        with pytest.raises(IpcError) as exc:
            c.call("jog_step", slot=0, mm=1.0, speed=20.0)
        assert "limit" in exc.value.message
        c.call("jog_step", slot=0, mm=-1.0, speed=20.0)  # away from the limit is allowed


# ---- E-stop latch (A1 §4.4, PORT-PLAN §8.2) -----------------------------------------------------


def test_ui_estop_latches_until_acknowledged() -> None:
    with running() as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        res = c.call("estop")
        assert res["estop_latched"] is True and "stop_all" in res["sent"]
        assert any(is_stop_all(v) for v in commands(sim))
        st = c.call("status")
        assert (
            st["estop_latched"] and st["arm_state"] == "DISARMED" and st["machine_state"] == "ALARM"
        )
        with pytest.raises(IpcError) as exc:
            c.call("arm_motion")
        assert exc.value.code == "arming"
        assert c.call("ack_estop")["estop_latched"] is False
        assert c.call("arm_motion") == {"arm_state": "MOTION_ARMED"}


def test_card_estop_bit_latches_and_ack_needs_release() -> None:
    with running() as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        sim.inject_alarm(alarm1=1 << 30)
        assert wait_for(lambda: daemon.gate.arming.estop_latched, 1.0)
        assert c.call("status")["alarms"][0]["text"] == "Emergency stop alarm"
        with pytest.raises(IpcError) as exc:
            c.call("ack_estop")
        assert exc.value.code == "refused"
        sim.inject_alarm(alarm1=0)
        assert wait_for(lambda: not c.call("status")["alarms"], 1.0)
        c.call("ack_estop")
        c.call("arm_motion")


# ---- two clients ---------------------------------------------------------------------------------


def test_status_subscriber_and_commander() -> None:
    with running() as (daemon, sim, path), MccdClient(path) as sub, MccdClient(path) as cmd:
        assert sub.call("subscribe", interval_ms=50) == {"interval_ms": 50}
        events = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.4:
            ev = sub.next_event(0.5)
            assert ev is not None
            events.append(ev)
        assert 4 <= len(events) <= 14
        assert all(e["event"] == "status" for e in events)
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
        cmd.call("arm_motion")
        wait_ready(daemon)
        cmd.call("jog_step", slot=1, mm=2.0, speed=100.0)

        def seen_armed() -> bool:
            ev = sub.next_event(0.2)
            return ev is not None and ev["data"]["arm_state"] == "MOTION_ARMED"

        assert wait_for(seen_armed, 2.0, period=0)
        assert sub.call("status")["link"] == "CONNECTED"  # requests work on a subscribed conn
        sub.call("unsubscribe")
        time.sleep(0.15)
        sub.events.clear()
        assert sub.next_event(0.2) is None
        cmd.call("disarm")


def test_bad_requests() -> None:
    with running() as (daemon, _sim, path), MccdClient(path) as c:
        for cmd, args in [
            ("jog_step", {"slot": 0, "mm": 1.0}),
            ("jog_step", {"slot": 0, "mm": 1.0, "speed": 500.0}),
            ("jog_step", {"slot": 0, "mm": float("nan") if False else 0, "speed": 5.0}),
            ("jog_continuous_start", {"slot": 0, "positive": 1, "speed": 5.0}),
            ("home", {"slots": []}),
            ("read_block", {"addr": 1000}),
            ("subscribe", {"interval_ms": 1}),
        ]:
            with pytest.raises(IpcError) as exc:
                c.call(cmd, **args)
            assert exc.value.code == "bad_request", (cmd, args)
        with pytest.raises(IpcError) as exc:
            c.call("jog_step", slot=2, mm=1.0, speed=5.0)
        assert exc.value.code == "refused"
        with pytest.raises(IpcError) as exc:
            c.call("home", slots=[0])
        assert exc.value.code == "arming"
