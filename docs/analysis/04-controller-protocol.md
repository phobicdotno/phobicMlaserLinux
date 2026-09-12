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
| Job streaming | Motion is streamed as a FIFO of 12-byte items via `func 0x40 → reg 0x66`: `[frameId u32][item0][item1]…`, each item = 3 × u32 `{0x0008_0BB8, (int16 dy<<16 | int16 dx), 0x1388_0004}` in the captured job. Frames observed with 99 items (1192-byte payload). FIFO control via reg 0x65 sub-commands (`startFifo/stopFifo/clearFifo`). | CONFIRMED framing; item field meaning = INFERENCE (medium) |
| Laser 10.1.1.170:10001 | **IPG YLR-style ASCII protocol** (`EMON\r`, `EMOFF\r`, `ABN\r`, `ABF\r`, `SDC <n>\r`, …) — `CIPGProtocolAdapter`; Raycus variant `\x1bO\r`, `\x1bS\r`, `\x1bCP<n>\r` (`CRaycusProtocolAdapter`). Port 10001 is the IPG default. | CONFIRMED strings/bytes; vendor mapping = INFERENCE (high) |
| MonitorIP 47.104.17.21:9001 | Remote telemetry ("远程监控 / Remote Monitor"): JSON documents (`devId`, `hwModuleId`, `lastUpdateTime`, `status`, `lightVal`, `warnId`, `time`) posted with method names `updateBasicInfoByDevId`, `updateStatusByDevId`, `upsertWarnByDevId`, `clearAllWarnByDevId`, via MFC/WinInet HTTP (`Content-Type:application/json;charset=UTF-8`). Only when `SOP.EnableRemoteMonitor` is on. 47.104.17.21 is an Alibaba-Cloud (Qingdao) address. Privacy: machine ID, alarms, laser-on time, run status leave the LAN. | CONFIRMED strings/keys; transport details = INFERENCE (medium) |
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
`AccessType=1` in the shipped ini and log strings `MC-Sendto` / `MC-Recvfrom` ⇒ **the card is driven over UDP/502, one datagram per request and one per reply**. (WS2_32 ordinal map used by the DLL: 2 bind, 3 closesocket, 4 connect, 8 htonl, 9 htons, 11 inet_addr, 16 recv, 17 recvfrom, 18 select, 19 send, 20 sendto, 21 setsockopt, 23 socket, 52 gethostbyname, 111 WSAGetLastError, 115 WSAStartup, 116 WSACleanup, 151 __WSAFDIsSet.)

### 3.2 Frame format — **EVIDENCE** (`CExtModbus::encode` @`0x10027ce0`, `::encodeWithSeq` @`0x10028240`, `::decode` @`0x10029620`, `::decodeWithSeq` @`0x10029b40`; CRC helper tables @`0x10089440` (hi) / `0x10089540` (lo))

```
offset  size  field
0       2     sequence number, BIG-endian (per HAL instance counter, incremented before every transaction; 16-bit wrap)
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
| any other (default) | frame has no payload (len 0) | — | unsupported |
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
5. After the last recv try with timeout, `Sleep(MCSendInterval)` and repeat the whole send (until `MCMaxSendTime` exhausted). `LeaveCriticalSection`.
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
| **2000** | 50 (=5 axes × 10) | `updateMCStatusAxisRO` (`0x100518c0`, stride 40 B) | `AxisRORegName_1..10`: status, speed, pulse position, encoder position, Z-phase encoder pos, stop pulse, cumulative travel, calc. cumulative pulses, calc. cumulative encoder, cumulative pulses (11 cum. encoder, 12 encoder at stop) |
| **5000** | 1 | `updateMCStatusRW` (`0x10051b00`) | `RWRegName_1..5`: input type, output mask, e-stop input port, safety decel, safety jerk |
| 10000 | 18 | (`0x1004ea50`) | pulse-axis group? (`PulseAxisRORegName_1..7`) — INFERENCE low |
| 11000 | ? | `0x1003e0c0/0x1003e240/0x1004ed90` | |
| 13000 / 13200 | ? | `updateMCStatusRO` (`0x1004eff0`) / `0x1004f8e0` | |
| **50000** | 26 | `updateMCStatusSystemRW` (`0x10051f20`) | `SystemRWRegName_1..`: IP, subnet, gateway, baud rate, spare, bus cycle, Z-axis enable, DA1–3 calibration, bus-axis 1–16 input config, pulse-axis input config, comm timeout, special input cfg, dual-drive cfg/tolerance/alarm time, precision coeff, brake delay, input filter times… |
| **50200** | 100 (=5 × 20) | `updateMCStatusAxisRW` (`0x10051d00`, stride 80 B) | `AxisRWRegName_1..14`: config, +soft limit, −soft limit, accel, jerk, coarse-home speed, fine-home speed, origin offset, spare, lead (pitch), cmd pulses/unit, encoder pulses/unit, input cfg, output cfg |
| **59600 + i** (i ≤ 31), blocks of 0xD0 = 52 regs | `readParamFromCard` (`0x10050830`) / `writeHardParam2Card` (`0x100504c0`) | hardware parameter area (what `BkHardPara.xml` mirrors) |
| 60001 | 120 | (logged) | parameter/servo info block |
| **0x65 = 101** | n | command register (func 0x40) | see §3.6 |
| **0x66 = 102** | 1 + 3·items | FIFO data (func 0x40) | see §3.7 |
| **0x67 = 103** | ? | written in `mcCoreProcess` (`0x10058031`) | unknown command/ack register |

A second, older register list `RegName1..143` in `lang.txt` (product id, program version, input status, axis go-origin status, …, `RegName23 FIFO frame id`, `RegName50 FIFO operation flag`, `RegName79 IP address`, `RegName89 MCC3721 hardware version`, `RegName113 FIFO protocol flags`) belongs to the **MCC3721** predecessor card (`pd1732/pd1733 "MCC3721H/NA"`); the MCC100 ("EtherCAT" bus card, `EtherCATErrorInfo_*`) uses the grouped map above.

### 3.6 Command register 0x65 — sub-commands (vector written with func 0x40, addr 0x65)

Format: `count` u32 words, first word = sub-command. **EVIDENCE**: log `DataEx` dumps + the builders that reference `0x65` (`0x10031f10/0x10032160` jog, `0x1004ccb0` setDA, `0x10051370` clearFifo, `0x10056b10` VM init, `0x10047cc0` offline upload, `0x10057950` mcCoreProcess). Meaning = INFERENCE (medium–high):

| words | example (from logs) | meaning |
|---|---|---|
| `[1, axis, mode, slowSpeed, fastSpeed]` | `01 01 02 1388 c350` | go-origin (home) axis 1, mode 2, 5000 / 50000 pulse/s (`AxisRW_6/7` coarse/fine home speeds) |
| `[3, axis, vel, acc, jerk, dist]` | `03 01 c350 176f ea56 ±3d0900` | single-axis relative move (jog step): axis 1, v=50000 or 200000, a=5999, j=59990, d=±4 000 000 pulses (dir = sign); exception 3 when illegal (limit) |
| `[5, axisMask, vel, acc, jerk, dX, dY, dZ, dW]` | `05 80000003 86470 2327 15f86 69062 2a811 0 0` | multi-axis jog/move (built by `0x10032160`: `[5, mask, min(v,vmax), min(a,amax), 5·a, …]`); bit31 of mask = flag |
| `[0x66]` (single word 102) | `40 65 01 66` | FIFO-related one-shot (stop/flush?) |
| `[9999, 1, …]` | (clearFifo, `0x10051370`) | 9999 = "misc control" family: 1 = FIFO clear |
| `[9999, 2, ch, 0/1]` | `270f 02 01 00/01` | set digital output / laser gate (on/off) |
| `[9999, 4, ch, value]` | (setDA `0x1004ccb0`) | set DA (analog) channel |
| `[9999, 13, 0xFFFF, hwVerHi…]` | VM init `0x10057575` | handshake / version exchange at connect |
| `[7]`, `[1, 8]` | offline-upload paths in `0x10047cc0` | offline program upload start/stop |
| `[4, …]` | mcCoreProcess "start manu" | start processing (FIFO run) |

`startFifo` / `stopFifo` (`0x10050af0` / `0x10050dc0`, log `VM Start Fifo` / `VM Stop Fifo`) and `clearFifo` (`0x100510f0`) are further 0x65 writes.

### 3.7 FIFO streaming — job model (reg 0x66)

**EVIDENCE** (log frames `40 66 12a <id> …`, builder `0x100437d0`):
`[func 0x40][addr 0x66][count 0x12A = 298][frameId][297 words = 99 items × 3 words]` → 1192-byte payload, 1206-byte datagram. The frame id (`0x3c, 0x28e, 0x78b, …`) is repeated unchanged on retries and corresponds to `RORegName_16 "FIFO frame identification"`; the PC compares it with the card's FIFO frame id / `RORegName_17 "FIFO space margin"` to decide how many items to push (`fillFifo` @`0x10052390`: "Update MC Status Success: CalcBufferSize:%d, RealItemNum:%d", three-step fill with timing logs).

Item = 12 bytes, all observed values (one captured contour):
```
w0 = 0x0008_0BB8   (hi=8, lo=3000)      constant for the whole frame
w1 = int16 hi | int16 lo  e.g. (0,-3) (-1,-3) (0,-4) (-1,-4)   ← per-cycle pulse increments of two axes
w2 = 0x1388_0004   (hi=5000, lo=4)      constant
```
INFERENCE (medium): w1 = (ΔY<<16 | ΔX) pulses for one interpolation tick (bus cycle); w0/w2 carry laser/PWM & flags (3000 ‰ or 30.00 % power?, 5000 Hz PWM?, 8 & 4 = flag bits). The exact field semantics must be confirmed by capture with different power/frequency settings (see §9). Interpolation itself is done on the PC (`MotionCtrl.dll`: `segInterp`, `arcInterp`, `newVelocityPlanning`, `newContourSmooth`) — the card only executes pulse deltas, which explains `EtherCATErrorInfo_2_05 "FIFO starvation"` and the 1600 ms `MCFifoTime` look-ahead.

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

### 4.1 Standard Modbus TCP (`CStdModbus` @`0x100287f0`/`0x1002a040`) — **EVIDENCE**
MBAP `[trans=0][proto=0][len=2·n][unit=0]`, FC **3** (`[addr BE][count BE]` → `[bytecount][words BE]`) and FC **16** (`[addr][count][bytecount][words BE]`). Used with the function-code constants 3/16 (`0x1008a678/0x1008a67c`) by `init 3710 ExCard` (`0x10053bd0`) and several `0x1004b020…0x10050180` routines. This is the **EC3710 extension card at 10.1.1.170:502** — on Linux use `libmodbus`.

### 4.2 RTU-style framing over the card's sub-ports (`CExtCardModbus` @`0x10028d00`, `CSerialModbus` @`0x10028a00`) — **EVIDENCE**
`[unit 0x02][func][…][CRC16 lo][CRC16 hi]` with FC 3 (`addr BE, count BE`), FC 16 (`addr, count, bytecount, words BE`), FC 0x26 (`u32 addr LE, count BE, bytes`), decoder also accepts FC 0x26 replies (`u32 LE value + u16`). Used by `CAFNetHalAPI` (auto-focus head, `AFIP:888`) and `CECNetHalAPI` (extension axis card, `ECIP:888/666`) — the card acts as a gateway to those sub-devices (log prefixes `AF Send Buf:/AF Recv Buf:`, `EC Send Buf:`). Z-follower ("ZF"/FTC) status uses `syncReadZFReg`/`updateZFStatus`; the second `CExtModbus` instance (`0x100aba10`) is most likely the ZF channel (INFERENCE, medium). `CFTC61Modbus` (`0x10029000`, frames start `00 00 00 00 … 0x21`) serves the FTC61 height controller over serial/"MCC serial".

### 4.3 Laser source 10.1.1.170:10001 — **EVIDENCE**
`CLaserNetHalAPI` + `CIPGModbus` (byte pass-through) + `CIPGProtocolAdapter` builders (`0x10027270…0x100274b0`, `0x10027930`):
`"EMON\r"`, `"EMOFF\r"`, `"ABN\r"`, `"ABF\r"`, `"EMOD\r"`(bytes 45 4D 4F 44 0D), `"ELE\r"`, `"DLE\r"`, `"DEABC\r"`, `"EEABC\r"`, `"SDC<n>\r"` (set diode current = power %). These are the **IPG YLR/YLS fiber-laser Ethernet command set** (emission on/off, aiming beam on/off, enable/disable laser emission & external aiming-beam control, set diode current); port 10001 is IPG's default TCP port. `CRaycusProtocolAdapter` (`0x10027120…`): `ESC 'O' \r`, `ESC 'S' \r`, `ESC 'C' 'P' <n≤100> \r`, and an 18-byte frame with frequency 50..50000 (Raycus RFL serial protocol); vendor list in `lang.txt`: `pd47 锐科/Raycus, pd48 IPG, pd49 semiconductor, pd50 创鑫/MaxPhotonics, pd50-1 nLight, pd50-2 国志/GZ`. Only IPG and Raycus have adapters in this build. Laser alarms `gp44..gp55` decode a laser status word (temperature, interlock, power, …).

### 4.4 Remote monitor 47.104.17.21:9001 — privacy relevant
**EVIDENCE**: `CMonitorHalAPI` (`open` is a stub; `vf1` @`0x1000c9b0` sets header `Content-Type:application/json;charset=UTF-8` through mfc100u ordinals 1312/902), `vf6` @`0x100262d0` → JSON builder `0x10022820` (boost::property_tree → keys `reqBuVoStr`, `mapProps`, `lastUpdateTime`) → send with 300 ms (`0x12c`) timeout. `0x1004d380` prepares documents with keys `devId` (= `SP.MachineID` "远程监控.机床ID"), `hwModuleId`, `lastUpdateTime` (`%d-%02d-%02d %02d:%02d:%02d.%03d`), `time`, `lightVal` (laser-on time), `status`, `warnId` and calls the server methods `updateBasicInfoByDevId`, `updateStatusByDevId`, `upsertWarnByDevId`, `clearAllWarnByDevId` (log names `moctUpdateBasicInfo/moctUpdateRunStatus/moctUpdateWarnInfo/moctClearAllWarn`). Parameters: `SOP.EnableRemoteMonitor (pd533)`, `SOP.RemoteMonitorHeartbeat (pd534)`, `SP.MachineID/DataCardID/CommandID (pd518-520)`, test mode `pd870-873`. A binary variant (`CMonitorModbus::encode` @`0x10029200`: `A5 A5 <type> <BCD> <BCD date/time…>`) also exists. `MonitorReconnInterval=300000`. **INFERENCE (high)**: with `EnableRemoteMonitor=1` the machine id, run state, alarms and laser-on time are pushed to an Alibaba-Cloud host in China; block it on the Linux side (or simply don't implement it). Also `LogModule.dll`/`YaoUtil` can POST log text (`logID=…&logData=…&machineID=…&isText=…`, `%s %s HTTP/1.1`, form-urlencoded) to a configurable `DestUrl` — none configured in the package.

### 4.5 MainApp WINHTTP client — **EVIDENCE**
`CHttpClient` (`0x4060b0` WinHttpOpen("CHttpClient/1.0") + WinHttpConnect; `0x406940` POST `/NexCut/File/LoadFile` JSON; `0x407430` POST `/NexCut/File/UploadGCode` multipart (`A250606_1`: files < 500 MB); `0x434990` login `/NexCut/Login?Name=NexCut&Password=12345678`; header `Authorization: DebugWithSuperpermissions`). Host/port come from software parameters (`A241218_1/2/3` "Equipment type / number / address", `A241221_0` "Connect"), not from ipAdd.ini. This is an optional LAN/cloud "NexCut" job server (`NexCut_X1_Http` build). `SensApi.IsNetworkAlive` is used to gate it. `http://www.au3tech.cn/key/` is assigned to a CString in `0x4ba190` (next to reading `MonitorIP/MonitorPort` from ipAdd.ini) and shown in the activation dialog; no HTTP request is made to it.

### 4.6 Licensing and dongles
* **Card-bound licence** (`CDog`, `Log/Code.txt`): activation code ↔ "Hid" (hardware id read from the card), card RTC ("CardTime"), licence days, `UserCode` (=`CheckUserID 109`); states `dogState_*` (no hardware, unauthorized, clock illegal, data sector broken, trial over…). The data area is read/written through the register protocol (addresses not recoverable statically; 150/151 are special-cased in `readReg` — INFERENCE low). A Linux port must either reproduce this (needs live capture of the exchange) or keep the card's stored licence untouched.
* **USB HID dongle VID 3689 PID 8762** (`HID#Vid_3689&Pid_8762`, strings `iKey.`, `PWDKeyCo.`) is opened by **AutoNest.dll** (`Nest_CheckLock`, `Nest_EncodeByKey`; error code 1 "没找到加密狗 / dongle not found" in `排样内核错误代码.txt`). Only the automatic nesting feature depends on it.
* **Wireless pendant**: `PHBX.dll` = "chengdu XHC Tec." product **PHB02 / XHC PHBX** (`Xinit/XOpen/XGetInput/XSendOutput/XGetDevRssi/XGetChannel/SetGetKeyCallbackFunction`), HID over USB receiver; MainApp `CHidUsb` + `SetupDi*`/`HidD_*`. Pairing codes are card registers (`RegName40-46/90-96` LCR/USB adapter addresses & key values).

---

## 5. Firmware image `SRC/Update/MCC100_V201.52.mcf`
**EVIDENCE**: 120 456 bytes; dwords LE `[0x0001D688 (=file size), 0x00F8B0F4, 0x00000000, 0x32BA741B, …]`; Shannon entropy 7.998 bits/byte in every 4 KiB block; zero printable strings > 6 chars; no ARM vector table (no clusters of `0x0800xxxx`/`0x2000xxxx` words); CRC32/Adler/sum over any header-aligned range ≠ 0x00F8B0F4. ⇒ payload is encrypted (or compressed with a header CRC we cannot match). CPU architecture **unknown**. MainApp `CHardwareUpdateDlg` (`0x565d20`) picks `\Update\MCC100_V*.mcf` (and `\Update\E310_V80*.afb` for the AF head), checks version (`mf176/mf244` file errors, `mf179` "hardware will restart"), and NCModule streams it with FC 0x26 byte-blocks (`fillDownFile`). The dword at +4 is probably a checksum the card verifies (INFERENCE, medium).

---

## 6. Error / status vocabulary useful for a re-implementation (`lang.txt`)
* Connection: `mf0` controller connected, `mf5` connect failed, `mf172` controller not connected, `gp0` hardware not connected, `gp13` network alarm during processing, `gp96` network state abnormal, `mf1001` main controller type changed, `mp22/mp7/mp8/mp9/mp10/mp11` MCC Offline/Ready/Origin/Jog/Stop/Process, `mf242/243` monitor connected/offline, `mf225/226` remote monitor connect ok/failed.
* Card alarms `gp1..gp59` (servo input, encoder, dual-drive, hard/soft limits per X/Y1/Y2/W, e-stop, FPGA not registered/loaded, FTC alarms, laser alarms, chiller), `gp200..gp216` extension-card axis alarms, `gp217/218` ZF value/signal alarms.
* Upgrade: `mf174–179`, `mf232` (card fw too old, auto-upgrade), `mf432` (ZF), `mf701` (AF), `hp38` (params need card restart).

---

## 7. What is CONFIRMED vs GUESSED

**Confirmed (disassembly + logs agree):** UDP/502 transport and socket options; frame layout incl. seq/CRC/len/unit/func; CRC algorithm and PC byte order; function codes 0x30/0x40/0x26/0x20/0x21; exception mapping (`func|0x80`, `ErrCode=500+code`); retry/timeout state machine and all ErrCodes; ini keys and defaults; register group addresses (1000, 2000, 5000, 50000, 50200, 59600+) and counts; command registers 0x65/0x66; FIFO frame structure (frameId + 3-word items, 99 items/frame); laser ASCII commands; monitor JSON keys/method names; IPSet.exe behaviour; dongle usage; firmware being opaque.

**Guessed:** the semantic names of individual registers (lang.txt order ↔ address order), meaning of 0x65 sub-command arguments, FIFO item field semantics (dx/dy/power/PWM), reg 0x67, which physical channel the second `CExtModbus` instance serves (ZF), the monitor transport (WinInet via mfc100u ordinals), the exact handshake order, licence register addresses.

---

## 8. Implications for the Linux port

Must be replicated (no library does it):
1. **`CExtModbus` framing** over UDP (one datagram per request, seq echo check, CRC-16/MODBUS over `[len..end]` stored hi-byte-first, LE payload) with the 0x30/0x40/0x26 function codes and the retry policy (send ≤3, recv ≤2, `select` 500 ms, retry on stale seq).
2. The **status polling schedule** (1000 every cycle; 2000/5000/50000/50200 periodically) and decoding of alarm/status words (bit meanings still to be captured).
3. The **command register 0x65** vocabulary (home, jog, move, IO/DA/PWM via 9999,*, FIFO start/stop/clear, handshake 9999,13).
4. The **FIFO streamer**: build 12-byte items from the PC-side interpolator, send frames of ≤99 items to reg 0x66 tagged with a monotonically increasing frame id, throttle on `FIFO space margin`, keep ≈1.6 s of motion queued.
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
* Register 0x67 and the single-word `[0x66]` command.
* Which device the second `CExtModbus` instance (0x100aba10) and the standard-Modbus instance (0x100aba18) really talk to (ZF at 10.1.1.169:502? on-board ZF at :999?).
* Whether the card also accepts TCP (AccessType=0 path exists) and whether the reply CRC (lo-first) implies the card *does* verify request CRCs in the PC's (hi-first) order.
* Licence data-area registers and the card-clock set/read commands.
* Firmware container format (`.mcf`): cipher/compression and the meaning of the 0x00F8B0F4 header word; CPU type of the MCC100.
* mfc100u ordinals 1312/902 used by the monitor HAL (CInternetSession/CHttpFile?) — confirm by capture of port 9001 traffic.
