# A8 — Wireless pendant (`PHBX.dll`, XHC PHB02) and USB hand-wheels (`CHidUsb`): HID protocol and MainApp consumption

Closes **O16** as far as static analysis can: the whole user-written part of `PHBX.dll` (`0x10001000–0x10002284`, everything after it is statically linked MFC 4.2 / MSVC 6 CRT) has been read instruction by instruction, together with every MainApp site that loads, polls or consumes it, and with MainApp's own `CHidUsb` readers for `RemoteType` 0/1/2.

Conventions: **EVIDENCE** = read from the binary at the cited VA (listings in `.scratch/asm/PHBX.asm`, `.scratch/asm/MainApp.asm`); **INFERENCE** = interpretation, with confidence. `SRC=/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52`. `g+X` = field of the settings singleton returned by `0x5ff1b0`. "Panel" = `CManuPanel` (`this+0x1d8` = CNCModule interface, A2 header). Byte offsets in HID buffers include the report-ID byte at `[0]`, as in Windows `ReadFile`/`HidD_GetFeature` and Linux `hidraw` `read()`/`HIDIOCGFEATURE`.

---

## 0. Summary for the Linux port

| Item | Value | Status |
|---|---|---|
| USB VID | `0x10CE` (Chengdu XHC) | EVIDENCE `0x10001848` |
| Accepted PIDs | 20 values `0xEB61…0xEBAA` (table §2.2); each gives a "model code" (500…980) | EVIDENCE `0x1000185a–0x100019da` |
| HID layout PHBX needs | two top-level collections: one with **InputReportByteLength = 6**, one with **FeatureReportByteLength = 8** | EVIDENCE `0x10001a09–0x10001a38` |
| Input report | `[0]=0x04 (ID) [1]=random [2]=key1 [3]=key2 [4]=selector (<0x1F) [5]=crc` | EVIDENCE (parse `0x100011d0`); names of [1]/[4] INFERENCE |
| Checksum | seed = **UTC day of month** sent to the device; `n=~r`; valid if `crc == ((key1+n) ^ (seed\|n))` **or** `crc == (((seed\|n)+n) ^ key1)` (8-bit) | EVIDENCE `0x1000136c–0x1000139a` |
| Feature report 6, host→device | `06 FE FD day FF FF 00 00` (set seed); `06 FE FD day LED 01 00 00` (outputs); `06 FA FB day fidL fidH 00 00` (write FID); `06 <7 bytes>…` (raw chunks) | EVIDENCE `0x10001020`, `0x10001de0/0x10001e90`, `0x100021b0`, `0x10002060` |
| Feature report 6, device→host | `[1..2]` dev-id (LE16), `[3]` channel-hi, `[4]` channel-lo, `[5]` RSSI (≥ 80 ⇒ "poor signal"), `[6..7]` FID (LE16) | EVIDENCE `0x10001120`; meanings from the export names, INFERENCE high |
| Key word handed to MainApp | `lParam = key1 \| key2<<8 \| selector<<16`, `wParam = 0x100` (keys down) / `0x101` (no key), message `0x4B1` | EVIDENCE `0x10001316–0x100013fa` |
| Status codes (XGetInput return) | 0 ok · 0x64 not open · 0x66 closing/closed · 0x68 link lost/idle · 0x69 asleep · 0x6A poor signal | EVIDENCE §3.4; MainApp texts §5.1 |
| MainApp key semantics | 19 codes: jog X±/Y± hold, Z± hold, Fn (0x0B) layer, start/pause/stop/frame/home, laser/red-pointer/gas/follow toggles, fast-mode/step-mode toggles, lifting table, ZF calibration | EVIDENCE §5.2 |
| Hand-wheel alternative (`RemoteType` 0/1) | `CHidUsb` VID **0x1000** / PID **0x2016**; report carries 32-bit key mask + five 32/24-bit pairing IDs | EVIDENCE §6 |
| "CypCut" receiver (`RemoteType` 2) | VID **0x6125** / PID **0x2012**; report `01 BC 55 k3 k2 k1 k0 …` (BE32 key mask) | EVIDENCE §6.4 |

Corrections to earlier docs: **01 §2.9** says MainApp's HID code uses "VID 2016, PID 6125/2012" — wrong; the pairs are VID_1000&PID_2016 and VID_6125&PID_2012 (§6.1). **07 §8.3** has the pairs right but the selection inverted ("value 1 or 2 → 1000/2016, otherwise 6125/2012"): the code at `0x59b0b3–0x59b101` gives 1000/2016 for 0, 1 and any other value, and 6125/2012 only for 2 (verifier). 01 §8 (bullet "`PHBX.dll`, XHC PHB02, USB-HID VID 0x3689 PID 0x8762") is also wrong: PHBX accepts VID 0x10CE only. **00 §4 row 12** attributes the `0xBC/0x55` report signature to the hand-wheel of `RemoteType` 1/2 — it belongs to `RemoteType` 2 only; `RemoteType` 0/1 reports have no signature and are filtered by pairing IDs (§6.3).

---

## 1. `PHBX.dll` identity and layout (EVIDENCE)

* PE32 DLL, linker 6.0, timestamp 2025-06-05 08:24:24, image base `0x10000000`; version resource `CompanyName "chengdu XHC Tec."`, `FileDescription/InternalName "PHB02"`, `FileVersion 6,3,1,5`, `LegalCopyright (C) 2018`, `OriginalFilename PHBX.dll` (`strings -e l`).
* Imports used by the user code: `HID.DLL` `HidD_SetFeature` (thunk `0x10002260`), `HidD_GetFeature` (`0x10002266`), `HidP_GetCaps` (`0x1000226c`), `HidD_GetPreparsedData` (`0x10002272`), `HidD_GetAttributes` (`0x10002278`), `HidD_GetHidGuid` (`0x1000227e`); `SETUPAPI` `SetupDiGetClassDevsA/EnumDeviceInterfaces/GetDeviceInterfaceDetailA/DestroyDeviceInfoList`; `KERNEL32` `CreateFileA ReadFile CancelIo CloseHandle CreateThread ResumeThread TerminateThread Sleep GetTickCount GetSystemTime GetLastError`; `USER32` `PostMessageA`. No `HidD_FreePreparsedData` import (the preparsed data is leaked per open).
* Exports (RVA): `SetGetKeyCallbackFunction 0x1010`, `XGetInput 0x14a0`, `Xinit 0x1560`, `XClose 0x1640`, `XOpen 0x1710`, `XSendOutput 0x1e90`, `XGetVersion 0x2000`, `XGetDevRssi 0x2010`, `XGetDevID 0x2020`, `XGetFID 0x2030`, `XGetDevTxstate 0x2040`, `XGetChannel 0x2050`, `XWritDev 0x2060`, `XWritFid 0x21b0` (names↔ordinals from `objdump -p`). All are `__stdcall`.
* Internal functions: `0x10001020` set-seed, `0x100010a0` debug profile write, `0x10001120` get-feature, `0x100011d0` read-one-report, `0x10001b70` reader thread, `0x10001c10` link-monitor thread, `0x10001de0` output-packet builder, `0x10002284` DllMain (MFC).
* Globals (all in `.data`):

| VA | meaning |
|---|---|
| `0x10019610` | input-collection handle (−1 = none) |
| `0x10019614` | feature-collection handle (−1 = none) |
| `0x10019618` | SetupDi device-info set |
| `0x1001960c`/`0x1001963c` | reader thread handle / id |
| `0x10019608`/`0x10019604` | monitor thread handle / id |
| `0x1001964c` | target HWND (arg of `XOpen`) |
| `0x10019644` | "closing" flag (set by `XClose`) |
| `0x100196a8` | key callback (from `SetGetKeyCallbackFunction`) |
| `0x10019690…97` | input buffer (6 bytes read; `…96/97` never filled) |
| `0x1001969c…9f` | copy of input bytes [2..5] (after 0xFF squashing) |
| `0x100196a4` | **key state word** returned by `XGetInput` |
| `0x100195f8` | model code from PID (default 500 = 0x1F4) → `XGetVersion` |
| `0x100195f0` | feature [2]<<8\|[1] → `XGetDevID` |
| `0x100195ea`/`0x100195eb` | feature [3] / [4] → `XGetChannel` = [3]<<8\|[4] |
| `0x100195f4` | feature [5] → `XGetDevRssi` |
| `0x100195ec` | feature [7]<<8\|[6] → `XGetFID` |
| `0x100195e9` | only ever written 0 (`Xinit` `0x100015c2`) → `XGetDevTxstate` always returns 0 after a feature refresh |
| `0x100195e8` | link state: 0 online, 1 lost/absent, 2 asleep |
| `0x10019603`/`0x10019602` | report counter / monitor's last-seen counter |
| `0x10019601` | monitor tick count since last report (80 ms ticks) |
| `0x100195fd` | "a key is currently held" |
| `0x100195e7`/`0x100195e6` | "key activity since last silence check" / "silence classified as sleep" |
| `0x100195e0` | GetTickCount at last key release |
| `0x100195e4` | consecutive reports with selector byte = 0 |
| `0x100194b0` | GetTickCount of last feature refresh / output |

---

## 2. Enumeration and open — `XOpen(HWND)` `0x10001710` (EVIDENCE)

### 2.1 Flow
1. If the feature handle is already valid → return 0 (`0x10001720–0x10001730`). If `hwnd == NULL` → return **0x67** (`0x10001740`).
2. `HidD_GetHidGuid` → `SetupDiGetClassDevsA(guid, 0, 0, 0x12 = DIGCF_PRESENT|DIGCF_DEVICEINTERFACE)`; iterate up to **80** interface indices (`cmp ebp,0x50` `0x100017bf`).
3. For each: `SetupDiGetDeviceInterfaceDetailA` (1024-byte buffer), `CreateFileA(path, GENERIC_READ (0x80000000), FILE_SHARE_READ|WRITE, NULL, OPEN_EXISTING, 0, NULL)` — synchronous, read-only (`0x10001811–0x10001824`).
4. `HidD_GetAttributes`; require `VendorID == 0x10CE` (`cmp WORD PTR [esp+0x1c],0x10ce` `0x10001848`) and a PID from §2.2, else close.
5. `HidD_GetPreparsedData`, `HidP_GetCaps` must return `0x110000` (`HIDP_STATUS_SUCCESS`, `0x10001a02`). With caps at `esp+0x50`: if **`FeatureReportByteLength` (`+8`) == 8** → this handle becomes the **feature handle**; else if **`InputReportByteLength` (`+4`) == 6** → the **input handle**; otherwise close (`0x10001a09–0x10001a53`). Stop once two matching collections are found.
6. After the loop: if no input handle → return **0x64**. Else clear link state, `0x10001120` (read feature report), start the reader thread (if none; `CreateThread(…CREATE_SUSPENDED…)` + `ResumeThread`), `0x10001020` (send seed), start the monitor thread (if none), return **0**.

INFERENCE (high): because the feature length is tested *before* the input length and each handle is assigned to only one role, PHBX works only with a receiver whose report descriptor has the input report and the feature report in **separate top-level collections** (Windows creates one device interface per collection). On Linux, `hidraw` is per USB *interface*, so both reports may arrive on **one** `/dev/hidrawN` — the port must not copy the "two handles" logic blindly. Residual check R1 (§8).

### 2.2 PID → model code (`XGetVersion`)
EVIDENCE `0x1000185a–0x100019da` (value stored in `0x100195f8`):

| PID | code | PID | code | PID | code | PID | code |
|---|---|---|---|---|---|---|---|
| 0xEB68 | 515 (0x203) | 0xEB6F | 500 (0x1F4) | 0xEB6D | 621 (0x26D) | 0xEBA0 | 901 (0x385) |
| 0xEB62 | 512 (0x200) | 0xEB66 | 600 (0x258) | 0xEB64 | 622 (0x26E) | 0xEBAA | 960 (0x3C0) |
| 0xEB8C | 700 (0x2BC) | 0xEB7D | 610 (0x262) | 0xEB65 | 611 (0x263) | 0xEB88 | 980 (0x3D4) |
| 0xEB7C | 510 (0x1FE) | 0xEB7E | **620 (0x26C)** | 0xEB6C | 501 (0x1F5) | 0xEBA1 | 961 (0x3C1) |
| 0xEB61 | 511 (0x1FF) | 0xEB63 | 513 (0x201) | 0xEB9A | 900 (0x384) | 0xEB87 | 910 (0x38E) |

Only model **620** changes behaviour (output bit remap, §4.2). None of these PIDs is `0xEB93` (WHB04B-6 in LinuxCNC `xhc-whb04b-6/usb.h`: `usbVendorId{0x10ce}`, `usbProductId{0xeb93}`), consistent with a different product family member. Which PID the fitted PHB02 receiver uses is unknown statically (R1).

### 2.3 `Xinit()` `0x10001560`, `XClose()` `0x10001640`
* `Xinit`: zeroes all state, model code := 500, both handles and both thread handles := −1, records GetTickCount in `0x100194b0/0x100195e0`; returns −1 (unused by MainApp).
* `XClose`: closing flag := 1; waits up to 20×100 ms for the reader thread to clear its id, else `TerminateThread`; `TerminateThread` on the monitor thread; closes both HID handles and sets them to −1. **Verifier correction:** after `XClose` has returned, `XGetInput` returns **0x64**, not 0x66, because the `hIn == −1` test (`0x100014a0`) precedes the closing-flag test (`0x100014c0`). 0x66 is only seen (a) while `XClose` is still waiting for the reader (handles not yet closed), or (b) after a *forced* `TerminateThread` of the reader followed by `XOpen` without `Xinit`: the reader id `0x1001963c` is then never cleared (only the reader's own exit `0x10001bf8` and `Xinit` clear it), so `XOpen` creates no reader (`0x10001abf`), the closing flag is never cleared (`0x10001ad8` is inside the create branch) and `XGetInput` returns 0x66 with no key input.

---

## 3. Input path

### 3.1 Reader thread `0x10001b70` (EVIDENCE)
Loop while closing flag = 0: if an input handle exists call `0x100011d0`; if that returns 0 (hard read error) the handles are closed and, unless `0x100195fe` (never set non-zero), `PostMessageA(hwnd, 0x4B2, 0, 0)`. If no input handle: `Sleep(400)` then `XOpen(hwnd)` — **PHBX re-enumerates by itself** after an unplug.

### 3.2 One report — `0x100011d0` (EVIDENCE)
```
buf[0..5] := 04 00 00 00 00 00 ; ReadFile(hIn, buf, 6, &n, NULL)          0x10001200-0x10001224
if ReadFile fails: GetLastError in {997,996,1453} and feature handle valid -> return 1 (retry)
                   else close both handles, return 0                          0x10001421-0x10001491
if now - lastRefresh > 1000 ms: lastRefresh = now; getFeature()               0x10001233-0x1000124f
if n < 6: goto count                                                           0x10001254
GetSystemTime(&st)            ; st.wDay used below (UTC!)                      0x10001264
state &= 0x00FF0000           ; keep selector byte                             0x1000127c
k1 = buf[2]; k2 = buf[3]; sel = buf[4]; crc = buf[5]
if k1 == 0 && k2 == 0:                                                         0x10001299-0x100012c0
    wParam = 0x101
    if keyHeld: tRelease = GetTickCount();  keyHeld = 0
else:
    wParam = 0x100
    if k1 == 0xFF: k1 = 0     (also in the copy used by the checksum)          0x100012cb-0x100012d9
    if k2 == 0xFF: k2 = 0                                                      0x100012f0-0x100012fa
    state |= k1 | k2<<8 ; keyHeld = 1 ; keyActivity = 1                        0x10001308-0x10001325
if sel != 0:
    if sel < 0x1F: state = (state & 0xFFFF) | sel<<16 ; zeroCnt = 0            0x1000132d-0x1000134e
    (sel >= 0x1F: ignored, previous selector kept)
else:
    if ++zeroCnt >= 2: state &= 0xFFFF                                         0x10001350-0x10001366
n1 = ~buf[1]
ok = (((k1 + n1) ^ (wDay | n1)) & 0xFF) == crc                                 0x1000136c-0x1000138c
  || ((((wDay | n1) + n1) ^ k1) & 0xFF) == crc                                 0x1000138e-0x1000139a
if ok:  linkState = 0
        PostMessageA(hwnd, 0x4B1, wParam, state)                                0x100013e4-0x100013fa
        keyEventFlag = 1 ; if callback: callback(state)   (cdecl)             0x10001400-0x1000141c
else:   if reportCounter % 3 == 0 && setSeed() == 0: seedResent = 1            0x1000139c-0x100013b8
count:  reportCounter++ ; connected = 1 ; return 1                             0x100013c1-0x100013e3
```
Notes (EVIDENCE): `buf[0]` is preset to 4 but **never checked**; `buf[3]` and `buf[4]` are not covered by the checksum; invalid reports are not *posted* but still count as "radio alive" for the monitor thread (§3.3); `k1` used in the checksum is the value *after* the 0xFF→0 squash. Verifier additions: (1) all state updates (`0x100196a4` state word, `keyHeld 0x100195fd`, `tRelease 0x100195e0`, `keyActivity 0x100195e7`, selector debounce) happen at `0x1000127c–0x10001366`, **before** the checksum test at `0x1000136c`, so a report with a bad checksum still changes the word `XGetInput` returns and arms the monitor's synthetic key-up; only `PostMessage(0x4B1)` and the callback are suppressed. (2) The 0xFF squash sits inside the "not both zero" branch, so `k1=k2=0xFF` yields `wParam=0x100` with key bytes 0. (3) The dead compare `cmp al,0xa5` after `cmp al,0xff; jb` (`0x100012cf`, `0x100012f4`) can never match.

INFERENCE (high): the layout matches the XHC family convention documented in LinuxCNC `xhc-whb04b-6/usb.h` `class UsbInPackage { header(0x04), randomByte, buttonKeyCode1, buttonKeyCode2, rotaryButtonFeedKeyCode, rotaryButtonAxisKeyCode, int8 stepCount, crc }` (8 bytes) — PHB02 is the 6-byte variant **without jog-wheel (`stepCount`) and with a single rotary/selector byte**. The host "seed" is the third byte of the `FE FD <seed>` output header in both products (LinuxCNC `UsbOutPackageData::clear()` `header = 0xfdfe; seed = 0xfe`); XHC's CRC there is `rnd − (key ^ (~seed & rnd))`, a different but related 8-bit mix. LinuxCNC's comment "checksum generator not clear yet … works with seed 0xfe and 0xff" is consistent with PHBX accepting *two* formulas. Whether `buf[4]` is a rotary knob, a mode switch or battery level is not determinable statically; MainApp ignores it (§5.2). `key1/key2` = up to two simultaneously held keys (like WHB04B).

### 3.3 Link monitor thread `0x10001c10` (EVIDENCE)
80 ms loop (`push 0x50; call Sleep`). If the report counter changed since last tick: tick := 0, linkState := 0. Otherwise tick++ and:

| condition | action | effect on `XGetInput` |
|---|---|---|
| no input handle and `tick % 20 == 0` (1.6 s) | `PostMessage(0x4B2,0,0)`, state &= 0xFF0000, linkState := 1 | 0x68 |
| key held and tick ≥ 13 (~1.04 s without reports) | synthetic key-up: state &= 0xFF0000, `PostMessage(0x4B1, 0x101, state)` — posted on ticks 13, 14 **and** 15 (three times) | — |
| key held and tick ≥ 15 (1.2 s) | `PostMessage(0x4B3,0,0)`, keyHeld := 0, **tick := 0** (so the 3.2 s classification below restarts: first 0x4B3/0x4B4 at 4.4 s), linkState := 1, state &= 0xFF0000 | 0x68 |
| tick ≥ 40 (3.2 s silence) | if `keyActivity`: sleep := (`now − tRelease` ∈ [22 000, 26 000] ms, measured **at detection time**, i.e. 3.2 s after the last report) (`0x10001cf3–0x10001d10`), keyActivity := 0; **if no key activity since the previous check the previous classification is kept** (sticky); then sleep ? (`PostMessage(0x4B4)`, linkState := 2) : (`PostMessage(0x4B3)`, linkState := 1); state &= 0xFF0000; tick := 0; repeats every further 3.2 s of silence. The `0x100010a0` call is **dead code**: it runs only if `[0x100195e5] != tick`, but tick was just zeroed and `0x100195e5` is only ever written 0 (`0x1000159a`, `0x10001d78`) | 0x69 / 0x68 |

INFERENCE (medium): the pendant transmits periodic reports while awake and stops transmitting ~19–23 s after the last key release (auto-sleep; the silence has to *begin* 18.8–22.8 s after release for the detection 3.2 s later to fall in PHBX's 22–26 s window); PHBX classifies such a silence as "asleep" (2), any other silence as "link lost" (1).

**Verifier — SAFETY (EVIDENCE):** the synthetic key-up exists only in the `hIn != −1` branch (`0x10001c6f je 0x10001d91`). If the receiver is unplugged (or `ReadFile` fails with any error other than 996/997/1453), `0x100011d0` closes both handles (`0x10001444–0x10001483`), the reader posts only `0x4B2` (`0x10001bcc–0x10001bdc`), the monitor's no-handle branch posts only `0x4B2` every 20 ticks (`0x10001d91–0x10001dc4`), and MainApp maps `0x4B2` to the no-op `0x57b680` and ignores `XGetInput` 0x64. **No `0x4B1` key-up is ever posted, so a jog started by a held key is never stopped by the pendant path** (INFERENCE high that MainApp has no other watchdog for it — none in `0x57a580`; whether the card itself times out continuous jogs is not known). The Linux port must stop all pendant-initiated motion on device loss. `0x100010a0` formats `"%d"` (`0x100160b0`) and calls MFC `CWinApp::WriteProfileString("xhcdev" 0x100160a0, "type" 0x100160a8, …)` — a debug trace (INFERENCE high; for an MFC DLL without registry key this goes to a private INI).

### 3.4 `XGetInput(DWORD *key, BYTE *len)` `0x100014a0` (EVIDENCE)

| check (in order) | `*key` | `*len` | return |
|---|---|---|---|
| input handle invalid | not written (state &= 0xFF0000) | — | **0x64** (100) |
| closing flag set | not written (state &= 0xFF0000) | — | **0x66** (102) |
| linkState 0 | state word | 4 | **0**, or **0x6A** (106) if RSSI byte `[0x100195f4] ≥ 0x50` (`cmp ecx,0x50; setl al; dec eax; and eax,0x6a`, `0x10001507–0x1000150e`) |
| linkState 1 | 0 | 4 | **0x68** (104) |
| linkState 2 | 0 | 4 | **0x69** (105) |
| other | — | — | 0x66 |

Other codes: `XOpen` 0x67 (NULL hwnd), 0x64 (no device); `XSendOutput`/`XWritDev`/`XWritFid` 0x64 (cannot open), **0x65** (101, `HidD_SetFeature` failed); set-seed `0x10001020` returns 0/0x65 via `neg al; sbb eax,eax; and al,0x9b; add eax,0x65`.

### 3.5 `SetGetKeyCallbackFunction(fn)` `0x10001010`
Stores `fn` in `0x100196a8`; called as `fn(state)` (cdecl, `add esp,4` at `0x1000141c`) after every valid report. MainApp does not use it (no string `SetGetKeyCallbackFunction` in MainApp.exe).

---

## 4. Feature report 6 (host↔device)

### 4.1 Read — `0x10001120` (EVIDENCE)
`buf[0]=6; HidD_GetFeature(hFeat, buf, 8)`; on success:

| byte | stored | export | INFERENCE |
|---|---|---|---|
| [1],[2] | `0x100195f0 = [2]<<8 \| [1]` | `XGetDevID` | paired pendant's device/radio id |
| [3] | `0x100195ea` | `XGetChannel` high byte | radio channel |
| [4] | `0x100195eb` | `XGetChannel` low byte | radio channel / sub-channel |
| [5] | `0x100195f4` | `XGetDevRssi` | RSSI magnitude; ≥ 80 → "poor signal" (INFERENCE: −dBm) |
| [6],[7] | `0x100195ec = [7]<<8 \| [6]` | `XGetFID` | "frequency id"/pairing group, cf. `XWritFid` |

Called at open, every > 1 s from the reader while reports arrive, and by `XGetDevTxstate`.

Verifier: the reader's 1-s test uses `0x100194b0`, which `XSendOutput` also overwrites on every call (`0x10001ee5–0x10001eef`). MainApp calls `XSendOutput` from `CManuPanel::OnTimer` (`0x568a50`, message-map entry WM_TIMER `0x113` at `.data 0x939d58`; timer 1 set at `0x56558a–0x56559d` with period `g+0x3fcc` = `IGP.UIRefreshCycle`, default 30 ms, range 10–100). If that call is reached on most ticks (early exits in `0x568a50` not traced), the reader's refresh **never fires**, so dev-id/channel/RSSI — and the 0x6A "poor signal" decision — stay at the values read during `XOpen` (INFERENCE high).

### 4.2 Writes (all `HidD_SetFeature(hFeat, buf, 8)`, `buf[0]=6`; "day" = `GetSystemTime().wDay`, low byte — **UTC** day of month)

| function | bytes | EVIDENCE |
|---|---|---|
| set seed `0x10001020` (at open; and after a checksum mismatch when `counter % 3 == 0`) | `06 FE FD day FF FF 00 00` | `0x1000104c–0x10001082` |
| `XSendOutput(BYTE *data, BYTE *outLen)` `0x10001e90` → builder `0x10001de0` (if the feature handle is −1 it first calls **`XOpen`** itself, `0x10001eb3–0x10001ed6` — a full SetupDi enumeration per call; `XWritDev` `0x10002082` and `XWritFid` `0x100021cf` do the same) | `06 FE FD day d0 d1 00 00`; `*outLen := 5` (written after `d1` was read); for model 620 (PID 0xEB7E) `d0,d1` are remapped: `o3 = (d0&0x20)?0x40:0`; `o4 = (d0&1?0x02) \| (d0&2?0x40) \| (d0&4?0x10) \| (d0&0x10?0x08) \| (d0&0x80?0x80) \| (d1&0x10?0x20) \| (d1&1?0x01)` | `0x10001e36–0x10001e7b` |
| `XWritFid(WORD fid)` `0x100021b0` | `06 FA FB day fidL fidH 00 00` | `0x10002202–0x1000223d` |
| `XWritDev(BYTE *data, BYTE *len)` `0x10002060` | raw payload split into 7-byte chunks, each sent as `06 <7 bytes>` | `0x100020d6–0x10002192` |

If the payload is > 7 bytes the builder/XWritDev loop sends consecutive 8-byte feature reports (same framing as LinuxCNC `xhc_set_display()`: report ID 6, 7 data bytes, `FE FD 0C` header on the first block). The 5-byte PHB02 output never needs a second block.

---

## 5. MainApp side (RemoteType = 3)

### 5.1 Load, poll, status texts (EVIDENCE)
* `SOP.RemoteType` = `g+0x4028` (descriptor `0x785875`, 01 §2.9); the defaults initialiser writes 3 (`0x4b1d5a`). `0x498ed6 cmp [g+0x4028],3` → `0x586e70`: `LoadLibraryW(L"PHBX.dll")` (handle `0x9e4260`), `Xinit()`, `XOpen(panel->m_hWnd = [this+0x20])`; success → `[this+0x2270] := 1`.
* Pendant thread start `0x597ce0` switches on `g+0x4028`: 0,1 → `0x599a80`; 2 → `0x59a010`; 3 → `0x599380` (jump table `0x597d2c`).
* `0x599380` = **status poller only** (keys arrive by window message, §5.2). It calls `XGetInput(&key,&len)` and acts only when the return code **changes**. **Verifier correction:** it is *not* a 200 ms poll — when the code is unchanged, `0x599510 jne / 0x599512 jmp 0x5993bb` loops straight back to `GetProcAddress`+`XGetInput` **without sleeping** (busy loop, one core); `Sleep(200)` (`0x599a4f`) runs only after a change or when `GetProcAddress` fails:

| code | MainApp action | text (`lang.txt`) |
|---|---|---|
| 0x66 | show `A250613_1`; if `[this+0x2270]==1` → `XClose`, flag := 0; else `XOpen(hwnd)` timed with log `"Open devive time:%d"` | 手柄断开 "The handle is disconnected" |
| 0x68 | show `A250613_4` | 手柄 休眠 "Handle Dormant" |
| 0x6A | show `A250613_3` | 手柄信号差 "Poor signal of the handle" |
| 0 | show `A250613_5` | 手柄 唤醒 "Handle Rouse" |
| 0x64, 0x69 | nothing (code remembered) | — |
| PHBX not loaded | shows `A250613_0` and loops **without sleeping** (`0x5993d0–0x5994c1`) | 手柄模块加载成功 "module loaded" |

`A250613_2` "low battery" (`MainApp.exe` file offset 0x441714, VA `0x842914`) is shown by the `CManuPanel` handler for **WM 0x4B5** (`0x57b690`: `push 0x842914` at `0x57b6bb`, lang lookup, `0x5b6dd0` status display; map entry `0x564a2e`). This PHBX build never posts 0x4B5 (its only message constants are 0x4B1–0x4B4, `PHBX.asm` grep) and no `push 0x4b5` exists in MainApp, so it is never raised with this DLL; a later PHBX may post it (verifier). Because MainApp maps 0x68 to "dormant" and ignores 0x69, the user sees "dormant/rouse" for ordinary radio silence (INFERENCE high). An unplug produces 0x64 (silent) and PHBX's own re-enumeration (§3.1), not "disconnected".
* `XSendOutput` is called from the `CManuPanel` poll routine `0x568a50` (call `0x56ca53`, `GetProcAddress` `0x56c979` every cycle). Argument: `data = {flags, 0x01}` — `flags` built at `0x56c992–0x56ca45` from the panel's 5 indicator bytes `[panel+0x1e38..0x1e3c]` computed in the loop `0x56c1d6–0x56c968`: bit4 ← indicator 0 (`[panel+0x1e78]==1`, ZF follow run state, A2 §6), bit1 ← indicator 1, bit5 ← indicator 2 (laser gate state `g+0x49a5` fibre / `g+0x49a6` CO2, A5 §5), bit6 ← indicator 3, bit0 ← indicator 4 (`[panel+0x1ed5]` hold-fire flag / laser DA state); `data[1]` = the adjacent stack byte `[ebp-0xa01]` = 1. Verifier: the call is `XSendOutput(&flags, &len)` with `len` = `[ebp-0xa01]` := 1 (`0x56c992`), i.e. MainApp passes a one-byte payload and a length pointer, while PHBX reads two bytes from `data` — the `01` in byte 5 is this length byte, not an intended output bit. Indicator 3 (bit 6) is computed from `[panel+0x1ed6]`/NC slot 82 on `g+0x94` or `g+0x864` (`0x56c5a3–0x56c69d`), plausibly the gas output (low). INFERENCE (medium): these are pendant **LEDs** (follow / gas? / laser / red-pointer? / emission); indicators 1 and 3 read DO/DI states through NC calls not traced here (low).

### 5.2 Key handler — `WM_USER+0xB1` (0x4B1) → `0x57a580` (EVIDENCE)
Message map `CManuPanel` (dynamic init `0x56493e`): `0x4B1 → 0x57a580`; `0x4B2`, `0x4B3`, `0x4B4 → 0x57b680` (returns 0, i.e. ignored).

Decode: returns immediately if `lParam` equals the previous `lParam` (`0x9e81b4`). Verifier: `0x9e81b4` is a function-static initialised on the first call to that call's own `lParam` (`0x57a5b4–0x57a5d0`), so **the first 0x4B1 message after start-up is always discarded**. `k1 = lParam & 0xFF`, `k2 = (lParam>>8) & 0xFF`; `wParam` is ignored. Primary code `c = k1`; if both non-zero, `c = (k1 == 0x0B) ? k2 : k1` (`0x57a6eb–0x57a733`); if `k1 == 0` and `k2 != 0`, `c = 0` (no button action), although the jog flags X±/Y± test membership in *either* byte (`0x57a638–0x57a6e1`). `g+0x4781` ("Fn held") := (k1==0x0B || k2==0x0B).

Per-report pre-processing: if hold-fire active (`[panel+0x1ed5]`) and `c != 4` → stop it (`0x57ea30(0)`); if Fn-lifting jog active (`+0x21b2`) → stop via `0x57e5b0`; if Z jog active (`+0x21b0`) and machine not in state 4 and `ZF.ZFType ≠ 0` → NC slot 58 (`[101]`, A1 row 17); for each of X+/X−/Y+/Y− whose key is no longer present → per-axis stop `0x65 ← [1, 1<<slot, 2, vd, 10·vd]` via NC slot 31 (A1 row 3; skipped when `MC.IsStepMove` `g+0x4770` is set).

| code | Fn not held | Fn (0x0B) held | EVIDENCE |
|---|---|---|---|
| 0x01 | `OnZFBtn` `0x57d950` (height-follower toggle) | same | table `0x57b638[0]` |
| 0x02 | laser gate toggle `0x57e880` (NC slot 88 on / 89 off; only in panel state 0 or 5) | same | |
| 0x03 | red pointer toggle `0x57e950` (NC slot 93/94) | same | |
| 0x04 | hold-to-fire: NC slot 90 `(1,0)` `0x57e9d0`, stopped when another report arrives (`0x57ea30`) | same | |
| 0x05 | `OnGasBtn` `0x57e610` | same | |
| 0x06 | `PostMessage(0x405)` → `OnBoundingBtn` `0x589070` (trace frame) | same | |
| 0x07 | — | `OnHomeBtn` `0x58a100` (via `0x58a980`) | |
| 0x08 | `PostMessage(0x403)` → `OnStartBtn` `0x57ef50` | same | |
| 0x09 | pause/continue `0x587a30` | same | |
| 0x0A | `OnStopBtn` `0x5880a0` | same | |
| 0x0B | **Fn modifier** | — | `0x57a605` |
| 0x0C | — | ZF floating-head calibration (`0x59cdd0` → `0x5f76c0`, texts `zf26`/`zf49`) | |
| 0x0D | toggle `MC.IsFastMode` `g+0x4782` + UI `0x58a9a0` | toggle `MC.IsStepMove` `g+0x4770` | labels `0x8429e0 "OnXSubBtn IsStepMove: "`, `0x8429c4 " IsFastMode: "` |
| 0x0E | Z− hold `OnZSubBtn` `0x57d570` (if NC slot 140 = idle) | lifting table down `0x57dc70(0)` (A1 row 10), only if `g+0x47cc` ∉ {3,7,8,9,0x0B,0x10,0x11} (`0x57a77e–0x57a7fc`, flag `[ebp-0x11]`) | `0x57b4d6` |
| 0x0F | **Y+** hold: NC slot 21 `(1,1)` | same | `0x57b299–0x57b2cb` |
| 0x10 | Z+ hold `OnZPlusBtn` `0x57d760` | lifting table up `0x57dc70(1)`, same `g+0x47cc` gate | `0x57b57b` |
| 0x11 | **X−** hold: NC slot 21 `(0,0)` | same | `0x57b250–0x57b282` |
| 0x12 | **Y−** hold: NC slot 21 `(1,0)` | same | `0x57b2e2–0x57b314` |
| 0x13 | **X+** hold: NC slot 21 `(0,1)` | same | `0x57b207–0x57b239` |

Jog details (speed from `g+0x4788`/`0x4790` by IsFastMode, stop deceleration, soft-limit distance) are in A1 rows 1–3. The key numbering is **device specific**: WHB04B-6 uses 0x0C for Fn and 0x01..0x10 for reset/stop/start/feed/spindle/home… (LinuxCNC `KeyCodes`), so the physical PHB02 legend must be matched by capture (R3). The pairing dialog text `jm1` "press Left and Right simultaneously" belongs to the `CHidUsb` path (§6.3), not PHBX (PHBX has no pairing call in MainApp; `XWritFid/XGetFID` are unused).

### 5.3 CManuPanel user-message map (EVIDENCE, dynamic init `0x5646xx–0x564a60`, 24-byte `AFX_MSGMAP_ENTRY`)
`0x401→0x58fb20`, `0x403→0x58fb80 (OnStartBtn 0x57ef50)`, `0x404→0x58fba0 (OnEmptyMoveBtn 0x583270)`, `0x405→0x58fbc0 (OnBoundingBtn 0x589070)`, `0x406→0x58fbe0 (E-stop, mp124)`, `0x407→0x58fce0 (pause 0x587a30)`, `0x408→0x58fd00 (OnStopBtn 0x5880a0)`, `0x409→0x58fd20 (OnPlatStartExchangeBtn 0x58ca40)`, `0x40a→0x58fd40 (0x58d5c0)`, `0x40b→0x58fd60 (0x58c350)`, `0x40d→0x58fd80 (0x58d8d0(1))`, `0x40e→0x58fda0 (0x58d8d0(0))`, `0x410→0x59d740`, `0x411→0x59d760`, `0x412→0x59d9b0`, `0x40c→0x58fdc0 (mf800)`, `0x2b10→0x59ce20`, `0x2b11→0x59cff0`, `0x2b12→0x59d530`, `0x4b1→0x57a580`, `0x4b4/0x4b2/0x4b3→0x57b680`, `0x4b5→0x57b690`. (Consistent with A1 §7, which lists the final targets; the wrappers `0x58fbxx/0x58fdxx` are added here.)

---

## 6. MainApp `CHidUsb` receivers (RemoteType 0, 1, 2)

### 6.1 Device selection (EVIDENCE `0x59b0b3–0x59b16b`)
`CHidUsb::CHidUsb(vidStr, pidStr)` `0x452230` (+0x28 = VID, +0x2c = PID). Defaults `L"1000"`/`L"2016"` (`0x843e14`/`0x843e20`); `RemoteType==1` → same (`0x843e2c`/`0x843e38`); `RemoteType==2` → `L"6125"`/`L"2012"` (`0x843e44`/`0x843e50`). The matcher `0x452310` finds `vid_` (case-insensitive byte compares `0x45234f–0x4523d6`) and compares the next 4 characters with `+0x28`, then `pid_` with `+0x2c` (`0x452463–0x452549`). ⇒ **VID 0x1000 / PID 0x2016** (types 0 and 1), **VID 0x6125 / PID 0x2012** (type 2).

### 6.2 Open/read (EVIDENCE `0x4526aa–0x452a64`)
Path = device-interface path + `L"\\"` (`0x7d0f88`) + `L"PIPE_00"` (`0x7d0f8c`); `CreateFileW(GENERIC_READ|GENERIC_WRITE, share 3, OPEN_EXISTING, overlapped? FILE_FLAG_OVERLAPPED : FILE_ATTRIBUTE_NORMAL)`; `HidP_GetCaps` → `InputReportByteLength` stored at `+0x18`; `HidP_GetSpecificValueCaps` probe; read = `ReadFile(len)` + `WaitForSingleObject(3000 ms)` + `GetOverlappedResult`, success only if exactly `len` bytes. INFERENCE (medium): the `\PIPE_00` suffix is inherited from a vendor USB-bulk driver template; whether Windows' HID class accepts it is not provable statically (it evidently did for the vendor).

### 6.3 RemoteType 0/1 report (reader `0x599a80`, EVIDENCE)
Every 10 ms; only when `[panel+0x21d1]` (enabled), else sleep 500 ms. Buffer `b` (report ID at `b[0]`):

| bytes | field | use |
|---|---|---|
| `b[13..16]` LE32 | key bit mask | `0x599b38–0x599b74` |
| `b[9..12]` LE32 | id1 | must equal `SOP.JoystickID1` `g+0x4030` (descriptor push `0x880ba8`) |
| `b[5..8]` LE32 | id2 | `g+0x4034` |
| `b[1..4]` LE32 | id3 | `g+0x4038` |
| `b[29..31]` LE24 | id4 | `SOP.JoystickID4` `g+0x403c` (descriptor `0x8802f8`; machine value 1 601 441 < 2²⁴ — consistent) |
| `b[25..28]` LE32 | id5 | `SOP.JoystickID5` `g+0x4040` (`0x8802ac`) |

⇒ input report ≥ 32 bytes. Pairing mode (`g+0x4058` set by the `jm` dialog): when mask == **0x00020002** (bits 1 and 17 = the X− and X+ keys, matching `jm1` "press Left and Right simultaneously") the five IDs are copied to `g+0x4044..0x4054` (`0x599ba3–0x599cfd`). In normal mode reports whose IDs differ are ignored. A changed mask is dispatched only if the panel is the active page (`[panel+0xf534]`, else `A240828_1` "Inactive page, handle not available") to `0x597d40` (or `0x5988e0` when `g+0x4cf2`, a run-state variant).

Mask decoding `0x597d40`: for bit index *i* = 0..31 the mask is `table[i]` at `0x8416c0` (`8,800,80000,8000000,10,1000,…`), dispatch table `0x598854`; held-state bytes `[panel+0x2140+i]`:

| i (mask) | action | i (mask) | action |
|---|---|---|---|
| 0 (0x8) | Fn? set `+0x2260` : `PostMessage(0x403)` Start | 16 (0x4) | lifting up `0x57dc70(1)` |
| 1 (0x800) | pause/continue | 17 (0x400) | **Y+** hold |
| 2 (0x80000) | `PostMessage(0x404)` empty-move | 18 (0x40000) | Z+ `OnZPlusBtn` |
| 3 (0x8000000) | Stop | 19 (0x4000000) | `PostMessage(0x405)` Bounding |
| 4 (0x10) | Gas | 20 (0x2) | **X−** hold |
| 5 (0x1000) | ZF toggle | 21 (0x20000) | **X+** hold |
| 6 (0x100000) | laser toggle | 22 (0x2000000) | level → `MC.IsFastMode` |
| 7 (0x10000000) | hold-fire | 23 (0x1) | lifting down `0x57dc70(0)` |
| 8 (0x20) | Home | 24 (0x100) | **Y−** hold |
| 9 (0x2000) | Fn? `OnBreakFindBtn` | 25 (0x10000) | Z− `OnZSubBtn` |
| 10 (0x200000) | **Fn** level (`g+0x4781`) | 26 (0x1000000) | level → `MC.IsStepMove` |
| 11 (0x20000000) | `PostMessage(0x406)` **E-stop** | 27 (0x80000000) | Fn? `OnReturnMarkPtBtn` |
| 12 (0x40) | Fn? ZF calibration | 28 (0x80) | Fn? `OnPlatStartExchangeBtn` |
| 13 (0x4000) | `OnBackwardBtn` | 29 (0x200) | Fn? `OnPlatGoOriginBtn` |
| 14 (0x400000) | Fn? `0x58c350` | 30 (0x8000) | Fn? `PostMessage(0x412)` |
| 15 (0x40000000) | `OnForwardBtn` | 31 (0x800000) | red pointer |

(Jog-bit to NC-slot-21 argument mapping `0x598429–0x598518`: +0x2155→(0,1) X+, +0x2154→(0,0) X−, +0x2151→(1,1) Y+, +0x2158→(1,0) Y−. Buttons act only when exactly one new bit is pressed, `0x598528–0x598530`.)

### 6.4 RemoteType 2 "CypCut" report (reader `0x59a010`, EVIDENCE)
Accept only `b[0]==0x01 && b[1]==0xBC && b[2]==0x55` (`0x59a0cd–0x59a0f2`); key mask = `b[3]<<24 | b[4]<<16 | b[5]<<8 | b[6]` (big-endian); **23** key bits (`i < 0x17`, `0x59a241`; mask table `0x843d70`), dispatch table `0x59a820` (22 cases, `cmp …,0x15`) onto the same `CManuPanel` handlers (Start 0x403, pause, 0x404, Stop, Gas, ZF, laser, hold-fire, Backward, Home, Forward, Z+/Z−, …). Full bit table not decoded (not needed for this machine, RemoteType=3). Verifier: the mask variable `[ebp-0x4]` is zeroed only once before the loop (`0x59a02a`); a failed read (`0x59a0c7 je 0x59a809`) skips processing and an unsigned report keeps the previous mask, so a lost receiver never produces a release on this path. By contrast the RemoteType 0/1 reader sets mask := 0 on a failed read (`0x599b79`) and dispatches it, which releases held keys.

---

## 7. Linux hidraw driver specification (PHB02, RemoteType 3)

1. **Find**: `/sys/class/hidraw/*/device/uevent` `HID_ID=0003:000010CE:0000EBxx` with xx in §2.2. Parse the report descriptor (`HIDIOCGRDESC`): expect input report ID 4 (5 data bytes) and feature report ID 6 (7 data bytes). Open `O_RDWR` (all nodes of the device if the two reports are on different interfaces).
2. **Init**: `HIDIOCGFEATURE(8)` with `buf[0]=6` → dev-id/channel/RSSI/FID (§4.1). `HIDIOCSFEATURE(8)` `06 FE FD d FF FF 00 00` with `d = gmtime().tm_mday`.
3. **Read loop** (`poll` + `read(fd, buf, 6)`): apply §3.2 exactly — squash 0xFF keys, selector debounce (2 zero reports), checksum with both formulas; drop invalid reports; on an invalid report re-send the seed when PHBX's 8-bit count of *all* reads (valid, invalid and short, `0x10019603`) is divisible by 3 — not literally "every third invalid report" (also re-send when the UTC day changes — PHBX does it implicitly). Emit `(k1, k2, sel, down=(k1|k2)!=0)`; de-duplicate identical state words as MainApp does.
4. **Refresh**: `HIDIOCGFEATURE` at most once per second while reports arrive; `rssi ≥ 80` → "poor signal".
5. **Link supervision** (timestamps instead of 80 ms ticks): no report 1.04 s with key held → synthesize key-up **and stop all jogs**; 1.2 s → "link lost"; 3.2 s idle → "asleep" if the silence started 22–26 s after the last key release, else "link lost". Any valid report → "online". (For safety the port should stop motion on the 1.04 s timeout even if MainApp's own handler would not, **and on any read error / hidraw removal**, which PHBX+MainApp do not handle — §3.3 verifier note. Only reports with a valid checksum should change key state; PHBX updates its state word before the check.)
6. **Outputs**: `HIDIOCSFEATURE(8)` `06 FE FD d flags 01 00 00` whenever the indicator set changes (MainApp sends every poll cycle); for PID 0xEB7E apply the §4.2 remap.
7. **Semantics**: §5.2 table; jog = continuous card jog while held (A1 row 1), stop on release (A1 row 3); Fn = 0x0B.

Reference checksum (Python, derived from `0x1000136c–0x1000139a`):
```python
def phb02_crc_ok(b, day):
    n = (~b[1]) & 0xFF
    k1 = 0 if b[2] == 0xFF else b[2]
    s = (day | n) & 0xFF
    return b[5] in (((k1 + n) & 0xFF) ^ s, ((s + n) & 0xFF) ^ k1)
```

---

## 8. Residual checks that need the machine (one capture each)

| # | question | exact step |
|---|---|---|
| R0 | Is a PHB02 receiver fitted at all? (99-gaps §3: only 3689:8762 seen) | `lsusb \| grep -i 10ce` with the receiver plugged in |
| R1 | PID, number of HID interfaces/collections, report IDs and sizes | `lsusb -v -d 10ce:` and `cat /sys/class/hidraw/hidraw*/device/report_descriptor \| xxd` (or `usbhid-dump -d 10ce`) — expect input ID 4 len 6 and feature ID 6 len 8; note whether they share one hidraw node |
| R2 | Checksum formula and which of the two variants is used; UTC-day seed | send `06 FE FD <day> FF FF 00 00` via `HIDIOCSFEATURE`, then log 50 raw reports while pressing keys; verify with `phb02_crc_ok`; repeat with a different seed byte |
| R3 | Physical key ↔ code (0x01…0x13, Fn 0x0B) and meaning of `buf[4]` | press each key alone and with Fn, turn any knob; record `buf[2..4]` |
| R4 | Feature report 6 content (dev-id, channel, RSSI scale, FID) | `HIDIOCGFEATURE` every second while moving the pendant away from the receiver |
| R5 | LED bit meaning | `HIDIOCSFEATURE 06 FE FD <day> <1<<i> 01 00 00` for i = 0..7, note which LED lights |
| R6 | Idle/sleep timing (heartbeat interval, ~20 s auto-sleep) | timestamped raw read log with no key pressed for 60 s |
| R7 | (only if RemoteType 0/1/2 hardware exists) report length and layout for VID 1000:2016 / 6125:2012 | `lsusb -v` + raw hidraw dump while pressing keys; compare with §6.3/§6.4 |

---

## Verification notes

Adversarial re-derivation (verifier, 2026-09-15) from `PHBX.dll` (`.scratch/asm/PHBX.asm`, IAT decoded with a PE parser: `0x10012088` HidD_SetFeature, `0x1001208c` HidP_GetCaps, `0x10012090` HidD_GetPreparsedData, `0x10012094` HidD_GetAttributes, `0x10012098` HidD_GetHidGuid, `0x1001209c` HidD_GetFeature, `0x10012200` ReadFile, `0x10012204` GetTickCount, `0x10012210` GetSystemTime, `0x10012344` PostMessageA) and `MainApp.exe` (`0x7c09e0` GetProcAddress, `0x7c09d4` Sleep, `0x7c0fa4` PostMessageW). Every VA cited in the 15 key facts was re-read.

**Confirmed exactly (instruction level):** VID `0x10CE` test and the 20-entry PID→model ladder (all 20 values/codes re-read); HidP_GetCaps role split (Feature==8 checked first, then Input==6; `CreateFileA(GENERIC_READ, share 3, OPEN_EXISTING, flags 0)`); input parse offsets and the 0xFF squash; both checksum formulas (bytes `f6 d1 / 0a c1 / 02 d9 / 32 d8` and `02 c1 / 32 c3`) with `wDay` = `SYSTEMTIME+6` from `GetSystemTime` (UTC); seed `06 FE FD day FF FF 00 00`, output `06 FE FD day d0 d1 00 00` (+ model-620 remap bit for bit), `XWritFid` `06 FA FB day fidL fidH 00 00`, `XWritDev` 7-byte chunks; feature-read byte map; `XGetInput` return ladder incl. `cmp ecx,0x50; setl; dec; and 0x6a`; export RVAs/names; message map `0x4B1→0x57a580`, `0x4B2/3/4→0x57b680` (returns 0), `0x4B5→0x57b690`; key jump table `0x57b638` (16 entries, decoded from file bytes) and jog pushes (0,1)/(0,0)/(1,1)/(1,0) on NC vtable `+0x54`; `CHidUsb` ctor `0x452230` (+0x28 = arg1 = VID "1000"/"6125", +0x2c = PID); RemoteType 0/1 byte map (mask b[13..16], ids b[9..12], b[5..8], b[1..4], b[29..31], b[25..28] vs `g+0x4030..0x4040`, pairing chord 0x00020002, 10 ms loop); RemoteType 2 signature `01 BC 55` + BE32; mask table `0x8416c0` and dispatch table `0x598854`. Independent cross-check: with `k1 = 0`, PHBX formula 1 reduces to `n ^ (day|n) = day & rnd`, which is exactly LinuxCNC's released-key check `crc == randomByte & seed` (`xhc-whb04b6.cc printCrcDebug`) — strengthens fact 13. LinuxCNC `usb.h` `usbVendorId{0x10ce}`, `usbProductId{0xeb93}`, 8-field `UsbInPackage`, `usb.cc` `header = 0xfdfe; seed = 0xfe; reportId 0x06` re-fetched and confirmed.

**Refuted / corrected (edited in place above):**
1. *Fact 11 "polls XGetInput every 200 ms"* — wrong: busy loop without sleep while the status is unchanged (`0x599510–0x599512`).
2. *§2.3 "after XClose, XGetInput returns 0x66 until a new XOpen"* — wrong: returns 0x64 (handle test first). 0x66 is transient, or permanent only after a forced reader termination + `XOpen` without `Xinit`. Consequence: MainApp's "disconnected" (`A250613_1`) branch with its `XClose`/`XOpen` toggle is practically unreachable in normal operation.
3. *Fact 11 / §5.1 "A250613_2 is never raised"* — true for this PHBX, but the text is wired to WM 0x4B5 (`0x57b690`); the report had listed `0x4b5→0x57b690` without saying what it does.
4. *Fact 8 "asleep if the silence began 22–26 s after the last key release"* — the 22–26 s window is measured at detection (3.2 s into the silence), so the silence must begin 18.8–22.8 s after release; the classification is sticky when there was no key activity; the key-held timeout resets the tick counter (next classification at 4.4 s); three synthetic key-ups are posted. "Link lost after 1.2 s" applies only while a key is held; otherwise 3.2 s.
5. *§3.3 "if tick-value changed, 0x100010a0(tick)"* — dead code (`0x100195e5` is only ever 0 and is compared with the just-zeroed tick).
6. *§6.4 "24 key bits"* — 23 (`i < 0x17`).
7. *Corrections paragraph "07 §8.3 was right"* — 07 §8.3 inverts the RemoteType→pair selection (types 0 and 2).
8. *§7 step 3 "every third invalid report"* — the modulo is on the count of all reads.

**Missed in the same functions (added):**
* **Safety:** receiver unplug / hard `ReadFile` error with a jog key held produces no key-up anywhere (PHBX posts only 0x4B2, MainApp ignores it and ignores 0x64). Same class of gap on RemoteType 2 (stale mask kept on read failure). RemoteType 0/1 does release on read failure.
* Invalid-checksum reports still update the PHBX state word, key-held flag and release time; only the post is suppressed.
* `XSendOutput`/`XWritDev`/`XWritFid` call `XOpen` when the feature handle is invalid; MainApp calls `XSendOutput` from its 30 ms UI timer, so with no receiver the UI thread re-enumerates HID devices on every tick (INFERENCE high, subject to OnTimer early exits), and with a receiver the reader's 1-s feature refresh is starved (RSSI frozen at open).
* MainApp's second `XSendOutput` argument is a length byte (=1) that PHBX reads as `data[1]`.
* The first 0x4B1 after start-up is discarded by the static-init dedup; `k1==0,k2!=0` gives no button action; Fn+Z±(lifting table) is additionally gated by `g+0x47cc`.
* `wParam=0x100` with zero key bytes when both keys are 0xFF.

**Confidence after verification:** facts 1–7, 9, 10, 14, 15 stay **confirmed** (fact 7 with the 0x64-after-XClose correction); fact 8 **confirmed with corrections**; fact 11 **downgraded** to partly wrong (timing, 0x4B5); fact 12 stays **likely** (bit sources confirmed; LED meaning is inference); fact 13 stays **likely** (strengthened). The "closed" answers for O16 (PHBX HID layout; CHidUsb pairs; XGetInput disassembly; 01 §2.9 VID/PID) hold, with the 07 §8.3 caveat. What remains machine-only is unchanged: R0–R7 (§8). One extra check for the safety item, preferably without motion: run MainApp under Wine with a PHB02 receiver passed through, hold a jog key, unplug the receiver and log whether NC slot 31 (per-axis stop) is ever called (API trace / `WINEDEBUG=+relay` on the NCModule interface). Only if a real-machine confirmation is wanted: laser disabled, lowest jog speed, axis far from its limits, hand on E-stop; expected result per the static analysis: the axis keeps moving until a UI stop or soft limit.

