"""core/config.py and the nexcut-mccd CLI card-address rule (PORT-PLAN §8, DECISIONS D1/D3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexcut.core.config import (
    REAL_CARD_IP,
    ConfigError,
    NexcutConfig,
    card_ip_needs_cli_confirmation,
    default_config_path,
    default_socket_path,
    is_loopback,
    load_config,
    parse_config,
)
from nexcut.mccd import cli
from nexcut.mccd.gate import UNHOMED_MAX_JOG_SPEED_MM_S, UNHOMED_MAX_STEP_MM


def test_defaults_are_simulator_and_ipadd_ini_values() -> None:
    cfg = load_config(env={"XDG_CONFIG_HOME": "/nonexistent-nexcut"})
    assert cfg.card.ip == "127.0.0.1" and cfg.card.port == 502 and cfg.card.access_type == 1
    mc = cfg.mc  # 04 §1 shipped ipAdd.ini
    assert (mc.mc_timeout_ms, mc.mc_fifo_time_ms, mc.mc_max_recv_time) == (500, 1600, 3)
    assert (mc.mc_send_interval_ms, mc.fifo_timeout_ms, mc.mc_core_ms) == (1, 600, 30)
    assert (mc.min_hardware_ver, mc.max_item_per_frame, mc.max_fill_item) == (20152, 60, 2000)
    assert cfg.mccd.watchdog_timeout_ms == 1000 and cfg.mccd.deadman_timeout_ms == 200
    assert cfg.mccd.input_silence_timeout_ms == 1040  # 11 N7 / A8 §7
    assert cfg.motion.unhomed_max_step_mm == UNHOMED_MAX_STEP_MM == 10.0
    assert cfg.motion.unhomed_max_jog_speed_mm_s == UNHOMED_MAX_JOG_SPEED_MM_S == 20.0
    assert cfg.motion.soft_limit_x_mm == (0.0, 1371.0)
    assert cfg.motion.soft_limit_y_mm == (0.0, 950.0)
    assert cfg.source is None


def test_xdg_paths() -> None:
    env = {"XDG_CONFIG_HOME": "/x/cfg", "XDG_RUNTIME_DIR": "/run/user/7"}
    assert default_config_path(env) == Path("/x/cfg/nexcut/config.toml")
    assert default_socket_path(env) == Path("/run/user/7/nexcut/mccd.sock")
    assert default_config_path({"HOME": "/home/u"}) == Path("/home/u/.config/nexcut/config.toml")
    assert default_socket_path({}).name == "mccd.sock"


def test_load_file_overrides(tmp_path: Path) -> None:
    p = tmp_path / "nexcut" / "config.toml"
    p.parent.mkdir()
    p.write_text(
        """
[card]
port = 1502
[motion]
unhomed_max_step_mm = 5
unhomed_max_jog_speed_mm_s = 12.5
soft_limit_y_mm = [0, 900]
[mccd]
watchdog_di_alarms = [[11, true], [4, false]]
"""
    )
    cfg = load_config(env={"XDG_CONFIG_HOME": str(tmp_path)})
    assert cfg.source == p and cfg.card.port == 1502
    assert cfg.motion.unhomed_max_step_mm == 5.0
    assert cfg.motion.unhomed_max_jog_speed_mm_s == 12.5
    assert cfg.motion.soft_limit_y_mm == (0.0, 900.0)
    assert cfg.mccd.watchdog_di_alarms == ((11, True), (4, False))


@pytest.mark.parametrize(
    "data",
    [
        {"motion": {"unhomed_max_step": 5}},  # typo in a safety key
        {"moton": {}},
        {"motion": {"unhomed_max_step_mm": "10"}},
        {"motion": {"unhomed_max_step_mm": 0}},
        {"motion": {"unhomed_max_jog_speed_mm_s": 500}},  # above max_jog_speed_mm_s
        {"motion": {"position_scale_verified": 1}},
        {"card": {"ip": "not-an-ip"}},
        {"card": {"port": 70000}},
        {"card": {"access_type": 0}},
        {"mccd": {"poll_timeout_ms": 1000}},
        {"mccd": {"watchdog_di_alarms": [[0, True]]}},
        {"motion": {"soft_limit_x_mm": [10, 5]}},
    ],
)
def test_invalid_config_raises(data: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        parse_config(data)


def test_explicit_missing_file_and_bad_toml(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "absent.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[card\n")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_real_card_needs_explicit_cli_ip() -> None:
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("localhost")
    assert not is_loopback(REAL_CARD_IP)
    cfg = NexcutConfig()
    assert not card_ip_needs_cli_confirmation(cfg, None)
    from_file = parse_config({"card": {"ip": REAL_CARD_IP}})
    assert card_ip_needs_cli_confirmation(from_file, None)
    assert not card_ip_needs_cli_confirmation(from_file.with_card(REAL_CARD_IP), REAL_CARD_IP)


def test_cli_refuses_non_loopback_card_from_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    p = tmp_path / "c.toml"
    p.write_text(f'[card]\nip = "{REAL_CARD_IP}"\n')
    assert cli.main(["run", "--config", str(p), "--socket", str(tmp_path / "s.sock")]) == 2
    assert "--card-ip" in capsys.readouterr().err


def test_cli_version_and_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("nexcut-mccd ")
    assert cli.main([]) == 0
