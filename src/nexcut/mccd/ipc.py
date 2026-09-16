"""Local IPC of ``nexcut-mccd``: JSON lines over a Unix domain socket (PORT-PLAN §2.3).

The daemon process is the only owner of the card socket; clients (UI, CLI, tests) get this
IPC and nothing else, so the safety gate cannot be bypassed from a client process
(tests/test_mcc_safety_adversarial.py F12, docs/DECISIONS.md D5).

One daemon per socket (docs/DECISIONS.md D12): :meth:`IpcServer.start` refuses a path that
another daemon already answers on, and removes a stale socket left by a dead one. The card
address has its own lock (:class:`nexcut.mccd.daemon.CardLock`), because two daemons with
different socket paths would otherwise drive the same card.

Socket: ``$XDG_RUNTIME_DIR/nexcut/mccd.sock`` (:func:`nexcut.core.config.default_socket_path`),
directory mode 0700, socket mode 0600, and the peer uid (``SO_PEERCRED``) must equal the
daemon's uid (docs/DECISIONS.md D5). The daemon refuses a socket directory that is a
symlink or is owned by another uid (a pre-created ``/tmp/nexcut-<uid>`` would let that user
replace the socket and swallow stop / E-stop requests); the client refuses a server whose
peer uid is not its own.

Wire format, one JSON object per ``\\n``-terminated UTF-8 line (max :data:`MAX_LINE` bytes):

* request  ``{"id": 7, "cmd": "jog_step", "slot": 0, "mm": 5, "speed": 20}`` - arguments are
  top-level keys; ``id`` is echoed (any JSON scalar, optional).
* response ``{"id": 7, "ok": true, "result": {...}}`` or
  ``{"id": 7, "ok": false, "error": {"code": "refused", "message": "..."}}``.
* event    ``{"event": "status", "seq": n, "data": {...}}`` - pushed after ``subscribe``,
  interleaved with responses on the same connection.

Error codes: ``bad_request``, ``unknown_command``, ``refused`` (safety gate / policy),
``arming``, ``busy``, ``not_connected``, ``card`` (transaction failure), ``internal``.
The command set is defined by the daemon (:data:`nexcut.mccd.daemon.IPC_COMMANDS`).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import socket
import stat
import struct
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = [
    "MAX_LINE",
    "PROTOCOL_VERSION",
    "Connection",
    "IpcError",
    "IpcServer",
    "MccdClient",
    "ensure_private_dir",
]

log = logging.getLogger("nexcut.mccd.ipc")

MAX_LINE = 64 * 1024
"""Longest accepted request line; longer input closes the connection."""
PROTOCOL_VERSION = 1
_SEND_TIMEOUT_S = 1.0
"""A client that does not drain its socket for this long is disconnected (it cannot stall
the daemon's publisher)."""


def ensure_private_dir(d: Path, what: str = "directory") -> None:
    """Create ``d`` mode 0700 and refuse it unless this uid owns a plain directory there.

    Shared by the IPC socket (docs/DECISIONS.md D5 socket-directory amendment) and the card
    lock (D12): without ``XDG_RUNTIME_DIR`` both live under ``/tmp/nexcut-<uid>``, which any
    local user can create first - and then replace what we put in it.
    """
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode):
        raise RuntimeError(f"{what} {d} is not a plain directory (symlink?)")
    if st.st_uid != os.getuid():
        raise RuntimeError(f"{what} {d} belongs to uid {st.st_uid}, not {os.getuid()}")
    if stat.S_IMODE(st.st_mode) & 0o077:
        os.chmod(d, 0o700)


class IpcError(Exception):
    """An IPC error reply (also raised by handlers to produce one)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _peer_uid(sock: socket.socket) -> int | None:
    """uid of the peer process (Linux ``SO_PEERCRED``), None where unsupported."""
    opt = getattr(socket, "SO_PEERCRED", None)
    if opt is None:  # pragma: no cover - non-Linux
        return None
    raw = sock.getsockopt(socket.SOL_SOCKET, opt, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", raw)
    return uid


class Connection:
    """One accepted client connection."""

    def __init__(self, cid: int, sock: socket.socket, peer_uid: int | None) -> None:
        self.id = cid
        self.sock = sock
        self.peer_uid = peer_uid
        self.subscribe_interval_s: float | None = None
        self.next_push = 0.0
        self.event_seq = 0
        self.context: dict[str, Any] = {}
        """Free slot for the daemon (e.g. the connection's input source)."""
        self.closed = False
        self._send_lock = threading.Lock()

    def send(self, obj: dict[str, Any]) -> bool:
        """Send one JSON line; returns False (and closes) if the peer is gone or stalled."""
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        with self._send_lock:
            if self.closed:
                return False
            try:
                self.sock.sendall(data)
                return True
            except OSError:
                self.close()
                return False

    def push_event(self, event: str, data: Any) -> bool:
        """Send a subscription event."""
        self.event_seq += 1
        return self.send({"event": event, "seq": self.event_seq, "data": data})

    def close(self) -> None:
        """Shut the socket down (the reader thread then ends)."""
        if self.closed:
            return
        self.closed = True
        with contextlib.suppress(OSError):
            self.sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(OSError):
            self.sock.close()


Handler = Callable[[Connection, dict[str, Any]], Any]


class IpcServer:
    """Threaded Unix-socket JSON-lines server; the daemon supplies ``handler``."""

    def __init__(
        self,
        path: Path | str,
        handler: Handler,
        *,
        on_open: Callable[[Connection], None] | None = None,
        on_close: Callable[[Connection], None] | None = None,
    ) -> None:
        self.path = Path(path)
        self.handler = handler
        self.on_open = on_open
        self.on_close = on_close
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._conns: dict[int, Connection] = {}
        self._lock = threading.Lock()
        self._next_id = 1
        self._closing = False

    # -- lifecycle ---------------------------------------------------------------------------

    def _prepare_path(self) -> None:
        ensure_private_dir(self.path.parent, "IPC socket directory")
        if self.path.exists() or self.path.is_symlink():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(0.5)
                probe.connect(str(self.path))
            except OSError:
                self.path.unlink()  # stale socket of a dead daemon
            else:
                raise RuntimeError(
                    f"another nexcut-mccd already listens on {self.path} "
                    "(one daemon per socket, docs/DECISIONS.md D12)"
                )
            finally:
                probe.close()

    def start(self) -> IpcServer:
        """Bind, listen and start accepting."""
        self._prepare_path()
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)
        try:
            s.bind(str(self.path))
        finally:
            os.umask(old_umask)
        os.chmod(self.path, 0o600)
        s.listen(16)
        self._sock = s
        self._thread = threading.Thread(
            target=self._accept_loop, name="mccd-ipc-accept", daemon=True
        )
        self._thread.start()
        log.info("IPC listening on %s", self.path)
        return self

    def close(self) -> None:
        """Stop accepting, close every connection (``on_close`` runs for each) and unlink."""
        self._closing = True
        s, self._sock = self._sock, None
        if s is not None:
            with contextlib.suppress(OSError):
                s.shutdown(socket.SHUT_RDWR)
            s.close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with self._lock:
            conns = list(self._conns.values())
        for c in conns:
            c.close()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with self._lock:
                if not self._conns:
                    break
            time.sleep(0.01)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def connections(self) -> list[Connection]:
        """Snapshot of open connections."""
        with self._lock:
            return list(self._conns.values())

    def subscribers_due(self, now: float) -> list[Connection]:
        """Subscribed connections whose next push time has come (advances their schedule)."""
        due = []
        for c in self.connections():
            iv = c.subscribe_interval_s
            if iv is not None and not c.closed and now >= c.next_push:
                c.next_push = max(c.next_push + iv, now)
                due.append(c)
        return due

    # -- threads -----------------------------------------------------------------------------

    def _accept_loop(self) -> None:
        while not self._closing:
            s = self._sock
            if s is None:
                return
            try:
                conn_sock, _ = s.accept()
            except OSError:
                if self._closing:
                    return
                continue
            uid = _peer_uid(conn_sock)
            if uid is not None and uid != os.getuid():
                log.warning("IPC: refusing peer uid %d", uid)
                conn_sock.close()
                continue
            conn_sock.settimeout(_SEND_TIMEOUT_S)
            with self._lock:
                cid = self._next_id
                self._next_id += 1
                conn = Connection(cid, conn_sock, uid)
                self._conns[cid] = conn
            threading.Thread(
                target=self._serve, args=(conn,), name=f"mccd-ipc-{cid}", daemon=True
            ).start()

    def _serve(self, conn: Connection) -> None:
        try:
            if self.on_open is not None:
                self.on_open(conn)
            buf = b""
            while not conn.closed:
                try:
                    chunk = conn.sock.recv(65536)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._dispatch(conn, line)
                if len(buf) > MAX_LINE:
                    conn.send(
                        {
                            "id": None,
                            "ok": False,
                            "error": {"code": "bad_request", "message": "line too long"},
                        }
                    )
                    break
        finally:
            conn.close()
            with self._lock:
                self._conns.pop(conn.id, None)
            if self.on_close is not None:
                try:
                    self.on_close(conn)
                except Exception:  # pragma: no cover - must not kill the server
                    log.exception("IPC on_close")

    def _dispatch(self, conn: Connection, line: bytes) -> None:
        if len(line) > MAX_LINE:
            conn.send(
                {
                    "id": None,
                    "ok": False,
                    "error": {"code": "bad_request", "message": "line too long"},
                }
            )
            return
        req_id: Any = None
        try:
            try:
                req = json.loads(line)
            except (ValueError, UnicodeDecodeError) as exc:
                raise IpcError("bad_request", f"invalid JSON: {exc}") from exc
            if not isinstance(req, dict):
                raise IpcError("bad_request", "request must be a JSON object")
            req_id = req.get("id")
            if isinstance(req_id, dict | list):
                req_id = None
                raise IpcError("bad_request", "id must be a scalar")
            if not isinstance(req.get("cmd"), str):
                raise IpcError("bad_request", "missing 'cmd'")
            result = self.handler(conn, req)
            conn.send({"id": req_id, "ok": True, "result": result})
        except IpcError as exc:
            conn.send(
                {"id": req_id, "ok": False, "error": {"code": exc.code, "message": exc.message}}
            )
        except Exception as exc:
            log.exception("IPC handler")
            conn.send(
                {"id": req_id, "ok": False, "error": {"code": "internal", "message": str(exc)}}
            )


class MccdClient:
    """Blocking client for the daemon IPC (UI, CLI, tests)."""

    def __init__(self, path: Path | str | None = None, *, timeout: float = 10.0) -> None:
        if path is None:
            from nexcut.core.config import default_socket_path

            path = default_socket_path()
        self.path = Path(path)
        self.timeout = timeout
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(str(self.path))
            uid = _peer_uid(self.sock)
        except OSError:
            self.sock.close()
            raise
        if uid is not None and uid != os.getuid():
            self.sock.close()
            raise PermissionError(f"{self.path}: server runs as uid {uid}, not {os.getuid()}")
        self._buf = b""
        self._next_id = 1
        self.events: deque[dict[str, Any]] = deque()
        self._lock = threading.Lock()

    def __enter__(self) -> MccdClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the connection."""
        with contextlib.suppress(OSError):
            self.sock.close()

    def _read_message(self, timeout: float | None) -> dict[str, Any] | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while b"\n" not in self._buf:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return None
            self.sock.settimeout(remaining if remaining is not None else self.timeout)
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError:
                if deadline is None:
                    raise
                return None
            if not chunk:
                raise ConnectionError("mccd closed the connection")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line)

    def request(self, cmd: str, **args: Any) -> dict[str, Any]:
        """Send a request and return the raw response object (events are queued)."""
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            payload = {"id": rid, "cmd": cmd, **args}
            self.sock.sendall((json.dumps(payload) + "\n").encode())
            deadline = time.monotonic() + self.timeout
            while True:
                msg = self._read_message(max(0.0, deadline - time.monotonic()))
                if msg is None:
                    raise TimeoutError(f"no reply to {cmd!r}")
                if "event" in msg:
                    self.events.append(msg)
                    continue
                if msg.get("id") == rid or msg.get("id") is None:
                    return msg

    def call(self, cmd: str, **args: Any) -> Any:
        """Send a request; return ``result`` or raise :class:`IpcError`."""
        msg = self.request(cmd, **args)
        if msg.get("ok"):
            return msg.get("result")
        err = msg.get("error") or {}
        raise IpcError(str(err.get("code", "internal")), str(err.get("message", "")))

    def next_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Next pushed event (None on timeout)."""
        with self._lock:
            if self.events:
                return self.events.popleft()
            deadline = None if timeout is None else time.monotonic() + timeout
            while True:
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                msg = self._read_message(remaining)
                if msg is None:
                    return None
                if "event" in msg:
                    return msg
