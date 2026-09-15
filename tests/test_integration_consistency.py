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
