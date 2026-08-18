"""0.7.3-B groove verification over the same GeometryEvidence types as holes/bosses."""

from __future__ import annotations

from backend.geometry.hole_verification import (
    _axis_from_candidate,
    _constraint,
    _existence_property,
    _host_property,
    _position_property,
)
from backend.geometry.references import host_aabb
from backend.geometry.resolver import to_geometry_correspondence
from backend.schemas import (
    FeatureV3,
    FeatureVerificationResult,
    GeometryCandidate,
    GeometryCorrespondence,
    GeometryEvidence,
    GeometryMeasurementReport,
    VerificationContext,
    VerificationPropertyResult,
    VerificationTolerancePolicy,
)


class GrooveVerifier:
    """Verify annular / internal annular grooves from MATCHED evidence only."""

    def verify(
        self,
        feature: FeatureV3,
        measurement: GeometryMeasurementReport,
        context: VerificationContext,
        *,
        plan_has_followup_features: bool,
        evidence: GeometryEvidence | None = None,
    ) -> FeatureVerificationResult:
        from backend.geometry import verification as _v

        _ = plan_has_followup_features
        policy = context.tolerance_policy
        correspondence = (
            to_geometry_correspondence(evidence.correspondence)
            if evidence is not None
            else GeometryCorrespondence(status="UNAVAILABLE", reason="No geometry evidence was supplied.")
        )
        existence = _existence_property(evidence, measurement)
        if evidence is None or evidence.correspondence.status != "MATCHED" or not evidence.bound_candidates:
            reason = (
                evidence.correspondence.reason
                if evidence is not None
                else "Groove identity is not MATCHED, so remaining properties cannot be proven."
            )
            properties = [existence]
            for name in ("width", "depth", "position", "axis", "host"):
                properties.append(VerificationPropertyResult(property_name=name, status="UNKNOWN", reason=reason))
            return _v._feature_result(feature, properties, correspondence=correspondence)

        candidate = evidence.bound_candidates[0]
        evidence_ref = candidate.measurement_ref
        properties = [
            existence,
            _width_property(feature, candidate, policy, evidence_ref),
            _groove_depth_property(feature, candidate, measurement, policy, evidence_ref),
            _position_property(evidence, policy, evidence_ref),
            _axis_from_candidate(feature, candidate, policy, evidence_ref),
            _host_property(evidence),
        ]
        return _v._feature_result(feature, properties, correspondence=correspondence)


def _width_property(
    feature: FeatureV3,
    candidate: GeometryCandidate,
    policy: VerificationTolerancePolicy,
    evidence_ref: str,
) -> VerificationPropertyResult:
    from backend.geometry import verification as _v

    expected = _v._dimension(feature, "axial_width")
    if expected is None:
        expected = _v._dimension(feature, "width")
    result = _v._numeric_property(
        "width",
        expected,
        candidate.properties.get("height"),
        policy.linear_absolute_mm,
        policy.linear_relative,
        evidence_ref=evidence_ref,
        unavailable_reason="Cylindrical V-span is unavailable, so groove width cannot be proven.",
    )
    if result.status in {"PASS", "FAIL"}:
        result.reason = "Observed width is the cylindrical-face V-span versus axial_width. " + result.reason
    return result


def _groove_depth_property(
    feature: FeatureV3,
    candidate: GeometryCandidate,
    measurement: GeometryMeasurementReport,
    policy: VerificationTolerancePolicy,
    evidence_ref: str,
) -> VerificationPropertyResult:
    from backend.geometry import verification as _v

    bound = candidate.properties.get("diameter")
    if feature.type == "internal_annular_groove":
        expected = _v._dimension(feature, "groove_depth")
        if expected is None:
            expected = _v._dimension(feature, "depth")
        host_inner = _extreme_cylinder_diameter(measurement, minimum=True)
        actual = None if bound is None or host_inner is None else (float(bound) - float(host_inner)) / 2.0
        return _v._numeric_property(
            "depth",
            expected,
            actual,
            policy.linear_absolute_mm,
            policy.linear_relative,
            evidence_ref=evidence_ref,
            unavailable_reason="Host inner diameter is not observable, so internal groove depth cannot be proven.",
        )

    expected = None
    host_outer = _host_outer_diameter(measurement)
    reduced = _v._dimension(feature, "reduced_outer_diameter")
    if host_outer is not None and reduced is not None:
        expected = (float(host_outer) - float(reduced)) / 2.0
    actual = None if bound is None or host_outer is None else (float(host_outer) - float(bound)) / 2.0
    return _v._numeric_property(
        "depth",
        expected,
        actual,
        policy.linear_absolute_mm,
        policy.linear_relative,
        evidence_ref=evidence_ref,
        unavailable_reason="Host outer diameter is not observable, so groove depth cannot be proven.",
    )


def _host_outer_diameter(measurement: GeometryMeasurementReport) -> float | None:
    bounds = host_aabb(measurement)
    if bounds is not None:
        size_x = bounds.get("size_x")
        size_y = bounds.get("size_y")
        if size_x is not None and size_y is not None:
            return max(float(size_x), float(size_y))
    return _extreme_cylinder_diameter(measurement, minimum=False)


def _extreme_cylinder_diameter(measurement: GeometryMeasurementReport, *, minimum: bool) -> float | None:
    values = [float(item.diameter) for item in measurement.cylinders if item.diameter is not None]
    if not values:
        return None
    return min(values) if minimum else max(values)
