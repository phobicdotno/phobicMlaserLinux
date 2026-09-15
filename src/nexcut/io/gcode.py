"""G-code / NC import (``*.nc;*.txt;*.cnc;*.g``) into the ``.chf`` glyph model.

Replaces ``CGCodeFileParse`` of ``Module/CADModule.dll`` (09 §3.7, file filter
``mf149``).  The original is a GOLD-parser grammar; its productions are stored
as UTF-16 strings in CADModule ``.rdata`` 0x10e384..0x10ef70 (EVIDENCE, 09 §3.7,
re-extracted for this module)::

    <line>       ::= | <n> | <cmd> | <n> <cmd>
    <cmd>        ::= <fs> | <Coordinate> <fs> | <Coordinate> | <g> <Coordinate> <fs>
                   | <g> <Coordinate> | <g> <p> | <g> <t> | <q> | <g> | <lp> | <l> | <d> | <m>
    <Coordinate> ::= <abc> | <xyzijkru> <abc> | <xyzijkru>
    <xyzijkru>   ::= <xyz> <u> | <xyz> <ijkr> | <ijkr> | <xyz>
    <abc>        ::= <a> <b> <c> | <b> <c> | <a> <c> | <a> <b> | <c> | <b> | <a>
    <fs>         ::= <s> | <f> | <f> <s>
    <ijkr>       ::= <r> | <i> <j> <k> | <i> <k> | <i> <j>
    <xyz>        ::= <x> <y> <z> | <y> <z> | <x> <z> | <x> <y> | <z> | <y> | <x>
    <t> ::= T DecLiteral          <u>|<s>|<f> ::= U|S|F FloatLiteral | DecLiteral
    <x>..<c>, <i>..<r> ::= letter <sign num>
    <d>|<p>|<q>|<m>|<g>|<n> ::= letter DecLiteral
    <lp> ::= LP DecLiteral        <l> ::= L DecLiteral DecLiteral
    <sign num>   ::= [sign] FloatLiteral | [sign] DecLiteral

plus the terminals ``;`` (line comment), ``Whitespace``, ``NewLine``, ``Comment``
and the GOLD engine messages ("Syntax error:", "Lexical error:", "The comment has
no end, it was started but not finished").  :func:`parse_line` is a recursive-
descent parser of exactly that grammar (``strict=True``); ``strict=False`` also
accepts words in any order, several G/M words per line, ``(...)`` comments,
``%`` and ``O`` lines, as common G-code writers emit them.

Semantic error strings (EVIDENCE, 09 §3.7 and CADModule ASCII strings at
0x10f170..0x10f510) are raised verbatim as :class:`GCodeError` messages:
``G code format is error!``, ``Arc chord is too small!``, ``Arc radius is error!``,
``M02 is not in main function``, ``M17 can not in main function``,
``Subfunction is redefined``, ``Subfunction can not define in function``,
``Sub program can not call LP instruct``, ``sub-functions can not be found``,
``Command is not in a function``.

Program model (INFERENCE from those messages, UNVERIFIED): the main function
runs from the top to ``M02``; after it, ``LP n`` opens subprogram *n*, closed by
``M17``; ``L n m`` calls subprogram *n* *m* times.  ``LP`` inside the main
function is "Subfunction can not define in function"; ``LP`` inside a
subprogram is "Sub program can not call LP instruct"; other commands after
``M02`` outside a subprogram are "Command is not in a function".

Motion semantics are standard RS-274 (UNVERIFIED against the original): G0 rapid
(ends a contour), G1 line, G2/G3 CW/CCW arc with incremental I/J or R (negative
R = major arc), G90/G91 absolute/incremental, G20/G21 inch/mm, G92 set position,
G4 dwell ignored.  In lenient mode the decimal codes G90.1/G91.1 select absolute /
incremental arc centres (CAM post-processors such as Fusion 360's emit
``G90 G94 G91.1 G40 G49 G17``; truncating ``G91.1`` to ``G91`` switched the whole
program to incremental distance mode - import-ui fidelity review); other decimal G
codes are ignored with a warning.  Numbers that overflow to inf are
``G code format is error!``.  F/S/T/D/Q/U/A/B/C/Z words are parsed and ignored.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from nexcut.io import cp936
from nexcut.model.glyph import ArcGlyph, LwPolylineGlyph, LwPolyVertex, Vec2
from nexcut.model.graph import ChfDocument, ContourElement
from nexcut.ops.import_gates import DEFAULT_PRECISION, build_contour

__all__ = [
    "ERR_ARC_CHORD",
    "ERR_ARC_RADIUS",
    "ERR_FORMAT",
    "ERR_LP_IN_MAIN",
    "ERR_LP_IN_SUB",
    "ERR_M02_IN_SUB",
    "ERR_M17_IN_MAIN",
    "ERR_NOT_IN_FUNCTION",
    "ERR_SUB_NOT_FOUND",
    "ERR_SUB_REDEFINED",
    "UNVERIFIED",
    "GCodeError",
    "GCodeImportOptions",
    "GCodeImportResult",
    "Line",
    "parse_line",
    "parse_program",
    "read_gcode",
]

ERR_FORMAT = "G code format is error!"
ERR_ARC_CHORD = "Arc chord is too small!"
ERR_ARC_RADIUS = "Arc radius is error!"
ERR_M02_IN_SUB = "M02 is not in main function"
ERR_M17_IN_MAIN = "M17 can not in main function"
ERR_SUB_REDEFINED = "Subfunction is redefined"
ERR_LP_IN_MAIN = "Subfunction can not define in function"
ERR_LP_IN_SUB = "Sub program can not call LP instruct"
ERR_SUB_NOT_FOUND = "sub-functions can not be found"
ERR_NOT_IN_FUNCTION = "Command is not in a function"
ERR_COMMENT = "The comment has no end, it was started but not finished"

UNVERIFIED: tuple[str, ...] = (
    "G-code program model: LP n defines a subprogram after M02, M17 ends it, L n m calls "
    "subprogram n m times (argument order), M30 treated as M02",
    "G-code: I/J incremental from the arc start; arc radius mismatch tolerance 0.01 mm; "
    "chord-too-small threshold 1e-6 mm for R arcs",
    "G-code: initial modal state G0/G90/G21 at (0,0); Z/A/B/C/U ignored; G17 plane only",
    "G-code: G0 (and any non-cutting word such as M codes) does not end a contour unless "
    "the position changes by a rapid move; laser M codes (M07/M08 ...) are not interpreted",
    "G-code: file bytes decoded as cp936 (Chinese comments)",
    "G-code: G90.1/G91.1 (absolute/incremental I/J) honoured in lenient mode; the original's "
    "grammar has no decimal G codes",
)

ARC_RADIUS_TOL = 0.01
"""mm - start/end radius mismatch accepted for I/J arcs (UNVERIFIED)."""

MIN_CHORD = 1e-6
"""mm - smallest chord of an R-form arc (UNVERIFIED)."""

MAX_CALL_DEPTH = 32
"""Recursion guard for subprogram calls (no evidence of the original's limit)."""


class GCodeError(ValueError):
    """A lexical, syntax or semantic error; ``message`` is the original's text where one exists."""

    def __init__(self, message: str, line: int | None = None, kind: str = "semantic") -> None:
        self.message = message
        self.line = line
        self.kind = kind
        where = f" (line {line})" if line is not None else ""
        super().__init__(f"{message}{where}")


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

_WORD_LETTERS = frozenset("XYZIJKRUABCFSTDPQMGNL")


@dataclass(slots=True)
class _Tok:
    kind: str  # letter ("X", "LP", ...), "sign", "dec", "float"
    text: str
    col: int


def _lex(text: str, line_no: int, strict: bool) -> list[_Tok]:
    toks: list[_Tok] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\x0c":
            i += 1
        elif c == ";":
            break
        elif c == "(" and not strict:
            j = text.find(")", i)
            if j < 0:
                raise GCodeError(ERR_COMMENT, line_no, "comment")
            i = j + 1
        elif c in "+-":
            toks.append(_Tok("sign", c, i))
            i += 1
        elif c.isdigit() or c == ".":
            j = i
            while j < n and text[j].isdigit():
                j += 1
            is_float = False
            if j < n and text[j] == ".":
                is_float = True
                j += 1
                while j < n and text[j].isdigit():
                    j += 1
            lit = text[i:j]
            if lit == ".":
                raise GCodeError(f"Lexical error: {lit!r}", line_no, "lexical")
            toks.append(_Tok("float" if is_float else "dec", lit, i))
            i = j
        else:
            u = c.upper() if not strict else c
            if (
                u == "L"
                and i + 1 < n
                and (text[i + 1].upper() if not strict else text[i + 1]) == "P"
            ):
                toks.append(_Tok("LP", "LP", i))
                i += 2
            elif u in _WORD_LETTERS:
                toks.append(_Tok(u, c, i))
                i += 1
            elif not strict and u in "O%/":
                if u == "/":  # block delete: skip the line
                    return []
                return [] if u in "O%" and not toks else toks
            else:
                raise GCodeError(f"Lexical error: {c!r}", line_no, "lexical")
    return toks


# ---------------------------------------------------------------------------
# Parser (recursive descent over the grammar in the module docstring)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Line:
    """One parsed line: words in source order as ``(letter, value)``; ``L`` carries two values."""

    number: int
    n: int | None = None
    words: list[tuple[str, float]] = field(default_factory=list)
    l_call: tuple[int, int] | None = None

    def get(self, letter: str) -> float | None:
        for k, v in self.words:
            if k == letter:
                return v
        return None

    def all(self, letter: str) -> list[float]:
        return [v for k, v in self.words if k == letter]

    @property
    def is_empty(self) -> bool:
        return self.n is None and not self.words and self.l_call is None


class _Parser:
    def __init__(self, toks: list[_Tok], line_no: int) -> None:
        self.t = toks
        self.i = 0
        self.line_no = line_no
        self.out = Line(line_no)

    def peek(self, k: int = 0) -> _Tok | None:
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def at(self, kind: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == kind

    def error(self, expected: str) -> GCodeError:
        tok = self.peek()
        got = "EOF" if tok is None else tok.text
        return GCodeError(
            f"Syntax error: Encountered {got!r}, but expected {expected}", self.line_no, "syntax"
        )

    def dec(self) -> int:
        tok = self.peek()
        if tok is None or tok.kind != "dec":
            raise self.error("DecLiteral")
        self.i += 1
        if len(tok.text.lstrip("0")) > 15:  # float(int) would overflow / lose the value
            raise GCodeError(ERR_FORMAT, self.line_no, "lexical")
        return int(tok.text)

    def number(self, allow_sign: bool) -> float:
        sign = 1.0
        tok = self.peek()
        if allow_sign and tok is not None and tok.kind == "sign":
            sign = -1.0 if tok.text == "-" else 1.0
            self.i += 1
            tok = self.peek()
        if tok is None or tok.kind not in ("dec", "float"):
            raise self.error("FloatLiteral or DecLiteral")
        self.i += 1
        value = sign * float(tok.text)
        if not math.isfinite(value):  # e.g. a 400-digit literal: inf would reach the geometry
            raise GCodeError(ERR_FORMAT, self.line_no, "lexical")
        return value

    def word(self, letter: str, value: str) -> None:
        """``letter DecLiteral`` / ``letter FloatLiteral`` / ``letter <sign num>``."""
        self.i += 1  # the letter
        if value == "dec":
            self.out.words.append((letter, float(self.dec())))
        elif value == "float":
            self.out.words.append((letter, self.number(allow_sign=False)))
        else:
            self.out.words.append((letter, self.number(allow_sign=True)))

    def opt(self, letter: str, value: str = "sign") -> bool:
        if self.at(letter):
            self.word(letter, value)
            return True
        return False

    # <line>
    def line(self) -> Line:
        if self.at("N"):
            self.i += 1
            self.out.n = self.dec()
        if self.peek() is not None:
            self.cmd()
        if self.peek() is not None:
            raise self.error("NewLine")
        return self.out

    # <cmd>
    def cmd(self) -> None:
        tok = self.peek()
        assert tok is not None
        k = tok.kind
        if k == "G":
            self.word("G", "dec")
            if self.at("P"):
                self.word("P", "dec")
            elif self.at("T"):
                self.word("T", "dec")
            elif self.coordinate_start():
                self.coordinate()
                self.fs()
        elif k in ("F", "S"):
            self.fs()
        elif k == "Q":
            self.word("Q", "dec")
        elif k == "LP":
            self.word("LP", "dec")
        elif k == "L":
            self.i += 1
            a = self.dec()
            b = self.dec()
            self.out.l_call = (a, b)
        elif k == "D":
            self.word("D", "dec")
        elif k == "M":
            self.word("M", "dec")
        elif self.coordinate_start():
            self.coordinate()
            self.fs()
        else:
            raise self.error("a command")

    def coordinate_start(self) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind in (
            "X",
            "Y",
            "Z",
            "I",
            "J",
            "K",
            "R",
            "U",
            "A",
            "B",
            "C",
        )

    # <Coordinate> ::= <xyzijkru> [<abc>] | <abc>
    def coordinate(self) -> None:
        if not self.xyzijkru():
            if not self.abc():
                raise self.error("<Coordinate>")
            return
        self.abc()

    def xyzijkru(self) -> bool:
        had_xyz = self.xyz()
        if had_xyz and self.at("U"):
            self.word("U", "float")
            return True
        if self.ijkr():
            return True
        return had_xyz

    def xyz(self) -> bool:
        got = [self.opt(c) for c in "XYZ"]
        return any(got)

    def ijkr(self) -> bool:
        if self.opt("R"):
            return True
        if not self.at("I"):
            return False
        self.word("I", "sign")
        has_j = self.opt("J")
        has_k = self.opt("K")
        if not (has_j or has_k):
            raise self.error("J or K")
        return True

    def abc(self) -> bool:
        return any([self.opt(c) for c in "ABC"])

    def fs(self) -> None:
        if self.at("F"):
            self.word("F", "float")
        if self.at("S"):
            self.word("S", "float")


def _parse_lenient(toks: list[_Tok], line_no: int) -> Line:
    p = _Parser(toks, line_no)
    out = p.out
    while p.peek() is not None:
        tok = p.peek()
        assert tok is not None
        if tok.kind == "N":
            p.i += 1
            out.n = p.dec()
        elif tok.kind == "L":
            p.i += 1
            a = p.dec()
            b = p.dec() if p.at("dec") else 1
            out.l_call = (a, b)
        elif tok.kind in ("G", "M", "D", "P", "Q", "T", "LP"):
            p.i += 1
            out.words.append((tok.kind, p.number(allow_sign=False)))
        elif tok.kind in _WORD_LETTERS:
            p.i += 1
            out.words.append((tok.kind, p.number(allow_sign=True)))
        else:
            raise p.error("a word letter")
    return out


def parse_line(text: str, line_no: int = 1, *, strict: bool = False) -> Line:
    """Parse one source line (module docstring grammar; ``strict`` = the original's grammar)."""
    toks = _lex(text, line_no, strict)
    if strict:
        return _Parser(toks, line_no).line()
    return _parse_lenient(toks, line_no)


def parse_program(text: str, *, strict: bool = False) -> list[Line]:
    """Parse every line of a program; a ``(`` comment may not span lines (``ERR_COMMENT``)."""
    return [parse_line(t, i + 1, strict=strict) for i, t in enumerate(text.splitlines())]


# ---------------------------------------------------------------------------
# Program structure and interpretation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GCodeImportOptions:
    """Import switches."""

    strict: bool = False
    """Enforce the original grammar exactly (word order, one G per line)."""
    precision: float = DEFAULT_PRECISION
    layer: int = 0


@dataclass(slots=True)
class GCodeImportResult:
    """Imported document plus statistics."""

    document: ChfDocument
    lines: int = 0
    g_counts: Counter[float] = field(default_factory=Counter)
    """G word counts; integral codes keyed as ``int``, decimal codes (``91.1``) as ``float``."""
    m_counts: Counter[int] = field(default_factory=Counter)
    subprograms: dict[int, int] = field(default_factory=dict)
    """Subprogram id -> line number of its ``LP``."""
    warnings: list[str] = field(default_factory=list)


_CUT_MODES = (1, 2, 3)
_KNOWN_G = frozenset(
    {0, 1, 2, 3, 4, 17, 20, 21, 40, 41, 42, 53, 54, 55, 56, 57, 58, 59, 90, 91, 92}
)


def _structure(lines: list[Line]) -> tuple[list[Line], dict[int, list[Line]], dict[int, int]]:
    """Split into main body and subprograms, raising the original's structure errors."""
    main: list[Line] = []
    subs: dict[int, list[Line]] = {}
    sub_lines: dict[int, int] = {}
    state = "main"  # main | outside | sub
    current: int | None = None
    for ln in lines:
        if ln.is_empty:
            continue
        lp = ln.get("LP")
        ms = [int(v) for v in ln.all("M")]
        if lp is not None:
            if state == "main":
                raise GCodeError(ERR_LP_IN_MAIN, ln.number)
            if state == "sub":
                raise GCodeError(ERR_LP_IN_SUB, ln.number)
            sid = int(lp)
            if sid in subs:
                raise GCodeError(ERR_SUB_REDEFINED, ln.number)
            subs[sid] = []
            sub_lines[sid] = ln.number
            current = sid
            state = "sub"
            continue
        if state == "main":
            if 17 in ms:
                raise GCodeError(ERR_M17_IN_MAIN, ln.number)
            main.append(ln)
            if 2 in ms or 30 in ms:
                state = "outside"
        elif state == "outside":
            if ln.words or ln.l_call is not None:
                raise GCodeError(ERR_NOT_IN_FUNCTION, ln.number)
        else:
            if 2 in ms or 30 in ms:
                raise GCodeError(ERR_M02_IN_SUB, ln.number)
            assert current is not None
            subs[current].append(ln)
            if 17 in ms:
                state = "outside"
                current = None
    return main, subs, sub_lines


class _Machine:
    def __init__(
        self, opts: GCodeImportOptions, res: GCodeImportResult, subs: dict[int, list[Line]]
    ) -> None:
        self.o = opts
        self.res = res
        self.subs = subs
        self.pos = (0.0, 0.0)
        self.mode = 0
        self.absolute = True
        self.ij_absolute = False
        self.scale = 1.0
        self.run: list[Vec2] = []
        self.elements: list[ContourElement] = []

    def warn(self, msg: str) -> None:
        if msg not in self.res.warnings:
            self.res.warnings.append(msg)

    def end_contour(self) -> None:
        self.flush_run()
        if self.elements:
            self.res.document.graphs.append(
                build_contour(self.elements, layer=self.o.layer, precision=self.o.precision)
            )
        self.elements = []

    def flush_run(self) -> None:
        if len(self.run) >= 2:
            self.elements.append(
                ContourElement(LwPolylineGlyph(0, [LwPolyVertex(p) for p in self.run]))
            )
        self.run = []

    def execute(self, lines: list[Line], depth: int = 0) -> None:
        if depth > MAX_CALL_DEPTH:
            raise GCodeError(f"subprogram calls nested deeper than {MAX_CALL_DEPTH}")
        for ln in lines:
            self.line(ln, depth)

    def line(self, ln: Line, depth: int) -> None:
        gs: list[int] = []
        for v in ln.all("G"):
            if v != int(v):  # decimal G code (lenient mode only: the grammar's <g> is a DecLiteral)
                self.res.g_counts[v] += 1
                if abs(v - 90.1) < 1e-9:
                    self.ij_absolute = True
                elif abs(v - 91.1) < 1e-9:
                    self.ij_absolute = False
                else:
                    self.warn(f"G{v:g} ignored")
                continue
            gs.append(int(v))
        for m in ln.all("M"):
            self.res.m_counts[int(m)] += 1
        motion: int | None = None
        for g in gs:
            self.res.g_counts[g] += 1
            if g not in _KNOWN_G:
                if self.o.strict:
                    raise GCodeError(ERR_FORMAT, ln.number)
                self.warn(f"G{g} ignored")
            elif g in (0, 1, 2, 3):
                motion = g
            elif g == 90:
                self.absolute = True
            elif g == 91:
                self.absolute = False
            elif g == 20:
                self.scale = 25.4
            elif g == 21:
                self.scale = 1.0
        if 92 in gs:
            x, y = ln.get("X"), ln.get("Y")
            self.end_contour()
            self.pos = (
                self.pos[0] if x is None else x * self.scale,
                self.pos[1] if y is None else y * self.scale,
            )
            return
        if ln.l_call is not None:
            sid, count = ln.l_call
            if sid not in self.subs:
                raise GCodeError(ERR_SUB_NOT_FOUND, ln.number)
            for _ in range(max(0, count)):
                self.execute(self.subs[sid], depth + 1)
            return
        if motion is not None:
            self.mode = motion
        has_xy = ln.get("X") is not None or ln.get("Y") is not None
        has_ij = ln.get("I") is not None or ln.get("J") is not None or ln.get("R") is not None
        if 4 in gs or not (has_xy or (has_ij and self.mode in (2, 3))):
            return
        target = self.target(ln)
        if self.mode == 0:
            if target != self.pos:
                self.end_contour()
            self.pos = target
        elif self.mode == 1:
            if not self.run:
                self.run = [Vec2(*self.pos)]
            if math.dist(target, self.run[-1]) > 1e-12:
                self.run.append(Vec2(*target))
            self.pos = target
        else:
            self.arc(ln, target, ccw=self.mode == 3)

    def target(self, ln: Line) -> tuple[float, float]:
        x, y = ln.get("X"), ln.get("Y")
        if self.absolute:
            return (
                self.pos[0] if x is None else x * self.scale,
                self.pos[1] if y is None else y * self.scale,
            )
        return (
            self.pos[0] + (0.0 if x is None else x * self.scale),
            self.pos[1] + (0.0 if y is None else y * self.scale),
        )

    def arc(self, ln: Line, end: tuple[float, float], ccw: bool) -> None:
        start = self.pos
        r_word = ln.get("R")
        if r_word is not None:
            r = r_word * self.scale
            chord = math.dist(start, end)
            if chord < MIN_CHORD:
                raise GCodeError(ERR_ARC_CHORD, ln.number)
            half = chord / 2.0
            if abs(r) < half - 1e-9:
                raise GCodeError(ERR_ARC_RADIUS, ln.number)
            h = math.sqrt(max(r * r - half * half, 0.0))
            mx, my = (start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0
            ux, uy = (end[0] - start[0]) / chord, (end[1] - start[1]) / chord
            # centre left of the chord for a CCW minor arc (or CW major arc)
            left = ccw == (r > 0)
            sgn = 1.0 if left else -1.0
            centre = (mx - uy * h * sgn, my + ux * h * sgn)
            radius = abs(r)
        else:
            i = (ln.get("I") or 0.0) * self.scale
            j = (ln.get("J") or 0.0) * self.scale
            if self.ij_absolute:  # G90.1: I/J are the absolute centre (RS-274 / LinuxCNC)
                ci, cj = ln.get("I"), ln.get("J")
                centre = (
                    start[0] if ci is None else i,
                    start[1] if cj is None else j,
                )
            else:
                centre = (start[0] + i, start[1] + j)
            radius = math.dist(start, centre)
            if radius < MIN_CHORD or abs(math.dist(end, centre) - radius) > ARC_RADIUS_TOL:
                raise GCodeError(ERR_ARC_RADIUS, ln.number)
        a0 = math.atan2(start[1] - centre[1], start[0] - centre[0])
        a1 = math.atan2(end[1] - centre[1], end[0] - centre[0])
        if math.dist(start, end) < 1e-9:
            sweep = 2 * math.pi
        else:
            sweep = (a1 - a0) % (2 * math.pi) if ccw else (a0 - a1) % (2 * math.pi)
        self.flush_run()
        c = Vec2(*centre)
        if ccw:
            self.elements.append(ContourElement(ArcGlyph(c, radius, a0, a0 + sweep), 1))
        else:  # CW travel: the CCW arc over the same points, traversed reversed (03 §6.1 dir flag)
            self.elements.append(ContourElement(ArcGlyph(c, radius, a0 - sweep, a0), -1))
        self.pos = end


def read_gcode(
    source: str | Path | bytes | IO[bytes], options: GCodeImportOptions | None = None
) -> GCodeImportResult:
    """Parse and interpret a G-code program into a :class:`ChfDocument`.

    ``str`` is taken as a path; pass ``bytes`` for in-memory text.
    """
    if isinstance(source, (str, Path)):
        data = Path(source).read_bytes()
    elif isinstance(source, bytes):
        data = source
    else:
        data = source.read()
    opts = options or GCodeImportOptions()
    text = cp936.decode(data)
    lines = parse_program(text, strict=opts.strict)
    main, subs, sub_lines = _structure(lines)
    res = GCodeImportResult(document=ChfDocument(), lines=len(lines), subprograms=sub_lines)
    machine = _Machine(opts, res, subs)
    machine.execute(main)
    machine.end_contour()
    return res
