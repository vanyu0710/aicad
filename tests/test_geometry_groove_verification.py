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


def _tube() -> FeatureV3:
    return FeatureV3(
        id="base_tube",
        type="hollow_cylinder",
        operation="base",
        dimensions={
            "outer_diameter": {"value": 40},
            "inner_diameter": {"value": 20},
            "length": {"value": 60},
        },
        placement=PlacementV3(axis="Z"),
    )


def _external(fid: str, *, reduced: float = 32.0, width: float = 6.0, z_start: float | None = 20.0) -> FeatureV3:
    dims = {"reduced_outer_diameter": {"value": reduced}, "axial_width": {"value": width}}
    if z_start is not None:
        dims["z_start"] = {"value": z_start}
    return FeatureV3(id=fid, type="annular_groove", operation="remove", dimensions=dims, placement=PlacementV3(axis="Z"))


def _internal(fid: str, *, depth: float = 3.0, width: float = 6.0, z_start: float | None = 20.0) -> FeatureV3:
    dims = {"groove_depth": {"value": depth}, "axial_width": {"value": width}}
    if z_start is not None:
        dims["z_start"] = {"value": z_start}
    return FeatureV3(id=fid, type="internal_annular_groove", operation="remove", dimensions=dims, placement=PlacementV3(axis="Z"))


def _meas(cylinders: list[CylinderFact]) -> GeometryMeasurementReport:
    return GeometryMeasurementReport(
        bounding_box=BoundingBoxFact(
            min_x=-20, min_y=-20, min_z=0, max_x=20, max_y=20, max_z=60, size_x=40, size_y=40, size_z=60
        ),
        volume=VolumeFact(volume=1),
        cylinders=cylinders,
    )


def _cyl(index: int, diameter: float, z: float, *, height: float = 6.0) -> CylinderFact:
    return CylinderFact(
        measurement_index=index,
        diameter=diameter,
        radius=diameter / 2.0,
        axis=[0.0, 0.0, 1.0],
        center=[0.0, 0.0, z],
        height=height,
    )


def _host_cylinders() -> list[CylinderFact]:
    return [_cyl(0, 20.0, 0.0, height=60.0), _cyl(1, 40.0, 0.0, height=60.0)]


def _props(report, fid: str) -> dict[str, str]:
    feature = next(item for item in report.features if item.feature_id == fid)
    return {item.property_name: item.status for item in feature.properties}


def _row(report, fid: str):
    feature = next(item for item in report.features if item.feature_id == fid)
    return feature.status, _props(report, fid), feature.correspondence.status if feature.correspondence else None


class GrooveVerificationTests(unittest.TestCase):
    def test_registry_reuses_evidence_framework(self) -> None:
        capability = DEFAULT_VERIFICATION_REGISTRY.get_capability("annular_groove")
        self.assertEqual(capability.implementation_status, "partial")
        self.assertIn("width", capability.supported_properties)
        self.assertIn("host", capability.supported_properties)

    def test_external_groove_binds_root_not_host(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_tube(), features=[_external("g1")]),
            _meas(_host_cylinders() + [_cyl(2, 32.0, 20.0)]),
        )
        status, props, corr = _row(report, "g1")
        self.assertEqual(corr, "UNIQUE")
        self.assertEqual(props["existence"], "PASS")
        self.assertEqual(props["width"], "PASS")
        self.assertEqual(props["depth"], "PASS")
        self.assertEqual(props["position"], "PASS")
        self.assertEqual(props["axis"], "PASS")
        self.assertEqual(props["host"], "PASS")
        self.assertEqual(status, "PASS")

    def test_internal_groove_uses_same_pipeline(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_tube(), features=[_internal("g1")]),
            _meas(_host_cylinders() + [_cyl(2, 26.0, 20.0)]),
        )
        status, props, _ = _row(report, "g1")
        self.assertEqual(props["existence"], "PASS")
        self.assertEqual(props["depth"], "PASS")
        self.assertEqual(status, "PASS")

    def test_wrong_z_fails_position(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_tube(), features=[_external("g1", z_start=20.0)]),
            _meas(_host_cylinders() + [_cyl(2, 32.0, 5.0)]),
        )
        status, props, _ = _row(report, "g1")
        self.assertEqual(props["existence"], "PASS")
        self.assertEqual(props["position"], "FAIL")
        self.assertEqual(status, "FAIL")

    def test_wrong_root_diameter_at_right_z_fails_depth(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_tube(), features=[_external("g1", reduced=32.0)]),
            _meas(_host_cylinders() + [_cyl(2, 36.0, 20.0)]),
        )
        status, props, _ = _row(report, "g1")
        self.assertEqual(props["existence"], "PASS")
        self.assertEqual(props["position"], "PASS")
        self.assertEqual(props["depth"], "FAIL")
        self.assertNotEqual(status, "PASS")

    def test_two_unlabeled_same_diameter_grooves_are_ambiguous(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_tube(), features=[_external("a", z_start=None), _external("b", z_start=None)]),
            _meas(_host_cylinders() + [_cyl(2, 32.0, 10.0), _cyl(3, 32.0, 40.0)]),
        )
        for fid in ("a", "b"):
            status, props, corr = _row(report, fid)
            self.assertEqual(corr, "AMBIGUOUS")
            self.assertTrue(all(value != "PASS" for value in props.values()))
            self.assertEqual(status, "UNKNOWN")

    def test_host_cylinder_is_not_the_groove(self) -> None:
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_tube(), features=[_external("g1")]),
            _meas(_host_cylinders()),
        )
        status, props, _ = _row(report, "g1")
        self.assertNotEqual(status, "PASS")
        self.assertNotEqual(props.get("existence"), "PASS")

    def test_groove_and_hole_cannot_share_one_cylinder(self) -> None:
        hole = FeatureV3(
            id="hole_a",
            type="through_hole",
            operation="remove",
            dimensions={"diameter": {"value": 32}},
            placement=PlacementV3(axis="Z", x=0.0, y=0.0),
        )
        report = verify_feature_plan(
            FeaturePlanV3(base_feature=_tube(), features=[_external("g1"), hole]),
            _meas(_host_cylinders() + [_cyl(2, 32.0, 20.0)]),
        )
        for fid in ("g1", "hole_a"):
            feature = next(item for item in report.features if item.feature_id == fid)
            self.assertNotEqual(feature.status, "PASS")


if __name__ == "__main__":
    unittest.main()
