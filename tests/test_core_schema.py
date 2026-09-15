"""Parameter descriptor schema tests (01 §0.2, §1; 02 §2.2, §2.3, §6.2).

Golden numbers are copied from the analysis docs and from the generator run on
MainApp.exe v0.0.0.52; nothing here reads the vendor package.
"""

from __future__ import annotations

import math
from collections import Counter

import pytest

from nexcut.core.schema import SchemaError, TypeCode, format_double, load_schema, parse_double

# 01 §0.2 verifier histogram of type codes over the 1 001 bound descriptors.
TYPE_HISTOGRAM = {
    1: 216,
    2: 289,
    3: 29,
    4: 140,
    5: 198,
    6: 46,
    7: 44,
    8: 9,
    9: 5,
    10: 3,
    11: 3,
    12: 14,
    13: 3,
    14: 2,
}
TABLE_COUNTS = {
    "M0": 81,
    "LAYER": 164,
    "CO2LAYER": 21,
    "HARD": 449,
    "GRAPH": 200,
    "GRAPH_X": 3,
    "IMPORT": 33,
    "ZFEC": 11,
    "AF": 9,
    "SOFT10": 10,
    "SOFT8": 8,
    "STRESSTEST": 7,
    "LASERTEST": 5,
}
# 01 §1.1 (446), §1.2 (331); 02 §2.1 (11x164 + 11x21); 1390backup.xml (sum); 02 §6.2.
LAYOUT_ATTRS = {
    "hard": 446,
    "manu": 331,
    "layer": 2035,
    "system_backup": 2812,
    "technology_fiber": 164,
    "technology_co2": 21,
}
HARD_GROUPS = [
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
]
MANU_GROUPS = [
    "PManuParam",
    "PSoftParam",
    "PAFParam",
    "PDOParam",
    "PZFParam",
    "PECParam",
    "PGraphParam",
    "PNestParam",
    "PImportGraphParam",
]


@pytest.fixture(scope="module")
def schema():
    return load_schema()


def test_record_counts(schema):
    assert len(schema.descriptors) == 1001
    assert schema.raw["counts"]["bound_descriptors"] == 1001
    assert len(schema.unbound) == 147
    assert {k: len(v) for k, v in schema.tables.items()} == TABLE_COUNTS


def test_type_histogram(schema):
    assert dict(Counter(d.type_code for d in schema.descriptors)) == TYPE_HISTOGRAM


def test_remote_type_descriptor(schema):
    # 01 §0.2 worked example at 0x785875..0x7858d4
    d = schema.get("SoftParam", "SOP.RemoteType")
    assert (d.label_id, d.value_offset, d.type_code, d.default_text) == ("pd292", 0x4028, 4, "0")
    assert (d.min, d.max) == (0.0, 1.0)
    assert d.enum_ids == ("pd178", "pd179", "pd180")
    assert d.table == "HARD" and d.storage == "int" and d.type_name == "enum"


def test_layer_descriptors(schema):
    # 02 §2.2 / §2.5
    cut = schema.get("LayerParam", "GP.CutSpeed")
    assert (cut.unit, cut.default, cut.label_id, cut.storage) == ("mm/s", 100.0, "pd125", "double")
    manu = schema.get("LayerParam", "GP.ManuType")
    assert manu.enum_ids == ("pd810", "pd811", "pd812", "pd813")
    freq = schema.get("CO2LayerParam", "GP.CutFreq")
    assert (freq.default, freq.unit, freq.type_code) == (5000, "Hz", TypeCode.INT)
    assert schema.get("LayerParam", "GP.FocusGradualTime2").label_id == "pd934"
    assert schema.get("CO2LayerParam", "GP.LayerFileName").label_id == "pd154"


def test_limit_label_swap_is_kept(schema):
    # 01 §1 verifier caveat: attribute name and label contradict each other
    assert (
        schema.get("MachineAxisConfig_0", "MAC.NegativeLimitInput").label_id == "EtherAxisInfos_8"
    )
    assert schema.get("MachineAxisConfig_0", "MAC.ForwardLimitInput").label_id == "EtherAxisInfos_9"


def test_layouts(schema):
    for kind, n in LAYOUT_ATTRS.items():
        assert schema.layout(kind).attribute_count == n, kind
    assert [g.name for g in schema.layout("hard").groups] == HARD_GROUPS
    assert [g.name for g in schema.layout("manu").groups] == MANU_GROUPS
    layer = [g.name for g in schema.layout("layer").groups]
    assert layer == [f"PLayerParam{i}" for i in range(1, 12)] + [
        f"PCO2LayerParam{i}" for i in range(1, 12)
    ]
    assert [g.name for g in schema.layout("technology_co2").groups] == ["PCO2LayerParam11"]
    home = schema.layout("hard").group("PHomeParam")
    assert [e.name for e in home.elements] == ["HP", "A0", "A1", "HPA3"]
    co2 = schema.layout("technology_co2").groups[0].element("GP")
    assert co2.attributes[-1].attribute == "CutFreq"  # 02 §2.3 row 21 (last)


def test_every_default_decodes(schema):
    for d in schema.descriptors:
        value, exact = d.parse(d.default_text)
        if (d.section, d.key) == ("DOParam", "DO.CurrentAdjGasType"):
            assert (value, exact) == (0, False)  # empty default text in the binary
        else:
            assert exact, d.key


def test_duplicates_recorded(schema):
    keys = {a.key for a, _ in schema.layout("manu").duplicates}
    assert {"GRP.OffsetType", "LMP.LoopCount", "GRP.EnableBatchCut"} <= keys
    a, b = next((a, b) for a, b in schema.layout("manu").duplicates if a.key == "GRP.OffsetType")
    assert a.value_offset != b.value_offset


def test_validate(schema):
    d = schema.get("LayerParam", "GP.CutSpeed")
    assert d.validate(100.0) == []
    assert d.validate(0.0) and d.validate("x")
    e = schema.get("SoftParam", "SOP.RemoteType")
    assert any("enum" in p for p in e.validate(3))


def test_unknown_lookups(schema):
    with pytest.raises(SchemaError):
        schema.get("SoftParam", "SOP.Nope")
    with pytest.raises(SchemaError):
        schema.layout("nope")


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0.6, "0.59999999999999998"),
        (11.000000000000002, "11.000000000000002"),
        (3.0, "3"),
        (float("-45.618105279627308"), "-45.618105279627308"),
        (1500.0, "1500"),
        (1e-20, "9.9999999999999995e-021"),
        (1.5e300, "1.5000000000000001e+300"),
    ],  # exponent style UNVERIFIED (MSVCR100)
)
def test_format_double(value, text):
    assert format_double(value) == text
    assert parse_double(text)[0] == value


def test_parse_double_c_rules():
    assert parse_double("1.#INF")[0] == math.inf
    assert parse_double("12abc") == (12.0, False)
    assert parse_double("") == (0.0, False)
