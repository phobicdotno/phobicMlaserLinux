"""Daemon / application configuration: ``$XDG_CONFIG_HOME/nexcut/config.toml`` (tomllib).

The file is optional; every key has a default. Sections:

* ``[card]`` - the ``ipAdd.ini [IP] CardIP/CardPort`` and ``[Soft] AccessType`` equivalents
  (04 §1). **Deviation (PORT-PLAN §8, docs/DECISIONS.md D3):** the default card address is
  ``127.0.0.1`` (the simulator), not the vendor's ``10.1.1.168``. A non-loopback address
  from this file is refused by ``nexcut-mccd run`` unless the operator also passes it with
  ``--card-ip`` (see :func:`card_ip_needs_cli_confirmation`).
* ``[mc]`` - the ``ipAdd.ini [Soft]`` link timing keys with the shipped values (04 §1
  table "Timing / retry keys"; ``MCMaxSendTime`` is read and ignored by the vendor, see
  :mod:`nexcut.mcc.transaction`).
* ``[mccd]`` - daemon scheduler, IPC and watchdog knobs (11 §3/§4.7, PORT-PLAN §3.4/§8.2).
  Values that are port choices rather than vendor evidence are marked UNVERIFIED.
* ``[motion]`` - jog limits, PC-side soft limits (01 §2.2 ``MAC/MAC_1.SoftLimitMaxLen``)
  and the F8 un-homed jog rule (docs/DECISIONS.md D1).

Unknown sections/keys and wrongly typed values raise :class:`ConfigError`: a typo in a
safety key must not silently fall back to a default.
"""

from __future__ import annotations

import ipaddress
import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, get_type_hints

__all__ = [
    "REAL_CARD_IP",
    "CardConfig",
    "ConfigError",
    "McConfig",
    "MccdConfig",
    "MotionConfig",
    "NexcutConfig",
    "card_ip_needs_cli_confirmation",
    "default_config_path",
    "default_socket_path",
    "is_loopback",
    "load_config",
    "parse_config",
]

REAL_CARD_IP = "10.1.1.168"
"""``ipAdd.ini [IP] CardIP`` of the machine (04 §1). Never a default in this port."""


class ConfigError(ValueError):
    """Invalid configuration file content."""


@dataclass(frozen=True, slots=True)
class CardConfig:
    """``[card]``: card endpoint (04 §1 ``CardIP``/``CardPort``/``AccessType``)."""

    ip: str = "127.0.0.1"
    """Deviation from ``ipAdd.ini`` (10.1.1.168): simulator by default (PORT-PLAN §8)."""
    port: int = 502
    access_type: int = 1
    """1 = UDP (shipped value). 0 = TCP is not implemented by this port (04 §1)."""


@dataclass(frozen=True, slots=True)
class McConfig:
    """``[mc]``: ``ipAdd.ini [Soft]`` link timing (04 §1, shipped ini values)."""

    connect_wait_ms: int = 500
    mc_timeout_ms: int = 500
    mc_max_send_time: int = 2
    """Read and never used by the vendor (``CVirtualMachine+0x814``, transaction.py)."""
    mc_max_recv_time: int = 3
    mc_send_interval_ms: int = 1
    mc_fifo_time_ms: int = 1600
    fifo_timeout_ms: int = 600
    fifo_alarm_num: int = 30
    max_item_per_frame: int = 60
    max_fill_item: int = 2000
    mc_core_ms: int = 30
    """Status poll period base: block 1000/36 every ``mc_core_ms * mc_update_factor`` (11 §3.2)."""
    mc_update_factor: int = 1
    zf_core_ms: int = 500
    zf_update_factor: int = 20
    min_hardware_ver: int = 20152
    """``MinHardwareVer``: reg 1001 must be >= this (11 §4.1 row 1)."""
    enable_log: int = 0


@dataclass(frozen=True, slots=True)
class MccdConfig:
    """``[mccd]``: daemon knobs (PORT-PLAN §3.4 scheduler, §8.2 watchdog/deadman)."""

    socket_path: str = ""
    """Empty = ``$XDG_RUNTIME_DIR/nexcut/mccd.sock`` (:func:`default_socket_path`)."""
    poll_timeout_ms: int = 150
    """Single-try timeout of the fast 1000/36 poll. UNVERIFIED port choice: the vendor uses
    the idle ladder (3.5 s), which would hide a dead link from the 1 s watchdog."""
    slow_timeout_ms: int = 150
    """Single-try timeout of slow-queue and IPC reads, bounding how long a deaf block
    (08 §4.3) can hold the transport away from the fast poll. UNVERIFIED port choice."""
    axis_ro_period_ms: int = 90
    """2000/50 period (UNVERIFIED: vendor reads it every 30 ms cycle, registers.PORT_POLL_SCHEDULE)."""
    fast_combined_period_ms: int = 1000
    """60001/120 period; 0 disables (UNVERIFIED period; vendor fast mode reads it every cycle)."""
    system_rw_period_ms: int = 1000
    """50000/26 period (UNVERIFIED; card is authoritative for K / bus cycle / ZFType, 11 §3.2)."""
    zf_status_period_ms: int = 10000
    """10000/18 period, only while ZFType != 0 (ZFCore x ZFUpdateFactor, LIKELY 10 s)."""
    watchdog_timeout_ms: int = 1000
    """Status poll age that counts as comm loss -> stop + disarm (PORT-PLAN §8.2)."""
    deadman_timeout_ms: int = 200
    """Continuous-jog refresh deadline (PORT-PLAN §8.2)."""
    input_silence_timeout_ms: int = 1040
    """Default silence timeout of an input source such as the pendant (11 N7, A8 §7: 13 x 80 ms)."""
    reconnect_interval_ms: int = 2000
    """Start-up sequence retry period while the card is not connected. UNVERIFIED choice."""
    home_timeout_ms: int = 120000
    """Per-axis homing supervision timeout. UNVERIFIED choice."""
    watchdog_di_alarms: tuple[tuple[int, bool], ...] = ()
    """``[[port, normally_closed], ...]`` DI alarms for the watchdog (11 §4.7 row 6: chiller
    DI11 NC, door DI4). Empty by default: the DI polarity (raw level vs card-inverted,
    A2 §2.1) is UNVERIFIED until the bench step of 11 §7."""


@dataclass(frozen=True, slots=True)
class MotionConfig:
    """``[motion]``: jog limits and soft limits (PORT-PLAN §8.2, docs/DECISIONS.md D1/D2)."""

    unhomed_max_step_mm: float = 10.0
    """F8 decision D1: largest relative jog on an axis whose position is unknown."""
    unhomed_max_jog_speed_mm_s: float = 20.0
    """F8 decision D1: continuous jog on an un-homed axis only at <= this speed, under the deadman."""
    max_jog_speed_mm_s: float = 200.0
    """``MC.JogFastSpeed`` of this machine (11 §2 header); IPC jog speeds above are refused."""
    max_step_mm: float = 1371.0
    """Largest step accepted over IPC once homed (soft limits still apply)."""
    soft_limit_x_mm: tuple[float, float] = (0.0, 1371.0)
    """``MAC.SoftLimitMaxLen`` 1371, lower bound 0 for GoOriginalDirection 0 (01 §2.2).
    UNVERIFIED that the card origin is the home corner (11 §7 step 4)."""
    soft_limit_y_mm: tuple[float, float] = (0.0, 950.0)
    """``MAC_1.SoftLimitMaxLen`` 950 (01 §2.2). Same caveat."""
    position_counts_per_mm: tuple[float, float] = (1000.0, 1000.0)
    """Axis RO word 2 counts per mm for X/Y. 1000 = simulator convention (µm). UNVERIFIED for
    the card, which reports motor pulses there (A2 §3; about 258.04 / 257.99 p/mm, 11 §5.3)."""
    position_scale_verified: bool = False
    """Soft limits from read-back positions are only trusted when true; otherwise homed axes
    keep the un-homed jog rule (docs/DECISIONS.md D2)."""


@dataclass(frozen=True, slots=True)
class NexcutConfig:
    """Whole configuration file."""

    card: CardConfig = field(default_factory=CardConfig)
    mc: McConfig = field(default_factory=McConfig)
    mccd: MccdConfig = field(default_factory=MccdConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    source: Path | None = None
    """File the values came from (None = defaults only)."""

    def with_card(self, ip: str | None = None, port: int | None = None) -> NexcutConfig:
        """Copy with the card endpoint replaced (CLI ``--card-ip`` / ``--card-port``)."""
        card = self.card
        if ip is not None:
            card = replace(card, ip=_check_ip(ip, "card.ip"))
        if port is not None:
            card = replace(card, port=_check_port(port, "card.port"))
        return replace(self, card=card)


_SECTIONS: dict[str, type] = {
    "card": CardConfig,
    "mc": McConfig,
    "mccd": MccdConfig,
    "motion": MotionConfig,
}


def default_config_path(env: Mapping[str, str] | None = None) -> Path:
    """``$XDG_CONFIG_HOME/nexcut/config.toml`` (fallback ``~/.config``, XDG base-dir spec)."""
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or str(Path(env.get("HOME", str(Path.home()))) / ".config")
    return Path(base) / "nexcut" / "config.toml"


def default_socket_path(env: Mapping[str, str] | None = None) -> Path:
    """``$XDG_RUNTIME_DIR/nexcut/mccd.sock``; without XDG_RUNTIME_DIR ``/tmp/nexcut-<uid>/``."""
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "nexcut" / "mccd.sock"
    return Path("/tmp") / f"nexcut-{os.getuid()}" / "mccd.sock"


def is_loopback(ip: str) -> bool:
    """True for 127.0.0.0/8 and ::1 (and ``localhost``)."""
    if ip == "localhost":
        return True
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def card_ip_needs_cli_confirmation(config: NexcutConfig, cli_card_ip: str | None) -> bool:
    """True if the configured card address is not loopback and was not given on the CLI.

    PORT-PLAN §8 / docs/DECISIONS.md D3: the real card is used only when the operator
    passes its address explicitly.
    """
    return cli_card_ip is None and not is_loopback(config.card.ip)


def _check_ip(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{key}: expected a string")
    if value != "localhost":
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            raise ConfigError(f"{key}: {value!r} is not an IP address") from exc
    return value


def _check_port(value: object, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 65536:
        raise ConfigError(f"{key}: expected a port number 1..65535")
    return value


def _coerce(key: str, typ: Any, value: object) -> object:
    """Validate one TOML value against the dataclass field type."""
    if typ is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"{key}: expected true/false")
        return value
    if typ is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{key}: expected an integer")
        return value
    if typ is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ConfigError(f"{key}: expected a number")
        f = float(value)
        if not math.isfinite(f):
            raise ConfigError(f"{key}: must be finite")
        return f
    if typ is str:
        if not isinstance(value, str):
            raise ConfigError(f"{key}: expected a string")
        return value
    if typ == tuple[float, float]:
        if not isinstance(value, list) or len(value) != 2:
            raise ConfigError(f"{key}: expected [a, b]")
        return tuple(_coerce(key, float, v) for v in value)
    if typ == tuple[tuple[int, bool], ...]:
        if not isinstance(value, list):
            raise ConfigError(f"{key}: expected [[port, normally_closed], ...]")
        out = []
        for item in value:
            if not isinstance(item, list) or len(item) != 2:
                raise ConfigError(f"{key}: expected [[port, normally_closed], ...]")
            out.append((_coerce(key, int, item[0]), _coerce(key, bool, item[1])))
        return tuple(out)
    raise ConfigError(f"{key}: unsupported type {typ!r}")  # pragma: no cover


def _validate(cfg: NexcutConfig) -> NexcutConfig:
    c, mc, d, m = cfg.card, cfg.mc, cfg.mccd, cfg.motion
    _check_ip(c.ip, "card.ip")
    _check_port(c.port, "card.port")
    if c.access_type != 1:
        raise ConfigError("card.access_type: only 1 (UDP) is implemented (04 §1)")
    for name in (
        "mc_timeout_ms",
        "mc_max_recv_time",
        "mc_fifo_time_ms",
        "fifo_timeout_ms",
        "mc_core_ms",
        "mc_update_factor",
    ):
        if getattr(mc, name) <= 0:
            raise ConfigError(f"mc.{name}: must be > 0")
    if mc.mc_send_interval_ms < 0:
        raise ConfigError("mc.mc_send_interval_ms: must be >= 0")
    for name in (
        "poll_timeout_ms",
        "slow_timeout_ms",
        "axis_ro_period_ms",
        "system_rw_period_ms",
        "zf_status_period_ms",
        "watchdog_timeout_ms",
        "deadman_timeout_ms",
        "input_silence_timeout_ms",
        "reconnect_interval_ms",
        "home_timeout_ms",
    ):
        if getattr(d, name) <= 0:
            raise ConfigError(f"mccd.{name}: must be > 0")
    if d.fast_combined_period_ms < 0:
        raise ConfigError("mccd.fast_combined_period_ms: must be >= 0 (0 = off)")
    if d.poll_timeout_ms >= d.watchdog_timeout_ms or d.slow_timeout_ms >= d.watchdog_timeout_ms:
        raise ConfigError("mccd: read timeouts must be shorter than watchdog_timeout_ms")
    for port, _nc in d.watchdog_di_alarms:
        if not 1 <= port <= 28:
            raise ConfigError("mccd.watchdog_di_alarms: DI port must be 1..28 (0 = unassigned)")
    for name in ("unhomed_max_step_mm", "unhomed_max_jog_speed_mm_s", "max_jog_speed_mm_s"):
        if getattr(m, name) <= 0:
            raise ConfigError(f"motion.{name}: must be > 0")
    if m.unhomed_max_jog_speed_mm_s > m.max_jog_speed_mm_s:
        raise ConfigError("motion.unhomed_max_jog_speed_mm_s: must be <= max_jog_speed_mm_s")
    if m.max_step_mm < m.unhomed_max_step_mm:
        raise ConfigError("motion.max_step_mm: must be >= unhomed_max_step_mm")
    for name in ("soft_limit_x_mm", "soft_limit_y_mm"):
        lo, hi = getattr(m, name)
        if not lo < hi:
            raise ConfigError(f"motion.{name}: lower bound must be below upper bound")
    if any(v <= 0 for v in m.position_counts_per_mm):
        raise ConfigError("motion.position_counts_per_mm: must be > 0")
    return cfg


def parse_config(data: Mapping[str, Any], source: Path | None = None) -> NexcutConfig:
    """Build a :class:`NexcutConfig` from parsed TOML (strict: unknown keys raise)."""
    sections: dict[str, object] = {}
    for name, value in data.items():
        cls = _SECTIONS.get(name)
        if cls is None:
            raise ConfigError(f"unknown section [{name}]")
        if not isinstance(value, dict):
            raise ConfigError(f"[{name}] must be a table")
        hints = get_type_hints(cls)
        known = {f.name for f in fields(cls)}
        kwargs = {}
        for key, v in value.items():
            if key not in known:
                raise ConfigError(f"unknown key {name}.{key}")
            kwargs[key] = _coerce(f"{name}.{key}", hints[key], v)
        sections[name] = cls(**kwargs)
    return _validate(NexcutConfig(**sections, source=source))  # type: ignore[arg-type]


def load_config(
    path: Path | str | None = None, env: Mapping[str, str] | None = None
) -> NexcutConfig:
    """Load ``path`` (default :func:`default_config_path`); a missing file gives the defaults.

    An explicitly given ``path`` that does not exist raises :class:`ConfigError`.
    """
    explicit = path is not None
    p = Path(path) if path is not None else default_config_path(env)
    if not p.is_file():
        if explicit:
            raise ConfigError(f"config file {p} not found")
        return _validate(NexcutConfig())
    try:
        with p.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{p}: {exc}") from exc
    return parse_config(data, source=p)
