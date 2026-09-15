"""Open any supported job file into a :class:`ChfDocument` (06 §4.3, 05 §4).

Suffixes follow the vendor "Open File" filter ``mf149``:
``*.dxf; *.chf; *.nc; *.txt; *.cnc; *.g; *.plt`` (06 §4.3).  ``.chf`` is read
losslessly (:func:`nexcut.io.chf.load_chf`, no degenerate filter, so a re-save
is byte-identical); DXF/PLT/G-code go through their importer followed by the
``IGP`` clean-up gates and ``IGP.AutoSortType`` (05 §4 pipeline,
:func:`nexcut.ops.import_gates.apply_import_gates`).

Qt-free.  File conversion only - nothing here talks to the controller.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from nexcut.io.chf import ChfError, load_chf, save_chf
from nexcut.model.graph import ChfDocument
from nexcut.ops.import_gates import GateParams, GateReport, apply_import_gates

__all__ = [
    "GCODE_SUFFIXES",
    "OPEN_SUFFIXES",
    "LoadError",
    "LoadedDocument",
    "file_kind",
    "load_document",
    "save_document",
]

GCODE_SUFFIXES = (".nc", ".txt", ".cnc", ".g")
"""G-code variants of the ``mf149`` filter (06 §4.3)."""

OPEN_SUFFIXES: dict[str, str] = {
    ".chf": "chf",
    ".dxf": "dxf",
    ".plt": "plt",
    **dict.fromkeys(GCODE_SUFFIXES, "gcode"),
}


class LoadError(ValueError):
    """A file could not be opened (unsupported suffix, parse error, I/O error)."""


@dataclass(slots=True)
class LoadedDocument:
    """A document plus where it came from and what the import reported."""

    document: ChfDocument
    path: Path
    kind: str
    warnings: list[str] = field(default_factory=list)
    gate_report: GateReport | None = None


def file_kind(path: str | os.PathLike[str]) -> str:
    """``chf``/``dxf``/``plt``/``gcode`` from the suffix (case-insensitive)."""
    kind = OPEN_SUFFIXES.get(Path(path).suffix.lower())
    if kind is None:
        raise LoadError(f"unsupported file type: {Path(path).name}")
    return kind


def load_document(
    path: str | os.PathLike[str],
    *,
    gate_params: GateParams | None = None,
    apply_gates: bool = True,
) -> LoadedDocument:
    """Open ``path`` (module docstring).  Raises :class:`LoadError` on any failure."""
    p = Path(path)
    kind = file_kind(p)
    if not p.is_file():
        raise LoadError(f"no such file: {p}")
    warnings: list[str] = []
    try:
        if kind == "chf":
            return LoadedDocument(load_chf(p), p, kind)
        if kind == "dxf":
            from nexcut.io.dxf import read_dxf

            res_d = read_dxf(p)
            doc, warnings = res_d.document, list(res_d.warnings)
        elif kind == "plt":
            from nexcut.io.plt import read_plt

            res_p = read_plt(p)
            doc, warnings = res_p.document, list(res_p.warnings)
        else:
            from nexcut.io.gcode import read_gcode

            res_g = read_gcode(p)
            doc, warnings = res_g.document, list(res_g.warnings)
    except LoadError:
        raise
    except (ChfError, ValueError, OSError, UnicodeError) as exc:
        raise LoadError(f"{p.name}: {exc}") from exc
    report = apply_import_gates(doc, gate_params) if apply_gates else None
    return LoadedDocument(doc, p, kind, warnings, report)


def save_document(doc: ChfDocument, path: str | os.PathLike[str]) -> Path:
    """Save as ``.chf`` (03 §4.1 writer); a missing suffix gets ``.chf`` appended."""
    p = Path(path)
    if p.suffix.lower() != ".chf":
        p = p.with_name(p.name + ".chf")
    save_chf(doc, p)
    return p
