"""backend/_font_compat.py 测试：坏系统字体不得阻断 build123d 导入。

本机 Windows 的 mstmc.ttf（数学字体度量文件）不是合法 sfnt，build123d
0.11.1 导入期扫描系统字体时会在 TTFont() 处抛 TTLibError，把整个后端打死。
守卫用 fontTools 惰性打开做真实校验，只过滤掉坏文件。
"""

from __future__ import annotations

import unittest

from backend._font_compat import _parseable_font, ensure_build123d_import


class ParseableFontTests(unittest.TestCase):
    def test_missing_file_is_not_parseable(self) -> None:
        self.assertFalse(_parseable_font("Z:/definitely/not/here.ttf"))

    def test_garbage_file_is_not_parseable(self) -> None:
        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as fh:
            fh.write(b"NOT A FONT" * 200)
            path = fh.name
        try:
            self.assertFalse(_parseable_font(path))
        finally:
            os.unlink(path)

    def test_real_font_is_parseable(self) -> None:
        import importlib.util
        from pathlib import Path

        spec = importlib.util.find_spec("build123d")
        self.assertIsNotNone(spec)
        base = Path(spec.submodule_search_locations[0])
        bundled = [str(p) for p in (base / "data" / "fonts").rglob("*.ttf")]
        self.assertTrue(bundled, "build123d 自带字体应存在")
        self.assertTrue(all(_parseable_font(p) for p in bundled))


class ImportGuardTests(unittest.TestCase):
    def test_build123d_import_succeeds(self) -> None:
        # 导入即验证守卫：backend 包 __init__ 已先行 ensure；这里再显式调用
        # 确认幂等，并真实使用几何 API。
        ensure_build123d_import()
        import build123d

        box = build123d.Box(2, 3, 4)
        self.assertAlmostEqual(box.volume, 24.0, places=6)

    def test_measurement_module_loads(self) -> None:
        from backend.geometry import measurement

        self.assertTrue(hasattr(measurement, "GeomType"))


if __name__ == "__main__":
    unittest.main()
