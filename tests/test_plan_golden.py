"""Shared builders for the planner golden tests (vendor dumps in SRC root, analysis 05 §5-§6)."""

from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

from nexcut.plan.contour_fit import FitGlyph, GlyphKind, line_glyph

SEGMENT_RX = re.compile(r"\(([-\d.]+), ([-\d.]+)\)\s+\(([-\d.]+), ([-\d.]+)\)")

SIDE_LINE = 60.0
"""``GRP.scanSideLineLength="60"`` (05 §5.2)."""
CONNECTOR_LENGTH = 2.66069
"""Post-refit length of the type-7 U-turn connector, from ``setDataWithoutReFit_segs.txt``.
Its geometry is not reconstructed (scan-fill module); only its length enters the dumps."""


def read_segments(src: Path) -> list[tuple[float, float, float, float]]:
    """Parse ``segments.txt`` (05 §5.1)."""
    text = (src / "segments.txt").read_text(encoding="ascii")
    return [tuple(map(float, m.groups())) for m in SEGMENT_RX.finditer(text)]  # type: ignore[misc]


def connector(p: np.ndarray, pitch: float, heading: float) -> FitGlyph:
    """Semicircular U-turn from ``p`` (moving along ``heading`` = +-1 in x) to ``p + (0, pitch)``.

    The shape is a stand-in; the glyph carries the dump length ``CONNECTOR_LENGTH``.
    """
    r = pitch / 2.0
    ang = -math.pi / 2.0 + math.pi * np.linspace(0.0, 1.0, 65)
    pts = np.column_stack((p[0] + heading * r * np.cos(ang), p[1] + r + r * np.sin(ang)))
    return FitGlyph(GlyphKind.CURVE, pts, radius=r, length=CONNECTOR_LENGTH, laser_on=False)


def scan_path(src: Path) -> list[FitGlyph]:
    """The 61-glyph fly-cut path of ``linkFlyLine_pathGlys.txt`` built from ``segments.txt``.

    Rows of three 25 mm cuts with 3 mm laser-off gaps, 60 mm laser-off side lines beyond the row
    ends and a connector between rows; no side line before the first / after the last row
    (05 §5.2 decoded counts 24/16/14/7).
    """
    segs = read_segments(src)
    rows = [segs[i : i + 3] for i in range(0, len(segs), 3)]
    out: list[FitGlyph] = []
    for r, row in enumerate(rows):
        heading = 1.0 if row[1][0] > row[0][0] else -1.0
        cuts = [
            ((x0, y0), (x1, y1)) if heading > 0 else ((x1, y1), (x0, y0)) for x0, y0, x1, y1 in row
        ]
        y = cuts[0][0][1]
        if r > 0:
            turn = out[-1].end
            out.append(connector(turn, y - turn[1], -heading))
            out.append(line_glyph(out[-1].end, cuts[0][0], laser_on=False))
        for k, (a, b) in enumerate(cuts):
            if k > 0:
                out.append(line_glyph(out[-1].end, a, laser_on=False))
            out.append(line_glyph(a, b, laser_on=True))
        if r < len(rows) - 1:
            end = out[-1].end
            out.append(line_glyph(end, (end[0] + heading * SIDE_LINE, y), laser_on=False))
    return out


# ------------------------------------------------------------------ small golden files
def test_golden_jump_add_time(src_dir: Path) -> None:
    """``JumpAddTime.txt`` (05 §3, §8): AddTime 200 ms, Is4Freq 0, K_X = K_Y = 100 % -> 1.0."""
    from nexcut.plan.params import JumpAddTime, read_jump_add_time

    raw = (src_dir / "JumpAddTime.txt").read_bytes()
    assert b"\r" not in raw and not raw.endswith(b"\n")  # LF only, no trailing newline
    assert read_jump_add_time(src_dir / "JumpAddTime.txt") == JumpAddTime(200, 0, 1.0, 1.0, 0, 3)


def test_golden_veldecc_empty(src_dir: Path) -> None:
    """``Log/VelDecc.txt`` is 0 bytes: the node-speed log was disabled (05 §7.2)."""
    from nexcut.plan.lookahead import format_veldecc

    assert (src_dir / "Log" / "VelDecc.txt").read_bytes() == format_veldecc([])
