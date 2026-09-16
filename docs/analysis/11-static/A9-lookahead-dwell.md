# A9 — The look-ahead core, the contour entry velocity (X2) and the dwell builders (X1)

Static re-trace of `Module/CADModule.dll`, `MotionCtrl.dll` and `MainApp.exe` in the read-only
package `SRC = /home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52`, plus a
re-reading of the 22 leaked FIFO frames (`tests/data/mcc/fifo_frames_2025-07.txt`, raw in
`Log/2025-07-13.log` and `Log/2025-07-17.log`).
Disassemblies: `objdump -d -M intel`, cached in `.scratch/asm/{CADModule,MainApp,MotionCtrl}.asm`
(shared, gitignored; `MotionCtrl.asm` is new with this note).  All VAs are image VAs
(MainApp base 0x400000, DLLs 0x10000000).  `g` = the settings singleton returned by `0x5ff1b0`
(01 §0.2); `layer` = the layer record returned by `0x437910` (A6 §0).

Legend: **EVIDENCE** = read from code/data at the cited address; **INFERENCE** = interpretation,
with confidence.

This note closes the two planner fidelity gaps listed as X1 and X2 in `docs/STATUS.md` §2.

---

## 0. Results at a glance

| Question | Answer | Status |
|---|---|---|
| Which copy of `CVelocityPlanning` runs for a cut? | **CADModule's own copy**: `plan` = `0x100fffe0`, node builder A = `0x100ff220`, B = `0x100ff930`, slow-start = `0x100ffac0`, look-ahead core = **`0x100fdc50`**.  `MotionCtrl.dll`'s copy (`plan` `0x10016720`, core `0x100114d0`, the addresses used by 05 §7) is a *different build of the same code* and is not the one CADModule calls.  05 §7.4's "`0x100114d0`" is right for MotionCtrl, wrong for the cut path. | EVIDENCE |
| How is the entry velocity of the first piece set? | It is **forced to zero**.  Node builder A ends with `fldz; fst [end-0x20]` (last node `+0x08`) and `fstp [begin+0x08]` (first node `+0x08`) — `0x100ff8dd–0x100ff90c`.  Every contour starts and ends at rest. | EVIDENCE |
| Is there a start-velocity floor / "slow start" that raises it? | **No.**  `P4` is the only floor (`max(v, P4)` in the node builder, `P7 = max(P7, P4)` in `plan` at `0x10100045`) and it is a constant 0 on the cut block (A6 §1.3); it is applied *before* the zeroing.  The slow-start routine `0x100ffac0` runs after the builder and only ever writes `min(field, P7)` — it can lower node speeds, never raise them, and it includes node 0 in the clamp (where `min(0, P7) = 0`). | EVIDENCE |
| Does the PWM / laser-on point sit inside an already-moving ramp? | Not in the record stream: `DO9 on` is emitted before the first tick of the contour (frames 0x39/0x1f8), and the ticks that follow are the ramp's own zero-displacement samples. | EVIDENCE (frames) |
| Which parameter supplies the entry velocity? | None.  The look-ahead core takes exactly five doubles — `{A, J, Ta, P5, Vmax}` (`0x100fdc76`, `rep movs` of 10 dwords) — and the node list.  There is no entry-velocity parameter in the P0..P9 block, in the GRP/MC descriptors, or in the core. | EVIDENCE |
| So why do the leaked frames seem to leave the pierce at ~7 mm/s? | Because frames **0x39 and 0x3a are not consecutive** (§3).  0x3a is a stretch ~145 ticks into a jerk-limited ramp, not the first 99 ticks of one. | EVIDENCE (log timestamps) + INFERENCE high (kinematics) |
| What produces the 14 stationary ticks after `DO9 on`? | The cut ramp itself, not a dwell.  On a CO2 cut the vendor's dwell builder only ever emits wait *records*; its stationary-tick branch is reachable only from the fibre multi-stage pierce emitter (§2). | EVIDENCE |
| Which value/parameter/units produce a dwell, and when is the gas delay applied? | `layer+0x88` = `LaserOnDelay` (ms) after the laser DO, `layer+0x8c` = `LaserOffBeforeDelay`, `layer+0x90` = `LaserOffAfterDelay`, each as **one `2001[ms, 3000]` wait record**; the gas delay is a *different* builder (`0x4424e0`) that sums `GC.GasDelay` / `DirectGasDelay` / `ChangeGasDelay` per state flag, subtracts the elapsed time and emits the remainder, again as a wait record.  A zero value emits nothing. | EVIDENCE (builders) / INFERENCE medium (which flag selects which delay) |

---

## 1. The CADModule velocity planner, end to end (EVIDENCE)

`CCADModule` builds two `CInterpMrg` objects; each owns a `CVelocityPlanning` whose `plan` is
slot 1.  In CADModule that is `0x100fffe0`, reached from `0x100f61b7`, `0x100f6dc7` and
`0x100f79fc`, all three with `mode = 1` (node builder A) — A6 §1.9.

`plan(this, CNurbsContour* pieces, const double params[10], bool modeA)`, `ret 0xc`:

| step | address | what |
|---|---|---|
| copy the block | `0x10100016` | `rep movs 0x14` dwords = **ten** doubles `P0..P9` → `this+0x28 .. this+0x70`.  (MotionCtrl's build copies only 8; the CADModule build carries `P8/P9` as well.) |
| clamp `Ta` low | `0x1010000d–0x1010002c` | `Ta = max(Ta, 0.06)` (`ds:0x10115180` = 0.06) |
| clamp `Ta` high | `0x1010002e–0x10100045` | `Ta = min(Ta, 0.25)` (`ds:0x10111098` = 0.25) |
| floor `P7` | `0x10100045–0x1010005c` | `P7 = max(P7, P4)` |
| node list | `0x1010007b` / `0x1010008c` | builder A `0x100ff220` (modeA) or B `0x100ff930` |
| slow start | `0x10100097` | `0x100ffac0(nodes)` |
| jerk | `0x1010009c–0x101000cb` | `J = (A·Ta·0.5 ≤ Vmax) ? (A+A)/Ta : Vmax·4.0/Ta/Ta` (`ds:0x1010f728` = 0.5, `ds:0x101115a8` = 4.0) — 05 §7.1 confirmed on the build that actually runs |
| look-ahead | `0x101000f5` | `0x100fdc50(&nodes, &{A, J, Ta, P5, Vmax})`, `this = this+0x78` |

The 0.99 constant of 05 §7.2 is `ds:0x101116d0`, read at `0x100ff866` inside node builder A, and
is only a *logging filter*: `fcomp [pieces.begin + k·0x48 + 0x40]` (the per-piece speed factor
that pass 4 multiplies into the node limit) and, when that factor is below 0.99 and the log flag
`[ebp-0x60]` is clear, `fprintf_s("NodeID:%d V:%f mm/s", …)` into `Log/VelDecc.txt`
(`0x100ff87a–0x100ff8b6`).  It scales nothing.  **Correction to 05 §7.2**, which read it as a
factor applied to node speeds.

### 1.1 Node builder A ends at rest (EVIDENCE `0x100ff8dd–0x100ff90c`)

```
100ff8dd: mov  ecx,[esi+0x4]          ; nodes.end
100ff8e0: fldz
100ff8e2: fst  QWORD PTR [ecx-0x20]   ; (end - 0x28) + 0x08  = last node's speed limit := 0
100ff8ea: mov  ecx,[esi]              ; nodes.begin
          …                            ; count == 0 → _invalid_parameter
100ff90c: fstp QWORD PTR [ecx+0x8]    ; first node's speed limit := 0
```

Node stride is `0x28` and `+0x08` is the node speed limit (05 §7.2).  This is the last thing the
builder does, after the junction formula, the `Vmax` clamp, the `P4` floor and the feed fill-in.
**Every contour therefore enters and leaves the look-ahead with `v = 0` at both ends.**

### 1.2 The slow-start routine only lowers speeds (EVIDENCE `0x100ffac0–0x100ffd60`)

`0x100ffac0(nodes)` with `this` = the `CVelocityPlanning` (so `[this+0x58] = P6`,
`[this+0x60] = P7`, `[this+0x68] = P8`):

* returns at once when the node count is < 2 (`0x100ffae6`);
* `L = last node's cumulative length`; if `P6 + P8 > L` both are replaced by `0.5·L`
  (`0x100ffafe–0x100ffb1c`);
* the head branch runs only while `P6 ≥ 0.1` (`ds:0x10111088`), walks nodes `i ≥ 1` until
  `s_i + 0.01 > P6` (`ds:0x1010f7e8` = 0.01), and, when `|s_i − P6| > 0.05`
  (`ds:0x1010f6b8` = 0.05), **inserts** a node at `s = P6` whose feed is `min(feed, P7)`
  (`0x100ffbb2–0x100ffc29`, insert helper `0x100fefa0`);
* then a 4×-unrolled loop over nodes `0 … i` writes `field = min(field, P7)` for the node fields
  `+0x08` and `+0x10` (`0x100ffc6d–0x100ffd51`);
* a mirror walk from the end does the same for `P8/P9` (`0x100ffb5a–0x100ffba3`,
  `0x100ffdb6`).

Every write is a `min`.  Node 0 is inside the clamped range, so its zero stays zero.  The
"slow start" of 05 §7.1 is a speed *ceiling* over the first `P6` mm, never a floor, and never an
entry velocity.

### 1.3 The look-ahead core takes no entry velocity (EVIDENCE `0x100fdc50`)

```
100fdc53: fld  ds:0x1010f8a0 (-1.0) ; fstp [this+0x48]
100fdc63: fldz                      ; fstp [this+0x58]
100fdc6a: mov  DWORD [this+0x50],-1
100fdc71: call 0x100fc2e0           ; vector<node>::operator= (copy the node list in)
100fdc7c: mov ecx,0xa ; rep movs    ; 10 dwords = {A, J, Ta, P5, Vmax} → this+0x10 .. this+0x30
100fdc9c: (count < 2) → return
100fdca5: call 0x100fc640           ; profiles.resize(0)   (profile stride 0xa8)
100fdcc8: call 0x100fda90(profiles, 0, count-1)   ; per-piece profile build (chunked/parallel)
100fdccf: call 0x100fd120                          ; backward pass
100fdcd6: call 0x100fcc30                          ; forward pass
100fdcde: call 0x100fa6d0(profiles)                ; finalise
```

The five doubles are the whole parameter surface of the core.  There is no sixth "entry
velocity", no minimum-velocity constant, and the node list it is given already has `v_0 = 0`.
(The corresponding MotionCtrl chain is `0x100114d0 → 0x10014870, 0x10014d20, 0x100116e0,
0x10012b20, 0x10012740, 0x10014740`, i.e. the one 05 §7.4 describes; the CADModule chain is the
same shape with `0x100fc2e0 / 0x100fc640 / 0x100fda90 / 0x100fd120 / 0x100fcc30 / 0x100fa6d0`.)

### 1.4 Consequence for the port

`plan/lookahead.py` (node 0 and node N at rest, jerk `J = 4·Vmax/Ta²` for the cut block) is a
faithful model of what the binary does.  **No change was needed in `lookahead.py`.**

---

## 2. The dwell builders (X1)

### 2.1 `0x4427e0` has two branches, and only one of them is reachable (EVIDENCE)

`0x4427e0(list, rec, int ms, bool asTicks)` — thiscall, `ret 0x10`:

```
4427f3: movzx eax,BYTE [ebp+0x14]   ; asTicks
4427f9: je   0x44283b               ; false → the record branch
4427fb: call 0x5ff1b0
442802: mov  eax,0x3e8 ; cdq
442808: idiv DWORD [ecx+0x4890]     ; 1000 idiv AX.InterpolationCycle(µs) = 4 here
44280e: imul eax,[ebp+0x10]         ; × ms   → n stationary records
442826: lea ecx,[ebp-0x20] ; call 0x425e00 ; default (all-zero) record
442832: call 0x447be0                      ; append, n times
-- record branch --
44283b: rec[0x16] = 1                ; record type 1 (IO)
442845: rec[0x00] = 5                ; sub-code 5
44284e: rec[0x04] = 0
44285b: rec[0x18] = ms
442865: call 0x448230                ; append ONE record
```

The tick branch makes `ms · (1000 / AX.InterpolationCycle)` = `ms · 4` ticks on this machine —
which can never be 14.  The record branch makes a type-1 sub-5 record, which NCModule turns into
`2001[ms, 3000]` **only when `ms > 0`** (A3 §4.4, type 1 sub 5; `emit2001` mode 0).

### 2.2 Nine of the ten call sites pass `false`; the tenth is fibre-only (EVIDENCE)

Argument order (`ret 0x10`, four stack args): the **first** push is `asTicks`, then `ms`, then
`rec`, then `list`.  The ten call sites of `0x4427e0` in `MainApp.exe`:

| call | `asTicks` | `ms` argument | role |
|---|---|---|---|
| `0x442c33` | `push 0` | stage descriptor `+0x40` | pierce-stage dwell |
| **`0x442cf0`** | **`movzx BYTE [stage+0x48]`** | `max(stage+0x38 − elapsed, 0)` | pierce-stage dwell, **the one site that can ask for ticks** |
| `0x442d6f` | `push 0` | stage descriptor `+0x44` | pierce-stage dwell |
| **`0x443259`** | `push 0` | **`layer+0x88`** (`0x443247`) | **after the laser DO: `LaserOnDelay`** |
| `0x4432e7` | `push 0` | `layer+0x8c` | before the laser DO goes off: `LaserOffBeforeDelay` |
| `0x443326` | `push 0` | `layer+0x90` | after the laser DO goes off: `LaserOffAfterDelay` |
| `0x4433cd`, `0x44340f` | `push 0` | `layer+0x8c`, `layer+0x90` | same pair on the second (resume) path |
| `0x443f88`, `0x443fbf` | `push 0` | layer 0 `+0x8c`, `+0x90` | job-end laser off |

The three `0x442c..`/`0x442d..` sites live inside the **multi-stage pierce (drill) emitter**
`0x442890(list, rec, layerIdx)`, which is entered only when

* `layer+0x38` (`ManuType`) ≠ 0 **or** `layer+0x410` ≠ 0, **and**
* `ZF.ZFType` (`g+0x490c`) ≠ 0, **and**
* `g+0x47c7 == 0 || g+0x4881 ≠ 0`

(`0x4428d2–0x442921`; otherwise it jumps straight to its epilogue at `0x442ec0`).  The stage count
comes from `ManuType` through the jump table at `0x442ed4` (`ManuType−2 ∈ 0..5` → 1..5 stages,
else 1) and the loop walks `0x58`-byte stage descriptors at `layer + 0x158 + k·0x58`
(`0x442b71–0x442b7e`) — the `DrillHeight{k}` / `DrillDelay{k}` / `BeforeLaserOffDelay{k}` family of
02 §3.3, which only the **fibre** layer record fills in.  This machine's CO2 layer record carries
no `ManuType`, and the 22 leaked frames contain no staged-pierce record at all, so the emitter did
not run for the job behind them (EVIDENCE for the frames; INFERENCE high that `ManuType = 0` for
every CO2 layer here).  INFERENCE (high) that `stage+0x48` means "spend this stage's delay as
stationary ticks instead of a card-side wait" — it also gates a second emitter,
`0x4373c0(list, min(stage+0x38, elapsed), stage)`, at `0x442d2b`.

**For a CO2 cut, therefore, every dwell that is emitted is a wait record.**

The layer offsets are identified by their position in the emitter, not by a descriptor parse:
`0x4425d0` (laser **on**: DO `g+0x88c` = `LGP.CO2DOLaser` for CO2 / `g+0xbc` for fibre, then the
PWM-set record from `layer+0x7c` freq and `layer+0x80` duty) is immediately followed by
`0x443259` with `layer+0x88`, and `0x4426f0` (laser **off**: DO off + PWM duty 0) is bracketed by
`layer+0x8c` and `layer+0x90`.  02 §2.3 names exactly that triple `LaserOnDelay` (pd126 / CO2
`A241025_3` 开光延时) / `LaserOffBeforeDelay` (pd137 关光前延时) / `LaserOffAfterDelay`
(pd138 关光后延时).  INFERENCE: high.

**So no CO2 job stream contains dwell ticks at all**, and the leaked frames confirm it: the 22
frames decode to `3000 ×2160, 3001 ×6, 9999 ×5, 3002 ×3, 2001 ×3, 103 ×2, 109 ×1, 118 ×1`
(A3 §8) and all three `2001` are the ZF `[0x03000002, 20000]` form — not one `2001[ms, 3000]`.

### 2.3 The gas delay is a different builder (EVIDENCE `0x4424e0`)

`0x4424e0(list, rec, int layerIdx, int elapsedMs)`:

```
4424f3: if (this+0x14b)  delay += g+0x4858   ; and clear the flag
442523: if (!this+0x1c8) delay += g+0x4854
44253b: if (this+0x14c != -1 && layerIdx != this+0x14c) delay += g+0x485c
442563: delay = max(delay - elapsedMs, 0)
442589: this+0x14c = layerIdx
44258f: rec[0x16] = 1 ; rec[0] = 5 ; rec[4] = 0 ; rec[0x18] = delay ; append
```

`g+0x4854/0x4858/0x485c` are the three `GC` gas delays (`GasDelay`, `DirectGasDelay`,
`ChangeGasDelay`; all 100 ms in `File/BkManuPara.xml`).  Which of them is added depends on three
state bytes — a "gas just switched" flag (`+0x14b`), a "gas already flowing" flag (`+0x1c8`,
cleared by `0x4423f0` when the gas is re-opened) and the previous layer index (`+0x14c`).
INFERENCE (medium) on the individual flags; EVIDENCE that the result is *one wait record* and
that it is reduced by the travel time already spent and dropped when it reaches 0.

### 2.4 What the leaked prologue therefore is

Frames 0x39 and 0x1f8 (identical except for one pulse of carry in the incoming rapid) contain,
after 79 ticks:

```
3001 ; 3002[5] ; tick(0,0) ; 3001 ; 9999[2,4,4] (DO3 gas on) ;
103[1000,0] ; 2001[0x03000002,20000] (ZF cut height + its wait) ;
9999[2,0x100,0x100] (DO9 on) ; 13 × tick(0,0,5000 Hz,4 %)
```

There is **no `2001[ms, 3000]`** anywhere in it: `LaserOnDelay = 0` on CO2 layer 2, and the gas
remainder was already 0.  The port's `LaserOnDelay + GC.GasDelay = 0 + 100 ms → 400 stationary
ticks` was wrong twice over — wrong record type and wrong sum.

**Port change.**  `plan/items.py`: `JobStreamBuilder.add_wait` emits the type-1 sub-5 record;
`prologue` emits `gas_delay_ms` once, with the gas DO, and `pierce_dwell_ms` (=
`layer.LaserOnDelay`) after the laser DO; `epilogue` emits the two laser-off waits.
`dwell_tick_count` / `add_dwell` are kept and documented as the dead branch.
`plan/__main__.py` passes `LaserOnDelay` and `GC.GasDelay` separately.
Result: `test_prologue_frames_504_and_57` now reproduces **the whole 297-word frame** word for
word instead of only its prologue prefix.

---

## 3. Frames 0x39 and 0x3a are not consecutive (X2)

### 3.1 The log says so (EVIDENCE)

`Log/2025-07-17.log`, the only lines carrying `DataEx:40 66 12a <id>`:

| time | id | |
|---|---|---|
| 13:32:19.172 | 0x1f8 = 504 | run A |
| 13:32:21.399 | 0x20b | run A |
| 13:38:49 … 13:40:25 | 0x279, 0x283, 0x28e, 0x2d5, 0x2e3 | run A |
| 13:57:34.761 | **0x50 = 80** | id drops → run B |
| 13:57:35.922 | 0x57 | run B |
| **14:22:17.654** | **0x39 = 57** | id drops → **run C** |
| 14:22:20.109 … 14:50:54 | 0x73, 0xd4, 0x129 | run C |
| 14:53:00.085 | 0x84 | id drops → run D |
| **15:04:19.282** | **0x3a = 58** | id drops → **run E** |
| 15:04:19.931 … | 0x3c (5 re-sends) | run E |

The frame id is the card's own FIFO-frame-id register + 1 (A3 §0), so it restarts with every job.
**0x39 belongs to the run that started around 14:22, 0x3a to the run that started around 15:04** —
42 minutes and two job starts apart.  They are frames 57 and 58 of *different* runs, and the
committed file keeps only the first occurrence of each id, so nothing in the data ties them
together.  (That the two runs are of the same drawing is likely — 0x39 and 0x1f8 are the same
contour prologue and differ only in where two adjacent pulses fall, and 0xd4 = 0x2e3 byte for
byte — but "same drawing" does not make frame 58 of run E follow frame 57 of run C: the same
contour prologue appears at frame 504 in run A and frame 57 in run C, i.e. the runs are not
aligned.)

### 3.2 The kinematics say so (EVIDENCE + INFERENCE high)

Frame 0x3a's 99 ticks, read as `dx` pulses: `.a.a.a.aa.a.a.aa…` — four pulses inside the first
nine ticks, i.e. ≈ 7 mm/s at the very first tick, with the acceleration already ≈ 400–580 mm/s².

A contour leaving rest under this layer's parameters (`Vmax = CutSpeed = 50`,
`Ta = MC.AccTime = 0.2 s` ⇒ `J = 4·Vmax/Ta² = 5000 mm/s³`) covers `J·t³/6` and needs
`t = (6/(J·258.04))^⅓ = 16.7 ms = 67 ticks` to accumulate one X pulse.  With the persistent
truncate-with-carry quantiser the first pulse can be brought forward only to tick 66 and the
fourth only to tick 105 — checked over the whole carry range in
`test_port_cut_start_matches_leaked_frames`.  No start-from-rest profile of this machine can put
four pulses in nine ticks.  Since §1.1 proves the vendor *does* start from rest, frame 0x3a is
not a contour start.

### 3.3 What frame 0x3a actually is

A jerk-limited ramp already ≈ 145 ticks (36 ms) old.  Sampling the port's own S-curve for a
straight line with `A = MC.ManuAcc`, `Ta = MC.AccTime` and `Vmax = 116.6 mm/s`
(⇒ `J = 4·Vmax/Ta² = 11 660 mm/s³`), quantising with a start carry of 0.58 and taking ticks
146…244 reproduces the frame: the same net −90 pulses, never more than one pulse apart at any
tick, and 97 of the 99 per-tick values identical.  Fitting `J` freely over
`J ∈ [4 000, 22 000]` picks `J ≈ 11.7 · 10³` as the only clear minimum.

`Vmax` itself stays **UNVERIFIED**: the drawing behind the 2025-07 logs is not in the package, and
116.6 mm/s does not match `PCO2LayerParam2.CutSpeed = 50` as the XML reads *today* (several other
cutting frames also exceed 50 mm/s — 0x3c averages 54.7 mm/s over its 99 ticks).  Either the layer
carried a different `CutSpeed` in July 2025, or a run-time speed override exists that has not been
traced.  This does not affect the conclusion: whatever `Vmax` was, `J = 4·Vmax/Ta²` from rest
cannot produce 0x3a's first nine ticks.

### 3.4 Consequence for the port

Nothing in `plan/lookahead.py`, `plan/scurve.py` or `plan/sampler.py` is wrong here.  The port
already does what the binary does.  `test_port_cut_start_matches_leaked_frames` was re-stated to
pin (a) the impossibility of 0x3a being a contour start under this machine's parameters and
(b) the port reproducing 0x3a as a mid-ramp stretch to within one pulse.

---

## 4. What would settle the remainder

The two gaps are closed as *port behaviour*; two vendor numbers stay unproven and need a capture:

1. **A vendor dry-run frame stream of a drawing the port also has** (11 §7 step 9 / session E):
   run the same `.chf` under the vendor tool with the laser key off, capture the whole `0x66`
   stream with `tcpdump`, and diff it against `nexcut-plan` frame by frame.  That settles the
   entry `Vmax` of §3.3, the rapid/cut block boundaries, and whether the ramp really is one
   uninterrupted S-curve per contour.
2. **A CO2 laser-on capture with `LaserOnDelay ≠ 0`** (session E): set `PCO2LayerParam*.
   LaserOnDelay` to, say, 200 ms, cut one contour, and confirm a single `2001[200, 3000]` between
   `DO9 on` and the first tick — and that no stationary ticks appear.  The same capture with the
   gas previously off settles §2.3's flag selection (`GasDelay` vs `DirectGasDelay` vs
   `ChangeGasDelay`) and the elapsed-time subtraction.

Until then the port emits `LaserOnDelay` and the gas remainder as wait records and nothing else,
which is what every leaked frame shows.
