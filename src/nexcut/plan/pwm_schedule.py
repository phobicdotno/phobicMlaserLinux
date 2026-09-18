"""Laser switching along the path and per-tick PWM (05 §5.2, §12; A6 §2.5; 02 §3.4; A3 §3).

* **Toggle positions** (05 §5.2): the laser switches at the path positions where the PWM state
  of consecutive PWM segments changes; the first "on" at 0 and the end of path are implicit.
  ``closePwmPosRatios.txt`` = positions / total length written with ``%g`` and CRLF - the
  48 − 2 = 46 toggles of the 24-segment raster.  The ratios are taken over the glyph lengths of
  ``linkFlyLine_pathGlys.txt`` (pre-refit), EVIDENCE: they reproduce byte for byte only with the
  connector length 2.65861, not with the refit 2.66069.
* **PWM pairs** (A6 §2.5): ``close[2i] = a_i - 0.5·b_i/L``, ``close[2i+1] = a_i + 0.5·b_i/L``
  (centre ratio ``a_i``, segment length ``b_i`` mm).
* **Speed compensation tables** (05 §5.2): ``GRP.CO2ScanFlyCompensateStr`` /
  ``FiberScanFlyCompensateStr`` = ``speed#lead`` pairs [mm/s -> mm].  ``pwmCompensation.txt`` /
  ``Co2_pwmCompensation.txt`` are reference copies never read by a binary (A7 §8, 11 C17); the
  live XML is authoritative.  How the lead is applied (interpolation between nodes, sign) is
  UNVERIFIED: the switching point is moved *earlier* by ``lead(v)`` (latency compensation, 05 §5.2
  INFERENCE high) with linear interpolation clamped at the table ends.
* **Forward cycles** ``FCP.FlycutLineOpenPwmForwardCycle`` / ``…ColsePwmForwardCycle`` = −3
  (05 §5.2): PWM on/off moved by that many interpolation cycles; negative = earlier (UNVERIFIED
  sign convention).
* **Power / frequency vs speed** (A3 §3, 02 §3.4): CADModule ``0x100f4e90`` (duty, u8) /
  ``0x100f4f80`` (freq, u16) evaluate ``y0 + (v - x0)·slope + 0.5`` truncated (round half up) on a
  piecewise-linear table; when ``v`` equals the nominal speed the constant is used.  Nodes are
  ``(speed % of CutSpeed, output % of CutDuty/CutFreq)`` pairs (02 §3.4 INFERENCE high); the
  table construction from the node string and the smoothing types 1/2 are UNVERIFIED (linear).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = [
    "CompensationTable",
    "CurveNodes",
    "LayerLaser",
    "close_pwm_ratios",
    "format_ratio_dump",
    "laser_mask",
    "parse_ratio_dump",
    "pwm_pair_ratios",
    "read_compensation_file",
    "toggle_positions",
]

FloatArray = NDArray[np.float64]


# ------------------------------------------------------------------------------ toggles
def toggle_positions(
    lengths: Sequence[float] | FloatArray, laser_on: Sequence[bool]
) -> list[float]:
    """Path positions where the laser state changes between consecutive PWM segments (05 §5.2).

    The start (0) and the end of the path are not listed.  Positions are accumulated with plain
    double addition in segment order (the dump values are printed with ``%g`` only).
    """
    if len(lengths) != len(laser_on):
        raise ValueError("lengths and laser_on differ in size")
    out: list[float] = []
    s = 0.0
    prev: bool | None = None
    for ln, on in zip(lengths, laser_on, strict=True):
        if prev is not None and bool(on) != prev:
            out.append(s)
        prev = bool(on)
        s += float(ln)
    return out


def close_pwm_ratios(
    lengths: Sequence[float] | FloatArray, laser_on: Sequence[bool]
) -> list[float]:
    """``closePwmPosRatios``: toggle positions divided by the total length (05 §5.2)."""
    total = math.fsum(float(v) for v in lengths)
    if total <= 0.0:
        return []
    return [p / total for p in toggle_positions(lengths, laser_on)]


def format_ratio_dump(ratios: Sequence[float]) -> bytes:
    """One ``%g`` value per line, CRLF (text-mode ``ofstream``, like the other CADModule dumps)."""
    return "".join(f"{float(r):g}\r\n" for r in ratios).encode("ascii")


def parse_ratio_dump(data: bytes | str) -> list[float]:
    """Inverse of :func:`format_ratio_dump`."""
    text = data.decode("ascii") if isinstance(data, bytes) else data
    return [float(t) for t in text.split()]


def pwm_pair_ratios(
    centres: Sequence[float], lengths_mm: Sequence[float], total: float
) -> list[float]:
    """``0x10065450``: ``[a_i - 0.5 b_i/L, a_i + 0.5 b_i/L]`` per ``(centre ratio, length mm)``
    node of ``<PWM Control>`` (A6 §2.5)."""
    if total <= 0.0:
        raise ValueError("contour length must be > 0")
    out: list[float] = []
    for a, b in zip(centres, lengths_mm, strict=True):
        half = 0.5 * float(b) / total
        out.extend((float(a) - half, float(a) + half))
    return out


# ------------------------------------------------------------------------------ compensation
_PAIR_TXT = re.compile(r"^\s*([-+\d.eE]+)\s*,\s*([-+\d.eE]+)\s*$")


@dataclass(frozen=True, slots=True)
class CompensationTable:
    """Speed [mm/s] -> switching-point lead [mm] (05 §5.2)."""

    speeds: tuple[float, ...]
    leads: tuple[float, ...]

    @classmethod
    def parse(cls, text: str) -> CompensationTable:
        """Parse ``"100#0.22,200#0.3,…"`` (XML) or ``"100,0.364\\n200,0.56…"`` (txt copies)."""
        pairs: list[tuple[float, float]] = []
        if "#" in text:
            for tok in text.split(","):
                tok = tok.strip()
                if not tok:
                    continue
                a, _, b = tok.partition("#")
                pairs.append((float(a), float(b)))
        else:
            for line in text.splitlines():
                if not line.strip():
                    continue
                m = _PAIR_TXT.match(line)
                if not m:
                    raise ValueError(f"bad compensation line {line!r}")
                pairs.append((float(m.group(1)), float(m.group(2))))
        pairs.sort()
        return cls(tuple(p[0] for p in pairs), tuple(p[1] for p in pairs))

    def format_xml(self) -> str:
        """``speed#lead`` string as stored in ``BkManuPara.xml`` (``%g``)."""
        return ",".join(f"{s:g}#{v:g}" for s, v in zip(self.speeds, self.leads, strict=True))

    def lead(self, speed: ArrayLike) -> FloatArray:
        """Lead at ``speed``; linear between nodes, clamped outside (UNVERIFIED rule)."""
        v = np.asarray(speed, dtype=np.float64)
        if not self.speeds:
            return np.zeros_like(v)
        return np.interp(v, self.speeds, self.leads)


def read_compensation_file(path: str | Path) -> CompensationTable:
    """Read ``pwmCompensation.txt`` / ``Co2_pwmCompensation.txt`` (reference data, A7 §8)."""
    return CompensationTable.parse(Path(path).read_text(encoding="ascii"))


def laser_mask(
    s: ArrayLike,
    v: ArrayLike,
    toggles: Sequence[float],
    *,
    start_on: bool = True,
    compensation: CompensationTable | None = None,
    open_forward_cycles: int = 0,
    close_forward_cycles: int = 0,
) -> NDArray[np.bool_]:
    """Laser state per interval of a sampled contour (``len(s) - 1`` values).

    Interval ``k`` spans samples ``k..k+1``; its state is the state at the abscissa of sample
    ``k+1`` after moving every toggle earlier by ``lead(v)`` and then by the forward cycles
    (open toggles use ``open_forward_cycles``, close toggles ``close_forward_cycles``;
    negative = earlier).  Port design on top of the evidenced inputs (05 §5.2), UNVERIFIED.
    """
    ss = np.asarray(s, dtype=np.float64)
    vv = np.asarray(v, dtype=np.float64)
    n = len(ss) - 1
    if n <= 0:
        return np.zeros(0, dtype=bool)
    state = np.full(n, bool(start_on))
    on = bool(start_on)
    # interval end abscissa
    ends = ss[1:]
    for pos in toggles:
        on = not on
        idx = int(np.searchsorted(ends, pos, side="right"))
        if compensation is not None and 0 <= idx < n:
            lead = float(compensation.lead(vv[min(idx + 1, len(vv) - 1)]))
            idx = int(np.searchsorted(ends, pos - lead, side="right"))
        idx += open_forward_cycles if on else close_forward_cycles
        idx = min(max(idx, 0), n)
        state[idx:] = on
    return state


# ------------------------------------------------------------------------------ power curves
@dataclass(frozen=True, slots=True)
class CurveNodes:
    """``PWMCurveNodes`` / ``FreqCurveNodes``: ``(speed %, output %)`` pairs (02 §3.4)."""

    x: tuple[float, ...]
    y: tuple[float, ...]

    @classmethod
    def parse(cls, text: str) -> CurveNodes:
        """``"0,48,22,66,50,87,100,100"`` -> nodes; empty string = flat 100 % (UNVERIFIED)."""
        vals = [float(t) for t in text.replace(";", ",").split(",") if t.strip()]
        if not vals:
            return cls((0.0, 100.0), (100.0, 100.0))
        if len(vals) % 2:
            raise ValueError(f"odd number of values in curve {text!r}")
        pairs = sorted(zip(vals[0::2], vals[1::2], strict=True))
        return cls(tuple(p[0] for p in pairs), tuple(p[1] for p in pairs))

    def evaluate(self, speed: ArrayLike, nominal_speed: float, base: float) -> NDArray[np.int64]:
        """``trunc(y0 + (v - x0)·slope + 0.5)`` in absolute units; ``v == nominal`` -> ``base``
        rounded the same way.  ``v`` is clamped to the node range (UNVERIFIED)."""
        v = np.asarray(speed, dtype=np.float64)
        if nominal_speed <= 0.0:
            return np.full(v.shape, int(base + 0.5), dtype=np.int64)
        xs = np.asarray(self.x) * nominal_speed / 100.0
        ys = np.asarray(self.y) * base / 100.0
        y = np.interp(np.clip(v, xs[0], xs[-1]), xs, ys)
        out = np.floor(y + 0.5).astype(np.int64)
        out[v == nominal_speed] = int(math.floor(base + 0.5))
        return out


@dataclass(frozen=True, slots=True)
class LayerLaser:
    """Laser settings of one layer record (CO2 table 02 §2.3; fibre uses ``CutPower``)."""

    cut_speed: float
    duty: int
    freq: int
    power_adjust: bool = False
    freq_adjust: bool = False
    power_curve: CurveNodes = CurveNodes((0.0, 100.0), (100.0, 100.0))
    freq_curve: CurveNodes = CurveNodes((0.0, 100.0), (100.0, 100.0))
    laser_on_delay_ms: float = 0.0
    gas_type: int = 0
    laser_off_before_ms: float = 0.0
    """``GP.LaserOffBeforeDelay`` (pd137): wait before the laser DO goes off (A9 §2.2).
    Absent from this machine's CO2 layer XML, so 0 - which emits no record."""
    laser_off_after_ms: float = 0.0
    """``GP.LaserOffAfterDelay`` (pd138): wait after the laser DO goes off (A9 §2.2)."""

    @classmethod
    def from_layer(cls, layer: Mapping[str, float | int | str]) -> LayerLaser:
        """From :func:`nexcut.plan.params.layer_values` (CO2: ``CutDuty``; fibre: ``CutPower``)."""
        duty = layer.get("CutDuty", layer.get("CutPower", 0))
        return cls(
            cut_speed=float(layer.get("CutSpeed", 0.0)),
            duty=int(float(duty)),
            freq=int(float(layer.get("CutFreq", 0))),
            power_adjust=bool(int(float(layer.get("PowerAdjustWithSpeed", 0)))),
            freq_adjust=bool(int(float(layer.get("FreqAdjustWithSpeed", 0)))),
            power_curve=CurveNodes.parse(str(layer.get("PWMCurveNodes", ""))),
            freq_curve=CurveNodes.parse(str(layer.get("FreqCurveNodes", ""))),
            laser_on_delay_ms=float(layer.get("LaserOnDelay", 0.0)),
            gas_type=int(float(layer.get("CutGasType", 0))),
            laser_off_before_ms=float(layer.get("LaserOffBeforeDelay", 0.0)),
            laser_off_after_ms=float(layer.get("LaserOffAfterDelay", 0.0)),
        )

    def tick_pwm(
        self, speed: ArrayLike, laser_on: ArrayLike | None = None
    ) -> tuple[NDArray[np.int64], NDArray[np.int64]]:
        """``(freq, duty)`` per interval; duty 0 where ``laser_on`` is False.  Values are clamped to
        the u16/u8 record fields (A3 §2)."""
        v = np.asarray(speed, dtype=np.float64)
        if self.power_adjust:
            duty = self.power_curve.evaluate(v, self.cut_speed, self.duty)
        else:
            duty = np.full(v.shape, self.duty, dtype=np.int64)
        if self.freq_adjust:
            freq = self.freq_curve.evaluate(v, self.cut_speed, self.freq)
        else:
            freq = np.full(v.shape, self.freq, dtype=np.int64)
        duty = np.clip(duty, 0, 100)
        freq = np.clip(freq, 0, 0xFFFF)
        if laser_on is not None:
            duty = np.where(np.asarray(laser_on, dtype=bool), duty, 0)
        return freq, duty
