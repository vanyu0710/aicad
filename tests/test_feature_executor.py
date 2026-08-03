from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mechcad.feature_executor import build_feature_plan_code, execute_feature_plan


class FeatureExecutorTests(unittest.TestCase):
    def test_tube_with_top_groove_exports_step_and_stl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = execute_feature_plan(_tube_plan(with_groove=True), Path(tmp), timeout=60)

        self.assertTrue(result.ok, result.sandbox.stderr)
        self.assertIn("base_tube", result.modeled_features)
        self.assertIn("top_groove", result.modeled_features)
        self.assertEqual(result.skipped_features, [])

    def test_missing_groove_width_is_skipped_without_guessing(self) -> None:
        plan = _tube_plan(with_groove=True)
        plan["features"][0]["dimensions"].pop("axial_width")

        code, modeled, skipped = build_feature_plan_code(plan)

        self.assertIn("base_tube", modeled)
        self.assertNotIn("top_groove", modeled)
        self.assertEqual(skipped[0]["feature"], "top_groove")
        self.assertIn("UNRESOLVED: top_groove", code)
        self.assertNotIn("height=10", code)

    def test_plate_with_two_through_holes_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = execute_feature_plan(_plate_plan(), Path(tmp), timeout=60)

        self.assertTrue(result.ok, result.sandbox.stderr)
        self.assertEqual(result.modeled_features, ["base_plate", "hole_left", "hole_right"])

    def test_flange_circular_hole_pattern_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = execute_feature_plan(_flange_plan(), Path(tmp), timeout=60)

        self.assertTrue(result.ok, result.sandbox.stderr)
        self.assertEqual(result.modeled_features, ["base_flange", "center_bore", "bolt_circle"])

    def test_null_dimension_evidence_does_not_block_execution(self) -> None:
        plan = _tube_plan(with_groove=False)
        plan["base_feature"]["dimensions"]["width"] = {"value": None, "unit": "mm", "evidence": None}

        code, modeled, skipped = build_feature_plan_code(plan)

        self.assertIn("base_tube", modeled)
        self.assertNotIn("FeaturePlan validation failed", code)
        self.assertEqual(skipped, [])


def _tube_plan(with_groove: bool) -> dict:
    features = []
    if with_groove:
        features.append(
            {
                "id": "top_groove",
                "type": "annular_groove",
                "operation": "remove",
                "dimensions": {
                    "z_start": {"value": 290, "unit": "mm", "evidence": "overall_length - axial_width"},
                    "axial_width": {"value": 10, "unit": "mm", "evidence": "visible 10mm end dimension"},
                    "reduced_outer_diameter": {"value": 43.84, "unit": "mm", "evidence": "visible diameter"},
                },
                "placement": {"reference": "top_end", "axis": "Z"},
                "depends_on": ["base_tube"],
                "evidence": "top end groove in axial section",
            }
        )
    return {
        "schema_version": "2.0",
        "units": "mm",
        "part_family": "axisymmetric_tube",
        "coordinate_system": {"main_axis": "Z", "origin": "bottom end center"},
        "base_feature": {
            "id": "base_tube",
            "type": "hollow_cylinder",
            "operation": "base",
            "dimensions": {
                "outer_diameter": {"value": 50, "unit": "mm", "evidence": "OD 50"},
                "inner_diameter": {"value": 38, "unit": "mm", "evidence": "ID 38"},
                "length": {"value": 300, "unit": "mm", "evidence": "L 300"},
            },
            "placement": {"reference": "bottom_end_center", "axis": "Z"},
            "evidence": "dimensioned section",
        },
        "features": features,
        "unresolved": [],
        "self_checks": {"cad_ready": True},
    }


def _plate_plan() -> dict:
    base = {
        "id": "base_plate",
        "type": "box_base",
        "operation": "base",
        "dimensions": {
            "length": {"value": 80, "unit": "mm", "evidence": "L 80"},
            "width": {"value": 45, "unit": "mm", "evidence": "W 45"},
            "height": {"value": 8, "unit": "mm", "evidence": "T 8"},
        },
        "placement": {"reference": "bottom center", "axis": "Z"},
        "evidence": "plate outline",
    }
    hole = {
        "type": "through_hole",
        "operation": "remove",
        "dimensions": {"diameter": {"value": 8, "unit": "mm", "evidence": "hole diameter"}},
        "placement": {"reference": "plate center", "x": -20, "y": 0, "z": 0, "axis": "Z"},
        "extent": "through",
        "depends_on": ["base_plate"],
        "evidence": "hole center mark",
    }
    right = {**hole, "id": "hole_right", "placement": {"reference": "plate center", "x": 20, "y": 0, "z": 0, "axis": "Z"}}
    left = {**hole, "id": "hole_left"}
    return {
        "schema_version": "2.0",
        "units": "mm",
        "part_family": "plate",
        "coordinate_system": {"main_axis": "Z", "origin": "plate center"},
        "base_feature": base,
        "features": [left, right],
        "unresolved": [],
        "self_checks": {"cad_ready": True},
    }


def _flange_plan() -> dict:
    return {
        "schema_version": "2.0",
        "units": "mm",
        "part_family": "flange",
        "coordinate_system": {"main_axis": "Z", "origin": "flange center"},
        "base_feature": {
            "id": "base_flange",
            "type": "cylinder_base",
            "operation": "base",
            "dimensions": {
                "outer_diameter": {"value": 80, "unit": "mm", "evidence": "OD 80"},
                "length": {"value": 10, "unit": "mm", "evidence": "thickness 10"},
            },
            "placement": {"reference": "center", "axis": "Z"},
            "evidence": "flange outline",
        },
        "features": [
            {
                "id": "center_bore",
                "type": "through_hole",
                "operation": "remove",
                "dimensions": {"diameter": {"value": 24, "unit": "mm", "evidence": "center bore"}},
                "placement": {"reference": "center", "x": 0, "y": 0, "z": 0, "axis": "Z"},
                "extent": "through",
                "depends_on": ["base_flange"],
                "evidence": "center bore",
            },
            {
                "id": "bolt_circle",
                "type": "circular_pattern",
                "operation": "pattern",
                "dimensions": {
                    "count": {"value": 6, "unit": "mm", "evidence": "6x"},
                    "pitch_radius": {"value": 28, "unit": "mm", "evidence": "PCD 56"},
                    "diameter": {"value": 6, "unit": "mm", "evidence": "bolt hole diameter"},
                },
                "placement": {"reference": "flange center", "x": 0, "y": 0, "z": 0, "axis": "Z"},
                "extent": "through",
                "depends_on": ["base_flange"],
                "evidence": "bolt circle",
            },
        ],
        "unresolved": [],
        "self_checks": {"cad_ready": True},
    }


if __name__ == "__main__":
    unittest.main()
