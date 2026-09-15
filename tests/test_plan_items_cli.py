"""``python -m nexcut.plan``: .chf -> frame file, dry run only (PORT-PLAN §8, 11 §5)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from nexcut.io.chf import save_chf
from nexcut.io.params import ParamDocument, default_document, write_params
from nexcut.mcc.dissector import parse_fifo_words
from nexcut.mcc.fifo import read_frame_file
from nexcut.mcc.safety import strip_laser_records
from nexcut.model.glyph import CircleGlyph, SegmentGlyph, Vec2
from nexcut.model.graph import ChfDocument, Contour, ContourElement
from nexcut.plan.__main__ import build_job, main
from nexcut.plan.items import UnsupportedConfiguration


def put(doc: ParamDocument, key: str, value: float | int | str) -> None:
    elem, attr = key.split(".", 1)
    for group, elems in doc.values.items():
        if elem in elems and attr in elems[elem]:
            doc.set(group, elem, attr, value)
            return
    raise KeyError(key)


def machine_docs() -> tuple[ParamDocument, ParamDocument, ParamDocument]:
    """Descriptor defaults with this CF1390's values where the stream depends on them."""
    manu, hard, layer = (
        default_document("manu"),
        default_document("hard"),
        default_document("layer"),
    )
    put(manu, "SP.m_iEnableLaserType", 1)
    for key, val in {
        "ZF.ZFType": 1, "LGP.CO2DOLaser": 9, "LGP.CO2LaserControlType": 2, "MGP.HighAir": 3,
        "MAC.SpeedRatio": 31.003, "MAC.WritePluse": 8000, "MAC_1.SpeedRatio": 31.009,
        "MAC_1.WritePluse": 8000, "AX.InterpolationCycle": 250,
    }.items():  # fmt: skip
        put(hard, key, val)
    group = next(g for g in layer.values if g.endswith("CO2LayerParam2"))
    for attr, val in {"CutSpeed": 50.0, "CutDuty": 4, "CutFreq": 5000, "CutGasType": 3}.items():
        layer.set(group, "GP", attr, val)
    return manu, hard, layer


def job_doc() -> ChfDocument:
    square = [((0, 0), (20, 0)), ((20, 0), (20, 20)), ((20, 20), (0, 20)), ((0, 20), (0, 0))]
    c1 = Contour(
        elements=[ContourElement(SegmentGlyph(Vec2(*a), Vec2(*b))) for a, b in square], layer=1
    )
    c2 = Contour(elements=[ContourElement(CircleGlyph(Vec2(40.0, 10.0), 5.0))], layer=1)
    return ChfDocument(graphs=[c1, c2])


def _items(frames: list[tuple[int, list[int]]]) -> list[tuple[int, tuple[int, ...]]]:
    return [
        (it.opcode, it.args) for fid, data in frames for it in parse_fifo_words([fid, *data]).items
    ]


def test_build_job_dry_run_stream() -> None:
    manu, hard, layer = machine_docs()
    job = build_job(job_doc(), manu, hard, layer)
    assert job.contours == 2 and job.cycle_us == 250
    # square corners are blended by smoothGly (05 §6.2), so the path is a little shorter
    assert 80.0 + 10.0 * 3.141 - 1.0 < job.cut_length_mm < 80.0 + 10.0 * 3.1416
    frames = [(i + 1, list(f.data)) for i, f in enumerate(job.frames)]
    for fid, data in frames:
        out, changed = strip_laser_records([fid, *data])
        assert changed == 0  # nothing laser-related in a dry run
    items = _items(frames)
    ops = [op for op, _ in items]
    assert ops[:3] == [3001, 3002, 3000] and ops.count(103) == 2 and ops.count(109) == 2
    assert (9999, (2, 0x100, 0x100)) not in items
    assert items.count((9999, (2, 4, 4))) == 2 and items[-1] == (9999, (2, 4, 0))  # gas off last
    assert all(args[1] & 0xFFFF == 0 for op, args in items if op == 3000)
    assert job.duration_s == pytest.approx(job.ticks * 0.00025)
    assert all(len(d) <= 304 for _, d in frames) and all(len(d) >= 296 for _, d in frames[:-1])


def test_build_job_laser_records_and_refusals() -> None:
    manu, hard, layer = machine_docs()
    job = build_job(job_doc(), manu, hard, layer, laser_records=True)
    items = _items([(i, list(f.data)) for i, f in enumerate(job.frames)])
    assert items.count((9999, (2, 0x100, 0x100))) == 2
    assert any(op == 3000 and args[1] == 0x13880004 for op, args in items)  # 5000 Hz / 4 %
    assert sum(f.laser_items for f in job.frames) > 0
    put(manu, "SP.m_iEnableLaserType", 0)
    with pytest.raises(UnsupportedConfiguration):
        build_job(job_doc(), manu, hard, layer)


def test_cli_writes_frames_and_never_sends(tmp_path: Path) -> None:
    manu, hard, layer = machine_docs()
    for name, doc in (
        ("BkManuPara.xml", manu),
        ("BkHardPara.xml", hard),
        ("BkLayerPara.xml", layer),
    ):
        write_params(tmp_path / name, doc)
    save_chf(job_doc(), tmp_path / "job.chf")
    out = tmp_path / "frames.txt"
    rc = main([
        str(tmp_path / "job.chf"), "--layer-xml", str(tmp_path / "BkLayerPara.xml"),
        "--hard-xml", str(tmp_path / "BkHardPara.xml"), "-o", str(out), "--first-frame-id", "0x39",
    ])  # fmt: skip
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# nexcut.plan dry run") and "NOT sent" in text
    frames = read_frame_file(out)
    assert frames[0][0] == 0x39 and [f for f, _ in frames] == list(range(0x39, 0x39 + len(frames)))
    assert main(["missing.chf", "--layer-xml", "x", "--hard-xml", "y", "-o", str(out)]) == 2


def test_cli_imports_no_transport() -> None:
    code = (
        "import sys, nexcut.plan.__main__ as m; "
        "bad = [n for n in ('nexcut.mcc.transaction', 'nexcut.mcc.safety', 'nexcut.mccd') "
        "if n in sys.modules]; print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_cli_on_vendor_job(src_dir: Path, tmp_path: Path) -> None:
    f = src_dir / "File"
    out = tmp_path / "auto.txt"
    rc = main([
        str(f / "autosave.chf"), "--layer-xml", str(f / "BkLayerPara.xml"),
        "--hard-xml", str(f / "BkHardPara.xml"), "-o", str(out),
    ])  # fmt: skip
    assert rc == 0
    frames = read_frame_file(out)
    items = _items(frames)
    assert sum(1 for op, _ in items if op == 103) == 24  # 24 line contours of autosave.chf
    for fid, data in frames:
        assert strip_laser_records([fid, *data])[1] == 0
