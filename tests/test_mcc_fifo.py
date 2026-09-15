"""FIFO frame packer, fillFifo flow control and frame files (11 §5.1, A3 §4.1, §7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexcut.mcc.dissector import parse_fifo_words
from nexcut.mcc.fifo import (
    FLUSH_WORDS,
    MAX_FRAMES_PER_FILL,
    FifoFeeder,
    FifoFrameLost,
    FramePacker,
    PackedFrame,
    format_frame_line,
    item_header,
    pack_records,
    parse_frame_line,
    read_frame_file,
    write_frame_file,
)

LEAKED = Path(__file__).parent / "data" / "mcc" / "fifo_frames_2025-07.txt"
TICK = [0x00080BB8, 0x00000000, 0x13880000]


def test_constants_and_header() -> None:
    assert FLUSH_WORDS == 300  # 0x4b0 bytes incl. the 4-word prefix
    assert MAX_FRAMES_PER_FILL == 50
    assert item_header(3000, 2) == 0x00080BB8
    assert item_header(3001, 0) == 0x00000BB9
    assert item_header(9999, 3) == 0x000C270F
    assert item_header(118, 3) == 0x000C0076
    with pytest.raises(ValueError):
        item_header(0x10000, 0)


def test_99_ticks_close_a_frame_with_count_0x12a() -> None:
    frames = pack_records([[TICK]] * 99)
    assert len(frames) == 1
    f = frames[0]
    assert len(f.data) == 297 and f.count == 0x12A and f.byte_size == 1204
    assert f.vector(0x279)[:4] == [0x40, 0x66, 0x12A, 0x279]
    # the 100th tick opens the next frame
    frames = pack_records([[TICK]] * 100)
    assert [len(x.data) for x in frames] == [297, 3]


def test_flush_is_checked_per_record_not_per_item() -> None:
    p = FramePacker()
    for _ in range(98):
        p.add_record([TICK])
    assert p.pending_words == 294  # 298 words with the prefix: still open
    # one record with three items (like record type 8: 109, 2001, 118) crosses the threshold
    p.add_record([[0x0008006D, 1000, 0], [0x000807D1, 0x03000002, 20000], [0x000C0076, 4, 0, 35]])
    assert len(p.frames) == 1 and len(p.frames[0].data) == 294 + 3 + 3 + 4
    p.end_batch()
    assert len(p.frames) == 1  # nothing pending


def test_boundary_items_allow_100_items_in_297_words() -> None:
    # frames 57/504/633 hold 100 items (3001 has no payload, A3 §4.1)
    recs = [[TICK]] * 97 + [[[0x00000BB9]]] * 2 + [[TICK]]
    frames = pack_records(recs)
    assert len(frames) == 1 and frames[0].items == 100 and len(frames[0].data) == 296


def test_packer_rejects_bad_items() -> None:
    p = FramePacker()
    with pytest.raises(ValueError):
        p.add_record([[0x00080BB8, 1]])
    with pytest.raises(ValueError):
        p.add_record([[]])


def _frames(n: int) -> list[PackedFrame]:
    return [PackedFrame(tuple(TICK * 99), items=99) for _ in range(n)]


def test_fill_frame_ids_space_rule_and_limit() -> None:
    feeder = FifoFeeder(_frames(120))
    sent: list[tuple[int, list[int]]] = []
    res = feeder.fill(56, 60000, lambda fid, w: sent.append((fid, w)))
    assert res.stopped_by == "space" and len(res.sent) == 48  # 60000 - 48*1204 = 2208 < 3204
    assert res.space_left == 60000 - 48 * 1204
    sent.clear()
    res = feeder.fill(56, 1_000_000, lambda fid, w: sent.append((fid, w)))
    assert res.stopped_by == "limit" and len(res.sent) == 50
    assert [fid for fid, _ in sent[:3]] == [57, 58, 59]  # reg 1015 + 1, then +1 per frame
    assert sent[0][1][0] == 57 and len(sent[0][1]) == 298
    assert res.space_left == 1_000_000 - 50 * 1204
    # space: 1204 + 2000 <= space must hold
    sent.clear()
    res = feeder.fill(106, 3203, lambda fid, w: sent.append((fid, w)))
    assert res.stopped_by == "space" and not sent
    res = feeder.fill(106, 3204, lambda fid, w: sent.append((fid, w)))
    assert [fid for fid, _ in sent] == [107] and res.stopped_by == "space"
    assert feeder.pending == 120 - 48 - 50 - 1


def test_fill_wraps_frame_id() -> None:
    feeder = FifoFeeder(_frames(2))
    ids: list[int] = []
    feeder.fill(0xFFFFFFFE, 60000, lambda fid, w: ids.append(fid))
    assert ids == [0xFFFFFFFF, 0]


def test_lost_frame_aborts_instead_of_dropping() -> None:
    feeder = FifoFeeder(_frames(3))

    def send(fid: int, words: list[int]) -> None:
        if fid == 2:
            raise TimeoutError("no reply")

    with pytest.raises(FifoFrameLost) as exc:
        feeder.fill(0, 60000, send)
    assert exc.value.frame_id == 2
    assert feeder.frames_sent == 1


def test_job_end_detection() -> None:
    feeder = FifoFeeder()
    feeder.push(_frames(1))
    assert not feeder.job_finished(60000, 1)
    feeder.fill(0, 60000, lambda fid, w: None)
    assert not feeder.job_finished(60000, 1)  # last batch not pushed yet
    feeder.push([], last=True)
    assert feeder.job_finished(60000, 1)
    assert feeder.job_finished(60000, 0x0101)  # low byte only
    assert not feeder.job_finished(58796, 1)
    assert not feeder.job_finished(60000, 0)


def test_leaked_frames_parse_and_reformat_identically() -> None:
    lines = [ln for ln in LEAKED.read_text(encoding="utf-8").splitlines() if "DataEx:" in ln]
    assert len(lines) == 22
    for ln in lines:
        fid, data = parse_frame_line(ln)  # type: ignore[misc]
        frame = parse_fifo_words([fid, *data])
        packer = FramePacker()
        for i, item in enumerate(frame.items):
            packer.add_record([[item.opcode | (len(item.args) * 4 << 16), *item.args]])
            if i < len(frame.items) - 1:
                assert not packer.frames, "flush rule closed the frame early"
        assert len(packer.frames) == 1  # closes exactly at the last item (300-word rule)
        assert format_frame_line(fid, packer.frames[0]) == ln.split("DataEx:")[1].strip()


def test_parse_frame_line_edge_cases() -> None:
    assert parse_frame_line("# comment") is None
    assert parse_frame_line("") is None
    assert parse_frame_line("40 65 04 270f 02 01 00") is None  # not 0x66
    with pytest.raises(ValueError):
        parse_frame_line("40 66 05 01 bb9")


def test_frame_file_roundtrip(tmp_path: Path) -> None:
    frames = _frames(2)
    path = tmp_path / "f.txt"
    assert write_frame_file(path, [(1, frames[0]), (2, frames[1])], ["dry run §5"]) == 2
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# dry run §5\n40 66 12a 01 80bb8 00 13880000")
    back = read_frame_file(path)
    assert [fid for fid, _ in back] == [1, 2] and back[0][1] == list(frames[0].data)


def test_largest_frame_fits_the_udp_encoder() -> None:
    from nexcut.mcc.framing import MAX_FRAME_LEN, encode_vector

    p = FramePacker()
    for _ in range(98):
        p.add_record([TICK])
    p.add_record([[0x00200068, *range(8)]])  # 104 long form: largest item (A3 §5)
    frame = p.frames[0]
    assert len(frame.data) == 294 + 9
    raw = encode_vector(frame.vector(7), seq=1)
    assert len(raw) <= MAX_FRAME_LEN
