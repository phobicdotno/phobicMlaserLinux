"""DXF import into the ``.chf`` glyph model, and DXF export, on top of ezdxf.

Replaces ``DxfParseDllvc100.dll`` + ``CADModule::CDxfParse`` (09 §3.2, §3.7,
§7 "ASCII DXF read ... and DXF write -> ezdxf").

Import behaviour and its sources
    * Entity coverage = the DxfParse keyword table (09 §3.2): LINE, ARC, CIRCLE,
      ELLIPSE, LWPOLYLINE (bulges), POLYLINE, SPLINE, POINT, TEXT, MTEXT, INSERT,
      SOLID/TRACE/3DFACE, XLINE/RAY.  HATCH, DIMENSION, IMAGE, LEADER, MLINE and
      everything else are not in that table and are ignored (09 §3.2, §9;
      PORT-PLAN §3.3 "ignore HATCH/DIM/IMAGE like the original").
    * Each geometric entity becomes one :class:`~nexcut.model.graph.Contour`
      holding one glyph; the ``IGP`` gates (``nexcut.ops.import_gates``) do the
      chaining afterwards (05 §4: import -> IGP clean-up -> glyph model).
    * The ``.chf`` geometry maps 1:1 onto DXF entities (03 §14): POINT -> type 1,
      LINE -> 2, ARC -> 3 (radians), CIRCLE -> 4, ELLIPSE -> 5, LWPOLYLINE with
      bulge -> 6, SPLINE degree 3 -> 7.
    * INSERT is exploded when ``pd373`` "auto-explode DXF groups/blocks" is on
      (09 §4 limits, 06 §4.7); otherwise the block reference becomes one
      :class:`~nexcut.model.graph.Group` (``COpGroupGraphCmd`` type 9).
    * TEXT/MTEXT are converted to outline curves when ``pd374`` "auto text ->
      curves" is on (09 §3.7 text-to-path; the original uses
      ``GetGlyphOutlineW(GGO_BEZIER)``, here ezdxf's text2path on fontTools).  If
      fontTools is unavailable, or the option is off, text is skipped with a warning.
    * ``$DWGCODEPAGE`` ``ANSI_936`` (the vendor test drawing, 99-gaps §1.2) is
      decoded with the Windows cp936 table of :mod:`nexcut.io.cp936`; files of
      ``$ACADVER`` >= AC1021 are UTF-8 as the DXF reference requires.
    * ``$INSUNITS`` is reported but not applied.  EVIDENCE: the vendor test file
      declares ``$INSUNITS=1`` (inches) while its geometry (31 x 90 units, preset
      "SS1mm") only fits the 1300 x 900 mm bed as millimetres, so the original
      reads coordinates as mm (INFERENCE medium).

Export
    ``write_dxf`` emits R2000 (AC1015, ``$DWGCODEPAGE ANSI_936``) with one entity
    per glyph.  The original writer's entity choice is unknown (09 §8 q.6:
    ``dxfTemplate.dxf`` not shipped), so layer naming and colours are UNVERIFIED.

Everything not backed by a cited source is listed in :data:`UNVERIFIED`.
"""

from __future__ import annotations

import importlib.util
import io
import math
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

import ezdxf
from ezdxf.document import Drawing
from ezdxf.entities import DXFEntity, DXFGraphic
from ezdxf.lldxf.encoding import decode_dxf_unicode, has_dxf_unicode
from ezdxf.math import BSpline, Vec3

from nexcut.io import cp936
from nexcut.model.glyph import (
    ArcGlyph,
    CircleGlyph,
    EllipseArcGlyph,
    Glyph,
    LwPolylineGlyph,
    LwPolyVertex,
    PointGlyph,
    SegmentGlyph,
    SplineGlyph,
    Vec2,
)
from nexcut.model.graph import ChfDocument, Contour, ContourElement, Graph, Group, iter_contours
from nexcut.ops.import_gates import (
    DEFAULT_PRECISION,
    build_contour,
    glyph_is_finite,
    refresh_group,
)

__all__ = [
    "IGNORED_TYPES",
    "SUPPORTED_TYPES",
    "UNVERIFIED",
    "DxfImportError",
    "DxfImportOptions",
    "DxfImportResult",
    "decode_dxf_bytes",
    "doc_to_drawing",
    "fonttools_available",
    "import_dxf",
    "read_dxf",
    "write_dxf",
]

SUPPORTED_TYPES = frozenset(
    {
        "LINE",
        "ARC",
        "CIRCLE",
        "ELLIPSE",
        "LWPOLYLINE",
        "POLYLINE",
        "SPLINE",
        "POINT",
        "TEXT",
        "MTEXT",
        "INSERT",
        "SOLID",
        "TRACE",
        "3DFACE",
        "XLINE",
        "RAY",
    }
)
"""The DxfParse entity keyword table (09 §3.2, ``.rdata`` 0x10018250) - ATTRIB rides on INSERT."""

IGNORED_TYPES = frozenset({"HATCH", "DIMENSION", "IMAGE", "LEADER", "MLINE"})
"""Named in 09 §3.2 as not in the keyword table (ignored by the original)."""

UNVERIFIED: tuple[str, ...] = (
    "DXF import: SOLID/TRACE/3DFACE become their closed outline (the original reads them as "
    "CDxf4Corner; what CADModule makes of them is untraced)",
    "DXF import: XLINE/RAY are skipped (infinite; CDxfXLine2d/CDxfRayLine2d handling untraced)",
    "DXF import: SPLINE flags int1/int2 written as 0 (03 §12 unknown)",
    "DXF import: rational or non-cubic SPLINEs are converted to a degree-3 non-rational B-spline "
    "(degree elevation exact for degree 2; rational curves re-interpolated through 32 samples per "
    "control point, deviation ~1e-3 mm on a 4 mm test curve)",
    "DXF import: process layer = 0 unless read_color (IGP.EnableReadGraphColor) maps ACI "
    "colour n to layer n-1",
    "DXF import: $INSUNITS ignored (inferred from the vendor test file)",
    "DXF import: entities with a non-finite coordinate are skipped with a warning (port "
    "safety choice; the original's handling is untraced)",
    "DXF import: text outlines use ezdxf fonts (the original: GDI GetGlyphOutlineW with the "
    "Windows font), glyph shapes and advance widths differ; text becomes one Group per entity",
    "DXF import: non-exploded INSERT -> one Group (nested INSERTs flattened into it)",
    "DXF export: R2000/ANSI_936, layer name = str(process layer), ACI colour = layer + 1",
)


class DxfImportError(ValueError):
    """The input is not a readable DXF file."""


@dataclass(slots=True)
class DxfImportOptions:
    """Import switches; names give the vendor parameter they stand for (06 §4.7, 01 §1.2)."""

    explode_blocks: bool = True
    """``pd373`` auto-explode DXF groups/blocks (off: each INSERT becomes a Group)."""
    text_to_curves: bool = True
    """``pd374`` convert text to curves (off: TEXT/MTEXT skipped with a warning)."""
    read_color: bool = False
    """``IGP.EnableReadGraphColor`` (value 0 on this machine, 01 §1.2)."""
    precision: float = DEFAULT_PRECISION
    """Contour ``precision`` constructor argument (03 §6.1 line 1; UNVERIFIED 0.01)."""
    layout: str = "Model"
    """Layout to import (modelspace; paper space is not machining geometry)."""


@dataclass(slots=True)
class DxfImportResult:
    """The imported document plus the facts an import dialog/report needs."""

    document: ChfDocument
    acadver: str = ""
    codepage: str = ""
    encoding: str = ""
    insunits: int = 0
    entity_counts: Counter[str] = field(default_factory=Counter)
    """Top-level entity types of the imported layout (before explosion)."""
    imported_counts: Counter[str] = field(default_factory=Counter)
    """Entity types converted to geometry (after explosion)."""
    ignored_counts: Counter[str] = field(default_factory=Counter)
    layers: list[str] = field(default_factory=list)
    """LAYER table names (``\\U+XXXX`` escapes decoded)."""
    entity_layers: Counter[str] = field(default_factory=Counter)
    """DXF layer name of every converted entity."""
    warnings: list[str] = field(default_factory=list)

    @property
    def bbox(self) -> tuple[Vec2, Vec2] | None:
        """Union of the cached contour bboxes (None for an empty drawing)."""
        boxes = [
            (c.bbox_min, c.bbox_max) for g in self.document.graphs for c, _ in iter_contours(g)
        ]
        if not boxes:
            return None
        return (
            Vec2(min(b[0][0] for b in boxes), min(b[0][1] for b in boxes)),
            Vec2(max(b[1][0] for b in boxes), max(b[1][1] for b in boxes)),
        )


def fonttools_available() -> bool:
    """True when fontTools (the TrueType backend of ezdxf text2path) can be imported."""
    return importlib.util.find_spec("fontTools") is not None


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

_BINARY_SENTINEL = b"AutoCAD Binary DXF\r\n\x1a\x00"
_HEADER_VAR = re.compile(rb"\n\s*9\r?\n\$(ACADVER|DWGCODEPAGE)\r?\n\s*[13]\r?\n([^\r\n]*)")


def _header_vars(data: bytes) -> dict[str, str]:
    head = data[: 1 << 16]
    if not head.startswith(b"\n"):
        head = b"\n" + head
    return {
        m.group(1).decode("ascii"): m.group(2).decode("ascii", "replace").strip()
        for m in _HEADER_VAR.finditer(head)
    }


def decode_dxf_bytes(data: bytes) -> tuple[str, str]:
    """Decode ASCII-DXF bytes to text; returns ``(text, encoding_name)``.

    ``$ACADVER`` >= AC1021 -> UTF-8; otherwise ``$DWGCODEPAGE`` selects the code
    page (``ANSI_936`` -> :func:`nexcut.io.cp936.decode`, the Windows table the
    vendor PC uses, 03 §4.1), with cp1252 as the DXF default.
    """
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    hv = _header_vars(data)
    ver = hv.get("ACADVER", "")
    if ver >= "AC1021":
        return data.decode("utf-8", "replace"), "utf-8"
    cp = hv.get("DWGCODEPAGE", "ANSI_1252").upper()
    if cp in ("ANSI_936", "DOS936", "GB2312", "GBK"):
        return cp936.decode(data), "cp936"
    from ezdxf.tools.codepage import toencoding

    enc = toencoding(cp)
    try:
        return data.decode(enc, "replace"), enc
    except LookupError:
        return data.decode("cp1252", "replace"), "cp1252"


def _load_drawing(source: str | Path | bytes | IO[bytes]) -> tuple[Drawing, str]:
    if isinstance(source, (str, Path)):
        try:
            data = Path(source).read_bytes()
        except OSError as e:
            raise DxfImportError(f"cannot open {source}: {e}") from e
    elif isinstance(source, bytes):
        data = source
    else:
        data = source.read()
    if not data.strip():
        raise DxfImportError("file is empty")
    try:
        if data.startswith(_BINARY_SENTINEL):
            from ezdxf.lldxf.tagger import binary_tags_loader

            return Drawing.load(binary_tags_loader(data)), "binary"
        text, enc = decode_dxf_bytes(data)
        return ezdxf.read(io.StringIO(text, newline=None)), enc
    except (ezdxf.DXFError, ValueError, IndexError, KeyError, OverflowError) as e:
        # OverflowError: ezdxf's tag compiler does int(float("1e999")) for an out-of-range
        # integer group value (import-ui fidelity review, fuzzed vendor DXF)
        raise DxfImportError(f"not a readable DXF file: {e}") from e


# ---------------------------------------------------------------------------
# Entity conversion
# ---------------------------------------------------------------------------


def _xy(p: Any) -> Vec2:
    return Vec2(float(p[0]), float(p[1]))


def _extrusion_kind(e: DXFGraphic) -> int:
    """+1 for (0,0,1), -1 for (0,0,-1), 0 for a tilted OCS."""
    ex = Vec3(e.dxf.get("extrusion", (0.0, 0.0, 1.0)))
    if abs(ex.x) < 1e-9 and abs(ex.y) < 1e-9:
        return 1 if ex.z > 0 else -1
    return 0


def _mirror(p: Vec2, kind: int) -> Vec2:
    """OCS -> WCS for extrusion (0,0,+-1): the arbitrary-axis x-axis is (-1,0,0) for -Z."""
    return p if kind == 1 else Vec2(-p[0], p[1])


def _param_samples(bs: BSpline) -> list[float]:
    """32 parameter samples per control point over the curve domain (for re-interpolation)."""
    n = max(64, 32 * bs.count)
    return [bs.max_t * i / (n - 1) for i in range(n)]


def _flush_run(run: list[Vec2], glyphs: list[Glyph]) -> None:
    """Append a straight run of >= 2 points as an open lwpolyline glyph."""
    if len(run) >= 2:
        glyphs.append(LwPolylineGlyph(0, [LwPolyVertex(p) for p in run]))


class _Converter:
    def __init__(self, opts: DxfImportOptions, result: DxfImportResult, drawing: Drawing) -> None:
        self.opts = opts
        self.res = result
        self.drawing = drawing
        self._text_warned = False

    # -- helpers -----------------------------------------------------------
    def warn(self, msg: str) -> None:
        if msg not in self.res.warnings:
            self.res.warnings.append(msg)

    def layer_of(self, e: DXFGraphic) -> int:
        if not self.opts.read_color:
            return 0
        aci = int(e.dxf.get("color", 256))
        if aci == 256:  # BYLAYER
            layer = self.drawing.layers.get(e.dxf.get("layer", "0")) if self.drawing else None
            aci = abs(int(layer.dxf.color)) if layer is not None else 7
        if aci <= 0 or aci > 255:
            return 0
        return aci - 1

    def contour(self, glyphs: Iterable[Glyph], e: DXFGraphic) -> Contour:
        return build_contour(glyphs, layer=self.layer_of(e), precision=self.opts.precision)

    def counted(self, e: DXFGraphic) -> None:
        self.res.imported_counts[e.dxftype()] += 1
        name = str(e.dxf.get("layer", "0"))
        self.res.entity_layers[decode_dxf_unicode(name) if has_dxf_unicode(name) else name] += 1

    def flatten_fallback(self, e: DXFGraphic, why: str) -> list[Graph]:
        from ezdxf import path as ezpath

        self.warn(f"{e.dxftype()} {why}: approximated by a polyline")
        p = ezpath.make_path(e)
        out: list[Graph] = []
        for sub in p.sub_paths():
            pts = [_xy(v) for v in sub.flattening(0.001)]
            if len(pts) >= 2:
                out.append(self.contour([LwPolylineGlyph(0, [LwPolyVertex(q) for q in pts])], e))
        return out

    # -- entities ----------------------------------------------------------
    def convert(self, e: DXFEntity) -> list[Graph]:
        t = e.dxftype()
        if t in IGNORED_TYPES or t not in SUPPORTED_TYPES:
            self.res.ignored_counts[t] += 1
            return []
        assert isinstance(e, DXFGraphic)
        handler = getattr(self, "_" + t.lower().replace("3d", "d3"), None)
        assert handler is not None, t
        try:
            graphs: list[Graph] = handler(e)
        except (ValueError, ZeroDivisionError, ezdxf.DXFError) as exc:
            self.warn(f"{t} skipped: {exc}")
            self.res.ignored_counts[t] += 1
            return []
        if t != "INSERT" and not all(
            glyph_is_finite(el.glyph)
            for g in graphs
            for c, _ in iter_contours(g)
            for el in c.elements
        ):
            self.warn(f"{t} skipped: non-finite coordinate")
            self.res.ignored_counts[t] += 1
            return []
        if graphs and t != "INSERT":
            self.counted(e)
        return graphs

    def _line(self, e: DXFGraphic) -> list[Graph]:
        return [self.contour([SegmentGlyph(_xy(e.dxf.start), _xy(e.dxf.end))], e)]

    def _point(self, e: DXFGraphic) -> list[Graph]:
        return [self.contour([PointGlyph(_xy(e.dxf.location))], e)]

    def _circle(self, e: DXFGraphic) -> list[Graph]:
        kind = _extrusion_kind(e)
        if kind == 0:
            return self.flatten_fallback(e, "with tilted extrusion")
        c = _mirror(_xy(e.dxf.center), kind)
        return [self.contour([CircleGlyph(c, float(e.dxf.radius))], e)]

    def _arc(self, e: DXFGraphic) -> list[Graph]:
        kind = _extrusion_kind(e)
        if kind == 0:
            return self.flatten_fallback(e, "with tilted extrusion")
        s, en = float(e.dxf.start_angle), float(e.dxf.end_angle)
        if kind == -1:
            s, en = 180.0 - en, 180.0 - s
        c = _mirror(_xy(e.dxf.center), kind)
        a0 = math.radians(s)
        sweep = math.radians((en - s) % 360.0)
        if sweep < 1e-12:
            sweep = 2 * math.pi
        return [self.contour([ArcGlyph(c, float(e.dxf.radius), a0, a0 + sweep)], e)]

    def _ellipse(self, e: DXFGraphic) -> list[Graph]:
        kind = _extrusion_kind(e)
        if kind == 0:
            return self.flatten_fallback(e, "with tilted extrusion")
        t0, t1 = float(e.dxf.start_param), float(e.dxf.end_param)
        if kind == -1:  # minor axis = (-Z) x major = -(model perpendicular): P(t) -> model P(-t)
            t0, t1 = -t1, -t0
        if t1 <= t0:
            t1 += 2 * math.pi
        g = EllipseArcGlyph(_xy(e.dxf.center), _xy(e.dxf.major_axis), float(e.dxf.ratio), t0, t1)
        return [self.contour([g], e)]

    def _lwpolyline(self, e: DXFGraphic) -> list[Graph]:
        kind = _extrusion_kind(e)
        if kind == 0:
            return self.flatten_fallback(e, "with tilted extrusion")
        pts = list(e.get_points("xyb"))  # type: ignore[attr-defined]
        if not pts:
            return []
        sign = 1.0 if kind == 1 else -1.0
        verts = [
            LwPolyVertex(_mirror(Vec2(float(x), float(y)), kind), sign * float(b))
            for x, y, b in pts
        ]
        closed = 1 if e.closed else 0  # type: ignore[attr-defined]
        return [self.contour([LwPolylineGlyph(closed, verts)], e)]

    def _polyline(self, e: DXFGraphic) -> list[Graph]:
        if e.is_polygon_mesh or e.is_poly_face_mesh:  # type: ignore[attr-defined]
            self.warn("POLYLINE mesh/polyface skipped")
            return []
        closed = 1 if e.is_closed else 0  # type: ignore[attr-defined]
        verts: list[LwPolyVertex] = []
        if e.is_2d_polyline:  # type: ignore[attr-defined]
            kind = _extrusion_kind(e)
            if kind == 0:
                return self.flatten_fallback(e, "with tilted extrusion")
            sign = 1.0 if kind == 1 else -1.0
            for v in e.vertices:  # type: ignore[attr-defined]
                if v.dxf.get("flags", 0) & 16:  # spline frame control point, not on the curve
                    continue
                verts.append(
                    LwPolyVertex(
                        _mirror(_xy(v.dxf.location), kind), sign * float(v.dxf.get("bulge", 0.0))
                    )
                )
        else:
            for v in e.vertices:  # type: ignore[attr-defined]
                if v.dxf.get("flags", 0) & 16:
                    continue
                verts.append(LwPolyVertex(_xy(v.dxf.location)))
        if not verts:
            return []
        return [self.contour([LwPolylineGlyph(closed, verts)], e)]

    def _spline(self, e: DXFGraphic) -> list[Graph]:
        bs: BSpline = e.construction_tool()  # type: ignore[attr-defined]
        if bs.degree == 1:
            pts = [_xy(p) for p in bs.control_points]
            return [self.contour([LwPolylineGlyph(0, [LwPolyVertex(p) for p in pts])], e)]
        weights = list(bs.weights())
        rational = bool(weights) and (max(weights) - min(weights)) > 1e-12 * max(1.0, max(weights))
        if rational:
            self.warn("rational SPLINE re-interpolated as a non-rational cubic B-spline")
            samples = list(bs.points(_param_samples(bs)))
            bs = BSpline.from_fit_points(samples, degree=3)
        elif bs.degree < 3:
            bs = bs.degree_elevation(3 - bs.degree)
        elif bs.degree > 3:
            self.warn(f"degree-{bs.degree} SPLINE re-interpolated as a cubic B-spline")
            bs = BSpline.from_fit_points(list(bs.points(_param_samples(bs))), degree=3)
        ctrl = [_xy(p) for p in bs.control_points]
        knots = [float(k) for k in bs.knots()]
        if len(ctrl) < 4 or len(knots) != len(ctrl) + 4:
            raise ValueError(
                f"cubic B-spline needs >= 4 control points and n+4 knots ({len(ctrl)}, {len(knots)})"
            )
        return [self.contour([SplineGlyph(0, 0, ctrl, knots)], e)]

    def _solid(self, e: DXFGraphic) -> list[Graph]:
        corners = [_xy(e.dxf.get(f"vtx{i}", (0.0, 0.0))) for i in range(4)]
        if e.dxftype() in ("SOLID", "TRACE"):
            corners = [corners[0], corners[1], corners[3], corners[2]]  # SOLID vertex order 0-1-3-2
            kind = _extrusion_kind(e)
            if kind == 0:
                return self.flatten_fallback(e, "with tilted extrusion")
            corners = [_mirror(c, kind) for c in corners]
        uniq: list[Vec2] = []
        for c in corners:
            if not uniq or math.dist(c, uniq[-1]) > 1e-12:
                uniq.append(c)
        if len(uniq) > 1 and math.dist(uniq[0], uniq[-1]) <= 1e-12:
            uniq.pop()
        if len(uniq) < 2:
            return []
        return [self.contour([LwPolylineGlyph(1, [LwPolyVertex(c) for c in uniq])], e)]

    _trace = _solid
    _d3dface = _solid

    def _xline(self, e: DXFGraphic) -> list[Graph]:
        self.warn(f"{e.dxftype()} skipped (infinite line)")
        self.res.ignored_counts[e.dxftype()] += 1
        return []

    _ray = _xline

    def _text(self, e: DXFGraphic) -> list[Graph]:
        if not self.opts.text_to_curves:
            self.warn("TEXT/MTEXT skipped: text-to-curves (pd374) is off")
            self.res.ignored_counts[e.dxftype()] += 1
            return []
        if not fonttools_available():
            self.warn("TEXT/MTEXT skipped: fontTools not installed")
            self.res.ignored_counts[e.dxftype()] += 1
            return []
        from ezdxf.addons import text2path

        paths = text2path.make_paths_from_entity(e)  # type: ignore[arg-type]
        contours = [c for p in paths for c in self._path_contours(p, e)]
        if not contours:
            return []
        grp = Group(children=contours, layer=contours[0].layer)
        return [refresh_group(grp)]

    _attrib = _text

    def _mtext(self, e: DXFGraphic) -> list[Graph]:
        if not self.opts.text_to_curves or not fonttools_available():
            return self._text(e)
        from ezdxf.addons import MTextExplode

        scratch = ezdxf.new("R2000")
        with MTextExplode(scratch.modelspace()) as xpl:
            xpl.explode(e, destroy=False)  # type: ignore[arg-type]
        children: list[Contour] = []
        for t in scratch.modelspace():
            for g in self._text(t):  # type: ignore[arg-type]
                assert isinstance(g, Group)
                children.extend(g.children)
        if not children:
            return []
        for c in children:
            c.layer = self.layer_of(e)
        return [refresh_group(Group(children=children, layer=children[0].layer))]

    def _path_contours(self, path: Any, e: DXFGraphic) -> Iterator[Contour]:
        """One contour per closed/open sub-path: line runs -> lwpolyline, Beziers -> type-7 splines."""
        from ezdxf.path import Command

        for sub in path.sub_paths():
            glyphs: list[Glyph] = []
            cur = _xy(sub.start)
            run: list[Vec2] = [cur]
            for cmd in sub.commands():
                end = _xy(cmd.end)
                if cmd.type == Command.LINE_TO:
                    if math.dist(end, run[-1]) > 1e-12:
                        run.append(end)
                    cur = end
                    continue
                _flush_run(run, glyphs)
                if cmd.type == Command.MOVE_TO:
                    cur = end
                    run = [cur]
                    continue
                if cmd.type == Command.CURVE3_TO:  # degree elevation of a quadratic Bezier
                    c1 = _xy(cmd.ctrl)
                    ctrl = [
                        cur,
                        Vec2(cur[0] + 2 / 3 * (c1[0] - cur[0]), cur[1] + 2 / 3 * (c1[1] - cur[1])),
                        Vec2(end[0] + 2 / 3 * (c1[0] - end[0]), end[1] + 2 / 3 * (c1[1] - end[1])),
                        end,
                    ]
                else:  # CURVE4_TO
                    ctrl = [cur, _xy(cmd.ctrl1), _xy(cmd.ctrl2), end]
                # a cubic Bezier is the clamped cubic B-spline with knots 0,0,0,0,1,1,1,1 (03 §7 type 7)
                glyphs.append(SplineGlyph(0, 0, ctrl, [0.0] * 4 + [1.0] * 4))
                cur = end
                run = [cur]
            _flush_run(run, glyphs)
            if glyphs:
                yield self.contour(glyphs, e)

    def _insert(self, e: DXFGraphic) -> list[Graph]:
        out: list[Graph] = []
        for ins in e.multi_insert():  # type: ignore[attr-defined]
            parts: list[Graph] = []
            try:
                virtual = list(ins.virtual_entities())
            except ezdxf.DXFError as exc:
                self.warn(f"INSERT {ins.dxf.name!r} not exploded: {exc}")
                continue
            for sub in virtual:
                parts.extend(self.convert(sub))
            if self.opts.text_to_curves:
                for att in ins.attribs:
                    if not att.is_invisible:
                        parts.extend(self.convert_attrib(att))
            if self.opts.explode_blocks:
                out.extend(parts)
            else:
                kids = [c for g in parts for c, _ in iter_contours(g)]
                if kids:
                    out.append(refresh_group(Group(children=kids, layer=kids[0].layer)))
        if out:
            self.res.imported_counts["INSERT"] += 1
        return out

    def convert_attrib(self, att: DXFGraphic) -> list[Graph]:
        graphs = self._text(att)
        if graphs:
            self.counted(att)
        return graphs


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def import_dxf(drawing: Drawing, options: DxfImportOptions | None = None) -> DxfImportResult:
    """Convert an ezdxf :class:`Drawing` into a :class:`ChfDocument` (module docstring)."""
    opts = options or DxfImportOptions()
    doc = ChfDocument()
    res = DxfImportResult(document=doc)
    hdr = drawing.header
    res.acadver = str(hdr.get("$ACADVER", ""))
    res.codepage = str(hdr.get("$DWGCODEPAGE", ""))
    res.insunits = int(hdr.get("$INSUNITS", 0))
    res.layers = [
        decode_dxf_unicode(n) if has_dxf_unicode(n) else n
        for n in (lay.dxf.name for lay in drawing.layers)
    ]
    try:
        layout = drawing.layouts.get(opts.layout)
    except KeyError as exc:
        raise DxfImportError(f"no layout {opts.layout!r}") from exc
    conv = _Converter(opts, res, drawing)
    for e in layout:
        res.entity_counts[e.dxftype()] += 1
        doc.graphs.extend(conv.convert(e))
    return res


def read_dxf(
    source: str | Path | bytes | IO[bytes], options: DxfImportOptions | None = None
) -> DxfImportResult:
    """Read a DXF file (path, bytes or binary stream) into the model; see :func:`import_dxf`."""
    drawing, enc = _load_drawing(source)
    res = import_dxf(drawing, options)
    res.encoding = enc
    return res


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _layer_name(layer: int) -> str:
    return str(layer)


def doc_to_drawing(doc: ChfDocument, *, dxfversion: str = "R2000") -> Drawing:
    """Build an ezdxf drawing with one entity per glyph of every contour (UNVERIFIED mapping).

    Arcs whose stored sweep is negative are written as the CCW DXF arc over the
    same points; ellipses with ``ratio > 1`` are normalised (DXF requires <= 1).
    """
    dwg = ezdxf.new(dxfversion)
    if dwg.dxfversion < "AC1021":
        dwg.encoding = "gbk"  # ezdxf writes $DWGCODEPAGE ANSI_936 from this
    msp = dwg.modelspace()
    for g in doc.graphs:
        for contour, _ in iter_contours(g):
            name = _layer_name(contour.layer)
            if name not in dwg.layers:
                dwg.layers.add(name, color=(contour.layer % 255) + 1)
            attribs = {"layer": name}
            for el in contour.elements:
                _add_glyph(msp, el, attribs)
    return dwg


def _add_glyph(msp: Any, el: ContourElement, attribs: dict[str, Any]) -> None:
    g = el.glyph
    match g:
        case PointGlyph():
            msp.add_point((g.pt[0], g.pt[1]), dxfattribs=attribs)
        case SegmentGlyph():
            msp.add_line((g.p0[0], g.p0[1]), (g.p1[0], g.p1[1]), dxfattribs=attribs)
        case ArcGlyph():
            a0, a1 = g.start_angle, g.end_angle
            if a1 < a0:
                a0, a1 = a1, a0
            if a1 - a0 >= 2 * math.pi - 1e-12:
                msp.add_circle((g.center[0], g.center[1]), g.radius, dxfattribs=attribs)
            else:
                msp.add_arc(
                    (g.center[0], g.center[1]),
                    g.radius,
                    math.degrees(a0),
                    math.degrees(a1),
                    dxfattribs=attribs,
                )
        case CircleGlyph():
            msp.add_circle((g.center[0], g.center[1]), g.radius, dxfattribs=attribs)
        case EllipseArcGlyph():
            major, ratio, t0, t1 = g.major_axis, g.ratio, g.start_param, g.end_param
            if ratio > 1.0:  # rotate the parametrisation by 90 deg: new major = minor axis
                major = Vec2(-major[1] * ratio, major[0] * ratio)
                ratio = 1.0 / ratio
                t0, t1 = t0 - math.pi / 2, t1 - math.pi / 2
            msp.add_ellipse(
                (g.center[0], g.center[1]),
                major_axis=(major[0], major[1], 0.0),
                ratio=ratio,
                start_param=t0,
                end_param=t1,
                dxfattribs=attribs,
            )
        case LwPolylineGlyph():
            pts = [(v.pt[0], v.pt[1], 0.0, 0.0, v.bulge) for v in g.vertices]
            msp.add_lwpolyline(pts, format="xyseb", close=bool(g.closed), dxfattribs=attribs)
        case SplineGlyph():
            sp = msp.add_open_spline(
                [(p[0], p[1]) for p in g.control_points], degree=3, dxfattribs=attribs
            )
            sp.knots = list(g.knots)
        case _:
            raise TypeError(f"not a glyph: {g!r}")


def write_dxf(
    doc: ChfDocument, target: str | Path | IO[bytes], *, dxfversion: str = "R2000"
) -> None:
    """Write the document as DXF (see :func:`doc_to_drawing`) to a path or binary stream.

    R2000 text is encoded with :func:`nexcut.io.cp936.encode` to match ``ANSI_936``.
    """
    dwg = doc_to_drawing(doc, dxfversion=dxfversion)
    buf = io.StringIO()
    dwg.write(buf)
    text = buf.getvalue().replace("\r\n", "\n").replace("\n", "\r\n")
    data = text.encode("utf-8") if dwg.dxfversion >= "AC1021" else cp936.encode(text)
    if isinstance(target, (str, Path)):
        Path(target).write_bytes(data)
    else:
        target.write(data)
