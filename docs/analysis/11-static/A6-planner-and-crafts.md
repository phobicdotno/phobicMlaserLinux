# A6 — Planner parameter mapping (O12), `.chf` crafts setters (O11), layer-dialog `ManuType` folding (O13)

Static analysis of `MainApp.exe`, `Module/CADModule.dll`, `Module/NCModule.dll` (package
`/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52`, read-only).
Disassemblies: `objdump -d -M intel` in `.scratch/asm/{MainApp,CADModule,NCModule}.asm`
(shared, gitignored). All VAs are image VAs (MainApp base 0x400000, DLLs 0x10000000).
`g` = the settings singleton returned by `0x5ff1b0` (01 §0.2); `layer` = `g+0x4db8+idx·0x480`
(fibre) or `g+0x7f38+idx·0x480` (CO2, when `SP.m_iEnableLaserType`≠0) — accessor `0x437910`.
Descriptor names below come from a re-parse of the 1 004 descriptor registrations
(`mov ds:X,eax; mov ds:X+4,type` preceded by `call 0x5ff1b0; add eax,OFF`), joined to the
UTF-16 `Elem.Attr` / `pdNNN` strings; the parse reproduces every example in 01 §0.2.

Legend: **EVIDENCE** = read from code/data at the cited address; **INFERENCE** = interpretation, with
confidence.

---

## 0. Closure status

| Q | Result | Confidence |
|---|---|---|
| O12 | **Closed.** Caller found (`CGraphNcDataEng::…` at `0x437cb0`, call at `0x438329`; second flow `0x43a5c0`/`0x43a81e`). Full P0..P9 ← `g+offset` map (§1.6). `MC.AccTime` **is multiplied by 0.001 in MainApp** (`0x435fa7–0x435fb9`). P2 is `MC.SplineAccuracyRate`, *not* `CornerAccuracyRate` (which goes to `CInterpMrg+0x50`). P4 is a constant 0. P5 = `AX.InterpolationCycle`·0.001. Split-and-stop is fed by the **cool-point list** (re-scaled to lead+contour+overcut length, §1.9). Node-builder B is never used by CADModule (all three `plan` calls push 1 — third site is `0x100f79e1`, not `0x100f44c4`). The mode-5 flow is the **Simulate** button (`CManuPanel::OnSimBtn`), not a real cut. *[verifier: mapping re-derived and confirmed; see Verification notes V1–V6 for corrections/additions]* | high (mapping); medium (P5 units, int arg) |
| O11 | **Mostly closed.** `int58` is never written by MainApp → constant 1 (parity odd). `compensate_type` 3 = ribbon command `0x3481` ("positive"/阳切 Outside), 2 = the neighbouring command (Inside). Lead enum = `pd297..pd300` (0 None, 1 Line, 2 Arc, 3 Line+Arc); `LeadPosType` = `pd307..310`; lead position ratio = `GRP.LeadPosPrecent`·0.01; the 5th `SetGuideLine` bool = `GRP.OutsideIsNegativeSide`. Cool positions are **path ratios** (same unit as `double170`). PWM node = **(centre ratio `a_i`, segment length `b_i` in mm)**; the close list is *derived* from it (`close = a_i ∓ b_i/(2·L)`, `0x10065450`) — *[verifier correction V9: not "start ratio / second value"]*; `[+0x104]` is a mode int, not a bool (V10). Trailer = (`edge-seek angle memorised` bool, angle double). Text/spline/type-12 flags remain open (§2.7). | high except where marked; PWM/tool-id items downgraded (V9–V11) |
| O13 | **Closed.** Two `ON_COMMAND_RANGE` handlers: `0x54b7e0` (process type: 0→0, 1→1, 2→**5**) and `0x54b9c0` (pierce: 0→0, 1→2, 2→3, 3→4, 4→**6**, 5→**7**); the reverse map for the dialog is at `0x544720`. `ManuType` is a single int; the two item groups are mutually exclusive, not folded arithmetically. **[verifier correction V12] 4/5-stage pierce (codes 6/7) is NOT selectable in this build:** only command ids `0x4e8e..0x4e91` are ever created (labels `newLang56/51/52/53`, `0x54028f/0x540406/0x54058f/0x54071d`); ids `0x4e92/0x4e93` are never pushed and not in any menu/dialog resource, and the reverse map has no check item for 6/7. The handler cases for 6/7 are dead code. | high (0,1,2,3,4,5 reachable; 6/7 unreachable from the layer UI) |

---

## 1. O12 — MainApp → CADModule planner block

### 1.1 Call chain (EVIDENCE)

* CADModule exports only `newModuleProvider` (RVA `0xe5810`); MainApp reaches `CCADModule` through the
  `ICADModule` vtable. `CCADModule` vtable = `0x10114964` (RTTI COL `0x10118864` → `.?AVCCADModule@@`),
  197 slots. The 9/12-double wrapper `0x100dab10` is **slot 120 (`+0x1e0`)**; `calcGraphCtInterpPt`
  wrapper `0x100dab90` is slot 126 (`+0x1f8`); the `.chf` save entry (`0x100ddf00`) is slot 145 (`+0x244`);
  lead-line setter slot 81 (`+0x144`), compensate slot 84 (`+0x150`), command dispatcher slot 24 (`+0x60`).
* MainApp call sites of slot `+0x1e0` (vtable-load pattern `mov r,[obj]; mov r2,[r+0x1e0]; call r2`):
  **`0x438329`** (in function `0x437cb0`) and **`0x43a81e`** (in `0x43a5c0`). Both are members of class
  **`CGraphNcDataEng`** (vtable `0x7d0e8c`, RTTI `.?AVCGraphNcDataEng@@`; ctor `0x435a60`). Its
  `ICADModule*` is `this+0x1c`, obtained in the ctor by `GetModule(L"CAD")` (`push 0x7d07e8` = `"CAD"`,
  module-manager slot `+0xc`, `0x435bce–0x435bf1`); `this+0x8` = `INCModule*` (set by `0x435dc0` from the
  main object's `+0x1d8`).
* Both sites do `lea esi,[this+0xb0]; sub esp,0x60; mov ecx,0x18; rep movs` — i.e. they push the
  **12 doubles at `this+0xb0..+0x108` verbatim** (a1 = `+0xb0` … a12 = `+0x108`), after pushing one int
  (`[ebp-0x90]` / `[ebp-0x54]`, §1.8). Both callers are reached from `0x5963xx`: `0x43a5c0` when
  `g+0x47cc == 5`, `0x437cb0` otherwise (`0x5963b9–0x5963f3`; `g+0x47cc` is the CNC job-state word, no
  descriptor). *[verifier]* The only writer of state 5 is `0x589f82` inside `CManuPanel::OnSimBtn`
  (`0x589d20`, log string `L"CManuPanel::OnSimBtn"` at `0x843580`; error `mp76` "加工数据为空，无法进行模拟" =
  *graphic data is empty, cannot simulate*; then `mp121` "加工信息计算中..." = *process information is
  calculating*). → **`0x43a5c0` is the simulation planner; `0x437cb0` is the real-cut planner** (EVIDENCE).

### 1.2 Wrapper argument layout (EVIDENCE `0x100dab10–0x100dab80`, `ret 0x64`)

25 dwords = 12 doubles a1..a12 (`[ebp+8]..[ebp+0x60]`) + int (`[ebp+0x68]`). The wrapper builds the
`CInterpMrg` ctor block (`0x100f5ea0`, `ret 0x5c`) as:

| CInterpMrg ctor arg | source | → CInterpMrg field |
|---|---|---|
| doubles 1..10 (`rep movs 0x14` dwords, `0x100f601e` → `+0x88..+0xd0`) | **P0=a7, P1=a8, P2=a11, P3=a1, P4=a2, P5=a9, P6=a3, P7=a4, P8=a5, P9=a6** | `+0x88..+0xd0` (planner block, 05 §7.1) |
| double 11 (`[ebp+0x58]` of ctor) | a10 | `+0x50` (`0x100f6006`) |
| int (`[ebp+0x60]` of ctor) | the int | `+0xd8` (`0x100f6029`) |
| — | a12 | **unused** (never loaded by the wrapper) |

### 1.3 `CGraphNcDataEng` block layout (EVIDENCE)

Two identical 0x60-byte blocks: **rapid block at `this+0x50`** and **cut block at `this+0xb0`** (the one
passed to CADModule). Block ctor `0x435c20` (called for both from the class ctor `0x435a60`) sets defaults:

| block off | cut-block addr | default (`.rdata`) | role after mapping (§1.2) |
|---|---|---|---|
| +0x00 | `+0xb0` | 999.0 (`0x7d0eb0`) | a1 → **P3 Vmax** |
| +0x08 | `+0xb8` | 0.0 | a2 → **P4** — **never written again** (only the two copy-ctors `0x40da3d`/`0x4eb4ac` copy it) → P4 ≡ 0, inert |
| +0x10 | `+0xc0` | 0.0 | a3 → **P6 slow-start length** |
| +0x18 | `+0xc8` | 200.0 (`0x7d0ea8`) | a4 → **P7 slow-start speed** |
| +0x20 | `+0xd0` | 0.0 | a5 → **P8** (end-segment length) |
| +0x28 | `+0xd8` | 200.0 | a6 → **P9** (end-segment speed) |
| +0x30 | `+0xe0` | 80000.0 (`0x7d0ea0`) | a7 → **P0 acceleration** |
| +0x38 | `+0xe8` | 0.125 (`0x7d0e98`) | a8 → **P1 acceleration time [s]** |
| +0x40 | `+0xf0` | 0.25 (`0x7d0e90`) | a9 → **P5 interpolation period** |
| +0x48 | `+0xf8` | 0.1 (`0x7c78f8`) | a10 → `CInterpMrg+0x50` |
| +0x50 | `+0x100` | 0.05 (`0x7c4710`) | a11 → **P2 corner precision** |
| +0x58 | `+0x108` | 2·A/Ta (`0x435cb0`: `2.0·[+0x30]/[+0x38]`, `0x7c4328`=2.0) | a12 — unused by the wrapper |

Other fields: `+0x48` = `750000.0 / [g+0xbab0]` (`0x435ea4–0x435eb6`, const `0x7d0ec0`); `g+0xbab0` (int, `fild`; read through `0x436380`) is
K = card units per mm (SystemRW word 17, = 1000 on this machine, A1 §2) — so `+0x48` = 750000/K = **750 mm/s** here,
a card-side rate ceiling (INFERENCE high for the meaning; the value follows from A1's K). *[verifier: "pulse equivalent" wording corrected]* `+0x110` = run
mode int (0 normal; `>1` → forward/backward jog, see §1.5); `+0x138` = layer index; `+0x40` = contour
count.

### 1.4 Refresh from settings — `0x435e60` (EVIDENCE, every store listed)

| store | value | descriptor | machine value |
|---|---|---|---|
| `[+0x48]` (`0x435eb6`) | `750000 / [g+0xbab0]` | pulse-equivalent ceiling | — |
| `[+0x50]` (`0x435f1c`) | `min(XFastMoveSpeed·EmptyMoveSpeedFactor, [+0x48])` (`0x435ec5–0x435f1c`) | `MC.XFastMoveSpeed` g+0x47d8 (pd89) × `MP.EmptyMoveSpeedFactor` g+0x4d38 (pd2205) | 500 × 1.1 |
| `[+0x80]` (`0x435f3d`) | `XFastMoveAcc · EmptyMoveAccFactor` | g+0x47e8 (pd90) × g+0x4d40 (pd2206) | 6000 × 1.5 |
| `[+0x88]` (`0x435f5a`) | `EmptyMoveAccTime · 0.001` | `MC.EmptyMoveAccTime` g+0x4820 (pd661) | 125 ms → 0.125 s |
| `[+0x90]` (`0x435f77`) | `InterpolationCycle · 0.001` (`fild`) | `AX.InterpolationCycle` g+0x4890 (pd238, int, "us") | 250 → 0.25 |
| `[+0xa8]` | `2·[+0x80]/[+0x88]` via `0x435cb0` | — | — |
| **`[+0xe0]`** (`0x435f9c`) | `MC.ManuAcc` | g+0x4808 (pd91 加工加速度 Cut Acc) | 6000 mm/s² |
| **`[+0xe8]`** (`0x435fb9`) | **`MC.AccTime · 0.001`** (`fld [eax+0x4818]; fmul ds:0x7d0eb8`; `0x7d0eb8` = 0.001) | g+0x4818 (pd92 加工加速时间 Process Acc Time, ms) | 200 ms → **0.2 s** |
| **`[+0x100]`** (`0x435fd0`) | `MC.SplineAccuracyRate` | g+0x4828 (pd93 曲线控制精度 Spline Precision) | 0.02 |
| **`[+0xf0]`** (`0x435fed`) | `InterpolationCycle · 0.001` (`fild`) | g+0x4890 | 0.25 |
| **`[+0xf8]`** (`0x436004`) | `MC.CornerAccuracyRate` | g+0x4830 (pd94 拐角控制精度 Corner Precision) | 0.05 |
| `[+0x108]` | `2·[+0xe0]/[+0xe8]` via `0x435cb0` (`0x436016`) | — | 60000 |

*[verifier V3 — missed in the same function]* after the cut block, `0x435e60` builds a 0x40-byte block at
`[ebp-0x4c]` and hands it to CAD slot 122 (`+0x1e8`, `0x100db1a0`, call at `0x4360c1`), which copies it into
**`CInterpMrg+0x08..+0x47`** of both interpolators (`+0x358` and `+0x4a8`, via `0x100f3f50` `rep movs 0x10`):

| block off | source | descriptor |
|---|---|---|
| +0x00 byte | g+0x4860 | `GP.IsDrillInMicoLink` pd595_1 |
| +0x01 byte | g+0x4991 | `GP.EnableMicroLinkDecc` pd594 |
| +0x08 | g+0x4868 | `GP.MicoLinkSlowDownVel` pd595 |
| +0x10 byte | g+0x4d04 | `MP.EnableSmallCircleSpeedLimit` pd1560 |
| +0x18 | g+0x4d08 | `MP.SmallCircleSpeedLimitRatio` pd1561 |
| +0x20..+0x38 | g+0x4968/0x4970/0x4978/0x4980 | `FCP.FlycutCircleVelRatio` pd290, `FlycutCirclePwmDelayTime` pd513, `FlycutLineOpenPwmForwardCycle` pd514, `FlycutLineColsePwmForwardCycle` pd515 |

The `CInterpMrg` ctor itself additionally reads `JumpAddTime.txt [Arc2SegVelK] K_X/K_Y` (`GetPrivateProfileIntW`,
default 100, ×0.01 → `+0x68/+0x70`, `0x100f5f04–0x100f5fc4`; already in 05 §3). The port's `planner_block` must carry
these too.

No `1000.0` constant (`0x7d0ec8`) is used anywhere outside the descriptor initialisers
(`0x7a55bf…0x7aa26b`); the only `0.001` multiplications on the path are the three above.

### 1.5 Per-run overrides in the two callers (EVIDENCE)

**Main cut flow `0x437cb0`** (per contour: the block is rewritten and slot `+0x1e0` is called once per list item `[ebp-0x44]`; the layer index is `clamp(item.layer, 0, 10)`, `0x437f9b–0x437fe2`; block writes `0x43807d`, `0x438160`, `0x438185`, `0x4381fd`, `0x438222`,
`0x43829a`):

| field | formula |
|---|---|
| `[+0xb0]` P3 | `v = ([this+0x110] > 1) ? MC.ForwardBackwardSpeed (g+0x47a8, pd85 暂停进退速度 "Pause BF speed") : layer.CutSpeed (layer+0x48, pd125)`; then `v = min(v, FCP.MaxSpeed (g+0x4994, int, pd509))` twice and `v = min(v, [this+0x48])` (`0x438083–0x438160`) |
| `[+0xc8]` P7 | `layer.UD_UpSpeed` (layer+0x390, pd2732 起刀.速度 "Start work Segment.Work Speed") |
| `[+0xc0]` P6 | `(P3 > P7 && layer.UD_UpEnable (layer+0x380, pd2730)) ? layer.UD_UpLen (layer+0x388, pd2731) : 0` (`0x438191–0x4381fd`) |
| `[+0xd8]` P9 | `layer.UD_DownSpeed` (layer+0x3b8, pd2742 收刀.速度 "End work Segment.Work Speed") |
| `[+0xd0]` P8 | `(P3 > P9 && layer.UD_DownEnable (layer+0x3a8, pd2740)) ? layer.UD_DownLen (layer+0x3b0, pd2741) : 0` |

`GP.SlowStart*` (layer+0x94/+0x98/+0xa0) is **not** referenced in this function.

**Second flow `0x43a5c0`** (active when `g+0x47cc == 5` = **Simulate**, see §1.1 verifier note; it also calls slots
`+0x1f8/+0x1fc/+0x200`; it only processes list items whose type word is 1 or 4, `0x43a673–0x43a68d`, and takes the layer index from CAD slot `+0x1c0` without clamping):

| field | formula |
|---|---|
| `[+0xb0]` P3 | `min(layer.CutSpeed, [this+0x48])` (`0x43a6d8–0x43a721`) — no `MaxSpeed` clamp |
| `[+0xc0]` P6 | `layer.SlowStart (layer+0x94, pd133) ? layer.SlowStartLength (layer+0x98, pd134) : 0` (`0x43a737–0x43a774`) |
| `[+0xc8]` P7 | `layer.SlowStartSpeed` (layer+0xa0, pd135) (`0x43a796`) |
| P8/P9 | untouched (ctor defaults 0 / 200, or whatever the last main-flow run left) |

So the **"slow start" that the planner implements (05 §7.1 P6/P7) is driven by the layer's 起刀 "start
work segment" (`GP.UD_Up*`) in the real cut flow, and by `GP.SlowStart*` only in the mode-5 (simulation) flow**; P8/P9
(收刀 "end work segment", `GP.UD_Down*`) are the mirror at the contour end (the 05 doc had P8/P9 unnamed).
A third function `0x445760` also rewrites `[+0xb0]` per contour from `layer.CutSpeed`/`MaxSpeed`
(`0x445cbf–0x445cdc`) around a saved copy of the block; it does not call slot `+0x1e0`. *[verifier V4]* It **does**
pass the block to CAD slot 134 (`+0x218`, `0x100dea00`) at `0x445ec1`, which builds a *second* `CInterpMrg` at
`CCADModule+0x4a8` with the same 10-double permutation except **P8 := 0.0 and P9 := 200.0** (`0x100dea2d–0x100dea3f`,
`ds:0x1010f6e0` = 200.0). The pushed copy is taken at `0x445cae`, *before* the `[+0xb0]` override, so P3 there is
stale. Runs only while `g+0x47cc ∈ {2,4,5}` (`0x445881–0x4458a4`); returns per-contour cool count, contour length and
PWM-segment count through its out-pointers (`0x100dead7–0x100deb36`) — INFERENCE medium: progress/time estimation.

### 1.6 Final map P0..P9 (ready for `plan/params.py`)

| P | CInterpMrg | planner | source (this machine) | value | unit |
|---|---|---|---|---|---|
| P0 | +0x88 | +0x28 A | `MC.ManuAcc` | 6000 | mm/s² |
| P1 | +0x90 | +0x30 Ta | `MC.AccTime`·0.001 | 0.2 | s |
| P2 | +0x98 | +0x38 c | **`MC.SplineAccuracyRate`** | 0.02 | mm |
| P3 | +0xa0 | +0x40 Vmax | `min(layer.CutSpeed, FCP.MaxSpeed, 750000/pulseEq)` (main flow) | per layer | mm/s |
| P4 | +0xa8 | +0x48 floor | constant 0 | 0 | mm/s |
| P5 | +0xb0 | +0x50 | `AX.InterpolationCycle`·0.001 | 0.25 (250 µs) | (ms as stored; core-side use per 05 §7.4) |
| P6 | +0xb8 | +0x58 slow-start len | `UD_UpEnable ? UD_UpLen : 0` (main) / `SlowStart ? SlowStartLength : 0` (mode 5) | per layer | mm |
| P7 | +0xc0 | +0x60 slow-start v | `UD_UpSpeed` (main) / `SlowStartSpeed` (mode 5) | per layer | mm/s |
| P8 | +0xc8 | (end-segment len) | `UD_DownEnable ? UD_DownLen : 0` | per layer | mm |
| P9 | +0xd0 | (end-segment v) | `UD_DownSpeed` | per layer | mm/s |
| a10 | +0x50 | — | `MC.CornerAccuracyRate` | 0.05 | mm — consumed by `CInterpMrg` itself (`0x100f68e5/0x100f7408`; *verifier:* `0x100f6473/0x100f648c` read `[edx+0x50]` of a 0x48-byte piece record, not `CInterpMrg+0x50`), not by `CVelocityPlanning` |
| int | +0xd8 | — | NCModule start index (§1.8) | 0 normally | glyph index |

**Correction to 05 §7.1:** "P2 = `MC.CornerAccuracyRate`" → P2 = `MC.SplineAccuracyRate` (0.02); the
junction formula `f(r, 0.5/Ta, c)` therefore uses **c = 0.02 mm** on this machine. `CornerAccuracyRate`
(0.05) enters `CInterpMrg+0x50` and is compared/copied around piece records in the point generator
(`0x100f68e5`: pushed to a glyph virtual; `0x100f7408`). *[verifier: the `0x100f6473` cite was a piece-record field.]* P1 =
0.2 s (not 0.125) for cutting; the rapid block uses 0.125 s but is not handed to CADModule.

### 1.7 Is `AccTime` divided by 1000? — **Yes, in MainApp** (EVIDENCE `0x435fa2–0x435fb9`: `call 0x5ff1b0; fld QWORD PTR [eax+0x4818]; fmul QWORD PTR ds:0x7d0eb8; … fstp QWORD PTR [edx+0xe8]`; `0x7d0eb8` holds `0.001`). Same for `EmptyMoveAccTime` (`0x435f48–0x435f5a`) and `InterpolationCycle` (`fild`, `0x435f65`/`0x435fdb`).

### 1.8 The int argument (EVIDENCE `0x4382a0–0x4382f6`, `0x43a79c–0x43a7eb`)

`v = INCModule->slot175()->+0x38` then `if (*INCModule->slot170() != 0) v = 0`. In NCModule
(`CNCModule` vtable `0x1008a1c4`) slot 175 = `0x10036910` = `lea eax,[this+0x120]` (a struct; `+0x38` →
`CNCModule+0x158`), slot 170 = `0x100368c0` = `lea eax,[this+0x108]`, and slot 171 (`0x100368d0`) stores
its argument to `+0x108` (called from MainApp `0x501428`, `0x566eab` with `push 9`). `CInterpMrg+0xd8`
is consumed only as the index argument of the glyph-list accessor `0x10067dc0(&out, idx, flag)` at
`0x100f676e` and `0x100f6fce`. INFERENCE (medium): the **glyph index to start from** (break-point resume);
0 when a resume list is armed elsewhere. No writer of `CNCModule+0x158` was found by pattern
(`[reg+0x38]` on the `+0x120` sub-struct) — leave as is; the value is 0 for a fresh job.

### 1.9 Split-and-stop feed, node-builder B (EVIDENCE)

* `0x100f6310(pieces, outPositions, ratios)` is called from `0x100f6c3f` (fn `0x100f6710`, the
  per-contour planner) and `0x100f780b` (fn `0x100f6f10`, `calcGraphCtInterpPt`). In both, the ratio
  vector is filled by `0x10018d40(&vec, 0x10060730(contour, &tmp))` (`0x100f67c3–0x100f67d7`,
  `0x100f7025–0x100f703c`), and `0x10060730` reads **`[contour+0x14c]` = the cool-point list**
  (`0x10060762–0x10060768`), rotating it so that entries ≥ `[+0x170]` come first and subtracting
  `[+0x170]` from each (`0x100607d6–0x1006081c`). *[verifier V5]* The fill depends on the `CInterpMrg+0x58` mode switch (`0x100f6fbf–0x100f729d`): mode 0 = the
  list above; mode 1 **replaces** it (move-assign `0x10018d40`) with `0x10060a80(contour, [+0x60])` (the same list
  shifted by `d=[+0x60]`, `0x100f70e3–0x100f70f0`); modes 2/3 leave it empty (no cool stops). `0x10060730` also
  (a) adds 1.0 to entries that became negative (`0x100608fb–0x1006090e`), (b) **rescales every ratio to the full
  pierce-to-end path**: `r' = (r·L + Llead)/(Llead + L + [+0x188])` with `L=[+0x48]`, `Llead = 0x1002d1c0(lead
  block)` (line part = hypot for type 1/3, arc part = |Δangle|·radius for type 2/3) (`0x1006094a–0x100609bb`), and
  (c) drops trailing entries > 1.0 (`0x100609d2–0x100609f1`). The `closePwmPosRatios` list (`+0x12c`) is served by
  `0x1005fb80/0x10060140` and is **not** what feeds `0x100f6310`. → the planner stops at **cool points**
  (INFERENCE high), consistent with `GP.CoolPostionDelay` (g+0x4870, eNewLang101 "cooling point delay").
  The `outPositions` argument of `calcGraphCtInterpPt` is its own `[ebp+0xc]`; when it is NULL the ratio
  vector is resized to 0 first (`0x100f77f5–0x100f77fb`, `0x10003af0` = `vector<double>::resize`).
* `plan` (CVelocityPlanning slot 1, `0x100fffe0`; `cmp byte [ebp+0x10],0` at `0x10100068` → nonzero = `0x100ff220`, zero = `0x100ff930`) is called at `0x100f61b7` (`push 1`, `0x100f61a8`), `0x100f6dc7`
  (`push 1`, `0x100f6dba`) and `0x100f79fc` (`push 1`, `0x100f79e1`) *[verifier: the earlier third cite `0x100f44c4/0x100f44c6` is `push 1; call [vt+0]` = scalar-deleting destructor of `[+0x108]` in the `CInterpMrg` dtor, not `plan`]*; `newVelocityPlanning` (`0x100ff1b0`)
  is used only at `0x100f4458` and `0x100f619c`. **Node-builder B (mode 0) is never selected by
  CADModule.** (MotionCtrl.dll's own copy was not re-checked; NCModule does not import it.)

---

## 2. O11 — `.chf` crafts scalars: who sets them

### 2.1 `int58` (`[+0x58]`) — constant 1 (EVIDENCE)

CADModule has no writer besides ctor/reader/clone (03 §6.1). MainApp has exactly four
`mov DWORD PTR [reg+0x58],…` stores (`0x40b2d5`, `0x40d974`, `0x4eb3e3`, `0x506bec`), all inside
copy-constructors of MainApp's own classes (adjacent `+0x54/+0x57` copies), none on an `IGraph`. No
`IGraph` vtable slot writes it. → `int58 = 1` always; parity odd ⇒ offset sign `+1` when
`compensate_type ∉ {2,3}` and `lead.flag = !orientation XOR arg` (03 §6.1.1). `io/chf.py` may write `1`.

### 2.2 `compensate_type` (`[+0xf0]`) 2 vs 3, `compensate_width` (`[+0xf8]`)

* `CCADModule` slot 84 (`+0x150`, `0x100e4930`, `ret 4`) → `0x100b5760(bool positive)`: for every
  selected type-8 contour: `0x1006c130(0,&w,0)` then `0x1006b460(positive)` (`0x100b58bf–0x100b58c1`)
  → `[+0xf0] = positive ? 3 : 2` (03). MainApp caller: `0x48d280` — an `ON_COMMAND_RANGE` handler,
  `positive = (cmdID == 0x3481)` (`0x48d29e–0x48d2a5`), guarded by `0x489760()` (busy flag). Ribbon
  buttons for ids `0x3480`/`0x3481` are created at `0x47515f`/`0x47530a`. *[verifier: upgraded to EVIDENCE]* the label
  key is pushed in the same construction block: `push L"mf98"` at `0x475090` before `push 0x3480`, `push L"mf99"` at
  `0x47523c` before `push 0x3481`; lang.txt `mf98#阴切#Inside`, `mf99#阳切#Outside`. The range is registered as
  `ON_COMMAND_RANGE(0x3480,0x3481)` by the runtime message-map initialiser `0x456c3e–0x456c5c`. **Table: 2 = 阴切 Inside,
  3 = 阳切 Outside** (label mapping; the geometric side still depends on contour orientation).
* *[verifier]* `0x1006b460` only changes `[+0xf0]` when compensation is **already enabled** (`cmp eax,-1; je`
  at `0x1006b47a`) — the button flips the side, it does not turn compensation on. It always recomputes
  `lead.flag [+0x1c8] = positive ? (orient[+0xdc]==0) : (orient!=0)` and re-applies the lead (`0x1005c510`). In slot 84
  the two `0x1006c130(NULL, &type, false)` calls (`0x100b584a`, `0x100b58d0`) are getters for the undo record, not a
  width change.
* Width: `0x1006c130(double* w, int* type, bool apply)` (`ret 0xc`) sets `[+0xf8]` (`0x1006c1ad`) and
  `[+0xf0]` (`0x1006c1a2`) and re-runs the offset `0x1006aef0`, restoring both on failure
  (`0x1006c1bf/0x1006c1c6`). 18 callers in CADModule (`0x100b584a … 0x100d5abd`); the layer-driven
  path `0x100cb1c0` (called from `0x100da9a0`) computes `[+0xf8]` and the PWM arrays from a
  per-layer table (`0x100cb482–0x100cb4b3`). INFERENCE (medium): width = `GP.*` compensate distance of
  the contour's layer (pd507), applied when the layer's compensation is enabled.

### 2.3 Lead line (`<GuideCurve Para>`) — slot 81, full argument map (EVIDENCE)

`CCADModule` slot 81 (`+0x144`, wrapper `0x100e48c0`, `ret 0x34`) → `0x100b53a0`:

| wrapper arg | MainApp source (`0x48cb56–0x48cbf6`, block = `g+0xb0b8`) | descriptor | use in `0x100b53a0` |
|---|---|---|---|
| a1 bool `[ebp+8]` | `[blk+0x28]` = g+0xb0e0 | `GRP.IsOnlyForEncolseContour` pd320 只对封闭图形有效 | only closed contours (`cmp [eax+0xd8],1`, `0x100b5499`) |
| a2 bool `[ebp+0xc]` | `[blk+0x29]` = g+0xb0e1 | `GRP.IsOnlyForSelectContour` pd321 | selected list vs all (`0x100b5406`) |
| a3 double `[ebp+0x10]` | `fild [blk+0x24]` = g+0xb0dc | `GRP.LeadPosPrecent` pd319 起点位置 (int %) | `× 0.01` (`fmul ds:0x1010f7e8`=0.01, `0x100b55a9–0x100b55ba`) → passed to `0x1006a990(contour, a4, ratio)` = start-point placement → **`double170` = ratio** |
| a4 int `[ebp+0x18]` | `[blk+0x20]` = g+0xb0d8 | `GRP.LeadPosType` pd318: options `pd307` 自动(长边优先) Auto-Long-Side=0, `pd308` 自动(顶点优先) Auto-Vertex=1, `pd309` 统一长度百分比 Unified Percent=2, `pd310` 不变起点 Keep=3 | start-point mode |
| a5 int `[ebp+0x1c]` | `[blk+0x2a]` = g+0xb0e2 | `GRP.OutsideIsNegativeSide` pd322 最外层为阴切 | 5th arg of `SetGuideLine` (side parity, 03 §6.1.1) |
| a6 int `[ebp+0x20]` | `[blk+0]` = g+0xb0b8 | `GRP.GuideLineType` pd315: **`pd297` 无 None=0, `pd298` 直线 Line=1, `pd299` 圆弧 Arc=2, `pd300` 直线+圆弧 Line+Arc=3** (option table `0xa60578`, builders `0x795aa7…0x795ae3`) | `lead.type` |
| a7 double `[ebp+0x24]` | `[blk+0x10]` = g+0xb0c8 | `GRP.GuideLineLength` pd317 | `lead.length` |
| a8 double `[ebp+0x2c]` | `[blk+0x18]` = g+0xb0d0 | `GRP.GuideArcRadius` pd599 | `lead.arc_radius` |
| a9 double `[ebp+0x34]` | `[blk+0x8]` = g+0xb0c0 | `GRP.GuideLineAngle` pd316 | `lead.angle_deg` |

Each contour gets an undo record (`0x100d60a0/0x100d60d0` snapshot `[+0x1a8]` block and `[+0x170]`).
The machine's `GuideLineType="2"` = Arc (r = `GuideArcRadius` 2 mm). Closes 03 O3/O4 for lead.type
and `double170`.

### 2.4 Cool points (`<coolPos Para>`, `[+0x14c]`) — unit = path ratio (EVIDENCE)

`0x10060730` compares each entry with `[+0x170]` (`fld [edi+0x170]; fcomp [edx]`, `0x100607d6`) and
subtracts it (`0x1006081c`); `[+0x170]` is a ratio (multiplied by the length in `0x1005b390`, 03).
The split-and-stop then does `s = r·totalLength` (05 §8). → **cool positions are ratios 0..1 measured
from the contour's original start.** They are added by the `CCADModule` interactive **tool 15** (handler table `this+0x118+4·id`, filled at `0x100ddedb`:
`+0x154 = 0x100dc8f0` → `0x100bcb00`, which works on `[graph+0x14c]` at `0x100bcd95`). *[verifier V11]* slot 24
(`+0x60`, `0x100ded20`, `ret 8`) is **not** `DoCommand(id,arg)`: its args are a pointer to a 16-byte point (copied to
`+0x1e0..+0x1ef`, `0x100dedb1–0x100dedd8`) and a byte flag (`+0x203`); it dispatches on the **currently selected tool
id `[this+0x1d8]`** (`0x100dee04–0x100dee10`), which is set by slot 21 (`+0x54`, `0x100da7e0`, `SetTool(id)`, store at
`0x100da855`). The handlers read the click point from `+0x258/+0x260`. i.e. ids 10/15 are mouse-tool modes. The MainApp caller that issues id 15 (the "冷却点 Cool Point" tool, `mf406`) was not isolated
(only two `+0x60` vtable-pattern sites exist, `0x4a82d3`/`0x59140f`, both generic). `GRP.IsLeadPtCool`
(g+0xb168, pd604) / `IsPeekPtCool` (g+0xb169, pd605) are the automatic-cool-point switches.

### 2.5 PWM pairs (`<PWM Control>`) (EVIDENCE + INFERENCE medium)

* Command **id 10** (`+0x140 = 0x100dc4e0` → `0x100c2d60`) calls `IGraph` slot 47 (`+0xbc`,
  `0x1006a340`, `ret 0x14`) = `(int glyphIdx, double pos, double len)`: rejects `len < 0.01`
  (`0x1006a350–0x1006a364`), converts the glyph-local position to a ratio via slot 10 (`+0x28`) and then
  requires `size(+0x12c) == 2·size(+0x108)` (`0x1006a3a0–0x1006a3c8`). *[verifier V9 — corrected]* the size test only gates a hit-test, it is not a precondition: if equal, each pair
  `(close[2i], close[2i+1])` is tested and a click **inside an existing segment removes that node** (erase
  `+0x118[i]`, `+0x108[i]`, then regenerate, `0x1006a41e–0x1006a4c3`); otherwise a new node is inserted only if it does
  not overlap its neighbours: `|pos − a_j|·L ≥ 0.5·(b_j + len)` (`0x1006a4df–0x1006a541`, `ds:0x1010f728`=0.5). The
  close list is **derived** by `0x10065450`: `close[2i] = a_i − 0.5·b_i/L`, `close[2i+1] = a_i + 0.5·b_i/L`
  (`0x100654f4–0x1006553d`, `L=[+0x48]`), regenerated only when `[+0x104] == 1`. → **`a_i` (`+0x108`) = centre ratio,
  `b_i` (`+0x118`) = segment length in mm**; the `closePwmPosRatios` list holds the two end ratios (EVIDENCE). `[+0x104]` gates the regeneration (`==1`) and is compared `==1` / `==2` in `calcGraphCtInterpPt`
  (`0x100f754c`, `0x100f7810`); CADModule writes **2** (`0x1009f64b`) and **3** (`0x1009845c`, `0x10099a91`) to it →
  it is a *mode* int, not a boolean *[verifier V10]* (`MP.IsEnablePWMPerContour` pd1608, g+0x4cfc, is the global enable). The layer curves
  `GP.PWMCurveNodes` (layer+0xcc, pd154) / `GP.FreqCurveNodes` (layer+0xf8, pd155) are strings parsed
  elsewhere and are **not** what `+0x108/+0x118` store.
* Slot 45 (`+0xb4`, `0x10069b80`) and slot 49 (`+0xc4`, `0x10064930`) also touch the arrays (22/8 refs;
  slot 49 = copy/assign of both vectors).

### 2.6 Trailer `trailer_bool` / `trailer_double` (EVIDENCE + INFERENCE medium-high)

`Save(path, bool, double)` (slot 145 `+0x244`) is called at `0x4abc2d` with `bool = g+0x4b1c`,
`double = g+0x4bb8` (`0x4abbe7–0x4abbfc`). `g+0x4b1c` is set to 1 in `0x489950` (strings `A250211_2`
"当前记忆寻边角度" = *current memorised edge-seek angle*, format `" : %.2f"`) and at `0x59321d` right after
`g+0x4bb8` is written from an angle computed against `π/2` (`0x5931b0–0x593212`, in the job-start function
`0x590d00`); it is reset to 0 at `0x4897de`. *[verifier]* `ds:0x9e8158` is BSS, set to π/2 at runtime by `0x7336e3`
(`ds:0x844230`=π ÷ 2.0); update rule `θ0 += 0.5·(θ − θ0 − π/2)` only if `|θ − θ0 − π/2|·(180/π) > 0.02`
(`ds:0x9e8150` = 180/π set at `0x733703`, `ds:0x7c43f0`=0.02, `ds:0x7c4708`=0.5). The `%.2f` display multiplies by
`ds:0x95c4c0` = 180/π (`0x6bcca3`) → stored in **radians**, shown in degrees. Further `g+0x4b1c := 1` writers:
`0x489ae9`, `0x489b27`, `0x529642`, `0x5933a7`, `0x5aff90`; the `A250211_2` string is in the next function (`0x489baf`). Other writers of `g+0x4bb8`:
`0x529637` (`[obj+0x298]`), `0x5aff85` (`[obj+0x1e0]`). → **trailer_bool = "edge-seek angle memorised",
trailer_double = that angle (radians; `%.2f` shown after conversion)**. The Linux port can write `0 / 0.0`.

### 2.7 Still open in O11

* Text (`d130/d138/d140`, `font_d0/d1/d2`), spline `int1/int2`, ContourEx link records — not traced (no
  MainApp caller located in this pass).

---

## 3. O13 — `ManuType` folding (EVIDENCE)

Layer dialog class holds the layer index at `this+0x220`; handlers write
`g + 0x4df0 + idx·0x480` = `GP.ManuType` (layer+0x38, pd815) then call `0x545500(idx)` (refresh).

*[verifier]* Both are `ON_COMMAND_RANGE` entries (WM_COMMAND, notify code 0): A = static map entry at `.data 0x937854`
`{0x111,0,0x4e84,0x4e86,0x3b,0x54b7e0}`; B = runtime initialiser `0x53d13a–0x53d16c` `{0x111,0,0x4e8e,0x4e93,0x3b,
0x54b9c0}`. They are command items (menu/ribbon-style check items), **not combo boxes** — "combo index" below means
item position.

**Handler A `0x54b7e0` — process-type group, control ids `0x4e84..0x4e86`** (`id − 0x4e84`):

| combo A index | label | `ManuType` |
|---|---|---|
| 0 | `newLang50` 标准切割 Standard cutting | **0** (`0x54b822`) |
| 1 | `pd811` 定高切割 Fix Height Cut | **1** (`0x54b843`) |
| 2 | `pd811-1` 高级定高 Adv Fix Height Cut | **5** (`0x54b864`) |

**Handler B `0x54b9c0` — pierce group, ids `0x4e8e..0x4e93`** (jump table `0x54bad0`):

| combo B index | label | `ManuType` |
|---|---|---|
| 0 | `newLang56` 不启用 Not enabled | **0** (`0x54ba02`) |
| 1 | `newLang51` 一级穿孔 | **2** (`0x54ba26`) |
| 2 | `newLang52` 二级穿孔 | **3** (`0x54ba4a`) |
| 3 | `newLang53` 三级穿孔 | **4** (`0x54ba6b`) |
| 4 | *(no item created)* | **6** (`0x54ba8c`) — dead case |
| 5 | *(no item created)* | **7** (`0x54baad`) — dead case |

**Reverse map (dialog init `0x544720`, `0x5447c6–0x544a0b`):** seven `SetCheck`-style calls (`0x4bea10`) on items
`[dlg+0x1a52c..+0x1a544]`: `+0x1a52c` "standard" ⇐ `ManuType ∈ {0,2,3,4,6,7}`; `+0x1a530` ⇐ `==1`; `+0x1a534` ⇐
`==5`; `+0x1a538` "not enabled" ⇐ `==0`; `+0x1a53c` ⇐ `==2`; `+0x1a540` ⇐ `==3`; `+0x1a544` ⇐ `==4`. *[verifier:
there is no item for 6/7 — the earlier "(6→4, 7→5)" was not in the code.]* Label binding (EVIDENCE, `0x53f782…0x53faee`,
`0x540223…0x54071d`): `0x4e84` newLang50, `0x4e85` pd811, `0x4e86` pd811-1; `0x4e8e` newLang56, `0x4e8f` newLang51,
`0x4e90` newLang52, `0x4e91` newLang53. So the two items are **exclusive views of one int**: choosing a
pierce level overwrites a fix-height choice and vice-versa (a pierce with fixed height is not
representable). *[verifier]* Codes 6/7 are **not** producible from this dialog: ids `0x4e92/0x4e93` are never created (no
`push 0x4e92/0x4e93` anywhere in MainApp; a scan of RT_MENU/RT_DIALOG/string resources finds neither id). They can
only arrive via a hand-edited `BkLayerPara.xml`. Also note the handlers always write the **fibre** layer table
(`g+0x4df0+idx·0x480`) even though the accessor `0x437910` switches to `g+0x7f38` when `g+0x46d8≠0` (CO2). `GP.NoFollow`
(layer+0x3f) and `GP.AdvFixHeightCutPos` (layer+0x58) are independent attributes, not folded.

Consolidated **`ManuType` enum for `plan/*.py` / `BkLayerPara.xml`:**

| code | meaning | pierce stages |
|---|---|---|
| 0 | direct / standard cut | 0 |
| 1 | fixed-height cut (`pd811`) | 0 |
| 2 | 1-stage pierce | 1 |
| 3 | 2-stage pierce | 2 |
| 4 | 3-stage pierce | 3 |
| 5 | advanced fixed-height cut (`pd811-1`, uses `AdvFixHeightCutPos`) | 0 |
| 6 | 4-stage pierce (handler only; no UI item in this build) | 4 |
| 7 | 5-stage pierce (handler only; no UI item in this build) | 5 |

(`stages = {2:1,3:2,4:3,6:4,7:5}.get(code,0)`; the legacy property-grid list `pd810..pd813` maps 0..3
only.)

---

## 4. Tables ready for code

### 4.1 `plan/params.py` — planner block from settings

```python
# all EVIDENCE §1.4–1.6; g = BkManuPara/BkHardPara values, layer = BkLayerPara record
def planner_block(g, layer, mode5=False):   # mode5 = Windows "Simulate" flow only (verifier V1); real cuts use mode5=False
    vceil = 750000.0 / g["pulse_equiv_x"]                      # this+0x48
    vmax  = min(layer["CutSpeed"], float(g["FCP.MaxSpeed"]), vceil)
    if mode5: vmax = min(layer["CutSpeed"], vceil)
    P7 = layer["SlowStartSpeed"] if mode5 else layer["UD_UpSpeed"]
    P6 = (layer["SlowStartLength"] if layer["SlowStart"] else 0.0) if mode5 else \
         (layer["UD_UpLen"] if (layer["UD_UpEnable"] and vmax > P7) else 0.0)
    P9 = layer["UD_DownSpeed"]
    P8 = layer["UD_DownLen"] if (layer["UD_DownEnable"] and vmax > P9) else 0.0
    return dict(
        P0=g["MC.ManuAcc"],                    # mm/s^2
        P1=g["MC.AccTime"] * 0.001,            # s   (MainApp does the /1000)
        P2=g["MC.SplineAccuracyRate"],         # mm  (NOT CornerAccuracyRate)
        P3=vmax, P4=0.0,
        P5=g["AX.InterpolationCycle"] * 0.001, # 250 us -> 0.25
        P6=P6, P7=P7, P8=P8, P9=P9,
        corner_precision=g["MC.CornerAccuracyRate"],   # CInterpMrg+0x50
        start_index=0)                                  # CInterpMrg+0xd8
```

Rapid-move block (not passed to CADModule, for the port's own rapids): `Vmax = min(XFastMoveSpeed·
EmptyMoveSpeedFactor, vceil)`, `A = XFastMoveAcc·EmptyMoveAccFactor`, `Ta = EmptyMoveAccTime·0.001`.

### 4.2 `io/chf.py` — crafts field semantics

| field | write | read |
|---|---|---|
| `int58` | `1` | ignore (parity only) |
| `compensate_type` | `-1` none, `2` inside, `3` outside | sign = {2:-1, 3:+1}, else parity(int58) |
| `compensate_width` | mm | |
| `pwm_enable` (`[+0x104]`) | `1` default; **mode int** (CADModule also writes 2/3) — preserve on read | |
| `pwm_nodes[i]` = `(a_i, b_i)` | **centre ratio / segment length mm** (verifier V9) | |
| `close_ratios` | derived: `[a_i − b_i/(2L), a_i + b_i/(2L)]` per node | recompute, don't trust |
| `double170` | lead start position ratio 0..1 (`LeadPosPrecent/100`) | |
| `double188` | over-cut length mm (unchanged inference) | |
| `lead.type` | 0 None, 1 Line, 2 Arc, 3 Line+Arc | |
| `lead.angle_deg`, `lead.length`, `lead.arc_radius` | deg, mm, mm | |
| `lead.flag` | `orientation XOR positive` (03) | |
| `cool_pos[i]` | ratio 0..1 of contour length `L` from the geometric start; the planner re-bases to `double170` and rescales to `(r·L+Llead)/(Llead+L+double188)` | |
| trailer | `0`, `0.0` (edge-seek angle memory off) | |

---

## 5. Residual checks (cheapest confirmation)

| item | cheapest step |
|---|---|
| Group A/B exclusivity | Wine: set 3-level pierce → `ManuType="4"`; then "Fix Height" → `1` (no pierce item checked). Codes 6/7: statically unreachable (V12) — no step needed. |
| PWM pair semantics | Wine: 100 mm square, add one PWM segment at 50 mm with length 10 mm → expect node `(0.125, 10.0)` (0.125 = 50/400) and close ratios `0.1125/0.1375`. |
| Cool-point ratio | Wine: 100 mm square, cool point at 25 mm from start → `cool_pos = 0.25`. |
| Trailer bool/double | Wine: run 电容寻边 (edge seek) once, save → trailer `1` and an angle; fresh start → `0 / 0.0`. |
| `P2 = SplineAccuracyRate` effect | bench only if a live comparison is wanted: change `MC.SplineAccuracyRate` 0.02→0.5 and observe corner speeds in `VelDecc.txt`; static evidence is already conclusive for the *mapping*. |
| `CNCModule+0x158` (start index) semantics | capture: break a job and resume; if the `.mcf`/point stream restarts mid-contour the index is the resume glyph. |
| Text/spline/type-12 flags | Wine: place one text object, one spline, one micro-joint (type-12) contour; diff `autosave.chf`. |

---

## 6. Method notes

* Descriptor re-parse: `/tmp/…/scratchpad/desc.py` → 1 004 records, 3 false positives (same as 01 §0.2);
  option tables read from `X+0x54/+0x58` and resolved through the `std::string` builders
  (`mov ecx,TABLE+0x1c·k` preceded by `push L"pdNNN"`).
* Vtable-slot call sites found with the pattern `mov B,[obj]` … `mov R,[B+slot]` … `call R`
  (`vscan2.py`); plain `[reg+off]` loads without a preceding vtable load are member accesses and were
  excluded (e.g. the false `+0x1e0` hits at `0x56d815`).
* Function boundaries from `push ebp; mov ebp,esp` after `int3/ret/jmp` (10 476 MainApp, 2 462
  CADModule starts); RTTI names from COL → TypeDescriptor.

---

## Verification notes

Adversarial re-derivation (independent disassembly reads of `.scratch/asm/{MainApp,CADModule,NCModule}.asm`,
own descriptor re-parse — 1 046 offsets / 1 579 registrations, strings resolved from the pushes preceding
`call 0x5ff1b0; add eax,OFF` — own `.rdata` constant and RTTI reads). Every key fact 1–11 was re-checked at the cited VA.

**Confirmed as stated (EVIDENCE re-read):**
* Wrapper `0x100dab10` = `CCADModule` slot 120 (vtable `0x10114964`, COL `0x10118864` → `.?AVCCADModule@@`); argument
  permutation P0=a7, P1=a8, P2=a11, P3=a1, P4=a2, P5=a9, P6=a3, P7=a4, P8=a5, P9=a6, `+0x50`=a10, int→`+0xd8`, a12
  unused (`0x100dab16–0x100dab6e`, ctor `0x100f6006/0x100f601e/0x100f6029`). Both MainApp call sites push 12 doubles
  from `this+0xb0` + one int.
* `0x435e60` stores: AccTime·0.001 (`ds:0x7d0eb8` = 0.001, bytes `fca9f1d24d62503f`), SplineAccuracyRate→`+0x100`,
  CornerAccuracyRate→`+0xf8`, InterpolationCycle·0.001→`+0xf0`, ManuAcc→`+0xe0`; descriptor names match my parse
  (pd91/92/93/94/238). Block ctor defaults (999, 0, 0, 200, 0, 200, 80000, 0.125, 0.25, 0.1, 0.05) re-read from `.rdata`.
* P4: only two `fstp [reg+0xb8]` in MainApp (`0x40da3d`, `0x4eb4ac`, copy-ctors); the four `mov dword [reg+0xb8]`
  stores are unrelated objects → P4 ≡ 0 holds.
* Main-flow min() and the P6/P8 conditions (`test ah,0x41` idioms decoded: strict `P3 > P7`, `P3 > P9`); layer offsets
  +0x48/+0x380/+0x388/+0x390/+0x3a8/+0x3b0/+0x3b8 = `GP.CutSpeed`, `UD_UpEnable/Len/Speed`, `UD_DownEnable/Len/Speed`;
  mode-5 flow +0x94/+0x98/+0xa0 = `GP.SlowStart/SlowStartLength/SlowStartSpeed`.
* Machine values re-read from `Mlaser-v0.0.0.52/File/BkManuPara.xml` (AccTime 200, SplineAccuracyRate 0.02,
  CornerAccuracyRate 0.05, ManuAcc ≈6000, EmptyMoveAccTime 125, GuideLineType 2, GuideArcRadius 2) and the Wine
  `HardPara.xml` (InterpolationCycle 250).
* `plan` mode byte: nonzero → `0x100ff220`, zero → `0x100ff930`; all three real call sites push 1 → builder B unused.
* ManuType handlers A/B and their stored constants; lead slot 81 argument order (`0x48cb6f–0x48cbf6` → wrapper
  `0x100e48c0` → `0x100b53a0` → `SetGuideLine 0x1005ce80`: type→`+0x1a8`, length→`+0x1b8`, radius→`+0x1c0`,
  angle→`+0x1b0`, a5 → lead-flag parity). Option tables: `GRP.GuideLineType` → table `0xa60578` count 4
  (pd297..pd300), `GRP.LeadPosType` → `0xa60690` count 4 (pd307..pd310).
* **Independent support for the lead enum:** `0x1002d1c0` (lead length used by the cool-ratio rescale) adds a hypot
  line length for type ∈ {1,3} and `|Δangle|·radius` for type ∈ {2,3} — exactly None/Line/Arc/Line+Arc.
* `int58`: MainApp has four `mov dword [reg+0x58]` stores, none on an `IGraph`; of 98 such stores in CADModule a
  context scan (IGraph field neighbours) flagged only `0x1007f187`, which belongs to `CEditableCircle`
  (RTTI bases `IEditableGlyph`, `IGlyph` — not `IGraph`). Constant 1 stands; confidence **medium-high** (no full
  type-aware proof over all 98).
* Trailer save call `0x4abbe7–0x4abc2d` (double `g+0x4bb8`, byte `g+0x4b1c`, slot `+0x244`).

**Corrections / downgrades applied in place:**
* **V1** `g+0x47cc == 5` is set only in `CManuPanel::OnSimBtn` (`0x589f82`) → the `0x43a5c0` flow (SlowStart*) is the
  simulation planner; real cuts never use `GP.SlowStart*` for P6/P7.
* **V2** `+0x48 = 750000/K` with K = card units per mm (A1: 1000) → 750 mm/s; "pulse equivalent" wording removed.
* **V3** Missed: slot 122 block (microlink / small-circle limit / fly-cut) into `CInterpMrg+0x08..+0x47` from the
  same refresh function (§1.4).
* **V4** Missed: `0x445760` → CAD slot 134 second interpolator, P8:=0, P9:=200, stale P3 (§1.5).
* **V5** Split-and-stop feed depends on `CInterpMrg+0x58` mode; `0x10060730` rescales ratios by lead length and
  overcut and drops >1.0; "adds" at `0x100f70e3` was a replace (§1.9).
* **V6** Third `plan` site is `0x100f79e1/0x100f79fc`; `0x100f44c4` is a destructor call. Conclusion unchanged.
  Also missed (INFERENCE medium): `0x100f6080` (2π circle generator, called from `0x100e4328`) runs its own planner
  with a copy of the block where **P3 is overridden and P6 = P8 = 0** (`0x100f616d–0x100f618c`).
* **V7** `CornerAccuracyRate` consumers: `0x100f6473/0x100f648c` are piece-record fields, not `CInterpMrg+0x50`.
* **V8** Compensate label binding upgraded to EVIDENCE (mf98/mf99 pushes); slot 84 only flips the side of an
  already-enabled compensation.
* **V9** PWM node = (centre ratio, length mm); close list derived; slot 47 toggles (insert/remove); size equality is a
  hit-test gate, not a requirement. The previous "start ratio / second value" was wrong.
* **V10** `[+0x104]` ("pwm_enable") is a mode int (writes of 2 and 3 in CADModule); do not coerce to bool in `io/chf.py`.
* **V11** Slot 24 is a click handler `(point*, byte)` dispatching on the selected tool `[+0x1d8]` (set by slot 21);
  ids 10/15 are tool modes. The MainApp site that selects tool 10/15 was not found by an immediate-push scan
  (still open; capture: set a breakpoint/log on CAD slot 21 in Wine while picking the PWM / cool-point tool).
* **V12** O13: codes 6/7 unreachable from the UI (only 4 pierce items created; no reverse-map item); the O13 answer
  otherwise stands. The handlers write the fibre layer table unconditionally.

**Residual confidence per claimed-closed item:** O12 mapping — confirmed (high); O13 codes 0/1/5 and 0/2/3/4 —
confirmed, 6/7 claim refuted; O11 int58 — medium-high; compensate 2/3 — confirmed (label EVIDENCE); lead enum and
slot-81 map — confirmed; cool-pos unit — confirmed with added rescale; PWM pair — **refuted as stated**, replaced by
V9; trailer bool/double — confirmed (radians).
