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
from collections.abc import Iterator, Sequence
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
    JobFrameStream,
    JobStreamBuilder,
    StreamConfig,
    TickQuantizer,
    UnsupportedConfiguration,
    gas_do_port,
)
from nexcut.plan.lookahead import plan_velocity
from nexcut.plan.params import (
    DEFAULT_K,
    Laser,
    PlannerParams,
    RapidParams,
    document_value,
    laser_from_manu,
    layer_values,
    planner_params,
    rapid_params,
)
from nexcut.plan.pwm_schedule import LayerLaser, laser_mask, toggle_positions
from nexcut.plan.sampler import PathGeometry, plan_line, sample_plan

__all__ = ["JobResult", "build_job", "main", "stream_job"]

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
    """Frames and statistics of a planned job.

    :func:`stream_job` fills every field *except* :attr:`frames` as it goes (the frames are
    yielded and forgotten); :func:`build_job` is the same job with the frames collected into
    the list, for callers small enough to hold them.
    """

    frames: list[PackedFrame] = field(default_factory=list)
    contours: int = 0
    skipped: list[str] = field(default_factory=list)
    ticks: int = 0
    cut_length_mm: float = 0.0
    cycle_us: int = 250
    frame_count: int = 0
    """Frames produced, whether or not they were kept in :attr:`frames`."""

    @property
    def duration_s(self) -> float:
        """Ticks x cycle (card-side period UNVERIFIED, 11 O1)."""
        return self.ticks * self.cycle_us * 1e-6

    def summary(self) -> list[str]:
        """The lines the frame file carries under its frames (see :func:`main`)."""
        lines = [
            f"contours {self.contours}, ticks {self.ticks}, frames {self.frame_count}, "
            f"cycle {self.cycle_us} us (card-side period UNVERIFIED), "
            f"duration {self.duration_s:.3f} s, path {self.cut_length_mm:.3f} mm"
        ]
        lines.extend(f"skipped: {s}" for s in self.skipped)
        return lines


def _value(doc: ParamDocument, key: str, default: float | int | str) -> float | int | str:
    try:
        return document_value(doc, key)
    except KeyError:
        return default


def stream_job(
    doc: ChfDocument,
    manu: ParamDocument,
    hard: ParamDocument,
    layer_doc: ParamDocument,
    *,
    laser_records: bool = False,
    cycle_us: int | None = None,
    k: int = DEFAULT_K,
    stats: JobResult | None = None,
) -> Iterator[PackedFrame]:
    """Plan every contour of ``doc`` and **yield** the FIFO frames one at a time.

    This is the production path (STATUS §5 task 6): the frames of a 100 000-contour job are
    202 M words and cannot be held in memory, and :class:`nexcut.mccd.feeder.JobFeeder` already
    consumes a lazy source.  ``stats`` is filled in as the job is planned - contour count,
    ticks, frames, path length and the skip list - and is complete once the iterator is
    exhausted; :func:`build_job` is this function with the frames collected into a list.

    The configuration is validated eagerly (before the first frame), so an unsupported machine
    still raises :class:`~nexcut.plan.items.UnsupportedConfiguration` at the call, not halfway
    through a file.
    """
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
        # The speed word of the prologue 103 and the epilogue 109 (11 §5.2; A3 §8 read
        # int(v*10) = 1000 off the leaked frames).  A3 could not tell `ZF.ZFFollowSpeed` from
        # `ZF.ZFUpSpeed` apart because both are 100 on this machine - UNVERIFIED which
        # descriptor `g+0x49c8` is (11 §7, session E).  Taking the follow speed at least makes
        # the records follow the machine instead of a hard-coded 100 mm/s.
        zf_move_speed=float(_value(manu, "ZF.ZFFollowSpeed", 100.0)),
        zf_up_speed=float(_value(manu, "ZF.ZFUpSpeed", 100.0)),
        zf_dock_height=float(_value(manu, "ZF.ZFDockHeight", 20.0)),
    )
    cycle = int(cycle_us or int(float(hv["AX.InterpolationCycle"])) or 250)
    builder = JobStreamBuilder(
        config=cfg,
        quantizer=TickQuantizer(AxisScale.from_values(hv)),  # type: ignore[arg-type]
        laser_records=laser_records,
    )
    result = stats if stats is not None else JobResult()
    result.cycle_us = cycle
    return _job_frames(
        doc,
        layer_doc,
        result,
        builder=builder,
        laser=laser,
        hv=hv,
        rapid=rapid_params(manu, hard, k=k),
        gas_delay_ms=float(_value(manu, "GC.GasDelay", 100.0)),
        manu=manu,
        hard=hard,
        cycle=cycle,
        k=k,
    )


def _job_frames(
    doc: ChfDocument,
    layer_doc: ParamDocument,
    result: JobResult,
    *,
    builder: JobStreamBuilder,
    laser: Laser,
    hv: dict[str, float | int | str],
    rapid: RapidParams,
    gas_delay_ms: float,
    manu: ParamDocument,
    hard: ParamDocument,
    cycle: int,
    k: int,
) -> Iterator[PackedFrame]:
    """The contour loop of :func:`stream_job` (module docstring for the per-contour pipeline)."""
    stream = JobFrameStream(builder)
    laser_port = int(float(hv["LGP.CO2DOLaser"]))
    pos: np.ndarray | None = None
    # Both are pure functions of the documents and the layer index, and a job of production size
    # has thousands of contours on a handful of layers (PORT-PLAN §8.3).  Kept as two caches so
    # the NoManu skip still happens before the planner block is read, as it did before.
    layer_cache: dict[int, dict[str, float | int | str]] = {}
    param_cache: dict[int, tuple[PlannerParams, LayerLaser]] = {}
    for gi, graph in enumerate(doc.graphs):
        for ci, (contour, kind) in enumerate(iter_contours(graph)):
            tag = f"graph {gi} contour {ci}"
            if kind == "child" and graph.TYPE == 11:
                continue  # scan sources: the generated paths are cut instead (03 §6.4)
            lv = layer_cache.get(contour.layer)
            if lv is None:
                lv = layer_cache[contour.layer] = layer_values(layer_doc, contour.layer, laser)
            if int(float(lv.get("NoManu", 0))):
                result.skipped.append(f"{tag}: layer {contour.layer} NoManu")
                continue
            glyphs = glyphs_from_contour(contour)
            if not glyphs:
                result.skipped.append(f"{tag}: no geometry")
                continue
            cached = param_cache.get(contour.layer)
            if cached is None:
                cached = param_cache[contour.layer] = (
                    planner_params(manu, hard, layer_doc, contour.layer, laser=laser, k=k),
                    LayerLaser.from_layer(lv),
                )
            pp, ll = cached
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
                yield from stream.motion(rmotion.xy)
            toggles = toggle_positions(fit.pieces.pwm_segments, fit.pieces.pwm_laser_on)
            start_on = bool(fit.pieces.pwm_laser_on[0]) if fit.pieces.pwm_laser_on.size else True
            mask = laser_mask(motion.s, motion.v, toggles, start_on=start_on)
            freq, duty = ll.tick_pwm(motion.interval_speed(), mask)
            cl = ContourLaser(
                gas_port=gas_do_port(ll.gas_type, hv),  # type: ignore[arg-type]
                laser_port=laser_port,
                pierce_dwell_ms=ll.laser_on_delay_ms,
                gas_delay_ms=gas_delay_ms,
            )
            yield from stream.contour(motion.xy, freq, duty, cl)
            pos = motion.xy[-1]
            result.contours += 1
            result.cut_length_mm += fit.pieces.total_length
            result.ticks = stream.ticks
            result.frame_count = stream.frames
    yield from stream.finish()
    result.ticks = stream.ticks
    result.frame_count = stream.frames


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
    """:func:`stream_job` with every frame collected into :attr:`JobResult.frames`.

    Only for jobs that fit in memory (a frame is ~300 words): the daemon and the CLI stream.
    """
    result = JobResult()
    result.frames = list(
        stream_job(
            doc,
            manu,
            hard,
            layer_doc,
            laser_records=laser_records,
            cycle_us=cycle_us,
            k=k,
            stats=result,
        )
    )
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
    first = args.first_frame_id
    stats = JobResult()
    try:
        doc = load_chf(args.chf)
        frames = stream_job(
            doc,
            _load(manu_path, "manu"),
            read_params(args.hard_xml, "hard"),
            read_params(args.layer_xml, "layer"),
            laser_records=args.laser_records,
            cycle_us=args.cycle_us,
            k=args.k,
            stats=stats,
        )
        header = [
            "nexcut.plan dry run - FIFO frames for register 0x66 (11 §5); NOT sent to any card",
            f"source {args.chf}",
            f"laser records {'kept' if args.laser_records else 'stripped (dry run)'}",
            f"frame ids from {first} (card reg 1015 + 1 at stream time)",
            "totals are in the trailing comment: the job is planned as it is written",
        ]
        # Streamed straight to the file: a production job (PORT-PLAN §8.3) is never held as
        # frames, so the totals can only be written once the last frame has gone out.
        count = write_frame_file(
            args.output,
            (((first + i) & 0xFFFFFFFF, f) for i, f in enumerate(frames)),
            header,
            stats.summary,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"{count} frames, {stats.ticks} ticks, {stats.contours} contours, "
        f"{math.ceil(stats.duration_s * 1000) / 1000:.3f} s -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
