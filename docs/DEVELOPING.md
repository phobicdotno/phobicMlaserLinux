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
runtime dependency for the planner milestones; the code in `src/` does not import it yet.

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
.venv/bin/nexcut-mccd arm                            # explicit arming step, then e.g.
.venv/bin/nexcut-mccd jog X 5 --speed 20 --wait      # relative 5 mm step jog (simulated)
.venv/bin/nexcut-mccd home X --wait
.venv/bin/nexcut-mccd stop                           # stop / estop / ack-estop / disarm
.venv/bin/nexcut-mccd ctl status                     # any IPC request, raw JSON reply
```

The IPC socket defaults to `$XDG_RUNTIME_DIR/nexcut/mccd.sock`; pass `--socket PATH` to every
command to run several daemons side by side. Configuration: `$XDG_CONFIG_HOME/nexcut/config.toml`
(or `--config`); a non-loopback `card.ip` in the file alone is refused (exit 2) - the real card is
used only as `nexcut-mccd serve --card-ip <IP>` (see `docs/M1-BENCH-SESSION.md` before doing that).

### Operator TUI

```sh
.venv/bin/nexcut-mccd tui                            # needs a running daemon
```

`m` arms, `d` disarms, arrows step-jog X/Y, Shift+arrow or H/J/K/L jog continuously while the
key repeats; the key map is shown on screen. Quitting leaves the daemon running and stops any jog the TUI started.
The guided M1 bench session (`tools/m1_session.py`) is a separate IPC client; rehearse it against
`serve --sim` first, as described in `docs/M1-BENCH-SESSION.md` §2.

### Viewer

```sh
.venv/bin/nexcut                                     # empty window
.venv/bin/nexcut job.dxf --lang en                   # .chf .dxf .plt .nc .txt .cnc .g
.venv/bin/nexcut job.chf --manu BkManuPara.xml --hard BkHardPara.xml
QT_QPA_PLATFORM=offscreen .venv/bin/nexcut render job.dxf -o job.png --bed --start
```

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

Expected on the owner's machine (2026-09-15, Python 3.14): `1108 passed, 4 xfailed` with
`NEXCUT_SRC` set (or unset: the fixture default is the owner's copy); without the vendor package
`1000 passed, 108 skipped, 4 xfailed`. The 4 xfails in `tests/test_io_fidelity_review.py` are
documented fidelity gaps, not regressions. The UI tests need `QT_QPA_PLATFORM=offscreen` when no
display is available.

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
model (glyph, graph, flatten) -> ops.import_gates -> io (chf, dxf, plt, gcode)
core.schema -> io.params -> plan.params
model -> plan.contour_fit -> plan.lookahead (+ junction, scurve) -> plan.sampler
fifo + commands -> plan.items;  plan.* + io.chf + io.params -> plan.__main__ (nexcut-plan)
io + ops.import_gates + model -> ui (loader, scene, render, canvas, main_window, app)
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
  mccd/   driver daemon, IPC, TUI; console script `nexcut-mccd` (§2.3, §3.4)
  ui/     PySide6 viewer (§3.1, M2); console script `nexcut`
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
