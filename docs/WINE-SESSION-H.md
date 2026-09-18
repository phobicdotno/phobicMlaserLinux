# Session H — the vendor tool under Wine, at the desk

**No machine. No card. No network to the machine.** Session H answers the questions of
`docs/STATUS.md` §3.6 by driving the *vendor* program (Mlaser v0.0.0.52 / NexCut) under Wine
on this laptop and keeping what it writes. It is the single highest-yield desk task in
`STATUS.md` §5 (task 5): it unblocks the **M2 sort gate**, the **M3 "Mlaser loads
port-written files" gate** and **X7** at once, and it needs nothing but an afternoon.

Prior work and the recipe it rests on: `docs/analysis/10-wine-mlaser-prior-work.md`
(32-bit prefix, `winetricks mfc42`, run from `C:`), `docs/M1-BENCH-SESSION.md` §8 (the
`EnableLog=1` trick), `09 §3.3` / `04 §4.6` (the USB dongle).

---

## 0. Safety, and what this session may not do

The machine is not involved and **must stay uninvolved**:

* Do **not** put the laptop's wired NIC on `10.1.1.0/24` for this session. Mlaser polls the
  card as soon as it can reach it, and a vendor tool that finds the card can move it. With no
  route to `10.1.1.168` the program starts, shows "Controller connecting failed" (`mf5`), and
  every file, parameter and sorting feature still works — which is all session H needs.
* Do not start `nexcut-mccd` during this session. One master per card (D12) is about the card;
  this is about not confusing yourself about which program is talking to what.
* The laser key stays off, as always.

If you *do* want the vendor tool on the card (that is the M1 bench session's step 8/9, a
different session), follow `docs/M1-BENCH-SESSION.md` §8 instead — it has the hand-over
protocol.

---

## 1. Prepare the prefix

```sh
cd ~/phobicMlaserLinux
tools/wine_session_h.sh --dry-run     # prints the plan, changes nothing
tools/wine_session_h.sh               # does it; safe to re-run
```

What it sets up, and why (all of it is idempotent and verified after each step; a run that was
interrupted during the ~103 MB copy is recovered on the next run rather than nested inside itself,
and the script no longer needs `$USER` to be exported):

| Step | What | Why |
|---|---|---|
| 1 | working copy `~/mlaser-wine/Mlaser` of the package | the package under `~/Documents/CF1390-…/Mlaser-v0.0.0.52` is **read-only evidence**. Mlaser writes into its own directory (`File/*`, `Log/*`, `Graph/*`, `Dump/*`) the moment it starts. The script refuses any write path that resolves inside the original |
| 2 | `WINEPREFIX=~/.wine-mlaser`, `WINEARCH=win32`, `wineboot -u` | MainApp.exe is PE32; a 64-bit prefix cannot run it (10, fix 1) |
| 3 | `winetricks -q mfc42` | `Module\CADModule.dll → AutoNest.dll → Dxf2Grp.dll → MFC42.DLL`, a VC6-era import Wine does not provide (10, fix 2) |
| 4 | symlink `drive_c/Mlaser` → the working copy | the app must run from `C:`: it creates `\Technology\…` with a leading backslash, and from `Z:` (= `/`) that fails with "Failed to create Technology folder" (10, fix 3) |
| 5 | `drive_c/Technology/{Fiber,CO2}` | the per-source technology tables live there (10). Note the open question in H-5: `io/params.technology_dir` currently expects them under `%LOCALAPPDATA%\NexCut` instead |
| 6 | `EnableLog=1` in the **working copy's** `File/ipAdd.ini` `[Soft]` | NCModule then logs `Send Cmd:/Recv Cmd:` with timings to `drive_c/users/$USER/AppData/Local/NexCut/Log/<date>.log` (04 §9). Useful even with no card: the start-up sequence is logged |
| 7 | writes `~/mlaser-wine/99-mlaser-dongle.rules` and **prints** a `pkexec` command | `/dev/hidraw*` is root-only, so Wine cannot see the dongle (USB HID `3689:8762`). Installing a udev rule needs root, and this script never elevates — you run the printed command, and the password dialog appears in the graphical session |

The dongle gates **auto-nesting only** (`Nest_CheckLock`, 09 §3.3, 04 §4.6). Everything session
H needs — files, parameters, sorting, lead lines, compensation — works without it. Install the
rule only if you also want to try nesting.

Start the program:

```sh
cd ~/.wine-mlaser/drive_c/Mlaser && WINEPREFIX=~/.wine-mlaser wine MainApp.exe
```

Optional privacy: blank `MonitorIP` in the working copy's `ipAdd.ini`, or switch Wi-Fi off.
Mlaser reports to `47.104.17.21:9001` every 5 minutes (10, "Telemetry").

---

## 2. Where the artefacts go

```
~/mlaser-captures/session-h/
├── h1-sort/        the sorted jobs (H-1)
├── h2-m3-gate/     port-written files, and what Mlaser made of them (H-2)
├── h3-x7/          the unknown-attribute round trip (H-3)
├── h4-crafts/      lead-in / compensation / cool-point variants (H-4)
├── h5-misc/        imports, scan, i18n, screenshots (H-5)
├── vendor-log/     drive_c/users/$USER/AppData/Local/NexCut/Log/<date>.log, copied at the end
└── notes.md        one section per question: what you clicked, what you saw
```

Rules for every artefact:

1. **Save the "before" too.** Every question is a *diff*: the port needs the input and the
   output. A file the vendor re-saved is worthless without the file that went in.
2. **Never overwrite.** Name files `<question>-<step>-<what>.chf`, never `test.chf` twice.
3. Copy out of the prefix, not out of the original package. `drive_c/Mlaser/...` is the
   working copy; `~/Documents/CF1390-…` must end the session byte-identical.
4. Write down what you *saw* even when nothing was saved — "Mlaser refused to open it with
   message X" is the answer to half of these questions.

The port side reads them through the `session_h_dir` fixture in `tests/conftest.py`, next to
`src_dir`: `NEXCUT_SESSION_H` (default `~/mlaser-captures/session-h`), skipping when the
directory is absent, so CI stays green.

### 2.1 What the port's tests read (exact names)

`tests/test_session_h.py` consumes the artefacts below. Each test skips on its own when the
file it needs is missing, so a partly-run session still feeds every test it can; with no
directory at all the file reports 14 skipped. **Use these names exactly** — a file saved under
another name is invisible to the tests.

| Path under `session-h/` | Test | What it asserts |
|---|---|---|
| `h1-sort/h1-00-unsorted.chf` | `test_vendor_sort_golden[*]` (input), `test_vendor_written_chf_is_rewritten_byte_identically` | the "before" of every sort; re-written by the port byte for byte |
| `h1-sort/h1-00-left-to-right.chf` … `h1-07-small-first.chf` (the eight names of the H-1 table) | `test_vendor_sort_golden[LEFT_TO_RIGHT … SMALL_FIRST]` | the vendor's graph order, matched back to `h1-00-unsorted.chf` by bounding box, equals `ops.sort.sort_graphs(unsorted, SortType.N).order` |
| `h1-sort/*.chf`, `h4-crafts/*.chf` (any name) | `test_every_session_h_chf_is_rewritten_byte_identically` | every vendor-written `.chf` survives `read_chf` → `write_chf` unchanged; the failures are listed by file |
| `h2-m3-gate/h2-vendor-resaved-autosave.chf` | `test_vendor_written_chf_is_rewritten_byte_identically` | as above |
| `h2-m3-gate/h2-vendor-resaved-BkLayerPara.xml` | `test_vendor_resaved_params_round_trip` | `serialize_params(parse_params(file, "layer"))` is the file |
| `h2-m3-gate/h2-vendor-resaved-technology-CO2.xml` | `test_technology_preset_matches_vendor_attribute_set` | `serialize_technology(parse_technology(file))` is the file — fails, by name, if the vendor dropped `CutFreq` (the H-2 step 3 verdict) |
| `h3-x7/h3-tampered.xml` + `h3-x7/h3-after.xml` | `test_x7_port_keeps_unknown_attributes_exactly_when_the_vendor_does` | `NexcutProbe="42"` survives the port's rewrite of `h3-tampered.xml` exactly when it is in `h3-after.xml` |

Some of these may **fail** on the first real run, and then the failure is the finding: a sort
golden fails wherever `ops/sort.py` orders differently from the vendor; the technology test
fails if the vendor re-save drops `CutFreq`; the X7 test fails if the vendor *keeps* the probe
(the port drops it today). If the vendor drops the probe too, the X7 test passes and the strict
xfail `test_unknown_attribute_survives_rewrite` has to be rewritten by hand as a fidelity
statement citing `h3-after.xml`. The consumers themselves are checked on synthetic artefacts in
the same file (and were run once end to end against a synthetic `session-h/` built from the
package's `autosave.chf` and `BkLayerPara.xml`: 18 passed), so a failure is about the data,
not the test.

---

## H-1 — The M2 sort reference (`GRP.SortType=4` and friends)

**Why.** PORT-PLAN §4 M2 asks that "a 24-contour job sorts like `ManuContour.dat` with
`GRP.SortType=4`". That gate is **unmeetable as written** (`STATUS.md` §1.3): `ManuContour.dat`
is `24, 0..23`, the array-copy order of `autosave.chf` (`GRP.Array*`), not a sort result — no
sort type reproduces it, and no shipped sample holds a *sorted* job. So `ops/sort.py`'s eight
strategies are, today, checked against nothing. One vendor-sorted file per strategy replaces
the gate's reference.

**What to click.**

1. `File ▸ Open` (`mf7`/`mf9`) → `C:\Mlaser\File\autosave.chf` (24 contours, the sample the
   gate names).
2. Turn on `View ▸ Show index` (`mf40`) and `Show path start` (`mf41`) so the order is visible
   on screen; screenshot it as `h1-sort/00-loaded.png`.
3. `File ▸ Save as` (`mf11`) → `C:\Mlaser\Graph\h1-00-unsorted.chf`. **Do this before any
   sort**: this is the "before" the diff needs, in the vendor's own writer, and it also tells
   us whether merely loading and saving changes anything.
4. `Select ▸ Select All` (`mf33`).
5. `Advanced Process ▸ Sort ▸ Nearest` (`mf104 ▸ mf105 ▸ mf110`, 局部最短路径 = `SortType` 4).
6. `File ▸ Save as` → `C:\Mlaser\Graph\h1-04-nearest.chf`. Screenshot as `h1-04-nearest.png`.
7. Repeat 4–6 for every other strategy, one file each, named by the `SortType` number the port
   uses (`src/nexcut/ops/sort.py`):

   | file | menu item | `SortType` |
   |---|---|---|
   | `h1-00-left-to-right.chf` | `mf106` Left to right | 0 |
   | `h1-01-right-to-left.chf` | `mf107` Right to left | 1 |
   | `h1-02-bottom-to-top.chf` | `mf108` Bottom to top | 2 |
   | `h1-03-top-to-bottom.chf` | `mf109` Top to bottom | 3 |
   | `h1-04-nearest.chf` | `mf110` Nearest | 4 |
   | `h1-05-inside-to-outside.chf` | `mf202` Inside to Outside | 5 |
   | `h1-06-outside-to-inside.chf` | `mf203` Outside to Inside | 6 |
   | `h1-07-small-first.chf` | `mf204` Small graphics priority | 7 |

   Re-load `h1-00-unsorted.chf` before each one, so every sort starts from the same order.
8. Two extra questions worth one file each while you are here:
   * `GRP.SortIsSmallFirst` — `ops/sort.py` does not apply it because its semantics next to
     `SortType 7` are unknown. Set it in `Options` (`mf129`), re-sort with a strategy that is
     *not* 7, save as `h1-08-smallfirst-on.chf`, and note the parameter's value.
   * The nearest-sort **start point**: `ops/sort.py` starts at `(0,0)`. Move one contour far
     from the origin, sort Nearest, save as `h1-09-nearest-origin-probe.chf`.

**What to save where.** Every `.chf` above plus the screenshots into
`~/mlaser-captures/session-h/h1-sort/`, and in `notes.md` the parameter values you saw in
`Options ▸ Sort` (`GRP.SortType`, `GRP.SortIsSmallFirst`, `IGP.AutoSortType`).

**Which port test consumes it.** `tests/test_session_h.py::test_vendor_sort_golden` (§2.1) — the graph order of each file
(`####graph NO:` order, read with `nexcut.io.chf.read_chf`) becomes the golden for
`sort_graphs(doc, SortType.<N>)`, one parametrised case per row of the table, replacing
`SORT_UNVERIFIED`'s first two entries. `h1-00-unsorted.chf` also becomes a ninth byte-identity
sample for `tests/test_chf_samples.py`, and it settles the M2 gate wording in `STATUS.md` §1.3.

---

## H-2 — The M3 gate: does Mlaser load what the port writes?

**Why.** PORT-PLAN §4 M3 requires that the Windows tool reads files the port saved. Nothing has
ever been handed to it. All three writers are candidates, and they fail differently: `.chf`
(byte-identical re-save proven, but never *re-read* by the vendor), `Bk*.xml` (the port
re-creates the whole document from the schema), and the technology preset written by the CO2
layer page — which differs from a vendor preset only by the `CutFreq` attribute the 13 shipped
CO2 presets lack (`STATUS.md` §1.4). That one attribute is the sharpest test in the session.

**Produce the port files first** (on Linux, before starting Mlaser):

```sh
cd ~/phobicMlaserLinux
OUT=~/mlaser-captures/session-h/h2-m3-gate
SRC=~/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52
mkdir -p "$OUT"
.venv/bin/python - "$SRC" "$OUT" <<'PY'
import sys
from pathlib import Path
from nexcut.io import chf, params
src, out = Path(sys.argv[1]), Path(sys.argv[2])
# 1. a .chf the port read and wrote back (byte-identical today - the vendor has never re-read one)
chf.save_chf(chf.load_chf(src / "File/autosave.chf"), out / "h2-port-autosave.chf")
# 2. a layer-parameter document the port re-created from the schema
doc = params.read_params(src / "File/BkLayerPara.xml", "layer")
params.write_params(out / "h2-port-BkLayerPara.xml", doc)
# 3. the technology preset the CO2 page writes (the CutFreq case); slot 2 = a real cut layer
preset = params.preset_from_layer(doc, "co2", 2)
params.write_technology(out / "h2-port-technology-CO2.xml", preset)
PY
cp "$OUT"/h2-port-*.chf "$OUT"/h2-port-*.xml ~/.wine-mlaser/drive_c/Mlaser/Graph/
```

(If any of those three raise, that is itself the finding — record the traceback in `notes.md`
and carry on with the ones that worked.)

**What to click.**

1. `File ▸ Open` → `C:\Mlaser\Graph\h2-port-autosave.chf`. Does it open? Does the canvas look
   like `File\autosave.chf` does? Screenshot both, side by side, as `h2-chf-open.png`.
   Then `File ▸ Save as` → `h2-vendor-resaved-autosave.chf` — the vendor's own opinion of the
   same job.
2. Parameters: `Advanced ▸ Options` (`mf127 ▸ mf129`) → the layer page (`lp0` 图层参数). Use
   the page's **import** button (`lp10`'s confirmation "Current parameters will be overriden,
   continue?" is what you should see) to load `h2-port-BkLayerPara.xml`.
   * Record the exact message if it refuses.
   * If it loads: check three values against the port's file (a speed, a power, a height), then
     **export/back up** the parameters again (`lp12` 备份系统参数) to
     `h2-vendor-resaved-BkLayerPara.xml`.
3. Technology preset: on the CO2 layer page, use the preset library ("导出到工艺库" / its Load
   counterpart, 02 §6.1) to load `h2-port-technology-CO2.xml`. This is the `CutFreq` probe:
   * does it load with an extra attribute present?
   * does re-saving it keep `CutFreq`, drop it, or refuse?
   Save the result as `h2-vendor-resaved-technology-CO2.xml`.
4. Note **where** the preset library actually reads and writes: `C:\Technology\CO2\` (what 10
   says and what the script pre-creates) or
   `C:\users\$USER\AppData\Local\NexCut\Technology\CO2\` (what `io/params.technology_dir`
   assumes). `ls` both afterwards; this is a real open question, not a formality.

**What to save where.** Everything above into `h2-m3-gate/`, both the port-written inputs and
the vendor-re-saved outputs, plus the screenshots and the exact text of any refusal.

**Which port test consumes it.** The M3 gate in `STATUS.md` §1.4 flips from ✘ to ✔ (or gets a
named defect). The re-saved files become goldens: `h2-vendor-resaved-autosave.chf` in
`tests/test_chf_samples.py` (does the vendor's re-save differ from the port's?), and the two
XML files in `tests/test_io_params.py` (round-trip, and the `CutFreq` verdict). If the
technology preset is refused over `CutFreq`, the fix is in `io/params.serialize_technology`
and the test is a new `test_technology_preset_matches_vendor_attribute_set`.

---

## H-3 — X7: does an unknown XML attribute survive a vendor round trip?

**Why.** `tests/test_io_fidelity_review.py::test_unknown_attribute_survives_rewrite` is a
strict xfail: the port **drops** attributes its schema does not know when it rewrites a
parameter file. ParaModule's set-value path (`0x10010cdf`, `FindElem`/`AddElem`/`SetAttrib`)
keeps a CMarkup DOM and *may* preserve them — UNVERIFIED. The answer decides whether a file
round-tripped through the port loses data a future vendor version added, and it is a
five-minute experiment.

**What to click.**

1. On Linux, take the working copy's own `File/BkLayerPara.xml`, add one attribute the schema
   cannot know, and keep the original:

   ```sh
   W=~/.wine-mlaser/drive_c/Mlaser/File
   H=~/mlaser-captures/session-h/h3-x7
   mkdir -p "$H"
   cp "$W/BkLayerPara.xml" "$H/h3-before.xml"
   .venv/bin/python - "$H" <<'PY'
   import sys
   from pathlib import Path
   h = Path(sys.argv[1])
   raw = h.joinpath("h3-before.xml").read_bytes()
   # one unknown attribute on the first layer group's <GP ...> element, nothing else touched
   i = raw.index(b"<GP ")
   out = raw[: i + 4] + b'NexcutProbe="42" ' + raw[i + 4 :]
   assert out != raw
   h.joinpath("h3-tampered.xml").write_bytes(out)
   PY
   cp "$H/h3-tampered.xml" "$W/BkLayerPara.xml"
   ```
   (`BkLayerPara.xml` is `<ParameterRoot><PLayerParam1><GP …/></PLayerParam1>…` — one `<GP>`
   attribute list per layer slot, 02 §4. `PLayerParam*` is the fibre table, `PCO2LayerParam*`
   the CO2 one; the probe above lands on the first fibre slot.)
2. Start Mlaser. Open `Advanced ▸ Options` → the layer page, change **one** visible value (for
   example a cut speed) so the program has a reason to write the file back, and confirm.
   Close Mlaser completely (it writes on exit as well).
3. `cp "$W/BkLayerPara.xml" "$H/h3-after.xml"`, then
   `diff "$H/h3-tampered.xml" "$H/h3-after.xml"` (the vendor writes one long line per group,
   so `grep -o 'NexcutProbe[^ ]*' "$H/h3-after.xml"` is the quick answer and the full diff is
   the interesting one).
4. Restore: `cp "$H/h3-before.xml" "$W/BkLayerPara.xml"`.

**What to record.** Whether `NexcutProbe` is still there, whether the *rest* of the file is
otherwise byte-identical (it tells you whether the vendor rewrites the whole document or edits
in place), the attribute order, and the XML declaration/encoding of the file it wrote.

**Which port test consumes it.** `tests/test_session_h.py::test_x7_port_keeps_unknown_attributes_exactly_when_the_vendor_does`
(§2.1) reads the two files, and `tests/test_io_fidelity_review.py::test_unknown_attribute_survives_rewrite`
follows the verdict:
if the vendor preserves the attribute, the strict xfail is removed and `io/params` must keep
unknown attributes (they belong next to the `ParamDocument` values); if it drops them, the test
stays but its `reason` becomes a fact with this citation instead of an UNVERIFIED guess — and
X7 leaves `STATUS.md` §2 either way.

---

## H-4 — Crafts: lead-in, kerf compensation, cool points (and what the vendor's `.chf` writer does)

**Why.** `03 §6.1.1` calls the whole `<Crafts>` block "a typed opaque struct": `compensate_type`
2/3 = inside/outside is INFERENCE medium, `double170` "probably `LeadPosPrecent`",
`double188` "probably over-cut mm", the `cool_pos` unit is undetermined, and the `LeadLine.type`
enum beyond {0, 2} is unknown (03 §12, O11). Every one of them is settled by writing the field
from the GUI and reading the file. This is also the only chance to probe the `.chf` **writer**
questions: CRT `_fltout2` formatting, and the v2–v4 reserved lines of `docs/DECISIONS.md` D14.

**What to click.** Start from one small file with a single closed contour — draw a rectangle
(`Draw ▸ Rectangle`, `mf44 ▸ mf51`) so the geometry stays trivially readable — and save a
baseline as `h4-00-plain.chf`. Then, one change per file, saving after each:

| file | what to do | which port field it settles |
|---|---|---|
| `h4-01-lead-line.chf` | `Lead Line ▸ Lead line parameters` (`mf87 ▸ mf205`): type = line, length 7, angle 30 | `LeadLine.type/angle_deg/length` (03 §6.1.1) |
| `h4-02-lead-arc.chf` | same, but an arc lead with radius 3 | `LeadLine.arc_radius`, and the `type` enum value the vendor writes for an arc (O11) |
| `h4-03-lead-pos.chf` | move the lead-in to a different point on the contour | `Crafts.double170` — is it the path ratio 0..1 we guess? |
| `h4-04-compensate-in.chf` | `Compensate ▸ Set Compensate` (`mf113 ▸ mf486`), **inside**, width 0.2 | `compensate_type` = 2 or 3? `compensate_width` in mm? |
| `h4-05-compensate-out.chf` | the same, **outside**, width 0.2 | the other value of `compensate_type` |
| `h4-06-overcut.chf` | set an over-cut / 环切 value if the dialog offers one | `Crafts.double188` ("added to the length") |
| `h4-07-cool-points.chf` | `Cool Point ▸ Manual Cool Point` (`mf406 ▸ mf406-1`), place two | `Crafts.cool_pos` — path ratios, mm, or something else? |
| `h4-08-pwm-nodes.chf` | switch per-contour PWM on and give it two nodes | `Crafts.pwm_enable`, `pwm_nodes`, `pwm_close_pos_ratios` |
| `h4-09-tiny-numbers.chf` | scale the rectangle so a coordinate lands near `1e-7` and another near `1e-11` | the `WriteDouble` branches `%12.10f` / `"0.0"` and the `_fltout2` rounding (`io/chf.msvc_fixed`, UNVERIFIED) |

**The v2–v4 reserved lines (D14/X6).** No shipped build writes a `.chf` older than v5, so the
GUI probably cannot produce one. Check anyway: look for a "save as version" / compatibility
choice in `File ▸ Save as`, and record its absence if there is none. Also open one of the four
v4 samples (`Graph/Work1/1.chf`) and save it again: **does the vendor write it back as v4 (with
the reserved lines) or upgrade it to v5?** That single observation says whether a v2–v4 writer
exists anywhere in this build, and it is the only evidence D14 is still waiting for.

**What to save where.** Every `.chf` into `h4-crafts/`, plus for each file one line in
`notes.md` with the *numbers you typed*. The port can only decode a field if it knows what was
entered.

**Which port test consumes it.** `tests/test_chf_writer.py` and
`tests/test_io_fidelity_review.py`: each file is read with `nexcut.io.chf.read_chf`, its
`Crafts` values asserted against what you typed, and re-written byte-identical (the eighth
existing sample family grows by nine). The findings are then written into `03 §6.1.1`/`§12`,
`model/graph.Crafts` docstrings and `STATUS.md` §3.6, and the v4-re-save observation goes into
`docs/DECISIONS.md` D14.

---

## H-5 — The rest of §3.6, in one pass

Lower value each, but the prefix is already open. One file or one screenshot per row, into
`h5-misc/`, named as the row says.

| # | Question (`STATUS.md` §3.6) | Do this | Port side |
|---|---|---|---|
| 5.1 | DXF import: SOLID/TRACE/3DFACE, XLINE/RAY, rational splines, `$INSUNITS`, INSERT → Group | import `Testfile SS1mm 2.0s F+1 N2.dxf` from the package's parent directory, plus a hand-made DXF with one of each entity; save as `.chf` (`h5-dxf-<entity>.chf`) | `tests/test_io_dxf.py` goldens |
| 5.2 | G-code import: LP/M17/L subprograms, I/J tolerance, G90.1/G91.1 | import a short `.nc` with each construct; save as `.chf` | `tests/test_io_gcode.py` |
| 5.3 | PLT import: 40 plu/mm, IP default, LB, fills, SC sweeps | same shape | `tests/test_io_plt.py` |
| 5.4 | Import gates: overlap, connect, minimal-graphic, auto sort | import a file with duplicate and tiny contours, `Options` gate values noted, save the result | `tests/test_ops_sort.py`, `ops/import_gates.py` |
| 5.5 | Scan / fly-cut: row placement, connector sampling, 60 mm side lines, `scanDirection` | make one scan region, save as `h5-scan.chf`, and copy `Dump/`, `segments.txt`, `linkFlyLine_pathGlys.txt`, `closePwmPosRatios.txt` that the run rewrites | `tests/test_ops_scan.py`, 05 §5.1/§5.2 |
| 5.6 | Text: ctor defaults `d130/d138/d140`, the outline group | place a text object, save as `h5-text.chf` | `model/graph.Text` UNVERIFIED defaults (03 §6.3) |
| 5.7 | i18n: first duplicate id wins, English fallback | switch `Options ▸ Lang` to two other languages, screenshot the main window | `ui/i18n.py`, 06 §1.5 |
| 5.8 | UI: layer colours, marker shapes, bed 1300×900 at the origin, background | screenshot the canvas with one contour per layer, with index/start/direction markers on | `ui/render.py`, `ui/canvas.py` |
| 5.9 | Where `Technology\{Fiber,CO2}` really lives | `ls` both candidates after a preset save (see H-2 step 4) | `io/params.technology_dir` |
| 5.10 | The start-up log with no card | copy `AppData\Local\NexCut\Log\<date>.log` | 04 §9, `mcc/dissector.py` — the `Send Cmd:` lines of a connect attempt |

---

## 3. Closing the session

1. Close Mlaser completely.
2. Copy the vendor logs:
   `cp ~/.wine-mlaser/drive_c/users/$USER/AppData/Local/NexCut/Log/*.log ~/mlaser-captures/session-h/vendor-log/`
3. Prove the original package is untouched — this is the one thing that cannot be redone:
   ```sh
   diff -rq ~/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52 \
            ~/mlaser-wine/Mlaser | head        # only the files you changed on purpose
   ```
   (`File/ipAdd.ini` differs by `EnableLog`, plus whatever H-3/H-4 wrote; the *original* must
   never appear in a write.)
4. Finish `notes.md`: one section per question, with what you clicked, what you saw, and what
   you could not do.
5. Hand over the whole `~/mlaser-captures/session-h/` directory. Like the pcaps, it stays out
   of the public repository — the parameter files describe your machine.

---

## 4. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Failed to create Technology folder" | Mlaser was started from `Z:`. Run it from `C:\Mlaser` (step 4 of the script) |
| Crash at start, around `0x4b24e6` | `mfc42` missing: `WINEPREFIX=~/.wine-mlaser winetricks -q mfc42` (00 §Wine) |
| `wine: could not exec … 32-bit` | the prefix is win64. Delete `~/.wine-mlaser` and re-run the script; it verifies `#arch=win32` |
| "Controller connecting failed" (`mf5`) | expected, and wanted: there is no card on this network (§0) |
| `ADVANCED ▸ Set IP` crashes | known: `File/IPSet.exe` hits Wine's `mprapi` stub (10). It only runs `netsh` on the PC's own NIC; irrelevant here |
| Dongle / "no key" on a nesting action | the udev rule is not installed. Run the `pkexec` lines the script printed, re-plug, check `ls -l /dev/hidraw*`. Nothing else in session H needs it |
| No `Log\<date>.log` | `EnableLog=1` did not take, or you are looking in the package's `Log/` instead of `AppData\Local\NexCut\Log` |
| Mlaser wrote into `~/Documents/…` | it cannot have: it only ever sees `C:\Mlaser`, the working copy. If the `diff -rq` above shows the original changed, stop and report it — something ran the program from the wrong directory |
| `mv: cannot overwrite '…/Mlaser/Mlaser.part': Directory not empty`, or a nested `work/Mlaser/Mlaser-v0.0.0.52/` | a Ctrl-C during the first copy used to leave a `.part` directory that the next run copied *into*. Fixed 2026-09-16: both leftovers are discarded first and the copy is verified complete before it is moved into place. If you see this on an old checkout, `rm -rf ~/mlaser-wine/Mlaser ~/mlaser-wine/Mlaser.part` and re-run — both are under `--work`, never under `--src` |
| `USER: unbound variable` | fixed 2026-09-16 (`$USER` → `$LOGNAME` → `id -un`). It used to abort *after* the working copy was edited and *before* the udev rule was written |
