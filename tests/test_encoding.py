from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class EncodingRegressionTests(unittest.TestCase):
    def test_key_chinese_strings_are_utf8_clean(self) -> None:
        checks = {
            "backend/ai.py": ["\u7ba1\u4ef6", "\u5916\u5f84", "\u5185\u5f84", "\u6cd5\u5170", "\u4e25\u683c\u6a21\u5f0f", "\u667a\u80fd\u6a21\u5f0f"],
            "backend/validation.py": ["\u4e3b\u57fa\u4f53", "\u58c1\u539a", "\u4f9d\u8d56\u73af", "\u672a\u786e\u8ba4\u5047\u8bbe"],
            "cad_worker/freecad_executor.py": ["\u4e3b\u57fa\u4f53", "\u73af\u69fd", "\u7f3a\u5c11", "\u4e0d\u652f\u6301\u7684\u7279\u5f81\u7c7b\u578b"],
            "frontend/src/i18n.ts": ["\u7b49\u5f85\u8f93\u5165", "\u4e25\u683c\u6a21\u5f0f", "\u667a\u80fd\u6a21\u5f0f"],
        }
        for relative_path, needles in checks.items():
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, text, f"{relative_path} lost expected UTF-8 text")

    def test_no_unicode_replacement_characters(self) -> None:
        source_files = [
            "backend/ai.py",
            "backend/validation.py",
            "backend/cad.py",
            "cad_worker/freecad_executor.py",
            "frontend/src/i18n.ts",
        ]
        for relative_path in source_files:
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("\ufffd", text, f"{relative_path} contains U+FFFD")


if __name__ == "__main__":
    unittest.main()
