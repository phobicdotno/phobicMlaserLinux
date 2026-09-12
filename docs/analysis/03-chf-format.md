# 03 — The `.chf` job/graphic file format ("scFlie" container)

Analyst report for the Linux re-implementation of Mlaser v0.0.0.52 (CF1390 / SC2000 platform).

* Package analysed (read-only): `/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52` (below: `SRC`)
* Parser delivered: `/home/karstein/phobicMlaserLinux/tools/chf_parse.py` (Python 3, stdlib only)
* Renders / JSON dumps: `/home/karstein/phobicMlaserLinux/tools/out/`
* Disassembly used: `objdump -d -M intel SRC/Module/CADModule.dll` (all addresses below are virtual addresses of that DLL, image base `0x10000000`; `.text` VA `0x10001000` = file offset `0x400`, `.rdata` VA `0x1010f000` = file offset `0x10d800`).

Throughout the document **EVIDENCE** means something directly observed (bytes in a file, an instruction sequence, a string) and **INFERENCE** means my interpretation, tagged with a confidence (high / medium / low).

---

## 1. Executive summary

* `.chf` is a **plain-text, line-oriented, CRLF-terminated ASCII file** (Chinese strings inside it are GBK), not a binary format. Every value is on its own line. The container starts with the literal line `scFlie` (sic — a typo of "scFile", and MainApp.exe also carries the string `cAscFlie`) followed by an integer **format version** and ends with the literal line `eof`. **EVIDENCE:** all 8 sample files; writer `0x100da450`/`0x100d9a30`, reader `0x100da140`.
* The reader/writer live entirely in **`SRC/Module/CADModule.dll`** (class `CCADModule`, helper class `CLIFileBasic`), not in MainApp.exe. **EVIDENCE:** the marker strings `<Begin Graphs>`, `####Gly: `, `<Crafts>`… exist only in CADModule.dll (§3).
* The file is a list of **graphs** (`IGraph`, type ids 8–12: contour, group, text, scan(fly-cut), contour-ex) each containing **glyphs** (`IGlyph`, type ids 1–7: point, segment, arc, circle, ellipse-arc, lwpolyline, cubic B-spline) plus a per-contour **`<Crafts>`** block (kerf compensation, PWM curve, lead-in ("GuideCurve"), cooling points).
* Units are **millimetres**, angles in the arc/ellipse records are **radians**, coordinates are absolute machine/canvas coordinates in a **Y-up** right-handed frame (inference, medium — see §10: the file alone cannot separate "Y-up + DXF bulge sign" from "Y-down + mirrored bulge sign"; Y-up rests on the 1:1 DXF import path). Arc direction is the sign of `end_angle - start_angle`, polyline arcs use the **DXF bulge convention** (`bulge = tan(θ/4)`, negative = clockwise in a Y-up frame).
* Cut order = order of graphs in the file; within a contour the glyph order plus a per-glyph **direction flag** (`1` forward, `-1` reversed) defines the tool path; the contour's start point is stored explicitly.
* The Python parser (`tools/chf_parse.py`) parses all 8 samples, and a self-check recomputes contour length, bounding box, start and end point from the geometry and matches the values stored in each file to <1e-3 mm — which proves the geometric interpretation of every field that the samples exercise (segment, circle, closed bulge polyline).
* The current writer emits **version 5**; the reader accepts versions 1–5 with documented differences (§9). Older sample files (`Graph/Work*/*.chf`, 2020) are version 4 and contain blank "reserved" lines that version 5 dropped.

---

## 2. Sample inventory

| File (under `SRC`) | Size | md5 | Version | Content |
|---|---|---|---|---|
| `File/autosave.chf` | 10431 | `d8e450b6…` | 5 | 24 graphs, each one **segment** (type 2), a 3×8 array of 284.27 mm tilted lines, x∈[395,1247], y∈[302,601] |
| `File/Temp/tempGraph.chf` | 500 | `d8017d9e…` | 5 | 1 graph, one **circle** (type 4) centre (47.44,47.44) r=47.42 |
| `Graph/Work1/1.chf` | 570 | `8d6c0f39…` | 4 | 1 graph, one **circle** r=1.195 |
| `Graph/Work1/2.chf` | 646 | `1f4fec8f…` | 4 | 1 graph, one closed **lwpolyline** (type 6), 4 vertices, bulge 0 → rectangle 2.28×1.70 |
| `Graph/Work1/3.chf` | 718 | `12aa1212…` | 4 | 1 graph, one closed **lwpolyline**, 6 vertices, bulges 0/−0.414214 → stadium ("rounded rectangle") |
| `Graph/Work2/1.chf` | 646 | `1f4fec8f…` | 4 | byte-identical to `Work1/2.chf` |
| `Graph/Work2/2.chf` | 718 | `12aa1212…` | 4 | byte-identical to `Work1/3.chf` |
| `Graph/Work2/3.chf` | 570 | `8d6c0f39…` | 4 | byte-identical to `Work1/1.chf` |

**EVIDENCE:** `md5sum`, `file` (all "ASCII text, with CRLF line terminators"), parser output.
MainApp.exe contains the path template `\Graph\Work%d\%d.chf` (UTF‑16 string), so `Graph/WorkN/M.chf` are per-"work slot" job files (the same three shapes were stored in two slots in a different order).

Other files that use the same container (`scFlie … eof`) but are **not** graphic files (**EVIDENCE:** `grep -l scFlie -r SRC`, verified by the verifier; the analyst only listed the first one):

| File | Lines between `scFlie` and `eof` | Interpretation |
|---|---|---|
| `File/Temp/tempIsBreak.ini` | `1`, `1` | "is break" flag file (breakpoint/resume state). INFERENCE medium (name). |
| `File/PithCompensate.pcf` | `0` | pitch-compensation table with 0 entries (`hp25 螺距补偿 = Pitch compensate`). INFERENCE medium. |
| `File/AutosaveParam1.ini`, `File/Temp/AutosaveParam1.ini` | `38`, `979454`, `342126`, `7`, `486` | five ints. `979454`/`342126` scaled by 1/1000 give (979.454, 342.126) which lies inside the 1300×900 bed — plausibly a resume position in µm. INFERENCE low. |
| `File/AutosaveParam2.ini`, `File/Temp/AutosaveParam2.ini` | `37`, `965189`, `340285`, `7`, `132` | same layout. |
| `File/ManuContour.dat`, `File/Temp/ManuContour.dat` | `24`, then `0`…`23` | a count followed by 24 indices — the same number of graphs as `File/autosave.chf` has (24). INFERENCE medium: manual cut-order list of the current document (graph indices, 0-based). |

Note the version line is absent in these files (the second line is already payload), so `scFlie` + `eof` is the only thing the container guarantees; the integer version is part of the `.chf` grammar, not of the container. The `CLIFileBasic` writer is a generic text serializer; the `.chf` grammar is only one client of it.

### 2.1 The `Report/*.chf.jpg` previews

`Report/rpt.chf.jpg` (rectangle with a circle inside), `Report/111.chf.jpg` and `Report/222.chf.jpg` (byte-identical, `cmp` verified; a tall rectangle) are renders of jobs named `rpt.chf`, `111.chf`, `222.chf` — **none of those .chf files exist in the package**, so a 1:1 comparison with the samples is impossible. **Added by verifier:** `Report/report.txt` (UTF-8, not GBK) is the report log for exactly these two jobs: `222.chf,15.45x34.34 mm,2025-03-07 16:04:41,3.57 m,1.05 m,0,1分26秒,62.28 Sec,11.32 Sec,0.00 Sec` and `111.chf,15.45x34.34 mm,2025-03-07 16:04:50,0.10 m,0.21 m,0,0分03秒,1.47 Sec,1.69 Sec,0.00 Sec` (`1分26秒` = "1 min 26 s"). So both jobs had the same 15.45 × 34.34 mm extent (consistent with the identical preview) but very different cut lengths (3.57 m vs 0.10 m), i.e. the preview shows the outline, not the number of passes/copies. `Report/LogReport.txt`/`TotalReport.txt` hold the same per-job records (`未命名-1` = "Untitled-1", size, cut length, move length, …). What can be verified is that the shapes they show (rectangles, circles) are exactly the glyph kinds the samples use (lwpolyline with 4 vertices / circle), and that my renders of the samples (`tools/out/*.png|svg`) show the expected geometry: 24 parallel tilted segments in a 3×8 grid (`File_autosave`), a stadium (`Work1_3`), a circle (`Temp_tempGraph`), a rectangle (`Work1_2`).

---

## 3. Where the code is

| Item | Evidence |
|---|---|
| Marker strings | `.rdata` of CADModule.dll, file offsets `0x110344…0x1103f0` (`<End Crafts>`, `<End coolPos Para>`, `<coolPos Para>`, `<End GuideCurve Para>`, `<GuideCurve Para>`, `<End PWM Control>`, `<PWM Control>`, `<Crafts>`, `<End Glyphs>`, `####Gly: `, `<Glyphs>`), `0x111bd0…0x111bf0` (`<End Graphs>`, `####graph NO:`, `<Begin Graphs>`), `0x1127a0…0x1127f4` (`%s%d`, `%12.10f`, `%f,%f`, `scFlie`, `eof`, `wb`). Also `####Group elem NO:` (VA `0x101120d0`), `####ContourEx link info:` (`0x10111f68`), `<Scan path>`/`####Path:`/`<End Scan path>` (`0x10112edc/0x10112ed0/0x10112ec0`), `<Text para>`/`<End Text para>` (`0x10113120/0x10113110`). |
| MainApp.exe | only has the strings `scFlie` and `cAscFlie` (file offsets `0x3c3544`, `0x3c3526`) and the file-name strings `autosave.chf`, `tempGraph.chf`, `\_tempSource.chf`, `chf File(*.chf)\|*.chf\|\|`, `(*.chf)\|*.chf\|dxf`, `\Graph\Work%d\%d.chf`. It calls CADModule through the `ICADModule` COM-like interface. |
| Writer version constant | `mov DWORD PTR [esi+0x970],0x5` at `0x100e5662` (CCADModule ctor); written as the 2nd line by `0x100ddf89`/`0x100de0a7`/`0x100e5159`. |
| Relevant RTTI class names | `IGlyph, IGraph, CGlyContour, CGlyContourEx, CGlyGroup, CGlyScan, CGlyText, CEditablePoint, CEditableSegment, CEditableArc, CEditableCircle, CEditableEllipsArc, CEditableLwpoly, CEditableSpline, CGuideCurve, CCoolPoint, CBridge, CLIFileBasic, CLICsv, CLIpos, CLIrtl, CCADModule, ICADModule` (`strings -a CADModule.dll \| grep '^\.\?AV'`). |

### 3.1 Function map (all in CADModule.dll)

| Function | VA | Role |
|---|---|---|
| `CLIFileBasic::CLIFileBasic` | `0x100d9720` | allocates 1000‑byte token buffer (`[this+0x14]`) and 5000‑byte line buffer (`[this+0x28]`) |
| `OpenForWrite(path, bufferedMode)` | `0x100da450` | `bufferedMode==0`: `_wfopen_s(path,"wb")` and `fprintf`; `!=0`: `CreateFileW(GENERIC_WRITE)` + `sprintf_s` + `WriteFile`. Then writes `scFlie`. |
| `OpenForRead(path)` | `0x100da140` | `CreateFileW(GENERIC_READ)`, reads whole file, first token must be `scFlie` (else error 5), file must end (ignoring trailing bytes ≤ 0x0d) with `eof` (else error 4). Error codes in `[this+4]`: 1 open failed, 2 empty/size, 3 read/parse error, 4 missing eof/unexpected end, 5 bad magic. |
| `ReadToken` | `0x100d9990` | see §4.2 |
| `ReadInt / ReadDouble / ReadBool / ReadPoint / ReadWStr / ReadDoubleArray / ReadPointArray` | `0x100d9db0 / 0x100d9df0 / 0x100d9e30 / 0x100d9e80 / 0x100da370 / 0x100da0d0 / 0x100da570` | typed readers |
| `WriteStr / WriteStrInt / WriteInt / WriteDouble / WriteBool / WritePoint / WriteWStr / WriteEof` | `0x100d9860 / 0x100d9aa0 / 0x100d9b10 / 0x100d9b70 / 0x100d9c70 / 0x100d9cc0 / 0x100da070 / 0x100d9a30` | typed writers |
| `CCADModule::WriteGraphs(file, level, bool, double)` | `0x100a52a0` | `<Begin Graphs>` … trailer |
| `CCADModule::ReadGraphs(file, version, flag)` | `0x100a9900` | |
| `CCADModule::LoadDocument` (reads trailer) | `0x100ab460` | |
| Save entry points | `0x100ddf89` (full document, bool+double from caller), `0x100e5159` (single graph → bool 0, double 0.0; used for `tempGraph.chf`) | |
| Load entry points | `0x100e0740`, `0x100e24e0`, `0x100e28c0`, `0x100e2e00` | read version int after magic |
| Glyph factory `CreateGlyph(type)` | `0x10059b90` (jump table `0x10059d50`) | types 1..7 |
| Graph factory `CreateGraph(type)` | `0x100a46f0` (jump table `0x100a4844`) | types 8..12 |
| vtable slot 23 = `Read(file, version, flag)`, slot 24 = `Write(file, level)` | | for both `IGlyph` and `IGraph` |

vtables (RTTI complete-object-locator scan): `CGlyContour vt=0x10111df4 Read=0x1006b4e0 Write=0x1005ab20`; `CGlyContourEx vt=0x10111f94 R=0x10072270 W=0x100704e0`; `CGlyGroup vt=0x101120ec R=0x10076910 W=0x10074630`; `CGlyScan vt=0x10112ef4 R=0x100948e0 W=0x10091e80`; `CGlyText vt=0x10113134 R=0x100a4030 W=0x100a1fe0`; `CEditablePoint R=0x100895d0 W=0x100890a0`; `CEditableSegment R=0x1008ac90 W=0x100897b0`; `CEditableArc R=0x1007eb10 W=0x1007c900`; `CEditableCircle R=0x10080420 W=0x1007f130`; `CEditableEllipsArc R=0x100828a0 W=0x100806b0`; `CEditableLwpoly R=0x10088d50 W=0x10083e70`; `CEditableSpline R=0x1008f250 W=0x1008b010`.

---

## 4. The container: `CLIFileBasic` text serializer

### 4.1 Writer value formats (EVIDENCE: `0x100d9860…0x100d9d1e`, format strings at VA `0x10113f90…0x10113fe8`)

| Writer | Format | Notes |
|---|---|---|
| `WriteStr(s)` | `"%s\r\n"`; if `s==NULL` → `"\r\n"` (an empty line) | markers `<…>` |
| `WriteStrInt(s,i)` | `"%s%d\r\n"` | `####graph NO:1`, `####Gly: 1`, `####Group elem NO:1`, `####ContourEx link info:1`, `####Path:1` |
| `WriteInt(i)` | `"%d\r\n"` | |
| `WriteDouble(x)` | `\|x\| < 1e-10` → `"0.0\r\n"` (direct-file mode) or `"0\r\n"` (buffered mode); `1e-10 ≤ \|x\| < 1e-6` → `"%12.10f\r\n"`; else `"%f\r\n"` | constants at `0x10113fd0` (1e‑10) and `0x10111dd0` (1e‑6). Samples show `0.0`, so they were written in direct-file mode. |
| `WriteBool(b)` | `"1\r\n"` / `"0\r\n"` | |
| `WritePoint(p)` | `"%f,%f\r\n"` | **no** zero special-casing → `0.000000,0.000000` |
| `WriteWStr(ws)` | `WideCharToMultiByte(CP_ACP)` then `"%s\r\n"` | text/font names; CP_ACP = GBK (cp936) on the target machines |
| `WriteEof()` | `"eof\r\n"` then closes the file | |

`%f` is the MSVCR100 default: 6 decimals, no exponent. Lines are always `\r\n`. There is no indentation (the `level` argument passed to `Write` is incremented but never printed).

### 4.2 Reader tokenizer (EVIDENCE: `0x100d9990`)

* One token per line. Bytes `0x0d` or `0x0a` terminate the token; a `0x0d` followed by `0x0a` is swallowed as one terminator.
* Spaces (`0x20`) and tabs (`0x09`) are **dropped anywhere in the line**, not just trimmed.
* An empty line yields an empty token. `ReadInt` accepts an empty token (returns `atoi("")=0`); `ReadDouble` likewise (`atof("")=0`).
* `ReadInt` validates chars ∈ `[0-9-]`, `ReadDouble` ∈ `[0-9.-]` with at most one `.`; `ReadPoint` splits at the first `,` and validates both halves the same way (each half must be ≤ 30 chars else it is replaced by `"0"`); `ReadBool` is `strcmp(token,"1")==0`. No exponent notation is accepted anywhere.
* Markers such as `<Glyphs>` are **not compared** by the reader — it just consumes one token where it expects a marker. (The only string compares are `scFlie` and `eof`.) Consequently a file with a wrong marker still loads as long as the line counts match. My parser is stricter and checks markers.
* **Added by verifier (re-read of `0x100d9990`):** (a) `[this+0x1c]` is a one-token push-back flag — when set, `ReadToken` clears it and returns `true` without advancing, i.e. the previous token is re-delivered. (b) A token that reaches the end of the buffer **without** a terminating `0x0d`/`0x0a` is still copied but `ReadToken` returns `false` (`xor bl,bl` at `0x100d99e9` → return 0), and the typed readers then set error 4 (`0x100d9dbc`); in practice this never triggers because `eof\r\n` is always last. (c) The `=`-handling branch at `0x100d99e1` is dead in this function (`al` is set to 1 at `0x100d9994` and never cleared), so `=` is an ordinary character. (d) The `eof` check in `OpenForRead` (`0x100da2f8…0x100da321`) walks back from the end over every byte whose *signed* value is ≤ `0x0d` (so CR, LF, NUL — and also any byte ≥ `0x80`), then requires the three preceding bytes to be `e`,`o`,`f`.
* `ReadInt` error path: if `ReadToken` fails, `[this+4]=4` and `false`; if the validator rejects the token, `false` without setting a code (`0x100d9dcd…0x100d9dd4`).

---

## 5. File grammar (version 5, what the current program writes)

```
scFlie
5                                     ; format version (int) [CCADModule+0x970]
<Begin Graphs>
N                                     ; graph count (int)
  repeat N times:
  ####graph NO:i                      ; 1-based index (WriteStrInt)
  T                                   ; graph type id: 8,9,10,11,12 (int) = [graph+0x8]
  <graph body for type T>             ; §6
<End Graphs>
B                                     ; bool (0/1)   trailer_bool   (see §8)
D                                     ; double       trailer_double
x,y                                   ; point        trailer_pt1  = [CCADModule+0x9cd0]
x,y                                   ; point        trailer_pt2  = [CCADModule+0x9ce0]
eof
```

**EVIDENCE:** writer `0x100a52a0` (`WriteStr("<Begin Graphs>")`, `WriteInt(count)`, loop `{WriteStrInt("####graph NO:",i+1); WriteInt([graph+8]); graph->Write(file, level+1)}`, `WriteStr("<End Graphs>")`, `WriteBool(arg3)`, `WriteDouble(arg4)`, `WritePoint([this+0x9cd0])`, `WritePoint([this+0x9ce0])`), reader `0x100a9900` + `0x100ab4c5…0x100ab54b`, save entry `0x100ddf89` (`WriteInt([this+0x970])` then `WriteGraphs`, then `WriteEof` `0x100d9a30`).

Reader-side validation in `0x100a9900` (**corrected by verifier**, disassembly `0x100a99ae…0x100a9ab8`): after each graph is read, if its stored length `[graph+0x48]` is **strictly less than** `0.01` (`fld 0.01; fcomp [edi+0x48]; test ah,0x41` — equality skips the check) then the graph is kept only if it is (a) a `CGlyContour` (`__RTDynamicCast` to `.?AVCGlyContour@@`) with **exactly one glyph whose type id is 1 (point)**, or (b) a `CGlyGroup` with exactly one child, **that child having exactly one glyph of type 1**. A graph failing this test is **not** a load error: it is deleted (`call [vtable+0]` with flag 1 at `0x100a9ab6`) and the loop continues with the next graph (`jmp 0x100a9a0f`), so degenerate contours are silently dropped and `ReadGraphs` still returns `true`. Graphs that pass are appended to the document list via `0x10018ac0`. (INFERENCE, high: this admits "single point" jobs — e.g. drilling — but discards zero-length contours.)

---

## 6. Graph records

Graph type ids and classes (**EVIDENCE:** jump table at `0x100a4844` → constructors: 8→`0x10064e70` (sets vtable `0x10111df4` = `CGlyContour`, `[this+8]=8` at `0x10064f89`), 9→`0x100760e0` (`CGlyGroup`), 10→`0x100a26b0` (`CGlyText`), 11→`0x100933c0` (`CGlyScan`), 12→`0x10071a20` (`CGlyContourEx`)).

| id | class | meaning |
|---|---|---|
| 8 | `CGlyContour` | one connected tool path (chain of glyphs) + crafts |
| 9 | `CGlyGroup` | a group of `CGlyContour`s (result of "group" command; also what a DXF block/nesting part becomes) |
| 10 | `CGlyText` | text object: text/font parameters + (v≥4) the outline as a group of contours |
| 11 | `CGlyScan` | "scan cut" / fly-cut object (mf112 `飞行切割` = *Scan Cutting*): a group plus the generated scan paths |
| 12 | `CGlyContourEx` | a group with extra "link info" records (§6.5) |

### 6.1 Type 8 — `CGlyContour` (EVIDENCE: Write `0x1005ab20`, Read `0x1006b4e0`)

Field offsets are those of the C++ object (useful when reading other parts of the DLL).

| # | Line(s) | Type | C++ member | Sample (autosave graph 1) | Meaning / confidence |
|---|---|---|---|---|---|
| 1 | `precision` | double | `[+0xc8]` | `0.010000` (autosave.chf) / `0.100000` (tempGraph.chf **and** the v4 files) | Geometric tolerance in mm used e.g. to decide closure (start==end within this). Constructor argument (`fld [ebp+8]; fst [esi+0xc8]` at `0x10064f48`), also stored ×0.1 in `[+0xd0]` (`fmul 0x10111088` = 0.1). **Verifier note:** the value is *not* tied to the file version — `File/Temp/tempGraph.chf` is v5 and carries `0.100000`; the caller chooses it. INFERENCE high. |
| 2 | `<Glyphs>` | marker | | | |
| 3 | `length` | double | `[+0x48]` | `284.266045` | Total path length (mm). Verified: equals Σ glyph lengths in all samples (parser `--check`). |
| 4 | `bbox_min` | point | `[+0x28]` | `395.330000,302.907000` | min x,y of the contour. Verified. |
| 5 | `bbox_max` | point | `[+0x38]` | `677.258772,339.284906` | max x,y. Verified. |
| 6 | `start` | point | `[+0x60]` | `395.330000,302.907000` | Path start point (cut start). Verified = first point of first glyph in stored direction. |
| 7 | `end` | point | `[+0x70]` | `677.258772,339.284906` | Path end point. Verified. |
| 8 | `nglyph` | int | size of `[+0xa8]` vector (8-byte elements `{IGlyph*, int dir}`) | `1` | must be ≥ 1 |
| 9 | per glyph: `####Gly: i` | string | | `####Gly: 1` | |
| 10 | `direction` | int | element `+4` | `1` | `1` = glyph traversed in its natural direction, `-1` = reversed. **EVIDENCE:** `0x1005b438`/`0x1005b4a8`: when `== -1` the curve parameter is mirrored (`t = 1 - t`), and `fild [elem+4]` at `0x1005b4db` multiplies a direction sign. |
| 11 | `glyph_type` | int | `[glyph+0x8]` | `2` | 1..7, §7 |
| 12 | glyph body | | | `395.330000,302.907000` / `677.258772,339.284906` | §7 |
| 13 | `<End Glyphs>` | marker | | | |
| 14 | `layer` | int | `[+0xc]` | `0` | Layer index (0-based). After reading, vtable slot 18 (`0x1005a8e0`) is called with it: it stores `[+0xc]`, propagates to every glyph's `[+0xc]` and to the lead-line object `[+0x100]`. UI: `gp80 图层1 = Layer 1`, `gp81 背景图层 = Bk Layer`, `gp82 标刻图层 = Mark Layer`. Layer parameters (speed/power/…) live outside the .chf in `File/BkLayerPara.xml`. Confidence high. |
| 15 | `int58` | int | `[+0x58]` | `1` | Default 1 (`0x1005f59a`, in the `IGraph` base ctor `0x1005f560`). **Verifier re-read:** no code in CADModule.dll writes `[+0x58]` other than the ctor, the file reader and the clone routine (`0x1006bad9`); grep for `inc/add/xor … [+0x58]` finds nothing, so the value can only come from the file or from a MainApp-side setter that was not found. Consumers all use **only its parity**: (a) `0x1006b027…0x1006b059`: `compensate_type==2 → −1`, `==3 → +1`, otherwise `int58 odd → +1, even → −1` (sign for the offset direction); (b) `0x1005cea2…0x1005cee7` (lead-line setter, see §6.1.1) and `0x1006ea6f`: `even(int58) XOR !orientation[+0xdc] XOR arg → lead.flag [+0x1c8]`. INFERENCE medium: a side/direction parity (which side of the path the offset and lead-in go). |
| — | *(not in file)* `[+0xdc]` | byte | | | **Added by verifier:** contour orientation flag, ctor default 1 (`0x10064f69`), set to `1`/`0` by an orientation computation in `0x10063cb0…0x10064182` (compares an accumulated signed quantity against 0 at `0x10064150`), and **toggled together with `lead.flag [+0x1c8]` by `Reverse`** (vtable slot 44 = `0x1006c200`, which also swaps `start [+0x60]`/`end [+0x70]`; flips at `0x1006c338…0x1006c352`) and by slot 6 (`0x1006e470`, flips at `0x1006e4d1…0x1006e4e2`). Not stored in the file; recomputed. INFERENCE medium: 1 = counter-clockwise ("positive"). |
| 16 | `<Crafts>` | marker | | | crafts block, §6.1.1 |
| … | | | | | |
| 33 | `<End Crafts>` | marker | | | |

Not stored, but derived after load: `closed = dist(start,end) ≤ precision` → `[+0xd8]` (`0x1006b66e…0x1006b693`, via `0x10012510`).

#### 6.1.1 The `<Crafts>` block (process parameters of one contour)

| Line | Type | C++ member | Sample | Meaning |
|---|---|---|---|---|
| `compensate_type` | int | `[+0xf0]` | `-1` | Kerf-compensation state: `-1` = none (ctor default `0x10064f96`). `0x1006b460(bool positive)` computes `ecx = positive ? 3 : 2` and stores it unless the current value is `-1` or already equal, then re-runs the offset (`0x1006aef0`); it then sets `lead.flag [+0x1c8] = orientation[+0xdc] XOR positive` and regenerates the lead (`0x1005c510`) if the flag changed (verifier re-read of `0x1006b460…0x1006b4ce`). `0x1006b027` maps type `2 → sign −1`, `3 → sign +1`. UI strings (`Lang/lang.txt`, UTF-16): `pd506 割缝补偿参数.类型 = Compensate Parameters.Compensate Type`, `mf98 阴切 = Inside`, `mf99 阳切 = Outside`, and `pd322 杂项.最外层为阴切 = Misc: outermost contour is an inside cut` (= `GRP.OutsideIsNegativeSide` in `File/BkManuPara.xml`). INFERENCE (medium): 3 = outside/"positive" cut (offset outward), 2 = inside/"negative" cut (offset inward). Which numeric side `+1` means geometrically is not proven. |
| `compensate_width` | double | `[+0xf8]` | `0.0` | Compensation distance (mm) (`pd507 割缝补偿参数.补偿距离 = Compensate Width`). INFERENCE high. |
| `<PWM Control>` | marker | | | per-contour laser PWM curve (`pd1608 激光参数.每段轮廓切换PWM使能 = Enable PWM Per Contour`, `GP.PWMCurveNodes`) |
| `pwm_enable` | int | `[+0x104]` | `1` | ctor default 1. INFERENCE high: PWM curve enabled/inherit flag. |
| `npwm` | int | size of `[+0x108]` | `0` | number of curve nodes |
| `npwm × (a, b)` | double, double | `[+0x108][i]`, `[+0x118][i]` | — | node i: two parallel double arrays, written interleaved (`a_i` then `b_i`). INFERENCE medium: position ratio along the path (0..1) and PWM power/duty (%) — cf. `GP.PWMCurveNodes`. No sample contains nodes. |
| `nclose` | int | size of `[+0x12c]` | `0` | |
| `nclose × ratio` | double | `[+0x12c][i]` | — | INFERENCE medium: path-ratio positions (0..1) where the laser is switched off; the debug dump `SRC/closePwmPosRatios.txt` (46 values 0.0166…0.9834, monotonically increasing, name "close PWM pos ratios") is exactly such a list. |
| `<End PWM Control>` | marker | | | |
| `double170` | double | `[+0x170]` | `0.0` | ctor default 0.0 (`0x10064f90`). **Verifier finding:** the lead-line setter (`0x1005ce80`, non-virtual: `SetGuideLine(int type, double length, double radius, double angle, bool)`) passes `[+0x170]` to `0x1005b390` (`0x1005cf25`), and `0x1005b390` multiplies it by the contour length `[+0x48]` and walks the glyphs to find the point at that arc-length (tolerance `0x1010f6b8` = 0.05) — i.e. it is a **path ratio 0..1**. Other users subtract it from ratio arrays (`0x1005fda9`, `0x10060297`, `0x10060509`). Both are reset to 0 by slot 51 (`0x1005ec50`, "clear crafts"). INFERENCE (medium): the lead-in / start-point position along the contour as a ratio = `GRP.LeadPosPrecent` (`pd319 引线参数.起点位置 = Lead Line.Start Point Position`). |
| `double188` | double | `[+0x188]` | `0.0` | ctor default 0.0 (`0x10064fa0`). **Verifier finding:** added to the contour length in `0x10060036…0x10060046` (`length + … + [+0x188]`) and tested `!= 0` at `0x1005e07a` before a special branch. INFERENCE (medium-low): over-cut length in mm (`pd324 缺口封口.过切大小 = Gap Seal.Overcut Length`, `GRP.LoopGapOverCutLength`, class `COpOverCutContourCmd`). |
| `<GuideCurve Para>` | marker | | | lead-in line ("guide curve" = `引线` = *Lead Line*, class `CGuideCurve`, member object at `[+0x1a0]`, pointer `[+0x100]`) |
| `lead.type` | int | `[+0x1a8]` | `0` | `GRP.GuideLineType` / `pd315 引线参数.类型 = Lead Line.Type`. INFERENCE high: 0 = none; other values = line / arc / line+arc (exact enum not recovered). **Verifier:** the machine's own `File/BkManuPara.xml` has `<GRP GuideLineType="2" GuideLineAngle="90" GuideLineLength="8" GuideArcRadius="2" LeadPosType="0" LeadPosPrecent="0" …>` (and an older `GuideLine="1" GuideLineDir="0" GuideLineLength="100"` set), so `2` is a real enum value used on this machine (lead-in enabled with a 2 mm arc radius). |
| `lead.angle_deg` | double | `[+0x1b0]` | `90.000000` | `GRP.GuideLineAngle` / `pd316 引线参数.角度 = Lead Line.Angle` in **degrees** (90 is the default UI value). |
| `lead.length` | double | `[+0x1b8]` | `5.000000` | `GRP.GuideLineLength` / `pd317 引线参数.长度 = Lead Line.Length`, mm. |
| `lead.arc_radius` | double | `[+0x1c0]` | `0.0` | only present when version > 1; `GRP.GuideArcRadius`. INFERENCE high. |
| `lead.flag` | bool | `[+0x1c8]` | `0` | **Verifier re-read:** computed, not merely toggled — `0x1006b460`: `flag = orientation[+0xdc] XOR positive`; `0x1005ce80` (SetGuideLine): `flag = even(int58) XOR !orientation XOR arg5`; `Reverse` (slot 44) inverts it together with `[+0xdc]`. So it encodes on which side of the path the lead-in lies, relative to the path direction. INFERENCE medium. |
| `<End GuideCurve Para>` | marker | | | |
| `<coolPos Para>` | marker | | | only when version > 2; cooling points (`mf406 冷却点 = Cool Point`, class `CCoolPoint`, `GP.CoolPostionDelay`) |
| `ncool` | int | size of `[+0x14c]` | `0` | |
| `ncool × pos` | double | `[+0x14c][i]` | — | INFERENCE medium: path positions of cooling stops (ratio 0..1 or mm along path — undetermined, no sample). |
| `<End coolPos Para>` | marker | | | |

Constructor defaults (`0x10064e70`, verified line by line at `0x10064f48…0x10064fa0`): `[+0x104]=1`, `[+0xf0]=-1`, `[+0xf8]=0`, `[+0x170]=0`, `[+0x188]=0`, `[+0xd8]=0`(closed), `[+0xdc]=1`, `[+0x278]=1` (16-bit), `[+0xc8]=arg`, `[+0xd0]=arg*0.1`. The lead-line parameter block at `[+0x1a0]` is initialised by `0x10061470`, which is called at `0x10064f43` with `push 0x101119e8` — **verifier correction:** the 90°/5 mm defaults are not literals inside `0x10061470`; they are copied from the constant block at `.rdata` VA `0x101119e8` = `{int 0 (type), pad, double 90.0 @0x101119f0, double 5.0 @0x101119f8, double 0.0 (arc radius), 0 (flag)}` (bytes `00000000 0000903f | 0000000000805640 | 0000000000001440 | 00…`). Consistent with every sample.

### 6.2 Type 9 — `CGlyGroup` (EVIDENCE: Write `0x10074630`, Read `0x10076910`)

```
[version 2..4: 3 blank lines]
length                ; double [+0x48]  (sum of children)
bbox_min              ; point  [+0x28]
bbox_max              ; point  [+0x38]
start                 ; point  [+0x60]
end                   ; point  [+0x70]
n                     ; int    number of children (4-byte pointer vector at [+0xa8])
  repeat n: ####Group elem NO:i
            <CGlyContour body>   ; exactly the type-8 body of §6.1 (precision line first),
                                 ; children are ALWAYS CGlyContour (reader allocates 0x280 bytes
                                 ; and calls the CGlyContour ctor 0x10064e70 at 0x100769f7..0x10076a20)
layer                 ; int [+0xc]   (SetLayer propagated to children via slot 18)
int58                 ; int [+0x58]
```
Note there is no `<Glyphs>`/`<Crafts>` of the group's own: each child carries its own crafts.

### 6.3 Type 10 — `CGlyText` (EVIDENCE: Write `0x100a1fe0`, Read `0x100a4030`)

```
[version 2..4: 5 blank lines]
position              ; point  [+0x120]  text insertion point
d130                  ; double [+0x130]  }  three text parameters; ctor defaults not recovered.
d138                  ; double [+0x138]  }  INFERENCE (low): height, width-ratio/rotation, char spacing
d140                  ; double [+0x140]  }
text                  ; wstring [+0xb8]  (GBK bytes)
<Text para>
font                  ; wstring [+0xd8]  default "宋体" (SimSun) from 0x1010f838
font_d0               ; double  default 1.0
font_d1               ; double  default 20.0  (0x1010f830)
font_d2               ; double  default 0.0
<End Text para>
if version >= 4:  <CGlyGroup body>   ; the rasterised outline (contours), layer, int58
else:             layer ; int
                  int58 ; int          (and the outline is regenerated from the font, 0x100a3b00)
```
Defaults observed in the reader prologue (`0x100a4060…0x100a40a5`): local font = L"宋体", doubles 1.0 / 20.0 / 0.0 — used when a `<Text para>` line is missing/invalid.

### 6.4 Type 11 — `CGlyScan` (EVIDENCE: Write `0x10091e80`, Read `0x100948e0`)

```
<CGlyGroup body>          ; the original contours (children)
<Scan path>
[version 2..4: 5 blank lines]
n                         ; int
  repeat n: ####Path:i
            <CGlyContour body>   ; generated fly-cut path (new CGlyContour, 0x100949d8)
<End Scan path>
```
After reading, `length` is recomputed as the sum of the path lengths (`0x10094a44…0x10094ac0`).

### 6.5 Type 12 — `CGlyContourEx` (EVIDENCE: Write `0x100704e0`, Read `0x10072270`, element reader `0x10070330`)

```
<CGlyGroup body>
n                          ; int, number of 0x48-byte "link info" records (vector at [+0xb8])
  repeat n: ####ContourEx link info:i
            int0           ; int    [e+0x00]
            int4           ; int    [e+0x04]
            d8             ; double [e+0x08]
            d10            ; double [e+0x10]
            d38            ; double [e+0x38]
            d40            ; double [e+0x40]
            pt18           ; point  [e+0x18]  (reader parses it only if int0 == 1, otherwise skips the line)
            pt28           ; point  [e+0x28]  (parsed only if int4 == 1)
```
INFERENCE (low): "link" records describe how sub-contours are joined (bridge/`CBridge`, micro-joint `微连`, or shared-edge links); `int0/int4` are validity flags for the two points. No sample; meaning of the doubles unknown.

---

## 7. Glyph records (types 1–7)

Glyph type ids (**EVIDENCE:** jump table at `0x10059d50` → `1→0x10059cd5` (ctor `0x100892b0`, 0x98 bytes, `CEditablePoint`), `2→0x10059bc6` (`0x1008a0b0`, 0xd8, `CEditableSegment`), `3→0x10059bfd` (`0x1007dbb0`, 0x138, `CEditableArc`), `4→0x10059c34` (`0x1007faf0`, 0x108, `CEditableCircle`), `5→0x10059d08` (`0x10081660`, 0x148, `CEditableEllipsArc`), `6→0x10059c6b` (`0x10084c10`, 0xd0, `CEditableLwpoly`), `7→0x10059ca2` (`0x1008cec0`, 0x178, `CEditableSpline`)). Class identity of each constructor is proven by the vtable pointer it stores (e.g. `0x10084c40: mov [eax],0x10112a84` = `CEditableLwpoly` vtable).

All glyph geometry starts at object offset `+0x60`.

| id | class | Lines (in order) | Semantics |
|---|---|---|---|
| 1 | `CEditablePoint` | `x,y` | a point (drill/pierce). |
| 2 | `CEditableSegment` | `x0,y0` / `x1,y1` | straight line from p0 to p1 (`[+0x60]`, `[+0x70]`). |
| 3 | `CEditableArc` | `cx,cy` / `r` / `a0` / `a1` | circular arc: centre `[+0x60]`, radius `[+0x70]`, start angle `[+0x78]`, end angle `[+0x80]`, **radians**, measured CCW from +X. Start point = `c + r·(cos a0, sin a0)`, end = `c + r·(cos a1, sin a1)` (**EVIDENCE:** `0x10001410` calls `_CIcos`/`_CIsin` (imports at IAT `0x1010f2f0`/`0x1010f2ec`) directly on `a0`,`a1` with no degree conversion; `0x1007ebb0` uses `(a1-a0)·r` as the arc length). Sweep = `a1 − a0`; positive = counter-clockwise, negative = clockwise (INFERENCE high; the DXF importer class `CDxfArc2d` feeds DXF arcs which are CCW-by-definition). |
| 4 | `CEditableCircle` | `cx,cy` / `r` | full circle, centre `[+0x60]`, radius `[+0x70]`. Verified: `2πr` equals stored contour length in 3 samples. Natural direction: starts at angle 0 and runs CCW (INFERENCE medium, from arc convention; start point in sample `Work1/1.chf`: `7.116472,13.540320` = centre `(5.921416,13.540320)` + `(r,0)` ✔ confirms start at angle 0). |
| 5 | `CEditableEllipsArc` | `cx,cy` / `mx,my` / `ratio` / `t0` / `t1` | DXF-style ellipse arc: centre `[+0x60]`, **major-axis vector relative to the centre** `[+0x70]` (semi-major length `a = hypot(mx,my)`, rotation `atan2(my,mx)`), `ratio = b/a` `[+0x80]`, start/end parameter (radians) `[+0x88]`,`[+0x90]`. Point(t) = `c + cos t·(mx,my) + sin t·ratio·(−my,mx)`. **EVIDENCE:** evaluator `0x10081040…0x1008126e`: `_hypot([+0x70])`, angle-of-vector helper `0x10009350`, `a·ratio` (`0x100810ee`), `cos/sin([+0x88])`, `cos/sin([+0x90])`, rotation by `cos/sin(atan2)` at `0x10081240…`. Confidence high. |
| 6 | `CEditableLwpoly` | `closed` / `n` / then n × (`x,y` / `bulge`) | lightweight polyline: `closed` int (`[+0x70]`, 1 = closed, the last vertex connects back to the first), `n` vertices; each vertex is a 24-byte record `{double bulge @0; point @8}` written **point first, bulge second**. `bulge = tan(θ/4)` of the arc from this vertex to the next (DXF convention, negative = clockwise; 0 = straight). **EVIDENCE:** sample `Work1/3.chf`: 6 vertices with bulges `0, −0.414214, 0, −0.414214…` (`tan(22.5°) = 0.414214` → 90° arcs) form a stadium whose recomputed length `9.673958` matches the stored length exactly only if the bulge of the *last* vertex applies to the closing segment. |
| 7 | `CEditableSpline` | `int1` / `int2` / `n` / n × `x,y` / `nk` / nk × `knot` | cubic B-spline (NURBS without weights): `int1 = [+0xb4]`, `int2 = [+0xb0]` (unknown flags, see Open questions), `n` control points (`[+0x70]` vector of 16-byte points), `nk` knots (`[+0x60]` vector of doubles). Reader (`0x1008f30a…0x1008f31a`) **requires `n ≥ 4` and `nk == n + 4`** → degree 3 (clamped or unclamped depends on the knot values). The debug dump `SRC/linkFlyLine_pathGlys.txt` lists `7, 2.65861` among `2, 25` entries (type id, length), i.e. splines are used for the fly-cut link curves. |

The per-glyph `direction` (`1`/`-1`, §6.1 line 10) is stored **outside** the glyph, in the contour's element list, so a glyph is never rewritten when the contour is reversed.

---

## 8. Trailer (after `<End Graphs>`)

| Line | Type | Where it comes from | Sample | Meaning |
|---|---|---|---|---|
| `trailer_bool` | bool | 3rd argument of `WriteGraphs` (`0x100a52a0`) — passed in from the save entry `0x100ddf89` which gets it from its own caller (MainApp through `ICADModule`); the single-graph exporter `0x100e5159` passes constant `0` (`push 0; push 0; fldz` at `0x100e5176…0x100e5188`, verified). On load it is returned to MainApp through an out-pointer (`0x100ab51c`). **Verifier:** the save entry's frame (`ret 0x28`, `cmp [ebp+0x1c],8` SSO test) shows the signature `Save(std::string path /*0x1c bytes by value*/, bool [ebp+0x24], double [ebp+0x28])`; MainApp's autosave path builder is at `0x45907a` (`L"autosave.chf"` + `L"\File\"`) and the document save wrapper at `0x4253e0` (`this+0xf3cc`), but the actual argument values were not traced. | `0` | Unknown (see Open questions). |
| `trailer_double` | double | 4th argument, same path; exporter passes `0.0`. | `0.0` | Unknown. |
| `trailer_pt1`, `trailer_pt2` | point | `[CCADModule+0x9cd0]`, `[+0x9ce0]`; if the graph list is empty they are reset to the default pair at `0x10113278` (`0x100a5353…`). Read straight back into the same members. | `0,0` / `0,0` | INFERENCE (medium): document-level reference points, e.g. the job's origin/anchor ("dock point", cf. `COpDockPtCmd`) and the last machine position; both are `0,0` in every sample. |

---

## 9. Version differences (reader logic; **EVIDENCE:** version tests `add eax,-2; cmp eax,2; ja` at `0x100a9915`, `0x1006b512`, `0x1006b647`, `0x1006b6ef`, `0x10076946`, `0x1009493f`, `0x100a40ab`; `cmp ebx,1` at `0x1006b86f`; `cmp ebx,2` at `0x1006b95d`; `cmp eax,4` at `0x100a418e`)

| Feature | v1 | v2 | v3 | v4 | v5 (current writer) |
|---|---|---|---|---|---|
| 5 blank lines after `<Begin Graphs>` | – | ✔ | ✔ | ✔ | – |
| 10 blank lines after `<Glyphs>` | – | ✔ | ✔ | ✔ | – |
| 3 blank lines after each glyph | – | ✔ | ✔ | ✔ | – |
| 20 blank lines after `<Crafts>` | – | ✔ | ✔ | ✔ | – |
| 3 blank lines at start of a group body | – | ✔ | ✔ | ✔ | – |
| 5 blank lines after `<Scan path>` / at start of text | – | ✔ | ✔ | ✔ | – |
| `lead.arc_radius` line | – | ✔ | ✔ | ✔ | ✔ |
| `<coolPos Para>` block | – | – | ✔ | ✔ | ✔ |
| Text stores its outline group | – | – | – | ✔ | ✔ |

The blank lines in v2–v4 are simply consumed (`ReadToken` in a counted loop); they were presumably reserved string fields that the writer emitted as `WriteStr(NULL)`. The four version‑4 samples confirm the counts (5/10/3/20).

---

## 10. Coordinate system, units, order, layers — what a job means

* **Units:** millimetres. **EVIDENCE:** the autosave job spans x 395…1247, y 302…601 on a 1300×900 mm bed; lead-line default 5 (mm), angle 90 (deg); circle radii 1.195 / 47.42.
* **Frame:** absolute canvas coordinates, origin bottom-left, +X right, +Y up (INFERENCE **medium**, downgraded by verifier: the arc/bulge conventions only prove *internal consistency* — the stadium in `Work1/3.chf` is traversed clockwise under Y-up/DXF-sign and counter-clockwise under Y-down/mirrored-sign, and either pair reproduces the stored length 9.673958 (verifier recomputation: 9.673961). What favours Y-up is that DXF entities are imported 1:1 through the `DxfParse::CDxf*2d` classes without a sign flip being visible in the samples, and that the machine coordinates in `autosave.chf` (y up to 600.9) are positive. The "OpenGL canvas is y-up" statement was not verified.) The SVG renderer therefore flips Y.
* **Cut order:** graphs are cut in file order (`####graph NO:1…N`); the `CAutoSort`/`COpAutoSortCmd` commands rewrite this order. Inside a contour glyphs are cut in list order, each in the direction given by its `direction` flag; `start`/`end` cache the resulting first/last points ("Show path start" `mf41`, "Adjust Start Pt" `mf604`, "Reverse" `mf94` = `COpReverseContourCmd`).
* **Layer:** `layer` int per contour (children of groups carry their own). Layer→process parameters mapping is in `File/BkLayerPara.xml` (not part of this report).
* **Lead-in/out:** only *parameters* are stored (`<GuideCurve Para>`); the lead geometry itself is regenerated at load (`0x1005c510` is called with `(0,0,0,0)` when the `flag` argument of `Read` is false; the `CGuideCurve` object is owned by the contour at `[+0x1a0]`/`[+0x100]`). No lead-out parameters exist in the file.
* **Compensation:** `compensate_type/width` per contour; the offset contour is not stored (regenerated).
* **Micro joints / bridges / over-cut:** not found as explicit fields in type 8; candidates are `double170/double188` and the type‑12 link records. The MainApp parameters (`GRP.MicroLinkLength`, `GRP.AutoMicroLinkNum/Step/Type`, `GRP.LoopGapOverCutLength`) are global settings, not per file.
* **Groups/arrays:** an array copy is stored as independent graphs (autosave: 24 separate segments, no array record; `File/BkManuPara.xml` has `ArrayRowNum="8" ArrayColNum="3"`, matching the 3×8 layout). "Group" (`COpGroupGraphCmd`) = type 9. **Nesting results are not in `.chf`**: `CNestResult`/`CSheetInfo`/`CPartInfo` have their own writer (`0x100ccd20`). **Verifier correction:** its marker strings are *narrow* ASCII written with the same `WriteStr`/`WriteStrInt` (`0x100d9860`/`0x100d9aa0`), not wide: `.rdata` file offsets `0x112440…0x1124d4` hold `Result Graphs`, `Result_`, `<Begin NestResults>`, `<End Sheets>`, `Sheet Graphs`, `Sheet_`, `<Begin Sheets>`, `<End Parts>`, `Part Graphs`, `Part_`, `<Begin Parts>`; only the payload names go through `WriteWStr` (`0x100ccdf7`). It is a separate grammar inside the same `scFlie` container, produced by `SmartNest.dll`/`AutoNest.dll` (not analysed here).
* **Bitmaps:** no bitmap/raster glyph exists in the format (no such class; `CGlyScan` is vector fly-cut, `CWglFontBitmap` is only OpenGL font rendering).
* **PWM curve / cool points:** per-contour arrays (§6.1.1); empty in every sample.

---

## 11. The parser (`tools/chf_parse.py`) and proof

* Implements the tokenizer and every reader above (functions carry the DLL addresses they mirror). Strict about markers (the DLL is not), lenient about empty numeric tokens (like the DLL).
* `--outdir DIR` writes `DIR/<dir>_<name>.json` (full tree), `.svg` and `.png` (own stdlib rasterizer, since no SVG rasterizer is installed on this machine).
* `--check` recomputes, from the parsed geometry (arcs, bulges, ellipses and splines are flattened at 0.002 mm chord step, direction flags applied), the contour **length, bbox, start, end** and compares with the stored values. Result for all 8 samples: **OK** (all within 1e‑3 mm). This validates: segment/circle/lwpolyline layouts, the bulge convention (sign and assignment to the *following* segment, closing segment included), circle start angle 0, y-up interpretation (bbox), and the direction flag.
* Renders were viewed and match expectation: `File_autosave.png` shows a 3×8 grid of 24 tilted 284 mm lines; `Work1_3.png` a stadium; `Temp_tempGraph.png` a circle (`Work1_2` — the rectangle — was verified numerically by `--check`, not viewed).

Run:
```
python3 tools/chf_parse.py --check --outdir tools/out $(find SRC -name '*.chf')
```

---

## 12. Fields whose meaning is still uncertain

| Field | Location | What is known |
|---|---|---|
| `int58` | contour/group line after `layer` | default 1; only its parity is used: offset sign when `compensate_type ∉ {2,3}` (`0x1006b041`), lead side in `0x1005cea2` / `0x1006ea6f`. No setter found in the DLL. Likely a side/direction parity. |
| `compensate_type` values 2 vs 3 | Crafts | set from a bool "positive" (`阳切`/outside) cut → 3, else 2; `0x1006b027` maps 2 → −1, 3 → +1. Geometric meaning of the sign not proven. |
| `pwm_nodes` pair semantics | Crafts | (ratio, value) is a guess; no sample has nodes. |
| `pwm_close_pos_ratios` | Crafts | suggested by `closePwmPosRatios.txt` debug dump (46 lines, CRLF, 0.0165935…0.983406, verified) but not proven from code — the dump's producer was not located. |
| `double170` | Crafts | path ratio (proven: multiplied by contour length in `0x1005b390`); *which* ratio (lead start position = `LeadPosPrecent`?) is inference (medium). |
| `double188` | Crafts | added to the contour length (`0x10060046`); over-cut length is inference (medium-low). |
| `lead.type` enum values | Crafts | 0 = none; `2` is used in `File/BkManuPara.xml` (`GuideLineType="2"`, with `GuideArcRadius="2"`); full enum unknown. |
| `lead.flag` | Crafts | side of the lead-in relative to path direction; computed as `orientation XOR positive` (`0x1006b460`) and flipped by Reverse (medium). |
| `cool_pos` unit | Crafts | ratio vs mm undetermined. |
| `trailer_bool`, `trailer_double` | trailer | provided by MainApp; always 0 / 0.0 in samples. |
| `trailer_pt1/pt2` | trailer | document reference points (medium). |
| text `d130/d138/d140`, `font_d0/d1/d2` | type 10 | defaults 1.0/20.0/0.0 for the font triple; height/width/spacing/italic/bold guesses. |
| spline `int1/int2` | glyph 7 | unknown flags (`[+0xb4]`,`[+0xb0]`); reader branches on the `flag` argument to `0x1008ef70`/`0x1008ef40` after reading. |
| ContourEx link records | type 12 | all fields unknown; two validity-flag ints and two points. |
| Arc/ellipse **direction for negative sweep** | glyph 3/5 | inferred from DXF conventions, no sample with arcs (type 3) or ellipses (type 5) exists in the package. |

---

## 13. Open questions

1. Are there sample `.chf` files with arcs (type 3), ellipse arcs (5), splines (7), text (10), scan (11), contour-ex (12) or non-empty crafts arrays anywhere on the machine (e.g. other `Graph/Work*` dirs, USB sticks)? They would let `--check` validate those readers too.
2. What do MainApp's save/load callers pass for `trailer_bool`/`trailer_double`? (Requires tracing the `ICADModule` vtable call in MainApp.exe.)
3. Exact enum of `lead.type` and of `compensate_type` (2/3) — best answered by running the Windows program once with a lead-in / compensation set and diffing `autosave.chf`.
4. Meaning of `double170`/`double188` and of `int58` (same experimental approach: toggle "Reverse", "Over cut", "Micro joint" in the UI and diff).
5. Whether `_tempSource.chf` / `tempGraph.chf` (single-graph export, bool 0/double 0.0) is also the format handed to the Report/preview generator.
6. The nesting-result file written by `0x100ccd20` (`<Begin NestResults>`, `<Begin Sheets>`/`<End Sheets>`, `<Begin Parts>`/`<End Parts>`, `Result_`/`Sheet_`/`Part_` + index — narrow markers, wide payload names) — separate format, separate report.
7. *(added by verifier)* Who writes `[+0x58]` (`int58`)? No setter exists in CADModule.dll; either MainApp pokes it through an `IGraph` accessor not identified here, or the value is effectively constant 1 in practice. Diff `autosave.chf` after "Reverse"/"Positive" commands in the Windows UI.
8. *(added by verifier)* `File/AutosaveParam1.ini`/`AutosaveParam2.ini` (five ints in the `scFlie` container) and `File/ManuContour.dat` (24 indices) — their producers in MainApp were not located; the "resume position in µm" and "manual cut order" readings are unproven.
9. *(added by verifier)* Which MainApp routine produced `SRC/closePwmPosRatios.txt` and `SRC/linkFlyLine_pathGlys.txt` (debug dumps in the program root) — they date the fly-cut/PWM features but were not tied to code.

---

## 14. Implications for the Linux port

**Must replicate exactly (for interchange with existing jobs):**
* The `scFlie`/version/`eof` container, CRLF line endings, `%f` (6 decimals) number formatting, the `0.0` zero special case and `%12.10f` for 1e‑10…1e‑6, `%f,%f` points, `1`/`0` bools, GBK-encoded strings — otherwise files written on Linux will not round-trip through the Windows program (and vice versa).
* Version 5 grammar of §5–§8 for writing; reading of versions 1–5 per §9 (cheap: it is only skip counts and three optional lines/blocks).
* The graph/glyph type ids (8–12, 1–7), the per-glyph direction flag, DXF bulge semantics, radians for arc angles, DXF ellipse parametrisation, cubic B-spline with `n+4` knots.
* Derived quantities (`length`, bbox, `start`, `end`, `closed`) must be recomputed on write (the Windows reader trusts `length` only for the ≤0.01 degenerate check, but MainApp displays it).

**Can be replaced by existing Linux/open-source components:**
* The `.chf` geometry maps 1:1 onto DXF entities (POINT, LINE, ARC, CIRCLE, ELLIPSE, LWPOLYLINE with bulge, SPLINE degree 3). A port can keep its internal model in DXF terms and use **`ezdxf`** (import/export, `ezdxf.math.bulge_to_arc`, `BSpline`) or **`libdxfrw`/`libdxf`** for CAD interchange, and only needs the thin `.chf` reader/writer from `tools/chf_parse.py` for compatibility with existing job files.
* Flattening/offsetting/ordering: `shapely` (offset = kerf compensation, `buffer(width, single_sided=True)`), `pyclipper` (robust offsets), `svgpathtools`/`ezdxf.path` (arc/spline flattening) instead of `CSegOffset`/`CContoutSmooth`.
* Text: the outline group (type 10, v≥4) is just contours; regenerate with FreeType (`freetype-py`) + `fontTools` from the stored font name/size (default 宋体 → substitute Noto Serif CJK).
* Rendering: any 2D canvas (Qt/Cairo) — the format carries no styling; layer colour comes from the layer table.

**Design notes for the port:**
* Keep the crafts block as an opaque-but-typed struct (all fields, even the unknown ones) so files re-saved by the Linux program keep the Windows program's values.
* Store the direction flag at the contour-element level as the original does; do not bake it into the glyphs.
* The reader in the DLL does not verify marker names; a Linux implementation should verify them (as the parser does) to fail fast on corrupt files, but must not *require* the blank lines of v2–v4.

---

## Verification notes

Adversarial re-check of this report against the primary files (`SRC/File/*.chf`, `SRC/Graph/Work*/*.chf`, `SRC/Module/CADModule.dll`, `SRC/MainApp.exe`, `SRC/Lang/lang.txt`, `SRC/File/BkManuPara.xml`, `SRC/Report/*`). Every address quoted below was re-disassembled with `objdump -d -M intel` and read again; every file fact was re-run (`md5sum`, `file`, `cmp`, `cat -A`, `xxd`).

### What was checked and confirmed as stated

| # | Claim | How it was re-verified |
|---|---|---|
| 1 | Container `scFlie` / int version / `eof`, CRLF, one value per line | `cat -A` of all 8 samples; `0x100da450` pushes `0x10113fec` (= `scFlie`) into `WriteStr` and `_wfopen_s(L"wb")` (`0x10113ff4`); `0x100d9a30` pushes `0x10113f9c` (= `eof`); `mov [esi+0x970],5` at `0x100e5662`; `OpenForRead` compares the first token with `scFlie` (error 5 at `0x100da2ea`) and the last three non-trailing bytes with `e`,`o`,`f` (error 4 at `0x100da333`). |
| 2 | All `.chf` code lives in CADModule.dll | `grep -l 'Begin Graphs' -r SRC` hits only the `.chf` samples and `Module/CADModule.dll`; MainApp.exe holds only `scFlie`/`cAscFlie` (narrow, `0x3c3544`/`0x3c3526`) and UTF-16 file names. `.rdata` offsets `0x110344…0x1103f0`, `0x111bd0…0x111bf0`, `0x1127a0…0x1127f4` confirmed with `strings -t x` and `xxd`. |
| 3 | Top-level grammar and trailer | `WriteGraphs 0x100a52a0`: `WriteStr(0x101133f0="<Begin Graphs>")`, `WriteInt`, loop `{WriteStrInt(0x101133e0="####graph NO:"), WriteInt([g+8]), g->vt[24]}`, `WriteStr(0x101133d0="<End Graphs>")`, `WriteBool`, `WriteDouble`, `WritePoint([this+0x9cd0])`, `WritePoint([this+0x9ce0])` (defaults from `0x10113278` = `{0.0,0.0}` when the list is empty). Reader `0x100ab4c5…0x100ab54b` mirrors it. |
| 4 | Type ids and classes | Jump tables read from the file bytes: `0x100a4844` → `0x100a4728/4769/47cf/479e/4800` (index = type−8, `cmp eax,4` at `0x100a4718`) → ctors `0x10064e70/0x100760e0/0x100a26b0/0x100933c0/0x10071a20`; `0x10059d50` → 7 stubs (index = type−1, `cmp eax,6`) → ctors `0x100892b0/0x1008a0b0/0x1007dbb0/0x1007faf0/0x10081660/0x10084c10/0x1008cec0`. The vtable each ctor stores was resolved through the RTTI complete-object-locator to `.?AVCGlyContour@@`, `CGlyGroup`, `CGlyText`, `CGlyScan`, `CGlyContourEx`, `CEditablePoint`, `CEditableSegment`, `CEditableArc`, `CEditableCircle`, `CEditableEllipsArc`, `CEditableLwpoly`, `CEditableSpline` — all match. Object sizes 0x280/0xb8/0x148/0xd0/0xd8 and 0x98/0xd8/0x138/0x108/0x148/0xd0/0x178 confirmed. |
| 5 | `CGlyContour` body and field offsets | `0x1005ab20` writes `[+0xc8]`, `<Glyphs>`, `[+0x48]`, `[+0x28]`, `[+0x38]`, `[+0x60]`, `[+0x70]`, count of `[+0xa8]` (8-byte elements), per element `WriteStrInt("####Gly: ")`, `WriteInt([e+4])`, `WriteInt([glyph+8])`, `glyph->vt[24]`, `<End Glyphs>`, `[+0xc]`, `[+0x58]`, `<Crafts>`. Reader `0x1006b4e0` is the exact mirror; vtable slots 23/24 of `0x10111df4` confirmed by dumping the table. |
| 6 | Crafts block and version gates | Writer `0x1005ac37…0x1005ae27` and reader `0x1006b70a…0x1006b9ca` re-read field by field; `cmp ebx,1` (`0x1006b86f`) gates the arc-radius line, `cmp ebx,2` (`0x1006b95d`) gates `<coolPos Para>`; ctor defaults at `0x10064f51…0x10064fa0`. PWM pairs are written interleaved from two parallel arrays `[+0x108]`/`[+0x118]`. |
| 7 | Direction flag ±1 | `cmp [ecx+eax*8+4],-1` → `fsubrp` (1−t) at `0x1005b438`/`0x1005b4a8`; `fild [edx+ebx*8+4]` at `0x1005b4db` (then `fmul 0.05`). |
| 8 | Layer applied via slot 18 | `0x1005a8e0` iterates `[+0xa8]` calling slot 18 on each glyph, stores `[+0xc]`, then calls slot 18 on `[+0x100]` if non-null; the reader calls it at `0x1006b6dd…0x1006b6e3`. |
| 9 | Glyph geometry | Arc: `0x10001410` = `c + r·(cos a, sin a)` for both angles via the `_CIcos`/`_CIsin` thunks (`0x10100470`/`0x10100476`, IAT `0x1010f2f0`/`0x1010f2ec`), and `0x1007ebc8…0x1007ebd5` = `|a1−a0|·r`. Ellipse: `_hypot` (IAT `0x1010f2fc`), `fmul [esi+0x80]` (ratio), `0x10009350` (angle helper), 2π constant at `0x1012d918`. Lwpoly writer `0x10083e70`: `WriteInt`, `WriteInt`, then `WritePoint(rec+8)`, `WriteDouble(rec+0)`. Spline reader `0x1008f2a6…0x1008f31a`: 3 `ReadInt`, `ReadPointArray(n)`, `ReadInt`, `ReadDoubleArray(nk)`, then `n ≥ 4` and `nk == n+4`. Numerics re-done independently: stadium length 9.673961 vs stored 9.673958; rectangle 2.279 × 1.696; circle start = centre + (r,0) in `tempGraph.chf` and `Work1/1.chf`; `autosave.chf` x∈[395.33,1247.12], y∈[302.91,600.93]. |
| 10 | Version-dependent blank lines | Counted loops: `mov esi,5` @`0x100a991d`, `mov ebx,0xa` @`0x1006b51a`, `mov ebx,3` @`0x1006b64f`, `mov ebx,0x14` @`0x1006b6f7`, `mov edi,3` @`0x1007694b`, `mov ebx,5` @`0x10094944`, `mov ebx,5` @`0x100a40b7`, each guarded by `add eax,-2; cmp eax,2; ja`. `Work1/3.chf` shows exactly 5/10/3/20 blank lines. |
| 11 | Parser self-check | `python3 tools/chf_parse.py --check` re-run: `check: OK` for all 8 files. |
| 13 | `0x1006b460` semantics | Confirmed and made precise (see §6.1.1). |
| 15 | Report previews | `cmp Report/111.chf.jpg Report/222.chf.jpg` identical; `find` finds no `rpt.chf`/`111.chf`/`222.chf`. |

Also confirmed: `WriteDouble` thresholds (constants `0x10113fd0` = 1e-10, `0x10111dd0` = 1e-6; `"%d\r\n"` with 0 in buffered mode, `"0.0\r\n"` in file mode, `"%12.10f\r\n"`, `"%f\r\n"`), `ReadBool` = `strcmp(token,"1")` (`0x10113fe8`), `ReadInt`/`ReadDouble` validators (`0x100d97b0`: `[0-9-]`; `0x100d9800`: `[0-9.-]` with ≤ 1 dot; empty token valid), the text reader defaults (`L"宋体"` at `0x1010f838`, 1.0 / 20.0 (`0x1010f830`) / 0.0 at `0x100a4095…0x100a40a3`) and the text writer order, group/scan/contour-ex writer and reader orders, all lang.txt translations quoted (file is UTF-16LE, not GBK).

### What was changed in the document

1. **§5 — refuted:** a graph failing the "length < 0.01 ⇒ single point" test is deleted and skipped, the load does **not** abort; the comparison is strict `<`, and the group case additionally requires the single child to contain one point glyph.
2. **§10 — refuted:** the nesting-result writer's markers are narrow ASCII (`<Begin NestResults>`, `<Begin Sheets>`, `<Begin Parts>`, …), not wide strings.
3. **§6.1 — corrected:** `precision` `0.1` also occurs in a v5 file (`tempGraph.chf`); it is a constructor argument, not version-dependent.
4. **§6.1.1 — corrected:** the 90°/5 mm lead defaults are copied from the constant block at `0x101119e8`, not set as literals in `0x10061470`; `lead.flag` is *computed* (`orientation XOR positive`), not just toggled.
5. **§1/§10 — downgraded:** Y-up frame from "high" to "medium" (the file evidence is symmetric under a Y-flip plus bulge-sign flip).
6. **§6.1/§12 — refined `int58`:** only its parity is consumed; type 2/3 map directly to ∓1; no setter exists in the DLL.
7. **Added:** `[+0xdc]` orientation flag and `Reverse` (slot 44) behaviour; `double170` is a path ratio (probably `LeadPosPrecent`), `double188` is added to the length (probably over-cut); `ICADModule::Save(std::string, bool, double)` signature and the MainApp call sites `0x45907a`/`0x4253e0`; tokenizer push-back flag, EOF-without-newline behaviour, dead `=` branch, signed trailing-byte skip in the `eof` check; the other `scFlie` files (`PithCompensate.pcf`, `AutosaveParam1/2.ini`, `ManuContour.dat`); `Report/report.txt` naming `111.chf`/`222.chf` with 15.45 × 34.34 mm; `File/BkManuPara.xml` GRP attributes (`GuideLineType="2"`, `GuideArcRadius="2"`, `LeadPosType`, `LeadPosPrecent`, `OutsideIsNegativeSide`, `ArrayRowNum="8" ArrayColNum="3"`).

### What remains uncertain

* Values MainApp passes as `trailer_bool`/`trailer_double` (the wrapper chain `0x45907a → 0x4500c0 → 0x4253e0` was located but not followed to the `ICADModule` vtable call).
* Which of `compensate_type` 2/3 offsets to the geometric left/right; the exact `lead.type` enum beyond {0, 2}; the meaning of `LeadPosType`.
* `double170` = lead start ratio and `double188` = over-cut length are medium/medium-low inferences from data flow only.
* `int58`'s writer, the `AutosaveParam*.ini` and `ManuContour.dat` producers, and the spline flag ints remain unidentified.
* No sample exercises arcs, ellipses, splines, text, scan, contour-ex, PWM nodes or cool points; those readers are proven only by disassembly.
