# STATUS: where the port stands

Snapshot date: **2026-09-16** (second adversarial-review pass). Written from the working tree at
that date. **Nothing has been run on the machine.** It covers what exists, which PORT-PLAN §4/§8.3
gates pass, and what is still assumed.

Section references follow the analysis docs: `11 §7` means `docs/analysis/11-static-findings.md` §7,
`A3 §8` means `docs/analysis/11-static/A3.md` §8, and `D9` means `docs/DECISIONS.md` D9.

Rule for this file: a gate counts as **met** only if it was actually run and passed. A gate that
passes on relaxed criteria, or on the simulator only, says so in the same sentence.

---

## 0. Numbers at a glance

| Item | Value |
|---|---|
| Full suite, `NEXCUT_SRC=…/Mlaser-v0.0.0.52 QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q -p no:cacheprovider` | **1330 passed, 4 xfailed (all strict), 0 failed, 0 skipped** in 197.8 s |
| Same suite with `NEXCUT_SRC=/nonexistent` (what CI runs) | **1214 passed, 116 skipped, 4 xfailed** in 182.8 s |
| Tests collected | **1334 in 56 files** (+ `conftest.py`) |
| `.venv/bin/ruff check . tools/m1_session.py` | **All checks passed!** |
| CI (`.github/workflows/ci.yml`) | ruff + pytest on Python 3.12/3.13/3.14, offscreen Qt, `-p no:cacheprovider --durations=15`. `SRC` is absent there, so the 116 vendor-golden tests skip |
| `UNVERIFIED` markers in `src/` + `tools/m1_session.py` | **247 lines** (grep), inventoried in §3 |
| Hardware sessions run | **0**. No pcap capture of this machine exists |
| Console scripts | `nexcut` (viewer, layer property page), `nexcut-mccd` (`serve`/`run`, `tui`, `status`, one-shots, `jog`, `home`, `run-job`, `job-status`, `ctl`), `nexcut-plan` (offline planner → frame file) |
| PORT-PLAN §8.3 streaming jitter gate (**simulator only**) | <!-- JITTER-GATE: rerun with `NEXCUT_JITTER_SECONDS=600 QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q -s tests/test_perf_streaming.py` (≈20 min for both ticks); the suite default is an 8 s job --> **met** (2026-09-16, re-measured for this report after the feeder review; **both ticks, a full 600 s job each**, `nexcut-mccd` + `CardSimulator` in one process). **250 µs tick** (cadence 24.8 ms/frame): 24 242 frames in 600.2 s — DONE, every tick consumed, **0 re-sends, 0 starvation events**, queue low water **4 162 items** (card) / 4 395 (reg 1016) against the `FifoAlarmNum` = 30 floor; frame interval p50 **26.4 ms**, p90 **39.2**, p99 **55.8**, max **80.2** — a producer stall of 31.0 ms (p99) / 55.4 ms (max) on top of the cadence. **1 ms tick** (cadence 99.0 ms/frame): 6 061 frames in 600.3 s, low water **4 688 / 4 698 items**, p50 **91.6 ms**, p90 **121.6**, p99 **125.8**, max **139.4** — stall 26.8 / 40.4 ms. Limits on this run: p99 < 106 ms, max < 532 ms (§8.3's 100 / 500 ms × `conftest.speed_factor()` = 1.06 here). Only the two *time* limits are scaled; the stream criteria — DONE, every tick consumed, 0 re-sends, 0 starvation, the queue floor — never are. 20 min 01 s for the pair |
| PORT-PLAN §8.3 planner throughput gate | <!-- PLANNER-GATE: rerun with `.venv/bin/pytest -q -s tests/test_perf_planner.py` --> **not met** (2026-09-16, re-measured for this report), strict xfail **X13**. 250 contours planned in 1.15 s = **4.61 ms/contour**, 168 756 ticks, **6.82 µs/tick** → **461 s for 100 000 contours** against a 30 s budget (15x over; earlier runs measured 478–507 s, so read it as ~460–510 s). Linearity re-checked in the same run: 4.86 ms/contour at 62 contours, 4.69 at 250, **675 ticks/contour** throughout, so the extrapolation is sound. Dominated by per-tick object construction in `plan/items.py` + `mcc/fifo.py`; geometry alone is ~1.35 ms/contour ≈ 135 s, also over budget. Such a job cannot be held in memory as frames either (202 M words), so the fix is a streaming, numpy-vectorised item path — not a constant factor |

Tests per area (collected, 1334 total):

| Area | Tests | Largest files |
|---|---|---|
| `mcc` (framing, CRC, dissector, simulator, commands, registers, transaction, fifo, safety) | 299 | `test_mcc_safety` 79, `test_mcc_protocol_fidelity` 44, `test_mcc_safety_adversarial` 37 |
| `mccd` + `core/config` + `tools/m1_session.py` | 186 | `test_mccd_safety_review` 55, `test_mccd_job` 32, `test_mccd_cli_tui` 29 |
| `plan` | 389 | `test_plan_junction` 175, `test_plan_scurve` 97 |
| `io` + `.chf` | 223 | `test_io_gcode` 53, `test_io_dxf` 41, `test_chf_writer` 37 |
| `ui` | 156 | `test_import_ui_fidelity_review` 53, `test_ui_scene` 29 |
| `ops` | 28 | `test_ops_sort` 17 |
| `core/schema` | 18 | — |
| cross-cutting stream/plan review | 9 | `test_stream_plan_fidelity_review` |
| integration + smoke | 22 | — |
| performance gates | 4 | `test_perf_streaming` 2, `test_perf_planner` 2 |

Adversarial review suites, counted inside the areas above: 245 tests in 7 files
(`test_mcc_protocol_fidelity` 44, `test_mcc_safety_adversarial` 37, `test_mccd_safety_review` 55,
`test_io_fidelity_review` 24, `test_import_ui_fidelity_review` 53, `test_plan_fidelity_review` 23,
`test_stream_plan_fidelity_review` 9).

---

## 1. Milestones

| M | Title | State | Gate — what was actually run |
|---|---|---|---|
| M0 | Foundations, passive instrumentation | **done**, one gate item waiting for the first capture | golden tests green ✔ · dissector on the package logs ✔ (805 transactions, 22 frames, 0 remainder) · dissector on a real tcpdump capture ✘ — **no capture exists** |
| M1 | Hello machine: jog/home on the real card | **software complete, hardware not run** | simulator rehearsal ✔ (`tools/m1_session.py`, 11 tests) · ruler/homing/exception gate ✘ — needs the owner at the machine (11 §7 steps 1–10) |
| M2 | File load and render | **partial** | "renders identically" ✔ only on the relaxed metric (IoU ≥ 0.9 + chamfer 1.000; pixel-identical on 2 of 8) · `ManuContour` order via `SortType=4` ✘ — the gate's premise is wrong (§1.3) |
| M3 | Layers, parameters, hardware config | **partial**: file layer complete, 1 of ~6 property pages | XML round-trip ✔ on every vendor file · Wine load of port-written files ✘ — not run |
| M4 | Job streaming, dry run | **partial**: end to end into the simulator, never onto the card | raster geometry ✔ · vendor item-stream diff ✘ (no vendor capture) · §8.3 jitter gate ✔ **simulator only** · §8.3 planner throughput ✘ (X13) · machine dry run ✘ |
| M5 | Live CO2 cutting | **not started, deliberately** — there is no way to reach `LASER_ARMED` | — (needs D13 first) |
| M6 | Fibre path | **not started** — fibre cutting is refused in `mcc/safety.py` until capture G | — |
| M7 | Nesting, scan, pendant, packaging | **not started**, two pieces present: `ops/scan.py` geometry and the 11-language i18n loader | — |

### 1.1 M0 — done (gate item 2b pending a capture)

* **Project.** `pyproject.toml` (setuptools, three console scripts), CI workflow, `docs/DEVELOPING.md`.
* **`mcc/framing.py`, `crc.py`, `dissector.py`** — log and pcap/pcapng parsing, IP reassembly, TCP/SLL, CLI. 45 tests.
* **`mcc/simulator.py`** — answers 1000/2, 1000/36, 50000, 60001; accepts 0x65/0x66/0x67; raises exceptions 2/3; consumes the FIFO. 14 tests, and it is the card under every `mccd` test.
* **`core/schema.json` + `schema.py`, `io/params.py`** — `Bk*.xml` and technology presets round-trip byte for byte. 64 tests.
* **`io/chf.py`, `io/cp936.py`, `model/*`** — 67 tests.

Gate run for this report:
* **Golden tests: pass**, inside the full suite.
* **Dissector on the package logs: pass.** `python -m nexcut.mcc.dissector --summary-only Log/*.log` →
  805 transactions (603 exception, 195 timeout, 5 send-error, 2 socket-error); 22 distinct FIFO
  frames, 0 with remainder; opcode census 3000:2160, 3001:6, 9999:5, 3002:3, 2001:3, 103:2, 109:1,
  118:1; 113 NC-start events. These match 08 §2–4 and A3, and `test_package_logs` pins them.
* **Dissector on the first tcpdump capture: not met.** No capture exists; the pcap/pcapng paths are
  tested on synthetic captures only. This is the single item between M0 and a clean tick, and it is
  produced by §5 task 1.
* The 11 §8 sub-command names (`1` = STOP, `2` = HOME, `3` = jog) are what the dissector prints, so
  the pcaps of task 1 cannot be misread as the older 04 naming.

### 1.2 M1 — software complete, hardware not run

* **`mcc/commands.py`** — vectors V0–V16 of 11 §2 with a per-vector `unverified` note. 32 tests + 44 protocol-fidelity tests.
* **`mcc/registers.py`** — block 1000, axis and alarm bit maps of 11 §4. 8 tests.
* **`mcc/transaction.py`** — the vendor retry ladder. 27 tests.
* **`mcc/safety.py`** — allow/deny lists of 11 §3, arming states, deadman leases, soft limits, laser-record strip. 79 + 37 tests.
* **`mccd/`** (daemon, gate, IPC, status, CLI, TUI) + `core/config.py` — 186 tests.
* **`tools/m1_session.py`** — the guided 10-step session with a capture wrapper, per-step census and safety checklist. 11 tests.
* **`docs/M1-BENCH-SESSION.md`** — the operator runbook, re-checked against the code for this report (§5 of that file, the flag tables and the job commands).

**Two adversarial safety reviews have now run over this code.** The first closed D9/D11/D12
(R1–R11). The second (R12–R21, 23 new tests in `tests/test_mccd_safety_review.py`, 55 in the file)
found five more defects, three of which could move the machine or hand the card to a second master.
Every fix has a test that failed first:

| # | Defect | Effect before the fix | Fix |
|---|---|---|---|
| R12 | D9 owned the *lifetime* of arming but not the right to move | client A armed; client B, which never armed, got `jog_step` and `home` **onto the card**. Any client connecting after `arm_motion` inherited an armed machine | `_require_arm_owner` on `jog_step`, `jog_continuous_start`, `home`, `load_job`, `start_job`. Stops and `jog_refresh` stay unowned so a stuck client cannot lock the operator out (D9 amendment) |
| R13 | the `flock` lives on the inode, so deleting the lock file handed the card to a second daemon | `acquire; unlink; touch; acquire` → two masters, undetectable from the reply stream | `CardLock.reassert()` compares `stat(path)` with `fstat(fd)` every 1 s; a replaced file is re-taken, a lost lock disarms and refuses `arm_motion` until restart. `release()` no longer unlinks the winner's file (D12 amendment) |
| R14 | the lock directory and file were unvalidated | the D5 socket-directory hardening had not been applied to `CardLock`; the lock file was opened following symlinks | `ipc.ensure_private_dir` (extracted from `IpcServer._prepare_path`, shared and backward compatible) + `O_NOFOLLOW` + an `fstat` uid check |
| R15 | the lock key was the raw IP string | `10.1.1.168` and `::ffff:10.1.1.168` were two keys, i.e. two masters | `ipaddress` canonical form with IPv4-mapped folding. What a lock still **cannot** see is pinned as a known gap by `test_r15_*`: a host name vs its address, a second NIC, NAT, Wine Mlaser, another workstation |
| R17 | `stop_job` on a job that had not started neither stopped it nor released it | `load_job` → `stop_job` → `start_job` put `[1, 2, 3, 1]` on the FIFO control register: the card was told to **start** a program the operator had already stopped. And the job never reached a final state, so every later `load_job` was refused `busy` for the life of the daemon with no IPC way out | `request_stop` finalises a never-started job; `_stream` re-checks the flag on entry and immediately before `0x67 ← [2]`; the feeder carries the D10 motion epoch of its own `start_job` request |

Reviewed and found sound, now pinned by tests: the D11 key table (exhaustive 112 keys × 4 modes ×
case matrix — motion in normal mode is exactly the ten named keys, in the home menu exactly
`x X y Y b B`, nothing in the read prompt or the help overlay; no escape sequence and no control
code decodes to a motion key by accident); client vanishing by clean close, SIGKILL, socket error
and **half close**; the dry-run strip on the wire (tick duty, laser DO mixed with a motion DO,
`MISC_PWM`, `MISC_PWM_5V`, DA on both candidate channels — the simulator saw duty 0, the laser bit
gone, DO1 kept, `outputs & 0x100 == 0`, PWM_FREQ 0); a grammar violation past frame 128 while
streaming; and E-stop mid-job.

**Residual risks, documented not fixed** (D9): a *frozen* arming client (SIGSTOP, hung syscall)
keeps the arming for ever — D9 rejected an idle timeout; with R12 nothing else can move and every
connection can still stop, estop and disarm. And `_release_arming` has a window in which a new
client can `arm_motion` between `_arm_owner = None` and `_disarm_and_stop` and is then disarmed
while holding an `ok` reply — the fail-safe direction (the machine ends DISARMED and its next
motion command is refused with the D9 message).

**Gate: not run.** It needs the machine: a 100 mm ruler move within 0.1 mm; repeatable homing; no
exception other than 3-while-moving; position-register semantics.

Known limits of the native path (`docs/M1-BENCH-SESSION.md` §11):
* step 4 (absolute move) is deferred — IPC has no absolute move, only the vendor/Wine variant;
* step 8's FIFO stream exists in software (`nexcut-mccd run-job`) but has only ever run against the
  simulator, so the vendor variant stays the primary source of the tick period and O6's FIFO half
  stays open;
* the native continuous jog is capped at 20 mm/s by D1;
* `tools/m1_session.py` reconnects lazily, and a reconnect drops the arming (D9) **and** the right
  to move (D9/R12): a step interrupted by a link loss must be restarted with `--steps N`.

PORT-PLAN §9 wording "capture sessions A–D dissected; `11-capture-findings.md`": not started; that
file does not exist.

### 1.3 M2 — partial

* **Importers.** `io/dxf.py` (ezdxf) 41 tests, `io/gcode.py` 53, `io/plt.py` 16; `.chf` v1–v5 read, v5 write.
* **`ops/import_gates.py`** (overlap/connect/minimal/sort gates) and **`ops/sort.py`** (SortType 0–7). 17 tests.
* **UI.** `ui/{app,main_window,canvas,scene,render,layers,loader,i18n}.py`: open DXF/`.chf`/PLT/G-code,
  save/save-as `.chf`, recent files, layer dock, selection info, start points, direction arrows,
  index numbers, measurement tool, offscreen `nexcut render`. 100 tests + 53 review tests.
* **Absent.** Lead-in defaults, auto micro-joint, and *every* editing operation. `.chf` re-save is
  identity only; there is no edit-then-save path.

Gate measured 2026-09-16 (render each sample, compare ink masks against `tools/out/*.png`, re-save each `.chf`):

| Sample | Size port / ref | Ink mask pixel-identical | IoU (64 cells) | chamfer (2 px) | Re-save byte-identical |
|---|---|---|---|---|---|
| File/autosave.chf | 800×311 / 800×311 | no | 0.945 | 1.000 | yes |
| File/Temp/tempGraph.chf | 800×799 / 800×800 | no | 0.960 | 1.000 | yes |
| Graph/Work1/1.chf | 800×797 / 800×797 | no | 0.944 | 1.000 | yes |
| Graph/Work1/2.chf | 800×607 / 800×607 | **yes** | 1.000 | 1.000 | yes |
| Graph/Work1/3.chf | 800×473 / 800×473 | no | 0.980 | 1.000 | yes |
| Graph/Work2/1.chf | 800×607 / 800×607 | **yes** | 1.000 | 1.000 | yes |
| Graph/Work2/2.chf | 800×473 / 800×473 | no | 0.980 | 1.000 | yes |
| Graph/Work2/3.chf | 800×797 / 800×797 | no | 0.944 | 1.000 | yes |

* **"Renders identically": met on the relaxed metric only.** IoU ≥ 0.9 everywhere, chamfer 1.000 on
  all 8, pixel identity on 2 of 8 — the line rasteriser differs (Qt vs the reference tool's integer
  DDA). The IoU gate alone was shown to be blind to mirroring on these symmetric samples;
  `chamfer_score` plus an asymmetric reference now catches mirror, rotation and bulge errors.
* **"24-contour job sorts like `ManuContour.dat` with `GRP.SortType=4`": not met, and not meetable
  as written.** `ManuContour.dat` is `24, 0..23` — the array-copy order of `autosave.chf` (row
  serpentine, `GRP.Array*`), not a sort result. No sort type reproduces it (NEAREST →
  `0,4,8,9,7,5,…`; BOTTOM_TO_TOP → `0,1,2,5,4,3,…`; all others differ too). The sample holds no
  sorted job, so the vendor sort algorithms cannot be checked at all. The gate needs a new
  reference: a vendor-sorted job produced under Wine (§5 task 5).
* **Performance.** 50 k one-segment contours open in < 3 s for DXF lwpolyline, G-code, PLT and CHF
  (the 50 k-contour `.chf` in 1.84–2.10 s since the vectorised token reader closed X12). 50 k
  *separate* DXF `LINE` entities still take ≈ 8.2 s: strict xfail X11, dominated by the ezdxf parse.

### 1.4 M3 — partial (file layer complete, one property page of about six)

* **`io/params.py` + `core/schema.py`.** Typed parameter documents, missing-attribute defaults,
  atomic write with verify; technology preset parse/serialise (`P…Param11` wrapper), preset ↔ layer
  transfer, `ParamStore` fallback chain (`Bk*` → `SecondBk*`). Round-trip tests on every vendor XML
  and technology file in `SRC`.
* **`ui/property_grid.py`** — a schema-driven `CBCGPPropList`-style grid (06 §8) built from
  `core/schema.Descriptor` rows, grouped by the `Group.Item` half of the `lang.txt` label, with
  `UN.SpeedUnit` / `UN.AccUnit` / `UN.GasPressureUnit` display units, enum combos, checkbox bools,
  descriptor min/max validation and a per-page cross-check hook. Rows whose meaning is UNVERIFIED
  are read-only. 23 tests.
* **`ui/pages/layer_co2.py`** — the CO2 layer page (02, A6 §3) over the 21 editable `CO2LayerParam`
  attributes, with the `lp19` cut-height cross-check and Load/Save exchange with the vendor
  technology presets. Process type, pierce stages, lead lines and cool points are read-only because
  A6 §3 leaves it UNVERIFIED whether the CO2 process reads the `ManuType` the vendor dialog writes
  into the fibre table. 20 tests. Reachable as `nexcut --layer BkLayerPara.xml`.
* **Absent.** The hardware, machining, software and graph-rule pages; the fibre layer page (164
  attributes, real pierce-stage editing, the `A250607_*` fibre/CO2 selector warning); the
  power/frequency curve editor behind `PWMCurveNodes`/`FreqCurveNodes`; the crafts editor for
  per-contour lead-in/out and cool points; writing `BkLayerPara.xml` back from the dock (only
  preset exchange is wired); hardware-page read-back of 50000/50200/59600+ (the daemon reads
  50000/26 only, for K, the bus cycle and ZFType).
* **Gate.** XML round-trip ✔. "The Windows tool (under Wine) loads files saved by the port" ✘ —
  not run. The technology XML the page writes is byte-identical to a vendor preset except for the
  `CutFreq` the 13 shipped CO2 presets lack, so it is the first candidate for Wine session H.
* **Known fidelity gap:** unknown attributes are dropped on rewrite (X7). X8/X9 (boost `nan`/`inf`
  spelling and the whole-literal number grammar) were closed on 2026-09-16.

### 1.5 M4 — partial: end to end into the simulator, never onto the card

* **`plan/`** (contour_fit, junction, lookahead, scurve, sampler, pwm_schedule, items, params,
  `__main__`) — 389 tests. Goldens: `segInterp`, `JumpAddTime`, `VelDecc`, junction, S-curve. The
  1506.62 mm raster reproduces as geometry. All 22 leaked frames re-encode word for word; the
  prologue frames 0x39/0x1f8, the epilogue frame 0x279, the constant-velocity run of frame 1931 and
  the rapid start of 0x279 match tick for tick.
* **`mcc/fifo.py`** (packer, `FifoFeeder`, reg-1016 byte accounting) — 13 tests.
* **`nexcut-plan`** writes a dry-run frame file and never opens a socket — now pinned by a
  subprocess that runs the whole CLI under `sys.addaudithook` failing on any `socket.*` event, and
  asserts `"socket" not in sys.modules` afterwards.
* **`mccd/feeder.py` + the five `*_job` IPC commands + `nexcut-mccd run-job`.** `JobFeeder` pulls
  packed frames from a *lazy* source, clears the FIFO (`0x67 ← [1]`), fills it through `FifoFeeder`
  under reg 1015/1016 accounting, starts the program (`0x67 ← [2]`), watches reg 1019 and the
  starvation alarm, re-sends an unacknowledged frame, drains, and always ends with `0x67 ← [3]` +
  `0x67 ← [1]`. It needs `MOTION_ARMED` **on its own connection** (D9/R12) and a job token, refuses
  to load while `LASER_ARMED`, and dies with the arming connection. 32 tests in `test_mccd_job.py`.

**The third adversarial review (stream and plan fidelity, `tests/test_stream_plan_fidelity_review.py`,
9 tests) found four more defects in this path, all fixed:**

| # | Defect | Effect before the fix | Fix |
|---|---|---|---|
| F1 | `_fill` replaced `_sent_window` and reset `_unacked_since` on every fill pass | the 600 ms `ipAdd.ini FifoTimeout` was re-armed at every ~30 ms status poll, so during continuous streaming the whole re-send/abort net was inoperative: 1500 frames sent with reg 1015 pinned at 0 produced **0 re-sends and no abort** | `_sent_window` holds `(send time, frame id, words)` for every frame reg 1015 has not confirmed; `_check_ack` drops the acknowledged prefix and measures the timeout from the **oldest** unacknowledged frame. New public `JobFeeder.unacknowledged_ids()` |
| F2 | a frame left unacknowledged by one pass was dropped from the window by the next | nothing could re-send it even if the timeout had fired | same change; pinned by a "not forgotten by the next fill pass" test and bounded by an "acknowledged prefix is dropped" test |
| F3 | the first fill used the status poll taken **before** `0x67 ← [1]`, then sent `0x67 ← [2]` unconditionally | a card still holding an aborted program reports a small reg 1016 → the first fill sends nothing → the port starts an **empty** FIFO program: the A2 §2.4 starvation case, self-inflicted | wait for a post-clear poll before the first fill; fail with `no frame fits the card FIFO after 0x67 ← [1] (reg 1016 = …)` if the first fill put nothing in the card |
| F4 | `JobState.RUNNING` was published before clear/fill/start | `start_job`, which waits for RUNNING to say whether the job took off, could answer RUNNING for a job about to fail | RUNNING is set after `0x67 ← [2]` |

A fifth finding is a fidelity gap rather than a defect: **F5** — `build_job` wired `ZF.ZFUpSpeed` and
`ZF.ZFDockHeight`, which only the record-9 *dock* branch reads and no CO2 contour emits, while
`StreamConfig.zf_move_speed` — the value the emitted prologue `103` and epilogue `109` actually read
— stayed at the class default 100.0. Any machine whose follow speed is not 100 mm/s would have been
commanded 100. `ZF.ZFFollowSpeed` is wired now; the vendor goldens (`103[1000,0]`, `109[1000,0]`,
`118[4,0,35]`) are unchanged, because both speeds are 100 on this machine — which is exactly why
**which descriptor `g+0x49c8` is remains UNVERIFIED** (§3.5, session E).

Re-derived from `tests/data/mcc/fifo_frames_2025-07.txt` in the same review, **no defect found**,
now pinned by tests: the 300-word rule and byte accounting (all 22 leaked frames decode to exactly
297 data words; max vector 310 words = 1254 B < 1448 B); the ZF prologue/epilogue item sequences;
the A9 §2 dwell correction (none of the 22 frames contains a `2001[ms, 3000]`; all three `2001` are
the ZF `[0x03000002, 20000]` form, so the port emits no dwell ticks); the A9 §3 entry-velocity
finding (0x3a has 4 pulses in its first 9 ticks, impossible from rest at layer-2 jerk — 0x39 and
0x3a are from different runs); pulse accounting end to end (`build_job` on `File/autosave.chf` → 24
contours, 23 rapids, 47 quantiser calls, max join gap **1.94e-10 mm**, 1277 frames / 126 089 ticks,
streamed sum **72 748 / 76 886** pulses against the exact 72 748.772 / 76 886.903 — within one pulse
and equal to `trunc(net · p/mm)`); and int16 headroom (max |tick| on that job = 39 pulses against
the 750 mm/s ceiling of 48.4 pulses per 250 µs tick, so no overflow path is reachable).

* **Gate 1 (simulator): partial.** The raster total and toggle geometry match, but no vendor *item
  stream* of the raster exists to diff against. `CardSimulator.consumed_words()` / `consumed_items`
  is the hook for that diff once a vendor capture exists (11 §7 step 9). The jitter half passes (§0).
* **Gate 2 (machine): not run.**
* **Closed fidelity gaps** (from the A9 re-trace of `0x4427e0` and the CADModule look-ahead core;
  X1 and X2 are ordinary passing tests now): the vendor emits **no** record after the laser DO when
  `LaserOnDelay = 0`, one `2001[t,3000]` wait record when it is not, and the gas delay as its own
  one-shot wait record — never the stationary ticks the port used to emit; and no start-from-rest
  profile at layer-2 parameters can put 4 pulses in 9 ticks at any carry.
* **Still open:** the planner throughput gate (100 k contours in < 30 s) is **not met** — strict
  xfail X13, numbers in §0. `build_job` also materialises every frame, so a job that size does not
  fit in memory as frames at all.
* **Port-only safety rules added by the plan review:** glyph gaps > 0.01 mm raise an error; runs of
  dropped segments are filled from the global clock.

### 1.6 M5–M7

* **M5: not started.** There is no `arm_laser` over IPC (D5). `strip_laser_records` removes
  DO9/PWM/DA records unless `LASER_ARMED`, and `LASER_ARMED` cannot be reached from any client.
  Gas DA2 non-zero is denied (`allow_nonzero_da=False`). The missing piece is a decision, not code:
  D13 (§4).
* **M6: not started.** Fibre cutting is refused in `mcc/safety.py` until capture G.
* **M7: not started.** Present: `ops/scan.py` (hatch, serpentine, fly-line link builder from
  `0x10096fc0`) and the i18n catalog for the 11 vendor language files (`nexcut --lang`). Absent:
  nesting, the PWM position-compensation run-time, the PHB02 pendant (hidraw), packaging.

---

## 2. Strict xfails (4)

Each documents a known divergence. Fixing one turns the test into an XPASS failure, which forces the
marker to be removed — so this list cannot rot.

Nine of the original thirteen were closed on 2026-09-16 and are ordinary passing tests now: **X1**,
**X2** (the A9 re-trace of the dwell builder and the look-ahead core), **X3**, **X4**, **X5** (D9,
D11, D12 decided and implemented), **X8**, **X9** (boost double spelling and the whole-literal
number grammar), **X10** (spline sampling honours the chord step), **X12** (vectorised `.chf` token
reader). What remains:

| # | Test | Sharpened reason | Resolved by |
|---|---|---|---|
| X6 | `test_io_fidelity_review::test_legacy_reserved_lines_with_content_are_not_lost` | `.chf` v2–v4 reserve one line per graph that the reader skips unread (03 §9). No shipped sample puts content there, so it is not known whether the vendor ever writes any — the port silently drops what it cannot model. This is a **design choice waiting to be made**, not a missing measurement: keep the bytes in a model slot, or reject such a file loudly | a decision (§4 `.chf`-legacy), optionally informed by a Wine write test |
| X7 | `test_io_fidelity_review::test_unknown_attribute_survives_rewrite` | Unknown XML attributes are dropped on rewrite. ParaModule's set-value path (`0x10010cdf` FindElem/AddElem/SetAttrib) keeps a CMarkup DOM and *may* preserve them — UNVERIFIED, and it decides whether a file round-tripped through the port loses data a future vendor version added | Wine session H: add an attribute, load and re-save in Mlaser, diff |
| X11 | `test_import_ui_fidelity_review::test_open_50k_separate_dxf_lines` | 50 k separate `LINE` entities take ≈ 8.2 s against a 3 s budget, of which ≈ 4.5 s is the ezdxf document build alone. No amount of tuning the port's own gates gets under the budget; it needs a reader that never builds an ezdxf document | performance work: a streaming DXF entity reader (§5 task 7) |
| X13 | `test_perf_planner::test_100k_contour_job_plans_in_under_30_s` | PORT-PLAN §8.3: 100 k contours project to 478–507 s against 30 s, measured linearly at 62/250/1000 contours (675 ticks/contour throughout). Dominated by per-tick object construction in `plan/items.py` + `mcc/fifo.py` at **7.08 µs/tick** (67.5 M ticks = 4.7 h of machine time); geometry alone is 1.35 ms/contour ≈ 135 s, also over budget. A second, harder limit: 202 M words of frames cannot be held in memory, so the fix is a streaming, numpy-vectorised item path — not a constant factor | performance work (§5 task 6) |

---

## 3. UNVERIFIED inventory, grouped by what resolves each item

247 `UNVERIFIED` lines live in `src/` and `tools/m1_session.py`. They fall into seven groups, and the
only thing that matters operationally is **which single activity clears each group**:

| Group | Activity that resolves it | Needs the owner? | Needs the machine? | Blocks |
|---|---|---|---|---|
| §3.1 | **M1 bench session**, native steps 1–7 and 10 (`tools/m1_session.py` + `docs/M1-BENCH-SESSION.md`) | yes | yes | M1 sign-off, D2, D7-open, homed soft limits |
| §3.2 | **M1 bench session, step 8** — FIFO tick period (vendor dry run preferred; native `run-job` optional) | yes | yes | M4 gate 2, O1, reg-1016 units |
| §3.3 | **M1 bench session, step 9** — vendor Pause/Continue/Stop, and the A/B item stream | yes | yes | N2, planner fidelity, M4 gate 1 |
| §3.4 | **Session E** — a real cut with the laser on, after M1 and D13 | yes | yes | M5 |
| §3.5 | **Capture G** — fibre source and height follower | yes | yes | M6 |
| §3.6 | **Wine session H** — vendor tool under Wine, **machine not needed** | yes (their Wine prefix) | no | M2 sort gate, M3 gate, X7, most import/XML questions |
| §3.7 | **Static re-trace** — disassembly, desk work, anyone can do it | no | no | planner fidelity details |
| §3.8 | **Port choices** — no capture settles them; they are design parameters | no | no | nothing; revisit as design |

### 3.1 M1 bench session, native steps 1–7 and 10

**Step 1 — connect and start-up reads** (`READ 1000/2, 1000/36, 50000/26, 50200/100`, `[9999,5,0,0]`):
* K = 1000, used in `mcc/safety.py` soft-limit words, `commands.MachineParams.k`, `m1_session.EXPECT_K`, and as the D1/D2 unit base;
* bus cycle reg 50005 = 250 µs (`m1_session.EXPECT_BUS_CYCLE_US`, simulator initial SystemRW);
* ZFType word 6 = 1; the lead unit in AxisRW word 9 (µm or mm); the AxisRW word names other than 9/10 (`registers.UNVERIFIED_NOTES`); axis-0/1 pulses and lead versus the XML (C23);
* success reply layouts derived from the PC decoder (`framing.encode_reply_vector`, simulator replies);
* that the card answers the 1-word marker read `READ 1000/1` (the m1 tool uses it to cut the pcap into steps);
* the upper bound of the combined 60001 read = 144 words (`safety.py`);
* the meaning of `[9999,5,0,0]` — only its necessity is known (`commands.connect_prologue`);
* simulator: a request with a non hi-first CRC is ignored.

**Step 2 — block 5000 and the E-stop:**
* whether reg 1004 is the raw DI level or already inverted by the card via block 5000 (`registers.di_active`, `config.watchdog_di_alarms` — off by default for exactly this reason — and the `mccd/status.py` DI mapping);
* alarm word offsets 1000+6/+7 and the bit meanings as the simulator emulates them;
* alarm_1 bit 24 = on-board follower axis (INFERENCE, low-medium);
* that the E-stop disables the servo drives (step 7 relies on it).

**Step 3 — step jog X +5 mm at 20 mm/s:**
* **O6 firmware gating:** that a jog is accepted without the licence exchange;
* exception 3 on jog-while-moving, and the simulator's busy code;
* how AxisCommandType 3/4/5 splits between jog and move (`registers.AxisCommandType`);
* axis RO word names 3–9 (encoder position, stop pulse, mileage…; table order only);
* that axis RO word 2 is in motor pulses (≈ 258.04 / 257.99 p/mm), not µm — `config.position_counts_per_mm`, `position_scale_verified = false` (D2), the TUI position display; the simulator uses µm;
* simulator conventions: reg 1008 run values 0/1/2, and how long a jog keeps the card busy;
* the physical direction of the TUI keys (Up = Y+, PgUp = W+).

**Step 4 — bit 31 = absolute:**
* `commands.move_axis_absolute` sets bit 31 for an absolute target (INFERENCE medium-high);
* that the card origin is the home corner, so soft limits are `[0, SoftLimitMaxLen]` (`config.soft_limit_x_mm/_y_mm`, `safety.soft_limits_word`).
* *Not reachable natively:* IPC has no absolute move, so this step is vendor-only for now.

**Step 5 — continuous jog and key-release stop:**
* the physical meaning and unit of `vd` in `[1, mask, 2, vd, 10·vd]` (`commands.stop_all`, `jog_release_stop`);
* the card's default deceleration for `stop_all_no_decel`;
* `max_jog_distance_word = 4 000 000` as a physical bound;
* simulator: that a stop halts dead at the current position.

**Step 6 — home X, then Y:**
* homing speeds and back-off are held on the card (AxisRW +5/+6, INFERENCE high);
* axis-status bits 0–5 during homing — the gate trips only while jogging (D7);
* `home_system` mask bit 0x10000 (INFERENCE low); the port does not use it.

**Step 7 — limit switches by hand; Y2 tracking:**
* **O8** limit polarity per DI pair (`mccd/status.py` mapping to DI pairs);
* **the open half of D7:** can the operator jog off a pressed hard limit while `alarm_1 ≠ 0`? The gate refuses all non-ALWAYS writes in that state today;
* Y2 dual-drive tracking on slot 2 (reg 2022).

**Step 10 — power-cycle, `READ 1000/2` at once:**
* exception 2 semantics and duration (simulator `not_ready_s`, `EXC_NOT_READY`);
* the simulator's reply codes for an unknown vector, a malformed stop or motion vector, and an unsupported function (code 1).

### 3.2 M1 bench session, step 8 — the FIFO tick period

* **O1: the card-side tick period, 250 µs assumed.** Planner side: `plan/sampler.CYCLE_US`, `plan/items`, `plan/__main__` durations. Simulator side: a 1 ms tick estimate.
* Reg-1016 units: bytes; whether frame prefixes count; how an item is charged (`mcc/fifo.py`, simulator `space_margin`).
* Simulator FIFO depth.
* That a re-sent frame id is acknowledged, not re-queued.
* The exception for an over-long frame.
* That motion commands are refused with exception 3 while the FIFO runs.
* That `0x67 ← [1]` discards queued frames (`safety.py`).
* That the FIFO starts without a licence exchange (**O6, FIFO half** — closed only by running `run-job` natively on the card).
* The vendor dry-run speed source.
* The m1 tool's tick-estimate method (30 ms poll resolution divided by the tick count).
* **Simulator caveat, now explicit in `tests/test_perf_streaming.py`:** `fifo_starvation_alarm` is off, because the simulator reports "FIFO empty" the instant the last item is consumed — `0x67 ← [3]` therefore always arrives after the FIFO ran dry, and a card that alarmed there would fault at the end of every vendor cut. What a real card does is UNVERIFIED.

### 3.3 M1 bench session, step 9 — vendor Pause / Continue / Stop and the A/B item stream

* **N2:** how Continue resumes a paused job. There is no resume primitive in the card vocabulary.
* The stop-manu DO bulk list: the vendor uses 27 fixed offsets plus a vector; the port uses this machine's DO list (`commands.do_ports_off_on_stop`).
* `[101]` means "ZF stop" (INFERENCE medium-high).
* Planner motion fidelity, diffed against the vendor frames of the same drawing:
  * rapid-end tails (0x1f8/0x39 unexplained);
  * how segment vectors are stitched, including the duplicated boundary sample and the dropped-run fill (`plan/sampler.py`);
  * whether the node list carries a leading `s = 0` (`lookahead.py`);
  * the `P8/P9` slow-end mirror (`apply_slow_end`);
  * which speed the vendor logs;
  * the rapid planner call (A6 §1.4).
* The job start position: the port adds no initial rapid (`plan/__main__`).

### 3.4 Session E — a real cut, laser on (M5, after M1 and D13)

These are laser records. A dry run never emits them, so steps 8/9 cannot settle them.

* The pierce dwell is settled statically (A9 §2): `ContourLaser.pierce_dwell_ms` is `layer.LaserOnDelay`, emitted as one `2001[ms,3000]` wait record, never as stationary ticks; the gas delay is its own wait record. What a capture still has to confirm is **which** of `GC.GasDelay` / `DirectGasDelay` / `ChangeGasDelay` a given contour sums, how much elapsed travel time the vendor subtracts (`ContourLaser.gas_delay_ms`), and that a non-zero `LaserOnDelay` really produces a single wait record (set it to e.g. 200 ms with the gas previously off).
* That gas switches off once at job end, not in each contour epilogue (`items.JobStreamBuilder`).
* The role of the one-tick `(0,0)` record between 3002 and the gas DO.
* PWM lead compensation: sign, interpolation, the −3 forward-cycle sign convention (`plan/pwm_schedule.py`).
* Power/frequency-vs-speed table construction and smoothing types 1/2.
* The in-stream DA channel base (`safety.fifo_laser_da_channel_words`, which strips both candidates).
* **ZF record descriptors in CO2 frames — sharpened by review finding F5.** `StreamConfig.zf_move_speed` now takes `ZF.ZFFollowSpeed`, but A3 could not tell `ZFFollowSpeed` from `ZFUpSpeed` apart because **both are 100 on this machine**, so which descriptor `g+0x49c8` is stays UNVERIFIED. To settle it, change *one* parameter between two captures: `ZFFollowSpeed` vs `ZFUpSpeed` (the `103`/`109` speed word), `zf_book_value` 35 (`118[4,0,35]`), `sub_device_type` 2 or 3.
* CO2 in-memory UD_* planner values at `layer+0x380..0x3b8` (`plan/params.py`) — a static re-trace also works.

### 3.5 Capture G — fibre only

* ZF status units, 10000+2i single-register spacing, the 10 s ZF status cadence (`registers`, `config`).
* Fibre pierce and follow stream; DO5 gate timing; laser nameplate.
* DO6 red pointer treated as a laser hazard class (`safety.laser_do_ports`).
* Fibre cutting is refused outright until then (`safety.py`).
* Dissector: the EC3710 frame heuristic, and whether the TCP path (`AccessType=0`) uses the same framing.

### 3.6 Wine session H — the vendor tool under Wine, no machine needed

This is the **largest group and the cheapest to run**: it needs the owner's Wine prefix and an
afternoon, and it unblocks the M2 sort gate, the M3 gate and X7 at once.

**`.chf` and model:** SPLINE flags int1/int2; text ctor defaults `d130/d138/d140`;
`CGlyScan`/`CGlyContourEx` precision pass; the v1–v4 writer (none shipped); CRT `_fltout2`
formatting; the reserved-line content (X6); the default contour precision 0.01 (`model/graph.py`,
`io/dxf.py`); ellipse flattening (no sample holds an ellipse).

**XML parameters** (`io/params.py`, `core/schema.py`): ANSI = cp936; first duplicate element wins;
`MNF_WITHREFS`; other slot numbers accepted; the "re-created" write and the meaning of "default";
`SecondBkManuPara` fallback order; unknown attributes kept (X7); the boost spelling and
whole-literal number grammar (X8/X9 — implemented from static evidence; a Wine diff still has to
confirm the `bad_lexical_cast` behaviour); MSVCR100 3-digit exponent; `_wtoi`/`_wtoi("")` prefix rules.

**DXF import:** SOLID/TRACE/3DFACE as outlines; XLINE/RAY skipped; rational and non-cubic splines
refit; `read_color` layer mapping; `$INSUNITS` ignored; entities with non-finite coordinates skipped;
text through ezdxf fonts instead of GDI; INSERT becomes a Group; export R2000/ANSI_936, layer name =
index, ACI = layer+1.

**G-code import:** LP/M17/L subprogram model; I/J tolerance 0.01 mm and the R-arc chord threshold;
initial modal state; G0 contour breaks; cp936 comments; G90.1/G91.1 handling.

**PLT import:** 40 plu/mm; IP default; LB not rendered; fills as outlines; pen-to-layer mapping;
reflecting SC sweeps; the ESC skip rule.

**Import gates and sorting** (`ops/import_gates.py`, `ops/sort.py`): gate order; MicoGraphGate length
criterion; OverlapGate whole-glyph Hausdorff distance; connect chaining rules; group cached geometry;
all sort strategies, the nearest-sort start at (0,0), `SortIsSmallFirst`. **A vendor-sorted job also
fixes the broken M2 gate reference** (§1.3).

**Scan:** row placement, `scanDirection` ≠ 0, connector sampling length; 60 mm side lines; link-builder variant 4 (`ops/scan.py`).

**UI** (screenshots): layer colours, marker look and sizes, canvas bed 1300×900 at the origin, background colour.

**i18n:** first duplicate id wins; English as the fallback column; a merge-recovered record loses (`ui/i18n.py`).

**Planner parameter defaults:** AddTime/Is4Freq when absent; the `p9` ctor default; the node-string `"0,48,…"` parsing and the empty-string rule (`plan/params.py`, `pwm_schedule.py`).

### 3.7 Static re-trace — disassembly, desk work

* Rapid planner call (A6 §1.4).
* What `CADMODULE_NODE_FACTOR` 0.99 multiplies.
* S-curve `0x1000eeb0` with no speed change; last-phase evaluation past T; sample order (`plan/scurve.py`).
* x87 precision control of `round_half_away` (53-bit assumed).
* HAL address-filter interval ends (`registers.vendor_would_send`).
* Dissector ladder grouping heuristics (`LADDER_GAP_S`, `max_rung`).
* Gating of `[9999,16]` (N6). DENY until then.
* **Done in this phase:** the look-ahead core (A9) and the dwell builder `0x4427e0` (A9 §2). Two
  corrections they produced still have to be written into `docs/analysis/05-motion-pipeline.md`:
  §7.4 names `0x100114d0`, which is MotionCtrl.dll's build of the core and **not** the one a cut
  runs (the cut path is CADModule `0x100fdc50`, reached from plan `0x100fffe0`), and §7.2's "0.99
  factor" is a `VelDecc.txt` logging filter on the per-piece speed factor, not a multiplier on node
  speeds.

### 3.8 Port choices — no capture resolves them

These are not vendor facts. They are design parameters, and changing one is a decision, not a
discovery.

* **D1:** un-homed limits 10 mm and 20 mm/s; the token bucket.
* **D4:** timeouts of 150 ms; poll periods 90 ms / 1 s / 1 s / 10 s.
* **D6:** deadman 200 ms; pendant silence 1040 ms.
* **D12:** the 1 s card-lock re-assert period (`_CARD_LOCK_RECHECK_S`, added by review R13).
* Timers: supervisor/service period 20 ms; `home_timeout_ms` 120 s; reconnect period.
* TUI key-repeat windows (`--hold-initial-ms` 700, `--repeat-gap-ms` 150).
* `m1_session` bounds: step ≤ 10 mm, speed ≤ 50 mm/s, hold ≤ 3 s.
* Planner and geometry: `plan/sampler.MAX_JOINT_GAP` 0.01 mm as a planner refusal; the dropped-run
  fill; `contour_fit` polyline-plus-blend instead of the openNURBS refit (the 1e-5 and 0.005 rad
  constants, corner blend 0.1 mm / R 2 mm); the `junction` physical-model option.
* Feeder: `unacked_timeout_s` from `ipAdd.ini FifoTimeout` = 600 ms is a vendor number, but
  *re-sending* rather than dropping the frame is a port deviation (11 §5.1, A3 §7) — and one that
  only actually worked after review finding F1.

---

## 4. Design decisions: what is closed, what is still open

### 4.1 Closed and enforced (`docs/DECISIONS.md` D1–D12)

All twelve entries are **decided**. Three of them were the open ones a phase ago (D9, D11, D12) and
are closed in code; five carry amendments written by the safety reviews of this phase.

| Id | Decision, in one line | Amended this phase |
|---|---|---|
| D1 | Un-homed jogs: ≤ 10 mm per step, or ≤ 20 mm/s under the deadman | — |
| D2 | Soft limits from read-back positions only once the position scale is verified (`position_scale_verified = false` today) | — |
| D3 | The default card address is the simulator; a real card only via an explicit `--card-ip` | — |
| D4 | Priority bus; short single-try reads; the vendor ladder for writes | — |
| D5 | The enforcement boundary is the `nexcut-mccd` process; a fixed IPC vocabulary, no raw register path | yes — job streaming, the socket-directory check, and **R17** (a stop on a job that never started now finalises it) |
| D6 | Deadman leases and input sources; a lease cannot be refreshed while DISARMED | — |
| D7 | Watchdog scope and recovery — **one half still open**, see §4.2 | — |
| D8 | Card-side motion gates mirrored on the PC, under one motion lock | — |
| D9 | **Arming is owned by the IPC connection that asked for it** | yes — **R12**: the owner also owns the *right to move*; stops stay unowned; the frozen-client and `_release_arming` residuals are recorded |
| D10 | Motion epoch: nothing queued before a stop may be sent after it | — |
| D11 | **No letter key starts motion; the key table is data** | survived an exhaustive re-review (112 keys × 4 modes × case) unchanged |
| D12 | **One master per card: an advisory lock keyed by the card address** | yes — **R13** re-assert every 1 s, **R14** directory/file ownership, **R15** address normalisation, and the cases a lock provably cannot see |

### 4.2 Still open

| Id | Question | Blocks | What decides it |
|---|---|---|---|
| **D13** | Laser arming: the IPC shape, the job token it must quote, the operator confirmation, the auto-disarm rule. D5 says `LASER_ARMED` needs its own entry | **all of M5** | write the entry first — this is the one open decision that gates a whole milestone, and no measurement is needed to make it |
| D7-open | May the operator jog off a pressed hard limit while `alarm_1 ≠ 0`? The gate refuses all non-ALWAYS writes in that state | M1 step 7 sign-off | 11 §7 step 7: press each limit, read 1006 and 2000+10·slot, then decide |
| D2-follow | Set `position_counts_per_mm` and flip `position_scale_verified` | homed soft limits, absolute moves | measured values from steps 3/4 only |
| M2-gate | The `ManuContour`/`SortType=4` gate has a wrong premise and must be replaced by a vendor-sorted reference | M2 sign-off | Wine session H: Sort, save, commit the order as a golden |
| M2-render | Accept "IoU ≥ 0.9 **and** chamfer = 1.0 against an asymmetric control" as the meaning of "renders identically" | M2 sign-off | amend PORT-PLAN §4 M2 wording, or build a DDA reference rasteriser |
| Daemon language (R8) | Keep the Python `mccd`, or move it to Rust/C++ | R8 retirement | **provisionally answered "keep Python"**: the §8.3 jitter gate passes on the simulator with margin (§0). Re-open only if the real card's tick period turns out much shorter than 250 µs (O1, step 8) |
| Streaming planner | `build_job` materialises every frame; 100 k contours need a generator (X13) | M4 at production sizes | vectorise the tick → item → word → frame path and yield frames into `JobFeeder`, which already takes a generator. The shape is decided; the work is not done |
| `.chf` legacy (X6) | Keep non-empty v2–v4 reserved lines in a model slot, or reject such a file | interchange | a decision; no measurement will make it for us |
| D9 residual | Close the `_release_arming` window (a new client can arm between `_arm_owner = None` and `_disarm_and_stop`), or keep it | nothing — the failure direction is fail-safe | closing it needs card I/O under `MccDaemon._lock`; deliberately not done |

---

## 5. Next 10 tasks, in priority order

### Needs the owner at the machine

1. **Run the M1 confirmation session, native steps 1–7 and 10** (`tools/m1_session.py`,
   `docs/M1-BENCH-SESSION.md`). Laser PSU key off, gantry mid-bed, hand on the E-stop, tcpdump
   running. Settles K, the bus cycle, block 5000, O6's jog half, bit 31, the stop profile, the home
   vector, O8 polarity and exception 2 — the whole of §3.1. The only prerequisite left is a
   simulator rehearsal (`M1-BENCH-SESSION.md` §2); D9/D11/D12 are closed in code and the runbook
   matches the code as of this report.
2. **Steps 8 and 9 with the vendor tool, in the same session.** The dry-run 100 mm X move with reg
   1015/1016 read before and after the first frame gives the tick period (O1) and the reg-1016 units;
   Pause/Continue/Stop gives N2. **Save the vendor frame stream of a drawing that also exists as
   `.chf`** — that single artefact is what turns M4 gate 1 from "geometry matches" into a real item
   diff, and it is the only thing §3.3 cannot get any other way. Optionally run `nexcut-mccd run-job`
   natively too: it closes O6's FIFO half, and it is the first time the port streams onto the card.
3. **Measure the M1 gate itself:** a 100 mm X move against a ruler (0.1 mm), homing repeatability,
   and read-back counts/mm — then set `position_counts_per_mm` and `position_scale_verified` (D2).
   Without this, homed soft limits and absolute moves stay unavailable by design.
4. **Dissect the captures into `docs/analysis/11-capture-findings.md`**, then push the results into
   `mcc/registers.py`, the simulator, `core/config.py` and §3.1–3.3 of this file. The dissecting is
   desk work; only the pcaps need the machine. This also closes the **last open M0 gate item**
   ("the dissector decodes the first tcpdump capture"), which is the cheapest tick on the board.

### Can be done without the machine

5. **Wine session H** — needs the vendor tool under Wine, **not the machine**, and it is the single
   highest-yield desk task: it unblocks three separate gates at once.
   * Sort `autosave.chf` with `SortType=4` and save it → a valid M2 sort reference (the current gate
     is unmeetable as written, §1.3).
   * Load port-written `.chf`, `Bk*.xml` and technology files → the M3 gate. Start with the
     technology XML the CO2 page writes; it differs from a vendor preset only by `CutFreq`.
   * Add an unknown attribute, load, re-save, diff → X7.
   * Save the variants that answer the rest of §3.6 (import, XML, crafts, scan, i18n).
6. **X13 — a streaming, numpy-vectorised planner item path.** Vectorise tick → item → word → frame
   (`plan/items.py` + `mcc/fifo.FramePacker`) so `build_job` *yields* frames instead of
   materialising a list. `mccd/feeder.JobFeeder` already consumes a generator, so this closes the
   PORT-PLAN §8.3 planner gate **and** removes the multi-GB memory ceiling in one change. It is the
   only thing between the port and a production-sized job, and it does not depend on any capture.
7. **X11 — a streaming DXF reader.** 50 k separate `LINE` entities take ≈ 8.2 s against a 3 s
   budget, ≈ 4.5 s of it inside ezdxf. Needs a reader that never builds an ezdxf document.
8. **Write D13 (laser arming) before any M5 code exists.** The IPC shape, the job token it has to
   quote, the operator confirmation, the auto-disarm rule. `mccd/feeder.py` already refuses to load
   a job while `LASER_ARMED`; that refusal is the placeholder D13 replaces. Writing the decision
   before the code is the whole point of the D-series, and M5 is blocked on it either way.
9. **The rest of the M3 property pages**, on top of `ui/property_grid.py`: hardware, machining,
   software and graph-rule pages; the fibre layer page (164 attributes, real pierce-stage editing,
   the `A250607_*` fibre/CO2 selector warning); the power/frequency curve editor
   (`PWMCurveNodes`/`FreqCurveNodes`); the crafts editor for per-contour lead-in/out and cool
   points; and writing `BkLayerPara.xml` back from the dock, not only preset exchange.
10. **The correctness and hygiene backlog** — each item is small, and each is a real gap:
    * **Doc corrections from the A9 re-trace** (§3.7): `docs/analysis/05-motion-pipeline.md` §7.4
      (the look-ahead core address a cut actually runs) and §7.2 (the 0.99 factor is a logging
      filter, not a multiplier).
    * **Decide X6** (`.chf` v2–v4 reserved lines: model slot or strict rejection).
    * **`StatusSnapshot.arm_owner`**, so the TUI can say "ARMED (this session)" versus "ARMED
      (another client)" — with R12 that distinction is now the difference between "you can move"
      and "you cannot", and nothing surfaces it.
    * **Re-arm after a reconnect in `tools/m1_session.py`.** `DaemonLink` reconnects lazily; a
      reconnect silently drops both the arming and the right to move (D9/R12), so a step interrupted
      by a link blip fails with an `arming` refusal the operator has to decode.
    * **Give `nexcut-mccd run-job` a retry for one poll period.** Two runs in quick succession are
      refused with `busy: axis status not refreshed since the last motion command`: `_require_ready`
      wants a 2000/50 poll newer than the last motion write, and a FIFO program start is not a jog.
      Either exempt `start_job` from that particular gate or retry in the CLI, as
      `tests/test_mccd_job.start_job()` already does.
    * **A test that every key named in the TUI's two hand-written footer lines is in
      `KEY_BINDINGS`.** Only the `?` overlay is generated from the table today, so the footer can
      drift away from D11's single source of truth.
    * **Bound the audit log.** `SafeMccClient.write_log` keeps a 298-word tuple plus the 1206 frame
      bytes per FIFO frame (~10 kB each) and is trimmed only opportunistically by the daemon
      watchdog, so a streaming job sits at tens of MB. A compact record for `0x66`, or a cap inside
      `safety.py`, is the fix.
    * **`CardSimulator.requests` grows without bound** (~60 MB over a 10-minute gate run); a ring
      buffer makes long simulator runs cheap.
    * **Expose `LaserOffBeforeDelay` / `LaserOffAfterDelay` on `plan/pwm_schedule.LayerLaser`** —
      both are absent from this machine's CO2 XML, so `build_job` passes 0 today, and the schema
      already carries them (pd137/pd138).
    * **Rename the two tests in `tests/test_plan_fidelity_review.py` that still carry splice wording
      in their names** (`test_prologue_dwell_is_14_stationary_ticks_in_frames_0x39_0x3a`,
      `test_vendor_cut_start_kinematics_frame_0x3a`); their docstrings are already corrected.

---

## 6. CI stability

The suite runs real threads (daemon, IPC server, card simulator, job feeder) and carries wall-clock
budgets, so it can fail for reasons that have nothing to do with the port. GitHub runners are slower
than the development laptop and share their cores. The rule this project holds itself to: **every
timing assertion must hold on a machine 3x slower**, and that is checked by running the suite with
every core busy.

### How it is checked

```sh
# one busy core each; the exact loop is in docs/DEVELOPING.md
for i in $(seq $(nproc)); do .venv/bin/python -c "
import time
end=time.monotonic()+3600
while time.monotonic()<end: pass" & done
NEXCUT_SRC=… QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q -p no:cacheprovider
```

`docs/DEVELOPING.md` ("Writing tests that survive CI") has that loop and the rules a new test has to
follow: wait for a condition instead of sleeping and asserting; never assert that something has
*not* happened yet after a short sleep; never assert a periodic event inside less than a few of its
periods; and keep "the work is finished" apart from "the thread has left" — a job's `DONE` is the
producer's last state, while the closing `0x67` pair, the token hand-back and the card's execution
of what it already holds all come after it.

The last rule keeps earning its place. In this phase a review test
(`test_r20_a_grammar_violation_in_the_middle_of_a_job_never_reaches_the_card`) read the card's FIFO
control register the instant `job_status` said `FAILED` and asserted the last write was `[1]`; it
got `[1, 2]`, deterministically red — the closing `0x67 ← [3]` / `[1]` are sent by the feeder thread
*after* the final state (`[1, 2]` at once, `[1, 2, 3, 1]` one second later). It now waits for
`daemon._job.closed` before reading the card; the assertions are unchanged.

### What the earlier load sweep changed (2026-09-16)

| Finding | Fix |
|---|---|
| Every wall-clock budget in `tests/test_import_ui_fidelity_review.py` (the 3 s open/fit/paint gates) failed with the cores busy: they measure the runner, not the port | `tests/conftest.speed_factor()` times a fixed pure-Python workload once per session against `CALIBRATION_REFERENCE_S` (0.052 s on the review laptop) and multiplies the budgets by it, never below 1.0. Raw seconds and the scaled budget are printed. Under load: budget 25.4 s, `.chf` 16.5 s, DXF lwpolyline 6.0 s — and X11 still failed at 59.0 s, so the strict xfail is preserved |
| `tests/test_perf_streaming.py` failed the p99 / max frame-interval limits under heavy load | The same scaling, applied **only** to the two time limits. The criteria that are facts about the stream — `DONE`, every tick consumed, 0 re-sends, 0 starvation alarms, the queue never below `FifoAlarmNum` = 30 items — stay unscaled |
| `test_streaming_jitter_gate[1000]` was refused with `busy: axis status not read yet` | The test waits for `daemon.axis_ro`, the same condition `tests/test_mccd_job.running()` already used |
| **A real defect, not a test race:** the job token was still set after `job_status` reported `DONE` | `JobFeeder.closed` (the thread has left) next to `finished` (the producer has no more work); `MccDaemon.load_job_frames` joins the previous feeder for up to `CLEAN_STOP_JOIN_S` = 5 s and otherwise refuses with "the previous job is still stopping" (D5, job-streaming amendment) |
| Three post-stop assertions read `MccdGate.leases()` one step too early | They wait for `leases() == {}` with a 2–3 s deadline and print the leases on failure; the assertions themselves are unchanged |
| `test_r1_stop_reaches_card_after_long_comm_loss_despite_refreshing_client` snapshotted the healthy-refresh count *after* observing `LINK_LOST` | The snapshot is taken before the link is cut, which is healthy by construction |
| `test_watchdog_simulator_drop_disarms_and_stops` asserted `DISARMED` the instant it saw `LINK_LOST` | Wait for `DISARMED` with a 3 s deadline |
| The `.chf` open budget still missed by 11 ms under load: the pure-interpreter calibration reported 2.32x while the Qt + file-I/O path had slowed by 3.2x | `LOAD_HEADROOM = 1.5` in `tests/conftest.py`, applied to the *excess* over 1.0, so a machine at the reference speed still gets the exact 3 s gate |
| `start_job` for a second job right after the first is refused until the next 2000/50 poll (~90 ms) lands | `tests/test_mccd_job.start_job()` retries while the daemon answers `busy`, which is what a real client has to do. The CLI does **not** yet — §5 task 10 |

Under that sweep the suite was run three times end to end with every core busy and once under
`pytest-xdist -n 4`, plus five repeats of the sixteen timing-sensitive files. The under-load
slowdown those runs saw was 2.3x on the pure-interpreter calibration and up to 3.2x on the Qt +
file-I/O paths.

### One flake to watch

`tests/test_m1_session.py::test_full_session_all_steps_on_sim` failed once during this phase, in a
full run that shared the laptop with three other live pytest processes, and then passed 15/15 in
isolation and at file level. `tools/m1_session.py` does not touch `JobFeeder`, so it is unrelated to
the feeder changes — but it is the one test in the suite that has failed without a diagnosed cause,
and it should be watched rather than assumed benign.

`.github/workflows/ci.yml` runs `pytest -q -p no:cacheprovider --durations=15`, so a slow runner
names the tests that ate the time instead of just timing out.
