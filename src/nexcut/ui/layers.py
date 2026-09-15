"""Layer table of the canvas and layer dock: names and display colours (analysis 02 §4).

Names (EVIDENCE, 02 §4 verifier notes): 11 slots, index 0 = background layer
(``gp81`` "Bk Layer"; the name builder at ``0x54eae0`` uses ``BackLayer``),
indices 1..9 = ``gp83`` "Layer" followed by the index formatted with ``%d``
(no separator, so "Layer1"), index 10 = film layer (``lp6`` "Film Layer",
``0x54d3e6``).  Every sample ``.chf`` contour is on index 0 (02 §4).

Colours: UNVERIFIED.  ``BkLayerPara.xml`` carries no colour attribute, and a
scan of ``MainApp.exe``, ``CADModule.dll`` and ``ParaModule.dll`` for COLORREF
or ``glColor3d`` double tables found no layer palette (only the VGA-16 bitmap
palette at ``MainApp.exe`` file offset ``0x674644``).  Until a screenshot or the
colour source is traced, the palette is the one of the M2 reference renders
(``tools/chf_parse.py`` ``LAYER_COLORS``, cycled to 11 slots), so layer 0 is
the red of ``tools/out/*.png``.

Qt-free: colours are ``#rrggbb`` strings.
"""

from __future__ import annotations

from nexcut.ui.i18n import Translator, get_translator

__all__ = [
    "LAYER_COLORS",
    "LAYER_COUNT",
    "SCANPATH_COLOR",
    "layer_color",
    "layer_name",
]

LAYER_COUNT = 11
"""Number of layer slots (02 §4: 11 ``PLayerParam``/``PCO2LayerParam`` groups)."""

FILM_LAYER = 10
"""Index of the film-removal layer (02 §4, ``0x54d3e6``)."""

_REFERENCE_PALETTE = [
    "#d62728",
    "#1f77b4",
    "#2ca02c",
    "#ff7f0e",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#17becf",
    "#bcbd22",
    "#7f7f7f",
]

LAYER_COLORS: list[str] = [
    _REFERENCE_PALETTE[i % len(_REFERENCE_PALETTE)] for i in range(LAYER_COUNT)
]
"""Display colour per layer index - UNVERIFIED (module docstring)."""

SCANPATH_COLOR = "#969696"
"""Generated scan paths are drawn grey, as in the reference renders (``tools/chf_parse.py``)."""


def layer_color(index: int, palette: list[str] | None = None) -> str:
    """Colour of layer ``index``; out-of-range indices wrap around the palette."""
    pal = palette or LAYER_COLORS
    return pal[index % len(pal)]


def layer_name(index: int, translator: Translator | None = None) -> str:
    """Display name of layer ``index`` (02 §4: gp81 / gp83+%d / lp6)."""
    t = translator or get_translator()
    if index == 0:
        return t.tr("gp81", "Bk Layer")
    if index == FILM_LAYER:
        return t.tr("lp6", "Film Layer")
    return f"{t.tr('gp83', 'Layer')}{index}"
