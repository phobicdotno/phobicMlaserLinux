"""Streaming ASCII-DXF entity reader: the fast path of :mod:`nexcut.io.dxf`.

STATUS §5 task 7 / strict xfail X11: 50 000 separate ``LINE`` entities took about
8.2 s against the PORT-PLAN §8.3 3 s open budget, roughly 4.5 s of it inside
ezdxf building a :class:`ezdxf.document.Drawing` that the importer then throws
away.  This module walks the ``ENTITIES`` section tag by tag and builds the glyph
model directly, so nothing but the geometry is ever materialised.

What it reads
    ``HEADER`` (``$ACADVER``, ``$DWGCODEPAGE``, ``$INSUNITS``), the ``LAYER`` and
    ``BLOCK_RECORD`` tables, and the modelspace entities of ``ENTITIES``:
    LINE, POINT, ARC, CIRCLE, ELLIPSE, LWPOLYLINE (with bulges), POLYLINE
    (2-D with bulges and 3-D), SPLINE, SOLID/TRACE/3DFACE and XLINE/RAY, with
    OCS/extrusion -Z handling and layer names, exactly as
    :class:`nexcut.io.dxf._Converter` does - both readers call the same pure
    geometry helpers (:func:`~nexcut.io.dxf.arc_glyph`,
    :func:`~nexcut.io.dxf.ellipse_glyph`, :func:`~nexcut.io.dxf.lwpoly_glyph`,
    :func:`~nexcut.io.dxf.solid_glyph`, :func:`~nexcut.io.dxf.spline_glyphs`), so
    only the *parsing* differs.  HATCH/DIMENSION/IMAGE/LEADER/MLINE and every
    type outside the DxfParse keyword table are counted and ignored (09 §3.2),
    as in the ezdxf path.

When it declines (:class:`StreamUnsupported` -> ezdxf fallback)
    binary DXF; a layout other than ``Model``; ``INSERT`` (block references need
    full block-table resolution); ``TEXT``/``MTEXT`` while
    text-to-curves is on (the outlines come from ezdxf's ``text2path``); a tilted
    OCS (the ezdxf path approximates such an entity with
    :meth:`~nexcut.io.dxf._Converter.flatten_fallback`); an entity owned by a
    layout that is neither model nor paper space; ``read_color`` with a layer that
    is not in the ``LAYER`` table; and any structural or numeric anomaly at all -
    a group code that is not an integer, a point code not followed by its y
    partner, a truncated or unterminated section, a numeric group value that
    ezdxf's tag compiler would reject.  Declining is always safe: the caller then
    runs the complete reader, which either reads the file or raises
    :class:`~nexcut.io.dxf.DxfImportError`.

Known, deliberate differences from the ezdxf path (they are metadata, never
geometry or layer assignment):

* ezdxf fills header variables and table entries that the file does not contain
  from its own R12 template (a file with no ``HEADER`` reports ``$ACADVER``
  ``AC1009``, ``$INSUNITS`` 6 and the layers ``0``/``Defpoints``).  This reader
  reports what the file actually says, i.e. ``""``/``0``/``[]``.
* Tags after the ``ENTITIES`` section are not validated, so a file damaged only
  in ``OBJECTS`` is read here and rejected by ezdxf.

UNVERIFIED items are the importer's, listed in :data:`nexcut.io.dxf.UNVERIFIED`;
this module adds no new geometry conventions of its own.
"""

from __future__ import annotations

import math
from math import isfinite
from typing import NoReturn

from ezdxf.lldxf.encoding import decode_dxf_unicode, has_dxf_unicode
from ezdxf.lldxf.types import POINT_CODES, TYPE_TABLE
from ezdxf.math import BSpline, fit_points_to_cad_cv
from ezdxf.math.bspline import round_knots

from nexcut.io.dxf import (
    IGNORED_TYPES,
    SUPPORTED_TYPES,
    DxfImportOptions,
    DxfImportResult,
    arc_glyph,
    decode_dxf_bytes,
    ellipse_glyph,
    extrusion_kind,
    fonttools_available,
    lwpoly_glyph,
    mirror_ocs,
    polyline_is_degenerate,
    solid_glyph,
    spline_glyphs,
)
from nexcut.model.glyph import (
    CircleGlyph,
    Glyph,
    PointGlyph,
    SegmentGlyph,
    Vec2,
)
from nexcut.model.graph import ChfDocument, Contour, ContourElement
from nexcut.ops.import_gates import build_contour, glyph_is_finite

__all__ = ["STREAM_TYPES", "StreamUnsupported", "read_stream"]

BINARY_SENTINEL = b"AutoCAD Binary DXF\r\n\x1a\x00"
"""First 22 bytes of a binary DXF file (ezdxf ``binary_tags_loader``)."""

STREAM_TYPES = frozenset(
    {
        "LINE",
        "POINT",
        "ARC",
        "CIRCLE",
        "ELLIPSE",
        "LWPOLYLINE",
        "POLYLINE",
        "SPLINE",
        "SOLID",
        "TRACE",
        "3DFACE",
        "XLINE",
        "RAY",
    }
)
"""The :data:`~nexcut.io.dxf.SUPPORTED_TYPES` this reader converts itself."""

_DELEGATED_TYPES = frozenset({"INSERT"})
"""Supported types that need the block table -> ezdxf.

``ATTDEF``/``ATTRIB`` are not in :data:`~nexcut.io.dxf.SUPPORTED_TYPES`, so a
top-level one is counted and ignored by both readers; the attributes that matter
ride on an ``INSERT``, which is delegated whole."""

_TEXT_TYPES = frozenset({"TEXT", "MTEXT"})

_SKIPPED_SECTIONS = frozenset({"CLASSES", "BLOCKS", "OBJECTS", "THUMBNAILIMAGE", "ACDSDATA"})

_TAU = 2.0 * math.pi


class StreamUnsupported(Exception):
    """This file needs the complete ezdxf reader; :func:`nexcut.io.dxf.read_dxf` falls back."""


def read_stream(
    data: bytes, options: DxfImportOptions, *, fast_line: bool = True
) -> DxfImportResult:
    """Read ASCII-DXF ``data`` into a :class:`~nexcut.io.dxf.DxfImportResult`.

    Raises :class:`StreamUnsupported` - never a parse error - for anything this
    reader will not vouch for (module docstring); the caller then uses ezdxf.

    ``fast_line=False`` sends ``LINE`` through the generic tag path instead of
    :meth:`_StreamReader._line_entity`; the two must agree, which is what the
    cross-check test asserts.
    """
    if options.layout != "Model":
        raise StreamUnsupported(f"layout {options.layout!r} is not the modelspace")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    if data.startswith(BINARY_SENTINEL):
        raise StreamUnsupported("binary DXF")
    text, encoding = decode_dxf_bytes(data)
    # universal newlines, as ezdxf.read() gets them from io.StringIO(newline=None)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    reader = _StreamReader(options, encoding, fast_line=fast_line)
    reader.run(lines)
    return reader.res


def _bad(message: str) -> NoReturn:
    raise StreamUnsupported(message)


def _code_of(line: str) -> int:
    try:
        return int(line)
    except ValueError:
        _bad(f"group code {line!r} is not an integer")


def _check_value(code: int, value: str) -> None:
    """Reject a group value ezdxf's ``tag_compiler`` would reject (its int/float table)."""
    conv = TYPE_TABLE.get(code)
    if conv is None:
        return
    try:
        conv(value)
    except ValueError:
        if conv is not int:
            _bad(f"group {code} value {value!r} is not a number")
        try:  # tag_compiler's "ProE stores int values as floats" retry
            int(float(value))
        except (ValueError, OverflowError):
            _bad(f"group {code} value {value!r} is not an integer")


class _StreamReader:
    """One pass over the tag lines of an ASCII DXF file."""

    def __init__(self, options: DxfImportOptions, encoding: str, *, fast_line: bool = True) -> None:
        self.opts = options
        self.fast_line = fast_line
        self.res = DxfImportResult(document=ChfDocument(), encoding=encoding, reader="stream")
        self.lines: list[str] = []
        self.n = 0
        self.layer_colors: dict[str, int] = {}
        self.model_handle: str | None = None
        self.paper_handles: set[str] = set()
        self._decoded: dict[str, str] = {}
        self._entities_seen = False
        self._text_to_curves = options.text_to_curves and fonttools_available()

    # -- driver ------------------------------------------------------------
    def run(self, lines: list[str]) -> None:
        self.lines = lines
        n = len(lines)
        if n and lines[n - 1] == "":  # the file's trailing newline
            n -= 1
        self.n = n
        i = 0
        while i + 1 < n:
            code = _code_of(lines[i])
            value = lines[i + 1]
            i += 2
            if code != 0:
                continue
            value = value.strip()
            if value == "EOF":
                break
            if value != "SECTION":
                continue
            if i + 1 >= n:
                _bad("truncated SECTION")
            if _code_of(lines[i]) != 2:
                _bad("SECTION without a name tag")
            name = lines[i + 1].strip()
            i += 2
            if name == "HEADER":
                i = self._header(i)
            elif name == "TABLES":
                if self._entities_seen:
                    # the LAYER colours and the modelspace block-record handle have to be
                    # known before the entities are converted (DXF section order says they are)
                    _bad("TABLES after ENTITIES")
                i = self._tables(i)
            elif name == "ENTITIES":
                if self._entities_seen:
                    _bad("more than one ENTITIES section")
                self._entities_seen = True
                i = self._entities(i)
            elif name in _SKIPPED_SECTIONS:
                i = self._skip_section(i)
            else:
                _bad(f"unknown section {name!r}")
        if not self._entities_seen:
            _bad("no ENTITIES section")

    def _skip_section(self, i: int) -> int:
        lines, n = self.lines, self.n
        while i + 1 < n:
            code = _code_of(lines[i])
            value = lines[i + 1]
            i += 2
            if code == 0:
                value = value.strip()
                if value == "ENDSEC":
                    return i
                if value == "EOF":
                    _bad("section not closed by ENDSEC")
        _bad("truncated section")

    # -- HEADER ------------------------------------------------------------
    def _header(self, i: int) -> int:
        lines, n = self.lines, self.n
        var = ""
        while i + 1 < n:
            code = _code_of(lines[i])
            value = lines[i + 1]
            i += 2
            if code == 0:
                if value.strip() == "ENDSEC":
                    return i
                _bad(f"0/{value.strip()!r} inside HEADER")
            if code == 9:
                var = value.strip()
                continue
            _check_value(code, value)
            if code == 1 and var == "$ACADVER":
                self.res.acadver = value
            elif code == 3 and var == "$DWGCODEPAGE":
                self.res.codepage = value
            elif code == 70 and var == "$INSUNITS":
                self.res.insunits = int(float(value))
        _bad("truncated HEADER")

    # -- TABLES ------------------------------------------------------------
    def _tables(self, i: int) -> int:
        lines, n = self.lines, self.n
        record = ""
        fields: dict[int, str] = {}
        while i + 1 < n:
            code = _code_of(lines[i])
            value = lines[i + 1]
            i += 2
            if code != 0:
                _check_value(code, value)
                fields.setdefault(code, value)  # first tag of a record wins
                continue
            self._table_record(record, fields)
            fields = {}
            value = value.strip()
            if value == "ENDSEC":
                return i
            if value == "EOF":
                _bad("TABLES not closed by ENDSEC")
            record = value
        _bad("truncated TABLES")

    def _table_record(self, record: str, fields: dict[int, str]) -> None:
        if record == "LAYER":
            raw = fields.get(2, "")
            self.res.layers.append(self._decode(raw))
            self.layer_colors[raw.lower()] = abs(int(float(fields.get(62, "7"))))
        elif record == "BLOCK_RECORD":
            name = fields.get(2, "").upper()
            handle = fields.get(5, "").strip().upper()
            if name == "*MODEL_SPACE":
                self.model_handle = handle
            elif name.startswith("*PAPER_SPACE"):
                self.paper_handles.add(handle)

    def _decode(self, name: str) -> str:
        """``\\U+XXXX`` escapes decoded, memoised (09 §3.2 layer names)."""
        out = self._decoded.get(name)
        if out is None:
            out = decode_dxf_unicode(name) if has_dxf_unicode(name) else name
            self._decoded[name] = out
        return out

    # -- ENTITIES ----------------------------------------------------------
    def _collect(self, i: int) -> tuple[list[int], list[str], int, str]:
        """Tags up to the next 0-tag: ``(codes, values, index, next 0-tag value)``."""
        lines, n = self.lines, self.n
        codes: list[int] = []
        values: list[str] = []
        expect = -1
        while i + 1 < n:
            try:
                code = int(lines[i])
            except ValueError:
                _bad(f"group code {lines[i]!r} is not an integer")
            value = lines[i + 1]
            i += 2
            if expect >= 0 and code != expect:
                # ezdxf's tag_compiler: "Missing required y coordinate"
                _bad(f"point group {expect - 10} without its {expect} partner")
            expect = code + 10 if code in POINT_CODES else -1
            if code == 0:
                return codes, values, i, value.strip()
            codes.append(code)
            values.append(value)
        _bad("truncated entity")

    def _entities(self, i: int) -> int:
        lines, n = self.lines, self.n
        if i + 1 >= n or _code_of(lines[i]) != 0:
            _bad("ENTITIES does not start with an entity")
        etype = lines[i + 1].strip()
        i += 2
        while True:
            if etype == "ENDSEC":
                return i
            if etype == "EOF" or not etype:
                _bad("ENTITIES not closed by ENDSEC")
            if etype == "LINE" and self.fast_line:
                i, etype = self._line_entity(i)
            else:
                _, _, i, etype = self._entity(etype, i)

    def _line_entity(self, i: int) -> tuple[int, str]:
        """LINE straight into a :class:`~nexcut.model.graph.Contour`: the X11 hot path.

        Identical in effect to :meth:`_entity` + :meth:`_convert` for a LINE (pinned
        by the cross-check test), but it never builds the per-entity tag lists and
        dict, and it fills the cached contour geometry (03 §6.1 lines 2-7) in closed
        form the way :func:`nexcut.ops.import_gates.refresh_contour` would.
        50 000 separate LINE entities are the STATUS §5 task 7 / X11 budget case.
        """
        lines, n = self.lines, self.n
        x0 = y0 = x1 = y1 = 0.0
        layer = "0"
        color = "256"
        owner: str | None = None
        paper = False
        expect = -1
        while i + 1 < n:
            try:
                code = int(lines[i])
            except ValueError:
                _bad(f"group code {lines[i]!r} is not an integer")
            value = lines[i + 1]
            i += 2
            if expect >= 0 and code != expect:
                _bad(f"point group {expect - 10} without its {expect} partner")
            expect = code + 10 if code in POINT_CODES else -1
            if code == 10:
                x0 = _float(value, 10)
            elif code == 20:
                y0 = _float(value, 20)
            elif code == 11:
                x1 = _float(value, 11)
            elif code == 21:
                y1 = _float(value, 21)
            elif code == 8:
                layer = value
            elif code == 0:
                self._line_contour(x0, y0, x1, y1, layer, color, owner, paper)
                return i, value.strip()
            elif code == 62:
                color = value
            elif code == 67:
                paper = _float(value, 67) != 0.0
            elif code == 330:
                owner = value
        _bad("truncated entity")

    def _line_contour(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        layer: str,
        color: str,
        owner: str | None,
        paper: bool,
    ) -> None:
        if paper or not self._owned_by_model(owner):
            return
        res = self.res
        res.entity_counts["LINE"] += 1
        if not (isfinite(x0) and isfinite(y0) and isfinite(x1) and isfinite(y1)):
            self.warn("LINE skipped: non-finite coordinate")
            res.ignored_counts["LINE"] += 1
            return
        p0, p1 = Vec2(x0, y0), Vec2(x1, y1)
        contour = Contour(
            precision=self.opts.precision,
            elements=[ContourElement(SegmentGlyph(p0, p1), 1)],
            layer=self._layer_number(layer, color),
        )
        contour.length = math.dist(p0, p1)
        contour.bbox_min = Vec2(min(x0, x1), min(y0, y1))
        contour.bbox_max = Vec2(max(x0, x1), max(y0, y1))
        contour.start, contour.end = p0, p1
        res.document.graphs.append(contour)
        res.imported_counts["LINE"] += 1
        res.entity_layers[self._decode(layer)] += 1

    def _entity(self, etype: str, i: int) -> tuple[list[int], list[str], int, str]:
        codes, values, i, nxt = self._collect(i)
        vertices: list[dict[int, str]] = []
        if etype == "POLYLINE":
            while nxt == "VERTEX":
                vc, vv, i, nxt = self._collect(i)
                vertices.append(_as_dict(vc, vv))
            if nxt == "SEQEND":
                _, _, i, nxt = self._collect(i)
        elif etype in ("VERTEX", "SEQEND"):
            _bad(f"stray {etype} outside a POLYLINE")
        self._convert(etype, codes, values, vertices)
        return codes, values, i, nxt

    # -- conversion --------------------------------------------------------
    def _convert(
        self,
        etype: str,
        codes: list[int],
        values: list[str],
        vertices: list[dict[int, str]],
    ) -> None:
        """Mirror of :meth:`nexcut.io.dxf._Converter.convert` on raw tags."""
        d = _as_dict(codes, values)
        if not self._in_modelspace(d):
            return
        res = self.res
        res.entity_counts[etype] += 1
        if etype in IGNORED_TYPES or etype not in SUPPORTED_TYPES:
            res.ignored_counts[etype] += 1
            return
        if etype in _DELEGATED_TYPES:
            _bad(f"{etype} needs the block table")
        if etype in _TEXT_TYPES:
            self._text(etype)
            return
        if etype not in STREAM_TYPES:  # pragma: no cover - the sets are exhaustive
            _bad(f"{etype} is not streamed")
        try:
            glyphs = self._glyphs(etype, d, codes, values, vertices)
        except (ValueError, ZeroDivisionError) as exc:
            self.warn(f"{etype} skipped: {exc}")
            res.ignored_counts[etype] += 1
            return
        if glyphs is None:
            return
        if not glyphs:
            return
        if not all(glyph_is_finite(g) for g in glyphs):
            self.warn(f"{etype} skipped: non-finite coordinate")
            res.ignored_counts[etype] += 1
            return
        res.document.graphs.append(
            build_contour(glyphs, layer=self._layer_of(d), precision=self.opts.precision)
        )
        res.imported_counts[etype] += 1
        res.entity_layers[self._decode(d.get(8, "0"))] += 1

    def _in_modelspace(self, d: dict[int, str]) -> bool:
        if _f(d, 67, 0.0) != 0.0:  # paper-space flag (R12 and later)
            return False
        return self._owned_by_model(d.get(330))

    def _owned_by_model(self, owner: str | None) -> bool:
        """Owner-handle test: modelspace yes, paper space no, anything else -> ezdxf."""
        if owner is None or self.model_handle is None:
            return True
        handle = owner.strip().upper()
        if handle == self.model_handle:
            return True
        if handle in self.paper_handles:
            return False
        _bad("entity owned by neither model nor paper space")

    def _layer_of(self, d: dict[int, str]) -> int:
        return self._layer_number(d.get(8, "0"), d.get(62, "256"))

    def _layer_number(self, layer: str, color: str) -> int:
        """``IGP.EnableReadGraphColor``: ACI colour n -> process layer n-1 (01 §1.2)."""
        if not self.opts.read_color:
            return 0
        aci = int(_float(color, 62))
        if aci == 256:  # BYLAYER
            key = layer.lower()
            if key not in self.layer_colors:
                _bad(f"read_color: layer {key!r} is not in the LAYER table")
            aci = self.layer_colors[key]
        if aci <= 0 or aci > 255:
            return 0
        return aci - 1

    def warn(self, message: str) -> None:
        if message not in self.res.warnings:
            self.res.warnings.append(message)

    def _text(self, etype: str) -> None:
        if self._text_to_curves:
            _bad(f"{etype} with text-to-curves on")
        if not self.opts.text_to_curves:
            self.warn("TEXT/MTEXT skipped: text-to-curves (pd374) is off")
        else:
            self.warn("TEXT/MTEXT skipped: fontTools not installed")
        self.res.ignored_counts[etype] += 1

    def _glyphs(
        self,
        etype: str,
        d: dict[int, str],
        codes: list[int],
        values: list[str],
        vertices: list[dict[int, str]],
    ) -> list[Glyph] | None:
        """Glyphs of one entity, ``[]`` for a degenerate one, ``None`` when it is skipped."""
        if etype == "LINE":
            return [SegmentGlyph(Vec2(_f(d, 10), _f(d, 20)), Vec2(_f(d, 11, 0.0), _f(d, 21, 0.0)))]
        if etype == "POINT":
            return [PointGlyph(Vec2(_f(d, 10), _f(d, 20)))]
        if etype in ("XLINE", "RAY"):
            self.warn(f"{etype} skipped (infinite line)")
            self.res.ignored_counts[etype] += 1
            return None
        if etype == "CIRCLE":
            kind = self._kind(d, etype)
            return [CircleGlyph(mirror_ocs(Vec2(_f(d, 10), _f(d, 20)), kind), _f(d, 40, 1.0))]
        if etype == "ARC":
            kind = self._kind(d, etype)
            return [
                arc_glyph(
                    Vec2(_f(d, 10), _f(d, 20)),
                    _f(d, 40, 1.0),
                    _f(d, 50, 0.0),
                    _f(d, 51, 360.0),
                    kind,
                )
            ]
        if etype == "ELLIPSE":
            kind = self._kind(d, etype)
            return [
                ellipse_glyph(
                    Vec2(_f(d, 10), _f(d, 20)),
                    Vec2(_f(d, 11, 1.0), _f(d, 21, 0.0)),
                    _f(d, 40, 1.0),
                    _f(d, 41, 0.0),
                    _f(d, 42, _TAU),
                    kind,
                )
            ]
        if etype == "LWPOLYLINE":
            return self._lwpolyline(d, codes, values)
        if etype == "POLYLINE":
            return self._polyline(d, vertices)
        if etype == "SPLINE":
            return spline_glyphs(_bspline(d, codes, values), self.warn)
        return self._solid(etype, d)

    def _kind(self, d: dict[int, str], etype: str) -> int:
        kind = extrusion_kind((_f(d, 210, 0.0), _f(d, 220, 0.0), _f(d, 230, 1.0)))
        if kind == 0:  # the ezdxf path approximates it with ezdxf.path.make_path
            _bad(f"{etype} with a tilted extrusion")
        return kind

    def _lwpolyline(self, d: dict[int, str], codes: list[int], values: list[str]) -> list[Glyph]:
        kind = self._kind(d, "LWPOLYLINE")
        pts: list[list[float]] = []
        cur: list[float] | None = None
        for code, value in zip(codes, values, strict=True):
            if code == 10:
                cur = [_float(value, 10), 0.0, 0.0]
                pts.append(cur)
            elif cur is None:
                continue
            elif code == 20:
                cur[1] = _float(value, 20)
            elif code == 42:
                cur[2] = _float(value, 42)
        closed = 1 if int(_f(d, 70, 0.0)) & 1 else 0
        if polyline_is_degenerate(len(pts), closed):
            if pts:
                self.warn("LWPOLYLINE skipped: open polyline with a single vertex")
            return []
        return [lwpoly_glyph([(p[0], p[1], p[2]) for p in pts], closed, kind)]

    def _polyline(self, d: dict[int, str], vertices: list[dict[int, str]]) -> list[Glyph]:
        flags = int(_f(d, 70, 0.0))
        if flags & 16 or flags & 64:  # POLYMESH / POLYFACE (ezdxf Polyline flag names)
            self.warn("POLYLINE mesh/polyface skipped")
            return []
        is_2d = flags & 88 == 0  # ezdxf Polyline.ANY3D
        kind = self._kind(d, "POLYLINE") if is_2d else 1
        pts: list[tuple[float, float, float]] = []
        for v in vertices:
            if int(_f(v, 70, 0.0)) & 16:  # spline frame control point, not on the curve
                continue
            bulge = _f(v, 42, 0.0) if is_2d else 0.0
            pts.append((_f(v, 10), _f(v, 20), bulge))
        closed = 1 if flags & 1 else 0
        if polyline_is_degenerate(len(pts), closed):
            if pts:
                self.warn("POLYLINE skipped: open polyline with a single vertex")
            return []
        return [lwpoly_glyph(pts, closed, kind)]

    def _solid(self, etype: str, d: dict[int, str]) -> list[Glyph]:
        corners = [Vec2(_f(d, 10 + k, 0.0), _f(d, 20 + k, 0.0)) for k in range(4)]
        reorder = etype in ("SOLID", "TRACE")
        kind = self._kind(d, etype) if reorder else 1
        glyph = solid_glyph(corners, reorder=reorder, kind=kind)
        return [] if glyph is None else [glyph]


# ---------------------------------------------------------------------------
# tag helpers
# ---------------------------------------------------------------------------


def _as_dict(codes: list[int], values: list[str]) -> dict[int, str]:
    """Scalar view of one entity's tags; a repeated code keeps its last value (as ezdxf does)."""
    return dict(zip(codes, values, strict=True))


def _float(value: str, code: int) -> float:
    try:
        return float(value)
    except ValueError:
        _bad(f"group {code} value {value!r} is not a number")


def _f(d: dict[int, str], code: int, default: float = 0.0) -> float:
    value = d.get(code)
    return default if value is None else _float(value, code)


def _bspline(d: dict[int, str], codes: list[int], values: list[str]) -> BSpline:
    """``ezdxf.entities.Spline.construction_tool`` from raw SPLINE tags."""
    control: list[tuple[float, float, float]] = []
    fit: list[tuple[float, float, float]] = []
    knots: list[float] = []
    weights: list[float] = []
    target: list[tuple[float, float, float]] | None = None
    xyz: list[float] = []
    for code, value in zip(codes, values, strict=True):
        if code == 10:
            target, xyz = control, [_float(value, 10), 0.0, 0.0]
            control.append((0.0, 0.0, 0.0))
        elif code == 11:
            target, xyz = fit, [_float(value, 11), 0.0, 0.0]
            fit.append((0.0, 0.0, 0.0))
        elif code in (20, 21) and target is not None:
            xyz[1] = _float(value, code)
            target[-1] = (xyz[0], xyz[1], xyz[2])
        elif code in (30, 31) and target is not None:
            xyz[2] = _float(value, code)
            target[-1] = (xyz[0], xyz[1], xyz[2])
        elif code == 40:
            knots.append(_float(value, 40))
        elif code == 41:
            weights.append(_float(value, 41))
    if control:
        return BSpline(
            control_points=control,
            order=int(float(d.get(71, "3"))) + 1,
            knots=round_knots(knots, _f(d, 42, 1e-10)) if knots else None,
            weights=weights or None,
        )
    if fit:
        tangents = None
        if 12 in d and 13 in d:
            tangents = [
                (_f(d, 12), _f(d, 22), _f(d, 32)),
                (_f(d, 13), _f(d, 23), _f(d, 33)),
            ]
        return fit_points_to_cad_cv(fit, tangents=tangents)
    raise ValueError("Construction tool requires control- or fit points.")
