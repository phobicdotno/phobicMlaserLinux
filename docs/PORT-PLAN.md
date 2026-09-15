# PORT-PLAN — native Linux reimplementation of Mlaser/NexCut for the CF1390 (MCC100)

Companion to `docs/analysis/00-overview.md` (system description) and the subsystem reports `01`–`10`. Section references below use the same `doc §` pointers. Target platform: Ubuntu 26.04 on the owner's x86-64 laptop; machine: Gweike M3 Ultra (CF1390 bed, MCC100 card at 10.1.1.168, CO2 tube + 1200 W fibre source).

---

## 1. Goals, non-goals, constraints

**Goals**
1. Drive the existing machine (MCC100 card, unchanged firmware) from Linux: home, jog, load DXF/.chf, set layer parameters, cut with the CO2 source, resume from break-points, keep the work report.
2. Read and write the vendor's files (`Bk*.xml`, `BkLayerPara.xml`, technology `.xml`, `.chf`, `scFlie` state files, `TotalReport.txt`) so the Windows tool and the port can be used alternately on the same machine.
3. Reach parity for the fibre path (height follower, pierce stages, laser-source control) once its protocols are captured.

**Non-goals (dropped deliberately)**
* Card licence handling (`CDog`), USB nesting dongle, remote telemetry (47.104.17.21), NexCut HTTP job server, `au3tech.cn` activation — 00 §5, §12.
* Firmware update of the card (`.mcf` via func 0x26) — keep the Windows tool (under Wine) for that.
* The VC6 nesting chain (`AutoNest/SmartNest/Dxf2Grp`) and the ODA library — licensing and dead paths (09 §9).
* Pixel-exact reproduction of the BCGControlBar ribbon; the touch skin (`SBT/SLED`) is a later second front-end.

**Constraints**
* The laser must never fire, and no axis must move, unless the operator explicitly armed it — see §8 (testing/safety).
* Never write to unknown card registers: the licence data area and RTC live in the same address space (07 §6.3a, 08 §5). The port only writes 0x65, 0x66, 0x67 and, after capture-based verification, the documented RW blocks.
* No proprietary binaries or machine configuration files with pairing codes in the public repo; the analysis references `SRC` by path only.

---

## 2. Language / toolkit decision

### 2.1 What the workload actually demands

| Requirement | Measured / derived | Consequence |
|---|---|---|
| Card link | UDP request/reply, one datagram each way, 500 ms `select` timeout, status poll every 30 ms (~47 transactions/s incl. jog), retry ladder 7 × 500 ms (04 §3.3, 08 §2.3) | ordinary sockets; no kernel RT needed |
| Job streaming | 298-word (1 206-byte) frames to reg 0x66; observed refill 8.5–15 frames/s; the card buffers ≈1.6 s (`MCFifoTime`) and alarms below `FifoAlarmNum=30` items (08 §4.4) | a **soft** real-time producer: deliver a frame every ≤ 25–100 ms with < ~300 ms worst-case jitter; a dedicated process with a pre-planned queue satisfies this in any language |
| Planner | NURBS refit + whole-contour look-ahead + analytic S-curve sampling at 250 µs (05); a 1 m contour at 50 mm/s = 80 k ticks; `ManuItemMaxCapcity=1 000 000` items | vectorisable (numpy) or native; not interactive-critical |
| Canvas | 2-D vector display of up to ~10⁵ entities, 24-contour typical jobs; original uses GL 1.1 immediate mode (07 §3.2) | QGraphicsView or QOpenGLWidget with VBO batches |
| UI surface | ≈90–110 screens, mostly property grids driven by `Group.Item` labels (06 §8) | a widget toolkit with docking + tree/property editors |
| Libraries the analysts recommend | ezdxf (Py), openNURBS (C++), Clipper2 (C++/Py `pyclipr`), CavalierContours (C++/Py), libnest2d (C++/Py `nest2D`), Ruckig (C++/Py), FreeType (`fontTools`/`freetype-py`), pymodbus/libmodbus | both Python and C++ have complete coverage; Rust lacks openNURBS/Ruckig/libnest2d bindings |
| Contributors | owner: industrial-automation/maker profile (PLC, ESP32, shell), one-to-few contributors | lowest-friction language wins; performance hot spots must remain replaceable |

### 2.2 Options weighed

| Option | OpenGL canvas | Streaming to card | Packaging on Ubuntu 26.04 | Contributor accessibility | Ecosystem for the replaced libs | Verdict |
|---|---|---|---|---|---|---|
| **Qt 6 / C++20** | QOpenGLWidget, best perf | deterministic threads, trivial | `qt6-base-dev` in apt, CMake, `.deb`/AppImage | highest barrier (C++, CMake, Qt build discipline) | all C++ libs native (openNURBS, Clipper2, CavalierContours, libnest2d, Ruckig, libmodbus) | best long-term performance, slowest to first cut |
| **Python 3.12 + PySide6** | QOpenGLWidget/QGraphicsView; numpy VBOs | fine in a separate driver process (GIL isolated); asyncio/threads | `python3-pyside6.*` in apt, or `uv`/pipx wheels; `.deb` via `dh-python`; AppImage optional | lowest barrier; matches existing `tools/chf_parse.py` and the owner's scripting habit | ezdxf (best DXF lib anywhere), `pyclipr`, `cavaliercontours`, `nest2D`, `ruckig`, `scipy.interpolate.BSpline`, `fontTools`, `pymodbus`, `scapy` for captures | fastest to a working machine; planner must be numpy-vectorised; hot spots portable to C extensions |
| **Rust + egui/iced** | egui immediate-mode, wgpu | excellent (tokio/threads) | cargo, single binary | medium (Rust learning curve); small pool | `dxf` crate, `cavalier_contours` crate; **no** openNURBS/Ruckig/libnest2d, immature docking/property-grid widgets, weak CJK text | good for the driver daemon only; wrong tool for a 100-screen CAD/CAM UI today |

### 2.3 Decision

**Python 3.12 + PySide6 (Qt 6 Widgets) for the application; the MCC100 link isolated in its own process (`nexcut-mccd`), written in Python first with a measured latency gate, replaceable by a Rust/C++ daemon behind the same local IPC if the gate fails.** Rationale:

1. The evidence says the card tolerates ≥ 1 s of PC hiccups (1.6 s look-ahead, `FifoTimeout=600`, single-try policy while streaming, 08 §4.4). A separate process with a pre-computed item queue meets that with margin in CPython; the GIL is irrelevant across processes.
2. Every library the reports recommend has a maintained Python binding; ezdxf alone removes most of the DXF importer work (09 §7).
3. `tools/chf_parse.py` (validated on all samples) and the planned capture dissector are already Python; reusing them as the first modules avoids a rewrite.
4. Milestone 1 ("prove control of the real machine") is a 300-line UDP script — the language must not slow that down.
5. Escape hatches are defined up-front: (a) numpy-vectorised sampler; (b) `pyclipr`/`cavaliercontours` for geometry; (c) driver daemon behind IPC. If profiling of a 100 k-contour job or the streaming jitter test (§8.3) fails, only that component is rewritten in C++ (pybind11) or Rust — the UI never is.

Repository layout (proposed):

```
nexcut/                # Python package
  core/                # units, parameter schema, i18n, config paths
  model/               # glyph/graph model, .chf, layers, crafts
  io/                  # dxf (ezdxf), plt, gcode, chf, technology xml, report csv, scFlie
  ops/                 # sort, lead-in, micro-joint, offset, chamfer, cool points, scan-fill, fly-link
  plan/                # contour_fit, junction, lookahead, scurve, sampler, pwm_schedule, items
  mcc/                 # protocol: framing, crc, transaction, registers, commands, fifo, simulator, dissector
  mccd/                # driver daemon (process): state machine, safety gate, IPC server
  ui/                  # PySide6 app: canvas, panels, property grids, control panel, alarms
tools/                 # chf_parse.py (existing), pcap dissector, log parsers, schema dump
tests/                 # golden files from SRC numbers, simulator tests, HIL scripts
docs/
```

---

## 3. Module breakdown (1:1 against the original)

Verdict legend: **R** = replicate exactly (behaviour visible to the machine or to file interchange), **L** = replace with library, **C** = needs live protocol capture before it can be written, **D** = drop.

### 3.1 MainApp.exe (shell)

| Original piece | Port module | Verdict | Notes |
|---|---|---|---|
| Ribbon / docking / property grids (BCGCBPRO) | `ui/` PySide6 `QMainWindow` + `QDockWidget`, `QToolBar` tabs, `QTreeView`-based property grid driven by the descriptor schema | L | Only 7 empty dialog templates exist; all screens are code-built (06 §8), so nothing is lost by re-layout. Keep the `Group.Item` label convention as the grid grouping rule. |
| `COpenGLView` canvas (GL 1.1) | `ui/canvas.py`: `QGraphicsView` first (fast to build, fine for 10⁴ items), `QOpenGLWidget` + numpy VBOs when profiling demands | L | Y-up frame, mm units, layer colours from the layer table; rulers (`IGP.EnableRuler`), show-start/direction/index toggles (`GRP.IsShow*`). |
| Plugin loader, module registry, singletons | Python packages | D | Not needed. |
| Parameter descriptor table (1 001 records) | `core/schema.json` generated from the reports (01 §1, 02 §2.3): section, key, label id, type, default, unit, min/max, enum ids | R | This is the port's single source of truth for editors, validation and XML I/O. |
| Start-up sequence (mutex, crash handler, registry, `IPSet`, ping buttons) | — / `nmcli` docs | D | `ip addr add 10.1.1.10/24 dev …`. |
| `CHttpClient` (NexCut server) | — | D | |
| `CHidUsb` hand-wheel, `PHBX.dll` pendant | `ui/pendant/` via `hidraw`/evdev | C (later) | Report layouts unknown (O16); a standard gamepad is the interim. |
| Licence UI (`dogState_*`, code input) | read-only "card licence status" indicator at most | D | Never write the data area. |
| Report launcher (`report.exe`) | `ui/report.py` (HTML/PDF via Qt) | L | Same CSV rows (08 §6). |
| Crash minidump | Python traceback log | D | |
| Compiled-in Chinese literals (≈110) | add to the i18n table | R | 07 §6.3a list. |

### 3.2 LangModule / ParaModule / LogModule / ControlModule

| Original | Port | Verdict | Notes |
|---|---|---|---|
| `LangModule` (`ID#zh#en`, fallback = id) | `core/i18n.py`: load `Lang/*.txt` (UTF-16LE) converted once to UTF-8 JSON; tolerant parser for the defects of 06 §1.5; per-language font | R | 11 languages for free; fix the known mistranslations in the English column only. |
| `ParaModule` XML store | `io/params.py` on `xml.etree`: exact element/attribute names and order, `%.17g` doubles, CRLF, no prolog; primary + `Bk*` + `SecondBk` write strategy (temp → rename) | R | Read `File/Bk*.xml` when the primary is missing (01 §0.3); default any missing attribute from the schema (02 §6.2). |
| `LogModule` | `logging` → `$XDG_STATE_HOME/nexcut/log/YYYY-MM-DD.log` with the same `<ts> [Level] -> msg` shape | L | Keep the shape so the vendor's support process can read it. |
| `ControlModule` widgets | Qt widgets | D | |

### 3.3 CADModule.dll

| Original | Port | Verdict | Notes |
|---|---|---|---|
| Glyph model (`IGraph` 8–12, `IGlyph` 1–7, direction flag, `<Crafts>`) | `model/` dataclasses mirroring 03 §6–7, crafts kept as a typed opaque struct so unknown scalars round-trip | R | |
| `.chf` reader/writer (`CLIFileBasic`) | promote `tools/chf_parse.py` into `io/chf.py`; add the v5 writer (`%f`, `0.0` special case, `%12.10f`, GBK strings) | R | Golden test: byte-identical re-write of the 8 samples. |
| DXF import (`DxfParseDllvc100`) | `io/dxf.py` on **ezdxf** (explode INSERT per `IGP`/`pd373`, text→curves per `pd374`, ignore HATCH/DIM/IMAGE like the original) | L | Keep the import gates `IGP.MicoGraphGate/OverlapGate/ConnectGate/MegerConnectGraphType/AutoSortType`. |
| DXF export | ezdxf | L | |
| PLT/HPGL-2 (`PltParse`) | `io/plt.py` hand-written (mnemonic set 09 §3.7) | R (small) | Units/scale handling open (09 §8.7). |
| G-code (`CGCodeFileParse`, BNF) | `io/gcode.py` recursive descent per 09 §3.7 incl. `L/LP/M17` | R (small) | |
| Text → outlines (`GetGlyphOutlineW`) | `fontTools`/`freetype-py` + fontconfig; Noto CJK for 宋体/微软雅黑 | L | Store the outline group in `.chf` type 10 v≥4 as the original does. |
| openNURBS wrapper (`splineAnalyer`) | `scipy.interpolate.BSpline` (degree 3, `n+4` knots) first; `opennurbs` via pybind11 only if fitting results must match to the µm | L | |
| Operations: sort (`CSSort/CRingSort`), lead-in (`CGuideCurve`), micro-joints, over-cut, chamfer/α, cool points, bridges, share-edge, fill circle, array/mirror/rotate/scale/align, group, reverse/positive, topology tree | `ops/*.py` re-implemented on the line/arc model; containment via Clipper2 `PolyTree` | R (behaviour) / L (primitives) | Parameter set from `GRP.*` (01 §1.2) fixes the behaviour; exact enum meanings of lead type / compensation side are O11. |
| Kerf offset (`CSegOffset`) | **CavalierContours** (arc-preserving polyline offsets) or Clipper2 | L | |
| Scan-fill + fly-line linking (`CGlyScan`, `linkFlyLine`) | `ops/scan.py` reproducing 05 §5 (serpentine, `scanSideLineLength`, spline U-turns, PWM toggle ratios) | R | Golden numbers: 1506.62 mm total, ratios 25/28/53/56/81/203.66 …. |
| `CContoutSmooth` | `plan/contour_fit.py`: refit tol clamp 0.01–0.3, merge collinear (0.001), blend > 30° unless cos < −0.984, piece list {cum length, radius, feed, cap, factor} | R | 05 §6 |
| `CVelocityPlanning` | `plan/junction.py` (05 §7.3 formula verbatim + optional physical model), `plan/lookahead.py` (backward/forward, ends at rest, slow-start insertion), `plan/scurve.py` (7-phase, J = 2A/Ta or 4Vmax/Ta², trapezoid fallback; **Ruckig** as cross-check) | R | Unit tests reproduce `segInterp_time` cases and the node rules of 05 §7.2. |
| `segInterp/arcInterp` sampler | `plan/sampler.py` numpy: s(t)/L per cycle, drop `L < 0.00025·v` | R | Cycle = `AX.InterpolationCycle` (250 µs) **until O1 says otherwise**. |
| `CInterpMrg` / `calcGraphCtInterpPt` | `plan/items.py`: XY → pulses (`WritePluse/SpeedRatio`), PWM freq/duty per tick from layer + curves, laser on/off at ratios, dwells, DO records, contour prologue/epilogue (08 §4.4) | R + C | The item encoding (which half is X, unit, tick) needs O1/O4. |
| Nesting bridge (`CSCNest` → AutoNest/NestLib) | `ops/nest.py` on **nest2D/libnest2d**; common-edge, remnants, guillotine on top (09 §5) | L | Dongle logic dropped. |
| Thumbnails (GDI+) | Qt `QImage` | L | |
| Debug dump files | none (optional `--dump` flag) | D | |

### 3.4 NCModule.dll

| Original | Port | Verdict | Notes |
|---|---|---|---|
| `CExtModbus` framing + CRC | `mcc/framing.py` (04 §3.2; CRC table verified against 4 logged frames) | R | Unit tests: `8c75`, `4534`, `45d0`, `85f5`. |
| Transaction engine (`0x1001d6a0`) | `mcc/transaction.py`: seq echo check, stale-seq re-receive, 500 ms select, idle ladder, single-try mode while streaming, ErrCode taxonomy (10060/10065/10038/502/503/603/9999) | R | Surface "unreachable / silent / refusing" distinctly (08 §10). |
| Register map + polling schedule | `mcc/registers.py` (04 §3.5), `mccd` scheduler: block 1000 every 30 ms on its own queue; slow blocks (10000/11000/50000/60001) on a background queue, never blocking the fast poll | R + C | Names and bit fields need O2/O5. |
| Command register 0x65 | `mcc/commands.py`: home, jog (units per 00 §10.1), multi-axis go-to, DO/DA `9999,*`, handshake `9999,13` | R + C | Verify each vector once in a capture (§7). |
| FIFO run control 0x67, FIFO writer 0x66 | `mcc/fifo.py`: TLV item packer, 99-item frames, monotonic frame id, throttle on space margin, abort on unacknowledged frame | R + C | O1 (tick/unit), O4 (opcodes). |
| `CVirtualMachine` (offline simulation) | `mcc/simulator.py`: a UDP server implementing framing, registers, exceptions 2/3, FIFO consumption at the tick rate, DI/DO, alarms | R (as test double) | Also the dry-run target of the UI. |
| ZF/AF/EC HALs, FTC61, EC3710 (`CStdModbus`) | `mcc/zf.py` (port 999) — later; EC3710 via `pymodbus` if ever fitted | C / L | O9. |
| Laser HALs (IPG ASCII, Raycus ESC) | `mcc/laser_ipg.py`, `laser_raycus.py` — trivial TCP/serial line protocols | R (later) | Not used on this machine (IO control). |
| `CDog` licence | — (read-only status display at most) | D | |
| Monitor / log uploader | — | D | Privacy. |
| Hardware parameter mirror (59600+) | `mccd`: read-back and compare only; **write only after capture-verified addresses** | C | `hp38` "params need hardware restart". |

### 3.5 Files and folders

| Original | Port | Verdict |
|---|---|---|
| `%LOCALAPPDATA%\NexCut` primaries + `File\Bk*` backups | `$XDG_DATA_HOME/nexcut/` primaries; optional "vendor-compatible backup dir" pointing at a Windows install/Wine prefix so both tools share state | R |
| Technology library `Technology\Fiber|CO2\*.xml` | same layout under the data dir; ship the 55 vendor presets as the initial library | R |
| `softPara.ini`, `AutosaveParam1/2.ini`, `ManuContour.dat`, `tempIsBreak.ini`, `autosave.chf` | `io/state.py` (`scFlie` container, even/odd parity, 0.001 mm ints) | R |
| `Report/TotalReport.txt` (+ measured wall-clock column added) | `io/report.py` | R (+ extension) |
| `ipAdd.ini` | `config.toml` with the same keys and defaults (04 §1) | R (semantics) |
| `.enc` / `.aut` packages | reader once the layout is diffed under Wine | C (O14) |
| `.mcf/.afb/.zfb/.efb` firmware | — | D |

### 3.6 Third-party DLLs

| DLL | Port | Verdict |
|---|---|---|
| `DxfParseDllvc100.dll` | ezdxf | L |
| `splineAnalyerVc100.dll` | scipy BSpline / openNURBS | L |
| `CircleFitDLL.dll` | small least-squares circle fit (numpy) — only needed for polyline→arc compression | L |
| `AutoNest.dll`, `SmartNest.dll`, `Dxf2Grp.dll` | libnest2d / nest2D; libredwg only if DWG import is wanted | D → L |
| `MotionCtrl.dll` | dead; its algorithms are already decoded in 05 | D |
| `PHBX.dll` | hidraw driver after capture | C |
| `BCGCBPRO`, `mfc100u`, `report.exe` (Qt 5 + Enigma) | Qt 6 | L |

---

## 4. Milestones (order of work)

Each milestone has a gate that must pass on the simulator before hardware, and a hardware sign-off under the safety rules of §8.
Current state per milestone, with the gate results actually measured, is in `docs/STATUS.md`.

### M0 — Foundations and passive instrumentation (no machine contact)
* Repo skeleton, `uv`/pyproject, CI with the golden tests; import `tools/chf_parse.py` as `io/chf.py`.
* `mcc/framing.py` + `mcc/dissector.py`: parse `Log/*.log` `DataEx/Buffer` lines and pcap files (scapy) into decoded transactions; reproduce every number in 08 §2–4 (CRC, ladder, 22 frames TLV-parsed with zero remainder).
* `mcc/simulator.py` v0: answers `READ 1000 n=2/36`, `50000`, `60001`, accepts 0x65/0x66/0x67, emulates exception 2 (not ready) and 3 (busy), consumes FIFO at a configurable tick.
* `core/schema.json` from 01/02 tables; `io/params.py` round-trips `Bk*.xml` byte-for-byte (modulo `%.17g` equivalence).
* Gate: all golden tests green; dissector decodes the package logs and the first tcpdump capture (§7).

### M1 — "Hello machine": prove end-to-end control (smallest real-hardware step)
Scope: connect over UDP, read version/status, decode positions, **jog one axis a few millimetres**, home. No laser, no job.
1. Capture session first: the **M1-confirmation session** (`docs/analysis/11-static-findings.md` §7; replaces §6 sessions A–C). The jog/home/stop vectors and units are already recovered statically (§6.0); the capture confirms K, bit 31, the stop profile, firmware licence gating and limit polarity before the port drives an axis on its own.
2. `mccd` v0: open socket, `READ 1000 n=2` (version ≥ `MinHardwareVer`), status poll, `READ 2000` positions, show alarm/DI/DO words raw.
3. Jog / stop / home with the statically recovered vectors (`docs/analysis/11-static-findings.md` §2; A1). Units: speed and distance × K (K = reg 50017, expected 1000 → µm/s, µm), acceleration plain mm/s², jerk = 10·a. After connect, send `0x65 ← [9999, 5, 0, 0]` as the vendor does.
   * **Step jog** X +5 mm at 50 mm/s: `0x65 ← [3, 0, 50000, 5999, 59990, 5000]`. Word 1 = axis-list index (0 X, 1 Y). The last word is a **relative** distance, not a target; bit 31 of word 1 would make it absolute.
   * **Continuous jog**: `[3, i, 200000 | 50000, 5999, 59990, ±4 000 000]`, one command per key press (the vendor does not repeat it). On key release send the **stop** `[1, 1<<slot, 2, 2000, 20000]` (vd = clamp(JogStopDccFactor·100000/(v/K), 2000, 0.4·FCP.MaxAcc)).
   * **Stop all** (E-stop key, watchdog): `0x67 ← [3]` while the FIFO runs, otherwise `[1, 0x1F, 2, 2000, 20000]`, then `[101]` (ZF stop, ZFType=1). Then explicitly switch DO9/PWM/gas off: the vendor's idle stop branch does not.
   * **Home** one axis at a time: `[2, 1<<slot, 0]` (X `[2,1,0]`, then Y `[2,2,0]`). Speeds and back-off are card parameters (50200 block), so none are sent. Verify the axis reaches the negative limit, backs off 22/15 mm, and axis-status bit 15 (homed) sets.
   * Expect exception 3 on commands sent while an axis moves. The card's jog/move builders act only when runStatus == 0.
   * *(Superseded: the earlier `target = current + 5 000` and `home [1, mask, 2, …]`. Sub-command 1 is STOP, 2 is HOME.)*
4. Minimal CLI/TUI (`nexcut-mccd --cli`): status line decoded per 11-static-findings §4 (DI/DO bits, alarm_1 bit 30 E-stop, alarm_2 bit 5 FIFO starvation, axis status bits 0–5/15), jog keys, home, E-stop key = the stop-all sequence of step 3.
* Gate: repeatable 100 mm X move measured with a ruler within 0.1 mm; homing repeatable; no exception other than 3-while-moving; position registers match `softPara.ini` semantics (0.001 mm).
* Resolves (confirms): O3, O6 firmware half (jog without licence exchange), part of O2 (DI word vs limit switches, O8); the M1-confirmation capture of 11-static-findings §7 replaces sessions A–C.

### M2 — File load and render
* DXF via ezdxf, `.chf` v4/v5, PLT, G-code → model; canvas with layers, start points, direction arrows, index numbers; measurement tool; open/save `.chf` (byte-identical re-save).
* Import gates and one-key planning steps (sort strategies, lead-in defaults, auto micro-joint) as pure geometry.
* Gate: all `Graph/Work*` and `File/autosave.chf` render identically to `tools/out/*.png`; a 24-contour job sorts to the same order as `ManuContour.dat` when using `GRP.SortType=4`.

### M3 — Layers, parameters, hardware configuration
* Schema-driven property grids (hardware, machining, software, layer fibre/CO2, graph rules) with units (`UN.*` display conversion), validation (`lp19`), enum labels from `lang.txt`.
* Technology library import/export (`P…Param11` wrapper, missing-attribute defaults); source selector fibre/CO2 with the `A250607_*` warning; per-contour crafts editor (kerf, lead, cool points, PWM curve).
* Hardware page reads back card blocks 50000/50200/59600+ for comparison (read-only until O5/O6 are closed).
* Gate: round-trip every vendor XML; the Windows tool (under Wine) loads files saved by the port.

### M4 — Full job streaming in dry run
* Planner (`plan/*`) producing ticks; `mcc/fifo.py` producing frames; `mccd` streaming with flow control; contour prologue/epilogue per 08 §4.4; break-point files; report rows; estimate dialog (`gp2000/2001`).
* **Dry-run semantics (as the original's 空走):** every tick carries duty 0, no `9999[2,0x100,·]` laser-enable record, gas records optional; the hardware laser interlock (§8.2) is additionally open.
* Gate 1 (simulator): identical item stream for the 24-segment raster as decoded in 05 §5–6 (1506.62 mm, 46 toggles); jitter test §8.3 passes.
* Gate 2 (machine, laser interlocked): the 200 × 200 mm test square of 08 runs at 50 mm/s without FIFO starvation; measured time ≈ planner estimate; frame-id / space-margin behaviour logged.
* Resolves: O1 (tick), O4 (opcodes, by A/B captures of the Windows tool vs the port on the same drawing).

### M5 — Live CO2 cutting
* Arm sequence (§8.2), gas DO3/DO2/DO7 + DA2 proportional valve, laser-enable DO9 in-stream, PWM freq/duty, dwells (`LaserOnDelay`, gas delays), power/frequency-vs-speed curves, small-circle limit, frog-jump/lift rules (CO2: fixed Z), stop/pause/resume, break-point resume with retract (`MC.ResumeBackLength`), work report with measured time, alarm panel with decoded words.
* Gate: cut the vendor's 30 × 25 mm test square and a 200 × 200 mm square on acrylic/plywood with the CO2 presets; dimensions within kerf tolerance; the Windows tool reads the produced `TotalReport.txt`/`autosave.chf`.

### M6 — Fibre path (after capture of port 999 / laser control)
* Height follower status/commands, pierce stages, focus, edge-seek wizards (capacitive), fibre laser IO/DA control, laser alarms.
* Gate: fibre preset cut on mild steel with O2.

### M7 — Nesting, fly-cut/scan engraving, pendant, touch skin, polish
* libnest2d bridge + common-edge/remnants; scan-fill with PWM position compensation tables; PHBX pendant via hidraw (after USB capture); i18n for 11 languages; `.deb`/AppImage packaging; docs.

---

## 5. Hard risks

| # | Risk | Why it is hard | Mitigation |
|---|---|---|---|
| R1 | **Controller protocol coverage** — register bit fields, jog/home encodings, FIFO tick/unit, opcode semantics are inferred from a *failure* log, not from successful traffic (04 §7, 08 §9) | only failed datagrams were ever logged; 18 open items | live captures (§7) before M1 and M4; A/B comparison of Windows-tool traffic vs port traffic on identical drawings; dissector in CI |
| R2 | **Safety interlocks** — no software E-stop input is configured (`DI.EStop=0`), soft limits are off in `BkManuPara.xml`, the card's stop behaviour (`0x67 ← 3`, `SafeStopFactor`) is unverified, and the laser is enabled by a plain DO plus in-stream PWM | a bug can move the gantry into the frame or fire the tube | §8: hardware laser interlock, PC-side soft limits always on, arming state machine, watchdog, simulator-first |
| R3 | **Laser-source protocol (fibre)** — IO/DA control path and the 10.1.1.170 Ethernet option are uncaptured; "peak current %" scaling to DA1 unknown (02 §7.9) | wrong scaling = wrong power | defer to M6; capture with fibre selected; start with duty-only |
| R4 | **Height follow (ZF)** — on-board FTC over port 999, 10000/11000 blocks, pierce/gradual/frog-jump handshakes entirely uncaptured (O9) | fibre cutting impossible without it; head crashes if wrong | M6 only; capture calibration + one pierce; keep `EnableManuCrashProtect` semantics |
| R5 | **Licence/firmware interaction** — the card may refuse commands if the PC never runs the `CDog` exchange, or the 180-day licence lapse (≈2025-12-14) may affect the card state (O6) | could make the port impossible without reverse-engineering the licence | test early in M1 (the port never performs the exchange; evidence says PC-side); keep Wine + Windows tool as fallback; never write the licence area |
| R6 | Planner fidelity — UI→P mapping partly untraced (O12), tick period ambiguity (O1) | cut quality/time differ from vendor | golden tests from the dumps; A/B item-stream diff vs Windows tool |
| R7 | Interchange fidelity — `.chf` crafts unknowns, `.enc/.aut`, `TotalReport` quirks | files rejected by the Windows tool | opaque round-trip of unknown fields; Wine-based diff tests |
| R8 | Python real-time margin | GC/GIL jitter starving the FIFO | separate `mccd` process, pre-computed queue, jitter gate §8.3, Rust/C++ fallback for the daemon only |

---

## 6. Exact next investigative steps (ordered)

### 6.0 Static sprint result (2026-09-15)

The static sprint (`docs/analysis/11-static/A1..A8`, consolidated in `docs/analysis/11-static-findings.md`) recovered from the binaries alone:
* the full 0x65 command vocabulary (stop / home / jog / go-to / DO / DA / ZF, with units);
* the block-1000 / axis-status / alarm bit maps and the alarm-code → text scheme;
* the complete FIFO item grammar (pulse ticks, X low half, ZF records, DO bit rule, frame id = reg 1015 + 1, 300-word flush);
* the licence block (59500–59511, disjoint from everything the port writes);
* the planner parameter map;
* every file container (`.enc`, `.aut`, `scFlie`, NormalExit, TotalReport);
* the PHB02 pendant protocol.

Status: O2, O3, O4, O7, O10, O12–O15, O18 and the Stop/Pause/E-stop vectors are CLOSED (static). O1, O5, O6, O8, O9, O11, O16 and block 5000 are NARROWED to named live steps. Resume-after-pause and `[9999,16]` are OPEN. O17 is a non-goal.

For the CO2 path, captures change from *discovery* to *confirmation*. One combined **M1-confirmation session** (11-static-findings §7, 10 steps) settles:
* K and the bus cycle (reg 50017/50005);
* block 5000;
* firmware licence gating;
* bit 31 = absolute;
* the stop profile and the home vector;
* limit polarity;
* the card tick period and reg-1016 units;
* the Continue path;
* exception 2/3.

It replaces sessions A–D and most of E/F below. The three most consequential corrections to 00/04/08: sub-cmd 1 = STOP, 2 = HOME, jog distances relative; 103/109 are ZF moves, not dwells, and ticks are motor pulses; register 150 is an FTC key register, not the licence switch (DENY either way).

1. **Prepare the capture rig.** Linux laptop NIC on `10.1.1.10/24` (`nmcli con add type ethernet ifname <nic> ipv4.method manual ipv4.addresses 10.1.1.10/24 ipv4.never-default yes`). Run the Windows tool under Wine from `~/.wine/drive_c/Mlaser` (works: `winetricks mfc42`, 10) **or** on the machine's Windows PC with a mirrored switch port / `tcpdump` on a bridge. Set `EnableLog=1` in `File/ipAdd.ini [Soft]` so `%LOCALAPPDATA%\NexCut\Log\<date>.log` also records `Send Cmd:/Recv Cmd:` with timestamps (04 §9).
2. **Capture filter:** `tcpdump -i <nic> -s 0 -w mlaser-<session>.pcap 'host 10.1.1.168 or host 10.1.1.169 or host 10.1.1.170'` (UDP and TCP; ports 502, 999, 888, 666, 10001). Block `47.104.17.21` and any `:8080` at the firewall.
3. **Sessions to record** (each with a written note of what was clicked and the parameter values in force):
   * A. **[confirmation-only]** Idle start-up with the card on → connect prologue `[9999,5,0,0]` (the "handshake `9999,13`" was an extended-DO write, 11-static-findings §2.1), version, 50000/26 (K word 17, bus cycle word 5), 59600+ parameter reads, poll cadences (O5 cadence; O6 addresses are already known statically: 59500–59511).
   * B. **[confirmation-only]** Jog each axis, step 1/10/100 mm and continuous, slow and fast; the W lifting-table up/down; "go to point" → confirms O3 vectors/units and O7 (slot 4 = lift). Only discovery left here: Y2 dual-drive tracking (reg 2022).
   * C. **[confirmation + one bench discovery]** Home X only, Y only (all-axis home is not needed); press each limit switch by hand with the machine stopped and read DI + axis status → **O8 polarity (still discovery)**, O2 bits (confirmation).
   * D. **[confirmation-only]** Toggle every output from the hardware-test page (gas valves, laser enable, red light, alarm lamp), set DA2 pressure → confirms `9999,2` mask/value and `9999,4` channel−1/mV encoding (O4).
   * E. **[still required, reduced]** Dry run, then a real cut of a 100 mm line at 100 mm/s and 20 mm/s, one square with two layers (different duty/frequency), one with a cool point and a micro-joint → **card tick period and reg-1016 units (O1, discovery)**. Unit = pulses, packing, frame id, prologue/epilogue and opcode encoding are confirmation-only.
   * F. **[still required for Continue]** Trigger a soft-limit stop, an E-stop, pause/resume, stop mid-cut → stop vectors, E-stop bit 30, break-point files are confirmation-only; **how Continue resumes a paused job (N2) is discovery**.
   * G. **[still discovery, fibre only]** (fibre selected) FTC calibration, follow, one pierce → in-stream fibre pierce/ZF records, ZF status units and 10000+2i addressing (O9); DO5/DA1 timing (O10). Port 999 is no longer a target (EC endpoint, never opened).
   * H. **[mostly confirmation, Wine only]** Save parameters, export technology, save `.chf` with lead-in/compensation/reverse toggled → O13/O14 and most of O11 confirmation; text/spline/type-12 craft flags are still discovery.
4. **Dissect** with `mcc/dissector.py`; write findings into `docs/analysis/11-capture-findings.md`, update `mcc/registers.py` and the simulator.
5. **Bench-check without the Windows tool** (after A–C are dissected): run M1's script against the real card with the gantry mid-bed.
6. **Static follow-ups**: done in the static sprint (O12 A6, O15 A2, O14 A7). Remaining static leads: the MainApp Continue path after Pause (N2), the gating of `[9999,16]` (A3), the `CAD slot 21` tool selector for PWM/cool-point tools (A6 V11).

---

## 7. Capture-driven questions → deliverables

| Capture session | Closes | Port artefact updated |
|---|---|---|
| A | O5, O6 (addresses), O18 | `registers.py`, `simulator.py`, start-up sequence in `mccd` |
| B | O1 (position unit), O3, O7 | `commands.py`, M1 jog/home |
| C | O2 (DI/limit bits), O8 | alarm decoder, homing config |
| D | O4 (DO/DA encoding) | `commands.py`, dry-run gate |
| E | O1 (tick), O4 (opcodes), FIFO flow control | `fifo.py`, `items.py`, planner A/B tests |
| F | stop/pause semantics, alarm words | safety state machine |
| G | O9, O10 | M6 |
| H | O11, O13, O14 | `io/chf.py`, `io/params.py` |

---

## 8. Testing strategy — the laser never fires unexpectedly

### 8.1 Test pyramid
1. **Golden unit tests** from package numbers: CRC values; TLV parse of the 22 leaked frames; `segInterp` thresholds; junction formula continuity at 1.6/6/12 mm; raster total 1506.62 mm and the 46 toggle ratios; `.chf` re-save byte identity; XML round-trip; report-row parser on all 2 950 rows incl. garbage.
2. **Simulator tests** (`mcc/simulator.py`, a UDP server): transaction ladder timing, stale-seq handling, exception 2/3 paths, FIFO under-run detection, frame-id/space-margin flow control, jog-while-moving rejection, a full job streamed and consumed at the configured tick; fault injection (drop N datagrams, delay 3.5 s, reboot mid-job, host unreachable).
3. **Record/replay**: every tcpdump from §6 becomes a fixture; the dissector must decode it and the simulator must reproduce the same reply sequence to the same requests.
4. **Hardware-in-the-loop (HIL)** scripts with explicit operator prompts, run only through the arming state machine below.

### 8.2 Safety design (product features, not just test rules)
* **Hardware laser interlock for all development:** the CO2 tube PSU enable / key switch is physically off (and for fibre: emission enable / key off, shutter closed) until M5 sign-off. Software cannot override a missing PSU enable.
* **Arming state machine in `mccd`:** `DISARMED` (no motion, no DO) → `MOTION_ARMED` (jog/home/dry run allowed; laser-enable DO and PWM duty forced to 0 in the item packer, in-stream `9999[2,0x100,·]` records stripped) → `LASER_ARMED` (only from the UI with a confirmation, only while a "job running" token exists, auto-disarms on stop/alarm/comm loss). Gas valves follow `MOTION_ARMED`.
* **Dry-run mode** = the original's 空走: same planner, same stream, duty 0, laser-enable never asserted — this is what M4 runs on the machine.
* **Simulate mode** = stream to `mcc/simulator.py` only; the UI shows the same status.
* **PC-side soft limits always enforced** from `SoftLimitMaxLen` regardless of `MS.EnableSoftLimit`, on every jog target and on the planned path (`mv31 graphic larger than machine range`); refuse to stream until homed.
* **Watchdog:** if the status poll of block 1000 fails for > 1 s during a job → `stopFifo` (0x67 ← 3) + disarm; on any card alarm word ≠ 0 → stop + disarm. Priority and bit meanings are in 11-static-findings §4.7 (1006 bit 30 E-stop, bits 25/26 bus/output fault, bits 0–23 axis faults with 2000+10·slot bits 0–5, 1007 bits 0–5 incl. FIFO starvation, 1031 == 1 restart required, DI alarms via NO/NC with port 0 = disabled).
* **Register write allow-list** (full table: `docs/analysis/11-static-findings.md` §3; A4 §3, A2 §4). Every write is checked before framing and logged with its bytes.
  * **ALLOW:**
    * `0x65` sub-cmd **1** stop (always, the safety primitive), **2** single-axis home, **3** jog on slots 0/1, **5** go-to (after homing);
    * `[9999,5,0,0]` connect prologue;
    * `[9999,2,mask,val]` DO per arming state (DO9 / laser gate only in `LASER_ARMED`);
    * `[101]` ZF stop;
    * `0x66` FIFO frames (dry run: duty 0, no DO9 / PWM-set records);
    * `0x67` 1/2/3.
  * **DENY:**
    * **150** (5555 = FTC factory reset, 9999 = FTC commit) and **151**;
    * **59500–59599** (licence data area and card RTC; reads too);
    * 59600+ hardware-parameter writes (read-compare only);
    * 11000+2k FTC property writes;
    * `100 ← 9999`;
    * 5000–5012 writes;
    * 200/201;
    * `0x65` `[102]`, `[107]`, `[117…]`, `[118…]`, out-of-FIFO `[103/104/109…]`, `[9999,13…]`, `[9999,16]`, `[9999,1…]`, `[7]`, `[4…]`, system home, lift-table and roll-feeder moves (until M3/M5);
    * func 0x26;
    * anything unnamed.
  * Reads of 1000/2000/60001/50000/50200/1050/10000/11000/59600+ are allowed. `READ 5000/9` is a diagnostic only.
* **Deadman for continuous jog:** the card jog is a single relative command (±4 000 000 µm). The port sends the per-axis stop `[1, 1<<slot, 2, vd, 10·vd]` when the key event goes stale (> 200 ms), on key release, on pendant read error / hidraw removal / 1.04 s pendant silence (the vendor has no key-up there, A8), and on focus loss (the vendor stops jogs on `WM_ACTIVATE` inactive).
* **E-stop:** the machine's hard-wired E-stop is the primary. The card reports it as alarm word 1006 bit 30. The UI E-stop sends the stop-all sequence (`0x67←3` or `[1,0x1F,2,vd,10·vd]`, `[101]`, DO9/PWM/gas off), disarms, and stays latched until acknowledged (`mp124`). Read block 5000 once (diagnostic) to see whether the card has its own e-stop input configured.

### 8.3 Performance gates (Python risk R8)
* Streaming jitter test against the simulator with a tick of 250 µs and 1 ms: 10-minute job, the queue depth must never fall below `FifoAlarmNum=30` items while the UI is stress-rendering a 50 k-entity drawing; p99 frame interval < 100 ms, max < 500 ms. Fail → move `mccd` to Rust/C++.
* Planner throughput: a 100 k-contour synthetic DXF plans in < 30 s; else vectorise/offload.

### 8.4 Interchange tests
* Every file the port writes is opened by the Windows tool under Wine (`~/.wine/drive_c/Mlaser`) in CI-like manual runs: `.chf`, `Bk*.xml`, technology `.xml`, `TotalReport.txt`, break-point files.

---

## 9. Deliverables per milestone (checklist)

- [x] M0: dissector + simulator + schema + golden tests (no machine) (gate item "dissector decodes the first tcpdump capture" still open: no capture exists yet; pcap/pcapng decoding is tested on synthetic captures only. See docs/STATUS.md §1.1)
- [ ] M1: `nexcut-mccd --cli` jog/home on the real card; capture sessions A–D dissected; `11-capture-findings.md` (partial: the daemon, CLI/TUI, safety gate, `tools/m1_session.py` and docs/M1-BENCH-SESSION.md are done and pass on the simulator; no hardware session run, no capture, no `11-capture-findings.md`; D9/D11/D12 open)
- [ ] M2: viewer/editor loads DXF/.chf/PLT/G-code, saves `.chf` (partial: the viewer loads all four formats and saves `.chf` byte-identically. No editing operations exist yet (lead-in, micro-joint). Render gate passes as IoU ≥ 0.9 with chamfer 1.000, pixel-identical on 2 of 8 samples. The ManuContour/SortType=4 gate fails: ManuContour.dat is the array order, so a vendor-sorted reference is needed)
- [ ] M3: parameter/layer/technology editors round-trip vendor files (partial: file layer only; every vendor XML and technology file round-trips in `io/params.py`; no editors/property grids; the Wine load check has not been run)
- [ ] M4: dry-run job streaming on the machine (laser interlocked) (partial: offline planner → items → frames (`nexcut-plan`) and a simulator feed test; no streaming in `nexcut-mccd`, no §8.3 jitter test, no machine run; strict xfails X1/X2 for pierce dwell / cut start)
- [ ] M5: first CO2 cut; reports; break-point resume (not started; laser arming deliberately absent)
- [ ] M6: fibre path (after captures G) (not started)
- [ ] M7: nesting, scan engraving, pendant, packaging (`.deb`, AppImage), i18n (partial: `ops/scan.py` scan-fill/fly-line geometry and the 11-language i18n loader exist; no nesting, pendant or packaging)
