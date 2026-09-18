"""Hardware, machining, software and graph-rule parameter pages (06 §8; PORT-PLAN §4 M3).

The vendor has no resource templates for these: 06 §8 found 7 ``RT_DIALOG``
resources in ``MainApp.exe`` against ~70 windows, and concluded that the
parameter pages are built programmatically from BCG property lists - "pure
``Group.Item`` label lists" - hosted in ``CHardwarePropView`` / ``CPropPanel``
with 25 tab labels (``hp2-7``, ``hp16-18``, ``hp25``, ``hp29-35``, ``hp55/56``,
``hp70-73``, ``hp80/81``).  This module reproduces that shape from the port's own
descriptor schema instead of guessing the tab layout: a page owns a set of XML
*group elements* of one parameter file, a section selector walks the
``Group/Element`` pairs inside them, and each section is one
:class:`~nexcut.ui.property_grid.PropertyGrid` grouped by the ``Group`` half of
the ``lang.txt`` label.

The four pages partition the two files exactly once each (``test_ui_param_pages``
pins it):

======================  =======  =================================================
page                    file     XML groups
======================  =======  =================================================
:data:`HARDWARE`        ``hard`` all 17 groups of ``BkHardPara.xml`` (01 §1)
:data:`MACHINING`       ``manu`` ``PManuParam``, ``PAFParam``, ``PDOParam``,
                                 ``PZFParam``, ``PECParam`` (01 §1.2, 02 §3.9)
:data:`SOFTWARE`        ``manu`` ``PSoftParam`` (06 §4.21)
:data:`GRAPH_RULES`     ``manu`` ``PGraphParam``, ``PNestParam``,
                                 ``PImportGraphParam`` (02 §3.5/§3.9, 06 §4.6)
======================  =======  =================================================

Section labels are ``<section> / <element>`` (``AxisParam / A0``) - a port choice:
``lang.txt`` names the vendor's *tabs* (``hp*``), and which tab holds which
element is not traced, so the page names the element the file itself uses rather
than inventing a tab mapping.  The section names *inside* a grid are the vendor's
own, because they come from the ``Group`` half of each descriptor's label.

Writing: :meth:`ParamPage.save` goes through :func:`nexcut.io.params.write_params`
with ``backup_suffix``, so the previous generation of the file is copied aside
before the new one is renamed into place (01 §0.3, and see that function).  A page
never writes anywhere but the path it was given.

SAFETY (PORT-PLAN §8): these pages read and write XML files.  None of them opens a
socket, moves an axis or arms a laser.  In particular the hardware page does *not*
push anything to the card: 11 §3.3 denies 59600+ hardware-parameter writes, and
this build has no read-compare path either (STATUS §1.4).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from nexcut.core.schema import Descriptor, Value
from nexcut.io.params import (
    BACKUP_SUFFIX,
    ParamDocument,
    ParamFileError,
    default_document,
    read_params,
    write_params,
)
from nexcut.ui.i18n import Translator, get_translator
from nexcut.ui.property_grid import PropertyGrid, UnitPolicy

__all__ = [
    "GRAPH_RULES",
    "HARDWARE",
    "MACHINING",
    "PAGE_SPECS",
    "SOFTWARE",
    "UNVERIFIED",
    "GraphRulePage",
    "HardwarePage",
    "MachiningPage",
    "PageSpec",
    "ParamPage",
    "SoftwarePage",
    "section_key",
]

UNVERIFIED: tuple[str, ...] = (
    "which vendor tab (hp2-7, hp16-18, hp25, hp29-35, hp55/56, hp70-73, hp80/81; 06 §8) "
    "holds which XML element is not traced; the pages are cut along the file's own group "
    "elements instead, and the section label is '<section> / <element>'",
    "the split of the manu file into machining / software / graph-rule pages is a port "
    "choice: it follows the XML groups (01 §1.2), not an observed vendor page boundary",
    "the hardware page does not read back 50000 / 50200 / 59600+ from the card; 11 §3.3 "
    "allows only a read-compare and the daemon reads 50000/26 alone (STATUS §1.4)",
)


def section_key(group: str, element: str, attribute: str) -> str:
    """Row identity inside a page: ``"PAxisParam/A0.Enable"`` (unique across a file)."""
    return f"{group}/{element}.{attribute}"


@dataclass(frozen=True, slots=True)
class PageSpec:
    """Which groups of which parameter file one page owns (module docstring)."""

    page_id: str
    kind: str
    """Layout kind of the document, i.e. the ``FILE_SETS`` key (``hard`` / ``manu``)."""
    groups: tuple[str, ...]
    title_id: str
    """``lang.txt`` id of the page title."""
    title_default: str


HARDWARE = PageSpec(
    "hardware",
    "hard",
    (
        "PAxisParam",
        "PHomeParam",
        "PZFParam",
        "PLaserParam",
        "PManuParam",
        "PGasParam",
        "PDOParam",
        "PDIParam",
        "PDAParam",
        "PFCParam",
        "PSoftParam",
        "PMachineAxisConfig",
        "PMachineAxisConfig_0",
        "PMachineAxisConfig_1",
        "PMachineAxisConfig_2",
        "PMachineAxisConfig_3",
        "PMachineAxisConfig_4",
    ),
    "hp2",
    "Hardware Parameters",
)
"""``BkHardPara.xml``: axes, homing, laser, gas, DI/DO/DA, follower, axis configs (01 §1)."""

MACHINING = PageSpec(
    "machining",
    "manu",
    ("PManuParam", "PAFParam", "PDOParam", "PZFParam", "PECParam"),
    "mf27",
    "Machining Parameters",
)
"""``BkManuPara.xml`` run-time control: ``MC``/``LC``/``GC``/``FC``/``MS``/``UN`` and the
auto-focus, gas-map, Z-follower and extended-axis blocks (01 §1.2, 02 §3.9)."""

SOFTWARE = PageSpec(
    "software",
    "manu",
    ("PSoftParam",),
    "hp35",
    "Software Parameters",
)
"""``BkManuPara.xml`` ``PSoftParam``: device counters, system and software options (06 §4.21).

The ``hard`` file carries a *second*, smaller ``PSoftParam`` (``SOP`` 15 + ``SP`` 5); that
one belongs to the hardware file and is shown on the hardware page."""

GRAPH_RULES = PageSpec(
    "graph_rules",
    "manu",
    ("PGraphParam", "PNestParam", "PImportGraphParam"),
    "pd593",
    "Graph Rules",
)
"""``GRP`` (166 attributes: lead lines, cool points, micro joints, sorting, roll sheet;
02 §3.5), the nesting defaults ``NP`` (06 §4.6) and the import gates ``IGP`` (03/`ops`)."""

PAGE_SPECS: dict[str, PageSpec] = {
    p.page_id: p for p in (HARDWARE, MACHINING, SOFTWARE, GRAPH_RULES)
}


def out_of_range_message(document: ParamDocument, show: int = 3) -> str:
    """``"N values outside their range: …"`` for :meth:`ParamDocument.validate`, or ``""``.

    A warning shown *after* a save, never a reason to refuse one: the vendor's own files
    violate the descriptor ranges (U3), and D15 writes what the operator did not touch
    byte for byte. The first ``show`` problems are named.
    """
    problems = document.validate()
    if not problems:
        return ""
    n = len(problems)
    head = f"{n} value outside its range" if n == 1 else f"{n} values outside their range"
    more = f" (+{n - show} more)" if n > show else ""
    return f"{head} (saved anyway): " + "; ".join(problems[:show]) + more


class ParamPage(QWidget):
    """One parameter page: a section selector over a :class:`PropertyGrid` (module docstring)."""

    valueChanged = Signal(str, object)
    """``("Group/Element.Attribute", stored value)`` after an edit."""

    documentSaved = Signal(str)
    """Path the document was written to."""

    sectionChanged = Signal(str)
    """The new ``Group/Element`` section name."""

    spec: PageSpec = MACHINING

    def __init__(
        self,
        spec: PageSpec | None = None,
        *,
        translator: Translator | None = None,
        document: ParamDocument | None = None,
        manu: object | None = None,
        path: str | Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.spec = spec or type(self).spec
        self.t = translator or get_translator()
        self.units = UnitPolicy.from_manu(manu)
        self.document = self._check(document)
        self.path: Path | None = Path(path) if path is not None else None
        self._ask_path: Callable[[bool], str | None] | None = None
        self.last_save_warning = ""
        """:func:`out_of_range_message` of the last save ("" = nothing out of range)."""

        outer = QVBoxLayout(self)
        outer.addLayout(self._build_header())
        self.grid = PropertyGrid(translator=self.t, units=self.units)
        self.grid.valueChanged.connect(self.valueChanged)
        self.grid.validationChanged.connect(self._show_problems)
        outer.addWidget(self.grid, 1)
        self.problem_label = QLabel("")
        self.problem_label.setWordWrap(True)
        self.problem_label.setStyleSheet("color: #a00;")
        outer.addWidget(self.problem_label)

        self.grid.set_source(self._get, self._set)
        self.grid.set_cross_check(self.cross_check)
        self._fill_sections()

    # ------------------------------------------------------------------ build
    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(QLabel(self.t.tr(self.spec.title_id, self.spec.title_default)))
        self.section_box = QComboBox()
        self.section_box.currentIndexChanged.connect(self._on_section_index)
        row.addWidget(self.section_box, 1)
        self.path_label = QLabel("")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.path_label, 1)
        self.reload_button = QPushButton(self.t.tr("mf13", "Reload"))
        self.reload_button.setToolTip("Re-read the file, discarding the edits on this page")
        self.reload_button.clicked.connect(lambda: self._guarded(self.reload))
        self.save_button = QPushButton(self.t.tr("mf10", "Save"))
        self.save_button.setToolTip(
            "Write the file, keeping the previous generation as <name>"
            f"{BACKUP_SUFFIX} (01 §0.3)"
        )
        self.save_button.clicked.connect(lambda: self._guarded(self.save))
        row.addWidget(self.reload_button)
        row.addWidget(self.save_button)
        return row

    def _check(self, document: ParamDocument | None) -> ParamDocument:
        """Descriptor defaults for ``None``; a document of the wrong kind is refused."""
        if document is None:
            return default_document(self.spec.kind)
        if not isinstance(document, ParamDocument) or document.kind != self.spec.kind:
            kind = getattr(document, "kind", type(document).__name__)
            raise ValueError(f"{self.spec.page_id} page needs a {self.spec.kind!r} document, got {kind!r}")
        return document

    # ---------------------------------------------------------------- sections
    def sections(self) -> list[tuple[str, str]]:
        """``(group, element)`` pairs this page owns, in file order."""
        out: list[tuple[str, str]] = []
        for name in self.spec.groups:
            group = self.document.layout.group(name)
            out.extend((group.name, e.name) for e in group.elements)
        return out

    def section_label(self, group: str, element: str) -> str:
        """``"AxisParam / A0"`` - the file's own names (module docstring, port choice)."""
        return f"{group.removeprefix('P')} / {element}"

    @property
    def section(self) -> tuple[str, str]:
        """The ``(group, element)`` currently shown."""
        data = self.section_box.currentData()
        return data if isinstance(data, tuple) else self.sections()[0]

    def set_section(self, group: str, element: str) -> None:
        """Show one ``(group, element)`` section."""
        for i in range(self.section_box.count()):
            if self.section_box.itemData(i) == (group, element):
                self.section_box.setCurrentIndex(i)
                return
        raise KeyError(f"{self.spec.page_id} page has no section {group}/{element}")

    def _fill_sections(self) -> None:
        self.section_box.blockSignals(True)
        self.section_box.clear()
        for group, element in self.sections():
            self.section_box.addItem(self.section_label(group, element), (group, element))
        self.section_box.setCurrentIndex(0)
        self.section_box.blockSignals(False)
        self._rebuild()

    def _on_section_index(self, index: int) -> None:
        if index < 0:
            return
        self._rebuild()
        group, element = self.section
        self.sectionChanged.emit(f"{group}/{element}")

    def descriptors(self) -> Sequence[Descriptor]:
        """Descriptors of the current section, in file order."""
        group, element = self.section
        return self.document.layout.group(group).element(element).attributes

    def read_only_keys(self) -> tuple[str, ...]:
        """Rows a page refuses to edit (none by default)."""
        return ()

    def cross_check(self) -> list[str]:
        """Page-level rules run after every edit (none by default)."""
        return []

    def _rebuild(self) -> None:
        group, element = self.section
        self.grid.build(
            self.descriptors(),
            keys=lambda d: section_key(group, element, d.attribute),
            read_only_keys=self.read_only_keys(),
        )
        self.path_label.setText(str(self.path) if self.path is not None else "")

    # ------------------------------------------------------------------ values
    def _split(self, key: str) -> tuple[str, str, str]:
        group, _, rest = key.partition("/")
        element, _, attribute = rest.partition(".")
        return group, element, attribute

    def _get(self, key: str) -> Value:
        return self.document.get(*self._split(key))

    def _set(self, key: str, value: Value) -> None:
        group, element, attribute = self._split(key)
        self.document.set(group, element, attribute, value, check=False)

    def keys(self) -> list[str]:
        """Every row key this page can address, across all its sections."""
        out: list[str] = []
        for group, element in self.sections():
            layout = self.document.layout.group(group).element(element)
            out.extend(section_key(group, element, d.attribute) for d in layout.attributes)
        return out

    def _show_problems(self, problems: list[str]) -> None:
        self.problem_label.setText("\n".join(problems))

    # ------------------------------------------------------------------- state
    def set_document(self, document: ParamDocument, manu: object | None = None) -> None:
        """Bind another document of this page's kind (and optionally re-read the units)."""
        self.document = self._check(document)
        if manu is not None:
            self.units = UnitPolicy.from_manu(manu)
            self.grid.set_units(self.units)
        self._fill_sections()

    def set_path(self, path: str | Path | None) -> None:
        """Set the file :meth:`save` and :meth:`reload` use."""
        self.path = Path(path) if path is not None else None
        self.path_label.setText(str(self.path) if self.path is not None else "")

    def set_path_chooser(self, chooser: Callable[[bool], str | None] | None) -> None:
        """Install the callable used when no path is set (tests replace the file dialog)."""
        self._ask_path = chooser

    def reload(self) -> bool:
        """Re-read the file into the page, discarding unsaved edits."""
        if self.path is None:
            return False
        self.set_document(read_params(self.path, self.spec.kind))
        return True

    def save(self, path: str | Path | None = None) -> Path | None:
        """Write the document (see the module docstring for the backup rule)."""
        target = Path(path) if path is not None else self.path
        if target is None:
            chosen = self._ask_path(True) if self._ask_path is not None else self._file_dialog(True)
            if not chosen:
                return None
            target = Path(chosen)
        write_params(target, self.document, backup_suffix=BACKUP_SUFFIX)
        self.set_path(target)
        # Never blocks (U3): the file is written; the count is shown afterwards.
        self.last_save_warning = out_of_range_message(self.document)
        if self.last_save_warning:
            self.problem_label.setText(self.last_save_warning)
        self.documentSaved.emit(str(target))
        return target

    def _guarded(self, action: Callable[[], object]) -> None:
        try:
            action()
        except (ParamFileError, OSError, ValueError) as exc:
            self.problem_label.setText(str(exc))

    def _file_dialog(self, save: bool) -> str | None:  # pragma: no cover - needs a desktop
        from PySide6.QtWidgets import QFileDialog

        title = self.t.tr("mf152" if save else "mf150", "Save as" if save else "Open File")
        picker = QFileDialog.getSaveFileName if save else QFileDialog.getOpenFileName
        path, _ = picker(self, title, "", "Parameter XML (*.xml)")
        return path or None


class HardwarePage(ParamPage):
    """``BkHardPara.xml`` (01 §1); read-only nothing, pushed to the card never (11 §3.3)."""

    spec = HARDWARE


class MachiningPage(ParamPage):
    """``BkManuPara.xml`` run-time control blocks (01 §1.2, 02 §3.9)."""

    spec = MACHINING


class SoftwarePage(ParamPage):
    """``BkManuPara.xml`` ``PSoftParam`` (06 §4.21).

    ``SOP.Device*`` are the machine's own counters (run time, travel, laser-on
    time; 01 §5 uses them to order the two ManuPara backups).  They are shown
    read-only: editing a counter would rewrite the machine's history, and 01 §5
    reads them as written by the controller, not by the operator.
    """

    spec = SOFTWARE

    def read_only_keys(self) -> tuple[str, ...]:
        group, element = self.section
        return tuple(
            section_key(group, element, d.attribute)
            for d in self.descriptors()
            if d.attribute.startswith("Device")
        )


class GraphRulePage(ParamPage):
    """``GRP`` / ``NP`` / ``IGP``: the geometry rules a job is built with (02 §3.5)."""

    spec = GRAPH_RULES
