#!/usr/bin/env python3
"""
chf_parse.py -- parser / JSON dumper / SVG renderer for Mlaser ".chf" job files
("scFlie" text container written by Module/CADModule.dll of Mlaser v0.0.0.52).

The reader below mirrors, line for line, the C++ readers recovered from
CADModule.dll (see docs/analysis/03-chf-format.md for the evidence):

  file            0x100da140 (open+check "scFlie"/"eof")   0x100e0740 (load entry)
  graph list      0x100a9900 (CCADModule::ReadGraphs)      trailer 0x100ab460
  CGlyContour     0x1006b4e0 (Read)   0x1005ab20 (Write)   graph type 8
  CGlyGroup       0x10076910          0x10074630           graph type 9
  CGlyText        0x100a4030          0x100a1fe0           graph type 10
  CGlyScan        0x100948e0          0x10091e80           graph type 11
  CGlyContourEx   0x10072270          0x100704e0           graph type 12
  glyph factory   0x10059b90  (types 1..7)
  CEditablePoint 1, CEditableSegment 2, CEditableArc 3, CEditableCircle 4,
  CEditableEllipsArc 5, CEditableLwpoly 6, CEditableSpline 7

Usage:
  chf_parse.py FILE.chf                 -> prints a short summary
  chf_parse.py --json OUT.json FILE.chf -> JSON dump of everything
  chf_parse.py --svg  OUT.svg  FILE.chf -> SVG rendering
  chf_parse.py --png  OUT.png  FILE.chf -> PNG rendering (stdlib rasterizer)
  chf_parse.py --check FILE.chf         -> recompute length/bbox/start/end and compare with file
  chf_parse.py --outdir DIR FILE...     -> DIR/<name>.json/.svg/.png for every file

Only the Python standard library is used.
"""
import json
import math
import os
import sys

# --------------------------------------------------------------------------
# Tokenizer (mirrors CLIFileBasic::ReadToken @ 0x100d9990)
# --------------------------------------------------------------------------


class ChfError(Exception):
    pass


class Tokenizer:
    """Line tokenizer.  One token per line; spaces and tabs are dropped anywhere
    in the line (the DLL drops them, it does not just trim); CR, LF or CRLF end a
    token.  Empty lines yield empty tokens (this matters for version<=4 files)."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.line_no = 0
        self.pushed = None

    def eof(self):
        return self.pos >= len(self.data)

    def tok(self) -> str:
        if self.pushed is not None:
            t, self.pushed = self.pushed, None
            return t
        buf = bytearray()
        n = len(self.data)
        while self.pos < n:
            c = self.data[self.pos]
            self.pos += 1
            if c in (0x0D, 0x0A):
                if c == 0x0D and self.pos < n and self.data[self.pos] == 0x0A:
                    self.pos += 1
                self.line_no += 1
                return bytes(buf).decode("gbk", errors="replace")
            if c in (0x20, 0x09):
                continue
            buf.append(c)
        self.line_no += 1
        return bytes(buf).decode("gbk", errors="replace")

    def unread(self, t):
        self.pushed = t

    # -- typed readers (0x100d9db0 / 0x100d9df0 / 0x100d9e30 / 0x100d9e80)
    def int(self) -> int:
        t = self.tok()
        if t == "":
            return 0  # atoi("") -- the DLL accepts an empty token as 0
        if not all(ch.isdigit() or ch == "-" for ch in t):
            raise ChfError(f"line {self.line_no}: expected int, got {t!r}")
        return int(t)

    def double(self) -> float:
        t = self.tok()
        if t == "":
            return 0.0
        if not all(ch.isdigit() or ch in "-." for ch in t) or t.count(".") > 1:
            raise ChfError(f"line {self.line_no}: expected double, got {t!r}")
        return float(t)

    def bool(self) -> bool:
        t = self.tok()
        return t == "1"

    def point(self):
        t = self.tok()
        if "," not in t:
            raise ChfError(f"line {self.line_no}: expected 'x,y', got {t!r}")
        x, y = t.split(",", 1)
        return [float(x or 0), float(y or 0)]

    def wstr(self) -> str:
        # WideCharToMultiByte(CP_ACP) on the writer side; CP_ACP == GBK on the
        # Chinese Windows boxes this software ships on.
        return self.tok()

    def expect(self, marker: str):
        t = self.tok()
        if t != marker.replace(" ", ""):
            raise ChfError(f"line {self.line_no}: expected {marker!r}, got {t!r}")
        return t

    def skip(self, n):
        for _ in range(n):
            self.tok()


def _legacy(version):
    """version in 2..4 -> writer used to emit reserved blank string lines."""
    return 2 <= version <= 4


# --------------------------------------------------------------------------
# Glyph readers (IGlyph::Read, vtable slot 23)
# --------------------------------------------------------------------------

GLYPH_NAMES = {
    1: "point",
    2: "segment",
    3: "arc",
    4: "circle",
    5: "ellipse_arc",
    6: "lwpolyline",
    7: "spline",
}


def read_glyph(tk: Tokenizer, gtype: int, version: int):
    if gtype == 1:  # CEditablePoint::Read 0x100895d0
        return {"type": "point", "pt": tk.point()}
    if gtype == 2:  # CEditableSegment::Read 0x1008ac90
        return {"type": "segment", "p0": tk.point(), "p1": tk.point()}
    if gtype == 3:  # CEditableArc::Read 0x1007eb10
        c = tk.point()
        r = tk.double()
        a0 = tk.double()
        a1 = tk.double()
        return {"type": "arc", "center": c, "radius": r, "start_angle": a0, "end_angle": a1}
    if gtype == 4:  # CEditableCircle::Read 0x10080420
        return {"type": "circle", "center": tk.point(), "radius": tk.double()}
    if gtype == 5:  # CEditableEllipsArc::Read 0x100828a0
        c = tk.point()
        major = tk.point()
        ratio = tk.double()
        t0 = tk.double()
        t1 = tk.double()
        return {"type": "ellipse_arc", "center": c, "major_axis": major,
                "ratio": ratio, "start_param": t0, "end_param": t1}
    if gtype == 6:  # CEditableLwpoly::Read 0x10088d50
        closed = tk.int()
        n = tk.int()
        verts = []
        for _ in range(n):
            p = tk.point()
            b = tk.double()
            verts.append({"pt": p, "bulge": b})
        return {"type": "lwpolyline", "closed": closed, "vertices": verts}
    if gtype == 7:  # CEditableSpline::Read 0x1008f250
        i1 = tk.int()
        i2 = tk.int()
        n = tk.int()
        ctrl = [tk.point() for _ in range(n)]
        nk = tk.int()
        knots = [tk.double() for _ in range(nk)]
        if n < 4 or nk != n + 4:
            raise ChfError(f"line {tk.line_no}: spline needs >=4 control points and n+4 knots (n={n}, knots={nk})")
        return {"type": "spline", "int1": i1, "int2": i2, "degree": 3,
                "control_points": ctrl, "knots": knots}
    raise ChfError(f"line {tk.line_no}: unknown glyph type {gtype}")


# --------------------------------------------------------------------------
# Graph readers (IGraph::Read, vtable slot 23)
# --------------------------------------------------------------------------

GRAPH_NAMES = {8: "contour", 9: "group", 10: "text", 11: "scan", 12: "contour_ex"}


def read_contour_body(tk: Tokenizer, version: int):
    """CGlyContour::Read @ 0x1006b4e0.  Called after '####graph NO:n' + type
    have been consumed (or after '####Group elem NO:n' / '####Path:n')."""
    g = {"type": "contour"}
    g["precision"] = tk.double()                      # [this+0xc8]
    tk.expect("<Glyphs>")
    if _legacy(version):
        tk.skip(10)
    g["length"] = tk.double()                         # [this+0x48]
    g["bbox_min"] = tk.point()                        # [this+0x28]
    g["bbox_max"] = tk.point()                        # [this+0x38]
    g["start"] = tk.point()                           # [this+0x60]
    g["end"] = tk.point()                             # [this+0x70]
    n = tk.int()
    if n < 1:
        raise ChfError(f"line {tk.line_no}: contour with {n} glyphs")
    glyphs = []
    for i in range(n):
        hdr = tk.tok()                                # "####Gly: i"
        if not hdr.startswith("####Gly:"):
            raise ChfError(f"line {tk.line_no}: expected '####Gly:', got {hdr!r}")
        direction = tk.int()                          # elem+4: 1 forward, -1 reversed
        gtype = tk.int()                              # glyph+8: glyph type id
        gl = read_glyph(tk, gtype, version)
        gl["direction"] = direction
        gl["type_id"] = gtype
        glyphs.append(gl)
        if _legacy(version):
            tk.skip(3)
    g["glyphs"] = glyphs
    tk.expect("<End Glyphs>")
    g["layer"] = tk.int()                             # [this+0xc]
    g["int58"] = tk.int()                             # [this+0x58]
    tk.expect("<Crafts>")
    if _legacy(version):
        tk.skip(20)
    cr = {}
    cr["compensate_type"] = tk.int()                  # [this+0xf0]  -1 none, 2/3 side
    cr["compensate_width"] = tk.double()              # [this+0xf8]
    tk.expect("<PWM Control>")
    cr["pwm_enable"] = tk.int()                       # [this+0x104]
    npwm = tk.int()
    pwm = []
    for _ in range(npwm):
        a = tk.double()                               # [0x108][i]
        b = tk.double()                               # [0x118][i]
        pwm.append([a, b])
    cr["pwm_nodes"] = pwm
    n2 = tk.int()
    cr["pwm_close_pos_ratios"] = [tk.double() for _ in range(n2)]   # [0x12c]
    tk.expect("<End PWM Control>")
    cr["double170"] = tk.double()                     # [this+0x170]
    cr["double188"] = tk.double()                     # [this+0x188]
    tk.expect("<GuideCurve Para>")
    lead = {}
    lead["type"] = tk.int()                           # [this+0x1a8]
    lead["angle_deg"] = tk.double()                   # [this+0x1b0]
    lead["length"] = tk.double()                      # [this+0x1b8]
    if version > 1:
        lead["arc_radius"] = tk.double()              # [this+0x1c0]
    lead["flag"] = tk.bool()                          # [this+0x1c8]
    cr["lead_line"] = lead
    tk.expect("<End GuideCurve Para>")
    if version > 2:
        tk.expect("<coolPos Para>")
        nc = tk.int()
        cr["cool_pos"] = [tk.double() for _ in range(nc)]   # [this+0x14c]
        tk.expect("<End coolPos Para>")
    tk.expect("<End Crafts>")
    g["crafts"] = cr
    return g


def read_group_body(tk: Tokenizer, version: int):
    """CGlyGroup::Read @ 0x10076910"""
    g = {"type": "group"}
    if _legacy(version):
        tk.skip(3)
    g["length"] = tk.double()
    g["bbox_min"] = tk.point()
    g["bbox_max"] = tk.point()
    g["start"] = tk.point()
    g["end"] = tk.point()
    n = tk.int()
    kids = []
    for i in range(n):
        hdr = tk.tok()                                # "####Group elem NO:i"
        if not hdr.startswith("####Groupelem"):
            raise ChfError(f"line {tk.line_no}: expected '####Group elem NO:', got {hdr!r}")
        kids.append(read_contour_body(tk, version))   # children are always CGlyContour
    g["children"] = kids
    g["layer"] = tk.int()
    g["int58"] = tk.int()
    return g


def read_contour_ex_body(tk: Tokenizer, version: int):
    """CGlyContourEx::Read @ 0x10072270"""
    g = read_group_body(tk, version)
    g["type"] = "contour_ex"
    n = tk.int()
    links = []
    for i in range(n):
        hdr = tk.tok()                                # "####ContourEx link info:i"
        e = {}
        e["int0"] = tk.int()
        e["int4"] = tk.int()
        e["d8"] = tk.double()
        e["d10"] = tk.double()
        e["d38"] = tk.double()
        e["d40"] = tk.double()
        t = tk.tok()
        e["pt18"] = [float(v) for v in t.split(",")] if e["int0"] == 1 and "," in t else t
        t = tk.tok()
        e["pt28"] = [float(v) for v in t.split(",")] if e["int4"] == 1 and "," in t else t
        links.append(e)
    g["links"] = links
    return g


def read_scan_body(tk: Tokenizer, version: int):
    """CGlyScan::Read @ 0x100948e0"""
    g = read_group_body(tk, version)
    g["type"] = "scan"
    tk.expect("<Scan path>")
    if _legacy(version):
        tk.skip(5)
    n = tk.int()
    paths = []
    for i in range(n):
        hdr = tk.tok()                                # "####Path:i"
        paths.append(read_contour_body(tk, version))
    g["paths"] = paths
    tk.expect("<End Scan path>")
    return g


def read_text_body(tk: Tokenizer, version: int):
    """CGlyText::Read @ 0x100a4030"""
    g = {"type": "text"}
    if _legacy(version):
        tk.skip(5)
    g["position"] = tk.point()                        # [this+0x120]
    g["d130"] = tk.double()
    g["d138"] = tk.double()
    g["d140"] = tk.double()
    g["text"] = tk.wstr()                             # [this+0xb8]
    tk.expect("<Text para>")
    g["font"] = tk.wstr()                             # [this+0xd8], default "宋体"
    g["font_d0"] = tk.double()                        # default 1.0
    g["font_d1"] = tk.double()                        # default 20.0
    g["font_d2"] = tk.double()                        # default 0.0
    tk.expect("<End Text para>")
    if version >= 4:
        grp = read_group_body(tk, version)
        g["outline"] = grp
        g["layer"] = grp["layer"]
        g["int58"] = grp["int58"]
    else:
        g["layer"] = tk.int()
        g["int58"] = tk.int()
    return g


def read_graph(tk: Tokenizer, gtype: int, version: int):
    if gtype == 8:
        return read_contour_body(tk, version)
    if gtype == 9:
        return read_group_body(tk, version)
    if gtype == 10:
        return read_text_body(tk, version)
    if gtype == 11:
        return read_scan_body(tk, version)
    if gtype == 12:
        return read_contour_ex_body(tk, version)
    raise ChfError(f"line {tk.line_no}: unknown graph type {gtype}")


# --------------------------------------------------------------------------
# File level
# --------------------------------------------------------------------------


def parse_chf(data: bytes, strict_eof=True):
    if not data.startswith(b"scFlie"):
        raise ChfError("missing 'scFlie' magic (DLL error code 5)")
    tail = data.rstrip(b"\r\n\t \x00")
    if strict_eof and not tail.endswith(b"eof"):
        raise ChfError("file does not end with 'eof' (DLL error code 4)")
    tk = Tokenizer(data)
    tk.expect("scFlie")
    version = tk.int()
    doc = {"magic": "scFlie", "version": version}
    tk.expect("<Begin Graphs>")
    if _legacy(version):
        tk.skip(5)
    n = tk.int()
    graphs = []
    for i in range(n):
        hdr = tk.tok()                                # "####graph NO:i"
        if not hdr.startswith("####graphNO:"):
            raise ChfError(f"line {tk.line_no}: expected '####graph NO:', got {hdr!r}")
        gtype = tk.int()                              # graph+8
        g = read_graph(tk, gtype, version)
        g["type_id"] = gtype
        g["index"] = i + 1
        graphs.append(g)
    doc["graphs"] = graphs
    tk.expect("<End Graphs>")
    doc["trailer_bool"] = tk.bool()
    doc["trailer_double"] = tk.double()
    doc["trailer_pt1"] = tk.point()
    doc["trailer_pt2"] = tk.point()
    t = tk.tok()
    if t != "eof":
        raise ChfError(f"line {tk.line_no}: expected 'eof', got {t!r}")
    return doc


# --------------------------------------------------------------------------
# Geometry flattening (for SVG and for a future motion pipeline)
# --------------------------------------------------------------------------


FLATTEN_STEP = 0.2   # mm chord step used when flattening curves (check_doc lowers it)


def _arc_pts(c, r, a0, a1, n=None):
    sweep = a1 - a0
    if n is None:
        n = max(8, int(abs(sweep) * max(r, 1.0) / FLATTEN_STEP) + 1)
        n = min(n, 20000)
    return [[c[0] + r * math.cos(a0 + sweep * i / n), c[1] + r * math.sin(a0 + sweep * i / n)]
            for i in range(n + 1)]


def _bulge_pts(p0, p1, bulge):
    if abs(bulge) < 1e-12:
        return [p0, p1]
    theta = 4.0 * math.atan(bulge)                    # included angle, sign = direction
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    chord = math.hypot(dx, dy)
    if chord < 1e-12:
        return [p0, p1]
    r = chord / (2.0 * math.sin(abs(theta) / 2.0))
    mx, my = (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0
    d = math.sqrt(max(r * r - (chord / 2.0) ** 2, 0.0))
    # centre lies to the left of the chord for CCW (positive bulge)
    nx, ny = -dy / chord, dx / chord
    if bulge > 0:
        cx, cy = mx + nx * d, my + ny * d
    else:
        cx, cy = mx - nx * d, my - ny * d
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    return _arc_pts([cx, cy], r, a0, a0 + theta)


def _ellipse_pts(c, major, ratio, t0, t1):
    if t1 <= t0 + 1e-12:
        t1 += 2 * math.pi
    a = math.hypot(major[0], major[1])
    n = max(16, int((t1 - t0) * max(a, 1.0) / FLATTEN_STEP) + 1)
    n = min(n, 20000)
    px, py = -major[1] * ratio, major[0] * ratio   # minor axis vector
    out = []
    for i in range(n + 1):
        t = t0 + (t1 - t0) * i / n
        out.append([c[0] + math.cos(t) * major[0] + math.sin(t) * px,
                    c[1] + math.cos(t) * major[1] + math.sin(t) * py])
    return out


def _bspline_pts(ctrl, knots, degree=3, n=None):
    m = len(ctrl)
    if n is None:
        n = max(24, 8 * m)
    lo, hi = knots[degree], knots[m]
    out = []
    for i in range(n + 1):
        u = lo + (hi - lo) * i / n
        if i == n:
            u = hi - 1e-12 * max(abs(hi), 1)
        # find span
        k = degree
        while k < m - 1 and u >= knots[k + 1]:
            k += 1
        d = [list(ctrl[j]) for j in range(k - degree, k + 1)]
        for r in range(1, degree + 1):
            for j in range(degree, r - 1, -1):
                i0 = k - degree + j
                den = knots[i0 + degree - r + 1] - knots[i0]
                alpha = 0.0 if den == 0 else (u - knots[i0]) / den
                d[j] = [(1 - alpha) * d[j - 1][0] + alpha * d[j][0],
                        (1 - alpha) * d[j - 1][1] + alpha * d[j][1]]
        out.append(d[degree])
    return out


def flatten_glyph(gl):
    """Return a list of polylines (each a list of [x,y]) approximating the glyph,
    in the glyph's stored direction (direction flag is NOT applied)."""
    t = gl["type"]
    if t == "point":
        return [[gl["pt"]]]
    if t == "segment":
        return [[gl["p0"], gl["p1"]]]
    if t == "arc":
        return [_arc_pts(gl["center"], gl["radius"], gl["start_angle"], gl["end_angle"])]
    if t == "circle":
        return [_arc_pts(gl["center"], gl["radius"], 0.0, 2 * math.pi)]
    if t == "ellipse_arc":
        return [_ellipse_pts(gl["center"], gl["major_axis"], gl["ratio"],
                             gl["start_param"], gl["end_param"])]
    if t == "lwpolyline":
        v = gl["vertices"]
        pts = []
        segs = len(v) if gl["closed"] else len(v) - 1
        for i in range(segs):
            p0 = v[i]["pt"]
            p1 = v[(i + 1) % len(v)]["pt"]
            seg = _bulge_pts(p0, p1, v[i]["bulge"])
            if pts:
                seg = seg[1:]
            pts.extend(seg)
        return [pts]
    if t == "spline":
        return [_bspline_pts(gl["control_points"], gl["knots"], gl["degree"])]
    return []


def iter_contours(graph):
    """Yield (contour_dict, path) for every CGlyContour inside a graph."""
    t = graph["type"]
    if t == "contour":
        yield graph, "self"
    elif t in ("group", "contour_ex"):
        for k in graph["children"]:
            yield k, "child"
    elif t == "scan":
        for k in graph["children"]:
            yield k, "child"
        for k in graph["paths"]:
            yield k, "scanpath"
    elif t == "text":
        if "outline" in graph:
            for k in graph["outline"]["children"]:
                yield k, "child"


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------

LAYER_COLORS = ["#d62728", "#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd",
                "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f"]


def to_svg(doc, title="", show_labels=True):
    polylines = []   # (pts, color, kind)
    markers = []     # (x, y, color, text)
    allpts = []
    for g in doc["graphs"]:
        for c, kind in iter_contours(g):
            color = LAYER_COLORS[c.get("layer", 0) % len(LAYER_COLORS)]
            if kind == "scanpath":
                color = "#999999"
            for gl in c["glyphs"]:
                for pl in flatten_glyph(gl):
                    polylines.append((pl, color, gl["type"]))
                    allpts.extend(pl)
            markers.append((c["start"][0], c["start"][1], color, f"{g['index']}" if kind == "self" else ""))
        if g["type"] == "text":
            markers.append((g["position"][0], g["position"][1], "#000", f"T{g['index']}:{g['text']}"))
            allpts.append(g["position"])
    if not allpts:
        allpts = [[0, 0], [10, 10]]
    xs = [p[0] for p in allpts]
    ys = [p[1] for p in allpts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w, h = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
    margin = 0.06 * max(w, h) + 1.0
    vb = (minx - margin, -(maxy + margin), w + 2 * margin, h + 2 * margin)
    sw = max(w, h) / 400.0
    out = []
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb[0]:.4f} {vb[1]:.4f} {vb[2]:.4f} {vb[3]:.4f}" '
               f'width="800" height="{800 * vb[3] / vb[2]:.0f}">')
    out.append('<rect x="%.4f" y="%.4f" width="%.4f" height="%.4f" fill="white"/>' % vb)
    if title:
        out.append(f'<text x="{vb[0] + sw * 4:.4f}" y="{vb[1] + sw * 12:.4f}" font-size="{sw * 10:.4f}" fill="#444">{title}</text>')
    for pts, color, kind in polylines:
        if len(pts) == 1:
            x, y = pts[0]
            out.append(f'<circle cx="{x:.4f}" cy="{-y:.4f}" r="{sw * 2:.4f}" fill="{color}"/>')
            continue
        d = " ".join(f"{p[0]:.4f},{-p[1]:.4f}" for p in pts)
        out.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="{sw:.4f}"/>')
    for x, y, color, text in markers:
        out.append(f'<circle cx="{x:.4f}" cy="{-y:.4f}" r="{sw * 1.5:.4f}" fill="none" stroke="{color}" stroke-width="{sw * 0.5:.4f}"/>')
        if show_labels and text:
            out.append(f'<text x="{x + sw * 2:.4f}" y="{-y - sw * 2:.4f}" font-size="{sw * 6:.4f}" fill="{color}">{text}</text>')
    out.append("</svg>")
    return "\n".join(out)


# --------------------------------------------------------------------------
# PNG (stdlib only; used to eyeball the SVG output without an SVG rasterizer)
# --------------------------------------------------------------------------


def _hex_rgb(s):
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def to_png(doc, width=800, pad=24):
    import zlib
    import struct

    lines = []
    allpts = []
    for g in doc["graphs"]:
        for c, kind in iter_contours(g):
            color = _hex_rgb(LAYER_COLORS[c.get("layer", 0) % len(LAYER_COLORS)])
            if kind == "scanpath":
                color = (150, 150, 150)
            for gl in c["glyphs"]:
                for pl in flatten_glyph(gl):
                    lines.append((pl, color))
                    allpts.extend(pl)
    if not allpts:
        allpts = [[0, 0], [10, 10]]
    xs = [p[0] for p in allpts]; ys = [p[1] for p in allpts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w, h = max(maxx - minx, 1e-6), max(maxy - miny, 1e-6)
    scale = (width - 2 * pad) / max(w, h)
    W = width
    H = int(h * scale) + 2 * pad
    img = bytearray(b"\xff" * (W * H * 3))

    def put(x, y, col):
        if 0 <= x < W and 0 <= y < H:
            i = (y * W + x) * 3
            img[i:i + 3] = bytes(col)

    def tr(p):
        return (int(round(pad + (p[0] - minx) * scale)), int(round(H - pad - (p[1] - miny) * scale)))

    for pl, col in lines:
        if len(pl) == 1:
            x, y = tr(pl[0])
            for dx in (-2, -1, 0, 1, 2):
                put(x + dx, y, col); put(x, y + dx, col)
            continue
        for a, b in zip(pl, pl[1:]):
            x0, y0 = tr(a); x1, y1 = tr(b)
            n = max(abs(x1 - x0), abs(y1 - y0), 1)
            for i in range(n + 1):
                put(x0 + (x1 - x0) * i // n, y0 + (y1 - y0) * i // n, col)
    raw = b"".join(b"\x00" + bytes(img[y * W * 3:(y + 1) * W * 3]) for y in range(H))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


# --------------------------------------------------------------------------
# Self check: recompute contour length / bbox / endpoints from the geometry
# and compare with the values stored in the file.
# --------------------------------------------------------------------------


def _poly_len(pts):
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def check_doc(doc, path):
    global FLATTEN_STEP
    saved, FLATTEN_STEP = FLATTEN_STEP, 0.002
    try:
        return _check_doc(doc, path)
    finally:
        FLATTEN_STEP = saved


def _check_doc(doc, path):
    ok = True
    for g in doc["graphs"]:
        for c, kind in iter_contours(g):
            total = 0.0
            pts = []
            first = last = None
            for gl in c["glyphs"]:
                for pl in flatten_glyph(gl):
                    if gl["direction"] == -1:
                        pl = pl[::-1]
                    total += _poly_len(pl)
                    pts.extend(pl)
                    if first is None:
                        first = pl[0]
                    last = pl[-1]
            if not pts:
                continue
            bb = [[min(p[0] for p in pts), min(p[1] for p in pts)],
                  [max(p[0] for p in pts), max(p[1] for p in pts)]]
            tol = 1e-3 * max(1.0, total)
            problems = []
            if abs(total - c["length"]) > tol:
                problems.append(f"length file={c['length']:.6f} computed={total:.6f}")
            if math.dist(bb[0], c["bbox_min"]) > 5e-3 * max(1.0, total) or math.dist(bb[1], c["bbox_max"]) > 5e-3 * max(1.0, total):
                problems.append(f"bbox file={c['bbox_min']},{c['bbox_max']} computed={bb}")
            if math.dist(first, c["start"]) > tol:
                problems.append(f"start file={c['start']} computed={first}")
            if math.dist(last, c["end"]) > tol:
                problems.append(f"end file={c['end']} computed={last}")
            if problems:
                ok = False
                print(f"  CHECK graph {g['index']} ({kind}): " + "; ".join(problems))
    print(f"  check: {'OK' if ok else 'MISMATCH'} ({path})")
    return ok


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def summarize(doc, path):
    print(f"{path}: version {doc['version']}, {len(doc['graphs'])} graph(s); "
          f"trailer bool={doc['trailer_bool']} double={doc['trailer_double']} "
          f"pt1={doc['trailer_pt1']} pt2={doc['trailer_pt2']}")
    for g in doc["graphs"]:
        cs = list(iter_contours(g))
        gly = sum(len(c["glyphs"]) for c, _ in cs)
        types = sorted({gl["type"] for c, _ in cs for gl in c["glyphs"]})
        extra = ""
        if g["type"] == "contour":
            cr = g["crafts"]
            extra = (f" layer={g['layer']} len={g['length']:.3f} closed_by_endpoints="
                     f"{math.dist(g['start'], g['end']) <= g['precision']} lead={cr['lead_line']} "
                     f"comp=({cr['compensate_type']},{cr['compensate_width']}) pwm_en={cr['pwm_enable']} "
                     f"cool={cr.get('cool_pos')}")
        print(f"  graph {g['index']}: type {g['type_id']} ({g['type']}), {len(cs)} contour(s), "
              f"{gly} glyph(s) {types}{extra}")


def main(argv):
    args = argv[1:]
    json_out = svg_out = outdir = png_out = None
    check = False
    files = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--json":
            json_out = args[i + 1]; i += 2
        elif a == "--svg":
            svg_out = args[i + 1]; i += 2
        elif a == "--outdir":
            outdir = args[i + 1]; i += 2
        elif a == "--png":
            png_out = args[i + 1]; i += 2
        elif a == "--check":
            check = True; i += 1
        else:
            files.append(a); i += 1
    if not files:
        print(__doc__)
        return 2
    rc = 0
    for f in files:
        data = open(f, "rb").read()
        try:
            doc = parse_chf(data)
        except ChfError as e:
            print(f"{f}: PARSE ERROR: {e}")
            rc = 1
            continue
        summarize(doc, f)
        if check and not check_doc(doc, f):
            rc = 1
        base = os.path.splitext(os.path.basename(f))[0]
        if outdir:
            os.makedirs(outdir, exist_ok=True)
            tag = os.path.basename(os.path.dirname(os.path.abspath(f)))
            stem = f"{tag}_{base}"
            with open(os.path.join(outdir, stem + ".json"), "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=1, ensure_ascii=False)
            with open(os.path.join(outdir, stem + ".svg"), "w", encoding="utf-8") as fh:
                fh.write(to_svg(doc, title=f"{tag}/{base}.chf"))
            with open(os.path.join(outdir, stem + ".png"), "wb") as fh:
                fh.write(to_png(doc))
        if json_out:
            with open(json_out, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=1, ensure_ascii=False)
        if svg_out:
            with open(svg_out, "w", encoding="utf-8") as fh:
                fh.write(to_svg(doc, title=base))
        if png_out:
            with open(png_out, "wb") as fh:
                fh.write(to_png(doc))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
