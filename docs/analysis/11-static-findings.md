# 11 — Static sprint: consolidated findings (O1..O18, Stop/E-stop, block 5000)

Lead synthesis of the eight verified sprint reports in `docs/analysis/11-static/`:
A1 motion commands · A2 status words · A3 FIFO items · A4 licence registers · A5 ZF/laser blocks · A6 planner & crafts · A7 file formats · A8 pendant HID.
Package root `SRC = /home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52` (read-only). Disassemblies are in `.scratch/asm/`.

Status legend (used here and in `00-overview.md §7.2`):
* **CLOSED (static)**: the port can be written from binary evidence. A live run only confirms it.
* **NARROWED**: static work cut the question down to one named live/bench/Wine step, and the implementation depends on that result.
* **OPEN**: still unknown, or a non-goal.

Where only an A-report is cited, its verified text (the `[verifier]` corrections) is authoritative. Claims I re-read myself during this synthesis are marked **(lead re-read)**.

---

## 0. Result in one screen

| # | Question | Status | Answer (short) | Evidence |
|---|---|---|---|---|
| O1 | Tick unit/period, frame id, MaxItemPerFrame | **NARROWED** | Ticks are **motor pulses**, truncated with a carried remainder. X = low half, Y = high half. Frame id = card reg 1015 + 1. Frames flush at 300 words. `MaxItemPerFrame` is read and ignored. The PC's tick period is `AX.InterpolationCycle`, overwritten from card reg **50005**. Still live: the card-side period and reg-1016 units | A3 §0, §3, §4.1, §7, V1/V3 |
| O2 | Status/alarm bits, axis status, exception 2/3 | **CLOSED (static)**, except exceptions 2/3 = NARROWED | Full block-1000 map. DI/DO bit = port−1. alarm_1 = per-axis summary + bus/output/E-stop bits. alarm_2 = command/FIFO-starvation bits. Axis status bits 0–5 fault, 15 homed, byte 2 busy, byte 3 command type (§4) | A2 §2–3, §7 |
| O3 | Jog/home units, index vs mask | **CLOSED (static)** | Speeds and distances × K (K = card units/mm = 1000). Acceleration is a plain integer. Jerk = 10·a. Jog word 1 = axis-list index. Jog target = **relative** distance (bit 31 = absolute). Home = `[2, 1<<slot, 0]`, a bit mask, with speeds held on the card | A1 §0, §2, §3, §5 |
| O4 | Opcodes 3001/3002/103/109/2001/118, DO bits | **CLOSED (static)** for encoding and DO rule. Meaning of 3001/3002/2001/118 is inference; replay verbatim | DO: `9999[2, 1<<(p−1), v<<(p−1)]`. 103/109 are **ZF moves** `[v·10, h·1000]`, not dwells. 2001 = wait-with-timeout. Full table §5 | A3 §4.4, §5, §6; A5 V10 |
| O5 | Blocks 10000/11000/13000/13200, 10000 stalls | **CLOSED (static)** for identity. Per-word addresses and the stall cause are NARROWED | 10000/18 = ZF status (`ZFReadOnly01..18`). 11000/39 = ZF properties. 13000/13200 = auto-focus, never read here (`AFType=0`). Single-register access is probably at 10000+**2i**. A failed 10000 read clears the card-connected flag | A5 §2, V2, V4; A2 §1 |
| O6 | Licence registers; does the card refuse motion unlicensed? | **NARROWED** | Licence block = **59500–59511**, just below the 59600 hardware-parameter base. MainApp *does* gate Start/frame/dry-run/forward-back/break-resume on `CDog.state==0`; Home/Stop/Z/jog are ungated. Firmware gating: one bench jog + one FIFO start without the exchange | A4 §0, §2, §4, §6 |
| O7 | Slots 3/4; lift-table slot; Y dual-drive | **CLOSED (static)** for the slot map. Y2 dual-drive is NARROWED | Slots: 0 X, 1 Y, 2 Y2, 3 height axis (调高轴), 4 W = lifting table (jog `[3,4,…]`, home `[2,0x10,0]`). Dual-drive: bench jog Y and watch slot-2 pulse position 2022 | A1 §5.3, A2 §2.3 (table `0x991138`) |
| O8 | Limit polarity per DI pair | **NARROWED** (bench) | Firmware maps DIs to axis-status bit 0 (hard +) / bit 1 (hard −). Press each switch and read 2000+10·slot bits 0/1 plus DI word 1004 | A2 §3.1, §2.1 |
| O9 | ZF protocol on port 999; do CO2 jobs command ZF | **NARROWED** | Port 999 is **not** a follower link. It is the extension-card endpoint and is never opened here (`EC.ECType=0`). All ZF traffic uses the main UDP link. Command set `[101]` stop, `[103,v·10,h·1000]` move, `[104,…]` follow, `[107]` head calibration, `[117,…]`, `[118,…]`. **CO2 jobs on this machine do emit ZF records** (`103/109/118/2001` in the leaked CO2 frames) because `ZFType=1`. Fibre pierce stream, units, and register spacing: capture G | A5 §1–3, V2–V3; A3 §4.4, §8 |
| O10 | Fibre laser control path; DA scaling | **CLOSED (static)** (fibre is inactive while CO2 is selected) | IO path: DO5 gate `[9999,2,0x10,0x10]`, DO6 red `[9999,2,0x20,0x20]`, DA1 `[9999,4,0,trunc(%·100)]` mV (0–10 V), 50 mV floor. Laser side = Max Photonics Modbus map, `AnalogPowerCoefficient=100`. Still to settle: which source is fitted (nameplate), DO5 timing (capture G) | A5 §5–6 |
| O11 | `.chf` crafts scalars | **NARROWED** | `int58`=1. compensate 2 = inside, 3 = outside. Lead type 0/1/2/3 = none/line/arc/line+arc. `double170` = lead start ratio. Cool positions = path ratios. PWM node = (centre ratio, length mm). `[+0x104]` = mode int. Trailer = edge-seek angle flag + angle in radians. Text/spline/type-12 flags: Wine diff (session H) | A6 §2, V9–V11 |
| O12 | UI → planner P0..P9; AccTime /1000 | **CLOSED (static)** | P0 ManuAcc, P1 AccTime·0.001, **P2 SplineAccuracyRate**, P3 min(CutSpeed, MaxSpeed, 750000/K), P4 0, P5 InterpolationCycle·0.001, P6/P7 `UD_Up*`, P8/P9 `UD_Down*`. `SlowStart*` is used only by Simulate | A6 §1.6 |
| O13 | `ManuType` folding | **CLOSED (static)** | 0 standard, 1 fixed height, 5 advanced fixed height, 2/3/4 = 1/2/3-stage pierce. 6/7 cannot be selected from the UI | A6 §3 |
| O14 | `.enc`/`.aut`; NormalExit; AutosaveParam; TotalReport trigger | **CLOSED (static)** | Marker-terminated concatenations (no lengths). NormalExit = 0 at connect-init, 1 in the destructor. AutosaveParam = 100 ms timer, counter parity. TotalReport row appended on the running→stopped transition (card state 0x17) | A7 §0–5 |
| O15 | Alarm id ↔ bit; gp1–32 | **CLOSED (static)** | Codes 8000+ / 8100+axis·32+bit / 9000+bit → `EtherCAT*` ids. gp1–32 are MCC3721 legacy and never raised | A2 §5 |
| O16 | PHBX / hand-wheel HID layout | **NARROWED** (USB only, no motion) | PHB02: VID 0x10CE, input ID 4 (6 bytes) with a UTC-day checksum, feature ID 6 (8 bytes), key codes 0x01..0x13, Fn = 0x0B. Receiver presence, PID and key legend: A8 R0–R3 | A8 §0, §7–8 |
| O17 | `.mcf` cipher | **OPEN** (non-goal) | — | 04 §5 |
| O18 | `[settings+0x4356]` forces TCP | **CLOSED (static)** | Set only by hidden command 0x426a (Reconnect over TCP), probably unreachable. The shipped build uses UDP unless `ipAdd.ini AccessType=0` | A1 §8 |
| N1 | Stop / Pause / E-stop vectors | **CLOSED (static)** | Stop = `[1,0x1F,2,vd,10·vd]`, or `0x67←3` while the FIFO runs, then laser/PWM/gas/DO off (running branch only) and `[101]`. **Pause = Stop.** UI E-stop = stop + laser gate off + latch. Hardware E-stop = alarm_1 bit 30 | A1 §0 rows 3–7, §4 |
| N2 | Resume / Continue after Pause | **OPEN** → capture F | No resume primitive in the card vocabulary. The Continue path was not traced statically | A1 §4.3 |
| N3 | Block 5000 (e-stop port / safety decel) | **NARROWED** | Vendor **never reads** 5000 (reader has no caller). It **writes** `5001←[9999,9,0xFFFF,0]` and `5008←v` (5012 is dropped PC-side). One diagnostic `READ 5000/9` at idle / E-stop pressed | A2 §4 |
| N4 | Connect prologue | **CLOSED (static)** (lead re-read) | After connect: `0x65←[9999,5,0,0]` (`NC 0x10056c6b–0x10056cd6`). The "handshake `[9999,13,0xFFFF,ver]`" is **not a handshake** (§2.1) | A5 added facts; this doc §2.1 |
| N5 | Exception codes 2/3 | **NARROWED** | PC never interprets them (ErrCode 500+code, re-send). Capture `READ 1000/2` at power-up and a jog while moving | A2 §8 |
| N6 | `0x65←[9999,16]` sent on every record push (VM slot 17) | **OPEN** | Not in any log. Gating not traced. Deny in the port until captured | A3 V "Added" |
| N7 | Pendant unplug with a jog key held | **CLOSED (static)**: safety gap | PHBX/MainApp send no key-up, so the axis runs until stopped. The port must stop on read error / hidraw removal / 1.04 s silence | A8 §7 step 5, verifier "Missed" |

---

## 1. Contradictions between reports: resolved

| # | Topic | Position A | Position B | Resolution |
|---|---|---|---|---|
| C1 | Register 150/151 | 99-gaps §2.4: licence "access switch", 5555 open / 9999 close. A5 §0: shared by licence and FTC pages, cites MainApp callers `0x48c486/0x48c4f6/0x48c9b8/0x5052b1` as licence code | A4 (verified): only runtime callers are `CZFStatusView` FTC write (`0x5fd267`, 150←9999) and FTC factory reset (`0x5fda16`, 150←5555). 151 = edge-seek buffered records (`0x592641`). No CDog path | **A4.** Lead re-read `0x48c486`: `call [vt+0xf4]` on `[frame+0x478]` with three doubles loaded from `g+0xb0b8+0xc0..0xd0` (graph-rule block). That is not the CNCModule and not licence code. `0x5052b1` was not verified by anyone. Both stay **DENY** (5555 = FTC factory reset) |
| C2 | Units of reg 1016 "FIFO space margin" | A2 §2: free item slots | A3 §7: the PC compares `bytes+2000 ≤ margin`, and 60000 = empty | Only the PC byte arithmetic is evidence. **Implement byte accounting as the vendor does.** Confirm with the delta after one frame (A3 §10.2) |
| C3 | Block 5000 | 99-gaps §4/§7: "read 5000 at start-up". A4 §5: "RW safety word (read-only in this build)". A2 first draft: read, never written | A2 verifier: reader `0x10051b00` has **no caller**. 5001/5008 **are written** | **A2 verifier.** Port may *read* 5000/9 as a diagnostic (inside the wire window 5000..5008). Never write |
| C4 | Name of K (reg 50017) | A1: maybe `SystemRWRegName_34` 精度系数 (low) | A3 V3: words 5/6 = `SystemRWRegName_6/7` (bus cycle / height-axis enable) supports i ↔ name i+1, which would make word 17 = `_18` "bus axis 8 input configuration" | **Name unresolved.** A1 shows the NCModule name table is non-contiguous (keys 1–10, 28–41); both mappings agree on words 5/6. The *value* K=1000 and its use are evidence from the logs (50→50000, ±4 000 000). Do not depend on the name. Read 50000/26 once |
| C5 | Axis speed read-back scale | A2 §1 slot 118: "÷ `+0xbab0` pulses per unit" | A1 §2: `+0xbab0` = K = card units per mm | **A1.** K is units/mm (µm). Axis RO speed word = 0.001 mm/s. Motor pulses appear only in FIFO ticks (A3) |
| C6 | ZF records in CO2 jobs | 00 §9 #10 and A5 §7: "leaked CO2 FIFO frames contain no ZF opcodes" | A3 §4.4/§8: prologue `103[1000,0]`, `2001[0x03000002,20000]`, epilogue `109[1000,0]`, `118[4,0,35]` are ZF-family records, emitted only when `g+0x490c` (ZFType) ≠ 0 (`0x10044f25`, `0x10044c30`) | **A3.** CO2 jobs on this machine command the on-board follower with zero heights. Port: emit the same records while card/XML `ZFType=1`. An A/B capture with ZFType=0 is optional |
| C7 | "Handshake `0x65←[9999,13,0xFFFF,ver]`" | 04 §3.6/§3.8 (`0x10057575`), 00 §4 step 1, 08 §3 | A1 §4.2: DO bulk write in stop-manu | **A1.** Lead re-read `0x100574d6–0x100575f2`: builds `[9999,2,0xFFFF,DOword & ~cfgMask & 0xFFFF]`, then only if `g+0x4d58≠0` `[9999,13,0xFFFF,(…>>16)&0xFFFF]` = **extended-DO bulk off**. The logs contain no `9999 13` vector (only `[9999,2,1,0/1]`, 29×). The real connect write is `[9999,5,0,0]` (`0x10056c6b`, lead re-read) |
| C8 | Jog word 1 | A4 §5 allow-list: "slot" | A1 verifier: caller's axis-list index `i` (`0x1002f9b5`) | **A1.** Equal to the slot here (`g+0xba5c=[0,1]`) |
| C9 | `[1, mask, 2, a, b]` | 00 §4/§5.1, 04 §3.6, 08 §4.5: home | A1: STOP (`0x10053df0`, MainApp `0x57a580`) | **A1.** Home is sub-cmd 2 |
| C10 | `0x65←[102]` | 00 §9 #16: comm re-sync | A5 §3: the same number shows as ZF run-command echo 102 → state 6 | Both observations hold; meaning unknown. Rejected with exception 2 in the logs. **DENY in M1** |
| C11 | 103/109 | 00 §4/§5.1, 08 §4.4, A5 §3 note: dwell before/after laser | A3 §5: ZF move `[speed·10, pos·±1000]` | **A3** (packers read). Pierce dwell = stationary 3000 ticks |
| C12 | Port 999 | 00 §5 row 2: on-board ZF endpoint | A5 (verifier V3): EC endpoint for card kinds 5/6, gated on `EC.ECType==1` | **A5.** Never opened on this machine |
| C13 | Which laser is live | A5 verifier flag: the report describes the fibre path, but `m_iEnableLaserType=1` (CO2) | 00 §1/§9 #9: dual source, CO2 selected at capture time | **No conflict.** The fibre IO path (A5 §5) runs only when the selector = 0. CO2 is the M1–M5 target |
| C14 | FIFO frame count word | A4 §5: `count = 1+3·items` | A3 §4.1: `count = 1 + data words` (items have variable length; flush at ≥ 300 words) | **A3** |
| C15 | Pause vector | A2 §7: "NC slot 22 vector `[10, 0x18]`" | A1 §4.3: VM *queue message* `{type 0xa, [0x18]}` | Same thing. It is not a card vector. The card sees the stop-all vector |
| C16 | `"%d, %d, %d, %.3f"` export | 99-gaps §1.1: `calib.csv` | A5 §2.4, A7 §7: test-data export `0x5fde00`. `calib.csv` = `"%d, %d\n"` | **A5/A7** |
| C17 | `pwmCompensation.txt` | 99-gaps §1.2: golden runtime tables | A7 §8: never read by any binary. Equal to `1390backup.xml` values. Live `BkManuPara.xml` differs in 11/12 pairs | **A7.** Live XML is authoritative. The txt files are reference data only |
| C18 | `CHidUsb` VID/PID | 01 §2.9 (swapped), 07 §8.3 (selection inverted) | A8 §6.1: 1000/2016 for RemoteType 0/1 (and default), 6125/2012 for 2 | **A8** |
| C19 | `AutosaveParam` field 1 | 01: `ManuContour` index. 08: processed-item index | A7 §4.2: global 100 ms sample counter | **A7** |
| C20 | Planner P2 | 05 §7.1: `CornerAccuracyRate` | A6 §1.6: `SplineAccuracyRate` (0.02). Corner (0.05) → `CInterpMrg+0x50` | **A6** |
| C21 | ZF status per-word addresses | A5 table: 10000+i | A5 verifier V2: 10000+2i (three call sites) | **Unresolved → NARROWED.** Block read `[0x30,10000,18]` is unaffected; single-register access needs capture |
| C22 | Alarm_1 bits 5–24 per-axis decode | A2 draft: axis *b* status bits | A2 verifier: `getReg(3, b·10)` reads past the 50-word block for b ≥ 5 | Decode per-axis detail **only for slots 0–4**. Report bits 5–23 as "axis n fault" without detail |
| C23 | Y pulses/mm source | A3 draft: card RW words 9/10 for both axes | A3 V1: copy loop refreshes slots 0, 2, 4 only | **A3 V1.** X uses card axis-0 lead/pulses (after refresh). Y uses XML `MAC_1.SpeedRatio/WritePluse`. Reproduce and log any card-vs-XML mismatch |

---

## 2. Command register 0x65 / FIFO control 0x67: vector table for M1

Transport: func 0x40 WRITE, register 101 (0x65), words little-endian u32 (04 §3.2). The frame on the wire is `[0x40, 0x65, n, w0..wn-1]`.
Units (A1 §2): speed ×K (0.001 mm/s), distance ×K (µm), acceleration = trunc(mm/s²) with no K, jerk = 10·acc. K = reg 50017 = 1000.
NCModule truncates (`_ftol2` = `cvttsd2si`), except where MainApp rounds (`0x4511d0`, half away from zero; lift jog v/d).
**This machine's values** (`File/BkManuPara.xml`, `BkHardPara.xml`, A1 §6): JogFastSpeed 200, JogSlowSpeed 50, IsFastMode 1, IsStepMove 0, StepLength 1, XFastMoveAcc 5999.99, FCP.MaxSpeed 3000, FCP.MaxAcc 20000, JogStopDccFactor 1, MS.EnableSoftLimit 0, XFastMoveSpeed 500, EmptyMoveSpeedFactor 1.1, EmptyMoveAccFactor 1.5, HPA3.Acc 4000, PlatformExchangeSpeed 100, MAC_2.SoftLimitMaxLen 1000, ZF.ZFType 1, ZFUpSpeed 100, ZFDockHeight 20, ZFSafeHeight 15.

| # | Action | Vector (this machine) | Rule | Evidence | M1 status |
|---|---|---|---|---|---|
| V0 | Connect prologue | `[9999, 5, 0, 0]` once the connected flag is set | Meaning unknown; the vendor always sends it | `NC 0x10056c6b–0x10056cd6` (lead re-read) | ALLOW (verbatim). Capture A confirms it is needed |
| V1 | Jog continuous X+ / X− (fast) | `[3, 0, 200000, 5999, 59990, +4000000]` / `[…, −4000000]` | word1 = axis-list index (0 X, 1 Y). v = (IsFastMode ? Fast : Slow)·K ≤ MaxSpeed·K. a = trunc(XFastMoveAcc) ≤ MaxAcc. d = trunc(20·JogFastSpeed·K) when soft limit off, else distance to the soft limit. **Relative.** Y: word1 = 1. Slow: v = 50000 | A1 row 1, §3.1. Logs `03 01 c350 176f ea56 3d0900` | ALLOW |
| V2 | Jog step / M1 5 mm test | `[3, 0, 50000, 5999, 59990, 5000]` | same, d = StepLength·K or chosen distance | A1 rows 1–2 | ALLOW |
| V3 | Jog key released | `[1, 1<<slot or OR-mask, 2, 2000, 20000]` | vd = clamp(JogStopDccFactor·100000 / (v_last/K), 2000, trunc(0.4·FCP.MaxAcc)=8000). 200 → 500 → 2000; 50 → 2000. Soft-limit-on branch: min 5000, per axis. Not sent for step moves. vd is most likely a decel in mm/s² | A1 row 3, §4.1 | ALLOW (safety primitive) |
| V4 | Stop all (Stop button / Pause / E-stop / lift release) | FIFO running: `0x67 ← [3]`, else `[1, 0x1F, 2, 2000, 20000]`. Then `[101]` if connected & ZFType≠0. Running/faulted branch also: CO2DOLaser DO off, CO2 DA 0, `[9999,3,PtLaserFreq,0,0]` + `[9999,0x11,PtLaserFreq,0,0]` PWM off, gas DAs 0, DO bulk `[9999,2,0xFFFF,keep]` | VM builder clamps vd to [2000,100000]. The **idle branch does not switch laser/PWM/gas/DO off**, so the port must do it explicitly | A1 row 4, §4.2. Log `01 1f 02 7d0 4e20` | ALLOW |
| V5 | Stop all, no decel words | `[1, 0x1F]` | Edge-seek routines; card default decel (inference) | A1 row 16 | ALLOW (fallback) |
| V6 | Home one axis | `[2, 1<<slot, 0]` | mask; word2 = 0. Speeds and back-off are card-side (50200 AxisRW) | A1 row 8, §5 | ALLOW (one axis at a time) |
| V7 | Home system | `[2, mask(g+0xba30), 0]` | config type 3 → X\|Y. Types 6/7/other → `[2,0,0]` | A1 row 9 | DENY in M1 (use V6 per axis) |
| V8 | Go-to point (absolute) | `[5, 0x80000003, 550000, 8999, 89990, x·1000, y·1000, 0, 0]` | mask = OR of configured axes \| bit31 (absolute); v = XFastMoveSpeed·EmptyMoveSpeedFactor·K, a = trunc(XFastMoveAcc·EmptyMoveAccFactor) (values from the log; caller `0x58c130` not re-derived); last two words Z/W = 0 | A1 row 13; log | ALLOW after homing |
| V9 | Lift table up / down | `[3, 4, round(min(jog v,100)·K), 4000, 40000, ±1000000]` | slot 4; d = StepLength or MAC_2.SoftLimitMaxLen (1000 mm)·K; **no soft-limit clip**. Release = V4 | A1 row 10, §3.2. Log `03 04 c350 fa0 9c40 f4240` | DENY in M1 (bench later) |
| V10 | Lift table home | `[2, 0x10, 0]` | | A1 row 12 | DENY in M1 |
| V11 | Set DO p (1..10) | `[9999, 2, 1<<(p−1), v<<(p−1)]`. DO1 lamp off = `[9999,2,1,0]`; DO3 high air on = `[9999,2,4,4]`; DO9 CO2 laser = `[9999,2,0x100,0x100]` | ports 11..26 → sub 13 only if `g+0x4d58≠0` | A3 §6, A5 V10 | ALLOW for DO1 (lamp). Gas DOs in MOTION_ARMED. **DO9/laser gate only in LASER_ARMED** |
| V12 | Set DA ch (1..2) | `[9999, 4, ch−1, mV]`, mV = trunc(V·1000) (VM `0x1003c760`) or trunc(%·100/50/40) (NC slot 85). 1..49 → 50, max 10000 | A3 §6, A5 §5 | DENY in M1 (gas pressure DA2 in M5) |
| V13 | PWM set without motion | `[9999, 0x11, freq, duty]` (CO2 5 V PWM, `CO2LaserControlType=2`) / `[9999,3,…]` | A3 §5 | DENY until LASER_ARMED |
| V14 | ZF stop | `[101]` | Vendor also sends it from the idle Stop button with no ZFType check | A1 row 17, A5 §3 | ALLOW (ZFType=1 here) |
| V15 | ZF move / follow | `[103, trunc(v·10), trunc(h·1000)]` / `[104, …]` | truncation: 2.01 mm → 2009 | A5 §3 | DENY in M1 |
| V16 | FIFO clear / start / stop | `0x67 ← [1]` / `[2]` / `[3]` | | A3 §5 "Not opcodes", A4 §5 | ALLOW ([2] only in dry-run with laser records stripped) |

Card-side gates to mirror (A1 §1): jog/move builders do nothing unless the card-derived runStatus (VM `0x100390d0`, §4.3) == 0. Home requires runStatus ∈ {0,1}. Re-sent jogs while moving get exception 3 (08 §4.5).

### 2.1 Why the old "handshake" is gone (lead re-read, EVIDENCE)
`NCModule 0x100574d6–0x100575f2` is inside the VM stop handler `0x10056e8d` (A1 §4.2). It ANDs the DO word with the complements of ≥ 8 configured DO ports, pushes `0x270f, 2, 0xffff, edi&0xffff` and writes 0x65. Then `cmp [cfg+0x4d58],0; je` skips a second vector `0x270f, 0xd, 0xffff, (edi>>16)&0xffff`. That second vector is the extended-output half of the same "all configured outputs off" write. Log census (`Log/*.log`, `DataEx:40 65 …`): 506× 6-word, 60× 5-word, 29× 4-word (all `270f 02 01 00/01`), 2× 1-word. There is no `270f 0d`.

---

## 3. Register ALLOW / DENY lists (`mcc/safety.py`)

Wire windows (A4 §1/§5, A2 §4). `readReg` drops (104,1000)∖{150,151}, (1100,2000), (3000,5000), (5008,6000), (51000,59000). `writeReg` additionally drops everything in 104..5000 except 150/151. So a vendor write to 5012 or to status blocks never reaches the wire. The port enforces its own list *before* framing and logs every write with its bytes.

### 3.1 ALLOW: writes (func 0x40)
| Register | Content | Condition |
|---|---|---|
| 0x65 | sub-cmds **1** (V3/V4/V5), **2** per-axis (V6), **3** slots 0/1 (V1/V2), **5** (V8), `[9999,5,0,0]` (V0), `[9999,2,…]` DO (V11, per arming state), `[101]` (V14) | armed state machine (PORT-PLAN §8.2). Soft limits PC-side |
| 0x66 | FIFO frames per §5 | MOTION_ARMED. In dry run: duty 0, no DO9/laser-gate record, no `9999[3/0x11]` |
| 0x67 | 1 clear, 2 start, 3 stop | 3 always allowed |

### 3.2 ALLOW: reads (func 0x30)
| Block | Count | Use |
|---|---|---|
| 1000 | 2 (version gate) / 36 (every 30 ms) | status, §4 |
| 2000 | 50 | axis RO (5×10) |
| 60001 | n (120) | fast combined 2000+50200 read (vendor "fast mode", `VM+0x11fc>0`) |
| 50000 | 26 | SystemRW. **Card is authoritative** for bus cycle (word 5 → `AX.InterpolationCycle`), ZFType (word 6), K (word 17) |
| 50200 | 100 | AxisRW (5×20, vendor keeps 14/axis). Axis-0 words 9/10 = lead / command pulses (X tick factor) |
| 1050 | 3 | version / time triple |
| 5000 | 9 | **diagnostic only** (never read by the vendor). Once at idle and once with the E-stop pressed |
| 10000 | 18 | ZF status, only if ZFType≠0. A failure here must **not** be treated as link loss unless deliberately chosen (vendor clears connected, A5 V4) |
| 11000 | 39 | ZF properties (read-only for the port) |
| 59600 + 0xD0·i | 52 | hardware-parameter compare (read only) |
| 12002…12801 (8×100), 10028/2 | — | FTC calibration / test-data viewers: not needed for M1, harmless reads |

### 3.3 DENY: writes the port must refuse
| Register / vector | Why | Evidence |
|---|---|---|
| **150** (any value, esp. 5555 / 9999) | 5555 = FTC factory reset (vendor asks for a password). 9999 = FTC parameter commit/restart | A4 §1, A5 §0 |
| **151** (write; reads also denied by default) | edge-seek buffered records | A4 §1 |
| **59500–59599** (writes *and* reads) | card licence: 59500/59501 verify/data, **59502/59503 RTC**, 59510 user, 59511 admin record | A4 §2–3 |
| **59600+** hardware-parameter writes | card config (`writeHardParam2Card`). Read-compare only until each block is capture-verified | A4 §5, 04 |
| **11000+2k** writes | FTC property writes (vendor follows with 150←9999) | A5 §2.2 |
| **100 ← 9999** | sent after "parameters changed, restart?" and after 5008/5012 writes. Meaning unproven (restart/apply) | A2 §2 row 31, §4 |
| **5000–5012** writes (`5001←[9999,9,0xFFFF,0]`, `5008←v`, `5012←…`) | e-stop port / input type / safety decel area. Semantics unknown | A2 §4 |
| **201** writes, **200** reads | indexed test-data window (FTC viewer only) | A5 §2.4 |
| 0x65 `[102]` | re-sync / ZF command 102. Rejected (exc 2) in logs | C10 |
| 0x65 `[107]`, `[117,…]`, `[118,…]`, `[103/104/109,…]` outside the FIFO | ZF head calibration / pierce / bookkeeping / Z moves | A5 §3 |
| 0x65 `[9999,13,…]` | extended DO (needs `g+0x4d58`), not used here | §2.1 |
| 0x65 `[9999,16]` | unexplained per-record write (N6) | A3 |
| 0x65 `[9999,1,…]`, `[7]`, `[4,…]`, `[1,8]` | 04's "legacy FIFO clear / offline upload / start" — not re-verified by the sprint | 04 §3.6 |
| 0x65 `[2, mask≠single axis]`, `[3, 4, …]`, `[3, 0x80000010, …]` | system home / lift table / roll feeder: not in M1 scope | A1 rows 9, 10, 14 |
| func **0x26** (any) | firmware download | 04 §5 |
| AF func 0x10 `(0x67, 0)` | AF device write in the vendor stop handler (AFType=0 here) | A1 §4.2 |
| Any address not in §3.1 | policy | PORT-PLAN §1 |

---

## 4. Status / alarm bit table

### 4.1 Block 1000 (word i = address 1000+i; A2 §2)
| i | Name | Decode for the port |
|---|---|---|
| 0 | program id | bits 17–18 card variant (only if version%10000 < 6000) |
| 1 | program version | `type·10000 + minor` (this card 20152); require ≥ `MinHardwareVer` |
| 2 / 3 | date / time | display |
| **4** | **DI** | bit n = DI n+1 (24-bit field, 12 used). **NO/NC applied by the PC**: `type 1` inverts. **Port 0 = unassigned: treat as disabled** (vendor reports it active with type 1) |
| **5** | **DO** | bit n = DO n+1 (16 bits) |
| **6** | **alarm_1** | §4.2 |
| **7** | **alarm_2** | §4.3 |
| 8 | run status | no consumer; unknown (capture) |
| 9 | AD sample | raw |
| 10–12 | DA1–DA3 read-back | raw |
| 13 / 14 | PWM freq (Hz) / duty (%) | |
| **15** | FIFO frame id | last id accepted; next frame id = value + 1 |
| **16** | FIFO space margin | vendor treats it as bytes free; **60000 = empty** (C2) |
| 17 / 18 | FIFO interp. config / slave device info | display (17 may advertise the period: capture) |
| **19** | processing status | low byte **1 = FIFO program running** |
| 20 | processing position | |
| 21 / 22 | expanded DI 13–28 / DO 11–26 | only with the extension flag |
| 23 / 24 | contour status / contour index | job progress |
| 25–27 | power-on / comm / laser-on time | counters |
| 28 | dual-drive deviation | |
| 29–30 | sampling encoder cfg / length | |
| **31** | parameter status | **1 = card needs restart after a parameter write**: refuse motion |
| 32–35 | spare | |

### 4.2 alarm_1 (reg 1006), 1 = active
| Bit | Meaning | Code → lang id | Port reaction |
|---|---|---|---|
| 0–4 | axis slot b (X, Y, Y2, height axis, W) has a fault → read 2000+10b bits 0–5 | 8100+b·32+k → `EtherCATAxisErrorInfo_k`(axis) | stop + disarm. Bits 0–3 of that axis = limit → block jog in that direction |
| 5–23 | "axis n fault" (no status word exists; vendor decode reads garbage, C22) | 8100+… | stop + disarm, generic text |
| 24 | on-board follower axis flag. Word == 0x01000000 alone is *not* an alarm (vendor, under two unnamed param gates) | — | log. Treat as alarm while cutting until capture G |
| 25 | bus fault | 8025 `EtherCATErrorInfo_1_25` | stop + disarm |
| 26 | output fault (output protection) | 8026 `_1_26` | stop + disarm |
| **30** | **emergency stop active** | 8030 `_1_30` | stop streaming, refuse motion, latch until acknowledged (`mp124`) |
| 27–29, 31 | reserved | — | treat ≠0 as unknown alarm |

### 4.3 alarm_2 (reg 1007)
| Bit | Meaning (code 9000+bit, `EtherCATErrorInfo_2_0b`) |
|---|---|
| 0 | illegal command |
| 1 | interpolation data length abnormal |
| 2 | axis control command exception |
| 3 | FTC command exception |
| 4 | PLC command exception |
| **5** | **FIFO starvation** |
Bits 6–31 are not examined by the vendor. Port: any bit → stop job + disarm.

### 4.4 Axis status word (reg 2000+10·slot; A2 §3.1)
| Bits | Meaning |
|---|---|
| 0 / 1 | hard + / hard − limit (which DI is which = O8 bench) |
| 2 / 3 | soft + / soft − limit |
| 4 | servo (drive) input alarm |
| 5 | dual-drive alarm |
| 15 | homed |
| 16–23 | command executing (≠0 = busy) |
| 24–31 | current command type: 2 go-origin, 3/4/5 jog / move variants |
Other words (A2 §3): +1 speed (0.001 mm/s), +2 pulse position, +3 encoder position, +4 stop pulse, +5 encoder at stop, +6 total mileage, +7..+9 cumulative counters (names 3–9 LIKELY).

### 4.5 Derived machine state (VM `0x100390d0`)
`word19&0xff == 1` → 4 Process. Else, for any axis 0..4: byte3==2 && busy → 1 Origin; byte3∈{3,4,5} && busy → 2 Jog. Otherwise 0 Ready. MainApp: 3 Stop, 5 Alarm.

### 4.6 ZF alarm word (reg 10001 = `getReg(7,1)&0xffff`, only if ZFType≠0; A2 §6)
bits 0–10 → gp33..gp43: Z hard up/down limit, soft up/down limit, servo input, touch plate, encoder, signal small, follow error, capacitance small, signal large. Bit 11 → gp141 ZF FPGA not loaded. Bit 12 → gp217 ZF axis value. Bit 13 → gp218 signal zero. Bit 15 → gp59 FTC alarm.
ZF state (A2 §6.1): `getReg(7,2)` bit 31 clear → 5 EStop; low byte 4 → Drill; else by `getReg(7,3)`: 102→6, 103→4 JogDown, 104→1 Follow, 105→0 Ready, 106→8, 107→7 (calibration).

### 4.7 Watchdog set (A2 §7, port priority)
1006 bit 30 → 1006 bits 25/26 → 1006 bits 0–23 (+ axis bits) → 1007 bits 0–5 → axis limit bits while jogging → DI alarms via the NO/NC map: chiller DI11 (NC), door DI4 custom alarm, laser DI1 (fibre only) → ZF alarm word and ZF state 5 (fibre jobs) → 1031 == 1 → status poll failure > 1 s. Vendor reaction to any of these: Pause (= stop-manu) + machine state 5, alarm lamp on.

---

## 5. FIFO item specification (register 0x66; A3)

### 5.1 Frame
`WRITE 0x66`, words `[count, frameId, item…]` after `[0x40, 0x66]`. `count = 1 + data words`.
The builder closes a frame once the vector reaches ≥ 300 words including the 4-word prefix `[0x40,0x66,count,id]`, or at the last record. With 3-word ticks that gives 297 data words = 99 ticks. Frame length varies with item size.
**frameId = reg 1015 + 1**, then +1 per frame within one `fillFifo` call (≤ 50 frames per call). Send only if `frameBytes + 2000 ≤ reg 1016`. A frame is popped before sending, so a frame that fails all retries is lost. The port should instead abort the job on an unacknowledged frame.
Job end: last batch pushed && reg 1016 == 60000 && processing status == 1 → `0x67 ← [3]`.

### 5.2 Item grammar
`header = (payloadBytes << 16) | opcode`, followed by `payloadBytes/4` words. All 22 leaked frames decode with no remainder and re-encode byte-identically (A3 §8–9).

| Opcode | Header | Payload | Meaning (status) |
|---|---|---|---|
| **3000** | `0x00080BB8` | `[(u16 dY<<16) \| u16 dX, (freq<<16) \| duty]` | one tick. dX/dY = int16 **motor pulses** on slots 0/1. freq<1 → 5000. (EVIDENCE) |
| 3001 | `0x00000BB9` | — | boundary: auto-inserted when a control record follows a tick (not for the first record of a batch), or from record type 0xE (EVIDENCE rule / INFERENCE role) |
| 3002 | `0x00040BBA` | `[mode]` | 5 before laser-on, 4 after (pass-through; meaning low) |
| 9999/2 | `0x000C270F` | `[2, 1<<p, v<<p]` | DO, p = XML port−1 |
| 9999/13 | `0x000C270F` | `[13, 1<<(p−10), v<<(p−10)]` | DO 11..26 |
| 9999/4 | `0x000C270F` | `[4, ch, value]` | DA (in-stream units unverified; not used with CO2 PWM) |
| 9999/3, /0x11 | `0x000C270F` | `[3\|0x11, freq, duty]` | PWM set without motion (0x11 on this machine) |
| **103** | `0x00080067` | `[trunc(v·10), trunc(h·1000)]` | ZF move to height (prologue `103[1000,0]`) |
| **109** | `0x0008006D` | `[trunc(v·10), trunc(h·−1000)]` | ZF lift/dock (epilogue `109[1000,0]`) |
| 104 | `0x00080068` / `0x00200068` | 2 / 8 words | ZF short / section block |
| 105 / 106 / 108 | `0x00100069` / `0x0018006A` / `0x0010006C` | 4 / 6 / 4 | ZF follow / section drill / gradual drill (fibre) |
| **118** | `0x000C0076` | `[4, flag, int(g+0x4d28)]` | ZF bookkeeping. flag kept only for fibre. `[4,0,35]` here |
| **2001** | `0x000807D1` | `[w, t]` | mode 0 `w=ms`, `t=3000` = wait ms (LIKELY). Mode 3 `w = r\|0x03000000`, `t = FollowOvertime`(20000) = wait for ZF condition r with timeout |
| 2002 / 2004 / 3 | — | — | not emitted in this configuration |

**Refuse** the configuration `LaserType≠0 && CO2LaserControlType ∉ {1,2} && !(LaserType==1 && ctl==3)`: the vendor emits a malformed 3000 item there (A3 §4.3).

### 5.3 Tick quantisation and period
`Δpulses = trunc(Δmm·10000·(pulsesPerRev / lead / 10000) + carry)`. The per-axis carry is kept across contours and jobs.
X uses card axis-0 AxisRW words 9/10 (lead / command pulses, after the 50200 refresh).
Y uses XML `MAC_1.SpeedRatio / WritePluse` (C23). This machine: 8000/31.003 → 258.04 p/mm X; 8000/31.009 → 257.99 p/mm Y.
Tick period = `AX.InterpolationCycle` µs as refreshed from **reg 50005** (XML default 250). Every "hold t ms" = `(1000 idiv cycle)·t` stationary ticks.
Duty/freq per tick come from the layer power/frequency-vs-speed curves, rounded half up (A3 §3).

### 5.4 Per-contour record sequence observed for CO2 layer 2 on this machine (A3 §8)
Prologue: `3001; 3002[5]; tick(0,0); 3001; 9999[2,4,4] (DO3 high air on); 103[1000,0]; 2001[0x03000002,20000]; 9999[2,0x100,0x100] (DO9 CO2 laser enable on); N×tick(0,0,5000 Hz,4 %)` (pierce dwell as stationary ticks).
Epilogue: `3001; 9999[2,0x100,0] (DO9 off); 109[1000,0]; 2001[0x03000002,20000]; 118[4,0,35]; 3001; 3002[4]`, then rapid ticks at 0 %.

---

## 6. Other closed facts the port needs (pointers)

* **Planner block** (A6 §1.6, §4.1): code-ready `planner_block()`. Also the slot-122 block (micro-link decel, small-circle limit, fly-cut) and `JumpAddTime.txt [Arc2SegVelK]`. Split-and-stop is fed by **cool points** rescaled to the lead + contour + overcut length.
* **`.chf` crafts** (A6 §4.2) and **file formats** (A7): `.enc`/`.aut` marker containers. `scFlie`. `softPara.ini` NormalExit rules. `AutosaveParam` 100 ms parity files with rounded µm. `TotalReport` 10-field UTF-8 CRLF row. `.pmf` = drop (writer only).
* **Licence** (A4): no card write; nothing to implement.
* **Fibre** (A5 §5–6): IO/DA path and Max Photonics register/alarm map for M6.
* **Pendant** (A8 §7): hidraw spec with checksum code. Add stop-on-unplug and stop-on-silence.
* **Vendor side effects not to copy**: `.enc` export sends Stop to the card and sets run state 2 (A7 §1.3). Stop-button idle path sends `[101]` without a ZFType check (A1 §9). Pendant unplug leaves the jog running (A8). A failed ZF status read clears the card link flag (A5 V4). `isDIOn(port 0, NC)` reports active (A2 §2.1).

---

## 7. What still needs the machine

A single **"M1 confirmation" capture** (tcpdump on 10.1.1.x, laser PSU key off, gantry mid-bed, hand on E-stop) settles every NARROWED item on the CO2 path. The port never touches §3.3 registers and never runs the licence exchange.

| Step | Action | Settles |
|---|---|---|
| 1 | Connect: `READ 1000/2`, `READ 1000/36`, `READ 50000/26`, `READ 50200/100`, `[9999,5,0,0]` | K = word 17 == 1000; bus cycle word 5 == 250; ZFType word 6; axis-0/1 lead/pulses vs XML (C23); version |
| 2 | `READ 5000/9` idle, then with E-stop pressed, then release; read 1006 each time | N3 block 5000 contents; alarm_1 bit 30; card-side DI inversion |
| 3 | `[3, 0, 20000, 5999, 59990, 5000]` (X +5 mm, 20 mm/s), poll 2000; repeat while moving | **O6 firmware gating** (moves ⇒ no gate); exception 3 on jog-while-moving (N5); axis status byte 2/3 values |
| 4 | `[3, 0x80000000, 20000, 5999, 59990, 100000]` twice | bit 31 = absolute |
| 5 | Continuous jog at 50 mm/s, release with `[1,1,2,2000,20000]` | stop profile / vd unit |
| 6 | `[2, 1, 0]` (home X), then `[2, 2, 0]` | home vector, card-side speeds and back-off, homed bit 15 |
| 7 | Press each limit switch by hand (motors disabled), read 1004 and 2000+10·slot | **O8** polarity; Y2 slot tracking during a Y jog (O7 dual drive) |
| 8 | `0x67←[1]`; send the first frame of a planned 100 mm pure-X move at 50 mm/s (duty 0, no DO9/PWM records) and read 1015/1016 before/after that frame; stream the rest, `0x67←[2]`; time the move and count 3000 items vs. elapsed time | **O1 card tick period**; reg-1016 units (C2); frame-id rule; FIFO start without licence exchange (O6) |
| 9 | Run a short dry job in the **Windows tool**: Pause, Continue, Stop | **N2 resume path**; stop-manu frames |
| 10 | Power-cycle the card and send `READ 1000/2` immediately | exception 2 semantics |

Remaining items that are *not* on the CO2 M1 path: capture G (fibre pierce stream, ZF units and 10000+2i spacing, DO5 timing, laser nameplate), session H (Wine diff: `.chf` text/spline/type-12 flags), pendant R0–R3 (USB only).

---

## 8. Corrections to earlier documents (ranked)

1. **Command semantics** (00 §4/§5.1/§10.1, 04 §3.6/§3.8, 08 §4.5):
   * sub-cmd **1 = STOP** `[1,mask,2,vd,10·vd]`, not home.
   * sub-cmd **2 = HOME** `[2,1<<slot,0]` with speeds on the card.
   * Jog targets are **relative** distances (bit 31 = absolute).
   * Jerk = 10·a.
   * `[9999,13,0xFFFF,…]` is an extended-DO bulk-off inside stop-manu, not a handshake. The connect write is `[9999,5,0,0]`.
2. **FIFO stream** (00 §4/§5.1/§9 #10/#16, 08 §4.4, 04 §3.7):
   * Ticks are **motor pulses** (X factor from the card, Y from XML), with the period taken from card reg 50005.
   * **103/109/118/2001 are ZF-follower records, not dwells**, and CO2 jobs emit them because ZFType=1.
   * Frame id = reg 1015 + 1. Frames are cut by a 300-word flush threshold, not an item count.
3. **Safety / licence register map** (99-gaps §2.4, 00 §1/§5 row 15/§9 #4, PORT-PLAN §8.2):
   * **150/151 are FTC / edge-seek registers**, not the licence switch. 150←5555 is an FTC factory reset.
   * The licence lives at **59500–59511**.
   * MainApp **does** gate Start/frame/dry-run/resume on the dog state.
   * The vendor never reads block 5000 but writes 5001/5008.
   * The idle stop path does not switch outputs off.

Further corrections (see §1): P2 = SplineAccuracyRate (05). Port 999 = EC endpoint (00 §5). AutosaveParam field 1 = 100 ms counter (01/08). `CHidUsb` VID/PID selection (01/07). `pwmCompensation.txt` not runtime data (99-gaps). `0x1004eff0` = `updateAFStatus` and `0x10052130` = 60001 reader (99-gaps/04).
