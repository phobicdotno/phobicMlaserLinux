"""``nexcut`` console entry: the GUI, plus the offscreen ``render`` sub-command (PORT-PLAN §4 M2).

Usage::

    nexcut [FILE] [--lang-txt PATH] [--lang en|zh] [--manu XML] [--hard XML] [--layer XML]
    nexcut render FILE -o OUT.png [--start] [--arrows] [--index] [--bed] ...

``--manu`` / ``--hard`` read vendor ``ManuPara``/``HardPara`` XML (01 §0.1) for
the view toggles, import gates and bed size; ``--layer`` reads ``LayerPara`` XML
for the layer-parameter dock (02).  Without them descriptor defaults /
1300 x 900 are used.  ``lang.txt`` is found via ``--lang-txt``,
``$NEXCUT_LANG_TXT`` or ``$NEXCUT_SRC/Lang/lang.txt`` (:mod:`nexcut.ui.i18n`).

SAFETY (PORT-PLAN §8): the application never contacts the controller in this
milestone; there is no card address option.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

__all__ = ["build_parser", "main"]


def build_parser() -> argparse.ArgumentParser:
    """Argument parser of the GUI entry."""
    p = argparse.ArgumentParser(
        prog="nexcut",
        description="NexCut for Linux (file load and render; no machine control yet).",
    )
    p.add_argument(
        "file", nargs="?", type=Path, help="job file to open (.chf/.dxf/.plt/.nc/.txt/.cnc/.g)"
    )
    p.add_argument("--lang-txt", type=Path, default=None, help="vendor Lang/lang.txt")
    from nexcut.ui.i18n import LANGUAGE_FILES

    p.add_argument(
        "--lang",
        choices=("en", "zh", *LANGUAGE_FILES),
        default="en",
        help="label language; secondary files (French ...) sit next to lang.txt (06 §1.5)",
    )
    p.add_argument(
        "--manu", type=Path, default=None, help="ManuPara XML (view toggles, import gates)"
    )
    p.add_argument("--hard", type=Path, default=None, help="HardPara XML (bed size)")
    p.add_argument(
        "--layer", type=Path, default=None, help="LayerPara XML (layer-parameter dock, 02)"
    )
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``nexcut``; returns the process exit code."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] == "render":
        from nexcut.ui.render import main as render_main

        return render_main(args_list[1:])
    args = build_parser().parse_args(args_list)

    from PySide6.QtWidgets import QApplication

    from nexcut.io.params import ParamFileError, read_params
    from nexcut.ui.i18n import LANGUAGE_FILES, Translator, default_lang_path, set_translator
    from nexcut.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([sys.argv[0] if sys.argv else "nexcut"])
    app.setApplicationName("nexcut")
    app.setOrganizationName("nexcut")
    if args.lang in LANGUAGE_FILES:
        lang_txt = args.lang_txt or default_lang_path()
        translator = (
            Translator.for_language(lang_txt.parent, args.lang)
            if lang_txt is not None and (lang_txt.parent / LANGUAGE_FILES[args.lang]).is_file()
            else Translator.from_path(args.lang_txt, "en")
        )
    else:
        translator = Translator.from_path(args.lang_txt, args.lang)
    set_translator(translator)
    try:
        manu = read_params(args.manu, "manu") if args.manu else None
        hard = read_params(args.hard, "hard") if args.hard else None
        layer = read_params(args.layer, "layer") if args.layer else None
    except ParamFileError as exc:
        print(f"nexcut: {exc}", file=sys.stderr)
        return 2
    win = MainWindow(translator=translator, manu=manu, hard=hard, layer=layer)
    win.show()
    if args.file is not None:
        win.open_path(args.file)
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
