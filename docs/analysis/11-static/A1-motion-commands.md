# A1 — Motion commands: what the Windows tool writes to the MCC100 for Stop / Pause / Resume / E-stop / Home / Jog / Go-to

Static recovery from `MainApp.exe` + `Module/NCModule.dll` (package `SRC = /home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52`, read-only). Tooling: `objdump -d -M intel` (disassemblies cached in `.scratch/asm/`), a small PE/RTTI/vtable/descriptor-table resolver (python3, stdlib), `Lang/lang.txt`, `File/BkHardPara.xml`, `File/BkManuPara.xml`, `Log/*.log`.
Conventions: **EVIDENCE** = read from the binary/file at the cited address; **INFERENCE** (confidence) = interpretation. "K" = the unit scale word described in §2. "g" = the global settings object returned by `MainApp 0x5ff1b0` (= `NCModule [this+0xb4]`, the same struct — both sides address it with identical offsets, e.g. `+0x47cc`, `+0x4356`).

Closes: **O3** (units / mask-vs-index), **O7** (which slot the lifting table is), **O18** (`[g+0x4356]`), and the PORT-PLAN "Stop/Pause/Resume/E-stop semantics" gap (99-gaps §4 row *new*). Corrects 04 §3.6 / 08 §4.5 / 00 §5.1: **sub-command 1 is STOP, sub-command 2 is HOME, sub-command 3 targets are RELATIVE distances** (see §7).

---

## 0. Transcription table (→ `mcc/commands.py`)

All commands are **write func 0x40 to register 0x65 (101)** unless stated; word 0 = sub-command. `slot` = machine axis slot 0..4 (0 = X, 1 = Y, 4 = W/lifting table on this machine, §5.3). Speeds in **0.001 mm/s**, distances in **0.001 mm** (K = 1000 on this machine, §2), accelerations in **mm/s²** as plain integers (no K), jerk = **10 × acc** (always).

| # | UI action | reg ← words | word meaning / units | Source (MainApp → NCModule) | Status |
|---|---|---|---|---|---|
| 1 | **Jog X/Y, key/button held** (continuous) | `0x65 ← [3, slot, v, a, 10·a, ±d]` | slot = axis slot index (bit31 clear = *relative*); v = (MC.IsFastMode ? MC.JogFastSpeed : MC.JogSlowSpeed)·K, clamped ≤ FCP.MaxSpeed·K; a = int(MC.XFastMoveAcc) clamped ≤ FCP.MaxAcc; d = round(dist·K), sign = direction; dist = **20 × MC.JogFastSpeed** (mm) when `MS.EnableSoftLimit=0` (→ ±4 000 000 in the logs), else the remaining distance to the soft limit | pendant/keyboard handler `0x57a580` (msg 0x4b1) / `0x597d40` → CNCModule **slot 21** (+0x54) `0x1002f750` → VM writeCmd | EVIDENCE (vector, units); dist formula EVIDENCE |
| 2 | **Jog step** (MC.IsStepMove=1) | same as #1 | dist = MC.StepLength (mm) | same | EVIDENCE |
| 3 | **Jog key released → stop that axis** | `0x65 ← [1, 1<<slot, 2, vd, 10·vd]` | mask = 1<<slot; word2 = 2 (mode, constant); vd = clamp(MP.JogStopDccFactor·100000·K / v_last, 2000, 100000) with v_last = last commanded jog speed word (`g+0x499c`) | built **inside MainApp** `0x57ad79–0x57ae30` (X), `0x57b0da–0x57b17a` (Y), `0x59815b–0x5981cd`; written raw through CNCModule **slot 31** (+0x7c) = VM writeCmd | EVIDENCE |
| 4 | **Stop button** (ribbon mp117 "Stop", ID 0x4656; lifting-dialog button 0x2712; pendant msg 0x408; mouse-up on canvas) | VM message `{0xa,[0x18]}` → card: `0x65 ← [1, 0x1F, 2, vd, 10·vd]` **or** `0x67 ← [3]` when the FIFO is running (`[VM+0xdc]==1`), then laser/PWM/gas/DO off (§4.2) | 0x1F = all five axis slots; vd as in #3 (`0x10053df0`) | `CManuPanel::OnStopBtn` `0x5880a0` → CNCModule **slot 22** (+0x58) → VM slot 18 `0x10045e30` ("send stop manu cmd") → queue → handler `0x10056e8d` | EVIDENCE |
| 5 | **Pause** (msg 0x407 handler `0x587a30`, label mp27/mp55) | **identical to #4** — VM slot 19 `0x10045f70` ("send pause manu cmd") enqueues the same `{0xa,[0x18]}` (byte-identical function except the log text) | — | `0x587a30` → CNCModule slot 22 → VM 18 (state `g+0x47cc` := 0xb) | EVIDENCE (identity of the two functions) |
| 6 | **Resume / Continue** | no dedicated card command: MainApp `0x5878f0` writes `0x65 ← [103, round(ZF.ZFUpSpeed·10), round(x)]` (ZF lift, only when ZF status bits 7/6 of reg-block 0xb say so) and then re-issues **Start** from the breakpoint (`_tempManuItem.olpf`, CNCModule slot 26 `0x100349b0`, state 0x47c8) | 103 = the same opcode seen in-stream (dwell/Z-lift family) | `0x587a30` (when `g+0x47c8==1`) → `0x5878f0` → CNCModule slot 33 (+0x84, guarded writeCmd) | EVIDENCE for the vector; "then Start" INFERENCE (high) |
| 7 | **E-stop (mp124)** — pendant E-stop key, *not* a card command | user msg **0x406** → `0x58fbe0`: calls `0x5884b0` = state reset + **#4** (slot 22) + **laser off** (CNCModule slot 98 `0x1002dda0`: CO2 → DO write `[9999,2,LGP.CO2DOLaser,0]`; fibre → per LGP.LaserControlType) + message box mp124 | — | posted by pendant handlers `0x597d40:0x598646`, `0x5988e0:0x598e82`; map entry `0x939f08` (msg 0x406 → 0x58fbe0) | EVIDENCE. The **hardware** E-stop is a card input (`DI.EStop`, alarm word 1 bit 30) — the PC only reacts (§4.4) |
| 8 | **Home one axis** | `0x65 ← [2, 1<<slot, 0]` | word1 = **bit-mask** (`shl edx,cl`), word2 = 0 | homing dialogs `0x4881e0/0x488f40/0x45c880/0x4c4b00/0x52dca0` → CNCModule **slot 23** (+0x5c) `0x10032350` | EVIDENCE |
| 9 | **Home all / system home** (arg −1) | `0x65 ← [2, mask, 0]` with mask from `g+0xba30` (machine-config type 1..8): 1 → X; 2 → Y; 3 → X\|Y; 4 → X\|Y\|0x10000; 5 → X\|Y\|(1<<Zslot); 8 → 0x10000 alone (bit 16 = a non-axis "unit", INFERENCE: Z-follower/AF) | same | same, `0x100323bf–0x10032496` | EVIDENCE (mask build); meaning of 0x10000 INFERENCE (low) |
| 10 | **Lifting table Up / Down** (`A250410_3/4`, buttons 0x2710/0x2711, WM_LBUTTONDOWN) | `0x65 ← [3, 4, v, a, 10·a, ±d]` | slot **4**; v = min(jog speed as #1, MP.PlatformExchangeSpeed)·K; a = int(HPA3.Acc) (= 4000 here); d = round((MC.IsStepMove ? MC.StepLength : MP.PlatformExchangeLength)·K), negative for Down, clipped to the 4th-axis soft limit when MC.EnableECAxisSoftLimit | `0x4534a0` (dialog message hook) → `0x57e590` (Up, dir=1) / `0x57e570` (Down, dir=0) → `0x57dc70` → CNCModule **slot 19** (+0x4c) `0x10032160` | EVIDENCE |
| 11 | **Lifting table button released** (WM_LBUTTONUP) | = **#4** (full stop-manu, not a single-axis stop) | — | `0x4534a0:0x453644` → `0x5880a0` | EVIDENCE |
| 12 | **Lifting table "GoOrigin"** (ec14, button 0x2713) | `0x65 ← [2, 0x10, 0]` | mask bit 4 | `0x4534a0:0x4535d3` (`push 4`) → slot 23 | EVIDENCE |
| 13 | **Go-to point** (ribbon ID 0x4652, mp112 "System will go to input point") | `0x65 ← [5, mask \| 0x80000000, v, a, 10·a, x, y, w, z]` | mask = OR of 1<<slot over the configured axis list (`g+0xba58` count, `g+0xba5c..` slots); **bit31 = absolute target** (INFERENCE high, §3.4); v·K, x·K, y·K; a clamped ≤ FCP.MaxAcc; last two words = args 6/5 of the call (Z/W, 0 here) | `0x58fec0` → `0x58bf10` (scales all doubles by K) → CNCModule **slot 20** (+0x50) `0x10031f10` | EVIDENCE (vector); v/a values in the log (550 000 / 8999 = 500·EmptyMoveSpeedFactor / 5999.99·EmptyMoveAccFactor) LIKELY from caller `0x58c130` (not re-derived) |
| 14 | **Absolute single-axis move** (lift table to 0) | `0x65 ← [3, slot \| 0x80000000, v, a, 10·a, pos]` | bit31 set in the axis word → absolute | `0x59edbd` (`push 0x80000010`, target 0) → slot 19 | EVIDENCE |
| 15 | **Start job** (ribbon mp116 "Run", ID 0x4655) | `0x67 ← [1]` clear, `0x66` frames, `0x67 ← [2]` start (04 §3.5/§3.7) | — | `0x5904a0` → slot 28 setState(1) + slot 26 (`0x100349b0`, writes `_tempManuItem.olpf`) → VM "start manu" (`0x10057950`) | EVIDENCE (unchanged from 04) |
| 16 | **Two-word stop-all** | `0x65 ← [1, 0x1F]` | no decel words | edge-seek routines `0x584d30/0x5854e0/0x585b00` (mf246, `seekEdge.txt`) → CNCModule slot 25 `0x1002fab0` | EVIDENCE (vector); semantic "stop with card default decel" INFERENCE |
| 17 | **ZF (height controller) stop** | `0x65 ← [101]` (single word `0x65`) | only when `ZF.ZFType ≠ 0` | Stop button in idle state (`0x5880a0:0x588181`, slot 58 `0x1002c510`), canvas mouse-up `0x498da0`, and the VM stop-manu handler `0x10057634` | EVIDENCE (sites); "ZF stop" INFERENCE (medium — gated by ZFType, paired with the `[103, ZFUpSpeed·10, ZFSafeHeight·1000]` lift that follows) |

Not a card command: **pause = stop** (row 5); **E-stop from the UI = stop + laser off** (row 7); **resume = re-start** (row 6). There is **no "resume motion" primitive** in this build; a paused job is restarted from the recorded breakpoint.

---

## 1. Call chain (EVIDENCE)

```
MainApp handler ──► CNCModule (INCModule vtable 0x1008a1c4, RTTI .?AVCNCModule@@, 208 slots)
                     │   MainApp reaches it through the module manager (push L"数控加工" → call [vt+0xc])
                     │   and caches the pointer in the panel object (this+0x1d8 in CManuPanel, +0x1e4 in the
                     │   lifting dialog, +0x46c in the main frame; fetch sites e.g. 0x452b6c)
                     ▼
CVirtualMachine singleton 0x100b3db0 (accessor 0x10038a10; vtable 0x1008bbc4, 149 slots)
   slot 26 (+0x68) 0x100497d0  writeCmd(reg, vector<int>)  → frame [0x40, reg, n, words…] → HAL writeReg
   slot 27 (+0x6c) 0x100494e0  writeReg(reg, value)         (single word)
   slot 28/29 (+0x70/+0x74)     guarded copies of the two (only when connected && ZFType≠0)
   slot 18..21 (+0x48..+0x54)   0x10045e30 stop-manu / 0x10045f70 pause-manu / 0x100460b0 / 0x100461a0:
                                enqueue {type, [0x18]} into [VM+0x48] (critical section 0x100b3cd8) — NOT a card write
   handler 0x10056b10 (called from mcCoreProcess 0x10057aa8): dispatch on message type via byte table
                                0x10057900 / jump table 0x100578d0:  0xa,0xb → 0x10056e8d ; 0xc → 0x10057835
   0x10053df0                   the STOP-ALL builder [1,0x1F,2,vd,10·vd]  (or stopFifo 0x10050dc0 = 0x67←[3])
```

CNCModule slots used by the motion UI (offset → function → what it writes):

| CNCModule slot | fn | writes |
|---|---|---|
| 19 (+0x4c) | `0x10032160` | `[3, axis(\|bit31 if flag), min(v, MaxSpeed·K), min(a, MaxAcc), 10·a, target]`, 6 words, args (v, a, axis, target, flag) — also sets `g+0x499c := v` and CNC state := 2 (Jog) |
| 20 (+0x50) | `0x10031f10` | `[5, mask(\|bit31), v, a, 10·a, dX, dY, arg6, arg5]`, 9 words; mask from `g+0xba58/0xba5c…` |
| 21 (+0x54) | `0x1002f750` | jog(axis-list index 0..5, dir): computes dist/v/a from MC.* (§3.1) and sends the 6-word sub-cmd-3 vector; gated on CNC state ∈ {0,3} and HAL state ∈ {0,2} |
| 22 (+0x58) | `0x1002b510` → VM 18 | stop-manu message |
| 23 (+0x5c) | `0x10032350` | `[2, mask, 0]` home; logs "MC 不满足回原状态" (MC not in a state that allows homing) when CNC state ∉ {0,1} |
| 25 (+0x64) | `0x1002fab0` | `[1, 0x1F]` |
| 26 (+0x68) | `0x100349b0` | writes `_tempManuItem.olpf` (start-manu) |
| 31 (+0x7c) | `0x1002b590` → VM 26 | raw writeCmd (used by MainApp for the per-axis stop, row 3) |
| 33 (+0x84) | `0x1002b5d0` → VM 28 | guarded writeCmd (row 6) |
| 58 / 59 (+0xe8/+0xec) | `0x1002c510/0x1002c520` | `[101]` / `[102]` single-word commands (via slot 32 `writeReg(0x65, 0x65/0x66)`) — 99-gaps §2.4 |
| 98 (+0x188) | `0x1002dda0` | laser off (CO2: DO `LGP.CO2DOLaser` := 0 through slot 79 `[9999,2,ch,0]`; fibre: by `LGP.LaserControlType`) |
| 79 (+0x13c) | `0x1002ffd0` | `[9999, 2, ch, val]` DO write / `[9999, 13, ch, val]` extended DO |

---

## 2. Units — what `[g+0xbab0]` is (O3)

* EVIDENCE: `0xbab0` has no XML descriptor (the 1 001-record descriptor table was parsed from the initialisers `0x458412–0x7ab430`; §6 lists the offsets that do). Its only writer is `NCModule 0x10039c36` (`mov [edx+0xbab0],ecx`, ecx = `[esi+0x348]`) inside the register-copy method VM slot 130 (`0x10039ae0`), case 12 of the jump table `0x1003a14c` (index 0 reads `+0x318`). `+0x318` is `VM+0x304 + 4·5`, and `VM+0x304` is where VM slot 119 (`0x10051f20`, read `[0x30, 50000, 26]`, size check 0x74) stores the **SystemRW block**. So `K = SystemRW word 17 (0-based) = register 50017`.
* INFERENCE (medium): by `lang.txt` order (keys 1–10 then 27/28–41 for the 26 words) word 17 is **`SystemRWRegName_34` 精度系数 "coefficient of precision"** — the card's user-unit scale (units per mm).
* EVIDENCE for its value on this machine: every logged jog speed is `JogSlowSpeed/JogFastSpeed × 1000` (50 → 50 000, 200 → 200 000), every distance is mm × 1000 (`±4 000 000` = 20·200 mm), the go-to coordinates are mm × 1000 → **K = 1000, i.e. speeds in 0.001 mm/s and positions in 0.001 mm (µm)**.
* How K is applied (EVIDENCE): speeds and distances are multiplied by K before being sent (`fimul [ebp-0x50]`@`0x1002f93e`, `fild [0xbab0]; fmul`@`0x1002f9ff`, `MainApp 0x436380` = `return g->0xbab0` used by `0x58bf10`, `0x57dc70`); **accelerations are not** (`MC.XFastMoveAcc` 5999.99 → 5999 as in the logs; `HPA3.Acc` 4000 → 4000); jerk is always `10·a` (`lea edx,[eax+eax*4]; add edx,edx` in every builder: `0x1003205a`, `0x1003226c`, `0x1002f9dc`, `0x10053f0b`, and MainApp `imul …,0xa` at `0x57aded`). The 04/00 statement "j = 5·a" was a misread of that idiom. Clamps: v ≤ `FCP.MaxSpeed`·K (3000 mm/s here), a ≤ `FCP.MaxAcc` (20000).
* The same K converts the card's reported values back (`fidiv [ecx+0xbab0]` at `0x10039da1/0x10039dc2/0x10039e10…` when accumulating axis travel) — consistent with "units per mm".

Live confirmation (one read): `READ 50000 n=26` → word 17 == 1000. If the machine's card ever reports another value, K changes and every speed/distance word scales with it.

---

## 3. Jog family — sub-command 3 in detail (EVIDENCE unless marked)

### 3.1 X/Y jog `0x1002f750` (CNCModule slot 21; args: `[ebp+8]` index into the configured-axis list `g+0xba5c[i]`, `[ebp+0xc]` direction byte)
```
gate:   HAL state ∈ {0,2}  &&  CNC state g+0x47cc ∈ {0,3}
dist:   g+0x4770 (MC.IsStepMove) ? g+0x4778 (MC.StepLength) : g+0x4788 (MC.JogFastSpeed) × 20.0   [ds:0x10089c70 = 20.0]
        if g+0x47c3 (MS.EnableSoftLimit):
            dir≠0: dist = min(dist, (g+0xbb60+160·slot) + (g+0xbb00+160·slot) − pos)   ; +limit − pos   (0xbb00 = MAC.SoftLimitMaxLen)
            dir=0: dist = min(dist, −((g+0xbb60+160·slot) − pos)) …  (i.e. distance to the −limit)
        else: dist = dir ? +dist : −dist                                              [fchs at 0x1002f911]
v:      (g+0x4782 MC.IsFastMode ? g+0x4788 JogFastSpeed : g+0x4790 JogSlowSpeed) × K  → int   (0x1002f93e)
a:      int(g+0x47e8 MC.XFastMoveAcc)                                                  (0x1002f946)
clamp:  v ≤ g+0x4994 (FCP.MaxSpeed)·K ; a ≤ g+0x4998 (FCP.MaxAcc) ; g+0x499c := v
vector: [3, slot, v, a, 10·a, round(dist·K)]  → VM writeCmd(0x65)  ; then CNC state := 2 ("Jog")
```
**The target word is a signed relative distance** (µm). Log cross-check: `03 01 c350 176f ea56 3d0900` = Y, 50 mm/s, 5999, 59990, +4 000 000 = 20·200 mm ✓; the "shrinking targets" of 08 §4.5 are the remaining distance to the soft limit while the head approaches it ✓. The verifier's "absolute position" reading is withdrawn.

### 3.2 W / lifting-table jog `MainApp 0x57dc70(dir)` (→ slot 19)
```
dist = g+0x4770 ? g+0x4778 (MC.StepLength) : g+0x4a88 (MP.PlatformExchangeLength = 2000)         (0x57de92–0x57decc)
v    = min(g+0x4782 ? JogFastSpeed : JogSlowSpeed, g+0x4a90 MP.PlatformExchangeSpeed = 100)      (0x57ded2–0x57df4f)
if MC.EnableECAxisSoftLimit (g+0x4b10): clip dist to the 4th-axis limits; dir==0 → dist := −dist (fmul −1.0 @0x57e17b)
call slot19(v·K, int(g+0x3f18 = HPA3.Acc = 4000), axis 4, round(dist·K), flag 0)               (0x57e1d4–0x57e26a)
```
Log cross-check: `03 04 c350 fa0 9c40 f4240` = slot 4, 50 mm/s, 4000, 40000, +1 000 000 ✓ (a and slot match; the length was evidently 1000 mm on 2025-07-08, 2000 in the shipped XML). Speeds 50 000/100 000 = JogSlowSpeed / min(JogFastSpeed 200, PlatformExchangeSpeed 100) ✓.

### 3.3 Absolute moves — bit 31
`0x10032160` ORs `0x80000000` into the axis word when its 5th argument (byte) is non-zero (`0x1003221f`); `0x10031f10` does the same to the mask (`0x1003200b`). MainApp passes it set for the go-to (`[5, 0x80000003, …]` in the log) and for "lift table to zero" (`push 0x80000010 … push 0 … push 0` at `0x59edbd`, target 0) and clear for every jog (relative distances, §3.1). INFERENCE (high): **bit 31 = absolute target coordinates**, clear = relative distance. Live check: send `[3, 0x80000000, 50000, 5999, 59990, 100000]` twice — an absolute move stays at 100 mm, a relative one would go to 200 mm.

---

## 4. Stop family — sub-command 1 in detail

### 4.1 Per-axis stop (jog key release) — built by MainApp (EVIDENCE `0x57ad6d–0x57ae30`, second copy `0x57b0ce–0x57b17a`, third `0x59814f–0x5981cd`)
```
vec = [1, 1<<g+0xba5c (X slot)  (or 1<<g+0xba60 Y slot),  2,  vd,  10·vd]
vd  = clamp( g+0x49a0 (MP.JogStopDccFactor=1) × 100000 / (g+0x499c / K) , 2000, 100000 )      (0x57ab01–0x57abfe: imul 0x186a0, idiv, cmp 0x7d0)
CNCModule slot 31 (+0x7c) → VM writeCmd(0x65, vec)
```
Log cross-check: `[1, 1, 2, 2000, 20000]`, `[1, 2, 2, 2000, 20000]` (after a 200 mm/s jog: 100000/200 = 500 → clamped to 2000), `[1, 1, 2, 5000, 50000]` (after a 20 mm/s jog) ✓. These are **stops, not homing** — the "home 1 / home 2 alternating 24× in 3 s" burst of 08 §4.5 is X/Y key releases.

### 4.2 Stop-manu (Stop button, Pause, E-stop, lifting-button release) — VM handler `0x10056e8d` (msg types 0xa/0xb)
```
if runStatus (VM slot 0x1f4) == 4  or  alarm bits 6/7 (VM +0xc4 (6), (7)):      ; job running / faulted
    [VM+0x76c] := 0x18 ; 0x10053df0 →  [VM+0xdc]==1 ? stopFifo (0x67 ← [3]) : 0x65 ← [1, 0x1F, 2, vd, 10·vd]
    CO2 (g+0x46d8==1): DO g+0x88c (LGP.CO2DOLaser) off (VM +0xb8), DA g+0x4944 (LGP.CO2LaserDAPort) := 0.0 (VM +0xb0)
    0x65 ← [9999, 3, g+0x483c (LC.PtLaserFreq), 0, 0]  and  [9999, 0x11, g+0x483c, 0, 0]        ; PWM off  (0x100570a2–0x10057198)
    gas DAs g+0x904/0x908/0x90c (MGP.RatioAir/O2/H2 ≠ 0) := 0.0
    DO bulk write 0x65 ← [9999, 2, 0xFFFF, keepMask&0xFFFF] (+ [9999, 13, 0xFFFF, keepMask>>16] if EC.ECIOType)   (0x100574d6–0x100575d3)
    if type==0xa && connected && ZF.ZFType≠0:  0x65 ← [101]  (ZF stop) ; if ZF flags: 0x65 ← [103, ZF.ZFUpSpeed·10, MP.ZFSafeHeight·1000]
    if connected && fibre: 0x65 ← [118, 5, 0]
    [VM+0x76c] := 0x19 ; log "vm stop manu" ; notify UI
else:                                                                      ; idle / jogging
    0x10053df0 (as above) ; if status(7)==0x75: 0x65 ← [103, ZF.ZFUpSpeed·10, ZF.ZFDockHeight·1000] ; fibre: [118,5,0]
    log "vm stop jog"
```
`0x10053df0` (EVIDENCE): `vd = clamp(g+0x49a0·100000 / (g+0x499c / g+0xbab0), 2000, 100000)`; `g+0x499c` defaults to 100000 if 0; vector `[1, 0x1F, 2, vd, 10·vd]` → VM writeCmd(0x65). Log: `01 1f 02 7d0 4e20` ✓ (7×, all rejected with exception 3 = already stopped, INFERENCE).
Message type 0xc (`0x10057835`) = just `0x10053df0` + log "stop jog".

### 4.3 Pause == Stop
VM slot 18 `0x10045e30` and slot 19 `0x10045f70` are byte-identical after address normalisation except the log text (`L"send stop manu cmd"` / `L"send pause manu cmd"`) and both enqueue `{0xa, [0x18]}` (`mov [ebp-0x28],0xa; mov [ebp-0x10],0x18` at `0x10045e6e/0x10045fae`). MainApp's Pause handler `0x587a30` (msg 0x407; label mp27 "Continue" is the toggled text) sets `g+0x47cc := 0xb`, calls CNCModule slot 22, and on the second press (`g+0x47c8==1`) runs `0x5878f0` + restart. **For the port: Pause must be implemented as a stop that records the breakpoint; the card has no pause/resume.**

### 4.4 E-stop
* Software side (row 7): pendant key → `PostMessage(0x406)` (`0x598646`, `0x598e82`) → `0x58fbe0` → `0x5884b0` (`g+0x47c8/0x47cc/0x4cf2 := 0`; slot 22 stop-manu; slot 98 laser off if `LC.LaserGateIsAutoInManu`) → MessageBox(mp124 "系统处于急停状态，请按确定解除" = system is in E-stop, press OK to release).
* Hardware side: `DI.EStop` (0 on this machine = not wired to the card, 01 §2.7) → alarm word 1 bit 30 (`EtherCATErrorInfo_1_30`); the PC's core loop then takes the same stop-manu path via the alarm-bit test in `0x10056e8d`. No dedicated "E-stop" register write exists in either binary.

---

## 5. Home family — sub-command 2 (`0x10032350`, CNCModule slot 23; EVIDENCE)
```
gate: connected (slot 12 → VM byte +0x38) && CNC state ∈ {0,1}; else log "MC 不满足回原状态"
arg ≥ 0 : mask = 1 << arg
arg = −1: switch g+0xba30 (1..8): 1 → 1<<Xslot ; 2 → 1<<Yslot ; 3 → X|Y ; 4 → X|Y|0x10000 ; 5 → (1<<g+0x4a30)|X|Y ; 8 → 0x10000 ; (6,7 → default)
vector: [2, mask, 0] → VM writeCmd(0x65)   (0x100324a6–0x100324e6)
then: g+0xbb54+160·slot := 0 for X, Y, Y2 slots ("homed" flags), and g+0xc554 := 0 when arg == 0x10
```
* Word 1 is a **bit-mask** (`mov edx,1; shl edx,cl`) — index-vs-mask closed. Word 2 is a constant 0 (built from `[ebp+8] := 0`). The coarse/fine homing speeds are **not** in the command: they live on the card (`AxisRW` +5/+6 = `MAC.FastSpeed`/`SecondSpeed`, 50200 block) — INFERENCE (high) from the register map.
* 5.3 **Lifting table = slot 4 (O7 closed)**: `0x4534a0:0x4535d3 push 4 → slot 23` (button 0x2713 = `ec14` "回原 GoOrigin" in the `A250410_2` "升降平台 Lifting platform" dialog created at `0x452c70`), and the up/down jog passes `push 4` to slot 19 (`0x57e228`). Consistent with the log's `[3, 4, 50000, 4000, 40000, ±1000000]`.

Note: the logged `[1, mask, 2, vSlow, vFast]` vectors were previously read as homing; no homing command was ever logged (homing succeeds and successes are not logged).

---

## 6. Parameter offsets used above (EVIDENCE — descriptor initialisers in MainApp; values from this machine's XML)

| g+off | key (label) | value here |
|---|---|---|
| 0x4770 / 0x4778 | MC.IsStepMove (pd74) / MC.StepLength (pd81) | 0 / 1 mm |
| 0x4782 / 0x4788 / 0x4790 | MC.IsFastMode (pd75) / MC.JogFastSpeed (pd82) / MC.JogSlowSpeed (pd83) | 1 / 200 / 50 mm/s |
| 0x47e8 | MC.XFastMoveAcc (pd90 "空走加速度") | 5999.99 mm/s² |
| 0x4994 / 0x4998 | FCP.MaxSpeed (pd509) / FCP.MaxAcc (pd510) | 3000 / 20000 |
| 0x499c | (runtime) last commanded jog speed word | — |
| 0x49a0 | MP.JogStopDccFactor (pd1551 手动移动停止减速系数) | 1 |
| 0x47c3 | MS.EnableSoftLimit (pd106) | 0 |
| 0x4a88 / 0x4a90 / 0x4a80 | MP.PlatformExchangeLength (pd998) / MP.PlatformExchangeSpeed (pd999) / MP.ExchangePlatformType (pd983) | 2000 mm / 100 mm/s / 0 |
| 0x3f18 | HPA3.Acc (pd2427 第4轴.加速度) | 4000 |
| 0x4b10 | MC.EnableECAxisSoftLimit (pd1205) | 0 |
| 0x4d68 | LPF.LiftingPlatformType (A250410_1) | 1 |
| 0x483c | LC.PtLaserFreq (pd95) | 1234 |
| 0x88c / 0x4944 | LGP.CO2DOLaser (pd264) / LGP.CO2LaserDAPort (pd260) | 9 / — |
| 0x49d0 / 0x49e8 / 0x4ce8 | ZF.ZFUpSpeed (pd657) / ZF.ZFDockHeight (pd631) / MP.ZFSafeHeight (pd949) | 100 / 20 / 15 |
| 0x490c / 0x4890 | ZF.ZFType (pd248) / AX.InterpolationCycle (pd238) — **both are overwritten from the card's SystemRW block** by `0x10039ae0` (words 5/6) | 1 / 250 |
| 0x46d8 | SP.m_iEnableLaserType (0 fibre, 1 CO2) | 1 |
| 0xba30, 0xba58, 0xba5c…, 0xbb54, 0xbb60, 0xbab0, 0x47cc, 0x47c8, 0x4356, 0x4310 | no descriptor → runtime state (axis-config type, configured-axis list, homed flags, −soft-limit, K, CNC state, pause flag, TCP flag, restart flag) | — |

---

## 7. Corrections to earlier documents

1. 04 §3.6 / 00 §5.1 row "`[1, axisMask?, mode, speedA, speedB]` = go-origin" → **STOP** (`[1, mask, 2, vd, 10·vd]`); `[2, mask, 0]` = **home**. 08 §4.5 "homing: axis 1 … axis 31" → per-axis / all-axis stops after jog-key release (their exception-3 replies = axis already stopped, INFERENCE).
2. "target absolute position in 0.001 mm" (08 §4.5 `[likely]`, 00 §1) → **relative distance** unless bit 31 is set (§3.3).
3. "j = 5·a" (04 §3.6) → **j = 10·a**.
4. 99-gaps §2.4 "0x1002c510 writes reg 0x65 ← [0x65]" — correct, but it goes through CNCModule slot 32 = VM `writeReg(0x65, 0x65)`, and it is the **ZF-stop** used by the Stop button / stop-manu, not a licence thunk.
5. PORT-PLAN §8.2 allow-list: sub-cmd **1** (stop) must be *allowed* (it is the safety primitive), sub-cmd 2 (home) allowed, `[0x65]`/`[0x66]` allowed only when ZFType≠0.

---

## 8. O18 — `[g+0x4356]` (EVIDENCE)
* Written `:= 1` only at `MainApp 0x49330c` inside `0x493300`, which is the **`ON_COMMAND(0x426a)` handler** — the message map is built at run time by `0x457b60…` (`mov ds:0x9357d0..0x9357e4, {0x111, 0, 0x426a, 0x426a, 0x39, 0x493300}`); after setting the flag it tail-calls `0x48f260`, the handler of command **0xcf0b** = ribbon button **mf130 "硬件重连 Reconnect"** (`CBCGPRibbonButton(0xcf0b, …)` at `0x484416`, label pushed at `0x484342`): `IsNetworkAlive()`, `[this+0x46c]->vt+0x18` (NC re-init), `g+0x4310 := 1`, `softPara.ini [SC2000] NormalExit=1`, `Sleep(500)`.
* Written `:= 0` at `0x4b1d69` in the settings-defaults initialiser (`0x4b1d30…`, which also sets `+0x4028 := 3` etc.).
* Read only by `NCModule 0x10056b86` (VM message type 0 = init): if non-zero, `AccessType` from `ipAdd.ini [Soft]` is forced to 0 → TCP (`socket(2,1,6)+connect`).
* **No UI element, accelerator or menu carries ID 0x426a**: the immediate occurs only in the map store (`0x457b80/0x457b8a`); no `push 0x426a`; no menu/accelerator resource; no `CBCGPRibbonButton` with that ID (the visible neighbour 0x4269 has an `ON_UPDATE_COMMAND_UI`, 0x426a has none). → **"Reconnect over TCP" is a dead/hidden command in v0.0.0.52; the shipped build always talks UDP unless `ipAdd.ini AccessType=0`.** It is not an XML parameter. Whether the card accepts TCP remains a live question (one `connect()` to 10.1.1.168:502 settles it) but is irrelevant to the port.

---

## 9. MainApp handler chain summary (EVIDENCE)

| Trigger | Message-map entry | Handler | NC calls |
|---|---|---|---|
| Ribbon mp116 "Run" (ID 0x4655, created `0x576059`) | `0x939e60` | `0x5904a0` | slot 12 connected?, slot 28 setState(1), slot 26 start(1,1,0,0,1) |
| Ribbon mp117 "Stop" (0x4656, `0x576225`) | `0x939e78` | `0x590670` → `0x5880a0` | slot 13, slot 58 `[101]` (idle only), slot 22 stop-manu, slot 89 (when state 2/3 && LaserGateIsAutoInManu) |
| Ribbon mp115 "Path Gen" (0x4654) / go-to (0x4652, mp112) | `0x939e48` / `0x939e30` | `0x590370` / `0x58fec0` → `0x58bf10` | planner / slot 20 |
| Pendant/keyboard (WM_USER+0xb1 = 0x4b1) | `0x93a088` | `0x57a580` | slot 21 jog(axis 0/1, dir 1/0) on press (`push 1;push 0` = X+, `push 0;push 0` = X−, `push 1;push 1` = Y+, `push 0;push 1` = Y−); raw `[1,mask,2,vd,10vd]` on release |
| Pendant type 3 (PHBX) / others | — | `0x597d40`, `0x5988e0`, `0x59a010` | same, plus PostMessage 0x403..0x406 |
| User msgs 0x403/0x404/0x405/0x406/0x407/0x408/0x409/0x40a/0x40b/0x40c | `0x939ec0…0x93a010` | `0x57ef50/0x583270/0x589070/**0x58fbe0 E-stop**/**0x587a30 pause-continue**/**0x5880a0 stop**/0x58ca40/0x58d5c0/0x58c350/0x58fdc0(mf800)` | see rows 4–7 |
| Canvas WM_LBUTTONUP | `0x935b20` | `0x498da0` | slot 22 stop-manu, slot 141, slot 58 `[101]` |
| Lifting dialog (`A250410_2`, `0x452c70`; buttons 0x2710 Up / 0x2711 Down / 0x2712 Stop / 0x2713 GoOrigin created `0x453007/0x45311e/0x453238`) | WM_LBUTTONDOWN/UP/DBLCLK hook `0x4534a0` (static map `0x7d5a40` only has 0x2712/0x2713 ON_COMMAND) | `0x57e590/0x57e570/0x5880a0/push 4→slot 23` | rows 10–12 |
| Homing prompts mf156/mf157/mf158, mp119 | — | `0x4881e0`, `0x488f40`, `0x45c880`, `0x584270` | slot 23 |

---

## 10. What still needs the live machine (one step each)

1. **K** — `READ 50000 n=26`, word 17 must be 1000 (else rescale everything). *(confirms §2)*
2. **bit 31 = absolute** — send `[3, 0x80000000, 50000, 5999, 59990, 100000]` twice from X=0: absolute → stays at 100 mm. *(§3.3)*
3. **Sub-cmd 1 decel semantics** — during a 200 mm/s jog send `[1, 1, 2, 2000, 20000]` and `[1, 1]`; observe the stop profile (vd is a *speed-shaped* number: 2000 = 2 mm/s?, or a deceleration in mm/s²·?); also confirm exception 3 when the axis is already stopped. *(only the physical meaning of vd is open; the vector is certain)*
4. **Sub-cmd 2 home** — `[2, 1, 0]` on X; confirm the card uses AxisRW coarse/fine speeds and what word 2 does (try 1).
5. **Mask bit 0x10000** in the system-home mask (config types 4/8) — irrelevant unless the port issues a "system home" with `g+0xba30 ∈ {4,8}`; this machine's value should be read from `MachineAxisConfig` first.
6. **`[101]` / `[103, v, h]` ZF commands** — only when `ZF.ZFType≠0` (it is 1 here): capture one Stop from idle and check the Z-follower reaction.
7. TCP acceptance (O18) — optional: `connect()` 10.1.1.168:502.
Everything else in §0 is transcribable now.
