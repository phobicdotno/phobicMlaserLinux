"""Cross-module consistency: the mcc layers share one set of constants (PORT-PLAN §3.4)."""

from __future__ import annotations

from nexcut.io import chf
from nexcut.mcc import commands, framing, registers, safety, simulator, transaction
from nexcut.model import ChfDocument, Contour


def test_commands_use_register_map() -> None:
    """Axis mask and K register come from registers (A1 row 4, A1 §2)."""
    assert commands.AXIS_MASK_ALL == (1 << registers.AXIS_SLOTS) - 1 == 0x1F
    assert commands.K_REGISTER == registers.SystemRW.K.addr == 50017
    assert commands.REG_COMMAND is registers.REG_COMMAND is framing.REG_COMMAND


def test_simulator_shares_command_vocabulary() -> None:
    """The simulator decodes the same sub-command numbers the builders emit (11 §2, §8)."""
    assert simulator.CMD_STOP == commands.CMD_STOP == 1
    assert simulator.CMD_HOME == commands.CMD_HOME == 2
    assert simulator.CMD_MOVE == commands.CMD_GOTO == 5
    assert simulator.CMD_MISC == commands.CMD_9999 == 9999
    assert (simulator.FIFO_CLEAR, simulator.FIFO_START, simulator.FIFO_STOP) == (
        commands.FIFO_CLEAR,
        commands.FIFO_START,
        commands.FIFO_STOP,
    )
    assert simulator.RO_OUTPUTS == registers.Status.DO.addr == 1005


def test_wire_layers_use_framing() -> None:
    """Transaction, simulator and safety encode through framing (04 §3.2)."""
    assert transaction.encode_vector is framing.encode_vector
    assert safety.encode_vector is framing.encode_vector
    assert simulator.read_reply is framing.read_reply


def test_do_set_matches_register_bit_map() -> None:
    """DO builder bit == registers.do_bit for every port 1..26 (A2 §2.2, A3 §6)."""
    for port in range(1, 27):
        _, bit = registers.do_bit(port)  # type: ignore[misc]
        assert commands.do_set(port, True).words[2] == 1 << bit


def test_chf_writer_uses_model_types() -> None:
    """io.chf reads/writes the nexcut.model dataclasses, not private copies (03 §6)."""
    assert chf.ChfDocument is ChfDocument
    assert chf.Contour is Contour


# ------------------------------------------------------------------------------------------------
# M1/M2 seams: planner -> fifo -> safety/dissector/simulator, mccd, ui (PORT-PLAN §3, §8)
# ------------------------------------------------------------------------------------------------


def test_planner_items_share_fifo_vocabulary() -> None:
    """Planner items use the 9999 sub-codes of mcc.commands and opcodes every reader knows
    (11 §5.2, A3 §5/§6)."""
    from nexcut.mcc import dissector, fifo
    from nexcut.plan import items

    assert (items.SUB_DO, items.SUB_PWM, items.SUB_DA, items.SUB_DO_EXT, items.SUB_PWM_5V) == (
        commands.MISC_DO,
        commands.MISC_PWM,
        commands.MISC_DA,
        commands.MISC_DO_EXT,
        commands.MISC_PWM_5V,
    )
    planner_ops = {
        items.OP_TICK, items.OP_BOUNDARY, items.OP_MODE, items.OP_MISC,
        items.OP_ZF_MOVE, items.OP_ZF_LIFT, items.OP_ZF_BOOK, items.OP_WAIT,
    }  # fmt: skip
    assert planner_ops <= set(dissector.FIFO_OPCODES)
    assert planner_ops <= safety._ALLOWED_FIFO_OPCODES
    assert items.OP_TICK == simulator.OP_TICK == safety._TICK == 3000
    assert items.OP_MISC == simulator.CMD_MISC == commands.CMD_9999
    assert items.item_header is fifo.item_header
    assert fifo.REG_FIFO_DATA is framing.REG_FIFO_DATA and fifo.FUNC_WRITE is framing.FUNC_WRITE


def test_planner_laser_ports_are_stripped_by_safety() -> None:
    """Every DO the planner flags as laser is a laser port for the safety gate (PORT-PLAN §8.2)."""
    from nexcut.plan import items

    cfg = safety.SafetyConfig()
    assert items.StreamConfig().laser_do_ports <= cfg.laser_do_ports
    for port in items.StreamConfig().laser_do_ports:
        words = [1, *items.do_item(port, True).words()]
        out, changed = safety.strip_laser_records(words, cfg)
        assert changed == 1 and out == [1]


def test_k_and_parameter_documents_agree() -> None:
    """K = 1000 card units/mm everywhere (A1 §2, 11 C4/C5); plan reads io.params documents."""
    from nexcut.io import params as io_params
    from nexcut.plan import params as plan_params

    sim_k = simulator.SimConfig().system_rw[registers.SystemRW.K]
    assert plan_params.DEFAULT_K == commands.MachineParams().k == sim_k == 1000
    assert plan_params.ParamDocument is io_params.ParamDocument


def test_fifo_margin_units_agree() -> None:
    """Simulator, feeder, mccd status and the vendor job-end test read reg 1016 the same way
    (11 C2, §4.1 row 16)."""
    from nexcut.mcc import fifo
    from nexcut.mccd.status import StatusSnapshot

    cfg = simulator.SimConfig()
    assert cfg.fifo_space_unit == "bytes"
    assert cfg.fifo_margin_empty == registers.FIFO_MARGIN_EMPTY == 60000
    with simulator.CardSimulator(config=cfg) as sim, transaction.McTransaction(sim.address) as cl:
        block = cl.read(1000, 36)
    snap = StatusSnapshot.build(
        t=0.0, link="CONNECTED", arm_state="DISARMED", estop_latched=False,
        poll_age_s=0.0, block1000=block, axis_ro=None,
    )  # fmt: skip
    assert snap.fifo_empty and snap.fifo_margin == 60000 and not snap.fifo_running
    feeder = fifo.FifoFeeder()
    feeder.push([], last=True)
    assert feeder.job_finished(block[registers.Status.FIFO_SPACE_MARGIN], 1)


def test_mccd_and_ui_use_shared_layers() -> None:
    """mccd gates through mcc.safety and decodes mcc.registers; the viewer opens files with the
    io readers and applies ops.import_gates (PORT-PLAN §2.3)."""
    from nexcut.io import dxf, gcode, plt
    from nexcut.mccd import daemon, gate, status
    from nexcut.ops import import_gates
    from nexcut.ui import loader

    assert gate.SafeMccClient is safety.SafeMccClient
    assert daemon.SafetyConfig is safety.SafetyConfig
    assert status.Status is registers.Status is daemon.Status
    assert loader.load_chf is chf.load_chf and loader.save_chf is chf.save_chf
    assert loader.apply_import_gates is import_gates.apply_import_gates
    assert set(loader.OPEN_SUFFIXES.values()) == {"chf", "dxf", "plt", "gcode"}
    assert callable(dxf.read_dxf) and callable(plt.read_plt) and callable(gcode.read_gcode)


def test_planned_job_streams_through_feeder_into_simulator() -> None:
    """End to end on the simulator only: .chf model -> planner (05, 11 §5) -> safety strip
    (PORT-PLAN §8.2) -> FifoFeeder flow control (A3 §7) -> simulator FIFO (11 C2)."""
    import time

    from nexcut.io.params import ParamDocument, default_document
    from nexcut.mcc.dissector import parse_fifo_words
    from nexcut.mcc.fifo import FifoFeeder
    from nexcut.model.glyph import SegmentGlyph, Vec2
    from nexcut.model.graph import ContourElement
    from nexcut.plan.__main__ import build_job

    manu, hard, layer = (default_document(k) for k in ("manu", "hard", "layer"))

    def put(doc: ParamDocument, key: str, value: float | int | str) -> None:
        elem, attr = key.split(".", 1)
        group = next(g for g, e in doc.values.items() if elem in e and attr in e[elem])
        doc.set(group, elem, attr, value)

    put(manu, "SP.m_iEnableLaserType", 1)
    for key, val in {
        "ZF.ZFType": 1, "LGP.CO2DOLaser": 9, "LGP.CO2LaserControlType": 2, "MGP.HighAir": 3,
        "MAC.SpeedRatio": 31.003, "MAC.WritePluse": 8000, "MAC_1.SpeedRatio": 31.009,
        "MAC_1.WritePluse": 8000, "AX.InterpolationCycle": 250,
    }.items():  # fmt: skip
        put(hard, key, val)
    group = next(g for g in layer.values if g.endswith("CO2LayerParam2"))
    for attr, val in {"CutSpeed": 50.0, "CutDuty": 4, "CutFreq": 5000, "CutGasType": 3}.items():
        layer.set(group, "GP", attr, val)
    line = Contour(elements=[ContourElement(SegmentGlyph(Vec2(0, 0), Vec2(4, 3)))], layer=1)
    job = build_job(ChfDocument(graphs=[line]), manu, hard, layer, laser_records=True)
    assert job.contours == 1 and len(job.frames) >= 3

    stripped = []
    for f in job.frames:
        out, _ = safety.strip_laser_records([0, *f.data])
        stripped.append(type(f)(tuple(out[1:]), 0, f.items))
    ticks = [it.tick for f in stripped for it in parse_fifo_words([0, *f.data]).items if it.tick]
    assert len(ticks) == job.ticks and all(t[3] == 0 for t in ticks)  # duty 0 after the strip
    dx, dy = sum(t[0] for t in ticks), sum(t[1] for t in ticks)
    assert abs(dx - 4 * 8000 / 31.003) <= 1 and abs(dy - 3 * 8000 / 31.009) <= 1  # 11 §5.3

    cfg = simulator.SimConfig(tick_s=0.002, fifo_margin_empty=6000)  # room for 3 frames
    feeder = FifoFeeder()
    feeder.push(stripped, last=True)
    stops: list[str] = []
    with simulator.CardSimulator(config=cfg) as sim, transaction.McTransaction(sim.address) as cl:
        cl.write(0x67, [commands.FIFO_CLEAR])
        started = False
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            st = cl.read(1000, 36)
            if feeder.pending:
                res = feeder.fill(
                    st[registers.Status.FIFO_FRAME_ID],
                    st[registers.Status.FIFO_SPACE_MARGIN],
                    lambda fid, words: cl.write(0x66, words),
                )
                stops.append(res.stopped_by)
                if not started:
                    cl.write(0x67, [commands.FIFO_START])
                    started = True
            elif sim.snapshot()["queued"] == 0:
                break
            time.sleep(0.01)
        snap = sim.snapshot()
        last = cl.read(1000, 36)
    assert "space" in stops and stops[-1] == "empty"  # the 2000-byte headroom test ran
    assert feeder.frames_sent == len(job.frames)
    assert snap["frames_accepted"] == len(job.frames) and snap["underruns"] <= 1
    assert snap["ticks_consumed"] == job.ticks and (snap["x"], snap["y"]) == (dx, dy)
    assert last[registers.Status.FIFO_SPACE_MARGIN] == cfg.fifo_margin_empty  # drained
