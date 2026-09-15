"""Interpolation-cycle sampling (05 §7.6, §8) and its hand-over to the quantiser (11 §5.3)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from nexcut.plan.contour_fit import arc_glyph, line_glyph, process
from nexcut.plan.items import TickQuantizer
from nexcut.plan.lookahead import plan_velocity
from nexcut.plan.params import PlannerParams, RapidParams
from nexcut.plan.sampler import (
    CYCLE_US_DEFAULT,
    PathGeometry,
    cycle_seconds,
    plan_line,
    sample_plan,
    segment_dropped,
)

PARAMS = PlannerParams(6000.0, 0.2, 0.02, 50.0)


def test_cycle_and_drop_rule() -> None:
    assert CYCLE_US_DEFAULT == 250 and cycle_seconds() == 0.00025
    with pytest.raises(ValueError):
        cycle_seconds(0)
    # L < 0.00025 * min(vs, ve) -> dropped (05 §7.6)
    assert segment_dropped(0.013, 50.0, 50.0) is False  # 0.0125 mm per cycle at 50 mm/s
    assert segment_dropped(0.012, 50.0, 60.0) is True
    assert segment_dropped(0.012, 0.0, 60.0) is False  # starts at rest
    assert segment_dropped(5e-5, 0.0, 0.0) is True  # L < 1e-4


def test_line_sampling_timing_and_end_point() -> None:
    plan, geom = plan_line((10.0, 5.0), (110.0, 5.0), PARAMS)
    m = sample_plan(plan, geom)
    assert m.ticks == pytest.approx(plan.total_time / 0.00025, abs=2)
    assert m.xy[0].tolist() == [10.0, 5.0] and m.xy[-1].tolist() == pytest.approx([110.0, 5.0])
    assert np.all(np.diff(m.s) >= -1e-12)
    step = np.diff(m.s)
    assert float(step.max()) == pytest.approx(50.0 * 0.00025, rel=1e-6)  # cruise 12.5 µm/tick
    assert m.v[0] == 0.0 and float(m.v.max()) == pytest.approx(50.0)
    cont = sample_plan(plan, geom, per_segment=False)
    assert cont.ticks == math.ceil(plan.total_time / 0.00025 - 1e-9)


def test_rapid_block_and_empty_plan() -> None:
    plan, geom = plan_line((0, 0), (300, 400), RapidParams(550.0, 9000.0, 0.125, 0.25))
    m = sample_plan(plan, geom)
    assert float(m.v.max()) <= 550.0 + 1e-6 and m.xy[-1].tolist() == pytest.approx([300, 400])
    plan0, geom0 = plan_line((1, 1), (1, 1), PARAMS)
    m0 = sample_plan(plan0, geom0)
    assert m0.ticks == 0


def test_contour_geometry_mapping() -> None:
    glyphs = [
        line_glyph((0.0, 0.0), (20.0, 0.0), laser_on=True),
        arc_glyph((20.0, 5.0), 5.0, -math.pi / 2, math.pi / 2),
        line_glyph((20.0, 10.0), (0.0, 10.0), laser_on=False),
    ]
    fit = process(glyphs, 0.02, 50.0)
    assert fit.pieces is not None
    geom = PathGeometry.from_glyphs(fit.smoothed)
    assert geom.length == pytest.approx(fit.pieces.total_length, rel=1e-9)
    assert geom.laser_on.size == len(geom.s) - 1 and geom.laser_on[0] and not geom.laser_on[-1]
    mid = geom.point_at(20.0 + 2.5 * math.pi)[0]
    assert mid.tolist() == pytest.approx([25.0, 5.0], abs=1e-3)  # arc apex
    plan = plan_velocity(fit.pieces, PARAMS)
    m = sample_plan(plan, geom)
    assert m.xy[-1].tolist() == pytest.approx([0.0, 10.0], abs=1e-9)
    r = np.hypot(m.xy[:, 0] - 20.0, m.xy[:, 1] - 5.0)
    on_arc = m.xy[:, 0] > 20.0 + 1e-6
    assert np.all(np.abs(r[on_arc] - 5.0) < 2e-3)  # polyline sagitta 1 µm + blend
    dx, dy = TickQuantizer().quantize(m.xy)
    assert len(dx) == m.ticks and max(map(abs, dx)) <= 4 and max(map(abs, dy)) <= 4
    assert sample_plan(plan).xy.tolist()[0] == [0.0, 0.0]  # geometry optional


def test_dropped_segment_keeps_geometry() -> None:
    # a 5 µm piece between two long lines at speed is dropped; its displacement is not lost
    glyphs = [
        line_glyph((0, 0), (10, 0)),
        line_glyph((10, 0), (10.005, 0)),
        line_glyph((10.005, 0), (20, 0)),
    ]
    fit = process(glyphs, 0.02, 50.0)
    assert fit.pieces is not None
    plan = plan_velocity(fit.pieces, PARAMS)
    m = sample_plan(plan, PathGeometry.from_glyphs(fit.smoothed))
    assert m.xy[-1].tolist() == pytest.approx([20.0, 0.0])
    assert PathGeometry.from_glyphs([]).length == 0.0
