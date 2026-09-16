"""Native job streaming in ``nexcut-mccd`` against the card simulator (11 §5, A3 §7).

Everything here is **simulator only** (PORT-PLAN §8, docs/DECISIONS.md D3): no test opens a
socket to a real card, and every job is a dry run - laser records are stripped when the job
is loaded and again by the safety gate on the way out (PORT-PLAN §8.2, D5).

Timing bounds are loose and every wait polls a condition: CI runners are slower than the
development laptop.
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

import numpy as np
import pytest

from nexcut.core.config import NexcutConfig
from nexcut.mcc import commands as C
from nexcut.mcc.dissector import parse_fifo_words
from nexcut.mcc.fifo import PackedFrame, item_header, write_frame_file
from nexcut.mcc.registers import Status
from nexcut.mcc.safety import ArmState, SafetyViolation
from nexcut.mcc.simulator import ALARM2_FIFO_STARVATION, CardSimulator, SimConfig
from nexcut.mccd.daemon import IPC_COMMANDS, Link, MccDaemon
from nexcut.mccd.feeder import FeederConfig, JobFeeder, JobState, frames_from_words, scan_items
from nexcut.mccd.ipc import IpcError, MccdClient

TICK_HEADER = item_header(3000, 2)
"""``3000`` with a 2-word payload (11 §5.2)."""


def tick_words(dx: int = 1, dy: int = 1, freq: int = 5000, duty: int = 0) -> tuple[int, ...]:
    """One interpolation tick item (A3 §4.2)."""
    return (TICK_HEADER, ((dy & 0xFFFF) << 16) | (dx & 0xFFFF), (freq << 16) | duty)


def tick_frame(n: int = 99, duty: int = 0) -> PackedFrame:
    """A full 297-word frame of ``n`` ticks (the 300-word flush rule, 11 §5.1)."""
    data: tuple[int, ...] = ()
    for _ in range(n):
        data += tick_words(duty=duty)
    return PackedFrame(data, 0, n)


def wait_for(cond: Callable[[], object], timeout: float = 5.0, period: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(period)
    return bool(cond())


@contextmanager
def running(
    *,
    sim_config: SimConfig | None = None,
    mccd: dict[str, Any] | None = None,
    arm: bool = True,
) -> Iterator[tuple[MccDaemon, CardSimulator, MccdClient]]:
    """Daemon + simulator + one armed client connection.

    The connection stays open for the whole job: arming dies with the connection that asked
    for it (docs/DECISIONS.md D9), and so does the job.
    """
    base = NexcutConfig()
    cfg = replace(base, mccd=replace(base.mccd, **(mccd or {})))
    sim = CardSimulator(
        config=sim_config or SimConfig(tick_s=0.001, fifo_starvation_alarm=False)
    ).start()
    d = Path(tempfile.mkdtemp(prefix="nxjob"))
    daemon = MccDaemon(cfg, card_addr=sim.address, socket_path=d / "mccd.sock").start()
    client: MccdClient | None = None
    try:
        assert daemon.wait_for_link(Link.CONNECTED, 10.0), daemon.last_error
        assert wait_for(lambda: daemon.axis_ro is not None)
        client = MccdClient(daemon.socket_path, timeout=30.0)
        if arm:
            client.call("arm_motion")
        yield daemon, sim, client
    finally:
        if client is not None:
            client.close()
        daemon.close()
        sim.stop()
        shutil.rmtree(d, ignore_errors=True)


def start_job(client: MccdClient, token: str, timeout: float = 15.0) -> dict[str, Any]:
    """``start_job``, retried while the daemon answers ``busy``.

    ``_require_ready`` wants an axis poll (2000/50, ~90 ms) *newer* than the last motion
    command, so a job started right after another one wrote to the card is refused for up to
    one poll period. A real client has the same obligation; retrying is what it would do.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return client.call("start_job", token=token)
        except IpcError as exc:
            if exc.code != "busy" or time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def run_to_end(client: MccdClient, timeout: float = 30.0) -> dict[str, Any]:
    """Poll ``job_status`` until the job reaches a final state."""
    deadline = time.monotonic() + timeout
    js: dict[str, Any] = {}
    while time.monotonic() < deadline:
        js = client.call("job_status")
        if js["state"] in ("DONE", "STOPPED", "FAILED"):
            return js
        time.sleep(0.02)
    return js


def vectors(sim: CardSimulator) -> list[tuple[int, ...]]:
    with sim._lock:
        return [r.vector for r in sim.requests if r.vector is not None]


def fifo_control(sim: CardSimulator) -> list[int]:
    """The ``0x67`` sub-commands the card received, in order (04 §3.5)."""
    return [v[3] for v in vectors(sim) if len(v) >= 4 and v[0] == 0x40 and v[1] == 0x67]


# ---- the IPC surface ------------------------------------------------------------------------


def test_job_commands_are_part_of_the_vocabulary() -> None:
    """D5: the command set is fixed and enumerated; nothing here is a raw register write."""
    for cmd in ("load_job", "start_job", "pause_job", "stop_job", "job_status"):
        assert cmd in IPC_COMMANDS
    assert "arm_laser" not in IPC_COMMANDS


def test_job_status_without_a_job() -> None:
    with running() as (_daemon, _sim, client):
        assert client.call("job_status") == {"state": "NONE"}
        with pytest.raises(IpcError) as exc:
            client.call("start_job")
        assert exc.value.code == "refused"


# ---- arming gate ----------------------------------------------------------------------------


def test_load_job_needs_motion_armed(tmp_path: Path) -> None:
    """The gate refuses streaming unless MOTION_ARMED (11 §3.1 row 0x66 -> Policy.MOTION)."""
    path = tmp_path / "job.txt"
    write_frame_file(path, [(1, tick_frame())])
    with running() as (daemon, _sim, client):
        client.call("disarm")
        assert daemon.gate.arming.state is ArmState.DISARMED
        with pytest.raises(IpcError) as exc:
            client.call("load_job", path=str(path))
        assert exc.value.code == "arming" and "MOTION_ARMED" in exc.value.message


def test_load_job_is_refused_while_laser_armed(tmp_path: Path) -> None:
    """D5 stands: no job may be built while the laser is armed, in this phase.

    ``LASER_ARMED`` has no IPC path at all, so it is forced in-process here; the refusal is
    the second line of defence behind "there is no ``arm_laser`` command".
    """
    path = tmp_path / "job.txt"
    write_frame_file(path, [(1, tick_frame())])
    with running() as (daemon, _sim, client):
        token = daemon.gate.arming.begin_job()
        daemon.gate.arming.arm_laser(confirmed=True, job_token=token)
        assert daemon.gate.arming.state is ArmState.LASER_ARMED
        with pytest.raises(IpcError) as exc:
            client.call("load_job", path=str(path))
        assert exc.value.code == "refused" and "LASER_ARMED" in exc.value.message
        daemon.gate.arming.end_job()


def test_feeder_refuses_to_start_while_laser_armed() -> None:
    """Same rule one level down, where the streaming thread itself checks it."""

    class _Arming:
        state = ArmState.LASER_ARMED
        estop_latched = False
        job_token = "t"

        def allows(self, policy: object) -> bool:
            return True

    class _Gate:
        arming = _Arming()

    feeder = JobFeeder(_Gate(), lambda: None, [tick_frame()], token="t")
    with pytest.raises(Exception) as exc:
        feeder.start()
    assert "LASER_ARMED" in str(exc.value)


# ---- the happy path -------------------------------------------------------------------------


def test_job_file_streams_and_the_card_executes_the_planned_items(tmp_path: Path) -> None:
    """End to end: frame file -> load_job -> start_job -> the simulator's item stream.

    The diff is the M4 gate-1 question "did the card execute exactly what was planned"
    (PORT-PLAN §4 M4), on the simulator only.
    """
    frames = [tick_frame() for _ in range(8)]
    path = tmp_path / "job.txt"
    write_frame_file(path, [((i + 1), f) for i, f in enumerate(frames)], ["test job"])
    sim_cfg = SimConfig(tick_s=0.0002, fifo_starvation_alarm=False, consumed_log_limit=10_000)
    with running(sim_config=sim_cfg) as (daemon, sim, client):
        loaded = client.call("load_job", path=str(path))
        assert loaded["frames"] == 8 and len(loaded["token"]) >= 16
        assert client.call("start_job", token=loaded["token"])["state"] == "RUNNING"
        js = run_to_end(client)
        assert js["state"] == "DONE", js["error"]
        # 'DONE' is the producer's last state; it is set before the feeder thread sends the
        # closing 0x67 pair and hands the job token back (JobFeeder.closed). Everything below
        # looks at what the card saw at the very end, so wait for the thread first.
        assert daemon.job is not None and daemon.job.join(5.0)
        assert js["stats"]["frames_sent"] == 8
        assert js["stats"]["ticks_sent"] == 8 * 99
        assert js["stats"]["resends"] == 0
        # the card ran exactly the planned words, in order
        planned: list[int] = []
        for f in frames:
            planned.extend(f.data)
        assert sim.consumed_words() == planned
        assert sim.snapshot()["ticks_consumed"] == 8 * 99
        # 0x67: clear, start, then stop + clear at the end (module docstring of mccd.feeder)
        assert fifo_control(sim)[:2] == [C.FIFO_CLEAR, C.FIFO_START]
        assert fifo_control(sim)[-2:] == [C.FIFO_STOP, C.FIFO_CLEAR]
        assert sim.snapshot()["queued"] == 0
        # the job token is gone, the machine is still armed (a job end is not a disarm)
        assert daemon.gate.arming.job_token is None
        assert daemon.gate.arming.state is ArmState.MOTION_ARMED


def test_a_second_job_can_start_as_soon_as_the_first_reports_done() -> None:
    """The clean stop of job 1 must not reach into job 2 (docs/DECISIONS.md D5, task 8).

    ``job_status`` reports ``DONE`` while the feeder thread is still sending its closing
    ``0x67 <- [3]`` / ``0x67 <- [1]`` and giving the job token back, so a client that acts on
    ``DONE`` at once used to be able to load a second job whose queue the first job then
    cleared. :meth:`MccDaemon.load_job_frames` now waits for ``JobFeeder.closed``.
    """
    n = 4
    frame = tick_frame()
    with running(
        sim_config=SimConfig(tick_s=0.0002, fifo_starvation_alarm=False)
    ) as (daemon, sim, client):  # fmt: skip
        first = daemon.load_job_frames((frame for _ in range(n)), total_frames=n)
        client.call("start_job", token=first.token)
        assert run_to_end(client)["state"] == "DONE"
        # no join here on purpose: this is exactly the window the interlock has to cover
        second = daemon.load_job_frames((frame for _ in range(n)), total_frames=n)
        assert second.token != first.token
        assert first.closed, "load_job returned while the previous feeder still owned the FIFO"
        start_job(client, second.token)
        assert run_to_end(client)["state"] == "DONE"
        assert second.join(5.0)
        assert wait_for(lambda: sim.snapshot()["ticks_consumed"] == 2 * n * 99, 10.0)
        assert sim.snapshot()["ticks_consumed"] == 2 * n * 99
        assert daemon.gate.arming.job_token is None
        # two complete programs, each clear -> start ... stop -> clear
        assert fifo_control(sim) == [
            C.FIFO_CLEAR, C.FIFO_START, C.FIFO_STOP, C.FIFO_CLEAR,
            C.FIFO_CLEAR, C.FIFO_START, C.FIFO_STOP, C.FIFO_CLEAR,
        ]  # fmt: skip


def test_in_process_job_streams_frames_from_a_generator() -> None:
    """``load_job_frames`` takes any iterable: a job never has to fit in memory (PORT-PLAN §8.3)."""
    n = 12
    frame = tick_frame()
    with running(
        sim_config=SimConfig(tick_s=0.0002, fifo_starvation_alarm=False)
    ) as (daemon, sim, client):  # fmt: skip
        feeder = daemon.load_job_frames((frame for _ in range(n)), name="gen", total_frames=n)
        client.call("start_job", token=feeder.token)
        js = run_to_end(client)
        assert js["state"] == "DONE", js["error"]
        assert sim.snapshot()["ticks_consumed"] == n * 99
        assert feeder.stats.frames_sent == n


# ---- dry run --------------------------------------------------------------------------------


def test_laser_records_never_reach_the_card(tmp_path: Path) -> None:
    """A job planned with laser records is neutralised on load *and* by the gate (PORT-PLAN §8.2).

    The frames carry a cutting duty, the CO2 laser DO9 on, and an in-stream PWM record; the
    card must see none of them (11 §5.2 / mcc.safety.strip_laser_records).
    """
    hot = (
        *tick_words(duty=40),
        item_header(9999, 3),
        C.MISC_DO,
        0x100,
        0x100,  # DO9 on
        item_header(9999, 3),
        C.MISC_PWM,
        5000,
        40,  # PWM 40 %
        *tick_words(duty=40),
    )
    path = tmp_path / "hot.txt"
    write_frame_file(path, [(1, PackedFrame(hot, 3, 4))])
    sim_cfg = SimConfig(tick_s=0.0002, fifo_starvation_alarm=False, consumed_log_limit=100)
    with running(sim_config=sim_cfg) as (_daemon, sim, client):
        loaded = client.call("load_job", path=str(path))
        client.call("start_job", token=loaded["token"])
        assert run_to_end(client)["state"] == "DONE"
        items = sim.consumed_items
        # the PWM record is removed; the DO record held only DO9, so clearing its laser bit
        # left an empty mask and the whole record was dropped (mcc.safety.strip_laser_records)
        assert [i.opcode for i in items] == [3000, 3000]
        assert all(i.tick[3] == 0 for i in items if i.tick)  # duty forced to 0
        assert not (sim.outputs & 0x100)  # DO9 never switched on
        assert sim.registers[Status.PWM_FREQ.addr] == 0  # the card's PWM was never set


def test_a_frame_outside_the_item_grammar_is_refused_on_load(tmp_path: Path) -> None:
    """11 §5.2: an unknown opcode is refused when the job is read, not mid-stream."""
    bad = (item_header(4242, 1), 0)
    path = tmp_path / "bad.txt"
    write_frame_file(path, [(1, PackedFrame(bad, 0, 1))])
    with running() as (_daemon, _sim, client):
        loaded = client.call("load_job", path=str(path))  # lazy: the words are read on start
        with pytest.raises(IpcError) as exc:
            client.call("start_job", token=loaded["token"])
        assert "FIFO opcode 4242 not allowed" in exc.value.message
        assert client.call("job_status")["state"] == "FAILED"


def test_an_over_long_frame_is_refused_before_it_is_streamed() -> None:
    """A frame that does not fit the card's 1448-byte datagram (04 §3.2) never leaves."""
    from nexcut.mcc.framing import MAX_FRAME_LEN, encode_vector

    big: list[int] = []
    for _ in range(120):
        big += [TICK_HEADER, 0x00010001, 5000 << 16]
    with pytest.raises(SafetyViolation) as exc:
        list(frames_from_words([(3, big)]))
    assert "datagram buffer" in exc.value.reason
    # the largest frame that is accepted really does encode
    ok = next(iter(frames_from_words([(3, big[: 3 * 99])])))
    assert len(encode_vector(ok.vector(1), seq=0)) <= MAX_FRAME_LEN


def test_frames_from_words_strips_and_counts() -> None:
    """Unit check of the load-time conversion (no daemon, no card)."""
    hot = [TICK_HEADER, 0x00010001, (5000 << 16) | 55]
    frames = list(frames_from_words([(7, hot)]))
    assert len(frames) == 1
    assert frames[0].data == (TICK_HEADER, 0x00010001, 5000 << 16)
    assert frames[0].items == 1 and scan_items(frames[0].data) == (1, 1)
    with pytest.raises(SafetyViolation):
        list(frames_from_words([(7, [item_header(4242, 0)])]))


# ---- stop / pause ---------------------------------------------------------------------------


def test_stop_job_clears_the_fifo_and_keeps_the_machine_armed() -> None:
    """A clean stop is ``0x67 <- [3]`` then ``0x67 <- [1]`` while still armed (task 8, D5)."""
    frame = tick_frame()
    with running(
        sim_config=SimConfig(tick_s=0.004, fifo_starvation_alarm=False)
    ) as (daemon, sim, client):  # fmt: skip
        feeder = daemon.load_job_frames((frame for _ in range(4000)), name="long")
        client.call("start_job", token=feeder.token)
        assert wait_for(lambda: feeder.stats.frames_sent > 10, 10.0)
        res = client.call("stop_job")
        assert res["state"] == "STOPPED"
        assert fifo_control(sim)[-2:] == [C.FIFO_STOP, C.FIFO_CLEAR]
        assert sim.snapshot()["queued"] == 0
        assert daemon.gate.arming.state is ArmState.MOTION_ARMED  # stop_job does not disarm
        assert daemon.gate.arming.job_token is None
        assert client.call("job_status")["state"] == "STOPPED"


def test_pause_stops_the_program_and_start_resumes_it() -> None:
    """Pause = ``0x67 <- [3]`` with the queue kept; resume = ``0x67 <- [2]`` (N2 UNVERIFIED)."""
    frame = tick_frame()
    with running(
        sim_config=SimConfig(tick_s=0.004, fifo_starvation_alarm=False)
    ) as (daemon, sim, client):  # fmt: skip
        feeder = daemon.load_job_frames(frame for _ in range(4000))
        client.call("start_job", token=feeder.token)
        assert wait_for(lambda: feeder.stats.frames_sent > 10, 10.0)
        assert client.call("pause_job")["state"] == "PAUSED"
        assert wait_for(lambda: not sim.fifo_running, 3.0)
        queued = sim.snapshot()["queued"]
        assert queued > 0  # the queue survives the pause
        consumed = sim.snapshot()["ticks_consumed"]
        time.sleep(0.2)
        assert sim.snapshot()["ticks_consumed"] == consumed  # nothing moves while paused
        assert client.call("start_job")["state"] == "RUNNING"
        assert wait_for(lambda: sim.snapshot()["ticks_consumed"] > consumed, 5.0)
        client.call("stop_job")


def test_global_stop_ends_the_job() -> None:
    """``stop`` (the operator's big hammer) ends the job as well (D10 motion epoch)."""
    frame = tick_frame()
    with running(
        sim_config=SimConfig(tick_s=0.004, fifo_starvation_alarm=False)
    ) as (daemon, sim, client):  # fmt: skip
        feeder = daemon.load_job_frames(frame for _ in range(4000))
        client.call("start_job", token=feeder.token)
        assert wait_for(lambda: feeder.stats.frames_sent > 10, 10.0)
        client.call("stop")
        # 'stop' aborts without joining, and the final state is set before the feeder thread
        # sends its closing 0x67 pair and returns the token: wait for the thread, not the state.
        assert feeder.join(10.0) and feeder.closed
        assert feeder.state in (JobState.STOPPED, JobState.FAILED)
        assert C.FIFO_STOP in fifo_control(sim)
        assert daemon.gate.arming.job_token is None


def test_disarm_ends_the_job_and_no_frame_follows_it() -> None:
    """PORT-PLAN §8.2: a disarm must end the stream; the gate refuses 0x66 afterwards."""
    frame = tick_frame()
    with running(
        sim_config=SimConfig(tick_s=0.004, fifo_starvation_alarm=False)
    ) as (daemon, sim, client):  # fmt: skip
        feeder = daemon.load_job_frames(frame for _ in range(4000))
        client.call("start_job", token=feeder.token)
        assert wait_for(lambda: feeder.stats.frames_sent > 10, 10.0)

        def frames_on_the_wire() -> int:
            return len([v for v in vectors(sim) if v[:2] == (0x40, 0x66)])

        client.call("disarm")
        # join, not 'finished': the final state is set before the thread's clean stop runs
        assert feeder.join(10.0)
        assert daemon.gate.arming.state is ArmState.DISARMED
        # nothing more reaches 0x66 once the machine is disarmed
        n = frames_on_the_wire()
        time.sleep(0.3)
        assert frames_on_the_wire() == n
        assert C.FIFO_STOP in fifo_control(sim)


def test_a_starvation_alarm_at_the_end_of_the_drain_still_counts_as_done() -> None:
    """The default simulator alarms when the running FIFO empties - which is also the job end.

    The card reports "FIFO empty" at the instant it consumes the last item, so ``0x67 <- [3]``
    always arrives after that: a card that alarmed there would fault at the end of every
    vendor cut (UNVERIFIED, 11 §7 step 8/9). The feeder therefore accepts a starvation alarm
    *during the drain*, records it, and still reports DONE - while the safety gate, which
    cannot tell the two apart either, does its conservative thing and disarms.
    """
    frames = [tick_frame() for _ in range(3)]
    with running(sim_config=SimConfig(tick_s=0.0002)) as (daemon, sim, client):
        feeder = daemon.load_job_frames(iter(frames), total_frames=3)
        client.call("start_job", token=feeder.token)
        js = run_to_end(client)
        assert js["state"] == "DONE", js["error"]
        assert js["stats"]["drain_starvation_alarm"] is True
        assert sim.snapshot()["ticks_consumed"] == 3 * 99
        assert daemon.gate.arming.state is ArmState.DISARMED  # the gate reacted to the alarm


def test_card_starvation_alarm_fails_the_job_and_disarms() -> None:
    """Reg 1007 bit 5 (A2 §2.4) is a hard error: the gate stops and disarms, the job fails."""
    frame = tick_frame()
    with running(
        sim_config=SimConfig(tick_s=0.004, fifo_starvation_alarm=False)
    ) as (daemon, sim, client):  # fmt: skip
        feeder = daemon.load_job_frames(frame for _ in range(4000))
        client.call("start_job", token=feeder.token)
        assert wait_for(lambda: feeder.stats.frames_sent > 10, 10.0)
        sim.inject_alarm(alarm2=ALARM2_FIFO_STARVATION)
        assert feeder.join(10.0)
        assert feeder.state is JobState.FAILED
        # the disarm is the daemon's answer to the failure, one hop behind the feeder thread
        assert wait_for(lambda: daemon.gate.arming.state is ArmState.DISARMED, 10.0)


def test_a_second_job_is_refused_while_one_is_loaded() -> None:
    with running() as (daemon, _sim, client):
        daemon.load_job_frames([tick_frame()])
        with pytest.raises(IpcError) as exc:
            daemon.load_job_frames([tick_frame()])
        assert exc.value.code == "busy"


# ---- unacknowledged frames -------------------------------------------------------------------


class _StubGate:
    """Minimal gate for the ack policy: records writes, never touches a card."""

    def __init__(self) -> None:
        self.writes: list[tuple[int, list[int]]] = []

    class arming:  # noqa: N801 - mimics the real attribute
        state = ArmState.MOTION_ARMED
        estop_latched = False
        job_token = "tok"

        @staticmethod
        def allows(policy: object) -> bool:
            return True

    def write(self, addr: int, words: list[int]) -> None:
        self.writes.append((addr, list(words)))


def _feeder_with_window(max_resends: int = 2) -> JobFeeder:
    gate = _StubGate()
    clock = {"t": 1000.0}
    feeder = JobFeeder(
        gate,
        lambda: None,
        [],
        token="tok",
        config=FeederConfig(unacked_timeout_s=0.0, max_resends=max_resends),
        clock=lambda: clock["t"],
    )
    # (send time, frame id, vector words) - what `_send_frame` records; all sent at t = 0 so
    # the `unacked_timeout_s = 0` of this config makes them overdue at once.
    feeder._sent_window = [(0.0, fid, list(tick_frame(2).payload(fid))) for fid in (10, 11, 12)]
    return feeder


def test_unacknowledged_frames_are_resent_from_the_reported_id() -> None:
    """Reg 1015 lagging means the card missed everything after it (A3 §7 deviation)."""
    feeder = _feeder_with_window()
    feeder._check_ack(10, 1000.0)
    assert [w[0] for w in feeder.gate.writes] == [0x66, 0x66]
    assert [w[1][0] for w in feeder.gate.writes] == [11, 12]
    assert feeder.stats.resends == 2


def test_an_acknowledged_window_is_forgotten() -> None:
    feeder = _feeder_with_window()
    feeder._check_ack(12, 1000.0)
    assert feeder.gate.writes == [] and feeder._sent_window == []


def test_repeated_non_acknowledgement_aborts_the_job() -> None:
    feeder = _feeder_with_window(max_resends=1)
    feeder._check_ack(10, 1000.0)
    with pytest.raises(Exception) as exc:
        feeder._check_ack(10, 1001.0)
    assert "not acknowledged" in str(exc.value)


# ---- the source is streamed lazily ------------------------------------------------------------


def test_the_source_is_pulled_lazily() -> None:
    """A job of any length costs constant memory: at most ``buffer_frames`` are materialised."""
    pulled = 0

    def source() -> Iterator[PackedFrame]:
        nonlocal pulled
        while True:
            pulled += 1
            yield tick_frame()

    with running(sim_config=SimConfig(tick_s=0.001, fifo_starvation_alarm=False)) as (
        daemon,
        _sim,
        client,
    ):
        feeder = daemon.load_job_frames(source())
        client.call("start_job", token=feeder.token)
        assert wait_for(lambda: feeder.stats.frames_sent > 60, 20.0)
        client.call("stop_job")
        assert pulled <= feeder.stats.frames_sent + feeder.config.buffer_frames + 2


# ---- concurrency ------------------------------------------------------------------------------


def test_the_status_poll_keeps_running_while_a_job_streams() -> None:
    """PORT-PLAN §3.4: streaming must not push the 30 ms block-1000 poll off the bus."""
    frame = tick_frame()
    with running(sim_config=SimConfig(tick_s=0.004, fifo_starvation_alarm=False)) as (
        daemon,
        _sim,
        client,
    ):
        feeder = daemon.load_job_frames(frame for _ in range(4000))
        client.call("start_job", token=feeder.token)
        assert wait_for(lambda: feeder.stats.frames_sent > 20, 10.0)
        ages = []
        for _ in range(20):
            ages.append(daemon.snapshot().poll_age_s or 99.0)
            time.sleep(0.05)
        client.call("stop_job")
        # a few periods of head room: the poll is 30 ms, the watchdog trips at 1 s
        assert max(ages) < 0.5, ages


def test_load_and_stream_do_not_block_an_estop() -> None:
    """An E-stop must get through while frames are streaming (URGENT beats COMMAND, D4)."""
    frame = tick_frame()
    with running(sim_config=SimConfig(tick_s=0.004, fifo_starvation_alarm=False)) as (
        daemon,
        _sim,
        client,
    ):
        feeder = daemon.load_job_frames(frame for _ in range(4000))
        client.call("start_job", token=feeder.token)
        assert wait_for(lambda: feeder.stats.frames_sent > 10, 10.0)
        t = time.monotonic()
        client.call("estop")
        assert time.monotonic() - t < 5.0
        assert feeder.join(10.0)
        assert daemon.gate.arming.estop_latched


def test_streaming_survives_a_busy_process() -> None:
    """A busy process must not break the stream (R8 smoke test; the gate is test_perf_streaming).

    The load is numpy work plus object churn, not a pure-Python spin loop: the simulated
    *card* runs on a thread of this same process, and a thread that never releases the GIL
    starves the card's consumption thread as well - the card then empties its FIFO in one
    catch-up burst and the test measures the harness instead of the daemon. A real card
    consumes at its own rate, and PORT-PLAN §2.3 puts the UI in another process anyway.
    """
    stop = threading.Event()

    def churn() -> None:
        pts = np.random.default_rng(3).random((20_000, 2))
        junk: list[list[int]] = []
        while not stop.is_set():
            np.hypot(pts[:, 0], pts[:, 1]).sum()
            junk.append(list(range(500)))
            if len(junk) > 200:
                junk.clear()

    frame = tick_frame()
    worker = threading.Thread(target=churn, daemon=True)
    worker.start()
    try:
        with running(sim_config=SimConfig(tick_s=0.0005, fifo_starvation_alarm=False)) as (
            daemon,
            sim,
            client,
        ):
            feeder = daemon.load_job_frames((frame for _ in range(200)), total_frames=200)
            client.call("start_job", token=feeder.token)
            js = run_to_end(client, timeout=60.0)
            assert js["state"] == "DONE", js["error"]
            # DONE means the producer is finished and reg 1016 came back to 60000, which on a
            # busy machine can be observed a tick before the simulator thread has executed the
            # last items it popped. Wait for the count instead of reading it once; a frame that
            # really went missing never arrives and the equality below still fails.
            wait_for(lambda: sim.snapshot()["ticks_consumed"] == 200 * 99, 10.0)
            assert sim.snapshot()["ticks_consumed"] == 200 * 99
    finally:
        stop.set()
        worker.join(timeout=5.0)


def test_parse_of_a_streamed_frame_matches_the_planned_items() -> None:
    """The words the daemon put on the wire re-parse into the planned items (11 §5.2)."""
    frame = tick_frame(3)
    with running(sim_config=SimConfig(tick_s=0.0002, fifo_starvation_alarm=False)) as (
        daemon,
        sim,
        client,
    ):
        feeder = daemon.load_job_frames([frame], total_frames=1)
        client.call("start_job", token=feeder.token)
        assert run_to_end(client)["state"] == "DONE"
        sent = [v for v in vectors(sim) if v[:2] == (0x40, 0x66)]
        assert len(sent) == 1
        parsed = parse_fifo_words(list(sent[0][3:]))
        assert len(parsed.items) == 3 and all(i.opcode == 3000 for i in parsed.items)


# ---- a real planned job ----------------------------------------------------------------------


def test_a_job_planned_by_nexcut_plan_streams_through_the_daemon(tmp_path: Path) -> None:
    """The M4 shape end to end: ``plan.build_job`` -> frame file -> daemon -> simulator.

    The planner is asked for laser records, so the file holds cutting duty and the DO9
    enable; the daemon must still put a dry run on the wire (PORT-PLAN §8.2, D5) and the
    card must execute exactly the frames the daemon sent.
    """
    from nexcut.io.params import ParamDocument, default_document
    from nexcut.model.glyph import SegmentGlyph, Vec2
    from nexcut.model.graph import ChfDocument, Contour, ContourElement
    from nexcut.plan.__main__ import build_job

    def put(doc: ParamDocument, key: str, value: float | int | str) -> None:
        elem, attr = key.split(".", 1)
        group = next(g for g, e in doc.values.items() if elem in e and attr in e[elem])
        doc.set(group, elem, attr, value)

    manu, hard, layer = (default_document(k) for k in ("manu", "hard", "layer"))
    put(manu, "SP.m_iEnableLaserType", 1)
    for key, val in {
        "ZF.ZFType": 0, "LGP.CO2DOLaser": 9, "LGP.CO2LaserControlType": 2, "MGP.HighAir": 3,
        "MAC.SpeedRatio": 31.003, "MAC.WritePluse": 8000, "MAC_1.SpeedRatio": 31.009,
        "MAC_1.WritePluse": 8000, "AX.InterpolationCycle": 250,
    }.items():  # fmt: skip
        put(hard, key, val)
    group = next(g for g in layer.values if g.endswith("CO2LayerParam2"))
    for attr, val in {"CutSpeed": 100.0, "CutDuty": 4, "CutFreq": 5000, "CutGasType": 3}.items():
        layer.set(group, "GP", attr, val)
    line = Contour(elements=[ContourElement(SegmentGlyph(Vec2(0, 0), Vec2(3, 4)))], layer=1)
    job = build_job(ChfDocument(graphs=[line]), manu, hard, layer, laser_records=True)
    assert job.contours == 1 and job.frames

    path = tmp_path / "planned.txt"
    write_frame_file(path, [((i + 1), f) for i, f in enumerate(job.frames)], ["planned"])

    sim_cfg = SimConfig(tick_s=0.00005, fifo_starvation_alarm=False, consumed_log_limit=200_000)
    with running(sim_config=sim_cfg) as (_daemon, sim, client):
        loaded = client.call("load_job", path=str(path))
        assert loaded["frames"] == len(job.frames)
        client.call("start_job", token=loaded["token"])
        js = run_to_end(client, timeout=60.0)
        assert js["state"] == "DONE", js["error"]

        # what the daemon put on the wire == what the card executed
        on_wire: list[int] = []
        for v in vectors(sim):
            if v[:2] == (0x40, 0x66):
                on_wire.extend(v[4:])
        assert sim.consumed_words() == on_wire

        # and it is a dry run: no PWM record, no laser DO on, every tick at duty 0
        for item in sim.consumed_items:
            if item.tick is not None:
                assert item.tick[3] == 0
            if item.opcode == 9999:
                assert item.args[0] not in (C.MISC_PWM, C.MISC_PWM_5V)
                assert not (item.args[2] & 0x100)  # DO9
        assert not (sim.outputs & 0x100)
        # the geometry still arrives: the planned pulse totals reach the card
        assert sim.snapshot()["ticks_consumed"] == job.ticks


# ---- the CLI --------------------------------------------------------------------------------


def test_cli_run_job_streams_a_frame_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``nexcut-mccd run-job FILE --arm`` arms, streams and disarms in one connection (D9)."""
    from nexcut.mccd import cli

    path = tmp_path / "job.txt"
    write_frame_file(path, [(i + 1, tick_frame()) for i in range(6)], ["cli test"])
    sim_cfg = SimConfig(tick_s=0.0002, fifo_starvation_alarm=False)
    with running(sim_config=sim_cfg, arm=False) as (daemon, sim, _client):
        code = cli.main(["run-job", str(path), "--arm", "--socket", str(daemon.socket_path)])
        out = capsys.readouterr()
        assert code == cli.EXIT_OK, out.err
        assert "DONE 6/6 frames" in out.out
        assert sim.snapshot()["ticks_consumed"] == 6 * 99
        assert daemon.gate.arming.state is ArmState.DISARMED  # --arm disarmed again


def test_cli_run_job_without_arming_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without ``--arm`` (and without another armed connection) the gate refuses the job."""
    from nexcut.mccd import cli

    path = tmp_path / "job.txt"
    write_frame_file(path, [(1, tick_frame())])
    with running(arm=False) as (daemon, _sim, _client):
        code = cli.main(["run-job", str(path), "--socket", str(daemon.socket_path)])
        err = capsys.readouterr().err
        assert code == cli.EXIT_ERROR
        assert "MOTION_ARMED" in err


def test_cli_job_status_without_a_job(capsys: pytest.CaptureFixture[str]) -> None:
    from nexcut.mccd import cli

    with running(arm=False) as (daemon, _sim, _client):
        code = cli.main(["job-status", "--socket", str(daemon.socket_path)])
        assert code == cli.EXIT_OK
        assert "no job loaded" in capsys.readouterr().out


def test_cli_run_job_rejects_a_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    from nexcut.mccd import cli

    code = cli.main(["run-job", "/nonexistent/job.txt"])
    assert code == cli.EXIT_USAGE
    assert "not a file" in capsys.readouterr().err
