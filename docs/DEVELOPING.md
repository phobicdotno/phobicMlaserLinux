# Developing nexcut

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Python >= 3.12 is required (3.12 and 3.13 are tested in CI).

If `python3 -m venv` fails with "ensurepip is not available" (Debian/Ubuntu
without `python3-venv`, and no root to install it), bootstrap pip by hand:

```sh
python3 -m venv --without-pip .venv
curl -sSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
.venv/bin/python -I /tmp/get-pip.py
.venv/bin/pip install -e '.[dev]'
```

## Running the tests and linter

Run from the repository root (`/home/karstein/phobicMlaserLinux`):

```sh
.venv/bin/pip install -e '.[dev]'        # after pulling changes to pyproject.toml
.venv/bin/ruff check src tests           # lint (CI runs `ruff check .`, tools/ is excluded)
.venv/bin/ruff format --check src tests  # formatting (not enforced in CI yet)
NEXCUT_SRC=/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52 \
    .venv/bin/pytest -q                  # full suite incl. golden tests against SRC
.venv/bin/pytest -q tests/test_mcc_simulator.py   # one file
```

Expected on the owner's machine (2026-09-15): `325 passed` with `NEXCUT_SRC` set;
without the vendor package (e.g. `NEXCUT_SRC=/nonexistent`, or CI) the SRC-dependent
tests skip: `281 passed, 44 skipped`.

Tests that compare against the original Windows package use the `src_dir`
fixture from `tests/conftest.py`. It reads the environment variable
`NEXCUT_SRC` (default: `/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52`)
and skips the test when the directory does not exist, so CI stays green
without the proprietary files. The package is read-only: never write into it,
and never read it at import time. Golden numbers derived from it must be copied
into the test source, never recomputed from the package at test time.

## Command-line tools

```sh
.venv/bin/python -m nexcut.io.chf FILE.chf --json out.json --svg out.svg --check
.venv/bin/python -m nexcut.io.chf FILE.chf --rewrite out.chf       # reader -> writer round trip
.venv/bin/python -m nexcut.mcc.dissector Log/*.log --summary-only  # decode vendor logs / pcaps
.venv/bin/nexcut-mccd                                              # driver daemon stub (M0)
```

## Module dependencies (mcc)

The card-protocol layers are stacked; keep constants in the lowest layer that owns them
and import upwards, never redefine:

```
crc -> framing -> registers -> commands -> safety
                     \-> dissector ----------/
framing + registers + commands + dissector -> simulator
framing -> transaction
model (glyph, graph, flatten) -> io.chf
core.schema -> io.params
```

* Wire encoding/decoding (`encode_vector`, `read_reply`, CRC order) lives only in
  `mcc/framing.py`; `transaction`, `simulator`, `safety` and `dissector` call it.
* Register addresses, word maps and axis-slot/DO-bit tables live in `mcc/registers.py`;
  `commands` takes `AXIS_SLOTS`, `do_bit`, `Status`, `SystemRW.K` from there.
* Sub-command numbers (`CMD_*`, `MISC_*`, `FIFO_*`, `ABSOLUTE_BIT`) live in
  `mcc/commands.py`; the simulator and the safety layer import them.
* `io/chf.py` reads and writes the `nexcut.model` dataclasses; it has no private model.
* `tests/test_integration_consistency.py` pins these relations.

## Layout

Mapped 1:1 to `docs/PORT-PLAN.md` §2.3 (repository layout) and §3 (module breakdown):

```
src/nexcut/
  core/   units, parameter schema, i18n, config paths        (§3.1, §3.2)
  model/  glyph/graph model, .chf document, layers, crafts   (§3.3)
  io/     chf, dxf, plt, gcode, technology xml, report, state (§3.3, §3.5)
  ops/    sort, lead-in, micro-joint, offset, scan-fill, nest (§3.3)
  plan/   contour_fit, junction, lookahead, scurve, sampler, items (§3.3)
  mcc/    card protocol: framing, transaction, registers, fifo, simulator, dissector (§3.4)
  mccd/   driver daemon process; console script `nexcut-mccd` (§2.3, §3.4)
  ui/     PySide6 application (§3.1) - not part of M0
tests/    pytest suite; conftest.py holds the SRC fixture
tools/    standalone scripts (chf_parse.py, token_usage.py)
docs/     PORT-PLAN.md, analysis/, this file
.scratch/ gitignored scratch space (disassemblies etc.)
```

## Conventions

* Every behaviour cites the analysis document section it comes from in its
  docstring, e.g. `"04 §3.2"`.
* Any constant not proven by evidence carries an `UNVERIFIED` comment.
* Stdlib first; allowed third-party in M0: numpy, pytest, ruff.
* `ruff` configuration lives in `pyproject.toml` (line length 100, isort rules on).
