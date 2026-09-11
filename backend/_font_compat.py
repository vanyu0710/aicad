"""Runtime compatibility for build123d's Windows system-font scan.

build123d 0.11.1 assumes every TTF/OTF/TTC file under the Windows font folders
parses cleanly. Real machines disagree: zero-byte placeholders and metrics-only
files (e.g. mstmc.ttf) live in C:\\Windows\\Fonts and abort ``import build123d``
with a fontTools TTLibError before any backend module finishes loading.

We narrow ``glob.glob`` to skip unparseable font files, but only while the
first build123d import runs, then restore the original. Geometry features that
need fonts (text engraving) are unaffected: valid fonts still register.
"""

from __future__ import annotations

import glob as _glob_module
import importlib
import importlib.util
from pathlib import Path
from typing import Any


def _parseable_font(path: str) -> bool:
    """True when fontTools can at least open the file (header + table index).

    Mirrors mech_kernel/_runtime_compat.py: lazy loading reads the table
    directory, not glyph outlines, so this rejects corrupt or metrics-only
    files cheaply, exactly like build123d's TTFont call would experience.
    """
    try:
        if Path(path).stat().st_size < 1024:
            return False
    except OSError:
        return False
    try:
        from fontTools.ttLib import TTFont, ttCollection
    except ImportError:
        return True
    handle = None
    try:
        if path.lower().endswith(".ttc"):
            handle = ttCollection.TTCollection(path)
        else:
            handle = TTFont(path, lazy=True)
    except Exception:
        return False
    finally:
        try:
            if handle is not None:
                handle.close()
        except Exception:
            pass
    return True


def _font_safe_glob(original: Any):
    def safe_glob(pattern: str, *args: Any, **kwargs: Any) -> list[str]:
        paths = original(pattern, *args, **kwargs)
        if not any(token in pattern.lower() for token in ("ttf", "otf", "ttc")):
            return paths
        return [path for path in paths if _parseable_font(path)]

    return safe_glob


def ensure_build123d_import() -> None:
    """Import build123d once, immune to malformed system fonts."""

    if importlib.util.find_spec("build123d") is None:
        return

    original = _glob_module.glob
    _glob_module.glob = _font_safe_glob(original)
    try:
        importlib.import_module("build123d")
    finally:
        _glob_module.glob = original
