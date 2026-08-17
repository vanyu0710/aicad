from __future__ import annotations

from copy import deepcopy
import unittest

from backend.feature_definitions import FEATURE_DEFINITIONS
from backend.geometry.resolver import (
    _axis_plane_intersection,
    resolve_feature_geometry_evidence,
    to_geometry_correspondence,
)
from backend.geometry.signatures import compile_feature_signature, compile_plan_signatures
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


def _hole(feature_id: str, *, x: float | None, y: float | None, diameter: float = 6.0) -> FeatureV3:
    return FeatureV3(
        id=feature_id,
        type="through_hole",
        operation="remove",
        dimensions={"diameter": {"value": diameter}},
        placement=PlacementV3(reference="origin", axis="Z", x=x, y=y, z=0.0),
    )


def _box_measurement(cylinders: list[CylinderFact] | None = None, *, status: str = "MEASUREMENT_SUCCESS") -> GeometryMeasurementReport:
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


def _cylinder(index: int, *, diameter: float, x: float, y: float, z: float = 4.0, axis: list[float] | None = None) -> CylinderFact:
    return CylinderFact(
        measurement_index=index,
        diameter=diameter,
        radius=diameter / 2.0,
        axis=axis or [0.0, 0.0, 1.0],
        center=[x, y, z],
        height=10.0,
    )


def _two_hole_plan(*, with_positions: bool) -> FeaturePlanV3:
    return FeaturePlanV3(
        base_feature=_box_base(),
        features=[
            _hole("hole_a", x=20.0 if with_positions else None, y=10.0 if with_positions else None),
            _hole("hole_b", x=-20.0 if with_positions else None, y=10.0 if with_positions else None),
        ],
    )


def _two_hole_measurement() -> GeometryMeasurementReport:
    return _box_measurement(
        [
            _cylinder(0, diameter=6.0, x=-20.0, y=10.0, z=3.0),
            _cylinder(1, diameter=6.0, x=20.0, y=10.0, z=7.0),
        ]
    )


def _evidence(report, feature_id: str):
    return next(item for item in report.features if item.feature_id == feature_id)


def _constraint(evidence, kind: str):
    return next(item for item in evidence.correspondence.constraint_results if item.constraint == kind)


class GeometryEvidenceResolverTests(unittest.TestCase):
    def test_signature_covers_every_registered_type_without_inventing_dimensions(self) -> None:
        for definition in FEATURE_DEFINITIONS.list():
            feature = FeatureV3(id=f"f_{definition.feature_type}", type=definition.feature_type, operation=definition.operation)
            signature = compile_feature_signature(feature)
            self.assertEqual(signature.feature_type, definition.feature_type)
            self.assertFalse(any(item.evaluable and item.kind == "dimension" for item in signature.constraints))
            for item in signature.constraints:
                if item.kind == "dimension":
                    self.assertIsNone(item.expected)

    def test_reference_exposes_origin_and_aabb_faces(self) -> None:
        plan = FeaturePlanV3(base_feature=_box_base())
        report = resolve_feature_geometry_evidence(plan, _box_measurement())
        names = {frame.name for frame in report.features[0].reference.frames}
        self.assertIn("origin", names)
        self.assertIn("left", names)
        self.assertIn("front", names)
        self.assertIn("top", names)
        left = next(frame for frame in report.features[0].reference.frames if frame.name == "left")
        self.assertEqual(left.origin[0], -50)

    def test_unknown_reference_is_unavailable(self) -> None:
        feature = _box_base()
        feature.placement.reference = "angled_face"
        plan = FeaturePlanV3(base_feature=feature)
        report = resolve_feature_geometry_evidence(plan, _box_measurement())
        self.assertEqual(report.features[0].reference.status, "UNAVAILABLE")

    def test_unique_cylinder_is_matched(self) -> None:
        plan = FeaturePlanV3(
            base_feature=FeatureV3(
                id="base_cyl",
                type="cylinder_base",
                operation="base",
                dimensions={"outer_diameter": {"value": 10}, "length": {"value": 20}},
                placement=PlacementV3(axis="Z"),
            )
        )
        measurement = GeometryMeasurementReport(
            bounding_box=BoundingBoxFact(min_x=-5, min_y=-5, min_z=0, max_x=5, max_y=5, max_z=20, size_x=10, size_y=10, size_z=20),
            volume=VolumeFact(volume=1570),
            cylinders=[_cylinder(0, diameter=10.0, x=0.0, y=0.0)],
        )
        evidence = _evidence(resolve_feature_geometry_evidence(plan, measurement), "base_cyl")
        self.assertEqual(evidence.correspondence.status, "MATCHED")
        self.assertEqual(evidence.correspondence.selected_candidate_ids, ["cyl:0"])
        self.assertTrue(evidence.usable_for_verification)

    def test_two_equal_holes_without_position_are_ambiguous_and_not_auto_selected(self) -> None:
        report = resolve_feature_geometry_evidence(_two_hole_plan(with_positions=False), _two_hole_measurement())
        for feature_id in ("hole_a", "hole_b"):
            evidence = _evidence(report, feature_id)
            self.assertEqual(_constraint(evidence, "dimension").status, "AMBIGUOUS")
            self.assertEqual(_constraint(evidence, "position").status, "UNAVAILABLE")
            self.assertEqual(evidence.correspondence.status, "AMBIGUOUS")
            self.assertEqual(evidence.correspondence.selected_candidate_ids, [])
            self.assertEqual(sorted(evidence.correspondence.candidate_ids), ["cyl:0", "cyl:1"])
            self.assertFalse(evidence.usable_for_verification)
            self.assertEqual(evidence.bound_candidates, [])

    def test_two_equal_holes_with_position_bind_distinct_cylinders(self) -> None:
        report = resolve_feature_geometry_evidence(_two_hole_plan(with_positions=True), _two_hole_measurement())
        hole_a = _evidence(report, "hole_a")
        hole_b = _evidence(report, "hole_b")
        self.assertEqual(hole_a.correspondence.status, "MATCHED")
        self.assertEqual(hole_b.correspondence.status, "MATCHED")
        self.assertEqual(hole_a.correspondence.selected_candidate_ids, ["cyl:1"])
        self.assertEqual(hole_b.correspondence.selected_candidate_ids, ["cyl:0"])
        self.assertEqual(_constraint(hole_a, "position").status, "MATCHED")
        self.assertEqual(_constraint(hole_b, "position").status, "MATCHED")

    def test_zero_matching_cylinders_is_not_found(self) -> None:
        plan = FeaturePlanV3(base_feature=_box_base(), features=[_hole("hole_a", x=20.0, y=10.0, diameter=8.0)])
        evidence = _evidence(resolve_feature_geometry_evidence(plan, _two_hole_measurement()), "hole_a")
        self.assertEqual(_constraint(evidence, "dimension").status, "NOT_FOUND")
        self.assertEqual(evidence.correspondence.status, "NOT_FOUND")
        self.assertEqual(evidence.correspondence.selected_candidate_ids, [])

    def test_missing_measurement_or_diameter_is_unavailable_not_not_found(self) -> None:
        plan = FeaturePlanV3(base_feature=_box_base(), features=[_hole("hole_a", x=20.0, y=10.0)])
        empty = GeometryMeasurementReport(status="MEASUREMENT_UNAVAILABLE", errors=["no shape"])
        unavailable = _evidence(resolve_feature_geometry_evidence(plan, empty), "hole_a")
        self.assertEqual(unavailable.correspondence.status, "UNAVAILABLE")
        self.assertNotEqual(unavailable.correspondence.status, "NOT_FOUND")

        no_diameter = FeaturePlanV3(base_feature=_box_base(), features=[_hole("hole_a", x=20.0, y=10.0)])
        no_diameter.features[0].dimensions = {}
        missing = _evidence(resolve_feature_geometry_evidence(no_diameter, _two_hole_measurement()), "hole_a")
        self.assertEqual(_constraint(missing, "dimension").status, "UNAVAILABLE")
        self.assertEqual(missing.correspondence.status, "MATCHED")
        self.assertEqual(missing.correspondence.selected_candidate_ids, ["cyl:1"])

    def test_position_uses_axis_plane_intersection_not_raw_center(self) -> None:
        plan = FeaturePlanV3(base_feature=_box_base(), features=[_hole("hole_a", x=20.0, y=10.0)])
        off_plane = _box_measurement([_cylinder(0, diameter=6.0, x=20.0, y=10.0, z=99.0)])
        evidence = _evidence(resolve_feature_geometry_evidence(plan, off_plane), "hole_a")
        self.assertEqual(_constraint(evidence, "position").status, "MATCHED")
        self.assertEqual(evidence.correspondence.status, "MATCHED")

        intersected = _axis_plane_intersection([20.0, 10.0, 0.0], [0.0, 0.1, 1.0], [0.0, 0.0, 10.0], [0.0, 0.0, 1.0])
        self.assertIsNotNone(intersected)
        self.assertAlmostEqual(intersected[0], 20.0)
        self.assertNotAlmostEqual(intersected[1], 10.0)

    def test_resolver_does_not_mutate_plan_or_measurement(self) -> None:
        plan = _two_hole_plan(with_positions=True)
        measurement = _two_hole_measurement()
        before_plan = deepcopy(plan)
        before_measurement = deepcopy(measurement)
        resolve_feature_geometry_evidence(plan, measurement)
        self.assertEqual(plan, before_plan)
        self.assertEqual(measurement, before_measurement)

    def test_unsupported_feature_signature_is_visible_but_not_bound(self) -> None:
        plan = FeaturePlanV3(
            base_feature=_box_base(),
            features=[FeatureV3(id="fillet_1", type="fillet", operation="modify")],
        )
        evidence = _evidence(resolve_feature_geometry_evidence(plan, _box_measurement()), "fillet_1")
        self.assertEqual(evidence.signature.primary_kind, "edge")
        self.assertFalse(evidence.signature.bindable)
        self.assertEqual(evidence.correspondence.status, "UNAVAILABLE")
        self.assertEqual(evidence.correspondence.selected_candidate_ids, [])

    def test_legacy_adapter_maps_matched_and_ambiguous(self) -> None:
        matched = resolve_feature_geometry_evidence(_two_hole_plan(with_positions=True), _two_hole_measurement())
        ambiguous = resolve_feature_geometry_evidence(_two_hole_plan(with_positions=False), _two_hole_measurement())
        self.assertEqual(to_geometry_correspondence(_evidence(matched, "hole_a").correspondence).status, "UNIQUE")
        self.assertEqual(to_geometry_correspondence(_evidence(ambiguous, "hole_a").correspondence).status, "AMBIGUOUS")
        self.assertEqual(to_geometry_correspondence(_evidence(ambiguous, "hole_a").correspondence).candidate_indices, [0, 1])

    def test_verification_consumes_evidence_for_positioned_holes(self) -> None:
        plan = _two_hole_plan(with_positions=True)
        measurement = _two_hole_measurement()
        resolve_feature_geometry_evidence(plan, measurement)
        verification = verify_feature_plan(plan, measurement)
        hole = next(item for item in verification.features if item.feature_id == "hole_a")
        self.assertEqual(hole.status, "PASS")
        self.assertTrue(any(item.property_name == "existence" and item.status == "PASS" for item in hole.properties))

    def test_compile_plan_signatures_includes_base_and_children(self) -> None:
        signatures = compile_plan_signatures(_two_hole_plan(with_positions=True))
        self.assertEqual([item.feature_id for item in signatures], ["base_box", "hole_a", "hole_b"])


if __name__ == "__main__":
    unittest.main()
