# 09 — Geometry / CAD / nesting dependencies of Mlaser (SC2000) v0.0.0.52

Package analysed: `/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52` (read-only, referred to as `SRC` below).
Tools: `objdump -p/-d/-h`, `strings -a [-e l]`, `xxd`, plus two small Python (stdlib only) helpers written to the scratchpad: a PE section-string extractor (GBK/UTF‑16 aware) and an export-table walker. No third-party PE parser was available.

Conventions used in this document:

* **EVIDENCE** = something directly observed (a string, an export name, an instruction, a file). Each is cited by file, section/RVA/offset or line.
* **INFERENCE** = interpretation; each carries a confidence (high / medium / low).
* Chinese strings are quoted verbatim with an English translation in brackets.
* All virtual addresses assume the DLLs' preferred ImageBase `0x10000000` (all six geometry DLLs and `Module/CADModule.dll` declare that base per `objdump -p`).

---

## 1. Executive summary

| Binary | What it really is | Vendor / licence status | Role in Mlaser |
|---|---|---|---|
| `Dxf2Grp.dll` (7.5 MB, VC6/MFC42, PE timestamp 2015‑09‑21) | Thin in-house wrapper (`CDrawing`, `CPart2`, `CLine2/CArc2/CCircle2`) around a **statically linked Open Design Alliance "DWGdirect 2.3.1.0"** (pre‑Teigha ODA library, © 2003‑2007) incl. its ACIS/FreeType sub‑components | Proprietary ODA (commercial member licence); wrapper © 2009 "版权所有 (C) 2009" | DWG/DXF → closed-contour "part" conversion for the **nesting** path only (imported by `AutoNest.dll`, not by the CAD module) |
| `DxfParseDllvc100.dll` (129 KB, VC10, 2024‑01‑13) | In-house **text DXF parser** "DxfParseDllvc100‑v1.4.92" (pdb path in binary) | Proprietary in-house | Primary DXF importer used by `Module/CADModule.dll` |
| `AutoNest.dll` (594 KB, VC6/MFC42, 2023‑04‑28) | "**HePin DLL**" (合拼 = "merge/combine") from **武汉智格科技有限公司** (Wuhan SmartNest Technology Co., Ltd., www.smartnest.com.cn) — a bridge that converts parts to NestLib's format, drives `smartnest.dll` through `LoadLibrary/GetProcAddress`, checks a USB HID dongle (VID 3689 / PID 8762) and writes G‑code‑like text output | Proprietary (SmartNest); contains SM2 elliptic‑curve constants used for licence encoding | The only nesting entry point used by CADModule (`Nest_*` API) |
| `SmartNest.dll` (946 KB, VC6, PE timestamp 2002‑10‑21) | **NestLib** by **Geometric** (Geometric Software Solutions, India) — "Nestlib Development Version", "VERSION : 3.0, DATE : 30 JUN 95", unix path `/mna/usr/home/nestlib/tbom` | Proprietary (Geometric/HCL NestLib), hardware‑lock protected ("Can not find the hardware lock") | True‑shape nesting kernel (loaded dynamically by AutoNest as `smartnest.dll`) |
| `splineAnalyerVc100.dll` (1.0 MB, VC10, 2020‑03‑17) | In-house wrapper "OpenNurbs_Dll_v1.3.14" around **openNURBS** (McNeel) — exports two factory functions returning `ISpline2DAnalyer` / `ISpline3DAnalyer` interfaces | openNURBS is MIT‑licensed; wrapper proprietary | NURBS/spline evaluation & discretisation for DXF SPLINE and text Béziers (imported by CADModule) |
| `CircleFitDLL.dll` (70 KB, 2015‑03‑05, no CRT imports) | Small in-house **least-squares circle/arc fitting** library (`CCircleFit::mainFit`) with 4 tuning globals | Proprietary in-house | Used by `Dxf2Grp.dll` and `AutoNest.dll` ("复杂曲线拟合压缩" = fit/compress complex curves before nesting) |
| `Module/CADModule.dll` (1.3 MB, VC10, 2025‑06‑16) | The **CAD engine plug‑in** of MainApp: contains the in-house DXF adapter (`CDxfParse`), **HPGL/PLT parser** (`PltParse::CPltFileParse`), **G‑code parser** (`CGCodeFileParse` with an embedded BNF grammar), text‑to‑outline via GDI `GetGlyphOutlineW(GGO_BEZIER)`, offset/compensation (`CSegOffset`), micro‑joints, bridges, lead‑ins, sorting, smoothing, nesting UI glue (`CSCNest`, `CNestResult`, `CShareEdge`) | Proprietary in-house | Everything geometric that is not in the six DLLs above |

**Key structural finding (EVIDENCE, `objdump -p`):** `MainApp.exe` imports **none** of the six geometry DLLs. It loads plug‑ins from `\Module\*.dll` (UTF‑16 string in MainApp) and `Module/CADModule.dll` imports `DxfParseDllvc100.dll`, `splineAnalyerVc100.dll` and `AutoNest.dll`. `AutoNest.dll` imports `Dxf2Grp.dll` and `CircleFitDLL.dll` and *dynamically* loads `smartnest.dll`. `Dxf2Grp.dll` imports `CircleFitDLL.dll`. See §2.

There is **no** DWG import in the normal CAD path (only DXF, PLT, G‑code/NC and the native `.chf`); DWG support exists only inside the nesting bridge via the ODA library. There is **no bitmap/raster import or engraving** anywhere (no `potrace`‑like strings, no image entity handling; GDI+ is used only to *write* JPEG thumbnails).

---

## 2. Load / dependency graph

EVIDENCE — `objdump -p` "DLL Name" tables:

```
MainApp.exe (MFC100u, BCGCBPRO2210u100, OPENGL32, GLU32, gdiplus, HID, SETUPAPI, WINHTTP, …)
 └─ LoadLibrary "\Module\*.dll"   (UTF‑16 string @ MainApp.exe.utf16 line 5456)
     ├─ Module/CADModule.dll   (imports: OPENGL32, mfc100u, MSVCR100/MSVCP100, GDI32, SHLWAPI, PSAPI,
     │                          DxfParseDllvc100.dll, splineAnalyerVc100.dll, AutoNest.dll)
     │    ├─ DxfParseDllvc100.dll   (imports KERNEL32, MSVCP100, MSVCR100 only)
     │    ├─ splineAnalyerVc100.dll (imports RPCRT4, KERNEL32, USER32, GDI32, MSVCP100, MSVCR100)
     │    └─ AutoNest.dll           (imports CircleFitDLL.dll, Dxf2Grp.dll, MFC42, MSVCRT, MSVCP60, KERNEL32, USER32, GDI32, ADVAPI32)
     │         ├─ Dxf2Grp.dll        (imports CircleFitDLL.dll, MFC42, MSVCRT, MSVCP60, KERNEL32, USER32, GDI32, ADVAPI32, ole32)
     │         │    └─ CircleFitDLL.dll (imports KERNEL32 only)
     │         ├─ CircleFitDLL.dll
     │         ├─ LoadLibraryA("smartnest.dll")  → SmartNest.dll (imports USER32, KERNEL32 only)   [.data 0x10088434]
     │         ├─ LoadLibraryExA("\hid.dll") / "\SetupApi.dll"  → USB dongle                      [.data 0x10089234 / 0x100892b8]
     │         └─ (Dump/…, dat/tmpnst1 temp dirs)
     ├─ Module/ControlModule.dll, LangModule.dll, LogModule.dll, NCModule.dll, ParaModule.dll (no geometry imports)
```

CADModule's symbol imports from the three DLLs (EVIDENCE, `objdump -p Module/CADModule.dll`):

* from `DxfParseDllvc100.dll`: `newDxfFileParse` (2 call sites in CADModule, disassembly lines 6186 and 9196)
* from `splineAnalyerVc100.dll`: `?newSpline2DAnalyer@@YAPAVISpline2DAnalyer@@XZ` (10 call sites)
* from `AutoNest.dll`: `??0HepinDLL@@QAE@XZ`, `??1HepinDLL@@QAE@XZ`, `Nest_CheckLock`, `Nest_GetId`, `Nest_SetCalNew`, `Nest_EncodeNew`, `Nest_EncodeByKey`, `Nest_SetNestPara`, `Nest_SetPartPara`, `Nest_WritePartData`, `Nest_WriteSheetData`, `Nest_RegProgressCB`, `Nest_AutoNest`, `Nest_GetErrorCode`, `Nest_ReadNestNum`, `Nest_ReadPartNumbyNest`, `Nest_ReadNestResult`, `Nest_Release`

Notable: the VC6‑era DLLs (`Dxf2Grp`, `AutoNest`) require `MFC42.DLL`, `MSVCRT.dll`, `MSVCP60.dll`, which are *not* shipped in the package (they rely on the OS‑provided copies). The rest of the app is VC10 (`mfc100u`, `msvcr100`, `msvcp100` shipped in `SRC`).

---

## 3. Per-DLL analysis

### 3.1 `Dxf2Grp.dll` — ODA DWGdirect 2.3.1 wrapped for nesting

**Identity (EVIDENCE)**

* VS_VERSION_INFO (`.rsrc` @ 0x106bd186…): FileDescription "Dxf2Grp DLL", InternalName "Dxf2Grp", LegalCopyright "版权所有 (C) 2009" [All rights reserved (C) 2009], ProductVersion 1,0,0,1, language 0804 (zh‑CN). No CompanyName.
* UTF‑16 strings: `DWGdirect 2.3.1.0`, `DWGdirect`, `DwgDirect`, `Copyright 2003-2007, Open Design Alliance Inc. ('Open Design')`, `Open Design Alliance ACIS Builder` (×4, with ACIS 4.00/7.00 build stamps 2005‑2007), `Internal error in Freetype font library`, `Loading DWG file...`, `Loading DXF file...`, `Unknown DXF file version. Trying to treat as R12.`, `Dwg file is encrypted`, `Dwg file needs recovery`.
* Autodesk ASM stamps (`16 Autodesk AutoCAD 17 ASM 6.0.4.7009 NT 24 …2004`) are ACIS‑SAT header templates carried by the ODA modeler, not evidence of RealDWG.
* Supported drawing versions (UTF‑16 table): `AC1001 … AC1014, AC1015 (R2000), AC1018 (R2004), AC1021 (R2007)`, plus `AC1500` (ODA's internal "R2007 beta"). **No `AC1024` (R2010) or later** — consistent with a 2007‑vintage DWGdirect 2.3.
* Full set of registered object classes present (`AcDbLine`, `AcDbArc`, `AcDbCircle`, `AcDbEllipse`, `AcDbSpline`, `AcDbPolyline`, `AcDb2dPolyline`, `AcDb3dPolyline`, `AcDbHatch`, `AcDbMText`, `AcDbText`, `AcDbBlockReference`, `AcDbMInsertBlock`, `AcDbRegion`, `AcDb3dSolid`, `AcDbBody`, dimensions, `AcDbRasterImage`, `AcDbXline`, `AcDbRay`, `AcDbLeader`, `AcDbMline`, `AcDbOle2Frame`, `AcDbDgnReference`, `AcDbDwfReference`, … 140+ names) — this is the whole DWGdirect object model, not what the wrapper converts.
* SHX font names embedded (`simplex.shx`, `gbcbig.shx`, `chineset.shx`, `extfont.shx`, `whgtxt.shx`, …) plus registry lookups `Software\Microsoft\Windows nt\CurrentVersion\Fonts` — the ODA text engine, used only for text-to-geometry if the wrapper ever explodes text (not observed).
* Statically linked: the DLL imports only Win32/MFC42/CRT + `CircleFitDLL.dll` (no `TD_*.dll`).

**Exports (EVIDENCE, 65 names; `objdump -p`)** — the wrapper API. Mangled names decode to:

| Export (demangled) | Notes |
|---|---|
| `CDrawing::CDrawing()/~CDrawing()` | main object |
| `bool CDrawing::OpenFile(const CString&)` | opens `.dwg`/`.dxf` (strings `.dwg`, `.dxf`, `%s\grptmpview.dwg`) |
| `bool CDrawing::Save()`, `void SaveNewFile(const CString&)` (private), `void GetNewFileName(CString&)` (private) | write-back; `%s(带标记).dwg` / `%s(带标记).dxf` [“(with marks)”] output names |
| `void CDrawing::ExportGrp2File(const CString&, OdDb::SaveType, OdDb::DwgVersion)` | **DWG/DXF export** using ODA enums (SaveType = kDwg/kDxf/kDxb, DwgVersion) |
| `void AddLine(OdGePoint3d, OdGePoint3d)`, `void AddCircle(OdGePoint3d, double, OdDbObjectId, bool)` | add primitives to the open DB |
| `int CaluChain(bool)` | contour chaining ("CaluChain Time: %d") |
| `void CreatePart()`, `void Convert2Part()` (private), `CPart2* GetPart2()` | DB → `CPart2` (closed contours) |
| `void CreateSpecLay()`, `OdDbObjectId GetSpecLayerId()`, `void LoadLineType()`, `void SetGrpLayer(CArray<CString>&, CArray<CString>&, CArray<CString>&)`, `void GetAllLayer(CArray<CString>&)` (private) | layer handling: three layer lists = cut / scribe / auxiliary |
| `bool IsQgLayer(const CString&)`, `bool IsHxLayer(const CString&)`, `bool IsAuxLayer(const CString&)` | Qg = 切割 (cut), Hx = 划线 (scribe/mark line), Aux = 辅助 (auxiliary) — confirmed by the dialog labels `切割层:` `画线层:` `辅助线层:` and the persisted keys `[m_strLayerQG]=`, `[m_strLayerHX]=`, `[m_strLayerAux]=` (`.data` 0x10680d14…) |
| `bool IsZeroEnt(const OdSmartPtr<OdDbEntity>&)`, `bool IsAntiClockwise(double×6)` | geometry helpers |
| `void SetPara(bool, double, bool, bool, double, bool, double, double)` | 8 conversion parameters (see below) |
| `void SetCalcuTol(double)`, `void SetMarkCircleRadius(double)`, `double GetMarkCircleRadius()` | tolerance and "mark circle" radius (used to draw red‑circle markers on unclosed contours: `标示非封闭轮廓(红色圆圈带叉)` [mark unclosed contours (red circle with cross)]) |
| `void SetBatProcess(bool)`, `void SaveBatInfoToFile()`, `CString GetDataProcessInfo()` | batch import of parts; writes `[batpara::begin] … [batpara::end]` and `[partpara::begin]/[partpara::end]` blocks |
| `int Dxf2Grp(const CString&, CPart2*&, bool, CPart2*&, bool)` | one file → part |
| `bool Dxf2Grps(CString*, int, CPart2**)`, `bool Dxf2Grps_CanAllOpen(CString*, int, CPart2**)` | batch (used by AutoNest: it imports `Dxf2Grps`) |
| `CPart2`, `COuter2`, `CElement2`, `CLine2`, `CArc2`, `CCircle2`, `CDPoint2`, `CProject2` ctors/dtors/vtables, `void CPart2::Release()` | the output model: a part = list of outers (closed loops) made of line/arc/circle elements only |
| `double s_dblCompressLen` (data export) | global "compression length" used by AutoNest too |

**Persisted parameter keys (EVIDENCE, `.data` 0x10680bd8–0x10680dcc, GBK):** `[m_bOutputInfo]`, `[m_dblMinDia]`, `[m_dblFitTol]`, `[m_bFit]`, `[m_bExportNoClose]`, `[m_bAllRestToQG]`, `[m_nNameReg]`, `[m_strLayerQG]`, `[m_strLayerHX]`, `[m_strLayerAux]`, `[m_dblCurveTol]`, `[m_dblTol]`, `[m_bTipSameEnt]`, `[m_dblMarkRadius]`, `[m_bMarkChain]`, `[m_bExtendByGeo]`, `[m_dblForceTol]`, `[m_bForceClose]`, plus per‑part attributes `[part_name]`, `[graph_no]`, `[comp_name]`, `[comp_dwgno]`, `[mat_name]`, `[mat_brand]`, `[density]`, `[thickness]`, `[IsDirection]`, `[AngleStep]`, `[NetWeight]`, `[Priority]`, `[Bak]`, `[count]`. Defaults seen nearby: `Q235`, `钢板` [steel plate], `切割零件` [cut part]. Config file name: `\smartnest.xjy` (0x10680de0). Also `SmartNestCAD没有启动！` [SmartNestCAD is not running!], `smartnesteditdwg:`, `SmartNestCADMainFrm` — the wrapper can hand a DWG to a separate "SmartNestCAD" editor window (not shipped).

**Dialog resources (EVIDENCE, `.rsrc` UTF‑16 0x106bd47e…)** — the wrapper contains its own MFC dialogs ("零件导入" [Part import], "零件批量导入" [Batch part import], "图形转换" [Graphic conversion]) with fields: 零件图号 [part drawing no.], 零件名称 [part name], 材料名称 [material], 密度 [density], 材质 [material grade], 板厚 [thickness], 有纹理要求(方向性) [grain direction requirement], 旋转步角 [rotation step angle], 零件净重(Kg) [net weight], 排料优先级 [nesting priority], 备注 [remarks], 数量 [quantity], 工号 [job no.], 部件号 [component no.]; conversion options: 标示非封闭轮廓 [mark unclosed contours], 标示重合实体(红色叉叉) [mark overlapping entities (red X)], 复杂图形拟合压缩 / 复杂曲线拟合压缩 [fit & compress complex curves], 轮廓强制封闭 [force‑close contours], 按几何对象延伸封闭 [close by extending geometry], 图层分配 [layer assignment], 轮廓封闭精度(0~1) [contour closing tolerance], 标记大小(半径) [marker size (radius)], 数据计算精度 [calculation precision], 曲线离散精度(0~1) [curve discretisation precision], 输出切割层的不封闭轮廓 [output unclosed contours of the cut layer], 压缩精度(0~1) [compression precision], 最小轮廓大小(>=0) [minimum contour size], 默认除画线和辅助线层以外的层都为切割层 [by default every layer except scribe & aux layers is a cut layer].

Statistics strings: `原图中发现%d个封闭轮廓和%d个不封闭轮廓` [found %d closed and %d unclosed contours in source], `转换后输出%d个封闭轮廓和%d个不封闭轮廓` [after conversion output …], `图形压缩比=%.2f%%` [graphic compression ratio], `有不封闭轮廓` [there are unclosed contours].

**INFERENCE (high):** `Dxf2Grp.dll` is the *SmartNest* vendor's part importer (same vendor as `AutoNest.dll`; the ".xjy" config and "SmartNestCAD" strings are theirs). Its role is to turn arbitrary DWG/DXF into closed loops of lines/arcs/circles (splines/ellipses discretised then arc‑fitted via `CircleFitDLL`) suitable for NestLib. In Mlaser it is reached only through `AutoNest.dll` (`Dxf2Grps` import), i.e. only when the user nests parts loaded from files; the normal drawing import does not touch it.

**INFERENCE (medium):** `SetPara(bool bFit, double dblFitTol, bool bExportNoClose, bool bAllRestToQG, double dblMinDia, bool bTipSameEnt, double dblCurveTol, double dblTol)` — matched by type pattern to the persisted keys; ordering not verified from code.

### 3.2 `DxfParseDllvc100.dll` — in-house DXF text parser v1.4.92

**Identity (EVIDENCE):** PDB path `D:\project\CAM\dxf文件解析\DxfParseDllvc100-v1.4.92\Release\DxfParseDllvc100.pdb` (`.rdata` 0x10019928; "dxf文件解析" = "DXF file parsing"). No version resource, no copyright string. Only CRT imports. PE timestamp 2024‑01‑13.

**Export (EVIDENCE):** a single C function `newDxfFileParse` (factory). RTTI shows the returned object implements `IDxfFileParse` (implemented by `CDxfFileParse`).

**Object model (EVIDENCE, RTTI `.?AV…`):**

* Sections: `CDxfHeaderSection`, `CDxfClassesSection`, `CDxfTablesSection`, `CDxfBlocksSection`, `CDxfEntitiesSection`, `CDxfObjectsSection` (base `CDxfSection`).
* Entity readers (`…EntityRW`): `CSegmentEntityRW` (LINE), `CArcEntityRW`, `CCircleEntityRW`, `CEllipseEntityRW`, `CLWPloyLineEntityRW` [sic], `CPloyLineEntityRW` [sic], `CSplineEntityRW`, `CPointEntityRW`, `CXLineEntityRW`, `CRayEntityRW`, `CTextEntityRW`, `CMTextEntityRW`, `CInsertEntityRW`, `CTraceEntityRW`, `CSolidEntityRW`, `C3DFaceEntityRW`, `CBlockEntity`.
* Output geometry (`namespace DxfParse`): `CDxfSegment2d`, `CDxfArc2d`, `CDxfCircle2d`, `CDxfEllipseArc2d`, `CDxfLWPloyLine2d`, `CDxfNubrs2d` [NURBS], `CDxfPoint2d`, `CDxfXLine2d`, `CDxfRayLine2d`, `CDxfText`, `CDxfMText`, `CDxf3DFace`, `CDxf4Corner` (SOLID/TRACE), `CDxfSolid`, `CDxfTrace`, exception `CDxfFileParseException`.
* Entity keyword table (`.rdata` 0x10018250…): `MTEXT TEXT INSERT XLINE POLYLINE SPLINE LWPOLYLINE ELLIPSE CIRCLE LINE POINT TRACE SOLID 3DFACE`, plus `REGION VERTEX HELIX SEQEND` (0x100192c4…) and structural `SECTION ENDSEC ENTITIES BLOCKS TABLES HEADER OBJECTS CLASSES BLOCK ENDBLK STYLE LAYER ENDTAB`, header vars `$ACADVER $UCSORG $UCSXDIR $UCSYDIR`, subclass markers `AcDbEntity AcDbMText AcDbText AcDbPoint AcDbRay AcDbXline AcDbLine AcDbCircle AcDbArc AcDbEllipse AcDbPolyline AcDbSpline AcDbSymbolTable AcDbSymbolTableRecord AcDbLayerTableRecord AcDbTextStyleTableRecord`, linetype `Continuous`, `Standard`, `Embedded Object`, and `dxfTemplate.dxf` (a template used when *writing*).
* Error strings (UTF‑16, `.rdata` 0x10018f80…): `文件写入失败` [file write failed], `内存不足` [out of memory], `出现无效块` [invalid block encountered], `出现非法数据` [illegal data encountered], `文件格式错误` [file format error], `文件为空` [file is empty], `文件被损坏` [file corrupted], `文件路径无效或文件被占用` [invalid path or file in use], `未知错误` [unknown error], `无异常` [no exception]. The same 10 strings are duplicated inside `CADModule.dll` (0x1010f8d0…) together with `memory error occur in read DXF!`.
* Output formatting `%12.10f` (0x100196f4) → the DLL can also **write** DXF (used for `dxfTemplate.dxf`‑based export; MainApp offers `(*.dxf)|*.dxf||` save filters).

**INFERENCE (high):** an ASCII‑DXF‑only reader/writer (no binary DXF, no DWG), handling R12–R2018 group‑code syntax generically; it reads geometry entities, blocks/INSERT (transform applied in CADModule's `CDxfParse`), TEXT/MTEXT (text content only; rendering is done in CADModule), and SPLINE (as NURBS handed to `splineAnalyerVc100`). HATCH, DIMENSION, IMAGE, LEADER, MLINE are **not** in the keyword table and are ignored.

### 3.3 `AutoNest.dll` ("HePin DLL") — SmartNest bridge, dongle and I/O glue

**Identity (EVIDENCE):** VS_VERSION_INFO: FileDescription "HePin DLL", InternalName/OriginalFilename "HePin", LegalCopyright "版权所有 (C) 2014"; a dialog "用户授权" [User authorisation] with fields 用户编码 [user code], 原授权码 [old licence code], 新授权码 [new licence code] and the text "感谢您使用SmartNest智格套料软件 … 武汉智格科技有限公司 公司网址 www.smartnest.com.cn 服务邮箱 smartnest_service@163.com 联系电话 027-87770198,15827029286" [Thank you for using SmartNest nesting software … please check the system date … obtain a new authorisation code from Wuhan SmartNest Technology Co., Ltd.]. VC6/MFC42 build, PE timestamp 2023‑04‑28.

**Exports (EVIDENCE, 26) with calling convention derived from the `ret imm16` of each body (`objdump -d`, scratch `autonest_d.txt`):** all `Nest_*` functions are **`thiscall` members of class `HepinDLL`** (they save `ecx` as `this` in their prologue; exported with undecorated names via a .def file). `HepinDLL` is a tiny object (CADModule allocates it on the stack at `[ebp-0x4d]`, so ≤ ~0x40 bytes).

| Export | Stack args (dwords) | Signature (INFERENCE from bodies / call sites; confidence) |
|---|---|---|
| `HepinDLL::HepinDLL()` / `~HepinDLL()` | 0 | ctor/dtor |
| `Nest_CheckLock` | 0 → `bool` | dongle presence check; calls the HID reader (`0x1006b290`, guarded by semaphore `"ex_sim"`) with two constants `0x012c4ca4` / `0x7ec54af5` or `0x374d0e51`; caches result in `ds:0x1008814c`; returns 0 on failure → CADModule returns error `0x97` (151) (high) |
| `Nest_GetId(void* out)` | 1 | reads dongle/user id (medium) |
| `Nest_SetCalNew(int)` | 1 | selects new licence‑code calculation (medium) |
| `Nest_EncodeNew(a, b)` | 2 | generate authorisation code (SM2 curve params present, see below) (medium) |
| `Nest_EncodeByKey(a, b, c)` | 3 | as above with key (medium) |
| `Nest_SetNestPara(int idx, double value)` | 3 | see table in §5.2 (high) |
| `Nest_SetPartPara(int mode, int a, int b)` | 3 | mode 0/1/2 → three internal setters on the global nest object `0x10089558` (high) |
| `Nest_WritePartData(p1, p2, int n, p4)` | 4 | feeds one part (contours) ; returns int status (0 = ok) (high for arity) |
| `Nest_WriteSheetData(struct of 10 dwords)` | 10 | CADModule passes `{int idx?, int bRect, int count=1, double W, double H, …}` copied from `[ebp-0x8c]` (`rep movs`, 0xa dwords) (medium) |
| `Nest_RegProgressCB(void (*cb)(...))` | 1 | stores callback at `ds:0x10089550` (high) |
| `Nest_AutoNest(int)` | 1 → `bool` | runs nesting via global object `0x10089558` (high) |
| `Nest_GetErrorCode()` | 0 → `int` | see §5.4 error codes (high) |
| `Nest_ReadNestNum()` | 0 → `int` | number of result sheets (CADModule checks `1 ≤ n ≤ 0x4e20`) (high) |
| `Nest_ReadPartNumbyNest(int sheet)` | 1 → `int` | parts placed on sheet; CADModule allocates `n*0x30` bytes (high) |
| `Nest_ReadNestResult(struct* out, int sheet)` | 2 | fills `{int a; …; int nParts @+0x10; int @+0x14; elem* @+0x18}`; each element is **48 bytes**: `int partIndex @0`, then doubles @+8, +0x10, +0x18, +0x20, +0x28 (INFERENCE: x, y, rotation, and two more, e.g. mirror/scale) (medium) |
| `Nest_Release()` | 0 | frees results |
| `HepinDLL::Nest_GetOddCountByNest(int)`, `HepinDLL::Nest_ReadOddData(stNestOdd&, int)` | 1 / 2 | "odd" = 余料 remnant/leftover sheets (not imported by CADModule) (medium) |
| `CDPoint2`, `CPart2` copy‑ctor/assign/vtable | — | re‑exports of the Dxf2Grp part model |

**NestLib binding (EVIDENCE):** function `0x100534ab` does `LoadLibraryA("smartnest.dll")` then 96 × `GetProcAddress` storing the pointers at `this+0x08 … this+0x188` in the order of the `.data` name table (0x10088444…): `NOpenNestLib, NStartSettingCallBacks, NSetErrorHandlerCB, NSetProgressFunctionCB, NSetNewPartGeomHandleCB, NSetNewSheetGeomHandleCB, NSetNestedSheetResultCB, NSetNestedOrderResultCB, NEndSettingCallBacks, NStartNewPartList, NStartSetParams, NEndSetParam, NAddPartInfo, NEndNewPartList, NStartNestOrder, NAddSheetInfo, NNestOrder, NSetStopNesting, NCloseNestLib, NSetApplyBestNestingDirection, NSetGuillotineCut*, NDwgGeomStart … NDwgEndGetGeom (geometry feed), NSetResolution/NInqResolution, NSetClearance/NInqClearance, NSetFitPartInPart, NSetEnableTurboMode, NSetConnectingDistance, NSetFlatEndParams, NInq* (results), NOffset* (profile offsetting), NPart* (utilities), NStartGroup/NAddGroupMember, NSetPartSeqMethod`. Then `NOpenNestLib(<CString path>, 1, errorCB=0x1005424f)` (matches NestLib's own trace format `NOpenNestLib(%s, %d, %d);`), and the seven callbacks are registered (`0x10054168`).

**Temp/exchange files (EVIDENCE, `.data` 0x10086a1c…):** `未排完零件图号.txt` [drawing numbers of parts not nested], `尺寸大于板材的零件.txt` [parts larger than the sheet], `special_sheet_for_2nd_nest.sht`, `special_py_for_2nd_nest.py`, `sheet_used.txt`, `nest_output_order.tmp`, `nest_output_sheet.%d`, `nest-%d.dxf`, `NONAME.SHT`, `autocut.tpy`, `打不开数据文件: ` [cannot open data file], `找不到 … 文件！` [file … not found]. NestLib input geometry is written in its own text format: `LINE %lf,%lf,%lf,%lf,0`, `ARC %lf,%lf,%lf,%lf,%lf`, `CIRCLE %lf,%lf,%lf,0,0`, sheet rectangle `LINE 0,0,%lf,0,0 / LINE %lf,0,%lf,%lf,0 / …` (0x10087018…). Result export as DXF R12 POLYLINE/VERTEX/SEQEND/CIRCLE/INSERT with a `TABLES/STYLE` entry using `SimFang.ttf` (0x10087af0–0x10087ecc) and as NC text `N%d…G01X%-.3lfY%-.3lf`, `G02/G03 X Y I J` (0x1007a100…, 0x10088250…). Debug path leftover: `D:\FE\异形dll\HePin新\Debug\dat\test1.dxf` ("异形dll" = "free‑form‑shape dll").

Nesting direction strings (0x10086988…): `水平方向(从左往右)` [horizontal, left→right], `竖直` [vertical], `自动(根据排样效果)` [auto, by nesting result], `各自` [each individually], `矩形板材` [rectangular sheet], `钢板虚拟零件` [virtual steel‑plate part], `高精度` / `低精度` [high / low precision], `自动设定` [auto].

**Licensing/dongle (EVIDENCE):** `HID#Vid_3689&Pid_8762`, registry `SYSTEM\ControlSet001\Control\DeviceClasses\{4d1e55b2-f16f-11cf-88cb-001111000030}`, dynamic `HidD_GetFeature/SetFeature/GetAttributes…`, `SetupDiGetClassDevsA…` (0x100890a0–0x100892b8); vendor string `PWDKeyCo.,` (0x10075e2c). Four 64‑hex‑digit constants at 0x10088f3c… are exactly the **SM2** (GB/T 32918) recommended curve parameters: `p‑derived a = FFFFFFFEFFFF…FFFC`, `b = 28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93`, `Gx = 32C4AE2C1F1981195F9904466A39C9948FE30BBFF2660BE1715A4589334C74C7`, `Gy = BC3736A2F4F6779C59BDCEE36B692153D0A9877CC62A474002DF32E52139F0A0`; ~250 16‑hex‑digit tokens at 0x10080856… (INFERENCE medium: a licence/ID table). The same VID/PID is the "USBKey" dongle plugged into the source PC. Detailed dongle protocol belongs to the licensing analyst; what matters here is that **nesting is gated by this dongle** (`Nest_CheckLock` → error 1 "没找到加密狗" [dongle not found]).

### 3.4 `SmartNest.dll` — Geometric NestLib 3.x kernel

**Identity (EVIDENCE):** strings `Geometric`, `Nestlib Development Version`, `NESTLIB_MODE`, `NESTLIB_TIMER`, `nestlib.err`, `/mna/usr/home/nestlib/tbom`, `VERSION : 3.0, DATE : 30 JUN 95`, `c:\tmp\napidump.log`, `NInqGetBuildInfo`, `Can not find the hardware lock. Please attach and execute Nesting`, `This version does not support multiple torch nesting.`, `You don't have permission to use %s` (module licence gate), `Multi Torch module`, `Common Edge module`, `Common Punch module`, `Grid Fit module`. PE timestamp 2002‑10‑21, no version resource, imports only USER32/KERNEL32. 226 exports, all `N*` C functions (plus a few C++ helpers `partGeomLib::Area/Perimeter`, `CmapDim`, `Dvector2`, `clusterElement`, `optFlags`, `optStruct`, `partToBeNested`, `partType`, `partSpecificCutterDia`, `runNestCommon`, `runNestWin`, `nestErrorInternal`, `DebugPrint`, `NTestApi`).

**Capability inventory from strings (EVIDENCE = the upper‑case keyword table in `.rdata`, and the message texts):**

| Feature | Evidence |
|---|---|
| True‑shape nesting with rotation list | `ROTATION_ANGLE`, `ROTATION_ANGLE_INCR`, `ROTATION_ANGLE_METHOD`, `BY_ANGLE_LIST`, `ANGLE_LIST`, `STEP_ANGLE`, `ORIENTN_ANGLE_INCR`, `NDefineAngleList`, `Part : %s. Step angle of rotation is too big/small.`, `Too many angular orientations.`, `Orientation : %5.2lf Degrees` |
| Mirroring | `IF_MIRROR`, `MIRROR_TYPE`, `MIRROR_HALF_COUNT`, `Can not place the part in mirrored orientation.` |
| Part‑in‑part (hole filling) | `FIT_PART_IN_PART`, `PREF_HOLE_FILL`, `NSetFitPartInPart`, `NPartCheckPartInPart`, `HOLES` |
| Clearance / kerf / resolution | `CLEARANCE`, `CUTTERDIA`, `DEFAULT_CUTTER_DIAMETER`, `PART_CUTTER_DIA`, `RESOLUTION`, `NSetClearance`, `NSetCutterDiameter`, `NSetResolution`, `Part clearance %lf is too small.`, `Resolution %lf is too small.` |
| Sheet margins / collar | `COLLAR`, `COLLAR_LEFT/RIGHT/TOP/BOTTOM`, `IF_NONUNIFORM_COLLAR`, `Collar width is too small.` |
| Grain direction | `GRAIN`, `Invalid grain direction.` |
| Common edge (共边) | `COMMON_EDGE_PROXIMITY`, `COMMON_EDGE_PROX_ANGLE`, `MIN_COMMON_EDGE_LEN`, `IF_COMMON_EDGE_PREFER`, `NoCommonEdges : %d`, `CommonEdgeX1..Y2` |
| Common punch / common tool | `COMMON_PUNCH`, `COMMON_TOOL` |
| Guillotine / shear (rectangular) nesting | `NSetGuillotineCut*`, `GUILLOTIN_CUT`, `SHEAR_*`, `STRIP_METHOD`, `STRIP_PARAMS`, `IF_RECTANGULAR`, `IfRectangular : %d` |
| Grid fit / single‑part grid | `IF_SINGLE_PART_GRID`, `Grid Fit module`, `Incorrect value for grid type for circles.` |
| Clusters / groups (pre‑paired parts) | `CLUSTER`, `CLUSTER_CUTOFF_UTIL`, `NEST_CLUSTERS_FIRST`, `NStartGroup/NAddGroupMember/NEndGroup` |
| Part priority, min/max quantity, fill last sheet | `PRIORITY`, `NChgPartMinQty/MaxQty`, `FILL_LAST_SHEET`, `NSetFitMaxOnLast`, `NEST_FILLERS_UPTO_NESTED_HT` |
| Local area / remnant nesting | `NEW_LOCAL_AREA`, `IF_PREFER_LOCAL_AREA`, `NSetCurrentSheetLocalArea`, `Local area point %lf %lf is outside sheet bound` |
| Master plate / remnant generation | `MASTER_PLATE`, `Trying for master plate.`, `Expected utilisation %lf for the master plate is incorrect.` |
| Multi‑torch | `MULTI_TORCH*`, `MAXIMUM_TORCHES`, `TORCH_DISTANCE` (disabled in this build) |
| Cut sequencing / lead‑ins | `CUTTING_SEQUENCE`, `IF_GENERATE_CUT_SEQUENCE`, `LEADIN/LEADOUT/LEADENTS`, `NSetOffDistLeadins`, `NSetPartSeqMethod`, `NStartNestOrder` |
| Profile offsetting utility | `NOffsetStart/…/NOffsetEnd`, `Part : %s. Can not offset.` |
| Quality levels / turbo | `NEST_QUALITY_TYPES`, `QUALITY1..5`, `COARSE/MEDIUM/FINE`, `TURBO_MODE_TYPE`, `NSetEnableTurboMode` |
| Limits | `Maximum %d part types can be nested in an order.` (Mlaser's `mf523` says 50), `Overflow. Too many entities/profiles/parts/sheets…`, `Layer table entries exceed limit.` |
| Reporting | `GLOBAL UTILIZATION : %lf percent.`, `LOCAL UTILIZATION`, `NESTED RECTANGLE : %lf X %lf.`, `SHEET RECTANGLE`, `TotalCutArea`, `TotalPartArea`, `NetSheetArea` |
| Input formats | own text `.py` (part) / `.sht` (sheet) / `.Nip` (pre‑processed part cache: `Part %s not found. Cannot open .Nip file`), config sections `PARAMETERS`, `PARTLIST`, `SHEETLIST`, `SETTINGS`, `DIRECTORIES`, `FILES`; also reads DXF (`ENTITIES/BLOCKS/TABLES/LINE/ARC/CIRCLE/POLYLINE/VERTEX/SEQEND/INSERT/ATTRIB/ATTDEF/DIMENSION/MTEXT/TEXT/SOLID/TRACE/POINT/VPORT/LTYPE/STYLE/APPID/DIMSTYLE/BLOCK_RECORD`), writes DXF and AutoCAD **SCR** scripts (`ifGenerateSCR`, `layer new %s color %d`, `line %lf,%lf %lf,%lf`, `arc c %lf,%lf …`, `NSetSCRGenerateParams`) |
| Languages | `ENGLISH DANISH DUTCH FINNISH FRENCH GERMAN HINDI ITALIAN NORWEGIAN PORTUGUESE SPANISH SWEDISH` message files |

**INFERENCE (high):** this is a genuine 2002 build of Geometric's NestLib (a commercial SDK later sold by Geometric Ltd/HCL). Its API is the well‑known `NOpenNestLib … NNestOrder … NCloseNestLib` state machine ("Invalid state. Can not … in this state." messages). It is a **true‑shape (irregular) nester**, polygon‑rasterised at a user resolution (`RESOLUTION`), with rectangular/strip/guillotine modes as options.

### 3.5 `splineAnalyerVc100.dll` — openNURBS wrapper

**Identity (EVIDENCE):** PDB `\opennurbs_Dll\OpenNurbs_Dll_v1.3.14\Release\splineAnalyerVc100.pdb`; ~180 `ON_*` RTTI classes (`ON_NurbsCurve`, `ON_NurbsSurface`, `ON_Brep`, `ON_Mesh`, `ON_ArcCurve`, `ON_PolyCurve`, `ON_PolylineCurve`, `ON_BinaryArchive`, `ON_3dmObjectAttributes`, …) and openNURBS diagnostic strings (`ON_BinaryArchive::BeginRead3dmChunk() - file is damaged.`, `ON_Font::Read - get newer version of opennurbs`, `Data size in 3dm archive: %d bytes`). This is the full openNURBS toolkit (≈ Rhino 5 era, 2012–2015) statically linked; no copyright string of the wrapper author.

**Exports (EVIDENCE):** `ISpline2DAnalyer* newSpline2DAnalyer()` and `ISpline3DAnalyer* newSpline3DAnalyer()` (cdecl, `push 0x6c; call malloc; jmp ctor` → object size **0x6c = 108 bytes**; vtable at 0x100e0bb4 / 0x100e0cac; the object embeds an `ON_NurbsCurve` at +4 (its vtable 0x100dd5cc is patched from `ds:0x100f1a30`), two `ON_SimpleArray`s at +0x3c and +0x4c (control points, 16‑byte = 2 doubles each; knots), and a byte flag at +0x38).

**Interface (INFERENCE from vtable walk, stack cleanup per slot; medium):** 25 virtual slots; identical layout for 2D/3D.

| Slot | `ret n` | Likely meaning |
|---|---|---|
| 0 | 4 | scalar deleting destructor |
| 1 | 16 | `bool SetData(const vector<Pt2d>& ctrlPts, const vector<double>& knots, int flags, int degree)` — CADModule calls it as `(this, &pts, &pts+0x10?, 0, 3)`; body checks `ctrlPts.size() ≥ degree+1` (`sar 4` → 16‑byte points) |
| 2 | 16 | `bool SetFromFitPoints(...)` — builds arrays then calls slot 1 with `(…,0,3)` (degree‑3 interpolation) |
| 3–9 | 0 | getters (degree/count/IsValid/…) |
| 10 | 12 | `bool Evaluate(double t, Pt2d* out)`‑style (clamps `t` to knot domain: compares with `knots[0]` and `knots[n-1]`) |
| 11, 12 | ? (no ret found within 400 insns) | large routines: discretisation / arc fitting (INFERENCE low) |
| 13, 18, 19 | 12 | 3‑dword args (double + ptr, or 3 ptrs) |
| 14–17 | 8 | 2‑dword args |
| 20 | 48 | 12 dwords: 4 doubles + 4 ints → forwards to `ON_NurbsCurve` vtable +0xb4 (e.g. `ON_Curve::ClosestPoint`/`Split`/`GetNurbFormParameterFromCurveParameter`) |
| 21, 23 | ? | large |
| 22 | 8 | 2 dwords |
| 24 | 4 | 1 dword |

CADModule uses it in `CCreateSpline`, `CEditableSpline`, `CNurbsContour`, `CContoutSmooth` and DXF SPLINE import (`CDxfNubrs2d`), and `pd369_1 自动光顺样条曲线` [auto‑fair spline on load]. MainApp parameter `pd93 运动控制.曲线控制精度` [Run Control.Spline Precision] governs discretisation.

### 3.6 `CircleFitDLL.dll` — least‑squares circle/arc fit

**Exports (EVIDENCE):** `CCircleFit::CCircleFit()`, `void CCircleFit::mainFit(MyPoint* const pts, int n, int mode, CdlFitOut* const out)` (thiscall, `ret 0x10`, forwards to cdecl `0x100011e0`), `MyPoint(double,double)`, `CdlFitOut(char)`, and four **double globals** `Err_CircleFit`, `Len_CircleFit`, `Ang_CircleFit`, `Rmax_CircleFit` plus `int fitOutSequence`. `AutoNest.dll` imports `Rmax_CircleFit` (writes the max allowed radius); `Dxf2Grp.dll` imports the DLL as well. No strings other than CRT float literals; timestamp 2015‑03‑05.

**INFERENCE (high):** fits sequences of polyline points into arcs/lines with tolerance `Err`, minimum segment length `Len`, max included angle `Ang`, max radius `Rmax` — the "复杂曲线拟合压缩" [curve fit & compress] step that turns discretised splines/ellipses into line/arc contours for NestLib (which accepts only LINE/ARC/CIRCLE).

### 3.7 `Module/CADModule.dll` — the in‑house CAD engine (host of the above)

**Identity (EVIDENCE):** PDB `C:\Users\Michael\source\repos\CAD_head_update\sc2000-e\Release\Module\CADModule.pdb`; single export `newModuleProvider` (plug‑in factory; RTTI `IModuleProvider`, `CCADModuleProvider`, `ICADModule`, `CCADModule`). Uses boost (`boost::format`, `boost::lexical_cast`, `shared_ptr`). Version strings `1.3.23`, `1.0.5`, `2019.8.11`, `CAD_SC`, `Jarpha`.

**Importers implemented here (EVIDENCE, RTTI + strings):**

* **DXF**: `CDxfParse` adapter over `DxfParseDllvc100` (entity classes `CDxfArc2d/Circle2d/EllipseArc2d/LWPloyLine2d/MText/Nubrs2d/Point2d/Segment2d/Text` re‑declared in CADModule's RTTI).
* **HPGL/PLT**: `namespace PltParse`: `CPltFileParse`, `CPltFileRead`, `CPltInputFile`, `CPltEntity`, `CPltSegment2d`, `CPltArc2d`, `CPltCircle2d`, `CPltEllipseArc2d`, `CPltRect2d`, `CPltNubrs2d`, `CPltFileParseException`. 2‑letter HPGL mnemonics present in `.rdata`: `IN IP SC SP PU PD PA PR AA AR AT CI EA ER RA RR BR BZ PE LB DT DF PG SI` (+ `PM EP FT` etc.) — i.e. HPGL/2 incl. absolute/relative arcs (`AA/AR/AT`), rectangles (`EA/ER/RA/RR`), Bézier (`BR/BZ`), polyline‑encoded (`PE`) and labels (`LB`).
* **G‑code / NC** (`*.nc;*.txt;*.cnc;*.g` per `mf149`): `CGCodeFileParse` with an embedded BNF (UTF‑16 `.rdata` 0x101107b8…): `<line> ::= <n> <cmd>`, `<cmd> ::= <g> <Coordinate> <fs> | <m> | <lp> | <l> | <d> | <p> | <q> | <t>`, `<Coordinate> ::= <xyzijkru> <abc>`, words `X Y Z I J K R U A B C F S T D P Q M G N L LP`, comment/lexical/syntax error classes, and semantic errors `G代码格式错误/G code format is error!`, `圆弧弦长太小/Arc chord is too small!`, `圆弧半径错误/Arc radius is error!`, `M02 is not in main function`, `M17 can not in main function`, `Subfunction is redefined`, `Subfunction can not define in function`, `Sub program can not call LP instruct`, `sub-functions can not be found`, `Command is not in a function`. (`L n n` / `LP n` = subprogram call/loop; `M17` = subprogram end.) Also `Arc2SegVelK`, `\JumpAddTime.txt`, `\Log\VelDecc.txt` for the velocity planner.
* **Native `.chf`**: text format, header `scFlie` (sic), version 4/5, sections `<Begin Graphs>`, `####graph NO:n`, `<Glyphs>`, `####Gly: n`, `<End Glyphs>`, `<Crafts>`, `<PWM Control>`, `<GuideCurve Para>`, `<coolPos Para>`, `<End Crafts>`, `<End Graphs>`, trailer `eof` (files `Graph/Work1/1.chf`, `File/autosave.chf`, `File/Temp/tempGraph.chf`). Covered in detail by the file‑format analyst; listed here because it is the app's primary interchange format.
* **Laser‑interferometer files** `*.rtl;*.ren;*.pos;*.csv` (`cp5`; classes `CLIrtl`, `CLIpos`, `CLICsv`, `CLIFileBasic`) — metrology import for pitch compensation, not drawings.

**Text (EVIDENCE):** `CGlyText`, `CTextGlyphAdapter/ITextGlyphAdapter`, `COpText2GroupCmd`, `CGlFont3D`, `CWglFontBitmap`; GDI imports `CreateFontA` (called with height=`ebx`, weight `0x1f4`=FW_MEDIUM, charset from variable, pitch `0x20`), `SelectObject`, `GetGlyphOutlineW` (called **twice per glyph with `uFormat = 3 = GGO_BEZIER`**, first for size then with buffer — disassembly 0x100e6a9c/0x100e6ae9), `DeleteObject`; OpenGL `wglUseFontBitmapsW`, `glBitmap`, `glCallLists` for on‑screen labels. MainApp UI: `gp77 字体/Font`, `gp78 字号/Font NO`, `gp79 宋体/Segoe UI`, `ti0 文字高度(mm)/Font Height(mm)`, `mv20 请输入文字内容/Please input text`, `mv33 文字转曲线/Text Convert Spline`, `pd374 自动转换文字为曲线/Convert text to curves`, `mf251 精品字/Adv Text`, `mf254 广告字/Advert Text` (with `pd1810/1811` type & offset distance — INFERENCE: single‑stroke/offset "sign‑making" text), `font_MicrosoftYaHei`, `CTextInputDlg`, `CGlyphPropPanel`.

**Bitmap / raster:** none for import. MainApp imports GDI+ only for `GdipCreateBitmapFromHBITMAP/FromScan0`, `GdipSaveImageToFile`, `GdipGetImageEncoders` (JPEG thumbnails `\_tempthumbnail.jpg`, `Report\*.chf.jpg`) and `GdipDrawLineI/GdipCreatePen1/GdipSetSmoothingMode` (2‑D preview drawing). `np11 文件底图/Background Graph` and `gp81 背景图层/Bk Layer` refer to a background *layer* for imported vector graphics, not raster.

**Geometry operations (EVIDENCE, RTTI class names in CADModule):** `CSegOffset`, `COpOffsetCmd` (割缝补偿 kerf compensation: `pd506/507` type + width, `pd501/502` inner‑shrink/outer‑expand rules, `mf452 自动补偿/Auto Offset`, `pd762–765` size‑error limits), `CBridge/COpBridgeCmd` (桥接), `COpMicoLinkContourCmd/COpExplodeMicoLinkCmd` (微连 micro‑joints, `pd313–pd346`), `CUniformStartPos`, `CGuideCurve/COpGuideLineCmd` (引线 lead‑in/out, `pd315–pd322`, `pd599`), `CArcRound/COpRoundCmd` (倒圆角 fillet `pd600–602`), `CAlphaRound` (倒α角 `pd607–614`), `CCoolPoint`, `COpOverCutContourCmd` (过切 overcut, `pd323/324 缺口封口` gap seal), `CContoutSmooth/IContourSmooth/COpSmoothContourCmd` (平滑, `pd369–371`, `pd717`), `COpSplitContourCmd` (分割 / 大图自动分割 `pd532`), `COpMergeGraphCmd` (合并相连线 `pd367/368`), `COpAutoSortCmd/CSSort/CRingSort/CPathLinkerPlan` (排序 `pd465/466`, `pd1607`, `pd700–708` circle fly‑cut sort), `COpShareEdgeCmd/CShareEdge` (共边 incl. `mf250_1 C型共边/C‑Type share edge`), `COpScanGraphCmd/CGlyScan` (飞行切割 scan cutting), `CFillCircle/COpFillGrphaCmd` (圆填充 `pd725–728`), `COpMirrorCmd/COpRotateCmd/COpScaleCmd/COpTranslateCmd/COpAlignCmd/COpEditCmdArrayCmd` (mirror/rotate/scale/move/align/array `pd332–339`), `COpGroupGraphCmd/COpBreakGroupGraphCmd` (群组), `COpReverseContourCmd/COpPositiveCmd` (direction), `CContourTopTree` (inner/outer topology `pd593`), `CCutupScrap` (余料切断/scrap cut‑up), `CUnloadAngle`, `CInterpMrg`, `CVelocityPlanning/IVelocityPlanning` (imports `MotionCtrl.dll` symbols? — no: velocity planning is internal here; `MotionCtrl.dll` is not imported by any module — see open questions).

**Nesting glue (EVIDENCE):** `CSCNest`, `CNestResult`, `CNestElemBasic`, `CPartInfo`, `CSheetInfo`; MainApp side: `CBasicNestPanel`, `CPartNestPannel`, `CSheetNestPannel`, `CReslutNestPannel`, `CNestGridCtrl`, `CNestDataChangeEvent/INestDataEvent`; strings `Rect Sheet`, `_Sheet`, `Sheet_`, `Part_`, `%04u%02u%02u-%02u%02u%02u` (timestamped result names), `NestProject File|*.nestproj||`.

---

## 4. Supported import / export formats (with evidence)

| Format | Direction | Where | Evidence |
|---|---|---|---|
| DXF (ASCII, any ACADVER) | import | CADModule → DxfParseDllvc100 | `mf149` filter `*.dxf`, `FileFilter_DxfFile#标准文件#Standard file`, keyword table §3.2 |
| DXF | export | CADModule/DxfParse (`dxfTemplate.dxf`, `%12.10f`) ; nesting results `nest-%d.dxf` (AutoNest) | MainApp filters `(*.dxf)\|*.dxf\|\|`, `(*.chf)\|*.chf\|dxf` |
| DWG (R2.5–R2007) | import (nesting part import only) | AutoNest → Dxf2Grp (ODA) | `AC1001…AC1021`, `Loading DWG file...`, `Dxf2Grps` import |
| DWG/DXF | export from nesting wrapper | `CDrawing::ExportGrp2File(…, OdDb::SaveType, OdDb::DwgVersion)` | export name |
| PLT / HPGL‑2 | import | CADModule `PltParse` | `mf149` `*.plt`, mnemonics §3.7 |
| NC / G‑code (`*.nc;*.txt;*.cnc;*.g`) | import | CADModule `CGCodeFileParse` | `mf149`, BNF strings |
| NC (`*.nc;*.enc`) | export/"Select file" | MainApp (`Select file(*.nc;*.enc)…`; `.enc` = encrypted NC) | MainApp UTF‑16 filters |
| `.chf` (native "scFlie") | load/save/autosave | MainApp/CADModule | `chf File(*.chf)`, files in `Graph/`, `File/` |
| `.nestproj` | nesting project | MainApp | `NestProject File\|*.nestproj\|\|`, `FileTitle_OpenNestProjFile` |
| `.py` / `.sht` / `.Nip` / `.tpy` / `.xjy` | NestLib part/sheet/cache/config text files (internal temp) | AutoNest/Dxf2Grp/SmartNest | §3.3 |
| `.mcf` | controller firmware | MainApp (`mf173`) | `Update/MCC100_V201.52.mcf` |
| `.afb`, `.zfb`, `.efb`, `.zpf`, `.aut` | device parameter blobs (height‑controller ZF, auto‑focus AF, EtherCAT?) | MainApp (`mf438`, `mf308`, `mf623`, `zpf files`, `aut file`) | filters; not geometry |
| `.rtl/.ren/.pos/.csv` | laser interferometer data | MainApp/CADModule `CLI*` | `cp5` |
| `.xml` | parameter import/export | MainApp (`hp9`) | `File/Bk*.xml` |
| `.jpg` | thumbnail **export only** | MainApp GDI+ | `GdipSaveImageToFile`, `Report/*.chf.jpg` |
| AI / EPS / SVG / PDF / LXD / BMP / PNG | **not supported** | — | no strings (`%!PS`, `Adobe`, `curveto`, `.ai`, `.svg`, `.lxd`, `.bmp` filters absent from MainApp, CADModule and DxfParse) |

Limits: `mf154` "文件超过100M，导入失败" [file > 100 MB: import fails, use "Open"], `mv31` "图形大于机床幅面" [graphic larger than machine range → load failed], `pd373` auto‑explode DXF groups/blocks, `pd369/369_1` auto smooth on load, `pd374` auto text→curves.

---

## 5. Nesting feature set as exposed by Mlaser

### 5.1 User‑visible features (EVIDENCE: `Lang/lang.txt`, UTF‑16LE; key `#zh#en`)

* Commands: `mf460 排样/Nest`, `mf463 快速排样/Simple Nest`, `mf464 高级排样/Advanced Nest`, `mf412 排样参数/Nest Parameters`, `mf461 零件导入/Import Part`, `mf462/mf466 板材导入/添加板材 Import/Add Sheet`, `mf473 标准板材/Std sheet`, `mf420 分板导出/Export to Sheets`, `nset_UI_Result2MainView 排样结果转加工/Copy to main`, panels `np_Part/np_Sheet/np_Result/np_StdSheet/np_SheetName 残板名称 [remnant sheet name]/np_SheetNum 残板数量 [remnant sheet count]/np_NestPartCount 已排零件数`.
* Parameters (`pd_Nest_*`): `PartSpace 间距` [part spacing], `EdgeStock 留边距` [sheet edge margin], `PartRotateStepAngle 零件旋转步角` [part rotation step angle], `NestDir 排样方向` with values `L2R 从左到右` / `D2U 从下到上`, `NestPartInner 是否嵌套排样` [nest inside part holes], `ShareEdge 是否共边` [enable common edge], `ShareEdgeMinLen 最小共边边长` [min common‑edge length], `NestAccuracy 排样图形精度` [nesting geometry accuracy], `OnlyNestSelectedPart 仅排样选中零件`, `StdSheet_W/H 整板材参数 宽度/高度`, `AddPartNum/AddSheetNum 导入零件/板材数量`; also `pd766 排样参数.零件数量`, `pd767–769 板材数量/高度/宽度`.
* Related but separate from the kernel: `mf79 阵列/Array` (`pd332–338`: rows, columns, offset type, row/column spacing, row/column direction) is a simple grid copy in CADModule (`COpEditCmdArrayCmd`), and `mf250 共边/Share Edge` + `mf250_1 C型共边` is a *post‑nesting* common‑cut path generator in CADModule (`CShareEdge`), independent of NestLib's own COMMON_EDGE option.

### 5.2 Kernel parameter mapping (EVIDENCE: CADModule `0x100d33e4–0x100d355c` calls `Nest_SetNestPara(idx, double)` from `CSCNest` fields; AutoNest `Nest_SetNestPara` switch at `0x1004c8a6`)

| idx | Source field in `CSCNest` | Stored at (AutoNest) | Type / post‑processing in AutoNest | INFERRED meaning (confidence) |
|---|---|---|---|---|
| 0 | `[esi+0x48]` double | `0x10088108` | double | part spacing `pd_Nest_PartSpace` (also passed as `NSetClearance`) (medium) |
| 1 | `[esi+0x50]` double | `0x10088110` | double | edge stock `pd_Nest_EdgeStock` → NestLib COLLAR (medium) |
| 2 | `[esi+0x58]` double | `0x10088118` | **quantised to {90, 180, 270, 360}**: `v ≤ 90 → 90; ≤180 → 180; ≤270 → 270; else 360` (constants 0x10075bf0/0x10075710/0x10075be8) | rotation step angle `pd_Nest_PartRotateStepAngle` — so the shipped build only rotates parts in 90° multiples (high) |
| 3 | `[esi+0x60]` int (fild) | `0x10088120` | int | nest direction `pd_Nest_NestDir` (0 = L2R, 1 = D2U) (medium) |
| 4 | `[esi+0x68]` double | `0x10088138` | double | min common‑edge length `pd_Nest_ShareEdgeMinLen` (medium) |
| 5 | computed bool: `[esi+0x70]` (share‑edge enabled) AND all selected parts closed/valid | `0x10088148` | int | enable common edge `pd_Nest_ShareEdge` (medium) |
| 6 | `[esi+0x71]` byte | `0x10089774` | int | nest in part inner `pd_Nest_NestPartInner` (medium) |
| 7 | `[esi+0x78]` double | `0x10089780` | int → double | accuracy `pd_Nest_NestAccuracy` (maps to NestLib RESOLUTION / 高精度‑低精度) (medium) |
| 8 | (not set by CADModule) | `0x10089778` | int | reserved / only‑selected? (low) |

Sheet: `Nest_WriteSheetData({idx, isRect(byte), 1, W, H, …})` from `[edi+0x44]` (rect flag), `[edi+0x48]`/`[edi+0x50]` (width/height); non‑rectangular sheets pass contour arrays (`mf462` import sheet from drawing; `A250212_5 自动识别板材尺寸` auto‑detect sheet size by capacitive edge finding is a separate machine function). Parts: `Nest_WritePartData(outerPtr, innerPtr, count, namePtr)` then `Nest_SetPartPara(id,1,0)`, `(id,0,1)`, `(id,1,2)` (quantity / priority / rotation flags — INFERENCE low). Progress callback `0x100cc6b0` registered via `Nest_RegProgressCB`. Results: `Nest_ReadNestNum()` (1…20000 sheets), per sheet `Nest_ReadPartNumbyNest(i)` → allocate `n×48` bytes → `Nest_ReadNestResult(&buf, i)`.

Error handling in CADModule: `Nest_CheckLock()==0` → return `0x97` (151 = "dongle"), `Nest_AutoNest()==false` → `Nest_GetErrorCode()` mapped into a composite code (`code%10000 + 50000…`), `Nest_WritePartData()!=0` → `Nest_GetErrorCode()` and part index encoded (`(idx+0x4e21)*1000 + code`); `-2` → `0x67`.

### 5.3 Rectangular vs true‑shape

* **True‑shape**: the kernel is NestLib (polygon‑based, raster resolution); geometry fed as LINE/ARC/CIRCLE closed profiles (`NDwgClosedProfStart/NDwgAddLine/NDwgAddArc/NDwgAddCircle`); holes as punch profiles (`NDwgClosedPunchProfStart`); part‑in‑part via `NSetFitPartInPart`. EVIDENCE: all of these are resolved and called from `AutoNest.dll` (§3.3 pointer table + call sites).
* **Rotation**: NestLib supports arbitrary angle lists, but the shipped bridge quantises the step to 90/180/270/360 (§5.2).
* **Rectangular/guillotine**: `NSetGuillotineCut*` are resolved and called from AutoNest (call sites at disassembly lines 134073, 77084…, 23190…) — INFERENCE (medium): used for the "矩形板材"/"Simple Nest" mode.
* **Sheets/remnants**: multiple sheets per order (`NAddSheetInfo` ×4 call sites), `sheet_used.txt`, "2nd nest" special sheet/part files, `Nest_GetOddCountByNest/Nest_ReadOddData` (余料 remnants), UI `残板` [remnant sheet] names/counts. Remnant *shape* generation appears to be done by CADModule (`CCutupScrap`) rather than NestLib's MASTER_PLATE.
* **Spacing**: single global part‑to‑part clearance + sheet edge margin; kerf is handled separately by CADModule's compensation, not by NestLib's CUTTERDIA (INFERENCE medium — `NSetCutterDiameter` is *not* in the resolved pointer table).

### 5.4 Nesting kernel error codes — `SRC/排样内核错误代码.txt` (UTF‑8, full translation)

| Code | Original | English |
|---|---|---|
| 0 | 正常 | OK / normal |
| 1 | 没找到加密狗 | Dongle (USB key) not found |
| 2 | 打不开临时文件 | Cannot open temporary file |
| 3 | 排样出现不封闭轮廓 | Unclosed contour encountered during nesting (`mf497`) |
| 4 | 打不开排样临时文件 | Cannot open nesting temporary file |
| 5 | 打不开板材临时文件 | Cannot open sheet temporary file |
| 6 | 打不开py | Cannot open `.py` (NestLib part file) |
| 7 | 找不到grp | `.grp` (part group file from Dxf2Grp) not found |
| 8 | 离散失败 | Discretisation (curve → segments) failed |
| 9 | 板材尺寸太小 | Sheet size too small (`mf498`) |
| 10 | geo失败 | "geo" step failed (NestLib geometry construction: `NDwgGeom*`) |
| 11 | cut失败 | "cut" step failed (cut sequencing / NNestOrder) |
| 12 | nestout失败 | "nestout" failed (reading back `nest_output_sheet.%d` / `nest_output_order.tmp`) |
| 13 | 导入外轮廓不封闭 | Imported outer contour not closed |
| 14 | 导入存在重复的点 | Imported geometry contains duplicate points |
| 15 | 线段太短 | Line segment too short (`mf499`) |
| 100 | 内存分配失败 | Memory allocation failed (`mf500`) |

MainApp adds its own messages: `mf506 排样零件为空/No nest part`, `mf520 零件尺寸过大/Part's size is over flow`, `mf521 零件不符合内外膜规则/The part violates topology rules (Inner‑Outer)`, `mf522 添加零件时参数错误/Parameter error while adding part`, `mf523 零件种类超过50/More than 50 parts` (NestLib `Maximum %d part types`), `mf524 DLL内核错误/Nest‑DLL kernel error`, `mf501 输出结果错误`, `mf502 内核未知错误`, `mf503/504/505` out‑of‑memory variants.

---

## 6. Rendering pipeline hints (MainApp + CADModule)

* **Canvas**: legacy immediate‑mode OpenGL 1.x (`glBegin/glVertex2d/glEnd`, `glOrtho`, `glLineStipple`, `glLineWidth`, display lists `glGenLists/glCallList`, `wglCreateContext/wglMakeCurrent`, `gluLookAt`). No shaders, no VBOs.
* **On‑screen text**: `wglUseFontBitmapsW` (Windows font → GL bitmap display lists) in both MainApp and CADModule (`CWglFontBitmap`, `CGlFont3D`).
* **Text‑to‑path**: GDI `CreateFontA` + `GetGlyphOutlineW(GGO_BEZIER)` → TrueType outlines as cubic Béziers → converted by `CTextGlyphAdapter` to contours (then optionally through the NURBS/arc‑fit pipeline).
* **2‑D preview/thumbnails**: GDI+ (`GdipCreateFromHDC`, `GdipDrawLineI`, `GdipSaveImageToFile` JPEG).
* **No raster engraving**: no image entity, no dithering/halftone strings, no bitmap import filters.

---

## 7. Recommended Linux replacements

| Capability (as used by Mlaser) | Windows implementation | Recommended replacement | Maturity / licence fit |
|---|---|---|---|
| ASCII DXF read (LINE, ARC, CIRCLE, ELLIPSE, LWPOLYLINE/POLYLINE with bulges, SPLINE, POINT, TEXT/MTEXT, INSERT/blocks, XLINE/RAY, SOLID/TRACE/3DFACE) and DXF write | `DxfParseDllvc100.dll` + `CADModule::CDxfParse` | **ezdxf** (Python, MIT) if the port is Python‑based — most complete, handles all versions, has `ezdxf.path`/`explode` for INSERT flattening and `ezdxf.addons.text2path`; for C++: **libdxfrw** (GPL‑2, used by LibreCAD/QCAD) or **dxflib** (GPL‑2/commercial, QCAD). ODA's free **ODA File Converter** only for batch DWG→DXF | ezdxf: very mature, MIT. libdxfrw: mature, reads DWG too (R13–R2018), GPL‑2 |
| DWG read (nesting part import only) | ODA DWGdirect 2.3.1 inside `Dxf2Grp.dll` (R2007 max) | **libredwg** (GPL‑3, reads R13–R2018 + writes R2000) or **libdxfrw** DWG reader; or require users to convert with ODA File Converter (free binary, not redistributable) | libredwg: functional but API churn; acceptable since DWG is a nice‑to‑have here (the CAD path never used it) |
| HPGL/PLT import | `PltParse` in CADModule | **hp2xx** (GPL) as reference; in practice write a small HPGL/2 tokenizer (PU/PD/PA/PR/AA/AR/AT/CI/EA/ER/RA/RR/BR/BZ/PE/LB) — ~500 lines; Inkscape's `hpgl_input.py` (GPL) is a compact reference | Trivial to re‑implement; no dependency needed |
| G‑code/NC import (X Y I J R, G0/1/2/3, N, M, subprograms L/LP/M17) | `CGCodeFileParse` (BNF) | Hand‑written recursive‑descent parser mirroring the BNF in §3.7; optional **gcode‑parser** libs are not worth it | — |
| Native `.chf` | CADModule | Re‑implement (text format; see format analyst's doc) | — |
| NURBS/spline evaluation, fitting, discretisation | openNURBS via `splineAnalyerVc100.dll` | **openNURBS** itself (MIT, builds on Linux with CMake; opennurbs v8 public repo) — same math, so DXF SPLINE results will match; alternatively **tinynurbs** (BSD) or **libnurbs**/**SISL** (AGPL) | openNURBS: mature, MIT — direct drop‑in for the math |
| Arc/line fitting of polylines ("curve fit & compress") | `CircleFitDLL.dll` | Implement least‑squares (Kåsa/Pratt/Taubin) circle fit + biarc fitting; references: **libfit**‑style code, `scikit‑guess`, or **CGAL** (GPL/LGPL mixed) `Circular_kernel` is overkill | Small; write in‑house (numpy/Eigen) |
| Kerf compensation / contour offset (inner shrink, outer expand, open‑contour offsets, self‑intersection cleanup) | `CSegOffset`/`COpOffsetCmd` in CADModule (arcs kept as arcs) | **Clipper2** (Boost Software License 1.0) `InflatePaths` for polygonal offsets; **CavalierContours** (MIT, C++/Python) offsets *polylines with arcs (bulges)* — the closest match to Mlaser's line/arc contour model; CGAL `polygon_offset`/`Minkowski` if exactness matters (GPL) | Clipper2: very mature. CavalierContours: mature enough, ideal for arc‑preserving offsets |
| Boolean ops, inner/outer topology, contour containment tree | `CContourTopTree` | **Clipper2** (PolyTree) or **CavalierContours** (`Polyline::containment`) | — |
| Text → outlines | GDI `GetGlyphOutlineW(GGO_BEZIER)` + `CreateFontA` | **FreeType** (FTL/GPL dual) `FT_Outline_Decompose` for glyph outlines (+ **HarfBuzz** (MIT) only if complex shaping/CJK vertical text is required; **fontconfig** for font lookup). Python: `fontTools.pens` (MIT) | Mature; FreeType gives quadratic (TTF) / cubic (CFF) segments — flatten or convert to the internal Bézier→arc pipeline |
| Bitmap tracing / raster engraving | not present in Mlaser | Not needed for parity; if added later: **potrace** (GPL) for tracing, custom dither for engraving | optional |
| True‑shape nesting (rotation steps, spacing, sheet margin, part‑in‑part, common edge preference, multi‑sheet, remnants) | Geometric **NestLib 3** (`SmartNest.dll`) + `AutoNest.dll` bridge + dongle | **libnest2d** (LGPL‑3; C++, NFP + optimiser, used by PrusaSlicer/Cura; supports rotations, spacing, multiple bins, holes via `Item` with holes) — best C++ fit; **SVGnest**/**Deepnest** (MIT, JS, genetic NFP nester) as algorithm reference or via a Node sidecar; **nest2D** (Python bindings to libnest2d); for rectangular/guillotine mode **rectpack** (MIT, Python) or **OR‑Tools** (Apache‑2) bin‑packing | libnest2d: mature but not feature‑complete vs NestLib (no common‑edge, no guillotine, no remnant management) — those must be implemented on top (common‑edge is already a CADModule post‑process in Mlaser, so that is natural) |
| Array / grid copy, mirror, rotate, scale, align, sort | CADModule commands | plain code (numpy/Eigen); path ordering via nearest‑neighbour + 2‑opt, or **OR‑Tools** routing for TSP‑style sequencing | — |
| Curve smoothing, lead‑in/out, micro‑joints, bridges, fillets, overcut, cool points | CADModule | plain code on the line/arc contour model; **CavalierContours** helps with arc‑aware splitting/offsetting | — |
| 2‑D display | OpenGL 1.x immediate mode + wgl font bitmaps | Any: Qt (QPainter/QOpenGLWidget) or OpenGL 3 core with a text atlas (FreeType); **Qt** is the natural host for an MFC/BCGControlBar‑style docking UI (LGPL‑3) | — |
| Thumbnails | GDI+ JPEG | Qt `QImage`/libjpeg, or cairo | — |

Licence caveat: the Linux port must not link or redistribute `SmartNest.dll` (Geometric, hardware‑locked), `AutoNest.dll`/`Dxf2Grp.dll` (SmartNest Technology, dongle‑gated) or the embedded ODA library (ODA membership licence). openNURBS (MIT), FreeType, Clipper2, libnest2d, ezdxf/libdxfrw are all redistributable under GPL‑compatible terms (note libdxfrw/libredwg/potrace are GPL — fine for a GPL port, a problem for a permissive one).

---

## 8. Open questions

1. **Exact semantics of `ISpline2DAnalyer` slots 3–24.** The vtable arity is known (§3.5) but the method names are not exported; CADModule call sites (10) need decompilation to name them (e.g. which slot performs discretisation at `pd93` precision and whether it emits arcs or only line segments).
2. **`Nest_SetNestPara` index 8 and `Nest_SetPartPara` modes.** CADModule never sets idx 8; the three `SetPartPara` calls with `(1,0)`, `(0,1)`, `(1,2)` likely set quantity=1, priority/rotate flags — unverified.
3. **Result element layout** (48 bytes: part index + 5 doubles) — which double is rotation, is there a mirror flag, are coordinates the part origin or its bbox corner?
4. **How CADModule hands part geometry to AutoNest** (`Nest_WritePartData` pointers): whether contours go as line/arc lists or as discretised polylines, and where the CircleFit tolerance globals (`Err/Len/Ang/Rmax`) are set (AutoNest writes `Rmax_CircleFit`).
5. **Which NestLib options the bridge enables** (`NSetFitPartInPart`, `NSetGuillotineCut`, `NDefineAngleList`, `NSetPartSeqMethod`): the pointer‑offset call‑site scan (§3.3) is noisy (offsets collide with other structs); a proper data‑flow pass is needed to confirm the exact option set and whether rotation uses the angle list.
6. **DXF writer scope**: `dxfTemplate.dxf` is referenced but not shipped in `SRC`; which entities the exporter emits (bulge polylines vs arcs, splines) is unknown.
7. **PLT dialect details**: pen‑width/scale handling (`IP/SC`), units (1/40 mm HPGL default?), and whether `LB` labels are rendered — needs a test file.
8. **"精品字/广告字" (Adv/Advert text)** with `pd1810/1811` (type, offset length) — probably outline‑offset or single‑line fonts for sign making; not verified.
9. **`MotionCtrl.dll`** (exports `arcInterp/newContourSmooth/newVelocityPlanning/segInterp*`) is not imported by MainApp or any Module DLL — it may be loaded via `LoadLibrary` by NCModule or be a leftover; the motion analyst should confirm (CADModule has its own `CVelocityPlanning`/`IContourSmooth`).
10. **`.chf` version 4 vs 5 differences** and the `<Crafts>` block semantics — file‑format analyst.
11. The remnant (余料/残板) workflow: whether `Nest_ReadOddData` (unused by CADModule) or `CCutupScrap` produces the residual sheet contour that is saved as a "残板".

---

## 9. Implications for the Linux port

**Must replicate (behavioural parity):**

* The **line/arc/circle contour model** with closed‑loop topology (inner/outer), because every downstream feature (compensation, lead‑ins, micro‑joints, common edge, sorting, nesting) is defined on it. Splines/ellipses/Bézier text are discretised and **arc‑fitted** (CircleFit) rather than kept as curves; the Linux port should offer the same "curve precision" (`pd93`, `pd370`, Dxf2Grp `曲线离散精度`) and fit tolerance knobs so that produced NC is equivalent.
* DXF import semantics: block explosion (`pd373`), text→curves (`pd374`), unit handling, `LWPOLYLINE` bulges, `SPLINE` via NURBS evaluation (use openNURBS for identical results), ellipse arcs, ignoring HATCH/DIM.
* PLT (HPGL/2) and G‑code importers with the same command coverage (§3.7), including the subprogram syntax (`L`, `LP`, `M17`, `M02`).
* Native `.chf` read/write for compatibility with existing job files and the machine's autosave workflow.
* Nesting UX and parameter set: spacing, edge margin, rotation step (note the shipped kernel effectively uses 90° steps), direction L2R/D2U, nest‑in‑holes, common‑edge min length, accuracy, standard sheet size, part quantities, multi‑sheet results, remnant sheets, ≤ 50 part types, and the error taxonomy of §5.4 (map to equivalent messages).
* Geometry tools list (§3.7) — all are in‑house code, none depend on a third‑party library, so they are pure re‑implementation work.
* Text tool: font selection, height in mm, TrueType outline conversion (FreeType instead of GDI), CJK fonts (default `宋体` SimSun / `Microsoft YaHei` — pick Noto Sans CJK on Linux).

**Can be replaced outright by open‑source libraries:**

* DXF/DWG parsing → ezdxf / libdxfrw (+ libredwg for DWG).
* NURBS math → openNURBS (identical library, MIT).
* Offsetting/booleans → Clipper2 or CavalierContours (arc‑aware).
* Nesting kernel → libnest2d (or Deepnest/SVGnest port); implement common‑edge, guillotine and remnant handling on top, as Mlaser itself does most of that outside NestLib.
* Text outlines → FreeType (+ HarfBuzz/fontconfig).
* Rendering → Qt/OpenGL; thumbnails → Qt.

**Must be dropped / redesigned:**

* All dongle/licence logic (`Nest_CheckLock`, SM2 authorisation codes, `PWDKey` HID) — the nesting feature must not depend on the USBKey; the Linux port simply omits it.
* The VC6/MFC42 legacy chain (`Dxf2Grp`, `AutoNest`, `SmartNest`) and the ODA library cannot be reused (licensing) nor run natively; even under Wine they need MFC42/MSVCP60, so no hybrid approach is recommended for nesting.
* GDI+/GDI‑specific text and image code.
