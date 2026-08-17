from __future__ import annotations

from copy import deepcopy
import unittest

from backend.geometry.verification import verify_feature_plan
from backend.schemas import (
    BoundingBoxFact,
    CylinderFact,
    FeaturePlanV3,
    FeatureV3,
    GeometryMeasurementReport,
    PlacementV3,
    VolumeFact,
)


def _box_base() -> FeatureV3:
    return FeatureV3(
        id="base_box",
        type="box_base",
        operation="base",
        dimensions={"length": {"value": 100}, "width": {"value": 50}, "height": {"value": 10}},
        placement=PlacementV3(reference="origin", axis="Z"),
    )


def _hole(
    feature_id: str,
    *,
    x: float | None,
    y: float | None,
    diameter: float = 6.0,
    hole_type: str = "through_hole",
    depth: float | None = None,
) -> FeatureV3:
    dimensions = {"diameter": {"value": diameter}}
    if depth is not None:
        dimensions["depth"] = {"value": depth}
    return FeatureV3(
        id=feature_id,
        type=hole_type,
        operation="remove",
        dimensions=dimensions,
        placement=PlacementV3(reference="origin", axis="Z", x=x, y=y, z=0.0),
    )


def _measurement(cylinders: list[CylinderFact] | None = None, *, status: str = "MEASUREMENT_SUCCESS") -> GeometryMeasurementReport:
    return GeometryMeasurementReport(
        status=status,
        bounding_box=BoundingBoxFact(
            min_x=-50,
            min_y=-25,
            min_z=0,
            max_x=50,
            max_y=25,
            max_z=10,
            size_x=100,
            size_y=50,
            size_z=10,
        ),
        volume=VolumeFact(volume=50000),
        cylinders=cylinders or [],
    )


def _cylinder(index: int, *, diameter: float, x: float, y: float, z: float = 5.0, height: float = 10.0) -> CylinderFact:
    return CylinderFact(
        measurement_index=index,
        diameter=diameter,
        radius=diameter / 2.0,
        axis=[0.0, 0.0, 1.0],
        center=[x, y, z],
        height=height,
    )


def _props(result, feature_id: str) -> dict[str, str]:
    feature = next(item for item in result.features if item.feature_id == feature_id)
    return {item.property_name: item.status for item in feature.properties}


class HoleVerificationTests(unittest.TestCase):
    def test_unique_through_hole_with_xy_passes_all_properties(self) -> None:
        plan = FeaturePlanV3(base_feature=_box_base(), features=[_hole("hole_a", x=20.0, y=10.0)])
        report = verify_feature_plan(plan, _measurement([_cylinder(0, diameter=6.0, x=20.0, y=10.0)]))
        hole = next(item for item in report.features if item.feature_id == "hole_a")
        self.assertEqual(_props(report, "hole_a"), {
            "existence": "PASS",
            "diameter": "PASS",
            "position": "PASS",
            "axis": "PASS",
            "depth": "PASS",
            "through": "PASS",
        })
        self.assertEqual(hole.status, "PASS")

    def test_two_positioned_holes_verify_independently(self) -> None:
        plan = FeaturePlanV3(
            base_feature=_box_base(),
            features=[_hole("hole_a", x=20.0, y=10.0), _hole("hole_b", x=-20.0, y=10.0)],
        )
        measurement = _measurement([
            _cylinder(0, diameter=6.0, x=-20.0, y=10.0),
            _cylinder(1, diameter=6.0, x=20.0, y=10.0),
        ])
        report = verify_feature_plan(plan, measurement)
        self.assertEqual(_props(report, "hole_a")["existence"], "PASS")
        self.assertEqual(_props(report, "hole_b")["existence"], "PASS")
        self.assertEqual(_props(report, "hole_a")["position"], "PASS")
        self.assertEqual(_props(report, "hole_b")["position"], "PASS")

    def test_two_unpositioned_equal_holes_never_pass(self) -> None:
        plan = FeaturePlanV3(
            base_feature=_box_base(),
            features=[_hole("hole_a", x=None, y=None), _hole("hole_b", x=None, y=None)],
        )
        measurement = _measurement([
            _cylinder(0, diameter=6.0, x=-20.0, y=10.0),
            _cylinder(1, diameter=6.0, x=20.0, y=10.0),
        ])
        report = verify_feature_plan(plan, measurement)
        for feature_id in ("hole_a", "hole_b"):
            hole = next(item for item in report.features if item.feature_id == feature_id)
            self.assertEqual(hole.correspondence.status, "AMBIGUOUS")
            self.assertTrue(all(status != "PASS" for status in _props(report, feature_id).values()))
            self.assertEqual(hole.status, "UNKNOWN")
            self.assertEqual(hole.correspondence.candidate_indices, [0, 1])

    def test_unique_hole_without_xy_has_unknown_position(self) -> None:
        plan = FeaturePlanV3(base_feature=_box_base(), features=[_hole("hole_a", x=None, y=None)])
        report = verify_feature_plan(plan, _measurement([_cylinder(0, diameter=6.0, x=20.0, y=10.0)]))
        props = _props(report, "hole_a")
        self.assertEqual(props["existence"], "PASS")
        self.assertEqual(props["diameter"], "PASS")
        self.assertEqual(props["position"], "UNKNOWN")
        hole = next(item for item in report.features if item.feature_id == "hole_a")
        self.assertEqual(hole.status, "UNKNOWN")

    def test_wrong_diameter_does_not_bind_and_does_not_pass(self) -> None:
        plan = FeaturePlanV3(base_feature=_box_base(), features=[_hole("hole_a", x=20.0, y=10.0, diameter=8.0)])
        report = verify_feature_plan(plan, _measurement([_cylinder(0, diameter=6.0, x=20.0, y=10.0)]))
        props = _props(report, "hole_a")
        self.assertEqual(props["existence"], "FAIL")
        self.assertNotEqual(props["diameter"], "PASS")

    def test_wrong_position_on_unique_hole_fails_position(self) -> None:
        plan = FeaturePlanV3(base_feature=_box_base(), features=[_hole("hole_a", x=0.0, y=0.0)])
        report = verify_feature_plan(plan, _measurement([_cylinder(0, diameter=6.0, x=20.0, y=10.0)]))
        props = _props(report, "hole_a")
        self.assertEqual(props["existence"], "PASS")
        self.assertEqual(props["position"], "FAIL")
        hole = next(item for item in report.features if item.feature_id == "hole_a")
        self.assertEqual(hole.status, "FAIL")

    def test_unavailable_measurement_is_unknown_not_fail(self) -> None:
        plan = FeaturePlanV3(base_feature=_box_base(), features=[_hole("hole_a", x=20.0, y=10.0)])
        report = verify_feature_plan(plan, GeometryMeasurementReport(status="MEASUREMENT_UNAVAILABLE", errors=["no shape"]))
        self.assertEqual(_props(report, "hole_a")["existence"], "UNKNOWN")
        self.assertNotEqual(_props(report, "hole_a")["existence"], "FAIL")

    def test_blind_hole_skips_through_and_uses_specified_depth(self) -> None:
        plan = FeaturePlanV3(
            base_feature=_box_base(),
            features=[_hole("hole_a", x=20.0, y=10.0, hole_type="blind_hole", depth=4.0)],
        )
        report = verify_feature_plan(plan, _measurement([_cylinder(0, diameter=6.0, x=20.0, y=10.0, height=4.0)]))
        props = _props(report, "hole_a")
        self.assertEqual(props["depth"], "PASS")
        self.assertEqual(props["through"], "SKIPPED")
        hole = next(item for item in report.features if item.feature_id == "hole_a")
        self.assertEqual(hole.status, "PASS")

    def test_verification_does_not_mutate_inputs(self) -> None:
        plan = FeaturePlanV3(base_feature=_box_base(), features=[_hole("hole_a", x=20.0, y=10.0)])
        measurement = _measurement([_cylinder(0, diameter=6.0, x=20.0, y=10.0)])
        before_plan = deepcopy(plan)
        before_measurement = deepcopy(measurement)
        first = verify_feature_plan(plan, measurement)
        second = verify_feature_plan(plan, measurement)
        self.assertEqual(first, second)
        self.assertEqual(plan, before_plan)
        self.assertEqual(measurement, before_measurement)


if __name__ == "__main__":
    unittest.main()
