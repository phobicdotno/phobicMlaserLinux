# 04 – Controller / network protocol analysis (MCC100 card, laser, AF/EC, remote monitor)

Package analysed (read-only): `/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52` (referred to as `SRC` below).
Method: static analysis only — `objdump -p/-d/-s`, `strings`, a small Python PE/RTTI parser, runtime logs in `SRC/Log`, `SRC/Lang/lang.txt` and the INI files. No traffic capture was available. Every claim is tagged **EVIDENCE** (directly observed) or **INFERENCE** (with confidence).

---

## 0. Executive summary

| Topic | Result | Status |
|---|---|---|
| Where the networking lives | **Not in MainApp.exe** (it imports no `ws2_32`). All device I/O is in `SRC/Module/NCModule.dll` (PDB `D:\SC2000\NexCut\NexCut_X1_Http\Release\Module\NCModule.pdb`), which imports `WS2_32.dll` (18 functions incl. `sendto/recvfrom/select/connect/send/recv`). `Module/LogModule.dll` also imports `ws2_32` (hand-rolled HTTP POST log uploader). MainApp uses `WINHTTP.dll` only for a "NexCut" HTTP file server client. | CONFIRMED |
| Transport to the MCC100 card | **UDP** to `CardIP:CardPort` = `10.1.1.168:502` when `AccessType=1` (the shipped value); TCP `connect()` when `AccessType=0`. Socket options: `SO_SNDTIMEO/SO_RCVTIMEO = MCTimeout`, `SO_LINGER off`. | CONFIRMED (disassembly of `CMCHalAPI::open` @`0x10006330` + log lines `MC-Sendto`/`MC-Recvfrom`) |
| Is port 502 "real" Modbus TCP? | **No.** The card protocol is a proprietary *Modbus-flavoured* framing implemented by class `CExtModbus`: `[seq u16 BE][CRC16 u16][len u16 BE][unit=0][func][payload LE]`, with custom function codes **0x20, 0x21, 0x26, 0x30, 0x40** and Modbus-style exception replies (`func|0x80`, exception byte). Standard Modbus TCP (`CStdModbus`, FC 3/16, MBAP) exists in the DLL but is used for the *EC3710 extension card*, not the MCC100. | CONFIRMED (disassembly + CRC verified against logged frames) |
| Register map | 32-bit registers addressed by u32. Groups found: `1000` (RO status), `1050`, `2000` (per-axis RO, 5 axes × 10), `5000` (RW), `10000`, `11000`, `13000`, `13200`, `50000` (system RW, 26), `50200` (per-axis RW, 5 × 20), `59600+` (hardware parameter blocks), `60001`, plus command registers **0x65 (101) = command**, **0x66 (102) = FIFO stream**, **0x67 (103)**. Human-readable names of the register groups are in `lang.txt` (`RORegName_*`, `AxisRORegName_*`, `RWRegName_*`, `AxisRWRegName_*`, `SystemRWRegName_*`). | CONFIRMED addresses; names = INFERENCE (high) |
| Job streaming | Motion is streamed as a FIFO of 12-byte items via `func 0x40 → reg 0x66`: `[frameId u32][item0][item1]…`, motion items = 3 × u32 `{0x0008_0BB8, (int16 hi | int16 lo pulse deltas), 0x1388_0004 / 0x1388_0000 / 0x07D0_0064}` in the captured jobs, **interleaved with control records whose w0 ∈ {0x0BB9, 0x0004, 0x03E8, 0x0002, 0x0100, 0x03000002}** (see §3.7, verifier). Frames observed with 99 items (1192-byte payload). FIFO run control is **register 0x67** (`startFifo`=2, `stopFifo`=3, `clearFifo`=1, one word, func 0x40) — *not* 0x65 (verifier correction). | CONFIRMED framing; item field meaning = INFERENCE (low–medium) |
| Laser 10.1.1.170:10001 | **IPG YLR-style ASCII protocol** (`EMON\r`, `EMOFF\r`, `ABN\r`, `ABF\r`, `SDC <n>\r`, …) — `CIPGProtocolAdapter`; Raycus variant `\x1bO\r`, `\x1bS\r`, `\x1bCP<n>\r` (`CRaycusProtocolAdapter`). Port 10001 is the IPG default. | CONFIRMED strings/bytes; vendor mapping = INFERENCE (high) |
| MonitorIP 47.104.17.21:9001 | Remote telemetry ("远程监控 / Remote Monitor"): JSON documents (`devId`, `hwModuleId`, `lastUpdateTime`, `status`, `lightVal`, `warnId`, `time`) posted with method names `updateBasicInfoByDevId`, `updateStatusByDevId`, `upsertWarnByDevId`, `clearAllWarnByDevId`, via MFC WinInet classes (`CInternetSession`/`CHttpConnection::OpenRequest`/`CHttpFile`; header `Content-Type:application/json;charset=UTF-8`). Only when `SOP.EnableRemoteMonitor` is on. 47.104.17.21 is an Alibaba-Cloud (Qingdao) address. Privacy: machine ID, alarms, laser-on time, run status leave the LAN. | CONFIRMED strings/keys; transport = CONFIRMED by verifier (mfc100u ordinals 5056/10984/12616 resolve to WinInet-backed MFC code, see §4.4) |
| WINHTTP in MainApp | Class `CHttpClient/1.0` → `POST /NexCut/Login`, `/NexCut/File/LoadFile`, `/NexCut/File/UploadGCode` (JSON / multipart, header `Authorization: DebugWithSuperpermissions`, default user `NexCut` / password `12345678`). Target host comes from a software parameter (`A241218_3 "address"`), not from ipAdd.ini. `http://www.au3tech.cn/key/` is only a stored string (activation-code web page). | CONFIRMED |
| Licensing | Card-bound: `CDog` in NCModule stores/reads an encrypted "data area" and the card's RTC through the register protocol; `Log/Code.txt` is its log. The USB HID dongle `HID#Vid_3689&Pid_8762` ("iKey", "PWDKeyCo.") is used **only by AutoNest.dll** (auto-nesting), via `Nest_CheckLock/Nest_EncodeByKey`. | CONFIRMED |
| Firmware `MCC100_V201.52.mcf` | 120 456 bytes, header `{u32 size=0x1D688, u32 0x00F8B0F4, u32 0, …}`, body is high-entropy (7.998 bits/byte) — **encrypted/compressed; CPU architecture not recoverable statically**. Uploaded with function code 0x26 (byte-block write) by `fillDownFile`. | CONFIRMED |

---

## 1. Inventory of network endpoints (`SRC/File/ipAdd.ini`)

**EVIDENCE** – file content (section `[IP]`, then `[Soft]`):

| Key | Value | Consumer (from NCModule loader code) | Meaning |
|---|---|---|---|
| CardIP / CardPort | 10.1.1.168 / 502 | `CVirtualMachine` init @`0x10058440…`: `GetPrivateProfileStringA("IP","CardIP","10.1.1.168")`, `GetPrivateProfileIntA("IP","CardPort",502)` | MCC100 motion card (UDP or TCP, see §3) |
| CardSubnet/Gateway | 255.255.255.0 / 10.1.1.1 | written to card system RW regs (`SystemRWRegName_1..3` = IP/mask/gateway) | card's own IP config |
| ZFIP / ZFPort | 10.1.1.169 / 502 | default `"10.1.1.169"`, 502 | external Z-follower (FTC/"调高器") over Ethernet |
| OnBZFIP / OnBZFPort | 10.1.1.168 / 999 | default 999 | **on-board** Z-follower channel on the card (same IP, port 999) |
| OnBLaserIP / OnBLaserPort | 10.1.1.168 / 888 | | on-board laser channel |
| LaserIP / LaserPort | 10.1.1.170 / 10001 | default port 0x2711 = 10001 | fiber laser source, IPG ASCII protocol |
| AFIP / AFPort | 10.1.1.168 / 888 | default 0x378 = 888 | auto-focus head ("电动调焦") via card |
| AdvAFIP / AdvAFPort | 10.1.1.168 / 666 | default 0x29A = 666 | "advanced" AF channel |
| ECIP / ECPort, AdvECIP/AdvECPort | 10.1.1.168 / 888, / 666 | | extension card (4th axis etc.) via card |
| EC3710IP / EC3710Port | 10.1.1.170 / 502 | default 502, uses **standard Modbus FC16** (`init 3710 ExCard` uses function-code constant 16) | "MCC3710" extension card, real Modbus TCP |
| MonitorIP / MonitorPort | 47.104.17.21 / 9001 | default `127.0.0.1` / 0x1778 = 6008 | remote monitoring server (WAN) |
| MonitorReconnInterval / MaxTime | 300000 / 1 | MainApp | reconnect every 300 s, max 1 |

Timing / retry keys (defaults in parentheses come from the DLL's `GetPrivateProfileIntA` calls, e.g. @`0x100586f0..0x10058bea`, `0x1001795c..0x1001825d`):

| Key | ini value | code default | Use (from `CMCHalAPI` transaction code, §3.4) |
|---|---|---|---|
| ConnectWait | 500 | 500 | ms wait after connect |
| MCTimeout | 500 | 500 | socket send/recv timeout (ms) → `SO_SNDTIMEO/SO_RCVTIMEO` and `select()` timeout |
| MCMaxSendTime | 2 | 3 | max `sendto` attempts per transaction |
| MCMaxRecvTime | 3 | 2 | max `recvfrom` attempts per send |
| MCSendInterval | 1 | 10 (also 1 in another loader) | `Sleep()` between full retries (ms) |
| MCFifoTime | 1600 | 1600 | FIFO fill pacing (ms of motion to keep queued) — INFERENCE |
| FifoTimeout | 600 | 600 | |
| FifoAlarmNum | 30 | | matches `RegName101` "Processing FIFO alarm limit threshold" |
| MaxItemPerFrame | 60 | 160 | items per streamed frame (AF/EC/ZF down-files; MC frames observed with 99 items) |
| MaxFillItem | 2000 | 1000 | max items queued per fill |
| MCCore | 30 | 10 | core loop period (ms) — INFERENCE |
| MCUpdateFactor | 1 | 1 | status poll every N core cycles |
| ZF*/AF*/EC*/Laser*/Serial*/Monitor* Timeout/MaxSend/MaxRecv/Interval | see file | see code | same semantics per channel |
| `[Soft] AccessType` | **1** | 1 | **1 = UDP, 0 = TCP** for the MC card (log string `Access: UDP` / `Access: TCP`) |
| MinHardwareVer | 20152 | 0x1773 = 6003 | minimum card firmware version accepted; below → auto-upgrade prompt `mf232` |
| OnBZFMinHardwareVer / NCZFMinHardwareVer / AFMinHardwareVer | 311 / 325 / 133 | 108/108/120 | |
| MaxZFDownItem / MaxAFDownItem / MaxECDownItem | 512 / 200 / 200 | 1024/200/200 | chunk sizes for file download to sub-devices |
| EnableLog | 0 | 0 | enables the verbose `Send Cmd:/Recv Cmd:` frame logging |
| AdvLaserWrite | 1 | 0 | |
| CheckUserID | 109 | | licensing "User ID" (厂商ID) |

`SRC/File/pingMC.bat` / `pingZF.bat` just `ping 10.1.1.168` / `10.1.1.169`.

### 1.1 `File/IPSet.exe` — what it really is
**EVIDENCE**: imports only `IPHLPAPI.dll (GetAdaptersInfo, GetInterfaceInfo)`, `MPRAPI.dll (MprConfigServerConnect, MprConfigGetFriendlyName)`, `MSVCR100.system`. Strings: `netsh interface ip set address name="`, `" source=static addr=10.1.1.`, ` mask=255.255.255.0 gateway=10.1.1.1`, PDB `c:\users\zzy\documents\visual studio 2010\Projects\IPSet\Release\IPSet.pdb`, class `CIPHelper`.
Disassembly (`main` at `0x402b40`): loops over the adapter list (28-byte entries), builds for adapter *i* the command `netsh interface ip set address name="<friendly name>" source=static addr=10.1.1.<10+i> mask=255.255.255.0 gateway=10.1.1.1` (`lea ecx,[edi+0xa]` → 10+i) and runs it with `system()`.
**Conclusion**: IPSet.exe configures the **PC's NICs** to 10.1.1.10, 10.1.1.11, … — it does not talk to the card at all. It is not a protocol window. (On Linux: `ip addr add 10.1.1.10/24 dev <nic>`.)

---

## 2. Software architecture of the communication layer

**EVIDENCE** (RTTI class names recovered from `NCModule.dll`, vtables via Complete-Object-Locators; `SRC/Module/NCModule.dll`):

```
IHalAPI  (9 virtuals: dtor, ?, open, readReg, writeReg, ?, ?, close?, ?)
 ├─ CMCHalAPI         open=0x10006330 readReg=0x100225e0 writeReg=0x10022760   ← MCC100 (and ZF, see below)
 ├─ CAFNetHalAPI      readReg=0x1001dbf0 writeReg=0x1001ee40                     ← AF head via card port 888/666
 ├─ CECNetHalAPI      readReg=0x1001ff00 writeReg=0x10020fb0                     ← extension card
 ├─ CLaserNetHalAPI   readReg=0x100105c0 writeReg=0x10010690                     ← laser TCP
 ├─ CSerialHalAPI     (CSerialPort, "\\.\COM%d", SetCommState…)                  ← serial laser/FTC
 └─ CMonitorHalAPI    open = stub (returns false); vf1=0x1000c9b0 (HTTP JSON)     ← remote monitor
IModbus  (5 virtuals: dtor, encodeWithSeq, encode, decodeWithSeq, decode)
 ├─ CStdModbus      standard Modbus/TCP MBAP, FC3 & FC16       (global @0x100aba18)
 ├─ CExtModbus      ** MCC100 framing ** (seq+CRC)             (globals @0x100aba08 [MC], @0x100aba10)
 ├─ CExtCardModbus  RTU-style [unit 0x02][func][..][CRC]       (global @0x100aba24, used by CECNetHalAPI)
 ├─ CSerialModbus   RTU-style with CRC (jump table func 3/16/0x26)(global @0x100aba20, used by CAFNetHalAPI + serial)
 ├─ CFTC61Modbus    (global @0x100aba28)                        ← FTC61 height controller
 ├─ CIPGModbus      raw byte pass-through (ASCII)              (global @0x100aba1c, laser)
 ├─ CRaycusModbus   raw byte pass-through                      (global @0x100aba2c, laser)
 └─ CMonitorModbus  0xA5 0xA5 … BCD timestamp frame            (global @0x100aba30)
ILaserProtocolAdapter → CIPGProtocolAdapter, CRaycusProtocolAdapter  (build the ASCII commands)
CVirtualMachine (149 virtuals) – device-side state machine: updateMCStatus*, fillFifo, startFifo, jog, …
CNCModule (208 virtuals) – exported via newModuleProvider() to MainApp
CDog – card-bound licence;  CFrogJumpSvr/CFJSAccSrv – "frog-jump" (flying) cut server; CVerticalCorrect
YaoUtil::YaoLog / YaoBinLog – logger with optional HTTP upload (DestUrl)
```

The global framing objects and their concrete classes come from the `.data` initial vtable pointers (`0x100aba08→CExtModbus`, `0x100aba10→CExtModbus`, `0x100aba18→CStdModbus`, `0x100aba1c→CIPGModbus`, `0x100aba20→CSerialModbus`, `0x100aba24→CExtCardModbus`, `0x100aba28→CFTC61Modbus`, `0x100aba2c→CRaycusModbus`, `0x100aba30→CMonitorModbus`); the getter functions `0x10027c60/70/80/90/cd0` return them and are called from the respective HAL read/write routines (verified: MC transaction uses `0x10027c60` → CExtModbus; AF HAL uses `0x10027c80` → CSerialModbus; EC HAL uses `0x10027c90` → CExtCardModbus; laser HAL uses `0x10027c70` (IPG) / `0x10027cd0` (Raycus)).

MainApp.exe ↔ NCModule.dll: MainApp loads the module DLLs and calls `newModuleProvider` (`IModuleManager`/`CLeanModuleManager`), i.e. **all protocol logic can be re-implemented by replacing NCModule.dll's role**.

---

## 3. The MCC100 card protocol (class `CExtModbus`, `CMCHalAPI`)

### 3.1 Transport & socket setup — **EVIDENCE** (`CMCHalAPI::open` @`0x10006330`)

```
WSAStartup(0x101)
if accessType == 0:   s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP); connect(s, {AF_INET, htons(port), ip}, 16)
if accessType == 1:   s = socket(AF_INET, SOCK_DGRAM,  IPPROTO_UDP)            ; no bind/connect
setsockopt(s, SOL_SOCKET, SO_SNDTIMEO(0x1005), &timeout_ms, 4)
setsockopt(s, SOL_SOCKET, SO_RCVTIMEO(0x1006), &timeout_ms, 4)
setsockopt(s, SOL_SOCKET, SO_LINGER (0x0080), {0,0}, 4)         (TCP path only)
this->timeout_us = timeout_s * 1000   (select() timeout)
```
`AccessType=1` in the shipped ini and log strings `MC-Sendto` / `MC-Recvfrom` ⇒ **the card is driven over UDP/502, one datagram per request and one per reply**.
Verifier additions (EVIDENCE): (a) `CVirtualMachine::init` @`0x10056b6d` reads `GetPrivateProfileIntA("Soft","AccessType",1)` and then, if a software-parameter byte at `[param+0x4356]` is non-zero, **forces the value to 0 (TCP)** (`0x10056b86..0x10056b8e`); the log string is chosen at `0x10056b94` (`==1` → `Access: UDP` @`0x1008be80`, else `Access: TCP` @`0x1008be68`). Neither string appears in the shipped logs (they are only emitted at `---NC Start---` level with logging on). (b) The `select()` timeout is a `timeval` at `this+0x28/+0x2c` = `{0, MCTimeout_ms*1000}` (`imul edx,edx,0x3e8` @`0x10006548`, also re-set by `setTimeout` @`0x100065b6`). (c) The correct wording for the accessType argument: it is the 4th argument of `open` (`[ebp+0x14]`), `0` → `socket(2,1,6)+connect+SO_LINGER`, `1` → `socket(2,2,17)` with only the two timeout options. (WS2_32 ordinal map used by the DLL: 2 bind, 3 closesocket, 4 connect, 8 htonl, 9 htons, 11 inet_addr, 16 recv, 17 recvfrom, 18 select, 19 send, 20 sendto, 21 setsockopt, 23 socket, 52 gethostbyname, 111 WSAGetLastError, 115 WSAStartup, 116 WSACleanup, 151 __WSAFDIsSet.)

### 3.2 Frame format — **EVIDENCE** (`CExtModbus::encode` @`0x10027ce0`, `::encodeWithSeq` @`0x10028240`, `::decode` @`0x10029620`, `::decodeWithSeq` @`0x10029b40`; CRC helper tables @`0x10089440` (hi) / `0x10089540` (lo))

```
offset  size  field
0       2     sequence number, BIG-endian (per-HAL-instance u16 counter at this+0x14e; the transaction routine uses the
              CURRENT value and post-increments it (0x1001d719..0x1001d731), so the first datagram after (re)connect has seq 0 —
              matches the logged '00 00 8c 75 …' and the exception replies with seq 0x00e4/0x00e5 right after a socket re-create.
              Note: CExtModbus::encode (no-seq variant, 0x10027d12) increments its OWN counter at CExtModbus+4; the MC path uses encodeWithSeq.)
2       2     CRC-16/MODBUS (poly 0x8005 reflected = 0xA001, init 0xFFFF) over bytes [4..end].
              PC stores it as  [2] = crc >> 8 , [3] = crc & 0xFF  (i.e. "hi first").
              The card answers with the opposite byte order ([2]=lo,[3]=hi). The PC decoder never checks the CRC.
4       2     length, BIG-endian = number of bytes following this field (unit + func + payload)
6       1     unit id = 0x00
7       1     function code
8       …     payload, all multi-byte integers LITTLE-endian
```
Maximum buffer 0x5A8 = 1448 bytes (fits one Ethernet frame).

Function codes (encoder jump table @`0x100281fc`/index `0x10028210`, decoder table @`0x10029b08`/`0x10029b1c`):

| func | request payload | reply payload | purpose (name = INFERENCE) |
|---|---|---|---|
| **0x30** | `u32 addr, u16 count` | `u32 addr, u16 count, count × u32 LE` (decoder checks `count*4 == len-14`) | READ registers |
| **0x40** | `u32 addr, u16 count, count × u32 LE` | `u32 addr, u16 count` (echo) | WRITE registers / command / FIFO data |
| **0x26** | `u32 addr, u16 count, count × u8` (low byte of each vector element) | `u32 addr, u16 count` | byte-block write — used by `fillDownFile`, `fillZFDownFile`, `fillAFDownFile`, `fillECDownFile` (firmware / program download) |
| **0x20 / 0x21** | `u16 count, count × u8` (no address) | 0x20: `[0,0,u16 count]` with `len-10 == count` check | stream/ack of byte data (used by the download/offline-upload code paths, `0x1001e291/0x1001e370`, `0x10023f22/0x10023f47`) |
| any other (default) | encoder emits only the 4-byte `[seq][crc=0]` header — no length/unit/func at all (jump-table default `0x100281a0`); CRC loop is skipped because len-4 = 0 | decoder default (`0x10029ae4`): `out = [func, byte[8]]` — this is also how exception replies are parsed | unsupported / exception |
| **exception** | — | `[func \| 0x80][exception code]` → `ErrCode = 500 + code` | Modbus-style exception |

**Verified against real traffic in the logs** (`SRC/Log/*.log`, written by `CMCHalAPI::writeMCLog` @`0x1001d160`):
```
MC-Sendto ErrCode:10065 … Data:00000030 000003e8 00000002  Buffer:00 00 8c 75 00 08 00 30 e8 03 00 00 02 00
  seq=0x0000  crc=0x8c75  len=8  unit=0  func=0x30  addr=0x000003E8 (1000) count=2        → READ 2 regs @1000
  CRC-16/MODBUS("00 08 00 30 e8 03 00 00 02 00") = 0x8C75  ✔
MC-RecvDataErr ErrCode:503 … Data:000000c0 00000003  Buffer:1d 3f 34 45 00 03 00 c0 03  DataEx:40 65 06 03 01 c350 176f ea56 3d0900
  reply frame: seq=0x1d3f crc bytes 34 45 (=0x4534 lo-first) len=3 unit=0 func=0xC0 (=0x40|0x80) exception=3 → ErrCode 503
  request was func 0x40 addr 0x65 count 6 data {3,1,50000,5999,59990,4000000}
MC-RecvDataErr ErrCode:502 … Buffer:06 c5 d0 45 00 03 00 b0 02   → func 0x30 exception 2 (illegal address) = ErrCode 502
```

### 3.3 Transaction, retry and error semantics — **EVIDENCE** (`0x1001d6a0`, called by `readReg`/`writeReg`)

1. `EnterCriticalSection(global)`; `seq = ++this->seqCounter`; `encodeWithSeq(request, buf, seq)`; on encode error → `ErrCode = rc + 1000`, log id 6 `"Encode"`.
2. Send loop (`MCMaxSendTime` tries): `sendto(sock, buf, len, 0, &sockaddr, 16)`. On `SOCKET_ERROR`: log `"Sendto"`; retry only if `WSAGetLastError()` ∈ {10052 NETRESET, 10053 CONNABORTED, 10054 CONNRESET, 10060 TIMEDOUT}, else abort with the winsock code.
3. Receive loop (`MCMaxRecvTime` tries): `select(fd+1,&rfds,0,0,{MCTimeout})`; if 0 → `ErrCode 10060`; if >0 but not set → `9999`; then `recvfrom(sock, buf, 0x5A8, 0, &from, &fromlen)`; `decodeWithSeq(buf, out, seq)` → rc: 2 = too short, 4 = **sequence mismatch (stale datagram; recv again)**, else `ErrCode = rc + 2000` (`"DecodeRecvData"`).
4. Validate reply: `out[0]==req[0] && out[1]==req[1]` → success (return 1). If `out[0]==req[0]+0x80` → `ErrCode = 500 + out[1]` (`"RecvDataErr"`, i.e. 501..5xx are card exception codes). Otherwise `ErrCode 603` (unexpected reply).
   Verifier note on the meaning of exception codes: the names "illegal address (2)" / "illegal data value (3)" used below are **Modbus convention only**. The logs contradict a literal reading: exception 2 was returned for `READ 1000/2`, `READ 60001/120`, `READ 50000/26`, `READ 10000/18`, `READ 11000/39` and `WRITE 0x65 ← [0x66]`, and the `READ 1000/2` cases occur with reply seq `0x00e4/0x00e5` immediately after an `MC-Recvfrom ErrCode:10038` (socket destroyed → HAL re-created → seq restarts). So code 2 more plausibly means "card not ready / busy after reset" (INFERENCE, medium). Code 3 was only ever seen for 0x65 sub-commands 1 and 3 (jog/home) — consistent with "command rejected in current state" (INFERENCE, medium).
   Return codes of `decodeWithSeq` (`0x10029b40`): 2 = frame < 8 bytes, 4 = seq mismatch (`0x10029ba3`), 5 / 6 = payload length inconsistent for 0x30 / 0x20-family; the no-seq `decode` (`0x10029620`) returns −100/−101/−102/−103 for the same conditions.
5. After the last recv try with timeout, `Sleep(MCSendInterval)` and repeat the whole send (until `MCMaxSendTime` exhausted). `LeaveCriticalSection`.
   Verifier refinement (EVIDENCE `0x1001d968..0x1001d981`, `0x1001dab7..0x1001dad0`): when the timeout (10060) happens on the **first recv try of the first send try** (`recvTry==MCMaxRecvTime && sendTry==MCMaxSendTime`), the code jumps straight to the Sleep+resend path instead of exhausting the remaining recv tries; on later tries a 10060 just decrements the recv counter. Log stage names (UTF-16 @`0x10088c2c..0x10088cb0`): `Encode`, `Sendto`, `Recvfrom`, `DecodeRecvData`, `RecvErr_selectFunc` (select returned 0 → 10060, or select error), `RecvDataErr`. All 1072 logged 10060 lines are `MC-RecvErr_selectFunc`, i.e. select() timeouts, not recvfrom() errors.
Log line format: `MC-<Stage> ErrCode:<n> Try-Times(R/S):<recvTry>/<sendTry> Data:<reply vector hex> Buffer:<raw frame> DataEx:<request vector hex>`.

Observed ErrCodes: 10060 (no reply / timeout, 1072 lines), 503 (illegal data value, 567), 502 (illegal address, 36), 10065 (host unreachable — NIC not on 10.1.1.x), 10038 (socket invalid).

### 3.4 Address validation on the PC side — **EVIDENCE** (`CMCHalAPI::readReg` @`0x100225e0`, `writeReg` @`0x10022760`)
For func ≠ 0x26 the PC *does not send* reads whose address lies in (104,1000) except 150/151 (returns failure with zeros), nor (1100,2000), (3000,5000), (5008,6000), (51000,59000) (returns success with zeros). Everything else goes to the wire. So the live map is: **0..104, 150, 151, 1000..1100, 2000..3000, 5000..5008, 6000..51000, 59000+**.

### 3.5 Register map (addresses = **EVIDENCE**, names = INFERENCE from `lang.txt` key order, high confidence)

Polling functions in `CVirtualMachine` build the vector `[0x30, addr, count]` from constants at `0x1008a630..0x1008a67c`:

| Address | count | Function | Content (lang.txt names) |
|---|---|---|---|
| **1000** | 2 / 36 / variable | `updateMCStatusFast` (`0x10052130`, every core cycle), `checkMCStatus` (`0x1004e3d0`, count ≈20) | `RORegName_1..32`: 1 program id, 2 program version, 3 date, 4 time, 5 input status, 6 output status, 7 alarm status 1, 8 alarm status 2, 9 run status, 10 AD sample, 11–13 DA1–3 sample, 14 PWM freq, 15 PWM duty, **16 FIFO frame id**, **17 FIFO space margin**, 18 FIFO interp data config, 19 slave device info, 20 processing status, 21 processing position, 22/23 extended in/out, 24 contour status, 25 contour index, 26 power-on time, 27 comm time, 28 laser-on time, 29 dual-drive deviation, 30/31 sampling encoder cfg/len, 32 parameter status |
| 1050 | 3 | (logged) | probably program-version/time triple |
| **2000** | 50 (=5 axes × 10) | `updateMCStatusAxisRO` (`0x100518c0`, stride 40 B, size check 0xD4 = 3+50 words) | `AxisRORegName_*` — lang.txt has 12 names; NCModule references keys 1,2,3,4,6,7,8,9,10,12 (not 5, not 11), i.e. exactly 10 = the per-axis stride, but the *order* used by the DLL (key 12 "encoder value when stopped" sits between 6 and 7 in the string table) is not the numeric order. Names: 1 status, 2 speed, 3 pulse position, 4 encoder position, (5 Z-phase encoder pos — unused), 6 stop pulse, 12 encoder at stop, 7 cumulative travel, 8 calc. cumulative pulses, 9 calc. cumulative encoder, 10 cumulative pulses, (11 cumulative encoder — unused). Index→address = INFERENCE (medium). |
| **5000** | 1 | `updateMCStatusRW` (`0x10051b00`, size check 0x10 = 3+1 words) | `RWRegName_*`: lang.txt lists 5 (input type, output mask, e-stop input port, safety decel, safety jerk) but the DLL references only keys 1, 4, 5 and reads a single register here; the PC-side window is 5000..5008. |
| 10000 | 18 | `0x1004ea50` (logs `false updateZFStatus: %d`; size check 0x54 = 3+18 words) | **on-board Z-follower (ZF) status block**, read every core cycle (549 of the 1072 logged timeouts are this read) — INFERENCE medium (from the log string). The analyst's "pulse-axis group" guess is withdrawn. |
| 11000 | 39 (logged `30 2af8 27`; size check 0xA8 = 3+39 words in `0x1004ee94`) | `0x1004ed90` (logs `updateZFProp`), `0x1003e0c0/0x1003e240` | ZF (height-controller) property block — INFERENCE medium (function name from its log string) |
| 13000 / 13200 | ? | `updateMCStatusRO` (`0x1004eff0`) / `0x1004f8e0` | |
| **50000** | 26 | `updateMCStatusSystemRW` (`0x10051f20`, size check 0x74 = 3+26 words) | `SystemRWRegName_*` — but lang.txt has **47** names and NCModule only references keys **1–10 and 28–41** (24 names; keys 11–27 and 42–47 are never used by the DLL), so the 26 registers cannot be mapped 1:1 onto the lang.txt numbering. Names in lang.txt: IP, subnet, gateway, baud rate, spare, bus cycle, Z-axis enable, DA1–3 calibration, (11–26 bus-axis 1–16 input config, 27 pulse-axis input cfg — unused), 28 comm timeout, 29 special input cfg, 30–32 dual-drive cfg/tolerance/alarm time, 33 spare, 34 precision coeff, 35 brake delay, 36–41 input filter times IN1–IN12. Mapping = INFERENCE (low). Note the card answered this read with exception 2 (9 times) and with timeouts (92 times) in the logs. |
| **50200** | 100 (=5 × 20) | `updateMCStatusAxisRW` (`0x10051d00`, stride 80 B) | `AxisRWRegName_1..14`: config, +soft limit, −soft limit, accel, jerk, coarse-home speed, fine-home speed, origin offset, spare, lead (pitch), cmd pulses/unit, encoder pulses/unit, input cfg, output cfg |
| **59600 + i** (i ≤ 31), blocks of 0xD0 = 52 regs | `readParamFromCard` (`0x10050830`) / `writeHardParam2Card` (`0x100504c0`) | hardware parameter area (what `BkHardPara.xml` mirrors) |
| 60001 | 120 | (logged) | parameter/servo info block |
| **0x65 = 101** | n | command register (func 0x40) | see §3.6 |
| **0x66 = 102** | 1 + 3·items | FIFO data (func 0x40) | see §3.7 |
| **0x67 = 103** | 1 | **FIFO run control** — `startFifo` @`0x10050af0` writes `[0x40, 0x67, 1, 2]`, `stopFifo` @`0x10050dc0` writes `[0x40, 0x67, 1, 3]`, `clearFifo` @`0x100510f0` writes `[0x40, 0x67, 1, 1]` (all via HAL vtable+0x10 = `writeReg`; log strings `VM Start Fifo`/`VM Stop Fifo`/`false clearFifo`) | EVIDENCE (verifier). Values: 1 = clear, 2 = start, 3 = stop (names from the functions' own log strings). `0x10058031` in `mcCoreProcess` passes `0x67` to `CVirtualMachine` virtual #34 (vtbl+0x88) together with a vector when state `[this+0x4a20]` ∈ {1,2}; likely a read/poll of the same register (INFERENCE, low). |

A second, older register list `RegName1..143` in `lang.txt` (product id, program version, input status, axis go-origin status, …, `RegName23 FIFO frame id`, `RegName50 FIFO operation flag`, `RegName79 IP address`, `RegName89 MCC3721 hardware version`, `RegName113 FIFO protocol flags`) belongs to the **MCC3721** predecessor card (`pd1732/pd1733 "MCC3721H/NA"`); the MCC100 ("EtherCAT" bus card, `EtherCATErrorInfo_*`) uses the grouped map above.

### 3.6 Command register 0x65 — sub-commands (vector written with func 0x40, addr 0x65)

Format: `count` u32 words, first word = sub-command. **EVIDENCE**: log `DataEx` dumps + the builders that reference `0x65` (`0x10031f10` = multi-axis sub-cmd 5 with 9 words, `0x10032160` = single-axis sub-cmd 3 with 6 words — the verifier found the analyst had these two swapped; `0x1004ccb0` setDA (`false setDA: %d`), `0x10051370` a second clear routine that also logs `false clearFifo: %d` and writes `[9999, 1, …]` with count 4, `0x10057540` VM init handshake, `0x10047cc0` offline upload, `0x10057950` mcCoreProcess). Meaning = INFERENCE (medium):

| words | example (from logs) | meaning |
|---|---|---|
| `[1, axisMask?, mode, speedA, speedB]` | `01 01 02 1388 c350`, `01 02 02 7d0 4e20`, `01 1f 02 7d0 4e20` | go-origin (home). The second word was observed as 1, 2 and **0x1F** (= bits 0–4 → all five axes), so it is most likely an axis bit-mask, not an index (verifier, INFERENCE medium). Speeds 5000/50000 and 2000/20000 (`AxisRW_6/7` coarse/fine home speeds?). Every logged instance was rejected with exception 3 (only failures are logged). |
| `[3, axis, vel, acc, jerk, dist]` | `03 01 c350 176f ea56 ±3d0900`, `03 00 30d40 176f ea56 …`, `03 04 c350 fa0 9c40 f4240` | single-axis relative move (jog step): axis field observed as 0, 1 and 4; v = 50000 / 200000, a = 5999 (0x176f), j = 59990 (0xea56), d = signed 32-bit (±4 000 000, and many arbitrary values such as 0xfff8ae4b); the `04` variant used a=4000, j=40000, d=1 000 000. Units unknown (pulses assumed). All logged instances were rejected with exception 3 (verifier: 567 lines, all sub-cmds 1/3 — successes are not logged). |
| `[5, axisMask, vel, acc, jerk, dX, dY, dZ, dW]` | `05 80000003 86470 2327 15f86 69062 2a811 0 0` | multi-axis jog/move (built by `0x10032160`: `[5, mask, min(v,vmax), min(a,amax), 5·a, …]`); bit31 of mask = flag |
| `[0x66]` (single word 102) | `40 65 01 66` | FIFO-related one-shot (stop/flush?) — rejected with exception 2 both times it was logged |
| `[9999, 1, a, b]` (count 4) | (`0x10051370`, logs `false clearFifo`) | 9999 = "misc control" family: 1 = FIFO clear (older/alternative clear path; the current `clearFifo` @`0x100510f0` uses reg 0x67 ← 1) |
| `[9999, 2, ch, 0/1]` | `270f 02 01 00/01` | set digital output / laser gate (on/off) |
| `[9999, 4, ch, value]` | (setDA `0x1004ccb0`) | set DA (analog) channel |
| `[9999, 13, 0xFFFF, hwVerHi…]` | VM init `0x10057575` | handshake / version exchange at connect |
| `[7]`, `[1, 8]` | offline-upload paths in `0x10047cc0` | offline program upload start/stop |
| `[4, …]` | mcCoreProcess "start manu" | start processing (FIFO run) |

~~`startFifo` / `stopFifo` (`0x10050af0` / `0x10050dc0`, log `VM Start Fifo` / `VM Stop Fifo`) and `clearFifo` (`0x100510f0`) are further 0x65 writes.~~ **Corrected by verifier:** these three write register **0x67** (`[0x40, 0x67, 1, 2]`, `[0x40, 0x67, 1, 3]`, `[0x40, 0x67, 1, 1]`), see §3.5.

### 3.7 FIFO streaming — job model (reg 0x66)

**EVIDENCE** (log frames `40 66 12a <id> …`, builder `0x100437d0`):
`[func 0x40][addr 0x66][count 0x12A = 298][frameId][297 words = 99 items × 3 words]` → 1192-byte payload, 1206-byte datagram. The frame id (`0x3c, 0x28e, 0x78b, …`) is repeated unchanged on retries and corresponds to `RORegName_16 "FIFO frame identification"`; the PC compares it with the card's FIFO frame id / `RORegName_17 "FIFO space margin"` to decide how many items to push (`fillFifo` @`0x10052390`: "Update MC Status Success: CalcBufferSize:%d, RealItemNum:%d", three-step fill with timing logs).

Item = 12 bytes. Verifier re-decoded **all 30 logged 0x66 frames (22 distinct frame ids, always count 0x12A = 1 + 297 words = 99 items)**. Values are *not* homogeneous:
```
motion items (2952 of 2970):  w0 = 0x0008_0BB8 (hi=8, lo=3000)
                              w1 = int16 hi | int16 lo   e.g. 0x00010000, 0x0001FFFF, 0, 1, 2, 0xFFFFFFFC, 0xFFFD …  (small deltas)
                              w2 = 0x1388_0004 (2259×), 0x1388_0000 (594×), 0x07D0_0064 (99× = one whole frame, id 0x78b)
control records (18, in frames 0x39/0x1f8 [items 79–85] and 0x279 [items 55–60], always a block whose length is a multiple of 3 words):
  (0x00000BB9, 0x00040BBA, 0x00000005)   (0x00000BB9, 0x000C270F, 0x00000002)   (0x00000004, 0x00000004, 0x00080067)
  (0x000003E8, 0x00000000, 0x000807D1)   (0x03000002, 0x00004E20, 0x000C270F)   (0x00000002, 0x00000100, 0x00000100)
  (0x00000100, 0x00000000, 0x0008006D)   (0x00000004, 0x00000000, 0x00000023)
```
INFERENCE (low–medium, verifier): the 12-byte record is type-tagged by the low 16 bits of w0 (0x0BB8 = 3000 = interpolation step, 0x0BB9 = 3001, 0x03E8 = 1000, …) rather than a fixed `{const, dxdy, const}` triple; the pairs (0x1388, 0x0004)/(0x1388, 0x0000)/(0x07D0, 0x0064) in w2 look like (PWM frequency 5000/2000 Hz, duty/power 4 %/0 %/100 %) and would explain laser-off moves (`0x1388_0000`), but this must be confirmed by capture (§9). `RORegName_18 "FIFO interpolation data configuration"` suggests the card advertises the record layout.
INFERENCE (low–medium): w1 = (Δaxis_hi<<16 | Δaxis_lo) pulses for one interpolation tick (bus cycle) — which half is X and which Y is unknown; w0/w2 carry laser/PWM & flags (see the verifier block above). `fillFifo` (`0x10052390..`) contains the constant `0x64` (100) three times and `0x32` (50) once; the ini key `MaxItemPerFrame=60` is *not* what limits MC frames (observed 99 = 100 − 1?) — INFERENCE low. The exact field semantics must be confirmed by capture with different power/frequency settings (see §9). Interpolation itself is done on the PC (`MotionCtrl.dll`: `segInterp`, `arcInterp`, `newVelocityPlanning`, `newContourSmooth`) — the card only executes pulse deltas, which explains `EtherCATErrorInfo_2_05 "FIFO starvation"` and the 1600 ms `MCFifoTime` look-ahead.

Related alarms (`lang.txt`): `EtherCATErrorInfo_2_00 Illegal command`, `2_01 interpolation data length abnormal`, `2_02 axis control command exception`, `2_03 FTC command exception`, `2_04 PLC command exception`, `2_05 FIFO starvation`, `1_25 bus fault`, `1_26 output fault`, `1_30 e-stop`, `3_01 no slave scanned`; per-axis `EtherCATAxisErrorInfo_0..5` (hard/soft limits ±, servo input, dual-drive).

### 3.8 Connection / handshake sequence (INFERENCE, medium — order reconstructed from `CVirtualMachine::init` @`0x10056b10` and `checkMCStatus`)
1. Read `ipAdd.ini`, `AccessType`; open UDP socket (§3.1). Log `---NC Start---`.
2. `checkMCStatus`: READ 1000 (≈20 regs) → program id / version / date / time; version compared with `MinHardwareVer` (20152 → `mf232` "hardware version too old, will upgrade"). Also reads card clock for licensing (`CDog`, `Log/Code.txt`: `get card clock fail`, `verify data area failed`).
3. Handshake write `0x65 ← [9999, 13, 0xFFFF, ver…]`, then a second 0x65 write.
4. `readParamFromCard` (59600+ blocks) / compare with `BkHardPara.xml`, `writeHardParam2Card` if changed ("params need hardware restart", `hp38`).
5. Configure sub-devices depending on flags read from the card: on-board ZF (port 999), AF (888/666), EC (888/666), EC3710 (10.1.1.170:502 Modbus FC16), laser (10.1.1.170:10001).
6. Core loop (`mcCoreProcess`, period `MCCore` ms): READ 1000 (fast status) every cycle; READ 2000/5000/50000/50200 every `MCUpdateFactor` cycles; during a job `fillFifo` writes 0x66 frames while `FIFO space margin` allows; jog/home/IO via 0x65.

---

## 4. Other channels

### 4.1 Standard Modbus TCP (`CStdModbus` @`0x100287f0`/`0x1002a040`) — **EVIDENCE** (verifier re-checked: vtable @`0x1008965c` is RTTI `.?AVCStdModbus@@`, global @`0x100aba18`; MBAP zero bytes and BE length seen in the encoder; the FC3/FC16 branch structure was not re-traced and is taken from the analyst)
MBAP `[trans=0][proto=0][len=2·n][unit=0]`, FC **3** (`[addr BE][count BE]` → `[bytecount][words BE]`) and FC **16** (`[addr][count][bytecount][words BE]`). Used with the function-code constants 3/16 (`0x1008a678/0x1008a67c`) by `init 3710 ExCard` (`0x10053bd0`) and several `0x1004b020…0x10050180` routines. This is the **EC3710 extension card at 10.1.1.170:502** — on Linux use `libmodbus`.

### 4.2 RTU-style framing over the card's sub-ports (`CExtCardModbus` @`0x10028d00`, `CSerialModbus` @`0x10028a00`) — **EVIDENCE**
`[unit 0x02][func][…][CRC16 lo][CRC16 hi]` with FC 3 (`addr BE, count BE`), FC 16 (`addr, count, bytecount, words BE`), FC 0x26 (`u32 addr LE, count BE, bytes`), decoder also accepts FC 0x26 replies (`u32 LE value + u16`). Used by `CAFNetHalAPI` (auto-focus head, `AFIP:888`) and `CECNetHalAPI` (extension axis card, `ECIP:888/666`) — the card acts as a gateway to those sub-devices (log prefixes `AF Send Buf:/AF Recv Buf:`, `EC Send Buf:`). Z-follower ("ZF"/FTC) status uses `syncReadZFReg`/`updateZFStatus`; the second `CExtModbus` instance (`0x100aba10`) is most likely the ZF channel (INFERENCE, medium). `CFTC61Modbus` (`0x10029000`, frames start `00 00 00 00 … 0x21`) serves the FTC61 height controller over serial/"MCC serial".

### 4.3 Laser source 10.1.1.170:10001 — **EVIDENCE**
`CLaserNetHalAPI` + `CIPGModbus` (byte pass-through) + `CIPGProtocolAdapter` builders (`0x10027270…0x100274b0`, `0x10027930`):
`"EMON\r"`, `"EMOFF\r"`, `"ABN\r"`, `"ABF\r"`, `"EMOD\r"`(bytes 45 4D 4F 44 0D), `"ELE\r"`, `"DLE\r"`, `"DEABC\r"`, `"EEABC\r"`, `"SDC<n>\r"` (set diode current = power %). These are the **IPG YLR/YLS fiber-laser Ethernet command set** (emission on/off, aiming beam on/off, enable/disable laser emission & external aiming-beam control, set diode current); port 10001 is IPG's default TCP port. `CRaycusProtocolAdapter` (`0x10027120…`): `ESC 'O' \r`, `ESC 'S' \r`, `ESC 'C' 'P' <n≤100> \r`, and an 18-byte frame with frequency 50..50000 (Raycus RFL serial protocol); vendor list in `lang.txt`: `pd47 锐科/Raycus, pd48 IPG, pd49 semiconductor, pd50 创鑫/MaxPhotonics, pd50-1 nLight, pd50-2 国志/GZ`. Only IPG and Raycus have adapters in this build. Laser alarms `gp44..gp55` decode a laser status word (temperature, interlock, power, …).

### 4.4 Remote monitor 47.104.17.21:9001 — privacy relevant
**EVIDENCE**: `CMonitorHalAPI` vtable @`0x10087044` (RTTI-verified): `open` = `0x10006ad0` = `xor al,al; ret 0x10` (stub, always false); `vf1` @`0x1000c9b0` passes the header string `Content-Type:application/json;charset=UTF-8` (@`0x10087540`) to mfc100u ordinal 1312 and calls ordinals 5056 (once) and 12616 (three times); `vf6` @`0x100262d0` → JSON builder `0x10022820` (boost::property_tree → keys `reqBuVoStr`, `mapProps`, `lastUpdateTime`) and then calls mfc100u ordinal 10984 with the argument pattern `(…, 0, 1, 0, 0, 0x20000000)`.
Verifier resolution of the mfc100u ordinals (from `SRC/mfc100u.dll`, ImageBase 0x785f0000, delay-import table): ordinal **12616** → `0x787e6c05`, whose body calls the delay-loaded `InternetSetOptionExW/InternetQueryOptionW/InternetSetStatusCallbackW/InternetSetCookieW` slots (`0x788a4eb0/…ea0/…eb4/…f30`) ⇒ `CInternetSession` construction/options; ordinal **5056** → `0x787e695c` (calls `InternetOpenUrlW`, `InternetGetCookieW`, …) ⇒ `CInternetSession` methods; ordinal **10984** → `0x787e8219`, in the code region that calls `HttpAddRequestHeadersW/HttpSendRequestW/HttpEndRequestW/HttpSendRequestExW/HttpQueryInfoW` (`0x788a4f0c..0x788a4f1c`), and its 7-argument call with `dwFlags = 0x20000000 = INTERNET_FLAG_EXISTING_CONNECT` (the MFC default) matches `CHttpConnection::OpenRequest(nVerb, pstrObject, pstrReferer=0, dwContext=1, ppstrAcceptTypes=0, pstrVersion=0, dwFlags)`. ⇒ the transport **is MFC WinInet HTTP** (CONFIRMED). Ordinals 1312 (`0x78646123`) and 902 (`0x78650bee`) are *not* WinInet code (unidentified CString-type helpers), so the analyst's "header set through ordinals 1312/902" is imprecise. `0x1004d380` prepares documents with keys `devId` (= `SP.MachineID` "远程监控.机床ID"), `hwModuleId`, `lastUpdateTime` (`%d-%02d-%02d %02d:%02d:%02d.%03d`), `time`, `lightVal` (laser-on time), `status`, `warnId` and calls the server methods `updateBasicInfoByDevId`, `updateStatusByDevId`, `upsertWarnByDevId`, `clearAllWarnByDevId` (log names `moctUpdateBasicInfo/moctUpdateRunStatus/moctUpdateWarnInfo/moctClearAllWarn`). Parameters: `SOP.EnableRemoteMonitor (pd533)`, `SOP.RemoteMonitorHeartbeat (pd534)`, `SP.MachineID/DataCardID/CommandID (pd518-520)`, test mode `pd870-873`. A binary variant (`CMonitorModbus::encode` @`0x10029200`: `A5 A5 <type> <BCD> <BCD date/time…>`) also exists. `MonitorReconnInterval=300000`. **INFERENCE (high)**: with `EnableRemoteMonitor=1` the machine id, run state, alarms and laser-on time are pushed to an Alibaba-Cloud host in China; block it on the Linux side (or simply don't implement it). Also `LogModule.dll`/`YaoUtil` can POST log text (`logID=…&logData=…&machineID=…&isText=…`, `%s %s HTTP/1.1`, form-urlencoded) to a configurable `DestUrl` — none configured in the package.

### 4.5 MainApp WINHTTP client — **EVIDENCE**
`CHttpClient` (`0x4060b0` WinHttpOpen("CHttpClient/1.0") + WinHttpConnect; `0x406940` POST `/NexCut/File/LoadFile` JSON; `0x407430` POST `/NexCut/File/UploadGCode` multipart (`A250606_1`: files < 500 MB); `0x434990` login `/NexCut/Login?Name=NexCut&Password=12345678`; header `Authorization: DebugWithSuperpermissions`). Host/port come from software parameters (`A241218_1/2/3` "Equipment type / number / address", `A241221_0` "Connect"), not from ipAdd.ini. This is an optional LAN/cloud "NexCut" job server (`NexCut_X1_Http` build). `SensApi.IsNetworkAlive` is used to gate it. `http://www.au3tech.cn/key/` is assigned to a CString at `0x4bb7d5` (inside the function starting `0x4ba190`, 0x50 bytes after the `MonitorIP` ipAdd.ini read at `0x4bb788`) and shown in the activation dialog; no HTTP request is made to it. Verifier cross-check of string references: `CHttpClient/1.0` @`0x406157/0x406a55/0x407755`, `/NexCut/Login` @`0x434af0`, `12345678` @`0x434acb`, `Authorization: DebugWithSuperpermissions` @`0x407088`, `\Update\MCC100_V` @`0x565e5f`; WinHttp IAT calls: `0x4060b0` region → `WinHttpOpen/WinHttpConnect`, `0x406940` region → `OpenRequest/SendRequest/ReceiveResponse/QueryDataAvailable/ReadData`, `0x407430` region → `WriteData/SendRequest/Connect`. The login routine `0x434990` itself contains no WinHttp calls (it builds the URL and delegates).

### 4.6 Licensing and dongles
* **Card-bound licence** (`CDog`, `Log/Code.txt`): activation code ↔ "Hid" (hardware id read from the card), card RTC ("CardTime"), licence days, `UserCode` (=`CheckUserID 109`); states `dogState_*` (no hardware, unauthorized, clock illegal, data sector broken, trial over…). Verifier: `Code.txt` message histogram — `verify data area failed!` ×683, `Check code Error: Hid:-N CallTime… CardTime… PcTime…` ×264, **`Arm Clock is invalid!` ×103**, `Admin data was broken (read admin data)!!!` ×88, `Active CodeStr data: Hid:N CodeTime:… LincensDay:N UserCode:N` ×29, two distinct 20-letter activation codes, `reset card clock fail!!` ×5. The strings `Arm Clock…`, `Admin data…`, `User data is error` do **not** occur in any binary of this package (they come from the older 2024 build that wrote those lines); the current NCModule uses `get card clock fail!!`. "Arm Clock" is a weak hint that the card's MCU is an ARM (INFERENCE, low). The data area is read/written through the register protocol (addresses not recoverable statically; 150/151 are special-cased in `readReg` — INFERENCE low). A Linux port must either reproduce this (needs live capture of the exchange) or keep the card's stored licence untouched.
* **USB HID dongle VID 3689 PID 8762** (`HID#Vid_3689&Pid_8762`, strings `iKey.`, `PWDKeyCo.,`) is referenced only by **AutoNest.dll** (`Nest_CheckLock`, `Nest_EncodeByKey`; error code 1 "没找到加密狗 / dongle not found" in `排样内核错误代码.txt`, a UTF-8 file). Verifier: AutoNest.dll is an MFC42/MSVCRT-era DLL that imports **no** HID.DLL/SETUPAPI (imports: CircleFitDLL, Dxf2Grp, MFC42, MSVCRT, KERNEL32, USER32, GDI32, MSVCP60, ADVAPI32) — it must open the device by its symbolic path with `CreateFile` (INFERENCE, medium). MainApp.exe contains no `Vid_3689`/`0x3689`/`0x8762` reference at all, so only the automatic nesting feature depends on the dongle (CONFIRMED). PHBX.dll compares the HID vendor id against **0x10CE** (`cmp WORD PTR [esp+0x1c],0x10ce` @`0x10001848`) — XHC's USB VID — confirming it is the pendant driver and not the dongle.
* **Wireless pendant**: `PHBX.dll` = "chengdu XHC Tec." product **PHB02 / XHC PHBX** (`Xinit/XOpen/XGetInput/XSendOutput/XGetDevRssi/XGetChannel/SetGetKeyCallbackFunction`), HID over USB receiver; MainApp `CHidUsb` + `SetupDi*`/`HidD_*`. Pairing codes are card registers (`RegName40-46/90-96` LCR/USB adapter addresses & key values).

---

## 5. Firmware image `SRC/Update/MCC100_V201.52.mcf`
**EVIDENCE**: 120 456 bytes; dwords LE `[0x0001D688 (=file size), 0x00F8B0F4, 0x00000000, 0x32BA741B, …]`; Shannon entropy 7.998 bits/byte overall (7.95 in the first/last 4 KiB); printable runs ≥ 6 chars: 191, which is what random bytes of this size produce (the analyst's "zero strings" was imprecise — there are no *meaningful* strings); no ARM vector table (no clusters of `0x0800xxxx`/`0x2000xxxx` words); CRC32/Adler/sum over any header-aligned range ≠ 0x00F8B0F4. ⇒ payload is encrypted (or compressed with a header CRC we cannot match). CPU architecture **unknown**. MainApp `CHardwareUpdateDlg` (`0x565d20`) picks `\Update\MCC100_V*.mcf` (and `\Update\E310_V80*.afb` for the AF head), checks version (`mf176/mf244` file errors, `mf179` "hardware will restart"), and NCModule streams it with FC 0x26 byte-blocks (`fillDownFile`). The dword at +4 is probably a checksum the card verifies (INFERENCE, medium).

---

## 6. Error / status vocabulary useful for a re-implementation (`lang.txt`)
* Connection: `mf0` controller connected, `mf5` connect failed, `mf172` controller not connected, `gp0` hardware not connected, `gp13` network alarm during processing, `gp96` network state abnormal, `mf1001` main controller type changed, `mp22/mp7/mp8/mp9/mp10/mp11` MCC Offline/Ready/Origin/Jog/Stop/Process, `mf242/243` monitor connected/offline, `mf225/226` remote monitor connect ok/failed.
* Card alarms `gp1..gp59` (servo input, encoder, dual-drive, hard/soft limits per X/Y1/Y2/W, e-stop, FPGA not registered/loaded, FTC alarms, laser alarms, chiller), `gp200..gp216` extension-card axis alarms, `gp217/218` ZF value/signal alarms.
* Upgrade: `mf174–179`, `mf232` (card fw too old, auto-upgrade), `mf432` (ZF), `mf701` (AF), `hp38` (params need card restart).

---

## 7. What is CONFIRMED vs GUESSED

**Confirmed (disassembly + logs agree; re-verified independently):** UDP/502 transport and socket options; frame layout incl. seq/CRC/len/unit/func; CRC algorithm and PC byte order (recomputed: request 0x8C75 stored `8c 75`, replies 0x4534 / 0x45D0 stored `34 45` / `d0 45`); function codes 0x30/0x40/0x26/0x20/0x21 (jump/index tables re-read); exception mapping (`func|0x80`, `ErrCode=500+code`); retry/timeout state machine and all ErrCodes; ini keys and defaults (all default constants re-read); register group addresses (1000, 2000, 5000, 10000, 11000, 50000, 50200, 59600+) and counts; command registers 0x65/0x66 **and 0x67 (FIFO run control 1/2/3)**; FIFO frame structure (frameId + 3-word records, 99 records/frame); laser ASCII commands (all 12 builders decoded); monitor JSON keys/method names **and WinInet transport**; IPSet.exe behaviour; dongle usage; firmware being opaque.

**Guessed:** the semantic names of individual registers (lang.txt order ↔ address order — weaker than the analyst stated, see §3.5), meaning of 0x65 sub-command arguments (axis index vs. mask), FIFO record semantics (dx/dy orientation, PWM/power fields, the control-record types), meaning of card exception codes 2/3, which physical channel the second `CExtModbus` instance serves (ZF), the exact handshake order, licence register addresses.

---

## 8. Implications for the Linux port

Must be replicated (no library does it):
1. **`CExtModbus` framing** over UDP (one datagram per request, seq echo check, CRC-16/MODBUS over `[len..end]` stored hi-byte-first, LE payload) with the 0x30/0x40/0x26 function codes and the retry policy (send ≤3, recv ≤2, `select` 500 ms, retry on stale seq).
2. The **status polling schedule** (1000 every cycle; 2000/5000/50000/50200 periodically) and decoding of alarm/status words (bit meanings still to be captured).
3. The **command register 0x65** vocabulary (home, jog, move, IO/DA/PWM via 9999,*, handshake 9999,13) **plus register 0x67 = FIFO run control (1 clear / 2 start / 3 stop)**.
4. The **FIFO streamer**: build 12-byte records from the PC-side interpolator (motion records 0x0BB8 *and* the control records seen in §3.7 — laser/PWM changes are apparently in-band), send frames of ≤99 records to reg 0x66 tagged with a monotonically increasing frame id, throttle on `FIFO space margin`, keep ≈1.6 s of motion queued.
5. Hardware parameter block read/write at 59600+ (mirror of `BkHardPara.xml`).
6. Firmware/file download with FC 0x26 (only if you want to update the card from Linux; otherwise keep the Windows tool for that).
7. Licence handshake with the card (unknown registers) — alternatively leave licence data untouched and just check that the card accepts commands.

Can be replaced by existing Linux/open-source components:
* EC3710 extension card: **libmodbus** (Modbus TCP FC3/FC16, unit 0, 10.1.1.170:502).
* IPG laser: plain TCP socket + ASCII lines (`EMON\r`…), any language; Raycus: same with ESC-framed commands; serial variants: termios.
* PC interpolation/velocity planning: **replace MotionCtrl.dll** with an own trajectory planner (e.g. Ruckig, or a LinuxCNC-style planner) that emits per-tick pulse deltas.
* Remote monitor / log upload / NexCut HTTP server: **omit** (privacy); if needed, libcurl.
* Pendant: XHC PHB02 receiver is USB HID → **hidapi/libusb** (XHC WHB04B protocol is documented by the LinuxCNC community).
* Auto-nesting dongle: not portable; use an open nesting library (e.g. libnest2d) instead of AutoNest.dll.
* NIC setup (IPSet.exe): `ip addr add 10.1.1.10/24 dev …`.

---

## 9. Recommended live-capture plan (to close the gaps)

1. On the Windows PC (or a Linux box bridged in between), capture **`udp port 502 and host 10.1.1.168`** plus `tcp port 10001 and host 10.1.1.170`, `port 999`, `port 888`, `port 666`, `tcp port 502 and host 10.1.1.170`, and `host 47.104.17.21`:
   `tcpdump -i <nic> -s 0 -w mlaser.pcap '(host 10.1.1.168 or host 10.1.1.169 or host 10.1.1.170 or host 47.104.17.21)'`
   Set `EnableLog=1` in `ipAdd.ini [Soft]` so NCModule also writes `Send Cmd:/Recv Cmd:` with timings.
2. Write a tiny dissector (Python/Scapy or a Wireshark Lua plugin) using §3.2: split `seq|crc|len|unit|func|payload`, decode 0x30/0x40 vectors LE, verify CRC both byte orders.
3. Sessions to record: (a) plain start-up with the machine idle → handshake (`9999,13`), version/licence reads, parameter reads at 59600+; (b) each jog key once per axis and direction → 0x65 sub-cmd 3/5 argument mapping and units; (c) homing → sub-cmd 1; (d) toggle gas/laser/red-light/outputs → `9999,2/4` channel numbers; (e) a small square with two power/frequency settings → confirm FIFO item fields (w0/w2 change with power/PWM, w1 with geometry) and the frame-id / space-margin flow control; (f) trigger a limit alarm and an e-stop → bit positions in `RORegName_7/8` alarm words; (g) enable "hardware upgrade" only if you accept the risk → observe FC 0x26 chunking (do not interrupt).
4. Compare the register dumps with `BkHardPara.xml`/`BkLayerPara.xml` values to name each register.
5. For the licence: capture the exchange right after `Active OK` in `Log/Code.txt` to learn which addresses hold the data area and the card clock.

---

## 10. Open questions
* Bit-level meaning of status/alarm words at 1000+4…1000+8 and axis status at 2000+0.
* Exact units of jog/move arguments (pulses vs. 0.001 mm) and which of `AxisRW` lead/pulse-equivalent registers the PC uses for conversion.
* FIFO item fields: is w1 (dx,dy) or (dy,dx)? what are 8/3000 and 5000/4? Is there a 4th/5th axis form (Z/W) with longer items or a different `count`?
* Semantics of the frame-id in 0x66 frames vs. `RORegName_16`, and the exact flow-control rule (`MCFifoTime` 1600 ms, `FifoAlarmNum` 30).
* ~~Register 0x67~~ (resolved: FIFO run control, values 1/2/3) — still open: the single-word `[0x66]` command written to 0x65 (rejected with exception 2 in the logs), and what `mcCoreProcess` does with 0x67 at `0x10058031`.
* Meaning of card exception codes 2 and 3 (they are returned for plain status reads right after a reconnect, so "illegal address" is doubtful).
* Semantics of the FIFO control records (w0 = 0x0BB9 / 0x0004 / 0x03E8 / 0x0002 / 0x0100 / 0x03000002) and of the w2 pairs (5000,4) / (5000,0) / (2000,100).
* Whether the sub-command 1 second word is an axis bit-mask (0x1F observed) and sub-command 3's is an index (0, 1, 4 observed).
* Which software parameter sits at `[param+0x4356]` and forces TCP (`AccessType` override).
* Which device the second `CExtModbus` instance (0x100aba10) and the standard-Modbus instance (0x100aba18) really talk to (ZF at 10.1.1.169:502? on-board ZF at :999?).
* Whether the card also accepts TCP (AccessType=0 path exists) and whether the reply CRC (lo-first) implies the card *does* verify request CRCs in the PC's (hi-first) order.
* Licence data-area registers and the card-clock set/read commands.
* Firmware container format (`.mcf`): cipher/compression and the meaning of the 0x00F8B0F4 header word; CPU type of the MCC100.
* mfc100u ordinals 1312/902 used by the monitor HAL (CInternetSession/CHttpFile?) — confirm by capture of port 9001 traffic.

---

## Verification notes (adversarial re-check of every key claim against `SRC`)

Method: every command in the analyst's evidence trail was re-run (`objdump -p/-d/-s`, `strings -a` / `-e l`, `iconv`, Python stdlib for PE/RTTI/CRC/entropy), the relevant functions were re-read from the disassembly, `lang.txt` (UTF-16LE) and all 16 log files were re-decoded. No file under `SRC` was modified.

### Claims checked and result

| # | Claim | Verdict | What was actually re-checked |
|---|---|---|---|
| 1 | Networking only in NCModule.dll, MainApp has no ws2_32 | **confirmed** | `objdump -p`: MainApp imports OPENGL32, GLU32, BCGCBPRO, HID, SETUPAPI, SensApi, PSAPI, dbghelp, mfc100u, MSVCR100/P100, KERNEL32…, gdiplus, WINHTTP, WINMM — no WS2_32. NCModule and LogModule import WS2_32 (18 ordinals: 2,3,4,8,9,11,16,17,18,19,20,21,23,52,111,115,116,151). PDB paths as stated. |
| 2 | UDP/502 when AccessType=1, TCP path for 0, SO_SNDTIMEO/RCVTIMEO/LINGER | **confirmed** | `0x10006330`: `[ebp+0x14]==0` → `socket(2,1,6)`, `connect(…,16)`, `setsockopt 0x1005/0x1006/0x80`; `==1` → `socket(2,2,17)`, `setsockopt 0x1005/0x1006`. Added: `[param+0x4356]` override to TCP, timeval `{0, ms*1000}`. |
| 3 | Frame layout, CRC-16/MODBUS over bytes 4.., PC hi-first, card lo-first, no CRC check on receive | **confirmed** | Encoder `0x10027ce0` re-read byte by byte; CRC tables at `0x10089440` (hi) / `0x10089540` (lo) are the standard Modbus tables; Python recompute: request 0x8C75 → logged `8c 75`; replies 0x4534 → `34 45`, 0x45D0 → `d0 45`. Decoder `0x10029620` checks only length ≥ 8 and seq. Corrected: seq is post-incremented (first frame seq 0); default encoder case emits only 4 bytes. |
| 4 | Function codes and payloads, exception `func|0x80`, ErrCode 500+code | **confirmed** (naming of exception codes **downgraded**) | Jump table `0x100281fc` = {0x10027fb9, 0x10028085, 0x10027d7f, 0x10027e51, 0x100281a0}; index table `0x10028210`: 0x20/0x21→0, 0x26→1, 0x30→2, 0x40→3, else 4. Decoder tables `0x10029b08/0x10029b1c` identical layout. `0x1001db04..0x1001db0e`: `edi = out[1] + 0x1f4` when `out[0] == req[0] + 0x80`. Logs: exception 2 also returned for READ 1000/2 right after reconnect → "illegal address" is convention only. |
| 5 | Retry policy and ErrCodes 9999/603/+1000/+2000 | **confirmed** | `0x1001d6a0` fully re-read: loops over `[esi+0x13c]` (MaxSend) / `[esi+0x140]` (MaxRecv); winsock retry set {0x2744,0x2745,0x2746,0x274c}; `0x270f` when select>0 but fd not set; `0x25b` unexpected reply; `eax+0x3e8` encode error; `eax+0x7d0` decode error; `Sleep([esi+0x144])`. Added the first-try shortcut and the stage names. |
| 6 | Register groups / counts / PC-side windows | **confirmed** | Constant table `0x1008a630..0x1008a67c` = {1000,5000,2000,50200,50000,10000,11000,13000,13200,1000,2000,1000,2000,16,16,0x30,0x40,0x26,3,16}; size checks 0xD4/0x10/0x19C/0x74/0x54/0xA8 in the six update functions; `readParamFromCard` adds 0xE8D0 with `cmp ebx,0x1f`; `readReg` `0x100225e0` windows re-read (104<a<1000 except 150/151 → fail; 1100<a<2000, 3000<a<5000, 5008<a<6000, 51000<a<59000 → zeros). Added 11000/39 and the ZF meaning of 10000/18 (from its log string `updateZFStatus`). |
| 7 | Register names from lang.txt keys | **downgraded** | The DLL references only subsets: `AxisRORegName_{1,2,3,4,6,7,8,9,10,12}`, `RWRegName_{1,4,5}`, `SystemRWRegName_{1..10,28..41}`, all 32 `RORegName_*`, `PulseAxisRORegName_{1,2,3,4,6,7}` and `PulseAxisRWRegName_1..14`. 26 system registers vs 24 referenced names; the numeric order is not the DLL's string-table order. Mapping index→address remains unproven. |
| 8 | 0x65 sub-commands; startFifo/stopFifo/clearFifo are 0x65 writes | **partly refuted** | `startFifo` `0x10050af0`, `stopFifo` `0x10050dc0`, `clearFifo` `0x100510f0` push `[0x40, 0x67, 1, 2/3/1]` and call HAL vtable+0x10 (`writeReg`). Builder addresses swapped (0x10031f10 = sub-cmd 5, 0x10032160 = sub-cmd 3). Handshake `[9999,13,0xFFFF,ver]` → 0x65 confirmed at `0x10057575..0x100575cc`. setDA `[0x40,0x65,4,9999,…]` confirmed. Sub-cmd 1 second word observed as 0x1F → likely a mask. |
| 9 | FIFO frames: `[frameId][99 × {0x80BB8, dxdy, 0x13880004}]` | **downgraded** | All 30 frames decoded: 22 ids, count always 0x12A, 99 records; but 18 control records with other w0 values and w2 variants 0x13880000 / 0x07D00064 exist. `fillFifo` uses constant 0x64, not `MaxItemPerFrame`. |
| 10 | CStdModbus for EC3710 (FC16), RTU-style CExtCardModbus/CSerialModbus for AF/EC | **confirmed** | `.data` vtable pointers resolved through RTTI: `0x100aba08/0x100aba10` → CExtModbus, `0x100aba18` → CStdModbus, `0x100aba1c` CIPGModbus, `0x100aba20` CSerialModbus, `0x100aba24` CExtCardModbus, `0x100aba28` CFTC61Modbus, `0x100aba2c` CRaycusModbus, `0x100aba30` CMonitorModbus; getters `0x10027c60/70/80/90/cd0` return `0x100aba08/1c/20/24/2c`; `0x10053bd0` pushes `0x1008a67c` (=16) and the `init 3710 ExCard` string; CExtCardModbus stores unit byte 0x02; FTC61 header `00 00 00 00 … 21`; Monitor `A5 A5` and `5A 5A FF`. The FC3/FC16 branch structure inside CStdModbus was not re-traced. |
| 11 | IPG ASCII / Raycus ESC commands | **confirmed** | All builders decoded from the `mov DWORD PTR [..],imm` sequences: `\x1bO\r`, `\x1bS\r`, `\x1bCP<n>\r` (5 bytes), `EMON\r`, `EMOFF\r`, `ABN\r`, `ABF\r`, `EMOD\r`, `ELE\r`, `DLE\r`, `DEABC\r`, `EEABC\r`, `SDC…` (`0x10027930`). LaserPort default 0x2711 re-read; vendor keys pd47..pd50-2 re-decoded. |
| 12 | Remote monitor JSON via MFC WinInet | **upgraded to confirmed** | mfc100u ordinals 5056/10984/12616 resolved through the delay-import table of `SRC/mfc100u.dll` (WININET.dll IAT `0x788a4e9c..0x788a4f34`); `CHttpConnection::OpenRequest` argument pattern with `INTERNET_FLAG_EXISTING_CONNECT`; open stub `0x10006ad0` = `xor al,al`. Ordinals 1312/902 are not WinInet. |
| 13 | MainApp WINHTTP client | **confirmed** | String references and WinHttp IAT call sites listed in §4.5. |
| 14 | Licensing / dongle / pendant | **confirmed** (details added) | AutoNest.dll strings + no HID imports; MainApp has no 0x3689/0x8762; PHBX.dll checks VID 0x10CE; Code.txt histogram; `Arm Clock` strings absent from current binaries. |
| 15 | Firmware opaque | **confirmed** (wording fixed) | Header `{0x1D688, 0xF8B0F4, 0, 0x32BA741B, 0x1800C, …}`, entropy 7.9984, no vector-table clusters (9 words in 0x0800xxxx, 4 in 0x2000xxxx of 30114), CRC32/sum over header-aligned ranges ≠ 0xF8B0F4. |
| 16 | IPSet.exe only runs netsh | **confirmed** | Imports IPHLPAPI/MPRAPI/`system`; strings; `lea ecx,[edi+0xa]` @`0x402be0`; `system` IAT `0x40511c` called in the loop. |

Other statements re-checked and found correct: all ini keys/values (§1), all code defaults (MCMaxSendTime 3, MCMaxRecvTime 2, MCSendInterval 10 and 1, MCTimeout 500, MCFifoTime 1600, MaxItemPerFrame 160, MaxFillItem 1000, MCCore 10, MinHardwareVer 0x1773, LaserPort 0x2711, AFPort 0x378, MonitorPort 0x1778, AccessType 1, CardPort 0x1f6, MCUpdateFactor 1, FifoTimeout 0x258), every quoted `lang.txt` key (mf*, gp*, pd*, hp38, RegName*, EtherCATErrorInfo_*, A24xxxx/A25xxxx), the `Try-Times(R/S)` log format, ErrCode histogram (10060 ×1072, 503 ×567, 502 ×36, 10065 ×5, 10038 ×2), buffer size 0x5A8, log-uploader strings (`%s %s HTTP/1.1`, `application/x-www-form-urlencoded`, `logID=`, `&machineID=`, `DestUrl`) — present in **both** LogModule.dll and NCModule.dll.

### Changes made to the document
* §0 rows "Job streaming" and "MonitorIP" rewritten (0x67, control records, WinInet confirmed).
* §3.1: AccessType override, timeval, argument position added.
* §3.2: sequence-number semantics corrected; default encoder/decoder case corrected; exception-code naming downgraded; decode return codes added.
* §3.3: first-try resend shortcut and stage names added.
* §3.5: 10000/18 re-attributed to ZF status, 11000/39 added, name-mapping confidence lowered for 2000/5000/50000, 0x67 resolved.
* §3.6: builder addresses swapped back, sub-command tables corrected (mask, observed values, exception outcomes), the "further 0x65 writes" sentence struck.
* §3.7: full re-decode of the logged frames, control records added, item semantics downgraded.
* §4.1/4.4/4.5/4.6/5/7/8/10 amended as marked "verifier".

### Remains uncertain
* Everything listed under §10, in particular the card-side meaning of exception codes, the FIFO record types, the axis-argument encoding of 0x65 sub-commands, and the register-name mapping. None of these can be closed without a live capture (§9).
