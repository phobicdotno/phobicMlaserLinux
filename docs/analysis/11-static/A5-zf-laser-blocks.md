# A5 — On-board Z-follower (ZF/FTC), register blocks 10000/11000/13000/13200, and the fibre-laser control path (MCC100 IO side + Max Photonics laser side)

Task: close **O5** (blocks 10000/11000/13000/13200), **O9** (ZF protocol on port 999, FTC register semantics, calib export) and **O10** (fibre laser control when `LaserControlType=3`, DA scaling, laser-side register/alarm map) from `NCModule.dll`, `MainApp.exe`, `Lang/lang.txt` and `Pc_Software.zip` alone.

Conventions: **EVIDENCE** = read in the binary/file at the cited address; **INFERENCE (high/medium/low)** = derived. NCModule VAs are image-base `0x10000000` (`.text` file off = VA − 0x10000c00, `.rdata` VA 0x10084000 = file 0x82a00). MainApp `.rdata` VA 0x7bf000 = file 0x3bde00. `param+0xNNN` = the shared settings object returned by MainApp `0x5ff1b0` (`0xa2efa0`); names below were resolved by locating the descriptor `call 0x5ff1b0; add eax,0xNNN` in MainApp (same offsets are used by NCModule through `VM+0x8`). `VM` = `CVirtualMachine` (vtable `0x1008bbc4`), `NC` = `CNCModule` interface (vtable `0x1008a1c4`).

---

## 0. Corrections to earlier documents (all EVIDENCE)

| Earlier claim | Where | What the binary says |
|---|---|---|
| "second `CExtModbus` instance at `0x100aba10`" (00 §5 row 2, 99-gaps §4 O9) | `.data` | `0x100aba08/10/18/1c/20/24/28/2c/30` are nine **static objects whose only initialiser is `mov [addr], 0x10089644`** (initialisers `0x100827c0…0x10082840`); vtable `0x10089644` → RTTI locator `0x1008e488` → type descriptor `0x100ab908` = **`.?AVIModbus@@`**. They are the *interface-typed* statics returned by tiny getters (`0x10027c60: mov eax,0x100aba08; ret`, `0x10027c70: mov eax,0x100aba1c; ret`), not a second card transport. They are handed out by the getters `0x10027c60/70/80/90/d0` (→ `0x100aba08/1c/20/24/2c`) and used as codec/interface singletons (`0x100290b5` reads `0x100aba20`); no `mov …,0x1008965c` exists anywhere in `.text`, so 04 §4.1's "`0x100aba18` = `CStdModbus` global" is not supported by an immediate either. **There is no separate ZF transport object.** |
| `0x1004eff0` = "block 10000/13000 reader `updateMCStatusRO`" (99-gaps §4 O5) | | `0x1004eff0` = **`updateAFStatus`** (auto-focus, log `false updateAFStatus: %d` @`0x1008b370`) and `0x1004f8e0` = **`updateAFProp`** (log `false updateAFProp: %d` @`0x1008b3e8`). Both are gated on the AF link flag `VM+0x3e` and `AF.AFType` (`param+0x4a20`). ZF readers are `0x1004ea50` / `0x1004ed90` (§2). A2 §1 had this right. |
| "port 999 = ZF Modbus link the port must speak" (00 §5, 04 §1) | | Port 999 (`OnBZFPort`, default `push 0x3e7` @`0x1004e8fe` and `0x10058609`) is only ever stored in the **extension-card (EC) endpoint slot `VM+0xd00`/`VM+0xe04`** and connected through `CECNetHalAPI` (`VM+0x30`) when the VM device kind `VM+0x1140` is 5 or 6 (§1.2). *[verifier: `VM+0x30` is the EC channel (`syncRead/WriteECReg`, `updateECStatus/Prop`, `init 3710 ExCard`), gated on `EC.ECType==1`; it is **not** the ZF firmware-download path — see Verification notes V3]* **Every ZF register read/write (10000, 11000, 12002…, 200/201, 0x65 commands) goes through the main card link `VM+0x10` (`CMCHalAPI`, UDP 10.1.1.168:502).** Nothing in NCModule reads ZF status over port 999. |
| A4 §2: register **150 ← 5555 / 9999** = "licence encrypted-area switch" (INFERENCE there) | MainApp | The ZF page also uses both: **factory reset of the FTC** (`0x5fd900`: password dialog (compared with a hard-coded string @`0x86ff30`, `0x5fd866`) → `zf58` confirm → NC slot 61 `0x1002c530` = `writeReg(150, 5555)` → `zf59` (@`0x86ff6c`) "reset success") and **"Write Params"** (`0x5fd200`: per changed row NC slot 32 = `writeSingleReg(11000+2k, value)` (*[verifier: not a vector write, see V5]*) → *if `ZF.ZFType≠0`* NC slot 62 `0x1002c550` = `writeReg(150, 9999)` → `Sleep(500)` → `zf37` "Parameters write successfully"). Other callers: `0x48c486`, `0x48c4f6` (slot 61), `0x48c9b8` (slot 62) in the licence code, `0x5052b1` (slot 61). INFERENCE (medium): 5555/9999 are a **card-side "parameter area" pair** (5555 = restore defaults / unlock, 9999 = commit to flash / lock) shared by the licence and the FTC pages; either way the port must not issue them for CO2 work. |
| A1 row 17 / §4: `0x65 ← [101]` "ZF stop" | | Confirmed, and the full ZF command family is now known (§3). A1's `[118, 5, 0]` (from `0x10053df0`) and the interface slot 63 vector `[118, 0, n]` are two different call sites of the same opcode. |

---

## 1. Where the on-board follower lives in the code

### 1.1 Transports (EVIDENCE)

`CVirtualMachine::CVirtualMachine` = VM vtable slot 0 `0x10058440` reads `File/ipAdd.ini` (`GetPrivateProfileString/Int`, keys in `.rdata` `0x1008b1dc–0x1008c178`):

| VM field | key (default) | consumer |
|---|---|---|
| `+0x8e0` / `+0x9e4` | `CardIP` (10.1.1.168) / `CardPort` (502) | `CMCHalAPI` `VM+0x10` (factory `0x10017e00` → ctor `0x100178a0`, vtable RTTI `.?AVCMCHalAPI@@`) |
| `+0x9e8` / `+0xaec` | `ZFIP` (10.1.1.169) / `ZFPort` (502) | network FTC10 — **unused** (`ZFType=1`) |
| `+0xaf0` / `+0xbf4` | `OnBZFIP` (10.1.1.168) / `OnBZFPort` (**999**) | copied into `+0xd00`/`+0xe04` by the per-kind init (§1.2) |
| `+0xbf8` / `+0xcfc` | if `LGP.LaserControlType`(`param+0x491c`) == 1: `OnBLaserIP`/`OnBLaserPort` (10.1.1.168:888); == 2: `LaserIP`/`LaserPort` (10.1.1.170:10001) | `CLaserNetHalAPI` (`0x10018100`… ctor `0x10017cd0`) — **neither on this machine (type 3 = IO)** |
| `+0x1014`, `+0xf10` | `AFPort`/`AFIP` (888) or `AdvAFPort`/`AdvAFIP` (666, when `AF.AFEnableAdvMCCSerial` `param+0x4a2c`) | `CAFNetHalAPI` `VM+0x2c` (ctor `0x10017a50`) |
| `+0xd00`, `+0xe04`, `+0x1150` | by kind (§1.2): `ECIP/ECPort` (888) · `EC3710IP/EC3710Port` (10.1.1.170:502) · `OnBZFIP/OnBZFPort` (999); `OnBZFMinHardwareVer`/`NCZFMinHardwareVer` (108) | `CECNetHalAPI` `VM+0x30` (ctor `0x10017b80`) |

### 1.2 The per-kind endpoint switch (EVIDENCE)

`0x1004e5a0`: `eax = VM+0x1140 − 1; jmp [0x1004ea2c + 4·eax]` → kind 1 → `0x1004e5b7` (AF endpoints), kind 3 → `0x1004e6d2` (EC / EC3710), **kinds 5 and 6 → `0x1004e7fe`**: `OnBZFIP` → `VM+0xd00`, `OnBZFPort` (999) → `VM+0xe04`, `NCZFMinHardwareVer` → `VM+0x1150`. Then `VM+0x40 = (VM+0x94 ≥ VM+0x1148)` (card program version ≥ minimum) and the sub-device connect calls (`vtbl+0x14` if `LaserControlType≠0`, `+0x10` if `EC.ECType` `param+0x4a40`, `+0xc` if `AF.AFType` `param+0x4a20`, `+0x18` if `SOP.EnableRemoteMonitor` `param+0x3fa9`).

Connect dispatcher `0x10056c00` (VM `connect(kind)`): `VM+0x30 = new CECNetHalAPI` (`0x10017ee0`) and `open(VM+0xd00, VM+0xe04, VM+0x1134, VM+0x1158)` at `0x10056dd2`/`0x10056e42`, result → `VM+0x3f` → `VM+0x3e`. ~~So the *only* thing on port 999 is the RTU-over-UDP sub-port channel of 04 §4.2, opened for firmware download (`fillZFDownFile` …) and the `mf432` ZF upgrade.~~ **[REFUTED by verifier, V3]** `fillZFDownFile` `0x10053170` writes through `VM+0x10` (main card link, `mov ecx,[esi+0x10]` @`0x10053351`), not `VM+0x30`. `VM+0x30` is the **extension-card** channel (slave/sub-address 0xb): `syncWriteECReg` `0x1004b3b0`, `syncWriteECRegs` `0x1004b770`, `syncReadECReg` `0x1004bd50`, `updateECStatus` `0x1004fe10`, `updateECProp` `0x10050180`, `fillECDownFile/fillAFDownFile` `0x100534b0`, `init 3710 ExCard` `0x10053bd0` — all gated on `EC.ECType` (`param+0x4a40`) `== 1`. The endpoint it opens is `ECIP:888` (kind 3), `EC3710IP:502` (kind 1) or `OnBZFIP:999` (kinds 5/6). INFERENCE (high): with `EC.ECType=0` on this machine **port 999 is never opened**, and the port can ignore it; it is an EC link, not a follower link.

### 1.3 Cadence (EVIDENCE for the guards)

`updateZFStatus` `0x1004ea50` starts with `GetTickCount() − [0x100b3be4] < 10 → return true` *[verifier: **dead guard** — `0x100b3be4` is never written (the only byte reference in the file is this `sub` at file off 0x4de88; initial `.data` value 0), so it only fires in the first 10 ms after Windows boot; there is **no** effective 10 ms rate limit, V1]* and `if !VM+0x38 (card connected) → false`; only if `ZF.ZFType` (`param+0x490c`) ≠ 0 it reads. A "time" log (`updateZFStatus time: %d` @`0x1008b2ac`) is emitted when the transaction took > 500 ms (`cmp eax,0x1f4` @`0x1004ec6f`). The same 500 ms threshold exists in `syncReadZFReg` (`0x1004a224`, `0x1004a58c`). The poll period itself is set by the caller (00 §4 LIKELY `ZFCore × ZFUpdateFactor`; `ZFUpdateFactor` key @ file `0x8aa4c`); not re-derived here.

---

## 2. ZF register blocks read through the card

### 2.1 Block **10000 / 18** — `updateZFStatus` `0x1004ea50` (EVIDENCE + names)

Vector `[0x30 READ, 10000 (0x1008a644), 18]` → reply check `0x54` bytes → 18 dwords copied to `VM+0x36c…+0x3b0` (`getReg(7, i)`, NC slot 104). `lang.txt` has **exactly 18 `ZFReadOnly` keys** (`grep -c '^ZFReadOnly' = 18`, `ZFReadWrite` = 10, no `ZFRW*`), so index *i* ↔ `ZFReadOnly(i+1)`. *[verifier, V2: the **`addr` column below (10000+i) is NOT established**; three independent call sites indicate each 32-bit value spans two register addresses, i.e. word *i* ↔ registers **10000+2i** (low) / 10000+2i+1 (high): 10001 → 10002 alarm, 10002 → 10004 run status, 10003 → 10006 run command, …, 10014 → 10028 edge-seek sample count. Reading the whole block with `[0x30,10000,18]` is unaffected; any single-register access must use the doubled address. Confidence: likely.]*

| addr | `getReg(7,i)` | lang id | 中文 | English | consumer (EVIDENCE) |
|---|---|---|---|---|---|
| 10000 | 0 | ZFReadOnly01 | 调高信息 | FTC info | ZF page (`0x5ea…` register monitor) |
| 10001 | 1 | ZFReadOnly02 | 报警状态 | alarm status | NC slot 142 `0x1002bf50` = `getReg(7,1) & 0xffff` → `gp33..gp43`, `gp141/217/218` (A2 §5.2) |
| 10002 | 2 | ZFReadOnly03 | 运行状态 | running status | VM `0x10038fe0` (NC slot 141): bit 31 clear → state 5 EStop; `(s & 0xff) == 4` → 2 Drill |
| 10003 | 3 | ZFReadOnly04 | 运行命令 | run command (echo of last 0x65 opcode) | same: 102→6, 103→4, 104→1 Follow, 105→0 Ready, 106→8, 107→7 |
| 10004 | 4 | ZFReadOnly05 | 信号强度 | signal strength (capacitance) | ~~VM slot 62 `0x10039080` reads it directly and returns bit 31~~ *[verifier: slot 62 does `syncReadZFReg(0x2714=10004)` and tests bit 31 — under the 2-address model that is word 2 (run status) bit 31, the same bit `0x10038fe0` tests on `getReg(7,2)`]* |
| 10005 | 5 | ZFReadOnly06 | 信号高度 | signal height (mm, calibrated) | ZF page `zf51` |
| 10006 | 6 | ZFReadOnly07 | 轴位置 | Z axis position | `zf7` "Z Coord(mm)" |
| 10007 | 7 | ZFReadOnly08 | 当前速度 | current speed | `zf61` |
| 10008 | 8 | ZFReadOnly09 | 跟随误差 | follow error | `zf65/69` |
| 10009 | 9 | ZFReadOnly10 | 跟随时间 | follow time | `zf63` |
| 10010 | 10 | ZFReadOnly11 | 板材坐标位置 | sheet (plate) coordinate | |
| 10011 | 11 | ZFReadOnly12 | 实时信号强度 | real-time signal strength | `zf80` |
| 10012 | 12 | ZFReadOnly13 | 信号补偿值 | signal compensation | `zf62` |
| 10013 | 13 | ZFReadOnly14 | 软下限位坐标 | soft lower-limit coordinate | `zf77/78` |
| 10014 | 14 | ZFReadOnly15 | 巡边采样数据条数 | edge-seek sample count | (MainApp reads `getReg(7,14)`, A2 §8) |
| 10015 | 15 | ZFReadOnly16 | 巡边偏差最大点位 | max edge-seek deviation point | |
| 10016 | 16 | ZFReadOnly17 | 最后巡边记录位置 | last edge-seek record position | `zf82` |
| 10017 | 17 | ZFReadOnly18 | 其它信息 | other info | |

Units: INFERENCE (medium): heights/positions in 0.001 mm and speeds in 0.1 mm/s, because every command the PC sends to the follower scales heights ×1000 and speeds ×10 (§3), and A1's resume logic compares status words against `ZFUpSpeed·10`.

### 2.2 Block **11000 / 39** — `updateZFProp` `0x1004ed90` (EVIDENCE)

Gate `ZF.ZFType≠0`; **side effect before the read**: `param+0x49f0 = 20000 (0x4e20)` and `param+0x49f4 = 200 (0xc8)` (`0x1004ede0/0x1004eded` — two ZF fields forced to constants; no descriptor uses these offsets, so they are runtime-only limits, INFERENCE low: max Z speed / max accel; *[verifier: MainApp `0x443a6f/0x443a86` passes `param+0x49f4`×0.001 (`0x7d0eb8`) and `param+0x49f0` as numeric arguments to NC slot `+0x2dc` together with `param+0x49c8` and a height — a Z motion call]*). Vector `[0x30, 11000 (0x1008a648), 39]`, reply `0xa8`, copy to `VM+0x3b4…+0x44c` = `getReg(8, i)`.

`lang.txt` names only ten of the 39 words (`ZFReadWriteNN`, EVIDENCE lines 3251-3260; index ↔ word INFERENCE medium, 1-based like `ZFReadOnly`): 08 精回原速度 fine-homing speed · 11 标定粗速度 calibration coarse speed · 12 标定精速度 calibration fine speed · 13 标定减速信号变化 calibration decel signal change · 18 最大有效信号 max valid signal · 24 电容异常变大门限 capacitance-abnormal-increase threshold · 30 信号异常为零告警使能 zero-signal alarm enable · 31 信号动态修正 dynamic signal correction · 33 信号补偿系数 signal compensation coefficient · 38 随动减速时间 follow deceleration time. The remaining property names are the `pd426–pd443`, `pd542–pd565`, `pd650–pd656`, `pd729–pd759-1` "调高器参数.*" labels (follow height/tolerance, wait height, drill type/height/speed, touch-plate signal/delay/lift, alarm lift, screw pitch, pulses/rev, servo/encoder direction, soft limits, KP/KI/low-pass for jog and follow, …) — which word is which needs the ZF property page's list order (its rows are built from `getReg(8,·)` in MainApp `0x5f7…`; not traced further). **Write path** *[corrected by verifier, V5]*: "Write Params" (`zf1`, MainApp `0x5fce00`) iterates the changed rows; for row *k* (`k = atoi(row+0x5c)` via `0x417390`, the same *k* the page reads back with `getReg(8,k)` @`0x5fc3fc–0x5fc47e`) it converts the cell by row type (`row+0x58`: int / float × `row+0xac` scale / bool) and, *only if `ZF.ZFType≠0`*, calls NC slot 32 `0x1002b570` → VM slot 27 `0x100494e0` = **`writeSingleReg(addr = 11000 + 2·k, value)`** = `[0x40, addr, 1, value]` (`lea eax,[edx+edx*1+0x2af8]` @`0x5fd1fc`); then reg 150 ← 9999 (§0). Note the page clamps row *k* = 82 (`cmp …,0x52` @`0x5fc492`) — a *k* ≥ 39 would index past the 39-word copy into the AF block (`VM+0x450`), so the row-*k* ↔ reply-word mapping is itself only LIKELY. Live check: read 11000/39 once, change one FTC property in the Windows UI, read again.

### 2.3 Calibration table **12002…12801** — VM slot 51 `0x100428b0` (EVIDENCE)

Loop `addr = 0x2ee2 (12002); … ; addr += 100 while addr < 0x3202 (12802)` = **8 reads × 100 words**; each reply is decoded as 16-bit halves: for every 4 words `A = w[i] | w[i+1]<<16`, `B = w[i+2] | w[i+3]<<16`; pairs `(A,B)` are appended to the vector at `VM+0x784` → **200 pairs** *[verifier, V6: decoding starts at reply offset **+8** (`[eax+esi+0x8]`, `A=(w[+0xc]<<16)+w[+8]`, add not or) and yields `(len/4−2)>>2` pairs per read, whereas `updateZFStatus`/`checkMCStatus` skip a 3-dword header (+0xc). "200" holds only if each 100-register reply is 102 dwords; otherwise the first low half is the echoed count. Downgraded to: **up to** 200 pairs, header alignment needs capture. The function is not gated on `VM+0x38` or `ZFType`]* NC slot 110 (`0x1002bad0` → VM +0xcc) returns that vector; MainApp `0x5fdb30` (button in the ZF page, guarded by NC slot 13 "ZF connected") writes it to **`<app>\Help\calib.csv`** (`0x86ff78`), mode `"w+"`, row format **`"%d, %d\n"`** (`0x86ff8c`), then `ShellExecute("explorer.exe", "<app>\Help\")`. INFERENCE (medium — *verifier downgrade from high; nothing in the binary names A/B*): this is the follower's **capacitance-sample → height calibration curve** (A = raw signal, B = height in 0.001 mm) produced by 浮头标定 head calibration (§3); words 12000/12001 (not read) are LIKELY count/status. That closes 99-gaps §2.3 / open item 2: the row is `(signal, height)`; the port does not need it to cut, only to reproduce the "Calib Datas" viewer.

### 2.4 "Test data" window **10028 + registers 200/201** — VM slot 55 `0x1003d790` (EVIDENCE)

~~`[0x30, 0x272c (10028), 2]` → N; for `i < N`: `[0x40, 201, 4, …]` then `[0x30, 200, 4]` … at most 240 records.~~ **[corrected by verifier, V7]** `[0x30, 0x272c (10028), 2]` → `N = (w[+0xc]<<16) + w[+8]`; `idx = 0`; loop while `idx < N`: **`[0x40, 201, 1, idx]`** (`0x1003d96f/0x1003da4c/0x1003db29/0x1003dbf4`) then **`[0x30, 200, n]`** with **`n = min(240, (N−idx)/4)`** (`sar eax,2; cmp eax,0xf0` @`0x1003dc2e`), reply must be exactly `n+3` dwords (`sub edx,3; cmp` @`0x1003df0e`), `n` words from `+0xc` are appended to `VM+0x794`, `idx += 4·n` (`lea eax,[edx+ecx*4]` @`0x1003e00d`). So N and idx are **byte offsets**, each window read returns up to **240 32-bit words (60 rows)**; both HAL calls pass extra args `(4, 0)` instead of `(0, 0)`. NC slot 114 (`0x1002bb50` → VM +0xdc) hands it to MainApp `0x5fde00`, which asks for a file (`csv files(*.csv)|*.csv||` @`0x86ffa8`), writes rows **`"%d, %d, %d, %.3f\n"`** (`0x870008`: three ints + the 4th word reinterpreted as float, `fld DWORD PTR` @`0x5fe068`) and reports `测试数据导出成功` "test data exported successfully" (`0x87001c`). INFERENCE (medium): the edge-seek / follow test log (`ZFReadOnly15..17` name the same data: sample count, max deviation, last record). *[verifier: 10028 = 10000 + 2·14 = exactly ZFReadOnly15 "巡边采样数据条数" under the 2-address model of V2 — supports both readings]* Registers **200/201 are a generic "indexed data window"** on the card (write index → read record).

### 2.5 Other ZF accessors (EVIDENCE)

* `syncReadZFReg(addr, int* out)` = VM slot 31 `0x1004a040` (NC slot 36, `+0x90`): `[0x30, addr, 2]` → `out = w[0] | w[1]<<16` (a 32-bit value spread over two 16-bit registers). `syncReadZFReg(addr, {count,sub}, vector*)` = VM slot 30 `0x1004a340` (NC slot 35, `+0x8c`): `[0x30, addr, count&0xffff, (int16)sub]` → copies `n−1` words. Both log `false syncReadZFReg: %d` (`0x1008ab10`) / `syncReadZFReg time: %d` (`0x1008aae0`) and are gated on `ZFType≠0`. MainApp uses them from the ZF page (register monitor rows `zf35…zf82`).
* `.zpf` parameter files (`zf102/zf103` import/export, `0x870068` "zpf", `0x5fe150`) are a PC-side dump of the 11000 block, not a card feature.

### 2.6 Blocks **13000 / 13200 = auto-focus (AF), not ZF** (EVIDENCE)

| block | function | gate | vector | destination |
|---|---|---|---|---|
| 13000 / 14 | `updateAFStatus` `0x1004eff0` (VM slot 84) | `VM+0x3e` AF connected; `AF.AFType` (`param+0x4a20`) selects the path | `[0x30, 13000 (0x1008a64c), 14]` (reply `0x44`) for AF types ≠ 2; type 2 = std-Modbus `1000/28` (`0x1008a654`) | `VM+0x450` = `getReg(9,·)` (NC slot 144 = `getReg(9,4)` AF alarm word); type 7 unpacks 16-bit halves into `VM+0x4dc` (33) and `VM+0x560` (15) = groups 13/14 |
| 13200 / n | `updateAFProp` `0x1004f8e0` (VM slot 85) | same; type 0/5/6 → no-op, type 3 → VM slot 87, type 7 → `[0x30, 13200 (0x1008a650), n]` with n from the double 7.5 @`0x1008b418` (n = 2·round(7.5·x), not traced), types 1/2 → std-Modbus FC3 `2000` through `VM+0x28`/`VM+0x2c` | `VM+0x488` = `getReg(10,·)` (21 words) |

On this machine `AF.AFType=0` (01 §2.4) → **neither block is ever read**; MainApp only consumes them in the AF page (`af*` labels) and the AF alarm test of A2 §7. O5 for 13000/13200 is closed as "AF status/property, unused here".

---

## 3. ZF command set on register 0x65 (EVIDENCE)

All built in `0x100312a0–0x1003179d` (CNCModule slots 60, 63–66, 68) and written with **NC** slot 31 `0x1002b590` (`vtbl+0x7c` → VM slot 26 `0x100497d0`, vector write) or NC slot 32 `0x1002b570` (`vtbl+0x80` → VM slot 27 `0x100494e0` = `[0x40, addr, 1, value]`); constants `0x10086eb8 = 10.0`, `0x10089c78 = 1000.0`. **[verifier, V8] every `round(…)` below is actually truncation toward zero**: the helper `0x10060c80` is MSVC `_ftol2` (`cvttsd2si`). Consequence: e.g. h = 2.01 mm → `2.01·1000 = 2009.999…` → **2009**, not 2010 (18 of the 2000 values 0.00…19.99 mm are affected); the port must truncate the IEEE-754 double product exactly the same way:

| NC slot (offset) | function | vector | called by (MainApp) |
|---|---|---|---|
| 58 (`0xe8`) | `0x1002c510` | `0x65 ← [101]` | Stop button / stop-manu (A1 row 17); ZF page "cancel head calibration" `zf27` (`0x5f7afc`, `0x5f7b88`) |
| 59 (`0xec`) | `0x1002c520` | `0x65 ← [102]` | re-sync (A1) — echoed as status 6 |
| 60 (`0xf0`) | `0x100312a0` | `0x65 ← [107]` (single word; the vector `[107, round(ZF.ZFDockHeight·1000)]` is built at `0x100312de–0x1003130b` but discarded) | **浮头标定 head calibration** (`zf26` confirm → `0x5f79bf`, `0x5f7ad8`; *[verifier: the button is a toggle — if `getReg(7,3)==107` it sends `[101]` instead (`0x5f7990`); with a non-zero handler argument `[107]` is sent **without** the prompt (`0x5f79a3`)]*); echoed as status 7 (`zf12` "Head Calibration Running") |
| 63 (`0xfc`) | `0x10031360` | `0x65 ← [118, 0, arg]` | *[verifier: 118 is a generic sub-command opcode — `[118,5,0]` at `0x1002f59c`, `0x10056fac`, `0x10057764` only when `SP.m_iEnableLaserType==0`, and `[118,7,…]` via VM `+0x70` at `0x10040c03` when `VM+0x42c==1`]*; A1 saw `[118, 5, 0]` from `0x10053df0` (stop-manu, fibre) |
| 64 (`0x100`) | `0x10031410` | `0x65 ← [109, round(v·10), arg2]` | (109 also appears in-stream as "dwell after laser off", A1/04) |
| 65 (`0x104`) | `0x100314d0` | `0x65 ← [104, round(v·10), round(h·1000)]` | **follow at height h with speed v** — echoed as status 1 *Follow* (`mp13`) |
| 66 (`0x108`) | `0x100315a0` | `0x65 ← [103, round(v·10), round(h·1000)]` | **move Z to absolute height h at v** — Stop/Resume lift `[103, ZF.ZFUpSpeed·10, MP.ZFSafeHeight·1000 | ZF.ZFDockHeight·1000]` (A1 rows 6/17) |
| 68 (**`0x110`**; *`0x10c` is slot 67 `0x1002c570` → VM `+0x204`*) | `0x10031670` | *[verifier, V9]* `0x65 ← [117, t(d1·10), t(d2), t(d3), t(d4), i5, i6, t(d7·10), i8]` — 9 words (`ret 0x34` = 4 doubles + 2 ints + 1 double + 1 int) | (jog/pierce family, caller not traced) |
| 61 / 62 (`0xf4`/`0xf8`) | `0x1002c530` / `0x1002c550` | `writeReg(150, 5555)` / `writeReg(150, 9999)` | FTC factory reset (`zf56/58/59`) / commit after "Write Params" (`zf37`) — §0 |

Status echo (VM `0x10038fe0`, A2 §6.1) maps command 105 → *Ready*, 104 → *Follow*, 103 → *JogDown*(4), 102 → 6, 106 → 8, 107 → 7; `(status10002 & 0xff) == 4` → *Drill* (pierce); bit 31 of 10002 clear → *EStop*. Command 105/106 emitters were not found in NCModule (probably card-internal transitions).

### 3.1 Follow / pierce state machine as far as static evidence allows

```
            [101] stop / e-stop                 status10002.bit31 == 0
  ┌───────────────────────────────────────────────►  EStop (5)  ──(homing done)──┐
  │                                                                              ▼
Ready (0) ──[104, v·10, h·1000]──► Follow (1)  ── card lowers to calibrated height h using
  ▲   ▲                                │            the 12002.. curve; alarms 10001 bits 0-13
  │   │ [103, ZFUpSpeed·10, h·1000]    │  in-stream pierce: (status10002 & 0xff)==4 → Drill (2)
  │   └──── lift to h (dock/safe) ◄────┘  (5-stage pierce heights DrillHeight{k} + gradual ramp
  │                                          are PC-planned: opcodes 103/109/117 family in the FIFO —
  │  [107] ─► head calibration (7) ─► writes 12002..12801, then Ready      NOT captured statically)
  └── [102] re-sync (6)
```

EVIDENCE: the transitions labelled with opcodes; INFERENCE: everything about *how the card* performs the pierce sequence (heights per stage, `ManuType`, gradual pierce, frog jump). Frog jump: NCModule has classes `CFrogJumpSvr` / `CFJSAccSrv` (RTTI `0xa9914`) and the layer/global parameters `pd103` 跟随控制.使用蛙跳式上抬 "use frog-style lift", `pd1004` 蛙跳模式 (normal/advanced `pd1002/1003`), `pd1300` frog-jump touch protection, `pd291` `FCP.FrogJumpMinHeight=10` — i.e. the arc-shaped lift between contours is **planned on the PC** and streamed as Z moves; its FIFO encoding is not in the CO2 log (08) and must come from capture G.

---

## 4. What the UI consumes (EVIDENCE, for the port's ZF panel)

* NC slot 13 (`0x1002b450`, alias of slot 12 card-connected) = "ZF connected"; slot 141 state → `mp12…mp17`; slot 142 alarm word → `gp33…gp43/141/217/218`; slot 104 raw `getReg(7,·)` for the monitor rows `zf5…zf82`; slots 35/36 `syncReadZFReg`; slot 110 calib pairs (`zf101` "Calib Datas", `Help\calib.csv`); slot 114 test records; slot 32 property write; slots 58/60/61/62/65/66 commands; the page strings `zf0…zf107` (refresh/write params, product model, sw/hw version, run status, signal strength, alarm state, DO state, head/servo calibration and their results 优/良/差, FTC restart/reconnect `zf33/34/38/39`, factory reset `zf56–59`, import/export `.zpf`).
* Servo calibration (`zf31`, `0x5f8054–0x5f81e3`) and the up/down jog buttons write through NC slot 90 (`0x10034c30`, jog) with `param+0x9bc0/0x9bc4/0x9bc8` (dialog fields) — not traced further.

---

## 5. Fibre laser, MCC100 side (`LGP.LaserControlType=3` = IO) — EVIDENCE

Parameter offsets resolved from MainApp descriptors: `param+0x491c` = **`LGP.LaserControlType`** (`pd259`), `+0x4918` `LGP.LaserType`, `+0x4920` `LGP.LaserDAPort` (=1), `+0x4924` `LGP.LaserDAType` (=0 → 0–10 V), `+0x4930` `LGP.LaserMaxPower`, `+0x4938` `MP.LaserDAKeepOutput`, `+0x6c` **`LGP.DOLaserGate`** (=5), `+0x94` `LGP.DORedLight` (=6; *verifier: descriptor confirmed, key @`0x881780` → `add eax,0x94`*), `+0x83c` `LGP.CO2DOLaserGate`, `+0x864` `LGP.CO2DORedLight`, `+0x88c` `LGP.CO2DOLaser`, `+0x4940/+0x4944` `LGP.CO2LaserControlType/CO2LaserDAPort`, `+0x46d8` `SP.m_iEnableLaserType`.

| NC slot | function | fibre branch for `LaserControlType` = 3 (jump table `0x1002d8b4`: 1→`0x1002d843` Raycus adapter, 2→`0x1002d821` IPG adapter, **3→`0x1002d7d2`**, 4→`0x1002d843`) | CO2 branch (`m_iEnableLaserType==1`) |
|---|---|---|---|
| 88 (`0x160`) | `0x1002d730` **laser on (manual)** | `DO[DOLaserGate] := 1` via slot 79 (*[verifier, V10: `0x65 ← [9999, 2, 1<<(port−1), (v&0xff)<<(port−1)]` — a mask/value bit-field, **not** `[9999,2,port,1]`; DO5 on = `[9999,2,0x10,0x10]`, off = `[9999,2,0x10,0]`; ports 11–26 → `[9999,13,mask,val]` only if `param+0x4d58≠0`]*), then `setDA(LaserDAPort, LaserDAType, power)` with `power = param+0x4840` = **`LC.PtLaserPeakCurrent`** (int %, `pd96` 点射峰值功率, =20 here; *verifier*) (× 0.95 if `LaserType==7` GZ); sets `param+0x49a5 = 1` | if `CO2LaserControlType≠0`: `DO[CO2DOLaserGate] := 1`; `param+0x49a6 = 1` |
| 98 (`0x188`) | `0x1002dda0` **laser off** | `DO[DOLaserGate] := 0`; unless `MP.LaserDAKeepOutput`: `setDA(LaserDAPort, LaserDAType, 0.0)` | `DO[CO2DOLaserGate] := 0`; `param+0x49a6 = 0` |
| 89 (`0x164`) | `0x1002d8d0` laser off + DA 0 (variant used by e-stop) | same as 98 | `setDA(CO2LaserDAPort, ·, 0)`, `DO[CO2DOLaserGate] := 0` |
| 93 / 94 (`0x174`/`0x178`) | `0x1002da70` / `0x1002db70` **red pointer on/off** | `DO[DORedLight] := 1/0` (+ IPG `ABN/ABF` only when type 2 & IPG) | `DO[CO2DORedLight] := 1/0` |
| 85 (`0x154`) | `0x10030180` **`setDA(port, type, value)`** | `word = trunc(value × {type0: 100, type1: 50, type2: 40})`, *any other type → word 0*; if `1 ≤ word ≤ 49` → `word = 50`; `0x65 ← [9999, 4, port−1, word]`; *port must be 1 or 2, otherwise nothing is sent* (`lea eax,[edi-1]; cmp eax,1; ja` @`0x100301aa`) *[verifier]* | same |

INFERENCE (high) on **DA scaling**: the three multipliers give 10000 / 5000 / 4000 for `value = 100`, i.e. the DA word is in **millivolts** for 0–10 V / 0–5 V / 0–4 V analog types and `value` is **percent of full scale**; the 50 mV floor avoids a dead-band. `DA.DA1OutputAdjustVal=3648` (01 §1.1) is the card-side 12-bit calibration of that channel. So at `LaserMaxPower` % the card puts 10.0 V on DA1, which the Max Photonics laser scales with `AnalogPowerCoefficient` (§6.4).

In-stream (job) use of the same lines: the CO2 capture (08 §4.4, A1) shows `9999[2, mask, v]` DO records and `2001[0x03000002, 20000]` PWM config per contour; for a fibre job the prologue must add the DA power record (`9999[4, 0, mV]`) and the ZF opcodes of §3 — **not in any log; capture G** (§8). Alarm input: `DI1 = laser alarm` (01 §2.8) is read from block 1000 word 4 (A2).

---

## 6. Fibre laser, laser side — Max Photonics `LaserApplication_SC` (Pc_Software.zip) — EVIDENCE from `ModbusDefines` constants (dnfile Constant table), `default.xml`, `Control1.xml`, and dncil IL of `MyMonitor.UpdateMcu / UpdateFPGA / Read_LED1_Call / Read_LED2_Call`

### 6.1 Link

* .NET 4.5 WPF, NModbus4; `ModbusFactory.CreatRtu` / `CreatTcp` (`eModbusType RTU=0 / TCP=1`, `ConnectType COMM=0 / TCP=1`); slave id from `Login.xml` **`HandAddress=25`** (`ModbusDefines.SlaveAddress`); TCP default `File/config.xml` `IPName1=192.168.0.178`, `MachineModel=2`; `ZLAN` class = ZLAN serial-server discovery (UDP, `Config_Net_Module`). Handshake constants: `StartValue=0x5A00`, `RightControlValue=0xABCD` (written to reg 39 `CMD_RightControl` by `WriteRightControl`, cleared by `WriteOffRightControl`), `CMD_Handshake` = reg 25.
* This is a **service/diagnostic** channel: Mlaser does not implement it (NCModule RTTI has only IPG/Raycus adapters, 99-gaps §1.2). The cutting-time interface is the IO connector (§6.5).

### 6.2 Holding-register map (`CMD_x` = address, `CMD_xLen` = count; all EVIDENCE)

| addr | count | name | meaning (from name / IL) |
|---|---|---|---|
| 0 / 1 / 2 | 1/1/2 | MCUVer0 / MCUVer1 / MCUVersion | MCU firmware version |
| **4** | **76** | **MCU_CallALLData** | **monitor block A** (polled; `AddData_0004` queue; `UpdateMcu` requires `len==76` and `w[0]==0x5678`) |
| 5 | 1 | MCU_CallALL | trigger for block A |
| 6 | 11 | Discharge | run-time/discharge counters (`Read_DischargeAll`: words 10/11 driver current, 18/21, 54–59 date/time, 69/70 hours) |
| 7 / 8 / 9 | 1 | PumpSource / OpticalModeT / ElectricalModeT | pump-source enable mask, optical/electrical temperatures |
| 11 | 4 | drv | driver-board currents 1–4 |
| 15 / 16 | 1 | MainControlBoardH / T | humidity / temperature |
| 25 / 26 / 27 | 1 | Handshake / WaveNume / LaserWaveNume | |
| 37 | 1 | **ClearAlarm** (write) | `myEditButton_ClearAlarm_Click` |
| 39 | 1 | RightControl | 0xABCD = take control |
| 49 | 1 | ClearRunTime | |
| 50 / 59 / 60 | 9/1/2 | Version_SN / PN / Machine_Date | serial no. (also 176/9 `Main_SN`, 185 `Main_Type`, 186 `Main_Number`) |
| 62–69 / 71–78 | 7 | Manufactor_Encryption / Customer_Encryption | time-lock (alarm bits below) |
| 80 / 81 / 82 / 83 / 84 | 1 | Laser_type / **Laser_LightMode** (turn-on mode `Read_TurnonLight`) / LASEROperating_mode / **REDOperating_mode** (`Read_ComboxRedType`) / Laser_Modular | operating-mode selectors |
| 85 | 1 | Call_the_police = alarm-enable mask `Enable0x0055` | |
| 86 / 87 / 88 | 1 | Laser_Dynamic / Staticstate / StaticstateDate | compensation |
| **89** | 1 | **Max_Electric** = `MaxCurrent` (36 A in `default.xml`, limit 40) | `Read_PwmPower` |
| 90 | 1 | QBH_AlarmEnable mask `Enable0x005A` (bits 16…2048 per `Read_QBH_AlarmEnable`) | |
| 91 / 171 | 1 | Min/Max_ReservedPulse | |
| 92 / 93 | 1 | PowerAll / TallLowlevel | |
| 94 / 104 / 114 | 10/10/20 | ModuleEnum_Alarm / _QCW / ModuleDnum_Alarm | multi-module |
| 133–136 | 1 | ModuleDnum / MaxPulse / MaxPulsePower / MaxPeakValue | |
| 138 / 220 | 1 | **RedCurr** (`RedLightCurrent=90`) / MaxRedCurr | guide-laser current |
| 139 / 140 | 1 | Compensate1PD / 2PD (`PD_CompareVoltage1/2`) | |
| 141–149 | 1 | Humidity / Current / GTemperature / HTemperature / LightT / MainT / Main_QBH / Main_OutShut alarm thresholds (`DumidityMaxLimit=20`, `LightMaxTValue=35`, `CircuitMaxTValue=35`, `PumpSourceMax/Min=48/18`, `QBHMaxTValue=35`, `OutShutMaxTValue=20`) | |
| 150 | 1 | LaserPowr | power setpoint (`Read_CMD_LaserPowe`) |
| 151 | 3 | PWMpL | PWM frequency / duty (`label_pwmPl`, `lalbel_pwmzkb`) |
| **154** | 1 | **AnalogPower** = `AnalogPowerCoefficient` (**100**) — label `模拟量系数(0-10V)` "AnalogCoeff (0–10 V)" | `Read_AnalogPower` |
| 155 / 156 / 157 | 1 | pumpOpenOff / PDAnalog (`PDAnalogEnable=1`) / SW_Resist | |
| **158** | 1 | **AnalogOffset** = `AnalogOffsetValue` (**13**) — label `0-10V模拟量补偿值` "0–10 V analog compensation" | |
| 159 / 160 / 161–164 | 1/1/4 | WaterflowCoeff / Waterflowmeter / Vmin,Vmax,Fmin,Fmax (1.5 V/5 V ↔ 2/16 L·min⁻¹) | |
| 165–170 | 1 | PDAlarmTime (1000) / ReservedConfig / **ClearPDLockAlarm** / PowerOffsetLimit / PowerDelay (5000) / DblOffsetV | |
| 172–175, 274–280 | 1 | fan control (FanAll 42 °C/100 %, FanHalf 35 °C/50 %), TempProtPower*, Full/LowPowerTemp | |
| **221** | 1 | **IO_Enable** — label `IO-使能/调制屏蔽` "IO enable/modulation shielding" (tip: default 禁止屏蔽 = no shielding; `IO_ControlLevelIndex=1`, `EN_EnableIndex=1`, `START_EnableIndex=0` in `default.xml`) | `Read_IOEnableConfig` |
| 222 / 223 / 224 | 1 | TimeUp / TimeDown / TimeEnable — slow-up/slow-down ramps (`SlowUpEnable/SlowDownEnable`) | |
| 225 / 226 | 1 | SwingWidth / SwingFreq | wobble |
| 227 / 228 | 1 | SlowUpStartPower / SlowDownEndPower | |
| 229 / 230 / 231 / 232 | 1 | PowerFlash / CoeffFlash / LinePower / LinePowerDelayTime | |
| 240–245 | 1 | PowerAlarmTypeIndex / PowerUp/DownLimit / DelayUnit / OpenDelayTime / CloseDelayTime | |
| **246** | 6 | PwmDelay (`CMD_PwmDelay` = U2 constant `0x00f6`, *verifier*; `Per20…Per70_Delay` = 1.1…6 in `default.xml`) | |
| 256 | 10 | AnaloyCalibrate (`Per10…Per100_CalibrateValue` = 100…1000, i.e. 0–10 V linearisation; `OutAnaloyCoeff=105`) | |
| 266 | 1 | SlowUnit | |
| 281 / 282 | 1 | WireFeedDelay / CloseAirDelay | |
| **284 / 285** | 1 | **IO_PWM_Mode** (`IO__PWM模式设置`, tip default 电平触发 = level-triggered) / **IO_PWM_Width** (`IO__PWM沿触发宽度` edge-trigger width, ms) | `Read_IO_PWM_Mode` |
| 286 / 287 | 1 | P_Setting / P_Save | parameter save |
| 512 / 513 | 21/13 | WhiteWaveForm / SetEncrypt | |
| 544 | 1 | ClearFlash | |
| 4095 | 1 | MCU_Restart | |
| 0x8000–0x8002 | 1/1/2 | CPLD_Ver0 / Ver1 / VerFPGA | |
| **0x8004** | **22** | **CPLD_ALLFPGA = monitor block B** (`UpdateFPGA` requires `len==22`) | |
| 0x8005 / 0x8006 | 2/1 | CPLD_CallAll / **CPLD_IOCall** (IO state read-back) | |
| 0x8007 / 0x8008 / 0x8009 | 1 | PD_ADC / PD_NotADC / CPLD_ADCPD | |
| 0x800a–0x800f | 1/5/1/1/1/1 | CPLD_ERR / Cycle / Energy / Width / WidthAll / min_cycle | QCW pulse data |
| **0x8022** | 1 | **CPLD_LaserOFFON** — `OpenLaser` writes 1.0, `CloseLaser` 0.0 (software laser enable) | |
| **0x8023** | 1 | **CPLD_RedOFFON** — `Write_LaserOff` writes 0 to 0x8023 then 0x8022 | |
| 0x8024 | 2 | CPLD_TIME | |
| **0x802d** | 1 | **CPLD_EMG** — `myEditButton_EMG_Click` writes 1.0 ("SW EMG Stop" / 上位机急停) | |

### 6.3 Monitor blocks and alarm bits (EVIDENCE, IL)

Block A (`0x0004`, 76 words): `w[0] == 0x5678` valid flag; **`w[1]` = MCU alarm word**: bit0 泵源温度 pump-source temperature · bit1 光路温度 optical-path temperature · bit2 电路温度 circuit temperature · bit3 厂家加密到期 manufacturer encryption expired · bit4 客户加密到期 customer encryption expired · bit5–8 驱动板1–4路电流 driver-board 1–4 current · bit9 QBH温度 QBH temperature · bit10 外合束器温度 combiner temperature · bit11 水流量计 water-flow meter · bit12 湿度 humidity (LED `led1_HumidityAlarm`); `w[60]/w[61]` are read with the encryption LEDs; `w[74]` used by `Read_DischargeAll`.

Block B (`0x8004`, 22 words): **`w[1]` = FPGA alarm word** (`Read_LED1_Call`): bit0 内部PD1 PD1 · bit1 内部PD2 PD2 · bit2 QCW脉冲能量 QCW pulse energy · bit3 QBH触点 QBH contact · **bit4 F1急停 F1 EMG** · bit5 水压开关 water-pressure switch · **bit6 互锁F1 interlock F1** · bit7 QCW功率 QCW power · bit8 MCU报警 MCU alarm (summary of block A) · bit9 外合束器PD combiner PD · **bit10 互锁F2 interlock F2** · bit11 出光异常 abnormal emission · **bit12 上位机急停 SW EMG** · bit13 功率报警 power alarm (`A功率/P功率` = analog / PWM power) · **bit14 F2急停 F2 EMG**. **`w[2]` = status word** (`Read_LED2_Call`): bits 0–7 drive state LEDs (bit0…bit7 tested with masks 1,2,4,8,16,32,64,128 — names not in strings; INFERENCE: laser-on, red-on, EN, START, PWM present, analog-mode, ready, …), bit10 `pwm_start_err`, bit11 `PD_PWR` alarm, bit12 PD锁机 PD lock. Words 0, 3–7, 12–21 feed the power/energy/time fields (`出光次数` emission count from w[18..21]).

`Control1.xml` `<Tag>` numbers (0…109, 99-gaps §1.2) are **UI element ids, not bit numbers** — the bit→LED mapping is the one above (e.g. Tag 103 `F1__EMG` ↔ FPGA bit 4, Tag 7 `F1__Interlock` ↔ bit 6, Tag 26 `F2__Interlock` ↔ bit 10, Tag 27 `F2__EMG` ↔ bit 14, Tag 104 `SW__EMG` ↔ bit 12, Tag 20 `pwm__start__err` ↔ status bit 10, Tag 101 customer-encryption ↔ MCU bit 4, Tag 102 factory ↔ MCU bit 3). The error log `bcx/Laser/Log/ERR_LOG/MQCSCFAD1519_*.xls` (NPOI) records these strings.

### 6.4 Analog scaling (EVIDENCE values, INFERENCE formula)

`default.xml`: `AnalogPowerCoefficient=100` (reg 154, "AnalogCoeff (0–10 V)"), `AnalogOffsetValue=13` (reg 158, "0–10 V analog compensation"), `outAnaloyCalibrate` 10 points `Per10…Per100 = 100…1000` (reg 256/10 — a straight line, i.e. **10 V ↔ 100 %** with no correction), `OutAnaloyCoeff=105`, `MaxCurrent=36` A (reg 89; `MaxCurrentLimit=40`), `powerOutLine` current-vs-percent table (10 % → 6.8 A … 100 % → 31.05 A) and `powerTestLineVerity` measured watts (5 % → 100 W … 100 % → **3290 W**: this parameter set belongs to a 3 kW-class unit `MQSC23040998`, while the error log names `MQCSCFAD1519` — the machine's 1200 W source; 99-gaps open item 7 stands). INFERENCE (high): `P% = clamp(V_analog / 10 V × 100 × AnalogPowerCoefficient/100, 0, 100)` with the offset register removing the DA zero error; `MaxCurrent` caps the pump current. The MCC100 DA word (§5) is therefore `mV = P% × 100` for `LaserDAType=0`.

### 6.5 IO interface model (what the MCC100 lines must drive) — EVIDENCE for the signal names (LED strings, register labels, `alarmEnable`), INFERENCE (high) for the mapping to MCC100 ports

| Laser signal | Direction | Laser-side evidence | MCC100 side (01 §2.5/§2.8, §5) |
|---|---|---|---|
| **EN** (laser enable, 使能) | in | `EN_EnableIndex=1`, reg 221 "IO enable/modulation shielding", status bit; software mirror reg 0x8022 | **DO5 `LGP.DOLaserGate`** (`0x65 ← [9999,2,0x10,0x10]` on laser-on, *verifier V10*) |
| **PWM / modulation** (调制) | in | reg 284 mode (level / edge), 285 edge width, `pwm_start_err` status bit 10, PWM freq/duty regs 151–153 | card **PWM output** (5 V PWM path configured by `2001[…]` + tick duty/freq, A1/04) |
| **0–10 V analog power** | in | regs 154/158/256, "Analog(0-10V)" | **DA1 `LGP.LaserDAPort=1`, type 0** (`0x65 ← [9999,4,0,mV]`) |
| **RED** (guide laser) | in | reg 0x8023, `RedCurr` 138, "RedModeSet" 83 | **DO6 `LGP.DORedLight`** |
| **START / control key** (`label_Laser13 "IO Start/Control键"`, `START_EnableIndex=0`) | in | disabled in this parameter set | — |
| **Interlock F1 / F2** | in (safety loop) | FPGA bits 6/10 | wired to the machine safety chain, not to a DO (bench) |
| **EMG F1 / F2** | in | FPGA bits 4/14 | e-stop chain (bench); SW EMG = reg 0x802d |
| **Alarm / ready output** | out | `gbIoAlarm "外控IO报警"` group, status word bits 0–7 | **DI1 laser alarm** (`01 §2.8`), polarity to be checked live |

---

## 7. Answers to the open questions

| Q | Status | Answer |
|---|---|---|
| O5 blocks 10000/11000 | **closed (static)** | ZF status 18 words = `ZFReadOnly01..18` (§2.1); ZF properties 39 words, 10 named (§2.2); both read over the main card link, gated on `ZFType`; 500 ms "slow" log. *[verifier: the "10 ms guard" is dead code (V1); a failed 10000/11000 transaction clears the card-connected flag `VM+0x38` (V4); register addresses of individual words are 10000+2i (likely, V2), not 10000+i]* |
| O5 blocks 13000/13200 | **closed (static)** | auto-focus status (14 words → `getReg(9,·)`) / property (→ `getReg(10,·)`), never read here (`AFType=0`). |
| O5 "why 10000 stalls" | still live | only a capture shows whether the card answers slowly (sub-device query) or the PC re-sends. |
| O9 port 999 | **closed (static)** *(reasoning corrected by verifier)* | not a follower channel at all: `OnBZFPort` is the **extension-card** endpoint for card kinds 5/6, used only when `EC.ECType==1` (=0 here). `fillZFDownFile` uses the main link. The port can ignore 999. |
| O9 register semantics | **partly closed** *(verifier downgrade)* | §2, §3; opcodes and scalings verified (with truncation, V8); calib.csv = up to 200 (A,B) pairs from 12002…12801, meaning (signal, height) is inference; test data = byte-offset window (write offset → 201, read ≤240 words ← 200, 4 words/row); per-word register addresses and reply-header alignment need capture. |
| O9 does a CO2 job command the ZF | still live | no ZF opcode in the CO2 frames (08), but the ZF page/Stop path writes `[101]`/`[103…]` whenever `ZFType≠0` regardless of laser family (A1) → capture G. |
| O10 MCC100 side | **closed (static)** *(frame corrected by verifier)* | DO5 enable = `[9999,2,0x10,0x10]`, DO6 red = `[9999,2,0x20,0x20]`, DA1 = `[9999,4,0,trunc(%×100)]` (mV inference), 50 floor; laser-off drops DO5 and DA unless `LaserDAKeepOutput`. **All of this runs only when `SP.m_iEnableLaserType==0` (fibre); this machine has 1 (CO2), so manual laser-on here is just `DO[CO2DOLaserGate]:=1`** (if `CO2LaserControlType≠0`). |
| O10 laser side | **closed (static)** | Max Photonics register/alarm map §6.2–6.3, analog scaling §6.4, IO model §6.5. |

---

## 8. What capture G (one fibre job + one FTC calibration, port 502 UDP) must confirm

1. **ZF status units**: read 10005/10006/10007 while the head sits at a known height (e.g. after `[103, 1000, 5000]`): expect 5000 = 5.000 mm and speed words in 0.1 mm/s.
2. **Follow/pierce opcodes in the FIFO**: the prologue of a fibre contour (`ManuType=2..4`) — which of 103/104/109/117 appear in-stream with `DrillHeight{k}`/`CutHeight` (×1000) and `GradualTime`, and the frog-jump lift between contours (expected a 103-family record or a dedicated `CFrogJumpSvr` opcode).
3. **DA word**: `9999[4, 0, mV]` before the first pierce; compare with `LaserMaxPower`/layer `CutPower` to confirm `mV = % × 100`.
4. **DO5/DO6 timing**: whether DO5 is raised once per job (enable) or per contour (gate) and its delay relative to the DA/PWM records (`LaserOnDelay`).
5. **Calibration**: run 浮头标定 once and dump 12000…12801 before/after; confirm 12000/12001 = count/status and the (signal, height) monotonic curve.
6. **Register 150**: sniff "Write Params" (expect `[0x40,11000,39,…]` then `150 ← 9999`) — settles the 5555/9999 semantics against A4.
7. **Laser Modbus** (optional): is the laser's RS-485/ZLAN reachable on the shop LAN (default 192.168.0.178, slave 25)? Reading 0x0004/76 and 0x8004/22 once validates §6.3 for the installed 1200 W unit (`MQCSCFAD1519`).

Privacy note: `Pc_Software/bcx/user/A.dll|E.dll` hold the laser tool's password hashes; nothing from them is reproduced here.

---

## Verification notes (adversarial re-derivation, 2026-09-15)

Every VA below was re-read in `objdump -d -M intel` of `Module/NCModule.dll` / `MainApp.exe` (`.scratch/asm/`), constants/strings decoded from the PE sections, and the laser-side constants re-read from `LaserApplication_SC.exe` metadata with dnfile/dncil. Overall: the transport, block, opcode and laser-side maps largely hold; **four concrete errors** (DO frame, "round", test-data window, port-999 purpose), one dead-code misreading, and an unestablished address column were found and fixed in place above.

**Confirmed as written** — `0x100aba08…30` initialised only with vtable `0x10089644` (locator `0x1008e488`, `.?AVIModbus@@`); `0x1008965c` (`.?AVCStdModbus@@`) has no immediate in `.text` · `updateZFStatus` `[0x30,10000,18]` via `VM+0x10` vtbl+0xc, reply `0x54`, 18 dwords from `+0xc` into `VM+0x36c` (`getReg(7,i)` = VM `0x10038d40` case 7) · `updateZFProp` forces `+0x49f0=20000`, `+0x49f4=200`, `[0x30,11000,39]`, reply `0xa8`, into `VM+0x3b4` · 500 ms slow log `cmp eax,0x1f4` @`0x1004ec6f` (QPC-based, only while connected) · kind switch `0x1004e5a0` table `[0x1004e5b7, –, 0x1004e6d2, –, 0x1004e7fe, 0x1004e7fe]`, `OnBZFPort` default 999 → `VM+0xe04` @`0x1004e90f` · AF readers gated on `VM+0x3e` and `AF.AFType` (`0x1004f020/0x1004f032`, `0x1004f912/0x1004f921`); AF type-branch details not re-verified (unused here) · opcodes 101/102 (`0x1002c510/20`), 107 single write with discarded `[107, ZFDockHeight·1000]` (`param+0x49e8` = `ZF.ZFDockHeight`, descriptor key `0x88a200`), 118/109/104/103 vectors and scalings (`0x100312a0–0x10031631`), state map `0x10038fe0` (102→6, 103→4, 104→1, 105→0, 106→8, 107→7; bit31 clear → 5; low byte 4 → 2) · `writeReg(150,5555)` / `(150,9999)` (`0x1002c530/50`) and their MainApp use in reset (`zf58`/`zf59`) and Write Params (`0x5fd23b–0x5fd27a`: slot 62, `Sleep(500)`, `zf37`) · parameter offsets `0x491c/0x6c/0x94/0x4920/0x4924/0x4938/0x490c/0x4a20/0x4a40/0x4918` all re-resolved from MainApp descriptors · laser-on/off/red branches `0x1002d730`, `0x1002d8d0`, `0x1002dda0`, `0x1002da70` (jump tables `0x1002d8b4`, `0x1002da60`, `0x1002def8`) and `× 0.95` (`0x10089c48`) · calib.csv (`%d, %d`, pair.first/second) and test export (`%d, %d, %d, %.3f`, 4th word as float32 `fld DWORD PTR` @`0x5fe068`) · ModbusDefines addresses (4/76, 5, 25, 37, 39=0xABCD, 89, 138, 150, 154, 156, 158, 220, 221, 256/10, 284, 285, 0x8004/22, 0x8005/2, 0x8006, 0x8022, 0x8023, 0x802d; 301 fields / 275 constants); `Login.xml HandAddress=25`; FPGA alarm word bits 0–14 exactly as listed (dncil `Read_LED1_Call`: masks 1…16384 ↔ `led2_*` ↔ alarm strings), fed from block B `w[1]` (`UpdateFPGA` `ldc.i4.1 ldelem.u2` @IL 0x224b9), status word `w[2]` → `Read_LED2_Call`; block A length 76 / `0x5678` check (`ldc.i4 22136`).

**V1 (refuted) "10 ms re-entry guard".** `sub eax,[0x100b3be4]` @`0x1004ea86` is the only reference to that global anywhere in the file (byte pattern `e4 3b 0b 10` occurs once, file off 0x4de88), and its `.data` initial value is 0. `GetTickCount()−0 < 10` is true only in the first 10 ms after boot. There is no effective rate limit; the caller's period governs (same dead pattern with `0x100b3be0` @`0x1004e155`).

**V2 (downgraded) per-word register addresses.** The table's `10000+i` addressing is contradicted by three call sites that all fit `10000+2i` (32-bit value in two registers, low first): (a) Write Params writes `11000+2k` for the row whose read-back is `getReg(8,k)` (`0x5fd1fc` vs `0x5fc45c`); (b) VM slot 62 `0x10039080` = `syncReadZFReg(10004)` + bit-31 test, the same test `0x10038fe0` applies to word 2; (c) the test-data count at 10028 = 10000+2·14 = `ZFReadOnly15` "edge-seek sample count". Unexplained: the block read returns 18 full 32-bit dwords (bit 31 of word 2 is used) while `syncReadZFReg` recombines two 16-bit halves. **Capture step:** in one poll cycle log `[0x30,10000,18]` and `syncReadZFReg(10004)`/`(10002)` replies; whichever of 10002/10004 equals word 2 settles it.

**V3 (refuted reasoning, conclusion kept) port 999.** `fillZFDownFile` `0x10053170` sends through `VM+0x10` (`0x10053351`). `VM+0x30` users are all EC functions (see §1.2 correction), gated on `EC.ECType==1`, sub-address 0xb. Port 999 is the EC endpoint for card kinds 5/6. Kinds 5/6 also require `version % 10000 ≥ 6000` (`0x1004e56a`); with the 20152 firmware of A2 the kind comes from word-0 bits 17–18 (1 or 3), so on that firmware the `OnBZF*` keys are not even loaded. `EC.ECType=0` → never opened.

**V4 (added) failure side effect.** On a failed transaction both `updateZFStatus` (`0x1004eb91`) and `updateZFProp` (`0x1004eecd`) execute `mov [esi+0x38],0` — the **card-connected flag** — and log `false updateZFStatus: %d` (`0x1008b2dc`) / `false updateZFProp`. A wrong reply length is silently ignored for 10000 (`jne 0x1004ec19`) but treated as failure (disconnect) for 11000. So one lost 10000 reply drops the whole card link state; relevant to O5 "why 10000 stalls".

**V5 (refuted) "NC slot 32 = generic vector write".** NC slot 32 `0x1002b570` → VM slot 27 `0x100494e0` builds `[0x40 (0x1008a670), addr, 1, value]` — a single-register write (also used by slots 58/59/61/62). Vector writes go through NC slot 31 → VM slot 26 `0x100497d0`.

**V6 (downgraded) calib.csv pair count/alignment.** Decoder at `0x10042b50` reads from reply `+8`, not `+0xc`; pair count per read = `((len/4)−2)>>2`. "200 pairs" and "(signal, height)" are inference.

**V7 (refuted) test-data window.** Correct loop given in §2.4: byte offset → reg 201 (`[0x40,201,1,idx]`), `n = min(240,(N−idx)/4)` words ← reg 200, `idx += 4n`, reply must be `n+3` dwords, HAL extra args `(4,0)`.

**V8 (refuted) "round".** `0x10060c80` = `_ftol2` (`cvttsd2si`, truncation). Applies to every ZF scaling (`v·10`, `h·1000`) and to `setDA`. Concrete: `2.01 mm` → 2009.

**V9 (corrected) slot 68.** Offset `0x110`; 9-word `[117, …]` vector as in §3.

**V10 (refuted) DO frame.** NC slot 79 `0x1002ffd0`: `[9999, 2, 1<<(port−1), (value&0xff)<<(port−1)]` for port 1..10 (`shl edx,cl` @`0x10030047/0x1003005f`); port ≤ 0 → nothing; 11..26 → `[9999, 13, …]` if `param+0x4d58≠0` (`0x100300c5–0x10030111`). This matches the `9999[2, mask, v]` records of the CO2 capture (08), and replaces `[9999,2,5,1]`. `setDA` additionally: port ∉ {1,2} → nothing sent; DA type ∉ {0,1,2} → word 0.

**Added facts.**
* Card connect (`0x10056c6b–0x10056cd6`): once `VM+0x38` is set, the VM sends `0x65 ← [9999, 5, 0, 0]` via VM `+0x68` — part of the connect prologue the port must reproduce (cross-check with A1).
* The fibre IO path (`LaserControlType=3`) is reached only when `SP.m_iEnableLaserType==0` (`cmp [eax+0x46d8],1` / `cmp ecx,edi` @`0x1002d765/0x1002d7a7`); 01 records `m_iEnableLaserType=1` (CO2) for this machine, which conflicts with this report's use of the Max Photonics 1200 W log as "the machine's source" — flag for the orchestrator; §5/§6 describe a configuration that is inactive in `BkManuPara.xml`.
* Laser types 1/4 call adapter `0x10026810` only if `LGP.LaserType==0`; type 2 raises DO gate and calls `0x10026860` only if `LaserType==1`; the vendor names (Raycus/IPG) were not re-verified.
* Factory reset compares the typed password against a hard-coded UI string (`0x86ff30`, `0x5fd866`); value intentionally not reproduced.
* `lang.txt` `zf37-1` "FTC is restarting, please connect the FTC manually!" suggests reg 150←9999 reboots the follower; no literal reference to that id was found, so the 9999 = "commit" reading stays medium.

**What still needs the machine.** (1) V2 address spacing (one poll-cycle capture). (2) Reply header alignment for 12002… and 10028 reads (+8 vs +0xc): capture one "Calib Datas" and one "test data export". (3) Whether 150←9999 restarts the FTC (watch for link loss after Write Params). (4) Which laser family is actually installed (CO2 `m_iEnableLaserType=1` vs Max Photonics fibre log): look at the machine or the laser nameplate.
