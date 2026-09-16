"""Adversarial stream/plan-fidelity review of the FIFO streamer and the offline planner.

Lens: ``docs/analysis/11-static-findings.md`` §5 (frame packing, byte accounting vs reg 1016,
frame id = reg 1015 + 1, the per-contour record sequence), ``11-static/A3`` (item grammar,
``fillFifo`` flow control) and ``11-static/A9`` (look-ahead entry velocity and the dwell
builders), plus ``05-motion-pipeline.md`` for the sampler.

Everything here is offline or simulator-only (PORT-PLAN §8): no test opens a socket, and the
one test that runs the planner CLI proves it by installing an audit hook.

Findings that are fixed are pinned by ordinary tests; each test's docstring names the evidence
and, for a finding, what the code did before.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from nexcut.io.chf import save_chf
from nexcut.io.params import write_params
from nexcut.mcc import commands as C
from nexcut.mcc.dissector import parse_fifo_words
from nexcut.mcc.fifo import read_frame_file
from nexcut.mcc.registers import FIFO_MARGIN_EMPTY, Status
from nexcut.mcc.safety import ArmState
from nexcut.mcc.simulator import SimConfig
from nexcut.mccd.feeder import FeederConfig, JobError, JobFeeder
from nexcut.plan.__main__ import build_job
from nexcut.plan.items import AxisScale, TickQuantizer
from nexcut.plan.params import document_value

# Sibling test modules: pytest's prepend import mode puts ``tests/`` on sys.path (conftest.py
# is the rootdir of the package-less test tree), which is how ``test_m1_session`` loads too.
from test_mccd_job import run_to_end, running, start_job, tick_frame  # noqa: I001
from test_plan_items_cli import job_doc, machine_docs, put

# ============================================================================== stub card / gate


class _RecordingGate:
    """The smallest gate a :class:`JobFeeder` needs: records every card write, never sends one.

    ``sent`` keeps the ``0x67`` control vectors and the ``0x66`` frame writes in one list, so a
    test can assert the *order* of the clear / fill / start sequence of A3 §7.
    """

    def __init__(self) -> None:
        self.writes: list[tuple[int, list[int]]] = []
        self.sent: list[tuple[str, tuple[int, ...]]] = []
        self.motion_epoch = 0

    class arming:  # noqa: N801 - mirrors MccdGate.arming
        state = ArmState.MOTION_ARMED
        estop_latched = False
        job_token = "tok"

        @staticmethod
        def allows(policy: object) -> bool:
            return True

        @staticmethod
        def end_job(token: str) -> None:
            return None

    def write(self, addr: int, words: list[int]) -> None:
        self.writes.append((addr, list(words)))
        self.sent.append(("write", (addr,)))

    def send(self, cmd: Any) -> None:
        self.sent.append((cmd.name, tuple(cmd.words)))


def _feeder(
    gate: _RecordingGate,
    frames: Any,
    clock: dict[str, float],
    **cfg: Any,
) -> JobFeeder:
    return JobFeeder(
        gate,
        lambda: None,
        frames,
        token="tok",
        config=FeederConfig(**cfg),
        clock=lambda: clock["t"],
    )


def _endless(n_ticks: int = 2) -> Any:
    while True:
        yield tick_frame(n_ticks)


# ================================================== A3 §7: a frame reg 1015 never acknowledges
def test_a_stalled_reg_1015_is_caught_while_frames_keep_flowing() -> None:
    """FINDING (``mccd/feeder.py``).  The module docstring promises that "a frame the card did
    not acknowledge within ``unacked_timeout_s`` is re-sent ... then the job is aborted", which
    is the port's deviation from the vendor's silent frame drop (11 §5.1, A3 §7).

    ``_fill`` used to *replace* ``_sent_window`` and restart ``_unacked_since`` on **every**
    fill pass, so the 600 ms ``ipAdd.ini FifoTimeout`` was re-armed roughly every 30 ms status
    poll.  A card whose reg 1015 stops advancing while it keeps taking frames was therefore
    never noticed: 3 s of streaming, 1500 frames, reg 1015 pinned at 0, zero re-sends and no
    abort.  The overdue clock has to run from the *oldest* unacknowledged frame.
    """
    gate = _RecordingGate()
    clock = {"t": 0.0}
    feeder = _feeder(gate, _endless(), clock, unacked_timeout_s=0.6, max_resends=2)
    with pytest.raises(JobError, match="not acknowledged"):
        for _ in range(40):  # 4 s of 100 ms passes; reg 1015 never leaves 0
            feeder._refill_ring()
            feeder._check_ack(0, clock["t"])
            feeder._fill(0, 1_000_000)
            clock["t"] += 0.1
    assert feeder.stats.resends > 0
    # the abort must arrive well inside the drain budget, not after the whole job
    assert clock["t"] <= 3.0


def test_an_unacknowledged_frame_is_not_forgotten_by_the_next_fill_pass() -> None:
    """FINDING (``mccd/feeder.py``).  Reg 1015 is the id of the *last frame the card accepted*
    (A3 §0, simulator ``RO_FIFO_FRAME_ID``).  A pass that sends ids 1..3 while the card only
    reports 1 leaves 2 and 3 unacknowledged; the next pass used to drop them from
    ``_sent_window`` altogether, so nothing could ever re-send them.  The window must hold
    every frame the card has not confirmed, oldest first.
    """
    gate = _RecordingGate()
    clock = {"t": 0.0}
    feeder = _feeder(gate, _endless(), clock, unacked_timeout_s=0.6, max_resends=2)
    feeder._refill_ring()
    feeder._fill(0, 3 * (tick_frame(2).byte_size + 2000))  # ids 1.. (space-limited)
    first = feeder.stats.frames_sent
    assert first >= 1
    clock["t"] = 0.05
    feeder._check_ack(1, clock["t"])  # the card confirms id 1 only
    feeder._fill(1, 1_000_000)
    ids = [w[1][0] for w in gate.writes]
    unacked = [i for i in ids if i > 1]
    assert feeder.unacknowledged_ids() == unacked, "ids 2.. must stay in the window"


def test_an_acknowledged_prefix_is_dropped_so_the_window_cannot_grow() -> None:
    """The complement of the test above: ids the card *has* confirmed leave the window, so a
    long job does not accumulate every frame it ever sent (A3 §7: ids increase within a pass)."""
    gate = _RecordingGate()
    clock = {"t": 0.0}
    feeder = _feeder(gate, _endless(), clock, unacked_timeout_s=0.6, max_resends=2)
    for _ in range(6):
        feeder._refill_ring()
        feeder._check_ack(feeder.stats.frames_sent, clock["t"])  # card is fully caught up
        feeder._fill(feeder.stats.frames_sent, 1_000_000)
        clock["t"] += 0.03
    assert feeder.stats.frames_sent > 100
    assert len(feeder.unacknowledged_ids()) <= feeder.config.max_frames_per_fill
    assert feeder.stats.resends == 0


# ========================================================= A3 §7: never start an empty program
def test_the_program_is_not_started_before_a_frame_reaches_the_card() -> None:
    """FINDING (``mccd/feeder.py``).  ``_stream`` filled from the status poll it had read
    *before* ``0x67 <- [1]`` and then sent ``0x67 <- [2]`` unconditionally.  A card that still
    held a program from an aborted job reports a small reg 1016, so the first fill sends
    nothing and the port starts an **empty** FIFO program - the starvation case of A2 §2.4,
    caused by the port itself.  The first fill has to use a status poll taken after the clear,
    and the program may only start once a frame is in the card.
    """
    stale = [0] * 36
    stale[Status.FIFO_FRAME_ID] = 7
    stale[Status.FIFO_SPACE_MARGIN] = 1000  # a leftover program: no room for a 1204-byte frame
    fresh = list(stale)
    fresh[Status.FIFO_SPACE_MARGIN] = FIFO_MARGIN_EMPTY  # after 0x67 <- [1]
    fresh[Status.PROCESSING_STATUS] = 1
    polls = [(stale, 1.0)] + [(fresh, 2.0 + i) for i in range(8)]
    seq = iter(polls)
    held: list[Any] = []

    def status() -> tuple[list[int], float]:
        try:
            held.append(next(seq))
        except StopIteration:
            feeder._stop_req.set()  # a few passes are enough; end the job
            held.append((held[-1][0], held[-1][1] + 1.0))
        return held[-1]

    gate = _RecordingGate()
    feeder = JobFeeder(gate, status, _endless(), token="tok", config=FeederConfig())
    try:
        feeder._stream()
    except Exception:  # the job may end however it likes; only the order matters here
        pass
    order = [name for name, _ in gate.sent]
    assert order[0] == C.fifo_clear().name
    assert C.fifo_start().name in order, order
    start = order.index(C.fifo_start().name)
    assert "write" in order[:start], f"0x67 <- [2] was sent before any 0x66 frame: {order}"


# ========================================================== 11 §5.2/§5.4: the ZF record family
def test_zf_prologue_and_epilogue_speed_word_follows_the_machine_xml() -> None:
    """FINDING (``plan/__main__.py``).  The prologue ``103`` and the epilogue ``109`` carry
    ``int(speed·10)`` (11 §5.2, A3 §8: the leaked frames hold ``103[1000,0]`` / ``109[1000,0]``
    on a machine whose ``ZF.ZFFollowSpeed`` is 100).  ``build_job`` wired ``ZF.ZFUpSpeed`` and
    ``ZF.ZFDockHeight`` - which only the record-9 *dock* path uses, and no CO2 contour emits
    one - while ``StreamConfig.zf_move_speed``, the value both emitted records actually read,
    stayed at the class default 100.0.  On this machine the two agree, so the goldens do not
    move; on any machine with a different follow speed the port would have commanded 100 mm/s.
    """
    manu, hard, layer = machine_docs()
    put(manu, "ZF.ZFFollowSpeed", 100.0)
    job = build_job(job_doc(), manu, hard, layer)
    items = [
        (it.opcode, it.args)
        for i, f in enumerate(job.frames)
        for it in parse_fifo_words([i + 1, *f.data]).items
    ]
    assert [a for op, a in items if op == 103] == [(1000, 0)] * 2  # vendor evidence, A3 §8
    assert [a for op, a in items if op == 109] == [(1000, 0)] * 2
    assert [a for op, a in items if op == 118] == [(4, 0, 35)] * 2

    put(manu, "ZF.ZFFollowSpeed", 40.0)
    slow = build_job(job_doc(), manu, hard, layer)
    slow_items = [
        (it.opcode, it.args)
        for i, f in enumerate(slow.frames)
        for it in parse_fifo_words([i + 1, *f.data]).items
    ]
    assert [a for op, a in slow_items if op == 103] == [(400, 0)] * 2
    assert [a for op, a in slow_items if op == 109] == [(400, 0)] * 2


# ======================================================= 11 §5.3: pulse accounting over a job
_SCALE_KEYS = ("MAC.SpeedRatio", "MAC.WritePluse", "MAC_1.SpeedRatio", "MAC_1.WritePluse")


def _axis_scale(hard: Any) -> AxisScale:
    """``WritePluse / SpeedRatio`` per axis from ``BkHardPara.xml`` (11 §5.3)."""
    return AxisScale.from_values({k: document_value(hard, k) for k in _SCALE_KEYS})


def _job_arrays(monkeypatch: pytest.MonkeyPatch) -> list[np.ndarray]:
    """Record every millimetre point list the job hands to the tick quantiser."""
    seen: list[np.ndarray] = []
    import nexcut.plan.__main__ as main_mod

    class Recording(TickQuantizer):
        def quantize(self, points_mm: Any) -> tuple[list[int], list[int]]:
            pts = np.asarray(points_mm, dtype=np.float64).reshape(-1, 2)
            seen.append(pts.copy())
            return super().quantize(pts)

    monkeypatch.setattr(main_mod, "TickQuantizer", Recording)
    return seen


def test_a_planned_job_loses_no_pulse_at_a_contour_or_rapid_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """11 §5.3: the per-axis carry persists across contours and jobs, so the pulses a whole job
    streams stay within **one** pulse of the net displacement, however many contours and rapids
    it is cut into.  (The residual carry is what holds the last fraction back; on these two jobs
    it never changes sign, so the sum also lands exactly on ``trunc(net · pulses/mm)`` - that
    equality is a golden for this geometry, not a general law.)

    The quantiser only emits the differences *inside* each point list it is given, so every
    join (contour end -> rapid start -> next contour start) has to be exact; a rapid whose last
    sample missed the contour's start point would silently swallow that step - up to a whole
    contour-to-contour move, with no error anywhere.  Both halves are checked here, end to end
    through ``build_job`` and back out of the packed frames.
    """
    arrays = _job_arrays(monkeypatch)
    manu, hard, layer = machine_docs()
    job = build_job(job_doc(), manu, hard, layer, laser_records=True)
    assert len(arrays) == 2 * job.contours - 1  # 2 contours, 1 rapid between them
    for a, b in zip(arrays, arrays[1:], strict=False):
        assert float(np.hypot(*(b[0] - a[-1]))) < 1e-9, "a join would lose its whole step"

    scale = _axis_scale(hard)
    net = arrays[-1][-1] - arrays[0][0]
    dx = dy = 0
    for i, frame in enumerate(job.frames):
        for it in parse_fifo_words([i + 1, *frame.data]).items:
            if it.tick is not None:
                dx += it.tick[0]
                dy += it.tick[1]
    assert abs(dx - net[0] * scale.x_per_mm) < 1.0
    assert abs(dy - net[1] * scale.y_per_mm) < 1.0
    assert dx == int(np.trunc(net[0] * scale.x_per_mm))
    assert dy == int(np.trunc(net[1] * scale.y_per_mm))


def test_a_long_vendor_job_streams_without_pulse_drift(
    src_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same invariant on a real 24-contour vendor drawing (24 contours + 23 rapids, 126 k
    ticks, 1277 frames): 72 748 / 76 886 pulses against 72 748.77 / 76 886.90 exact."""
    from nexcut.io.chf import load_chf
    from nexcut.io.params import read_params

    arrays = _job_arrays(monkeypatch)
    f = src_dir / "File"
    job = build_job(
        load_chf(f / "autosave.chf"),
        read_params(f / "BkManuPara.xml", "manu"),
        read_params(f / "BkHardPara.xml", "hard"),
        read_params(f / "BkLayerPara.xml", "layer"),
        laser_records=True,
    )
    assert job.contours == 24 and len(arrays) == 47
    for a, b in zip(arrays, arrays[1:], strict=False):
        assert float(np.hypot(*(b[0] - a[-1]))) < 1e-9
    scale = _axis_scale(read_params(f / "BkHardPara.xml", "hard"))
    net = arrays[-1][-1] - arrays[0][0]
    ticks = [
        it.tick
        for i, frame in enumerate(job.frames)
        for it in parse_fifo_words([i + 1, *frame.data]).items
        if it.tick is not None
    ]
    assert len(ticks) == job.ticks
    assert sum(t[0] for t in ticks) == int(np.trunc(net[0] * scale.x_per_mm))
    assert sum(t[1] for t in ticks) == int(np.trunc(net[1] * scale.y_per_mm))
    # every tick fits the int16 the packer writes (A3 §4.2): the card rate ceiling is 750 mm/s
    assert max(max(abs(t[0]), abs(t[1])) for t in ticks) < 0x8000


# ============================================================ PORT-PLAN §8: no socket, ever
def test_the_offline_planner_cli_opens_no_socket(tmp_path: Path) -> None:
    """PORT-PLAN §8 / ``plan/__main__`` docstring: ``nexcut-plan`` "opens no socket and imports
    no transport".  The existing check only looked at ``sys.modules`` after *import*; this one
    runs the whole CLI under a ``sys.addaudithook`` that fails the run on any socket event, so
    a future planner that reached for the card at plan time could not slip through.
    """
    manu, hard, layer = machine_docs()
    for name, doc in (
        ("BkManuPara.xml", manu),
        ("BkHardPara.xml", hard),
        ("BkLayerPara.xml", layer),
    ):
        write_params(tmp_path / name, doc)
    save_chf(job_doc(), tmp_path / "job.chf")
    out = tmp_path / "frames.txt"
    code = textwrap.dedent(
        """
        import sys
        BAD = ("socket.__new__", "socket.connect", "socket.bind", "socket.sendto",
               "socket.getaddrinfo", "socket.gethostbyname")

        def hook(event, args):
            if event in BAD:
                raise AssertionError("nexcut-plan touched the network: " + event)

        sys.addaudithook(hook)
        from nexcut.plan.__main__ import main
        rc = main(sys.argv[1:])
        assert "socket" not in sys.modules, sorted(sys.modules)
        raise SystemExit(rc)
        """
    )
    argv = [
        str(tmp_path / "job.chf"),
        "--layer-xml", str(tmp_path / "BkLayerPara.xml"),
        "--hard-xml", str(tmp_path / "BkHardPara.xml"),
        "-o", str(out),
    ]  # fmt: skip
    proc = subprocess.run(
        [sys.executable, "-c", code, *argv], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    frames = read_frame_file(out)
    assert frames and all(len(data) >= 3 for _fid, data in frames)


# ================================================ A3 §7 / 11 C2: flow control on a slow card
def test_a_slow_consumer_never_makes_the_port_overfill_the_card() -> None:
    """11 C2 byte accounting against a card that empties its FIFO slowly.

    The ``frame_bytes + 2000 <= reg 1016`` test (A3 §7) is the only thing keeping the port from
    over-filling the card, and reg 1016 is read from a status poll up to one ``MCCore`` period
    old.  Here the simulator consumes 1250 items/s while the feeder offers 50 frames a pass, so
    the FIFO sits at its capacity for the whole job and every frame is sent on the margin test
    alone.  A frame the card has no room for is refused with exception 3
    (``CardSimulator._fifo_overflows``, UNVERIFIED like the unit itself), which would show up
    here as a failed job or an accepted-frame count below the frames sent.
    """
    frames = 40
    with running(sim_config=SimConfig(tick_s=0.0008, fifo_starvation_alarm=False)) as (
        daemon,
        sim,
        client,
    ):
        feeder = daemon.load_job_frames(
            iter([tick_frame() for _ in range(frames)]), total_frames=frames
        )
        start_job(client, feeder.token)
        status = run_to_end(client, 120.0)
        assert status["state"] == "DONE", status
        assert feeder.stats.frames_sent == frames
        assert feeder.stats.resends == 0
        assert sim.frames_accepted == frames  # not one frame was refused
        # the card really did fill up: reg 1016 fell far below the 60000 of an empty FIFO
        assert feeder.stats.min_margin is not None
        assert feeder.stats.min_margin < FIFO_MARGIN_EMPTY // 2, feeder.stats.to_json()
