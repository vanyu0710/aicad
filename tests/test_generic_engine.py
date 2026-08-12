from __future__ import annotations

import unittest

from backend.generic_engine import (
    FAMILY_TEMPLATES,
    build_evidence_set,
    build_execution_report,
    build_template_plan,
    detect_family,
    detect_input_kind,
    extract_clues,
    feature_semantics,
)
from backend.schemas import FeaturePlanV3, FeatureV3, PlacementV3


class InputRoutingTests(unittest.TestCase):
    def test_detect_input_kind(self):
        self.assertEqual(detect_input_kind(False, "plate"), "text_only")
        self.assertEqual(detect_input_kind(True, ""), "image_only")
        self.assertEqual(detect_input_kind(True, "plate"), "mixed")

    def test_detect_family_chinese_and_english(self):
        self.assertEqual(detect_family("\u6cd5\u5170"), "flange")
        self.assertEqual(detect_family("tube"), "tube")
        self.assertEqual(detect_family("rocker link"), "link_plate")
        self.assertEqual(detect_family("phone stand"), "phone_stand")
        self.assertEqual(detect_family("spur gear"), "spur_gear")


class ClueExtractionTests(unittest.TestCase):
    def test_chinese_dimension_clues(self):
        clues = extract_clues("\u5916\u5f84 50mm \u5185\u5f84 38mm \u957f\u5ea6 300mm \u69fd\u5bbd 10mm \u87ba\u6813\u5706\u76f4\u5f84 80mm")
        self.assertEqual(clues["outer_diameter"], 50.0)
        self.assertEqual(clues["inner_diameter"], 38.0)
        self.assertEqual(clues["length"], 300.0)
        self.assertEqual(clues["groove_width"], 10.0)
        self.assertEqual(clues["pitch_circle_diameter"], 80.0)

    def test_english_dimension_clues(self):
        clues = extract_clues("flange outer diameter 100 thickness 12 center through hole 20 bolt circle 80 M6")
        self.assertEqual(clues["outer_diameter"], 100.0)
        self.assertEqual(clues["height"], 12.0)
        self.assertEqual(clues["hole_diameter"], 20.0)
        self.assertEqual(clues["pitch_circle_diameter"], 80.0)
        self.assertEqual(clues["metric_thread"], 6.0)

    def test_gear_and_groove_clues(self):
        clues = extract_clues("spur gear module 2 teeth 20 hub diameter 16 groove width 5 groove depth 2 distance from face 10")
        self.assertEqual(clues["module"], 2.0)
        self.assertEqual(clues["tooth_count"], 20.0)
        self.assertEqual(clues["hub_outer_diameter"], 16.0)
        self.assertEqual(clues["groove_depth"], 2.0)
        self.assertEqual(clues["groove_distance"], 10.0)


class EvidenceTests(unittest.TestCase):
    def test_text_evidence_is_user_confirmed(self):
        evidence = build_evidence_set("\u5916\u5f84 50 \u4e2d\u5fc3\u5b54 12", None, False)
        self.assertEqual(evidence.input_kind, "text_only")
        outer = evidence.get("outer_diameter")
        self.assertIsNotNone(outer)
        self.assertTrue(outer.confirmed_by_user)
        self.assertEqual(evidence.get("hole_diameter").value, 12.0)

    def test_image_evidence_source_is_drawing(self):
        vision = {"dimensions": [{"name": "outer_diameter", "value": 60, "unit": "mm"}]}
        evidence = build_evidence_set("", vision, True)
        self.assertEqual(evidence.input_kind, "image_only")
        self.assertEqual(evidence.get("outer_diameter").source, "drawing")

    def test_text_vision_conflict_is_recorded(self):
        vision = {"dimensions": [{"name": "outer_diameter", "value": 60, "unit": "mm"}]}
        evidence = build_evidence_set("outer diameter 50", vision, True)
        self.assertGreaterEqual(len(evidence.conflicts), 1)


class TemplatePlanTests(unittest.TestCase):
    def test_flange_template_uses_center_hole_diameter(self):
        plan = build_template_plan(
            "Flange outer diameter 100 thickness 12 with a 20mm center through hole",
            mode="strict",
        )
        self.assertEqual(plan.part_family, "flange")
        self.assertEqual(plan.base_feature.type, "cylinder_base")
        self.assertEqual(plan.base_feature.dimensions["outer_diameter"].value, 100.0)
        self.assertEqual(plan.base_feature.dimensions["length"].value, 12.0)
        center = next(feature for feature in plan.features if feature.id == "center_hole")
        self.assertEqual(center.dimensions["diameter"].value, 20.0)
        self.assertEqual(center.execution_status, "unresolved")

    def test_tube_template_strict_keeps_missing_groove_dimensions_unresolved(self):
        plan = build_template_plan(
            "tube outer diameter 50 inner diameter 38 length 300 top groove slot width 10",
            mode="strict",
        )
        self.assertEqual(plan.part_family, "tube")
        groove = next(feature for feature in plan.features if feature.type == "internal_annular_groove")
        self.assertEqual(groove.dimensions["axial_width"].value, 10.0)
        self.assertIsNone(groove.dimensions["groove_depth"].value)
        self.assertIsNone(groove.dimensions["z_start"].value)
        self.assertTrue(any(item.get("feature") == groove.id for item in plan.unresolved))

    def test_tube_template_smart_fills_defaults_with_assumptions(self):
        plan = build_template_plan(
            "tube outer diameter 50 inner diameter 38 length 300",
            mode="smart",
            smart_fill_policy="limited_fill",
        )
        groove = next(feature for feature in plan.features if feature.type == "internal_annular_groove")
        self.assertEqual(groove.dimensions["axial_width"].value, 4.0)
        self.assertEqual(groove.dimensions["groove_depth"].value, 2.0)
        self.assertEqual(groove.dimensions["z_start"].value, 20.0)
        self.assertTrue(all(dim.source == "assumption" and not dim.confirmed_by_user for dim in groove.dimensions.values()))
        self.assertTrue(plan.design_review.requires_confirmation)
        self.assertGreaterEqual(len(plan.assumption_details), 3)

    def test_link_plate_template(self):
        plan = build_template_plan(
            "link_plate length 180 width 40 height 10 end 1 diameter 12 end 2 diameter 10",
            mode="strict",
        )
        self.assertEqual(plan.part_family, "link_plate")
        self.assertEqual(plan.base_feature.type, "link_plate")
        holes = {feature.id: feature for feature in plan.features}
        self.assertEqual(holes["hole_end_1"].dimensions["diameter"].value, 12.0)
        self.assertEqual(holes["hole_end_2"].dimensions["diameter"].value, 10.0)

    def test_link_plate_end_holes_use_center_distance(self):
        plan = build_template_plan(
            "link_plate length 180 width 40 height 10 center distance 120 end 1 diameter 12 end 2 diameter 10",
            mode="strict",
        )
        holes = {feature.id: feature for feature in plan.features}
        self.assertEqual(holes["hole_end_1"].placement.x, -60.0)
        self.assertEqual(holes["hole_end_2"].placement.x, 60.0)

    def test_phone_stand_template(self):
        plan = build_template_plan(
            "phone stand length 120 width 80 height 8 support height 90 support thickness 6 hole diameter 5",
            mode="strict",
        )
        self.assertEqual(plan.part_family, "phone_stand")
        types = {feature.type for feature in plan.features}
        self.assertIn("rectangular_pad", types)
        self.assertIn("through_hole", types)
        self.assertIn("rib_box", types)

    def test_spur_gear_marks_teeth_unsupported(self):
        plan = build_template_plan("spur gear module 2 teeth 20 center bore 8", mode="strict")
        self.assertEqual(plan.part_family, "spur_gear")
        self.assertEqual(plan.base_feature.type, "cylinder_base")
        self.assertTrue(any("spur_gear_teeth" in reason for reason in [str(item.get("reason")) for item in plan.unresolved]))
        self.assertIn("spur_gear_teeth", plan.design_intent_details.unsupported_requirements)

    def test_registry_and_semantics_are_data_driven(self):
        self.assertIsNotNone(FAMILY_TEMPLATES.get("flange"))
        self.assertIsNotNone(FAMILY_TEMPLATES.get("tube"))
        semantics = {item.feature_type: item for item in feature_semantics()}
        self.assertEqual(semantics["internal_annular_groove"].required_dimensions, ["axial_width", "groove_depth", "z_start"])


class ExecutionReportTests(unittest.TestCase):
    def test_four_dimensional_report(self):
        plan = FeaturePlanV3(
            part_family="plate",
            base_feature=FeatureV3(id="base_plate", type="box_base", operation="base", dimensions={}, placement=PlacementV3(reference="origin")),
            features=[
                FeatureV3(id="hole_1", type="through_hole", operation="remove", dimensions={}),
                FeatureV3(id="hole_2", type="through_hole", operation="remove", dimensions={}),
            ],
        )
        plan.base_feature.execution_status = "modeled"
        plan.features[0].execution_status = "modeled"
        plan.features[1].execution_status = "skipped"
        plan.features[1].unresolved.append("missing diameter")
        report = build_execution_report(
            plan,
            {"engine": "build123d", "warnings": ["hole_2 skipped"]},
            execution_ok=False,
            fallback_used=True,
            mode="strict",
        )
        self.assertFalse(report.execution_ok)
        self.assertFalse(report.geometry_valid)
        self.assertFalse(report.plan_complete)
        self.assertFalse(report.production_ready)
        self.assertEqual(report.skipped_features, ["hole_2"])
        self.assertTrue(report.fallback_used)
        self.assertIn("hole_2 skipped", report.details)


if __name__ == "__main__":
    unittest.main()