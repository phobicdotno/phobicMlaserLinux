"""Golden tests of nexcut.io.chf against the 8 vendor ``.chf`` samples (analysis 03 §2, §11).

The samples are read (read-only) from SRC via the ``src_dir`` fixture and the
tests skip when SRC is absent.  Golden md5s and values are copied from 03 §2/§6.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from nexcut.io import chf
from nexcut.model import CircleGlyph, Contour, LwPolylineGlyph, SegmentGlyph

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "chf_parse.py"

# path under SRC -> (md5, size, version, graph count)  -- 03 §2
SAMPLES: dict[str, tuple[str, int, int, int]] = {
    "File/autosave.chf": ("d8e450b611500562dd382db62d54aa24", 10431, 5, 24),
    "File/Temp/tempGraph.chf": ("d8017d9eb5ff9f344a7c7a1b0bc82d4f", 500, 5, 1),
    "Graph/Work1/1.chf": ("8d6c0f39d7055f9d1b5962bb8b759745", 570, 4, 1),
    "Graph/Work1/2.chf": ("1f4fec8f91ffefbe4a265f3f238e13fd", 646, 4, 1),
    "Graph/Work1/3.chf": ("12aa1212ef473b03390e9d1cea21fd82", 718, 4, 1),
    "Graph/Work2/1.chf": ("1f4fec8f91ffefbe4a265f3f238e13fd", 646, 4, 1),
    "Graph/Work2/2.chf": ("12aa1212ef473b03390e9d1cea21fd82", 718, 4, 1),
    "Graph/Work2/3.chf": ("8d6c0f39d7055f9d1b5962bb8b759745", 570, 4, 1),
}


def _sample(src_dir: Path, rel: str) -> bytes:
    path = src_dir / rel
    if not path.is_file():
        pytest.skip(f"sample {rel} not present")
    data = path.read_bytes()
    md5, size, _, _ = SAMPLES[rel]
    assert len(data) == size and hashlib.md5(data).hexdigest() == md5, f"{rel} differs from 03 §2"
    return data


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("chf_parse_tool", TOOL)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("rel", sorted(SAMPLES))
def test_byte_identical_rewrite(src_dir: Path, rel: str) -> None:
    data = _sample(src_dir, rel)
    doc = chf.read_chf(data)
    _, _, version, ngraphs = SAMPLES[rel]
    assert doc.version == version and len(doc.graphs) == ngraphs
    assert chf.write_chf(doc) == data
    assert chf.read_chf(data, strict=False) == doc


@pytest.mark.parametrize("rel", sorted(SAMPLES))
def test_json_and_svg_match_reference_tool(src_dir: Path, rel: str, tool: ModuleType) -> None:
    data = _sample(src_dir, rel)
    doc = chf.read_chf(data)
    ref = tool.parse_chf(data)
    assert chf.to_tool_dict(doc) == ref
    assert chf.to_json(doc) == json.dumps(ref, indent=1, ensure_ascii=False)
    title = Path(rel).stem
    assert chf.to_svg(doc, title=title) == tool.to_svg(ref, title=title)


@pytest.mark.parametrize("rel", sorted(SAMPLES))
def test_cached_geometry_matches_recomputation(src_dir: Path, rel: str) -> None:
    assert chf.check_document(chf.read_chf(_sample(src_dir, rel))) == []


@pytest.mark.parametrize("rel", ["Graph/Work1/1.chf", "Graph/Work1/2.chf", "Graph/Work1/3.chf"])
def test_v4_converts_to_v5(src_dir: Path, rel: str) -> None:
    doc = chf.read_chf(_sample(src_dir, rel))
    v5 = chf.write_chf(doc, version=5)
    back = chf.read_chf(v5)
    assert back.version == 5
    back.version = 4
    assert back == doc
    assert b"\r\n\r\n" not in v5


def test_golden_values(src_dir: Path) -> None:
    auto = chf.read_chf(_sample(src_dir, "File/autosave.chf"))
    g1 = auto.graphs[0]
    assert isinstance(g1, Contour)
    assert g1.precision == 0.01 and g1.length == 284.266045
    assert g1.bbox_min == (395.33, 302.907) and g1.bbox_max == (677.258772, 339.284906)
    assert g1.layer == 0 and g1.int58 == 1
    assert isinstance(g1.elements[0].glyph, SegmentGlyph) and g1.elements[0].direction == 1
    cr = g1.crafts
    assert (cr.compensate_type, cr.compensate_width, cr.pwm_enable) == (-1, 0.0, 1)
    assert (cr.lead_line.type, cr.lead_line.angle_deg, cr.lead_line.length) == (0, 90.0, 5.0)
    assert cr.cool_pos == [] and auto.trailer_bool is False and auto.trailer_pt2 == (0.0, 0.0)

    w1 = chf.read_chf(_sample(src_dir, "Graph/Work1/1.chf")).graphs[0]
    assert isinstance(w1, Contour) and isinstance(w1.elements[0].glyph, CircleGlyph)
    assert w1.start == (7.116472, 13.54032) and w1.elements[0].glyph.center == (5.921416, 13.54032)

    w3 = chf.read_chf(_sample(src_dir, "Graph/Work1/3.chf")).graphs[0]
    assert isinstance(w3, Contour)
    poly = w3.elements[0].glyph
    assert isinstance(poly, LwPolylineGlyph) and poly.closed == 1
    assert [v.bulge for v in poly.vertices] == [0.0, -0.414214, -0.414214, 0.0, -0.414214, -0.414214]
    assert w3.length == 9.673958


def test_reference_tool_still_runs(src_dir: Path) -> None:
    path = src_dir / "File/Temp/tempGraph.chf"
    if not path.is_file():
        pytest.skip("sample not present")
    r = subprocess.run([sys.executable, str(TOOL), "--check", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "check: OK" in r.stdout


def test_module_cli(src_dir: Path, tmp_path: Path) -> None:
    path = src_dir / "Graph/Work1/3.chf"
    if not path.is_file():
        pytest.skip("sample not present")
    out = tmp_path / "re.chf"
    js = tmp_path / "d.json"
    assert chf.main([str(path), "--check", "--rewrite", str(out), "--json", str(js)]) == 0
    assert out.read_bytes() == path.read_bytes()
    assert json.loads(js.read_text(encoding="utf-8"))["version"] == 4
