# 99 — Completeness critique: what is still unknown, what was never looked at, what is "confirmed" on inference

Role: completeness critic. Inputs: `00-overview.md`, `PORT-PLAN.md`, all of `01`–`10`, and a fresh pass over every file under `SRC = /home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52`, its **parent directory** and the Wine prefix. Conventions as in `00`: **EVIDENCE** = observed in a file/binary; **INFERENCE** = interpretation with a confidence level.

Method for the coverage matrix: for every file in `SRC` (`find -type f`, 116 files) I grepped the basename across `docs/analysis/*.md` + `PORT-PLAN.md`, then read the citing passage to judge *depth* (mentioned vs. actually analysed). Additional probes were run with `objdump`, `strings`, `xxd`, `python3` and a small string-resolver (`scratchpad/xs.py`) over the existing disassemblies; every new observation below cites its evidence.

---

## 1. Source coverage matrix

### 1.1 Files inside `SRC`

| Path | Cited by | Depth of analysis | Verdict |
|---|---|---|---|
| `MainApp.exe` | all | imports, RTTI, resources, descriptor table, start-up, licence, HTTP, HID (07); parameter descriptors (01/02); planner wrapper *not* traced (05 O12) | analysed; large untraced areas (see §4) |
| `Module/CADModule.dll` | 03, 05, 09 | `.chf` reader/writer, planner classes, nesting bridge, DXF/PLT/G-code, debug dumps | analysed deeply |
| `Module/NCModule.dll` | 04, 08 | framing, CRC, transaction engine, register windows, 0x65/0x66/0x67 builders, HALs | analysed; **FIFO item builders at `0x1003ee10/0x1003fa0a/0x1003fb55/0x10043c23` (push `0xbb8`/`0xbb9`) and the licence thunks at `0x1002c510–0x1002c564` are not cited by any doc** (see §2.4) |
| `Module/ParaModule.dll` | 01, 07 | XML store, "only an entity table" | adequate |
| `Module/LangModule.dll` | 06, 07 | class map only; **duplicate-ID policy never read from code** (06 O6) | shallow |
| `Module/LogModule.dll` | 07, 08 | class map, log line format | adequate |
| `Module/ControlModule.dll` | 07 | class map only | adequate (UI widgets, droppable) |
| `MotionCtrl.dll` | 05, 09 | fully disassembled (planner) | analysed |
| `PHBX.dll` | 01, 04, 07 | exports, imports, version info, VID check `0x10ce` | **`XGetInput` (`0x100014a0`, ~200 bytes, returns 0x64/0x66/0x68/0x69 error codes) and the HID feature-report layout never disassembled** — whole DLL is 24 k asm lines |
| `DxfParseDllvc100.dll`, `splineAnalyerVc100.dll`, `CircleFitDLL.dll`, `AutoNest.dll`, `SmartNest.dll`, `Dxf2Grp.dll` | 09 | entity coverage, vtable arity, NestLib API, dongle | analysed |
| `BCGCBPRO2210u100.dll`, `BCGPStyle*.dll`, `mfc100u.dll`, `msvcp100/msvcr100.dll`, `vcredist_x86.exe` | 07 (mentions) | none needed | irrelevant runtime |
| `File/BkHardPara.xml`, `BkManuPara.xml`, `SecondBkManuPara.xml`, `BkLayerPara.xml` | 01, 02 | every attribute inventoried with descriptors | analysed |
| `File/ipAdd.ini`, `softPara.ini`, `IPSet.exe`, `pingMC.bat`, `pingZF.bat` | 01, 04, 07 | full key inventory; IPSet = netsh wrapper | analysed |
| `File/autosave.chf`, `Temp/tempGraph.chf`, `Graph/Work1|2/{1,2,3}.chf` | 03 | parsed by `tools/chf_parse.py`; **samples contain only glyph types 2/4/6 (segment, circle, bulge polyline)** — arc/ellipse/spline/text/scan readers are disassembly-only | analysed, weak sample coverage |
| `File/AutosaveParam1/2.ini`, `Temp/AutosaveParam*.ini`, `Temp/tempIsBreak.ini`, `ManuContour.dat`, `Temp/ManuContour.dat`, `PithCompensate.pcf`, `ProcessesStatistic.txt` | 01, 08 | formats decoded; producers in MainApp *not located* (03 O8) | adequate |
| `File/logo_1.bmp` | 07 (name only) | never opened. **EVIDENCE (this pass):** 200×200×1-bit bitmap of a hand-drawn blob; MainApp loads `\File\logo.bmp` (not `logo_1`) next to `res\splash.bmp` (UTF-16 string block around `strings -e l` line 20550). Purpose unknown (UI image or scan-engrave sample) | unexamined, low value |
| `File/SCFile.ico`, `Report/icon.ico` | 07 | icons | irrelevant |
| `File/PM/` (empty) | 01 §0 (as a path), 07 §12.3 | **never explained.** EVIDENCE: MainApp UTF-16 `\File\PM\p0.pmf` at file 0x3e4db8/0x4461b0 (VA 0x7e5fb8/0x8473b0); the second is pushed at `0x5a6605` inside the function `0x5a6400–0x5a6900`, which sits in the same string block as `slp0..slp19` (`lang.txt`: 向前插入 Insert Before, 延时【%d】毫秒 Delay %d ms, 开启循环 Start Loop, 开始加工 Start Manu, 等待DI端口 Wait DI, 打开DO端口 Open DO, 开始平台交换 Start Change Platform …) and `CSimplePlcDlg` (07 §4.2). INFERENCE (high): **`.pmf` = the "simple PLC" process-flow file of the flow editor**; `p0` = flow #0. Format unknown. | **never analysed** |
| `Help/` (empty) | 07 §12.3 (name) | **never explained.** EVIDENCE: ASCII `\Help\calib.csv` (file 0x46ed78, VA 0x86ff78) pushed at `0x5fdbc3` in function `0x5fd900–0x5fde00` that also pushes `zf58` (该操作将会重置调高器所有硬件参数 "This action will reset the FTC parameters"), `zf59` (恢复出厂设定成功 "reset success"), writes rows with `"%d, %d"` / `"%d, %d, %d, %.3f"` (0x46ed8c/0x46ee08) and launches `explorer.exe` on `\Help\`. INFERENCE (high): **`Help/` is the export folder of the on-board height-follower (FTC) calibration table** (capacitance sample → height curve). Row meaning unknown. | **never analysed** — relevant to O9 |
| `dat/tmpnst1/` (empty) | 07, 09 | nesting temp dir | adequate |
| `Lang/lang.txt`, `lang.txt.orig`, `lang.ini`, `Readme.txt`, `语言修改记录.txt`, `LanFormatEStr.txt` | 06 | exhaustively mined | analysed |
| `Lang/French|German|Italian|Polish|Portuguese|Russian|Spanish|Turkdili|Vietnamese|Vietnamese_LE.txt` | 06 §1.5 (table) | statistics/defects only; contents not needed by the port beyond re-use | adequate |
| `Log/2025-06-25 … 2025-07-18.log` (15 files), `2026-09-11.log`, `Code.txt`, `VelDecc.txt` | 08 | every line classified (1 794 + 1 252 lines) | analysed |
| `Report/TotalReport.txt`, `LogReport.txt`, `report.txt`, `lang.txt` | 08 | row grammar, statistics | analysed |
| `Report/report.exe` | 07 §11 | stub strings, Enigma container identified | adequate (droppable) |
| `Report/111.chf.jpg`, `222.chf.jpg`, `rpt.chf.jpg` | 03 §2.1, 08 | identified as 800×800 thumbnails | adequate |
| `Report/未命名-1.jpg`, `Untitled-1.jpg` | 08 §1 (names) | never opened | unexamined, low value |
| `Report/广告.jpg` = `1111.jpg` (md5 `709723…`, 838×709, Exif+XMP from Adobe) | 08 §1: "vendor banner" | **wrong.** EVIDENCE (viewed): a cartoon bear "OK" sticker, i.e. a user picture, not vendor material. `TotalReport.txt` has one job named `1111.chf` (2024-11-05, 08 §8) → INFERENCE (medium): a picture the operator tried to engrave/trace. **No `*.jpg/*.bmp` file filter exists in MainApp** (`strings -e l` finds none), so how a JPEG became a job is unexplained → possible undocumented bitmap path (see §5 Q-new-6) | misdescribed |
| `res/splash.bmp` | 07 (name) | never opened. EVIDENCE (viewed after BMP→PNG): "**Mlaser Creative Space — Bring the extraordinary to life**", "M LASER" logo; no vendor company name | branding only |
| `Update/MCC100_V201.52.mcf` | 04 §5 | header + entropy; opaque | adequate for a non-goal |
| `Dump/20260911-175115.dmp` | 07 §12.1 | parsed with a custom minidump reader | analysed |
| root `*.txt` dumps (`segments.txt`, `closePwmPosRatios.txt`, `linkFlyLine_pathGlys.txt`, `after_*`, `before_*`, `setDataWithoutReFit_segs.txt`, `p_micoLinkLenPos.txt`, `calcGraphCtInterpPt.txt`) | 05, 09 | decoded to the mm | analysed |
| `JumpAddTime.txt`, `ClearFile.bat`, `排样内核错误代码.txt` | 01, 07, 06/09 | read and translated | analysed |

### 1.2 Sources **outside** `SRC` that belong to the same package delivery (parent directory `/home/karstein/Documents/CF1390-250715-1084-0973/`)

| Path | Cited by | Status |
|---|---|---|
| `1390backup.xml` (62 KB) | 02 | analysed (layer backup) |
| `Cutting parameters/{1200W,1200W_air,CO2}/**/*.xml` (53 presets), `Cutting parameters.xlsx`, `Cleaning and quenching/*.xml` (2) | 02 §6 | analysed |
| `pwmCompensation.txt`, `Co2_pwmCompensation.txt` | 02 §3.8 (one mention) | **content never quoted.** EVIDENCE: CRLF, `speed,mm` pairs — fibre `100,0.364 / 200,0.56 / 300,0.8 / 400,1.02 / 500,1.3 / 600,1.52`; CO2 `100,0.18 / 200,0.27 / 300,0.48 / 400,0.58 / 500,0.74 / 600,0.86`. Same shape as `GRP.FiberScanFlyCompensateStr="100#0.35,…"` (02) → INFERENCE (high): **speed (mm/s) → PWM switch-point lead (mm) tables for scan/fly cutting**, the vendor's measured values for this machine; the port needs them as golden data for `pwm_schedule` |
| **`Pc_Software.zip` (4.2 MB)** | **nobody** | **Never opened. EVIDENCE (this pass, extracted to scratchpad):** `PC_SC_V01.01.000.240715/LaserApplication_SC.exe` = .NET 4.5 WPF app **with its full PDB** (`LaserApplication_SC.pdb`, source tree `E:\work\Laser sw\S\MaxLaserApplication2-NewModbus\…\ControlClass\ModbusDefines.cs`, `ModbusFactory.cs`, `BasicClass\TCP_Helper.cs`, `ZLAN.cs`, `Config_Net_Module.cs`), `NModbus4.dll`, `Newtonsoft.Json.dll`, `NPOI*.dll`, `log4net.dll`; `Images/web.ini` → `http://www.maxphotonics.com`; `File/config.xml` (`IPName1=192.168.0.178`, `MachineModel=2`), `Login.xml` (`HandAddress=25` = Modbus slave id), `MacConfig.bin` (16 bytes), `Parameter/default.xml` (laser parameter set: `MaxCurrent=36`, `LaserSerialNo=MQSC23040998`, PD/temperature/water-flow limits, `AnalogPowerCoefficient=100`, pump-source arrays), `Parameter/Control1.xml` (UTF-16; monitor LEDs with `<Tag>` numbers 0–109: HumidityAlarm 105, Waterflow 15, PumpTemp 1, OpticalTemp 22, CircuitTemp 21, QBHTemp 16, Driver1..4 current 60–63, F1/F2 Interlock 7/26, F1/F2 EMG 103/27, SW EMG 104, PD1/PD2 0/18, pwm_start_err 20, 客户加密到期 "customer encryption expired" 101 …), `Parameter/Chinese.xml`/`English.xml` (UI text), plus `bcx/Laser/Log/ERR_LOG/MQCSCFAD1519_20250422090456.xls` (NPOI-generated laser error log for serial `MQCSCFAD1519`) and `bcx/user/A.dll`, `E.dll` (INI files `[PW] Administrator=<md5>` / `Engineer=…`, `Operator=…` — password hashes of the laser tool; `禁止删除警告.txt` "the two files under user are the laser host-computer configuration, do not delete"). **Conclusion (EVIDENCE + INFERENCE high): the fibre source on this machine is a Max Photonics (创鑫) MFSC-class 1200 W laser, controlled by the vendor's "MaxLaserApplication" over Modbus (TCP via a ZLAN serial server or RS-232, slave 25).** Mlaser has **no Max Photonics HAL** (NCModule RTTI only `CIPGModbus`, `CRaycusModbus`, `CIPGProtocolAdapter`, `CRaycusProtocolAdapter`; `lang.txt` has no 创鑫/Max option) — consistent with `LaserControlType=3` (IO): the MCC100 drives the laser through discrete enable/modulation/analog lines and the Max tool is only for setup/diagnostics. This closes half of **O10** statically (§4). |
| `Testfile SS1mm 2.0s F+1 N2.dxf` (207 KB) | **nobody** | vendor test drawing: AC1015, `$DWGCODEPAGE ANSI_936` (GBK), 24 364 lines, entities `CIRCLE 1, ARC 4, SPLINE 3, LWPOLYLINE 1` on 15 layers (`0, 9, 43, 66, 71, 74, 79, 100, 140, 302, 330, 340, Defpoints, hp, 图层1`). **The only real-world DXF in the delivery** and the only sample with SPLINE entities → mandatory golden file for the ezdxf importer and for the spline→NURBS path (03 O1, 09 §3.2). Name encodes the preset "SS 1 mm, 2.0 s pierce, focus +1, N2". |
| `TeamViewer_Setup_x64.exe` | — | irrelevant |

### 1.3 Wine prefix

`~/.wine/drive_c/users/karstein/AppData/Local/NexCut/{HardPara.xml, CustomInformation.ini, Log/2026-09-11.log, Technology/{Fiber,CO2}/}` — used by 00 §2. Both `Technology` folders are **empty** (the 55 presets were never imported), so the "library on disk" layout of a populated `%LOCALAPPDATA%\NexCut\Technology` is still unobserved (02 O7).

---

## 2. New evidence produced by this pass (not in 01–10)

### 2.1 `Pc_Software.zip` — see §1.2. Direct consequences
* PORT-PLAN §3.4 lists `laser_ipg.py` / `laser_raycus.py`; neither matches the installed source. If the port ever wants Ethernet control of the fibre source (M6), the protocol is **standard Modbus** (NModbus4) with a register map recoverable from `ModbusDefines.cs` IL — no capture needed (§4, task 2).
* `default.xml` `AnalogPowerCoefficient=100`, `OutAnaloyCoeff=105`, `PowerOffsetValue`, `AnalogOffsetValue=13`, `MaxCurrent=36`, `MaxCurrentLimit=40` are the laser-side scaling of the 0–10 V analog input — the missing half of 02 O9 ("how is `CutPeakCurrent %` scaled to DA1"). The MCC100 side (`DA1OutputAdjustVal=3648`) is known; the laser side is now readable.
* Laser alarm inputs the MCC100 sees on DI1 (`DI.LaserAlarm`) correspond to the laser's "MAlarm/EMG/Interlock" outputs (Control1.xml tags 100/103/104/7/26) — useful for the alarm panel text.

### 2.2 `File/PM/p0.pmf` = SimplePLC flow file (EVIDENCE §1.1). PORT-PLAN never mentions the flow editor (06 §4 "PLC flow editor" is in the feature list). Decide in/out of scope; if in, the format must be reverse-engineered from `0x5a6400` (writer/reader) — no sample exists.

### 2.3 `Help/calib.csv` = FTC calibration export (EVIDENCE §1.1). Confirms that the on-board follower's capacitance→height table is readable from the PC, i.e. the ZF register semantics (O9) are partly recoverable **statically** from the function at `0x5fd900` and the NCModule ZF HAL it calls.

### 2.4 NCModule single-word register writers (EVIDENCE: `objdump -d Module/NCModule.dll`, thunks with no direct callers → virtual methods of the VM/HAL class, dispatched through vtable slot `+0x80` of the object returned by `0x10038a10`):

| VA | Writes | Comment |
|---|---|---|
| `0x1002c510` | reg `0x65` ← `[0x65]` | unknown one-word command |
| `0x1002c520` | reg `0x65` ← `[0x66]` | the "single-word `[0x66]` to 0x65" of 04 §3.6/08 §3.3 (rejected with exception 2 in logs) — now located |
| `0x1002c530` | reg **`0x96` (150)** ← `0x15b3` (5555) | 150/151 are the addresses special-cased in `readReg/writeReg` (04 §3.4) |
| `0x1002c550` | reg **`0x96` (150)** ← `0x270f` (9999) | |

INFERENCE (medium): 150 ← 5555 / 9999 is the **"加密数据域访问开关" (encrypted-data-area access switch)** open/close pair of the card licence (`lang.txt` register group names, 07 §6.3a). This is exactly the write the port must never issue; the addresses were unknown to 00 §5 row 15 ("addresses unknown"). Confirm by finding the vtable slot's callers in `CDog` (task 4).

### 2.5 Alarm-ID string tables in MainApp (EVIDENCE): two contiguous UTF-16 blocks `gp59…gp10` at file 0x3c55a0–0x3c57ec (VA 0x7c67a0…, stride 12) and 0x3c87e0… (VA 0x7c99e0…); the first is used by a static initialiser at `0x6a5ca0` that builds global `CString`s at `0x93cf18 + 0x1c·i` (destructors at `0x60a940…`). No absolute xref to those globals exists outside ctor/dtor (checked by dword scan of the image: only 0x60a941/0x6a5cc8/0x7aba0d) → the alarm decoder addresses them **relatively** (base register + index·0x1c) or uses the second block. This makes O15 (alarm bit → id) a tractable static task (task 5).

### 2.6 `[settings+0x4356]` (O18, "forces TCP"): EVIDENCE `mov BYTE PTR [eax+0x4356],1` at `0x49330c` (function `0x4932a0…`, calls the settings singleton `0x5ff1b0` and `0x48f260`) and `…,0` at `0x4b1d69` (function `0x4b1d30…`). No `lang.txt` id is pushed in either window → it is a runtime flag (set by a UI action / connection-state), not an XML parameter. Resolvable statically by reading the two functions (task 9).

### 2.7 `Report/广告.jpg` is not a vendor banner (§1.1) — correct 08 §1.

### 2.8 `pwmCompensation.txt` / `Co2_pwmCompensation.txt` content (§1.2) — golden data for the scan/fly PWM lead tables; not in any doc.

---

## 3. Claims in `00-overview.md` labelled CONFIRMED that rest (partly) on inference

| 00 § | Claim | What the evidence actually supports | Suggested label |
|---|---|---|---|
| §1 Axes | "Pulse/direction **servo** axes" | Only `WritePluse=8000 p/rev` and `Enable4Freq` (encoder ×4) are observed. Servo vs. closed-loop stepper is not evidenced; 8000 p/rev and an encoder feedback register (`AxisRORegName` "encoder position") make servo likely | LIKELY (high) |
| §1 Axes | "20000 mm/s² axis accel, 125 ms accel time" | `MAC.Acceleration=20000` is the *card-side* axis limit; the *planner* uses `MC.*Acc` (5999.99) and `AccTime`. Both true, but juxtaposition suggests one number | fine, clarify |
| §1 Motion controller | "hardware model 224 = universal card with software-selectable laser family" | INFERENCE from the branch at `0x567288–0x5672d5` (221/222/223 force a family, 224 leaves choice). No firmware/vendor document | LIKELY (high) |
| §1 Height follower | "On-board capacitive FTC **inside** the MCC100" | Evidence: option label 板载调高器 "onboard FTC" and endpoint = card IP, port 999. Whether the sensor amplifier is on the card or a separate board bridged by the card is not decidable | LIKELY |
| §1 Pendant | "XHC PHB02 wireless pendant … USB receiver VID 0x10CE" CONFIRMED | The **driver identity** is confirmed. That a receiver is *fitted* is not: the only HID device seen on this PC is 3689:8762 (nesting dongle). `RemoteType=3` alone does not prove hardware | CONFIRMED (software) / OPEN (hardware) |
| §1 Other I/O | "no E-stop configured in software … must be hard-wired" | `DI.EStop=0` is evidence. But register block **5000** has an RW "急停输入端口 e-stop input port" (04 §3.5, `RWRegName_*`), i.e. the e-stop may be configured **on the card** independent of the XML. "Must be hard-wired" is an inference and the safety design in PORT-PLAN §8.2 should read block 5000 before assuming | LIKELY; add task |
| §1 PC | "logs show UTC+8 operation (factory/dealer test profile)" | timestamps + job names; plausible, not proven | INFERENCE |
| §1 Licence | "enforcement PC-side" | labelled LIKELY — correct. But the *new* 2.4 evidence (access-switch writes to reg 150) shows the PC does perform card writes during the licence exchange; whether the card gates motion on that state is still O6 | keep OPEN |
| §4 header | "the card **only** executes a FIFO of pre-interpolated ticks and gates the PWM" | Contradicted by the card-side commands: jog/home/go-to carry (v, a, j) and are planned **by the card** (0x65 sub-cmds 1/3/5, 08 §4.5), block 5000 holds safety decel/jerk, and `SafeStopFactor` is card-side. Correct statement: *job* trajectories are PC-planned; *manual* motion and stops are card-planned. 05's verifier already softened "only" | reword |
| §4 step 4 | "stationary ticks with duty > 0 = pierce/dwell" | INFERENCE from frame content (08 §4.4) | LIKELY |
| §4 step 4 / §5.1 | opcode names `3001 marker, 3002[mode], 103 dwell before laser-on, 109 dwell after laser-off, 2001 laser/PWM config, 118 contour bookkeeping` | All INFERENCE from position in the stream (08 §4.4, O4); none traced to the emitting code (NCModule `0x1003ee10…0x10044fb4`, CADModule) | LIKELY (medium); static task 1 |
| §5 row 1 | "exceptions `2 = not ready after reset, 3 = busy/refused`" | 04 §3.3 verifier: INFERENCE medium, Modbus names are convention only | LIKELY (medium) |
| §5 row 1 | "Retry: … one-try-then-continue while streaming" | from log statistics (08 §2.3) + code; fine | ok |
| §5 row 2 | "second `CExtModbus` instance = ZF (LIKELY)" | labelled | ok |
| §5.1 | register-block *contents* (e.g. 1000: "DI, DO, alarm words 1/2, run status …"; 50000: "IP/mask/gateway…"; 50200 per-axis layout) | Addresses/counts = EVIDENCE; **names = INFERENCE from `lang.txt` order**, and 04 §3.5 says the 50000 mapping is *low* confidence (47 names for 26 registers). §7.1 row "Register groups/counts" is fine, but §5.1 presents names without the caveat | add "names LIKELY/low" |
| §5.1 0x65 | "`[102]` re-sync" | §9 #16 says LIKELY; listed as fact in §5.1 | LIKELY |
| §5.1 0x66 | "3000 tick `[(dY<<16|dX), …]`" | 08 §4.4 argues from the raster geometry; 04 §10 still lists "(dx,dy) or (dy,dx)?" as open. The half-order is LIKELY (medium-high), not confirmed | LIKELY |
| §4 stream | "keep ≈1.6 s queued (`MCFifoTime`), throttle on FIFO space margin (reg 1000+16/17)" | from parameter names + `fillFifo` log strings; the flow-control rule itself is untraced (04 §10) | LIKELY |
| §6 `.chf` | "reference parser validated on all 8 samples" | true, but the samples exercise only glyph types 2/4/6 and graph type 8; the other layouts are disassembly-only | keep; note coverage |
| §1 USB "USBKey" | "gates nothing else" | Absence of the VID/PID constant in MainApp/PHBX is strong; AutoNest.dll is loaded by CADModule at start-up (09 §2) — if `Nest_CheckLock` ran at load, nesting-dongle absence could still surface as a start-up message. 09 says it is called from the nesting command only | ok (high) |
| §2 | "MotionCtrl.dll dead file" | exhaustive negative search | ok |
| §7.1 Protocol | "CRC … never checked on receive" | 04 §3.2 line: "The PC decoder never checks the CRC" — evidence in `decode` | ok |

Net: nothing in 00 is *wrong*, but eight CONFIRMED rows should be re-labelled LIKELY, and the "card only executes ticks" framing hides that manual motion is card-planned — which matters for M1 (jog/home are the card's own profiles, so the port cannot "soften" them PC-side).

---

## 4. Open questions: closable **now** by static analysis vs. truly needing the machine

Legend: **S** = static, doable today with the listed commands; **S+L** = static narrows it, live capture confirms; **L** = needs the live machine/bench.

| Q | Question (from 00 §7.2 unless noted) | Class | Exactly how (static part) |
|---|---|---|---|
| O1 | Tick period / unit of opcode-3000 increments; frame-id semantics; `MaxItemPerFrame` | **S+L** | (a) In CADModule find the tick generator: `grep -n 'WritePluse\|SpeedRatio'` is useless (names live in MainApp), so instead locate `calcGraphCtInterpPt` (its dump-file name is an ASCII string in CADModule; xref it) and read whether the per-tick Δ is `round(Δmm · pulses_per_mm)` (a `fmul` by a per-axis double loaded from the hardware-param block) or `Δmm·1000`. (b) In NCModule read the three opcode-3000 packers (`push 0xbb8` at `0x1003ee10`, `0x1003fa0a`, `0x1003fb55`) to see the int16 packing order (which half is written first = X or Y) — `awk '/^1003ed80/,/^1003ef00/' nc.asm`. (c) Frame id: read `fillFifo` `0x10052390` for the counter's reset point (`clearFifo` vs. session). Live: 100 mm move count-check remains the final proof of the period. |
| O2 | Bit meaning of status/alarm words (1000+4…+8), axis status | **S+L** | The PC *must* decode these to light the IO panel and alarm list. (a) NCModule `updateMCStatusRO` `0x1004eff0` copies the 36 words into the VM object; find the field offsets; (b) MainApp reads DI/DO words for the IO page (`RORegName_5/6` labels) — locate the function that pushes `RORegName_5` (UTF-16 string xref) and follow the bit loop; (c) alarm words → `gp1..gp59`: xref the second gp block (VA `0x7c99e0`, `grep -n '0x7c99e0\|0x7c9[9a-f]' mainapp.asm`) and look for `bt`/`test eax,1<<n` or a `shr` loop. Live: only to confirm polarity of a few bits (limit hit, e-stop). |
| O3 | Jog/home argument units, index vs. mask | **S+L** | NCModule sub-cmd 3 builder `0x10032160`: `imul edx,[eax+0xbab0]` and `imul eax,ecx` (lines 39/47 of that function) scale the speed/target by a per-axis field at `+0xbab0` — identify what is stored there (search the writer of `[…+0xbab0]`; if it is `1000` → 0.001 mm units, if it is pulses/mm → pulses). Home builder (sub-cmd 1, `0x10031f10` region per 04) likewise: check whether the second word is built with `shl`/`or` (mask) or stored directly (index). |
| O4 | Opcodes 3001/3002/103/109/2001/118 semantics; DO bits | **S+L** | NCModule: `grep -n -E '0xbb9|0xbba|push +0x67$|push +0x6d$|push +0x76$|0x7d1' nc.asm` — hits at `0x10043c23` (3001), `0x10044fb4` (3001), `0x1002be7e/0x1002be9e/0x1002f522/0x1002fd71/0x10030e27/0x10057244` (103) etc. Read each emitter: the argument it packs (e.g. `LaserOnDelay`, `GC.*` gas delay, `MP.IsEnablePWMPerContour`) names the opcode. Also the `9999,2,mask,value` DO writer at `0x1002e284…0x100300f7` (`mov …,0x270f`) shows whether `mask` is 1-based port or bit. Live: A/B capture confirms. |
| O5 | Blocks 10000/11000/13000/13200; why 10000 stalls | **S+L** | `lang.txt` has `ZFReadOnly*`/`ZFRW*` names — count them (`iconv … | grep -c '^ZFReadOnly'`) and match 18/39; NCModule `updateZFStatus` reader gives the field copy order. 13000/13200 readers: `0x1004eff0`/`0x1004f8e0` — read the size checks and where the words go. Stall cause = live. |
| O6 | Licence registers / RTC / does the card refuse motion | **S+L** | Static part now concrete (§2.4): find all callers of vtable slot `+0x80` values 150/151 in NCModule (`grep -n 'push +0x9[67]$' nc.asm` → `0x1002c53d`, `0x1002c55d`, `0x1003ec63`), read `CDog` (`dogState_*` strings) to list every register touched and the exchange order; in MainApp find where `dogState` gates the Start/Jog buttons (xref `dogActiveReslut*` and the `ls*` ids). If gating is only in MainApp → strong PC-side evidence. Live: run the port without the exchange (M1). |
| O7 | Slots 3/4 roles, which slot the lifting table jogs | **S** | MainApp: buttons `A250410_3/4` created at `0x453007`/`0x45311e` (01 §7) — follow their `ON_COMMAND` handler (message-map entry → handler VA) to the NCModule jog call and read the axis argument. |
| O8 | Limit-switch polarity per pair | **L** (bench) | Static cannot decide which DI the firmware treats as "+"; the XML only names the ports. Bench: press each switch, read DI word (M1 capture C). Partial static: the 59600+ hardware block copy (`writeHardParam2Card`) shows the offset at which `NegativeLimitInput` lands — at least the port can mirror the same offsets. |
| O9 | ZF protocol on port 999; CO2 jobs and ZF | **S+L** | Static: NCModule ZF HAL (`syncReadZFReg`, `updateZFStatus`, second `CExtModbus` at `0x100aba10`) → register list and the calib export at MainApp `0x5fd900` (§2.3). Live: capture during a fibre job. |
| O10 | Fibre laser control path; DA1 scaling | **S** (largely) | `LaserControlType=3` = IO: the MCC100 side is DO5 shutter/enable, DO6 red pointer, DA1 analog, PWM. The **laser side is now readable** from `Pc_Software.zip` (§1.2): decode `ModbusDefines.cs`/`ModbusFactory.cs` IL (task 2) for the register map, and `Parameter/default.xml` for analog scaling. Only the wiring (which MCC100 DO reaches which laser input) is bench. |
| O11 | `.chf` crafts scalars | **S+L(Wine)** | Static: MainApp setters for `+0x58/+0xf0/+0x104` through the `IGraph` vtable (03 O7); Wine diff is cheaper. |
| O12 | UI → planner P-block mapping; `AccTime`/1000 | **S** | MainApp caller of CADModule wrapper `0x100dab10` (9 doubles): find the import thunk / vtable slot in MainApp, read the 10 loads before the call and map each to a descriptor `g+offset` (01 §0.2 gives offsets for `MC.AccTime` etc.). Grep: `grep -n 'fdiv\|fmul' mainapp.asm` around that call for a `1000.0` constant. |
| O13 | `ManuType` folding | **S+L(Wine)** | dialog save handler `0x53f300–0x540400` (02 O10). |
| O14 | `.enc`/`.aut` layout; `NormalExit`; report row trigger | **S** | MainApp ASCII markers `NEXCUT_CHF_END…` (strings lines 5015–5021) and `TASK_GRAPH/TASK_LAYER`: xref each marker → the writer function → section order and length fields. `NormalExit`: xref `L"NormalExit"` → both writers. `TotalReport` trigger: xref `\Report\TotalReport.txt`. |
| O15 | Alarm-id ↔ bit mapping | **S** | see O2 (c) and §2.5. |
| O16 | PHBX HID report layout | **S** | `objdump -d -M intel PHBX.dll` is 24 k lines; `XGetInput` at `0x100014a0` (RVA 0x14a0), `XOpen` 0x1710, `Xinit` 0x1560, `XSendOutput` 0x1e90, callback setter 0x1010. Read the `HidD_GetFeature`/`ReadFile` buffer parsing (report ids, byte→key mapping). Cross-check with LinuxCNC `xhc-whb04b-6` (same vendor). |
| O17 | `.mcf` cipher | **L/never** | non-goal |
| O18 | `[param+0x4356]` | **S** | read `0x4932a0–0x493340` and `0x4b1d30–0x4b1d90` (§2.6). |
| new | Stop / pause / resume / E-stop commands (PORT-PLAN session F; not an O-number) | **S+L** | MainApp Stop/Pause handlers (`mp124` E-stop id, `mp*` pause ids) → NCModule → register writes (`0x67←3`? `0x65←[…]`?). This is a *safety-critical* gap that M1 needs before any motion. |
| new | Block 5000 e-stop port / safety decel semantics | **S+L** | NCModule `updateMCStatusRW` `0x10051b00` and the writer of block 5000 (search `push 0x1388` = 5000). |

---

## 5. What the port cannot proceed without (blocking), in dependency order

1. **Stop semantics** (new) and **jog/home encodings** (O3) — M1 cannot move an axis safely without knowing how the Windows tool stops one. Static work (§4) gets the vectors; one capture confirms.
2. **Status/alarm/DI word decoding** (O2/O15) — without it the M1 CLI shows raw words; the safety watchdog ("any alarm ≠ 0 → stop") is blind to *which* alarm. Static.
3. **Tick unit/period + item packing order** (O1) and **opcode semantics** (O4) — block M4. Static narrows to one hypothesis; the 100 mm capture proves it.
4. **Licence interaction** (O6) — viability risk R5. Static: list the registers the `CDog` exchange touches so the port's write allow-list is provably disjoint (§2.4 shows 150 ← 5555/9999 exists).
5. **Slot 3/4 roles and Y dual-drive** (O7) — homing all axes (mask 31) with an unknown slot map can move the lifting table. Static for the slot map; dual-drive flag = bench/capture.
6. **Limit polarity** (O8) — bench only; until then home one axis at a time with a hand on the E-stop (PORT-PLAN §8 already says so).
7. Fibre path (O9/O10) — not blocking for CO2 milestones; now largely static thanks to `Pc_Software.zip`.

Items the PORT-PLAN treats as "capture first" that are in fact static-first: O2, O3, O4 (emitters), O6 (addresses), O7, O12, O14, O15, O16, O18, and the laser-side half of O10.

---

## 6. Open questions raised by this critique

1. What is the `.pmf` SimplePLC flow format and is the flow editor in scope? (§2.2)
2. What does `Help/calib.csv` contain row-wise (`%d, %d, %d, %.3f` = sample index, raw capacitance, filtered, height mm?) and is it needed to reproduce the FTC calibration wizard? (§2.3)
3. Which of the two `gp` string blocks (VA 0x7c67a0 / 0x7c99e0) does the live alarm decoder use, and are `gp1..gp9` handled elsewhere (only `gp10..gp59` are in block 1)? (§2.5)
4. Is register 150 the licence access switch and 151 its companion, and does *any* non-licence path write them? (§2.4)
5. Does the card enforce the e-stop configured in block 5000 even when `DI.EStop=0` in the XML? (§3)
6. How did a JPEG (`1111.jpg`) become a job `1111.chf` on 2024-11-05 when MainApp has no image filter — is there an undocumented bitmap tracing/engraving import (e.g. via the "advertising" 广告字 path or the NexCut HTTP server)? (§1.1)
7. The Max Photonics laser serials differ between `default.xml` (`MQSC23040998`) and the error log (`MQCSCFAD1519`): which one is on this machine, and does the machine's laser expose the Modbus/TCP port on the 10.1.1.x LAN (ipAdd `LaserIP=10.1.1.170:10001` vs. the Max default `192.168.0.178`)?
8. Are `pwmCompensation.txt` tables the *source* of `GRP.FiberScanFlyCompensateStr` (imported into the XML) or an independent runtime file read from the package root? (grep MainApp for `pwmCompensation` to settle.)
9. `File/logo.bmp` (loaded by MainApp) vs. shipped `logo_1.bmp`: which feature consumes it?
10. `MacConfig.bin` (16 bytes) in the laser tool — a licence/MAC binding of the laser software; irrelevant unless the port wants to reuse that tool's config.

---

## 7. Implications for the Linux port

**Must be added to the plan / replicated**
* A static-analysis sprint (tasks 1–10 below) *before* the first capture session: most of what PORT-PLAN §6 wants to capture is recoverable from the binaries, and the capture then becomes confirmation rather than discovery — cheaper and safer (fewer runs with a live gantry).
* The register write allow-list (PORT-PLAN §8.2) must explicitly deny **150/151** and `0x65 ← [0x65]/[0x66]` until their meaning is known (§2.4).
* Stop/pause/E-stop command vectors must be first-class in `mcc/commands.py` and verified before any jog (§5 item 1).
* Read block **5000** (e-stop port, safety decel/jerk) at start-up and show it in the safety panel; do not assume "no software e-stop".
* Golden test data to ship: `Testfile SS1mm 2.0s F+1 N2.dxf` (SPLINE/ARC/CIRCLE/LWPOLYLINE, GBK code page), the two `pwmCompensation` tables, the 55 presets, the 8 `.chf` samples.
* Fibre laser (M6): model the Max Photonics IO/analog interface (enable, modulation/PWM, 0–10 V analog with `AnalogPowerCoefficient`, alarm/interlock outputs) rather than IPG/Raycus; optional Modbus/TCP monitor from the decoded `ModbusDefines` map.
* Decide on the SimplePLC flow editor (`.pmf`) — either drop it explicitly (add to non-goals) or schedule its format recovery.

**Can be replaced / dropped (confirmed by this pass)**
* `Report/广告.jpg`, `splash.bmp`, `logo_1.bmp`, icons: no functional content — new branding for the port.
* Max Photonics `LaserApplication_SC.exe` itself (WPF/.NET): not needed; its Modbus map can be served by `pymodbus` in a small diagnostics panel if desired.
* `Pc_Software/bcx/user/A.dll|E.dll` password hashes: never copy into the public repo (privacy/security).

**Tooling note**: the .NET binary can be decompiled on this machine without Mono by `python3 -m venv .venv && .venv/bin/pip install dnfile dncil` (pure Python) — if pip has no network, the `#Strings`/`#US` heaps are already readable with `strings -e l`, and `ldc.i4` constants can be pulled with a 40-line CIL walker over the method bodies whose names come from the PDB.
