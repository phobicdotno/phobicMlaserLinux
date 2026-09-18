# STATUS: where the port stands

Snapshot date: **2026-09-16** (third adversarial-review pass: the vectorised streaming planner, the
streaming DXF reader, and the M3 UI / safety / UX surfaces). Written from the working tree at that
date, and **every number in §0 was re-measured for this report** on the owner's idle laptop.
**Nothing has been run on the machine.** This file covers what exists, which PORT-PLAN §4/§8.3
gates pass, and what is still assumed.

Section references follow the analysis docs: `11 §7` means `docs/analysis/11-static-findings.md` §7,
`A3 §8` means `docs/analysis/11-static/A3.md` §8, and `D9` means `docs/DECISIONS.md` D9.

Rule for this file: a gate counts as **met** only if it was actually run and passed. A gate that
passes on relaxed criteria, or on the simulator only, says so in the same sentence. A number that
was **not** re-measured for this report says whose run it comes from.

---

## How to pick this up (a fresh session, ten minutes)

**Read these three, in this order.**

1. **This file** — §0 (the measured numbers), §1 (what each milestone actually is), §5 (the next
   ten tasks, in priority order and split by whether they need the owner). Everything else here is
   the evidence behind those three sections.
2. **`docs/analysis/11-static-findings.md`** — the protocol truth, and it **supersedes `04` and
   `00`** wherever they disagree (0x65 sub-command 1 is STOP, 2 is HOME, 3 is jog — the older
   naming is wrong). §2 is the command vectors, §3 the allow/deny lists, §4 the registers, §5 the
   FIFO frame grammar, §7 the bench-session script the port is built around.
3. **`docs/DECISIONS.md`** — D1…D15. Fourteen are decided and enforced in code; **D13 (laser
   arming) is `proposed` and unsigned**, and no M5 code may exist until the owner signs it.
   `LASER_ARMED` is unreachable from any client today and three tests fail the moment that stops
   being true (§1.6).

Then, according to what you are about to do: `docs/PORT-PLAN.md` §4 (milestone gates), §8
(safety and the performance gates) and §9 (the deliverable checklist); `docs/M1-BENCH-SESSION.md`
if the owner is at the machine; `docs/WINE-SESSION-H.md` if the owner is at a Wine prefix;
`docs/DEVELOPING.md` §"Writing tests that survive CI" before adding any timing-sensitive test.

**Run this before changing anything** (~8 minutes wall clock on the owner's laptop):

```sh
cd ~/phobicMlaserLinux
NEXCUT_SRC=~/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52 QT_QPA_PLATFORM=offscreen \
    .venv/bin/pytest -q -p no:cacheprovider     # 1761 passed, 14 skipped, 2 xfailed, ~5 min
NEXCUT_SRC=/nonexistent QT_QPA_PLATFORM=offscreen \
    .venv/bin/pytest -q -p no:cacheprovider     # what CI runs: the vendor-golden tests skip
.venv/bin/ruff check . tools/m1_session.py      # All checks passed!
```

The three PORT-PLAN §8.3 performance gates are **not** part of those runs except as a pass/fail —
their numbers only appear with `-s`:

```sh
.venv/bin/pytest -q -s tests/test_perf_planner.py                       # throughput + memory, ~5 s
NEXCUT_JITTER_SECONDS=120 .venv/bin/pytest -q -s tests/test_perf_streaming.py    # jitter, 4 min
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q -s \
    tests/test_import_ui_fidelity_review.py -k 50k                      # the X11 import budget
```

**Two xfails are expected, and both are strict** (X7, X13 — §2; X11 was closed on 2026-09-18). A strict xfail that
starts passing *fails* the suite; that is deliberate, so a divergence cannot quietly rot. Never
weaken one to make a run green.

**The single most valuable next action is §5 task 1 — the M1 bench session at the machine.**
It is the only activity that produces the first packet capture of *this* card, and that one
artefact closes M0's last open gate item, the whole of §3.1–§3.3, D2 and the M1 hardware sign-off
together. No desk work substitutes for it; tasks 2 and 3 are the same sitting, and M4's gate 1(a),
M5 and M6 all wait behind the artefacts it produces.
*If the owner cannot be at the machine:* task 4 (Wine session H) needs them at a keyboard only and
unblocks three gates at once. *If nobody but you is available:* start at task 7 — tasks 7–10 need
neither the owner nor the machine.

---

## 0. Numbers at a glance

Every row was measured on the owner's laptop on **2026-09-16**, idle unless it says otherwise,
except the two rows that name an earlier run and the rows marked **2026-09-18** (§5 task 7), which
were measured while another agent's test run shared the laptop — their `speed_factor` is given.

| Item | Value |
|---|---|
| Full suite, `NEXCUT_SRC=…/Mlaser-v0.0.0.52 QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q -p no:cacheprovider` | **2026-09-18, after merging tasks 7, 9 and 10: 1761 passed, 14 skipped, 2 xfailed (both strict)** in 308.17 s. The run before it had **1 failed**, 1760 passed (306.41 s), and the failure did not repeat: the X11 50 k-`LINE` budget measured 2.89–3.02 s against 3.09–3.29 s in five repeats on a laptop at load ~1.2, so that test is the suspect and its headroom is being widened (§5 task 7). The 14 skips are `tests/test_session_h.py` waiting for Wine session H. (2026-09-16, idle: 1676 passed, 3 xfailed in 224.44 s) |
| Same suite with `NEXCUT_SRC=/nonexistent` (what CI runs) | **2026-09-18: 1616 passed, 159 skipped, 2 xfailed** in 289.97 s (laptop at load ~1.2). (2026-09-16, idle: 1540 passed, 136 skipped, 3 xfailed in 212.00 s) |
| Same suite with every core busy | **not re-run for this report.** The last load sweep was the 2026-09-16 tree (1571 tests): **1571 passed, 3 xfailed** twice, 332.08 s and 332.19 s against 212.8 s idle (1.56x) (§6). Re-running the sweep is §5 task 10 |
| Tests collected | **2026-09-18: 1777 in 71 files** (+ `conftest.py` and the `gates_reference.py` helper). The per-area table below is still the 2026-09-16 count of 1679 |
| `.venv/bin/ruff check . tools/m1_session.py` | **All checks passed!** |
| CI (`.github/workflows/ci.yml`) | ruff + pytest on Python 3.12/3.13/3.14, offscreen Qt, `-p no:cacheprovider --durations=15`. `SRC` is absent there, so the 136 vendor-golden tests skip |
| `UNVERIFIED` markers in `src/` + `tools/m1_session.py` | **261 lines** (grep), inventoried in §3 |
| Hardware sessions run | **0**. No pcap capture of this machine exists |
| Console scripts | `nexcut` (viewer; CO2 + fibre layer, hardware, machining, software and graph-rule pages, curve and crafts editors), `nexcut-mccd` (`serve`/`run`, `tui`, `status`, one-shots, `jog`, `home`, `run-job`, `job-status`, `ctl`), `nexcut-plan` (offline planner → frame file) |
| PORT-PLAN §8.3 streaming jitter gate (**simulator only**) | <!-- JITTER-GATE: `NEXCUT_JITTER_SECONDS=120 QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q -s tests/test_perf_streaming.py` is the 4-minute short form; the full gate is the same command with 600 --> **met** (re-measured for this report at **120 s of machine time per tick**, 4 min for the pair, `nexcut-mccd` + `CardSimulator` in one process). **250 µs tick** (cadence 24.8 ms/frame): 4 848 frames in 120.2 s — DONE, every tick consumed, **0 re-sends, 0 starvation events**, queue low water **4 559 items** (card) / 4 596 (reg 1016) against the `FifoAlarmNum` = 30 floor; frame interval p50 **27.6 ms**, p90 **32.9**, p99 **36.0**, max **44.8** — a producer stall of 11.2 ms (p99) / 20.0 ms (max) on top of the cadence. **1 ms tick** (cadence 99.0 ms/frame): 1 212 frames in 120.2 s, low water **4 695 / 4 703**, p50 **91.1**, p90 **121.5**, p99 **125.7**, max **126.7** — stall 26.7 / 27.7 ms. Limits on this run: p99 stall < 103 ms, max stall < 513 ms (§8.3's 100 / 500 ms × `conftest.speed_factor()` = 1.03 here), and at the 250 µs tick the absolute p99 < 103 / max < 513 are asserted as well. Only the two *time* limits are scaled; the stream criteria — DONE, every tick consumed, 0 re-sends, 0 starvation, the queue floor — never are. **The full 600 s form was last run in the previous phase** and is recorded there: 24 242 frames in 600.2 s at the 250 µs tick with p99 55.8 / max 80.2 ms, and 6 061 frames at 1 ms with p99 125.8 / max 139.4 — the same picture over 5x the duration |
| PORT-PLAN §8.3 planner throughput gate | <!-- PLANNER-GATE: `.venv/bin/pytest -q -s tests/test_perf_planner.py` --> **not met**, strict xfail **X13**. 250 contours in 0.49 s = **1.94 ms/contour**, 168 756 ticks, **2.88 µs/tick** → **194 s for 100 000 contours** against the 30 s budget; 1.93 ms/contour at 62 contours and 675 ticks/contour at every size, so the extrapolation is linear. It was 4.61 ms/contour = 461 s (earlier runs 478–520 s) before the vectorised item path. **The memory half of this gate is closed** (next row). What is left is per-contour numpy dispatch in the geometry stages, itemised in §2 |
| Planner peak memory | **solved, re-measured for this report** in a fresh interpreter: a 3 000-contour job (2 147 816 ticks, 21 877 frames, 6 491 455 words) grows the RSS by **+2.6 MB while streaming** against **+197.7 MB** materialised as a frame list — 76x, and the gap widens with every contour. The previous phase measured the full 100 000-contour job (72.0 M ticks, 733 756 frames, 218 M words) at a **140.3 MB peak**, of which 135.3 MB is the `.chf` document itself: planning and writing cost **4.9 MB, flat in job size**, where a frame list would need ~8 GB |
| Import budget (PORT-PLAN §4 M2, 3 s × `speed_factor`) | 2026-09-16, `speed_factor` 1.03 → 3.08 s: 50 000-segment path, open + fit + paint: DXF lwpolyline **0.39 s**, G-code **0.83 s**, PLT **0.36 s**, `.chf` **0.41 s**; 50 000 separate `.chf` contours **2.38 s**; 50 000 separate contours on the canvas **1.02 s**. **50 000 separate DXF `LINE`s: met, X11 closed (2026-09-18, §5 task 7)** — five runs **2.72–2.86 s** against budgets of 3.13–3.39 s (`speed_factor` 1.04–1.13), of which the streaming reader **0.64–0.66 s** and `ops/import_gates` + `ops/sort` **0.82–0.83 s**; the 50 000-contour `.chf` control case in the same runs 2.44–2.59 s. The pre-task-7 code measured in the same session (three runs, `speed_factor` 1.03–1.12): **5.34–5.44 s**, gates **3.52–3.69 s**, control 2.38–2.47 s |

Tests per area (collected, 1736 total; only `ops` changed on 2026-09-18):

| Area | Tests | Largest files |
|---|---|---|
| `plan` | 473 | `test_plan_junction` 175, `test_plan_scurve` 97, `test_plan_vectorised_equivalence` 52, `test_plan_vectorisation_review` 32 |
| `ui` | 325 | `test_import_ui_fidelity_review` 54, `test_ui_safety_review` 52, `test_ui_curve_editor` 37, `test_ui_param_pages` 30 |
| `mcc` (framing, CRC, dissector, simulator, commands, registers, transaction, fifo, safety) | 299 | `test_mcc_safety` 79, `test_mcc_protocol_fidelity` 44, `test_mcc_safety_adversarial` 37 |
| `io` + `.chf` | 293 | `test_io_gcode` 53, `test_io_dxf_stream` 46, `test_io_dxf` 41, `test_chf_writer` 37, `test_io_dxf_stream_review` 21 |
| `mccd` + `core/config` + `tools/` (`m1_session.py`, `wine_session_h.sh`) | 206 | `test_mccd_safety_review` 55, `test_mccd_job` 32, `test_mccd_cli_tui` 29, `test_m1_session` 14 |
| `ops` | 85 | `test_ops_gates_equivalence` 57, `test_ops_sort` 17, `test_ops_scan` 11 |
| integration + smoke | 22 | `test_smoke` 11, `test_integration_consistency` 11 |
| `core/schema` | 18 | — |
| cross-cutting stream/plan review | 9 | `test_stream_plan_fidelity_review` |
| performance gates | 6 | `test_perf_planner` 4, `test_perf_streaming` 2 |

Adversarial review suites, counted inside the areas above: **354 tests in 10 files**
(`test_mccd_safety_review` 55, `test_import_ui_fidelity_review` 54, `test_ui_safety_review` 52,
`test_mcc_protocol_fidelity` 44, `test_mcc_safety_adversarial` 37, `test_plan_vectorisation_review` 32,
`test_io_fidelity_review` 27, `test_plan_fidelity_review` 23, `test_io_dxf_stream_review` 21,
`test_stream_plan_fidelity_review` 9) — 105 of them added by this phase's three reviews.

---

## 1. Milestones

| M | Title | State | Gate — what was actually run |
|---|---|---|---|
| M0 | Foundations, passive instrumentation | **done**, one gate item waiting for the first capture | golden tests green ✔ · dissector on the package logs ✔ (805 transactions, 22 frames, 0 remainder) · dissector on a real tcpdump capture ✘ — **no capture of this machine exists** (§5 task 1) |
| M1 | Hello machine: jog/home on the real card | **software complete and through three safety reviews; hardware not run** | simulator rehearsal ✔ (`tools/m1_session.py`, 14 tests; a re-arm after a link blip now needs its own operator `y`) · ruler/homing/exception gate ✘ — needs the owner at the machine (11 §7 steps 1–10) |
| M2 | File load and render | **partial** | "renders identically" ✔ only on the relaxed metric (IoU ≥ 0.9 + chamfer 1.000; pixel-identical on 2 of 8) · `ManuContour` order via `SortType=4` ✘ — the gate's premise is wrong (§1.3) · the 50 k-`LINE` import budget ✔ (X11 closed 2026-09-18: 2.72–2.86 s against 3.13–3.39 s, §5 task 7) |
| M3 | Layers, parameters, hardware config | **partial**: file layer complete, six property pages + the curve and crafts editors, write-back wired — and since the UI review the pages no longer move a value the operator did not edit | XML round-trip ✔ on every vendor file · every editor on every page opened and committed over the real `BkHardPara.xml` / `BkManuPara.xml` without changing a byte ✔ (§1.4) · Wine load of port-written files ✘ — not run (§5 task 4) |
| M4 | Job streaming, dry run | **partial**: end to end into the simulator, never onto the card; the planner streams, the memory ceiling is gone, and the *production* stream path now reproduces vendor frames byte for byte | raster geometry ✔ · 3 leaked vendor frames re-encoded byte-identically through `JobFrameStream` + `push_uniform` ✔ (new, §1.5) · vendor item-stream diff ✘ (no vendor capture) · §8.3 jitter gate ✔ **simulator only** · §8.3 planner throughput ✘ (X13, 194 s against 30 s) · machine dry run ✘ |
| M5 | Live CO2 cutting | **not started, deliberately** — `LASER_ARMED` is unreachable from any client, and D13 is written but unsigned | — (needs D13 signed off: §5 task 5) |
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
* **`tools/m1_session.py`** — the guided 10-step session with a capture wrapper, per-step census and safety checklist. 14 tests.
* **`docs/M1-BENCH-SESSION.md`** — the operator runbook, re-checked against the code for this report (§5 of that file, the flag tables and the job commands).

**Three adversarial safety reviews have now run over this code.** The first closed D9/D11/D12
(R1–R11). The second (R12–R21, 23 new tests in `tests/test_mccd_safety_review.py`, 55 in the file)
found five more defects, three of which could move the machine or hand the card to a second master.
The third (2026-09-16, lens *ui-safety-ux*, `tests/test_ui_safety_review.py`) re-derived D13's
unreachability and `arm_owner`'s contents and found nothing wrong with either, but did find **M1**
below — the one place where the tool could put the machine back under power without asking. Every
fix has a test that failed first:

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
* `tools/m1_session.py` reconnects lazily, and a reconnect is a new IPC connection that owns
  neither the arming (D9) nor the right to move (D9/R12). Since 2026-09-16 the tool notices this
  itself: every motion command goes through `_motion_call`, which re-runs *this step's* arming on
  the new connection and confirms with `status` (`arm_owner_is_self`) that the daemon agrees, and
  otherwise raises `ArmingLost` and **sends nothing**. Only a blip it cannot repair ends the step,
  which is then restarted with `--steps N`.
* **Finding M1 of the UI/safety review, fixed:** that repair used to happen on a printed line
  alone. But per D9 the daemon has already *stopped and disarmed* the machine by then, and the axis
  need not be where the step left it — so the operator's earlier `y` was consent for a motion on a
  machine that no longer exists. `_ensure_armed` now asks its own `op.confirm("s<n>.rearm.<cmd>")`,
  defaulting to **No**; a `no` records `rearm_declined`, raises `ArmingLost` and sends nothing.

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
  reference: a vendor-sorted job produced under Wine (§5 task 4).
* **Performance, re-measured for this report** (idle laptop, `speed_factor` 1.03 → budget 3.08 s
  where PORT-PLAN asks 3 s): a 50 000-segment path opens, fits and paints in **0.39 s** (DXF
  lwpolyline), **0.83 s** (G-code), **0.36 s** (PLT) and **0.41 s** (`.chf`); 50 000 *separate*
  contours from a `.chf` in **2.38 s**; 50 000 separate contours on the canvas in **1.02 s**.
  50 000 *separate* DXF `LINE` entities took **5.18 s** in that run (strict xfail X11) — not
  because of the parse: `io/dxf_stream.py` reads the `ENTITIES` section tag by tag without
  ever building an ezdxf document (**0.80 s** for those 50 000 `LINE`s in this run, against
  3.22–3.63 s through ezdxf), and `read_dxf(reader="auto")` falls back to ezdxf for anything the
  fast path will not vouch for (binary DXF, `INSERT`, text-to-curves, a tilted OCS, any structural
  anomaly), recording which reader ran in `DxfImportResult.reader` / `fallback_reason`. What was
  left was `ops/import_gates` + `ops/sort`: 3.39 s of the 5.18 s. **Closed on 2026-09-18 by §5
  task 7**: the gates now take 0.82–0.83 s and the whole open 2.72–2.86 s against 3.13–3.39 s
  (`speed_factor` 1.04–1.13, shared laptop), with output pinned to the old code by
  `tests/test_ops_gates_equivalence.py`.
* **The streaming reader survived an adversarial review** (2026-09-16, lens *silent divergence
  between the two readers*, `tests/test_io_dxf_stream_review.py`, 21 tests): **no finding of
  substance**. cp936 layer names resolved through the `LAYER` table, nested and block-local
  `INSERT` (the fallback fires for the first and correctly does not for the second), five spline
  flavours including closed/periodic and fit-points-with-tangents, bulge extremes, and a hostile
  tag corpus (repeated codes, a leading bulge, a wrong vertex count, colour 0/−3/257, `XLINE`/`RAY`,
  degenerate `SOLID`/`3DFACE`) all give an identical `ChfDocument`, an identical layer assignment
  and identical counters — or a clean `StreamUnsupported`. Plus 240 random corpora × both
  `read_color` states: 0 disagreements.

### 1.4 M3 — partial (file layer complete, six property pages, no Wine cross-check yet)

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
* **`ui/pages/param_pages.py`, `ui/pages/layer_fiber.py`, `ui/pages/layer_file.py`,
  `ui/curve_editor.py`, `ui/pages/crafts.py`** (previous phase, 2 132 lines + 1 461 lines of tests,
  115 new tests). Six property pages instead of one — CO2 layer, fibre layer (164 attributes,
  pierce stages, the `A250607_*` fibre/CO2 selector warning), hardware (446 attributes),
  machining (331), software and graph rules — plus the `PWMCurveNodes`/`FreqCurveNodes` curve
  editor with a preview, and the crafts editor for per-contour lead-in/out and cool points. The
  main window has 9 docks instead of 4. `BkLayerPara.xml` is written back from the dock
  (`layer_file.LayerFileBar`), and `ui/app.py` passes the `--layer/--manu/--hard` paths through
  as the Save target. Vendor round-trips held byte for byte through the editors: `BkHardPara.xml`,
  `BkManuPara.xml` (3 pages), `BkLayerPara.xml` (11 slots × 5 pierce stages × both layer pages),
  3 `.chf` files through the crafts editor, and every fibre technology preset.
**A fourth adversarial review (lens *ui-safety-ux*, `tests/test_ui_safety_review.py`, 52 tests)
went through the property pages, the write-back and the two operator tools. It found twelve
defects, and the four that matter are all the same defect in different clothes: the editor changed
a value nobody edited.** Every fix has a test that failed first:

| # | Defect | Effect before the fix | Fix |
|---|---|---|---|
| U1 | `editor_range` handed a u32 descriptor range straight to a `QSpinBox` | `createEditor` raised **`OverflowError`** for **ten editable Hardware-page rows** (`SP.MachineID`, `SP.DataCardID`, `SP.CommandID`, `SOP.JoystickID1..5`, `SOP.MonitorStartID/EndID`, all min 0 max 4294967295): a double-click threw out of a Qt delegate callback, so those rows were not editable at all | int rows are clipped to the spin box's own window (`INT32_MIN/INT32_MAX`). An **UNVERIFIED port choice** (§3.8), justified by the vendor writing these signed — `SOP.JoystickID2 = "-684904636"` in this machine's `File/BkHardPara.xml` |
| U2 | the editor's own end stop stored a value `Descriptor.validate` rejects once the display unit ≠ the descriptor unit | **82 distinct (row, display unit) pairs**: `ZF.ZFFollowSpeed` max 9999 → `9999.000000000002` (m/min), `MC.ManuAcc` min 500 → `499.999` (inch/s²), `FCP.MaxAcc` min 1 → **0** (G, int truncation) | bounds are quantised *inward* to the editor's resolution and re-checked through `to_stored`, and `clamp_stored` runs on write |
| U3 | a vendor value outside the descriptor range could not be represented, so opening and closing the row wrote the clamp back | **the file changed with no edit, no prompt and no undo**: `GRP.EdgeBoardSizeX/Y` hold `5.0` in this machine's `File/BkManuPara.xml` against a descriptor min of `50` → 5 became **50**, a 10× change. `Descriptor.validate`'s own docstring says vendor data is not guaranteed to satisfy min/max | new `PropertyGrid.stored_bounds` widens the range to contain the value the row already holds: the file is preserved, never "corrected" |
| U4 | display rounding moved a value on a no-op open/commit | **15 rows of the real vendor files moved**: `MAC.Acceleration` 20000 → 19999.68 (G, 4 dp), `MC.XFastMoveAcc` 6000 → 5999.71; worst, an int row in a converted unit is unrepresentable — `FCP.MaxAcc` 20000 mm/s² = 2.0394 G, an int spin box can only offer `2` → **19613** (−2 %) | an editor closed without a change writes nothing (`_OPENED_AT`), and an int row in a converted unit gets a `QDoubleSpinBox` — the rule `display_text` already used |
| W1–W3 | `io/params._atomic_write` | no directory `fsync` after `os.replace` (so a crash could lose exactly the ordering `write_params`'s docstring promises); `mkstemp`'s `0600` was carried onto the vendor file on **every save**, and the `.bak` took the temp file's mode rather than its source's; the verify refusal named no recovery path | `_fsync_dir` after every replace; the replaced file's mode is carried over and the backup inherits its source's; the refusal names the `.bak` |

Re-derived in the same review and **found sound, now pinned**: `write_params` under a disk-full
mid-write (old file *and* backup intact, no temp left behind), two saves in a row, a concurrent
editor, and the vendor `BkLayerPara.xml` round-tripping byte-identically through `LayerFileBar`;
the crafts editor leaving `compensate_type/width`, `pwm_enable`, `double170`, `double188` and the
legacy lines untouched; the curve editor clamping a typed coordinate, locking on an unparsable
curve and sorting a non-monotonic curve through the same code path the stream uses
(`plan.pwm_schedule.CurveNodes.parse`), so the preview cannot diverge from what is streamed.

*Since 2026-09-18:* `LayerFileBar.save` / `ParamPage.save` write the file and then show "N values
outside their range (saved anyway): …" from `document.validate()`. Blocking would be wrong —
vendor files already violate their own schema, which is the whole of U3. The same pass fixed a
bool row stored as `2` being rewritten to `1` merely by building a page (§5 task 10).

* **Absent.** Hardware-page read-back of 50000/50200/59600+ (the daemon reads 50000/26 only, for
  K, the bus cycle and ZFType); the vendor's `CSelectTechnologyDlg` preset browser over
  `Technology/Fiber` and `Technology/CO2` (the pages use a plain file dialog; `list_technology`
  already exists in `io/params`); a document-modified flag, so an edited job has to be saved
  through File > Save (that belongs with the editing work of M2).
* **Gate.** XML round-trip ✔. **New since the UI review:** opening and committing *every* editor
  on *every* page over this machine's real `BkHardPara.xml` and `BkManuPara.xml` now leaves both
  files byte-identical — before it, 15 values moved and two were changed by 10 % and −2 %.
  "The Windows tool (under Wine) loads files saved by the port" ✘ — still not run. The technology
  XML the page writes is byte-identical to a vendor preset except for the `CutFreq` the 13 shipped
  CO2 presets lack, so it is the first candidate for Wine session H (§5 task 4).
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
* **The planner streams** (previous phase). `plan.__main__.stream_job()` yields one `PackedFrame`
  at a time and `build_job()` is a thin materialising wrapper over it, so every existing caller and
  golden keeps working; `nexcut-plan` writes each frame as it is produced and `mccd.feeder.JobFeeder`
  already took a lazy source. The tick → item → word → frame path is numpy array work
  (`plan/items.carry_cells`, `plan/items.tick_words`, `mcc/fifo.FrameStream.push_uniform`) with a
  scalar reference path below 256 ticks, held against the scalar loop over 1.1 M random ticks
  (`tests/test_plan_vectorised_equivalence.py`) and against the vendor `autosave.chf` re-planning
  to its recorded 1 277 frames / 126 089 ticks.
  * **How close the two paths really are** — corrected by review finding R-V4, and this file used
    to overstate it. They are **not** cell-for-cell identical. With *random* increments the grid
    roundings are random and the running sums wander as √n, which is all the existing fuzz could
    ever see; with a **constant** increment — a straight line at constant speed, the commonest
    geometry there is — every tick gets the same rounding and the sums separate *linearly*:
    **2 cells of 40 000 differ on a 1 m line, 372 on a 10⁶-tick diagonal**. What is guaranteed, and
    now pinned, is the bound and not identity: the emitted position never differs from the scalar
    loop by more than **one pulse (3.9 µm)** at any tick — a pulse displaced by one 250 µs cycle,
    never dropped or duplicated — the totals are exactly equal, and the residual carry stays inside
    `n · max|increment| · 2⁻⁴⁷`. That is smaller than the difference between this float64 model and
    the vendor's own x87 80-bit arithmetic. Making the increments exact costs 74.7 → 166.7 ms per
    10⁶ two-axis ticks (≈ +6.6 s on X13's projection) and was deliberately not taken.
  * **Memory, re-measured for this report** in a fresh interpreter: a 3 000-contour job
    (2 147 816 ticks, 21 877 frames, 6 491 455 words) grew the RSS by **+2.6 MB while streaming**
    against **+197.7 MB** for the same job materialised as a frame list — a 76x gap that widens
    with every contour. The previous phase measured the full 100 000-contour job (72.0 M ticks,
    733 756 frames, 218 M words) streamed to `/dev/null` at a peak of **140.3 MB RSS**, of which
    135.3 MB is the `.chf` document built before the run: planning and writing added **4.9 MB,
    flat in job size**, where a frame list would need ~8 GB.

**The streaming planner was then reviewed adversarially** (lens *silent numerical drift*,
`tests/test_plan_vectorisation_review.py`, 32 tests). Two defects and one coverage hole, all
closed; the drift itself is R-V4 above:

| # | Defect | Effect before the fix | Fix |
|---|---|---|---|
| R-V1 | `JobFrameStream.motion` emitted the records the wrapped builder had queued **after** the ticks | a caller that writes the prologue and then asks for the move got `3000 3000 3000 …` where the scalar path gives `3001 3002 3000 3001 9999 103 2001 …`: the `3002` mode word, the gas/laser `9999` DO records, the ZF `103` cut-height move and its `2001` follow wait all landed *behind* the cut they must precede — with `laser_records=True`, the laser switched on after the contour was cut. Not reachable from `_job_frames`, which drains explicitly, but it is public API | `yield from self._drain()` at the head of `motion` |
| R-V2 | `tick_words` named a different tick and a different axis from `tick_item` for the same overflowing run | both raise `ValueError`, so diagnostic only — but "which tick overflowed" must not depend on which path ran | earliest offending tick, dY before dX, hot path unchanged |
| R-V3 | `FrameStream.push_uniform` accepted a laser mask of the wrong length | too short → `IndexError` out of `np.cumsum`; too long → frames charged `laser_items` from the wrong records (metadata only: safety re-derives laser content from the words) | `ValueError("<k> laser flags for <n> records")` |
| R-V5 | **coverage hole**: no vendor byte had ever been compared against the *production* path | `tests/test_plan_items_golden.py` re-encodes the 22 leaked frames through `JobStreamBuilder` + `FramePacker` — the *definition* path. What a job actually streams is `JobFrameStream` + `push_uniform`, and the goldens would have stayed green straight through R-V1 | frames 0x1f8, 0x39 and 0x279 now reproduce **byte-identically through the production path**; all three failed before the R-V1 fix, and no tolerance was widened anywhere |

Re-derived independently in that review with **no defect found**: pulse totals on a 1 m line, a
10 000-segment polyline, a 10⁶-tick diagonal and a 10⁶-tick reversal (each exactly
`trunc(net_mm · pulses_per_mm)`, and cutting one move into 10 000 separate quantiser calls gives
identical cells); a 1.5 M-tick differential fuzz over ten shape families with an independent
generator and seeds (0 mismatches); the 300-word rule for record widths 1…300 with partial laser
masks; and 200 random jobs through `JobFrameStream` versus `JobStreamBuilder.frames`.
* **Still open:** the planner *throughput* gate (100 k contours in < 30 s) is **not met** — strict
  xfail X13, numbers in §0. It is **194 s** in this report's run instead of 461–520 s, and the
  remaining cost is per-contour numpy dispatch in the geometry stages, not the item path (§2).
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

## 2. Strict xfails (2)

Each documents a known divergence. Fixing one turns the test into an XPASS failure, which forces the
marker to be removed — so this list cannot rot. **Never relax one to make a run green**; a strict
xfail is the project's record that a gate is not met.

Ten of the original thirteen were closed on 2026-09-16 and are ordinary passing tests now: **X1**,
**X2** (the A9 re-trace of the dwell builder and the look-ahead core), **X3**, **X4**, **X5** (D9,
D11, D12 decided and implemented), **X6** (D14: a non-empty v2–v4 reserved line is kept in
`ChfDocument.legacy_reserved` and re-emitted verbatim), **X8**, **X9** (boost double spelling and
the whole-literal number grammar), **X10** (spline sampling honours the chord step), **X12**
(vectorised `.chf` token reader). **X11** (the 50 000-`LINE` import budget) followed on
2026-09-18 with §5 task 7: `test_import_ui_fidelity_review::test_open_50k_separate_dxf_lines` is a
plain timed test now — 2.72–2.86 s in five runs against 3.13–3.39 s (`speed_factor` 1.04–1.13),
where the pre-task-7 code took 5.34–5.44 s in the same session. What remains:

| # | Test | Sharpened reason, with this report's numbers | Resolved by |
|---|---|---|---|
| X7 | `test_io_fidelity_review::test_unknown_attribute_survives_rewrite` | **The only one of the three that is a fidelity gap rather than a speed gap, and the only one no amount of work here can close.** Unknown XML attributes are dropped on rewrite. ParaModule's set-value path (`0x10010cdf` FindElem/AddElem/SetAttrib) keeps a CMarkup DOM and *may* preserve them — which decides whether a file round-tripped through the port loses data a future vendor version added. It cannot be settled by reading the disassembly further: the answer is a diff of two files the vendor tool wrote | **§5 task 4, Wine session H, question H-3**: add an attribute, load and re-save in Mlaser, diff. One afternoon, no machine |
| X13 | `test_perf_planner::test_100k_contour_job_plans_in_under_30_s` | PORT-PLAN §8.3: 100 k contours project to **194 s** against 30 s, i.e. **6.5x**, down from 461–520 s (**15–17x**). The *memory* half of this entry is **closed** — the planner streams, so a job of any size costs ~2.6 MB of frames instead of the ~8 GB `build_job` used to demand — and so is the item path, which is 5.4x faster and now costs 47 s of the 194. What is left is **per-contour numpy call overhead in the geometry stages**: the inter-contour rapid plan 55 s, `lookahead.plan_velocity` 30 s, the rapid stream 25 s, `sampler.sample_plan` 18 s, everything else 24 s. A one-segment contour is ~675 ticks and each stage makes tens of small array calls on it at ~1–3 µs each whatever the length, so the next factor has to come from planning **many contours in one array pass** | **§5 task 8**: batch `plan/lookahead.py`, `plan/junction.py`, `plan/scurve.py` and the rapid planner. No capture, no owner |

---

## 3. UNVERIFIED inventory, grouped by what resolves each item

**261 `UNVERIFIED` lines** live in `src/` and `tools/m1_session.py`. They fall into eight groups,
and the only thing that matters operationally is **which single bench or Wine step clears each
group** — so the table below is sorted by that, and the last column points at the §5 task that
does it. Five of the eight groups are cleared by exactly two sessions.

| Group | The step that resolves it | Needs the owner? | Needs the machine? | Blocks | §5 task |
|---|---|---|---|---|---|
| §3.1 | **M1 bench session**, native steps 1–7 and 10 (`tools/m1_session.py` + `docs/M1-BENCH-SESSION.md`) | yes | yes | M0's last gate item, M1 sign-off, D2, D7-open, homed soft limits | **1** |
| §3.2 | **M1 bench session, step 8** — FIFO tick period (vendor dry run preferred; native `run-job` optional) | yes | yes | M4 gate 2, O1, reg-1016 units, O6's FIFO half | **2** |
| §3.3 | **M1 bench session, step 9** — vendor Pause/Continue/Stop, and the A/B item stream | yes | yes | N2, planner fidelity, M4 gate 1(a) | **2** |
| §3.6 | **Wine session H** — the vendor tool under Wine, **machine not needed**; the largest group and the cheapest to run | yes (their Wine prefix) | no | M2 sort gate, M3 gate, X7, most import/XML questions | **4** |
| §3.4 | **Session E** — a real cut with the laser on, after M1 *and* a signed-off D13 | yes | yes | M5 | after 1–3, 5 |
| §3.5 | **Capture G** — fibre source and height follower | yes | yes | M6 | after 1–3 |
| §3.7 | **Static re-trace** — disassembly, desk work, anyone can do it | no | no | planner fidelity details | 10 |
| §3.8 | **Port choices** — no capture settles them; they are design parameters | no | no | nothing; revisit as design | — |

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

**Property-page editor bounds** (new, from the UI review): whether the vendor's own editor for a
u32 row (`SP.MachineID`, `SP.DataCardID`, `SP.CommandID`, `SOP.JoystickID1..5`,
`SOP.MonitorStartID/EndID`) accepts a value above 2³¹−1, or writes it signed as
`SOP.JoystickID2 = "-684904636"` in this machine's file suggests. The port clips those editors to
the `QSpinBox` window (§3.8) and preserves whatever the file holds; the vendor's behaviour decides
whether that clip is right. Also: what the vendor does when a file holds a value outside a
descriptor's own min/max (the port keeps it, U3).

**i18n:** first duplicate id wins; English as the fallback column; a merge-recovered record loses (`ui/i18n.py`).

**Planner parameter defaults:** AddTime/Is4Freq when absent; the `p9` ctor default; the node-string `"0,48,…"` parsing and the empty-string rule (`plan/params.py`, `pwm_schedule.py`).

### 3.7 Static re-trace — disassembly, desk work

* Rapid planner call (A6 §1.4).
* S-curve `0x1000eeb0` with no speed change; last-phase evaluation past T; sample order (`plan/scurve.py`).
* x87 precision control of `round_half_away` (53-bit assumed).
* HAL address-filter interval ends (`registers.vendor_would_send`).
* Dissector ladder grouping heuristics (`LADDER_GAP_S`, `max_rung`).
* Gating of `[9999,16]` (N6). DENY until then.
* **Done in this phase:** the look-ahead core (A9) and the dwell builder `0x4427e0` (A9 §2), and
  both corrections they produced are now **written into** `docs/analysis/05-motion-pipeline.md`
  (previous phase): §7.4 says the cited `0x100114d0` is MotionCtrl.dll's build of the core and that a
  cut runs CADModule's `0x100fdc50` (reached from plan `0x100fffe0`), and §7.2 carries a marked
  correction block saying the `0.99` at `.rdata:0x101116d0` is the **threshold of the
  `VelDecc.txt` log**, compared against the per-piece speed factor — it multiplies nothing. The
  open question "what does `CADMODULE_NODE_FACTOR` multiply?" is therefore answered and gone from
  the list above; `plan/lookahead.py` says so at the constant. One small fidelity gap is left
  and documented there: `format_veldecc()` writes a line per node, while the vendor writes one
  only for a node whose piece factor is below 0.99.

### 3.8 Port choices — no capture resolves them

These are not vendor facts. They are design parameters, and changing one is a decision, not a
discovery.

* **D1:** un-homed limits 10 mm and 20 mm/s; the token bucket.
* **D4:** timeouts of 150 ms; poll periods 90 ms / 1 s / 1 s / 10 s.
* **D6:** deadman 200 ms; pendant silence 1040 ms.
* **D12:** the 1 s card-lock re-assert period (`_CARD_LOCK_RECHECK_S`, added by review R13).
* **`ui/property_grid.INT32_MIN` / `INT32_MAX` as the editor bound for a u32 descriptor** (UI
  review U1). A value above 2³¹−1 in a vendor file is still *preserved* and *shown* — `stored_bounds`
  widens the range to contain it — but it cannot be typed in. Settle in Wine session H (§3.6).
* **`ui/property_grid._QUANTISE_STEPS = 4`** — how far a descriptor bound may be stepped inward to
  become representable in the editor's own resolution (UI review U2). Bounded by construction.
* **`tools/wine_session_h.sh` `WINE_USER` fallback chain** (`$USER` → `$LOGNAME` → `id -un`).
  Cosmetic: it only builds a log-path hint.
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

### 4.1 Closed and enforced (`docs/DECISIONS.md` D1–D12, D14, D15)

Fourteen of the fifteen entries are **decided**; **D13 (laser arming) is written but `proposed`
and unsigned**, see §4.2 — it is the only one of the fifteen that blocks a milestone. Three of the
decided ones were the open questions a phase ago (D9, D11, D12) and are closed in code; five carry
amendments written by the safety reviews, and D15 is new this phase.

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
| D14 | **`.chf` v2–v4 reserved lines are kept verbatim in a model slot; nothing is rejected** | decided 2026-09-16, implemented in `io/chf.py` + `model/graph.py` (`legacy_reserved*`), closes strict xfail X6 |
| D15 | **A parameter file changes only where the operator changed it**: an editor that is opened and closed writes nothing, a bound is never allowed to store what the schema rejects, and a vendor value outside its own descriptor range is preserved rather than "corrected" | **new — decided 2026-09-16** by the UI review (U1–U4, W1–W3); implemented in `ui/property_grid.py` and `io/params.py` (§1.4) |

### 4.2 Still open

| Id | Question | Blocks | What decides it |
|---|---|---|---|
| **D13** | Laser arming: the IPC shape, the job token it must quote, the operator confirmation, the auto-disarm rule. D5 says `LASER_ARMED` needs its own entry | **all of M5** | **written 2026-09-16 and marked "proposed — needs the owner's sign-off before implementation"** (`docs/DECISIONS.md` D13). No M5 code exists or may exist until it is signed off; The audit-log bound its §4 depended on is in place (§5 task 9, `mcc.safety.WriteLog`). Signing it off is §5 task 5 |
| D7-open | May the operator jog off a pressed hard limit while `alarm_1 ≠ 0`? The gate refuses all non-ALWAYS writes in that state | M1 step 7 sign-off | 11 §7 step 7: press each limit, read 1006 and 2000+10·slot, then decide |
| D2-follow | Set `position_counts_per_mm` and flip `position_scale_verified` | homed soft limits, absolute moves | measured values from steps 3/4 only |
| M2-gate | The `ManuContour`/`SortType=4` gate has a wrong premise and must be replaced by a vendor-sorted reference | M2 sign-off | Wine session H (§5 task 4): Sort, save, commit the order as a golden; then rewrite the gate (§5 task 6) |
| M2-render | Accept "IoU ≥ 0.9 **and** chamfer = 1.0 against an asymmetric control" as the meaning of "renders identically" | M2 sign-off | amend PORT-PLAN §4 M2 wording, or build a DDA reference rasteriser (§5 task 6) |
| Daemon language (R8) | Keep the Python `mccd`, or move it to Rust/C++ | R8 retirement | **provisionally answered "keep Python"**: the §8.3 jitter gate passes on the simulator with margin (§0). Re-open only if the real card's tick period turns out much shorter than 250 µs (O1, step 8) |
| D9 residual | Close the `_release_arming` window (a new client can arm between `_arm_owner = None` and `_disarm_and_stop`), or keep it | nothing — the failure direction is fail-safe | closing it needs card I/O under `MccDaemon._lock`; deliberately not done |

---

## 5. The next ten tasks, in priority order

**What the last phase closed**, so nobody repeats it: the streaming, numpy-vectorised planner item
path (old task 6 — memory solved, 2.4x end to end, X13 still open on the geometry stages); the
streaming DXF reader (old task 7 — the parse is 4–5.8x faster, X11 then still open on the import gates, since closed by task 7 below);
D13 written and marked *proposed* (old task 8); the five remaining M3 property pages plus the curve
and crafts editors (old task 9); the A9 doc corrections, D14/X6, `arm_owner` in the status snapshot
and the m1-session re-arm (old task 10).

Three adversarial reviews then went over all of it, each with its own lens, and **every defect they
found is fixed with a test that failed first**: twelve in the UI, write-back and operator tools
(§1.4, plus the m1-session re-arm in §1.2 and the two Wine-kit fixes in task 4), three in the streaming planner plus one overstated
claim and one coverage hole (§1.5), and **none at all** in the streaming DXF reader (§1.3) — which
is itself a result, since that reader is the one that silently decides which of two code paths
reads a customer's drawing.

The ten below are sorted by value, and grouped by **who has to be in the room**. A session with
nobody but an agent starts at task 7; tasks 7–10 need neither the owner nor the machine and can run
in any order.

### Needs the owner at the machine (tasks 1–3 — one bench session, about half a day)

These three are one sitting, not three. They are the highest-value work in the project by a wide
margin: between them they close M0's last gate item, the M1 sign-off, D2, D7-open and the whole of
§3.1–§3.3, and they are the only source of the artefact task 2 produces. Everything else in this
list is worth less than the first hour of this session.

1. **Run the M1 confirmation session — native steps 1–7 and 10, tcpdump running.**
   `tools/m1_session.py`, runbook `docs/M1-BENCH-SESSION.md`. Laser PSU key off, gantry mid-bed,
   hand on the E-stop. The only prerequisite left is a simulator rehearsal (that runbook §2);
   D9/D11/D12 are closed in code and the runbook was re-checked against the code for this report.
   *Settles* K, the bus cycle, block 5000, O6's jog half, bit 31, the stop profile, the home vector,
   O8 polarity, exception 2 — the whole of §3.1. *Produces* the first pcap of this machine, which
   is also the last open M0 gate item.
   **New since the last runbook read:** if the link to the daemon blips mid-step, the tool now asks
   its own `y/N` before re-arming (the daemon has already stopped and disarmed the machine, D9), and
   a `no` ends the step with nothing sent. Re-run just that step with `--steps N`.
2. **Steps 8 and 9 in the same session, with the vendor tool.** The dry-run 100 mm X move with reg
   1015/1016 read before and after the first frame gives the tick period (**O1**, the single most
   load-bearing UNVERIFIED number in the planner) and the reg-1016 units; Pause/Continue/Stop gives
   N2. **Save the vendor frame stream of a drawing that also exists as `.chf`.** That one artefact
   is what turns M4 gate 1(a) from "the geometry matches" into a real item diff, and §3.3 cannot be
   got any other way. Optionally run `nexcut-mccd run-job` natively too: it closes O6's FIFO half
   and is the first time the port streams onto the card.
3. **Measure the M1 gate itself, then dissect what the session recorded.** A 100 mm X move against
   a ruler (0.1 mm), homing repeatability, read-back counts/mm → set `position_counts_per_mm` and
   flip `position_scale_verified` (D2); without it, homed soft limits and absolute moves stay
   unavailable *by design*. Step 7's limit readings answer D7-open. Then, at the desk (no machine,
   no owner): write `docs/analysis/11-capture-findings.md` and push the results into
   `mcc/registers.py`, `mcc/simulator.py`, `core/config.py` and §3.1–§3.3 of this file.

### Needs the owner at the keyboard, but not at the machine (tasks 4–6)

4. **Wine session H.** The single highest-yield desk task: the vendor tool under Wine, an
   afternoon, and it unblocks three gates at once.
   * Sort `autosave.chf` with `SortType=4` and save it → a valid M2 sort reference. The current
     gate is unmeetable as written (§1.3), so this replaces the reference rather than passing it.
   * Load port-written `.chf`, `Bk*.xml` and technology files → the M3 gate. Start with the
     technology XML the CO2 page writes: it differs from a vendor preset only by `CutFreq`.
   * Add an unknown attribute, load, re-save, diff → **X7**, the only strict xfail that is a
     fidelity gap rather than a speed gap.
   * Answer the rest of §3.6 (import, XML, crafts, scan, i18n) and the two new editor-bound
     questions the UI review raised.

   The kit is written and tested: `docs/WINE-SESSION-H.md` and `tools/wine_session_h.sh` (prepares
   the prefix, `--dry-run` creates nothing, never writes into the vendor package — 11 tests). Since
   the UI review the script also recovers from an interrupted copy and no longer aborts when `$USER`
   is unset. What is missing is the session itself.
5. **Sign off, amend or reject D13 (laser arming).** It is written (`docs/DECISIONS.md` D13) and
   marked *proposed*; **no M5 code exists or may exist until the owner signs it**, and M5 is the
   milestone the machine actually exists for. Reading it takes twenty minutes; §2's eight checks and
   §3's eleven auto-disarm events are where the argument is. The audit-log bound §4 of that
   entry depended on is done (task 9, 2026-09-18).
6. **Settle the two M2 gate wordings** (`docs/PORT-PLAN.md` §4 M2, and §4.2 of this file). One is a
   decision only the owner can make — accept "IoU ≥ 0.9 **and** chamfer 1.000 against an asymmetric
   control" as the meaning of "renders identically", or pay for a DDA reference rasteriser. The
   other falls out of task 4: with a vendor-sorted job in hand, rewrite the `ManuContour` /
   `SortType=4` half of the gate against a reference that exists.

### Can be done without the owner and without the machine (tasks 7–10)

7. **Done 2026-09-18 — X11 closed: `ops/import_gates.py` and `ops/sort.py` vectorised.** The
   50 000-`LINE` open now takes **2.72–2.86 s** (five runs, budgets 3.13–3.39 s at `speed_factor`
   1.04–1.13 on a laptop shared with another agent's test run); the same session measured the
   pre-task-7 code at 5.34–5.44 s, and the 50 000-contour `.chf` control case at 2.44–2.59 s
   after against 2.38–2.47 s before, so the cost was removed, not moved. The gates went from
   **3.52–3.69 s to 0.82–0.83 s**; per stage on the X11 drawing after the change (best of five,
   stages timed separately): `sort._nearest` 0.42 s, `remove_overlaps` 0.14 s, `merge_connected`
   0.11 s, open flags + containment + reversals 0.08 s. How:
   * `sort._nearest`: every step starts at the exit point of the row just chosen, so the 8
     nearest entry rows of every possible exit point come from one batched KD-tree query; a step
     scans that list with the same tie window as the live query and falls back to the live
     KD-tree only when the list cannot prove its answer (about 370 of 50 000 steps here).
   * `remove_overlaps` / `merge_connected`: one KD-tree pass (glyph bbox corners, Chebyshev; open
     contour endpoints, Euclidean; radius widened by 1e-6 relative so it is a superset of the exact
     test) finds the items that can possibly be a duplicate / touch another contour, and only
     those go through the unchanged per-item grid logic, in document order.
   * smaller: `contour_endpoints` / `is_closed` / `_is_point_contour` short-cut plain segments,
     `reverse_contour` calls the constructor instead of `dataclasses.replace` (guarded by a
     field-list check), the nearest sort computes open/closed flags once for both containment
     and the walk.
   Behaviour is unchanged by construction and by test: `tests/gates_reference.py` is the
   pre-task-7 code, frozen, and `tests/test_ops_gates_equivalence.py` (57 tests) requires identical
   graphs, identity pattern, counts, order, reversal flags and notes on 45 seeded random drawings
   (exact-tie lattices, gate-boundary gaps, duplicates, nesting, every glyph and graph kind), a
   tie-only point cloud, the X11 drawing itself and the eight vendor `.chf` samples. Two mutants
   (dropping the tie-window check; halving the prefilter radius) both fail it.
   What is left of the 2.7–2.9 s is outside `ops/`: the canvas build/fit/paint ~1.0 s and the
   parse ~0.65 s.
8. **X13 — plan many contours in one array pass.** 194 s against 30 s (§2), and the item path is
   already done: what is left is the inter-contour rapid plan (55 s), `lookahead.plan_velocity`
   (30 s), the rapid stream (25 s) and `sampler.sample_plan` (18 s), all of it per-contour numpy
   *call* overhead in `plan/lookahead.py`, `plan/junction.py`, `plan/scurve.py` and the rapid
   planner. Larger and riskier than task 7 — these are the files the vendor goldens pin — so do it
   after task 7, and keep every golden byte-identical.
9. **Done (2026-09-18): the audit log and the simulator's request list are bounded.**
   `SafeMccClient.write_log` is a `mcc.safety.WriteLog`: every non-FIFO write and every refusal
   (FIFO frames included) is kept in full, the newest 32 sent/failed `0x66` frames in full, and
   older ones are compacted to a `FifoDigest` (frame id, word count, CRC32 of the words as sent
   and of the encoded frame, frame length, first three opcodes). Each part has a cap
   (`WriteLogLimits`: 10 000 other records, 32 full frames, 20 000 digests); iteration, `[i]`
   and slicing see one sequence in write order, and `compacted` / `dropped_fifo` /
   `dropped_other` / `summary()` say what was trimmed. `WriteRecord.laser_items` now carries the
   laser-record count of a `LASER_ARMED` frame — the counter D13 §4 asks for. The daemon no longer
   trims the log. Measured with the default caps: **13.05 MB after 24 000 frames and 13.05 MB after
   48 000** (flat; a list of full records would be ~580 MB), ~14 µs per appended frame including
   the compaction. `CardSimulator.requests` is a `RequestLog` ring (`SimConfig.request_log_limit`,
   default 20 000, `None` = unbounded, `requests.dropped` counts), which still supports
   `len`/iteration/`[i]`/`[a:b]` and iterates a snapshot. Tests: `tests/test_bounded_logs.py`
   (11), written first; the 321 tests that read either log pass unchanged.
10. **The correctness and hygiene backlog.** Six of the eight items were done on 2026-09-18;
    two are still open:
    * **Open — re-run the under-load sweep** on this tree (§6): the last one was the previous
      phase's 1 571 tests. Not done in the 2026-09-18 pass because another agent was running
      performance measurements on the same four cores at the time, and a sweep that loads every
      core would have corrupted both.
    * **Open — `plan/lookahead.format_veldecc()`** writes a line per node; the vendor writes one
      only for a node whose piece speed factor is below 0.99 (A9 §1). Documented at the function,
      not fixed; left alone on 2026-09-18 because `plan/lookahead.py` was being changed by the
      performance work at the same time.
    * **Done — `nexcut-mccd run-job` retries `start_job`** (`cli._start_job`) while the daemon
      refuses it with exactly `busy: axis status not refreshed since the last motion command`
      (`daemon.STALE_POLL_REFUSAL`), for at most `START_JOB_RETRY_S` = 1.0 s (~11 periods of the
      90 ms 2000/50 poll); every other refusal — MOVING, homing, arming (D9), a missing job — is
      final at once. The CLI retry was chosen over exempting `start_job` from `_require_ready`: the
      exemption would let a FIFO program start on a READY read taken *before* the last motion
      write, which is the D8 check the gate exists for. Tests:
      `tests/test_mccd_job.py::test_cli_run_job_twice_in_quick_succession` (two back-to-back runs,
      then the refusal forced by a motion timestamp 0.3 s in the future — failed before the fix)
      and `test_cli_start_job_retries_only_the_stale_poll_refusal`.
    * **Done — Save warns when values are outside their range.** `LayerFileBar.save` and
      `ParamPage.save` write the file, then show `param_pages.out_of_range_message()` ("N values
      outside their range (saved anyway): …", the first three named) in the status / problem label
      and keep it as `last_save_warning`. Never blocking (U3). Writing the tests found a D15
      defect: building a page over a bool row stored as `2` rewrote it to `1` without an edit,
      because the checkbox handler compared the check state with the raw value
      (`PropertyGrid._on_item_changed`); fixed. The descriptor defaults of `BkManuPara` already
      carry 2 out-of-range values (`GRP.EdgeBoardSizeX/Y` = 5 against a minimum of 50), so a
      Save of a default manu document warns. Tests: `tests/test_ui_save_range_warning.py` (7, one needs `src_dir`).
    * **Done — the footer lines are data and checked.** `tui.FOOTER_LINES` holds the two lines;
      `tests/test_mccd_cli_tui.py::test_every_footer_key_is_in_the_key_table` parses every key they
      name, resolves it with `binding_for(key, MODE_NORMAL)`, and also checks that every
      normal-mode and global action appears on the footer; a second test proves the parser flags a
      key that is not in `KEY_BINDINGS`.
    * **Done — `LayerLaser.laser_off_before_ms` / `laser_off_after_ms`** from
      `LaserOffBeforeDelay` / `LaserOffAfterDelay` (pd137/pd138, default 0), passed by `build_job`
      into `ContourLaser`'s two epilogue wait records. The CO2 layer layout has no such attributes,
      so every vendor golden stays byte-identical (150 plan tests re-run). Test:
      `tests/test_plan_items_cli.py::test_laser_off_delays_reach_the_stream`.
    * **Done — the two splice-worded tests are renamed**
      `test_committed_splice_0x39_0x3a_has_14_stationary_ticks_after_do9_on` and
      `test_frame_0x3a_is_mid_ramp_kinematics_not_a_contour_start` (helper `_splice_cumulative_x`).
    * **Done — the session H consumers.** `session_h_dir` in `tests/conftest.py`
      (`NEXCUT_SESSION_H`, default `~/mlaser-captures/session-h`, skip when absent) and
      `tests/test_session_h.py`: the eight `SortType` goldens, the vendor-written `.chf` / `Bk*.xml`
      / technology files as byte-identity samples (the technology one is the `CutFreq` verdict),
      and the X7 verdict, each skipping when its file is absent (14 skipped today), plus 4 tests
      that run the same consumers on synthetic artefacts. The exact file layout is
      `docs/WINE-SESSION-H.md` §2.1. Run once end to end against a synthetic `session-h/` built from
      the package's `autosave.chf` and `BkLayerPara.xml`: 18 passed.

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
| `start_job` for a second job right after the first is refused until the next 2000/50 poll (~90 ms) lands | `tests/test_mccd_job.start_job()` retries while the daemon answers `busy`, which is what a real client has to do. Since 2026-09-18 `nexcut-mccd run-job` retries that one refusal for up to 1 s as well (§5 task 10) |

Under that sweep the suite was run three times end to end with every core busy and once under
`pytest-xdist -n 4`, plus five repeats of the sixteen timing-sensitive files. The under-load
slowdown those runs saw was 2.3x on the pure-interpreter calibration and up to 3.2x on the Qt +
file-I/O paths.

The integration sweep of 2026-09-16 repeated it on the merged tree with four spin loops on four
cores: the full suite **1571 passed, 3 xfailed** twice (332.08 s and 332.19 s against 212.8 s
idle, 1.56x), and `tests/test_mccd_*.py` + `tests/test_perf_*.py` **186 passed, 1 xfailed** five
times (114.50 / 116.69 / 114.14 / 116.89 / 116.26 s). Nothing flaked in those seven runs after
the three fixes in the table above.

### What the 2026-09-16 integration sweep changed

The four parallel tasks of this phase landed in one tree; the integration pass re-ran everything,
swept it under load again and fixed what moved. Three defects, all of them in the "measures the
runner, not the port" family except the last, which was a real race in the simulator:

| Finding | Fix |
|---|---|
| Three wall-clock budgets in `tests/test_import_ui_fidelity_review.py` were still raw `assert elapsed < 3.0` (`test_import_of_many_circles_is_fast`, and `test_nearest_sort_is_not_quadratic` twice) while every other budget in the file went through `budget_s()`. They passed idle and failed with the cores busy | They go through `budget_s()` now and print their raw seconds next to the scaled budget, like the rest of the file |
| `tests/test_mccd_job.py::test_a_starvation_alarm_at_the_end_of_the_drain_still_counts_as_done` failed once in a full CI-mode run: `drain_starvation_alarm` was `False`. **Not a test bug** — `CardSimulator._advance` raised the FIFO starvation alarm only when it had tick *budget left over* after emptying the queue, so whether the card alarmed at all depended on a status read landing inside the one 0.2 ms tick in which the queue emptied exactly | The simulator alarms when the running queue is empty after a step, which is what the card does ("FIFO empty at the instant it consumes the last item", 11 §7 step 8) and what `mccd/feeder._note_depth` already documents. Deterministic, and one tick earlier than before |
| `ui/app.py` built `MainWindow` without `param_paths`, so Save in the five M3 docks asked for a path even when `--layer/--manu/--hard` had named one — an integration gap between the new pages and the CLI that no test covered | `app.main` passes the three paths through; `tests/test_ui_main_window.py::test_app_main_hands_the_parameter_paths_to_the_docks` pins it |

### What this report's runs showed (2026-09-16, third review pass)

Everything in §0 was re-run for this report on an idle laptop, one thing at a time, so no gate
number is contaminated by another gate: the full suite with the vendor package (**1676 passed,
3 xfailed, 224.44 s**), the same suite in CI mode (**1540 passed, 136 skipped, 3 xfailed,
212.00 s**), `ruff` (**All checks passed!**), then `tests/test_perf_planner.py`, the 120-second
form of `tests/test_perf_streaming.py` and the import budgets of
`tests/test_import_ui_fidelity_review.py` separately. Nothing flaked in any of them.

**What was *not* re-run: the under-load sweep.** The numbers in §0's third row are the previous
phase's tree, 172 tests ago. The three review suites added since (`test_plan_vectorisation_review`,
`test_io_dxf_stream_review`, `test_ui_safety_review`) contain no sleeps, no wall-clock budgets and
no periodic-event assertions — they are pure computation, Qt-offscreen widget work and subprocess
calls under the existing 120 s timeout — so nothing in them *should* be load-sensitive. "Should" is
not "was measured", which is why the sweep is an explicit item in §5 task 10.

### One flake to watch

`tests/test_m1_session.py::test_full_session_all_steps_on_sim` failed once in the previous phase, in
a full run that shared the laptop with three other live pytest processes, and then passed 15/15 in
isolation and at file level. It failed once more during this phase, in a run one reviewer made while
another agent was writing `src/nexcut/plan/items.py` and the test files in the same tree — an
explanation, not a diagnosis. It passed in every run made for this report, including both full
suites. `tools/m1_session.py` does not touch `JobFeeder`, so it is unrelated to the feeder changes —
but it remains the one test in the suite that has failed without a diagnosed cause, and it should be
watched rather than assumed benign.

`.github/workflows/ci.yml` runs `pytest -q -p no:cacheprovider --durations=15`, so a slow runner
names the tests that ate the time instead of just timing out.
