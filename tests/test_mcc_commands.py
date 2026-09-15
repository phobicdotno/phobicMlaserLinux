"""Command builders vs. the vectors quoted in 11-static-findings §2 and the 08 logs.

Golden vectors are copied from ``SRC/Log/*.log`` ``DataEx:40 65 ...`` lines (hex words)
and from 11 §2 / A1 / A3 / A5; ``test_every_logged_command_is_reproduced`` re-reads the
logs when SRC is available.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest

from nexcut.mcc import commands as C
from nexcut.mcc.commands import MachineParams, Policy, StopVariant

P = MachineParams()
SLOW = replace(P, is_fast_mode=False)


def hexw(text: str) -> tuple[int, ...]:
    """Words as printed after ``DataEx:40 65 NN`` (hex, no leading zeros)."""
    return tuple(int(t, 16) for t in text.split())


# ---- V0 / V14 / V16 ------------------------------------------------------------------------------


def test_connect_prologue_v0() -> None:
    cmd = C.connect_prologue()
    assert (cmd.register, cmd.words, cmd.vector_id) == (0x65, (9999, 5, 0, 0), "V0")
    assert cmd.policy is Policy.CONNECT and cmd.unverified  # meaning unknown -> flagged


def test_old_handshake_is_ext_do_bulk_off_and_denied() -> None:
    cmd = C.ext_do_bulk_off(0x12345)
    assert cmd.words == (9999, 13, 0xFFFF, 0x2345)  # 11 §2.1
    assert cmd.policy is Policy.DENY


def test_zf_stop_and_fifo_control() -> None:
    assert C.zf_stop().words == (101,)
    assert (C.fifo_clear().register, C.fifo_clear().words) == (0x67, (1,))
    assert C.fifo_start().words == (2,)
    assert C.fifo_stop().words == (3,) and C.fifo_stop().policy is Policy.ALWAYS


# ---- jog V1 / V2 / V9 ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("axis", "positive", "params", "logged"),
    [
        (1, True, SLOW, "03 01 c350 176f ea56 3d0900"),
        (0, True, P, "03 00 30d40 176f ea56 3d0900"),
        (0, False, P, "03 00 30d40 176f ea56 ffc2f700"),
        (1, False, SLOW, "03 01 c350 176f ea56 ffc2f700"),
        (1, True, P, "03 01 30d40 176f ea56 3d0900"),
    ],
)
def test_jog_continuous_matches_log(
    axis: int, positive: bool, params: MachineParams, logged: str
) -> None:
    cmd = C.jog_continuous(axis, positive, params)
    assert cmd.words == hexw(logged)
    assert cmd.vector_id == "V1" and cmd.policy is Policy.MOTION


def test_jog_continuous_signed_view() -> None:
    assert C.jog_continuous(0, False).signed_words == (3, 0, 200000, 5999, 59990, -4000000)


def test_jog_soft_limit_clip_matches_log() -> None:
    # logged "03 00 c350 176f ea56 fff8ae4b": remaining distance to the -limit 479.669 mm
    cmd = C.jog_continuous(0, False, SLOW, soft_limit_remaining_mm=479.669)
    assert cmd.words == hexw("03 00 c350 176f ea56 fff8ae4b")


def test_jog_step_v2_findings_vector() -> None:
    assert C.jog_step(0, 5.0, SLOW).words == (3, 0, 50000, 5999, 59990, 5000)  # 11 §2 V2
    assert C.jog_step(1, -1.0, SLOW).signed_words[-1] == -1000


def test_jog_speed_and_acc_clamps() -> None:
    fast = replace(P, jog_fast_speed=5000.0, x_fast_move_acc=99999.0)
    w = C.jog_step(0, 1.0, fast).words
    assert w[2] == 3000 * 1000 and w[3] == 20000 and w[4] == 200000


def test_jog_rejects_other_axes() -> None:
    with pytest.raises(ValueError):
        C.jog_step(4, 1.0)


def test_absolute_move_sets_bit31() -> None:
    cmd = C.move_axis_absolute(0, 100.0, replace(P, jog_fast_speed=20.0))
    assert cmd.words == (3, 0x80000000, 20000, 5999, 59990, 100000)  # 11 §7 step 4
    assert cmd.policy is Policy.MOTION_HOMED and cmd.unverified


def test_lift_table_jog_v9_matches_log() -> None:
    cmd = C.lift_table_jog(True, SLOW)
    assert cmd.words == hexw("03 04 c350 fa0 9c40 f4240")
    assert cmd.policy is Policy.DENY


# ---- stop family V3 / V4 / V5 ------------------------------------------------------------------------


def test_stop_decel_word_rule() -> None:
    # 200 -> 500 -> 2000; 50 -> 2000 (11 §2 V3)
    assert C.stop_decel_word(200000, P, StopVariant.MAINAPP_NO_SOFT_LIMIT) == 2000
    assert C.stop_decel_word(50000, P, StopVariant.MAINAPP_NO_SOFT_LIMIT) == 2000
    assert C.stop_decel_word(10000, P, StopVariant.MAINAPP_NO_SOFT_LIMIT) == 8000  # 0.4*MaxAcc
    assert C.stop_decel_word(10000, P, StopVariant.PENDANT_OR_VM) == 10000
    assert C.stop_decel_word(0, P) == 2000  # v_last <= 0 -> 100000 -> 1 -> clamp
    assert C.stop_decel_word(500, P) == 100000  # vendor would divide by zero (deviation)


@pytest.mark.parametrize(
    ("slots", "v_last", "variant", "logged"),
    [
        ([0], 200000, StopVariant.MAINAPP_NO_SOFT_LIMIT, "01 01 02 7d0 4e20"),
        ([1], 50000, StopVariant.MAINAPP_NO_SOFT_LIMIT, "01 02 02 7d0 4e20"),
        ([0], 200000, StopVariant.MAINAPP_SOFT_LIMIT, "01 01 02 1388 c350"),
        ([1], 50000, StopVariant.MAINAPP_SOFT_LIMIT, "01 02 02 1388 c350"),
    ],
)
def test_jog_release_stop_matches_log(
    slots: list[int], v_last: int, variant: StopVariant, logged: str
) -> None:
    cmd = C.jog_release_stop(slots, v_last, P, variant)
    assert cmd.words == hexw(logged)
    assert cmd.policy is Policy.ALWAYS and cmd.vector_id == "V3"


def test_stop_all_v4_and_v5() -> None:
    cmd = C.stop_all(200000)
    assert cmd.words == hexw("01 1f 02 7d0 4e20")
    assert cmd.request == [0x40, 0x65, 5, 1, 0x1F, 2, 0x7D0, 0x4E20]  # DataEx form
    assert C.stop_all_no_decel().words == (1, 0x1F)


def test_stop_sequence_running_branch_this_machine() -> None:
    seq = C.stop_sequence(P, fifo_running=True, current_do_word=0x104 | 0x8)
    assert [(c.register, c.words) for c in seq] == [
        (0x67, (3,)),
        (0x65, (9999, 2, 0x100, 0)),  # LGP.CO2DOLaser = 9 off
        (0x65, (9999, 4, 0, 0)),  # CO2LaserDAPort 1 := 0 V
        (0x65, (9999, 3, 1234, 0, 0)),  # PWM off, PtLaserFreq 1234
        (0x65, (9999, 0x11, 1234, 0, 0)),
        (0x65, (9999, 4, 1, 0)),  # gas DA RatioO2 = 2 := 0 V
        (0x65, (9999, 2, 0xFFFF, 0x8)),  # DO4 (unconfigured) keeps its state
        (0x65, (101,)),
    ]
    assert all(c.policy is Policy.ALWAYS for c in seq)


def test_stop_sequence_idle_uses_stop_all_and_zf_gate() -> None:
    seq = C.stop_sequence(replace(P, zf_type=0), fifo_running=False)
    assert seq[0].words == (1, 0x1F, 2, 2000, 20000)
    assert (101,) not in [c.words for c in seq]
    assert [c.words for c in C.pause_sequence(P)] == [c.words for c in C.stop_sequence(P)]


def test_estop_sequence_gate_port_zero_sends_nothing_extra() -> None:
    assert [c.words for c in C.estop_sequence(P)] == [c.words for c in C.stop_sequence(P)]
    gated = C.estop_sequence(replace(P, co2_do_laser_gate=5))
    assert gated[1].words == (9999, 2, 0x10, 0)


def test_resume_is_unresolved() -> None:
    with pytest.raises(C.UnresolvedCommandError):
        C.resume()


# ---- home V6 / V7 / V10, go-to V8 ----------------------------------------------------------------------


def test_home_vectors() -> None:
    assert C.home_axis(0).words == (2, 1, 0) and C.home_axis(0).policy is Policy.MOTION
    assert C.home_axis(1).words == (2, 2, 0)
    assert C.lift_table_home().words == (2, 0x10, 0) and C.lift_table_home().policy is Policy.DENY
    assert C.home_system(3).words == (2, 3, 0)
    assert C.home_system(4).words == (2, 0x10003, 0)
    assert C.home_system(6).words == (2, 0, 0)
    assert C.home_system(5).words == (2, 0xB, 0)


def test_go_to_matches_log() -> None:
    cmd = C.go_to(430.178, 174.097)
    assert cmd.words == hexw("05 80000003 86470 2327 15f86 69062 2a811 00 00")
    assert cmd.policy is Policy.MOTION_HOMED and cmd.unverified


# ---- DO / DA / PWM / ZF -------------------------------------------------------------------------------


def test_do_vectors() -> None:
    assert C.do_set(1, False).words == hexw("270f 02 01 00")  # logged 22x
    assert C.do_set(1, True).words == hexw("270f 02 01 01")  # logged 7x
    assert C.do_set(3, True).words == (9999, 2, 4, 4)  # DO3 high air (A3 §8 prologue)
    assert C.do_set(9, True).words == (9999, 2, 0x100, 0x100)  # DO9 CO2 laser
    assert C.do_set(9, False).words == (9999, 2, 0x100, 0)
    assert C.do_set(5, True).words == (9999, 2, 0x10, 0x10)  # DO5 fibre gate (A5 V10)
    assert C.do_set(9, True).policy is Policy.LASER
    assert C.do_set(3, True).policy is Policy.MOTION
    assert C.do_set(9, False).policy is Policy.ALWAYS
    assert C.do_set(12, True).words == (9999, 13, 2, 2) and C.do_set(12, True).policy is Policy.DENY
    with pytest.raises(ValueError):
        C.do_set(0, True)


def test_da_vectors() -> None:
    assert C.da_set(1, 5.0).words == (9999, 4, 0, 5000)
    assert C.da_set(2, 0.03).words == (9999, 4, 1, 50)  # 1..49 mV -> 50
    assert C.da_set(1, 12.0).words == (9999, 4, 0, 10000)
    assert C.da_set(1, 0.0).policy is Policy.ALWAYS
    assert C.da_set(1, 1.0).policy is Policy.DENY
    with pytest.raises(ValueError):
        C.da_set(3, 1.0)
    with pytest.raises(ValueError):
        C.da_set(1, -1.0)


def test_pwm_and_zf_move() -> None:
    assert C.pwm_off(1234).words == (9999, 0x11, 1234, 0, 0)
    assert C.pwm_set(5000, 4).words == (9999, 0x11, 5000, 4)
    assert C.pwm_set(5000, 4).policy is Policy.LASER
    assert C.zf_move(100.0, 2.01).words == (103, 1000, 2009)  # truncation (A5 V8)


def test_trunc_int_toward_zero() -> None:
    assert C.trunc_int(-1.9) == -1 and C.trunc_int(8999.985) == 8999
    with pytest.raises(ValueError):
        C.trunc_int(float("nan"))


# ---- all logged 0x65 vectors (needs SRC) ---------------------------------------------------------------

_DATAEX = re.compile(r"DataEx:40 65 ([0-9a-f ]+)")


def _rebuild(words: tuple[int, ...]) -> C.CommandVector | None:
    """Find the builder call that reproduces a logged vector (None = not an M1 builder)."""
    sub = words[0]
    if sub == 3 and words[1] in (0, 1):
        params = SLOW if words[2] == 50000 else P
        d = words[5] - (1 << 32) if words[5] & 0x80000000 else words[5]
        # The vendor's mm value is an unknown double (soft limit - position); +0.25 µm away
        # from zero makes trunc() recover the logged integer, so only the layout is tested.
        mm = (abs(d) + 0.25) / 1000
        return C.jog_step(words[1], mm if d >= 0 else -mm, params)
    if sub == 3 and words[1] == 4:
        return C.lift_table_jog(not words[5] & 0x80000000, SLOW if words[2] == 50000 else P)
    if sub == 1:
        slots = [s for s in range(5) if (words[1] >> s) & 1]
        if words[1] == 0x1F:
            return C.stop_all(200000)
        variant = (
            StopVariant.MAINAPP_SOFT_LIMIT
            if words[3] == 5000
            else StopVariant.MAINAPP_NO_SOFT_LIMIT
        )
        return C.jog_release_stop(slots, 200000, P, variant)
    if sub == 9999 and words[1] == 2:
        return C.do_set(words[2].bit_length(), bool(words[3]))
    if sub == 5:
        return C.go_to((words[5] + 0.25) / 1000, (words[6] + 0.25) / 1000)
    return None


def test_every_logged_command_is_reproduced(src_dir: Path) -> None:
    seen = 0
    for log in sorted((src_dir / "Log").glob("*.log")):
        for m in _DATAEX.finditer(log.read_text(encoding="utf-8", errors="replace")):
            n, *rest = hexw(m.group(1))
            words = tuple(rest)
            assert len(words) == n
            if words == (0x66,):
                continue  # [102] re-sync, DENY (C10)
            cmd = _rebuild(words)
            assert cmd is not None, words
            assert cmd.words == words, (log.name, [hex(w) for w in words])
            seen += 1
    assert seen >= 590  # 11 §2.1 census: 506 + 60 + 29 (minus the two [102])
