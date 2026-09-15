"""``nexcut-plan`` / ``python -m nexcut.plan``: plan a ``.chf`` job into a FIFO frame file (dry run, never sends).

Pipeline per contour (05 §4): ``.chf`` glyphs (03 §6-7) -> ``contour_fit.process`` (05 §6) ->
``plan_velocity`` (05 §7) -> 250 µs sampling (05 §7.6) -> laser mask / PWM per tick (05 §5.2,
02 §3.4) -> job records with the CO2 prologue/epilogue (11 §5.4) -> items (11 §5.2) -> frames
(11 §5.1).  Contours are cut in document order (sorting is a separate op); a rapid move joins
consecutive contours; the job starts at the first contour's start point (no initial rapid,
UNVERIFIED: the machine position at Start is not known offline).

Safety (PORT-PLAN §8): this command only writes a text file.  It opens no socket and imports no
transport.  By default the frames are dry-run frames (duty 0, no laser-enable DO records);
``--laser-records`` keeps the laser records for comparison with vendor frames, and even then any
later transmission goes through :mod:`nexcut.mcc.safety`, which strips them unless LASER_ARMED.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from nexcut.io.chf import load_chf
from nexcut.io.params import ParamDocument, default_document, read_params
from nexcut.mcc.fifo import PackedFrame, write_frame_file
from nexcut.model.graph import ChfDocument, iter_contours
from nexcut.plan.contour_fit import glyphs_from_contour, process
from nexcut.plan.items import (
    AxisScale,
    ContourLaser,
    JobStreamBuilder,
    StreamConfig,
    TickQuantizer,
    UnsupportedConfiguration,
    gas_do_port,
    total_ticks,
)
from nexcut.plan.lookahead import plan_velocity
from nexcut.plan.params import (
    DEFAULT_K,
    document_value,
    laser_from_manu,
    layer_values,
    planner_params,
    rapid_params,
)
from nexcut.plan.pwm_schedule import LayerLaser, laser_mask, toggle_positions
from nexcut.plan.sampler import PathGeometry, plan_line, sample_plan

__all__ = ["JobResult", "build_job", "main"]

_HARD_KEYS = (
    "MAC.SpeedRatio",
    "MAC.WritePluse",
    "MAC_1.SpeedRatio",
    "MAC_1.WritePluse",
    "MGP.LowAir",
    "MGP.LowO2",
    "MGP.LowN2",
    "MGP.HighAir",
    "MGP.HighO2",
    "MGP.HighN2",
    "LGP.CO2DOLaser",
    "LGP.CO2LaserControlType",
    "ZF.ZFType",
    "AX.InterpolationCycle",
)


@dataclass(slots=True)
class JobResult:
    """Frames and statistics of a planned job."""

    frames: list[PackedFrame]
    contours: int = 0
    skipped: list[str] = field(default_factory=list)
    ticks: int = 0
    cut_length_mm: float = 0.0
    cycle_us: int = 250

    @property
    def duration_s(self) -> float:
        """Ticks x cycle (card-side period UNVERIFIED, 11 O1)."""
        return self.ticks * self.cycle_us * 1e-6


def _value(doc: ParamDocument, key: str, default: float | int | str) -> float | int | str:
    try:
        return document_value(doc, key)
    except KeyError:
        return default


def build_job(
    doc: ChfDocument,
    manu: ParamDocument,
    hard: ParamDocument,
    layer_doc: ParamDocument,
    *,
    laser_records: bool = False,
    cycle_us: int | None = None,
    k: int = DEFAULT_K,
) -> JobResult:
    """Plan every contour of ``doc`` and pack the job into frames (module docstring)."""
    laser = laser_from_manu(manu)
    hv = {key: _value(hard, key, 0) for key in _HARD_KEYS}
    laser_type = int(float(_value(manu, "SP.m_iEnableLaserType", 1)))
    if laser_type != 1:
        raise UnsupportedConfiguration(
            "only the CO2 stream (SP.m_iEnableLaserType = 1) is implemented; the fibre records "
            "4/5 are not traced (A3 §4.4)"
        )
    cfg = StreamConfig(
        laser_type=laser_type,
        co2_control_type=int(float(hv["LGP.CO2LaserControlType"])),
        zf_type=int(float(hv["ZF.ZFType"])),
        zf_up_speed=float(_value(manu, "ZF.ZFUpSpeed", 100.0)),
        zf_dock_height=float(_value(manu, "ZF.ZFDockHeight", 20.0)),
    )
    cycle = int(cycle_us or int(float(hv["AX.InterpolationCycle"])) or 250)
    gas_delay_ms = float(_value(manu, "GC.GasDelay", 100.0))
    builder = JobStreamBuilder(
        config=cfg,
        quantizer=TickQuantizer(AxisScale.from_values(hv)),  # type: ignore[arg-type]
        laser_records=laser_records,
    )
    rapid = rapid_params(manu, hard, k=k)
    result = JobResult(frames=[], cycle_us=cycle)
    pos: np.ndarray | None = None
    for gi, graph in enumerate(doc.graphs):
        for ci, (contour, kind) in enumerate(iter_contours(graph)):
            tag = f"graph {gi} contour {ci}"
            if kind == "child" and graph.TYPE == 11:
                continue  # scan sources: the generated paths are cut instead (03 §6.4)
            lv = layer_values(layer_doc, contour.layer, laser)
            if int(float(lv.get("NoManu", 0))):
                result.skipped.append(f"{tag}: layer {contour.layer} NoManu")
                continue
            glyphs = glyphs_from_contour(contour)
            if not glyphs:
                result.skipped.append(f"{tag}: no geometry")
                continue
            pp = planner_params(manu, hard, layer_doc, contour.layer, laser=laser, k=k)
            fit = process(glyphs, pp.precision, pp.vmax)
            if fit.pieces is None or fit.pieces.total_length <= 0.0:
                result.skipped.append(f"{tag}: zero length")
                continue
            plan = plan_velocity(fit.pieces, pp)
            geom = PathGeometry.from_glyphs(fit.smoothed)
            motion = sample_plan(plan, geom, cycle)
            start = motion.xy[0]
            if pos is not None and float(np.hypot(*(start - pos))) > 1e-9:
                rplan, rgeom = plan_line(pos, start, rapid)
                rmotion = sample_plan(rplan, rgeom, cycle)
                builder.add_motion(rmotion.xy)
            ll = LayerLaser.from_layer(lv)
            toggles = toggle_positions(fit.pieces.pwm_segments, fit.pieces.pwm_laser_on)
            start_on = bool(fit.pieces.pwm_laser_on[0]) if fit.pieces.pwm_laser_on.size else True
            mask = laser_mask(motion.s, motion.v, toggles, start_on=start_on)
            freq, duty = ll.tick_pwm(motion.interval_speed(), mask)
            cl = ContourLaser(
                gas_port=gas_do_port(ll.gas_type, hv),  # type: ignore[arg-type]
                laser_port=int(float(hv["LGP.CO2DOLaser"])),
                pierce_dwell_ms=ll.laser_on_delay_ms + gas_delay_ms,
            )
            builder.add_contour(motion.xy, freq, duty, cl, cycle)
            pos = motion.xy[-1]
            result.contours += 1
            result.cut_length_mm += fit.pieces.total_length
    builder.finish()
    result.frames = builder.frames()
    result.ticks = total_ticks(result.frames)
    return result


def _load(path: str | None, kind: str) -> ParamDocument:
    return read_params(path, kind) if path else default_document(kind)


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""
    ap = argparse.ArgumentParser(
        prog="nexcut-plan",  # also `python -m nexcut.plan`
        description="Plan a .chf job into MCC100 FIFO frames (dry run: writes a file, never sends).",
    )
    ap.add_argument("chf", help="job file (.chf)")
    ap.add_argument("--layer-xml", required=True, help="BkLayerPara.xml")
    ap.add_argument("--hard-xml", required=True, help="BkHardPara.xml")
    ap.add_argument(
        "--manu-xml",
        help="BkManuPara.xml (default: next to --hard-xml, else descriptor defaults)",
    )
    ap.add_argument("-o", "--output", required=True, help="frame file to write")
    ap.add_argument(
        "--laser-records",
        action="store_true",
        help="keep laser records (duty, DO9) in the file for comparison; still never sent",
    )
    ap.add_argument("--cycle-us", type=int, help="interpolation cycle override [µs]")
    ap.add_argument("--first-frame-id", type=lambda s: int(s, 0), default=1)
    ap.add_argument("--k", type=int, default=DEFAULT_K, help="card units per mm (reg 50017)")
    args = ap.parse_args(argv)

    manu_path = args.manu_xml
    if manu_path is None:
        sibling = Path(args.hard_xml).with_name("BkManuPara.xml")
        manu_path = str(sibling) if sibling.is_file() else None
    try:
        doc = load_chf(args.chf)
        job = build_job(
            doc,
            _load(manu_path, "manu"),
            read_params(args.hard_xml, "hard"),
            read_params(args.layer_xml, "layer"),
            laser_records=args.laser_records,
            cycle_us=args.cycle_us,
            k=args.k,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    first = args.first_frame_id
    header = [
        "nexcut.plan dry run - FIFO frames for register 0x66 (11 §5); NOT sent to any card",
        f"source {args.chf}",
        f"laser records {'kept' if args.laser_records else 'stripped (dry run)'}",
        f"contours {job.contours}, ticks {job.ticks}, cycle {job.cycle_us} us "
        f"(card-side period UNVERIFIED), duration {job.duration_s:.3f} s, "
        f"path {job.cut_length_mm:.3f} mm",
        f"frame ids from {first} (card reg 1015 + 1 at stream time)",
    ]
    header.extend(f"skipped: {s}" for s in job.skipped)
    write_frame_file(
        args.output,
        (((first + i) & 0xFFFFFFFF, f) for i, f in enumerate(job.frames)),
        header,
    )
    print(
        f"{len(job.frames)} frames, {job.ticks} ticks, {job.contours} contours, "
        f"{math.ceil(job.duration_s * 1000) / 1000:.3f} s -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
