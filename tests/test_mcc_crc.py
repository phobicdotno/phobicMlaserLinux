"""CRC-16/MODBUS golden tests (04 §3.2, 08 §2.2)."""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

import pytest

from nexcut.mcc.crc import CrcOrder, crc16_modbus, crc_bytes, crc_from_bytes

# The four distinct raw frames logged in Log/*.log (08 §2.2), copied verbatim.
LOGGED_FRAMES: list[tuple[str, int, CrcOrder]] = [
    # MC-Sendto ErrCode:10065 (PC request, READ 1000 x2), 5 lines
    ("00 00 8c 75 00 08 00 30 e8 03 00 00 02 00", 0x8C75, CrcOrder.HI_FIRST),
    # MC-RecvDataErr ErrCode:503 (card exception to 0x40, code 3), 567 lines
    ("1d 3f 34 45 00 03 00 c0 03", 0x4534, CrcOrder.LO_FIRST),
    # MC-RecvDataErr ErrCode:502 (card exception to 0x30, code 2), 34 lines
    ("06 c5 d0 45 00 03 00 b0 02", 0x45D0, CrcOrder.LO_FIRST),
    # MC-RecvDataErr ErrCode:502 (card exception to 0x40, code 2), 2 lines
    ("61 09 f5 85 00 03 00 c0 02", 0x85F5, CrcOrder.LO_FIRST),
]


def _bitwise(data: bytes) -> int:
    c = 0xFFFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return c


@pytest.mark.parametrize(("hexframe", "crc", "order"), LOGGED_FRAMES)
def test_logged_crcs(hexframe: str, crc: int, order: CrcOrder) -> None:
    frame = bytes.fromhex(hexframe)
    assert crc16_modbus(frame[4:]) == crc
    assert frame[2:4] == crc_bytes(crc, order)
    assert crc_from_bytes(frame[2:4], order) == crc
    other = CrcOrder.LO_FIRST if order is CrcOrder.HI_FIRST else CrcOrder.HI_FIRST
    assert crc_from_bytes(frame[2:4], other) != crc


def test_standard_check_value() -> None:
    # CRC-16/MODBUS catalogue check value for "123456789".
    assert crc16_modbus(b"123456789") == 0x4B37


def test_table_matches_bitwise() -> None:
    for n in (0, 1, 7, 64, 1200):
        data = os.urandom(n)
        assert crc16_modbus(data) == _bitwise(data)


def test_crc_bytes_orders() -> None:
    assert crc_bytes(0x8C75) == b"\x8c\x75"
    assert crc_bytes(0x8C75, CrcOrder.LO_FIRST) == b"\x75\x8c"
    with pytest.raises(ValueError):
        crc_from_bytes(b"\x00", CrcOrder.HI_FIRST)


def _pe_read(path: Path, va: int, size: int) -> bytes:
    d = path.read_bytes()
    pe = struct.unpack_from("<I", d, 0x3C)[0]
    nsec = struct.unpack_from("<H", d, pe + 6)[0]
    opt = struct.unpack_from("<H", d, pe + 20)[0]
    base = struct.unpack_from("<I", d, pe + 24 + 28)[0]
    rva = va - base
    for i in range(nsec):
        _name, vs, sva, rs, ro = struct.unpack_from("<8sIIII", d, pe + 24 + opt + i * 40)
        if sva <= rva < sva + max(vs, rs):
            return d[ro + rva - sva : ro + rva - sva + size]
    raise AssertionError(f"VA {va:#x} not mapped")


def test_dll_tables_match(src_dir: Path) -> None:
    """NCModule.dll CRC tables at 0x10089440/0x10089540 equal ours (crc.py docstring)."""
    dll = src_dir / "Module" / "NCModule.dll"
    hi = _pe_read(dll, 0x10089440, 256)
    lo = _pe_read(dll, 0x10089540, 256)
    expected_hi = bytes(_bitwise_table(i) & 0xFF for i in range(256))
    expected_lo = bytes(_bitwise_table(i) >> 8 for i in range(256))
    assert hi == expected_hi
    assert lo == expected_lo
    assert hashlib.sha256(hi + lo).hexdigest().startswith("dccb1cc4")


def _bitwise_table(i: int) -> int:
    c = i
    for _ in range(8):
        c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return c
