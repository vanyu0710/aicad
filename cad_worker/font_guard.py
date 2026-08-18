"""Prevent Build123d FontManager from crashing on corrupt Windows fonts.

The CAD worker never needs system fonts. Scanning C:/Windows/Fonts can raise
TTLibError on a truncated file and abort the whole subprocess before any
FeaturePlan is executed.
"""

from __future__ import annotations

import glob


def install() -> None:
    original = glob.glob

    def _skip_font_glob(pathname, *args, **kwargs):
        text = str(pathname).replace("\\", "/").lower()
        if text.endswith((".ttf", ".otf", ".ttc")) or text.endswith(("*ttf", "*otf", "*ttc")):
            return []
        return original(pathname, *args, **kwargs)

    glob.glob = _skip_font_glob  # type: ignore[assignment]
