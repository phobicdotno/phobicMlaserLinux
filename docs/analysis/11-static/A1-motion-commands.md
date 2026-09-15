# A1 — Motion commands: what the Windows tool writes to the MCC100 for Stop / Pause / Resume / E-stop / Home / Jog / Go-to

Static recovery from `MainApp.exe` + `Module/NCModule.dll` (package `SRC = /home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52`, read-only). Tooling: `objdump -d -M intel` (disassemblies cached in `.scratch/asm/`), a small PE/RTTI/vtable/descriptor-table resolver (python3, stdlib), `Lang/lang.txt`, `File/BkHardPara.xml`, `File/BkManuPara.xml`, `Log/*.log`.
Conventions: **EVIDENCE** = read from the binary/file at the cited address; **INFERENCE** (confidence) = interpretation. "K" = the unit scale word described in §2. "g" = the global settings object returned by `MainApp 0x5ff1b0` (= `NCModule [this+0xb4]`, the same struct — both sides address it with identical offsets, e.g. `+0x47cc`, `+0x4356`).

Closes: **O3** (units / mask-vs-index), **O7** (which slot the lifting table is), **O18** (`[g+0x4356]`), and the PORT-PLAN "Stop/Pause/Resume/E-stop semantics" gap (99-gaps §4 row *new*). Corrects 04 §3.6 / 08 §4.5 / 00 §5.1: **sub-command 1 is STOP, sub-command 2 is HOME, sub-command 3 targets are RELATIVE distances** (see §7).

---

## 0. Transcription table (→ `mcc/commands.py`)

All commands are **write func 0x40 to register 0x65 (101)** unless stated; word 0 = sub-command. `slot` = machine axis slot 0..4 (0 = X, 1 = Y, 4 = W/lifting table on this machine, §5.3). Speeds in **0.001 mm/s**, distances in **0.001 mm** (K = 1000 on this machine, §2), accelerations in **mm/s²** as plain integers (no K), jerk = **10 × acc** (always).

| # | UI action | reg ← words | word meaning / units | Source (MainApp → NCModule) | Status |
|---|---|---|---|---|---|
| 1 | **Jog X/Y, key/button held** (continuous) | `0x65 ← [3, slot, v, a, 10·a, ±d]` | word1 = the **caller's axis-list index i** (0 = X, 1 = Y; equal to the slot only because `g+0xba5c[i]==i` on this build — [verifier]) (bit31 clear = *relative*); v = (MC.IsFastMode ? MC.JogFastSpeed : MC.JogSlowSpeed)·K, clamped ≤ FCP.MaxSpeed·K; a = int(MC.XFastMoveAcc) clamped ≤ FCP.MaxAcc; d = **trunc**(dist·K) (`_ftol2` = `cvttsd2si` at `0x10060c80` — [verifier]: not round), sign = direction; dist = **20 × MC.JogFastSpeed** (mm) when `MS.EnableSoftLimit=0` (→ ±4 000 000 in the logs), else the remaining distance to the soft limit | pendant/keyboard handler `0x57a580` (msg 0x4b1) / `0x597d40` → CNCModule **slot 21** (+0x54) `0x1002f750` → VM writeCmd | EVIDENCE (vector, units); dist formula EVIDENCE |
| 2 | **Jog step** (MC.IsStepMove=1) | same as #1 | dist = MC.StepLength (mm) | same | EVIDENCE |
| 3 | **Jog key released → stop** | `0x65 ← [1, mask, 2, vd, 10·vd]` | word2 = 2 (constant, meaning unknown); **three MainApp variants, not one** ([verifier], §4.1): (a) `0x57a580`, `MS.EnableSoftLimit=0` (this machine): mask = OR of all released axes, vd = clamp(F·100000/(v_last/K), **2000, int(0.4·FCP.MaxAcc)=8000**); (b) `0x57a580`, soft limit on: one vector per axis, mask = 1<<slot, vd clamp **[5000, 0.4·FCP.MaxAcc]**, sent only if the computed braking distance < distance to the soft limit; (c) PHBX pendant `0x597d40`: OR mask, clamp [2000, 100000]. F = MP.JogStopDccFactor, v_last = `g+0x499c`. Not sent at all when MC.IsStepMove=1 | (a)/(b) `0x57af9b–0x57b17a` / `0x57aaba–0x57af96`; (c) `0x59807b–0x5981cd`; written raw through CNCModule **slot 31** (+0x7c) = VM writeCmd | EVIDENCE (re-derived) |
| 4 | **Stop button** (ribbon mp117 "Stop", ID 0x4656; lifting-dialog button 0x2712; pendant msg 0x408; main-frame deactivation WM_ACTIVATE/WA_INACTIVE while jogging — [verifier], was "mouse-up on canvas") | VM message `{0xa,[0x18]}` → card: `0x65 ← [1, 0x1F, 2, vd, 10·vd]` **or** `0x67 ← [3]` when the FIFO is running (`[VM+0xdc]==1`), then laser/PWM/gas/DO off (§4.2) | 0x1F = all five axis slots; vd as in #3 (`0x10053df0`) | `CManuPanel::OnStopBtn` `0x5880a0` → CNCModule **slot 22** (+0x58) → VM slot 18 `0x10045e30` ("send stop manu cmd") → queue → handler `0x10056e8d` | EVIDENCE |
| 5 | **Pause** (msg 0x407 handler `0x587a30`, label mp27/mp55) | **identical to #4** — VM slot 19 `0x10045f70` ("send pause manu cmd") enqueues the same `{0xa,[0x18]}` (byte-identical function except the log text) | — | `0x587a30` → CNCModule slot 22 → VM 18 (state `g+0x47cc` := 0xb) | EVIDENCE (identity of the two functions) |
| 6 | **Resume / Continue** | **UNRESOLVED ([verifier] downgrade).** `0x5878f0` builds `0x65 ← [103, trunc(ZF.ZFUpSpeed·10), trunc(max(ZFreg(7,6)·0.001 − ZF.ZFDockHeight, 5.0)·1000)]` (a ZF head-lift, via CNCModule slot 33 = ZFType-guarded writeCmd) with no condition of its own. Its callers are the Pause handler `0x587a30` in states 0/1 when `g+0x47c8==1` — where it is **followed by slot 22 stop-manu, not by a Start** — and a script step at `0x591a26`. No restart-from-breakpoint call exists in `0x587a30`; the Run handler `0x5904a0` does not test state 0xb. How "Continue" resumes a paused job is not established statically | 103 = ZF lift-to-height (same opcode as in the VM stop handler) | `0x587a30:0x587d68/0x587dff`, `0x591a26` → `0x5878f0` | vector EVIDENCE; "resume = restart from breakpoint" **not supported** |
| 7 | **E-stop (mp124)** — pendant E-stop key, *not* a card command | user msg **0x406** → `0x58fbe0`: calls `0x5884b0` = state reset + **#4** (slot 22) + **laser off** (only if LC.LaserGateIsAutoInManu; CNCModule slot 98 `0x1002dda0`: CO2 → DO write `[9999,2,LGP.CO2DOLaserGate (g+0x83c),0]` — [verifier]: gate DO, not CO2DOLaser; fibre → per LGP.LaserControlType) + message box mp124 | — | posted by pendant handlers `0x597d40:0x598646`, `0x5988e0:0x598e82`; map entry `0x939f08` (msg 0x406 → 0x58fbe0) | EVIDENCE. The **hardware** E-stop is a card input (`DI.EStop`, alarm word 1 bit 30) — the PC only reacts (§4.4) |
| 8 | **Home one axis** | `0x65 ← [2, 1<<slot, 0]` | word1 = **bit-mask** (`shl edx,cl`), word2 = 0 | homing dialogs `0x4881e0/0x488f40/0x45c880/0x4c4b00/0x52dca0` → CNCModule **slot 23** (+0x5c) `0x10032350` | EVIDENCE |
| 9 | **Home all / system home** (arg −1) | `0x65 ← [2, mask, 0]` with mask from `g+0xba30` (machine-config type 1..8): 1 → X; 2 → Y; 3 → X\|Y; 4 → X\|Y\|0x10000; 5 → X\|Y\|(1<<Zslot); 8 → 0x10000 alone (bit 16 = a non-axis "unit", INFERENCE: Z-follower/AF); **6, 7 and any other value → mask 0, i.e. `[2, 0, 0]` is still sent** ([verifier], jump table `0x10032604`) | same | same, `0x100323bf–0x10032496` | EVIDENCE (mask build); meaning of 0x10000 INFERENCE (low) |
| 10 | **Lifting table Up / Down** (`A250410_3/4`, buttons 0x2710/0x2711, WM_LBUTTONDOWN; also keyboard keys 14/16 while key 0xb is held, `0x57b4ff/0x57b5a4`) | `0x65 ← [3, 4, v, a, 10·a, ±d]` | slot **4**; `0x57dc70` has **three branches** ([verifier], §3.2). On this machine (`MP.ExchangePlatformType=0`, `MP.RollSheetType=0`, `LPF.LiftingPlatformType=1`) the taken branch is `0x57e2a1–0x57e544`: v = min(jog speed as #1, MP.PlatformExchangeSpeed)·K; a = trunc(HPA3.Acc) (= 4000); d = (MC.IsStepMove ? MC.StepLength : **`g+0xbc40` = MAC_2.SoftLimitMaxLen = 1000 mm**)·K, negated for dir 0; **no soft-limit clip**; gate: connected and runStatus ∉ {1,2,4} | `0x4534a0` → `0x57e590` (Up, dir=1) / `0x57e570` (Down, dir=0) → `0x57dc70` → CNCModule **slot 19** (+0x4c) `0x10032160` | EVIDENCE (re-derived; log `03 04 c350 fa0 9c40 f4240` = +1 000 000 µm matches MAC_2.SoftLimitMaxLen=1000 exactly) |
| 11 | **Lifting table button released** (WM_LBUTTONUP) | = **#4** (full stop-manu, not a single-axis stop) — only if the button was pressed and **MC.IsStepMove=0** (`0x45360d`; step moves run to completion) | — | `0x4534a0:0x453644` → `0x5880a0` | EVIDENCE |
| 12 | **Lifting table "GoOrigin"** (ec14, button 0x2713) | `0x65 ← [2, 0x10, 0]` | mask bit 4 | `0x4534a0:0x4535d3` (`push 4`) → slot 23 | EVIDENCE |
| 13 | **Go-to point** (ribbon ID 0x4652, mp112 "System will go to input point") | `0x65 ← [5, mask \| 0x80000000, v, a, 10·a, x, y, w, z]` | mask = OR of 1<<slot over the configured axis list (`g+0xba58` count, `g+0xba5c..` slots); **bit31 = absolute target** (INFERENCE high, §3.4); v·K, x·K, y·K; a clamped ≤ FCP.MaxAcc; last two words = args 6/5 of the call (Z/W, 0 here) | `0x58fec0` → `0x58bf10` (scales all doubles by K) → CNCModule **slot 20** (+0x50) `0x10031f10` | EVIDENCE (vector); v/a values in the log (550 000 / 8999 = 500·EmptyMoveSpeedFactor / 5999.99·EmptyMoveAccFactor) LIKELY from caller `0x58c130` (not re-derived) |
| 14 | **Absolute single-axis move** (roll-sheet feeder "go to 0", **not the lift table** — [verifier]) | `0x65 ← [3, axis \| 0x80000000, v, a, 10·a, 0]` | `0x59ed50`: MP.RollSheetType==3 → axis word **0x80000010** (index 16 = the same "unit 16" as home-mask bit 0x10000), a = trunc(`g+0xc508`), v = MP.SingleRollSheetSpeed·K; RollSheetType==4 → axis `g+0x4ab0` \| 0x80000000, a = MAC_slot.Acceleration. Bit 31 is OR-ed by the caller (flag arg = 0) | `0x59ed6c` (`push 0x80000010`) / `0x59edea` → slot 19 (`0x59edbd`) | EVIDENCE (vector); unused on this machine (RollSheetType=0) |
| 15 | **Start job** (ribbon mp116 "Run", ID 0x4655) | `0x67 ← [1]` clear, `0x66` frames, `0x67 ← [2]` start (04 §3.5/§3.7) | — | `0x5904a0` → slot 28 setState(1) + slot 26 (`0x100349b0`, writes `_tempManuItem.olpf`) → VM "start manu" (`0x10057950`) | EVIDENCE (unchanged from 04) |
| 16 | **Two-word stop-all** | `0x65 ← [1, 0x1F]` | no decel words | edge-seek routines `0x584d30/0x5854e0/0x585b00` (mf246, `seekEdge.txt`) → CNCModule slot 25 `0x1002fab0` | EVIDENCE (vector); semantic "stop with card default decel" INFERENCE |
| 17 | **ZF (height controller) stop** | `0x65 ← [101]` (single word `0x65`) | gating differs per site ([verifier]): Stop button idle path `0x588181` → CNCModule slot 58 → slot 32 → **unguarded** VM writeReg (only `connected`, **no ZFType test**); main-frame **WM_ACTIVATE(WA_INACTIVE)** `0x498da0` (not canvas mouse-up) — only if ZFType≠0 and VM slot 61 == 4; pendant `0x57a8b6` after a ZF manual move (keys 14/16, `0x57d570/0x57d760`) — only if ZFType≠0; VM stop handler `0x10057634` — only message type 0xa, connected, ZFType≠0 | see left | EVIDENCE (sites, gates); "ZF stop" INFERENCE (medium-high: paired with ZF manual-move release and with the `[103,…]` lift) |

Not a card command: **pause = stop** (row 5); **E-stop from the UI = stop + laser off** (row 7). There is **no "resume motion" primitive** in this build. [verifier] How Continue resumes a paused job is **open** (row 6, §4.3); the earlier "restarted from the recorded breakpoint" was not supported by the handler code.

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
| 19 (+0x4c) | `0x10032160` | `[3, axis(\|bit31 if flag), min(v, MaxSpeed·K), min(a, MaxAcc), 10·a, target]`, 6 words, args (v, a, axis, target, flag) — also sets `g+0x499c := v` (**unclamped** v) and CNC state := 2 (Jog); silently does nothing unless VM runStatus == 0 and `[VM+0x76c]` ≠ 2 [verifier]; target is **not** scaled here (caller scales) |
| 20 (+0x50) | `0x10031f10` | `[5, mask(\|bit31), v, a, 10·a, dX, dY, arg6, arg5]`, 9 words; mask from `g+0xba58/0xba5c…` |
| 21 (+0x54) | `0x1002f750` | jog(axis-list index 0..5, dir): computes dist/v/a from MC.* (§3.1) and sends the 6-word sub-cmd-3 vector; gated on CNC state ∈ {0,3} and HAL state ∈ {0,2} |
| 22 (+0x58) | `0x1002b510` → VM 18 | stop-manu message |
| 23 (+0x5c) | `0x10032350` | `[2, mask, 0]` home; logs "MC 不满足回原状态" (MC not in a state that allows homing) unless connected and **VM runStatus** (slot 140 → VM slot 125 `0x100390d0`, derived from card status words, not `g+0x47cc`) ∈ {0,1} [verifier]; afterwards clears `g+0xbb54+0xa0·slot` for the X, Y **and Y2** slots regardless of which axis was homed |
| 25 (+0x64) | `0x1002fab0` | `[1, 0x1F]` |
| 26 (+0x68) | `0x100349b0` | writes `_tempManuItem.olpf` (start-manu) |
| 31 (+0x7c) | `0x1002b590` → VM 26 | raw writeCmd (used by MainApp for the per-axis stop, row 3) |
| 33 (+0x84) | `0x1002b5d0` → VM 28 | guarded writeCmd (row 6) |
| 58 / 59 (+0xe8/+0xec) | `0x1002c510/0x1002c520` | `[101]` / `[102]` single-word commands (via slot 32 `writeReg(0x65, 0x65/0x66)`) — 99-gaps §2.4 |
| 98 (+0x188) | `0x1002dda0` | laser off (CO2: DO `LGP.CO2DOLaserGate` (`g+0x83c`) := 0 [verifier] through slot 79 `[9999,2,ch,0]`; fibre: by `LGP.LaserControlType`) |
| 79 (+0x13c) | `0x1002ffd0` | `[9999, 2, ch, val]` DO write / `[9999, 13, ch, val]` extended DO |

---

## 2. Units — what `[g+0xbab0]` is (O3)

* EVIDENCE: `0xbab0` has no XML descriptor (the 1 001-record descriptor table was parsed from the initialisers `0x458412–0x7ab430`; §6 lists the offsets that do). Its only writer is `NCModule 0x10039c36` (`mov [edx+0xbab0],ecx`, ecx = `[esi+0x348]`) inside the register-copy method VM slot 130 (`0x10039ae0`), case 12 of the jump table `0x1003a14c` (index 0 reads `+0x318`). `+0x318` is `VM+0x304 + 4·5`, and `VM+0x304` is where VM slot 119 (`0x10051f20`, read `[0x30, 50000, 26]`, size check 0x74) stores the **SystemRW block**. So `K = SystemRW word 17 (0-based) = register 50017`.
* INFERENCE (**low**, verifier downgrade): word 17 *may* be **`SystemRWRegName_34` 精度系数 "coefficient of precision"** — the card's user-unit scale (units per mm). The key order cannot settle it: NCModule references keys 1–10 and 28–41 (24 names for 26 words, 04 §198). Counted contiguously from 28, word 17 would be key 35 "brake closing delay"; with keys 26–41, key 33 "spare". Only the name's meaning fits. The *value* (K = 1000) and its use as the unit scale do not depend on the name and stay EVIDENCE.
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
vector: [3, i, v, a, 10·a, trunc(dist·K)]  → VM writeCmd(0x65)  ; then CNC state := 2 ("Jog")
        ; [verifier] word1 is the ARGUMENT i (push of [ebp+8] at 0x1002f9b5), not g+0xba5c[i]; the slot is used only to index the soft-limit tables.
        ; logs contain both `03 00 …` and `03 01 …`, so word1 cannot be a bit-mask (0 would select no axis).
```
**The target word is a signed relative distance** (µm). Log cross-check: `03 01 c350 176f ea56 3d0900` = Y, 50 mm/s, 5999, 59990, +4 000 000 = 20·200 mm ✓; the "shrinking targets" of 08 §4.5 are the remaining distance to the soft limit while the head approaches it ✓. The verifier's "absolute position" reading is withdrawn.

### 3.2 W / lifting-table jog `MainApp 0x57dc70(dir)` (→ slot 19) — rewritten by verifier
```
A. MP.ExchangePlatformType (g+0x4a80) ∈ {1,3}:   gate CNC slot 12 (connected) && slot 16; runStatus ∈ {1,2,4} → message box, nothing sent
     dist = IsStepMove ? StepLength : MP.PlatformExchangeLength (g+0x4a88)                         (0x57de92–0x57decc)
     v    = min(IsFastMode ? JogFastSpeed : JogSlowSpeed, MP.PlatformExchangeSpeed g+0x4a90)          (0x57ded2–0x57df4f)
     type 1: soft-limit flag = bit31 of status(0xb,5), position = status(0xb,7)·0.001, direction word from reg 0x7ea (CNC slot 43); type 3: position = 5th double of CNC slot 105's array
     if MC.EnableECAxisSoftLimit (g+0x4b10) && flag: clip dist; |dist| ≤ 1e-5 → nothing; dir==0 → dist := −dist
     type 1 → CNCModule slot 44 (+0xb0)(v, dist) doubles  — a different path, NOT sub-cmd 3
     type 3 → slot19(round(v·K), trunc(HPA3.Acc), 4, round(dist·K), 0)                                 (0x57e1d4–0x57e26a)
B. else MP.RollSheetType (g+0x4aac) ≠ 0  → 0x59d9e0(dir)   (roll-sheet feeder, not traced)
C. else LPF.LiftingPlatformType (g+0x4d68) ≠ 0 && connected && runStatus ∉ {1,2,4}:                  (0x57e2a1–0x57e544)
     dist = IsStepMove ? StepLength : g+0xbc40  (= MAC_2.SoftLimitMaxLen, descriptor 'EtherAxisInfos_4' of MachineAxisConfig_2)
     v    = min(jog speed, MP.PlatformExchangeSpeed) ; no soft-limit clip ; |dist| ≤ 1e-5 → nothing ; dir==0 → −dist
     slot19(round(v·K), trunc(HPA3.Acc), 4, round(dist·K), 0)                                          (push 4 at 0x57e502)
```
This machine: ExchangePlatformType=0, RollSheetType=0, LiftingPlatformType=1 (`File/BkHardPara.xml` and the Wine `HardPara.xml`) → **branch C**. The five `SoftLimitMaxLen` values in the XML are 1371, 950, **1000**, 1000, 1000, so d = 1000 mm → `f4240` = 1 000 000, which is exactly the logged `03 04 c350 fa0 9c40 f4240` (slot 4, 50 mm/s, 4000, 40000). The earlier "length was evidently 1000 mm on 2025-07-08" guess is withdrawn: the length comes from MAC_2.SoftLimitMaxLen, not PlatformExchangeLength. INFERENCE (low): reading slot 2's soft-limit length for an axis-4 move looks like a vendor copy/paste; the port should reproduce the value, not the reason. `round` = helper `0x4511d0` (exact rounding mode not re-derived); `trunc` = `0x606c10` (`cvttsd2si`).

### 3.3 Absolute moves — bit 31
`0x10032160` ORs `0x80000000` into the axis word when its 5th argument (byte) is non-zero (`0x1003221f`); `0x10031f10` does the same to the mask (`0x1003200b`). MainApp passes it set for the go-to (`[5, 0x80000003, …]` in the log) and — by OR-ing it into the axis argument itself, flag = 0 — for the roll-sheet feeder "go to 0" (`push 0x80000010` at `0x59ed6c` for RollSheetType 3; `g+0x4ab0 | 0x80000000` at `0x59edea` for type 4; target 0; [verifier]: this is **not** the lift table) and clear for every jog (relative distances, §3.1). INFERENCE (medium-high; a relative move of 0 would be a no-op, which supports it): **bit 31 = absolute target coordinates**, clear = relative distance. Live check: send `[3, 0x80000000, 50000, 5999, 59990, 100000]` twice — an absolute move stays at 100 mm, a relative one would go to 200 mm.

---

## 4. Stop family — sub-command 1 in detail

### 4.1 Jog-release stop — built by MainApp (rewritten by verifier; EVIDENCE)
Handler `0x57a580` (msg 0x4b1). Keys 0x13/0x11 = X+/X−, 0xf/0x12 = Y+/Y− set flags `this+0x21b3..0x21b6`; on release the handler ORs `1<<g+0xba5c` (X) or `1<<g+0xba60` (Y) into a mask. Nothing is sent if `MC.IsStepMove`.
```
MS.EnableSoftLimit = 0  (this machine)   0x57af9b–0x57b17a
    vd  = F·100000 / (v_last / K)            integer idiv; v_last := 100000 if ≤ 0
    vd  = clamp(vd, 2000 (0x57b00d cmp 0x7d0), trunc(0.4 · FCP.MaxAcc)) (0x57b079 fild [g+0x4998]; fmul 0.4 @0x7c4700)
    vec = [1, mask (OR of released axes), 2, vd, 10·vd]  → CNCModule slot 31
MS.EnableSoftLimit = 1                  0x57aaba–0x57af96 (X at 0x57ad79–0x57ae30, Y copy after 0x57ae44)
    vd  = clamp(F·100000/(v_last/K), 5000 (0x57ab52 cmp 0x1388), trunc(0.4 · FCP.MaxAcc))
    brake = min(v·(v/300)·SP.LimitDeccLengthRatio, 5.0), or JogFastSpeed·300/1000 when IsFastMode
    per axis: rem = distance to the soft limit; if brake < rem: vec = [1, 1<<slot, 2, vd, 10·vd]; else nothing (the jog target already ends at the limit)
PHBX pendant 0x597d40 (third copy)       0x59807b–0x5981cd
    vd = clamp(F·100000/(v_last/K), 2000, 100000) ; vec = [1, mask (OR), 2, vd, 10·vd]
```
F = MP.JogStopDccFactor (`g+0x49a0`, int). The previous text gave clamp [2000, 100000] and cited `0x57ab01–0x57abfe … cmp 0x7d0` for MainApp; that range actually holds the **5000** clamp of the soft-limit branch, and the upper clamp there is **0.4·FCP.MaxAcc**, not 100000.
Log cross-check: `[1, 1, 2, 2000, 20000]` ×13, `[1, 2, 2, 2000, 20000]` ×11 (200 or 50 mm/s → 500 or 2000 → 2000); `[1, 1, 2, 5000, 50000]` ×21 and `[1, 2, 2, 5000, 50000]` ×8 — the only logged jog speeds are 50 and 200 mm/s (`c350`, `30d40`), so 5000 is the **soft-limit branch minimum**, not "a 20 mm/s jog" (withdrawn). The 5000 stops and the non-±4 000 000 jog targets in the logs both mean the soft limit was enabled at some point during the logged sessions. These are **stops, not homing**.
INFERENCE (medium-high, new): vd is a **deceleration in mm/s²** and word 4 is its jerk. MainApp clamps it against 0.4·FCP.MaxAcc (mm/s²), and word 4 = 10·vd is the same idiom as acc/jerk in sub-commands 3 and 5.

### 4.2 Stop-manu (Stop button, Pause, E-stop, lifting-button release) — VM handler `0x10056e8d` (msg types 0xa/0xb)
```
if runStatus (VM slot 125, +0x1f4) == 4  or  status block-0 word 6 ≠ 0 (alarm_1)  or  word 7 ≠ 0 (alarm_2)  (getter VM slot 49 +0xc4):   ; job running / faulted
    [VM+0x76c] := 0x18 ; 0x10053df0 →  [VM+0xdc]==1 ? stopFifo (0x67 ← [3]) : 0x65 ← [1, 0x1F, 2, vd, 10·vd]
    CO2 (g+0x46d8==1): DO g+0x88c (LGP.CO2DOLaser) off (VM +0xb8), DA g+0x4944 (LGP.CO2LaserDAPort) := 0.0 (VM +0xb0)
    0x65 ← [9999, 3, g+0x483c (LC.PtLaserFreq), 0, 0]  and  [9999, 0x11, g+0x483c, 0, 0]        ; PWM off  (0x100570a2–0x10057198)
    gas DAs g+0x904/0x908/0x90c (MGP.RatioAir/O2/H2 ≠ 0) := 0.0
    DO bulk write 0x65 ← [9999, 2, 0xFFFF, keepMask&0xFFFF] (+ [9999, 13, 0xFFFF, keepMask>>16] if EC.ECIOType)   (0x100574d6–0x100575d3)
    [verifier] if VM byte +0x3e (AF link up) && AF.AFType (g+0x4a20) ∈ {1,2}: VM slot 34 (0x1004a6c0)(0x67, 0) — a func-0x10 write to the AF (auto-focus) device, not a card 0x65 vector
    DO bulk: word3 = current DO word (status block-0 word 5, VM+0xa4) with the bits cleared for 27 fixed DO-channel parameters (1-based; g+0xe4…0x1fc, 0xbc…0x88c, 0x4f4…0x724), 2 more (+0x65c/+0x684) if MP.EnablePLCProcess1, and every entry of the vector at g+0x3474 → those outputs OFF, all others unchanged
    if type==0xa && connected && ZF.ZFType≠0:  0x65 ← [101]  (ZF stop) ; if ZF status (VM+0x370 bit12 clear, VM+0x374 bit31 set, ZFpos·0.001 ≥ MP.ZFSafeHeight, …): 0x65 ← [103, trunc(ZF.ZFUpSpeed·10), trunc(MP.ZFSafeHeight·1000)]
    if connected && fibre (g+0x46d8==0): 0x65 ← [118, 5, 0]   — [verifier]: through the ZFType-guarded writer VM slot 28, so also only when ZF.ZFType≠0
    [VM+0x76c] := 0x19 ; log "vm stop manu" ; notify UI
else:                                                                      ; idle / jogging
    0x10053df0 (as above) ; if ZF status block 7 word 3 == 0x75 (117): 0x65 ← [103, ZF.ZFUpSpeed·10, ZF.ZFDockHeight·1000] ; fibre: [118,5,0]   (both via the ZFType-guarded writer)
    log "vm stop jog"   — [verifier]: this idle branch does NOT switch off laser/PWM/gas/DO
```
`0x10053df0` (EVIDENCE): `vd = clamp(g+0x49a0·100000 / (g+0x499c / g+0xbab0), 2000, 100000)` (integer division — v_last < K would divide by zero); `g+0x499c` defaults to 100000 if 0; vector `[1, 0x1F, 2, vd, 10·vd]` → VM writeCmd(0x65). Log: `01 1f 02 7d0 4e20` ✓ (7×, all rejected with exception 3 = already stopped, INFERENCE).
Message type 0xc (`0x10057835`) = just `0x10053df0` + log "stop jog".

### 4.3 Pause == Stop
VM slot 18 `0x10045e30` and slot 19 `0x10045f70` are byte-identical after address normalisation except the log text (`L"send stop manu cmd"` / `L"send pause manu cmd"`) and both enqueue `{0xa, [0x18]}` (`mov [ebp-0x28],0xa; mov [ebp-0x10],0x18` at `0x10045e6e/0x10045fae`). MainApp's Pause handler `0x587a30` (msg 0x407) switches on `g+0x47cc` (byte table `0x587ecc`, jump table `0x587eb4`). [verifier] **States 2, 3, 0xa, 0xb**: set the button text to mp27 "继续 Continue", `g+0x47cc := 0xb`, slot 22 stop-manu if connected, `g+0x47c8 := 0`, log "Sys Pause". **States 4, 6, 0xc–0xe, 0x13, 0x14**: state := 0, stop-manu. **States 0/1 with `g+0x47c8==1`**: `0x5878f0` ZF lift and then stop-manu (state 0 only when one of about 15 alarm/flag conditions holds). **States 0x10–0x12**: `0x59ca30`. The earlier "on the second press runs `0x5878f0` + restart" is not in the code: a second press in state 0xb just stops again, and no Start/breakpoint call exists here. **For the port: Pause = stop-manu (the card has no pause primitive). How Continue resumes is OPEN** (candidates: Run button logic or the `_tempManuItem` breakpoint in slot 26; not traced).

### 4.4 E-stop
* Software side (row 7): pendant key → `PostMessage(0x406)` (`0x598646`, `0x598e82`) → `0x58fbe0` → `0x5884b0` (`g+0x47c8/0x47cc/0x4cf2 := 0`; slot 22 stop-manu; slot 98 laser off if `LC.LaserGateIsAutoInManu`) → MessageBox(mp124 "系统处于急停状态，请按确定解除" = system is in E-stop, press OK to release).
* Hardware side: `DI.EStop` (0 on this machine = not wired to the card, 01 §2.7) → alarm word 1 bit 30 (`EtherCATErrorInfo_1_30`); [verifier] `0x10056e8d` only *tests* alarm words 6/7 while processing a stop message it has already been given; no automatic alarm → stop-manu path was traced (downgrade to INFERENCE low). No dedicated "E-stop" register write exists in either binary.

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
* [verifier] arg −1 with `g+0xba30` ∈ {6, 7} or outside 1..8 still sends `[2, 0, 0]`; arg 16 (`0x10`) → mask 0x10000 and clears `g+0xc554`.
* 5.3 **Lifting table = slot 4 (O7 closed)**: `0x4534a0:0x4535d3 push 4 → slot 23` (button 0x2713 = `ec14` "回原 GoOrigin" in the `A250410_2` "升降平台 Lifting platform" dialog created at `0x452c70`), and the up/down jog passes `push 4` to slot 19 (`0x57e228`). Consistent with the log's `[3, 4, 50000, 4000, 40000, +1000000]` (only + was logged; d = MAC_2.SoftLimitMaxLen, §3.2).

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
* **No UI element, accelerator or menu carries ID 0x426a**: the immediate occurs only in the map store (`0x457b80/0x457b8a`); no `push 0x426a`; no menu/accelerator resource; no `CBCGPRibbonButton` with that ID (the visible neighbour 0x4269 has an `ON_UPDATE_COMMAND_UI`, 0x426a has none). [verifier] Re-checked: no dword/word/decimal `17002` occurrence outside the map store; RT_MENU ×2, RT_ACCELERATOR ×1 and all dialog resources are free of 0x4268–0x4271 (the 0x426a word hits in `.rsrc` all fall inside PNG/bitmap data); no ON_COMMAND_RANGE covers it. **But** the neighbouring IDs 0x426c, 0x426d, 0x426e and 0x426f are in exactly the same situation (map entry, real handler `0x492ce0/0x49a640/0x49a680/0x49aea0`, no push anywhere), so "no static reference" does not prove unreachability (a BCGP ribbon/toolbar state restored from the registry, or a computed ID, would also match this pattern). Downgrade: dead command = LIKELY, not confirmed. → **"Reconnect over TCP" is most likely a hidden command in v0.0.0.52; the shipped build always talks UDP unless `ipAdd.ini AccessType=0`.** It is not an XML parameter. Whether the card accepts TCP remains a live question (one `connect()` to 10.1.1.168:502 settles it) but is irrelevant to the port.

---

## 9. MainApp handler chain summary (EVIDENCE)

| Trigger | Message-map entry | Handler | NC calls |
|---|---|---|---|
| Ribbon mp116 "Run" (ID 0x4655, created `0x576059`) | `0x939e60` | `0x5904a0` | slot 12 connected?, slot 28 setState(1), slot 26 start(1,1,0,0,1) |
| Ribbon mp117 "Stop" (0x4656, `0x576225`) | `0x939e78` | `0x590670` → `0x5880a0` | `g+0x47c8 := 0`, `g+0x4cf2 := 0`; state 0 && connected → slot 58 `[101]` (**no ZFType gate**); state 5 → state := 0 and **nothing is sent**; else if connected: slot 22 stop-manu, then slot 89 when state 2/3 && LaserGateIsAutoInManu; not connected → state := 0 [verifier] |
| Ribbon mp115 "Path Gen" (0x4654) / go-to (0x4652, mp112) | `0x939e48` / `0x939e30` | `0x590370` / `0x58fec0` → `0x58bf10` | planner / slot 20 |
| Pendant/keyboard (WM_USER+0xb1 = 0x4b1) | `0x93a088` | `0x57a580` | slot 21 jog(axis 0/1, dir 1/0) on press (`push 1;push 0` = X+, `push 0;push 0` = X−, `push 1;push 1` = Y+, `push 0;push 1` = Y−); raw `[1,mask,2,vd,10vd]` on release |
| Pendant type 3 (PHBX) / others | — | `0x597d40`, `0x5988e0`, `0x59a010` | same, plus PostMessage 0x403..0x406 |
| User msgs 0x403/0x404/0x405/0x406/0x407/0x408/0x409/0x40a/0x40b/0x40c | `0x939ec0…0x93a010` | `0x57ef50/0x583270/0x589070/**0x58fbe0 E-stop**/**0x587a30 pause-continue**/**0x5880a0 stop**/0x58ca40/0x58d5c0/0x58c350/0x58fdc0(mf800)` | see rows 4–7 |
| **Main frame WM_ACTIVATE (0x0006)** with nState = WA_INACTIVE [verifier: map entry at `0x935b18` = {6, 0, 0, 0, sig 0x29, 0x498da0}; was "canvas WM_LBUTTONUP 0x935b20"] | `0x935b18` | `0x498da0` | if connected: runStatus == 2 → slot 22 stop-manu; runStatus ≠ 4 && ZFType≠0 && slot 141 == 4 → slot 58 `[101]` (stops a jog when the window loses focus) |
| Lifting dialog (`A250410_2`, `0x452c70`; buttons 0x2710 Up / 0x2711 Down / 0x2712 Stop / 0x2713 GoOrigin created `0x453007/0x45311e/0x453238`) | WM_LBUTTONDOWN/UP/DBLCLK hook `0x4534a0` (static map `0x7d5a40` only has 0x2712/0x2713 ON_COMMAND) | `0x57e590/0x57e570/0x5880a0/push 4→slot 23` | rows 10–12 |
| Homing prompts mf156/mf157/mf158, mp119 | — | `0x4881e0`, `0x488f40`, `0x45c880`, `0x584270` | slot 23 |

---

## 10. What still needs the live machine (one step each)

1. **K** — `READ 50000 n=26`, word 17 must be 1000 (else rescale everything). *(confirms §2)*
2. **bit 31 = absolute** — send `[3, 0x80000000, 50000, 5999, 59990, 100000]` twice from X=0: absolute → stays at 100 mm. *(§3.3)*
3. **Sub-cmd 1 decel semantics** — (verifier: statically vd looks like a deceleration in mm/s², clamped to 0.4·FCP.MaxAcc in MainApp, §4.1; the capture should confirm that) — during a 200 mm/s jog send `[1, 1, 2, 2000, 20000]` and `[1, 1]`; observe the stop profile (vd is a *speed-shaped* number: 2000 = 2 mm/s?, or a deceleration in mm/s²·?); also confirm exception 3 when the axis is already stopped. *(only the physical meaning of vd is open; the vector is certain)*
4. **Sub-cmd 2 home** — `[2, 1, 0]` on X; confirm the card uses AxisRW coarse/fine speeds and what word 2 does (try 1).
5. **Mask bit 0x10000** in the system-home mask (config types 4/8) — irrelevant unless the port issues a "system home" with `g+0xba30 ∈ {4,8}`; this machine's value should be read from `MachineAxisConfig` first.
6. **`[101]` / `[103, v, h]` ZF commands** — ZFType is 1 here. Note the idle Stop button sends `[101]` even with ZFType=0 (§9). Capture one Stop from idle and check the Z-follower reaction.
8. **Continue after Pause** — press Pause during a job, then Continue, and capture the 0x65/0x66/0x67 traffic (the static trace of the resume path is open, §4.3).
7. TCP acceptance (O18) — optional: `connect()` 10.1.1.168:502.
Everything else in §0 is transcribable now.

---

## Verification notes

Adversarial re-derivation, 2026-09-15. Every address in the key-facts list was re-disassembled from `MainApp.exe` / `Module/NCModule.dll` (`.scratch/asm/*.asm`). Vtables, jump tables and constants were decoded straight from the PE bytes. The g-offset → XML-key map was rebuilt by parsing all 971 descriptor initialisers (record layout: group / key / label strings at −0x54/−0x38/−0x1c from the offset field, type at +4). Parameter values come from `File/BkHardPara.xml`, `File/BkManuPara.xml` and the Wine `HardPara.xml`. Vector counts come from `Log/*.log`.

**Confirmed as stated**
* The writeCmd frame is `[0x40 (ds:0x1008a670), reg, n, words]` (`0x100497d0`); writeReg is `[0x40, reg, 1, v]` (`0x100494e0`). The guarded copies VM slots 28/29 (`0x10038c90/0x10038c60`) require connected && `g+0x490c` (ZFType) ≠ 0. CNCModule vtable `0x1008a1c4` (RTTI `.?AVCNCModule@@`): slots 19/20/21/22/23/25/26/31/32/33/58/59/98 resolve as listed. VM vtable `0x1008bbc4` (`.?AVCVirtualMachine@@`), singleton `0x100b3db0`.
* Sub-cmd 2 = home `[2, mask, 0]` with `mask = 1<<arg` (`0x1003248a`) or the `g+0xba30` table (`0x10032604`). Sub-cmd 1 stop-all `[1,0x1F,2,vd,10vd]` / stopFifo `0x67←[3]` (`0x10050e43..0x10050e7b`). Sub-cmd 5 mask via `shl` + bit 31 (`0x1003200b`).
* The sub-cmd 3 jog builder `0x1002f750`: dist = 20·JogFastSpeed (ds:0x10089c70 = 20.0), `fchs` for dir 0, v·K with the FCP.MaxSpeed·K clamp, a = trunc(XFastMoveAcc) with the FCP.MaxAcc clamp, jerk = 10·a. The target is relative (log `±4 000 000`).
* Jerk/decel word = 10× in every builder: `0x1003205a`, `0x1003226c`, `0x1002f9dc`, `0x10053f0b`, MainApp `0x57aded`, `0x57b139`, `0x59819c`.
* K = `[g+0xbab0]` is written only at `0x10039c36` from `VM+0x348` = SystemRW (`[0x30, 0xc350, 0x1a]`, `0x10051faa`/`0x10051ff4`) word 17; jump table `0x1003a14c[12]` = `0x10039c2d`. Logs agree with K = 1000.
* Pause VM slot 19 `0x10045f70` equals stop VM slot 18 `0x10045e30` instruction-for-instruction; the only differences are the log string and its length (`push 0x12`/`0x13`). Both enqueue `{0xa,[0x18]}` (the key facts cite `0x10056e6e` for this; the actual address is `0x10045e6e`). VM slots 20/21 enqueue types 0xb/0xc. Dispatch: byte table `0x10057900` → `0x100578d0`: 0 → init, 0xa/0xb → `0x10056e8d`, 0xc → `0x10057835`.
* E-stop chain: map `0x939f08` = {0x406 → `0x58fbe0`}, senders `0x598646`/`0x598e82`, `0x58fc0e` → `0x5884b0`; string mp124 at `0x843830`.
* Lifting dialog hook `0x4534a0`: 0x2710 → `0x57e590` (dir 1), 0x2711 → `0x57e570` (dir 0), 0x2712 → `0x5880a0`, 0x2713 → `push 4` → slot 23 → `[2,0x10,0]`. Slot 4 for the jog (`push 4` in both slot-19 branches). HPA3.Acc (`g+0x3f18`, pd2427) = 4000.
* O18: the only writers of `[g+0x4356]` are `0x49330c` (:=1, handler `0x493300`, map `{0x111,0,0x426a,0x426a,0x39}` at `0x9357d0`) and `0x4b1d69` (:=0). The only reader is NCModule `0x10056b86`, which forces `GetPrivateProfileIntA("Soft","AccessType",1)` to 0; the logs then say "Access: TCP" vs "Access: UDP" (`0x1008be68/0x1008be80`). Tail call to `0x48f260`.

**Refuted / corrected (edited in place above, marked [verifier])**
1. *Per-axis stop vd = clamp(…, 2000, 100000) at MainApp `0x57ab01–0x57abfe`.* Wrong for MainApp. `0x57ab52` is `cmp eax,0x1388` (**5000**, soft-limit branch). The non-soft-limit branch (`0x57b00d`, this machine) clamps at 2000 but caps at **trunc(0.4·FCP.MaxAcc)** (`0x57b07f fmul ds:0x7c4700 = 0.4`), not 100000. That branch also sends one vector with the OR of the released axes, and "second copy (Y) 0x57b0da" is that combined branch, not a Y copy. Only the PHBX copy `0x59807b–0x5981cd` and the VM builder use [2000, 100000]. "5000 after a 20 mm/s jog" is withdrawn: no 20 mm/s jog appears in the logs, and 5000 is the soft-limit-branch minimum.
2. *O7 lift-jog formula (dist = MP.PlatformExchangeLength, EC soft-limit clip, `0x57de92..0x57e26a`).* That is branch A (ExchangePlatformType 1/3), which is not taken on this machine (ExchangePlatformType=0). The taken branch C (`0x57e2a1–0x57e544`, LiftingPlatformType=1) uses **`g+0xbc40` = MAC_2.SoftLimitMaxLen = 1000 mm** and no clip, which reproduces the logged `f4240` exactly. The slot (4) and the acceleration (HPA3.Acc) survive, so the O7 core answer holds.
3. *"Lift table to 0" = `push 0x80000010` at `0x59edbd`.* `0x59ed50` is the **roll-sheet feeder** (MP.RollSheetType 3/4, speed MP.SingleRollSheetSpeed). The axis word is 16|bit31 (not slot 4), bit 31 is OR-ed by the caller, and the flag argument is 0. Bit 31 = absolute stays INFERENCE, downgraded from high to medium-high.
4. *Resume = `[103,…]` + restart from breakpoint (INFERENCE high).* Not supported. `0x5878f0` has no condition of its own and is a ZF head-lift `[103, trunc(ZFUpSpeed·10), trunc(max(ZFz − ZFDockHeight, 5)·1000)]`. In `0x587a30` it is followed by **stop-manu**, not Start. The pause handler is a 21-state switch, and a second press in state 0xb stops again. Resume path marked OPEN (live step §10.8).
5. *`[101]` sent "only when ZF.ZFType≠0" at all sites; site "canvas mouse-up".* The Stop-button idle path (`0x588181` → slot 58 → slot 32 → unguarded VM writeReg) has **no ZFType test**. `0x498da0` is registered for **WM_ACTIVATE** (map entry `0x935b18`: msg 6, sig 0x29) and acts on WA_INACTIVE; it is not WM_LBUTTONUP. In the VM handler, `[118,5,0]` also goes through the ZFType-guarded writer.
6. *Jog word 1 = "slot".* It is the caller's axis-list index `i` (`0x1002f9b5` pushes `[ebp+8]`). The slot `g+0xba5c[i]` is used only for the soft-limit tables. They coincide because `g+0xba5c=0, g+0xba60=1` (01 §872). Logs with `03 00 …` prove it is not a mask.
7. *`round(dist·K)` in NCModule.* It is truncation (`_ftol2` → `cvttsd2si`, `0x10060c80`).
8. *Home "CNC state ∈ {0,1}"; jog "HAL state ∈ {0,2}".* Both are **VM runStatus** (CNC slot 140 → VM slot 125 `0x100390d0`, derived from card status words; 4 when block-0 word 19 low byte = 1). Slot 19/20 additionally need runStatus == 0 and `[VM+0x76c]` ≠ 2, otherwise they do nothing silently.
9. *Stop handler "alarm bits 6/7".* These are whole status block-0 **words** 6/7 (alarm_1/alarm_2, VM+0xa8/+0xac) tested ≠ 0 through getter VM slot 49 `0x10038d40`. The E-stop "core loop takes the stop-manu path" claim: no such automatic path was traced; downgraded.
10. *Slot 98 CO2 laser off writes LGP.CO2DOLaser.* It writes **LGP.CO2DOLaserGate** (`g+0x83c`) := 0 and clears `g+0x49a6`. (The VM stop handler does switch `g+0x88c` CO2DOLaser off.)
11. *O18 "no UI element → dead" as confirmed.* Downgraded to LIKELY. IDs 0x426c–0x426f are equally unreferenced but have substantive handlers, so the absence of a static reference is not proof of unreachability.
12. *K = SystemRWRegName_34 (medium).* Downgraded to low; the key order does not determine the word→name mapping.

**Added (missed in the same functions)**
* VM stop handler, running branch: an **AF device write** (VM slot 34 `0x1004a6c0`, func 0x10, `(0x67, 0)`) when AF.AFType ∈ {1,2}. The DO bulk word is "current DO word with the configured output-channel bits cleared" (27 fixed + 2 PLC + the g+0x3474 list). The idle branch does **not** switch laser/PWM/gas/DO off.
* Home with `g+0xba30` ∈ {6,7} or out of range still sends `[2, 0, 0]`. Home clears the X, Y and Y2 "homed" flags whatever axis was homed.
* `0x10053df0` uses integer `v_last / K`: v_last < K (< 1 mm/s) divides by zero.
* Stop button in state 5 resets the state and sends **nothing**. Lifting-button release does not stop a step move (`MC.IsStepMove`). Jog-release stops are not sent for step moves.
* Keyboard/pendant keys 14/16 jog the lift table when key 0xb is held (`0x57b4ff/0x57b5a4`). Without it they call the ZF manual moves `0x57d570/0x57d760`, whose release sends `[101]`.
* Stop decel word: vd is capped by 0.4·FCP.MaxAcc in MainApp, so it is most likely a deceleration in mm/s² (INFERENCE medium-high).

**Net verdict**: The command vectors for sub-commands 1, 2, 3 and 5 (word order, index vs mask, K scaling, 10× jerk, relative targets) hold, as do stop/pause identity, the E-stop chain, the lift-table slot 4 and the O18 flag mechanics. Four things were wrong or overstated: the MainApp stop clamps, the lift-jog branch and distance source, the "resume" semantics, and the `[101]` gating and site list. They are corrected above. O3: closed. O7: closed, with the distance source corrected. O18: mechanics closed, "dead command" likely. Stop/Pause/E-stop: closed. Resume: open.
