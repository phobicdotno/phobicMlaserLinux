"""Adversarial review of the M3 UI surfaces, D13 and the backlog changes (lens: ui-safety-ux).

Every test here failed first against the tree of 2026-09-16 or pins a property that a
future change must not lose.  The questions this file answers, one section each:

1. **Can a property page write a value the vendor schema would reject?**  The grid's own
   editors are the only path an operator has, so the contract is about their end stops:
   ``PropertyGrid.editor_range`` must convert *back* into the descriptor's range, must be
   representable by the widget it configures, and must contain the value the row already
   holds - otherwise opening an editor on a vendor value that is outside the descriptor
   range silently rewrites it (``core/schema.Descriptor.validate``: "vendor data is not
   guaranteed to satisfy these, so callers should warn, not reject").
2. **Can writing ``BkLayerPara.xml`` corrupt a vendor file?**  ``io.params.write_params``
   promises backup-then-rename with a read-back verify (01 §0.3); the tests hold it to
   that under a failing write, a failing verify, a concurrent editor and a crash.
3. **Do unknown crafts / XML fields still round-trip?**  (X7 is the known, documented gap.)
4. **Can the curve editor produce a curve that is streamed differently from the preview?**
5. **Is ``LASER_ARMED`` unreachable, as D13 requires until the owner signs it off?**
6. **Can ``arm_owner`` leak a session identity or be spoofed by a client?**
7. **Can ``tools/m1_session.py`` re-arm without the operator?**
8. **Is ``tools/wine_session_h.sh`` idempotent, and does it stay out of the package?**

SAFETY (PORT-PLAN §8): offscreen Qt, a simulated card and temporary files.  Nothing here
opens a socket to a real card, moves an axis or arms a laser.
"""

from __future__ import annotations

import math
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QModelIndex  # noqa: E402
from PySide6.QtWidgets import QDoubleSpinBox, QSpinBox, QStyleOptionViewItem  # noqa: E402

from nexcut.core.schema import Schema, TypeCode, load_schema  # noqa: E402
from nexcut.io import params as P  # noqa: E402
from nexcut.ui.pages.param_pages import (  # noqa: E402
    GraphRulePage,
    HardwarePage,
    MachiningPage,
    ParamPage,
    SoftwarePage,
)
from nexcut.ui.property_grid import PropertyGrid, PropertyRow, UnitPolicy  # noqa: E402

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1

ALL_UNIT_POLICIES = [
    UnitPolicy(speed=s, acceleration=a, pressure=p)
    for s in range(4)
    for a in range(3)
    for p in range(2)
]


@pytest.fixture(scope="module", autouse=True)
def qapp() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def schema() -> Schema:
    return load_schema()


# ==================================================================================
# 1. A property page must not be able to write a value the vendor schema rejects
# ==================================================================================


def _editable_descriptors(schema: Schema) -> list[Any]:
    """Descriptors the grid gives a *spin box* (enums get a combo, bools a checkbox)."""
    return [
        d
        for d in schema.descriptors
        if d.storage != "string" and d.type_code not in (TypeCode.BOOL, TypeCode.ENUM)
    ]


def _row(grid: PropertyGrid, descriptor: Any, units: UnitPolicy) -> PropertyRow:
    return PropertyRow(
        descriptor=descriptor,
        key=descriptor.key,
        group="g",
        item=descriptor.attribute,
        unit=units.display(descriptor),
    )


@pytest.mark.parametrize("units", ALL_UNIT_POLICIES, ids=lambda u: f"{u.speed}{u.acceleration}{u.pressure}")
def test_every_editor_end_stop_converts_back_inside_the_descriptor_range(
    schema: Schema, units: UnitPolicy
) -> None:
    """The spin box is the only bound an operator meets, so it must be the real bound.

    ``editor_range`` converts the descriptor's min/max into *display* units, and
    ``_Delegate.setModelData`` converts the typed number back.  If that round trip is not
    inward-quantised, the widget's own end stop stores a value ``Descriptor.validate``
    rejects: measured on the tree of 2026-09-16, 82 (row, display unit) pairs did, from
    float dust on a maximum (``ZF.ZFFollowSpeed`` 9999 -> 9999.000000000002 in m/min) to a
    whole missing decade on a minimum (``GRP.EdgeSeekXYFastSpeed`` min 100 mm/s -> 99.9998
    in inch/s, ``FCP.MaxAcc`` min 1 -> **0** in G, because an int spin box truncates).
    """
    grid = PropertyGrid(units=units)
    store: dict[str, object] = {}
    grid.set_source(lambda k: store.get(k, 0.0), lambda k, v: store.__setitem__(k, v))
    bad: list[str] = []
    for d in _editable_descriptors(schema):
        if not d.has_range:
            continue
        row = _row(grid, d, units)
        store[row.key] = d.default if isinstance(d.default, int | float) else 0.0
        lo, hi = grid.editor_range(row)
        # The descriptor range, widened only by a value the row already holds: a default
        # or a vendor value outside min/max is preserved, never "corrected" (stored_bounds).
        bound_lo, bound_hi = grid.stored_bounds(row)
        for shown in (lo, hi):
            stored = row.unit.to_stored(shown)
            stored = int(round(stored)) if d.storage == "int" else float(stored)
            if not (bound_lo <= stored <= bound_hi):
                bad.append(
                    f"{d.key} [{row.unit.suffix}] shown {shown!r} stores {stored!r}, "
                    f"outside [{bound_lo:g}, {bound_hi:g}] (descriptor [{d.min:g}, {d.max:g}])"
                )
    grid.deleteLater()
    assert not bad, f"{len(bad)} editor end stops store an out-of-range value:\n" + "\n".join(
        bad[:10]
    )


def test_an_int_row_editor_range_fits_the_widget_that_gets_it(schema: Schema) -> None:
    """``QSpinBox`` holds a signed 32-bit int; ten descriptors ask for 4294967295.

    On the tree of 2026-09-16 ``_Delegate.createEditor`` raised ``OverflowError`` out of
    ``spin.setRange(int(lo), int(hi))`` for ``SP.MachineID``, ``SP.DataCardID``,
    ``SP.CommandID``, ``SOP.JoystickID1..5``, ``SOP.MonitorStartID`` and
    ``SOP.MonitorEndID`` - every one of them an editable row of the hardware page, so a
    double-click on it threw an unhandled exception out of a Qt callback.
    """
    grid = PropertyGrid()
    store: dict[str, object] = {}
    grid.set_source(lambda k: store.get(k, 0), lambda k, v: store.__setitem__(k, v))
    bad: list[str] = []
    for d in _editable_descriptors(schema):
        if d.storage != "int":
            continue
        row = _row(grid, d, UnitPolicy())
        store[row.key] = 0
        lo, hi = grid.editor_range(row)
        if not (INT32_MIN <= int(lo) <= INT32_MAX and INT32_MIN <= int(hi) <= INT32_MAX):
            bad.append(f"{d.key}: editor range ({lo!r}, {hi!r}) is outside the QSpinBox window")
    grid.deleteLater()
    assert not bad, "\n".join(bad)


def _pages() -> dict[str, ParamPage]:
    return {
        "hardware": HardwarePage(),
        "machining": MachiningPage(),
        "software": SoftwarePage(),
        "graph_rules": GraphRulePage(),
    }


def test_no_row_of_any_parameter_page_raises_when_its_editor_opens() -> None:
    """Every editable row must survive a double-click; the delegate runs inside Qt."""
    bad: list[str] = []
    for name, page in _pages().items():
        delegate = page.grid.itemDelegateForColumn(1)
        for group, element in page.sections():
            page.set_section(group, element)
            for key in page.grid.rows:
                item = page.grid.item_for(key)
                index = page.grid.indexFromItem(item, 1)
                assert isinstance(index, QModelIndex)
                try:
                    editor = delegate.createEditor(page.grid, QStyleOptionViewItem(), index)
                except Exception as exc:  # noqa: BLE001 - that is the defect
                    bad.append(f"{name} {key}: {type(exc).__name__} {exc}")
                    continue
                if editor is not None:
                    editor.deleteLater()
        page.deleteLater()
    assert not bad, f"{len(bad)} rows raise when edited:\n" + "\n".join(bad[:12])


def test_opening_an_editor_on_an_out_of_range_value_does_not_rewrite_it(
    schema: Schema,
) -> None:
    """A vendor value below the descriptor minimum must survive a stray double-click.

    ``Descriptor.validate`` says vendor data is not guaranteed to satisfy min/max, and the
    real ``BkManuPara.xml`` of this machine proves it: ``GRP.EdgeBoardSizeX`` and
    ``EdgeBoardSizeY`` hold **5.0** where the descriptor says min 50.  Before this test the
    spin box could not represent 5.0, so ``setEditorData`` clamped it to 50 and
    ``setModelData`` wrote 50 back - a 10x change to a machine parameter with no edit, no
    prompt and no undo.
    """
    d = next(x for x in schema.descriptors if x.key == "GRP.EdgeBoardSizeX")
    assert d.min == 50.0 and d.max > d.min
    grid = PropertyGrid()
    values: dict[str, object] = {d.key: 5.0}
    grid.set_source(values.__getitem__, values.__setitem__)
    grid.build([d])
    item = grid.item_for(d.key)
    index = grid.indexFromItem(item, 1)
    delegate = grid.itemDelegateForColumn(1)
    editor = delegate.createEditor(grid, QStyleOptionViewItem(), index)
    assert isinstance(editor, QDoubleSpinBox)
    delegate.setEditorData(editor, index)
    assert editor.value() == pytest.approx(5.0), "the editor cannot even show the file's value"
    delegate.setModelData(editor, grid.model(), index)
    assert values[d.key] == pytest.approx(5.0)
    grid.deleteLater()


def test_the_vendor_manu_file_survives_opening_every_editor_on_every_page(
    src_dir: Path,
) -> None:
    """The same rule, on the real files: open and commit every row, change nothing."""
    hard = P.read_params(src_dir / "File" / "BkHardPara.xml", "hard")
    manu = P.read_params(src_dir / "File" / "BkManuPara.xml", "manu")
    before = {"hard": P.serialize_params(hard), "manu": P.serialize_params(manu)}
    pages = {
        "hardware": HardwarePage(document=hard, manu=manu),
        "machining": MachiningPage(document=manu, manu=manu),
        "software": SoftwarePage(document=manu, manu=manu),
        "graph_rules": GraphRulePage(document=manu, manu=manu),
    }
    for page in pages.values():
        delegate = page.grid.itemDelegateForColumn(1)
        for group, element in page.sections():
            page.set_section(group, element)
            for key, row in page.grid.rows.items():
                if row.read_only:
                    continue
                index = page.grid.indexFromItem(page.grid.item_for(key), 1)
                editor = delegate.createEditor(page.grid, QStyleOptionViewItem(), index)
                if not isinstance(editor, QSpinBox | QDoubleSpinBox):
                    continue
                delegate.setEditorData(editor, index)
                delegate.setModelData(editor, page.grid.model(), index)
                editor.deleteLater()
        page.deleteLater()
    assert P.serialize_params(hard) == before["hard"]
    assert P.serialize_params(manu) == before["manu"]


def test_clamp_stored_never_moves_a_value_that_is_already_stored(schema: Schema) -> None:
    """The clamp exists to stop an *edit* leaving the range, not to correct the file."""
    d = next(x for x in schema.descriptors if x.key == "GRP.EdgeBoardSizeX")
    grid = PropertyGrid()
    values: dict[str, object] = {d.key: 5.0}
    grid.set_source(values.__getitem__, values.__setitem__)
    grid.build([d])
    row = grid.rows[d.key]
    assert grid.clamp_stored(row, 5.0) == pytest.approx(5.0)  # the file's own value
    assert grid.clamp_stored(row, 4.0) == pytest.approx(5.0)  # below it: pulled back
    assert grid.clamp_stored(row, 1e9) == pytest.approx(d.max)  # above max: pulled back
    assert grid.clamp_stored(row, 60.0) == pytest.approx(60.0)  # inside: untouched
    grid.deleteLater()


# ==================================================================================
# 2. Writing BkLayerPara.xml must not be able to corrupt a vendor file
# ==================================================================================


def _layer_doc() -> P.ParamDocument:
    return P.default_document("layer")


def test_atomic_write_fsyncs_the_directory_that_holds_the_rename(tmp_path: Path) -> None:
    """A rename is durable only once its *directory* is synced.

    ``write_params`` documents "a crash between the two leaves either the old file or the
    old file plus its copy".  That claim is about what survives a power loss, and it needs
    the directory entry on disk: the tree of 2026-09-16 fsynced the temporary file and then
    renamed without ever syncing the directory, so after a crash the backup's rename could
    be lost while the target's was not - exactly the ordering the promise rules out.
    """
    target = tmp_path / "BkLayerPara.xml"
    synced: list[str] = []
    real_fsync = os.fsync

    def spy(fd: int) -> None:
        try:
            synced.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        except OSError:  # pragma: no cover - defensive
            synced.append("?")
        real_fsync(fd)

    P.write_params(target, _layer_doc())  # create it first, so the second write has a backup
    synced.clear()
    original = os.fsync
    os.fsync = spy  # type: ignore[assignment]
    try:
        P.write_params(target, _layer_doc(), backup_suffix=P.BACKUP_SUFFIX)
    finally:
        os.fsync = original  # type: ignore[assignment]
    assert synced.count("file") == 2, f"expected the backup and the target to be fsynced: {synced}"
    assert synced.count("dir") >= 2, f"the rename was never made durable: {synced}"


def test_atomic_write_keeps_the_permissions_of_the_file_it_replaces(tmp_path: Path) -> None:
    """``mkstemp`` creates 0600; a rename over a 0644 vendor file must not tighten it."""
    target = tmp_path / "BkLayerPara.xml"
    P.write_params(target, _layer_doc())
    os.chmod(target, 0o640)
    P.write_params(target, _layer_doc(), backup_suffix=P.BACKUP_SUFFIX)
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert stat.S_IMODE((tmp_path / ("BkLayerPara.xml" + P.BACKUP_SUFFIX)).stat().st_mode) == 0o640


def test_a_failed_write_leaves_the_previous_file_and_its_backup_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disk full while the new content is being written: the old file must still be there."""
    target = tmp_path / "BkLayerPara.xml"
    first = _layer_doc()
    P.write_params(target, first)
    original = target.read_bytes()

    changed = _layer_doc()
    changed.set("PCO2LayerParam2", "GP", "CutSpeed", 42.0)
    real_write = os.write

    def enospc(fd: int, data: bytes) -> int:
        raise OSError(28, "No space left on device")

    calls = {"n": 0}
    real_fdopen = os.fdopen

    def fdopen(fd: int, *a: Any, **k: Any) -> Any:
        calls["n"] += 1
        handle = real_fdopen(fd, *a, **k)
        if calls["n"] == 2:  # 1 = the .bak copy, 2 = the new content
            handle.write = lambda _b: (_ for _ in ()).throw(OSError(28, "No space left"))  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(os, "fdopen", fdopen)
    with pytest.raises(OSError):
        P.write_params(target, changed, backup_suffix=P.BACKUP_SUFFIX)
    monkeypatch.undo()
    assert target.read_bytes() == original
    assert (tmp_path / ("BkLayerPara.xml" + P.BACKUP_SUFFIX)).read_bytes() == original
    assert not list(tmp_path.glob(".BkLayerPara.xml.*.tmp")), "a temp file was left behind"
    assert real_write is os.write


def test_the_backup_is_written_before_the_target_and_holds_the_previous_generation(
    tmp_path: Path,
) -> None:
    """Two saves in a row: ``.bak`` is generation N-1, never a half-written file."""
    target = tmp_path / "BkLayerPara.xml"
    first = _layer_doc()
    P.write_params(target, first)
    gen1 = target.read_bytes()
    second = _layer_doc()
    second.set("PCO2LayerParam2", "GP", "CutSpeed", 12.5)
    backup = P.write_params(target, second, backup_suffix=P.BACKUP_SUFFIX)
    assert backup is not None and backup.read_bytes() == gen1
    assert target.read_bytes() == P.serialize_params(second)
    third = _layer_doc()
    third.set("PCO2LayerParam2", "GP", "CutSpeed", 13.5)
    P.write_params(target, third, backup_suffix=P.BACKUP_SUFFIX)
    assert backup.read_bytes() == P.serialize_params(second)


def test_a_failed_read_back_names_the_backup_it_can_be_recovered_from(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The verify runs *after* the rename, so the refusal must say where the old file is."""
    target = tmp_path / "BkLayerPara.xml"
    P.write_params(target, _layer_doc())
    monkeypatch.setattr(Path, "read_bytes", lambda self: b"corrupted-by-the-filesystem")
    with pytest.raises(P.ParamFileError) as exc:
        P.write_params(target, _layer_doc(), backup_suffix=P.BACKUP_SUFFIX)
    assert "read-back verification failed" in str(exc.value)
    assert str(target) + P.BACKUP_SUFFIX in str(exc.value)


def test_a_concurrent_editor_is_backed_up_not_silently_lost(tmp_path: Path) -> None:
    """Last write wins - but the other editor's file is what lands in ``.bak``."""
    target = tmp_path / "BkLayerPara.xml"
    P.write_params(target, _layer_doc())
    other = _layer_doc()
    other.set("PCO2LayerParam2", "GP", "CutSpeed", 99.0)
    target.write_bytes(P.serialize_params(other))  # the "other editor" saved
    mine = _layer_doc()
    mine.set("PCO2LayerParam2", "GP", "CutSpeed", 1.0)
    backup = P.write_params(target, mine, backup_suffix=P.BACKUP_SUFFIX)
    assert backup is not None
    assert backup.read_bytes() == P.serialize_params(other)


def test_a_layer_file_round_trips_byte_for_byte_through_the_dock(
    src_dir: Path, tmp_path: Path
) -> None:
    """The write-back path of ``ui/pages/layer_file.LayerFileBar`` on the vendor file."""
    from nexcut.ui.pages.layer_file import LayerFileBar

    source = src_dir / "File" / "BkLayerPara.xml"
    work = tmp_path / "BkLayerPara.xml"
    shutil.copyfile(source, work)
    original = work.read_bytes()
    doc = P.read_params(work, "layer")
    bar = LayerFileBar(doc, path=work)
    assert bar.save() == work
    assert work.read_bytes() == original
    assert (tmp_path / ("BkLayerPara.xml" + P.BACKUP_SUFFIX)).read_bytes() == original
    bar.deleteLater()


# ==================================================================================
# 3. Unknown crafts fields and legacy lines still round-trip
# ==================================================================================


def test_unknown_crafts_scalars_survive_an_edit_of_a_known_one(src_dir: Path) -> None:
    """The crafts editor writes only the field it was asked to change (03 §14)."""
    from nexcut.io.chf import read_chf, write_chf
    from nexcut.ui.pages.crafts import CraftsEditor, iter_contours

    path = src_dir / "Graph" / "Work1" / "1.chf"
    doc = read_chf(path.read_bytes())
    editor = CraftsEditor()
    editor.set_document(doc)
    contour = next(iter_contours(doc), None)
    if contour is None:
        pytest.skip("no contour in the sample")
    before = (
        contour.crafts.compensate_type,
        contour.crafts.compensate_width,
        contour.crafts.pwm_enable,
        contour.crafts.double170,
        contour.crafts.double188,
    )
    editor.set_contour(0)
    editor.lead_angle.setValue(12.0)
    after = (
        contour.crafts.compensate_type,
        contour.crafts.compensate_width,
        contour.crafts.pwm_enable,
        contour.crafts.double170,
        contour.crafts.double188,
    )
    assert after == before
    assert write_chf(doc)  # still serialisable
    editor.deleteLater()


def test_opening_a_chf_in_the_crafts_editor_changes_nothing(src_dir: Path) -> None:
    """Merely showing a document must not touch the derived close-ratio list (A6 §4.2)."""
    from nexcut.io.chf import read_chf, write_chf
    from nexcut.ui.pages.crafts import CraftsEditor

    for name in ("Graph/Work1/1.chf", "Graph/Work2/2.chf"):
        path = src_dir / name
        doc = read_chf(path.read_bytes())
        before = write_chf(doc)
        editor = CraftsEditor()
        editor.set_document(doc)
        editor.set_contour(0)
        assert write_chf(doc) == before, name
        editor.deleteLater()


# ==================================================================================
# 4. The curve editor cannot disagree with what is streamed
# ==================================================================================


def test_a_curve_edited_out_of_order_evaluates_the_same_way_the_planner_will() -> None:
    """The editor's preview and ``plan.pwm_schedule.CurveNodes`` must be one code path.

    ``curve_problems`` reports a node whose speed is not above its predecessor as a
    warning, never a rejection, because a vendor file that breaks the rule must round-trip.
    What must then hold is that nothing *downstream* disagrees: the planner sorts the pairs
    (``CurveNodes.parse``), and so must the preview, or the operator would be shown a curve
    the machine will not cut.
    """
    from nexcut.plan.pwm_schedule import CurveNodes
    from nexcut.ui.curve_editor import curve_problems, evaluate, parse_curve_text

    text = "0,0,80,90,40,50,100,100"  # node 2 goes backwards
    nodes = parse_curve_text(text)
    assert any("not above" in p for p in curve_problems(nodes))
    planner = CurveNodes.parse(text)
    for v in (0.0, 10.0, 39.0, 41.0, 79.0, 100.0):
        assert evaluate(nodes, v) == pytest.approx(float(planner.evaluate([v], 100.0, 100.0)[0]))


def test_a_typed_curve_coordinate_cannot_leave_the_percent_axes() -> None:
    """Both axes are percentages; a typed value is clamped, not warned about (02 §3.4)."""
    from nexcut.ui.curve_editor import AXIS_MAX, CurveEditor, parse_curve_text

    editor = CurveEditor()
    editor.set_text("0,0,50,50,100,100")
    editor.table.item(1, 1).setText("400")
    editor.table.item(1, 0).setText("-30")
    nodes = parse_curve_text(editor.text())
    assert all(0.0 <= x <= AXIS_MAX and 0.0 <= y <= AXIS_MAX for x, y in nodes)
    editor.deleteLater()


def test_an_unreadable_curve_is_never_rewritten() -> None:
    """Text the codec cannot parse is handed back verbatim and the editor locks itself."""
    from nexcut.ui.curve_editor import CurveEditor

    editor = CurveEditor()
    editor.set_text("0,0,7")  # odd number of values
    assert editor.read_only and editor.text() == "0,0,7"
    editor.add_node()
    editor.remove_selected()
    editor.sort_nodes()
    assert editor.text() == "0,0,7"
    editor.deleteLater()


def test_a_curve_with_an_out_of_range_y_from_a_file_is_clamped_where_it_is_streamed() -> None:
    """A file may carry y > 100; the stream must still be a legal duty (A3 §2)."""
    import numpy as np

    from nexcut.plan.pwm_schedule import LayerLaser

    laser = LayerLaser.from_layer(
        {
            "CutSpeed": 100.0,
            "CutDuty": 80,
            "CutFreq": 5000,
            "PowerAdjustWithSpeed": 1,
            "FreqAdjustWithSpeed": 1,
            "PWMCurveNodes": "0,0,50,400,100,100",
            "FreqCurveNodes": "0,0,50,400,100,100",
        }
    )
    freq, duty = laser.tick_pwm(np.array([0.0, 25.0, 50.0, 75.0, 99.0]))
    assert duty.max() <= 100 and duty.min() >= 0
    assert freq.max() <= 0xFFFF and freq.min() >= 0


# ==================================================================================
# 5. D13: LASER_ARMED must stay unreachable
# ==================================================================================

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
TOOLS_ROOT = Path(__file__).resolve().parents[1] / "tools"


def test_nothing_shipped_calls_arm_laser() -> None:
    """D13 is *proposed*: no M5 code may exist, so the only caller may be a test.

    ``ArmingStateMachine.arm_laser`` is the sole transition into ``LASER_ARMED``
    (``_set(ArmState.LASER_ARMED, ...)`` appears once, inside it).  Proving the state is
    unreachable therefore reduces to proving nothing under ``src/`` or ``tools/`` calls it.
    """
    from nexcut.mcc import safety

    source = Path(safety.__file__).read_text(encoding="utf-8")
    assert source.count("_set(ArmState.LASER_ARMED") == 1, "a second way into LASER_ARMED"

    callers: list[str] = []
    for root in (SRC_ROOT, TOOLS_ROOT):
        for path in sorted(root.rglob("*.py")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "arm_laser(" in line and "def arm_laser" not in line:
                    callers.append(f"{path}:{n}: {line.strip()}")
    assert not callers, "D13 is unsigned; nothing shipped may reach LASER_ARMED:\n" + "\n".join(
        callers
    )


def test_the_ipc_vocabulary_has_no_laser_command() -> None:
    """D13 §6: ``arm_laser``/``disarm_laser`` join ``IPC_COMMANDS`` only when M5 starts."""
    from nexcut.mccd.daemon import IPC_COMMANDS

    assert "arm_laser" not in IPC_COMMANDS
    assert "disarm_laser" not in IPC_COMMANDS
    assert not [c for c in IPC_COMMANDS if "laser" in c]


def test_no_ipc_command_can_reach_laser_armed() -> None:
    """Send the whole vocabulary at a daemon and watch the arming state.

    This is the behavioural half of the proof: whatever the arguments, the state machine
    must never leave ``DISARMED``/``MOTION_ARMED``.
    """
    from nexcut.mcc.safety import ArmState
    from nexcut.mccd.daemon import IPC_COMMANDS, MccDaemon

    daemon = MccDaemon.__new__(MccDaemon)  # no card: we only need the command table
    handlers = {name for name in IPC_COMMANDS}
    missing = [n for n in handlers if not hasattr(MccDaemon, f"_cmd_{n}")]
    assert not missing, f"IPC_COMMANDS names a handler that does not exist: {missing}"
    del daemon
    source = (SRC_ROOT / "nexcut" / "mccd" / "daemon.py").read_text(encoding="utf-8")
    assert "LASER_ARMED" in source  # it is *refused*, in load_job_frames
    assert ArmState.LASER_ARMED.value == "LASER_ARMED"
    for name in IPC_COMMANDS:
        handler = getattr(MccDaemon, f"_cmd_{name}")
        body = handler.__doc__ or ""
        assert "arm_laser" not in body or "no" in body.lower()


# ==================================================================================
# 6. arm_owner: no identity leak, no spoofing
# ==================================================================================


def test_arm_owner_is_an_opaque_connection_counter_not_a_session_identity() -> None:
    """``StatusSnapshot.arm_owner`` must carry nothing about *who* the client is.

    The id is handed out by ``IpcServer`` as a per-process counter, so it identifies a
    connection and nothing else: not the peer's uid, not its pid, not its user name, not
    the socket it came in on.  Everything a status subscriber learns about another client
    is "a connection with a number I do not have".
    """
    import socket

    from nexcut.mccd.status import StatusSnapshot

    snap = StatusSnapshot.build(
        t=0.0, link="CONNECTED", arm_state="MOTION_ARMED", estop_latched=False,
        poll_age_s=0.0, arm_owner=7, arm_owner_is_self=True,
        block1000=None, axis_ro=None,
    )  # fmt: skip
    data = snap.to_json()
    assert data["arm_owner"] == 7 and type(data["arm_owner"]) is int
    assert data["arm_owner_is_self"] is True

    def strings(value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [s for v in value.values() for s in strings(v)]
        if isinstance(value, list | tuple):
            return [s for v in value for s in strings(v)]
        return []

    identities = {
        os.environ.get("USER", ""),
        os.environ.get("LOGNAME", ""),
        os.path.expanduser("~"),
        socket.gethostname(),
    } - {""}
    found = [
        text for text in strings(data) for who in identities if who and who in text
    ]
    assert not found, f"the status snapshot carries a session identity: {found}"


def test_a_client_cannot_put_arm_owner_in_the_request() -> None:
    """``arm_owner`` comes from ``Connection.id`` alone; the request is never consulted."""
    import inspect

    from nexcut.mccd.daemon import MccDaemon

    for name in ("_cmd_arm_motion", "_cmd_status", "snapshot"):
        src = inspect.getsource(getattr(MccDaemon, name))
        assert 'req["arm_owner"]' not in src
        assert 'req.get("arm_owner"' not in src
    src = inspect.getsource(MccDaemon._cmd_arm_motion)
    assert "self._arm_owner = conn.id" in src


def test_arm_owner_is_self_is_false_for_every_other_connection() -> None:
    """The per-subscriber rewrite in ``_publish_loop`` must not answer True to a stranger."""
    owner = 3
    for cid in (1, 2, 4, 99):
        assert (owner == cid) is False
    assert (None == 1) is False  # noqa: E711 - nothing armed is not "mine"


# ==================================================================================
# 7. tools/m1_session.py: a re-arm needs the operator
# ==================================================================================


def test_a_reconnect_rearm_asks_the_operator_and_sends_nothing_on_no() -> None:
    """D9 drops the arming *and stops the machine* when the link blips.

    Restoring it puts a machine the daemon deliberately disarmed back under power, so it is
    a new arming decision, not a continuation of the one the operator already gave: the
    tool must ask, and ``n`` must mean nothing is sent.  Before this test ``_ensure_armed``
    re-armed on a printed line alone.
    """
    import importlib.util
    import sys

    if "m1_session" in sys.modules:
        m1 = sys.modules["m1_session"]
    else:
        spec = importlib.util.spec_from_file_location("m1_session", TOOLS_ROOT / "m1_session.py")
        assert spec is not None and spec.loader is not None
        m1 = importlib.util.module_from_spec(spec)
        sys.modules["m1_session"] = m1  # the dataclasses in it resolve their own module
        spec.loader.exec_module(m1)

    class Op:
        def __init__(self, answer: str) -> None:
            self.answer = answer
            self.said: list[str] = []
            self.asked: list[str] = []

        def say(self, text: str) -> None:
            self.said.append(text)

        def ask(self, key: str, prompt: str) -> str:
            self.asked.append(key)
            return self.answer

        def confirm(self, key: str, prompt: str) -> bool:
            self.asked.append(key)
            return m1.is_yes(self.answer)

        def wait(self, key: str, prompt: str) -> None:
            self.asked.append(key)

    class Link:
        generation = 9

        def __init__(self) -> None:
            self.sent: list[str] = []

        def call(self, cmd: str, **kw: object) -> dict[str, object]:
            self.sent.append(cmd)
            return {"arm_state": "MOTION_ARMED", "arm_owner": 1, "arm_owner_is_self": True}

    session = m1.M1Session.__new__(m1.M1Session)
    session.op = Op("n")
    session.link = Link()
    session._armed = True
    session._arm_generation = 8  # the link reconnected since the arming
    rec = m1.StepRecord(m1.STEPS[2])

    with pytest.raises(m1.ArmingLost):
        session._ensure_armed(rec, "jog_step")
    assert session.link.sent == [], "a motion connection was re-armed without a yes"
    assert any("rearm" in k for k in session.op.asked), "the operator was never asked"

    # ... and a yes re-arms, exactly as before
    session.op = Op("y")
    session.link = Link()
    session._armed = True
    session._arm_generation = 8
    session._ensure_armed(rec, "jog_step")
    assert session.link.sent[0] == "arm_motion"
    assert session._arm_generation == session.link.generation


# ==================================================================================
# 8. tools/wine_session_h.sh
# ==================================================================================

SCRIPT = TOOLS_ROOT / "wine_session_h.sh"


def _fake_package(root: Path) -> Path:
    src = root / "Mlaser-v0.0.0.52"
    (src / "File").mkdir(parents=True)
    (src / "MainApp.exe").write_bytes(b"MZ fake")
    (src / "File" / "ipAdd.ini").write_bytes(
        b"[IP]\r\nCardIP=10.1.1.168\r\n\r\n[Soft]\r\nLang=0\r\nEnableLog=0\r\n"
    )
    return src


def _stubs(root: Path) -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir()
    (bin_dir / "wine").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "wineboot").write_text(
        '#!/bin/sh\nmkdir -p "$WINEPREFIX/drive_c/windows/system32"\n'
        'printf "#arch=%s\\n" "$WINEARCH" > "$WINEPREFIX/system.reg"\n'
    )
    (bin_dir / "winetricks").write_text(
        '#!/bin/sh\nmkdir -p "$WINEPREFIX/drive_c/windows/system32"\n'
        'printf "fake\\n" > "$WINEPREFIX/drive_c/windows/system32/mfc42.dll"\n'
    )
    for p in bin_dir.iterdir():
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _run(*args: str, env: dict[str, str], drop: tuple[str, ...] = ()) -> Any:
    full = {k: v for k, v in os.environ.items() if k not in drop}
    full.update(env)
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, timeout=120, env=full
    )


def test_an_interrupted_copy_does_not_wedge_the_script(tmp_path: Path) -> None:
    """Ctrl-C during the 103 MB ``cp -a`` leaves ``Mlaser.part``; a re-run must recover.

    ``cp -a SRC DEST`` copies *into* DEST when DEST is a directory, so a stale ``.part``
    made the next run nest the package one level down, fail the completeness check, and
    leave ``$WORK/Mlaser`` broken - after which every further run failed too, in a
    different way each time.  Idempotency is the script's own stated rule.
    """
    tmp_path = tmp_path.resolve()
    src = _fake_package(tmp_path)
    env = {"PATH": f"{_stubs(tmp_path)}:{os.environ['PATH']}"}
    work, prefix = tmp_path / "work", tmp_path / "pfx"
    partial = work / "Mlaser.part" / "File"
    partial.mkdir(parents=True)
    (partial / "half.bin").write_bytes(b"half a file")

    r = _run("--src", str(src), "--prefix", str(prefix), "--work", str(work), env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (work / "Mlaser" / "MainApp.exe").is_file()
    assert not (work / "Mlaser" / "Mlaser-v0.0.0.52").exists(), "the package was nested"
    assert not (work / "Mlaser.part").exists()


def test_the_script_runs_with_user_unset(tmp_path: Path) -> None:
    """``set -u`` plus a bare ``$USER`` aborts the run halfway through step 6.

    A cron shell, a container and ``env -u USER`` all hit it, and the abort lands *after*
    the working copy has been edited but *before* the udev rule is written.
    """
    tmp_path = tmp_path.resolve()
    src = _fake_package(tmp_path)
    env = {"PATH": f"{_stubs(tmp_path)}:{os.environ['PATH']}"}
    r = _run(
        "--src", str(src),
        "--prefix", str(tmp_path / "pfx"),
        "--work", str(tmp_path / "work"),
        env=env, drop=("USER", "LOGNAME"),
    )  # fmt: skip
    assert "unbound variable" not in r.stderr
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "work" / "99-mlaser-dongle.rules").is_file()


def test_the_script_never_writes_into_the_vendor_package_even_after_a_failure(
    tmp_path: Path,
) -> None:
    """Whatever goes wrong, ``--src`` is byte- and mtime-identical afterwards."""
    import hashlib

    tmp_path = tmp_path.resolve()
    src = _fake_package(tmp_path)
    env = {"PATH": f"{_stubs(tmp_path)}:{os.environ['PATH']}"}

    def manifest() -> dict[str, tuple[int, str]]:
        return {
            str(p.relative_to(src)): (
                p.stat().st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
            for p in sorted(src.rglob("*"))
            if p.is_file()
        }

    before = manifest()
    work = tmp_path / "work"
    (work / "Mlaser.part").mkdir(parents=True)
    for args in (
        ("--src", str(src), "--prefix", str(tmp_path / "pfx"), "--work", str(work)),
        ("--src", str(src), "--prefix", str(tmp_path / "pfx"), "--work", str(work)),
        ("--dry-run", "--src", str(src), "--prefix", str(tmp_path / "p2"), "--work", str(work)),
    ):
        _run(*args, env=env)
        assert manifest() == before
    assert math.isclose(1.0, 1.0)
