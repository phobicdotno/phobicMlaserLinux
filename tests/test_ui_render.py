"""ui/render.py: offscreen renders vs the M2 reference renders (PORT-PLAN §4 M2 gate).

The gate says every ``Graph/Work*`` and ``File/autosave.chf`` renders
identically to ``tools/out/*.png``.  Pixel identity is not reachable with a
different line rasteriser (Qt vs the reference tool's integer DDA), so the
metric is: binarise (non-white), crop both images to their ink bounding box,
downscale onto a 64-cell grid (cell = any ink) and require IoU >= 0.9.
Negative controls prove the metric rejects a mirrored or incomplete drawing.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtGui import QImage  # noqa: E402

from nexcut.io import chf  # noqa: E402
from nexcut.model.graph import ChfDocument  # noqa: E402
from nexcut.ui import render  # noqa: E402
from nexcut.ui.render import (  # noqa: E402
    RenderOptions,
    ViewFlags,
    aligned_iou,
    ink_mask,
    render_document,
)
from nexcut.ui.scene import build_scene  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
REF_DIR = REPO / "tools" / "out"
IOU_MIN = 0.9
CELLS = 64

SAMPLES = {
    "File/autosave.chf": "File_autosave.png",
    "File/Temp/tempGraph.chf": "Temp_tempGraph.png",
    "Graph/Work1/1.chf": "Work1_1.png",
    "Graph/Work1/2.chf": "Work1_2.png",
    "Graph/Work1/3.chf": "Work1_3.png",
    "Graph/Work2/1.chf": "Work2_1.png",
    "Graph/Work2/2.chf": "Work2_2.png",
    "Graph/Work2/3.chf": "Work2_3.png",
}

VENDOR_DXF = "Testfile SS1mm 2.0s F+1 N2.dxf"


@pytest.fixture(scope="module", autouse=True)
def qapp() -> object:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _reference(name: str) -> QImage:
    path = REF_DIR / name
    if not path.is_file():
        pytest.skip(f"reference render {path} missing")
    img = QImage(str(path))
    assert not img.isNull()
    return img


@pytest.mark.parametrize("rel", sorted(SAMPLES))
def test_sample_matches_reference_render(src_dir: Path, rel: str, tmp_path: Path) -> None:
    ref = _reference(SAMPLES[rel])
    img = render_document(chf.load_chf(src_dir / rel))
    assert img.width() == ref.width() == 800
    # Temp_tempGraph.png is 800x800 while the current reference framing gives 799 (its
    # circle's sampled bbox is 0.0002 mm wider than tall): allow a couple of pixels.
    assert abs(img.height() - ref.height()) <= 2
    iou = aligned_iou(ink_mask(img), ink_mask(ref), CELLS)
    assert iou >= IOU_MIN, f"{rel}: IoU {iou:.3f}"
    out = tmp_path / "out.png"
    assert img.save(str(out), "PNG") and QImage(str(out)).size() == img.size()


def test_metric_rejects_mirror_and_missing_graphs(src_dir: Path) -> None:
    ref = ink_mask(_reference("File_autosave.png"))
    doc = chf.load_chf(src_dir / "File/autosave.chf")
    good = ink_mask(render_document(doc))
    assert aligned_iou(good, ref, CELLS) >= IOU_MIN
    assert aligned_iou(good[::-1, :], ref, CELLS) < IOU_MIN  # Y-down frame would fail
    half = ChfDocument(version=doc.version, graphs=doc.graphs[::2])
    assert aligned_iou(ink_mask(render_document(half)), ref, CELLS) < IOU_MIN
    assert aligned_iou(np.zeros_like(ref), ref, CELLS) == 0.0
    assert aligned_iou(np.zeros_like(ref), np.zeros_like(ref), CELLS) == 1.0


def _raster_reference_dxf(path: Path, width: int = 800, pad: int = 24) -> np.ndarray:
    """Independent reference: ezdxf's own path flattening, reference-tool framing and DDA."""
    import ezdxf
    from ezdxf import path as ezpath

    doc = ezdxf.readfile(path)
    polys = []
    for e in doc.modelspace():
        p = ezpath.make_path(e)
        pts = np.array([(v.x, v.y) for v in p.flattening(0.01)], dtype=float)
        if len(pts):
            polys.append(pts)
    allp = np.concatenate(polys)
    minx, miny = allp.min(axis=0)
    maxx, maxy = allp.max(axis=0)
    w, h = maxx - minx, maxy - miny
    scale = (width - 2 * pad) / max(w, h)
    height = int(h * scale) + 2 * pad
    mask = np.zeros((height, width), dtype=bool)
    for pts in polys:
        xs = np.rint(pad + (pts[:, 0] - minx) * scale).astype(int)
        ys = np.rint(height - pad - (pts[:, 1] - miny) * scale).astype(int)
        for x0, y0, x1, y1 in zip(xs[:-1], ys[:-1], xs[1:], ys[1:], strict=True):
            n = max(abs(x1 - x0), abs(y1 - y0), 1)
            t = np.arange(n + 1)
            mask[y0 + (y1 - y0) * t // n, x0 + (x1 - x0) * t // n] = True
    return mask


def test_vendor_dxf_matches_ezdxf_reference(src_dir: Path) -> None:
    p = src_dir.parent / VENDOR_DXF
    if not p.is_file():
        pytest.skip("vendor DXF not available")
    from nexcut.ui.loader import load_document

    ref = _raster_reference_dxf(p)
    loaded = load_document(p)  # importer + IGP gates, as File > Open does
    img = render_document(loaded.document)
    mask = ink_mask(img)
    assert mask.shape[1] == ref.shape[1] and abs(mask.shape[0] - ref.shape[0]) <= 2
    iou = aligned_iou(mask, ref, CELLS)
    assert iou >= IOU_MIN, f"IoU {iou:.3f}"


def test_reference_framing() -> None:
    f = render.reference_frame((10.0, 20.0, 110.0, 70.0), 800, 24)
    assert (f.width, f.height) == (800, int(50 * 7.52) + 48)
    t = f.transform()
    p = t.map(render.QPointF(10.0, 20.0))
    assert (p.x(), p.y()) == pytest.approx((24.5, f.height - 24 + 0.5))
    q = t.map(render.QPointF(110.0, 70.0))
    assert q.x() == pytest.approx(776.5) and q.y() == pytest.approx(f.height - 24 - 50 * 7.52 + 0.5)
    empty = render.reference_frame(None)
    assert empty.width == 800 and empty.height == 800


def test_markers_bed_and_selection_add_ink(src_dir: Path) -> None:
    doc = chf.load_chf(src_dir / "Graph/Work1/2.chf")
    base = ink_mask(render_document(doc)).sum()
    flags = ViewFlags(show_start=True, show_arrows=True, show_index=True, show_bed=False)
    marked = render_document(doc, RenderOptions(flags=flags))
    assert ink_mask(marked).sum() > base
    bed = render_document(
        doc,
        RenderOptions(flags=ViewFlags(False, False, False, False, True), bed=render.DEFAULT_BED),
    )
    assert bed.width() == 800 and bed.height() == int(900 * (752 / 1300)) + 48
    sel = render_document(
        doc, RenderOptions(selected=[0], style=render.PaintStyle(selection_color="#0000ff"))
    )
    arr = render.image_array(sel)
    assert ((arr[:, :, 2] > 200) & (arr[:, :, 0] < 50)).any()


def test_polygon_fast_path_roundtrip() -> None:
    arr = np.random.default_rng(1).random((1000, 2)) * 1000.0
    poly = render.polygon_from_array(arr)
    assert poly.size() == 1000
    for i in (0, 499, 999):
        assert (poly.at(i).x(), poly.at(i).y()) == (arr[i, 0], arr[i, 1])
    assert render.polygon_from_array(np.zeros((0, 2))).size() == 0


def test_point_glyph_cross(tmp_path: Path) -> None:
    from nexcut.model.glyph import PointGlyph, SegmentGlyph, Vec2
    from nexcut.ops.import_gates import build_contour

    doc = ChfDocument(
        graphs=[
            build_contour([SegmentGlyph(Vec2(0, 0), Vec2(100, 100))]),
            build_contour([PointGlyph(Vec2(50, 20))]),
        ]
    )
    scene = build_scene(doc)
    img = render.render_scene(scene)
    arr = render.image_array(img)
    x, y = round(24 + 50 * 7.52), round(img.height() - 24 - 20 * 7.52)
    assert (
        (arr[y, x] != 255).any() and (arr[y, x - 2] != 255).any() and (arr[y - 2, x] != 255).any()
    )


def test_render_cli(src_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "w.png"
    assert render.main([str(src_dir / "Graph/Work1/3.chf"), "-o", str(out)]) == 0
    img = QImage(str(out))
    ref = _reference("Work1_3.png")
    assert aligned_iou(ink_mask(img), ink_mask(ref), CELLS) >= IOU_MIN
    from nexcut.ui.app import main as nexcut_main

    out2 = tmp_path / "w2.png"
    rc = nexcut_main(
        [
            "render",
            str(src_dir / "File/autosave.chf"),
            "-o",
            str(out2),
            "--start",
            "--arrows",
            "--index",
            "--bed",
            "--width",
            "400",
        ]
    )
    assert rc == 0 and QImage(str(out2)).width() == 400
    assert render.main([str(tmp_path / "nope.chf"), "-o", str(tmp_path / "x.png")]) == 1
    assert "nexcut render" in capsys.readouterr().err
