"""ui: PySide6 application - canvas, docking panels, property grids, control panel, alarms (PORT-PLAN §3.1).

M2 modules: ``app`` (``nexcut`` entry), ``main_window``, ``canvas``, ``render``
(offscreen), plus the Qt-free helpers ``i18n``, ``layers``, ``scene`` and
``loader``.  Importing the package itself does not import Qt.

M3 modules: ``property_grid`` (the schema-driven ``Group.Item`` grid of 06 §8)
and the ``pages`` package built on it, starting with ``pages.layer_co2``.
"""
