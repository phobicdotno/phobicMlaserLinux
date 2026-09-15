"""FIFO frame packer and flow control for register 0x66 (11 §5.1, A3 §4.1, §7).

Two vendor functions are reproduced:

* **item builder flush** (NCModule VM slot 121 ``0x100437d0``, flush test ``0x10045389-0x100453b3``):
  a frame vector starts with the 4-word prefix ``[0x40, 0x66, count, frame_id]``; after every
  *record* (one record may emit several items) the vector is closed when it holds
  ``>= 0x4b0`` bytes = **300 words** (prefix included) or when the batch ends.  ``count = words - 3``
  = frame id + data words.  With 3-word ticks a frame closes after 99 ticks (297 data words,
  ``count = 0x12a``); ``ipAdd.ini MaxItemPerFrame`` is read and ignored (A3 V).
* **fillFifo** (VM slot 122 ``0x10052390``): ``frame_id = reg 1015 + 1``, +1 per frame, at most 50
  frames per call, a frame is sent only if ``frame_bytes + 2000 <= reg 1016``; the byte count is the
  PC-side vector size including the ``0x40, 0x66`` words (``space -= f.bytes``,
  ``0x10052aa8-0x10052abb``).  Reg 1016 is treated as *bytes free* because that is the only
  evidence (11 C2); the card-side unit is UNVERIFIED (11 §7 step 8).

Deviation (safety, 11 §5.1): the vendor pops a frame before sending and drops it after the
retries fail; :meth:`FifoFeeder.fill` raises :class:`FifoFrameLost` instead so the caller aborts
the job (``0x67 <- [3]``) rather than stream a hole into the path.

Nothing here opens a socket: :meth:`FifoFeeder.fill` takes a ``send`` callable, which in the port
is a :class:`nexcut.mcc.safety.SafeMccClient` write (laser records stripped unless LASER_ARMED).
The frame-file helpers write/read the ``DataEx:``-style text of ``Log/*.log`` (08 §2.1) for dry
runs and golden comparisons.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from nexcut.mcc.framing import FUNC_WRITE, REG_FIFO_DATA
from nexcut.mcc.registers import (
    FIFO_MARGIN_EMPTY,
    FIFO_SEND_HEADROOM_BYTES,
    fifo_program_running,
    next_frame_id,
)

__all__ = [
    "FLUSH_WORDS",
    "MAX_FRAMES_PER_FILL",
    "PREFIX_WORDS",
    "FifoFeeder",
    "FifoFrameLost",
    "FillResult",
    "FramePacker",
    "PackedFrame",
    "format_frame_line",
    "item_header",
    "pack_records",
    "parse_frame_line",
    "read_frame_file",
    "write_frame_file",
]

WORD_BYTES = 4
FLUSH_BYTES = 0x4B0
"""Flush threshold of the item builder in bytes (``cmp …,0x4b0`` @``0x10045389``, A3 §4.1)."""
FLUSH_WORDS = FLUSH_BYTES // WORD_BYTES
"""300 words including the 4-word prefix (11 §5.1)."""
PREFIX_WORDS = 4
"""``[0x40, 0x66, count, frame_id]`` (``0x10043947``/``0x1004395f``, A3 §4.1)."""
MAX_FRAMES_PER_FILL = 50
"""``cmp …,0x32`` @``0x100523f4``: at most 50 frames per ``fillFifo`` call (A3 §7)."""
MAX_ITEM_PAYLOAD_WORDS = 0x3FFF
"""Header keeps the payload byte count in 16 bits (``(bytes << 16) | opcode``)."""


class FifoFrameLost(RuntimeError):
    """A frame could not be delivered; the job must be stopped (port deviation, 11 §5.1)."""

    def __init__(self, frame_id: int, cause: BaseException) -> None:
        super().__init__(f"FIFO frame {frame_id} not acknowledged: {cause}")
        self.frame_id = frame_id
        self.cause = cause


def item_header(opcode: int, payload_words: int) -> int:
    """``(payload_bytes << 16) | opcode`` (11 §5.2)."""
    if not 0 <= opcode <= 0xFFFF:
        raise ValueError(f"opcode {opcode} outside u16")
    if not 0 <= payload_words <= MAX_ITEM_PAYLOAD_WORDS:
        raise ValueError(f"payload of {payload_words} words does not fit the header")
    return ((payload_words * WORD_BYTES) << 16) | opcode


@dataclass(frozen=True, slots=True)
class PackedFrame:
    """One closed frame: the item words after the frame id, plus bookkeeping.

    ``laser_items`` counts items flagged as laser records by the producer (metadata only; the
    safety gate re-derives laser content from the words, PORT-PLAN §8.2).
    """

    data: tuple[int, ...]
    laser_items: int = 0
    items: int = 0

    @property
    def vector_words(self) -> int:
        """Words of the vendor vector ``[0x40, 0x66, count, id, data…]``."""
        return PREFIX_WORDS + len(self.data)

    @property
    def byte_size(self) -> int:
        """PC-side size used for the reg-1016 test (``f.bytes``, A3 §7): 1204 for 99 ticks."""
        return self.vector_words * WORD_BYTES

    @property
    def count(self) -> int:
        """Count word ``words - 3`` = 1 (frame id) + data words (A3 §4.1)."""
        return 1 + len(self.data)

    def payload(self, frame_id: int) -> list[int]:
        """Words for ``write(0x66, …)``: ``[frame_id, data…]``."""
        return [frame_id & 0xFFFFFFFF, *self.data]

    def vector(self, frame_id: int) -> list[int]:
        """The vendor vector as printed after ``DataEx:`` (08 §2.1)."""
        return [FUNC_WRITE, REG_FIFO_DATA, self.count, *self.payload(frame_id)]


class FramePacker:
    """Item builder flush rule (VM slot 121, A3 §4.1).

    Call :meth:`add_record` with the item word lists produced by one record, then
    :meth:`end_batch` when the batch of records handed to the builder ends (the vendor flushes
    at the last record).  Closed frames accumulate in :attr:`frames`.
    """

    def __init__(self) -> None:
        self.frames: list[PackedFrame] = []
        self._data: list[int] = []
        self._laser = 0
        self._items = 0

    def add_record(self, items: Iterable[Sequence[int]], laser_items: int = 0) -> None:
        """Append the items of one record; close the frame when it reaches 300 words."""
        for words in items:
            if not words:
                raise ValueError("empty item")
            n = (words[0] >> 16) // WORD_BYTES
            if n != len(words) - 1:
                raise ValueError(f"item header {words[0]:#x} does not match {len(words) - 1} words")
            self._data.extend(int(w) & 0xFFFFFFFF for w in words)
            self._items += 1
        self._laser += laser_items
        if PREFIX_WORDS + len(self._data) >= FLUSH_WORDS:
            self._close()

    def end_batch(self) -> None:
        """Last record of the batch: close a non-empty frame (flush ``|| last record``)."""
        if self._data:
            self._close()

    def _close(self) -> None:
        self.frames.append(PackedFrame(tuple(self._data), self._laser, self._items))
        self._data = []
        self._laser = 0
        self._items = 0

    @property
    def pending_words(self) -> int:
        """Data words of the open frame."""
        return len(self._data)


def pack_records(records: Iterable[Sequence[Sequence[int]]]) -> list[PackedFrame]:
    """Pack one batch of records (each a list of item word lists) into frames."""
    p = FramePacker()
    for rec in records:
        p.add_record(rec)
    p.end_batch()
    return p.frames


@dataclass(slots=True)
class FillResult:
    """Outcome of one :meth:`FifoFeeder.fill` call."""

    sent: list[tuple[int, PackedFrame]] = field(default_factory=list)
    space_left: int = 0
    stopped_by: str = ""
    """``"empty"`` (ring drained), ``"space"`` (reg 1016 test) or ``"limit"`` (50 frames)."""


class FifoFeeder:
    """``fillFifo`` flow control over a ring of packed frames (A3 §7)."""

    def __init__(self, frames: Iterable[PackedFrame] = ()) -> None:
        self._ring: deque[PackedFrame] = deque(frames)
        self.last_batch_pushed = False
        self.frames_sent = 0

    def push(self, frames: Iterable[PackedFrame], *, last: bool = False) -> None:
        """Queue frames; ``last`` marks the job's final batch (job-end detection)."""
        self._ring.extend(frames)
        if last:
            self.last_batch_pushed = True

    @property
    def pending(self) -> int:
        """Frames not yet sent."""
        return len(self._ring)

    def fill(
        self,
        frame_id_reg: int,
        space_margin: int,
        send: Callable[[int, list[int]], None],
        *,
        max_frames: int = MAX_FRAMES_PER_FILL,
    ) -> FillResult:
        """Send frames while ``bytes + 2000 <= space`` (≤ 50 per call).

        ``frame_id_reg`` = reg 1015 and ``space_margin`` = reg 1016 from the latest status poll.
        ``send(frame_id, payload_words)`` writes register 0x66; if it raises, the frame is
        reported with :class:`FifoFrameLost` (the vendor would drop it silently, A3 §7 verifier).
        """
        res = FillResult(space_left=int(space_margin))
        frame_id = next_frame_id(frame_id_reg)
        space = int(space_margin)
        while True:
            if not self._ring:
                res.stopped_by = "empty"
                break
            if len(res.sent) >= max_frames:
                res.stopped_by = "limit"
                break
            f = self._ring[0]
            if f.byte_size + FIFO_SEND_HEADROOM_BYTES > space:
                res.stopped_by = "space"
                break
            self._ring.popleft()
            try:
                send(frame_id, f.payload(frame_id))
            except Exception as exc:
                raise FifoFrameLost(frame_id, exc) from exc
            res.sent.append((frame_id, f))
            self.frames_sent += 1
            frame_id = (frame_id + 1) & 0xFFFFFFFF
            space -= f.byte_size
        res.space_left = space
        return res

    def job_finished(self, space_margin: int, processing_status: int) -> bool:
        """Vendor job end (``0x10057f2d``): last batch pushed, reg 1016 == 60000 and reg 1019
        low byte == 1 -> caller sends ``0x67 <- [3]``.  The port also requires an empty ring."""
        return (
            self.last_batch_pushed
            and not self._ring
            and space_margin == FIFO_MARGIN_EMPTY
            and fifo_program_running(processing_status)
        )


# ------------------------------------------------------------------------------ frame files
_HEX = re.compile(r"^[0-9a-fA-F]+$")


def format_frame_line(frame_id: int, frame: PackedFrame | Sequence[int]) -> str:
    """``40 66 <count> <id> <words…>`` in the log's ``%02x`` style (08 §2.1)."""
    data = frame.data if isinstance(frame, PackedFrame) else tuple(frame)
    words = [FUNC_WRITE, REG_FIFO_DATA, 1 + len(data), frame_id & 0xFFFFFFFF, *data]
    return " ".join(f"{w & 0xFFFFFFFF:02x}" for w in words)


def parse_frame_line(line: str) -> tuple[int, list[int]] | None:
    """Parse a frame line (plain or a log line containing ``DataEx:``) -> ``(frame_id, data)``.

    Returns ``None`` for comments/blank lines and lines that are not ``WRITE 0x66``.
    Raises ``ValueError`` when the count word disagrees with the number of words.
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if "DataEx:" in text:
        text = text.split("DataEx:", 1)[1].strip()
    toks = text.split()
    if len(toks) < 4 or not all(_HEX.match(t) for t in toks):
        return None
    words = [int(t, 16) for t in toks]
    if words[0] != FUNC_WRITE or words[1] != REG_FIFO_DATA:
        return None
    if words[2] != len(words) - 3:
        raise ValueError(f"count word {words[2]:#x} != {len(words) - 3} words")
    return words[3], words[4:]


def write_frame_file(
    path: str | Path, frames: Iterable[tuple[int, PackedFrame]], header: Sequence[str] = ()
) -> int:
    """Write ``# header`` lines and one frame per line; returns the number of frames."""
    n = 0
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for h in header:
            fh.write(f"# {h}\n")
        for fid, frame in frames:
            fh.write(format_frame_line(fid, frame) + "\n")
            n += 1
    return n


def read_frame_file(path: str | Path) -> list[tuple[int, list[int]]]:
    """Read every frame line of a frame file or log excerpt."""
    out: list[tuple[int, list[int]]] = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        parsed = parse_frame_line(line)
        if parsed is not None:
            out.append(parsed)
    return out
