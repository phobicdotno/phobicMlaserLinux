"""``nexcut-mccd tui``: a curses operator console over the daemon IPC (PORT-PLAN §4 M1 step 4).

The TUI is a plain IPC client (docs/DECISIONS.md D5): every safety rule stays in the
daemon, the TUI only decides *which* request to send and *when to stop refreshing*.
Quitting the TUI leaves the daemon running; closing its connections makes the daemon stop
any jog they started (D6).

Structure (everything except :class:`CursesKeys` / :class:`CursesScreen` runs headless):

* **Keys** - :class:`KeySource` yields symbolic key names (:data:`KEY_NAMES`).
  :func:`tokenize` turns raw curses codes and undecoded escape sequences into those names;
  :class:`ScriptedKeys` replays a timed script for tests.
* **Backend** - :class:`IpcBackend` talks to the daemon on four connections so the UI never
  waits for the daemon (the task's "responsive while busy" rule):

  - *urgent lane* - ``stop``, ``estop``, ``disarm``, ``jog_continuous_stop``,
    ``ack_estop``; requests queue and are never refused client-side;
  - *command lane* - ``arm_motion``, ``jog_step``, ``jog_continuous_start``, ``home``,
    ``read_block``; a request is **refused client-side while the lane is busy**, so motion
    keys pressed during a slow reply are dropped rather than executed later;
  - *refresher* - ``jog_refresh`` every :attr:`TuiOptions.refresh_period_s`, but only until
    the deadline the controller set (:meth:`IpcBackend.set_refresh`). If the UI thread
    hangs, refreshing stops by itself and the daemon deadman (200 ms, PORT-PLAN §8.2)
    stops the axis;
  - *status* - ``subscribe`` stream (11 §4 decoded snapshot of :mod:`nexcut.mccd.status`).

* **Controller** - :class:`TuiController` maps keys to requests through the data-driven
  :data:`KEY_BINDINGS` table (``?`` shows it as an overlay, :func:`help_lines`) and runs the
  continuous-jog deadman (below). :func:`format_status` renders a snapshot as text lines
  (shared with ``nexcut-mccd status``).

Key safety (docs/DECISIONS.md D11): curses cannot see Caps Lock, so every printable key is
bound in both cases and **no letter key starts motion** - jogs are arrow keys and
Shift+arrow only. Homing is a two-key confirmation (``h`` then ``x`` / ``y``).

Continuous jog (Shift+arrow only, docs/DECISIONS.md D11): a terminal delivers no key-release event, only
auto-repeat. The jog is started on the first key (V1 ``[3, i, v, a, 10a, ±d]``, 11 §2) and
kept alive while repeats arrive. Before the first repeat the window is
:attr:`TuiOptions.initial_hold_s` (must exceed the terminal auto-repeat delay), after it
:attr:`TuiOptions.repeat_gap_s`. When the window passes without a key - key released,
terminal lost focus, SSH stalled - the TUI stops refreshing and sends
``jog_continuous_stop`` (the per-axis stop ``[1, 1<<slot, 2, vd, 10vd]``, 11 §2 V3 /
A1 §4.1). Any other key, a focus-out report (xterm ``CSI ?1004``), or a lost status stream
also stops it. The window lengths are UNVERIFIED port choices (typical X11 auto-repeat
delay 500-660 ms, rate 25-30 Hz).

Positions are shown as axis RO word +2 divided by K from block 50000 (11 §3.2 reg 50017,
11 §4.4). That is mm on the simulator (µm counts); the real card reports motor pulses in
that word (A2 §3), so the value is UNVERIFIED on hardware until the O2/§7 bench step.
"""

from __future__ import annotations

import contextlib
import itertools
import queue
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from nexcut.mcc.safety import ArmState
from nexcut.mccd.ipc import IpcError, MccdClient

__all__ = [
    "AXIS_BY_NAME",
    "BINDINGS_BY_MODE",
    "DISPLAY_AXES",
    "KEY_BINDINGS",
    "KEY_NAMES",
    "STEP_SIZES_MM",
    "CursesKeys",
    "CursesScreen",
    "IpcBackend",
    "KeyBinding",
    "KeySource",
    "Lane",
    "RecordingScreen",
    "Reply",
    "Screen",
    "ScriptedKeys",
    "TuiController",
    "TuiOptions",
    "arm_text",
    "binding_for",
    "format_status",
    "help_lines",
    "parse_axis",
    "run_curses",
    "run_loop",
    "tokenize",
]

AXIS_BY_NAME: dict[str, int] = {"X": 0, "Y": 1, "Y2": 2, "Z": 3, "H": 3, "W": 4}
"""Axis names -> slot (11 §0 O7: 0 X, 1 Y, 2 Y2, 3 height axis, 4 W lifting table)."""
DISPLAY_AXES: tuple[tuple[str, int], ...] = (("X", 0), ("Y", 1), ("Z", 3), ("W", 4))
"""Axes shown on the position line."""
STEP_SIZES_MM: tuple[float, ...] = (0.1, 1.0, 10.0)
"""Step sizes cycled with ``[`` / ``]``."""

# Symbolic key names delivered by a KeySource. Printable keys are the character itself.
UP, DOWN, LEFT, RIGHT = "UP", "DOWN", "LEFT", "RIGHT"
S_UP, S_DOWN, S_LEFT, S_RIGHT = "S_UP", "S_DOWN", "S_LEFT", "S_RIGHT"
PGUP, PGDN = "PGUP", "PGDN"
ESC, ENTER, BACKSPACE = "ESC", "ENTER", "BACKSPACE"
FOCUS_IN, FOCUS_OUT = "FOCUS_IN", "FOCUS_OUT"
RESIZE, UNKNOWN = "RESIZE", "UNKNOWN"
KEY_NAMES: frozenset[str] = frozenset(
    {UP, DOWN, LEFT, RIGHT, S_UP, S_DOWN, S_LEFT, S_RIGHT, PGUP, PGDN, ESC, ENTER, BACKSPACE,
     FOCUS_IN, FOCUS_OUT, RESIZE, UNKNOWN}
)  # fmt: skip

# ------------------------------------------------------------------------------ key table
#
# docs/DECISIONS.md D11: **no single letter key may start motion.** Curses cannot see the
# Caps Lock state, so a letter binding is really a binding of both cases; the vi-style
# H/J/K/L continuous-jog keys therefore turned the lowercase menu key 'h' into a 20 mm/s
# jog X- (safety review R7). Motion in normal mode is bound to arrow keys and Shift+arrow
# only. The home menu keeps letter keys, but they are reachable only after the explicit
# 'h' that opens the menu and they are shown in the prompt - a two-key confirmation, not a
# single keystroke. The invariants are asserted by
# tests/test_mccd_cli_tui.py::test_key_table_has_no_case_or_motion_trap.

MODE_NORMAL, MODE_HOME, MODE_READ, MODE_HELP = "normal", "home", "read", "help"
GLOBAL = "global"
"""Pseudo-mode of the keys that win in every mode (PORT-PLAN §8.2: stop keys)."""


@dataclass(frozen=True, slots=True)
class KeyBinding:
    """One row of :data:`KEY_BINDINGS`: which keys, in which mode, do what."""

    keys: tuple[str, ...]
    """Key names as :func:`tokenize` delivers them. A printable key must be listed in both
    cases, so Caps Lock cannot change what a key does."""
    action: str
    """Stable identifier dispatched by :meth:`TuiController.handle_key`."""
    label: str
    """Key spelling shown in the help overlay."""
    what: str
    """One-line description shown in the help overlay."""
    mode: str = MODE_NORMAL
    motion: bool = False
    """True if this binding can put an axis in motion."""
    arg: Any = None
    """``(slot, positive)`` for jogs, the slot list for homing, ±1 for the step size."""


KEY_BINDINGS: tuple[KeyBinding, ...] = (
    # -- stop keys win in every mode (PORT-PLAN §8.2), in both cases (safety review R7)
    KeyBinding((ESC, "e", "E"), "estop", "Esc / e", "E-STOP (latches until a)", mode=GLOBAL),
    KeyBinding((" ", "s", "S"), "stop", "space / s", "STOP", mode=GLOBAL),
    # -- normal mode, no motion
    KeyBinding(("m", "M"), "arm", "m", "arm motion (needed before any move)"),
    KeyBinding(("d", "D"), "disarm", "d", "disarm (stops first)"),
    KeyBinding(("a", "A"), "ack_estop", "a", "acknowledge the E-stop latch"),
    KeyBinding(("h", "H"), "home_menu", "h", "home menu: then x / y (never a jog, D11)"),
    KeyBinding(("r", "R"), "read_block", "r", "read a register block ADDR/N"),
    KeyBinding(("?",), "help", "?", "this key list"),
    KeyBinding(("q", "Q"), "quit", "q", "quit (the daemon keeps running)"),
    KeyBinding(("[",), "step_size", "[", "smaller step size", arg=-1),
    KeyBinding(("]",), "step_size", "]", "larger step size", arg=+1),
    # -- normal mode, motion: arrow keys only (D11). Up = Y+ and PgUp = W+ are UNVERIFIED
    #    physical directions (the V9 lift sign is not tied to "up" by evidence, 11 §2 V9).
    KeyBinding((LEFT,), "step_jog", "left", "step X-", motion=True, arg=(0, False)),
    KeyBinding((RIGHT,), "step_jog", "right", "step X+", motion=True, arg=(0, True)),
    KeyBinding((UP,), "step_jog", "up", "step Y+", motion=True, arg=(1, True)),
    KeyBinding((DOWN,), "step_jog", "down", "step Y-", motion=True, arg=(1, False)),
    KeyBinding((PGUP,), "step_jog", "PgUp", "step W+ (lift table)", motion=True, arg=(4, True)),
    KeyBinding((PGDN,), "step_jog", "PgDn", "step W- (lift table)", motion=True, arg=(4, False)),
    KeyBinding((S_LEFT,), "cont_jog", "Shift+left", "hold: jog X-", motion=True, arg=(0, False)),
    KeyBinding((S_RIGHT,), "cont_jog", "Shift+right", "hold: jog X+", motion=True, arg=(0, True)),
    KeyBinding((S_UP,), "cont_jog", "Shift+up", "hold: jog Y+", motion=True, arg=(1, True)),
    KeyBinding((S_DOWN,), "cont_jog", "Shift+down", "hold: jog Y-", motion=True, arg=(1, False)),
    # -- home menu (entered with 'h'; every other key cancels it)
    KeyBinding(("x", "X"), "home_axis", "x", "home X", mode=MODE_HOME, motion=True, arg=(0,)),
    KeyBinding(("y", "Y"), "home_axis", "y", "home Y", mode=MODE_HOME, motion=True, arg=(1,)),
    KeyBinding(
        ("b", "B"), "home_axis", "b", "home X then Y (only with --allow-home-all)",
        mode=MODE_HOME, motion=True, arg=(0, 1),
    ),  # fmt: skip
)
"""The complete key table. ``nexcut-mccd tui`` binds nothing that is not listed here."""


def _bindings_by_mode() -> dict[str, dict[str, KeyBinding]]:
    out: dict[str, dict[str, KeyBinding]] = {}
    for b in KEY_BINDINGS:
        table = out.setdefault(b.mode, {})
        for k in b.keys:
            if k in table:  # pragma: no cover - a coding error, caught at import
                raise AssertionError(f"key {k!r} bound twice in mode {b.mode}")
            table[k] = b
    return out


BINDINGS_BY_MODE: dict[str, dict[str, KeyBinding]] = _bindings_by_mode()
"""``mode -> key -> binding``; ``GLOBAL`` holds the keys that win in every mode."""


def binding_for(key: str, mode: str) -> KeyBinding | None:
    """The binding of ``key`` in ``mode``, global keys first (None if unbound)."""
    b = BINDINGS_BY_MODE[GLOBAL].get(key)
    return b if b is not None else BINDINGS_BY_MODE.get(mode, {}).get(key)


def help_lines(*, allow_home_all: bool = False) -> list[str]:
    """The key table as text (help overlay and ``?``)."""
    out = ["KEYS (docs/DECISIONS.md D11: only arrow keys and Shift+arrow start a jog)"]
    groups = (
        (GLOBAL, "always"),
        (MODE_NORMAL, "normal"),
        (MODE_HOME, "home menu (press h first)"),
    )
    for mode, title in groups:
        out.append(f"-- {title} --")
        for b in KEY_BINDINGS:
            if b.mode != mode:
                continue
            if b.action == "home_axis" and b.arg == (0, 1) and not allow_home_all:
                continue
            out.append(f"  {b.label:<12} {b.what}")
    out.append("-- read prompt (press r first) --")
    out.append("  digits / ,  address and count, Enter reads, q cancels")
    return out


_STEP_KEYS: dict[str, tuple[int, bool]] = {
    k: b.arg for b in KEY_BINDINGS if b.action == "step_jog" for k in b.keys
}
"""Step jog keys -> (slot, positive), derived from :data:`KEY_BINDINGS`."""


def parse_axis(text: str) -> int:
    """``X``/``Y``/``Y2``/``Z``/``W`` (any case) or a slot number 0..4 -> slot."""
    t = text.strip().upper()
    if t in AXIS_BY_NAME:
        return AXIS_BY_NAME[t]
    if t.isdigit() and 0 <= int(t) <= 4:
        return int(t)
    raise ValueError(f"unknown axis {text!r} (X, Y, Y2, Z, W or 0..4)")


# ------------------------------------------------------------------------------------ keys

_CSI_FINAL = {"A": UP, "B": DOWN, "C": RIGHT, "D": LEFT}
_CSI_SHIFT = {"A": S_UP, "B": S_DOWN, "C": S_RIGHT, "D": S_LEFT}


def _decode_csi(body: str) -> str:
    """Decode the part of ``ESC [ ...`` after ``[`` (xterm / rxvt conventions)."""
    if body in ("I", "O"):
        return FOCUS_IN if body == "I" else FOCUS_OUT
    final, params = body[-1], body[:-1]
    if final in _CSI_FINAL:
        if params == "":
            return _CSI_FINAL[final]
        mod = params.split(";")[-1]
        return _CSI_SHIFT[final] if mod == "2" else UNKNOWN
    if final in "abcd" and params == "":  # rxvt Shift+arrow
        return _CSI_SHIFT[final.upper()]
    if final == "~":
        return {"5": PGUP, "6": PGDN}.get(params, UNKNOWN)
    return UNKNOWN


def tokenize(codes: Sequence[int], curses_map: Mapping[int, str] | None = None) -> list[str]:
    """Raw key codes (curses ``getch`` values) -> key names.

    Codes >= 256 are curses function keys looked up in ``curses_map``. ``27`` starts an
    escape sequence the terminal library did not decode: ``CSI`` (``ESC [ ... final``) and
    ``SS3`` (``ESC O x``) are decoded; a lone ``ESC`` - or ``ESC`` followed by anything
    else, e.g. an Alt chord - is the E-stop key ``ESC`` (conservative: when in doubt the
    E-stop wins) and the following code is processed on its own.
    """
    cmap = curses_map or {}
    out: list[str] = []
    i, n = 0, len(codes)
    while i < n:
        c = codes[i]
        if c == 27:
            if i + 1 < n and codes[i + 1] == ord("["):
                j = i + 2
                while j < n and not 0x40 <= codes[j] <= 0x7E:
                    j += 1
                if j >= n:
                    out.append(UNKNOWN)
                    return out
                out.append(_decode_csi("".join(chr(x) for x in codes[i + 2 : j + 1])))
                i = j + 1
                continue
            if i + 2 < n and codes[i + 1] == ord("O") and codes[i + 2] < 256:
                out.append(_CSI_FINAL.get(chr(codes[i + 2]), UNKNOWN))
                i += 3
                continue
            out.append(ESC)
            i += 1
            continue
        if c >= 256:
            out.append(cmap.get(c, UNKNOWN))
        elif c in (10, 13):
            out.append(ENTER)
        elif c in (8, 127):
            out.append(BACKSPACE)
        elif 32 <= c < 127:
            out.append(chr(c))
        else:
            out.append(UNKNOWN)
        i += 1
    return out


class KeySource(Protocol):
    """Source of key events (curses terminal or a test script)."""

    def read(self, timeout: float) -> list[str]:
        """Keys available now; waits up to ``timeout`` s for the first one."""
        ...


class ScriptedKeys:
    """Headless :class:`KeySource`: ``[(t_offset_s, key), ...]`` replayed in real time.

    Offsets are relative to the first :meth:`read`. :attr:`done` is true once every key
    was delivered.
    """

    def __init__(
        self, script: Iterable[tuple[float, str]], clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._script = deque(sorted(script, key=lambda e: e[0]))
        self._clock = clock
        self._t0: float | None = None

    @property
    def done(self) -> bool:
        """All scripted keys were delivered."""
        return not self._script

    def read(self, timeout: float) -> list[str]:
        """Deliver due keys, sleeping until the next one (at most ``timeout``)."""
        now = self._clock()
        if self._t0 is None:
            self._t0 = now
        due = []
        while self._script and self._script[0][0] <= now - self._t0:
            due.append(self._script.popleft()[1])
        if due:
            return due
        wait = timeout
        if self._script:
            wait = min(timeout, max(0.0, self._t0 + self._script[0][0] - now))
        time.sleep(wait)
        return []


class Screen(Protocol):
    """Where the controller's text lines go."""

    def draw(self, lines: Sequence[str]) -> None:
        """Replace the screen content."""
        ...


class RecordingScreen:
    """Headless :class:`Screen` keeping the last frame."""

    def __init__(self) -> None:
        self.frames = 0
        self.last: list[str] = []

    def draw(self, lines: Sequence[str]) -> None:
        """Store the frame."""
        self.frames += 1
        self.last = list(lines)


# --------------------------------------------------------------------------------- backend


class Lane(StrEnum):
    """Request lane of :class:`IpcBackend`."""

    URGENT = "urgent"
    COMMAND = "command"


@dataclass(frozen=True, slots=True)
class Reply:
    """Outcome of one backend request, delivered to the controller."""

    tag: str
    cmd: str
    args: dict[str, Any]
    ok: bool
    result: Any = None
    code: str | None = None
    """IPC error code (ipc.py) or ``"ipc"`` when the daemon could not be reached."""
    message: str = ""


class Backend(Protocol):
    """What :class:`TuiController` needs from the daemon connection."""

    def submit(self, lane: Lane, cmd: str, args: dict[str, Any], tag: str = "") -> bool:
        """Queue a request; False if refused client-side (command lane busy)."""
        ...

    def set_refresh(self, slot: int | None, until: float) -> None:
        """Refresh the deadman lease of ``slot`` until monotonic time ``until`` (None = off)."""
        ...

    def replies(self) -> list[Reply]:
        """Replies that arrived since the last call."""
        ...

    def status(self) -> tuple[dict[str, Any] | None, bool]:
        """(latest status snapshot, status stream alive)."""
        ...

    def close(self) -> None:
        """Drain the urgent lane (bounded) and close all connections."""
        ...


ClientFactory = Callable[..., MccdClient]


class _Lane:
    """One IPC connection with a worker thread and a request queue."""

    def __init__(
        self,
        name: str,
        connect: Callable[[], MccdClient],
        post: Callable[[Reply], None],
        *,
        refuse_when_busy: bool,
    ) -> None:
        self.name = name
        self._connect = connect
        self._post = post
        self._refuse = refuse_when_busy
        self._q: queue.Queue[tuple[str, dict[str, Any], str] | None] = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._client: MccdClient | None = None
        self._thread = threading.Thread(target=self._run, name=f"tui-{name}", daemon=True)
        self._thread.start()

    @property
    def busy(self) -> bool:
        """A request is queued or in flight."""
        with self._lock:
            return self._pending > 0

    def submit(self, cmd: str, args: dict[str, Any], tag: str) -> bool:
        with self._lock:
            if self._refuse and self._pending:
                return False
            self._pending += 1
        self._q.put((cmd, dict(args), tag))
        return True

    def wait_idle(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._idle:
            while self._pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._idle.wait(remaining)
        return True

    def _drop_client(self) -> None:
        c, self._client = self._client, None
        if c is not None:
            c.close()

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                self._drop_client()
                return
            cmd, args, tag = item
            try:
                self._post(self._call(cmd, args, tag))  # posted before the lane is idle
            finally:
                with self._idle:
                    self._pending -= 1
                    self._idle.notify_all()

    def _call(self, cmd: str, args: dict[str, Any], tag: str) -> Reply:
        try:
            if self._client is None:
                self._client = self._connect()
            result = self._client.call(cmd, **args)
            return Reply(tag, cmd, args, True, result)
        except IpcError as exc:
            return Reply(tag, cmd, args, False, None, exc.code, exc.message)
        except (OSError, ValueError) as exc:  # includes TimeoutError / ConnectionError
            # A timed-out request leaves the stream out of step: reconnect next time.
            self._drop_client()
            return Reply(tag, cmd, args, False, None, "ipc", f"daemon unreachable: {exc}")

    def close(self) -> None:
        self._q.put(None)
        self._thread.join(timeout=2.0)
        self._drop_client()


class IpcBackend:
    """:class:`Backend` over the daemon Unix socket (module docstring for the lanes)."""

    def __init__(
        self,
        socket_path: Path | str | None = None,
        *,
        refresh_period_s: float = 0.05,
        subscribe_interval_ms: int = 100,
        request_timeout_s: float = 5.0,
        stale_after_s: float = 1.0,
        client_factory: ClientFactory = MccdClient,
    ) -> None:
        self.socket_path = socket_path
        self._factory = client_factory
        self._timeout = request_timeout_s
        self._refresh_period = refresh_period_s
        self._interval_ms = subscribe_interval_ms
        self._stale_after = stale_after_s
        self._replies: deque[Reply] = deque()
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._kick = threading.Event()
        self._refresh_target: tuple[int | None, float] = (None, 0.0)
        self._snapshot: dict[str, Any] | None = None
        self._snapshot_rx = -1e18
        self._status_client: MccdClient | None = None
        self.lanes = {
            Lane.URGENT: _Lane("urgent", self._connect, self._post, refuse_when_busy=False),
            Lane.COMMAND: _Lane("command", self._connect, self._post, refuse_when_busy=True),
        }
        self._threads = [
            threading.Thread(target=self._refresh_loop, name="tui-refresh", daemon=True),
            threading.Thread(target=self._status_loop, name="tui-status", daemon=True),
        ]
        for t in self._threads:
            t.start()

    def _connect(self) -> MccdClient:
        return self._factory(self.socket_path, timeout=self._timeout)

    def _post(self, reply: Reply) -> None:
        with self._lock:
            self._replies.append(reply)

    # -- Backend protocol

    def submit(self, lane: Lane, cmd: str, args: dict[str, Any], tag: str = "") -> bool:
        """Queue ``cmd`` on ``lane`` (command lane: refused while busy)."""
        if self._closed.is_set():
            return False
        return self.lanes[lane].submit(cmd, args, tag or cmd)

    def set_refresh(self, slot: int | None, until: float) -> None:
        """Deadman refresh target; refreshing ends by itself at ``until``."""
        with self._lock:
            changed = self._refresh_target[0] != slot
            self._refresh_target = (slot, until)
        if changed:
            self._kick.set()

    def replies(self) -> list[Reply]:
        """Pop pending replies."""
        with self._lock:
            out = list(self._replies)
            self._replies.clear()
        return out

    def status(self) -> tuple[dict[str, Any] | None, bool]:
        """Latest snapshot and whether the stream delivered one within ``stale_after_s``."""
        with self._lock:
            snap, rx = self._snapshot, self._snapshot_rx
        return snap, time.monotonic() - rx <= self._stale_after

    def busy(self, lane: Lane = Lane.COMMAND) -> bool:
        """True while ``lane`` has a request queued or in flight."""
        return self.lanes[lane].busy

    def wait_idle(self, lane: Lane, timeout: float) -> bool:
        """Wait until ``lane`` has no pending request."""
        return self.lanes[lane].wait_idle(timeout)

    def close(self) -> None:
        """Let queued stops go out (<= 2 s), then close every connection."""
        self.lanes[Lane.URGENT].wait_idle(2.0)
        self._closed.set()
        self._kick.set()
        c = self._status_client
        if c is not None:
            with contextlib.suppress(OSError):
                c.sock.shutdown(2)
        for lane in self.lanes.values():
            lane.close()
        for t in self._threads:
            t.join(timeout=2.0)

    # -- threads

    def _refresh_loop(self) -> None:
        client: MccdClient | None = None
        while not self._closed.is_set():
            self._kick.wait(self._refresh_period)
            self._kick.clear()
            if self._closed.is_set():
                break
            with self._lock:
                slot, until = self._refresh_target
            if slot is None or time.monotonic() >= until:
                continue
            try:
                if client is None:
                    client = self._connect()
                res = client.call("jog_refresh", slot=slot)
                alive = bool(res.get("alive")) if isinstance(res, dict) else False
                if not alive:
                    self._clear_refresh(slot)
                    self._post(Reply("refresh", "jog_refresh", {"slot": slot}, True, res))
            except IpcError as exc:
                self._clear_refresh(slot)
                self._post(
                    Reply("refresh", "jog_refresh", {"slot": slot}, False, None, exc.code,
                          exc.message)
                )  # fmt: skip
            except (OSError, ValueError) as exc:
                if client is not None:
                    client.close()
                client = None
                self._clear_refresh(slot)
                self._post(
                    Reply("refresh", "jog_refresh", {"slot": slot}, False, None, "ipc",
                          f"daemon unreachable: {exc}")
                )  # fmt: skip
        if client is not None:
            client.close()

    def _clear_refresh(self, slot: int) -> None:
        with self._lock:
            if self._refresh_target[0] == slot:
                self._refresh_target = (None, 0.0)

    def _status_loop(self) -> None:
        while not self._closed.is_set():
            try:
                client = self._factory(self.socket_path, timeout=2.0)
            except OSError:
                self._closed.wait(0.5)
                continue
            self._status_client = client
            try:
                client.call("subscribe", interval_ms=self._interval_ms)
                while not self._closed.is_set():
                    ev = client.next_event(0.5)
                    if ev is None:
                        continue
                    if ev.get("event") == "status" and isinstance(ev.get("data"), dict):
                        with self._lock:
                            self._snapshot = ev["data"]
                            self._snapshot_rx = time.monotonic()
            except (OSError, ValueError, IpcError):
                pass
            finally:
                self._status_client = None
                client.close()
            self._closed.wait(0.5)


# ------------------------------------------------------------------------------ controller


@dataclass(frozen=True, slots=True)
class TuiOptions:
    """TUI tunables (CLI flags of ``nexcut-mccd tui``)."""

    step_speed_mm_s: float = 50.0
    """Step jog speed: 11 §2 V2 / PORT-PLAN §4 M1 "X +5 mm at 50 mm/s"."""
    jog_speed_mm_s: float = 20.0
    """Continuous jog speed; 20 mm/s = the un-homed limit of docs/DECISIONS.md D1."""
    initial_hold_s: float = 0.7
    """Keep a continuous jog alive this long after the first key (auto-repeat delay).
    UNVERIFIED port choice; must exceed the terminal's auto-repeat delay."""
    repeat_gap_s: float = 0.15
    """Once repeats arrive: longest gap between repeats before the jog is stopped.
    UNVERIFIED port choice (repeat period ~33-40 ms)."""
    refresh_period_s: float = 0.05
    """``jog_refresh`` period, well inside the 200 ms deadman (PORT-PLAN §8.2)."""
    step_refresh_margin_s: float = 0.3
    """A step longer than ``speed x deadman`` is refreshed for ``|mm| / speed x 1.25 +
    margin`` (the gate leases such steps, safety.py ``_register_lease``). UNVERIFIED."""
    allow_home_all: bool = False
    """Offer ``a`` = home X then Y in the home menu (never by default, 11 §2 V6)."""
    step_index: int = 1
    """Initial index into :data:`STEP_SIZES_MM` (1 mm)."""


@dataclass(slots=True)
class _ContJog:
    token: int
    slot: int
    positive: bool
    key: str
    last_key_t: float
    repeats: bool = False
    acked: bool = False


@dataclass(slots=True)
class _Message:
    text: str = ""
    error: bool = False
    history: list[str] = field(default_factory=list)


class TuiController:
    """Key handling, the continuous-jog deadman and rendering (module docstring).

    Headless: feed :meth:`handle_key` and call :meth:`tick` with a monotonic time; read
    :meth:`lines`. All I/O goes through the :class:`Backend`.
    """

    def __init__(
        self,
        backend: Backend,
        options: TuiOptions | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.options = opts = options or TuiOptions()
        self.clock = clock
        self.step_index = max(0, min(len(STEP_SIZES_MM) - 1, opts.step_index))
        self.mode = "normal"
        """``normal`` | ``home`` (axis menu) | ``read`` (block address prompt)."""
        self.read_buffer = ""
        self.read_result: list[str] = []
        self.cont: _ContJog | None = None
        self.step_refresh: tuple[int, float] | None = None
        self.quit = False
        self.msg = _Message()
        self._tokens = itertools.count(1)
        self._ipc_alive = False
        self.arm_owner: int | None = None
        """``arm_owner`` of this TUI's own ``arm_motion`` reply (D9/R12).

        The status stream runs on a connection of its own, so the snapshot's
        ``arm_owner_is_self`` is about *that* connection, not about the command lane that
        armed.  The id from the reply is what tells the two apart in the status line.  It is
        dropped again as soon as a snapshot reports DISARMED, so an id from a previous daemon
        process (ids restart at 1) cannot be mistaken for this session's."""

    # -- helpers

    @property
    def step_mm(self) -> float:
        """Current step size."""
        return STEP_SIZES_MM[self.step_index]

    def _say(self, text: str, error: bool = False) -> None:
        self.msg.text, self.msg.error = text, error
        self.msg.history.append(text)
        del self.msg.history[:-50]

    def _urgent(self, cmd: str, **args: Any) -> None:
        self.backend.submit(Lane.URGENT, cmd, args)

    def _command(self, cmd: str, tag: str = "", **args: Any) -> bool:
        if self.backend.submit(Lane.COMMAND, cmd, args, tag or cmd):
            return True
        self._say(f"daemon busy with the previous request: {cmd} NOT sent", error=True)
        return False

    def _release_cont(self, reason: str) -> None:
        """Stop the continuous jog: stop refreshing, send the per-axis stop (11 §2 V3)."""
        c = self.cont
        if c is None:
            return
        self.cont = None
        self.backend.set_refresh(None, 0.0)
        self._urgent("jog_continuous_stop", slot=c.slot)
        self._say(f"continuous jog {_axis_label(c.slot, c.positive)} stopped ({reason})")

    def _cancel_motion_tracking(self) -> None:
        self.cont = None
        self.step_refresh = None
        self.backend.set_refresh(None, 0.0)

    # -- keys

    def handle_key(self, key: str, now: float | None = None) -> None:
        """Process one key through :data:`KEY_BINDINGS` (press ``?`` for the overlay)."""
        now = self.clock() if now is None else now
        b = binding_for(key, self.mode)
        # 1. Stop keys win in every mode (PORT-PLAN §8.2), in both cases (D11).
        if b is not None and b.mode == GLOBAL:
            self._cancel_motion_tracking()
            self.mode = MODE_NORMAL
            if b.action == "estop":
                self._urgent("estop")
                self._say("E-STOP sent (latched; press a to acknowledge)", error=True)
            else:
                self._urgent("stop")
                self._say("stop sent")
            return
        # 2. Terminal events.
        if key == FOCUS_OUT:
            self.step_refresh = None
            if self.cont is not None:
                self._release_cont("terminal lost focus")
            else:
                self.backend.set_refresh(None, 0.0)
            return
        if key in (FOCUS_IN, RESIZE):
            return
        # 3. The help overlay swallows the next key.
        if self.mode == MODE_HELP:
            self.mode = MODE_NORMAL
            return
        # 4. A running continuous jog: its own key keeps it alive, any other releases it.
        if self.cont is not None:
            if key == self.cont.key:
                self.cont.last_key_t = now
                self.cont.repeats = True
                return
            self._release_cont("other key")
            if b is not None and b.action == "cont_jog":
                return  # direction change: the new jog needs a fresh READY state (D8)
        # 5. Modes.
        if self.mode == MODE_HOME:
            self._home_menu_key(key, b)
            return
        if self.mode == MODE_READ:
            self._read_prompt_key(key)
            return
        self._normal_key(key, b, now)

    def _normal_key(self, key: str, b: KeyBinding | None, now: float) -> None:
        o = self.options
        if b is None:
            if key not in (ENTER, UNKNOWN):
                self._say(f"key {key!r} not bound (? = key list)")
            return
        if b.action == "quit":
            self.quit = True
        elif b.action == "arm":
            if self._command("arm_motion"):
                self._say("arm motion requested")
        elif b.action == "disarm":
            self.step_refresh = None
            self._urgent("disarm")
            self._say("disarm sent")
        elif b.action == "ack_estop":
            self._urgent("ack_estop")
            self._say("E-stop acknowledge sent")
        elif b.action == "step_size":
            self.step_index = max(0, min(len(STEP_SIZES_MM) - 1, self.step_index + int(b.arg)))
            self._say(f"step size {self.step_mm:g} mm")
        elif b.action == "step_jog":
            slot, positive = b.arg
            mm = self.step_mm if positive else -self.step_mm
            if self._command("jog_step", slot=slot, mm=mm, speed=o.step_speed_mm_s):
                self._say(f"step {_axis_name(slot)} {mm:+g} mm @ {o.step_speed_mm_s:g} mm/s")
        elif b.action == "cont_jog":
            slot, positive = b.arg
            token = next(self._tokens)
            if self._command(
                "jog_continuous_start", tag=f"cont:{token}", slot=slot, positive=positive,
                speed=o.jog_speed_mm_s,
            ):  # fmt: skip
                self.step_refresh = None
                self.cont = _ContJog(token, slot, positive, key, now)
                self._say(
                    f"continuous jog {_axis_label(slot, positive)} @ {o.jog_speed_mm_s:g} mm/s "
                    "(hold the key)"
                )
        elif b.action == "home_menu":
            self.mode = MODE_HOME
            self._say("home: x = X axis, y = Y axis" + (", b = X then Y" if o.allow_home_all
                                                         else "") + ", other key = cancel")  # fmt: skip
        elif b.action == "read_block":
            self.mode = MODE_READ
            self.read_buffer = ""
            self._say("read block: type ADDR/N (e.g. 1000/36), Enter = read, q = cancel")
        elif b.action == "help":
            self.mode = MODE_HELP
            self._say("key list; any key returns")

    def _home_menu_key(self, key: str, b: KeyBinding | None) -> None:
        self.mode = MODE_NORMAL
        if b is None or b.action != "home_axis":
            self._say("home cancelled")
            return
        slots = list(b.arg)
        if len(slots) > 1 and not self.options.allow_home_all:
            self._say("home cancelled")  # X then Y needs --allow-home-all (11 §2 V6)
            return
        if self._command("home", slots=slots):
            self._say("home " + " then ".join(_axis_name(s) for s in slots) + " requested")

    def _read_prompt_key(self, key: str) -> None:
        if key == ENTER:
            self.mode = "normal"
            parts = [p for p in self.read_buffer.replace(",", "/").split("/") if p]
            try:
                addr = int(parts[0])
                n = int(parts[1]) if len(parts) > 1 else 1
                if len(parts) > 2:
                    raise ValueError
            except (ValueError, IndexError):
                self._say(f"bad block {self.read_buffer!r}: use ADDR/N", error=True)
                return
            if self._command("read_block", addr=addr, n=n):
                self._say(f"read {addr}/{n} requested")
        elif key == BACKSPACE:
            self.read_buffer = self.read_buffer[:-1]
        elif len(key) == 1 and (key.isdigit() or key in "/,"):
            if len(self.read_buffer) < 16:
                self.read_buffer += key
        elif key == "q":
            self.mode = "normal"
            self._say("read cancelled")

    # -- periodic

    def tick(self, now: float | None = None) -> None:
        """Process replies, run the continuous-jog deadman, update the refresh target."""
        now = self.clock() if now is None else now
        for r in self.backend.replies():
            self._on_reply(r, now)
        _snap, alive = self.backend.status()
        if self._ipc_alive and not alive and self.cont is not None:
            self._release_cont("status stream lost")
        self._ipc_alive = alive
        c = self.cont
        if c is not None:
            o = self.options
            deadline = c.last_key_t + (o.repeat_gap_s if c.repeats else o.initial_hold_s)
            if now >= deadline:
                self._release_cont("key released")
            elif c.acked:
                self.backend.set_refresh(c.slot, deadline)
            return
        if self.step_refresh is not None:
            slot, until = self.step_refresh
            if now < until:
                self.backend.set_refresh(slot, until)
            else:
                self.step_refresh = None
                self.backend.set_refresh(None, 0.0)

    def _on_reply(self, r: Reply, now: float) -> None:
        err = f"{r.cmd}: {r.code}: {r.message}"
        if r.tag.startswith("cont:"):
            token = int(r.tag.split(":", 1)[1])
            c = self.cont
            current = c is not None and c.token == token
            if not r.ok:
                if current:
                    self.cont = None
                    self.backend.set_refresh(None, 0.0)
                self._say(err, error=True)
            elif current and c is not None:
                c.acked = True
            else:
                # Released (or E-stopped) before the start was acknowledged: stop it again
                # in case the start reached the card after the first stop (lane ordering).
                self._urgent("jog_continuous_stop", slot=int(r.args.get("slot", 0)))
            return
        if r.tag == "refresh":
            slot = r.args.get("slot")
            if self.cont is not None and self.cont.slot == slot:
                self.cont = None
                self._say(
                    "continuous jog ended by the daemon (deadman / limit)" if r.ok else err,
                    error=not r.ok,
                )
            if self.step_refresh is not None and self.step_refresh[0] == slot:
                self.step_refresh = None
            return
        if not r.ok:
            self._say(err, error=True)
            return
        res = r.result if isinstance(r.result, dict) else {}
        if r.cmd == "jog_step":
            if res.get("deadman"):
                speed = float(r.args["speed"])
                dur = abs(float(r.args["mm"])) / speed * 1.25 + self.options.step_refresh_margin_s
                self.step_refresh = (int(r.args["slot"]), now + dur)
        elif r.cmd == "read_block":
            self.read_result = _format_words(int(res.get("addr", 0)), res.get("words") or [])
            self._say(f"read {res.get('addr')}/{len(res.get('words') or [])} ok")
        elif r.cmd == "estop":
            failed = res.get("failed") or []
            if failed:
                self._say(f"E-STOP: failed vectors {failed}", error=True)
        elif r.cmd == "stop":
            failed = res.get("failed") or []
            if failed:
                self._say(f"stop: failed vectors {failed}", error=True)
        elif r.cmd in ("arm_motion", "disarm", "ack_estop"):
            if r.cmd == "arm_motion":
                owner = res.get("arm_owner")
                self.arm_owner = owner if isinstance(owner, int) else None
            elif r.cmd == "disarm":
                self.arm_owner = None
            self._say(f"{r.cmd}: {res.get('arm_state', '')}".rstrip(": "))

    def shutdown(self) -> None:
        """Quit: stop an active continuous jog, then close the backend (daemon keeps running)."""
        if self.cont is not None:
            self._release_cont("quit")
        self._cancel_motion_tracking()
        self.backend.close()

    # -- rendering

    def lines(self) -> list[str]:
        """The frame: status, jog settings, mode prompt, message, help."""
        snap, alive = self.backend.status()
        if snap is not None and snap.get("arm_state") == str(ArmState.DISARMED):
            self.arm_owner = None
        out = format_status(snap, ipc_connected=alive, own_arm_owner=self.arm_owner)
        o = self.options
        jog = (
            f"jog: step {self.step_mm:g} mm @ {o.step_speed_mm_s:g} mm/s   "
            f"continuous @ {o.jog_speed_mm_s:g} mm/s"
        )
        if self.cont is not None:
            jog += f"   [CONTINUOUS {_axis_label(self.cont.slot, self.cont.positive)}]"
        if self.backend_busy():
            jog += "   (waiting for daemon)"
        out.append(jog)
        if self.mode == MODE_HOME:
            keys = "x = X, y = Y" + (", b = X then Y" if o.allow_home_all else "")
            out.append(f"HOME (one axis at a time): {keys}; any other key cancels")
        elif self.mode == MODE_READ:
            out.append(f"READ BLOCK ADDR/N: {self.read_buffer}_")
        out.append(("! " if self.msg.error else "> ") + self.msg.text)
        out.extend(self.read_result)
        if self.mode == MODE_HELP:
            out.extend(help_lines(allow_home_all=o.allow_home_all))
            return out
        out.append(
            "arrows X/Y step  PgUp/PgDn W step  Shift+arrows hold = continuous jog  "
            "[ ] step size"
        )
        out.append(
            "m arm  d disarm  h home  r read block  a ack E-stop  space/s STOP  "
            "Esc/e E-STOP  ? keys  q quit"
        )
        return out

    def backend_busy(self) -> bool:
        """Command lane busy (if the backend can tell)."""
        busy = getattr(self.backend, "busy", None)
        return bool(busy()) if callable(busy) else False


def _axis_name(slot: int) -> str:
    names = {0: "X", 1: "Y", 2: "Y2", 3: "Z", 4: "W"}
    return names.get(slot, f"slot{slot}")


def _axis_label(slot: int, positive: bool) -> str:
    return f"{_axis_name(slot)}{'+' if positive else '-'}"


def _format_words(
    addr: int, words: Sequence[int], per_line: int = 8, max_lines: int = 5
) -> list[str]:
    out = []
    for i in range(0, len(words), per_line):
        if len(out) == max_lines:
            out.append(f"  ... {len(words) - i} more words")
            break
        chunk = " ".join(f"{int(w):>10d}" for w in words[i : i + per_line])
        out.append(f"  {addr + i:>6d}: {chunk}")
    return out


def arm_text(snap: Mapping[str, Any], own_arm_owner: int | None = None) -> str:
    """The arm field of the status line, with *who* owns the arming (D9/R12).

    Since the R12 amendment of D9 the arming owner is the only connection that may jog, home
    or start a job, so "armed" alone does not tell the operator whether *they* can move.
    ``own_arm_owner`` is the ``arm_owner`` this client saw in its own ``arm_motion`` reply -
    needed because the TUI arms on its command lane and reads status on another connection;
    when it is not given, the snapshot's per-connection ``arm_owner_is_self`` decides.
    """
    state = str(snap.get("arm_state"))
    if state == str(ArmState.DISARMED):
        return state
    owner = snap.get("arm_owner")
    mine = own_arm_owner is not None and owner is not None and owner == own_arm_owner
    if not mine and snap.get("arm_owner_is_self") is True:
        mine = True
    if mine:
        return f"{state} (this session)"
    if owner is not None:
        return f"{state} (another client)"
    return state  # armed by an in-process caller (no connection owns it)


def format_status(
    snap: Mapping[str, Any] | None,
    *,
    ipc_connected: bool = True,
    own_arm_owner: int | None = None,
) -> list[str]:
    """Human-readable status lines of a ``status`` snapshot (11 §4, :mod:`nexcut.mccd.status`)."""
    if snap is None:
        return [f"IPC: {'connected' if ipc_connected else 'NOT CONNECTED (retrying)'}  "
                "no status yet"]  # fmt: skip
    ipc = "ok" if ipc_connected else "STALE"
    poll = snap.get("poll_age_s")
    poll_txt = "-" if poll is None else f"{poll * 1000:.0f} ms"
    head = (
        f"IPC {ipc}  link {snap.get('link')}  state {snap.get('machine_state')}  "
        f"arm {arm_text(snap, own_arm_owner)}  poll {poll_txt}"
    )
    if snap.get("estop_latched"):
        head += "  ** E-STOP LATCHED (A = acknowledge) **"
    lines = [head]
    k = int(snap.get("k") or 1000)
    k_known = snap.get("bus_cycle_us") is not None
    homed = set(snap.get("homed_slots") or ())
    axes = {int(a["slot"]): a for a in snap.get("axes") or ()}
    homed_txt = " ".join(f"{n}{'*' if s in homed else '-'}" for n, s in DISPLAY_AXES)
    fifo = (
        f"FIFO margin {snap.get('fifo_margin')}{' (empty)' if snap.get('fifo_empty') else ''}"
        f" frame {snap.get('fifo_frame_id')}{' RUNNING' if snap.get('fifo_running') else ''}"
    )
    homing = snap.get("homing") or ()
    lines.append(
        f"homed {homed_txt}   K={k}{'' if k_known else '?'}   {fifo}"
        + (f"   homing {' '.join(_axis_name(int(s)) for s in homing)}" if homing else "")
    )
    pos = []
    for name, slot in DISPLAY_AXES:
        a = axes.get(slot)
        if a is None:
            pos.append(f"{name} ?")
            continue
        flag = " busy" if a.get("busy") else ""
        pos.append(f"{name} {int(a.get('position_counts', 0)) / k:10.3f}{flag}")
    lines.append("pos mm " + "  ".join(pos) + "   (word/K)")
    alarms = snap.get("alarms") or ()
    if alarms:
        lines.append("alarms: " + "; ".join(f"{a.get('code')} {a.get('text')}" for a in alarms))
    else:
        lines.append("alarms: none")
    for w in snap.get("watchdog") or ():
        lines.append(f"watchdog: {w}")
    if snap.get("machine_fault"):
        lines.append(f"fault: {snap.get('machine_fault')}")
    if snap.get("zf_alarm"):
        lines.append("ZF alarm: " + " ".join(snap.get("zf_alarm") or ()))
    if snap.get("last_error"):
        lines.append(f"last error: {snap.get('last_error')}")
    return lines


# ------------------------------------------------------------------------------ main loop


def _collapse(keys: Sequence[str]) -> list[str]:
    """Drop consecutive duplicate step keys of one batch (buffered repeats after a stall
    must not turn into a queue of steps)."""
    out: list[str] = []
    for k in keys:
        if out and k == out[-1] and k in _STEP_KEYS:
            continue
        out.append(k)
    return out


def run_loop(
    controller: TuiController,
    keys: KeySource,
    screen: Screen,
    *,
    frame_s: float = 0.03,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """Drive the controller until ``q`` (or ``should_stop()``); always calls ``shutdown``."""
    try:
        while not controller.quit and not (should_stop is not None and should_stop()):
            for k in _collapse(keys.read(frame_s)):
                controller.handle_key(k, controller.clock())
            controller.tick(controller.clock())
            screen.draw(controller.lines())
    finally:
        controller.shutdown()


class CursesKeys:  # pragma: no cover - needs a terminal
    """:class:`KeySource` on a curses window (keypad on, 25 ms ESC delay)."""

    def __init__(self, win: Any) -> None:
        import curses

        self.win = win
        self.map = {
            curses.KEY_UP: UP, curses.KEY_DOWN: DOWN, curses.KEY_LEFT: LEFT,
            curses.KEY_RIGHT: RIGHT, curses.KEY_SR: S_UP, curses.KEY_SF: S_DOWN,
            curses.KEY_SLEFT: S_LEFT, curses.KEY_SRIGHT: S_RIGHT, curses.KEY_PPAGE: PGUP,
            curses.KEY_NPAGE: PGDN, curses.KEY_ENTER: ENTER, curses.KEY_BACKSPACE: BACKSPACE,
            curses.KEY_RESIZE: RESIZE,
        }  # fmt: skip
        win.keypad(True)

    def read(self, timeout: float) -> list[str]:
        self.win.timeout(max(0, int(timeout * 1000)))
        first = self.win.getch()
        if first == -1:
            return []
        codes = [first]
        self.win.nodelay(True)
        try:
            while len(codes) < 256:
                c = self.win.getch()
                if c == -1:
                    break
                codes.append(c)
        finally:
            self.win.nodelay(False)
        return tokenize(codes, self.map)


class CursesScreen:  # pragma: no cover - needs a terminal
    """:class:`Screen` on a curses window."""

    def __init__(self, win: Any) -> None:
        self.win = win

    def draw(self, lines: Sequence[str]) -> None:
        import curses

        h, w = self.win.getmaxyx()
        self.win.erase()
        for i, line in enumerate(lines[: max(0, h)]):
            attr = 0
            if (
                line.startswith("!")
                or "E-STOP LATCHED" in line
                or line.startswith("alarms: ")
                and "none" not in line
            ):
                attr = curses.A_BOLD
            with contextlib.suppress(curses.error):
                self.win.addnstr(i, 0, line, max(0, w - 1), attr)
        self.win.refresh()


def run_curses(
    socket_path: Path | str | None, options: TuiOptions | None = None
) -> int:  # pragma: no cover - needs a terminal
    """Run the TUI on the controlling terminal. Returns an exit status."""
    import curses
    import locale
    import sys

    locale.setlocale(locale.LC_ALL, "")
    opts = options or TuiOptions()

    def body(stdscr: Any) -> None:
        with contextlib.suppress(curses.error):
            curses.curs_set(0)
        with contextlib.suppress(AttributeError):
            curses.set_escdelay(25)
        out = sys.__stdout__
        if out is not None:
            out.write("\x1b[?1004h")  # focus in/out reports (xterm); ignored elsewhere
            out.flush()
        backend = IpcBackend(socket_path, refresh_period_s=opts.refresh_period_s)
        controller = TuiController(backend, opts)
        try:
            run_loop(controller, CursesKeys(stdscr), CursesScreen(stdscr))
        finally:
            if out is not None:
                out.write("\x1b[?1004l")
                out.flush()

    try:
        curses.wrapper(body)
    except KeyboardInterrupt:
        return 130
    return 0
