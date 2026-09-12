# 06 – UI feature inventory from the language resources (SC2000 / Mlaser v0.0.0.52)

Analyst note: everything below is derived from the language-resource text files of the package, cross-checked
against string/RTTI/resource data pulled from `MainApp.exe` with `strings`, a minimal PE resource walker and
`objdump`. Nothing under `SRC` was modified.

* `SRC` = `/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52`
* Primary sources: `SRC/Lang/lang.txt`, `SRC/Lang/lang.txt.orig`, `SRC/Lang/语言修改记录.txt`,
  `SRC/Lang/Readme.txt`, `SRC/Lang/lang.ini`, `SRC/LanFormatEStr.txt`, other `SRC/Lang/*.txt`
* Secondary (for cross-checks only): `SRC/MainApp.exe`, `SRC/Module/*.dll`, `SRC/File/ipAdd.ini`,
  `SRC/排样内核错误代码.txt`, `SRC/Report/*`

Conventions: **EVIDENCE** = directly observed in a file; **INFERENCE** = my interpretation, always with a
confidence tag (high / medium / low). Chinese strings are quoted verbatim with an English gloss; where the
package's own English column is misleading I say so.

---

## 1. Files, encodings and format

### 1.1 Encoding survey (EVIDENCE, `file` + `xxd`)

| File | Size | Encoding | Notes |
|---|---|---|---|
| `Lang/lang.txt` | 289 746 B, 3 485 lines | UTF-16LE with BOM `FF FE`, CRLF | master file, modified 2025-06-24 |
| `Lang/lang.txt.orig` | 227 996 B, 2 814 lines | UTF-16LE BOM, CRLF | previous master, dated 2024-01-24 |
| `Lang/French.txt` … `Vietnamese_LE.txt` (10 files) | 249–296 KB | UTF-16LE BOM, CRLF | all dated 2024-09-26 |
| `Lang/Readme.txt` | 1 190 B | UTF-8, CRLF | format description |
| `Lang/lang.ini` | 303 B | UTF-8, CRLF | `[Global] hintStr=… Lang=English` |
| `Lang/语言修改记录.txt` ("language change record") | 4 140 B | UTF-8, CRLF | change log 2022-04 → 2024-03 |
| `LanFormatEStr.txt` (root) | 258 B | ASCII, CRLF | `[SC] Var1..Var15` list of IDs |
| `排样内核错误代码.txt` (root, "nesting kernel error codes") | 398 B | UTF-8 (not GBK – GBK decode fails at byte 11) | see §7.9 |
| `Report/report.txt`, `Report/LogReport.txt`, `Report/TotalReport.txt` | – | GBK (Chinese `分`/`秒` decode correctly with `iconv -f GBK`) | CSV-like report rows |

Nothing in `Lang/` is GBK; the whole UI string set is Unicode. The task brief's expectation of GBK applies only to
some root-level text files and the report CSVs.

### 1.2 Record format (EVIDENCE)

`Lang/Readme.txt` (translated):

> SC2000 language notes. Principles: (1) every text entry has a unique Id, defined by the software designer;
> (2) when an Id cannot be found in the document, the software substitutes the Id itself for the target text.
> File "lang.txt": contains both [简体中文] (Simplified Chinese) and [英文] (English), maintained by the developers.
> Other language files follow: (1) a specific file name; (2) content format `ID#content`, e.g. `af16#Detener`;
> (3) the Ids must match those of lang.txt (identifier and count must agree, otherwise text is not displayed).
> Files: Russian.txt, German.txt, Spanish.txt. Adding a language: create a new file, add text for every Id,
> report the file name and language identifier to the developers.
> Note: parameter-class text has the format `MainItem.SubItem`; if the format is wrong the software shows
> `Lang Format Error -- [illegal content in language file]` when loading parameters.

Actual `lang.txt` layout (EVIDENCE): there are **no** `[简体中文]` / `[English]` section headers (`grep '^\['`
returns nothing). Every line is a three-field record `ID#Chinese#English` (first record after BOM:
`0_SongFont#宋体#Arial`). Other language files are two-field `ID#text`. `#` is the separator and is never escaped;
one malformed line exists: `mv24, ,##` (ID contains a comma and space, both texts empty).

Runtime behaviour implied by Readme rule (2) is confirmed by the binary: 2 564 of the 3 427 unique IDs occur
literally as UTF-16 strings in `MainApp.exe`; the remaining 863 are either numbered families that the code builds
with `sprintf` (`RegName%d`, `AllAxisInfo_%d`, `RTC_RO_%02d` …) or dated `A2504xx_n` IDs that live in the module
DLLs (`Module/LangModule.dll` = 69 120 B is the loader; INFERENCE, high).

`Lang/lang.ini` `[Global] hintStr` enumerates the language index → name table used by parameter `pd378`
"软件.语言(Language)": `0 简体中文, 1 English, 2 русский, 3 Deutsch, 4 Español, 5 Portuguese, 6 French,
7 Italian, 8 Polish, 9 Vietnamese, 10 Turkdili`; the active value is `Lang=English`. (`File/ipAdd.ini [Soft] Lang=0`
also stores a language index.)

### 1.3 `LanFormatEStr.txt` (EVIDENCE + INFERENCE)

```
[SC]
Var1=pd719_4  Var2=pd719_4  Var3=pd719_3  Var4=pd719_4
Var5=pd113-1  Var6=pd117    Var7=pd113-1  Var8=pd117   Var9=pd113-1  Var10=pd117
Var11=EtherAxisInfos_23  Var12..Var15=AFEtherCATIndex
```

All listed IDs are parameter labels ("参数.扫描方向", "短距离不关气", "加工中不关气", "抱闸.输出端口",
"EtherCAT.轴序号"). INFERENCE (medium): this is the exception list for the `MainItem.SubItem` format check
described in the Readme ("Lan Format E[xception] Str") – IDs whose text is allowed to violate the dotted format
(pd113-1 and pd117 have no dot; pd719_x are reused inside a different group). A Linux port should treat the
dotted convention as a *display* convention (group header / row label in a property grid), not as data.

### 1.4 Integrity of the master file (EVIDENCE)

* 3 431 records, 3 427 unique IDs, 0 records with fewer than 3 fields.
* 4 duplicated IDs (first occurrence wins if the loader uses a map; unknown which wins in practice):
  * `gp100` = "电动调焦硬正限位告警 / Auto focus hardware positive limit alarm" (line 222) and "工艺 / Craft" (line 380)
  * `pd1001` = "停止交换 / Stop Exchange" (1181) and "一级穿孔基础.穿孔高度 / Third Drill Basic.Third Drill Height" (2455; the English is a copy-paste error)
  * `A241224_0` = "杂项.比例阀开启前延时 / Misc.Proportional valve opening delay" and "记忆零点 / Memory zero"
  * `A250616_0` = "反向间隙补偿 / Reverse clearance compensation" twice (second with "(mm)")
* Known mistranslations/placeholders in the English column (keep Chinese as ground truth): `ap7` "右 → OK" (should be
  Right), `hp15` "硬件参数导入成功！→ Arial", `mf439`/`mf624` English column contains Chinese "请重新启动软件",
  `ls12` "格式错误#请输入8位数字！" (both fields Chinese), `pd168/pd169` swapped (负向→Positive, 正向→Negative),
  `pd470` "Y轴反向间隙 → Compensate Type", `pd793` duplicate "Disconnected Light Color", `sp6-8` "焦点/气压/温度 → PWM
  Power/PWM Freq", `pd282` "报警铃声 → Dedust", `pd988` "B台到位 → A Platform In Place", `RegName26`
  "Y2轴 → Z-axis", `AxisRWRegName_7`/`PulseAxisRWRegName_7` "精回原速度 → Sperm recovery speed / semen return"
  (machine translation of 精 "fine"), `A250212_10` "扎头风险 → risk of tying" (means head-crash risk),
  `newLang1` "烤机测试 → Roasting machine test" (= burn-in test).

### 1.5 Other language files vs master (EVIDENCE)

| File | Entries | IDs missing vs lang.txt | Obsolete IDs not in lang.txt |
|---|---|---|---|
| Spanish.txt | 3 266 | 169 | 8 (`mf410`, `mp127`, `pd231`, `pd232`, `pd80-1`…) |
| French.txt | 3 284 | 154 | 11 (`hp101-103`, `mf159`, `mp127`…) |
| German.txt | 3 275 | 165 | 13 |
| Russian.txt | 3 283 | 154 | 10 |

All ten secondary files are dated 2024-09-26 and lack every ID added after that date (the `A2410xx…A2506xx`,
`newLang*`, EtherCAT register families). Per Readme rule (2) those UIs display raw IDs for the new features.
None of the secondary files contains an entry that helps fill a gap in `lang.txt`, so they were not needed.

---

## 2. Recent changes: `lang.txt` vs `lang.txt.orig` and the change log

### 2.1 Statistics (EVIDENCE, diff by ID)

* Added: **630** IDs. Removed: **2** (`ab0#确定#OK`, `mp40#返回#Return`). Text changed: **45**.
* The additions are exactly the new-generation feature set (see §5): EtherCAT bus axes (`EtherAxisInfos_*`,
  `AllAxisInfo_*`, `AxisTypeName_*`, `AxisInputStr_*`, `EtherCAT*`, `GoHomeAdv_*`, `SystemRWRegName_*`,
  `RORegName_*`, `RWRegName_*`, `AxisR[OW]RegName_*`, `PulseAxis*`), smart cutting-head telemetry (`RTC_RO_*`,
  `RTC_RW_*`), height-controller register view (`ZFReadOnly*`, `ZFReadWrite*`), touch-panel buttons/LEDs
  (`SBT01-42`, `SLED01-11`), themes (`Theme01/02`), burn-in test, laser test, single-axis test, disc centre finding,
  4th/5th-level piercing (`newLang*`), and 166 dated `Ayymmdd_n` IDs (2024-08-27 → 2025-06-20).

### 2.2 Notable text changes (EVIDENCE, old → new)

| ID | Old | New | Meaning |
|---|---|---|---|
| `ab6` | 版权所有 2014起 奥森迪科 / Copyright 2014-2099 AU3TECH | (blank) | **Vendor copyright removed** – original developer is 奥森迪科 "AU3TECH" (Aosendike). White-labelled build. |
| `mf173` | scb文件(*.scb) | mcf文件(*.mcf) | Controller firmware file extension changed; matches `Update/MCC100_V201.52.mcf`. |
| `pd171` | 网口(FTC) | FTC10 网口 (Net FTC10) | Height-controller model naming. |
| `pd1732/1733` | MCC集成(3721H) / MCC集成_Ex(3721HA) | MCC3721H / MCC3721NA | Controller model naming. |
| `pd640_4th` | 第四轴控制 (4th Axis Control) | 脉冲轴控制 (Pulse axis control) | 4th axis re-labelled as generic pulse axis (bus generation). |
| `pd127/132/663/664/821/834/937/96` | 切割功率(占空比)/峰值电流 | 占空比/峰值功率 | "peak current" renamed "peak power" everywhere (English column still says Peak Current). |
| `pd126/827/840/943` | 开光延时 / 穿孔关光前延时 | 停留时间 (dwell time) | Delay semantics re-labelled. |
| `pd828/841/944` | 穿孔关光后延时 | 停光吹气 (gas blow after beam off) | |
| `pd23/27/284/487-489` | 水冷报警 / 激光器报警 | 光纤激光器… / 输入端口_光纤激光器… | DI alarms split into Fiber-laser vs CO2-laser sets (`A250429_0-5`). |
| `gp40/gp43` | 信号线异常 / 电容信号异常变大，请重新标定 | 信号异常变小告警 / 信号异常变大告警 | FTC signal alarms renamed "abnormally small / abnormally large". |
| `hp32/hp32-1` | 辅助气体（建议气阀开关配置为DO9或DO10 / 从DO13到DO16） | 辅助气体 | Hint about recommended gas-valve DO ports removed (old text reveals DO9/DO10 and DO13-16 were the recommended valve outputs). |
| `pd2114/2115` | X/Y边缘交点补偿距离 | X/Y留边距离 (edge margin) | Edge-seek terminology. |
| `pd249/250` | 电脑串口.端口号/波特率 | 总体.端口号/波特率 | Moved group. |
| `pd124` | 根据速度实时调节功率频率 | 根据速度实时调节功率 (freq split out to `A241012_5`) | |
| `mp80/mf146` | 当前速度(mm/s): %.1f | 当前速度%s: %.1f / 9999.000 | Unit is now a runtime string (inch support `pd53-1/2`, `pd55-1`). |

### 2.3 `语言修改记录.txt` (change log) – translation (EVIDENCE)

* **2022-04-17, version 1.0.392.4** – Added: `MsgBox_CutOffSheet` "Cut-off requires the laser head to be above the
  sheet. Execute cut-off?"; `mf13_5` 切断参数 "Cut-off parameters"; `mf459` 系统 "SC System"; `mf520-524` nesting
  errors (sheet too large, part violates inner/outer topology, parameter error while adding part, more than 50 part
  kinds, nest-DLL kernel error); `pd2408_1` unlimited-roll cut-off X right coordinate; `pd598` batch film-removal
  contour count. Modified: `mf13-1..4`, `mf17-1` renamed to underscore form (rotate platform, exchange platform,
  roll-sheet control, start cut-off, gantry-correction homing); `mp134` "System will clean the nozzle, continue?";
  `pd2408` cut-off X left coordinate. Deleted: `mf410` 排样 "Nest".
* **2022-12-06** – Added: `mf252/253` Work Report [New]/[All]; `mp303` "Empty data"; `pd2421-2430` 4th-axis homing /
  speed parameters; `pd640_EC` "Extend Axis Control", `pd640_4th` "4th Axis Control"; `hp100-103` "The 4th axis has
  already been configured as auto-focus / exchange platform / auto roll / rotate platform, please reconfigure".
  Deleted: `pd231/232` 4th-axis max speed/acc, `pd980` 4th axis, old `hp100` "was repeatedly configured".
* **2024-03-02** – Added `SBT01-42` (touch-panel buttons: Precise beam, Aiming (red light), Shutter, Burst, Simulate,
  Dry run, Frame, Preview, Focus+, Blow, Air, O2, N2, Back, Forward, Return 0, Low, High, Step, Follow, Calibrate,
  Origin, Focus origin, Breakpoint locate, Z home, Pallet home, More (run params), Focus-, Loop, Auto feeding,
  Pallet mode, Pallet changer, Go mark, Mark, Mark1-6, Continuity) and `SLED01-11` (status LEDs: process speed, X/Y/Z
  coordinate, laser power, focus, gas pressure, temperature, total length, process time, perforation count).

The log stops at 2024-03; the 630 later additions are undocumented except by their dated IDs.

---

## 3. ID-prefix → module map

Counts are unique IDs in `lang.txt`. The module attribution comes from (a) the text content, (b) MFC runtime-class
names found as RTTI strings in `MainApp.exe` (`.?AVC…@@`, 171 classes, 69 application-specific windows – list in
§9), and (c) which IDs appear literally in the exe. Confidence is given per row.

| Prefix | Count | Module / window | Evidence & confidence |
|---|---|---|---|
| `pd` (+`pd_Nest_`, `pdAFDA_`) | 1 291 (+17+16) | **Parameter definitions**: every hardware, layer/craft, software, auxiliary-device parameter label and enum value; rendered in BCG property grids (`CPropPanel`, `CHardwarePropView`, `CLayerPropPanel`, `CGraphPropView`) | `Group.Item` format (Readme); `Module/ParaModule.dll`; high |
| `mf` (+`mf_S_File_*`) | 409 (+4) | **Main frame** ribbon menus, tabs, buttons, file dialogs, frame-level message boxes (`CMainFrame`, ribbon) | mf6..mf1050 are menu labels; high |
| `mp` | 200 | **Manufacture/control panel** (`CManuPanel`): start/pause/stop, jog, gas, laser, marks, status strings, dongle messages | high |
| `A<yymmdd>_<n>` | 166 | Dated additions 2024-08-27 → 2025-06-20 across all modules (see §5) | high |
| `gp` | 162 | **Global prompts**: alarm texts gp0-59/92-122/141/200-220, graph property labels gp61-85, alarm-log column headers gp84-94, work-report fields gp130-146, gp300-301 layer names, gp1000/2000/2001 process stats (`CErrorPanel`, `CErrorReportCtrl`) | high |
| `RegName<n>` | 144 | **Controller register monitor** for the MCC3721 generation (read/write register names incl. wireless-handle address, flycut FIFO, DI filter times) | contains "MCC3721硬件版本"; medium-high |
| `zf` | 92 | **Z follower / height controller (FTC) status view** (`CZFStatusView`): calibration, restart, reconnect, params | high |
| `newLang<n>` / `eNewLang<n>` | 80 / 10 | Burn-in test (`StressTestView`), disc centring, 4th/5th-level pierce, laser test (`LaserTestView`), single-axis test, EtherCAT node names | high |
| `hp` | 74 | **Hardware property view** (`CHardwarePropView`): tab names, import/export, verticality & pitch compensation, custom DI/DO/alarms | high |
| `SystemRWRegName_`, `RORegName_`, `RWRegName_`, `AxisRORegName_`, `AxisRWRegName_`, `PulseAxisR[OW]RegName_` | 47/32/5/12/14/7+14 | **Bus-generation controller register monitor** (EtherCAT era; 16 bus axes + pulse axis) | high |
| `SBT<nn>` / `SLED<nn>` | 41 / 11 | Touch-skin buttons / LED tiles (`CManuLedStatic`, `CLaserPowerLedStatic`, `CAxisLedStatic`, `CManufactureSpeedLedStatic`) | change-log; high |
| `mv` | 37 | **Main OpenGL view** context menu & drawing prompts (`COpenGLView`/`CMainView`) | high |
| `af` | 36 | **Auto-focus (electric focus head) status view** (`CAutoFocusView`, "ECH Property") | high |
| `RTC_RO_` / `RTC_RW_` | 33 / 15 | **Smart cutting-head telemetry** (lens temperatures, scatter, humidity, gas pressure, focus) | medium (see §8) |
| `ec` | 31 | **Extended card (EBH) status view** (`CECStatusView`): 4th axis, PWM, platform A/B | high |
| `ap` | 28 | **Adjust-point / auxiliary dialogs** (`CAdjustPtDlg`, `CDockPtDlg`, roll-sheet/batch file list `RollSheetSetDlg`/`BatchCutSetDlg`) | medium |
| `ss` | 25 | **System status / hardware test view** (`CSystemStatusView`): DI/DO/pulse send, access password | high |
| `cp` (+`cpd`) | 23 (+2) | **Precision (pitch/backlash) compensation dialog** (`CCompensateDlg`), interferometer files | high |
| `EtherAxisInfos_`, `AllAxisInfo_`, `AxisTypeName_`, `AxisInputStr_`, `GoHomeAdv_`, `EtherCAT*` | 22/15/6/8/6/~20 | **EtherCAT axis configuration** pages | high |
| `lp` | 20 | **Layer parameters dialog** (`CLayerPropDlg`/`CLayerPropPanel`) | high |
| `slp` | 20 | **Simple PLC / process-flow manager** (`CSimplePlcDlg`, menu `mf603` 流程管理) | high |
| `ZFReadOnly` / `ZFReadWrite` | 18 / 10 | FTC register view (new generation) | high |
| `gpa` | 14 | **Gas-pressure calibration** (`CGasPAdjStatic`, `CAdjGridCtrl`) | high |
| `ls` | 14 | **License/activation-code setup** (hardware ID → code, days) | high |
| `tk` | 14 | **Numeric touch keyboard** (`CNumTouchKeyboard`, `CTouchKeyboard`) | high |
| `ab` | 12 | **About / activation dialog** (`CAboutDlg`) | high |
| `dogActiveReslut` / `dogState_` | 11 / 9 | **Dongle** ("dog" = 加密狗) status strings | high |
| `db` / `da` | 10 / 7 | **Craft database** (工艺数据库) delete/add (`CSelectTechnologyDlg`, `CCraftPropDlg`) | high |
| `de` | 10 | Tool-tip descriptions for run parameters | high |
| `sp` | 10 | Floating **status window** (`CSysStatusDlg`, "启用状态悬浮窗") | medium |
| `pe` | 9 | **Platform exchange dialog** (`CPlatformExchangeDlg`) | high |
| `es` | 5 | **Manual two-point edge seek** (`CEdgeSeekDlg`) | high |
| `gmsg` | 5 | Graph object type names (scan cut, text, group, contour) | high |
| `jm` | 4 | **Joystick/remote pendant matching** (`CJoystickMatchDlg`) | high |
| `pp` | 4 | Parameter range-validation prompts | high |
| `dsc` | 3 | **Dual-servo (gantry) monitoring** (`CDualServoCheckDlg`) | high |
| `gd` / `gs` / `gv` | 3/2/3 | Basic process dialog (`CGraphParaDlg`), graphic zoom (`CGraphScaleDlg`), graphic interaction params | high |
| `pi` | 3 | Password/unlock input (`CPwdInputDlg`) | high |
| `rsd` | 3 | Roll-sheet move dialog (`CRollSheetMoveDlg`) | high |
| `sf` | 3 | Nest part info | high |
| `np_*`, `nset_UI_*`, `UI_Btn_*`, `MsgBox_*`, `FileTitle_*`, `FileFilter_*` | 15+3+4+6+4+2 | **Nesting module** panels (`CBasicNestPanel`, `CPartNestPannel`, `CSheetNestPannel`, `CReslutNestPannel`, `CNestGridCtrl`) | high |
| `Theme01/02`, `0_SongFont`, `0_FontSize` | 2+2 | Theme names (DARK/GRAY), UI font per language | high |
| `cd`, `cup`, `dp`, `ds`, `is`, `lcv`, `ti` | 1-2 each | Code-input dlg, custom DO lock type, dock point, DO select dlg (`CDOSelectDlg`), IP/access set (`CIPSetDlg`), laser-curve view (`CLaserCurveView`), text input (`CTextInputDlg`) | high |

---

## 4. Feature inventory by subsystem

Format: **English label** (Chinese) – ID – one-line description. Where the English column of the file is poor I
give my own gloss and note it.

### 4.1 Main frame: ribbon tabs, menus, toolbars (`mf*`)

Ribbon top-level tabs / groups (INFERENCE from label grouping, high):

| Tab / group | IDs | Items |
|---|---|---|
| **Start** (开始) `mf6` | mf7-11 | File: New, Open, Save, Save as; `mf_S_File_*` New/Open/Save/Save-as **Project** (nest module) |
| Interact (交互) `mf12` | mf13 | Import graphics (导入图形) |
| Aux devices under Import | mf13_1-5, mf13_3_1/2 | Rotate platform, Exchange platform, Roll-sheet control (+ Coil axis zero return, Record coil axis zero), Start cut-off sheet, Cut-off parameters |
| Initialize (初始化) `mf14` | mf15-18, mf17_1/2, mf222, mf482 | Go origin; Only X; Only Y; Gantry-correction homing (龙门校正回原 "Dual Servo Adjust"); 4th-axis homing; Only Z follower; XYZ synchronous homing; "ZF first then XY" |
| Edge seek | mf19, mf20, mf20-1, mf483 | Edge seek (巡边), Cancel edge seek, Auto edge seek, Open edge seek |
| Alarm / gantry | mf21, mf21_1/2 | Clear warning; Gantry position calibration (龙门位置标定 "Dual Servo Calib"); Calib settings |
| Backup/Help | mf22-24 | Backup, Help, About |
| **Draw** (图形设计 / 绘图) `mf25`,`mf4401` | mf27-29 | Contour select, Vertex (node) edit, View move |
| View (视图/查看/常用) `mf26`, `mf26_1/2` | mf38-43, mf38_0, mf198-200 | Show graphic bounding box; Show unclosed as red; Show index; Show path start; Show path direction arrows; Show move (rapid) path; Zoom in/out/reset |
| Edit | mf30-37 | Undo, Redo, Select, Select all, Invert selection, Select unclosed curves, Select graphics smaller than…, Select similar curves |
| Draw shapes (绘制) `mf44` | mf45-58 | Line, Circle, 3-point circle, 3-point arc, Scan arc, Ellipse, Rectangle, Rounded rectangle, Polygon, Star, Text, Polyline, Curve (spline), Point |
| Transform (图形变换 / 操作 / 几何变换) `mf59`,`mf182`,`mf590` | mf60-69, mf63_1, mf183-185, mf591 | Scale, Rotate (90° CW/CCW, 180° CCW, 45° CW/CCW, arbitrary), Mirror horizontal/vertical, Size |
| Align (对齐) `mf70`, `mf1050` | mf71-77 | Left, Right, Horizontal centre, Top, Bottom, Vertical centre, Centre |
| Advanced transform `mf78` | mf79-85 | Array (阵列), Group, Select all groups, Explode selected/all groups, Merge, Explode |
| **Process / craft** (工艺 / 基础工艺 / 工艺设置) `mf186`,`mf86`,`mf860` | mf87-99 | Lead line (引线): manual lead, check lead, clear lead, clear error marks; Home ref / dock (停靠); Path start; Reverse; Gap (缺口); Seal (封口); Overcut (过切); Inside cut (阴切); Outside cut (阳切) |
| Micro joint (微连) `mf100` | mf101-103, mf103_1/2, mf100_1 | Manual micro joint, its parameters, Auto micro joint, Explode micro joint, Clear micro joint; Fill circle |
| Advanced process (高级工艺) `mf104` | mf105-111, mf202-204, mf610/611 | Sort: left→right, right→left, bottom→top, top→bottom, local shortest path, manual sort, inside→outside, outside→inside, small-graphics first, selected layer first/last |
| Fly cutting (飞行切割 "Scan cutting") `mf112` | mf112_1-5 | Circle fly cut, Segment (line) fly cut, Advertising-sign circle fly cut (广告发光字), Arc fly cut, **Scan engraving** (扫描雕刻, new) |
| Compensation etc. | mf113, mf217, mf486, mf452, mf114 | Kerf compensate, Cancel compensate, Set compensate, Auto compensate (自动补偿), Bridge (桥接) |
| Corners | mf400-404, mf484, mf485 | Chamfer (倒角), Auto chamfer, α-angle (α角 – sharp-corner loop), Clear α, Unload/release corner (释放角), Manual chamfer, Add α |
| Cool points | mf406-408, mf406-1 | Cool point, Manual/Auto cool point, Clear |
| Common line | mf250, mf250_1 | Share edge (共边), C-type share edge |
| Text | mf251, mf254, mv33, mv20, ti0 | Advanced text (精品字), Advertising text (广告字), Text → curves, "Please input text", Font height (mm) |
| Split/other | mf216, mf213, mf233, mf487, mf604, mf605, mf474-476, mf470-473 | Split, Smooth, Refactor (重构), Ring cut (环切), Normalize start point, Single-point replace circle, Curve smoothing, Curve segmentation, Merge connected lines, Optimize, Remove duplicate lines, Replace circle, Standard sheet |
| Auxiliary (辅助) `mf115`,`mf139` | mf116-118, mf140/170/171, mf180, mf702, mf703, mf229, mf214/215 | Measurement, System analysis, Component monitor, Enable/Disable dust extraction, Laser interferometer, Ball-bar (球杆仪), Dual-servo monitoring, Data measurement, Enable/disable error measurement |
| Monitor panes | mf119-126 | Controller, FTC, Simulation speed, Running status, Alarm, Log, Diagnostics, Error measurement |
| **Advanced** (高级) `mf127` → Hardware `mf128` | mf129-135, mf212, mf436 | Options (参数配置), Reconnect, Restart, Upgrade, Set local IP, Access config, Remote pendant match, Hardware test, Network test |
| Software `mf136` | mf137, mf138, mf210/211, mf234-241, mf245, mf480/481, mf444, mf445-447, mf451, mf431, mf431-1 | Options, System recovery, Parameters backup/import, Export/import parameters to/from file & hardware, Param upload, Custom IO, Keyboard fine-tuning (auto step / step length), Status window, Open log folder, Open dongle log |
| Upgrade `mf510/511`, `mf247`, `mf440`, `mf625` | | Update, One-key (auto) update, FTC upgrade, Auto-focus upgrade, Extended-card upgrade |
| Process control (加工控制 / 运行参数) `mf141/142` | | Run control, Run params |
| Coordinate systems `mf307` | mf300-306, mf513/514 | Float coordinate, Work coordinate 1-6, Manual coordinate, Set origin |
| Nesting `mf460`, `mf411/412`, `mf463-466`, `mf420` | | Nest, Advanced nest, Nest parameters, Simple nest, Add part, Add sheet, Import part/sheet, Export to sheets (分板导出) |
| Reports `mf252/253`, `mf801` | | Work report [new]/[all], Device (run) report |
| Tools `mf790`, Shortcut `mf181/195/196/197`, `mf603` | | Shortcut process, One-key planning (一键规划) + parameters, Process (flow) manage |
| Quick-process toolbar `mf187-194` | | Graphics optimize, Sort, Optimize params, Sort params, Micro-joint params, Dock params, Open file, Rotate |
| Status-bar strings | mf143-146, mf242/243, mf430, mf433/434, mf228 | "MCC <Ready>", "FTC <Ready>", "Laser <Ready>", "Monitor <Ready/Offline>", "Ping: n", "Focus Pos(mm)", "SC system trial period remaining: %d days" |

### 4.2 Main view (canvas) context menu and drawing prompts (`mv*`, `gmsg*`, `gp61-85`)

| Label | ID | Description |
|---|---|---|
| From this point continue | mv0 | Set breakpoint / resume point on the path |
| Reset view, Finish/Cancel/End draw, Enclose, Line, Arc | mv1-6, mv3-1 | Interactive polyline drawing with line/arc segments and close |
| Copy, Paste, Delete, Cancel | mv7-10 | Clipboard |
| Clear track line | mv11 | Clear the process indicator line |
| End start-point set, Previous step, Exit manual sort, Last/First process | mv12-16 | Start-point and manual sequencing modes |
| Measurement result X/Y/length | mv17 | Ruler tool |
| Manual sort: end / step / step 0 | mv18-23 | Manual ordering by clicking contours |
| Text convert to spline; Select same-layer contours; Apply craft to similar contours | mv33-35 | |
| File errors: invalid path, empty, broken, incomplete, not an SC file, SC file load failed, larger than machine range, partial import failure | mv25-32 | `.chf` loader diagnostics |
| Property grid labels: X/Y coordinate, Height, Width, Align point, Inclination angle, Radius, Central angle, Major/minor axis, Corner R, Vertex count, Outer/Inner R, Index, Contour count, Font, Font size, default font 宋体/Segoe UI | gp61-79 | `CGlyphPropPanel` shape properties (rect, ellipse, polygon, star, text) |
| Layer names: Layer 1, Background layer, Mark layer, Film layer, Jog layer, Paint layer | gp80-83, lp6, gp300/301 | Special layers: background image (`np11` 文件底图), marking, film removal, jog cutting |
| Object kinds: Scan cut, Text, Group, Contour, "not processed" | gmsg0-4 | |

### 4.3 Import / export formats

| Feature | ID(s) | Formats |
|---|---|---|
| Open process file | mf149/150 | `*.dxf; *.chf; *.nc; *.txt; *.cnc; *.g; *.plt` (DXF, native CHF, G-code variants, HPGL) |
| Save process file | mf152, mf650 | native `.chf` ("Untitled-" default); autosave `File/autosave.chf`, `File/Temp/tempGraph.chf` |
| Import limit | mf154 | files > 100 MB refused by Import; use Open |
| Craft (layer) file | FileFilter_CraftFile, lp7-11 | "工艺文件" export/import of layer parameters; A250418_0-A250419_2 **technology library** (工艺库) with description, export-to-library, overwrite prompt |
| Hardware parameters | hp9-15 | `*.xml` export/import (`File/BkHardPara.xml`) |
| Controller firmware | mf173-179 | `*.mcf` (formerly `*.scb`) |
| FTC firmware | mf308 | `*.zfb` |
| Auto-focus firmware | mf438 | `*.afb` |
| Extended card firmware | mf623 | `*.efb` |
| Interferometer data | cp5, hp34 | `*.rtl; *.ren; *.pos; *.csv` (Renishaw laser-interferometer exports) |
| Activation code file | ls5-9 | `*.txt` with "Hardware ID: %d / Code: %d Days: %d" |
| Nest files | FileTitle_*, FileFilter_DxfFile | Export nest result, import parts file, open/save nest project |
| Process task | A250320_0-3, newLang41/42, A241115_1, A241220_0-3 | Task import/save/export, **offline processing file** generation + upload (500 MB limit `A250606_1`) |
| DXF options | pd373/374, pd485-1, pd369/369_1 | auto-explode DXF groups, text→curves, read colours, auto-smooth splines |

### 4.4 Layer & craft parameter dialogs (`lp*`, `pd112-156`, `pd810-849`, `pd930-944`, `newLang50-94`, `pd2705-2749`, `A2410xx`)

Layer dialog (`CLayerPropDlg`, `lp0` 图层参数): per-layer flags and a 12-group parameter set. Validation rules
`lp16-19`: cut height ≤ lift height, ≤ pierce height, ≤ film-cut height; pierce height ≤ 3rd-level pierce height.
Notes/power curve/frequency curve per layer (`lp2-4`, `pd154/155`, `lcv0` delete curve point).

Per-layer switches (`pd112-124`): Uncut layer, Short-move no-lift, Short-move keep gas, No follow, Pre-pierce,
With-film cutting, Keep gas on, Direct cut, Section pierce, Gradual pierce, Fixed-height cut, Off-sheet follow,
3-stage pierce, Dynamic power vs speed (`A241012_5` dynamic frequency; `A241012_1-4` smoothing type Default/Linear/Smooth).

Process type enum (`pd815` 基础工艺.加工方式): Direct cut, Fixed-height cut, Advanced fixed height, 1-/2-/3-step
pierce (`pd810-814`), now 4th/5th-level pierce (`newLang50-56`).

| Group (zh / en) | IDs | Parameters |
|---|---|---|
| 切割基础 Cut basic | pd125, pd129, pd589, A241025_0 | cut speed, cut height, cut focus, cutting height |
| 切割激光 Cut laser | pd127, pd128, pd132, A241025_3 | duty cycle, frequency, peak power, laser-on delay |
| 切割气体 Cut gas | pd130, pd131, A241025_1/2 | gas type, pressure, no-gas-off for short distance / during process |
| 切割过程 Delays | pd126, pd137, pd138 | dwell, before-off delay, after-off delay |
| 高级工艺 Advanced | pd133-136, pd853, pd854 | slow start enable/length/speed, lift height, pre-laser-on factor, exchange platform |
| 穿孔参数 Drill | pd139-144, pd663, pd789, pd849 | delay, power, height, pressure, freq, gas, peak power, max direct-pierce height, max direct 2nd-pierce height |
| 渐进穿孔 Gradual | pd145/146, pd658, pd665-668 | speed, delay, end height, type (by speed/time), time |
| 三级穿孔 3-stage | pd147-152, pd664, pd522-525, pd669/670 | delay, power, height, pressure, freq, gas, peak; 3-stage section/gradual |
| N级穿孔 (N=1..5) Basic/Laser/Gas/Process | pd816-848, pd930-944, newLang60-94 | height, focus, focus-gradual enable/target/time; power (duty), freq, peak; gas type, pressure; time, gradual enable/time, dwell, post-off blow |
| 无感穿孔 Smooth pierce | A241009_3-6, A241010_1-3 | height, time, focus; duty, freq, peak |
| 闪电穿孔 Bolt pierce | pd2705-2707 | enable, end duty, end freq |
| 除渣 Slag/residue cleaning | pd2710-2720 | enable, height, speed, focus, gas, pressure, peak %, duty, freq, radius, spiral turns |
| 起刀 / 收刀 Start / end segment | pd2730-2746 | enable, length, speed, laser control, duty, freq, (end: pressure) |
| 预穿孔工艺 Pre-drill | pd1010/1011 | pre-drill no lift, re-pierce before cut |
| 冷却气 Cool-down gas | pd945/946 | |
| 高级定高 Adv fixed height | pd129-2, pd136-1/2 | cut position, lift height/position |
| ZF振动抑制 Vibration suppression | pd2747-2749, A240827_1-8 | thin/thick plate factors (0-90), coefficient, -5/0/5 presets |
| 图形工艺控制 Graph process control | pd593-598, pd595_1-3, A250105_0, newLang501, eNewLang101 | auto inner/outer classification, micro-joint deceleration & speed, re-pierce at micro joint, trace-less micro joint & power %, cool-point delay, pre-pierce max count, batch film-removal count, re-pierce after continue, continue retract distance |
| 精品字参数 Adv text | pd1810/1811 | type, offset |

Craft database (`da0-6`, `db0-9`): named process records with ID/name/update time; insert/update/delete/reset;
SQLite-like "database connect failed" (`db9`). `A250421_0` warns when a task's laser type differs from current.

### 4.5 Machine control panel (`mp*`, `SBT*`, `SLED*`, `ap*`, `pd74-106`)

| Function | IDs | Notes |
|---|---|---|
| Start / Pause / Stop / Continue | mp54-56, mp27, pd800-802 | Also DI-mapped (Start/Pause/Stop DI via custom DI `hp64/65`) |
| Dry run (空走), Frame (边框), Simulate (模拟), Preview | mp57-59, SBT05-08, mf121 | Frame speed `pd528`; simulate speed pane |
| Breakpoint locate / continue | mp60, mp64, SBT24, mf231, mp122, mp145, A250320_3 | "Breakpoint invalid, set manually", "graphics changed, breakpoint invalid" |
| Backward / Forward along path | mp61/62, mp65, pd84/85 | pause back/forward length & speed |
| Jog: step / fast / continuous | mp31/32, SBT19/42, pd81-83, pd74/75, A240904_1-2 | step lengths mp90-99 (0.05…200), speeds mp100-110 |
| Jog with laser (点动切割) | mp184, pd76, pd956, mp300 | |
| Homing | mp29, mp29_1/2, mp30, SBT16/22/25/26, mf156-158, mp79-82, mp119, mp125/126, mp133 | system/XY/Z/exchange-platform homing prompts |
| Set/return manual origin, work origin | mp129/130, mp163/164, af20/24, mf513/514 | 6 work coordinate systems mp201-206, float mp200 |
| Mark points 1-6, go mark, mark coord | mp33-39, mp390/391, SBT33-41, pd62-67, pd490/491 | |
| Return-after-done: dock/start/end/origin | mp67-72, pd77/78, pd86, pd58-61, dp0 | |
| Gas: Air/O2/N2, high-pressure variants, blow | mp42-48, SBT10-13, pd87, mp41 | |
| Laser: shutter, red light, laser, burst power/time, precise beam | mp49-53, mp321/322, SBT01-04, pd88, pd95/96, A240904_3-6 | |
| Follow (跟随) | mp41, SBT20 | |
| Selected-only process | mp52, pd79, pd1568 | part mode / float mode |
| Loop process | mp171, mp63, SBT29, pd80, pd340-342, ap11, A240904_8/9 | loop type/times/interval |
| Piece count / timer | mp160-162, mp75, mp77, pd850, pd855-859, pd858, mf512, gp219/mf800 | plan count, increment/decrement, action when done (none / prompt / prohibit) |
| Soft limit | mp73, pd106, pd404/405, ec30 | |
| Positioning move | mp111-113 | "Position to" dialog |
| Estimated time report | mp26, mp83/86, gp2000/2001 | cut/idle/pierce lengths and times, system delay |
| Status texts | mp7-17, mp22-25 | MCC Ready/Origin/Jog/Stop/Process/Offline; FTC Ready/Follow/Drill/JogUp/JogDown/EStop/Offline; Laser Ready/Offline |
| Nozzle clean | mp134, pd2000-2012, ap40-43 | brush Z, dive depth, start XY, speed, direction, times, length |
| Focus +/- , focus origin | SBT09/28/23, af14/15 | |
| Pallet / exchange / auto-feed / rotate / unlimited roll | mp180-187, SBT26/30-32, mp500/501 | |
| Handheld pendant status | A240613_0-5, A240828_1, mp0/01 | loaded, disconnected, low battery, poor signal, sleep/wake; "inactive page, handle unavailable" |
| Dock/adjust points | ap2-10 (9 positions), ap20-22, ap30/31 | set zero, all zero, auto seek, seek settings |
| Precise beam / burst test | newLang200-206 | laser test view: power/duty/freq, long-emission time, remaining time |

Run/process parameter group `pd74-106` (加工控制/运动控制/气体控制/激光控制/跟随控制): jog step/fast/slow, move
speed/acc/acc-time (`pd661`), cut acc/acc-time, spline & corner precision, default pressure, gas-on/first/change
delays, keep gas, short-move no-lift distance, frog-jump lift, follow during dry run, clear track after done, seek
edge before work (`pd86_1`), Z to safe position before process (`pd493`), tooltips `de0-10` (ranges 60-250 ms,
corner precision 0.01-1.0).

### 4.6 Nesting (`np*`, `nset*`, `pd_Nest_*`, `pd766-769`, `mf460-466`, `mf496-506`, `mf520-524`, `sf*`)

Parts / sheets / results panels (`nset_UI_Part/Sheet/Result`), part name/number, remnant sheet (残板) name/number,
standard sheet W/H, nested part count, copy part/sheet/result to main view, background graph. Parameters: part
number, sheet number/size, edge stock, part spacing, nest direction (L→R / D→U), nest inside parts, rotate step
angle, share edge + min length, nest accuracy, only selected parts. Engine: `AutoNest.dll` + `SmartNest.dll`
(error text `mf524` "Nest-DLL kernel error"; error codes in §7.9). `mf651/652` select outer/inner mould.

### 4.7 Array / duplicate, transform parameters

`pd332-338` array rows/cols, offset type (offset vs spacing `pd301/302`), row/col offsets, directions
(`pd303-306`); `pd339` rotate angle; `pd348-350, pd659` scale percent/width/height/ratio lock; `pd2700-2702`
graph shift X/Y; `gs0/1` zoom centre.

### 4.8 Lead-in/out, gap/seal, micro-joints, corners, cool points, bridge, compensation

| Feature | IDs | Parameters |
|---|---|---|
| Lead line | pd315-322, pd599, newLang40, pd297-300, pd307-310 | type none/line/arc/line+arc, angle, length, radius, start-point mode (long-side-first / vertex-first / unified %, keep), only closed, only selected, outer = inside cut |
| Gap / seal / overcut | pd323/324 | gap length, overcut length |
| Micro joint | pd313/314, pd313_0-03, pd314_0/1, pd325, pd343-346 | by number / by spacing, direction (along path / X only / Y only), at head of open contour, min size, length, count, distance |
| Chamfer / round | pd600-602, pd607-614, pd603 | radius, min/max angle; α-angle max angle, min edge, outer lead length, type auto/outer/inner/both; unload-angle radius |
| Cool point | pd604-606 | at lead-in, at sharp corners, max angle |
| Bridge | pd347 | width |
| Kerf compensation | pd501-507 | inner-shrink/outer-expand (outer shrink or expand), all shrink, all expand; width; `ap12` advice |
| Auto compensation (measured size error) | pd762-765 | max/min size error type (too large/small) and value |
| Fill circle | pd725-728 | type, radius, stock, spacing |
| Sort | pd351-358, pd372, pd465/466, pd700-705, pd1607 | none, direction-first, length-first, distance-first, L→R, nearest move, die-board sort, small-first |
| Graphics optimize | pd363-371 | remove tiny graphics (+min length), duplicate lines (+precision), merge connected (+precision), auto smooth (+precision, skip Bezier), large-contour auto split (pd532_1/2) |
| One-key planning steps | pd476-481 | H-mirror, V-mirror, optimize, sort, lead, micro-joint |

### 4.9 Fly cutting (飞行切割), scan engraving, common-line cutting

`pd289/290` encoder check tolerance ratio, circle-fly speed ratio; `pd513-515` circle/line fly PWM open/close time
correction; `newLang500` square-fly coordinate tolerance (µm); `pd707/708, pd851` circle-fly sort & max fly-line
length, prefer circles in one contour; `pd715` line-fly start corner (`pd710-713`); `pd716-719_4` collinear
tolerance, max smooth link, max fly line, min horizontal gap, min scan line length, side line length, scan direction
X/Y/along edge (`pd720-722`). `mf161` "graphics do not satisfy fly-cut condition". Registers `RegName37-39, 87`
show fly-cut data FIFO and encoder-tolerance registers in the MCC. Common line: `mf250/250_1`, `pd_Nest_ShareEdge`.

### 4.10 Edge finding (寻边 / 巡边)

Three mechanisms are present:

1. **Manual two-point** (`es0-4`, `A250211_1`): mark P1/P2 with red light → rotation angle.
2. **Capacitive edge seek** via FTC (`A250211_0`, `A250212_1-11`, `A250220_0-3`, `A240902_1-4`, `A250213_0-3`,
   `A250219_0`, `pd2108-2115`, `pd2013`, `mp88_1-4`, `newLang34`): 3-point fast / 4-point precise, auto sheet-size
   detection, edge correction value, start corner must be a bounding-box corner, detection-point spacing/ratio, fast/slow
   /edge-out speeds, X/Y margin ("留边"), intersection & angle result, remembered angle with clear/restore/restore+home,
   auto-clear after processing, warning about head crash if sheet size wrong.
3. **Disc centring** (`newLang27-33` 圆盘寻中) for round stock.
   FTC registers `ZFReadOnly15-17`, `pd759-3/4`, `RegName132-136` expose edge-seek sample data and positions.

### 4.11 Height follow (调高器 / FTC / ZF) and auto-focus (电动调焦 / AF / ECH)

FTC status view (`zf0-107`): product model, versions, run status, signal strength/height, Z coord, temperature,
alarm/DO state, **head calibration** (浮头标定) & **servo calibration** with quality Excellent/Good/Bad, restart,
reconnect, factory reset (password), run params (follow speed, slow/fast jog, lift speed, dock height), advanced
mode (soft limit coord, crystal error count, follow lib version, DA register), import/export params.
Connection types (`pd171-173, pd1731-1733_1, pd641-644`): FTC10 network, FTC61 IO, FTC61 PC serial, FTC61 MCC
serial, MCC3721H/NA integrated, onboard FTC, net, IO, PC serial. Height-controller parameter families:
`pd394-443` (mechanics: servo model, pitch, pulses/rev, gain, switch types; run; process; alarms), `pd426-443`,
`pd542-561`, `pd565`, `pd650-656`, `pd729-759-4` (PID: KP/KI/low-pass/saturation for jog & follow, zero clamp,
signal compensation, touch-panel alarms, soft limits 1/2, edge-seek sampling), `pd2100-2107` (fast down, sensitivity,
follow height, move-out lift/check time/tolerance, move-in height, safe height), `pd947-949`, `pd1004`
(frog-jump type normal/advanced), `pd1300`, `pd2014-2016` (auto calib after clean), `ZFReadWrite08-38`,
`A250212_0`, `A250220_0`. Servo type vendors `pd562/563` 联品 "Super", 天星 "TXStar".

Auto-focus (`af0-30_5`, `pd564-597`, `pd660`, `pd681-692`, `pd790-797`, `pd955`, `pd1611/1612`, `pdAFDA_*`,
`A240919_1-3`, `A250620_0`, `AllAxisInfo_8-11`): enable, control type (none / analog DA / extended axis / pulse
axis / EtherCAT / "XY V2_0" `pd640_*`), screw pitch, direction, micro-step (800…6400 `pd578-583`), current, travel,
origin offset, run modes normal/test1/test2, gas-pressure & temperature alarms with thresholds, LED colours
(`af29`), focus drill start/end/speed, focus map data, status window with cavity pressure, collimator/focus/lower
protective lens temperatures, humidity (`af30_1-5`). DA-driven focus (`pdAFDA_*`): DA port, invert, max DA, enable
DO, go-origin DO with delays, done/alarm DI.

Smart head telemetry `RTC_RO_01-33` / `RTC_RW_01-15`: focus position, collimator / focusing / protective lens /
internal temperatures, collimator scatter, cutting gas pressure, internal gas pressure, humidity, mirror
configuration, alarm words 1/2, focus shift & real-time deviation, alarm-limit settings, set focus, home, alarm
clear, failure-alarm mask, versions 1/2, cutting-head name.

### 4.12 Gas control

Outputs `pd1-12`, `pd266-278`, `pd1025-1030`: main valve, low/high secondary valves, low/high Air/O2/N2, proportional
valves Air/O2/N2 (DA) with switches and max pressure per gas (`pd272-275`, `pd773-775`, `A241202_2-4`), opening
delay (`A241224_0`). Pressure calibration (`gpa0-13`, `pd1600-1606`, `hp80`): per-gas voltage→bar tables, point
count, save. Units bar/MPa (`pd900-902`). Gas-pressure warning on AF head (`af28/30`).

### 4.13 Laser source control

Laser types `pd47-51, pd50-1/2`: 锐科 Raycus, IPG, 半导体 Semiconductor (diode), 创鑫 MaxPhotonics, nLight, 国志
GZ (Guozhi), Others; source class `A241024_0-2` Fiber / CO2 / Blue; CO2 glass tube `A241025_4`. Control
(`pd258-265`, `pd248/259/950`, `pd174-180`, `pd260/261`, `pd662`, `pd286-288`, `A250516_5/6`, `A250522_0-3`,
`A250306_2/3`, `A241104_0-3`): laser type, control type (SC MCC / SC PC / **CypCut** compatible), DA port 1/2
range 0-10/0-5/0-4 V, PWM 24 V / 5 V / DA, remote key, shutter, emission, red light, external-control output; CO2 set:
remote start, gate, red light, laser, external control. Serial (`pd183-196` COM1-9, 9600-115200), Ethernet
(`pd451-453`, ipAdd `LaserIP=10.1.1.170:10001`). Auto shutter during process `pd467`, PWM per contour `pd1608`,
max power `pd959`, laser lock/unlock code (`gp120-122`, `mf490-495`, `pi0-2`), laser "curve" view (`lcv0`).
Alarm mapping DI `pd23/27, pd284, pd487-489` (fiber) and `A250429_0-5` (CO2).

### 4.14 IO, diagnostics, registers, tests

* DI/DO names `pd0-37-2` (Z follow, valves, standby/process/alarm lights, ring, remote key, dedust, Z drill/jog
  up/down, E-stop, water alarm, Z alarm, follow/drill in place, laser alarm, DI7-14, exchange start/stop, roll
  ready/start-stop). Custom DO/DI/alarms (`hp39-50_2`, `pd521`, `pd803`, `pd1400-1404` Fn1/Fn2, `newLang300/301`,
  `cup0/1`), DI filter times DI1-24 (`pd1701-1724`), switch logic NO/NC (`pd164/165`, `pd785-788`, `pd1201-1204`).
* Section (zone) DO `pd538-541, pd566, pd592` (dust-extraction zones by rows/cols), lubrication `pd535-537`,
  LED/ring blink `pd673-678, pd776-781`, PLC process 1 `pd1562-1567`, after-done DO `pd1509/1510`.
* Hardware test view (`ss0-24`, `newLang400-403`): DO toggles, DI view, send N pulses per axis, DA1/DA2/PWM entry,
  read-only vs read-write access with password, NEG/ORG/POS limit LEDs, oil, dedust.
* Register monitors: MCC3721 `RegName1-143` (incl. network IP/mask/gateway, baud rates, laser type, DA calib, DI
  filters, wireless handle & USB adapter addresses, fly-cut FIFO, Z-phase, edge-seek positions), bus generation
  `RORegName_1-32`, `RWRegName_1-5`, `SystemRWRegName_1-47`, per-axis `AxisRO/RW`, pulse axis. FTC `ZFReadOnly01-18`.
* Tests: burn-in (`newLang1-24`: XY/Z/XYZ idle-run cycles, interval, return, mask capacitance alarm), laser
  emission test (`newLang200-206`), single-axis pulse test (`newLang400-403`), network test (`mf436`), monitor test
  (`mf601/602`, `pd870-873`), dual-servo check (`dsc0-2`, `pd1900-1902`), ball-bar (`pd1100-1106`), interferometer
  run (`mp114-118`, `pd455-462`), error measurement (`mf126/214/215/449`).
* Compensation dialog (`cp0-33`, `hp18-28`, `hp52/53`, `hp61-63`, `pd454`, `pd469/470`, `A250616_0/1`): none /
  backlash only / full pitch; verticality correction via square AB/AC lengths (min 0.02°, parallelogram check);
  per-position forward/backward measured & error, backlash average/adjust; file `File/PithCompensate.pcf`.

### 4.15 Motion axes & controller configuration

Pulse generation (`pd197-247`, `pd471-475`, `pd233-238`, `pd2421-2430`): X, Y1, Y2, 4th axis usability, index, dual
drive, encoder reverse, limit logic, max length, pulse equivalent, max speed/acc; dual-drive error alarm/tolerance/
time, 4× encoder, safety decel factor, interpolation period, homing (Z-phase, sample signal, switch logic, fast/slow
speed, direction, offset), second homing `pd852`, default gantry homing `pd379_1`. Servo presets `pd380-383_1`:
Panasonic A5, Delta B2, Yaskawa, Inovance IS, Leadshine L7P/L7RS.
EtherCAT generation (`EtherAxisInfos_0-28`, `AllAxisInfo_0-14`, `AxisTypeName_0-5`, `eNewLang1-7`, `GoHomeAdv_*`,
`A241009_1/2`, `A241021_1`, `EtherCATSelect`): axis work type, limit logic/ports, max stroke, reverse, encoder
reverse, home direction, lead, encoder resolution, Z-phase, second home, sample signal, brake output + release
delay, rotary axis, alarm input type/mask, acc/acc-time; axis roles X/Y/Y-slave/Z-height/coil/exchange/focus,
node-not-scanned error, function-conflict error; bus init delay. Controller network `pd444-447`, `is0/1`, `mf133`,
`RegName79-81`; hardware version `pd296/805`, min versions in `ipAdd.ini [Soft]`.
Small-circle speed limit `pd1560/1561` (also `JumpAddTime.txt [LimitSamllCircleVel]`), limit deceleration
`pd516/517`, system max speed/acc `pd509/510`, crash protection `pd1571-1575`, encoder-velocity `pd804`.

### 4.16 Auxiliary machinery

* **Exchange platform** (`pe0-8`, `pd954`, `pd983-1001`, `pd1511-1547`, `pd1801/1802`, `pd2210`, `pd2500/2501`,
  `ec28/29`, `mp166-170`, `mp180-183`, `mf661/662`): manual/semi/auto, push A/B, clamp/slow/fast outputs, in-place
  inputs, delays, timeouts, exchange length/speed, VFD or PLC1-A/B.
* **Auto roll / coil feed** (`hp72`, `pd1500-1508`, `pd1529`, `pd1569-1570_4`, `pd2600-2617`, `pd2650/2651`,
  `rsd1-3`, `mp301`, `ap100-104`): single roll length/speed, ready DI, back-roll, VFD motor with W-axis external
  encoder (`pd26010`), forward/backward roll, clamp DOs, coil-axis zero.
* **Unlimited roll** (`pd2400-2414`, `A240902_5`, `MsgBox_CutOffSheet`): cut-off sheet width/speed/X coords, direction.
* **Batch cut** (`pd2415-2420_1`, `ap100_1`, `mf705`): file list with planned/done quantity, start DI.
* **Rotate platform / rotary station** (`hp73`, `pd2200-2310`): 2 positions, rotate axis DO, points per station.
* **Lifting platform** (`A250410_1-4`, `LiftingPlatformDlg`).
* **Extended card** (`ec*`, `hp70`, `pd960-979`, `pd1200-1205`, `gp200-216`): 4th-axis parameters, modes.
* **Dust extraction**, **lubrication**, **nozzle cleaning** – see §4.14/4.5.

### 4.17 Alarms display, log, reports, statistics

Alarm panel (`gp84-94`, `gp140`, `gp88/88-1`, `mf123/124`, `pd526/527`, `pd671/672`): time/description/message
columns, "Alarm: %s %s" / "Alarm cleared", reconnect, clear alarm, Z home, settings; alarm bar colours; log enable.
Work report (`gp130-146`, `mf252/253`, `Report/report.exe`, `Report/*.txt`): file name, size, cut length, dry length,
pierce count, end time, process time, piece count; CSV rows in `Report/TotalReport.txt` (GBK:
`未命名-1,20.84×20.84mm,0.07m,0.05m,0,0分01秒,2024-07-25 14:48:15`). Device report (`pd880-888`, `mf801`, `ap20-22`,
`mp302`): total power-on, controller comm time, processing time, last processing time, laser-on time, processing
count, X/Y/Z total travel; `A241105_1` run time. Statistics view `mf117`.

### 4.18 Remote monitoring, login, offline/online

`pd518-520` machine ID / data-card ID / command ID, `pd533/534` enable + heartbeat, `mf225/226/242/243`, `pd870-873`
test; `ipAdd.ini MonitorIP=47.104.17.21:9001`. `A241218_0-3` device connection (type, number, address),
`A241221_0` connect, `A241105_0` Wifi, `A250416_0-2` **Login**, offline/online run (`LoginDlg`), `A250529_0` file upload.

### 4.19 Dongle / registration / licensing

* Dongle ("加密狗" USB key): `dogState_*` (normal, no hardware, unauthorized, data sector broken, clock broken,
  clock illegal, trial over, not-the-app, verification error), `dogActiveReslut1-11` (no hardware, invalid hardware,
  clock illegal, data damaged, wrong code, hardware ID mismatch, vendor ID mismatch, code expired/used, board clock
  too slow, clock op failed, code entry error), `mf496` "No dongle", `mf431-1` dongle log.
* Activation: `ab1-12` (Hardware ID, activation code, permanent licence, remaining days, wrong code, unauthorized
  hardware, wrong password), `ls0-13` (8-digit code, 0-9999 days, save to txt), `mp1-6` (trial expired, clock damaged,
  days left), `mf228`, `cd0`, `pi0-2` laser unlock, `A250417_0/1` manufacturer password & start with operator
  privileges, `pd1573/1574` custom manufacturer name (`ipAdd.ini Custom=1234`, `CheckUserID=109`).
* Class `CHidUsb` in the exe corroborates the HID dongle path.

### 4.20 Updates

`mf132`, `mf173-179` controller `.mcf` upgrade with automatic "hardware version too old" (`mf232`, `mf432`, `mf701`),
`mf241/244` auto-update file checks, `mf510/511`, FTC/AF/EC upgrades (`mf247/248`, `mf439/440`, `mf624/625`),
`eNewLang100` "hardware parameters changed, hardware restart required".

### 4.21 Software settings / theme / fonts

`pd378` language, `pd379/672_1` theme (`Theme01 DARK`, `Theme02 GRAY`), `pd862` UI orientation landscape/portrait,
`pd468` shortcut process, `pd529/1573` manufacturer name, `pd530` prompt homing at start, `pd630` auto-load last
graph, `pd485` ruler, `pd375-377` refresh period & keyboard move step, `A250419_3` drawing background colour,
`pd361/362` white/black, `gv0-2`, `0_SongFont/0_FontSize` per-language UI font (宋体 14 / Arial 14; `mf147`
微软雅黑/Segoe UI, `gp79`), `mf220` "not supported on Windows XP", `mf600` "use SC1000 V1.0.107 SP2 for this board".

---

## 5. Dated additions (`A<yymmdd>_n`) – timeline of 2024-08 → 2025-06 development

| Date | IDs | Feature |
|---|---|---|
| 2024-08-27/28/30 | A240827_1-8, A240828_1, A240830_1 | AF homing, ZF vibration suppression thin/thick, pendant page lock |
| 2024-09-02/03/04/11/19 | A240902_1-7, A240903_1, A240904_1-9, A240911_1, A240919_1-3 | edge-seek spacing/direction, roll cut direction, jog/burst panel labels, signal correction period, focus range/home |
| 2024-10-09/10/12/21/24/25 | A241009_1-6, A241010_1-3, A241012_1-5, A241021_1, A241024_0-2, A241025_0-4 | EtherCAT init delay, smooth pierce, power smoothing, encoder res., laser class fiber/CO2/blue, CO2 glass tube |
| 2024-11-04/05/15 | A241104_0-3, A241105_0/1, A241115_1 | CO2 IO set, Wifi, run time, offline process |
| 2024-12-02/18/20/21/24/25 | A241202_1-4, A241218_0-3, A241220_0-3, A241221_0, A241224_0, A241225_0 | parts library, per-gas max pressure, device connection, offline file upload, valve delay |
| 2025-01-05/06/07 | A250105_0/1, A250106_0, A250107_0 | re-pierce after continue, import failure, undefined layer gas, restore layer params |
| 2025-02-11/12/13/19/20 | A250211_0-5, A250212_0-11, A250213_0-3, A250219_0, A250220_0-3 | capacitive edge seek wizard |
| 2025-03-06/20 | A250306_2/3, A250320_0-3 | external control output, task import/save |
| 2025-04-10/16/17/18/19/21/29 | A250410_1-4, A250416_0-2, A250417_0/1, A250418_0-4, A250419_0-3, A250421_0, A250429_0-5 | lifting platform, login, operator privileges, technology library, CO2 alarms |
| 2025-05-16/22/29 | A250516_0-6, A250522_0-3, A250529_0 | axis names, PWM 5 V, PWM port type, file upload |
| 2025-06-06/07/13/16/20 | A250606_0/1, A250607_0-3, A250613_0-5, A250616_0/1, A250620_0 | 500 MB limit, fiber/CO2 switch warnings, pendant status, backlash compensation, AF distance coefficient |

---

## 6. Full alarm / error / warning message table

Codes: the language file carries no numeric codes; the *ID* is the code the software uses. Where the ID number
maps to a controller bit (gp1-59 look like a bit-ordered alarm-word decode: servo 1-4, encoder 5-8, dual-drive
9-12, network 13, FPGA 14/15, E-stop 16, hard +limit 17-20, hard -limit 21-24, soft +limit 25-28, soft -limit
29-32, Z 33-43, laser 44-55, chiller 56, comm 57-59) I note it as INFERENCE (medium; consistent with `RegName5`
"Alarm status" and `RegName31` "Laser alarm status" registers).

### 6.1 Controller / axis alarms (`gp*`)

| ID | Chinese | English (file) / gloss |
|---|---|---|
| gp0 | 硬件未连接，请检查控制器及调高器是否连接就绪 | Hardware not connected, check controller and FTC |
| gp1-4 | X/Y1/Y2/W轴伺服输入告警 | X/Y1/Y2/W axis servo input alarm |
| gp5-8 | X/Y1/Y2/W轴编码器告警 | encoder alarm |
| gp9-12 | X/Y1/Y2/W轴双驱误差告警 | dual-drive error |
| gp13 | 加工过程网络异常 | Network alarm during process |
| gp14/15 | FPGA程序未注册 / 未加载 | FPGA program not registered / not loaded |
| gp16 | 急停告警 | Emergency stop |
| gp17-20 / 21-24 | 硬正限位 / 硬负限位 (X,Y1,Y2,W) | hardware +/− limit |
| gp25-28 / 29-32 | 软正限位 / 软负限位 (X,Y1,Y2,W) | software +/− limit |
| gp33-36 | 调高器硬上/硬下/软上/软下限位告警 | Z hard up/down, soft up/down limit |
| gp37 | 调高器伺服输入告警 | Z servo input alarm |
| gp38 | 调高器碰板告警 | cutting head touched sheet |
| gp39 | 调高器编码器异常告警 | Z encoder alarm |
| gp40 | 调高器信号异常变小告警 | FTC signal abnormally small (was "signal wire") |
| gp41 | 调高器跟随误差告警 | FTC follow error |
| gp42 | 调高器电容变化过小告警 | FTC capacitance variation too small |
| gp43 | 调高器信号异常变大告警 | FTC signal abnormally large |
| gp44 | 激光器串口通讯异常 | Laser serial communication alarm |
| gp45 | 调高器未回原，请首先进行调高器回原操作 | FTC not homed (English says "emergency stop" – wrong) |
| gp46-55 | 激光器温度故障 / 故障2 / 故障3 / 功率故障 / 外部interlock / 出光故障 / 内部interlock / 电源板 / 电流板 / 未知故障 | laser temperature / alarm 2 / alarm 3 / power / external interlock / emission / internal interlock / PSU board / current board / unknown |
| gp56 | 冷水机异常 | chiller alarm |
| gp57 | 调高器通讯异常，请检查调高器是否连接就绪 | FTC not connected |
| gp58 | 激光器通讯异常，请检查激光器是否连接就绪 | Laser not connected |
| gp59 | 调高器告警，请检查确认调高器工作状态 | FTC alarm, check state |
| gp92 | 激光器告警，请检查确认激光器工作状态 | Laser alarm |
| gp95 | 电动调焦头通讯异常，请检查是否连接就绪 | Auto-focus not connected |
| gp96 | 网络状态异常告警 | Network state abnormal |
| gp97 | 系统运动轴脉冲当量设置有误，请重新设置 | Axis pulse equivalent wrong |
| gp100-104 | 电动调焦硬正/硬负/软正/软负限位告警, 电机接入告警 | AF hard/soft ± limit, motor connection |
| gp105-110, gp115 | 电动调焦告警5-10, 15 | AF alarm 5-10, 15 (reserved bits) |
| gp111 | 电动调焦未回原 | AF not homed |
| gp112-114 | 电动调焦低气压 / 高气压 / 高温告警 | AF low/high gas pressure, high temperature |
| gp121 | 激光器到期被锁定，请解锁激光器 | Laser locked (licence expiry) |
| gp141 | 调高器FPGA程序未加载 | ZF FPGA not loaded |
| gp200 | 扩展卡通讯异常，请检查是否连接就绪 | Extended card not connected |
| gp201-206 | 第四轴硬负/硬正/软负/软正限位, 电机告警, 急停告警 | 4th axis limits, servo, E-stop |
| gp207-216 | 第四轴告警7-16 | extended-card axis alarm 7-16 (reserved) |
| gp217 | 调高器坐标异常告警 | ZF axis value warning |
| gp218 | 调高器信号异常变零告警 | ZF signal is zero |
| gp219 / mf800 | 计划加工件数已完成 | plan count finished |
| gp220 | 电动调焦头未回原 | AF not homed |
| gp1000 | 加工超出范围 | processing graphics out of range |
| EtherCATAxisErrorInfo_0-5 | %s硬正限位 / 硬负限位 / 软正限位 / 软负限位 / 伺服输入 / 双驱告警 | per bus-axis alarms (formatted with axis name) |
| EtherCATErrorInfo_1_25 / 1_26 / 1_30 | 总线故障 / 输出保护 / 急停告警 | Bus fault / output fault / E-stop (word 1 bits 25, 26, 30) |
| EtherCATErrorInfo_2_00-2_05 | 非法命令 / 插补数据内容长度异常 / 轴控命令执行异常 / FTC命令执行异常 / PLC命令执行异常 / FIFO饥饿 | word 2: illegal command, interpolation data length, axis command, FTC command, PLC command, FIFO starvation |
| EtherCATErrorInfo_3_01 | 未扫描到从站信息,请检查连接链路,并重启硬件! | No slave scanned |
| EtherCATAxisEnableError(_2) | %s功能轴配置冲突 / 轴功能配置冲突,请重新选择! | axis function conflict |
| eNewLang20 | 未扫描到序号为%s的节点,轴配置不可用! | node %s not scanned |
| eNewLang100 | 检测到硬件参数发生变化,需要进行硬件重启 | hardware restart required |
| hp100-104 | 被重复配置; 第四轴已被配置为电动调焦头/交换平台/自动卷料/旋转平台; 如需配置第四轴，请先联系厂家 | 4th-axis configuration conflicts; contact manufacturer |
| pdAFDA_Alarm | 调焦报警 | focus alarm (DI) |
| RTC_RO_11 / RTC_RO_27 | 报警信息1 / 2 | smart-head alarm words (bits undocumented) |

### 6.2 Process / operation messages (`mp*`, `mf*`, `pe*`, `ss*`)

| ID | Chinese → English gloss |
|---|---|
| mp18/19/21, mf167-169 | abnormal termination detected, recover last state? / recovered, will home |
| mp74/76/78 | data empty: cannot start / simulate / frame |
| mp84 | path length or time exceeds system range |
| mp85 | PC memory insufficient, interpolation error |
| mp87 | graphics order may be wrong, re-sort? |
| mp89, mp132 | mark point / work origin not initialised |
| mp112/113 | positioning move confirm / input error |
| mp120 | graphics out of range, continue? |
| mp122, mp145, mf231 | breakpoint invalid (graphics changed / auto-recorded) – set manually |
| mp124 | system in E-stop, press OK to release |
| mp128 | movement not ended |
| mp133, mp144, mp151 | system / Z follower / AF not homed – will home automatically |
| mp165 | PC available memory too low |
| mp168-170 | exchange platform length wrong / extended card disconnected / platform not in place |
| mp301 | not idle, continue roll? |
| mp302 | hardware not connected, report may be inaccurate |
| mp400-403 | gantry dual-servo calibration OK/failed; correction homing OK/failed |
| pe6-8 | exchange platform not homed (must / continue?), rotate platform not homed |
| ss1-3 | hardware not connected, cannot move; moving, cannot execute; pulse input must be integer |
| ss24 | ensure clamp released before jog |
| mf0/5 | controller connect success / failed |
| mf151-155 | wrong path; invalid file; >100 MB; wrong type |
| mf160 | out of memory |
| mf161 | graphics do not satisfy fly-cut condition |
| mf163, mf1000 | system processing, stop first / cannot exit |
| mf165/166, zf40 | reconnect success / failed |
| mf172, mf622 | controller / extended card not connected |
| mf175-179, mf241, mf244, mf232, mf432, mf701 | firmware upgrade prompts, file errors, version too old auto-upgrade |
| mf221 | Z homing timeout |
| mf223, mf435 | driver / dual-drive alarm cleared, home now? |
| mf224, mf246 | FTC reconnect failed / disconnected, homing stopped |
| mf227, mf230, mp150 | hardware restarting / done, restart software |
| mf236-240, mf441-443 | parameter export/import to hardware success/failure; system parameter read failed / recovered |
| mf448, mf449, hp54, hp60 | pulse equivalent not set – axes disabled; home before error measurement; axis index duplicated |
| mf490-495 | laser unlock success / code verify error / length error / too many attempts – locked / %d attempts left / failed |
| mf600, mf1001, mf220 | unsupported board type, controller type changed, Windows XP unsupported |
| mf900-903 | Y not dual-drive – cannot calibrate / correct-home; confirmations |
| A240903_1 | cannot modify graphics while processing |
| A250106_0, A250107_0, A250105_1, A250418_3/4, A250419_1/2, A250421_0 | layer %s gas undefined; restore layer params?; parameter import failed; technology params wrong / library empty; process file not found; file exists overwrite?; task laser type mismatch |
| A250213_2, A250220_3, A250212_10 | graphic <50×50 mm cannot auto-match sheet; start corner must be a box corner; wrong sheet size → head-crash risk |
| A250606_0/1, A241220_0-3 | file error; >500 MB; offline file wrong / generated–upload? / upload failed / success |
| A250607_0-3 | switched to / currently fiber or CO2 laser – check settings before processing |
| A240613_1-3 | pendant disconnected / low battery / poor signal |
| newLang24 | leaving page stops burn-in test |
| jm2/3 | pendant pairing success / invalid signal |
| is1, af23, hp37/38, zf37-39, zf50 | parameters written – hardware will restart / restart software / reconnect |
| hp8, hp36, hp51, lp1, lp5, lp15, mf148, MsgBox_NestDataChange_IsSave | unsaved changes prompts; save/load errors |
| hp61-63 | verticality compensation enabled %0.2f° / < 0.02° / not a parallelogram |
| lp16-19 | layer height constraint violations |
| pp0-3 | value out of range (%0.2f–%0.2f / %d–%d); parameter file save error → backup; backup read error |
| gpa8, gpa11 | calibration point count error; will clear existing data |
| ec18-25, ss16-18 | PWM freq 1-50000 Hz, duty 0-100, step 0-10000 mm, speed 1-200 mm/s, DA/PWM input errors |
| cp18, cp30-33 | backlash input error; invalid path / empty / broken / unsupported file |
| mv25-32 | file diagnostics (see §4.2) |
| zf26, zf29, zf49, zf58 | head calibration confirm; ≥20 mm servo travel?; not homed – cannot calibrate; factory reset confirm |
| ab7, ab10-12, ls3, ls11-13, cd0, pi0 | hardware ID read failure; invalid code; unauthorized hardware; wrong password; save failed; format errors |
| dogState_*, dogActiveReslut1-11, mp1-4, mf496 | dongle states (see §4.19) |
| mf497-506, mf520-524 | nesting: unclosed contours, sheet too small, segment too short, malloc failed, wrong output, kernel error, process/system out of memory, too much fill data, no part, part too large, topology violation, param error, >50 part kinds, DLL kernel error |
| da1/2/4/6, db1/6-9 | craft name empty / overwrite? / update-insert-delete failed / read error / reset error / file damaged / DB connect failed |

### 6.3 Nesting kernel error codes (`SRC/排样内核错误代码.txt`, UTF-8; EVIDENCE)

| Code | Chinese | English |
|---|---|---|
| 0 | 正常 | OK |
| 1 | 没找到加密狗 | dongle not found |
| 2 | 打不开临时文件 | cannot open temp file |
| 3 | 排样出现不封闭轮廓 | unclosed contour in nesting |
| 4 | 打不开排样临时文件 | cannot open nesting temp file |
| 5 | 打不开板材临时文件 | cannot open sheet temp file |
| 6 | 打不开py | cannot open .py |
| 7 | 找不到grp | .grp not found |
| 8 | 离散失败 | discretisation failed |
| 9 | 板材尺寸太小 | sheet too small |
| 10 / 11 / 12 | geo失败 / cut失败 / nestout失败 | geo / cut / nestout step failed |
| 13 | 导入外轮廓不封闭 | imported outer contour not closed |
| 14 | 导入存在重复的点 | imported duplicate points |
| 15 | 线段太短 | segment too short |
| 100 | 内存分配失败 | memory allocation failed |

These map 1:1 onto `mf496-506` (INFERENCE, high) and reveal the nesting kernel works through temp files, a
Python script and `.grp` files (`Dxf2Grp.dll`, `dat/tmpnst1/`).

---

## 7. Hardware revealed by the strings

| Category | Evidence | Item |
|---|---|---|
| Vendor | `ab6` (orig) 版权所有 2014起 奥森迪科 / AU3TECH; `ab2` SC激光切割系统; `mf459` SC System; `mf600` SC1000 V1.0.107 SP2; Readme "SC2000" | Original developer 奥森迪科 (Aosendike, "AU3TECH"); product line SC1000 → SC2000 |
| Motion controllers | `pd1732/1733` MCC3721H / MCC3721NA; `RegName89` MCC3721硬件版本; `mf143` "MCC"; `Update/MCC100_V201.52.mcf`; `mp150` "MC Controller" | MCC3721H/NA (FPGA-based, "FPGA program not loaded"), MCC100 (this machine); Modbus port 502 in `ipAdd.ini` |
| Bus generation | `EtherAxisInfos_*`, `SystemRWRegName_11-27` (16 bus axes + pulse axis), `pd640_EtherCAT`, `pd640_XY2` "XY V2_0", `EtherCATErrorInfo_*`, `A241009_1` | EtherCAT master controller variant (not the one fitted here – INFERENCE medium: `ipAdd.ini` has no EtherCAT keys and `EC3710IP` refers to the extended card) |
| Height controllers (FTC/ZF) | `pd171-173, pd1731, pd1733_1` FTC10 (Ethernet 10.1.1.169:502), FTC61 (IO / PC serial / MCC serial), onboard FTC; `pd562/563` 联品 "Super", 天星 "TXStar" servo types; `zf67` follow lib version; `ipAdd.ini OnBZFMinHardwareVer=311, NCZFMinHardwareVer=325` | Capacitive height sensor with its own FPGA (`gp141`), PID params exposed |
| Auto-focus head | `af19` "ECH Property", `pd578-583` micro-step 800-6400, `pd795` LED light type, `af29` LED RGB, `AFMinHardwareVer=133`, `AFPort=888/666` | Stepper-driven electric focus head with RGB LED |
| Smart cutting head | `RTC_RO_33` 切割头名称, lens temps, scatter, humidity | Head with sensor telemetry; "RTC" family (INFERENCE low: could be Raytools-style head) |
| Lasers | `pd47-51` Raycus, IPG, Semiconductor, MaxPhotonics (创鑫), nLight, GZ (国志), Others; `A241024_*` fiber / CO2 / blue; `A241025_4` glass tube; `pd180` CypCut; `LaserIP 10.1.1.170:10001` | Laser vendor list; CO2 glass-tube support is new (2024-10) |
| Servo drives | `pd380-383_1` Panasonic A5, Delta B2, Yaskawa, Inovance IS, Leadshine L7P/L7RS | |
| Pendant | `RegName40-46, 90-96` 无线手柄 "LCR", USB adapter addresses & match codes; `jm0-3` pair by pressing Left+Right; `pd292-295` remoter type & 3 match codes; `A240613_*` battery/signal/sleep; `PHBX.dll` (`XGetDevRssi`) | Wireless handheld pendant with USB dongle receiver |
| Extended card | `ec27` "EBH Property", `EC3710IP=10.1.1.170:502`, `pd960-979` | "EC3710" 4th-axis/PWM extension board |
| Gas | proportional valves via DA (0-10 V), Air/O2/N2 low/high | |
| Metrology | `cp5` Renishaw formats `.rtl .ren .pos`; `mf702` ball-bar; `mf180` interferometer | |
| Mechanics | dual-drive Y gantry (Y1/Y2, `mf17_1`, `mf21_1`), 4th/W axis, exchange pallets A/B, VFD roll feeder, rotary stations, lifting platform, nozzle-cleaning brush, dust zones, lubrication | |
| Dongle | `CHidUsb`, `dog*` strings, hardware ID + vendor ID checks, on-board clock | USB HID key (VID 3689 / PID 8762 per brief) |
| PC side | `mf220` Win7+ only; `pd862` portrait/landscape; touch keyboards `tk*` | Touch-screen HMI variant ("Skin=1") |

Network map from `File/ipAdd.ini` (EVIDENCE): Card 10.1.1.168:502; ZF 10.1.1.169:502; OnB-ZF 10.1.1.168:999;
OnB-Laser 10.1.1.168:888; Laser 10.1.1.170:10001; AF 10.1.1.168:888 / AdvAF :666; EC 10.1.1.168:888 / AdvEC :666;
EC3710 10.1.1.170:502; Monitor 47.104.17.21:9001.

---

## 8. Dialog / window count estimate

EVIDENCE:
* PE resources of `MainApp.exe`: **7** `RT_DIALOG` (IDs 319, 331, 393, 443, 444, 448, 459), 2 `RT_MENU`, 12 string
  tables, 408 bitmaps, 71 PNG, 1 accelerator. `Dxf2Grp.dll` 3 dialogs, `AutoNest.dll` 1, `BCGCBPRO2210u100.dll` 32
  (library), `mfc100u.dll` 27 (library). The six `Module/*.dll` have no resources at all.
* MFC/BCG RTTI in `MainApp.exe`: 171 class names, of which **69 are application-specific windows**:
  `BatchCutSetDlg, CAboutDlg, CAdjustPtDlg, CAutoFocusView, CBasicNestPanel, CCodeInputDlg, CCompensateDlg,
  CControlPanel, CCraftPropDlg, CDockPtDlg, CDOSelectDlg, CDualServoCheckDlg, CECStatusView, CEdgeSeekDlg,
  CErrorPanel, CGlyphPropPanel, CGraphParaDlg, CGraphPropView, CGraphScaleDlg, CHardwarePropView,
  CHardwareUpdateDlg, CHelpDlg, CInterferePanel, CIPSetDlg, CircleCenterDlg, CJoystickMatchDlg, CLaserCodeDlg,
  CLaserCurveView, CLayerPropDlg, CLayerPropPanel, CMainFrame, CMainView, CManuPanel, CO2LayerPropDlg, COpenGLView,
  CPanelBar, CPartNestPannel, CPlatformExchangeDlg, CProcessParaDlg, CPropPanel, CPulseEqTouchKeyboardDlgProp,
  CPwdInputDlg, CReslutNestPannel, CRollSheetMoveDlg, CSCanFlyCutDlg, CSCFileDialog, CSCPreviewView,
  CSectionDOView, CSelectTechnologyDlg, CSheetNestPannel, CSimplePlcDlg, CSplashDlg, CSysStatusDlg,
  CSystemStatusView, CTextInputDlg, CTouchKeyboardDlgProp, CZFStatusView, EdgeSeekAuto, EmbeddedDlg,
  LaserTestView, LiftingPlatformDlg, LoginDlg, MarkPointView, RollSheetSetDlg, StressTestView, CNumTouchKeyboard,
  CPasswordKeyboard, CTouchKeyboard` plus custom controls (`CAdjGridCtrl`, `CAFDAAdjGridCtrl`, `CNestGridCtrl`,
  `CGridCtrlEx`, `CErrorReportCtrl`, `CTabCtrlEx`, `CTabbedView`, LED statics).

INFERENCE (high): dialogs are almost all created programmatically from BCG property lists (`CBCGPPropList`,
`CBCGPGridCtrl`, `CBCGPDialog`) rather than from resource templates – which is why only 7 templates exist. The
language IDs support this: parameter pages are pure `Group.Item` label lists.

Estimate of distinct user-facing windows: **≈ 70 named windows** (69 RTTI classes) + **≈ 15-20 property-grid pages**
hosted inside `CHardwarePropView`/`CPropPanel` (hardware tabs `hp2-7, hp16-18, hp25, hp29-35, hp55/56, hp70-73,
hp80/81` = 20 tab labels; layer dialog 12 groups; run-params) + ribbon (2 menus) + ~10 modal message-box flows ⇒
**roughly 90-110 screens** to reproduce, of which ~60 are simple forms and ~10 are complex (main OpenGL view,
control panel, layer dialog, hardware property view, nest panels, FTC/AF/EC status views, PLC editor, edge-seek
wizard).

---

## 9. Open questions

1. **Alarm bit mapping**: are `gp1…gp59` indexed by bit position of `RegName5`/`RegName31` (and `RORegName_7/8`
   "报警状态_1/2") as the numbering suggests? Needs confirmation from the alarm-decode routine in `MainApp.exe`
   (next analyst: search for the `gp%d` format string or a table of 60 IDs).
2. **Which of the two controller generations is in use here** – `RegName*` (MCC3721/MCC100 FIFO protocol over
   TCP 502) or the EtherCAT `*RegName_` family? `ipAdd.ini` and `Update/MCC100_*.mcf` point to the former; the
   EtherCAT strings may be dead code for this machine.
3. **`RTC_*` head**: which cutting head model/protocol (serial? via FTC?) – no IP entry exists for it.
4. **FTC type actually fitted**: `ZFIP=10.1.1.169` (FTC10 network) vs `OnBZFIP=10.1.1.168:999` (onboard) – needs the
   `File/BkHardPara.xml` value of `pd248/259` "总体.控制方式".
5. **Laser control type here**: `pd258` laser type and `pd259` control type values (SC MCC / SC PC / CypCut);
   whether the "CypCut" option means CypCut-compatible laser protocol or PWM/DA emulation.
6. **Duplicate IDs** (`gp100`, `pd1001`, `A241224_0`, `A250616_0`): which occurrence the loader uses.
7. Meaning of `SBT`/`SLED` prefixes (side-button / status-LED?) and whether the touch skin (`Skin=1`) uses them
   exclusively.
8. `pd180` "CypCut", `pd178/179` "SC板载/SC电脑" – confirm these are laser-control options and not FTC options.
9. Report generator `Report/report.exe` (45 MB, own `lang.txt` "lang==0"): separate binary, likely .NET/Qt – out of
   scope here but needed for §4.17 parity.
10. The `Ayymmdd_n` IDs are missing from every secondary language file – is there a newer translation set
    upstream, or do those UIs really show raw IDs?

---

## 10. Implications for the Linux port

**Must be replicated (behaviour visible to operators):**

* The **string-ID indirection** itself: 3 427 IDs, `ID#zh#en` master + `ID#text` per language, fallback to the raw
  ID when missing, per-language UI font (`0_SongFont/0_FontSize`), language index table from `lang.ini`. A port
  can reuse the existing files verbatim (UTF-16LE → convert once to UTF-8; keep the IDs as translation keys) – this
  is the cheapest way to get 11 languages for free. Fix the known mistranslations listed in §1.4 in the English
  column only.
* The **`Group.Item` parameter-label convention** drives the property-grid layout (group header = text before the
  dot). ~200 groups (§4 counts) must render as collapsible groups; enum values are separate IDs (`pd297-300`,
  `pd351-358` …) referenced by index, so the enum → ID tables must be recovered from `ParaModule.dll`/`BkHardPara.xml`
  (another analyst's task).
* Feature surface (§4): CAD editing set, DXF/CHF/G-code/PLT import, layer/craft system with 1-5 level pierce, lead
  /micro-joint/chamfer/cool-point/compensation tooling, sort strategies, fly-cut & scan engraving, nesting, control
  panel with breakpoint/loop/marks/work coordinates, edge-seek (3 methods), FTC/AF/EC device views, PLC flow
  editor, gas calibration, reports, alarms with the full message table in §6.
* Hardware protocols implied by the strings (Modbus-ish register maps `RegName*`, FTC register map `ZFRead*`,
  AF/EC on ports 888/666/999) – these are the interfaces the Linux daemon must speak; the language file gives the
  register *names* and ordering that the protocol analyst can align with packet captures.

**Can be replaced by existing Linux / open-source components:**

| SC2000 piece | Replacement candidate |
|---|---|
| DXF import (`Dxf2Grp.dll`, `DxfParseDllvc100.dll`) | libdxfrw / ezdxf / dxflib |
| PLT (HPGL), G-code import | own small parser; existing HPGL parsers (e.g. `hpgl` Python, Inkscape's) |
| Spline analysis (`splineAnalyerVc100.dll`), circle fitting (`CircleFitDLL.dll`) | tinyspline / GEOS / Clipper2 for offsets & kerf compensation / CGAL |
| Nesting (`AutoNest.dll`, `SmartNest.dll`, python + grp temp files) | libnest2d / SVGnest / Deepnest (already Python/JS-friendly, matches the "打不开py" hint) |
| BCGControlBar ribbon + property grids | Qt (QtWidgets property browser / KDDockWidgets) or Dear ImGui for the touch skin |
| OpenGL canvas (`COpenGLView`, `CGlFont3D`, `CWglFontBitmap`) | Qt OpenGL / QGraphicsView or wgpu; FreeType for glyph outlines (`mf55` text → curves) |
| Text / fonts (宋体, 微软雅黑) | fontconfig + FreeType; Noto CJK |
| Interferometer/pitch compensation (`cp*`) | LinuxCNC-style comp tables; Renishaw file parsers are trivial CSV/text |
| Craft database (`db9` "database connect failed") | SQLite |
| Remote monitor (47.104.17.21:9001) | optional; MQTT or plain TCP client |
| Dongle / activation (`dog*`, `ab*`, `ls*`) | **not to be replicated**: licensing of the proprietary vendor; the port can drop it (open question on whether the MCC100 itself enforces the licence – `ls3` "hardware connect failed, data save failed" implies the activation code is *written to the controller*, so the controller may refuse motion without it). |
| Report generator (`report.exe`) | any HTML/PDF template engine over the CSV rows |
| Touch keyboards (`tk*`) | Qt virtual keyboard |
| Windows-only bits (`mf220`, COM ports `pd183-191`, `IPSet.exe`) | `/dev/ttyUSB*`, NetworkManager |

**Design consequences:** keep the ID namespace as the i18n key space; model parameters as a flat key→value store
with group derived from the label; implement the alarm table as `ID → (source, bit, text)` so §6 can be loaded
directly; treat `SBT/SLED` as the touch-skin widget set (a second front-end over the same command API).
