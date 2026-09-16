#!/usr/bin/env python3
"""Guided M1 confirmation session: the 10 bench steps of 11-static-findings §7.

Runbook: ``docs/M1-BENCH-SESSION.md``. Run it with the project venv while ``nexcut-mccd``
is serving the card and ``tcpdump`` is capturing::

    .venv/bin/python tools/m1_session.py [--socket PATH] [--steps 1-10] [--pcap FILE]

What the tool does (PORT-PLAN §8.2, 11 §7):

* It is a plain **IPC client** of ``nexcut-mccd`` (:class:`nexcut.mccd.ipc.MccdClient`). It
  never opens the card socket and has no raw write: every vector is built and checked by
  the daemon's safety gate (docs/DECISIONS.md D5). The card address is whatever the operator
  passed to ``nexcut-mccd serve --card-ip`` (or ``--sim``).
* It refuses to start unless the operator confirms every item of the safety checklist
  (laser key off, E-stop in reach, gantry mid-bed, bed clear, gas closed, single master).
* Reads (``read_block``, allow-listed by the daemon, 11 §3.2) run automatically.
* Every motion is preceded by the planned vector(s) and needs an explicit ``y``; anything
  else (including an empty answer) skips that motion. The tool arms the daemon only after
  that ``y`` and disarms at the end of each step.
* Observations (automatic decodes and the operator's typed notes) go to
  ``~/mlaser-captures/m1-<date>/report.json`` and ``report.md`` after every step.
* At the end it runs ``python -m nexcut.mcc.dissector`` on the pcap (if present), appends
  the summary and a per-step traffic census, and copies the Wine/Mlaser logs it finds.

Steps whose native half the daemon cannot perform in this build (11 §7 step 4 absolute
move, step 8 FIFO stream: ``nexcut-mccd`` has no such IPC command yet) are recorded as
*deferred* and offer the vendor/Wine variant instead (docs/analysis/10, PORT-PLAN §6 step 1).

Ctrl-C at any time sends ``stop`` and ``disarm`` and writes the report.

Exit status: 0 session finished, 1 refused / aborted, 2 usage, 3 daemon unreachable,
130 interrupted.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

_REPO = Path(__file__).resolve().parents[1]
try:  # run from a checkout without an installed package
    import nexcut  # noqa: F401
except ImportError:  # pragma: no cover - the venv has the package installed
    sys.path.insert(0, str(_REPO / "src"))

from nexcut.core.config import default_socket_path  # noqa: E402
from nexcut.mcc import commands as C  # noqa: E402
from nexcut.mcc.registers import (  # noqa: E402
    AXIS_RO_WORDS,
    AXIS_RW_WORDS_ON_WIRE,
    AxisRO,
    AxisRW,
    Status,
    SystemRW,
)
from nexcut.mccd.ipc import IpcError, MccdClient  # noqa: E402

__all__ = [
    "EXIT_ABORTED",
    "EXIT_INTERRUPTED",
    "EXIT_OK",
    "EXIT_UNREACHABLE",
    "EXIT_USAGE",
    "SAFETY_CHECKLIST",
    "STEPS",
    "ConsoleOperator",
    "M1Session",
    "Operator",
    "Options",
    "SessionAborted",
    "main",
    "render_markdown",
]

EXIT_OK = 0
EXIT_ABORTED = 1
EXIT_USAGE = 2
EXIT_UNREACHABLE = 3
EXIT_INTERRUPTED = 130

# ------------------------------------------------------------------------------------------
# Expectations the session checks (sources cited; values not proven are marked UNVERIFIED)
# ------------------------------------------------------------------------------------------

EXPECT_K = 1000
"""Reg 50017 card units per mm (A1 §2, logs 50 -> 50000; 11 §3.2)."""
EXPECT_BUS_CYCLE_US = 250
"""Reg 50005 = ``AX.InterpolationCycle`` XML default 250 µs (11 §5.3). UNVERIFIED card value."""
EXPECT_ZF_TYPE = 1
"""Reg 50006 = ``ZF.ZFType`` 1 on this machine (11 §2 header)."""
MIN_HARDWARE_VER = 20152
"""``MinHardwareVer`` / this card's version (11 §4.1 row 1)."""
XML_PULSES_PER_MM: dict[int, tuple[int, float]] = {0: (8000, 31.003), 1: (8000, 31.009)}
"""(pulses per rev, lead mm) for X (card axis-0 words 9/10) and Y (XML MAC_1), 11 §5.3 / C23."""
ESTOP_BIT = 1 << 30
"""alarm_1 (reg 1006) bit 30 = emergency stop active (11 §4.2)."""
MARKER_READ = (1000, 1)
"""``READ 1000/1`` written at every step boundary so the pcap can be cut per step. Port
choice: the daemon itself never reads 1000/1 (it reads 1000/2 once and 1000/36 every 30 ms,
11 §3.2), so the marker is unique in the capture. UNVERIFIED that the card answers a
1-word read of block 1000 (a failure is harmless and only recorded)."""

MAX_STEP_MM = 10.0
"""Upper bound of ``--step-mm``: the daemon's un-homed step limit (docs/DECISIONS.md D1)."""
MAX_SPEED_MM_S = 50.0
"""Upper bound of the tool's jog speeds (11 §7 uses 20 and 50 mm/s). Port choice."""
MAX_CONT_HOLD_S = 3.0
"""Upper bound of ``--cont-hold-s`` (continuous jog travel <= 150 mm at 50 mm/s). Port choice."""

SAFETY_CHECKLIST: tuple[tuple[str, str], ...] = (
    (
        "laser_off",
        "Laser power is OFF: CO2 tube PSU key/enable switched off AND the fibre source "
        "emission key off (PORT-PLAN §8.2 hardware laser interlock).",
    ),
    (
        "estop_reachable",
        "The machine's hardware E-stop is within reach of your hand for the whole session, "
        "and you have checked that it stops the machine.",
    ),
    (
        "gantry_mid_bed",
        "The gantry is parked mid-bed: at least 100 mm from every X and Y end stop.",
    ),
    (
        "bed_clear",
        "Material, tools and loose objects are removed from the bed and the gantry path; "
        "the cutting head / Z is clear of the bed; nobody else is near the machine.",
    ),
    (
        "gas_closed",
        "Assist-gas supplies (air / N2 / O2) are closed at the cylinder or compressor.",
    ),
    (
        "single_master",
        "Only ONE program talks to the card: the machine's own control PC is disconnected "
        "and Mlaser (Windows or Wine) is not running while nexcut-mccd is.",
    ),
)
"""Every item must be answered ``y`` or the session does not start (PORT-PLAN §8.2)."""


@dataclass(frozen=True, slots=True)
class StepInfo:
    """One row of 11 §7."""

    n: int
    title: str
    action: str
    settles: str


STEPS: tuple[StepInfo, ...] = (
    StepInfo(
        1,
        "Connect: start-up reads",
        "READ 1000/2, READ 1000/36, READ 50000/26, READ 50200/100, [9999,5,0,0]",
        "K = word 17 == 1000; bus cycle word 5 == 250; ZFType word 6; axis-0/1 lead/pulses "
        "vs XML (C23); version",
    ),
    StepInfo(
        2,
        "Block 5000 and the E-stop",
        "READ 5000/9 idle, with E-stop pressed, after release; read 1006 each time",
        "N3 block 5000 contents; alarm_1 bit 30; card-side DI inversion",
    ),
    StepInfo(
        3,
        "Jog X +5 mm at 20 mm/s",
        "[3, 0, 20000, 5999, 59990, 5000], poll 2000; repeat while moving",
        "O6 firmware gating (moves => no gate); exception 3 on jog-while-moving (N5); axis "
        "status byte 2/3 values",
    ),
    StepInfo(
        4,
        "Bit 31 = absolute",
        "[3, 0x80000000, 20000, 5999, 59990, 100000] twice",
        "bit 31 = absolute",
    ),
    StepInfo(
        5,
        "Continuous jog and key-release stop",
        "continuous jog at 50 mm/s, release with [1,1,2,2000,20000]",
        "stop profile / vd unit",
    ),
    StepInfo(
        6,
        "Home X, then Y",
        "[2, 1, 0] (home X), then [2, 2, 0]",
        "home vector, card-side speeds and back-off, homed bit 15",
    ),
    StepInfo(
        7,
        "Limit switches by hand; Y2 tracking",
        "press each limit switch by hand (motors disabled), read 1004 and 2000+10*slot",
        "O8 polarity; Y2 slot tracking during a Y jog (O7 dual drive)",
    ),
    StepInfo(
        8,
        "FIFO tick period",
        "0x67<-[1]; one frame of a 100 mm X move at 50 mm/s (duty 0), read 1015/1016 "
        "before/after; stream the rest, 0x67<-[2]; time the move, count 3000 items",
        "O1 card tick period; reg-1016 units (C2); frame-id rule; FIFO start without licence "
        "exchange (O6)",
    ),
    StepInfo(
        9,
        "Pause / Continue / Stop in the vendor tool",
        "short dry job in the Windows tool: Pause, Continue, Stop",
        "N2 resume path; stop-manu frames",
    ),
    StepInfo(
        10,
        "Power-cycle, READ 1000/2 immediately",
        "power-cycle the card and send READ 1000/2 immediately",
        "exception 2 semantics (N5)",
    ),
)
STEP_BY_N = {s.n: s for s in STEPS}

VENDOR_LOG_DIR_DEFAULT = (
    Path.home()
    / ".wine/drive_c/users"
    / os.environ.get("USER", "user")
    / "AppData/Local/NexCut/Log"
)
"""``%LOCALAPPDATA%\\NexCut\\Log`` of the Wine prefix (PORT-PLAN §6 step 1, 04 §9)."""

_SAFE_RETRY = frozenset({"ping", "status", "read_block", "stop", "disarm", "estop"})
"""Idempotent IPC commands that may be re-sent after a broken connection. Motion commands
are never re-sent: the first request may already have reached the card."""


# ------------------------------------------------------------------------------------------
# Operator interface
# ------------------------------------------------------------------------------------------


class SessionAborted(Exception):
    """The session cannot continue (operator refusal, closed input)."""


class Operator(Protocol):
    """Where prompts go. ``key`` is a stable id of the prompt (tests script answers by key)."""

    def say(self, text: str) -> None:
        """Show information."""
        ...

    def ask(self, key: str, prompt: str) -> str:
        """Free-text answer (may be empty)."""
        ...

    def confirm(self, key: str, prompt: str) -> bool:
        """True only for an explicit yes."""
        ...

    def wait(self, key: str, prompt: str) -> None:
        """Wait until the operator is ready (Enter)."""
        ...


def is_yes(answer: str) -> bool:
    """Only ``y`` / ``yes`` (any case) count as yes; everything else is No."""
    return answer.strip().lower() in ("y", "yes")


class ConsoleOperator:
    """Terminal operator (``input()``). End of input aborts the session (safe default)."""

    def say(self, text: str) -> None:
        print(text, flush=True)

    def ask(self, key: str, prompt: str) -> str:
        try:
            return input(f"{prompt} ").strip()
        except EOFError as exc:
            raise SessionAborted("operator input closed (EOF)") from exc

    def confirm(self, key: str, prompt: str) -> bool:
        return is_yes(self.ask(key, f"{prompt} [y/N]"))

    def wait(self, key: str, prompt: str) -> None:
        self.ask(key, f"{prompt} [Enter]")


# ------------------------------------------------------------------------------------------
# Daemon access
# ------------------------------------------------------------------------------------------


class DaemonUnavailable(Exception):
    """``nexcut-mccd`` cannot be reached on its socket."""


class DaemonLink:
    """IPC client with lazy (re)connect. Only idempotent commands are retried."""

    def __init__(self, socket_path: Path, timeout: float = 10.0) -> None:
        self.path = socket_path
        self.timeout = timeout
        self._client: MccdClient | None = None

    def _get(self) -> MccdClient:
        if self._client is None:
            try:
                self._client = MccdClient(self.path, timeout=self.timeout)
            except OSError as exc:
                raise DaemonUnavailable(f"{self.path}: {exc}") from exc
        return self._client

    def call(self, cmd: str, **args: Any) -> Any:
        """Result of ``cmd`` or :class:`IpcError`; :class:`DaemonUnavailable` if unreachable."""
        attempts = 2 if cmd in _SAFE_RETRY else 1
        for attempt in range(attempts):
            client = self._get()
            try:
                return client.call(cmd, **args)
            except IpcError:
                raise
            except (OSError, ConnectionError, ValueError) as exc:
                self.close()
                if attempt == attempts - 1:
                    raise DaemonUnavailable(f"{cmd}: {exc}") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


class _Refresher(threading.Thread):
    """Deadman refresh on its own connection until a fixed deadline (PORT-PLAN §8.2).

    The deadline is set before the thread starts, so a hung tool stops refreshing by itself
    and the daemon's 200 ms deadman then stops the axis.
    """

    def __init__(self, socket_path: Path, slot: int, until_mono: float, period: float = 0.05):
        super().__init__(name=f"m1-refresh-{slot}", daemon=True)
        self.socket_path = socket_path
        self.slot = slot
        self.until = until_mono
        self.period = period
        self.calls = 0
        self.ended_by = ""
        self._stop_evt = threading.Event()

    def run(self) -> None:
        try:
            with MccdClient(self.socket_path, timeout=2.0) as c:
                while time.monotonic() < self.until:
                    if self._stop_evt.is_set():
                        self.ended_by = "stopped by the tool"
                        return
                    alive = bool(c.call("jog_refresh", slot=self.slot).get("alive"))
                    self.calls += 1
                    if not alive:
                        self.ended_by = "lease already gone (daemon stopped the jog)"
                        return
                    self._stop_evt.wait(self.period)
                self.ended_by = "deadline"
        except Exception as exc:  # recorded; the daemon's deadman covers the rest
            self.ended_by = f"error: {exc}"

    def stop(self) -> None:
        self._stop_evt.set()
        self.join(timeout=3.0)


class _LinkMonitor(threading.Thread):
    """Polls ``status`` on its own connection and keeps every link/error change (step 10)."""

    def __init__(self, socket_path: Path, period: float = 0.1) -> None:
        super().__init__(name="m1-link-monitor", daemon=True)
        self.socket_path = socket_path
        self.period = period
        self.changes: list[dict[str, Any]] = []
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()

    def run(self) -> None:
        client: MccdClient | None = None
        last: tuple[Any, ...] | None = None
        while not self._stop_evt.is_set():
            key: tuple[Any, ...]
            try:
                if client is None:
                    client = MccdClient(self.socket_path, timeout=2.0)
                snap = client.call("status")
                key = (True, snap.get("link"), snap.get("last_error"), snap.get("program_version"))
            except Exception as exc:
                if client is not None:
                    client.close()
                    client = None
                key = (False, None, f"daemon unreachable: {type(exc).__name__}", None)
            if key != last:
                last = key
                with self._lock:
                    self.changes.append(
                        {
                            **_stamp(),
                            "daemon_reachable": key[0],
                            "link": key[1],
                            "last_error": key[2],
                            "program_version": key[3],
                        }
                    )
            self._stop_evt.wait(self.period)
        if client is not None:
            client.close()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self.changes)

    def stop(self) -> None:
        self._stop_evt.set()
        self.join(timeout=3.0)


# ------------------------------------------------------------------------------------------
# Small helpers
# ------------------------------------------------------------------------------------------


def _stamp(t: float | None = None) -> dict[str, Any]:
    t = time.time() if t is None else t
    return {
        "t": datetime.fromtimestamp(t).astimezone().isoformat(timespec="milliseconds"),
        "epoch": t,
    }


def _s32(w: int) -> int:
    w &= 0xFFFFFFFF
    return w - (1 << 32) if w & 0x80000000 else w


def _reg_name(register: int) -> str:
    return {0x65: "0x65 command", 0x66: "0x66 FIFO", 0x67: "0x67 FIFO control"}.get(
        register, hex(register)
    )


def _fmt_vector(cmd: C.CommandVector) -> str:
    words = ", ".join(
        hex(w & 0xFFFFFFFF) if i == 1 and w & 0x80000000 else str(w)
        for i, w in enumerate(cmd.signed_words)
    )
    vid = f" ({cmd.vector_id})" if cmd.vector_id else ""
    return f"{_reg_name(cmd.register)} <- [{words}]{vid}"


def _vector_json(cmd: C.CommandVector) -> dict[str, Any]:
    return {
        "register": cmd.register,
        "words": list(cmd.signed_words),
        "name": cmd.name,
        "vector_id": cmd.vector_id,
        "unverified": list(cmd.unverified),
    }


def _axis_sample(words: Sequence[int], slot: int, k: int) -> dict[str, Any]:
    """Decode one slot of block 2000 (11 §4.4)."""
    base = AXIS_RO_WORDS * slot
    st = int(words[base + AxisRO.STATUS]) & 0xFFFFFFFF
    return {
        "slot": slot,
        "status_word": f"{st:#010x}",
        "fault_bits": st & 0x3F,
        "homed": bool(st & 0x8000),
        "busy_byte2": (st >> 16) & 0xFF,
        "cmd_type_byte3": (st >> 24) & 0xFF,
        "speed_mm_s": _s32(int(words[base + AxisRO.SPEED])) / float(k or 1000),
        "pulse_position": _s32(int(words[base + AxisRO.PULSE_POSITION])),
    }


def _bits(word: int, width: int) -> list[int]:
    return [i for i in range(width) if (word >> i) & 1]


def _verdict(ok: bool | None) -> str:
    return "INCONCLUSIVE" if ok is None else ("CONFIRMED" if ok else "CONTRADICTED")


def parse_steps(text: str) -> tuple[int, ...]:
    """``"1-3,5,10"`` -> ``(1, 2, 3, 5, 10)`` (sorted, unique, 1..10)."""
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
            out.update(range(lo, hi + 1))
        else:
            out.add(int(part))
    if not out or any(n not in STEP_BY_N for n in out):
        raise ValueError(f"steps must be within 1..10: {text!r}")
    return tuple(sorted(out))


def compact_status(snap: dict[str, Any]) -> dict[str, Any]:
    """The fields of a daemon status snapshot the report keeps (11 §4)."""
    keep = (
        "link",
        "machine_state",
        "arm_state",
        "estop_latched",
        "program_version",
        "di_word",
        "do_word",
        "alarm_1",
        "alarm_2",
        "homed_slots",
        "homing",
        "jogs",
        "fifo_frame_id",
        "fifo_margin",
        "fifo_running",
        "restart_required",
        "k",
        "bus_cycle_us",
        "zf_type",
        "zf_state",
        "machine_fault",
        "watchdog",
        "last_error",
        "poll_age_s",
    )
    out = {k: snap.get(k) for k in keep}
    out["alarm_texts"] = [a.get("text") for a in snap.get("alarms") or ()]
    out["axes"] = [
        {
            "slot": a.get("slot"),
            "status_word": f"{int(a.get('status_word', 0)):#010x}",
            "busy": a.get("busy"),
            "command_type": a.get("command_type"),
            "position_counts": a.get("position_counts"),
            "speed_mm_s": a.get("speed_mm_s"),
        }
        for a in snap.get("axes") or ()
    ]
    return out


# ------------------------------------------------------------------------------------------
# Report
# ------------------------------------------------------------------------------------------


class StepRecord:
    """Everything recorded for one step (serialised into report.json)."""

    def __init__(self, info: StepInfo) -> None:
        self.info = info
        self.data: dict[str, Any] = {
            "n": info.n,
            "title": info.title,
            "doc_action": info.action,
            "settles": info.settles,
            "status": "pending",
            "started": None,
            "finished": None,
            "results": {},
            "hypotheses": [],
            "motions": [],
            "wine_variants": [],
            "observations": [],
            "deferred": [],
            "notes": [],
            "events": [],
        }
        self.incomplete = False

    def event(self, kind: str, **data: Any) -> dict[str, Any]:
        ev = {**_stamp(), "kind": kind, **data}
        self.data["events"].append(ev)
        return ev

    def result(self, key: str, value: Any) -> None:
        self.data["results"][key] = value

    def hypothesis(self, hid: str, claim: str, verdict: str, evidence: str) -> None:
        self.data["hypotheses"].append(
            {"id": hid, "claim": claim, "verdict": verdict, "evidence": evidence}
        )

    def note(self, text: str) -> None:
        self.data["notes"].append(text)

    def defer(self, what: str, why: str) -> None:
        self.data["deferred"].append({"what": what, "why": why})
        self.incomplete = True


_MD_VALUE_MAX = 600
"""Longest JSON value shown inline in report.md (report.json keeps everything)."""


def render_markdown(report: dict[str, Any]) -> str:
    """Human-readable report.md from the report dict."""
    L: list[str] = []
    L.append(f"# M1 bench session report ({report.get('date')})")
    L.append("")
    L.append("* tool: `tools/m1_session.py`, runbook `docs/M1-BENCH-SESSION.md`, 11 §7")
    for key in ("started", "finished", "outcome", "socket", "serve_command", "pcap"):
        L.append(f"* {key}: {report.get(key)}")
    ping = report.get("daemon") or {}
    L.append(f"* daemon: version {ping.get('version')}, protocol {ping.get('protocol')}")
    L.append("")
    L.append("## Safety checklist")
    L.append("")
    for item in report.get("checklist") or ():
        mark = "x" if item.get("confirmed") else " "
        L.append(f"- [{mark}] {item.get('text')} ({item.get('t')})")
    if report.get("abort_reason"):
        L.append("")
        L.append(f"**Aborted:** {report['abort_reason']}")
    for step in report.get("steps") or ():
        L.append("")
        L.append(f"## Step {step['n']}: {step['title']} [{step['status']}]")
        L.append("")
        L.append(f"* 11 §7 action: {step['doc_action']}")
        L.append(f"* settles: {step['settles']}")
        L.append(f"* started {step.get('started')}, finished {step.get('finished')}")
        if step.get("hypotheses"):
            L.append("")
            L.append("| hypothesis | verdict | evidence |")
            L.append("|---|---|---|")
            for h in step["hypotheses"]:
                ev = str(h["evidence"]).replace("|", "\\|")
                L.append(f"| {h['id']}: {h['claim']} | **{h['verdict']}** | {ev} |")
        if step.get("results"):
            L.append("")
            L.append("Automatic results:")
            L.append("")
            for k, v in step["results"].items():
                text = json.dumps(v)
                if len(text) > _MD_VALUE_MAX:
                    text = text[:_MD_VALUE_MAX] + " ... (full value in report.json)"
                L.append(f"* `{k}`: `{text}`")
        for m in step.get("motions") or ():
            L.append("")
            L.append(f"Motion **{m['title']}**: {m.get('outcome')}")
            for p in m.get("planned") or ():
                L.append(f"* planned `{p['register']:#x} <- {p['words']}` {p.get('vector_id', '')}")
            if m.get("sent_words") is not None:
                L.append(
                    f"* daemon sent `{m['sent_words']}` (matches plan: {m.get('matches_plan')})"
                )
        for d in step.get("deferred") or ():
            L.append("")
            L.append(f"Deferred: {d['what']}: {d['why']}")
        for w in step.get("wine_variants") or ():
            L.append("")
            L.append(
                f"Wine / vendor variant **{w['title']}**: {'run' if w.get('run') else 'not run'}"
            )
            for mk in w.get("marks") or ():
                L.append(f"* {mk['t']}: {mk['text']}")
        if step.get("observations"):
            L.append("")
            L.append("Operator observations:")
            L.append("")
            for o in step["observations"]:
                L.append(f"* {o['question']} -> {o['answer'] or '(no answer)'}")
        for n in step.get("notes") or ():
            L.append(f"* note: {n}")
        traffic = (report.get("capture") or {}).get("steps", {}).get(str(step["n"]))
        if traffic:
            L.append("")
            L.append(
                f"Captured traffic in this step's window ({traffic['transactions']} transactions):"
            )
            L.append("")
            reads = ", ".join(f"{k} x{v}" for k, v in traffic["by_request"].items())
            L.append(f"* requests: {reads}")
            for desc, w in traffic["writes"].items():
                st = ", ".join(f"{k} x{v}" for k, v in w["status"].items())
                L.append(f"* write `{desc}` x{w['count']} (first {w['first']}; {st})")
            if traffic.get("fifo_frames"):
                L.append(
                    f"* FIFO frames {traffic['fifo_frames']}, census {traffic['fifo_opcode_census']}"
                )
            if traffic.get("tick_period_estimate"):
                L.append(f"* tick period estimate: `{json.dumps(traffic['tick_period_estimate'])}`")
    cap = report.get("capture") or {}
    if cap:
        L.append("")
        L.append("## Capture")
        L.append("")
        for k in ("pcap", "size", "sha256", "error"):
            if cap.get(k) is not None:
                L.append(f"* {k}: {cap[k]}")
        if cap.get("dissector_summary"):
            L.append("")
            L.append(f"`{cap.get('dissector_command')}` (exit {cap.get('dissector_returncode')}):")
            L.append("")
            L.append("```")
            L.append(cap["dissector_summary"].rstrip())
            L.append("```")
            L.append("")
            L.append(
                "Note: request labels follow 11 §8 - 0x65 sub-command 1 = STOP, 2 = HOME, "
                "3 = relative jog (bit 31 = absolute), 5 = go-to. A summary produced by a "
                "build older than 2026-09-16 labelled 1 'home' and 3 'move-axis' (04 naming); "
                "re-run the dissector rather than trusting such a file."
            )
    if report.get("vendor_logs"):
        L.append("")
        L.append("## Vendor logs copied")
        L.append("")
        for p in report["vendor_logs"]:
            L.append(f"* {p}")
    L.append("")
    return "\n".join(L)


# ------------------------------------------------------------------------------------------
# Session
# ------------------------------------------------------------------------------------------


@dataclass(slots=True)
class Options:
    """Command-line options (see ``--help``)."""

    socket: Path = field(default_factory=default_socket_path)
    out_dir: Path | None = None
    pcap: Path | None = None
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    steps: tuple[int, ...] = tuple(range(1, 11))
    step_mm: float = 5.0
    step_speed: float = 20.0
    cont_speed: float = 20.0
    cont_hold_s: float = 1.0
    motion_timeout_s: float = 30.0
    home_timeout_s: float = 180.0
    connect_timeout_s: float = 30.0
    power_cycle_timeout_s: float = 300.0
    estop_wait_s: float = 5.0
    switches: tuple[str, ...] = ("X-", "X+", "Y-", "Y+", "Y2-", "Y2+")
    vendor_log_dir: Path | None = VENDOR_LOG_DIR_DEFAULT
    run_dissector: bool = True
    sample_period_s: float = 0.05

    def resolved_out_dir(self) -> Path:
        return self.out_dir or Path.home() / "mlaser-captures" / f"m1-{self.date}"

    def resolved_pcap(self) -> Path:
        return self.pcap or Path.home() / "mlaser-captures" / f"m1-{self.date}.pcap"


class M1Session:
    """The guided session (module docstring). ``run()`` returns the exit status."""

    def __init__(self, options: Options, operator: Operator) -> None:
        self.opts = options
        self.op = operator
        self.link = DaemonLink(options.socket)
        self.out_dir = options.resolved_out_dir()
        self.t0 = time.time()
        self.report: dict[str, Any] = {
            "tool": "tools/m1_session.py",
            "doc": "docs/analysis/11-static-findings.md §7, docs/M1-BENCH-SESSION.md",
            "date": options.date,
            "started": _stamp(self.t0)["t"],
            "started_epoch": self.t0,
            "finished": None,
            "outcome": "running",
            "socket": str(options.socket),
            "options": {
                k: (str(v) if isinstance(v, Path) else v)
                for k, v in (
                    ("steps", list(options.steps)),
                    ("step_mm", options.step_mm),
                    ("step_speed", options.step_speed),
                    ("cont_speed", options.cont_speed),
                    ("cont_hold_s", options.cont_hold_s),
                    ("switches", list(options.switches)),
                )
            },
            "daemon": None,
            "serve_command": None,
            "capture_running": None,
            "checklist": [],
            "steps": [],
            "pcap": str(options.resolved_pcap()),
            "capture": None,
            "vendor_logs": [],
            "abort_reason": None,
        }
        self._armed = False
        self._k = EXPECT_K
        self._current: StepRecord | None = None

    # -- persistence ---------------------------------------------------------------------------

    def _prepare_out_dir(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        old = self.out_dir / "report.json"
        if old.exists():  # never overwrite evidence of an earlier run
            tag = datetime.fromtimestamp(old.stat().st_mtime).strftime("%H%M%S")
            for name in ("report.json", "report.md"):
                p = self.out_dir / name
                if p.exists():
                    p.rename(self.out_dir / f"{p.stem}-{tag}{p.suffix}")

    def save(self) -> None:
        """Write report.json and report.md atomically."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for name, text in (
            ("report.json", json.dumps(self.report, indent=2, default=str)),
            ("report.md", render_markdown(self.report)),
        ):
            tmp = self.out_dir / f".{name}.tmp"
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.out_dir / name)

    # -- prompts ---------------------------------------------------------------------------------

    def _observe(self, rec: StepRecord, questions: Sequence[tuple[str, str]]) -> None:
        if not questions:
            return
        self.op.say("Write down what you saw (Enter = no answer):")
        for key, q in questions:
            ans = self.op.ask(f"s{rec.info.n}.obs.{key}", f"  {q}\n  >")
            rec.data["observations"].append({**_stamp(), "key": key, "question": q, "answer": ans})

    # -- daemon helpers --------------------------------------------------------------------------

    def _status(self, rec: StepRecord | None, label: str) -> dict[str, Any]:
        snap = self.link.call("status")
        self._k = int(snap.get("k") or EXPECT_K)
        if rec is not None:
            rec.event("status", label=label, status=compact_status(snap))
        return snap

    def _read(
        self, rec: StepRecord, addr: int, n: int, label: str, *, record: bool = True
    ) -> list[int] | None:
        try:
            res = self.link.call("read_block", addr=addr, n=n)
        except IpcError as exc:
            rec.event(
                "read", label=label, addr=addr, n=n, ok=False, error=f"{exc.code}: {exc.message}"
            )
            return None
        words = [int(w) & 0xFFFFFFFF for w in res["words"]]
        if record:
            rec.event("read", label=label, addr=addr, n=n, ok=True, words=words)
        return words

    def _marker(self, rec: StepRecord, what: str) -> None:
        try:
            self.link.call("read_block", addr=MARKER_READ[0], n=MARKER_READ[1])
            rec.event("marker", what=what, read=f"READ {MARKER_READ[0]}/{MARKER_READ[1]}", ok=True)
        except (IpcError, DaemonUnavailable) as exc:
            rec.event("marker", what=what, ok=False, error=str(exc))

    def _arm(self, rec: StepRecord) -> None:
        res = self.link.call("arm_motion")
        self._armed = True
        rec.event("arm_motion", reply=res)

    def _disarm(self, rec: StepRecord | None, why: str) -> None:
        if not self._armed:
            return
        try:
            res = self.link.call("disarm")
            self._armed = False
            if rec is not None:
                rec.event("disarm", why=why, reply=res)
        except (IpcError, DaemonUnavailable) as exc:
            if rec is not None:
                rec.event("disarm", why=why, ok=False, error=str(exc))

    def _emergency_stop(self, why: str) -> None:
        """Ctrl-C / abort: stop, then disarm (the daemon also stops on client disconnect)."""
        rec = self._current
        for cmd in ("stop", "disarm"):
            try:
                res = self.link.call(cmd)
                if rec is not None:
                    rec.event(cmd, why=why, reply=res)
            except (IpcError, DaemonUnavailable) as exc:
                if rec is not None:
                    rec.event(cmd, why=why, ok=False, error=str(exc))
        self._armed = False

    def _wait_connected(self, rec: StepRecord | None, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            try:
                snap = self.link.call("status")
                if snap.get("link") == "CONNECTED" and snap.get("machine_state") != "UNKNOWN":
                    self._k = int(snap.get("k") or EXPECT_K)
                    return True
            except DaemonUnavailable:
                pass
            if time.monotonic() >= deadline:
                if rec is not None:
                    rec.event("wait_connected", ok=False, timeout_s=timeout)
                return False
            time.sleep(0.2)

    def _params(self, speed: float) -> C.MachineParams:
        """The daemon's jog parameters (``MccDaemon._jog_params``) for the planned vector."""
        return replace(
            C.MachineParams(k=self._k),
            jog_fast_speed=speed,
            jog_slow_speed=speed,
            is_fast_mode=True,
        )

    # -- motion ------------------------------------------------------------------------------------

    def _motion(
        self,
        rec: StepRecord,
        key: str,
        title: str,
        planned: Sequence[C.CommandVector],
        physical: str,
        action: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any] | None:
        """Print the plan, ask y/N, arm, run ``action`` (PORT-PLAN §8.2)."""
        lines = ["", f">>> MOTION: {title}", "    Planned vector(s) the daemon will send:"]
        lines += [f"      {_fmt_vector(c)}" for c in planned]
        lines.append(f"    Expected: {physical}")
        lines.append("    Hand on the E-stop. Anything but 'y' skips this motion.")
        self.op.say("\n".join(lines))
        entry: dict[str, Any] = {
            **_stamp(),
            "key": key,
            "title": title,
            "planned": [_vector_json(c) for c in planned],
            "expected": physical,
            "confirmed": False,
            "outcome": None,
            "sent_words": None,
            "matches_plan": None,
        }
        rec.data["motions"].append(entry)
        if not self.op.confirm(f"s{rec.info.n}.motion.{key}", "    Send this motion?"):
            entry["outcome"] = "declined by the operator (nothing sent)"
            rec.incomplete = True
            self.op.say("    Skipped.")
            return None
        entry["confirmed"] = True
        entry["confirmed_at"] = _stamp()["t"]
        try:
            self._arm(rec)
            action(entry)
        except IpcError as exc:
            entry["outcome"] = (
                entry.get("outcome") or f"refused by the daemon: {exc.code}: {exc.message}"
            )
            entry["error"] = {"code": exc.code, "message": exc.message}
            rec.incomplete = True
            self.op.say(f"    Daemon refused: {exc.code}: {exc.message}")
            return entry
        except KeyboardInterrupt:
            entry["outcome"] = "interrupted by Ctrl-C (stop sent)"
            raise
        if entry["outcome"] is None:
            entry["outcome"] = "sent"
        self.op.say(f"    Outcome: {entry['outcome']}")
        return entry

    def _compare(self, entry: dict[str, Any], words: Sequence[int]) -> None:
        entry["sent_words"] = list(words)
        planned = entry["planned"][0]["words"] if entry["planned"] else None
        entry["matches_plan"] = planned is not None and list(words) == list(planned)

    def _fresh(self, snap: dict[str, Any], after_epoch: float) -> bool:
        age = snap.get("axis_ro_age_s")
        return age is not None and float(snap.get("t", 0.0)) - float(age) > after_epoch

    def _wait_idle(
        self,
        rec: StepRecord,
        slots: Sequence[int],
        after_epoch: float,
        timeout: float,
        *,
        sample_slots: Sequence[int] = (),
        until: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """Sample block 2000 until the axes are idle on a status read after ``after_epoch``."""
        deadline = time.monotonic() + timeout
        samples: list[dict[str, Any]] = []
        t_start = time.time()
        outcome = "timeout"
        while True:
            if sample_slots:
                words = self._read(rec, 2000, 50, "sample", record=False)
                if words is not None:
                    now = time.time()
                    samples.append(
                        {
                            "dt_s": round(now - t_start, 4),
                            "axes": [_axis_sample(words, s, self._k) for s in sample_slots],
                        }
                    )
            snap = self.link.call("status")
            if self._fresh(snap, after_epoch):
                if snap.get("estop_latched"):
                    outcome = "aborted: E-stop latched"
                    break
                if self._armed and snap.get("arm_state") == "DISARMED":
                    outcome = f"aborted: daemon disarmed ({snap.get('machine_fault') or snap.get('watchdog')})"
                    break
                axes = {int(a["slot"]): a for a in snap.get("axes") or ()}
                idle = snap.get("machine_state") == "READY" and all(
                    s in axes and not axes[s].get("busy") for s in slots
                )
                if idle and (until is None or until(snap)):
                    outcome = "completed"
                    break
            if time.monotonic() >= deadline:
                outcome = f"timeout after {timeout:g} s: stop sent"
                with contextlib.suppress(IpcError, DaemonUnavailable):
                    rec.event("stop", why="motion timeout", reply=self.link.call("stop"))
                break
            time.sleep(self.opts.sample_period_s)
        return {
            "outcome": outcome,
            "duration_s": round(time.time() - t_start, 3),
            "samples": samples,
        }

    def _step_jog(
        self,
        rec: StepRecord,
        entry: dict[str, Any],
        slot: int,
        mm: float,
        speed: float,
        *,
        sample_slots: Sequence[int],
        while_moving: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        t_sent = time.time()
        res = self.link.call("jog_step", slot=slot, mm=mm, speed=speed)
        self._compare(entry, res.get("words") or [])
        entry["deadman_lease"] = bool(res.get("deadman"))
        rec.event("jog_step", slot=slot, mm=mm, speed=speed, reply=res)
        refresher = None
        if res.get("deadman"):
            # |d| > v x 200 ms: the gate leased the step; refresh for its expected duration only.
            until = time.monotonic() + abs(mm) / speed * 1.25 + 0.3
            refresher = _Refresher(self.opts.socket, slot, until)
            refresher.start()
        try:
            if while_moving is not None:
                while_moving()
            wait = self._wait_idle(
                rec, [slot], t_sent, self.opts.motion_timeout_s, sample_slots=sample_slots
            )
        finally:
            if refresher is not None:
                refresher.stop()
                entry["deadman_refresh"] = {
                    "calls": refresher.calls,
                    "ended_by": refresher.ended_by,
                }
        entry["outcome"] = wait["outcome"]
        entry["duration_s"] = wait["duration_s"]
        return wait

    # -- Wine / vendor variant -------------------------------------------------------------------

    def _probe_daemon(self) -> str | None:
        """Link state if the daemon answers on a fresh connection, else None."""
        try:
            with MccdClient(self.opts.socket, timeout=2.0) as c:
                return str(c.call("status").get("link"))
        except Exception:
            return None

    def _wine_variant(
        self,
        rec: StepRecord,
        key: str,
        title: str,
        instructions: Sequence[str],
        marks: Sequence[tuple[str, str]],
        questions: Sequence[tuple[str, str]],
    ) -> bool:
        """Vendor Mlaser under Wine while tcpdump runs (docs/analysis/10, PORT-PLAN §6 step 1)."""
        n = rec.info.n
        entry: dict[str, Any] = {**_stamp(), "key": key, "title": title, "run": False, "marks": []}
        rec.data["wine_variants"].append(entry)
        self.op.say(
            f"\n=== Wine / vendor variant: {title} ===\n"
            "    Uses Mlaser under Wine (see docs/M1-BENCH-SESSION.md, 'Vendor tool under Wine')."
        )
        if not self.op.confirm(f"s{n}.wine.{key}.run", "    Run this vendor variant now?"):
            entry["outcome"] = "not run (operator)"
            return False
        self._disarm(rec, "before handing the card to Mlaser")
        if not self.op.confirm(
            f"s{n}.wine.{key}.mccd_stopped",
            "    Stop nexcut-mccd first (Ctrl-C in its terminal): Mlaser and nexcut-mccd must "
            "never talk to the card at the same time. Is nexcut-mccd stopped?",
        ):
            entry["outcome"] = "not run: nexcut-mccd not stopped"
            return False
        self.link.close()
        state = self._probe_daemon()
        entry["daemon_link_when_handed_over"] = state
        if state == "CONNECTED" and not self.op.confirm(
            f"s{n}.wine.{key}.daemon_still_connected",
            "    WARNING: nexcut-mccd still answers and is CONNECTED to a card. Continue ONLY "
            "if it runs on the simulator (--sim), never with the real card. Continue?",
        ):
            entry["outcome"] = "not run: nexcut-mccd still connected"
            return False
        entry["run"] = True
        self.op.say("\n".join(f"    {line}" for line in instructions))
        for mkey, text in marks:
            self.op.wait(f"s{n}.wine.{key}.{mkey}", f"    {text}")
            entry["marks"].append({**_stamp(), "mark": mkey, "text": text})
        self._observe(rec, questions)
        self.op.wait(
            f"s{n}.wine.{key}.restart",
            "    Close Mlaser completely, restart 'nexcut-mccd serve --card-ip 10.1.1.168' "
            "and press Enter",
        )
        if not self._wait_connected(rec, self.opts.connect_timeout_s):
            self.op.say("    nexcut-mccd is not connected to the card.")
            if not self.op.confirm(
                f"s{n}.wine.{key}.continue_without_daemon",
                "    Continue the session anyway (later steps need the daemon)?",
            ):
                raise SessionAborted("daemon not back after the vendor variant")
        entry["outcome"] = "run"
        return True

    # -- session flow ----------------------------------------------------------------------------

    def run(self) -> int:
        """Checklist, steps, capture analysis. Always writes the report."""
        self._prepare_out_dir()
        code = EXIT_OK
        try:
            if not self._preflight():
                code = EXIT_UNREACHABLE
            elif not self._checklist():
                code = EXIT_ABORTED
            else:
                for n in self.opts.steps:
                    self._run_step(STEP_BY_N[n])
                    self.save()
                self._finish()
                self.report["outcome"] = "completed"
        except KeyboardInterrupt:
            self.op.say("\nInterrupted: sending stop + disarm.")
            self._emergency_stop("Ctrl-C")
            self._close_step("interrupted")
            self.report["outcome"] = "interrupted"
            self.report["abort_reason"] = "Ctrl-C"
            code = EXIT_INTERRUPTED
        except SessionAborted as exc:
            self._emergency_stop(f"aborted: {exc}")
            self._close_step("aborted")
            self.report["outcome"] = "aborted"
            self.report["abort_reason"] = str(exc)
            code = EXIT_ABORTED
        finally:
            with contextlib.suppress(Exception):
                self._disarm(self._current, "session end")
            self.report["finished"] = _stamp()["t"]
            self.save()
            self.link.close()
        self.op.say(f"\nReport: {self.out_dir / 'report.md'} (and report.json)")
        return code

    def _preflight(self) -> bool:
        self.op.say(
            "M1 confirmation session (11-static-findings §7). Runbook: docs/M1-BENCH-SESSION.md\n"
            f"Daemon socket: {self.opts.socket}\nReport directory: {self.out_dir}"
        )
        try:
            self.report["daemon"] = self.link.call("ping")
        except DaemonUnavailable as exc:
            self.op.say(
                f"nexcut-mccd is not reachable ({exc}).\n"
                "Start it first: nexcut-mccd serve --card-ip 10.1.1.168   (or --sim to rehearse)"
            )
            self.report["outcome"] = "daemon unreachable"
            self.report["abort_reason"] = str(exc)
            return False
        return True

    def _checklist(self) -> bool:
        self.op.say("\nSAFETY CHECKLIST: every item must be answered 'y'.")
        for key, text in SAFETY_CHECKLIST:
            ok = self.op.confirm(f"checklist.{key}", f"  - {text}\n    Confirmed?")
            self.report["checklist"].append({**_stamp(), "key": key, "text": text, "confirmed": ok})
            if not ok:
                self.op.say("Refused: the session does not start until every item is confirmed.")
                self.report["outcome"] = "refused: safety checklist"
                self.report["abort_reason"] = f"checklist item not confirmed: {key}"
                return False
        self.report["serve_command"] = self.op.ask(
            "setup.serve_command",
            "Paste the nexcut-mccd command line you started (e.g. 'nexcut-mccd serve --card-ip "
            "10.1.1.168'):",
        )
        self.report["capture_running"] = self.op.confirm(
            "setup.capture_running", f"Is tcpdump capturing to {self.opts.resolved_pcap()}?"
        )
        if not self._wait_connected(None, self.opts.connect_timeout_s):
            self.op.say("nexcut-mccd is running but not CONNECTED to a card.")
            if not self.op.confirm("setup.continue_unconnected", "Continue anyway?"):
                raise SessionAborted("daemon not connected to the card")
        return True

    def _close_step(self, status: str) -> None:
        rec = self._current
        if rec is not None and rec.data["finished"] is None:
            rec.data["status"] = status
            rec.data["finished"] = _stamp()["t"]
            rec.data["finished_epoch"] = time.time()

    def _run_step(self, info: StepInfo) -> None:
        rec = StepRecord(info)
        self._current = rec
        self.report["steps"].append(rec.data)
        self.op.say(
            f"\n==================== Step {info.n}/10: {info.title} ====================\n"
            f"11 §7 action : {info.action}\nSettles      : {info.settles}\n"
            f"Runbook      : docs/M1-BENCH-SESSION.md, 'Step {info.n}'"
        )
        if not self.op.confirm(f"s{info.n}.start", "Start this step? (N = skip it)"):
            rec.data["status"] = "skipped"
            rec.data["started"] = rec.data["finished"] = _stamp()["t"]
            return
        st = _stamp()
        rec.data["started"], rec.data["started_epoch"] = st["t"], st["epoch"]
        try:
            self._marker(rec, "step start")
            with contextlib.suppress(IpcError):
                self._status(rec, "step start")
            getattr(self, f"_step{info.n}")(rec)
            status = "partial" if rec.incomplete else "done"
        except DaemonUnavailable as exc:
            rec.event("error", error=f"daemon unavailable: {exc}")
            self.op.say(f"Daemon unavailable: {exc}. Step recorded as failed.")
            status = "failed"
        except IpcError as exc:
            rec.event("error", error=f"{exc.code}: {exc.message}")
            self.op.say(f"Daemon error: {exc.code}: {exc.message}. Step recorded as failed.")
            status = "failed"
        self._disarm(rec, "step end")
        with contextlib.suppress(IpcError, DaemonUnavailable):
            self._status(rec, "step end")
            self._marker(rec, "step end")
        self._close_step(status)
        self.op.say(f"Step {info.n}: {status}")

    # -- the ten steps (11 §7) -------------------------------------------------------------------

    def _step1(self, rec: StepRecord) -> None:
        """11 §7 step 1: start-up reads; the daemon did them and the connect write at connect."""
        rec.note(
            "nexcut-mccd performed the start-up sequence READ 1000/2, 1000/36, 50000/26, "
            "50200/100 and the connect write [9999,5,0,0] when it connected (11 §2 V0, N4); "
            "the tool repeats the four reads so they appear in this step's capture window."
        )
        ver = self._read(rec, 1000, 2, "1000/2 version")
        st = self._read(rec, 1000, 36, "1000/36 status")
        sysrw = self._read(rec, 50000, 26, "50000/26 system RW")
        axrw = self._read(rec, 50200, 100, "50200/100 axis RW")
        if ver is not None:
            rec.result("program_id", ver[0])
            rec.result("program_version", ver[1])
            rec.hypothesis(
                "version",
                f"reg 1001 >= MinHardwareVer {MIN_HARDWARE_VER} (11 §4.1)",
                _verdict(ver[1] >= MIN_HARDWARE_VER),
                f"reg 1001 = {ver[1]}",
            )
        if st is not None:
            rec.result("di_word", f"{st[Status.DI]:#08x}")
            rec.result("do_word", f"{st[Status.DO]:#06x}")
            rec.result("alarm_1", f"{st[Status.ALARM_1]:#010x}")
            rec.result("alarm_2", f"{st[Status.ALARM_2]:#010x}")
            rec.result("run_status_1008", st[Status.RUN_STATUS])
            rec.result("fifo_frame_id_1015", st[Status.FIFO_FRAME_ID])
            rec.result("fifo_margin_1016", st[Status.FIFO_SPACE_MARGIN])
            rec.result("fifo_interp_config_1017", st[Status.FIFO_INTERP_CONFIG])
            rec.result("param_status_1031", st[Status.PARAM_STATUS])
        if sysrw is not None:
            k, cyc, zf = sysrw[SystemRW.K], sysrw[SystemRW.BUS_CYCLE], sysrw[SystemRW.ZF_TYPE]
            rec.result("system_rw", sysrw)
            rec.hypothesis(
                "K",
                "reg 50017 (word 17) K == 1000 (A1 §2)",
                _verdict(k == EXPECT_K),
                f"word 17 = {k}",
            )
            rec.hypothesis(
                "bus_cycle",
                "reg 50005 (word 5) bus cycle == 250 µs (11 §5.3)",
                _verdict(cyc == EXPECT_BUS_CYCLE_US),
                f"word 5 = {cyc}",
            )
            rec.hypothesis(
                "ZFType",
                "reg 50006 (word 6) ZFType == 1 (11 §2)",
                _verdict(zf == EXPECT_ZF_TYPE),
                f"word 6 = {zf}",
            )
        if axrw is not None:
            for slot, (ppr, lead_mm) in XML_PULSES_PER_MM.items():
                base = AXIS_RW_WORDS_ON_WIRE * slot
                lead, pulses = axrw[base + AxisRW.LEAD], axrw[base + AxisRW.CMD_PULSES_PER_UNIT]
                xml = ppr / lead_mm
                cands = {
                    f"lead/{scale}": round(pulses / (lead / scale), 4)
                    for scale in (1, 1000)
                    if lead
                }
                match = [c for c, v in cands.items() if abs(v - xml) / xml < 0.001]
                rec.result(
                    f"axis{slot}_lead_pulses",
                    {
                        "word9_lead": lead,
                        "word10_pulses": pulses,
                        "pulses_per_mm_candidates": cands,
                        "xml_pulses_per_mm": round(xml, 4),
                    },
                )
                rec.hypothesis(
                    f"axis{slot}_scale",
                    f"card axis-{slot} words 9/10 give the XML {ppr}/{lead_mm} = {xml:.2f} p/mm (C23)",
                    _verdict(bool(match)) if lead and pulses else "INCONCLUSIVE",
                    f"word9={lead} word10={pulses} candidates={cands}",
                )
        self._observe(
            rec,
            [
                ("panel", "Anything shown on the machine / card LEDs / alarm lamp?"),
                (
                    "serve_log",
                    "Did nexcut-mccd print 'card connected, version ...'? Copy the line:",
                ),
            ],
        )

    def _wait_alarm(self, rec: StepRecord, want_on: bool, label: str) -> bool | None:
        deadline = time.monotonic() + self.opts.estop_wait_s
        while True:
            snap = self._status(None, label)
            on = bool(int(snap.get("alarm_1") or 0) & ESTOP_BIT)
            if on == want_on:
                rec.event("status", label=label, status=compact_status(snap))
                return on
            if time.monotonic() >= deadline:
                rec.event("status", label=f"{label} (timeout)", status=compact_status(snap))
                return on
            time.sleep(0.1)

    def _ack_estop(self, rec: StepRecord) -> None:
        snap = self._status(rec, "before E-stop acknowledge")
        if not snap.get("estop_latched"):
            return
        if self.op.confirm(
            f"s{rec.info.n}.ack_estop",
            "The daemon latched the E-stop. Acknowledge the latch now (no motion)?",
        ):
            try:
                rec.event("ack_estop", reply=self.link.call("ack_estop"))
            except IpcError as exc:
                rec.event("ack_estop", ok=False, error=f"{exc.code}: {exc.message}")
                self.op.say(f"Acknowledge refused: {exc.message}")
                rec.incomplete = True
        else:
            rec.note("E-stop latch left set by the operator: later motions will be refused")
            rec.incomplete = True

    def _step2(self, rec: StepRecord) -> None:
        """11 §7 step 2 / N3: block 5000 idle, E-stop pressed, released; 1006 each time."""
        states: dict[str, dict[str, Any]] = {}

        def capture(label: str) -> None:
            b5000 = self._read(rec, 5000, 9, f"5000/9 {label}")
            st = self._read(rec, 1000, 36, f"1000/36 {label}")
            states[label] = {
                "block5000": b5000,
                "alarm_1": None if st is None else st[Status.ALARM_1],
                "di_word": None if st is None else st[Status.DI],
            }

        capture("idle")
        self.op.wait("s2.estop.press", "Press the hardware E-stop now, then press Enter.")
        seen = self._wait_alarm(rec, True, "E-stop pressed")
        capture("estop_pressed")
        self.op.wait("s2.estop.release", "Release (twist out) the E-stop, then press Enter.")
        cleared = self._wait_alarm(rec, False, "E-stop released")
        capture("released")
        rec.result("states", states)
        idle, pressed = states["idle"], states["estop_pressed"]
        if idle["block5000"] is not None and pressed["block5000"] is not None:
            diff = {
                str(5000 + i): [a, b]
                for i, (a, b) in enumerate(
                    zip(idle["block5000"], pressed["block5000"], strict=True)
                )
                if a != b
            }
            rec.result("block5000_changed_words_idle_vs_pressed", diff)
        else:
            diff = None
        rec.hypothesis(
            "N3",
            "block 5000/9 is readable and reflects the e-stop port (A2 §4)",
            "INCONCLUSIVE" if diff is None else ("CONFIRMED" if diff else "CONTRADICTED"),
            f"changed words idle->pressed: {diff}",
        )
        rec.hypothesis(
            "estop_bit30",
            "alarm_1 (1006) bit 30 set while the E-stop is pressed, clear after release (11 §4.2)",
            _verdict(bool(seen) and cleared is False) if seen is not None else "INCONCLUSIVE",
            f"pressed: {pressed['alarm_1']}, released: {states['released']['alarm_1']}",
        )
        if idle["di_word"] is not None and pressed["di_word"] is not None:
            changed = _bits(idle["di_word"] ^ pressed["di_word"], 24)
            rec.result(
                "di_ports_changed_by_estop",
                [
                    {
                        "port": b + 1,
                        "idle": (idle["di_word"] >> b) & 1,
                        "pressed": (pressed["di_word"] >> b) & 1,
                    }
                    for b in changed
                ],
            )
        self._ack_estop(rec)
        self._observe(
            rec,
            [
                ("drives", "Did the servo drives lose enable when pressed (LEDs, brake click)?"),
                ("lamp", "Alarm lamp / buzzer / panel message while pressed?"),
            ],
        )

    def _step3(self, rec: StepRecord) -> None:
        """11 §7 step 3: X +5 mm at 20 mm/s, poll 2000, repeat while moving (O6, N5)."""
        mm, speed = self.opts.step_mm, self.opts.step_speed
        planned = C.jog_step(0, mm, self._params(speed))
        before = self._read(rec, 2000, 50, "2000/50 before")
        again = self.op.confirm(
            "s3.plan.repeat_while_moving",
            "Also send the same jog a second time while the first is still moving (jog-while-"
            "moving, N5)? The daemon is expected to refuse it PC-side.",
        )
        second: dict[str, Any] = {}

        def while_moving() -> None:
            if not again:
                return
            try:
                res = self.link.call("jog_step", slot=0, mm=mm, speed=speed)
                second.update({**_stamp(), "sent": True, "reply": res})
            except IpcError as exc:
                second.update({**_stamp(), "sent": False, "error": f"{exc.code}: {exc.message}"})
            rec.event("jog_while_moving", **second)

        wait: dict[str, Any] = {}

        def action(entry: dict[str, Any]) -> None:
            wait.update(
                self._step_jog(
                    rec, entry, 0, mm, speed, sample_slots=(0,), while_moving=while_moving
                )
            )

        self._motion(
            rec,
            "jog_x",
            f"Jog X {mm:+g} mm at {speed:g} mm/s"
            + (" (+ a second jog while moving)" if again else ""),
            [planned] * (2 if again else 1),
            f"X moves {mm:+g} mm slowly{' (up to twice)' if again else ''}, then stops",
            action,
        )
        after = self._read(rec, 2000, 50, "2000/50 after")
        if wait:
            rec.result("samples_2000", wait["samples"])
            rec.result(
                "byte2_values",
                sorted({a["busy_byte2"] for s in wait["samples"] for a in s["axes"]}),
            )
            rec.result(
                "byte3_values",
                sorted({a["cmd_type_byte3"] for s in wait["samples"] for a in s["axes"]}),
            )
        if before is not None and after is not None:
            d = (
                _axis_sample(after, 0, self._k)["pulse_position"]
                - _axis_sample(before, 0, self._k)["pulse_position"]
            )
            rec.result("x_pulse_position_delta", d)
            busy_seen = any(a["busy_byte2"] for s in wait.get("samples", ()) for a in s["axes"])
            moved = d != 0 or busy_seen
            rec.hypothesis(
                "O6_firmware_gating",
                "the card executes a jog without the licence exchange (moves => no firmware gate)",
                "CONFIRMED" if moved else ("INCONCLUSIVE" if not wait else "CONTRADICTED"),
                f"pulse position delta {d}, busy seen {busy_seen}, motion outcome {wait.get('outcome')}",
            )
        if again:
            rec.result("jog_while_moving", second)
            rec.hypothesis(
                "N5_exception3",
                "a jog re-sent while moving is answered with exception 3 by the card (08 §4.5)",
                "INCONCLUSIVE",
                "the daemon mirrors the card gate PC-side (A1 §1) and "
                + ("refused it before the wire" if not second.get("sent") else "sent it")
                + "; the card answer is only visible in the Wine variant capture",
            )
        self._observe(
            rec,
            [
                ("moved", "Did X move? Measured distance (mm) and direction (towards which side)?"),
                ("sound", "Any unusual noise, jerk or vibration?"),
            ],
        )
        self._wine_variant(
            rec,
            "jog_ab",
            "vendor step jog X for A/B and exception 3",
            [
                "In Mlaser set jog step length 5 mm and slow speed (JogSlowSpeed 50), step mode on.",
                "Click X+ once, wait until it stops, then click X+ twice in quick succession.",
            ],
            [("done", "Press Enter when the three clicks are done and X is stopped.")],
            [("wine_moved", "Total X travel seen (mm)? Did the quick second click move it?")],
        )

    def _step4(self, rec: StepRecord) -> None:
        """11 §7 step 4: bit 31 = absolute (A1 §3.3). No IPC command for it in this build."""
        planned = C.move_axis_absolute(0, 100.0, self._params(self.opts.step_speed))
        rec.result("planned_vector", _vector_json(planned))
        rec.defer(
            "native [3, 0x80000000, v, a, 10a, 100000] twice",
            "nexcut-mccd has no absolute single-axis move command in its IPC vocabulary (the gate "
            "classifies it MOTION_HOMED with soft limits, 11 §3.1); needs a daemon command",
        )
        rec.hypothesis(
            "bit31_absolute",
            "bit 31 on the axis word = absolute target (A1 §3.3, INFERENCE medium-high)",
            "NOT TESTED natively",
            "see the vendor go-to variant: its [5, 0x80000003, ...] shows bit 31 on sub-command 5",
        )
        self.op.say(
            f"Native absolute move not available in this build; planned: {_fmt_vector(planned)}"
        )
        self._wine_variant(
            rec,
            "goto_twice",
            "vendor 'go to point' twice (bit 31 on sub-command 5)",
            [
                "Home X and Y in Mlaser first if it asks for it (it moves at card homing speed).",
                "Go-to runs at XFastMoveSpeed x EmptyMoveSpeedFactor = 550 mm/s: pick a target "
                "only 20 mm from the current position, path clear, hand on the E-stop.",
                "Use 'go to point' (ribbon, message mp112) with that target, wait, then send "
                "exactly the same target again.",
            ],
            [
                ("first", "Press Enter right after the FIRST go-to finished."),
                ("second", "Press Enter right after the SECOND go-to finished."),
            ],
            [("second_moved", "Did the second go-to move the head (absolute: no; relative: yes)?")],
        )

    def _step5(self, rec: StepRecord) -> None:
        """11 §7 step 5: continuous jog, released with the per-axis stop (A1 §4.1, V1/V3)."""
        speed, hold = self.opts.cont_speed, self.opts.cont_hold_s
        params = self._params(speed)
        start = C.jog_continuous(0, True, params)
        release = C.jog_release_stop([0], start.words[2], params)
        if speed != 50.0:
            rec.note(
                f"continuous jog at {speed:g} mm/s: 11 §7 asks for 50 mm/s, which the daemon only "
                "allows on a homed axis with a verified position scale (DECISIONS D1/D2); the "
                "vendor variant below records the 50 mm/s release vector"
            )
        result: dict[str, Any] = {}

        def action(entry: dict[str, Any]) -> None:
            res = self.link.call("jog_continuous_start", slot=0, positive=True, speed=speed)
            t_start = time.time()
            self._compare(entry, res.get("words") or [])
            rec.event("jog_continuous_start", reply=res)
            refresher = _Refresher(self.opts.socket, 0, time.monotonic() + hold)
            refresher.start()
            samples: list[dict[str, Any]] = []
            try:
                while time.time() - t_start < hold:
                    words = self._read(rec, 2000, 50, "sample", record=False)
                    if words is not None:
                        samples.append(
                            {
                                "dt_s": round(time.time() - t_start, 4),
                                "axes": [_axis_sample(words, 0, self._k)],
                            }
                        )
                    time.sleep(self.opts.sample_period_s)
            finally:
                stop_t = time.time()
                try:
                    stop = self.link.call("jog_continuous_stop", slot=0)
                    rec.event("jog_continuous_stop", reply=stop)
                finally:
                    refresher.stop()
            wait = self._wait_idle(rec, [0], stop_t, self.opts.motion_timeout_s, sample_slots=(0,))
            for s in wait["samples"]:
                s["dt_s"] = round(s["dt_s"] + (stop_t - t_start), 4)
            samples.extend(wait["samples"])
            entry["outcome"] = (
                f"held {stop_t - t_start:.2f} s, stop sent; after stop: {wait['outcome']}"
            )
            entry["deadman_refresh"] = {"calls": refresher.calls, "ended_by": refresher.ended_by}
            result.update(
                {
                    "hold_s": round(stop_t - t_start, 3),
                    "stop_dt_s": round(stop_t - t_start, 4),
                    "samples": samples,
                }
            )

        self._motion(
            rec,
            "cont_jog_x",
            f"Continuous jog X+ at {speed:g} mm/s for {hold:g} s, then key-release stop",
            [start, release],
            f"X moves about {speed * hold:g} mm and stops; watch how abruptly it stops",
            action,
        )
        if result:
            rec.result("continuous_jog", result)
            at_stop = [s for s in result["samples"] if s["dt_s"] <= result["stop_dt_s"]]
            after = [s for s in result["samples"] if s["dt_s"] > result["stop_dt_s"]]
            if at_stop and after:
                p0 = at_stop[-1]["axes"][0]["pulse_position"]
                p1 = after[-1]["axes"][0]["pulse_position"]
                rec.result("position_counts_travelled_after_stop", p1 - p0)
            rec.hypothesis(
                "vd_unit",
                f"stop word vd = {release.words[3]} is a deceleration in mm/s² (A1 §4.1, INFERENCE)",
                "INCONCLUSIVE",
                "decide from the speed samples (axis word +1) after the stop and the capture timing",
            )
        self._observe(
            rec,
            [
                (
                    "stop_feel",
                    "Did X stop abruptly or with a visible ramp? Estimated stopping distance (mm)?",
                ),
                ("travel", "Total X travel measured (mm)?"),
            ],
        )
        self._wine_variant(
            rec,
            "cont_jog_ab",
            "vendor continuous jog X at 50 mm/s",
            [
                "In Mlaser switch jog to continuous (step mode off) and slow speed (50 mm/s).",
                "Hold X+ for about half a second, release.",
            ],
            [("done", "Press Enter after releasing and X has stopped.")],
            [("wine_stop_feel", "Stop behaviour compared with the nexcut jog?")],
        )

    def _step6(self, rec: StepRecord) -> None:
        """11 §7 step 6: home X then Y, one axis at a time (V6)."""
        for slot, name in ((0, "X"), (1, "Y")):
            planned = C.home_axis(slot)
            info: dict[str, Any] = {}

            def action(
                entry: dict[str, Any], slot: int = slot, info: dict[str, Any] = info
            ) -> None:
                t_sent = time.time()
                res = self.link.call("home", slots=[slot])
                rec.event("home", slot=slot, reply=res)
                entry["sent_words"] = None
                entry["matches_plan"] = (
                    "daemon sends home_axis(slot) = planned (not echoed over IPC)"
                )

                def finished(snap: dict[str, Any]) -> bool:
                    return not snap.get("homing") and slot in (snap.get("homed_slots") or ())

                wait = self._wait_idle(
                    rec,
                    [slot],
                    t_sent,
                    self.opts.home_timeout_s,
                    sample_slots=(slot,),
                    until=finished,
                )
                entry["outcome"] = wait["outcome"]
                entry["duration_s"] = wait["duration_s"]
                info.update(wait)

            self._motion(
                rec,
                f"home_{name.lower()}",
                f"Home {name} (card-side speeds and back-off)",
                [planned],
                f"{name} runs to its home switch, backs off and stops; this can travel the full axis",
                action,
            )
            if info:
                samples = info["samples"]
                last = samples[-1]["axes"][0] if samples else None
                rec.result(
                    f"home_{name}",
                    {
                        "outcome": info["outcome"],
                        "duration_s": info["duration_s"],
                        "last_sample": last,
                        "samples": samples[-40:],
                    },
                )
                rec.hypothesis(
                    f"homed_bit15_{name}",
                    f"axis status bit 15 of slot {slot} = homed after [2, {1 << slot}, 0] (A2 §3.1)",
                    _verdict(bool(last and last["homed"])) if last else "INCONCLUSIVE",
                    f"last sample {last}",
                )
            self._observe(
                rec,
                [
                    (f"{name}_direction", f"{name}: homing direction and which end/corner?"),
                    (
                        f"{name}_speeds",
                        f"{name}: fast approach, slow re-approach, back-off distance (estimates)?",
                    ),
                ],
            )

    def _step7(self, rec: StepRecord) -> None:
        """11 §7 step 7 / O8: limit switches by hand with the drives disabled; Y2 tracking."""
        self.op.wait(
            "s7.estop.press",
            "Press the hardware E-stop (drives disabled) and keep it pressed while you actuate "
            "the switches. Press Enter when it is pressed.",
        )
        self._wait_alarm(rec, True, "E-stop pressed for switch test")
        base_st = self._read(rec, 1000, 36, "1000/36 baseline")
        base_ax = self._read(rec, 2000, 50, "2000/50 baseline")
        table: list[dict[str, Any]] = []
        for sw in self.opts.switches:
            key = sw.replace("+", "pos").replace("-", "neg")
            if not self.op.confirm(
                f"s7.switch.{key}",
                f"Actuate the {sw} limit switch by hand and HOLD it. Ready to read? (N = no such switch / skip)",
            ):
                table.append({"switch": sw, "skipped": True})
                continue
            st = self._read(rec, 1000, 36, f"1000/36 {sw} held")
            ax = self._read(rec, 2000, 50, f"2000/50 {sw} held")
            self.op.wait(f"s7.switch.{key}.release", f"Release the {sw} switch, then press Enter.")
            st_rel = self._read(rec, 1000, 36, f"1000/36 {sw} released")
            row: dict[str, Any] = {"switch": sw, "skipped": False}
            if base_st is not None and st is not None:
                changed = _bits(base_st[Status.DI] ^ st[Status.DI], 24)
                row["di_changed"] = [
                    {"port": b + 1, "level_held": (st[Status.DI] >> b) & 1} for b in changed
                ]
                if st_rel is not None:
                    row["di_restored_after_release"] = (st_rel[Status.DI] & 0xFFFFFF) == (
                        base_st[Status.DI] & 0xFFFFFF
                    )
            if base_ax is not None and ax is not None:
                row["axis_bits_changed"] = [
                    {
                        "slot": s,
                        "bits": _bits(
                            (base_ax[AXIS_RO_WORDS * s] ^ ax[AXIS_RO_WORDS * s]) & 0x3F, 6
                        ),
                        "held_bits": _bits(ax[AXIS_RO_WORDS * s] & 0x3F, 6),
                    }
                    for s in range(5)
                    if (base_ax[AXIS_RO_WORDS * s] ^ ax[AXIS_RO_WORDS * s]) & 0x3F
                ]
            table.append(row)
        rec.result("limit_switch_table", table)
        rec.hypothesis(
            "O8_polarity",
            "each hard limit raises axis status bit 0 (+) / bit 1 (-) of its slot (A2 §3.1)",
            "INCONCLUSIVE",
            "compare 'axis_bits_changed' with the switch names in limit_switch_table",
        )
        self.op.wait("s7.estop.release", "Release the E-stop, then press Enter.")
        self._wait_alarm(rec, False, "E-stop released after switch test")
        self._ack_estop(rec)
        # The direction is asked positively for each sign: an empty / unsure answer must not
        # pick a direction, least of all towards the end stop Y sits at (PORT-PLAN §8.2).
        if self.op.confirm(
            "s7.y_direction",
            "After homing (step 6) Y sits at its home end. Does Y+ move AWAY from that end? "
            "(y = plan a Y+ jog)",
        ):
            sign: str | None = "+"
        elif self.op.confirm(
            "s7.y_direction_negative",
            "Does Y- move AWAY from that end? (y = plan a Y- jog, N = skip the Y jog)",
        ):
            sign = "-"
        else:
            sign = None
        rec.result("y_jog_direction", sign)
        if sign is None:
            rec.note("Y jog skipped: the operator did not confirm which direction leaves the end")
            rec.incomplete = True
            self._observe(
                rec,
                [
                    (
                        "switch_types",
                        "Switch types (mechanical / inductive) and any switch that did not "
                        "register?",
                    )
                ],
            )
            return
        mm = self.opts.step_mm if sign == "+" else -self.opts.step_mm
        speed = self.opts.step_speed
        planned = C.jog_step(1, mm, self._params(speed))
        before = self._read(rec, 2000, 50, "2000/50 before Y jog")
        wait: dict[str, Any] = {}

        def action(entry: dict[str, Any]) -> None:
            wait.update(self._step_jog(rec, entry, 1, mm, speed, sample_slots=(1, 2)))

        self._motion(
            rec,
            "jog_y_dual",
            f"Jog Y {mm:+g} mm at {speed:g} mm/s (watch Y2 = slot 2, reg 2022)",
            [planned],
            f"both Y sides move {mm:+g} mm together",
            action,
        )
        after = self._read(rec, 2000, 50, "2000/50 after Y jog")
        if before is not None and after is not None:
            d1 = (
                _axis_sample(after, 1, self._k)["pulse_position"]
                - _axis_sample(before, 1, self._k)["pulse_position"]
            )
            d2 = (
                _axis_sample(after, 2, self._k)["pulse_position"]
                - _axis_sample(before, 2, self._k)["pulse_position"]
            )
            rec.result("y_slot1_delta", d1)
            rec.result("y2_slot2_delta", d2)
            if wait:
                rec.result("samples_2000_slots_1_2", wait["samples"])
            rec.hypothesis(
                "O7_dual_drive",
                "slot 2 (Y2) pulse position 2022 tracks slot 1 during a Y jog",
                "INCONCLUSIVE" if d1 == 0 else _verdict(d2 != 0),
                f"slot1 delta {d1}, slot2 delta {d2}",
            )
        self._observe(
            rec,
            [
                (
                    "switch_types",
                    "Switch types (mechanical / inductive) and any switch that did not register?",
                ),
                ("y2", "Did both Y sides move together?"),
            ],
        )

    def _step8(self, rec: StepRecord) -> None:
        """11 §7 step 8 / O1, C2: FIFO tick period. Native stream deferred (no IPC command)."""
        st = self._read(rec, 1000, 36, "1000/36 FIFO baseline")
        if st is not None:
            rec.result(
                "fifo_baseline",
                {
                    "frame_id_1015": st[Status.FIFO_FRAME_ID],
                    "margin_1016": st[Status.FIFO_SPACE_MARGIN],
                    "interp_config_1017": st[Status.FIFO_INTERP_CONFIG],
                    "processing_status_1019": st[Status.PROCESSING_STATUS],
                },
            )
            rec.hypothesis(
                "C2_margin_empty",
                "reg 1016 == 60000 while the FIFO is empty (11 §4.1 row 16)",
                _verdict(st[Status.FIFO_SPACE_MARGIN] == 60000),
                f"reg 1016 = {st[Status.FIFO_SPACE_MARGIN]}",
            )
        rec.defer(
            "native 0x67<-[1], first frame, 1015/1016 delta, stream, 0x67<-[2]",
            "nexcut-mccd has no FIFO streaming command in its IPC vocabulary yet (M3/M4); "
            "O6 'FIFO start without licence exchange' stays open",
        )
        self._wine_variant(
            rec,
            "dry_run_line",
            "vendor dry run of a 100 mm X line at 50 mm/s (tick period from the capture)",
            [
                "In Mlaser draw (or import) one horizontal 100 mm line, CO2 layer, cut speed 50 mm/s.",
                "Laser key stays OFF, gas closed. Use Dry run (空走), NOT Start.",
                "The dry run may command the Z follower (ZF 103/109 records, 11 §5.4): watch the head.",
            ],
            [
                ("start", "Press Enter at the moment you click Dry run."),
                ("end", "Press Enter at the moment the head stops at the end of the line."),
            ],
            [
                (
                    "speeds",
                    "Speed values Mlaser used for the dry run (layer speed, dry-run speed setting)?",
                ),
                ("stopwatch", "Stopwatch time of the 100 mm move (s), if measured?"),
            ],
        )

    def _step9(self, rec: StepRecord) -> None:
        """11 §7 step 9 / N2: Pause, Continue, Stop in the vendor tool (no native equivalent)."""
        rec.defer(
            "native part",
            "no resume primitive exists in the card vocabulary (N2 OPEN); vendor only",
        )
        ran = self._wine_variant(
            rec,
            "pause_continue_stop",
            "vendor dry job: Pause, Continue, Stop",
            [
                "Load a 200 mm x 100 mm rectangle, CO2 layer, speed 20 mm/s; laser key OFF.",
                "Dry run (空走). After ~3 s click Pause, wait 3 s, click Continue, wait 3 s, click Stop.",
            ],
            [
                ("start", "Press Enter when you click Dry run."),
                ("pause", "Press Enter when you click Pause."),
                ("continue", "Press Enter when you click Continue."),
                ("stop", "Press Enter when you click Stop."),
            ],
            [
                (
                    "pause_motion",
                    "On Pause: did the head stop at once or finish something first? Z?",
                ),
                (
                    "continue_motion",
                    "On Continue: resumed from where it stopped, backed up, or restarted the contour?",
                ),
                ("dialogs", "Any dialog texts (copy them)?"),
            ],
        )
        if not ran:
            rec.incomplete = True

    def _step10(self, rec: StepRecord) -> None:
        """11 §7 step 10 / N5: power-cycle the card; the daemon's first request is READ 1000/2."""
        rec.note(
            "after a link loss nexcut-mccd retries its start-up sequence every reconnect interval; "
            "its first request is READ 1000/2 (11 §7 step 1), so the capture shows the card's "
            "answer to READ 1000/2 immediately after power-up"
        )
        monitor = _LinkMonitor(self.opts.socket)
        monitor.start()
        try:
            self.op.wait(
                "s10.power.off",
                "Switch the motion card / controller power OFF now (not the laptop), then press Enter.",
            )
            off_t = time.time()
            self.op.wait(
                "s10.power.on", "Wait about 10 s, switch the power ON again, then press Enter."
            )
            on_t = time.time()
            deadline = time.monotonic() + self.opts.power_cycle_timeout_s
            reconnected = False
            while time.monotonic() < deadline:
                ch = monitor.snapshot()
                lost = any(c["link"] != "CONNECTED" for c in ch)
                if lost and ch and ch[-1]["link"] == "CONNECTED":
                    reconnected = True
                    break
                time.sleep(0.1)
        finally:
            monitor.stop()
        changes = monitor.snapshot()
        rec.result("power_off_at", _stamp(off_t)["t"])
        rec.result("power_on_at", _stamp(on_t)["t"])
        rec.result("link_changes", changes)
        rec.result("reconnected", reconnected)
        errors = [c["last_error"] for c in changes if c.get("last_error")]
        exc2 = [e for e in errors if "ErrCode:502" in e]
        rec.result("daemon_errors", errors)
        if reconnected:
            ver = self._read(rec, 1000, 2, "1000/2 after power-up")
            rec.result("version_after_power_up", ver)
        rec.hypothesis(
            "N5_exception2",
            "the card answers exception 2 (ErrCode 502) while it is not ready after power-up (08 §7)",
            "CONFIRMED"
            if exc2
            else (
                "INCONCLUSIVE" if not any(c["link"] != "CONNECTED" for c in changes) else "NOT SEEN"
            ),
            f"{len(exc2)} daemon errors with ErrCode:502 (first: {exc2[:1]}); "
            f"{len(errors)} errors in total (daemon_errors)",
        )
        if not reconnected:
            rec.incomplete = True
            rec.note("the daemon did not report a loss and re-connection within the timeout")
        self._observe(
            rec,
            [("boot", "How long did the controller take to boot (s)? Anything on the panel?")],
        )

    # -- end of session ----------------------------------------------------------------------------

    def _finish(self) -> None:
        self.op.wait(
            "final.capture_stopped",
            "\nSession steps done. Stop tcpdump now (Ctrl-C in its terminal), then press Enter.",
        )
        self._copy_vendor_logs()
        if self.opts.run_dissector:
            self.report["capture"] = analyse_capture(
                self.opts.resolved_pcap(), self.report["steps"], self.out_dir
            )

    def _copy_vendor_logs(self) -> None:
        d = self.opts.vendor_log_dir
        if d is None or not d.is_dir():
            return
        dest = self.out_dir / "vendor-log"
        for p in sorted(d.glob("*.log")):
            with contextlib.suppress(OSError):
                if p.stat().st_mtime >= self.t0 - 60:
                    dest.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest / p.name)
                    self.report["vendor_logs"].append(str(dest / p.name))


# ------------------------------------------------------------------------------------------
# Capture analysis
# ------------------------------------------------------------------------------------------


def _dissector_env() -> dict[str, str]:
    env = dict(os.environ)
    src = str(_REPO / "src")
    env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def analyse_capture(pcap: Path, steps: Sequence[dict[str, Any]], out_dir: Path) -> dict[str, Any]:
    """Run ``python -m nexcut.mcc.dissector`` on the pcap and census the traffic per step."""
    out: dict[str, Any] = {"pcap": str(pcap)}
    if not pcap.exists():
        out["error"] = "pcap not found (capture not run or another path: use --pcap)"
        return out
    try:
        digest = hashlib.sha256()
        with pcap.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as exc:
        out["error"] = f"cannot read the pcap ({exc}); fix with: pkexec chown $USER {pcap}"
        return out
    out["size"] = pcap.stat().st_size
    out["sha256"] = digest.hexdigest()
    cmd = [sys.executable, "-m", "nexcut.mcc.dissector", "--summary-only", str(pcap)]
    out["dissector_command"] = "python -m nexcut.mcc.dissector --summary-only " + str(pcap)
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=900, env=_dissector_env()
        )
        out["dissector_returncode"] = proc.returncode
        out["dissector_summary"] = proc.stdout + (
            f"\n[stderr]\n{proc.stderr}" if proc.stderr else ""
        )
        (out_dir / "dissector-summary.txt").write_text(out["dissector_summary"], encoding="utf-8")
        full = subprocess.run(
            [sys.executable, "-m", "nexcut.mcc.dissector", "--fifo", str(pcap)],
            capture_output=True,
            text=True,
            timeout=900,
            env=_dissector_env(),
        )
        (out_dir / "pcap-decoded.txt").write_text(full.stdout, encoding="utf-8")
        out["decoded_file"] = str(out_dir / "pcap-decoded.txt")
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["dissector_error"] = str(exc)
    try:
        out["steps"] = _per_step_traffic(pcap, steps)
    except Exception as exc:  # analysis is best effort; the raw pcap is the evidence
        out["per_step_error"] = f"{type(exc).__name__}: {exc}"
    return out


def label_write(vector: Sequence[int]) -> str:
    """``WRITE`` request vector -> label with 11 §8 sub-command names and the raw words."""
    func, reg, words = vector[0], vector[1], list(vector[3:])
    if reg == 0x67 and words:
        name = {1: "FIFO clear", 2: "FIFO start", 3: "FIFO stop"}.get(words[0], "FIFO control ?")
    elif reg == 0x65 and words:
        sub = words[0]
        if sub == C.CMD_JOG and len(words) > 1:
            name = "jog absolute" if words[1] & C.ABSOLUTE_BIT else "jog relative"
        elif sub == C.CMD_9999 and len(words) > 1:
            name = f"misc 9999/{words[1]}"
        else:
            name = {1: "stop", 2: "home", 5: "go-to", 101: "ZF stop", 102: "resync?"}.get(
                sub, f"sub-command {sub}"
            )
    else:
        name = f"write {reg:#x}"
    shown = [_s32(w) if i == 5 or (reg == 0x65 and i > 4) else w for i, w in enumerate(words)]
    return f"{name} {reg:#x} <- {shown}" if func == 0x40 else f"func {func:#x} {shown}"


def _per_step_traffic(pcap: Path, steps: Sequence[dict[str, Any]]) -> dict[str, Any]:
    from nexcut.mcc.dissector import dissect_file, request_kind

    txs = dissect_file(pcap)
    result: dict[str, Any] = {}
    for step in steps:
        t0, t1 = step.get("started_epoch"), step.get("finished_epoch")
        if t0 is None or t1 is None:
            continue
        win = [t for t in txs if t0 <= t.t_start.timestamp() <= t1]
        by_kind = Counter(request_kind(t.request_vector) for t in win if t.request_vector)
        writes: dict[str, dict[str, Any]] = {}
        census: Counter[int] = Counter()
        frames: set[int] = set()
        running: list[float] = []
        for t in win:
            v = t.request_vector
            if not v:
                continue
            if v[0] == 0x40 and v[1] != 0x66:
                d = writes.setdefault(
                    label_write(v),
                    {"first": _stamp(t.t_start.timestamp())["t"], "count": 0, "status": Counter()},
                )
                d["count"] += 1
                d["status"][t.status + (f"({t.errcode})" if t.errcode is not None else "")] += 1
            f = t.fifo
            if f is not None and f.frame_id not in frames:
                frames.add(f.frame_id)
                census.update(f.census())
            rv = t.reply.vector if t.reply is not None else None
            if v[0] == 0x30 and v[1] == 1000 and v[2] == 36 and rv and len(rv) >= 3 + 20:
                if (rv[3 + Status.PROCESSING_STATUS] & 0xFF) == 1:
                    running.append(t.t_start.timestamp())
        entry: dict[str, Any] = {
            "transactions": len(win),
            "by_request": dict(by_kind.most_common()),
            "writes": {k: {**v, "status": dict(v["status"])} for k, v in list(writes.items())[:60]},
        }
        if frames:
            entry["fifo_frames"] = len(frames)
            entry["fifo_opcode_census"] = {str(k): n for k, n in census.most_common()}
        if running and census.get(3000):
            dur = running[-1] - running[0]
            entry["tick_period_estimate"] = {
                "running_span_s": round(dur, 4),
                "ticks_3000": census[3000],
                "s_per_tick": dur / census[3000],
                "note": "UNVERIFIED estimate: span of 1019==1 polls (30 ms resolution) / tick count",
            }
        result[str(step["n"])] = entry
    return result


# ------------------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tools/m1_session.py",
        description="Guided M1 confirmation session (11-static-findings §7). "
        "Runbook: docs/M1-BENCH-SESSION.md",
    )
    p.add_argument(
        "--socket",
        type=Path,
        help="nexcut-mccd IPC socket (default $XDG_RUNTIME_DIR/nexcut/mccd.sock)",
    )
    p.add_argument(
        "--out-dir", type=Path, help="report directory (default ~/mlaser-captures/m1-<date>)"
    )
    p.add_argument(
        "--pcap", type=Path, help="capture file (default ~/mlaser-captures/m1-<date>.pcap)"
    )
    p.add_argument("--date", help="date tag YYYY-MM-DD (default today)")
    p.add_argument("--steps", default="1-10", help="steps to run, e.g. 1-3,5 (default 1-10)")
    p.add_argument(
        "--step-mm", type=float, default=5.0, help=f"step jog distance mm (0, {MAX_STEP_MM:g}] (5)"
    )
    p.add_argument("--step-speed", type=float, default=20.0, help="step jog speed mm/s (20)")
    p.add_argument(
        "--cont-speed",
        type=float,
        default=20.0,
        help="continuous jog speed mm/s (20; the daemon allows > 20 only when homed and position-verified)",
    )
    p.add_argument(
        "--cont-hold-s",
        type=float,
        default=1.0,
        help=f"continuous jog hold time s (0, {MAX_CONT_HOLD_S:g}] (1.0)",
    )
    p.add_argument("--home-timeout-s", type=float, default=180.0)
    p.add_argument("--motion-timeout-s", type=float, default=30.0)
    p.add_argument("--connect-timeout-s", type=float, default=30.0)
    p.add_argument("--power-cycle-timeout-s", type=float, default=300.0)
    p.add_argument(
        "--estop-wait-s",
        type=float,
        default=5.0,
        help="how long to wait for 1006 bit 30 to follow the E-stop",
    )
    p.add_argument("--switches", default="X-,X+,Y-,Y+,Y2-,Y2+", help="limit switches for step 7")
    p.add_argument(
        "--vendor-log-dir",
        type=Path,
        default=VENDOR_LOG_DIR_DEFAULT,
        help="Mlaser log dir to copy from",
    )
    p.add_argument("--no-dissector", action="store_true", help="do not analyse the pcap at the end")
    return p


def options_from_args(ns: argparse.Namespace) -> Options:
    """Validate the parsed arguments (raises ValueError)."""
    steps = parse_steps(ns.steps)
    if not 0 < ns.step_mm <= MAX_STEP_MM:
        raise ValueError(f"--step-mm must be in (0, {MAX_STEP_MM:g}]")
    for name in ("step_speed", "cont_speed"):
        v = getattr(ns, name)
        if not 0 < v <= MAX_SPEED_MM_S:
            raise ValueError(f"--{name.replace('_', '-')} must be in (0, {MAX_SPEED_MM_S:g}]")
    if not 0 < ns.cont_hold_s <= MAX_CONT_HOLD_S:
        raise ValueError(f"--cont-hold-s must be in (0, {MAX_CONT_HOLD_S:g}]")
    opts = Options(
        socket=ns.socket or default_socket_path(),
        out_dir=ns.out_dir,
        pcap=ns.pcap,
        steps=steps,
        step_mm=ns.step_mm,
        step_speed=ns.step_speed,
        cont_speed=ns.cont_speed,
        cont_hold_s=ns.cont_hold_s,
        motion_timeout_s=ns.motion_timeout_s,
        home_timeout_s=ns.home_timeout_s,
        connect_timeout_s=ns.connect_timeout_s,
        power_cycle_timeout_s=ns.power_cycle_timeout_s,
        estop_wait_s=ns.estop_wait_s,
        switches=tuple(s.strip() for s in ns.switches.split(",") if s.strip()),
        vendor_log_dir=ns.vendor_log_dir,
        run_dissector=not ns.no_dissector,
    )
    if ns.date:
        datetime.strptime(ns.date, "%Y-%m-%d")
        opts.date = ns.date
    return opts


def main(argv: Sequence[str] | None = None, *, operator: Operator | None = None) -> int:
    """Entry point; ``operator`` replaces the terminal (tests)."""
    parser = _parser()
    ns = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        opts = options_from_args(ns)
    except ValueError as exc:
        print(f"m1_session: {exc}", file=sys.stderr)
        return EXIT_USAGE
    return M1Session(opts, operator or ConsoleOperator()).run()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
