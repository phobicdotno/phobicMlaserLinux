"""MCC100 ``CExtModbus`` frame encoder/decoder (PC <-> card, UDP/502).

Wire layout (04 §3.2, verified against the logged frames in 08 §2.2)::

    offset size field
    0      2    seq      u16 BIG-endian (per-HAL counter; retries re-send the same buffer)
    2      2    crc      CRC-16/MODBUS over bytes [4..end]; PC stores it hi-first, card lo-first
    4      2    length   u16 BIG-endian = number of bytes after this field (unit + func + payload)
    6      1    unit     0x00
    7      1    func     0x20/0x21 stream, 0x26 byte block, 0x30 READ, 0x40 WRITE; func|0x80 = exception
    8      ...  payload  all integers LITTLE-endian

Two API levels are provided:

* the *vector* level mirrors the DLL exactly: the application builds ``vector<int>``
  ``[func, addr, count, data...]`` and ``CExtModbus::encodeWithSeq`` (``0x10028240``)
  serialises it; ``CExtModbus::decodeWithSeq`` (``0x10029b40``) turns a reply
  into ``[func, ...]``. This is also the form the driver prints after ``DataEx:``
  / ``Data:`` in ``Log/*.log`` (08 §2.1). See :func:`encode_vector`,
  :func:`decode_reply_vector`, :func:`decode_request_vector`,
  :func:`encode_reply_vector`.
* the *PDU* level (:class:`RawFrame`, :class:`Pdu`, :func:`decode_pdu`) is a
  tolerant parser for dissection: it never raises on malformed input, it records
  problems in :attr:`Pdu.problems` instead.

Evidence re-read for this module from ``NCModule.asm`` (objdump of
``Module/NCModule.dll``):

* ``encodeWithSeq`` jump table ``0x100287ac`` / index bytes ``0x100287c0``:
  func 0x20 and 0x21 -> case ``0x10028546``, 0x26 -> ``0x1002861e``, 0x30 ->
  ``0x100282d2``, 0x40 -> ``0x100283be``, everything else -> ``0x10028759``
  (only the 4-byte ``[seq][00 00]`` header is emitted, CRC loop skipped).
* length formulas (n = number of vector words): 0x30 and 0x40 ``4n-4``;
  0x26 ``n+5``; 0x20/0x21 ``n+1``. 0x20/0x21/0x26/0x40 check ``v[2] == n-3``
  and return 2 otherwise; ``n < 3`` returns 1 (``0x10028250``).
  0x26/0x20/0x21 emit only the low byte of each data element.
  0x20/0x21 do not emit ``v[1]`` (no address).
* ``decodeWithSeq`` jump table ``0x10029ffc`` / index ``0x1002a010``:
  0x20 -> ``0x10029f08`` (``[0x20, 0, u16@8]``, rc 6 unless ``count == size-10``),
  0x26 -> ``0x10029f5f`` (``[0x26, u32@8, u16@12]``),
  0x30 -> ``0x10029c35`` (``[0x30, u32@8, u16@12, count x u32@14+4i]``, rc 5 unless
  ``4*count == size-14``), 0x40 -> ``0x10029e18`` (``[0x40, u32@8, u16@12]``),
  **0x21 and all others** -> default ``0x10029fde`` (``[func, byte@8]``; this is how
  exception replies decode). rc 2 = frame shorter than 8 bytes, rc 4 = seq
  mismatch. The decoder never checks CRC, length field or unit byte.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from nexcut.mcc.crc import CrcOrder, crc16_modbus, crc_bytes, crc_from_bytes

__all__ = [
    "EXCEPTION_BIT",
    "FUNC_BYTE_BLOCK",
    "FUNC_NAMES",
    "FUNC_READ",
    "FUNC_STREAM",
    "FUNC_STREAM_ALT",
    "FUNC_WRITE",
    "HEADER_LEN",
    "MAX_FRAME_LEN",
    "MIN_FRAME_LEN",
    "REG_COMMAND",
    "REG_FIFO_CONTROL",
    "REG_FIFO_DATA",
    "DecodeError",
    "Direction",
    "EncodeError",
    "FramingError",
    "Pdu",
    "RawFrame",
    "build_frame",
    "byte_block_request",
    "decode_pdu",
    "decode_reply_vector",
    "decode_request_vector",
    "encode_reply_vector",
    "encode_vector",
    "exception_errcode",
    "exception_reply",
    "parse_raw_frame",
    "read_reply",
    "read_request",
    "stream_request",
    "write_reply",
    "write_request",
]

# --- constants (04 §3.2 function-code table) -------------------------------------------------

FUNC_STREAM = 0x20
"""Byte stream (download / offline upload paths), request ``u16 count, count x u8`` (04 §3.2)."""
FUNC_STREAM_ALT = 0x21
"""Second byte-stream code; encoded like 0x20, but its reply falls into the decoder default."""
FUNC_BYTE_BLOCK = 0x26
"""Byte-block write ``u32 addr, u16 count, count x u8`` (firmware/file download, 04 §3.2)."""
FUNC_READ = 0x30
"""READ registers ``u32 addr, u16 count`` -> ``u32 addr, u16 count, count x u32`` (04 §3.2)."""
FUNC_WRITE = 0x40
"""WRITE registers / command / FIFO data ``u32 addr, u16 count, count x u32`` (04 §3.2)."""
EXCEPTION_BIT = 0x80
"""Reply func ``req_func | 0x80`` + one exception byte; ``ErrCode = 500 + code`` (04 §3.3)."""

FUNC_NAMES: dict[int, str] = {
    FUNC_STREAM: "STREAM20",
    FUNC_STREAM_ALT: "STREAM21",
    FUNC_BYTE_BLOCK: "BYTEBLOCK",
    FUNC_READ: "READ",
    FUNC_WRITE: "WRITE",
}

REG_COMMAND = 0x65
"""Command register 101 (04 §3.6)."""
REG_FIFO_DATA = 0x66
"""FIFO data register 102 (04 §3.7, 08 §4.4)."""
REG_FIFO_CONTROL = 0x67
"""FIFO run control 103: 1 clear, 2 start, 3 stop (04 §3.5 verifier)."""

HEADER_LEN = 6
"""seq + crc + length; the length field counts the bytes after offset 6 (04 §3.2)."""
MIN_FRAME_LEN = 8
"""``decodeWithSeq`` rejects shorter frames with rc 2 (04 §3.3)."""
MAX_FRAME_LEN = 0x5A8
"""Receive buffer size passed to ``recvfrom`` = 1448 bytes (04 §3.2/§3.3)."""


class FramingError(ValueError):
    """Base class; ``rc`` is the DLL return code, ``errcode`` the logged ErrCode."""

    rc: int
    errcode: int

    def __init__(self, rc: int, message: str, errcode_base: int) -> None:
        super().__init__(f"rc={rc}: {message}")
        self.rc = rc
        self.errcode = errcode_base + rc


class EncodeError(FramingError):
    """``encodeWithSeq`` failure: rc 1 = fewer than 3 words, rc 2 = count mismatch.

    The transaction logs these as ``ErrCode = rc + 1000`` (04 §3.3).
    """

    def __init__(self, rc: int, message: str) -> None:
        super().__init__(rc, message, 1000)


class DecodeError(FramingError):
    """``decodeWithSeq`` failure: rc 2 short, 4 seq mismatch, 5/6 length inconsistent.

    Logged as ``ErrCode = rc + 2000`` (04 §3.3); rc 4 makes the driver receive again.
    """

    def __init__(self, rc: int, message: str) -> None:
        super().__init__(rc, message, 2000)


class Direction(StrEnum):
    """Which side sent a frame."""

    REQUEST = "request"  # PC -> card
    REPLY = "reply"  # card -> PC


def exception_errcode(code: int) -> int:
    """Map a card exception byte to the driver's ErrCode (04 §3.3: ``500 + code``)."""
    return 500 + code


# --- generic frame builder ---------------------------------------------------------------------


def build_frame(
    seq: int,
    func: int,
    payload: bytes = b"",
    *,
    unit: int = 0,
    crc_order: CrcOrder = CrcOrder.HI_FIRST,
) -> bytes:
    """Build ``[seq BE][crc][len BE][unit][func][payload]`` (04 §3.2).

    ``crc_order`` defaults to the PC's hi-first order; pass ``CrcOrder.LO_FIRST``
    to produce card-style replies (08 §2.2).
    """
    body = bytes((unit & 0xFF, func & 0xFF)) + bytes(payload)
    if len(body) > 0xFFFF:
        raise ValueError("frame body too long for the u16 length field")
    tail = struct.pack(">H", len(body)) + body
    crc = crc16_modbus(tail)
    frame = struct.pack(">H", seq & 0xFFFF) + crc_bytes(crc, crc_order) + tail
    if len(frame) > MAX_FRAME_LEN:
        # The DLL's buffer is 0x5A8; larger frames cannot be produced or received by it.
        raise ValueError(f"frame of {len(frame)} bytes exceeds MAX_FRAME_LEN={MAX_FRAME_LEN}")
    return frame


# --- vector level: PC side (encodeWithSeq / its inverse) --------------------------------------


def _u32(v: int) -> int:
    return v & 0xFFFFFFFF


def encode_vector(vector: Sequence[int], seq: int) -> bytes:
    """Serialise a request vector exactly like ``CExtModbus::encodeWithSeq`` (04 §3.2).

    ``vector`` is ``[func, addr, count, data...]`` as printed after ``DataEx:``.
    Raises :class:`EncodeError` with the DLL's return codes.
    """
    n = len(vector)
    if n < 3:
        raise EncodeError(1, "vector needs at least [func, addr, count]")
    seq_bytes = struct.pack(">H", seq & 0xFFFF)
    # The case is selected on the whole int32 word (``sub edi,0x20; cmp edi,0x20; ja
    # default`` at 0x100282b8..0x100282be), not on its low byte: 0x130 or -0xD0 take the
    # default case even though their low byte is 0x30.
    word0 = vector[0] & 0xFFFFFFFF
    if word0 not in FUNC_NAMES:
        # jump-table default 0x10028759: header only, CRC field left 0 (04 §3.2 table)
        return seq_bytes + b"\x00\x00"
    func = word0
    addr = _u32(vector[1])
    count = vector[2] & 0xFFFF
    data = vector[3:]
    if func == FUNC_READ:
        if n != 3:
            # The DLL would write len=4n-4 but only 8 body bytes (inconsistent frame); never used.
            raise ValueError("READ vector must be exactly [0x30, addr, count]")
        payload = struct.pack("<IH", addr, count)
    else:
        if vector[2] != n - 3:
            raise EncodeError(2, f"count word {vector[2]} != {n - 3} data elements")
        if func == FUNC_WRITE:
            payload = struct.pack("<IH", addr, count) + struct.pack(
                f"<{len(data)}I", *(_u32(w) for w in data)
            )
        elif func == FUNC_BYTE_BLOCK:
            payload = struct.pack("<IH", addr, count) + bytes(w & 0xFF for w in data)
        else:  # 0x20 / 0x21: no address emitted
            payload = struct.pack("<H", count) + bytes(w & 0xFF for w in data)
    return build_frame(seq, func, payload, crc_order=CrcOrder.HI_FIRST)


def decode_request_vector(frame: bytes) -> list[int]:
    """Inverse of :func:`encode_vector`: PC request frame -> ``[func, addr, count, data...]``.

    For 0x20/0x21 the address word is not on the wire and is returned as 0.
    Raises :class:`DecodeError` (rc 2 short, rc 5 length inconsistent) — these rc
    values are this module's own, the DLL never decodes requests.
    """
    if len(frame) < MIN_FRAME_LEN:
        raise DecodeError(2, f"request frame of {len(frame)} bytes is shorter than 8")
    func = frame[7]
    p = frame[8:]
    if func in (FUNC_READ, FUNC_WRITE, FUNC_BYTE_BLOCK):
        if len(p) < 6:
            raise DecodeError(5, "payload shorter than addr+count")
        addr, count = struct.unpack_from("<IH", p)
        rest = p[6:]
        if func == FUNC_READ:
            if rest:
                raise DecodeError(5, "READ request carries trailing bytes")
            return [func, addr, count]
        if func == FUNC_WRITE:
            if len(rest) != 4 * count:
                raise DecodeError(5, f"WRITE count {count} but {len(rest)} data bytes")
            return [func, addr, count, *struct.unpack(f"<{count}I", rest)]
        if len(rest) != count:
            raise DecodeError(5, f"BYTEBLOCK count {count} but {len(rest)} data bytes")
        return [func, addr, count, *rest]
    if func in (FUNC_STREAM, FUNC_STREAM_ALT):
        if len(p) < 2:
            raise DecodeError(5, "payload shorter than count")
        (count,) = struct.unpack_from("<H", p)
        rest = p[2:]
        if len(rest) != count:
            raise DecodeError(5, f"STREAM count {count} but {len(rest)} data bytes")
        return [func, 0, count, *rest]
    return [func, *p]


# --- vector level: card side (decodeWithSeq / its inverse) ------------------------------------


def decode_reply_vector(frame: bytes, seq: int | None = None) -> list[int]:
    """Decode a card reply exactly like ``CExtModbus::decodeWithSeq`` (04 §3.2/§3.3).

    ``seq=None`` skips the sequence check. CRC, length field and unit byte are
    ignored, as in the DLL.
    """
    if len(frame) < MIN_FRAME_LEN:
        raise DecodeError(2, f"reply frame of {len(frame)} bytes is shorter than 8")
    got_seq = (frame[0] << 8) | frame[1]
    if seq is not None and got_seq != (seq & 0xFFFF):
        raise DecodeError(4, f"seq mismatch: got {got_seq:#06x}, want {seq & 0xFFFF:#06x}")
    func = frame[7]
    size = len(frame)

    def need(n: int) -> None:
        # The DLL reads past the datagram in these cases; a Python port must not.
        if size < n:
            raise DecodeError(2, f"func {func:#04x} reply needs {n} bytes, got {size}")

    if func == FUNC_READ:
        need(14)
        addr, count = struct.unpack_from("<IH", frame, 8)
        if 4 * count != size - 14:
            raise DecodeError(5, f"READ reply count {count} but {size - 14} data bytes")
        return [func, addr, count, *struct.unpack_from(f"<{count}I", frame, 14)]
    if func in (FUNC_WRITE, FUNC_BYTE_BLOCK):
        need(14)
        addr, count = struct.unpack_from("<IH", frame, 8)
        return [func, addr, count]
    if func == FUNC_STREAM:
        need(10)
        (count,) = struct.unpack_from("<H", frame, 8)
        if count != size - 10:
            raise DecodeError(6, f"STREAM reply count {count} but {size - 10} data bytes")
        return [func, 0, count]
    need(9)
    return [func, frame[8]]


def encode_reply_vector(
    vector: Sequence[int],
    seq: int,
    *,
    data: bytes = b"",
    crc_order: CrcOrder = CrcOrder.LO_FIRST,
) -> bytes:
    """Build a card reply that :func:`decode_reply_vector` maps back to ``vector``.

    Intended for the simulator and tests. Layouts follow the decoder (04 §3.2);
    only exception replies have been observed on the wire (08 §2.2), so the
    success layouts are UNVERIFIED (derived from the PC decoder, not from card
    traffic). ``data`` is the byte payload of a 0x20 reply (the DLL does not
    return it in the vector).
    """
    if not vector:
        raise ValueError("empty reply vector")
    func = vector[0] & 0xFF
    if func == FUNC_READ:
        addr, count, *words = vector[1:]
        if count != len(words):
            raise ValueError("READ reply count must equal the number of words")
        payload = struct.pack(f"<IH{count}I", _u32(addr), count, *(_u32(w) for w in words))
    elif func in (FUNC_WRITE, FUNC_BYTE_BLOCK):
        addr, count = vector[1:3]
        payload = struct.pack("<IH", _u32(addr), count & 0xFFFF)
    elif func == FUNC_STREAM:
        count = vector[2] if len(vector) > 2 else len(data)
        if count != len(data):
            raise ValueError("STREAM reply count must equal len(data)")
        payload = struct.pack("<H", count) + bytes(data)
    else:
        if len(vector) != 2:
            raise ValueError("default/exception reply vector is [func, byte]")
        payload = bytes((vector[1] & 0xFF,))
    return build_frame(seq, func, payload, crc_order=crc_order)


# --- typed convenience builders ----------------------------------------------------------------


def read_request(seq: int, addr: int, count: int) -> bytes:
    """``READ`` request frame (func 0x30, 04 §3.2)."""
    return encode_vector([FUNC_READ, addr, count], seq)


def write_request(seq: int, addr: int, words: Sequence[int]) -> bytes:
    """``WRITE`` request frame (func 0x40): command 0x65, FIFO 0x66/0x67, RW registers."""
    return encode_vector([FUNC_WRITE, addr, len(words), *words], seq)


def byte_block_request(seq: int, addr: int, data: bytes) -> bytes:
    """Byte-block write (func 0x26, ``fillDownFile``; 04 §3.2, §5)."""
    return encode_vector([FUNC_BYTE_BLOCK, addr, len(data), *data], seq)


def stream_request(seq: int, data: bytes, func: int = FUNC_STREAM) -> bytes:
    """Byte stream (func 0x20 or 0x21, no address; 04 §3.2)."""
    if func not in (FUNC_STREAM, FUNC_STREAM_ALT):
        raise ValueError("stream func must be 0x20 or 0x21")
    return encode_vector([func, 0, len(data), *data], seq)


def read_reply(seq: int, addr: int, words: Sequence[int]) -> bytes:
    """Card reply to READ (UNVERIFIED layout, see :func:`encode_reply_vector`)."""
    return encode_reply_vector([FUNC_READ, addr, len(words), *words], seq)


def write_reply(seq: int, addr: int, count: int, func: int = FUNC_WRITE) -> bytes:
    """Card echo reply to WRITE/BYTEBLOCK (UNVERIFIED layout)."""
    return encode_reply_vector([func, addr, count], seq)


def exception_reply(seq: int, func: int, code: int) -> bytes:
    """Card exception reply ``[func|0x80][code]``, CRC lo-first (EVIDENCE 08 §2.2)."""
    return encode_reply_vector([(func | EXCEPTION_BIT) & 0xFF, code], seq)


# --- tolerant PDU level (dissection) ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawFrame:
    """Header-level view of one datagram/frame; never raises on malformed data."""

    raw: bytes
    seq: int | None
    crc_field: bytes
    length: int | None
    unit: int | None
    func: int | None
    payload: bytes
    crc_value: int | None
    """CRC-16/MODBUS computed over ``raw[4:]`` (04 §3.2)."""
    crc_order: CrcOrder | None
    """Order in which the stored CRC matches; ``None`` if it matches neither."""

    @property
    def crc_ok(self) -> bool:
        """True if the stored CRC matches in either byte order."""
        return self.crc_order is not None

    @property
    def length_ok(self) -> bool:
        """True if the length field equals the bytes following it."""
        return self.length is not None and self.length == len(self.raw) - HEADER_LEN


def parse_raw_frame(raw: bytes, prefer: CrcOrder = CrcOrder.HI_FIRST) -> RawFrame:
    """Split ``raw`` into header fields and check CRC in both orders (04 §3.2, 08 §2.2).

    If both orders match (``crc_hi == crc_lo``) ``prefer`` is reported.
    """
    raw = bytes(raw)
    seq = (raw[0] << 8) | raw[1] if len(raw) >= 2 else None
    crc_field = raw[2:4]
    length = (raw[4] << 8) | raw[5] if len(raw) >= 6 else None
    unit = raw[6] if len(raw) >= 7 else None
    func = raw[7] if len(raw) >= 8 else None
    crc_value: int | None = None
    order: CrcOrder | None = None
    if len(raw) >= 4:
        crc_value = crc16_modbus(raw[4:])
        other = CrcOrder.LO_FIRST if prefer is CrcOrder.HI_FIRST else CrcOrder.HI_FIRST
        for o in (prefer, other):
            if crc_from_bytes(crc_field, o) == crc_value:
                order = o
                break
    return RawFrame(raw, seq, crc_field, length, unit, func, raw[8:], crc_value, order)


@dataclass(frozen=True, slots=True)
class Pdu:
    """A decoded request or reply (dissector view)."""

    direction: Direction
    frame: RawFrame
    vector: tuple[int, ...] | None
    """DLL-style vector (``DataEx`` form for requests, ``Data`` form for replies)."""
    problems: tuple[str, ...] = field(default=())

    @property
    def seq(self) -> int | None:
        return self.frame.seq

    @property
    def func(self) -> int | None:
        return self.frame.func

    @property
    def is_exception(self) -> bool:
        """Reply whose func has bit 7 set (04 §3.2)."""
        return (
            self.direction is Direction.REPLY
            and self.frame.func is not None
            and bool(self.frame.func & EXCEPTION_BIT)
        )

    @property
    def exception_code(self) -> int | None:
        if not self.is_exception or len(self.frame.payload) < 1:
            return None
        return self.frame.payload[0]

    @property
    def addr(self) -> int | None:
        v = self.vector
        if v is None or len(v) < 3 or v[0] not in (FUNC_READ, FUNC_WRITE, FUNC_BYTE_BLOCK):
            return None
        return v[1]

    @property
    def count(self) -> int | None:
        v = self.vector
        if v is None or len(v) < 3 or v[0] not in FUNC_NAMES:
            return None
        return v[2]

    @property
    def data(self) -> tuple[int, ...]:
        """Data elements after ``[func, addr, count]`` (words or bytes)."""
        v = self.vector
        if v is None or len(v) < 3 or v[0] not in FUNC_NAMES:
            return ()
        return v[3:]


def decode_pdu(raw: bytes, direction: Direction) -> Pdu:
    """Tolerantly decode ``raw`` sent in ``direction`` (04 §3.2).

    Problems (bad CRC, wrong CRC order for the direction, length-field mismatch,
    non-zero unit, undecodable payload) are collected, not raised.
    """
    prefer = CrcOrder.HI_FIRST if direction is Direction.REQUEST else CrcOrder.LO_FIRST
    frame = parse_raw_frame(raw, prefer)
    problems: list[str] = []
    if len(frame.raw) < MIN_FRAME_LEN:
        problems.append(f"short frame ({len(frame.raw)} bytes)")
        return Pdu(direction, frame, None, tuple(problems))
    if frame.crc_order is None:
        problems.append(
            f"crc mismatch (stored {frame.crc_field.hex()}, computed {frame.crc_value:04x})"
        )
    elif frame.crc_order is not prefer:
        problems.append(f"crc stored {frame.crc_order} (expected {prefer} for a {direction})")
    if not frame.length_ok:
        problems.append(f"length field {frame.length} != {len(frame.raw) - HEADER_LEN}")
    if frame.unit != 0:
        problems.append(f"unit {frame.unit} != 0")
    vector: tuple[int, ...] | None
    try:
        if direction is Direction.REQUEST:
            vector = tuple(decode_request_vector(frame.raw))
        else:
            vector = tuple(decode_reply_vector(frame.raw))
    except FramingError as exc:
        problems.append(f"payload: {exc}")
        vector = None
    return Pdu(direction, frame, vector, tuple(problems))
