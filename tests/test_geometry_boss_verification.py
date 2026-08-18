from __future__ import annotations

import unittest

from backend.geometry.verification import DEFAULT_VERIFICATION_REGISTRY, verify_feature_plan
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


def _boss(fid: str, x=None, y=None, diameter=10.0, height=8.0) -> FeatureV3:
    return FeatureV3(
        id=fid,
        type="boss_cylinder",
        operation="add",
        dimensions={"diameter": {"value": diameter}, "height": {"value": height}},
        placement=PlacementV3(reference="origin", axis="Z", x=x, y=y, z=10.0),
    )


def _meas(cylinders: list[CylinderFact]) -> GeometryMeasurementReport:
    return GeometryMeasurementReport(
        bounding_box=BoundingBoxFact(
            min_x=-50, min_y=-25, min_z=0, max_x=50, max_y=25, max_z=18, size_x=100, size_y=50, size_z=18
        ),
        volume=VolumeFact(volume=50000),
        cylinders=cylinders,
    )


def _cyl(index: int, diameter: float, x: float, y: float, *, height: float = 8.0) -> CylinderFact:
    return CylinderFact(
        measurement_index=index,
        diameter=diameter,
        radius=diameter / 2.0,
        axis=[0.0, 0.0, 1.0],
        center=[x, y, 10.0],
        height=height,
    )


def _props(report, fid: str) -> dict[str, str]:
    feature = next(item for item in report.features if item.feature_id == fid)
    return {item.property_name: item.status for item in feature.properties}


class BossVerificationTests(unittest.TestCase):
    def test_registry_uses_same_evidence_framework(self) -> None:
        capability = DEFAULT_VERIFICATION_REGISTRY.get_capability("boss_cylinder")
        self.assertIsNotNone(capability)
        self.assertEqual(capability.implementation_status, "partial")
        self.assertIn("existence", capability.supported_properties)
        self.assertIn("host", capability.supported_properties)

    def test_unique_boss_passes_core_properties(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_box(), features=[_boss("boss_a", 20.0, 10.0)]),
            _meas([_cyl(0, 10.0, 20.0, 10.0)]),
        )
        boss = next(item for item in report.features if item.feature_id == "boss_a")
        self.assertEqual(_props(report, "boss_a"), {
            "existence": "PASS",
            "diameter": "PASS",
            "height": "PASS",
            "axis": "PASS",
            "position": "PASS",
            "host": "PASS",
        })
        self.assertEqual(boss.status, "PASS")

    def test_two_unpositioned_bosses_are_ambiguous(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_box(), features=[_boss("a"), _boss("b")]),
            _meas([_cyl(0, 10.0, -20.0, 10.0), _cyl(1, 10.0, 20.0, 10.0)]),
        )
        for fid in ("a", "b"):
            feature = next(item for item in report.features if item.feature_id == fid)
            self.assertEqual(feature.correspondence.status, "AMBIGUOUS")
            self.assertTrue(all(status != "PASS" for status in _props(report, fid).values()))
            self.assertEqual(feature.status, "UNKNOWN")

    def test_wrong_diameter_at_correct_position_fails_diameter(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_box(), features=[_boss("boss_a", 20.0, 10.0, diameter=10.0)]),
            _meas([_cyl(0, 8.0, 20.0, 10.0)]),
        )
        props = _props(report, "boss_a")
        self.assertEqual(props["existence"], "PASS")
        self.assertEqual(props["position"], "PASS")
        self.assertEqual(props["diameter"], "FAIL")
        self.assertEqual(next(item.status for item in report.features if item.feature_id == "boss_a"), "FAIL")

    def test_hole_and_boss_cannot_share_one_cylinder(self) -> None:
        plan = FeaturePlanV3(
            base_feature=_box(),
            features=[
                FeatureV3(
                    id="hole_a",
                    type="through_hole",
                    operation="remove",
                    dimensions={"diameter": {"value": 10}},
                    placement=PlacementV3(axis="Z", x=20.0, y=10.0),
                ),
                _boss("boss_a", 20.0, 10.0, diameter=10.0),
            ],
        )
        report = verify_feature_plan(plan, _meas([_cyl(0, 10.0, 20.0, 10.0, height=10.0)]))
        for fid in ("hole_a", "boss_a"):
            feature = next(item for item in report.features if item.feature_id == fid)
            self.assertEqual(feature.correspondence.status, "AMBIGUOUS")
            self.assertNotEqual(feature.status, "PASS")

    def test_hollow_host_cylinder_is_not_a_boss(self) -> None:
        tube = FeatureV3(
            id="tube",
            type="hollow_cylinder",
            operation="base",
            dimensions={"outer_diameter": {"value": 40}, "inner_diameter": {"value": 10}, "length": {"value": 20}},
            placement=PlacementV3(axis="Z"),
        )
        measurement = GeometryMeasurementReport(
            bounding_box=BoundingBoxFact(
                min_x=-20, min_y=-20, min_z=0, max_x=20, max_y=20, max_z=20, size_x=40, size_y=40, size_z=20
            ),
            volume=VolumeFact(volume=1),
            cylinders=[_cyl(0, 10.0, 0.0, 0.0, height=20.0), _cyl(1, 40.0, 0.0, 0.0, height=20.0)],
        )
        report = verify_feature_plan(FeaturePlanV3(base_feature=tube, features=[_boss("boss_a", 0.0, 0.0, diameter=10.0, height=20.0)]), measurement)
        boss = next(item for item in report.features if item.feature_id == "boss_a")
        self.assertNotEqual(boss.status, "PASS")


if __name__ == "__main__":
    unittest.main()
