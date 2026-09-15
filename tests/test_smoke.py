"""Smoke tests for the M0 skeleton (PORT-PLAN §4 M0)."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import nexcut
from nexcut.mccd import cli

SUBPACKAGES = ["core", "model", "io", "ops", "plan", "mcc", "mccd", "ui"]


def test_version() -> None:
    assert nexcut.__version__ == "0.0.1"


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    mod = importlib.import_module(f"nexcut.{name}")
    assert mod.__doc__, f"nexcut.{name} needs a module docstring"


def test_mccd_cli_placeholder(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"nexcut-mccd {nexcut.__version__}"
    assert cli.main([]) == 0


def test_src_fixture_points_at_package(src_dir: Path) -> None:
    """Runs only where the vendor package is present (skips in CI)."""
    assert (src_dir / "MainApp.exe").is_file()
    assert (src_dir / "Module").is_dir()
