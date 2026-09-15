"""mccd/ipc.py transport: JSON lines, errors, events, socket permissions (PORT-PLAN §2.3)."""

from __future__ import annotations

import os
import socket
import stat
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from nexcut.mccd.ipc import MAX_LINE, Connection, IpcError, IpcServer, MccdClient


@pytest.fixture
def sock_dir() -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(prefix="nxipc"))
    yield d
    for p in sorted(d.rglob("*"), reverse=True):
        p.unlink() if not p.is_dir() else p.rmdir()
    d.rmdir()


def echo_handler(conn: Connection, req: dict[str, Any]) -> Any:
    if req["cmd"] == "boom":
        raise RuntimeError("kaputt")
    if req["cmd"] == "refuse":
        raise IpcError("refused", "no")
    if req["cmd"] == "sub":
        conn.subscribe_interval_s = 0.01
        return {}
    return {"echo": req}


def test_round_trip_errors_and_permissions(sock_dir: Path) -> None:
    path = sock_dir / "run" / "mccd.sock"
    server = IpcServer(path, echo_handler).start()
    try:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        with MccdClient(path, timeout=2) as c:
            assert c.call("hello", x=1)["echo"] == {"id": 1, "cmd": "hello", "x": 1}
            with pytest.raises(IpcError) as exc:
                c.call("refuse")
            assert exc.value.code == "refused"
            with pytest.raises(IpcError) as exc:
                c.call("boom")
            assert exc.value.code == "internal"
            c.sock.sendall(b"not json\n")
            msg = c._read_message(2)
            assert msg is not None and msg["error"]["code"] == "bad_request"
            c.sock.sendall(b'{"id": 5}\n')
            msg = c._read_message(2)
            assert msg is not None and msg["id"] == 5 and msg["error"]["code"] == "bad_request"
        with pytest.raises(RuntimeError, match="already listens"):
            IpcServer(path, echo_handler).start()
    finally:
        server.close()
    assert not path.exists()


def test_stale_socket_is_replaced(sock_dir: Path) -> None:
    path = sock_dir / "mccd.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.close()  # bound, never listening: stale
    server = IpcServer(path, echo_handler).start()
    try:
        with MccdClient(path, timeout=2) as c:
            assert c.call("x")
    finally:
        server.close()


def test_events_and_overlong_line(sock_dir: Path) -> None:
    path = sock_dir / "mccd.sock"
    closed: list[int] = []
    server = IpcServer(path, echo_handler, on_close=lambda conn: closed.append(conn.id)).start()
    try:
        with MccdClient(path, timeout=2) as c:
            c.call("sub")
            time.sleep(0.05)
            for conn in server.subscribers_due(time.monotonic()):
                conn.push_event("status", {"n": 1})
            ev = c.next_event(1.0)
            assert ev is not None and ev["event"] == "status" and ev["data"] == {"n": 1}
            c.sock.sendall(b"x" * (MAX_LINE + 10))
            msg = c._read_message(2)
            assert msg is not None and msg["error"]["message"] == "line too long"
        deadline = time.monotonic() + 2
        while len(closed) < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert closed
    finally:
        server.close()
    assert os.getuid() >= 0
