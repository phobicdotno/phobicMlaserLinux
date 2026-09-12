# 10 — Prior work: Mlaser under Wine (repo `phobicdotno/Wine-Mlaser`)

Source: https://github.com/phobicdotno/Wine-Mlaser (README + `docs/binary-analysis.md`,
commit `faad384`, 2026-09-11). Static analysis + a working Wine setup, done by the project
owner on Linux Mint 22.3 / Wine 9.0. Everything below is **confirmed by that work** unless
marked otherwise; it supersedes guesses elsewhere in this folder where they conflict.

## Identity
| | |
|---|---|
| Machine | **Gweike M3 Ultra** 6-in-1 (1200 W fiber + 130 W CO2), CF1390-class bed |
| Software | Mlaser v0.0.0.52, real product name **NexCut**, build `NexCut_X1_Http` |
| Vendor | SC2000 / **au3tech.cn** (PDB path `D:\SC2000\NexCut\NexCut_X1_Http\Release\MainApp.pdb`) |
| Binary | PE32 MFC, VS2010 |

## Runs under Wine — three fixes
1. 32-bit prefix (`WINEARCH=win32`), `wine32:i386` installed.
2. `winetricks -q mfc42` — `Dxf2Grp.dll` is a VC6-era build importing **MFC42.DLL**;
   chain: `Module\CADModule.dll → AutoNest.dll → Dxf2Grp.dll → MFC42.DLL`.
   (So `Module/` holds plug-in DLLs — `CADModule.dll` at least — not yet inventoried here.)
3. Run from `C:`: the app builds `\Technology\Fiber` / `\Technology\CO2` with a leading
   backslash (drive root). From `Z:` (= `/`) it fails with "Failed to create Technology folder".
   → The parameter database lives under `<drive>\Technology\{Fiber,CO2}` — a location the
   other reports must account for (per-source technology tables, not only `File/*.xml`).

Verified working through Wine: full UI, parameter editing/import/export, **Modbus TCP to the
controller**. `ADVANCED → Set IP` (`File/IPSet.exe`) crashes on Wine's `mprapi` stub — irrelevant,
it only runs `netsh` to set the *PC's* IP; use `nmcli` (`ipv4.never-default yes`, no gateway).
`Report/report.exe` = Qt 5.15.2, untested.

## Network facts
| Device | Address | Protocol |
|---|---|---|
| Motion controller (MCC100) | `10.1.1.168:502` | **Modbus TCP** (confirmed by traffic) |
| Z-follower (ZF) | `10.1.1.169:502` | Modbus TCP |
| Laser source | `10.1.1.170:10001` | raw TCP |

IPs compiled into `MainApp.exe`: `10.1.1.168`, `10.1.1.169`, `127.0.0.1`. `47.104.17.21` is
**not** compiled in — it comes from `ipAdd.ini` and can be blanked.

## Telemetry
- `MonitorIP=47.104.17.21:9001`, reconnect every 300 000 ms (5 min), Alibaba Cloud, no PTR.
- Hardcoded `http://www.au3tech.cn/key/` (plain HTTP via WinHTTP; `123.56.242.109`, Alibaba
  Beijing) adjacent to the MonitorIP strings — `/key/` suggests licensing/activation; relation to
  the HID dongle is an open question (07-exe-internals should settle it).
- `scripts/watch-vendor.sh` in that repo captures and attributes traffic to those hosts.

## Implications for the Linux port
- The transport question is settled: **Modbus TCP** to the card. The remaining unknown is the
  register map / command semantics — a live capture with Mlaser-under-Wine + `tcpdump host
  10.1.1.168` is the fastest way to get it (plan in `PORT-PLAN.md`).
- Technology tables under `\Technology\Fiber|CO2` must be located and decoded (they are outside the
  package directory — look in the Wine prefix `drive_c/Technology` after a run).
- No telemetry in the port; document the vendor endpoints so users can firewall the original.
- The M3 Ultra is dual-source (fiber + CO2): the layer/param model must carry a source selector.

## Open questions raised here
- Whether the dongle is required to *run* (the Wine write-up does not mention it) or only to
  unlock features/activation via `au3tech.cn/key/`.
- Contents of `Module/` (CADModule.dll and siblings) and of `drive_c/Technology` after first run.
