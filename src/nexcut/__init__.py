"""nexcut: native Linux reimplementation of the Mlaser/NexCut laser-cutter software.

Package layout follows docs/PORT-PLAN.md §2.3 (repository layout) and §3
(module breakdown). Nothing here reads the original vendor package at import
time; tests obtain its path through the ``src_dir`` fixture in tests/conftest.py.
"""

__version__ = "0.0.1"
