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

`--layer` opens the first schema-driven property editor (`ui/property_grid.py`,
`ui/pages/layer_co2.py`): pick a layer in the layer tree and the dock shows that layer's CO2
parameters: rows grouped by the `Group.Item` half of their `lang.txt` label, display units
chosen by `UN.SpeedUnit` / `UN.AccUnit` / `UN.GasPressureUnit`, descriptor min/max validation
plus the `lp19` cross-check, and UNVERIFIED rows shown read-only. *Load* / *Save* exchange the
slot with a vendor technology preset (`Technology/CO2/*.xml`, 02 §6.1) through `io/params.py`.
Other pages (hardware, machining, software, the fibre layer, the curve and crafts editors) are
not built yet, and the dock does not write `BkLayerPara.xml` back.

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

### Other tools

```sh
.venv/bin/python -m nexcut.io.chf FILE.chf --json out.json --svg out.svg --check
.venv/bin/python -m nexcut.io.chf FILE.chf --rewrite out.chf       # reader -> writer round trip
.venv/bin/python -m nexcut.mcc.dissector Log/*.log --summary-only  # decode vendor logs / pcaps
```

## Running the tests and linter

Run from the repository root (`/home/karstein/phobicMlaserLinux`):

```sh
.venv/bin/ruff check src tests tools/m1_session.py  # lint (CI: `ruff check . tools/m1_session.py`)
.venv/bin/ruff format --check src tests             # formatting (not enforced in CI)
NEXCUT_SRC=/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52 \
    QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q   # full suite incl. golden tests against SRC
NEXCUT_SRC=/nonexistent QT_QPA_PLATFORM=offscreen \
    .venv/bin/pytest -q                             # what CI sees (SRC-dependent tests skip)
.venv/bin/pytest -q tests/test_mcc_simulator.py     # one file
```

Expected on the owner's machine (2026-09-16, Python 3.14): `1302 passed, 4 xfailed in 186.21s`
with `NEXCUT_SRC` set (or unset: the fixture default is the owner's copy); with
`NEXCUT_SRC=/nonexistent`, which is what CI does, `1187 passed, 115 skipped, 4 xfailed in
174.12s` — the SRC-dependent tests skip instead. The 4
strict xfails (X6, X7 in `tests/test_io_fidelity_review.py`, X11 in
`tests/test_import_ui_fidelity_review.py`, X13 in `tests/test_perf_planner.py`) are documented
gaps listed in `docs/STATUS.md` §2, not regressions. The UI tests need
`QT_QPA_PLATFORM=offscreen` when no display is available.

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
```

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

To reproduce CI conditions locally, load every core and run without the cache plugin:

```sh
for i in $(seq $(nproc)); do .venv/bin/python -c "
import time
end=time.monotonic()+900
while time.monotonic()<end: pass" & done
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q -p no:cacheprovider
wait
```

`pytest-xdist` is not a declared dependency; `pip install pytest-xdist` then
`pytest -q -p xdist -n 4` also works and is a useful shake-out for order and isolation bugs —
the suite passes under it (`1302 passed, 4 xfailed in 70.50s` on 2026-09-16), and it is how the
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
core.schema -> io.params -> plan.params
model -> plan.contour_fit -> plan.lookahead (+ junction, scurve) -> plan.sampler
fifo + commands -> plan.items;  plan.* + io.chf + io.params -> plan.__main__ (nexcut-plan)
io + ops.import_gates + model -> ui (loader, scene, render, canvas, main_window, app)
core.schema + io.params -> ui.property_grid -> ui.pages.layer_co2 -> ui.main_window
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
* `io/chf.py` reads and writes the `nexcut.model` dataclasses; it has no private model.
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
tools/    standalone scripts (m1_session.py bench session, chf_parse.py, token_usage.py)
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
