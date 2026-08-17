"""Pure, composable semantic verification over FeaturePlan intent and BRep facts.

The module deliberately does not build geometry, mutate the plan, call models,
or make a verification result an execution-policy decision.
"""

from __future__ import annotations

from math import acos, degrees, isfinite, pi, sqrt
from typing import Any

from backend.geometry.correspondence import resolve_unique_cylinder
from backend.geometry.verification_registry import (
    FeatureVerifier,
    GeometryVerificationRegistry,
    build_default_registry,
)
from backend.schemas import (
    BoundingBoxExpectation,
    FeaturePlanV3,
    FeatureV3,
    FeatureVerificationResult,
    GeometryEvidenceReport,
    GeometryMeasurementReport,
    ModelVerificationReport,
    VerificationContext,
    VerificationPropertyResult,
    VerificationStatus,
    VerificationTolerancePolicy,
)


class BoxBaseVerifier:
    """Verify an isolated rectangular base against final BRep global facts."""

    def verify(
        self,
        feature: FeatureV3,
        measurement: GeometryMeasurementReport,
        context: VerificationContext,
        *,
        plan_has_followup_features: bool,
        evidence=None,
    ) -> FeatureVerificationResult:
        _ = evidence
        if plan_has_followup_features:
            return _unknown_feature(
                feature,
                ["size_x", "size_y", "size_z", "volume"],
                "Final BRep contains follow-up features, so it cannot safely prove isolated base dimensions.",
            )
        expected = {
            "size_x": _dimension(feature, "length"),
            "size_y": _dimension(feature, "width"),
            "size_z": _dimension(feature, "height"),
        }
        properties = _bounding_properties(expected, measurement, context.tolerance_policy)
        dimensions = list(expected.values())
        if all(value is not None and value > 0 for value in dimensions):
            expected_volume = float(dimensions[0] * dimensions[1] * dimensions[2])
            properties.append(_volume_property(expected_volume, measurement, context.tolerance_policy, "base_volume"))
        else:
            properties.append(_skipped("volume", "Base dimensions are incomplete; expected box volume is not defined."))
        return _feature_result(feature, properties)


class CylinderBaseVerifier:
    """Verify isolated cylindrical base primitives without classifying holes/bosses."""

    def verify(
        self,
        feature: FeatureV3,
        measurement: GeometryMeasurementReport,
        context: VerificationContext,
        *,
        plan_has_followup_features: bool,
        evidence=None,
    ) -> FeatureVerificationResult:
        _ = evidence
        if plan_has_followup_features:
            return _unknown_feature(
                feature,
                ["size_x", "size_y", "size_z", "volume", "cylindrical_geometry", "diameter", "axis"],
                "Final BRep contains follow-up features, so it cannot safely prove isolated base geometry.",
            )

        outer = _dimension(feature, "outer_diameter")
        length = _dimension(feature, "length")
        expected = {"size_x": outer, "size_y": outer, "size_z": length}
        properties = _bounding_properties(expected, measurement, context.tolerance_policy)

        inner = _dimension(feature, "inner_diameter") if feature.type == "hollow_cylinder" else None
        if outer is not None and length is not None and outer > 0 and length > 0 and (inner is None or 0 < inner < outer):
            expected_volume = (pi / 4.0) * (outer**2 - (inner or 0.0) ** 2) * length
            properties.append(_volume_property(expected_volume, measurement, context.tolerance_policy, "base_volume"))
        else:
            properties.append(_skipped("volume", "Base dimensions are incomplete or invalid; expected cylinder volume is not defined."))

        resolution = resolve_unique_cylinder(
            measurement.cylinders,
            diameter=outer,
            policy=context.tolerance_policy,
            measurement_status=measurement.status,
        )
        properties.extend(_cylinder_properties(feature, outer, resolution, context.tolerance_policy))
        return _feature_result(feature, properties, correspondence=resolution.correspondence)


def verify_feature_plan(
    plan: FeaturePlanV3,
    measurement: GeometryMeasurementReport,
    context: VerificationContext | None = None,
    *,
    registry: GeometryVerificationRegistry | None = None,
    evidence_report: GeometryEvidenceReport | None = None,
) -> ModelVerificationReport:
    """Return deterministic semantic evidence without changing plan or measurement."""

    context = context or VerificationContext()
    registry = registry or DEFAULT_VERIFICATION_REGISTRY
    if evidence_report is None:
        from backend.geometry.resolver import resolve_feature_geometry_evidence

        evidence_report = resolve_feature_geometry_evidence(plan, measurement, policy=context.tolerance_policy)
    evidence_by_id = {item.feature_id: item for item in evidence_report.features}
    global_properties = _global_properties(measurement, context)
    features: list[FeatureVerificationResult] = []
    errors: list[str] = []
    feature_list = ([plan.base_feature] if plan.base_feature is not None else []) + list(plan.features)
    for index, feature in enumerate(feature_list):
        has_followup = index == 0 and bool(plan.features)
        verifier = registry.get_verifier(feature.type)
        capability = registry.get_capability(feature.type)
        if verifier is None:
            property_names = capability.supported_properties if capability and capability.supported_properties else ["feature"]
            properties = [
                VerificationPropertyResult(
                    property_name=name,
                    status="UNSUPPORTED",
                    reason="No deterministic semantic verifier is implemented for this property in 1D-2.",
                )
                for name in property_names
            ]
            features.append(_feature_result(feature, properties))
            continue
        try:
            features.append(
                verifier.verify(
                    feature,
                    measurement,
                    context,
                    plan_has_followup_features=has_followup,
                    evidence=evidence_by_id.get(feature.id),
                )
            )
        except Exception as exc:  # A verification defect must not alter CAD execution.
            errors.append(f"Verification error for {feature.id}: {exc}")
            features.append(
                FeatureVerificationResult(
                    feature_id=feature.id,
                    feature_type=feature.type,
                    status="UNKNOWN",
                    reasons=[f"Verifier execution failed: {exc}"],
                )
            )
    return ModelVerificationReport(
        status=_aggregate_model_status(global_properties, features),
        measurement_version=measurement.measurement_version,
        global_properties=global_properties,
        features=features,
        errors=errors,
    )


def _global_properties(
    measurement: GeometryMeasurementReport, context: VerificationContext
) -> list[VerificationPropertyResult]:
    properties: list[VerificationPropertyResult] = []
    if context.expected_bounding_box is not None:
        expected = context.expected_bounding_box.model_dump(exclude_none=True)
        properties.extend(_bounding_properties(expected, measurement, context.tolerance_policy, prefix="model_"))
    if context.expected_volume is not None:
        properties.append(_volume_property(context.expected_volume, measurement, context.tolerance_policy, "model_volume"))
    return properties


def _bounding_properties(
    expected: dict[str, float | None],
    measurement: GeometryMeasurementReport,
    policy: VerificationTolerancePolicy,
    *,
    prefix: str = "",
) -> list[VerificationPropertyResult]:
    box = measurement.bounding_box
    return [
        _numeric_property(
            f"{prefix}{name}",
            expected_value,
            getattr(box, name, None) if box is not None and box.status == "MEASUREMENT_SUCCESS" else None,
            policy.linear_absolute_mm,
            policy.linear_relative,
            evidence_ref="geometry_measurement.bounding_box",
            unavailable_reason="Bounding-box measurement is unavailable.",
        )
        for name, expected_value in expected.items()
    ]


def _volume_property(
    expected: float | None,
    measurement: GeometryMeasurementReport,
    policy: VerificationTolerancePolicy,
    property_name: str,
) -> VerificationPropertyResult:
    actual = measurement.volume.volume if measurement.volume is not None and measurement.volume.status == "MEASUREMENT_SUCCESS" else None
    return _numeric_property(
        property_name,
        expected,
        actual,
        policy.volume_absolute_mm3,
        policy.volume_relative,
        evidence_ref="geometry_measurement.volume",
        unavailable_reason="Volume measurement is unavailable.",
    )


def _cylinder_properties(
    feature: FeatureV3,
    expected_diameter: float | None,
    resolution: Any,
    policy: VerificationTolerancePolicy,
) -> list[VerificationPropertyResult]:
    correspondence = resolution.correspondence
    if correspondence.status == "NONE":
        return [
            VerificationPropertyResult(
                property_name="cylindrical_geometry",
                status="FAIL",
                expected="one unique cylindrical surface",
                actual="none",
                evidence_refs=["geometry_measurement.cylinders"],
                reason=correspondence.reason,
            ),
            VerificationPropertyResult(
                property_name="diameter",
                status="FAIL",
                expected=expected_diameter,
                actual=None,
                evidence_refs=["geometry_measurement.cylinders"],
                reason=correspondence.reason,
            ),
            _skipped("axis", "No matching cylindrical surface is available for axis comparison."),
        ]
    if correspondence.status != "UNIQUE" or resolution.candidate is None:
        return [
            VerificationPropertyResult(
                property_name="cylindrical_geometry",
                status="UNKNOWN",
                expected="one unique cylindrical surface",
                actual=correspondence.candidate_indices,
                evidence_refs=["geometry_measurement.cylinders"],
                reason=correspondence.reason,
            ),
            VerificationPropertyResult(
                property_name="diameter",
                status="UNKNOWN",
                expected=expected_diameter,
                actual=None,
                evidence_refs=["geometry_measurement.cylinders"],
                reason=correspondence.reason,
            ),
            _skipped("axis", "Cylinder correspondence is not unique."),
        ]

    candidate = resolution.candidate
    evidence_ref = f"geometry_measurement.cylinders[{candidate.measurement_index}]"
    properties = [
        VerificationPropertyResult(
            property_name="cylindrical_geometry",
            status="PASS",
            expected="one unique cylindrical surface",
            actual=candidate.measurement_index,
            evidence_refs=[evidence_ref],
            reason="Unique cylindrical measurement candidate resolved by expected diameter.",
        ),
        _numeric_property(
            "diameter",
            expected_diameter,
            candidate.diameter,
            policy.linear_absolute_mm,
            policy.linear_relative,
            evidence_ref=evidence_ref,
            unavailable_reason="Matched cylindrical surface has no reliable diameter.",
        ),
    ]
    expected_axis = _explicit_axis(feature)
    if expected_axis is None:
        properties.append(_skipped("axis", "FeaturePlan does not explicitly declare an intended cylinder axis."))
    elif candidate.axis is None:
        properties.append(
            VerificationPropertyResult(
                property_name="axis",
                status="UNKNOWN",
                expected=expected_axis,
                actual=None,
                tolerance={"angular_degrees": policy.angular_degrees, "axial_equivalence": policy.axial_axis_equivalence},
                evidence_refs=[evidence_ref],
                reason="Matched cylindrical surface has no reliable axis measurement.",
            )
        )
    else:
        properties.append(_axis_property(expected_axis, candidate.axis, policy, evidence_ref))
    return properties


def _numeric_property(
    property_name: str,
    expected: float | None,
    actual: float | None,
    absolute_tolerance: float,
    relative_tolerance: float,
    *,
    evidence_ref: str,
    unavailable_reason: str,
) -> VerificationPropertyResult:
    if expected is None:
        return _skipped(property_name, "No expected value was supplied for verification.")
    if not isfinite(expected) or expected <= 0:
        return VerificationPropertyResult(
            property_name=property_name,
            status="UNKNOWN",
            expected=expected,
            evidence_refs=[evidence_ref],
            reason="Expected value is invalid for deterministic verification.",
        )
    if actual is None or not isfinite(actual):
        return VerificationPropertyResult(
            property_name=property_name,
            status="UNKNOWN",
            expected=expected,
            actual=actual,
            tolerance=_numeric_tolerance(expected, absolute_tolerance, relative_tolerance),
            evidence_refs=[evidence_ref],
            reason=unavailable_reason,
        )
    deviation = actual - expected
    tolerance = max(absolute_tolerance, abs(expected) * relative_tolerance)
    return VerificationPropertyResult(
        property_name=property_name,
        status="PASS" if abs(deviation) <= tolerance else "FAIL",
        expected=expected,
        actual=actual,
        tolerance=_numeric_tolerance(expected, absolute_tolerance, relative_tolerance),
        deviation=deviation,
        evidence_refs=[evidence_ref],
        reason="Observed value is within software verification tolerance."
        if abs(deviation) <= tolerance
        else "Observed value exceeds software verification tolerance.",
    )


def _axis_property(
    expected: list[float], actual: list[float], policy: VerificationTolerancePolicy, evidence_ref: str
) -> VerificationPropertyResult:
    expected_normalized = _normalize_vector(expected)
    actual_normalized = _normalize_vector(actual)
    if expected_normalized is None or actual_normalized is None:
        return VerificationPropertyResult(
            property_name="axis",
            status="UNKNOWN",
            expected=expected,
            actual=actual,
            tolerance={"angular_degrees": policy.angular_degrees, "axial_equivalence": policy.axial_axis_equivalence},
            evidence_refs=[evidence_ref],
            reason="Expected or observed axis is degenerate.",
        )
    dot = sum(left * right for left, right in zip(expected_normalized, actual_normalized))
    dot = abs(dot) if policy.axial_axis_equivalence else dot
    dot = min(1.0, max(-1.0, dot))
    angle = degrees(acos(dot))
    return VerificationPropertyResult(
        property_name="axis",
        status="PASS" if angle <= policy.angular_degrees else "FAIL",
        expected=expected_normalized,
        actual=actual_normalized,
        tolerance={"angular_degrees": policy.angular_degrees, "axial_equivalence": policy.axial_axis_equivalence},
        deviation=angle,
        evidence_refs=[evidence_ref],
        reason="Observed cylinder axis is within angular tolerance."
        if angle <= policy.angular_degrees
        else "Observed cylinder axis exceeds angular tolerance.",
    )


def _dimension(feature: FeatureV3, name: str) -> float | None:
    dimension = feature.dimensions.get(name)
    return dimension.value if dimension is not None else None


def _explicit_axis(feature: FeatureV3) -> list[float] | None:
    if "axis" not in feature.placement.model_fields_set:
        return None
    return {"X": [1.0, 0.0, 0.0], "Y": [0.0, 1.0, 0.0], "Z": [0.0, 0.0, 1.0]}.get(feature.placement.axis)


def _feature_result(
    feature: FeatureV3,
    properties: list[VerificationPropertyResult],
    *,
    correspondence=None,
) -> FeatureVerificationResult:
    return FeatureVerificationResult(
        feature_id=feature.id,
        feature_type=feature.type,
        status=_aggregate_feature_status(properties),
        properties=properties,
        correspondence=correspondence,
        reasons=[item.reason for item in properties if item.reason],
    )


def _unknown_feature(feature: FeatureV3, property_names: list[str], reason: str) -> FeatureVerificationResult:
    return _feature_result(
        feature,
        [VerificationPropertyResult(property_name=name, status="UNKNOWN", reason=reason) for name in property_names],
    )


def _skipped(property_name: str, reason: str) -> VerificationPropertyResult:
    return VerificationPropertyResult(property_name=property_name, status="SKIPPED", reason=reason)


def _numeric_tolerance(expected: float, absolute: float, relative: float) -> dict[str, float]:
    return {
        "absolute": absolute,
        "relative": relative,
        "effective": max(absolute, abs(expected) * relative),
    }


def _normalize_vector(vector: list[float]) -> list[float] | None:
    if len(vector) != 3 or not all(isfinite(value) for value in vector):
        return None
    magnitude = sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return None
    return [value / magnitude for value in vector]


def _aggregate_feature_status(properties: list[VerificationPropertyResult]) -> VerificationStatus:
    statuses = {item.status for item in properties}
    if "FAIL" in statuses:
        return "FAIL"
    if "PASS" in statuses and statuses.issubset({"PASS", "SKIPPED"}):
        return "PASS"
    if "PASS" in statuses or "UNKNOWN" in statuses:
        return "UNKNOWN"
    if "UNSUPPORTED" in statuses:
        return "UNSUPPORTED"
    return "SKIPPED"


def _aggregate_model_status(
    global_properties: list[VerificationPropertyResult], features: list[FeatureVerificationResult]
) -> str:
    property_statuses = [item.status for item in global_properties]
    property_statuses.extend(item.status for feature in features for item in feature.properties)
    if "FAIL" in property_statuses:
        return "FAILED"
    if "PASS" in property_statuses and all(status in {"PASS", "SKIPPED"} for status in property_statuses):
        return "VERIFIED"
    if "PASS" in property_statuses:
        return "PARTIALLY_VERIFIED"
    if "UNKNOWN" in property_statuses:
        return "UNKNOWN"
    if "UNSUPPORTED" in property_statuses:
        return "UNSUPPORTED"
    return "UNKNOWN"


from backend.geometry.hole_verification import HoleVerifier

DEFAULT_VERIFICATION_REGISTRY = build_default_registry(BoxBaseVerifier(), CylinderBaseVerifier(), HoleVerifier())
