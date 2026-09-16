"""``nexcut-mccd`` one-shot commands against a ``serve --sim`` daemon (PORT-PLAN §4 M1 step 4).

The daemon runs through ``cli.main(["serve", "--sim", ...])`` on a thread (stopped with
``stop_event``); one test also runs it as a real process and ends it with SIGTERM.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from nexcut.mccd import cli
from nexcut.mccd.ipc import MccdClient


def wait_for(cond: Callable[[], object], timeout: float = 5.0, period: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if cond():
                return True
        except OSError:
            pass
        time.sleep(period)
    return False


def _status(path: Path) -> dict[str, Any]:
    with MccdClient(path, timeout=2.0) as c:
        return c.call("status")


@pytest.fixture
def served() -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(prefix="nxcli"))
    path = d / "mccd.sock"
    stop = threading.Event()
    rc: list[int] = []
    t = threading.Thread(
        target=lambda: rc.append(
            cli.main(["serve", "--sim", "--socket", str(path)], stop_event=stop)
        ),
        daemon=True,
    )
    t.start()
    try:
        assert wait_for(lambda: path.exists() and _status(path)["machine_state"] == "READY", 8.0), (
            "daemon did not come up"
        )
        yield path
    finally:
        stop.set()
        t.join(timeout=10.0)
        shutil.rmtree(d, ignore_errors=True)
    assert rc == [0]


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_status_json_and_text(served: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(["status", "--json", "--socket", str(served)], capsys)
    assert code == 0
    snap = json.loads(out)
    assert snap["link"] == "CONNECTED" and snap["arm_state"] == "DISARMED"
    assert snap["program_version"] == 20152 and snap["k"] == 1000
    code, out, _ = run(["status", "--socket", str(served)], capsys)
    assert code == 0
    assert "link CONNECTED" in out and "arm DISARMED" in out and "state READY" in out
    assert "pos mm" in out and "alarms: none" in out and "K=1000 " in out


def test_one_shot_arm_does_not_survive_its_process(
    served: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """docs/DECISIONS.md D9: arming belongs to the connection that asked for it."""
    s = ["--socket", str(served)]
    code, out, err = run(["arm", *s], capsys)
    assert code == 0 and json.loads(out)["arm_state"] == "MOTION_ARMED"
    assert "D9" in err and "jog --arm" in err
    assert wait_for(lambda: _status(served)["arm_state"] == "DISARMED")
    code, _, err = run(["jog", "X", "5", "--wait", *s], capsys)
    assert code == 1 and "D9" in err
    assert _status(served)["axes"][0]["position_counts"] == 0


def test_one_shot_motion_flow(served: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = ["--socket", str(served)]
    # Nothing moves without an explicit arm (PORT-PLAN §8).
    code, _, err = run(["jog", "X", "5", *s], capsys)
    assert code == 1 and err
    assert _status(served)["axes"][0]["position_counts"] == 0

    # --arm arms, moves and disarms inside one connection (D9); it implies --wait.
    code, out, _ = run(["jog", "X", "5", "--speed", "50", "--arm", *s], capsys)
    assert code == 0, capsys.readouterr()
    assert json.loads(out.splitlines()[0])["words"] == [3, 0, 50000, 5999, 59990, 5000]  # 11 §2 V2
    assert _status(served)["axes"][0]["position_counts"] == 5000
    assert _status(served)["arm_state"] == "DISARMED"

    code, out, _ = run(["jog", "x", "-2", "--arm", *s], capsys)  # negative positional
    assert code == 0
    assert _status(served)["axes"][0]["position_counts"] == 3000

    # A step longer than speed x deadman is leased by the gate: the CLI refreshes it for its
    # expected duration, otherwise the 200 ms deadman would stop the 0.3 s simulator move.
    code, out, _ = run(["jog", "Y", "20", "--speed", "20", "--arm", *s], capsys)
    assert code == 0
    assert json.loads(out.splitlines()[0])["deadman"] is True
    assert _status(served)["axes"][1]["position_counts"] == 20000

    code, out, _ = run(["home", "X", "--arm", *s], capsys)
    assert code == 0 and json.loads(out.splitlines()[0]) == {"homing": [0]}
    snap = _status(served)
    assert 0 in snap["homed_slots"] and 1 not in snap["homed_slots"]
    assert snap["axes"][0]["position_counts"] == 0
    assert snap["arm_state"] == "DISARMED"

    code, out, _ = run(["stop", *s], capsys)
    assert code == 0 and "stop_all" in json.loads(out)["sent"]
    code, out, _ = run(["disarm", *s], capsys)
    assert code == 0 and json.loads(out)["arm_state"] == "DISARMED"


def test_refusals_and_estop(served: Path, capsys: pytest.CaptureFixture[str]) -> None:
    s = ["--socket", str(served)]
    assert wait_for(lambda: _status(served)["machine_state"] == "READY")
    # Deny-listed axes are refused even with --arm (11 §3.3 rows "lift table" / V9-V10).
    code, _, err = run(["jog", "W", "1", "--arm", *s], capsys)  # lift table: DENY in M1
    assert code == 1 and "refused" in err
    code, _, err = run(["home", "W", "--arm", *s], capsys)
    assert code == 1 and "refused" in err
    code, _, err = run(["jog", "Q", "1", *s], capsys)
    assert code == 2 and "unknown axis" in err
    code, out, _ = run(["estop", *s], capsys)
    assert code == 0 and json.loads(out)["estop_latched"] is True
    code, _, err = run(["arm", *s], capsys)
    assert code == 1 and "arming" in err
    code, out, _ = run(["ack-estop", *s], capsys)
    assert code == 0 and json.loads(out)["estop_latched"] is False
    code, out, _ = run(["ctl", "read_block", '{"addr": 1000, "n": 2}', *s], capsys)
    assert code == 0 and json.loads(out)["result"]["words"][1] == 20152


def test_unreachable_daemon_and_usage(capsys: pytest.CaptureFixture[str]) -> None:
    d = Path(tempfile.mkdtemp(prefix="nxcli"))
    try:
        missing = str(d / "none.sock")
        for argv in (["status", "--socket", missing], ["stop", "--socket", missing],
                     ["jog", "X", "1", "--socket", missing]):  # fmt: skip
            code, _, err = run(argv, capsys)
            assert code == cli.EXIT_UNREACHABLE and "cannot reach" in err
        code, _, _ = run(["ctl", "status", "[1]", "--socket", missing], capsys)
        assert code == cli.EXIT_USAGE
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert cli.main([]) == 0


def test_serve_process_sim_and_sigterm() -> None:
    d = Path(tempfile.mkdtemp(prefix="nxcli"))
    path = d / "mccd.sock"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(cli.__file__).resolve().parents[2])
    proc = subprocess.Popen(
        [sys.executable, "-m", "nexcut.mccd.cli", "serve", "--sim", "--socket", str(path)],
        env=env,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert wait_for(lambda: path.exists() and _status(path)["link"] == "CONNECTED", 15.0)
        snap = _status(path)
        assert snap["arm_state"] == "DISARMED"  # serving never arms
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=10.0) == 0
        _, err = proc.communicate(timeout=5.0)
        assert "simulator 127.0.0.1:" in err
        assert not path.exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        shutil.rmtree(d, ignore_errors=True)
