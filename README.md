# phobicMlaserLinux

Native Linux reimplementation (`nexcut`, Python) of the Mlaser / NexCut laser-cutter control
software for the Gweike M3 Ultra / CF1390 machine with the MCC100 motion card.

The original Windows package is proprietary and is *not* included in this repository; the
analysis documents reference it by relative path only.

## Status

| Area | State |
|---|---|
| Analysis of the vendor binaries and logs | done - `docs/analysis/` (`11-static-findings.md` is the protocol reference) |
| M0 file formats: `.chf` read/write, parameter XML, CP936 | done, round-trip tested against the vendor samples |
| M1 card driver: framing, transactions, safety gate, simulator, daemon `nexcut-mccd`, TUI | done on the simulator; the real-machine confirmation session (`docs/M1-BENCH-SESSION.md`) is pending |
| M2 viewer `nexcut`: open `.chf`/DXF/PLT/G-code, render, layers | first version; DXF has a streaming reader that skips the ezdxf document build (~5x faster on entity-heavy files) |
| M3 parameter editing: XML round-trip, schema-driven property pages (`nexcut --layer/--manu/--hard`) | CO2 + fibre layer pages, hardware, machining, software and graph-rule pages, the PWM/frequency curve editor and the crafts editor; every write is an atomic verify-on-read-back round trip |
| Planner: contour fit, look-ahead, S-curve, 250 µs sampling, FIFO items/frames `nexcut-plan` | dry run only (writes frame files, never sends); the item path is vectorised and the job streams frame by frame |
| M4 job streaming `nexcut-mccd run-job` | dry run against the card simulator; the §8.3 jitter gate passes, the planner throughput gate does not (~200 s projected for 100 k contours against 30 s) |
| M5 live cutting | not started; there is no laser-arming command, and the safety gate strips every laser record |

Plan and milestones: `docs/PORT-PLAN.md`. Safety design: PORT-PLAN §8 and `docs/DECISIONS.md`
(D1-D14; D13, laser arming, is *proposed* and gates all of M5). Where the port stands in detail,
with every measured number and every open question: `docs/STATUS.md`.

## Quickstart

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,gui]'

.venv/bin/nexcut-mccd serve --sim          # driver daemon against the built-in card simulator
.venv/bin/nexcut-mccd tui                  # operator console (second terminal)
.venv/bin/nexcut job.dxf                   # viewer
.venv/bin/nexcut job.chf --layer BkLayerPara.xml --manu BkManuPara.xml --hard BkHardPara.xml
.venv/bin/nexcut-plan job.chf --layer-xml BkLayerPara.xml --hard-xml BkHardPara.xml -o frames.txt
.venv/bin/nexcut-mccd run-job frames.txt --arm   # stream that job (dry run, simulator)
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q
```

The suite is 1 574 tests (`1571 passed, 3 xfailed` with the vendor package present,
`1439 passed, 132 skipped, 3 xfailed` without it, which is what CI runs); the three strict
xfails are documented gaps, listed in `docs/STATUS.md` §2.

Nothing talks to a real card unless its address is given explicitly with
`nexcut-mccd serve --card-ip <IP>`, and motion always needs an explicit `arm` that dies with the
connection that asked for it.
Details, extras and the full test commands: `docs/DEVELOPING.md`.

Token usage for the project is tracked in `docs/TOKEN-USAGE.md` (regenerate with `python3 tools/token_usage.py`).
