from __future__ import annotations

import unittest

from backend.ai import apply_feature_operations, patch_feature
from backend.capabilities import (
    CAPABILITIES,
    CapabilityValidationError,
    validate_feature_edit_set,
    validate_feature_operation,
    validate_feature_patch,
)
from backend.schemas import (
    DesignReview,
    DimensionV3,
    FeatureEditOperation,
    FeatureEditSet,
    FeaturePlanV3,
    FeatureV3,
    PlacementV3,
)


def _dim(value: float) -> DimensionV3:
    return DimensionV3(
        value=value,
        unit="mm",
        evidence="test",
        source="user",
        confirmed_by_user=True,
    )


def _tube_plan() -> FeaturePlanV3:
    return FeaturePlanV3(
        part_family="tube",
        base_feature=FeatureV3(
            id="base_tube",
            type="hollow_cylinder",
            operation="base",
            dimensions={
                "outer_diameter": _dim(20),
                "inner_diameter": _dim(16),
                "length": _dim(60),
            },
            placement=PlacementV3(reference="center", axis="Z"),
        ),
        features=[
            FeatureV3(
                id="top_groove",
                type="annular_groove",
                operation="remove",
                dimensions={
                    "reduced_outer_diameter": _dim(14),
                    "axial_width": _dim(6),
                    "z_start": _dim(50),
                },
                placement=PlacementV3(reference="bottom_end_center", axis="Z"),
                depends_on=["base_tube"],
            ),
            FeatureV3(
                id="center_hole",
                type="through_hole",
                operation="remove",
                dimensions={"diameter": _dim(6)},
                placement=PlacementV3(reference="model_center", x=0, y=0, z=30, axis="Z"),
                depends_on=["base_tube"],
            ),
        ],
        design_review=DesignReview(),
    )


class CapabilityRegistryTests(unittest.TestCase):
    def test_known_and_unknown_lookup(self) -> None:
        capability = CAPABILITIES.get("box_base")
        self.assertIsNotNone(capability)
        self.assertEqual(capability.feature_type, "box_base")
        self.assertIsNone(CAPABILITIES.get("fillet"))
        self.assertIsNone(CAPABILITIES.get("thread"))

    def test_duplicate_registration_raises(self) -> None:
        with self.assertRaises(ValueError):
            CAPABILITIES.register(CAPABILITIES.get("through_hole"))

    def test_allowed_operation_passes(self) -> None:
        plan = _tube_plan()
        operation = FeatureEditOperation(
            op="update",
            feature_id="center_hole",
            dimensions={"diameter": _dim(7)},
        )
        self.assertEqual(validate_feature_operation(operation, plan), [])

    def test_disallowed_operation_returns_error_code(self) -> None:
        plan = _tube_plan()
        operation = FeatureEditOperation(
            op="add",
            type="box_base",
            dimensions={"length": _dim(10), "width": _dim(10), "height": _dim(10)},
        )
        issues = validate_feature_operation(operation, plan)
        self.assertTrue(any(item.error_code == "CAPABILITY_OPERATION_NOT_ALLOWED" for item in issues))

    def test_unknown_parameter_rejected(self) -> None:
        plan = _tube_plan()
        operation = FeatureEditOperation(
            op="update",
            feature_id="center_hole",
            dimensions={"radius": _dim(5)},
        )
        issues = validate_feature_operation(operation, plan)
        self.assertTrue(any(item.error_code == "CAPABILITY_PARAMETER_UNKNOWN" for item in issues))

    def test_non_editable_parameter_rejected(self) -> None:
        plan = _tube_plan()
        operation = FeatureEditOperation(
            op="update",
            feature_id="center_hole",
            dimensions={"id": DimensionV3(value=1, unit="mm", source="user", confirmed_by_user=True)},
        )
        issues = validate_feature_operation(operation, plan)
        self.assertTrue(any(item.error_code == "CAPABILITY_PARAMETER_NOT_EDITABLE" for item in issues))

    def test_negative_value_rejected(self) -> None:
        plan = _tube_plan()
        operation = FeatureEditOperation(
            op="update",
            feature_id="center_hole",
            dimensions={"diameter": _dim(-1)},
        )
        issues = validate_feature_operation(operation, plan)
        self.assertTrue(any(item.error_code == "CAPABILITY_PARAMETER_INVALID_VALUE" for item in issues))

    def test_wrong_type_rejected(self) -> None:
        issues = CAPABILITIES.validate_parameter("through_hole", "diameter", "abc")
        self.assertTrue(any(item.error_code == "CAPABILITY_PARAMETER_INVALID_VALUE" for item in issues))

    def test_validation_is_stateless(self) -> None:
        plan = _tube_plan()
        before = plan.model_dump()
        registry_before = CAPABILITIES.get("through_hole")
        edit_set = FeatureEditSet(
            operations=[
                FeatureEditOperation(op="update", feature_id="center_hole", dimensions={"diameter": _dim(8)}),
                FeatureEditOperation(op="add", type="box_base"),
            ]
        )
        first = validate_feature_edit_set(edit_set, plan)
        second = validate_feature_edit_set(edit_set, plan)
        self.assertEqual(plan.model_dump(), before)
        self.assertIs(CAPABILITIES.get("through_hole"), registry_before)
        self.assertEqual([item.reason for item in first], [item.reason for item in second])


class CapabilityEditIntegrationTests(unittest.TestCase):
    def test_center_hole_diameter_update(self) -> None:
        plan = _tube_plan()
        edit_set = FeatureEditSet(
            operations=[
                FeatureEditOperation(
                    op="update",
                    feature_id="center_hole",
                    dimensions={"diameter": _dim(12)},
                )
            ]
        )
        updated, _, steps = apply_feature_operations(plan, edit_set, None, "zh")
        self.assertEqual(steps[0].status, "completed")
        self.assertEqual(updated.features[1].dimensions["diameter"].value, 12.0)

    def test_hole_movement_update(self) -> None:
        plan = _tube_plan()
        edit_set = FeatureEditSet(
            operations=[
                FeatureEditOperation(
                    op="update",
                    feature_id="center_hole",
                    placement=PlacementV3(reference="model_center", x=5, y=0, z=30, axis="Z"),
                )
            ]
        )
        updated, _, steps = apply_feature_operations(plan, edit_set, None, "zh")
        self.assertEqual(steps[0].status, "completed")
        self.assertEqual(updated.features[1].placement.x, 5.0)

    def test_top_groove_delete(self) -> None:
        plan = _tube_plan()
        edit_set = FeatureEditSet(operations=[FeatureEditOperation(op="delete", feature_id="top_groove")])
        updated, _, steps = apply_feature_operations(plan, edit_set, None, "zh")
        self.assertEqual(steps[0].status, "completed")
        self.assertNotIn("top_groove", [feature.id for feature in updated.features])

    def test_m6_pattern_add(self) -> None:
        plan = _tube_plan()
        edit_set = FeatureEditSet(
            operations=[
                FeatureEditOperation(
                    op="add",
                    type="circular_pattern",
                    dimensions={"count": _dim(4), "diameter": _dim(6)},
                )
            ]
        )
        updated, _, steps = apply_feature_operations(plan, edit_set, None, "zh")
        self.assertEqual(steps[0].status, "completed")
        self.assertTrue(any(feature.type == "circular_pattern" for feature in updated.features))

    def test_change_type_to_blind_hole_accepts_target_depth(self) -> None:
        plan = _tube_plan()
        edit_set = FeatureEditSet(
            operations=[
                FeatureEditOperation(
                    op="change_type",
                    feature_id="center_hole",
                    type="blind_hole",
                    dimensions={"diameter": _dim(6), "depth": _dim(8)},
                )
            ]
        )
        updated, _, steps = apply_feature_operations(plan, edit_set, None, "zh")
        self.assertEqual(steps[0].status, "completed")
        hole = next(feature for feature in updated.features if feature.id == "center_hole")
        self.assertEqual(hole.type, "blind_hole")
        self.assertEqual(hole.dimensions["depth"].value, 8.0)

    def test_invalid_operation_blocked_without_polluting_plan(self) -> None:
        plan = _tube_plan()
        edit_set = FeatureEditSet(
            operations=[
                FeatureEditOperation(
                    op="add",
                    type="box_base",
                    dimensions={"length": _dim(10), "width": _dim(10), "height": _dim(10)},
                ),
                FeatureEditOperation(
                    op="update",
                    feature_id="center_hole",
                    dimensions={"diameter": _dim(7)},
                ),
            ]
        )
        updated, _, steps = apply_feature_operations(plan, edit_set, None, "zh")
        self.assertEqual(steps[0].status, "blocked")
        self.assertEqual(steps[1].status, "completed")
        self.assertFalse(any(feature.type == "box_base" for feature in updated.features))
        self.assertEqual(updated.features[1].dimensions["diameter"].value, 7.0)

    def test_property_patch_valid_and_invalid(self) -> None:
        plan = _tube_plan()
        updated = patch_feature(plan, "center_hole", {"dimensions": {"diameter": _dim(8)}})
        self.assertEqual(updated.features[1].dimensions["diameter"].value, 8.0)

        original = plan.model_dump()
        with self.assertRaises(CapabilityValidationError) as ctx:
            patch_feature(plan, "center_hole", {"dimensions": {"radius": _dim(8)}})
        self.assertTrue(any(item.error_code == "CAPABILITY_PARAMETER_UNKNOWN" for item in ctx.exception.issues))
        self.assertEqual(plan.model_dump(), original)

    def test_validate_feature_patch_unknown_type(self) -> None:
        issues = validate_feature_patch("fillet", {"radius": _dim(1)})
        self.assertTrue(any(item.error_code == "CAPABILITY_UNKNOWN_FEATURE" for item in issues))


if __name__ == "__main__":
    unittest.main()
