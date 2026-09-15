"""Junction / arc speed formula (analysis 05 §7.3)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from nexcut.plan.junction import (
    JunctionModel,
    centripetal_speed,
    equivalent_corner_radius,
    junction_deviation_speed,
    vendor_arc_speed,
    vendor_radicand,
)


def reference(a: float, b: float, c: float) -> float:
    """05 §7.3 transcribed branch by branch (independent of the vectorised implementation)."""
    if a <= 1.6:
        v0 = 8 * b * a
    elif a <= 6:
        v0 = math.sqrt(a * (102.4 * b * b + (30 * b - 50) * (a - 1.6)))
    elif a < 12:
        v0 = math.sqrt(a * (102.4 * b * b + 132 * b - 220 + 150 * (a - 6) * (b - 2)))
    else:
        v0 = math.sqrt(a * (102.4 * b * b + 1032 * b - 2020))
    if c >= 0.03:
        den = (73.8 * c - 3.69) ** 2
        q = 25 * b * b / den if den else math.inf
        return v0 + 73.8 * (c - 0.05) * min(a, q)
    return v0 + 15 * (30 * c - 1) * min(a, b * b / (3 - 90 * c) ** 2)


B_VALUES = [0.5 / ta for ta in (0.06, 0.1, 0.125, 0.2, 0.25)]
C_VALUES = [0.01, 0.02, 0.029, 0.03, 0.05, 0.1, 0.5, 1.0]
A_VALUES = [0.0, 0.01, 0.5, 1.0, 1.6, 1.61, 3.0, 6.0, 6.01, 9.0, 11.99, 12.0, 25.0, 100.0, 1e4]


@pytest.mark.parametrize("b", B_VALUES)
@pytest.mark.parametrize("c", C_VALUES)
def test_matches_reference_transcription(b: float, c: float) -> None:
    for a in A_VALUES:
        assert vendor_arc_speed(a, b, c) == pytest.approx(
            max(reference(a, b, c), 0.0), rel=1e-12, abs=1e-12
        )


def test_vectorised_equals_scalar() -> None:
    a = np.array(A_VALUES)
    for b in B_VALUES:
        for c in C_VALUES:
            vec = vendor_arc_speed(a, b, c)
            assert isinstance(vec, np.ndarray)
            assert np.allclose(vec, [vendor_arc_speed(float(x), b, c) for x in a], rtol=1e-14)


@pytest.mark.parametrize("b", B_VALUES)
def test_radicand_breakpoint_values(b: float) -> None:
    """05 §7.3 verifier: 12.8 b at 1.6 (as v0), 102.4b²+132b-220 at 6, 102.4b²+1032b-2020 at 12."""
    assert math.sqrt(float(vendor_radicand(1.6, b))) == pytest.approx(12.8 * b)
    assert float(vendor_radicand(1.6 + 1e-12, b)) == pytest.approx(1.6 * 102.4 * b * b)
    assert float(vendor_radicand(6.0, b)) / 6.0 == pytest.approx(102.4 * b * b + 132 * b - 220)
    assert float(vendor_radicand(12.0, b)) / 12.0 == pytest.approx(102.4 * b * b + 1032 * b - 2020)


@pytest.mark.parametrize("b", B_VALUES)
@pytest.mark.parametrize("c", C_VALUES)
@pytest.mark.parametrize("x", [1.6, 6.0, 12.0])
def test_continuity_at_breakpoints(b: float, c: float, x: float) -> None:
    """C0 continuity at 1.6 / 6 / 12 mm (PORT-PLAN §9 golden test list)."""
    eps = 1e-9
    lo = vendor_arc_speed(x - eps, b, c)
    hi = vendor_arc_speed(x + eps, b, c)
    at = vendor_arc_speed(x, b, c)
    assert abs(hi - lo) < 1e-5
    assert abs(at - lo) < 1e-5


def test_branch_selection_is_inclusive_as_in_binary() -> None:
    b, c = 2.5, 0.05
    assert vendor_arc_speed(1.6, b, c) == pytest.approx(8 * b * 1.6)  # a <= 1.6 linear
    assert vendor_arc_speed(6.0, b, c) == pytest.approx(
        math.sqrt(6 * (102.4 * b * b + (30 * b - 50) * 4.4))
    )
    assert vendor_arc_speed(12.0, b, c) == pytest.approx(
        math.sqrt(12 * (102.4 * b * b + 1032 * b - 2020))
    )


def test_precision_correction_zero_at_default() -> None:
    """The correction vanishes exactly at c = 0.05 (05 §7.3 INFERENCE) without NaN/inf."""
    for b in B_VALUES:
        for a in A_VALUES:
            v0 = reference(a, b, 0.05)
            assert vendor_arc_speed(a, b, 0.05) == pytest.approx(v0)
            assert math.isfinite(vendor_arc_speed(a, b, 0.05))


def test_first_correction_collapses_for_large_radius() -> None:
    """For a >= Q the c >= 0.03 correction is 25b²/(73.8(c-0.05)), independent of a (05 §7.3)."""
    b, c = 4.0, 0.2
    q = 25 * b * b / (73.8 * (c - 0.05)) ** 2
    for a in (q + 1, q + 50):
        v0 = math.sqrt(a * (102.4 * b * b + 1032 * b - 2020)) if a >= 12 else reference(a, b, 0.05)
        assert vendor_arc_speed(a, b, c) - v0 == pytest.approx(25 * b * b / (73.8 * (c - 0.05)))


def test_centripetal_acceleration_examples() -> None:
    """05 §7.3: b = 4 -> 3746 mm/s², b = 2.5 -> 1200 mm/s² (v² / r for r >= 12)."""
    for b, an in ((4.0, 3746.4), (2.5, 1200.0)):
        r = 50.0
        assert vendor_arc_speed(r, b, 0.05) ** 2 / r == pytest.approx(an)


def test_cf1390_values() -> None:
    """Ta = MC.AccTime 0.2 s -> b = 2.5, c = P2 = SplineAccuracyRate 0.02 (A6 §1.6)."""
    b, c = 0.5 / 0.2, 0.02
    # a <= 1.6: 8ba + 15(30c-1) a = 20a - 6a
    assert vendor_arc_speed(1.0, b, c) == pytest.approx(14.0)
    q2 = b * b / (3 - 90 * c) ** 2
    assert vendor_arc_speed(20.0, b, c) == pytest.approx(math.sqrt(20 * 1200.0) - 6.0 * q2)


def test_never_negative_in_parameter_range() -> None:
    a = np.concatenate((np.linspace(0, 20, 2001), np.geomspace(20, 1e6, 200)))
    for ta in np.linspace(0.06, 0.25, 12):
        for c in np.linspace(0.01, 1.0, 34):
            v = vendor_arc_speed(a, 0.5 / ta, c)
            assert np.all(np.isfinite(v))
            assert np.all(v >= 0)
            raw = np.array([reference(float(x), 0.5 / ta, float(c)) for x in a[::50]])
            assert np.all(raw >= -1e-9)


def test_monotone_increasing_in_radius_cf1390() -> None:
    a = np.linspace(0, 200, 20001)
    v = vendor_arc_speed(a, 2.5, 0.02)
    assert np.all(np.diff(v) >= -1e-9)


def test_infinite_radius_is_infinite_speed() -> None:
    assert vendor_arc_speed(math.inf, 2.5, 0.02) == math.inf


def test_physical_model() -> None:
    assert centripetal_speed(2.0, 800.0) == pytest.approx(40.0)
    assert equivalent_corner_radius(0.0, 0.1) == math.inf
    assert equivalent_corner_radius(math.pi, 0.1) == 0.0
    r90 = equivalent_corner_radius(math.pi / 2, 0.1)
    assert r90 == pytest.approx(0.1 * math.cos(math.pi / 4) / (1 - math.cos(math.pi / 4)))
    # apex of the tangent arc lies exactly `deviation` from the corner
    assert r90 / math.cos(math.pi / 4) - r90 == pytest.approx(0.1)
    assert junction_deviation_speed(math.pi / 2, 1000.0, 0.1) == pytest.approx(
        math.sqrt(1000 * r90)
    )
    m = JunctionModel("physical", normal_acc=800.0)
    assert m.limit([2.0], 0.2, 0.02, 6000.0)[0] == pytest.approx(40.0)
    assert JunctionModel().limit([1.0], 0.2, 0.02, 6000.0)[0] == pytest.approx(14.0)
