"""ui/main_window.py + ui/canvas.py + ui/app.py: offscreen smoke tests (PORT-PLAN §4 M2).

Open files, toggle the view options, save a .chf round trip, recent files,
selection / measurement / zoom / pan through synthetic input events, and the
10^4-glyph responsiveness check.  No machine control exists in the UI
(PORT-PLAN §8); the Machine dock must stay disabled.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QWheelEvent  # noqa: E402

from nexcut.io import chf  # noqa: E402
from nexcut.io.params import read_params  # noqa: E402
from nexcut.model.glyph import ArcGlyph, SegmentGlyph, Vec2  # noqa: E402
from nexcut.model.graph import ChfDocument, Contour, ContourElement  # noqa: E402
from nexcut.ui.canvas import (  # noqa: E402
    CanvasView,
    RulerWidget,
    bed_from_hardware,
    canvas_style,
    colorref_to_hex,
)
from nexcut.ui.i18n import LangCatalog, Translator  # noqa: E402
from nexcut.ui.main_window import MAX_RECENT, MainWindow  # noqa: E402

VENDOR_DXF = "Testfile SS1mm 2.0s F+1 N2.dxf"


@pytest.fixture(scope="module", autouse=True)
def qapp() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture()
def window(tmp_path: Path, qapp: QtWidgets.QApplication) -> Iterator[MainWindow]:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    win = MainWindow(settings=settings, interactive=False, translator=Translator(LangCatalog()))
    win.resize(1100, 760)
    win.show()
    qapp.processEvents()
    yield win
    win.close()
    win.deleteLater()
    qapp.processEvents()


def _send_mouse(
    view: CanvasView,
    kind: QEvent.Type,
    pos: QPointF,
    button: Qt.MouseButton,
    buttons: Qt.MouseButton,
    mods: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    vp = view.viewport()
    ev = QMouseEvent(kind, pos, vp.mapToGlobal(pos), button, buttons, mods)
    QtWidgets.QApplication.sendEvent(vp, ev)


def _click(
    view: CanvasView, pos: QPointF, mods: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier
) -> None:
    left = Qt.MouseButton.LeftButton
    _send_mouse(view, QEvent.Type.MouseButtonPress, pos, left, left, mods)
    _send_mouse(view, QEvent.Type.MouseButtonRelease, pos, left, Qt.MouseButton.NoButton, mods)


def _drag(view: CanvasView, a: QPointF, b: QPointF, button: Qt.MouseButton) -> None:
    _send_mouse(view, QEvent.Type.MouseButtonPress, a, button, button)
    mid = (a + b) / 2
    _send_mouse(view, QEvent.Type.MouseMove, mid, Qt.MouseButton.NoButton, button)
    _send_mouse(view, QEvent.Type.MouseMove, b, Qt.MouseButton.NoButton, button)
    _send_mouse(view, QEvent.Type.MouseButtonRelease, b, button, Qt.MouseButton.NoButton)


def test_open_toggle_save_roundtrip(window: MainWindow, src_dir: Path, tmp_path: Path) -> None:
    src = src_dir / "File/autosave.chf"
    assert window.windowTitle().startswith("Untitled-")
    assert window.open_path(src)
    view = window.canvas
    assert view.scene_data is not None and len(view.scene_data.contours) == 24
    assert window.layer_items[0].text(1) == "24" and window.layer_items[1].text(1) == "0"
    assert window.windowTitle().startswith("autosave.chf")
    # the whole drawing is inside the viewport after zoom-to-fit
    vis = view.visible_scene_rect()
    x0, y0, x1, y1 = view.scene_data.bbox
    assert vis.left() <= x0 and vis.right() >= x1 and vis.top() <= y0 and vis.bottom() >= y1

    # view toggles drive the canvas flags (GRP.IsShow*, IGP.EnableRuler defaults 1/1/0/1)
    f = view.flags
    assert (f.show_start, f.show_arrows, f.show_index, f.show_rulers) == (True, True, False, True)
    window.action_show_index.setChecked(True)
    window.action_show_start.setChecked(False)
    window.action_show_arrows.setChecked(False)
    assert (f.show_start, f.show_arrows, f.show_index) == (False, False, True)
    assert window.canvas_widget.h_ruler.isVisibleTo(window)
    window.action_rulers.setChecked(False)
    assert not f.show_rulers and not window.canvas_widget.h_ruler.isVisibleTo(window)
    window.action_rulers.setChecked(True)
    assert window.canvas_widget.v_ruler.isVisibleTo(window)
    img = window.grab().toImage()
    assert not img.isNull() and img.width() > 0

    # Save: autosave.chf re-saved byte-identically, becomes current path + recent entry
    out = tmp_path / "saved.chf"
    assert window.save_to(out)
    assert out.read_bytes() == src.read_bytes()
    assert window.path == out
    assert window.save()  # save again to the current path
    assert out.read_bytes() == src.read_bytes()
    assert window.recent_files()[0] == str(out.resolve())
    assert str(src.resolve()) in window.recent_files()

    # machine dock is a disabled placeholder
    assert not window.machine_dock.widget().isEnabled()


def test_open_every_sample_and_dxf_then_save_chf(
    window: MainWindow, src_dir: Path, tmp_path: Path
) -> None:
    samples = sorted((src_dir / "Graph").rglob("*.chf")) + [
        src_dir / "File/autosave.chf",
        src_dir / "File/Temp/tempGraph.chf",
    ]
    for p in samples:
        assert window.open_path(p), p
        assert window.canvas.scene_data is not None and window.canvas.scene_data.contours
    dxf = src_dir.parent / VENDOR_DXF
    if not dxf.is_file():
        pytest.skip("vendor DXF not available")
    assert window.open_path(dxf)
    assert window.path is None  # an import has no .chf path yet
    assert len(window.document.graphs) == 3
    out = tmp_path / "from_dxf.chf"
    assert window.save_to(out)
    back = chf.load_chf(out)
    assert len(back.graphs) == 3
    assert chf.write_chf(back) == out.read_bytes()
    # recent list: newest first, deduplicated, capped
    rec = window.recent_files()
    assert rec[0] == str(out.resolve()) and len(rec) == len(set(rec)) <= MAX_RECENT
    actions = window.recent_menu.actions()
    assert len(actions) == len(rec)
    actions[1].trigger()  # the DXF
    assert window.loaded is not None and window.loaded.kind == "dxf"


def test_open_failure_is_reported_without_dialog(window: MainWindow, tmp_path: Path) -> None:
    bad = tmp_path / "broken.chf"
    bad.write_bytes(b"garbage")
    assert not window.open_path(bad)
    assert window.document is None
    assert "failed" in window.statusBar().currentMessage().lower()
    assert not window.open_path(tmp_path / "x.svg")
    assert not window.save()


def test_select_measure_zoom_pan(
    window: MainWindow, src_dir: Path, qapp: QtWidgets.QApplication
) -> None:
    assert window.open_path(src_dir / "Graph/Work1/2.chf")
    view = window.canvas
    data = view.scene_data
    assert data is not None
    cv = data.contours[0]
    # point on the first contour, in viewport pixels
    pl = cv.polylines[0]
    mid_mm = QPointF(float(pl[len(pl) // 2, 0]), float(pl[len(pl) // 2, 1]))
    pos = view.map_from_scene_f(mid_mm)
    _click(view, pos)
    assert view.selected == [0]
    assert window.property_panel.values["count"].text() == "1"
    assert window.property_panel.values["kind"].text() in ("Contour", "Group")
    assert window.selection_label.text() == "1 selected"
    # click on empty space far away clears
    _click(view, QPointF(3, 3))
    assert view.selected == []
    # ctrl-click toggles
    _click(view, pos, Qt.KeyboardModifier.ControlModifier)
    assert view.selected == [0]
    _click(view, pos, Qt.KeyboardModifier.ControlModifier)
    assert view.selected == []
    # box select everything
    vp = view.viewport().rect()
    _drag(view, QPointF(1, 1), QPointF(vp.width() - 2, vp.height() - 2), Qt.MouseButton.LeftButton)
    assert view.selected == list(range(len(data.contours)))
    # hidden layer: not pickable
    window.layer_items[0].setCheckState(0, Qt.CheckState.Unchecked)
    assert 0 in view.hidden_layers and view.pick_at(pos) is None
    window.layer_items[0].setCheckState(0, Qt.CheckState.Checked)
    assert view.pick_at(pos) == 0

    # cursor position in mm on the status bar
    _send_mouse(view, QEvent.Type.MouseMove, pos, Qt.MouseButton.NoButton, Qt.MouseButton.NoButton)
    x_txt = window.cursor_label.text()
    assert f"X: {mid_mm.x():.1f}"[:-1] in x_txt and "Y:" in x_txt

    # measure tool: drag 100 px horizontally and 50 px vertically
    window.action_measure.trigger()
    assert view.tool == "measure"
    s = view.scale_factor()
    a = QPointF(100, 300)
    _drag(view, a, a + QPointF(100, -50), Qt.MouseButton.LeftButton)
    msg = window.statusBar().currentMessage()
    assert msg.startswith("Measurement Result")
    dx, dy = 100 / s, 50 / s
    assert (
        f"X: {dx:.3f}mm" in msg
        and f"Y: {dy:.3f}mm" in msg
        and f"Length: {math.hypot(dx, dy):.3f}mm" in msg
    )
    window.action_select_tool.trigger()
    assert view.tool == "select"

    # wheel zoom keeps the scene point under the cursor (within scroll-bar pixel rounding)
    cursor = QPointF(220, 180)
    before = view.map_to_scene_f(cursor)
    s0 = view.scale_factor()
    wheel = QWheelEvent(
        cursor,
        view.viewport().mapToGlobal(cursor),
        QPoint(0, 0),
        QPoint(0, 240),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QtWidgets.QApplication.sendEvent(view.viewport(), wheel)
    assert view.scale_factor() == pytest.approx(s0 * 1.25**2)
    after = view.map_from_scene_f(before)
    assert abs(after.x() - cursor.x()) <= 1.5 and abs(after.y() - cursor.y()) <= 1.5

    # middle-drag pan moves the content with the mouse
    ref = view.map_to_scene_f(QPointF(300, 300))
    _drag(view, QPointF(300, 300), QPointF(360, 260), Qt.MouseButton.MiddleButton)
    moved = view.map_from_scene_f(ref)
    assert abs(moved.x() - 360) <= 1.5 and abs(moved.y() - 260) <= 1.5

    # Y-up: a larger scene y is higher on screen
    lo, hi = view.map_from_scene_f(QPointF(0, 0)), view.map_from_scene_f(QPointF(0, 10))
    assert hi.y() < lo.y()

    # rulers produce ticks with labels in mm
    ticks = window.canvas_widget.h_ruler.ticks()
    assert ticks and any(major for _, _, major in ticks)
    assert all(t1[0] < t2[0] for t1, t2 in zip(ticks, ticks[1:], strict=False))
    vticks = window.canvas_widget.v_ruler.ticks()
    assert vticks and all(t1[0] > t2[0] for t1, t2 in zip(vticks, vticks[1:], strict=False))

    window.action_fit.trigger()
    qapp.processEvents()


def test_params_drive_toggles_bed_and_background(
    src_dir: Path, tmp_path: Path, qapp: QtWidgets.QApplication
) -> None:
    manu = read_params(src_dir / "File/BkManuPara.xml", "manu")
    hard = read_params(src_dir / "File/BkHardPara.xml", "hard")
    manu.set("PGraphParam", "GRP", "IsShowNO", 1)
    manu.set("PImportGraphParam", "IGP", "EnableRuler", 0)
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    win = MainWindow(manu=manu, hard=hard, settings=settings, interactive=False)
    try:
        f = win.canvas.flags
        assert (f.show_start, f.show_arrows, f.show_index, f.show_rulers) == (
            True,
            True,
            True,
            False,
        )
        assert win.action_show_index.isChecked() and not win.action_rulers.isChecked()
        assert win.canvas.bed == (1371.0, 950.0)  # MAC / MAC_1 SoftLimitMaxLen
        assert win.canvas.style.background == "#000000"  # SP.DrawblkColor = 0
        assert win.gate_params.overlap_gate == pytest.approx(0.01)
    finally:
        win.close()
        win.deleteLater()
    assert bed_from_hardware(None) == (1300.0, 900.0)
    assert colorref_to_hex(0x0000FF) == "#ff0000" and colorref_to_hex(0xFF8000) == "#0080ff"
    assert canvas_style().background == "#000000"


def _big_document(n_contours: int = 2000) -> ChfDocument:
    """10^4 glyphs: 2 000 contours of 4 segments + 1 arc on a 50 x 40 grid."""
    graphs = []
    for i in range(n_contours):
        ox, oy = (i % 50) * 25.0, (i // 50) * 22.0
        els = [
            ContourElement(SegmentGlyph(Vec2(ox, oy), Vec2(ox + 20, oy))),
            ContourElement(SegmentGlyph(Vec2(ox + 20, oy), Vec2(ox + 20, oy + 15))),
            ContourElement(SegmentGlyph(Vec2(ox + 20, oy + 15), Vec2(ox + 5, oy + 15))),
            ContourElement(ArcGlyph(Vec2(ox + 5, oy + 10), 5.0, math.pi / 2, math.pi)),
            ContourElement(SegmentGlyph(Vec2(ox, oy + 10), Vec2(ox, oy))),
        ]
        graphs.append(Contour(elements=els, layer=i % 3))
    return ChfDocument(graphs=graphs)


def test_ten_thousand_glyphs_stay_responsive(qapp: QtWidgets.QApplication) -> None:
    doc = _big_document()
    view = CanvasView(flags=None)
    view.resize(1000, 700)
    view.show()
    qapp.processEvents()
    t0 = time.perf_counter()
    data = view.set_document(doc)
    view.zoom_to_fit()
    t_build = time.perf_counter() - t0
    assert data is not None and data.glyph_count == 10_000
    assert view.batch_count <= 10  # batched per layer, not one item per glyph

    img = QImage(view.viewport().size(), QImage.Format.Format_RGB32)

    def paint() -> float:
        t = time.perf_counter()
        p = QPainter(img)
        view.render(p)
        p.end()
        return time.perf_counter() - t

    t_paint = paint()
    view.set_flags(show_index=True)
    t_markers = paint()
    view.zoom_at(8.0, QPointF(500, 350))
    t_zoomed = paint()
    t = time.perf_counter()
    view.select(range(0, 2000, 2))
    t_select = time.perf_counter() - t
    idx = view.pick_at(view.map_from_scene_f(QPointF(10.0, 0.0)))
    # Budgets are generous (UNVERIFIED port targets for slow CI machines); typical: < 0.5 s each.
    assert t_build < 8.0, t_build
    assert t_paint < 2.0 and t_markers < 3.0 and t_zoomed < 2.0, (t_paint, t_markers, t_zoomed)
    assert t_select < 3.0
    assert idx == 0
    view.close()
    view.deleteLater()


def test_ruler_nice_steps(qapp: QtWidgets.QApplication) -> None:
    from nexcut.ui.canvas import _nice_step

    assert [_nice_step(x) for x in (0.7, 1.0, 1.1, 3.0, 7.0, 49.0, 0.013)] == [
        1.0,
        1.0,
        2.0,
        5.0,
        10.0,
        50.0,
        0.02,
    ]
    assert _nice_step(0.0) == 1.0
    view = CanvasView()
    r = RulerWidget(view, Qt.Orientation.Horizontal)
    view.resize(400, 300)
    view.zoom_to_fit()
    assert r.ticks()
    r.deleteLater()
    view.deleteLater()


def test_app_parser_and_bad_params(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from nexcut.ui import app

    args = app.build_parser().parse_args(["job.dxf", "--lang", "zh"])
    assert args.file == Path("job.dxf") and args.lang == "zh"
    assert app.main(["--manu", str(tmp_path / "missing.xml")]) == 2
    assert "nexcut" in capsys.readouterr().err


def test_app_main_runs_event_loop(
    src_dir: Path, tmp_path: Path, qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PySide6.QtCore import QTimer

    from nexcut.ui import app
    from nexcut.ui.i18n import set_translator

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(tmp_path))
    seen: list[str] = []

    def check_and_quit() -> None:
        wins = [
            w
            for w in QtWidgets.QApplication.topLevelWidgets()
            if isinstance(w, MainWindow) and w.isVisible()
        ]
        seen.extend(w.windowTitle() for w in wins)
        for w in wins:
            w.close()
        QtWidgets.QApplication.quit()

    QTimer.singleShot(300, check_and_quit)
    try:
        rc = app.main(
            [str(src_dir / "Graph/Work1/1.chf"), "--lang-txt", str(src_dir / "Lang/lang.txt")]
        )
    finally:
        set_translator(None)
    assert rc == 0
    assert any(t.startswith("1.chf") for t in seen)
    assert list((tmp_path / "cache" / "nexcut").glob("lang-*.json"))


def test_app_main_hands_the_parameter_paths_to_the_docks(
    tmp_path: Path, qapp: QtWidgets.QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--manu/--hard/--layer`` also name the file the M3 docks save *back* to.

    ``MainWindow(param_paths=...)`` is what ``ui/pages/param_pages.LayerFileBar`` and the four
    parameter pages use as the target of their Save button (02 §6.1); without it Save asks for
    a path the operator already gave on the command line.  Integration gap found when the M3
    pages landed: ``ui/app.py`` built the window without the mapping.
    """
    from PySide6.QtCore import QTimer

    from nexcut.io.params import default_document, write_params
    from nexcut.ui import app
    from nexcut.ui import main_window as mw

    paths = {}
    for key in ("manu", "hard", "layer"):
        p = tmp_path / f"Bk{key.capitalize()}Para.xml"
        write_params(p, default_document(key))
        paths[key] = p

    seen: dict[str, object] = {}

    class FakeWindow:
        def __init__(self, **kwargs: object) -> None:
            seen.update(kwargs)

        def show(self) -> None:
            # Quit from *inside* the loop: quit() before exec() would never return.
            QTimer.singleShot(0, lambda: QtWidgets.QApplication.quit())

    monkeypatch.setattr(mw, "MainWindow", FakeWindow)
    rc = app.main(
        [
            "--manu", str(paths["manu"]),
            "--hard", str(paths["hard"]),
            "--layer", str(paths["layer"]),
        ]  # fmt: skip
    )
    assert rc == 0
    assert seen["param_paths"] == paths
