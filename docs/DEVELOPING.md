# Developing nexcut

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,gui]'
```

Python >= 3.12 is required (3.12, 3.13 and 3.14 run in CI; the owner's venv is 3.14).

Extras (`pyproject.toml`):

| Install | Pulls in | Needed for |
|---|---|---|
| `pip install -e .` | numpy, scipy, ezdxf | file readers/writers (`io`), planner (`plan`), daemon (`mccd`) |
| `pip install -e '.[gui]'` | + PySide6 | the viewer `nexcut` and `nexcut render` |
| `pip install -e '.[dev]'` | + pytest, ruff, ruckig | tests and lint (`ruckig` is only a reference in `tests/test_plan_scurve.py`) |

Tests that need an extra that is not installed skip (`pytest.importorskip`). scipy is a declared
runtime dependency: `ops/sort.py` and `ops/import_gates.py` import `scipy.spatial.cKDTree` at
module level, and `model/flatten.py`, `ui/scene.py` and `ui/render.py` import `scipy.interpolate`
/ `scipy.ndimage` lazily inside the functions that need them.

If `python3 -m venv` fails with "ensurepip is not available" (Debian/Ubuntu
without `python3-venv`, and no root to install it), bootstrap pip by hand:

```sh
python3 -m venv --without-pip .venv
curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
.venv/bin/python -I /tmp/get-pip.py
.venv/bin/pip install -e '.[dev,gui]'
```

Headless machines (CI, SSH) need the system libraries Qt links against and the offscreen
platform plugin:

```sh
sudo apt-get install -y libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1 libx11-6
export QT_QPA_PLATFORM=offscreen
```

After pulling changes to `pyproject.toml` (new dependencies or console scripts), re-run
`.venv/bin/pip install -e '.[dev,gui]'`.

## Running the programs

Safety first (PORT-PLAN §8, docs/DECISIONS.md): nothing below talks to the real card unless you
pass its address with `--card-ip`. The default card address is loopback, `--sim` uses the
in-process simulator, and no program arms motion by itself. Laser emission is not reachable in
this phase: the planner writes files only and the safety gate strips laser records.

### Driver daemon on the simulator

```sh
.venv/bin/nexcut-mccd serve --sim -v                 # terminal 1: daemon + card simulator
.venv/bin/nexcut-mccd status                         # terminal 2: one status snapshot
.venv/bin/nexcut-mccd status --json
.venv/bin/nexcut-mccd jog X 5 --arm --speed 20       # arm, 5 mm step jog, disarm (one connection)
.venv/bin/nexcut-mccd home X --arm
.venv/bin/nexcut-mccd stop                           # stop / estop / ack-estop / disarm
.venv/bin/nexcut-mccd ctl status                     # any IPC request, raw JSON reply
```

The IPC socket defaults to `$XDG_RUNTIME_DIR/nexcut/mccd.sock`; pass `--socket PATH` to every
command to run several daemons side by side. Configuration: `$XDG_CONFIG_HOME/nexcut/config.toml`
(or `--config`); a non-loopback `card.ip` in the file alone is refused (exit 2) - the real card is
used only as `nexcut-mccd serve --card-ip <IP>` (see `docs/M1-BENCH-SESSION.md` before doing that).

**Arming lives and dies with the connection that asked for it** (`docs/DECISIONS.md` D9). A bare
`nexcut-mccd arm` therefore disarms again the instant the process exits, and says so on stderr;
use `jog --arm` / `home --arm` (which arm, move and disarm inside one connection) or hold a
connection open with the TUI or `tools/m1_session.py`. The same rule kills a running job.
One daemon per card: `serve` takes an advisory `flock` keyed by the card address (D12), so a
second daemon pointed at the same card exits with status 1 and names the holder's pid instead
of fighting the first over the FIFO. Two `--sim` daemons do not collide (each simulator gets its
own ephemeral port), so `--socket` is all that is needed to run several side by side.

### Job streaming into the simulator (dry run)

```sh
.venv/bin/nexcut-plan job.chf --layer-xml BkLayerPara.xml --hard-xml BkHardPara.xml -o frames.txt
.venv/bin/nexcut-mccd serve --sim -v                 # terminal 1
.venv/bin/nexcut-mccd run-job frames.txt --arm       # terminal 2: arm, stream, disarm
.venv/bin/nexcut-mccd job-status --json              # while it runs, from a third terminal
.venv/bin/nexcut-mccd ctl pause_job                  # pause / start_job / stop_job
```

`run-job` hands the frame file to the daemon (`load_job`), which fills the card FIFO through
`mcc/fifo.FifoFeeder` under reg 1015/1016 flow control, starts the program with `0x67 <- [2]`,
and always ends with `0x67 <- [3]` + `0x67 <- [1]` so nothing is left queued in the card.
It is a **dry run**: laser records are stripped when the file is planned, again when the daemon
loads it, and a third time by the safety gate on the way out (PORT-PLAN §8.2); there is no way to
arm the laser in this phase (D5). Against `--sim` nothing but the in-process simulator sees it.

### Operator TUI

```sh
.venv/bin/nexcut-mccd tui                            # needs a running daemon
```

`m` arms, `d` disarms, arrow keys step-jog X/Y, Shift+arrow jogs continuously while the key
repeats, `h` then `x`/`y` homes, `?` shows the whole key table. **No letter key starts motion**
(`docs/DECISIONS.md` D11): curses cannot see Caps Lock, so every printable key is bound in both
cases and the vi-style `H`/`J`/`K`/`L` jogs are gone. Quitting leaves the daemon running, stops
any jog the TUI started, and disarms — the TUI's connection owns the arming (D9).
The guided M1 bench session (`tools/m1_session.py`) is a separate IPC client; rehearse it against
`serve --sim` first, as described in `docs/M1-BENCH-SESSION.md` §2.

### Viewer

```sh
.venv/bin/nexcut                                     # empty window
.venv/bin/nexcut job.dxf --lang en                   # .chf .dxf .plt .nc .txt .cnc .g
.venv/bin/nexcut job.chf --manu BkManuPara.xml --hard BkHardPara.xml
.venv/bin/nexcut job.chf --layer BkLayerPara.xml    # + the Layer parameters dock (M3 editor)
QT_QPA_PLATFORM=offscreen .venv/bin/nexcut render job.dxf -o job.png --bed --start
```

The parameter docks are schema-driven property editors over `ui/property_grid.py`: rows grouped
by the `Group.Item` half of their `lang.txt` label, display units chosen by `UN.SpeedUnit` /
`UN.AccUnit` / `UN.GasPressureUnit`, descriptor min/max validation, page cross-checks, and
UNVERIFIED rows shown read-only.

| Dock (View menu) | Module | Document | Opened by |
|---|---|---|---|
| Layer parameters, CO2 + Fiber tabs | `ui/pages/layer_co2.py`, `ui/pages/layer_fiber.py` | `BkLayerPara.xml` | `--layer` |
| Hardware | `ui/pages/param_pages.py` (`HardwarePage`) | `BkHardPara.xml` | `--hard` |
| Machining / Software / Graph rules | `ui/pages/param_pages.py` | `BkManuPara.xml` | `--manu` |
| Crafts (per-contour lead-in/out, cool points) | `ui/pages/crafts.py` | the loaded job | any job file |
| Power/frequency curve editor (inside the Fiber tab) | `ui/curve_editor.py` | `PWMCurveNodes` / `FreqCurveNodes` of the layer slot | `--layer` |

*Load* / *Save* on a layer page exchange the slot with a vendor technology preset
(`Technology/CO2/*.xml`, `Technology/Fiber/*.xml`, 02 §6.1) through `io/params.py`; the file bar
above the tabs (`ui/pages/layer_file.py`) writes `BkLayerPara.xml` itself, and the four parameter
pages save back to the file `--manu` / `--hard` / `--layer` named (`ui/app.py` passes those paths
into `MainWindow(param_paths=…)`; a page opened without one asks for a path). Every write goes
through the atomic verify-on-read-back path of `io/params.write_params`, so a vendor file is
re-emitted byte for byte unless a value actually changed.

UI strings come from the vendor `Lang/lang.txt` (`--lang-txt`, `$NEXCUT_LANG_TXT`, or
`$NEXCUT_SRC/Lang/lang.txt`); without it the built-in default labels are shown. The viewer never
contacts the controller in this milestone.

### Planner CLI (dry run)

```sh
F=$NEXCUT_SRC/File
.venv/bin/nexcut-plan job.chf --layer-xml $F/BkLayerPara.xml --hard-xml $F/BkHardPara.xml \
    -o frames.txt                                    # --manu-xml defaults to next to --hard-xml
.venv/bin/python -m nexcut.plan ...                  # same program
```

Writes register-0x66 FIFO frames as text (`40 66 <count> <id> <words...>`, the `DataEx:` style of
the vendor logs) and prints frames/ticks/duration. Duty is 0 and laser DO/PWM records are left out
unless `--laser-records` is given; the file is never sent anywhere.

The planner **streams**: `plan.__main__.stream_job()` yields one `PackedFrame` at a time and the
CLI writes each frame as it is produced (`mcc/fifo.write_frame_file` consumes the iterator lazily
and puts the totals in a footer), so a job's frames never have to fit in memory — a 100 000-contour
job is 218 M words, gigabytes as Python objects. `build_job()` is the same work with the frames
collected into a list and is what the goldens and the fidelity tests still use;
`mccd.feeder.JobFeeder` already took a lazy source. The tick → item → word → frame path itself is
numpy array work (`plan/items.carry_cells`, `plan/items.tick_words`, `mcc/fifo.FrameStream`), with
a scalar reference path kept for short runs and pinned cell-for-cell against it by
`tests/test_plan_vectorised_equivalence.py`.

### Other tools

```sh
.venv/bin/python -m nexcut.io.chf FILE.chf --json out.json --svg out.svg --check
.venv/bin/python -m nexcut.io.chf FILE.chf --rewrite out.chf       # reader -> writer round trip
.venv/bin/python -m nexcut.mcc.dissector Log/*.log --summary-only  # decode vendor logs / pcaps
tools/wine_session_h.sh --dry-run                                  # Wine session H capture kit
```

`tools/wine_session_h.sh` prepares and collects the artefacts of the vendor-tool-under-Wine
session (`docs/WINE-SESSION-H.md`, STATUS §3.6); `--dry-run` creates nothing and never writes
into the vendor package, which `tests/test_wine_kit.py` checks.

## Running the tests and linter

Run from the repository root (`/home/karstein/phobicMlaserLinux`):

```sh
.venv/bin/ruff check src tests tools/m1_session.py  # lint (CI: `ruff check . tools/m1_session.py`)
.venv/bin/ruff format --check src tests             # formatting: NOT a gate (24 files differ today)
NEXCUT_SRC=/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52 \
    QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q   # full suite incl. golden tests against SRC
NEXCUT_SRC=/nonexistent QT_QPA_PLATFORM=offscreen \
    .venv/bin/pytest -q                             # what CI sees (SRC-dependent tests skip)
.venv/bin/pytest -q tests/test_mcc_simulator.py     # one file
```

Expected on the owner's machine (2026-09-18, Python 3.14, 1 736 tests in 68 files, measured
while another test run shared the laptop): `1734 passed, 2 xfailed in 298.04s` with `NEXCUT_SRC`
set (or unset: the fixture default is the owner's copy); with `NEXCUT_SRC=/nonexistent`, which is
what CI does, `1590 passed, 144 skipped, 2 xfailed in 290.58s` — the SRC-dependent tests skip
instead. (The last every-core-busy run, on the 1 574-test tree of 2026-09-16, was
`1571 passed, 3 xfailed in 332s`.) The 2 strict xfails (X7 in `tests/test_io_fidelity_review.py`,
X13 in `tests/test_perf_planner.py`) are documented gaps listed in `docs/STATUS.md` §2, not
regressions — X6 was closed by decision D14 and X11 by STATUS §5 task 7. The UI tests need `QT_QPA_PLATFORM=offscreen` when no display is
available.

### Performance gates (PORT-PLAN §8.3)

Two files measure the §8.3 gates and print their numbers with `-s`. Both run a short version
inside the normal suite; the gate-sized run is an environment variable away:

```sh
# streaming jitter: queue never below FifoAlarmNum = 30 items, p99 < 100 ms, max < 500 ms
.venv/bin/pytest -q -s tests/test_perf_streaming.py                       # 8 s job (suite default)
NEXCUT_JITTER_SECONDS=600 .venv/bin/pytest -q -s tests/test_perf_streaming.py   # the gate, ~20 min
# planner throughput: 100 k contours in < 30 s (currently a strict xfail, X13)
.venv/bin/pytest -q -s tests/test_perf_planner.py
NEXCUT_PLAN_CONTOURS=1000 .venv/bin/pytest -q -s tests/test_perf_planner.py     # bigger sample
NEXCUT_PLAN_MEM_CONTOURS=100000 .venv/bin/pytest -q -s tests/test_perf_planner.py \
    -k memory                                                 # the streaming-memory measurement
```

Measured on the owner's laptop on 2026-09-16, idle, by the integrator of this phase (rerun the
commands above to reproduce; `docs/STATUS.md` §0 keeps the authoritative record):

| Gate | Measurement |
|---|---|
| planner, 250 contours | 2.00 ms/contour, 2.97 µs/tick → **200 s** projected for 100 k, budget 30 s |
| planner, 1 000 contours | 1.95 ms/contour, 2.73 µs/tick → **195 s**; 675–716 ticks/contour at every size, so the extrapolation is linear |
| planner memory, 300 contours | streaming **+0.0 MB** RSS against **+18.1 MB** for the same job materialised as frames |
| jitter, 250 µs tick, 120 s job | 4 848 frames, DONE, 0 re-sends, 0 starvation, queue low water 4 552 items; interval p50 27.7 / p90 32.8 / p99 36.3 / max 46.5 ms |
| jitter, 1 ms tick, 120 s job | 1 212 frames, DONE, 0 re-sends, 0 starvation, queue low water 4 696 items; p50 91.0 / p90 121.9 / p99 125.2 / max 134.7 ms (cadence 99.0) |

The 120 s runs above are the short form; the 600 s numbers behind the `JITTER-GATE` marker in
`docs/STATUS.md` §0 are the gate as PORT-PLAN §8.3 words it.

The jitter test runs `nexcut-mccd` and `CardSimulator` in one process and reads the *producer*
stall out of the frame intervals: at a 1 ms simulated tick a frame is 99 ms of machine time, so
the §8.3 limits are applied to the interval *minus* that cadence there, and literally at the
250 µs tick. Results are recorded in `docs/STATUS.md` §0 behind the `JITTER-GATE` /
`PLANNER-GATE` comment markers.

**Wall-clock budgets are scaled to the machine.** `tests/conftest.py` times a fixed pure-Python
workload once per session and divides by the reference measured on the review laptop
(`CALIBRATION_REFERENCE_S`); `speed_factor()` is that ratio, never below 1.0, with the excess
over 1.0 carrying a `LOAD_HEADROOM` of 1.5 because the budgets also cover Qt rasterisation and
file I/O, which degrade faster than pure interpreter work when the cores are contended. The 3 s open/render
budgets and the jitter gate's 100 ms / 500 ms percentile limits are multiplied by it, so the gate
is exact on a machine of the review machine's speed and proportional on a slower or busier one —
a budget can never be relaxed on a *fast* machine. Everything that is a fact about the stream
rather than about the clock (job DONE, every tick consumed, no re-send, no starvation alarm, the
queue never below `FifoAlarmNum`) stays unscaled. Every one of these tests prints its raw seconds
next to the scaled budget, so `-s` still shows the real number.

### Writing tests that survive CI

GitHub runners are slower and noisier than the development laptop, and the suite starts real
threads (daemon, IPC server, card simulator, feeder). Rules that the timing-sensitive files
follow, and that a new test must follow too:

* Never `sleep(t)` and then assert that something *has* happened - poll a condition with a
  generous deadline (`wait_for(...)` in `tests/test_mccd_job.py` and
  `tests/test_mccd_daemon.py`, or a `join()` on the thread that does the work). Sleeping longer
  than intended is the normal failure mode under load, and a wait-for-condition is immune to it.
* Never assert that something has *not* happened yet after a short sleep: under load the thing
  you were racing has already finished. Where a test needs a window (a move still busy, a lease
  still held), make the window long enough to survive a 3x slower machine - `SimConfig
  (motion_time_s=...)` and the lease timeouts exist for exactly that.
* Never assert that a periodic event happened within less than a few of its periods.
* Distinguish "the work is finished" from "the thread has left". A job's `DONE`
  (`JobFeeder.finished`) means the producer has no more frames; the closing `0x67` pair and the
  token hand-back happen afterwards (`JobFeeder.closed`, `join()`), and the simulated card may
  still be executing items it already holds. Wait for the thing you are about to assert on.
* Every wall-clock budget goes through `budget_s()` / `speed_factor()`. A raw `assert elapsed <
  3.0` measures the runner; three of them survived in
  `tests/test_import_ui_fidelity_review.py` until the 2026-09-16 integration sweep and failed
  under load while every scaled budget in the same file passed.
* If an assertion depends on *when* a poll lands relative to a simulated event, fix the
  simulator, not the deadline. `CardSimulator._advance` used to raise the FIFO starvation alarm
  only when it had tick budget left over after emptying the queue, so
  `test_a_starvation_alarm_at_the_end_of_the_drain_still_counts_as_done` passed or failed
  depending on whether a status read landed inside the one tick in which the queue emptied
  exactly. The card reports "FIFO empty" the instant it consumes the last item (11 §7 step 8),
  which is both more faithful and deterministic.

To reproduce CI conditions locally, load every core and run without the cache plugin:

```sh
for i in $(seq $(nproc)); do .venv/bin/python -c "
import time
end=time.monotonic()+900
while time.monotonic()<end: pass" & done
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q -p no:cacheprovider
wait
```

Always bound the busy loops (the `+900` above) and kill them when the sweep ends: an orphaned
spin loop silently corrupts every timing gate in the suite for whoever runs it next. The sweep
the project runs before landing a phase is that loop plus **two full suites and five repeats of
the timing-sensitive files** (`tests/test_mccd_*.py`, `tests/test_perf_*.py`).

`pytest-xdist` is not a declared dependency; `pip install pytest-xdist` then
`pytest -q -p xdist -n 4` also works and is a useful shake-out for order and isolation bugs —
the suite passed under it (`1302 passed, 4 xfailed in 70.50s`, 2026-09-16, before this phase's
tests landed), and it is how the
`test_r1_stop_reaches_card_after_long_comm_loss_despite_refreshing_client` ordering race was
found.

Tests that compare against the original Windows package use the `src_dir`
fixture from `tests/conftest.py`. It reads the environment variable
`NEXCUT_SRC` (default: `/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52`)
and skips the test when the directory does not exist, so CI stays green
without the proprietary files. Files next to the package (the vendor DXF, `pwmCompensation.txt`)
are reached as `src_dir.parent` and skip the same way. The package is read-only: never write
into it, and never read it at import time. Golden numbers derived from it must be copied
into the test source, never recomputed from the package at test time.

## Module dependencies

The card-protocol layers are stacked; keep constants in the lowest layer that owns them
and import upwards, never redefine:

```
crc -> framing -> registers -> commands -> safety
                     \-> dissector ----------/
framing + registers + commands + dissector -> simulator
framing -> transaction
framing + registers -> fifo
commands + safety + transaction + registers -> mccd (gate, status, daemon) -> ipc -> tui, cli
fifo + commands + safety -> mccd.feeder (job streaming) -> mccd.daemon
model (glyph, graph, flatten) -> ops.import_gates -> io (chf, dxf, plt, gcode)
io.dxf_stream -> io.dxf (fast path; the geometry helpers live in io.dxf and are shared)
core.schema -> io.params -> plan.params
model -> plan.contour_fit -> plan.lookahead (+ junction, scurve) -> plan.sampler
fifo + commands -> plan.items;  plan.* + io.chf + io.params -> plan.__main__ (nexcut-plan)
io + ops.import_gates + model -> ui (loader, scene, render, canvas, main_window, app)
core.schema + io.params -> ui.property_grid -> ui.pages.{layer_co2, layer_fiber, param_pages}
                                            -> ui.curve_editor, ui.pages.{crafts, layer_file}
                                            -> ui.main_window
```

* Wire encoding/decoding (`encode_vector`, `read_reply`, CRC order) lives only in
  `mcc/framing.py`; `transaction`, `simulator`, `safety` and `dissector` call it.
* Register addresses, word maps, axis-slot/DO-bit tables and the FIFO status helpers
  (`FIFO_MARGIN_EMPTY`, `next_frame_id`, `fifo_program_running`) live in `mcc/registers.py`.
* Sub-command numbers (`CMD_*`, `MISC_*`, `FIFO_*`, `ABSOLUTE_BIT`) live in
  `mcc/commands.py`; the simulator, the safety layer and `plan/items.py` (`SUB_*`) import them.
* Reg 1016 (FIFO space margin) is *bytes free, 60000 = empty* everywhere (11 C2): `mcc/fifo.py`
  flow control, the simulator (default `SimConfig.fifo_space_unit="bytes"`), `mccd/status.py`.
* Nothing under `plan/`, `io/` or `ui/` imports a transport (`mcc.transaction`, `mcc.safety`,
  `mccd`); `tests/test_plan_items_cli.py` checks this for the planner.
* Only `mccd/feeder.py` writes register 0x66 and the `0x67` program controls in the port's
  live path; it goes through the safety gate like every other write, and the daemon is what
  owns a feeder (`MccDaemon.load_job_frames`).
* `io/chf.py` reads and writes the `nexcut.model` dataclasses; it has no private model. Bytes it
  cannot model are kept rather than dropped: a non-empty v2-v4 reserved line goes into
  `ChfDocument.legacy_reserved` and is re-emitted verbatim (D14, closes X6).
* `io/dxf_stream.py` is a parser only. It declines (`StreamUnsupported`) whenever a file needs
  the full reader — binary DXF, `INSERT`, text-to-curves, a tilted OCS, any structural anomaly —
  and `io/dxf.read_dxf(reader="auto")` then runs ezdxf; `DxfImportResult.reader` /
  `fallback_reason` record which path ran. Both readers must build the *same* `ChfDocument`,
  which is what `tests/test_io_dxf_stream.py` cross-checks on randomised corpora.
* `tests/test_integration_consistency.py` pins these relations, including an end-to-end
  planner -> safety strip -> `FifoFeeder` -> simulator run.

## Layout

Mapped 1:1 to `docs/PORT-PLAN.md` §2.3 (repository layout) and §3 (module breakdown):

```
src/nexcut/
  core/   units, parameter schema, i18n, config paths        (§3.1, §3.2)
  model/  glyph/graph model, .chf document, layers, crafts   (§3.3)
  io/     chf, dxf, plt, gcode, technology xml, report, state (§3.3, §3.5)
  ops/    sort, lead-in, micro-joint, offset, scan-fill, nest (§3.3)
  plan/   contour_fit, junction, lookahead, scurve, sampler, items; `nexcut-plan` (§3.3)
  mcc/    card protocol: framing, transaction, registers, fifo, simulator, dissector (§3.4)
  mccd/   driver daemon, IPC, job feeder, TUI; console script `nexcut-mccd` (§2.3, §3.4)
  ui/     PySide6 viewer + property pages (§3.1, M2/M3); console script `nexcut`
tests/    pytest suite; conftest.py holds the SRC fixture
tools/    standalone scripts (m1_session.py bench session, wine_session_h.sh, chf_parse.py,
          token_usage.py); only m1_session.py is linted (pyproject excludes tools/)
docs/     PORT-PLAN.md, analysis/, this file
.scratch/ gitignored scratch space (disassemblies etc.)
```

## Conventions

* Every behaviour cites the analysis document section it comes from in its
  docstring, e.g. `"04 §3.2"`.
* Any constant not proven by evidence carries an `UNVERIFIED` comment.
* Stdlib first; allowed third-party: numpy, scipy, ezdxf (runtime), PySide6 (`gui` extra),
  pytest, ruff, ruckig (`dev` extra). A new dependency goes into `pyproject.toml` and this file.
* `ruff` configuration lives in `pyproject.toml` (line length 100, isort rules on).
