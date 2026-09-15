"""Console entry point for ``nexcut-mccd`` (PORT-PLAN §2.3, §4 M1 step 4).

Placeholder for M0: parses no arguments and exits after printing the version.
The real daemon (socket open, status poll, jog/home, CLI/TUI) arrives in M1.
"""

from __future__ import annotations

import sys

from nexcut import __version__


def main(argv: list[str] | None = None) -> int:
    """Entry point; returns the process exit status."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in {"-V", "--version"}:
        print(f"nexcut-mccd {__version__}")
        return 0
    print(
        f"nexcut-mccd {__version__}: driver daemon not implemented yet (M0 skeleton)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
