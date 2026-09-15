"""CRC-16/MODBUS as used by the MCC100 ``CExtModbus`` framing.

Behaviour sources:

* 04 §3.2 -- algorithm (poly 0x8005 reflected = 0xA001, init 0xFFFF, no final XOR),
  computed over frame bytes ``[4..end]`` (length field, unit, func, payload).
* 08 §2.2 -- the four logged frames whose CRCs are 0x8C75, 0x4534, 0x45D0, 0x85F5
  (brute-forced there; only this parameterisation matches).
* EVIDENCE (re-checked for this module): ``NCModule.dll`` holds the classic Modbus
  split lookup tables at VA ``0x10089440`` (256 bytes, low byte of the reflected
  table, "auchCRCHi" in the Modbus reference code) and ``0x10089540`` (high byte,
  "auchCRCLo"); both are byte-identical to :data:`_TABLE` below
  (sha256 of the 512 table bytes ``dccb1cc4…ac5a65ab``). The loop at
  ``0x10028759..0x100287a0`` in ``CExtModbus::encodeWithSeq`` is the reference
  ``idx = hi ^ byte; hi = lo ^ tblHi[idx]; lo = tblLo[idx]`` and stores
  ``buf[2] = lo_state`` / ``buf[3] = hi_state``, i.e. the numeric CRC value
  high byte first (see :func:`crc_bytes`).
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["CrcOrder", "crc16_modbus", "crc_bytes", "crc_from_bytes"]


def _build_table() -> tuple[int, ...]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
        table.append(c)
    return tuple(table)


_TABLE: tuple[int, ...] = _build_table()


class CrcOrder(StrEnum):
    """Byte order of the stored CRC in frame bytes [2], [3] (04 §3.2, 08 §2.2).

    ``HI_FIRST``: ``[2] = crc >> 8``; written by the PC (``encodeWithSeq``).
    ``LO_FIRST``: ``[2] = crc & 0xFF``; written by the card (standard RTU order),
    as seen in every logged reply frame.
    """

    HI_FIRST = "hi-first"
    LO_FIRST = "lo-first"


def crc16_modbus(data: bytes | bytearray | memoryview, init: int = 0xFFFF) -> int:
    """Return CRC-16/MODBUS of ``data`` (04 §3.2).

    >>> hex(crc16_modbus(bytes.fromhex("00080030e80300000200")))
    '0x8c75'
    """
    crc = init & 0xFFFF
    for b in bytes(data):
        crc = (crc >> 8) ^ _TABLE[(crc ^ b) & 0xFF]
    return crc


def crc_bytes(crc: int, order: CrcOrder = CrcOrder.HI_FIRST) -> bytes:
    """Serialise a CRC value for frame bytes [2..3] in the given order (04 §3.2)."""
    crc &= 0xFFFF
    if order is CrcOrder.HI_FIRST:
        return bytes((crc >> 8, crc & 0xFF))
    return bytes((crc & 0xFF, crc >> 8))


def crc_from_bytes(two: bytes, order: CrcOrder) -> int:
    """Inverse of :func:`crc_bytes`."""
    if len(two) != 2:
        raise ValueError("CRC field is exactly 2 bytes")
    if order is CrcOrder.HI_FIRST:
        return (two[0] << 8) | two[1]
    return (two[1] << 8) | two[0]
