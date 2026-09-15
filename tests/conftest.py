"""Shared pytest fixtures.

The original vendor package (SRC) is proprietary and lives outside the
repository. Tests that need it request the ``src_dir`` fixture, which reads
``NEXCUT_SRC`` (default: the owner's local copy) and skips when the directory
is absent, so the suite stays green in CI.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

DEFAULT_SRC = Path("/home/karstein/Documents/CF1390-250715-1084-0973/Mlaser-v0.0.0.52")


def src_path() -> Path:
    """Return the configured SRC path without checking that it exists."""
    return Path(os.environ.get("NEXCUT_SRC", str(DEFAULT_SRC)))


@pytest.fixture(scope="session")
def src_dir() -> Path:
    """Path of the read-only original Mlaser package; skips the test if missing."""
    path = src_path()
    if not path.is_dir():
        pytest.skip(f"original Mlaser package not available at {path} (set NEXCUT_SRC)")
    return path
