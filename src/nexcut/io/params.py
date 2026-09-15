"""Vendor parameter XML files: HardPara / ManuPara / LayerPara, backups, technology presets.

File format (EVIDENCE, 01 §0.1/§0.3, 02 §2.1/§6.2, re-verified on 65 vendor files):

* no XML declaration, no BOM, UTF-8, CRLF after every line, no indentation;
* ``<ParameterRoot>`` -> group elements ``P<Section>`` (layer groups ``P<Section><slot>``)
  -> one leaf element per ``Element`` prefix, carrying every parameter as an attribute
  ``Attr="value"`` and closed with ``/>``;
* element/attribute order = first-occurrence order of the descriptor tables registered
  for that file (``nexcut.core.schema.Schema.layout``);
* ints as ``%d``, doubles as ``%.17g``, strings raw with XML escaping (``&quot;`` seen
  in ``MSC.LatestFilePath="L&quot;&quot;"``).

Serialising with these rules reproduces every one of the 65 vendor files byte for byte
(4 ``File/*Para*.xml``, ``1390backup.xml``, 55 technology presets, the Wine-written
``HardPara.xml`` and its ``File`` copies).

Primary/backup strategy (01 §0.3, §5; 00 §2 "primary + backup write flow"):

* primaries live in ``%LOCALAPPDATA%\\NexCut`` (``HardPara.xml``, ``ManuPara.xml``,
  ``LayerPara.xml``), backups in ``<install>\\File`` (``BkHardPara.xml``,
  ``BkManuPara.xml`` + ``SecondBkManuPara.xml``, ``BkLayerPara.xml``);
* load: primary -> backup (-> second backup for ManuPara). When a ManuPara candidate
  fails, it is copied to ``File\\ErrorManuPara.xml`` / ``File\\ErrorBkManuPara.xml``
  (EVIDENCE: ``CopyFileW`` import 0x7c098c called at 0x4b1849 and 0x4b19cb right after
  the pushes of those names at 0x4b17f8 / 0x4b197a);
* save: primary and backup are written in the same operation (Wine prefix evidence,
  00 §2 item 4); each write is read back and verified (strings
  ``MainFrm saveManuParam read failed`` / ``... backup read failed``, 0x45c1ed, 0x45c4d5);
  a hardware parameter set that is still all defaults is not saved
  (``MainFrame saveHardParam param is default, not save``, 0x45c58e).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from nexcut.core.schema import Descriptor, Layout, Schema, SchemaError, Value, load_schema

__all__ = [
    "FILE_SETS",
    "LoadResult",
    "ParamDocument",
    "ParamFileError",
    "ParamStore",
    "ReadReport",
    "TechnologyPreset",
    "default_document",
    "escape_attribute",
    "list_technology",
    "parse_params",
    "parse_technology",
    "read_params",
    "read_technology",
    "serialize_params",
    "serialize_technology",
    "technology_dir",
    "write_params",
    "write_technology",
]

Laser = Literal["fiber", "co2"]
ROOT_TAG = "ParameterRoot"  # literal in Module/ParaModule.dll (02 §2.1)
NEWLINE = "\r\n"
LAYER_SLOTS = 11  # 02 §4
TECHNOLOGY_SLOT = 11  # 02 §6.2: presets are always written as slot 11

_TECH_GROUP = {"fiber": "PLayerParam", "co2": "PCO2LayerParam"}
_TECH_LAYOUT = {"fiber": "technology_fiber", "co2": "technology_co2"}
_LAYER_SECTION = {"fiber": "LayerParam", "co2": "CO2LayerParam"}
_TECH_DIR = {"fiber": "Fiber", "co2": "CO2"}  # 02 §6.1: \Technology\Fiber, \Technology\CO2
_TECH_RX = re.compile(r"^P(LayerParam|CO2LayerParam)(\d+)$")


class ParamFileError(Exception):
    """A parameter file could not be read (missing, not XML, wrong root)."""


@dataclass
class ReadReport:
    """What the reader had to do beyond a clean read."""

    missing: list[str] = field(default_factory=list)
    """``Group/Element.Attr`` filled from the descriptor default (02 §6.2)."""
    unknown: list[str] = field(default_factory=list)
    """Groups/elements/attributes present in the file but not in the layout (dropped)."""
    coerced: list[str] = field(default_factory=list)
    """Attributes whose text was not a clean number and was read with C prefix rules."""

    @property
    def clean(self) -> bool:
        """True when nothing was defaulted, dropped or coerced."""
        return not (self.missing or self.unknown or self.coerced)


class ParamDocument:
    """Values of one parameter file, addressed as ``values[group][element][attribute]``.

    The document always holds every attribute of its layout; ``serialize_params``
    writes them in layout order.
    """

    def __init__(self, kind: str, schema: Schema | None = None) -> None:
        self.schema = schema or load_schema()
        self.kind = kind
        self.layout: Layout = self.schema.layout(kind)
        self.report = ReadReport()
        self.values: dict[str, dict[str, dict[str, Value]]] = {
            g.name: {e.name: {d.attribute: d.default for d in e.attributes} for e in g.elements}
            for g in self.layout.groups
        }

    def descriptor(self, group: str, element: str, attribute: str) -> Descriptor:
        """Descriptor bound to ``group/element.attribute``."""
        return self.layout.group(group).element(element).attribute(attribute)

    def get(self, group: str, element: str, attribute: str) -> Value:
        """Current value."""
        self.descriptor(group, element, attribute)
        return self.values[group][element][attribute]

    def set(
        self, group: str, element: str, attribute: str, value: Value, *, check: bool = True
    ) -> list[str]:
        """Set a value; coerces to the descriptor storage type.

        Returns the validation problems (min/max, enum) without rejecting the value;
        raises ``ValueError`` when ``check`` is set and the storage type is wrong.
        """
        d = self.descriptor(group, element, attribute)
        if d.storage == "string":
            if not isinstance(value, str):
                raise ValueError(f"{d.key} is a string parameter")
        elif isinstance(value, str) or isinstance(value, bool):
            if check:
                raise ValueError(f"{d.key} is numeric ({d.storage})")
        elif d.storage == "int":
            if check and isinstance(value, float) and not value.is_integer():
                raise ValueError(f"{d.key} is an integer parameter")
            value = int(value)
        else:
            value = float(value)
        self.values[group][element][attribute] = value
        return d.validate(value)

    def section_values(self, group: str) -> dict[str, Value]:
        """Flat ``{"Elem.Attr": value}`` view of one group."""
        return {f"{e}.{a}": v for e, attrs in self.values[group].items() for a, v in attrs.items()}

    def validate(self) -> list[str]:
        """All range/enum problems in the document (warnings; vendor data may violate)."""
        out: list[str] = []
        for g, e, d in self.layout.iter_attributes():
            out.extend(
                f"{g.name}/{p}" for p in d.validate(self.values[g.name][e.name][d.attribute])
            )
        return out

    def is_default(self) -> bool:
        """True when every value equals its descriptor default."""
        return all(
            _same(self.values[g.name][e.name][d.attribute], d.default)
            for g, e, d in self.layout.iter_attributes()
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ParamDocument):
            return NotImplemented
        return self.kind == other.kind and self.values == other.values

    # layer helpers ---------------------------------------------------------------
    def layer_slot(self, laser: Laser, slot: int) -> dict[str, Value]:
        """Attributes of fibre/CO2 layer ``slot`` (1..11; slot 1 = background layer, 02 §4)."""
        group = f"P{_LAYER_SECTION[laser]}{slot}"
        if group not in self.values:
            raise SchemaError(f"{self.kind!r} document has no group {group}")
        return dict(self.values[group]["GP"])


def _same(a: Value, b: Value) -> bool:
    if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
        return True
    return a == b


def default_document(kind: str, schema: Schema | None = None) -> ParamDocument:
    """A document holding only descriptor defaults (what the vendor re-creates, 01 §0.3)."""
    return ParamDocument(kind, schema)


# ------------------------------------------------------------------------------ read
_DECL = re.compile(rb"^\s*<\?xml[^>]*?\?>", re.S)
_DECL_TEXT = re.compile(r"^\s*<\?xml[^>]*?\?>", re.S)
_DECL_ENCODING = re.compile(rb"""encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""")
_ATTR_VALUE = re.compile(r"""=\s*("[^"<]*"|'[^'<]*')""")
_WS_REFS = str.maketrans({"\t": "&#9;", "\n": "&#10;", "\r": "&#13;"})


def _decode_xml(data: bytes) -> str:
    """Decode parameter-file bytes to text the way ParaModule's CMarkup reader would.

    EVIDENCE: ``Module/ParaModule.dll`` is CMarkup (entity hash table at file offset
    ``0x14e9c``..; result names ``utf8_detection``, ``ANSI``, ``converted_to``, ``encoding``,
    ``charset`` at ``0x19750``/``0x19728``/``0x19734``/``0x1953c``/``0x194c4``).  INFERENCE
    (medium, CMarkup ``ReadTextFile`` behaviour): a BOM wins, then a declared encoding, then
    UTF-8 if the bytes are valid UTF-8, else the ANSI code page.  UNVERIFIED: ANSI = cp936.
    Vendor files are BOM-less UTF-8 (01 §0.1), so they take the UTF-8 branch.
    """
    for bom, codec in (
        (b"\xef\xbb\xbf", "utf-8"),
        (b"\xff\xfe", "utf-16-le"),
        (b"\xfe\xff", "utf-16-be"),
    ):
        if data.startswith(bom):
            return data[len(bom) :].decode(codec, errors="replace")
    decl = _DECL.match(data)
    if decl:
        m = _DECL_ENCODING.search(decl.group())
        if m:
            try:
                return data.decode(m.group(1).decode("ascii"))
            except (LookupError, UnicodeDecodeError):
                pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")  # UNVERIFIED: CP_ACP = 936


def _parse_root(data: bytes) -> ET.Element:
    if not data.strip():
        raise ParamFileError("empty parameter file")
    text = _decode_xml(data)
    text = _DECL_TEXT.sub("", text, count=1)
    # CMarkup does not normalise attribute whitespace (INFERENCE, medium); expat would turn
    # raw TAB/CR/LF inside a value into spaces, so protect them as character references.
    text = _ATTR_VALUE.sub(lambda m: m.group(0).translate(_WS_REFS), text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ParamFileError(f"not well-formed XML: {exc}") from None
    if root.tag != ROOT_TAG:
        raise ParamFileError(f"root element is <{root.tag}>, expected <{ROOT_TAG}>")
    return root


def _fill(doc: ParamDocument, groups: Mapping[str, ET.Element], strict: bool) -> ParamDocument:
    report = doc.report
    for g in doc.layout.groups:
        gel = groups.get(g.name)
        children: dict[str, ET.Element] = {}
        if gel is not None:
            for child in gel:
                # UNVERIFIED: first element of a given name wins, as a DOM child lookup would.
                children.setdefault(child.tag, child)
            known = {e.name for e in g.elements}
            report.unknown.extend(f"{g.name}/{c.tag}" for c in gel if c.tag not in known)
        for e in g.elements:
            el = children.get(e.name)
            attrs = el.attrib if el is not None else {}
            known_attrs = {d.attribute for d in e.attributes}
            report.unknown.extend(f"{g.name}/{e.name}.{a}" for a in attrs if a not in known_attrs)
            for d in e.attributes:
                where = f"{g.name}/{e.name}.{d.attribute}"
                if d.attribute not in attrs:
                    report.missing.append(where)  # default already in place (02 §6.2)
                    continue
                value, exact = d.parse(attrs[d.attribute])
                if not exact:
                    if strict:
                        raise ParamFileError(f"{where}: bad {d.storage} {attrs[d.attribute]!r}")
                    report.coerced.append(where)
                doc.values[g.name][e.name][d.attribute] = value
    return doc


def parse_params(
    data: bytes, kind: str, schema: Schema | None = None, *, strict: bool = False
) -> ParamDocument:
    """Parse the bytes of a ``hard``/``manu``/``layer``/``system_backup`` file.

    Missing attributes keep the descriptor default and are listed in
    ``doc.report.missing``; unknown groups/elements/attributes are dropped and listed
    in ``doc.report.unknown`` (the vendor engine only reads registered descriptors).
    """
    doc = ParamDocument(kind, schema)
    root = _parse_root(data)
    groups: dict[str, ET.Element] = {}
    known = {g.name for g in doc.layout.groups}
    for gel in root:
        if gel.tag in known:
            groups.setdefault(gel.tag, gel)
        else:
            doc.report.unknown.append(gel.tag)
    return _fill(doc, groups, strict)


def read_params(
    path: str | os.PathLike[str], kind: str, schema: Schema | None = None
) -> ParamDocument:
    """Read a parameter file from disk (see ``parse_params``)."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ParamFileError(f"{path}: {exc.strerror or exc}") from None
    return parse_params(data, kind, schema)


# ----------------------------------------------------------------------------- write
_ATTR_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&apos;", '"': "&quot;"}


def escape_attribute(text: str) -> str:
    """XML-escape an attribute value exactly like ParaModule's CMarkup writer.

    EVIDENCE: ``x_EscapeText`` at ``0x10004cb0`` in ``Module/ParaModule.dll`` picks the
    find-set ``<&>'"`` (``.rdata`` ``0x1001a870``) when flag ``0x100`` (escape quotes) is set -
    the attribute setter passes ``or eax,0x100`` at ``0x1000663e`` - and replaces each hit via
    the pointer table ``0x100204f0`` -> ``&lt; &amp; &gt; &apos; &quot;``.  Nothing else is
    escaped: control characters (TAB/CR/LF) are written raw.  ``"`` -> ``&quot;`` is also seen
    in ``BkManuPara.xml``.  UNVERIFIED: whether any caller sets ``MNF_WITHREFS`` (``0x8``,
    ``&name;`` passed through, ``0x10004d97``); none of the vendor strings contains ``&``.
    """
    return "".join(_ATTR_ESCAPES.get(ch, ch) for ch in text)


def _render(groups: Iterable[tuple[str, Iterable[tuple[str, Iterable[tuple[str, str]]]]]]) -> bytes:
    lines = [f"<{ROOT_TAG}>"]
    for gname, elements in groups:
        lines.append(f"<{gname}>")
        for ename, attrs in elements:
            body = "".join(f' {a}="{escape_attribute(v)}"' for a, v in attrs)
            lines.append(f"<{ename}{body}/>")
        lines.append(f"</{gname}>")
    lines.append(f"</{ROOT_TAG}>")
    return (NEWLINE.join(lines) + NEWLINE).encode("utf-8")


def serialize_params(doc: ParamDocument) -> bytes:
    """Serialise a document in the exact vendor byte format (module docstring)."""
    return _render(
        (
            g.name,
            (
                (
                    e.name,
                    (
                        (d.attribute, d.format(doc.values[g.name][e.name][d.attribute]))
                        for d in e.attributes
                    ),
                )
                for e in g.elements
            ),
        )
        for g in doc.layout.groups
    )


def _atomic_write(path: Path, data: bytes) -> None:
    """Write via temp file + fsync + rename in the target directory (PORT-PLAN §3 ParaModule row)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def write_params(path: str | os.PathLike[str], doc: ParamDocument, *, verify: bool = True) -> None:
    """Atomically write ``doc`` to ``path``; with ``verify`` read it back and compare."""
    p = Path(path)
    data = serialize_params(doc)
    _atomic_write(p, data)
    if verify:
        back = p.read_bytes()
        if back != data:
            raise ParamFileError(f"{p}: read-back verification failed")


# ------------------------------------------------------------------------ technology
@dataclass
class TechnologyPreset:
    """One material-library preset: a single fibre or CO2 layer slot (02 §6)."""

    laser: Laser
    values: dict[str, Value]
    """Attribute name (without ``GP.``) -> value, in descriptor order."""
    source_group: str = ""
    """Wrapper element found in the file (``PLayerParam11`` in every vendor file)."""
    report: ReadReport = field(default_factory=ReadReport)

    @property
    def layer_file_name(self) -> str:
        """``LayerFileName`` -- a free-text recipe label that need not match the file name (02 §6.1)."""
        return str(self.values.get("LayerFileName", ""))


def _tech_element(laser: Laser, schema: Schema) -> tuple[Layout, str]:
    layout = schema.layout(_TECH_LAYOUT[laser])
    return layout, layout.groups[0].name


def parse_technology(data: bytes, schema: Schema | None = None) -> TechnologyPreset:
    """Parse a technology preset file.

    The laser family is taken from the wrapper element: ``PLayerParam<n>`` = fibre,
    ``PCO2LayerParam<n>`` = CO2. Vendor presets always use slot 11 (02 §6.2);
    UNVERIFIED whether MainApp accepts other slot numbers -- they are accepted here and
    recorded in ``source_group``. Missing attributes take the descriptor default (the 13
    vendor CO2 presets lack ``CutFreq`` -> 5000 Hz, 02 §6.2).
    """
    schema = schema or load_schema()
    root = _parse_root(data)
    wrappers = [g for g in root if _TECH_RX.match(g.tag)]
    if not wrappers:
        raise ParamFileError("no <PLayerParamN>/<PCO2LayerParamN> element in technology file")
    wrapper = wrappers[0]
    laser: Laser = "co2" if wrapper.tag.startswith("PCO2") else "fiber"
    layout, gname = _tech_element(laser, schema)
    doc = ParamDocument(layout.kind, schema)
    for other in root:
        if other is not wrapper:
            doc.report.unknown.append(other.tag)
    _fill(doc, {gname: wrapper}, strict=False)
    rename = f"{gname}/"
    report = ReadReport(
        missing=[m.replace(rename, f"{wrapper.tag}/") for m in doc.report.missing],
        unknown=[u.replace(rename, f"{wrapper.tag}/") for u in doc.report.unknown],
        coerced=[c.replace(rename, f"{wrapper.tag}/") for c in doc.report.coerced],
    )
    return TechnologyPreset(laser, dict(doc.values[gname]["GP"]), wrapper.tag, report)


def serialize_technology(preset: TechnologyPreset, schema: Schema | None = None) -> bytes:
    """Serialise a preset; the wrapper is always slot 11 (02 §6.2), all attributes written."""
    schema = schema or load_schema()
    layout, gname = _tech_element(preset.laser, schema)
    elem = layout.groups[0].element("GP")
    attrs = []
    for d in elem.attributes:
        value = preset.values.get(d.attribute, d.default)
        attrs.append((d.attribute, d.format(value)))
    return _render([(gname, [("GP", attrs)])])


def read_technology(path: str | os.PathLike[str], schema: Schema | None = None) -> TechnologyPreset:
    """Read a technology preset from disk."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise ParamFileError(f"{path}: {exc.strerror or exc}") from None
    return parse_technology(data, schema)


def write_technology(path: str | os.PathLike[str], preset: TechnologyPreset) -> None:
    """Atomically write a preset file."""
    _atomic_write(Path(path), serialize_technology(preset))


def preset_from_layer(doc: ParamDocument, laser: Laser, slot: int) -> TechnologyPreset:
    """Export layer ``slot`` of a ``layer`` document as a preset ("导出到工艺库", 02 §6.1)."""
    return TechnologyPreset(
        laser, doc.layer_slot(laser, slot), f"{_TECH_GROUP[laser]}{TECHNOLOGY_SLOT}"
    )


def apply_preset(
    doc: ParamDocument,
    preset: TechnologyPreset,
    slot: int,
    *,
    set_layer_file_name: str | None = None,
) -> None:
    """Import a preset into layer ``slot`` of ``doc`` (02 §6.1).

    ``LayerFileName`` is copied from the preset unchanged by default: the vendor files
    show the importer does not overwrite it with the file name (02 §6.1, VERIFIER, low).
    Pass ``set_layer_file_name`` to force a label.
    """
    group = f"P{_LAYER_SECTION[preset.laser]}{slot}"
    target = doc.values.get(group)
    if target is None:
        raise SchemaError(f"{doc.kind!r} document has no group {group}")
    for d in doc.layout.group(group).element("GP").attributes:
        target["GP"][d.attribute] = preset.values.get(d.attribute, d.default)
    if set_layer_file_name is not None:
        target["GP"]["LayerFileName"] = set_layer_file_name


def technology_dir(base: str | os.PathLike[str], laser: Laser) -> Path:
    """``<base>/Technology/Fiber`` or ``<base>/Technology/CO2`` (02 §6.1; base = ``%LOCALAPPDATA%\\NexCut``)."""
    return Path(base) / "Technology" / _TECH_DIR[laser]


def list_technology(base: str | os.PathLike[str], laser: Laser) -> list[Path]:
    """Preset files (``*.xml``, 02 §6.1) of one laser family, sorted by name.

    The UI lists presets by file stem; ``LayerFileName`` inside may differ (02 §6.1).
    """
    d = technology_dir(base, laser)
    if not d.is_dir():
        return []
    return sorted(
        (p for p in d.iterdir() if p.suffix.lower() == ".xml" and p.is_file()), key=lambda p: p.name
    )


# ------------------------------------------------------------------ primary + backups
@dataclass(frozen=True)
class FileSet:
    """File names of one parameter kind (01 §0.3 load sequence)."""

    kind: str
    primary: str
    backups: tuple[str, ...]
    error_copies: tuple[str, ...] = ()
    """Name a failed candidate is copied to, per candidate (primary, backup, ...)."""


FILE_SETS: dict[str, FileSet] = {
    "hard": FileSet("hard", "HardPara.xml", ("BkHardPara.xml",)),
    "manu": FileSet(
        "manu",
        "ManuPara.xml",
        ("BkManuPara.xml", "SecondBkManuPara.xml"),
        ("ErrorManuPara.xml", "ErrorBkManuPara.xml"),
    ),
    "layer": FileSet("layer", "LayerPara.xml", ("BkLayerPara.xml",)),
}


@dataclass
class LoadResult:
    """Outcome of ``ParamStore.load``."""

    document: ParamDocument
    source: Path | None
    """File actually used; None when every candidate failed and defaults were returned."""
    failures: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def from_backup(self) -> bool:
        """True when the primary was not used."""
        return bool(self.failures) or self.source is None


class ParamStore:
    """Primary (``%LOCALAPPDATA%\\NexCut``) + backup (``<install>\\File``) parameter store.

    Linux mapping (00 §8): ``primary_dir`` = ``$XDG_DATA_HOME/nexcut``; ``backup_dir`` may
    point at a Windows install's ``File`` directory so both tools share state.
    """

    def __init__(
        self,
        primary_dir: str | os.PathLike[str],
        backup_dir: str | os.PathLike[str],
        schema: Schema | None = None,
    ) -> None:
        self.primary_dir = Path(primary_dir)
        self.backup_dir = Path(backup_dir)
        self.schema = schema or load_schema()

    def paths(self, kind: str) -> list[Path]:
        """Candidate files in load order."""
        fs = FILE_SETS[kind]
        return [self.primary_dir / fs.primary, *(self.backup_dir / b for b in fs.backups)]

    def load(self, kind: str, *, copy_errors: bool = True) -> LoadResult:
        """Load ``kind`` from the first readable candidate.

        A candidate that exists but fails to parse is copied to its ``Error*.xml`` name in
        the backup directory (ManuPara only, EVIDENCE 0x4b1849/0x4b19cb). When nothing is
        readable, a default document is returned (``source`` None); whether the vendor then
        writes it immediately ("... was not found and was re-created.") is UNVERIFIED, so
        nothing is written here.
        """
        fs = FILE_SETS[kind]
        failures: list[tuple[Path, str]] = []
        for i, path in enumerate(self.paths(kind)):
            try:
                doc = read_params(path, kind, self.schema)
            except ParamFileError as exc:
                failures.append((path, str(exc)))
                if copy_errors and path.exists() and i < len(fs.error_copies):
                    self.backup_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, self.backup_dir / fs.error_copies[i])
                continue
            return LoadResult(doc, path, failures)
        return LoadResult(default_document(kind, self.schema), None, failures)

    def save(self, doc: ParamDocument, *, rotate_second_backup: bool = True) -> bool:
        """Write the primary, then the backup, each verified by read-back.

        * ``hard``: refuses (returns False) when every value is still the default
          (EVIDENCE string at 0x45c58e; UNVERIFIED that "default" means exactly this).
        * ``manu``: with ``rotate_second_backup`` the previous ``BkManuPara.xml`` becomes
          ``SecondBkManuPara.xml`` first. UNVERIFIED: inferred from the second backup
          lagging the first by one save generation (01 §5).
        """
        if doc.kind not in FILE_SETS:
            raise SchemaError(f"no file set for kind {doc.kind!r}")
        if doc.kind == "hard" and doc.is_default():
            return False
        fs = FILE_SETS[doc.kind]
        write_params(self.primary_dir / fs.primary, doc)
        backup = self.backup_dir / fs.backups[0]
        if doc.kind == "manu" and rotate_second_backup and backup.exists():
            _atomic_write(self.backup_dir / fs.backups[1], backup.read_bytes())
        write_params(backup, doc)
        return True
