from __future__ import annotations

import unittest
from copy import deepcopy

from backend.normalization import normalize_feature_plan
from backend.schemas import FeaturePlanV3
from backend.validation import (
    apply_validation_result,
    compute_feature_order,
    order_feature_plan,
    validate_feature_plan,
)


def _base(part_family="plate", base_type="box_base", dimensions=None):
    dims = dimensions or {
        "length": {"value": 60, "unit": "mm", "confirmed_by_user": True},
        "width": {"value": 30, "unit": "mm", "confirmed_by_user": True},
        "height": {"value": 4, "unit": "mm", "confirmed_by_user": True},
    }
    return {
        "id": "base_plate",
        "type": base_type,
        "operation": "base",
        "dimensions": dims,
    }


def _feature(feature_id, feature_type, operation="remove", dimensions=None, placement=None, depends_on=None):
    return {
        "id": feature_id,
        "type": feature_type,
        "operation": operation,
        "dimensions": dimensions or {},
        "placement": placement or {"reference": "origin", "axis": "Z"},
        "depends_on": depends_on or ["base_plate"],
    }


class ValidationTests(unittest.TestCase):
    def test_order_feature_plan_puts_base_and_remove_before_add(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [
                    _feature("boss1", "boss_cylinder", "add", {"diameter": {"value": 10}, "height": {"value": 5}}),
                    _feature("hole1", "through_hole", "remove", {"diameter": {"value": 6}}, placement={"reference": "center", "x": 0, "y": 0, "axis": "Z"}),
                ],
            }
        )
        ordered, cycles = order_feature_plan(plan)
        self.assertEqual(ordered, ["base_plate", "hole1", "boss1"])
        self.assertEqual(cycles, [])

    def test_strict_blocks_missing_child_dimensions_and_smart_does_not(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [_feature("hole1", "through_hole", "remove", {})],
            }
        )
        strict = validate_feature_plan(plan, mode="strict")
        self.assertTrue(any("缺少" in item or "missing" in item.lower() for item in strict["blocking"]))
        smart = validate_feature_plan(plan, mode="smart")
        self.assertEqual(smart["blocking"], [])
        self.assertTrue(any("缺少" in item or "missing" in item.lower() for item in smart["warnings"]))

    def test_inner_diameter_geometry_blocks(self):
        plan = FeaturePlanV3.model_validate(
            {
                "part_family": "tube",
                "base_feature": _base(
                    "tube",
                    "hollow_cylinder",
                    {
                        "outer_diameter": {"value": 20, "unit": "mm", "confirmed_by_user": True},
                        "inner_diameter": {"value": 24, "unit": "mm", "confirmed_by_user": True},
                        "length": {"value": 60, "unit": "mm", "confirmed_by_user": True},
                    },
                ),
                "features": [],
            }
        )
        result = validate_feature_plan(plan)
        self.assertTrue(any("内径必须小于外径" in item or "inner" in item.lower() for item in result["blocking"]))

    def test_hole_larger_than_base_blocks(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [
                    _feature("hole1", "through_hole", "remove", {"diameter": {"value": 70}}, placement={"reference": "center", "x": 0, "y": 0, "axis": "Z"})
                ],
            }
        )
        result = validate_feature_plan(plan)
        self.assertTrue(any("孔径" in item or "hole" in item.lower() for item in result["blocking"]))

    def test_hole_outside_plate_blocks(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [
                    _feature("hole1", "through_hole", "remove", {"diameter": {"value": 6}}, placement={"reference": "origin", "x": 100, "y": 0, "axis": "Z"})
                ],
            }
        )
        result = validate_feature_plan(plan)
        self.assertTrue(any("超出" in item or "outside" in item.lower() for item in result["blocking"]))

    def test_groove_outside_base_blocks(self):
        plan = FeaturePlanV3.model_validate(
            {
                "part_family": "tube",
                "base_feature": _base(
                    "tube",
                    "hollow_cylinder",
                    {
                        "outer_diameter": {"value": 20, "unit": "mm", "confirmed_by_user": True},
                        "inner_diameter": {"value": 16, "unit": "mm", "confirmed_by_user": True},
                        "length": {"value": 60, "unit": "mm", "confirmed_by_user": True},
                    },
                ),
                "features": [
                    _feature(
                        "top_groove",
                        "annular_groove",
                        "remove",
                        {
                            "reduced_outer_diameter": {"value": 18, "unit": "mm", "confirmed_by_user": True},
                            "axial_width": {"value": 20, "unit": "mm", "confirmed_by_user": True},
                            "z_start": {"value": 50, "unit": "mm", "confirmed_by_user": True},
                        },
                        placement={"reference": "main_axis", "axis": "Z"},
                    )
                ],
            }
        )
        result = validate_feature_plan(plan)
        self.assertTrue(any("超出" in item or "outside" in item.lower() for item in result["blocking"]))

    def test_missing_dependency_and_cycle_block(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [_feature("hole1", "through_hole", "remove", {"diameter": {"value": 6}}, depends_on=["ghost"])],
            }
        )
        result = validate_feature_plan(plan)
        self.assertTrue(any("依赖" in item or "depend" in item.lower() for item in result["blocking"]))

        cycle = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [
                    _feature("a", "through_hole", "remove", {"diameter": {"value": 6}}, depends_on=["b"]),
                    _feature("b", "through_hole", "remove", {"diameter": {"value": 6}}, depends_on=["a"]),
                ],
            }
        )
        result = validate_feature_plan(cycle)
        self.assertTrue(any("依赖环" in item or "cycle" in item.lower() for item in result["blocking"]))

    def test_unconfirmed_assumption_blocks_strict_but_warns_smart(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [
                    _feature(
                        "hole1",
                        "through_hole",
                        "remove",
                        {"diameter": {"value": 6, "unit": "mm", "source": "assumption", "confirmed_by_user": False}},
                        placement={"reference": "center", "x": 0, "y": 0, "axis": "Z"},
                    )
                ],
            }
        )
        strict = validate_feature_plan(plan, mode="strict")
        self.assertTrue(any("假设" in item or "assum" in item.lower() for item in strict["blocking"]))
        smart = validate_feature_plan(plan, mode="smart")
        self.assertEqual(smart["blocking"], [])
        self.assertTrue(any("假设" in item or "assum" in item.lower() for item in smart["warnings"]))

    def test_missing_placement_blocks_strict_and_warns_smart(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [
                    _feature("hole1", "through_hole", "remove", {"diameter": {"value": 6}}, placement={"reference": "needs_position", "axis": "Z"})
                ],
            }
        )
        strict = validate_feature_plan(plan, mode="strict", language="en")
        self.assertTrue(any("placement" in item.lower() for item in strict["blocking"]))
        smart = validate_feature_plan(plan, mode="smart", language="en")
        self.assertEqual(smart["blocking"], [])
        self.assertTrue(any("placement" in item.lower() for item in smart["warnings"]))

        unnamed = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [
                    _feature("slot1", "rectangular_slot", "remove", {"length": {"value": 10}, "width": {"value": 4}, "depth": {"value": 2}}, placement={"reference": "top_face", "axis": "Z"})
                ],
            }
        )
        result = validate_feature_plan(unnamed, mode="strict", language="en")
        self.assertTrue(any("placement" in item.lower() for item in result["blocking"]))

    def test_self_checks_are_written_into_plan(self):
        plan = FeaturePlanV3.model_validate({"base_feature": _base(), "features": []})
        result = validate_feature_plan(plan)
        self.assertNotIn("checks", plan.self_checks)
        apply_validation_result(plan, result)
        self.assertIn("checks", plan.self_checks)
        self.assertIn("order", plan.self_checks)
        self.assertEqual(plan.self_checks["summary"]["block"], 0)

    def test_validate_feature_plan_is_pure_and_repeatable(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [
                    _feature("boss1", "boss_cylinder", "add", {"diameter": {"value": 10}, "height": {"value": 5}}),
                    _feature("hole1", "through_hole", "remove", {"diameter": {"value": 6}}, placement={"reference": "center", "x": 0, "y": 0, "axis": "Z"}),
                ],
            }
        )
        before = plan.model_dump()
        first = validate_feature_plan(plan, mode="strict")
        second = validate_feature_plan(plan, mode="strict")
        self.assertEqual(plan.model_dump(), before)
        self.assertEqual(first, second)

    def test_validate_feature_plan_deepcopy_mutation_regression(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [
                    _feature(
                        "hole1",
                        "through_hole",
                        "remove",
                        {"diameter": {"value": 6, "source": "assumption", "confirmed_by_user": False}},
                        placement={"reference": "center", "x": 0, "y": 0, "axis": "Z"},
                    )
                ],
            }
        )
        normalize_feature_plan(plan)
        apply_validation_result(plan, validate_feature_plan(plan, mode="smart"), mode="smart")
        plan_before = deepcopy(plan)
        result = validate_feature_plan(plan, mode="smart")
        self.assertEqual(plan, plan_before)
        self.assertEqual(plan.model_dump(), plan_before.model_dump())
        self.assertIsInstance(result, dict)

    def test_double_validation_is_stable_and_plan_unchanged(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [
                    _feature(
                        "hole1",
                        "through_hole",
                        "remove",
                        {"diameter": {"value": 6, "source": "assumption", "confirmed_by_user": False}},
                        placement={"reference": "center", "x": 0, "y": 0, "axis": "Z"},
                    )
                ],
            }
        )
        normalize_feature_plan(plan)
        apply_validation_result(plan, validate_feature_plan(plan, mode="smart"), mode="smart")
        plan_before = deepcopy(plan)
        result1 = validate_feature_plan(plan, mode="smart")
        result2 = validate_feature_plan(plan, mode="smart")
        self.assertEqual(result1, result2)
        self.assertEqual(plan, plan_before)
        self.assertEqual(plan.model_dump(), plan_before.model_dump())


    def test_compute_feature_order_does_not_mutate_plan(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [
                    _feature("boss1", "boss_cylinder", "add", {"diameter": {"value": 10}, "height": {"value": 5}}),
                    _feature("hole1", "through_hole", "remove", {"diameter": {"value": 6}}, placement={"reference": "center", "x": 0, "y": 0, "axis": "Z"}),
                ],
            }
        )
        before = [feature.id for feature in plan.features]
        ordered, cycles = compute_feature_order(plan)
        self.assertEqual([feature.id for feature in plan.features], before)
        self.assertEqual(ordered, ["base_plate", "hole1", "boss1"])
        self.assertEqual(cycles, [])

    def test_normalize_feature_plan_is_idempotent(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [
                    _feature(
                        "hole1",
                        "through_hole",
                        "remove",
                        {"diameter": {"value": 6, "unit": "mm", "source": "assumption", "confirmed_by_user": False}},
                        placement={"reference": "center", "x": 0, "y": 0, "axis": "Z"},
                        depends_on=["ghost"],
                    )
                ],
            }
        )
        normalize_feature_plan(plan)
        first = plan.model_dump()
        normalize_feature_plan(plan)
        self.assertEqual(plan.model_dump(), first)
        self.assertTrue(any(item["feature"] == "hole1" and "Missing dependency" in item["reason"] for item in plan.unresolved))
        self.assertTrue(any(item.feature_id == "hole1" and item.dimension == "diameter" for item in plan.assumption_details))

    def test_apply_validation_result_writes_self_checks_and_review(self):
        plan = FeaturePlanV3.model_validate(
            {
                "base_feature": _base(),
                "features": [_feature("hole1", "through_hole", "remove", {})],
            }
        )
        result = validate_feature_plan(plan, mode="strict")
        apply_validation_result(plan, result, mode="strict")
        self.assertEqual(plan.self_checks["mode"], "strict")
        self.assertTrue(any(item.startswith("[validation]") for item in plan.design_review.blocking))
        self.assertTrue(all(item.startswith("[validation]") for item in plan.design_review.blocking))


if __name__ == "__main__":
    unittest.main()
