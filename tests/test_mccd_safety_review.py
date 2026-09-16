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
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from nexcut.mcc import commands as C
from nexcut.mcc.safety import ArmState
from nexcut.mcc.simulator import CardSimulator, SimConfig
from nexcut.mccd import daemon as daemon_mod
from nexcut.mccd import ipc, tui
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
        # Snapshot before the link is cut, not after LINK_LOST is observed: the watchdog can
        # refuse a refresh before this thread gets scheduled again, and the refusals would then
        # land in the "while the link was up" slice. Up to here the link is healthy by
        # construction, so every refresh in it must have been alive.
        n_alive = len(alive)
        assert n_alive, "the refresher never ran"
        sim.drop_requests(10**9)
        assert wait_for(lambda: daemon.link is Link.LINK_LOST, 3.0)
        time.sleep(4.5)  # longer than one write retry ladder (3.5 s)
        assert False not in alive[:n_alive], alive[:n_alive]
        assert False in alive[n_alive:], alive[n_alive:]  # refresh refused
        # and once refused it never comes back alive: the lease is gone for good
        first_refused = next(i for i, a in enumerate(alive) if a is not True)
        assert all(a is not True for a in alive[first_refused:]), alive[first_refused:]
        n = len(commands(sim))
        with sim._lock:
            sim._drop_requests = 0
        assert daemon.wait_for_link(Link.CONNECTED, 8.0)
        stop.set()
        t.join(2)
        after = commands(sim)[n:]
        stops = [i for i, v in enumerate(after) if is_stop_all(v) or is_axis_stop(v, 0)]
        assert stops, after
        # the lease is dropped around the stop, not necessarily before it reaches the card
        assert wait_for(lambda: daemon.gate.leases() == {}, 3.0), daemon.gate.leases()
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


def test_r11_second_daemon_on_same_card_is_refused() -> None:
    """docs/DECISIONS.md D12: an flock keyed by the card address, not by the socket path.

    Before the fix, a second ``nexcut-mccd`` started with another ``--socket`` drove the
    same card: two gates, two E-stop latches, two deadmen, two sequence counters.
    """
    with running() as (daemon, sim, _path):
        assert daemon.card_lock.held and daemon.card_lock.holder_pid() == os.getpid()
        d = Path(tempfile.mkdtemp(prefix="nxmccd2"))
        other = MccDaemon(daemon.config, card_addr=sim.address, socket_path=d / "s.sock")
        try:
            with pytest.raises(RuntimeError, match="already driving") as exc:
                other.start()
            assert str(os.getpid()) in str(exc.value) and "D12" in str(exc.value)
            assert not (d / "s.sock").exists()  # it never got as far as the IPC socket
        finally:
            other.close()
            shutil.rmtree(d, ignore_errors=True)
        assert daemon.card_lock.held  # the refused daemon did not disturb the holder


def test_r11_same_socket_path_is_refused_too() -> None:
    """The socket path is the second interlock (one daemon per socket, D12)."""
    with running() as (daemon, sim, path):
        other = MccDaemon(daemon.config, card_addr=("127.0.0.1", sim.address[1] + 1),
                          socket_path=path)  # fmt: skip
        try:
            with pytest.raises(RuntimeError, match="already listens"):
                other.start()
        finally:
            other.close()
        assert path.exists()  # the holder's socket survived


def test_r11_card_lock_of_a_sigkilled_daemon_is_not_stale() -> None:
    """A daemon killed with SIGKILL leaves the lock file but not the lock (D12)."""
    sim = CardSimulator().start()
    d = Path(tempfile.mkdtemp(prefix="nxmccd3"))
    lock = daemon_mod.card_lock_path(*sim.address)
    script = textwrap.dedent(f"""
        import sys, time
        from nexcut.mccd.daemon import MccDaemon, Link
        d = MccDaemon(card_addr=({sim.address[0]!r}, {sim.address[1]}),
                      socket_path={str(d / "victim.sock")!r}).start()
        d.wait_for_link(Link.CONNECTED, 10.0)
        print("up", flush=True)
        time.sleep(60)
    """)
    proc = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    try:
        assert proc.stdout is not None and proc.stdout.readline().strip() == "up"
        assert lock.exists() and int(lock.read_text().split()[0]) == proc.pid
        proc.kill()
        proc.wait(10)
        # The file is still there with a dead pid; the kernel dropped the flock with the
        # process, so the next daemon takes it.
        assert lock.exists()
        second = MccDaemon(card_addr=sim.address, socket_path=d / "second.sock").start()
        try:
            assert second.card_lock.held
            assert second.card_lock.holder_pid() == os.getpid()
        finally:
            second.close()
    finally:
        proc.kill()
        sim.stop()
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


def test_r7_caps_lock_home_key_does_not_start_motion() -> None:
    """docs/DECISIONS.md D11: no letter key starts motion, in either case.

    Before the fix 'H' was the vi-style continuous jog X-, so typing the home-menu key 'h'
    with Caps Lock on jogged X- at 20 mm/s for the initial hold (~14 mm).
    """
    c, be = make_tui()
    c.handle_key("H", 0.0)  # operator typed 'h' with Caps Lock on
    assert not any(name.startswith("jog") for _, name, _, _ in be.sent)
    assert c.mode == "home"  # it opened the home menu, exactly as 'h' does


def test_r7_no_printable_key_starts_motion_in_normal_mode() -> None:
    """Every printable key, in both cases, against the whole table (D11).

    The check is exhaustive rather than per-key: the R7 bug was one row of a table that
    nobody read as a whole.
    """
    printable = [chr(c) for c in range(32, 127)]
    for key in printable:
        c, be = make_tui()
        c.handle_key(key, 0.0)
        sent = [name for _, name, _, _ in be.sent]
        assert not any(n.startswith("jog") or n == "home" for n in sent), (key, sent)


def test_r7_key_table_has_no_case_or_motion_trap() -> None:
    """Invariants of :data:`nexcut.mccd.tui.KEY_BINDINGS` (D11)."""
    for mode, table in tui.BINDINGS_BY_MODE.items():
        for key, b in table.items():
            if len(key) != 1 or not key.isalpha():
                continue
            # Caps Lock / a stuck Shift must not change what a key does.
            other = tui.binding_for(key.swapcase(), mode)
            assert other is b, (mode, key, b.action, None if other is None else other.action)
    for b in tui.KEY_BINDINGS:
        if not b.motion:
            continue
        # Motion outside a confirmed menu is bound to named keys only (arrows, Shift+arrow).
        if b.mode in (tui.GLOBAL, tui.MODE_NORMAL):
            assert all(k in tui.KEY_NAMES for k in b.keys), b
        else:  # a menu key is reachable only after the key that opened the menu
            assert b.mode == tui.MODE_HOME
    # Both cases of every printable key are bound, or neither is.
    for b in tui.KEY_BINDINGS:
        for k in b.keys:
            if len(k) == 1 and k.isalpha():
                assert k.swapcase() in b.keys, (b.action, k)


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


_ARMER = """
import sys, time
from nexcut.mccd.ipc import MccdClient
c = MccdClient(sys.argv[1])
print(c.call("arm_motion")["arm_state"], flush=True)
while True:
    time.sleep(0.05)
"""


def test_r9_arming_does_not_outlive_the_arming_client() -> None:
    """docs/DECISIONS.md D9: arming is owned by the IPC connection that asked for it.

    Before the fix MOTION_ARMED was daemon state: after the arming client was gone (TUI
    ``q``, one-shot ``nexcut-mccd arm``, or a crash) any later client of the same uid could
    jog and home without arming anything itself.
    """
    with running() as (daemon, sim, path):
        with MccdClient(path) as a:
            a.call("arm_motion")
            assert daemon.gate.arming.state is ArmState.MOTION_ARMED
        assert wait_for(lambda: daemon.gate.arming.state is ArmState.DISARMED, 3.0)
        with MccdClient(path) as b:
            wait_ready(daemon)
            with pytest.raises(IpcError) as exc:
                b.call("jog_step", slot=0, mm=1.0, speed=20.0)
            assert "D9" in exc.value.message
            # b can arm for itself, and that arming is b's.
            b.call("arm_motion")
            wait_ready(daemon)
            b.call("jog_step", slot=0, mm=1.0, speed=20.0)
        assert wait_for(lambda: daemon.gate.arming.state is ArmState.DISARMED, 3.0)


def test_r9_arming_dies_with_a_killed_arming_client() -> None:
    """The same for a client killed with SIGKILL: no clean close, no disarm request."""
    with running() as (daemon, sim, path):
        wait_ready(daemon)
        proc = subprocess.Popen(
            [sys.executable, "-c", _ARMER, str(path)], stdout=subprocess.PIPE, text=True
        )
        try:
            assert proc.stdout is not None
            assert proc.stdout.readline().strip() == "MOTION_ARMED"
            assert daemon.gate.arming.state is ArmState.MOTION_ARMED
            n = len(commands(sim))
            with MccdClient(path) as b:
                wait_ready(daemon)
                # R12 amendment of D9: the arming belongs to the subprocess's connection,
                # so this one may not move while the machine is armed either.
                with pytest.raises(IpcError) as exc:
                    b.call("jog_step", slot=0, mm=1.0, speed=20.0)
                assert "D9" in exc.value.message
            proc.kill()
            proc.wait(10)
            assert wait_for(lambda: daemon.gate.arming.state is ArmState.DISARMED, 5.0)
            with MccdClient(path) as b:
                wait_ready(daemon)
                with pytest.raises(IpcError):
                    b.call("jog_step", slot=0, mm=1.0, speed=20.0)
            assert [v for v in commands(sim)[n:] if v[0] in (C.CMD_JOG, C.CMD_HOME)] == []
        finally:
            proc.kill()
            proc.wait()


def test_r9_closing_the_arming_client_stops_a_moving_axis() -> None:
    """D9: the disarm on close sends the stop sequence when anything could be moving.

    Since the R12 amendment only the arming connection can start a jog, so it is that
    connection which goes away here; the stop sequence of ``_disarm_and_stop`` still has to
    cover a homing job and anything the gate has a lease for.
    """
    with running() as (daemon, sim, path):
        n = 0
        with MccdClient(path) as armer:
            armer.call("arm_motion")
            wait_ready(daemon)
            armer.call("jog_continuous_start", slot=0, positive=True, speed=20.0)
            n = len(commands(sim))
        assert wait_for(lambda: daemon.gate.arming.state is ArmState.DISARMED, 3.0)
        after = commands(sim)[n:]
        assert any(is_stop_all(v) or is_axis_stop(v, 0) for v in after), after
        # DISARMED is published before the stop sequence releases the per-axis leases
        assert wait_for(lambda: daemon.gate.leases() == {}, 3.0), daemon.gate.leases()


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


# ---- R12 (D9): arming was owned by a connection, but *motion* was not ---------------------------


def _card_motions(sim: CardSimulator) -> list[tuple[int, ...]]:
    return [v for v in commands(sim) if v and v[0] in (C.CMD_JOG, C.CMD_HOME)]


def test_r12_a_foreign_connection_cannot_move_while_another_client_is_armed() -> None:
    """docs/DECISIONS.md D9: arming belongs to the connection that asked for it.

    D9 was implemented for the *lifetime* of arming only: while the arming client was alive,
    any other connection of the same uid still jogged and homed without arming anything
    itself - exactly the "a stray ``nexcut-mccd jog`` moves the machine with no arming step
    of its own" case D9 names as the whole point of the arming state (PORT-PLAN §8.2).
    """
    with running() as (daemon, sim, path), MccdClient(path) as a, MccdClient(path) as b:
        a.call("arm_motion")
        wait_ready(daemon)
        n = len(commands(sim))
        for cmd, args in (
            ("jog_step", {"slot": 0, "mm": 1.0, "speed": 20.0}),
            ("jog_continuous_start", {"slot": 0, "positive": True, "speed": 20.0}),
            ("home", {"slots": [0]}),
        ):
            with pytest.raises(IpcError) as exc:
                b.call(cmd, **args)
            assert exc.value.code == "arming", (cmd, exc.value.code)
            assert "D9" in exc.value.message, (cmd, exc.value.message)
        assert _card_motions(sim)[n:] == []
        # the arming connection still moves
        wait_ready(daemon)
        a.call("jog_step", slot=0, mm=1.0, speed=20.0)


def test_r12_a_client_connecting_while_armed_does_not_inherit_the_arming() -> None:
    """A connection opened *after* arm_motion must arm for itself (D9)."""
    with running() as (daemon, sim, path), MccdClient(path) as a:
        a.call("arm_motion")
        wait_ready(daemon)
        with MccdClient(path) as late:
            with pytest.raises(IpcError) as exc:
                late.call("jog_step", slot=0, mm=1.0, speed=20.0)
            assert "D9" in exc.value.message
            # arm_motion transfers ownership: now it is the late client that may move,
            # and the first one may not.
            late.call("arm_motion")
            wait_ready(daemon)
            late.call("jog_step", slot=0, mm=1.0, speed=20.0)
            wait_ready(daemon)
            with pytest.raises(IpcError) as exc:
                a.call("jog_step", slot=0, mm=1.0, speed=20.0)
            assert "D9" in exc.value.message


def test_r12_a_foreign_connection_cannot_load_or_start_a_job(tmp_path: Path) -> None:
    """The same for the job commands: a job is motion (D5 job stream, D9)."""
    from nexcut.mcc.fifo import write_frame_file
    from test_mccd_job import tick_frame

    job = tmp_path / "job.txt"
    write_frame_file(job, [(1, tick_frame())])
    with running() as (daemon, _sim, path), MccdClient(path) as a, MccdClient(path) as b:
        a.call("arm_motion")
        wait_ready(daemon)
        with pytest.raises(IpcError) as exc:
            b.call("load_job", path=str(job))
        assert exc.value.code == "arming" and "D9" in exc.value.message
        loaded = a.call("load_job", path=str(job))
        with pytest.raises(IpcError) as exc:
            b.call("start_job", token=loaded["token"])
        assert exc.value.code == "arming" and "D9" in exc.value.message
        a.call("stop_job")


def test_r12_bad_arguments_are_still_bad_requests() -> None:
    """The D9 check must not shadow argument validation (a refusal has to name the real fault)."""
    with running() as (_daemon, _sim, path), MccdClient(path) as c:
        for cmd, args in (
            ("jog_step", {"slot": 0, "mm": 1.0}),
            ("jog_continuous_start", {"slot": 0, "positive": 1, "speed": 5.0}),
            ("home", {"slots": []}),
        ):
            with pytest.raises(IpcError) as exc:
                c.call(cmd, **args)
            assert exc.value.code == "bad_request", (cmd, exc.value.code)


# ---- R13/R14/R15 (D12): the card lock could be taken away from under the holder -----------------


def test_r13_a_removed_card_lock_file_does_not_hand_the_card_to_a_second_daemon() -> None:
    """docs/DECISIONS.md D12: the lock must survive the file being deleted.

    The refusal message names the lock path, which invites "just delete the stale lock".
    Before the fix the holder never looked at the file again: after ``rm`` (or any other
    user in a shared ``/tmp/nexcut-<uid>``) a second daemon created a new inode, flocked it
    and drove the same card - two gates, two E-stop latches, undetected.
    """
    with running() as (daemon, sim, _path):
        lock = daemon.card_lock.path
        assert lock.exists()
        lock.unlink()  # operator "cleans a stale lock"
        assert wait_for(lambda: lock.exists(), 10.0), "the holder never restored the lock file"
        other = daemon_mod.CardLock(lock, "the card")
        with pytest.raises(RuntimeError, match="already driving"):
            other.acquire()
        assert daemon.card_lock.held and daemon.card_lock.holder_pid() == os.getpid()


def test_r13_a_stolen_card_lock_disarms_the_daemon() -> None:
    """If another process did win the race, the daemon must stop being a master (D12)."""
    with running() as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        lock = daemon.card_lock.path
        lock.unlink()
        thief = daemon_mod.CardLock(lock, "the card")
        thief.acquire()
        try:
            assert wait_for(lambda: daemon.card_lock_lost is not None, 10.0)
            assert wait_for(lambda: daemon.gate.arming.state is ArmState.DISARMED, 5.0)
            with pytest.raises(IpcError) as exc:
                c.call("arm_motion")
            assert "D12" in exc.value.message
        finally:
            thief.release()


def test_r14_card_lock_refuses_a_foreign_or_symlinked_runtime_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock directory gets the same check as the IPC socket directory (D5 amendment, D12).

    Without ``XDG_RUNTIME_DIR`` both live in ``/tmp/nexcut-<uid>``, which any local user can
    create first; the socket directory was checked, the lock directory was not.
    """
    d = Path(tempfile.mkdtemp(prefix="nxlock"))
    try:
        foreign = d / "foreign"
        foreign.mkdir(mode=0o755)
        real_uid = os.getuid()
        monkeypatch.setattr(ipc.os, "getuid", lambda: real_uid + 1)
        with pytest.raises(RuntimeError, match="belongs to uid"):
            daemon_mod.CardLock(foreign / "card.lock").acquire()
        monkeypatch.undo()
        real = d / "real"
        real.mkdir(mode=0o700)
        (d / "link").symlink_to(real)
        with pytest.raises(RuntimeError, match="not a plain directory"):
            daemon_mod.CardLock(d / "link" / "card.lock").acquire()
        # a lock file that is a symlink is refused too (O_NOFOLLOW)
        ok = d / "ok"
        ok.mkdir(mode=0o700)
        (ok / "card.lock").symlink_to(d / "elsewhere")
        with pytest.raises(RuntimeError, match="cannot open the card lock"):
            daemon_mod.CardLock(ok / "card.lock").acquire()
        # a directory the daemon creates is 0700 and the lock file 0600
        fresh = d / "fresh" / "nexcut"
        lock = daemon_mod.CardLock(fresh / "card.lock")
        lock.acquire()
        try:
            assert (fresh.stat().st_mode & 0o777) == 0o700
            assert ((fresh / "card.lock").stat().st_mode & 0o777) == 0o600
        finally:
            lock.release()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_r13_a_lost_lock_is_released_without_deleting_the_winner_s_file() -> None:
    """``release()`` must not unlink the lock file of the daemon that took the card (D12)."""
    d = Path(tempfile.mkdtemp(prefix="nxlock2"))
    try:
        path = d / "card.lock"
        loser = daemon_mod.CardLock(path)
        loser.acquire()
        path.unlink()
        winner = daemon_mod.CardLock(path)
        winner.acquire()
        assert loser.reassert() == "lost"
        loser.release()
        assert path.exists() and winner.held
        assert winner.holder_pid() == os.getpid()
        winner.release()
        assert not path.exists()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_r15_the_card_lock_key_normalises_the_address() -> None:
    """Two spellings of one numeric address must collide (D12: the key is the card).

    What stays undetectable, and is documented as such in D12: a host *name* and its
    address, a second NIC, NAT, or another machine on the card network.
    """
    p = daemon_mod.card_lock_path
    assert p("10.1.1.168", 8000) == p("::ffff:10.1.1.168", 8000)
    assert p("::1", 8000) == p("0:0:0:0:0:0:0:1", 8000)
    assert p("10.1.1.168", 8000) != p("10.1.1.169", 8000)
    assert p("10.1.1.168", 8000) != p("10.1.1.168", 8001)
    # documented limit: a name is not resolved, so it does not collide with its address
    assert p("laser.local", 8000) != p("10.1.1.168", 8000)


# ---- R17: a stop on a loaded job neither stopped it nor let it go ------------------------------


def test_r17_stop_job_before_start_really_stops_the_job(tmp_path: Path) -> None:
    """A stop must end the job; ``start_job`` after it must not start the card's program.

    Before the fix ``stop_job`` on a job that had never been started left it ``LOADED``
    with a latent stop request: ``start_job`` then cleared the FIFO, streamed the frames and
    sent ``0x67 <- [2]`` before the feeder's own loop noticed the stop (the card saw
    ``clear, start, stop, clear``), and every later ``load_job`` was refused with "a job is
    already loaded (LOADED)" for the life of the daemon.
    """
    from nexcut.mcc.fifo import write_frame_file
    from test_mccd_job import fifo_control, tick_frame

    job = tmp_path / "job.txt"
    write_frame_file(job, [(1, tick_frame())])
    with running() as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        c.call("load_job", path=str(job))
        assert c.call("stop_job")["state"] == "STOPPED"
        assert c.call("job_status")["state"] == "STOPPED"
        with pytest.raises(IpcError) as exc:
            c.call("start_job")
        assert "STOPPED" in exc.value.message
        assert 2 not in fifo_control(sim), "the card was told to start a stopped job"
        # the daemon is not wedged: a new job loads
        assert c.call("load_job", path=str(job))["state"] == "LOADED"
        c.call("stop_job")


def test_r17_a_global_stop_on_a_loaded_job_does_not_wedge_the_daemon(tmp_path: Path) -> None:
    """``stop`` / ``disarm`` on a loaded-but-unstarted job must release the job slot."""
    from nexcut.mcc.fifo import write_frame_file
    from test_mccd_job import tick_frame

    job = tmp_path / "job.txt"
    write_frame_file(job, [(1, tick_frame())])
    for ending in ("stop", "disarm"):
        with running() as (daemon, _sim, path), MccdClient(path) as c:
            c.call("arm_motion")
            wait_ready(daemon)
            c.call("load_job", path=str(job))
            c.call(ending)
            assert c.call("job_status")["state"] == "STOPPED", ending
            c.call("arm_motion")
            assert c.call("load_job", path=str(job))["state"] == "LOADED", ending
            c.call("stop_job")


# ---- R18 (D11): the whole key matrix, every mode, both cases -----------------------------------


def _motion_keys_of(mode: str) -> set[str]:
    """Keys that reach the daemon with a motion command when the TUI is in ``mode``."""
    keys = [chr(c) for c in range(32, 127)] + sorted(tui.KEY_NAMES)
    out = set()
    for key in keys:
        c, be = make_tui(allow_home_all=True)
        c.mode = mode
        c.handle_key(key, 0.0)
        names = [name for _, name, _, _ in be.sent]
        if any(n.startswith("jog_step") or n.startswith("jog_continuous_start") or n == "home"
               for n in names):  # fmt: skip
            out.add(key)
    return out


def test_r18_only_the_documented_keys_start_motion_in_any_mode() -> None:
    """docs/DECISIONS.md D11, exhaustively: mode x key x case.

    ``_motion_keys_of`` feeds a fresh controller every printable character in both cases and
    every symbolic key name, in every mode, and collects the ones that send a motion command.
    Any new binding that starts motion from a letter - the R7 trap - changes one of these
    sets. The home menu is the one place where letters move, and only after the ``h`` that
    opened it (a two-key confirmation shown on screen).
    """
    assert _motion_keys_of(tui.MODE_NORMAL) == {"UP", "DOWN", "LEFT", "RIGHT", "PGUP", "PGDN",
                                                "S_UP", "S_DOWN", "S_LEFT", "S_RIGHT"}  # fmt: skip
    assert _motion_keys_of(tui.MODE_HOME) == {"x", "X", "y", "Y", "b", "B"}
    assert _motion_keys_of(tui.MODE_READ) == set()
    assert _motion_keys_of(tui.MODE_HELP) == set()


def test_r18_no_escape_sequence_decodes_to_a_motion_key_by_accident() -> None:
    """Modifier chords must not decode to the four Shift+arrow jog keys (D11, tokenize)."""
    jog_names = {"S_UP", "S_DOWN", "S_LEFT", "S_RIGHT"}
    for final in "ABCD":
        for mod in ("3", "4", "5", "6", "7", "8", "9"):  # Alt / Ctrl / Meta combinations
            assert tui.tokenize([ord(x) for x in f"\x1b[1;{mod}{final}"]) == ["UNKNOWN"]
        assert tui.tokenize([ord(x) for x in f"\x1b[1;2{final}"])[0] in jog_names
    # Alt+<letter> is E-stop plus the plain letter, never a chord that moves
    for letter in "hHxXyYbB":
        assert tui.tokenize([27, ord(letter)]) == ["ESC", letter]
    # control codes (Ctrl+<letter>) never produce a bound motion key
    for code in range(1, 32):
        assert tui.tokenize([code])[0] in {"UNKNOWN", "ENTER", "BACKSPACE", "ESC"}


# ---- R19 (D9): every way a client can vanish, and the one way it cannot ------------------------


def test_r19_a_half_closed_connection_disarms() -> None:
    """A client that shuts its write side down (half close) is gone as far as D9 goes.

    The daemon's reader sees EOF and runs ``_on_close`` - the same path as a clean close or
    a SIGKILL. The client can still read, so it would otherwise sit there armed for ever.
    """
    import socket as _socket

    with running() as (daemon, _sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        assert daemon.gate.arming.state is ArmState.MOTION_ARMED
        c.sock.shutdown(_socket.SHUT_WR)
        assert wait_for(lambda: daemon.gate.arming.state is ArmState.DISARMED, 5.0)


def test_r19_a_frozen_arming_client_keeps_the_arming_but_moves_nothing() -> None:
    """The documented residual of D9: a client that is alive but stuck holds the arming.

    D9 rejected an idle timeout, so a SIGSTOPped (or hung) client leaves the machine armed -
    its socket is open, so no close event ever arrives. What must still hold: no other
    connection can move the machine (R12), and stop / E-stop / disarm from any connection
    still work, so an operator is never locked out.
    """
    script = "\n".join(
        [
            "import sys, signal, os",
            "from nexcut.mccd.ipc import MccdClient",
            "c = MccdClient(sys.argv[1])",
            "print(c.call('arm_motion')['arm_state'], flush=True)",
            "os.kill(os.getpid(), signal.SIGSTOP)",
            "c.call('ping')",
        ]
    )
    with running() as (daemon, _sim, path):
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(path)], stdout=subprocess.PIPE, text=True
        )
        try:
            assert proc.stdout is not None
            assert proc.stdout.readline().strip() == "MOTION_ARMED"
            assert wait_for(lambda: proc.poll() is None, 1.0)
            time.sleep(0.4)  # the frozen client is not going to disconnect
            assert daemon.gate.arming.state is ArmState.MOTION_ARMED
            with MccdClient(path) as other:
                wait_ready(daemon)
                with pytest.raises(IpcError) as exc:
                    other.call("jog_step", slot=0, mm=1.0, speed=20.0)
                assert "D9" in exc.value.message
                other.call("estop")  # an operator is never locked out by a stuck client
                assert daemon.gate.arming.state is ArmState.DISARMED
                assert daemon.gate.arming.estop_latched
        finally:
            proc.kill()
            proc.wait(10)


def test_r19_the_job_dies_with_the_client_that_loaded_it(tmp_path: Path) -> None:
    """A client killed mid-job takes the job with it (D9 + D5 job stream)."""
    from nexcut.mcc.fifo import write_frame_file
    from test_mccd_job import fifo_control, tick_frame

    job = tmp_path / "job.txt"
    write_frame_file(job, [(i, tick_frame()) for i in range(1, 60)])
    with running(sim_config=SimConfig(tick_s=0.004, fifo_starvation_alarm=False)) as (
        daemon,
        sim,
        path,
    ):
        c = MccdClient(path, timeout=20.0)
        c.call("arm_motion")
        wait_ready(daemon)
        loaded = c.call("load_job", path=str(job))
        c.call("start_job", token=loaded["token"])
        assert wait_for(lambda: daemon.job is not None and daemon.job.stats.frames_sent > 0, 10.0)
        c.close()  # the operator's client is gone
        assert wait_for(lambda: daemon.job is not None and daemon.job.closed, 10.0)
        assert daemon.gate.arming.state is ArmState.DISARMED
        assert not daemon.gate.fifo_running
        assert fifo_control(sim)[-1] in (1, 3), fifo_control(sim)


# ---- R20: what the card is told when the job stream goes wrong ---------------------------------


def test_r20_a_grammar_violation_in_the_middle_of_a_job_never_reaches_the_card(
    tmp_path: Path,
) -> None:
    """11 §5.2: a frame the planner got wrong stops the job; only good frames are sent.

    ``load_job`` reads the file lazily, so an item outside the grammar is found *while*
    streaming. The job must fail there, the bad words must not be written, and the card's
    program must be stopped and cleared.
    """
    from nexcut.mcc.fifo import item_header, write_frame_file
    from nexcut.mcc.framing import REG_FIFO_DATA
    from test_mccd_job import fifo_control, run_to_end, tick_frame

    good = tick_frame()
    bad = daemon_mod.PackedFrame((item_header(4242, 1), 0), 0, 1)
    job = tmp_path / "mixed.txt"
    # Past ``FeederConfig.buffer_frames`` (128), so the bad frame is pulled from the source
    # while the job is already streaming - not while the ring is first filled.
    frames = [(i, good) for i in range(1, 141)]
    write_frame_file(job, [*frames, (141, bad), *frames])
    with running(sim_config=SimConfig(tick_s=0.0002, fifo_starvation_alarm=False)) as (
        daemon,
        sim,
        path,
    ), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        loaded = c.call("load_job", path=str(job))
        with contextlib.suppress(IpcError):
            c.call("start_job", token=loaded["token"])
        js = run_to_end(c)
        assert js["state"] == "FAILED" and "4242" in (js["error"] or "")
        # FAILED is the producer's last state; the closing 0x67 <- [3] / [1] are sent by the
        # feeder thread afterwards (JobFeeder.closed, docs/STATUS.md §6).  Wait for the thread
        # to leave before reading what the card was told, or this races on any machine.
        assert wait_for(lambda: daemon._job is not None and daemon._job.closed, 10.0)
        written = [r for r in daemon.gate.write_log if r.addr == REG_FIFO_DATA]
        assert written, "the job never streamed, so the mid-file case was not exercised"
        assert all(4242 not in r.words for r in written), "a refused frame reached the card"
        assert all(r.decision != "refused" for r in written)
        assert fifo_control(sim)[-1] == 1, fifo_control(sim)  # stopped and cleared
        assert not daemon.gate.fifo_running


def test_r20_every_kind_of_laser_record_is_neutralised_on_the_wire(tmp_path: Path) -> None:
    """PORT-PLAN §8.2 dry run, checked against what the simulator actually received.

    One frame carries every laser-bearing record the 11 §5.2 grammar allows: a cutting duty
    on the tick, the CO2 laser DO together with a motion DO in one record, the two PWM
    sub-records and a DA on both candidate laser channels (both are stripped because the
    in-stream channel base is UNVERIFIED, ``SafetyConfig.fifo_laser_da_channel_words``).
    """
    from nexcut.mcc.fifo import item_header, write_frame_file
    from nexcut.mcc.framing import REG_FIFO_DATA
    from nexcut.mcc.registers import Status
    from test_mccd_job import run_to_end, tick_words

    hot = (
        *tick_words(duty=70),
        item_header(9999, 3), C.MISC_DO, 0x101, 0x101,   # DO9 (laser) + DO1 (motion lamp)
        item_header(9999, 3), C.MISC_PWM, 5000, 70,
        item_header(9999, 3), C.MISC_PWM_5V, 5000, 70,
        item_header(9999, 3), C.MISC_DA, 0, 5000,
        item_header(9999, 3), C.MISC_DA, 1, 5000,
        *tick_words(duty=99),
    )  # fmt: skip
    job = tmp_path / "hot.txt"
    write_frame_file(job, [(1, daemon_mod.PackedFrame(hot, 0, 7))])
    sim_cfg = SimConfig(tick_s=0.0002, fifo_starvation_alarm=False, consumed_log_limit=100)
    with running(sim_config=sim_cfg) as (daemon, sim, path), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        loaded = c.call("load_job", path=str(job))
        c.call("start_job", token=loaded["token"])
        assert run_to_end(c)["state"] == "DONE"
        items = sim.consumed_items
        assert [i.opcode for i in items] == [3000, 9999, 3000], [i.opcode for i in items]
        assert all(i.tick[3] == 0 for i in items if i.tick)  # no duty ever reached the card
        assert items[1].args == (C.MISC_DO, 0x1, 0x1)  # the laser bit is gone, DO1 remains
        assert not (sim.outputs & 0x100) and sim.registers[Status.PWM_FREQ.addr] == 0
        # and no word carrying the cutting duty was ever written to 0x66
        for r in daemon.gate.write_log:
            if r.addr != REG_FIFO_DATA or r.decision != "sent":
                continue
            assert all(w & 0xFFFF not in (70, 99) for w in r.words), r.words


def test_r21_an_estop_mid_job_stops_the_program_and_no_leftover_can_run(tmp_path: Path) -> None:
    """PORT-PLAN §8.2: an E-stop must stop the card's FIFO program, and what stays queued
    in the card must be unable to run later.

    ``0x67 <- [1]`` (clear) needs ``MOTION_ARMED``, and an E-stop disarms first, so the
    feeder's clean stop only gets the ``[3]``: the card keeps the rest of the program. That
    is the documented deviation (``mccd.feeder`` module docstring); what makes it safe is
    that only a feeder ever sends ``0x67 <- [2]`` and every feeder clears the FIFO before
    its first frame. Both halves are pinned here.
    """
    from nexcut.mcc.fifo import write_frame_file
    from test_mccd_job import fifo_control, tick_frame

    job = tmp_path / "job.txt"
    write_frame_file(job, [(i, tick_frame()) for i in range(1, 80)])
    with running(sim_config=SimConfig(tick_s=0.004, fifo_starvation_alarm=False)) as (
        daemon,
        sim,
        path,
    ), MccdClient(path) as c:
        c.call("arm_motion")
        wait_ready(daemon)
        loaded = c.call("load_job", path=str(job))
        c.call("start_job", token=loaded["token"])
        assert wait_for(lambda: daemon.job is not None and daemon.job.stats.frames_sent > 0, 10.0)
        c.call("estop")
        assert wait_for(lambda: daemon.job is not None and daemon.job.closed, 10.0)
        assert 3 in fifo_control(sim) and not daemon.gate.fifo_running
        c.call("ack_estop")
        c.call("arm_motion")
        wait_ready(daemon)
        n = len(vectors_of(sim))
        loaded = c.call("load_job", path=str(job))
        c.call("start_job", token=loaded["token"])
        assert wait_for(lambda: daemon.job is not None and daemon.job.stats.frames_sent > 0, 10.0)
        after = vectors_of(sim)[n:]
        clear = next(i for i, v in enumerate(after) if v[:4] == (0x40, 0x67, 1, 1))
        first_frame = next(i for i, v in enumerate(after) if v[:2] == (0x40, 0x66))
        assert clear < first_frame, "a new job streamed before it cleared the card FIFO"
        c.call("stop_job")


def vectors_of(sim: CardSimulator) -> list[tuple[int, ...]]:
    """Every request vector the simulator received (``(func, reg, n, *words)``)."""
    with sim._lock:
        return [r.vector for r in sim.requests if r.vector is not None]
