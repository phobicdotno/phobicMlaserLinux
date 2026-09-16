"""Parameter pages built on :mod:`nexcut.ui.property_grid` (PORT-PLAN §4 M3).

One module per vendor page.  A page owns the descriptor selection, the section
order and the cross-field rules of that page; the grid itself is generic.

SAFETY (PORT-PLAN §8): pages edit files only.  None of them opens a socket,
moves an axis or arms a laser.
"""

from __future__ import annotations

__all__ = ["layer_co2"]
