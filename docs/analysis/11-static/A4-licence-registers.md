# A4 — Licence (CDog) registers: DENY-LIST for `mcc/safety.py`, ALLOW-LIST for the port, and "can M1 jog without the exchange?"

Task (scope = *protecting* the machine, never circumventing): the Linux port will **not** implement the card-licence exchange. It must therefore (1) never write any register the exchange touches, and (2) know whether the firmware refuses motion/FIFO when the exchange has not run. This note recovers, from `MainApp.exe` + `Module/NCModule.dll` alone, every register address / command vector the `CDog` licence path reads or writes and the order it uses them, and shows where `MainApp` uses `dogState` / `dogActiveReslut*` — to decide if gating is PC-side.

`SRC = /home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52` (read-only). Tooling: `objdump -d -M intel` (listings cached in `.scratch/asm/{NCModule.dll,MainApp.exe}.asm`), a stdlib PE/vtable resolver. **EVIDENCE** = read at the cited VA; **INFERENCE** (confidence) = interpretation.

Closes the static half of **O6** (07 §14, 08 §5, 99-gaps §2.4/§4 row O6). **This document contains no keygen, patch, or bypass — only the register set to avoid and read-only diagnostics.**

Register/func conventions (from 04 §3.2–3.5): a transaction is a word vector `[func, addr, count, …]`; `func 0x30` = READ, `func 0x40` = WRITE, `func 0x26` = firmware chunk. `CMCHalAPI::readReg` @`0x100225e0` / `writeReg` @`0x10022760` gate which addresses reach the wire.

---

## 0. Bottom line

* **The card licence lives in a dedicated register block 59500–59511 (`0xe86c`–`0xe877`) plus an access-switch at register 150 (`0x96`) and a data register 151 (`0x97`).** All of it is touched **only** by class `CDog` (`NCModule.dll`, RTTI `.?AVCDog@@` @`0x100a0008`) and its VM data-area helpers. None of it is on any motion, jog, home, FIFO or IO path. **CONFIRMED** (§1–§3).
* **MainApp never gates a motion action on the licence state.** `dogState_*` and `dogActiveReslut*` are consumed by exactly two code sites, both of which only build `CString` arrays for display / a message box (`0x56750a`, `0x4bbba0`). The Start (`0x5904a0`), Jog (`0x57a580`) and alarm-watchdog (`0x568a50`) paths contain no reference to the dog-state member or to the magic constant `0xb77be948`. **CONFIRMED** the PC-side enforcement is cosmetic (§4).
* Combined with 08 §5.4 (2950 jobs ran, including on days the log recorded a `Check code Error` at *every* start), the evidence says **jog/motion is not blocked PC-side**. Whether firmware V201.52 itself refuses commands when the data area is invalid **cannot be settled statically** — successes are never logged, so the 503/exception-3 jog rejections in the logs prove nothing either way. One bench step settles it (§6).
* Action for the port: **treat 150, 151 and 59500–59599 as write-forbidden and read-pointless; never touch the card RTC.** Everything the port needs (status, jog, home, go-to, IO/DA, FIFO) lives in a disjoint register set (§5).

---

## 1. The four single-word licence thunks (the grep target)

`grep 'push +0x9[67]$'` in `NCModule.dll.asm` → `0x1002c53d`, `0x1002c55d` (both `push 0x96`) and `0x1003ec63` (`mov …,0x97`). These sit in the thunk band `0x1002c510–0x1002c564`. EVIDENCE (`.scratch/asm/NCModule.dll.asm`):

```
1002c510: mov eax,[ecx]; mov edx,[eax+0x80]; push 0x65; push 0x65; call edx; ret   ; writeReg(reg=0x65, val=0x65)
1002c520: mov eax,[ecx]; mov edx,[eax+0x80]; push 0x66; push 0x65; call edx; ret   ; writeReg(reg=0x65, val=0x66)
1002c530: mov eax,[ecx]; mov edx,[eax+0x80]; push 0x15b3; push 0x96; call edx; ret ; writeReg(reg=150, val=5555)  ← ACCESS-SWITCH OPEN
1002c550: mov eax,[ecx]; mov edx,[eax+0x80]; push 0x270f; push 0x96; call edx; ret ; writeReg(reg=150, val=9999)  ← ACCESS-SWITCH CLOSE
1002c570: push ebp; call 0x10038a10; mov edx,[eax]; mov edx,[edx+0x204]; jmp edx   ; → VM slot 129 = readReg(151) (0x1003ebd0)
```

`+0x80` is the VM/HAL single-register write `writeReg(reg,value)`; calling convention is `(reg pushed second, value pushed first)`, confirmed by the 150-writers pushing `0x96` last. These five thunks are entries in the **`CNCModule` interface vtable** `0x1008a1c4` (208 slots, the object MainApp talks to):

| CNC vtable slot | offset | thunk | effect |
|---|---|---|---|
| 58 | +0xe8 | `0x1002c510` | write reg **0x65 ← 0x65** (single word; = the ZF-stop / re-sync one-shot, A1 row 17, 04 §3.6) |
| 59 | +0xec | `0x1002c520` | write reg **0x65 ← 0x66** (the "single-word `[0x66]` to 0x65", 08 §3.3, rejected exc 2 in logs) |
| **61** | **+0xf4** | `0x1002c530` | **write reg 150 ← 5555 — open encrypted-data-area access switch** |
| **62** | **+0xf8** | `0x1002c550` | **write reg 150 ← 9999 — close encrypted-data-area access switch** |
| 67 | +0x10c | `0x1002c570` | **read reg 151** (the encrypted data register), via `0x1003ebd0` |
| 37 | +0x94 | `0x1002c4f0` | generic `writeCmd` forwarder → VM vtable `+0x80` |

**5555 / 9999** are literally the "open / close" values of the group `lang.txt` calls **`加密数据域访问开关` [encrypted-data-area access switch]** (07 §6.3a). This confirms 99-gaps §2.4's inference exactly (there labelled "medium"): 150 is the access switch, 151 its data register.

**Caller check (EVIDENCE).** There is **no** `call` immediate to any of `0x1002c510/520/530/550/570` in either binary, and **no genuine `call [CNCModule+0xf4]` / `[+0xf8]`** on the motion path. (The two `call [reg+0xf4]`/`[+0xf8]` sites in MainApp at `0x48c486`/`0x48c9b8` are on the object cached at `MainApp this+0x478`, which is a *different* module — those calls pass FP arguments `[eax+0xc0/0xc8/0xd0/0xd8]`, whereas `0x1002c530` takes no arguments; false positive.) So in *this* build the 150 access-switch writers are exposed interface methods that the runtime UI never invokes on any traced path — they belong to the activation/manufacturing exchange, not to normal operation. The registers 150/151 are also **special-cased in `readReg`/`writeReg`** to be the *only* addresses in `(104,1000)` allowed to reach the wire (`0x1002260a`/`0x10022612` in readReg, `0x10022781`/`0x10022788` in writeReg) — further proof they are a deliberate side channel, not part of the normal register windows.

---

## 2. The `CDog` data-area block: 59500–59511 (`0xe86c`–`0xe877`)

`CDog` (functions `0x100036c0–0x100050b0`, all carrying the `Log\Code.txt` strings) reaches the card through a set of **VM data-area helpers** in `NCModule.dll` (VM vtable `0x1008bbc4`, called as `[VM_vtable + 0x1a0 … +0x1c8]`). Each helper builds a `[func, addr, count, …]` vector with a **hard-coded address** and calls `readReg`/`writeReg`. EVIDENCE (helper bodies + the `push 0x1008a66c` READ / `push 0x1008a670` WRITE constant, and the addr immediate):

| VM slot (off) | fn VA | addr (dec) | dir | count | Reached from (CDog fn → its log string) | Role (INFERENCE, high) |
|---|---|---|---|---|---|---|
| 102 (+0x198) | `0x1003ca10` | **59500** `0xe86c` | WRITE | 4 | init/verify path | verify / init write |
| 104 (+0x1a0) | `0x1003cc00` | **59501** `0xe86d` | READ | 4 | `0x10003840` → `verify data area failed!` | verify data area |
| 105 (+0x1a4) | `0x1003cd20` | **1050** `0x41a` | READ | 3 | `0x10003be0` | program-id/version/time triple (also read by normal poll, 04 §3.5) |
| 106 (+0x1a8) | `0x1003ceb0` | **59502** `0xe86e` | READ | 1 | clock path | card clock read |
| 107 (+0x1ac) | `0x1003cf60` | **59502** `0xe86e` | R/W | 2 | `0x10003b00` → `get card clock fail!!` | **card RTC read** |
| 108 (+0x1b0) | `0x1003d0f0` | **59510** `0xe876` | WRITE | 0x100 | `0x10003dc0` (reset routine) | user / data-area write |
| 109 (+0x1b4) | `0x1003d300` | **59510** `0xe876` | READ | 0x100 | `0x10004380` → `User data is error/was broken` | **user record read** |
| 110 (+0x1b8) | `0x1003d440` | **59511** `0xe877` | WRITE | 0x20 | `0x10003fc0` | admin / data-area write |
| 111 (+0x1bc) | `0x1003d650` | **59511** `0xe877` | READ | 0x20 | `0x100041c0` → `Admin data was broken / not current App data` | **admin (vendor) record read** |
| 113 (+0x1c4) | `0x10038e50` | **59503** `0xe86f` | READ | 1 | `0x100048c0` (Active) | **card RTC field** |
| 114 (+0x1c8) | `0x10038e80` | **59503** `0xe86f` | WRITE | 1 | `0x100048c0` (Active) → `-Active: reset clock OK` | **card RTC write (clock reset)** |
| 129 (+0x204) | `0x1003ebd0` | **151** `0x97` | READ | (n·5) | `0x1002c570` (CNC slot 67) | encrypted data register |

The `count 32` in the log line `Check code Error: … DataLen:32` (08 §5.1) matches a 32-byte record read from this block — the "32-byte data area" of 00 §5 row 15.

**Boundary (important, EVIDENCE).** The hardware-parameter area that `BkHardPara.xml` mirrors starts at **59600 = `0xe8d0`** (`readParamFromCard` "adds 0xE8D0", 04 verifier / A2). So the licence block **59500–59511 sits just below** the legitimate parameter area and is cleanly separable: **59500–59599 = licence; 59600+ = hardware parameters (allowed).**

**Exchange order (INFERENCE, high — from the caller topology of `0x10004540` "Key initial" and `0x100048c0` "Active"):**
1. read program-id/version/clock (`1050`, `59502`) → `checkMCStatus`;
2. read + verify data area (`59500/59501`), read admin record (`59511`) and user record (`59510`);
3. compare card clock (`59502/59503`) against `lastLicenseClock`; on activation, **write** the RTC (`59503`, slot 114) and data area (`59510/59511`, slots 108/110) → `-Active: reset clock OK`;
4. the access switch (reg 150 ← 5555 / ← 9999) brackets the privileged writes when that path is used.
No `Active OK` is written unless a valid code was entered (08 §5.3).

---

## 3. DENY-LIST for `mcc/safety.py`

The port must **refuse to emit** any of the following. Encode as an assertion in the register-write path (reject before framing):

```python
# --- LICENCE / CDog registers — the port MUST NEVER write these, and never
#     read the RTC/data area (reads are pointless and risk waking the exchange). ---
DENY_WRITE_REGS   = {0x96, 0x97}                     # 150 access switch, 151 data register
DENY_WRITE_RANGE  = range(0xe86c, 0xe8d0)            # 59500..59599 licence data area (RTC, admin, user, verify)
#   0xe86e/0xe86f (59502/59503) = card RTC          → NEVER write (clock is licence-critical)
#   0xe876/0xe877 (59510/59511) = user / admin recs → NEVER write
#   0xe86c/0xe86d (59500/59501) = verify/data area  → NEVER write
DENY_SWITCH_VALUES = {(0x96, 0x15b3), (0x96, 0x270f)}  # the 5555/9999 open/close pair — never emit
# Reads of 150/151/59500..59599 are also forbidden by policy: they serve only the exchange.
```

Rationale per entry (all EVIDENCE unless noted):
* **150 (`0x96`)** — access switch; writes 5555 (open) / 9999 (close). Thunks `0x1002c530`/`0x1002c550`, CNC slots 61/62. Do not write, do not read.
* **151 (`0x97`)** — encrypted data register; read via `0x1003ebd0`. Special-cased alongside 150 in `readReg`/`writeReg`. Do not touch.
* **59500–59511 (`0xe86c`–`0xe877`)** — the licence data block: RTC at 59502/59503, user record 59510, admin/vendor record 59511, verify/data 59500/59501. **Writing the RTC or data area is exactly the "reset clock" / "Active" operation the port must never perform** (would alter the card's stored licence state). Reserve the whole 59500–59599 span below the 59600 hardware-parameter base to be safe (INFERENCE, high — the observed touches are 59500–59511; the guard covers to 59599).
* **1050 (`0x41a`)** — read by CDog *and* by normal status polling (04 §3.5). Not a licence-only register; **read-only is fine, never write** (it is RO on the card anyway).

The port therefore never issues any activation code, never opens the access switch, and never writes the RTC or data area. The card's stored licence is left byte-for-byte untouched.

---

## 4. Where `dogState` / `dogActiveReslut*` gate the UI — they don't (evidence)

**Strings (MainApp `.rdata`, EVIDENCE):** `dogState_normal … dogState_NoHardware` at `0x841b20–0x841c80` (nine keys, contiguous); `dogActiveReslut1..11` at `0x7e8ae4–0x7e8c48` (contiguous), plus `ab10` at `0x7e8ab8`. No `ls*`/`ab1..9` present (matches 07 §6.3).

**Consumer #1 — start-up state display (`0x5674a0`, inside big-init `0x566ee0`, EVIDENCE):**
```
5674bb: mov eax,[ebp-0x680]         ; the main object
5674c1: mov ecx,[eax+0x1d8]         ; +0x1d8 = the NC (CNCModule) module
5674d7: mov eax,[edx+0x2b0]; call eax ; NC slot +0x2b0 → licence-state getter
5674e5: cmp DWORD PTR [ecx+0x1e8],0x4  ; if state member == 4 …
5674ee: cmp DWORD PTR [ebp-0x488],0xb77be948  ; …and magic matches → reset [+0x1e8]=0
56750a: push 0x841b20 …             ; build the dogState_* CString array (display only)
```
The magic `0xb77be948` occurs **exactly once** in the whole binary (here). After this the code only *constructs the nine `dogState_*` strings* into an array — no branch disables Start/Jog/Home/FIFO.

**Consumer #2 — activation-result message (`0x4bbba0…`, EVIDENCE):** pushes `dogActiveReslut1..11` (`0x7e8ae4…`) and `ab10` into an array feeding a dialog (`ds:0x7bf07c` = a MessageBox-family import). Display only.

**Consumer — `CheckUserID` (`0x566da0`, EVIDENCE, refines 07 §8.1):** reads the card user code via NC slot `+0x2c0 → [result+0x48]`, reads `ipAdd.ini [Soft]CheckUserID` (`0x566e4c`, default 0), and if `ini==0 ? u!=109 : ini!=u` calls NC slot `+0x2ac` with arg **9**. That slot (`0x100368d0`) is `mov [this+0x108],eax` — it just **stores the number 9 in a CNCModule field**; it is not a card write and not a motion gate.

**Motion paths carry no dog check (EVIDENCE):**
* Start button `CManuPanel::OnRunBtn` `0x5904a0`: calls the settings singleton `0x5ff1b0` repeatedly; **no** reference to `[+0x1e8]`, to the magic, or to any `dogState`/`dogActiveReslut` literal.
* Jog handler `0x57a580`: no `dogState`/dog-member reference.
* Alarm watchdog `CManuPanel` poll `0x568a50`: no `[+0x1e8]` reference (its stop trigger is alarm-word based, A2 §7).

(`[obj+0x1e8]` is a heavily-reused generic member — 231 hits across MainApp — not a dedicated dog gate; the *only* dog-related use is the single magic compare above.)

**Conclusion (CONFIRMED, static):** PC-side licence handling in this build is **display + a message box + a stored status number**. No motion, jog, home or FIFO action is conditioned on it. This matches 08 §5.4 (2950 jobs completed, many on days with a `Check code Error` at every start) and upgrades 99-gaps row O6's static half to **"PC-side gating is cosmetic — CONFIRMED."**

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

The wire-reachable address windows (`readReg`, 04 §3.4) are `0..104, 150, 151, 1000..1100, 2000..3000, 5000..5008, 6000..51000, 59000+`. **Everything the port needs is inside these windows and none of it overlaps §3** except the deliberately-excluded 150/151 and 59500–59599.

---

## 6. "Can M1 jog without the exchange?" — answer + the one test that settles it

**Static answer (EVIDENCE, high confidence for the PC side; open for firmware):**
* PC side: **Yes.** No PC-side code gates jog/home/start/FIFO on the licence state (§4). A port that connects, polls block 1000/2000, and writes `0x65 ← [3, slot, v, a, 10·a, ±d]` issues a byte-identical jog to the Windows app regardless of `dogState`.
* Firmware side: **Not determinable statically.** The jog/home rejections in the logs (`ErrCode:503`, exception 3; 08 §4.5) are **not** licence evidence — NCModule logs *only failures*, so successful jogs leave no line, and every logged rejection has an ordinary cause candidate (wrong argument units, soft-limit, not-homed). Nothing in firmware `MCC100_V201.52.mcf` is readable (encrypted, 04 §5).

**The single M1 bench step that settles it (do this and nothing else is needed):**
> On the real machine, with the port **never** writing registers 150, 151 or 59500–59599 (and never running any activation), after the normal connect/handshake (`0x65 ← [9999,13,…]`) issue **one** single-axis relative jog `WRITE 0x65 ← [3, 0, 20000, 5999, 59990, 200000]` (X, +? small move at safe speed) and then poll `READ 2000` (per-axis block).
> - If word 3 of the X axis slot (pulse position, A2) **changes** and no alarm bit sets in `READ 1000` word 6/7 → **the firmware does not gate motion on the licence; the port is viable without the exchange.**
> - If the write returns an exception and the position does **not** change while a jog with identical arguments succeeds after a licence is present → firmware gates motion (then the port must at least read, never write, the licence state; re-open O6).
>
> This is safe: a single small jog at configured speed, no laser (`DO9`/PWM never enabled), no FIFO, no licence-area access. Capture the UDP exchange (`tcpdump -i <nic> udp` on 10.1.1.x) to record the frames for the record.

Until that capture, R5 (licence viability, PORT-PLAN) stays **LIKELY-resolved (PC-side only)**, not CONFIRMED, on the firmware half.

---

## 7. Cross-references / corrections

* Confirms 99-gaps §2.4 (150 = access switch, values 5555/9999) and locates the data block it left "addresses unknown" (00 §5 row 15): **59500–59511**.
* Refines 04 §4.6 ("data-area addresses not recoverable statically") — they **are** recoverable: `0xe86c–0xe877`, `150/151`, RTC at `59502/59503`.
* Refines 07 §8.1 — the `CheckUserID` "arg 9" call is a field-store (`[this+0x108]=9`), not a card write.
* Consistent with A1 (motion commands) and A2 (status/alarm words); the DENY set is provably disjoint from every register A1/A2 use.
