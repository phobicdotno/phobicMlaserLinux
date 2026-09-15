# A2 — Status block, alarm / DI / DO words and axis status: static decode

Task: close **O2** (bit meaning of the block-1000 words and the axis status word), **O15** (alarm bit → `gp*` lang id) and the "block 5000 e-stop / safety decel" question of `99-gaps.md §4`, from `NCModule.dll` and `MainApp.exe` alone.

Conventions: **EVIDENCE** = read from the binaries at the cited VA (objdump listings in `.scratch/asm/`); **INFERENCE** = interpretation, with confidence. Register words are 0-based *word indices* inside a block (word *i* of block 1000 = address 1000+*i*); the `lang.txt` names are 1-based (`RORegName_(i+1)`). "VM" = the `CVirtualMachine` object in NCModule (vtable `0x1008bbc4`), "NC" = the `CNCModule` interface object MainApp talks to (vtable `0x1008a1c4`, 208 slots; MainApp caches its pointer at `this+0x1d8` in the panels). `SRC = /home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52`.

Bottom line:

* The 36 words of block 1000 land verbatim at `VM+0x90 + 4·i` (reader `0x100516b0`), and every consumer reaches them through `VM::getReg(group=0, index=i)` (`0x10038d40`, VM vtable slot 49 / `+0xc4`), so the `lang.txt` order `RORegName_1..32` **is** the address order — **CONFIRMED**, not inferred any more (§1, §2).
* DI word = bit *port−1* for ports 1–12 (24-bit field), DO word = bit *port−1* for ports 1–10 (16-bit field); expanded ports 13–28 / 11–26 live in words 21/22. NO/NC polarity is applied by the PC per input (`type==1` inverts). **CONFIRMED** (§2.1, §2.2).
* Alarm status_1 (word 6) is an **EtherCAT-style summary word**: bits 0–23 = "axis *n* has a fault" (details in that axis' status word bits 0–5), bit 24 = the on-board follower axis, bit 25 bus fault, bit 26 output fault, bit 30 emergency stop. Alarm status_2 (word 7) bits 0–5 = illegal command / interpolation-data length / axis command / FTC command / PLC command / **FIFO starvation**. **CONFIRMED** from the decoder (§2.3, §2.4, §5).
* `gp1…gp32` are **not** bit positions of these words. On the MCC100 path the per-axis alarms are the `EtherCATAxisErrorInfo_0..5` ids formatted with the axis name; `gp1–gp32` and `gp13–16` are never raised anywhere in MainApp (they are the MCC3721 legacy set). The full numeric-code → lang-id map is in §5 (**CONFIRMED**).
* Block 5000 is read as **one word** into `VM+0x1e8` (getReg group 1) and is **never written** by this build; nothing branches on its value. The e-stop reaches the PC only as alarm_1 bit 30 (§4).
* The Windows app's own "watchdog" is the `CManuPanel` poll routine `0x568a50`: any alarm condition → machine state 5 and `OnPauseBtn` (or `OnStopBtn`). The list of bits a Linux port must react to is in §7.

---

## 0. Method

1. `objdump -d -M intel` of both binaries (`.scratch/asm/NCModule.dll.asm`, `MainApp.exe.asm`), helper scripts in the session scratchpad (range dumper, string annotator, vtable/RTTI resolvers).
2. NCModule: read the six `updateMCStatus*` readers, the `getReg` accessor and its jump table, then the `CNCModule` vtable to learn which interface slot returns which word.
3. MainApp: no `call [reg+0xc4]` exists (MainApp never touches the VM directly); instead the UTF-16 `gp*`/`EtherCAT*` literals and their global `CString` copies were mapped (2 546 globals via the `push <lit>; mov ecx,<global>; call CString::ctor` initialiser pattern) — every reference outside the constructors/destructors turned out to be an *array base* used by one function, `CErrorPanel::addAlarm` (`0x52c3f0`). Its single caller chain leads to the code generator `0x568a50`, which is where the bits are tested.
4. Every claim below cites the VA it was read at.

---

## 1. Where the words live (NCModule)

All readers build the request vector `[func, addr, count]` from the constant table at `0x1008a630` (EVIDENCE, `objdump -s`):

| VA | value | VA | value |
|---|---|---|---|
| `0x1008a630` | 1000 | `0x1008a64c` | 13000 |
| `0x1008a634` | 5000 | `0x1008a650` | 13200 |
| `0x1008a638` | 2000 | `0x1008a654/5c` | 1000 (std-Modbus variants) |
| `0x1008a63c` | 50200 | `0x1008a658/60` | 2000 (std-Modbus variants) |
| `0x1008a640` | 50000 | `0x1008a66c` | 0x30 READ |
| `0x1008a644` | 10000 | `0x1008a670` | 0x40 WRITE |
| `0x1008a648` | 11000 | `0x1008a674` | 0x26 |
| | | `0x1008a678/7c` | 3 / 16 (std Modbus FC) |

Readers (EVIDENCE — vector constants, the reply-length check and the copy loop of each function; function names from the log string each one pushes on failure):

| Block / count | Function (name from its log string) | Reply check (bytes) | Destination in VM | `getReg` group |
|---|---|---|---|---|
| **1000 / 36** | `updateMCStatusRO` `0x100516b0` (`push 0x1008a630` @`0x1005171d`, count `0x24` @`0x1005173a`, log `false updateMCStatusRO: %d` @`0x1008b3a4`) | `cmp eax,0x9c` @`0x1005177f` = 3 + 36 words | `add esi,0x90` @`0x10051786`, 36-dword loop `0x10051792–0x1005179d` → **`VM+0x90 … +0x11c`** | **0** |
| 1000 / 2 | `checkMCStatus` `0x1004e3d0` (`cmp ecx,0x14` @`0x1004e4a0`) | 3 + 2 words | word0 → `VM+0x90`, word1 → `VM+0x94` (`0x1004e4a8/b1`) | 0 |
| **2000 / 50** | `updateMCStatusAxisRO` `0x100518c0` (`0x1008a638`, count `0x32`; log @`0x100519ea`) | `0xd4` = 3 + 50 words | 5 blocks × 10 dwords (reply stride `0x28`) → **`VM+0x120 … +0x1e4`** (`lea ecx,[esi+0x124]` @`0x10051996`) | **3** |
| **5000 / 1** | `updateMCStatusRW` `0x10051b00` (`0x1008a634`, count 1; log `false updateMCStatusRW` @`0x1008b6e4`) | `0x10` = 3 + 1 word | **`VM+0x1e8`** (`0x10051bd6`) | **1** |
| 50200 / 100 | `updateMCStatusAxisRW` `0x10051d00` (count `0x64`, reply `0x19c`) | 3 + 100 | 5 axes × 14 words kept (reply stride `0x50`), `mov esi,5; mov edx,0xe` @`0x10051de0/7` → `VM+0x1ec … +0x300` | 4 |
| 50000 / 26 | `updateMCStatusSystemRW` `0x10051f20` (count `0x1a`, reply `0x74`) | 3 + 26 | `add esi,0x304` @`0x10051ff4` → `VM+0x304 … +0x368` | 5 |
| 10000 / 18 | `updateZFStatus` `0x1004ea50` (count `0x12`, reply `0x54`) | 3 + 18 | `lea ecx,[esi+0x36c]` @`0x1004eb66` → `VM+0x36c … +0x3b0` | 7 |
| 11000 / 39 | `updateZFProp` `0x1004ed90` (count `0x27`, reply `0xa8`) | 3 + 39 | `lea ecx,[esi+0x3b4]` @`0x1004ee9c` → `VM+0x3b4 … +0x44c` | 8 |
| 13000 / 14 (or 13200 / n, or std-Modbus 1000 / 28) | **`updateAFStatus`** `0x1004eff0` — the function `99-gaps` called `updateMCStatusRO` is the **auto-focus** reader (log `false updateAFStatus: %d` @`0x1008b370`, `updateAFStatus time` @`0x1008b340`); sub-device type `[param+0x4a20]` selects the vector | `0x44` = 3 + 14 for the type≠2 path (`0x1004f5f5`) | `VM+0x450` (14 words, `0x1004f5fa`); type 7 unpacks 16-bit halves into `VM+0x4dc` (33) and `VM+0x560` (15) | 9 / 13 / 14 |
| 60001 / n (`[VM+0x11fc]`, 120 in the logs) | `updateMCStatusFast` `0x10052130` (`mov [ebp-0xbc],0xea61` @`0x100521aa`, log @`0x1008b7a4`) | 3 + n | words 0–49 → `VM+0x120` (= the 2000 block), 50–119 → `VM+0x1ec` (= the 5×14 AxisRW area), 120–129 → `VM+0x3a0`, 130–143 → `VM+0x56c` (`0x1005221b–0x10052257`) | 3, 4 |

`getReg(group, index)` = `0x10038d40` (VM vtable `0x1008bbc4` slot 49, `+0xc4`), jump table `0x10038e14` (EVIDENCE):

| group | base | content | group | base | content |
|---|---|---|---|---|---|
| 0 | `+0x90` | block 1000 (36 words) | 8 | `+0x3b4` | ZF property block 11000 |
| 1 | `+0x1e8` | block 5000 (1 word) | 9 | `+0x450` | AF status 13000 (14 words) |
| 2, 6 | — | returns −1 | 10 | `+0x488` | (21 words, writer not identified) |
| 3 | `+0x120` | axis RO 2000 (5×10) | 11 | `+0x59c` | ext-card (EC) words (bit tests at `0x1002b6f0/0x1002b730`, alarm word index 4) |
| 4 | `+0x1ec` | axis RW 50200 (5×14) | 12 | `+0x5d8` | — |
| 5 | `+0x304` | system RW 50000 (26) | 13 | `+0x4dc` | AF status as 16-bit halves (type 7) |
| 7 | `+0x36c` | ZF status 10000 (18) | 14 | `+0x560` | AF 13200 halves (type 7) |

So the `READ 60001/120` seen in the logs (`04 §3.5`, `08 §3`) is simply the 2000 block plus a compacted 50200 block fetched in one datagram (INFERENCE from the destinations, high); `04` attributed `0x10052130` to block 1000, which is wrong — the 1000/36 reader is `0x100516b0`.

**Interface slots MainApp uses** (`CNCModule` vtable `0x1008a1c4`; EVIDENCE — each thunk read):

| NC slot (offset) | Function | What it returns |
|---|---|---|
| 12 (`0x30`) / 13 (`0x34`) | `0x1002b440` → VM slot 7 (`mov al,[vm+0x38]`) ; `0x1002b450` = `jmp [vtbl+0x30]` (alias) | card connected (slot 13 is used as "ZF connected" — identical because the follower is on-board) |
| 14 (`0x38`) | `0x1002b460` → VM `[+0x3c]` | laser link connected |
| 15 (`0x3c`) | `0x1002b470`: `param+0x3fa9 && VM [+0x3d]` | AF connected |
| 16 (`0x40`) | `0x1002b490` → VM `[+0x3e]` | EC (extension card) connected |
| 81 (`0x144`) | `0x1002b890`: `getReg(0,5) & 0xffff` | **DO word** |
| 82 (`0x148`) | `0x1002b810`: port 1–10 → bit *port−1* of slot 81; port 11–26 (if `param+0x4d58`) → bit *port−11* of `getReg(0,22) & 0xffff` | `isDOOn(port)` |
| 83 (`0x14c`) | `0x1002b8b0`: `getReg(0,4) & 0xffffff` | **DI word** |
| 84 (`0x150`) | `0x1002b8d0`: port 1–12 → bit *port−1* of slot 83; port 13–28 (if `param+0x4d58`) → bit *port−13* of `getReg(0,21) & 0xffff`; then `if type==1: result = !bit` (`0x1002b93b–0x1002b947`) | `isDIOn(port, type)` — **NO/NC handled on the PC** |
| 104 (`0x1a0`) | `0x1002b990` | raw `getReg(group,index)` pass-through |
| 118 (`0x1d8`) | `0x1002bbd0`: `getReg(3, axisSlot·10+1) / param+0xbab0` (pulses per unit), X/Y combined as √(x²+y²) | current speed → confirms axis word 1 = speed |
| 137 (`0x224`) | `0x1002beb0` → VM slot 126 = `0x10039160` | per-axis "homed" bitmask (bit 15 of each axis status word) |
| 138 (`0x228`) | `0x1002bed0`: `getReg(0,6)` | **Alarm status_1** |
| 139 (`0x22c`) | `0x1002bef0`: `getReg(0,7)` | **Alarm status_2** |
| 140 (`0x230`) | `0x1002bf10` → VM slot 125 = `0x100390d0` | machine state (§3.2) |
| 141 (`0x234`) | `0x1002bf30` → VM slot 61 = `0x10038fe0` | ZF state (§6.1) |
| 142 (`0x238`) | `0x1002bf50`: `getReg(7,1) & 0xffff` (if `param+0x490c`) | **ZF alarm word** |
| 143 (`0x23c`) | `0x10007ee0`: `xor eax,eax; ret` | laser alarm word — **stubbed to 0** in this build |
| 144 (`0x240`) | `0x1002bf80`: `getReg(9,4)` | AF alarm word |
| 22 (`0x58`) | `0x1002b510` → VM slot 18 = `0x10045e30` (log `send stop manu cmd`) | stop processing |
| 58 (`0xe8`) | `0x1002c510`: VM slot 32 `writeSingleReg(0x65, 101)` (`push 0x65; push 0x65`) | the single-word `0x65 ← [101]` command; `0x1002c520` is `0x65 ← [102]` (the re-sync seen in the logs) |

---

## 2. Block 1000 — read-only status, 36 words

Address = 1000 + *i*; VM field = `+0x90 + 4·i`; lang id = `RORegName_(i+1)` (EVIDENCE for the identity of the order: DA1/DA2/DA3 selection `fild [esi+0xb8]/[esi+0xbc]/[esi+0xc0]` for `[param+0x3f38]` = 1/2/3 @`0x1004f14d–0x1004f167`; FIFO frame id `[esi+0xcc]`+1 and FIFO margin `[esi+0xd0]` in `fillFifo` @`0x100523c0–0x100523d3`; DI = index 4, DO = index 5, alarms 6/7, processing status 19 (`push 0x13` @`0x100390db`), parameter status 31 (`push 0x1f` @`0x56fbbb`), ext in/out 21/22 (`0x1002b918`, `0x1002b85f`); the register-monitor page fills 36 rows from `getReg(0,i)`, `cmp [ebp-0x198],0x24` @`0x5ea397`).

| i | addr | `lang.txt` | Chinese / English | Consumer (EVIDENCE) | Decode / notes |
|---|---|---|---|---|---|
| 0 | 1000 | RORegName_1 | 程序标识 Program identification | `checkMCStatus` `0x1004e57a`: `(w0 >> 17) & 3` → `[VM+0x1140]` = 3 or 1 (card sub-type) | bits 17–18 = card variant flag; rest opaque |
| 1 | 1001 | RORegName_2 | 程序版本 Program version | `0x1004e556–0x1004e572`: compared with 6000 (`0x1770`) and 10000 (`0x2710`); MainApp `getReg(0,1)` @`0x4ba84e` | firmware version number (20152 seen live) |
| 2 | 1002 | RORegName_3 | 日期 date | monitor only | |
| 3 | 1003 | RORegName_4 | 时间 time | monitor only | |
| **4** | **1004** | RORegName_5 | 输入状态 **Input status (DI)** | NC slot 83/84 | **bit *n* = DI *n+1***, 24 bits used (`and eax,0xffffff`); level as the card sees it, NO/NC applied by the PC (§2.1) |
| **5** | **1005** | RORegName_6 | 输出状态 **Output status (DO)** | NC slot 81/82; NCModule `0x1005724d–0x100572cf` clears bits `1<<(port-1)` for seven configured DO ports | **bit *n* = DO *n+1***, 16 bits used |
| **6** | **1006** | RORegName_7 | 报警状态_1 **Alarm status_1** | NC slot 138 → `CManuPanel+0x1e8c` | §2.3 |
| **7** | **1007** | RORegName_8 | 报警状态_2 **Alarm status_2** | NC slot 139 → `+0x1e90` | §2.4 |
| 8 | 1008 | RORegName_9 | 运行状态 Operating status | **no consumer** (NCModule only zeroes `[vm+0xb0]` at init `0x100566a3`; MainApp never calls `getReg(0,8)`) | unknown — live capture needed |
| 9 | 1009 | RORegName_10 | AD采样值 AD sample | MainApp `getReg(0,9)` @`0x4c2de9` (display) | raw ADC |
| 10–12 | 1010–1012 | RORegName_11..13 | DA1/DA2/DA3 sample | `0x1004f14d…`, MainApp `getReg(0,10/11/12)` | raw DAC read-back, scaled by the double at `0x10086ec0` |
| 13 | 1013 | RORegName_14 | PWM频率 PWM frequency | monitor only | Hz |
| 14 | 1014 | RORegName_15 | PWM占空比 PWM duty | MainApp `getReg(0,14)` ×3 (`0x4eddbe`, `0x56c2fc`, `0x56c37e`) | % |
| **15** | 1015 | RORegName_16 | FIFO帧标识 FIFO frame id | `fillFifo` `mov eax,[esi+0xcc]; inc eax` @`0x100523cc` | last frame id accepted; next frame = value+1 |
| **16** | 1016 | RORegName_17 | FIFO空间余量 FIFO space margin | `fillFifo` `[esi+0xd0]` @`0x100523c0`; **`cmp [esi+0xd0],0xea60`** @`0x10057f2d` | free item slots; **60000 = FIFO empty** (capacity); job end detected as `margin == 60000 && processing status == 1` → `stopFifo` (`0x10057f3d–0x10057f8d`) |
| 17 | 1017 | RORegName_18 | FIFO插补数据配置 FIFO interp. data config | monitor only | |
| 18 | 1018 | RORegName_19 | 从站设备信息 Slave device info | monitor only | |
| **19** | 1019 | RORegName_20 | 加工状态 **Processing status** | VM `0x100390d0`: `cmp al,1` → machine state 4 (Process); `0x10053e1a`: `if [vm+0xdc]==1 → stopFifo`; `0x10057f3d` | **1 = FIFO program running**; other values unknown (no other compare exists) |
| 20 | 1020 | RORegName_21 | 加工位置 Processing position | monitor only | |
| 21 | 1021 | RORegName_22 | 拓展输入状态 Expanded input | NC slot 84 (ports 13–28 → bits 0–15) | bit *n* = DI *n+13* |
| 22 | 1022 | RORegName_23 | 拓展输出状态 Expanded output | NC slot 82 (ports 11–26 → bits 0–15) | bit *n* = DO *n+11* |
| 23 | 1023 | RORegName_24 | 轮廓状态 Contour status | MainApp `getReg(0,23)` ×4 (`0x4dbc29`, `0x56db20`, `0x581612`, `0x582c54`; job progress) | |
| 24 | 1024 | RORegName_25 | 轮廓索引 Contour index | VM `0x100393f0` copies `[vm+0xec]/[vm+0xf0]` (words 23/24) to caller | index of contour being cut |
| 25–27 | 1025–1027 | RORegName_26..28 | 通电 / 通讯 / 出光时间 power-on / comm / laser-on time | monitor | counters |
| 28 | 1028 | RORegName_29 | 双驱反馈偏差 Dual-drive deviation | MainApp `getReg(0,28)` @`0x518a13` | |
| 29–30 | 1029–1030 | RORegName_30..31 | sampling encoder cfg / length | monitor | |
| **31** | 1031 | RORegName_32 | 参数状态 **Parameter status** | MainApp `0x56fbbb–0x56fc02`: `getReg(0,31) == 1` → lang `eNewLang100` "hardware parameters changed, restart required" | **1 = card needs restart after parameter write** |
| 32–35 | 1032–1035 | (no name) | — | word 35 read once at `0x56c33d` (gated by `param+0x4940==2`) | spare |

### 2.1 DI word (1004) — port ↔ bit, polarity

EVIDENCE `0x1002b8d0` (NC slot 84): `lea eax,[esi-1]; cmp eax,0xb; ja` → ports 1..12 use `getDIWord()` (`getReg(0,4) & 0xffffff`) and test `1 << (port-1)`; ports 13..28 use word 21 bit `port-13` when the extension card flag `param+0x4d58` is set; finally `cmp [ebp+0xc],1; sete al` inverts the result when the *type* argument is 1.

* The type argument is the per-input NO/NC parameter (`WaterWarningType`, `LaserWarningType`, `CO2WaterWarningType`, custom-alarm type field `+0x3b70+i·0x28`; see the calls at `0x568cd2–0x568dd6` and `0x568e19–0x568e67`), i.e. **`type 0` = normally open (bit set ⇒ active), `type 1` = normally closed (bit clear ⇒ active)** — matches `pd164/pd165` "常开/常闭". (INFERENCE on the label, high.)
* Only 12 on-board inputs are addressed by the app although the word is masked to 24 bits; `SystemRWRegName_36..47` name filters up to IN24 → bits 12–23 are physically present but unused here (INFERENCE, medium).
* Limit-switch polarity (O8) is *not* decidable statically: the limits are consumed by the firmware and surface only as axis-status bits 0–3 (§3).

### 2.2 DO word (1005) — port ↔ bit

EVIDENCE `0x1002b810` (NC slot 82): ports 1..10 → `getDOWord()` bit `port-1`; ports 11..26 → word 22 bit `port-11`. Independent confirmation inside NCModule: at the end of a job `0x1005724d–0x100572cf` takes `[vm+0xa4]` (word 5) and ANDs out `~(1 << (port-1))` for the seven DO port numbers stored at `[param+0x1fc/0x1d4/0x1ac/0x184/0x15c/0x134/0x10c]` — the same 1-based convention as the in-stream `9999[2,mask,value]` DO records of `08 §4.4`.

### 2.3 Alarm status_1 (1006) — bit table

Decoder: `CManuPanel` poll routine `0x568a50`, loop `0x56a82d–0x56aaec` over bits 0..31 of `[this+0x1e8c]` (= NC slot 138); raise = `0x5b7ae0(code)`, clear = `0x5b7b90(code)`; the codes are turned into lang ids by `CErrorPanel::addAlarm` `0x52c3f0` (§5). EVIDENCE for every row.

| bit | code raised | lang id (English text) | meaning | polarity |
|---|---|---|---|---|
| 0–15 | for each set bit *b*: read `getReg(3, b·10)` (axis *b* status word) and raise `8100 + b·32 + k` for each set bit *k* ∈ 0..5 (`0x56a893–0x56a92a`); clear the six codes when bit *b* is clear (`0x56aa19–0x56aa67`) | `EtherCATAxisErrorInfo_k` formatted with the axis name of slot *b* (`0x52c7bd: 0x516a50(word)`) | **axis *b* has a fault** — summary flag; see §3 for *k* | 1 = fault |
| 16–23 | same code path (`0x56a931–0x56a9e2`) | same | axes 16–23 (bus axes) | 1 = fault |
| 24 | same per-axis path (`cmp [ebp-0x9a0],0x18` @`0x56a886`) — but `0x569bc9`: **if word == 0x01000000 exactly, the "alarm present" flag `+0x1e88` is reset to 0** (only when the follower is enabled, `param+0x4688/+0x4690`) | axis "24" | the **on-board Z-follower axis** flag (INFERENCE, medium — its "axis status" read `getReg(3,240)` falls outside the 2000 block) | 1 = fault, but alone it is not treated as an alarm |
| 25 | 8025 | `EtherCATErrorInfo_1_25` 总线故障 **Bus fault** | | 1 = fault |
| 26 | 8026 | `EtherCATErrorInfo_1_26` 输出保护 **Output fault** (output protection) | | 1 = fault |
| 27–29, 31 | 8027–8029, 8031 | table entries are empty strings (`0x9a42c0 + n·0x1c` unset) | reserved | — |
| **30** | **8030** | **`EtherCATErrorInfo_1_30` 急停告警 Emergency stop alarm** | **E-stop input of the card active** | 1 = e-stop |

The whole word ≠ 0 (or word 7 ≠ 0) also sets the "alarm present" flag `[this+0x1e88] = -1` (`0x5699c7–0x5699e5`) and makes the `CErrorPanel` timer show the alarm window (`0x52dc38–0x52dc78`: `getAlarm1() || getAlarm2()` → mfc100u ordinal 13047 with argument 5 — consistent with `CWnd::ShowWindow(SW_SHOW)`, INFERENCE).

### 2.4 Alarm status_2 (1007) — bit table

Loop `0x56aaf1–0x56ab6d`: bits 0..5 of `[this+0x1e90]` → code `9000 + bit` (raise) / clear.

| bit | code | lang id | Chinese / English |
|---|---|---|---|
| 0 | 9000 | `EtherCATErrorInfo_2_00` | 非法命令 Illegal command |
| 1 | 9001 | `EtherCATErrorInfo_2_01` | 插补数据内容长度异常 Interpolation data length abnormal |
| 2 | 9002 | `EtherCATErrorInfo_2_02` | 轴控命令执行异常 Axis control command execution exception |
| 3 | 9003 | `EtherCATErrorInfo_2_03` | FTC命令执行异常 FTC command exception |
| 4 | 9004 | `EtherCATErrorInfo_2_04` | PLC命令执行异常 PLC command exception |
| 5 | 9005 | `EtherCATErrorInfo_2_05` | FIFO饥饿 **FIFO starvation** |
| 6–31 | — | not examined by the PC | |

`EtherCATErrorInfo_3_01` ("no slave scanned, restart hardware") is **not** a bit of a third word: it is gp-table index 103 (code 103, §5) and no generator raises it in this build (INFERENCE: leftover of the EtherCAT product line).

---

## 3. Block 2000 — axis read-only words (5 slots × 10)

Word *k* of axis slot *a* = address 2000 + 10·a + k, VM `+0x120 + 4·(10a+k)`, `getReg(3, 10a+k)`.

| k | `lang.txt` (static-table order, EVIDENCE `0x1006e…` initialiser / MainApp table at `0x9a4b80`) | Consumer (EVIDENCE) | Confidence |
|---|---|---|---|
| **0** | AxisRORegName_1 状态 **Status** | `0x100390d0`, `0x10039160`, `0x100391a0`, MainApp `0x56a8bb` | CONFIRMED |
| 1 | AxisRORegName_2 速度 Speed | NC slot 118 (`10·slot+1`, ÷ `param+0xbab0` pulses/unit, √(x²+y²)) | CONFIRMED |
| 2 | AxisRORegName_3 脉冲位置 Pulse position | MainApp `imul …,0xa; add …,2; push; push 3` @`0x45d01b`, `0x45d073`, `0x5680e5` | CONFIRMED |
| 3 | AxisRORegName_4 编码器位置 Encoder position | monitor | table order (LIKELY) |
| 4 | AxisRORegName_6 停止时脉冲 Stop pulse | monitor | LIKELY |
| 5 | AxisRORegName_12 停止时编码器值 Encoder at stop | monitor | LIKELY |
| 6 | AxisRORegName_7 累计行程 Total mileage | monitor | LIKELY |
| 7 | AxisRORegName_8 计算累计脉冲数 Calc. cumulative pulses | MainApp `imul …,0xa; add …,7` @`0x584ffe` | LIKELY (index confirmed, name from table order) |
| 8 | AxisRORegName_9 计算累计编码器数 | monitor | LIKELY |
| 9 | AxisRORegName_10 累计脉冲数 | monitor | LIKELY |

(`AxisRORegName_5` Z-phase and `_11` cumulative encoder are absent from the table, which is why 12 names map to 10 words — `04 §3.5`'s observation, now explained.)

### 3.1 Axis status word (2000 + 10·a) — bit table

| bits | meaning | EVIDENCE |
|---|---|---|
| 0 | 硬正限位 hard **+** limit (`EtherCATAxisErrorInfo_0`) | raised as `8100 + a·32 + 0` when alarm_1 bit *a* is set (`0x56a8e4–0x56a92a`) |
| 1 | 硬负限位 hard **−** limit (`_1`) | same, k = 1 |
| 2 | 软正限位 soft + limit (`_2`) | k = 2 |
| 3 | 软负限位 soft − limit (`_3`) | k = 3 |
| 4 | 伺服输入告警 servo (drive) input alarm (`_4`) | k = 4 |
| 5 | 双驱告警 dual-drive alarm (`_5`) | k = 5 |
| 0–3 as nibble | "a limit is active" | VM `0x100391a0` (slot 127): `and eax,0xf` per axis, returns "all zero" |
| **15** | **homed** (origin found) | VM `0x10039160` (slot 126 → NC slot 137): `sar eax,0xf; and eax,1` collected into a 5-bit mask (INFERENCE on the name: it is the only per-axis flag exported as a mask and the go-origin UI is its consumer, medium-high) |
| 16–23 | **command executing / busy** | VM `0x100390d0`: `sar eax,0x10; and eax,0xff; test` |
| 24–31 | **current command type**: 2 = go-origin, 3/4/5 = jog / move variants | `0x100390d0`: `sar esi,0x18; and esi,0xff; cmp esi,2 / 3 / 4 / 5` |

Polarity of bits 0–3 vs. the physical switch is decided in the firmware from the `NegativeLimitInput/ForwardLimitInput` parameters — bench only (O8 stays open).

### 3.2 Machine state derived from the words (VM `0x100390d0`, NC slot 140)

```
if word19 (processing status) == 1            → 4  (mp11 "MCC <Process>")
for each axis a in 0..4:
    t = byte3(status_a); busy = byte2(status_a) != 0
    if t == 2 and busy                        → 1  (mp8  "MCC <Origin>")
    if t in {3,4,5} and busy                  → 2  (mp9  "MCC <Jog>")
otherwise                                     → 0  (mp7  "MCC <Ready>")
```

State names are the array at `0x9e7f70` (`mp7, mp8, mp9, mp10 "Stop", mp11, [5] [6] [7] unnamed, mp12…`); state **5 is "alarm"** (set at `0x56af39` and `0x56b0ee` when any alarm is pending, handled by the jump-table case `0x56b73a`), state 3 "Stop" is set by the stop handlers. The MainApp copy `[this+0x1e74]` is also forced to 4 when the follower's run-command word (`getReg(7,3)`) equals 107 (`0x569010`).

---

## 4. Block 5000 — RW word, e-stop port, safety deceleration

* Reader: `updateMCStatusRW` `0x10051b00` reads **one** word (5000) into `VM+0x1e8` (`getReg(1,0)`). EVIDENCE §1.
* Writers: **none**. `0x1008a634` (the constant 5000) has exactly one reference (the reader); no `push 0x1388`/`0x1388` immediate exists in NCModule; the three `0x1388` immediates in MainApp are `glDeleteLists/glGenLists(…, 5000)` (`0x4015ef`, `0x401642`, IAT `0x7c0ee0/0x7c0e60`) and a `malloc(5000)` (`0x403dd9`). The generic single-register writer (VM slot 32 `0x10040cb0`) is only called with addresses 0x65 (values 101/102) and 150 (values 5555/9999) — `0x1002c510–0x1002c564`.
* Readers of the value: only the register-monitor page (`getReg(1, i)` loop at `0x5ea5a5`) and two display sites (`0x5d5bbb`, `0x5d5c57`); nothing branches on it.
* Names: the static id table contains `RWRegName_1` (输入类型 input type), `RWRegName_4` (安全减速度 safety deceleration) and `RWRegName_5` (安全减减速度 safety jerk) but **not** `RWRegName_2/3` (output mask, e-stop input port) — consistent with a 1-word read whose siblings are unused by this build.

Consequence for the port (INFERENCE, high): on the MCC100 the e-stop input assignment and the safety decel/jerk are **not** configured through block 5000 by this software. The e-stop input is a hardware parameter (`DI.EStop`, descriptor row "输入端口.急停 DI.Emergency Stop" in `01-hardware-config.md §1`, = 0 on this machine) written with the 59600+ parameter area, and its activation is reported to the PC as **alarm_1 bit 30**. The "safe deceleration" the card applies is `AX.SafeStopFactor` (= 3, row "杂项.安全减速系数" in the same table) from the same area. (`RegName72/73` "急停输入端口 / 软急停标志" are the MCC3721 legacy registers.) → The port should read 5000 for parity but must not rely on it; one live read of 5000..5008 will show whether the card even exposes more than one word.

---

## 5. Alarm code → lang id (`CErrorPanel::addAlarm`, `0x52c3f0`)

The only entry point that turns a *number* into text (single caller `0x52c12d`; raise/clear helpers `0x5b7ae0/0x5b7b90` keep the list at `CManuPanel+0x5b30` and forward to the panel). EVIDENCE — the compare ladder `0x52c485–0x52caf8` and the array bases; array contents recovered from the static initialisers (2 546 `CString` globals mapped):

| code range | text source | contents |
|---|---|---|
| 0 ≤ c < 104 | `gpTable[c]` at `0x9a2d88 + c·0x1c` (`imul edx,edx,0x1c; add edx,0x9a2d88` @`0x52c525`) | `[0..59] = gp0..gp59`, `[60] gp92, [61] gp95, [62] gp96, [63] gp97, [64..79] gp100..gp115, [80] gp121, [81] gp141, [82..98] gp200..gp216, [99] gp217, [100] gp218, [101] gp219, [102] gp220, [103] EtherCATErrorInfo_3_01` |
| 150 ≤ c ≤ 159 | `0x9a23cc + (c−146)·0x20` | function-DI names `pd1025..pd1030` (低压空气 … 高压氮气 gas-select inputs) — status messages, not alarms |
| 1000 ≤ c < 1016 | settings singleton `+0x3b74 + (c−1000)·0x28` | **custom DI alarm name** *i* (`DI.AlarmDIStr`, e.g. "Door" on DI4) |
| 8000 ≤ c < 8100 | `0x9a42c0 + (c−8000)·0x1c` | index 25 `EtherCATErrorInfo_1_25`, 26 `_1_26`, 30 `_1_30`, 32–37 `_2_00.._2_05`; other entries empty. i.e. **c = 8000 + (word−1)·32 + bit** for alarm words 1/2 |
| 8100 ≤ c < 8800 | `0x9a46e8 + ((c−8100) mod 32)·0x1c`, formatted with axis name `(c−8100) div 32` | `EtherCATAxisErrorInfo_0..5` "%s hard +/−, soft +/−, servo input, dual drive" |
| 8800 ≤ c < 9000 | ignored | |
| 9000 ≤ c < 10000 | `0x9a4640 + (c−9000)·0x1c` | `EtherCATErrorInfo_2_00..2_05` |
| c ≥ 10000 | `0x9a1f84 + (c−10000)·0x1c` | `gp1000` (processing out of range), `pd22` 急停 E-stop, `pd23` water alarm, `pd24` Z alarm, `pd25`, `pd26` |

**Every code raised anywhere in MainApp** (all 29 `call 0x5b7ae0` sites enumerated): 0 (gp0 hardware not connected, `0x4e9966`, `0x565ce6`), 45 gp45, 56 gp56, 57 gp57, 58 gp58, 59 gp59, 60 gp92, 61 gp95, 63 gp97, 79 gp115, 80 gp121, 81 gp141, 82 gp200, 99 gp217, 100 gp218, 101 gp219, 33+bit (gp33–gp43), 46+bit (gp46–gp55), 64+bit (gp100–gp115), 83+bit (gp201–gp216), 1000+i, 8000+bit, 8100+axis·32+bit, 9000+bit, and one data-driven site `0x59f2f3`. **Codes 1–32 (`gp1`–`gp32`: X/Y1/Y2/W servo, encoder, dual-drive, hard/soft limits), 13 (network), 14/15 (FPGA) and 16 (急停 gp16) are never raised** — they are the MCC3721 `RegName5` bit set kept for the legacy card. So `06 §6`'s guess "gp1–32 = one alarm word" is **wrong for the MCC100**; the equivalent information arrives as alarm_1 bit *axis* + axis-status bits 0–5.

---

## 6. The generator: `CManuPanel` poll routine `0x568a50` (all alarm sources)

Executed on the panel timer; `this+0x1d8` = NC interface, `this+0x1ec` = alarm sink. EVIDENCE per line.

| source word / flag | fetched by | stored at | bits → code → lang id |
|---|---|---|---|
| card connected | NC slot 12 (`0x568a9f`) | — | false → `0x565640` reconnect path; code 0 `gp0` raised in `0x565ce6` |
| **Alarm status_1** | NC slot 138 (`0x56997f`) | `+0x1e8c` | §2.3 |
| **Alarm status_2** | NC slot 139 (`0x5699ad`) | `+0x1e90` | §2.4 |
| **ZF alarm word** (`getReg(7,1)&0xffff`) | NC slot 142 (`0x569d32`), only if `param+0x490c` (ZF enabled); zeroed and code 57 `gp57` raised when "ZF offline" (`0x569a1d–0x569b71`, status text `mp23`) | `+0x1e94` | bit *n* (0–10) → code 33+n → **gp33..gp43** (Z hard up/down, soft up/down limit, servo input, touch-plate, encoder, signal small, follow error, capacitance small, signal large) `0x56ac5d–0x56acd4`; bit 11 (`0x800`) → 81 `gp141` ZF FPGA not loaded; bit 12 (`0x1000`) → 99 `gp217` ZF axis value; bit 13 (`0x2000`) → 100 `gp218` ZF signal zero; bit 15 (`0x8000`) → 59 `gp59` FTC alarm (`0x56ab6f–0x56ac5d`) |
| ZF run state | NC slot 141 (`0x569c06`) → VM `0x10038fe0` | `+0x1e78` | see §6.1; state 5 = `mp17` "FTC <EStop>"; 45 `gp45` "FTC not homed" raised while state == 5 and NC slot 13 true (`0x56a6c4–0x56a716`) |
| laser link | NC slot 14 (`0x569dc3`); offline → 58 `gp58` (`0x569efe`), text `mp24` | `+0x1e7c` | |
| **laser alarm word** | NC slot 143 (`0x56a059`) — **stub returning 0** | `+0x1e98` | bit *n* (0–9) → 46+n → gp46..gp55 (`0x56acd6–0x56ad4d`) — dead in this build |
| laser locked | NC slot `0x1c4` (`0x56a09c`) | byte `+0x1f28` | → 80 `gp121` (`0x56aec7–0x56aee0`) |
| AF connected / **AF alarm word** | NC slot 15; word from NC slot 144 (`getReg(9,4)`) masked `0xcfff` (AF type 1/2), full (type 3), or `getReg(13,10)<<16 + getReg(13,26)` (type 7) (`0x56a15f–0x56a33e`) | `+0x1e9c`, `+0x1ea0` (bits 12/13 for type 2 with `param+0x4cf1`) | bit *n* (0–15) → 64+n → **gp100..gp115** (`0x56ad96–0x56ae0f`); `+0x1ea0` bits 12/13 → 76/77 `gp112/gp113`; type 7 nonzero → 79 `gp115`; offline → 61 `gp95` |
| EC connected / **EC alarm word** | NC slot 16; `getReg(11,4)` (`0x56a4a3`) | `+0x1ea8` | bit *n* (0–15) → 83+n → **gp201..gp216** (`0x56ae0f–0x56ae88`); offline → 82 `gp200` |
| chiller DI | `isDIOn(param+0x3b00 port, +0x3b04 type)` (`0x568d58–0x568d98`; fibre variant `+0x35b0/+0x35b4` first, then overwritten) | byte `+0x1eac` | → 56 **gp56** 冷水机异常 chiller alarm (`0x56a71b–0x56a754`) |
| laser DI (fibre) | `isDIOn(param+0x3b28, +0x3b2c)` (`+0x3650/+0x3654` for fibre) | byte `+0x1ead` | → 60 **gp92** laser alarm (`0x56a754–0x56a78d`) |
| custom DI alarms 0..15 | `isDIOn(param+0x3b6c+i·0x28, +0x3b70+i·0x28)` (`0x568e19–0x568e67`), gated by "only while processing" byte `+0x3b90+i·0x28` and job state | bytes `+0x1eaf+i` | → 1000+i (custom name) (`0x56a78d–0x56a814`); cleared unless `OnlyManualRelieveAlarm` (`param+0x3ea4`) |
| pulse-equivalent check | `0x589040` | byte `+0x1ebf` | → 63 `gp97` |
| plan count finished | byte `+0x1f2a` | | → 101 `gp219` |
| network-abnormal 62 `gp96` | only ever **cleared** (`0x568c99`) | | |

### 6.1 ZF (on-board follower) status words used (block 10000, `getReg(7,·)`)

VM `0x10038fe0` (EVIDENCE): `s = getReg(7,2)` (ZFReadOnly03 运行状态 running status), `c = getReg(7,3)` (ZFReadOnly04 运行命令 run command):
`if s >= 0 (bit 31 clear) → 5 (EStop)`; `elif (s & 0xff) == 4 → 2 (Drill)`; `else switch c: 102→6, 103→4 (JogDown), 104→1 (Follow), 105→0 (Ready), 106→8, 107→7, other→0` (jump table `0x10039068`). Names `0x9e8050`: 0 `mp12` Ready, 1 `mp13` Follow, 2 `mp14` Drill, 3 `mp15` JogUp, 4 `mp16` JogDown, 5 `mp17` EStop, 6–8 unnamed. MainApp also treats `getReg(7,3) == 107` as "processing" (`0x569010`). VM slot 62 (`0x10039080`) reads register **10004** directly and returns bit 31 (`push 0x2714`, `test …,0x80000000`) — semantics unknown, no MainApp caller found.

---

## 7. What the safety watchdog must react to

Derived from the Windows app's own reactions (EVIDENCE): the "all clear" test at `0x56a4db–0x56a657` requires `alarm_1 == 0 && alarm_2 == 0 && ZFalarm == 0 && AFalarm == 0 && ECalarm == 0 && ZFstate ≠ 5 && laser/AF/EC not offline && !pulseEquivError && laserAlarm == 0 && !chillerDI && !laserDI && !customDI && NC slot 0x1f0() == 0 && NC slot 0x1f4() == 0 && !laserLocked`. When it fails (`0x56a65d–0x56a6b9`): unless the job state is already 3 or 11 → `OnPauseBtn` (`0x587a30`, log "Sys Pause"), or `OnStopBtn` (`0x5880a0`, log `CManuPanel::OnStopBtn` → NC slot 22 "send stop manu cmd" + NC slot 58 `0x65 ← [101]`) when the plan count is finished during a running job; then machine state 5, alarm lamp DO on / standby+process DO off (`0x56b73a–0x56b8a0`), status LED red (`push 0xff0000`).

For the Linux port (priority order):

| # | word / bit | condition | reaction |
|---|---|---|---|
| 1 | 1006 bit **30** | e-stop active | stop streaming, refuse motion, show `EtherCATErrorInfo_1_30`; after release prompt `mp124` (the Windows app shows it from the callback stored at `0x939f1c` = `0x58fbe0`, which first calls `0x5884b0` to reset the job state) |
| 2 | 1006 bits **25 / 26** | bus fault / output fault | same as 1 |
| 3 | 1006 bits **0–23** (+24 with ZF) | axis *n* fault → read 2000+10n bits 0–5 | stop; report `EtherCATAxisErrorInfo_k(axis)`; bits 0–3 = limit hit (block further jog in that direction) |
| 4 | 1007 bits **0–5** | illegal cmd / interp length / axis cmd / FTC cmd / PLC cmd / **FIFO starvation** (bit 5) | stop the job; bit 5 is the under-run alarm `MCFifoTime` is meant to prevent |
| 5 | 2000+10n bits 0–3 while jogging | limit active (even without the summary bit) | refuse jog in that direction |
| 6 | 1004 DI bits through the NO/NC map | chiller (DI11, NC), laser alarm DI (fibre), custom alarms (`Door` DI4), e-stop DI if `DI.EStop` ≠ 0 | pause/stop per the Windows rule above |
| 7 | 10001 (ZF alarm) bits 0–13, 15; ZF state 5 | follower faults / follower e-stop | fibre-head jobs only |
| 8 | 1031 == 1 | parameter status "restart needed" | refuse motion until the card is power-cycled |
| 9 | 1019 / 1016 | processing status 1 and FIFO margin (60000 = empty) | job supervision: keep margin from running out, detect end-of-job |
| 10 | link | `updateMCStatusRO` failure (`false updateMCStatusRO`) → `[vm+0x38] = 0` | treat as alarm (gp0) |

Polarity summary: alarm bits **1 = active**; DI/DO bits = raw level, NO/NC applied by the PC (`type` per input); axis-status bits 0–5 **1 = fault**; homed bit 15 **1 = homed** (INFERENCE on the last, medium-high).

---

## 8. Exception codes 2 / 3 (static status)

The PC side never interprets them: `ErrCode = 500 + code` (`04 §3.3`), the logger prints it, and the jog/home paths just re-send. The only places MainApp materialises 502/503 are two constant stores in a UI routine (`0x47a339: mov [ebp-0x4],0x1f7`, `0x47a355: …,0x1f6`) with no branch on card state. NCModule's offline simulator does not fabricate exception replies (no `func|0x80` builder found). **Still open (live):** one capture of `READ 1000/2` immediately after power-up (expect code 2 until the card's stack is ready) and one jog command sent while an axis is moving (expect code 3) settles both; the 08-doc reading (2 = not ready after reset, 3 = refused in current state) remains the best hypothesis.

---

## 9. Closed / still open

Closed statically:

* **O2** — block 1000 word map (§2) incl. DI/DO bit convention, alarm_1/alarm_2 bit tables, processing/parameter status values, FIFO margin capacity; axis status word bit fields (§3.1); machine-state derivation (§3.2). Remaining unknown inside O2: word 8 "run status" (no consumer) and the exact meaning of axis-status byte-3 values 3/4/5.
* **O15** — alarm id ↔ bit: the numeric-code scheme and both lookup tables (§5); `gp1–32` are legacy and never raised.
* **Block 5000** — one word, never written, no consumer (§4); e-stop/safety-decel live in the hardware-parameter area.
* Corrections to earlier docs: `0x1004eff0` is `updateAFStatus` (auto-focus, blocks 13000/13200), not the 1000-block reader; `0x10052130` reads **60001** (a combined 2000+50200 read), not 1000; the 1000/36 reader is `0x100516b0`; NC interface slot 143 (laser alarm word) is a stub, so `gp46–55` cannot appear on this build.

Still open (needs the machine):

* Polarity of axis-status bits 0/1 vs. the physical switch pair (O8): press each switch, read 2000+10n, note which of bits 0/1 rises (one jog-free bench step).
* Semantics of exception codes 2/3 (§8) — one capture at power-up and one jog-while-moving.
* Block-1000 word 8 and words 32–35; axis-status byte-3 values: capture idle / homing / jog / running and diff the words.
* Whether 5000..5008 returns more than one word and what `RWRegName_3/4/5` hold on the MCC100: a single `READ 5000/9` (the PC-side window allows it).
* ZF status bit 31 / register 10004 bit 31 (fibre head only).

---

## Appendix A — VM object field map used above

| VM offset | content | VM offset | content |
|---|---|---|---|
| `+0x38` | card connected (byte) | `+0x120..+0x1e4` | block 2000 (5×10) |
| `+0x3c/+0x3d/+0x3e` | laser / AF / EC connected | `+0x1e8` | block 5000 word 0 |
| `+0x90..+0x11c` | block 1000 words 0–35 | `+0x1ec..+0x300` | block 50200 compacted (5×14) |
| `+0xa0/+0xa4` | DI / DO words | `+0x304..+0x368` | block 50000 (26) |
| `+0xa8/+0xac` | alarm_1 / alarm_2 | `+0x36c..+0x3b0` | ZF status 10000 (18) |
| `+0xb0` | run status (unused) | `+0x3b4..+0x44c` | ZF prop 11000 (39) |
| `+0xcc/+0xd0` | FIFO frame id / margin | `+0x450..+0x484` | AF status 13000 (14) |
| `+0xdc` | processing status | `+0x4dc`, `+0x560` | AF 16-bit unpacked areas |
| `+0xec/+0xf0` | contour status / index | `+0x11fc` | count for the 60001 read |
| `+0x10c` | parameter status | `+0x1140` | card sub-type from word 0 bits 17–18 |

## Appendix B — direct `getReg` reads issued by MainApp (group, index → sites)

From the scan of all `call [NC+0x1a0]` sites (push order: index first, then group): group 0 → 1, 9, 10, 11, 12, 14, 23, 28, 31, 35 and the monitor loop `i < 36` (`0x5ea3aa`); group 1 → monitor loop (`0x5ea5a5`), 2 (`0x5d5bbb`, `0x5d5c57`); group 3 → `10a+0` (`0x56a8bb` alarm scan), `10a+2` (positions), `10a+7`, monitor loop `0x5ea49d`; group 7 → 0..7, 14, 15, 16, 17 (ZF page and state logic); group 9 → 0, 4, 5, 7, 9, 11 (AF); group 11 → 4 (EC alarm word), 5 (bit 31 = platform flag, `0x569329`), 6, 7, 13; group 13 → 0, 4, 10, 11, 26; groups 5/8/10/12/14 → monitor pages only.
