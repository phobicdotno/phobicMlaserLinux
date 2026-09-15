# A7 — File formats: `.enc` / `.aut` task containers, `scFlie` state files, `NormalExit`, `TotalReport.txt`, `.pmf`, `calib.csv`, `pwmCompensation.txt`, `logo.bmp`

Task: close **O14** (`.enc`/`.aut` layout, `NormalExit` write path, `AutosaveParam` event, `TotalReport` row trigger) and 99-gaps §4 items 1 (`.pmf`), 2 (`calib.csv`), 8 (`pwmCompensation.txt`), 9 (`logo.bmp`) from `MainApp.exe` (+ `NCModule.dll` for the `.olpf` section) alone.

Conventions as in `00`: **EVIDENCE** = observed bytes/instructions (file, VA, string); **INFERENCE** = interpretation with a confidence. All VAs are `MainApp.exe` (image base 0x400000; `.rdata` VA 0x7bf000 = file 0x3bde00) unless prefixed `NC:` (`NCModule.dll`, base 0x10000000). Disassembly used: `.scratch/asm/MainApp.asm`, `.scratch/asm/NCModule.asm` (`objdump -d -M intel`). Helper scripts (scratchpad): `xs.py` (string → VA → code xrefs), `ann.py` (annotated slices: string literals and IAT names resolved inline), `fstart.py` (enclosing function + callers).

`G` below = the MainApp global parameter/state block returned by `0x5ff1b0` (`mov eax,0xa2efa0; ret`, EVIDENCE) — the same object 07/A1/A3 call `g`.

---

## 0. Summary (what a port needs)

| Item | Result | Status |
|---|---|---|
| `.enc` layout | plain concatenation of 5 files, each immediately followed by an ASCII marker, **no length fields, no encryption**: `olpi` `NEXCUT_OLPI_END` `olpf` `NEXCUT_OLPF_END` `jpg` `NEXCUT_JPG_END` `xml` `NEXCUT_LAYER_XML_END` `chf` `NEXCUT_CHF_END` | EVIDENCE §1 |
| `.aut` layout | same scheme, 4–6 sections: `chf` `TASK_GRAPH` `xml` `TASK_LAYER` `manu` `TASK_MANU` [`param1` `TASK_PARAM_ONE` `param2` `TASK_PARAM_TWO`] `isbreak` `TASK_IS_BREAK` | EVIDENCE §2 |
| readers | `.aut`: byte-by-byte accumulate, flush whenever the buffer *ends with* a marker (§2.3). `.enc`: **never read by MainApp** — it is uploaded to the NexCut HTTP service (`POST /NexCut/File/LoadFile`) | EVIDENCE §1.4 |
| `NormalExit` | `=0` written at **start-up** (`0x568365`, in the init routine `0x566ee0`); `=1` written by the **main-window destructor** (`0x459aa8`, `0x459820`) and by a **network-triggered exit** (`0x48f338`, only if `IsNetworkAlive()==NETWORK_ALIVE_LAN`). Axis positions (`XAxis…WAxis`, 0.001 mm) are written by `0x45cf80`, called right after the `=1` write | EVIDENCE §3 |
| `AutosaveParam1/2.ini` | written every **100 ms** by a `timeSetEvent` callback while `G+0x47cc == 2` (running); even counter → file 1, odd → file 2 | EVIDENCE §4.2 |
| `ManuContour.dat` | written by "SaveIndex" (`0x4368e0`) at job **start** (`CManuPanel::OnStartBtn` / `OnSimBtn` / resume chain) | EVIDENCE §4.1 |
| `TotalReport.txt` row | appended by the control-panel **poll handler** `0x568a50` on the stop transition; 10 fields in this order: name, `,%.2f×%.2f mm`, `,%Y-%m-%d %H:%M:%S`, `,%.2f m` cut, `,%.2f m` idle, `,%d` pierces, `,` + `gp146` duration, `,%.2f Sec`×3 | EVIDENCE §5 |
| `.pmf` | `scFlie` text container: `count`, then per step `type` + 6 ints; **writer only**, no reader in the binary; recommend **drop** | EVIDENCE §6 |
| `calib.csv` | `"%d, %d\n"` rows of the FTC (signal, height) table = A5 §2.3; viewer-only | EVIDENCE §7 |
| `pwmCompensation.txt` | **not referenced by any binary** (ASCII/UTF-16, all modules) → only the human source of `GRP.FiberScanFlyCompensateStr` | EVIDENCE §8 |
| `logo.bmp` | OEM logo overlay on the **splash screen** (`CBCGPDialog::OnInitDialog` `0x5a84e0`), 150×150 static next to `res\splash.bmp`; optional (`PathFileExistsW`) | EVIDENCE §9 |

---

## 1. `.enc` — "task package" for the NexCut server

### 1.1 Strings and functions (EVIDENCE)

| VA | string / function | role |
|---|---|---|
| `0x7db728` `"NEXCUT_OLPI_END"`, `0x7db768` `"NEXCUT_OLPF_END"`, `0x7db7a0` `"NEXCUT_JPG_END"`, `0x7db7d8` `"NEXCUT_LAYER_XML_END"`, `0x7db810` `"NEXCUT_CHF_END"` (ASCII) | each referenced **once**: `0x469129`, `0x46919a`, `0x46921a`, `0x46929a`, `0x46931a` — all inside the writer `0x468fe0` | markers |
| `0x7db738` `L"\_tempProcessInfo.olpi"`, `0x7db778` `L"\_tempManuItem.olpf"`, `0x7db7b0` `L"\_tempthumbnail.jpg"`, `0x7db7f0` `L"\_tempLayer.xml"`, `0x7db820` `L"\_tempSource.chf"` | section source files, pushed at `0x46913e`, `0x4691b5`, `0x469235`, `0x4692b5`, `0x469335` | |
| `0x468fe0` (`0x468fe0–0x4693c4`) | **`.enc` writer**; single caller `0x46310f` | |
| `0x4693d0` | append-one-section helper (ifstream → `ofs << ifs.rdbuf()` → `ofs << marker` → `remove(tempfile)`) | |
| `0x462c40` | **Export handler** (save dialog `L"enc文件(*.enc)\|*.enc\|\|"` at `0x462c7d`, default ext `L"enc"`, title `mf152` 保存加工文件 "Save File") | |
| `0x466c90` | **Open handler** for `L"Select file(*.nc;*.enc)\|*.nc;*.enc\|All Files (*.*)\|*.*\|\|"` (`0x466ccc`) — uploads, does not parse (§1.4) | |
| `0x49b8f0` | writes `_tempProcessInfo.olpi` + renders `_tempthumbnail.jpg` (§1.3) | |
| `NC:0x100349b0` | CNCModule slot 26 (A3 §1): when `G+0x46e0 != 0` writes `_tempManuItem.olpf` instead of queuing to the card | |

### 1.2 Writer `0x468fe0` — container layout (EVIDENCE)

```
469017 call 0x5ff1b0 ; add eax,0x4704   → G+0x4704 = file name (wstring)
469022 push L"\"      ; add eax,0x46e4   → G+0x46e4 = directory (wstring)
469036 call 0x4083b0  (concat)  → path = dir + L"\" + name
469062 call 0x408470  (wstring → std::string)
46908d call 0x49cfd0  std::ofstream(path, mode 0x20 = ios::binary, prot 0x40)
4690ba call ??7ios_base@std@@QBE_NXZ  (operator!) → on failure log "Error opening output file!" (0x7db6f0) via 0x5b6dd0 and return
469129 push "NEXCUT_OLPI_END"  … 46917d call 0x4693d0(ofs, G+0x46e4 + L"\_tempProcessInfo.olpi", marker)
46919a push "NEXCUT_OLPF_END"  … 4691f7 call 0x4693d0(…, L"\_tempManuItem.olpf", …)
46921a push "NEXCUT_JPG_END"   … 469277 call 0x4693d0(…, L"\_tempthumbnail.jpg", …)
46929a push "NEXCUT_LAYER_XML_END" … 4692f7 call 0x4693d0(…, L"\_tempLayer.xml", …)
46931a push "NEXCUT_CHF_END"   … 469377 call 0x4693d0(…, L"\_tempSource.chf", …)
4693a7 call 0x468bb0  (ofstream close/dtor)
```

Helper `0x4693d0` (identical twin of the `.aut` helper `0x4689e0`, read in full):
`std::ifstream in(path, binary)` (`0x4a40d0`, mode `0x20`); if `!in` → log `L"Error opening …"` (`0x7df450`) and **skip the section** (no marker written either); else `ofs << in.rdbuf()` (`0x468ab2`/`0x46949a` = `basic_ostream<char>::operator<<(streambuf*)`), `ofs << marker` (`0x4a07e0` = `operator<<(ostream&, const std::string&)`), `in.close()`, then `remove(fullpath)` (`0x469549` → IAT `remove`).

**Result (EVIDENCE):** the `.enc` byte stream is

```
<olpi bytes>NEXCUT_OLPI_END<olpf bytes>NEXCUT_OLPF_END<jpg bytes>NEXCUT_JPG_END<LayerPara.xml bytes>NEXCUT_LAYER_XML_END<chf bytes>NEXCUT_CHF_END
```

— no header, no lengths, no CRC, no encryption; markers are the bare ASCII strings without terminator. A section whose temp file is missing is silently dropped together with its marker (INFERENCE high: a consumer must therefore search markers, not assume all five).

### 1.3 The five sections and where they come from (EVIDENCE, handler `0x462c40`)

Sequence in the export handler after the save dialog:

1. `0x462dbf–0x462ea5`: full path split at the last `\` (`CString::ReverseFind(0x5c)` `0x462e45`): `G+0x4704` ← file name, `G+0x46e4` ← directory.
2. `0x462ed9`: `G+0x46e0 = 1` ("export mode"), `0x462ee5`: `G+0x47c1 = 0`.
3. `0x462ef2–0x462f21`: CAD virtual `[+0x1c4]` then `0x59ee50` — the pre-start routine (range check `mp120` 图形超出加工范围 "Graphics are out of range, continue?", then `0x596530` → **`0x4368e0` "SaveIndex"** → `ManuContour.dat`, then the planner). Because `G+0x46e0 != 0`, CNCModule slot 26 `NC:0x100349b0` (`cmp BYTE PTR [eax+0x46e0],0` at `NC:0x100349e9`) writes the FIFO record vector to **`_tempManuItem.olpf`** and returns without starting the card.
4. `0x462fbb`: modal dialog `0x530f50(0xa)`; `IDNO (7)` aborts.
5. `0x463014 call 0x49b8f0(statsRecord)`: writes **`_tempProcessInfo.olpi`** (`_wfopen_s` `L"wt+"`, `0x49ba69`) and renders the drawing into a bitmap → **`_tempthumbnail.jpg`** (`CreateCompatibleBitmap`/`BitBlt` `0x49bc34–0x49be53`, encoder `0x4a20d0`, path `0x49be79`).
6. `0x463019–0x4630a0`: `ICADModule::Save(dir + "\_tempSource.chf", 0, …)` via `[CAD+0xed88]` (`0x4abbb0`) → **`_tempSource.chf`** (native job, format in 03).
7. `0x4630a7–0x4630f7`: `CopyFileW(G+0x4104, dir + L"\_tempLayer.xml", FALSE)`. `G+0x4104` is assigned once, in "--- Init System param ---" (`0x4aff60`) right after `push L"\LayerPara.xml"` (`0x4b0031`) → **`_tempLayer.xml` = a verbatim copy of `File\LayerPara.xml`** (the current layer-parameter file, 02).
8. `0x46310f call 0x468fe0` (container), `0x46311a call 0x5880a0` = `CManuPanel::OnStopBtn` (log string `0x5880d5`) → resets the run state.
9. `0x463125–0x4632b0`: if the HTTP client object `G+0xf53c` (`0x488180`) exists and `0x406f90()` (connected) → `0x406430` (opens the file with `CreateFileW`/`GetFileSize`; prompts `A250606_0` 文件错误 "File Error!" / `A250606_1` "Only files smaller than 500MB are supported for transfer!") → dialog `0x530f50(0xc)` → `0x406fb0`/`0x406940`: WinHTTP `POST` to **`/NexCut/File/LoadFile`** (`0x406971`) with body `{"fileName":"<name>"}` (`0x7c4910`), answer parsed for `"state"` `true/false` (`0x406deb–0x406ec4`).

**`_tempProcessInfo.olpi` — text, 7 lines (EVIDENCE `0x49ba91–0x49bb97`, format strings at `0x7de464…0x7de490`):**

| line | `fprintf_s` format | value |
|---|---|---|
| 1 | `%.2f\n` | `rec+0x20` (double) — part width mm |
| 2 | `%.2f\n` | `rec+0x28` (double) — part height mm |
| 3 | `%.2fm\n` | `rec+0x30` (int) × 0.001 (`0x7d0eb8` = 0.001) — cut length (stored in mm) |
| 4 | `%.2fm\n` | `rec+0x34` × 0.001 — idle-move length |
| 5 | `%.2d\n` | `rec+0x38` — pierce count |
| 6 | `%d:%d\n` | `t = rec+0x3c / 10` (100 ms units → s): `t/60`, `t%60` — estimated duration min:sec |
| 7 | `%d\n` | `G+0x48f4` — small enum 0..9 set from a dialog field (`0x5aeb65`); meaning not identified (INFERENCE low: material/gas/process-count selector) |

`rec` = the same 0x98-byte statistics record used for `TotalReport.txt` (§5): the `.olpi` is the report row's numbers in file form.

**`_tempManuItem.olpf` — text (EVIDENCE `NC:0x10034a95–0x10034b78`, formats at `NC:0x10089cc0…`):**

```
Manu_Begin %d %d\n            ← (arg1!=0, arg2!=0) = the two bool args of slot 26 (A3: "bool,bool")
%d %d %d %d %d %d %d %d %d\n  ← one line per 28-byte Record (A3 §2), arg order = +0x18, u8 +0x16 (type), u16 +0x14 (freq), u8 +0x12 (duty), u16 +0x10, +0, +4, +8, +0xc
…
Manu_End\n
```
(`"Size %d"` at `NC:0x10089ccc` is written by another branch, `NC:0x10034b9f`, when `arg2 != 0` — not on the export path.) Record field meanings are in A3 §2. INFERENCE (high): the `.olpf` is the fully planned, card-ready motion/IO stream — the server side needs no planner.

**`_tempthumbnail.jpg`**: rendered by MainApp's own GDI drawing (`0x4a12c0`, `0x4a1380`), JPEG via `0x4a20d0` (`0x88c5f4` = encoder CLSID/format blob). Matches `Report/*.chf.jpg` (03).

### 1.4 There is no `.enc` reader in MainApp (EVIDENCE)

The five `NEXCUT_*` markers have exactly one xref each (all in the writer), no other module contains them (`strings -a`/`-e l` over every DLL: only `MainApp.exe` hits; `NCModule.dll` holds only `L"\_tempManuItem.olpf"`). The "Open .nc/.enc" handler `0x466c90` logs the directory and name (`0x466dee`, `0x466e22`) and hands the file to the same HTTP uploader (`0x466e2d…0x466f40`: `0x406f90` → `0x406430` → dialog `0x530f50(0xc)` → `0x406fb0`/`0x406940`). INFERENCE (high): `.enc` is an outbound package consumed by the NexCut server/HMI (07 §HTTP); the port does not need to parse one, only to write one if it wants to feed such a server.

---

## 2. `.aut` — offline task export/import ("Task" ribbon: `A250320_0` 任务 Task, `A250320_1` 导入任务 Import task)

### 2.1 Strings and functions (EVIDENCE)

Two copies of each marker exist; the first block is the writer's, the second the reader's:

| marker (ASCII) | writer VA / xref | reader VA / xref | section source (writer) → target (reader) |
|---|---|---|---|
| `TASK_GRAPH` | `0x7db620` / `0x46868b` | `0x7db8ac` / `0x4696ef` | `\File\tempGraph.chf` → `\File\Temp\tempGraph.chf` |
| `TASK_LAYER` | `0x7db62c` / `0x4686c7` | `0x7db8d4` / `0x469774` | `\File\tempLayer.xml` → `\File\Temp\tempLayer.xml` |
| `TASK_MANU` | `0x7db638` / `0x468703` | `0x7db900` / `0x469802` | `\File\tempManu.ini` → `\File\Temp\ManuContour.dat` |
| `TASK_PARAM_ONE` | `0x7db644` / `0x46874e` | `0x7db934` / `0x469890` | `\File\AutosaveParam1.ini` → `\File\Temp\AutosaveParam1.ini` |
| `TASK_PARAM_TWO` | `0x7db67c` / `0x4687ca` | `0x7db96c` / `0x46991e` | `\File\AutosaveParam2.ini` → `\File\Temp\AutosaveParam2.ini` |
| `TASK_IS_BREAK` | `0x7db6b4` / `0x468846` | `0x7db99c` / `0x4699ac` | `\File\tempIsBreak.ini` → `\File\Temp\tempIsBreak.ini` |

Functions: writer/export handler **`0x467c80`** (save dialog `L"aut file(*.aut)|*.aut|All Files (*.*)|*.*||"` `0x467cc3`, ext `L"aut"`, title `mf152`; section-append helper `0x4689e0`; ends `0x4689d0`). Reader **`0x469590`** (arg = path), called from the import handler **`0x466f90`** at `0x4673fe` (open dialog `0x467171`, title `mf150` 打开加工文件 "Open File").

### 2.2 Writer `0x467c80` — order and sources (EVIDENCE)

Preparation (all paths = `<exe dir>\File\` + name, `0x467f39`):
* `0x467f8b DeleteFileW(tempGraph.chf)`; `0x467fbc` `ICADModule::Save(tempGraph.chf)` (`0x4abbb0`).
* `0x467fec DeleteFileW(tempLayer.xml)`; `0x467ff7 cmp G+0x46d8,0` selects the layer list for the current laser type (`0x5ff760`/`0x5ff2d0` when 0, `0x5ffb50`/`0x5ff780` otherwise) and writes `tempLayer.xml` layer by layer.
* `0x468179–0x4682a4`: `tempIsBreak.ini` = `scFlie` container (§4.0) with **two ints**: `isBreak` (computed `0x468198–0x468227`: 0 only when `G+0x4380 == 0` and CAD `+0x148 == 0` and `G+0x47cc == 0` and (CAD `+0x13c == 0` or a CAD query is 0); else 1) and **`G+0x46d8` = laser type**.
* `0x4682a9–0x468520`: `tempManu.ini` = `scFlie` with `count` then the manual cut-order indices (vector `[ebp-0x62c]`, elements via `0x42a4e0`, loop `0x4684b0–0x4684ed`) and finally `G+0x47c1` as an extra int (`0x4684ef–0x468508`).

Container (`0x468518`): `std::ofstream(path, binary)`; on failure logs `"Error opening output file!"` (`0x7db5e8`). Then, via `0x4689e0(ofs, sourcepath, marker)` (same body as §1.2's helper, including `remove(source)` afterwards):

```
<tempGraph.chf>TASK_GRAPH<tempLayer.xml>TASK_LAYER<tempManu.ini>TASK_MANU
[ <AutosaveParam1.ini>TASK_PARAM_ONE<AutosaveParam2.ini>TASK_PARAM_TWO ]   ← only if isBreak ([ebp-0x681]) != 0, 0x468748 je
<tempIsBreak.ini>TASK_IS_BREAK
```

Note the writer deletes `AutosaveParam1/2.ini` after appending (the helper's `remove`), i.e. exporting a break-point task consumes the live break-point files. Every section is text (`.chf` = text 03; `.xml`; `scFlie` text) — a `.aut` is a pure ASCII/GBK text file.

### 2.3 Reader `0x469590` — marker-terminated streaming (EVIDENCE, read in full)

```
4695d2  std::wifstream in(path, binary)        ; on failure: wcerr << L"Error opening input file!" (0x7db844) << endl, return
469635  dir = <exe>\File\Temp\ ; GetFileAttributesW == -1 → CreateDirectoryW
4696ba… vector<pair<marker,outpath>> built IN THIS ORDER (0x4a0c00 pair ctor, 0x49df90 push_back):
        (TASK_GRAPH, tempGraph.chf) (TASK_LAYER, tempLayer.xml) (TASK_MANU, ManuContour.dat)
        (TASK_PARAM_ONE, AutosaveParam1.ini) (TASK_PARAM_TWO, AutosaveParam2.ini) (TASK_IS_BREAK, tempIsBreak.ini)
469a11  loop: in.get(wch) (0x7c0b80 basic_istream<wchar_t>::get) ; while (in) :
469a57      buf.push_back(wch)                                (0x4a4f30)
469a77      for each entry i:                                (0x49e110 size, 0x49e130 operator[])
469b01          if buf.size() >= marker.size() and
469b3d             buf.compare(buf.size()-marker.size(), marker.size(), marker) == 0 :   (0x49c920)
469b82                 std::wofstream out(dir + entry.path, binary)   ; failure: wcerr << L"Error creating output file: " (0x7db9ac) << path
469ca8                 out.write(buf.data(), buf.size()-marker.size())  (?write@basic_ostream<wchar_t>)
469cbf                 buf.clear()                                   (0x49c890)
```

INFERENCE (high): sections are therefore delimited purely by the marker text; a marker string must not occur inside a payload (it cannot: payloads are `.chf`/XML/`scFlie` text). Order of sections in the file does not matter to the reader; missing optional sections (`TASK_PARAM_*`) are simply never flushed. The streams are `wchar_t`-based in binary mode (MSVC widens byte→wchar 1:1 in the "C" locale and narrows back), so bytes survive; the port should just treat the file as bytes.

### 2.4 Import handler `0x466f90` after extraction (EVIDENCE)

`0x467454–0x4674d4`: read `Temp\tempIsBreak.ini` (`scFlie` reader `0x403eb0`/`0x404380`): int 1 = isBreak, int 2 = laser type; `0x4674ff cmp` against `G+0x46d8` → mismatch prompts `A250421_0` 加工任务与当前激光器类型不符 "The processing task does not match the current type of laser?" and aborts (`0x467649`). `0x467653`: load `Temp\tempGraph.chf` (`0x4ab660`), then `0x4676ff call 0x45bca0` → `0x43c530` = reader of `\File\Temp\{ManuContour.dat, AutosaveParam1.ini, AutosaveParam2.ini}` (`0x43c563…0x43c602`); return 2 → `L"Graph Index file was error!"` (`0x7db424`), 3 → `L"Broken point file was error!"` (`0x7db45c`). `0x467746`: import `Temp\tempLayer.xml` into the layer list of the current laser type (`A250105_1` 参数导入失败 "Parameter import failure" on error). `0x467bcf`: `G+0x4d6c = 1` (task loaded).

---

## 3. `softPara.ini` — `NormalExit` and axis positions (EVIDENCE)

`[SC2000]` section, file `<exe>\File\softPara.ini`, all via `WritePrivateProfileStringW`/`GetPrivateProfileIntW`.

| VA (write) | value | function | when |
|---|---|---|---|
| `0x568365` | `NormalExit=0` | `0x566ee0` (the big init routine, 07 §start-up; called from `0x568b42` inside the poll handler `0x568a50` on first card connection) — preceded by the reads `XAxis`/`YAxis`/`ZAxis` (`0x567b7b/bc0/c05`, default 0) and `NormalExit` (`0x567c4a`, **default 1**) into `G+0x3ff8…` | **start-up**: marks "running / not yet cleanly closed" |
| `0x459aa8` | `NormalExit=1` | `0x459820` = the main window's **destructor body** (logs `L"--- Exit Sys ---"` `0x7da348`; deleting-dtor wrapper `0x4594f0` calls it then `delete`); then `0x459ab4 call 0x45cf80` (axes), then `TerminateThread` ×3 on `G+0xf32c/f330/f334` | **normal close** |
| `0x48f338` | `NormalExit=1`, then `Sleep(500)` | `0x48f260`, guarded by `IsNetworkAlive(&f) && f == 1 (NETWORK_ALIVE_LAN)` (`0x605e0a` thunk → IAT `IsNetworkAlive`); sets `G+0x4310 = 1` and `[this+0xf401] = 0`; callers `0x493316` (a ribbon command, `0x493300`) and `0x52df60` (state dispatcher `0x52df10`, when `[+0x1898] ∈ {0,0x3a,0x3d,0x3e,0x52}`) | **network-side exit** (INFERENCE medium: remote/HMI shutdown or app restart) |
| `0x45d183 / 1a5 / 1ca / 1ec` | `XAxis`, `YAxis`, `ZAxis`, `WAxis` | `0x45cf80`: skipped when `G+0x4310 != 0` (`0x45cfb6`); for each axis slot `G+0xba5c…` reads the card register `slot*10+2` through NC virtual `[+0x1a0](3, idx)` (`0x45d015–0x45d046`) and writes it as an integer string (`0x44a7e0`) — units 0.001 mm (01 §4, 08 §6.6 values 1020277/175510) | called from the destructor (`0x459ab4`), from `0x4e92c0` and `0x565640` (both = the "MCC <Offline>" / `mp22` connection-lost handlers, strings `0x4e9333`, `0x5656b3`) |

INFERENCE (high) on 08 §9.14: the file captured on 2025-07-18 shows `NormalExit=0` because the poll handler wrote `0` at start-up and the last write (axes at 15:49:27.575, 145 ms after the last log line) came through `0x4e92c0/0x565640` (card connection lost) — a path that writes the axes **without** writing `NormalExit=1`. So `NormalExit=0` on disk means "the session ended by a card-disconnect/crash path, not through the destructor", exactly what 08 suspected; the port should write `0` at start and `1` only in its clean-exit path, and read it at start for the `mp18/mp19/mp21` recovery prompts (07 §12).

---

## 4. `scFlie` state files: `ManuContour.dat`, `AutosaveParam1/2.ini`, `tempIsBreak.ini`, `tempManu.ini`, `.pmf`, `.zpf`

### 4.0 The container class (EVIDENCE)

One small class (ctor `0x403d90`) is used by every one of these files: `open(path, mode)` `0x4046e0` (mode 1: `CreateFileW(GENERIC_WRITE, CREATE_ALWAYS=2)`, else `_wfopen_s(L"wb")`) writes the line `scFlie` (`0x7c4744`) through `0x4048a0` (`"%s\r\n"` `0x7c4768`); `writeInt(int)` `0x404990` = `sprintf_s/fprintf("%d\r\n")` (`0x7c4770`/`0x7c4778`); `close()` `0x404800` writes `eof` (`0x7c474c`), `FlushFileBuffers`/`fflush`, `CloseHandle`/`fclose`. There is also a `"%12.10f\r\n"` double writer (`0x7c4780`). Reader side: `openRead` `0x403eb0` (`CreateFileW(GENERIC_READ, OPEN_EXISTING=3)`, `GetFileSize`), `readInt` `0x404380` (`atoi` of the next line), `readDouble` (`atof`), `close` `0x4042b0`. Every user site: `0x436be8`, `0x43c668/95c/ac5`, `0x446cc8`, `0x467479`, `0x468245`, `0x468451`, `0x5a65f9`, `0x5fe359`, `0x5fe5e3` (all `call 0x403d90` sites).

Format, confirmed against the shipped samples (03 §, 08 §6.6):
```
scFlie\r\n
<int>\r\n … \r\n
eof\r\n
```

The four live paths are stored in the control-panel view (`CBCGPFormView` ctor `0x563060`, `0x56354f–0x56364c`: `\File\` + `AutosaveParam2.ini`, `AutosaveParam1.ini`, `autosave.dat`, `ManuContour.dat`) and handed to the state sub-object `view+0x1f58` (ctor `0x435e00`: `+0x158` ← ManuContour, `+0x1ac` ← autosave.dat, `+0x174` ← AutosaveParam1, `+0x190` ← AutosaveParam2).

### 4.1 `ManuContour.dat` — writer `0x4368e0` ("SaveIndex") (EVIDENCE)

Log tags in the function: `L"GetCtGly"`, `L"InitCtData"`, `L"DeleteFile"`, `L"SaveIndex"` (`0x4369d5–0x436a11`). `0x436bd2 DeleteFileW([this+0x158])`, `0x436c1d open(mode 1)`, `0x436c61 writeInt(vector.size())` (`0x42f6c0`), loop `0x436c72–0x436cbb` `writeInt(vector[i])` (`0x42a4e0`), `0x436cc3 close()`. → `count` followed by `count` contour indices (sample: `24`, `0…23` — 03 §).

Callers: `0x596530` (`0x596558`, guarded by `0x435e60`) ← `0x57ed75` in **`CManuPanel::OnStartBtn`** (`0x57eb40`, log string `0x57ef99`), `0x589e93` in **`CManuPanel::OnSimBtn`** (`0x589d20`, `0x589d58`), `0x59f001` in the pre-start routine `0x59ee50` (used by Start and by the `.enc` export); and `0x4368d3` (`0x436820` ← `0x4a7e8a`, the resume/restore path). So the file is (re)written at every **job start / simulation start / resume**, matching the 08 timing (`ManuContour.dat` 15:35:10.13, `autosave.chf` 15:35:10.17 = the same Start event; `autosave.chf` is written through `ICADModule::Save` with the path stored at `0x45907a`, 03 §).

### 4.2 `AutosaveParam1/2.ini` — writer `0x446c50`, a 100 ms timer (EVIDENCE)

`0x56533b timeBeginPeriod(…); 0x56535e timeSetEvent(delay 0x64 = 100 ms, res, callback 0x59ce00, user = view, TIME_PERIODIC (1))` in `0x564ae0` (panel initialisation); handle kept at `view+0x1ed0`. Callback `0x59ce00` → `0x446c50(view+0x1f58)`:

```
446c80  cmp G+0x47cc, 2 ; jne return            ← only while the NC state is 2 (= running, A2/A1)
446cb7  NC virtual [+0x278](&graphIdx, &pointIdx)   (current item counters)
446cc0  0x4366a0 → current X,Y (doubles, via NC virtual [+0x1a4] and 0x403af0)
446cd4  n = ds:0x9495ac ; (n & 1) == 0 ? path = [this+0x174] (AutosaveParam1.ini) : [this+0x190] (AutosaveParam2.ini)
446d1e  open(mode 1)
446d34  writeInt(n)                      ← global sequence number (0x9495ac)
446d55  writeInt((int)(X * 1000.0))      ← 0x7d0ec8 = 1000.0 → µm (0.001 mm)
446d76  writeInt((int)(Y * 1000.0))
446d82  writeInt(graphIdx)
446d8e  writeInt(pointIdx)
446d96  close()
446da4  ds:0x9495ac += 1
```

This settles 00 §7.2 disagreement 11 and 08 §9.8: field 1 is a **running sample counter**, not an index into `ManuContour.dat`; the two files alternate every 100 ms (even → 1, odd → 2), which is why the samples are 100 ms apart and one apart in value (`38`/`37`); the counter only resets with the process (static init), so values exceed the contour count. Fields: `n, X_µm, Y_µm, graphIdx, pointIdx` (03/01 readings confirmed). The reader `0x43c530` (used by resume and by `.aut` import) reads `Temp\` copies: `ManuContour.dat` (`0x43c668`), `AutosaveParam1.ini` (`0x43c95c`), `AutosaveParam2.ini` (`0x43cac5`).

### 4.3 `tempIsBreak.ini`, `tempManu.ini` — see §2.2 (writer) / §2.4 (reader). `File/Temp/tempIsBreak.ini` in the package is the last import's copy.

### 4.4 `.zpf` (FTC parameter export/import, `zf104/105/107`) — `0x5fe150` writes `scFlie` with `count` + `count` ints (`0x5fe38f`, loop `0x5fe3a0–0x5fe3e2`); `0x5fe510` reads them back (`0x5fe63a`, `0x5fe675`). Same container; contents = the FTC property words (A5 §2.2). Not in scope for cutting.

---

## 5. `Report\TotalReport.txt` — row trigger and field order (EVIDENCE)

Writer **`0x49bfa0`** (single caller `0x56d0fe`):
* `0x49bfd9 std::ofstream(<exe>\Report\TotalReport.txt, mode 0xa = out|app, prot 0x40)` — append, no header.
* `rec = last element of the vector G+0x4394` (0x98-byte records; `0x447720` = back()).
* Fields, in emission order (`0x49c070…0x49c6be`, `wstring += …`):
  1. `rec+0x00` (wstring) — job/file name (`0x49c091`);
  2. `L",%.2f×%.2f mm"` (`0x7de4ec`, **the exe string contains `×` U+00D7**, correcting 08 §6.1's "lower-case x" remark) with `rec+0x20`, `rec+0x28`;
  3. `L","` + `strftime(L"%Y-%m-%d %H:%M:%S")` (`0x7de508`) of `rec+0x40` (64-bit time set at `0x56d0ae` from `0x446a50` = now);
  4. `L",%.2f m"` `rec+0x30` × 0.001 (cut length);
  5. `L",%.2f m"` `rec+0x34` × 0.001 (idle length);
  6. `L",%d"` `rec+0x38` (pierces);
  7. `L","` + `gp146` (`%d分%02d秒` / `%02dMin%02dSec`) with `t = rec+0x3c/10; t/60, t%60` (`0x49c479–0x49c4a6`);
  8. `L",%.2f Sec"` `rec+0x68`; 9. `L",%.2f Sec"` `rec+0x70`; 10. `L",%.2f Sec"` `rec+0x78` (cut / idle / pierce seconds, 08 §6.1 format B);
* converted to a narrow string (`0x4a3cf0`) and written `ofs << row << std::endl` (`0x49c78e`, `0x49c798`).

**Trigger (EVIDENCE):** `0x568a50` is the control panel's periodic **poll handler** (`GetTickCount` `0x568abb`; connection check → `0x565640` "MCC <Offline>"; first-connect init `0x566ee0` at `0x568b42`; `mf1001` "MCC hardware has changed" check). Inside its stop branch (`0x56cf02 cmp G+0xb320,0 ; jg skip` → the block that shows `mp26` 实际加工时间 "Actual processing time" and the `%d / %d` progress gauge) it does, when `G+0x4394` is non-empty (`0x414270` = size in 0x98 units, `0x56d08a jbe skip`): `rec+0x40 ← now`, `rec+0x48 += 1` (`0x56d0d6`), `0x5b3e80([this+0x1ec])`, then `0x49bfa0([this+0x1fc])`. INFERENCE (high): the row is appended once per **running→stopped transition** seen by the poll timer, whatever the reason (finished, Stop button, alarm) — consistent with 08 §6.2's "rows for 1-s runs". The numbers are the planner's estimates stored in the record at Start (the `.olpi` in §1.3 dumps the same record before any cutting).

`LogReport.txt` (`0x490773`), `report.txt` (`0x490860`), `lang.txt` (`0x491704`) are written by the "Work Report [New]" viewer launcher (07 §11), not by this path.

---

## 6. `\File\PM\p0.pmf` — SimplePLC flow (EVIDENCE) and recommendation

* Path string `L"\File\PM\p0.pmf"` `0x8473b0` (pushed at `0x5a6605` in **`0x5a65b0`**); a second copy `0x7e5fb8` is unreferenced.
* `0x5a65b0` = **the only writer**: `scFlie` `open(mode 1)` (`0x5a665b`), `writeInt(steps.size())` (`0x5a6686`, vector at `[this+0x1ac4]`, `0x448600`), then per step (`0x4b7430` = element i, stride 0x1c): `writeInt(step.type)` (`[eax]`, `0x5a66c4`) and `writeInt(step.param[k])` for k = 0..5 (`[eax+4+k*4]`, loop `0x5a66d5–0x5a6715`), `close()` (`0x5a67c2`). When `type == 4` (`0x5a672c`) it additionally bookkeeps a DO-port list in memory (`0x4b9ab0`/`0x4b99b0`) — nothing extra is written.
* Step types: two static label arrays are built at start-up in `0x73bd20`: `[slp4, slp5, slp6, slp7, slp8, slp9, slp10, slp11, slp18]` and `[slp12, slp13, slp14, slp19]`. INFERENCE (high, index = type code):

| type | label (`lang.txt`) | params used |
|---|---|---|
| 0 | `slp4` 开始加工 Start Manu | — |
| 1 | `slp5` 开始空走 Start Empty Move | — |
| 2 | `slp6` 等待【%s】（超时【%d】毫秒） Wait event (Timeout ms) — event index into `[slp12 加工完成 Manu Done, slp13 空走完成 Empty Move Done, slp14 回原完成 Go-origin Done, slp19 平台交换完成 Platform Change Done]` | event, timeout |
| 3 | `slp7` 等待DI端口【%d】触发 Wait DI (Timeout ms) | port, timeout |
| 4 | `slp8` 打开DO端口【%d】 Open DO | port |
| 5 | `slp9` 关闭DO端口【%d】 Close DO | port |
| 6 | `slp10` 延时【%d】毫秒 Delay ms | ms |
| 7 | `slp11` 开启循环（循环次数【%d】） Start Loop (count) | count |
| 8 | `slp18` 开始平台交换 Start Change Platform | — |

  Editor actions `slp0–slp3` (insert before/after, modify, delete), `slp15` delete-confirm, `slp16` 回原 Go Origin, `slp17` 点动 Jog (menu items of the same dialog, `0x5a6830`).
* **No reader exists**: the `scFlie` read helpers (`0x403eb0`/`0x404380`) are called only from `0x43c530` (break-point files), `0x466f90` (`.aut` import) and `0x5fe510` (`.zpf`); the PM directory in the package is empty; `Pc_Software.zip` and the Wine prefix contain no `.pmf`. INFERENCE (high): the flow editor in v0.0.0.52 can save but never reloads its flow; the feature is vestigial on this machine.

**Recommendation: drop.** Add the SimplePLC flow editor (`.pmf`, `slp*` strings, `\File\PM\`) to PORT-PLAN non-goals; if ever needed the format above is complete (`scFlie` / `count` / `type,p0..p5` ×count / `eof`).

---

## 7. `Help\calib.csv` (EVIDENCE) — confirms A5 §2.3

`0x5fdb30` ("Calib Datas" `zf101` button on the ZF page): guarded by ZF virtual `[+0x34]` (connected, `0x5fdb80`); `[+0x1b8]` (`0x5fdbae`) returns the pair vector (NC slot 110 → VM+0x784, A5 §2.3), copied by `0x5fee20`; `fopen_s(<exe>\Help\calib.csv, "w+")` (`0x5fdbc3`, `0x5fdc26`); per element `fprintf("%d, %d\n", e[0], e[1])` (`0x86ff8c`, `0x5fdc9c`, element stride 8: `[eax]`, `[eax+4]`); `fclose`; then `CreateProcessA("explorer.exe <exe>\Help\")` (`0x5fdd34`, `0x5fddab`). Row = `(raw capacitance signal, height)` per A5 (200 pairs from FTC words 12002…12801). The `"%d, %d, %d, %.3f"` variant quoted in 99-gaps §1.1 is not in this function (no such string at `0x86ff70–0x86ffb0`). Port: viewer-only, not needed to cut.

---

## 8. `pwmCompensation.txt` / `Co2_pwmCompensation.txt` (EVIDENCE)

`grep -rl -a -i pwmCompensation` over the whole package directory and the Wine prefix returns nothing except the two files themselves; `strings -a` and `strings -a -e l` of `MainApp.exe`, all `Module/*.dll` and every top-level DLL contain neither `pwmCompensation` nor any `*Compensation*.txt` name (the only `.txt` paths in MainApp are `\JumpAddTime.txt`, `\Report\LogReport.txt`, `\report.txt`, `\lang.txt`, `\Report\TotalReport.txt`, `//LanFormatEStr.txt`, `D:\seekEdge.txt`, `TestReport.txt`). `FiberScanFlyCompensateStr` appears only in `File/BkManuPara.xml`, `File/SecondBkManuPara.xml` and `1390backup.xml`. → **Not read at runtime.** INFERENCE (high): the vendor's hand-off tables from which `GRP.FiberScanFlyCompensateStr="100#0.35,…"` was typed (values differ slightly from the txt: 0.364 vs 0.35 — the XML is authoritative). Ship the two `.txt` files as reference/golden data only.

---

## 9. `\File\logo.bmp` (EVIDENCE)

`0x5a84e0` = `OnInitDialog` of the **splash dialog** (`CBCGPDialog::OnInitDialog` `0x5a8517`, layered window alpha 0x80 `0x5a87ea`): creates a static control and `LoadImageW(NULL, L"res\splash.bmp", IMAGE_BITMAP, 0,0, LR_LOADFROMFILE)` (`0x5a86bf–0x5a86c6`); then builds `<exe>\File\logo.bmp` (`0x5a86e4`), `PathFileExistsW` (`0x5a8738`) and, if present, creates a second static at rect (0x244,0x118)–(0x2da,0x1ae) = **(580,280)–(730,430), 150×150 px** and `LoadImageW(… logo.bmp …)` into it (`0x5a87a8`). Shipped `File/logo_1.bmp` (200×200 1-bit) is not that file name; the feature is an optional OEM logo on the splash. Port: optional; if the splash is reproduced, overlay `File/logo.bmp` when present.

---

## 10. Golden files the port should ship / generate

| file | source | use |
|---|---|---|
| `Report/TotalReport.txt` (2 950 rows) | package | parser tests for formats A/B; writer must emit format B exactly (§5) |
| `File/AutosaveParam1.ini`, `2.ini`, `Temp/*` | package | `scFlie` reader/writer round-trip (§4.2) |
| `File/ManuContour.dat`, `Temp/ManuContour.dat` | package | idem (§4.1) |
| `File/Temp/tempIsBreak.ini` | package | idem (§2.2) |
| `File/softPara.ini` | package | `NormalExit`/axis restore (§3) |
| `pwmCompensation.txt`, `Co2_pwmCompensation.txt` | parent dir | reference only (§8) |
| `File/logo_1.bmp`, `res/splash.bmp` | package | splash (§9) |
| **synthetic** `sample.aut` | build from `File/autosave.chf` + `File/LayerPara.xml` + a `tempManu.ini` + `tempIsBreak.ini` with the §2.2 concatenation | `.aut` reader/writer tests (no vendor sample exists) |
| **synthetic** `sample.enc` | `.olpi` (§1.3) + `.olpf` (A3 record dump) + `Report/rpt.chf.jpg` + `LayerPara.xml` + a `.chf`, §1.2 order | `.enc` writer test; consumer is the NexCut server, not the port |
| `Help/calib.csv` | none shipped; generate from the A5 §2.3 word layout when the FTC is captured | viewer test |

Nothing in this document requires the `.pmf` editor.

---

## 11. Open-question status

* **O14 closed statically**: layouts (§1, §2), `NormalExit` (§3), `AutosaveParam` event (§4.2), `TotalReport` trigger and field order (§5).
* 99-gaps §4 item 1 (`.pmf`) closed (§6, drop); item 2 (`calib.csv`) closed (§7, matches A5); item 8 (`pwmCompensation`) closed (§8); item 9 (`logo.bmp`) closed (§9).
* 00 §7.2 disagreement 11 (`AutosaveParam` field 1) closed: running 100 ms sample counter (§4.2).

Still needing the live machine (one step each):
1. **`.enc` server acceptance** — capture the WinHTTP `POST /NexCut/File/LoadFile` exchange (07 §HTTP) with a real `.enc` to learn whether the server expects exactly these five sections; static analysis cannot see the consumer.
2. **`G+0x48f4`** (7th `.olpi` line): change the suspected dialog field (the enum 0..9 at `0x4aaf4c…`) in the Windows UI, export an `.enc`, diff line 7.
3. **`isBreak` semantics of `TASK_PARAM_*`**: export a `.aut` once while a job is paused and once idle; confirm the two extra sections appear only in the first (§2.2 flag logic).
4. **`NormalExit`**: close MainApp normally once and compare `softPara.ini` (`=1` expected, §3) — confirms the destructor path on the real build.
