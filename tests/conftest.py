"""Shared pytest fixtures.

The original vendor package (SRC) is proprietary and lives outside the
repository. Tests that need it request the ``src_dir`` fixture, which reads
``NEXCUT_SRC`` (default: the owner's local copy) and skips when the directory
is absent, so the suite stays green in CI.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

DEFAULT_SRC = Path("/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52")


def src_path() -> Path:
    """Return the configured SRC path without checking that it exists."""
    return Path(os.environ.get("NEXCUT_SRC", str(DEFAULT_SRC)))


@pytest.fixture(scope="session")
def src_dir() -> Path:
    """Path of the read-only original Mlaser package; skips the test if missing."""
    path = src_path()
    if not path.is_dir():
        pytest.skip(f"original Mlaser package not available at {path} (set NEXCUT_SRC)")
    return path


# --------------------------------------------------------------------------- machine speed

CALIBRATION_REFERENCE_S = 0.052
"""Seconds :func:`calibration_seconds` takes on the review machine, unloaded.

Measured 2026-09-16 on the owner's laptop (i5-5350U, Python 3.14): 30 samples, min 0.0519 s,
median 0.0530 s, spread under 3 %. Re-measure if the reference machine changes; a wrong value
only scales every performance budget by a constant, it cannot make a budget disappear.

The workload is pure interpreter work, so the factor also tracks the *interpreter*: on the
3.12 and 3.13 legs of CI it is a little above 1.0 on identical hardware, which is right - a
budget measured on 3.14 should not be asserted verbatim against a slower interpreter.
"""


def calibration_seconds() -> float:
    """Time a fixed, allocation-heavy pure-Python workload (no I/O, no clock granularity).

    It is a proxy for how fast *this* machine runs the interpreter right now, which is what
    the open/render budgets of ``tests/test_import_ui_fidelity_review.py`` actually depend
    on. Deliberately not numpy: the budgets are dominated by Python-level work.
    """
    import time

    t = time.perf_counter()
    total = 0
    for i in range(350_000):
        total += len(str(i)) + (i & 7)
    assert total  # keep the loop
    return time.perf_counter() - t


LOAD_HEADROOM = 1.5
"""Extra slack per unit of measured slowdown (see :func:`speed_factor`).

The budgets cover Qt rasterisation, file I/O and allocator work as well as interpreter time,
and those degrade *faster* than a pure-Python loop when the cores are contended. Measured
2026-09-16 with every core busy: the calibration reported 2.32x while the 50 k-contour `.chf`
open actually took 3.2x longer, and the budget missed by 11 ms. The headroom is applied to the
excess over 1.0, so a machine at the reference speed still gets the exact gate."""

_SPEED_FACTOR: float | None = None


def speed_factor() -> float:
    """How much slower than the review machine this one is right now, at least 1.0.

    The performance budgets in the test suite come from PORT-PLAN §8.3 and were measured on
    the review laptop. CI runners are slower and noisier, and a budget asserted as an
    absolute wall-clock number there is a coin flip rather than a gate (the project's
    CI-stability rule: a timing test must pass on a machine 3x slower). Multiplying the
    budget by this factor keeps the gate exact on a machine of the review machine's speed
    and proportional everywhere else; tests print the raw seconds as well, and
    ``docs/STATUS.md`` records the unscaled laptop numbers.

    Measured once per session. The factor is never below 1.0, so a fast machine can never
    relax a budget; on a slower one the excess over 1.0 carries :data:`LOAD_HEADROOM`.
    """
    global _SPEED_FACTOR
    if _SPEED_FACTOR is None:
        best = min(calibration_seconds() for _ in range(3))  # best of 3: ignore one hiccup
        raw = max(1.0, best / CALIBRATION_REFERENCE_S)
        _SPEED_FACTOR = 1.0 + (raw - 1.0) * LOAD_HEADROOM
    return _SPEED_FACTOR


@pytest.fixture(scope="session")
def machine_speed_factor() -> float:
    """Session-scoped :func:`speed_factor`."""
    return speed_factor()
