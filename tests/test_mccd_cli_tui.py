"""``nexcut-mccd tui`` driven headless: key decoding, key -> request mapping, the
continuous-jog deadman (PORT-PLAN §8.2) and responsiveness while the daemon is busy.

No terminal is used: :class:`FakeBackend` records requests with a fake clock, and the
integration tests replay :class:`~nexcut.mccd.tui.ScriptedKeys` against a daemon on the
card simulator.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from nexcut.core.config import NexcutConfig
from nexcut.mcc.simulator import CardSimulator, SimConfig
from nexcut.mccd import tui
from nexcut.mccd.daemon import Link, MccDaemon
from nexcut.mccd.ipc import Connection, IpcServer, MccdClient
from nexcut.mccd.tui import (
    IpcBackend,
    Lane,
    RecordingScreen,
    Reply,
    ScriptedKeys,
    TuiController,
    TuiOptions,
    format_status,
    run_loop,
    tokenize,
)


def wait_for(cond: Callable[[], object], timeout: float = 3.0, period: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(period)
    return bool(cond())


# ---- fake backend ---------------------------------------------------------------------------


class FakeBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[Lane, str, dict[str, Any], str]] = []
        self.refresh: tuple[int | None, float] = (None, 0.0)
        self.refresh_history: list[tuple[int | None, float]] = []
        self.pending: list[Reply] = []
        self.command_busy = False
        self.snapshot: dict[str, Any] | None = None
        self.alive = True
        self.closed = False

    def submit(self, lane: Lane, cmd: str, args: dict[str, Any], tag: str = "") -> bool:
        if lane is Lane.COMMAND and self.command_busy:
            return False
        self.sent.append((lane, cmd, dict(args), tag or cmd))
        return True

    def set_refresh(self, slot: int | None, until: float) -> None:
        self.refresh = (slot, until)
        self.refresh_history.append(self.refresh)

    def replies(self) -> list[Reply]:
        out, self.pending = self.pending, []
        return out

    def status(self) -> tuple[dict[str, Any] | None, bool]:
        return self.snapshot, self.alive

    def close(self) -> None:
        self.closed = True

    # helpers
    def cmds(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [(str(lane), cmd, args) for lane, cmd, args, _ in self.sent]

    def last(self) -> tuple[Lane, str, dict[str, Any], str]:
        return self.sent[-1]

    def ack(self, index: int = -1, result: Any = None, ok: bool = True, code: str = "") -> None:
        lane, cmd, args, tag = self.sent[index]
        self.pending.append(Reply(tag, cmd, args, ok, result, code or None, "nope"))


def make(**opts: Any) -> tuple[TuiController, FakeBackend]:
    be = FakeBackend()
    return TuiController(be, TuiOptions(**opts), clock=lambda: 0.0), be


# ---- key decoding -------------------------------------------------------------------------


def codes(s: str) -> list[int]:
    return [ord(c) for c in s]


def test_tokenize_escape_sequences() -> None:
    assert tokenize(codes("\x1b[A\x1b[B\x1b[C\x1b[D")) == ["UP", "DOWN", "RIGHT", "LEFT"]
    assert tokenize(codes("\x1bOA\x1bOD")) == ["UP", "LEFT"]  # SS3 (application cursor mode)
    assert tokenize(codes("\x1b[1;2A\x1b[1;2B\x1b[1;2C\x1b[1;2D")) == [
        "S_UP", "S_DOWN", "S_RIGHT", "S_LEFT",
    ]  # fmt: skip
    assert tokenize(codes("\x1b[a\x1b[c")) == ["S_UP", "S_RIGHT"]  # rxvt
    assert tokenize(codes("\x1b[1;5C")) == ["UNKNOWN"]  # Ctrl+Right is not a jog key
    assert tokenize(codes("\x1b[5~\x1b[6~\x1b[3~")) == ["PGUP", "PGDN", "UNKNOWN"]
    assert tokenize(codes("\x1b[I\x1b[O")) == ["FOCUS_IN", "FOCUS_OUT"]
    # Lone ESC and ESC + anything else: E-stop wins, the next key is processed on its own.
    assert tokenize([27]) == ["ESC"]
    assert tokenize(codes("\x1bs")) == ["ESC", "s"]
    assert tokenize(codes("\x1b\x1b[A")) == ["ESC", "UP"]
    assert tokenize(codes("m [\r\x7f\x08")) == ["m", " ", "[", "ENTER", "BACKSPACE", "BACKSPACE"]
    assert tokenize([338, 339, 999], {338: "PGDN", 339: "PGUP"}) == ["PGDN", "PGUP", "UNKNOWN"]
    assert tokenize(codes("\x1b[1;2")) == ["UNKNOWN"]  # truncated sequence
    assert tokenize([1]) == ["UNKNOWN"]


def test_parse_axis() -> None:
    assert [tui.parse_axis(a) for a in ("x", "Y", "y2", "Z", "w", "4")] == [0, 1, 2, 3, 4, 4]
    with pytest.raises(ValueError):
        tui.parse_axis("5")


def test_scripted_keys_and_collapse() -> None:
    t = [0.0]
    keys = ScriptedKeys([(0.0, "m"), (0.0, "RIGHT"), (0.5, "q")], clock=lambda: t[0])
    assert keys.read(0.0) == ["m", "RIGHT"]
    assert keys.read(0.0) == [] and not keys.done
    t[0] = 0.6
    assert keys.read(0.0) == ["q"] and keys.done
    assert tui._collapse(["RIGHT", "RIGHT", "RIGHT", "S_UP", "S_UP", "LEFT"]) == [
        "RIGHT", "S_UP", "S_UP", "LEFT",
    ]  # fmt: skip


# ---- key -> request mapping ---------------------------------------------------------------


def test_basic_keys_map_to_requests() -> None:
    c, be = make()
    c.handle_key("m")
    assert be.last()[:3] == (Lane.COMMAND, "arm_motion", {})
    c.handle_key("d")
    assert be.last()[:3] == (Lane.URGENT, "disarm", {})
    c.handle_key("RIGHT")
    assert be.last()[:3] == (Lane.COMMAND, "jog_step", {"slot": 0, "mm": 1.0, "speed": 50.0})
    c.handle_key("LEFT")
    assert be.last()[2]["mm"] == -1.0
    c.handle_key("UP")
    assert be.last()[2] == {"slot": 1, "mm": 1.0, "speed": 50.0}
    c.handle_key("DOWN")
    assert be.last()[2] == {"slot": 1, "mm": -1.0, "speed": 50.0}
    c.handle_key("PGUP")
    assert be.last()[2] == {"slot": 4, "mm": 1.0, "speed": 50.0}  # W lift table, slot 4
    c.handle_key("PGDN")
    assert be.last()[2] == {"slot": 4, "mm": -1.0, "speed": 50.0}
    for _ in range(3):
        c.handle_key("]")
    assert c.step_mm == 10.0
    c.handle_key("RIGHT")
    assert be.last()[2]["mm"] == 10.0
    for _ in range(4):
        c.handle_key("[")
    assert c.step_mm == 0.1
    c.handle_key("LEFT")
    assert be.last()[2]["mm"] == -0.1
    for key, cmd in ((" ", "stop"), ("s", "stop"), ("ESC", "estop"), ("e", "estop"),
                     ("A", "ack_estop")):  # fmt: skip
        c.handle_key(key)
        assert be.last()[:2] == (Lane.URGENT, cmd), key
    n = len(be.sent)
    c.handle_key("x")  # unbound
    assert len(be.sent) == n and "not bound" in c.msg.text
    assert not c.quit
    c.handle_key("q")
    assert c.quit and len(be.sent) == n  # quitting sends nothing to the daemon when idle


def test_home_menu_one_axis_at_a_time() -> None:
    c, be = make()
    c.handle_key("h")
    assert c.mode == "home" and "HOME" in "\n".join(c.lines())
    c.handle_key("x")
    assert be.last()[1:3] == ("home", {"slots": [0]}) and c.mode == "normal"
    c.handle_key("h")
    c.handle_key("y")
    assert be.last()[1:3] == ("home", {"slots": [1]})
    n = len(be.sent)
    c.handle_key("h")
    c.handle_key("a")  # "all" is not offered by default
    assert len(be.sent) == n and c.msg.text == "home cancelled"
    c.handle_key("h")
    c.handle_key("q")  # q cancels the menu, it does not quit
    assert not c.quit and c.mode == "normal"
    c.handle_key("h")
    c.handle_key("ESC")  # E-stop works inside the menu
    assert be.last()[1] == "estop" and c.mode == "normal"

    c2, be2 = make(allow_home_all=True)
    c2.handle_key("h")
    c2.handle_key("a")
    assert be2.last()[1:3] == ("home", {"slots": [0, 1]})


def test_read_block_prompt() -> None:
    c, be = make()
    c.handle_key("r")
    for k in "1000/36":
        c.handle_key(k)
    assert c.read_buffer == "1000/36" and "READ BLOCK ADDR/N: 1000/36_" in c.lines()
    c.handle_key("BACKSPACE")
    c.handle_key("6")
    c.handle_key("ENTER")
    assert be.last()[1:3] == ("read_block", {"addr": 1000, "n": 36}) and c.mode == "normal"
    be.ack(result={"addr": 1000, "words": list(range(36))})
    c.tick(0.0)
    assert c.read_result[0].split()[:3] == ["1000:", "0", "1"]
    c.handle_key("r")
    for k in "12//":
        c.handle_key(k)
    c.handle_key("ENTER")
    assert be.last()[2] == {"addr": 12, "n": 1}  # empty fields are skipped, N defaults to 1
    c.handle_key("r")
    for k in "1/2/3":
        c.handle_key(k)
    n = len(be.sent)
    c.handle_key("ENTER")
    assert len(be.sent) == n and c.msg.error
    c.handle_key("r")
    c.handle_key(" ")  # space still means STOP inside the prompt
    assert be.last()[1] == "stop" and c.mode == "normal"


def test_command_lane_busy_drops_motion_keys() -> None:
    c, be = make()
    be.command_busy = True
    c.handle_key("RIGHT")
    c.handle_key("S_RIGHT")
    c.handle_key("m")
    assert be.sent == [] and c.cont is None
    assert c.msg.error and "NOT sent" in c.msg.text
    c.handle_key(" ")  # the urgent lane is never refused
    assert be.last()[:2] == (Lane.URGENT, "stop")


def test_error_replies_are_shown() -> None:
    c, be = make()
    c.handle_key("RIGHT")
    be.ack(ok=False, code="refused")
    c.tick(0.0)
    assert c.msg.error and c.msg.text == "jog_step: refused: nope"
    assert "! jog_step: refused: nope" in c.lines()


# ---- continuous jog deadman ---------------------------------------------------------------


def test_continuous_jog_refreshes_while_repeats_arrive_then_stops() -> None:
    c, be = make(initial_hold_s=0.7, repeat_gap_s=0.15)
    c.handle_key("S_RIGHT", 0.0)
    assert be.last()[:3] == (
        Lane.COMMAND, "jog_continuous_start", {"slot": 0, "positive": True, "speed": 20.0},
    )  # fmt: skip
    c.tick(0.01)
    assert be.refresh == (None, 0.0) or be.refresh[0] is None  # not before the start is acked
    be.ack()
    c.tick(0.05)
    assert be.refresh == (0, 0.7)  # initial hold covers the auto-repeat delay
    c.tick(0.69)
    assert c.cont is not None
    for t in (0.55, 0.59, 0.63, 0.67):  # repeats (~25 Hz)
        c.handle_key("S_RIGHT", t)
        c.tick(t)
    assert be.refresh == (0, pytest.approx(0.82))
    assert not any(cmd == "jog_continuous_stop" for _, cmd, _ in be.cmds())
    c.tick(0.81)
    assert c.cont is not None
    c.tick(0.83)  # no key for > repeat_gap: released
    assert be.last()[:3] == (Lane.URGENT, "jog_continuous_stop", {"slot": 0})
    assert be.refresh == (None, 0.0) and c.cont is None
    starts = [cmd for _, cmd, _ in be.cmds() if cmd == "jog_continuous_start"]
    assert len(starts) == 1  # repeats never re-send the start (11 §2 V1: one per key press)


def test_continuous_jog_single_press_stops_after_initial_hold() -> None:
    c, be = make(initial_hold_s=0.7)
    c.handle_key("L", 0.0)  # vi-style alternative
    be.ack()
    c.tick(0.1)
    c.tick(0.69)
    assert c.cont is not None
    c.tick(0.70)
    assert be.last()[1:3] == ("jog_continuous_stop", {"slot": 0}) and c.cont is None


@pytest.mark.parametrize("key", ["FOCUS_OUT", "RIGHT", "m", "S_LEFT", "h"])
def test_continuous_jog_stopped_by_focus_loss_or_other_key(key: str) -> None:
    c, be = make()
    c.handle_key("S_UP", 0.0)
    be.ack()
    c.tick(0.05)
    assert be.refresh[0] == 1
    c.handle_key(key, 0.1)
    stops = [a for _, cmd, a in be.cmds() if cmd == "jog_continuous_stop"]
    assert stops == [{"slot": 1}]
    assert c.cont is None and be.refresh[0] is None
    assert not any(cmd == "jog_continuous_start" and a["slot"] == 0 for _, cmd, a in be.cmds())


def test_continuous_jog_estop_and_stop_keys() -> None:
    for key, cmd in (("ESC", "estop"), (" ", "stop")):
        c, be = make()
        c.handle_key("S_DOWN", 0.0)
        be.ack()
        c.tick(0.05)
        c.handle_key(key, 0.1)
        assert be.last()[:2] == (Lane.URGENT, cmd)
        assert c.cont is None and be.refresh[0] is None


def test_start_acknowledged_after_release_is_stopped_again() -> None:
    c, be = make(initial_hold_s=0.3)
    c.handle_key("S_RIGHT", 0.0)
    c.tick(0.31)  # released before the daemon answered
    assert be.last()[1] == "jog_continuous_stop"
    be.ack(index=0)
    c.tick(0.4)
    stops = [a for _, cmd, a in be.cmds() if cmd == "jog_continuous_stop"]
    assert stops == [{"slot": 0}, {"slot": 0}]
    assert be.refresh[0] is None


def test_start_refused_and_deadman_end_clear_the_jog() -> None:
    c, be = make()
    c.handle_key("S_RIGHT", 0.0)
    be.ack(ok=False, code="refused")
    c.tick(0.05)
    assert c.cont is None and c.msg.error and be.refresh[0] is None

    c.handle_key("S_RIGHT", 1.0)
    be.ack()
    c.tick(1.05)
    be.pending.append(Reply("refresh", "jog_refresh", {"slot": 0}, True, {"alive": False}))
    c.tick(1.1)
    assert c.cont is None and "ended by the daemon" in c.msg.text


def test_status_stream_loss_stops_the_jog() -> None:
    c, be = make()
    c.tick(0.0)
    c.handle_key("S_RIGHT", 0.0)
    be.ack()
    c.tick(0.05)
    be.alive = False
    c.tick(0.1)
    assert be.last()[1] == "jog_continuous_stop" and c.cont is None


def test_leased_step_is_refreshed_for_its_duration_only() -> None:
    c, be = make(step_refresh_margin_s=0.3)
    c.handle_key("]", 0.0)
    c.handle_key("]", 0.0)
    c.handle_key("UP", 0.0)
    be.ack(result={"words": [], "deadman": True})
    c.tick(1.0)
    until = 1.0 + 10.0 / 50.0 * 1.25 + 0.3
    assert be.refresh == (1, pytest.approx(until))
    c.tick(until + 0.01)
    assert be.refresh == (None, 0.0) and c.step_refresh is None
    c.handle_key("UP", 2.0)
    be.ack(result={"words": [], "deadman": True})
    c.tick(2.0)
    c.handle_key("FOCUS_OUT", 2.1)
    assert be.refresh == (None, 0.0) and c.step_refresh is None


def test_quit_during_jog_stops_it_and_closes() -> None:
    c, be = make()
    c.handle_key("S_LEFT", 0.0)
    be.ack()
    c.tick(0.05)
    c.handle_key("q", 0.1)  # any other key stops the jog first, then q quits
    assert be.last()[1] == "jog_continuous_stop" and c.quit and c.cont is None
    c.quit = False
    c.handle_key("S_LEFT", 0.2)
    be.ack()
    c.tick(0.25)
    c.shutdown()  # e.g. Ctrl+C / terminal closed while jogging
    assert be.last()[1:3] == ("jog_continuous_stop", {"slot": 0}) and be.closed


# ---- rendering ----------------------------------------------------------------------------


def _snap(**kw: Any) -> dict[str, Any]:
    axes = [
        {"slot": s, "busy": s == 1, "position_counts": p}
        for s, p in enumerate([12345, -500, 0, 7000, 1000])
    ]
    base = {
        "link": "CONNECTED", "machine_state": "READY", "arm_state": "MOTION_ARMED",
        "poll_age_s": 0.012, "estop_latched": False, "k": 1000, "bus_cycle_us": 1000,
        "homed_slots": [0], "axes": axes, "fifo_margin": 4000, "fifo_empty": True,
        "fifo_frame_id": 3, "fifo_running": False, "alarms": [], "watchdog": [],
        "homing": [], "last_error": None,
    }  # fmt: skip
    base.update(kw)
    return base


def test_format_status_lines() -> None:
    lines = format_status(_snap())
    text = "\n".join(lines)
    assert "link CONNECTED" in lines[0] and "arm MOTION_ARMED" in lines[0] and "12 ms" in lines[0]
    assert "homed X* Y- Z- W-" in text and "K=1000 " in text
    assert "FIFO margin 4000 (empty) frame 3" in text
    assert "X     12.345" in text and "Y     -0.500 busy" in text
    assert "Z      7.000" in text and "W      1.000" in text
    assert "alarms: none" in text
    lines = format_status(
        _snap(
            estop_latched=True,
            k=2000,
            bus_cycle_us=None,
            alarms=[
                {"code": 1030, "text": "Emergency stop alarm"},
                {"code": 8100, "text": "X hard positive limit alarm"},
            ],
            watchdog=["E-stop active (1006 bit 30)"],
            homing=[1],
            last_error="boom",
        ),  # fmt: skip
        ipc_connected=False,
    )
    text = "\n".join(lines)
    assert "E-STOP LATCHED" in lines[0] and "IPC STALE" in lines[0]
    assert "K=2000?" in text and "X      6.173" in text and "homing Y" in text
    assert "alarms: 1030 Emergency stop alarm; 8100 X hard positive limit alarm" in text
    assert "watchdog: E-stop active" in text and "last error: boom" in text
    assert "NOT CONNECTED" in format_status(None, ipc_connected=False)[0]


def test_run_loop_headless_with_scripted_keys() -> None:
    be = FakeBackend()
    be.snapshot = _snap()
    c = TuiController(be, TuiOptions())
    screen = RecordingScreen()
    keys = ScriptedKeys([(0.0, "m"), (0.0, "RIGHT"), (0.0, "RIGHT"), (0.05, "q")])
    run_loop(c, keys, screen, frame_s=0.01)
    assert [cmd for _, cmd, _ in be.cmds()] == ["arm_motion", "jog_step"]  # repeat collapsed
    assert be.closed and screen.frames >= 1
    assert any("link CONNECTED" in line for line in screen.last)


# ---- against a real IPC server ------------------------------------------------------------


@contextmanager
def slow_server() -> Iterator[tuple[Path, threading.Event, list[str]]]:
    d = Path(tempfile.mkdtemp(prefix="nxtui"))
    gate = threading.Event()
    seen: list[str] = []

    def handler(conn: Connection, req: dict[str, Any]) -> Any:
        seen.append(req["cmd"])
        if req["cmd"] == "jog_step":
            gate.wait(5.0)  # the daemon is busy (e.g. a write retry ladder)
            return {"words": [], "deadman": False}
        if req["cmd"] == "stop":
            return {"sent": ["stop_all"], "failed": []}
        return {}

    server = IpcServer(d / "s.sock", handler).start()
    try:
        yield server.path, gate, seen
    finally:
        gate.set()
        server.close()
        shutil.rmtree(d, ignore_errors=True)


def test_ui_stays_responsive_while_daemon_is_busy() -> None:
    with slow_server() as (path, gate, seen):
        be = IpcBackend(path)
        c = TuiController(be, TuiOptions())
        try:
            c.handle_key("RIGHT")
            assert wait_for(lambda: "jog_step" in seen)
            t0 = time.monotonic()
            c.handle_key("RIGHT")  # refused client-side: not queued behind the busy request
            c.handle_key("S_RIGHT")
            c.handle_key(" ")
            c.tick()
            lines = c.lines()
            assert time.monotonic() - t0 < 0.1
            assert "(waiting for daemon)" in "\n".join(lines)
            assert wait_for(lambda: "stop" in seen, 1.0)  # urgent lane passes the busy one
            assert wait_for(lambda: any(r.cmd == "stop" for r in be.replies()), 1.0)
            assert seen.count("jog_step") == 1 and "jog_continuous_start" not in seen
            gate.set()
            assert wait_for(lambda: not be.busy(), 2.0)
            assert seen.count("jog_step") == 1  # nothing was executed late
        finally:
            be.close()


def test_backend_reports_unreachable_daemon() -> None:
    d = Path(tempfile.mkdtemp(prefix="nxtui"))
    try:
        be = IpcBackend(d / "absent.sock")
        c = TuiController(be, TuiOptions())
        c.handle_key("m")
        assert be.wait_idle(Lane.COMMAND, 2.0)
        c.tick()
        assert c.msg.error and "ipc" in c.msg.text
        assert be.status() == (None, False)
        assert "NOT CONNECTED" in c.lines()[0]
        be.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


@contextmanager
def daemon_on_sim(**mccd: Any) -> Iterator[tuple[MccDaemon, CardSimulator, Path]]:
    base = NexcutConfig()
    cfg = replace(base, mccd=replace(base.mccd, **mccd))
    sim = CardSimulator(config=SimConfig(motion_time_s=5.0)).start()
    d = Path(tempfile.mkdtemp(prefix="nxtui"))
    daemon = MccDaemon(cfg, card_addr=sim.address, socket_path=d / "s.sock").start()
    try:
        assert daemon.wait_for_link(Link.CONNECTED, 5.0)
        assert wait_for(lambda: daemon.snapshot().machine_state == "READY", 3.0)
        yield daemon, sim, daemon.socket_path
    finally:
        daemon.close()
        sim.stop()
        shutil.rmtree(d, ignore_errors=True)


def axis_stops(sim: CardSimulator, slot: int) -> list[tuple[float, tuple[int, ...]]]:
    with sim._lock:
        reqs = [(r.t, r.vector) for r in sim.requests if r.vector is not None]
    out = []
    for t, v in reqs:
        w = v[3:] if v[:2] == (0x40, 0x65) else ()
        if len(w) == 5 and w[0] == 1 and w[1] == 1 << slot and w[2] == 2:
            out.append((t, w))
    return out


def jog_starts(sim: CardSimulator) -> list[float]:
    with sim._lock:
        return [
            r.t for r in sim.requests
            if r.vector is not None and r.vector[:3] == (0x40, 0x65, 6) and r.vector[3] == 3
        ]  # fmt: skip


def run_tui(path: Path, script: list[tuple[float, str]], opts: TuiOptions,
            until: Callable[[TuiController], bool]) -> TuiController:  # fmt: skip
    be = IpcBackend(path, refresh_period_s=opts.refresh_period_s)
    c = TuiController(be, opts)
    keys = ScriptedKeys(script)
    run_loop(c, keys, RecordingScreen(), frame_s=0.01, should_stop=lambda: until(c))
    return c


def test_tui_continuous_jog_on_simulator_held_then_released() -> None:
    with daemon_on_sim() as (daemon, sim, path):
        opts = TuiOptions(initial_hold_s=0.5, repeat_gap_s=0.15)
        hold = [(0.0, "m")] + [(0.3 + 0.04 * i, "S_RIGHT") for i in range(25)]  # 1 s held
        t_end = [0.0]

        def until(c: TuiController) -> bool:
            if not t_end[0] and jog_starts(sim) and c.cont is None:
                t_end[0] = time.monotonic()
            return bool(t_end[0]) and time.monotonic() - t_end[0] > 0.3

        t0 = time.monotonic()
        run_tui(path, hold, opts, until)
        starts = jog_starts(sim)
        assert len(starts) == 1
        stops = axis_stops(sim, 0)
        assert stops, "jog was never stopped"
        held_for = stops[0][0] - starts[0]
        # key held ~0.96 s after the start: refreshes kept it alive past the 200 ms deadman,
        # and the release was detected within the repeat gap (+ loop / IPC slack)
        assert 0.8 <= held_for <= 1.5, held_for
        assert stops[0][0] - t0 < 2.5
        assert daemon.gate.leases() == {}
        assert daemon.snapshot().axes[0].position_counts == 0  # simulator: stopped mid-move


def test_tui_single_press_outlives_deadman_only_for_initial_hold() -> None:
    with daemon_on_sim() as (daemon, sim, path):
        opts = TuiOptions(initial_hold_s=0.6)
        script = [(0.0, "m"), (0.3, "S_UP")]
        run_tui(path, script, opts, lambda c: bool(axis_stops(sim, 1)) and c.cont is None)
        starts, stops = jog_starts(sim), axis_stops(sim, 1)
        dt = stops[0][0] - starts[0]
        assert 0.45 <= dt <= 1.0, dt  # the 200 ms deadman alone would stop at ~0.2 s


def test_hung_ui_lets_the_daemon_deadman_stop_the_jog() -> None:
    with daemon_on_sim() as (daemon, sim, path):
        opts = TuiOptions(initial_hold_s=0.3)
        be = IpcBackend(path)
        c = TuiController(be, opts)
        try:
            c.handle_key("m")
            assert be.wait_idle(Lane.COMMAND, 2.0)
            c.tick()
            assert wait_for(lambda: daemon.snapshot().machine_state == "READY")
            c.handle_key("S_RIGHT")
            assert be.wait_idle(Lane.COMMAND, 2.0)
            c.tick()  # acked -> refresh until last key + 0.3 s
            assert c.cont is not None and c.cont.acked
            t_hang = time.monotonic()
            # the UI thread now hangs: no tick, no explicit stop
            assert wait_for(lambda: bool(axis_stops(sim, 0)), 2.0)
            assert axis_stops(sim, 0)[0][0] - t_hang < 1.0
            assert daemon.gate.leases() == {}
        finally:
            c.cont = None
            be.close()


def test_tui_read_block_refusals_and_quit_leave_daemon_running() -> None:
    with daemon_on_sim() as (daemon, sim, path):
        script = [(0.0, "m"), (0.2, "r")] + [(0.2, k) for k in "1000/2"] + [(0.2, "ENTER")]
        script += [(0.4, "r")] + [(0.4, k) for k in "151/1"] + [(0.4, "ENTER")]
        script += [(0.6, "PGUP"), (0.8, "h"), (0.8, "x"), (1.2, "q")]
        c = run_tui(path, script, TuiOptions(), lambda c: False)
        assert c.quit
        history = "\n".join(c.msg.history)
        assert "read 1000/2 ok" in history and c.read_result[0].split()[1:] == ["100", "20152"]
        assert "read_block: refused: reg 151" in history  # read allow-list (11 §3.3)
        assert "jog_step: refused: axis 4 is not jog-enabled" in history  # W lift: DENY in M1
        assert (2, 1, 0) in sim.commands  # home X, one axis
        assert not any(v[:2] == (2, 2) or v[:2] == (2, 3) for v in sim.commands)
        with MccdClient(path) as client:
            st = client.call("status")
        assert st["link"] == "CONNECTED" and st["arm_state"] == "MOTION_ARMED"
