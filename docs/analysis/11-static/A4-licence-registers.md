# A4 — Licence (CDog) registers: DENY-LIST for `mcc/safety.py`, ALLOW-LIST for the port, and "can M1 jog without the exchange?"

Task (scope = *protecting* the machine, never circumventing): the Linux port will **not** implement the card-licence exchange. It must therefore (1) never write any register the exchange touches, and (2) know whether the firmware refuses motion/FIFO when the exchange has not run. This note recovers, from `MainApp.exe` + `Module/NCModule.dll` alone, every register address / command vector the `CDog` licence path reads or writes and the order it uses them, and shows where `MainApp` uses `dogState` / `dogActiveReslut*` — to decide if gating is PC-side.

`SRC = /home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52` (read-only). Tooling: `objdump -d -M intel` (listings cached in `.scratch/asm/{NCModule.dll,MainApp.exe}.asm`), a stdlib PE/vtable resolver. **EVIDENCE** = read at the cited VA; **INFERENCE** (confidence) = interpretation.

Closes the static half of **O6** (07 §14, 08 §5, 99-gaps §2.4/§4 row O6). **This document contains no keygen, patch, or bypass — only the register set to avoid and read-only diagnostics.**

Register/func conventions (from 04 §3.2–3.5): a transaction is a word vector `[func, addr, count, …]`; `func 0x30` = READ, `func 0x40` = WRITE, `func 0x26` = firmware chunk. `CMCHalAPI::readReg` @`0x100225e0` / `writeReg` @`0x10022760` gate which addresses reach the wire.

---

> **VERIFIER BANNER (2026-09-15, adversarial re-derivation — see "Verification notes" at the end).** Two of the headline conclusions of the first version were **REFUTED** and have been corrected in place: (a) PC-side licence gating is **not** cosmetic — `CManuPanel::OnStartBtn`, `OnEmptyMoveBtn`, `OnBoundingBtn`, `OnForwardBtn`, `OnBackwardBtn`, `OnBreakFindBtn` all refuse to act unless the dog state `[CManuPanel+0x1e8]` is 0; (b) registers **150/151 are not shown to be licence registers** — their only runtime callers are the FTC (height-controller) dialog (150←9999 after a parameter write, 150←5555 for FTC factory reset) and the edge-seek debug dump (151). The 59500–59511 CDog block stands (with corrected per-slot directions). The deny-list stays (it is conservative) but its labels changed.

## 0. Bottom line

* **The card licence lives in a dedicated register block 59500–59511 (`0xe86c`–`0xe877`).** It is touched by class `CDog` (`NCModule.dll`, RTTI `.?AVCDog@@` @`0x100a0008`, embedded in `CNCModule` at `+0xf0`) and two further helper functions at `0x100010b0`/`0x10001d00`, all through the VM singleton's data-area helpers. **CONFIRMED** (§2). ~~plus an access-switch at 150 and data register 151~~ — **REFUTED as stated**: 150/151 have no CDog caller; their only runtime callers are the FTC dialog and edge-seek (§1). They are still on the deny-list (§3).
* ~~MainApp never gates a motion action on the licence state~~ — **REFUTED (verifier).** `CNCModule` slot 170 (`+0x2a8`, `0x100368c0` = `lea eax,[ecx+0x108]`) returns `&CDog.state` (`CDog+0x18`, set to 0..8 by CDog at `0x10003877`…`0x10004eb0`); big-init copies it into `[CManuPanel+0x1e8]` (`0x56706f`) and uses it as the `dogState_*` index (`0x56762f`, `imul 0x1c`). `OnStartBtn` (`0x57ef50`, guard `0x57f48e`), `OnEmptyMoveBtn` (`0x579d00`/`0x583270`), `OnBoundingBtn` (`0x589070`), `OnForwardBtn` (`0x5892f0`), `OnBackwardBtn` (`0x5894b0`), `OnBreakFindBtn` (`0x589670`), `0x59e630` and three watchdog branches in `0x568a50` require `[+0x1e8]==0`. Home, Stop, jogStop, Z±, ZF, Gas, go-origin, mark-point handlers do not test it. **PC-side gating of job start / frame / dry-run / forward-backward / breakpoint-resume is CONFIRMED** (§4).
* 08 §5.4 (2950 jobs ran on days whose `Code.txt` shows `Check code Error`) therefore only shows that `CDog.state` was 0 when Start was pressed on those days (the `Check code Error` line is a diagnostic dump in `0x10003be0`'s path and does not by itself set a non-zero state) — it is **not** evidence that the PC side does not gate. It does not matter for the port (the port does not run MainApp), but it removes the "jobs ran anyway" argument for the firmware half too. Whether firmware V201.52 itself refuses commands when the data area is invalid **cannot be settled statically** — successes are never logged, so the 503/exception-3 jog rejections in the logs prove nothing either way. One bench step settles it (§6).
* Action for the port: **treat 59500–59599 as write-forbidden and read-pointless; never touch the card RTC. Also deny writes to 150** (150←5555 is the vendor's password-protected *FTC factory reset*, 150←9999 follows an FTC parameter write — both are destructive/restart actions, not motion). 151 is a read-only buffered-record register used by edge-seek; deny by default. Everything the port needs for M1 (status, jog, home, go-to, IO/DA, FIFO) lives in a disjoint register set (§5); FTC parameter editing is out of scope and would need 150 (§1).

---

## 1. The single-word thunks around 150/151 (the grep target) — *not* licence (verifier-corrected)

`grep 'push +0x9[67]$'` in `NCModule.dll.asm` → `0x1002c53d`, `0x1002c55d` (both `push 0x96`) and `0x1003ec63` (`mov …,0x97`). These sit in the thunk band `0x1002c510–0x1002c564`. EVIDENCE (`.scratch/asm/NCModule.dll.asm`):

```
1002c510: mov eax,[ecx]; mov edx,[eax+0x80]; push 0x65; push 0x65; call edx; ret   ; writeReg(reg=0x65, val=0x65)
1002c520: mov eax,[ecx]; mov edx,[eax+0x80]; push 0x66; push 0x65; call edx; ret   ; writeReg(reg=0x65, val=0x66)
1002c530: mov eax,[ecx]; mov edx,[eax+0x80]; push 0x15b3; push 0x96; call edx; ret ; writeReg(reg=150, val=5555)  ← FTC factory reset (verifier; was 'access-switch open')
1002c550: mov eax,[ecx]; mov edx,[eax+0x80]; push 0x270f; push 0x96; call edx; ret ; writeReg(reg=150, val=9999)  ← after FTC param write (verifier; was 'access-switch close')
1002c570: push ebp; call 0x10038a10; mov edx,[eax]; mov edx,[edx+0x204]; jmp edx   ; → VM slot 129 = readReg(151) (0x1003ebd0)
```

**Correction (verifier):** `mov eax,[ecx]` in these thunks dereferences the **`CNCModule`** `this` (they are CNC vtable entries), so `+0x80` is **CNC slot 32** = `0x1002b570`, which tail-jumps to the VM singleton (`0x10038a10` → `0x100b3db0`) slot 27 `+0x6c` = `0x100494e0`. That function builds `[0x40 (from 0x1008a670), arg1, 1, arg2]` and sends it via `[esi+0x10]→+0x10` — i.e. a single-word WRITE `addr=arg1 ← value=arg2`. Args are pushed right-to-left, so `push 0x15b3; push 0x96` = `write(addr 150, value 5555)`. (VM slot 32 `+0x80` = `0x10040cb0` is an unrelated `[0x76,4,…]→0x65` command builder.) Independent confirmation of the `+0x6c` semantics: `0x1002be70`/`0x1002be90` = `VM+0x6c(0x67, 2/1)` = FIFO start/clear. These five thunks are entries in the **`CNCModule` interface vtable** `0x1008a1c4` (208 slots, the object MainApp talks to):

| CNC vtable slot | offset | thunk | effect |
|---|---|---|---|
| 58 | +0xe8 | `0x1002c510` | write reg **0x65 ← 0x65** (single word; = the ZF-stop / re-sync one-shot, A1 row 17, 04 §3.6) |
| 59 | +0xec | `0x1002c520` | write reg **0x65 ← 0x66** (the "single-word `[0x66]` to 0x65", 08 §3.3, rejected exc 2 in logs) |
| **61** | **+0xf4** | `0x1002c530` | **write reg 150 ← 5555** — called by `CZFStatusView` FTC factory reset `0x5fda16` (verifier) |
| **62** | **+0xf8** | `0x1002c550` | **write reg 150 ← 9999** — called by `CZFStatusView` FTC parameter write `0x5fd267` (verifier) |
| 67 | +0x10c | `0x1002c570` | **read reg 151** (5-word records, count from ZF status word 14), via `0x1003ebd0`; called by `CManuPanel` edge-seek dump `0x592641` (verifier) |
| 37 | +0x94 | `0x1002c4f0` | generic `writeCmd` forwarder → VM vtable `+0x80` |

~~5555 / 9999 are literally the "open / close" values of the group `lang.txt` calls `加密数据域访问开关`~~ — **REFUTED (verifier).** `加密数据域访问开关` is not in `lang.txt`; it is one of 15 bare UTF-16 group-name literals in MainApp (`0x7c5ac8`, assigned to globals by `0x6a46d0`) with **no register address and no value** attached anywhere. Nothing in either binary pairs 5555/9999 with that name or with "open/close". 99-gaps §2.4 stays an unconfirmed inference and is **contradicted** by the runtime callers below.

**Caller check — REFUTED (verifier).** There is no `call` immediate to the thunks (true), but MainApp calls them virtually. There are six `mov reg,[reg+0xf4/0xf8]` sites in MainApp: `0x48c486`, `0x48c4f6`, `0x48c9b8` (object `this+0x478`, FP args — not CNCModule, as the first version said) **and `0x5fd267` (`+0xf8`) / `0x5fda16` (`+0xf4`) on `[this+0x1dc]`**, which the first version missed. Those two are in class **`CZFStatusView`** (vtable `0x8701a4`, RTTI `.?AVCZFStatusView@@`; ctor `0x5f3b80` stores the module named `数控加工` [NC machining] = NCModule into `+0x1dc` at `0x5f3e45`). The message map at `.data 0x93aa08` routes WM_COMMAND IDs to them:
* ID `0x2715` → `0x5fce00` *FTC parameter write*: for each edited FTC property calls `NC+0x80(11000+2·i, value)` (`0x5fd1f5`–`0x5fd224`, i.e. writes into the 11000 ZF-property block), then **`NC+0xf8()` → write 150 ← 9999** (`0x5fd267`), `Sleep(500)` (`0x5fd274`), message `zf37` "参数写入成功！" [Parameters write successfully!], then `NC+0x288`.
* ID `0x2714` → `0x5fd7a0` *FTC factory reset*: `zf57` "请输入操作密码" [please input password] (literal `1273` pushed nearby), confirm `zf58` "该操作将会重置调高器所有硬件参数，是否继续" [this will reset all FTC hardware parameters, continue?], then **`NC+0xf4()` → write 150 ← 5555** (`0x5fda16`), then `zf59` "恢复出厂设定成功" [factory reset succeeded].

No 5555 precedes the 9999 in the write path, so the "open … close bracket" model does not fit. **INFERENCE (medium-high):** register 150 is an FTC/ZF command/key register: **9999 = commit/apply FTC parameters (FTC restarts, cf. `zf50`), 5555 = FTC factory reset.** Firmware-side meaning not provable statically.

**151 (verifier).** `0x1003ebd0` reads `READ 151, count = 5·min([VM+0x3a4], 40)`; `[VM+0x3a4]` is word 14 of the `READ 10000 ×18` on-board-ZF status block stored at `VM+0x36c` by VM slot 82 `0x1004ea50` (`push 0x1008a644`=10000, count `0x12`). Its runtime caller is `CManuPanel` fn `0x590d00` (`0x592641`, `NC+0x10c`), which, when a debug file handle `[this+0x226c]` is open, loops reading 151 and `fprintf`s each record as `"%d, %d, %d, %d, %d\n"` (`0x8438c8`), then logs `"EdgeSeek Move Out Done %d x=%f y=%f"` (`0x8438dc`). **INFERENCE (medium):** 151 is a buffered 5-word record register (plausibly the `读缓存的编码器数据` [read buffered encoder data] group), used by edge-seek — not an encrypted-data register. No CDog function references 150 or 151 (the only `+0xf4/+0xf8/+0x10c` loads inside NCModule are the VM forwarders `0x1002bf39`/`0x1002be59`/`0x1002c00c`). The registers 150/151 are **special-cased in `readReg` `0x100225e0`/`writeReg` `0x10022760`** (EVIDENCE re-checked): readReg zero-fills without wire I/O for `104<addr<1000` except 150/151 (`0x10022604`–`0x10022612`); writeReg returns 1 without wire I/O for `104<addr<5000` except 150/151 (`0x10022770`–`0x10022788`), and also for `5008<addr<6000` and `51000<addr<59000`. So they are deliberately wire-reachable — but that says nothing about *licence*.

---

## 2. The `CDog` data-area block: 59500–59511 (`0xe86c`–`0xe877`)

`CDog` (functions `0x10003710–0x100050b0`; `0x100036c0` itself is a `std::string operator+` helper called from all over the DLL, not CDog — verifier) plus two helper functions `0x100010b0` and `0x10001d00` reach the card through the **VM singleton** (`call 0x10038a10` → `0x100b3db0`, vtable `0x1008bbc4`, RTTI `.?AVCVirtualMachine@@`) data-area helpers. Each helper builds a `[func, addr, count, …]` vector with a **hard-coded address**. EVIDENCE (verifier re-derived every row from the helper bodies; func constants `[0x1008a66c]=0x30` READ, `[0x1008a670]=0x40` WRITE):

| VM slot (off) | fn VA | addr (dec) | dir | count | Callers found (`[VM+off]` loads) | Role (INFERENCE) |
|---|---|---|---|---|---|---|
| 102 (+0x198) | `0x1003ca10` | **59500** `0xe86c` | WRITE | 4 (from caller buffer) | `0x100010e3`, `0x10001159` (fn `0x100010b0`) | verify / init write |
| 103 (+0x19c) | `0x10038f60` | **59500** `0xe86c` | READ (via VM+0x248) | 1 | `0x1000110b`, `0x1000117d` (fn `0x100010b0`) | *added by verifier* — read-back of 59500 |
| 104 (+0x1a0) | `0x1003cc00` | **59501** `0xe86d` | READ | 4 | `0x100038ad` (fn `0x10003840` → `verify data area failed!` `0x10084a04`) | verify data area |
| 105 (+0x1a4) | `0x1003cd20` | **1050** `0x41a` | READ | 3 | (CNC forwarders `0x1002c44c`, `0x1002f7e2`) | program-id/version/time triple (also read by normal poll, 04 §3.5) |
| 106 (+0x1a8) | `0x1003ceb0` | **59502** `0xe86e` | **WRITE (via VM+0x68)** | **2** (`arg1,arg2`) | no `[x+0x1a8]` load found in NCModule | **card RTC write, 2 words** — first version said "READ 1", **wrong** |
| 107 (+0x1ac) | `0x1003cf60` | **59502** `0xe86e` | **READ** | 2 | `0x10003b47` (fn `0x10003b00` → `get card clock fail!!`), `0x1000485e`, `0x10004e4e`; CNC forwarder `0x1003695c` | **card RTC read** (first version said "R/W") |
| 108 (+0x1b0) | `0x1003d0f0` | **59510** `0xe876` | WRITE | = caller vector length | `0x10003e1f` (fn `0x10003dc0`), `0x1000440c` (fn `0x10004380`) | user-record write |
| 109 (+0x1b4) | `0x1003d300` | **59510** `0xe876` | READ | = caller arg2 | `0x10003e52`, `0x100043c3` (fn `0x10004380` → `User data is error/was broken`) | user-record read |
| 110 (+0x1b8) | `0x1003d440` | **59511** `0xe877` | WRITE | = caller vector length | `0x1000401f` (fn `0x10003fc0`), `0x10004249` (fn `0x100041c0`) | admin-record write |
| 111 (+0x1bc) | `0x1003d650` | **59511** `0xe877` | READ | = caller arg2 | `0x10004052`, `0x10004203` (fn `0x100041c0` → `Admin data was broken / not current App data`) | admin (vendor) record read |
| 113 (+0x1c4) | `0x10038e50` | **59503** `0xe86f` | READ (via VM+0x248) | 1 | `0x10001d41` (fn `0x10001d00`, returns 999999 `0xf423f` if not connected), `0x10004793` | card RTC / licence-time field |
| 114 (+0x1c8) | `0x10038e80` | **59503** `0xe86f` | WRITE (via VM+0x6c) | 1 | `0x10004dcd`, `0x10004ef2` (fn `0x100048c0` Active → `-Active: reset clock OK`) | **card RTC write (clock reset)** |
| 129 (+0x204) | `0x1003ebd0` | **151** `0x97` | READ | 5·min(ZFstat[14],40) | CNC slot 67 thunk `0x1002c570` only; **no CDog caller** | ~~encrypted data register~~ — buffered 5-word records, edge-seek (see §1) — **not part of the CDog block** |

The fixed counts `0x100`/`0x20` of the first version for 59510/59511 are not in the helpers (count is caller-supplied), so the "32-byte record" match to `DataLen:32` (08 §5.1) is **unverified** (INFERENCE, low).

**Boundary (important, EVIDENCE).** The hardware-parameter area that `BkHardPara.xml` mirrors starts at **59600 = `0xe8d0`** (`readParamFromCard` "adds 0xE8D0", 04 verifier / A2; verifier re-saw `add esi,0xe8d0` @`0x10050548` and `add ebx,0xe8d0` @`0x100508c6`). So the licence block **59500–59511 sits just below** the legitimate parameter area and is cleanly separable: **59500–59599 = licence; 59600+ = hardware parameters (allowed).**

**Exchange order (INFERENCE, medium — from the caller topology of `0x10004540` "Key initial" and `0x100048c0` "Active"):**
1. read program-id/version/clock (`1050`, `59502`) → `checkMCStatus`;
2. read + verify data area (`59500/59501`), read admin record (`59511`) and user record (`59510`);
3. compare card clock (`59502/59503`) against `lastLicenseClock`; on activation, **write** the RTC (`59503`, slot 114) and data area (`59510/59511`, slots 108/110) → `-Active: reset clock OK`;
4. ~~the access switch (reg 150 ← 5555 / ← 9999) brackets the privileged writes~~ — **REFUTED (verifier):** no CDog function reaches CNC slots 61/62 or register 150; the RTC 2-word write (slot 106) has no located caller.
No `Active OK` is written unless a valid code was entered (08 §5.3).

---

## 3. DENY-LIST for `mcc/safety.py`

The port must **refuse to emit** any of the following. Encode as an assertion in the register-write path (reject before framing):

```python
# --- LICENCE / CDog registers — the port MUST NEVER write these, and never
#     read the RTC/data area (reads are pointless and risk waking the exchange). ---
DENY_WRITE_REGS   = {0x96, 0x97}                     # 150 FTC commit(9999)/factory-reset(5555) key reg, 151 buffered-record reg (verifier relabel)
DENY_WRITE_RANGE  = range(0xe86c, 0xe8d0)            # 59500..59599 licence data area (RTC, admin, user, verify)
#   0xe86e/0xe86f (59502/59503) = card RTC          → NEVER write (clock is licence-critical)
#   0xe876/0xe877 (59510/59511) = user / admin recs → NEVER write
#   0xe86c/0xe86d (59500/59501) = verify/data area  → NEVER write
DENY_SWITCH_VALUES = {(0x96, 0x15b3), (0x96, 0x270f)}  # 5555 = FTC factory reset, 9999 = FTC param commit/restart — never emit
# Reads of 59500..59599 are forbidden by policy (licence only). 151 reads are harmless-looking (edge-seek dump) but not needed for M1: deny by default.
```

Rationale per entry (all EVIDENCE unless noted):
* **150 (`0x96`)** — writes 5555 / 9999 via thunks `0x1002c530`/`0x1002c550` (CNC slots 61/62). Verifier: the only runtime callers are `CZFStatusView` FTC factory reset (5555, password + confirm dialog) and FTC parameter write (9999, then "parameters written"). Not shown to be licence; still **never write** — 5555 would wipe FTC hardware parameters.
* **151 (`0x97`)** — read via `0x1003ebd0` as 5-word records (count from ZF status word 14); only runtime caller is the `CManuPanel` edge-seek debug dump `0x592641`. Special-cased alongside 150 in `readReg`/`writeReg`. Not needed for M1; deny.
* **59500–59511 (`0xe86c`–`0xe877`)** — the licence data block: RTC at 59502/59503, user record 59510, admin/vendor record 59511, verify/data 59500/59501. **Writing the RTC or data area is exactly the "reset clock" / "Active" operation the port must never perform** (would alter the card's stored licence state). Reserve the whole 59500–59599 span below the 59600 hardware-parameter base to be safe (INFERENCE, high — the observed touches are 59500–59511; the guard covers to 59599).
* **1050 (`0x41a`)** — read by CDog *and* by normal status polling (04 §3.5). Not a licence-only register; **read-only is fine, never write** (it is RO on the card anyway).

The port therefore never issues any activation code, never writes register 150, and never writes the RTC or data area. The card's stored licence is left byte-for-byte untouched.

---

## 4. Where `dogState` / `dogActiveReslut*` gate the UI — they DO (verifier rewrite)

**Strings (MainApp `.rdata`, EVIDENCE, re-checked):** `dogState_normal, _TrialOver, _ClockIllegal, _ClockBroken, _Unauth, _DataBroken, _VerifError, _NotTheApp, _NoHardware, _VerifError` pushed in that order at `0x56750a…` (`0x841b20…0x841c80`); index 0 = normal. `dogActiveReslut1..11` at `0x7e8ae4…` (consumer `0x4bbba0`, message box via `ds:0x7bf07c`).

**Where the state comes from (EVIDENCE):**
* NCModule: `CDog` sits at `CNCModule+0xf0` (`0x100368e0`: `add ecx,0xf0; jmp 0x10003930`). `CDog.state` = `[CDog+0x18]` = `[CNCModule+0x108]`, written by CDog with codes 0–8 (`0x10003877`=7, `0x100038db`=8, `0x100042cc`=6, `0x1000434d`=5, `0x1000450d`=4, `0x100046d3`=0, `0x100047c1`=1, `0x10004887`=3, `0x10004eb0`=2, …) and by CNC slot 171 `0x100368d0` (`CheckUserID` mismatch stores 9). CNC slot 170 `+0x2a8` = `0x100368c0: lea eax,[ecx+0x108]` returns a pointer to it.
* MainApp big-init `0x566ee0` (this = `CManuPanel`, RTTI via ctor vtable `0x8442ac`): `[this+0x1e8]=6` if `0x588680` fails (`0x566f41`); `[this+0x1e8] = *NC+0x2a8()` (`0x56705f`–`0x56706f`); `=4` if `[NC+0x2bc()+0x3c] < 20` (`0x567158`–`0x567164`); reset to 0 if `==4` and the `NC+0x2b0` buffer holds magic `0xb77be948` (`0x5674e5`–`0x567500`, the only occurrence of the magic); used as `dogState_*` index (`0x56762f` `imul eax,0x1c`) and message box if `>=1` (`0x56767c`–`0x56772d`). These four are the only writes to `[CManuPanel+0x1e8]` in `0x562000–0x5a0000`.

**Gates (EVIDENCE — all `cmp [this+0x1e8],0; jne <skip>` followed by the same `+0x1e74/+0x1e88/+0x1e94 == 0` and `settings[+0x4380]==0` checks):**

| Function (name string pushed in it) | guard VA | effect when dogState ≠ 0 |
|---|---|---|
| `CManuPanel::OnStartBtn` `0x57ef50` | `0x57f48e` | `[ebp-0x25d]` (can-start) stays 0 → `jmp 0x582d45` (return, no job) |
| `CManuPanel::OnEmptyMoveBtn` `0x579d00` | `0x579dc7` | → `0x57a2ed` (skip) |
| `CManuPanel::OnEmptyMoveBtn` `0x583270` | `0x583337` | → `0x583c20` (skip) |
| `CManuPanel::OnBoundingBtn` `0x589070` | `0x5890f7` | → `0x5892cf` (skip frame trace) |
| `CManuPanel::OnForwardBtn` `0x5892f0` | `0x589374` | → `0x58949a` |
| `CManuPanel::OnBackwardBtn` `0x5894b0` | `0x589534` | → `0x58965a` |
| `CManuPanel::OnBreakFindBtn` `0x589670` | `0x58981f` | → `0x589cfa` (no breakpoint resume) |
| `0x59e630` (called from `0x49b863`, uses NC `+0x1ac`) | `0x59e64a` | → `0x59eada` |
| watchdog/poll `0x568a50` | `0x56ed1d`, `0x56ee0f`, `0x56ef70` | start-button enable / auto-start conditions false |

**Not gated** (no `+0x1e8` reference in the function): `OnHomeBtn 0x58a100`, `OnStopBtn 0x5880a0`, `jogStop 0x59adb0`, `OnZPlusBtn 0x57d760`, `OnZSubBtn 0x57d570`, `OnZFBtn 0x57d950`, `OnGasBtn 0x57e610`, `sysGoOrigin 0x583fe0`, `OnPlatGoOriginBtn 0x58c4f0`, `OnMarkPtBtn 0x596570`, `OnReturnMarkPtBtn 0x596980`, `OnSimBtn 0x589d20`. The XY jog handler was not identified by name; `0x57a580` is the handler for message `0x4b1` (map entry written at `0x93a088`), not a proven jog handler.

**Corrections to the first version:** `0x5904a0` is the WM_COMMAND ID `0x4655` handler (NC `+0x30/+0x70/+0x68` only), **not** `OnRunBtn`/Start; the watchdog `0x568a50` **does** reference `[+0x1e8]`; the "231 generic hits" argument is invalid (member offsets are class-relative).

**Consumer — `CheckUserID` (`0x566da0`, EVIDENCE, refines 07 §8.1):** reads the card user code via NC slot `+0x2c0 → [result+0x48]`, reads `ipAdd.ini [Soft]CheckUserID` (`0x566e4c`, default 0), and if `ini==0 ? u!=109 : ini!=u` calls NC slot `+0x2ac` with arg **9**. That slot (`0x100368d0`) stores 9 into `CDog.state` — which, if it runs before `0x56706f`, becomes `dogState` 9 and **blocks Start** by the gates above (INFERENCE, high; call order not traced). Not a card write.

**Conclusion (CONFIRMED, static):** the PC side **does** gate job start, frame/bounding, dry-run, forward/backward and breakpoint-resume on `CDog.state == 0`; homing, stop, Z moves and go-origin are not gated. This is irrelevant to a port that never runs MainApp, but it means 99-gaps O6's static half is **"PC-side gating exists (start/frame/dry-run); firmware gating unknown"**, not "cosmetic". The "NCModule motion is not gated inside the DLL" question: no call from outside CDog to CDog functions except the CNC thunks `0x100368e0/0x100368f0/0x10036900/0x10036940/0x10036970` (EVIDENCE, call-immediate scan); a shared flag checked elsewhere is not excluded (INFERENCE, medium).

---

## 5. ALLOW-LIST — the register set the port may use (disjoint from §3)

All from A1/A2 and 04 §3.5–3.7 (EVIDENCE for addresses; sub-cmd meanings per A1).

**Command register `0x65` (101), func 0x40 — sub-commands (word 0):**

| sub-cmd | meaning | vector (A1) |
|---|---|---|
| 1 | **STOP** (decel) | `[1, axisMask, 2, vd, 10·vd]`; `[1,0x1F]` = stop-all |
| 2 | **HOME** | `[2, 1<<slot, 0]` |
| 3 | **relative / absolute single-axis move (jog step)** | `[3, slot(\|0x80000000 abs), v, a, 10·a, ±d]` |
| 5 | **multi-axis move / go-to** | `[5, mask(\|0x80000000 abs), v, a, 10·a, dX,dY,dZ,dW]` |
| `[9999,1,a,b]` | FIFO clear (legacy) | |
| `[9999,2,ch,0/1]` | set DO / laser gate | |
| `[9999,4,ch,val]` | set DA (analog) | |
| `[9999,13,0xFFFF,ver…]` | connect handshake / version exchange | |
| `[7]`, `[1,8]`, `[4,…]` | offline-upload / start-manu | |
| single word `[0x65]` | ZF-stop one-shot (only if `ZFType≠0`) | |
| single word `[0x66]` | FIFO one-shot (re-sync; rejected exc 2 in logs) | |

**FIFO data register `0x66` (102), func 0x40:** `[0x66, count=1+3·items, frameId, items…]` (04 §3.7).

**FIFO run-control register `0x67` (103), func 0x40:** `1` = clear, `2` = start, `3` = stop (`startFifo 0x10050af0`, `stopFifo 0x10050dc0`, `clearFifo 0x100510f0`).

**Read blocks (func 0x30) — status / config, all allowed:**

| addr | count | content |
|---|---|---|
| 1000 | 2 / 36 | RO status (input/output/alarm/run/FIFO frame-id & margin…) — A2 §2 |
| 2000 | 50 | per-axis RO (5×10) — A2 |
| 5000 | 1 (window 5000–5008) | RW safety word (read-only in this build) |
| 10000 | 18 | on-board ZF status |
| 11000 | 39 | ZF property |
| 13000 / 13200 | — | RO |
| 50000 | 26 | system RW |
| 50200 | 100 | per-axis RW (5×20) |
| **59600 + i·0xD0** | 52 | **hardware parameters (`BkHardPara.xml`) — starts at 0xe8d0, i.e. above the licence block** |
| 60001 | 120 | parameter / servo info |
| 1050 | 3 | program-id/version/time (read-only) |

The wire-reachable address windows (`readReg`, 04 §3.4) are `0..104, 150, 151, 1000..1100, 2000..3000, 5000..5008, 6000..51000, 59000+`. **Everything the port needs is inside these windows and none of it overlaps §3** except the deliberately-excluded 150/151 and 59500–59599. *(Verifier: those are the **readReg** windows. `writeReg` `0x10022760` is stricter — writes are silently dropped (return 1, no wire I/O) for `104<addr<5000` except 150/151, for `5008<addr<6000` and for `51000<addr<59000`; so status blocks 1000/2000 are write-dropped PC-side. Also: the 11000 ZF-property block is read-only for M1 — the vendor app commits FTC property writes with `150 ← 9999`, which the port denies.)*

---

## 6. "Can M1 jog without the exchange?" — answer + the one test that settles it

**Static answer (EVIDENCE, high confidence for the PC side; open for firmware):**
* PC side: **Irrelevant to the port, and not 'ungated' in the vendor app.** MainApp gates Start/frame/dry-run/forward/backward/breakpoint on `dogState==0` but not Home/Stop/Z/go-origin (§4). A port that connects, polls block 1000/2000, and writes `0x65 ← [3, slot, v, a, 10·a, ±d]` issues the same wire vector as the vendor app; the PC-side gate simply does not exist in the port.
* Firmware side: **Not determinable statically.** The jog/home rejections in the logs (`ErrCode:503`, exception 3; 08 §4.5) are **not** licence evidence — NCModule logs *only failures*, so successful jogs leave no line, and every logged rejection has an ordinary cause candidate (wrong argument units, soft-limit, not-homed). Nothing in firmware `MCC100_V201.52.mcf` is readable (encrypted, 04 §5).

**The single M1 bench step that settles it (do this and nothing else is needed):**
> On the real machine, with the port **never** writing registers 150, 151 or 59500–59599 (and never running any activation), after the normal connect/handshake (`0x65 ← [9999,13,…]`) issue **one** single-axis relative jog `WRITE 0x65 ← [3, 0, 20000, 5999, 59990, 200000]` (X, +? small move at safe speed) and then poll `READ 2000` (per-axis block).
> - If word 3 of the X axis slot (pulse position, A2) **changes** and no alarm bit sets in `READ 1000` word 6/7 → **the firmware does not gate motion on the licence; the port is viable without the exchange.**
> - If the write returns an exception and the position does **not** change while a jog with identical arguments succeeds after a licence is present → firmware gates motion (then the port must at least read, never write, the licence state; re-open O6).
>
> This is safe: a single small jog at configured speed, no laser (`DO9`/PWM never enabled), no FIFO, no licence-area access. Capture the UDP exchange (`tcpdump -i <nic> udp` on 10.1.1.x) to record the frames for the record.

Until that capture, R5 (licence viability, PORT-PLAN) stays **OPEN on the firmware half**; the old "jobs ran on Check-code-Error days" argument is weakened (§0). Note: the bench step should also run one FIFO start (`0x67 ← 2`) with the laser gate off, since the vendor's PC-side gate protects *job start*, not jog — a firmware gate, if any, may sit on the same action.

---

## 7. Cross-references / corrections

* ~~Confirms~~ **Contradicts** 99-gaps §2.4 (150 = access switch, values 5555/9999): runtime callers are the FTC dialog (verifier). Locates the data block 00 §5 row 15 left "addresses unknown": **59500–59511** (confirmed).
* Refines 04 §4.6 ("data-area addresses not recoverable statically") — they **are** recoverable: `0xe86c–0xe877`, RTC at `59502/59503` (150/151 removed by verifier).
* Refines 07 §8.1 — the `CheckUserID` "arg 9" call stores 9 into `CDog.state` (`[CNCModule+0x108]`), not a card write — but that state gates Start (§4).
* Refines 04/A1 FTC notes — FTC parameter writes to 11000+2·i are followed by `write 150 ← 9999`; FTC factory reset is `write 150 ← 5555` (`CZFStatusView` `0x5fce00`/`0x5fd7a0`).
* Consistent with A1 (motion commands) and A2 (status/alarm words); the DENY set is provably disjoint from every register A1/A2 use.

---

## Verification notes

Adversarial verifier, 2026-09-15. Re-derived from `MainApp.exe` / `Module/NCModule.dll` with `objdump -d -M intel` (listings `.scratch/asm/{MainApp.exe,NCModule.dll}.asm`) and a stdlib PE/vtable/RTTI resolver. Verdict per key fact of the first version:

1. **150 = encrypted-data-area access switch, 5555 open / 9999 close — REFUTED as stated (label), bytes CONFIRMED.** Thunk bytes `0x1002c530` (`68 b3 15 00 00 68 96 00 00 00`) and `0x1002c550` (`68 0f 27 00 00 68 96 00 00 00`) and CNC vtable `0x1008a1c4` (RTTI `.?AVCNCModule@@`) slots 61/62 confirmed. But (a) the call path is CNC slot 32 `0x1002b570` → VM slot 27 `0x100494e0` (single-word WRITE), not "VM `+0x80`"; (b) the Chinese group name is a bare MainApp literal (`0x7c5ac8`) with no address/value; (c) the only runtime callers are `CZFStatusView` FTC parameter write (`0x5fd267`, 9999) and FTC factory reset (`0x5fda16`, 5555, behind password `zf57` and confirm `zf58`). New inference (medium-high): 150 is an FTC command/key register.
2. **151 = encrypted data register — REFUTED/DOWNGRADED.** Read helper and special-casing confirmed; count is `5·min(ZFstatus[14],40)`; only runtime caller is the edge-seek debug dump in `CManuPanel` (`0x592641`, `fprintf "%d, %d, %d, %d, %d"`). No CDog reference. Meaning: buffered 5-word records (inference, medium).
3. **CDog block 59500–59511 — CONFIRMED with corrections.** Addresses and READ/WRITE constants confirmed; slot 106 is a **2-word WRITE** to 59502 (was "READ 1"), slot 107 is READ 2 (was "R/W"), slot 103 (READ 59500) was missing, counts for 59510/59511 are caller-supplied (0x100/0x20 not in code), extra callers `0x100010b0`/`0x10001d00` exist outside the stated CDog range, and `0x100036c0` is a string helper. 59600 base (`add …,0xe8d0` @`0x10050548`/`0x100508c6`) confirmed.
4. **MainApp never gates motion on licence state — REFUTED.** `[CManuPanel+0x1e8]` is the dog state (filled from CNC slot 170 → `CDog.state`); OnStartBtn, OnEmptyMoveBtn ×2, OnBoundingBtn, OnForwardBtn, OnBackwardBtn, OnBreakFindBtn, `0x59e630` and the watchdog `0x568a50` require it to be 0 (§4 table). `0x5904a0` is not the Start handler. Home/Stop/Z/go-origin handlers are ungated.
5. **"Jobs ran on Check-code-Error days, so PC side does not block" — DOWNGRADED to invalid reasoning.** The code shows a gate; the logs only imply `CDog.state` was 0 at those Starts.
6. **150 writers have no runtime caller — REFUTED.** Two virtual call sites via `CZFStatusView+0x1dc` (NCModule) found (`0x5fd267`, `0x5fda16`); slot 67 (151) is called at `0x592641`.

**O6 status after verification:** static half = "PC-side gate exists on job start / frame / dry-run / forward-backward / breakpoint-resume; not on jog-stop/home/Z/go-origin; the port does not inherit it". Firmware half still OPEN — one M1 bench step (§6): after connect, with 150/151/59500–59599 never touched, send one small relative jog `0x65 ← [3, …]` and one FIFO start `0x67 ← 2` on an empty/aux-only buffer with laser gate off; watch `READ 2000` position and `READ 1000` alarm/run words, capture UDP. Movement / run-state change ⇒ firmware does not gate.

**Deny-list safety verdict:** still safe (it is a superset); only its labels were wrong. Added reason to keep 150 denied: `150 ← 5555` is the vendor's FTC factory reset.

