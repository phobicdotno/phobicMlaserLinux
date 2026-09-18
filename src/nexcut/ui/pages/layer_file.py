"""The layer-file bar: read and write ``BkLayerPara.xml`` itself (01 §0.3, 02 §2.1).

The layer dock used to be able to exchange a *slot* with the technology library
(02 §6.1) but never to write the layer file back, so an edit made in the dock
lived only in memory.  This bar closes that: it owns the path of the ``layer``
document the pages edit and offers Reload / Save / Save as over
:func:`nexcut.io.params.write_params`.

**The write is backup-then-rename.**  ``write_params(..., backup_suffix=...)``
copies the file that is already there to ``<name>.bak`` *before* the new content
is renamed over it, and verifies the result by reading it back - the single-file
form of the vendor's own flow, which writes the primary and the backup in one
operation and read-back-verifies each (01 §0.3; the failure strings
``MainFrm saveManuParam read failed`` / ``... backup read failed`` at 0x45c1ed /
0x45c4d5).  The vendor's own second generation is a differently *named* file
(``SecondBkManuPara.xml``) and exists for ``ManuPara`` only, so ``.bak`` is a port
choice, marked UNVERIFIED on :data:`nexcut.io.params.BACKUP_SUFFIX`.

The bar shows what the reader had to do with the file it loaded
(:class:`~nexcut.io.params.ReadReport`): attributes filled from a descriptor
default, groups or attributes dropped because the layout does not know them, and
values whose text was not a clean number.  A file that reports nothing is one the
port re-serialises byte for byte.

SAFETY (PORT-PLAN §8): file I/O only.  No connection, no motion, no laser.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from nexcut.io.params import (
    BACKUP_SUFFIX,
    ParamDocument,
    ParamFileError,
    read_params,
    write_params,
)
from nexcut.ui.i18n import Translator, get_translator

__all__ = ["LayerFileBar"]


class LayerFileBar(QWidget):
    """Path, Reload, Save and Save-as for one ``layer`` parameter document."""

    documentReloaded = Signal(object)
    """The freshly read :class:`~nexcut.io.params.ParamDocument`."""

    documentSaved = Signal(str)
    """Path the document was written to."""

    def __init__(
        self,
        document: ParamDocument,
        *,
        translator: Translator | None = None,
        path: str | Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.t = translator or get_translator()
        self.document = document
        self.path: Path | None = Path(path) if path is not None else None
        self.last_backup: Path | None = None
        self._ask_path: Callable[[bool], str | None] | None = None

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.path_label = QLabel("")
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.path_label, 1)
        self.reload_button = QPushButton(self.t.tr("mf13", "Reload"))
        self.reload_button.setToolTip("Re-read the layer file, discarding unsaved edits")
        self.reload_button.clicked.connect(lambda: self._guarded(self.reload))
        self.save_button = QPushButton(self.t.tr("mf10", "Save"))
        self.save_button.setToolTip(
            f"Write the layer file; the previous one is kept as <name>{BACKUP_SUFFIX} (01 §0.3)"
        )
        self.save_button.clicked.connect(lambda: self._guarded(self.save))
        self.save_as_button = QPushButton(self.t.tr("mf11", "Save as"))
        self.save_as_button.clicked.connect(lambda: self._guarded(self.save_as))
        for button in (self.reload_button, self.save_button, self.save_as_button):
            row.addWidget(button)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        row.addWidget(self.status_label, 1)
        self._refresh()

    # ------------------------------------------------------------------ state
    def set_document(self, document: ParamDocument, path: str | Path | None = None) -> None:
        """Bind another ``layer`` document (and optionally its path)."""
        if document.kind != "layer":
            raise ValueError(f"the layer file bar needs a 'layer' document, got {document.kind!r}")
        self.document = document
        if path is not None:
            self.path = Path(path)
        self._refresh()

    def set_path(self, path: str | Path | None) -> None:
        """Set the file Reload and Save use."""
        self.path = Path(path) if path is not None else None
        self._refresh()

    def set_path_chooser(self, chooser: Callable[[bool], str | None] | None) -> None:
        """Install the callable used instead of a ``QFileDialog`` (tests replace it)."""
        self._ask_path = chooser

    # ------------------------------------------------------------------- I/O
    def reload(self) -> ParamDocument | None:
        """Re-read the file; the new document is emitted, not merged into the old one."""
        if self.path is None:
            return None
        document = read_params(self.path, "layer")
        self.document = document
        self._refresh()
        self.documentReloaded.emit(document)
        return document

    def save(self, path: str | Path | None = None) -> Path | None:
        """Write the document (module docstring); returns the path written."""
        target = Path(path) if path is not None else self.path
        if target is None:
            return self.save_as()
        self.last_backup = write_params(target, self.document, backup_suffix=BACKUP_SUFFIX)
        self.path = target
        self._refresh()
        self.documentSaved.emit(str(target))
        return target

    def save_as(self) -> Path | None:
        """Ask for a path and write there."""
        chosen = self._ask_path(True) if self._ask_path is not None else self._file_dialog(True)
        return self.save(chosen) if chosen else None

    # --------------------------------------------------------------- display
    def report_text(self) -> str:
        """One line describing the reader's :class:`~nexcut.io.params.ReadReport`."""
        report = self.document.report
        if report.clean:
            return "read clean"
        parts = []
        if report.missing:
            parts.append(f"{len(report.missing)} defaulted")
        if report.unknown:
            parts.append(f"{len(report.unknown)} unknown dropped")
        if report.coerced:
            parts.append(f"{len(report.coerced)} coerced")
        return ", ".join(parts)

    def _refresh(self) -> None:
        self.path_label.setText(str(self.path) if self.path is not None else "(no layer file)")
        text = self.report_text()
        if self.last_backup is not None:
            text = f"{text}; previous kept as {self.last_backup.name}"
        self.status_label.setText(text)
        self.reload_button.setEnabled(self.path is not None)

    def _guarded(self, action: Callable[[], object]) -> None:
        try:
            action()
        except (ParamFileError, OSError, ValueError) as exc:
            self.status_label.setText(str(exc))

    def _file_dialog(self, save: bool) -> str | None:  # pragma: no cover - needs a desktop
        from PySide6.QtWidgets import QFileDialog

        title = self.t.tr("mf152" if save else "mf150", "Save as" if save else "Open File")
        picker = QFileDialog.getSaveFileName if save else QFileDialog.getOpenFileName
        path, _ = picker(self, title, "", "Layer parameters (*.xml)")
        return path or None
