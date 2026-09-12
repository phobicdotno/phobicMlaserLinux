# 07 — MainApp.exe internals (static analysis)

Package: `/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52` (referred to as `SRC` below).
Target: `SRC/MainApp.exe` (18,044,416 bytes, PE32 i386, GUI subsystem). Companion binaries in `SRC/Module/`, `SRC/Report/`, `SRC/Update/`, `SRC/Dump/` and the third‑party DLLs in `SRC/`.

Tooling: `objdump -p/-h/-d -M intel`, `strings -a` / `strings -a -e l`, `xxd`, and small stdlib-only Python scripts (PE section/entropy walker, resource-directory walker, minidump parser, disassembly annotator that resolves immediates to string addresses). No dynamic execution was performed.

Conventions: **EVIDENCE** = directly observed (with file/offset/address). **INFERENCE** = interpretation, each tagged with a confidence (high / medium / low). Virtual addresses (VA) assume the preferred image base 0x400000. Chinese strings are quoted verbatim with an English translation in brackets.

---

## 1. Executive summary

* MainApp.exe is a **thin MFC/BCGControlBar "shell"**: it hosts the ribbon UI, the OpenGL canvas, dialogs and panels, and a plugin loader. The domain logic lives in six **plugin DLLs under `SRC/Module/`** (`CADModule`, `NCModule`, `ParaModule`, `LangModule`, `LogModule`, `ControlModule`), each exporting one function, `newModuleProvider`, and discovered at run time via `\Module\*.dll` + `LoadLibraryW` + `GetProcAddress("newModuleProvider")` (VA 0x4b6280).
* Internal product name is **"NexCut"** (PDB path `D:\SC2000\NexCut\NexCut_X1_Http\Release\MainApp.pdb`); the version resource still says "SC2000". The window title is chosen from "NexCut Fiber laser" / "NexCut CO2 laser" / "NexCut Blue laser". Displayed version string: `v0.0.0.52`.
* **There is no `ws2_32` import in MainApp.exe**: all raw TCP/UDP to the MCC100 controller (and every other device) is done inside **`Module/NCModule.dll`** (imports 18 `WS2_32` functions incl. `socket/connect/send/recv/sendto/recvfrom/select`). MainApp itself only uses **WinHTTP** for a small HTTP client (`CHttpClient/1.0`) talking to a "NexCut" web service (`/NexCut/Login`, `/NexCut/File/LoadFile`, `/NexCut/File/UploadGCode`).
* **Three separate "protection" mechanisms exist**, and it is important not to conflate them:
  1. The **controller-card licence** ("dog" = 加密狗 in the vendor's vocabulary, but here it is the *MCC100 card's* clock/licence data area, not a USB device). Implemented by class `CDog` in `NCModule.dll`; MainApp only displays the state via language keys `dogState_*` / `dogActiveReslut*` and writes `Log\Code.txt`.
  2. The **USB HID dongle VID 0x3689 / PID 0x8762 ("USBKey")** is used **only by `AutoNest.dll`** (the auto-nesting kernel, "HePin"), reached from `CADModule.dll`. It gates the nesting feature (`Nest_CheckLock`), nothing else. MainApp.exe contains no reference to 0x3689/0x8762.
  3. The vendor key/activation URL `http://www.au3tech.cn/key/` (ASCII, VA 0x7e8a64) is **assigned as a constant** (CString `operator=` at 0x4bb7d5–0x4bb7e6 into field +0x30c of the settings object) right after `MonitorIP/MonitorPort` are read from `File\ipAdd.ini [IP]`. *(Verifier correction: it is not an INI default — the call at 0x4bb7e6 is a 1-line wrapper around 0x409c00 (CString assign), not `GetPrivateProfileString`; whether the field is later overridden from a file could not be shown statically.)*
* MainApp's own HID code (`CHidUsb`, VA 0x452200–0x452a80) is for a **USB joystick / hand-wheel** (VID_1000/PID_2016 or VID_6125/PID_2012, selected by a hardware-parameter field) — not the dongle. The wireless pendant goes through **`PHBX.dll`** (loaded dynamically, `Xinit/XOpen/XGetInput/XSendOutput/XClose`).
* Telemetry: `MonitorIP=47.104.17.21:9001` is consumed by **NCModule** (`CMonitorHalAPI`/`CMonitorModbus`, JSON via boost::property_tree). NCModule also has a hand-written HTTP/1.1 POST client that uploads logs (`logID=&isText=&machineID=&logData=`). MainApp's WinHTTP client talks to a "NexCut" server whose **host comes from the row selected in the login dialog's device grid and whose port is hard-coded to 8080** (`push 0x1f90` at 0x434a51 — verifier correction, see §9.1), with default credentials `NexCut` / `12345678` and an `Authorization: DebugWithSuperpermissions` header on one request.
* `Report/report.exe` is a **Qt 5.15.2 (MSVC 2019) application packed with Enigma Virtual Box** (sections `.enigma1`, `.enigma2` hold the Qt runtime; the application's own ~40 KB of code and its GBK strings sit in the *visible* `.text/.rdata` stub — verifier correction, see §11); it renders the job reports (`Report/report.txt`, `Report/*.chf.jpg`) and is launched by MainApp with `ShellExecuteW("open", "<dir>\Report\report.exe")` after writing `Report\lang.txt`.
* `Dump/20260911-175115.dmp` is a minidump written by MainApp's own `MiniDumpWriteDump` handler; it was produced **under Wine** (`Z:\home\karstein\...`, `winex11.drv`) and shows an access violation at VA 0x4b24e6 caused by a NULL module-interface pointer — `CADModule.dll` is absent from the loaded-module list (it depends on `AutoNest.dll` → `MFC42.DLL`, which the package does not ship and Wine does not provide).

---

## 2. PE header, sections, packer check

### 2.1 Header (EVIDENCE — `objdump -p SRC/MainApp.exe`)

| Field | Value |
|---|---|
| Machine / Magic | i386, PE32 (0x10b) |
| Characteristics | 0x122 (executable, large-address-aware, 32-bit) |
| Link timestamp | Tue Jun 24 04:20:03 2025 |
| Linker | 10.0 (Visual Studio 2010) |
| Subsystem | 2 (Windows GUI), min OS/subsystem 5.1 |
| Entry point | RVA 0x206a84 |
| ImageBase | 0x400000, SizeOfImage 0x1275000 |
| DllCharacteristics | 0x8140: DYNAMIC_BASE (ASLR), NX_COMPAT, TERMINAL_SERVICE_AWARE |
| Checksum | 0x0113e71d |
| Debug directory | RSDS, age 1, PDB = `D:\SC2000\NexCut\NexCut_X1_Http\Release\MainApp.pdb` |
| Load config | SecurityCookie at 0x93abd0, SafeSEH handler table 0x8917c0 with 3340 handlers |
| TLS directory | none |
| Export directory | none |
| CLR header | none (native code) |

### 2.2 Sections (EVIDENCE — `objdump -h` + entropy script)

| Section | VA | vsize | raw off | raw size | Shannon entropy | Notes |
|---|---|---|---|---|---|---|
| .text | 0x401000 | 0x3bd806 | 0x400 | 0x3bda00 | 6.435 | normal MSVC code |
| .rdata | 0x7bf000 | 0x173284 | 0x3bde00 | 0x173400 | 4.414 | strings, RTTI, vtables, IAT |
| .data | 0x933000 | 0x144a5c | 0x531200 | 0x7e00 | 3.842 | large BSS (≈1.3 MB) — global state objects |
| .rsrc | 0xa78000 | 0xb70938 | 0x539000 | 0xb70a00 | 6.381 | 11.9 MB of bitmaps/PNG |
| .reloc | 0x15e9000 | 0x8ba40 | 0x10a9a00 | 0x8bc00 | 6.472 | relocations |

Overlay: 0 bytes (file ends exactly at the end of `.reloc`).

**INFERENCE (high):** No packer/protector. Standard section names, code entropy 6.4 (typical for x86 code, not compressed/encrypted), no TLS callbacks, IAT fully in the clear with 20 DLLs and 2,100+ named imports, intact RTTI, intact PDB path. The only "anti-analysis"-looking thing is a CRT exception-filter patch at VA 0x4ae650 (see §7.4), which is a crash-dump hook, not protection.

### 2.3 Manifest and version resource (EVIDENCE — resource walker)

* Manifest (RT_MANIFEST id 1): Common-Controls 6.0 dependency, **`requestedExecutionLevel level="requireAdministrator"`**, `dpiAware=true`.
* VS_VERSION_INFO (lang 0x0804/CP 1200): `CompanyName="TODO: <公司名>"` [TODO: <company name>], `FileDescription="SC2000"`, `FileVersion=1.0.0.1`, `InternalName=SC2000.exe`, `OriginalFilename=SC2000.exe`, `ProductName="TODO: <产品名>"` [TODO: <product name>], `LegalCopyright="TODO: (C) <公司名>。保留所有权利。"` [TODO: (C) <company>. All rights reserved.]. I.e. the wizard-generated placeholders were never filled in.

---

## 3. Import inventory

### 3.1 Per-DLL counts (EVIDENCE)

| DLL | # imports | Role |
|---|---|---|
| BCGCBPRO2210u100.dll | 1549 | BCGControlBar Pro 22.10 (Unicode, VC100) — ribbon, docking, grids, property lists, gauges |
| mfc100u.dll | 436 (by ordinal) | MFC 10 Unicode |
| MSVCP100.dll | 173 | C++ std library |
| MSVCR100.dll | 97 | CRT |
| KERNEL32.dll | 76 | |
| USER32.dll | 41 | |
| OPENGL32.dll | 34 | canvas rendering |
| GDI32.dll | 22 | incl. `ChoosePixelFormat/SetPixelFormat/SwapBuffers/DescribePixelFormat` |
| gdiplus.dll | 17 | thumbnail/JPEG export |
| WINHTTP.dll | 10 | HTTP client |
| SHLWAPI.dll | 7 | path helpers |
| WINMM.dll | 5 | multimedia timer |
| SHELL32.dll | 5 | `ShellExecuteW`, special folders |
| SETUPAPI.dll | 5 | device-interface enumeration |
| HID.DLL | 5 | HID capability queries |
| ADVAPI32.dll | 3 | `RegCreateKeyA/RegSetValueA/RegCloseKey` |
| OLEAUT32.dll | 6 (ordinals 2,6,7,8,9,12) | BSTR/variant helpers (`SysAllocString`, `SysFreeString`, `SysStringLen`, ...) |
| GLU32.dll | 1 | `gluLookAt` |
| SensApi.dll | 1 | `IsNetworkAlive` |
| PSAPI.DLL | 1 | `GetProcessMemoryInfo` |
| dbghelp.dll | 1 | `MiniDumpWriteDump` |
| COMCTL32.dll | 1 | `_TrackMouseEvent` |

**Absent:** `WS2_32.dll`, `WSOCK32.dll`, `IPHLPAPI`, `WININET`, `CRYPT32`, `OLE32` (no COM/`CoCreateInstance`), `MSXML`, `ODBC`. **Networking to the machine therefore cannot happen in MainApp.exe; it happens in `NCModule.dll` / `LogModule.dll`** (see §3.4).

### 3.2 Full lists of the small/interesting DLLs (EVIDENCE)

* **OPENGL32.dll (34):** glPopAttrib glGenLists glCallList glListBase glPushAttrib glRasterPos3d wglUseFontBitmapsW glPopMatrix glColor3d glOrtho glLoadIdentity glPushMatrix glMatrixMode glColor4d glFinish glShadeModel glEnable glClearDepth wglMakeCurrent glFlush wglCreateContext wglDeleteContext glViewport glLineWidth glTranslated glClear glClearColor glEnd glVertex2d glBegin glDisable glBlendFunc wglGetCurrentDC glDeleteLists. **GLU32:** gluLookAt.
  *INFERENCE (high):* Pure fixed-function OpenGL 1.x immediate mode (`glBegin/glVertex2d/glEnd`, `glOrtho`, display lists for bitmap fonts via `wglUseFontBitmapsW`). No shaders, no VBOs, no textures. 192 OpenGL call sites, concentrated in ~10 functions (VA pages 0x4b2000–0x4b4000, 0x5e7000–0x5e9000, 0x401000–0x402000, 0x4a1000, 0x556000).
* **HID.DLL (5):** HidD_GetHidGuid, HidD_GetPreparsedData, HidP_GetCaps, HidD_FreePreparsedData, HidP_GetSpecificValueCaps. **No** `HidD_GetAttributes`, `HidD_GetFeature`, `HidD_SetFeature`, `HidD_GetInputReport` — i.e. MainApp never does a feature-report exchange; it only reads input reports (see §8.3).
* **SETUPAPI.dll (5):** SetupDiDestroyDeviceInfoList, SetupDiGetClassDevsW, SetupDiEnumDeviceInfo, SetupDiEnumDeviceInterfaces, SetupDiGetDeviceInterfaceDetailW.
* **WINHTTP.dll (10):** WinHttpOpen, WinHttpConnect, WinHttpOpenRequest, WinHttpAddRequestHeaders, WinHttpSendRequest, WinHttpWriteData, WinHttpReceiveResponse, WinHttpQueryDataAvailable, WinHttpReadData, WinHttpCloseHandle.
* **WINMM.dll (5):** timeSetEvent, timeKillEvent, timeBeginPeriod, timeEndPeriod, timeGetDevCaps (one multimedia timer created at VA 0x56535e, killed at 0x563e19 — the periodic status-poll tick; *INFERENCE medium*).
* **gdiplus.dll (17):** GdiplusStartup/Shutdown, GdipCreateBitmapFromHBITMAP, GdipCreateBitmapFromScan0, GdipSaveImageToFile, GdipGetImageEncoders(Size), GdipCreateFromHDC, GdipCreatePen1, GdipDrawLineI, GdipSetSmoothingMode, ... (thumbnail `_tempthumbnail.jpg` / `*.chf.jpg` generation; `GdipSaveImageToFile` called from VA 0x607534).
* **ADVAPI32 (3):** RegCreateKeyA, RegSetValueA, RegCloseKey — used only in one function (VA 0x4ae700–0x4aea10) that registers a file-type: format strings `%s\DefaultIcon`, `%s\Shell`, `Open`, `%s\Shell\Open\Command`, `%s "%1"` (associating `.chf` with the exe; *INFERENCE high*). There is **no** registry-based licensing/config in MainApp.
* **SHELL32 (5):** ShellExecuteW (VA 0x4917fb, 0x498c02), SHGetSpecialFolderPathA/W, SHCreateDirectoryExA/W.
* **KERNEL32 highlights:** CreateProcessA (4 sites), CreateToolhelp32Snapshot/Process32FirstW/NextW (single-instance check), CreateMutexW (2 sites: mutex names `"SC3000"` and `"HighSea_SC1000"`), LoadLibraryA/W + GetProcAddress (plugin loading), VirtualProtect (3 sites, all in the CRT-filter patch at 0x4ae68e/0x4ae6a0/0x4ae6d6), SetThreadExecutionState (keep display awake, 0x45a6a8), GetPrivateProfileInt/String A/W + WritePrivateProfileString A/W (INI I/O), CreateFileW/ReadFile/WriteFile/GetOverlappedResult/CancelIo (HID + serial I/O), IsDebuggerPresent (only inside the CRT's `_invoke_watson`/abort path at 0x60742a — not an anti-debug check).
* **USER32 highlights:** RegisterDeviceNotificationW (VA 0x45ad30 — device arrival/removal for the joystick), FindWindowW (0x491c41, detects an already-running report.exe), SetLayeredWindowAttributes, SetWindowRgn, GetAsyncKeyState.
* **MSVCR100:** includes `system` (one call, VA 0x498d0c: `"ping 10.1.1.168 -l 1000 && ping 10.1.1.169 -l 1000 && pause"`), `_wfopen_s`/`fopen_s`, `__RTDynamicCast`, `_CxxThrowException`, `wcsftime`, `_localtime64_s`, `vswprintf_s`.

### 3.3 Where each import family is called (EVIDENCE — IAT cross-references in the disassembly)

| Import | IAT slot | Call sites (VA) | Enclosing function / purpose |
|---|---|---|---|
| HidD_GetHidGuid / HidD_GetPreparsedData / HidP_GetCaps / HidD_FreePreparsedData / HidP_GetSpecificValueCaps | 0x7c08b4…0x7c08c4 | via thunks 0x605dec… from 0x452279, 0x45289f, 0x4528b7, 0x4528d8, 0x4528ff | `CHidUsb` (0x452200–0x452a80) |
| SetupDiGetClassDevsW (flags 0x12 = PRESENT|DEVICEINTERFACE) | 0x7c0ef4 | 0x4526aa | `CHidUsb` enumerate |
| SetupDiEnumDeviceInfo / EnumDeviceInterfaces / GetDeviceInterfaceDetailW | | 0x4525b7, 0x4525eb, 0x452619, 0x452636 | `CHidUsb` enumerate |
| CreateFileW (share 3, OPEN_EXISTING) + CreateEventW + ReadFile + WaitForSingleObject(3000 ms) + GetOverlappedResult + CancelIo | | 0x452882, 0x4527d8, 0x4529a1, 0x4529b6, 0x4529db, 0x4529f9 | `CHidUsb` open / overlapped read |
| IsNetworkAlive | 0x7c0f40 | thunk 0x605e0a ← 0x48a0e4 (fn 0x48a0d0), 0x48f296 (fn 0x48f260, callers 0x493316, 0x52df60) | pre-check before HTTP/monitor operations (*INFERENCE medium*) |
| GetProcessMemoryInfo | 0x7c0ee8 | thunk 0x605e10 ← 0x4a979d | memory-shortage warning when importing graphics (strings `Import graph - Application Available Memory shortage - %d/%d`) |
| MiniDumpWriteDump | 0x7c1034 | thunk 0x605e16 ← 0x4ae581 (fn 0x4ae3f0) | crash handler; writes `\Dump\<date>.dmp` |
| WinHttp* | 0x7c0ff0…0x7c1014 | 0x406100 (login), 0x406940 (LoadFile JSON), 0x406fc0 (generic JSON POST), 0x407430 (multipart upload) | `CHttpClient` (§9.1) |
| LoadLibraryW + GetProcAddress | 0x7c08f0 / 0x7c09e0 | 0x4b649e/0x4b64c5 (`newModuleProvider`), 0x586e87/0x586ea6/0x586ec4 (`PHBX.dll`: `Xinit`, `XOpen`), 0x5994d1 (`XGetInput`), 0x599623/0x59965e (`XClose`/`XOpen`), 0x563f03 (`XClose`), 0x56c979 (`XSendOutput`), 0x516de7 (`ImageList_AddMasked` from comctl32), 0x603f3c/0x603f52 (`KERNEL32.DLL!CancelIo`), 0x4ae672/0x4ae679 (`msvcrt.dll!_XcptFilter`) | plugin & pendant loading |
| CreateProcessA | 0x7c08cc | 0x491df6 (`"explorer.exe " + …\NexCut\Log\`), 0x491f38 (`"explorer.exe " + \Log\Code.txt`), 0x4b415e, 0x5fddab (`"explorer.exe " + \Help\`) | "open folder/file" buttons |
| ShellExecuteW | 0x7c0f08 | 0x4917fb (`open` `\Report\report.exe`), 0x498c02 (`open` `\File\ipset.exe`) | launch helper tools |
| system | 0x7c0cf8 | 0x498d0c | ping diagnostic |
| RegCreateKeyA/RegSetValueA | 0x7bf008/0x7bf004 | 0x4ae733…0x4aea05 | file association |
| CreateMutexW | 0x7c09d0 | 0x4aec7c (`"SC3000"`), 0x4aed1a (`"HighSea_SC1000"`) | single instance (checks `GetLastError()==0xb7` ERROR_ALREADY_EXISTS) |
| CreateToolhelp32Snapshot | 0x7c09c4 | 0x6074c8, used by 0x4aeba1 with process names `SC3000.exe`, `SCTube.exe`, `MainApp.exe` | refuses to start if a sibling product is running (*INFERENCE high*) |
| RegisterDeviceNotificationW | 0x7c0fa8 | 0x45ad30 | WM_DEVICECHANGE for HID arrival (`VID_`/`PID_` matching in 0x59b080) |
| timeSetEvent / timeKillEvent | 0x7c101c / 0x7c1028 | 0x56535e / 0x563e19 | periodic tick |
| GdipSaveImageToFile | 0x7c105c | 0x607534 | thumbnails |

### 3.4 How networking happens without ws2_32 (EVIDENCE)

`objdump -p` on the plugins:

| DLL | Imports (DLL: count) | Notes |
|---|---|---|
| Module/NCModule.dll (788 KB, 2025-06-24) | **WS2_32 (18)**: send gethostbyname htonl inet_addr WSAGetLastError sendto recvfrom bind WSAStartup socket htons connect setsockopt WSACleanup closesocket select __WSAFDIsSet recv; KERNEL32 54, RPCRT4 (UuidCreateSequential), SHELL32 2, SHLWAPI 3, MSVCR100 77, MSVCP100 158, mfc100u 50 | All device links: `CMCHalAPI` (motion card, Modbus/UDP), `CAFNetHalAPI`, `CECNetHalAPI`, `CLaserNetHalAPI`, `CSerialHalAPI`, `CMonitorHalAPI`; Modbus flavours `CStdModbus`, `CExtModbus`, `CExtCardModbus`, `CSerialModbus`, `CIPGModbus`, `CRaycusModbus`, `CFTC61Modbus`, `CMonitorModbus`; also `CDog`, `CVirtualMachine`, `CFrogJumpSvr`, `CFJSAccSrv`, `CVerticalCorrect`. |
| Module/LogModule.dll (70 KB) | **WS2_32 (10)**: socket WSAStartup WSACleanup recv gethostbyname inet_addr connect closesocket htons send; RPCRT4 UuidCreateSequential | Log writer (`YaoUtil::YaoLog/YaoBinLog/YaoFormattedBinLog`) with a TCP client (*INFERENCE medium:* remote log sink). |
| Module/CADModule.dll (1.3 MB, 2025-06-14, PDB `C:\Users\Michael\source\repos\CAD_head_update\sc2000-e\Release\Module\CADModule.pdb`) | AutoNest.dll (18), OPENGL32 (26), splineAnalyerVc100 (1: `newSpline2DAnalyer`), DxfParseDllvc100 (1: `newDxfFileParse`), PSAPI 1, mfc100u 29, MSVCP100 118 | Geometry/CAD kernel, DXF/PLT import, nesting bridge, NC path planning. Note: **built by a different developer/machine ("Michael") and 10 days earlier than the rest.** |
| Module/ParaModule.dll (135 KB) | ADVAPI32 3, mfc100u 23 | `CXMLParaEngine` — the XML parameter store. |
| Module/LangModule.dll (69 KB) | SHLWAPI 3, mfc100u 48 | `CTxtFile` — `Lang\lang.txt` loader. |
| Module/ControlModule.dll (44 KB) | BCGCBPRO 66, mfc100u 137 | Custom controls `CHSButton/CHSComboBox/CHSEdit/CHSListCtrl/CHSSliderCtrl/CHSStatic/CVerticalButton` + `I*Ex` interfaces. |

*Verifier note:* the WS2_32 ordinals were re-resolved (19 send, 52 gethostbyname, 8 htonl, 11 inet_addr, 111 WSAGetLastError, 20 sendto, 17 recvfrom, 2 bind, 115 WSAStartup, 23 socket, 9 htons, 4 connect, 21 setsockopt, 116 WSACleanup, 3 closesocket, 18 select, 151 __WSAFDIsSet, 16 recv) and match the list above exactly. NCModule has 132 unique RTTI descriptors of which 45 are non-std/boost application classes (listed in §4.4).

The UDP/Modbus link to MCC100 at `10.1.1.168:502` is therefore entirely inside NCModule.dll (out of scope for this document; see the protocol analysis documents). MainApp's log `Log\2025-07-18.log` confirms NCModule's wording: `MC-Sendto ErrCode:10065 …`, `MC-RecvErr_selectFunc ErrCode:10060 …` (WSAEHOSTUNREACH / WSAETIMEDOUT — sendto/select/recvfrom = UDP).

---

## 4. Architecture map from RTTI

### 4.1 Method

`strings -a MainApp.exe | grep '^\.?A[UV]'` yields 246 unique type descriptors; after removing `std::`, `boost::`, `ATL::`, `Gdiplus::` and the 38 `CBCGP*` base classes, **≈130 application/MFC classes** remain. The plugin DLLs were processed the same way (CADModule 193, NCModule 132, ControlModule 40, ParaModule 35, LangModule 26, LogModule 15 descriptors). The lists below are exhaustive for the application classes.

### 4.2 MainApp.exe class clusters (EVIDENCE: names; INFERENCE: grouping, high)

| Cluster | Count | Classes |
|---|---|---|
| Application / document / frame | 8 | `CMainApp` (CWinApp/CBCGPWinApp), `CMainDoc` (CDocument), `CDocumentAdapter@CDocument`, `CMainFrame` (CBCGPFrameWnd), `CMainView`, `CMyRibbonStatusBar`, `CSplashDlg`, `CAboutDlg` |
| Views (CView/CFormView/CScrollView derived) | 15 | `COpenGLView` (canvas), `CGraphPropView`, `CHardwarePropView`, `CSCPreviewView`, `CSystemStatusView`, `CECStatusView` (EtherCAT), `CZFStatusView` (ZF = 随动/height follower), `CSectionDOView`, `CAutoFocusView`, `CLaserCurveView`, `LaserTestView`, `StressTestView`, `MarkPointView`, `CTabbedView`, `CTabCtrlEx` |
| Docking panels | 12 | `CControlPanel`, `CManuPanel` (manufacturing/run panel), `CErrorPanel`, `CInterferePanel`, `CPropPanel`, `CLayerPropPanel`, `CGlyphPropPanel`, `CPanelBar`, `CBasicNestPanel`, `CPartNestPannel`, `CSheetNestPannel`, `CReslutNestPannel` |
| Dialogs — process / machine setup | 28 | `BatchCutSetDlg`, `CAdjustPtDlg`, `CCompensateDlg`, `CCraftPropDlg`, `CDockPtDlg`, `CDOSelectDlg`, `CDualServoCheckDlg`, `CEdgeSeekDlg`, `EdgeSeekAuto`, `CGraphParaDlg`, `CGraphScaleDlg`, `CHardwareUpdateDlg`, `CIPSetDlg`, `CircleCenterDlg`, `CJoystickMatchDlg`, `CLaserCodeDlg`, `CLayerPropDlg`, `CO2LayerPropDlg`, `CPlatformExchangeDlg`, `CProcessParaDlg`, `CRollSheetMoveDlg`, `RollSheetSetDlg`, `CSCanFlyCutDlg`, `CSelectTechnologyDlg`, `CSimplePlcDlg`, `CSysStatusDlg`, `LiftingPlatformDlg`, `CSCFileDialog` |
| Dialogs — generic input | 6 | `LoginDlg`, `CPwdInputDlg`, `CCodeInputDlg` (activation code), `CTextInputDlg`, `CHelpDlg`, `EmbeddedDlg` |
| Touch keyboards (touchscreen HMI) | 5 | `CTouchKeyboard`, `CNumTouchKeyboard`, `CPasswordKeyboard`, `CTouchKeyboardDlgProp`, `CPulseEqTouchKeyboardDlgProp` |
| Property-grid item types (BCGP `CBCGPProp` subclasses) | 9 | `CCheckBoxProp`, `CCOMComboProp`, `CDAComboProp`, `CDIComboProp`, `CDOComboProp`, `CEnumComboProp`, `CEtherCATComboProp`, `CExtDOComboProp`, `CGasDAComboProp` |
| Grid controls / items | 7 | `CAdjGridCtrl`, `CAFDAAdjGridCtrl`, `CGridCtrlEx`, `CGridItemFloat3`, `CNestGridCtrl`, `CThumbGridItem`, `CErrorReportCtrl` |
| LED / indicator statics | 8 | `CAFDAAdjStatic`, `CAFLedStatic`, `CAxisLedStatic`, `CBtnStatic`, `CGasPAdjStatic`, `CLaserPowerLedStatic`, `CManufactureSpeedLedStatic`, `CManuLedStatic` |
| OpenGL text | 2 | `CGlFont3D`, `CWglFontBitmap` |
| Engines / infrastructure | 8 | `CGraphNcDataEng` (graph→NC data), `CSerialEng`, `CHidUsb`, `CLeanModuleManager` / `IModuleManager` (plugin registry), `CNestDataChangeEvent` / `INestDataEvent`, plus the boost-based `CHttpClient` (no RTTI, identified from its user-agent string) |

Other observations from RTTI/strings in MainApp:
* **boost** is statically linked: `boost::format`, `boost::function`, `boost::lexical_cast`, `boost::algorithm::token_finder`, `boost::io::basic_altstringbuf`, `std::tr1::regex` nodes. NCModule additionally embeds `boost::property_tree::json_parser` (source path `C:\OrionLib\boost 1.5.3\…` → **Boost 1.53**).
* `CNestDataChangeEvent`/`INestDataEvent`/`CNestGridCtrl`/`C*NestPannel` show the nesting UI lives in MainApp while the nesting engine lives in CADModule → AutoNest.dll.
* Code size: 10,521 `push ebp` prologues (≈ functions), 8,602 register-indirect calls (`call eax/ecx/edx`) — the very high proportion of virtual calls reflects the interface-based plugin design (every module is reached through an `I*Module` vtable).

### 4.3 Global state and module manager (EVIDENCE)

* `0x5ff1b0` is a one-line accessor `mov eax, 0xa2efa0; ret` — referenced **6,081 times**. It returns the address of a large global object in `.data`. Fields observed being read: `+0x213c` (HID handle/joystick object), `+0x4028` (joystick type selector), `+0x46d8` (laser type flag), `+0x47cc` (machine state enum: values 0x3, 0xb, 0x10, 0x11, 0x17 compared), `+0x2f0` (pointer to a module interface, NULL in the Wine crash), `+0x40b8`, `+0xf4d4` etc.
  *INFERENCE (high):* this is the application-wide "system context" singleton holding module-interface pointers, hardware state and cached parameters.
* `0x4aeb00` returns `0x963e00` (second singleton) and is used immediately before language look-ups (`push "mf147"; call 0x405130 (CString ctor); call 0x4aeb00; … call [vtable]`), i.e. it returns the language/module manager used to translate a key. `0x5ff1c0` returns `0xa3b420` (third singleton).
* Plugin loader at **0x4b6280**: builds `<exe dir>\Module\*.dll` (VA 0x7e6350), `FindFirstFileW/FindNextFileW`, `LoadLibraryW` (0x4b649e), `GetProcAddress(h, "newModuleProvider")` (0x4b64c5, string VA 0x7e636c), calls the returned function (0x4b64de) and registers the provider (`IModuleProvider` → `IModule`). Every `Module/*.dll` exports exactly one symbol, `newModuleProvider` (EVIDENCE: `objdump -p` on all six; re-verified).
* **Modules are looked up by a Chinese *registered name*** (EVIDENCE, verifier addition): the pattern `call 0x4aeb00` (module manager) → `push L"<name>"` → `call [vtbl+0xc]` (GetModule-by-name) occurs everywhere. Names found as UTF-16 literals in `.rdata`: `语言包` [language pack] = LangModule (**2,048 copies**, one per translation site — e.g. 0x7c4838, 0x843e90, 0x7de240; it is *not* an HTTP parameter, which resolves the analyst's open question 2), `基础控件` [basic controls] = ControlModule (0x7c6f04, 59 copies), `参数引擎` [parameter engine] = ParaModule (0x7c6f10, 39), `数控加工` [NC machining] = NCModule (0x7c6f24, 46), `系统日志` [system log] = LogModule (0x7d07d4, 22), and `CAD` (ASCII/UTF-16 L"CAD", 0x7e58ac) = CADModule. The singleton field **+0x2f0 is filled from the `"CAD"` lookup** (0x4af483 `push L"CAD"` … 0x4af4b8 `mov [eax+0x2f0],ecx`), which is the pointer that is NULL in the Wine crash (§12.1). At 0x40aa2d–0x40aafb a dialog caches the four module pointers at `this+0x1dc/0x1e0/0x1e4/0x1e8` (controls / parameters / language / NC).
* Missing-module handling (EVIDENCE): 0x4b6b9c builds `APP组件(` + name + `)cycle require,failed run!` (0x7e6380) and 0x7e6480 holds `）缺失，无法启动！ APP Component lost, failed run!`; 0x4aedaf shows `系统主框架加载失败！(App load failed!)` [main frame load failed]. The mutex-collision message is `SC系列应用程序正在运行，请关闭之后再重新打开！` [An SC-series application is already running, close it and reopen] (0x7e56f4).

### 4.4 Plugin DLL class maps (EVIDENCE)

**CADModule.dll (193 descriptors):**
* Module glue: `CCADModule`, `CCADModuleProvider`, `ICADModule`, `IModule`, `IModuleProvider`, `CCmdDeque`, `ICmd`, `CEditCmd`, `CEditCmdArrayCmd`.
* Glyph model: `IGlyph`, `IGraph`, `CGlyGroup`, `CGlyContour`, `CGlyContourEx`, `CGlyCt`, `CGlyScan`, `CGlyText`, `CTextGlyphAdapter`/`ITextGlyphAdapter`, `CNurbsContour`, `CContourTopTree`, `CGly2ContourEng`.
* Creation commands (`ICreateGlyph`): `CCreateArc`, `CCreateArc3Pt`, `CCreateArcORA`, `CCreateCircle`, `CCreateCircle3Pt`, `CCreateCircleOR`, `CCreateEllipse`, `CCreateLwploy`, `CCreatePoint`, `CCreatePolygon`, `CCreateRect`, `CCreateRoundRect`, `CCreateSegment`, `CCreateSpline`, `CCreateStar`.
* Editable primitives (`IEditableGlyph`): `CEditableArc/Circle/EllipsArc/Lwpoly/Point/Segment/Spline` and matching `CEdit*Cmd`.
* Operations (`COperationCmd` subclasses, 33): Align, AllCraft, AutoSort, BreakGroupGraph, Bridge, CoolPoint, Copy, Create, Delete, DockPt, ExplodeMicoLink, FillGrpha, GraphLayer, GroupGraph, GuideLine, MergeGraph, MicoLinkContour, Mirror, Offset, OverCutContour, Positive, ReverseContour, Rotate, Round, Scale, ScanGraph, ShareEdge, SmoothContour, SplitContour, SwitchGraph, Text2Group, Translate, GraphPropCmd.
* Process geometry: `CAlphaRound`, `CArcRound`, `CBridge`, `CCoolPoint`, `CCutupScrap`, `CFillCircle`, `CGuideCurve`, `CInterpMrg`, `CPathLinkerPlan`, `CRingSort`, `CSSort`, `CSegOffset`, `CShareEdge`, `CSSubSeg`, `CSubSeg`, `CUniformStartPos`, `CUnloadAngle`, `CContoutSmooth`/`IContourSmooth`, `CVelocityPlanning`/`IVelocityPlanning`, `CDynCaptureEng`.
* Import/export: `DxfParse::CDxfParse` + `CDxf{Arc2d,Circle2d,EllipseArc2d,Entity,LWPloyLine2d,MText,Nubrs2d,Point2d,Segment2d,Text}`; `PltParse::CPltFileParse` + `CPlt{Arc2d,Circle2d,EllipseArc2d,Entity,FileRead,InputFile,Nubrs2d,Rect2d,Segment2d}` (HPGL/PLT); `CGCodeFileParse`; `CLIFileBasic/CLICsv/CLIpos/CLIrtl` (list/CSV/position import).
* Nesting: `CSCNest`, `CNestElemBasic`, `CNestResult`, `CPartInfo`, `CSheetInfo`.
* Also `CGlFont3D`, `CWglFontBitmap` (duplicated from MainApp), `CSerialEng`.

**NCModule.dll (132):** see §3.4; `CDog` (licence), `CVirtualMachine`/`IVirtualMachine` (offline simulation), `CFrogJumpSvr`/`CFJSAccSrv` (frog-jump/flying-cut acceleration server), `CVerticalCorrect`, `CDataBuffer`, `CSerialPort`, `ILaserProtocolAdapter` with `CIPGProtocolAdapter`, `CRaycusProtocolAdapter`, YaoUtil logging.

**ParaModule.dll:** `CParaModule`, `CParaModuleProvider`, `IParaModule`, `CXMLParaEngine`. **LangModule.dll:** `CLangModule`, `ILangModule`, `CTxtFile`. **LogModule.dll:** `CLogModule`, `ILogModule`, YaoUtil. **ControlModule.dll:** custom widgets only.

---

## 5. Resource inventory (EVIDENCE — custom `.rsrc` walker)

| Type | Count | Bytes | Notes |
|---|---|---|---|
| BITMAP | 408 | 10,689,950 | ribbon/toolbar/LED images, ids 400–…; language 2052 (zh-CN) |
| "PNG" (named type) | 71 | 1,267,179 | BCGP-style PNG toolbar images; one carries Photoshop 21.0 XMP dated 2025-04-24 |
| DIALOG | 7 | 462 | ids 319, 331, 393, 443 ("Dialog"), 444, 448, 459 — **all have 0 controls** (empty DIALOGEX shells; the UI is created in code) |
| MENU | 2 | 502 | id 128: MFC default 文件/编辑/视图/帮助 [File/Edit/View/Help] with "关于 SC2000(&A)…" [About SC2000]; id 134: context menu 粘贴/确认/取消/闭合/直线/圆弧 [Paste/Confirm/Cancel/Close/Line/Arc] (manual contour tool) |
| STRING | 12 blocks / 56 strings | 1,376 | MFC boilerplate (`SC2000\n\nSC2000\n\n\nSC2000.Document`, 就绪 [Ready], …) |
| ACCEL | 1 | 104 | |
| ICON / GROUP_ICON | 1 / 1 (id 128) | 9,640 | app icon |
| VERSION | 1 | 692 | see §2.3 |
| MANIFEST | 1 | 866 | requireAdministrator |

Languages: 504 entries in 2052 (zh-CN), 1 in 1033 (manifest) — all counts re-verified by the verifier's own resource walker. **INFERENCE (high, corrected):** *almost* all user-visible text is produced at run time from `Lang\lang.txt` keys. The original claim that MainApp `.rdata` contains **zero** CJK strings was an artefact of `strings -e l`, which only emits UTF-16 strings whose code units are ASCII; a direct UTF-16 scan of `.rdata` finds **≈110 distinct genuine Chinese literals (≈3,100 copies)**, see §6.3a. The verifier counts **2,578 distinct `.rdata` UTF-16 literals that are exact `lang.txt` keys** and 12 key-looking literals with no entry (`ec10-1, mv24, newLang22/23/26, pd694/695/696, pd80-1/2/3, pdSave`); the analyst's 2,096/2,088 figures could not be reproduced and are superseded. The resource script is essentially only images.

---

## 6. String inventory (UTF-16 unless noted)

### 6.1 Files and directories touched by MainApp (EVIDENCE — strings, code refs where checked)

| Path string | Purpose / where used |
|---|---|
| `\File\ipAdd.ini` (A and W) | read at 0x4ba190: `[IP] CardIP` (default `10.1.1.168`, 0x4af0e0), `[IP] MonitorIP` (default `127.0.0.1`, 0x4bb788), `[IP] MonitorPort` (`GetPrivateProfileIntA`, default 0x1778=6008, 0x4bb7ae), `[IP] MonitorReconnInterval/MonitorReconnMaxTime` (W variants, 0x45938f/0x4593bf), `[Soft] AlarmDay` (0x4bad13), `[Soft] CheckUserID` (default 0, 0x566e42 — in function 0x566da0). *(Verifier correction: `MonitorIP/MonitorPort` are in `[IP]`, not `[Soft]`; the au3tech URL is a constant assignment, not an INI default. Every other `ipAdd.ini` key — timeouts, `Lang`, `Skin`, `MinHardwareVer`, … — is not read by MainApp directly and must be read by NCModule/other modules.)* |
| `\File\softPara.ini` `[SC2000]` `NormalExit`, `XAxis`, `YAxis`, `ZAxis`, `WAxis` | last position + clean-exit flag; read with `GetPrivateProfileIntW` at 0x567b7b/0x567bc0/0x567c05/0x567c4a, written with `WritePrivateProfileStringW` at 0x45d183–0x45d1ec (axes) and 0x459aa8/0x48f338/0x568365 (`NormalExit`); used at start for the mp18/mp19/mp21 recovery prompts. `JumpAddTime.txt` `[Jump] AddTime` (0x443b85) and `[Axis4Freq] Is4Freq` (0x4afec0) are read the same way (verifier). |
| `\CustomInformation.ini` `[CustomInformation] Custom` and `RunModel` (+ `\NexCut` subfolder, `MP.CustomCompanyPassword`, `mf162` "请输入操作密码" [Enter the password]) | OEM/customer password and run mode; `Custom` read at 0x4543ac/0x5d2f8b, written at 0x5db47d; `RunModel` read at 0x4af03b/0x5d39aa, written at 0x5db81a (verifier inventory of all `*PrivateProfile*` call sites) |
| `\HardPara.xml`, `\LayerPara.xml`, `\ManuPara.xml`, `\SystemPara.xml`, `\_tempLayer.xml`, `\File\ErrorBkManuPara.xml`, `\File\ErrorManuPara.xml`, `\BkHardPara.xml`, `\BkLayerPara.xml`, `\BkManuPara.xml`, `\SecondBkManuPara.xml`, `\*.xml*` | parameter files (ParaModule); the `Bk*` = backup copies present in `SRC/File/` |
| `autosave.chf`, `autosave.dat`, `AutosaveParam1.ini`, `AutosaveParam2.ini`, `ManuContour.dat`, `tempGraph.chf`, `tempIsBreak.ini`, `tempManu.ini`, `\_tempSource.chf`, `\_tempthumbnail.jpg`, `\Graph\Work%d\%d.chf` | autosave / breakpoint-resume state; `*.chf` is the native job format ("chf File(*.chf)|*.chf||") |
| `\File\PithCompensate.pcf` | pitch-compensation table (content: `scFlie/0/eof` = empty) |
| `\JumpAddTime.txt`, `//LanFormatEStr.txt` | tuning constants (see §12) |
| `\Report`, `\Report\LogReport.txt`, `\Report\TotalReport.txt`, `\report.txt`, `\lang.txt`, `lang==0`/`lang==1`, `\report.exe`, `TestReport.txt` | job statistics + report viewer launch (§11) |
| `\Update\MCC100_V` + `.mcf`, `\Update\E310_V80` + `.afb` | firmware update files for the motion card (`mf232`: "控制卡硬件版本过低…自动对硬件进行升级" [controller hardware version too old, will be upgraded automatically]) and for the E310 auto-focus head (`mf701`) |
| `\Module\*.dll`, `PHBX.dll` | plugin loading, pendant |
| `\Dump\` + `%04u%02u%02u-%02u%02u%02u` + `.dmp` | crash dumps |
| `\Log\Code.txt`, `\NexCut\Log\`, `\Help\` | opened with `explorer.exe` |
| `res\SC2000.ico`, `res\splash.bmp`, `res\help.bmp`, `\File\logo.bmp`, `\File\help.bmp` | UI images loaded from disk |
| `ipset.exe` + `\File\` | launches `SRC/File/IPSet.exe` (MSVC 2010 console tool importing IPHLPAPI/MPRAPI — sets the PC's NIC address; *INFERENCE high*) |
| `pingMC.bat`, `pingZF.bat`, `D:\seekEdge.txt` | diagnostics (the `D:\` path is a developer left-over; referenced at 0x58588d) |
| `\\.\COM`, `t\\.\COM%d` | serial ports (`CSerialEng`) |
| `SC2000.exe`, `SC3000.exe`, `SCTube.exe`, `MainApp.exe` | sibling-product process names |
| File filters: `(*.chf)|*.chf|dxf`, `(*.dxf)|*.dxf||`, `csv files(*.csv)|*.csv||`, `Select file(*.nc;*.enc)|*.nc;*.enc|All Files (*.*)|*.*||` | open/save dialogs |

### 6.2 Parameter keys and the XML schema (EVIDENCE)

~~MainApp references **839 distinct `PREFIX.Name` keys**. Their prefixes: GP 330, GRP 175, MP 96, MAC 60, DI 59, MC 44, DO 44, SP 39, SOP 32, LGP 31, MGP 22, ZF 18, IGP 18, AX 12, NP 11, EC 11, MSC 9, AFDA 9, FCP 8, ST 7, HP 6, LT 5, LMP 5, AF 5, LPF 4, GC 4, UN 3, LC 3, FC 3, MS 2.~~ **Verifier re-count (regex `^[A-Z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*$` over `.rdata` UTF-16 literals, file names excluded): MainApp references 969 distinct `PREFIX.Name` keys.** Prefixes: GP 172, GRP 169, MP 90, DI 57, DO 44, MC 43, SP 39, SOP 29, MAC 26, MAC_1..MAC_4 22 each, LGP 19, IGP 18, MGP 18, ZF 18, NP 11, HPA3 10, A0 9, A1 9, AFDA 9, FCP 8, MSC 8, EC 8, AX 7, A2 7, A3 7, ST 7, HP 6, AF 5, LT 5, GC 4, FC 3, UN 3, LC 3, LMP 3, DA 2, MS 2.

Cross-check against `SRC/File/*.xml`: the XML files are `<ParameterRoot><PLayerParam1><GP attr="…"/></PLayerParam1>…` (BkLayerPara: `PLayerParam1..8` each holding one `<GP>`; BkHardPara: `PAxisParam, PHomeParam, PZFParam, PLaserParam, PManuParam, PGasParam, PDOParam, PDIParam, PDAParam, PFCParam, PSoftParam, PMachineAxisConfig, PMachineAxisConfig_0..4`), i.e. **key = `<element>.<attribute>`**. Of 942 element.attribute pairs in the four XML files, **all 942 are referenced by MainApp** (the analyst's "812" and the statement that `A0..A3.*`, `HPA3.*`, `MAC_1..4.*` are not referenced are **refuted** — e.g. `HPA3.IdelSpeed`, `A0.DoubleDriver`, `MAC_1.*` are present as literals, 47 such strings). 27 MainApp keys are not in these files: `GP.ZFVibAbat_Enable`, `GRP.ZoomHeight/ZoomWidth/ZoomRatioIsLock`, `LT.ltDutyfactor/ltEnableTime/ltFreq/ltLaserTime/ltPower`, `SP.BaudRate`, `SP.CardIPAddtInt/CardSubnetMask/CardDefaultGateway`, `SP.LaserIPAdd/LaserSubnetMask/LaserDefaultGateway`, `SP.ZFIPAdd/ZFSubnetMask/ZFDefaultGateway`, `ST.EnableTestAlarm/STIsReturnAfterManu/Type/xyCycle/xySleep/zEndPos/zStartPos` — network settings and laser-test/stress-test values living elsewhere (the IPs are in `ipAdd.ini [IP]`). Prefix meanings (INFERENCE, high, from the lang keys `pd*` such as `pd2408 无限卷料.切断板材-X左侧坐标`): GP = layer/process ("graph process") parameters, GRP = graph/geometry rules, MP = machine parameters, MC = manual/jog control, SP = system parameters, SOP = system operation statistics (`SOP.DeviceTotalLaserOnTime`, `SOP.DeviceXAxisTotalMoveLength`, `SOP.EnableRemoteMonitor`, `SOP.RemoteMonitorHeartbeat`, `SOP.MonitorStartID/EndID`, `SOP.HardwareType`), DI/DO = digital I/O mapping, MAC = machine/axis config, ZF = height follower, AF/AFDA = auto-focus, EC = EtherCAT, HP = homing, LGP/MGP/IGP = laser/marking/… group params, NP = nesting.

### 6.3 Language keys (EVIDENCE)

`Lang\lang.txt` is UTF-16LE with BOM `FF FE`, CRLF, format `ID#中文#English` (`Lang\Readme.txt`, UTF-8: "内容格式为： ID#内容" [content format: ID#content]; it also documents that a missing ID is displayed as the ID itself, and that parameter-type entries must be `主条目.子条目` [main.sub] or the software reports "Lang Format Error"); 3,485 lines, **3,427 keys** (re-verified; `lang.txt.orig` from 2024-01 has 2,800). Prefix statistics (verifier re-count): pd 1290 (parameter descriptions), mf 409 (menus/frames), mp 200 (message prompts), A 164 (dated additions `A250607_2` = added 2025-06-07), gp 161, RegName 144, zf 92, newLang 80, hp 74, SystemRWRegName_ 47, SBT 41, mv 37, af 36, RTC_RO_ 33, RORegName_ 32, ec 31, ap 28, ss 25, cp 23, EtherAxisInfos_ 22, **lp 20, slp 20, ls 14 (licence), ab 12 (activation)** (analyst had "ls 20"). `Lang\语言修改记录.txt` [language change log] (UTF-8) records additions per version (e.g. 2022-4-17 v1.0.392.4 added `mf520..mf524`; 2022-12-6 added `mf252/mf253`).

**Which licence-UI keys MainApp actually uses (verifier):** MainApp `.rdata` contains `dogState_*` (all 9), `dogActiveReslut1,2,3,5,6,7,8,9,10,11` (not 4), `mf431-1`, and only **`ab10`** of the `ab*`/`ls*` family. None of `ab1..ab9`, `ls0..ls13` appears in MainApp or in any `Module/*.dll` — the activation-code *generation/export* strings (`ls5` 文本文件(*.txt), `ls6` 保存激活码文件 [save activation-code file], `ls7` 硬件ID：%d, `ls8` 激活码：%d 授权天数：%d, `ls10/11` 激活码数据存储成功/失败 [activation data stored OK/failed]) are stale or belong to a vendor tool, not to this build. The code-input dialog uses raw Chinese literals instead: `输入解锁码长度错误` / `输入解锁码格式错误` [unlock-code length / format error] (0x823cd4/0x823ce8, used at 0x538494/0x5385a7). `Lang\lang.ini` selects the UI language (`Lang=English`; hint lists 0 简体中文 … 10 Turkdili). Ten additional language files exist (French, German, Italian, Polish, Portuguese, Russian, Spanish, Turkdili, Vietnamese, Vietnamese_LE).

Keys resolved for this document (中文 | English):
* `dogState_normal` 加密信息正常 | Encryption is OK; `dogState_TrialOver` 系统试用期结束 | Trial period is expired; `dogState_ClockIllegal` 板卡时钟非法 | Hardware clock is illegal; `dogState_ClockBroken` 获取板卡时钟失败 | Get hardware clock failed; `dogState_Unauth` 非授权硬件 | Unauthorized hardware; `dogState_DataBroken` 硬件数据区被损 | Data sector is error; `dogState_VerifError` 系统认证错误 | Verification is error; `dogState_NotTheApp` 非匹配应用程序 | Unauthenticated App ID; `dogState_NoHardware` 未发现硬件 | No Hardware.
* `dogActiveReslut1..11`: 加密-未找到硬件 [no hardware], 未授权硬件 [unauthorised hardware], 时钟值非法 [clock illegal], 数据区受损 [data sector damaged], 密码错误 [invalid activation code], 硬件ID不匹配 [hardware ID mismatch], 厂商ID不匹配 [vendor/user ID mismatch], 密码过期 [code already used], 板卡时钟过慢 [board clock too slow], 时钟操作失败 [clock operation failed], 密码输入错误 [code entry error].
* `ab1` 激活 | Activate; `ab3`/`ls2` 永久授权 | Permanent license; `ab8` 请输入授权激活码 | Please enter the activation code; `ab9` 激活成功,剩余天数 | Activation successful, remaining days; `ls0` 激活码 | Activation code; `ls1` 授权天数 | Activation days left; `mf228` SC系统授权试用期还剩余：%d天 | Hardware expires in %d days; `mp1` 试用期结束; `mp5` 授权时间剩; `mf431-1` 打开加密日志 | Open Dog Log; `mf496` 没找到加密狗 | No dongle; `gp14` FPGA程序未注册 | FPGA program not registered.
* `mp01` 手柄适配器已断开 | Remoter disconnect; `A250613_1` 手柄断开 | The handle is disconnected; `A240828_1` 非活动页面，手柄不可用! [Inactive page, handle not available]; `mf225/226` 远程监控连接成功/失败 | Remote monitor connect success/failed; `mf437` IP设置完成 | IP Set Done; `mf147` 微软雅黑 | Segoe UI (UI font); `mf650` 未命名- | Untitled-; `hp37` 参数需软件重启后生效 [restart required]; `mf1001` 检测到主控制器类型有变化，请重新启动软件 [controller type changed, restart]; `mp18/19/21` abnormal-exit recovery prompts; `mp119` 硬件连接成功，是否对系统进行回原？ [hardware connected, home now?]; `mp151` auto-focus head must home first; `A250607_2/3` safety confirmation for fiber/CO2 laser; `A250606_0/1` 文件错误 / 仅支持传输<500MB的文件 [only files <500 MB can be transferred] (HTTP upload); `A250516_5/6` PWM(5V)频率/占空比 [PWM frequency/duty]; `A241105_0/1` Wifi / 运行时长 [run time]; `mf252/253` 加工报告[最新]/[全部] | Work Report [New]/[All]; `mf524` DLL内核错误 | Nest-DLL kernel error.

### 6.3a Chinese literals that *are* compiled into MainApp (verifier addition, EVIDENCE)

A direct UTF-16 scan of `.rdata` (0x7bf000–0x932284) for strings containing CJK code units whose bytes are not both printable ASCII (to exclude mis-aligned ASCII) gives ≈110 distinct genuine strings. Apart from the module names of §4.3 they fall into these groups:

| Group | Strings (VA of first copy) |
|---|---|
| MCC100 register-group names, 15 strings × **53 copies** (a header-level static initialiser, see 0x6a46f2–0x6a4809 where they are assigned to globals at 0x93b90c… together with keys `SystemRWRegName_41`, `pd0..pd3`) | `管理员加密数据` [admin encryption data] 0x7c5a90, `用户加密数据` [user encryption data], `时间日期` [date/time], `识别码数据` [ID-code data], `加密数据域访问开关` [encryption-data-area access switch], `写缓存的飞切编码器数据` [write buffered fly-cut encoder data], `读缓存的编码器数据` [read buffered encoder data], `高级控制` [advanced control], `标准控制` [standard control], `轴减速停止` [axis decelerate-stop], `轴运行` [axis run], `轴回零` [axis home], `IO高级控制` [IO advanced control], `缓存控制` [buffer control], `写缓存数据` [write buffer data] |
| Nesting-kernel error texts (the `排样内核错误代码.txt` table compiled in; index 1 → key `mf496`, others raw) | 0x7dde68 `mf496`, 0x7dde74 `打不开临时文件`, `排样出现不封闭轮廓`, `打不开排样临时文件`, `打不开板材临时文件`, `打不开py`, `找不到grp`, `离散失败`, `geo失败`, `cut失败`, `nestout失败`, `导入外轮廓不封闭`, `导入存在重复的点`, `输入零件数量为0` [part count is 0], `内存分配失败`, `未知错误` [unknown error]; used at 0x495f0b–0x4960c1 (`cmp [ebp-0x58c],1` → `mf496`, else `mf524` + `--` + text) |
| Breakpoint-resume diagnostics | 0x7d0a08 `轮廓不在图形中！` [contour not in graph], 0x7d0a24… `断点_引线上，纠正到轮廓末端` [breakpoint on lead-in, corrected to contour end], `断点_轮廓上，运动到后端/前端` [breakpoint on contour, move to back/front], `断点_轮廓上，开环轮廓前端/末端` [open contour front/end], `断点在轮廓间` [breakpoint between contours], 0x841f58 `断点查找错误` [breakpoint search error], 0x7d0934 `NC上报轮廓ID溢出 - ` [NC reported contour-ID overflow] |
| OpenGL init errors | 0x7c4370 `OpenGL像素初始化错误！` [pixel-format init error], `OpenGL绘图描述表创建失败！` [rendering-context creation failed], `OpenGL绘图描述表错误！` |
| Fonts | `宋体` [SimSun] 0x7c7358, `微软雅黑` [Microsoft YaHei] 0x7e88a4, `幼圆` [YouYuan] 0x84c26c, `简体中文` 0x887e9c |
| Misc UI/file messages | `没有可加工数据！` [no machinable data] 0x842d08, `图形引擎存在异常！` [graph engine has an exception] 0x7dd5bc, `暂不能读取Dxf文件中的面域(REGION)图元，请先在AutoCAD中将其'分解'` [DXF REGION entities not supported, explode them in AutoCAD first] 0x7e2d28, `非法文件路径`/`空文件`/`文件被损坏`/`非LI文件` [illegal path / empty file / file damaged / not an LI file] 0x7ffa70…, `是否保存文件？` [save file?], `Send Device %d, 加工/待机` [processing/standby] 0x7de2e0/0x7de310, `双平台交换` [dual-platform exchange] 0x847330, `序号`/`流程动作` [index / flow action], test-report dialog strings `测试人`, `生成报告`, `板卡ID不能为空！` [card ID must not be empty], `测试人ID不能为空！`, `测试报告生成成功！`, `Ping控制器`/`Ping调高器` [ping controller / height follower], `轴状态复位` [axis state reset], `DA1值输入错误！`, `DA2值输入错误！`, `PWM值输入错误！`, `测试数据值为空`, `测试数据导出成功`, ribbon category names `扩展板`/`电动头`/`调高器`/`控制器`/`排样`/`高级`/`工艺`/`绘图`/`开始` [expansion board / motorised head / height follower / controller / nesting / advanced / process / drawing / start] 0x817860–0x8178a0, `工艺库` [process library], `背景图层`/`图层` [background layer / layer], `加工统计` [processing statistics] 0x7dd92c (= report.exe window title used with `FindWindowW`) |

**INFERENCE (high):** the localisation is therefore not 100 % key-based; a port that reuses `lang.txt` still has to supply translations for these ≈100 literals (mostly diagnostics and the nesting error table).

### 6.4 Format strings (134 in MainApp)

Examples: `%04d-%02d-%02d %02d:%02d:%02d`, `%04u%02u%02u-%02u%02u%02u` (dump name), `\Graph\Work%d\%d.chf`, `COM%d`, `%d.%d.%d.%d`, `DA%d -- %.2f`, `ECAF WriteReg: Add %d, Val %d`, `OneProcess:%d [GetPara:%d, GetPt:%d, SmoothCt:%d, PlanPath:%d]; Pt2Item:%d; Item2Hal:%d` (pipeline timing log — names the stages graph→parameters→points→contour smoothing→path planning→items→HAL), `Open devive time:%d`, `Import graph - System Available Memory shortage - %d/%d`, `,%.2fx%.2f mm`, `,%.2f m`, `,%.2f Sec` (report.txt columns).

---

## 7. Start-up sequence (INFERENCE from the annotated disassembly, medium-high)

1. `CMainApp::InitInstance` region (0x4aeb80–0x4aed40): enumerate processes; if `SC3000.exe`, `SCTube.exe` or another `MainApp.exe` is running, abort; create mutexes `SC3000` and `HighSea_SC1000`; install the crash handler (0x4ae650: `SetUnhandledExceptionFilter`, then `LoadLibraryA("msvcrt.dll")` + `GetProcAddress("_XcptFilter")` + `VirtualProtect` to patch the CRT's own filter so the custom minidump filter cannot be overridden — the well-known "SetUnhandledExceptionFilter protection" trick); register the `.chf` file type in the registry (0x4ae700).
2. Load `\File\ipAdd.ini` (0x4ba190) and `\File\softPara.ini`; read `[IP] CardIP/MonitorIP/MonitorPort`, `[Soft] AlarmDay`, assign the constant key URL; choose the window title by laser type (`NexCut Fiber laser` / `NexCut CO2 laser` / `NexCut Blue laser`, UTF-16 at file offsets 0x3e7794/0x3e77bc/0x3e77e0; the version literal `v0.0.0.52` is at 0x470508).
3. Load plugins from `\Module\*.dll` (0x4b6280).
4. Big init routine 0x566ee0 (called from 0x568b42; the `CheckUserID` read at 0x566e42 is in the *preceding* function 0x566da0 — verifier correction): shows `hp37`, evaluates laser-type safety prompts `A250607_2/3`, queries the card licence state (virtual call → NCModule `CDog`), maps the numeric state to `dogState_*` keys (0x56750a–0x5675c1; a magic constant `0xb77be948` is compared at 0x5674ee), starts the remote monitor (`mf225/226`), restores `XAxis/YAxis/ZAxis` and `NormalExit` from `softPara.ini` and offers recovery (`mp18/mp19/mp21`), runs the firmware-version check (`\Update\MCC100_V*.mcf`, 0x565d20; `mf232`/`mf176`), prompts homing (`mp119`), etc.
5. Load `PHBX.dll` (0x586e60) and open the pendant (`Xinit`, `XOpen`); start the `CHidUsb` joystick reader if configured; register device notifications (0x45ad30).
6. Create the multimedia timer (0x56535e) for the status loop.

---

## 8. Licensing / protection — the three mechanisms

### 8.1 Controller-card licence ("dog" in NCModule) — EVIDENCE

* `NCModule.dll` contains RTTI `.?AVCDog@@` and strings: `Active CodeStr data: Hid:%u CodeTime:%d-%d-%d %d:%d:%d LincensDay:%d UserCode:%d`, `-Active: Active OK`, `-Active: clock error!!!!`, `-Active: Code input error !!!`, `-Active: Data broken!!!!`, `-Active: hard ID is not match!!!`, `-Active: Logo Name no match!!!!`, `-Active: reset clock OK`, `-Active: reset lastLincenseTime OK`, `--Active: code overdue!!!!`, `get card clock fail!!`, `initial without card clock, successful`, `Key initial (initial without card clock), invalid keyword!`, `verify data area failed!`, `\Log\Code.txt`, `LocalPcTime:%d-%d-%d %d:%d:%d - %s`.
* `SRC/Log/Code.txt` (88 KB) is the "dog log" (`mf431-1` 打开加密日志 [Open Dog Log]). **Verifier correction — it is not only the repeated `verify data area failed!` line.** Line-type census: 683 × `verify data area failed!`, 103 × `Arm Clock is invalid!`, 88 × `Admin data was broken (read admin data)!!!`, 7 × `Admin data is not current App data`, 6 × `User data is error`, 5 × `get card clock fail!!`, 5 × `reset card clock fail!!`, plus the activation history. (`Arm Clock is invalid!` / `reset card clock fail!!` are **not** strings of the shipped NCModule.dll, so earlier NCModule builds wrote them; "Arm" = the card's ARM CPU clock.) Activation records (`<date> Active codeStr:<20 letters>` → `Active CodeStr data: Hid:<u32> CodeTime:<t> LincensDay:<n> UserCode:<n>` → result): 2024-7-15 code `KFUGCADZFVHVBIYZXKYC` (Hid 3662239894, 7 days, UserCode 224) → `Logo Name no match!!!!`, then `EIKTMASOGOXBFSBKHAIZ` (UserCode 0) → `Active OK`; 2024-11-11 `OBYPHMAMCHLPWGPBXICL` (Hid 3642650783, 3 days, UserCode 999) → alternating `reset clock OK` / `lastLicenseClock:2054-11-11 … code overdue!!!!`; 2025-6-17 `ZMILVIOQKBPXJNEZRSZC` (Hid **3709908410**, **LincensDay 180**, UserCode 999) → four times `hard ID is not match!!!`, then entries stamped **2055-6-17** (card clock 30 years ahead, `lastLicenseClock:2085-6-16`) → `code overdue`, and finally at `LocalPcTime:2025-6-17 17:52:42` → `-Active: reset clock OK`. The log also contains a `2000-0-1` timestamp (uninitialised clock). **INFERENCE (medium):** the last successful activation on this machine granted a 180-day licence from 2025-06-17 (nominal expiry ≈ 2025-12-14), after the card clock had been reset; three different `Hid` values over time mean the activation codes were generated for different hardware IDs (or the card ID changed).
* `ipAdd.ini [Soft] CheckUserID=109` is read by MainApp at 0x566e42 (`GetPrivateProfileIntA`, default 0). The code at 0x566e55–0x566eab (EVIDENCE) calls vtable slot +0x2c0 of the module cached at `this+0x1d8` (the NC module, cf. §4.3), takes `[result+0x48]` as the card's user code `u`, then: if the ini value is 0 and `u != 0x6d (109)` nothing happens; otherwise if `ini != u` it calls slot +0x2ac of the same module with argument 9. *(Verifier correction/downgrade: so **109 is also hard-coded in MainApp** and is compared with a value from the NC module; the activation codes in `Code.txt` carry `UserCode` 224/0/999, so `UserCode` in the code string is not simply the same number. What "9" means (a dog-state code? `dogActiveReslut9` = 板卡时钟过慢?) is unknown — INFERENCE low.)*
* MainApp only *displays* the state (`dogState_*`, `dogActiveReslut*`) and has a code-input dialog (`CCodeInputDlg`, RTTI present; it uses `ab10` and the raw literals `输入解锁码长度错误/格式错误`, see §6.3). The `ab1..9`/`ls*` keys are unused by this build.
* The card-side data areas are named in MainApp's own register table (§6.3a): `管理员加密数据` [admin encryption data], `用户加密数据` [user encryption data], `时间日期` [date/time], `识别码数据` [ID-code data], `加密数据域访问开关` [encryption-area access switch] — i.e. the licence lives in dedicated MCC100 register groups next to the motion registers (INFERENCE high).

**INFERENCE (high):** the primary licence is stored in the **MCC100 card** (a data area + a real-time clock on the board, read over the same Modbus link), with a hardware ID, vendor ID (109), licence days and activation codes. This is what "trial period", "hardware clock illegal" etc. refer to. It does not involve the USB dongle at all. Whether the card refuses motion commands when unlicensed cannot be determined statically from MainApp (see Open questions).

### 8.2 USB HID dongle VID 0x3689 / PID 0x8762 — EVIDENCE

* **MainApp.exe contains no occurrence** of 0x3689/0x8762 (immediate or string; also checked decimal 14473/34658 and "USBKey").
* **`SRC/AutoNest.dll`** (MSVC 6 build, PDB `DLL\HePin\Release\AutoNest.pdb`, imports MFC42.DLL/MSVCRT/MSVCP60) contains:
  * ASCII string `HID#Vid_3689&Pid_8762` (file offset 0x890a0), device-class registry path `SYSTEM\ControlSet001\Control\DeviceClasses\{4d1e55b2-f16f-11cf-88cb-001111000030}` (the HID interface GUID), `\\?\`, `\\.\`, `\hid.dll`, `\SetupApi.dll`, and the dynamically-resolved names `HidD_GetAttributes`, `HidD_GetFeature`, `HidD_SetFeature`, `HidD_GetPreparsedData`, `HidD_FreePreparsedData`, `HidP_GetCaps`, `HidP_GetValueCaps`, `HidP_GetButtonCaps`, `HidP_SetUsages`, `HidP_GetUsages`, `HidP_SetUsageValue`, `HidP_GetUsageValue`, `HidP_GetScaledUsageValue`, `HidP_MaxUsageListLength`.
  * Disassembly at 0x1006e6f9–0x1006e74a: `CreateFileA(path, 0xC0000000 (GENERIC_READ|WRITE), share 3, OPEN_EXISTING)` → `HidD_GetAttributes` → `cmp ax,0x3689` (VendorID) and `cmp cx,0x8762` **or** `cmp cx,0x2020` (two accepted ProductIDs).
  * SM2 elliptic-curve constants as hex strings at 0x88f3c–0x89008: `28E9FA9E9D9F5E344D5A9E4BCF6509A7F39789F515AB8F92DDBCBD414D940E93` (SM2 *b*), `32C4AE2C1F1981195F9904466A39C9948FE30BBFF2660BE1715A4589334C74C7` (SM2 *Gx*), `BC3736A2F4F6779C59BDCEE36B692153D0A9877CC62A474002DF32E52139F0A0` (SM2 *Gy*), `FFFFFFFEFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF00000000FFFFFFFFFFFFFFFC` (SM2 *a*); plus `ex_sim`, `EE6743DBECCFD909`, `%02X`, a 16-byte `%02x…` formatter and `0123456789ABCDEF` tables.
  * Exports `Nest_CheckLock` (0x1004c57c) and `Nest_GetId` (0x1004c64b) call the SDK verification routine 0x1006b290 with constant arguments (0x12c4ca4, 0x7ec54af5 / 0x374d0e51, context 0x1008966c) — re-verified (four such calls in 0x1004c58c–0x1004c62a).
  * AutoNest exports 26 symbols in total (verifier): the 18 `Nest_*` C functions plus `HepinDLL` ctor/dtor, `?Nest_GetOddCountByNest@HepinDLL@@QAEHH@Z`, `?Nest_ReadOddData@HepinDLL@@QAEXAAVstNestOdd@@H@Z` (remnant/"odd" data readers, not imported by CADModule) and `CDPoint2`/`CPart2` copy helpers. Version resource: "HePin DLL", "www.smartnest.com.cn", "smartnest_service@163.com". `SetupDi*`/`Hid*` are resolved at run time via `LoadLibraryA`/`GetProcAddress` (imports 0x1007505c/0x10075060), which is why they are absent from the import table. The VID/PID check reads the `HIDD_ATTRIBUTES` returned by `HidD_GetAttributes` (`[ebp-0x64]` VendorID, `[ebp-0x62]` ProductID).
* `CADModule.dll` imports exactly these AutoNest functions: `Nest_CheckLock`, `Nest_GetId`, `Nest_EncodeByKey`, `Nest_EncodeNew`, `Nest_SetCalNew`, `Nest_AutoNest`, `Nest_SetNestPara`, `Nest_SetPartPara`, `Nest_WritePartData`, `Nest_WriteSheetData`, `Nest_ReadNestNum`, `Nest_ReadPartNumbyNest`, `Nest_ReadNestResult`, `Nest_RegProgressCB`, `Nest_GetErrorCode`, `Nest_Release`, `HepinDLL` ctor/dtor.
* `SRC/排样内核错误代码.txt` [nesting-kernel error codes]: `0--正常` [OK], **`1--没找到加密狗` [dongle not found]**, `2--打不开临时文件` [cannot open temp file], `3--排样出现不封闭轮廓` [open contour], `4/5--打不开排样/板材临时文件`, `6--打不开py`, `7--找不到grp`, `8--离散失败` [discretisation failed], `9--板材尺寸太小` [sheet too small], `10--geo失败`, `11--cut失败`, `12--nestout失败`, `13--导入外轮廓不封闭`, `14--导入存在重复的点` [duplicate points], `15--线段太短` [segment too short], `100--内存分配失败` [malloc failed].
* AutoNest also dynamically loads `smartnest.dll` (string present) and links `Dxf2Grp.dll` (`Dxf2Grps`, `CPart2`) and `CircleFitDLL.dll` (`Rmax_CircleFit`).

**INFERENCE (high):** the USBKey is a feature-report-based (SetFeature/GetFeature) crypto token using SM2 (Chinese ECC) challenge/response, consumed by the nesting SDK ("HePin" = 合拼, "combine/nest") to unlock the **auto-nesting** feature and possibly to identify the licensee (`Nest_GetId`/`Nest_EncodeByKey`). Its absence yields nesting error 1 and the MainApp message `mf496` "No dongle". It does **not** gate cutting, motion, file I/O or the UI. The `2020` alternative PID suggests a second dongle model from the same vendor.

**Wine implications (INFERENCE, medium-high):** AutoNest.dll opens the dongle through `SetupDi*` + `CreateFileA` on the `\\?\HID#Vid_3689&Pid_8762…` path and uses `HidD_GetFeature/SetFeature`. Under Wine this requires (a) the USB HID device being visible to Wine's `hidclass`/`winebus` drivers, which in turn needs read/write access to the corresponding `/dev/hidraw*` node (udev rule for `3689:8762`), and (b) Wine's implementation of feature reports over hidraw (`HIDIOCSFEATURE`/`HIDIOCGFEATURE`), which exists in modern Wine (≥ 6.x). The registry path `SYSTEM\ControlSet001\Control\DeviceClasses\{4d1e55b2-…}` that AutoNest also enumerates is only populated by Wine's PnP layer when the device is actually attached through winebus. Even with the dongle working, **AutoNest.dll itself cannot load under Wine without `MFC42.DLL`, `MSVCRT.dll` and `MSVCP60.dll` (the package ships none of them; Wine has builtin `msvcrt`/`msvcp60` but no `mfc42`)** — this is the direct cause of the crash in `SRC/Dump/20260911-175115.dmp` (§12.1). No patching or bypassing is described or recommended here.

### 8.3 MainApp's own HID code (`CHidUsb`) — EVIDENCE

* Class `CHidUsb` at 0x452200–0x452a80: `HidD_GetHidGuid` (0x452279) → `SetupDiGetClassDevsW(guid, 0, 0, 0x12)` (0x4526aa) → enumerate interfaces → `SetupDiGetDeviceInterfaceDetailW` → for each device path call the parser 0x452310, which scans case-insensitively for `v i d _` and `p i d _` (byte compares 0x76/0x56, 0x69/0x49, 0x64/0x44, 0x5f) and then parses 4 hex digits each → open with `CreateFileW(path, access, share 3, …, OPEN_EXISTING)` (0x452882) → `HidD_GetPreparsedData`, `HidP_GetCaps` (result compared with 0x110000 = HIDP_STATUS_SUCCESS), `HidP_GetSpecificValueCaps`, `HidD_FreePreparsedData` → overlapped `ReadFile` loop with `WaitForSingleObject(…, 3000)` / `GetOverlappedResult` / `CancelIo` (0x452960–0x452a64). String `PIPE_00` (0x7d0f8c) is pushed at 0x452835 while building the path.
* The caller 0x59b080 chooses the target IDs from the system-context field `+0x4028`: value 1 or 2 → strings `"1000"`/`"2016"` (VID_1000&PID_2016), otherwise `"6125"`/`"2012"` (VID_6125&PID_2012); it builds `VID_xxxx`, `PID_xxxx`, `----` and matches them with `wcsstr` (0x59b5b0) against the enumerated path; on failure it shows `mp01` 手柄适配器已断开 [remote adapter disconnected]. Callers of 0x59b080: 0x45b1ca (WM_DEVICECHANGE handler, after `RegisterDeviceNotificationW`) and 0x56526c (startup).
* The input-report parser at 0x599a00–0x59a100 compares report bytes with 0xbc/0x55 and reads fields at `+0x4030…+0x4040` (axis/button mapping — cf. `CJoystickMatchDlg`).

**INFERENCE (high):** `CHidUsb` is the USB *hand-wheel / joystick* driver (two supported vendor/product pairs), unrelated to licensing. (The two VID/PID pairs are not in the public USB-ID list; *INFERENCE low:* generic Chinese hand-wheel/gamepad receivers.)

### 8.4 Wireless pendant — `PHBX.dll` — EVIDENCE

Loaded with `LoadLibraryW(L"PHBX.dll")` at 0x586e87; symbols resolved: `Xinit`, `XOpen`, `XGetInput`, `XSendOutput`, `XClose` (other exports `XGetChannel`, `XGetDevID`, `XGetDevRssi`, `XGetDevTxstate`, `XGetFID`, `XGetVersion`, `XWritDev`, `XWritFid`, `SetGetKeyCallbackFunction` are not referenced by MainApp). PHBX.dll (MSVC 6, 2025-06-05) imports `HID.DLL`: HidD_SetFeature, HidP_GetCaps, HidD_GetPreparsedData, HidD_GetAttributes, HidD_GetHidGuid, HidD_GetFeature; `SETUPAPI` (ANSI variants) and ADVAPI32 registry functions. *INFERENCE (high):* the pendant's 2.4 GHz USB receiver is a HID device configured through feature reports; RSSI/channel/FID are radio-link management. Under Wine the same hidraw requirements as §8.2 apply.

---

## 9. Network endpoints and telemetry

### 9.1 MainApp WinHTTP client (`CHttpClient/1.0`) — EVIDENCE

Four functions in 0x406100–0x407fc0, user agent `CHttpClient/1.0` (VAs 0x7c4840, 0x7c4920, 0x7c4bb4):

| Function | Method / path | Headers | Body / notes | Callers |
|---|---|---|---|---|
| 0x4060b0 (ctor `CHttpClient(CString host, WORD port)`; 0x406100 is inside it) | stores host at `this+0` (CString) and port at `this+0x1c`; obtains the **LangModule** via `call 0x4aeb00` + `push L"语言包"` + `call [vtbl+0xc]` and caches it at `this+0x48` (for error texts) — *verifier correction: `语言包` is the language module's registered name, not a host/port parameter*; then `WinHttpOpen(L"CHttpClient/1.0", 1, 0, 0, 0)` (0x40615c) → `WinHttpConnect(hSession, host, port, 0)` (0x40618b) and request `?Name=…&Password=…` | | login; strings `/NexCut/Login` (0x7ce1ec), default user `NexCut` (0x7ce1dc), default password `12345678` (0x7ce1c8) are pushed at 0x434acb–0x434af0. **The ctor has exactly one caller, 0x488145 (fn 0x4880b0), reached from the login site 0x434ab7; that site passes `push 0x1f90` = port 8080 (0x434a51) and a host string taken from the currently selected row of a `CBCGPGridCtrl` (`GetCurSel`/`GetRowId` at 0x4349cd–0x4349fa) via fn 0x45a4a0** | 0x434a60 (login dialog) |
| 0x406940 | `POST /NexCut/File/LoadFile` | `Content-Type: application/json`, `Accept: */*` | body `{"fileName":"…"}` ; parses reply for `"state"` `true/false` and `"message"` | 0x463260, 0x466f31 (ribbon "Sync Device…" / file list) |
| 0x406fc0 | generic `POST` | `Authorization: DebugWithSuperpermissions`, `Accept: */*`, `Connection: keep-alive` | JSON reply `"state"`/`"message"` | 0x40635c |
| 0x407430 | `POST /NexCut/File/UploadGCode` | `Content-Type: multipart/form-data; boundary=--------------------------938424730992556897868402`, part header `Content-Disposition: form-data; name="file"; filename="…"` + `Content-Type: application/octet-stream` (0x7c4b37/0x7c4b66), plus an `Authorization: ` header (0x7c4c04) carrying a token from the login reply | streams the file in 0x1000-byte `WinHttpWriteData` chunks; error keys `A250606_0` 文件错误 / `A250606_1` <500 MB | 0x407f8a ← ribbon command builder 0x531210 (labels `Export Project...`, `Sync Device...`, `offline upload...`) |

`IsNetworkAlive` is called before these operations (0x48f260, callers 0x493316/0x52df60).

**INFERENCE (medium):** this is a "NexCut" HTTP file service on a device reachable on **port 8080** (hard-coded) at a user-selected host (the build is literally named `NexCut_X1_Http`) used for pushing G-code/projects from the PC to a device/server. No host name is hard-coded; the port is. It is not required for normal operation.

### 9.2 Remote monitor 47.104.17.21:9001 — EVIDENCE

* `File\ipAdd.ini`: `MonitorIP=47.104.17.21`, `MonitorPort=9001`, `MonitorReconnInterval=300000`, `MonitorReconnMaxTime=1`, `MonitorTimeout=3000`, `MonitorMaxSendTime=3`, `MonitorMaxRecvTime=1`, `MonitorSendInterval=1000`, `MonitorCore=500`.
* MainApp reads `[IP] MonitorIP` (`GetPrivateProfileStringA`, default `127.0.0.1`) / `[IP] MonitorPort` (`GetPrivateProfileIntA`, default 6008) at 0x4bb788–0x4bb7b3, and `[IP] MonitorReconnInterval/MonitorReconnMaxTime` (W variants at 0x45938f/0x4593bf); immediately afterwards it **assigns** the key-server URL `http://www.au3tech.cn/key/` (0x4bb7d5 → `mov ecx,[obj]; add ecx,0x30c; call 0x4bd2c0`, where 0x4bd2c0 is a one-line wrapper around the CString assignment 0x409c00) to field +0x30c of the settings object. ~~*INFERENCE (medium):* it is the default of another `[Soft]` key~~ *(verifier: refuted — it is a hard-coded constant, not an INI default; 142 code sites touch a `+0x30c` field so the consumer could not be isolated statically).* Parameters `SOP.EnableRemoteMonitor`, `SOP.RemoteMonitorHeartbeat`, `SOP.MonitorTestInterval`, `SOP.MonitorStartID`, `SOP.MonitorEndID` control it; UI messages `mf225/226`.
* The actual socket client is `NCModule.dll` (`CMonitorHalAPI`, `CMonitorModbus`; strings `MonitorIP/MonitorPort/MonitorTimeout/MonitorMaxSendTime/MonitorMaxRecvTime/MonitorSendInterval/MonitorCore`, `Content-Type:application/json;charset=UTF-8`, boost `json_parser` read/write). NCModule also has a hand-rolled HTTP/1.1 client: `%s %s HTTP/1.1`, `Host`, `Accept: text/plain`, `Content-type: application/x-www-form-urlencoded`, `Content-Length`, `Connection: keep-alive`, `POST`, form fields `logID=`, `&isText=`, `&machineID=`, `&logData=` and a base64 alphabet.

**INFERENCE (medium-high):** telemetry = (a) periodic JSON heartbeat/status frames ("Modbus"-style register snapshot serialised to JSON, hence `CMonitorModbus`) to `MonitorIP:9001` (an Alibaba Cloud address, 47.104.0.0/16 is Aliyun Qingdao), and (b) log-file upload keyed by `machineID`. Payload field names are inside NCModule and belong to the NCModule analysis. With `SOP.EnableRemoteMonitor=0` or an unreachable host the software continues (`mf226` is informational).

### 9.3 Other network-related strings

`ping 10.1.1.168 -l 1000 && ping 10.1.1.169 -l 1000 && pause` (`system()` at 0x498d0c, diagnostic button), `\File\ipset.exe` launcher, `SP.CardIPAddtInt/CardSubnetMask/CardDefaultGateway`, `SP.ZFIPAdd`, `SP.LaserIPAdd` parameters (the IPs in `ipAdd.ini [IP]`: Card 10.1.1.168:502, ZF 10.1.1.169:502, OnBZF 10.1.1.168:999, OnBLaser :888, Laser 10.1.1.170:10001, AF :888, AdvAF :666, EC :888, AdvEC :666, EC3710 10.1.1.170:502).

---

## 10. Third-party dependency map

| Component | Version / build | Used by | How |
|---|---|---|---|
| BCGControlBar Pro (`BCGCBPRO2210u100.dll`, 8 MB; skins `BCGPStyle2007Obsidian2210.dll`, `BCGPStyle2010Black2210.dll`) | 22.10, VC10 Unicode | MainApp (1549 imports), ControlModule (66) | Ribbon (`CBCGPRibbonBar/Button/Edit/Slider/CheckBox/Label/StatusBar`), docking (`CBCGPDockingControlBar`), grids (`CBCGPGridCtrl` 300 imports — the most-used class), property lists (`CBCGPPropList/Prop/ColorProp`), gauges (`CBCGPGaugeImpl/TextGaugeImpl/StaticGaugeImpl`), `CBCGPVisualContainerCtrl`, `CBCGPChartVisualObject`, `CBCGPProgressDlg`, `CBCGPWorkspace` (settings persistence), `CBCGPTabView`. `ipAdd.ini Skin=1` selects the skin DLL. |
| MFC 10 / CRT 10 (`mfc100u.dll`, `msvcr100.dll`, `msvcp100.dll`, `vcredist_x86.exe`) | VS2010 SP1 | everything built in 2020–2025 | |
| Boost 1.53 (static) | | MainApp (format/function/lexical_cast/algorithm), NCModule (property_tree JSON, spirit) | |
| `AutoNest.dll` ("HePin") | MSVC 6, 2023-04-28 | CADModule | auto-nesting kernel + USB dongle check (§8.2). Depends on MFC42/MSVCRT/MSVCP60 (not shipped), `Dxf2Grp.dll`, `CircleFitDLL.dll`, dynamically `smartnest.dll`, `hid.dll`, `SetupApi.dll` |
| `SmartNest.dll` | MSVC 6, **2002-10-21**, imports only KERNEL32/USER32; self-identifies as `Nestlib Development Version`, `VERSION : 3.0, DATE : 30 JUN 95`, `This version does not support multiple torch nesting.` (verifier) | AutoNest (dynamic) | **226 exports** (the analyst's 452 double-counted the two export tables — verifier correction): a C nesting library API (`NOpenNestLib`, `NAddPartInfo`, `NAddSheetInfo`, `NDwgAdd{Arc,Circle,Line}`, `NNestOrder`, `NInqGuillotineCut*`, `NOffset*`, `NPartCheckMinimumDistance`, …) with C++ helper classes (`partGeomLib`, `partToBeNested`, `optStruct`, `CmapDim`). *INFERENCE (medium):* a licensed third-party rectangular/true-shape nesting engine; AutoNest is the vendor's wrapper that adds the dongle check and the `Nest_*` API. |
| `Dxf2Grp.dll` (7.5 MB) | MSVC 6, 2015-09-21 | AutoNest | DXF → part groups; contains ODA/Teigha types (`OdDbObjectId`, `OdGePoint3d`, `OdDb::SaveType`, `DwgVersion`) → statically linked **Open Design Alliance DWG/DXF library**; classes `CDrawing`, `CPart2`, `CArc2/CCircle2/CLine2/CElement2/COuter2`, layer helpers `IsQgLayer/IsHxLayer/IsAuxLayer`. Not imported by MainApp/CADModule directly. |
| `DxfParseDllvc100.dll` | VS2010, 2024-01-13 | CADModule (`newDxfFileParse`) | the DXF importer actually used by the CAD module (in-house, `DxfParse::*` classes) |
| `splineAnalyerVc100.dll` | VS2010, 2020-03-17 | CADModule (`newSpline2DAnalyer`), MotionCtrl | NURBS/spline discretisation |
| `CircleFitDLL.dll` | MSVC 6, 2015-03-05 | Dxf2Grp, AutoNest (`?Rmax_CircleFit@@3NA` global) | least-squares circle fit (`CCircleFit::mainFit`, globals `Ang/Err/Len/Rmax_CircleFit`, `fitOutSequence`) |
| `MotionCtrl.dll` | VS2010, 2021-07-29, PDB `\MotionCtrl_dll_V1.3.22\Release\MotionCtrl.pdb`; imports `splineAnalyerVc100!newSpline2DAnalyer`; carries RTTI `CContoutSmooth`/`IContourSmooth` (same class names as CADModule — verifier) | **no static importer found** (not in MainApp, CADModule or NCModule import tables; not in the Wine module list) | exports `arcInterp`, `segInterp`, `segInterp_time`, `newContourSmooth`, `newVelocityPlanning`. CADModule has its own `CContoutSmooth`/`CVelocityPlanning` classes with the same interface names (`IContourSmooth`, `IVelocityPlanning`). *INFERENCE (medium):* MotionCtrl.dll is an older/alternate build of the interpolation code that is either dynamically loaded by CADModule under some setting or is a stale file. |
| `PHBX.dll` | MSVC 6, 2025-06-05; imports HID (6), SETUPAPI (4, ANSI), ADVAPI32 registry (6), COMCTL32, and **WINSPOOL.DRV** (`OpenPrinterA/DocumentPropertiesA/ClosePrinter` — MFC-static leftovers, verifier) | MainApp (dynamic) | wireless pendant (§8.4) |
| GDI+ | system | MainApp | thumbnails |
| Qt 5 + Enigma Virtual Box | | Report/report.exe | §11 |

---

## 11. `Report/report.exe` and the report data

* **EVIDENCE:** 45,789,184 bytes; PE32, linker 14.29 (VS2019), subsystem 6.0, 7 sections: `.text/.rdata/.data/.rsrc/.reloc` (tiny, ≈100 KB of stub code) plus **`.enigma1`** (raw size 0x2b46000 ≈ 45 MB, entropy 6.6) and **`.enigma2`** (0x4c000). TLS directory present (callbacks at 0x41c044) and `ntdll!ZwProtectVirtualMemory`, `RtlDosPathNameToNtPathName_U` imports — the Enigma Virtual Box loader. The embedded payload is a **Qt 5 application**: strings `Qt5Core.dll`, `Qt5Gui.dll`, `Qt5Widgets.dll`, `Qt5PrintSupport.dll`, `Qt5Svg.dll`, `qwindows.dll`, `qwindowsvistastyle.dll`, image plugins (`qjpeg`, `qgif`, `qico`, `qsvg`, `qtiff`, `qwebp`, …), `libEGL`, `opengl32sw`, `D3DCompiler`, `QMainWindow`, Qt "About Qt" texts, and PDF/XMP producer strings (`QPdf`). Digitally-signed Microsoft CRT redistributables are also embedded (certificate URLs).
* ~~The app-specific code is inside the compressed container, so its own strings are not visible~~ **Verifier correction (EVIDENCE):** the application's own code is the *visible* stub — `.text` is 0xa492 bytes and `.rdata` (file 0xaa00–0x16200) contains its GBK strings and Qt import names; only the Qt runtime (`Qt5Core/Gui/Widgets/PrintSupport/Svg.dll`, `qwindows.dll`, `qwindowsvistastyle.dll`, `qjpeg/qsvg`, `libEGL`, `opengl32sw`, `D3DCompiler_47`) is inside the Enigma container. Qt build string: `Qt 5.15.2 (i386-little_endian-ilp32 shared (dynamic) release build; by MSVC 2019)`. Stub strings (GBK, file offsets): `统计/计费` [statistics/billing] 0xb998, `./icon.ico`, `/report.txt` 0xc22c, `/lang.txt` 0xc384, `lang==0` 0xc3b8, **`加工统计` [processing statistics] 0xc3c0 = the main-window title that MainApp's `FindWindowW` (0x491c41, `push 0x7dd92c`) looks for**, table header `选择,加工文件名,幅面尺寸,日期,切割总长,空移总长,穿孔个数,预估加工时长,预估切割时间,预估空移时间,预估穿孔时间` [select, job file name, sheet size, date, total cut length, total rapid length, pierce count, est. processing time, est. cutting time, est. rapid time, est. piercing time] 0xc418, `确定删除选中数据？` [delete selected data?], `请选择打印目标` [choose print target], `打印预览` [print preview], `加工报告单` / `Processing Report` 0xc6c0/0xc6cc, `读取图片文件失败` [failed to read image file], Qt slots `on_tableView_doubleClicked`, `on_tableChange`, `slot_previewPdf(QPrinter*)`, `produceReport`, classes `QMainWindow`, `QTableView`, `QStandardItemModel`, `QPrintPreviewDialog`, `QPdfWriter`, `QSharedMemory` (single-instance guard), fonts `微软雅黑`/`宋体`. **INFERENCE (high):** a stand-alone job-report viewer/printer (table of `report.txt`/`TotalReport.txt` rows + thumbnail `.jpg`, print preview / PDF); the Chinese column names are hard-coded, `lang==1` presumably switches to English captions.
* Launch (EVIDENCE, 0x491700–0x491c60): writes `<dir>\Report\lang.txt` containing `lang==0` or `lang==1` (0x7dd8e8/0x7dd8f0; the file present contains `lang==0`), then `ShellExecuteW(NULL, L"open", L"<dir>\Report\report.exe", …)` (0x4917fb), and uses `FindWindowW` (0x491c41) to avoid launching twice. Ribbon entries `mf252` 加工报告[最新] and `mf253` 加工报告[全部] [Work Report New/All].
* Data files (EVIDENCE, UTF-8 with BOM):
  * `Report/report.txt` — the "latest" report, CSV: `222.chf,15.45x34.34 mm,2025-03-07 16:04:41,3.57 m,1.05 m,0,1分26秒,62.28 Sec,11.32 Sec,0.00 Sec` → file, size, time, cut length, rapid length, pierce count, estimated processing time, estimated cutting time, estimated rapid time, **estimated piercing time** (EVIDENCE: report.exe's own column header at stub offset 0xc418, see above; the analyst's "other time" is corrected).
  * `Report/TotalReport.txt` — 2,950 lines, `未命名-1,20.84×20.84mm,0.07m,0.05m,0,0分01秒,2024-07-25 14:48:15` (name, size, cut length, rapid length, pierces, duration, timestamp). `Report/LogReport.txt` — same with a leading log timestamp. `File/ProcessesStatistic.txt` — one similar line.
  * `Report/*.chf.jpg` (111, 222, rpt) — job thumbnails rendered by MainApp via GDI+; `Report/广告.jpg` [advertisement.jpg] and `Report/1111.jpg` (identical size 59,558 bytes), `未命名-1.jpg`, `Untitled-1.jpg`, `icon.ico` — images used by the report layout.

---

## 12. `Dump/`, `Module/`, `Update/` and miscellaneous files

### 12.1 Crash dump (EVIDENCE — custom minidump parser)

`SRC/Dump/20260911-175115.dmp` (28,206 bytes): MDMP version 0xa793, 8 streams (SystemInfo, ThreadList (3 threads), stream type 24, ModuleList (58 modules), vendor stream 0xfff0, MemoryList, MiscInfo, Exception), timestamp 2026-09-11 15:51:16 UTC (re-parsed by the verifier; the "4 CPUs" figure could not be confirmed — the field read was `ProcessorLevel`=6 — and is dropped). Exception `0xC0000005` (access violation) in thread 36 at **VA 0x4b24e6** with context **EAX = 0, ECX = 0, EDX = 0x963e28** — the NULL dereference is confirmed by the register dump. `Log/2026-09-11.log` contains only the NCModule banner `NC Start`, i.e. NCModule initialised before the crash. Module list contains `Z:\home\karstein\Documents\CF1390-250715-1084-0973\Mlaser-v0.0.0.52\MainApp.exe`, Wine builtins (`winex11.drv`, `ucrtbase`, `zlib1.dll`, `jsproxy.dll`), the package's `BCGCBPRO2210u100.dll`, `mfc100u`, `MSVCR100`, `MSVCP100`, `DxfParseDllvc100.dll`, `splineAnalyerVc100.dll`, `CircleFitDLL.dll`, `MSVCP60.dll` (Wine builtin), `HID.DLL`, `SETUPAPI.dll`, `SensApi.dll`, `WINHTTP.dll`, `ws2_32.dll`, and **`Module/ControlModule.dll`, `LangModule.dll`, `LogModule.dll`, `NCModule.dll`, `ParaModule.dll` — but NOT `CADModule.dll`**, and none of `AutoNest.dll`, `Dxf2Grp.dll`, `MFC42.DLL`, `MSVCRT.dll`, `PHBX.dll`, `MotionCtrl.dll`.

Crash site (EVIDENCE): 0x4b24c3 `call 0x5ff1b0` (singleton) … 0x4b24d4 `mov ecx,[eax+0x2f0]`, 0x4b24e0 `mov eax,[edx+0x2f0]`, **0x4b24e6 `mov edx,[eax]`** (NULL vtable fetch), 0x4b24ee `call [edx+0x2b0]` (virtual slot 172). The function (0x4afe10, called from 0x4af4cf) had just read `\File\PithCompensate.pcf`.
**INFERENCE (high, strengthened by the verifier):** `CADModule.dll` failed to load under Wine because its import `AutoNest.dll` needs `MFC42.DLL` (absent — `find SRC -iname 'mfc42*'` returns nothing; only `msvcrt`/`MSVCP60` are Wine builtins and both *are* in the module list), so the `"CAD"` module pointer at singleton +0x2f0 (§4.3) stayed NULL and the first CAD call dereferenced it. Supporting detail: CADModule's *other* dependencies `DxfParseDllvc100.dll`, `splineAnalyerVc100.dll` and AutoNest's `CircleFitDLL.dll`/`MSVCP60.dll` **are** in the loaded-module list, which is exactly what a loader that resolved CADModule's import tree up to the missing `MFC42.DLL` would leave behind. Note that MainApp has a "component missing" message (§4.3) but the crash shows the missing-module path did not abort start-up. Providing a genuine `mfc42.dll` (+ `msvcp60.dll`, `msvcrt.dll` are Wine builtins) in the prefix is the prerequisite for getting past this point; the dongle question (§8.2) only arises afterwards and only for nesting.

### 12.2 `Module/` and `Update/`

* `Module/`: the six plugins (§3.4). Build order: CADModule 2025-06-14 11:34 (different PDB root/author), LangModule 06-24 04:18:57, ControlModule 04:18:59, NCModule 04:19:15, ParaModule 04:19:16, LogModule 04:20:01, MainApp 04:20:03 — one release build of the `NexCut_X1_Http` solution.
* `Update/MCC100_V201.52.mcf` (120,456 bytes): motion-card firmware image whose file name is matched by MainApp with `\Update\MCC100_V` + `.mcf` (0x565e2e/0x565e5f) inside the version check (`ipAdd.ini MinHardwareVer=20152` — *INFERENCE medium:* encodes V201.52; `mf232`, `mf176`). `\Update\E310_V80*.afb` would be the auto-focus head firmware (`mf701`, `AFMinHardwareVer=133`). The update *transport* is in NCModule (out of scope).

### 12.3 Other root-level files (EVIDENCE)

* `ClearFile.bat` (GBK): deletes `*.pdb *.ilk *.exp *.log *.xml` and `%Module\*.lib` — a developer clean-up script accidentally shipped; note it would delete the parameter XMLs.
* `JumpAddTime.txt`: `[Jump] AddTime=200`, `[Axis4Freq] Is4Freq=0`, `[LimitSamllCircleVel] IsLimit=0 SlowRatio=3`, `[Arc2SegVelK] K_X=100 K_Y=100` — motion tuning knobs read as an INI.
* `LanFormatEStr.txt`: `[SC] Var1..Var15` mapping to lang keys (`pd719_4`, `pd113-1`, `pd117`, `EtherAxisInfos_23`, `AFEtherCATIndex`) — a list of language entries known to have illegal format ("Lang Format Error" whitelist; *INFERENCE medium*).
* Debug dumps from 2025-07-18 in the root: `segments.txt`, `closePwmPosRatios.txt`, `linkFlyLine_pathGlys.txt`, `before/after_mergeLinearGly.txt`, `after_smoothGly.txt`, `setDataWithoutReFit_segs.txt`, `after_setDataWithoutReFit.txt`, `calcGraphCtInterpPt.txt`, `p_micoLinkLenPos.txt` — names of internal path-planning stages (merge linear glyphs → smooth → fly-line linking → PWM close-position ratios → segment list) written by CADModule debugging code.
* `File/IPSet.exe` (VS2010, 2016-12-08, PDB `c:\users\zzy\documents\visual studio 2010\Projects\IPSet\Release\IPSet.pdb`, class `CIPHelper`; enumerates adapters with `GetAdaptersInfo` and runs `netsh interface ip set address name="…" source=static addr=10.1.1.<n> mask=255.255.255.0 gateway=10.1.1.1` — verifier), `File/pingMC.bat`, `File/pingZF.bat`, `File/SCFile.ico`, `File/logo_1.bmp`, `File/PM/` (empty), `File/Temp/` (autosave scratch), `Graph/Work1|Work2/{1,2,3}.chf` (2020 sample jobs), `dat/tmpnst1/` (nesting temp dir, cf. error codes 2/4/5), `res/splash.bmp` (2 MB), `Help/` (empty), `Log/*.log` (NCModule daily logs), `Log/VelDecc.txt` (empty).

---

## 13. Assessment: thin wrappers vs. in-house logic

| Subsystem | Location | Verdict | Basis |
|---|---|---|---|
| Ribbon/docking/property-grid UI | MainApp + BCGCBPRO | **Thin wrapper** over BCGControlBar Pro; in-house only the ~130 dialog/panel classes and 9 property types | 1549 BCGP imports, 0-control dialog resources |
| Canvas | MainApp (`COpenGLView`, `CGlFont3D`, `CWglFontBitmap`) | In-house but **trivial**: GL 1.1 immediate mode, `glOrtho` 2-D, bitmap fonts | import list §3.2 |
| Plugin framework | MainApp (`CLeanModuleManager`) + `IModule/IModuleProvider` | In-house, simple | §4.3 |
| Parameter store | ParaModule (`CXMLParaEngine`) | In-house XML attribute store; format fully visible in `File/*.xml` | §6.2 |
| Localisation | LangModule (`CTxtFile`) | In-house key#zh#en text file | §6.3 |
| Logging | LogModule (YaoUtil) | In-house, with TCP upload | §3.4 |
| Geometry/CAD, path planning, lead-ins, micro-joints, velocity planning | CADModule (+ splineAnalyer, DxfParseDll) | **In-house, the largest body of proprietary logic** (193 classes) | §4.4 |
| DXF/PLT/G-code import | CADModule (`DxfParse`, `PltParse`, `CGCodeFileParse`) | In-house parsers; `Dxf2Grp` (ODA-based) only reached through AutoNest | §10 |
| Nesting | AutoNest → SmartNest | **Third-party engine** (SmartNest 2002) wrapped by vendor DLL with dongle check | §8.2 |
| Device protocols (MCC100 Modbus/UDP, height follower, laser sources IPG/Raycus/FTC61, EtherCAT status, remote monitor) | NCModule | In-house | §3.4 |
| Licence (card) | NCModule `CDog` + MainApp UI | In-house | §8.1 |
| Dongle | AutoNest (SM2 token SDK) | Third-party SDK statically linked into AutoNest | §8.2 |
| Pendant / joystick | PHBX.dll (vendor SDK) / MainApp `CHidUsb` | thin HID readers | §8.3–8.4 |
| HTTP client | MainApp (`CHttpClient`, WinHTTP) | thin wrapper, ~4 functions | §9.1 |
| Reports | report.exe (Qt) + text files | separate tool; data format trivial CSV | §11 |
| Crash handling | MainApp (`MiniDumpWriteDump`, CRT filter patch) | boilerplate | §7 |

---

## 14. Open questions

1. **Does the MCC100 card enforce its licence itself** (refuse motion when `dogState_*` ≠ normal), or is enforcement purely in the PC software (`CDog` in NCModule)? Needs the NCModule/firmware analysis; the `Log/Code.txt` history ("verify data area failed!" on 2024-06-19) suggests the card's data area was once invalid on this machine.
2. ~~Exact meaning of the string pushed before `WinHttpOpen` at 0x406129 (`语言包`)~~ **Resolved by the verifier:** `语言包` is the LangModule's registered module name (§4.3); the NexCut host is the device selected in the login dialog's grid and the port is the constant 8080 (§9.1). Still open: which grid/list feeds the host (probably a device-discovery list) and the JSON schema of the login reply (`"state"`, `"message"`, token for `Authorization:`).
3. ~~The `[Soft]` key whose default is `http://www.au3tech.cn/key/`~~ The URL is a hard-coded constant stored at settings+0x30c (§9.2); what consumes that field (an online activation/"key" service of the licence vendor "au3tech"?) is still unknown.
3a. (new) What does the NC-module call with argument 9 at 0x566eab do when `CheckUserID` mismatches the card's user code (§8.1)? Is 109 a per-OEM/vendor number, and is the card's stored user code readable through the `识别码数据` [ID-code data] register group?
3b. (new) The licence recorded in `Log/Code.txt` is 180 days from 2025-06-17. Whether the machine is currently in `dogState_TrialOver` (and what that blocks) should be checked at run time.
4. Whether `MotionCtrl.dll` is loaded at all in this build (no importer found; not present in the Wine module list at crash time, but the crash happened before CAD init).
5. Full payload schema of the remote monitor JSON and the `logID/machineID/logData` upload (NCModule).
6. What `Nest_GetId`/`Nest_EncodeByKey`/`Nest_EncodeNew`/`Nest_SetCalNew` return to CADModule — whether the dongle also encrypts saved nesting projects (`FileTitle_SaveNestProjFile`), which would matter for reading existing nest files.
7. The joystick HID report layout (bytes 0xbc/0x55 headers, fields at `+0x4030…`) — only relevant if the Linux port wants to support the same hand-wheel.
8. Meaning of the two "vendor" minidump streams (types 24 and 0xfff0) — probably BCGP/MFC state; harmless.

---

## 15. Implications for the Linux port

**What must be replicated (proprietary, no library equivalent):**
* The **parameter model**: 942 element.attribute keys in `File/*.xml` (§6.2) with their semantics (documented by the `pd*` language entries — `Lang/lang.txt` is effectively the parameter documentation and should be parsed by the port).
* The **`.chf` job format**, autosave/breakpoint files (`autosave.chf`, `AutosaveParam*.ini`, `tempIsBreak.ini`, `ManuContour.dat` — all share the `scFlie … eof` container), and the report CSV formats (§11).
* The **CAD/process pipeline** (CADModule §4.4): contour extraction, lead-in/out, micro-joints (`MicoLink`), cooling points, bridges, share-edge, over-cut, fly-cut (`CSCanFlyCutDlg`, `linkFlyLine`), start-point uniformisation, sorting, contour smoothing and velocity planning, PWM position ratios. This is the bulk of the reimplementation effort and is only reachable through further analysis of `CADModule.dll` (and `MotionCtrl.dll`, whose exports name the interpolation primitives).
* The **device protocols** in NCModule (MCC100 Modbus/UDP at 10.1.1.168:502 with the retry/timeout parameters in `ipAdd.ini`, plus ZF/AF/EC/laser links) and the firmware-update transport for `.mcf`/`.afb`.
* The **card licence handshake** only if the card refuses to run without it (Open question 1). Note that the licence data lives in MCC100 register groups (`管理员加密数据`, `用户加密数据`, `时间日期`, `识别码数据`, `加密数据域访问开关`, §6.3a) and that the last activation on this machine was a 180-day code from 2025-06-17 (§8.1) — a port that talks to the card directly must at least be able to *read* the licence state to reproduce the vendor's behaviour.
* The ≈100 hard-coded Chinese diagnostics of §6.3a (nesting error table, breakpoint-resume messages, OpenGL errors, test-report dialog) have no `lang.txt` entry and need their own translations.

**What can be replaced by existing Linux/open-source components:**
* UI (BCGControlBar ribbon/docking/grids) → Qt Widgets/QML or GTK; there is nothing in the resource section worth reusing except icons/bitmaps.
* OpenGL 1.1 immediate-mode canvas → any 2-D canvas (Qt Graphics View, Cairo, or modern GL); the port need not reproduce the GL code.
* DXF import → `libdxfrw`/`ezdxf`-class libraries (the original's `DxfParse` supports LINE, ARC, CIRCLE, ELLIPSE, LWPOLYLINE, POINT, SPLINE/NURBS, TEXT, MTEXT); PLT/HPGL → `hp2xx`-style parsers; splines → any NURBS tessellator.
* Nesting → open-source nesting (e.g. `libnest2d`, `SVGnest`-style, or `Deepnest`) instead of AutoNest/SmartNest — this also removes the USB dongle dependency entirely, since the dongle gates nothing else.
* HTTP client → libcurl; JSON → any library (the NexCut server features are optional).
* Remote monitor/log upload → simply omit (privacy) or make opt-in; MainApp treats failure as informational.
* Localisation → reuse `Lang/*.txt` directly (UTF-16, `ID#zh#en`); gettext is unnecessary.
* Pendant/joystick → Linux `hidraw`/evdev; PHBX's protocol would need capture, but a standard gamepad can replace `CHidUsb`.
* Reports → any CSV/HTML/PDF generator; report.exe (Qt/Enigma) is a separate viewer that reads text files.
* Crash dumps / single-instance / registry association → standard Linux equivalents.

**Running the original under Wine (for reference captures):** provide `mfc42.dll` in the prefix (needed by `AutoNest.dll`/`Dxf2Grp.dll` → `CADModule.dll`), grant hidraw access to `3689:8762` (nesting dongle), the pendant receiver and any hand-wheel, run with a network namespace/route to 10.1.1.0/24 for the controller, and expect the `requireAdministrator` manifest and the `explorer.exe`/`ShellExecuteW` helpers to be harmless. The remote-monitor host (47.104.17.21:9001) and the NexCut HTTP server should be blocked or pointed at localhost (`MonitorIP=127.0.0.1` is the built-in default when the ini key is absent).

---

## Verification notes (adversarial re-check, 2026-09-12)

Method: every command was re-run from scratch against `SRC` (objdump -p/-h/-d, `strings -a`/`-e l -t x`, own PE/resource/minidump parsers, a disassembly annotator that resolves immediates to `.rdata` strings and IAT slot names, direct UTF-16/GBK scans of the binaries). Nothing under `SRC` was modified.

**Confirmed as stated (18 key facts re-checked):** PE header/sections/entropies/RSDS/manifest (§2) — exact match; import counts per DLL (§3.1) — exact; WS2_32 absent from MainApp, 18/10 ordinals in NCModule/LogModule resolved to the listed names (§3.4); plugin loader 0x4b62e3/0x4b649e/0x4b64c5 and single export `newModuleProvider` in all six modules (§4.3); dongle VID/PID only in AutoNest (`HID#Vid_3689&Pid_8762` @0x890a0, `cmp cx,0x8762`/`cmp ax,0x3689`/`cmp cx,0x2020` @0x1006e72c–0x1006e744 after `HidD_GetAttributes`; the 17 `0x8762…` hits in MainApp's disassembly are `.data` addresses `push 0x8762f8` etc., not immediates) (§8.2); CADModule's 18 AutoNest imports; `排样内核错误代码.txt` (UTF-8, not GBK) error 1 = 没找到加密狗; `mf496` used only for nesting error 1 (0x49601e); NCModule `CDog` strings; `CHidUsb` (SetupDi flags 0x12, `v/V i/I d/D _` byte compares at 0x45234f–0x4523d6, `HidP_GetCaps`==0x110000 at 0x4528bc, 3000 ms overlapped read), joystick IDs 1000/2016 vs 6125/2012 selected by +0x4028, report parser `cmp ecx,0xbc`/`cmp edx,0x55` at 0x59a0e0/0x59a0ef (§8.3); WinHTTP strings and call sites (§9.1); NCModule monitor/HTTP/base64 strings and `C:\OrionLib\boost 1.5.3` (§9.2); report.exe sections/linker 14.29/TLS/ntdll imports (§11); minidump module list (58 modules, CADModule/AutoNest/Dxf2Grp/MFC42/PHBX/MotionCtrl absent) and crash site `mov edx,[eax]` with EAX=0 (§12.1); resources 408 BITMAP / 71 PNG / 7 empty DIALOGEX / 2 MENU / 56 strings / version placeholders (§5); RTTI counts 246/193/40/35/26/15 and 132 for NCModule (§4); PHBX dynamic loading sites and its HID/SETUPAPI imports (§8.4); start-up process names, mutexes `SC3000`/`HighSea_SC1000`, `_XcptFilter` patch, `.chf` registry association strings, softPara.ini keys, `\Update\MCC100_V` + `%.2f` + `.mcf` (§7); build timestamps of every DLL (§10).

**Refuted / corrected:**
1. "MainApp `.rdata` contains 0 CJK strings" — false; `strings -e l` cannot see non-ASCII UTF-16. ≈110 distinct Chinese literals exist (§6.3a), including the module names, the nesting error table, breakpoint-resume diagnostics and 15 MCC100 register-group names.
2. `语言包` before `WinHttpOpen` is the LangModule's registered name, not an HTTP host/port parameter (§4.3, §9.1); the NexCut port is hard-coded 8080 (§9.1).
3. `MonitorIP/MonitorPort` are read from `[IP]`, not `[Soft]`; the au3tech URL is a constant CString assignment, not an INI default (§6.1, §9.2).
4. Parameter-key cross-check: 969 keys (not 839); all 942 XML pairs are referenced (not 812); `A0..A3`, `HPA3`, `MAC_1..4` *are* referenced; prefix counts replaced (§6.2).
5. `Log/Code.txt` is a full activation history with `Hid`/`LincensDay`/`UserCode` values, not just the repeated "verify data area failed!" line; `CheckUserID=109` is compared in code against a value from the NC module and 109 is also hard-coded (§8.1).
6. report.exe's own code/strings are in the visible stub (Qt 5.15.2, MSVC 2019); the container holds the Qt runtime; `report.txt`'s last column is estimated piercing time (§11).
7. SmartNest exports 226, not 452 (§10). `lang.txt` prefix "ls 20" → ls 14 / lp 20; `ab1..9`/`ls*` keys are unused by this build (§6.3).
8. Lang-key reference counts 2,096/2,088 not reproducible; verifier: 2,578 exact key literals, 12 absent (§5).
9. `CheckUserID` is read in function 0x566da0, not in 0x566ee0 (§7).
10. Minidump "4 CPUs" not supported by the stream layout — dropped (§12.1).

**Downgraded:** the `CheckUserID` ↔ `UserCode` identification (§8.1) and the meaning of the au3tech URL (§9.2) are now low-confidence inferences.

**Added:** module registry names and the `"CAD"` → +0x2f0 mapping; the module-missing/cycle messages; the full inventory of INI reads/writes (`CardIP`, `MonitorReconn*`, `CustomInformation` `Custom`/`RunModel`, `JumpAddTime.txt` keys); the MCC100 register-group name table; report.exe's column semantics and window title `加工统计` = the `FindWindowW` target; AutoNest's 26 exports and run-time `LoadLibraryA` of hid/SetupApi; SmartNest = "Nestlib 3.0 (1995)"; IPSet.exe = `netsh` wrapper; PHBX imports WINSPOOL; the 180-day licence record and card-clock resets in `Code.txt`; `Lang/Readme.txt`/`语言修改记录.txt` contents.

**Still uncertain:** who reads settings+0x30c (au3tech URL); the semantics of the NC-module call with argument 9; whether the card enforces the licence; what the login grid's host list is; PHBX/hand-wheel HID report layouts; whether `MotionCtrl.dll` is ever loaded.
