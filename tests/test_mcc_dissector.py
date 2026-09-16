"""Dissector tests: log lines, ladder grouping, FIFO TLV, pcap/pcapng, CLI (08 §2-4, 04 §3)."""

from __future__ import annotations

import json
import struct
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from nexcut.mcc.crc import CrcOrder
from nexcut.mcc.dissector import (
    COMMAND_NAMES,
    MISC_SUBCOMMANDS,
    READ_BLOCKS,
    FifoParseError,
    describe_vector,
    dissect_capture,
    dissect_file,
    dissect_logs,
    iter_capture,
    parse_fifo_words,
    parse_log_line,
    parse_log_text,
    request_kind,
    summarize,
    transactions_from_log,
    write_pcap,
)
from nexcut.mcc.framing import (
    Direction,
    exception_reply,
    read_reply,
    read_request,
    write_reply,
    write_request,
)

DATA = Path(__file__).parent / "data" / "mcc"
FIFO_FILE = DATA / "fifo_frames_2025-07.txt"

# --- verbatim log lines (CRLF as in the files) --------------------------------------------------
L_START = "2025-07-17 14:51:52.628 [Info] -> ---------------------------------NC Start-------------------------------------\r\n"
L_SENDTO = "2025-07-07 08:59:02.710 [Info] -> MC-Sendto ErrCode:10065 Try-Times(R/S):0/1 Data:00000030 000003e8 00000002  Buffer:00 00 8c 75 00 08 00 30 e8 03 00 00 02 00 \r\n"
L_503 = "2025-06-25 18:18:46.603 [Info] -> MC-RecvDataErr ErrCode:503 Try-Times(R/S):1/1 Data:000000c0 00000003  Buffer:1d 3f 34 45 00 03 00 c0 03  DataEx:40 65 06 03 01 c350 176f ea56 3d0900 \r\n"
L_502W = "2025-07-09 16:39:44.330 [Info] -> MC-RecvDataErr ErrCode:502 Try-Times(R/S):1/1 Data:000000c0 00000002  Buffer:61 09 f5 85 00 03 00 c0 02  DataEx:40 65 01 66 \r\n"
L_RECVFROM = "2025-07-09 15:27:13.103 [Info] -> MC-Recvfrom ErrCode:10038 Try-Times(R/S):2/3 Buffer: DataEx:30 c350 1a \r\n"
LADDER = """\
2025-07-17 14:51:45.912 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):1/1 Data: Buffer: DataEx:30 2710 12
2025-07-17 14:51:52.628 [Info] -> ---------------------------------NC Start-------------------------------------
2025-07-17 14:51:56.410 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):1/1 Data: Buffer: DataEx:30 3e8 02
2025-07-17 14:51:56.925 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):1/2 Data: Buffer: DataEx:30 3e8 02
2025-07-17 14:51:57.428 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):2/2 Data: Buffer: DataEx:30 3e8 02
2025-07-17 14:51:57.928 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):3/2 Data: Buffer: DataEx:30 3e8 02
2025-07-17 14:51:58.431 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):1/3 Data: Buffer: DataEx:30 3e8 02
2025-07-17 14:51:58.932 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):2/3 Data: Buffer: DataEx:30 3e8 02
2025-07-17 14:51:59.432 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):3/3 Data: Buffer: DataEx:30 3e8 02
2025-07-17 14:52:02.932 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):1/1 Data: Buffer: DataEx:30 3e8 02
2025-07-17 14:52:03.434 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):1/2 Data: Buffer: DataEx:30 3e8 02
2025-07-12 16:16:46.012 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):1/1 Data: Buffer: DataEx:30 c350 1a
2025-07-12 16:16:46.515 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):1/2 Data: Buffer: DataEx:30 c350 1a
2025-07-12 16:16:47.016 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):2/2 Data: Buffer: DataEx:30 c350 1a
2025-07-12 16:16:47.516 [Info] -> MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):3/2 Data: Buffer: DataEx:30 c350 1a
2025-07-12 16:16:47.520 [Info] -> MC-RecvDataErr ErrCode:502 Try-Times(R/S):1/3 Data:000000b0 00000002  Buffer:91 98 d0 45 00 03 00 b0 02  DataEx:30 c350 1a
2025-07-12 16:16:47.521 [Info] -> MC-RecvDataErr ErrCode:502 Try-Times(R/S):1/1 Data:000000b0 00000002  Buffer:91 99 d0 45 00 03 00 b0 02  DataEx:30 ea61 78
"""


# ================================================================================================
# log lines
# ================================================================================================


def test_parse_nc_start() -> None:
    rec = parse_log_line(L_START)
    assert rec is not None and rec.kind == "nc-start"
    assert rec.timestamp.isoformat() == "2025-07-17T14:51:52.628000"


def test_parse_sendto_line() -> None:
    rec = parse_log_line(L_SENDTO)
    assert rec is not None and rec.kind == "mc"
    assert (rec.channel, rec.stage, rec.errcode, rec.recv_try, rec.send_try) == (
        "MC",
        "Sendto",
        10065,
        0,
        1,
    )
    assert rec.data == (0x30, 1000, 2) and rec.dataex is None
    assert rec.request_vector == (0x30, 1000, 2)
    assert rec.buffer == bytes.fromhex("00008c75000800 30e80300000200".replace(" ", ""))
    (tx,) = transactions_from_log([rec])
    assert tx.status == "send-error" and tx.errcode == 10065 and tx.seq == 0
    assert tx.request is not None and tx.request.problems == ()
    assert tx.request.frame.crc_order is CrcOrder.HI_FIRST
    assert tx.notes == []


def test_exception_line_rebuilds_request_frame() -> None:
    rec = parse_log_line(L_503)
    assert rec is not None
    assert rec.data == (0xC0, 3)
    assert rec.dataex == (0x40, 0x65, 6, 3, 1, 50000, 5999, 59990, 4000000)
    (tx,) = transactions_from_log([rec])
    assert tx.status == "exception" and tx.errcode == 503 and tx.seq == 0x1D3F
    assert tx.reply is not None and tx.reply.exception_code == 3
    assert tx.reply.frame.crc_order is CrcOrder.LO_FIRST
    # decodeWithSeq accepts only the request's seq, so the request is recoverable byte-exactly
    assert tx.request is not None and tx.request.problems == ()
    assert tx.request.frame.raw == write_request(0x1D3F, 0x65, [3, 1, 50000, 5999, 59990, 4000000])
    # 11 §8 correction 1: sub-command 3 is a relative JOG, not "move-axis" (04 naming)
    assert "CMD jog" in tx.describe()
    assert "axis Y distance +4000.000 mm (relative)" in tx.describe()
    assert tx.notes == []


def test_502_resync_and_recvfrom() -> None:
    (tx,) = transactions_from_log([parse_log_line(L_502W)])  # type: ignore[list-item]
    assert tx.errcode == 502 and tx.request_vector == (0x40, 0x65, 1, 0x66)
    assert request_kind(tx.request_vector) == "CMD zf-resync?"  # 11 §3.3 [102], A1 §1 slot 59
    rec = parse_log_line(L_RECVFROM)
    assert rec is not None and rec.buffer is None and rec.data is None
    (tx2,) = transactions_from_log([rec])
    assert tx2.status == "socket-error" and tx2.errcode == 10038 and tx2.rungs == ((2, 3),)


# ---- 0x65 label table against 11 §2 / §3.3 / §5.2 -----------------------------------------------


def _cmd(words: list[int]) -> str:
    return describe_vector([0x40, 0x65, len(words), *[w & 0xFFFFFFFF for w in words]])


def test_command_labels_match_the_vector_table_of_11_section_2() -> None:
    """Every 0x65 sub-command the findings name has the right label (11 §2, §8 correction 1).

    Before this fix the table was the 04-era one: 1 was "home" and 3 "move-axis", so a
    capture of 60 stops read as 60 homings (STATUS §1.1 "known defect").
    """
    assert COMMAND_NAMES[1] == "stop"  # V3/V4/V5 [1, mask, 2, vd, 10*vd]
    assert COMMAND_NAMES[2] == "home"  # V6 [2, 1<<slot, 0]
    assert COMMAND_NAMES[3] == "jog"  # V1/V2 relative, bit 31 absolute
    assert COMMAND_NAMES[5] == "goto"  # V8 go-to point
    assert COMMAND_NAMES[101] == "zf-stop"  # V14
    assert COMMAND_NAMES[9999] == "misc"
    # No label may still claim a motion meaning that 11 §8 corrected away.
    assert "home" not in _cmd([1, 0x1F, 2, 2000, 20000])
    assert "move-axis" not in _cmd([3, 0, 200000, 5999, 59990, 4000000])
    # Deny-listed sub-commands (11 §3.3) are named, and their guesses end in "?".
    for sub in (4, 7, 102, 107, 117, 118, 103, 104, 109):
        assert sub in COMMAND_NAMES, sub
    assert all(COMMAND_NAMES[sub].endswith("?") for sub in (4, 7, 102, 107, 117))
    # The 9999 family of 11 §2 V11-V13 / §5.2, including the connect prologue V0.
    assert MISC_SUBCOMMANDS[2] == "DO 1..10"
    assert MISC_SUBCOMMANDS[4] == "DA"
    assert MISC_SUBCOMMANDS[5] == "connect-prologue"
    assert MISC_SUBCOMMANDS[13] == "DO 11..26"
    assert MISC_SUBCOMMANDS[0x11] == "PWM(5V)"
    # Register blocks: everything 11 §3.2 allows, and the deny-listed ones flagged (§3.3).
    for addr in (1000, 1050, 2000, 5000, 10000, 11000, 50000, 50200, 60001):
        assert addr in READ_BLOCKS, addr
    for addr in (150, 151, 59500):
        assert READ_BLOCKS[addr].endswith("- DENY"), addr
    assert "READ 1000 x36 (status RO)" == describe_vector([0x30, 1000, 36])
    assert "(card licence - DENY)" in describe_vector([0x30, 59500, 12])


def test_command_descriptions_carry_the_vector_meaning() -> None:
    """The human line says what the vector does, in words (11 §2 columns 2-3)."""
    # V1 continuous jog X-, V2 step, V8 absolute go-to (11 §7 step 4: bit 31 = absolute).
    assert _cmd([3, 0, 200000, 5999, 59990, -4000000]) == (
        "CMD jog [3, 0x0, 200000, 5999, 59990, -4000000]  axis X distance -4000.000 mm "
        "(relative), v 200.000 mm/s, a 5999 mm/s^2, jerk 59990"
    )
    assert "axis Y target +100.000 mm" in _cmd([3, 0x80000001, 20000, 5999, 59990, 100000])
    # V4 stop-all and V5 two-word stop; V6 home one axis versus V7 system home.
    assert "stop axes X|Y|Y2|Z|W, decel 2000" in _cmd([1, 0x1F, 2, 2000, 20000])
    assert "card default decel" in _cmd([1, 0x1F])
    assert "home axes X" in _cmd([2, 1, 0])
    assert "system home axes X|Y" in _cmd([2, 3, 0])
    # V11 DO write: DO9 is the CO2 laser enable on this machine (11 §2 V11).
    assert "set DO9=1" in _cmd([9999, 2, 0x100, 0x100])
    assert "set DO1=0" in _cmd([9999, 2, 1, 0])
    assert "set DO11=1" in _cmd([9999, 13, 1, 1])
    assert "DA channel 2 = 5000 mV" in _cmd([9999, 4, 1, 5000])
    assert "PWM 5000 Hz, duty 4 %" in _cmd([9999, 0x11, 5000, 4])
    assert "connect prologue" in _cmd([9999, 5, 0, 0])
    # ZF vectors outside the FIFO are deny-listed but must still read correctly (11 §3.3).
    assert "ZF v 100.0 mm/s, height +0.000 mm" in _cmd([103, 1000, 0])
    # An unknown sub-command never invents a meaning.
    assert _cmd([4242, 1]) == "CMD sub4242 [4242, 1]"


def test_ladder_grouping() -> None:
    """08 §2.3: 1/1,1/2,2/2,3/2,1/3,2/3,3/3 at ~0.5 s is one transaction."""
    txs = transactions_from_log(parse_log_text(LADDER, "ladder.log"))
    assert [(t.request_vector, len(t.rungs), t.status) for t in txs] == [
        ((0x30, 10000, 18), 1, "timeout"),
        ((0x30, 1000, 2), 7, "timeout"),
        ((0x30, 1000, 2), 2, "timeout"),  # caller retried 3.5 s later
        ((0x30, 50000, 26), 5, "exception"),  # 502 arrives during the ladder (1/3)
        ((0x30, 60001, 120), 1, "exception"),
    ]
    full = txs[1]
    assert full.rungs == ((1, 1), (1, 2), (2, 2), (3, 2), (1, 3), (2, 3), (3, 3))
    assert full.attempts == 3 and "ladder incomplete" not in full.notes
    assert "ladder incomplete" in txs[2].notes
    assert txs[3].seq == 0x9198 and txs[4].seq == 0x9199  # consecutive seqs
    assert txs[3].request is not None and txs[3].request.frame.raw == read_request(
        0x9198, 50000, 26
    )


def test_escalated_fifo_retry_splits() -> None:
    """08 §4.4: 1/1 then 1/1,1/2,2/2,3/2 for the same FIFO frame = two transactions."""
    fifo = "40 66 4 3c 80bb8 1 13880004"
    text = "".join(
        f"2025-07-17 15:04:{t} [Info] -> MC-RecvErr_selectFunc ErrCode:10060 "
        f"Try-Times(R/S):{r} Data: Buffer: DataEx:{fifo} \r\n"
        for t, r in [
            ("19.931", "1/1"),
            ("20.442", "1/1"),
            ("20.944", "1/2"),
            ("21.445", "2/2"),
            ("21.946", "3/2"),
        ]
    )
    txs = transactions_from_log(parse_log_text(text))
    assert [len(t.rungs) for t in txs] == [1, 4]
    f = txs[0].fifo
    assert f is not None and f.frame_id == 0x3C and f.items[0].tick == (1, 0, 5000, 4)


# ================================================================================================
# FIFO TLV (08 §4.4, A3 §8/§9)
# ================================================================================================

# frame id -> (items, opcode census), computed from the 22 leaked frames
GOLDEN_FRAMES: dict[int, tuple[int, dict[int, int]]] = {
    1931: (99, {3000: 99}),
    504: (100, {103: 1, 2001: 1, 3000: 93, 3001: 2, 3002: 1, 9999: 2}),
    523: (99, {3000: 99}),
    633: (100, {109: 1, 118: 1, 2001: 1, 3000: 93, 3001: 2, 3002: 1, 9999: 1}),
    643: (99, {3000: 99}),
    654: (99, {3000: 99}),
    725: (99, {3000: 99}),
    739: (99, {3000: 99}),
    80: (99, {3000: 99}),
    87: (99, {3000: 99}),
    57: (100, {103: 1, 2001: 1, 3000: 93, 3001: 2, 3002: 1, 9999: 2}),
    115: (99, {3000: 99}),
    212: (99, {3000: 99}),
    297: (99, {3000: 99}),
    132: (99, {3000: 99}),
    141: (99, {3000: 99}),
    58: (99, {3000: 99}),
    60: (99, {3000: 99}),
    67: (99, {3000: 99}),
    69: (99, {3000: 99}),
    1191: (99, {3000: 99}),
    1199: (99, {3000: 99}),
}
# 08 §4.4 / A3 §8 opcode census over the 22 distinct frames
GOLDEN_CENSUS = {3000: 2160, 3001: 6, 9999: 5, 3002: 3, 2001: 3, 103: 2, 109: 1, 118: 1}


@pytest.fixture(scope="module")
def leaked_frames() -> dict[int, tuple[int, ...]]:
    txs = transactions_from_log(parse_log_text(FIFO_FILE.read_text(), FIFO_FILE.name))
    out = {}
    for t in txs:
        assert t.request_vector is not None
        out[t.request_vector[3]] = t.request_vector
    return out


def test_22_leaked_frames_zero_remainder(leaked_frames: dict[int, tuple[int, ...]]) -> None:
    assert len(leaked_frames) == 22
    census: Counter[int] = Counter()
    for fid, vec in leaked_frames.items():
        assert vec[:3] == (0x40, 0x66, 0x12A) and len(vec) == 3 + 0x12A
        frame = parse_fifo_words(vec[3:], strict=True)  # raises on any remainder
        assert frame.frame_id == fid and frame.remainder == ()
        assert sum(1 + len(i.args) for i in frame.items) == 297
        assert (len(frame.items), dict(frame.census())) == GOLDEN_FRAMES[fid]
        census.update(frame.census())
        # every 3000 tick carries exactly two args (08 §4.4)
        assert all(len(i.args) == 2 for i in frame.items if i.opcode == 3000)
        # re-encode: 1206-byte datagram (04 §3.7)
        assert len(write_request(0, 0x66, vec[3:])) == 1206
    assert dict(census) == GOLDEN_CENSUS


def test_frame_633_matches_A3_reconstruction(leaked_frames: dict[int, tuple[int, ...]]) -> None:
    def tick(dx: int, dy: int, freq: int, duty: int) -> tuple[int, tuple[int, ...]]:
        return 3000, (((dy & 0xFFFF) << 16) | (dx & 0xFFFF), (freq << 16) | duty)

    seq = (
        [tick(0, 0, 5000, 4)] * 38
        + [tick(-1, 0, 5000, 4)]
        + [tick(0, 0, 5000, 4)] * 16
        + [
            (3001, ()),
            (9999, (2, 0x100, 0)),
            (109, (1000, 0)),
            (2001, (0x03000002, 20000)),
            (118, (4, 0, 35)),
            (3001, ()),
            (3002, (4,)),
        ]
        + [tick(0, 0, 5000, 0)] * 21
        + [tick(1, 0, 5000, 0)]
        + [tick(0, 0, 5000, 0)] * 5
        + [tick(1, 0, 5000, 0)]
        + [tick(0, 0, 5000, 0)] * 3
        + [tick(1, 0, 5000, 0)]
        + [tick(0, 0, 5000, 0)] * 2
        + [tick(1, 0, 5000, 0)]
        + [tick(0, 0, 5000, 0)] * 2
        + [tick(1, 0, 5000, 0)]
    )
    frame = parse_fifo_words(leaked_frames[633][3:])
    assert [(i.opcode, i.args) for i in frame.items] == seq


def test_frame_504_prologue(leaked_frames: dict[int, tuple[int, ...]]) -> None:
    """08 §4.4 'start of a contour': ... markers, DO, 103, 2001, DO, 13 pierce ticks at 4 %."""
    items = parse_fifo_words(leaked_frames[504][3:]).items
    tail = [(i.opcode, i.args) for i in items[-21:]]
    assert tail[:8] == [
        (3001, ()),
        (3002, (5,)),
        (3000, (0, 0x13880000)),
        (3001, ()),
        (9999, (2, 4, 4)),
        (103, (1000, 0)),
        (2001, (0x03000002, 20000)),
        (9999, (2, 0x100, 0x100)),
    ]
    assert all(i.tick == (0, 0, 5000, 4) for i in items[-13:])
    assert all(i.tick == (0, 0, 5000, 0) for i in items[-37:-21])


def test_fifo_parse_errors() -> None:
    with pytest.raises(FifoParseError):
        parse_fifo_words([])
    with pytest.raises(FifoParseError):
        parse_fifo_words([1, 0x00080BB8, 0])  # header wants 2 args, 1 left
    loose = parse_fifo_words([1, 0x00000BB9, 0x00080BB8, 0], strict=False)
    assert [i.opcode for i in loose.items] == [3001] and loose.remainder == (0x00080BB8, 0)
    with pytest.raises(FifoParseError):
        parse_fifo_words([1, 0x00030001, 0, 0])  # 3-byte payload is not whole words
    t = parse_fifo_words([7, 0x00080BB8, 0x0001FFFF, 0x07D00064]).items[0]
    assert t.tick == (-1, 1, 2000, 100) and t.name == "tick"


# ================================================================================================
# pcap / pcapng
# ================================================================================================

PC, CARD = "10.1.1.10", "10.1.1.168"


def _ip_checksum(h: bytes) -> int:
    s = sum(struct.unpack(f">{len(h) // 2}H", h))
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def _ipv4(
    src: str, dst: str, proto: int, payload: bytes, ident: int = 1, flags_off: int = 0
) -> bytes:
    import socket

    hdr = struct.pack(
        ">BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(payload),
        ident,
        flags_off,
        64,
        proto,
        0,
        socket.inet_aton(src),
        socket.inet_aton(dst),
    )
    hdr = hdr[:10] + struct.pack(">H", _ip_checksum(hdr)) + hdr[12:]
    return hdr + payload


def _udp(src: str, sport: int, dst: str, dport: int, payload: bytes) -> bytes:
    return _ipv4(src, dst, 17, struct.pack(">HHHH", sport, dport, 8 + len(payload), 0) + payload)


def _eth(ip: bytes, vlan: bool = False) -> bytes:
    head = b"\x00\x11\x22\x33\x44\x55" + b"\x66\x77\x88\x99\xaa\xbb"
    if vlan:
        head += b"\x81\x00\x00\x05"
    return head + b"\x08\x00" + ip + (b"\x00" * max(0, 46 - len(ip)))  # Ethernet padding


def _udp_session() -> list[tuple[float, bytes]]:
    p = 50000
    fifo = write_request(8, 0x66, [0x10, 0x00080BB8, 0x00010000, 0x13880004])
    return [
        (100.000, _eth(_udp(PC, p, CARD, 502, read_request(5, 1000, 2)))),
        (100.500, _eth(_udp(PC, p, CARD, 502, read_request(5, 1000, 2)))),  # retry, same seq
        (100.512, _eth(_udp(CARD, 502, PC, p, read_reply(5, 1000, [20152, 7])))),
        (
            101.000,
            _eth(
                _udp(PC, p, CARD, 502, write_request(6, 0x65, [3, 1, 50000, 5999, 59990, 1000])),
                vlan=True,
            ),
        ),
        (101.004, _eth(_udp(CARD, 502, PC, p, exception_reply(6, 0x40, 3)))),
        (101.100, _eth(_udp(PC, p, CARD, 502, write_request(7, 0x67, [2])))),  # never answered
        (101.200, _eth(_udp(PC, p, CARD, 502, fifo))),
        (101.203, _eth(_udp(CARD, 502, PC, p, write_reply(8, 0x66, 4)))),
        (101.300, _eth(_udp(CARD, 502, PC, p, exception_reply(99, 0x30, 2)))),  # orphan
        (101.400, _eth(_udp(PC, 40000, "10.1.1.170", 10001, b"EMON\r"))),  # laser: ignored
    ]


def _check_udp_session(txs: list) -> None:
    by_seq = {t.seq: t for t in txs}
    assert len(txs) == 5
    ok = by_seq[5]
    assert ok.status == "ok" and ok.attempts == 2 and ok.errcode is None
    assert ok.latency == pytest.approx(0.012, abs=1e-5)
    assert ok.reply is not None and ok.reply.vector == (0x30, 1000, 2, 20152, 7)
    exc = by_seq[6]
    assert exc.status == "exception" and exc.errcode == 503
    assert exc.latency == pytest.approx(0.004, abs=1e-5)
    assert by_seq[7].status == "timeout" and by_seq[7].errcode == 10060
    assert "FIFO-CTRL start" in by_seq[7].describe()
    assert by_seq[8].status == "ok" and by_seq[8].fifo is not None
    assert by_seq[99].status == "orphan-reply"
    assert all(t.notes == [] for t in txs)


def test_pcap_udp_pairing(tmp_path: Path) -> None:
    path = tmp_path / "s.pcap"
    write_pcap(path, _udp_session())
    pkts = list(iter_capture(path))
    assert len(pkts) == 10 and pkts[2].ts == pytest.approx(100.512)
    txs = dissect_capture(pkts)
    _check_udp_session(txs)
    s = summarize(txs)
    assert s["by_status"] == {"ok": 2, "exception": 1, "timeout": 1, "orphan-reply": 1}
    assert s["latency_ms"]["n"] == 3  # type: ignore[index]


def _pcapng(packets: list[tuple[float, bytes]], big_endian: bool, nanos: bool) -> bytes:
    e = ">" if big_endian else "<"

    def block(btype: int, body: bytes) -> bytes:
        body += b"\x00" * (-len(body) % 4)
        n = 12 + len(body)
        return struct.pack(e + "II", btype, n) + body + struct.pack(e + "I", n)

    out = block(0x0A0D0D0A, struct.pack(e + "IHHq", 0x1A2B3C4D, 1, 0, -1))
    opts = b""
    if nanos:
        opts = struct.pack(e + "HH", 9, 1) + b"\x09\x00\x00\x00" + struct.pack(e + "HH", 0, 0)
    out += block(1, struct.pack(e + "HHI", 1, 0, 65535) + opts)
    scale = 10**9 if nanos else 10**6
    for ts, data in packets:
        t = round(ts * scale)
        out += block(
            6, struct.pack(e + "IIIII", 0, t >> 32, t & 0xFFFFFFFF, len(data), len(data)) + data
        )
    out += block(5, b"\x00" * 8)  # interface statistics: ignored
    return out


@pytest.mark.parametrize(("big", "nanos"), [(False, False), (True, True)])
def test_pcapng(tmp_path: Path, big: bool, nanos: bool) -> None:
    path = tmp_path / "s.pcapng"
    path.write_bytes(_pcapng(_udp_session(), big, nanos))
    pkts = list(iter_capture(path))
    assert len(pkts) == 10 and pkts[2].ts == pytest.approx(100.512)
    _check_udp_session(dissect_capture(pkts))


def test_ip_fragment_reassembly(tmp_path: Path) -> None:
    vec = parse_fifo_words
    words = [0x279] + [0x00080BB8, 0, 0x13880004] * 99
    frame = write_request(0x1234, 0x66, words)
    assert len(frame) == 1206
    udp = struct.pack(">HHHH", 50001, 502, 8 + len(frame), 0) + frame
    cut = 600  # multiple of 8
    f1 = _ipv4(PC, CARD, 17, udp[:cut], ident=77, flags_off=0x2000)
    f2 = _ipv4(PC, CARD, 17, udp[cut:], ident=77, flags_off=cut // 8)
    reply = _udp(CARD, 502, PC, 50001, write_reply(0x1234, 0x66, len(words)))
    path = tmp_path / "frag.pcap"
    write_pcap(path, [(1.0, _eth(f2)), (1.0001, _eth(f1)), (1.01, _eth(reply))])
    (tx,) = dissect_file(path)
    assert tx.status == "ok" and tx.request is not None and tx.request.problems == ()
    assert tx.fifo is not None and len(tx.fifo.items) == 99 and vec(words).frame_id == 0x279


def test_tcp_stream_and_sll(tmp_path: Path) -> None:
    def tcp(
        src: str, sport: int, dst: str, dport: int, seq: int, payload: bytes, flags: int = 0x18
    ) -> bytes:
        seg = struct.pack(">HHIIBBHHH", sport, dport, seq, 0, 0x50, flags, 8192, 0, 0) + payload
        ip = _ipv4(src, dst, 6, seg)
        sll = struct.pack(">HHH8sH", 0, 1, 6, b"\x00" * 8, 0x0800)
        return sll + ip

    r1, r2 = read_request(1, 1000, 2), read_request(2, 2000, 50)
    a1, a2 = read_reply(1, 1000, [1, 2]), exception_reply(2, 0x30, 2)
    both = r1 + r2
    pk = [
        (5.0, tcp(PC, 3000, CARD, 502, 999, b"", flags=0x02)),  # SYN
        (5.1, tcp(PC, 3000, CARD, 502, 1000, both[:9])),
        (5.2, tcp(PC, 3000, CARD, 502, 1009, both[9:])),
        (5.2, tcp(PC, 3000, CARD, 502, 1009, both[9:])),  # retransmission: dropped
        (5.3, tcp(CARD, 502, PC, 3000, 7000, a1 + a2)),
    ]
    path = tmp_path / "tcp.pcap"
    write_pcap(path, pk, linktype=113)
    txs = dissect_file(path)
    assert [(t.seq, t.status, t.errcode, t.attempts) for t in txs] == [
        (1, "ok", None, 1),
        (2, "exception", 502, 1),
    ]


def test_direction_by_crc_when_ports_ambiguous(tmp_path: Path) -> None:
    path = tmp_path / "amb.pcap"
    write_pcap(
        path,
        [
            (1.0, _eth(_udp(PC, 502, CARD, 502, read_request(3, 1000, 2)))),
            (1.001, _eth(_udp(CARD, 502, PC, 502, exception_reply(3, 0x30, 2)))),
        ],
    )
    (tx,) = dissect_file(path)
    assert tx.status == "exception" and tx.request is not None
    assert tx.request.direction is Direction.REQUEST


# ================================================================================================
# CLI
# ================================================================================================


def test_cli_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from nexcut.mcc.dissector import main

    assert main([str(FIFO_FILE), "--fifo"]) == 0
    out = capsys.readouterr().out
    assert "FIFO id=633 (0x279) 100 items" in out
    assert "zf-bookkeeping" in out and "fifo_distinct_frames: 22" in out
    assert "fifo_frames_with_remainder: 0" in out


def test_cli_json_pcap(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from nexcut.mcc.dissector import main

    path = tmp_path / "s.pcap"
    write_pcap(path, _udp_session())
    assert main([str(path), "--json"]) == 0
    rows = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert rows[-1]["summary"]["transactions"] == 5
    assert rows[0]["status"] == "ok" and rows[0]["seq"] == 5 and rows[0]["attempts"] == 2


def test_cli_module_entrypoint(tmp_path: Path) -> None:
    log = tmp_path / "x.txt"
    log.write_text(L_START + L_503, encoding="ascii")
    res = subprocess.run(
        [sys.executable, "-m", "nexcut.mcc.dissector", str(log)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "exception(503)" in res.stdout and "--- NC Start ---" in res.stdout


# ================================================================================================
# the real package logs (skipped without SRC); expected numbers copied from 08 §2-§4
# ================================================================================================


def test_package_logs(src_dir: Path) -> None:
    logs = sorted((src_dir / "Log").glob("*.log"))
    records, txs = dissect_logs(logs)
    kinds = Counter(r.kind for r in records)
    assert kinds == {
        "mc": 1682,
        "nc-start": 113,
    }  # 1794 lines in 2025 + 1 NC Start in 2026 (08 §2.1)
    stages = Counter(r.stage for r in records if r.kind == "mc")
    assert stages == {"RecvErr_selectFunc": 1072, "RecvDataErr": 603, "Sendto": 5, "Recvfrom": 2}
    exc = [t for t in txs if t.status == "exception"]
    assert Counter(t.errcode for t in exc) == {503: 567, 502: 36}
    assert Counter(t.reply.frame.raw[2:].hex() for t in exc if t.reply) == {
        "3445000300c003": 567,
        "d045000300b002": 34,
        "f585000300c002": 2,
    }
    for t in txs:
        for pdu in (t.request, t.reply):
            if pdu is not None:
                assert pdu.problems == (), (t.describe(), pdu.problems)
        assert t.notes in ([], ["ladder incomplete"])
    kinds_req = Counter(request_kind(t.request_vector) for t in exc if t.request_vector)
    # 11 §8 correction 1 / §2.1 census: 506 six-word jogs (sub 3), 60 five-word stops
    # (sub 1 - labelled "home" before this fix), 1 go-to (sub 5).
    assert (kinds_req["CMD jog"], kinds_req["CMD stop"], kinds_req["CMD goto"]) == (506, 60, 1)
    assert kinds_req["CMD home"] == 0  # sub-command 2 was never sent in the package logs
    ladders = Counter(len(t.rungs) for t in txs if t.status == "timeout")
    # 7-rung ladders: 139 with this grouping (analyst's count; the verifier's rule gave 138, 08 §2.3)
    assert ladders[7] == 139
    fifo = {t.fifo.frame_id: t.fifo for t in txs if t.fifo is not None}
    assert len(fifo) == 22 and all(not f.remainder for f in fifo.values())
    census: Counter[int] = Counter()
    for f in fifo.values():
        census.update(f.census())
    assert dict(census) == GOLDEN_CENSUS
    assert sum(1 for r in records if r.dataex and r.dataex[:2] == (0x40, 0x66)) == 30
