from __future__ import annotations

from copy import deepcopy
import unittest

from build123d import Box, BuildPart, Cylinder, Locations, Mode

from backend.feature_definitions import FEATURE_DEFINITIONS
from backend.geometry.measurement import measure_shape
from backend.geometry.verification import (
    BoxBaseVerifier,
    CylinderBaseVerifier,
    DEFAULT_VERIFICATION_REGISTRY,
    verify_feature_plan,
)
from backend.geometry.verification_registry import GeometryVerificationRegistry
from backend.schemas import (
    BoundingBoxExpectation,
    BoundingBoxFact,
    CylinderFact,
    FeaturePlanV3,
    FeatureV3,
    GeometryMeasurementReport,
    GeometryVerificationCapability,
    PlacementV3,
    VerificationContext,
    VerificationTolerancePolicy,
    VolumeFact,
)


def _box_plan(*, children: list[FeatureV3] | None = None) -> FeaturePlanV3:
    return FeaturePlanV3(
        base_feature=FeatureV3(
            id="base_box",
            type="box_base",
            operation="base",
            dimensions={
                "length": {"value": 100},
                "width": {"value": 50},
                "height": {"value": 10},
            },
            placement=PlacementV3(reference="origin", axis="Z"),
        ),
        features=children or [],
    )


def _cylinder_plan(*, diameter: float = 10.0, axis: str = "Z", hollow: bool = False) -> FeaturePlanV3:
    dimensions = {"outer_diameter": {"value": diameter}, "length": {"value": 20.0}}
    if hollow:
        dimensions["inner_diameter"] = {"value": 6.0}
    return FeaturePlanV3(
        base_feature=FeatureV3(
            id="base_cylinder",
            type="hollow_cylinder" if hollow else "cylinder_base",
            operation="base",
            dimensions=dimensions,
            placement=PlacementV3(reference="origin", axis=axis),
        )
    )


def _cylinder_measurement(cylinders: list[CylinderFact]) -> GeometryMeasurementReport:
    return GeometryMeasurementReport(
        bounding_box=BoundingBoxFact(
            min_x=-5,
            min_y=-5,
            min_z=-10,
            max_x=5,
            max_y=5,
            max_z=10,
            size_x=10,
            size_y=10,
            size_z=20,
        ),
        volume=VolumeFact(volume=500 * 3.141592653589793),
        cylinders=cylinders,
    )


class VerificationRegistryTests(unittest.TestCase):
    def test_registry_covers_every_canonical_feature(self) -> None:
        registered = {item.feature_type for item in DEFAULT_VERIFICATION_REGISTRY.capabilities()}
        expected = {item.feature_type for item in FEATURE_DEFINITIONS.list()}
        self.assertEqual(registered, expected)
        self.assertEqual(DEFAULT_VERIFICATION_REGISTRY.get_capability("through_hole").implementation_status, "partial")
        self.assertEqual(DEFAULT_VERIFICATION_REGISTRY.get_capability("box_base").implementation_status, "supported")

    def test_duplicate_registry_entry_is_rejected(self) -> None:
        registry = GeometryVerificationRegistry()
        capability = GeometryVerificationCapability(feature_type="box_base")
        registry.register(capability, BoxBaseVerifier())
        with self.assertRaises(ValueError):
            registry.register(capability, BoxBaseVerifier())


class GlobalVerificationTests(unittest.TestCase):
    def test_bounding_box_and_volume_exact_match(self) -> None:
        plan = _box_plan()
        report = verify_feature_plan(
            plan,
            measure_shape(Box(100, 50, 10)),
            VerificationContext(
                expected_bounding_box=BoundingBoxExpectation(size_x=100, size_y=50, size_z=10),
                expected_volume=50000,
            ),
        )
        self.assertEqual(report.status, "VERIFIED")
        self.assertTrue(all(item.status == "PASS" for item in report.global_properties))

    def test_global_tolerance_boundary_and_mismatch(self) -> None:
        measurement = measure_shape(Box(100.04, 50, 10))
        pass_report = verify_feature_plan(
            _box_plan(),
            measurement,
            VerificationContext(expected_bounding_box=BoundingBoxExpectation(size_x=100)),
        )
        self.assertEqual(pass_report.global_properties[0].status, "PASS")
        fail_report = verify_feature_plan(
            _box_plan(),
            measurement,
            VerificationContext(expected_bounding_box=BoundingBoxExpectation(size_x=99.9)),
        )
        self.assertEqual(fail_report.global_properties[0].status, "FAIL")

    def test_global_measurement_unavailable_is_unknown(self) -> None:
        measurement = GeometryMeasurementReport(
            status="MEASUREMENT_UNAVAILABLE",
            bounding_box=BoundingBoxFact(status="MEASUREMENT_UNAVAILABLE"),
            volume=VolumeFact(status="MEASUREMENT_UNAVAILABLE"),
        )
        report = verify_feature_plan(
            _box_plan(),
            measurement,
            VerificationContext(expected_bounding_box=BoundingBoxExpectation(size_x=100), expected_volume=50000),
        )
        self.assertEqual([item.status for item in report.global_properties], ["UNKNOWN", "UNKNOWN"])


class CylindricalVerificationTests(unittest.TestCase):
    def test_unique_cylinder_diameter_and_axis_pass(self) -> None:
        report = verify_feature_plan(_cylinder_plan(), measure_shape(Cylinder(5, 20)))
        feature = report.features[0]
        properties = {item.property_name: item for item in feature.properties}
        self.assertEqual(report.status, "VERIFIED")
        self.assertEqual(feature.correspondence.status, "UNIQUE")
        self.assertEqual(properties["diameter"].status, "PASS")
        self.assertEqual(properties["axis"].status, "PASS")

    def test_missing_cylinder_is_fail_not_fake_success(self) -> None:
        report = verify_feature_plan(_cylinder_plan(), measure_shape(Box(10, 10, 20)))
        properties = {item.property_name: item for item in report.features[0].properties}
        self.assertEqual(properties["cylindrical_geometry"].status, "FAIL")
        self.assertEqual(properties["diameter"].status, "FAIL")
        self.assertEqual(properties["axis"].status, "SKIPPED")

    def test_duplicate_cylinders_are_ambiguous(self) -> None:
        with BuildPart() as part:
            Cylinder(5, 20)
            with Locations((30, 0, 0)):
                Cylinder(5, 20, mode=Mode.ADD)
        report = verify_feature_plan(_cylinder_plan(), measure_shape(part.part))
        properties = {item.property_name: item for item in report.features[0].properties}
        self.assertEqual(report.features[0].correspondence.status, "AMBIGUOUS")
        self.assertEqual(properties["diameter"].status, "UNKNOWN")
        self.assertEqual(properties["axis"].status, "SKIPPED")

    def test_axis_opposite_direction_is_allowed_only_for_axial_semantics(self) -> None:
        measurement = _cylinder_measurement(
            [CylinderFact(measurement_index=9, radius=5, diameter=10, axis=[0, 0, -1], center=[0, 0, 0], height=20)]
        )
        axial = verify_feature_plan(_cylinder_plan(), measurement)
        self.assertEqual(next(item for item in axial.features[0].properties if item.property_name == "axis").status, "PASS")
        oriented = verify_feature_plan(
            _cylinder_plan(),
            measurement,
            VerificationContext(tolerance_policy=VerificationTolerancePolicy(axial_axis_equivalence=False)),
        )
        self.assertEqual(next(item for item in oriented.features[0].properties if item.property_name == "axis").status, "FAIL")

    def test_reordered_measurement_candidates_do_not_change_result(self) -> None:
        first = _cylinder_measurement(
            [
                CylinderFact(measurement_index=3, radius=5, diameter=10, axis=[0, 0, 1]),
                CylinderFact(measurement_index=1, radius=5, diameter=10, axis=[0, 0, 1]),
            ]
        )
        second = first.model_copy(update={"cylinders": list(reversed(first.cylinders))})
        self.assertEqual(verify_feature_plan(_cylinder_plan(), first), verify_feature_plan(_cylinder_plan(), second))

    def test_unavailable_cylinder_measurement_is_unknown_not_fail(self) -> None:
        measurement = GeometryMeasurementReport(
            status="MEASUREMENT_UNAVAILABLE",
            bounding_box=BoundingBoxFact(status="MEASUREMENT_UNAVAILABLE"),
            volume=VolumeFact(status="MEASUREMENT_UNAVAILABLE"),
        )
        report = verify_feature_plan(_cylinder_plan(), measurement)
        properties = {item.property_name: item for item in report.features[0].properties}
        self.assertEqual(report.features[0].correspondence.status, "UNAVAILABLE")
        self.assertEqual(properties["cylindrical_geometry"].status, "UNKNOWN")
        self.assertEqual(properties["diameter"].status, "UNKNOWN")

    def test_invalid_expected_dimension_fails_safely_as_unknown(self) -> None:
        report = verify_feature_plan(_cylinder_plan(diameter=-10), measure_shape(Cylinder(5, 20)))
        self.assertEqual(report.features[0].status, "UNKNOWN")


class MultiFeatureAndPurityTests(unittest.TestCase):
    def test_verified_base_and_unsupported_child_are_partially_verified(self) -> None:
        hole = FeatureV3(
            id="fillet_01",
            type="fillet",
            operation="modify",
        )
        plan = _box_plan(children=[hole])
        report = verify_feature_plan(
            plan,
            measure_shape(Box(100, 50, 10)),
            VerificationContext(expected_volume=50000),
        )
        self.assertEqual(report.features[0].status, "UNKNOWN")
        self.assertEqual(report.features[1].status, "UNSUPPORTED")
        self.assertEqual(report.status, "PARTIALLY_VERIFIED")

    def test_supported_and_unsupported_results_are_independent(self) -> None:
        box_plan = _box_plan()
        report = verify_feature_plan(box_plan, measure_shape(Box(100, 50, 10)))
        self.assertEqual(report.features[0].status, "PASS")
        unsupported = FeaturePlanV3(
            base_feature=FeatureV3(id="gear", type="spur_gear", operation="modify"),
        )
        unsupported_report = verify_feature_plan(unsupported, measure_shape(Box(1, 1, 1)))
        self.assertEqual(unsupported_report.status, "UNSUPPORTED")

    def test_a_verified_property_and_a_failed_property_aggregate_to_failed(self) -> None:
        report = verify_feature_plan(
            _box_plan(),
            measure_shape(Box(100, 50, 10)),
            VerificationContext(expected_bounding_box=BoundingBoxExpectation(size_x=99.0), expected_volume=50000),
        )
        self.assertEqual(report.status, "FAILED")

    def test_verification_is_pure_and_repeatable(self) -> None:
        plan = _cylinder_plan()
        measurement = measure_shape(Cylinder(5, 20))
        before_plan = deepcopy(plan)
        before_measurement = deepcopy(measurement)
        first = verify_feature_plan(plan, measurement)
        second = verify_feature_plan(plan, measurement)
        self.assertEqual(first, second)
        self.assertEqual(plan, before_plan)
        self.assertEqual(measurement, before_measurement)


if __name__ == "__main__":
    unittest.main()
