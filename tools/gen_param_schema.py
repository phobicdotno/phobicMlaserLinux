#!/usr/bin/env python3
"""gen_param_schema.py -- regenerate ``src/nexcut/core/schema.json`` from MainApp.exe.

Development-only tool (never imported by the package). It re-extracts the
parameter descriptor table that MainApp.exe v0.0.0.52 builds in its static
initialisers (01 §0.2, 02 §2.2) and writes it as JSON together with the table
registry and the XML file layouts.

Evidence used (all VAs are MainApp.exe, image base 0x400000):

* Descriptor record = 0xd0 bytes in static arrays in ``.bss`` (0xa3b420..0xa77610).
  Field offsets inside a record R (recovered from the initialiser at
  0x785875..0x78590d, ``SOP.RemoteType``):

  ====== ============================================================
  R+0x00 std::wstring section        (e.g. L"SoftParam")
  R+0x1c std::wstring key            (L"SOP.RemoteType" = Element.Attribute)
  R+0x38 std::wstring label id       (L"pd292", Lang/lang.txt)
  R+0x54 value pointer               (settings object 0xa2efa0 + offset,
                                      ``call 0x5ff1b0; add eax,imm; mov ds:R+0x54,eax``)
  R+0x58 int type code
  R+0x5c std::wstring default        (text)
  R+0x78 std::wstring unit
  R+0x98 double min, R+0xa0 double max
  R+0xa8 pointer to option table (std::wstring array, stride 0x1c), R+0xac count
  R+0xb0 std::wstring tag            (L"OnlyMC", L"LaserDA", L"de0".. or empty)
  R+0xcc int unit class              (1 mm, 2 deg, 3 Hz, 4 %, 6 ms, 9 speed, ...)
  ====== ============================================================

  Strings are built with the std::wstring constructor ``0x405130``
  (``push <literal>; mov ecx,<dest>; call 0x405130``).

* Table getters (``mov eax,<table>; ret`` / ``mov eax,<count>; ret``) at
  0x5ff1c0/0x5ff1d0 (0xa3b420, 81), 0x5ff2c0/0x5ff2d0 (0xa3f6a8, 164),
  0x5ff770/0x5ff780 (0xa47c08, 21), 0x5ffb60/0x5ffb70 (0xa49888, 449),
  0x5ffcb0/0x5ffcc0 (0xa60c10, 200), 0x5ffce0/0x5ffcf0 (0xa6b488, 33),
  0x5ffd20/0x5ffd30 (0xa71b40, 11), 0x5ffd80/0x5ffd90 (0xa73cf8, 9),
  0x5ffdc0/0x5ffdd0 (0xa754f8, 10), 0x5ffde0/0x5ffe00 (0xa75d50, 8),
  0x5ffe10/0x5ffe20 (0xa76c50, 7), 0x5ffe30 (0xa77200).

* File compositions (engine vtable +0x1c = register(table, count, file id)):
  ManuPara: 0x4b1bb9..0x4b1cee and 0x45c057..0x45c105 register M0, ZFEC, AF,
  SOFT8, GRAPH, IMPORT; HardPara: 0x45c5e2 (HARD only); LayerPara: slot loops
  0x45bf71..0x45c012 (LAYER then CO2LAYER, file id 2); system backup
  (``lp12``): 0x4653ce..0x465723 (HARD, M0, ZFEC, AF, SOFT8, GRAPH, IMPORT,
  LAYER, CO2LAYER). The resulting element/attribute order (first occurrence
  wins) reproduces BkHardPara.xml, BkManuPara.xml, BkLayerPara.xml and
  PKG/1390backup.xml exactly.

Usage::

    tools/gen_param_schema.py [--exe MainApp.exe] [--asm MainApp.asm] [--out schema.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_EXE = Path(
    "/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52/MainApp.exe"
)
DEFAULT_ASM = REPO / ".scratch" / "asm" / "MainApp.asm"
DEFAULT_OUT = REPO / "src" / "nexcut" / "core" / "schema.json"

RECORD_SIZE = 0xD0
STRING_CTOR = "0x405130"
SETTINGS_GETTER = "0x5ff1b0"  # returns the settings object 0xa2efa0

# name, first record VA, count getter evidence
TABLES: list[tuple[str, int, str]] = [
    ("M0", 0xA3B420, "getters 0x5ff1c0/0x5ff1d0 (count 0x51)"),
    ("LAYER", 0xA3F6A8, "getters 0x5ff2c0/0x5ff2d0 (count 0xa4)"),
    ("CO2LAYER", 0xA47C08, "getters 0x5ff770/0x5ff780 (count 0x15)"),
    ("HARD", 0xA49888, "getters 0x5ffb60/0x5ffb70 (count 0x1c1)"),
    ("GRAPH", 0xA60C10, "getters 0x5ffcb0/0x5ffcc0 (count 0xc8)"),
    ("GRAPH_X", 0xA6AE90, "getter 0x5ffcd0 (callers 0x5c3b08..0x5c5a0c)"),
    ("IMPORT", 0xA6B488, "getters 0x5ffce0/0x5ffcf0 (count 0x21)"),
    ("ZFEC", 0xA71B40, "getters 0x5ffd20/0x5ffd30 (count 0xb)"),
    ("AF", 0xA73CF8, "getters 0x5ffd80/0x5ffd90 (count 9)"),
    ("SOFT10", 0xA754F8, "getters 0x5ffdc0/0x5ffdd0 (count 0xa)"),
    ("SOFT8", 0xA75D50, "getters 0x5ffde0/0x5ffe00 (count 8)"),
    ("STRESSTEST", 0xA76C50, "getters 0x5ffe10/0x5ffe20 (count 7)"),
    ("LASERTEST", 0xA77200, "getter 0x5ffe30"),
]

LAYOUTS = {
    "hard": {
        "parts": [{"table": "HARD"}],
        "evidence": "saveHardParam 0x45c5e2..0x45c78a registers only HARD; "
        "layout reproduces File/BkHardPara.xml",
    },
    "manu": {
        "parts": [{"table": t} for t in ("M0", "ZFEC", "AF", "SOFT8", "GRAPH", "IMPORT")],
        "evidence": "init load 0x4b1bb9..0x4b1cee, save 0x45c057..0x45c105; "
        "layout reproduces File/BkManuPara.xml and SecondBkManuPara.xml",
    },
    "layer": {
        "parts": [
            {"table": "LAYER", "slots": 11},
            {"table": "CO2LAYER", "slots": 11},
        ],
        "evidence": "slot loops 0x45bf71..0x45c012 (file id 2); layout reproduces "
        "File/BkLayerPara.xml (02 §2.1)",
    },
    "system_backup": {
        "parts": [{"table": t} for t in ("HARD", "M0", "ZFEC", "AF", "SOFT8", "GRAPH", "IMPORT")]
        + [{"table": "LAYER", "slots": 11}, {"table": "CO2LAYER", "slots": 11}],
        "evidence": "lp12 backup handler 0x4653ce..0x465723; layout reproduces "
        "PKG/1390backup.xml",
    },
    "technology_fiber": {
        "parts": [{"table": "LAYER", "slots": [11]}],
        "evidence": "02 §6.2: exported presets always use <PLayerParam11>",
    },
    "technology_co2": {
        "parts": [{"table": "CO2LAYER", "slots": [11]}],
        "evidence": "02 §6.2: exported presets always use <PCO2LayerParam11>",
    },
}

TYPE_CODES = {
    "1": {"name": "int", "storage": "int"},
    "2": {"name": "double", "storage": "double"},
    "3": {"name": "string", "storage": "string"},
    "4": {"name": "enum", "storage": "int"},
    "5": {"name": "bool", "storage": "int"},
    "6": {"name": "do_port", "storage": "int"},
    "7": {"name": "di_port", "storage": "int"},
    "8": {"name": "ipv4_int", "storage": "int"},
    "9": {"name": "pulse_equivalent", "storage": "double"},
    "10": {"name": "da_port", "storage": "int"},
    "11": {"name": "da_port_valve", "storage": "int"},
    "12": {"name": "ext_do_port", "storage": "int"},
    "13": {"name": "com_port", "storage": "int"},
    "14": {"name": "colorref", "storage": "int"},
}


class PE:
    """Minimal PE32 reader: virtual address -> bytes/strings."""

    def __init__(self, path: Path) -> None:
        self.data = path.read_bytes()
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        nsec = struct.unpack_from("<H", self.data, pe + 6)[0]
        opt = struct.unpack_from("<H", self.data, pe + 20)[0]
        base = struct.unpack_from("<I", self.data, pe + 24 + 28)[0]
        self.sections = []
        off = pe + 24 + opt
        for _ in range(nsec):
            vsize, va, rsize, roff = struct.unpack_from("<IIII", self.data, off + 8)
            self.sections.append((base + va, vsize, rsize, roff))
            off += 40

    def offset(self, va: int) -> int | None:
        for sva, vsize, rsize, roff in self.sections:
            if sva <= va < sva + max(vsize, rsize):
                return roff + va - sva if va - sva < rsize else None
        return None

    def wstr(self, va: int) -> str:
        o = self.offset(va)
        if o is None:
            raise ValueError(f"VA {va:#x} not file-backed")
        e = o
        while self.data[e : e + 2] != b"\0\0":
            e += 2
        return self.data[o:e].decode("utf-16le")

    def double(self, va: int) -> float | None:
        o = self.offset(va)
        return None if o is None else struct.unpack_from("<d", self.data, o)[0]


LINE_RX = re.compile(r"^\s*([0-9a-f]+):\t(?:[0-9a-f]{2} )+\s*\t(\S+)\s*(.*)$")


def scan_initialisers(asm: Path, pe: PE):
    """Symbolically execute the static initialisers: absolute writes into .bss."""
    strs: dict[int, str] = {}
    ints: dict[int, int] = {}
    dbls: dict[int, float] = {}
    vptr: dict[int, tuple[int, int]] = {}
    last_push = last_ecx = None
    eax: int | None = None
    fpu: float | None = None
    xmm: dict[str, float] = {}
    with asm.open() as fh:
        for line in fh:
            m = LINE_RX.match(line)
            if not m:
                continue
            addr = int(m.group(1), 16)
            op, args = m.group(2), m.group(3)
            if op == "push":
                last_push = int(args, 16) if re.fullmatch(r"0x[0-9a-f]+", args) else None
            elif op == "mov":
                if args.startswith("ecx,0x"):
                    last_ecx = int(args[4:], 16)
                elif mm := re.fullmatch(r"ds:0x([0-9a-f]+),eax", args):
                    if eax is not None:
                        vptr[int(mm.group(1), 16)] = (eax, addr)
                elif mm := re.fullmatch(r"DWORD PTR ds:0x([0-9a-f]+),0x([0-9a-f]+)", args):
                    ints[int(mm.group(1), 16)] = int(mm.group(2), 16)
                if args.startswith("eax,"):
                    eax = None
            elif op == "call":
                if args == STRING_CTOR and last_ecx is not None and last_push is not None:
                    try:
                        strs[last_ecx] = pe.wstr(last_push)
                    except (ValueError, UnicodeDecodeError):
                        pass
                eax = 0 if args == SETTINGS_GETTER else None
            elif op == "add":
                mm = re.fullmatch(r"eax,0x([0-9a-f]+)", args)
                eax = int(mm.group(1), 16) if (mm and eax == 0) else (None if args.startswith("eax") else eax)
            elif op == "fldz":
                fpu = 0.0
            elif op == "fld1":
                fpu = 1.0
            elif op == "fld":
                mm = re.fullmatch(r"QWORD PTR ds:0x([0-9a-f]+)", args)
                fpu = pe.double(int(mm.group(1), 16)) if mm else None
            elif op == "fstp":
                mm = re.fullmatch(r"QWORD PTR ds:0x([0-9a-f]+)", args)
                if mm and fpu is not None:
                    dbls[int(mm.group(1), 16)] = fpu
                fpu = None
            elif op == "movsd":
                if mm := re.fullmatch(r"(xmm\d),QWORD PTR ds:0x([0-9a-f]+)", args):
                    v = pe.double(int(mm.group(2), 16))
                    if v is not None:
                        xmm[mm.group(1)] = v
                elif mm := re.fullmatch(r"QWORD PTR ds:0x([0-9a-f]+),(xmm\d)", args):
                    if mm.group(2) in xmm:
                        dbls[int(mm.group(1), 16)] = xmm[mm.group(2)]
    return strs, ints, dbls, vptr


def build_record(r: int, strs, ints, dbls) -> dict:
    opt, cnt = ints.get(r + 0xA8), ints.get(r + 0xAC)
    enum = [strs.get(opt + i * 0x1C) for i in range(cnt)] if opt and cnt else None
    return {
        "va": hex(r),
        "section": strs[r],
        "key": strs[r + 0x1C],
        "label": strs[r + 0x38],
        "type": ints[r + 0x58],
        "default": strs.get(r + 0x5C, ""),
        "unit": strs.get(r + 0x78, ""),
        "min": dbls.get(r + 0x98, 0.0),
        "max": dbls.get(r + 0xA0, 0.0),
        "enum": enum,
        "enum_va": hex(opt) if opt and cnt else None,
        "tag": strs.get(r + 0xB0, ""),
        "unit_class": ints.get(r + 0xCC, 0),
    }


def extract(exe: Path, asm: Path) -> tuple[list[dict], list[dict]]:
    pe = PE(exe)
    strs, ints, dbls, vptr = scan_initialisers(asm, pe)
    bound: list[dict] = []
    for x in sorted(vptr):
        t = ints.get(x + 4)
        if t is None or not 1 <= t <= 20:
            continue
        r = x - 0x54
        rec = build_record(r, strs, ints, dbls)
        rec["value_offset"] = hex(vptr[x][0])
        rec["init"] = hex(vptr[x][1])
        bound.append(rec)
    known = {int(b["va"], 16) for b in bound}
    unbound: list[dict] = []
    for r in sorted(strs):
        if r in known:
            continue
        if (
            r + 0x1C in strs
            and r + 0x38 in strs
            and r + 0x5C in strs
            and 1 <= ints.get(r + 0x58, 0) <= 20
        ):
            rec = build_record(r, strs, ints, dbls)
            rec["value_ptr"] = ints.get(r + 0x54, 0)
            unbound.append(rec)
    # table membership
    starts = sorted((va, name) for name, va, _ in TABLES)
    for rec in bound:
        va = int(rec["va"], 16)
        name = None
        for sva, sname in starts:
            if va >= sva:
                name = sname
        rec["table"] = name
    return bound, unbound


def check_contiguity(bound: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, va, _ in TABLES:
        recs = [b for b in bound if b["table"] == name]
        for i, b in enumerate(recs):
            assert int(b["va"], 16) == va + i * RECORD_SIZE, (name, i, b["va"])
        counts[name] = len(recs)
    return counts


def dump(bound: list[dict], unbound: list[dict], counts: dict[str, int], out: Path) -> None:
    order = ["table", "va", "init", "section", "key", "label", "type", "default", "unit",
             "min", "max", "enum", "enum_va", "tag", "unit_class", "value_offset"]
    head = {
        "format": "nexcut.param-schema/1",
        "source": {
            "binary": "MainApp.exe v0.0.0.52 (2025-06-24)",
            "generator": "tools/gen_param_schema.py",
            "docs": ["01-hardware-config.md §0.2, §1", "02-layer-params.md §2.2, §2.3, §6.2"],
            "record_size": RECORD_SIZE,
            "settings_object_va": "0xa2efa0",
        },
        "counts": {
            "bound_descriptors": len(bound),
            "unbound_descriptors": len(unbound),
            "per_table": counts,
        },
        "type_codes": TYPE_CODES,
        "type_codes_confidence": "names: INFERENCE likely (01 §0.2); storage: EVIDENCE, every "
        "value in 65 vendor XML files re-formats identically (int -> %d, double -> %.17g)",
        "tables": [{"name": n, "va": hex(v), "evidence": e} for n, v, e in TABLES],
        "layouts": LAYOUTS,
    }
    lines = ["{"]
    for k, v in head.items():
        lines.append(f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},")
    for name, recs, extra in (("descriptors", bound, []), ("unbound_descriptors", unbound, ["value_ptr"])):
        lines.append(f"  {json.dumps(name)}: [")
        keys = [k for k in order if k not in ("table", "init", "value_offset")] + extra if extra else order
        body = []
        for rec in recs:
            body.append("    " + json.dumps({k: rec.get(k) for k in keys}, ensure_ascii=False))
        lines.append(",\n".join(body))
        lines.append("  ]" + ("," if name == "descriptors" else ""))
    lines.append("}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    ap.add_argument("--asm", type=Path, default=DEFAULT_ASM)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    if not a.asm.exists():
        a.asm.parent.mkdir(parents=True, exist_ok=True)
        tmp = a.asm.with_suffix(f".asm.tmp.{os.getpid()}")
        with tmp.open("w") as fh:
            subprocess.run(["objdump", "-d", "-M", "intel", str(a.exe)], stdout=fh, check=True)
        os.replace(tmp, a.asm)
    bound, unbound = extract(a.exe, a.asm)
    counts = check_contiguity(bound)
    dump(bound, unbound, counts, a.out)
    print(f"{len(bound)} bound + {len(unbound)} unbound descriptors -> {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
