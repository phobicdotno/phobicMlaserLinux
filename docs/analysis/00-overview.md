# 00 — System overview: Mlaser v0.0.0.52 ("NexCut") as it exists

Lead-architect synthesis of the nine subsystem reports (`01`–`09`) plus the prior-work note (`10`). Every statement below points at the report that carries the evidence; where two reports disagreed I re-checked the primary files and say which one is right (§9). Package root is `SRC = /home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52` (read-only).

Conventions: **CONFIRMED** = evidence in a file/binary/log re-verified by at least one adversarial pass; **LIKELY** = strong inference; **OPEN** = not determinable statically. Pointers are `doc §section`.

---

## 1. What the machine is

| Item | Fact | Status | Source |
|---|---|---|---|
| Machine class | CF1390-class flat-bed (1300 × 900 mm nominal), soft limits **X 1371 mm / Y 950 mm** | CONFIRMED | 01 §2.2 |
| Model | **Gweike (金威刻 Jinweike) M3 Ultra "6-in-1"** hybrid: 1200 W fibre source + CO2 glass tube. Evidence: owner's identification (10), the vendor spreadsheet "六合一" six-in-one (02 §6.2), `.nc` job names `金威刻logo-12线.nc` in the work report (08 §8), laser-family selector fibre/CO2/blue (`SP.m_iEnableLaserType`, 01 §2.1) | CONFIRMED | 02, 08, 10 |
| Active source at capture time | **CO2** (`m_iEnableLaserType=1`; `SecondBkManuPara.xml` still says fibre = the previous state). CO2 tube driven by **5 V PWM** (`CO2LaserControlType=2`) + laser-enable **DO9**; fibre path is IO-controlled (`LaserControlType=3`, shutter DO5, red pointer DO6, laser alarm DI1) and is live when fibre is selected — *not* a leftover (see §9 #9) | CONFIRMED | 01 §2.5, 02 §4 |
| Motion controller | **MCC100** card (successor of MCC3721), firmware `Update/MCC100_V201.52.mcf` (encrypted, entropy 7.998), Ethernet 10.1.1.168, hardware model 224 = universal card with software-selectable laser family | CONFIRMED | 01 §2.1, 04 §5 |
| Axes | Pulse/direction servo axes: X (slot 0) 8000 p/rev, lead 31.003 mm → 258.04 p/mm; Y1 (slot 1) 8000 p/rev, lead 31.009 → 257.99 p/mm; Y2 slave slot 2 (dual-drive flag contradictory: `A1/A2.DoubleDriver=1` vs `MAC.DoubleDevice=0`); slots 3/4 = Z / W (lifting table `LiftingPlatformType=1`) — role assignment OPEN. Both X and Y run reversed (`AxisReverse=1`), home in the negative direction onto limit switches at 80/20 mm/s, back-off 22/15 mm, 20000 mm/s² axis accel, 125 ms accel time | CONFIRMED (numbers) / OPEN (slot 3/4, Y2) | 01 §2.2–2.3 |
| Limit inputs | X: DI5/DI6, Y: DI7/DI8, slot 3: DI9/DI10, slot 4: DI2/DI3, all normally-open; **which of each pair is + / −** is ambiguous because the UI label and the XML attribute name contradict each other | OPEN | 01 §1 caveat, §2.8 |
| Other I/O | DO1 alarm lamp, DO2 high-N2 valve, DO3 high-air valve, DO7 low-O2 valve, DO9 CO2 laser enable, DA1 laser analogue (unused with PWM), DA2 O2 proportional valve (0–10 V ≙ 0–10 bar), DI4 door alarm, DI11 chiller alarm (NC), DI12 "low-O2" function key; **no E-stop configured in software** (`DI.EStop=0`, must be hard-wired) | CONFIRMED | 01 §2.7–2.8 |
| Height follower | On-board capacitive FTC inside the MCC100 (`ZFType=1`, "OnB" endpoints port 999) — used by the fibre head; CO2 layers carry no height parameters and cut at a fixed Z (`AdvFixHeightCutPos`) | CONFIRMED / LIKELY (CO2 behaviour) | 01 §2.4, 02 §3.4 |
| Auto-focus head, extension card, exchange table, roll feeder, rotary, EtherCAT | all present in software, **none fitted/enabled** here | CONFIRMED | 01 §2.4, 06 §4.16 |
| Pendant | XHC PHB02 wireless pendant (`RemoteType=3`, `PHBX.dll`, USB receiver VID 0x10CE); optional USB hand-wheel (`CHidUsb`, VID 1000/6125) | CONFIRMED | 01 §2.9, 04 §4.6, 07 §8.3–8.4 |
| USB "USBKey" VID 3689 / PID 8762 | SM2-token dongle used **only by auto-nesting** (`AutoNest.dll`); gates nothing else | CONFIRMED | 04 §4.6, 07 §8.2, 09 §3.3 |
| PC | Windows 7+, requires admin, NIC statically on 10.1.1.10/24 (`IPSet.exe` = `netsh` wrapper); logs show UTC+8 operation (factory/dealer test profile, 2 950 test jobs, median 4 s) | CONFIRMED | 04 §1.1, 08 §8 |
| Licence | Card-bound: 32-byte data area + admin/user records + RTC on the MCC100, activation codes; last activation 2025-06-17, 180 days (≈ expires 2025-12-14); enforcement PC-side (jobs ran on days with licence errors) | CONFIRMED (mechanism) / LIKELY (PC-side) | 07 §8.1, 08 §5 |

---

## 2. The software package

* Product: "Mlaser v0.0.0.52", internal name **NexCut** (build `NexCut_X1_Http`), lineage SC1000 → SC2000, original vendor 奥森迪科 **AU3TECH** (copyright string removed in this white-label build) — 06 §2.2, 07 §1.
* `MainApp.exe` = 18 MB, PE32, VS2010, MFC 10 + **BCGControlBar Pro 22.10**, OpenGL 1.1 immediate-mode canvas, no packer, PDB path intact; the six `Module/*.dll` plugins are one release build of 2025-06-24 except `CADModule.dll` (2025-06-14, different developer/PDB tree) — 07 §2, §12.2.
* Directory roles (07 §6.1, 08 §1, 03 §2):

| Path | Role |
|---|---|
| `MainApp.exe`, `BCGCBPRO2210u100.dll`, `mfc100u.dll`, `msvcr/p100.dll`, `BCGPStyle*.dll` | shell + UI library + CRT |
| `Module/{CAD,NC,Para,Lang,Log,Control}Module.dll` | the six plugins (single export `newModuleProvider`) |
| `DxfParseDllvc100.dll`, `splineAnalyerVc100.dll`, `AutoNest.dll`, `Dxf2Grp.dll`, `CircleFitDLL.dll`, `SmartNest.dll` | geometry / nesting chain (see §3) |
| `MotionCtrl.dll` | **dead file** (stand-alone build of planner classes that are statically linked into CADModule) |
| `PHBX.dll` | XHC pendant driver (dynamically loaded) |
| `File/` | machine configuration backups (`Bk*.xml`), `ipAdd.ini`, `softPara.ini`, autosave/break-point state (`scFlie` files), `IPSet.exe` |
| `File/Temp/` | task-package staging (`tempGraph.chf`, `tempIsBreak.ini`, …) |
| `Lang/` | `lang.txt` (UTF-16LE `ID#zh#en`, 3 427 keys) + 10 language files |
| `Graph/Work1|2/*.chf` | sample jobs (2020, format v4) |
| `Report/` | `TotalReport.txt` (work report CSV), `report.exe` (Qt 5.15 viewer packed with Enigma VB), thumbnails |
| `Log/` | **MCC100 comm-error log only** (`NC Start`, timeouts, exceptions); `Code.txt` = licence log |
| `Update/MCC100_V201.52.mcf` | card firmware (opaque) |
| `Dump/` | minidump from the Wine run on this PC (2026-09-11) |
| root `*.txt` (`segments.txt`, `closePwmPosRatios.txt`, …) | CADModule debug dumps written on every job |
| `JumpAddTime.txt`, `LanFormatEStr.txt`, `排样内核错误代码.txt`, `ClearFile.bat` | tuning knobs, language-format exception list, nesting error codes, developer left-over |

* **Where the live data lives (new evidence, this session):** the primary parameter files, technology library and the *application* log are under **`%LOCALAPPDATA%\NexCut\`** (`SHGetSpecialFolderPathW(CSIDL_LOCAL_APPDATA)` + `\NexCut`, 02 §1.1). The Wine run of 2026-09-11 on this PC created exactly `~/.wine/drive_c/users/karstein/AppData/Local/NexCut/{HardPara.xml, Technology/Fiber, Technology/CO2, Log/2026-09-11.log, CustomInformation.ini}`. Saving hardware parameters wrote `…\NexCut\HardPara.xml` **and** `<exe>\File\BkHardPara.xml` at the same second (18:00:26) → primary + backup write flow confirmed. That app log is the real application log (`--- Start Sys ---`, `v0.0.0.52`, `[Error]/[Warning]/[Info]`, `Access: UDP`, `false checkMCStatus: 10060`, `Controller connecting failed`, `Alarm: 0 Hardware is not connected`), distinct from the package's `Log/` comm-error log (08 §0).
* **Caution:** the copy in `~/.wine/drive_c/Mlaser/File/BkHardPara.xml` was modified by that Wine session (X lead 31.02, back-off 20, different pendant pairing codes). The authoritative machine calibration is the untouched `SRC/File/BkHardPara.xml` (X 31.003 / Y 31.009, back-off 22 / 15).

---

## 3. Module architecture

```
MainApp.exe (thin MFC/BCGP shell: ribbon, docking panels, ~130 UI classes, GL 1.1 canvas,
             plugin loader, WinHTTP "NexCut" client, CHidUsb hand-wheel, PHBX pendant, crash dump)
  │ LoadLibraryW(\Module\*.dll) → newModuleProvider(); modules looked up by registered name
  ├── "CAD"       CADModule.dll   glyph model, 33 op-commands, DXF/PLT/G-code/.chf I/O, lead-in, micro-joint,
  │                               offsets, sorting, scan/fly-cut, CContoutSmooth + CVelocityPlanning + CInterpMrg
  │                               (planner statically linked), nesting bridge, thumbnails
  │     ├── DxfParseDllvc100.dll  in-house ASCII DXF parser v1.4.92
  │     ├── splineAnalyerVc100.dll openNURBS wrapper (ISpline2DAnalyer, 25 slots)
  │     └── AutoNest.dll ("HePin", SmartNest Tech.) → Dxf2Grp.dll (ODA DWGdirect 2.3, dead path) → CircleFitDLL.dll
  │                               → LoadLibrary("smartnest.dll") = Geometric NestLib (2002), USB dongle 3689:8762
  ├── "数控加工"  NCModule.dll    ALL device I/O: CMCHalAPI/CExtModbus (MCC100, UDP), ZF/AF/EC/laser/monitor HALs,
  │                               CVirtualMachine (offline simulator), CDog (card licence), FIFO filler, log uploader
  ├── "参数引擎"  ParaModule.dll  CXMLParaEngine — flat attribute-bag XML store
  ├── "语言包"    LangModule.dll  lang.txt loader (ID → text)
  ├── "系统日志"  LogModule.dll   daily log writer (+ TCP/HTTP log sink, unused)
  └── "基础控件"  ControlModule.dll custom widgets
Report/report.exe  (Qt 5.15, separate process, launched via ShellExecute; reads Report/report.txt)
MotionCtrl.dll     (orphan copy of CContoutSmooth/CVelocityPlanning, never loaded)
```
Evidence: 07 §3–4 (imports, RTTI, loader at `0x4b6280`, module names), 05 §2 (planner provenance), 09 §2 (geometry chain).

Where each concern lives (07 §13):

| Concern | Location | Nature |
|---|---|---|
| UI, ribbon, property grids, touch skin | MainApp + BCGCBPRO | thin wrapper over a commercial toolkit; only 7 empty dialog templates — screens are built in code from `lang.txt` keys |
| Canvas | MainApp `COpenGLView` | GL 1.1 immediate mode, `glOrtho`, bitmap fonts |
| Parameter store | ParaModule + descriptor table in MainApp (≈1 001 records: section, `Elem.Attr`, label id, type, default, unit, min/max, enum ids) | in-house, fully recovered (01 §0.2, 02 §2.2) |
| Localisation | LangModule + `Lang/*.txt` | in-house; ≈110 Chinese literals are compiled into MainApp outside the key system (07 §6.3a) |
| Geometry, process path, planner | CADModule (+ openNURBS wrapper) | **the bulk of proprietary logic** |
| Device protocols, licence, simulator | NCModule | in-house |
| Nesting | AutoNest → NestLib (third-party, dongle) | not portable |
| Reports | report.exe + CSV | trivial format |

---

## 4. Data flow: from CAD file to motion

The whole trajectory is computed on the PC; the card only executes a FIFO of pre-interpolated ticks and gates the PWM (05 §1, 04 §3.7, 08 §4.4).

```
 DXF / PLT / G-code / .chf ──(CADModule importers; IGP.* clean-up gates)──▶ glyph model
   (IGraph 8..12: contour, group, text, scan, contour-ex; IGlyph 1..7: point, segment, arc, circle,
    ellipse-arc, lwpolyline+bulge, cubic B-spline; per-contour <Crafts>: kerf, PWM curve, lead-in, cool points)
        │  interactive / one-key operations: sort, lead lines, micro-joints, chamfer/α, cool points, bridges,
        │  kerf offset, scan-fill (segments.txt), fly-line linking (linkFlyLine_pathGlys.txt, closePwmPosRatios.txt)
        ▼
 layer index per contour (0 background, 1–9, 10 film) ──▶ BkLayerPara.xml slot (fibre 164 attrs / CO2 21 attrs)
        │  + global process params (BkManuPara.xml: MC.* speeds/accel/precision, GC.* gas delays, FC.* lift rules)
        ▼
 CContoutSmooth::process   NURBS refit (tol 0.01–0.3 mm) → merge collinear → blend corners >30° → piece list
                           {cum length, radius, feed, speed cap, factor} + PWM toggle ratios
        ▼
 CVelocityPlanning::plan   nodes from pieces; junction speed v = f(radius, 0.5/Ta, corner precision);
                           slow-start clamp; whole-contour backward/forward look-ahead; 7-phase jerk-limited
                           S-curve (J = 2A/Ta or 4Vmax/Ta²); Log\VelDecc.txt
        ▼
 sampling (segInterp/arcInterp)  s(t)/L every interpolation cycle (AX.InterpolationCycle = 250 µs), drop < 1 cycle
        ▼
 CInterpMrg / calcGraphCtInterpPt   XY (and pulses via 8000 p / 31.003 mm) + PWM freq/duty per tick,
                                     power/frequency-vs-speed curves, laser on/off at ratio positions, dwells
        ▼
 NCModule fillFifo   TLV items → 298-word frames (frameId + 297 words) → WRITE reg 0x66 (func 0x40) over UDP;
                     keep ≈1.6 s queued (MCFifoTime), throttle on "FIFO space margin" (reg 1000+16/17)
        ▼
 MCC100   executes ticks, drives pulse outputs, PWM (5 V, freq/duty per tick), DOs from in-stream 9999 records,
          applies its own safety decel/jerk on stop, raises FIFO-starvation alarm on under-run
```

Job lifecycle as observed (08 §3–4, 04 §3.8):

1. **Start-up**: open UDP socket; first datagram `READ 1000 n=2` (program id/version, gated by `MinHardwareVer=20152`); then `READ 1000/36 → 50000/26 → 60001/120` (+ `10000/18`); handshake `0x65 ← [9999,13,0xFFFF,ver…]`; read/compare hardware parameter blocks at 59600+; licence check (`CDog`, `Code.txt`); prompt to home (`mp119`).
2. **Idle**: poll block 1000 every `MCCore` = 30 ms (≈47 transactions/s incl. jog); slower reads of 2000/5000/50000/50200; ZF status block 10000/18 at a low cadence (it regularly needs > 3.5 s and is harmless when it times out).
3. **Jog/home**: `0x65 ← [3, axis, v, a, j, target]` (v in 0.001 mm/s: 50 000 / 200 000 = `JogSlowSpeed`/`JogFastSpeed`; a = 5999 = `XFastMoveAcc` truncated, j = 10·a; target absolute 0.001 mm or ±4 000 000 = continuous), `[1, axisMask, 2, vSlow, vFast]` home (mask 31 = all), `[5, 0x80000003, 550000, 8999, 89990, x, y, 0, 0]` two-axis go-to (550 = 500 × `EmptyMoveSpeedFactor` 1.1; 8999 = 5999.99 × 1.5 `EmptyMoveAccFactor`) — see §10 for this unit derivation. Exception 3 = command refused while the axis is moving; the Windows app re-sends up to 3× at 52 ms.
4. **Job**: `clearFifo` (0x67 ← 1), stream frames, `startFifo` (0x67 ← 2); per contour a prologue (`3001, 3002[5], 9999[2,4,4], 103[1000,0], 2001[…], 9999[2,0x100,0x100]`, stationary ticks with duty > 0 = pierce/dwell) and an epilogue (`9999[2,0x100,0], 109[1000,0], 2001, 118[…], 3002[4]`); `stopFifo` (0x67 ← 3) at the end; DO write `[9999,2,1,0]` 1–14 s after the stop.
5. **Stop event**: a row is appended to `Report/TotalReport.txt` (estimated times, not measured — written for aborted runs too); break-point records `AutosaveParam1/2.ini` (even/odd item index), `ManuContour.dat`, `autosave.chf`; at exit `softPara.ini` (`NormalExit`, last X/Y in 0.001 mm).

---

## 5. External interfaces

| # | Interface | Endpoint / bus | Protocol | Used on this machine? | Source |
|---|---|---|---|---|---|
| 1 | MCC100 motion card | 10.1.1.168 **UDP** 502 (`AccessType=1`; TCP path exists) | Proprietary `CExtModbus` framing: `[seq u16 BE][CRC-16/MODBUS over bytes 4.., stored hi-first][len u16 BE][unit 0][func][LE payload]`; func 0x30 READ (u32 addr, u16 count → u32 words), 0x40 WRITE/command/FIFO, 0x26 byte-block (firmware/file download), 0x20/0x21 byte streams; exceptions `func|0x80, code` → ErrCode 500+code (2 = "not ready" after reset, 3 = "busy/refused"). Retry: send ≤ 2 re-sends, recv ≤ 3, `select` 500 ms, 7-datagram ladder ≈ 3.5 s idle / one-try-then-continue while streaming | **yes** | 04 §3, 08 §2 |
| 2 | On-board Z-follower (FTC) | 10.1.1.168 port 999 (`OnBZF*`) | second `CExtModbus` instance (LIKELY); status block 10000/18, property block 11000/39 read through the card | yes (fibre head) | 01 §3, 04 §3.5 |
| 3 | Network FTC10 | 10.1.1.169:502 (`ZF*`) | Modbus-like | no (`ZFType=1` = on-board) | 01 §3 |
| 4 | Fibre laser source | 10.1.1.170:10001 (`Laser*`) | IPG YLR ASCII (`EMON\r`, `EMOFF\r`, `ABN/ABF\r`, `SDC n\r`) or Raycus ESC frames; serial variants | no (`LaserControlType=3` = IO; `LaserType=0` Raycus) | 04 §4.3 |
| 5 | On-board laser channel | 10.1.1.168:888 (`OnBLaser*`) | serial-over-card | no | 01 §3 |
| 6 | Auto-focus head (AF/E310), extension card (EC) | 10.1.1.168:888 / "Adv" 666 | RTU-style CRC frames tunnelled through the card | no (`AFType=0`, `ECType=0`) | 04 §4.2 |
| 7 | EC3710 extension box | 10.1.1.170:502 | **standard Modbus TCP** FC3/FC16 | no | 04 §4.1 |
| 8 | Remote monitor (telemetry) | **47.104.17.21:9001** (Alibaba Cloud Qingdao) | MFC WinInet HTTP, JSON (`devId`, `status`, `warnId`, `lightVal`, …), methods `updateBasicInfoByDevId` etc.; log upload `logID=&machineID=&logData=` | disabled (`EnableRemoteMonitor=0`) — privacy: block in any port | 04 §4.4, 07 §9.2 |
| 9 | "NexCut" job server | user-selected host, port **8080** hard-coded | WinHTTP `POST /NexCut/Login`, `/NexCut/File/LoadFile`, `/NexCut/File/UploadGCode`, default `NexCut`/`12345678`, header `Authorization: DebugWithSuperpermissions` | optional | 07 §9.1 |
| 10 | Activation URL | `http://www.au3tech.cn/key/` | string shown in the activation dialog; **no request is made** | — | 04 §4.5, 07 §9.2 |
| 11 | Wireless pendant | USB HID receiver VID 0x10CE (XHC PHB02) via `PHBX.dll` (`Xinit/XOpen/XGetInput/XSendOutput/XClose`, feature reports) | proprietary XHC | yes (`RemoteType=3`) | 07 §8.4 |
| 12 | Hand-wheel/joystick | USB HID VID 1000/PID 2016 or VID 6125/PID 2012 (`CHidUsb`, input reports 0xbc/0x55) | proprietary | only for `RemoteType` 1/2 | 07 §8.3 |
| 13 | Nesting dongle | USB HID VID 3689 / PID 8762 (or 0x2020), SM2 challenge/response | `PWDKey` SDK inside `AutoNest.dll` | only for auto-nesting | 07 §8.2, 09 §3.3 |
| 14 | Serial | `\\.\COM%d` (FTC61, serial lasers) | RTU/ASCII | no | 04 §2 |
| 15 | Card licence | MCC100 register groups "管理员加密数据 / 用户加密数据 / 时间日期 / 识别码数据 / 加密数据域访问开关" (addresses unknown; 150/151 special-cased) | via #1 | yes | 07 §6.3a, 08 §5 |

### 5.1 MCC100 register map (04 §3.4–3.7, 08 §4)

| Address | Count | Content |
|---|---|---|
| 1000 | 2 / 36 | RO status: program id, version, date, time, DI, DO, alarm words 1/2, run status, AD, DA1–3, PWM freq/duty, **FIFO frame id (16)**, **FIFO space margin (17)**, FIFO config, processing status/position, contour status/index, power-on/comm/laser-on time, dual-drive deviation |
| 1050 | 3 | version/time triple (LIKELY) |
| 2000 | 50 = 5 axes × 10 | per-axis RO: status, speed, pulse position, encoder position, stop pulse, cumulative travel/pulses … (index→address order not proven) |
| 5000 | 1 (window 5000..5008) | RW: input type / output mask / e-stop port / safety decel / jerk |
| 10000 / 11000 | 18 / 39 | on-board ZF status / property blocks (slow) |
| 13000 / 13200 | ? | unknown RO |
| 50000 | 26 | system RW: IP/mask/gateway, bus cycle, DA calibration, comm timeout, dual-drive, brake delay, DI filters (mapping to the 47 lang names not 1:1) |
| 50200 | 100 = 5 × 20 | per-axis RW: config, ±soft limits, accel, jerk, home speeds, origin offset, lead, cmd/encoder pulses per unit, I/O config |
| 59600 + 0xD0·i | 52 each | hardware parameter blocks mirrored by `BkHardPara.xml` (`readParamFromCard` / `writeHardParam2Card`) |
| 60001 | 120 | parameter/servo block read at start |
| 0x65 (101) | n | **command**: 1 home, 3 single-axis move, 5 multi-axis move, 4 start, 7 / [1,8] offline upload, [102] re-sync, 9999,n misc (1 clear FIFO, 2 set DO, 4 set DA, 13 handshake) |
| 0x66 (102) | 1 + 297 | **FIFO data**: frame id + TLV items `header = (payloadBytes<<16) | opcode`: 3000 tick `[(dY<<16|dX), (freq<<16|duty)]`, 3001 marker, 3002[mode], 9999[2,mask,value] DO, 103[ms,0] dwell before laser-on, 109[ms,0] dwell after laser-off, 2001[0x03000002,20000] laser/PWM config, 118[4,0,n] contour bookkeeping |
| 0x67 (103) | 1 | **FIFO run control**: 1 clear, 2 start, 3 stop |

PC-side address windows that are never sent: (104,1000) except 150/151, (1100,2000), (3000,5000), (5008,6000), (51000,59000).

---

## 6. File formats (all owned by the port)

| Format | Where | Grammar | Detail |
|---|---|---|---|
| Hardware / machining / software parameters | `File/BkHardPara.xml` (446 attrs, 14 groups), `File/BkManuPara.xml` (331 attrs), `SecondBkManuPara.xml` (2nd-generation backup); primaries `%LOCALAPPDATA%\NexCut\{HardPara,ManuPara,SystemPara}.xml` | `<ParameterRoot>` → group elements `P<Section>[_n]` → leaf elements whose **attributes** are the keys (`Elem.Attr`); ASCII/UTF-8, CRLF, no prolog, `%.17g` doubles; every key has a descriptor (type, unit, default, min/max, enum ids) recovered from MainApp | 01 §0–1 |
| Layer / process parameters | `File/BkLayerPara.xml` (`PLayerParam1..11/GP` 164 attrs fibre, `PCO2LayerParam1..11/GP` 21 attrs CO2); primary `…\NexCut\LayerPara.xml` | same attribute-bag; slot 0 = background, 1–9, 10 = film; enums: gas 0 Air/1 O2/2 N2/3 HiAir/4 HiO2/5 HiN2; `ManuType` 0 direct, 1 fixed-height, 2/3/4 = 1/2/3-stage pierce | 02 §2–4 |
| Technology library | `%LOCALAPPDATA%\NexCut\Technology\Fiber|CO2\*.xml`; 53 + 2 vendor presets in the parent folder | single slot always wrapped as `P…Param11`; CO2 presets lack `CutFreq` (older build) → importer must default missing attrs | 02 §6 |
| Job file `.chf` | `File/autosave.chf`, `Graph/Work*/N.chf`, `File/Temp/tempGraph.chf` | "scFlie" line container, version 5 (reader accepts 1–5), graphs 8–12, glyphs 1–7, `<Crafts>` block, trailer; `%f` numbers, `0.0` zero special case, GBK strings; reference parser `tools/chf_parse.py` (validated on all 8 samples) | 03 |
| Task packages | `.enc` (`NEXCUT_CHF_END`, `NEXCUT_LAYER_XML_END`, `NEXCUT_JPG_END`, `NEXCUT_OLPF_END`, `NEXCUT_OLPI_END`), `.aut` (`TASK_GRAPH/LAYER/MANU/PARAM_ONE/PARAM_TWO/IS_BREAK`) | concatenated sections with ASCII markers; exact binary layout OPEN | 02 §6.3 |
| State files | `File/ManuContour.dat` (cut-order index list), `AutosaveParam1/2.ini` (item idx, X µm, Y µm, graph idx, point idx; even/odd item parity), `File/Temp/tempIsBreak.ini`, `PithCompensate.pcf` (0 records) | `scFlie` / values / `eof`, CRLF | 01 §6, 08 §6.6 |
| `File/softPara.ini` | `[SC2000] NormalExit XAxis YAxis ZAxis WAxis` | last positions in 0.001 mm (X 1020.277, Y 175.510) | 01 §4 |
| `File/ipAdd.ini` | `[IP]` endpoints + timing, `[Soft]` misc | INI; full key inventory in 04 §1 | 04 §1 |
| `JumpAddTime.txt` | `[Jump] AddTime=200`, `[Axis4Freq] Is4Freq=0`, `[Arc2SegVelK] K_X/K_Y=100`, `[LimitSamllCircleVel]` (unreferenced) | INI, LF | 01 §6.5, 05 §8 |
| `%LOCALAPPDATA%\NexCut\CustomInformation.ini` | `[CustomInformation] RunModel=0 Custom=1234` | OEM password / run mode | 07 §6.1 |
| Language | `Lang/lang.txt` `ID#中文#English` UTF-16LE BOM, 3 431 records / 3 427 ids (4 duplicates); 10 secondary `ID#text` files with defects | line-based; fallback = show the id | 06 §1 |
| Work report | `Report/TotalReport.txt` UTF-8: 7-field rows to 2025-03-06, 10-field rows after; `LogReport.txt`, `report.txt` (`\r\r\n`), `Report/lang.txt` (`lang==0/1`) | localised name/duration, `×`, garbage negatives for empty jobs | 08 §6 |
| Comm log / licence log / app log | `Log/YYYY-MM-DD.log` (five templates), `Log/Code.txt`, `%LOCALAPPDATA%\NexCut\Log\YYYY-MM-DD.log` | `<ts> [Level] -> msg` | 08 §2, §5; this doc §2 |
| Firmware | `Update/MCC100_V201.52.mcf` (`E310_V80*.afb`, `.zfb`, `.efb` for sub-devices) | 20-byte header (size, 0x00F8B0F4, …) + encrypted body; uploaded with func 0x26 | 04 §5 |
| Debug dumps | root `*.txt` | planner stage outputs (decoded: 24 × 25 mm raster, 60 mm side lines, 2.66 mm spline U-turns, total 1506.62 mm; 46 PWM toggle ratios) | 05 §5–6 |
| Nesting temp | `dat/tmpnst1/`, `.py/.sht/.Nip/.grp/.xjy` | NestLib text formats | 09 §3.3 |
| Imports | DXF (ASCII; LINE/ARC/CIRCLE/ELLIPSE/LWPOLYLINE/POLYLINE/SPLINE/POINT/TEXT/MTEXT/INSERT/XLINE/RAY/SOLID/TRACE/3DFACE; no HATCH/DIM/IMAGE), PLT/HPGL-2, G-code (BNF with `L/LP/M17` subprograms), Renishaw `.rtl/.ren/.pos/.csv`; **no** DWG (unreachable), AI, SVG, PDF, bitmap | | 09 §3.2, §3.7, §4 |

---

## 7. Consolidated fact table

### 7.1 CONFIRMED (cross-verified)

| Area | Fact | Doc |
|---|---|---|
| Architecture | MainApp has no `ws2_32`; all device I/O in NCModule; six plugins with one export each; modules resolved by Chinese registered names; singleton at `0x5ff1b0`; `"CAD"` module pointer at +0x2f0 (NULL → Wine crash) | 07 §3–4, §12 |
| Architecture | MotionCtrl.dll is imported/loaded by nothing; identical classes are inside CADModule (RTTI, `1.3.23`) | 05 §2, 09 §8 |
| Architecture | Dxf2Grp/ODA/CircleFit are on no reachable path; DWG import unreachable | 09 §3.1 |
| Parameters | 1 001 descriptors; all 777 hardware/machining keys and all 185 layer keys have descriptors; 969 `PREFIX.Attr` literals in MainApp; all 942 XML pairs referenced | 01 §0.2, 02 §2.2, 07 §6.2 |
| Parameters | Primary files in `%LOCALAPPDATA%\NexCut`; `Bk*` are backups re-read on failure; `SecondBkManuPara.xml` older snapshot (soft limits 1→0, laser fibre→CO2) | 02 §1.1, 01 §5, this doc §2 |
| Parameters | Layer naming index 0/1–9/10 from code; `.chf` stores 0-based layer after `<End Glyphs>` (`CGlyContour::save`, `COpGraphLayerCmd`) | 02 §4–5 |
| Parameters | `lang.txt` direction labels `pd168/pd169` are swapped in English; `NegativeLimitInput`/`ForwardLimitInput` labels contradict the names | 01 §1 |
| .chf | Container, version-5 grammar, all glyph layouts (segment, circle, bulge polyline proven numerically; arc/ellipse/spline/text/scan/contour-ex from disassembly), version 1–4 skip counts, degenerate-graph dropping | 03 |
| Protocol | UDP/502 socket setup; frame layout; CRC-16/MODBUS (0xA001, init 0xFFFF) over `[len..end]`, PC hi-first, card lo-first, never checked on receive; func codes; exception mapping; retry ladder (labels `1/1,1/2,2/2,3/2,1/3,2/3,3/3`, 500 ms); `Access: UDP` logged at runtime | 04 §3, 08 §2, this doc §2 |
| Protocol | Register groups/counts; PC-side address windows; command reg 0x65 vocabulary; **0x67 = FIFO run control 1/2/3**; FIFO frames 298 words; TLV grammar consumes all 22 leaked frames exactly | 04 §3.5–3.7, 08 §4.4 |
| Protocol | Start-up read order; reconnect `0x65 ← [102]` then `READ 1000 n=2`; jog/home vectors with real constants; exception 3 on re-sent jogs at 52 ms | 08 §3–4 |
| Protocol | EC3710 = standard Modbus TCP FC3/16; AF/EC = RTU-style through the card; IPG/Raycus command strings; monitor = MFC WinInet JSON | 04 §4 |
| Planner | Pipeline order; junction formula (all branches re-derived); J = 2A/Ta or 4Vmax/Ta², Ta ∈ [0.06, 0.25] s; S-curve generator; sampler and validator thresholds (`L < 0.00025·v` dropped); slow-start; split-and-stop at PWM/micro-joint ratios; `CInterpMrg` blocks; dump files decoded to the mm | 05 |
| Layers | Process model (5 pierce stages, bolt, smooth pierce, residue spiral, power/freq curves as (speed %, output %) pairs, UD segments, ZF vibration abatement); units mm/s, %, Hz, mm, bar≙V, ms; only `lp19` validation enforced | 02 §3 |
| Licence | Card-bound `CDog`; `Code.txt` history (7/3/180-day codes, RTC loss, 30-year offset, 2055 clock trick); `CheckUserID=109` hard-coded and compared to the card's user code; dongle only in AutoNest; PHBX checks VID 0x10CE | 07 §8, 08 §5 |
| Runtime | `Log/*.log` is the comm-error log; five templates; 1 794 lines; failure modes (no route 10065, card off, 2025-07-17 outage, deaf block 10000, jog rejections) | 08 |
| Runtime | Work report semantics: row per stop event, estimates not measurements, two formats, localisation, garbage doubles | 08 §6 |
| UI | 3 427 language ids; prefix→module map; 69 window classes; ≈90–110 screens; full alarm table `gp0–59/92–122/141/200–220`, EtherCAT words; ≈110 compiled-in Chinese literals; touch skin `SBT/SLED` | 06, 07 §5–6 |
| Geometry libs | DxfParse entity coverage; PLT mnemonics; G-code BNF; text via `GetGlyphOutlineW(GGO_BEZIER)`; NestLib API actually called (clearance, resolution, part-in-part, offsets — no guillotine, no punch profiles); `Nest_SetNestPara` index mapping re-derived | 09 |
| Wine | Runs with `winetricks mfc42` from `C:`; crash without it at `0x4b24e6`; app data in `AppData\Local\NexCut` | 07 §12, 10, this doc §2 |

### 7.2 OPEN (needs live capture, bench test or further disassembly)

| # | Question | Blocks | Best way to close | Doc | Status (static sprint, 2026-09-15) |
|---|---|---|---|---|---|
| O1 | Tick period and unit of opcode-3000 increments (2.1–2.2 units/tick at 50 mm/s, ≈8 at rapid; 250 µs × 258 p/mm predicts 3.2); frame-id semantics (restarts without `NC Start`, reaches 1931 in 10 s); what `MaxItemPerFrame=60` limits vs 99 observed | job streaming | capture one 100 mm move at 100 mm/s and count items/frames/time | 08 §9.1, §9.11, 04 §10 | **NARROWED**: tick = motor pulses (X low / Y high, trunc+carry); frame id = reg 1015+1; 300-word flush; MaxItemPerFrame unused. Still live: card tick period (reg 50005 + 100 mm move) and reg-1016 units → 11-static-findings §0, §5 |
| O2 | Bit meaning of status/alarm words (1000+4…+8), axis status (2000+0), exception codes 2/3 precise semantics | any UI, safety | capture idle, e-stop, limit hit, jog-while-moving | 04 §10, 06 §9.1 | **CLOSED (static)**: block-1000 map, DI/DO bits, alarm_1/alarm_2 and axis-status bits; exception 2/3 NARROWED (power-up read, jog-while-moving) → 11-static-findings §4 |
| O3 | Jog/home argument units and index-vs-mask (home 2nd word 0x1F seen; sub-cmd 3 axis 0/1/4); home `[1,axis,2,2000,20000]` speeds vs configured 80/20 mm/s | milestone 1 | capture each jog key/home with known settings | 04 §3.6, 08 §4.5 | **CLOSED (static)**: ×K=1000 (µm, µm/s), acc plain, jerk 10·a; jog word1 = axis index, target **relative** (bit31 abs); home `[2,1<<slot,0]`; `[1,…]` is STOP → 11-static-findings §2 |
| O4 | Opcodes 3001/3002/103/109/2001/118 semantics; which DOs bits 2 and 8 are (see §10: LIKELY DO3 high-air, DO9 CO2 enable); which output the 0x65 `[9999,2,1,x]` sets | streaming | capture with different gas/laser settings | 08 §9.2 | **CLOSED (static)** (encoding; replay verbatim): DO `[2,1<<(p−1),v<<(p−1)]`; 103/109/118/2001 = ZF records, not dwells → 11-static-findings §5 |
| O5 | Block 10000/11000/13000/13200 contents; why 10000 stalls > 3.5 s; poll cadence | driver design | capture + register naming | 04 §3.5, 08 §4.3 | **CLOSED (static)** identity (10000 ZF status, 11000 ZF props, 13000/13200 AF, unused); per-word address spacing and stall cause NARROWED → 11-static-findings §0 |
| O6 | Licence registers / data area / RTC commands; does firmware refuse motion when unlicensed (evidence says PC-side) | port viability | capture start-up after `Active OK`; test with expired licence | 07 §14, 08 §9.6 | **NARROWED**: licence block 59500–59511; PC-side dog gate on Start/frame/dry-run/resume exists; firmware gate = one bench jog + FIFO start without exchange → 11-static-findings §3, §7 |
| O7 | Axis slots 3/4 (Z vs W), which slot the lifting table jogs; Y dual-drive (`DoubleDriver=1` vs `DoubleDevice=0`) | homing/lifting | UI Advanced page or capture which 2000-block slot moves | 01 §7 | **CLOSED (static)**: slots 0 X, 1 Y, 2 Y2, 3 height axis, 4 W = lifting table; Y dual-drive NARROWED (watch reg 2022 during Y jog) → 11-static-findings §0 |
| O8 | Limit-switch polarity per pair (DI5/6, DI7/8, …) | homing safety | bench: press each switch, read DI word | 01 §1 | **NARROWED** (bench): axis-status bits 0/1 = hard +/−; press each switch, read 1004 + 2000+10·slot → 11-static-findings §7 |
| O9 | ZF (height follower) protocol on port 999; whether CO2 jobs command it; FTC register semantics (`ZFReadOnly*`) | fibre cutting, edge seek | capture port 999 during a fibre job / calibration | 04 §10, 02 §7 | **NARROWED**: port 999 = EC endpoint, never opened; ZF over main UDP link; command set known; CO2 jobs *do* emit ZF records (ZFType=1); fibre pierce stream + units = capture G → 11-static-findings §0, §1 C6 |
| O10 | Fibre laser control when fibre is selected (IO/DA vs 10.1.1.170 Ethernet); DA1 scaling of "peak current %" | fibre cutting | capture with fibre selected | 02 §7.9, 04 §4.3 | **CLOSED (static)**: DO5 gate / DO6 red / DA1 mV = %·100; Max Photonics laser-side map (fibre path inactive while CO2 selected); nameplate + DO5 timing in capture G → 11-static-findings §0 |
| O11 | `.chf` crafts scalars (`int58`, `double170/188`, PWM pair meaning, lead enum, compensate 2 vs 3, cool-pos unit, trailer bool/double, text/spline flags, type-12 links) | file round-trip | diff `autosave.chf` after UI actions under Wine | 03 §12–13 | **NARROWED**: int58=1, compensate 2 inside/3 outside, lead enum, ratios, PWM node = (centre ratio, mm), trailer = edge-seek angle; text/spline/type-12 flags = Wine diff → 11-static-findings §0 (A6 §2) |
| O12 | Exact UI → planner block mapping (P4/P5/P8/P9; whether `AccTime` is /1000 in MainApp); which ratio list feeds split-and-stop; which flows use node-builder B | planner fidelity | disassemble MainApp caller of `CCADModule+0x…` wrapper, or black-box tests under Wine | 05 §13 | **CLOSED (static)**: full P0..P9 map; AccTime ×0.001 in MainApp; P2 = SplineAccuracyRate → 11-static-findings §0 (A6 §1.6) |
| O13 | `ManuType` codes for "advanced fixed height"; folding of the two combos | layer editor | Wine test + diff `BkLayerPara.xml` | 02 §7 | **CLOSED (static)**: 0 std, 1 fixed, 5 adv. fixed, 2/3/4 = 1/2/3-stage pierce; 6/7 unreachable → 11-static-findings §0 (A6 §3) |
| O14 | `.enc`/`.aut` binary layout; `NormalExit` write path; `AutosaveParam` event; `TotalReport` row trigger | interchange, resume | Wine tests | 02 §7, 08 §9 | **CLOSED (static)**: marker containers, NormalExit, 100 ms AutosaveParam timer, TotalReport trigger → 11-static-findings §0 (A7) |
| O15 | Alarm-id ↔ bit mapping (`gp1–32` one word?); how numbered ids are assembled; duplicate-id policy in LangModule | alarm panel | disassembly of the alarm decoder | 06 §9 | **CLOSED (static)**: code scheme 8000/8100/9000 → EtherCAT* ids; gp1–32 legacy, never raised → 11-static-findings §4 |
| O16 | PHBX pendant HID report layout; hand-wheel report layout | pendant support | USB capture | 07 §14 | **NARROWED** (USB only): PHB02 report/checksum/key map decoded; receiver presence, PID, key legend = A8 R0–R3 → 11-static-findings §0 (A8) |
| O17 | `.mcf` cipher, CPU type | firmware update from Linux | not needed if Windows tool keeps that job | 04 §10 | **OPEN** (non-goal) |
| O18 | Which value sits at `[param+0x4356]` that forces TCP; does the card also accept TCP | transport | try TCP once in a capture session | 04 §10 | **CLOSED (static)**: hidden command 0x426a only; build uses UDP unless AccessType=0 → 11-static-findings §0 (A1 §8) |
| N1 | Stop / Pause / E-stop command vectors (new, 99-gaps §4) | M1 safety | static | A1 | **CLOSED (static)**: stop `[1,0x1F,2,vd,10·vd]` or `0x67←3`; Pause = Stop; UI E-stop = stop + laser gate off → 11-static-findings §2 |
| N2 | Resume / Continue after Pause (new) | M5 | capture F | A1 §4.3 | **OPEN** → 11-static-findings §7 step 9 |
| N3 | Block 5000 e-stop port / safety decel (new) | safety panel | static + read | A2 §4 | **NARROWED**: never read by vendor, 5001/5008 written; one `READ 5000/9` → 11-static-findings §3, §7 |
| N4 | Connect prologue; "handshake `[9999,13,…]`" (new) | M1 | static | A5, lead | **CLOSED (static)**: connect sends `[9999,5,0,0]`; `[9999,13,0xFFFF,…]` is an extended-DO bulk-off in stop-manu → 11-static-findings §2.1 |
| N5 | `0x65←[9999,16]` per record push (new) | streaming | capture | A3 | **OPEN** (deny) → 11-static-findings §3.3 |
| N6 | Pendant unplug with jog key held (new) | safety | static | A8 | **CLOSED (static)**: no key-up is generated; the port must stop on read error/silence → 11-static-findings §0 N7 |

---

## 8. Storage and privacy summary

* Everything the port must read/write on the user's side: `%LOCALAPPDATA%\NexCut\` (primaries, technology library, app log, `CustomInformation.ini`) and `<install>\File\` (backups, state), `<install>\Report\`, `<install>\Log\`, `<install>\Update\`. Linux equivalents: `$XDG_DATA_HOME/nexcut/` (primaries + library), `$XDG_STATE_HOME/nexcut/` (logs, reports, break-points), `$XDG_CONFIG_HOME/nexcut/`.
* Outbound network in the original: card LAN only, plus opt-in telemetry to 47.104.17.21:9001 and the optional NexCut server. The port implements neither.

---

## 9. Contradictions between reports — resolved

| # | Topic | Position A | Position B | Resolution and why |
|---|---|---|---|---|
| 1 | Card transport | 10 (and project memory): "Modbus TCP confirmed by traffic"; 01 §3 "Modbus-TCP-like (CStdModbus) [likely]"; 05 "Modbus/TCP" | 04: **UDP** datagrams with proprietary `CExtModbus` framing when `AccessType=1`; 08: logs show `sendto/recvfrom/select` errors 10065/10038/10060 | **B.** Shipped `ipAdd.ini` has `AccessType=1`; the disassembly builds `socket(AF_INET, SOCK_DGRAM)`; the Wine run on this PC logged `Access: UDP` in `AppData\Local\NexCut\Log\2026-09-11.log`. "Modbus TCP" in 10 is a loose label for "port 502" — the frames are not Modbus/TCP MBAP (no MBAP header, custom function codes 0x30/0x40/0x26, CRC present). A TCP path exists (`AccessType=0` or a parameter byte) so capture filters must include both. |
| 2 | Parameter DB / technology folder | 10 (and memory): drive-root `\Technology\Fiber|CO2`, "run from C:" | 02 §1.1: `%LOCALAPPDATA%\NexCut\…` via `SHGetSpecialFolderPathW(CSIDL_LOCAL_APPDATA)` | **B.** Verified on this PC: `~/.wine/drive_c/users/karstein/AppData/Local/NexCut/Technology/{Fiber,CO2}` exist, plus `HardPara.xml`, `Log/`, `CustomInformation.ini`. The first-launch "Failed to create Technology folder" was a fresh-prefix race, not a drive-root path (memory note already corrected). |
| 3 | What the USB "USBKey" 3689:8762 is | 06 §4.19/§7, 01 §2.9 (guess), memory: the software's licence dongle / pendant receiver | 04 §4.6, 07 §8.2, 09 §3.3: referenced only by `AutoNest.dll` (`HID#Vid_3689&Pid_8762`, `cmp ax,0x3689`), gates auto-nesting only; PHBX checks VID 0x10CE; MainApp has no such constant | **B.** Static evidence is exhaustive (no constant in MainApp/PHBX). The "dog" (`dogState_*`) strings are the *card* licence (§1). |
| 4 | Licence enforcement location | 06 §10: controller may refuse motion (`ls3`) | 08 §5.4: jobs ran on days with licence failures at every start → PC-side | **B (LIKELY)**, to be confirmed by O6; the port must still never write to the licence area. |
| 5 | FIFO item format | 04 §3.7 original: fixed 12-byte `{0x80BB8, dxdy, 0x13880004}` | 08 §4.4: TLV list `header=(bytes<<16)|opcode` | **B.** TLV consumes all 22 frames with zero remainder; opcode 3000 with 8 payload bytes *is* the 12-byte case, so both views agree for motion ticks. |
| 6 | Register block 10000 | 04 original / 08 §4.3 quoting it: "pulse-axis group"; 08 §9.3 guess: device-report counters; 04 said "read every core cycle" | 04 verifier: the reader logs `false updateZFStatus` → on-board ZF status | **On-board ZF status block** (function-name evidence). Not every core cycle: 08's statistics show isolated bursts 10–40 min apart while block 1000 (30 ms) never failed — cadence is lower (LIKELY `ZFCore=500 ms × ZFUpdateFactor=20`). Slowness fits a sub-device query. Open O5. |
| 7 | MotionCtrl.dll loaded? | 07 §10: "maybe dynamically loaded under some setting" | 05 §2, 09 §8: no binary contains `MotionCtrl` or its export names, delay-load tables empty | **Dead file.** |
| 8 | Layer slot count | 07 §6.2: "`PLayerParam1..8`" | 02: 11 fibre + 11 CO2 | **02**; re-verified by grep (22 elements). |
| 9 | Machine / fibre I/O | 01 §2.5: CF1390 = glass-tube CO2 cutter, fibre settings are template leftovers | 10, 02 §6.2, 08 §8: dual-source Gweike M3 Ultra | **Dual-source.** The fibre block (`LaserControlType=3` IO, DO5 shutter, DO6 red pointer, DI1 laser alarm, ZF on-board) is the live wiring when `m_iEnableLaserType=0`; `SecondBkManuPara.xml` (=0) is that earlier state. The port must model both sources and the switch warning `A250607_*`. |
| 10 | Height follower vs CO2 | 02 §3.4: "CO2 head has no capacitive follower" | 01 §2.4: `ZFType=1` on-board FTC active | **Both.** FTC is configured for the fibre head; CO2 layers have no height fields and the leaked CO2 FIFO frames contain no ZF opcodes. Whether the CO2 job still talks to port 999 is O9. |
| 11 | `AutosaveParam` field 1 | 01 §6.3: index into `ManuContour.dat` | 08 §9.8: values 37/38 exceed the 24-entry list | **08**: index into the processed-item list (same job: files written 4 s apart). Parity rule (even → file 1, odd → file 2) from 01 stands and matches all samples. |
| 12 | Vendor preset count | 02 summary / task brief: 54 | 02 verifier: 53 (+2 cleaning/quenching) | **53 + 2.** |
| 13 | Report CSV encoding | 01 §6.4 implies GBK for report rows | 06 §1.1, 08 §1: UTF-8 | **UTF-8**; only `File/ProcessesStatistic.txt` is GBK. |
| 14 | Register 0x67 vs opcode 103 | 04: 0x67 = FIFO run-control register | 08: in-stream opcode 103 = dwell | Different namespaces (register address vs TLV opcode); both hold. |
| 15 | `au3tech.cn/key/` | 10: "plain HTTP via WinHTTP" | 04 §4.5, 07 §9.2: constant string assigned to the settings object; no HTTP call | **No request is made** in this build; safe to ignore. |
| 16 | `[102]` written to 0x65 | 04: FIFO one-shot | 08 §3.3: comm re-sync before version read | Context favours **re-sync** (LIKELY); both rejected with exception 2 in the logs. Open O5/O2. |
| 17 | Items per frame | 05 §4, `ipAdd.ini MaxItemPerFrame=60` | 08: 99 items in every leaked frame; `fillFifo` uses constant 100 | 60 governs something else (sub-device down-files per 04 §1); MC frames are 99 items. Open O1. |
| 18 | `CHidUsb` = dongle | 06 §4.19 | 07 §8.3: hand-wheel reader, VID 1000/6125 | **07.** |

---

## 10. New cross-report inferences (made here)

1. **Command-vector units (LIKELY, high).** The two-axis go-to `[5, 0x80000003, 550000, 8999, 89990, 430178, 174097, 0, 0]` (08 §4.5) decodes with the machining parameters of 01 §2.6: 550000 = 500 mm/s `MC.XFastMoveSpeed` × 1.1 `MP.EmptyMoveSpeedFactor` in **0.001 mm/s**; 8999 = 5999.99 `MC.XFastMoveAcc` × 1.5 `MP.EmptyMoveAccFactor`, truncated, in **mm/s²**; jerk 89990 = 10 × accel. The jog vectors match the same rule (200000 = `JogFastSpeed` 200 mm/s, 5999 = accel, 59990 = 10·a; W axis 4000/40000 = `HPA3.Acc`). Positions are absolute 0.001 mm (consistent with `softPara.ini`). → milestone 1 can build jog commands from the XML values without guessing.
2. **In-stream DO bits (LIKELY, medium-high).** `9999[2, mask, value]` with value 4 = bit 2 and 0x100 = bit 8 (08 §4.4) corresponds, 1-based, to **DO3 = `MGP.HighAir`** and **DO9 = `LGP.CO2DOLaser`** (01 §2.8); the frames belong to CO2 layer slot 2 (`CutSpeed 50, CutDuty 4, CutFreq 5000, CutGasType 3 = High Air`, re-read from `BkLayerPara.xml`). So the prologue is "gas on → dwell → PWM config → laser enable on → pierce ticks", the epilogue "laser enable off → dwell". The 0x65 command `[9999, 2, 1, 0/1]` by the same rule targets DO1 = alarm lamp (`DO.AlarmSignal=1`), plausible for on-at-fault/off-after-job but unproven (O4).
3. **Frame cadence vs tick.** Observed refill 8.5–15 frames/s × 99 ticks ≈ 0.85–1.5 k ticks/s, i.e. ~1 ms per tick if streamed in real time — in tension with `AX.InterpolationCycle=250` µs and the 258 p/mm pulse scaling (O1). The capture experiment in `PORT-PLAN.md` §7 resolves it.
4. **Primary/backup write flow.** A parameter save writes the AppData primary and the `File\Bk*.xml` backup in the same second (Wine prefix evidence, §2), so a port that keeps only one copy must still emit the `Bk*` file if the Windows tool is to read its state.
5. **App log exists** (`%LOCALAPPDATA%\NexCut\Log\`) with `[Info]/[Warning]/[Error]` levels and function-tagged messages (`false checkMCStatus: 10060`, `CControlPanel::OnMarkPointBtn …`) — with `EnableLog=1` it also receives `Send Cmd:/Recv Cmd:` frame dumps (04 §9), which makes it the cheapest protocol trace next to tcpdump.

---

## 11. Open questions (prioritised for the port)

1. O1/O3/O4 — tick unit, jog units/index encoding, DO bit mapping, opcode semantics (**blocks milestone 1 and 4**).
2. O2 — status/alarm bit fields (**blocks a safe UI**).
3. O6 — licence: does the card run when the PC never performs the `CDog` exchange? (**blocks viability**; evidence says yes).
4. O7/O8 — slot 3/4 roles, Y dual-drive, limit polarity (**blocks homing**).
5. O9/O10 — ZF and fibre-laser control paths (**blocks fibre cutting**, not CO2).
6. O5, O11–O18 — fidelity/interchange details, closable incrementally.

---

## 12. Implications for the Linux port (summary; the plan is `docs/PORT-PLAN.md`)

* **Replicate exactly:** the parameter model (schema, descriptors, enums, backup flow), the layer/process model and technology library, `.chf` v5 + `scFlie` state files, the MCC100 framing/transaction engine/register map/command vocabulary/FIFO grammar, the planner semantics (junction formula, S-curve, sampling, PWM scheduling), the process sequence per contour, report/break-point files, the string-ID localisation.
* **Replace with libraries:** DXF (ezdxf), NURBS (openNURBS/scipy), offsets/booleans (Clipper2/CavalierContours), nesting (libnest2d/nest2D), text (FreeType/fontTools), UI (Qt), reports (HTML/PDF), Modbus TCP for EC3710 (pymodbus/libmodbus), IPSet (`nmcli`).
* **Needs live capture first:** everything in O1–O10.
* **Drop:** card licence writes, USB dongle, telemetry, NexCut HTTP server, Wine-only helpers, crash-dump/registry/mutex boilerplate, VC6 nesting chain.
