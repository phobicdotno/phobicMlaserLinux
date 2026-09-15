"""Planner parameter block P0..P9 built from the vendor settings (A6 §1.2-§1.8, §4.1; 05 §7.1).

The block is the 10-double ``CInterpMrg+0x88..+0xd0`` array handed to ``CVelocityPlanning``
(05 §8).  Its sources were closed statically in A6 (11-static-findings O12):

==  =====================  ========================================================  ==========
P   planner field          source (main cut flow ``0x437cb0``)                        CF1390
==  =====================  ========================================================  ==========
P0  acceleration A         ``MC.ManuAcc``                                            6000 mm/s²
P1  acceleration time Ta   ``MC.AccTime * 0.001`` (MainApp ``0x435fa7-0x435fb9``)     0.2 s
P2  precision c            ``MC.SplineAccuracyRate`` (not CornerAccuracyRate!)       0.02 mm
P3  Vmax                   ``min(layer.CutSpeed, FCP.MaxSpeed, 750000/K)``           per layer
P4  speed floor            constant 0 (never written, A6 §1.3)                        0
P5  interpolation period   ``AX.InterpolationCycle * 0.001``                          0.25 (ms)
P6  slow-start length      ``UD_UpEnable and P3 > P7 ? UD_UpLen : 0``                 per layer
P7  slow-start speed       ``UD_UpSpeed``                                             per layer
P8  end-segment length     ``UD_DownEnable and P3 > P9 ? UD_DownLen : 0``             per layer
P9  end-segment speed      ``UD_DownSpeed``                                           per layer
==  =====================  ========================================================  ==========

plus ``a10 = MC.CornerAccuracyRate`` (``CInterpMrg+0x50``) and the start glyph index
(``CInterpMrg+0xd8``, 0 for a fresh job, A6 §1.8).  The Simulate flow (``0x43a5c0``, A6 §1.1
verifier V1) uses ``min(CutSpeed, 750000/K)`` and ``GP.SlowStart*`` for P6/P7 instead.

Also here: the rapid-move block (A6 §1.4/§4.1), the slot-122 block copied into
``CInterpMrg+0x08..+0x47`` (A6 §1.4 verifier V3) and ``JumpAddTime.txt`` (05 §3, §8).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import astuple, dataclass
from pathlib import Path
from typing import Literal

from nexcut.core.schema import Value
from nexcut.io.params import ParamDocument

__all__ = [
    "ACC_TIME_SCALE",
    "CARD_RATE_CEILING",
    "INTERP_SCALE",
    "InterpExtras",
    "JumpAddTime",
    "Laser",
    "PlannerParams",
    "RapidParams",
    "document_value",
    "laser_from_manu",
    "layer_values",
    "parse_jump_add_time",
    "planner_params",
    "planner_params_from_values",
    "rapid_params",
    "rapid_params_from_values",
    "read_jump_add_time",
    "interp_extras",
    "velocity_ceiling",
]

Laser = Literal["fiber", "co2"]
Flow = Literal["cut", "simulate"]

ACC_TIME_SCALE = 0.001
"""``fmul ds:0x7d0eb8`` (= 0.001) applied to ``MC.AccTime``/``EmptyMoveAccTime`` in MainApp (A6 §1.7)."""
INTERP_SCALE = 0.001
"""``AX.InterpolationCycle`` (µs, ``fild``) x 0.001 -> P5 (A6 §1.4)."""
CARD_RATE_CEILING = 750000.0
"""``this+0x48 = 750000 / K`` (const ``0x7d0ec0``, A6 §1.3/§1.4)."""
DEFAULT_K = 1000
"""K = card units per mm = SystemRW word 17 (reg 50017); 1000 on the CF1390 (A1 §2, 11 C4/C5).
The card is authoritative: pass the value read from the card when connected."""
LAYER_INDEX_MAX = 10
"""The main flow clamps the item's layer index to ``[0, 10]`` (A6 §1.5, ``0x437f9b-0x437fe2``)."""


@dataclass(frozen=True, slots=True)
class PlannerParams:
    """Planner block P0..P9 + ``a10``/start index (A6 §1.6; roles per 05 §7.1 as corrected by A6).

    ``acc_time`` is stored as delivered (seconds, not clamped); ``plan`` clamps it to
    ``[0.06, 0.25]`` (05 §7.1) - see :attr:`effective_acc_time`.
    """

    acc: float  # P0 [mm/s^2]
    acc_time: float  # P1 [s]
    precision: float  # P2 [mm] - junction formula c (MC.SplineAccuracyRate)
    vmax: float  # P3 [mm/s]
    min_speed: float = 0.0  # P4 [mm/s] floor (max), inert at 0
    interp_period: float = 0.25  # P5 [ms] (AX.InterpolationCycle*0.001)
    slow_start_len: float = 0.0  # P6 [mm]
    slow_start_speed: float = 10.0  # P7 [mm/s]
    slow_end_len: float = 0.0  # P8 [mm]
    slow_end_speed: float = 200.0  # P9 [mm/s]
    corner_precision: float = 0.05  # a10 -> CInterpMrg+0x50 (MC.CornerAccuracyRate)
    start_index: int = 0  # int -> CInterpMrg+0xd8

    def block(self) -> tuple[float, ...]:
        """The ten doubles P0..P9 in ``CInterpMrg+0x88`` order (05 §8)."""
        return astuple(self)[:10]

    @classmethod
    def from_block(
        cls, block: Sequence[float], corner_precision: float = 0.05, start_index: int = 0
    ) -> PlannerParams:
        """Inverse of :meth:`block`; an 8-double MotionCtrl block gets P8 = 0, P9 = 200."""
        vals = [*block, 0.0, 200.0] if len(block) == 8 else list(block)
        if len(vals) != 10:
            raise ValueError("block must hold 8 or 10 doubles")
        return cls(*map(float, vals), corner_precision=corner_precision, start_index=start_index)

    @classmethod
    def motionctrl_defaults(cls) -> PlannerParams:
        """``newVelocityPlanning`` defaults {2000, 0.125, 0.05, 200, 0, 1, 0, 10} (05 §7.1,
        ``.data:0x1001c288``); P8/P9 are not part of the 8-double block (0 / 200 assumed)."""
        return cls.from_block((2000.0, 0.125, 0.05, 200.0, 0.0, 1.0, 0.0, 10.0))

    @classmethod
    def cadmodule_defaults(cls) -> PlannerParams:
        """``CInterpMrg`` default ctor block {2000, 0.125, 0.05, 200, 0, 1, 0, 200, 0, 200} (05 §8)."""
        return cls.from_block((2000.0, 0.125, 0.05, 200.0, 0.0, 1.0, 0.0, 200.0, 0.0, 200.0))

    @classmethod
    def mainapp_defaults(cls) -> PlannerParams:
        """``CGraphNcDataEng`` cut-block ctor defaults permuted into P order (A6 §1.3):
        P0 80000, P1 0.125, P2 0.05, P3 999, P4 0, P5 0.25, P6 0, P7 200, P8 0, P9 200, a10 0.1."""
        return cls(80000.0, 0.125, 0.05, 999.0, 0.0, 0.25, 0.0, 200.0, 0.0, 200.0, 0.1)

    @property
    def effective_acc_time(self) -> float:
        """``Ta`` clamped to ``[0.06, 0.25]`` s as ``plan`` does (05 §7.1)."""
        return min(max(self.acc_time, 0.06), 0.25)

    @property
    def jerk(self) -> float:
        """``J = 2A/Ta`` if ``A*Ta/2 < Vmax`` else ``4 Vmax/Ta^2`` with the clamped ``Ta`` (05 §7.1)."""
        ta = self.effective_acc_time
        if self.acc * ta / 2.0 < self.vmax:
            return 2.0 * self.acc / ta
        return 4.0 * self.vmax / (ta * ta)

    @property
    def mainapp_jerk_field(self) -> float:
        """``2*P0/P1`` stored at ``this+0x108`` (a12, *unused* by the wrapper; A6 §1.3/§1.4)."""
        return 2.0 * self.acc / self.acc_time


@dataclass(frozen=True, slots=True)
class RapidParams:
    """Rapid-move block ``this+0x50`` (A6 §1.4, §4.1) - not passed to CADModule by the vendor."""

    vmax: float  # min(XFastMoveSpeed*EmptyMoveSpeedFactor, 750000/K)
    acc: float  # XFastMoveAcc*EmptyMoveAccFactor
    acc_time: float  # EmptyMoveAccTime*0.001
    interp_period: float  # InterpolationCycle*0.001

    @property
    def jerk_field(self) -> float:
        """``2*acc/acc_time`` (``this+0xa8``, A6 §1.4)."""
        return 2.0 * self.acc / self.acc_time


@dataclass(frozen=True, slots=True)
class InterpExtras:
    """Slot-122 block copied into ``CInterpMrg+0x08..+0x47`` (A6 §1.4 verifier V3)."""

    drill_in_micro_link: bool  # GP.IsDrillInMicoLink (pd595_1)
    micro_link_decel: bool  # GP.EnableMicroLinkDecc (pd594)
    micro_link_slow_speed: float  # GP.MicoLinkSlowDownVel (pd595)
    small_circle_limit: bool  # MP.EnableSmallCircleSpeedLimit (pd1560)
    small_circle_ratio: float  # MP.SmallCircleSpeedLimitRatio (pd1561)
    flycut_circle_vel_ratio: float  # FCP.FlycutCircleVelRatio (pd290)
    flycut_circle_pwm_delay: float  # FCP.FlycutCirclePwmDelayTime (pd513)
    flycut_line_open_forward_cycle: float  # FCP.FlycutLineOpenPwmForwardCycle (pd514)
    flycut_line_close_forward_cycle: float  # FCP.FlycutLineColsePwmForwardCycle (pd515)


# ------------------------------------------------------------------------ value lookup
def document_value(doc: ParamDocument, key: str) -> Value:
    """``"Elem.Attr"`` from the first group of ``doc`` that holds it (layout order).

    Raises ``KeyError`` when no group has the attribute.
    """
    elem, attr = key.split(".", 1)
    for group in doc.values.values():
        attrs = group.get(elem)
        if attrs is not None and attr in attrs:
            return attrs[attr]
    raise KeyError(f"{doc.kind}: no attribute {key}")


def laser_from_manu(manu: ParamDocument) -> Laser:
    """CO2 layer table when ``SP.m_iEnableLaserType != 0`` (A6 preamble, accessor ``0x437910``)."""
    try:
        return "co2" if int(document_value(manu, "SP.m_iEnableLaserType")) != 0 else "fiber"
    except KeyError:
        return "fiber"


def layer_values(
    layer_doc: ParamDocument, layer_index: int, laser: Laser = "fiber"
) -> dict[str, Value]:
    """``GP`` attributes of layer ``clamp(index, 0, 10)`` (A6 §1.5); slot = index + 1 (02 §4)."""
    idx = min(max(layer_index, 0), LAYER_INDEX_MAX)
    return layer_doc.layer_slot(laser, idx + 1)


def velocity_ceiling(k: int | float = DEFAULT_K) -> float:
    """``750000 / K`` [mm/s] (A6 §1.3); 750 mm/s for K = 1000."""
    if k <= 0:
        raise ValueError("K must be > 0")
    return CARD_RATE_CEILING / float(k)


def _f(v: Value) -> float:
    if isinstance(v, str):
        raise TypeError(f"numeric parameter expected, got {v!r}")
    return float(v)


_UD_DEFAULTS: dict[str, Value] = {
    "UD_UpEnable": 0,
    "UD_UpLen": 10.0,
    "UD_UpSpeed": 100.0,
    "UD_DownEnable": 0,
    "UD_DownLen": 10.0,
    "UD_DownSpeed": 100.0,
}
"""Used when a layer table has no ``GP.UD_*`` descriptors (the CO2 table in the schema has
none).  UNVERIFIED: the in-memory CO2 record values at ``layer+0x380..+0x3b8`` were not traced;
the fibre descriptor defaults (disabled) are assumed."""


def planner_params_from_values(
    g: Mapping[str, Value],
    layer: Mapping[str, Value],
    *,
    k: int | float = DEFAULT_K,
    flow: Flow = "cut",
    run_mode: int = 0,
    start_index: int = 0,
) -> PlannerParams:
    """A6 §4.1 ``planner_block`` with the per-run overrides of A6 §1.5.

    ``g`` maps ``"Elem.Attr"`` -> value (ManuPara + HardPara), ``layer`` maps layer attribute
    names (without ``GP.``) -> value.  ``flow="cut"`` = ``0x437cb0``; ``flow="simulate"`` =
    ``0x43a5c0`` (Simulate button only).  ``run_mode > 1`` (``this+0x110``, pause
    forward/backward jog) takes ``MC.ForwardBackwardSpeed`` instead of ``CutSpeed`` (cut flow).
    """
    vceil = velocity_ceiling(k)
    if flow == "simulate":
        vmax = min(_f(layer["CutSpeed"]), vceil)
        p7 = _f(layer["SlowStartSpeed"])
        p6 = _f(layer["SlowStartLength"]) if int(_f(layer["SlowStart"])) else 0.0
        p9 = 200.0  # untouched: ctor default (A6 §1.5) - UNVERIFIED if a cut run preceded
        p8 = 0.0
    else:
        base = _f(g["MC.ForwardBackwardSpeed"]) if run_mode > 1 else _f(layer["CutSpeed"])
        vmax = min(base, _f(g["FCP.MaxSpeed"]), vceil)
        ud = {**_UD_DEFAULTS, **{k_: v for k_, v in layer.items() if k_ in _UD_DEFAULTS}}
        p7 = _f(ud["UD_UpSpeed"])
        p6 = _f(ud["UD_UpLen"]) if (vmax > p7 and int(_f(ud["UD_UpEnable"]))) else 0.0
        p9 = _f(ud["UD_DownSpeed"])
        p8 = _f(ud["UD_DownLen"]) if (vmax > p9 and int(_f(ud["UD_DownEnable"]))) else 0.0
    return PlannerParams(
        acc=_f(g["MC.ManuAcc"]),
        acc_time=_f(g["MC.AccTime"]) * ACC_TIME_SCALE,
        precision=_f(g["MC.SplineAccuracyRate"]),
        vmax=vmax,
        min_speed=0.0,
        interp_period=_f(g["AX.InterpolationCycle"]) * INTERP_SCALE,
        slow_start_len=p6,
        slow_start_speed=p7,
        slow_end_len=p8,
        slow_end_speed=p9,
        corner_precision=_f(g["MC.CornerAccuracyRate"]),
        start_index=start_index,
    )


_G_KEYS_MANU = (
    "MC.ManuAcc",
    "MC.AccTime",
    "MC.SplineAccuracyRate",
    "MC.CornerAccuracyRate",
    "MC.ForwardBackwardSpeed",
    "MC.XFastMoveSpeed",
    "MC.XFastMoveAcc",
    "MC.EmptyMoveAccTime",
    "GP.IsDrillInMicoLink",
    "GP.EnableMicroLinkDecc",
    "GP.MicoLinkSlowDownVel",
)
_G_KEYS_HARD = (
    "FCP.MaxSpeed",
    "AX.InterpolationCycle",
    "MP.EmptyMoveSpeedFactor",
    "MP.EmptyMoveAccFactor",
    "MP.EnableSmallCircleSpeedLimit",
    "MP.SmallCircleSpeedLimitRatio",
    "FCP.FlycutCircleVelRatio",
    "FCP.FlycutCirclePwmDelayTime",
    "FCP.FlycutLineOpenPwmForwardCycle",
    "FCP.FlycutLineColsePwmForwardCycle",
)


def _settings(manu: ParamDocument, hard: ParamDocument) -> dict[str, Value]:
    g: dict[str, Value] = {}
    for key in _G_KEYS_MANU:
        g[key] = document_value(manu, key)
    for key in _G_KEYS_HARD:
        g[key] = document_value(hard, key)
    return g


def planner_params(
    manu: ParamDocument,
    hard: ParamDocument,
    layer_doc: ParamDocument,
    layer_index: int,
    *,
    laser: Laser | None = None,
    k: int | float = DEFAULT_K,
    flow: Flow = "cut",
    run_mode: int = 0,
    start_index: int = 0,
) -> PlannerParams:
    """Planner block from ManuPara / HardPara / LayerPara documents (A6 §1.4-§1.6).

    Descriptor homes (schema): ``MC.*`` in ManuPara ``PManuParam``; ``FCP.*``, ``AX.*``,
    ``MP.*`` in HardPara; layer ``GP.*`` in ``P[CO2]LayerParam<index+1>``.  ``laser`` defaults
    to the table selected by ``SP.m_iEnableLaserType``.
    """
    lz = laser or laser_from_manu(manu)
    return planner_params_from_values(
        _settings(manu, hard),
        layer_values(layer_doc, layer_index, lz),
        k=k,
        flow=flow,
        run_mode=run_mode,
        start_index=start_index,
    )


def rapid_params_from_values(g: Mapping[str, Value], *, k: int | float = DEFAULT_K) -> RapidParams:
    """Rapid block (A6 §1.4 stores ``+0x50/+0x80/+0x88/+0x90``, §4.1)."""
    return RapidParams(
        vmax=min(
            _f(g["MC.XFastMoveSpeed"]) * _f(g["MP.EmptyMoveSpeedFactor"]), velocity_ceiling(k)
        ),
        acc=_f(g["MC.XFastMoveAcc"]) * _f(g["MP.EmptyMoveAccFactor"]),
        acc_time=_f(g["MC.EmptyMoveAccTime"]) * ACC_TIME_SCALE,
        interp_period=_f(g["AX.InterpolationCycle"]) * INTERP_SCALE,
    )


def rapid_params(
    manu: ParamDocument, hard: ParamDocument, *, k: int | float = DEFAULT_K
) -> RapidParams:
    """Rapid block from documents."""
    return rapid_params_from_values(_settings(manu, hard), k=k)


def interp_extras(manu: ParamDocument, hard: ParamDocument) -> InterpExtras:
    """Slot-122 block (A6 §1.4 V3) from documents."""
    g = _settings(manu, hard)
    return InterpExtras(
        drill_in_micro_link=bool(int(_f(g["GP.IsDrillInMicoLink"]))),
        micro_link_decel=bool(int(_f(g["GP.EnableMicroLinkDecc"]))),
        micro_link_slow_speed=_f(g["GP.MicoLinkSlowDownVel"]),
        small_circle_limit=bool(int(_f(g["MP.EnableSmallCircleSpeedLimit"]))),
        small_circle_ratio=_f(g["MP.SmallCircleSpeedLimitRatio"]),
        flycut_circle_vel_ratio=_f(g["FCP.FlycutCircleVelRatio"]),
        flycut_circle_pwm_delay=_f(g["FCP.FlycutCirclePwmDelayTime"]),
        flycut_line_open_forward_cycle=_f(g["FCP.FlycutLineOpenPwmForwardCycle"]),
        flycut_line_close_forward_cycle=_f(g["FCP.FlycutLineColsePwmForwardCycle"]),
    )


# ------------------------------------------------------------------------ JumpAddTime.txt
@dataclass(frozen=True, slots=True)
class JumpAddTime:
    """``<exe dir>\\JumpAddTime.txt`` (05 §3, §8; LF line endings, no trailing newline).

    ``add_time_ms`` = ``[Jump] AddTime`` added per rapid in time estimates (MainApp);
    ``is_4freq`` = ``[Axis4Freq] Is4Freq`` (MainApp); ``k_x``/``k_y`` = ``[Arc2SegVelK]
    K_X/K_Y`` x 0.01 (CADModule ``CInterpMrg`` ctor ``0x100f5ea0``, default 100).
    ``[LimitSamllCircleVel]`` is read by no binary (05 §8) and kept only for completeness.
    Defaults for AddTime/Is4Freq when absent are UNVERIFIED (MainApp defaults not traced).
    """

    add_time_ms: int = 200
    is_4freq: int = 0
    k_x: float = 1.0
    k_y: float = 1.0
    limit_small_circle: int = 0
    small_circle_slow_ratio: int = 3


_SECTION_RX = re.compile(r"^\s*\[([^\]]*)\]")


def _profile_int(text: str, default: int) -> int:
    """``GetPrivateProfileIntW`` value semantics: optional sign + leading decimal digits; an empty
    value yields the default and a non-numeric one 0 (Wine's implementation; UNVERIFIED against
    Windows)."""
    m = re.match(r"\s*([+-]?\d+)", text)
    return int(m.group(1)) if m else (0 if text.strip() else default)


def parse_jump_add_time(text: str | bytes) -> JumpAddTime:
    """Parse ``JumpAddTime.txt`` with Win32 profile rules (case-insensitive section/key names)."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    data: dict[tuple[str, str], str] = {}
    section = ""
    for raw in text.splitlines():
        m = _SECTION_RX.match(raw)
        if m:
            section = m.group(1).strip().lower()
            continue
        if "=" in raw:
            key, value = raw.split("=", 1)
            data.setdefault((section, key.strip().lower()), value.strip())

    def get(sec: str, key: str, default: int) -> int:
        v = data.get((sec.lower(), key.lower()))
        return default if v is None else _profile_int(v, default)

    return JumpAddTime(
        add_time_ms=get("Jump", "AddTime", 200),
        is_4freq=get("Axis4Freq", "Is4Freq", 0),
        k_x=get("Arc2SegVelK", "K_X", 100) * 0.01,
        k_y=get("Arc2SegVelK", "K_Y", 100) * 0.01,
        limit_small_circle=get("LimitSamllCircleVel", "IsLimit", 0),
        small_circle_slow_ratio=get("LimitSamllCircleVel", "SlowRatio", 3),
    )


def read_jump_add_time(path: str | os.PathLike[str]) -> JumpAddTime:
    """Read ``JumpAddTime.txt``; a missing file yields the defaults (profile API behaviour)."""
    p = Path(path)
    if not p.is_file():
        return JumpAddTime()
    return parse_jump_add_time(p.read_bytes())
