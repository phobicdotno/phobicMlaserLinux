# 01 — Hardware / machine configuration model (Mlaser-v0.0.0.52, CF1390)

Analyst scope: `File/BkHardPara.xml`, `File/BkManuPara.xml`, `File/SecondBkManuPara.xml`, `File/softPara.ini`, `File/ipAdd.ini`, `File/PithCompensate.pcf`, `File/ManuContour.dat`, `File/AutosaveParam1.ini`, `File/AutosaveParam2.ini`, `File/Temp/*.ini`, `File/Temp/ManuContour.dat`, `File/ProcessesStatistic.txt`, `JumpAddTime.txt`.

Package root (read-only): `/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52` (referred to as `SRC` below).

Conventions used in this document:

* **EVIDENCE** = something directly observed in a file, a string table, or the disassembly (with the location).
* **INFERENCE** = my interpretation; every inference carries a confidence tag: `[confirmed]` (verified in code), `[likely]` (strong circumstantial evidence), `[guess]` (plausible, unverified).
* Chinese strings are quoted verbatim with an English rendering in parentheses. Where `Lang/lang.txt` already provides an English translation it is quoted; where that translation is wrong or misleading I say so.

---

## 0. Method and how the parameter model is wired inside MainApp.exe

### 0.1 Files and encodings (EVIDENCE)

| File | Size | Encoding / format | Notes |
|---|---|---|---|
| `File/BkHardPara.xml` | 10 219 B | ASCII, CRLF, no XML prolog | root `<ParameterRoot>`; 14 group elements; every parameter is an XML *attribute* on a leaf element |
| `File/BkManuPara.xml` | 8 243 B | ASCII, CRLF | same layout; 6 group elements |
| `File/SecondBkManuPara.xml` | 8 243 B | ASCII, CRLF | same schema as BkManuPara, differs in 3 elements only (see §5) |
| `File/softPara.ini` | 71 B | ASCII INI | `[SC2000] NormalExit/XAxis/YAxis/ZAxis/WAxis` |
| `File/ipAdd.ini` | 2 301 B | ASCII INI | `[IP]` and `[Soft]` sections |
| `File/PithCompensate.pcf` | 16 B | "scFlie" text container | `scFlie\r\n0\r\neof\r\n` |
| `File/ManuContour.dat` | 103 B | "scFlie" text container | count 24 followed by 0..23 |
| `File/AutosaveParam1.ini`, `2.ini` | 41 B each | "scFlie" text container (not a real INI) | 5 integers |
| `File/Temp/AutosaveParam1.ini`, `2.ini`, `ManuContour.dat`, `tempIsBreak.ini`, `tempGraph.chf` | 40/40/22/19/500 B | "scFlie" containers | snapshot of an earlier job (Apr 23 2025) |
| `File/ProcessesStatistic.txt` | 53 B | GBK text | one CSV job-statistics line |
| `JumpAddTime.txt` (package root) | 114 B | ASCII INI | four sections of tuning knobs |
| `Lang/lang.txt` | 289 746 B | UTF-16LE with BOM | `ID#中文#English` lines; 3 485 lines |

The "scFlie" container (sic — the developers misspelled "scFile") is a trivial line-oriented text format used by MainApp for all its small state files: first line `scFlie`, then one value per line, last line `eof`, CRLF line endings. EVIDENCE: MainApp.exe writer at `0x4047d0` pushes the ASCII literal `"scFlie"` (VA `0x7c4744`) after `fopen(...,"wb")`, and the closer at `0x404800` writes `"eof"` (VA `0x7c474c`); integer lines are produced by `0x404990` via `fprintf`-style formatting (`sprintf_s(buf,0x1387,fmt,int)`, format at `0x7c4770`/`0x7c4778`).

### 0.2 How MainApp maps XML attributes to UI labels, units, defaults and ranges (EVIDENCE)

MainApp does **not** read the XML with hard-coded field code; it has a static *parameter descriptor table* built at start-up (initialiser functions around `0x785000–0x7a2000`). Each descriptor is a 0xf4-byte record holding, in order: a group index, the XML *section* name (`"AxisParam"`, `"HomeParam"`, `"ZFParam"`, `"LaserParam"`, `"ManuParam"`, `"GasParam"`, `"DOParam"`, `"DIParam"`, `"DAParam"`, `"FCParam"`, `"SoftParam"`, `"MachineAxisConfig"`, `"MachineAxisConfig_0..4"`, `"AFParam"`, `"ECParam"`, `"GraphParam"`, `"NestParam"`, `"ImportGraphParam"`, `"LayerParam"`, `"CO2LayerParam"`, `"LaserTest"`, `"StressTest"`), the XML path `Elem.Attr` (e.g. `"SOP.RemoteType"`), the `lang.txt` label id (e.g. `"pd292"`), a pointer to the live value inside the global settings object (returned by `0x5ff1b0`), a type code, a default-value string, a unit string, min/max doubles and, for enumerations, a pointer to a table of option label ids (28-byte stride) and their count.

Example (disassembly `0x785875–0x7858d4`): `push "SoftParam"`, `push "SOP.RemoteType"`, `push "pd292"`, value ptr = `g+0x4028`, type `4`, default `"0"`, min `0`, max `1`, options table `0xa490e8` × 3 (`pd178`,`pd179`,`pd180`).

I parsed 1 001 descriptors out of the disassembly (`objdump -d -M intel MainApp.exe`) and joined them with the two XML files and `Lang/lang.txt`. That join is the basis of the inventory tables in §1. **Every XML attribute present in this machine's `BkHardPara.xml` and `BkManuPara.xml` has a descriptor in the executable (777 keys checked, 0 unknown)**; the descriptors that have no counterpart in these two files belong to `BkLayerPara.xml` (`GP.*` layer/process parameters, out of scope here), to `GRP.Zoom*`, or to test dialogs (`LT.*`, `ST.*`).

Type codes observed (INFERENCE `[likely]`, from the value ranges and the widgets they drive): `1`=int, `2`=double, `3`=string, `4`=enumeration (combo), `5`=bool (byte), `6`=DO port number (0 = none, max 26), `7`=DI port number (0 = none, max 28), `8`=IPv4 packed int, `9`=pulse-equivalent (double, unit "p / mm"), `10`=DA channel (0 none,1 DA1,2 DA2), `11`=DA channel for proportional valves, `12`=extended-card DO (max 16), `13`=COM port index (0..16), `14`=COLORREF.

The `lang.txt` label convention: a label of the form `主条目.子条目` (Group.Item) as described in `Lang/Readme.txt` ("参数类的文本格式为 主条目.子条目" = "parameter-class text format is MainEntry.SubEntry"). `lang.txt` line format is `ID#简体中文#English`.

### 0.3 File-loading order (EVIDENCE, MainApp strings at `.rdata` lines 5400-5450 of the UTF-16 string dump)

`--- Init System param ---` → `\SystemPara.xml`, `\HardPara.xml`, `\ManuPara.xml`, `\LayerPara.xml` (legacy names) → `\File\` → `"The hardware parameter file was not found and was re-created."` → `\BkHardPara.xml`, `\BkLayerPara.xml`, `\BkManuPara.xml`, `\SecondBkManuPara.xml` → `"Hard Param File Read Failed"`, `"Read Backup"`, `"Manu Param File Read Failed"`, `\File\ErrorManuPara.xml`, `"Backup Manu Param File Read Failed, Read Second Backup File"`, `\File\ErrorBkManuPara.xml`, `"MainApp saveManuParam second backup read failed"` → `\File\PithCompensate.pcf` → `\File\PM\p0.pmf` → `\Technology\Fiber`, `\Technology\CO2`.

INFERENCE `[confirmed]`: the "Bk" prefix means *backup copy*: MainApp keeps the live parameters in memory, writes them to `BkHardPara.xml` / `BkManuPara.xml`, and keeps `SecondBkManuPara.xml` as a second-level backup of the machining parameters; on read failure it falls back to the backup and, failing that, to the second backup (and moves the broken file to `ErrorManuPara.xml` / `ErrorBkManuPara.xml`). `hp51`: "硬件配置参数保存失败，请重新保存！" (hardware configuration save failed, please save again). See §5.

---

## 1. Complete parameter inventory

Columns: *Value* is the value in this machine's file; *Label* is the `lang.txt` text for the descriptor's label id; *Type/Unit/Default/Min/Max/Enum* come from the descriptor table in MainApp.exe. An empty Label means the descriptor's label id has no `lang.txt` entry (e.g. `pd694/pd695/pd696` for `EC.ECStepLength/ECFastSpeed/ECJogIsStepMode`, or `SaveFileItem`). Enum option texts are the English `lang.txt` text with the Chinese in parentheses where the two differ in meaning.

> Caveat on `lang.txt` mistranslations that matter here: `pd168` is `负向` (**negative** direction) but its English text says "Positive"; `pd169` is `正向` (**positive**) but its English says "Negative". All *direction* enums below (`HomeDirection`, `GoOriginalDirection`, `ECAxisServoDir`) must be read from the Chinese: value **0 = 负向 negative, 1 = 正向 positive** for `A0/A1/HPA3.HomeDirection` and `MAC*.GoOriginalDirection`; for `EC.ECAxisServoDir` the option table is reversed (0 = `pd169` 正向 positive, 1 = `pd168` 负向 negative).

### 1.1 `File/BkHardPara.xml` (hardware parameters) — 446 attributes

| Section | Key (Elem.Attr) | Value | Label (zh) | Label (en) | Type | Unit | Default | Min | Max | Enum options |
|---|---|---|---|---|---|---|---|---|---|---|
| PAxisParam | `A0.EnableType` | `0` | X轴.可用性 | X Axis.Usability | enum |  | 0 | 0.0 | 2.0 | 0=Standard (普通轴); 1=Rotate (旋转轴); 2=Invalid (无效) |
| PAxisParam | `A0.AxisIndex` | `0` | X轴.轴序号 | X Axis.Axis Index | enum |  | 0 | 0.0 | 3.0 | 0=1; 1=2; 2=3; 3=4 |
| PAxisParam | `A0.DoubleDriver` | `0` | X轴.双边驱动 | X Axis.Double Drive | bool |  | 0 | 0.0 | 1.0 |  |
| PAxisParam | `A0.PulseEquivalent` | `1` | X轴.脉冲当量 | X Axis.Pulse Equivalent | pulse-eq | p / mm | 1 | 1.0 | 999999.0 |  |
| PAxisParam | `A0.MaxLength` | `1500` | X轴.最大行程 | X Axis.Max Length | double | mm | 1500 | 1.0 | 99999.0 |  |
| PAxisParam | `A0.LimitSwitchType` | `0` | X轴.限位开关逻辑 | X Axis.Limit Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PAxisParam | `A0.EncoderReverse` | `0` | X轴.编码器反向 | X Axis.Encode Reverse | bool |  | 0 | 0.0 | 1.0 |  |
| PAxisParam | `A1.EnableType` | `0` | Y1轴.可用性 | Y1 Axis.Usability | enum |  | 0 | 0.0 | 2.0 | 0=Standard (普通轴); 1=Rotate (旋转轴); 2=Invalid (无效) |
| PAxisParam | `A1.AxisIndex` | `1` | Y1轴.轴序号 | Y1 Axis.Axis Index | enum |  | 1 | 0.0 | 3.0 | 0=1; 1=2; 2=3; 3=4 |
| PAxisParam | `A1.DoubleDriver` | `1` | Y1轴.双边驱动 | Y1 Axis.Double Drive | bool |  | 1 | 0.0 | 1.0 |  |
| PAxisParam | `A1.PulseEquivalent` | `1` | Y1轴.脉冲当量 | Y1 Axis.Pulse Equivalent | pulse-eq | p / mm | 1 | 1.0 | 999999.0 |  |
| PAxisParam | `A1.MaxLength` | `3000` | Y1轴.最大行程 | Y1 Axis.Max Length | double | mm | 3000 | 1.0 | 99999.0 |  |
| PAxisParam | `A1.LimitSwitchType` | `0` | Y1轴.限位开关逻辑 | Y1 Axis.Limit Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PAxisParam | `A1.EncoderReverse` | `0` | Y1轴.编码器反向 | Y1 Axis.Encode Reverse | bool |  | 0 | 0.0 | 1.0 |  |
| PAxisParam | `A2.EnableType` | `0` | Y2轴.可用性 | Y2 Axis.Usability | enum |  | 0 | 0.0 | 2.0 | 0=Standard (普通轴); 1=Rotate (旋转轴); 2=Invalid (无效) |
| PAxisParam | `A2.AxisIndex` | `2` | Y2轴.轴序号 | Y2 Axis.Axis Index | enum |  | 2 | 0.0 | 3.0 | 0=1; 1=2; 2=3; 3=4 |
| PAxisParam | `A2.DoubleDriver` | `1` | Y2轴.双边驱动 | Y2 Axis.Double Drive | bool |  | 1 | 0.0 | 1.0 |  |
| PAxisParam | `A2.PulseEquivalent` | `1` | Y2轴.脉冲当量 | Y2 Axis.Pulse Equivalent | pulse-eq | p / mm | 1 | 1.0 | 999999.0 |  |
| PAxisParam | `A2.MaxLength` | `3000` | Y2轴.最大行程 | Y2 Axis.Max Length | double | mm | 3000 | 1.0 | 99999.0 |  |
| PAxisParam | `A2.LimitSwitchType` | `0` | Y2轴.限位开关逻辑 | Y2 Axis.Limit Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PAxisParam | `A2.EncoderReverse` | `0` | Y2轴.编码器反向 | Y2 Axis.Encode Reverse | bool |  | 0 | 0.0 | 1.0 |  |
| PAxisParam | `A3.EnableType` | `2` | 第4轴.可用性 | 4th Axis.Usability | enum |  | 2 | 0.0 | 2.0 | 0=Standard (普通轴); 1=Rotate (旋转轴); 2=Invalid (无效) |
| PAxisParam | `A3.AxisIndex` | `3` | 第4轴.轴序号 | 4th Axis.Axis Index | enum |  | 3 | 0.0 | 3.0 | 0=1; 1=2; 2=3; 3=4 |
| PAxisParam | `A3.DoubleDriver` | `0` | 第4轴.双边驱动 | 4th Axis.Double Drive | bool |  | 0 | 0.0 | 1.0 |  |
| PAxisParam | `A3.PulseEquivalent` | `1` | 第4轴.脉冲当量 | 4th Axis.Pulse Equivalent | pulse-eq | p / mm | 1 | 1.0 | 999999.0 |  |
| PAxisParam | `A3.MaxLength` | `1500` | 第4轴.最大行程 | 4th Axis.Max Length | double | mm | 1500 | 1.0 | 99999.0 |  |
| PAxisParam | `A3.LimitSwitchType` | `0` | 第4轴.限位开关逻辑 | 4th Axis.Limit Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PAxisParam | `A3.EncoderReverse` | `0` | 第4轴.编码器反向 | 4th Axis.Encode Reverse | bool |  | 0 | 0.0 | 1.0 |  |
| PAxisParam | `AX.DoubleDriverAlarm` | `1` | 杂项.双驱误差报警 | Misc.Double Drive Error Alarm | bool |  | 1 | 0.0 | 1.0 |  |
| PAxisParam | `AX.DoubleDriverToleranceLength` | `3` | 杂项.双驱允差 | Misc.Double Drive Error Tolerance | double |  | 3.0 | 0.01 | 1000.0 |  |
| PAxisParam | `AX.DoubleDriverToleranceTime` | `100` | 杂项.双驱允差持续时间 | Misc.Double Drive Error Keep Time | int |  | 100 | 1.0 | 10000.0 |  |
| PAxisParam | `AX.Enable4Freq` | `1` | 杂项.编码器4倍频 | Misc.Encoder 4 multiplier freq | bool |  | 1 | 0.0 | 1.0 |  |
| PAxisParam | `AX.SafeStopFactor` | `3` | 杂项.安全减速系数 | Misc.Safe deceleration factor | int |  | 3 | 1.0 | 3.0 |  |
| PAxisParam | `AX.InterpolationCycle` | `250` | 杂项.插补周期 | Misc.Interpolation period | int | us | 1000 | 100.0 | 10000.0 |  |
| PAxisParam | `AX.InitializationDelay` | `0` | 杂项.总线初始化延时 | Misc.EtherCAT Initialization delay | int | ms | 0 | 0.0 | 15000.0 |  |
| PHomeParam | `HP.UseZPulse` | `0` | 回原点.使用Z相信号 | Go Origin.Use Z Phase Signal | bool |  | 0 | 0.0 | 1.0 |  |
| PHomeParam | `HP.SampleSignal` | `1` | 回原点.采样信号（重启有效） | Go Origin.Sample Signal Type | enum | ms | 1 | 0.0 | 99999.0 | 0=Origin (原点); 1=Limit (限位) |
| PHomeParam | `HP.LimitSwitchType` | `0` | 回原点.行程开关逻辑 | Go Origin.Limit Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PHomeParam | `HP.FastSpeed` | `50` | 回原点.粗定位速度 | Go Origin.Fast Speed | double | mm/s | 50 | 0.01 | 999999.0 |  |
| PHomeParam | `HP.SlowSpeed` | `10` | 回原点.精定位速度 | Go Origin.Slow Speed | double | mm/s | 10 | 0.01 | 999999.0 |  |
| PHomeParam | `HP.EnableSecondTimeHome` | `0` | 高级参数.启用二次回原 | Advanced Parameters.Enable Second Times Home | bool |  | 0 | 0.0 | 1.0 |  |
| PHomeParam | `A0.HomeDirection` | `0` | X轴.回原点方向 | X Axis.Go Origin Direction | enum |  | 0 | 0.0 | 1.0 | 0=Positive (负向); 1=Negative (正向) |
| PHomeParam | `A0.HomeOffset` | `10` | X轴.返回距离 | X Axis.Go Origin Offset | double | mm | 10 | 1.0 | 999999.0 |  |
| PHomeParam | `A1.HomeDirection` | `0` | Y轴.回原点方向 | Y Axis.Go Origin Direction | enum |  | 0 | 0.0 | 1.0 | 0=Positive (负向); 1=Negative (正向) |
| PHomeParam | `A1.HomeOffset` | `10` | Y轴.返回距离 | Y Axis.Go Origin Offset | double | mm | 10 | 1.0 | 999999.0 |  |
| PHomeParam | `HPA3.HomeDirection` | `0` | 第4轴.回原点方向 | The 4th Axis.Go Origin Direction | enum |  | 0 | 0.0 | 1.0 | 0=Positive (负向); 1=Negative (正向) |
| PHomeParam | `HPA3.HomeOffset` | `10` | 第4轴.回原点返回距离 | The 4th Axis.Go Origin Offset | double | mm | 10 | 1.0 | 999999.0 |  |
| PHomeParam | `HPA3.SampleSignal` | `1` | 第4轴.回原点采样信号 | The 4th Axis.Go Origin Sample Signal Type | enum | ms | 1 | 0.0 | 99999.0 | 0=Origin (原点); 1=Limit (限位) |
| PHomeParam | `HPA3.OriginSwitchType` | `0` | 第4轴.原点开关逻辑 | The 4th Axis.Origin Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PHomeParam | `HPA3.FastSpeed` | `50` | 第4轴.粗定位速度 | The 4th Axis.Go Origin Fast Speed | double | mm / s | 50 | 0.01 | 999999.0 |  |
| PHomeParam | `HPA3.SlowSpeed` | `10` | 第4轴.精定位速度 | The 4th Axis.Go Origin Slow Speed | double | mm / s | 10 | 0.01 | 999999.0 |  |
| PHomeParam | `HPA3.Acc` | `4000` | 第4轴.加速度 | The 4th Axis.Accelerate | double | mm / s2 | 4000.0 | 500.0 | 20000.0 |  |
| PHomeParam | `HPA3.AccTime_ms` | `125` | 第4轴.加速时间 | The 4th Axis.Accelerate time | int | ms | 125 | 60.0 | 250.0 |  |
| PHomeParam | `HPA3.WorkSpeed` | `50` | 第4轴.工作速度 | The 4th Axis.Work Speed | double |  | 50 | 0.1 | 5000.0 |  |
| PHomeParam | `HPA3.IdelSpeed` | `100` | 第4轴.空跳速度 | The 4th Axis.Idle Speed | double |  | 100 | 0.1 | 5000.0 |  |
| PZFParam | `ZF.ZFType` | `1` | 总体.控制方式 | General.Control Type | enum | mm | 0 | 0.0 | 4.0 | 0=None (不使用); 1=Onboard FTC (板载调高器) |
| PZFParam | `ZF.SerialPort` | `0` | 总体.端口号(COM) | PC Serial.Port Number(COM) | COM port |  | 0 | 0.0 | 16.0 |  |
| PZFParam | `ZF.SerialBaudRate` | `0` | 总体.波特率 | PC Serial.Baud Rate | enum |  | 0 | 0.0 | 4.0 | 0=9600; 1=19200; 2=38400; 3=57600; 4=115200 |
| PZFParam | `ZF.DOFollow` | `0` | 输入.跟随 | DI.Follow | DO port |  | 0 | 0.0 | 26.0 |  |
| PZFParam | `ZF.DODrill` | `0` | 输入.穿孔 | DI.Drill | DO port |  | 0 | 0.0 | 26.0 |  |
| PZFParam | `ZF.DOJogUp` | `0` | 输入.点动上 | DI.Jog Up | DO port |  | 0 | 0.0 | 26.0 |  |
| PZFParam | `ZF.DOJogDown` | `0` | 输入.点动下 | DI.Jog Down | DO port |  | 0 | 0.0 | 26.0 |  |
| PZFParam | `ZF.DIWarning` | `0` | 输出.告警状态 | DO.Alarm Status | int |  | 0 | 0.0 | 16.0 |  |
| PZFParam | `ZF.DIFollowReady` | `0` | 输出.跟随到位 | DO.Follow in Place | int |  | 0 | 0.0 | 16.0 |  |
| PZFParam | `ZF.DIDrillReady` | `0` | 输出.穿孔到位 | DO.Drill in Place | int |  | 0 | 0.0 | 16.0 |  |
| PLaserParam | `LGP.LaserType` | `0` | 总体.激光器类型 | General.Laser Type | enum |  | 0 | 0.0 | 8.0 | 0=Raycus (锐科); 1=IPG; 2=Semiconductor (半导体); 3=MaxPhotonics (创鑫); 4=Super (联品); 5=TXStar (天星); 6=nLight; 7=GZ (国志); 8=Others (其它) |
| PLaserParam | `LGP.LaserControlType` | `3` | 总体.控制方式 | General.Control Type | enum |  | 0 | 0.0 | 4.0 | 0=None (不使用); 1=MCC Serial (板载串口); 2=Net (网口); 3=IO; 4=PC Serial (电脑串口) |
| PLaserParam | `LGP.LaserDAPort` | `1` | DA.DA端口 | DA.DA Port | DA port |  | 0 | 0.0 | 2.0 |  |
| PLaserParam | `LGP.LaserDAType` | `0` | DA.DA范围 | DA.DA Range | enum |  | 0 | 0.0 | 2.0 | 0=0～10V; 1=0～5V; 2=0～4V |
| PLaserParam | `LGP.DORemoteStart` | `0` | IO.远程钥匙（准备） | IO.Remote Key | DO port |  | 0 | 0.0 | 26.0 |  |
| PLaserParam | `LGP.DOLaserGate` | `5` | IO.光闸 | IO.Shutter | DO port |  | 0 | 0.0 | 26.0 |  |
| PLaserParam | `LGP.DOLaser` | `0` | IO.激光输出 | IO.Laser Emission | DO port |  | 0 | 0.0 | 26.0 |  |
| PLaserParam | `LGP.DORedLight` | `6` | IO.红光 | IO.Red Light | DO port |  | 0 | 0.0 | 26.0 |  |
| PLaserParam | `LGP.LaserSerialPort` | `0` | 总体.端口号(COM) | PC Serial.Port Number(COM) | COM port |  | 0 | 0.0 | 16.0 |  |
| PLaserParam | `LGP.LaserSerialBaudRate` | `0` | 总体.波特率 | PC Serial.Baud Rate | enum |  | 0 | 0.0 | 4.0 | 0=9600; 1=19200; 2=38400; 3=57600; 4=115200 |
| PLaserParam | `LGP.LaserMaxPower` | `0` | 总体.激光最大功率 | General.Laser Max Power | double | W | 0 | 0.0 | 1000000.0 |  |
| PLaserParam | `LGP.CO2LaserControlType` | `2` | 总体.控制方式 | General.Control Type | enum |  | 2 | 0.0 | 3.0 | 0=None (不使用); 1=24V PWM; 2=5V PWM; 3=DA |
| PLaserParam | `LGP.CO2LaserDAPort` | `1` | DA.DA端口 | DA.DA Port | DA port |  | 0 | 0.0 | 2.0 |  |
| PLaserParam | `LGP.CO2LaserDAType` | `1` | DA.DA范围 | DA.DA Range | enum |  | 0 | 0.0 | 2.0 | 0=0～10V; 1=0～5V; 2=0～4V |
| PLaserParam | `LGP.CO2DORemoteStart` | `0` | IO.远程钥匙（准备） | IO.Remote Key | DO port |  | 0 | 0.0 | 26.0 |  |
| PLaserParam | `LGP.CO2DOLaserGate` | `0` | IO.光闸 | IO.Shutter | DO port |  | 0 | 0.0 | 26.0 |  |
| PLaserParam | `LGP.CO2DOLaser` | `9` | IO.激光输出 | IO.Laser Emission | DO port |  | 0 | 0.0 | 26.0 |  |
| PLaserParam | `LGP.CO2DORedLight` | `0` | IO.红光 | IO.Red Light | DO port |  | 0 | 0.0 | 26.0 |  |
| PLaserParam | `LGP.doCO2EnableOutput` | `0` | IO.外控输出 | IO.External control output | DO port |  | 0 | 0.0 | 26.0 |  |
| PLaserParam | `LPF.LiftingPlatformType` | `1` | 升降平台.控制方式 | Lifting platform.Control Type | enum |  | 0 | 0.0 | 1.0 | 0=None (不启用); 1=W Axis (W轴) |
| PManuParam | `MP.LaserDAKeepOutput` | `0` | DA.DA上电输出 | DA.DA Keep Output | bool |  | 1 | 0.0 | 1.0 |  |
| PManuParam | `MP.XAxisGapCompensate` | `0` | 精度补偿.X轴反向间隙 | Precsion Compensate.X Axis Back Lash | double |  | 0 | 0.0 | 100.0 |  |
| PManuParam | `MP.YAxisGapCompensate` | `0` | 精度补偿.Y轴反向间隙 | Precsion Compensate.Compensate Type | double |  | 0 | 0.0 | 100.0 |  |
| PManuParam | `MP.CompensateType` | `1` | 精度补偿.补偿类型 | Precsion Compensate.Compensate Type | int |  | 0 | 0.0 | 2.0 |  |
| PManuParam | `MP.OilDOPort` | `0` | 自润滑.润滑开关端口 | Lubrication.Lubrication DO Port | DO port |  | 0 | 0.0 | 26.0 |  |
| PManuParam | `MP.OilInterval` | `60` | 自润滑.润滑间隔时间 | Lubrication.Lubrication Interval | int |  | 60 | 1.0 | 9999.0 |  |
| PManuParam | `MP.OilKeepTime` | `30` | 自润滑.润滑持续时间 | Lubrication.Lubrication Keep Time | int |  | 30 | 1.0 | 9999.0 |  |
| PManuParam | `MP.SecitonDORow` | `2` | 分区输出.分区输出行数 | Section DO.Section DO Rows Count | int |  | 2 | 1.0 | 16.0 |  |
| PManuParam | `MP.SecitonDOColumn` | `2` | 分区输出.分区输出列数 | Section DO.Section DO Columns Count | int |  | 2 | 1.0 | 16.0 |  |
| PManuParam | `MP.SecitonDOCloseDelayTime` | `5` | 分区输出.输出关闭延时 | Section DO.Section DO Close Delay | int |  | 5 | 0.0 | 9999.0 |  |
| PManuParam | `MP.SecitonDOOnlyOpenInManu` | `1` | 分区输出.仅在切割时输出 | Section DO.Section DO Only Open In Manu | bool |  | 1 | 0.0 | 1.0 |  |
| PManuParam | `MP.EnableVerCorrect` | `0` | 精度补偿.补偿类型 | Precsion Compensate.Compensate Type | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.VerCorrectType` | `0` | 精度补偿.补偿类型 | Precsion Compensate.Compensate Type | int |  | 1 | 0.0 | 1.0 |  |
| PManuParam | `MP.VerCorrectABLength` | `150` | 精度补偿.补偿类型 | Precsion Compensate.Compensate Type | double |  | 100.0 | 0.0 | 9999.0 |  |
| PManuParam | `MP.VerCorrectACLength` | `150` | 精度补偿.补偿类型 | Precsion Compensate.Compensate Type | double |  | 100.0 | 0.0 | 9999.0 |  |
| PManuParam | `MP.VerCorrectL1Length` | `212.19999999999999` | 精度补偿.补偿类型 | Precsion Compensate.Compensate Type | double |  | 100.0 | 0.0 | 9999.0 |  |
| PManuParam | `MP.VerCorrectL2Length` | `212.19999999999999` | 精度补偿.补偿类型 | Precsion Compensate.Compensate Type | double |  | 100.0 | 0.0 | 9999.0 |  |
| PManuParam | `MP.AlarmSignalIsTwinkle` | `0` | 报警设置.开启报警灯闪烁 | Alarm Settings.Enable Alarm LED Twinkle | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.AlarmSignalOnSecond` | `0.5` | 报警设置.报警灯开启时间 | Alarm Settings.Alarm LED ON Time | double |  | 0.5 | 0.0 | 100.0 |  |
| PManuParam | `MP.AlarmSignalOffSecond` | `0.5` | 报警设置.报警灯关闭时间 | Alarm Settings.Alarm LED OFF Time | double |  | 0.5 | 0.0 | 100.0 |  |
| PManuParam | `MP.AlarmRingIsTwinkle` | `0` | 报警设置.开启报警铃声断续 | Alarm Settings.Enable Alarm Ring Twinkle | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.AlarmRingOnSecond` | `0.5` | 报警设置.报警铃声开启时间 | Alarm Settings.Alarm Ring ON Time | double |  | 0.5 | 0.0 | 100.0 |  |
| PManuParam | `MP.AlarmRingOffSecond` | `0.5` | 报警设置.报警铃声关闭时间 | Alarm Settings.Alarm Ring OFF Time | double |  | 0.5 | 0.0 | 100.0 |  |
| PManuParam | `MP.WaitSignalIsTwinkle` | `0` | 指示灯设置.开启待机指示灯闪烁 | LED Settings.Enable Wait LED Twinkle | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.WaitSignalOnSecond` | `0.5` | 指示灯设置.待机指示灯开启时间 | LED Settings.Wait LED ON Time | double |  | 0.5 | 0.0 | 100.0 |  |
| PManuParam | `MP.WaitSignalOffSecond` | `0.5` | 指示灯设置.待机指示灯关闭时间 | LED Settings.Wait LED OFF Time | double |  | 0.5 | 0.0 | 100.0 |  |
| PManuParam | `MP.ManuSignalIsTwinkle` | `0` | 指示灯设置.开启加工指示灯闪烁 | LED Settings.Enable Manu LED Twinkle | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.ManuSignalOnSecond` | `0.5` | 指示灯设置.加工指示灯开启时间 | LED Settings.Manu LED ON Time | double |  | 0.5 | 0.0 | 100.0 |  |
| PManuParam | `MP.ManuSignalOffSecond` | `0.5` | 指示灯设置.加工指示灯关闭时间 | LED Settings.Manu LED OFF Time | double |  | 0.5 | 0.0 | 100.0 |  |
| PManuParam | `MP.DirectDrillMaxHeight` | `6` | 穿孔参数.直接穿孔最大高度 | Drill Parameters.Direction Drill Max Height | double |  | 6.0 | 1.0 | 1000.0 |  |
| PManuParam | `MP.DirectSecondDrillMaxHeight` | `4` | 穿孔参数.直接次级穿孔最大高度 | Drill Parameters.Direction Second Drill MAx Height | double |  | 4.0 | 1.0 | 1000.0 |  |
| PManuParam | `MP.PreLaserOnFactor` | `0` | 高级工艺.出光系数 | Adv Parameters.Pre Laser On Factor | double |  | 0 | 0.0 | 100.0 |  |
| PManuParam | `MP.EnableExchangePlatform` | `0` | 高级工艺.启用交换工作平台 | Adv Parameters.Enable Exchange Platform | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.IsStopWaitZFDone` | `0` | 工艺过程.停止或完成加工时等待调高到位 | Process Parameters.Wait ZF Done When Stop Manu | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.ExchangePlatformType` | `0` | 总体.平台类型 | General Parameters.Platform Type | enum |  | 0 | 0.0 | 2.0 | 0=None (不启用); 1=Extend Axis Control (扩展轴控制); 2=Variable Freq Motor (变频电机); 3=Pulse axis control (脉冲轴控制); 4=Bus axis control (总线轴控制) |
| PManuParam | `MP.PlatformExchangeLength` | `2000` | 总体.平台交换长度 | General Parameters.Platform Exhange Length | double |  | 2000 | 0.0 | 10000.0 |  |
| PManuParam | `MP.PlatformExchangeSpeed` | `100` | 总体.平台交换速度 | General Parameters.Platform Exhange Speed | double |  | 100 | 0.0 | 2000.0 |  |
| PManuParam | `MP.PlatInPlaceClampDelay` | `0` | 延时参数.到位夹紧延时 | Delay Parameters.Inplace Clamp Delay | int | ms | 0 | 0.0 | 9999.0 |  |
| PManuParam | `MP.PlatClampClearDelay` | `0` | 延时参数.夹紧解除延时 | Delay Parameters.Clamp Clear Delay | int | ms | 0 | 0.0 | 9999.0 |  |
| PManuParam | `MP.PlatWaitDccTimeout` | `0` | 超时参数.等待减速信号超时时间 | Timeout Parameters.Wait Decelerate Signal Timeout | int | ms | 0 | 0.0 | 9999.0 |  |
| PManuParam | `MP.PlatWaitInPlaceTimeout` | `0` | 超时参数.等待到位信号超时时间 | Timeout Parameters.Wait In Place Signal Timeout | int | ms | 0 | 0.0 | 9999.0 |  |
| PManuParam | `MP.IsZFGoOriginAfterDone` | `0` | 调高器.加工完成后调高器自动回原 | ZF Parameters.ZF Go Origin Automatically When Manu Done | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.ZFSafeHeight` | `15` | 调高器.停止时抬起安全高度 | ZF Parameters.Go Up Safe Height | double |  | 15 | 1.0 | 100.0 |  |
| PManuParam | `MP.ZFFrogJumpType` | `0` | 调高器.蛙跳模式 | ZF Parameters.Frog Jump Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal (普通蛙跳); 1=Advanced (高级蛙跳) |
| PManuParam | `MP.EnableZFFrogJumpProtect` | `0` | 调高器.启用蛙跳碰板保护 | ZF Parameters.Enable Frog Jump Safe Protect | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.RollSheetType` | `0` | 总体.平台类型 | General Parameters.Platform Type | enum |  | 0 | 0.0 | 4.0 | 0=None (不启用); 1=Extend Axis Control (扩展轴控制); 2=Variable Freq Motor (变频电机); 3=Pulse axis control (脉冲轴控制); 4=Bus axis control (总线轴控制) |
| PManuParam | `MP.SingleRollSheetLength` | `2000` | 总体.单次卷料长度 | General Parameters.Single Roll Length | double |  | 2000 | 0.0 | 100000000.0 |  |
| PManuParam | `MP.SingleRollSheetSpeed` | `100` | 总体.单次卷料速度 | General Parameters.Single Roll Speed | double |  | 100 | 0.0 | 2000.0 |  |
| PManuParam | `MP.ManuDoneOutputDelay` | `500` | 流程输出.加工完成后开启端口持续时间 | Process Output.After Manu Done Open Port Keep Time | int | ms | 500 | 0.0 | 1000.0 |  |
| PManuParam | `MP.IsEnablePWMPerContour` | `1` | 激光参数.每段轮廓切换PWM使能 | Laser Parameters.Enable PWM Per Contour | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.PlatAfterClampDelay` | `0` | 延时.平台夹紧前延时 | Delay.Before Platform Clamp Delay | int | ms | 0 | 0.0 | 999999.0 |  |
| PManuParam | `MP.PlatBeforeClampDelay` | `0` | 延时.平台夹紧后延时 | Delay.After Platform Clamp Delay | int | ms | 0 | 0.0 | 999999.0 |  |
| PManuParam | `MP.PlatBeforeLoosenDelay` | `0` | 延时.平台松开前延时 | Delay.Before Platform Loosen Delay | int | ms | 0 | 0.0 | 999999.0 |  |
| PManuParam | `MP.PlatAfterLoosenDelay` | `0` | 延时.平台松开后延时 | Delay.After Platform Loosen Delay | int | ms | 0 | 0.0 | 999999.0 |  |
| PManuParam | `MP.AutoCloseLoopManuAfterManuDone` | `0` | 加工工程.加工完成后自动关闭循环加工 | Manu Process.Auto Close Loop Manu After Manu Done | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.JogStopDccFactor` | `1` | 手动控制.手动移动停止减速系数 | Jog Parameters.Jog Stop Deceleration Factor | int |  | 1 | 1.0 | 100.0 |  |
| PManuParam | `MP.EnableSmallCircleSpeedLimit` | `0` | 小圆限速.启用小圆限速 | Small Circle Speed Limit.Enable Small Circle Speed Limit | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.SmallCircleSpeedLimitRatio` | `0.59999999999999998` | 小圆限速.小圆限速比率 | Small Circle Speed Limit.Small Circle Speed Limit Ratio | double |  | 0.6 | 0.1 | 1.0 |  |
| PManuParam | `MP.ZFGoOriginDoneOutputDelay` | `200` | 交换平台.调高器回原完成输出延时 | Exchange Platform.ZF Go Origin Output Delay | int | ms | 200 | 0.0 | 999999.0 |  |
| PManuParam | `MP.EnablePLCProcess1` | `0` | PLC过程1.启用PLC过程1 | PLC Process 1.Enable PLC Process 1 | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.PLCProcess1AfterOpenADelay` | `0` | PLC过程1.输出口A打开后延时 | PLC Process 1.After Open DO-A Delay | int |  | 0 | 0.0 | 100000000.0 |  |
| PManuParam | `MP.PLCProcess1AfterOpenBDelay` | `0` | PLC过程1.输出口B打开后延时 | PLC Process 1.After Open DO-B Delay | int |  | 0 | 0.0 | 100000000.0 |  |
| PManuParam | `MP.PLCProcess1BeforeCloseDODelay` | `0` | PLC过程1.输出口关闭前延时 | PLC Process 1.Before Close DO Delay | int |  | 0 | 0.0 | 100000000.0 |  |
| PManuParam | `MP.SelectedProcessMode` | `0` | 选中加工.加工模式 | Selected Process.Process Mode | enum |  | 0 | 0.0 | 1.0 | 0=Part Mode (工件模式); 1=Float Mode (浮动模式) |
| PManuParam | `MP.BeforeRollSheetDelay` | `0` | 高级.卷料就绪延时 | Advanced.Roll Sheet Ready Delay | int |  | 0 | 0.0 | 100000000.0 |  |
| PManuParam | `MP.EnableManuCrashProtect` | `1` | 碰撞保护.启用加工中碰撞保护 | Crash Protect.Enable Process Crash Protect | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.ManuCrashProtectUpHeight` | `35` | 碰撞保护.碰撞保护上抬高度 | Crash Protect.Enable Crash Protect Up Height | double |  | 50 | 0.0 | 10000.0 |  |
| PManuParam | `MP.EnableManuCrashProtectMinHeight` | `5` | 碰撞保护.启用碰撞保护最小高度 | Crash Protect.Enable Crash Protect Min Height | double |  | 20 | 0.0 | 10000.0 |  |
| PManuParam | `MP.ShowManufacturerName` | `0` | 自定义.显示自定义制造商名称 | Customized.Show Customized Company Name | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.CustomCompanyName` | `` | 自定义.自定义制造商名称 | Customized.Company Name | string |  |  | 0.0 | 0.0 |  |
| PManuParam | `MP.RotatePlatformType` | `0` | 总体.平台类型 | General Parameters.Platform Type | enum |  | 0 | 0.0 | 1.0 | 0=None (不启用); 1=Extend Axis Control (扩展轴控制) |
| PManuParam | `MP.RotateAxisChangeDelay` | `200` | 输出.旋转轴切换延时 | Output Parameters.Rotate Axis Change Delay | int |  | 200 | 0.0 | 1000000.0 |  |
| PManuParam | `MP.EmptyMoveSpeedFactor` | `1.1000000000000001` | 加工系数.速度系数1 | Manu Parameters.Speed Factor 1 | double |  | 1.1 | 0.0 | 1000000.0 |  |
| PManuParam | `MP.EmptyMoveAccFactor` | `1.5` | 加工系数.速度系数2 | Manu Parameters.Speed Factor 2 | double |  | 1.5 | 0.0 | 1000000.0 |  |
| PManuParam | `MP.EnablePlatformABSyncClamp` | `0` | 高级.平台AB是否同时夹紧 | Advanced.Enable Platform A/B Sync Clamp | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.AfterRollSheetDelay` | `2000` | 高级.卷料后延时 | Advanced.After Roll Sheet Delay | int |  | 2000 | 0.0 | 100000000.0 |  |
| PManuParam | `MP.RollSheetManuStartPortIndex` | `0` | 高级.卷料后加工开始输入端口 | Advanced.After Roll Sheet Process Start DI | DI port |  | 0 | 0.0 | 28.0 |  |
| PManuParam | `MP.RollSheetManuStartPortType` | `0` | 高级.卷料后加工开始输入端口类型 | Advanced.After Roll Sheet Process Start DI | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PManuParam | `MP.IsAutoPauseAfterRollSheetDone` | `0` | 高级.卷料完成后加工自动暂停 | Advanced.Enable Auto Pause After Roll Sheet Done | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.ClampDOChangeDelay` | `0` | 高级.夹紧输出口切换延时 | Advanced.Roll Sheet Clamp DO Change Delay | int |  | 0 | 0.0 | 100000000.0 |  |
| PManuParam | `MP.AfterClampDelay` | `0` | 高级.卷料夹紧后延时 | Advanced.After Roll Sheet Clamp Delay | int |  | 0 | 0.0 | 100000000.0 |  |
| PManuParam | `MP.EnableForwardBackwardRollsheet` | `0` | 往返卷料.启用往返卷料 | Forward And Backward Roll Sheet.Enable Forward And Backward Roll Sheet | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.SingleForwardRollLength` | `500` | 往返卷料.单次卷料前进距离 | Forward And Backward Roll Sheet.Single Forward Roll Length | double |  | 500 | -1000000.0 | 1000000.0 |  |
| PManuParam | `MP.SingleBackwardLength` | `500` | 往返卷料.单次卷料回退距离 | Forward And Backward Roll Sheet.Single Backward Roll Length | double |  | 500 | -1000000.0 | 1000000.0 |  |
| PManuParam | `MP.Use4thAxisForExtEncoderPosition` | `0` | 变频电机.使用W轴采集外置编码器位置 | VFD Motor.Use W axis for the external encoder position | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.RollSheetEnableFindHome` | `0` | 高级.启用卷料轴回零 | Advanced. Enable reel axis zero return | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.CustomCompanyPassword` | `1234` | 自定义.自定义制造商密码 | Customized.Company Password | int |  | 0 | 0.0 | 999999.0 |  |
| PManuParam | `MP.EnableOpenOperatePermission` | `0` | 自定义.以操作员权限启动 | Customized.Start with operator privileges | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `AF.AFType` | `0` | 电动调焦.控制方式 | Auto Focus.Control Type | enum |  | 0 | 0.0 | 1.0 | 0=None (不使用); 1=MCC Serial (板载串口) |
| PManuParam | `AF.SerialPort` | `0` | 总体.端口号(COM) | PC Serial.Port Number(COM) | COM port |  | 0 | 0.0 | 16.0 |  |
| PManuParam | `AF.SerialBaudRate` | `0` | 总体.波特率 | PC Serial.Baud Rate | enum |  | 0 | 0.0 | 4.0 | 0=9600; 1=19200; 2=38400; 3=57600; 4=115200 |
| PManuParam | `AF.AFEnableAdvMCCSerial` | `0` | 板载串口.启用高级板载串口 | MCC Serial.Enable Advanced MCC Serial | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `EC.ECType` | `0` | 总体.控制方式 | General.Control Type | enum |  | 0 | 0.0 | 1.0 | 0=None (不使用); 1=MCC Serial (板载串口) |
| PManuParam | `EC.ECAxisPulseEq` | `1000` | 扩展轴.脉冲当量 | Extended Axis.Pulse Equivalent | pulse-eq | p/mm | 1000 | 1.0 | 999999.0 |  |
| PManuParam | `EC.ECAxisServoDir` | `0` | 扩展轴.伺服方向 | Extended Axis.Servo Direction | enum |  | 0 | 0.0 | 1.0 | 0=Negative (正向); 1=Positive (负向) |
| PManuParam | `EC.ECAxisMaxSpeed` | `1000` | 扩展轴.最大速度 | Extended Axis.Max Speed | int |  | 1000 | 1.0 | 9999.0 |  |
| PManuParam | `EC.ECIOType` | `0` | 总体.控制方式 | General.Control Type | enum |  | 0 | 0.0 | 1.0 | 0=None (不使用); 1=MCC Serial (板载串口) |
| PManuParam | `MGP.iProportionalOpenSleep` | `50` | 记忆零点 | Memory zero | int | ms | 50 | 0.0 | 999.0 |  |
| PGasParam | `MGP.LowAir` | `0` | 低压阀.低压空气 | Low Pressure Valve.Low Pressure Air | DO port |  | 0 | 0.0 | 26.0 |  |
| PGasParam | `MGP.LowO2` | `7` | 低压阀.低压氧气 | Low Pressure Valve.Low Pressure O2 | DO port |  | 0 | 0.0 | 26.0 |  |
| PGasParam | `MGP.LowN2` | `0` | 低压阀.低压氮气 | Low Pressure Valve.Low Pressure N2 | DO port |  | 0 | 0.0 | 26.0 |  |
| PGasParam | `MGP.HighAir` | `3` | 高压阀.高压空气 | High Pressure Valve.High Pressure Air | DO port |  | 0 | 0.0 | 26.0 |  |
| PGasParam | `MGP.HighO2` | `0` | 高压阀.高压氧气 | High Pressure Valve.High Pressure O2 | DO port |  | 0 | 0.0 | 26.0 |  |
| PGasParam | `MGP.HighN2` | `2` | 高压阀.高压氮气 | High Pressure Valve.High Pressure N2 | DO port |  | 0 | 0.0 | 26.0 |  |
| PGasParam | `MGP.RatioAir` | `0` | 比例阀.空气比例阀(DA) | Proportional Valve.Air(DA) | DA(prop.valve) |  | 0 | 0.0 | 2.0 |  |
| PGasParam | `MGP.RatioO2` | `2` | 比例阀.氧气比例阀(DA) | Proportional Valve.O2(DA) | DA(prop.valve) |  | 0 | 0.0 | 2.0 |  |
| PGasParam | `MGP.RatioH2` | `0` | 比例阀.氮气比例阀(DA) | Proportional Valve.N2(DA) | DA(prop.valve) |  | 0 | 0.0 | 2.0 |  |
| PGasParam | `MGP.DAMaxPressure` | `10` | 比例阀.最高气压 | Proportional Valve.Max Pressure | double |  | 10 | 0.01 | 100.0 |  |
| PGasParam | `MGP.RatioAirSwitch` | `0` | 比例阀.空气比例阀开关 | Proportional Valve.Air Proportional Valve Switch | DO port |  | 0 | 0.0 | 26.0 |  |
| PGasParam | `MGP.RatioO2Switch` | `0` | 比例阀.氧气比例阀开关 | Proportional Valve.O2 Proportional Valve Switch | DO port |  | 0 | 0.0 | 26.0 |  |
| PGasParam | `MGP.RatioN2Switch` | `0` | 比例阀.氮气比例阀开关 | Proportional Valve.N2 Proportional Valve Switch | DO port |  | 0 | 0.0 | 26.0 |  |
| PGasParam | `MGP.CoolGas` | `0` | 杂项.冷却气 | Misc.Cool Down Gas | DO port |  | 0 | 0.0 | 26.0 |  |
| PGasParam | `MGP.NewDAMAxPressureAir` | `10` | 比例阀.空气最高气压 | Proportional Valve.Max Pressure Air | double |  | 10 | 0.01 | 100.0 |  |
| PGasParam | `MGP.NewDAMAxPressureO2` | `10` | 比例阀.氧气最高气压 | Proportional Valve.Max Pressure O2 | double |  | 10 | 0.01 | 100.0 |  |
| PGasParam | `MGP.NewDAMAxPressureN2` | `10` | 比例阀.氮气最高气压 | Proportional Valve.Max Pressure N2 | double |  | 10 | 0.01 | 100.0 |  |
| PDOParam | `DO.WaitSignal` | `0` | 输出端口.待机指示灯 | DO.Standby Signal | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.WaitSignalIsNormalOn` | `0` | 输出端口.待机指示灯常亮 | Misc.Standby Signal Always On | bool |  | 0 | 0.0 | 1.0 |  |
| PDOParam | `DO.ManuSignal` | `0` | 输出端口.加工指示灯 | DO.Process Signal | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.AlarmSignal` | `1` | 输出端口.报警指示灯 | DO.Alarm Signal | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.AlarmRing` | `0` | 输出端口.报警铃声 | DO.Dedust | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.CustomDOStr` | `` | 输出.自定义输出 | DO.Custom DO | string |  |  | 0.0 | 0.0 |  |
| PDOParam | `DO.SectionDOStr` | `0,0,0;0,1,0;1,0,0;1,1,0;` | 分区输出.输出端口 | Section DO.DO Port | string |  |  | 0.0 | 0.0 |  |
| PDOParam | `DO.ClearAsh` | `0` | 自定义输出.除尘端口 | Custom DO.Clear Ash Port | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.Fn1Port` | `0` | 自定义输出.Fn1端口 | Custom DO.Fn1 Port Index | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.Fn1Type` | `1` | 自定义输出.Fn1输出类型 | Custom DO.Fn1 Port Type | enum |  | 1 | 0.0 | 1.0 | 0=UnSelf-Lock (非自锁输出); 1=Self-Lock (自锁输出) |
| PDOParam | `DO.Fn2Port` | `0` | 自定义输出.Fn2端口 | Custom DO.Fn2 Port Index | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.Fn2Type` | `1` | 自定义输出.Fn2输出类型 | Custom DO.Fn2 Port Type | enum |  | 1 | 0.0 | 1.0 | 0=UnSelf-Lock (非自锁输出); 1=Self-Lock (自锁输出) |
| PDOParam | `DO.ManuOutput` | `0` | 输出.加工中输出端口 | Output Parameters.Manu Output Output Port | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.RollSheetOutput` | `0` | 输出.卷料中输出端口 | Output Parameters.Roll Sheet Output Port | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.ManuDoneOutput` | `0` | 流程输出.加工完成后开启端口 | Process Output.After Manu Done Open Port | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.GtAirEnableGasDAMap` | `0` | 气压映射.启用空气气压校正 | Gas Pressure Map.Enable Air Pressure Adjust | bool |  | 0 | 0.0 | 1.0 |  |
| PDOParam | `DO.GtO2EnableGasDAMap` | `1` | 气压映射.启用氧气气压校正 | Gas Pressure Map.Enable O2 Pressure Adjust | bool |  | 0 | 0.0 | 1.0 |  |
| PDOParam | `DO.GtN2EnableGasDAMap` | `0` | 气压映射.启用氮气气压校正 | Gas Pressure Map.Enable N2 Pressure Adjust | bool |  | 0 | 0.0 | 1.0 |  |
| PDOParam | `DO.PlatAClamp` | `0` | 输出.平台A夹紧 | DO.Platform A Clamp | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.PlatAFastMove` | `0` | 输出.平台A高速移动 | DO.Platform A Fast Move | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.PlatASlowMove` | `0` | 输出.平台A低速移动 | DO.Platform A Slow Move | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.PlatBClamp` | `0` | 输出.平台B夹紧 | DO.Platform B Clamp | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.PlatBFastMove` | `0` | 输出.平台B高速移动 | DO.Platform B Fast Move | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.PlatBSlowMove` | `0` | 输出.平台B低速移动 | DO.Platform B Slow Move | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.ZFGoOriginDone` | `0` | 交换平台.调高器回原完成输出端口 | Exchange Platform.ZF Go Origin Done Output Port | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.PLCProcess1A` | `0` | PLC过程1.输出口A | PLC Process 1.DO A | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.PLCProcess1B` | `0` | PLC过程1.输出口B | PLC Process 1.DO B | DO port |  | 0 | 0.0 | 26.0 |  |
| PDOParam | `DO.RotateAxisChange` | `0` | 输出.旋转轴切换端口 | Output Parameters.Rotate Axis Change Port | DO port |  | 0 | 0.0 | 32.0 |  |
| PDOParam | `DO.PlatAMoveEnable` | `0` | 输出.平台A运动使能 | DO.Platform A Move Enable | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.PlatBMoveEnable` | `0` | 输出.平台B运动使能 | DO.Platform B Move Enable | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.RollSheetMoveEnable` | `0` | 变频电机.卷料运动使能 | VFD Motor.Roll Sheet Move Enable | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.RollSheetFastMove` | `0` | 变频电机.卷料高速移动 | VFD Motor.Roll Sheet Fast Move | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.RollSheetSlowMove` | `0` | 变频电机.卷料低速移动 | VFD Motor.Roll Sheet Slow Move | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.BackRollMoveEnable` | `0` | 变频电机.退料运动使能 | VFD Motor.Back Roll Move Enable | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.VFDMotorMoveLengthCalcFactor` | `1` | 变频电机.变频电机运动长度换算系数 | VFD Motor.Move Length Calc Factor | double |  | 1 | 0.0 | 1000000.0 |  |
| PDOParam | `DO.VFDMotorSlowStartLength` | `100` | 变频电机.变频电机慢速起步距离 | VFD Motor.Start Slow Move Length | double |  | 100 | 0.0 | 1000000.0 |  |
| PDOParam | `DO.VFDMotorSlowStopLength` | `100` | 变频电机.变频电机慢速停止距离 | VFD Motor.Stop Slow Move Length | double |  | 100 | 0.0 | 1000000.0 |  |
| PDOParam | `DO.RollSheetClampDO1` | `0` | 高级.卷料夹紧输出口1 | Advanced.Roll Sheet Clamp DO1 | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDOParam | `DO.RollSheetClampDO2` | `0` | 高级.卷料夹紧输出口2 | Advanced.Roll Sheet Clamp DO2 | ext DO port |  | 0 | 0.0 | 16.0 |  |
| PDIParam | `DI.EStop` | `0` | 输入端口.急停 | DI.Emergency Stop | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.WaterWarning` | `0` | 输入端口_光纤激光器.水冷报警 | DI_FiberLaser.Water Alarm | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.WaterWarningType` | `0` | 输入端口_光纤激光器.水冷报警开关类型 | DI_FiberLaser.Water Alarm Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PDIParam | `DI.LaserWarning` | `1` | 输入端口_光纤激光器.激光器报警 | DI_FiberLaser.Laser Alarm | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.LaserWarningType` | `0` | 输入端口_光纤激光器.激光器报警开关类型 | DI_FiberLaser.Laser Alarm Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PDIParam | `DI.AlarmDIStr` | `Door#4#-1#0` | 输出.自定义输出 | DO.Custom DO | string |  |  | 0.0 | 0.0 |  |
| PDIParam | `DI.FunctionDIStr` | `Start#0#0,Pause#0#0,Stop#0#0,ZF Go Origin#0#0,Low-pressure Air#0#0,Low-pressure Oxygen#12#-1,Low-pressure Nitrogen#0#0,High-pressure Air#0#0,High-pressure Oxygen#0#0,High-pressure Nitrogen#0#0` | 自定义输入.输入端口 | Func DI.DI Port | string |  |  | 0.0 | 0.0 |  |
| PDIParam | `DI.EPReady` | `0` | 输入信号.交换平台就绪 | Input Parameters.Exchange Platform Ready | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatADecc` | `0` | 输入信号.A台减速 | Input Parameters.A Platform Decelerate | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatBDecc` | `0` | 输入信号.B台减速 | Input Parameters.B Platform Decelerate | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatAInPlace` | `0` | 输入信号.A台到位 | Input Parameters.A Platform In Place | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatBInPlace` | `0` | 输入信号.B台到位 | Input Parameters.A Platform In Place | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatALimit` | `0` | 输入信号.A台硬限位 | Input Parameters.A Platform Hardware Limit | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatBLimit` | `0` | 输入信号.B台硬限位 | Input Parameters.B Platform Hardware Limit | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatEnableBtn` | `0` | 外部按钮.工作台使能 | External Button.Platform Enable | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatChangeStartBtn` | `0` | 外部按钮.开始交换 | External Button.Start Exchange | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatChangeStopBtn` | `0` | 外部按钮.停止交换 | External Button.Stop Exchange | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.RollSheetReady` | `0` | 输入.卷料就绪 | Input Parameters.Roll Sheet Ready | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.DI1SmoothTime` | `0` | 输入.DI1滤波时间 | DI.DI1 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI2SmoothTime` | `0` | 输入.DI2滤波时间 | DI.DI2 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI3SmoothTime` | `0` | 输入.DI3滤波时间 | DI.DI3 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI4SmoothTime` | `0` | 输入.DI4滤波时间 | DI.DI4 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI5SmoothTime` | `0` | 输入.DI5滤波时间 | DI.DI5 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI6SmoothTime` | `0` | 输入.DI6滤波时间 | DI.DI6 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI7SmoothTime` | `0` | 输入.DI7滤波时间 | DI.DI7 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI8SmoothTime` | `0` | 输入.DI8滤波时间 | DI.DI8 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI9SmoothTime` | `0` | 输入.DI9滤波时间 | DI.DI9 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI10SmoothTime` | `0` | 输入.DI10滤波时间 | DI.DI10 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI11SmoothTime` | `0` | 输入.DI11滤波时间 | DI.DI11 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI12SmoothTime` | `0` | 输入.DI12滤波时间 | DI.DI12 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI13SmoothTime` | `0` | 输入.DI13滤波时间 | DI.DI13 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI14SmoothTime` | `0` | 输入.DI14滤波时间 | DI.DI14 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI15SmoothTime` | `0` | 输入.DI15滤波时间 | DI.DI15 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.DI16SmoothTime` | `0` | 输入.DI16滤波时间 | DI.DI16 Smooth Signal Time | int |  | 0 | 0.0 | 1000.0 |  |
| PDIParam | `DI.PlatAClampDone` | `0` | 输入.平台A夹紧到位 | DI.Platform A Clamp Done | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatALoosenDone` | `0` | 输入.平台A松开到位 | DI.Platform A Loosen Done | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatASlowMoveLimit` | `0` | 输入.平台A低速限位 | DI.Platform A Slow Move Limit | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatAFastMoveLimit` | `0` | 输入.平台A停止限位 | DI.Platform A Fast Move Limit | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatBClampDone` | `0` | 输入.平台B夹紧到位 | DI.Platform B Clamp Done | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatBLoosenDone` | `0` | 输入.平台B松开到位 | DI.Platform B Loosen Done | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatBSlowMoveLimit` | `0` | 输入.平台B低速限位 | DI.Platform B Slow Move Limit | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatBFastMoveLimit` | `0` | 输入.平台B停止限位 | DI.Platform B Fast Move Limit | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.RollSheetStartStop` | `0` | 测试输入.卷料启停 | Test Input Parameters.Roll Sheet Start/Stop | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.PlatAClampDoneType` | `0` | 输入.平台A夹紧到位开关类型 | DI.Platform A Clamp Done Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PDIParam | `DI.PlatALoosenDoneType` | `0` | 输入.平台A松开到位开关类型 | DI.Platform A Loosen Done Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PDIParam | `DI.PlatASlowMoveLimitType` | `0` | 输入.平台A低速限位开关类型 | DI.Platform A Slow Move Limit Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PDIParam | `DI.PlatAFastMoveLimitType` | `0` | 输入.平台A停止限位开关类型 | DI.Platform A Fast Move Limit Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PDIParam | `DI.PlatBClampDoneType` | `0` | 输入.平台B夹紧到位开关类型 | DI.Platform B Clamp Done Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PDIParam | `DI.PlatBLoosenDoneType` | `0` | 输入.平台B松开到位开关类型 | DI.Platform B Loosen Done Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PDIParam | `DI.PlatBSlowMoveLimitType` | `0` | 输入.平台B低速限位开关类型 | DI.Platform B Slow Move Limit Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PDIParam | `DI.PlatBFastMoveLimitType` | `0` | 输入.平台B停止限位开关类型 | DI.Platform B Fast Move Limit Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PDIParam | `DI.RollSheetAxisFindHome` | `0` | 测试输入.卷料轴回零 | Test input. Coil axis return to zero | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.OnlyManualRelieveAlarm` | `0` | 仅可手动清除告警 | Alarms can only be cleared manually | bool |  | 0 | 0.0 | 1.0 |  |
| PDIParam | `DI.CO2WaterWarning` | `11` | 输入端口_CO2激光器.水冷报警 | DI_CO2Laser.Water Alarm | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.CO2WaterWarningType` | `1` | 输入端口_CO2激光器.水冷报警开关类型 | DI_CO2Laser.Water Alarm Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PDIParam | `DI.CO2LaserWarning` | `0` | 输入端口_CO2激光器.激光器报警 | DI_CO2Laser.Laser Alarm | DI port |  | 0 | 0.0 | 28.0 |  |
| PDIParam | `DI.CO2LaserWarningType` | `0` | 输入端口_CO2激光器.激光器报警开关类型 | DI_CO2Laser.Laser Alarm Switch Type | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PDIParam | `SP.EnableEncoderVel` | `0` | 轴运动.使用编码器值计算速度 | Axis Misc.Enable Enocder Velocity | bool |  | 0 | 0.0 | 1.0 |  |
| PDIParam | `DO.GtAirGasDAMapStr` | `` | 气压映射.空气气压校正数据 | Gas Pressure Map.Air Pressure Adjust Data | string |  |  | 0.0 | 0.0 |  |
| PDIParam | `DO.GtO2GasDAMapStr` | `` | 气压映射.氧气气压校正数据 | Gas Pressure Map.O2 Pressure Adjust Data | string |  |  | 0.0 | 0.0 |  |
| PDIParam | `DO.GtN2GasDAMapStr` | `` | 气压映射.氮气气压校正数据 | Gas Pressure Map.N2 Pressure Adjust Data | string |  |  | 0.0 | 0.0 |  |
| PDIParam | `DO.EnableBackRollSheet` | `0` | 高级.退料使能输出端口 | Advanced.Enable Back Roll Sheet DO Port | DO port |  | 0 | 0.0 | 26.0 |  |
| PDAParam | `DA.DA1OutputAdjustVal` | `3648` | DA.DA1输出校准值 | DA.DA1 Correction | int |  | 3648 | 0.0 | 4095.0 |  |
| PDAParam | `DA.DA2OutputAdjustVal` | `3480` | DA.DA2输出校准值 | DA.DA2 Correction | int |  | 3480 | 0.0 | 4095.0 |  |
| PDAParam | `AD.ADAdjustVal` | `3240` | AD.AD输入校准值 | AD.AD Correction | int |  | 3240 | 0.0 | 4095.0 |  |
| PFCParam | `FCP.FrogJumpMinHeight` | `10` | 高级切割.蛙跳最小高度 | Advanced Cutting.Frog Jump Min Height | int |  | 10 | 0.0 | 1000.0 |  |
| PFCParam | `FCP.MaxSpeed` | `3000` | 高级切割.系统最大速度 | Advanced Cutting.System Max Speed | int |  | 3000 | 1.0 | 9999.0 |  |
| PFCParam | `FCP.MaxAcc` | `20000` | 高级切割.系统最大加速度 | Advanced Cutting.System Max Acc | int |  | 20000 | 1.0 | 99999.0 |  |
| PFCParam | `FCP.FlycutEncoderToleranceRatio` | `6` | 飞行切割.编码器检测容差系数 | Scan Cutting.Encode Check Tolerance Ratio | double |  | 6.0 | 1.0 | 10.0 |  |
| PFCParam | `FCP.FlycutCircleVelRatio` | `1` | 飞行切割.圆飞切速度比例 | Scan Cutting.Circle Cut Circle Velocity Ratio | double |  | 1.0 | 0.0 | 10.0 |  |
| PFCParam | `FCP.FlycutCirclePwmDelayTime` | `0` | 飞行切割.圆飞切Pwm延后时间 | Scan Cutting.Cycle Cut Open PWM Offset Time | double |  | 0 | -10.0 | 10.0 |  |
| PFCParam | `FCP.FlycutLineOpenPwmForwardCycle` | `-3` | 飞行切割.方飞切开Pwm时间修正 | Scan Cutting.Line Cut Open PWM Offset Time | double |  | -10 | -50.0 | 50.0 |  |
| PFCParam | `FCP.FlycutLineColsePwmForwardCycle` | `-3` | 飞行切割.方飞切关Pwm时间修正 | Scan Cutting.Line Cut Close PWM Offset Time | double |  | -10 | -50.0 | 50.0 |  |
| PSoftParam | `SOP.RemoteType` | `3` | 手柄.手柄类型 | Remoter.Remoter Type | enum |  | 0 | 0.0 | 1.0 | 0=SC MCC (SC板载); 1=SC PC (SC电脑); 2=CypCut |
| PSoftParam | `SOP.JoystickID1` | `179705051` | 手柄.配对码1 | Remoter.Match Code 1 | int |  | 0 | 0.0 | 4294967295.0 |  |
| PSoftParam | `SOP.JoystickID2` | `-684904636` | 手柄.配对码2 | Remoter.Match Code 2 | int |  | 0 | 0.0 | 4294967295.0 |  |
| PSoftParam | `SOP.JoystickID3` | `434577794` | 手柄.配对码3 | Remoter.Match Code 3 | int |  | 0 | 0.0 | 4294967295.0 |  |
| PSoftParam | `SOP.HardwareType` | `0` | 控制卡.硬件版本 | Controller.Hardware Version | enum |  | 0 | 0.0 | 1.0 | 0=1; 1=2 |
| PSoftParam | `SOP.JoystickID4` | `1601441` | 手柄.配对码1 | Remoter.Match Code 1 | int |  | 0 | 0.0 | 4294967295.0 |  |
| PSoftParam | `SOP.JoystickID5` | `242474605` | 手柄.配对码1 | Remoter.Match Code 1 | int |  | 0 | 0.0 | 4294967295.0 |  |
| PSoftParam | `SOP.ShowManufacturerName` | `0` | 软件.显示默认制造商名称 | Software.Show Default Manufacturer Name | bool |  | 1 | 0.0 | 1.0 |  |
| PSoftParam | `SOP.IsStartNeedGoOrigin` | `1` | 软件.开机是否提示回原 | Software.System Need Go Origin | bool |  | 0 | 0.0 | 1.0 |  |
| PSoftParam | `SOP.IsStartAutoLoadGraph` | `0` | 软件.开机自动打开上次图形 | Software.Auto Load Last Graph | bool |  | 0 | 0.0 | 1.0 |  |
| PSoftParam | `SOP.EnableRemoteMonitor` | `0` | 远程监控.启用远程监控 | Remote Monitor.Enable Remote Monitor | bool |  | 0 | 0.0 | 1.0 |  |
| PSoftParam | `SOP.RemoteMonitorHeartbeat` | `0` | 远程监控.心跳周期 | Remote Monitor.Remote Monitor Heartbeat | int |  | 0 | 0.0 | 999999.0 |  |
| PSoftParam | `SOP.MonitorStartID` | `0` | 远程监控测试.测试起始ID | Remote Monitor Test.Test Start ID | int |  | 0 | 0.0 | 4294967295.0 |  |
| PSoftParam | `SOP.MonitorEndID` | `0` | 远程监控测试.测试终止ID | Remote Monitor Test.Test End ID | int |  | 0 | 0.0 | 4294967295.0 |  |
| PSoftParam | `SOP.MonitorTestInterval` | `1` | 远程监控测试.测试发送周期 | Remote Monitor Test.Test Interval | int |  | 1 | 0.0 | 100000.0 |  |
| PSoftParam | `SP.LimitDeccFactor` | `1` | 软件.限位减速系数 | Software.Limit Decceleration Factor | int |  | 1 | 1.0 | 10.0 |  |
| PSoftParam | `SP.LimitDeccLengthRatio` | `0.10000000000000001` | 软件.限位减速机床幅面比例 | Software.Limit Decceleration Length Ratio | double |  | 0.1 | 0.01 | 0.2 |  |
| PSoftParam | `SP.MachineID` | `0` | 远程监控.机床ID | Remote Monitor.Machine ID | int |  | 0 | 0.0 | 4294967295.0 |  |
| PSoftParam | `SP.DataCardID` | `0` | 远程监控.数据卡ID | Remote Monitor.Data Card ID | int |  | 0 | 0.0 | 4294967295.0 |  |
| PSoftParam | `SP.CommandID` | `0` | 远程监控.指令ID | Remote Monitor.Command ID | int |  | 0 | 0.0 | 4294967295.0 |  |
| PMachineAxisConfig | `MAC.DoubleDevice` | `0` | Y轴.双驱 | Y axis.Dual drive | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig | `MAC.AllowableError` | `2000` | Y轴.双驱容差 | Y axis.Dual drive tolerance | int |  | 2000 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig | `MAC.AlarmTime` | `1000` | Y轴.告警时间 | Y axis. Alarm time | int |  | 1000 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig | `MAC.CloseBreakTime` | `500` | 抱闸.释放延时 | Brake.release delay | int |  | 500 | 0.0 | 1000.0 |  |
| PMachineAxisConfig_0 | `MAC.SoftLimitMaxLen` | `1371` | 轴基本参数.最大行程 | Basic parameters of the axis.Maximum stroke | double |  | 1000 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_0 | `MAC.WritePluse` | `8000` | 轴基本参数.每转脉冲数 | Basic axis parameters.Encoder resolution | int |  | 10000 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_0 | `MAC.AxisReverse` | `1` | 轴基本参数.运行反向 | Axis basic parameters. Run in reverse direction | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_0 | `MAC.EncoderReverse` | `0` | 轴基本参数.编码器反向 | Axis basic parameters. Encoder reverse | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_0 | `MAC.GoOriginalDirection` | `0` | 轴基本参数.回原方向 | Axis basic parameters. Return to original direction | enum |  | 0 | 0.0 | 1.0 | 0=Positive (负向); 1=Negative (正向) |
| PMachineAxisConfig_0 | `MAC.SpeedRatio` | `31.003` | 轴基本参数.导程(mm) | Basic parameters of the shaft.Lead(mm) | double |  | 100 | 0.0 | 1000.0 |  |
| PMachineAxisConfig_0 | `MAC.IsRotatingShaft` | `0` | 轴基本参数.作为旋转轴 | Basic parameters of the axis. As a rotation axis | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_0 | `MAC.ZoreType` | `0` | 限位.原点输入类型 | Limit. Origin input type | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_0 | `MAC.OriginalInput` | `0` | 限位.原点输入端口 | Limit. Origin input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_0 | `MAC.NegativeType` | `0` | 限位.正限位开关逻辑 | Limit. Positive limit switch logic | enum |  | 0 | 0.0 | 1.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_0 | `MAC.NegativeLimitInput` | `6` | 限位.正限位输入端口 | Limit. Positive limit input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_0 | `MAC.ForwardType` | `0` | 限位.负限位开关逻辑 | Limit. Negative limit switch logic | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_0 | `MAC.ForwardLimitInput` | `5` | 限位.负限位输入端口 | Limit. Negative limit input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_0 | `MAC.BrakeOutput` | `0` | 抱闸.输出端口 | Brake.output port | DO port |  | 0 | 0.0 | 26.0 |  |
| PMachineAxisConfig_0 | `MAC.EnableZphaseSignal` | `0` | 回原高级参数.使用Z相信号 | Restore the advanced parameters. Use Z phase signal | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_0 | `MAC.SecondGoHome` | `0` | 回原高级参数.二次回原 | Reset advanced parameters. Reset again | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_0 | `MAC.SampleType` | `1` | 回原高级参数.采样信号 | Reset advanced parameters. Sample signal | enum |  | 1 | 0.0 | 0.0 | 0=Origin (原点); 1=Limit (限位) |
| PMachineAxisConfig_0 | `MAC.FastSpeed` | `80` | 回原高级参数.粗定位速度 | Return to original advanced parameters. Coarse positioning speed | double |  | 30 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_0 | `MAC.SecondSpeed` | `20` | 回原高级参数.精定位速度 | Return to original advanced parameters. Precision positioning speed | double |  | 20 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_0 | `MAC.ReturnLength` | `22` | 回原高级参数.返回距离 | Return to original advanced parameters. Return distance | double |  | 10 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_0 | `MAC.Acceleration` | `20000` | 工作参数.加速度 | Working parameter.Acceleration | double |  | 20000 | 0.0 | 1000000.0 |  |
| PMachineAxisConfig_0 | `MAC.AccelerationTime` | `125` | 工作参数.加速时间 | Working parameter.Acceleration time | int | ms | 125 | 60.0 | 250.0 |  |
| PMachineAxisConfig_1 | `MAC_1.SoftLimitMaxLen` | `950` | 轴基本参数.最大行程 | Basic parameters of the axis.Maximum stroke | double |  | 1000 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_1 | `MAC_1.WritePluse` | `8000` | 轴基本参数.每转脉冲数 | Basic axis parameters.Encoder resolution | int |  | 10000 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_1 | `MAC_1.AxisReverse` | `1` | 轴基本参数.运行反向 | Axis basic parameters. Run in reverse direction | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_1 | `MAC_1.EncoderReverse` | `0` | 轴基本参数.编码器反向 | Axis basic parameters. Encoder reverse | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_1 | `MAC_1.GoOriginalDirection` | `0` | 轴基本参数.回原方向 | Axis basic parameters. Return to original direction | enum |  | 0 | 0.0 | 1.0 | 0=Positive (负向); 1=Negative (正向) |
| PMachineAxisConfig_1 | `MAC_1.SpeedRatio` | `31.009` | 轴基本参数.导程(mm) | Basic parameters of the shaft.Lead(mm) | double |  | 100 | 0.0 | 1000.0 |  |
| PMachineAxisConfig_1 | `MAC_1.IsRotatingShaft` | `0` | 轴基本参数.作为旋转轴 | Basic parameters of the axis. As a rotation axis | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_1 | `MAC_1.ZoreType` | `0` | 限位.原点输入类型 | Limit. Origin input type | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_1 | `MAC_1.OriginalInput` | `0` | 限位.原点输入端口 | Limit. Origin input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_1 | `MAC_1.NegativeType` | `0` | 限位.正限位开关逻辑 | Limit. Positive limit switch logic | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_1 | `MAC_1.NegativeLimitInput` | `8` | 限位.正限位输入端口 | Limit. Positive limit input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_1 | `MAC_1.ForwardType` | `0` | 限位.负限位开关逻辑 | Limit. Negative limit switch logic | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_1 | `MAC_1.ForwardLimitInput` | `7` | 限位.负限位输入端口 | Limit. Negative limit input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_1 | `MAC_1.BrakeOutput` | `0` | 抱闸.输出端口 | Brake.output port | DO port |  | 0 | 0.0 | 26.0 |  |
| PMachineAxisConfig_1 | `MAC_1.EnableZphaseSignal` | `0` | 回原高级参数.使用Z相信号 | Restore the advanced parameters. Use Z phase signal | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_1 | `MAC_1.SecondGoHome` | `0` | 回原高级参数.二次回原 | Reset advanced parameters. Reset again | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_1 | `MAC_1.SampleType` | `1` | 回原高级参数.采样信号 | Reset advanced parameters. Sample signal | enum |  | 1 | 0.0 | 0.0 | 0=Origin (原点); 1=Limit (限位) |
| PMachineAxisConfig_1 | `MAC_1.FastSpeed` | `80` | 回原高级参数.粗定位速度 | Return to original advanced parameters. Coarse positioning speed | double |  | 30 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_1 | `MAC_1.SecondSpeed` | `20` | 回原高级参数.精定位速度 | Return to original advanced parameters. Precision positioning speed | double |  | 20 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_1 | `MAC_1.ReturnLength` | `15` | 回原高级参数.返回距离 | Return to original advanced parameters. Return distance | double |  | 10 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_1 | `MAC_1.Acceleration` | `20000` | 工作参数.加速度 | Working parameter.Acceleration | double |  | 20000 | 0.0 | 1000000.0 |  |
| PMachineAxisConfig_1 | `MAC_1.AccelerationTime` | `125` | 工作参数.加速时间 | Working parameter.Acceleration time | int | ms | 125 | 60.0 | 250.0 |  |
| PMachineAxisConfig_2 | `MAC_2.SoftLimitMaxLen` | `1000` | 轴基本参数.最大行程 | Basic parameters of the axis.Maximum stroke | double |  | 1000 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_2 | `MAC_2.WritePluse` | `2000` | 轴基本参数.每转脉冲数 | Basic axis parameters.Encoder resolution | int |  | 10000 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_2 | `MAC_2.AxisReverse` | `1` | 轴基本参数.运行反向 | Axis basic parameters. Run in reverse direction | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_2 | `MAC_2.EncoderReverse` | `0` | 轴基本参数.编码器反向 | Axis basic parameters. Encoder reverse | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_2 | `MAC_2.GoOriginalDirection` | `0` | 轴基本参数.回原方向 | Axis basic parameters. Return to original direction | enum |  | 0 | 0.0 | 1.0 | 0=Positive (负向); 1=Negative (正向) |
| PMachineAxisConfig_2 | `MAC_2.SpeedRatio` | `5` | 轴基本参数.导程(mm) | Basic parameters of the shaft.Lead(mm) | double |  | 100 | 0.0 | 1000.0 |  |
| PMachineAxisConfig_2 | `MAC_2.IsRotatingShaft` | `0` | 轴基本参数.作为旋转轴 | Basic parameters of the axis. As a rotation axis | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_2 | `MAC_2.ZoreType` | `0` | 限位.原点输入类型 | Limit. Origin input type | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_2 | `MAC_2.OriginalInput` | `0` | 限位.原点输入端口 | Limit. Origin input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_2 | `MAC_2.NegativeType` | `0` | 限位.正限位开关逻辑 | Limit. Positive limit switch logic | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_2 | `MAC_2.NegativeLimitInput` | `0` | 限位.正限位输入端口 | Limit. Positive limit input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_2 | `MAC_2.ForwardType` | `0` | 限位.负限位开关逻辑 | Limit. Negative limit switch logic | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_2 | `MAC_2.ForwardLimitInput` | `0` | 限位.负限位输入端口 | Limit. Negative limit input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_2 | `MAC_2.BrakeOutput` | `0` | 抱闸.输出端口 | Brake.output port | DO port |  | 0 | 0.0 | 26.0 |  |
| PMachineAxisConfig_2 | `MAC_2.EnableZphaseSignal` | `0` | 回原高级参数.使用Z相信号 | Restore the advanced parameters. Use Z phase signal | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_2 | `MAC_2.SecondGoHome` | `0` | 回原高级参数.二次回原 | Reset advanced parameters. Reset again | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_2 | `MAC_2.SampleType` | `1` | 回原高级参数.采样信号 | Reset advanced parameters. Sample signal | enum |  | 1 | 0.0 | 0.0 | 0=Origin (原点); 1=Limit (限位) |
| PMachineAxisConfig_2 | `MAC_2.FastSpeed` | `30` | 回原高级参数.粗定位速度 | Return to original advanced parameters. Coarse positioning speed | double |  | 30 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_2 | `MAC_2.SecondSpeed` | `20` | 回原高级参数.精定位速度 | Return to original advanced parameters. Precision positioning speed | double |  | 20 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_2 | `MAC_2.ReturnLength` | `5` | 回原高级参数.返回距离 | Return to original advanced parameters. Return distance | double |  | 10 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_2 | `MAC_2.Acceleration` | `20000` | 工作参数.加速度 | Working parameter.Acceleration | double |  | 20000 | 0.0 | 1000000.0 |  |
| PMachineAxisConfig_2 | `MAC_2.AccelerationTime` | `125` | 工作参数.加速时间 | Working parameter.Acceleration time | int | ms | 125 | 60.0 | 250.0 |  |
| PMachineAxisConfig_3 | `MAC_3.SoftLimitMaxLen` | `1000` | 轴基本参数.最大行程 | Basic parameters of the axis.Maximum stroke | double |  | 1000 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_3 | `MAC_3.WritePluse` | `10000` | 轴基本参数.每转脉冲数 | Basic axis parameters.Encoder resolution | int |  | 10000 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_3 | `MAC_3.AxisReverse` | `0` | 轴基本参数.运行反向 | Axis basic parameters. Run in reverse direction | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_3 | `MAC_3.EncoderReverse` | `0` | 轴基本参数.编码器反向 | Axis basic parameters. Encoder reverse | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_3 | `MAC_3.GoOriginalDirection` | `0` | 轴基本参数.回原方向 | Axis basic parameters. Return to original direction | enum |  | 0 | 0.0 | 1.0 | 0=Positive (负向); 1=Negative (正向) |
| PMachineAxisConfig_3 | `MAC_3.SpeedRatio` | `5` | 轴基本参数.导程(mm) | Basic parameters of the shaft.Lead(mm) | double |  | 100 | 0.0 | 1000.0 |  |
| PMachineAxisConfig_3 | `MAC_3.IsRotatingShaft` | `0` | 轴基本参数.作为旋转轴 | Basic parameters of the axis. As a rotation axis | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_3 | `MAC_3.ZoreType` | `0` | 限位.原点输入类型 | Limit. Origin input type | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_3 | `MAC_3.OriginalInput` | `0` | 限位.原点输入端口 | Limit. Origin input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_3 | `MAC_3.NegativeType` | `0` | 限位.正限位开关逻辑 | Limit. Positive limit switch logic | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_3 | `MAC_3.NegativeLimitInput` | `9` | 限位.正限位输入端口 | Limit. Positive limit input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_3 | `MAC_3.ForwardType` | `0` | 限位.负限位开关逻辑 | Limit. Negative limit switch logic | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_3 | `MAC_3.ForwardLimitInput` | `10` | 限位.负限位输入端口 | Limit. Negative limit input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_3 | `MAC_3.BrakeOutput` | `0` | 抱闸.输出端口 | Brake.output port | DO port |  | 0 | 0.0 | 26.0 |  |
| PMachineAxisConfig_3 | `MAC_3.EnableZphaseSignal` | `0` | 回原高级参数.使用Z相信号 | Restore the advanced parameters. Use Z phase signal | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_3 | `MAC_3.SecondGoHome` | `0` | 回原高级参数.二次回原 | Reset advanced parameters. Reset again | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_3 | `MAC_3.SampleType` | `1` | 回原高级参数.采样信号 | Reset advanced parameters. Sample signal | enum |  | 1 | 0.0 | 0.0 | 0=Origin (原点); 1=Limit (限位) |
| PMachineAxisConfig_3 | `MAC_3.FastSpeed` | `30` | 回原高级参数.粗定位速度 | Return to original advanced parameters. Coarse positioning speed | double |  | 30 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_3 | `MAC_3.SecondSpeed` | `20` | 回原高级参数.精定位速度 | Return to original advanced parameters. Precision positioning speed | double |  | 20 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_3 | `MAC_3.ReturnLength` | `10` | 回原高级参数.返回距离 | Return to original advanced parameters. Return distance | double |  | 10 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_3 | `MAC_3.Acceleration` | `20000` | 工作参数.加速度 | Working parameter.Acceleration | double |  | 20000 | 0.0 | 1000000.0 |  |
| PMachineAxisConfig_3 | `MAC_3.AccelerationTime` | `125` | 工作参数.加速时间 | Working parameter.Acceleration time | int | ms | 125 | 60.0 | 250.0 |  |
| PMachineAxisConfig_4 | `MAC_4.SoftLimitMaxLen` | `1000` | 轴基本参数.最大行程 | Basic parameters of the axis.Maximum stroke | double |  | 1000 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_4 | `MAC_4.WritePluse` | `2000` | 轴基本参数.每转脉冲数 | Basic axis parameters.Encoder resolution | int |  | 10000 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_4 | `MAC_4.AxisReverse` | `1` | 轴基本参数.运行反向 | Axis basic parameters. Run in reverse direction | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_4 | `MAC_4.EncoderReverse` | `0` | 轴基本参数.编码器反向 | Axis basic parameters. Encoder reverse | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_4 | `MAC_4.GoOriginalDirection` | `1` | 轴基本参数.回原方向 | Axis basic parameters. Return to original direction | enum |  | 0 | 0.0 | 1.0 | 0=Positive (负向); 1=Negative (正向) |
| PMachineAxisConfig_4 | `MAC_4.SpeedRatio` | `10` | 轴基本参数.导程(mm) | Basic parameters of the shaft.Lead(mm) | double |  | 100 | 0.0 | 1000.0 |  |
| PMachineAxisConfig_4 | `MAC_4.IsRotatingShaft` | `0` | 轴基本参数.作为旋转轴 | Basic parameters of the axis. As a rotation axis | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_4 | `MAC_4.ZoreType` | `0` | 限位.原点输入类型 | Limit. Origin input type | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_4 | `MAC_4.OriginalInput` | `0` | 限位.原点输入端口 | Limit. Origin input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_4 | `MAC_4.NegativeType` | `0` | 限位.正限位开关逻辑 | Limit. Positive limit switch logic | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_4 | `MAC_4.NegativeLimitInput` | `2` | 限位.正限位输入端口 | Limit. Positive limit input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_4 | `MAC_4.ForwardType` | `0` | 限位.负限位开关逻辑 | Limit. Negative limit switch logic | enum |  | 0 | 0.0 | 0.0 | 0=Normal Open (常开); 1=Normal Close (常闭) |
| PMachineAxisConfig_4 | `MAC_4.ForwardLimitInput` | `3` | 限位.负限位输入端口 | Limit. Negative limit input port | DI port |  | 0 | 0.0 | 28.0 |  |
| PMachineAxisConfig_4 | `MAC_4.BrakeOutput` | `0` | 抱闸.输出端口 | Brake.output port | DO port |  | 0 | 0.0 | 26.0 |  |
| PMachineAxisConfig_4 | `MAC_4.EnableZphaseSignal` | `0` | 回原高级参数.使用Z相信号 | Restore the advanced parameters. Use Z phase signal | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_4 | `MAC_4.SecondGoHome` | `0` | 回原高级参数.二次回原 | Reset advanced parameters. Reset again | bool |  | 0 | 0.0 | 1.0 |  |
| PMachineAxisConfig_4 | `MAC_4.SampleType` | `1` | 回原高级参数.采样信号 | Reset advanced parameters. Sample signal | enum |  | 1 | 0.0 | 0.0 | 0=Origin (原点); 1=Limit (限位) |
| PMachineAxisConfig_4 | `MAC_4.FastSpeed` | `30` | 回原高级参数.粗定位速度 | Return to original advanced parameters. Coarse positioning speed | double |  | 30 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_4 | `MAC_4.SecondSpeed` | `20` | 回原高级参数.精定位速度 | Return to original advanced parameters. Precision positioning speed | double |  | 20 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_4 | `MAC_4.ReturnLength` | `5` | 回原高级参数.返回距离 | Return to original advanced parameters. Return distance | double |  | 10 | 0.0 | 99999999.0 |  |
| PMachineAxisConfig_4 | `MAC_4.Acceleration` | `20000` | 工作参数.加速度 | Working parameter.Acceleration | double |  | 20000 | 0.0 | 1000000.0 |  |
| PMachineAxisConfig_4 | `MAC_4.AccelerationTime` | `125` | 工作参数.加速时间 | Working parameter.Acceleration time | int | ms | 125 | 60.0 | 250.0 |  |

### 1.2 `File/BkManuPara.xml` (machining / software parameters) — 331 attributes

| Section | Key (Elem.Attr) | Value | Label (zh) | Label (en) | Type | Unit | Default | Min | Max | Enum options |
|---|---|---|---|---|---|---|---|---|---|---|
| PManuParam | `MC.IsStepMove` | `0` | 加工控制.启用点动步进 | Manu Control.Enable Step Jog | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.IsFastMode` | `1` | 加工控制.启用快速模式 | Manu Control.Enable Fast Mode | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.EnableLoopManu` | `0` | 加工控制.启用循环加工 | Manu Control.Enable Loop Manu | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.IsOnlyManuSelectGraph` | `0` | 加工控制.仅加工选中图形 | Manu Control.Only selected | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.EnableAutoExchange` | `0` |  |  | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.StepLength` | `1` | 运动控制.点动步进长度 | Run Control.Step Jog length | double | mm | 10 | 0.01 | 9999.0 |  |
| PManuParam | `MC.JogFastSpeed` | `200` | 运动控制.点动快速速度 | Run Control.Jog fast speed | double | mm/s | 200 | 0.01 | 2000.0 |  |
| PManuParam | `MC.JogSlowSpeed` | `50` | 运动控制.点动慢速速度 | Run Control.Jog slow speed | double | mm/s | 50 | 0.01 | 100.0 |  |
| PManuParam | `MC.MarkPtIndex` | `0` | 运动控制.标记点序号 | Run Control.Mark Point Index | enum |  | 0 | 0.0 | 5.0 | 0=Mark P1 (标记点1); 1=Mark P2 (标记点2); 2=Mark P3 (标记点3); 3=Mark P4 (标记点4); 4=Mark P5 (标记点5); 5=Mark P6 (标记点6) |
| PManuParam | `MC.ForwardBackwardLength` | `10` | 加工控制.暂停进退距离 | Manu Control.Pause BF length | double | mm | 10 | 0.01 | 9999.0 |  |
| PManuParam | `MC.ForwardBackwardSpeed` | `10` | 加工控制.暂停进退速度 | Manu Control.Pause BF speed | double | mm/s | 10 | 0.01 | 2000.0 |  |
| PManuParam | `MC.AutoHomeAfterStop` | `1` | 加工控制.加工中停止后返回停靠点 | Manu Control.Auto Stop return | bool |  | 1 | 0.0 | 1.0 |  |
| PManuParam | `MC.IsReturnAfterManu` | `1` | 加工控制.加工完成后是否返回点 | Manu Control.Done return | bool |  | 1 | 0.0 | 1.0 |  |
| PManuParam | `MC.ReturnPtTypeAfterManu` | `2` | 加工控制.加工完成后返回点类型 | Manu Control.Done return type | enum |  | 0 | 0.0 | 9.0 | 0=Dock Point (停靠点); 1=Start Point (起点); 2=End Point (终点); 3=Origin Point (原点); 4=Mark P1 (标记点1); 5=Mark P2 (标记点2); 6=Mark P3 (标记点3); 7=Mark P4 (标记点4); 8=Mark P5 (标记点5); 9=Mark P6 (标记点6) |
| PManuParam | `MC.BoundSpeed` | `500` | 运动控制.走边框速度 | Run Control.Go Frame Speed | double | mm/s | 100.0 | 0.01 | 500.0 |  |
| PManuParam | `MC.XFastMoveSpeed` | `500` | 运动控制.空走速度 | Run Control.Move Speed | double | mm/s | 100.0 | 0.01 | 500.0 |  |
| PManuParam | `MC.XFastMoveAcc` | `5999.9999999999991` | 运动控制.空走加速度 | Run Control.Move Acc | double | mm/s2 | 4000.0 | 500.0 | 10000.0 |  |
| PManuParam | `MC.EmptyMoveAccTime` | `125` | 运动控制.空走加速时间 | Run Control.Empty Move Acc Time | double | ms | 125 | 60.0 | 250.0 |  |
| PManuParam | `MC.ManuAcc` | `5999.9999999999991` | 运动控制.加工加速度 | Run Control.Cut Acc | double | mm/s2 | 4000.0 | 500.0 | 10000.0 |  |
| PManuParam | `MC.AccTime` | `200` | 运动控制.加工加速时间 | Run Control.Process Acc Time | double | ms | 125 | 60.0 | 250.0 |  |
| PManuParam | `MC.SplineAccuracyRate` | `0.02` | 运动控制.曲线控制精度 | Run Control.Spline Precision | double |  | 0.05 | 0.0 | 0.1 |  |
| PManuParam | `MC.CornerAccuracyRate` | `0.050000000000000003` | 运动控制.拐角控制精度 | Run Control.Corner Precision | double |  | 0.1 | 0.0 | 1.0 |  |
| PManuParam | `MC.LaserPointPower` | `40` | 激光控制.激光点射功率 | Laser Control.Laser Power | int | % | 50 | 0.0 | 100.0 |  |
| PManuParam | `MC.GasType` | `1` | 气体控制.手动气体类型 | Gas Control.Manual Gas type | enum |  | 0 | 0.0 | 5.0 | 0=Low Air (低压空气); 1=Low O2 (低压氧气); 2=Low N2 (低压氮气); 3=High Air (高压空气); 4=High O2 (高压氧气); 5=High N2 (高压氮气) |
| PManuParam | `MC.EnableAutoRollSheet` | `0` |  |  | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.EnableECAxisSoftLimit` | `0` | 扩展板参数.启用第四轴软限位 | Extended Card Parameters.Enable 4th Axis Soft Limit | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.SortIsSmallFirst` | `1` | 排序.小图优先 | Sort.Small Contour First | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.ShowStatusWindow` | `0` | 电动调焦.显示状态窗口 | Auto Focus.Show Status Window | bool |  | 1 | 0.0 | 1.0 |  |
| PManuParam | `MC.EnableRotatePlatform` | `0` |  |  | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.IsZFMoveToSafePosBeforeProcess` | `0` | 运动控制.加工前Z轴到安全位置 | Run Control.Z Move to safe pos befor Process | bool |  | 1 | 0.0 | 1.0 |  |
| PManuParam | `MC.ClearUpFilmNum_Pre` | `0` | 图形工艺控制.批量去膜轮廓数 | Graph Process Control.The number of contour for Removal of film | int |  | 0 | 0.0 | 9999999.0 |  |
| PManuParam | `MC.EnableAdvMicoLink` | `0` | 图形工艺控制.启用无痕微连 | Graph Process Control.Enable non-trace Mico-link | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.AdvMicLinkPower_Pre` | `0` | 图形工艺控制.无痕微连功率百分比 | Graph Process Control.non-trace Mico-link Laser power percent | int |  | 60 | 0.0 | 100.0 |  |
| PManuParam | `MC.EnableSeekBeforWork` | `0` | 加工控制.开始前寻边 | Manu Control.Seek Edge before work | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.PtLaserTime_ms` | `200` |  |  | int |  | 0 | 0.0 | 9999.0 |  |
| PManuParam | `MC.ResumeBackLength` | `2` | 图形工艺控制.继续回退距离 | Graphic process control. Continue to retract distance | double |  | 2 | 0.0 | 20.0 |  |
| PManuParam | `MC.afterContinueDill` | `0` | 图形工艺控制.继续后重新穿孔 | Graphic process control. Re-punch after continuing | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.EnableSeekClearAngle` | `0` | 加工控制.加工完成自动清除寻边角度 | Manu Control.Automatically clear the Angle of finding after processing | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.EnableContourShift` | `0` | 图形偏移.启用图形偏移 | Graph Shift.Enable Graph Shift | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MC.ContourShiftXDist` | `0` | 图形偏移.X方向偏移 | Graph Shift.Graph Shift X Direction Length | double | mm | 0 | -100000.0 | 100000.0 |  |
| PManuParam | `MC.ContourShiftYDist` | `0` | 图形偏移.Y方向偏移 | Graph Shift.Graph Shift Y Direction Length | double | mm | 0 | -100000.0 | 100000.0 |  |
| PManuParam | `MC.RollSheetFileTableStr` | `` |  |  | string |  |  | 0.0 | 0.0 |  |
| PManuParam | `MC.BatchCutFileTableStr` | `` |  |  | string |  |  | 0.0 | 0.0 |  |
| PManuParam | `LC.PtLaserFreq` | `1234` | 激光控制.点射激光频率 | Laser Control.Laser Freq | int | Hz | 1234 | 1.0 | 50000.0 |  |
| PManuParam | `LC.PtLaserPeakCurrent` | `20` | 激光控制.点射峰值功率 | Laser Control.Peak Current | int | % | 100 | 0.0 | 100.0 |  |
| PManuParam | `LC.LaserGateIsAutoInManu` | `1` | 激光控制.加工时自动控制光闸 | Laser Control.Auto Control Shutter when Process | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `GC.DefaultGasPressure` | `3.4500000000000002` | 气体控制.默认气压 | Gas Control.Default Pressure | double | V | 3.45 | 0.0 | 1000.0 |  |
| PManuParam | `GC.GasDelay` | `100` | 气体控制.开气延时 | Gas Control.Gas On Delay | int | ms | 100 | 0.0 | 10000.0 |  |
| PManuParam | `GC.DirectGasDelay` | `100` | 气体控制.首点开气延时 | Gas Control.First Gas On Delay | int | ms | 100 | 0.0 | 1500.0 |  |
| PManuParam | `GC.ChangeGasDelay` | `100` | 气体控制.换气延时 | Gas Control.Gas Change Delay | int | ms | 100 | 0.0 | 1000.0 |  |
| PManuParam | `FC.ShortNoUpMaxLength` | `10` | 跟随控制.短距不上抬距离 | Follow Control.Short Move Unlift Length | int | mm | 10 | 0.0 | 1000.0 |  |
| PManuParam | `FC.EnableLeapFrogUp` | `1` | 跟随控制.使用蛙跳式上抬 | Follow Control.Enable Frog Style Up | bool |  | 1 | 0.0 | 1.0 |  |
| PManuParam | `FC.EnableEmtptMoveFollow` | `0` | 跟随控制.空走时启用跟随 | Follow Control.Enable Follow when Dry Cut | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MS.AutoClearManuLine` | `1` | 加工指示.加工后自动清除轨迹线 | Process.Clear track line after process done | bool |  | 1 | 0.0 | 1.0 |  |
| PManuParam | `MS.EnableSoftLimit` | `0` | 加工指示.启用软限位 | Process.Enable software limit | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `GP.IsAutoCheckGraphOrder` | `1` | 图形工艺控制.加工前自动区分内外模 | Graph Process Control.Auto distinguish inner outer before process | bool |  | 1 | 0.0 | 1.0 |  |
| PManuParam | `GP.EnableMicroLinkDecc` | `0` | 图形工艺控制.启用微连减速模式 | Graph Process Control.Enable MicroLink Deceleration | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `UN.SpeedUnit` | `1` | 单位.速度单位 | Unit.Speed Unit | enum |  | 0 | 0.0 | 3.0 | 0=mm/s; 1=m/min (米/分); 2=inch/s; 3=inch/min |
| PManuParam | `UN.AccUnit` | `1` | 单位.加速度单位 | Unit.Acc Unit | enum |  | 0 | 0.0 | 2.0 | 0=mm/s2; 1=G; 2=inch/s2 |
| PManuParam | `UN.GasPressureUnit` | `0` | 单位.气压单位 | Unit.Gas Pressure Unit | enum |  | 0 | 0.0 | 1.0 | 0=bar; 1=MPa |
| PManuParam | `MSC.RecenFileIndex` | `0` | 杂项.最近文件序号 | Misc.Recent file index | int |  | 0 | 0.0 | 10.0 |  |
| PManuParam | `MSC.LatestFilePath` | `L&quot;&quot;` | 杂项.最近打开文件 | Misc.Recent open files | string |  | L"" | 0.0 | 0.0 |  |
| PManuParam | `MSC.TestCardID` | `0` | 杂项.最近文件序号 | Misc.Recent file index | string |  | 0 | 0.0 | 0.0 |  |
| PManuParam | `MSC.TestPersonID` | `L&quot;&quot;` | 杂项.最近打开文件 | Misc.Recent open files | string |  | L"" | 0.0 | 0.0 |  |
| PManuParam | `MSC.MarkPtStr` | `` | 杂项.标记点 | Misc.Mark Points | string |  |  | 0.0 | 0.0 |  |
| PManuParam | `MSC.DockPtType` | `7` | 杂项.标记点 | Misc.Mark Points | int |  | 7 | 0.0 | 10.0 |  |
| PManuParam | `MSC.ToolCoordPtStr` | `` | 杂项.标记点 | Misc.Mark Points | string |  |  | 0.0 | 0.0 |  |
| PManuParam | `MSC.CoordSysType` | `0` | 运动控制.坐标系类型 | Run Control.Coord System Type | enum |  | 0 | 0.0 | 7.0 | 0=Float (浮动); 1=Tool 1 (工件1); 2=Tool 2 (工件2); 3=Tool 3 (工件3); 4=Tool 4 (工件4); 5=Tool 5 (工件5); 6=Tool 6 (工件6); 7=Manual (手动) |
| PManuParam | `MP.EnableGasPWarning` | `0` | 电动调焦头.启用气压预警 | AF Parameters.Enable Gas Pressure Warning | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `MP.EnableJogManu` | `0` | 点动控制.启用点动切割 | Jog Parameters.Enable Jog Manu | bool |  | 0 | 0.0 | 1.0 |  |
| PManuParam | `LMP.LoopCount` | `9999` | 循环加工.循环次数 | Loop Process.Loop Times | int |  | 10 | 1.0 | 99999.0 |  |
| PManuParam | `LMP.LoopInterval` | `1` | 循环加工.循环间隔 | Loop Process.Loop Interval | double |  | 1 | 0.1 | 9999.0 |  |
| PManuParam | `LMP.LoopType` | `0` | 循环加工.循环类型 | Loop Process.Loop Type | enum |  | 0 | 0.0 | 1.0 | 0=Dry Cut (空走); 1=Process (加工) |
| PSoftParam | `GP.MicoLinkSlowDownVel` | `10` | 图形工艺控制.微连减速速度 | Graph Process Control.Mico-link Slow down Velcity | double | mm/s | 10 | 1.0 | 500.0 |  |
| PSoftParam | `GP.IsDrillInMicoLink` | `0` | 图形工艺控制.微连处重新穿孔 | Graph Process Control.Re-drill in Mico-link position | bool |  | 0 | 0.0 | 1.0 |  |
| PSoftParam | `GP.CoolPostionDelay` | `0` | 图形工艺控制.冷却点延时 | Graphic process control. Cooling point delay | int | ms | 0 | 0.0 | 80000.0 |  |
| PSoftParam | `GP.PreDrillMaxNum` | `5` | 图形工艺控制.预穿孔最大数目 | Graph Process Control.Pre Drill Max Num | int |  | 5 | 1.0 | 1000.0 |  |
| PSoftParam | `SOP.LastLoadFilePath` | `C:\Users\Administrator\Desktop\222.chf` | 软件.开机自动打开上次图形 | Software.Auto Load Last Graph | string |  |  | 0.0 | 0.0 |  |
| PSoftParam | `SOP.ManuCount` | `0` | 系统参数.已完成加工件数 | System Parameters.Finished Count | int |  | 0 | 0.0 | 99999999.0 |  |
| PSoftParam | `SOP.PlanManuCount` | `100` | 系统参数.计划加工件数 | System Parameters.Plan Count | int |  | 100 | 0.0 | 99999999.0 |  |
| PSoftParam | `SOP.TotalManuCount` | `6115` | 系统参数.已完成加工件数 | System Parameters.Finished Count | int |  | 0 | 0.0 | 99999999.0 |  |
| PSoftParam | `SOP.AfterPlanFinishedActionType` | `0` | 系统参数.计划完成后动作 | System Parameters.After Finished Action | enum |  | 0 | 0.0 | 2.0 | 0=No Action (不做任何动作); 1=Prompt System Message Box (弹出对话框提示); 2=Prohibit Further Processing (禁止继续加工) |
| PSoftParam | `SOP.DeviceSoftwareTotalUseTime` | `-45.618105279627308` | 运行报告.累计开机时间 | Device Report.Software Total Use Time | double |  | 0 | 0.0 | 99999999.0 |  |
| PSoftParam | `SOP.DeviceControllerTotalUseTime` | `142.68301165285453` | 运行报告.控制卡累计通讯时间 | Device Report.Controller Total Use Time | double |  | 0 | 0.0 | 99999999.0 |  |
| PSoftParam | `SOP.DeviceTotalManuTime` | `7.0444947222222192` | 运行报告.累计加工时间 | Device Report.Device Total Processing Time | double |  | 0 | 0.0 | 99999999.0 |  |
| PSoftParam | `SOP.DeviceLastManuTime` | `0.0060508333333333334` | 运行报告.前次加工时间 | Device Report.Device Last Processing Time | double |  | 0 | 0.0 | 99999999.0 |  |
| PSoftParam | `SOP.DeviceTotalLaserOnTime` | `10.163611111111107` | 运行报告.累计出光时间 | Device Report.Device Total Laser On Time | double |  | 0 | 0.0 | 99999999.0 |  |
| PSoftParam | `SOP.DeviceTotalManuCount` | `6029` | 运行报告.累计加工次数 | Device Report.Device Total Processing Count | int |  | 0 | 0.0 | 99999999.0 |  |
| PSoftParam | `SOP.DeviceXAxisTotalMoveLength` | `12986.53200000001` | 运行报告.X轴累计行程 | Device Report.X Axis Total Move Length | double |  | 0 | 0.0 | 99999999.0 |  |
| PSoftParam | `SOP.DeviceYAxisTotalMoveLength` | `8174.1090000000013` | 运行报告.Y轴累计行程 | Device Report.Y Axis Total Move Length | double |  | 0 | 0.0 | 99999999.0 |  |
| PSoftParam | `SOP.DeviceZAxisTotalMoveLength` | `0` | 运行报告.Z轴累计行程 | Device Report.Z Axis Total Move Length | double |  | 0 | 0.0 | 99999999.0 |  |
| PSoftParam | `SP.InterfereAxisIndex` | `0` | 运动.轴类型 | Motion.Axis Type | enum |  | 0 | 0.0 | 1.0 | 0=X Axis (X轴); 1=Y Axis (Y轴) |
| PSoftParam | `SP.InterfereSpeed` | `100` | 运动.运动速度 | Motion.Speed | double |  | 100 | 1.0 | 99999.0 |  |
| PSoftParam | `SP.InterfereLoopTime` | `1` | 运动.循环次数 | Motion.Loop Times | int |  | 1 | 1.0 | 100.0 |  |
| PSoftParam | `SP.InterfereStayTime` | `4` | 运动.停留时间 | Motion.Stay Time | double |  | 4 | 0.0 | 1000.0 |  |
| PSoftParam | `SP.InterfereTotalLength` | `3000` | 干涉仪.行程范围 | Laser Interferometer.Move Range | double |  | 3000 | 1.0 | 20000.0 |  |
| PSoftParam | `SP.InterfereStepLength` | `150` | 干涉仪.间隔距离 | Laser Interferometer.Interval | double |  | 150 | 1.0 | 10000.0 |  |
| PSoftParam | `SP.InterfereIsFirstPtGapAdjust` | `1` | 干涉仪.首点间隙调整 | Laser Interferometer.First Point Gap Adjust | bool |  | 1 | 0.0 | 1.0 |  |
| PSoftParam | `SP.InterfereGapAdjust` | `5` | 干涉仪.间隙调整距离 | Laser Interferometer.Gap Size | double |  | 5 | 0.0 | 1000.0 |  |
| PSoftParam | `SP.Lang` | `1` | 软件.语言(Language) | Software.Language | enum |  | 0 | 0.0 | 10.0 | 0=简体中文; 1=English; 2=русский; 3=Deutsch; 4=Español; 5=Português; 6=French; 7=Italian; 8=Polish; 9=Vietnamese; 10=Turk dili |
| PSoftParam | `SP.Skin` | `0` | 软件.主题 | Software.Theme | enum |  | 0 | 0.0 | 1.0 | 0=White (白色); 1=Black (黑色) |
| PSoftParam | `SP.UIDir` | `0` | 软件.界面方向 | Software.UI Direction | enum |  | 0 | 0.0 | 1.0 | 0=Horizontal (横屏); 1=Vertical (竖屏) |
| PSoftParam | `SP.AlarmPanelColor` | `176` | 软件.告警条颜色 | Software.Alarm Panel Color | color |  | 176 | 0.0 | 16777215.0 |  |
| PSoftParam | `SP.AlarmPanelTextColorColor` | `2` | 软件.告警条文字颜色 | Software.Alarm Text Color | enum |  | 2 | 0.0 | 2.0 | 0=Red (红色); 1=Black (黑色); 2=White (白色) |
| PSoftParam | `SP.EnableGeneralLog` | `1` | 日志.启用标准日志 | Log.Enable General Log | bool |  | 1 | 0.0 | 1.0 |  |
| PSoftParam | `SP.EnableDebugLog` | `0` | 日志.启用调试日志 | Log.Enable Debug Log | bool |  | 0 | 0.0 | 1.0 |  |
| PSoftParam | `SP.DualCheckTime` | `5` | 双驱检测.检测时间 | Dual Servo Check.Check Time | int |  | 5 | 0.0 | 10000.0 |  |
| PSoftParam | `SP.DualCheckMaxErrorValue` | `3` | 双驱检测.双驱最大误差值 | Dual Servo Check.Dual Servo Max Error Value | double |  | 3 | 0.0 | 100.0 |  |
| PSoftParam | `SP.AutoStopCheckWhenValueTooLarge` | `0` | 双驱检测.误差过大时自动停止检测 | Dual Servo Check.Auto Stop Check When Value Too Large | bool |  | 0 | 0.0 | 1.0 |  |
| PSoftParam | `SP.EnableDualFindHome` | `0` | 软件.默认龙门回零 | Software.Default dual find home | bool |  | 0 | 0.0 | 1.0 |  |
| PSoftParam | `SP.SoftwareThemeType` | `1` | 软件.主题 | Software.Theme | enum |  | 1 | 0.0 | 1.0 | 0=DARK; 1=GRAY |
| PSoftParam | `SP.m_iEnableLaserType` | `1` | 激光器 | Laser | int |  | 0 | 0.0 | 1.0 |  |
| PSoftParam | `SP.m_iHardwareModel` | `224` | 激光器 | Laser | int |  | 224 | 224.0 | 300.0 |  |
| PSoftParam | `SP.DrawblkColor` | `0` | 软件.绘图区背景 | Software.Drawing area color | color |  | 0 | 0.0 | 16777215.0 |  |
| PAFParam | `AF.AFPosSpeed` | `10` | 运动控制.焦点运行速度 | Motion control. Focus speed | double |  | 10 | 0.1 | 15.0 |  |
| PAFParam | `AFDA.DAPort` | `0` | 输出端口.调焦用DA端口 | Output Port.Change Focus DA Port | DA port |  | 0 | 0.0 | 2.0 |  |
| PAFParam | `AFDA.EnableMovePort` | `0` | 输出端口.调焦使能 | Output Port.Enable Focus Port | DO port |  | 0 | 0.0 | 26.0 |  |
| PAFParam | `AFDA.FocusEnableDelay` | `200` | 输出端口.调焦使能延时 | Output Port.Focus Enable Delay | int |  | 200 | 0.0 | 10000.0 |  |
| PAFParam | `AFDA.GoOriginPort` | `0` | 输出端口.焦点回原 | Output Port.Focus Go Origin Port | DO port |  | 0 | 0.0 | 26.0 |  |
| PAFParam | `AFDA.FocusGoOriginDelay` | `200` | 输出端口.焦点回原延时 | Output Port.Focus Go Origin Delay | int |  | 200 | 0.0 | 10000.0 |  |
| PAFParam | `AFDA.ExeDonePort` | `0` | 输入端口.调焦完成 | Input Port.Focus Done Port | DI port |  | 0 | 0.0 | 28.0 |  |
| PAFParam | `AFDA.AlarmPort` | `0` | 输入端口.系统报警 | Input Port.System Alarm Port | DI port |  | 0 | 0.0 | 28.0 |  |
| PAFParam | `AFDA.OriginOffset` | `0` | 调焦参数.原点偏移 | Focus Parameters.Origin Offset | double |  | 0 | -100.0 | 100.0 |  |
| PAFParam | `AFDA.AFDAMapStr` | `` | 电动头焦点映射.电动头焦点校正数据 | AF DA Map.AF DA Adjust Data | string |  |  | 0.0 | 0.0 |  |
| PDOParam | `DO.CurrentAdjGasType` | `1` | 气压映射.当前气压校正类型 | Gas Pressure Map.Current Gas Adjust Type | int |  |  | 0.0 | 2.0 |  |
| PZFParam | `ZF.ZFFollowSpeed` | `100` | 调高器运行参数.运行速度 | ZF Run Property.Run Speed | double |  | 200 | 0.1 | 9999.0 |  |
| PZFParam | `ZF.ZFJogSpeed` | `10` | 调高器运行参数.点动速度 | ZF Run Property.Jog Speed | double |  | 10 | 0.1 | 9999.0 |  |
| PZFParam | `ZF.ZFFastJogSpeed` | `50` | 调高器运行参数.点动速度 | ZF Run Property.Jog Speed | double |  | 50 | 0.1 | 9999.0 |  |
| PZFParam | `ZF.ZFDockHeight` | `20` | 调高器运行参数.停靠高度 | ZF Run Property.Dock Height | double |  | 20 | 0.1 | 9999.0 |  |
| PZFParam | `ZF.ZFUpSpeed` | `100` | 调高器运行参数.上抬速度 | ZF Run Property.Up Speed | double |  | 300 | 0.1 | 9999.0 |  |
| PZFParam | `ZF.ZFOriginSoftLimitIndex` | `0` | 调高器运行参数.初始软限位序号 | ZF Run Property.Origin Soft Limit Index | enum |  | 0 | 0.0 | 1.0 |  |
| PZFParam | `ZF.ZFSignalCorrectionPeriod` | `30` | 飞行切割.方飞切坐标检测容差(um) | Flying cutting. Square flying cutting coordinate detection tolerance (um) | double |  | 30 | 1.0 | 180.0 |  |
| PECParam | `EC.ECStepLength` | `1` |  |  | double |  | 1 | 0.0 | 999999.0 |  |
| PECParam | `EC.ECFastSpeed` | `30` |  |  | double |  | 30 | 0.0 | 999999.0 |  |
| PECParam | `EC.ECJogIsStepMode` | `0` |  |  | bool |  | 0 | 0.0 | 1.0 |  |
| PECParam | `ZF.zfSoftLimitEnable` | `0` | 启用软限位 | Enable Software Limit | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.GuideLineType` | `2` | 引线参数.类型 | Lead Line.Type | enum |  | 1 | 0.0 | 3.0 | 0=None (无); 1=Line (直线); 2=Arc (圆弧); 3=Line+Arc (直线+圆弧) |
| PGraphParam | `GRP.GuideLineAngle` | `90` | 引线参数.角度 | Lead Line.Angle | double |  | 90 | 0.0 | 90.0 |  |
| PGraphParam | `GRP.GuideLineLength` | `8` | 引线参数.长度 | Lead Line.Length | double | mm | 3 | 0.01 | 1000.0 |  |
| PGraphParam | `GRP.GuideArcRadius` | `2` | 引线参数.半径 | Lead Line.Radius   | double | mm | 2 | 0.01 | 30.0 |  |
| PGraphParam | `GRP.LeadPosType` | `0` | 引线参数.起点选择模式 | Lead Line.Start Point Mode | enum |  | 0 | 0.0 | 3.0 | 0=Auto Start Point(Long Side First) (自动选择起点位置(长边优先)); 1=Auto Start Point(Vertex First) (自动选择起点位置(顶点优先)); 2=Unified Length Percent (统一设置长度位置百分比); 3=Not Change Start Point (不变起点位置) |
| PGraphParam | `GRP.LeadPosPrecent` | `0` | 引线参数.起点位置 | Lead Line.Start Point Position | int |  | 0 | 0.0 | 100.0 |  |
| PGraphParam | `GRP.IsOnlyForEncolseContour` | `1` | 杂项.只对封闭图形有效 | Lead Line.Only for closed graphics | bool |  | 1 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.IsOnlyForSelectContour` | `0` | 杂项.只对选中图形有效 | Lead Line.Only for selected graphics | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.OutsideIsNegativeSide` | `0` | 杂项.最外层为阴切 | Lead Line.Outer layer is inside cut  | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.LoopGapOverCutLength` | `0` | 缺口封口.缺口大小 | Gap Seal.Gap Length | double | mm | 2 | 0.0 | 1000.0 |  |
| PGraphParam | `GRP.MicroLinkLength` | `0.5` | 手动微连.微连长度 | Manual Micro Joint.Micro Joint Length | double | mm | 2 | 0.01 | 1000.0 |  |
| PGraphParam | `GRP.IsShowEnvelopBox` | `0` | 视图参数.显示图形外框 | View Parameters.Show box for graphics | bool | mm | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.IsShowEncolseWithRed` | `0` | 视图参数.红色显示不封闭图形 | View Parameters.Show unclosed curve as red | bool | mm | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.IsShowNO` | `0` | 视图参数.显示序号 | View Parameters.Show index | bool | mm | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.IsShowStartPt` | `1` | 视图参数.显示路径起点 | View Parameters.Show path start | bool | mm | 1 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.IsShowDirArrow` | `1` | 视图参数.箭头显示加工路径 | View Parameters.Show path direction | bool | mm | 1 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.IsShowLinkerLine` | `0` | 视图参数.显示空走路径 | View Parameters.Show move path | bool | mm | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.ArrayRowNum` | `8` | 阵列参数.行数 | Array Parameters.Row Number | int |  | 3 | 1.0 | 10000.0 |  |
| PGraphParam | `GRP.ArrayColNum` | `3` | 阵列参数.列数 | Array Parameters.Column Number | int |  | 3 | 1.0 | 10000.0 |  |
| PGraphParam | `GRP.OffsetType` | `0` | 阵列参数.偏移类型 | Array Parameters.Offset Type | enum |  | 1 | 0.0 | 1.0 | 0=Offset (偏移); 1=Spacing (间距) |
| PGraphParam | `GRP.RowGapVct` | `1` | 阵列参数.行偏移（间距） | Array Parameters.Row Offset (Spacing) | double |  | 10 | 0.0 | 10000.0 |  |
| PGraphParam | `GRP.ColGapVct` | `3` | 阵列参数.列偏移（间距） | Array Parameters.Column Offset (Spacing) | double |  | 10 | 0.0 | 10000.0 |  |
| PGraphParam | `GRP.RowDir` | `1` | 阵列参数.行方向 | Array Parameters.Row Direction | enum |  | 1 | 0.0 | 1.0 | 0=Down (向下); 1=Up (向上) |
| PGraphParam | `GRP.ColDir` | `1` | 阵列参数.列方向 | Array Parameters.Column Direction | enum |  | 1 | 0.0 | 1.0 | 0=Left (向左); 1=Right (向右) |
| PGraphParam | `GRP.RotateAngle_deg` | `30` | 旋转参数.旋转角度 | Rotate Parameters.Rotate Angle | int |  | 30 | -180.0 | 180.0 |  |
| PGraphParam | `GRP.AutoMicroLinkType` | `0` | 自动微连.微连方式 | Auto Micro Joint.Micro Joint Type | enum | mm | 0 | 0.0 | 1.0 | 0=Micro Joint by Numbers (按数量微连); 1=Micro Joint by Spacing (按间隔距离微连) |
| PGraphParam | `GRP.AutoMicroLinkNum` | `2` | 自动微连.微连数量 | Auto Micro Joint.Micro Joint Number | int |  | 2 | 1.0 | 1000.0 |  |
| PGraphParam | `GRP.AutoMicroLinkStep` | `50` | 自动微连.微连间距 | Auto Micro Joint.Micro Joint Distance | int | mm | 50 | 1.0 | 10000.0 |  |
| PGraphParam | `GRP.AutoMicoLinkDir` | `0` | 自动微连.微连方向 | Auto Micro Joint.Micro Joint direction | enum | mm | 0 | 0.0 | 2.0 | 0=Micro Joint follow path (沿路径微连); 1=Micro Joint By X-Dir (仅X方向微连); 2=Micro Joint by Y-Dir (仅Y方向微连) |
| PGraphParam | `GRP.MinEnableMicoLinkGraphSize` | `5` | 自动微连.最小可微连图形大小 | Auto Micro Joint.Min-size of mico joint graph | double |  | 5 | 0.0 | 999999.0 |  |
| PGraphParam | `GRP.IsMicoLinkEncloseGraphHead` | `0` | 杂项.非封闭图起点处微连 | Misc.Micro Joint at the head of the non-closed graph | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.BridgeWidth` | `2` | 桥接参数.桥接宽度 | Bridge Parameters.Bridge Width | double | mm | 2 | 0.01 | 1000.0 |  |
| PGraphParam | `GRP.SortType` | `4` | 排序参数.排序策略 | Sort Parameters.Sort Type | enum |  | 4 | 0.0 | 7.0 | 0=Left to right (从左到右); 1=Right to left (从右到左); 2=Bottom to top  (从下到上); 3=Top to bottom (从上到下); 4=Nearest (局部最短路径); 5=Inside to Outside (从内到外); 6=Outside to Inside (从外到内); 7=Small graphics priority (小图优先) |
| PGraphParam | `GRP.SortIsSmallFirst` | `0` | 排序参数.小图优先 | Sort Parameters.Small Graphics Priority | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.OneKeySort` | `1` | 一键规划步骤.4-排序 | OneKey Planning Step.4-Sort | bool |  | 1 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.OneKeyGuideLine` | `1` | 一键规划步骤.5-引线 | OneKey Planning Step.5-Lead Line | bool |  | 1 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.OneKeyMicroLink` | `1` | 一键规划步骤.6-微连 | OneKey Planning Step.6-Micro Joint | bool |  | 1 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.SmoothAccuracy` | `0.050000000000000003` | 自动平滑.曲线平滑精度 | Auto Smooth.Smooth Precision | double | mm | 0.05 | 0.001 | 100.0 |  |
| PGraphParam | `GRP.OffsetDist` | `0.10000000000000001` | 割缝补偿参数.补偿距离 | Compensate Parameters.Compensate Width | double | mm | 0.1 | 0.01 | 1.0 |  |
| PGraphParam | `GRP.ArcRoundRadius` | `1` | 倒圆角.半径 | Arc round.Radius | double | mm | 1.0 | 0.1 | 10.0 |  |
| PGraphParam | `GRP.ArcRoundMinAngle_deg` | `0` | 倒圆角.最小角度 | Arc round.Min angle | double | ° | 0.0 | 0.0 | 180.0 |  |
| PGraphParam | `GRP.ArcRoundMaxAngle_deg` | `90` | 倒圆角.最大角度 | Arc round.Max angle | double | ° | 90.0 | 0.0 | 180.0 |  |
| PGraphParam | `GRP.UnloadAngleRadius` | `0.5` | 释放角.半径 | Unload angle.Radius | double | mm | 0.5 | 0.1 | 10.0 |  |
| PGraphParam | `GRP.IsLeadPtCool` | `0` | 冷却点.引入点冷却 | Cool Point.Cool Lead Position | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.IsPeekPtCool` | `0` | 冷却点.尖角点冷却 | Cool Point.Cool Sharp Position | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.PeekAngle_deg` | `90` | 冷却点.最大尖角角度 | Cool Point.Max Sharp Angle | double | ° | 90.0 | 1.0 | 170.0 |  |
| PGraphParam | `GRP.AlphaMaxAngle_deg` | `90` | 倒α角.α最大角度 | Round α Angle.Max Angle | double | ° | 90.0 | 1.0 | 170.0 |  |
| PGraphParam | `GRP.AlphaMinEdgeLen` | `3` | 倒α角.最小边长 | Round α Angle.Min edge length | double | ° | 3 | 1.0 | 50.0 |  |
| PGraphParam | `GRP.AlphaLen` | `8` | 倒α角.外引长度 | Round α Angle.Outside lead length | double | ° | 8.0 | 0.5 | 10.0 |  |
| PGraphParam | `GRP.AlphaType` | `0` | 倒α角.方式 | Round α Angle.Type | enum |  | 0 | 0.0 | 4.0 | 0=Auto (自动); 1=Outside α (外部α); 2=Inner α (内部α); 3=Both sde α (内外都α) |
| PGraphParam | `GRP.CircleFlySortType` | `0` | 圆飞切排序.排序方式 | Circle Fly Sort.Sort type | enum |  | 0 | 0.0 | 5.0 | 0=Not Sort (不排序); 1=Left to Right (左到右); 2=Right to Left (右到左); 3=Down to Up (下到上); 4=Up to Down (上到下); 5=Local Optimal (局部最短) |
| PGraphParam | `GRP.CircleFlyMaxLen` | `80` | 圆飞行参数.最大飞行线长度 | Circle Fly Param.Max length of fly | double | mm | 80 | 5.0 | 1001.0 |  |
| PGraphParam | `GRP.FirstDoneSingleContourCircleScan` | `1` | 圆飞行参数.优先飞切单个轮廓内的圆 | Circle Fly Param.First Done Single Contour Circle | bool |  | 1 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.LineFlyStartPos` | `0` | 直线飞切.起刀位置 | Line Fly.Corner of start | enum |  | 0 | 0.0 | 3.0 | 0=Left-down Corner (左下); 1=Right-down Corner (右下); 2=Left-up Corner (左上); 3=Right-up Corner (右上) |
| PGraphParam | `GRP.LineFlyCollineTol` | `1` | 参数.允许偏差距离 | Parameter.Max tolerance of collinear | double | mm | 1 | 0.01 | 30.0 |  |
| PGraphParam | `GRP.LineFlyMaxLinkLen` | `80` | 参数.最大光滑连接距离 | Parameter.Max length of smooth linker | double | mm | 80 | 0.1 | 1000.0 |  |
| PGraphParam | `GRP.LineFlyMaxLen` | `40` | 参数.最大飞行线长度 | Parameter.Max length of fly | double | mm | 40 | 0.1 | 1000.0 |  |
| PGraphParam | `GRP.FillGlyDir` | `0` | 填充圆.填充方式 | Fill circle.Fill Type | enum |  | 0 | 0.0 | 2.0 | 0=X-orientation (X方向); 1=Y-orientation (Y方向); 2=along edge (环边) |
| PGraphParam | `GRP.FillCircleRadius` | `4` | 填充圆.半径 | Fill circle.Raduis | double | mm | 4 | 0.1 | 500.0 |  |
| PGraphParam | `GRP.FillCircleStock` | `10` | 填充圆.边距 | Fill circle.Stock | double | mm | 10 | 0.1 | 300.0 |  |
| PGraphParam | `GRP.FillCircleSpace` | `10` | 填充圆.间距 | Fill circle.Space | double | mm | 10 | 0.1 | 300.0 |  |
| PGraphParam | `GRP.MaxSizeErrorType` | `0` | 自动补偿.最大尺寸偏差类型 | Auto Offset.Max Size Error Type | enum |  | 0 | 0.0 | 1.0 | 0=Too Large (偏大); 1=Too Small (偏小) |
| PGraphParam | `GRP.MaxSizeError` | `0` | 自动补偿.最大尺寸偏差值 | Auto Offset.Max Size Error | double | mm | 0 | -1000.0 | 1000.0 |  |
| PGraphParam | `GRP.MinSizeErrorType` | `0` | 自动补偿.最小尺寸偏差类型 | Auto Offset.Min Size Error Type | enum |  | 0 | 0.0 | 1.0 | 0=Too Large (偏大); 1=Too Small (偏小) |
| PGraphParam | `GRP.MinSizeError` | `0` | 自动补偿.最小尺寸偏差值 | Auto Offset.Min Size Error | double | mm | 0 | -1000.0 | 1000.0 |  |
| PGraphParam | `GRP.AddPartNum` | `1` | 排样参数.零件数量 | Nest Parameter.Part Number | int |  | 1 | 1.0 | 10000.0 |  |
| PGraphParam | `GRP.StdSheetHeight` | `2400` | 板材参数.板材高度 | Sheet Parameter.Sheet Height | double |  | 2400 | 1.0 | 100000.0 |  |
| PGraphParam | `GRP.StdSheetWidth` | `1200` | 板材参数.板材宽度 | Sheet Parameter.Sheet Width | double |  | 1200 | 1.0 | 100000.0 |  |
| PGraphParam | `GRP.AddSheetNum` | `1` | 板材参数.板材数量 | Sheet Parameter.Sheet Number | int |  | 1 | 1.0 | 10000.0 |  |
| PGraphParam | `GRP.OverlapGate` | `0.01` | 重复线.重复线检测精度 | Duplicate Lines.Duplicate Limit | double | mm | 0.1 | 0.01 | 0.1 |  |
| PGraphParam | `GRP.ManualConnectGate` | `0.10000000000000001` | 相连线.相连线检测精度 | Combine Near Lines.Auto Combine Precision | double | mm | 0.1 | 0.01 | 100.0 |  |
| PGraphParam | `GRP.BallArmGuideLineLength` | `100` | 球杆仪.引导线长度 | Ball Arm.Guide Line Length | double |  | 100 | 0.0 | 9999.0 |  |
| PGraphParam | `GRP.BallArmGuideLineDir` | `0` | 球杆仪.引导线方向 | Ball Arm.Guide Line Direction | enum |  | 0 | 0.0 | 1.0 | 0=Horizontal (水平); 1=Vertical (垂直) |
| PGraphParam | `GRP.BallArmCircleRadius` | `50` | 球杆仪.圆半径 | Ball Arm.Circle Radius | double |  | 50 | 0.01 | 9999.0 |  |
| PGraphParam | `GRP.BallArmCircleTimes` | `2` | 球杆仪.画圆次数 | Ball Arm.Circle Times | int |  | 2 | 1.0 | 9999.0 |  |
| PGraphParam | `GRP.BallArmCircleDir` | `0` | 球杆仪.画圆方向 | Ball Arm.Circle Direction | enum |  | 0 | 0.0 | 1.0 | 0=Clockwise (顺时针); 1=AntiClockwise (逆时针) |
| PGraphParam | `GRP.AdvTextType` | `0` | 精品字参数.类型 | Adv Text.Adv Text Type | enum |  | 0 | 0.0 | 1.0 | 0=All shrink (全部内缩); 1=All expand (全部外扩) |
| PGraphParam | `GRP.AdvTextOffset` | `0` | 精品字参数.补偿距离 | Adv Text.Offset Length | double | mm | 0 | 0.0 | 1000.0 |  |
| PGraphParam | `GRP.BrushUpZVal` | `50` | 清洁喷嘴.刷头上表面Z值 | Clean Header.Brush Up Z | double | mm | 50 | 0.0 | 1000.0 |  |
| PGraphParam | `GRP.BrushDiveHeight` | `5` | 清洁喷嘴.喷嘴吃刷头深度 | Clean Header.Brush Dive Height | double | mm | 5 | 0.0 | 1000.0 |  |
| PGraphParam | `GRP.BeforeCleanZGoOrigin` | `1` | 清洁喷嘴.清洁前Z轴先回原 | Clean Header.Z Go Origin First Before Clean | bool | mm | 1 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.CleanStartXPos` | `0` | 清洁喷嘴.清洁喷嘴起点X值 | Clean Header.Clean Start X Position | double | mm | 0 | -100000.0 | 100000.0 |  |
| PGraphParam | `GRP.CleanStartYPos` | `0` | 清洁喷嘴.清洁喷嘴起点Y值 | Clean Header.Clean Start Y Position | double | mm | 0 | -100000.0 | 100000.0 |  |
| PGraphParam | `GRP.CleanMoveSpeed` | `10` | 清洁喷嘴.运动速度 | Clean Header.Move Speed | double | mm | 10 | 0.0 | 99999999.0 |  |
| PGraphParam | `GRP.CleanMoveDirection` | `0` | 清洁喷嘴.运动方向 | Clean Header.Move Direction | enum |  | 0 | 0.0 | 1.0 | 0=Clean Header.X Dir (X方向); 1=Clean Header.Y Dir (Y方向) |
| PGraphParam | `GRP.CleanMoveLength` | `10` | 清洁喷嘴.运动长度 | Clean Header.Move Length | double | mm | 10 | 0.0 | 10000.0 |  |
| PGraphParam | `GRP.CleanMoveTimes` | `1` | 清洁喷嘴.运动往返次数 | Clean Header.Move Times | int |  | 1 | 1.0 | 99999999.0 |  |
| PGraphParam | `GRP.AfterCleanReturnOriginPt` | `0` | 清洁喷嘴.清洁后返回原始点 | Clean Header.After Clean Return Origin Point | bool | mm | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.CloseZFTouchSheetWarning` | `1` | 清洁喷嘴.清洁中关闭调高器碰板告警 | Clean Header.Close ZF Touch Sheet Warning In Process | bool |  | 1 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.EdgeSeekDownSpeed` | `100` | 调高器.快下速度 | Z Follower.Fast Down Speed | double |  | 100 | 0.0 | 10000000.0 |  |
| PGraphParam | `GRP.EdgeSeekSensitivity` | `20` | 调高器.跟随灵敏度 | Z Follower.Sensor Sensitivity | int |  | 20 | 1.0 | 30.0 |  |
| PGraphParam | `GRP.EdgeSeekFollowHeight` | `2` | 调高器.跟随高度 | Z Follower.Follow Height | double |  | 2 | 0.2 | 5.0 |  |
| PGraphParam | `GRP.EdgeSeekUpHeight` | `30` | 调高器.移出抬起高度 | Z Follower.Move Out Up Height | double |  | 30 | 10.0 | 999.0 |  |
| PGraphParam | `GRP.FastEdgeSeekMoveOutTolerance` | `2` | 调高器.快速移出检测容差 | Z Follower.Fast Move Out Check Tolerance | double |  | 2 | 0.1 | 10.0 |  |
| PGraphParam | `GRP.SlowEdgeSeekMoveOutTolerance` | `0.40000000000000002` | 调高器.阶跃高度 | FTC.Step height | double |  | 0.4 | 0.2 | 1.0 |  |
| PGraphParam | `GRP.EdgeSeekXYFastSpeed` | `200` | 运动轴.快移速度 | Normal Axis.Fast Move Speed | double |  | 200 | 100.0 | 999.0 |  |
| PGraphParam | `GRP.EdgeSeekXYSlowSpeed` | `50` | 运动轴.寻边速度 | Motion axis.Edge finding speed | double |  | 50 | 25.0 | 200.0 |  |
| PGraphParam | `GRP.EdgeSeekXYPointDist` | `100` | 运动轴.检测点间距 | Normal Axis.Check Point Distance | double |  | 100 | 0.0 | 10000000.0 |  |
| PGraphParam | `GRP.EdgeOffsetX` | `5` | 运动轴.X留边距离 | Normal Axis.X Edge intersection compensates distance | double |  | 5 | -1000.0 | 1000.0 |  |
| PGraphParam | `GRP.EdgeOffsetY` | `5` | 运动轴.Y留边距离 | Normal Axis.Y Edge intersection compensates distance | double |  | 5 | -1000.0 | 1000.0 |  |
| PGraphParam | `GRP.EnableFastEdgeSeekMode` | `0` | 总体.启用快速巡边模式 | General.Enable Fast Edge Seek Mode | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.FastEdgeSeekMoveOutCheckTime` | `1` | 调高器.快速移出检测时间 | Z Follower.Fast Move Out Check Time | int |  | 1 | 0.0 | 10000000.0 |  |
| PGraphParam | `GRP.SlowEdgeSeekMoveOutCheckTime` | `20` |  |  | int |  | 20 | 0.0 | 10000000.0 |  |
| PGraphParam | `GRP.EdgeSeekMoveInDestHeight` | `5` | 调高器.移入目标高度 | Z Follower.Move In Destination Height | double |  | 5 | 0.0 | 10000000.0 |  |
| PGraphParam | `GRP.EdgeSeekXYMoveDir` | `0` | 运动轴.运动方向 | Normal Axis.Move Direction | enum |  | 0 | 0.0 | 1.0 | 0=Clean Header.X Negative Dir (X负方向); 1=Clean Header.X Positive Dir (X正方向) |
| PGraphParam | `GRP.EdgeSeekSafeUpHeight` | `20` | 调高器.安全抬起高度 | Z Follower.Safe Up Height | double |  | 20 | 0.0 | 10000000.0 |  |
| PGraphParam | `GRP.DualServoCalibMaxLength` | `20` | 龙门位置标定.双驱轴最大检测距离 | Dual Servo Calib.Dual Servo Calib Max Length | double |  | 20.0 | -100.0 | 100.0 |  |
| PGraphParam | `GRP.DualServoCalibAdjustOffset` | `2` | 龙门位置标定.双驱轴校准偏移 | Dual Servo Calib.Dual Servo Adjust Offset | double |  | 2.0 | -100.0 | 100.0 |  |
| PGraphParam | `GRP.DualServoCalibAdjustTolerance` | `5` | 龙门位置标定.双驱轴修正门限 | Dual Servo Calib.Dual Servo Adjust Tolerance | double |  | 5.0 | 0.0 | 100.0 |  |
| PGraphParam | `GRP.IsAutoStartAfterRotatePlatform` | `0` | 总体.切换工位后自动开始加工 | General.Auto Start After Platform Change | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.RotatePlatformDelay` | `0` | 总体.旋转后延时 | General.Delay After Rotate | int |  | 0 | 0.0 | 99999999.0 |  |
| PGraphParam | `GRP.RotatePlatformManuPtNum0` | `3` | 旋转工位1.加工点位数量 | Rotate Platform 1.Process Points Number | int |  | 3 | 1.0 | 99999999.0 |  |
| PGraphParam | `GRP.RotatePlatformManuPtRotateSpeed0` | `100` | 旋转工位1.点位旋转速度 | Rotate Platform 1.Points Rotate Speed | double |  | 100.0 | 0.0 | 100000.0 |  |
| PGraphParam | `GRP.RotatePlatformManuPtStepLength0` | `10` | 旋转工位1.点位旋转步长 | Rotate Platform 1.Points Rotate Step Length | double |  | 10.0 | -100000.0 | 100000.0 |  |
| PGraphParam | `GRP.RotatePlatformManuPtNum1` | `3` | 旋转工位2.加工点位数量 | Rotate Platform 2.Process Points Number | int |  | 3 | 1.0 | 99999999.0 |  |
| PGraphParam | `GRP.RotatePlatformManuPtRotateSpeed1` | `100` | 旋转工位2.点位旋转速度 | Rotate Platform 2.Points Rotate Speed | double |  | 100.0 | 0.0 | 100000.0 |  |
| PGraphParam | `GRP.RotatePlatformManuPtStepLength1` | `10` | 旋转工位2.点位旋转步长 | Rotate Platform 2.Points Rotate Step Length | double |  | 10.0 | -100000.0 | 100000.0 |  |
| PGraphParam | `GRP.AfterCleanZFAutoCalib` | `0` | 调高器标定.清洁完成后调高器自动标定 | ZF Calibration.After Clean ZF Auto Calib | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.AfterCleanZFCalibXPos` | `0` | 调高器标定.自动标定点X值 | ZF Calibration.Auto Calib X Position | double | mm | 0 | -100000.0 | 100000.0 |  |
| PGraphParam | `GRP.AfterCleanZFCalibYPos` | `0` | 调高器标定.自动标定点Y值 | ZF Calibration.Auto Calib Y Position | double | mm | 0 | -100000.0 | 100000.0 |  |
| PGraphParam | `GRP.EnableUnlimitRollSheet` | `0` | 无限卷料.是否启用无限卷料 | Unlimit Roll Sheet.Enable Unlimit Roll Sheet Mode | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.EnableZFGoOriginBeforeRollSheet` | `0` | 无限卷料.卷料前调高器强制回原 | Unlimit Roll Sheet.ZF Go Origin Before Roll Sheet | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.PerRollSheetOffset` | `100` | 无限卷料.每次卷料补偿距离 | Unlimit Roll Sheet.Roll Sheet Length Offset | double | mm | 100 | -100000.0 | 100000.0 |  |
| PGraphParam | `GRP.BeforeManuDockPtX` | `100` | 无限卷料.加工前停靠位置X坐标 | Unlimit Roll Sheet.Before Process Dock Pt X | double | mm | 10 | -100000.0 | 100000.0 |  |
| PGraphParam | `GRP.BeforeManuDockPtY` | `100` | 无限卷料.加工前停靠位置Y坐标 | Unlimit Roll Sheet.Before Process Dock Pt Y | double | mm | 10 | -100000.0 | 100000.0 |  |
| PGraphParam | `GRP.EnableCutOffRollSheet` | `0` | 无限卷料.启用切断 | Unlimit Roll Sheet.Enable Cut-off sheet | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.RollSheetWidth` | `1200` | 无限卷料.切断板材的宽度 | Unlimit Roll Sheet.Width of Cut-off Sheet | double |  | 1200 | 10.0 | 99999.0 |  |
| PGraphParam | `GRP.CutOffRollSheetX_LeftEdge` | `100` | 无限卷料.切断板材-X左侧坐标 | Unlimit Roll Sheet.Cut-off Sheet [X-Coord of Left point] | double |  | 100 | -99999.0 | 99999.0 |  |
| PGraphParam | `GRP.CutOffRollSheetX_RightEdge` | `100` | 无限卷料.切断板材-X右侧坐标 | Unlimit Roll Sheet.Cut-off Sheet [X-Coord of Right point] | double |  | 100 | -99999.0 | 99999.0 |  |
| PGraphParam | `GRP.EnableRollSheetMicoLink` | `0` | 无限卷料.载入图后自动微连 | Unlimit Roll Sheet.Auto mico-Link after load graph file | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.RollSheetWidth_btnCmd` | `1200` | 无限卷料.切断板材的宽度 | Unlimit Roll Sheet.Width of Cut-off Sheet | double | mm | 1200 | 10.0 | 9999.0 |  |
| PGraphParam | `GRP.CutOffRollSheetSpeed` | `100` | 无限卷料.切断板材的速度 | Unlimit Roll Sheet.Cut Speed of Cut-off Sheet | double |  | 100 | 10.0 | 99999.0 |  |
| PGraphParam | `GRP.CutOffOutEdgeCheckTime` | `100` | 无限卷料.切断-出边检测时间 | Unlimit Roll Sheet.Cut-off [check time of Outing-edge] | int |  | 100 | 0.0 | 99999.0 |  |
| PGraphParam | `GRP.CutOffOutEdgeCheckTol` | `1` | 无限卷料.切断-出边检测容差 | Unlimit Roll Sheet.Cut-off [check tolerance of Outing-edge] | double |  | 1 | 0.0 | 99999.0 |  |
| PGraphParam | `GRP.CutOffHeadUpHWorkDone` | `3` | 无限卷料.切断-完成后上Z抬高度 | Unlimit Roll Sheet.Cut-off [Z Up height after work done] | double |  | 3 | 0.0 | 99999.0 |  |
| PGraphParam | `GRP.UnlimitRollDoneReset` | `0` | 无限卷料.完成后重置状态 | Unlimit Roll Sheet.Reset after work done | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.EnableBatchCut` | `0` | 批量加工.是否启用批量加工 | Batch Cut.Enable Batch Cut | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.BatchCutX_LeftEdge` | `100` | 批量加工.加工前停靠位置X坐标 | Batch Cut.Before Process Dock Pt X | double |  | 100 | -99999.0 | 99999.0 |  |
| PGraphParam | `GRP.BatchCutX_RightEdge` | `100` | 批量加工.加工前停靠位置Y坐标 | Batch Cut.Before Process Dock Pt Y | double |  | 100 | -99999.0 | 99999.0 |  |
| PGraphParam | `GRP.BatchCutMicoLink` | `0` | 批量加工.载入图后自动微连 | Batch Cut.Auto mico-Link after load graph file | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.BatchCutRoate` | `0` | 批量加工.载入图后自动旋转 | Batch Cut.Auto Rotate after load graph file | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.BatchCutRoateAngle` | `0` | 批量加工.载入图后自动旋转角度 | Batch Cut.Auto Rotate Angle after load graph file | double |  | 0 | -180.0 | 180.0 |  |
| PGraphParam | `GRP.RollSheetRoate` | `0` | 无限卷料.载入图后自动旋转 | Unlimit Roll Sheet.Auto Rotate after load graph file | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.RollSheetRoateAngle` | `0` | 无限卷料.载入图后自动旋转角度 | Unlimit Roll Sheet.Auto Rotate Angle after load graph file | double |  | 0 | -180.0 | 180.0 |  |
| PGraphParam | `GRP.EdgeSeekXDirection` | `0` | 运动轴.X轴寻边方向 | Motion axis.X axis edge search direction | enum |  | 0 | 0.0 | 1.0 | 0=Positive (正向); 1=Negative (负向) |
| PGraphParam | `GRP.EdgeSeekYDirection` | `0` | 运动轴.Y轴寻边方向 | Motion axis.Y axis edge search direction | enum |  | 0 | 0.0 | 1.0 | 0=Positive (正向); 1=Negative (负向) |
| PGraphParam | `GRP.EdgeSeekXPointDist` | `100` | 运动轴.X轴检测点间距 | Motion axis.X axis detection point spacing | double |  | 100 | -10000000.0 | 10000000.0 |  |
| PGraphParam | `GRP.EdgeSeekYPointDist` | `100` | 运动轴.Y轴检测点间距 | Motion axis.Y axis detection point spacing | double |  | 100 | -10000000.0 | 10000000.0 |  |
| PGraphParam | `GRP.CutOffType` | `0` | 无限卷料.切断方向 | Unlimited coils.Cutting direction | enum |  | 0 | 0.0 | 1.0 | 0=Horizontal (横向); 1=Vertical (纵向) |
| PGraphParam | `GRP.EdgeOutTime` | `1` | 调高器.移出检测时间 | Z Follower.Move Out Check Time | int |  | 1 | 1.0 | 99.0 |  |
| PGraphParam | `GRP.EdgeAutoBoardSize` | `0` | 调高器.移出检测时间 | Z Follower.Move Out Check Time | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.EdgeBoardSizeX` | `5` | 运动轴.X留边距离 | Normal Axis.X Edge intersection compensates distance | double |  | 5 | 50.0 | 99999.0 |  |
| PGraphParam | `GRP.EdgeBoardSizeY` | `5` | 运动轴.Y留边距离 | Normal Axis.Y Edge intersection compensates distance | double |  | 5 | 50.0 | 99999.0 |  |
| PGraphParam | `GRP.EdgeSeekMode` | `3` | 运动轴.Y留边距离 | Normal Axis.Y Edge intersection compensates distance | int |  | 3 | 0.0 | 7.0 |  |
| PGraphParam | `GRP.EdgeSeekIsCurrentStart` | `0` | 调高器.移出检测时间 | Z Follower.Move Out Check Time | bool |  | 0 | 0.0 | 1.0 |  |
| PGraphParam | `GRP.EdgeSeekStartPointX` | `0` | 运动轴.X留边距离 | Normal Axis.X Edge intersection compensates distance | double |  | 0 | -10000000.0 | 10000000.0 |  |
| PGraphParam | `GRP.EdgeSeekStartPointY` | `0` | 运动轴.Y留边距离 | Normal Axis.Y Edge intersection compensates distance | double |  | 0 | -10000000.0 | 10000000.0 |  |
| PGraphParam | `GRP.EdgeSeekXPercent` | `0.5` | 运动轴.X轴检测点间距比例 | Motion axis.X axis detection point spacing ratio | double |  | 0.5 | 0.1 | 1.0 |  |
| PGraphParam | `GRP.EdgeSeekYPercent` | `0.5` | 运动轴.Y轴检测点间距比例 | Motion axis.Y axis detection point spacing ratio | double |  | 0.5 | 0.1 | 1.0 |  |
| PGraphParam | `GRP.EdgeSeekOutSpeed` | `50` | 运动轴.出边速度 | Motion axis. Edging velocity | double |  | 50 | 25.0 | 100.0 |  |
| PGraphParam | `GRP.minHorizontalLineGap` | `0.01` | 参数.最小水平线间距 | Parameter.Minimal Horizontal Line Gap | double | mm | 0.1 | 0.01 | 100.0 |  |
| PGraphParam | `GRP.minScanLineLength` | `0.01` | 参数.最小扫描线长度 | Parameter.Minimal Scan Line Length | double | mm | 0.1 | 0.01 | 100.0 |  |
| PGraphParam | `GRP.scanSideLineLength` | `60` | 参数.外扩线长度 | Parameter.Side line length | double | mm | 5.0 | 0.01 | 100.0 |  |
| PGraphParam | `GRP.scanDirection` | `0` | 参数.扫描方向 | Parameter.Scan direction | enum |  | 0 | 0.0 | 1.0 | 0=Left-down Corner (左下); 1=Left-up Corner (左上) |
| PGraphParam | `GRP.FiberScanFlyCompensateStr` | `100#0.35,200#0.59,300#0.82,400#1.02,500#1.26,600#1.48` | 参数.扫描方向 | Parameter.Scan direction | string |  |  | 0.0 | 0.0 |  |
| PGraphParam | `GRP.CO2ScanFlyCompensateStr` | `100#0.22,200#0.3,300#0.57,400#0.71,500#0.85,600#0.94` | 参数.扫描方向 | Parameter.Scan direction | string |  |  | 0.0 | 0.0 |  |
| PNestParam | `NP.SheetWidth` | `1200` | 整板材参数.宽度 | Std Sheet Parameter.Sheet Width | double | mm | 1500 | 10.0 | 100000.0 |  |
| PNestParam | `NP.SheetHeight` | `2400` | 整板材参数.高度 | Std Sheet Parameter.Sheet Height | double | mm | 3000 | 10.0 | 100000.0 |  |
| PNestParam | `NP.EdgeStock` | `5` | 排样参数.留边距 | Nest Parameter.Edge stock | double | mm | 5.0 | 0.0 | 100.0 |  |
| PNestParam | `NP.PartSpace` | `3` | 排样参数.间距 | Nest Parameter.Part Space | double | mm | 3.0 | 0.0 | 100.0 |  |
| PNestParam | `NP.NestDirection` | `0` | 排样参数.排样方向 | Nest Parameter.Nest Direction | enum |  | 0 | 0.0 | 1.0 | 0=Left to Right (从左到右); 1=Down to Up (从下到上) |
| PNestParam | `NP.PartRoateStepAngle_deg` | `90` | 排样参数.零件旋转步角 | Nest Parameter.Part rotate step angle | int | ° | 90 | 0.0 | 360.0 |  |
| PNestParam | `NP.IsNestPartInner` | `0` | 排样参数.是否嵌套排样 | Nest Parameter.Is Nest in part inner | bool |  | 0 | 0.0 | 1.0 |  |
| PNestParam | `NP.EnableShareEdge` | `0` | 排样参数.是否共边 | Nest Parameter.Enable share edge | bool |  | 0 | 0.0 | 1.0 |  |
| PNestParam | `NP.ShareEdgeMinLen` | `20` | 排样参数.最小共边边长 | Nest Parameter.Min length of share edge | double |  | 20 | 2.0 | 99999.0 |  |
| PNestParam | `NP.NestAccuracy` | `0.5` | 排样参数.排样图形精度 | Nest Parameter.Nest Graph accuracy | double |  | 0.5 | 0.1 | 1.0 |  |
| PNestParam | `NP.OnlyNestSelectedPart` | `0` | 排样参数.仅排样选中零件 | Nest Parameter.Only Nest selected part | bool |  | 0 | 0.0 | 1.0 |  |
| PImportGraphParam | `IGP.IsAutoFilterMicoGraph` | `1` | 小图形.自动去除极小图形 | Minimal Graphics.Remove minimal graphic | bool |  | 1 | 0.0 | 1.0 |  |
| PImportGraphParam | `IGP.MicoGraphGate` | `0.01` | 小图形.最小图形长度 | Minimal Graphics.Smallest Length | double | mm | 0.1 | 0.01 | 10.0 |  |
| PImportGraphParam | `IGP.IsFilteOverlapGraph` | `1` | 重复线.自动去除重复线 | Duplicate Lines.Remove duplicate lines | bool |  | 1 | 0.0 | 1.0 |  |
| PImportGraphParam | `IGP.OverlapGate` | `0.01` | 重复线.重复线检测精度 | Duplicate Lines.Duplicate Limit | double | mm | 0.1 | 0.01 | 0.5 |  |
| PImportGraphParam | `IGP.MegerConnectGraphType` | `1` | 相连线.自动合并相连线 | Combine Near Lines.Auto combine near lines | enum |  | 1 | 0.0 | 3.0 | 0=None (不启用); 1=Direction First (方向优先); 2=Length First (长度优先); 3=Distance First (距离优先) |
| PImportGraphParam | `IGP.ConnectGate` | `0.01` | 相连线.相连线检测精度 | Combine Near Lines.Auto Combine Precision | double | mm | 0.1 | 0.01 | 0.5 |  |
| PImportGraphParam | `IGP.IsAutoSmoothGraph` | `0` | 自动平滑.读入文件时自动曲线平滑 | Auto Smooth.Auto smooth imported graphics | bool |  | 0 | 0.0 | 1.0 |  |
| PImportGraphParam | `IGP.SmoothAccuracy` | `0.050000000000000003` | 自动平滑.曲线平滑精度 | Auto Smooth.Smooth Precision | double | mm | 0.05 | 0.01 | 10.0 |  |
| PImportGraphParam | `IGP.AutoSortType` | `5` | 自动排序.自动排序策略 | Auto Sort.Sort Type | enum |  | 5 | 0.0 | 7.0 | 0=None (不启用); 1=Left to right (从左到右); 2=Right to left (从右到左); 3=Bottom to top  (从下到上); 4=Top to bottom (从上到下); 5=Nearest (局部最短路径); 6=Inside to Outside (从内到外); 7=Outside to Inside (从外到内) |
| PImportGraphParam | `IGP.LongContourSplitFactor` | `0.40000000000000002` | 杂项.大图优化系数 | Misc.Long Contour Split Factor | double |  | 0.4 | 0.05 | 1.0 |  |
| PImportGraphParam | `IGP.UIRefreshCycle` | `30` | 杂项.界面绘制刷新周期 | Misc.User interface refresh period | int |  | 30 | 10.0 | 100.0 |  |
| PImportGraphParam | `IGP.EnableRuler` | `1` | 杂项.启用标尺 | Misc.Enable Ruler | bool |  | 1 | 0.0 | 1.0 |  |
| PImportGraphParam | `IGP.EnableReadGraphColor` | `0` | 杂项.启用图形颜色读取 | Misc.Enable Read Graphics Color | bool |  | 0 | 0.0 | 1.0 |  |
| PImportGraphParam | `IGP.AutoCalcKeyboardMoveStepLength` | `1` | 交互.自动计算键盘移动图形单步长度 | Misc.Auto calculate keyboard move length  | bool |  | 1 | 0.0 | 1.0 |  |
| PImportGraphParam | `IGP.KeyboardMoveStepLength` | `1` | 交互.键盘移动图形单步长度 | Misc.Keyboard move step length | double |  | 1.0 | 0.01 | 1000.0 |  |
| PImportGraphParam | `IGP.IsAutoSmoothSpline` | `0` | 自动平滑.读入文件时自动光顺样条曲线 | Automatic smoothing. Automatically smooth spline curves when reading files | bool |  | 0 | 0.0 | 1.0 |  |
| PImportGraphParam | `IGP.LongContourAutoSplit` | `0` | 大图形.大图自动分割 | Large Contour.Long Contour Auto Split | bool |  | 0 | 0.0 | 1.0 |  |
| PImportGraphParam | `IGP.AutoSplitThreshold` | `100000` | 大图形.自动分割阈值 | Large Contour.Auto Split Threshold | double |  | 100000 | 10000.0 | 200000.0 |  |

---

## 2. Derived physical machine description

### 2.1 Controller, firmware, laser family (EVIDENCE + INFERENCE)

* Motion controller: MCC100 over Ethernet, `CardIP=10.1.1.168`, `CardPort=502` (`File/ipAdd.ini` `[IP]`). Firmware image `Update/MCC100_V201.52.mcf`; MainApp builds the update path from `"\Update\MCC100_V"` + `".mcf"` (UTF-16 strings, `.rdata`), and `ipAdd.ini` `[Soft] MinHardwareVer=20152` — INFERENCE `[likely]` 20152 ⇔ V201.52 (the app upgrades the card automatically when its reported version is lower; `mf232`: "控制卡硬件版本过低…将自动对硬件进行升级" = "controller hardware version too old… hardware will be updated automatically"). A second firmware family `"\Update\E310_V80"` + `".afb"` (the AF/auto-focus head, `mf701`) exists but no such file ships here.
* Card family strings: `"3721"` (pushed next to `hp51` in the hard-param save path) and `RegName89` "MCC3721硬件版本" (MCC3721 hardware version); `pd1732/pd1733` = "MCC3721H"/"MCC3721NA". INFERENCE `[likely]`: MCC100 is the current name of the MCC3721 board line.
* `SP.m_iHardwareModel=224` (`BkManuPara.xml`): descriptor default is `"224"`, range 224–300, label `gp120` ("激光器" = Laser). The value is copied from the connection object's field `+0x3c` at `0x567263` (card-reported model). `0x567288–0x5672d5`: model `0xdd`(221) → `m_iEnableLaserType=2`, `0xde`(222) → `1`, `0xdf`(223) → `0`; model `0xe0`(224) leaves the user's choice and shows `A250607_2/_3` ("当前为<光纤激光器>/<CO2激光器>…请确认相关设置" = "currently fiber / CO2 laser… please confirm settings"). INFERENCE `[confirmed]`: **224 = universal MCC100 whose laser family is selected in software**; 221/222/223 are fixed-family variants (blue / CO2 / fiber respectively — see next bullet).
* `SP.m_iEnableLaserType`: `1` in `BkManuPara.xml`, `0` in `SecondBkManuPara.xml`. Code: value `0` selects the `\Technology\Fiber\` path and the `LayerParam` descriptor set; value `1` selects `\Technology\CO2\` and `CO2LayerParam`; `2` is set for model 221 (`A241024_2` "蓝光激光器" = blue laser is the third entry of the laser-family list `A241024_0/1/2` = Fiber / CO2 / Blue). INFERENCE `[confirmed]`: **0 = fiber, 1 = CO2, 2 = blue-diode**; this machine is configured as a **CO2 laser** (consistent with `CO2LaserControlType`, `CO2DOLaser`, `CO2WaterWarning` being the non-default ones in `BkHardPara.xml`, and with a CF1390 being a glass-tube CO2 cutter; `A241025_4` "玻璃管" = glass tube appears in the laser-type option list).
* `SOP.HardwareType=0` → option list `pd181/pd182` = "1"/"2", label `pd296` "控制卡.硬件版本" (Controller.Hardware Version) → hardware version "1".
* `SP.MachineID/DataCardID/CommandID=0`, `SOP.EnableRemoteMonitor=0`: the remote monitor (`MonitorIP=47.104.17.21:9001`, HTTP POST `logID=…&machineID=…&isText=…&logData=…` in NCModule.dll) is **disabled** on this machine.

### 2.2 Axes

Two parallel axis models coexist in `BkHardPara.xml`:

1. `PAxisParam/A0..A3` + `PHomeParam` — the older "4 pulse axes" model whose labels are `pd197..pd247`: **A0 = X轴, A1 = Y1轴, A2 = Y2轴, A3 = 第4轴 (4th axis)**. The MCC100 register names confirm the card has exactly these four pulse axes: `RegName9..12` = "X/Y1/Y2/W轴累计脉冲" (accumulated pulses), `RegName63..70` = per-axis soft limits X/Y1/Y2/W, `RegName97..100` "PC已发送脉冲数" X/Y/Z/W.
2. `PMachineAxisConfig` + `PMachineAxisConfig_0..4` (`MAC`, `MAC_1..MAC_4`) — the newer per-slot model whose labels are `EtherAxisInfos_*` / `GoHomeAdv_*` / `AllAxisInfo_*` ("轴基本参数" basic axis parameters). Five slots. INFERENCE `[likely]`: slot order is **X, Y1, Y2, Z, W**, from the contiguous UI name list `A250516_0..4` = "X轴/Y1轴/Y2轴/Z轴/W轴" and the values below (slot 0 travel 1371 ≈ 1300 mm bed, slot 1 travel 950 ≈ 900 mm bed; slot 4 has lead 10 mm and homes in the negative direction which fits a lifting table; `LPF.LiftingPlatformType=1` = "W Axis"). MainApp reads the slot index for X/Y/Z/W from global fields `g+0xba5c/0xba60/0xba64/0xba68` (e.g. `0x45d015`, `0x567b8f`); I could not find where those are assigned (probably constructor-initialised constants 0,1,3,4 — **open question**).

| Slot / role | `SoftLimitMaxLen` (mm) | `WritePluse` (pulses/rev) | `SpeedRatio` = lead (mm/rev) | derived pulses/mm | `AxisReverse` | Home dir (`GoOriginalDirection`) | `ReturnLength` (mm, home back-off) | `FastSpeed`/`SecondSpeed` (mm/s) | Limit inputs `Neg`/`Fwd` (DI#) | `Acceleration` (mm/s²) / `AccelerationTime` (ms) |
|---|---|---|---|---|---|---|---|---|---|---|
| `MAC` = X | **1371** | 8000 | **31.003** | **258.04** | 1 (reversed) | 0 = 负向 negative | 22 | 80 / 20 | DI6 / DI5 | 20000 / 125 |
| `MAC_1` = Y1 | **950** | 8000 | **31.009** | **257.99** | 1 | 0 negative | 15 | 80 / 20 | DI8 / DI7 | 20000 / 125 |
| `MAC_2` = Y2 (dual-drive slave, unused: `MAC.DoubleDevice=0`) | 1000 (default) | 2000 | 5 | 400 | 1 | 0 | 5 | 30 / 20 | 0 / 0 (none) | 20000 / 125 |
| `MAC_3` = Z | 1000 (default) | 10000 (default) | 5 | 2000 | 0 | 0 | 10 | 30 / 20 | DI9 / DI10 | 20000 / 125 |
| `MAC_4` = W (lifting platform) | 1000 (default) | 2000 | 10 | 200 | 1 | **1 = 正向 positive** | 5 | 30 / 20 | DI2 / DI3 | 20000 / 125 |

EVIDENCE for the meaning of the columns: descriptor labels `EtherAxisInfos_4` "轴基本参数.最大行程" (max stroke), `A241021_1` "轴基本参数.每转脉冲数" (pulses per revolution, English "Encoder resolution"), `EtherAxisInfos_10` "轴基本参数.导程(mm)" (lead, mm), `EtherAxisInfos_5` "运行反向" (run reversed), `EtherAxisInfos_6` "编码器反向", `EtherAxisInfos_7` "回原方向", `GoHomeAdv_3/4/5` "粗定位速度/精定位速度/返回距离" (coarse/fine homing speed, return distance), `EtherAxisInfos_8/9/11` "负限位/正限位/原点输入端口" (neg/pos/origin input port), `EtherAxisInfos_1/2/3` "正限位/负限位开关逻辑, 原点输入类型" (switch logic NO/NC), `EtherAxisInfos_23` "抱闸.输出端口" (brake output), `EtherAxisInfos_24` "作为旋转轴" (is rotary axis), `EtherAxisInfos_27/28` "工作参数.加速度/加速时间", `GoHomeAdv_0/1/2` "使用Z相信号/二次回原/采样信号" (use Z-pulse / second homing / sample signal = origin switch vs limit switch).

Key derived numbers `[likely]`:
* **Pulse equivalent X = 8000 / 31.003 = 258.04 pulses/mm, Y = 8000 / 31.009 = 257.99 pulses/mm** (i.e. 3.875 µm/pulse). The near-identical but not equal leads (31.003 vs 31.009) are typical of a calibrated belt/rack drive.
* **Usable travel X = 1371 mm, Y = 950 mm** (soft limits; nominal bed 1300 × 900).
* Max axis speed / accel are not axis parameters but process parameters (§2.6).
* `PAxisParam` `A0..A3.PulseEquivalent="1"` and `MaxLength=1500/3000/3000/1500` are the untouched *defaults* of the legacy model (descriptor defaults are 1 and 1500/3000/3000/1500) — INFERENCE `[likely]`: with `m_iHardwareModel=224` the MCC100 uses the `MAC*` block, and the `A*` block only contributes `EnableType`/`AxisIndex`/`DoubleDriver`/`LimitSwitchType`. `A3.EnableType=2` (`pd159` "无效" invalid) ⇒ the 4th pulse axis is disabled in that model; `A1/A2.DoubleDriver=1` claim Y is dual-drive whereas `MAC.DoubleDevice=0` (AllAxisInfo_1 "Y轴.双驱") says single — contradictory legacy leftovers; **open question** which one the firmware honours (Y2 has no limit inputs assigned, which suggests single-drive).
* `AX`: `DoubleDriverAlarm=1`, tolerance 3 mm for 100 ms (dual-drive skew alarm), `Enable4Freq=1` (encoder ×4), `SafeStopFactor=3`, `InterpolationCycle=250` (unit "us" per descriptor; default 1000), `InitializationDelay=0` ms ("总线初始化延时" EtherCAT init delay — unused here).
* `PMachineAxisConfig/MAC`: `AllowableError=2000`, `AlarmTime=1000` (AllAxisInfo_6/7 "Y轴.双驱容差 / 告警时间"), `CloseBreakTime=500` (AllAxisInfo_12 "抱闸.释放延时" brake release delay, ms).

### 2.3 Homing

* Legacy block `HP`: `UseZPulse=0`, `SampleSignal=1` (`pd166/pd167`: 0 = 原点 origin switch, 1 = 限位 **limit switch**), `LimitSwitchType=0` (NO), `FastSpeed=50`, `SlowSpeed=10` mm/s, `EnableSecondTimeHome=0`; `A0/A1.HomeDirection=0` (负向 negative), `HomeOffset=10` mm. `HPA3` (4th axis) same plus `Acc=4000`, `AccTime_ms=125`, `WorkSpeed=50`, `IdelSpeed=100`.
* Slot block (`MAC*`): `SampleType=1` (home on limit switch), `ZoreType/NegativeType/ForwardType=0` (all NO), `EnableZphaseSignal=0`, `SecondGoHome=0`, X back-off 22 mm, Y 15 mm.
* INFERENCE `[likely]`: X and Y home in the negative direction onto their **negative limit switch** (DI6 for X, DI8 for Y), then back off 22/15 mm; machine origin is therefore the negative-limit corner. `SOP.IsStartNeedGoOrigin=1` (`pd530` "开机是否提示回原" prompt to home at start-up) and `mp119` "硬件连接成功，是否对系统进行回原？".
* `SP.EnableDualFindHome=0` (`pd379_1` "默认龙门回零" default gantry homing) — gantry squaring routine off; `SP.DualCheckTime=5`, `DualCheckMaxErrorValue=3`.

### 2.4 Z axis / height follower / focus

* `ZF.ZFType=1` → option list `pd170/pd1733_1` = "不使用 None" / "**板载调高器 Onboard FTC**" (FTC = follow-the-contour capacitive height controller, 调高器). Non-default (default 0). So the capacitive Z-follower is the one **built into the MCC100** ("OnB" in ipAdd.ini = on-board; `OnBZFIP=10.1.1.168` = the card, `OnBZFPort=999`). Other ZF types known to the code base (`pd171..pd173`, `pd1731..pd1733`: "FTC10 网口", "FTC61 IO", "FTC61 电脑串口", "FTC61 板载串口", "MCC3721H", "MCC3721NA") are **not** offered by this build (their label ids are absent from the exe).
* ZF I/O mapping `ZF.DOFollow/DODrill/DOJogUp/DOJogDown/DIWarning/DIFollowReady/DIDrillReady = 0` — unused (only relevant for the "FTC61 IO" type).
* Run-time ZF settings (`BkManuPara.xml` `PZFParam/ZF`): `ZFFollowSpeed=100`, `ZFUpSpeed=100`, `ZFJogSpeed=10`, `ZFFastJogSpeed=50` (mm/s), `ZFDockHeight=20` mm, `ZFOriginSoftLimitIndex=0`, `ZFSignalCorrectionPeriod=30` (`newLang500`: "信号修正间隔" signal correction period, 1–180). `MP.ZFSafeHeight=15` mm, `MP.ZFFrogJumpType=0` (普通蛙跳 normal frog-jump), `MP.IsZFGoOriginAfterDone=0`, `MP.IsStopWaitZFDone=0`, `FC.EnableLeapFrogUp=1`, `FC.ShortNoUpMaxLength=10` mm, `FC.EnableEmtptMoveFollow=0`, `MC.IsZFMoveToSafePosBeforeProcess=0`, `MP.EnableManuCrashProtect=1` with `ManuCrashProtectUpHeight=35` and `EnableManuCrashProtectMinHeight=5` (crash-protection lift).
* Auto-focus head (电动调焦, AF): `AF.AFType=0` (`pd640/pd641`: None / MCC Serial) — **no motorised focus**. `AFDA.*` (analog "DA" focus drive: `DAPort=0`, `EnableMovePort=0`, `GoOriginPort=0`, `ExeDonePort=0`, `AlarmPort=0`) all unassigned. `PAFParam/AF.AFPosSpeed=10`.
* Extended card (扩展板, EC): `EC.ECType=0`, `ECIOType=0` (None / MCC Serial), `ECAxisPulseEq=1000`, `ECAxisMaxSpeed=1000`, `ECAxisServoDir=0` — **no extension card** (used for exchange tables, roll feeders, rotary platforms: `MP.ExchangePlatformType/RollSheetType/RotatePlatformType=0`).
* Lifting platform: `LPF.LiftingPlatformType=1` (`A250410_1` "升降平台.控制方式" lifting platform control type; options `pd351/pd41` = 不使用 None / **W轴 W axis**). INFERENCE `[likely]`: the CF1390's motorised Z-table (bed lift) is driven as pulse axis W (slot `MAC_4`: 2000 p/rev, 10 mm lead, limits DI2/DI3, homes positive); UI buttons `A250410_3/4` "上升/下降" (Up/Down).

### 2.5 Laser type and control mode

`PLaserParam/LGP` holds two parallel sets (fiber `Laser*`/`DO*` and CO2 `CO2*`):

| Key | Value | Decoded |
|---|---|---|
| `LaserType` | 0 | `pd47` "锐科 Raycus" (fiber-laser brand list: 0 Raycus, 1 IPG, 2 半导体 semiconductor, 3 创鑫 MaxPhotonics, 4 联品 Super, 5 天星 TXStar, 6 nLight, 7 国志 GZ, 8 其它 Others) — default, irrelevant for CO2 |
| `LaserControlType` | **3** | `pd640..pd644`: 0 None, 1 板载串口 MCC serial, 2 网口 Net, **3 IO**, 4 电脑串口 PC serial (fiber-laser channel; non-default) |
| `LaserDAPort` | 1 | DA1 (0 none,1 DA1,2 DA2); `LaserDAType=0` = 0–10 V |
| `DORemoteStart` | 0 | none |
| `DOLaserGate` | **5** | `pd263` "IO.光闸" shutter → DO5 |
| `DOLaser` | 0 | none |
| `DORedLight` | **6** | `pd265` "IO.红光" red pointer → DO6 |
| `LaserSerialPort/BaudRate` | 0/0 | COM none / 9600 |
| `LaserMaxPower` | 0 W | unset |
| `CO2LaserControlType` | **2** | `pd640`, `A250522_1..3`: 0 None, 1 **24V PWM**, 2 **5V PWM**, 3 DA → **5 V PWM** |
| `CO2LaserDAPort` | 1 | DA1 |
| `CO2LaserDAType` | 1 | 0–5 V |
| `CO2DORemoteStart` | 0 | none |
| `CO2DOLaserGate` | 0 | none |
| `CO2DOLaser` | **9** | `A241104_3` "CO2 激光" laser-enable output → DO9 |
| `CO2DORedLight` | 0 | none |
| `doCO2EnableOutput` | 0 | `A250306_2` "IO.外控输出" external-control output, none |

INFERENCE `[confirmed]` for the CO2 path: **the tube is fired by a 5 V PWM signal from the MCC100 (frequency/duty from the layer parameters: `RegName54/55` "PWM频率输出 / PWM占空比输出", `ec19` range 1–50 000 Hz) plus a discrete laser-enable on DO9**; there is no analog power control for the CO2 tube (DA type is only used when `CO2LaserControlType=3`). The DA calibration values `DA.DA1OutputAdjustVal=3648`, `DA2OutputAdjustVal=3480` (12-bit, range 0–4095; `pd286/287` "DA1/DA2输出校准值") and `AD.ADAdjustVal=3240` (`pd288` "AD输入校准值") are card calibration constants. `MP.LaserDAKeepOutput=0` (`pd662` "DA.DA上电输出" keep DA output at power-up) is non-default. `MP.IsEnablePWMPerContour=1` (`pd1608` "每段轮廓切换PWM使能" switch PWM per contour) is non-default. `LC.PtLaserFreq=1234` Hz, `PtLaserPeakCurrent=20 %`, `MC.LaserPointPower=40 %`, `MC.PtLaserTime_ms=200` are the manual "点射" (laser test-shot) settings. The fiber-laser settings (`LaserControlType=3 IO`, `DOLaserGate=5`, `DORedLight=6`) are inactive while `m_iEnableLaserType=1` (CO2) `[likely]` — they look like leftovers from a fiber template.

`ipAdd.ini`: `LaserIP=10.1.1.170:10001` = Ethernet fiber laser (NCModule `CLaserNetHalAPI`, `CIPGModbus`, `CRaycusModbus`), `OnBLaserIP=10.1.1.168:888` = laser control service on the card. Neither is used for a PWM-driven CO2 tube `[likely]`.

### 2.6 Speeds, accelerations, units (from `BkManuPara.xml`)

* `UN.SpeedUnit=1` → **m/min** (`pd52..pd53-2`: 0 mm/s, 1 m/min, 2 inch/s, 3 inch/min); `UN.AccUnit=1` → **G** (0 mm/s², 1 G, 2 inch/s²); `UN.GasPressureUnit=0` → bar. These are *display* units; the XML stores SI base units (descriptor unit strings are mm/s and mm/s2).
* `MC.XFastMoveSpeed=500` mm/s (`pd89` 空走速度 rapid speed, max 500), `MC.XFastMoveAcc=6000` mm/s² (`pd90` 空走加速度), `MC.EmptyMoveAccTime=125` ms, `MC.ManuAcc=6000` mm/s² (`pd91` 加工加速度 cutting accel), `MC.AccTime=200` ms, `MC.BoundSpeed=500` mm/s (`pd528` 走边框速度 frame-trace speed), `MC.JogFastSpeed=200`, `MC.JogSlowSpeed=50` mm/s, `MC.StepLength=1` mm, `MC.SplineAccuracyRate=0.02`, `MC.CornerAccuracyRate=0.05`.
* Hard caps in `BkHardPara.xml` `PFCParam/FCP`: `MaxSpeed=3000` mm/s, `MaxAcc=20000` mm/s² (`pd509/pd510` "高级切割.系统最大速度/加速度"), `FrogJumpMinHeight=10`, `FlycutEncoderToleranceRatio=6`, `FlycutCircleVelRatio=1`, `FlycutLineOpenPwmForwardCycle=-3`, `FlycutLineColsePwmForwardCycle=-3` (PWM lead/lag in interpolation cycles for scan cutting; default −10).
* `MP.EmptyMoveSpeedFactor=1.1`, `EmptyMoveAccFactor=1.5` (`pd2205/2206` 速度系数1/2), `MP.JogStopDccFactor=1`, `SP.LimitDeccFactor=1`, `SP.LimitDeccLengthRatio=0.1` (limit-approach deceleration zone = 10 % of the travel).
* `MS.EnableSoftLimit=0` in `BkManuPara.xml` (**soft limits off**) but `=1` in `SecondBkManuPara.xml` — see §5.
* `JumpAddTime.txt` `[Jump] AddTime=200`: added to the frog-jump time as ms (code `0x443b79`: `GetPrivateProfileInt("Jump","AddTime",100)`, then `fild; fmul 0.001; fadd` into a seconds value) — INFERENCE `[confirmed]` unit ms, default 100.

### 2.7 Gas system

`PGasParam/MGP` (DO port numbers unless noted; 0 = unassigned):

| Key | Value | Label | Meaning |
|---|---|---|---|
| `LowAir` | 0 | 低压阀.低压空气 | low-pressure air valve — none |
| `LowO2` | **7** | 低压阀.低压氧气 | low-pressure O2 valve → **DO7** |
| `LowN2` | 0 | 低压阀.低压氮气 | none |
| `HighAir` | **3** | 高压阀.高压空气 | high-pressure air → **DO3** |
| `HighO2` | 0 | 高压阀.高压氧气 | none |
| `HighN2` | **2** | 高压阀.高压氮气 | high-pressure N2 → **DO2** |
| `RatioAir` | 0 | 比例阀.空气比例阀(DA) | proportional valve DA channel: none |
| `RatioO2` | **2** | 比例阀.氧气比例阀(DA) | O2 proportional valve → **DA2** |
| `RatioH2` | 0 | 比例阀.氮气比例阀(DA) (attribute misnamed "H2", label says N2) | none |
| `DAMaxPressure` | 10 | 比例阀.最高气压 | full-scale pressure of the proportional valve, bar |
| `NewDAMAxPressureAir/O2/N2` | 10/10/10 | 比例阀.空气/氧气/氮气最高气压 | per-gas full scale, bar |
| `RatioAirSwitch/RatioO2Switch/RatioN2Switch` | 0 | 比例阀.xx比例阀开关 | DO that enables each proportional valve — none |
| `CoolGas` | 0 | 杂项.冷却气 | cooling-gas DO — none |
| `iProportionalOpenSleep` | 50 ms | 杂项.比例阀开启前延时 | delay before opening proportional valve |

`DO.GtO2EnableGasDAMap=1` (`pd1601` "气压映射.启用氧气气压校正" enable O2 pressure-to-voltage correction map; the map string `GtO2GasDAMapStr` is empty, so the linear default is used), `DO.CurrentAdjGasType=1` (O2). `GC.DefaultGasPressure=3.45` (descriptor unit "V" — it is the DA voltage default, `pd97` "默认气压"), `GC.GasDelay/DirectGasDelay/ChangeGasDelay=100` ms, `MC.GasType=1` (`pd68..pd73`: 0 低压空气 low air, **1 低压氧气 low O2**, 2 low N2, 3 high air, 4 high O2, 5 high N2) = manual gas type. INFERENCE `[likely]`: three solenoid gas outputs (DO7 low-O2, DO3 high-air, DO2 high-N2) plus one electronic proportional regulator on DA2 for O2 (0–10 V ≙ 0–10 bar).

### 2.8 Digital I/O map (this machine)

DO/DI numbers are 1-based physical ports of the MCC100; 0 = not assigned. (EVIDENCE for 1-based numbering: `DI.DI1SmoothTime…DI16SmoothTime`, `RegName114..129` "DI1..DI16滤波时间", IO-monitor names `pd28..pd35` "输入7…输入14"; `FunctionDIStr` uses `#12#` for DI12.) The card exposes at least 16 DI with per-input filter time (`SystemRWRegName_36..47` go to IN24) and outputs numbered up to 26 in the descriptors (INFERENCE `[guess]`: 16 on-board DO + 8–10 expansion outputs).

**Outputs**

| DO | Function | Source key |
|---|---|---|
| DO1 | 报警指示灯 alarm lamp | `DO.AlarmSignal=1` |
| DO2 | 高压氮气 high-pressure N2 valve | `MGP.HighN2=2` |
| DO3 | 高压空气 high-pressure air valve | `MGP.HighAir=3` |
| DO5 | 光闸 shutter (fiber path, inactive for CO2) | `LGP.DOLaserGate=5` |
| DO6 | 红光 red pointer (fiber path) | `LGP.DORedLight=6` |
| DO7 | 低压氧气 low-pressure O2 valve | `MGP.LowO2=7` |
| DO9 | CO2 激光 laser enable | `LGP.CO2DOLaser=9` |
| DA1 | laser analog channel (unused with 5 V PWM) | `LGP.LaserDAPort=1`, `CO2LaserDAPort=1` |
| DA2 | O2 proportional valve | `MGP.RatioO2=2` |
| PWM (5 V) | CO2 tube power | `LGP.CO2LaserControlType=2` |
| — | wait/process lamp, ring, dedust (`ClearAsh`), Fn1/Fn2, oil pump (`MP.OilDOPort`), section blowers (`SectionDOStr="0,0,0;0,1,0;1,0,0;1,1,0;"` = 2×2 grid, every cell DO 0), platform/roll/PLC outputs | all 0 = unassigned |

**Inputs**

| DI | Function | Logic | Source key |
|---|---|---|---|
| DI1 | 光纤激光器激光器报警 fiber-laser alarm (inactive for CO2) | NO (`LaserWarningType=0`) | `DI.LaserWarning=1` |
| DI2 / DI3 | W axis negative / positive limit | NO | `MAC_4.NegativeLimitInput=2`, `ForwardLimitInput=3` |
| DI4 | custom alarm "Door" | see below | `DI.AlarmDIStr="Door#4#-1#0"` |
| DI5 / DI6 | X positive / negative limit (negative one is also the home switch) | NO | `MAC.ForwardLimitInput=5`, `NegativeLimitInput=6` |
| DI7 / DI8 | Y positive / negative limit | NO | `MAC_1.ForwardLimitInput=7`, `NegativeLimitInput=8` |
| DI9 / DI10 | Z (slot 3) negative / positive limit | NO | `MAC_3.NegativeLimitInput=9`, `ForwardLimitInput=10` |
| DI11 | CO2 激光器水冷报警 chiller alarm | **NC** (`CO2WaterWarningType=1`) | `DI.CO2WaterWarning=11` |
| DI12 | function input "Low-pressure Oxygen" (manual gas select) | type −1 | `DI.FunctionDIStr` entry `Low-pressure Oxygen#12#-1` |
| — | E-stop (`DI.EStop=0`!), water alarm (fiber), platform/roll inputs, Start/Pause/Stop buttons, ZF Go Origin | | all 0 = unassigned |

String-encoded I/O tables: `DI.FunctionDIStr="Start#0#0,Pause#0#0,Stop#0#0,ZF Go Origin#0#0,Low-pressure Air#0#0,Low-pressure Oxygen#12#-1,Low-pressure Nitrogen#0#0,High-pressure Air#0#0,High-pressure Oxygen#0#0,High-pressure Nitrogen#0#0"` = comma list of `Name#DIport#type` (INFERENCE `[likely]`: type −1/0 = switch logic or edge selection; the ten names match `pd800/801/802/1020/1025..1030`). `DI.AlarmDIStr="Door#4#-1#0"` = `Name#DIport#type#flag` custom alarm list (`hp45` "自定义报警", `hp50_1` "仅加工中报警" only-alarm-while-processing, `hp50_2` "仅可手动清除告警" manual-clear-only; `DI.OnlyManualRelieveAlarm=0`). `DO.CustomDOStr=""` (`hp39` custom outputs, `Name#DO#selflock`). `DO.Fn1Type/Fn2Type=1` = `cup0` "自锁输出" self-locking (0 = `cup1` momentary). Alarm lamp blinking (`MP.AlarmSignalIsTwinkle` etc.) all off, 0.5 s on/off.

Notable: **no E-stop input is configured in software** (`DI.EStop=0`); the emergency stop must be wired directly into the drives/card (`RegName72` "急停输入端口" exists on the card). No fiber water-alarm input either (`DI.WaterWarning=0`).

### 2.9 Pendant / remote (EVIDENCE + INFERENCE)

`SOP.RemoteType=3` although the descriptor's option list has only three entries (`pd178/pd179/pd180` = "SC板载" SC MCC (card-attached receiver), "SC电脑" SC PC (USB), "CypCut") and `max=1`. Code at `0x498ed6` (`cmp [g+0x4028],3; jne`) calls `0x586e70`, which `LoadLibraryW(L"PHBX.dll")` and calls its `Xinit`/`XOpen` exports; a 4-way `switch` on the same field at `0x597cfa` has cases 0,1 → `0x599a80`, 2 → `0x59a010`, 3 → `0x599380`. INFERENCE `[confirmed]`: **RemoteType 3 = XHC "PHB02/PHBX" wireless pendant** (PHBX.dll version info: `CompanyName "chengdu XHC Tec."`, `FileDescription "PHB02"`, `ProductName "XHC PHBX"`, imports `HidD_*`/`SetupDi*` → the USB-HID dongle VID 0x3689 PID 0x8762 seen on this PC is its receiver). `A250613_0..5` messages ("手柄模块加载成功/断开/电量低/信号差/休眠/唤醒" = pendant module loaded / disconnected / low battery / poor signal / sleep / wake). `SOP.JoystickID1..5` (179705051, −684904636, 434577794, 1601441, 242474605) are the pairing codes (`pd293..295` "手柄.配对码1..3"; `RegName40..46/90..96` hold the wireless-handle address bytes on the card for the "SC MCC" type). `A241105_0` "Wifi" is another option present in the strings but not in this list.

### 2.10 Verticality / squareness and backlash (`PManuParam/MP`, `pd454` group)

`EnableVerCorrect=0`, `VerCorrectType=0` (0 = `hp20` "以X轴为基准矫正Y轴" correct Y using X as reference), square-test lengths `AB=AC=150`, diagonals `L1=L2=212.2` (150·√2 → zero skew). `XAxisGapCompensate=0`, `YAxisGapCompensate=0` mm (backlash), `CompensateType=1` — `hp26/hp27/hp28`: 0 "不补偿" no compensation, **1 "仅补偿反向间隙" backlash only**, 2 "完整螺距补偿" full pitch compensation. So pitch compensation is *not* active and the `.pcf` table is empty (§6.1).

### 2.11 Operating counters (`BkManuPara.xml` `SOP`)

`DeviceControllerTotalUseTime=142.68` h, `DeviceTotalManuTime=7.04` h, `DeviceTotalLaserOnTime=10.16` h, `DeviceTotalManuCount=6029`, `TotalManuCount=6115`, `DeviceXAxisTotalMoveLength=12986.5` m, `DeviceYAxisTotalMoveLength=8174.1` m, `DeviceZAxisTotalMoveLength=0`, `DeviceSoftwareTotalUseTime=-45.6` (negative — a counter bug), `LastLoadFilePath=C:\Users\Administrator\Desktop\222.chf`. Labels `pd880..pd888` "运行报告.*" (device report).

---

## 3. `File/ipAdd.ini` — endpoints and what ZF / AF / EC / OnB / EC3710 / AdvAF / AdvEC mean

All keys are read by `Module/NCModule.dll` (its string table contains every key name, plus defaults `10.1.1.168`, `10.1.1.169`, `10.1.1.170`, `127.0.0.1`; parsing via boost::property_tree). Class names in NCModule (`.?AV…` RTTI): `IHalAPI`, `CMCHalAPI` (motion card), `CAFNetHalAPI`, `CECNetHalAPI`, `CLaserNetHalAPI`, `CSerialHalAPI`, `CMonitorHalAPI`; Modbus flavours `IModbus`, `CStdModbus`, `CIPGModbus`, `CRaycusModbus`, `CSerialModbus`, `CExtCardModbus`, `CFTC61Modbus`, `CMonitorModbus`, `CExtModbus`. Log tags `AF Send Buf/AF Recv Buf`, `EC Send Buf/EC Recv Buf`, `syncReadZFReg`, `syncWriteAFReg`, `syncWriteECReg`.

| Prefix | Expansion (EVIDENCE) | Endpoint here | Role (INFERENCE) |
|---|---|---|---|
| `Card*` | motion-control card | 10.1.1.168:502, mask /24, gw 10.1.1.1 | MCC100 main channel (`CMCHalAPI`), Modbus-TCP-like on port 502 `[likely]` (`CStdModbus`) |
| `ZF*` | 调高器 Z-Follower (`zf*` labels: "调高器 FTC"; `NCZFMinHardwareVer=325`) | 10.1.1.169:502 | **network** height controller (FTC10 网口) — not used because `ZFType=1` is the on-board one; `pingZF.bat` pings it |
| `OnBZF*` | **On-Board ZF** (`pd1733_1` "板载调高器 Onboard FTC"; `OnBZFMinHardwareVer=311`) | 10.1.1.168:999 | the height-follower service *inside the MCC100* — **this is the active Z-follower** `[confirmed by ZFType=1]` |
| `OnBLaser*` | On-Board laser control | 10.1.1.168:888 | laser-control service on the card (serial-over-ethernet to a fiber source) `[likely]`; unused for CO2 |
| `Laser*` | fiber laser (`pd451..453` "激光器.IP地址/子网掩码/默认网关") | 10.1.1.170:10001 | direct Ethernet link to an IPG/Raycus fiber laser (`CLaserNetHalAPI`); unused here |
| `AF*` | 电动调焦 Auto-Focus head (`pd564` "电动调焦.启用电动调焦", `af*` labels, `AFMinHardwareVer=133`, firmware `E310_V80.afb`) | 10.1.1.168:888 | on-board AF service (`CAFNetHalAPI`); `AFType=0` ⇒ unused |
| `AdvAF*` | "Advanced" AF (`pd1552` "板载串口.启用高级板载串口" Enable *Advanced* MCC serial; `AF.AFEnableAdvMCCSerial=0`) | 10.1.1.168:666 | newer-protocol AF channel `[likely]`; unused |
| `EC*` | 扩展板 Extended Card (`pd960..979` "扩展板参数.*", `ec27` "扩展板属性 EBH Property", `hp70` "扩展板") | 10.1.1.168:888 | on-board extension-card service (`CECNetHalAPI`, `CExtCardModbus`); `ECType=0` ⇒ unused |
| `AdvEC*` | "Advanced" EC | 10.1.1.168:666 | newer-protocol EC channel `[likely]`; unused |
| `EC3710*` | extension card model **3710** (sibling of MCC3721; `RegName89` "MCC3721") | 10.1.1.170:502 | stand-alone Modbus-TCP I/O/axis expansion box `[likely]`; unused |
| `Monitor*` | 远程监控 remote monitor (`pd518..520`, `pd533/534`) | 47.104.17.21:9001, reconnect 300 s ×1 | vendor cloud telemetry (Alibaba-Cloud Qingdao IP range) — disabled (`EnableRemoteMonitor=0`) |

Timing keys: `ConnectWait=500`, `MCFifoTime=1600`, `MCTimeout=500`, `MCMaxSendTime=2`, `MCMaxRecvTime=3`, `MCSendInterval=1`, `FifoTimeout=600`, `MaxItemPerFrame=60`, `MaxFillItem=2000`, `FifoAlarmNum=30` (`RegName101` "加工FIFO告警门限条数"), `MCCore=30`, `ZFCore=500`, `LaserCore=1000`, `AFCore/ECCore=1000`, `MonitorCore=500` (ms polling periods `[likely]`), `MCUpdateFactor=1`, `ZFUpdateFactor=20`, `AFUpdateFactor=30`, `ECUpdateFactor=30`, `OfflineTimeout=5000`, `MaxZFDownItem=512`, `MaxAFDownItem=200`, `MaxECDownItem=200`, `AFUpdateTimeout=2000`. The logs (`Log/2025-07-18.log`) show `MC-RecvErr_selectFunc ErrCode:10060 Try-Times(R/S):1/2…3/3` sequences that match `MCMaxSendTime=2`/`MCMaxRecvTime=3` retry counts and `MCTimeout=500` ms spacing — EVIDENCE that these are the card-channel retry parameters.

`[Soft]`: `Lang=0`, `Skin=1`, `RunModel=0`, `EnableOvertime=1`, `FollowOvertime=20000`, `SectionDrillOvertime=20000`, `GradualDrillAddOvertime=20000` (ms watchdogs for follow / piercing), `DAMinVal=50`, `ManuItemMaxCapcity=1000000`, `AccessType=1`, `EnableLog=0`, `R/G/B=255/1/2`, `AlarmDay=10`, `AdvLaserWrite=1`, `Custom=1234`, `CheckUserID=109` (dongle vendor id check, cf. `dogActiveReslut7` "厂商ID不匹配").

Also `File/pingMC.bat` = `ping 10.1.1.168`, `File/pingZF.bat` = `ping 10.1.1.169`; `File/IPSet.exe` is a tiny helper that runs `netsh interface ip set address name="…" source=static addr=10.1.1.<n> mask=255.255.255.0 gateway=10.1.1.1` to put the PC NIC on the 10.1.1.0/24 subnet.

---

## 4. `softPara.ini` — `XAxis=1020277`, `YAxis=175510`

EVIDENCE:
* Written at exit by the function at `0x45d0xx` (`--- Exit Sys ---` context): for each of X/Y/Z/W it calls the HAL virtual method at vtable offset `0x1a0` with arguments `(3, axisSlot*10 + 2)` (axisSlot from `g+0xba5c/60/64/68`), converts the returned 32-bit integer to text (`0x44a7e0`) and `WritePrivateProfileStringW(L"SC2000", L"XAxis"|"YAxis"|"ZAxis"|"WAxis", text, "\File\softPara.ini")`. `NormalExit` is written as `"1"` on a clean exit (`0x567c3e` writes `1`) and reset to `0` at start-up (strings `--- Start Sys ---` … `NormalExit`).
* Read at start-up (`0x567b71`): `GetPrivateProfileIntW(L"SC2000", L"XAxis", 0)` → stored into the per-slot axis structure field `g + 0xbb54 + slot*0xa0` (the same field the run-time refresh at `0x5d9602` fills from the card register); `NormalExit` read with default 1. If `NormalExit==0` the app shows `mp18` "检测到系统最后一次加工为非正常退出，是否恢复到最近的加工状态？" ("last run ended abnormally, restore the last processing state?") and `mp19/mp21`, then reloads `autosave.chf` / `ManuContour.dat` / `AutosaveParam*.ini` (see §6.3).
* `NormalExit=0` in the shipped file ⇒ the last session on this PC ended abnormally (or the app was still running when the folder was copied — the `Log/2025-07-18.log` ends 15:49 with `MC-RecvErr` retries, i.e. the card stopped answering).

INFERENCE `[likely]`: **XAxis/YAxis/ZAxis/WAxis are the axis position registers (register index slot·10+2, the third per-axis read-only register = `AxisRORegName_3` "脉冲位置" pulse position) captured at shutdown, in 0.001 mm units** — X = 1020.277 mm, Y = 175.510 mm, both inside the 1371 × 950 mm travel. The raw-pulse interpretation is ruled out (1 020 277 pulses / 258 p/mm = 3 954 mm > travel). The µm convention is corroborated by the break-point files (§6.3), where the same magnitude of integers is multiplied by 0.001 before use. Purpose `[likely]`: position memory so that after an abnormal exit / power loss the software can restore the axis coordinates without re-homing ("记忆零点" `A241224_0` = memory zero) and offer break-point resume.

---

## 5. `BkManuPara.xml` vs `SecondBkManuPara.xml`

`diff` shows exactly three differing elements; everything else (the other 328 attributes) is byte-identical:

| Element | `BkManuPara.xml` | `SecondBkManuPara.xml` |
|---|---|---|
| `PManuParam/MS EnableSoftLimit` | `0` | `1` |
| `PSoftParam/SOP` counters | `DeviceSoftwareTotalUseTime=-45.618…`, `DeviceControllerTotalUseTime=142.683…`, `DeviceTotalLaserOnTime=10.1636…`, `DeviceXAxisTotalMoveLength=12986.532`, `DeviceYAxisTotalMoveLength=8174.109` | `-46.1329…`, `142.168…`, `10.0606…`, `12875.602`, `8131.860` (all slightly *smaller* = older) |
| `PSoftParam/SP m_iEnableLaserType` | `1` (CO2) | `0` (fiber) |

EVIDENCE for the mechanism: strings `"MainFrm saveManuParam read failed"`, `"MainFrm saveManuParam backup read failed"`, `"MainApp saveManuParam second backup read failed"`, `pp2` "加工参数文件保存错误，读取备份加工参数文件！" ("manu parameter file save error, reading backup"), `pp3` "备份加工参数文件读取错误，请重新启动软件！", `lp12/lp13/lp14` "备份系统参数 / 备份成功 / 还原成功". Load order (§0.3): `BkManuPara.xml` then `SecondBkManuPara.xml`.

INFERENCE `[confirmed]`: `SecondBkManuPara.xml` is a **second-generation backup** of the machining parameters, written less often than `BkManuPara.xml` (the counters lag by ≈0.5 h of controller time and ≈110 m of X travel). It is *not* a different profile. The two semantic differences are therefore a snapshot of settings the operator changed during the last session(s): soft limits were **switched off** and the laser family was **switched from fiber to CO2** (`A250607_0/1`: "已为您切换至<光纤/CO2激光器>…" "switched to fiber / CO2 laser for you…"). A Linux port should treat `BkManuPara.xml` as authoritative and only fall back to `SecondBkManuPara.xml` when the primary fails to parse.

---

## 6. Small state files — formats

### 6.1 `File/PithCompensate.pcf` (pitch compensation)

Content: `73 63 46 6c 69 65 0d 0a 30 0d 0a 65 6f 66 0d 0a` = `scFlie␍␊0␍␊eof␍␊`.

EVIDENCE: loaded at start-up (`0x4b248b`, path `\File\PithCompensate.pcf`) into a container object at `g+0x40b8` through the HAL interface method at vtable `+0x2b0` (`(obj, path, 0)`); on failure the container is cleared (`0x4140e0`). The precision-compensation dialog (`cp*` labels: `cp6` "精度补偿", `cp7` "位置" position, `cp8` "正向实测值" forward measured, `cp9` "正向误差" forward error, `cp10` "反向实测值", `cp11` "反向误差", `cp12` "反向间隙" backlash, `cp13` "误差值取反" negate errors, `cp14` "正反向交换" swap directions, `cp15` "全程平均反向间隙 (mm)：%.4f", `cp16` "调整平均反向间隙 (mm)：", `cp5` file filter "干涉仪文件(*.rtl;*.ren;*.pos;*.csv;)" laser-interferometer files, `cp30..33` "非法文件路径/空文件/文件被损坏/非LI文件" invalid path / empty / broken / not an LI (laser-interferometer) file) reloads the same path at `0x5013bb` through vtable `+0x2ac` and prompts `"是否保存文件？"` ("save file?"). The dialog has an X/Y axis selector (`cp0/cp1`) and `hp34` "导入补偿数据" (import compensation data). At start-up the loader also derives per-slot soft-limit bounds right after loading (`0x4b2508–0x4b25b3`: for slots X,Y: `lowerBound = (GoOriginalDirection==0) ? 0 : -SoftLimitMaxLen`).

INFERENCE `[likely]`: the file is `scFlie` / *N* = number of compensation records / *N* records / `eof`; with `N = 0` here (**no pitch-compensation table has ever been generated on this machine**; consistent with `MP.CompensateType=1` = backlash-only and both backlash values = 0). The per-record layout (axis, position, +error, −error — the columns the dialog edits, printed with `"%.4lf"`) could not be confirmed because no non-empty sample exists — see Open questions. Import is from interferometer exports (Renishaw `.rtl/.ren`, `.pos`, `.csv`); a Linux port can regenerate this table from its own measurement workflow.

### 6.2 `File/ManuContour.dat` and `File/Temp/ManuContour.dat`

`File/ManuContour.dat`: `scFlie / 24 / 0 / 1 / 2 / … / 23 / eof`. `File/Temp/ManuContour.dat`: `scFlie / 2 / 0 / 0 / eof`.

EVIDENCE: written by the function at `0x436c1d` ("save contour File" log tag, `SaveIndex` step name in the job-preparation pipeline `GetCtGly → InitCtData → DeleteFile → SaveIndex`; strings at `.rdata` `0x7d07fc–0x7d0854`): open for write, write *count*, then in a loop write one integer per contour, `eof`. Read back by the break-point finder at `0x43c5b1` (`"find break point between Contours"`, `"find break point in Contour"`, `"read index:%d(%d + %d = %d)"`).

INFERENCE `[confirmed]`: it is the **ordered list of contour (glyph) indices of the current job as it was sent to the controller** — the machining order (24 contours in the last job; the Temp snapshot had 2 contours, both index 0 — i.e. one contour cut twice / a two-item job). It exists so that after an abnormal exit the app can map the controller's "current contour number" (`RegName47` "当前执行轮廓序号") back to a graph object.

### 6.3 `AutosaveParam1.ini` / `AutosaveParam2.ini` / `Temp/tempIsBreak.ini` (break-point resume)

`File/AutosaveParam1.ini`: `scFlie / 38 / 979454 / 342126 / 7 / 486 / eof`; `AutosaveParam2.ini`: `scFlie / 37 / 965189 / 340285 / 7 / 132 / eof`. Temp copies: `30 / 89243 / 69837 / 1 / 4950` and `31 / 88331 / 71462 / 1 / 5323`. `Temp/tempIsBreak.ini`: `scFlie / 1 / 1 / eof`.

EVIDENCE: writer at `0x446c50` (only when the machining state `g+0x47cc == 2`): line 1 = global `ds:0x9495ac` (current item index), line 2 = `round(x * 1000.0)` (`fmul ds:0x7d0ec8` = 1000.0, then `0x4511d0` double→int), line 3 = `round(y * 1000.0)`, lines 4–5 two more integers, then `eof`. Reader (`0x43c9a2–0x43ca9a`, two copies for Param1/Param2): `openRead`, `readInt`, `readDouble`, `readDouble`, `readInt`, `readInt`; the two doubles are then scaled by `0x7d0eb8` = **0.001** (`0x43ca47`) into a point. Diagnostic format `"read Index:%d -- read point:(%d,%d) -- Graph Index:%d"`. `mf147`-adjacent messages: `"Graph Index file was error!"`, `"Broken point file was error!"`.

INFERENCE `[confirmed]` for fields 1–4, `[likely]` for field 5: `AutosaveParamN.ini` = break-point record *N* (two alternating slots so that a crash while writing one leaves the other intact): **[1] contour/item index in `ManuContour.dat` order, [2] X position in 0.001 mm, [3] Y position in 0.001 mm, [4] graph (glyph) index, [5] interpolation-point index inside the contour** (`"read index:%d(%d + %d = %d)"` adds an offset to it). Example: the last job stopped at contour 38, at (979.454 mm, 342.126 mm), graph 7, point 486. `tempIsBreak.ini` = `isBreak` flag `1` + a second flag (`[likely]` "break-point valid"). The `File/Temp/` set (`tempGraph.chf`, `tempLayer.xml`, `tempIsBreak.ini`, `tempManu.ini`, `ManuContour.dat`, `AutosaveParam*.ini`) is the *task package* staging area: the "导入任务/导出任务" (import/export task, `A250320_*`) feature bundles exactly these files with the section tags `TASK_MANU`, `TASK_PARAM_ONE`, `TASK_PARAM_TWO`, `TASK_IS_BREAK`, `TASK_LAYER` (ASCII tags at `0x7db638…`, function `0x4686xx`).

### 6.4 `File/ProcessesStatistic.txt`

GBK text, one line: `未命名-1,0.0X0.0mm,0mm,0mm,0,0秒,1970-01-01 08:00:00` → `<job name ("Untitled-1")>,<width>X<height>mm,<cut length>,<idle-move length>,<pierce count>,<time s>,<start timestamp>` — the same record as `Report/LogReport.txt` and `Report/TotalReport.txt` (which add `…,<cut s>,<idle s>,<pierce s>` in the 2025 format, cf. `gp2000/gp2001`: "加工总长/空走总长/穿孔数", "切割时间/空跳时间/穿孔时间/系统延时/总用时"). This file is the *last-job* statistics snapshot used by `Report/report.exe`; the shipped one is an empty placeholder (epoch date). INFERENCE `[likely]`.

### 6.5 `JumpAddTime.txt` (package root)

```
[Jump]            AddTime=200        ; ms added to every frog-jump/lift time (code: GetPrivateProfileInt("Jump","AddTime",100)·0.001 s)
[Axis4Freq]       Is4Freq=0          ; byte g+0x4cfd: encoder 4x multiplication for the 4th axis (read at 0x4afeb6; default 0)
[LimitSamllCircleVel] IsLimit=0 SlowRatio=3   ; small-circle speed limiting (cf. MP.EnableSmallCircleSpeedLimit / SmallCircleSpeedLimitRatio)
[Arc2SegVelK]     K_X=100 K_Y=100    ; arc→segment velocity gain per axis (%), MotionCtrl.dll planner tuning
```
EVIDENCE for the first two: disassembly at `0x443b7b` and `0x4afeb6`. The last two sections' key names do not appear in MainApp's string tables (`IsLimit`, `SlowRatio`, `K_X`, `K_Y` not found in MainApp.exe or MotionCtrl.dll strings) — INFERENCE `[guess]`: they are read by a helper not covered here or are dead; treat as developer tuning knobs.

---

## 7. Open questions

1. **Axis-slot → role mapping** (`PMachineAxisConfig_0..4` ⇔ X/Y1/Y2/Z/W): inferred from `A250516_0..4` and values; the global index fields `g+0xba5c/60/64/68` are only read, never written, in MainApp. Verify on the running system (Advanced page "运动轴配置" `AllAxisInfo`) or in ControlModule/NCModule.
2. **Dual-drive Y**: `A1/A2.DoubleDriver=1` vs `MAC.DoubleDevice=0`; `MAC_2` (Y2) has no limit inputs. Which flag the firmware honours decides whether the gantry has one or two Y motors.
3. **Units of the card position registers**: the 0.001 mm interpretation of `softPara.ini` values is consistent but not proven from the protocol; confirm by reading `AxisRORegName_3` live (protocol analyst).
4. **`PithCompensate.pcf` record layout**: no non-empty sample; the loader lives behind vtable `+0x2ac/+0x2b0` of the HAL object — locate its implementation (probably in MainApp near `0x5028b0`/`0x502980`/`0x502b40`) to confirm `(pos, +err, −err)` per row and whether X and Y tables are concatenated.
5. **DO/DI port counts**: descriptor maxima are 26 (DO), 28 (DI), 16 (extended DO); the physical MCC100 count (16 in / 16 out?) should be confirmed from the register map (`RORegName_5/6`, `RORegName_22/23` "拓展输入/输出状态").
6. **`AutosaveParam` field 5** (486 / 132 / 4950 / 5323): interpreted as the interpolation-point index within the contour; confirm with the NC item stream.
7. **`JumpAddTime.txt` sections 3–4** (`LimitSamllCircleVel`, `Arc2SegVelK`): no reader found in MainApp or MotionCtrl.dll string tables.
8. **`LaserControlType=3 (IO)` + `DOLaserGate=5`/`DORedLight=6`** while the machine is CO2: are DO5/DO6 physically wired (e.g. a red-dot pointer diode)? Needs bench check.
9. Whether `HPA3` (4th-axis homing) applies to W (lifting table) when `A3.EnableType=2` (invalid) but `LiftingPlatformType=1`.

## 8. Implications for the Linux port

What must be replicated faithfully:

* **The parameter model**: two flat attribute-per-element XML files, section/element/attribute names exactly as listed in §1 (the exe accepts nothing else; the descriptor table also gives min/max/default so a port can validate). Keep the primary/backup/second-backup write strategy (write temp → rename, keep two generations) — the vendor software recovers from truncated files this way, and users' exported `.xml` parameter files (`hp9` filter `*.xml`) will be exchanged with the Windows tool.
* **Units**: internal SI (mm, mm/s, mm/s², ms, Hz, %, bar, V); display units selected by `UN.*`. Pulse equivalent is *derived* (`WritePluse / SpeedRatio`), not stored.
* **Enumerations** exactly as decoded in §1 (they are transmitted to the card as integers, e.g. `LaserControlType`, `CO2LaserControlType`, `LaserDAType`, `SampleType`, direction flags); note the mistranslated direction labels.
* **The scFlie container** (`scFlie` / values / `eof`, CRLF) for `ManuContour.dat`, `AutosaveParam*.ini`, `tempIsBreak.ini`, `PithCompensate.pcf` and the `.chf` job format — trivial to implement; needed if break-point resume and task-package import/export are to stay compatible.
* **Break-point and position memory semantics**: positions in 0.001 mm integers; `NormalExit` flag protocol in `softPara.ini`.
* The **I/O function → port** mapping and gas-valve/proportional-valve/PWM model of §2.5, §2.7, §2.8: this is the machine's electrical contract and must be configurable identically (DO1 alarm lamp, DO2 high-N2, DO3 high-air, DO7 low-O2, DO9 CO2 laser enable, DA2 O2 proportional 0–10 V ≙ 0–10 bar, 5 V PWM for tube power, DI limits 5/6, 7/8, 9/10, 2/3, DI11 chiller NC alarm, DI4 door).
* Homing recipe: negative direction onto the negative limit switch, fast 80 / slow 20 mm/s, back-off 22 mm (X) / 15 mm (Y); soft limits 1371/950.

What can be replaced by existing Linux / open-source pieces:

* XML parsing: `ParaModule.dll` is a plain XML/property-tree library (its string table is only an HTML-entity/encoding table) — any XML library (pugixml, libxml2, Python `xml.etree`) is a drop-in.
* INI parsing (`softPara.ini`, `ipAdd.ini`, `JumpAddTime.txt`): Windows `GetPrivateProfile*` semantics → any INI reader.
* `IPSet.exe` (netsh) → `nmcli`/`ip addr`.
* The wireless pendant (`PHBX.dll`, XHC PHB02, USB-HID VID 0x3689 PID 0x8762): Linux `hidraw`/`hidapi`; the WHB04B/PHB04 family HID reports are documented in open-source projects (e.g. LinuxCNC `xhc-whb04b-6` driver) and can serve as a starting point — but only if `RemoteType=3` is to be supported.
* Pitch/backlash compensation: LinuxCNC's `comp` / screw-compensation file formats already model `(position, +err, −err)` per axis; conversion from interferometer `.rtl/.ren/.csv` exists in open tooling.
* Remote monitoring (`MonitorIP` HTTP POST) is optional and can be dropped.
* Language files: `Lang/lang.txt` (`ID#zh#en`) and the per-language `ID#text` files can be loaded as-is (UTF-16LE) or converted to gettext.

Not replaceable (must be re-implemented against the card): everything that turns these parameters into MCC100 register writes (`RegName*`, `AxisRWRegName*`, `SystemRWRegName*`) — covered by the protocol analysis.
