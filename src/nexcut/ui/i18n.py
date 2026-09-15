"""UI labels from the vendor ``Lang/lang.txt`` (analysis 06 §2), cached as JSON.

File format (06 §2, EVIDENCE): UTF-16LE with BOM, CRLF, one record per line
``ID#Chinese#English``; ``#`` is never escaped; blank lines are interspersed;
parameter labels use the ``Group.Item`` convention.  The shipped file has 3 431
records and 3 427 unique ids (``gp100``, ``pd1001``, ``A241224_0`` and
``A250616_0`` occur twice).

Port behaviour:

* :func:`parse_lang_txt` converts the file to ``{id: (chinese, english)}``.
  For a duplicated id the *first* record wins - UNVERIFIED: which record the
  vendor's ``LangModule.dll`` map keeps was not traced.
* :func:`load_catalog` stores the conversion in
  ``$XDG_CACHE_HOME/nexcut/lang-<sha256[:16]>.json`` (``~/.cache`` when unset)
  so the 290 KB UTF-16 file is parsed once per content version.
* :class:`Translator.tr` returns the English column, then a caller-supplied
  default, then the id itself (the vendor shows nothing for a missing id,
  06 §2 Readme rule 3; showing the id is a port choice).

* :func:`parse_translation_txt` reads the secondary language files
  (``French.txt`` ...; Readme format ``ID#text``, 06 §2) and tolerates the defects
  listed in 06 §1.5: 1-field lines (lost ``#``) are reported and skipped, 3-field
  lines keep the translated field, records merged by a missing CRLF are split
  at known master ids, ids with trailing blanks are stripped, duplicates keep the
  first record.  :class:`Translator` shows such a file's text, then falls back to
  the English column of ``lang.txt`` (port choice, UNVERIFIED: the vendor shows the
  id, Readme rule 2), then the caller default, then the id.

Nothing here imports Qt, so the catalog can be used by CLIs and tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "CACHE_FORMAT",
    "LANGUAGE_FILES",
    "LangCatalog",
    "LangDefect",
    "TranslationCatalog",
    "Translator",
    "cache_dir",
    "default_lang_path",
    "get_translator",
    "load_catalog",
    "parse_lang_txt",
    "parse_translation_txt",
    "set_translator",
    "tr",
]

CACHE_FORMAT = 1
"""Version of the JSON cache layout (bump when the structure changes)."""

LANG_COLUMNS = {"zh": 0, "en": 1}
"""Column index of each language inside a record's text pair (06 §2: ``ID#Chinese#English``)."""


@dataclass(slots=True)
class LangCatalog:
    """All records of one ``lang.txt``: ``entries[id] = (chinese, english)``."""

    entries: dict[str, tuple[str, str]] = field(default_factory=dict)
    duplicates: list[str] = field(default_factory=list)
    """Ids seen more than once (first record kept, UNVERIFIED rule)."""
    source: str = ""
    sha256: str = ""

    def __len__(self) -> int:
        return len(self.entries)

    def get(self, key: str, lang: str = "en") -> str | None:
        """Text of ``key`` in ``lang`` (``"en"``/``"zh"``) or ``None`` when the id is unknown."""
        rec = self.entries.get(key)
        if rec is None:
            return None
        return rec[LANG_COLUMNS[lang]]


LANGUAGE_FILES: dict[str, str] = {
    "Russian": "Russian.txt",
    "German": "German.txt",
    "Spanish": "Spanish.txt",
    "Portuguese": "Portuguese.txt",
    "French": "French.txt",
    "Italian": "Italian.txt",
    "Polish": "Polish.txt",
    "Vietnamese": "Vietnamese.txt",
    "Turkdili": "Turkdili.txt",
}
"""Secondary language name -> file (06 §1.2: ``pd378`` enum / ``lang.ini hintStr`` order
ru, de, es, pt, fr, it, pl, vi, Turkdili; ``Vietnamese_LE.txt`` is referenced by nothing)."""

_LINE_BREAK = re.compile(r"\r\n|\r|\n")
_ID_BEFORE_HASH = re.compile(r"([A-Za-z0-9_\-]{3,})#")


def _decode_lang(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


def _lines(text: str) -> list[str]:
    """Split on CR/LF only (``str.splitlines`` also splits on U+2028, FF, VT ... inside texts)."""
    return _LINE_BREAK.split(text)


def parse_lang_txt(data: bytes) -> LangCatalog:
    """Parse ``lang.txt`` bytes (06 §2 record layout).

    Accepts UTF-16 with BOM (the shipped encoding) and, as a convenience,
    BOM-less UTF-8.  Lines without two ``#`` separators are skipped.  Extra
    ``#`` characters beyond the second separator stay in the English text
    (``split('#', 2)``; the shipped file has none).
    """
    text = _decode_lang(data)
    cat = LangCatalog()
    for raw in _lines(text):
        if not raw.strip():
            continue
        parts = raw.split("#", 2)
        if len(parts) != 3:
            continue
        key, zh, en = parts
        if key in cat.entries:
            cat.duplicates.append(key)
            continue
        cat.entries[key] = (zh, en)
    cat.sha256 = hashlib.sha256(data).hexdigest()
    return cat


@dataclass(slots=True, frozen=True)
class LangDefect:
    """One tolerated defect of a secondary language file (06 §1.5)."""

    line: int
    """1-based line number in the file."""
    kind: str
    """``no-separator`` | ``empty-id`` | ``merged`` | ``three-field`` | ``id-whitespace``."""
    text: str


@dataclass(slots=True)
class TranslationCatalog:
    """A secondary language file: ``entries[id] = text``."""

    entries: dict[str, str] = field(default_factory=dict)
    duplicates: list[str] = field(default_factory=list)
    defects: list[LangDefect] = field(default_factory=list)
    unknown_ids: list[str] = field(default_factory=list)
    """Ids absent from the master catalog (obsolete ids, 06 §1.5; empty without a master)."""
    source: str = ""
    sha256: str = ""

    def __len__(self) -> int:
        return len(self.entries)


def _split_merged(line: str, known: Mapping[str, object]) -> list[str]:
    """Split a line holding several records glued by a lost CRLF (06 §1.5 Vietnamese).

    A split happens before the longest known master id (>= 3 chars) that ends right
    before a ``#`` after the line's first separator - never on arbitrary ``word#``.
    A recovered record never replaces a standalone record of the same id (the
    shipped Vietnamese.txt repeats ``mf249``/``pd125``/``pd1538``/``pd810`` on the
    next line, which is what a CRLF-splitting loader shows).
    """
    first = line.find("#")
    if first < 0 or not known:
        return [line]
    cuts: list[int] = []
    for m in _ID_BEFORE_HASH.finditer(line, first + 1):
        run, start = m.group(1), m.start(1)
        for k in range(len(run) - 3 + 1):
            if run[k:] in known:
                cut = start + k
                if cut > first + 1 and (not cuts or cut > cuts[-1]):
                    cuts.append(cut)
                break
    if not cuts:
        return [line]
    bounds = [0, *cuts, len(line)]
    return [line[a:b] for a, b in zip(bounds, bounds[1:], strict=False)]


def parse_translation_txt(data: bytes, master: LangCatalog | None = None) -> TranslationCatalog:
    """Parse a secondary language file (``ID#text``, Readme; defects of 06 §1.5).

    ``master`` (the parsed ``lang.txt``) enables splitting merged records, telling a
    leading English copy from a ``#`` inside the text, and the unknown-id report.
    """
    known: Mapping[str, object] = master.entries if master is not None else {}
    cat = TranslationCatalog(sha256=hashlib.sha256(data).hexdigest())
    recovered: dict[str, str] = {}
    for no, raw in enumerate(_lines(_decode_lang(data)), 1):
        if not raw.strip():
            continue
        records = _split_merged(raw, known)
        if len(records) > 1:
            cat.defects.append(LangDefect(no, "merged", raw))
        for part, rec in enumerate(records):
            if "#" not in rec:
                cat.defects.append(LangDefect(no, "no-separator", rec))
                continue
            key, rest = rec.split("#", 1)
            if key != key.strip():
                cat.defects.append(LangDefect(no, "id-whitespace", rec))
                key = key.strip()
            if not key:
                cat.defects.append(LangDefect(no, "empty-id", rec))
                continue
            text = rest
            if "#" in rest:
                f2, f3 = rest.split("#", 1)
                english = master.get(key, "en") if master is not None else None
                if not f2 or f2 == f3 or (english is not None and f2.strip() == english.strip()):
                    text = f3  # ``id##text`` / ``id#text#text`` / ``id#English#translation``
                    cat.defects.append(LangDefect(no, "three-field", rec))
                # else: a genuine '#' inside the text (Portuguese mf439) - keep it whole
            if part > 0:  # recovered from a merge: a standalone record of the id wins
                recovered.setdefault(key, text)
                continue
            if key in cat.entries:
                cat.duplicates.append(key)
                continue
            cat.entries[key] = text
            if master is not None and key not in known:
                cat.unknown_ids.append(key)
    for key, text in recovered.items():
        cat.entries.setdefault(key, text)
    return cat


def cache_dir(env: Mapping[str, str] | None = None) -> Path:
    """``$XDG_CACHE_HOME/nexcut`` (``~/.cache/nexcut`` when unset or empty)."""
    e = os.environ if env is None else env
    base = e.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "nexcut"


def default_lang_path(env: Mapping[str, str] | None = None) -> Path | None:
    """Locate ``lang.txt``: ``$NEXCUT_LANG_TXT``, else ``$NEXCUT_SRC/Lang/lang.txt``.

    Returns ``None`` when neither is set or the file does not exist (the UI then
    falls back to built-in English defaults / ids).
    """
    e = os.environ if env is None else env
    candidates: list[Path] = []
    if e.get("NEXCUT_LANG_TXT"):
        candidates.append(Path(e["NEXCUT_LANG_TXT"]))
    if e.get("NEXCUT_SRC"):
        candidates.append(Path(e["NEXCUT_SRC"]) / "Lang" / "lang.txt")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _cache_path(digest: str, env: Mapping[str, str] | None) -> Path:
    return cache_dir(env) / f"lang-{digest[:16]}.json"


def load_catalog(
    path: str | os.PathLike[str], *, env: Mapping[str, str] | None = None, use_cache: bool = True
) -> LangCatalog:
    """Load ``lang.txt`` through the JSON cache (module docstring).

    The cache key is the SHA-256 of the file content, so an edited file is
    re-converted automatically.  Cache write failures are ignored (read-only
    home, sandbox): the parsed catalog is returned either way.
    """
    p = Path(path)
    data = p.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    cpath = _cache_path(digest, env)
    if use_cache and cpath.is_file():
        try:
            blob = json.loads(cpath.read_text(encoding="utf-8"))
            if blob.get("format") == CACHE_FORMAT and blob.get("sha256") == digest:
                return LangCatalog(
                    entries={k: (v[0], v[1]) for k, v in blob["entries"].items()},
                    duplicates=list(blob.get("duplicates", [])),
                    source=str(p),
                    sha256=digest,
                )
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            pass  # corrupt cache: fall through and rebuild
    cat = parse_lang_txt(data)
    cat.source = str(p)
    if use_cache:
        blob = {
            "format": CACHE_FORMAT,
            "source": str(p),
            "sha256": digest,
            "duplicates": cat.duplicates,
            "entries": {k: list(v) for k, v in cat.entries.items()},
        }
        try:
            cpath.parent.mkdir(parents=True, exist_ok=True)
            tmp = cpath.with_name(cpath.name + ".tmp")
            tmp.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
            tmp.replace(cpath)
        except OSError:
            pass
    return cat


class Translator:
    """Label lookup with the fallback chain catalog -> default -> id (module docstring)."""

    def __init__(
        self,
        catalog: LangCatalog | None = None,
        lang: str = "en",
        overlay: TranslationCatalog | None = None,
    ) -> None:
        if lang not in LANG_COLUMNS:
            raise ValueError(f"unsupported language {lang!r} (use 'en' or 'zh')")
        self.catalog = catalog or LangCatalog()
        self.lang = lang
        self.overlay = overlay
        """Secondary language texts shown before the ``lang`` column (module docstring)."""

    @classmethod
    def for_language(cls, lang_dir: str | os.PathLike[str], name: str) -> Translator:
        """Translator for a ``pd378`` language name (``English``, ``简体中文``/``zh``, ``French`` ...).

        Reads ``lang.txt`` and, for a secondary language, ``LANGUAGE_FILES[name]``
        from ``lang_dir``; English is the fallback column (UNVERIFIED, module docstring).
        """
        d = Path(lang_dir)
        master = load_catalog(d / "lang.txt") if (d / "lang.txt").is_file() else None
        if name in ("English", "en"):
            return cls(master, "en")
        if name in ("简体中文", "zh", "Chinese"):
            return cls(master, "zh")
        if name not in LANGUAGE_FILES:
            raise ValueError(f"unknown language {name!r}")
        overlay = parse_translation_txt((d / LANGUAGE_FILES[name]).read_bytes(), master)
        overlay.source = str(d / LANGUAGE_FILES[name])
        return cls(master, "en", overlay)

    @classmethod
    def from_path(cls, path: str | os.PathLike[str] | None = None, lang: str = "en") -> Translator:
        """Build from ``path`` or :func:`default_lang_path`; an empty catalog when none is found."""
        p = Path(path) if path is not None else default_lang_path()
        if p is None or not p.is_file():
            return cls(None, lang)
        return cls(load_catalog(p), lang)

    def tr(self, key: str, default: str | None = None) -> str:
        """Label for ``key``: overlay text, catalog text, else ``default``, else ``key``."""
        if self.overlay is not None:
            over = self.overlay.entries.get(key)
            if over:
                return over
        text = self.catalog.get(key, self.lang)
        if text:
            return text
        return default if default is not None else key

    def item(self, key: str, default: str | None = None) -> str:
        """The ``Item`` half of a ``Group.Item`` parameter label (06 §2 Readme note)."""
        text = self.tr(key, default)
        return text.split(".", 1)[1] if "." in text else text

    def file_filter(self, key: str, default: str) -> str:
        """Convert an MFC filter ``Desc|*.a;*.b||`` (e.g. ``mf149``) into a Qt name filter.

        Result ``"Desc (*.a *.b)"``; the display half's ``*plt`` typo (06 §4.3) is
        replaced by the pattern half.
        """
        raw = self.tr(key, default)
        parts = [s for s in raw.split("|") if s]
        if len(parts) < 2:
            return raw
        desc = parts[0].split("(", 1)[0].strip()
        patterns = " ".join(x.strip() for x in parts[1].split(";") if x.strip())
        return f"{desc} ({patterns})"


_translator: Translator | None = None


def get_translator() -> Translator:
    """Process-wide translator, created lazily from :func:`default_lang_path`."""
    global _translator
    if _translator is None:
        _translator = Translator.from_path()
    return _translator


def set_translator(translator: Translator | None) -> None:
    """Replace (or reset with ``None``) the process-wide translator."""
    global _translator
    _translator = translator


def tr(key: str, default: str | None = None) -> str:
    """Shortcut for ``get_translator().tr(key, default)``."""
    return get_translator().tr(key, default)
