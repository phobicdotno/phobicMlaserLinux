# 05 – Path processing and motion-planning pipeline (MotionCtrl / splineAnalyer / CircleFit / CADModule)

Package analysed: `/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52` (referred to as `SRC`).
Machine: CF1390, platform "SC2000", controller MCC100 (Modbus/TCP 10.1.1.168:502).

All addresses below are virtual addresses of the 32-bit PE images at their preferred base
(`0x10000000` for every DLL). Disassembly was produced with `objdump -d -M intel`; RTTI/vtable
recovery, string tables and constant decoding were done with small Python scripts over the raw
files (no third-party PE library). Throughout the document **EVIDENCE** = directly observed
in a file/binary; **INFERENCE** = interpretation, with a confidence tag
(*high* / *medium* / *low*).

---

## 1. Executive summary

* The whole path-processing and motion-planning chain runs **on the PC**. The MCC100 card
  receives a stream of pre-interpolated items through a FIFO (NCModule strings
  `startFifo/stopFifo/clearFifo/fillFifo`, ini keys `MaxItemPerFrame=60`, `MaxFillItem=2000`,
  `MCFifoTime=1600`, `FifoTimeout=600`, hardware parameter `AX.InterpolationCycle="250"`
  [µs]). Card-side registers only expose "FIFO space margin", "FIFO interpolation data
  configuration", "PWM enable FIFO control flag" and per-axis acceleration/jerk limits
  (lang.txt `RORegName_17/18`, `RegName112`, `AxisRWRegName_4/5`).
* `MotionCtrl.dll` (v1.3.23, built 2021-07-29) is a **stand-alone build of the planning
  library**. Nothing in the package imports it. The very same classes (`CNurbsContour`,
  `CContoutSmooth : IContourSmooth`, `CVelocityPlanning : IVelocityPlanning`, version string
  `1.3.23`) are **statically linked into `Module/CADModule.dll`** (built 2025-06-14), and it is
  the CADModule copy – extended with file dumps – that produced the debug text files in the
  package root. MotionCtrl.dll is therefore the cleanest artefact to study the algorithms, and
  CADModule.dll tells us how they are wired into the application.
* `splineAnalyerVc100.dll` is a thin façade (`ISpline2DAnalyer`, 25 virtual methods) around an
  embedded copy of **openNURBS** (`ON_NurbsCurve`, `ON_Brep`, … RTTI and
  `..\Nurbs\OpenNurbs\opennurbs_*.cpp` source paths). It is used for degree-3 NURBS fitting of
  contours; MotionCtrl.dll and CADModule.dll both import its single factory export.
* `CircleFitDLL.dll` (`CCircleFit::mainFit`, `MyPoint`, `CdlFitOut`) is imported **only** by
  `AutoNest.dll` and `Dxf2Grp.dll`; it belongs to nesting / DXF import (circle recognition),
  not to the motion pipeline.
* Pipeline (PC side, in order): import clean-up → geometry operations (lead-in, corner rounding,
  micro-joints, fly-cut/scan path assembly with spline connectors) → per-contour
  `CContoutSmooth` (NURBS refit → `mergeLinearGly` → `smoothGly` → `setDataWithoutReFit`
  = segmentation into a `CNurbsContour` piece list with lengths, plus PWM on/off positions) →
  `CVelocityPlanning` (node list with junction speed limits from an empirical
  radius/accel-time formula, slow-start clamp, multi-pass whole-contour look-ahead,
  **7-phase jerk-limited S-curve** between nodes, solved with closed-form cubics) → sampling
  at the interpolation period into normalised path positions (`segInterp`/`arcInterp`) →
  `CInterpMrg` (interpolation manager: XY point generation, per-axis arc→segment velocity
  factors `Arc2SegVelK`, jump/time bookkeeping) → NCModule FIFO fill over Modbus/TCP.

---

## 2. Binary inventory and provenance

### 2.1 Files and link relationships (EVIDENCE: `objdump -p`)

| File | Size / build stamp | Exports | Imports of interest | Who imports it |
|---|---|---|---|---|
| `SRC/MotionCtrl.dll` | 115 200 B, export TS `0x61021cd7` = 2021-07-29; PDB `D:\project\CAM\加减速规划\MotionCtrl_dll_V1.3.22\Release\MotionCtrl.pdb` (directory name is UTF-8, not GBK: 加减速规划 = “acceleration/deceleration planning”); UTF-16 version string `1.3.23` | `arcInterp`, `newContourSmooth`, `newVelocityPlanning`, `segInterp`, `segInterp_time` | `splineAnalyerVc100.dll!?newSpline2DAnalyer@@YAPAVISpline2DAnalyer@@XZ`, MSVCR100 `_CIsin/_CIcos/_CIacos/_CIatan2/_CIpow/_CIsqrt/_hypot`, MSVCP100 (only `_Xlength_error`, `_Xout_of_range`, `_Orphan_all`) | **nobody** (no static import, and no binary contains the string `MotionCtrl` or any export name). *Verifier re-check:* `objdump -p` import tables of MainApp.exe, every `Module/*.dll`, AutoNest, Dxf2Grp, SmartNest, PHBX, DxfParseDllvc100 — none lists MotionCtrl.dll; `strings -a` / `strings -a -e l` find no `MotionCtrl` and no `arcInterp/segInterp/newContourSmooth/newVelocityPlanning` in any other PE; the Delay Import Directory of MainApp.exe and all modules is empty (no delay-load either) |
| `SRC/Module/CADModule.dll` | 1 299 456 B, TS `0x684d4218` = 2025-06-14; PDB `C:\Users\Michael\source\repos\CAD_head_update\sc2000-e\Release\Module\CADModule.pdb` | `newModuleProvider` | `splineAnalyerVc100.dll` (same factory), `DxfParseDllvc100.dll!newDxfFileParse`, `AutoNest.dll!Nest_*`, OPENGL32, mfc100u | MainApp.exe (module provider) |
| `SRC/splineAnalyerVc100.dll` | 1 027 072 B, TS `0x5e705333` = 2020-03-17; PDB `D:\project\CAM\NURBS曲线\opennurbs_Dll\OpenNurbs_Dll_v1.3.14\Release\splineAnalyerVc100.pdb` (NURBS曲线 = “NURBS curve”; product version 1.3.14), source paths `D:\project\CAM\NURBS`, `..\Nurbs\OpenNurbs\opennurbs_*.cpp` (19 distinct openNURBS translation units, 123 `.?AVON_*` RTTI descriptors) | `?newSpline2DAnalyer@@YAPAVISpline2DAnalyer@@XZ`, `?newSpline3DAnalyer@@YAPAVISpline3DAnalyer@@XZ` | RPCRT4 (UUIDs for ON_ classes), GDI32/USER32 (ON_Font/bitmap) | MotionCtrl.dll, CADModule.dll |
| `SRC/CircleFitDLL.dll` | 69 632 B, TS `0x54f815e8` = 2015-03-05, statically linked old CRT | `??0CCircleFit@@QAE@XZ`, `?mainFit@CCircleFit@@QAEXQAVMyPoint@@HHQAVCdlFitOut@@@Z`, `??0MyPoint@@QAE@NN@Z`, `??0CdlFitOut@@QAE@D@Z`, globals `?Ang_CircleFit@@3NA`, `?Err_CircleFit@@3NA`, `?Len_CircleFit@@3NA`, `?Rmax_CircleFit@@3NA`, `?fitOutSequence@@3HA` | KERNEL32 only | `AutoNest.dll`, `Dxf2Grp.dll` only |
| `SRC/Module/NCModule.dll` | 788 480 B | `newModuleProvider` | – | MainApp.exe |

**INFERENCE (high):** `MotionCtrl.dll` is a leftover deliverable of the "MotionCtrl_dll_V1.3.22"
project; its code was folded into CADModule (same RTTI names `.?AVCContoutSmooth@@`,
`.?AVCVelocityPlanning@@`, `.?AVCNurbsContour@@`, `.?AVIContourSmooth@@`,
`.?AVIVelocityPlanning@@`, same version literal `1.3.23` at CADModule `.rdata:0x10115108`, same
constant tables, same vtable layouts). The application never loads MotionCtrl.dll.

### 2.2 Classes recovered from RTTI (EVIDENCE: `.?AV…@@` type descriptors + complete-object-locator scan)

MotionCtrl.dll vtables (`.rdata`):

| vtable VA | class | slots (function VAs) |
|---|---|---|
| `0x1001966c` | `CNurbsContour` | 1 (`0x10001300` dtor) |
| `0x1001968c` | `IContourSmooth` | 8 (pure) |
| `0x100196b0` | `CContoutSmooth` | `0x10005e70` dtor, `0x1000a890` setParams, `0x10005f70` process, `0x100084e0`, `0x10007fd0`, `0x10008b70`, `0x10008df0`, `0x10009260` |
| `0x1001970c` | `IVelocityPlanning` | 8 (pure) |
| `0x10019730` | `CVelocityPlanning` | `0x100165d0` dtor, `0x10016720` plan(), `0x100168c0`, `0x100168f0`, `0x10016970`, `0x100165b0`, `0x100165c0`, `0x100173b0` |

CADModule.dll counterparts: `CNurbsContour` vt `0x10114d7c`; `IContourSmooth` vt
`0x10114dbc`; `CContoutSmooth` vt `0x10114de4` (slot 2 = `0x100f0410`, the dump-instrumented
`process`); `IVelocityPlanning` vt `0x1011509c`; `CVelocityPlanning` vt `0x1011511c`
(slot 1 = `0x100fffe0`, dump-instrumented `plan`); plus the CAD-side classes `CInterpMrg`
(vt `0x10114fd4`), `CPathLinkerPlan`, `CGly2ContourEng`, `CGlyContour`, `CGlyContourEx`,
`CGlyGroup`, `CGlyScan`, `CGlyText`, `CGlyCt`, `CArcRound`, `CAlphaRound`, `CCoolPoint`,
`CUnloadAngle`, `CBridge`, `CGuideCurve`, `CSubSeg`, `CSSubSeg`, `CSegOffset`,
`CUniformStartPos`, `CShareEdge`, `CCutupScrap`, `CRingSort`, `CSSort`, `CContourTopTree`,
`CEditable{Point,Segment,Arc,Circle,EllipsArc,Lwpoly,Spline}`, `CCreate{…}`,
`COp{SmoothContour,MicoLinkContour,ExplodeMicoLink,Round,GuideLine,ScanGraph,FillGrpha,
OverCutContour,Bridge,CoolPoint,ShareEdge,SplitContour,MergeGraph,…}Cmd`.

---

## 3. Terminology decoder

| Token (as seen in code / files) | Meaning | Evidence |
|---|---|---|
| **Gly / Glyph** | one geometric entity ("图元" primitive): point, line segment, arc, circle, ellipse-arc, polyline, spline. `IGlyph` is the base interface; `CGlyContour` = a contour (closed/open chain of glyphs), `CGlyContourEx`, `CGlyGroup`, `CGlyText`, `CGlyScan` (scan/fly-cut path object). The `.chf` file writer emits `<Glyphs>` … `####Gly: ` … `<End Glyphs>` (CADModule `0x1005ab20`). | RTTI names; strings at `.rdata:0x10111be4`, `0x10111bf0` |
| **Glyph type code** (first column of `linkFlyLine_pathGlys.txt`) | `1`=Point, `2`=Segment (straight line), `3`=Arc, `4`=Circle, `5`=Ellipse/ellipse-arc, `6`=Lwpoly (polyline), `7`=Spline (NURBS) | constructors store the code: `CEditablePoint` `[this+8]=1` (`0x10089240`), `CEditableSegment` `=2` (`0x1008a030`), `CEditableArc` `=3` (`0x1007db50`), `CEditableCircle` `=4` (`0x1007fac0`), `CEditableEllipsArc` `=5` (`0x100815d0`), `CEditableLwpoly` `=6` (`0x10084bb0`); `CEditableSpline` `[this+8]=7` (`0x1008cfea`); `CCreateSpline` `[this+0x40]=7` (function `0x100dfd50`, the store is at `0x100dff60`), `CCreateSegment` `=2`, `CCreateEllipse` `=5`, `CCreateLwploy` `=6` |
| **Ct** (`CGlyCt`, `GetCtGly`, `calcGraphCtInterpPt`) | "contour" (轮廓). `calcGraphCtInterpPt` = "calculate graph-contour interpolation points". | naming + the function writes `calcGraphCtInterpPt.txt` |
| **seg / segments** | (a) in `segments.txt`: the raw scan-line segments `(x0,y0) (x1,y1)` generated by the scan-fill operation; (b) in `setDataWithoutReFit_segs.txt`: the *PWM segments* = consecutive pieces of the processed path with constant laser state; (c) in `segInterp`: one straight motion segment of length L for velocity profiling. | writers at CADModule `0x100a0e70`, `0x100ec5e0`; MotionCtrl `segInterp` |
| **pathGlys** | the ordered list of glyphs that make up a fly-cut/scan path after linking (lines + type-7 spline connectors) | `linkFlyLine_pathGlys.txt` written in `0x10096fc0` |
| **linkFlyLine** | "link flying lines" = 直线飞行切割 "Segment Scan Cutting" (lang `mf112_2`): join collinear/parallel cut segments into one continuous motion with laser gated by PWM; parameters `GRP.LineFlyCollineTol/LineFlyMaxLinkLen/LineFlyMaxLen/LineFlyStartPos`. | lang.txt `pd715–pd718`; BkManuPara.xml |
| **Nurbs / CNurbsContour** | internal representation of a contour after `CContoutSmooth`: a list of degree-3 NURBS pieces (openNURBS `ON_NurbsCurve`) plus per-piece length/curvature/speed records | `CNurbsContour` fields, `totalNurbsLength:` string |
| **mergeLinearGly** | merge consecutive collinear line glyphs into one | strings `before_/after_mergeLinearGly.txt`; call `0x100ef340` |
| **smoothGly** | corner smoothing / blending of a contour (args `0.1`, `2.0`; 30° threshold inside) | call `0x100efef0(this, contour, 0.1, 2.0)` |
| **setDataWithoutReFit** | build the NURBS piece list from the (already fitted) glyph list without re-running the NURBS fit; also produces PWM-segment lengths | `0x100ec5e0` |
| **closePwmPosRatios** | positions, as a fraction of the total path length, where the PWM (laser) state toggles (close = off, next = on, …) | file content; verified numerically below |
| **micoLink / MicroLink / 微连** | micro-joint (uncut bridge left in a contour); `p_micoLinkLenPos` = list of micro-joint length/position pairs along the contour | lang `mf100`, `pd325`, `pd343–pd346` |
| **fly line / 飞行切割 / Scan cut** | "flying cut": continuous high-speed motion over several collinear features with the laser switched by position (`RegName37/38/39` "flycut data cache …", `FCP.*` parameters) | lang.txt |
| **JumpAddTime** | `[Jump] AddTime=200`: extra time (ms) added per rapid ("jump", 空跳) move in time estimation | `JumpAddTime.txt` (114 bytes, **LF line endings, no CR**, no trailing newline — verified with `xxd`), MainApp strings `Jump`,`AddTime` (UTF-16 only) |
| **Arc2SegVelK K_X/K_Y** | per-axis velocity coefficient (percent, default 100) applied when converting arcs to segments; read by `CInterpMrg` ctor from `JumpAddTime.txt` | CADModule `0x100f5ea0` |
| **VelDecc** | node-velocity log `NodeID:%d V:%f mm/s` written by the CADModule copy of `CVelocityPlanning::plan` to `Log\VelDecc.txt` (append mode `"at+"`) | CADModule `0x100ff220`, strings `0x1011513c`, `0x1011515c` |

---

## 4. The pipeline, end to end

```
 DXF/PLT/AI import (CADModule: CDxfParse, CPltFileParse, DxfParseDllvc100, CircleFitDLL via Dxf2Grp)
        │  IGP.* clean-up: MicoGraphGate, OverlapGate, ConnectGate/MegerConnectGraphType,
        │  IsAutoSmoothGraph/IsAutoSmoothSpline + SmoothAccuracy, LongContourSplit*
        ▼
 Glyph model (IGlyph list per CGlyContour, per layer GP.* process params)
        │  interactive/one-key ops: sort (CSSort/CRingSort), lead lines (CGuideCurve),
        │  corner rounding (CArcRound/CAlphaRound), micro joints (COpMicoLinkContourCmd),
        │  over-cut, cool points, bridges, scan-fill (CGlyScan, 0x100a0e70 → segments.txt),
        │  fly-cut linking (0x10096fc0 → linkFlyLine_pathGlys.txt, closePwmPosRatios.txt)
        ▼
 CContoutSmooth::process (CADModule 0x100f0410 == MotionCtrl 0x10005f70)   [per contour]
        │  refit (0x100ed4f0, tol clamped 0.01..0.3) → before_mergeLinearGly.txt
        │  mergeLinearGly (0x100ef340)                 → after_mergeLinearGly.txt
        │  smoothGly (0x100efef0, 0.1, 2.0)            → after_smoothGly.txt
        │  setDataWithoutReFit (0x100ec5e0)            → setDataWithoutReFit_segs.txt (PWM segs)
        │                                              → after_setDataWithoutReFit.txt (NURBS pieces)
        ▼
 CNurbsContour: vector of 0x48-byte piece records {cum. length, radius/curvature, feed, …}
        ▼
 CVelocityPlanning::plan (CADModule 0x100fffe0 == MotionCtrl 0x10016720)
        │  param block {acc, accTime, cornerPrecision, vmax, minSpeedFloor, factor, slowStartLen, slowStartSpeed}
        │  node builder (0x100169b0): junction speed limits v = f(radius, 0.5/accTime, cornerPrecision)
        │  slow-start clamp (0x10017270); look-ahead core (0x100114d0): backward/forward passes,
        │  7-phase S-curve solver (0x1000eeb0), cubic closed forms (Cardano, 0x10012220…)
        │                                              → Log\VelDecc.txt (NodeID, V mm/s)
        ▼
 Sampling at interpolation period (segInterp / arcInterp → vector<double> of s/L per cycle)
        ▼
 CInterpMrg (CADModule, ctor 0x100f5ea0, reads JumpAddTime.txt [Arc2SegVelK]) /
 calcGraphCtInterpPt (0x100f6f10) → XY points + PWM/power per cycle
        │                                              → calcGraphCtInterpPt.txt, p_micoLinkLenPos.txt
        ▼
 NCModule: fillFifo (0x10052390, "FillFifo Step1/2/3 Time", "CalcBufferSize/RealItemNum"),
 Modbus/TCP to MCC100 10.1.1.168:502, 60 items per frame, FIFO of 2000 items
        ▼
 MCC100 executes items every 250 µs (AX.InterpolationCycle), drives PWM from FIFO flags
```

Stage-by-stage details follow.

---

## 5. Stage A – glyph-level operations that produce the process path (CADModule)

### 5.1 Scan-fill / engraving ("扫描雕刻", lang `mf112_5`) – writer of `segments.txt`

EVIDENCE: `segments.txt` is written by CADModule function `0x100a0e70` (called from `0x100bb810`,
itself reached from `CCADModule` vtable member `0x100e4990`). It iterates a `std::vector` of
32-byte records `{x0,y0,x1,y1}` (doubles at `+0`,`+8`,`+0x10`,`+0x18`) and prints
`"(" x0 ", " y0 ")   (" x1 ", " y1 ")"` (format pieces at `.rdata:0x10113090/0x10111ec4/0x1011309c/0x10113094`).
Constants in the function: `0.01` (= `GRP.minScanLineLength="0.01"`), `500`, `1.1`,
`±0.707107` (cos/sin 45° – diagonal scan direction option `GRP.scanDirection`).

Decoded content (24 lines): three columns of 25 mm segments at
x∈{252.152–277.152, 280.152–305.152, 308.152–333.152}, eight rows y = 363.416 … 370.416
(pitch 1.000 mm), ordered as a serpentine (row 1 left→right, row 2 right→left, …).
**INFERENCE (high):** this is the raster fill of three 25×7 mm rectangles (or one hatched
region with two 3 mm gaps) at 1 mm line pitch; the file lists the *cut* segments only.

### 5.2 Fly-cut / scan path assembly – writer of `linkFlyLine_pathGlys.txt` and `closePwmPosRatios.txt`

EVIDENCE: both files are written by CADModule `0x10096fc0` (792 condensed lines; called once from
`0x100a1d8f` inside the scan-fill routine). Each `pathGlys` line is `"<type>, <length>"`
(`??6basic_ostream<int>` then `", "` at `0x10111ec4` then `<<double`).

Decoded `linkFlyLine_pathGlys.txt` (61 entries): 24 × `2, 25` (cut lines), 16 × `2, 3`
(3 mm laser-off links across the column gaps), 14 × `2, 60` (60 mm run-out / run-in lines
beyond the row ends = `GRP.scanSideLineLength="60"`, lang `pd719_3` 外扩线长度 "Side line
length"), 7 × `7, 2.65861` (type-7 **spline** U-turn connecting the end of one row's run-out
to the next row's run-in, 1 mm apart). Total path = 600 + 48 + 840 + 18.61 = **1506.6 mm**.

`closePwmPosRatios.txt` (46 values in (0,1)): multiplying by the total NURBS length
1506.62 gives 25.00, 28.00, 53.00, 56.00, 81.00, 203.66, 228.66, … (verifier re-computed all
46: successive differences are exactly the repeating pattern 3, 25, 3, 25, 122.66, 25, …, and
the sum of the 61 PWM segments is 1506.62483) i.e. the successive
laser **off / on** toggle positions (off after each 25 mm cut, on after each 3 mm gap or after
the 122.66 mm turnaround = 60 + 2.66 + 60). The first "on" at 0 and the final end-of-path are
implicit (48 toggles − 2). **INFERENCE (high):** these ratios are the data behind
`MP.IsEnablePWMPerContour` / the card's "PWM enable FIFO control flag": the interpolator
switches the PWM enable bit when the normalised path position crosses each ratio.

Geometry constants of the connector construction (`0x10097140–0x10097400`): 0.005 (collinearity
distance), 0.035 (relative tolerance), 0.14, 0.4 and 0.5 (fractions of the row gap used to
place control points) — these five were re-found by the verifier in that range; the "30, 4"
are immediates outside it; it builds control polygons of 3 and 5 points
(`vector<double>` sizes 6 and 10) that are fitted to a spline (type 7). Two variants exist
(`[ebp+0x10] == 4` selects the second, `0x10097ac5`).
**INFERENCE (medium):** the connector is a degree-3 NURBS through 3–5 control points giving
G1 continuity with the ±x tangents, which is why its length (2.66 mm) exceeds the semicircle
(1.57 mm) for a 1 mm pitch.

Related parameters (BkManuPara.xml `<GRP>`): `LineFlyCollineTol="1"` (pd716 允许偏差距离 "Max
tolerance of collinear"), `LineFlyMaxLinkLen="80"` (pd717 最大光滑连接距离 "Max length of
smooth linker"), `LineFlyMaxLen="40"` (pd718 最大飞行线长度 "Max length of fly"),
`LineFlyStartPos="0"` (pd715 起刀位置 corner of start), `CircleFlyMaxLen="80"`,
`CircleFlySortType`, `FirstDoneSingleContourCircleScan`, `FillGlyDir="0"`,
`scanDirection="0"`, `minScanLineLength="0.01"`, `scanSideLineLength="60"`,
`CO2ScanFlyCompensateStr="100#0.22,200#0.3,300#0.57,400#0.71,500#0.85,600#0.94"`,
`FiberScanFlyCompensateStr="100#0.35,…,600#1.48"` (speed [mm/s] → position compensation [mm]
of the laser switching point, laser-type specific; **INFERENCE (high)**: latency compensation
`Δs = v·t_latency`, ≈ 1.5–2.5 ms for CO2, ≈ 2.5–3.5 ms for fiber).
Hardware side (BkHardPara.xml `<FCP>`): `FlycutEncoderToleranceRatio="6"`,
`FlycutCircleVelRatio="1"` (pd290), `FlycutCirclePwmDelayTime="0"` (pd513),
`FlycutLineOpenPwmForwardCycle="-3"` / `FlycutLineColsePwmForwardCycle="-3"` (pd514/515:
PWM on/off advanced by 3 interpolation cycles = 0.75 ms).

### 5.3 Other glyph operators (names only; algorithms not analysed here)

`CArcRound` (`GRP.ArcRoundRadius=1`, `ArcRoundMinAngle_deg=0`, `ArcRoundMaxAngle_deg=90` –
lang pd600–602 倒圆角), `CAlphaRound` (`AlphaMaxAngle_deg`, `AlphaMinEdgeLen`, `AlphaLen`),
`CGuideCurve` (lead-in: `GuideLineType/Angle/Length`, `GuideArcRadius`), `CCoolPoint`,
`CUnloadAngle`, `CBridge` (`BridgeWidth`), `COpMicoLinkContourCmd` (`MicroLinkLength=0.5`,
`AutoMicroLinkType/Num/Step`, `GP.EnableMicroLinkDecc`, `MicoLinkSlowDownVel=10`,
`MC.EnableAdvMicoLink`/`AdvMicLinkPower_Pre` "non-trace micro-joint" = power reduction instead of
laser off), `COpOverCutContourCmd` (`LoopGapOverCutLength`), `CSubSeg/CSSubSeg/CSegOffset`
(kerf offset `OffsetDist=0.1`), `CUniformStartPos`, `CShareEdge`, `CCutupScrap`.

---

## 6. Stage B – `CContoutSmooth` (contour → `CNurbsContour`)

### 6.1 Construction and parameters (EVIDENCE: MotionCtrl `newContourSmooth` `0x10005e10`, `setParams` slot 1 `0x1000a890`)

`newContourSmooth()` allocates 0x48 bytes, vtable `0x100196b0`, and initialises:

| offset | type | default | clamp in `setParams` | INFERENCE (medium) |
|---|---|---|---|---|
| `+0x04..+0x0c` | `std::vector<CNurbsContour*>` | empty | – | list of contours owned by the smoother (dtor deletes each, `0x10005f00`) |
| `+0x18` | double | 0.001 | `[0, 0.1]` | fitting tolerance (mm) — corresponds to `MC.SplineAccuracyRate=0.02` (pd93 曲线控制精度 "Spline Precision") |
| `+0x20` | int | 37 (0x25) | `[7, 30]` | sample/control-point count per piece |
| `+0x28` | double | 4.0 | `[2, 8]` | smoothing factor 1 (used with 8·… and 30° in the process routine) |
| `+0x30` | double | 2.2 | `[2.2, 4]` | smoothing factor 2 |
| `+0x38` | double | 1.2 | forced to exactly 1.0 | (disabled scale) |
| `+0x40` | ptr/int | −1 | – | "current" contour pointer (skipped in dtor loop) |

`setParams` copies a **5-double (40-byte) block** from its argument (`rep movs` of 10 dwords into
`this+0x18`).

### 6.2 `process` (MotionCtrl slot 2 `0x10005f70`; CADModule `0x100f0410` with dumps)

EVIDENCE (CADModule listing):
1. `0x100eb150(contour)` – prepare; tolerance argument `[ebp+0xc]` clamped to
   `[0.01, 0.3]` (`.rdata:0x1010f7e8` = 0.01, `0x101119d0` = 0.3); `0x100ed4f0(contour, tol)`
   = **refit** (uses 30° = 0.523599 rad, π/2, 0.005, 0.01, 0.05, 1e-5, `atan2`, `hypot`).
2. dump `before_mergeLinearGly.txt` = one line per glyph with its length, then `total: `.
3. `0x100ef340` = **mergeLinearGly** (constant 0.001 mm; calls glyph accessors, uses the
   `ISpline2DAnalyer` factory `0x1010f504` once → re-fits merged pieces).
4. dump `after_mergeLinearGly.txt`.
5. `0x100efef0(this, contour, 0.1, 2.0)` = **smoothGly** (angle threshold 30·(π/180) = 30°).
6. dump `after_smoothGly.txt`.
7. `0x100ec5e0` = **setDataWithoutReFit**: builds the NURBS piece list; dumps
   `setDataWithoutReFit_segs.txt` (PWM segment lengths + `totalPwmSegments: `).
8. dump `after_setDataWithoutReFit.txt` (piece lengths + `totalNurbsLength: `), then further
   per-piece dumps (loop at `0x100f0f48…0x100f13e8`, streams without a file name string – written
   to already-open streams).

MotionCtrl's version of `process` shows the algorithmic core: it calls
`newSpline2DAnalyer()` three times (`0x100061b5`, `0x100070ee`, `0x10007f6b`; a fourth call
at `0x1000aa4d` lies outside `process`, after `setParams`) and invokes
**`ISpline2DAnalyer` slot 1** (`vtable+0x4`) as
`fit(ON_NurbsCurve* out = contour+0x20, std::vector<pt>* points, int 0, int degree = 3)`;
the analyzer pointer is stored at `CNurbsContour+0x80`, the curve domain at `+0x68/+0x70`
(0..1 or −1 = not yet), the cached length at `+0x78` (initialised −1). A corner test uses the
cosine threshold `−0.984375` (`.rdata:0x100199d8`, used at `0x10006383/0x100063d0/0x100064e5/0x10006551`;
≈ cos 170°): consecutive tangents whose dot product is below it are treated as a reversal (no
blending). Constants 2, −1, 0.001 and 1e-5 also appear.

**INFERENCE (high):** `CContoutSmooth` converts every contour into a chain of degree-3
NURBS pieces (openNURBS) with a chord tolerance = "Spline Precision" (0.01–0.3 mm), merges
collinear lines, blends corners sharper than 30° that are not reversals, and records for every
piece its length, curvature radius and programmed feed. Straight segments stay straight
(their length is exactly 25/3/60 in the dumps); only the type-7 connector changed length
(2.65861 → 2.66069) because it was re-fitted.

### 6.3 Decoding the four dump files

| file | writer | content | decoded |
|---|---|---|---|
| `before_mergeLinearGly.txt` | `0x100f0795` | `284.266` / `total: 284.266` | contour with a single glyph of 284.266 mm (a closed curve of that perimeter, e.g. a Ø 90.5 mm circle) |
| `after_mergeLinearGly.txt` | `0x100f09a9` | same | nothing merged |
| `after_smoothGly.txt` | `0x100f0bc7` | same | nothing smoothed |
| `setDataWithoutReFit_segs.txt` | `0x100ec85e` | 61 lengths: `25,3,25,3,25,60,2.66069,60,…,25` + `totalPwmSegments: 1506.62` | PWM segments of the scan path of §5.2 (same order as pathGlys) |
| `after_setDataWithoutReFit.txt` | `0x100f0ddd` | `141, 2.66069, 201, 2.66069, …, 141` + `totalNurbsLength: 1506.62` | NURBS pieces: consecutive collinear/straight PWM segments were merged into one straight piece (141 = 25+3+25+3+25+60 for the first row, 201 = 60+25+3+25+3+25+60 for middle rows); the 7 spline connectors remain separate pieces |

The files are opened with `ofstream(name, ios::out /*=2*/, 0x40 /*_SH_DENYNO*/)`
(`0x100a12ac`; the same `push 0x40 / push 0x2` pair precedes every `ofstream` ctor call in
`process`, e.g. `0x100f0791–0x100f07a4`), i.e. truncated on every run.

**Verifier finding (EVIDENCE):** `process` (`0x100f0410`) is *not* a straight-line function.
At `0x100f0ce8` it tests its fourth argument, a `bool` at `[ebp+0x14]`
(`cmp BYTE PTR [ebp+0x14],0; jne 0x100f12d2`), and when the flag is set it **skips
`setDataWithoutReFit` (`0x100ec5e0`) together with the two dumps
`setDataWithoutReFit_segs.txt` and `after_setDataWithoutReFit.txt`**, jumping to the
epilogue at `0x100f12d2`. The prototype is therefore
`process(vector<glyphRec(0xd0)>* in, double tol, bool skipSetData, double a, double b)`
(the entry loop at `0x100f04d0–0x100f0638` walks 0xd0-byte records, divisor magic
`0x4ec4ec4f >> 6` = ÷208, and tests `[rec+0] == 1`). Each stage function has exactly one
caller inside `process` (`0x100ed4f0` ← `0x100f0694`, `0x100ef340` ← `0x100f08c6`,
`0x100efef0` ← `0x100f0ae3`, `0x100ec5e0` ← `0x100f0cf5`).
**INFERENCE (medium-high):** the package state is produced by two successive calls: an earlier
`process(scanPath, …, skipSetData=false)` wrote all five files with the 1506.62 mm scan path,
and a later `process(284.266 mm contour, …, skipSetData=true)` rewrote only the first three.
The 284.266 mm closed contour is the last object that went through merge/smooth (e.g. a
Ø 90.5 mm circle, or the outline around the engraving); nothing in the dumps identifies it
further. This replaces the earlier "loop position" hypothesis; a controlled run is still the
only way to prove it (§13).

### 6.4 `CNurbsContour` piece record (0x48 = 72 bytes) – EVIDENCE from `CVelocityPlanning` node builder

The node builder reads pieces with stride 0x48 (`imul 0x38e38e39 ; sar 4` = ÷72):

| offset | meaning (INFERENCE, high unless noted) |
|---|---|
| `+0x00` | cumulative arc length at the *end* of the piece (differences give piece lengths) |
| `+0x08` | curvature radius of the piece (argument `a` of the junction speed formula) |
| `+0x10` | programmed feed for the piece (layer `GP.CutSpeed`, or slow-down speed) |
| `+0x18` | **per-piece speed cap (EVIDENCE, corrected by verifier):** read by node builder A at `0x10016eb5` (`vJ = min(vJ, piece[+0x18])`) and used *directly* as the node speed limit by node builder B (`0x10016fc6`). CADModule `0x100f6310` writes **0.0** into it (forced stop) for the piece that ends at a listed path position, or inserts a new piece ending exactly at that position with `+0x18 = 0` (see §8). |
| `+0x40` | **per-piece speed factor (EVIDENCE, corrected by verifier):** node builder A multiplies the (Vmax-clamped) junction limit by it at `0x10016e49–0x10016e56` (`vJ *= piece[+0x40]`); 1.0 = neutral. |
| `+0x20..+0x38` | remaining 4 doubles: not read by the planner (INFERENCE low: start/end tangent or PWM/power flags). `setDataWithoutReFit` does not use the `vector<piece>` helpers `0x100f5150/0x100f5230`, so its writers were not traced. |

---

## 7. Stage C – `CVelocityPlanning` (look-ahead + S-curve)

### 7.1 Object layout and parameter block (EVIDENCE: `newVelocityPlanning` `0x10016640/0x10016660`, `plan` `0x10016720`)

`newVelocityPlanning()` allocates **0x140 bytes**, vtable `0x10019730`; `+0x28..+0x67` is a
block of **8 doubles** (`rep movs` 16 dwords from the caller's block), `+0x68` and `+0xd0`
are two sub-objects (0x68 bytes each, constructed by `0x100157e0`, INFERENCE: `std::vector`
triplets of nodes / profile pieces), `+0x138` a flag byte.

| index / offset | default | clamp | role (INFERENCE) | confidence | matching UI parameter |
|---|---|---|---|---|---|
| P0 `+0x28` | 2000 | – | **acceleration A** (mm/s²); copied into each node `+0x18` | high (dimensional analysis of P0·P1/2 vs P3 below) | `MC.ManuAcc="6000"` (pd91 加工加速度 Cut Acc) / `MC.XFastMoveAcc` for rapids |
| P1 `+0x30` | 0.125 | `[0.06, 0.25]` s | **acceleration time Ta** (time from rest to A-profile completion) | high (lang `de2/de4`: "加速时间 … 范围 60 - 250", `MC.AccTime="200"` ms, `EmptyMoveAccTime="125"`) | pd92 加工加速时间 Process Acc Time |
| P2 `+0x38` | 0.05 | – (formula cut-off 0.03) | **corner precision c** (mm) | high (`MC.CornerAccuracyRate="0.05"`, lang `de6` "拐角部分的控制精度, 0.01–1.0") | pd94 拐角控制精度 Corner Precision |
| P3 `+0x40` | 200 | – | **maximum speed Vmax** (mm/s) | high | `MC.XFastMoveSpeed="500"`, `FCP.MaxSpeed="3000"` |
| P4 `+0x48` | 0 | – | **minimum-speed floor** (corrected by verifier): `plan` does `P7 = max(P7, P4)` at `0x10016797–0x100167ae` (fcomp sets C0 when P7 < P4 and *that* branch loads P4), and node builder A does `vJ = max(vJ, P4)` at `0x10016e75–0x10016e89`. It is *not* an upper bound / cut speed. With the default 0 it is inert. | medium (semantics), high (max, not min) | unknown UI parameter (no candidate found; possibly `MP.*` small-circle limit or an internal constant) |
| P5 `+0x50` | 1.0 | – | speed factor passed to the core (`[esp+0x78]`) | low | `MP.EmptyMoveSpeedFactor="1.1"` / `FlycutCircleVelRatio` |
| P6 `+0x58` | 0 | routine active only if P6 ≥ 0.1 | **slow-start length** (mm): nodes with cumulative s + 0.01 ≤ P6 are clamped, and a node is inserted at s = P6 unless one lies within 0.05 mm (details below) | high (`0x10017270`) | `GP.SlowStartLength="1"` (pd134 起步距离) |
| P7 `+0x60` | 10 (MotionCtrl); **200 in the CADModule `CInterpMrg` default block** (`0x100f43e6`) | `max(P7,P4)` (see P4), then `max(0.1,P7)` when passed to the slow-start routine | **slow-start speed** (mm/s) | high | `GP.SlowStartSpeed="10"` (pd135 起步速度), `GP.SlowStart` enable |

Derived in `plan` (`0x1001682c…0x1001685d`):
`if (A·Ta/2 ≤ Vmax) J = 2·A/Ta else J = 4·Vmax/Ta²` — the **jerk** of a triangular
acceleration profile that reaches A in Ta/2 (or, when Vmax is reached before A, the jerk that
reaches Vmax in Ta). The core receives `{A, J, Ta, P5, Vmax}` at `[esp+0x5c/0x68/0x70/0x78/0x80]`.

`plan(this, CNurbsContour* contour, const double params[8], bool mode)` — `ret 0xc`; the bool
selects node builder A (`0x100169b0`, full junction analysis) or B (`0x10016f60`).
**Node builder B (EVIDENCE, verifier):** copies `piece[+0x18]` straight into the node speed
limit (`+0x08`), `piece[+0x10]` into `+0x10`, P0 into `+0x18`, `piece[+0]` into `+0x20`, then
computes the lengths; it applies **no** junction formula, no Vmax clamp, no P4 floor and does
**not** zero the first/last node — i.e. mode B replays speed limits that were pre-computed
into the piece list. The only CADModule call site found (`0x100f61a8`) passes `1` (mode A).

**Slow start (EVIDENCE `0x100167e8–0x1001682c` and `0x10017270`, corrected by verifier):** the
routine runs only when `P6 ≥ 0.1` mm, with `v = max(0.1, P7)`. Walking nodes i ≥ 1: while
`s_i + 0.01 ≤ P6` both `+0x08` and `+0x10` are clamped to `≤ v`. At the first node beyond,
if `|s_i − P6| ≤ 0.05` it is clamped as well and the walk stops; otherwise a **new node is
inserted at s = P6** (`0x10017344–0x1001739d`, insert helper `0x10017830`) splitting the piece
(`len = P6 − s_{i−1}`, next `len = s_i − P6`, limits `min(v, feed)`), and the walk stops.

**Acceleration time (lang `de2/de4`):** 机床从静止到最大空走/加工速度所需的时间 = “time the
machine needs from standstill to the maximum move/cut speed”. Both jerk branches are consistent
with this: a triangular acceleration profile reaching `A` at `Ta/2` has `J = 2A/Ta`; a
triangular profile that reaches `Vmax` after `Ta` without saturating has `v(Ta) = J·Ta²/4`,
i.e. `J = 4Vmax/Ta²`; the branch is chosen by whether `A·Ta/2` (the speed gained by the
triangular profile) is below `Vmax` (`0x10016836–0x1001685d`; equality goes to the second
branch, where both formulas coincide).

### 7.2 Node records (0x28 = 40 bytes) built from the piece list (EVIDENCE `0x100169b0`)

| offset | content (corrected by verifier from the full listing `0x100169b0–0x10016f54`) |
|---|---|
| `+0x00` | length of the piece ending at this node (`s_i − s_{i−1}`), pass 2 (`0x10016bc0`) |
| `+0x08` | pass 1: `f_i = f(piece_i.radius, 0.5/Ta, cornerPrecision)` (§7.3, call `0x10016a65`). Pass 4 (`0x10016db5–0x10016f16`): `v_i = min(f_i, Vmax=P3)`; `v_i *= piece_i[+0x40]`; `v_i = max(v_i, P4)`; `v_i = min(v_i, piece_i[+0x18])`; `v_i = min(v_i, piece_i[+0x10])`. Finally **node[0].+0x08 = 0 and node[last].+0x08 = 0** (`0x10016f1f–0x10016f4b`): the contour starts and ends at rest. There is **no** `min` with the neighbouring node. |
| `+0x10` | pass 1: `piece_i.feed`; pass 2 (`0x10016b30–0x10016b93`): `max(f_{i−1}, f_i)` then `min(·, piece_i.feed)` (uses the *raw* radius limits, before pass 4); pass 3 (`0x10016be5–0x10016db2`, iterated until stable, unrolled ×4): every node whose `+0x10 ≤ 0.01` inherits the previous node's value (or, for node 1, the next node's if larger) — a fill-in for pieces with no programmed feed. The analyst's `min(feed_i, feed_{i+1})` is **not** what the code does. |
| `+0x18` | acceleration A (P0), pass 1 (`0x10016a91`) |
| `+0x20` | cumulative length s_i, pass 1 (`0x10016af2`) |

CADModule's copy (`0x100ff220`, called from the plan copy at `0x1010007b`) adds a `0.99`
factor (`.rdata:0x101116d0`, used at `0x100ff866`) and the `VelDecc.txt` log (`NodeID:%d V:%f mm/s`
per node, via `fprintf_s`, only when a flag byte `[ebp-0x49]` is set → `Log/VelDecc.txt` in the
package is 0 bytes and dated 2024-06-19, i.e. it has not been rewritten by any 2025 run).

### 7.3 Junction / arc speed formula (EVIDENCE: full listing of `0x100170e0`, args `(a, b, c)`)

Let `a` = radius of the piece [mm], `b = 0.5 / Ta` (so Ta = 0.125 s → b = 4; Ta = 0.2 s → b = 2.5),
`c` = corner precision [mm]. The function is piecewise-defined and C0-continuous:

```
if a ≤ 1.6:            v0 = 8·b·a
elif a ≤ 6:            v0 = sqrt( a · (102.4·b² + (30·b − 50)·(a − 1.6)) )
elif a < 12:           v0 = sqrt( a · (102.4·b² + 132·b − 220 + 150·(a − 6)·(b − 2)) )
else:                  v0 = sqrt( a · (102.4·b² + 1032·b − 2020) )
if c ≥ 0.03:           v = v0 + 73.8·(c − 0.05) · min( a, 25·b² / (73.8·c − 3.69)² )
else:                  v = v0 + 15·(30·c − 1)   · min( a, b² / (3 − 90·c)² )
```

*Verifier re-derivation:* every branch was re-traced through the x87 stack (`0x100170e0–0x10017264`);
the breakpoints are `a ≤ 1.6` (`test ah,0x41; jp`), `a ≤ 6` (`test ah,1; jne`), `a < 12`
(`test ah,0x41; jne`), and the correction selects `c ≥ 0.03` vs `c < 0.03`. The radicand is
C0-continuous at the breakpoints: at a = 1.6 both forms give 12.8·b; at a = 6 both give
102.4b² + 132b − 220; at a = 12 both give 102.4b² + 1032b − 2020. Since
`73.8c − 3.69 = 73.8(c − 0.05)`, the first correction can be written
`73.8(c−0.05)·min(a, 25b²/(73.8(c−0.05))²)`, which for `a ≥ Q` collapses to
`25b²/(73.8(c−0.05))` (independent of a).

**INFERENCE (high):** this is a "centripetal acceleration" rule `v = sqrt(a_n · r)` with an
empirically fitted `a_n(b)` (e.g. b = 4 → 3746 mm/s²; b = 2.5 → 1200 mm/s²), linearised for
r ≤ 1.6 mm, plus a corner-precision correction that vanishes exactly at c = 0.05 mm (the
default) and grows/shrinks with the allowed corner deviation. It is applied per piece and the
node limit is the minimum over the two adjacent pieces. There is no explicit angle-based
corner rule — corners are handled by `smoothGly` (blend arcs) so that every junction has a
finite radius.

### 7.4 Look-ahead core (EVIDENCE: `0x100114d0`, constants and call graph)

`core(nodes, params)` = `0x10014870` (pass 1) → `0x10014d20` → `0x100116e0` (backward pass,
uses 0.05 and the S-curve solver) → `0x10012b20` → `0x10012740` → `0x10014740`. It calls the
7-phase profile generator `0x1000eeb0` five times, the reachable-velocity solvers
`0x10012220/0x10012490` (closed-form cubic: constants 1/3 via `_CIpow`, 27, −6, 6, 0.25, 0.5 —
Cardano's formula for `v` given distance, jerk and acceleration), and tolerance/bisection
constants 0.001, 0.01, 1.2, 1.25, 1.33, 1.5, 0.75, 0.6/0.4 blend, 5, 8. There is no window
size: every node of the contour is visited in each pass. Second-level helpers
(`0x100149e0..0x100164b0`) use limits 500, 8000, 40000 (INFERENCE low: velocity /
acceleration / jerk sanity caps).

**INFERENCE (high):** classical two-pass (backward then forward) look-ahead over the whole
contour: each node speed is limited by (i) the junction limit of 7.3, (ii) the feed limit,
(iii) what can be reached from the previous node within the piece length under the
jerk-limited profile, (iv) what allows deceleration to the next node's limit. Iteration
(five profile calls) refines the case where a piece is too short to reach the requested
speed.

### 7.5 Velocity profile between two nodes – 7-phase S-curve (EVIDENCE: `0x1000eeb0`, `0x1000ecc0`, `0x1000f5a0`)

Profile descriptor (0x70+ bytes, `esi`):

| offset | field |
|---|---|
| `+0x00` L | segment length |
| `+0x08` vs | start speed |
| `+0x10` vmax | cruise speed (reduced in place if unreachable) |
| `+0x18` ve | end speed |
| `+0x20` A | acceleration limit |
| `+0x28` J | jerk |
| `+0x30,+0x38,+0x40` | t1,t2,t3 – accel jerk-up / const-accel / jerk-down |
| `+0x48` | t4 – cruise |
| `+0x50,+0x58,+0x60` | t5,t6,t7 – decel phases |
| `+0x68…` | phase distances (`v·t + J·t³/6`, `A·t²/2` …) |

Logic (`0x1000eef0…`): if `(vmax − v) ≤ A²/J` → t1 = t3 = √((vmax−v)/J), t2 = 0 (acceleration
never saturates); else t1 = t3 = A/J, t2 = (vmax − v − J·t1²)/A. The same is done for the
deceleration side with `ve`. `0x1000ecc0(v, vmax, A, J)` returns the distance needed for the
transition; if `d_acc + d_dec > L` the cruise speed is reduced (branch `0x1000f0be`, tolerance
0.001/0.01). `segInterp_time` returns `(t1+…+t7)·1000` ms. The alternative generator
`0x1000f5a0` (type ≠ 2) is a plain **trapezoid** (`v² = vs² + 2·a·L`, constants 0.5, 2, sqrt).

### 7.6 Sampling – `segInterp` / `arcInterp` (EVIDENCE: `0x100101c0`, `0x10010320`, `0x10010090`, `0x1000f730`)

```
int segInterp(std::vector<double>* out, double L, double vmax, double vs, double ve,
              double acc, double jerk, double dt_ms, int type /*2 = S-curve, else trapezoid*/);
int segInterp_time(double* t_ms, double L, double vmax, double vs, double ve,
              double acc, double jerk, int type);
int arcInterp(std::vector<double>* out, double theta0, double theta1, double radius,
              double vmax, double vs, double ve, double accX, double accY,
              double jerkX, double jerkY, double dt_ms, int type);
```
Validation in `0x10010090` (re-traced by verifier): L < 1e-4 → return 1 (skip); vmax < 0.001
or vmax > 10000 → −2; `L < 0.00025·min(vs,ve)` → return 1 (**a segment shorter than one
250 µs cycle at the entry speed is dropped** — matches `AX.InterpolationCycle="250"`);
vs < 0 or ve < 0 → −2; L > 1e6 → −2; **acc < 5** (not ≤) → −2; vs, ve are clamped to vmax;
the profile struct is filled as `{L, vs, vmax, ve, A, J}` at `+0x00..+0x28` (`0x10010166–0x1001017f`).
`segInterp` itself does **not** validate `dt`. **Return values (EVIDENCE `0x10010221–0x10010241`):**
`segInterp` returns **0** both on success and on "skip" (validator 1; the output vector was
cleared at `0x100101e0`, so an empty vector means "dropped"), and **−2** on a parameter error;
the sample count returned by the sampler in `eax` is discarded (`xor eax,eax`).
`segInterp_time` (`0x10010260`) writes `*t_ms = 0` first, then `(t1+…+t7)·1000` on success,
and always returns 0. `arcInterp` (`0x10010320`) additionally requires jerkX, jerkY ≥ 2,
dt ≥ 0.01 ms, r ≥ 0, and uses `L = |θ1 − θ0|·r`, `acc = min(accX, accY)`,
`jerk = min(jerkX, jerkY)` (`0x1001048e–0x100104a1` picks the smaller); unlike `segInterp` it
**returns the number of samples** (`(end−begin)>>3`, `0x100104c9–0x100104d1`).

Sampler `0x1000f730(out, profile, dt_ms)`: `N = round(T_total / (dt_ms/1000))`, reserve N+5,
then for k = 0..N pushes **one double per cycle = s(t_k)/L**, the normalised position along the
piece evaluated analytically in the current phase (`v·t + a·t²/2 + j·t³/6`). Returns the count.
**INFERENCE (high):** downstream code maps `s/L` onto the NURBS parameter to obtain XY (and the
PWM ratio comparison of §5.2 uses the same normalised abscissa).

Per-node speeds are in mm/s (`VelDecc` format), lengths in mm, times in s internally
(dt supplied in ms, converted with 0.001; times reported ×1000).

---

## 8. Stage D – interpolation manager (`CInterpMrg`) and point generation (CADModule only)

* `CInterpMrg` constructor `0x100f5ea0` (`ret 0x5c` → 23 dword arguments ≈ 11 doubles + ints):
  reads `GetPrivateProfileIntW("Arc2SegVelK","K_X",100, "<exe dir>\JumpAddTime.txt")` and
  `"K_Y"` and stores `K·0.01` at `this+0x68` / `+0x70` (per-axis arc→segment velocity scale,
  default 1.0). Other fields: `+0x50 = arg[+0x58]`, `+0x60`, `+0x80 = 10.0`, `+0xd8 = arg[+0x60]`,
  `+0x148 = 6000 (0x1770)`, `+0x14c = 0`, `+0x7c = 1`. Called from `CCADModule` members
  `0x100dab10` (wrapper, `ret 0x64`) and `0x100dea8b`.
* `calcGraphCtInterpPt` `0x100f6f10` (`ret 0x24` → 9 args; the function body extends to
  `0x100f8182`; it is called from the `CCADModule` member `0x100dab90`, which pushes 7 dwords
  + 1 double = 9 dwords) is the point generator: it times itself with
  `QueryPerformanceCounter`, walks the glyph list (`0x10067dc0`, `0x1005fb80`, `0x10060730`,
  `0x10060140`, `0x10060a80` accessors), uses constants 10, ±2, 6, 3π/2 (4.71239), 0.05, 0.5,
  0.1, 1000 (s→ms), and dumps `calcGraphCtInterpPt.txt` (`0x100f7d5e`) and
  `p_micoLinkLenPos.txt` (`0x100f7e3e`) (both **empty** in the package: the dump branches were
  not taken in the last run, or the lists were empty).
  **Correction (verifier):** `0x100f5150` and `0x100f5230` are *not* smoother/planner calls —
  they are `std::vector<piece(0x48)>::resize` (copies 0x12 dwords per element) and
  `::insert(pos, const piece&)`; `0x100f5450` (`ret 0xc`, constants 30000 and 0.05, helper
  `0x10026570` called 11×) is a large piece-list post-processing routine that contains no
  virtual calls. The planner is actually created and used in **`0x100f6130`** (`ret 0x20`):
  it copies the **10-double** block `CInterpMrg+0x88..+0xd0` to the stack (`rep movs` 0x14
  dwords at `0x100f6182`), then **overwrites P3 (Vmax) with a computed speed, sets P6
  (slow-start length) = 0 and P8 = 0**, creates a `CVelocityPlanning` via `0x100ff1b0`
  (CADModule's `newVelocityPlanning`; ctor `0x100fee80` stores vtable `0x1011511c`), calls
  `plan(&pieces, &block, mode=1)` through vtable slot 1 (`0x100f61a5–0x100f61b7`), fetches the
  node speeds through slot 4 (`+0x10`, args `(&vector<double>, −1)`), and converts them with
  sqrt/trig-like helpers (`0x1010063c`, `0x10100476`, `0x10100470`; INFERENCE low: speed
  components along an arc) into an output vector of 16-byte pairs. Its piece loop
  (`0x100f6130–0x100f6167`) fills only `+0x00/+0x08/+0x10` from an angle increment
  (`2π/N`, `0x100f60da`), so this call site plans **circle/arc pieces** (INFERENCE medium:
  the circle fly-cut `CircleFly*` / `FlycutCircleVelRatio` path).
* `CInterpMrg` default ctor `0x100f4350` (stores vtable `0x10114fd4`) seeds the planner
  block at `+0x88` = {2000, 0.125, 0.05, 200, 0, 1, 0, **200**, 0, 200} (P0..P9; the
  MotionCtrl default P7 = 10 is instead stored at `+0x10`), the smoother block at `+0xe0` =
  {0.001, 0x25, 4.0, 2.2, 1.2}, and creates the smoother (`0x100ebdd0` → `+0x108`) and the
  planner (`0x100ff1b0` → `+0x10c`). The 23-dword ctor `0x100f5ea0` copies its **first ten
  doubles straight into `+0x88..+0xd0`** (`lea edi,[ebx+0x88]; lea esi,[ebp+0x8]`,
  `0x100f6000–0x100f6010`); the `CCADModule` wrapper `0x100dab10` (`ret 0x64`) assembles that
  block from its own arguments in the order (a7, a8, a11, a1, a2, a9, a3, a4, a5, a6) (a_k =
  k-th double argument). The wrapper is a virtual entry called from MainApp.exe, which is the
  only binary containing the XML names (`MC.AccTime`, `MC.EmptyMoveAccTime`,
  `MC.CornerAccuracyRate`, … as UTF-16); the exact UI→P mapping therefore lives in MainApp
  and was not traced (see §13).
* `0x100f6310` (`ret 0xc`; args `(vector<piece>*, vector<double>* outPositions,
  vector<double>* ratios)`) — **split-and-stop routine (EVIDENCE, verifier):** for every ratio
  r (processed last→first) it computes `s = r · totalLength`, ignores positions within 0.2 mm
  of either contour end, locates the piece containing s, and with tolerance
  `tol = min(0.25·pieceLen, 0.1 mm)`: if s is within `tol` of the piece end → that piece's
  `+0x18 := 0`; if within `tol` of the piece start → the previous piece's `+0x18 := 0`;
  otherwise a **new piece ending at s** is inserted (`0x100f5230`) with `+0x18 = 0`,
  `+0x10 = CInterpMrg+0xa0` (P3 default 200) and the original radius. Every accepted s is
  appended to `outPositions`, which is finally reversed. **INFERENCE (medium):** the ratio
  list is the `closePwmPosRatios` list (or the micro-joint list `p_micoLinkLenPos`); with
  `+0x18 = 0` the planner brings the head to a **full stop at every laser on/off point** —
  the behaviour needed when `LaserOnDelay/LaserOffDelay` dwell times apply — whereas fly-cut
  keeps moving. Which list feeds it, and whether it is skipped for fly-cut, is open (§13).
* MainApp reads `[Jump] AddTime=200` and `[Axis4Freq] Is4Freq=0` from `JumpAddTime.txt`
  (strings `Jump`, `AddTime`, `Axis4Freq`, `Is4Freq` in MainApp.exe). The section
  `[LimitSamllCircleVel] IsLimit=0 SlowRatio=3` is **not referenced by any binary** in the
  package (the equivalent live parameters are `MP.EnableSmallCircleSpeedLimit="0"` and
  `MP.SmallCircleSpeedLimitRatio="0.6"`, lang pd1560/1561 小圆限速).

---

## 9. Stage E – streaming to the MCC100 (NCModule) and the PC/card split

EVIDENCE (NCModule strings/functions): `CMCHalAPI`, `CStdModbus`, `CExtCardModbus`,
`CVirtualMachine`, `CFrogJumpSvr`, `CFJSAccSrv`; functions logging
`"FillFifo Step1 Time: %d"` (`0x1005258c`), `"Step2"`, `"Step3"`,
`"first/second/Three fillFifo error: %d"`, `"Update MC Status Success: CalcBufferSize:%d,
RealItemNum:%d"`, `"false startFifo/stopFifo/clearFifo/setPWM/writeHardParam2Card/
readParamFromCard: %d"`, `"fillFifo Update MC Status Test Failure"`; ini keys
`MaxItemPerFrame=60`, `MaxFillItem=2000`, `MCFifoTime=1600`, `FifoTimeout=600`,
`FifoAlarmNum=30`, `ManuItemMaxCapcity=1000000`, `MCCore=30`, `MCSendInterval=1`
(`File/ipAdd.ini`). Card registers named in lang.txt: `RORegName_17` FIFO空间余量 "FIFO space
margin", `RORegName_18` FIFO插补数据配置 "FIFO interpolation data configuration",
`RegName112` PWM使能FIFO控制标志 "PWM enable FIFO control mark", `RegName37/39` 飞切数据缓存
余量/大小 "Flycut data cache margin/size", `RegName38` "Status of flycut on",
`RegName87` 飞切编码器坐标检测容差, `RegName54/55` PWM frequency/duty output,
`AxisRWRegName_4/5` 加速度/加加速度 "Acceleration/Jerk", `RWRegName_4/5` safety
deceleration / "minus deceleration", `RegName130` 运动轴4倍频 "Axis 4x frequency enable",
`SystemRWRegName_6` 总线周期 "Bus cycle".

*Verifier additions (EVIDENCE):* the FIFO strings are UTF-16 and all live in one NCModule
function, `0x10052390` (`FillFifo Step1/2/3 Time` at `0x1005258c`, `first fillFifo error`
at `0x10052630`, `Update MC Status Success: CalcBufferSize:%d, RealItemNum:%d` at
`0x1005272c`); `false setPWM: %d` is in `0x1004cfd0`; `VM Start Fifo`/`VM Stop Fifo` belong to
`CVirtualMachine` (`0x10050af0`) — a **software simulation target** that consumes the same
FIFO stream (useful for the port's dry-run mode). NCModule also carries `CDog` (dongle
check), `CFrogJumpSvr`/`CFJSAccSrv`, `CVerticalCorrect` and Boost property_tree/JSON.

**INFERENCE (high):** The PC computes the complete trajectory (positions per 250 µs cycle plus
PWM/power state) and pushes it as FIFO "items" (60 per Modbus frame, up to 2000 buffered,
refilled every `MCFifoTime`/`MCCore` ms). Among the *named* registers in lang.txt the card
exposes FIFO margin/config, PWM-enable flag, acc/jerk and fly-cut cache registers (there are
many more registers — power, frequency, encoder, bus cycle — so "only" should not be read as
an exhaustive register map). The card (a) clocks the items out to the servo/step outputs,
(b) applies its own safety deceleration/jerk limits for stops, (c) gates the PWM from the item
flags, (d) for encoder-checked fly-cut it verifies the encoder position against a tolerance. The per-item byte layout is outside the scope of this document (see the
communication-protocol analysis); the strings show a two-step "calculate buffer size → fill"
handshake.

**Firmware file `Update/MCC100_V201.52.mcf` (verifier):** 120 456 bytes, dated 2025-05-26.
It is *not* entirely structureless: the first dword `0x0001d688` = **120456 = the file size**,
then `0x00f8b0f4`, `0x00000000`, `0x32ba741b`, and at `+0x10` `0x0001800c` = 98 316
(INFERENCE low: payload length or load address; `0x00f8b0f4`/`0x32ba741b` look like a
checksum/CRC and a key or magic). From `+0x14` on the content has 7.998 bits/byte entropy
(every 4 KiB block ≥ 7.9), all 256 byte values occur, and `strings -a` yields 1485 random
4+-char runs (196 with `-n 6`, 33 with `-n 8`, none meaningful; no UTF-16). The body is
encrypted or compressed; the analyst's "no readable strings" holds.

---

## 10. Parameter inventory relevant to planning

Source columns: XML file/element (`File/*.xml`), lang id and label (Chinese → English), where the
value enters the pipeline.

| XML attribute | lang id | label (zh) | label (en) | value on this machine | pipeline use |
|---|---|---|---|---|---|
| `AX.InterpolationCycle` (BkHardPara) | pd238 | 杂项.插补周期 | Misc.Interpolation period | 250 (µs) | dt of `segInterp`/`arcInterp`; `L < 0.00025·v` drop rule |
| `AX.SafeStopFactor` | pd237 | 杂项.安全减速系数 | Safe deceleration factor | 3 | card-side stop decel |
| `AX.Enable4Freq` | pd236 | 编码器4倍频 | Encoder 4× | 1 | encoder decode (also `Axis4Freq/Is4Freq` in JumpAddTime.txt) |
| `MC.XFastMoveSpeed` (BkManuPara) | pd89 | 运动控制.空走速度 | Run Control.Move Speed | 500 mm/s | Vmax for rapids |
| `MC.XFastMoveAcc` | pd90 | 空走加速度 | Move Acc | 6000 mm/s² | A for rapids |
| `MC.EmptyMoveAccTime` | pd661 | 空走加速时间 | Empty Move Acc Time | 125 ms | Ta for rapids (clamp 60–250) |
| `MC.ManuAcc` | pd91 | 加工加速度 | Cut Acc | 6000 mm/s² | A for cutting |
| `MC.AccTime` | pd92 | 加工加速时间 | Process Acc Time | 200 ms | Ta for cutting → b = 0.5/Ta = 2.5 in the junction formula |
| `MC.SplineAccuracyRate` | pd93 | 曲线控制精度 | Spline Precision | 0.02 mm | NURBS fit tolerance (`CContoutSmooth+0x18`, clamp 0–0.1 / process clamp 0.01–0.3) |
| `MC.CornerAccuracyRate` | pd94 | 拐角控制精度 | Corner Precision | 0.05 mm | parameter c of the junction formula (UI range 0.01–1.0) |
| `MC.BoundSpeed` | pd528 | 走边框速度 | Go Frame Speed | 500 | frame-trace move |
| `MP.EmptyMoveSpeedFactor/AccFactor` | pd2205/2206 | 加工系数.速度系数1/2 | Speed Factor 1/2 | 1.1 / 1.5 | scale of rapid speed/acc |
| `MP.EnableSmallCircleSpeedLimit`, `SmallCircleSpeedLimitRatio` | pd1560/1561 | 小圆限速 | Small circle speed limit | 0 / 0.6 | extra clamp on small-circle speed (INFERENCE) |
| `MP.IsEnablePWMPerContour` | pd1608 | 每段轮廓切换PWM使能 | Enable PWM per contour | 1 | PWM toggle items per contour/segment |
| `MP.JogStopDccFactor` | pd1551 | 手动移动停止减速系数 | Jog stop decel factor | 1 | jog only |
| `SP.LimitDeccFactor`, `LimitDeccLengthRatio` | pd516/517 | 限位减速系数 / 幅面比例 | Limit decel factor / length ratio | 1 / 0.1 | soft-limit approach deceleration |
| `FCP.MaxSpeed`, `FCP.MaxAcc` (BkHardPara) | pd509/510 | 高级切割.系统最大速度/加速度 | System Max Speed/Acc | 3000 / 20000 | absolute caps |
| `FCP.Flycut*` | pd289/290/513/514/515 | 飞行切割.… | Scan Cutting.… | 6 / 1 / 0 / −3 / −3 | fly-cut encoder tolerance, circle speed ratio, PWM timing offsets (cycles) |
| `MAC.Acceleration`, `MAC.AccelerationTime`, `MAC.SpeedRatio`, `MAC.WritePluse` | EtherAxisInfos_27/28/10, A241021_1 | 工作参数.加速度/加速时间, 导程, 每转脉冲数 | axis acc / acc time / lead (mm) / pulses per rev | 20000 / 125 / 31.003 (X), 31.009 (Y) / 8000 | axis scaling: 8000 pulses per 31 mm ≈ 258 pulses/mm |
| `GP.CutSpeed` (BkLayerPara) | pd125 | 切割速度 | Cut Speed | per layer (11, 250, 100, 83.3 …) | piece feed `+0x10` |
| `GP.SlowStart/SlowStartLength/SlowStartSpeed` | pd133/134/135 | 慢速起步/起步距离/起步速度 | Slow start / length / speed | 0 / 1 / 10 | P6, P7 |
| `GP.PowerAdjustWithSpeed`, `GP.PWMCurveNodes`, `GP.FreqCurveNodes`, `*CurveSmoothType` | pd124/154/155, A241012_3/4/5 | 根据速度实时调节功率, 功率曲线点, 频率曲线点, 平滑方式 | Dynamic Power, Power/Freq curves, smooth type | e.g. `0,0,15,35,37,69,59,92,76,100,100,100` | piecewise-linear (or smoothed) **power-% vs speed-%** curve evaluated per interpolation item ("power vs position" is derived from the planned speed profile) |
| `GP.LaserOnDelay`, `LaserOffBeforeDelay/AfterDelay` | pd126… | 切割过程.停留时间 … | laser on/off delays | per layer | timing items around PWM toggles |
| `GRP.SmoothAccuracy` / `IGP.SmoothAccuracy` | pd370 | 自动平滑.曲线平滑精度 | Smooth Precision | 0.05 | tolerance for `COpSmoothContourCmd` / import smoothing |
| `IGP.ConnectGate`, `MegerConnectGraphType`, `OverlapGate`, `MicoGraphGate` | pd367/368/366/364 | 相连线/重复线/小图形 | connect, overlap, tiny-graph gates | 0.01 | import clean-up |
| `IGP.LongContourSplitFactor`, `LongContourAutoSplit`, `AutoSplitThreshold` | pd532… | 大图优化系数 | long contour split | 0.4 / 0 / 100000 | splitting very long contours before planning (INFERENCE: FIFO/latency bound) |
| `GRP.ArcRound*`, `Alpha*`, `GuideLine*`, `MicroLink*`, `LineFly*`, `CircleFly*`, `scan*`, `*ScanFlyCompensateStr` | see §5 | | | | geometry operators |
| `JumpAddTime.txt [Jump] AddTime` | – | | | 200 ms | added per rapid in time estimate (`gp2001` 空跳时间) |
| `JumpAddTime.txt [Arc2SegVelK] K_X/K_Y` | – | | | 100 % | per-axis arc velocity scale in `CInterpMrg` |

---

## 11. Data structures summary

| structure | size | fields (offset: meaning) | source |
|---|---|---|---|
| scan segment | 32 B | 0 x0, 8 y0, 16 x1, 24 y1 (double) | `0x100a0e70` |
| `CContoutSmooth` | 0x48 | see §6.1 | `0x10005e10` |
| `CNurbsContour` piece | 0x48 | 0 cum-length, 8 radius, 0x10 feed, 0x18 speed cap (0 = forced stop), 0x40 speed factor, 0x20–0x38 unknown | §6.4 |
| `CNurbsContour` object | ≥0x270 | +0x20 ON_NurbsCurve/analyzer data, +0x68/+0x70 domain, +0x78 length (−1), +0x80 `ISpline2DAnalyer*`, +0xc0/+0xc8 start point, +0x128/+0x160/+0x250 sampled data | `0x10005f70` |
| planner param block | 8 doubles read by `plan` (CADModule stores 10 at `CInterpMrg+0x88`) | A, Ta, c, Vmax, minSpeedFloor (P4), factor (P5), slowStartLen, slowStartSpeed, [P8, P9 unused by `plan`] | §7.1, §8 |
| `CVelocityPlanning` | 0x140 | +0x28 params, +0x68 & +0xd0 containers, +0x138 flag | `0x10016660` |
| `CInterpMrg` (CADModule) | ≥0x150 | +0x10 = 10.0, +0x68/+0x70 K_X/K_Y·0.01, +0x88..+0xd0 planner block (10 doubles), +0xe0..+0x100 smoother block, +0x108 `IContourSmooth*`, +0x10c `IVelocityPlanning*`, +0x148 = 6000 | `0x100f4350`, `0x100f5ea0` |
| node | 0x28 | 0 len, 8 vLimit (radius formula → Vmax clamp → ×piece[0x40] → max P4 → min piece[0x18] → min feed; ends = 0), 0x10 vFeed (min(feed, max(f_{i−1}, f_i)) with zero fill), 0x18 A, 0x20 s | §7.2 |
| S-curve profile | ≥0x70 | L, vs, vmax, ve, A, J, t1..t7, distances | §7.5 |
| `segInterp` output | `vector<double>` | s/L per interpolation cycle | §7.6 |
| PWM ratios | `vector<double>` | toggle positions / total length | §5.2 |
| `ISpline2DAnalyer` | 25 virtual slots | slot 0 dtor(flag), slot 1 `fit(ON_NurbsCurve*, vector<pt>*, int, int degree)`, slots 3/4 evaluation/length (INFERENCE) | `0x100e0bb4` |

---

## 12. Algorithm characteristics (what a re-implementation must reproduce)

| aspect | finding | confidence |
|---|---|---|
| Contour representation | degree-3 NURBS pieces fitted with openNURBS at a chord tolerance ("Spline Precision", clamp 0.01–0.3 mm; default 0.02) | high |
| Line handling | straight segments preserved exactly and merged when collinear (0.001 mm) | high |
| Corner treatment | blend corners sharper than 30° unless nearly a reversal (cos < −0.984); junction speed from radius via §7.3; corner precision c shifts the limit | high (structure), medium (exact geometry of blend) |
| Look-ahead | whole-contour, multi-pass backward/forward with iterative refinement; no fixed window; first and last node speed forced to 0 | high (structure) / medium (pass details not re-traced by verifier) |
| Profile | 7-phase jerk-limited S-curve (type 2) with A and J derived from (A, Ta, Vmax); trapezoid fallback (other types) | high |
| Jerk | J = 2A/Ta or 4Vmax/Ta² (Ta clamped 0.06–0.25 s) | high |
| Slow start | first `SlowStartLength` mm limited to `SlowStartSpeed`; active only if length ≥ 0.1 mm; a node is inserted at the boundary unless an existing node is within 0.05 mm | high |
| Sampling | analytic evaluation of s(t) every 250 µs → normalised abscissa; pieces shorter than one cycle dropped | high |
| Rapids | same planner with `XFastMoveSpeed/XFastMoveAcc/EmptyMoveAccTime` (× speed/acc factors); `+200 ms` per jump in time estimates | medium |
| Laser control | PWM toggles at path-length ratios (per contour); optional power/frequency-vs-speed curves; fly-cut PWM advanced/delayed by N cycles and speed-dependent position compensation tables | high |
| Fly/scan cut | serpentine with 60 mm run-in/out lines and spline U-turns; whole raster becomes one contour | high |
| Units | mm, mm/s, mm/s², s internally (ms at API boundaries) | high |

---

## 13. Open questions

1. (Largely resolved by the verifier, §6.3) `process` has a `bool skipSetData` argument
   (`[ebp+0x14]`, test at `0x100f0ce8`) that skips `setDataWithoutReFit` and its two dumps;
   the package state matches a scan-path call followed by a 284.266 mm-contour call with the
   flag set. Still open: which caller passes `true`, and what the 284.266 mm contour was.
   Needs a controlled run (e.g. under Wine with a single circle, then a single scan region).
2. Exact meaning of piece doubles `+0x20..+0x38` (`+0x18` = speed cap, `+0x40` = speed factor
   are now known) and of `P5` (default 1.0; passed to the core at `[esp+0x78]`). Also which UI
   value, if any, reaches `P4` (the minimum-speed floor) — the wrapper `0x100dab10` receives
   it as its 9th double argument from MainApp.
2b. The exact UI→P mapping: MainApp.exe is the only binary holding the XML names
   (`MC.AccTime`, `MC.EmptyMoveAccTime`, `MC.CornerAccuracyRate`, `HPA3.AccTime_ms`, …); the
   CADModule side only shows the argument order (a7, a8, a11, a1, a2, a9, a3, a4, a5, a6) →
   P0..P9. Whether `AccTime` is divided by 1000 in MainApp or in the ParaModule layer was not
   found (CADModule's only 0.001 constant, `.rdata:0x1010f778`, is used 89× but never next to
   the block copy).
2c. Which list (`closePwmPosRatios` or micro-joint positions) feeds the split-and-stop routine
   `0x100f6310`, and whether fly-cut/scan bypasses it.
3. Exact NURBS fitting method inside `CSpline2DAnalyer::fit` (interpolation vs least-squares,
   parameterisation) and what slots 3/4 return.
4. Byte layout of a FIFO "item" and how PWM enable / power / frequency are encoded per cycle
   (NCModule `fillFifo`; see the protocol analysis document).
5. Whether arcs from CAD are kept as arcs (`arcInterp` exists) or always converted to NURBS
   before planning (the planner only sees NURBS pieces; `arcInterp` may serve rapids/jog).
6. Second node-builder mode (`0x10016f60`) – which flows use it.
7. How the `PWMCurveNodes` speed-percentage curve is sampled (per item vs per piece) and
   whether `FreqCurveNodes` is applied to the same items.

---

## 14. Implications for the Linux port

**Must be replicated (behaviour visible to the user / to the card):**

* Per-contour pipeline order: import gates → glyph operations → NURBS refit & merge & blend →
  piece list with (length, radius, feed) → junction limits → look-ahead → S-curve → 250 µs
  sampling → PWM toggle positions → FIFO items. The parameter semantics of §10 (especially
  `AccTime` 60–250 ms, `CornerAccuracyRate`, `SplineAccuracyRate`, slow start, fly-cut
  parameters) should be preserved so existing `BkManuPara.xml/BkLayerPara.xml` files keep their
  meaning.
* The junction speed formula of §7.3 if identical cut quality/timing is desired (it is
  cheap to implement verbatim). A physically cleaner alternative (`v = sqrt(a_n·r)` with a
  user-set `a_n`, plus a corner-deviation rule) can be offered as an option.
* 7-phase jerk-limited S-curve with J = 2A/Ta (or 4Vmax/Ta²), whole-path look-ahead.
* Position-indexed PWM switching with the ±cycle offsets and the speed→distance
  compensation tables; power/frequency vs speed curves.
* Scan-fill generation (serpentine, side lines, spline U-turns) and fly-line linking rules.

**Can be replaced by existing open-source components:**

| function in Mlaser | Linux replacement | notes |
|---|---|---|
| `splineAnalyerVc100.dll` (openNURBS wrapper) | **openNURBS** (McNeel, github.com/mcneel/opennurbs, MIT-style licence) `ON_NurbsCurve`, `ON_NurbsCurve::CreateClampedUniformNurbs`, `ON_Curve::GetLength`, `ON_Curve::GetNurbForm`; or **libnurbs / tinynurbs / Geomview** for a lighter dependency; **Eigen** for least-squares fitting | Reusing openNURBS gives identical curve evaluation |
| `CircleFitDLL` (Kåsa/Taubin-style circle fit) | any least-squares circle fit (e.g. Chernov's `circle_fit` C code, OpenCV `fitEllipse`, or 20 lines with Eigen) | only used by nesting/DXF import |
| `CVelocityPlanning` look-ahead + S-curve | **LinuxCNC** `tp` (trajectory planner) and `tc.c` — trapezoidal by default, but the newer "jerk-limited" branch / `simple_tp`; **Klipper** `toolhead.py`/`trapq` with `lookahead` (junction deviation, square-corner velocity); **grbl / grblHAL** `planner.c` (junction deviation, backward/forward passes, trapezoid); **Ruckig** (github.com/pantor/ruckig, MIT for community version) for time-optimal 7-phase jerk-limited profiles per segment; **Reflexxes RML** (LGPL type II); **PyRoboPlan / TOPP-RA** for path-constrained time-optimal profiles | The Mlaser planner = grbl-style two-pass look-ahead + Ruckig-style per-segment S-curve; Klipper's `MoveQueue` structure is the closest architectural match (lookahead flush + junction speed) |
| junction speed rule | Klipper "junction deviation"/`square_corner_velocity`; grbl's centripetal model `v² = a·δ·sinθ/2 /(1−sinθ/2)` | The Mlaser rule is radius-based because corners are pre-blended; keep both |
| 250 µs sampling / NURBS evaluation | de Boor evaluation (openNURBS `ON_NurbsCurve::Evaluate`) with arc-length reparameterisation (Gauss-Legendre or cumulative chord table) | needed to map s/L → (x,y) |
| PWM/power scheduling | LinuxCNC `motion` spindle-sync analogue; **LaserWeb / LightBurn-style "power vs speed" tables** (LightBurn and grbl-laser use `M4` dynamic power ∝ speed — the same idea as `PowerAdjustWithSpeed`) | implement as per-item power computed from planned v |
| DXF/PLT import | **libdxfrw**, **dxflib**, **ezdxf** (Python) | replaces `DxfParseDllvc100`/`Dxf2Grp` |
| scan-fill, fly-cut linking, micro joints, lead-ins, offsets | **Clipper2** (offsets, kerf), **CGAL**/`shapely` for hatching; LaserWeb/LightBurn algorithms as design reference | geometry only; the parameter set of §5 must be honoured |
| FIFO streaming | custom Modbus/TCP client (libmodbus) feeding the MCC100; real-time thread keeping ≥ `FifoAlarmNum` items ahead | protocol per the communications document |

**Recommended structure for the Linux implementation:** one library `mlaser_plan` with
(1) `contour_fit.cpp` (openNURBS, tolerance clamp 0.01–0.3, collinear merge, 30° blend),
(2) `junction.cpp` (§7.3 formula + optional physical model), (3) `lookahead.cpp`
(grbl/Klipper-style backward/forward passes over `Node{len, vJunction, vFeed, A, s}`),
(4) `scurve.cpp` (Ruckig or a hand-written 7-phase generator reproducing §7.5, including the
"reduce vmax when L too short" branch), (5) `sampler.cpp` (250 µs, s/L → XY → pulses using
`WritePluse/SpeedRatio` = 8000 pulses per 31.003 mm), (6) `pwm_schedule.cpp` (toggle ratios,
cycle offsets, speed compensation tables, power/freq curves), (7) `fifo_stream.cpp`.
Unit tests should reproduce the numbers in this package: total length 1506.62 mm for the
24-segment raster with 60 mm side lines, toggle positions 25/28/53/56/81/203.66…, and
`segInterp_time` for simple segments.

---

## Verification notes

Adversarial re-check of this document against the primary files under `SRC` (2026-09-12).
Tools: `objdump -p/-h/-d -M intel/-s`, `strings -a` and `strings -a -e l`, `xxd`, `iconv`,
Python (stdlib) scripts that resolve every `ds:0x…` operand to its double/float value from the
`.rdata`/`.data` dumps and that recover vtables from RTTI complete-object locators.
Scratch listings: `mc.asm`/`cad.asm`/`nc.asm` in the session scratchpad.

### What was checked and confirmed (evidence re-produced)

| # | claim | re-check result |
|---|---|---|
| 1 | MotionCtrl.dll imported by nobody; classes statically linked in CADModule | import tables of all 13 PEs, ASCII+UTF-16 string search (`MotionCtrl`, export names), empty delay-load directories; RTTI names `.?AV{CNurbsContour,CContoutSmooth,IContourSmooth,CVelocityPlanning,IVelocityPlanning}@@` present in both; UTF-16 `1.3.23` at CADModule file offset `0x113908` = VA `0x10115108`; dump-file strings exist **only** in CADModule (MotionCtrl has no `.txt`/`NodeID`/`total` strings). Vtables: MotionCtrl `0x100196b0` (8 slots, exactly as listed) and `0x10019730` (8 slots). **Confirmed.** PDB directory names decoded (UTF-8): 加减速规划, NURBS曲线. |
| 2 | splineAnalyer = openNURBS wrapper, 25-slot `ISpline2DAnalyer`, slot 1 = fit(out, pts, 0, 3) | 123 `.?AVON_*` descriptors, 19 `opennurbs_*.cpp` paths, PDB `OpenNurbs_Dll_v1.3.14`; vtables `ISpline2DAnalyer` `0x100e0b4c`, `CSpline2DAnalyer` `0x100e0bb4`, `CSpline3DAnalyer` `0x100e0cac` — 25 slots each; MotionCtrl `0x100061b5–0x100061d5` = `mov edx,[eax]; mov edx,[edx+4]; push 3; push 0; push esi; lea ecx,[edi+0x20]; push ecx; call edx`. **Confirmed.** |
| 3 | CircleFitDLL used only by AutoNest/Dxf2Grp | import tables. **Confirmed** (11 exports incl. the ctors/dtors/operator= listed; the `?mainFit…` symbol and the five globals appear in the importers' tables). |
| 4 | Trajectory computed on PC, streamed through FIFO | NCModule UTF-16 strings located in `0x10052390` (fillFifo), `0x1004cfd0` (setPWM), `0x10050af0` (VM); ini keys, `InterpolationCycle="250"`, lang entries all re-read. **Confirmed**, wording "only" softened; VM/simulation target added. |
| 5 | 7-phase S-curve, J = 2A/Ta or 4Vmax/Ta², Ta ∈ [0.06, 0.25] | `0x1001675b–0x10016797` clamps (0.06 at `.rdata:0x100199b0`, 0.25 at `0x10019800`); J at `0x10016836–0x1001685d` (0.5, 4.0); profile generator `0x1000eeb0` re-traced (`(vmax−v) ≤ A²/J` test, `sqrt((vmax−v)/J)` vs `A/J`, `t2 = (vmax−v−J t1²)/A`, distances with `/6` and `0.5`; `vmax` is raised to `max(vs,ve)` if lower, `0x1000eee3–0x1000eeed`). Cardano constants in the core were **not** re-traced (analyst's evidence accepted as plausible). **Confirmed** (profile), **plausible** (core). |
| 6 | Junction/arc formula | full x87 re-derivation of `0x100170e0` (see §7.3 addendum); breakpoints, coefficients, both corrections and the call-site arguments (`piece+8`, `0.5/[this+0x30]`, `[this+0x38]`) at `0x10016a4a–0x10016a65`. **Confirmed.** |
| 7 | P-block ↔ UI parameters | defaults {2000, 0.125, 0.05, 200, 0, 1, 0, 10} confirmed in MotionCtrl `.data:0x1001c288` (8 doubles preceded by 2π, π/2, 180/π, π/180) and in `newVelocityPlanning` `0x100166a9–0x100166e6`; CADModule seeds the same block in `CInterpMrg::CInterpMrg` (`0x100f4350`) but with **P7 = 200**. P0–P3, P6, P7 mapping stays *likely*; **P4 refuted as "cut speed / upper bound"** — it is a floor (`max`). The UI→block path goes through MainApp (only holder of `MC.AccTime` etc.); not traced. **Downgraded** to *likely* for P0–P3/P6/P7, *unknown* for P4/P5. |
| 8 | segInterp/arcInterp/validator/sampler | argument layout, every threshold (1e-4, 0.001, 10000, 0.00025, 1e6, 5, 2, 0.01), profile struct layout, sampler `N = int(T/dt·1000 + 0.5)`, reserve N+5, `(v t + J t³/6)/L` per cycle — all re-read. Corrections: `acc < 5` (not ≤); return-value semantics (segInterp returns 0/−2 and drops the count, arcInterp returns the count, segInterp_time always 0); `segInterp` does not check `dt`. **Confirmed with corrections.** |
| 9 | Dump-file decoding | all eight files re-read; counts 24/16/14/7, sums 1506.61 (pathGlys) / 1506.62483 (PWM segs) / 1506.62 (NURBS pieces); all 46 PWM ratios × 1506.62 re-computed. Glyph codes 1–6 re-found in the `CEditable*` ctors (`0x100892ec`, `0x1008a054`, `0x1007dbec`, `0x1007fb32`, `0x100816c7`, `0x10084c7c`); code 7 at `0x1008cfea` (CEditableSpline) and `0x100dff60` (CCreateSpline). **Confirmed.** |
| 10 | JumpAddTime.txt readers | `Jump/AddTime/Axis4Freq/Is4Freq/\JumpAddTime.txt` UTF-16 only in MainApp; `Arc2SegVelK/K_X/K_Y/\JumpAddTime.txt` UTF-16 only in CADModule, used at `0x100f5ed8–0x100f5fa6` with `push 0x64` default and `fmul 0.01`; `LimitSamllCircleVel/IsLimit/SlowRatio` in no binary (ASCII and UTF-16, min length 3). **Confirmed.** File uses LF-only line endings. |
| 11 | .mcf opaque | entropy 7.998 bits/byte; string counts 1485/196/33 for min lengths 4/6/8 (the analyst's "196" is the `-n 6` count). **Confirmed**, with the 20-byte header structure (size field) added. |

### What was changed in the document

* §2.1: PDB paths decoded; import/delay-load re-check added.
* §3: JumpAddTime.txt line-ending note; spline type-code store addresses.
* §5.2: numeric re-verification of the PWM ratios; connector constants scoped.
* §6.2: fourth `newSpline2DAnalyer` call site and the addresses using −0.984375.
* §6.3: **replaced** the "loop position" hypothesis with the `skipSetData` flag finding.
* §6.4: piece record — `+0x18` (speed cap / forced stop) and `+0x40` (speed factor) **are** read
  by the planner; the "not read" statement was wrong.
* §7.1: **P4 is a minimum-speed floor** (`max`, not `min`); P7 CADModule default 200; node
  builder B described; slow-start algorithm corrected (0.1 mm activation, 0.01/0.05
  tolerances, node insertion); acceleration-time semantics tied to lang `de2/de4`.
* §7.2: node fields `+0x08`/`+0x10` corrected from the full four-pass listing (no
  "min with neighbour"; `max(f_{i−1}, f_i)` then `min(·, feed)`; zero-fill pass; end nodes
  forced to 0); 0.99 factor address; VelDecc.txt date.
* §7.3: continuity check and algebraic simplification of the correction term.
* §7.6: validator threshold and return-value corrections.
* §8: `0x100f5150/0x100f5230/0x100f5450` re-identified (vector helpers / post-processing, not
  planner calls); the real planner call site `0x100f6130` and its overrides (P3, P6 = 0,
  P8 = 0, mode = 1); `CInterpMrg` layout (10-double block at `+0x88`, smoother block at
  `+0xe0`, owned smoother/planner); the split-and-stop routine `0x100f6310`.
* §9: FIFO string→function addresses; `CVirtualMachine` simulation target; "only" softened;
  .mcf header analysis.
* §11, §12, §13: tables and open questions updated accordingly.

### What remains uncertain

* The look-ahead core (`0x100114d0` and callees) was not re-traced; the "backward/forward,
  five profile calls, Cardano" description is the analyst's and is rated *plausible*.
* The exact UI parameter → P0..P9 mapping (needs MainApp.exe analysis of the caller of the
  `CCADModule` virtual wrapper `0x100dab10`), including whether `AccTime` is divided by 1000
  before it reaches P1 and which value feeds P4/P5/P8/P9.
* Which position list feeds `0x100f6310` (PWM toggles vs micro-joints) and which flows skip it.
* Semantics of piece doubles `+0x20..+0x38` and of the `CNurbsContour` fields beyond `+0x80`.
* The role of the 0xd0-byte glyph records consumed by `process` and the identity of the
  284.266 mm contour in the dumps.
* The .mcf header fields beyond the size dword.
