"""STATUS §5 task 10: Save warns - never blocks - when values are outside their range.

``ParamDocument.validate()`` lists every range/enum problem. Vendor files already violate it
(U3), so a save that refused them would refuse the machine's own files; the layer-file bar and
the parameter pages write the file and then say how many values are outside their range.

Offscreen Qt; nothing here touches a machine.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from nexcut.io.params import (  # noqa: E402
    ParamDocument,
    default_document,
    read_params,
    serialize_params,
)
from nexcut.ui.pages.layer_file import LayerFileBar  # noqa: E402
from nexcut.ui.pages.param_pages import (  # noqa: E402
    HardwarePage,
    MachiningPage,
    out_of_range_message,
)


@pytest.fixture(scope="module", autouse=True)
def qapp() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _break(doc: ParamDocument, n: int) -> list[str]:
    """Put ``n`` ranged numeric attributes above their maximum; return their keys."""
    keys = []
    for g, e, d in doc.layout.iter_attributes():
        if len(keys) == n:
            break
        if d.storage != "string" and d.has_range and not d.enum_ids:
            doc.values[g.name][e.name][d.attribute] = d.max + 1
            keys.append(f"{g.name}/{e.name}.{d.attribute}")
    assert len(keys) == n
    return keys


def test_the_message_counts_the_problems() -> None:
    doc = default_document("layer")
    assert doc.validate() == [] and out_of_range_message(doc) == ""
    _break(doc, 3)
    msg = out_of_range_message(doc)
    assert msg.startswith("3 values outside their range")
    one = default_document("layer")
    (key,) = _break(one, 1)
    msg = out_of_range_message(one)
    assert msg.startswith("1 value outside its range") and key in msg


def test_layer_bar_saves_and_warns(tmp_path: Path) -> None:
    doc = default_document("layer")
    _break(doc, 2)
    target = tmp_path / "BkLayerPara.xml"
    bar = LayerFileBar(doc, path=target)
    try:
        assert bar.save() == target  # never blocked
        assert target.read_bytes() == serialize_params(doc)
        assert bar.last_save_warning.startswith("2 values outside their range")
        assert "2 values outside their range" in bar.status_label.text()
        # a clean document clears the warning on the next save
        bar.set_document(default_document("layer"))
        assert bar.save() == target
        assert bar.last_save_warning == ""
        assert "outside their range" not in bar.status_label.text()
    finally:
        bar.deleteLater()


def test_param_page_saves_and_warns(tmp_path: Path) -> None:
    doc = default_document("hard")
    _break(doc, 4)
    target = tmp_path / "BkHardPara.xml"
    page = HardwarePage(document=doc, path=target)
    try:
        assert page.save() == target
        assert read_params(target, "hard") == doc
        assert page.last_save_warning.startswith("4 values outside their range")
        assert "4 values outside their range" in page.problem_label.text()
    finally:
        page.deleteLater()


def test_param_page_clean_save_says_nothing(tmp_path: Path) -> None:
    page = HardwarePage(document=default_document("hard"), path=tmp_path / "h.xml")
    try:
        assert page.save() is not None
        assert page.last_save_warning == ""
        assert "outside their range" not in page.problem_label.text()
    finally:
        page.deleteLater()


def test_the_descriptor_defaults_of_manu_already_warn(tmp_path: Path) -> None:
    """Two ``BkManuPara`` descriptor defaults are outside their own range - U3 again, and
    the reason the save only warns."""
    doc = default_document("manu")
    n = len(doc.validate())
    assert n > 0
    page = MachiningPage(document=doc, path=tmp_path / "BkManuPara.xml")
    try:
        assert page.save() is not None
        assert page.last_save_warning.startswith(f"{n} value")
    finally:
        page.deleteLater()


def test_vendor_layer_file_saves_byte_identically_with_a_warning_if_any(
    src_dir: Path, tmp_path: Path
) -> None:
    """U3: the vendor's own file is written unchanged whatever validate() says."""
    import shutil

    work = tmp_path / "BkLayerPara.xml"
    shutil.copyfile(src_dir / "File" / "BkLayerPara.xml", work)
    original = work.read_bytes()
    doc = read_params(work, "layer")
    bar = LayerFileBar(doc, path=work)
    try:
        assert bar.save() == work and work.read_bytes() == original
        n = len(doc.validate())
        assert (bar.last_save_warning == "") == (n == 0)
        if n:
            assert bar.last_save_warning.startswith(f"{n} value")
    finally:
        bar.deleteLater()


def test_opening_a_page_leaves_an_out_of_range_bool_alone() -> None:
    """D15, found while writing the tests above: a bool row stored as 2 was rewritten to 1
    by merely building the page, because the checkbox's initial state went through the
    toggle handler, which compared the check state with the raw stored value."""
    doc = default_document("hard")
    doc.values["PAxisParam"]["A0"]["DoubleDriver"] = 2
    before = serialize_params(doc)
    page = HardwarePage(document=doc)
    try:
        assert doc.values["PAxisParam"]["A0"]["DoubleDriver"] == 2
        assert serialize_params(doc) == before
        assert any("DoubleDriver" in p for p in doc.validate())
    finally:
        page.deleteLater()
