"""``tools/m1_session.py`` run non-interactively against a daemon on the card simulator.

The guided M1 confirmation session (11-static-findings §7, PORT-PLAN §8.2) is driven by a
scripted operator: every prompt has a stable key, hooks play the physical side (E-stop,
limit switches, power cycle) on :class:`~nexcut.mcc.simulator.CardSimulator`.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import socket
import struct
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from nexcut.core.config import NexcutConfig
from nexcut.mcc.dissector import write_pcap
from nexcut.mcc.safety import ArmState
from nexcut.mcc.simulator import CardSimulator, SimConfig
from nexcut.mccd.daemon import Link, MccDaemon

TOOL = Path(__file__).resolve().parents[1] / "tools" / "m1_session.py"


def _load_tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("m1_session", TOOL)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["m1_session"] = mod
    spec.loader.exec_module(mod)
    return mod


m1 = _load_tool()


def wait_for(cond: Callable[[], object], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


class ScriptedOperator:
    """Answers prompts by key. ``motion`` answers every ``s<n>.motion.*`` prompt."""

    def __init__(
        self,
        *,
        motion: str = "y",
        default: str = "y",
        answers: dict[str, str] | None = None,
        hooks: dict[str, Callable[[], None]] | None = None,
    ) -> None:
        self.motion = motion
        self.default = default
        self.answers = answers or {}
        self.hooks = hooks or {}
        self.said: list[str] = []
        self.prompts: list[tuple[str, str, str]] = []

    def _answer(self, kind: str, key: str, prompt: str, fallback: str) -> str:
        hook = self.hooks.get(key)
        if hook is not None:
            hook()
        ans = self.answers.get(key, fallback)
        self.prompts.append((kind, key, ans))
        return ans

    def say(self, text: str) -> None:
        self.said.append(text)

    def ask(self, key: str, prompt: str) -> str:
        return self._answer("ask", key, prompt, f"note for {key}")

    def confirm(self, key: str, prompt: str) -> bool:
        fallback = self.motion if ".motion." in key else self.default
        return m1.is_yes(self._answer("confirm", key, prompt, fallback))

    def wait(self, key: str, prompt: str) -> None:
        self._answer("wait", key, prompt, "")


@contextmanager
def daemon_on_sim() -> Iterator[tuple[MccDaemon, CardSimulator, Path]]:
    base = NexcutConfig()
    cfg = replace(base, mccd=replace(base.mccd, reconnect_interval_ms=300))
    sim = CardSimulator(config=SimConfig(motion_time_s=0.4)).start()
    d = Path(tempfile.mkdtemp(prefix="nxm1"))
    daemon = MccDaemon(cfg, card_addr=sim.address, socket_path=d / "s.sock").start()
    try:
        assert daemon.wait_for_link(Link.CONNECTED, 5.0)
        assert wait_for(lambda: daemon.snapshot().machine_state == "READY", 3.0)
        yield daemon, sim, daemon.socket_path
    finally:
        daemon.close()
        sim.stop()
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------- synthetic capture


def _ip_checksum(h: bytes) -> int:
    s = sum(struct.unpack(f">{len(h) // 2}H", h))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def _udp_frame(src: str, sport: int, dst: str, dport: int, payload: bytes) -> bytes:
    udp = struct.pack(">HHHH", sport, dport, 8 + len(payload), 0) + payload
    hdr = struct.pack(
        ">BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 1, 0, 64, 17, 0,
        socket.inet_aton(src), socket.inet_aton(dst),
    )  # fmt: skip
    hdr = hdr[:10] + struct.pack(">H", _ip_checksum(hdr)) + hdr[12:]
    eth = b"\x00\x11\x22\x33\x44\x55\x66\x77\x88\x99\xaa\xbb\x08\x00"
    return eth + hdr + udp


def write_capture_from_sim(sim: CardSimulator, path: Path) -> None:
    """Requests the simulator received, as a pcap with wall-clock timestamps."""
    offset = time.time() - time.monotonic()
    with sim._lock:
        reqs = list(sim.requests)
    write_pcap(
        path,
        [(r.t + offset, _udp_frame("10.1.1.10", 50000, "10.1.1.168", 502, r.raw)) for r in reqs],
    )


def physical_hooks(sim: CardSimulator, pcap: Path | None) -> dict[str, Callable[[], None]]:
    estop_on = lambda: sim.inject_alarm(alarm1=1 << 30)  # noqa: E731
    estop_off = lambda: sim.inject_alarm(alarm1=0)  # noqa: E731
    hooks: dict[str, Callable[[], None]] = {
        "s2.estop.press": estop_on,
        "s2.estop.release": estop_off,
        "s7.estop.press": estop_on,
        "s7.estop.release": estop_off,
        "s7.switch.Xneg": lambda: sim.set_inputs(1 << 3),  # DI4 in the simulator
        "s7.switch.Xneg.release": lambda: sim.set_inputs(0),
        "s10.power.off": lambda: sim.reboot(silent_s=1.5, not_ready_s=1.0),
    }
    if pcap is not None:
        hooks["final.capture_stopped"] = lambda: write_capture_from_sim(sim, pcap)
    return hooks


def argv_for(path: Path, out: Path, *extra: str) -> list[str]:
    return [
        "--socket", str(path), "--out-dir", str(out), "--pcap", str(out.parent / "cap.pcap"),
        "--date", "2026-09-15", "--estop-wait-s", "3", "--connect-timeout-s", "10",
        "--power-cycle-timeout-s", "30", "--vendor-log-dir", str(out.parent / "no-vendor-log"),
        *extra,
    ]  # fmt: skip


def jogs_and_homes(sim: CardSimulator) -> list[tuple[str, tuple[int, ...]]]:
    """Accepted 0x65 home/jog/go-to vectors (``sim.requests`` survives ``reboot()``)."""
    names = {2: "home", 3: "jog", 5: "go_to"}
    with sim._lock:
        reqs = [r for r in sim.requests if r.vector is not None and r.action == "replied"]
    return [
        (names[r.vector[3]], tuple(r.vector[3:]))
        for r in reqs
        if r.vector[:2] == (0x40, 0x65) and len(r.vector) > 3 and r.vector[3] in names
    ]


def step(report: dict[str, Any], n: int) -> dict[str, Any]:
    return next(s for s in report["steps"] if s["n"] == n)


def verdict(s: dict[str, Any], hid: str) -> str:
    return next(h["verdict"] for h in s["hypotheses"] if h["id"] == hid)


# ------------------------------------------------------------------------------- tests


def test_full_session_all_steps_on_sim(tmp_path: Path) -> None:
    out = tmp_path / "m1-2026-09-15"
    pcap = tmp_path / "cap.pcap"
    with daemon_on_sim() as (daemon, sim, path):
        op = ScriptedOperator(hooks=physical_hooks(sim, pcap))
        rc = m1.main(argv_for(path, out), operator=op)
        assert rc == m1.EXIT_OK, op.said[-5:]
        assert daemon.gate.arming.state is ArmState.DISARMED
        motions = jogs_and_homes(sim)

    report = json.loads((out / "report.json").read_text())
    md = (out / "report.md").read_text()
    assert report["outcome"] == "completed"
    assert [s["n"] for s in report["steps"]] == list(range(1, 11))
    for s in report["steps"]:
        assert s["status"] in ("done", "partial"), (s["n"], s["status"], s["events"][-3:])
        assert s["started"] and s["finished"]
        assert f"## Step {s['n']}:" in md
    assert all(item["confirmed"] for item in report["checklist"])

    s1 = step(report, 1)
    assert verdict(s1, "K") == "CONFIRMED"
    assert verdict(s1, "bus_cycle") == "CONFIRMED"
    assert verdict(s1, "version") == "CONFIRMED"
    assert s1["results"]["fifo_margin_1016"] >= 0

    s2 = step(report, 2)
    assert verdict(s2, "estop_bit30") == "CONFIRMED"
    assert any(e["kind"] == "ack_estop" for e in s2["events"])

    s3 = step(report, 3)
    jog = s3["motions"][0]
    assert jog["confirmed"] and jog["sent_words"] == [3, 0, 20000, 5999, 59990, 5000]
    assert jog["matches_plan"] is True
    assert jog["outcome"] == "completed"
    assert verdict(s3, "O6_firmware_gating") == "CONFIRMED"
    assert s3["results"]["x_pulse_position_delta"] == 5000  # simulator: µm
    assert s3["results"]["jog_while_moving"]["sent"] is False  # daemon refuses PC-side
    assert 1 in s3["results"]["byte2_values"] and 3 in s3["results"]["byte3_values"]

    s4 = step(report, 4)
    assert s4["status"] == "partial" and s4["deferred"]
    assert s4["results"]["planned_vector"]["words"][:2] == [3, -0x80000000]

    s5 = step(report, 5)
    cont = s5["motions"][0]
    assert cont["sent_words"] == [3, 0, 20000, 5999, 59990, 400000]
    assert [p["words"] for p in cont["planned"]][1] == [1, 1, 2, 5000, 50000]
    assert any(n == "jog" and w[5] == 400000 for n, w in motions)

    s6 = step(report, 6)
    assert [m["outcome"] for m in s6["motions"]] == ["completed", "completed"]
    assert verdict(s6, "homed_bit15_X") == "CONFIRMED"
    assert verdict(s6, "homed_bit15_Y") == "CONFIRMED"
    assert [w for n, w in motions if n == "home"] == [(2, 1, 0), (2, 2, 0)]

    s7 = step(report, 7)
    table = {r["switch"]: r for r in s7["results"]["limit_switch_table"]}
    assert table["X-"]["di_changed"] == [{"port": 4, "level_held": 1}]
    assert table["X-"]["di_restored_after_release"] is True
    assert s7["results"]["y_jog_direction"] == "+"
    assert s7["results"]["y_slot1_delta"] == 5000

    s8 = step(report, 8)
    assert verdict(s8, "C2_margin_empty") in ("CONFIRMED", "CONTRADICTED")
    assert s8["deferred"] and s8["wine_variants"][0]["run"]
    assert [m["mark"] for m in s8["wine_variants"][0]["marks"]] == ["start", "end"]

    s9 = step(report, 9)
    assert len(s9["wine_variants"][0]["marks"]) == 4
    assert any(o["key"] == "continue_motion" for o in s9["observations"])

    s10 = step(report, 10)
    assert s10["results"]["reconnected"] is True
    assert verdict(s10, "N5_exception2") == "CONFIRMED"
    assert s10["results"]["version_after_power_up"][1] == 20152

    cap = report["capture"]
    assert cap["dissector_returncode"] == 0
    assert "transactions:" in cap["dissector_summary"]
    assert (out / "dissector-summary.txt").exists() and (out / "pcap-decoded.txt").exists()
    traffic3 = cap["steps"]["3"]
    assert any("READ 1000 x1" == k for k in traffic3["by_request"])  # step marker
    assert "jog relative 0x65 <- [3, 0, 20000, 5999, 59990, 5000]" in traffic3["writes"]
    assert any(k.startswith("stop 0x65 <- [1, 31, 2,") for k in cap["steps"]["2"]["writes"])
    assert "## Capture" in md


def test_motions_refused_when_operator_answers_no(tmp_path: Path) -> None:
    out = tmp_path / "m1"
    with daemon_on_sim() as (daemon, sim, path):
        before = list(sim.axis_pos)
        op = ScriptedOperator(motion="N", hooks=physical_hooks(sim, None))
        rc = m1.main(argv_for(path, out, "--no-dissector"), operator=op)
        assert rc == m1.EXIT_OK
        assert jogs_and_homes(sim) == []
        assert list(sim.axis_pos) == before
        assert not any(t.new is ArmState.MOTION_ARMED for t in daemon.gate.arming.history)

    report = json.loads((out / "report.json").read_text())
    assert [s["n"] for s in report["steps"]] == list(range(1, 11))
    motions = [m for s in report["steps"] for m in s["motions"]]
    assert len(motions) == 5  # step 3, step 5, step 6 X and Y, step 7 Y
    for m in motions:
        assert m["confirmed"] is False
        assert m["outcome"].startswith("declined")
        assert m["sent_words"] is None
    for n in (3, 5, 6, 7):
        assert step(report, n)["status"] == "partial"
    assert not any(e["kind"] == "arm_motion" for s in report["steps"] for e in s["events"])
    # the planned vector was shown before each motion prompt
    motion_prompts = [i for i, (_, k, _) in enumerate(op.prompts) if ".motion." in k]
    assert motion_prompts
    assert any("[3, 0, 20000, 5999, 59990, 5000]" in t for t in op.said)
    assert any("[2, 1, 0]" in t for t in op.said)


def test_checklist_refusal_stops_before_any_step(tmp_path: Path) -> None:
    out = tmp_path / "m1"
    with daemon_on_sim() as (daemon, sim, path):
        op = ScriptedOperator(answers={"checklist.gantry_mid_bed": "n"})
        rc = m1.main(argv_for(path, out), operator=op)
        assert rc == m1.EXIT_ABORTED
        assert jogs_and_homes(sim) == []
        assert daemon.gate.arming.state is ArmState.DISARMED
    report = json.loads((out / "report.json").read_text())
    assert report["outcome"] == "refused: safety checklist"
    assert report["steps"] == []
    assert [c["key"] for c in report["checklist"]][-1] == "gantry_mid_bed"
    assert not any(k.startswith("s1.") for _, k, _ in op.prompts)


def test_checklist_needs_explicit_yes(tmp_path: Path) -> None:
    out = tmp_path / "m1"
    with daemon_on_sim() as (_daemon, _sim, path):
        op = ScriptedOperator(answers={"checklist.laser_off": ""})
        assert m1.main(argv_for(path, out), operator=op) == m1.EXIT_ABORTED
    assert json.loads((out / "report.json").read_text())["checklist"][0]["confirmed"] is False


def test_ctrl_c_during_session_stops_and_disarms(tmp_path: Path) -> None:
    out = tmp_path / "m1"

    def interrupt() -> None:
        raise KeyboardInterrupt

    with daemon_on_sim() as (daemon, sim, path):
        op = ScriptedOperator(hooks={"s3.obs.moved": interrupt})
        rc = m1.main(argv_for(path, out, "--steps", "3"), operator=op)
        assert rc == m1.EXIT_INTERRUPTED
        assert daemon.gate.arming.state is ArmState.DISARMED
    report = json.loads((out / "report.json").read_text())
    assert report["outcome"] == "interrupted"
    s3 = report["steps"][0]
    assert s3["status"] == "interrupted"
    assert any(e["kind"] == "stop" for e in s3["events"])


def test_unreachable_daemon(tmp_path: Path) -> None:
    out = tmp_path / "m1"
    op = ScriptedOperator()
    rc = m1.main(argv_for(tmp_path / "missing.sock", out), operator=op)
    assert rc == m1.EXIT_UNREACHABLE
    report = json.loads((out / "report.json").read_text())
    assert report["outcome"] == "daemon unreachable"
    assert not op.prompts  # nothing asked, nothing sent


def test_earlier_report_is_kept(tmp_path: Path) -> None:
    out = tmp_path / "m1"
    out.mkdir()
    (out / "report.json").write_text("{}")
    (out / "report.md").write_text("old")
    m1.main(argv_for(tmp_path / "missing.sock", out), operator=ScriptedOperator())
    kept = sorted(p.name for p in out.iterdir())
    assert "report.json" in kept and any(
        n.startswith("report-") and n.endswith(".json") for n in kept
    )


def test_console_operator_only_explicit_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["", "Y", "yes", "no", "yeah", " y "])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    op = m1.ConsoleOperator()
    assert [op.confirm("k", "?") for _ in range(6)] == [False, True, True, False, False, True]

    def eof(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)
    with pytest.raises(m1.SessionAborted):
        op.confirm("k", "?")


def test_option_validation_and_steps(tmp_path: Path) -> None:
    assert m1.parse_steps("1-3,5,10") == (1, 2, 3, 5, 10)
    with pytest.raises(ValueError):
        m1.parse_steps("0-2")
    sock = str(tmp_path / "x.sock")
    for bad in (
        ["--step-mm", "20"],
        ["--cont-speed", "200"],
        ["--cont-hold-s", "9"],
        ["--steps", "11"],
    ):
        assert m1.main(["--socket", sock, *bad], operator=ScriptedOperator()) == m1.EXIT_USAGE


def test_render_markdown_minimal() -> None:
    md = m1.render_markdown({"date": "2026-09-15", "steps": [], "checklist": []})
    assert md.startswith("# M1 bench session report (2026-09-15)")


def test_label_write_uses_11_section_8_names() -> None:
    assert (
        m1.label_write([0x40, 0x65, 5, 1, 1, 2, 2000, 20000])
        == "stop 0x65 <- [1, 1, 2, 2000, 20000]"
    )
    assert m1.label_write([0x40, 0x65, 3, 2, 2, 0]).startswith("home ")
    neg = m1.label_write([0x40, 0x65, 6, 3, 0, 20000, 5999, 59990, (-5000) & 0xFFFFFFFF])
    assert neg == "jog relative 0x65 <- [3, 0, 20000, 5999, 59990, -5000]"
    assert m1.label_write([0x40, 0x65, 6, 3, 0x80000000, 1, 1, 10, 100000]).startswith(
        "jog absolute"
    )
    assert m1.label_write([0x40, 0x67, 1, 2]).startswith("FIFO start")


# ------------------------------------------------- reconnect mid-step (D9/R12, STATUS §5.10)


def _session(path: Path, out: Path, op: ScriptedOperator) -> Any:
    ns = m1._parser().parse_args(argv_for(path, out, "--no-dissector"))
    return m1.M1Session(m1.options_from_args(ns), op)


def _break_all_ipc_connections(daemon: MccDaemon) -> None:
    """Drop every server-side IPC connection - a link blip, as far as a client can tell."""
    server = daemon.server
    assert server is not None
    for conn in list(server._conns.values()):
        conn.close()


def test_reconnect_mid_step_rearms_before_the_motion(tmp_path: Path) -> None:
    """A reconnect drops arming and the right to move (D9/R12); the tool arms again.

    Forced exactly where it hurts: the step arms, the link blips, an idempotent call
    (``status``, retried by ``DaemonLink``) reconnects, and only then does the motion go out.
    Before this, that motion was refused with a bare ``arming:`` message.
    """
    out = tmp_path / "m1"
    with daemon_on_sim() as (daemon, sim, path):
        s = _session(path, out, ScriptedOperator())
        rec = m1.StepRecord(m1.STEPS[2])  # step 3, the X jog
        s._current = rec
        s._arm(rec)
        first_generation = s.link.generation
        assert daemon.gate.arming.state is ArmState.MOTION_ARMED
        assert s.link.call("status")["arm_owner_is_self"] is True

        _break_all_ipc_connections(daemon)
        assert wait_for(lambda: daemon.gate.arming.state is ArmState.DISARMED, 5.0)
        s.link.call("status")  # idempotent: reconnects on a new connection
        assert s.link.generation == first_generation + 1

        res = s._motion_call(rec, "jog_step", slot=0, mm=1.0, speed=5.0)
        assert res.get("words")
        kinds = [e["kind"] for e in rec.data["events"]]
        assert "arming_lost" in kinds and "arming_restored" in kinds
        assert kinds.count("arm_motion") == 2
        assert s._arm_generation == s.link.generation
        assert s.link.call("status")["arm_owner_is_self"] is True
        assert [k for k, _ in jogs_and_homes(sim)] == ["jog"]
        s.link.close()


def test_motion_step_refuses_to_continue_when_arming_cannot_be_restored(tmp_path: Path) -> None:
    """If re-arming fails the step stops there: ``ArmingLost``, and nothing is sent."""
    out = tmp_path / "m1"
    with daemon_on_sim() as (daemon, sim, path):
        s = _session(path, out, ScriptedOperator())
        rec = m1.StepRecord(m1.STEPS[2])
        s._current = rec
        s._arm(rec)

        _break_all_ipc_connections(daemon)
        sim.inject_alarm(alarm1=1 << 30)  # E-stop: the daemon refuses to arm again
        assert wait_for(lambda: daemon.gate.arming.estop_latched, 5.0)
        s.link.call("status")  # reconnect

        with pytest.raises(m1.ArmingLost):
            s._motion_call(rec, "jog_step", slot=0, mm=1.0, speed=5.0)
        assert jogs_and_homes(sim) == []
        assert not s._armed
        assert any(e["kind"] == "arming_lost" for e in rec.data["events"])
        assert not any(e["kind"] == "arming_restored" for e in rec.data["events"])
        sim.inject_alarm(alarm1=0)
        s.link.close()


def test_motion_prompt_records_the_lost_arming_and_marks_the_step_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_motion`` turns an unrecoverable ``ArmingLost`` into a recorded, harmless outcome."""
    out = tmp_path / "m1"
    with daemon_on_sim() as (_daemon, sim, path):
        op = ScriptedOperator()
        s = _session(path, out, op)
        rec = m1.StepRecord(m1.STEPS[2])
        s._current = rec

        def boom(_rec: Any, what: str) -> None:
            raise m1.ArmingLost(f"link reconnected and re-arming failed ({what})")

        monkeypatch.setattr(s, "_ensure_armed", boom)
        entry = s._motion(
            rec,
            "jog_x",
            "Jog X +1 mm",
            [],
            "X moves",
            lambda e: s._motion_call(rec, "jog_step", slot=0, mm=1.0, speed=5.0),
        )
        assert entry is not None
        assert entry["outcome"].startswith("arming lost:")
        assert entry["error"]["code"] == "arming"
        assert rec.incomplete
        assert jogs_and_homes(sim) == []
        assert any("Re-run this step on its own with --steps 3" in t for t in op.said)
        s._disarm(rec, "test end")
        s.link.close()
