"""Console entry point ``nexcut-mccd`` (PORT-PLAN §2.3, §4 M1 step 4).

::

    nexcut-mccd serve [--config PATH] [--card-ip IP | --sim] [--card-port N]
                      [--socket PATH] [-v]                  # the daemon ("run" is an alias)
    nexcut-mccd tui   [--socket PATH] [--step-speed V] [--jog-speed V] [--allow-home-all] ...
    nexcut-mccd status [--json]                             # one-shot snapshot
    nexcut-mccd arm | disarm | stop | estop | ack-estop     # one-shot
    nexcut-mccd jog AXIS MM [--speed V] [--wait]            # one relative step jog
    nexcut-mccd home AXIS [--wait]                          # one axis
    nexcut-mccd ctl CMD [JSON-ARGS]                         # any IPC request, raw reply
    nexcut-mccd --version

Every client subcommand accepts ``--socket PATH`` (default ``$XDG_RUNTIME_DIR/nexcut/mccd.sock``).

Exit status: 0 success, 1 refused / failed (the daemon's error is printed to stderr),
2 usage or configuration error, 3 the daemon cannot be reached.

Safety (PORT-PLAN §8, docs/DECISIONS.md D3): the default card address is 127.0.0.1. A
non-loopback card is used only when the operator passes ``--card-ip`` on the command line;
a non-loopback ``card.ip`` from ``config.toml`` alone makes ``serve`` exit with status 2.
``--sim`` starts the card simulator (:mod:`nexcut.mcc.simulator`) in-process on an
ephemeral loopback port and points the daemon at it. The daemon never arms by itself:
motion needs an explicit ``arm`` (or ``m`` in the TUI) followed by a motion command. The
one-shot commands never arm implicitly.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import signal
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from nexcut import __version__
from nexcut.core.config import (
    ConfigError,
    card_ip_needs_cli_confirmation,
    is_loopback,
    load_config,
)

__all__ = ["EXIT_ERROR", "EXIT_OK", "EXIT_UNREACHABLE", "EXIT_USAGE", "main"]

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_UNREACHABLE = 3

_ONE_SHOT = {
    "arm": "arm_motion",
    "disarm": "disarm",
    "stop": "stop",
    "estop": "estop",
    "ack-estop": "ack_estop",
}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nexcut-mccd", description="MCC100 driver daemon and clients")
    p.add_argument("-V", "--version", action="store_true", help="print the version and exit")
    sock = argparse.ArgumentParser(add_help=False)
    sock.add_argument(
        "--socket", help="IPC socket path (default $XDG_RUNTIME_DIR/nexcut/mccd.sock)"
    )
    sub = p.add_subparsers(dest="command")

    serve = sub.add_parser("serve", aliases=["run"], parents=[sock], help="run the daemon")
    serve.add_argument("--config", help="config.toml (default $XDG_CONFIG_HOME/nexcut/config.toml)")
    src = serve.add_mutually_exclusive_group()
    src.add_argument("--card-ip", help="card IP; required for any non-loopback (real) card")
    src.add_argument(
        "--sim", "--simulator", dest="sim", action="store_true",
        help="start the card simulator in-process on loopback and use it",
    )  # fmt: skip
    serve.add_argument("--card-port", type=int, help="card UDP port (default 502)")
    serve.add_argument("-v", "--verbose", action="count", default=0)

    tui = sub.add_parser(
        "tui", parents=[sock], help="curses operator console (daemon keeps running)"
    )
    tui.add_argument("--step-speed", type=float, default=50.0, help="step jog speed mm/s (50)")
    tui.add_argument("--jog-speed", type=float, default=20.0, help="continuous jog speed mm/s (20)")
    tui.add_argument(
        "--hold-initial-ms", type=int, default=700,
        help="continuous jog: keep-alive after the first key, > terminal repeat delay (700)",
    )  # fmt: skip
    tui.add_argument(
        "--repeat-gap-ms", type=int, default=150,
        help="continuous jog: longest gap between key repeats before stopping (150)",
    )  # fmt: skip
    tui.add_argument(
        "--allow-home-all", action="store_true",
        help="offer 'home X then Y' in the home menu (default: one axis per request)",
    )  # fmt: skip

    status = sub.add_parser("status", parents=[sock], help="print one status snapshot")
    status.add_argument("--json", action="store_true", help="raw JSON snapshot")

    for name, cmd in _ONE_SHOT.items():
        sub.add_parser(name, parents=[sock], help=f"send '{cmd}'")

    jog = sub.add_parser("jog", parents=[sock], help="one relative step jog (V2)")
    jog.add_argument("axis", help="X, Y (jog-enabled in M1), W, Z or a slot number")
    jog.add_argument("mm", type=float, help="signed distance in mm")
    jog.add_argument("--speed", type=float, default=50.0, help="mm/s (default 50)")
    jog.add_argument("--wait", action="store_true", help="wait until the axis is idle again")
    jog.add_argument("--timeout", type=float, default=60.0, help="--wait timeout in s (60)")

    home = sub.add_parser("home", parents=[sock], help="home one axis (V6)")
    home.add_argument("axis", help="X or Y")
    home.add_argument("--wait", action="store_true", help="wait until homing finished")
    home.add_argument("--timeout", type=float, default=180.0, help="--wait timeout in s (180)")

    ctl = sub.add_parser("ctl", parents=[sock], help="send one raw IPC request, print the reply")
    ctl.add_argument("cmd", help="command, e.g. status")
    ctl.add_argument("args", nargs="?", default="{}", help="JSON object, e.g. '{\"slot\": 0}'")
    return p


# ---------------------------------------------------------------------------------- serve


def _serve(ns: argparse.Namespace, stop_event: threading.Event | None = None) -> int:
    """Run the daemon until SIGINT/SIGTERM (or ``stop_event``)."""
    from nexcut.mccd.daemon import MccDaemon

    logging.basicConfig(
        level=logging.DEBUG if ns.verbose > 1 else logging.INFO if ns.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        cfg = load_config(ns.config)
        cfg = cfg.with_card(ns.card_ip, ns.card_port)
    except ConfigError as exc:
        print(f"nexcut-mccd: config: {exc}", file=sys.stderr)
        return EXIT_USAGE
    sim = None
    card_addr = None
    if ns.sim:
        from nexcut.mcc.simulator import CardSimulator

        sim = CardSimulator().start()
        card_addr = sim.address
    elif card_ip_needs_cli_confirmation(cfg, ns.card_ip):
        print(
            f"nexcut-mccd: config card.ip {cfg.card.ip} is not loopback; pass it explicitly "
            "with --card-ip to talk to a real card (PORT-PLAN §8)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if card_addr is None and not is_loopback(cfg.card.ip):
        logging.getLogger("nexcut.mccd").warning(
            "REAL CARD at %s:%d (explicit --card-ip)", cfg.card.ip, cfg.card.port
        )
    stop = stop_event or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
    try:
        daemon = MccDaemon(cfg, card_addr=card_addr, socket_path=ns.socket).start()
    except RuntimeError as exc:
        print(f"nexcut-mccd: {exc}", file=sys.stderr)
        if sim is not None:
            sim.stop()
        return EXIT_ERROR
    where = f"simulator {card_addr[0]}:{card_addr[1]}" if card_addr else f"card {cfg.card.ip}"
    print(f"nexcut-mccd {__version__}: IPC on {daemon.socket_path} ({where})", file=sys.stderr)
    try:
        stop.wait()
    finally:
        daemon.close()
        if sim is not None:
            sim.stop()
    return EXIT_OK


# -------------------------------------------------------------------------------- clients


class _Unreachable(Exception):
    pass


def _client(ns: argparse.Namespace) -> Any:
    from nexcut.mccd.ipc import MccdClient

    try:
        return MccdClient(ns.socket, timeout=10.0)
    except OSError as exc:
        raise _Unreachable(str(exc)) from exc


def _fail(prog: str, exc: Exception) -> int:
    from nexcut.mccd.ipc import IpcError

    if isinstance(exc, IpcError):
        print(f"nexcut-mccd {prog}: {exc.code}: {exc.message}", file=sys.stderr)
        return EXIT_ERROR
    print(f"nexcut-mccd {prog}: cannot reach the daemon: {exc}", file=sys.stderr)
    return EXIT_UNREACHABLE


def _print_json(obj: Any, indent: int | None = None) -> None:
    print(json.dumps(obj, indent=indent))


def _with_client(ns: argparse.Namespace, prog: str, fn: Callable[[Any], int]) -> int:
    from nexcut.mccd.ipc import IpcError

    try:
        with _client(ns) as client:
            return fn(client)
    except IpcError as exc:
        return _fail(prog, exc)
    except (_Unreachable, OSError) as exc:
        return _fail(prog, exc)


def _status(ns: argparse.Namespace) -> int:
    def run(client: Any) -> int:
        snap = client.call("status")
        if ns.json:
            _print_json(snap, indent=2)
        else:
            from nexcut.mccd.tui import format_status

            print("\n".join(format_status(snap)))
        return EXIT_OK

    return _with_client(ns, "status", run)


def _one_shot(ns: argparse.Namespace) -> int:
    cmd = _ONE_SHOT[ns.command]

    def run(client: Any) -> int:
        res = client.call(cmd)
        _print_json(res)
        failed = res.get("failed") if isinstance(res, dict) else None
        return EXIT_ERROR if failed else EXIT_OK

    return _with_client(ns, ns.command, run)


def _fresh_snapshot(client: Any, after_wall: float) -> dict[str, Any] | None:
    """A status snapshot whose axis block was read after wall time ``after_wall``."""
    snap = client.call("status")
    age = snap.get("axis_ro_age_s")
    if age is None or float(snap.get("t", 0.0)) - float(age) <= after_wall:
        return None
    return snap


def _wait(
    client: Any,
    prog: str,
    after_wall: float,
    timeout: float,
    done: Callable[[dict[str, Any]], bool],
    refresh: Callable[[], None] | None = None,
) -> int:
    """Poll status until ``done`` on a snapshot taken after the command (D8 freshness)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if refresh is not None:
            refresh()
        snap = _fresh_snapshot(client, after_wall)
        if snap is not None:
            if snap.get("estop_latched") or snap.get("arm_state") == "DISARMED":
                print(
                    f"nexcut-mccd {prog}: aborted: arm {snap.get('arm_state')}, "
                    f"state {snap.get('machine_state')}, watchdog {snap.get('watchdog')}",
                    file=sys.stderr,
                )
                return EXIT_ERROR
            if done(snap):
                return EXIT_OK
        time.sleep(0.05)
    print(f"nexcut-mccd {prog}: timed out after {timeout:g} s", file=sys.stderr)
    return EXIT_ERROR


def _axis_idle(snap: dict[str, Any], slot: int) -> bool:
    axes = {int(a["slot"]): a for a in snap.get("axes") or ()}
    a = axes.get(slot)
    return snap.get("machine_state") == "READY" and a is not None and not a.get("busy")


def _jog(ns: argparse.Namespace) -> int:
    from nexcut.mccd.tui import parse_axis

    try:
        slot = parse_axis(ns.axis)
    except ValueError as exc:
        print(f"nexcut-mccd jog: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if not math.isfinite(ns.mm) or not math.isfinite(ns.speed) or ns.speed <= 0:
        print("nexcut-mccd jog: MM and --speed must be finite, speed > 0", file=sys.stderr)
        return EXIT_USAGE

    def run(client: Any) -> int:
        res = client.call("jog_step", slot=slot, mm=ns.mm, speed=ns.speed)
        t_sent = time.time()
        _print_json(res)
        refresh: Callable[[], None] | None = None
        if res.get("deadman"):
            # The gate leased this step (|d| > v x deadman, PORT-PLAN §8.2): keep it alive for
            # its expected duration only, so it still stops if this process dies.
            until = time.monotonic() + abs(ns.mm) / ns.speed * 1.25 + 0.3
            state = {"alive": True}

            def refresh() -> None:
                if state["alive"] and time.monotonic() < until:
                    state["alive"] = bool(client.call("jog_refresh", slot=slot).get("alive"))

            if not ns.wait:
                while state["alive"] and time.monotonic() < until:
                    refresh()
                    time.sleep(0.05)
        if not ns.wait:
            return EXIT_OK
        return _wait(client, "jog", t_sent, ns.timeout, lambda s: _axis_idle(s, slot), refresh)

    return _with_client(ns, "jog", run)


def _home(ns: argparse.Namespace) -> int:
    from nexcut.mccd.tui import parse_axis

    try:
        slot = parse_axis(ns.axis)
    except ValueError as exc:
        print(f"nexcut-mccd home: {exc}", file=sys.stderr)
        return EXIT_USAGE

    def run(client: Any) -> int:
        res = client.call("home", slots=[slot])
        t_sent = time.time()
        _print_json(res)
        if not ns.wait:
            return EXIT_OK

        def finished(snap: dict[str, Any]) -> bool:
            return (
                not snap.get("homing")
                and slot in (snap.get("homed_slots") or ())
                and _axis_idle(snap, slot)
            )

        return _wait(client, "home", t_sent, ns.timeout, finished)

    return _with_client(ns, "home", run)


def _ctl(ns: argparse.Namespace) -> int:
    try:
        args = json.loads(ns.args)
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
    except ValueError as exc:
        print(f"nexcut-mccd ctl: {exc}", file=sys.stderr)
        return EXIT_USAGE
    args.pop("cmd", None)
    args.pop("id", None)
    try:
        with _client(ns) as client:
            reply = client.request(ns.cmd, **args)
    except (_Unreachable, OSError) as exc:
        return _fail("ctl", exc)
    _print_json(reply, indent=2)
    return EXIT_OK if reply.get("ok") else EXIT_ERROR


def _tui(ns: argparse.Namespace) -> int:  # pragma: no cover - needs a terminal
    from nexcut.mccd.tui import TuiOptions, run_curses

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("nexcut-mccd tui: needs a terminal", file=sys.stderr)
        return EXIT_USAGE
    opts = TuiOptions(
        step_speed_mm_s=ns.step_speed,
        jog_speed_mm_s=ns.jog_speed,
        initial_hold_s=ns.hold_initial_ms / 1000.0,
        repeat_gap_s=ns.repeat_gap_ms / 1000.0,
        allow_home_all=ns.allow_home_all,
    )
    return run_curses(ns.socket, opts)


def main(argv: list[str] | None = None, *, stop_event: threading.Event | None = None) -> int:
    """Entry point; returns the process exit status.

    ``stop_event`` ends ``serve`` from another thread (tests; signals are only installed
    when running on the main thread).
    """
    ns = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    if ns.version:
        print(f"nexcut-mccd {__version__}")
        return EXIT_OK
    cmd = ns.command
    if cmd in ("serve", "run"):
        return _serve(ns, stop_event)
    if cmd == "tui":
        return _tui(ns)
    if cmd == "status":
        return _status(ns)
    if cmd in _ONE_SHOT:
        return _one_shot(ns)
    if cmd == "jog":
        return _jog(ns)
    if cmd == "home":
        return _home(ns)
    if cmd == "ctl":
        return _ctl(ns)
    _parser().print_help(sys.stderr)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
