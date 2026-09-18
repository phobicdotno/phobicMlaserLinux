"""Graph (``IGraph``) records, crafts and the ``.chf`` document (analysis 03 §5-§8).

The classes keep every scalar the original serialises - including fields whose
meaning is still unknown (``int58``, ``double170``, ``double188``, the text
doubles, the contour-ex link records) - so a document read and written again
preserves the Windows program's values (03 §14 "design notes").  Derived cached
values (``length``, bbox, ``start``, ``end``) are stored as read; recomputation
lives in :mod:`nexcut.model.flatten`.

**``legacy_reserved*`` slots (03 §9, docs/DECISIONS.md D14).**  Versions 2-4 emit a fixed
number of extra lines at seven defined places.  The vendor reader consumes them with a
counted ``ReadToken`` loop and never looks at them; every shipped sample leaves them empty,
and no v2-v4 writer exists in the shipped DLL.  Rather than drop what cannot be modelled,
each record keeps the raw text of the lines it owns, so a v2-v4 file written by some other
producer round-trips byte for byte.  Entries are tokens as read, latin-1 decoded (the
tokenizer has already dropped spaces and tabs, 03 §4.2); on write a short list is padded
with empty lines and a list longer than the slot count is refused.  Reserved lines that are
all empty - the only case any shipped sample has - are kept as the empty list, so a record
the port built and the same record read back compare equal.  The lists are always empty for
versions 1 and 5, which have no reserved lines.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Literal

from nexcut.model.glyph import Glyph, Vec2

ZERO = Vec2(0.0, 0.0)

CURRENT_VERSION = 5
"""Format version the current writer emits: ``mov [esi+0x970],5`` at ``0x100e5662`` (03 §3)."""


class GraphType(IntEnum):
    """Graph type ids ``[graph+0x8]`` (03 §6, factory jump table ``0x100a4844``)."""

    CONTOUR = 8
    GROUP = 9
    TEXT = 10
    SCAN = 11
    CONTOUR_EX = 12


@dataclass(slots=True)
class LeadLine:
    """``<GuideCurve Para>`` - lead-in ("guide curve", ``CGuideCurve``) parameters (03 §6.1.1).

    Defaults are the ``.rdata`` block at ``0x101119e8`` copied by the contour
    constructor: type 0, 90 deg, 5 mm, arc radius 0, flag 0.
    ``arc_radius`` is ``None`` only for version-1 files (line absent, 03 §9).
    ``type`` enum beyond {0 = none, 2 = used on this machine} is unknown (03 §12, O11).
    """

    type: int = 0
    angle_deg: float = 90.0
    length: float = 5.0
    arc_radius: float | None = 0.0
    flag: bool = False


@dataclass(slots=True)
class Crafts:
    """``<Crafts>`` block of one contour, kept as a typed opaque struct (03 §6.1.1).

    Field meanings marked INFERENCE in 03 §6.1.1/§12; values round-trip
    unchanged whatever they mean.  Defaults are the ``CGlyContour`` constructor
    values (``0x10064f48..0x10064fa0``).  ``cool_pos`` is ``None`` for files of
    version <= 2 (block absent, 03 §9).
    """

    compensate_type: int = -1
    """``[+0xf0]``: -1 none; 2/3 inside/outside (INFERENCE medium, O11)."""
    compensate_width: float = 0.0
    """``[+0xf8]``: kerf compensation distance, mm (INFERENCE high)."""
    pwm_enable: int = 1
    """``[+0x104]``: per-contour PWM curve enable (INFERENCE high)."""
    pwm_nodes: list[tuple[float, float]] = field(default_factory=list)
    """``[+0x108]/[+0x118]`` pairs written interleaved (semantics INFERENCE medium)."""
    pwm_close_pos_ratios: list[float] = field(default_factory=list)
    """``[+0x12c]``: path ratios where the laser switches off (INFERENCE medium)."""
    double170: float = 0.0
    """``[+0x170]``: a path ratio 0..1, probably ``LeadPosPrecent`` (INFERENCE medium)."""
    double188: float = 0.0
    """``[+0x188]``: added to the length, probably over-cut mm (INFERENCE medium-low)."""
    lead_line: LeadLine = field(default_factory=LeadLine)
    cool_pos: list[float] | None = field(default_factory=list)
    """``[+0x14c]``: cooling-stop positions (unit undetermined)."""
    legacy_reserved: list[str] = field(default_factory=list)
    """v2-v4: the 20 reserved lines after ``<Crafts>`` (``mov ebx,0x14`` @``0x1006b6f7``)."""


@dataclass(slots=True)
class ContourElement:
    """One entry of the contour's 8-byte ``{IGlyph*, int dir}`` vector ``[+0xa8]`` (03 §6.1).

    ``direction`` is normally :class:`~nexcut.model.glyph.Direction` (``1``/``-1``);
    any other int read from a file is preserved as-is.
    """

    glyph: Glyph
    direction: int = 1
    legacy_reserved: list[str] = field(default_factory=list)
    """v2-v4: the 3 reserved lines after this glyph (``mov ebx,3`` @``0x1006b64f``)."""


@dataclass(slots=True)
class Contour:
    """``CGlyContour`` (type 8): one connected tool path plus crafts (03 §6.1).

    ``precision`` is a constructor argument chosen by the caller
    (0.01 in ``autosave.chf``, 0.1 in ``tempGraph.chf`` and the v4 files).
    ``int58`` defaults to 1 (``0x1005f59a``); only its parity is consumed.
    """

    precision: float = 0.01  # UNVERIFIED: no single default exists (caller-chosen ctor arg, 03 §6.1)
    length: float = 0.0
    bbox_min: Vec2 = ZERO
    bbox_max: Vec2 = ZERO
    start: Vec2 = ZERO
    end: Vec2 = ZERO
    elements: list[ContourElement] = field(default_factory=list)
    layer: int = 0
    int58: int = 1
    crafts: Crafts = field(default_factory=Crafts)
    legacy_reserved_glyphs: list[str] = field(default_factory=list)
    """v2-v4: the 10 reserved lines after ``<Glyphs>`` (``mov ebx,0xa`` @``0x1006b51a``)."""

    TYPE = GraphType.CONTOUR


@dataclass(slots=True)
class Group:
    """``CGlyGroup`` (type 9): a list of ``CGlyContour`` children (03 §6.2).

    Children are always contours (the reader constructs ``CGlyContour``
    directly at ``0x100769f7``).  The group has no crafts of its own.
    """

    length: float = 0.0
    bbox_min: Vec2 = ZERO
    bbox_max: Vec2 = ZERO
    start: Vec2 = ZERO
    end: Vec2 = ZERO
    children: list[Contour] = field(default_factory=list)
    layer: int = 0
    int58: int = 1
    legacy_reserved: list[str] = field(default_factory=list)
    """v2-v4: the 3 reserved lines at the start of the group body (``mov edi,3``
    @``0x1007694b``).  Inherited by :class:`ContourEx` and :class:`Scan`, and carried by the
    outline group of a :class:`Text`."""

    TYPE = GraphType.GROUP


@dataclass(slots=True)
class LinkInfo:
    """One 0x48-byte ``####ContourEx link info`` record (03 §6.5); all fields of unknown meaning.

    The writer (``0x100704e0``) always emits both points; the reader
    (``0x10072270``) only parses ``pt18`` when ``int0 == 1`` and ``pt28`` when
    ``int4 == 1``.  The raw line tokens are kept so a lossless re-write and the
    reference JSON dump are both possible.
    """

    int0: int = 0
    int4: int = 0
    d8: float = 0.0
    d10: float = 0.0
    d38: float = 0.0
    d40: float = 0.0
    pt18: Vec2 = ZERO
    pt28: Vec2 = ZERO
    pt18_token: str | None = None
    pt28_token: str | None = None


@dataclass(slots=True)
class ContourEx(Group):
    """``CGlyContourEx`` (type 12): a group body plus link records (03 §6.5)."""

    links: list[LinkInfo] = field(default_factory=list)

    TYPE = GraphType.CONTOUR_EX


@dataclass(slots=True)
class Scan(Group):
    """``CGlyScan`` (type 11): source contours (group body) plus generated fly-cut paths (03 §6.4)."""

    paths: list[Contour] = field(default_factory=list)
    legacy_reserved_paths: list[str] = field(default_factory=list)
    """v2-v4: the 5 reserved lines after ``<Scan path>`` (``mov ebx,5`` @``0x10094944``)."""

    TYPE = GraphType.SCAN


@dataclass(slots=True)
class Text:
    """``CGlyText`` (type 10): text parameters plus (v>=4) the outline group (03 §6.3).

    ``d130/d138/d140`` and ``font_d0..2`` have unknown meaning (INFERENCE low);
    font defaults ``宋体`` / 1.0 / 20.0 / 0.0 come from the reader prologue
    ``0x100a4060..0x100a40a5``.  For version >= 4 ``outline`` holds the group
    body and ``layer``/``int58`` mirror ``outline.layer``/``outline.int58``;
    for version < 4 ``outline`` is ``None``.
    """

    position: Vec2 = ZERO
    d130: float = 0.0  # UNVERIFIED default: text ctor defaults not recovered (03 §6.3)
    d138: float = 0.0  # UNVERIFIED default
    d140: float = 0.0  # UNVERIFIED default
    text: str = ""
    font: str = "宋体"
    font_d0: float = 1.0
    font_d1: float = 20.0
    font_d2: float = 0.0
    outline: Group | None = field(default_factory=Group)
    layer: int = 0
    int58: int = 1
    legacy_reserved: list[str] = field(default_factory=list)
    """v2-v4: the 5 reserved lines before the text record (``mov ebx,5`` @``0x100a40b7``)."""

    TYPE = GraphType.TEXT


Graph = Contour | Group | ContourEx | Scan | Text

GRAPH_NAMES: dict[GraphType, str] = {
    GraphType.CONTOUR: "contour",
    GraphType.GROUP: "group",
    GraphType.TEXT: "text",
    GraphType.SCAN: "scan",
    GraphType.CONTOUR_EX: "contour_ex",
}
"""Names used by ``tools/chf_parse.py`` JSON dumps (03 §11)."""


@dataclass(slots=True)
class ChfDocument:
    """A whole ``.chf`` file: version, graphs in cut order, trailer (03 §5, §8, §10).

    ``trailer_bool``/``trailer_double`` are the ``Save(path, bool, double)``
    arguments passed by MainApp (meaning unknown, always 0/0.0 in samples);
    ``trailer_pt1``/``trailer_pt2`` are ``[CCADModule+0x9cd0]``/``[+0x9ce0]``.
    """

    version: int = CURRENT_VERSION
    graphs: list[Graph] = field(default_factory=list)
    trailer_bool: bool = False
    trailer_double: float = 0.0
    trailer_pt1: Vec2 = ZERO
    trailer_pt2: Vec2 = ZERO
    legacy_reserved: list[str] = field(default_factory=list)
    """v2-v4: the 5 reserved lines after ``<Begin Graphs>`` (``mov esi,5`` @``0x100a991d``)."""


ContourKind = Literal["self", "child", "scanpath"]


def graph_type(graph: Graph) -> GraphType:
    """Return the type id a graph is written with (03 §6)."""
    return graph.TYPE


def iter_contours(graph: Graph) -> Iterator[tuple[Contour, ContourKind]]:
    """Yield ``(contour, kind)`` for every ``CGlyContour`` inside ``graph`` (03 §6).

    Order matches ``tools/chf_parse.py``: a contour yields itself; groups and
    contour-ex yield children; scans yield children then scan paths; text yields
    its outline children.
    """
    if isinstance(graph, Contour):
        yield graph, "self"
    elif isinstance(graph, Scan):
        for k in graph.children:
            yield k, "child"
        for k in graph.paths:
            yield k, "scanpath"
    elif isinstance(graph, Group):
        for k in graph.children:
            yield k, "child"
    elif isinstance(graph, Text):
        if graph.outline is not None:
            for k in graph.outline.children:
                yield k, "child"
