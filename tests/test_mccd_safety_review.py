"""Adversarial review of ``nexcut-mccd``, its CLI/TUI and ``tools/m1_session.py``.

Lens: safety / concurrency (PORT-PLAN §8.2, docs/DECISIONS.md D1-D11). Every test here
failed before the fix it names (or is a strict xfail for an open decision). Interleavings
are forced with deterministic delays at the race point, and the stress tests repeat the
race 20 times.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from nexcut.mcc import commands as C
from nexcut.mcc.safety import ArmState
from nexcut.mcc.simulator import SimConfig
from nexcut.mccd import ipc
from nexcut.mccd.daemon import Link, MccDaemon
from nexcut.mccd.gate import MccdGate
from nexcut.mccd.ipc import IpcError, IpcServer, MccdClient
from nexcut.mccd.tui import Lane
from test_m1_session import (
    ScriptedOperator,
    argv_for,
    daemon_on_sim,
    jogs_and_homes,
    m1,
    physical_hooks,
    step,
)
from test_mccd_cli_tui import make as make_tui
from test_mccd_daemon import (
    commands,
    is_axis_stop,
    is_stop_all,
    running,
    wait_for,
    wait_ready,
)
from test_mccd_gate import Fake

# ---- R1: a refreshed lease survived a trip; the undelivered stop was never re-sent ------------


def _refresher(path: Path, slot: int, stop: threading.Event, alive: list[Any]) -> threading.Thread:
    def run() -> None:
        with MccdClient(path) as r:
            while not stop.is_set():
                try:
                    alive.append(r.call("jog_refresh", slot=slot)["alive"])
                except (IpcError, OSError) as exc:
                    alive.append(str(exc))
                time.sleep(0.05)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_r1_stop_reaches_card_after_long_comm_loss_despite_refreshing_client() -> None:
    """PORT-PLAN §8.2 watchdog: comm loss -> stop. The minimal stop is tried once (one retry
    ladder, 3.5 s). With the link down longer and a client still refreshing, the lease stayed
    alive (the deadman never retried) and the reconnect sent no stop: a real card would keep
    running its 4000 mm continuous jog (11 N7)."""
    with running(mccd={"reconnect_interval_ms": 300}) as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        c.call("jog_continuous_start", slot=0, positive=True, speed=20.0)
        stop, alive = threading.Event(), []
        t = _refresher(path, 0, stop, alive)
        time.sleep(0.2)
        sim.drop_requests(10**9)
        assert wait_for(lambda: daemon.link is Link.LINK_LOST, 3.0)
        n_alive = len(alive)
        time.sleep(4.5)  # longer than one write retry ladder (3.5 s)
        assert False not in alive[:n_alive] and False in alive[n_alive:]  # refresh refused
        n = len(commands(sim))
        with sim._lock:
            sim._drop_requests = 0
        assert daemon.wait_for_link(Link.CONNECTED, 8.0)
        stop.set()
        t.join(2)
        after = commands(sim)[n:]
        stops = [i for i, v in enumerate(after) if is_stop_all(v) or is_axis_stop(v, 0)]
        assert stops, after
        assert daemon.gate.leases() == {}
        assert daemon.gate.arming.state is ArmState.DISARMED


def test_r1_pending_stop_is_sent_before_link_reported_connected() -> None:
    with running(mccd={"reconnect_interval_ms": 200}) as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        c.call("jog_step", slot=0, mm=5.0, speed=50.0)  # no lease: only _moving_possible
        sim.drop_requests(10**9)
        assert wait_for(lambda: daemon.link is Link.LINK_LOST, 3.0)
        assert wait_for(lambda: "watchdog stop_all" in (daemon.last_error or ""), 6.0)
        assert daemon._stop_pending  # the helper's single try failed
        n = len(commands(sim))
        with sim._lock:
            sim._drop_requests = 0
        assert daemon.wait_for_link(Link.CONNECTED, 8.0)
        after = commands(sim)[n:]
        assert after[0] == (9999, 5, 0, 0) and is_stop_all(after[1]), after
        assert daemon._stop_pending == 0


def test_r1_lease_not_refreshable_after_card_alarm() -> None:
    with running(mccd={"deadman_timeout_ms": 200}) as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        c.call("jog_continuous_start", slot=1, positive=True, speed=20.0)
        sim.drop_replies(0)
        sim.inject_alarm(alarm1=1 << 25)
        assert wait_for(lambda: daemon.gate.arming.state is ArmState.DISARMED, 1.0)
        assert c.call("jog_refresh", slot=1) == {"alive": False}


def test_r1_deadman_stop_answered_with_exception_is_not_resent_forever() -> None:
    """08 §4.5 (INFERENCE): the card may answer exception 3 to a stop when nothing moves. The
    deadman kept the lease on that "failure" and re-sent the stop every 20 ms (59 stops in
    1.5 s on the simulator), holding the bus at URGENT priority."""
    cfg = SimConfig(stop_while_idle_exception=True, motion_time_s=0.1)
    with running(sim_config=cfg) as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        assert c.call("jog_step", slot=0, mm=5.0, speed=20.0)["deadman"] is True
        time.sleep(1.5)  # not refreshed: the lease expires after the move ended
        assert len([v for v in commands(sim) if is_axis_stop(v, 0)]) == 1
        assert daemon.gate.leases() == {}


# ---- R2: stop during homing / jog send race (motion epoch, D10) --------------------------------


def _delay_before_send(daemon: Any, sub: int, delay: float) -> threading.Event:
    orig = daemon.gate.send
    entered = threading.Event()

    def slow_send(cmd: C.CommandVector) -> None:
        if cmd.words and cmd.words[0] == sub and cmd.register == 0x65:
            entered.set()
            time.sleep(delay)  # thread pre-empted between its checks and the wire
        orig(cmd)

    daemon.gate.send = slow_send
    return entered


@pytest.mark.parametrize("request_stop", ["stop", "disarm", "estop"])
def test_r2_home_queued_before_stop_never_reaches_card(request_stop: str) -> None:
    with running() as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        entered = _delay_before_send(daemon, C.CMD_HOME, 0.3)
        c.call("home", slots=[0])
        assert entered.wait(2)
        n = len(commands(sim))
        c.call(request_stop)
        time.sleep(0.6)
        after = commands(sim)[n:]
        assert not any(v[0] == C.CMD_HOME for v in after), after
        refused = [r for r in daemon.gate.write_log if r.decision == "refused"]
        if request_stop == "stop":
            assert any("D10" in r.reason for r in refused)
        assert wait_for(lambda: daemon._homing is None)


def test_r2_jog_queued_behind_stop_is_refused_20x() -> None:
    with running() as (daemon, sim, path), MccdClient(path) as a, MccdClient(path) as b:
        a.call("arm_motion")
        entered = _delay_before_send(daemon, C.CMD_JOG, 0.05)
        for i in range(20):
            wait_ready(daemon)
            entered.clear()
            errs: list[str] = []

            def jog(i: int = i, errs: list[str] = errs) -> None:
                try:
                    a.call("jog_step", slot=i % 2, mm=1.0, speed=50.0)
                except IpcError as exc:
                    errs.append(exc.code)

            t = threading.Thread(target=jog)
            t.start()
            assert entered.wait(2)
            n = len(commands(sim))
            b.call("stop")
            t.join(3)
            assert not any(v[0] == C.CMD_JOG for v in commands(sim)[n:])
            assert errs == ["refused"]


# ---- R3: un-homed step chaining beat the D1 speed bound ----------------------------------------


def test_r3_chained_unhomed_steps_are_rate_limited() -> None:
    """D1: un-homed motion faster than 20 mm/s only as a <= 10 mm step. Before the fix a
    client chaining 10 mm steps at 200 mm/s moved an un-homed axis at ~27 mm/s on the
    simulator (0.3 s per move) - on the card (10 mm in ~0.1 s) about 50 mm/s."""
    with running() as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        t0 = time.monotonic()
        total, refused = 0.0, 0
        while time.monotonic() - t0 < 3.0:
            try:
                c.call("jog_step", slot=0, mm=10.0, speed=200.0)
                total += 10.0
            except IpcError as exc:
                refused += "step rate" in exc.message
                time.sleep(0.01)
        elapsed = time.monotonic() - t0
        assert total <= 10.0 + 20.0 * elapsed + 1e-9, total
        assert refused > 0
        # slow jogs (<= 20 mm/s) are not rate limited: they run under the deadman
        wait_ready(daemon)
        c.call("jog_step", slot=0, mm=1.0, speed=20.0)


def test_r3_step_bucket_units() -> None:
    now = [100.0]
    gate = MccdGate(Fake(), supervise=False, clock=lambda: now[0])
    gate.arming.arm_motion()
    gate.read(1000, 36)
    fast = C.MachineParams(jog_fast_speed=200.0, jog_slow_speed=200.0)
    gate.send(C.jog_step(0, 6.0, fast))
    gate.send(C.jog_step(0, 4.0, fast))
    with pytest.raises(PermissionError, match="step rate"):
        gate.send(C.jog_step(0, 1.0, fast))
    gate.send(C.jog_step(1, 10.0, fast))  # per axis
    now[0] += 0.25  # 20 mm/s x 0.25 s = 5 mm
    gate.send(C.jog_step(0, 5.0, fast))
    with pytest.raises(PermissionError, match="step rate"):
        gate.send(C.jog_step(0, 0.5, fast))
    gate.homed_slots, gate.positions_word = {0}, {0: 500_000}
    gate.send(C.jog_step(0, 10.0, fast))  # homed + trusted position: soft limits instead


# ---- R4: concurrent motion commands both passed the READY mirror (D8) -------------------------


def test_r4_concurrent_motion_commands_never_both_reach_the_card_20x() -> None:
    with running() as (daemon, sim, path), MccdClient(path) as c1, MccdClient(path) as c2:
        c1.call("arm_motion")
        for i in range(20):
            wait_ready(daemon)
            barrier = threading.Barrier(2)

            def go(
                c: MccdClient, slot: int, i: int = i, barrier: threading.Barrier = barrier
            ) -> None:
                barrier.wait()
                with contextlib.suppress(IpcError):  # one of them may get "busy"
                    c.call("jog_step", slot=slot, mm=0.5 if i % 2 else -0.5, speed=50.0)

            ts = [
                threading.Thread(target=go, args=(c1, 0)),
                threading.Thread(target=go, args=(c2, 1)),
            ]
            for t in ts:
                t.start()
            for t in ts:
                t.join(5)
        with sim._lock:
            exceptions = [r for r in sim.requests if r.action == "exception"]
        assert exceptions == []  # the card never saw a motion while another was running


def test_r4_jog_refused_between_homing_axes() -> None:
    with running() as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        c.call("home", slots=[0, 1])
        assert wait_for(lambda: daemon._homing is not None and daemon._homing.done == [0], 5.0)
        with pytest.raises(IpcError) as exc:
            c.call("jog_step", slot=0, mm=1.0, speed=20.0)
        assert exc.value.code == "busy"
        assert wait_for(lambda: daemon._homing is None, 5.0)


# ---- deadman on crash / hang of a separate client process (regression, passed before) --------

_CLIENT = """
import sys, time
from nexcut.mccd.ipc import MccdClient
c = MccdClient(sys.argv[1])
c.call("arm_motion")
c.call("jog_continuous_start", slot=0, positive=True, speed=20.0)
r = MccdClient(sys.argv[1])
print("started", flush=True)
while True:
    r.call("jog_refresh", slot=0)
    time.sleep(0.05)
"""


@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGSTOP])
def test_deadman_on_client_kill9_or_hang(sig: int) -> None:
    with running(mccd={"deadman_timeout_ms": 200}) as (daemon, sim, path):
        wait_ready(daemon)
        p = subprocess.Popen(
            [sys.executable, "-c", _CLIENT, str(path)], stdout=subprocess.PIPE, text=True
        )
        try:
            assert p.stdout is not None and p.stdout.readline().strip() == "started"
            time.sleep(0.4)
            assert not any(is_axis_stop(v, 0) for v in commands(sim))  # refreshed: runs
            t0 = time.monotonic()
            os.kill(p.pid, sig)
            assert wait_for(lambda: any(is_axis_stop(v, 0) for v in commands(sim)), 1.0)
            assert time.monotonic() - t0 < (0.2 if sig == signal.SIGKILL else 0.6)
        finally:
            p.kill()
            p.wait()


# ---- watchdog fault injection (regression) ----------------------------------------------------


@pytest.mark.parametrize("fault", ["delay", "drop_replies", "stale"])
def test_watchdog_trips_on_reply_faults(fault: str) -> None:
    with running(mccd={"reconnect_interval_ms": 300}) as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        if fault == "delay":
            sim.delay_replies(0.5)
        elif fault == "drop_replies":
            sim.drop_replies(10**9)
        else:
            sim.delay_replies(0.3)
            sim.send_stale(10**9)
        t0 = time.monotonic()
        assert wait_for(lambda: daemon.link is Link.LINK_LOST, 3.0)
        assert time.monotonic() - t0 < 2.5
        # The watchdog disarms from its own thread, a moment after it flips the link
        # state, so wait for it instead of sampling (PORT-PLAN 8.2: stop + disarm).
        assert wait_for(lambda: daemon.gate.arming.state is ArmState.DISARMED, 2.0)


# ---- R5: E-stop acknowledge on a stale status ---------------------------------------------------


def test_r5_ack_estop_needs_a_live_status_read() -> None:
    with (
        running(mccd={"reconnect_interval_ms": 10000}) as (daemon, sim, path),
        MccdClient(path) as c,
    ):
        c.call("estop")
        sim.drop_requests(10**9)
        assert wait_for(lambda: daemon.link is Link.LINK_LOST, 3.0)
        sim.inject_alarm(alarm1=1 << 30)  # E-stop pressed while the PC cannot see it
        with pytest.raises(IpcError) as exc:
            c.call("ack_estop")
        assert exc.value.code == "not_connected"
        assert daemon.gate.arming.estop_latched


def test_estop_latch_survives_link_loss_and_reconnect() -> None:
    with running(mccd={"reconnect_interval_ms": 200}) as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        c.call("estop")
        sim.drop_requests(10**9)
        assert wait_for(lambda: daemon.link is Link.LINK_LOST, 3.0)
        with sim._lock:
            sim._drop_requests = 0
        assert daemon.wait_for_link(Link.CONNECTED, 5.0)
        assert daemon.gate.arming.estop_latched
        with pytest.raises(IpcError) as exc:
            c.call("arm_motion")
        assert exc.value.code == "arming"
        sim.inject_alarm(alarm1=1 << 30)
        assert wait_for(lambda: c.call("status")["alarms"], 1.0)
        with pytest.raises(IpcError):
            c.call("ack_estop")  # still pressed: refused
        assert daemon.gate.arming.estop_latched


# ---- R11 (open, D12): a second daemon on the same card -----------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="open decision D12: nothing stops a second nexcut-mccd (other --socket) from driving "
    "the same card; two gates, two E-stop latches, two deadmen (single master is only a "
    "checklist item in tools/m1_session.py)",
)
def test_r11_second_daemon_on_same_card_is_refused() -> None:
    with running() as (daemon, sim, _path):
        d = Path(tempfile.mkdtemp(prefix="nxmccd2"))
        other = MccDaemon(daemon.config, card_addr=sim.address, socket_path=d / "s.sock")
        try:
            with pytest.raises(RuntimeError):
                other.start()
        finally:
            other.close()
            shutil.rmtree(d, ignore_errors=True)


# ---- R6: Unix socket directory ownership / server identity --------------------------------------


def test_r6_daemon_refuses_foreign_or_symlinked_socket_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    d = Path(tempfile.mkdtemp(prefix="nxipc"))
    try:
        foreign = d / "foreign"
        foreign.mkdir(mode=0o755)
        real_uid = os.getuid()
        monkeypatch.setattr(ipc.os, "getuid", lambda: real_uid + 1)  # dir "owned by someone else"
        with pytest.raises(RuntimeError, match="belongs to uid"):
            IpcServer(foreign / "mccd.sock", lambda c, r: {}).start()
        monkeypatch.undo()
        real = d / "real"
        real.mkdir(mode=0o700)
        (d / "link").symlink_to(real)
        with pytest.raises(RuntimeError, match="not a plain directory"):
            IpcServer(d / "link" / "mccd.sock", lambda c, r: {}).start()
        loose = d / "loose"
        loose.mkdir(mode=0o700)
        os.chmod(loose, 0o777)
        srv = IpcServer(loose / "mccd.sock", lambda c, r: {"ok": 1}).start()
        try:
            assert (loose.stat().st_mode & 0o777) == 0o700
            assert ((loose / "mccd.sock").stat().st_mode & 0o777) == 0o600
            with MccdClient(loose / "mccd.sock") as cl:
                assert cl.call("x") == {"ok": 1}
        finally:
            srv.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_r6_client_refuses_server_of_another_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    d = Path(tempfile.mkdtemp(prefix="nxipc"))
    srv = IpcServer(d / "s.sock", lambda c, r: {}).start()
    try:
        monkeypatch.setattr(ipc, "_peer_uid", lambda sock: os.getuid() + 1)
        with pytest.raises(PermissionError, match="uid"):
            MccdClient(d / "s.sock", timeout=1.0)
    finally:
        monkeypatch.undo()
        srv.close()
        shutil.rmtree(d, ignore_errors=True)


# ---- R7: TUI stop keys under Caps Lock ----------------------------------------------------------


@pytest.mark.parametrize(("key", "cmd"), [("S", "stop"), ("E", "estop"), ("D", "disarm")])
def test_r7_tui_stop_keys_work_with_caps_lock(key: str, cmd: str) -> None:
    c, be = make_tui()
    c.handle_key("S_RIGHT", 0.0)  # a continuous jog is running
    be.ack()
    c.handle_key(key, 0.05)
    assert (Lane.URGENT, cmd) in [(lane, name) for lane, name, _, _ in be.sent], be.sent
    if cmd != "disarm":
        assert c.cont is None


@pytest.mark.xfail(
    strict=True,
    reason="open decision D11: with Caps Lock on, 'h' (home menu) arrives as 'H' = continuous "
    "jog X- (vi-style binding); needs a key-binding decision",
)
def test_r7_caps_lock_home_key_does_not_start_motion() -> None:
    c, be = make_tui()
    c.handle_key("H", 0.0)  # operator typed 'h' with Caps Lock on
    assert not any(name.startswith("jog") for _, name, _, _ in be.sent)


# ---- R8: m1_session step 7 defaulted to jogging Y towards the end stop -------------------------


def test_r8_m1_step7_empty_direction_answer_plans_no_jog(tmp_path: Path) -> None:
    out = tmp_path / "m1"
    with daemon_on_sim() as (daemon, sim, path):
        op = ScriptedOperator(
            answers={"s7.y_direction": "", "s7.y_direction_negative": "", "s6.start": "n"}
        )
        op.hooks = physical_hooks(sim, None)
        rc = m1.main(argv_for(path, out, "--steps", "7", "--no-dissector"), operator=op)
        assert rc == m1.EXIT_OK
        assert jogs_and_homes(sim) == []
    report = json.loads((out / "report.json").read_text())
    s7 = step(report, 7)
    assert s7["motions"] == [] and s7["status"] == "partial"
    assert s7["results"]["y_jog_direction"] is None
    assert not any(".motion." in k for _, k, _ in op.prompts)


def test_r8_m1_step7_negative_needs_its_own_yes(tmp_path: Path) -> None:
    out = tmp_path / "m1"
    with daemon_on_sim() as (daemon, sim, path):
        op = ScriptedOperator(
            answers={"s7.y_direction": "n", "s7.y_direction_negative": "y"},
            hooks=physical_hooks(sim, None),
        )
        assert m1.main(argv_for(path, out, "--steps", "7", "--no-dissector"), operator=op) == 0
        assert [w for n, w in jogs_and_homes(sim)] == [(3, 1, 20000, 5999, 59990, 2**32 - 5000)]


# ---- R9 (open, D9): arming outlives the client that armed ---------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="open decision D9: MOTION_ARMED persists after the arming connection closes, so a "
    "later client (same uid) moves without its own arm_motion; the one-shot CLI flow "
    "'arm' then 'jog' relies on it",
)
def test_r9_arming_does_not_outlive_the_arming_client() -> None:
    with running() as (daemon, sim, path):
        with MccdClient(path) as a:
            a.call("arm_motion")
        time.sleep(0.1)
        with MccdClient(path) as b:
            wait_ready(daemon)
            with pytest.raises(IpcError):
                b.call("jog_step", slot=0, mm=1.0, speed=20.0)


# ---- stress: poll x commands x stops, every card write went through the gate -------------------


def test_stress_every_card_write_went_through_the_gate_and_estop_is_final() -> None:
    for _round in range(20):
        with (
            running(mccd={"deadman_timeout_ms": 200}) as (daemon, sim, path),
            MccdClient(path) as a,
            MccdClient(path) as b,
        ):
            a.call("arm_motion")
            wait_ready(daemon)
            stop_evt = threading.Event()

            def mover(stop_evt: threading.Event = stop_evt) -> None:
                k = 0
                while not stop_evt.is_set():
                    k += 1
                    try:
                        if k % 3 == 0:
                            a.call(
                                "jog_continuous_start", slot=k % 2, positive=bool(k % 4), speed=20.0
                            )
                            a.call("jog_refresh", slot=k % 2)
                            a.call("jog_continuous_stop", slot=k % 2)
                        else:
                            a.call("jog_step", slot=k % 2, mm=0.2 if k % 4 else -0.2, speed=50.0)
                    except IpcError:
                        pass

            t = threading.Thread(target=mover, daemon=True)
            t.start()
            time.sleep(0.15)
            for cmd in ("stop", "disarm"):
                b.call(cmd)
                a.call("arm_motion")
                time.sleep(0.05)
            b.call("estop")
            n = len(commands(sim))
            time.sleep(0.2)
            stop_evt.set()
            t.join(3)
            late = [v for v in commands(sim)[n:] if v[0] in (C.CMD_JOG, C.CMD_HOME)]
            assert late == []
            sent = {(r.addr, r.words) for r in daemon.gate.write_log if r.decision != "refused"}
            with sim._lock:
                wire = {
                    (r.vector[1], tuple(x & 0xFFFFFFFF for x in r.vector[3:]))
                    for r in sim.requests
                    if r.vector is not None and r.vector[0] == 0x40
                }
            assert wire <= sent, wire - sent
