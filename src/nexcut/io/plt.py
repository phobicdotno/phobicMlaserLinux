"""HPGL/2 (``*.plt``) import into the ``.chf`` glyph model.

Replaces ``CPltFileParse``/``CPltFileRead``/``CPltInputFile`` and the
``PltParse::CPlt*`` entities of ``Module/CADModule.dll`` (09 §3.7, file filter
``mf149`` ``*.plt``).

Mnemonic set (EVIDENCE, 09 §3.7, extracted from CADModule ``.rdata``)::

    AA AR AT BR BZ CI CO DF DT EA EP ER EW IN IP KL LB OD OE OH OI OP OS
    PA PD PE PG PM PR PU RA RR RT SC SM SP WG

Only these are interpreted; any other mnemonic is counted and ignored.  The
PltParse entity classes (``CPltSegment2d``, ``CPltArc2d``, ``CPltCircle2d``,
``CPltEllipseArc2d``, ``CPltRect2d``, ``CPltNubrs2d``) say which geometry the
original keeps: segments, arcs, circles, rectangles and Bezier/NURBS curves.
The semantics of each mnemonic follow the HP-GL/2 reference; nothing about the
original's dialect handling was traced (09 §8 q.7), so:

* units: 40 plotter units per mm (the HP-GL default 1 plu = 0.025 mm) - UNVERIFIED,
* ``IP`` default P1/P2 = (0, 0)/(10000, 10000) plu (device dependent) - UNVERIFIED,
* ``LB`` labels are recorded but not rendered - UNVERIFIED (09 §8 q.7),
* fill primitives ``RA/RR/WG`` are imported as their outline, polygon-mode
  buffers are emitted as closed outlines on ``EP`` - UNVERIFIED,
* the pen number is not a process layer unless ``pen_to_layer`` is set - UNVERIFIED.

Continuous pen-down drawing becomes one contour (line runs -> lwpolyline glyph,
arcs -> arc glyphs with the direction flag, Beziers -> type-7 splines); ``CI``,
``EA/ER/RA/RR`` and ``EW/WG`` each make a closed contour of their own.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from nexcut.model.glyph import (
    ArcGlyph,
    CircleGlyph,
    Glyph,
    LwPolylineGlyph,
    LwPolyVertex,
    SplineGlyph,
    Vec2,
)
from nexcut.model.graph import ChfDocument, Contour, ContourElement
from nexcut.ops.import_gates import DEFAULT_PRECISION, build_contour

__all__ = [
    "MNEMONICS",
    "PLU_PER_MM",
    "UNVERIFIED",
    "PltError",
    "PltImportOptions",
    "PltImportResult",
    "decode_pe",
    "read_plt",
    "tokenize",
]

MNEMONICS = frozenset(
    "AA AR AT BR BZ CI CO DF DT EA EP ER EW IN IP KL LB OD OE OH OI OP OS "
    "PA PD PE PG PM PR PU RA RR RT SC SM SP WG".split()
)
"""The 37 two-letter tokens in CADModule ``.rdata`` (09 §3.7, verifier re-extraction)."""

PLU_PER_MM = 40.0
"""Plotter units per millimetre - UNVERIFIED (09 §8 q.7 "units (1/40 mm HPGL default?)")."""

DEFAULT_P1 = (0.0, 0.0)
DEFAULT_P2 = (10000.0, 10000.0)
"""Scaling points after ``IN`` - UNVERIFIED (device dependent in HP-GL/2)."""

ETX = "\x03"

UNVERIFIED: tuple[str, ...] = (
    "PLT units 40 plu/mm (09 §8 q.7)",
    "PLT IP default P1=(0,0) P2=(10000,10000) plu",
    "PLT LB labels recorded, not rendered",
    "PLT RA/RR/WG fills imported as outlines; PM buffers emitted as outlines on EP",
    "PLT pen number ignored for layers unless pen_to_layer (then layer = pen - 1)",
    "PLT SC anisotropic scaling applied to arc centres/end points only (arcs stay circular); "
    "a reflecting SC reverses AA/AR/EW/WG sweeps and mirrors the EW/WG start angle",
    "PLT ESC device-control sequences skipped up to the next ':' / ';' / newline",
)


class PltError(ValueError):
    """Malformed HPGL input that cannot be interpreted."""


@dataclass(slots=True)
class PltImportOptions:
    """Import switches (all UNVERIFIED defaults, module docstring)."""

    plu_per_mm: float = PLU_PER_MM
    pen_to_layer: bool = False
    precision: float = DEFAULT_PRECISION


@dataclass(slots=True)
class PltImportResult:
    """Imported document plus statistics."""

    document: ChfDocument
    command_counts: Counter[str] = field(default_factory=Counter)
    ignored_counts: Counter[str] = field(default_factory=Counter)
    labels: list[tuple[Vec2, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


def _is_letter(c: str) -> bool:
    return ("A" <= c <= "Z") or ("a" <= c <= "z")


def tokenize(text: str) -> Iterator[tuple[str, str]]:
    """Yield ``(MNEMONIC, raw_parameter_text)`` pairs from HPGL text.

    ``LB`` parameters run to the label terminator (``DT``, default ETX),
    ``DT`` takes one character, ``CO`` a quoted string, ``PE``/``SM`` run to ``;``;
    everything else runs to ``;`` or the next letter (HP-GL/2 syntax).
    """
    i, n = 0, len(text)
    term = ETX
    while i < n:
        c = text[i]
        if c == "\x1b":  # device-control escape (PJL/RTL/HP-GL/2 ESC.), UNVERIFIED skip rule
            while i < n and text[i] not in ":;\n":
                i += 1
            i += 1
            continue
        if not _is_letter(c) or i + 1 >= n or not _is_letter(text[i + 1]):
            i += 1
            continue
        mnem = text[i : i + 2].upper()
        i += 2
        if mnem == "LB":
            j = text.find(term, i)
            j = n if j < 0 else j
            yield mnem, text[i:j]
            i = j + 1
        elif mnem == "DT":
            j = i
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] == ";":
                term = ETX
                yield mnem, ""
                i = j + 1
            else:
                term = text[j] if j < n else ETX
                k = text.find(";", j + 1)
                k = n if k < 0 else k
                yield mnem, text[j:k]
                i = k + 1
        elif mnem == "CO":
            j = i
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] == '"':
                k = text.find('"', j + 1)
                k = n if k < 0 else k
                yield mnem, text[j + 1 : k]
                i = k + 1
            else:
                yield mnem, ""
                i = j
        elif mnem in ("PE", "SM"):  # PE digits and the SM symbol may be letters
            k = text.find(";", i)
            k = n if k < 0 else k
            yield mnem, text[i:k]
            i = k + 1
        else:
            j = i
            while j < n and text[j] != ";" and not _is_letter(text[j]) and text[j] != "\x1b":
                j += 1
            yield mnem, text[i:j]
            i = j + 1 if j < n and text[j] == ";" else j


def _numbers(raw: str) -> list[float]:
    out: list[float] = []
    for part in raw.replace(",", " ").split():
        try:
            value = float(part)
        except ValueError as e:
            raise PltError(f"bad number {part!r}") from e
        if not math.isfinite(value):  # "9" * 400 -> inf would reach the geometry (review finding)
            raise PltError(f"number out of range {part[:20]!r}")
        out.append(value)
    return out


# ---------------------------------------------------------------------------
# PE (polyline encoded) decoding
# ---------------------------------------------------------------------------


def decode_pe(raw: str) -> Iterator[tuple[str, float | tuple[float, float, bool, bool]]]:
    """Decode a ``PE`` parameter string (HP-GL/2 base-64 / 7-bit base-32 encoding).

    Yields ``("pen", n)`` for ``:`` and ``("pt", (x, y, pen_up, absolute))`` for
    each coordinate pair.  Values use the sign-in-LSB zig-zag code; digits are
    least significant first; the terminating digit comes from the upper range
    (8-bit: 191..254, 7-bit after ``7``: 95..126).
    """
    i, n = 0, len(raw)
    base32 = False
    frac = 0
    pen_up = False
    absolute = False

    def number() -> float:
        nonlocal i
        value = 0
        mult = 1
        while i < n:
            c = ord(raw[i])
            i += 1
            if base32:
                if 63 <= c <= 94:
                    value += (c - 63) * mult
                    mult *= 32
                    continue
                if 95 <= c <= 126:
                    value += (c - 95) * mult
                    break
            else:
                if 63 <= c <= 126:
                    value += (c - 63) * mult
                    mult *= 64
                    continue
                if 191 <= c <= 254:
                    value += (c - 191) * mult
                    break
            if c in (32, 9, 10, 13):
                continue
            raise PltError(f"invalid PE character {chr(c)!r}")
        else:
            raise PltError("PE number without terminator")
        if value.bit_length() > 1000:  # float() would raise OverflowError
            raise PltError("PE number out of range")
        return -float(value >> 1) if value & 1 else float(value >> 1)

    while i < n:
        c = raw[i]
        if c in " \t\r\n":
            i += 1
        elif c == "7":
            base32 = True
            i += 1
        elif c == ":":
            i += 1
            yield "pen", number()
        elif c == ">":
            i += 1
            frac = int(number())
        elif c == "<":
            pen_up = True
            i += 1
        elif c == "=":
            absolute = True
            i += 1
        else:
            x = number()
            y = number()
            scale = 2.0**frac
            yield "pt", (x / scale, y / scale, pen_up, absolute)
            pen_up = False
            absolute = False


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------


def _norm_arc(c: Vec2, r: float, a0: float, sweep: float) -> ContourElement:
    """CCW arc glyph over the swept points; CW travel is expressed by direction -1 (03 §6.1)."""
    sweep = max(-2 * math.pi, min(2 * math.pi, sweep))
    if sweep >= 0:
        return ContourElement(ArcGlyph(c, r, a0, a0 + sweep), 1)
    return ContourElement(ArcGlyph(c, r, a0 + sweep, a0), -1)


class _Plotter:
    def __init__(self, opts: PltImportOptions, res: PltImportResult) -> None:
        self.o = opts
        self.res = res
        self.reset()

    def reset(self) -> None:
        self.pos = (0.0, 0.0)  # plu
        self.pen_down = False
        self.absolute = True
        self.pen = 1
        self.p1 = DEFAULT_P1
        self.p2 = DEFAULT_P2
        self.sc: tuple[float, float, float, float] | None = None  # x = off + (u - umin)*k
        self.run: list[Vec2] = []
        self.elements: list[ContourElement] = []
        self.polygon: list[list[ContourElement]] | None = None
        self.polygon_last: list[list[ContourElement]] = []

    # ---- units ------------------------------------------------------------
    def mm(self, p: tuple[float, float]) -> Vec2:
        k = self.o.plu_per_mm
        return Vec2(p[0] / k, p[1] / k)

    def user_abs(self, x: float, y: float) -> tuple[float, float]:
        if self.sc is None:
            return x, y
        ox, kx, oy, ky = self.sc
        return ox + x * kx, oy + y * ky

    def user_rel(self, dx: float, dy: float) -> tuple[float, float]:
        if self.sc is None:
            return dx, dy
        return dx * self.sc[1], dy * self.sc[3]

    def target(self, x: float, y: float, relative: bool | None = None) -> tuple[float, float]:
        rel = (not self.absolute) if relative is None else relative
        if rel:
            dx, dy = self.user_rel(x, y)
            return self.pos[0] + dx, self.pos[1] + dy
        return self.user_abs(x, y)

    # ---- contour assembly -------------------------------------------------
    @property
    def layer(self) -> int:
        return max(0, self.pen - 1) if self.o.pen_to_layer else 0

    def flush_run(self) -> None:
        if len(self.run) >= 2:
            self.elements.append(
                ContourElement(LwPolylineGlyph(0, [LwPolyVertex(p) for p in self.run]))
            )
        self.run = []

    def end_contour(self) -> None:
        self.flush_run()
        if self.elements:
            if self.polygon is not None:
                self.polygon.append(self.elements)
            else:
                self.emit(self.elements)
        self.elements = []

    def emit(self, elements: list[ContourElement | Glyph]) -> None:
        c: Contour = build_contour(elements, layer=self.layer, precision=self.o.precision)
        self.res.document.graphs.append(c)

    def closed_shape(self, elements: list[ContourElement]) -> None:
        """A self-contained closed contour (CI/EA/ER/RA/RR/EW/WG)."""
        if self.polygon is not None:
            self.polygon.append(elements)
        else:
            self.emit(list(elements))

    def line_to(self, p: tuple[float, float]) -> None:
        if self.pen_down:
            if not self.run:
                self.run = [self.mm(self.pos)]
            q = self.mm(p)
            if math.dist(q, self.run[-1]) > 1e-12:
                self.run.append(q)
        else:
            if p != self.pos:
                self.end_contour()
        self.pos = p

    @property
    def mirrored(self) -> bool:
        """True when ``SC`` maps user units to plotter units with a reflection (one axis reversed)."""
        return self.sc is not None and (self.sc[1] < 0) != (self.sc[3] < 0)

    def user_angle(self, deg: float) -> float:
        """A user-unit angle (``EW``/``WG`` start) as a plotter-unit angle in radians."""
        a = math.radians(deg)
        if self.sc is None:
            return a
        return math.atan2(
            math.copysign(1.0, self.sc[3]) * math.sin(a),
            math.copysign(1.0, self.sc[1]) * math.cos(a),
        )

    def arc(self, centre: tuple[float, float], sweep_deg: float) -> None:
        r_plu = math.dist(self.pos, centre)
        a0 = math.atan2(self.pos[1] - centre[1], self.pos[0] - centre[0])
        # the sweep is counter-clockwise in *user* units: a reflecting SC reverses it in
        # plotter units, otherwise the arc ends away from where the next user-unit
        # coordinate continues (import-ui fidelity review)
        sweep = math.radians(-sweep_deg if self.mirrored else sweep_deg)
        end = (centre[0] + r_plu * math.cos(a0 + sweep), centre[1] + r_plu * math.sin(a0 + sweep))
        if not self.pen_down:
            self.line_to(end)
            return
        if r_plu > 0 and abs(sweep) > 0:
            self.flush_run()
            k = self.o.plu_per_mm
            self.elements.append(_norm_arc(self.mm(centre), r_plu / k, a0, sweep))
        self.pos = end

    def arc3(self, pi: tuple[float, float], pe: tuple[float, float]) -> None:
        p0 = self.pos
        ax, ay = p0
        bx, by = pi
        cx, cy = pe
        d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if abs(d) < 1e-9 or not self.pen_down:
            if self.pen_down:
                self.line_to(pi)
            self.line_to(pe)
            return
        ux = (
            (ax * ax + ay * ay) * (by - cy)
            + (bx * bx + by * by) * (cy - ay)
            + (cx * cx + cy * cy) * (ay - by)
        ) / d
        uy = (
            (ax * ax + ay * ay) * (cx - bx)
            + (bx * bx + by * by) * (ax - cx)
            + (cx * cx + cy * cy) * (bx - ax)
        ) / d
        ccw = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > 0
        a0 = math.atan2(ay - uy, ax - ux)
        ae = math.atan2(cy - uy, cx - ux)
        if math.dist(p0, pe) < 1e-9:
            sweep = 2 * math.pi if ccw else -2 * math.pi
        elif ccw:
            sweep = (ae - a0) % (2 * math.pi)
        else:
            sweep = -((a0 - ae) % (2 * math.pi))
        self.arc((ux, uy), math.degrees(-sweep if self.mirrored else sweep))  # plu sweep
        self.pos = pe

    def bezier(
        self, c1: tuple[float, float], c2: tuple[float, float], e: tuple[float, float]
    ) -> None:
        if self.pen_down:
            self.flush_run()
            ctrl = [self.mm(self.pos), self.mm(c1), self.mm(c2), self.mm(e)]
            self.elements.append(ContourElement(SplineGlyph(0, 0, ctrl, [0.0] * 4 + [1.0] * 4)))
            self.pos = e
        else:
            self.line_to(e)

    def rectangle(self, corner: tuple[float, float]) -> None:
        x0, y0 = self.pos
        x1, y1 = corner
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        glyph = LwPolylineGlyph(1, [LwPolyVertex(self.mm(p)) for p in pts])
        self.closed_shape([ContourElement(glyph)])

    def wedge(self, radius: float, start_deg: float, sweep_deg: float) -> None:
        cx, cy = self.pos
        r = abs(radius)
        a0 = self.user_angle(start_deg)
        sweep = math.radians(-sweep_deg if self.mirrored else sweep_deg)
        k = self.o.plu_per_mm
        p_start = Vec2((cx + r * math.cos(a0)) / k, (cy + r * math.sin(a0)) / k)
        p_end = Vec2((cx + r * math.cos(a0 + sweep)) / k, (cy + r * math.sin(a0 + sweep)) / k)
        centre = self.mm(self.pos)
        els = [
            ContourElement(LwPolylineGlyph(0, [LwPolyVertex(centre), LwPolyVertex(p_start)])),
            _norm_arc(centre, r / k, a0, sweep),
            ContourElement(LwPolylineGlyph(0, [LwPolyVertex(p_end), LwPolyVertex(centre)])),
        ]
        self.closed_shape(els)

    # ---- commands ---------------------------------------------------------
    def run_command(self, mnem: str, raw: str) -> None:
        res = self.res
        if mnem not in MNEMONICS:
            res.ignored_counts[mnem] += 1
            return
        res.command_counts[mnem] += 1
        if mnem in ("PE", "LB", "CO", "DT", "SM"):
            nums: list[float] = []
        else:
            nums = _numbers(raw)
        match mnem:
            case "IN":
                self.end_contour()
                self.reset()
            case "DF":
                self.end_contour()
                self.absolute = True
                self.sc = None
                self.polygon = None
            case "PU" | "PD":
                down = mnem == "PD"
                if not down:
                    self.end_contour()
                self.pen_down = down
                for x, y in zip(nums[0::2], nums[1::2], strict=False):
                    self.line_to(self.target(x, y))
            case "PA" | "PR":
                self.absolute = mnem == "PA"
                for x, y in zip(nums[0::2], nums[1::2], strict=False):
                    self.line_to(self.target(x, y))
            case "AA" | "AR":
                if len(nums) >= 3:
                    c = self.target(nums[0], nums[1], relative=mnem == "AR")
                    self.arc(c, nums[2])
            case "AT" | "RT":
                if len(nums) >= 4:
                    rel = mnem == "RT"
                    pi = self.target(nums[0], nums[1], relative=rel)
                    pe = self.target(nums[2], nums[3], relative=rel)
                    self.arc3(pi, pe)
            case "CI":
                if nums:
                    k = self.o.plu_per_mm
                    r = abs(self.user_rel(nums[0], 0.0)[0]) / k
                    if r > 0:
                        self.end_contour()
                        glyph: Glyph = CircleGlyph(self.mm(self.pos), r)
                        self.closed_shape([ContourElement(glyph)])
            case "EA" | "RA":
                if len(nums) >= 2:
                    self.end_contour()
                    self.rectangle(self.target(nums[0], nums[1], relative=False))
            case "ER" | "RR":
                if len(nums) >= 2:
                    self.end_contour()
                    self.rectangle(self.target(nums[0], nums[1], relative=True))
            case "EW" | "WG":
                if len(nums) >= 3:
                    self.end_contour()
                    r = self.user_rel(nums[0], 0.0)[0]
                    self.wedge(r, nums[1], nums[2])
            case "BZ" | "BR":
                rel = mnem == "BR"
                for j in range(0, len(nums) - 5, 6):
                    base = self.pos
                    if rel:
                        rels = [self.user_rel(nums[j + m], nums[j + m + 1]) for m in (0, 2, 4)]
                        pts = [(base[0] + d[0], base[1] + d[1]) for d in rels]
                    else:
                        pts = [self.user_abs(nums[j + m], nums[j + m + 1]) for m in (0, 2, 4)]
                    self.bezier(pts[0], pts[1], pts[2])
            case "PE":
                self.polyline_encoded(raw)
            case "IP":
                if len(nums) >= 4:
                    self.p1, self.p2 = (nums[0], nums[1]), (nums[2], nums[3])
                elif len(nums) >= 2:
                    w, h = self.p2[0] - self.p1[0], self.p2[1] - self.p1[1]
                    self.p1, self.p2 = (nums[0], nums[1]), (nums[0] + w, nums[1] + h)
                else:
                    self.p1, self.p2 = DEFAULT_P1, DEFAULT_P2
            case "SC":
                self.scale(nums)
            case "SP":
                self.end_contour()
                self.pen = int(nums[0]) if nums else 0
            case "PM":
                mode = int(nums[0]) if nums else 0
                if mode == 0:
                    self.end_contour()
                    self.polygon = []
                elif self.polygon is not None:
                    self.end_contour()
                    if mode == 2:
                        self.polygon_last = self.polygon
                        self.polygon = None
            case "EP":
                for els in self.polygon_last:
                    self.emit(list(els))
                self.polygon_last = []
            case "LB":
                self.res.labels.append((self.mm(self.pos), raw))
                self.warn("LB labels are not converted to geometry")
            case "PG":
                self.end_contour()
            case "SM":
                if raw.strip():
                    self.warn("SM symbol mode is ignored")
            case _:  # CO DT KL OD OE OH OI OP OS: comments, terminators, queries
                pass

    def warn(self, msg: str) -> None:
        if msg not in self.res.warnings:
            self.res.warnings.append(msg)

    def scale(self, nums: list[float]) -> None:
        if len(nums) < 4:
            self.sc = None
            return
        p1x, p1y = self.p1
        p2x, p2y = self.p2
        stype = int(nums[4]) if len(nums) >= 5 else 0
        if stype == 2:  # point factor: xmin, xfactor, ymin, yfactor
            xmin, kx, ymin, ky = nums[0], nums[1], nums[2], nums[3]
            self.sc = (p1x - xmin * kx, kx, p1y - ymin * ky, ky)
            return
        xmin, xmax, ymin, ymax = nums[:4]
        if xmax == xmin or ymax == ymin:
            raise PltError("SC with an empty user range")
        kx = (p2x - p1x) / (xmax - xmin)
        ky = (p2y - p1y) / (ymax - ymin)
        ox, oy = p1x, p1y
        if stype == 1:  # isotropic, centred by left/bottom percentages (default 50 %)
            k = min(abs(kx), abs(ky))
            left = nums[5] if len(nums) >= 6 else 50.0
            bottom = nums[6] if len(nums) >= 7 else 50.0
            ox += (abs(kx) - k) * (xmax - xmin) * left / 100.0
            oy += (abs(ky) - k) * (ymax - ymin) * bottom / 100.0
            kx, ky = math.copysign(k, kx), math.copysign(k, ky)
        self.sc = (ox - xmin * kx, kx, oy - ymin * ky, ky)

    def polyline_encoded(self, raw: str) -> None:
        for kind, val in decode_pe(raw):
            if kind == "pen":
                self.end_contour()
                self.pen = int(val)  # type: ignore[arg-type]
                continue
            assert isinstance(val, tuple)
            x, y, up, absolute = val
            if absolute:
                p = self.user_abs(x, y)
            else:
                dx, dy = self.user_rel(x, y)
                p = (self.pos[0] + dx, self.pos[1] + dy)
            if up:
                self.end_contour()
                self.pen_down = False
                self.line_to(p)
                self.pen_down = True
            else:
                self.pen_down = True
                self.line_to(p)


def read_plt(
    source: str | Path | bytes | IO[bytes], options: PltImportOptions | None = None
) -> PltImportResult:
    """Parse an HPGL/2 file (path, bytes or binary stream) into a :class:`ChfDocument`.

    Bytes are read as Latin-1 so the 8-bit ``PE`` digits (191..254) survive.
    """
    if isinstance(source, (str, Path)):
        data = Path(source).read_bytes()
    elif isinstance(source, bytes):
        data = source
    else:
        data = source.read()
    opts = options or PltImportOptions()
    res = PltImportResult(document=ChfDocument())
    plotter = _Plotter(opts, res)
    for mnem, raw in tokenize(data.decode("latin-1")):
        plotter.run_command(mnem, raw)
    plotter.end_contour()
    if plotter.polygon:
        res.warnings.append("polygon mode not closed (PM2 missing); buffer discarded")
    return res
