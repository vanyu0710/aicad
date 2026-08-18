from __future__ import annotations

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


def _box() -> FeatureV3:
    return FeatureV3(
        id="base_box",
        type="box_base",
        operation="base",
        dimensions={"length": {"value": 100}, "width": {"value": 50}, "height": {"value": 10}},
        placement=PlacementV3(axis="Z"),
    )


def _hole(fid: str, x=None, y=None, diameter=6.0, kind="through_hole", depth=None, axis="Z") -> FeatureV3:
    dimensions = {"diameter": {"value": diameter}}
    if depth is not None:
        dimensions["depth"] = {"value": depth}
    return FeatureV3(
        id=fid,
        type=kind,
        operation="remove",
        dimensions=dimensions,
        placement=PlacementV3(reference="origin", axis=axis, x=x, y=y, z=0.0),
    )


def _meas(cylinders: list[CylinderFact], *, status: str = "MEASUREMENT_SUCCESS") -> GeometryMeasurementReport:
    return GeometryMeasurementReport(
        status=status,
        bounding_box=BoundingBoxFact(
            min_x=-50, min_y=-25, min_z=0, max_x=50, max_y=25, max_z=10, size_x=100, size_y=50, size_z=10
        ),
        volume=VolumeFact(volume=50000),
        cylinders=cylinders,
    )


def _cyl(index: int, diameter: float, x: float, y: float, *, height: float = 10.0, axis=None) -> CylinderFact:
    return CylinderFact(
        measurement_index=index,
        diameter=diameter,
        radius=diameter / 2.0,
        axis=axis or [0.0, 0.0, 1.0],
        center=[x, y, 5.0],
        height=height,
    )


def _row(report, feature_id: str) -> tuple[str, dict[str, str], str | None]:
    feature = next(item for item in report.features if item.feature_id == feature_id)
    props = {item.property_name: item.status for item in feature.properties}
    correspondence = feature.correspondence.status if feature.correspondence is not None else None
    return feature.status, props, correspondence


class HoleAdversarialTests(unittest.TestCase):
    """False PASS is the gate. Negative cases must not promote to feature PASS."""

    def test_correct_diameter_wrong_position_fails_position(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_box(), features=[_hole("h", 20.0, 10.0)]),
            _meas([_cyl(0, 6.0, 0.0, 0.0)]),
        )
        status, props, _ = _row(report, "h")
        self.assertEqual(props["position"], "FAIL")
        self.assertEqual(status, "FAIL")
        self.assertNotEqual(status, "PASS")

    def test_correct_position_wrong_diameter_fails_diameter_not_existence(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_box(), features=[_hole("h", 20.0, 10.0, diameter=6.0)]),
            _meas([_cyl(0, 5.0, 20.0, 10.0)]),
        )
        status, props, corr = _row(report, "h")
        self.assertEqual(corr, "UNIQUE")
        self.assertEqual(props["existence"], "PASS")
        self.assertEqual(props["position"], "PASS")
        self.assertEqual(props["diameter"], "FAIL")
        self.assertEqual(status, "FAIL")

    def test_wrong_axis_fails_axis(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_box(), features=[_hole("h", 20.0, 10.0)]),
            _meas([_cyl(0, 6.0, 20.0, 10.0, axis=[1.0, 0.0, 0.0])]),
        )
        status, props, _ = _row(report, "h")
        self.assertEqual(props["axis"], "FAIL")
        self.assertEqual(status, "FAIL")

    def test_wrong_depth_fails_depth_and_through(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_box(), features=[_hole("h", 20.0, 10.0)]),
            _meas([_cyl(0, 6.0, 20.0, 10.0, height=4.0)]),
        )
        status, props, _ = _row(report, "h")
        self.assertEqual(props["depth"], "FAIL")
        self.assertEqual(props["through"], "FAIL")
        self.assertEqual(status, "FAIL")

    def test_identical_holes_without_xy_are_ambiguous_with_no_pass(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_box(), features=[_hole("a"), _hole("b")]),
            _meas([_cyl(0, 6.0, -20.0, 10.0), _cyl(1, 6.0, 20.0, 10.0)]),
        )
        for fid in ("a", "b"):
            status, props, corr = _row(report, fid)
            self.assertEqual(corr, "AMBIGUOUS")
            self.assertTrue(all(value != "PASS" for value in props.values()))
            self.assertEqual(status, "UNKNOWN")

    def test_no_cylinders_is_not_found_not_pass(self) -> None:
        report = verify_feature_plan(FeaturePlanV3(base_feature=_box(), features=[_hole("h", 20.0, 10.0)]), _meas([]))
        status, props, corr = _row(report, "h")
        self.assertEqual(corr, "NONE")
        self.assertEqual(props["existence"], "FAIL")
        self.assertEqual(status, "FAIL")

    def test_missing_measurement_is_unknown_not_pass(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_box(), features=[_hole("h", 20.0, 10.0)]),
            GeometryMeasurementReport(status="MEASUREMENT_UNAVAILABLE", errors=["no shape"]),
        )
        status, props, corr = _row(report, "h")
        self.assertEqual(corr, "UNAVAILABLE")
        self.assertEqual(props["existence"], "UNKNOWN")
        self.assertEqual(status, "UNKNOWN")

    def test_hollow_inner_bore_is_not_a_child_hole(self) -> None:
        tube = FeatureV3(
            id="tube",
            type="hollow_cylinder",
            operation="base",
            dimensions={
                "outer_diameter": {"value": 40},
                "inner_diameter": {"value": 6},
                "length": {"value": 20},
            },
            placement=PlacementV3(axis="Z"),
        )
        measurement = GeometryMeasurementReport(
            bounding_box=BoundingBoxFact(
                min_x=-20, min_y=-20, min_z=0, max_x=20, max_y=20, max_z=20, size_x=40, size_y=40, size_z=20
            ),
            volume=VolumeFact(volume=1),
            cylinders=[_cyl(0, 6.0, 0.0, 0.0, height=20.0), _cyl(1, 40.0, 0.0, 0.0, height=20.0)],
        )
        report = verify_feature_plan(FeaturePlanV3(base_feature=tube, features=[_hole("h", 0.0, 0.0)]), measurement)
        status, props, _ = _row(report, "h")
        self.assertNotEqual(status, "PASS")
        self.assertNotEqual(props.get("existence"), "PASS")

    def test_unlocated_wrong_diameter_does_not_steal_located_hole(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(
                base_feature=_box(),
                features=[
                    _hole("center", 0.0, 0.0, diameter=25.0),
                    _hole("seed", None, None, diameter=6.0),
                ],
            ),
            _meas([_cyl(0, 25.0, 0.0, 0.0)]),
        )
        center_status, center_props, center_corr = _row(report, "center")
        seed_status, seed_props, seed_corr = _row(report, "seed")
        self.assertEqual(center_corr, "UNIQUE")
        self.assertEqual(center_props["existence"], "PASS")
        self.assertEqual(center_props["diameter"], "PASS")
        self.assertEqual(center_status, "PASS")
        self.assertNotEqual(seed_status, "PASS")
        self.assertNotEqual(seed_props.get("existence"), "PASS")
        self.assertEqual(seed_corr, "NONE")

    def test_two_planned_holes_cannot_share_one_cylinder(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_box(), features=[_hole("a", 20.0, 10.0), _hole("b", 20.0, 10.0)]),
            _meas([_cyl(0, 6.0, 20.0, 10.0)]),
        )
        for fid in ("a", "b"):
            status, props, corr = _row(report, fid)
            self.assertEqual(corr, "AMBIGUOUS")
            self.assertEqual(status, "UNKNOWN")
            self.assertTrue(all(value != "PASS" for value in props.values()))


if __name__ == "__main__":
    unittest.main()
