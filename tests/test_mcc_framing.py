"""Frame encode/decode tests (04 §3.2/§3.3, 08 §2.2)."""

from __future__ import annotations

import struct

import pytest

from nexcut.mcc.crc import CrcOrder, crc16_modbus
from nexcut.mcc.framing import (
    FUNC_BYTE_BLOCK,
    FUNC_READ,
    FUNC_STREAM,
    FUNC_STREAM_ALT,
    FUNC_WRITE,
    MAX_FRAME_LEN,
    DecodeError,
    Direction,
    EncodeError,
    build_frame,
    byte_block_request,
    decode_pdu,
    decode_reply_vector,
    decode_request_vector,
    encode_reply_vector,
    encode_vector,
    exception_errcode,
    exception_reply,
    parse_raw_frame,
    read_reply,
    read_request,
    stream_request,
    write_reply,
    write_request,
)

SENDTO_FRAME = bytes.fromhex("0000 8c75 0008 00 30 e8030000 0200")  # 08 §2.2
EXC_503 = bytes.fromhex("1d3f 3445 0003 00 c0 03")  # 08 §2.2 / 04 §3.2
EXC_502_READ = bytes.fromhex("06c5 d045 0003 00 b0 02")  # 04 §3.2
EXC_502_WRITE = bytes.fromhex("6109 f585 0003 00 c0 02")  # Log/2025-07-09.log 16:39:44.330


def test_read_request_matches_logged_sendto() -> None:
    assert read_request(0, 1000, 2) == SENDTO_FRAME
    assert encode_vector([0x30, 0x3E8, 0x02], 0) == SENDTO_FRAME
    assert decode_request_vector(SENDTO_FRAME) == [0x30, 1000, 2]


@pytest.mark.parametrize(
    ("frame", "seq", "func", "code", "errcode"),
    [
        (EXC_503, 0x1D3F, 0x40, 3, 503),
        (EXC_502_READ, 0x06C5, 0x30, 2, 502),
        (EXC_502_WRITE, 0x6109, 0x40, 2, 502),
    ],
)
def test_exception_replies(frame: bytes, seq: int, func: int, code: int, errcode: int) -> None:
    assert exception_reply(seq, func, code) == frame
    assert decode_reply_vector(frame, seq) == [func | 0x80, code]
    assert exception_errcode(code) == errcode
    pdu = decode_pdu(frame, Direction.REPLY)
    assert pdu.problems == ()
    assert pdu.is_exception and pdu.exception_code == code
    assert pdu.frame.crc_order is CrcOrder.LO_FIRST


def test_decode_reply_errors() -> None:
    with pytest.raises(DecodeError) as e:
        decode_reply_vector(EXC_503, 0x1D40)
    assert e.value.rc == 4 and e.value.errcode == 2004
    with pytest.raises(DecodeError) as e:
        decode_reply_vector(EXC_503[:7])
    assert e.value.rc == 2


def test_command_write_logged_vector() -> None:
    # DataEx:40 65 06 03 01 c350 176f ea56 3d0900 answered by EXC_503 (seq 0x1d3f), 04 §3.2
    vec = [0x40, 0x65, 6, 3, 1, 0xC350, 0x176F, 0xEA56, 0x3D0900]
    frame = encode_vector(vec, 0x1D3F)
    assert frame[:2] == b"\x1d\x3f"
    assert struct.unpack_from(">H", frame, 4)[0] == 8 + 4 * 6 == len(frame) - 6
    assert frame[6:8] == b"\x00\x40"
    assert frame[8:14] == struct.pack("<IH", 0x65, 6)
    crc = crc16_modbus(frame[4:])
    assert frame[2:4] == bytes((crc >> 8, crc & 0xFF))  # PC order: hi first
    assert decode_request_vector(frame) == vec
    assert write_request(0x1D3F, 0x65, vec[3:]) == frame


def test_negative_words_are_u32() -> None:
    frame = write_request(1, 0x65, [3, 0, 200000, 5999, 59990, -4000000])
    assert decode_request_vector(frame)[-1] == (-4000000) & 0xFFFFFFFF


def test_fifo_frame_size() -> None:
    # 298 words = 1192 bytes; + u32 addr + u16 count + 8-byte header = 1206-byte datagram (04 §3.7)
    frame = write_request(9, 0x66, [0x279] + [0] * 297)
    assert len(frame) == 1206 <= MAX_FRAME_LEN
    assert struct.unpack_from(">H", frame, 4)[0] == 1200


def test_encode_errors() -> None:
    with pytest.raises(EncodeError) as e:
        encode_vector([0x30, 1000], 0)
    assert e.value.rc == 1 and e.value.errcode == 1001
    with pytest.raises(EncodeError) as e:
        encode_vector([0x40, 0x65, 2, 1], 0)
    assert e.value.rc == 2 and e.value.errcode == 1002


def test_default_func_emits_header_only() -> None:
    # jump-table default 0x10028759: [seq][00 00] only (04 §3.2)
    assert encode_vector([0x55, 1, 0], 0x0102) == b"\x01\x02\x00\x00"


def test_byte_block_and_streams() -> None:
    f = byte_block_request(3, 0x1234, b"\x01\x02\xff")
    assert struct.unpack_from(">H", f, 4)[0] == 3 + 3 + 5  # n + 5 with n = 6 vector words
    assert f[7] == FUNC_BYTE_BLOCK
    assert f[8:] == struct.pack("<IH", 0x1234, 3) + b"\x01\x02\xff"
    # only the low byte of each element is emitted
    assert encode_vector([0x26, 0x1234, 3, 0x101, 0x102, 0x1FF], 3) == f
    assert decode_request_vector(f) == [0x26, 0x1234, 3, 1, 2, 255]
    for func in (FUNC_STREAM, FUNC_STREAM_ALT):
        s = stream_request(4, b"abc", func)
        assert struct.unpack_from(">H", s, 4)[0] == 6 + 1  # n + 1
        assert s[7] == func and s[8:] == b"\x03\x00abc"  # no address on the wire
        assert decode_request_vector(s) == [func, 0, 3, *b"abc"]
    with pytest.raises(ValueError):
        stream_request(4, b"", 0x30)


def test_reply_roundtrips() -> None:
    r = read_reply(7, 1000, [1, 0xFFFFFFFF])
    assert r[2:4] == struct.pack("<H", crc16_modbus(r[4:]))  # card order lo-first
    assert decode_reply_vector(r, 7) == [FUNC_READ, 1000, 2, 1, 0xFFFFFFFF]
    with pytest.raises(DecodeError) as e:
        decode_reply_vector(r[:-1], 7)
    assert e.value.rc == 5
    w = write_reply(8, 0x66, 298)
    assert decode_reply_vector(w, 8) == [FUNC_WRITE, 0x66, 298]
    b = write_reply(8, 0x10, 5, func=FUNC_BYTE_BLOCK)
    assert decode_reply_vector(b) == [FUNC_BYTE_BLOCK, 0x10, 5]
    s = encode_reply_vector([FUNC_STREAM, 0, 2], 9, data=b"xy")
    assert decode_reply_vector(s) == [FUNC_STREAM, 0, 2]
    with pytest.raises(DecodeError) as e:
        decode_reply_vector(s + b"z")
    assert e.value.rc == 6
    # 0x21 replies fall into the decoder default: [func, byte@8]
    s21 = build_frame(9, FUNC_STREAM_ALT, b"\x05\x00", crc_order=CrcOrder.LO_FIRST)
    assert decode_reply_vector(s21) == [FUNC_STREAM_ALT, 5]


def test_decode_pdu_problems() -> None:
    bad = bytearray(SENDTO_FRAME)
    bad[3] ^= 1
    p = decode_pdu(bytes(bad), Direction.REQUEST)
    assert not p.frame.crc_ok and any("crc mismatch" in x for x in p.problems)
    swapped = decode_pdu(EXC_503, Direction.REQUEST)
    assert any("crc stored lo-first" in x for x in swapped.problems)
    long = decode_pdu(SENDTO_FRAME + b"\x00", Direction.REQUEST)
    assert any("length field" in x for x in long.problems)
    short = decode_pdu(b"\x00\x01", Direction.REPLY)
    assert short.vector is None and short.problems
    ok = decode_pdu(SENDTO_FRAME, Direction.REQUEST)
    assert ok.problems == () and ok.addr == 1000 and ok.count == 2 and ok.data == ()


def test_parse_raw_frame_fields() -> None:
    rf = parse_raw_frame(SENDTO_FRAME)
    assert (rf.seq, rf.length, rf.unit, rf.func) == (0, 8, 0, 0x30)
    assert rf.crc_value == 0x8C75 and rf.crc_order is CrcOrder.HI_FIRST and rf.length_ok


def test_build_frame_limits() -> None:
    with pytest.raises(ValueError):
        build_frame(0, 0x40, b"\x00" * MAX_FRAME_LEN)
