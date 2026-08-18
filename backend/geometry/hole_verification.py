"""1D-2.2 hole verification over GeometryEvidence. Never auto-selects AMBIGUOUS."""

from __future__ import annotations

from typing import Any

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


class HoleVerifier:
    """Verify through_hole / blind_hole using MATCHED evidence only."""

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
            properties = [existence]
            reason = (
                evidence.correspondence.reason
                if evidence is not None
                else "Hole identity is not MATCHED, so remaining properties cannot be proven."
            )
            for name in ("diameter", "position", "axis", "depth"):
                properties.append(
                    VerificationPropertyResult(
                        property_name=name,
                        status="UNKNOWN",
                        reason=reason or "Hole identity is not MATCHED.",
                    )
                )
            properties.append(_through_without_identity(feature, reason))
            return _v._feature_result(feature, properties, correspondence=correspondence)

        candidate = evidence.bound_candidates[0]
        evidence_ref = candidate.measurement_ref
        properties = [
            existence,
            _diameter_property(feature, candidate, policy, evidence_ref),
            _position_property(evidence, policy, evidence_ref),
            _axis_from_candidate(feature, candidate, policy, evidence_ref),
            _depth_property(feature, candidate, measurement, policy, evidence_ref),
            _through_property(feature, candidate, measurement, policy, evidence_ref),
        ]
        return _v._feature_result(feature, properties, correspondence=correspondence)


def _existence_property(evidence: GeometryEvidence | None, measurement: GeometryMeasurementReport) -> VerificationPropertyResult:
    if evidence is None:
        return VerificationPropertyResult(
            property_name="existence",
            status="UNKNOWN",
            reason="No geometry evidence was supplied for this hole.",
        )
    status = evidence.correspondence.status
    if status == "MATCHED":
        return VerificationPropertyResult(
            property_name="existence",
            status="PASS",
            expected="one MATCHED cylindrical bore",
            actual=evidence.correspondence.selected_candidate_ids,
            evidence_refs=[item.measurement_ref for item in evidence.bound_candidates],
            reason="Exactly one cylindrical candidate survived evaluable identity filters.",
        )
    if status == "NOT_FOUND" and measurement.status == "MEASUREMENT_SUCCESS":
        return VerificationPropertyResult(
            property_name="existence",
            status="FAIL",
            expected="one MATCHED cylindrical bore",
            actual=[],
            reason=evidence.correspondence.reason or "No measured cylinder corresponds to this hole.",
        )
    return VerificationPropertyResult(
        property_name="existence",
        status="UNKNOWN",
        expected="one MATCHED cylindrical bore",
        actual=evidence.correspondence.candidate_ids,
        reason=evidence.correspondence.reason or f"Hole correspondence is {status}; no candidate was selected.",
    )


def _diameter_property(
    feature: FeatureV3,
    candidate: GeometryCandidate,
    policy: VerificationTolerancePolicy,
    evidence_ref: str,
) -> VerificationPropertyResult:
    from backend.geometry import verification as _v

    expected = _v._dimension(feature, "diameter")
    if expected is None:
        expected = _v._dimension(feature, "hole_diameter")
    return _v._numeric_property(
        "diameter",
        expected,
        candidate.properties.get("diameter"),
        policy.linear_absolute_mm,
        policy.linear_relative,
        evidence_ref=evidence_ref,
        unavailable_reason="Matched cylindrical surface has no reliable diameter.",
    )


def _position_property(
    evidence: GeometryEvidence,
    policy: VerificationTolerancePolicy,
    evidence_ref: str,
) -> VerificationPropertyResult:
    constraint = _constraint(evidence, "position")
    if constraint is None or constraint.status == "UNAVAILABLE":
        return VerificationPropertyResult(
            property_name="position",
            status="UNKNOWN",
            reason=constraint.reason if constraint is not None else "Position was not evaluated.",
        )
    expected = constraint.expected
    actual = constraint.observed
    if constraint.status == "MATCHED":
        return VerificationPropertyResult(
            property_name="position",
            status="PASS",
            expected=expected,
            actual=actual,
            tolerance={"linear_absolute_mm": policy.linear_absolute_mm},
            evidence_refs=[evidence_ref],
            reason="Hole XY is the axis intersection with the host plane, within software tolerance.",
        )
    if constraint.status == "NOT_FOUND":
        return VerificationPropertyResult(
            property_name="position",
            status="FAIL",
            expected=expected,
            actual=actual,
            tolerance={"linear_absolute_mm": policy.linear_absolute_mm},
            evidence_refs=[evidence_ref],
            reason=constraint.reason or "Matched cylinder does not intersect the host plane at the expected XY.",
        )
    return VerificationPropertyResult(
        property_name="position",
        status="UNKNOWN",
        expected=expected,
        actual=actual,
        reason=constraint.reason or "Position correspondence is not unique.",
    )


def _axis_from_candidate(
    feature: FeatureV3,
    candidate: GeometryCandidate,
    policy: VerificationTolerancePolicy,
    evidence_ref: str,
) -> VerificationPropertyResult:
    from backend.geometry import verification as _v

    expected = _v._explicit_axis(feature)
    actual = candidate.properties.get("axis")
    if expected is None:
        return _v._skipped("axis", "FeaturePlan does not explicitly declare an intended hole axis.")
    if not isinstance(actual, list):
        return VerificationPropertyResult(
            property_name="axis",
            status="UNKNOWN",
            expected=expected,
            evidence_refs=[evidence_ref],
            reason="Matched cylindrical surface has no reliable axis measurement.",
        )
    return _v._axis_property(expected, [float(value) for value in actual], policy, evidence_ref)


def _depth_property(
    feature: FeatureV3,
    candidate: GeometryCandidate,
    measurement: GeometryMeasurementReport,
    policy: VerificationTolerancePolicy,
    evidence_ref: str,
) -> VerificationPropertyResult:
    from backend.geometry import verification as _v

    expected = _expected_depth(feature, measurement)
    actual = candidate.properties.get("height")
    result = _v._numeric_property(
        "depth",
        expected,
        actual,
        policy.linear_absolute_mm,
        policy.linear_relative,
        evidence_ref=evidence_ref,
        unavailable_reason="Cylindrical V-span is unavailable, so hole depth cannot be proven.",
    )
    if result.status in {"PASS", "FAIL"}:
        result.reason = (
            "Observed depth is the cylindrical-face V-span compared with host thickness or specified blind depth; "
            "it is not a manufacturing depth or bottom-face measurement. "
            + result.reason
        )
    return result


def _through_property(
    feature: FeatureV3,
    candidate: GeometryCandidate,
    measurement: GeometryMeasurementReport,
    policy: VerificationTolerancePolicy,
    evidence_ref: str,
) -> VerificationPropertyResult:
    from backend.geometry import verification as _v

    if feature.type != "through_hole":
        return _v._skipped("through", "Not a through-hole feature.")
    expected = _host_axis_size(feature, measurement)
    actual = candidate.properties.get("height")
    result = _v._numeric_property(
        "through",
        expected,
        actual,
        policy.linear_absolute_mm,
        policy.linear_relative,
        evidence_ref=evidence_ref,
        unavailable_reason="Through-ness cannot be proven without a host thickness and cylindrical V-span.",
    )
    result.property_name = "through"
    if result.status in {"PASS", "FAIL"}:
        result.reason = (
            "Through-ness is span evidence (V-span versus host AABB size along the hole axis), "
            "not a topological both-ends-open proof. "
            + result.reason
        )
    return result


def _through_without_identity(feature: FeatureV3, reason: str) -> VerificationPropertyResult:
    from backend.geometry import verification as _v

    if feature.type != "through_hole":
        return _v._skipped("through", "Not a through-hole feature.")
    return VerificationPropertyResult(property_name="through", status="UNKNOWN", reason=reason or "Hole identity is not MATCHED.")


def _expected_depth(feature: FeatureV3, measurement: GeometryMeasurementReport) -> float | None:
    from backend.geometry import verification as _v

    if feature.type == "blind_hole":
        return _v._dimension(feature, "depth")
    return _host_axis_size(feature, measurement)


def _host_axis_size(feature: FeatureV3, measurement: GeometryMeasurementReport) -> float | None:
    bounds = host_aabb(measurement)
    if bounds is None:
        return None
    axis = feature.placement.axis or "Z"
    return {"X": bounds.get("size_x"), "Y": bounds.get("size_y"), "Z": bounds.get("size_z")}.get(axis)


class BossVerifier:
    """Verify boss_cylinder using the same GeometryEvidence contract as holes."""

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
                else "Boss identity is not MATCHED, so remaining properties cannot be proven."
            )
            properties = [existence]
            for name in ("diameter", "height", "axis", "position", "host"):
                properties.append(VerificationPropertyResult(property_name=name, status="UNKNOWN", reason=reason))
            return _v._feature_result(feature, properties, correspondence=correspondence)

        candidate = evidence.bound_candidates[0]
        evidence_ref = candidate.measurement_ref
        properties = [
            existence,
            _diameter_property(feature, candidate, policy, evidence_ref),
            _height_property(feature, candidate, policy, evidence_ref),
            _axis_from_candidate(feature, candidate, policy, evidence_ref),
            _position_property(evidence, policy, evidence_ref),
            _host_property(evidence),
        ]
        return _v._feature_result(feature, properties, correspondence=correspondence)


def _height_property(
    feature: FeatureV3,
    candidate: GeometryCandidate,
    policy: VerificationTolerancePolicy,
    evidence_ref: str,
) -> VerificationPropertyResult:
    from backend.geometry import verification as _v

    expected = _v._dimension(feature, "height")
    if expected is None:
        expected = _v._dimension(feature, "length")
    result = _v._numeric_property(
        "height",
        expected,
        candidate.properties.get("height"),
        policy.linear_absolute_mm,
        policy.linear_relative,
        evidence_ref=evidence_ref,
        unavailable_reason="Cylindrical V-span is unavailable, so boss height cannot be proven.",
    )
    if result.status in {"PASS", "FAIL"}:
        result.reason = "Observed height is the cylindrical-face V-span, not a manufacturing height. " + result.reason
    return result


def _host_property(evidence: GeometryEvidence) -> VerificationPropertyResult:
    constraint = _constraint(evidence, "host")
    if constraint is None:
        return VerificationPropertyResult(property_name="host", status="UNKNOWN", reason="Host was not evaluated.")
    if constraint.status == "MATCHED":
        return VerificationPropertyResult(
            property_name="host",
            status="PASS",
            observed=constraint.observed,
            reason="A host bounding region is available for this feature.",
        )
    if constraint.status == "UNAVAILABLE":
        return VerificationPropertyResult(property_name="host", status="UNKNOWN", reason=constraint.reason)
    return VerificationPropertyResult(
        property_name="host",
        status="FAIL" if constraint.status == "NOT_FOUND" else "UNKNOWN",
        reason=constraint.reason or "Host correspondence is not unique.",
    )


def _constraint(evidence: GeometryEvidence, kind: str) -> Any:
    for item in evidence.correspondence.constraint_results:
        if item.constraint == kind:
            return item
    return None
