"""Reader (versions 1-5) and writer of Mlaser ``.chf`` job files (analysis 03).

``.chf`` is the ``scFlie`` text container of ``CLIFileBasic`` in
``Module/CADModule.dll``: one CRLF-terminated value per line, GBK strings,
``scFlie`` + version first and ``eof`` last (03 §1, §4, §5).  This module is
the promoted form of ``tools/chf_parse.py`` (which stays the reference tool);
the JSON and SVG exports reproduce that tool's output for the same input.

Reader
    Mirrors ``CLIFileBasic::ReadToken`` (``0x100d9990``) and the typed readers
    (03 §4.2), the graph/glyph readers (03 §5-§7) and the version gates (03 §9).
    Unlike the DLL it checks marker lines by default (``strict=True``); with
    ``strict=False`` markers are consumed unchecked like the original.

Writer
    Mirrors ``WriteStr/WriteInt/WriteDouble/WritePoint/WriteWStr`` (03 §4.1) and
    the ``Write`` methods (03 §5-§7).  Version 5 is what the current program
    writes; versions 1-4 are produced by inverting the reader's version gates,
    which reproduces the four v4 samples byte for byte.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

from nexcut.io import cp936
from nexcut.model.flatten import check_contour, flatten_glyph
from nexcut.model.glyph import (
    GLYPH_NAMES,
    ArcGlyph,
    CircleGlyph,
    EllipseArcGlyph,
    Glyph,
    GlyphType,
    LwPolylineGlyph,
    LwPolyVertex,
    PointGlyph,
    SegmentGlyph,
    SplineGlyph,
    Vec2,
)
from nexcut.model.graph import (
    CURRENT_VERSION,
    GRAPH_NAMES,
    ZERO,
    ChfDocument,
    Contour,
    ContourElement,
    ContourEx,
    Graph,
    GraphType,
    Group,
    LinkInfo,
    Scan,
    Text,
    iter_contours,
)

__all__ = [
    "MAGIC",
    "ChfError",
    "format_double",
    "format_point",
    "msvc_fixed",
    "read_chf",
    "load_chf",
    "write_chf",
    "save_chf",
    "to_tool_dict",
    "to_json",
    "to_svg",
    "check_document",
    "main",
]

MAGIC = "scFlie"
"""First line of every container (sic), ``.rdata`` ``0x10113fec`` (03 §1, §3)."""

EOF_MARK = "eof"
"""Last line, written by ``WriteEof`` ``0x100d9a30`` (03 §4.1)."""

ENCODING = "gbk"
"""``WriteWStr`` uses ``WideCharToMultiByte(CP_ACP)``; CP_ACP = cp936/GBK on the target (03 §4.1).

``"gbk"``/``"cp936"`` select :mod:`nexcut.io.cp936` (Windows table incl. ``0x80`` = EURO SIGN,
user-defined area and best-fit fallbacks); any other name is used as a Python codec.
"""

_CP936_NAMES = frozenset({"gbk", "cp936", "936", "ms936"})

INT_MIN, INT_MAX = -(2**31), 2**31 - 1

# Marker strings as the writer emits them (.rdata of CADModule.dll, 03 §3).
BEGIN_GRAPHS = "<Begin Graphs>"
END_GRAPHS = "<End Graphs>"
GRAPH_NO = "####graph NO:"
GLYPHS = "<Glyphs>"
END_GLYPHS = "<End Glyphs>"
GLY = "####Gly: "
CRAFTS = "<Crafts>"
END_CRAFTS = "<End Crafts>"
PWM = "<PWM Control>"
END_PWM = "<End PWM Control>"
GUIDE = "<GuideCurve Para>"
END_GUIDE = "<End GuideCurve Para>"
COOL = "<coolPos Para>"
END_COOL = "<End coolPos Para>"
GROUP_ELEM = "####Group elem NO:"
LINK_INFO = "####ContourEx link info:"
SCAN_PATH = "<Scan path>"
SCAN_PATH_NO = "####Path:"
END_SCAN_PATH = "<End Scan path>"
TEXT_PARA = "<Text para>"
END_TEXT_PARA = "<End Text para>"

# Reserved blank-line counts of versions 2..4 (03 §9, counted loops at the cited VAs).
LEGACY_BLANKS_BEGIN_GRAPHS = 5  # mov esi,5   @0x100a991d
LEGACY_BLANKS_GLYPHS = 10  # mov ebx,0xa @0x1006b51a
LEGACY_BLANKS_PER_GLYPH = 3  # mov ebx,3   @0x1006b64f
LEGACY_BLANKS_CRAFTS = 20  # mov ebx,0x14 @0x1006b6f7
LEGACY_BLANKS_GROUP = 3  # mov edi,3   @0x1007694b
LEGACY_BLANKS_SCAN = 5  # mov ebx,5   @0x10094944
LEGACY_BLANKS_TEXT = 5  # mov ebx,5   @0x100a40b7

DEGENERATE_LENGTH = 0.01
"""``ReadGraphs`` drops graphs with ``length < 0.01`` unless they are a single point (03 §5)."""

POINT_HALF_MAX = 30
"""``ReadPoint`` replaces a half longer than 0x1e chars by ``"0"`` (``0x100d9f70``, 03 §4.2)."""


class ChfError(ValueError):
    """A ``.chf`` read error.

    ``code`` follows ``CLIFileBasic [this+4]`` (03 §3.1): 1 open failed,
    2 empty/size, 3 read/parse error, 4 missing ``eof``/unexpected end,
    5 bad magic.
    """

    def __init__(self, message: str, code: int = 3, line: int | None = None) -> None:
        self.code = code
        self.line = line
        prefix = f"line {line}: " if line is not None else ""
        super().__init__(f"{prefix}{message} (DLL error code {code})")


def _legacy(version: int) -> bool:
    """Versions 2..4 carry reserved blank lines (``add eax,-2; cmp eax,2; ja``, 03 §9)."""
    return 2 <= version <= 4


# ---------------------------------------------------------------------------
# Number formatting (writer side)
# ---------------------------------------------------------------------------


def msvc_fixed(x: float, precision: int) -> str:
    """Format like MSVCR100 ``printf("%.<precision>f")`` (03 §4.1 "``%f`` is the MSVCR100 default").

    Emulates two pre-UCRT CRT traits: the binary value is first reduced to 17
    significant decimal digits, then rounded half-up at ``precision`` decimals
    (Python's ``%f`` is exact and rounds half-even, e.g. ``0.0078125`` ->
    ``0.007812`` vs MSVC ``0.007813``).
    UNVERIFIED: recalled CRT behaviour (``_fltout2``/``_fptostr``), not
    disassembled from MSVCR100.dll; values with <= 15 significant digits (all
    samples) format identically either way.
    """
    if not math.isfinite(x):
        raise ValueError(f"cannot write non-finite value {x!r} to .chf")
    d = Decimal(x)
    if d.is_zero():
        return f"{'-' if math.copysign(1.0, x) < 0 else ''}{0:.{precision}f}"
    d = d.quantize(Decimal(1).scaleb(d.adjusted() - 16), rounding=ROUND_HALF_UP)
    d = d.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)
    return f"{d:.{precision}f}"


ZeroStyle = Literal["file", "buffered"]


def format_double(x: float, zero_style: ZeroStyle = "file") -> str:
    """``CLIFileBasic::WriteDouble`` (``0x100d9b70``), without the CRLF (03 §4.1).

    ``|x| < 1e-10`` -> ``"0.0"`` (direct-file mode, format ``0x10113fb0``) or
    ``"0"`` (buffered mode, ``"%d"`` of 0); ``1e-10 <= |x| < 1e-6`` ->
    ``"%12.10f"``; otherwise ``"%f"``.  The samples were written in file mode.
    NaN/inf raise ``ValueError`` (the DLL would print MSVC ``1.#QNAN0``-style
    text that its own reader rejects).
    """
    if not math.isfinite(x):
        raise ValueError(f"cannot write non-finite value {x!r} to .chf")
    ax = abs(x)
    if ax < 1e-10:
        return "0" if zero_style == "buffered" else "0.0"
    if ax < 1e-6:
        return f"{msvc_fixed(x, 10):>12}"
    return msvc_fixed(x, 6)


def format_point(p: Sequence[float]) -> str:
    """``CLIFileBasic::WritePoint`` (``0x100d9cc0``): ``"%f,%f"``, no zero special case (03 §4.1)."""
    return f"{msvc_fixed(p[0], 6)},{msvc_fixed(p[1], 6)}"


# ---------------------------------------------------------------------------
# Tokenizer and typed readers
# ---------------------------------------------------------------------------

_ATOI = re.compile(rb"-?\d+")
_ATOF = re.compile(rb"-?(?:\d+\.?\d*|\.\d+)")


_INT_CHARS = b"0123456789-"
_DOUBLE_CHARS = b"0123456789-."
_POINT_CHARS = b"0123456789-.,"
_VEC2_NEW = tuple.__new__

VALUE_CACHE_MAX = 1 << 17
"""Distinct tokens per kind whose decoded value :class:`_Tokens` memoises.

``.chf`` repeats its tokens: the 50 000-contour perf file of the import/UI
fidelity review has 1.95 M tokens and 150 k distinct ones, and a hand-made job
repeats ``0``/``0.0``/``1`` thousands of times.  The cap keeps the cost bounded
for a file whose tokens are all distinct (nothing is evicted; past the cap the
caches simply stop growing).  Values are immutable (``int``/``float``/``Vec2``),
so sharing one object between readings is safe.
"""


def _atoi(tok: bytes) -> int:
    """C ``atoi`` on a token already validated to ``[0-9-]`` (03 §4.2).

    EVIDENCE: ``ReadInt`` calls MSVCR100 ``atoi`` (IAT ``0x1010f39c`` at ``0x100d9dda``);
    in ``SRC/msvcr100.dll`` ``atoi`` (RVA ``0x1fa21``) jumps to ``atol`` (``0x1fc9d``), which is
    ``strtol(s, NULL, 10)`` (``push 0xa; push 0; ... call 0x78abfc75``), so an out-of-range
    value saturates at ``INT_MIN``/``INT_MAX`` (32-bit ``long``) instead of growing.

    ``int(tok)`` is the fast path for the usual whole-token number; a token that
    only *starts* with a number (``1-2``) or holds none falls back to the
    ``strtol`` prefix rule.  The token alphabet is ``[0-9-]`` so no Python-only
    spelling (``_`` separators, whitespace, ``0x``) can reach the fast path.
    """
    try:
        value = int(tok)
    except ValueError:
        m = _ATOI.match(tok)
        if m is None:
            return 0
        value = int(m.group())
    return min(INT_MAX, max(INT_MIN, value))


def _atof(tok: bytes) -> float:
    """C ``atof`` on a token already validated to ``[0-9.-]`` with <= 1 dot (03 §4.2).

    Same two-step shape as :func:`_atoi`: ``float(tok)`` for a whole-token number,
    else the prefix rule.  The validated alphabet excludes ``e``/``E``/``_``/space,
    so ``float`` cannot accept anything ``strtod`` would reject here.
    """
    try:
        return float(tok)
    except ValueError:
        m = _ATOF.match(tok)
        return float(m.group()) if m else 0.0


def _valid_int(tok: bytes) -> bool:
    """Validator ``0x100d97b0``: only ``0-9`` and ``-`` (empty is valid).

    ``bytes.translate(None, keep)`` deletes every accepted byte, so an empty
    result means every byte was accepted (one C pass instead of a per-byte loop).
    """
    return not tok.translate(None, _INT_CHARS)


def _valid_double(tok: bytes) -> bool:
    """Validator ``0x100d9800``: only ``0-9 . -`` and at most one dot (empty is valid)."""
    return not tok.translate(None, _DOUBLE_CHARS) and tok.count(b".") <= 1


_BLANKS = b" \t"
"""Bytes ``ReadToken`` drops anywhere in a line (space, tab; 03 §4.2)."""

_CR_BLANKS_LF = re.compile(rb"\r[ \t]+(?=\n)")
"""A CR, blanks, then an LF: two line endings that must not fuse into one CRLF."""


_MARKER_BYTES: dict[str, bytes] = {}
"""ASCII form of each marker/header literal, with the blanks ``ReadToken`` drops removed.

Filled on first use by :func:`_marker_bytes`; the keys are the module's own
marker constants, so it stays tiny.
"""


def _marker_bytes(marker: str) -> bytes:
    """ASCII form of a marker/header literal with the blanks ``ReadToken`` drops removed."""
    value = marker.replace(" ", "").encode("ascii")
    _MARKER_BYTES[marker] = value
    return value


class _Tokens:
    """``CLIFileBasic::ReadToken`` (``0x100d9990``) plus the typed readers (03 §4.2).

    One token per line; CR, LF or CRLF terminate it; spaces and tabs are
    dropped anywhere in the line; an empty line is an empty token.

    The whole buffer is tokenised once, in two C passes: ``translate`` drops the
    blanks and ``bytes.splitlines`` cuts on CR / LF / CRLF - exactly the three
    terminators of the DLL loop, and it also drops the empty remainder after a
    final terminator, which is where ``ReadToken`` reports end of file.  The
    byte-at-a-time Python loop this replaces needed ~7 s for a 50 000-contour
    file (1.95 M calls; import/UI fidelity review, STATUS X12).
    """

    __slots__ = ("_doubles", "_ints", "_points", "_tokens", "line", "strict")

    def __init__(self, data: bytes, strict: bool) -> None:
        # GBK is safe here: no trail byte is 0x09/0x20/0x0a/0x0d (lead 0x81-0xfe,
        # trail 0x40-0xfe minus 0x7f), so removing blanks first cannot split a
        # multi-byte character, and the DLL drops those bytes unconditionally too.
        # A blanks-only line between a CR and an LF would leave "\r\n" once the
        # blanks are gone - one terminator instead of two, losing that empty token.
        # Writing the CR's own terminator back in front of the LF keeps them apart.
        # The two-byte probe costs a memchr and is false for every real .chf, where
        # blanks occur only inside marker lines such as "<Begin Graphs>".
        if b"\r " in data or b"\r\t" in data:
            data = _CR_BLANKS_LF.sub(b"\r\n", data)
        clean = data.translate(None, _BLANKS)
        self._tokens: list[bytes] = clean.splitlines()
        # A last line that holds nothing but blanks is a line too: the DLL loop
        # reaches it, drops the blanks and returns an empty token.  ``splitlines``
        # cannot see it any more, so put it back.
        if data and data[-1:] not in (b"\r", b"\n") and (not clean or clean[-1:] in (b"\r", b"\n")):
            self._tokens.append(b"")
        self.line = 0
        self.strict = strict
        # Decoded-value caches (see VALUE_CACHE_MAX): .chf repeats its tokens
        # heavily - a 50 000-contour file has 1.95 M tokens but 150 k distinct ones.
        self._ints: dict[bytes, int] = {}
        self._doubles: dict[bytes, float] = {}
        self._points: dict[bytes, Vec2] = {}

    def tok(self) -> bytes:
        i = self.line
        try:
            t = self._tokens[i]
        except IndexError:
            raise ChfError("unexpected end of file", 4, i + 1) from None
        self.line = i + 1
        return t

    def int(self) -> int:
        """``ReadInt`` ``0x100d9db0``.

        ``tok`` + :func:`_valid_int` + :func:`_atoi` are inlined and the decoded
        value is memoised: a 50 000-contour file makes 600 000 of these calls and
        the Python frames dominated the read (import/UI fidelity review, STATUS
        X12).  The validation and the value are unchanged.
        """
        i = self.line
        try:
            t = self._tokens[i]
        except IndexError:
            raise ChfError("unexpected end of file", 4, i + 1) from None
        self.line = i + 1
        cache = self._ints
        value = cache.get(t)
        if value is not None:
            return value
        if t.translate(None, _INT_CHARS):
            raise ChfError(f"expected int, got {t!r}", 3, i + 1)
        try:
            value = int(t)
        except ValueError:
            value = _atoi(t)
        else:
            if value > INT_MAX:
                value = INT_MAX
            elif value < INT_MIN:
                value = INT_MIN
        if len(cache) < VALUE_CACHE_MAX:
            cache[t] = value
        return value

    def double(self) -> float:
        """``ReadDouble`` ``0x100d9df0`` (inlined and memoised like :meth:`int`)."""
        i = self.line
        try:
            t = self._tokens[i]
        except IndexError:
            raise ChfError("unexpected end of file", 4, i + 1) from None
        self.line = i + 1
        cache = self._doubles
        value = cache.get(t)
        if value is not None:
            return value
        if t.translate(None, _DOUBLE_CHARS):
            raise ChfError(f"expected double, got {t!r}", 3, i + 1)
        try:
            value = float(t)
        except ValueError:
            # Over the validated alphabet ``float`` fails only for a token that is
            # not a whole number ('1-2', '', '-') or that holds a second dot, so
            # the remaining dot check of 0x100d9800 is needed only on this path.
            if t.count(b".") > 1:
                raise ChfError(f"expected double, got {t!r}", 3, i + 1) from None
            value = _atof(t)
        if len(cache) < VALUE_CACHE_MAX:
            cache[t] = value
        return value

    def bool(self) -> bool:
        """``ReadBool`` ``0x100d9e30``: int-validated, then ``strcmp(token, "1") == 0``."""
        t = self.tok()
        if not _valid_int(t):
            raise ChfError(f"expected bool, got {t!r}", 3, self.line)
        return t == b"1"

    def point_from(self, t: bytes) -> Vec2:
        """``ReadPoint`` body (``0x100d9e80``): split at the first comma, halves > 30 chars -> "0"."""
        xs, comma, ys = t.partition(b",")
        if not comma:
            raise ChfError(f"expected 'x,y', got {t!r}", 3, self.line)
        if len(xs) > POINT_HALF_MAX:
            xs = b"0"
        if len(ys) > POINT_HALF_MAX:
            ys = b"0"
        if xs.translate(None, _DOUBLE_CHARS) or ys.translate(None, _DOUBLE_CHARS):
            raise ChfError(f"expected 'x,y', got {t!r}", 3, self.line)
        try:
            return Vec2(float(xs), float(ys))
        except ValueError:  # see :meth:`double`: only here can a half hold two dots
            if xs.count(b".") > 1 or ys.count(b".") > 1:
                raise ChfError(f"expected 'x,y', got {t!r}", 3, self.line) from None
            return Vec2(_atof(xs), _atof(ys))

    def point(self) -> Vec2:
        """``ReadPoint`` ``0x100d9e80`` (token read inlined and memoised like :meth:`int`).

        The fast path needs the token to be at most ``2 * POINT_HALF_MAX + 1``
        bytes long, so that neither half can trip the "> 30 chars -> 0" rule of
        the DLL, and to hold exactly one comma with nothing outside ``[0-9.,-]``.
        Everything else goes through :meth:`point_from` unchanged.
        """
        i = self.line
        try:
            t = self._tokens[i]
        except IndexError:
            raise ChfError("unexpected end of file", 4, i + 1) from None
        self.line = i + 1
        cache = self._points
        p = cache.get(t)
        if p is not None:
            return p
        if len(t) <= POINT_HALF_MAX + 1 and not t.translate(None, _POINT_CHARS) and t.count(b",") == 1:
            xs, _, ys = t.partition(b",")
            try:
                p = _VEC2_NEW(Vec2, (float(xs), float(ys)))
            except ValueError:
                p = self.point_from(t)
        else:
            p = self.point_from(t)
        if len(cache) < VALUE_CACHE_MAX:
            cache[t] = p
        return p

    def wstr(self) -> str:
        """``ReadWStr`` ``0x100da370``: cp936 bytes; unmappable bytes survive via surrogateescape."""
        return cp936.decode(self.tok())

    def marker(self, marker: str) -> None:
        """Consume a marker line; checked only in strict mode (the DLL never compares, 03 §4.2)."""
        i = self.line
        try:
            t = self._tokens[i]
        except IndexError:
            raise ChfError("unexpected end of file", 4, i + 1) from None
        self.line = i + 1
        if self.strict and t != (_MARKER_BYTES.get(marker) or _marker_bytes(marker)):
            raise ChfError(f"expected {marker!r}, got {t!r}", 3, i + 1)

    def header(self, prefix: str) -> None:
        """Consume a ``####...<n>`` index line (number ignored, like the DLL)."""
        i = self.line
        try:
            t = self._tokens[i]
        except IndexError:
            raise ChfError("unexpected end of file", 4, i + 1) from None
        self.line = i + 1
        if self.strict and not t.startswith(_MARKER_BYTES.get(prefix) or _marker_bytes(prefix)):
            raise ChfError(f"expected {prefix!r}, got {t!r}", 3, i + 1)

    def skip(self, n: int) -> None:
        """Consume ``n`` tokens (end of file is reported by the next read, as in the DLL)."""
        if n <= 0:
            return
        if self.line + n > len(self._tokens):
            self.line = len(self._tokens)
            raise ChfError("unexpected end of file", 4, self.line + 1)
        self.line += n


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


def _read_glyph(tk: _Tokens, gtype: int) -> Glyph:
    """Glyph ``Read`` methods, vtable slot 23 (03 §7)."""
    if gtype == GlyphType.POINT:  # CEditablePoint::Read 0x100895d0
        return PointGlyph(tk.point())
    if gtype == GlyphType.SEGMENT:  # CEditableSegment::Read 0x1008ac90
        return SegmentGlyph(tk.point(), tk.point())
    if gtype == GlyphType.ARC:  # CEditableArc::Read 0x1007eb10
        return ArcGlyph(tk.point(), tk.double(), tk.double(), tk.double())
    if gtype == GlyphType.CIRCLE:  # CEditableCircle::Read 0x10080420
        return CircleGlyph(tk.point(), tk.double())
    if gtype == GlyphType.ELLIPSE_ARC:  # CEditableEllipsArc::Read 0x100828a0
        return EllipseArcGlyph(tk.point(), tk.point(), tk.double(), tk.double(), tk.double())
    if gtype == GlyphType.LWPOLYLINE:  # CEditableLwpoly::Read 0x10088d50
        closed = tk.int()
        n = tk.int()
        verts = []
        for _ in range(n):
            p = tk.point()
            verts.append(LwPolyVertex(p, tk.double()))
        return LwPolylineGlyph(closed, verts)
    if gtype == GlyphType.SPLINE:  # CEditableSpline::Read 0x1008f250
        i1 = tk.int()
        i2 = tk.int()
        n = tk.int()
        ctrl = [tk.point() for _ in range(n)]
        nk = tk.int()
        knots = [tk.double() for _ in range(nk)]
        if n < 4 or nk != n + 4:  # 0x1008f30a..0x1008f31a
            raise ChfError(f"spline needs >=4 control points and n+4 knots (n={n}, knots={nk})", 3, tk.line)
        return SplineGlyph(i1, i2, ctrl, knots)
    raise ChfError(f"unknown glyph type {gtype}", 3, tk.line)


def _read_contour(tk: _Tokens, version: int) -> Contour:
    """``CGlyContour::Read`` ``0x1006b4e0`` (03 §6.1, crafts 03 §6.1.1)."""
    c = Contour()
    c.precision = tk.double()
    tk.marker(GLYPHS)
    if _legacy(version):
        tk.skip(LEGACY_BLANKS_GLYPHS)
    c.length = tk.double()
    c.bbox_min = tk.point()
    c.bbox_max = tk.point()
    c.start = tk.point()
    c.end = tk.point()
    n = tk.int()
    if n < 1:
        raise ChfError(f"contour with {n} glyphs", 3, tk.line)
    for _ in range(n):
        tk.header(GLY)
        direction = tk.int()
        gtype = tk.int()
        c.elements.append(ContourElement(_read_glyph(tk, gtype), direction))
        if _legacy(version):
            tk.skip(LEGACY_BLANKS_PER_GLYPH)
    tk.marker(END_GLYPHS)
    c.layer = tk.int()
    c.int58 = tk.int()
    tk.marker(CRAFTS)
    if _legacy(version):
        tk.skip(LEGACY_BLANKS_CRAFTS)
    cr = c.crafts
    cr.compensate_type = tk.int()
    cr.compensate_width = tk.double()
    tk.marker(PWM)
    cr.pwm_enable = tk.int()
    npwm = tk.int()
    for _ in range(npwm):
        a = tk.double()
        cr.pwm_nodes.append((a, tk.double()))
    nclose = tk.int()
    cr.pwm_close_pos_ratios = [tk.double() for _ in range(nclose)]
    tk.marker(END_PWM)
    cr.double170 = tk.double()
    cr.double188 = tk.double()
    tk.marker(GUIDE)
    lead = cr.lead_line
    lead.type = tk.int()
    lead.angle_deg = tk.double()
    lead.length = tk.double()
    lead.arc_radius = tk.double() if version > 1 else None  # cmp ebx,1 @0x1006b86f
    lead.flag = tk.bool()
    tk.marker(END_GUIDE)
    if version > 2:  # cmp ebx,2 @0x1006b95d
        tk.marker(COOL)
        ncool = tk.int()
        cr.cool_pos = [tk.double() for _ in range(ncool)]
        tk.marker(END_COOL)
    else:
        cr.cool_pos = None
    tk.marker(END_CRAFTS)
    return c


def _read_group_into(tk: _Tokens, version: int, g: Group) -> Group:
    """``CGlyGroup::Read`` ``0x10076910`` (03 §6.2)."""
    if _legacy(version):
        tk.skip(LEGACY_BLANKS_GROUP)
    g.length = tk.double()
    g.bbox_min = tk.point()
    g.bbox_max = tk.point()
    g.start = tk.point()
    g.end = tk.point()
    n = tk.int()
    for _ in range(n):
        tk.header(GROUP_ELEM)
        g.children.append(_read_contour(tk, version))
    g.layer = tk.int()
    g.int58 = tk.int()
    return g


def _read_contour_ex(tk: _Tokens, version: int) -> ContourEx:
    """``CGlyContourEx::Read`` ``0x10072270``, element reader ``0x10070330`` (03 §6.5)."""
    g = ContourEx()
    _read_group_into(tk, version, g)
    n = tk.int()
    for _ in range(n):
        tk.header(LINK_INFO)
        e = LinkInfo(tk.int(), tk.int(), tk.double(), tk.double(), tk.double(), tk.double())
        e.pt18_token = tk.tok().decode("latin-1")
        e.pt18 = _link_point(tk, e.pt18_token, e.int0 == 1)
        e.pt28_token = tk.tok().decode("latin-1")
        e.pt28 = _link_point(tk, e.pt28_token, e.int4 == 1)
        g.links.append(e)
    return g


def _link_point(tk: _Tokens, token: str, parsed_by_dll: bool) -> Vec2:
    """Link-record point: strictly parsed when the DLL parses it, else best effort (03 §6.5)."""
    raw = token.encode("latin-1")
    if parsed_by_dll:
        return tk.point_from(raw)
    try:
        return tk.point_from(raw)
    except ChfError:
        return ZERO  # UNVERIFIED: the DLL leaves the member at its (unrecovered) default


def _read_scan(tk: _Tokens, version: int) -> Scan:
    """``CGlyScan::Read`` ``0x100948e0`` (03 §6.4)."""
    g = Scan()
    _read_group_into(tk, version, g)
    tk.marker(SCAN_PATH)
    if _legacy(version):
        tk.skip(LEGACY_BLANKS_SCAN)
    n = tk.int()
    for _ in range(n):
        tk.header(SCAN_PATH_NO)
        g.paths.append(_read_contour(tk, version))
    tk.marker(END_SCAN_PATH)
    return g


def _read_text(tk: _Tokens, version: int) -> Text:
    """``CGlyText::Read`` ``0x100a4030`` (03 §6.3)."""
    g = Text()
    if _legacy(version):
        tk.skip(LEGACY_BLANKS_TEXT)
    g.position = tk.point()
    g.d130 = tk.double()
    g.d138 = tk.double()
    g.d140 = tk.double()
    g.text = tk.wstr()
    tk.marker(TEXT_PARA)
    g.font = tk.wstr()
    g.font_d0 = tk.double()
    g.font_d1 = tk.double()
    g.font_d2 = tk.double()
    tk.marker(END_TEXT_PARA)
    if version >= 4:  # cmp eax,4 @0x100a418e
        g.outline = _read_group_into(tk, version, Group())
        g.layer = g.outline.layer
        g.int58 = g.outline.int58
    else:
        g.outline = None
        g.layer = tk.int()
        g.int58 = tk.int()
    return g


_GRAPH_READERS: dict[int, Callable[[_Tokens, int], Graph]] = {
    GraphType.CONTOUR: _read_contour,
    GraphType.GROUP: lambda tk, v: _read_group_into(tk, v, Group()),
    GraphType.TEXT: _read_text,
    GraphType.SCAN: _read_scan,
    GraphType.CONTOUR_EX: _read_contour_ex,
}


def _is_single_point(c: Contour) -> bool:
    return len(c.elements) == 1 and isinstance(c.elements[0].glyph, PointGlyph)


def _keep_graph(g: Graph) -> bool:
    """Degenerate-graph filter of ``ReadGraphs`` (``0x100a99ae..0x100a9ab8``, 03 §5).

    A graph with ``length < 0.01`` survives only as a contour with exactly one
    point glyph, or a group whose single child is such a contour.
    UNVERIFIED: whether ``CGlyScan``/``CGlyContourEx`` (and text) pass the
    ``CGlyGroup`` dynamic cast, and which length a text object carries; here
    group subclasses are treated as groups and text uses its outline length.
    """
    if isinstance(g, Text):
        if g.outline is None:
            return True
        length = g.outline.length
    else:
        length = g.length
    if not length < DEGENERATE_LENGTH:
        return True
    if isinstance(g, Contour):
        return _is_single_point(g)
    grp = g.outline if isinstance(g, Text) else g
    return grp is not None and len(grp.children) == 1 and _is_single_point(grp.children[0])


def read_chf(data: bytes, *, strict: bool = True, drop_degenerate: bool = False) -> ChfDocument:
    """Parse ``.chf`` bytes into a :class:`ChfDocument` (03 §4.2, §5-§9).

    ``strict`` checks marker/header lines and the final ``eof`` token (the DLL
    only checks the magic token and the file tail).  ``drop_degenerate``
    applies the ``ReadGraphs`` filter of 03 §5 (off by default so reading is
    lossless).  Any version number is accepted; gates follow 03 §9.
    """
    if not data:
        raise ChfError("empty file", 2)
    # OpenForRead 0x100da2f8..0x100da321: walk back over bytes whose *signed*
    # value is <= 0x0d (CR, LF, NUL, ... and every byte >= 0x80), then "eof".
    end = len(data)
    while end > 0 and (data[end - 1] <= 0x0D or data[end - 1] >= 0x80):
        end -= 1
    tk = _Tokens(data, strict)
    if tk.tok() != MAGIC.encode("ascii"):
        raise ChfError("missing 'scFlie' magic", 5, 1)
    if data[max(0, end - 3) : end] != b"eof":
        raise ChfError("file does not end with 'eof'", 4)
    doc = ChfDocument()
    doc.version = tk.int()
    tk.marker(BEGIN_GRAPHS)
    if _legacy(doc.version):
        tk.skip(LEGACY_BLANKS_BEGIN_GRAPHS)
    n = tk.int()
    for _ in range(n):
        tk.header(GRAPH_NO)
        gtype = tk.int()
        reader = _GRAPH_READERS.get(gtype)
        if reader is None:
            raise ChfError(f"unknown graph type {gtype}", 3, tk.line)
        g = reader(tk, doc.version)
        if not drop_degenerate or _keep_graph(g):
            doc.graphs.append(g)
    tk.marker(END_GRAPHS)
    doc.trailer_bool = tk.bool()
    doc.trailer_double = tk.double()
    doc.trailer_pt1 = tk.point()
    doc.trailer_pt2 = tk.point()
    if strict:
        t = tk.tok()
        if t != EOF_MARK.encode("ascii"):
            raise ChfError(f"expected 'eof', got {t!r}", 4, tk.line)
    return doc


def load_chf(path: str | Path, **kwargs: Any) -> ChfDocument:
    """Read a ``.chf`` file from disk; keyword arguments go to :func:`read_chf`."""
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        raise ChfError(f"cannot open {path}: {e}", 1) from e
    return read_chf(data, **kwargs)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


class _Out:
    """``CLIFileBasic`` writer methods (03 §4.1); lines are joined with CRLF."""

    def __init__(self, zero_style: ZeroStyle, encoding: str) -> None:
        self.lines: list[bytes] = []
        self.zero_style = zero_style
        self.encoding = encoding

    def str(self, s: str | None) -> None:
        """``WriteStr`` ``0x100d9860``: ``"%s\\r\\n"``; ``None`` -> empty line."""
        self.lines.append(b"" if s is None else s.encode("ascii"))

    def str_int(self, s: str, i: int) -> None:
        """``WriteStrInt`` ``0x100d9aa0``: ``"%s%d\\r\\n"``."""
        self.lines.append(f"{s}{i:d}".encode("ascii"))

    def int(self, i: int) -> None:
        """``WriteInt`` ``0x100d9b10``: ``"%d\\r\\n"`` (32-bit on the original)."""
        i = int(i)
        if not -(2**31) <= i < 2**31:
            raise ValueError(f"int {i} does not fit the original's 32-bit field")
        self.lines.append(f"{i:d}".encode("ascii"))

    def double(self, x: float) -> None:
        """``WriteDouble`` ``0x100d9b70``."""
        self.lines.append(format_double(float(x), self.zero_style).encode("ascii"))

    def bool(self, b: bool) -> None:
        """``WriteBool`` ``0x100d9c70``: ``"1"``/``"0"``."""
        self.lines.append(b"1" if b else b"0")

    def point(self, p: Sequence[float]) -> None:
        """``WritePoint`` ``0x100d9cc0``."""
        self.lines.append(format_point(p).encode("ascii"))

    def wstr(self, s: str) -> None:
        """``WriteWStr`` ``0x100da070``: ``WideCharToMultiByte(CP_ACP)`` then ``"%s\\r\\n"``.

        EVIDENCE ``0x100da097..0x100da0a4``: ``dwFlags = 0`` and no default-char
        arguments, so Windows best-fit applies and unmappable characters become
        ``?``; the cp936 table is :mod:`nexcut.io.cp936`.  CR/LF would break the
        line grammar and are rejected.  Note that
        the original reader drops spaces/tabs, so they do not survive a
        round-trip through the Windows program either (03 §4.2).
        """
        if "\r" in s or "\n" in s:
            raise ValueError(f"string {s!r} contains a line break")
        if self.encoding.lower() in _CP936_NAMES:
            self.lines.append(cp936.encode(s))
            return
        out = bytearray()
        for ch in s:
            try:
                out += ch.encode(self.encoding, errors="surrogateescape")
            except UnicodeEncodeError:
                out += b"?"
        self.lines.append(bytes(out))

    def link_point(self, token: str | None, p: Vec2) -> None:
        """Link-record point line (03 §6.5): the raw token read from the file if ``p`` is unchanged.

        The DLL only parses ``pt18``/``pt28`` when ``int0``/``int4 == 1``, so the other line
        may hold anything (e.g. an uninitialised double printed by ``%f``); re-emitting the
        stored token keeps a read/write cycle byte-identical.  A changed point is written
        with ``WritePoint``.
        """
        if token is not None and "\r" not in token and "\n" not in token:
            raw = token.encode("latin-1")
            try:
                parsed = _Tokens(b"", strict=False).point_from(raw)
            except ChfError:
                parsed = ZERO
            if parsed == p:
                self.lines.append(raw)
                return
        self.point(p)

    def blanks(self, n: int) -> None:
        self.lines.extend([b""] * n)


def _write_glyph(o: _Out, g: Glyph) -> None:
    """Glyph ``Write`` methods, vtable slot 24 (03 §7; order re-read at the cited VAs)."""
    match g:
        case PointGlyph():  # 0x100890a0
            o.point(g.pt)
        case SegmentGlyph():  # 0x100897b0
            o.point(g.p0)
            o.point(g.p1)
        case ArcGlyph():  # 0x1007c900
            o.point(g.center)
            o.double(g.radius)
            o.double(g.start_angle)
            o.double(g.end_angle)
        case CircleGlyph():  # 0x1007f130
            o.point(g.center)
            o.double(g.radius)
        case EllipseArcGlyph():  # 0x100806b0
            o.point(g.center)
            o.point(g.major_axis)
            o.double(g.ratio)
            o.double(g.start_param)
            o.double(g.end_param)
        case LwPolylineGlyph():  # 0x10083e70: closed, n, n x (point, bulge)
            o.int(g.closed)
            o.int(len(g.vertices))
            for v in g.vertices:
                o.point(v.pt)
                o.double(v.bulge)
        case SplineGlyph():  # 0x1008b010
            if len(g.control_points) < 4 or len(g.knots) != len(g.control_points) + 4:
                raise ValueError("spline needs >=4 control points and n+4 knots (03 §7)")
            o.int(g.int1)
            o.int(g.int2)
            o.int(len(g.control_points))
            for p in g.control_points:
                o.point(p)
            o.int(len(g.knots))
            for k in g.knots:
                o.double(k)
        case _:
            raise TypeError(f"not a glyph: {g!r}")


def _write_contour(o: _Out, c: Contour, version: int) -> None:
    """``CGlyContour::Write`` ``0x1005ab20`` (03 §6.1, §6.1.1)."""
    if not c.elements:
        raise ValueError("a contour needs at least one glyph (reader rejects 0, 03 §6.1)")
    legacy = _legacy(version)
    o.double(c.precision)
    o.str(GLYPHS)
    if legacy:
        o.blanks(LEGACY_BLANKS_GLYPHS)
    o.double(c.length)
    o.point(c.bbox_min)
    o.point(c.bbox_max)
    o.point(c.start)
    o.point(c.end)
    o.int(len(c.elements))
    for i, el in enumerate(c.elements, 1):
        o.str_int(GLY, i)
        o.int(el.direction)
        o.int(el.glyph.TYPE)
        _write_glyph(o, el.glyph)
        if legacy:
            o.blanks(LEGACY_BLANKS_PER_GLYPH)
    o.str(END_GLYPHS)
    o.int(c.layer)
    o.int(c.int58)
    o.str(CRAFTS)
    if legacy:
        o.blanks(LEGACY_BLANKS_CRAFTS)
    cr = c.crafts
    o.int(cr.compensate_type)
    o.double(cr.compensate_width)
    o.str(PWM)
    o.int(cr.pwm_enable)
    o.int(len(cr.pwm_nodes))
    for a, b in cr.pwm_nodes:
        o.double(a)
        o.double(b)
    o.int(len(cr.pwm_close_pos_ratios))
    for r in cr.pwm_close_pos_ratios:
        o.double(r)
    o.str(END_PWM)
    o.double(cr.double170)
    o.double(cr.double188)
    o.str(GUIDE)
    lead = cr.lead_line
    o.int(lead.type)
    o.double(lead.angle_deg)
    o.double(lead.length)
    if version > 1:
        o.double(0.0 if lead.arc_radius is None else lead.arc_radius)
    o.bool(lead.flag)
    o.str(END_GUIDE)
    if version > 2:
        o.str(COOL)
        cool = cr.cool_pos or []
        o.int(len(cool))
        for p in cool:
            o.double(p)
        o.str(END_COOL)
    o.str(END_CRAFTS)


def _write_group(o: _Out, g: Group, version: int) -> None:
    """``CGlyGroup::Write`` ``0x10074630`` (03 §6.2)."""
    if _legacy(version):
        o.blanks(LEGACY_BLANKS_GROUP)
    o.double(g.length)
    o.point(g.bbox_min)
    o.point(g.bbox_max)
    o.point(g.start)
    o.point(g.end)
    o.int(len(g.children))
    for i, k in enumerate(g.children, 1):
        o.str_int(GROUP_ELEM, i)
        _write_contour(o, k, version)
    o.int(g.layer)
    o.int(g.int58)


def _write_graph(o: _Out, g: Graph, version: int) -> None:
    """Graph ``Write`` methods, vtable slot 24 (03 §6)."""
    legacy = _legacy(version)
    match g:
        case Contour():
            _write_contour(o, g, version)
        case ContourEx():  # 0x100704e0: group body, n, records; both points always written
            _write_group(o, g, version)
            o.int(len(g.links))
            for i, e in enumerate(g.links, 1):
                o.str_int(LINK_INFO, i)
                o.int(e.int0)
                o.int(e.int4)
                o.double(e.d8)
                o.double(e.d10)
                o.double(e.d38)
                o.double(e.d40)
                o.link_point(e.pt18_token, e.pt18)
                o.link_point(e.pt28_token, e.pt28)
        case Scan():  # 0x10091e80
            _write_group(o, g, version)
            o.str(SCAN_PATH)
            if legacy:
                o.blanks(LEGACY_BLANKS_SCAN)
            o.int(len(g.paths))
            for i, p in enumerate(g.paths, 1):
                o.str_int(SCAN_PATH_NO, i)
                _write_contour(o, p, version)
            o.str(END_SCAN_PATH)
        case Group():
            _write_group(o, g, version)
        case Text():  # 0x100a1fe0
            if legacy:
                o.blanks(LEGACY_BLANKS_TEXT)
            o.point(g.position)
            o.double(g.d130)
            o.double(g.d138)
            o.double(g.d140)
            o.wstr(g.text)
            o.str(TEXT_PARA)
            o.wstr(g.font)
            o.double(g.font_d0)
            o.double(g.font_d1)
            o.double(g.font_d2)
            o.str(END_TEXT_PARA)
            if version >= 4:
                outline = g.outline if g.outline is not None else Group(layer=g.layer, int58=g.int58)
                _write_group(o, outline, version)
            else:
                o.int(g.layer)
                o.int(g.int58)
        case _:
            raise TypeError(f"not a graph: {g!r}")


def write_chf(
    doc: ChfDocument,
    *,
    version: int | None = None,
    zero_style: ZeroStyle = "file",
    encoding: str = ENCODING,
) -> bytes:
    """Serialise a document to ``.chf`` bytes (03 §4.1, §5-§8).

    ``version`` defaults to ``doc.version``; 5 is the current writer
    (``0x100ddf89``).  Versions 1-4 invert the reader gates of 03 §9 (the
    reserved lines are written as empty lines, as ``WriteStr(NULL)`` would).
    UNVERIFIED: no v1-v4 writer exists in the shipped DLL; v4 output is
    confirmed only by the four byte-identical v4 samples.  Cached geometry
    (``length``, bbox, ``start``, ``end``) is written as stored.
    """
    v = doc.version if version is None else version
    if not 1 <= v <= CURRENT_VERSION:
        raise ValueError(f"cannot write .chf version {v} (supported 1..{CURRENT_VERSION})")
    o = _Out(zero_style, encoding)
    o.str(MAGIC)  # OpenForWrite 0x100da450
    o.int(v)  # WriteInt([this+0x970])
    o.str(BEGIN_GRAPHS)  # WriteGraphs 0x100a52a0
    if _legacy(v):
        o.blanks(LEGACY_BLANKS_BEGIN_GRAPHS)
    o.int(len(doc.graphs))
    for i, g in enumerate(doc.graphs, 1):
        o.str_int(GRAPH_NO, i)
        o.int(g.TYPE)
        _write_graph(o, g, v)
    o.str(END_GRAPHS)
    o.bool(doc.trailer_bool)
    o.double(doc.trailer_double)
    o.point(doc.trailer_pt1)
    o.point(doc.trailer_pt2)
    o.str(EOF_MARK)  # WriteEof 0x100d9a30
    return b"\r\n".join(o.lines) + b"\r\n"


def save_chf(doc: ChfDocument, path: str | Path, **kwargs: Any) -> None:
    """Write a document to disk atomically-ish (temp file + rename); see :func:`write_chf`."""
    p = Path(path)
    data = write_chf(doc, **kwargs)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)


# ---------------------------------------------------------------------------
# JSON export (same structure as tools/chf_parse.py)
# ---------------------------------------------------------------------------


def _pt(p: Sequence[float]) -> list[float]:
    return [p[0], p[1]]


def _glyph_dict(g: Glyph) -> dict[str, Any]:
    name = GLYPH_NAMES[g.TYPE]
    match g:
        case PointGlyph():
            return {"type": name, "pt": _pt(g.pt)}
        case SegmentGlyph():
            return {"type": name, "p0": _pt(g.p0), "p1": _pt(g.p1)}
        case ArcGlyph():
            return {"type": name, "center": _pt(g.center), "radius": g.radius,
                    "start_angle": g.start_angle, "end_angle": g.end_angle}  # fmt: skip
        case CircleGlyph():
            return {"type": name, "center": _pt(g.center), "radius": g.radius}
        case EllipseArcGlyph():
            return {"type": name, "center": _pt(g.center), "major_axis": _pt(g.major_axis),
                    "ratio": g.ratio, "start_param": g.start_param, "end_param": g.end_param}  # fmt: skip
        case LwPolylineGlyph():
            return {"type": name, "closed": g.closed,
                    "vertices": [{"pt": _pt(v.pt), "bulge": v.bulge} for v in g.vertices]}  # fmt: skip
        case SplineGlyph():
            return {"type": name, "int1": g.int1, "int2": g.int2, "degree": SplineGlyph.DEGREE,
                    "control_points": [_pt(p) for p in g.control_points], "knots": list(g.knots)}  # fmt: skip
    raise TypeError(f"not a glyph: {g!r}")


def _contour_dict(c: Contour) -> dict[str, Any]:
    glyphs = []
    for el in c.elements:
        d = _glyph_dict(el.glyph)
        d["direction"] = el.direction
        d["type_id"] = int(el.glyph.TYPE)
        glyphs.append(d)
    cr = c.crafts
    lead: dict[str, Any] = {"type": cr.lead_line.type, "angle_deg": cr.lead_line.angle_deg,
                            "length": cr.lead_line.length}  # fmt: skip
    if cr.lead_line.arc_radius is not None:
        lead["arc_radius"] = cr.lead_line.arc_radius
    lead["flag"] = cr.lead_line.flag
    crafts: dict[str, Any] = {
        "compensate_type": cr.compensate_type,
        "compensate_width": cr.compensate_width,
        "pwm_enable": cr.pwm_enable,
        "pwm_nodes": [[a, b] for a, b in cr.pwm_nodes],
        "pwm_close_pos_ratios": list(cr.pwm_close_pos_ratios),
        "double170": cr.double170,
        "double188": cr.double188,
        "lead_line": lead,
    }
    if cr.cool_pos is not None:
        crafts["cool_pos"] = list(cr.cool_pos)
    return {
        "type": "contour",
        "precision": c.precision,
        "length": c.length,
        "bbox_min": _pt(c.bbox_min),
        "bbox_max": _pt(c.bbox_max),
        "start": _pt(c.start),
        "end": _pt(c.end),
        "glyphs": glyphs,
        "layer": c.layer,
        "int58": c.int58,
        "crafts": crafts,
    }


def _group_dict(g: Group, name: str = "group") -> dict[str, Any]:
    return {
        "type": name,
        "length": g.length,
        "bbox_min": _pt(g.bbox_min),
        "bbox_max": _pt(g.bbox_max),
        "start": _pt(g.start),
        "end": _pt(g.end),
        "children": [_contour_dict(k) for k in g.children],
        "layer": g.layer,
        "int58": g.int58,
    }


def _link_point_json(token: str | None, flag: int, p: Vec2) -> list[float] | str:
    if token is None:
        token = format_point(p)
    if flag == 1 and "," in token:
        return [float(v) for v in token.split(",")]
    return token


def _graph_dict(g: Graph) -> dict[str, Any]:
    match g:
        case Contour():
            return _contour_dict(g)
        case ContourEx():
            d = _group_dict(g, "contour_ex")
            d["links"] = [
                {"int0": e.int0, "int4": e.int4, "d8": e.d8, "d10": e.d10, "d38": e.d38, "d40": e.d40,
                 "pt18": _link_point_json(e.pt18_token, e.int0, e.pt18),
                 "pt28": _link_point_json(e.pt28_token, e.int4, e.pt28)}  # fmt: skip
                for e in g.links
            ]
            return d
        case Scan():
            d = _group_dict(g, "scan")
            d["paths"] = [_contour_dict(p) for p in g.paths]
            return d
        case Group():
            return _group_dict(g)
        case Text():
            d = {"type": "text", "position": _pt(g.position), "d130": g.d130, "d138": g.d138,
                 "d140": g.d140, "text": g.text, "font": g.font, "font_d0": g.font_d0,
                 "font_d1": g.font_d1, "font_d2": g.font_d2}  # fmt: skip
            if g.outline is not None:
                d["outline"] = _group_dict(g.outline)
            d["layer"] = g.layer
            d["int58"] = g.int58
            return d
    raise TypeError(f"not a graph: {g!r}")


def to_tool_dict(doc: ChfDocument) -> dict[str, Any]:
    """Return the JSON-able tree ``tools/chf_parse.py`` builds (03 §11), key order included."""
    graphs = []
    for i, g in enumerate(doc.graphs, 1):
        d = _graph_dict(g)
        d["type_id"] = int(g.TYPE)
        d["index"] = i
        graphs.append(d)
    return {
        "magic": MAGIC,
        "version": doc.version,
        "graphs": graphs,
        "trailer_bool": doc.trailer_bool,
        "trailer_double": doc.trailer_double,
        "trailer_pt1": _pt(doc.trailer_pt1),
        "trailer_pt2": _pt(doc.trailer_pt2),
    }


def to_json(doc: ChfDocument) -> str:
    """JSON text identical to ``chf_parse.py --json`` (``indent=1, ensure_ascii=False``)."""
    return json.dumps(to_tool_dict(doc), indent=1, ensure_ascii=False)


# ---------------------------------------------------------------------------
# SVG export (same drawing as tools/chf_parse.py)
# ---------------------------------------------------------------------------

LAYER_COLORS = ["#d62728", "#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd",
                "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f"]  # fmt: skip


def _xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_svg(doc: ChfDocument, title: str = "", show_labels: bool = True) -> str:
    """Render the document as SVG, Y flipped for the Y-up frame (03 §10).

    Output equals ``tools/chf_parse.py``'s ``to_svg`` except that ``&<>`` in
    the title/text labels are XML-escaped here.
    """
    polylines: list[tuple[list[tuple[float, float]], str]] = []
    markers: list[tuple[float, float, str, str]] = []
    allpts: list[Sequence[float]] = []
    for idx, g in enumerate(doc.graphs, 1):
        for c, kind in iter_contours(g):
            color = LAYER_COLORS[c.layer % len(LAYER_COLORS)]
            if kind == "scanpath":
                color = "#999999"
            for el in c.elements:
                for pl in flatten_glyph(el.glyph):
                    polylines.append((pl, color))
                    allpts.extend(pl)
            markers.append((c.start[0], c.start[1], color, f"{idx}" if kind == "self" else ""))
        if isinstance(g, Text):
            markers.append((g.position[0], g.position[1], "#000", f"T{idx}:{g.text}"))
            allpts.append(g.position)
    if not allpts:
        allpts = [(0, 0), (10, 10)]
    xs = [p[0] for p in allpts]
    ys = [p[1] for p in allpts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w, h = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
    margin = 0.06 * max(w, h) + 1.0
    vb = (minx - margin, -(maxy + margin), w + 2 * margin, h + 2 * margin)
    sw = max(w, h) / 400.0
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb[0]:.4f} {vb[1]:.4f} {vb[2]:.4f} {vb[3]:.4f}" '
        f'width="800" height="{800 * vb[3] / vb[2]:.0f}">',
        f'<rect x="{vb[0]:.4f}" y="{vb[1]:.4f}" width="{vb[2]:.4f}" height="{vb[3]:.4f}" fill="white"/>',
    ]
    if title:
        out.append(
            f'<text x="{vb[0] + sw * 4:.4f}" y="{vb[1] + sw * 12:.4f}" font-size="{sw * 10:.4f}" '
            f'fill="#444">{_xml(title)}</text>'
        )
    for pts, color in polylines:
        if len(pts) == 1:
            x, y = pts[0]
            out.append(f'<circle cx="{x:.4f}" cy="{-y:.4f}" r="{sw * 2:.4f}" fill="{color}"/>')
            continue
        d = " ".join(f"{p[0]:.4f},{-p[1]:.4f}" for p in pts)
        out.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="{sw:.4f}"/>')
    for x, y, color, text in markers:
        out.append(
            f'<circle cx="{x:.4f}" cy="{-y:.4f}" r="{sw * 1.5:.4f}" fill="none" stroke="{color}" '
            f'stroke-width="{sw * 0.5:.4f}"/>'
        )
        if show_labels and text:
            out.append(
                f'<text x="{x + sw * 2:.4f}" y="{-y - sw * 2:.4f}" font-size="{sw * 6:.4f}" '
                f'fill="{color}">{_xml(text)}</text>'
            )
    out.append("</svg>")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Self check and CLI
# ---------------------------------------------------------------------------


def check_document(doc: ChfDocument) -> list[str]:
    """Recompute every contour's cached geometry and list mismatches (03 §11 ``--check``)."""
    problems = []
    for idx, g in enumerate(doc.graphs, 1):
        for c, kind in iter_contours(g):
            for p in check_contour(c):
                problems.append(f"graph {idx} ({kind}): {p}")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m nexcut.io.chf``: summary, ``--json``, ``--svg``, ``--check``, ``--rewrite``."""
    ap = argparse.ArgumentParser(prog="python -m nexcut.io.chf", description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--json", type=Path, help="write the JSON dump (single input)")
    ap.add_argument("--svg", type=Path, help="write an SVG rendering (single input)")
    ap.add_argument("--rewrite", type=Path, help="re-write the file with the writer (single input)")
    ap.add_argument("--check", action="store_true", help="recompute cached geometry and compare")
    ap.add_argument("--lenient", action="store_true", help="do not check marker lines (like the DLL)")
    args = ap.parse_args(argv)
    if (args.json or args.svg or args.rewrite) and len(args.files) != 1:
        ap.error("--json/--svg/--rewrite take exactly one input file")
    rc = 0
    for f in args.files:
        try:
            doc = load_chf(f, strict=not args.lenient)
        except ChfError as e:
            print(f"{f}: PARSE ERROR: {e}")
            rc = 1
            continue
        kinds = ", ".join(GRAPH_NAMES[g.TYPE] for g in doc.graphs[:8])
        more = " ..." if len(doc.graphs) > 8 else ""
        print(f"{f}: version {doc.version}, {len(doc.graphs)} graph(s): {kinds}{more}")
        if args.check:
            problems = check_document(doc)
            for p in problems:
                print(f"  CHECK {p}")
            print(f"  check: {'OK' if not problems else 'MISMATCH'}")
            rc |= 1 if problems else 0
        if args.json:
            args.json.write_text(to_json(doc), encoding="utf-8")
        if args.svg:
            args.svg.write_text(to_svg(doc, title=f.stem), encoding="utf-8")
        if args.rewrite:
            save_chf(doc, args.rewrite)
    return rc


if __name__ == "__main__":
    sys.exit(main())
