"""Dissector for MCC100 traffic: driver logs (``Log/*.log``) and pcap/pcapng captures.

Usage::

    python -m nexcut.mcc.dissector FILE [FILE ...] [--json] [--fifo] [--summary-only]

Inputs and the behaviour they come from:

* ``Log/YYYY-MM-DD.log`` written by ``CMCHalAPI::writeMCLog`` (``0x1001d160``):
  line grammar, the five message templates, the meaning of ``Data:``/``Buffer:``/
  ``DataEx:`` and the retry ladder ``1/1,1/2,2/2,3/2,1/3,2/3,3/3`` (08 §2.1-§2.3,
  04 §3.3). Only *failed* transactions are logged, so log transactions are never
  ``ok`` and carry no latency.
* classic pcap (µs and ns) and pcapng (SHB/IDB/EPB/SPB/PB, either byte order),
  link types Ethernet (+802.1Q), Linux SLL/SLL2, raw IPv4, BSD loopback; IPv4
  fragments are reassembled; UDP (``AccessType=1``) and TCP (``AccessType=0``)
  are both handled (04 §3.1). Requests are paired with replies by
  (PC endpoint, card endpoint, seq) (04 §3.2/§3.3); re-sent identical requests
  count as attempts of one transaction.
* FIFO frames (``WRITE 0x66``) are split into TLV items with
  ``header = (payload_bytes << 16) | opcode`` (08 §4.4, 11-static/A3 §5/§8).

Everything here is stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import statistics
import struct
import sys
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from nexcut.mcc.crc import CrcOrder
from nexcut.mcc.framing import (
    EXCEPTION_BIT,
    FUNC_BYTE_BLOCK,
    FUNC_NAMES,
    FUNC_READ,
    FUNC_WRITE,
    HEADER_LEN,
    REG_COMMAND,
    REG_FIFO_CONTROL,
    REG_FIFO_DATA,
    Direction,
    FramingError,
    Pdu,
    decode_pdu,
    encode_vector,
    exception_errcode,
    parse_raw_frame,
)

__all__ = [
    "CapturedPacket",
    "Datagram",
    "FifoFrame",
    "FifoItem",
    "FifoParseError",
    "LogRecord",
    "Transaction",
    "describe_vector",
    "dissect_capture",
    "dissect_file",
    "dissect_logs",
    "iter_capture",
    "iter_datagrams",
    "main",
    "parse_fifo_words",
    "parse_log_line",
    "parse_log_text",
    "request_kind",
    "summarize",
    "transactions_from_log",
    "write_pcap",
]

# ================================================================================================
# Descriptive tables (names are INFERENCE in the analysis docs; used for display only)
# ================================================================================================

READ_BLOCKS: dict[int, str] = {
    1000: "status RO",  # 04 §3.5 RORegName_*
    1050: "RO 1050",
    2000: "axis RO",
    5000: "RW",
    10000: "ZF status",
    11000: "ZF props",
    13000: "RO 13000",
    13200: "RO 13200",
    50000: "system RW",
    50200: "axis RW",
    60001: "param block",
}
"""Register block labels from 04 §3.5 (addresses EVIDENCE, names INFERENCE)."""

FIFO_CONTROL_VALUES: dict[int, str] = {1: "clear", 2: "start", 3: "stop"}
"""Register 0x67 values (04 §3.5 verifier: clearFifo/startFifo/stopFifo)."""

COMMAND_NAMES: dict[int, str] = {
    1: "home",  # 04 §3.6, 08 §4.5 [1, axisMask, 2, vSlow, vFast]
    3: "move-axis",  # [3, axis, v, a, j, target]
    4: "start-manu?",  # 04 §3.6 INFERENCE
    5: "move-multi",  # [5, mask, v, a, j, dX, dY, dZ, dW]
    7: "offline-upload?",
    0x66: "reset/resync?",  # 08 §3.3 [102]
    9999: "misc",
}
"""Command register 0x65 sub-commands (04 §3.6; meanings INFERENCE)."""

MISC_SUBCOMMANDS: dict[int, str] = {
    1: "fifo-clear(alt)",  # 04 §3.6
    2: "DO 1..10",  # A3 §6
    3: "PWM",
    4: "DA",
    13: "DO 11..26 / init",  # A3 §5 vs 04 §3.6 (conflicting readings)
    17: "PWM(5V)",
}
"""Sub-codes of the 9999 family, both on 0x65 and in the FIFO stream (A3 §5/§6, 04 §3.6)."""

FIFO_OPCODES: dict[int, str] = {
    3000: "tick",
    3001: "boundary",
    3002: "mode",
    9999: "misc",
    103: "zf-move",
    104: "zf-104",
    105: "zf-follow",
    106: "zf-section-drill",
    108: "zf-gradual-drill",
    109: "zf-move-neg",
    118: "zf-bookkeeping",
    2001: "wait/config",
    2002: "fibre-2002",
    2004: "fibre-2004",
    3: "fibre-3",
}
"""FIFO item opcodes (08 §4.4, 11-static/A3 §5; meanings INFERENCE except 3000 fields)."""


def _s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - 0x1_0000_0000 if v & 0x8000_0000 else v


# ================================================================================================
# FIFO TLV items (08 §4.4)
# ================================================================================================


class FifoParseError(ValueError):
    """The word list does not split into whole TLV items."""


@dataclass(frozen=True, slots=True)
class FifoItem:
    """One FIFO item: header ``(len(args)*4 << 16) | opcode`` + ``args`` (08 §4.4)."""

    opcode: int
    args: tuple[int, ...]
    offset: int
    """Index of the header word within the data words (after the frame id)."""

    @property
    def name(self) -> str:
        return FIFO_OPCODES.get(self.opcode, f"op{self.opcode}")

    @property
    def tick(self) -> tuple[int, int, int, int] | None:
        """``(dX, dY, freq, duty)`` for opcode 3000: X low int16, Y high int16 (A3 §5)."""
        if self.opcode != 3000 or len(self.args) != 2:
            return None
        w1, w2 = self.args

        def s16(v: int) -> int:
            return v - 0x10000 if v & 0x8000 else v

        return s16(w1 & 0xFFFF), s16((w1 >> 16) & 0xFFFF), (w2 >> 16) & 0xFFFF, w2 & 0xFFFF

    def describe(self) -> str:
        t = self.tick
        if t is not None:
            return f"tick(dX={t[0]},dY={t[1]},{t[2]}Hz/{t[3]}%)"
        return f"{self.opcode}[{', '.join(hex(a) if a > 9 else str(a) for a in self.args)}]"


@dataclass(frozen=True, slots=True)
class FifoFrame:
    """A ``WRITE 0x66`` payload: ``[frame_id, item words...]`` (04 §3.7, 08 §4.4)."""

    frame_id: int
    items: tuple[FifoItem, ...]
    remainder: tuple[int, ...] = ()
    """Words left over after the last whole item (empty on all 22 leaked frames)."""

    def census(self) -> Counter[int]:
        return Counter(i.opcode for i in self.items)

    def describe(self) -> str:
        c = self.census()
        parts = " ".join(f"{op}x{n}" for op, n in sorted(c.items(), key=lambda kv: -kv[1]))
        rem = f" REMAINDER {len(self.remainder)} words" if self.remainder else ""
        return (
            f"FIFO id={self.frame_id} ({self.frame_id:#x}) {len(self.items)} items [{parts}]{rem}"
        )


def parse_fifo_words(words: Sequence[int], *, strict: bool = True) -> FifoFrame:
    """Split the data words of a ``WRITE 0x66`` into TLV items (08 §4.4).

    ``words`` = everything after ``[0x40, 0x66, count]``, i.e. starting with the
    frame id. ``strict`` raises :class:`FifoParseError` on a remainder or an item
    length that is not a multiple of 4 bytes; otherwise the rest is returned in
    :attr:`FifoFrame.remainder`.
    """
    if not words:
        raise FifoParseError("empty FIFO payload (no frame id)")
    frame_id = words[0] & 0xFFFFFFFF
    data = [w & 0xFFFFFFFF for w in words[1:]]
    items: list[FifoItem] = []
    i = 0
    while i < len(data):
        header = data[i]
        nbytes, opcode = header >> 16, header & 0xFFFF
        nwords = nbytes // 4
        if nbytes % 4 or i + 1 + nwords > len(data):
            if strict:
                raise FifoParseError(
                    f"item at word {i} (header {header:#010x}) needs {nbytes} bytes, "
                    f"{4 * (len(data) - i - 1)} left"
                )
            return FifoFrame(frame_id, tuple(items), tuple(data[i:]))
        items.append(FifoItem(opcode, tuple(data[i + 1 : i + 1 + nwords]), i))
        i += 1 + nwords
    return FifoFrame(frame_id, tuple(items))


# ================================================================================================
# Vector descriptions
# ================================================================================================


def describe_vector(vector: Sequence[int]) -> str:
    """One-line human description of a DLL request vector (04 §3.5-§3.7)."""
    if not vector:
        return "(empty)"
    func = vector[0]
    if len(vector) < 3 or func not in FUNC_NAMES:
        if func & EXCEPTION_BIT and len(vector) == 2:
            return f"EXCEPTION {func & 0x7F:#04x} code {vector[1]}"
        return f"func {func:#04x} {' '.join(f'{w:x}' for w in vector[1:])}".rstrip()
    addr, count, data = vector[1], vector[2], list(vector[3:])
    if func == FUNC_READ:
        label = READ_BLOCKS.get(addr)
        return f"READ {addr} x{count}" + (f" ({label})" if label else "")
    if func == FUNC_WRITE:
        if addr == REG_FIFO_DATA:
            try:
                return parse_fifo_words(data, strict=False).describe()
            except FifoParseError as exc:
                return f"FIFO ({exc})"
        if addr == REG_FIFO_CONTROL and count == 1 and data:
            return f"FIFO-CTRL {FIFO_CONTROL_VALUES.get(data[0], data[0])}"
        if addr == REG_COMMAND and data:
            sub = data[0]
            name = COMMAND_NAMES.get(sub, f"sub{sub}")
            if sub == 9999 and len(data) > 1:
                name = f"misc/{MISC_SUBCOMMANDS.get(data[1], data[1])}"
            args = ", ".join(str(_s32(w)) for w in data[1:])
            return f"CMD {name} [{sub}{', ' if args else ''}{args}]"
        return f"WRITE {addr} x{count} [{', '.join(f'{w:#x}' for w in data[:8])}{'...' if count > 8 else ''}]"
    if func == FUNC_BYTE_BLOCK:
        return f"BYTEBLOCK {addr} x{count}"
    return f"{FUNC_NAMES[func]} {count} bytes"


def request_kind(vector: Sequence[int]) -> str:
    """Coarse request class for statistics (no per-call arguments)."""
    if len(vector) < 3 or vector[0] not in FUNC_NAMES:
        return f"func {vector[0]:#04x}" if vector else "(empty)"
    func, addr = vector[0], vector[1]
    if func == FUNC_READ:
        return f"READ {addr} x{vector[2]}"
    if func == FUNC_WRITE:
        if addr == REG_FIFO_DATA:
            return "FIFO"
        if addr == REG_FIFO_CONTROL and len(vector) > 3:
            return f"FIFO-CTRL {FIFO_CONTROL_VALUES.get(vector[3], vector[3])}"
        if addr == REG_COMMAND and len(vector) > 3:
            sub = vector[3]
            if sub == 9999 and len(vector) > 4:
                return f"CMD misc/{MISC_SUBCOMMANDS.get(vector[4], vector[4])}"
            return f"CMD {COMMAND_NAMES.get(sub, f'sub{sub}')}"
        return f"WRITE {addr}"
    if func == FUNC_BYTE_BLOCK:
        return f"BYTEBLOCK {addr}"
    return FUNC_NAMES[func]


# ================================================================================================
# Transactions
# ================================================================================================


@dataclass(slots=True)
class Transaction:
    """One request (with retries) and its reply or failure.

    ``status``: ``ok``, ``exception`` (``errcode = 500+code``), ``unexpected-reply``
    (603), ``timeout`` (10060), ``send-error``, ``socket-error``, ``decode-error``,
    ``orphan-reply`` (reply without a matching request) - 04 §3.3.
    """

    source: str
    t_start: datetime
    t_end: datetime
    status: str
    request_vector: tuple[int, ...] | None = None
    request: Pdu | None = None
    reply: Pdu | None = None
    seq: int | None = None
    errcode: int | None = None
    attempts: int = 1
    latency: float | None = None
    """Seconds from the last send of the request to the reply (captures only)."""
    rungs: tuple[tuple[int, int], ...] = ()
    """Log ``Try-Times(R/S)`` labels ``(recv, send)`` of the grouped lines (08 §2.3)."""
    lines: tuple[int, ...] = ()
    origin: str = ""
    """File name, plus endpoints for captures."""
    notes: list[str] = field(default_factory=list)

    @property
    def fifo(self) -> FifoFrame | None:
        v = self.request_vector
        if v and len(v) >= 4 and v[0] == FUNC_WRITE and v[1] == REG_FIFO_DATA:
            try:
                return parse_fifo_words(v[3:], strict=False)
            except FifoParseError:
                return None
        return None

    def describe(self) -> str:
        """One output line."""
        ts = self.t_start.isoformat(sep=" ", timespec="milliseconds")
        seq = f"{self.seq:04x}" if self.seq is not None else "----"
        what = describe_vector(self.request_vector) if self.request_vector else "?"
        status = self.status
        if self.errcode is not None:
            status += f"({self.errcode})"
        extra = []
        if self.attempts > 1:
            extra.append(f"x{self.attempts}")
        if self.rungs:
            extra.append("rungs " + ",".join(f"{r}/{s}" for r, s in self.rungs))
        if self.latency is not None:
            extra.append(f"{self.latency * 1000:.1f} ms")
        extra.extend(self.notes)
        tail = f"  [{'; '.join(extra)}]" if extra else ""
        return f"{ts}  seq={seq}  {status:<18} {what}{tail}"

    def to_dict(self) -> dict[str, object]:
        """JSON-serialisable form."""
        fifo = self.fifo
        return {
            "source": self.source,
            "origin": self.origin,
            "t_start": self.t_start.isoformat(),
            "t_end": self.t_end.isoformat(),
            "status": self.status,
            "errcode": self.errcode,
            "seq": self.seq,
            "attempts": self.attempts,
            "latency_s": self.latency,
            "request": describe_vector(self.request_vector) if self.request_vector else None,
            "request_vector": list(self.request_vector) if self.request_vector else None,
            "request_frame": self.request.frame.raw.hex() if self.request else None,
            "reply_vector": list(self.reply.vector) if self.reply and self.reply.vector else None,
            "reply_frame": self.reply.frame.raw.hex() if self.reply else None,
            "problems": [
                *(self.request.problems if self.request else ()),
                *(self.reply.problems if self.reply else ()),
            ],
            "rungs": [f"{r}/{s}" for r, s in self.rungs],
            "lines": list(self.lines),
            "fifo": None
            if fifo is None
            else {
                "frame_id": fifo.frame_id,
                "items": len(fifo.items),
                "remainder": len(fifo.remainder),
                "census": {str(k): v for k, v in sorted(fifo.census().items())},
            },
            "notes": list(self.notes),
        }


# ================================================================================================
# (a) Log/*.log
# ================================================================================================

_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) \[(?P<level>[^\]]*)\] -> (?P<msg>.*)$"
)
_MC_RE = re.compile(
    r"^(?P<chan>[A-Za-z0-9]+)-(?P<stage>\w+) ErrCode:(?P<err>-?\d+) "
    r"Try-Times\(R/S\):(?P<r>\d+)/(?P<s>\d+)(?P<rest>.*)$"
)
_FIELD_RE = re.compile(r"\b(DataEx|Data|Buffer):")

_STAGE_STATUS: dict[str, str] = {
    "RecvErr_selectFunc": "timeout",  # 04 §3.3 select() == 0 -> 10060
    "RecvDataErr": "exception",  # card exception, or 603 unexpected reply
    "Sendto": "send-error",
    "Recvfrom": "socket-error",
    "DecodeRecvData": "decode-error",
    "Encode": "encode-error",
}


@dataclass(frozen=True, slots=True)
class LogRecord:
    """One parsed line of ``Log/*.log`` (08 §2.1)."""

    timestamp: datetime
    level: str
    kind: str
    """``nc-start``, ``mc`` (a transaction failure line) or ``other``."""
    text: str
    origin: str = ""
    lineno: int = 0
    channel: str | None = None
    stage: str | None = None
    errcode: int | None = None
    recv_try: int | None = None
    send_try: int | None = None
    data: tuple[int, ...] | None = None
    buffer: bytes | None = None
    dataex: tuple[int, ...] | None = None

    @property
    def request_vector(self) -> tuple[int, ...] | None:
        """``DataEx`` if present; a ``Sendto`` line prints the request in ``Data`` (08 §2.1)."""
        if self.dataex:
            return self.dataex
        if self.stage in ("Sendto", "Encode") and self.data:
            return self.data
        return None

    @property
    def rung(self) -> tuple[int, int]:
        """Ladder position ordered by (send try, recv try) (08 §2.3)."""
        return (self.send_try or 0, self.recv_try or 0)


def _hex_words(text: str) -> tuple[int, ...] | None:
    toks = text.split()
    if not toks:
        return None
    return tuple(int(t, 16) for t in toks)


def parse_log_line(line: str, origin: str = "", lineno: int = 0) -> LogRecord | None:
    """Parse one log line; ``None`` for blank/unrecognised lines (08 §2.1)."""
    line = line.rstrip("\r\n")
    m = _LINE_RE.match(line)
    if not m:
        return None
    ts = datetime.strptime(m["ts"], "%Y-%m-%d %H:%M:%S.%f")
    msg = m["msg"].rstrip()
    if "NC Start" in msg:
        return LogRecord(ts, m["level"], "nc-start", msg, origin, lineno)
    mm = _MC_RE.match(msg)
    if not mm:
        return LogRecord(ts, m["level"], "other", msg, origin, lineno)
    fields: dict[str, str] = {}
    parts = _FIELD_RE.split(mm["rest"])
    for name, value in zip(parts[1::2], parts[2::2], strict=True):
        fields[name] = value.strip()
    try:
        data = _hex_words(fields.get("Data", ""))
        dataex = _hex_words(fields.get("DataEx", ""))
        buf_txt = fields.get("Buffer", "")
        buffer = bytes(int(t, 16) for t in buf_txt.split()) if buf_txt.split() else None
    except ValueError:
        return LogRecord(ts, m["level"], "other", msg, origin, lineno)
    return LogRecord(
        ts,
        m["level"],
        "mc",
        msg,
        origin,
        lineno,
        channel=mm["chan"],
        stage=mm["stage"],
        errcode=int(mm["err"]),
        recv_try=int(mm["r"]),
        send_try=int(mm["s"]),
        data=data,
        buffer=buffer,
        dataex=dataex,
    )


def parse_log_text(text: str, origin: str = "") -> list[LogRecord]:
    """Parse the whole content of a log file."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        rec = parse_log_line(line, origin, n)
        if rec is not None:
            out.append(rec)
    return out


LADDER_GAP_S = 2.0  # UNVERIFIED: grouping heuristic from 08 §2.3, not a driver constant
"""Lines of one ladder are < 2 s apart (08 §2.3 verifier grouping rule; rungs are ~0.5 s)."""


def transactions_from_log(
    records: Iterable[LogRecord],
    max_rung: tuple[int, int] = (3, 3),  # UNVERIFIED as a rule: last label seen in the logs
) -> list[Transaction]:
    """Group failure lines into transactions (08 §2.3, 04 §3.3).

    A line continues the open transaction when it has the same channel and
    request vector, follows within :data:`LADDER_GAP_S`, and its ``(send, recv)``
    rung is strictly later. Timeout lines never close a transaction; exception,
    send and socket errors do. ``max_rung`` = ``(MCMaxSendTime+1, MCMaxRecvTime)``
    of the shipped ``ipAdd.ini`` as observed in the logs (the last label of the
    138 full ladders is ``3/3``, 08 §2.3); a timeout ladder that stops earlier
    is flagged ``ladder incomplete`` because a later try may have succeeded
    silently.
    """
    out: list[Transaction] = []
    open_tx: Transaction | None = None
    open_last: LogRecord | None = None

    def close() -> None:
        nonlocal open_tx, open_last
        if open_tx is not None and open_tx.status == "timeout":
            if open_tx.rungs and (open_tx.rungs[-1][1], open_tx.rungs[-1][0]) != max_rung:
                open_tx.notes.append("ladder incomplete")
        open_tx, open_last = None, None

    for rec in records:
        if rec.kind != "mc":
            close()
            continue
        vec = rec.request_vector
        status = _STAGE_STATUS.get(rec.stage or "", f"error:{rec.stage}")
        joins = (
            open_tx is not None
            and open_last is not None
            and open_last.channel == rec.channel
            and open_tx.request_vector == vec
            and (rec.timestamp - open_last.timestamp).total_seconds() < LADDER_GAP_S
            and rec.rung > open_last.rung
        )
        if not joins:
            close()
            open_tx = Transaction(
                source="log",
                t_start=rec.timestamp,
                t_end=rec.timestamp,
                status=status,
                request_vector=vec,
                attempts=0,
                origin=rec.origin,
            )
            out.append(open_tx)
        assert open_tx is not None
        tx = open_tx
        tx.t_end = rec.timestamp
        tx.status = status
        tx.errcode = rec.errcode
        tx.rungs = (*tx.rungs, (rec.recv_try or 0, rec.send_try or 0))
        tx.lines = (*tx.lines, rec.lineno)
        tx.attempts = max(1, rec.send_try or 1)
        open_last = rec
        if rec.buffer:
            if rec.stage in ("Sendto", "Encode"):
                tx.request = decode_pdu(rec.buffer, Direction.REQUEST)
                tx.seq = tx.request.seq
                if vec and tx.request.vector is not None and tuple(tx.request.vector) != vec:
                    tx.notes.append("Buffer does not match Data vector")
            else:
                tx.reply = decode_pdu(rec.buffer, Direction.REPLY)
                tx.seq = tx.reply.seq
                if rec.data and tx.reply.vector is not None and tuple(tx.reply.vector) != rec.data:
                    tx.notes.append("Buffer does not match Data vector")
                if tx.reply.is_exception and tx.reply.exception_code is not None:
                    code_err = exception_errcode(tx.reply.exception_code)
                    if code_err != rec.errcode:
                        tx.notes.append(f"ErrCode {rec.errcode} != 500+code {code_err}")
                if vec and tx.seq is not None and tx.request is None:
                    # decodeWithSeq only accepts a reply whose seq equals the request's
                    # (04 §3.3), so the request frame can be rebuilt byte-exactly.
                    try:
                        tx.request = decode_pdu(encode_vector(vec, tx.seq), Direction.REQUEST)
                    except (FramingError, ValueError) as exc:
                        tx.notes.append(f"cannot rebuild request: {exc}")
        if status != "timeout":
            close()
    close()
    return out


def dissect_logs(paths: Sequence[Path]) -> tuple[list[LogRecord], list[Transaction]]:
    """Parse log files (in timestamp order) and group their transactions."""
    records: list[LogRecord] = []
    for p in paths:
        text = Path(p).read_bytes().decode("ascii", errors="replace")
        records.extend(parse_log_text(text, Path(p).name))
    records.sort(key=lambda r: r.timestamp)  # stable: keeps file/line order for equal stamps
    return records, transactions_from_log(records)


# ================================================================================================
# (b) pcap / pcapng
# ================================================================================================

LINKTYPE_NULL = 0
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_LOOP = 108
LINKTYPE_LINUX_SLL = 113
LINKTYPE_IPV4 = 228
LINKTYPE_LINUX_SLL2 = 276


@dataclass(frozen=True, slots=True)
class CapturedPacket:
    """One link-layer packet from a capture file."""

    ts: float
    linktype: int
    data: bytes
    orig_len: int
    index: int
    interface: int = 0


def _iter_pcap(buf: bytes) -> Iterator[CapturedPacket]:
    magic = buf[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        e = "<"
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        e = ">"
    else:
        raise ValueError("not a pcap file")
    nano = magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d")
    if len(buf) < 24:
        raise ValueError("truncated pcap header")
    (network,) = struct.unpack_from(e + "I", buf, 20)
    linktype = network & 0xFFFF
    off, idx = 24, 0
    div = 1e9 if nano else 1e6
    while off + 16 <= len(buf):
        sec, frac, incl, orig = struct.unpack_from(e + "IIII", buf, off)
        off += 16
        data = buf[off : off + incl]
        off += incl
        if len(data) < incl:
            break  # truncated trailing record
        yield CapturedPacket(sec + frac / div, linktype, data, orig, idx)
        idx += 1


def _iter_pcapng(buf: bytes) -> Iterator[CapturedPacket]:
    off, idx = 0, 0
    e = "<"
    ifaces: list[tuple[int, float]] = []  # (linktype, seconds per ts unit)
    while off + 12 <= len(buf):
        if buf[off : off + 4] == b"\x0a\x0d\x0d\x0a":
            bom = buf[off + 8 : off + 12]
            e = "<" if bom == b"\x4d\x3c\x2b\x1a" else ">"
            ifaces = []
        btype, blen = struct.unpack_from(e + "II", buf, off)
        if blen < 12 or off + blen > len(buf):
            break
        body = buf[off + 8 : off + blen - 4]
        if btype == 1 and len(body) >= 8:  # IDB
            linktype = struct.unpack_from(e + "H", body, 0)[0]
            res = 1e-6
            o = 8
            while o + 4 <= len(body):
                code, olen = struct.unpack_from(e + "HH", body, o)
                if code == 0:
                    break
                if code == 9 and olen >= 1:  # if_tsresol
                    v = body[o + 4]
                    res = 2.0 ** -(v & 0x7F) if v & 0x80 else 10.0**-v
                o += 4 + ((olen + 3) & ~3)
            ifaces.append((linktype, res))
        elif btype == 6 and len(body) >= 20:  # EPB
            iface, th, tl, cap, orig = struct.unpack_from(e + "IIIII", body, 0)
            lt, res = ifaces[iface] if iface < len(ifaces) else (LINKTYPE_ETHERNET, 1e-6)
            yield CapturedPacket(((th << 32) | tl) * res, lt, body[20 : 20 + cap], orig, idx, iface)
            idx += 1
        elif btype == 3 and len(body) >= 4:  # SPB: no timestamp
            (orig,) = struct.unpack_from(e + "I", body, 0)
            lt = ifaces[0][0] if ifaces else LINKTYPE_ETHERNET
            yield CapturedPacket(0.0, lt, body[4 : 4 + orig], orig, idx)
            idx += 1
        elif btype == 2 and len(body) >= 20:  # obsolete Packet Block
            iface, _drops, th, tl, cap, orig = struct.unpack_from(e + "HHIIII", body, 0)
            lt, res = ifaces[iface] if iface < len(ifaces) else (LINKTYPE_ETHERNET, 1e-6)
            yield CapturedPacket(((th << 32) | tl) * res, lt, body[20 : 20 + cap], orig, idx, iface)
            idx += 1
        off += blen


def iter_capture(path: Path | str) -> Iterator[CapturedPacket]:
    """Yield packets from a pcap or pcapng file (auto-detected by magic)."""
    buf = Path(path).read_bytes()
    if buf[:4] == b"\x0a\x0d\x0d\x0a":
        yield from _iter_pcapng(buf)
    else:
        yield from _iter_pcap(buf)


def write_pcap(
    path: Path | str,
    packets: Iterable[tuple[float, bytes]],
    linktype: int = LINKTYPE_ETHERNET,
) -> None:
    """Write a classic little-endian µs pcap (used by tests and the simulator)."""
    with open(path, "wb") as fh:
        fh.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, linktype))
        for ts, data in packets:
            sec = int(ts)
            usec = int(round((ts - sec) * 1e6))
            if usec >= 1_000_000:
                sec, usec = sec + 1, usec - 1_000_000
            fh.write(struct.pack("<IIII", sec, usec, len(data), len(data)))
            fh.write(data)


@dataclass(frozen=True, slots=True)
class Datagram:
    """A UDP datagram or TCP segment payload with its IPv4 endpoints."""

    ts: float
    proto: str
    src: str
    sport: int
    dst: str
    dport: int
    payload: bytes
    index: int
    tcp_seq: int | None = None
    tcp_flags: int = 0


def _link_to_ip(pkt: CapturedPacket) -> bytes | None:
    d, lt = pkt.data, pkt.linktype
    if lt == LINKTYPE_ETHERNET:
        if len(d) < 14:
            return None
        etype, off = struct.unpack_from(">H", d, 12)[0], 14
        while etype in (0x8100, 0x88A8) and len(d) >= off + 4:
            etype = struct.unpack_from(">H", d, off + 2)[0]
            off += 4
        return d[off:] if etype == 0x0800 else None
    if lt == LINKTYPE_LINUX_SLL:
        return d[16:] if len(d) >= 16 and struct.unpack_from(">H", d, 14)[0] == 0x0800 else None
    if lt == LINKTYPE_LINUX_SLL2:
        return d[20:] if len(d) >= 20 and struct.unpack_from(">H", d, 0)[0] == 0x0800 else None
    if lt in (LINKTYPE_RAW, LINKTYPE_IPV4, 12, 14):
        return d if d[:1] and d[0] >> 4 == 4 else None
    if lt in (LINKTYPE_NULL, LINKTYPE_LOOP):
        if len(d) < 4:
            return None
        fam_le, fam_be = struct.unpack_from("<I", d)[0], struct.unpack_from(">I", d)[0]
        return d[4:] if 2 in (fam_le, fam_be) else None
    return None


def iter_datagrams(packets: Iterable[CapturedPacket]) -> Iterator[Datagram]:
    """Decode IPv4 (reassembling fragments) and yield UDP/TCP payloads."""
    frags: dict[tuple[bytes, bytes, int, int], dict[int, tuple[bytes, bool]]] = {}
    for pkt in packets:
        ip = _link_to_ip(pkt)
        if ip is None or len(ip) < 20 or ip[0] >> 4 != 4:
            continue
        ihl = (ip[0] & 0x0F) * 4
        total = struct.unpack_from(">H", ip, 2)[0]
        ident = struct.unpack_from(">H", ip, 4)[0]
        ff = struct.unpack_from(">H", ip, 6)[0]
        proto = ip[9]
        src, dst = ip[12:16], ip[16:20]
        body = ip[ihl : total if total >= ihl else len(ip)]  # strip Ethernet padding
        more, frag_off = bool(ff & 0x2000), (ff & 0x1FFF) * 8
        if more or frag_off:
            key = (src, dst, ident, proto)
            parts = frags.setdefault(key, {})
            parts[frag_off] = (body, more)
            assembled = bytearray()
            done = False
            while True:
                piece = parts.get(len(assembled))
                if piece is None:
                    break
                assembled += piece[0]
                if not piece[1]:
                    done = True
                    break
                if not piece[0]:
                    break
            if not done:
                continue
            del frags[key]
            body = bytes(assembled)
        s, t = socket.inet_ntoa(src), socket.inet_ntoa(dst)
        if proto == 17 and len(body) >= 8:
            sport, dport, ulen = struct.unpack_from(">HHH", body, 0)
            end = ulen if 8 <= ulen <= len(body) else len(body)
            yield Datagram(pkt.ts, "udp", s, sport, t, dport, body[8:end], pkt.index)
        elif proto == 6 and len(body) >= 20:
            sport, dport, seqno = struct.unpack_from(">HHI", body, 0)
            doff = (body[12] >> 4) * 4
            flags = body[13]
            yield Datagram(pkt.ts, "tcp", s, sport, t, dport, body[doff:], pkt.index, seqno, flags)


def _looks_like_mbap(raw: bytes) -> bool:
    """Standard Modbus/TCP (EC3710, 04 §4.1): protocol id 0 and consistent MBAP length.

    UNVERIFIED heuristic (no EC3710 traffic exists); such frames are skipped.
    """
    if len(raw) < 8 or raw[2:4] != b"\x00\x00":
        return False
    if struct.unpack_from(">H", raw, 4)[0] != len(raw) - HEADER_LEN:
        return False
    return not parse_raw_frame(raw).crc_ok


class _TcpStream:
    """Minimal in-order TCP reassembly; frames are delimited by the length field.

    UNVERIFIED: that the TCP path (``AccessType=0``, 04 §3.1) uses the same
    framing with no extra delimiter - it shares ``encodeWithSeq`` but no TCP
    traffic or recv-loop analysis exists. Default-case 4-byte frames cannot be
    delimited this way.
    """

    def __init__(self) -> None:
        self.next_seq: int | None = None
        self.buf = bytearray()
        self.gap_pending = False

    def feed(self, seg: Datagram) -> list[bytes]:
        assert seg.tcp_seq is not None
        seq, data = seg.tcp_seq, seg.payload
        if seg.tcp_flags & 0x02:  # SYN
            self.next_seq = (seq + 1) & 0xFFFFFFFF
            self.buf.clear()
            return []
        if not data:
            return []
        if self.next_seq is None:
            self.next_seq = seq
        rel = (seq - self.next_seq) & 0xFFFFFFFF
        if rel >= 0x8000_0000:  # segment starts before next_seq: retransmission/overlap
            overlap = 0x1_0000_0000 - rel
            if overlap >= len(data):
                return []
            data = data[overlap:]
        elif rel:
            self.gap_pending = True
            self.buf.clear()
        self.buf += data
        self.next_seq = (seq + len(seg.payload)) & 0xFFFFFFFF
        frames = []
        while len(self.buf) >= HEADER_LEN:
            flen = HEADER_LEN + struct.unpack_from(">H", self.buf, 4)[0]
            if len(self.buf) < flen:
                break
            frames.append(bytes(self.buf[:flen]))
            del self.buf[:flen]
        return frames


def dissect_capture(
    packets: Iterable[CapturedPacket],
    card_ports: Sequence[int] = (502,),
    origin: str = "",
) -> list[Transaction]:
    """Decode card traffic from captured packets and pair requests with replies.

    Direction: destination port in ``card_ports`` = request (PC -> card), source
    port = reply; if both or neither match the CRC byte order decides (hi-first =
    PC, 04 §3.2). Pairing key: (PC ip:port, card ip:port, seq). Identical
    re-sends of a pending request are counted as ``attempts`` (04 §3.3: the
    retry loop re-sends the same buffer). A pending request whose seq is reused
    with different bytes, or that never gets a reply, becomes ``timeout``.
    Replies are validated like the driver: ``reply[0] == req[0]`` and
    ``reply[1] == req[1]`` is ok, ``reply[0] == req[0] | 0x80`` is an exception
    (``ErrCode = 500 + code``), anything else 603.
    """
    ports = set(card_ports)
    pending: dict[tuple[str, str, int], Transaction] = {}
    done: list[Transaction] = []
    streams: dict[tuple[str, int, str, int], _TcpStream] = {}
    last_send: dict[tuple[str, str, int], float] = {}

    def when(ts: float) -> datetime:
        return datetime.fromtimestamp(ts, UTC)

    for dg in iter_datagrams(packets):
        if dg.dport not in ports and dg.sport not in ports:
            continue
        if dg.proto == "tcp":
            st = streams.setdefault((dg.src, dg.sport, dg.dst, dg.dport), _TcpStream())
            frames = st.feed(dg)
        else:
            frames = [dg.payload] if dg.payload else []
        gap = dg.proto == "tcp" and bool(frames) and st.gap_pending
        if gap:
            st.gap_pending = False
        for raw in frames:
            if _looks_like_mbap(raw):
                continue
            # UNVERIFIED: card replies come from the card port (04 §3.1 shows sendto/recvfrom
            # on an unbound socket; the reply source port is not evidenced).
            if (dg.dport in ports) != (dg.sport in ports):
                direction = Direction.REQUEST if dg.dport in ports else Direction.REPLY
            else:
                rf = parse_raw_frame(raw)
                direction = (
                    Direction.REPLY if rf.crc_order is CrcOrder.LO_FIRST else Direction.REQUEST
                )
            pdu = decode_pdu(raw, direction)
            if direction is Direction.REQUEST:
                pc, card = f"{dg.src}:{dg.sport}", f"{dg.dst}:{dg.dport}"
            else:
                pc, card = f"{dg.dst}:{dg.dport}", f"{dg.src}:{dg.sport}"
            where = f"{origin} {pc}->{card}".strip()
            seq = pdu.seq if pdu.seq is not None else -1
            key = (pc, card, seq)
            if direction is Direction.REQUEST:
                tx = pending.get(key)
                if tx is not None and tx.request is not None and tx.request.frame.raw == raw:
                    tx.attempts += 1
                    tx.t_end = when(dg.ts)
                    last_send[key] = dg.ts
                    continue
                if tx is not None:
                    tx.status, tx.errcode = "timeout", 10060
                    tx.notes.append("seq reused before reply")
                    done.append(pending.pop(key))
                tx = Transaction(
                    source="pcap",
                    t_start=when(dg.ts),
                    t_end=when(dg.ts),
                    status="timeout",
                    request_vector=pdu.vector,
                    request=pdu,
                    seq=pdu.seq,
                    errcode=10060,
                    origin=where,
                )
                if gap:
                    tx.notes.append("tcp gap before this frame")
                last_send[key] = dg.ts
                pending[key] = tx
                continue
            tx = pending.pop(key, None)
            if tx is None:
                orphan = Transaction(
                    source="pcap",
                    t_start=when(dg.ts),
                    t_end=when(dg.ts),
                    status="orphan-reply",
                    reply=pdu,
                    seq=pdu.seq,
                    origin=where,
                )
                done.append(orphan)
                continue
            sent = last_send.pop(key)
            tx.reply = pdu
            tx.t_end = when(dg.ts)
            tx.latency = dg.ts - sent
            req, rep = tx.request_vector, pdu.vector
            if req and rep and len(rep) >= 2 and rep[0] == req[0] and rep[1] == req[1]:
                tx.status, tx.errcode = "ok", None
            elif req and rep and len(rep) >= 2 and rep[0] == (req[0] | EXCEPTION_BIT):
                tx.status, tx.errcode = "exception", exception_errcode(rep[1])
            elif rep is None:
                tx.status, tx.errcode = "decode-error", None
            else:
                tx.status, tx.errcode = "unexpected-reply", 603
            done.append(tx)
    done.extend(pending.values())
    done.sort(key=lambda t: t.t_start)
    return done


# ================================================================================================
# Summary + CLI
# ================================================================================================


def summarize(transactions: Sequence[Transaction]) -> dict[str, object]:
    """Counts by status/request kind, FIFO census and latency statistics."""
    by_status = Counter(t.status for t in transactions)
    by_kind: Counter[str] = Counter()
    fifo_ids: set[int] = set()
    census: Counter[int] = Counter()
    fifo_remainders = 0
    for t in transactions:
        v = t.request_vector
        if not v:
            by_kind["(no request)"] += 1
            continue
        by_kind[request_kind(v)] += 1
        f = t.fifo
        if f is not None and f.frame_id not in fifo_ids:
            fifo_ids.add(f.frame_id)
            census.update(f.census())
            fifo_remainders += bool(f.remainder)
    lat = [t.latency for t in transactions if t.latency is not None]
    return {
        "transactions": len(transactions),
        "by_status": dict(by_status.most_common()),
        "by_request": dict(by_kind.most_common()),
        "fifo_distinct_frames": len(fifo_ids),
        "fifo_frames_with_remainder": fifo_remainders,
        "fifo_opcode_census": {str(k): v for k, v in census.most_common()},
        "latency_ms": None
        if not lat
        else {
            "n": len(lat),
            "min": min(lat) * 1000,
            "median": statistics.median(lat) * 1000,
            "max": max(lat) * 1000,
        },
    }


def _is_capture(path: Path) -> bool:
    with open(path, "rb") as fh:
        magic = fh.read(4)
    return magic in (
        b"\x0a\x0d\x0d\x0a",
        b"\xd4\xc3\xb2\xa1",
        b"\xa1\xb2\xc3\xd4",
        b"\x4d\x3c\xb2\xa1",
        b"\xa1\xb2\x3c\x4d",
    )


def dissect_file(path: Path | str, card_ports: Sequence[int] = (502,)) -> list[Transaction]:
    """Dissect one file, choosing the capture or log parser by content."""
    p = Path(path)
    if _is_capture(p):
        return dissect_capture(iter_capture(p), card_ports, p.name)
    return dissect_logs([p])[1]


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: ``python -m nexcut.mcc.dissector FILE...``."""
    ap = argparse.ArgumentParser(
        prog="python -m nexcut.mcc.dissector",
        description="Decode MCC100 traffic from Mlaser Log/*.log files or pcap/pcapng captures.",
    )
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--port", type=int, action="append", help="card port(s), default 502")
    ap.add_argument("--json", action="store_true", help="one JSON object per transaction")
    ap.add_argument("--fifo", action="store_true", help="also list FIFO items")
    ap.add_argument("--status", action="append", help="only show these statuses")
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args(argv)
    ports = tuple(args.port or (502,))

    logs = [f for f in args.files if not _is_capture(f)]
    caps = [f for f in args.files if _is_capture(f)]
    txs: list[Transaction] = []
    events: list[LogRecord] = []
    if logs:
        records, ltx = dissect_logs(logs)
        events = [r for r in records if r.kind == "nc-start"]
        txs.extend(ltx)
    for c in caps:
        txs.extend(dissect_capture(iter_capture(c), ports, c.name))

    out = sys.stdout
    if not args.summary_only:
        shown = [t for t in txs if not args.status or t.status in args.status]
        rows: list[tuple[datetime, int, object]] = [(t.t_start, 1, t) for t in shown]
        if not args.json and not args.status:
            rows.extend((e.timestamp, 0, e) for e in events)
        rows.sort(key=lambda r: (r[0].replace(tzinfo=None), r[1]))
        for _, _, obj in rows:
            if isinstance(obj, LogRecord):
                out.write(
                    f"{obj.timestamp.isoformat(sep=' ', timespec='milliseconds')}  --- NC Start ---\n"
                )
                continue
            assert isinstance(obj, Transaction)
            if args.json:
                out.write(json.dumps(obj.to_dict()) + "\n")
                continue
            out.write(obj.describe() + "\n")
            f = obj.fifo
            if args.fifo and f is not None:
                for item in f.items:
                    out.write(f"    +{item.offset:03d} {item.name:<14} {item.describe()}\n")
    summary = summarize(txs)
    summary["nc_start_events"] = len(events)
    if args.json:
        out.write(json.dumps({"summary": summary}) + "\n")
    else:
        out.write("\n# summary\n")
        for k, v in summary.items():
            out.write(f"{k}: {v}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
