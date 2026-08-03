import unittest

from mechcad.clarification import build_clarification_questions, dimension_ledger_markdown


class ClarificationTests(unittest.TestCase):
    def test_unresolved_dimensions_become_user_questions(self):
        questions = build_clarification_questions(
            {"uncertainties": []},
            {
                "unresolved": [
                    {"feature": "top_groove", "reason": "axial width and position are missing"}
                ]
            },
        )
        self.assertEqual(len(questions), 1)
        self.assertIn("槽宽多少 mm", questions[0])
        self.assertIn("从底端多少 mm 到多少 mm", questions[0])

    def test_duplicate_uncertainties_are_deduplicated(self):
        questions = build_clarification_questions(
            {"uncertainties": ["槽尺寸不清楚", "槽尺寸不清楚"]},
            {"unresolved": []},
        )
        self.assertEqual(len(questions), 1)

    def test_dimension_ledger_lists_visible_annotations(self):
        markdown = dimension_ledger_markdown(
            {
                "sketch": {
                    "raw_annotations": [
                        {"text": "300", "endpoints_or_region": "overall length", "confidence": 1.0},
                        {"text": "⌀50", "endpoints_or_region": "main outside diameter", "confidence": 0.95},
                    ]
                }
            }
        )

        self.assertIn("图上已识别尺寸", markdown)
        self.assertIn("300", markdown)
        self.assertIn("⌀50", markdown)


if __name__ == "__main__":
    unittest.main()
