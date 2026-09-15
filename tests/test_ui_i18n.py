"""ui/i18n.py: lang.txt conversion, JSON cache, fallback chain (06 §2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexcut.ui import i18n
from nexcut.ui.i18n import LangCatalog, Translator, load_catalog, parse_lang_txt
from nexcut.ui.layers import LAYER_COUNT, layer_name


def _lang_bytes(lines: list[str]) -> bytes:
    return "\r\n".join(lines).encode("utf-16")  # Python's utf-16 codec writes the FF FE BOM


SAMPLE = [
    "0_SongFont#宋体#Arial",
    "",
    "mf7#文件#File",
    "mf149#支持的加工文件(*.dxf;*.chf;*plt)|*.dxf;*.chf;*.plt||#Supported File Types(*.dxf;*.chf;*plt)|*.dxf;*.chf;*.plt||",
    "pd485#杂项.启用标尺#Misc.Enable Ruler",
    "pd1001#停止交换#Stop Exchange",
    "pd1001#一级穿孔基础.穿孔高度#Third Drill Basic.Third Drill Height",
    "mv24, ,##",
    "broken line without separators",
]


def test_parse_records_and_duplicates() -> None:
    data = _lang_bytes(SAMPLE)
    assert data[:2] == b"\xff\xfe"
    cat = parse_lang_txt(data)
    assert cat.get("0_SongFont") == "Arial"
    assert cat.get("0_SongFont", "zh") == "宋体"
    assert cat.get("mf7") == "File"
    assert cat.get("pd1001") == "Stop Exchange"  # first record kept (UNVERIFIED rule)
    assert cat.duplicates == ["pd1001"]
    assert cat.get("mv24, ,") == ""
    assert cat.get("missing") is None
    assert len(cat) == 6


def test_translator_fallback_chain() -> None:
    t = Translator(parse_lang_txt(_lang_bytes(SAMPLE)))
    assert t.tr("mf7", "Datei") == "File"
    assert t.tr("nope", "Default") == "Default"
    assert t.tr("nope") == "nope"
    assert t.tr("mv24, ", "fallback") == "fallback"  # empty text falls through
    assert t.item("pd485") == "Enable Ruler"
    assert t.file_filter("mf149", "") == "Supported File Types (*.dxf *.chf *.plt)"
    assert Translator(parse_lang_txt(_lang_bytes(SAMPLE)), "zh").tr("mf7") == "文件"
    with pytest.raises(ValueError):
        Translator(None, "de")
    empty = Translator()
    assert empty.tr("mf7") == "mf7"


def test_json_cache_written_and_reused(tmp_path: Path) -> None:
    src = tmp_path / "lang.txt"
    src.write_bytes(_lang_bytes(SAMPLE))
    env = {"XDG_CACHE_HOME": str(tmp_path / "cache")}
    cat = load_catalog(src, env=env)
    files = list((tmp_path / "cache" / "nexcut").glob("lang-*.json"))
    assert len(files) == 1
    blob = json.loads(files[0].read_text(encoding="utf-8"))
    assert blob["format"] == i18n.CACHE_FORMAT
    assert blob["entries"]["mf7"] == ["文件", "File"]
    assert cat.sha256 == blob["sha256"]
    # the cache is what gets read the second time
    blob["entries"]["mf7"] = ["文件", "File (cached)"]
    files[0].write_text(json.dumps(blob), encoding="utf-8")
    assert load_catalog(src, env=env).get("mf7") == "File (cached)"
    # an edited source changes the digest -> re-converted
    src.write_bytes(_lang_bytes([*SAMPLE, "mf8#新建#New"]))
    again = load_catalog(src, env=env)
    assert again.get("mf7") == "File" and again.get("mf8") == "New"
    assert len(list((tmp_path / "cache" / "nexcut").glob("lang-*.json"))) == 2
    # corrupt cache is ignored and rebuilt
    for f in (tmp_path / "cache" / "nexcut").glob("lang-*.json"):
        f.write_text("{not json", encoding="utf-8")
    assert load_catalog(src, env=env).get("mf8") == "New"


def test_cache_dir_and_default_path(tmp_path: Path) -> None:
    assert i18n.cache_dir({"XDG_CACHE_HOME": "/x/y"}) == Path("/x/y/nexcut")
    assert i18n.cache_dir({}) == Path.home() / ".cache" / "nexcut"
    lang = tmp_path / "Lang" / "lang.txt"
    lang.parent.mkdir()
    lang.write_bytes(_lang_bytes(SAMPLE))
    assert i18n.default_lang_path({"NEXCUT_SRC": str(tmp_path)}) == lang
    assert i18n.default_lang_path({"NEXCUT_LANG_TXT": str(lang)}) == lang
    assert i18n.default_lang_path({}) is None
    assert i18n.default_lang_path({"NEXCUT_SRC": str(tmp_path / "missing")}) is None


def test_global_translator(tmp_path: Path) -> None:
    i18n.set_translator(Translator(parse_lang_txt(_lang_bytes(SAMPLE))))
    try:
        assert i18n.tr("mf7") == "File"
        assert i18n.get_translator().tr("xx", "d") == "d"
    finally:
        i18n.set_translator(None)


def test_layer_names_fallback_and_catalog() -> None:
    t = Translator(LangCatalog())
    assert [layer_name(i, t) for i in (0, 1, 9, 10)] == [
        "Bk Layer",
        "Layer1",
        "Layer9",
        "Film Layer",
    ]
    assert LAYER_COUNT == 11


def test_vendor_lang_txt(src_dir: Path, tmp_path: Path) -> None:
    path = src_dir / "Lang" / "lang.txt"
    cat = load_catalog(path, env={"XDG_CACHE_HOME": str(tmp_path)})
    assert len(cat) == 3427  # 3 431 records, 4 duplicated ids (06 §2 + re-count)
    assert sorted(set(cat.duplicates)) == ["A241224_0", "A250616_0", "gp100", "pd1001"]
    t = Translator(cat)
    assert t.tr("mf7") == "File"
    assert t.tr("mf9") == "Open"
    assert t.tr("mf26") == "View"
    assert t.tr("mf40") == "Show index"
    assert t.tr("mf41") == "Show path start"
    assert t.tr("mf42") == "Show path direction"
    assert t.item("pd485") == "Enable Ruler"
    assert t.tr("mf650") == "Untitled-"
    assert (
        t.file_filter("mf149", "")
        == "Supported File Types (*.dxf *.chf *.nc *.txt *.cnc *.g *.plt)"
    )
    assert (
        t.tr("mv17") % (1.0, 2.0, 3.0)
        == "Measurement Result  X: 1.000mm  Y: 2.000mm  Length: 3.000mm"
    )
    assert [layer_name(i, t) for i in (0, 3, 10)] == ["Bk Layer", "Layer3", "Film Layer"]
    assert Translator(cat, "zh").tr("gp81") == "背景图层"
