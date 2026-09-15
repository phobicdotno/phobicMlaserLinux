"""MCC100 transaction engine: one UDP request datagram, one reply datagram (04 §3.1, §3.3).

This is a port of ``CMCHalAPI``'s transaction routine ``0x1001d6a0`` (called by
``readReg`` ``0x100225e0`` / ``writeReg`` ``0x10022760``) and of the timeout setter
``setTimeout`` ``0x10006570`` (HAL vtable +0x1c).

Behaviour, with the evidence re-read from ``NCModule.dll`` for this module
(``.scratch/asm/NCModule.asm``):

* Socket (04 §3.1): ``socket(AF_INET, SOCK_DGRAM)``, **no bind/connect**,
  ``SO_SNDTIMEO``/``SO_RCVTIMEO`` = timeout, ``select()`` timeout ``{0, ms*1000}``.
* HAL address windows (04 §3.4; ``readReg`` ``0x100225e0``, ``writeReg`` ``0x10022760``):
  requests whose address the vendor HAL answers locally never reach the wire, see
  :func:`hal_suppresses`. Deviation: the vendor returns fabricated zeros / success; this
  port raises :class:`RequestSuppressed` instead (no datagram, no sequence number used).
* Pre-transaction flush (``0x1001d6d9`` -> ``0x10006280``): inside the critical section,
  before the sequence number is taken, ``select(sock, timeout {0,0})`` and, if readable,
  one ``recv(sock, buf, 100, 0)``: exactly one queued datagram (typically the late reply
  of an abandoned transaction) is discarded.
* Sequence (04 §3.2): per-instance u16, the *current* value is used and then
  post-incremented (``0x1001d719..0x1001d731``), so a fresh socket starts at 0.
* Loop structure (``0x1001d7ef..0x1001db92``)::

      send_left = send_times
      while send_left > 0:
          sendto                  error: log "Sendto" (R=0); retry (send_left-=1, no sleep)
                                  only for 10052/10053/10054/10060, else abort
          recv_left = recv_times
          while recv_left > 0:
              select              0 -> 10060, <0 -> WSAGetLastError, set-but-not-ISSET -> 9999;
                                  log "RecvErr_selectFunc"; first recv try of the first send
                                  with 10060 -> resend at once; else recv_left-=1, select again
              recvfrom            error: log "Recvfrom"; first/first 10060 -> resend;
                                  10052/53/54/60 -> recv_left-=1; other -> resend
              decodeWithSeq rc    rc!=0: log "DecodeRecvData" 2000+rc; rc 4 (stale seq) ->
                                  recv_left-=1 and receive again; other rc -> abort
              out[0]==req[0] and out[1]==req[1]         -> return 1 (success)
              out[0]==req[0]+0x80 -> 500+out[1], else 603; log "RecvDataErr"; abort
          send_left -= 1; Sleep(send_interval)          (resend path 0x1001db77)

* Retry counts (**corrects 04 §1 / 00 §4 "send <= 2"**): every MC call site passes
  ``setTimeout(MCTimeout, MCTimeout, -1, MCMaxRecvTime, MCSendInterval)``
  (``0x10039320``, ``0x100528ad``, ``0x1005291e``, ``0x10052a6c``) and ``-1`` makes the
  setter compute ``send_times = MCFifoTime / MCTimeout`` (``0x100065bf..0x100065d3``;
  ``+0x134`` is loaded from ``MCFifoTime`` at ``0x1001795c``). ``MCMaxSendTime`` is read
  into ``CVirtualMachine+0x814`` (``0x100586f0``) and never read again. With the shipped
  ini (1600 / 500) that is **3 sends x 3 recv tries**, and because the first send only
  gets one select before resending, the timeout ladder is exactly the logged
  ``1/1, 1/2, 2/2, 3/2, 1/3, 2/3, 3/3`` = 7 select timeouts (3 datagrams sent), 3.5 s
  (08 §2.3). The labels are ``R/S`` = recv try / send try (log argument order at
  ``0x1001da7b..0x1001da88``).
* Streaming mode: ``fillFifo`` sets ``setTimeout(FifoTimeout, FifoTimeout, 1, 1,
  MCSendInterval)`` before writing FIFO frames (``0x100524da..0x100524f5``,
  ``FifoTimeout`` = ``+0x1128`` loaded at ``0x100587d9`` with default 600) and restores the
  idle policy afterwards. The policy is HAL-wide, so any read issued by another thread
  in that window is also single-try -- which explains the verifier's observation that
  register reads are logged once with ``1/1`` while a job streams (08 §2.3, §4.4).
  INFERENCE (medium) for the "other threads" part.

The Linux port surfaces failures as typed exceptions that separate *card unreachable*
(no route), *card silent* (no reply within the ladder) and *card refusing* (exception
reply), as required by 08 §10 item 2.
"""

from __future__ import annotations

import errno
import select
import socket
import struct
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nexcut.mcc.framing import (
    EXCEPTION_BIT,
    FUNC_READ,
    FUNC_WRITE,
    MAX_FRAME_LEN,
    DecodeError,
    EncodeError,
    decode_reply_vector,
    encode_vector,
)

__all__ = [
    "DEFAULT_CARD_ADDR",
    "ERR_BAD_REPLY",
    "ERR_HOST_UNREACHABLE",
    "ERR_NET_UNREACHABLE",
    "ERR_NOT_SOCKET",
    "ERR_SELECT",
    "ERR_TIMEOUT",
    "CardBusy",
    "CardNotReady",
    "CardRefused",
    "CardSilent",
    "CardUnreachable",
    "LinkState",
    "LogEvent",
    "McError",
    "McTransaction",
    "ReplyDecodeFailed",
    "ReplySizeError",
    "RequestEncodeFailed",
    "RequestSuppressed",
    "RetryPolicy",
    "SelectFailed",
    "SocketError",
    "SocketInvalid",
    "StaleRepliesOnly",
    "UnexpectedReply",
    "error_for",
    "format_log_line",
    "hal_suppresses",
    "oserror_to_errcode",
]

DEFAULT_CARD_ADDR = ("10.1.1.168", 502)
"""``ipAdd.ini [IP] CardIP/CardPort`` (04 §1)."""

# --- ErrCode taxonomy (04 §3.3, 08 §2.1) --------------------------------------------------------

ERR_TIMEOUT = 10060
"""WSAETIMEDOUT: ``select()`` returned 0 (logged as ``RecvErr_selectFunc``)."""
ERR_HOST_UNREACHABLE = 10065
"""WSAEHOSTUNREACH from ``sendto`` (NIC not on 10.1.1.x, 08 §3.2)."""
ERR_NET_UNREACHABLE = 10051
"""WSAENETUNREACH; Linux returns ENETUNREACH when there is no route at all."""
ERR_NOT_SOCKET = 10038
"""WSAENOTSOCK: the socket was closed under the receiver (08 §2.1 template 5)."""
ERR_SELECT = 9999
"""``select() > 0`` but the socket is not in the set (``0x1001da58``)."""
ERR_BAD_REPLY = 603
"""Reply neither matches the request nor is its exception (``0x1001db16``)."""

_RETRY_WSA = frozenset({10052, 10053, 10054, 10060})
"""NETRESET/CONNABORTED/CONNRESET/TIMEDOUT: retried by sendto/recvfrom (``0x1001d85c``)."""

_ERRNO_TO_WSA: dict[int, int] = {
    errno.ETIMEDOUT: 10060,
    errno.EAGAIN: 10060,  # SO_SNDTIMEO/SO_RCVTIMEO expiry on Linux == WSAETIMEDOUT on Windows
    errno.EHOSTUNREACH: 10065,
    errno.ENETUNREACH: 10051,
    errno.EHOSTDOWN: 10064,
    errno.EBADF: 10038,
    errno.ENOTSOCK: 10038,
    errno.ENETRESET: 10052,
    errno.ECONNABORTED: 10053,
    errno.ECONNRESET: 10054,
    errno.ECONNREFUSED: 10061,
    errno.EMSGSIZE: 10040,
    errno.EADDRNOTAVAIL: 10049,
    errno.ENOBUFS: 10055,
    errno.EACCES: 10013,
    errno.EPERM: 10013,
}


def oserror_to_errcode(exc: OSError) -> int:
    """Map a Linux socket error to the Winsock ErrCode the Windows driver would log.

    Unmapped errnos return ``-errno`` so they can never collide with a real code.
    The table is the standard errno <-> WSAE* correspondence (WSAE* = 10000 + BSD errno).
    """
    if isinstance(exc, TimeoutError):
        return ERR_TIMEOUT
    if exc.errno is None:
        return -1
    return _ERRNO_TO_WSA.get(exc.errno, -exc.errno)


class LinkState(StrEnum):
    """What a failure tells the operator about the link (08 §10 item 2)."""

    UNREACHABLE = "unreachable"  # no route / NIC down: fix the PC network
    SILENT = "silent"  # datagrams leave, nothing valid comes back: card off/rebooting/cable
    REFUSING = "refusing"  # card answers with an exception
    PROTOCOL = "protocol"  # card answers with something we do not understand
    LOCAL = "local"  # our own socket or request is broken


# --- exceptions ---------------------------------------------------------------------------------


class McError(Exception):
    """A failed MC transaction; attributes mirror the Windows log line (08 §2.1)."""

    state: LinkState = LinkState.LOCAL

    def __init__(
        self,
        errcode: int,
        stage: str,
        *,
        recv_try: int = 0,
        send_try: int = 0,
        request: Sequence[int] = (),
        reply: Sequence[int] = (),
        buffer: bytes = b"",
        os_error: OSError | None = None,
    ) -> None:
        self.errcode = errcode
        self.stage = stage
        self.recv_try = recv_try
        self.send_try = send_try
        self.request = tuple(request)
        self.reply = tuple(reply)
        self.buffer = bytes(buffer)
        self.os_error = os_error
        super().__init__(
            f"MC-{stage} ErrCode:{errcode} Try-Times(R/S):{recv_try}/{send_try} "
            f"[{self.state}] request={' '.join(f'{w:x}' for w in self.request)}"
        )


class CardUnreachable(McError):
    """``sendto`` failed with no route (10065/10051/10064): PC network problem (08 §3.2)."""

    state = LinkState.UNREACHABLE


class CardSilent(McError):
    """The whole retry ladder elapsed without a valid reply (10060, 08 §2.3)."""

    state = LinkState.SILENT


class StaleRepliesOnly(CardSilent):
    """Only replies with a foreign sequence number arrived (ErrCode 2004, 04 §3.3)."""


class CardRefused(McError):
    """The card answered ``func|0x80, code``; ``errcode = 500 + code`` (04 §3.3)."""

    state = LinkState.REFUSING

    @property
    def exception_code(self) -> int:
        return self.errcode - 500


class CardNotReady(CardRefused):
    """Exception 2 (ErrCode 502): seen after reset/reboot on every block (04 §3.3, 08 §7).

    Modbus calls code 2 "illegal address"; the logs contradict that reading. INFERENCE medium.
    """


class CardBusy(CardRefused):
    """Exception 3 (ErrCode 503): jog/home rejected while moving (08 §4.5). INFERENCE medium."""


class UnexpectedReply(McError):
    """ErrCode 603: the reply neither echoes the request nor is its exception."""

    state = LinkState.PROTOCOL


class ReplyDecodeFailed(McError):
    """ErrCode 2000 + ``decodeWithSeq`` rc (2 short, 5/6 length inconsistent)."""

    state = LinkState.PROTOCOL


class ReplySizeError(McError):
    """A READ reply carried a different word count than requested.

    The DLL's callers check the vector size (e.g. ``0x54 = 3 + 18`` words, 04 §3.5);
    ``errcode`` is 0 because the DLL logs these with their own messages.
    """

    state = LinkState.PROTOCOL


class RequestEncodeFailed(McError):
    """ErrCode 1000 + ``encodeWithSeq`` rc (04 §3.3 step 1)."""


class RequestSuppressed(McError):
    """The vendor HAL would never put this request on the wire (04 §3.4, :func:`hal_suppresses`).

    ``errcode`` is 0: the vendor logs nothing and returns zeros (reads) or success (writes)
    without a transaction; the port refuses loudly instead (deviation, see module docstring).
    """


class SocketInvalid(McError):
    """ErrCode 10038: the socket was closed while in use (08 §2.1)."""


class SelectFailed(McError):
    """ErrCode 9999: ``select`` reported readiness without our socket in the set."""


class SocketError(McError):
    """Any other socket error code (not one of the documented ones)."""


_UNREACHABLE = frozenset({10065, 10051, 10064})


def error_for(errcode: int, stage: str, **ctx: Any) -> McError:
    """Build the typed exception for an ErrCode (04 §3.3, 08 §10)."""
    cls: type[McError]
    if errcode in _UNREACHABLE:
        cls = CardUnreachable
    elif errcode == ERR_TIMEOUT:
        cls = CardSilent
    elif errcode == 2004:
        cls = StaleRepliesOnly
    elif errcode == 502:
        cls = CardNotReady
    elif errcode == 503:
        cls = CardBusy
    elif 500 <= errcode < 600:
        cls = CardRefused
    elif errcode == ERR_BAD_REPLY:
        cls = UnexpectedReply
    elif 2000 < errcode < 3000:
        cls = ReplyDecodeFailed
    elif 1000 < errcode < 2000:
        cls = RequestEncodeFailed
    elif errcode == ERR_NOT_SOCKET:
        cls = SocketInvalid
    elif errcode == ERR_SELECT:
        cls = SelectFailed
    else:
        cls = SocketError
    return cls(errcode, stage, **ctx)


# --- HAL address windows (readReg 0x100225e0 / writeReg 0x10022760) ------------------------------

_FUNC_BYTE_BLOCK = 0x26


def hal_suppresses(func: int, addr: int) -> bool:
    """True if ``CMCHalAPI::readReg``/``writeReg`` would answer without a transaction (04 §3.4).

    EVIDENCE (signed ``cmp`` chains on the vector's address word):

    * func 0x26 always goes to the wire (``cmp [eax],0x26; je`` at ``0x100225ea`` / ``0x10022768``).
    * readReg: ``104 < a < 1000`` except 150/151 (returns failure, ``0x100225f7..0x1002263d``);
      ``1100 < a < 2000``, ``3000 < a < 5000``, ``5008 < a < 6000``, ``51000 < a < 59000``
      (return success with zeros, ``0x10022640..0x10022740``).
    * writeReg: ``104 < a < 5000`` except 150/151, ``5008 < a < 6000``, ``51000 < a < 59000``
      (return success, ``0x1002276d..0x100227ad``).

    ``func`` 0x30 selects the read table; every other function code is a write
    (``McTransaction.read`` uses readReg, ``write`` uses writeReg).
    """
    if func == _FUNC_BYTE_BLOCK:
        return False
    a = int(addr)
    if a in (150, 151):
        return False
    if func == FUNC_READ:
        return (
            104 < a < 1000
            or 1100 < a < 2000
            or 3000 < a < 5000
            or 5008 < a < 6000
            or 51000 < a < 59000
        )
    return 104 < a < 5000 or 5008 < a < 6000 or 51000 < a < 59000


# --- retry policy (setTimeout 0x10006570) -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Arguments of ``CMCHalAPI::setTimeout`` (04 §3.3; see module docstring)."""

    timeout_ms: int = 500
    """``select`` and ``SO_RCVTIMEO``/``SO_SNDTIMEO`` timeout (``MCTimeout``)."""
    send_times: int = 3
    """Datagrams per transaction (= ``MCFifoTime // MCTimeout`` in idle mode)."""
    recv_times: int = 3
    """Receive tries per send (``MCMaxRecvTime``); the first send only gets one on timeout."""
    send_interval_ms: int = 1
    """``Sleep`` before a resend (``MCSendInterval``, ini 1, loader default 10)."""

    def __post_init__(self) -> None:
        if self.timeout_ms <= 0 or self.send_times <= 0 or self.recv_times <= 0:
            raise ValueError("timeout, send_times and recv_times must be positive")
        if self.send_interval_ms < 0:
            raise ValueError("send_interval_ms must be >= 0")

    @classmethod
    def idle(
        cls,
        *,
        mc_timeout_ms: int = 500,
        mc_fifo_time_ms: int = 1600,
        mc_max_recv_time: int = 3,
        mc_send_interval_ms: int = 1,
    ) -> RetryPolicy:
        """Idle policy: ``setTimeout(MCTimeout, MCTimeout, -1, MCMaxRecvTime, MCSendInterval)``.

        ``-1`` -> ``send_times = MCFifoTime / MCTimeout`` (integer division, ``0x100065cb``).
        Defaults are the shipped ``ipAdd.ini`` values (04 §1). If the quotient is 0 the DLL
        *stores* 0 (``0x100065d3`` is reached unconditionally from the ``-1`` branch) and the
        transaction then returns success without sending anything (``edi = 1`` at
        ``0x1001d77e``, ``jle 0x1001db98`` at ``0x1001d7e9``). Deliberate deviation: this port
        uses 1 send instead of a silent fake success.
        """
        sends = mc_fifo_time_ms // mc_timeout_ms if mc_timeout_ms > 0 else 0
        return cls(mc_timeout_ms, max(1, sends), mc_max_recv_time, mc_send_interval_ms)

    @classmethod
    def streaming(cls, *, fifo_timeout_ms: int = 600, mc_send_interval_ms: int = 1) -> RetryPolicy:
        """Streaming policy: ``setTimeout(FifoTimeout, FifoTimeout, 1, 1, MCSendInterval)``.

        EVIDENCE ``fillFifo`` ``0x100524da..0x100524f5``; ini ``FifoTimeout=600``.
        """
        return cls(fifo_timeout_ms, 1, 1, mc_send_interval_ms)

    @property
    def worst_case_s(self) -> float:
        """Upper bound of a full silent ladder (select timeouts + resend sleeps)."""
        selects = 1 + (self.send_times - 1) * self.recv_times
        return (selects * self.timeout_ms + self.send_times * self.send_interval_ms) / 1000.0


# --- log events ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LogEvent:
    """One ``writeMCLog`` call (``0x1001d160``): the data of an ``MC-<Stage>`` log line."""

    stage: str
    errcode: int
    recv_try: int
    send_try: int
    data: tuple[int, ...] = ()
    buffer: bytes = b""
    dataex: tuple[int, ...] = ()
    monotonic: float = field(default_factory=time.monotonic)


def format_log_line(event: LogEvent, channel: str = "MC") -> str:
    """Render ``<ch>-<Stage> ErrCode:n Try-Times(R/S):r/s Data:.. Buffer:.. DataEx:..`` (08 §2.1).

    ``Data`` words are 8-digit hex, ``DataEx`` words hex without leading zeros, ``Buffer``
    one 2-digit hex token per byte -- the format ``nexcut.mcc.dissector`` parses.
    """
    data = " ".join(f"{w & 0xFFFFFFFF:08x}" for w in event.data)
    buf = " ".join(f"{b:02x}" for b in event.buffer)
    parts = [
        f"{channel}-{event.stage} ErrCode:{event.errcode} "
        f"Try-Times(R/S):{event.recv_try}/{event.send_try}",
        f"Data:{data}",
        f"Buffer:{buf}",
    ]
    if event.dataex:
        parts.append("DataEx:" + " ".join(f"{w & 0xFFFFFFFF:x}" for w in event.dataex))
    return " ".join(parts)


# --- the engine ---------------------------------------------------------------------------------


class _Resend(Exception):
    """Internal: leave the receive loop and take the Sleep+resend path (``0x1001db77``)."""


class McTransaction:
    """UDP transaction client for the MCC100 (04 §3.1, §3.3).

    Thread-safe: one transaction at a time, as the DLL's global critical section
    ``0x100b3b18``. Use :meth:`streaming` around FIFO fills to get the single-try policy.
    """

    def __init__(
        self,
        card_addr: tuple[str, int] = DEFAULT_CARD_ADDR,
        *,
        policy: RetryPolicy | None = None,
        streaming_policy: RetryPolicy | None = None,
        on_log: Callable[[LogEvent], None] | None = None,
        accept_foreign_source: bool = False,
    ) -> None:
        """Open the socket.

        ``accept_foreign_source=False`` is a deliberate deviation: the DLL ignores the
        ``recvfrom`` source address; this port treats datagrams from any other address
        like a stale reply (consumes a receive try).
        """
        self.card_addr = (socket.gethostbyname(card_addr[0]), int(card_addr[1]))
        self.policy = policy or RetryPolicy.idle()
        self.streaming_policy = streaming_policy or RetryPolicy.streaming()
        self.on_log = on_log
        self.accept_foreign_source = accept_foreign_source
        self._lock = threading.RLock()
        self._sock: socket.socket | None = None
        self._seq = 0
        self.open()

    # -- socket lifecycle --------------------------------------------------------------------

    def open(self) -> None:
        """Create the UDP socket and reset the sequence counter to 0 (04 §3.1, §3.2)."""
        with self._lock:
            self.close()
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self._sock = s
            self._seq = 0
            self._apply_timeouts(self.policy)

    def close(self) -> None:
        """Close the socket (a transaction in another thread then fails with 10038)."""
        s, self._sock = self._sock, None
        if s is not None:
            s.close()

    def __enter__(self) -> McTransaction:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _apply_timeouts(self, policy: RetryPolicy) -> None:
        if self._sock is None:
            return
        ms = policy.timeout_ms
        tv = struct.pack("@ll", ms // 1000, (ms % 1000) * 1000)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDTIMEO, tv)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, tv)

    @property
    def next_seq(self) -> int:
        """Sequence number the next transaction will use."""
        return self._seq

    def set_policy(self, policy: RetryPolicy) -> None:
        """``setTimeout``: HAL-wide policy for subsequent transactions."""
        with self._lock:
            self.policy = policy
            self._apply_timeouts(policy)

    @contextmanager
    def streaming(self) -> Iterator[None]:
        """Single-try policy for the duration of a FIFO fill, then restore (``0x100524da``)."""
        with self._lock:
            previous = self.policy
            self.set_policy(self.streaming_policy)
        try:
            yield
        finally:
            with self._lock:
                self.set_policy(previous)

    # -- public API ----------------------------------------------------------------------------

    def read(self, addr: int, count: int) -> list[int]:
        """READ ``count`` u32 registers from ``addr`` (func 0x30, 04 §3.2); returns the words."""
        out = self.transact([FUNC_READ, addr, count])
        words = out[3:]
        if len(out) < 3 or out[2] != count or len(words) != count:
            raise ReplySizeError(0, "ReadSize", request=[FUNC_READ, addr, count], reply=out)
        return words

    def write(self, addr: int, words: Sequence[int]) -> None:
        """WRITE u32 words to ``addr`` (func 0x40: command 0x65, FIFO 0x66/0x67, RW regs)."""
        self.transact([FUNC_WRITE, addr, len(words), *words])

    def transact(self, request: Sequence[int]) -> list[int]:
        """Run one transaction for a ``[func, addr, count, data...]`` vector.

        Returns the decoded reply vector (``decodeWithSeq`` form). Raises a
        :class:`McError` subclass on failure (see :func:`error_for`).
        """
        req = [int(w) for w in request]
        if len(req) >= 2 and hal_suppresses(req[0], req[1]):
            raise RequestSuppressed(0, "HalWindow", request=req)
        with self._lock:
            self._flush_one()
            policy = self.policy
            seq = self._seq
            self._seq = (seq + 1) & 0xFFFF
            try:
                buf = encode_vector(req, seq)
            except EncodeError as exc:
                code = exc.errcode
                self._log("Encode", code, 0, 0, dataex=req)
                raise error_for(code, "Encode", request=req) from exc
            return self._run(req, buf, seq, policy)

    # -- the loop of 0x1001d6a0 ----------------------------------------------------------------

    def _flush_one(self) -> None:
        """Discard at most one queued datagram, like ``0x10006280`` (no logging, errors ignored)."""
        sock = self._sock
        if sock is None:
            return
        try:
            ready, _, _ = select.select([sock], [], [], 0)
            if ready:
                sock.recv(100)
        except (OSError, ValueError):
            pass

    def _log(
        self,
        stage: str,
        code: int,
        r: int,
        s: int,
        *,
        data: Sequence[int] = (),
        buffer: bytes = b"",
        dataex: Sequence[int] = (),
    ) -> None:
        if self.on_log is not None:
            self.on_log(LogEvent(stage, code, r, s, tuple(data), bytes(buffer), tuple(dataex)))

    def _run(self, req: list[int], buf: bytes, seq: int, policy: RetryPolicy) -> list[int]:
        max_send, max_recv = policy.send_times, policy.recv_times
        timeout_s = policy.timeout_ms / 1000.0
        send_left = max_send
        last: McError | None = None
        while send_left > 0:
            send_try = max_send - send_left + 1
            sock = self._sock
            try:
                if sock is None:
                    raise OSError(errno.EBADF, "socket closed")
                sock.sendto(buf, self.card_addr)
            except OSError as exc:
                code = oserror_to_errcode(exc)
                # "Sendto" logs the request vector in Data (08 §2.1 template 4).
                self._log("Sendto", code, 0, send_try, data=req, buffer=buf)
                last = error_for(
                    code, "Sendto", send_try=send_try, request=req, buffer=buf, os_error=exc
                )
                if code in _RETRY_WSA:
                    send_left -= 1  # 0x1001d880: retry without Sleep
                    continue
                raise last from exc
            try:
                return self._receive(req, seq, sock, send_left, max_send, max_recv, timeout_s)
            except _Resend as rs:
                last = rs.args[0]
            send_left -= 1
            if policy.send_interval_ms:
                time.sleep(policy.send_interval_ms / 1000.0)
        assert last is not None
        raise last

    def _receive(
        self,
        req: list[int],
        seq: int,
        sock: socket.socket,
        send_left: int,
        max_send: int,
        max_recv: int,
        timeout_s: float,
    ) -> list[int]:
        send_try = max_send - send_left + 1
        recv_left = max_recv
        last: McError | None = None
        while recv_left > 0:
            recv_try = max_recv - recv_left + 1
            first_rung = recv_left == max_recv and send_left == max_send
            ctx: dict[str, Any] = {"recv_try": recv_try, "send_try": send_try, "request": req}
            # --- select ---
            try:
                ready, _, _ = select.select([sock], [], [], timeout_s)
                code = 0 if ready else ERR_TIMEOUT
                exc_os: OSError | None = None
            except (OSError, ValueError) as exc:  # ValueError: fd -1 after close
                exc_os = exc if isinstance(exc, OSError) else OSError(errno.EBADF, str(exc))
                code = oserror_to_errcode(exc_os)
            if code:
                self._log("RecvErr_selectFunc", code, recv_try, send_try, dataex=req)
                last = error_for(code, "RecvErr_selectFunc", os_error=exc_os, **ctx)
                if first_rung and code == ERR_TIMEOUT:
                    raise _Resend(last)  # 0x1001dab7..0x1001dad0
                recv_left -= 1
                continue
            # --- recvfrom ---
            try:
                data, source = sock.recvfrom(MAX_FRAME_LEN)
            except OSError as exc:
                code = oserror_to_errcode(exc)
                self._log("Recvfrom", code, recv_try, send_try, dataex=req)
                last = error_for(code, "Recvfrom", os_error=exc, **ctx)
                if first_rung and code == ERR_TIMEOUT:
                    raise _Resend(last) from exc  # 0x1001d968..0x1001d981
                if code in _RETRY_WSA:
                    recv_left -= 1
                    continue
                raise _Resend(last) from exc
            # --- decodeWithSeq ---
            if not self.accept_foreign_source and source[:2] != self.card_addr:
                rc = 4  # deviation: treated as a stale datagram
                self._log("DecodeRecvData", 2000 + rc, recv_try, send_try, buffer=data, dataex=req)
                last = error_for(2000 + rc, "DecodeRecvData", buffer=data, **ctx)
                recv_left -= 1
                continue
            try:
                out = decode_reply_vector(data, seq)
            except DecodeError as exc:
                code = exc.errcode
                self._log("DecodeRecvData", code, recv_try, send_try, buffer=data, dataex=req)
                last = error_for(code, "DecodeRecvData", buffer=data, **ctx)
                if exc.rc == 4:
                    recv_left -= 1  # stale sequence: receive again (0x1001da37)
                    continue
                raise last from exc
            # --- validate (0x1001dae4) ---
            if len(out) >= 2 and len(req) >= 2 and out[0] == req[0] and out[1] == req[1]:
                return out
            if out and out[0] == req[0] + EXCEPTION_BIT and len(out) >= 2:
                code = 500 + out[1]
            else:
                code = ERR_BAD_REPLY
            self._log("RecvDataErr", code, recv_try, send_try, data=out, buffer=data, dataex=req)
            raise error_for(code, "RecvDataErr", reply=out, buffer=data, **ctx)
        assert last is not None
        raise _Resend(last)
