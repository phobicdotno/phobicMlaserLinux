"""Junction / arc speed limits (analysis 05 §7.3, PORT-PLAN §3 ``CVelocityPlanning`` row).

Two models are provided:

* :func:`vendor_arc_speed` - the empirical formula of ``CVelocityPlanning`` function
  ``0x100170e0`` reproduced **verbatim** (05 §7.3, EVIDENCE: full x87 re-derivation by the
  verifier).  Arguments ``(a, b, c)`` = piece radius [mm], ``b = 0.5 / Ta`` [1/s] and the
  precision parameter ``c`` [mm].  Which UI value feeds ``c`` is settled by A6 §1.6: it is
  planner block ``P2`` = ``MC.SplineAccuracyRate`` (0.02 mm on the CF1390), *not*
  ``MC.CornerAccuracyRate`` as 05 §7.1 originally assumed.
* an optional physical model (05 §14 "physically cleaner alternative"):
  :func:`centripetal_speed` ``v = sqrt(a_n * r)`` and :func:`junction_deviation_speed`
  (grbl/Klipper junction deviation).  These are *not* what the vendor does; they are offered
  for experimentation and are selected explicitly through :class:`JunctionModel`.

All functions accept scalars or numpy arrays (vectorised, 05 §12 / PORT-PLAN §2.2 "planner must
be numpy-vectorised").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, overload

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = [
    "BREAK_LINEAR",
    "BREAK_MID",
    "BREAK_HIGH",
    "PRECISION_SPLIT",
    "JunctionModel",
    "centripetal_speed",
    "equivalent_corner_radius",
    "junction_deviation_speed",
    "vendor_arc_speed",
    "vendor_radicand",
]

BREAK_LINEAR = 1.6
"""``a <= 1.6`` -> linear branch ``v0 = 8*b*a`` (05 §7.3, ``test ah,0x41; jp``)."""
BREAK_MID = 6.0
"""``a <= 6`` upper bound of the first square-root branch (05 §7.3, ``test ah,1; jne``)."""
BREAK_HIGH = 12.0
"""``a < 12`` upper bound of the second square-root branch (05 §7.3, ``test ah,0x41; jne``)."""
PRECISION_SPLIT = 0.03
"""``c >= 0.03`` selects the first precision correction (05 §7.3)."""

FloatArray = NDArray[np.float64]


def vendor_radicand(a: ArrayLike, b: ArrayLike) -> FloatArray:
    """Radicand ``a * (...)`` of the three square-root branches of 05 §7.3.

    For ``a <= 1.6`` the value returned is ``(8*b*a)**2`` so that ``sqrt`` of the result is the
    linear branch; the function is C0-continuous at 1.6 / 6 / 12 (05 §7.3 verifier note).
    """
    a_arr = np.asarray(a, dtype=np.float64)
    b_arr = np.asarray(b, dtype=np.float64)
    b2 = b_arr * b_arr
    r1 = a_arr * (102.4 * b2 + (30.0 * b_arr - 50.0) * (a_arr - 1.6))
    r2 = a_arr * (102.4 * b2 + 132.0 * b_arr - 220.0 + 150.0 * (a_arr - 6.0) * (b_arr - 2.0))
    r3 = a_arr * (102.4 * b2 + 1032.0 * b_arr - 2020.0)
    lin = (8.0 * b_arr * a_arr) ** 2
    return np.where(
        a_arr <= BREAK_LINEAR,
        lin,
        np.where(a_arr <= BREAK_MID, r1, np.where(a_arr < BREAK_HIGH, r2, r3)),
    )


@overload
def vendor_arc_speed(a: float, b: float, c: float) -> float: ...
@overload
def vendor_arc_speed(a: ArrayLike, b: ArrayLike, c: ArrayLike) -> FloatArray | float: ...


def vendor_arc_speed(a: ArrayLike, b: ArrayLike, c: ArrayLike) -> FloatArray | float:
    """Speed limit [mm/s] of a piece with radius ``a`` - 05 §7.3 formula verbatim.

    ::

        if a <= 1.6:   v0 = 8*b*a
        elif a <= 6:   v0 = sqrt(a*(102.4 b^2 + (30 b - 50)(a - 1.6)))
        elif a < 12:   v0 = sqrt(a*(102.4 b^2 + 132 b - 220 + 150 (a - 6)(b - 2)))
        else:          v0 = sqrt(a*(102.4 b^2 + 1032 b - 2020))
        if c >= 0.03:  v = v0 + 73.8 (c - 0.05) * min(a, 25 b^2 / (73.8 c - 3.69)^2)
        else:          v = v0 + 15 (30 c - 1)   * min(a, b^2 / (3 - 90 c)^2)

    ``b = 0.5/Ta`` (call site ``0x10016a4a-0x10016a65`` passes ``0.5/[this+0x30]``).  At
    ``c = 0.05`` the first correction is exactly 0 (the ``25 b^2/0`` term is +inf, the product
    with ``c - 0.05 = 0`` is 0), reproduced here without a floating-point warning.

    Deviation (safety, not vendor): a negative radicand or a negative result is clamped to 0 so a
    caller can never receive a NaN/negative speed.  With ``Ta`` inside its clamp ``[0.06, 0.25]``
    (``b`` in ``[2, 8.33]``) and ``c >= 0.01`` neither case occurs (checked in the tests).
    Scalars in -> float out; arrays in -> array out.
    """
    a_arr = np.asarray(a, dtype=np.float64)
    b_arr = np.asarray(b, dtype=np.float64)
    c_arr = np.asarray(c, dtype=np.float64)
    scalar = a_arr.ndim == 0 and b_arr.ndim == 0 and c_arr.ndim == 0
    with np.errstate(divide="ignore", invalid="ignore"):
        rad = vendor_radicand(a_arr, b_arr)
        v0 = np.where(a_arr <= BREAK_LINEAR, 8.0 * b_arr * a_arr, np.sqrt(np.maximum(rad, 0.0)))
        b2 = b_arr * b_arr
        den1 = (73.8 * c_arr - 3.69) ** 2
        q1 = np.where(den1 > 0.0, 25.0 * b2 / np.where(den1 > 0.0, den1, 1.0), np.inf)
        corr1 = 73.8 * (c_arr - 0.05) * np.minimum(a_arr, q1)
        corr1 = np.where(c_arr == 0.05, 0.0, corr1)
        den2 = (3.0 - 90.0 * c_arr) ** 2
        q2 = np.where(den2 > 0.0, b2 / np.where(den2 > 0.0, den2, 1.0), np.inf)
        corr2 = 15.0 * (30.0 * c_arr - 1.0) * np.minimum(a_arr, q2)
        v = v0 + np.where(c_arr >= PRECISION_SPLIT, corr1, corr2)
    v = np.maximum(np.nan_to_num(v, nan=0.0, posinf=np.inf), 0.0)
    return float(v) if scalar else v


def centripetal_speed(radius: ArrayLike, normal_acc: float) -> FloatArray | float:
    """Physical alternative ``v = sqrt(a_n * r)`` (05 §7.3 INFERENCE, 05 §14). Not vendor behaviour."""
    r = np.asarray(radius, dtype=np.float64)
    v = np.sqrt(np.maximum(r, 0.0) * max(normal_acc, 0.0))
    return float(v) if r.ndim == 0 else v


def equivalent_corner_radius(turn_angle: ArrayLike, deviation: float) -> FloatArray | float:
    """Radius of the arc tangent to both legs of a corner whose apex lies ``deviation`` away.

    ``turn_angle`` = change of direction [rad] (0 = straight on, pi = reversal).  Geometry:
    ``r = d * cos(phi/2) / (1 - cos(phi/2))`` - the same construction grbl uses for its
    junction deviation (05 §14).  Returns +inf for a straight junction and 0 for a reversal.
    """
    phi = np.asarray(turn_angle, dtype=np.float64)
    half = np.cos(np.clip(phi, 0.0, np.pi) / 2.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(half >= 1.0, np.inf, deviation * half / (1.0 - half))
    r = np.where(phi >= np.pi, 0.0, r)
    return float(r) if phi.ndim == 0 else r


def junction_deviation_speed(
    turn_angle: ArrayLike, acc: float, deviation: float
) -> FloatArray | float:
    """grbl junction-deviation speed ``v = sqrt(a * r_eq)`` for a sharp corner (05 §14). Not vendor."""
    r = equivalent_corner_radius(turn_angle, deviation)
    with np.errstate(invalid="ignore"):
        v = np.sqrt(np.asarray(r, dtype=np.float64) * max(acc, 0.0))
    return float(v) if np.ndim(v) == 0 else v


@dataclass(frozen=True, slots=True)
class JunctionModel:
    """Selects the per-piece speed limit used by the node builder (05 §7.2 pass 1).

    ``kind="vendor"`` (default) = :func:`vendor_arc_speed` with ``b = 0.5/Ta`` and ``c = P2``;
    ``kind="physical"`` = :func:`centripetal_speed` with ``normal_acc`` (defaults to the path
    acceleration when None).  UNVERIFIED for the physical model: no vendor evidence, port option.
    """

    kind: Literal["vendor", "physical"] = "vendor"
    normal_acc: float | None = None

    def limit(self, radius: ArrayLike, acc_time: float, precision: float, acc: float) -> FloatArray:
        """Speed limit [mm/s] for each radius (array out)."""
        r = np.atleast_1d(np.asarray(radius, dtype=np.float64))
        if self.kind == "vendor":
            return np.asarray(vendor_arc_speed(r, 0.5 / acc_time, precision), dtype=np.float64)
        a_n = acc if self.normal_acc is None else self.normal_acc
        return np.asarray(centripetal_speed(r, a_n), dtype=np.float64)
