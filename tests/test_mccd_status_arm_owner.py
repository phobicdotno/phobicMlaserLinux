"""``StatusSnapshot.arm_owner`` end to end: daemon -> IPC -> TUI status line (D9/R12).

Since the R12 amendment of D9 the connection that armed is the only one that may jog, home
or start a job, so "ARMED" alone no longer tells an operator whether *they* can move.  These
tests pin the three places that carry the distinction: the snapshot fields, the per-connection
computation in the ``status`` reply and in pushed ``status`` events, and the TUI status line.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from nexcut.core.config import NexcutConfig
from nexcut.mcc.simulator import CardSimulator
from nexcut.mccd.daemon import Link, MccDaemon
from nexcut.mccd.ipc import IpcError, MccdClient
from nexcut.mccd.status import StatusSnapshot
from nexcut.mccd.tui import Lane, Reply, TuiController, TuiOptions, arm_text, format_status


def wait_for(cond: Callable[[], object], timeout: float = 5.0, period: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(period)
    return bool(cond())


@contextmanager
def running() -> Iterator[tuple[MccDaemon, Path]]:
    sim = CardSimulator().start()
    d = Path(tempfile.mkdtemp(prefix="nxarmowner"))
    daemon = MccDaemon(NexcutConfig(), card_addr=sim.address, socket_path=d / "mccd.sock").start()
    try:
        assert daemon.wait_for_link(Link.CONNECTED, 5.0), daemon.last_error
        assert wait_for(lambda: daemon.axis_ro is not None)
        yield daemon, daemon.socket_path
    finally:
        daemon.close()
        sim.stop()
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------------------------ the snapshot fields


def test_snapshot_defaults_are_unowned() -> None:
    """A snapshot built without a connection knows the owner but not whether it is "you"."""
    s = StatusSnapshot.build(
        t=1.0,
        link="CONNECTED",
        arm_state="DISARMED",
        estop_latched=False,
        poll_age_s=0.0,
        block1000=None,
        axis_ro=None,
    )
    assert s.arm_owner is None and s.arm_owner_is_self is None
    assert s.to_json()["arm_owner"] is None
    assert "arm_owner_is_self" in s.to_json()


# ------------------------------------------------------------------ over the IPC


def test_status_reply_names_the_arming_owner_per_connection() -> None:
    """The armer sees ``arm_owner_is_self``; a second client sees the same id, not itself."""
    with running() as (_daemon, path), MccdClient(path) as a, MccdClient(path) as b:
        assert a.call("status")["arm_owner"] is None
        owner = a.call("arm_motion")["arm_owner"]
        assert isinstance(owner, int)

        sa = a.call("status")
        assert sa["arm_owner"] == owner and sa["arm_owner_is_self"] is True
        assert sa["arm_state"] == "MOTION_ARMED"

        sb = b.call("status")
        assert sb["arm_owner"] == owner and sb["arm_owner_is_self"] is False

        # ... and that is exactly the connection that may move (D9/R12).
        with pytest.raises(IpcError) as exc:
            b.call("jog_step", slot=0, mm=1.0, speed=5.0)
        assert exc.value.code == "arming"

        # a hand-over moves the id with the right to move
        owner_b = b.call("arm_motion")["arm_owner"]
        assert owner_b != owner
        assert a.call("status")["arm_owner_is_self"] is False
        assert b.call("status")["arm_owner_is_self"] is True

        b.call("disarm")
        assert b.call("status")["arm_owner"] is None
        assert b.call("status")["arm_owner_is_self"] is False


def test_status_events_carry_a_per_subscriber_arm_owner_is_self() -> None:
    """The publish loop builds one snapshot; ``arm_owner_is_self`` is still per subscriber."""
    with running() as (_daemon, path), MccdClient(path) as a, MccdClient(path) as b:
        owner = a.call("arm_motion")["arm_owner"]
        for c in (a, b):
            c.call("subscribe", interval_ms=20)

        def first_event(c: MccdClient) -> dict[str, object]:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                ev = c.next_event(0.5)
                if ev is not None and ev.get("event") == "status":
                    data = ev["data"]
                    assert isinstance(data, dict)
                    return data
            raise AssertionError("no status event")

        ea, eb = first_event(a), first_event(b)
        assert ea["arm_owner"] == owner == eb["arm_owner"]
        assert ea["arm_owner_is_self"] is True
        assert eb["arm_owner_is_self"] is False


# ------------------------------------------------------------------ the TUI status line


class _TuiBackend:
    """Minimal :class:`~nexcut.mccd.tui.Backend`: records submits, replays replies."""

    def __init__(self) -> None:
        self.sent: list[tuple[Lane, str, dict[str, Any], str]] = []
        self.pending: list[Reply] = []
        self.snapshot: dict[str, Any] = {}

    def submit(self, lane: Lane, cmd: str, args: dict[str, Any], tag: str = "") -> bool:
        self.sent.append((lane, cmd, dict(args), tag or cmd))
        return True

    def set_refresh(self, slot: int | None, until: float) -> None:
        pass

    def replies(self) -> list[Reply]:
        out, self.pending = self.pending, []
        return out

    def status(self) -> tuple[dict[str, Any] | None, bool]:
        return self.snapshot or None, True

    def close(self) -> None:
        pass

    def reply(self, cmd: str, result: dict[str, Any]) -> None:
        self.pending.append(Reply(cmd, cmd, {}, True, result))


def test_arm_text_says_which_session_holds_the_arming() -> None:
    assert arm_text({"arm_state": "DISARMED", "arm_owner": None}) == "DISARMED"
    armed = {"arm_state": "MOTION_ARMED", "arm_owner": 4}
    # the one-shot / m1 case: the daemon computed it for this very connection
    assert arm_text({**armed, "arm_owner_is_self": True}) == "MOTION_ARMED (this session)"
    assert arm_text({**armed, "arm_owner_is_self": False}) == "MOTION_ARMED (another client)"
    # the TUI case: status arrives on a different connection than the one that armed
    assert arm_text({**armed, "arm_owner_is_self": False}, 4) == "MOTION_ARMED (this session)"
    assert arm_text({**armed, "arm_owner_is_self": False}, 5) == "MOTION_ARMED (another client)"
    # armed by an in-process caller: owned by nobody, so neither claim is made
    assert arm_text({"arm_state": "MOTION_ARMED", "arm_owner": None}) == "MOTION_ARMED"
    # a disarmed machine never carries an owner suffix, whatever the client remembers
    assert arm_text({"arm_state": "DISARMED", "arm_owner": 4}, 4) == "DISARMED"


def test_format_status_head_carries_the_owner() -> None:
    snap = {
        "arm_state": "MOTION_ARMED",
        "arm_owner": 2,
        "arm_owner_is_self": False,
        "link": "CONNECTED",
        "machine_state": "READY",
        "poll_age_s": 0.01,
    }
    assert "arm MOTION_ARMED (another client)" in format_status(snap)[0]
    assert "arm MOTION_ARMED (this session)" in format_status(snap, own_arm_owner=2)[0]


def test_tui_controller_remembers_its_own_arm_owner() -> None:
    """The TUI arms on its command lane and reads status on another connection (D9/R12).

    So the id from its own ``arm_motion`` reply - not the snapshot's per-connection
    ``arm_owner_is_self``, which is about the *status* connection - decides the suffix.
    """
    be = _TuiBackend()
    c = TuiController(be, TuiOptions(), clock=lambda: 0.0)
    be.snapshot = {
        "arm_state": "DISARMED",
        "arm_owner": None,
        "arm_owner_is_self": False,
        "link": "CONNECTED",
        "machine_state": "READY",
        "poll_age_s": 0.01,
    }
    assert "arm DISARMED" in c.lines()[0]

    c.handle_key("m")  # arm motion
    assert be.sent[-1][1] == "arm_motion"
    be.reply("arm_motion", {"arm_state": "MOTION_ARMED", "arm_owner": 11})
    c.tick(0.0)
    # the status stream is a different connection, so the daemon says "not you" there
    be.snapshot = {**be.snapshot, "arm_state": "MOTION_ARMED", "arm_owner": 11}
    assert "arm MOTION_ARMED (this session)" in c.lines()[0]

    # another client takes the arming over: the id moves, the suffix flips
    be.snapshot = {**be.snapshot, "arm_owner": 12}
    assert "arm MOTION_ARMED (another client)" in c.lines()[0]

    # and a DISARMED snapshot forgets the id, so a restarted daemon cannot reuse it
    be.snapshot = {**be.snapshot, "arm_state": "DISARMED", "arm_owner": None}
    assert "arm DISARMED" in c.lines()[0]
    assert c.arm_owner is None
    be.snapshot = {**be.snapshot, "arm_state": "MOTION_ARMED", "arm_owner": 11}
    assert "arm MOTION_ARMED (another client)" in c.lines()[0]
