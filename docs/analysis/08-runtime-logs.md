# 08 – Runtime behaviour reconstructed from logs and reports

Analyst scope: `Log/2025-06-25.log … 2025-07-18.log` (+ `Log/2026-09-11.log`), `Log/Code.txt`, `Log/VelDecc.txt`,
`Report/TotalReport.txt`, `Report/LogReport.txt`, `Report/report.txt`, `Report/lang.txt`, `File/ProcessesStatistic.txt`,
`File/Temp/tempIsBreak.ini`, `File/AutosaveParam*.ini` (and, for cross-checking, `File/ipAdd.ini`, `File/softPara.ini`,
`File/BkLayerPara.xml`, `Lang/lang.txt`, `Update/`). All paths below are relative to
`SRC = /home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52`.

Conventions: **EVIDENCE** = directly observed in a file (quoted with path/line); **INFERENCE** = my interpretation, with a
confidence tag `[confirmed]` (two independent sources agree), `[likely]`, `[guess]`. Where this document touches the
wire protocol it cross-references `04-controller-protocol.md` (disassembly-based) and only adds what the *runtime*
artefacts prove or contradict. Chinese strings are quoted verbatim with an English rendering.

---

## 0. Executive summary

* `Log/*.log` is **not** a general application log. It is the *communication-error* log of the MCC100 card driver
  (`NCModule.dll`, `CMCHalAPI::writeMCLog`): 1 794 lines over 15 days, only five message types, all `[Info]`, all ASCII.
  It records (a) every application start (`NC Start`) and (b) every UDP transaction that failed – timeouts, card
  exception replies, socket errors. Successful traffic is never logged (`ipAdd.ini [Soft] EnableLog=0`). Consequently the
  logs are a *negative image* of the protocol: they leak the request vectors (`DataEx:`) of the commands that failed,
  including whole 1 206-byte FIFO motion frames.
* `Log/Code.txt` is the **licence ("dog") log**: a card-bound, time-limited licence scheme with activation codes,
  a hardware id, the card's real-time clock and a "data area" on the card. It shows three activations
  (7-day, 3-day and **180-day** licences), repeated failures, and the technician setting the PC clock to the year 2055
  to work around a clock check. The last activation (2025-06-17, 180 days, `UserCode:999`) expires around
  **2025-12-14** – relevant to why the Windows software may stop working.
* `Log/VelDecc.txt` is empty (0 bytes, dated 2024-06-19).
* `Report/TotalReport.txt` is the cumulative **work report**: 2 950 CSV rows, 2024-07-25 → 2025-07-18, two row formats
  (7 fields until 2025-03-06, 10 fields from 2025-03-10). The times in it are the planner's *estimates*
  (identical for repeated runs of the same drawing), the timestamp is the job **end time** (`gp135 加工完成时间`).
  The machine was used almost exclusively for **short test cuts of unsaved drawings** (99.6 % `未命名-1` / "Untitled-1",
  median job 4 s, 30×25 mm squares and 7 mm-high strips) – a factory/dealer test profile, not production.
* The start-up handshake, the reconnect handshake, the jog/home/multi-axis command vectors with real parameter values, the
  FIFO stream grammar (a TLV item list, not fixed 12-byte items), the PWM frequency/duty encoding inside the stream, the
  retry ladder (7 datagrams, 500 ms apart) and the real-world failure modes (card unreachable, card "deaf" for 3.5 s on
  register block 10000, jog rejections while moving) are all reconstructed below with timestamps.

---

## 1. Inventory of the analysed artefacts

| File | Size | Encoding / format | Content (short) |
|---|---|---|---|
| `Log/2025-06-25.log` … `2025-07-18.log` (15 files) | 114 B – 85 KB, 316 KB total | ASCII, CRLF, one line per event | MCC100 driver error log (§2) |
| `Log/2026-09-11.log` | 114 B | same | one `NC Start` at `2026-09-11 17:50:32.506` – written *on this Linux PC* (the file is newer than the 2025-07-29 copy date of everything else), i.e. somebody launched `MainApp.exe` (Wine) from this directory; the app writes `Log\<date>.log` next to the exe even without a card |
| `Log/Code.txt` | 88 860 B, 1 252 lines | ASCII, CRLF | licence/activation log 2024-06-19 → 2025-06-17 (§5) |
| `Log/VelDecc.txt` | 0 B (2024-06-19) | – | empty; name suggests a velocity/deceleration dump that was never written |
| `Report/TotalReport.txt` | 270 817 B, 2 950 lines | UTF-8 (Chinese `未命名`, `分`, `秒`, `×` decode as UTF-8; **not** GBK – `file` says "CSV Unicode text, UTF-8") | cumulative work report (§6) |
| `Report/LogReport.txt` | 4 795 B, 40 lines | UTF-8 | work-report *export/print* log: `<write time>  <row>` (§6.3) |
| `Report/report.txt` | 198 B, 2 lines | UTF-8 | the rows handed to `report.exe` for the last "Work Report [New]" (§6.4) |
| `Report/lang.txt` | 7 B `lang==0` | ASCII | language switch for `report.exe` (0 = Chinese; MainApp has `lang==0`/`lang==1` strings) |
| `Report/report.exe` | 45 MB, Qt5 static build (`QDateTimeParser`, `QHeaderView` symbols, `csvgdsvgz` MIME list) | – | the report viewer/printer (out of scope; only its inputs matter) |
| `Report/*.jpg` | 800×800 previews `111.chf.jpg`, `222.chf.jpg`, `rpt.chf.jpg`, `Untitled-1.jpg`, `未命名-1.jpg`; 838×709 `广告.jpg` ("advertisement") = `1111.jpg` | JPEG | job thumbnails rendered for the report, plus a vendor banner |
| `File/ProcessesStatistic.txt` | 53 B | **GBK** (`ceb4 c3fc c3fb` = 未命名), LF | last-job statistics, one line (§6.5) |
| `File/Temp/tempIsBreak.ini` | 19 B | `scFlie` container | break-point flag (§6.6) |
| `File/AutosaveParam1.ini`, `2.ini`, `File/Temp/AutosaveParam1.ini`, `2.ini` | 40–41 B | `scFlie` container | break-point records (§6.6) |

Timezone: all timestamps are PC local time. The card-clock lines in `Code.txt` (`2024-11-11 14:06:14` local ↔
`…6:06:14` stored) show the PC ran on **UTC+8 (China Standard Time)** during the whole logged period – the machine was
logged at the manufacturer/dealer, not in Norway.

---

## 2. `Log/YYYY-MM-DD.log` – format and message inventory

### 2.1 Line grammar (EVIDENCE)

Every line matches

```
<YYYY-MM-DD HH:MM:SS.mmm> [Info] -> <message>
```

`[Info]` is the only level that ever occurs (1 794/1 794 lines). Files are created per calendar day, first line is always
the `NC Start` banner of the first application start of that day. There is no header, no rotation, no session id.

`<message>` is one of exactly five templates (counts over the 15 files of 2025; the 2026 file adds one `NC Start`):

| # | Template (verbatim) | Count | Meaning |
|---|---|---|---|
| 1 | `---------------------------------NC Start-------------------------------------` | 112 (+1 in the 2026 file) | NC module (card driver) initialised = application start / reconnect via "Reconnect" (`gp89 硬件重连`) |
| 2 | `MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):r/s Data: Buffer: DataEx:<request words>` | 1 072 | `select()` on the UDP socket timed out (`WSAETIMEDOUT` 10060) – **no reply from the card** within `MCTimeout=500 ms` |
| 3 | `MC-RecvDataErr ErrCode:5xx Try-Times(R/S):1/1 Data:<reply words> Buffer:<reply frame hex> DataEx:<request words>` | 603 | the card answered with a **Modbus-style exception** `func|0x80, code` → `ErrCode = 500 + code` (503 ×567, 502 ×36) |
| 4 | `MC-Sendto ErrCode:10065 Try-Times(R/S):0/1 Data:<request words> Buffer:<request frame hex>` | 5 | `sendto()` failed with `WSAEHOSTUNREACH` 10065 – no route to 10.1.1.168 (NIC down / not configured on 10.1.1.x / cable) |
| 5 | `MC-Recvfrom ErrCode:10038 Try-Times(R/S):2/3 Buffer: DataEx:<request words>` | 2 | `recvfrom()` failed with `WSAENOTSOCK` 10038 – the socket was closed by another thread while a receive was pending (application shutdown / reconnect) |

Field semantics (EVIDENCE from the values, consistent with the decoder description in `04-controller-protocol.md` §3.3):

* `Try-Times(R/S):r/s` – receive attempt / send attempt counters. `Sendto` is logged with `0/1` (failed before any
  receive). Exceptions are always `1/1` (first reply to the first send). Timeouts step through the ladder of §2.3.
* `Data:` – the decoded reply vector as 32-bit words (hex, no `0x`): for exceptions `000000c0 00000003` = function
  `0xC0` (= `0x40|0x80`) + exception code 3; `000000b0 00000002` = `0x30|0x80` + code 2. For `Sendto` it is the request
  vector instead.
* `Buffer:` – the raw datagram, one hex byte per token. Reply frames are 9 bytes, the `Sendto` request 14 bytes.
* `DataEx:` – the *request* vector as 32-bit words in hex **without leading zeros** (`30 2710 12` = `[0x30, 10000, 18]`).
  This is the field that leaks the protocol; it is present on every timeout and exception line.

### 2.2 The five distinct raw frames (`Buffer:`) and their decode

| Occurrences | `Buffer:` | Decode (per `04-controller-protocol.md` §3.2, verified here) |
|---|---|---|
| 5 (all `Sendto`) | `00 00 8c 75 00 08 00 30 e8 03 00 00 02 00` | seq `0x0000` (fresh socket), CRC `8c 75`, len `0x0008`, unit `00`, func `0x30` READ, addr `e8 03 00 00` = 1000 (u32 LE), count `02 00` = 2 (u16 LE). **CRC-16/MODBUS over `00 08 00 30 e8 03 00 00 02 00` = 0x8C75, stored hi-byte first** (recomputed: poly 0xA001 reflected, init 0xFFFF – brute-forced against all four frames, only this parameterisation matches) |
| 567 | `ss ss 34 45 00 03 00 c0 03` | reply: seq (big-endian, e.g. `1d 3f`, `71 5a`, `71 66` – increments), CRC bytes `34 45` = **0x4534 stored lo-byte first** (CRC-16/MODBUS of `00 03 00 c0 03` = 0x4534), len 3, unit 0, func `0xC0` = exception to `0x40`, code `03` |
| 34 | `ss ss d0 45 00 03 00 b0 02` | exception to `0x30` (`0xB0`), code `02`; CRC of `00 03 00 b0 02` = 0x45D0 stored lo-first |
| 2 | `ss ss f5 85 00 03 00 c0 02` | exception to `0x40`, code `02`; CRC 0x85F5 lo-first |
| (timeouts) | empty | – |

So the PC writes the CRC hi-first and the card writes it lo-first (standard Modbus RTU order); both are CRC-16/MODBUS
over `[len][unit][func][payload]`. `[confirmed]` (matches the disassembly in doc 04 and all 604 frames here).

Sequence numbers in the exception replies are big-endian and grow with the traffic rate: e.g. `1c 69 → 1c 73 → 1c 7e →
1c 8a` within 0.7 s on 2025-07-07 09:13:10 = 33 transactions/0.7 s ≈ **47 transactions/s** while idle-jogging; this is
the polling rate implied by `MCCore=30` ms (`ipAdd.ini`) plus the jog traffic. `[likely]`

### 2.3 The retry ladder (EVIDENCE, 1 072 timeout lines)

Within one failed transaction the log always shows the same sequence of `Try-Times(R/S)` labels, 500 ms apart
(`MCTimeout=500`):

```
1/1, 1/2, 2/2, 3/2, 1/3, 2/3, 3/3        → 7 datagrams, 3.5 s, then the transaction is abandoned
```

Example `2025-07-17 14:51:56.410 … 14:51:59.432` (READ 1000 n=2) and immediately again `14:52:02.932 … 14:52:05.940`
(the caller retried the whole transaction after 3.5 s). Interpretation `[likely]`: outer loop = send attempt
(labelled `s` = 1..3), inner loop = receive attempts (`r`), where the first send gets one receive try and the later sends
three (`MCMaxRecvTime=3`). `MCMaxSendTime=2` in `ipAdd.ini` therefore means "2 *re*-sends". Bursts shorter than 7 (x1,
x2, x3, x4, x5 – 58 of the 197 bursts) are transactions that succeeded on a later attempt; e.g. `2025-07-12 15:10:29.798
x4` shows labels `1/1, 1/2, 2/2, 3/2` then success. A reply that finally arrives is not logged.

Distribution of timeout bursts by request (197 bursts):

| Request (`DataEx`) | bursts | of which full (7 tries) | isolated (no other error ±60 s) | Note |
|---|---|---|---|---|
| `30 2710 12` READ 10000 n=18 | 83 | 76 | 60 | see §4.3 – the card regularly does not answer this block |
| `30 3e8 24` READ 1000 n=36 | 26 | 20 | – | status block (`checkMCStatus`) |
| `30 ea61 78` READ 60001 n=120 | 22 | 10 | – | parameter block |
| `40 66 12a …` FIFO frame | 22 | 0 (20 single-try `1/1`, 2 escalated `1/1,1/1,1/2,2/2,3/2`) | – | only on 2025-07-13 09:09 and 2025-07-17 (§4.4) |
| `30 c350 1a` READ 50000 n=26 | 19 | 10 | – | system RW block |
| `30 3e8 02` READ 1000 n=2 | 19 | 18 | – | first request after `NC Start` (§3) |
| `40 65 04 270f 02 01 xx` CMD 9999,2,1,0/1 | 5 | 4 | – | set output 1 off/on around job start (§4.2) |
| `30 41a 03` READ 1050 n=3 | 1 | 1 | – | 2025-07-11 10:47:24 |

---

## 3. Start-up and (re)connect sequence as evidenced

The logs only show what *failed*, so the sequence is reconstructed from the days on which the card was off or
unreachable, where every step timed out in order. Times are wall-clock; `Δ` is measured from `NC Start`.

### 3.1 Application start with the card powered off – `2025-06-28.log` (complete file)

```
08:28:08.794  NC Start                                   (1st start; nothing else – user closed it?)
08:28:34.608  NC Start                                   (2nd start, Δ0)
08:40:22.947 … 08:40:25.956  READ 1000 n=36  ×7 timeouts (Δ +11m48s – user pressed Reconnect / opened a dialog)
08:40:26.458 … 08:40:29.466  READ 50000 n=26 ×7 timeouts
08:40:29.969 … 08:40:32.977  READ 60001 n=120 ×7 timeouts
```

The same triplet `1000/36 → 50000/26 → 60001/120`, each 3.5 s, appears on 2025-07-09 14:44:22/14:44:40/14:45:43,
15:21:06, 15:27:07, 15:38:55, 15:41:45; 2025-07-12 16:11:00, 16:16:42, 16:19:54; 2025-07-14 09:19:44, 10:03:55,
10:04:55; and with a 4th member `READ 10000 n=18` inserted before 60001 on 2025-07-12 19:14:13 and 2025-07-13 09:30:58
(`1000/36 → 50000/26 → 10000/18 → 60001/120`). INFERENCE `[confirmed]` with doc 04 §3.8: this is
`checkMCStatus` (status block 1000, 36 regs) → `updateMCStatusSystemRW` (50000, 26 regs) → parameter block 60001
(120 regs), i.e. **"connect / verify card / read parameters"**, executed on start-up and on every Reconnect.

### 3.2 Application start with the NIC not on 10.1.1.x – the `Sendto 10065` cases

```
2025-07-07 08:58:48.385 NC Start
2025-07-07 08:59:02.710 MC-Sendto ErrCode:10065 … Data:00000030 000003e8 00000002   (Δ +14.3 s)
2025-07-07 09:07:32.194 NC Start                                                    (user fixed network, restarted)
```
Identical pairs: 2025-07-14 14:47:17→14:47:21 (Δ4 s), 17:22:31→17:22:38 (Δ7 s); 2025-07-17 15:05:18→15:05:21 (Δ3 s);
2025-07-18 09:43:03→09:43:08 (Δ5 s). In all five the **very first datagram of a session is `READ 1000 count 2`**
(program id + program version, compared against `MinHardwareVer=20152`). It is also the request that times out first
after a start when the card is merely unpowered (`2025-07-10 08:38:57 NC Start → 08:39:06 READ 1000 n=2 ×7`, `10:52:55 →
10:52:57`, `10:54:07 → 10:54:11`, `10:57:32 → 10:57:35`, `10:57:44 → 10:57:50`, `11:11:40 → 11:11:44`; 2025-07-11
08:30:08 → 08:30:20, 10:32:53 → 10:32:56, 10:40:42 → 10:41:14, 14:32:34 → 14:32:56; 2025-07-12 08:43:42 → 08:43:53;
2025-07-18 09:44:13 → 09:44:23). Δ between `NC Start` and the first datagram is 3–30 s = the time MainApp needs to
load its modules/UI before the NC thread starts talking (`ConnectWait=500` is not the dominant term). `[confirmed]`

`Sendto 10065` (host unreachable) means the Windows IP stack had no route/ARP to 10.1.1.168: NIC disabled, cable out,
or the PC address not yet set (the package ships `File/IPSet.exe` and `pingMC.bat`/`pingZF.bat` for exactly this).

### 3.3 Reconnect after a lost poll – the `0x65 ← [102]` handshake

```
2025-07-09 16:39:41.319 … 16:39:44 READ 10000 n=18 ×7 timeouts
2025-07-09 16:39:44.330 MC-RecvDataErr ErrCode:502 … DataEx:40 65 01 66          (write reg 0x65 ← [0x66])
2025-07-12 17:37:49.933 … 17:37:52 READ 10000 n=18 ×7
2025-07-12 17:37:52.944 ErrCode:502 DataEx:40 65 01 66
2025-07-12 17:37:53.063 ErrCode:502 DataEx:30 3e8 02  (×2)                        (READ 1000 n=2 = version check again)
2025-07-12 17:42:22 READ 10000 ×4, 17:42:23.592 502 READ 10000 n=18, 17:42:24.942 502 READ 1000 n=2, 17:42:39 NC Start
```
INFERENCE `[likely]`: after a poll fails the driver enters a re-initialisation path: single-word command `102` to the
command register (doc 04 calls it a "FIFO-related one-shot"; here it is clearly used as a *communication reset / re-sync*
before the version read), then `READ 1000 n=2`. The card answered both with exception 2 ("illegal data address") – the
card was in a state where even the status block is refused (booting, or firmware in a fault state), and shortly
afterwards the operator restarted the application (`NC Start` at 17:42:39).

### 3.4 What the logs do *not* show

* No line about the USB HID dongle, `PHBX.dll` pendant, laser (10.1.1.170), ZF/AF/EC sub-devices, monitor
  (47.104.17.21:9001) or HTTP client: these channels have their own prefixes in NCModule (`ZF-`, `AF-`…, doc 04) but
  none was ever logged – either they are disabled on this CO2 configuration or they never failed. `LaserControlType`
  on this machine is IO/PWM (doc 01), so no laser network traffic is expected.
* No firmware version string, no IP negotiation: the version read (`READ 1000 n=2`) succeeds silently. The only version
  facts are `Update/MCC100_V201.52.mcf` (dated 2025-05-26 – installed shortly before the June activation) and
  `MinHardwareVer=20152`.
* The licence check is logged in `Code.txt`, not here (§5).

---

## 4. Job-run sequence and the motion stream

### 4.1 Timeline shape of a working day

`NC Start` count per day and the report rows show the rhythm (2025-07 only; `Report/TotalReport.txt` rows are *finished*
jobs, `end time` field):

| Day | `NC Start` | first start | last log line | jobs finished | job end times | Σ est. process time |
|---|---|---|---|---|---|---|
| 06-25 | 2 | 18:13 | 19:27 | 11 | 11:07–18:46 | 543 s |
| 06-26 | 2 | 08:32 | 10:49 | 49 | 08:35–10:43 | 388 s |
| 06-28 | 2 | 08:28 | 08:40 | 0 | – | – |
| 07-01 | 1 | 10:43 | 10:43 | 0 | – | – |
| 07-06 | 7 | 09:52 | 16:27 | 78 | 09:55–14:37 | 457 s |
| 07-07 | 7 | 08:58 | 19:26 | 135 | 09:18–19:26 | 673 s |
| 07-08 | 5 | 08:37 | 20:35 | 119 | 08:44–19:08 | 586 s |
| 07-09 | 9 | 09:08 | 21:51 | 218 | 14:48–21:56 | 3 299 s |
| 07-10 | 16 | 08:36 | 20:58 | 160 | 08:50–21:21 | 1 317 s |
| 07-11 | 15 | 08:30 | 20:54 | 177 | 14:40–20:11 | 963 s |
| 07-12 | 13 | 08:43 | 19:23 | 181 | 08:54–19:30 | 904 s |
| 07-13 | 6 | 08:44 | 15:54 | 62 | 09:02–15:03 | 391 s |
| 07-14 | 10 | 09:16 | 18:27 | 207 | 10:14–18:16 | 1 112 s |
| 07-17 | 12 | 13:30 | 20:13 | 37 | 15:23–17:31 | 339 s |
| 07-18 | 5 | 09:43 | 15:49 | 113 | 09:56–14:24 | 617 s |

Jobs are short (median 4 s) and dense (median gap between consecutive job ends in July 2025 = 46 s, quartiles 18 s /
113 s): an operator repeatedly cutting the same small test drawing, adjusting parameters, re-running.

### 4.2 Commands seen around a job (from exception/timeout lines)

The only *job-related* commands that ever failed are these (all 0x65 writes and FIFO frames):

* `40 65 04 270f 02 01 00` = `[9999, 2, 1, 0]` and `… 01 01` = `[9999, 2, 1, 1]`: "set digital output 1 → 0 / 1".
  Timed out (×7) at `2025-07-11 15:10:12` (+9 s after a 36-s job started, per §6.2 timing), `2025-07-13 09:09:29`
  (0.6 s after a FIFO frame timed out), `2025-07-14 10:17:03` (1 s after a job ended), `15:15:30` (14 s after),
  `2025-07-18 15:49:24` (`…,1,1`, after a READ 10000 burst). INFERENCE `[likely]`: output 1 is switched at job
  start/end (gas/blower or "processing" lamp; `DOLaserGate=5` so it is not the laser gate).
* FIFO frames `40 66 12a <frameId> …` (§4.4).
* Home (`[1, axis, 2, vSlow, vFast]`) and jog (`[3, axis, v, a, j, target]`) rejections cluster in the seconds *before*
  a job (positioning the head) and *after* a job; §4.5.

### 4.3 Status polling and the "deaf 10000 block"

`READ 10000 n=18` produced 83 timeout bursts (549 lines, 51 % of all timeouts), 60 of them *isolated* – no other
request failed within ±60 s, and the 30-ms status poll of block 1000 (which runs thousands of times per minute) did not
fail. Typical spacing: 10–40 min (e.g. 2025-07-13 10:10, 10:26, 10:29, 11:02, 11:26, 13:28, 13:50, 13:52, 14:21,
14:53). INFERENCE `[likely]`: block 10000 is not part of the fast poll; it is read occasionally (or on a UI event), and
the card sometimes needs > 3.5 s to serve it (e.g. it queries a slow sub-device – doc 04 tentatively calls it the
"pulse-axis group"). It is harmless: the driver gives up after 7 tries and the machine keeps working (jobs finish
normally before and after). A Linux port should treat block 10000 as *optional/slow* and never block the core loop on it.

### 4.4 The FIFO motion stream – what the leaked frames prove

22 complete frames leaked on 2025-07-13 09:09:28 and 2025-07-17 13:32–15:21 (each logged because the card did not
acknowledge it within 500 ms). Frame layout (EVIDENCE): `40 66 12a <frameId> <297 words>` = write 298 words to register
0x66: word 0 = frame id, then **297 data words**. Frame ids are a **per-session counter** (reset by `NC Start`: 504, 523,
633, 643, 654 … on the 13:31 session; 80, 87 after the 13:51 restart; 57, 115 after 14:22; 1931 on 2025-07-13 25 min
into the session), incremented by the number of frames actually sent (504→523 in 2.2 s ≈ 8.5 frames/s ≈ 850 items/s
while refilling after timeouts).

**The 297 words are not 99 fixed 12-byte items** (as assumed in doc 04 §3.7 from a single frame). They are a
**TLV item list**: header word = `(payloadBytes << 16) | opcode`, followed by `payloadBytes/4` argument words. Parsing all
22 frames with that rule consumes exactly 297 words in every frame (no remainder) and yields these opcodes:

| opcode (dec) | header | args | occurrences | observed args | INFERENCE (confidence) |
|---|---|---|---|---|---|
| **3000** | `0x00080BB8` | 2 | 2 952 | arg0 = two int16 `(dY<<16 \| dX)` in −4…+8; arg1 = `(freq<<16) \| duty`: `0x13880004` (5000, 4), `0x13880000` (5000, 0), `0x07D00064` (2000, 100) | **interpolation tick**: axis increments for one bus cycle + laser PWM frequency (Hz) and duty (%) for that tick. `[confirmed]`: the (5000, 4) frames belong to the 200×200 mm test cut at 49.7 mm/s (`Report` row 2025-07-17 15:23:36: 1.65 m in 33.18 s) and the current `BkLayerPara.xml` CO2 layer with `CutSpeed="50"` has `CutFreq="5000" CutDuty="4"`; duty 0 = laser off (rapid moves, ramps); (2000, 100) on 2025-07-13 = a layer with 2 kHz / 100 % |
| 3001 | `0x00000BB9` | 0 | 6 | – | segment boundary / sync marker `[likely]` |
| 3002 | `0x00040BBA` | 1 | 3 | `5`, `4` | segment type/mode marker (5 before laser-on, 4 after laser-off) `[guess]` |
| 9999 | `0x000C270F` | 3 | 5 | `[2, 4, 4]`, `[2, 0x100, 0x100]`, `[2, 0x100, 0]` | same "misc 9999" family as the 0x65 command: sub 2 = set outputs, `(mask, value)` bit-wise: bit 2 (value 4) and bit 8 (0x100) switched on before cutting, bit 8 off after `[likely]` (laser enable / gas valve) |
| 103 | `0x00080067` | 2 | 2 | `[1000, 0]` | dwell 1000 ms before laser on (`LaserOnDelay`/pierce delay) `[guess]`; 103 = 0x67, the register doc 04 saw written in `mcCoreProcess` |
| 109 | `0x0008006D` | 2 | 1 | `[1000, 0]` | dwell 1000 ms after laser off `[guess]` |
| 2001 | `0x000807D1` | 2 | 3 | `[0x03000002, 20000]` | laser/PWM configuration (mode bits, 20000 = ?) `[guess]` |
| 118 | `0x000C0076` | 3 | 1 | `[4, 0, 0x23]` | end-of-contour bookkeeping (contour index 35?) `[guess]` |

Reconstructed **start of a contour** (frame 504, 2025-07-17 13:32:19; identical in frame 57, 14:22:17):

```
3000(0,1|5000/0) … 3000(0,0|5000/0)×16      rapid move to the start point, decelerating to rest, laser off
3001 ; 3002[5] ; 3000(0,0|5000/0) ; 3001    markers
9999[2, 4, 4]                               output bit 2 on
103[1000, 0]                                wait 1 s
2001[0x3000002, 20000]                      configure laser/PWM
9999[2, 0x100, 0x100]                       output bit 8 on
3000(0,0|5000/4) ×13 …                      stationary ticks with duty 4 % = PIERCE / laser-on dwell
(next frame 523) 3000(1,-1|5000/4) …        cutting moves at 5 kHz / 4 %
```
Reconstructed **end of a contour** (frame 633, 13:38:49):
```
3000(0,0|5000/4)×38, (0,-1), (0,0)×16       decelerate to rest with laser still on
3001 ; 9999[2, 0x100, 0] ; 109[1000, 0] ; 2001[…] ; 118[4, 0, 0x23] ; 3001 ; 3002[4]
3000(0,0|5000/0)×21, (0,1), …               rapid to the next contour, laser off, S-ramp
```
The 1 s dwells and the stationary laser-on ticks are what the report calls 穿孔时间 "pierce time" (§6.2: `t3` = 0.7–1.8 s
for pierce count 1) and 系统延时 "system delay".

Tick increments: cutting at the 50 mm/s layer shows |(dX,dY)| ≈ 2.1–2.2 units per tick (frames 1191/1199); rapids reach
(3,7)–(4,7) ≈ 8 units per tick (frames 739, 212); S-curve ramps are visible (frame 87: 0 → (3,4) over 99 ticks; frame
115: (0,2) → (0,8) over 99 ticks). The physical unit of one increment and the tick period cannot be fixed from these
files alone (open question §9); doc 04's estimate of a 1-ms bus cycle with ~1.6 s of look-ahead (`MCFifoTime=1600`)
is consistent with 8.5 frames/s refill bursts.

**Retry policy for FIFO frames differs from register reads** (EVIDENCE: the 30 FIFO timeout lines): a frame that is
not acknowledged is logged once with `1/1` and the streamer simply continues with later frame ids (1f8 → 20b, 279 → 283
→ 28e within 1.3 s); only when the same frame id is sent again (28e on 13:38:50–52, 3c on 15:04:19–21) does the
`1/1, 1/1, 1/2, 2/2, 3/2` ladder run, after which the job is dead (the operator restarted the application 80 s later).
INFERENCE `[likely]`: streaming writes use `MaxRecvTime = 1` and rely on the card's FIFO-frame-id register to detect
gaps; a re-send of a specific frame is the resynchronisation attempt.

**Consequence of a lost FIFO frame:** every day on which FIFO frames timed out has *no* report row for the job that
was running (2025-07-13 09:09 – the frame belongs to a run between the jobs that ended 09:09:19 and 09:12:57;
2025-07-17 13:30–15:22 – 12 frames lost, 0 jobs finished, 9 application restarts, then the first finished job at
15:23:36 once the network was stable). INFERENCE `[confirmed]`: an unacknowledged FIFO frame ends the job (FIFO
starvation alarm `EtherCATErrorInfo_2_05`, doc 04), and only finished jobs are written to the report.

### 4.5 Jog / home rejections (`ErrCode:503`, 567 lines)

All 567 exception-3 replies are answers to command-register writes:

| Vector | count | decode |
|---|---|---|
| `[3, axis, v, 5999, 59990, target]` | 506 | single-axis move: axis 0 (X) 225×, axis 1 (Y) 277×, axis 4 (W/lift, `v` 50000/100000, `a` 4000, `j` 40000, target ±1 000 000) 4×; `v` = 50000 (slow jog, 231×) or 200000 (fast jog, 358×) |
| `[1, axis, 2, vSlow, vFast]` | 60 | homing: axis 1 with (2000, 20000) 13× / (5000, 50000) 21×, axis 2 (2000/20000) 11× / (5000/50000) 8×, axis 31 (= all axes, bit mask 0b11111) (2000, 20000) 7× |
| `[5, 0x80000003, 550000, 8999, 89990, 430178, 174097, 0, 0]` | 1 | two-axis move X+Y (mask bits 0,1; bit 31 = flag), v 550 000, a 8999, j 89990, to (430.178, 174.097) mm – 2025-07-08 13:24:33, i.e. "go to point" / return to start |

Units: the `target` argument is an **absolute position in 0.001 mm** `[likely]`: values range −1 378 393 … +1 795 859 for X
and −560 652 … +933 009 for Y, `softPara.ini` stores the last position as `XAxis=1020277 YAxis=175510` (same scale), and
in a burst the targets shrink monotonically while the head approaches the key-held direction (2025-07-07 09:13:10.504 →
09:13:11.755: X target 121 571 → 112 345 → 99 968 → 80 156 → 15 388 within 1.25 s at v = 200 000 → 200 mm/s ⇒ velocity
unit 0.001 mm/s and accel 5 999 ≈ 6 mm/s² ×1000? – see open questions). `±4 000 000` (= ±4 m, 158 lines) is the
"continuous jog until key release" form. Speed constants 50 000 / 200 000 = `ManuPara` slow/fast jog speeds
(50 and 200 mm/s), 5999/59990 = accel/jerk parameters.

Why are they rejected? The bursts come at the keyboard/pendant auto-repeat rate (180–250 ms) and always while an axis is
already moving; the report timestamps show them 4–15 s *before* a job end minus its duration (= right before pressing
Start) or seconds after a job. INFERENCE `[likely]`: exception 3 ("illegal data value") is the card's answer to a new
absolute move while a move on that axis is still executing (or to a homing request while another axis is homing –
the 2025-07-17 15:09:29 burst alternates `home 1` / `home 2` 24 times in 3 s). The first press of a key succeeds (never
logged); every auto-repeat during the motion is refused and logged. There is no evidence of soft-limit rejections
(targets up to 1 795 mm on a 1 300 mm X axis were sent, e.g. 2025-07-13 09:11:36, and rejected with the same code, so the
code does not discriminate). For the Linux port: **do not resend a jog target while the axis reports "moving"; expect
and swallow exception 3**.

---

## 5. `Log/Code.txt` – the licence log

### 5.1 Format

Three line shapes (all ASCII, CRLF, month **1-based** in `LocalPcTime:` and `Active` lines but **0-based** in the
`Check code Error` line – a `tm_mon` bug; `2025-0-13` = 2025-01-13, `2024-9-31` = 2024-10-31 – which is also why
`CardTime:2000-0-1 0:0:0` means "2000-01-01, RTC never set"):

```
LocalPcTime:<Y-M-D h:m:s> - <message>
<Y-M-D h:m:s>  Active codeStr:<20 upper-case letters>
Active CodeStr data: Hid:<u32> CodeTime:<Y-M-D h:m:s> LincensDay:<n> UserCode:<n>
Check code Error: Hid:<i32> CallTime:<1|2|3> DataLen:32 CardTime:<Y-M-D h:m:s>  PcTime:<Y-M-D h:m:s>
```

### 5.2 Message inventory (1 252 lines)

| Message (verbatim) | Count | Meaning (matched to `Lang/lang.txt` `dogState_*` / `dogActiveReslut*` strings) |
|---|---|---|
| `verify data area failed!` | 683 | licence data block on the card failed verification (`dogState_DataBroken 硬件数据区被损 / Data sector is error`, `dogState_VerifError 系统认证错误`). Written in triplets (3 attempts) at every start-up 2024-06-19…06-27 (every session!), then sporadically |
| `Arm Clock is invalid!` | 103 | the card's (ARM) RTC is invalid (`dogState_ClockIllegal 板卡时钟非法 / Hardware clock is illegal`); 2024-06-28 and 2024-10-18…11-05, at every start |
| `Admin data was broken (read admin data)!!!` | 88 | admin (vendor) record on the card unreadable; always follows a `Check code Error` triplet |
| `Check code Error: Hid:… CallTime:1/2/3 DataLen:32 CardTime:… PcTime:…` | 237 (79 triplets) | the 32-byte licence record read from the card does not verify; logged three times per attempt with card and PC time; `CardTime` = `2000-0-1 0:0:0` from 2024-09-18 on (RTC lost) |
| `Admin data is not current App data (read admin data)!!!` | 7 | licence written by a different application/product id (`dogState_NotTheApp 非匹配应用程序 / Unauthenticated App ID`), 2024-07-17 |
| `User data is error (read user data)!!!` | 6 | user record unreadable |
| `get card clock fail!!` / `you lose your clock (get clock fail)!` | 5 / 2 | reading the card RTC failed (`dogState_ClockBroken 获取板卡时钟失败`) |
| `This is a surprise!!!!!` | 3 | unexpected state (developer message), 2024-06-28 15:21–15:23 |
| `Active codeStr:<CODE>` + `Active CodeStr data: Hid:… CodeTime:… LincensDay:… UserCode:…` | 29 pairs | an activation code was entered; the decoded code content is printed (hardware id it was issued for, issue time, licence days, vendor/user code) |
| `-Active: Logo Name no match!!!!` | 2 | vendor ("logo") name in the code ≠ this build (`dogActiveReslut7 厂商ID不匹配 / Match User ID fail`) – the 2024-07-15 code with `UserCode:224` |
| `-Active: Active OK` | 1 | 2024-07-15 13:57:50 – code `EIKTMASOGOXBFSBKHAIZ`, 7 days, `UserCode:0` |
| `-Active: reset clock OK` | 9 | activation path wrote the card clock successfully |
| `-Active: Setting card clock fail!!!!` / `reset card clock fail!!` | 4 / 5 | writing the card RTC failed (`dogActiveReslut10 时钟操作失败`) |
| `lastLicenseClock:<T> --Active: code overdue!!!!` | 7 | code rejected as expired/used (`dogActiveReslut8 密码过期 / Activation code is Used`): the stored "last licence clock" is later than the code time |
| `-Active: hard ID is not match!!!` | 5 | code issued for another hardware id (`dogActiveReslut6 硬件ID不匹配`) |

### 5.3 Timeline (EVIDENCE, condensed)

| Date (PC) | Events |
|---|---|
| 2024-06-19 … 06-27 | every start: `verify data area failed!` ×3 (668 lines) – the card had no valid licence block; the software still ran (trial?) |
| 2024-06-28 | `This is a surprise` ×3, `Arm Clock is invalid` ×2 |
| 2024-07-11 | `Check code Error Hid:-632724586`, `Admin data was broken`, `User data is error` (card and PC time agree: RTC was running) |
| 2024-07-15 13:40 | code `KFUGCADZFVHVBIYZXKYC` (Hid 3662239894, 7 days, UserCode 224) → `Logo Name no match` twice; 13:57 code `EIKTMASOGOXBFSBKHAIZ` (7 days, UserCode 0, CodeTime 2024-07-27 21:06 – issued "in the future") → **`Active OK`** |
| 2024-07-16 … 07-18 | `Admin data is not current App data` ×7, `User data is error`, `you lose your clock` |
| 2024-08-09 … 09-20 | `get card clock fail` ×5 |
| 2024-09-18 (logged 10-18) | `CardTime:2000-0-1` from now on – the card RTC has reset (battery/firmware) |
| 2024-10-18 … 10-23 | `Arm Clock is invalid!` at every start (101 lines) |
| 2024-10-31 … 11-05 | 79 `Check code Error` triplets (Hid −652316513) + `Admin data was broken` – every start fails |
| 2024-11-11 14:05–15:02 | code `OBYPHMAMCHLPWGPBXICL` (Hid 3642650783 = −652316513 as int32 ✔ – the same id the failing checks reported, **3 days**, UserCode 999) entered 11 times; alternating `reset clock OK` and `code overdue` (`lastLicenseClock:2054-11-11 6:06:14` = PC UTC time **+30 years** – the card stores/returns the clock with a 30-year offset, i.e. an epoch-2000 counter interpreted as epoch-1970 `[likely]`) |
| 2024-11-15 … 2025-06-03 | one `Check code Error` triplet + `Admin data was broken` per start on 8 days (Hid drifting: −652316462, −652316214, −652316489, −652316227, −652316510, then −584860729 on 2025-04-22 and −585250654 on 2025-06-03) |
| 2025-06-17 17:19–17:52 | code `ZMILVIOQKBPXJNEZRSZC` (Hid 3709908410, **180 days**, UserCode 999, CodeTime 2025-06-17 16:45:09): `hard ID is not match` ×4; `reset card clock fail`; **PC clock set to 2055-06-17** (lines timestamped 2055); `hard ID is not match`, `code overdue (lastLicenseClock:2085-6-16)`, `reset clock OK`, fails, `reset clock OK`, fails, `reset clock OK`; PC clock back to 2025-06-17 17:52:42: `reset clock OK`. No `Active OK` line is printed for this code, but the machine then ran without licence errors through 2025-07-18 (no further Code.txt lines) |

### 5.4 What this tells the port

* The licence is **stored on the MCC100 card** (a 32-byte record + admin/user records + the card RTC), read through the
  register protocol at every start (`CallTime 1..3` = three read attempts). Neither the USB dongle nor the PC is the
  licence anchor. `Hid` is derived from the card (the same card gives values within ±300 over months, then jumps after
  the firmware/hardware change in spring 2025), i.e. it is a hash that includes something volatile `[likely]`.
* Licence days are counted against the **card clock**, which the activation writes (`reset clock`). The technician's
  2055 trick shows the check compares `card clock` against `lastLicenseClock` stored on the card, not against the PC.
* The 180-day licence of 2025-06-17 lapses ≈ 2025-12-14 (`mf228 SC系统授权试用期还剩余：%d天 / Hardware expires in %d days`,
  `mp1 试用期结束 / Trial period is expired`). A Linux re-implementation that talks to the card directly is not subject
  to this check unless the *firmware* enforces it; the logs show the card executing commands while the PC side logged
  licence errors (2024-10/11 jobs in `TotalReport.txt` exist on days with `Check code Error` at every start), so the
  enforcement is on the PC side `[likely]`.

---

## 6. Statistics and reporting data model

### 6.1 `Report/TotalReport.txt` – row formats

Rows are appended at job end, newest last, no header. Two formats, switching between line 1 213 (2025-03-06 16:53:22)
and line 1 214 (2025-03-10 11:31:39) – the same week the `Report/` tool files are dated (2025-03-07) → software update.

**Format A (2024-07-25 … 2025-03-06, 1 213 rows, 7 fields):**
```
未命名-1,20.84×20.84mm,0.07m,0.05m,0,0分01秒,2024-07-25 14:48:15
name, W×Hmm, cut length m, idle-move length m, pierce count, m分ss秒, end time
```
**Format B (2025-03-10 → , 1 737 rows, 10 fields):**
```
未命名-1,33.00×25.00 mm,2025-07-18 14:16:12,0.08 m,0.03 m,1,0分15秒,7.03 Sec,0.19 Sec,7.20 Sec
name, W×H mm, end time, cut length m, idle length m, pierce count, total m分ss秒, cut time s, idle-move time s, pierce time s
```

Column names come from `Lang/lang.txt` (UTF-16LE, `key#中文#English`): `gp130 加工文件名 / File Name`, `gp131 幅面尺寸 /
Process Size` (`gp142 %.2fmm×%.2fmm`), `gp132 切割长度 / Cut Length` (`gp137 %.2f米`), `gp133 空走长度 / Dry Cut Length`,
`gp134 穿孔次数 / Drill Times` (`gp138 %d次`), `gp135 加工完成时间 / End Time`, `gp139 加工耗时 / Process Time`
(`gp146 %d分%02d秒 / %02dMin%02dSec`, `gp136 %02d小时%02d分%02d秒` for ≥ 1 h), `gp143 加工计件 / Process Count`; the three
seconds columns are the `gp2001` triple `切割时间 / Cut Time`, `空跳时间 / Idle Move Time`, `穿孔时间 / Drill Time`
(the dialog also shows `系统延时 / Sys Delay` and `总用时 / total`, not written to the file). MainApp format strings:
`,%.2fx%.2f mm`, `,%.2f m`, `,%.2f Sec` (UTF-16 strings in `MainApp.exe`) – note the exe string uses a lower-case `x`
while every TotalReport row uses `×` (the `gp142` pattern); `Report/report.txt` and the 2025-03-07 `LogReport` rows use
the lower-case `x` variant, i.e. two different writers exist.

Language dependence: rows written while the UI was in English (`Lang=1`) read `Untitled-1,…,00Min09Sec` (8 rows,
2025-07-12 09:16–09:21) – the file name of an unsaved drawing and the duration format are localised. A parser must
accept both `未命名-1`/`Untitled-1`, `分…秒`/`Min…Sec`, `×`/`x`, with/without the space before `mm`/`m`/`Sec`.

### 6.2 Semantics established from the data

* **`end time`**: `gp135` says 加工完成时间 "processing finished time". Consistent with `LogReport.txt` being written 2–6 s
  *after* it (§6.3) and with jog rejections clustering 4–15 s before `end − duration` (= just before Start) `[confirmed]`.
* **Times are planner estimates, not measurements**: repeated runs of the same drawing carry byte-identical
  `cut/idle/pierce` seconds (`4.91 Sec,0.19 Sec,1.75 Sec` for nine 30×25 mm runs on 2025-07-13; only `cut` changes when
  the operator changes the layer speed: 4.11/4.91/5.70 s), and `total = round(cut + idle + pierce)` in 1 496 of 1 737
  rows (±1 s in another 63). The `gp2001` dialog is the "estimated time report" (`mp26`, doc 06). Real wall-clock
  duration is *not* stored anywhere in the reports.
* **pierce count / pierce time**: 1 064 rows have pierce ≥ 1; in format B `pierce time` is 0 exactly when pierce count is 0
  (993 rows) and 0.7–1.8 s (occasionally 7.2–8.2 s) when pierce = 1 – the per-pierce dwell of the layer (`t3` values
  0.8 s ×268, 1.1 s ×182, 1.3 s ×156, 1.8 s ×55). Max pierce count 256 (a dot-matrix job), Σ = 4 043.
* **Empty jobs**: 314 rows have size `0.00×0.00` (Start pressed with nothing selected / an empty drawing); they are still
  logged. 29 consecutive rows on 2025-07-14 10:14:12–11:39 carry garbage in `cut time`
  (`-553419785105102400000000000000.00 Sec`) and `LogReport.txt` 2025-03-07 shows
  `-92559631349317830000000000000000000000000000000000000000000000.00 Sec` ×3 – an **uninitialised double**
  (≈ −9.26e61 = the `0xCDCDCDCD…` debug-fill pattern) printed when the job had no geometry. A parser must tolerate
  arbitrary negative garbage.
* Duplicate rows: 1 consecutive pair with identical end time (a double write), several near-identical pairs seconds
  apart (Start pressed twice on an empty drawing).

### 6.3 `Report/LogReport.txt`

40 lines, `YYYY-MM-DD HH:MM:SS  <TotalReport row>` – the time at which the "Work Report [New]" (`mf252 加工报告[最新]`)
was generated (`report.exe` launched), followed by the row(s) it was given. Gaps `write − end time` are 2–6 s for
2024-02-29…2024-11-15 and 2025-03-07 (report generated automatically at job end) but 28 min for 2024-06-25 10:47:51 /
10:48:29 (the same 10:19:44 job reported twice – manual re-generation). Rows of 2024-02-29 … 2024-11-15 predate the
first `TotalReport.txt` row (2024-07-25), so `TotalReport.txt` was created/reset on 2024-07-25 while `LogReport.txt`
was kept. The 2025-03-07 15:57–16:17 block is the installation test of the new report tool with `111.chf`/`222.chf`
(their thumbnails `Report/111.chf.jpg`, `222.chf.jpg` are dated 2025-03-07).

### 6.4 `Report/report.txt` and `Report/lang.txt`

`report.txt` = the two rows for the most recent multi-file report (`222.chf` then `111.chf`, both 15.45×34.34 mm,
2025-03-07 16:04:41/16:04:50) – the *input* of `report.exe` (it renders them with the `<name>.jpg` thumbnails and
`广告.jpg`). `lang.txt` = `lang==0` selects Chinese labels in `report.exe`. Both are rewritten by MainApp before it
spawns `report.exe` (`MainApp.exe` strings `\report.txt`, `1\report.exe`, `lang==0`, `lang==1`).

### 6.5 `File/ProcessesStatistic.txt`

GBK, one line: `未命名-1,0.0X0.0mm,0mm,0mm,0,0秒,1970-01-01 08:00:00` = the *last-job* statistics in an older layout
(`X`, lengths in mm, seconds only, time_t 0 rendered in UTC+8). Dated 2022-12-06 – a leftover from a previous software
generation; the current build writes `TotalReport.txt` instead (doc 01 §6.4 agrees).

### 6.6 Break-point / autosave state (`AutosaveParam*.ini`, `tempIsBreak.ini`, `softPara.ini`)

| File | Content | Reading (doc 01 §6.3 for the writer/reader code) |
|---|---|---|
| `File/AutosaveParam1.ini` (2025-07-18) | `scFlie / 38 / 979454 / 342126 / 7 / 486 / eof` | break-point slot 1: contour #38, X = 979.454 mm, Y = 342.126 mm, glyph 7, point 486 |
| `File/AutosaveParam2.ini` (2025-07-18) | `scFlie / 37 / 965189 / 340285 / 7 / 132 / eof` | slot 2 (alternating), contour #37 |
| `File/Temp/AutosaveParam1.ini` / `2.ini` (2025-04-23) | `30 / 89243 / 69837 / 1 / 4950`, `31 / 88331 / 71462 / 1 / 5323` | snapshot of an April job (Temp copy made when a break was recorded) |
| `File/Temp/tempIsBreak.ini` (2025-04-23) | `scFlie / 1 / 1 / eof` | "a break-point exists" flag (1) + slot/valid flag (1) `[likely]` |
| `File/softPara.ini` (2025-07-18) | `[SC2000] NormalExit=0 XAxis=1020277 YAxis=175510 ZAxis=0 WAxis=0` | **the last session did not exit normally** (`NormalExit=0`) and the head was last known at X = 1020.277 mm, Y = 175.510 mm (0.001 mm units) – consistent with the last log line of 2025-07-18 (15:49:24 output-command timeout) being the end of the record |

The break-point position (979/342 mm) lies inside the 225.45×85.17 mm job region cut at 14:23–14:24 on 2025-07-18 only
if the drawing was placed at ~(800…1000, 300…400) mm – plausible; the two slots are 14 mm apart, i.e. written a few
contours apart during the same job.

---

## 7. Errors and alarms actually encountered – catalogue with likely cause

| Code / message | Where | Count | Context observed | Likely cause (confidence) |
|---|---|---|---|---|
| `MC-RecvDataErr 503` on `[3, axis, …]` jog | daily logs | 506 | bursts at key-repeat rate while jogging, seconds before/after jobs | card refuses a new target while the axis is moving `[likely]` (§4.5) |
| `503` on `[1, axis, 2, …]` home | daily logs | 60 | repeated home presses; `home 1`/`home 2` alternating 24× in 3 s (2025-07-17 15:09) | homing already in progress `[likely]` |
| `503` on `[5, mask, …]` two-axis move | daily logs | 1 | 2025-07-08 13:24:33 | same as above `[guess]` |
| `MC-RecvDataErr 502` on `READ 1000 n=2`, `READ 50000 n=26`, `READ 60001 n=120`, `READ 10000 n=18`, `READ 11000 n=39` | daily logs | 34 | immediately after a `READ 1000 n=36` timeout burst, 1 ms apart | card reachable but in a boot/fault state that rejects register blocks (firmware not initialised) `[likely]`; note `READ 11000 n=39` (`30 2af8 27`) appears only here (2025-07-14 10:10:24) |
| `502` on `[102]` command | daily logs | 2 | reconnect path (§3.3) | same |
| `MC-RecvErr_selectFunc 10060` | daily logs | 1 072 | see §2.3 table | (a) card off / rebooting (2025-06-28, 07-09 14:44–15:41, 07-14 09:19–10:05); (b) NIC/link trouble (2025-07-17 13:30–15:22: 9 restarts, FIFO frames and all reads failing, then `Sendto 10065` at 15:05:21 → cable/IP problem) `[confirmed]`; (c) the periodic `READ 10000` deafness `[likely]` |
| `MC-Sendto 10065` | daily logs | 5 | 3–14 s after `NC Start` | Windows had no route to 10.1.1.168 (adapter down/unconfigured) `[confirmed]` |
| `MC-Recvfrom 10038` | daily logs | 2 | 2025-07-09 15:27:13, 2025-07-17 15:07:07, both mid-burst | socket closed under the receiver during shutdown/reconnect `[confirmed]` |
| Lost FIFO frame → job aborted | daily logs + report | 22 frames / ≥ 2 aborted jobs | §4.4 | FIFO starvation alarm `[confirmed]` |
| `verify data area failed`, `Arm Clock is invalid`, `Check code Error`, `Admin data was broken`, … | `Code.txt` | 1 252 | §5 | licence block/RTC on the card invalid, expired codes, wrong hardware id `[confirmed]` |
| `NormalExit=0` | `softPara.ini` | – | last session | crash or power-off without closing MainApp `[confirmed]` |
| garbage `-5.5e29 Sec` / `-9.3e61 Sec` | reports | 32 rows | empty jobs | uninitialised double `[confirmed]` |

Machine alarms proper (limits, e-stop, servo, chiller, laser – `gp1..gp59`) are **not** written to any file in the
package; the alarm panel (`gp84-94`) keeps them in memory only (no alarm log file exists under `SRC`).

---

## 8. Machine usage profile (from `TotalReport.txt`, 2 950 jobs)

* **Period**: 2024-07-25 → 2025-07-18, 68 distinct days. Monthly: 2024-07 121, 08 159, 09 230, 10 531, 11 114, 12 1,
  2025-01 3, 02 12, 03 158, 04 31, 05 25, 06 78, **07 1 487** (half of all jobs in the last 13 days before the package was
  copied on 2025-07-29; the archive name `CF1390-250715` suggests a machine built/commissioned 2025-07-15).
* **Files**: 2 938 × `未命名-1` (unsaved "Untitled-1"), 8 × `Untitled-1` (English UI), 1 × `1111.chf` (2024-11-05, the
  longest job: 10 min 10 s, 1.50 m cut, 7.39 m rapid), 3 × `.nc` G-code imports on 2025-06-25: `金威刻logo-12线.nc`
  ("JWK logo, 12 lines" – 金威刻 = Jinweike, the brand), `大象-12线.nc` ("elephant, 12 lines"), `田字格-6线.nc`
  ("田-grid, 6 lines") – line-fill engraving tests (19.65 / 51.44 / 18.96 m of cut path in 113 / 288 / 105 s ≈ 175 mm/s).
* **Sizes**: 57 % ≤ 50 mm, 28 % 50–200 mm, 4 % 200–500 mm, 8 rows ≥ 500 mm (5 × `800×0 mm` lines, 3 × `17926.87×555.30 mm`
  on 2024-08-01 – a drawing with a stray far-away object, or an "unlimited roll" test). Most frequent July-2025 sizes:
  `33.00×25.00 mm` 282×, `30.00×25.00` 241×, `0.00×0.00` 222×, `25.00×25.00` 133×, `35.00×25.00` 110×, `200.00×200.00`
  64×, `109.00×7.00` 60×, `302.78×302.78` 30×, `94.00×7.00` 28×. 2024 favourites: `0.00×49.03 mm` (134×), `0.00×72.00`
  (73×) = vertical single lines (X extent 0).
* **Durations (estimates)**: median 4 s, mean 6.9 s, 79 % < 10 s, 11 % 10–60 s, 30 jobs 1–5 min, 2 jobs > 5 min;
  Σ = 20 212 s = 5.6 h of estimated processing in a year. Σ cut length 984 m, Σ rapid 354 m, Σ pierces 4 043.
* **Working hours**: 08:30–22:00 CST; on 2025-07-09 218 jobs between 14:48 and 21:56.
* **Interpretation** `[likely]`: parameter tuning and QA on a CO2 machine (`SC.m_iEnableLaserType=1`, doc 01): cut-speed
  ladders on 30×25 mm squares, 7 mm-high strips (`81/92/94/96/109×7 mm` = speed-test bars), 200×200 mm squares at the
  50 mm/s / 4 % layer (2025-07-17). No production nesting, no long jobs, no file management.

---

## 9. Open questions

1. **Tick period and increment unit of opcode 3000**: |Δ| ≈ 2.2 units/tick at 49.7 mm/s and ≈ 8 units/tick for rapids.
   Neither 1 ms/1 µm nor 1 ms/pulse fits both the layer speed and the 191 k items sent in 25 min on 2025-07-13 (frame id
   1 931). Needs a capture with a known move (e.g. 100 mm at 100 mm/s) – doc 04 §9.
2. **Opcodes 3001/3002/103/109/2001/118** and the meaning of `[9999, 2, mask, value]` bits 2 and 8 (which physical DO?).
   Compare with `BkHardPara.xml` DO assignments (`DOLaserGate=5`, `DORedLight=6`, gas/blower ports).
3. **Register block 10000 (18 regs)**: why does the card fail to answer it in 3.5 s ~every 10–40 min while block 1000
   is polled at 30 ms without loss? Is it a UI-triggered read (e.g. the "device report" `pd880-888` counters – total
   power-on / laser-on time / axis travel – which would be slow EEPROM reads)? `[guess]`
4. **Block 11000 (39 regs)** – read once (2025-07-14 10:10:24, rejected with 502). Unknown.
5. **Exception 3 semantics**: "busy" vs "out of range" – capture one jog into a soft limit vs one during motion.
6. **Licence**: which registers hold the 32-byte record, admin/user data and the RTC; whether firmware V201.52 itself
   refuses motion without a valid licence (evidence says PC-side only). The Hid drift (±300 between starts, +67 000 000
   after the 2025 firmware update) suggests Hid mixes a card serial with a version/date field.
7. **The +30-year card clock** (`lastLicenseClock:2054-…`): epoch-2000 counter, or `tm_year` written without −1900?
8. **`ManuContour.dat`** counts (`24` entries `0..23` in `File/`, `2` entries in `Temp/`) vs `AutosaveParam` contour
   #37/#38 – the break-point index space is larger than the manual-contour list, so field 1 is an index into the
   processed-item list, not into `ManuContour.dat` (doc 01 assumed the latter).
9. **Real durations**: nothing in the package records measured job time; only the device report counters
   (`pd880-888`) would, and they live on the card (`mp302` warns the report is wrong when the card is disconnected).
10. `Log/VelDecc.txt` – which build wrote it (empty since 2024-06-19)?

---

## 10. Implications for the Linux port

**Must be replicated (behaviour the operator/machine depends on):**

1. **Transaction engine semantics** exactly as observed: UDP request/reply with seq check, 500 ms `select` timeout,
   the 7-step retry ladder (≈ 3.5 s), exception decoding `500 + code`, and the *tolerance* for (a) exception 3 on jog
   re-sends and (b) 3.5-s silences on non-critical register blocks. The 30-ms status poll of block 1000 must never be
   blocked by a slow read of block 10000 – run slow reads on a separate queue/thread or drop them.
2. **Start-up order**: `READ 1000 n=2` (version gate ≥ `MinHardwareVer`) → `READ 1000 n=36` → `READ 50000 n=26` →
   `READ 60001 n=120` (+ `10000 n=18`), then the `[9999,13,…]` handshake of doc 04; on comm loss: `0x65 ← [102]` then
   `READ 1000 n=2` again; surface "card unreachable" (10065) vs "card silent" (10060) vs "card refusing" (502)
   distinctly in the UI – the operator on 2025-07-17 restarted the program nine times because the Windows UI did not
   distinguish them.
3. **Jog/home command vectors** with the observed constants: slow/fast jog 50 000 / 200 000 (0.001 mm/s), accel 5 999,
   jerk 59 990, absolute target in 0.001 mm, `±4 000 000` for continuous jog, W-axis (4) with 50 000/100 000, 4 000,
   40 000, ±1 000 000; homing `[1, axis, 2, 2000, 20000]` (or 5000/50000) and `31` for all axes; two-axis go-to
   `[5, 0x80000003, v, a, j, x, y, 0, 0]`.
4. **FIFO stream writer**: register 0x66, 298-word frames (`frameId` + 297 words), per-session frame counter, TLV items
   with `header = (bytes<<16)|opcode`; per-tick items `3000[(dY<<16|dX), (freq<<16|duty)]`, contour prologue
   `3001, 3002[5], 3000, 3001, 9999[2,4,4], 103[1000,0], 2001[…], 9999[2,0x100,0x100]`, epilogue
   `3001, 9999[2,0x100,0], 109[1000,0], 2001[…], 118[…], 3001, 3002[4]`; keep ≈ 1.6 s queued; a frame that is not
   acknowledged within the ladder must abort the job and raise a FIFO alarm.
5. **Work report**: append one row per finished job to `Report/TotalReport.txt` in format B (UTF-8, `×`, ` mm`, ` m`,
   `Sec`, localised name/duration) if the user wants continuity with the Windows history; keep `end time` semantics and
   the estimate-based time columns, or – better – add measured wall-clock time as an extra column and document it.
   Provide the `gp2000/gp2001` estimate dialog from the planner output (cut length, rapid length, pierce count, times).
6. **Break-point resume**: write/read the `scFlie` records (`AutosaveParamN.ini`, `tempIsBreak.ini`) and
   `softPara.ini` (`NormalExit`, last X/Y in 0.001 mm) so that a crash can be resumed and the last position restored.
7. **Licence**: nothing to replicate for the user's own machine; but the port must *not* clobber the card's licence
   data area / RTC registers if it ever writes to unknown addresses (the Windows tool may still be needed for firmware
   updates and would then find the licence broken).

**Can be replaced by existing Linux/open-source components:**

* Logging: any structured logger (spdlog/journald); keep a `Log/<date>.log`-compatible writer only if the vendor's
  support process depends on it.
* Report viewer (`report.exe`, Qt5): a CSV → HTML/PDF template (Python + Jinja/WeasyPrint) over the same rows; the JPEG
  thumbnails can be rendered from the `.chf` geometry with Cairo.
* CSV/statistics: pandas or plain Python (`csv` module with the tolerant grammar of §6.1–6.2: both formats, `×`/`x`,
  `分秒`/`MinSec`, garbage negatives).
* Networking diagnostics (`pingMC.bat`, `IPSet.exe`): `ip addr add 10.1.1.10/24 dev …`, `ping`, `arping`; detect
  "no route" before sending (Linux returns `ENETUNREACH`/`EHOSTUNREACH` from `sendto` just like 10065).
* Time base: `clock_gettime(CLOCK_MONOTONIC)` for the retry ladder; `CLOCK_REALTIME` (UTC, ISO-8601 with offset) for
  report timestamps – avoid the 0-based-month bug seen in `Code.txt`.
