"""``tools/wine_session_h.sh``: the session-H Wine preparation kit (docs/WINE-SESSION-H.md).

The script prepares the *vendor* tool under Wine for the desk session that answers STATUS §3.6
(the M2 sort reference, the M3 gate, X7, the crafts diffs). Two things about it have to hold
on every machine, including CI, which has no Wine prefix and no vendor package:

* ``--dry-run`` plans the whole thing and changes **nothing**;
* no write path may ever resolve inside the read-only vendor package.

The real mode is exercised too, against a fake package and stub ``wine``/``wineboot``/
``winetricks`` binaries, because the properties that matter - idempotency, the working-copy
rule, the ``EnableLog`` edit - are not visible in a dry run.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "wine_session_h.sh"

DONGLE = ("3689", "8762")


def run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full = dict(os.environ)
    full.update(env or {})
    return subprocess.run(
        [str(SCRIPT), *args], capture_output=True, text=True, timeout=120, env=full
    )


@pytest.fixture
def tmp_path(tmp_path: Path) -> Path:  # noqa: PT004 - shadowing is deliberate
    """``tmp_path`` with symlinks resolved: the script reports realpaths, so tests must match."""
    return tmp_path.resolve()


def fake_package(root: Path) -> Path:
    """A miniature stand-in for the vendor package: enough files for every step to act."""
    src = root / "Mlaser-v0.0.0.52"
    (src / "File").mkdir(parents=True)
    (src / "MainApp.exe").write_bytes(b"MZ fake")
    (src / "File" / "ipAdd.ini").write_bytes(
        b"[IP]\r\nCardIP=10.1.1.168\r\n\r\n[Soft]\r\nLang=0\r\nEnableLog=0\r\nEnableOvertime=1\r\n"
    )
    return src


def manifest(root: Path) -> dict[str, tuple[int, str]]:
    """Every file under ``root`` with its mtime_ns and content hash."""
    out: dict[str, tuple[int, str]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = (
                p.stat().st_mtime_ns,
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
    return out


@pytest.fixture
def stub_bin(tmp_path: Path) -> Path:
    """``wine``/``wineboot``/``winetricks`` stubs that fake exactly what the script verifies."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "wine").write_text("#!/bin/sh\nexit 0\n")
    (bin_dir / "wineboot").write_text(
        '#!/bin/sh\nmkdir -p "$WINEPREFIX/drive_c/windows/system32"\n'
        'printf "#arch=%s\\n" "$WINEARCH" > "$WINEPREFIX/system.reg"\n'
    )
    (bin_dir / "winetricks").write_text(
        '#!/bin/sh\nmkdir -p "$WINEPREFIX/drive_c/windows/system32"\n'
        'printf "fake\\n" > "$WINEPREFIX/drive_c/windows/system32/mfc42.dll"\n'
    )
    for p in bin_dir.iterdir():
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return bin_dir


# ------------------------------------------------------------------------------- basics


def test_script_is_executable_and_syntactically_valid() -> None:
    assert SCRIPT.is_file() and os.access(SCRIPT, os.X_OK)
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True).returncode == 0


def test_help_lists_every_option() -> None:
    r = run("--help")
    assert r.returncode == 0
    for flag in ("--dry-run", "--prefix", "--src", "--work"):
        assert flag in r.stdout
    assert "set -euo pipefail" not in r.stdout  # the header only, not the code


def test_unknown_argument_is_a_usage_error() -> None:
    r = run("--nonsense")
    assert r.returncode == 2 and "unknown argument" in r.stderr


# ------------------------------------------------------------------------------ dry run


def test_dry_run_without_a_wine_prefix_plans_every_step_and_creates_nothing(
    tmp_path: Path,
) -> None:
    src = fake_package(tmp_path)
    prefix, work = tmp_path / "wineprefix", tmp_path / "work"
    before = manifest(src)

    r = run("--dry-run", "--src", str(src), "--prefix", str(prefix), "--work", str(work))

    assert r.returncode == 0, r.stderr
    out = r.stdout
    # nothing was created anywhere
    assert not prefix.exists() and not work.exists()
    assert manifest(src) == before
    # ... and the plan covers all seven steps of docs/WINE-SESSION-H.md §1
    assert f"copy {src} -> {work / 'Mlaser'}" in out  # 1 working copy
    assert "WINEARCH=win32 wineboot -u" in out  # 2 the prefix
    assert "winetricks -q mfc42" in out  # 3 MFC42
    assert f"would symlink {prefix / 'drive_c' / 'Mlaser'} -> {work / 'Mlaser'}" in out  # 4
    assert f"would create {prefix / 'drive_c' / 'Technology' / 'Fiber'}" in out  # 5
    assert f"would create {prefix / 'drive_c' / 'Technology' / 'CO2'}" in out
    assert f"would set EnableLog=1 in [Soft] of {work / 'Mlaser' / 'File' / 'ipAdd.ini'}" in out
    assert "99-mlaser-dongle.rules" in out  # 7 the udev rule
    assert "DRY RUN" in out and "nothing was created" in out


def test_dry_run_prints_the_pkexec_command_instead_of_installing_the_udev_rule(
    tmp_path: Path,
) -> None:
    """Installing the rule needs root; the script must never elevate by itself."""
    src = fake_package(tmp_path)
    r = run(
        "--dry-run",
        "--src", str(src),
        "--prefix", str(tmp_path / "p"),
        "--work", str(tmp_path / "w"),
    )  # fmt: skip
    assert r.returncode == 0
    assert "pkexec install -m 0644" in r.stdout
    assert "/etc/udev/rules.d/99-mlaser-dongle.rules" in r.stdout
    assert "udevadm control --reload-rules" in r.stdout
    # it says it is not doing it, and it does not try
    assert "would NOT install it" in r.stdout
    assert "sudo " not in r.stdout


def test_dry_run_works_without_the_vendor_package(tmp_path: Path) -> None:
    """CI has no package: the dry run must still plan, and say what is missing."""
    missing = tmp_path / "no-such-package"
    r = run(
        "--dry-run",
        "--src", str(missing),
        "--prefix", str(tmp_path / "p"),
        "--work", str(tmp_path / "w"),
    )  # fmt: skip
    assert r.returncode == 0, r.stderr
    assert f"MISSING: vendor package at {missing}" in r.stdout
    assert not (tmp_path / "p").exists() and not (tmp_path / "w").exists()


# --------------------------------------------------------- the read-only package guard


@pytest.mark.parametrize("bad", ["work-inside-src", "prefix-inside-src", "src-inside-work"])
def test_no_write_target_may_resolve_inside_the_vendor_package(tmp_path: Path, bad: str) -> None:
    """The package is evidence; the script refuses the arrangement, it does not just avoid it."""
    src = fake_package(tmp_path)
    args = {
        "work-inside-src": ("--work", str(src / "inside"), "--prefix", str(tmp_path / "p")),
        "prefix-inside-src": ("--prefix", str(src / "pfx"), "--work", str(tmp_path / "w")),
        "src-inside-work": ("--work", str(tmp_path), "--prefix", str(tmp_path / "p")),
    }[bad]
    r = run("--dry-run", "--src", str(src), *args)
    assert r.returncode != 0
    assert "FAILED" in r.stderr and "must not be inside" in r.stderr
    assert not (tmp_path / "p").exists()


# ------------------------------------------------------------------- real mode, stubbed


def test_real_run_prepares_everything_and_is_idempotent(tmp_path: Path, stub_bin: Path) -> None:
    src = fake_package(tmp_path)
    prefix, work = tmp_path / "wineprefix", tmp_path / "work"
    before = manifest(src)
    env = {"PATH": f"{stub_bin}:{os.environ['PATH']}"}

    r = run("--src", str(src), "--prefix", str(prefix), "--work", str(work), env=env)
    assert r.returncode == 0, r.stdout + r.stderr

    copy = work / "Mlaser"
    assert (copy / "MainApp.exe").is_file()
    assert (prefix / "system.reg").read_text().strip() == "#arch=win32"
    assert (prefix / "drive_c/windows/system32/mfc42.dll").is_file()
    link = prefix / "drive_c/Mlaser"
    assert link.is_symlink() and Path(os.readlink(link)) == copy
    assert (prefix / "drive_c/Technology/Fiber").is_dir()
    assert (prefix / "drive_c/Technology/CO2").is_dir()
    # EnableLog flipped in the copy, CRLF and everything else kept
    ini = (copy / "File/ipAdd.ini").read_bytes()
    assert b"EnableLog=1\r\n" in ini and b"EnableLog=0" not in ini
    assert ini.count(b"\r\n") == (src / "File/ipAdd.ini").read_bytes().count(b"\r\n")
    rule = (work / "99-mlaser-dongle.rules").read_text()
    assert all(v in rule for v in DONGLE) and 'TAG+="uaccess"' in rule
    assert 'SUBSYSTEM=="hidraw"' in rule
    # the original package is untouched, contents and mtimes
    assert manifest(src) == before

    # ... and running it again changes nothing and reports every step as already done
    copy_before = manifest(copy)
    second = run("--src", str(src), "--prefix", str(prefix), "--work", str(work), env=env)
    assert second.returncode == 0, second.stdout + second.stderr
    for done in ("working copy exists", "prefix exists and is win32", "mfc42.dll present",
                 "already  C:\\Mlaser already points at the working copy",
                 "EnableLog is already 1"):
        assert done in second.stdout, done
    assert manifest(src) == before
    assert manifest(copy) == copy_before  # the working copy keeps the session's edits


def test_real_run_refuses_a_foreign_symlink_rather_than_replacing_it(
    tmp_path: Path, stub_bin: Path
) -> None:
    """C:\\Mlaser pointing somewhere else is a mistake to report, not to silently fix."""
    src = fake_package(tmp_path)
    prefix, work = tmp_path / "wineprefix", tmp_path / "work"
    env = {"PATH": f"{stub_bin}:{os.environ['PATH']}"}
    assert run("--src", str(src), "--prefix", str(prefix), "--work", str(work), env=env).returncode == 0

    link = prefix / "drive_c/Mlaser"

    # (a) pointed straight at the read-only package - the mistake the working copy prevents
    link.unlink()
    link.symlink_to(src)
    r = run("--src", str(src), "--prefix", str(prefix), "--work", str(work), env=env)
    assert r.returncode != 0
    assert "refusing to write inside the read-only vendor package" in r.stderr
    assert link.is_symlink() and Path(os.readlink(link)) == src  # untouched

    # (b) pointed at some other directory - reported, never silently repointed
    other = tmp_path / "somewhere-else"
    other.mkdir()
    link.unlink()
    link.symlink_to(other)
    r = run("--src", str(src), "--prefix", str(prefix), "--work", str(work), env=env)
    assert r.returncode != 0
    assert "not at" in r.stderr
    assert link.is_symlink() and Path(os.readlink(link)) == other
