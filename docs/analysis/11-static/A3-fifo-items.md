# A3 — Job-stream item format (register 0x66): tick unit, packers, opcode table, DO/DA encoding, fillFifo

Static recovery from `MainApp.exe`, `Module/NCModule.dll` and `Module/CADModule.dll` (package `SRC = /home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52`, read-only). Tooling: `objdump -d -M intel` (cached in `.scratch/asm/`), a small PE section/vtable reader (python3 stdlib), `Lang/lang.txt`, `File/ipAdd.ini`, the 30 logged `40 66 …` frames in `Log/2025-07-13.log` and `Log/2025-07-17.log` (22 distinct frame ids).
Conventions: **EVIDENCE** = read from the binary/file at the cited VA; **INFERENCE (confidence)** = interpretation. `g` = the global settings object (`MainApp 0x5ff1b0` = `NCModule [this+0xb4]`, same offsets as in A1/A2); `VM` = the `CVirtualMachine` singleton (vtable `0x1008bbc4`); `K` = `[g+0xbab0]` = card units per mm (= 1000 here, A1 §2). Record = the 28-byte job record described in §2.

Closes **O1** (tick unit and the PC-side tick period, frame-id semantics, `MaxItemPerFrame`) and **O4** (opcode semantics, DO bit rule); closes 04 §10 "is w1 (dx,dy) or (dy,dx)". Corrects 08 §4.4 / 00 §4: opcodes **103/109 are not dwells** (they are ZF-axis moves), the **tick unit is pulses, not µm**, and the **frame id is the card's own FIFO-frame-id register + 1**, not a PC counter.

---

## 0. Results at a glance

| Question | Answer | Status |
|---|---|---|
| (a) unit of the opcode-3000 increment | **motor pulses**: per tick `Δ = trunc(Δmm · WritePluse/SpeedRatio + carry)` with a per-axis residual carry (error diffusion); 8000 p/rev ÷ 31.003 mm ⇒ 258.04 p/mm on X. Computed in **MainApp** `0x437500` from mm points that CADModule slot `+0x21c` (`0x100f6080`) returns; NCModule never rescales. **[verifier]** the factor fields of slot 0 (X) are overwritten from the card's axis-0 RW words 9/10 by `0x10039ae0`, but that loop steps **two slots at a time** (slots 0,2,4), so **slot 1 (Y) keeps the XML `MAC_1.WritePluse / MAC_1.SpeedRatio`** — see Verification notes V1. | EVIDENCE §3 (source of the Y factor corrected) |
| (a) tick period assumed by the PC | `AX.InterpolationCycle` (`g+0x4890`, µs, = 250): every "hold for *t* ms" is emitted as `t·1000/InterpolationCycle` ticks (`0x4373c0`, `0x4409a0`); the sampler in CADModule uses the same dt (05 §). Whether the card really clocks 250 µs is the one live check left. **[verifier]** `g+0x4890` is itself overwritten from the card's SystemRW word 5 (reg 50005, `SystemRWRegName_6` 总线周期 "bus cycle") at `0x10039b0f–0x10039b18`, so the PC's tick period is the card-reported bus cycle whenever that refresh has run (V3). | EVIDENCE (PC side) / **L** (card side) |
| (b) 3000 packing | `w1 = (u16(dY) << 16) \| u16(dX)`, **X (axis slot 0) in the low half**, two's-complement int16 each, taken from record cells +0/+4 by `movsx`; `w2 = (freq << 16) \| duty`, freq defaults to 5000 when the record's freq < 1. | EVIDENCE §4.2 |
| (c) other opcodes | full table in §5: 3001 = auto boundary marker, 3002 = mode from record, 9999 sub 2/13 = DO, sub 4 = DA, sub 3/0x11 = PWM set, 103/109/105/104/106/108 = ZF (height-follower) family, 2001 = wait/config with `FollowOvertime` timeout, 118 = ZF bookkeeping, 2002/2004/3 = fibre-only records. | EVIDENCE (packers) / INFERENCE (meaning) |
| (d) DO writer | mask **is a bit mask**: `9999[2, 1<<(port-1), value<<(port-1)]` for XML ports 1..10 (sub 13 for ports 11..26); in-stream records carry `port-1` (MainApp subtracts 1). DA: `9999[4, channel, value]`, value in mV clamped to 50..10000 (0x65 path). | EVIDENCE §6 |
| (e) fillFifo | frame id = **card's reg 1015 (FIFO frame id) + 1**, then +1 per frame within one `fillFifo` call (≤ 50 frames/call); a frame is sent only if `bytes + 2000 ≤ reg 1016 (space margin)`; frames are closed by the item builder when the vector reaches **300 words** (4-word prefix + ≥296 data words) ⇒ 297 data words for 3-word ticks; `MaxItemPerFrame=60` is not used. | EVIDENCE §7 |
| leaked frames | all 22 distinct frames (30 logged) decode with zero remainder **and re-encode byte-identically** with the grammar in §8/§9 (**[verifier]** re-run independently: 22/22; note this only proves the length-prefixed framing, not item semantics). | EVIDENCE |

---

## 1. Where the stream is built (call chain, EVIDENCE)

```
CADModule  slot +0x21c = 0x100f6080  (thunk 0x100e4300; ret 0x20: out vector<{x,y} doubles>, 3 doubles, int)
           slot +0x210 = 0x100f4e90  duty  per point (u8)   ┐ adjustor thunks 0x100e42e0/0x100e42f0 (this+0x5f8),
           slot +0x214 = 0x100f4f80  freq  per point (u16)  ┘ CCADModule vtable 0x10114964 (RTTI .?AVCCADModule@@)
                │  points in mm, one per interpolation cycle
MainApp    0x437500  XY tick builder: Δmm → pulses (§3), duty/freq copied in, records appended (0x442010)
           0x4409a0 / 0x443270 / 0x442xxx  contour sequencer + IO/ZF record builders (types 1,3,4,5,0xe,0x11,0x13 …)
           0x5904a0  Start handler → CNCModule slot 26 (+0x68) 0x100349b0(bool,bool,&vector<Record>,0,0,1)
                │  vector<Record28> (also dumped to \_tempManuItem.olpf: "%d %d %d %d %d %d %d %d %d\n")
NCModule   CNCModule 0x100349b0 → VM slot 17 (+0x44) 0x10047650: push_back into VM+0x628 (vector<vector<Record>>),
                     VM+0x758 += n (pending records, compared with 0x1f40 = 8000 @0x10047944)
           mcCoreProcess pump 0x10057d96–0x10057f1c: takes records contour by contour into a local vector, then
           VM slot 121 (+0x1e4) 0x100437d0  = item builder: Record → TLV words, closes frames, queues them in the
                     ring VM+0x11e4..0x11f4 (frames are 4-word-prefixed word vectors, §7.1)
           VM slot 122 (+0x1e8) 0x10052390  = fillFifo: patches count/frame-id, throttles on reg 1016, writes reg 0x66
           VM slot 123 (+0x1ec) 0x1003e5a0  = emit2001 helper; 0x1003ed50 = emit118; 0x1003ede0 = per-laser-type tail
```
Evidence: VM vtable slots read from `0x1008bbc4` (`+0x44→0x10047650, +0x1e4→0x100437d0, +0x1e8→0x10052390, +0x1ec→0x1003e5a0, +0xb0→0x1003c760, +0xb8→0x1003c860`); CAD vtable slots read from `0x10114964`; `0x5904a0:0x590633` `call [edx+0x68]` with `[ebp-0x44]+0x1d8` = CNCModule; the olpf dump loop `0x10034afb–0x10034b37` (fprintf arg order = `+0x18, +0x16, +0x14, +0x12, +0x10, +0, +4, +8, +0xc`).

---

## 2. The 28-byte job record (EVIDENCE)

Constructor `MainApp 0x425e00` (zeroes 4 dwords at +0..+0xc, u16 +0x10, u8 +0x12, u16 +0x14, u8 +0x16 = 0, dword +0x18); element stride 28 (`imul …,0x1c` in MainApp, `0x92492493` magic in NCModule `0x100437d0`, `0x10057dd6`, `0x10034ac6`).

| off | type | meaning | written by |
|---|---|---|---|
| +0x00 | i32 | axis slot 0 increment (X) in pulses — or sub-code for IO records | `0x437500` via `0x450130`; IO builders `0x4420f0…` |
| +0x04 | i32 | axis slot 1 increment (Y) — or **port index (XML port − 1)** / DA channel for IO records | idem; `0x442440`, `0x4426ce` (`sub …,1` then store) |
| +0x08 | i32 | axis slot 2 increment (unused on this 2-axis machine) | — |
| +0x0c | i32 | axis slot 3 increment; also the fibre analogue power value (§4.3) | `0x437960` writes a K-unit delta here for single-axis profile moves |
| +0x10 | u16 | spare (dumped, never packed) | — |
| +0x12 | u8 | PWM duty % for this tick | `0x437830` from ctx+0x34 (CAD slot 0x210 result) |
| +0x14 | u16 | PWM frequency Hz | `0x43784c` from ctx+0x38 (CAD slot 0x214 result) |
| +0x16 | u8 | **record type** (0 tick, 1 IO, 3, 4, 5, 6, 7, 8, 9, 0xa, 0xb, 0xe, 0x11, 0x12, 0x13; 0xc/0xd/0xf/0x10 skipped) | `mov BYTE PTR [..+0x16], n` sites `0x442415…0x442d3b`, template `0x9e5034` (type 0xe) at `0x5905ff` |
| +0x18 | i32 | value / flags (3002 mode, DO value, DA value, ZF flags: bits 4–7 and 8–11 sub-selectors, bit 4/5 flags) | IO builders (`0x4425af`, `0x44285b`) |

The `Record` list is per contour; `VM+0x1174` remembers the type of the previous record (used for the 3001 rule, §4.1).

---

## 3. (a) The tick quantiser — pulses, truncation with carry, 250 µs (EVIDENCE)

**Points → pulses, MainApp `0x437500`** (args: record template, segment index):
1. `0x437646`: `call [CAD+0x21c]` with `(&out, speed doubles…, idx)` → vector of `{x, y}` doubles (16-byte stride, read at `0x43770b`/`0x437720` as `[eax]`, `[eax+8]`). Returns early if fewer than 2 points (`0x437653`).
2. `0x4376a2–0x4376d6`: per-axis factor `f = 0x4378d0(slot)` for slot `g+0xba5c` (X) and `g+0xba60` (Y):
   `0x4378d0` = `fild int[g+0xbb38+slot·0xa0] ; fdiv dbl[g+0xbb30+slot·0xa0] ; fdiv 10000.0` (`0x7d0ed0`).
3. `0x43770d`, `0x437780`: `Xs = X_mm · 10000.0` (0.1 µm intermediate), same for Y.
4. `0x4377a9–0x4377c7`: `d = (Xs_i − Xs_{i−1}) · f` → `0x450130(&rec[i−1].cell0, d, axis 0)`; `0x4377d7–0x437801`: Y into `cell1` (`+0x4`), axis 1.
5. `0x450130` (EVIDENCE): `t = d + carry[axis]` (`fadd [eax*8+0x9495b0]`); `*cell = _ftol(t)` (`0x606c10`, truncation toward zero); `carry[axis] = t − *cell`. The carry array `0x9495b0[axis]` is never reset by code (only static-initialised), so the residual (|carry| < 1 pulse) persists across contours and jobs.
6. `0x437830`/`0x43784c`: `rec.duty = u8 ctx+0x34`, `rec.freq = u16 ctx+0x38`; `0x437866`: append to the contour list (`0x442010`).

**What the per-axis fields are** (EVIDENCE): NCModule VM slot 130 (`0x10039ae0`, the per-axis RW-block copy, cf. A1 §2) stores `dbl[g+0xbb30+slot·0xa0] = word9 / K` (`fild [ebx+0x20]; fidiv [eax+0xbab0]; fstp …+0xbb30` @`0x10039ed5–0x10039ee1`) and `int[g+0xbb38+slot·0xa0] = word10` (`mov eax,[ebx+0x24]; … mov […+0xbb38],eax` @`0x10039ef2–0x10039ef8`), where words are the card's per-axis RW block (register 50200 + 20·slot): `lang.txt` `AxisRWRegName_10 = 导程 "Lead"`, `AxisRWRegName_11 = 当量指令脉冲数 "equivalent command pulse number"` (0-based words 9 and 10). With `MAC.SpeedRatio = 31.003` (lead, mm) and `MAC.WritePluse = 8000` (01 §, 05 §656):

    Δpulses = Δmm · 10000 · (8000 / 31.003 / 10000) = Δmm · 258.04      (X);  Y: 8000 / 31.009 = 258.00 p/mm

**[verifier] correction (V1):** the copy loop in `0x10039ae0` advances `edi += 0x140` (2 slots), `ebx += 0x70` (2 × 14-word axis records), `[ebp-4] += 2`, until `edi > 0x320` (`0x10039f85–0x10039f9d`), i.e. it refreshes **slots 0, 2, 4 only**. Slot 1 (`g+0xbbd0 / g+0xbbd8`, Y) is written only by the XML descriptors `MAC_1.SpeedRatio` / `MAC_1.WritePluse` (`MainApp 0x790e17 / 0x790b2c`). So X uses card axis-0 lead/pulses (after a refresh) and Y uses the XML values (`BkHardPara.xml` 31.009 / 8000; the Wine prefix `HardPara.xml` has 30.991 / 8000 and X 31.02). The 258.00 p/mm figure for Y is therefore from XML, not from the card.

INFERENCE (high): the FIFO therefore streams **motor pulses** per interpolation cycle; K (units/mm = 1000) is used only for the 0x65 command registers (A1) — two different unit systems. The int16 packer (§4.2) limits a tick to ±32767 pulses (127 mm/tick at 258 p/mm, never reached).

**Tick period assumed by the PC** (EVIDENCE): `0x4373c0` (PWM ramp records) computes `N = (1000 / [g+0x4890]) · ms` (`mov eax,0x3e8; idiv [ecx+0x4890]; imul eax,[ebp+0xc]` @`0x4373ec–0x4373f8`), and the sequencer `0x4409a0` uses `fild [g+0x4890]` / `idiv [g+0x4890]` the same way (`0x44188e`, `0x441cca`). `g+0x4890` is the descriptor of **`AX.InterpolationCycle` (pd238)** (A1 §6 table; `BkHardPara.xml` = 250). So a 1-s pierce is 4000 stationary ticks and the CADModule sampler (05 §) produces one point per 250 µs. Whether firmware V201.52 executes one item per 250 µs (and not per 1 ms) cannot be proven from files; §10 gives the one-capture check. The 22 leaked frames give |Δ| = 1…8.5 pulses/tick, mean 2.43 (§8): at 250 µs that is **15.5–132 mm/s** (**[verifier]** corrected from "9–33 mm/s": 1 p/tick = 3.88 µm/0.25 ms = 15.5 mm/s; 8.54 p/tick = 132 mm/s) (the leaked cut frames were not at 50 mm/s, or the period differs) — consistent with either reading, hence the live check.

**Duty/frequency per tick** (EVIDENCE): CADModule `0x100f4e90` (duty, u8) and `0x100f4f80` (freq, u16) evaluate, for every point's speed `v[i]`, a piecewise-linear table (`[edi+0x14]` entries of 32 bytes `{x0,·,y0,slope}` via `0x100f3f70`) — `y0 + (v−x0)·slope + 0.5` then `fistp` with the control word forced to **truncate (0xc00)** = round-half-up (`0x100f4f1b–0x100f4f3f`, `0x100f500d–0x100f5031`); when `v == nominal ([edi+8])` the constant `[edi+0x11]` / `[edi+0x26]` is used. These are the layer's power/frequency-vs-speed curves (02 §).

---

## 4. The NCModule item builder — VM slot 121 `0x100437d0` (EVIDENCE)

Input: `vector<Record>` (28-byte, count = bytes ÷ 28 via `0x92492493`). Output: one or more frame vectors queued in the ring `VM+0x11e4` (critical section `0x1008426c`; storage `+0x11e8`, capacity `+0x11ec`, head `+0x11f0`, count `+0x11f4`).

### 4.1 Frame prefix, 3001 rule, flush rule
* A new frame vector starts with **4 words** `[0x40, 0x66, 0, 0]` (`mov [ecx],0x40` @`0x10043947`, `0x66` @`0x1004395f`/`0x10043a38`, zeros @`0x10043b18`/`0x10043bec`) = func, register, count placeholder, frame-id placeholder.
* **3001 auto-marker** (`0x10043c03–0x10043c2a`): before processing a record whose type `t` satisfies `1 ≤ t < 0x14 && t != 0xe`, if the previous record's type (`VM+0x1174`) was 0 (a tick), push `0x00000bb9`. Record type **0xe pushes 3001 explicitly** (`0x10044fb4`). Hence the "3001 ; 3002[5] ; tick ; 3001 ; 9999…" shape in the leaked prologue.
* **Flush** (`0x10045389–0x100453b3`): after every record, `if (bytes_in_vector >= 0x4b0 /*300 words*/ || last record)`: `vec[2] = words − 3` (count = frame id + data words), copy the vector into a ring slot (`0x100317a0`), `count++`. With 3-word ticks the vector reaches 301 words after 99 ticks ⇒ `count = 298 = 0x12a`, **297 data words** — exactly the logged frames; a frame with 0-payload 3001 items can hold 100 items in the same 297 words (frames 504/633/57). Data words per frame are therefore 296…304 (largest item = 104 with 8 words + header), never a fixed 99 items. `ipAdd.ini MaxItemPerFrame=60` is not referenced here (closes 08 §9.12).

### 4.2 Record type 0 → opcode 3000 (the tick) `0x1004517b–0x10045389`
```
mask = 0
for i in 0..[g+0xba58]-1:                       # configured axis count (2 here)
    d = i32 rec[4*i]; s = VM+0x115c[4*i]        # s = sign of the last non-zero increment on axis i
    if sign(d)*s < 0: s = -s                    # (0x100451a1-0x100451d1)
    if s == -1: mask |= 1<<i
    VM+0x115c[4*i] = s
push 0x00080bb8                                 # header: 8 payload bytes, opcode 3000   (0x100451ec)
x = |movsx i16 rec[+0]|; if mask&1: x = -x      # 0x10045200-0x10045213
y = |movsx i16 rec[+4]|; if mask&2: y = -y      # 0x10045216-0x10045227
push (u16(y) << 16) | u16(x)                    # 0x1004522a-0x1004523f   -> X = LOW half, Y = HIGH half
```
Net effect: the packed value equals the record's int16 increment (sign restored from the direction cell; a zero increment stays 0). Then the PWM word (`0x10045356–0x10045384`, the CO2 / plain path): `duty = u8 rec[+0x12]; freq = u16 rec[+0x14]; if freq < 1: freq = 5000 (0x1388)`; `push (freq << 16) | duty`.

### 4.3 The CO2-analogue variant of the PWM word (`0x10045244–0x10045354`, EVIDENCE for the arithmetic, names INFERENCE) — **[verifier]** not "fibre": `SP.m_iEnableLaserType` 0 = fibre, 1 = CO2, 2 = blue (01 §863)
Taken when `g+0x46d8 (SP.m_iEnableLaserType) == 1` **and** `g+0x4940 (CO2LaserControlType) == 3` (DA/analogue): `pwr14 = ((u16(k) · rec[+0xc] · 1000) / 100) & 0x3fff` (**[verifier]** `and ecx,0x3fff` @`0x100452f0` — a 14-bit *wrap*, not a clamp) with `k = 10 if g+0x4948==0, 5 if ==1, else g+0x4940+1`; `w2 = (n << 16) | (mode << 14) | pwr14` where `mode = clamp(g+0x4944 − 1, 0, 1)` and `n = clamp(g+0x88c, 1, 10)` placed as a **u8** in bits 16–23, two's-complement-negated (`neg al; movzx eax,al` → 0xff…0xf6) when `rec[+0x12] == 0`. Not exercised on this machine (`CO2LaserControlType = 2`). **[verifier] Missed branch:** when `LaserType != 0` and `CO2LaserControlType ∉ {1,2}` and not (`LaserType == 1 && ctl == 3`) the code jumps straight to the flush (`0x1004526d–0x10045279 → 0x10045389`) — the 3000 item then carries header `0x80bb8` (8 payload bytes) but **only one payload word**, i.e. a malformed stream. A port must refuse that configuration rather than replicate it.

### 4.4 Record type → items (the sequencer table)
`t = rec[+0x16]`; `sub = rec[+0]`; `p = rec[+4]`; `v = rec[+0x18]`. "ZF on" = `g+0x490c (ZF.ZFType) != 0` (= 1 here, so the ZF items **are** emitted for CO2 jobs).

| t | items emitted | code |
|---|---|---|
| 0 | `3000` tick (§4.2) | `0x1004517b` |
| 1 | jump on `sub` (table `0x100454c8`): **1,2** → `9999[3 or 0x11, u16 rec+0x14, u8 rec+0x12]` (0x11 when LaserType==1 && CO2LaserControlType==2 — this machine) = **set PWM freq/duty**; **3** → `9999[4, p, v]` = **DA**; **4** → `9999[2, 1<<p, v<<p]` for 0 ≤ p < 10, `9999[13, 1<<(p−10), v<<(p−10)]` for 10 ≤ p < 26 = **DO** (p = XML port − 1); **5** → if v > 0: `2001[v, 3000]` (mode-0 form: word1 = v = a time, word2 = 3000) — INFERENCE: "hold/wait v ms"; **6** → `118[4, (v != 0 && LaserType == 0) ? v : 0, int(g+0x4d28)]` (**[verifier]** polarity corrected, see §5 row 118) | `0x10043c85…0x10043ee0` |
| 3 | `3002[v]` (v = 5 before laser-on, 4 after, set by MainApp) | `0x10043ee5` |
| 4 | ZF on: `118[4,…,int(g+0x4d28)]`, then fibre laser-on sequence: `105[(flags<<16)\|int(ratio·1000), int(g+0x49c8·10), 0, int(g+0x49d0·10), int(z·1000)]` (header `0x100069`, "ZF follow to cut height") or with `(v>>4)&0xf == 0xe` the 105/109 variants; then `2001[0x03000004, VM+0x85c]`; then `0x1003ede0` tail | `0x10043f16…0x100442e3` |
| 5 | ZF on: sub-selector `(v>>4)&0xf`: 0xf → `109[int(g+0x49d0·10), int(z·−1000)]`+…; 0xe → `108[...]` (header `0x10006c`, 4 words) etc.; then `2001[0x03000002, VM+0x85c+…]` and tail | `0x100442e8…0x100447ee` |
| 6 | if sub == 3: `2001[v \| 0x03000000, VM+0x858]` | `0x100447f3` |
| 7 | ZF on: `118[…]`, sub-device setter `0x1003f890` (**[verifier]** gated on `g+0x4a20`, not on laser type), `104[...]` (header `0x200068`, 8 words: ZF calibration/section block) or `104` short form (`0x80068`, 2 words), `2001[0x03000003, VM+0x858]`, tail | `0x10044826…0x10044ae4` |
| 8 | ZF on: if `v & 0x10` and not ZF-type 5: `106[(flags<<16)\|u16 rec+0x14, rec+4·K?, …]` (header `0x18006a`, 6 words); else **`109[int(g+0x49c8·10), int(z·−1000)]`, `2001[0x03000002, VM+0x858]`, `118[4, 0, int(g+0x4d28)]`** ← the leaked **epilogue** | `0x10044ae9…0x10044cbe` |
| 9 | ZF on: `109[int(g+0x49d0·10), int(g+0x49e8·−1000)]`, `2001[0x03000002, VM+0x858]` (ZF dock: ZFUpSpeed, ZFDockHeight) | `0x10044cc3` |
| 0xa | ZF on: power/sub-device setter, `104[int(g+0x49c8·10), 1000]`, `2001[0x03000003, VM+0x858]`, `109[int(g+0x49c8·10), int((−z−1)·1000)]`… | `0x10044d81` |
| 0xb | ZF on: analogue power setter `0x1003ef40` only (**[verifier]** gated on ZF+0x3c, `g+0x47c7==0`, and `LaserType==1 && CO2LaserControlType==3` = CO2-analogue — not fibre) | `0x10044d54` |
| 0xe | `3001` | `0x10044fad` |
| 0x11 | ZF on: `0x1003f890` / `0x1003ef40` setters (emit nothing on this machine), **`103[int(g+0x49c8·10), int(ZF+0x58 · 1000)]`, `2001[0x03000002, VM+0x858]`**, tail ← the leaked **prologue** (103 = "ZF: go to follow/cut height at speed g+0x49c8"; ZF+0x58 = 0 for the CO2 layer) | `0x10044eba…0x10044fa8` |
| 0x12 | fibre power/ZF selector by `[ZF+0x38]` (jump table `0x100454e0`) | `0x10044fc1` |
| 0x13 | ZF on: `2001[0x03000002, VM+0x85c]`, tail | `0x1004513a` |
| 0xc, 0xd, 0xf, 0x10 | nothing | `0x10043c4a–0x10043c64` |

`0x1003ede0(vec, arg)` ("tail", called after most 2001s) dispatches on `g+0x4a20` (sub-device type, jump table `0x1003ef18`): 1 → `2004[1, 0x3f2, 4, arg, 0x80000002]` (header `0x1407d4`, 5 words); 2,3 → nothing (**this machine: nothing is added**, matching the frames); 4 → `2001[N-1 form, 3000]` or `2001[1000, 3000]`; 5 → `2001[0x02001000, VM+0x858+arg]`; 6 → `2001[(g+0x4a30\|0x20000)<<8, VM+0x858+arg]`.

Parameter names used above (A1 §6 / 01): `g+0x49c8`, `g+0x49d0 = ZF.ZFUpSpeed (pd657)`, `g+0x49e8 = ZF.ZFDockHeight (pd631)`, `g+0x490c = ZF.ZFType`, `g+0x46d8 = SP.m_iEnableLaserType`, `g+0x4940 = CO2LaserControlType (pd259)`, `g+0x88c = LGP.CO2DOLaser`, `g+0x4944 = LGP.CO2LaserDAPort`; `g+0x4d28` (the "35" in `118[4,0,35]`) has no name in the docs yet (INFERENCE: a ZF height/ratio parameter, read as `int(double)`). `VM+0x858 / +0x85c` = `ipAdd.ini [Soft] FollowOvertime / SectionDrillOvertime` (read with default 3000 at `0x10058aec`/`0x10058b10`; `ipAdd.ini` lines 131–132 = **20000**) — the 2001 second word in the leaked frames.

---

## 5. Opcode table (all emitters found; header = `(payload_bytes << 16) | opcode`)

| opcode | header(s) | payload words | meaning | evidence (emitter) | confidence |
|---|---|---|---|---|---|
| **3000** | `0x00080bb8` | `[(dY<<16)\|dX, (freq<<16)\|duty]` | one interpolation tick: pulses on axis slots 0/1, PWM for the tick | `0x100451ec` | EVIDENCE |
| **3001** | `0x00000bb9` | — | boundary marker, pushed automatically when a control record follows a tick, or by record 0xe (contour start/end) | `0x10043c23`, `0x10044fb4` | EVIDENCE (rule); role on the card INFERENCE (sync/segment boundary) |
| **3002** | `0x00040bba` | `[mode]` | record-3 value; 5 before laser-on, 4 after laser-off in the leaked job | `0x10043ef0` | EVIDENCE (value pass-through); meaning INFERENCE (low: processing-state flag shown in reg 1019 "processing status"?) |
| **9999** sub 2 | `0x000c270f` | `[2, mask, value]` | set digital outputs 1..10: `mask = 1<<(port−1)`, value bits likewise | in-stream `0x10043dc6`; 0x65 path `0x1003c8d2` | EVIDENCE |
| 9999 sub 13 | `0x000c270f` | `[13, mask, value]` | DO ports 11..26 (extended outputs) | `0x10043e36`; `0x1003c996` | EVIDENCE (encoding) |
| 9999 sub 4 | `0x000c270f` | `[4, channel, value]` | set DA (analogue) output; 0x65 path: channel−1 ∈ {0,1}, value = int(V·1000) clamped 50..10000 | `0x10043d77`; `0x1003c760` | EVIDENCE (0x65 path) / LIKELY (in-stream uses the same units) |
| 9999 sub 3 / 0x11 | `0x000c270f` | `[3\|17, freq, duty]` | set PWM frequency/duty without motion (power ramp records from `0x4373c0`, N = ms·1000/cycle) | `0x10043c85`, `0x10043cfe` | EVIDENCE (fields) / INFERENCE (0x11 = "5 V PWM" variant of 3) |
| **103** | `0x00080067` | `[speed·10, pos·1000]` | ZF (height-follower Z) move: speed `g+0x49c8` (0.1 mm/s units), target `ZF+0x58` in µm — the prologue's `103[1000,0]` | `0x10044f25` (record 0x11) | EVIDENCE (fields) / INFERENCE (high) "Z/ZF move" |
| **109** | `0x0008006d` | `[speed·10, pos·1000]` | ZF move with the second word negated (`·−1000`): lift/dock — the epilogue's `109[1000,0]` | `0x10044c30`, `0x10044cde`, `0x10044256`, `0x10044768`, `0x10044332`, `0x10044e54` | idem |
| 104 | `0x00080068` / `0x00200068` | 2 / 8 words | ZF: `[speed·10, 1000]` short form; long form = ZF section/calibration block (`[speed·10, K·…, 1, …, ·1000, ·1000, 10·idx]`) | `0x10044a7b`, `0x10044dec`, `0x1004498f` | EVIDENCE (fields) / INFERENCE (ZF) |
| 105 | `0x00100069` | 4 words | ZF follow-to-height with flags `(sub<<8\|mode)<<16 \| ratio·1000` | `0x1004401c`, `0x100441c9` | idem |
| 106 | `0x0018006a` | 6 words | ZF section-drill (`[flags\|freq, dz·K, …, 0, u16, …]`) | `0x10044ba0` | idem |
| 108 | `0x0010006c` | 4 words | ZF gradual-drill (`[t·10, dz·1000, z·1000, 1]`) | `0x100444f1`, `0x100446f8` | idem |
| **118** | `0x000c0076` | `[4, flag, int(g+0x4d28)]` | ZF bookkeeping (**[verifier] corrected:** flag = arg only if `arg != 0 && LaserType == 0` (fibre), else 0 — `0x1003ed5a–0x1003ed6d`; in record 8 the arg is `int(g+0x4d30)` @`0x10044ca7`) — `[4,0,35]` in the epilogue; emitted only when ZF is enabled and `g+0x4d24` byte set | `0x1003ed50` | EVIDENCE (fields) / INFERENCE |
| **2001** | `0x000807d1` | `[w, t]` | `w` per mode (§4, `0x1003e5a0`): mode 0 `w = p`; mode 1 `((q\|0x10000)<<8)\|r`; mode 2 `((q\|0x20000)<<8)\|r`; **mode 3 `r \| 0x03000000`**; `t` = `FollowOvertime`/`SectionDrillOvertime` (ms) or 3000 | `0x1003e5f4` | EVIDENCE (encoding) / INFERENCE (medium): "wait for condition r (2 = follow height reached, 3/4 = other ZF states) with timeout t ms" — a **no-op-with-timeout for CO2** since the CO2 job carries no height |
| 2002 | `0x000007d2`… | — | fibre-only (`0x1003e63b`, VM slot +0x1f0) | `0x1003e630` | EVIDENCE (exists) |
| 2004 | `0x001407d4` / `0x001807d4` | 5 / 6 words | sub-device (`g+0x4a20`==1) / fibre power block `[0, 101, 12, 3, …]` | `0x1003eeb2`, `0x1003faa7` | EVIDENCE (exists) |
| 3 | `0x00140003` | 5 words | fibre-only ZF/laser record in `0x1003f890` | `0x1003fb91` | EVIDENCE (exists) |

Not opcodes: the `push 0x67` sites (`0x1002be7e/9e`, `0x1002f522`, `0x1002fd71`, `0x10030e27`, `0x10057244`) write **register 0x67** (`←2` start `0x1002be70`, `←1` clear `0x1002be90`, `←3` stop in `0x10050dc0`); `cmp edx,0xbb8` at `0x10022683` is readReg's (3000,5000) address-window check; the three `push 0xbb8` at `0x10058aec…` are the INI defaults (3000 ms) of `FollowOvertime` etc.

---

## 6. (d) DO / DA encoding

* **0x65 path, VM slot +0xb8 `0x1003c860(port, value)`** (EVIDENCE): `if 1 ≤ port ≤ 10: write 0x65 ← [9999, 2, 1<<(port−1), value<<(port−1)]` (`lea ecx,[esi-1]; shl edx,cl` @`0x1003c8d2–0x1003c8f2`); `port > 10` → `[9999, 13, 1<<(port−11), …]` (`0x1003c996`); `port ≤ 0` → nothing. So the logged `0x65 ← [9999,2,1,0]` is **XML port 1 = `DO.AlarmSignal`** (01 §2.8) switched off, and the in-stream `[2,4,4]` / `[2,0x100,0x100]` are **DO3 = `MGP.HighAir`** and **DO9 = `LGP.CO2DOLaser`** — the MainApp IO builders store `XML − 1` (`0x4426c2–0x4426ce`: `mov eax,[g+0x88c]; sub eax,1; mov [rec+4],eax`, same at `0x44243a`, `0x4424a2`, `0x44263c`, `0x442732`, `0x4427b3`) and NCModule shifts by that index. Confirms 00 §7.4 item 2.
* **DA, VM slot +0xb0 `0x1003c760(channel, volts)`** (EVIDENCE): `channel−1 ∈ {0,1}` else refused; `v = int(volts·1000)`; `if 1 ≤ v ≤ 49: v = 50; if v > 10000: v = 10000` (0 stays 0); writes `0x65 ← [9999, 4, …]`. **[verifier]** the stored channel word is `channel−1` (0-based, `mov [ebp+0x10],esi` after `dec esi` @`0x1003c78a/0x1003c7ff`); negative volts are *not* clamped (`lea ecx,[eax-1]; cmp ecx,0x30; ja` is unsigned, then `cmp eax,0x2710; jle` is signed). In-stream DA = record type 1 sub 3 → `9999[4, rec+4, rec+0x18]`. **[verifier] refuted citation:** `0x4427e0` is the *dwell* builder and `0x44281a` is a loop-counter decrement, not a DA port store; the MainApp DA record builder was **not located** — channel base / units of the in-stream DA remain UNVERIFIED (irrelevant for `CO2LaserControlType = 2`).

---

## 7. (e) fillFifo `0x10052390` = VM slot 122 (EVIDENCE)

```
space   = VM+0xd0            # reg 1016 "FIFO space margin" (A2: block 1000 lands at VM+0x90+4i; i=16)
frameId = VM+0xcc + 1        # reg 1015 "FIFO frame id" (last id the card accepted) + 1
n = 0
while ring.count > 0 and n < 0x32 (50):                       # 0x100523db-0x100523f4
    f = copy(ring.front)                                      # 0x1003c0c0 / 0x100317a0
    if f.bytes + 0x7d0 (2000) > space: break                  # 0x10052447-0x10052457  "space margin"
    ring.pop()                                                # 0x1005249b-0x100524c5
    f[3] = frameId                                            # 0x100524d7   (4th word = frame id)
    HAL slot +0x1c (retry/timeout config: VM+0x1128, 1, 1, VM+0x81c), then send f via HAL slot +0x10 [verifier]; on failure log
        "first/second/Three fillFifo error: %d" (0x1008b994/0x1008b830/0x1008b864) and retry paths,
        "Update MC Status" + re-read (0x1008b970), "FillFifo Step1/2/3 Time: %d" when a step > 100 ms
    frameId += 1; n += 1; space -= f.bytes                    # 0x10052aa8-0x10052abb
    VM slot +0x144 (0x1004e120)                               # per-frame status hook
```
* **Frame-id semantics (closes O1/08 §9.11):** the PC keeps no counter; `VM+0xcc` is overwritten by every status poll of block 1000 (A2 §1: `VM+0x90+4i`), and the pump zeroes it only in the VM reset `0x10056170` (`0x100566cd`). The id therefore restarts whenever the **card's** FIFO frame id restarts — i.e. after `clearFifo` (`0x67 ← 1`, `0x1002be90`) which every job start issues (00 §4) — explaining the "57 after 87 without NC Start" observation: the same application session, a new job. ~~A re-sent identical id (08 §4.4) is simply the next `fillFifo` call re-reading an unchanged reg 1015 after a lost datagram.~~ **[verifier] REFUTED:** the repeated frames in the log (ids 60 and 654, 5× each) are transport retries of one datagram — every copy is logged as `MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):1/1 … 3/2`, 500 ms apart (`Log/2025-07-17.log` lines 11–15, 81–85). In addition `fillFifo`'s own retry path (`first/second/Three fillFifo error`, `0x10052629–0x10052963`) re-sends the **same vector with the same frame id** (the id is not recomputed after the `Update MC Status` re-read), and the frame has already been **popped from the ring before the first send** (`0x1005245d–0x100524c5`), so a frame that fails all retries is dropped. That reg 1015 means "last id *accepted*" (not "last id executed") is INFERENCE (high): it is the only reading under which ids from successive `fillFifo` calls cannot collide.
* **Space margin units:** `space` is compared with `bytes + 2000`, so reg 1016 is treated as **bytes free**; `0x10057f2d` tests `space == 0xea60 (60000)` = "FIFO empty" and, with `VM+0xdc == 1` (FIFO running), calls `stopFifo` `0x10050dc0` (`0x67 ← 3`) — job end. 60000 bytes ÷ 12 bytes = 5000 ticks = 1.25 s at 250 µs (5 s at 1 ms), against `MCFifoTime = 1600 ms` look-ahead (04).
* **Batching:** one call sends up to 50 frames; the pump (`0x10057d96…`) hands the builder a per-call slice of records (limit `[ebp-0xbc]` contours; pending counter `VM+0x758` vs 8000 @`0x10047944`), so bursts of 8–15 frames/s (08) are the refill cadence, not the tick rate.
* The log line `Update MC Status Success: CalcBufferSize:%d, RealItemNum:%d` (`0x1008b8f8`) prints `space` and the pushed count.

---

## 8. Cross-check against the 22 leaked frames (EVIDENCE)

Decoding every logged `DataEx: 40 66 12a <id> …` with the grammar (`header = (bytes<<16)|opcode`, item lengths from the header) consumes exactly 297 words in all 22 frames; **re-encoding the decoded items reproduces every frame word-for-word (and the LE payload byte-for-byte)** — `fifo_items.py` below, run against the logs: `frames re-encoded identically: 22 / 22`. Opcode census: 3000 ×2160, 3001 ×6, 9999 ×5, 3002 ×3, 2001 ×3, 103 ×2, 109 ×1, 118 ×1. Non-zero tick magnitude: 1.00 … 8.54 pulses, mean 2.43.

Frame 504 (13:32:19, 100 items) decoded: 47 rapid ticks `dX=+1/+2, dY=0, 5000 Hz 0 %` decelerating to 0 (X only — a pure-X rapid), 16 × `(0,0)`, then `3001 ; 3002[5] ; tick(0,0) ; 3001 ; 9999 DO3 on ; 103[1000,0] ; 2001[0x03000002, 20000] ; 9999 DO9 on ; 13 × tick(0,0, 5000 Hz 4 %)`. Frame 633: `38 × tick(0,0,4 %) ; (−1,0) ; 16 × (0,0,4 %) ; 3001 ; 9999 DO9 off ; 109[1000,0] ; 2001[0x03000002,20000] ; 118[4,0,35] ; 3001 ; 3002[4] ; 21 × (0,0,0 %) ; +1,0 …` = record sequence `0xe?/3(5) … 1(DO) 0x11 1(DO) 0… | 0 … 1(DO) 8 0xe 3(4) 0 …` per §4.4 — the CO2 job on this ZF-enabled machine emits the ZF prologue/epilogue with zero heights. `103[1000,0]` = `int(g+0x49c8·10)` ⇒ `g+0x49c8 = 100.0` (a ZF speed, mm/s); `2001` word 2 = `ipAdd.ini FollowOvertime = 20000`.

Reconciliation of the O1 numbers: 2.43 pulses/tick ≈ 9.4 µm/tick; at 250 µs that is 38 mm/s (rapid frames 8.5 p/tick = 132 mm/s). The leaked cutting frames (1191/1199) are from a job whose layer speed is not recorded, so the period cannot be pinned from the logs; the PC-side period is 250 µs (§3).

---

## 9. Python — decoder / encoder that reproduces a leaked frame byte-identically

```python
import struct

def s16(v): return v - 0x10000 if v & 0x8000 else v

def decode_items(words):                       # words = the 297 data words after the frame id
    i, items = 0, []
    while i < len(words):
        h = words[i]; op, nbytes = h & 0xffff, h >> 16
        assert nbytes % 4 == 0
        n = nbytes // 4
        items.append((op, words[i+1:i+1+n])); i += 1 + n
    assert i == len(words)                     # zero remainder on all 22 leaked frames
    return items

def encode_item(op, args):                     # header = (payload_bytes << 16) | opcode
    return [((4 * len(args)) << 16) | op] + list(args)

def tick(dx, dy, freq, duty):                  # opcode 3000: X in the low half, Y in the high half
    return encode_item(3000, [((dy & 0xffff) << 16) | (dx & 0xffff), (freq << 16) | duty])

def do_item(xml_port, on):                     # opcode 9999 sub 2 (ports 1..10) / sub 13 (11..26)
    p = xml_port - 1
    return encode_item(9999, [2, 1 << p, int(on) << p]) if p < 10 else encode_item(9999, [13, 1 << (p-10), int(on) << (p-10)])

def frame_words(frame_id, items):              # exactly what the log prints after "DataEx:"
    data = [w for op, a in items for w in encode_item(op, a)]
    return [0x40, 0x66, 1 + len(data), frame_id] + data

def payload_bytes(frame_id, items):            # LE payload after [func][addr][count] (04 §3.2)
    data = [w for op, a in items for w in encode_item(op, a)]
    return struct.pack('<%dI' % (1 + len(data)), frame_id, *data)

# Frame 633 (Log/2025-07-17.log 13:38:49.128), rebuilt from the decoded sequence:
items = ([tick(0, 0, 5000, 4)] * 38 + [tick(-1, 0, 5000, 4)] + [tick(0, 0, 5000, 4)] * 16
         + [encode_item(3001, []), do_item(9, 0), encode_item(109, [1000, 0]),
            encode_item(2001, [0x03000002, 20000]), encode_item(118, [4, 0, 35]),
            encode_item(3001, []), encode_item(3002, [4])]
         + [tick(0, 0, 5000, 0)] * 21 + [tick(1, 0, 5000, 0)] + [tick(0, 0, 5000, 0)] * 5
         + [tick(1, 0, 5000, 0)] + [tick(0, 0, 5000, 0)] * 3 + [tick(1, 0, 5000, 0)]
         + [tick(0, 0, 5000, 0)] * 2 + [tick(1, 0, 5000, 0)] + [tick(0, 0, 5000, 0)] * 2 + [tick(1, 0, 5000, 0)])
items = [(w[0] & 0xffff, w[1:]) for w in items]
logged = [int(x, 16) for x in open('/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52/Log/2025-07-17.log', encoding='utf-8', errors='replace')
          .read().split('DataEx:40 66 12a 279 ')[1].split('\n')[0].split()]
assert frame_words(0x279, items) == [0x40, 0x66, 0x12a, 0x279] + logged
assert payload_bytes(0x279, items) == struct.pack('<298I', 0x279, *logged)
```
(Both asserts pass. The general form — parse every `40 66` line in `Log/*.log`, `decode_items`, `frame_words`, compare — was run over all 30 logged frames / 22 distinct ids: 22/22 identical.)

---

## 10. What still needs the live machine, and the exact step

1. **Tick period on the card** (the only part of O1 left): stream one 100 mm pure-X move at a known speed with `AX.InterpolationCycle = 250` and count the 3000 items (expected `100·258.04 = 25 804` pulses total; at 250 µs and 100 mm/s ⇒ 4000 ticks of ≈6.45 pulses; at 1 ms ⇒ 1000 ticks of ≈25.8). Cheaper: read reg **1017 "FIFO interpolation data configuration"** (`RORegName_18`) once — INFERENCE that it advertises the item period/format.
2. **Reg 1016 units** (bytes vs items): compare the value right after `clearFifo` (expected 60000) with the value after sending exactly one frame (PC-side accounting deducts 1204 = the 301-word vector incl. the `0x40,0x66` words; the card might deduct 1192 = frameId+297 words, 1188 = data only, or 99/100 items — expected 58796 / 58808 / 58812 / 59901 — reg 1015 = id in all cases). **[verifier]** A2 §2 reads reg 1016 as "free item slots"; only the PC's byte arithmetic is EVIDENCE.
3. **2001 / 3002 semantics** (harmless to the port as long as they are replayed verbatim): A/B capture with `ZF.ZFType = 0` — INFERENCE says the whole 103/109/118/2001 group disappears and only DO/3002/3001 remain.
4. **Which DA channel base the in-stream `9999[4,…]` uses** — only relevant for `CO2LaserControlType = 3`; not needed for this machine.

---

## 11. Corrections to earlier documents

* 08 §4.4 / 00 §4 / 04 §3.7: `103[1000,0]` and `109[1000,0]` are **not** "dwell 1000 ms" — both are ZF-axis moves `[speed·10, pos·1000]` (speed 100 mm/s, target 0) emitted because `ZF.ZFType = 1`; the pierce dwell is realised purely by stationary 3000 ticks (13 + 38 + … at 4 %), i.e. `ms·4` ticks.
* 08 §9.1 / 04 §10: the 3000 unit is **pulses** (not 0.001 mm and not "units"); X is the low half, Y the high half (04 §10 "(dx,dy) or (dy,dx)" closed).
* 08 §4.4 "frame ids are a counter incremented per frame sent … restart with each job [likely]": the id is the **card's reg 1015 + 1**; restarts follow `clearFifo`.
* 00 §7.4 item 2 confirmed (DO3 HighAir / DO9 CO2DOLaser; `[9999,2,1,x]` on 0x65 = DO1 AlarmSignal).
* 04 §3.7 "fillFifo uses constant 100": the 99-item shape comes from the **300-word (0x4b0-byte) flush threshold** in the item builder, not from an item count.

---

## Verification notes

Adversarial re-derivation (verifier, 2026-09-15). All addresses re-read from `.scratch/asm/{MainApp,NCModule}.asm` (spot-checked against a fresh `objdump --start-address` run on the DLL), constants re-read from the PE sections, and the 30 logged `40 66` frames re-decoded with an independent script (not the report's `fifo_items.py`, which is not in the repo). Legend: **OK** = re-derived as stated; **FIX** = corrected in place above; **DOWN** = confidence lowered; **ADD** = missed.

**Confirmed (OK)**
* Quantiser `0x437500`: `fld [pt]; fmul 10000.0 (0x7d0ed0 = 10000.0)`; `(Xs_i − Xs_{i−1}) · f → 0x450130(&rec[i−1]+0, d, 0)`, Y into `+4` with axis 1; `0x4378d0` = `fild [g+0xbb38+s·0xa0]; fdiv [g+0xbb30+s·0xa0]; fdiv 10000.0`. `0x450130`: `fadd [axis·8+0x9495b0]`, `_ftol2` `0x606c10` = `cvttsd2si` (truncation), residual stored back. Only two references to `0x9495b0` exist (both in `0x450130`); the `.data` initial bytes are zero. Note the closed-claim wording "round-with-carry" is wrong — it is **truncate**-with-carry (the report body says so).
* `0x10039ed5/0x10039ef2` read `[ebx+0x20]/[ebx+0x24]` with `ebx = VM+0x1f0 = VM+0x1ec+4`; the 50200 reader `0x10051d00` stores 5 × 14 words at `VM+0x1ec` (`mov esi,5; mov edx,0xe; add edi,0x50`), so these are 0-based words 9/10 = `AxisRWRegName_10` 导程 Lead / `_11` 当量指令脉冲数 command pulses per unit. OK — but see V1 for which slots.
* Period formula `0x4373ec–0x4373f8` and `0x442802–0x44280e`: `N = (1000 idiv cycle) · ms` — **integer division first**, so a cycle that does not divide 1000 silently truncates (250 → exactly 4 ticks/ms). `0x441cca`: `g+0x4870·1000 idiv cycle`.
* 3000 packer `0x1004517b–0x1004523f`: header `0x80bb8`; X = `movsx word [rec+0]` in the low 16 bits, Y = `movsx word [rec+4]` shifted by 16. The sign cells `VM+0x115c..0x1168` are initialised to **1** in the VM reset (`mov eax,1` @`0x1005652a`, stores @`0x1005659d–0x100565af`), which is what makes "packed = int16(record)" true; with a 0 cell the sign would be lost (`imul` with 0 never flips). PWM word `0x10045356–0x10045384`: `freq<1 → 0x1388`. OK.
* Flush `0x10045389–0x100453b3`: `bytes >= 0x4b0` or last record → `vec[2] = words − 3`; the prefix constant comes from `.rdata 0x1008a670 = 0x40` (@`0x10043870`) and `0x66` @`0x1004395f`. Re-decode of the logs: 22 distinct ids, each 297 data words, 99 items (100 in ids 57, 504, 633), opcode census 3000×2160, 3001×6, 9999×5, 3002×3, 2001×3, 103×2, 109×1, 118×1; non-zero tick magnitude 1.00–8.544, mean 2.434. OK.
* `MaxItemPerFrame`: read at `0x10058770` (`GetPrivateProfileInt("…", "MaxItemPerFrame", 160)` then `shr eax,1`) into `VM+0x748`; the only reference to `+0x748` in NCModule is that store. **Unused — confirmed** (ADD: it is read and halved, then ignored).
* fillFifo `0x100523c0–0x100523f4`, `0x10052447–0x10052457`, `0x100524d7`, `0x10052aa8–0x10052abb`, 50-frame limit, `bytes+2000 ≤ VM+0xd0`: OK. stopFifo trigger `0x10057f21–0x10057f8d` additionally requires `VM+0x870 != 0` (job-active flag).
* DO 0x65 `0x1003c860`: `[9999, 2, 1<<(port−1), u8(value)<<(port−1)]` for 1..10; ports 11..26 → sub 13 **only if `g+0x4d58 != 0`** (ADD, `0x1003c960`; not gated in-stream). In-stream DO `0x10043dc6–0x10043e9b`: `1<<p`, `i32(rec+0x18)<<p`. MainApp stores `XML−1`: `0x4426c2` (`g+0x88c` = `LGP.CO2DOLaser` = 9 → mask 0x100), `0x44263c/0x442732` (`g+0xbc`, = HighAir 3 → mask 4), `0x44243a`, `0x4424a2`, `0x4427b3`. `BkHardPara.xml`: `AlarmSignal=1`, `HighAir=3`, `CO2DOLaser=9` — bits 2/8 and DO1 alarm OK.
* 103 `0x10044f25–0x10044f75`: `int(g+0x49c8 · 10.0)` (`0x10086eb8 = 10.0`), `int(ZF[+0x58] · 1000.0)` (`0x10089c78 = 1000.0`), gate `g+0x490c != 0`; 109 in record 8 `0x10044c30–0x10044c80`: `int(g+0x49c8·10)`, `int(ZF[+0x68] · −1000.0)` (`0x1008a870 = −1000.0`; note **+0x68**, a different ZF field than 103's +0x58). `ZFType="1"` in both XML files. "Not dwells" — confirmed from the packers. "ZF move" remains INFERENCE (high); it could equally be "follow to height at speed" for a capacitive follower.
* emit2001 `0x1003e5a0` (6 stack args, `ret 0x18`): mode 0 `w=p`, 1 `((q|0x10000)<<8)|r`, 2 `((q|0x20000)<<8)|r`, 3 `r|0x03000000`, mode > 3 → nothing; `t` word = arg 6. INI reads `0x10058aec/0x10058b10` (`FollowOvertime`, `SectionDrillOvertime`, default 3000) → `VM+0x858/+0x85c`; `ipAdd.ini` lines 131–132 = 20000 = `0x4e20` in the frames. OK.
* 3001 rule `0x10043c03–0x10043c2a` and explicit type 0xe `0x10044fb4`; 3002 `0x10043ef0`; IO sub-jump table `0x100454c8` = {c85, cfe, d77, dc6, ea5, ed1}. OK. ADD: `VM+0x1174` is set to **−1** on every record push (`or edi,-1` @`0x10047880`, store @`0x100478f4`), so the first control record of a newly pushed batch gets **no** automatic 3001.
* VM vtable re-read: `+0x44 0x10047650, +0x68 0x100497d0, +0xb0 0x1003c760, +0xb8 0x1003c860, +0x1cc 0x100516b0 (block-1000 reader), +0x1e4 0x100437d0, +0x1e8 0x10052390, +0x1ec 0x1003e5a0, +0x1f0 0x1003e630, +0x144 0x1004e120`. MainApp `0x590633` `call [CNCModule vtbl+0x68]` with args `(1,1,&vec,0,0,1)`. OK.

**Refuted / corrected (FIX)**
* **V1 — per-axis factor source (claim 1).** "lead/pulses come from card per-axis regs word 9/10 copied to g+0xbb30/0xbb38" is only true for **even slots**. `0x10039ae0` loops `edi = 0, 0x140, 0x280` (slots 0, 2, 4) over `VM+0x1f0 + 0x70·k` (card axes 0, 2, 4). Slot 1 (Y, `g+0xba60 = 1`) is never refreshed from the card on this build, so Y's pulses/mm = XML `MAC_1.WritePluse / MAC_1.SpeedRatio` (descriptor initialisers `0x790b2c` / `0x790e17`, strings `MAC_1.WritePluse`, `MAC_1.SpeedRatio`). Whether this is a vendor bug or intentional cannot be told statically; a port must reproduce *these* sources, or X and Y scale differently from Mlaser whenever card and XML disagree. The two XML copies disagree already (Bk 31.003/31.009, Wine 31.02/30.991).
* **Speed arithmetic in §3** "9–33 mm/s" → 15.5–132 mm/s at 250 µs (§8 already had 38/132).
* **§4.3 "fibre variant"** → CO2-analogue variant (`LaserType 1 = CO2`, 01 §863); `clamp14` is `& 0x3fff`; `n` is a u8 with two's-complement sign.
* **Missed branch in the 3000 packer:** `LaserType != 0 && ctl ∉ {1,2} && !(LaserType==1 && ctl==3)` emits a 3000 item whose header claims 2 payload words but carries 1 (`0x1004526d → 0x10045389`). Hazard for any port that copies the logic.
* **118 flag polarity:** `0x1003ed5a–0x1003ed6d` keeps the argument only when `arg != 0 && LaserType == 0` (fibre); otherwise the flag word is 0. The report said "if LaserType != 0". Epilogue arg = `int(g+0x4d30)`, third word `int(g+0x4d28)` (35).
* **"fibre (power) setter" labels:** `0x1003ef40` is gated on `LaserType==1 && CO2LaserControlType==3` (CO2 analogue) plus `ZF+0x3c` and `g+0x47c7 == 0`; `0x1003f890` is gated on `g+0x4a20` (sub-device type). Neither is fibre-specific. The "fibre-only" labels on 2002/2004/3 in §0/§5 were not re-derived and should be read as "not emitted on this configuration".
* **DA:** 0x65 word 2 is `channel−1` (0-based); negative volts pass unclamped. The cited in-stream DA builder (`0x4427e0` / `0x44281a`) is wrong — `0x4427e0` is the **dwell builder**: if its bool arg is set it appends `(1000 idiv cycle)·ms` default (all-zero) records = stationary ticks `(0,0,5000 Hz,0 %)`; otherwise it appends one type-1 sub-5 record with `value = ms`, which NCModule turns into `2001[ms, 3000]` (mode 0). This **upgrades** the type-1 sub-5 reading "2001 mode 0 = wait v ms" from INFERENCE to LIKELY (MainApp names the same argument ms in the sibling branch). The real in-stream DA record builder was not found.
* **Frame-id re-sends (claim "identical re-sends = re-read of an unchanged reg 1015"): REFUTED.** Logged duplicates are HAL retries of one datagram (`MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S)`, 500 ms spacing). fillFifo's own retry path re-sends the same vector/id after `Update MC Status`; the frame is popped before the first send. "reg 1015 = last *accepted* id" is DOWN to INFERENCE (high).
* **HAL call roles in §7:** `+0x1c` = retry/timeout set-up (`VM+0x1128, VM+0x1128, 1, 1, VM+0x81c` first try; `VM+0x1124, VM+0x1124, −1, VM+0x818, VM+0x81c` on retries), `+0x10` = the transaction.
* **Reg 1016 units:** A2 says item slots, A3 says bytes; only the PC's byte arithmetic is EVIDENCE. §10 step 2 now lists the four candidate deltas.

**Added (ADD)**
* **V3 — the PC tick period is card-reported.** `0x10039ae0` case 0 (`0x10039b0f`): `g+0x4890 = VM+0x318` = SystemRW (block 50000 stored at `VM+0x304`) word 5 = register **50005**, `SystemRWRegName_6` 总线周期 "bus cycle"; case 1: `g+0x490c (ZFType) = VM+0x31c` = word 6 `SystemRWRegName_7` 调高轴使能 "height-axis enable" — the name/field pairing supports the 50000 name mapping. Consequence for O1: after a status refresh the PC quantises with the card's own bus cycle, so the card-side period very likely equals `g+0x4890` in µs (INFERENCE, high — the card could still consume items at a different rate than its bus cycle). Reading register 50005 once (expected 250) is the cheap first check. The XML value 250 is only the pre-connect default.
* `VM slot 17` (`0x10047650`) writes `0x65 ← [9999, 16]` via VM `+0x68` (`0x100478a0–0x100478cc`) on every record push. It does not appear in any log (the 0x65 census has only `[3,…]`, `[1,…]`, `[9999,2,…]`, `[5,…]`, `[102]`), so either it is gated in a way not traced here or logged differently. Outside A3's scope — open.
* The quantiser writes the full i32 increment but the packer keeps only its low 16 bits (`movsx word`) while taking the sign from the full i32 — an increment ≥ 32768 pulses would be silently corrupted (not reachable at 258 p/mm and 250 µs: that would be 127 mm per tick).

**Still needs the machine**
1. Read register **50005** once (`READ 0x30, 50000, 26`, word 5): if 250, the PC and card agree on the configured bus cycle (V3) — strong but indirect evidence that one item is consumed per 250 µs; §10 step 1 (count ticks for a 100 mm move against measured travel time) is still the only direct proof and should be run once before trusting speeds.
2. Card axis-1 RW words 9/10 (register 50200 + 20 + 9/10) versus XML `MAC_1.SpeedRatio / WritePluse`: if they differ, Mlaser's Y scale differs from the card's own Y config (V1).
3. Reg 1016 delta after one frame (§10 step 2).

**Verdict:** the core grammar (header layout, 3000 packing with X low, 3001/3002/9999/2001/103/109/118 field layouts, DO bit rule, 300-word flush, frame id = reg 1015 + 1, MaxItemPerFrame unused) holds. Refuted or corrected: the Y pulses/mm source, the re-send explanation, the 118 flag polarity, the "fibre" labels, the DA builder citation, the §3 speed range and "clamp14"; missed: the malformed-3000 branch and the fact that the PC period comes from card register 50005.
