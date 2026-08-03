from __future__ import annotations

import site
from pathlib import Path


def main() -> None:
    patched = False
    for site_dir in site.getsitepackages():
        target = Path(site_dir) / "build123d" / "text.py"
        if not target.exists():
            continue
        text = target.read_text(encoding="utf-8")
        old = "            for result in results:\n                font_faces += self.register_font(result, override, single_stroke)\n"
        new = (
            "            for result in results:\n"
            "                try:\n"
            "                    font_faces += self.register_font(result, override, single_stroke)\n"
            "                except Exception:\n"
            "                    continue\n"
        )
        if old in text:
            target.write_text(text.replace(old, new), encoding="utf-8")
            patched = True
            print(f"patched {target}")
        elif new in text:
            patched = True
            print(f"already patched {target}")
    if not patched:
        raise SystemExit("build123d/text.py not found or patch pattern changed")


if __name__ == "__main__":
    main()

