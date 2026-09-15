"""Vendor parameter XML round-trip and primary/backup tests (01 §0.1/§0.3/§5, 02 §2.1/§6.2).

Round-trip tests read the original files from SRC (read-only, skipped when absent) and
assert (a) %.17g equivalence of every attribute and (b) byte identity. The expected
file hashes below are golden copies taken when the tests were written.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from nexcut.io.params import (
    FILE_SETS,
    ParamFileError,
    ParamStore,
    apply_preset,
    default_document,
    escape_attribute,
    list_technology,
    parse_params,
    parse_technology,
    preset_from_layer,
    read_params,
    serialize_params,
    serialize_technology,
    technology_dir,
    write_technology,
)

SRC_FILES = {
    # name: (kind, size, sha256)
    "BkHardPara.xml": (
        "hard",
        10219,
        "8c3724f2f8586b3d55912ca2b5278790c12315a8bfa2c6be50770d3ffc3c3b3d",
    ),
    "BkManuPara.xml": (
        "manu",
        8243,
        "826337510515476288128c0b9053e2f95a82e1103a26e6930cfd0d3382265909",
    ),
    "SecondBkManuPara.xml": (
        "manu",
        8243,
        "0b94027cd96f0cd975f0777ced7c58999690a0da5a72101c3fcb2fb41631f403",
    ),
    "BkLayerPara.xml": (
        "layer",
        44148,
        "343839f13b3fdd5e5ffe66b04634c3109a36b7a65edfd600c921a01184cd6fee",
    ),
}
PRESETS = {
    # path below PKG/"Cutting parameters": (laser, sha256, missing attributes)
    "1200W/Carbon steel/Carbon 10.0mm  4.0D F+12 O2.xml": (
        "fiber",
        "0e32aa6fb39bcdce4867a6d66a5d77928936e963e95427e963db4a023a06af02",
        [],
    ),
    "1200W/Stainless steel/SS2.0mm 1.5S F-3N2.xml": (
        "fiber",
        "991401bc68cb6e166ba77880eb4802cf6ba236bf1a871c55fce8e0c422a9f79c",
        [],
    ),
    "CO2/Acrylic/Acrylic 24mm air.xml": (
        "co2",
        "7f579fa2ba365fe82a036d580bbbca9a7fab6da1c7333c93eb0794cb0632efb5",
        ["PCO2LayerParam11/GP.CutFreq"],
    ),
}
BACKUP_1390 = (
    "1390backup.xml",
    62310,
    "76a47aac1072ed83e2889d9b8fb3e01617bc0eae6e5d7181b05fb9ab467a648f",
)

_LINE = re.compile(r'^<(/?)([\w]+)((?: \w+="[^"]*")*)(/?)>$')
_ATTR = re.compile(r' (\w+)="([^"]*)"')


def assert_equivalent(out: bytes, ref: bytes) -> None:
    """Same structure, names and order; attribute texts equal or equal as %.17g doubles."""
    assert out.endswith(b"\r\n") and ref.endswith(b"\r\n")
    assert b"\n" not in out.replace(b"\r\n", b"")
    a, b = out.decode("utf-8").split("\r\n"), ref.decode("utf-8").split("\r\n")
    assert len(a) == len(b)
    for la, lb in zip(a, b, strict=True):
        if la == lb:
            continue
        ma, mb = _LINE.match(la), _LINE.match(lb)
        assert ma and mb, (la[:80], lb[:80])
        assert ma.group(1, 2, 4) == mb.group(1, 2, 4)
        aa, ab = _ATTR.findall(ma.group(3)), _ATTR.findall(mb.group(3))
        assert [n for n, _ in aa] == [n for n, _ in ab]
        for (name, va), (_, vb) in zip(aa, ab, strict=True):
            if va != vb:
                assert float(va) == float(vb), (name, va, vb)
                assert format(float(vb), ".17g") == va, (name, va, vb)


def _golden(path: Path, size: int | None, sha: str) -> bytes:
    data = path.read_bytes()
    if size is not None:
        assert len(data) == size
    assert hashlib.sha256(data).hexdigest() == sha, f"{path} differs from the analysed copy"
    return data


@pytest.fixture(scope="module")
def pkg_dir(src_dir: Path) -> Path:
    return src_dir.parent


@pytest.mark.parametrize("name", list(SRC_FILES))
def test_src_file_round_trip(src_dir: Path, name: str) -> None:
    kind, size, sha = SRC_FILES[name]
    raw = _golden(src_dir / "File" / name, size, sha)
    doc = parse_params(raw, kind)
    assert doc.report.clean, doc.report
    out = serialize_params(doc)
    assert_equivalent(out, raw)
    assert out == raw


def test_system_backup_round_trip(pkg_dir: Path) -> None:
    name, size, sha = BACKUP_1390
    path = pkg_dir / name
    if not path.exists():
        pytest.skip("1390backup.xml not present")
    raw = _golden(path, size, sha)
    doc = parse_params(raw, "system_backup")
    assert doc.report.clean
    assert serialize_params(doc) == raw


@pytest.mark.parametrize("rel", list(PRESETS))
def test_technology_round_trip(pkg_dir: Path, rel: str) -> None:
    laser, sha, missing = PRESETS[rel]
    path = pkg_dir / "Cutting parameters" / rel
    if not path.exists():
        pytest.skip("vendor technology library not present")
    raw = _golden(path, None, sha)
    preset = parse_technology(raw)
    assert preset.laser == laser
    assert preset.report.missing == missing
    out = serialize_technology(preset)
    if missing:
        # 02 §6.2: the writer emits every registered attribute; CutFreq takes its default.
        assert preset.values["CutFreq"] == 5000
        expected = raw.replace(
            b'"/>\r\n</PCO2LayerParam11>', b'" CutFreq="5000"/>\r\n</PCO2LayerParam11>'
        )
        assert expected != raw
    else:
        expected = raw
    assert_equivalent(out, expected)
    assert out == expected


def test_all_vendor_presets_load(pkg_dir: Path) -> None:
    files = sorted((pkg_dir / "Cutting parameters").rglob("*.xml"))
    if not files:
        pytest.skip("vendor technology library not present")
    assert len(files) == 53  # 02 §1 verifier count
    lasers = {"fiber": 0, "co2": 0}
    for f in files:
        raw = f.read_bytes()
        p = parse_technology(raw)
        lasers[p.laser] += 1
        out = serialize_technology(p)
        assert out == raw or p.report.missing == ["PCO2LayerParam11/GP.CutFreq"], f
    assert lasers == {"fiber": 40, "co2": 13}


def test_values_are_typed(src_dir: Path) -> None:
    doc = read_params(src_dir / "File" / "BkLayerPara.xml", "layer")
    slot1 = doc.layer_slot("fiber", 1)
    assert slot1["CutSpeed"] == 11.000000000000002 and isinstance(slot1["ManuType"], int)
    assert doc.layer_slot("co2", 1)["LayerFileName"] == "亚克力24mm"
    manu = read_params(src_dir / "File" / "BkManuPara.xml", "manu")
    assert manu.get("PManuParam", "MSC", "LatestFilePath") == 'L""'  # &quot; decoded


# ------------------------------------------------------------------ synthetic tests
def test_missing_and_unknown_attributes() -> None:
    doc = default_document("hard")
    raw = serialize_params(doc)
    cut = raw.replace(b' MaxLength="1500"', b"", 1).replace(b"<AX ", b'<AX Bogus="1" ', 1)
    back = parse_params(cut, "hard")
    assert back.report.missing == ["PAxisParam/A0.MaxLength"]
    assert back.report.unknown == ["PAxisParam/AX.Bogus"]
    assert back.get("PAxisParam", "A0", "MaxLength") == 1500.0
    assert serialize_params(back) == raw


def test_set_and_format() -> None:
    doc = default_document("layer")
    doc.set("PLayerParam3", "GP", "CutSpeed", 0.1)
    doc.set("PLayerParam3", "GP", "Note", 'a&b<"c">\t')
    out = serialize_params(doc)
    assert b'CutSpeed="0.10000000000000001"' in out
    # CMarkup x_EscapeText (ParaModule 0x10004cb0): only <&>'" are escaped, TAB stays raw.
    assert b'Note="a&amp;b&lt;&quot;c&quot;&gt;\t"' in out
    back = parse_params(out, "layer")
    assert back == doc
    with pytest.raises(ValueError):
        doc.set("PLayerParam3", "GP", "ManuType", 1.5)
    assert doc.set("PLayerParam3", "GP", "ManuType", 9)  # warns: outside range / no label


def test_escape_attribute() -> None:
    assert escape_attribute('L""') == "L&quot;&quot;"


@pytest.mark.parametrize("bad", [b"", b"<ParameterRoot>", b"<Other/>\r\n", b"\x00garbage"])
def test_bad_files_raise(bad: bytes) -> None:
    with pytest.raises(ParamFileError):
        parse_params(bad, "manu")


def test_technology_wrapper_and_layer_transfer(tmp_path: Path) -> None:
    layer = default_document("layer")
    layer.set("PLayerParam4", "GP", "CutSpeed", 42.5)
    layer.set("PLayerParam4", "GP", "LayerFileName", "Carbon 3.0mm")
    preset = preset_from_layer(layer, "fiber", 4)
    data = serialize_technology(preset)
    assert data.startswith(b"<ParameterRoot>\r\n<PLayerParam11>\r\n<GP NoManu=")
    assert data.endswith(b"/>\r\n</PLayerParam11>\r\n</ParameterRoot>\r\n")
    # a wrapper with another slot number is accepted and re-written as slot 11
    other = data.replace(b"PLayerParam11", b"PLayerParam2")
    p2 = parse_technology(other)
    assert p2.source_group == "PLayerParam2" and serialize_technology(p2) == data
    target = default_document("layer")
    apply_preset(target, p2, 9)
    assert target.get("PLayerParam9", "GP", "CutSpeed") == 42.5
    assert target.get("PLayerParam9", "GP", "LayerFileName") == "Carbon 3.0mm"
    apply_preset(target, p2, 9, set_layer_file_name="x")
    assert target.get("PLayerParam9", "GP", "LayerFileName") == "x"
    d = technology_dir(tmp_path, "co2")
    assert d == tmp_path / "Technology" / "CO2"
    write_technology(technology_dir(tmp_path, "fiber") / "b.xml", preset)
    write_technology(technology_dir(tmp_path, "fiber") / "a.xml", preset)
    assert [p.name for p in list_technology(tmp_path, "fiber")] == ["a.xml", "b.xml"]
    assert list_technology(tmp_path, "co2") == []


# ------------------------------------------------------------ primary/backup strategy
def _manu(value: int) -> bytes:
    doc = default_document("manu")
    doc.set("PManuParam", "LMP", "LoopCount", value)
    return serialize_params(doc)


def test_store_fallback_chain(tmp_path: Path) -> None:
    primary, backup = tmp_path / "NexCut", tmp_path / "File"
    primary.mkdir()
    backup.mkdir()
    (primary / "ManuPara.xml").write_bytes(b"<ParameterRoot><PManu")  # truncated
    (backup / "BkManuPara.xml").write_bytes(b"")  # empty
    (backup / "SecondBkManuPara.xml").write_bytes(_manu(7))
    store = ParamStore(primary, backup)
    res = store.load("manu")
    assert res.source == backup / "SecondBkManuPara.xml"
    assert res.document.get("PManuParam", "LMP", "LoopCount") == 7
    assert [p for p, _ in res.failures] == [primary / "ManuPara.xml", backup / "BkManuPara.xml"]
    assert (backup / "ErrorManuPara.xml").read_bytes() == b"<ParameterRoot><PManu"
    assert (backup / "ErrorBkManuPara.xml").read_bytes() == b""
    assert res.from_backup


def test_store_defaults_when_nothing_readable(tmp_path: Path) -> None:
    res = ParamStore(tmp_path / "a", tmp_path / "b").load("layer")
    assert res.source is None and res.document.is_default() and len(res.failures) == 2
    assert not (tmp_path / "b").exists()


def test_store_save(tmp_path: Path) -> None:
    primary, backup = tmp_path / "NexCut", tmp_path / "File"
    store = ParamStore(primary, backup)
    assert FILE_SETS["manu"].backups == ("BkManuPara.xml", "SecondBkManuPara.xml")
    first = parse_params(_manu(1), "manu")
    assert store.save(first)
    assert (primary / "ManuPara.xml").read_bytes() == _manu(1)
    assert (backup / "BkManuPara.xml").read_bytes() == _manu(1)
    assert not (backup / "SecondBkManuPara.xml").exists()
    assert store.save(parse_params(_manu(2), "manu"))
    assert (backup / "BkManuPara.xml").read_bytes() == _manu(2)
    assert (backup / "SecondBkManuPara.xml").read_bytes() == _manu(1)
    assert store.load("manu").source == primary / "ManuPara.xml"
    assert list(primary.glob("*.tmp")) == []


def test_store_refuses_default_hard(tmp_path: Path) -> None:
    store = ParamStore(tmp_path / "p", tmp_path / "b")
    doc = default_document("hard")
    assert store.save(doc) is False
    assert not (tmp_path / "p" / "HardPara.xml").exists()
    doc.set("PAxisParam", "A0", "MaxLength", 1371.0)
    assert store.save(doc) is True
    assert read_params(tmp_path / "b" / "BkHardPara.xml", "hard") == doc
