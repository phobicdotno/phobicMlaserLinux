# STATUS: where the port stands

Snapshot date: **2026-09-16**. This file is written from the working tree at that date. Nothing was run on the machine.
It covers what exists, which PORT-PLAN §4 gates pass, and what is still assumed.
Section references follow the analysis docs: `11 §7` means `docs/analysis/11-static-findings.md` §7, and `D9` means `docs/DECISIONS.md` D9.

Rule for this file: a gate counts as "met" only if it was actually run and passed.
A gate that passes on relaxed criteria, or on the simulator only, says so.

---

## 0. Numbers at a glance

| Item | Value |
|---|---|
| Full suite, run with `NEXCUT_SRC=…/Mlaser-v0.0.0.52 QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q` | **1205 passed, 12 xfailed (all strict), 0 failed, 0 skipped** in 155.6 s |
| Tests collected | 1217 in 50 files |
| `ruff check . tools/m1_session.py` | clean |
| CI (`.github/workflows/ci.yml`) | ruff + pytest on Python 3.12/3.13/3.14, offscreen Qt. SRC is absent there, so every vendor-golden test skips in CI |
| `UNVERIFIED` markers in `src/` + `tools/` | 224 lines (grep), grouped in §3 |
| Hardware sessions run | **0**. No pcap capture exists yet |
| Console scripts | `nexcut` (viewer), `nexcut-mccd` (daemon/CLI/TUI), `nexcut-plan` (offline planner → frame file) |

Tests per area (collected):
* `mcc`: 297 in 12 files
* `mccd` + config + `m1_session`: 128
* `io` + `chf`: 223
* `core/schema`: 18
* `plan`: 388
* `ops`: 28
* `ui`: 113, including the import/UI review
* integration + smoke: 22
* review suites: 210 in total, counted inside the areas above (io 24, import/UI 53, plan 22, safety 37 + 30, protocol 44)

---

## 1. Milestones

| M | Title | State | Gate |
|---|---|---|---|
| M0 | Foundations, passive instrumentation | **done** (one gate item waits for the first capture) | golden tests green ✔; dissector on package logs ✔; dissector on first tcpdump capture ✘ (no capture exists) |
| M1 | Hello machine: jog/home on the real card | **partially done**: software and bench procedure ready, no hardware run | not run: needs the owner at the machine (11 §7 steps 1–10) |
| M2 | File load and render | **partially done** | renders ✔ on a relaxed metric (not pixel identity); ManuContour order via `SortType=4` ✘ (the gate's premise is wrong, see §1.3) |
| M3 | Layers, parameters, hardware config | **partially done**: file layer only, no editors | XML round-trip ✔; Wine load of port-written files ✘ (not run) |
| M4 | Job streaming, dry run | **partially done**: offline planner + packer + simulator feed | gate 1 partial (raster geometry matches, no vendor item stream to diff, no jitter test); gate 2 not run |
| M5 | Live CO2 cutting | **not started**: laser arming deliberately absent (D5) | — |
| M6 | Fibre path | **not started**: fibre cutting refused until capture G | — |
| M7 | Nesting, scan, pendant, packaging | **not started**, with two pieces present: scan-fill geometry (`ops/scan.py`) and the i18n loader for 11 languages | — |

### 1.1 M0: done (gate item 2b pending a capture)

Evidence:
* **Project.** `pyproject.toml` with setuptools and three console scripts; CI workflow; `docs/DEVELOPING.md`.
* **`mcc/framing.py`, `crc.py`, `dissector.py`.**
  * Log and pcap/pcapng parsing, IP reassembly, TCP/SLL, CLI.
  * Tests: `test_mcc_framing` 15, `test_mcc_crc` 8, `test_mcc_dissector` 20.
* **`mcc/simulator.py`.**
  * Answers 1000/2, 1000/36, 50000, 60001; accepts 0x65/0x66/0x67; raises exceptions 2/3; consumes the FIFO.
  * Tests: `test_mcc_simulator` 8, `test_mcc_sim_commands` 6. It is also used by the mccd tests.
* **`core/schema.json` + `schema.py`, `io/params.py`.**
  * `Bk*.xml` and technology presets round-trip byte for byte.
  * Tests: `test_core_schema` 18, `test_io_params` 22, `test_io_fidelity_review` 24 (4 of them strict xfail).
* **`io/chf.py`, `io/cp936.py`, `model/*`.**
  * Tests: `test_chf_samples` 30, `test_chf_writer` 37.

Gate run for this report (2026-09-16):
* **Golden tests: pass.** All pass inside the full suite.
* **Dissector on the package logs: pass.** Command: `python -m nexcut.mcc.dissector --summary-only Log/*.log`.
  * 805 transactions: 603 exception, 195 timeout, 5 send-error, 2 socket-error.
  * 22 distinct FIFO frames, 0 with remainder.
  * Opcode census 3000:2160, 3001:6, 9999:5, 3002:3, 2001:3, 103:2, 109:1, 118:1.
  * 113 NC-start events.
  * These match 08 §2–4 and A3, and `test_package_logs` pins them.
* **Dissector on the first tcpdump capture: not met.** No capture exists. The pcap/pcapng paths are tested on synthetic captures only.
* **Known defect, cosmetic.** The dissector still labels `0x65` sub-command 1 "home" and 3 "move-axis" (04 naming). Per 11 §8, 1 is STOP and 2 is HOME. In the summary above, "CMD home 60" are stops. `tools/m1_session.py` uses the corrected names.

### 1.2 M1: partially done (all software, no hardware)

Evidence:
* **`mcc/commands.py`.** Vectors V0–V16 of 11 §2, with `unverified` notes on each vector. Tests: 32, plus `test_mcc_protocol_fidelity` 44.
* **`mcc/registers.py`.** Block 1000, axis and alarm bit maps of 11 §4. 8 tests.
* **`mcc/transaction.py`.** Vendor retry ladder. 27 tests.
* **`mcc/safety.py`.** Allow/deny lists of 11 §3, arming states, deadman, soft limits, laser-record strip. Tests: 79, `test_mcc_safety_adversarial` 37.
* **`mccd/`** (daemon, gate, IPC, status, CLI, TUI) and `core/config.py`.
  * Tests: `test_mccd_*` 117 in total, including `test_mccd_safety_review` 30 (3 strict xfail).
  * Enforced decisions: D1–D8 and D10.
  * Open decisions: D9, D11, D12.
* **`tools/m1_session.py`.** Guided 10-step session with a capture wrapper, a per-step census and a safety checklist. 11 tests.
* **`docs/M1-BENCH-SESSION.md`.** The operator procedure.

Gate: **not run.** It needs the machine:
* a 100 mm ruler move within 0.1 mm;
* repeatable homing;
* no exception other than 3-while-moving;
* position register semantics.

Known limits of the native path (`docs/M1-BENCH-SESSION.md` §11):
* Step 4 (absolute move) is deferred, because IPC has no absolute move.
* Step 8 (FIFO stream) is deferred, because IPC has no FIFO commands.
* For both, only the vendor/Wine variant exists.
* The native continuous jog is capped at 20 mm/s by D1.

PORT-PLAN §9 wording "capture sessions A–D dissected; `11-capture-findings.md`": not started. That file does not exist.

### 1.3 M2: partially done

Evidence:
* **Importers.**
  * `io/dxf.py` (ezdxf): 41 tests.
  * `io/gcode.py`: 53 tests.
  * `io/plt.py`: 16 tests.
  * `.chf` v1–v5 read and v5 write.
* **`ops/import_gates.py`** (overlap/connect/minimal/sort gates) and **`ops/sort.py`** (SortType 0–7). 17 tests.
* **UI.** `ui/{app,main_window,canvas,scene,render,layers,loader,i18n}.py`.
  * Features: open DXF/.chf/PLT/G-code, save/save-as `.chf`, recent files, layer dock, selection info, start points, direction arrows, index numbers, measurement tool, offscreen `nexcut render`.
  * Tests: `test_ui_*` 60, `test_import_ui_fidelity_review` 53 (3 strict xfail).
* **Not present:** lead-in defaults, auto micro-joint, and any editing operation. The ops package docstring lists them, but no module exists. `.chf` re-save is identity only; there is no edit-then-save path.

Gate run for this report (script: render each sample, compare ink masks to `tools/out/*.png`, re-save each `.chf`):

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

* **"Renders identically": met on the relaxed metric, not literally.**
  * The relaxed metric is IoU ≥ 0.9, and the chamfer score is 1.000 on all 8 samples.
  * Pixel identity holds for 2 of 8. The line rasteriser differs: Qt versus the reference tool's integer DDA.
  * The review showed the IoU gate is blind to mirroring on these symmetric samples. `chamfer_score` plus an asymmetric reference now catches mirror, rotation and bulge errors.
* **"24-contour job sorts to the same order as `ManuContour.dat` with `GRP.SortType=4`": not met, and not meetable as written.**
  * `ManuContour.dat` is `24, 0..23`, which is the array-copy order of autosave.chf (row serpentine, `GRP.Array*`).
  * No sort type reproduces it. Checked on 2026-09-16:

    | Sort type | First contours |
    |---|---|
    | NEAREST | 0,4,8,9,7,5,… |
    | BOTTOM_TO_TOP | 0,1,2,5,4,3,… |

    All other types differ as well.
  * The sample contains no sorted job, so the vendor sort algorithms cannot be checked (`ops/sort.py` GAPS).
  * The gate needs a new reference: a vendor-sorted job (Sort, then save `autosave.chf` + `ManuContour.dat`), producible under Wine.
* **Performance.**
  * 50 k one-segment contours open in < 3 s for DXF lwpolyline, G-code, PLT and CHF.
  * 50 k separate DXF LINEs take 8.2 s, and a 50 k-contour `.chf` takes 8.4 s. Both are strict xfails.

### 1.4 M3: partially done (files only)

* **Present in `io/params.py` and `core/schema.py`.**
  * Typed parameter documents, missing-attribute defaults, atomic write with verify.
  * Technology preset parse/serialise (`P…Param11` wrapper), preset ↔ layer transfer, `ParamStore` fallback chain (`Bk*` → `SecondBk*`).
  * Round-trip tests on every vendor XML and technology file in SRC.
* **Absent.**
  * Property grids of any kind (hardware, machining, software, layer, graph rules).
  * `UN.*` unit display, `lp19` validation, the fibre/CO2 selector with the `A250607_*` warning, the crafts editor.
  * Hardware-page read-back of 50000/50200/59600+. The daemon reads 50000/26 only for K, bus cycle and ZFType.
* **Gate.** XML round-trip ✔. "The Windows tool (under Wine) loads files saved by the port" was not run ✘.
* **Known fidelity gaps** (strict xfails): unknown attributes are dropped on rewrite; non-finite doubles are spelled `1.#QNAN` instead of boost `nan`; `1_0` is parsed as exact.

### 1.5 M4: partially done (offline)

* **Present.**
  * **`plan/`** (contour_fit, junction, lookahead, scurve, sampler, pwm_schedule, items, params, `__main__`).
    * 388 tests.
    * Goldens: `segInterp`, `JumpAddTime`, `VelDecc`, junction, S-curve.
    * The 1506.62 mm raster reproduces as geometry (`ops/scan.py`, `test_plan_contour_fit`).
    * All 22 leaked frames re-encode word for word.
    * Prologue frames 504/57 and epilogue frame 633 match exactly.
    * The constant-velocity run of frame 1931 matches tick for tick (speed fitted).
    * The rapid start of frame 0x279 matches tick for tick.
  * **`mcc/fifo.py`** (packer, `FifoFeeder`, reg-1016 byte accounting): 13 tests.
  * **`nexcut-plan`** writes a dry-run frame file and never opens a socket.
  * `test_planned_job_streams_through_feeder_into_simulator`: a planned job is consumed by the simulator.
* **Absent.**
  * Streaming inside `nexcut-mccd`: no FIFO IPC commands, no job token, no flow-control loop against the card.
  * Break-point files, report rows, estimate dialog, UI job controls.
  * The PORT-PLAN §8.3 jitter test (10-minute job, queue ≥ 30 items, p99 < 100 ms).
  * The planner throughput test (100 k contours in < 30 s).
* **Gate 1 (simulator): partial.**
  * The raster total and toggle geometry match, but no vendor *item stream* of the raster exists to diff against.
  * The jitter test does not exist.
* **Gate 2 (machine): not run.**
* **Open fidelity gaps** (strict xfails, from frames 0x39/0x3a):
  * pierce dwell: 400 ticks in the port versus 14 in the vendor stream;
  * cut start: the port starts from rest, the vendor leaves at v0 ≈ 6.9 mm/s.
* **Port-only safety rules added by the plan review:**
  * glyph gaps > 0.01 mm raise an error;
  * runs of dropped segments are filled from the global clock.

### 1.6 M5–M7

* **M5: not started.**
  * There is no `arm_laser` over IPC (D5).
  * `strip_laser_records` removes DO9/PWM/DA records unless LASER_ARMED, and LASER_ARMED cannot be reached.
  * Gas DA2 non-zero is denied (`allow_nonzero_da=False`).
* **M6: not started.** Fibre cutting is refused in `mcc/safety.py` until capture G.
* **M7: not started.**
  * Present pieces: `ops/scan.py` (hatch, serpentine, fly-line link builder from `0x10096fc0`); the i18n catalog for the 11 vendor language files (`nexcut --lang`).
  * Absent: nesting, the PWM position-compensation run-time, the PHB02 pendant (hidraw), packaging (`.deb`/AppImage).

---

## 2. Strict xfails (12)

Each one documents a known divergence. Fixing it turns the test into an XPASS failure, which forces the marker to be removed.

| # | Test | Reason | Resolves with |
|---|---|---|---|
| X1 | `test_plan_fidelity_review::test_port_pierce_dwell_matches_leaked_frames` | Port dwells LaserOnDelay + GC.GasDelay = 0 + 100 ms = 400 ticks after DO9 on; vendor frames 0x39/0x3a show 14 stationary ticks (3.5 ms, not an integer-ms dwell). Dwell builder `0x4427e0` not traced | static re-trace; then a vendor laser-on frame capture (session E) |
| X2 | `test_plan_fidelity_review::test_port_cut_start_matches_leaked_frames` | Port starts each contour at rest with zero acceleration (J = 5000); vendor covers 90 pulses in the first 113 ticks (v0 ≈ 6.9 mm/s, a ≈ 570 mm/s²) versus < 6 for the port. Look-ahead core `0x100114d0` not re-traced (05 §7.4) | static re-trace; A/B frames of a vendor dry run (11 §7 step 9 / session E) |
| X3 | `test_mccd_safety_review::test_r9_arming_does_not_outlive_the_arming_client` | Open decision **D9**: MOTION_ARMED persists after the arming connection closes | design decision (before M1 on the machine) |
| X4 | `test_mccd_safety_review::test_r7_caps_lock_home_key_does_not_start_motion` | Open decision **D11**: with Caps Lock on, `h` arrives as `H`, which starts a continuous jog X− | design decision (before M1) |
| X5 | `test_mccd_safety_review::test_r11_second_daemon_on_same_card_is_refused` | Open decision **D12**: a second `nexcut-mccd` can drive the same card | design decision (flock, before M1) |
| X6 | `test_io_fidelity_review::test_legacy_reserved_lines_with_content_are_not_lost` | `.chf` v2–v4 reserved lines are skipped unread (03 §9); non-empty content is lost | design (model slot or strict rejection) |
| X7 | `test_io_fidelity_review::test_unknown_attribute_survives_rewrite` | Unknown XML attributes are dropped on rewrite; ParaModule's CMarkup DOM may keep them (UNVERIFIED) | Wine diff |
| X8 | `test_io_fidelity_review::test_non_finite_doubles_use_boost_spelling` | `core/schema.py` writes `1.#QNAN`/`1.#INF`; ParaModule uses boost::lexical_cast (`nan`/`inf`, `0x1000ade0`) | code fix (evidence is static); confirm by Wine diff |
| X9 | `test_io_fidelity_review::test_underscore_number_is_not_exact` | Python `float()` accepts `1_0`, ` 5` and `infinity`; boost::lexical_cast rejects them | code fix; bad_lexical_cast handling via Wine diff |
| X10 | `test_import_ui_fidelity_review::test_cached_spline_length_is_accurate` | `model/flatten._bspline_pts` ignores the step, so the cached 1 m spline length is 0.36 mm short | code fix (no machine) |
| X11 | `test_import_ui_fidelity_review::test_open_50k_separate_dxf_lines` | ezdxf parse of 50 k LINEs ≈ 4.5 s plus Python gates = 8.2 s (budget 3 s) | performance work (streaming DXF reader) |
| X12 | `test_import_ui_fidelity_review::test_open_50k_contour_chf` | `io/chf.py` token reader ≈ 7 s for 50 k contours | performance work (vectorised tokenizer) |

---

## 3. UNVERIFIED assumptions, grouped by what resolves them

Each group lists the concrete assumptions and where they live in the code.
"Step n" means 11 §7 step n, run with `tools/m1_session.py` and `docs/M1-BENCH-SESSION.md`.
Items marked *(port choice)* are not vendor facts at all. No capture settles them; they are design parameters to revisit, collected in §3.14.

### 3.1 Step 1: connect and start-up reads (`READ 1000/2, 1000/36, 50000/26, 50200/100`, `[9999,5,0,0]`)

K and the bus cycle:
* K = 1000. Used in `mcc/safety.py` soft-limit words, `commands.MachineParams.k`, `m1_session.EXPECT_K`, and as the D1/D2 unit base.
* Bus cycle reg 50005 = 250 µs (`m1_session.EXPECT_BUS_CYCLE_US`, simulator initial SystemRW).

Axis parameter words:
* ZFType word 6 = 1.
* Lead unit in AxisRW word 9: µm or mm.
* AxisRW word names other than 9/10 (`registers.UNVERIFIED_NOTES`).
* Axis-0/1 pulses and lead versus the XML (C23).

Reply layouts and read cadence:
* Success reply layouts derived from the PC decoder (`framing.encode_reply_vector`, simulator replies).
* The card answers the 1-word marker read `READ 1000/1` (m1 tool).
* Upper bound of the combined 60001 read = 144 words (`safety.py`).
* Meaning of `[9999,5,0,0]`; only its necessity is known (`commands.connect_prologue`).
* Simulator: a request with a non hi-first CRC is ignored.

### 3.2 Step 2: block 5000 and the E-stop

* Whether reg 1004 is the raw DI level or already inverted by the card via block 5000:
  * `registers.di_active`;
  * `config.watchdog_di_alarms`, off by default;
  * `mccd/status.py` DI mapping.
* Alarm word offsets 1000+6/+7 and the bit meanings as the simulator emulates them.
* alarm_1 bit 24 = on-board follower axis (INFERENCE, low-medium).
* The E-stop disables the servo drives. Step 7 relies on this.

### 3.3 Step 3: step jog X +5 mm at 20 mm/s

* **O6 firmware gating:** a jog is accepted without the licence exchange.
* Exception 3 on jog-while-moving, and the simulator's busy code.
* How AxisCommandType 3/4/5 split between jog and move (`registers.AxisCommandType`).
* Axis RO word names 3–9 (encoder position, stop pulse, mileage and so on; table order only).
* Axis RO word 2 is in motor pulses (≈ 258.04 / 257.99 p/mm), not µm:
  * `config.position_counts_per_mm`, `position_scale_verified = false` (D2);
  * the TUI position display;
  * the simulator uses µm.
* Simulator conventions: reg 1008 run values 0/1/2; how long a jog keeps the card busy.
* Physical direction of the TUI keys (Up = Y+, PgUp = W+).

### 3.4 Step 4: bit 31 = absolute

* `commands.move_axis_absolute` sets bit 31 for an absolute target (INFERENCE medium-high).
* The card origin is the home corner, so soft limits are `[0, SoftLimitMaxLen]` (`config.soft_limit_x_mm/_y_mm`, `safety.soft_limits_word`).

### 3.5 Step 5: continuous jog and key-release stop

* Physical meaning and unit of `vd` in `[1, mask, 2, vd, 10·vd]` (`commands.stop_all`, `jog_release_stop`).
* The card's default deceleration for `stop_all_no_decel`.
* `max_jog_distance_word = 4 000 000` as a physical bound.
* Simulator: a stop halts dead at the current position.

### 3.6 Step 6: home X, then Y

* Homing speeds and back-off are held on the card (AxisRW +5/+6, INFERENCE high).
* Axis-status bits 0–5 during homing: the gate trips only while jogging (D7).
* `home_system` mask bit 0x10000 (INFERENCE low). The port does not use it.

### 3.7 Step 7: limit switches by hand; Y2 tracking

* **O8** limit polarity per DI pair (`mccd/status.py` mapping to DI pairs).
* D7 open issue: can the operator jog off a pressed hard limit while `alarm_1 ≠ 0`?
* Y2 dual-drive tracking on slot 2 (reg 2022).

### 3.8 Step 8: FIFO tick period (dry frame stream)

* **O1:** the card-side tick period, 250 µs assumed.
  * Planner side: `plan/sampler.CYCLE_US`, `plan/items`, `plan/__main__` durations.
  * Simulator side: 1 ms tick estimate.
* Reg-1016 units: bytes, whether frame prefixes count, how an item is charged (`mcc/fifo.py`, simulator `space_margin`).
* Simulator FIFO depth.
* A re-sent frame id is acknowledged, not re-queued.
* The exception for an over-long frame.
* Motion commands are refused with exception 3 while the FIFO runs.
* `0x67 ← [1]` discards queued frames (`safety.py`).
* The FIFO starts without a licence exchange (O6, FIFO half).
* Vendor dry-run speed source.
* The m1 tool's tick estimate method (30 ms poll resolution divided by the tick count).

### 3.9 Step 9: vendor Pause / Continue / Stop (also yields vendor dry-run frames for A/B)

* **N2:** how Continue resumes a paused job.
* The stop-manu DO bulk list: the vendor uses 27 fixed offsets plus a vector; the port uses this machine's DO list (`commands.do_ports_off_on_stop`).
* `[101]` means "ZF stop" (INFERENCE medium-high).
* Planner motion fidelity, diffed against the vendor frames of the same drawing:
  * **X2** cut start;
  * rapid-end tails (0x1f8/0x39 unexplained);
  * how segment vectors are stitched, including the duplicated boundary sample and the dropped-run fill (`plan/sampler.py`);
  * whether the node list carries a leading `s = 0` (`lookahead.py`);
  * the `P8/P9` slow-end mirror (`apply_slow_end`);
  * which speed the vendor logs;
  * the rapid planner call (A6 §1.4).
* Job start position: the port adds no initial rapid (`plan/__main__`).

### 3.10 Step 10: power-cycle, `READ 1000/2` at once

* Exception 2 semantics and duration (simulator `not_ready_s`, `EXC_NOT_READY`).
* The simulator's reply codes for an unknown vector, a malformed stop or motion vector, and an unsupported function (code 1).

### 3.11 Session E (real cut, laser on; M5, after the steps above)

These are laser records. A dry run never emits them, so steps 8/9 cannot settle them.
* **X1** pierce dwell: the 14 stationary ticks; `StreamConfig.pierce_dwell_ms = GasDelay` is contradicted.
* Gas switches off once at job end, not in each contour epilogue (`items.JobStreamBuilder`).
* The role of the one-tick `(0,0)` record between 3002 and the gas DO.
* PWM lead compensation: sign, interpolation, the −3 forward-cycle sign convention (`plan/pwm_schedule.py`).
* Power/frequency-vs-speed table construction and smoothing types 1/2.
* The in-stream DA channel base (`safety.fifo_laser_da_channel_words`, which strips both words).
* ZF record descriptors in CO2 frames. To settle them, change one parameter between two captures:
  * `zf_move_speed`: ZFFollowSpeed or ZFUpSpeed;
  * `zf_book_value` 35 (`118[4,0,35]`);
  * `sub_device_type` 2 or 3.
* CO2 in-memory UD_* planner values at `layer+0x380..0x3b8` (`plan/params.py`). A static re-trace also works.

### 3.12 Capture G (fibre only)

* ZF status units, 10000+2i single-register spacing, the 10 s ZF status cadence (`registers`, `config`).
* Fibre pierce and follow stream; DO5 gate timing; laser nameplate.
* DO6 red pointer treated as a laser hazard class (`safety.laser_do_ports`).
* Fibre cutting is refused outright until then (`safety.py`).
* Dissector: the EC3710 frame heuristic, and whether the TCP path (`AccessType=0`) uses the same framing.

### 3.13 Wine diff (vendor tool under Wine, no machine; session H)

**`.chf` and model:**
* SPLINE flags int1/int2;
* text ctor defaults `d130/d138/d140`;
* `CGlyScan`/`CGlyContourEx` precision pass;
* the v1–v4 writer (none shipped);
* CRT `_fltout2` formatting;
* the reserved-line content (X6);
* the default contour precision 0.01 (`model/graph.py`, `io/dxf.py`);
* ellipse flattening (no sample holds an ellipse).

**XML parameters** (`io/params.py`, `core/schema.py`):
* ANSI = cp936;
* the first duplicate element wins;
* `MNF_WITHREFS`;
* other slot numbers accepted;
* the "re-created" write and the meaning of "default";
* `SecondBkManuPara` fallback order;
* unknown attributes kept (X7);
* boost spelling (X8/X9);
* MSVCR100 3-digit exponent;
* `_wtoi`/`_wtoi("")` prefix rules.

**DXF import:**
* SOLID/TRACE/3DFACE as outlines;
* XLINE/RAY skipped;
* rational and non-cubic splines refit;
* `read_color` layer mapping;
* `$INSUNITS` ignored;
* entities with non-finite coordinates skipped;
* text through ezdxf fonts instead of GDI;
* INSERT becomes a Group;
* export: R2000/ANSI_936, layer name = index, ACI = layer+1.

**G-code import:**
* LP/M17/L subprogram model;
* I/J tolerance 0.01 mm and the R-arc chord threshold;
* initial modal state;
* G0 contour breaks;
* cp936 comments;
* G90.1/G91.1 handling.

**PLT import:**
* 40 plu/mm;
* IP default;
* LB not rendered;
* fills as outlines;
* pen-to-layer mapping;
* reflecting SC sweeps;
* ESC skip rule.

**Import gates and sorting** (`ops/import_gates.py`, `ops/sort.py`):
* gate order;
* MicoGraphGate length criterion;
* OverlapGate whole-glyph Hausdorff distance;
* connect chaining rules;
* group cached geometry;
* all sort strategies, the nearest-sort start at (0,0), `SortIsSmallFirst`.

A vendor-sorted job (see §1.3) also fixes the M2 gate reference.

**Scan:**
* row placement, `scanDirection` ≠ 0, connector sampling length;
* 60 mm side lines;
* link-builder variant 4 (`ops/scan.py`).

**UI** (screenshots): layer colours, marker look and sizes, canvas bed 1300×900 at the origin, background colour (`ui/layers.py`, `render.py`, `canvas.py`, `scene.py`).

**i18n:** first duplicate id wins; English as the fallback column; a merge-recovered record loses (`ui/i18n.py`).

**Planner parameter defaults:**
* AddTime/Is4Freq when absent;
* the `p9` ctor default;
* the node-string `"0,48,…"` parsing and the empty-string rule (`plan/params.py`, `pwm_schedule.py`).

### 3.14 Static re-trace (disassembly, no machine)

* Look-ahead core `0x100114d0` (X2).
* Dwell builder `0x4427e0` (X1).
* Rapid planner call (A6 §1.4).
* What `CADMODULE_NODE_FACTOR` 0.99 multiplies.
* S-curve `0x1000eeb0` with no speed change; last-phase evaluation past T; sample order (`plan/scurve.py`).
* x87 precision control of `round_half_away` (53-bit assumed).
* HAL address-filter interval ends (`registers.vendor_would_send`).
* Dissector ladder grouping heuristics (`LADDER_GAP_S`, `max_rung`).
* Gating of `[9999,16]` (N6). DENY until then.

### 3.15 Port choices (no capture resolves them; revisit as design)

* **D1:** un-homed limits 10 mm and 20 mm/s; token bucket.
* **D4:** timeouts of 150 ms; poll periods 90 ms / 1 s / 1 s / 10 s.
* **D6:** deadman 200 ms; pendant silence 1040 ms.
* Timers: supervisor/service period 20 ms; `home_timeout_ms` 120 s; reconnect period.
* TUI key-repeat windows.
* `m1_session` bounds: step ≤ 10 mm, speed ≤ 50 mm/s, hold ≤ 3 s.
* Planner and geometry:
  * `plan/sampler.MAX_JOINT_GAP` 0.01 mm as a planner refusal;
  * the dropped-run fill;
  * `contour_fit` polyline-plus-blend instead of the openNURBS refit, including the 1e-5 and 0.005 rad constants and the corner blend 0.1 mm / R 2 mm;
  * `junction` physical model option.

---

## 4. Open design decisions

| Id | Question | Blocks | Options (from DECISIONS.md) |
|---|---|---|---|
| D9 | Lifetime of MOTION_ARMED after the arming client disconnects (X3) | M1 on the machine | (a) disarm on close, with `jog --arm` in one process; (b) idle timeout; (c) keep, and have TUI/m1 disarm on exit |
| D11 | Caps Lock turns the `h` menu key into `H` = continuous jog (X4) | M1 on the machine | drop letter jog bindings; move the home key; confirmation for letter jogs |
| D12 | Single master per card (X5) | M1 on the machine | `flock` on `$XDG_RUNTIME_DIR/nexcut/card-<ip>-<port>.lock` (same user and host only) |
| D7-open | Jogging off a pressed hard limit while `alarm_1 ≠ 0` is refused | M1 step 7 | decide after step 7 shows which bits the card raises |
| D2-follow | Set `position_counts_per_mm` from steps 3/4 and flip `position_scale_verified` | homed soft limits on the card | measured values only |
| M2-gate | Replace the ManuContour gate with a vendor-sorted reference (Wine) | M2 sign-off | Wine: Sort, save; commit the numbers as goldens |
| M2-render | Accept "IoU ≥ 0.9 and chamfer = 1.0 with an asymmetric control" as the meaning of "renders identically" | M2 sign-off | amend PORT-PLAN §4 M2 wording, or build a DDA reference rasteriser |
| Laser arming | UX and IPC for LASER_ARMED (job token, confirmation, auto-disarm) — D5 says it needs its own entry | M5 | write D13 before M5 |
| Streaming home | Job streaming inside `nexcut-mccd` (FIFO IPC, job token, dry-run enforcement point) | M4 gate 2, and native step 8 | extend `IPC_COMMANDS` with a job API that goes through the gate |
| Daemon language | Keep the Python `mccd` or move it to Rust/C++ | R8 | decide from the §8.3 jitter test, which does not exist yet |
| `.chf` legacy | Keep or reject non-empty v2–v4 reserved lines (X6) | interchange | model slot or strict rejection |

---

## 5. Next 10 tasks, in priority order

### Needs the owner at the machine

1. **Run the M1 confirmation session (11 §7 steps 1–7 and 10) with `tools/m1_session.py`.**
   * Setup: laser PSU key off, gantry mid-bed, hand on the E-stop, tcpdump running.
   * Settles K, bus cycle, block 5000, O6 (jog half), bit 31, stop profile, home vector, O8 polarity and exception 2.
   * First do tasks 6–7 below (D9/D11/D12) and a simulator rehearsal (`docs/M1-BENCH-SESSION.md` §2).
2. **Steps 8 and 9 with the vendor tool:**
   * the dry-run 100 mm X move with 1015/1016 read before and after the first frame, to get the tick period (O1) and reg-1016 units;
   * Pause/Continue/Stop, to get N2 and the vendor dry-run frames.
   * Save the vendor frame stream of a drawing that also exists as `.chf`, to feed the X2 A/B diff.
3. **Measure the M1 gate:**
   * 100 mm X move against a ruler (0.1 mm);
   * homing repeatability;
   * read-back counts/mm, then set `position_counts_per_mm` and `position_scale_verified` (D2).
4. **Dissect the captures into `docs/analysis/11-capture-findings.md`.** Then update `registers.py`, the simulator and the UNVERIFIED lists in §3.1–3.10. This also closes the M0 gate item "dissector decodes the first tcpdump capture". The dissecting itself is desk work; the pcaps come from tasks 1–2.
5. **Wine session H.** Needs the owner's Wine prefix with the vendor tool; the machine is not needed.
   * Sort `autosave.chf` with `SortType=4` and save it, to give a new M2 sort reference.
   * Load port-written `.chf`, `Bk*.xml` and technology files (M3 gate).
   * Save variants for the §3.13 import, XML and craft questions.

### Can be done without the machine

6. **Close D9, D11 and D12 in code before any hardware session**, and turn X3/X4/X5 into passing tests:
   * disarm when the arming connection closes, with `jog --arm`;
   * no letter keys that start motion;
   * a card flock.
7. **Fix the dissector's 0x65 labels** (1 = STOP, 2 = HOME, 3 = jog) per 11 §8. Then re-pin `test_package_logs`, so the pcaps from task 1 are not misread.
8. **Job streaming in `nexcut-mccd`:**
   * FIFO IPC with a job token, dry-run strip enforced in the gate, `FifoFeeder` flow control on reg 1015/1016;
   * the PORT-PLAN §8.3 jitter test against the simulator (10 min, queue ≥ 30 items, p99 < 100 ms);
   * the 100 k-contour planner throughput test.

   This is the prerequisite for native step 8 and for the M4 gates.
9. **Static re-trace of the look-ahead core `0x100114d0` and the dwell builder `0x4427e0`** to explain X2 (the vendor cut leaves at v0 ≈ 6.9 mm/s) and X1 (14-tick dwell). It lowers the cost of the A/B diff after task 2.
10. **Quick code fixes, then M3 editors:**
    * X10: spline length honours the step;
    * X8/X9: boost-style double spelling and parsing in `core/schema.py`;
    * X12: vectorised `.chf` tokenizer;
    * then start the schema-driven property grids (layer CO2 page first) needed for M3/M5.
