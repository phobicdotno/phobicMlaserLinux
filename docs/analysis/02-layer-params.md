# 02 — Per-layer cutting parameter model (BkLayerPara.xml, layer references in .chf, technology library)

Analyst report for the Linux re-implementation of the SC2000 / "NexCut X1" control software (`Mlaser-v0.0.0.52`, `MainApp.exe` 2025-06-24).
All paths below are relative to `SRC = /home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52` unless stated otherwise. `PKG` denotes the parent folder `/home/karstein/Documents/CF1390-250715-1084-0973` (it contains a vendor material library and a full parameter backup that are directly relevant).

Conventions: **EVIDENCE** = something observed in a file or binary (with location). **INFERENCE** = my interpretation, always tagged with a confidence (high / medium / low). Chinese strings are quoted verbatim with an English gloss; where the vendor's own English translation from `Lang/lang.txt` exists it is given after the slash (`中文 / vendor English`), and when the vendor English is misleading I add my own gloss in brackets.

---

## 0. Summary of findings

* The per-layer process model is a flat attribute bag: one `<GP .../>` element per layer with **164 attributes** for the fibre laser (`PLayerParam1..11`) and **21 attributes** for the CO2 laser (`PCO2LayerParam1..11`). The XML is a plain serialisation of a parameter registry; MainApp carries a descriptor table (attribute name → unit → default → language ID) that fully documents every attribute (Section 2).
* There are **11 layer slots** per laser type. Slot 1 (0-based index 0) is the *background layer* (`背景图层 / BackLayer`), slots 2..10 are `图层 %d / Layer %d`, slot 11 is the *film-removal layer* (`去膜图层 / Film Layer`, the only slot with `WithFilm="1"`). Section 4.
* A job file (`*.chf`, text format `scFlie` v5) references a layer by a **0-based integer written immediately after `<End Glyphs>`** in each graph block; every graph in every sample file uses layer 0. This was confirmed by disassembling the writer and the `COpGraphLayerCmd` command in `Module/CADModule.dll` (Section 5).
* The process model is a classic fibre-laser cutting sequence: up to **five pierce stages** ("一级…五级穿孔", each with height, focus, focus ramp, power/frequency/peak-current, gas type/pressure, duration, gradual height ramp, dwell and blow-off), an optional *smooth pierce*, *bolt pierce* bursts, *residue cleaning* spiral, then cutting with a speed-dependent **power curve** and **frequency curve**, start/end segments (`起刀/收刀`), lead-in geometry, cool points, gas hold-over on short moves, Z lift height, frog-jump, and Z-follower vibration abatement (Section 3).
* A **material library** ("工艺库 / Technology") exists: single-layer XML files (`<ParameterRoot><PLayerParam11><GP …/></PLayerParam11></ParameterRoot>`) stored under `<base>\NexCut\Technology\Fiber\*.xml` and `...\CO2\*.xml`. The package ships **53** such files under `PKG/Cutting parameters/` (VERIFIER: recounted — 20 + 20 + 13, not 54; plus 2 under `PKG/Cleaning and quenching/`) plus a spreadsheet of the same data (Section 6). VERIFIER: the 13 CO2 files carry only **20** attributes (no `CutFreq`), so the importer must tolerate missing attributes (§6.2).

---

## 1. Files examined

| File | Size / encoding | Role (evidence) |
|---|---|---|
| `File/BkLayerPara.xml` | 44,148 B, UTF-8 (no BOM), CRLF, one element per line | Backup copy of the layer parameter set. MainApp strings (UTF-16) `\LayerPara.xml`, `\BkLayerPara.xml`, `The layer parameter file was not found and was re-created.`, `Read Backup` (file offsets around 0x42a000 in `MainApp.exe`, see §1.1). |
| `File/BkHardPara.xml`, `File/BkManuPara.xml`, `File/SecondBkManuPara.xml` | 10 KB / 8 KB | Backups of hardware / machining parameters; contain the global process parameters referenced by the layer model (gas valves, delays, follower). |
| `PKG/1390backup.xml` | 62,310 B | Full system backup (all parameter groups incl. `PLayerParam1..11`, `PCO2LayerParam1..11`) — produced by `lp12 备份系统参数 / System parameters backup`. Confirms the structure and gives a second data point per layer. |
| `File/autosave.chf`, `Graph/Work1/{1,2,3}.chf`, `Graph/Work2/{1,2,3}.chf`, `File/Temp/tempGraph.chf` | text, CRLF | Job files (format versions 5 and 4); carry the per-graph layer index. |
| `Lang/lang.txt` | UTF-16LE, 3,485 lines, `ID#中文#English` | Master language table; the layer dialog labels are the `pd*`, `lp*`, `gp*`, `newLang*`, `A2*` IDs cited below. |
| `MainApp.exe` `.rdata` | file 0x472468–0x4768c0 (VA 0x873668–0x877ac0; `.rdata` VA = file offset + 0x401200) | Parameter descriptor table for groups `LayerParam` / `CO2LayerParam` (§2.1). |
| `Module/CADModule.dll` | 1.3 MB, image base 0x10000000 | Owns the graph model and the `.chf` reader/writer (`####graph NO:`, `<Crafts>` … strings at 0x10111b44–0x101133f0). |
| `Module/ParaModule.dll` | 135 KB | XML parameter engine (`ParameterRoot` string at line 292 of its UTF-16 strings; PDB path `D:\SC2000\NexCut\NexCut_X1_Http\Release\Module\ParaModule.pdb`). |
| `PKG/Cutting parameters/**/*.xml` (53 files — VERIFIER: 1200W 20, 1200W_air 20, CO2 13; the analyst's 54 was a miscount), `PKG/Cleaning and quenching/*.xml` (2 files), `PKG/Cutting parameters/Cutting parameters.xlsx` | | Vendor material library in the technology-export format. |

### 1.1 Where the primary file lives (EVIDENCE + INFERENCE)

EVIDENCE — UTF-16 strings in `MainApp.exe` (scratch list line numbers 5420-5440) appear in this order:
`\NexCut`, `--- Init System param ---`, `\SystemPara.xml`, `\HardPara.xml`, `\ManuPara.xml`, `\LayerPara.xml`, `\File`, `The hardware parameter file was not found and was re-created.`, `\BkHardPara.xml`, `\BkLayerPara.xml`, `The software parameter file was not found and was re-created.`, `\BkManuPara.xml`, `\SecondBkManuPara.xml`, `The layer parameter file was not found and was re-created.`, `Hard Param File Read Failed`, `Read Backup`, `Manu Param File Read Failed`, `Read Backup`.
`ClearFile.bat` in SRC (a developer script) deletes `*.xml` recursively, and no `LayerPara.xml` exists anywhere in SRC.

INFERENCE (high): the live copies `SystemPara.xml / HardPara.xml / ManuPara.xml / LayerPara.xml` are kept in a per-machine data folder named `…\NexCut\` (probably under the user profile / AppData — the same `\NexCut` prefix is used for the `Technology` folder), and `File\Bk*.xml` are the backups that are re-read ("Read Backup") when the primary is missing or corrupt. The Linux port only has the backup copy, which is byte-for-byte the same schema.

VERIFIER — now EVIDENCE (high): the base folder is **`%LOCALAPPDATA%\NexCut`**. `MainApp.exe` `.text` 0x432900 is a helper that calls `SHGetSpecialFolderPathW(NULL, buf, 0x1c /* CSIDL_LOCAL_APPDATA */, TRUE)` (pushes at 0x43291d–0x43292a, IAT slot 0x7c0f18) and returns it as a string; the parameter-init routine calls that helper at 0x4afef1, appends the literal `\NexCut` (VA 0x7e59f0, `push` at 0x4aff12 — this is the `\NexCut` string that immediately precedes `--- Init System param ---` in `.rdata`), then calls `PathFileExistsW` (IAT 0x7c0f2c, call at 0x4aff45) before the `\SystemPara.xml` … `\LayerPara.xml` names are appended. `SHGetSpecialFolderPathW` has no other call site in the executable, and `GetModuleFileNameW` is called only once (0x4500eb), so the parameter set does not live next to the executable. The same helper (0x432900) is used for the `\NexCut\Technology\Fiber|CO2` library path (§6.1) and for `\NexCut\CustomInformation.ini`. Just before this, the init routine reads `GetPrivateProfileIntW("Axis4Freq","Is4Freq",…)` from `\JumpAddTime.txt` (strings at 0x7e59cc/0x7e59dc; file `JumpAddTime.txt` exists in SRC root).

---

## 2. XML structure

### 2.1 Element tree (EVIDENCE — parsed with `xml.etree`)

```
ParameterRoot
├── PLayerParam1      └── GP  (164 attributes)      ← fibre-laser layer slot 1  (index 0, "BackLayer")
├── PLayerParam2      └── GP  (164 attributes)      ← slot 2 (index 1, "Layer 1")
│   …
├── PLayerParam11     └── GP  (164 attributes)      ← slot 11 (index 10, film-removal layer)
├── PCO2LayerParam1   └── GP  (21 attributes)       ← CO2-laser layer slot 1
│   …
└── PCO2LayerParam11  └── GP  (21 attributes)
```

* No text nodes, comments or XML declaration. Element names are `P` + *group name* + *1-based slot number*; the group names `LayerParam` and `CO2LayerParam` appear verbatim in the descriptor table (§2.2), the prefix `P` and the number are added at runtime (neither `PLayerParam` nor `ParameterRoot` exists as a literal in `MainApp.exe`; `ParameterRoot` is a literal in `Module/ParaModule.dll`).
* Attribute order is identical in all 11 slots and identical to the order of the descriptor records (reversed, because MSVC emitted the string literals bottom-up) — i.e. the file is written by iterating the registry.
* Doubles are written with `%.17g` (`0.59999999999999998`, `11.000000000000002`, `8.3333333333333339`), ints as plain ints, strings unescaped except XML specials.
* VERIFIER: re-parsed both files — 22 elements, 164/21 attributes, identical attribute order in every slot, UTF-8 without BOM, CRLF, no declaration: confirmed. The *values* in `1390backup.xml` differ from `BkLayerPara.xml` in slots 1, 2, 10 and CO2 slots 1, 2, 3, 9 (e.g. backup slot 1 = `Carbon 3.0mm 1.2D F+14 O2`, slot 2 = `Carbon 10.0mm 4.0D F+12 O2`, whereas `BkLayerPara.xml` has the 10 mm recipe in slot 1 and `Carbon 1.0mm 2.0S F-2 N2` in slot 2) — the two files are snapshots from different times, not copies.
* The same 22 elements appear, in the same order and with the same attribute sets, inside `PKG/1390backup.xml` after the hardware/manufacturing groups (`PAxisParam`, `PHomeParam`, `PZFParam`, `PLaserParam`, `PManuParam`, `PGasParam`, `PDOParam`, `PDIParam`, `PDAParam`, `PFCParam`, `PSoftParam`, `PMachineAxisConfig*`, `PAFParam`, `PECParam`, `PGraphParam`, `PNestParam`, `PImportGraphParam`).

### 2.2 The descriptor table in MainApp.exe (EVIDENCE)

`MainApp.exe` `.rdata` contains, for every parameter of every group, a record of adjacent UTF-16 literals. Reading the region file offset 0x472468 … 0x4768c0 backwards, each record is

```
[unit] , default , <lang ID> , "GP.<AttributeName>" , "<GroupName>"
```

Examples (file offsets — VERIFIER: the offsets below are those of the trailing `LayerParam` marker string; the `GP.<name>` literal starts 0x18 bytes earlier, e.g. `GP.CutSpeed` at 0x475e24, `GP.ManuType` at 0x4747c0. The registration code for `GP.ManuType` is at `.text` 0x77ab8d–0x77ac19, pushing `''`, `LayerParam`, `GP.ManuType`, `pd815`, `0`, `''` in that order):

| offset | strings |
|---|---|
| 0x475e3c | `mm/s`, `100`, `pd125`, `GP.CutSpeed`, `LayerParam` |
| 0x475ca4 | `V`, `5`, `pd131`, `GP.CutAirPressure`, `LayerParam` |
| 0x475980 | `mm`, `5`, `pd816`, `GP.DrillHeight0`, `LayerParam` |
| 0x4747d8 | `0`, `pd815`, `GP.ManuType`, `LayerParam` — VERIFIER: this record is **not** adjacent to any option list. The strings `pd666, pd665, pd813, pd812, pd811, pd810` sit at file 0x472400–0x472448, i.e. immediately *before the last record* (`GP.FreqAdjustWithSpeed`, 0x472468), and are pushed by a separate option-list builder function at `.text` 0x7775c0 (pushes 0x87363c `pd810`, 0x873630 `pd811`, 0x873624 `pd812`, 0x873618 `pd813` — only these four; `pd814` does not occur anywhere in `MainApp.exe`). A sibling builder at 0x777550 pushes `pd665/pd666` (gradual-pierce type). Neither builder has a direct `call` xref (they are reached through a function pointer), so their association with `GP.ManuType` is by content, not by code adjacency. |
| 0x472498 | `0`, `A241012_5`, `GP.FreqAdjustWithSpeed`, `LayerParam` (last record of the group) |
| 0x4765d4 | `%`, `100`, `pd127`, `GP.CutDuty`, `CO2LayerParam` |

Group population (count of group-marker strings): `LayerParam` 164, `CO2LayerParam` 21 — exactly the attribute counts in the XML, and every XML attribute name was found as `GP.<name>` in the executable (checked programmatically; zero missing in either direction). The lang IDs resolve to the UI labels in `Lang/lang.txt` (the labels use the vendor's `主条目.子条目` "group.item" convention, see `Lang/Readme.txt`: "参数类的文本格式为 主条目.子条目" = parameter-type texts are formatted *main entry.sub entry*).

Seven `GP.*` names exist in the executable that are **not** layer attributes: `GP.CoolPostionDelay`, `GP.EnableMicroLinkDecc`, `GP.IsAutoCheckGraphOrder`, `GP.IsDrillInMicoLink`, `GP.MicoLinkSlowDownVel`, `GP.PreDrillMaxNum`, `GP.ZFVibAbat_Enable`. The first six are the `GP` sub-elements of `PManuParam` and `PSoftParam` in `File/BkManuPara.xml` (global graph-process control, §3.9); `ZFVibAbat_Enable` is an obsolete name superseded by `ZFVibAbatType` (INFERENCE, medium).

### 2.3 Complete attribute inventory

Columns: unit and default from the descriptor table; lang ID and label from `Lang/lang.txt`; values from `File/BkLayerPara.xml` for slots L1…L11 (identical values collapsed to `all = v`). Two descriptor quirks are the vendor's, not mine: `DrillGasType0..4` carry the unit `ms` (VERIFIER: `DrillGasType0`, `2`, `3`, `4` do; `DrillGasType1` at 0x475104 has **no** unit string — see table row 47) and `LayerFileName` re-uses lang ID `pd154` — copy/paste errors in the vendor table. VERIFIER: a third quirk — `FocusGradualTime2` (0x474cf8) re-uses `pd934` (the *end-position* label) instead of a `pd933`-style "time" label, so the property grid shows the wrong caption for that attribute (table row 58 already reflects this); `ZFVibAbatType` (0x472920) carries unit `ms` although it is an enum. `lang.txt` itself has four duplicated IDs (`gp100`, `pd1001`, `A241224_0`, `A250616_0`); `pd1001` is both `停止交换 / Stop Exchange` and `一级穿孔基础.穿孔高度 / Third Drill Basic.Third Drill Height` (sic), and `A241224_0` is both `杂项.比例阀开启前延时 / Misc.Proportional valve opening delay` and `记忆零点 / Memory zero` — which one the loader keeps depends on its duplicate policy.

#### Group `LayerParam` (elements `PLayerParam1..PLayerParam11`)

| # | Attribute | Unit | Default | Lang ID | Label (zh / en) | Values L1..L11 (BkLayerPara.xml) |
|---|---|---|---|---|---|---|
| 1 | `NoManu` |  | 0 | pd112 | 此图层不加工 / Uncut | all = 0 |
| 2 | `NoFollow` |  | 0 | pd114 | 此图层不跟随 / Unfollow | all = 0 |
| 3 | `ShortDistNoUp` |  | 1 | pd113 | 短距离不上抬 / Short Unlift | all = 1 |
| 4 | `ShortDistGasKeepOn` |  | 0 | pd113-1 | 短距离不关气 / Short Keep Gas On | all = 0 |
| 5 | `NoCloseGasInManu` |  | 0 | pd117 | 加工中不关气 / Keep Gas On | all = 0 |
| 6 | `PreDrill` |  | 0 | pd115 | 预先穿孔加工 / PreDrill | all = 0 |
| 7 | `WithFilm` |  | 0 | pd116 | 启用带膜切割 / With Film | 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 1 |
| 8 | `CutHeight` | mm | 1 | pd129 | 切割基础.切割高度 / Cut Basic.Cut Height | 0.6 / 0.5 / 1 / 1 / 1 / 1 / 1 / 1 / 10 / 1 / 1 |
| 9 | `CutFocusPos` | mm | 0 | pd589 | 切割基础.切割焦点 / Cut Basic.Cut Focus Position | all = 0 |
| 10 | `CutSpeed` | mm/s | 100 | pd125 | 切割基础.切割速度 / Cut Basic.Cut Speed | 11 / 250 / 100 / 100 / 100 / 100 / 100 / 100 / 83.33 / 100 / 100 |
| 11 | `CutPower` | % | 100 | pd127 | 切割激光.占空比 / Cut Laser.Cut Power | 88 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 |
| 12 | `CutFreq` | Hz | 5000 | pd128 | 切割激光.切割频率 / Cut Laser.Cut Freq | 2000 / 5000 / 5000 / 5000 / 5000 / 5000 / 5000 / 5000 / 5000 / 5000 / 5000 |
| 13 | `CutPeakCurrent` | % | 100 | pd132 | 切割激光.峰值功率 / Cut Laser.Peak Current | 88 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 15 / 100 / 100 |
| 14 | `CutGasType` |  | 0 | pd130 | 切割气体.切割气体 / Cut Gas.Gas Type | 1 / 5 / 0 / 0 / 0 / 0 / 0 / 0 / 5 / 5 / 0 |
| 15 | `CutAirPressure` | V | 5 | pd131 | 切割气体.切割气压 / Cut Gas.Gas Pressure | 0.6 / 0.85 / 5 / 5 / 5 / 5 / 5 / 5 / 0.85 / 2 / 5 |
| 16 | `LaserOnDelay` | ms | 0 | pd126 | 切割过程.停留时间 / Delay Parameters.Laser On Delay | 10 / 10 / 0 / 0 / 0 / 0 / 0 / 0 / 100 / 0 / 0 |
| 17 | `LaserOffBeforeDelay` | ms | 0 | pd137 | 切割过程.关光前延时 / Delay Parameters.Before Laser Off Delay | all = 0 |
| 18 | `LaserOffAfterDelay` | ms | 0 | pd138 | 切割过程.关光后延时 / Delay Parameters.After Laser Off Delay | all = 0 |
| 19 | `UpHeight` | mm | 20 | pd136 | 高级工艺.上抬高度 / Adv Parameters.Up Height | 15 / 15 / 20 / 20 / 20 / 20 / 20 / 20 / 20 / 20 / 20 |
| 20 | `SlowStart` |  | 0 | pd133 | 高级工艺.启用慢速起步 / Adv Parameters.Slow Start | all = 0 |
| 21 | `SlowStartLength` | mm | 1 | pd134 | 高级工艺.起步距离 / Adv Parameters.Start Length | all = 1 |
| 22 | `SlowStartSpeed` | mm/s | 10 | pd135 | 高级工艺.起步速度 / Adv Parameters.Start Speed | all = 10 |
| 23 | `PreLaserOnFactor` | mm/s | 0 | pd853 | 高级工艺.出光系数 / Adv Parameters.Pre Laser On Factor | all = 0 |
| 24 | `DrillHeight0` | mm | 5 | pd816 | 一级穿孔基础.穿孔高度 / First Drill Basic.First Drill Height | 6 / 10 / 5 / 5 / 5 / 5 / 5 / 5 / 10 / 5 / 5 |
| 25 | `DrillFocusPos0` | mm | 0 | pd817 | 一级穿孔基础.穿孔焦点 / First Drill Basic.First Drill Focus Position | all = 0 |
| 26 | `EnableFocusGradual0` |  | 0 | pd818 | 一级穿孔基础.启用焦点渐近 / First Drill Basic.Enable Focus Graudal | all = 0 |
| 27 | `FocusGradualEndPos0` | mm | 0 | pd845 | 一级穿孔基础.渐近目标焦点 / First Drill Basic.Focus Graudal End Position | all = 0 |
| 28 | `FocusGradualTime0` | ms | 1000 | pd847 | 一级穿孔基础.焦点渐近时间 / First Drill Basic.Focus Graudal Time | 1500 / 1500 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 800 / 500 / 1000 |
| 29 | `DrillPower0` | % | 50 | pd819 | 一级穿孔激光.穿孔功率(占空比) / First Drill Laser.First Drill Power | 65 / 40 / 50 / 50 / 50 / 50 / 50 / 50 / 60 / 80 / 50 |
| 30 | `DrillFreq0` | Hz | 500 | pd820 | 一级穿孔激光.穿孔频率 / First Drill Laser.First Drill Frequency | 150 / 150 / 500 / 500 / 500 / 500 / 500 / 500 / 150 / 300 / 500 |
| 31 | `DrillPeakCurrent0` | % | 100 | pd821 | 一级穿孔激光.峰值功率 / First Drill Laser.First Drill Peak Current | 60 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 80 / 100 |
| 32 | `DrillGasType0` | ms | 0 | pd822 | 一级穿孔气体.穿孔气体 / First Drill Gas.First Drill Gas Type | 1 / 5 / 0 / 0 / 0 / 0 / 0 / 0 / 5 / 5 / 0 |
| 33 | `DrillGasPressure0` | V | 5 | pd823 | 一级穿孔气体.穿孔气压 / First Drill Gas.First Drill Gas Pressure | 0.75 / 0.8 / 5 / 5 / 5 / 5 / 5 / 5 / 0.8 / 1 / 5 |
| 34 | `DrillDelay0` | ms | 1000 | pd824 | 一级穿孔过程.穿孔时长 / First Drill Process.First Drill Time | 1500 / 1500 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 800 / 500 / 1000 |
| 35 | `EnableGradualDrill0` |  | 0 | pd825 | 一级穿孔过程.启用渐近穿孔 / First Drill Process.Enable Gradual Drill | 0 / 1 / 0 / 0 / 0 / 0 / 0 / 0 / 1 / 0 / 0 |
| 36 | `GradualTime0` | ms | 1000 | pd826 | 一级穿孔过程.渐近穿孔时间 / First Drill Process.Gradual Drill Time | 1500 / 1500 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 800 / 500 / 1000 |
| 37 | `BeforeLaserOffDelay0` | ms | 0 | pd827 | 一级穿孔过程.停留时间 / First Drill Process.Before Laser Off Delay | 500 / 500 / 0 / 0 / 0 / 0 / 0 / 0 / 500 / 200 / 0 |
| 38 | `AfterLaserOffDelay0` | ms | 0 | pd828 | 一级穿孔过程.停光吹气 / First Drill Process.After Laser Off Delay | 50 / 50 / 0 / 0 / 0 / 0 / 0 / 0 / 50 / 0 / 0 |
| 39 | `DrillHeight1` | mm | 10 | pd829 | 二级穿孔基础.穿孔高度 / Second Drill Basic.Second Drill Height | 18 / 15 / 10 / 10 / 10 / 10 / 10 / 10 / 15 / 10 / 10 |
| 40 | `DrillFocusPos1` | mm | 0 | pd830 | 二级穿孔基础.穿孔焦点 / Second Drill Basic.Second Drill Focus Position | all = 0 |
| 41 | `EnableFocusGradual1` |  | 0 | pd831 | 二级穿孔基础.启用焦点渐近 / Second Drill Basic.Enable Focus Graudal | all = 0 |
| 42 | `FocusGradualEndPos1` | mm | 0 | pd846 | 二级穿孔基础.渐近目标焦点 / Second Drill Basic.Focus Graudal End Position | all = 0 |
| 43 | `FocusGradualTime1` | ms | 1000 | pd848 | 二级穿孔基础.焦点渐近时间 / Second Drill Basic.Focus Graudal Time | 5500 / 300 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 300 / 500 / 1000 |
| 44 | `DrillPower1` | % | 50 | pd832 | 二级穿孔激光.穿孔功率(占空比) / Second Drill Laser.Second Drill Power | 85 / 45 / 50 / 50 / 50 / 50 / 50 / 50 / 45 / 70 / 50 |
| 45 | `DrillFreq1` | Hz | 500 | pd833 | 二级穿孔激光.穿孔频率 / Second Drill Laser.Second Drill Frequency | 200 / 300 / 500 / 500 / 500 / 500 / 500 / 500 / 300 / 300 / 500 |
| 46 | `DrillPeakCurrent1` | % | 100 | pd834 | 二级穿孔激光.峰值功率 / Second Drill Laser.Second Drill Peak Current | 85 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 |
| 47 | `DrillGasType1` |  | 0 | pd835 | 二级穿孔气体.穿孔气体 / Second Drill Gas.Second Drill Gas Type | 1 / 5 / 0 / 0 / 0 / 0 / 0 / 0 / 5 / 5 / 0 |
| 48 | `DrillGasPressure1` | V | 5 | pd836 | 二级穿孔气体.穿孔气压 / Second Drill Gas.Second Drill Gas Pressure | 1.2 / 5 / 5 / 5 / 5 / 5 / 5 / 5 / 5 / 5 / 5 |
| 49 | `DrillDelay1` | ms | 1000 | pd837 | 二级穿孔过程.穿孔时长 / Second Drill Process.Second Drill Time | 5500 / 300 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 300 / 500 / 1000 |
| 50 | `EnableGradualDrill1` |  | 0 | pd838 | 二级穿孔过程.启用渐近穿孔 / Second Drill Process.Enable Gradual Drill | 1 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 |
| 51 | `GradualTime1` | ms | 1000 | pd839 | 二级穿孔过程.渐近穿孔时间 / Second Drill Process.Gradual Drill Time | 5500 / 300 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 300 / 500 / 1000 |
| 52 | `BeforeLaserOffDelay1` | ms | 0 | pd840 | 二级穿孔过程.停留时间 / Second Drill Process.Before Laser Off Delay | all = 0 |
| 53 | `AfterLaserOffDelay1` | ms | 0 | pd841 | 二级穿孔过程.停光吹气 / Second Drill Process.After Laser Off Delay | all = 0 |
| 54 | `DrillHeight2` | mm | 10 | pd930 | 三级穿孔基础.穿孔高度 / Third Drill Basic.Third Drill Height | 20 / 10 / 10 / 10 / 10 / 10 / 10 / 10 / 10 / 10 / 10 |
| 55 | `DrillFocusPos2` | mm | 0 | pd931 | 三级穿孔基础.穿孔焦点 / Third Drill Basic.Third Drill Focus Position | all = 0 |
| 56 | `EnableFocusGradual2` |  | 0 | pd932 | 三级穿孔基础.启用焦点渐近 / Third Drill Basic.Enable Focus Graudal | all = 0 |
| 57 | `FocusGradualEndPos2` | mm | 0 | pd934 | 三级穿孔基础.渐近目标焦点 / Third Drill Basic.Focus Graudal End Position | all = 0 |
| 58 | `FocusGradualTime2` | ms | 1000 | pd934 | 三级穿孔基础.渐近目标焦点 / Third Drill Basic.Focus Graudal End Position | 350 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 |
| 59 | `DrillPower2` | % | 50 | pd935 | 三级穿孔激光.穿孔功率(占空比) / Third Drill Laser.Third Drill Power | 80 / 50 / 50 / 50 / 50 / 50 / 50 / 50 / 50 / 50 / 50 |
| 60 | `DrillFreq2` | Hz | 500 | pd936 | 三级穿孔激光.穿孔频率 / Third Drill Laser.Third Drill Frequency | all = 500 |
| 61 | `DrillPeakCurrent2` | % | 100 | pd937 | 三级穿孔激光.峰值功率 / Third Drill Laser.Third Drill Peak Current | 80 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 |
| 62 | `DrillGasType2` | ms | 0 | pd938 | 三级穿孔气体.穿孔气体 / Third Drill Gas.Third Drill Gas Type | 1 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 |
| 63 | `DrillGasPressure2` | V | 5 | pd939 | 三级穿孔气体.穿孔气压 / Third Drill Gas.Third Drill Gas Pressure | 1.4 / 5 / 5 / 5 / 5 / 5 / 5 / 5 / 5 / 5 / 5 |
| 64 | `DrillDelay2` | ms | 1000 | pd940 | 三级穿孔过程.穿孔时长 / Third Drill Process.Third Drill Time | 350 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 |
| 65 | `EnableGradualDrill2` |  | 0 | pd941 | 三级穿孔过程.启用渐近穿孔 / Third Drill Process.Enable Gradual Drill | all = 0 |
| 66 | `GradualTime2` | ms | 1000 | pd942 | 三级穿孔过程.渐近穿孔时间 / Third Drill Process.Gradual Drill Time | 350 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 / 1000 |
| 67 | `BeforeLaserOffDelay2` | ms | 0 | pd943 | 三级穿孔过程.停留时间 / Third Drill Process.Before Laser Off Delay | all = 0 |
| 68 | `AfterLaserOffDelay2` | ms | 0 | pd944 | 三级穿孔过程.停光吹气 / Third Drill Process.After Laser Off Delay | all = 0 |
| 69 | `PowerAdjustWithSpeed` |  | 0 | pd124 | 根据速度实时调节功率 / Dynamic Power | 0 / 1 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 |
| 70 | `Note` |  | (string) | pd153 | 杂项.用户备注 / Misc.Notes | all = "" |
| 71 | `PWMCurveNodes` |  | (string) | pd154 | 杂项.功率曲线点 / Misc.Power Curves | "0,0,15,35,37,69,59,92,76,100,100,100" / "0,0,15,35,37,69,59,92,76,100,100,100" / "" / "" / "" / "" / "" / "" / "0,21,11,59,40,88,60,100,100,100" / "0,100,100,100" / "" |
| 72 | `FreqCurveNodes` |  | (string) | pd155 | 杂项.频率曲线点 / Misc.Freq Curves | "0,100,100,100" / "0,100,100,100" / "" / "" / "" / "" / "" / "" / "0,100,100,100" / "0,100,100,100" / "" |
| 73 | `ManuType` |  | 0 | pd815 | 基础工艺.加工方式 / General Process.Process Type | 4 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 1 / 2 / 1 |
| 74 | `RecentFile` |  | (string) | pd156 | 杂项.最近打开文件 / Misc.Recent Open Files | "C:\Users\HP\Desktop\11.chf" / "C:\Users\HP\Desktop\11.chf" / "" / "" / "" / "" / "" / "" / "C:\Users\HP\Desktop\11.chf" / "" / "" |
| 75 | `PreDrillIsNotUp` |  | 0 | pd1010 | 预穿孔工艺.预穿孔不上抬 / PreDrill Process.Enable PreDrill No Up | all = 0 |
| 76 | `AfterPreDrillMustDrillBeforeCut` |  | 0 | pd1011 | 预穿孔工艺.切割前再次穿孔 / PreDrill Process.Enable Before Cut Drill | all = 0 |
| 77 | `AdvFixHeightCutPos` | mm | 20 | pd129-2 | 高级定高.定高切割位置 / Adv Fix Height.Cut Position | all = 20 |
| 78 | `EnableContourShift` |  | 0 | pd2700 | 图形偏移.启用图形偏移 / Graph Shift.Enable Graph Shift | all = 0 |
| 79 | `ContourShiftXDist` | mm | 0 | pd2701 | 图形偏移.X方向偏移 / Graph Shift.Graph Shift X Direction Length | all = 0 |
| 80 | `ContourShiftYDist` | mm | 0 | pd2702 | 图形偏移.Y方向偏移 / Graph Shift.Graph Shift Y Direction Length | all = 0 |
| 81 | `BoltDrill_Enable_1` |  | 0 | pd2705 | 闪电穿孔.启用 / Bolt Drill.Enable | all = 0 |
| 82 | `BoltDrill_Power_1` |  | 10 | pd2706 | 闪电穿孔.结尾激光功率[占空比] / Bolt Drill.Laser Power Duty [End] | all = 10 |
| 83 | `BoltDrill_Freq_1` |  | 1000 | pd2707 | 闪电穿孔.结尾激光频率 / Bolt Drill.Laser frequency [End] | all = 1000 |
| 84 | `BoltDrill_Enable_2` |  | 0 | pd2705 | 闪电穿孔.启用 / Bolt Drill.Enable | all = 0 |
| 85 | `BoltDrill_Power_2` |  | 10 | pd2706 | 闪电穿孔.结尾激光功率[占空比] / Bolt Drill.Laser Power Duty [End] | all = 10 |
| 86 | `BoltDrill_Freq_2` |  | 1000 | pd2707 | 闪电穿孔.结尾激光频率 / Bolt Drill.Laser frequency [End] | all = 1000 |
| 87 | `BoltDrill_Enable_3` |  | 0 | pd2705 | 闪电穿孔.启用 / Bolt Drill.Enable | all = 0 |
| 88 | `BoltDrill_Power_3` |  | 10 | pd2706 | 闪电穿孔.结尾激光功率[占空比] / Bolt Drill.Laser Power Duty [End] | all = 10 |
| 89 | `BoltDrill_Freq_3` |  | 1000 | pd2707 | 闪电穿孔.结尾激光频率 / Bolt Drill.Laser frequency [End] | all = 1000 |
| 90 | `CleanResidue_Enable` |  | 0 | pd2710 | 除渣.启用 / Clean Residue.Enable | all = 0 |
| 91 | `CleanResidue_WorkH` |  | 10 | pd2711 | 除渣.工作高度 / Clean Residue.Work height | all = 10 |
| 92 | `CleanResidue_WorkV` |  | 10 | pd2712 | 除渣.工作速度 / Clean Residue.Work Speed | all = 10 |
| 93 | `CleanResidue_WorkFocus` |  | 0 | pd2713 | 除渣.激光焦点 / Clean Residue.Laser focus | all = 0 |
| 94 | `CleanResidue_GasType` |  | 0 | pd2714 | 除渣.气体类型 / Clean Residue.Gas type | all = 0 |
| 95 | `CleanResidue_GasP` |  | 5 | pd2715 | 除渣.气压 / Clean Residue.Gas Pressure | all = 5 |
| 96 | `CleanResidue_PeakCurrent` |  | 50 | pd2716 | 除渣.激光峰值功率百分比 / Clean Residue.percent of Laser peakpower (elec Current) | all = 50 |
| 97 | `CleanResidue_Power` |  | 10 | pd2717 | 除渣.激光功率[占空比] / Clean Residue.Laser Power [Duty] | all = 10 |
| 98 | `CleanResidue_Freq` |  | 5000 | pd2718 | 除渣.激光频率 / Clean Residue.Laser frequency | all = 5000 |
| 99 | `CleanResidue_WorkR` |  | 2 | pd2719 | 除渣.工作半径 / Clean Residue.Work radius | all = 2 |
| 100 | `CleanResidue_SpiralTimes` |  | 2 | pd2720 | 除渣.螺旋圈数 / Clean Residue.Coil cycle number | all = 2 |
| 101 | `UD_UpEnable` |  | 0 | pd2730 | 起刀.启用 / Start work Segment.Enable | all = 0 |
| 102 | `UD_UpLen` |  | 10 | pd2731 | 起刀.长度 / Start work Segment.Length | 5 / 5 / 10 / 10 / 10 / 10 / 10 / 10 / 5 / 10 / 10 |
| 103 | `UD_UpSpeed` |  | 100 | pd2732 | 起刀.速度 / Start work Segment.Work Speed | 8.333 / 8.333 / 100 / 100 / 100 / 100 / 100 / 100 / 8.333 / 100 / 100 |
| 104 | `UD_UpAdvEnable` |  | 0 | pd2733 | 起刀.启用功率工艺 / Start work Segment.Enable Laser Control | all = 0 |
| 105 | `UD_UpDuty` |  | 10 | pd2734 | 起刀.激光功率[占空比] / Start work Segment.Laser Power [Duty] | all = 10 |
| 106 | `UD_UpFreq` |  | 1000 | pd2735 | 起刀.激光频率 / Start work Segment.Laser frequency | all = 1000 |
| 107 | `UD_DownEnable` |  | 0 | pd2740 | 收刀.启用 / End work Segment.Enable | all = 0 |
| 108 | `UD_DownLen` |  | 10 | pd2741 | 收刀.长度 / End work Segment.Length | all = 10 |
| 109 | `UD_DownSpeed` |  | 100 | pd2742 | 收刀.速度 / End work Segment.Work Speed | all = 100 |
| 110 | `UD_DownAdvEnable` |  | 0 | pd2743 | 收刀.启用功率工艺 / End work Segment.Enable Laser Control | all = 0 |
| 111 | `UD_DownDuty` |  | 10 | pd2744 | 收刀.激光功率[占空比] / End work Segment.Laser Power [Duty] | all = 10 |
| 112 | `UD_DownFreq` |  | 1000 | pd2745 | 收刀.激光频率 / End work Segment.Laser frequency | all = 1000 |
| 113 | `ZFVibAbat_Level` |  | 0 | pd2747 | ZF振动抑制.薄板抑制系数(0~90) / ZF vibration Abatement.Abatement factor - [thin] | all = 0 |
| 114 | `ZFVibAbat_Level_Thick` |  | 0 | pd2749 | ZF振动抑制.厚板抑制系数(0~90) / ZF vibration Abatement.Abatement factor - [thick] | 90 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 |
| 115 | `ZFVibAbat_Coef` |  | 200 | pd2748 | ZF振动抑制.系数 / ZF vibration Abatement.Coefficient | all = 200 |
| 116 | `LeadLineParam_Enable` |  | 0 | newLang40 | 引线工艺.启用 / Lead Process. Enable | all = 0 |
| 117 | `DrillHeight3` | mm | 5 | newLang60 | 四级穿孔基础.穿孔高度 / Four Drill Basic.Four Drill Height | all = 5 |
| 118 | `DrillFocusPos3` | mm | 0 | newLang61 | 四级穿孔基础.穿孔焦点 / Four Drill Basic.Four Drill Focus Position | all = 0 |
| 119 | `EnableFocusGradual3` |  | 0 | newLang62 | 四级穿孔基础.启用焦点渐近 / Four Drill Basic.Enable Focus Graudal | all = 0 |
| 120 | `FocusGradualEndPos3` | mm | 0 | newLang63 | 四级穿孔基础.渐近目标焦点 / Four Drill Basic.Focus Graudal End Position | all = 0 |
| 121 | `FocusGradualTime3` | ms | 1000 | newLang64 | 四级穿孔基础.焦点渐近时间 / Four Drill Basic.Focus Graudal Time | all = 1000 |
| 122 | `DrillPower3` | % | 50 | newLang65 | 四级穿孔激光.穿孔功率(占空比) / Four Drill Laser.Four Drill Power | all = 50 |
| 123 | `DrillFreq3` | Hz | 500 | newLang66 | 四级穿孔激光.穿孔频率 / Four Drill Laser.Four Drill Frequency | all = 500 |
| 124 | `DrillPeakCurrent3` | % | 100 | newLang67 | 四级穿孔激光.峰值功率 / Four Drill Laser.Four Drill Peak Current | all = 100 |
| 125 | `DrillGasType3` | ms | 0 | newLang68 | 四级穿孔气体.穿孔气体 / Four Drill Gas.Four Drill Gas Type | all = 0 |
| 126 | `DrillGasPressure3` | V | 5 | newLang69 | 四级穿孔气体.穿孔气压 / Four Drill Gas.Four Drill Gas Pressure | all = 5 |
| 127 | `DrillDelay3` | ms | 1000 | newLang70 | 四级穿孔过程.穿孔时长 / Four Drill Process.Four Drill Time | all = 1000 |
| 128 | `EnableGradualDrill3` |  | 0 | newLang71 | 四级穿孔过程.启用渐近穿孔 / Four Drill Process.Enable Gradual Drill | all = 0 |
| 129 | `GradualTime3` | ms | 1000 | newLang72 | 四级穿孔过程.渐近穿孔时间 / Four Drill Process.Gradual Drill Time | all = 1000 |
| 130 | `BeforeLaserOffDelay3` | ms | 0 | newLang73 | 四级穿孔过程.停留时间 / Four Drill Process.Before Laser Off Delay | all = 0 |
| 131 | `AfterLaserOffDelay3` | ms | 0 | newLang74 | 四级穿孔过程.停光吹气 / Four Drill Process.After Laser Off Delay | all = 0 |
| 132 | `BoltDrill_Enable_4` |  | 0 | pd2705 | 闪电穿孔.启用 / Bolt Drill.Enable | all = 0 |
| 133 | `BoltDrill_Power_4` |  | 10 | pd2706 | 闪电穿孔.结尾激光功率[占空比] / Bolt Drill.Laser Power Duty [End] | all = 10 |
| 134 | `BoltDrill_Freq_4` |  | 1000 | pd2707 | 闪电穿孔.结尾激光频率 / Bolt Drill.Laser frequency [End] | all = 1000 |
| 135 | `DrillHeight4` | mm | 5 | newLang80 | 五级穿孔基础.穿孔高度 / Five Drill Basic.Five Drill Height | all = 5 |
| 136 | `DrillFocusPos4` | mm | 0 | newLang81 | 五级穿孔基础.穿孔焦点 / Five Drill Basic.Five Drill Focus Position | all = 0 |
| 137 | `EnableFocusGradual4` |  | 0 | newLang82 | 五级穿孔基础.启用焦点渐近 / Five Drill Basic.Enable Focus Graudal | all = 0 |
| 138 | `FocusGradualEndPos4` | mm | 0 | newLang83 | 五级穿孔基础.渐近目标焦点 / Five Drill Basic.Focus Graudal End Position | all = 0 |
| 139 | `FocusGradualTime4` | ms | 1000 | newLang84 | 五级穿孔基础.焦点渐近时间 / Five Drill Basic.Focus Graudal Time | all = 1000 |
| 140 | `DrillPower4` | % | 50 | newLang85 | 五级穿孔激光.穿孔功率(占空比) / Five Drill Laser.Five Drill Power | all = 50 |
| 141 | `DrillFreq4` | Hz | 500 | newLang86 | 五级穿孔激光.穿孔频率 / Five Drill Laser.Five Drill Frequency | all = 500 |
| 142 | `DrillPeakCurrent4` | % | 100 | newLang87 | 五级穿孔激光.峰值功率 / Five Drill Laser.Five Drill Peak Current | all = 100 |
| 143 | `DrillGasType4` | ms | 0 | newLang88 | 五级穿孔气体.穿孔气体 / Five Drill Gas.Five Drill Gas Type | all = 0 |
| 144 | `DrillGasPressure4` | V | 5 | newLang89 | 五级穿孔气体.穿孔气压 / Five Drill Gas.Five Drill Gas Pressure | all = 5 |
| 145 | `DrillDelay4` | ms | 1000 | newLang90 | 五级穿孔过程.穿孔时长 / Five Drill Process.Five Drill Time | all = 1000 |
| 146 | `EnableGradualDrill4` |  | 0 | newLang91 | 五级穿孔过程.启用渐近穿孔 / Five Drill Process.Enable Gradual Drill | all = 0 |
| 147 | `GradualTime4` | ms | 1000 | newLang92 | 五级穿孔过程.渐近穿孔时间 / Five Drill Process.Gradual Drill Time | all = 1000 |
| 148 | `BeforeLaserOffDelay4` | ms | 0 | newLang93 | 五级穿孔过程.停留时间 / Five Drill Process.Before Laser Off Delay | all = 0 |
| 149 | `AfterLaserOffDelay4` | ms | 0 | newLang94 | 五级穿孔过程.停光吹气 / Five Drill Process.After Laser Off Delay | all = 0 |
| 150 | `BoltDrill_Enable_5` |  | 0 | pd2705 | 闪电穿孔.启用 / Bolt Drill.Enable | all = 0 |
| 151 | `BoltDrill_Power_5` |  | 10 | pd2706 | 闪电穿孔.结尾激光功率[占空比] / Bolt Drill.Laser Power Duty [End] | all = 10 |
| 152 | `BoltDrill_Freq_5` |  | 1000 | pd2707 | 闪电穿孔.结尾激光频率 / Bolt Drill.Laser frequency [End] | all = 1000 |
| 153 | `ZFVibAbatType` | ms | 0 | A240827_4 | ZF振动抑制.振动抑制 / ZF vibration suppression. Vibration suppression | 2 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 |
| 154 | `LayerFileName` |  | (string) | pd154 | 杂项.功率曲线点 / Misc.Power Curves | "Carbon 10.0mm  4.0D F+12 O2" / "Carbon 1.0mm  2.0S  F-2 N2" / "" / "" / "" / "" / "" / "" / "3.0 SUS  单3.0  F-1" / "" / "" |
| 155 | `EnableSmoothPierce` |  | 0 | A241009_3 | 无感穿孔 / Smooth pierce | all = 0 |
| 156 | `SmoothPierceDrillHeight` | mm | 5 | A241009_4 | 无感穿孔.穿刺高度 / Smooth pierce.height | all = 5 |
| 157 | `SmoothPierceDrillTime_ms` | ms | 100 | A241009_5 | 无感穿孔.穿刺时间 / Smooth pierce.time | all = 100 |
| 158 | `SmoothPierceDrillFocusPos` | mm | 0 | A241009_6 | 无感穿孔.穿刺焦点 / Smooth pierce.focus | all = 0 |
| 159 | `SmoothPierceDrillPower` | % | 50 | A241010_1 | 无感穿孔激光.占空比 / Smooth pierce Laser.Duty cycle | all = 50 |
| 160 | `SmoothPierceDrillFreq` | Hz | 500 | A241010_2 | 无感穿孔激光.切割频率 / Smooth pierce Laser.Frequency | all = 500 |
| 161 | `SmoothPierceDrillPeakCurrent` | % | 100 | A241010_3 | 无感穿孔激光.峰值功率 / Smooth pierce Laser.Power | all = 100 |
| 162 | `PowerCurveSmoothType` |  | 0 | A241012_4 | 平滑方式: / Smooth Type | all = 0 |
| 163 | `FreqCurveSmoothType` |  | 0 | A241012_4 | 平滑方式: / Smooth Type | all = 0 |
| 164 | `FreqAdjustWithSpeed` |  | 0 | A241012_5 | 根据速度实时调节频率 / Dynamic Freq | all = 0 |

#### Group `CO2LayerParam` (elements `PCO2LayerParam1..PCO2LayerParam11`)

| # | Attribute | Unit | Default | Lang ID | Label (zh / en) | Values L1..L11 (BkLayerPara.xml) |
|---|---|---|---|---|---|---|
| 1 | `PowerCurveSmoothType` |  | 0 | A241012_4 | 平滑方式: / Smooth Type | all = 0 |
| 2 | `FreqCurveSmoothType` |  | 0 | A241012_4 | 平滑方式: / Smooth Type | all = 0 |
| 3 | `PowerAdjustWithSpeed` |  | 0 | pd124 | 根据速度实时调节功率 / Dynamic Power | all = 0 |
| 4 | `FreqAdjustWithSpeed` |  | 0 | A241012_5 | 根据速度实时调节频率 / Dynamic Freq | all = 0 |
| 5 | `CutSpeed` | mm / s | 100 | pd125 | 切割基础.切割速度 / Cut Basic.Cut Speed | 600 / 50 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 10 |
| 6 | `AdvFixHeightCutPos` | mm | 20 | pd129-2 | 高级定高.定高切割位置 / Adv Fix Height.Cut Position | 0 / 0 / 20 / 20 / 20 / 20 / 20 / 20 / 0 / 20 / 0 |
| 7 | `LaserOnDelay` | ms | 0 | A241025_3 | 切割激光.开光延时 / Cutting laser. Light delay | all = 0 |
| 8 | `CutDuty` | % | 100 | pd127 | 切割激光.占空比 / Cut Laser.Cut Power | 20 / 4 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 / 100 |
| 9 | `CutGasType` |  | 0 | pd130 | 切割气体.切割气体 / Cut Gas.Gas Type | 3 / 3 / 0 / 0 / 0 / 0 / 0 / 0 / 3 / 0 / 3 |
| 10 | `CutAirPressure` | V | 5 | pd131 | 切割气体.切割气压 / Cut Gas.Gas Pressure | all = 5 |
| 11 | `ShortDistGasKeepOn` |  | 0 | A241025_1 | 切割气体.短距离不关气 / Cut gas.No gas for short distance | 1 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 |
| 12 | `NoCloseGasInManu` |  | 0 | A241025_2 | 切割气体.加工中不关气 / Cut gas.No gas in processing | 1 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 / 0 |
| 13 | `SlowStart` |  | 0 | pd133 | 高级工艺.启用慢速起步 / Adv Parameters.Slow Start | all = 0 |
| 14 | `SlowStartLength` | mm | 1 | pd134 | 高级工艺.起步距离 / Adv Parameters.Start Length | all = 1 |
| 15 | `SlowStartSpeed` | mm / s | 10 | pd135 | 高级工艺.起步速度 / Adv Parameters.Start Speed | all = 10 |
| 16 | `PWMCurveNodes` |  | (string) | pd154 | 杂项.功率曲线点 / Misc.Power Curves | "0,48,22,66,50,87,100,100" / "0,100,100,100" / "0,100,100,100" / "" / "" / "" / "" / "" / "0,100,100,100" / "0,100,100,100" / "0,100,100,100" |
| 17 | `FreqCurveNodes` |  | (string) | pd155 | 杂项.频率曲线点 / Misc.Freq Curves | "0,100,100,100" / "0,100,100,100" / "0,100,100,100" / "" / "" / "" / "" / "" / "0,100,100,100" / "0,100,100,100" / "0,100,100,100" |
| 18 | `LayerFileName` |  | (string) | pd154 | 杂项.功率曲线点 / Misc.Power Curves | "亚克力24mm" / "" / "" / "" / "" / "" / "" / "" / "" / "" / "" |
| 19 | `Note` |  | (string) | pd153 | 杂项.用户备注 / Misc.Notes | all = "" |
| 20 | `RecentFile` |  | (string) | pd156 | 杂项.最近打开文件 / Misc.Recent Open Files | all = "" |
| 21 | `CutFreq` | Hz | 5000 | pd128 | 切割激光.切割频率 / Cut Laser.Cut Freq | all = 5000 |

### 2.4 Units and value conventions (EVIDENCE → INFERENCE)

| Quantity | Stored as | Evidence | Display |
|---|---|---|---|
| Speeds (`CutSpeed`, `SlowStartSpeed`, `UD_*Speed`, `CleanResidue_WorkV`) | **mm/s** | descriptor unit `mm/s`; `UN.SpeedUnit="1"` in `BkManuPara.xml` (`pd107 单位.速度单位 / Unit.Speed Unit`); the Carbon-10 mm preset stores `CutSpeed="11.000000000000002"` while the vendor spreadsheet lists 0.66 m/min for the same material (0.66 m/min = 11.0 mm/s); `250` ↔ 15 m/min; `83.333` ↔ 5 m/min. | m/min in the UI (INFERENCE high). VERIFIER: the unit suffixes `(mm/s)`, `(m/min)`, `(inch/s)`, `(inch/min)` sit together at file 0x42a23c/0x42a22c/0x42a218/0x42a200 (bottom-up → source order mm/s, m/min, inch/s, inch/min), so `UN.SpeedUnit` is most likely `0 = mm/s, 1 = m/min, 2 = inch/s, 3 = inch/min` (INFERENCE medium-high); stored value `1` = m/min matches. Spreadsheet row `碳钢 10 mm`: `0.66` m/min, pressure `0.6` bar, duty `88` — matches slot 1 / the 10 mm preset exactly. |
| Laser power (`CutPower`, `DrillPower*`, `*_Power`, `*Duty`) | **% PWM duty** | `pd127 切割激光.占空比 / Cut Laser.Cut Power` (占空比 = duty cycle); `ec21 PWM占空比超出范围（0-100）` (duty out of range 0-100) | % |
| `CutPeakCurrent`, `DrillPeakCurrent*`, `*_PeakCurrent` | **% of laser peak power (analogue/DA "peak current")** | `pd132 切割激光.峰值功率 / Cut Laser.Peak Current`, `pd2716 除渣.激光峰值功率百分比 / percent of Laser peakpower (elec Current)`; laser control type `LGP.LaserControlType="3"`, `LaserDAPort="1"` in `BkHardPara.xml` | % |
| Frequencies (`CutFreq`, `DrillFreq*`, `*_Freq`) | **Hz** (PWM) | descriptor unit `Hz`; `ec19 PWM频率值超出范围（1-50000Hz）` | Hz |
| Heights (`CutHeight`, `DrillHeight*`, `UpHeight`, `SmoothPierceDrillHeight`, `CleanResidue_WorkH`) | **mm** above sheet (capacitive follower) | descriptor unit `mm`; `lp16/17/18/19` cross-checks | mm |
| Focus (`CutFocusPos`, `DrillFocusPos*`, `FocusGradualEndPos*`, `*_WorkFocus`) | **mm** (signed) | descriptor unit `mm`; `mf434 焦点位置(mm): %0.1f`; the auto-focus head is disabled on this machine (`AF.AFType="0"`, `AFDA.DAPort="0"`) which is why every preset has `CutFocusPos="0"` even though file names say `F+12`, `F-3` etc. (INFERENCE high: focus is set manually here) | mm |
| Gas pressure (`CutAirPressure`, `DrillGasPressure*`, `CleanResidue_GasP`) | number, **descriptor unit is `V`** (proportional-valve DA volts) but the values are in **bar** and mapped to volts through a calibration table | `gpa5 电压(V) / Voltage(V)`, `gpa6 气压(Bar) / GasP(Bar)`, `pd1600-1606 气压映射 / Gas Pressure Map`; `MGP.DAMaxPressure="10"`, `NewDAMAxPressureO2="10"`; `DO.GtO2EnableGasDAMap="1"`; the Carbon-10 mm preset stores `0.6` and the spreadsheet says 0.6 bar for that material. `pd902 单位.气压单位 / Unit.Gas Pressure Unit` = 0. | bar (INFERENCE high). Only the O2 channel is on a proportional valve here (`RatioO2="2"`); the N2/Air "high pressure" channels are on/off DOs, so the stored `0.85` for N2 presets is not what regulates the 12–16 bar named in the spreadsheet (INFERENCE medium). VERIFIER (downgraded to **medium**): the "mapped through the gas pressure map" part is **not** supported — all three correction tables are empty in the backup (`PDIParam/DO.GtAirGasDAMapStr=""`, `GtO2GasDAMapStr=""`, `GtN2GasDAMapStr=""`, descriptor lang `pd1603..1605 气压映射.*气压校正数据 / Gas Pressure Map.* Adjust Data`), even though `GtO2EnableGasDAMap="1"`. What *is* supported: `UN.GasPressureUnit` (`pd902`) has the option labels `pd900 bar / bar` and `pd901 MPa / MPa` (INFERENCE high: 0 = bar, 1 = MPa; the machine stores 0), and `MGP.DAMaxPressure` is labelled `pd275 比例阀.最高气压 / Proportional Valve.Max Pressure` (= pressure at full DA scale). With `DAMaxPressure=10` on a 0–10 V DA, 1 bar ≙ 1 V numerically, which is why a descriptor unit of `V` and a stored value of `0.6` bar are not in conflict. |
| Times (`*Delay*`, `DrillDelay*`, `GradualTime*`, `FocusGradualTime*`, `SmoothPierceDrillTime_ms`) | **ms** | descriptor unit `ms` | ms |
| Booleans (`NoManu`, `Enable*`, `SlowStart`, `WithFilm`, …) | `0`/`1` ints | | checkbox |
| `PWMCurveNodes`, `FreqCurveNodes` | comma-separated flat list of (x,y) pairs, x = speed % of `CutSpeed`, y = power/frequency % | see §3.4 | curve editor (`lp2 功率曲线 / Power`, `lp3 频率曲线 / Freq`) |

### 2.5 Enumerations

**Gas type** (`CutGasType`, `DrillGasType0..4`, `CleanResidue_GasType`). EVIDENCE: the manual-gas buttons `mp43 空气 / Air`, `mp44 氧气 / O2`, `mp45 氮气 / N2`, `mp46 高压空气 / High Air`, `mp47 高压氧气 / High O2`, `mp48 高压氮气 / High N2` and the DO-port names `pd7..pd12` (`空气, 氧气, 氮气, 高压空气, 高压氧气, 高压氮气`) enumerate the same six gases in the same order; the hardware config `PGasParam/MGP` in `BkHardPara.xml` maps them to outputs: `LowAir=0 LowO2=7 LowN2=0 HighAir=3 HighO2=0 HighN2=2` (DO numbers, 0 = unassigned) plus proportional valve `RatioO2=2`. Values actually used in the data: 1 for oxygen cutting of carbon steel, 5 for all N2 presets (stainless, aluminium, copper, thin carbon), 3 for every CO2 layer, 0 as default.
INFERENCE (high): `0=Air(low) 1=O2(low) 2=N2(low) 3=High Air 4=High O2 5=High N2`. (The machine's "1 MPa air" presets also use 5 → the "High N2" solenoid is plumbed to compressed air on this particular machine.) `MGP.CoolGas` (`pd946 杂项.冷却气 / Misc.Cool Down Gas`) selects the gas for cool points.

**`ManuType`** (`pd815 基础工艺.加工方式 / General Process.Process Type`). EVIDENCE: the descriptor record is preceded by the option-label list `pd810 直接切割 / Direct Cut`, `pd811 定高切割 / Fix Height Cut`, `pd812 一级穿孔 / One Step Drill`, `pd813 二级穿孔 / Two Steps Drill` (and `pd814 三级穿孔 / Three Steps Drill` exists in lang.txt; `pd811-1 高级定高 / Adv Fix Height Cut`). The newer layer dialog (strings at file 0x42794c..0x427a34) shows two combo lists: `newLang50 标准切割 / Standard cutting`, `pd811 定高切割`, `pd811-1 高级定高` and `newLang56 不启用 / Not enabled`, `newLang51..55 一级…五级穿孔 / 1st…5th level perforation`. Data: presets with three configured pierce stages have `ManuType=4` (Carbon 10 mm, Carbon 8 mm, Al 5 mm air); with two stages `3` (Al 3 mm, Cu 2/3 mm); one stage `2` (most 3–6 mm presets); thin sheets `0` (Carbon 1 mm N2, SS 1/2 mm, Cu 1 mm); the cleaning/quenching presets and the film layer (slot 11) use `1`.
INFERENCE (high for 0–4, low for ≥5): `0 = direct cut (no pierce)`, `1 = fixed-height cut (no pierce, cut at CutHeight without lowering to a pierce height)`, `2 = 1-stage pierce`, `3 = 2-stage pierce`, `4 = 3-stage pierce`; codes for advanced fixed height and 4/5-stage pierce are probably `5..7` but no sample uses them.

VERIFIER (downgraded to **medium-high for 0–4; the "5..7" guess is withdrawn**): (a) the property-grid option list bound to this attribute contains only four labels (`pd810..pd813`, builder at 0x7775c0) — `pd814 三级穿孔 / Three Steps Drill` exists in `lang.txt` but **not in `MainApp.exe`**, so the value `4` found in the data cannot come from that list; (b) `newLang54` / `newLang55` (4th/5th-level perforation) are also **absent from `MainApp.exe`** — the second combo of the new dialog only carries `newLang56, newLang51, newLang52, newLang53` (pushes at 0x540223, 0x540397, … inside the dialog code 0x53f300–0x540400, each label in its own `switch` arm together with the lang-pack section string `语言包`), so this build's UI cannot select a 4- or 5-stage pierce even though the five attribute blocks exist; (c) the first combo carries `newLang50 标准切割 / Standard cutting`, `pd811 定高切割 / Fix Height Cut`, `pd811-1 高级定高 / Adv Fix Height Cut` (pushes 0x53f782, 0x53f8f6, 0x53fa7f). No `CB_GETCURSEL`/`CB_SETCURSEL` message pushes exist in that region — the controls are BCGP property-grid items (`Value`/`Property` strings), so the combined code is produced by the dialog's own arithmetic, which I did not locate. The 0–4 mapping therefore rests on the 55 sample files only (pierce-stage count = `ManuType − 1` for `ManuType ≥ 2`; `1` for the fixed-height cleaning/quenching/film recipes; `0` for thin sheets).

**`ZFVibAbatType`** (`A240827_4 ZF振动抑制.振动抑制 / ZF vibration suppression`): options `A240830_1 不启用 / Not Enabled`, `A240827_2 薄板抑制 / Thin plate suppression`, `A240827_3 厚板抑制 / Thick plate suppression` → INFERENCE (high) `0/1/2`; the 10 mm and 8 mm carbon presets use `2` with `ZFVibAbat_Level_Thick=90`.

**`PowerCurveSmoothType` / `FreqCurveSmoothType`** (`A241012_4 平滑方式 / Smooth Type`): `A241012_1 默认 / Default`, `A241012_2 线性 / Line[ar]`, `A241012_3 平滑 / Smooth` → `0/1/2` (INFERENCE high).

**Gradual-pierce mode** (global, `ZF` group): `pd667 渐进穿孔.渐进方式 / Gradual Type` with options `pd665 按速度渐近 / Gradual On Speed`, `pd666 按时间渐近 / Gradual On Time`; the layer attributes only carry the time variant (`GradualTime*`), so the per-layer model is "by time" (INFERENCE medium).

---

## 3. The process model

This section reconstructs what the controller does with the attributes. Global (non-layer) parameters that participate are quoted from `File/BkManuPara.xml` (`MC`, `GC`, `FC`, `MP`, `GP` sub-elements) and `File/BkHardPara.xml`, with their descriptor labels.

### 3.1 Per-layer switches

| Attribute | Label | Meaning (EVIDENCE / INFERENCE) |
|---|---|---|
| `NoManu` | `pd112 此图层不加工 / Uncut` | Layer is not machined (skipped, drawn only). |
| `NoFollow` | `pd114 此图层不跟随 / Unfollow` | Capacitive height follow disabled for this layer (cut at a fixed Z). Combined with `ManuType=1` and `AdvFixHeightCutPos` (`pd129-2 高级定高.定高切割位置 / Adv Fix Height.Cut Position`, mm) for fixed-height cutting. |
| `ShortDistNoUp` | `pd113 短距离不上抬 / Short Unlift` | Do not lift the head between contours whose start points are closer than `FC.ShortNoUpMaxLength` (10 mm here; tooltip `de10`: "当两个切割轮廓的起点之间的距离小于该值时，则切割头不会上抬，而是一直保持跟随状态" = when the distance between the start points of two contours is smaller than this value the head is not lifted but stays in follow mode). |
| `ShortDistGasKeepOn` | `pd113-1 短距离不关气 / Short Keep Gas On` | Keep assist gas on during such short moves. |
| `NoCloseGasInManu` | `pd117 加工中不关气 / Keep Gas On` | Never close the gas valve during the whole job (saves `GC.GasDelay`). |
| `PreDrill` | `pd115 预先穿孔加工 / PreDrill` | Pre-pierce pass: pierce all start points first, then cut (§3.6). |
| `PreDrillIsNotUp` | `pd1010 预穿孔工艺.预穿孔不上抬 / PreDrill Process.Enable PreDrill No Up` | Do not lift between pre-pierces. |
| `AfterPreDrillMustDrillBeforeCut` | `pd1011 预穿孔工艺.切割前再次穿孔 / Enable Before Cut Drill` | Pierce again when the cut starts (re-pierce). |
| `WithFilm` | `pd116 启用带膜切割 / With Film` | Layer is a film-removal ("de-film") pass; validation `lp19`: "图层%d中的【切割高度】不能大于【带膜切割高度】" (cut height must not exceed the with-film cut height). |
| `EnableContourShift`, `ContourShiftXDist/YDist` | `pd2700..2702 图形偏移 / Graph Shift` (mm) | Shift this layer's geometry by (dx, dy) at run time (also exists globally as `MC.EnableContourShift`). |
| `LeadLineParam_Enable` | `newLang40 引线工艺.启用 / Lead Process. Enable` | Enable the per-layer lead-in/lead-out process (§3.5). |
| `PowerAdjustWithSpeed`, `FreqAdjustWithSpeed` | `pd124 根据速度实时调节功率 / Dynamic Power`, `A241012_5 根据速度实时调节频率 / Dynamic Freq` | Enable the speed→power / speed→frequency curves (§3.4). |
| `Note`, `LayerFileName`, `RecentFile` | `pd153 杂项.用户备注 / Notes`, (technology name), `pd156 杂项.最近打开文件 / Recent Open Files` | House-keeping strings: `LayerFileName` is the technology-library preset name the slot was loaded from (`Carbon 10.0mm  4.0D F+12 O2`, `3.0 SUS  单3.0  F-1` = "3.0 stainless, single-3.0 nozzle, focus −1", `亚克力24mm` = "acrylic 24 mm"); `RecentFile` is the last `.chf` open when the slot was saved. |

### 3.2 Cutting parameters (per contour)

`CutHeight` (`pd129 切割基础.切割高度 / Cut Height`, mm), `CutFocusPos` (`pd589 切割基础.切割焦点 / Cut Focus Position`), `CutSpeed` (`pd125`), `CutPower` (`pd127`, duty %), `CutFreq` (`pd128`), `CutPeakCurrent` (`pd132`), `CutGasType` (`pd130`), `CutAirPressure` (`pd131`), `LaserOnDelay` (`pd126 切割过程.停留时间 / Laser On Delay` — dwell after the laser is switched on and before motion starts; CO2 variant `A241025_3 切割激光.开光延时 / Light delay`), `LaserOffBeforeDelay` (`pd137 关光前延时` — dwell at contour end before laser off), `LaserOffAfterDelay` (`pd138 关光后延时` — dwell after laser off, gas still on), `UpHeight` (`pd136 高级工艺.上抬高度 / Up Height`, mm — lift height for rapid moves; `lp16`: cut height must not exceed up height), `SlowStart` / `SlowStartLength` / `SlowStartSpeed` (`pd133..135 高级工艺.启用慢速起步 / 起步距离 / 起步速度` — creep at `SlowStartSpeed` for the first `SlowStartLength` mm of each contour), `PreLaserOnFactor` (`pd853 高级工艺.出光系数 / Pre Laser On Factor`, unit `mm/s` in the descriptor — a laser-on lead applied ahead of motion; also global `MP.PreLaserOnFactor`; INFERENCE low: distance = factor × speed).

Validation rules enforced by the editor (EVIDENCE `lp16..lp19`): `CutHeight ≤ UpHeight`, `CutHeight ≤ DrillHeight`, `DrillHeight ≤ Three-Level DrillHeight` (`三级穿孔高度`), `CutHeight ≤ WithFilm CutHeight`.

VERIFIER (downgraded): only **`lp19`** is referenced by `MainApp.exe` (two sites, 0x4fcfdd and 0x54d05d); the IDs `lp16`, `lp17`, `lp18` do not occur in the binary at all, so the first three rules are legacy `lang.txt` entries that this build does **not** enforce. The `lp19` check at 0x54cfd5–0x54d065 loops over layer indices 0..9 (`cmp [ebp-0x68],0xa`) and `fcomp`s the film layer's `CutHeight` at a fixed struct offset `[base+0x7b08]` against `[base+0x4e08 + i*0x480]` — incidentally showing that the in-memory layer table is an array of 11 records of **0x480 bytes** (= 144 doubles) starting at +0x4e08 in the parameter object, slot 10 (film) at +0x7b08 (INFERENCE medium on the stride; the two offsets and the loop bound are EVIDENCE).

### 3.3 Pierce stages (`穿孔` = perforation/pierce; "drill" in the vendor English)

Five identical stage blocks exist, suffix `0..4` = `一级..五级穿孔` (1st..5th level). Stage labels: `pd816..pd828/pd845/pd847` (1st), `pd829..pd841/pd846/pd848` (2nd), `pd930..pd944` (3rd), `newLang60..74` (4th), `newLang80..94` (5th). Per stage:

| Attribute (stage k) | Label (1st stage shown) | Meaning |
|---|---|---|
| `DrillHeight{k}` | `一级穿孔基础.穿孔高度 / First Drill Height` (mm) | Nozzle height during this stage. |
| `DrillFocusPos{k}` | `穿孔焦点 / Drill Focus Position` (mm) | Focus during this stage (auto-focus head). |
| `EnableFocusGradual{k}`, `FocusGradualEndPos{k}`, `FocusGradualTime{k}` | `启用焦点渐近 / Enable Focus Gradual`, `渐近目标焦点 / Focus Gradual End Position`, `焦点渐近时间 / Focus Gradual Time` (ms) | Ramp the focus from `DrillFocusPos` to the end position over the given time while piercing. |
| `DrillPower{k}`, `DrillFreq{k}`, `DrillPeakCurrent{k}` | `穿孔功率(占空比) / Drill Power`, `穿孔频率 / Drill Frequency`, `峰值功率 / Drill Peak Current` | Laser settings during this stage. |
| `DrillGasType{k}`, `DrillGasPressure{k}` | `穿孔气体 / Gas Type`, `穿孔气压 / Gas Pressure` | Gas for this stage (may differ from the cut gas, e.g. O2 pierce / N2 cut). |
| `DrillDelay{k}` | `穿孔时长 / Drill Time` (ms) | Duration of the stage (laser on). |
| `EnableGradualDrill{k}`, `GradualTime{k}` | `启用渐近穿孔 / Enable Gradual Drill`, `渐近穿孔时间 / Gradual Drill Time` (ms) | "Gradual pierce": descend from `DrillHeight{k}` towards the next stage height / the cut height over `GradualTime` while the laser is on (global helpers: `pd658 渐进穿孔.渐进终点高度 / Gradual End Height`, `pd145 渐进速度 / Gradual Speed`, `pd146 渐进后延时 / After Gradual Delay`, Z-follower registers `pd413 渐进穿孔速度 / Gradual Drill Speed`). In every preset `GradualTime{k} == DrillDelay{k}` (the editor keeps them equal). |
| `BeforeLaserOffDelay{k}` | `停留时间 / Before Laser Off Delay` (ms) | Dwell (laser still on, no motion) at the end of the stage. |
| `AfterLaserOffDelay{k}` | `停光吹气 / After Laser Off Delay` (ms; literally "stop light, blow gas") | Gas blow-off after laser off, before the next stage. |
| `BoltDrill_Enable_{k+1}`, `BoltDrill_Power_{k+1}`, `BoltDrill_Freq_{k+1}` | `pd2705..2707 闪电穿孔.启用 / 结尾激光功率[占空比] / 结尾激光频率` ("lightning pierce": enable / end laser duty / end laser frequency) | Optional short high-energy burst at the end of stage k (INFERENCE high on placement: the attributes are numbered 1..5 and interleaved with stages 0..4 in the descriptor table). |

Which stages run is selected by `ManuType` (§2.5). Sample: the 10 mm carbon-steel preset (slot 1 / `Carbon 10.0mm 4.0D F+12 O2`) = 3-stage pierce, O2 throughout:

| stage | height | power/freq/peak | gas / bar | time | gradual | dwell / blow |
|---|---|---|---|---|---|---|
| 0 | 6 mm | 65 % / 150 Hz / 60 % | O2 / 0.75 | 1500 ms | off | 500 / 50 ms |
| 1 | 18 mm | 85 % / 200 Hz / 85 % | O2 / 1.2 | 5500 ms | **on** (5500 ms) | 0 / 0 |
| 2 | 20 mm | 80 % / 500 Hz / 80 % | O2 / 1.4 | 350 ms | off | 0 / 0 |
| cut | 0.6 mm | 88 % / 2000 Hz / 88 % | O2 / 0.6 | 11 mm/s (0.66 m/min) | `LaserOnDelay` 10 ms | `UpHeight` 15 mm |

(Stage order in the descriptor/UI is 1st→2nd→3rd; the physical order used by the firmware is not directly observable — INFERENCE medium that stages execute 0,1,2 in order.)

Related global parameters: `MP.DirectDrillMaxHeight=6` (`pd789 穿孔参数.直接穿孔最大高度 / Direction Drill Max Height`) and `MP.DirectSecondDrillMaxHeight=4` (`pd849`) — height limits above which the Z-follower cannot "drill directly" (INFERENCE medium: if a stage height exceeds this the head is positioned in follow mode rather than by the follower's drill command); Z-follower I/O `pd19 调高穿孔 / Z Drill` (DO), `pd26 穿孔到位 / Drill in Place` (DI), follower status texts `mp13 调高器 <跟随中> / FTC <Follow>`, `mp14 调高器 <穿孔中> / FTC <Drill>`; `pd655 调高器参数.穿孔容差 / Drill Tolerance`.

**Smooth pierce** (`无感穿孔` "imperceptible pierce", `A241009_3`): `EnableSmoothPierce`, `SmoothPierceDrillHeight` (`A241009_4 穿刺高度`, mm), `SmoothPierceDrillTime_ms` (`A241009_5 穿刺时间`), `SmoothPierceDrillFocusPos` (`A241009_6`), `SmoothPierceDrillPower/Freq/PeakCurrent` (`A241010_1..3`). INFERENCE (medium): a single very short pierce used instead of the staged pierce for thin material (all presets have it disabled).

**Residue cleaning** (`除渣`, `pd2710..2720`): `CleanResidue_Enable`, `_WorkH` (`工作高度` height), `_WorkV` (`工作速度` speed), `_WorkFocus`, `_GasType`, `_GasP`, `_PeakCurrent`, `_Power`, `_Freq`, `_WorkR` (`工作半径` radius), `_SpiralTimes` (`螺旋圈数` number of spiral turns). INFERENCE (high): after piercing, run a spiral of radius `WorkR` for `SpiralTimes` turns to clear the pierce slag before starting the cut.

### 3.4 Power / frequency curves

`PWMCurveNodes` / `FreqCurveNodes` (`pd154 杂项.功率曲线点 / Misc.Power Curves`, `pd155 杂项.频率曲线点 / Misc.Freq Curves`), enabled by `PowerAdjustWithSpeed` / `FreqAdjustWithSpeed`, smoothed per `PowerCurveSmoothType` / `FreqCurveSmoothType`.

EVIDENCE: values are flat integer lists of even length whose last pair is always `100,100` and whose first x is `0`: `0,0,15,35,37,69,59,92,76,100,100,100` (6 nodes), `0,21,11,59,40,88,60,100,100,100`, `0,48,22,66,50,87,100,100`, default `0,100,100,100` (2 nodes); the editor caption is `lp2 功率曲线 / Power`; the property grid shows `Value/Property` pairs (`0x4279b8`). The global switch `MP.IsEnablePWMPerContour="1"` (`pd1608 激光参数.每段轮廓切换PWM使能 / Enable PWM Per Contour`) is on.
INFERENCE (high): nodes are `(speed %, output %)` pairs — at `x` % of `CutSpeed` the laser runs at `y` % of `CutPower` (or of `CutFreq`); linear (or smoothed) interpolation between nodes; this is the standard "power ramp with speed" used in corners/small features. The vendor's debug dumps in SRC root — `closePwmPosRatios.txt` (46 monotonically increasing ratios 0.0166…0.983), `segments.txt`, `setDataWithoutReFit_segs.txt` ("totalPwmSegments: 1506.62"), `linkFlyLine_pathGlys.txt` — are produced by `CADModule.dll` (the file names are literals at its `.rdata` 0x111xxx) when it plans PWM switching positions along a path; they are development artefacts, not configuration.

CO2 layers use `CutDuty` (`pd127`) instead of `CutPower`/`CutPeakCurrent` and have no pierce or height parameters at all (the CO2 head has no capacitive follower: `AdvFixHeightCutPos` is the fixed Z).

### 3.5 Lead-in / lead-out, start & end segments, cool points, micro joints

* Per-layer **start segment** `UD_Up*` (`pd2730..2735 起刀.启用 / 长度 / 速度 / 启用功率工艺 / 激光功率[占空比] / 激光频率` — "start-work segment": enable, length mm, speed, enable laser control, duty, frequency) and **end segment** `UD_Down*` (`pd2740..2746 收刀.*`, plus a global `pd2746 收刀.气压 / End work Segment.Gas Pressure`). INFERENCE (high): the first `UD_UpLen` mm of each contour are cut at `UD_UpSpeed` with (optionally) their own duty/frequency, likewise the last `UD_DownLen` mm — a ramp-in/ramp-out independent of the lead-line geometry. Slots 1, 2, 9 keep `UD_UpLen=5, UD_UpSpeed=8.333` (= 0.5 m/min).
* **Lead line geometry** is a *graph-level* property stored in the `.chf` (`<GuideCurve Para>`: type, angle, length, radius, flag — §5) and generated from the global `PGraphParam/GRP` defaults: `GuideLineType` (`pd315 引线参数.类型` — `pd300 直线+圆弧 / Line+Arc` etc.), `GuideLineAngle` (`pd316`, 90°), `GuideLineLength` (`pd317`, 8 mm here), `GuideArcRadius` (`pd599 引线参数.半径`, 2 mm), `LeadPosType`/`LeadPosPrecent` (`pd318/319 起点选择模式 / 起点位置`: `pd307 自动选择起点位置(长边优先)`, `pd308 (顶点优先)`, `pd309 统一设置长度位置百分比`, `pd310 不变起点位置`), `IsOnlyForEncolseContour` (`pd320 只对封闭图形有效` closed contours only), `OutsideIsNegativeSide` (`pd322 最外层为阴切` outermost contour is an inside cut), `LoopGapOverCutLength` (`pd323/324 缺口封口.缺口大小 / 过切大小` gap / over-cut length). `LeadLineParam_Enable` in the layer is the per-layer switch that lets the layer override these (INFERENCE medium).
* **Cool points** (`冷却点`, `mf406..408`): `GRP.IsLeadPtCool` (`pd604 冷却点.引入点冷却` cool at lead-in point), `IsPeekPtCool` (`pd605 尖角点冷却` cool at sharp corners), `PeekAngle_deg` (`pd606 最大尖角角度`), `GP.CoolPostionDelay` (`pd596 图形工艺控制.冷却点延时`), gas `MGP.CoolGas`. Cool points are stored per graph (`<coolPos Para>` list of path positions) and executed as: stop, laser off, blow `CoolGas` for `CoolPostionDelay`, continue (INFERENCE high).
* **Micro joints** (`微连`): geometry per graph; global `GRP.AutoMicroLinkType/Num/Step/Dir/MicroLinkLength`, run-time `GP.EnableMicroLinkDecc` + `MicoLinkSlowDownVel` (`pd594/595 启用微连减速模式 / 微连减速速度`), `IsDrillInMicoLink` (`pd595_1 微连处重新穿孔` re-pierce after a micro joint), `MC.EnableAdvMicoLink` + `AdvMicLinkPower_Pre` (`pd595_2/3 启用无痕微连 / 无痕微连功率百分比` "trace-less micro joint": instead of switching the laser off, reduce power to N %).
* **Corner rounding / release angles** (`倒圆角 pd600..602`, `释放角 pd603`, `倒α角 pd607..609`) are geometry post-processing, not run-time.

### 3.6 Sequencing of a contour (INFERENCE, medium — assembled from the attribute semantics, the time-report string `gp2001 切割时间 / 空跳时间 / 穿孔时间 / 系统延时 (%.1f + %.1f) / 总用时` = cut time / idle-move time / pierce time / system delays / total, and the global delays)

1. Rapid move at `UpHeight` to the contour start (or lead-in start). If `FC.EnableLeapFrogUp=1` (`pd103 跟随控制.使用蛙跳式上抬 / Enable Frog Style Up`, `pd1004 调高器.蛙跳模式 / Frog Jump Type` = `pd1002 普通蛙跳 / Normal` or `pd1003 高级蛙跳 / Advanced`, `FCP.FrogJumpMinHeight=10`) the lift and the XY move overlap in a parabola. If `ShortDistNoUp` and the move is shorter than `FC.ShortNoUpMaxLength`, no lift.
2. Gas: open valve for the first stage's gas; wait `GC.DirectGasDelay` (`pd99 首点开气延时 / First Gas On Delay`, 100 ms) on the first contour, `GC.GasDelay` (`pd98 开气延时`, 100 ms) on every later opening, `GC.ChangeGasDelay` (`pd100 换气延时`, 100 ms) whenever the gas type changes; proportional valve needs `MGP.iProportionalOpenSleep` (`A241224_0 杂项.比例阀开启前延时`, 50 ms — VERIFIER: `A241224_0` is defined twice in `lang.txt`; the other definition is `记忆零点 / Memory zero`).
3. Pierce stages per `ManuType` (§3.3), each: follower to `DrillHeight{k}` (drill command / follow), focus to `DrillFocusPos{k}`, gas `DrillGasType{k}` @ `DrillGasPressure{k}`, laser on with `DrillPower/Freq/PeakCurrent{k}` for `DrillDelay{k}` (with optional height and focus ramps), `BeforeLaserOffDelay{k}`, laser off, `AfterLaserOffDelay{k}`, optional bolt burst. Optional smooth pierce / residue-cleaning spiral.
4. Follower to `CutHeight`, focus `CutFocusPos`, gas `CutGasType` @ `CutAirPressure`, laser on with cut settings, dwell `LaserOnDelay`.
5. Cut: `SlowStart` creep, `UD_Up` start segment, lead-in, contour (with speed-dependent power/frequency curves, micro-joint handling, cool points, corner accuracy `MC.CornerAccuracyRate` (`pd94 运动控制.拐角控制精度`, 0.05, tooltip `de6` "range 0.01-1.0"), acceleration `MC.ManuAcc`/`AccTime`, small-circle limit `MP.EnableSmallCircleSpeedLimit`/`SmallCircleSpeedLimitRatio`), lead-out / over-cut, `UD_Down` end segment.
6. `LaserOffBeforeDelay`, laser off, `LaserOffAfterDelay`; close gas unless `NoCloseGasInManu` / `ShortDistGasKeepOn` applies; lift to `UpHeight` (unless short-distance rule) and go to 1.
7. Job end: return per `MC.IsReturnAfterManu` / `ReturnPtTypeAfterManu`, follower to `MP.ZFSafeHeight` / origin (`pd948/949`).

Pre-pierce mode (`PreDrill=1`): all start points of the layer are pierced first (batch size `GP.PreDrillMaxNum`, `pd597 图形工艺控制.预穿孔最大数目 / Pre Drill Max Num` = 5 in `BkManuPara.xml`), without lifting if `PreDrillIsNotUp`, then the contours are cut, re-piercing at each start if `AfterPreDrillMustDrillBeforeCut`.
Film mode (`WithFilm=1`, slot 11): the film-removal layer is run first over `MC.ClearUpFilmNum_Pre` contours per batch (`pd598 批量去膜轮廓数 / number of contours for removal of film`) at its own (higher) cut height, then the real cut layer follows — the vendor sample uses `CutSpeed=100`, `CutPower=100`, `ManuType=1` (fixed height) for that slot.

### 3.7 Z follower / height control parameters that interact with the layer

`ZF` group in `BkManuPara.xml`: `ZFFollowSpeed=100` (`pd567 调高器运行参数.运行速度`), `ZFUpSpeed=100` (`pd657 上抬速度`), `ZFDockHeight=20` (`pd631 停靠高度`), `ZFJogSpeed=10`, `ZFSignalCorrectionPeriod=30`; `MP.ZFSafeHeight=15` (`pd949 调高器.停止时抬起安全高度`), `MP.EnableManuCrashProtect=1` / `ManuCrashProtectUpHeight=35` (`pd1572 碰撞保护`). Layer-level: `CutHeight`, the five `DrillHeight`s, `UpHeight`, `NoFollow`, `ZFVibAbat_Level` (`pd2747 ZF振动抑制.薄板抑制系数(0~90)` thin-plate factor), `ZFVibAbat_Level_Thick` (`pd2749 厚板抑制系数(0~90)`), `ZFVibAbat_Coef` (`pd2748 系数`, 200) and `ZFVibAbatType`. INFERENCE (medium): the abatement parameters are pushed to the height controller (a "ZF" board reached via its own Modbus endpoint in `File/ipAdd.ini`) to soften Z reactions on thick plates.

### 3.8 Marking / engraving vs cutting

There is **no dedicated marking mode** in the fibre layer record. What exists: (a) low `CutPeakCurrent` (slot 9 `3.0 SUS 单3.0 F-1` uses `CutPeakCurrent=15`, `CutHeight=10`, `ManuType=1`, `LaserOnDelay=100` — a fixed-height, low-power pass consistent with surface marking; INFERENCE medium); (b) named UI layers `gp82 标刻图层 / Mark Layer`, `gp301 标刻图层 / Paint Layer`, `gp300 点动图层 / Jog Layer` (used by the drawing UI rather than by the layer table); (c) the vendor spreadsheet sheets "Fiber engraving parameters" / "CO2 engraving parameters" (power 14–43 %, 800 mm/s scan speed) which would be entered as ordinary layer parameters; (d) scan/fly-cut support (`CGlyScan` class, `GRP.scanDirection`, `FiberScanFlyCompensateStr="100#0.35,…"` = speed→PWM lead compensation table, `pwmCompensation.txt` in PKG). Evaporation/"pre-treatment" passes are realised with the film layer (`WithFilm`) and the residue-cleaning spiral.

### 3.9 Global "graph process control" parameters (not in the layer file, but consumed together with it)

From `File/BkManuPara.xml` (with descriptor labels): `GP.IsAutoCheckGraphOrder=1` (`pd593 加工前自动区分内外模 / Auto distinguish inner outer before process`), `GP.EnableMicroLinkDecc=0`, `GP.MicoLinkSlowDownVel=10`, `GP.IsDrillInMicoLink=0`, `GP.CoolPostionDelay=0`, `GP.PreDrillMaxNum=5`; `MC.IsFastMode=1` (`pd75 加工控制.启用快速模式 / Enable Fast Mode`), `MC.XFastMoveSpeed=500`, `XFastMoveAcc=6000`, `ManuAcc=6000`, `AccTime=200`, `EmptyMoveAccTime=125`, `SplineAccuracyRate=0.02`, `CornerAccuracyRate=0.05`, `MC.LaserPointPower=40` (`pd88 激光控制.激光点射功率 / burst power`), `LC.PtLaserFreq=1234`, `LC.PtLaserPeakCurrent=20`, `MC.PtLaserTime_ms=200`, `MC.GasType=1` (manual gas), `GC.DefaultGasPressure=3.45`, `MC.ResumeBackLength=2` (`newLang501 继续回退距离` retract distance on resume), `MC.afterContinueDill=0` (`A250105_0 继续后重新穿孔` re-pierce after resume), `MP.EmptyMoveSpeedFactor=1.1`, `EmptyMoveAccFactor=1.5`, `MP.IsEnablePWMPerContour=1`.

---

## 4. How many layers, and how they are named

EVIDENCE:
* 11 `PLayerParam` + 11 `PCO2LayerParam` slots in both `BkLayerPara.xml` and `1390backup.xml`; the export format always names the single exported slot `…Param11` (§6).
* `MainApp.exe` `.rdata` 0x82ce50–0x82ce8c (UTF-16): `背景图层`, `图层`, `%d`, `BackLayer`, `Layer`, `%d`; lang IDs `gp80 图层1 / Layer 1`, `gp81 背景图层 / Bk Layer`, `gp83 图层 / Layer`, `lp6 去膜图层 / Film Layer`.
* The layer-panel builder at `.text` 0x54e2a0–0x54e77d pushes `gp80` once (0x54e2d6), then runs a loop `cmp dword [ebp-0x168],0xa ; jge` (0x54e448) whose body pushes `gp81` when the index is 0 (0x54e53a–0x54e547) and `gp83` otherwise (0x54e61c).
* `WithFilm="1"` only in slot 11; the film layer has its own validation message `lp19`.
* The layer-selection warning `A250106_0 图层 %s 气体未配置,是否继续？ / Layer %s Undefined [gas not configured], continue?` names layers by string.

INFERENCE (medium-high): slot 1 = index 0 = **背景图层 / BackLayer** — the default layer of every imported contour (all sample `.chf` graphs carry layer 0); slots 2–10 = indices 1–9 = **图层1…图层9 / Layer 1…9** (the ten-iteration loop covers indices 0–9); slot 11 = index 10 = **去膜图层 / Film layer**.

VERIFIER — upgraded to **EVIDENCE (high)**: the layer-name builder at `.text` 0x54eae0 tests a language flag (`cmp [eax+0x3f88],0` → Chinese branch / else English), then `cmp [edx+0x254],0`: if the layer index is 0 it pushes 0x82ce50 `背景图层` (English branch 0x54ec44 pushes 0x82ce6c `BackLayer`), otherwise it formats 0x82ce5c `图层` + 0x82ce64 `%d` with that index (English `Layer` + `%d`). The film layer is named at 0x54d3e6: `cmp [eax+0x220],0xa ; jne … ; push 0x82a704 (lp6 去膜图层)` — i.e. **index 10 is the film layer** and the check is on the layer index, not on `WithFilm`. Also, in the panel loop the number appended after `gp83 图层` is the loop index itself (`lea ecx,[ebp-0x168]` passed to the formatter at 0x54e68c), giving `图层1..图层9` for indices 1..9. Note `SecondBkManuPara.xml` has `SP.m_iEnableLaserType="0"` while `BkManuPara.xml` has `"1"`; the descriptor for that attribute is labelled `gp120 激光器 / Laser` with options `A241024_0/1/2` (fibre / CO2 / blue), so INFERENCE (medium): 0 = fibre, 1 = CO2, and the primary backup was taken while the CO2 source was selected. The CO2 table is a parallel set of 11 slots selected by the active laser type (`SP.m_iEnableLaserType="1"` in `BkManuPara.xml`; `A241024_0 光纤激光器 / Fiber laser`, `A241024_1 CO2激光器 / CO2 laser`, `A241024_2 蓝光激光器 / Blue laser`; `A250421_0 加工任务与当前激光器类型不符 / The processing task does not match the current type of laser`).

Layer assignment commands: `mv34 选中同一图层轮廓 / Select Same Layer Contours`, `mf610/611 选中图层最先/最后 / Selected Layer First/Last` (sort order), `IGP.EnableReadGraphColor=0` (`pd485-1` — DXF colour → layer mapping disabled on import), and the undoable command class `COpGraphLayerCmd` in `CADModule.dll` (§5.2). `pd1610 软件.图层参数校验值 / Soft.Layer Para Verify Value` is a checksum the software keeps for the layer file (INFERENCE medium). VERIFIER (downgraded to **low**): neither `pd1610` nor `pd1609` (`制造参数校验值 / Manu Para Verify Value`) occurs in `MainApp.exe` (UTF-16 or ASCII), and no `*Verify*` attribute exists in `BkManuPara.xml`, `BkHardPara.xml` or `1390backup.xml` — the verify value is a `lang.txt` leftover, not a feature of this build, so the port need not reproduce any checksum.

---

## 5. Layer references inside a job file (`*.chf`)

### 5.1 File format (EVIDENCE: samples + writer disassembly)

`.chf` is a line-oriented ASCII file (CRLF). `File/autosave.chf` (2025, 24 graphs) and `File/Temp/tempGraph.chf` are **version 5**; `Graph/Work*/*.chf` (2020) are **version 4** and contain many empty lines where v5 has none (older string fields written empty — a reader must tolerate both). The writer lives in `Module/CADModule.dll`:

* graph list: function at 0x100a52a0 — writes `<Begin Graphs>` (0x101133f0), the graph count (`[ebx+4]`), then per graph `####graph NO:%d` (0x101133e0), the int at `[graph+0x8]`, then calls the graph's virtual `save` (vtable slot +0x60), and finally `<End Graphs>` (0x101133d0).
* `CGlyContour::save` at 0x1005ab20 (RTTI `.?AVCGlyContour@@`, vtable 0x10111df4).

Decoded layout of one graph block (member offsets of `CGlyContour` in brackets; *value* = what every sample contains):

```
####graph NO:<n>
<int>            [graph+0x08]  graph type code (always 8 in samples → CGlyContour; INFERENCE medium)
<double>         [+0xc8]       tolerance/precision (0.010000 in v5, 0.100000 in v4 files)
<Glyphs>
<double>         [+0x48]       contour length (284.266045 = length of the 24 identical rectangles)
<x,y> <x,y>      [+0x28],[+0x38]  bounding box min / max      (INFERENCE high)
<x,y> <x,y>      [+0x60],[+0x70]  start point / end point     (INFERENCE high)
<int>            number of glyph elements (vector [+0xa8..+0xac], 8-byte entries {ptr,int})
  ####Gly: <i>
  <int>          [elem+4]  (1 in samples)
  <int>          [gly+0x8] glyph type: 2 = line segment (two points follow), 4 = circle/arc (centre + radius follow)
  … glyph data …
<End Glyphs>
<int>            [+0x0c]  ***LAYER INDEX*** (0-based; 0 in every sample)
<int>            [+0x58]  (1 in every sample; INFERENCE low: closed/valid flag)
<Crafts>
<int>            [+0xf0]  -1 in every sample (INFERENCE medium: per-graph process override, -1 = "use layer")
<double>         [+0xf8]  0.0
<PWM Control>
<int>            [+0x104] 1
<int> + pairs    count of vector [+0x108]; then (value[+0x108][i], value[+0x118][i]) doubles
<int> + values   count of vector [+0x12c]; then doubles
<End PWM Control>
<double>         [+0x170] 0.0
<double>         [+0x188] 0.0
<GuideCurve Para>
<int>            [+0x1a8] lead-line type (0)
<double>         [+0x1b0] angle (90.000000)
<double>         [+0x1b8] length (5.000000)
<double>         [+0x1c0] radius (0.0)
<int>            [+0x1c8] (byte) flag (0)
<End GuideCurve Para>
<coolPos Para>
<int> + values   count of vector [+0x14c]; then doubles (path positions of cool points)
<End coolPos Para>
<End Crafts>
```

File header/trailer: `scFlie` / version / `<Begin Graphs>` … `<End Graphs>` / `0` / `0.0` / `0.000000,0.000000` / `0.000000,0.000000` / `eof` (the four trailer fields are written by the caller at 0x100a5330-0x100a534x from its own arguments: an int, a double and two points — INFERENCE low: dock point / origin offsets). `File/ManuContour.dat` (`scFlie`, 24, then 0..23, `eof`) is the cut order of the 24 contours of `autosave.chf`; `File/AutosaveParam1.ini` / `2.ini` are autosave counters; `File/Temp/tempIsBreak.ini` (`scFlie / 1 / 1 / eof`) is the breakpoint marker.

### 5.2 Proof that `[+0x0c]` is the layer index (EVIDENCE)

* `COpGraphLayerCmd` (RTTI type descriptor 0x1012e390, vtable 0x101135b0): its `Execute` (0x100d5060) and `Undo` (0x100d50a0) loop over the selected graphs and call virtual slot **+0x48** of each graph with an int taken from two parallel arrays (`[cmd+0x2c]` new values, `[cmd+0x1c]` old values). VERIFIER: re-derived from the DLL (`.data` VA 0x10128000 = file 0x126400): `.?AVCOpGraphLayerCmd@@` type descriptor 0x1012e390 → complete-object locators 0x10117520/0x10117548 → vtable 0x101135b0 whose slots +0x4/+0x8 are 0x100d5060/0x100d50a0 — confirmed. `.?AVCGlyContour@@` type descriptor is 0x1012c378 → vtable 0x10111df4, slot +0x48 = 0x1005a8e0, slot +0x60 = 0x1005ab20 — confirmed; the writer's string pushes resolve to `<Glyphs>` 0x10111bf0, `####Gly: ` 0x10111be4, `<End Glyphs>` 0x10111bd4, `<Crafts>` 0x10111bc8, `<PWM Control>` 0x10111bb8, `<End PWM Control>` 0x10111ba4, and the int written right after `<End Glyphs>` is `[edi+0xc]` (0x1005ac15) followed by `[edi+0x58]` — confirmed. Sample files: 24/24 graphs in `autosave.chf` and the single graphs in `tempGraph.chf`, `Work1/1.chf`, `Work2/3.chf` all have `0` then `1` after `<End Glyphs>`; the v4 files contain 38 empty lines of 87 (older string fields), as stated.
* `CGlyContour` vtable slot +0x48 = 0x1005a8e0: it forwards the int to every child glyph's slot +0x48, then executes `mov [ebx+0xc], eax` (0x1005a91c), and forwards to the linked `ContourEx` object at `[+0x100]` — i.e. it is `setLayer(int)`.
* The writer emits `[edi+0xc]` right after `<End Glyphs>` (0x1005ac15–0x1005ac1b).

INFERENCE (high): a contour references its layer by the 0-based slot index `0..10` into `PLayerParam1..11` (or `PCO2LayerParam1..11` when the CO2 laser is active). The job file does **not** embed the layer parameters — they come from the machine's layer table at run time. (Exception: the `.enc` job package, §6.3.)

---

## 6. Material library / presets ("工艺库 / Technology")

### 6.1 UI and storage (EVIDENCE)

* Lang strings: `A250418_0 工艺库 / Technology`, `A250418_1 工艺: / Technology:`, `A250418_2 工艺描述: / Technology description:`, `A250418_3 工艺参数有误! / The technology parameters are incorrect!`, `A250418_4 工艺库为空! / The technology [library is] empty!`, `A250419_0 导出到工艺库 / Export technology`, `A250419_1 未找到工艺文件! / The process documents were not found!`, `A250419_2 文件 %s 已存在，是否覆盖? / File %s already exists. Do you want to overwrite it?`, `lp7 保存工艺文件 / Save layer parameters`, `lp8 工艺参数导出成功！ / exported successfully`, `lp9 导入工艺文件 / Import the layer parameters`, `lp10 该操作会覆盖当前系统参数 / Current parameters will be overridden`, `lp11`, `FileFilter_CraftFile 工艺文件 / Craft file`, `hp9 xml文件(*.xml)|*.xml|…`.
* `MainApp.exe` strings: `\NexCut`, `\Technology`, `\Fiber`, `\CO2`, `\Technology\Fiber\`, `\Technology\CO2\`, `\*.xml`, `Failed to create Technology folder.`, `Failed to create Fiber f[older]`, next to the lang IDs above (file 0x3c9168–0x3c9200 and 0x42939c).
* Vendor-delivered library in `PKG/Cutting parameters/`: `1200W/{Carbon steel, Stainless steel, Aluminum alloy, Copper}` (O2 / N2 recipes), `1200W_air/{…}` (compressed-air recipes, "1Mpa"), `CO2/{Acrylic, Plywood, Density board}`; plus `PKG/Cleaning and quenching/{Cleaning 1.0MPA.xml, quenching 0.6 MPA.xml}`. File names encode *material thickness nozzle focus gas*, e.g. `Carbon 10.0mm  4.0D F+12 O2` = carbon steel 10 mm, 4.0 mm double nozzle, focus +12 mm, oxygen; `SS3.0mm 1.5S F-4N2` = stainless 3 mm, 1.5 mm single nozzle, focus −4, nitrogen (the spreadsheet's `单/双` = single/double nozzle).

INFERENCE (high): the library directory is `<base>\NexCut\Technology\Fiber\` or `\CO2\` (base = same data folder as `LayerPara.xml`; VERIFIER: base = `%LOCALAPPDATA%`, see §1.1); "Export technology" writes the current slot as one XML file named `<LayerFileName>.xml`; "Import" reads such a file into a slot and stores the file's base name into `LayerFileName`. VERIFIER (the second half downgraded to **low**): in the vendor files `LayerFileName` frequently does **not** equal the file's base name — e.g. `Carbon 6.0mm  1.5D F+12 O2.xml` carries `LayerFileName="Carbon 5.0mm  1.5D F+13 O2"`, `Aluminum1.0mm 1.5S F-3N2.xml` carries `Aluminum1.0mm 2.0S F+1 N2`, all seven acrylic files carry `亚克力8mm` (acrylic 8 mm) or `""`, `Plywood 3mm air.xml` carries `三合板21mm` (plywood 21 mm), and both cleaning/quenching files carry `不锈钢1.0mm 单喷2.0 F0` (stainless 1.0 mm, single nozzle 2.0, focus 0 — they were derived from the SS 1.0 mm recipe). So `LayerFileName` is a free-text recipe label that survives "save as" under a different file name; the importer evidently does not overwrite it. The `PKG` folder is a copy of that library (the 10 mm carbon file is identical to slot 1 of `BkLayerPara.xml` except for 8 values the operator later re-tuned: `DrillPower0` 75→65, `DrillPeakCurrent0` 75→60, `EnableGradualDrill0` 1→0, `FocusGradualTime1/DrillDelay1/GradualTime1` 6000→5500, `…2` 400→350).

### 6.2 Technology file format (EVIDENCE)

```xml
<ParameterRoot>
<PLayerParam11>
<GP NoManu="0" … LayerFileName="Carbon 10.0mm  4.0D F+12 O2" … FreqAdjustWithSpeed="0"/>
</PLayerParam11>
</ParameterRoot>
```
i.e. exactly one slot, always written as `PLayerParam11` (fibre) or `PCO2LayerParam11` (CO2), same 164 / 21 attributes, same order — regardless of which slot it came from. VERIFIER: fibre files — 164 attributes in exactly the `BkLayerPara.xml` order (checked for all 42); CO2 files — only **20** attributes: `CutFreq` (the newest CO2 attribute, last in the descriptor order) is missing from all 13, i.e. they were exported by an older build. Since the current build ships them as its library, the importer must fill absent attributes with the descriptor default (`5000` Hz) — INFERENCE (high). All 55 files are UTF-8 without BOM, CRLF, no XML declaration, like `BkLayerPara.xml`. Key values of all 55 files:

| file | speed mm/s (m/min) | duty/freq/peak | gas / bar | height | ManuType | pierce0 (h, duty, ms, gradual) | pierce1 | pierce2 |
|---|---|---|---|---|---|---|---|---|
| 1200W/Carbon 1.0mm 1.5S F-1 N2 | 333 (20) | 100/5000/100 | 5 (HiN2) / 0.85 | 0.5 | 0 | 10, 40 %, 1500, on | – | – |
| 1200W/Carbon 2.0mm 1.5S F-3 N2 | 125 (7.5) | 100/5000/100 | 5 / 0.85 | 0.5 | 2 | 5, 100 %, 100, on | – | – |
| 1200W/Carbon 3.0mm 1.2D F+13 O2 | 41.7 (2.5) | 100/5000/100 | 1 (O2) / 0.7 | 1.0 | 2 | 10, 100 %, 400, on | – | – |
| 1200W/Carbon 4.0mm 1.2D F+13 O2 | 33.3 (2.0) | 100/5000/100 | 1 / 0.55 | 1.0 | 2 | 10, 100 %, 400, on | – | – |
| 1200W/Carbon 5.0mm 1.2D F+12 O2 | 33.3 (2.0) | 100/5000/100 | 1 / 0.65 | 0.6 | 2 | 10, 100 %, 400, on | – | – |
| 1200W/Carbon 6.0mm 1.5D F+12 O2 | 22.5 (1.35) | 100/5000/100 | 1 / 0.35 | 0.6 | 2 | 10, 100 %, 400, on | – | – |
| 1200W/Carbon 6.0mm 4.D F+13 O2 | 26.7 (1.6) | 100/5000/100 | 1 / 0.55 | 1.0 | 2 | 10, 100 %, 400, on | – | – |
| 1200W/Carbon 8.0mm 4.0D F+12 O2 | 13.3 (0.8) | 80/2000/80 | 1 / 0.6 | 0.6 | 4 | 6, 65 %, 1500, off | 18, 85 %, 5500, on | 20, 80 %, 350 |
| 1200W/Carbon 10.0mm 4.0D F+12 O2 | 11 (0.66) | 88/2000/88 | 1 / 0.6 | 0.6 | 4 | 6, 75 %, 1500, on | 18, 85 %, 6000, on | 20, 80 %, 400 |
| 1200W/SS1.0mm 1.5S F-3 N2 | 300 (18) | 95/1111/100 | 5 / 0.85 | 0.5 | 0 | 10, 40 %, 1500, on | – | – |
| 1200W/SS2.0mm 1.5S F-3N2 | 217 (13) | 100/5000/100 | 5 / 0.85 | 0.5 | 0 | 10, 40 %, 1500, on | – | – |
| 1200W/SS3.0mm 1.5S F-4N2 | 66.7 (4) | 90/1111/100 | 5 / 0.85 | 0.5 | 2 | 5, 100 %, 100, on | – | – |
| 1200W/SS4.0mm 1.5S F-4N2 | 38.3 (2.3) | 90/1111/100 | 5 / 0.85 | 0.5 | 2 | 5, 100 %, 100, on | – | – |
| 1200W/SS5.0mm 1.5S F-5N2 | 21.7 (1.3) | 86/999/100 | 5 / 0.85 | 1.0 | 2 | 6, 88 %, 888, on | – | – |
| 1200W/Aluminum1.0mm 1.5S F-3N2 | 250 (15) | 100/5000/100 | 5 / 0.85 | 0.5 | 2 | 5, 100 %, 100, on | – | – |
| 1200W/Aluminum2.0mm 1.5S F-4 N2 | 117 (7) | 100/5000/100 | 5 / 0.85 | 0.5 | 2 | 8, 88 %, 100, on | – | – |
| 1200W/Aluminum3.0mm 1.5S F-5 N2 | 25 (1.5) | 100/2000/100 | 5 / 0.6 | 0.5 | 3 | 6, 90 %, 500, off | 15, 85 %, 500, on | – |
| 1200W/Copper 1.0mm 1.5S F-1 N2 | 250 (15) | 100/5000/100 | 5 / 0.85 | 0.5 | 0 | 10, 40 %, 1500, on | – | – |
| 1200W/Copper 2.0mm 1.5S F-3 N2 | 83.3 (5) | 100/2000/100 | 5 / 0.6 | 1.0 | 3 | 6, 90 %, 500, off | 15, 85 %, 500, on | – |
| 1200W/Copper 3.0mm 1.5S F-5 N2 | 20 (1.2) | 100/2000/100 | 5 / 0.6 | 1.0 | 3 | 6, 90 %, 500, off | 15, 85 %, 500, on | – |
| 1200W_air/Carbon 1…6 mm | 283…10 | 80–95 / 188–1000 / 100 | 5 / 0.85 | 0.3–0.6 | 0 (1–2 mm), 2 (3–6 mm) | 10, 70 %, 500, off | – | – |
| 1200W_air/SS 1…5 mm | 300…29 | 90–98 / 999–1111 / 100 | 5 / 0.85 | 0.3–0.8 | 0 (1–2 mm), 2 (3–5 mm) | 10, 40–88 %, 300–1500 | – | – |
| 1200W_air/Aluminum 1…5 mm | 217…10 | 95–100 / 1000 / 100 | 5 / 0.85–1 | 0.3–0.5 | 2 (1–2 mm), 3 (3–4 mm), 4 (5 mm) | 6–10, 70–88 % | 8–10, 77–88 % | 10, 88 % |
| 1200W_air/Copper 1…3 mm | 167…55 | 100/1000/100 | 5 / 0.85 | 0.3–0.5 | 2 | 6–10, 70–100 %, 500 | – | – |
| CO2/Acrylic 3…24 mm air | 50 … 1.67 | `CutDuty` 100, `CutFreq` 5000 | 3 (HiAir) / 5 | (`AdvFixHeightCutPos` 0) | – | – | – | – |
| CO2/Plywood 3/7/21 mm, Density board 3/6/9 mm | 167/58/4.2, 83/33/8.3 | 100 (99) / 5000 | 3 / 5 | – | – | – | – | – |
| Cleaning 1.0MPA (surface cleaning) | 83.3 (5) | 100/5000/100 | 5 / 0.85 | 1.0 | 1 | (unused) | | |
| quenching 0.6 MPA (laser hardening) | 8.33 (0.5) | 100/5000/100 | 5 / 0.85 | 1.0 | 1 | (unused) | | |

The same numbers appear in `Cutting parameters.xlsx` (sheets: `六合一金属切割参数` "six-in-one metal cutting parameters" for 1200 W and 800 W, CO2 cutting table by tube power, welding, hand-held cutting, hand-held cleaning, CO2 engraving, fibre engraving) with columns 材料/厚度/速度 m/min/频率/焦点/喷嘴/高度/气体/压力 bar/占空比/功率 — i.e. material, thickness, speed, frequency, focus, nozzle, height, gas, pressure, duty, power. The machine is a "six-in-one" combination (cut, weld, clean, hand-held cut/clean, engrave) with a 1200 W fibre source and a CO2 tube; only the cutting recipes map onto the layer model.

### 6.3 Job packages carrying layer parameters (EVIDENCE + INFERENCE)

`MainApp.exe` contains, next to the file dialogs (`mf149 支持的加工文件(*.dxf;*.chf;*.nc;*.txt;*.cnc;*.g;*plt)`, `enc文件(*.enc)|*.enc`, `aut file(*.aut)`), the temp names `\_tempSource.chf`, `\_tempLayer.xml`, `\_tempthumbnail.jpg`, `\_tempManuItem.olpf` and the ASCII markers `NEXCUT_CHF_END`, `NEXCUT_LAYERXML_END`, `NEXCUT_JPG_END` (file 0x3da620). VERIFIER (corrected): the exact ASCII markers are `NEXCUT_CHF_END` (0x3da610, paired with `\_tempSource.chf` 0x3da620), **`NEXCUT_LAYER_XML_END`** (0x3da5d8, with `\_tempLayer.xml` 0x3da5f0), `NEXCUT_JPG_END` (0x3da5a0, with `\_tempthumbnail.jpg` 0x3da5b0), plus two the analyst missed: `NEXCUT_OLPF_END` (0x3da568, with `\_tempManuItem.olpf` 0x3da576) and `NEXCUT_OLPI_END` (0x3da528, with `\_tempProcessInfo.olpi` 0x3da536) — so an `.enc` package has up to five sections (graph, layer XML, thumbnail, manufacturing-item list, process info). A **second container** lives in the same code region: ASCII markers `TASK_GRAPH` (0x3da6ac), `TASK_LAYER` (0x3da6d4), `TASK_MANU` (0x3da700), `TASK_PARAM_ONE` (0x3da734), `TASK_PARAM_TWO` (0x3da76c), `TASK_IS_BREAK` (0x3da79c) interleaved with the UTF-16 names `\File\Temp\`, `tempGraph.chf`, `tempLayer.xml`, `ManuContour.dat`, `AutosaveParam1.ini`, `AutosaveParam2.ini`, `tempIsBreak.ini`, `tempManu.ini`, the file filter `aut file(*.aut)|*.aut|…` (0x3da2f0) and the messages `Graph Index file was error!` / `Broken point file was error!` (0x3da224/0x3da25c). INFERENCE (medium): the `.aut` file bundles the whole `File\Temp` autosave/break-point set — including a `tempLayer.xml` — so it is a second vehicle by which layer parameters travel with a job (break-point resume / "offline" upload). INFERENCE (high): the `.enc` "processing task" package (`newLang41 导出加工任务 / Export processing tasks`, `newLang42 载入加工任务 / Loading processing tasks`, `A241115_1 离线加工 / Offline Process`, `A241220_1 离线文件生成完成，上传加工? / Offline file generation completed, upload processing?`) concatenates the `.chf`, a layer-parameter XML (same schema as `BkLayerPara.xml`, presumably all 11 slots) and a JPEG thumbnail, separated by those markers. That is the only place where layer parameters travel with a job; `A250107_0 同时恢复图层参数? / Also restore the layer parameters?` is asked on system-recovery. The Linux port should support reading such packages if it must accept vendor-exported tasks.

---

## 7. Open questions

1. **Exact `ManuType` codes above 4.** Codes 0–4 are well supported by the data; the codes for `高级定高 / Adv Fix Height` (`pd811-1`), and for 4-/5-stage pierce (`newLang54/55`) are unobserved. VERIFIER: `newLang54/55` and `pd814` are not present in `MainApp.exe`, so 4-/5-stage pierce is not selectable in this build; only the `高级定高` code (and how the two property-grid items are folded into `ManuType`) remains open. Whether the new two-combo dialog (`标准切割/定高/高级定高` × `不启用/一级…五级`) still serialises into the single `ManuType` int, or whether the "process type" combo drives `NoFollow`/`AdvFixHeightCutPos`, needs a disassembly of the dialog's save handler (around MainApp file 0x42794c references) or a live test with the Windows software.
2. **Physical order and timing of pierce stages** (0→1→2 assumed), and whether `GradualTime` ramps the height to the *next stage height* or to `CutHeight`. The firmware (`Update/MCC100_V201.52.mcf`) or the Modbus command stream (analysed in the motion-controller report) is the authority.
3. **Gas pressure semantics for on/off channels.** For the O2 proportional valve the value is bar via the `Gas Pressure Map`; for High-N2/High-Air (plain DOs on this machine) the stored value may be ignored or used only for the gas-pressure warning (`MP.EnableGasPWarning`, `af30`).
4. **`PreLaserOnFactor` (unit `mm/s`)** — likely "laser-on lead = factor × speed" or a lead time; unverified.
5. **`[+0x58]` and `[+0xf0]` in the `.chf` graph block** (always `1` and `-1`): candidates are "closed contour" and "per-contour process override index"; `[+0x104]=1` in `<PWM Control>` is probably "use layer PWM curve".
6. **Which layer the CO2 table maps to** when `SP.m_iEnableLaserType` switches; and whether layer index 10 (film) is selectable for CO2.
7. **Whether the technology-export file's fixed `…Param11` element name matters on import** (probably the importer only looks for the first `GP` child).
8. **`pd1610 Soft.Layer Para Verify Value`** — a checksum/verification of `LayerPara.xml`; algorithm unknown (search `SP`/`SOP` handling in `PSoftParam` if the port must produce files the Windows tool accepts). VERIFIER: resolved — the ID is not referenced by this build and no such attribute exists in any parameter file; no checksum is needed.
10. VERIFIER: **How the property-grid "process type" + "pierce" pair is folded into `ManuType`** (dialog code 0x53f300–0x540400) and what code the `高级定高` choice produces.
11. VERIFIER: **Exact layout of the `.aut` (`TASK_*`) bundle and of the `.olpf`/`.olpi` sections of `.enc`**; whether the layer XML inside them carries all 22 slots or only the referenced ones.
9. **How the `DrillPeakCurrent`/`CutPeakCurrent` percentage is scaled** to the DA (`LGP.LaserDAType`, `DA1OutputAdjustVal=3648`) and how `LaserMaxPower=0` (unset) affects it — belongs to the laser-control report.

## 8. Implications for the Linux port

**Must be replicated (data model)**
* A layer registry with 11 fibre slots × 164 attributes and 11 CO2 slots × 21 attributes, using exactly the attribute names, order, defaults, units and enum codes listed in §2.3–2.5, so that `BkLayerPara.xml`, `1390backup.xml` and every vendor technology file load unchanged. Serialising doubles with `%.17g`, ints without decimals, and keeping the `P<Group><n>/GP` element convention keeps files interchangeable with the Windows tool.
* Layer index semantics: 0 = background/default, 1–9 = user layers, 10 = film layer; `.chf` stores the 0-based index after `<End Glyphs>`.
* Validation rules `lp16..lp19` and the `A250106_0` "gas not configured" check (a layer whose gas has no DO/valve assigned in the hardware config must warn). VERIFIER: only `lp19` (film-layer cut height) is actually enforced by this build; `lp16..lp18` may be implemented as improvements but are not required for compatibility.
* Technology library: import/export of single-slot XML (`…Param11` wrapper), a folder per laser type, and preset naming via `LayerFileName`. The 55 vendor files can be shipped as the initial library. VERIFIER: the importer must (a) default any attribute missing from a file (the 13 CO2 presets lack `CutFreq`) and (b) treat `LayerFileName` as a free label, not as the file name. On Windows the library and the live parameter files live under `%LOCALAPPDATA%\NexCut\` (§1.1); the Linux equivalent is `$XDG_DATA_HOME/nexcut/` or similar.
* Unit conversions in the UI (mm/s ↔ m/min, bar ↔ DA volts via the calibration map) — the files stay in mm/s and bar.
* The process sequence of §3.6 (staged pierce with per-stage gas/laser/height/focus, gradual pierce, dwell/blow-off, bolt burst, smooth pierce, residue spiral, slow start, start/end segments, power & frequency curves vs speed, cool points, micro-joint deceleration/re-pierce, short-distance no-lift with gas hold, frog-jump lift, pre-pierce batching, film pass, contour shift). Which of these are executed by the PC and which by the MCC100 firmware is decided by the motion-controller protocol report; the layer model itself is purely PC-side data.

**Can be replaced by existing Linux/open-source components**
* XML I/O: any DOM library (pugixml, libxml2, Qt XML, Python `xml.etree`) — the format is trivial (attributes only). ParaModule's home-grown parser (with its `encoding`/`charset` heuristics) need not be reproduced.
* Property-grid style editor for the 164 attributes: Qt `QtPropertyBrowser`/model-view or GTK equivalents; the descriptor table (name, unit, default, lang ID, enum options) can be exported once from §2.3 into a JSON schema that drives the editor and validation.
* Localisation: the `ID#中文#English` tables (`Lang/*.txt`) convert directly to gettext/Qt `.ts`; the parameter labels are just the lang IDs in §2.3.
* Speed-dependent power/frequency curves and corner/velocity planning: the PC-side look-ahead in `MotionCtrl.dll` (`newVelocityPlanning`, `newContourSmooth`) and CADModule's PWM segment planner correspond to what LinuxCNC's trajectory planner or the `libplanner`/`grbl`-style S-curve planners do; if the MCC100 executes pre-planned segments, the port must produce equivalent PWM-switch positions (the `closePwmPosRatios.txt`-style output) — a reimplementation, not an off-the-shelf library.
* Geometry helpers (lead-in/out, over-cut, micro joints, cool points, rounding) are standard 2-D path operations — Clipper2/CGAL/Shapely cover offsetting and intersection; the specific parameters in `PGraphParam` (§3.5) define the vendor behaviour to reproduce.
* Material spreadsheet: keep as documentation; the machine-readable form is the XML library.

---

## Verification notes

Adversarial re-check of this report against the primary files (verifier pass, 2026-09-12). Everything below was re-run from scratch: `xml.etree` parses of `File/BkLayerPara.xml`, `PKG/1390backup.xml`, `File/BkHardPara.xml`, `File/BkManuPara.xml`, `File/SecondBkManuPara.xml` and all 55 vendor XML files; a UTF-16 string walk of `MainApp.exe` file 0x472400–0x477000 rebuilt into 185 descriptor records; `strings -a -e l MainApp.exe` (whole-string matching); `objdump -d -M intel` of `MainApp.exe` and `Module/CADModule.dll` at every address cited; `Lang/lang.txt` decoded as UTF-16LE; the `.xlsx` unzipped and its `sharedStrings.xml`/`sheet1.xml` read with the stdlib.

### Checked and confirmed (unchanged)
1. XML tree: `ParameterRoot` > `PLayerParam1..11/GP` (164 attrs) + `PCO2LayerParam1..11/GP` (21 attrs); UTF-8 no BOM, CRLF, no declaration; identical attribute order in all slots; the 17-significant-digit doubles (`0.59999999999999998`, `1.3999999999999999`, `83.333333333333343`) are consistent with `%.17g`. Same 22 elements in `1390backup.xml` (values differ — added).
2. Descriptor table: 164 `LayerParam` + 21 `CO2LayerParam` records; reversed record order equals the XML attribute order for both groups; zero name mismatches; the cited example records resolve to the stated unit/default/lang ID (the analyst's offsets point at the trailing group marker, noted). The seven non-layer `GP.*` names are exactly the extra ones (172 unique `GP.*` strings = 164 + `CutDuty` + 7).
3. Speed unit: descriptor `mm/s`; `UN.SpeedUnit=1`; spreadsheet `碳钢 10` row = 0.66 m/min / 0.6 bar / duty 88 — matches `CutSpeed=11.000000000000002`, `CutAirPressure=0.6`, `CutPower=88`.
4. Gas-type enum: `mp43..48` and `pd7..12` order re-read; `MGP` values `LowO2=7 HighAir=3 HighN2=2 RatioO2=2` confirmed; sample values 1/5/3/0 confirmed across the 55 files. Still an inference (no code decodes the enum); confidence unchanged (likely).
7. `.chf` layer index: `CGlyContour::save` 0x1005ab20 writes `[edi+0xc]` at 0x1005ac15 right after pushing `<End Glyphs>` (0x10111bd4); slot +0x48 = 0x1005a8e0 does `mov [ebx+0xc],eax`; `COpGraphLayerCmd` vtable 0x101135b0 / typedesc 0x1012e390 re-derived; all sample graphs carry `0`.
9. Technology library: strings `\NexCut`, `\Technology\Fiber`, `\Technology\CO2`, `\*.xml`, `Failed to create Technology folder.` confirmed; all fibre files are `PLayerParam11`, all CO2 files `PCO2LayerParam11`.
10. Five pierce stages / bolt / smooth / residue attribute blocks: names, lang IDs and defaults confirmed from the descriptor walk.
11. PWM/frequency curves: every non-empty value in all 55 files + backups is an even-length list starting with `0,…` and ending `100,100`, with both coordinates monotonic when read as pairs — supports the (speed %, output %) reading; still inference.
12. Init sequence (`--- Init System param ---`, `\LayerPara.xml`, `\BkLayerPara.xml`, `Read Backup`, …) confirmed; upgraded (see below).
13. `.enc` temp names confirmed; marker names corrected (see below).

### Changed
* **Refuted / corrected**: preset count is 53 + 2 = 55, not 54 + 2 = 56 (§0, §1, §6). Marker is `NEXCUT_LAYER_XML_END`, not `NEXCUT_LAYERXML_END`; `NEXCUT_OLPF_END`/`NEXCUT_OLPI_END` and the `TASK_*` (`.aut`) container were missing (§6.3). The `pd810..pd813` option list is not adjacent to the `GP.ManuType` record and does not contain `pd814` (§2.2). `DrillGasType1` has no unit string (§2.3). CO2 preset files have 20 attributes, not 21 (§6.2).
* **Downgraded**: gas-pressure "mapped through the gas pressure map" — the three map strings are empty; what holds is bar/MPa per `UN.GasPressureUnit` (`pd900 bar`, `pd901 MPa`) scaled by `DAMaxPressure` (§2.4). `ManuType` codes ≥ 5 — `newLang54/55` and `pd814` are absent from the executable, so 4-/5-stage pierce is not selectable; "probably 5..7" withdrawn (§2.5, §7). Validation rules `lp16..lp18` — not referenced by the binary; only `lp19` is enforced (§3.2, §8). `pd1610` verify value — not referenced by the binary, no such attribute anywhere; low (§4, §7). `LayerFileName` = file base name — contradicted by 10+ vendor files; low (§6.1).
* **Upgraded**: base folder is `%LOCALAPPDATA%\NexCut` — `SHGetSpecialFolderPathW(…, 0x1c, TRUE)` at 0x43291f/0x43292a via helper 0x432900, `\NexCut` appended at 0x4aff12, `PathFileExistsW` at 0x4aff45 (§1.1). Layer naming: index 0 → `背景图层/BackLayer`, index n → `图层%d/Layer %d` at 0x54eae0–0x54ec66; index 10 → `lp6 去膜图层` at 0x54d3e6 — now EVIDENCE (§4).
* **Added**: `1390backup.xml` value differences; `SecondBkManuPara.xml` `m_iEnableLaserType=0` vs `1` and the `gp120` / `A241024_*` laser-type descriptor; in-memory layer table stride 0x480 bytes at +0x4e08 (from the `lp19` check); `lang.txt` duplicate IDs (`gp100`, `pd1001`, `A241224_0`, `A250616_0`) and the `FocusGradualTime2`→`pd934` caption bug; speed-unit suffix strings giving the `UN.SpeedUnit` enum; importer must default missing attributes; `JumpAddTime.txt` / `Axis4Freq` read during init.

### Not re-verified (taken from the analyst)
* The graph-list writer at 0x100a52a0 and the trailer fields; the per-glyph type codes (2 = line, 4 = arc); the `PWM Control` vector semantics — only the strings and the post-`<End Glyphs>` ints were re-checked.
* The layer-panel builder 0x54e2a0–0x54e77d: loop bound `0xa` at 0x54e448, `gp81` at 0x54e547, `gp83` at 0x54e61c confirmed; what the pre-loop `gp80` push at 0x54e2d6 is for was not determined.
* All global-parameter values quoted in §3.6–§3.9 were spot-checked (`GC.*Delay`, `FC.ShortNoUpMaxLength`, `FC.EnableLeapFrogUp`, `MP.IsEnablePWMPerContour`, `MP.DirectDrillMaxHeight`, `GP.PreDrillMaxNum`, `MC.GasType`, `GC.DefaultGasPressure`, `FiberScanFlyCompensateStr`) and match; the remaining ones were not individually re-read.

### Still uncertain
* The arithmetic that folds the dialog's "process type" and "pierce level" items into the single `ManuType` int, and the code for `高级定高`.
* Whether the runtime honours `DrillGasType{k}` per stage and how it treats gas-pressure values on the on/off (non-proportional) channels.
* The physical stage order and what `GradualTime` ramps towards — needs the controller-protocol/motion analysis, as the analyst said.
* Exact binary layout of the `.enc` sections and of the `.aut` bundle (only the marker strings and file names are known).
