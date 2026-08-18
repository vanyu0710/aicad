"""Pure Feature-to-BRep geometry evidence resolver (v0.7 1D-2.1)."""

from __future__ import annotations

from math import acos, degrees, isfinite, sqrt
from typing import Any, Iterable

from backend.geometry.candidates import collect_geometry_candidates
from backend.geometry.references import frame_by_name, host_aabb, resolve_reference_context
from backend.geometry.signatures import compile_feature_signature
from backend.schemas import (
    ConstraintResult,
    CorrespondenceResult,
    CorrespondenceStatus,
    FeatureGeometrySignature,
    FeaturePlanV3,
    FeatureV3,
    GeometryCandidate,
    GeometryCorrespondence,
    GeometryEvidence,
    GeometryEvidenceReport,
    GeometryMeasurementReport,
    ReferenceContext,
    SignatureConstraint,
    VerificationTolerancePolicy,
)


_CONSTRAINT_ORDER = ("type", "position", "axis", "dimension", "region", "host", "relationship")
_SOFT_WHEN_UNIQUE = {"axis", "position", "dimension"}
_CHILD_CYLINDER_TYPES = {
    "through_hole",
    "blind_hole",
    "counterbore_hole",
    "boss_cylinder",
    "annular_groove",
    "internal_annular_groove",
}
_HOST_CYLINDER_TYPES = {"cylinder_base", "hollow_cylinder"}


def resolve_feature_geometry_evidence(
    plan: FeaturePlanV3,
    measurement: GeometryMeasurementReport,
    *,
    policy: VerificationTolerancePolicy | None = None,
) -> GeometryEvidenceReport:
    """Bind each FeaturePlan feature to report-local BRep candidates.

    Pure: does not mutate ``plan`` or ``measurement`` and does not emit PASS/FAIL.
    """

    policy = policy or VerificationTolerancePolicy()
    candidates = collect_geometry_candidates(measurement)
    features: list[GeometryEvidence] = []
    errors: list[str] = []
    reserved = _reserved_host_cylinder_ids(plan, candidates, policy)
    for feature in _plan_features(plan):
        try:
            features.append(_resolve_one(plan, feature, measurement, candidates, policy, reserved=reserved))
        except Exception as exc:
            errors.append(f"Geometry evidence error for {feature.id}: {exc}")
            signature = compile_feature_signature(feature)
            features.append(
                GeometryEvidence(
                    feature_id=feature.id,
                    feature_type=feature.type,
                    signature=signature,
                    reference=ReferenceContext(feature_id=feature.id, status="UNAVAILABLE", reason=str(exc)),
                    correspondence=CorrespondenceResult(
                        feature_id=feature.id,
                        status="UNAVAILABLE",
                        reason=str(exc),
                    ),
                    notes=[str(exc)],
                )
            )
    _apply_exclusive_assignment(features)
    return GeometryEvidenceReport(
        measurement_version=measurement.measurement_version,
        features=features,
        errors=errors,
    )


def to_geometry_correspondence(result: CorrespondenceResult) -> GeometryCorrespondence:
    """Map 1D-2.1 vocabulary onto the existing 1D-2 correspondence contract."""

    status_map = {
        "MATCHED": "UNIQUE",
        "AMBIGUOUS": "AMBIGUOUS",
        "NOT_FOUND": "NONE",
        "UNAVAILABLE": "UNAVAILABLE",
    }
    indices: list[int] = []
    for candidate_id in result.candidate_ids:
        if candidate_id.startswith("cyl:"):
            try:
                indices.append(int(candidate_id.split(":", 1)[1]))
            except ValueError:
                continue
    return GeometryCorrespondence(
        status=status_map[result.status],  # type: ignore[arg-type]
        candidate_indices=indices,
        reason=result.reason,
    )


def _resolve_one(
    plan: FeaturePlanV3,
    feature: FeatureV3,
    measurement: GeometryMeasurementReport,
    candidates: list[GeometryCandidate],
    policy: VerificationTolerancePolicy,
    *,
    reserved: set[str] | None = None,
) -> GeometryEvidence:
    signature = compile_feature_signature(feature)
    reference = resolve_reference_context(plan, feature, measurement)
    usable = list(candidates)
    notes: list[str] = []
    if feature.type in _CHILD_CYLINDER_TYPES and reserved:
        usable = [item for item in candidates if item.candidate_id not in reserved]
        notes.append("Host cylinder primitives are reserved and cannot bind a child feature.")
    correspondence = correspond_feature(signature, reference, usable, measurement, policy)
    bound = []
    if correspondence.status == "MATCHED":
        selected = set(correspondence.selected_candidate_ids)
        bound = [candidate for candidate in usable if candidate.candidate_id in selected]
    if correspondence.status == "AMBIGUOUS":
        notes.append("AMBIGUOUS correspondence never auto-selects a candidate.")
    if not signature.bindable:
        notes.append("Feature type is not bindable in 1D-2.1.")
    return GeometryEvidence(
        feature_id=feature.id,
        feature_type=feature.type,
        signature=signature,
        reference=reference,
        correspondence=correspondence,
        bound_candidates=bound,
        usable_for_verification=correspondence.status == "MATCHED" and bool(bound),
        notes=notes,
    )


def correspond_feature(
    signature: FeatureGeometrySignature,
    reference: ReferenceContext,
    candidates: Iterable[GeometryCandidate],
    measurement: GeometryMeasurementReport,
    policy: VerificationTolerancePolicy,
) -> CorrespondenceResult:
    all_candidates = list(candidates)
    if not signature.bindable:
        return CorrespondenceResult(
            feature_id=signature.feature_id,
            status="UNAVAILABLE",
            constraint_results=[
                ConstraintResult(
                    constraint=item.kind,
                    status="UNAVAILABLE",
                    property_name=item.property_name,
                    expected=item.expected,
                    reason=item.reason or "Feature type is not bindable in 1D-2.1.",
                )
                for item in signature.constraints
            ],
            reason="Feature type is not bindable in 1D-2.1.",
        )

    universe = [item for item in all_candidates if item.kind == signature.primary_kind]
    universe.sort(key=lambda item: item.candidate_id)
    constraint_results: list[ConstraintResult] = []
    survivors = list(universe)
    evaluable_ran = False

    for kind in _CONSTRAINT_ORDER:
        constraint = _constraint_by_kind(signature, kind)
        if constraint is None:
            continue
        if not constraint.evaluable:
            constraint_results.append(
                ConstraintResult(
                    constraint=kind,  # type: ignore[arg-type]
                    status="UNAVAILABLE",
                    property_name=constraint.property_name,
                    expected=constraint.expected,
                    reason=constraint.reason or f"{kind} is not evaluable from FeaturePlan intent.",
                )
            )
            continue
        result = _apply_constraint(kind, constraint, survivors, all_candidates, reference, measurement, policy)
        constraint_results.append(result)
        if result.status == "UNAVAILABLE":
            continue
        evaluable_ran = True
        # Unique leftovers stay bound so property verifiers can FAIL diameter,
        # axis, or position instead of deleting the identity. Diameter is only
        # kept when the candidate was already located; otherwise a lone leftover
        # cylinder would steal another feature's identity.
        soft = kind in _SOFT_WHEN_UNIQUE and len(survivors) == 1
        if kind == "dimension":
            position = next((item for item in constraint_results if item.constraint == "position"), None)
            soft = soft and position is not None and position.status == "MATCHED"
        if result.status == "NOT_FOUND":
            if not soft:
                survivors = []
        else:
            allowed = set(result.candidate_ids)
            survivors = [item for item in survivors if item.candidate_id in allowed]

    survivor_ids = [item.candidate_id for item in survivors]
    if not evaluable_ran:
        status: CorrespondenceStatus = "UNAVAILABLE"
        selected: list[str] = []
        reason = "No evaluable geometry constraint could be applied."
    elif not survivor_ids:
        status = "NOT_FOUND"
        selected = []
        reason = "Evaluable constraints left no matching geometry candidate."
    elif len(survivor_ids) > 1:
        status = "AMBIGUOUS"
        selected = []
        reason = "Multiple geometry candidates survived evaluable constraints; no candidate was selected."
    else:
        status = "MATCHED"
        selected = [survivor_ids[0]]
        reason = "Exactly one geometry candidate survived evaluable constraints."

    return CorrespondenceResult(
        feature_id=signature.feature_id,
        status=status,
        selected_candidate_ids=selected,
        candidate_ids=survivor_ids,
        constraint_results=constraint_results,
        reason=reason,
    )


def _apply_constraint(
    kind: str,
    constraint: SignatureConstraint,
    survivors: list[GeometryCandidate],
    all_candidates: list[GeometryCandidate],
    reference: ReferenceContext,
    measurement: GeometryMeasurementReport,
    policy: VerificationTolerancePolicy,
) -> ConstraintResult:
    if kind == "type":
        matched = [item.candidate_id for item in survivors if item.status == "MEASUREMENT_SUCCESS"]
        if measurement.status == "MEASUREMENT_UNAVAILABLE" and not matched:
            return ConstraintResult(
                constraint="type",
                status="UNAVAILABLE",
                expected=constraint.expected,
                reason="Measurement is unavailable, so candidate type cannot be searched.",
            )
        if not matched:
            return ConstraintResult(
                constraint="type",
                status="NOT_FOUND",
                expected=constraint.expected,
                observed=[],
                reason="No measured candidate has the expected primitive kind.",
            )
        return ConstraintResult(
            constraint="type",
            status="MATCHED" if len(matched) == 1 else "AMBIGUOUS",
            expected=constraint.expected,
            observed=matched,
            candidate_ids=matched,
            reason="Candidates of the expected primitive kind are present.",
        )

    if kind == "dimension":
        return _dimension_constraint(constraint, survivors, policy)
    if kind == "axis":
        return _axis_constraint(constraint, survivors, policy)
    if kind == "position":
        return _position_constraint(constraint, survivors, reference, policy)
    if kind == "region":
        return _region_constraint(survivors, measurement, policy)
    if kind == "host":
        hosts = [item.candidate_id for item in all_candidates if item.kind == "bounding_region" and item.status == "MEASUREMENT_SUCCESS"]
        if not hosts:
            return ConstraintResult(
                constraint="host",
                status="UNAVAILABLE",
                reason="No host bounding region is available from the measurement report.",
            )
        return ConstraintResult(
            constraint="host",
            status="MATCHED",
            observed=hosts,
            candidate_ids=[item.candidate_id for item in survivors],
            reason="Host bounding region is available.",
        )
    return ConstraintResult(
        constraint="relationship",
        status="UNAVAILABLE",
        reason=constraint.reason or "Relationship constraints are not evaluated in 1D-2.1.",
    )


def _dimension_constraint(
    constraint: SignatureConstraint,
    survivors: list[GeometryCandidate],
    policy: VerificationTolerancePolicy,
) -> ConstraintResult:
    expected = constraint.expected
    if expected is None:
        return ConstraintResult(
            constraint="dimension",
            status="UNAVAILABLE",
            property_name=constraint.property_name,
            reason="No expected dimension was supplied.",
        )
    matched: list[str] = []
    observed: list[Any] = []
    if isinstance(expected, dict):
        for candidate in survivors:
            if candidate.status != "MEASUREMENT_SUCCESS":
                continue
            if all(
                _close(candidate.properties.get(name), value, policy)
                for name, value in expected.items()
                if value is not None
            ):
                matched.append(candidate.candidate_id)
                observed.append({name: candidate.properties.get(name) for name in expected})
    else:
        for candidate in survivors:
            actual = candidate.properties.get(constraint.property_name or "diameter")
            if candidate.status != "MEASUREMENT_SUCCESS":
                continue
            if _close(actual, expected, policy):
                matched.append(candidate.candidate_id)
                observed.append(actual)
    if not matched and not survivors:
        return ConstraintResult(
            constraint="dimension",
            status="UNAVAILABLE",
            property_name=constraint.property_name,
            expected=expected,
            reason="No candidates are available to compare the expected dimension.",
        )
    if not matched:
        if any(item.status != "MEASUREMENT_SUCCESS" for item in survivors):
            return ConstraintResult(
                constraint="dimension",
                status="UNAVAILABLE",
                property_name=constraint.property_name,
                expected=expected,
                reason="Candidate dimension measurement is unavailable or incomplete.",
            )
        return ConstraintResult(
            constraint="dimension",
            status="NOT_FOUND",
            property_name=constraint.property_name,
            expected=expected,
            observed=observed,
            reason="No candidate matches the expected dimension within software tolerance.",
        )
    return ConstraintResult(
        constraint="dimension",
        status="MATCHED" if len(matched) == 1 else "AMBIGUOUS",
        property_name=constraint.property_name,
        expected=expected,
        observed=observed[0] if len(observed) == 1 else observed,
        candidate_ids=matched,
        reason="Candidate dimension matches the expected value." if len(matched) == 1 else "Multiple candidates match the expected dimension.",
    )


def _axis_constraint(
    constraint: SignatureConstraint,
    survivors: list[GeometryCandidate],
    policy: VerificationTolerancePolicy,
) -> ConstraintResult:
    if not survivors:
        return ConstraintResult(
            constraint="axis",
            status="UNAVAILABLE",
            expected=constraint.expected,
            reason="No candidates are available to compare the expected axis.",
        )
    expected = constraint.expected
    if not isinstance(expected, list):
        return ConstraintResult(constraint="axis", status="UNAVAILABLE", expected=expected, reason="No expected axis vector is available.")
    matched: list[str] = []
    observed: list[Any] = []
    unavailable = False
    for candidate in survivors:
        actual = candidate.properties.get("axis")
        if actual is None:
            unavailable = True
            continue
        if _axes_close(expected, actual, policy):
            matched.append(candidate.candidate_id)
            observed.append(actual)
    if not matched and unavailable and all(item.properties.get("axis") is None for item in survivors):
        return ConstraintResult(
            constraint="axis",
            status="UNAVAILABLE",
            expected=expected,
            reason="Candidate axis measurement is unavailable.",
        )
    if not matched:
        return ConstraintResult(
            constraint="axis",
            status="NOT_FOUND",
            expected=expected,
            reason="No candidate axis matches the expected FeaturePlan axis.",
        )
    return ConstraintResult(
        constraint="axis",
        status="MATCHED" if len(matched) == 1 else "AMBIGUOUS",
        expected=expected,
        observed=observed[0] if len(observed) == 1 else observed,
        candidate_ids=matched,
        reason="Candidate axis matches the expected FeaturePlan axis.",
    )


def _position_constraint(
    constraint: SignatureConstraint,
    survivors: list[GeometryCandidate],
    reference: ReferenceContext,
    policy: VerificationTolerancePolicy,
) -> ConstraintResult:
    if not survivors:
        return ConstraintResult(
            constraint="position",
            status="UNAVAILABLE",
            reason="No candidates are available to compare position.",
        )
    if isinstance(constraint.expected, dict) and "z_start" in constraint.expected:
        return _axial_position_constraint(constraint, survivors, policy)
    expected = constraint.expected if isinstance(constraint.expected, list) else reference.resolved_position
    if expected is None or not isinstance(expected, list) or len(expected) < 2:
        return ConstraintResult(
            constraint="position",
            status="UNAVAILABLE",
            reason="No executable PlacementV3 X/Y is available.",
        )
    host_plane = _position_plane(survivors, reference)
    if host_plane is None:
        return ConstraintResult(
            constraint="position",
            status="UNAVAILABLE",
            expected=expected[:2],
            reason="Host reference plane is unavailable, so axis intersection cannot be computed.",
        )
    matched: list[str] = []
    observed: list[Any] = []
    unavailable = False
    for candidate in survivors:
        point = _axis_plane_intersection(
            candidate.properties.get("axis_point"),
            candidate.properties.get("axis"),
            host_plane["origin"],
            host_plane["normal"],
        )
        if point is None:
            unavailable = True
            continue
        if _close(point[0], expected[0], policy) and _close(point[1], expected[1], policy):
            matched.append(candidate.candidate_id)
            observed.append(point[:2])
    if not matched and unavailable and not observed:
        return ConstraintResult(
            constraint="position",
            status="UNAVAILABLE",
            expected=expected[:2],
            reason="Cylinder axis/plane intersection could not be computed from measured facts.",
        )
    if not matched:
        return ConstraintResult(
            constraint="position",
            status="NOT_FOUND",
            expected=expected[:2],
            observed=observed,
            reason="No candidate axis intersects the host plane at the expected PlacementV3 XY.",
        )
    return ConstraintResult(
        constraint="position",
        status="MATCHED" if len(matched) == 1 else "AMBIGUOUS",
        expected=expected[:2],
        observed=observed[0] if len(observed) == 1 else observed,
        candidate_ids=matched,
        reason="Candidate position is the axis intersection with the host plane, not CylinderFact.center.",
    )


def _axial_position_constraint(
    constraint: SignatureConstraint,
    survivors: list[GeometryCandidate],
    policy: VerificationTolerancePolicy,
) -> ConstraintResult:
    expected = constraint.expected if isinstance(constraint.expected, dict) else {}
    z_start = expected.get("z_start")
    width = expected.get("width")
    if z_start is None:
        return ConstraintResult(
            constraint="position",
            status="UNAVAILABLE",
            property_name="z_start",
            reason="No executable axial z_start is available.",
        )
    matched: list[str] = []
    observed: list[Any] = []
    unavailable = False
    for candidate in survivors:
        point = candidate.properties.get("axis_point")
        height = candidate.properties.get("height")
        if not isinstance(point, list) or len(point) < 3:
            unavailable = True
            continue
        z_loc = float(point[2])
        if _close(z_loc, z_start, policy):
            matched.append(candidate.candidate_id)
            observed.append({"z": z_loc, "height": height})
            continue
        if height is not None:
            lo = min(z_loc, z_loc + float(height))
            hi = max(z_loc, z_loc + float(height))
            expected_hi = z_start + float(width or 0.0)
            overlap = min(hi, max(z_start, expected_hi)) - max(lo, min(z_start, expected_hi))
            if overlap + policy.linear_absolute_mm >= min(float(width or height), float(height)) * 0.5:
                matched.append(candidate.candidate_id)
                observed.append({"z": z_loc, "height": height, "overlap": overlap})
    if not matched and unavailable and not observed:
        return ConstraintResult(
            constraint="position",
            status="UNAVAILABLE",
            property_name="z_start",
            expected=z_start,
            reason="Candidate axis location is unavailable for axial comparison.",
        )
    if not matched:
        return ConstraintResult(
            constraint="position",
            status="NOT_FOUND",
            property_name="z_start",
            expected=z_start,
            observed=observed,
            reason="No candidate occupies the expected axial groove interval.",
        )
    return ConstraintResult(
        constraint="position",
        status="MATCHED" if len(matched) == 1 else "AMBIGUOUS",
        property_name="z_start",
        expected=z_start,
        observed=observed[0] if len(observed) == 1 else observed,
        candidate_ids=matched,
        reason="Candidate axial location matches groove z_start / occupancy.",
    )


def _region_constraint(
    survivors: list[GeometryCandidate],
    measurement: GeometryMeasurementReport,
    policy: VerificationTolerancePolicy,
) -> ConstraintResult:
    if not survivors:
        return ConstraintResult(constraint="region", status="UNAVAILABLE", reason="No candidates are available to compare region.")
    bounds = host_aabb(measurement)
    if bounds is None:
        return ConstraintResult(constraint="region", status="UNAVAILABLE", reason="Host AABB is unavailable.")
    pad = max(policy.linear_absolute_mm, 0.0)
    matched: list[str] = []
    for candidate in survivors:
        point = candidate.properties.get("axis_point")
        if candidate.kind == "bounding_region":
            matched.append(candidate.candidate_id)
            continue
        if not isinstance(point, list) or len(point) < 2:
            continue
        if (
            bounds["min_x"] - pad <= point[0] <= bounds["max_x"] + pad
            and bounds["min_y"] - pad <= point[1] <= bounds["max_y"] + pad
        ):
            matched.append(candidate.candidate_id)
    if not matched:
        return ConstraintResult(
            constraint="region",
            status="NOT_FOUND",
            reason="No candidate lies inside the host bounding region.",
        )
    return ConstraintResult(
        constraint="region",
        status="MATCHED" if len(matched) == 1 else "AMBIGUOUS",
        candidate_ids=matched,
        reason="Candidate lies inside the host AABB.",
    )


def _position_plane(survivors: list[GeometryCandidate], reference: ReferenceContext) -> dict[str, list[float]] | None:
    expected_axis = None
    for candidate in survivors:
        if isinstance(candidate.properties.get("axis"), list):
            expected_axis = candidate.properties["axis"]
            break
    name = "top"
    if expected_axis is not None:
        vector = _normalize(expected_axis)
        if vector is not None:
            abs_axis = [abs(value) for value in vector]
            dominant = abs_axis.index(max(abs_axis))
            name = ("right", "back", "top")[dominant]
    frame = frame_by_name(reference, name)
    if frame is None or frame.normal is None:
        return None
    return {"origin": frame.origin, "normal": frame.normal}


def _axis_plane_intersection(
    point: Any,
    direction: Any,
    plane_point: list[float],
    plane_normal: list[float],
) -> list[float] | None:
    if not isinstance(point, list) or not isinstance(direction, list) or len(point) < 3 or len(direction) < 3:
        return None
    axis_point = [float(value) for value in point[:3]]
    axis_dir = _normalize([float(value) for value in direction[:3]])
    normal = _normalize(plane_normal)
    if axis_dir is None or normal is None:
        return None
    denom = _dot(axis_dir, normal)
    if abs(denom) < 1e-9:
        return None
    offset = [plane_point[index] - axis_point[index] for index in range(3)]
    scale = _dot(offset, normal) / denom
    return [axis_point[index] + axis_dir[index] * scale for index in range(3)]


def _constraint_by_kind(signature: FeatureGeometrySignature, kind: str) -> SignatureConstraint | None:
    for item in signature.constraints:
        if item.kind == kind:
            return item
    return None


def _plan_features(plan: FeaturePlanV3) -> list[FeatureV3]:
    features: list[FeatureV3] = []
    if plan.base_feature is not None:
        features.append(plan.base_feature)
    features.extend(plan.features)
    return features


def _feature_dimension(feature: FeatureV3, name: str) -> float | None:
    dimension = feature.dimensions.get(name)
    if dimension is None or dimension.value is None:
        return None
    try:
        return float(dimension.value)
    except (TypeError, ValueError):
        return None


def _reserved_host_cylinder_ids(
    plan: FeaturePlanV3,
    candidates: list[GeometryCandidate],
    policy: VerificationTolerancePolicy,
) -> set[str]:
    base = plan.base_feature
    if base is None or base.type not in _HOST_CYLINDER_TYPES:
        return set()
    reserved: set[str] = set()
    expected = [_feature_dimension(base, "outer_diameter")]
    if base.type == "hollow_cylinder":
        expected.append(_feature_dimension(base, "inner_diameter"))
    for candidate in candidates:
        if candidate.kind != "cylinder":
            continue
        diameter = candidate.properties.get("diameter")
        if any(value is not None and _close(diameter, value, policy) for value in expected):
            reserved.add(candidate.candidate_id)
    return reserved


def _apply_exclusive_assignment(features: list[GeometryEvidence]) -> None:
    owners: dict[str, list[GeometryEvidence]] = {}
    for item in features:
        if item.correspondence.status != "MATCHED":
            continue
        for candidate_id in item.correspondence.selected_candidate_ids:
            owners.setdefault(candidate_id, []).append(item)
    contended = {candidate_id: group for candidate_id, group in owners.items() if len(group) > 1}
    if not contended:
        return
    seen: set[int] = set()
    for group in contended.values():
        for item in group:
            marker = id(item)
            if marker in seen:
                continue
            seen.add(marker)
            item.correspondence.status = "AMBIGUOUS"
            item.correspondence.selected_candidate_ids = []
            item.correspondence.reason = (
                "Multiple FeaturePlan features matched the same geometry candidate; no candidate was selected."
            )
            item.bound_candidates = []
            item.usable_for_verification = False
            item.notes.append("Exclusive assignment: one BRep candidate cannot verify two features.")


def _close(actual: Any, expected: Any, policy: VerificationTolerancePolicy) -> bool:
    try:
        left = float(actual)
        right = float(expected)
    except (TypeError, ValueError):
        return False
    if not isfinite(left) or not isfinite(right):
        return False
    return abs(left - right) <= max(policy.linear_absolute_mm, abs(right) * policy.linear_relative)


def _axes_close(expected: list[float], actual: list[float], policy: VerificationTolerancePolicy) -> bool:
    left = _normalize(expected)
    right = _normalize(actual)
    if left is None or right is None:
        return False
    dot = _dot(left, right)
    if policy.axial_axis_equivalence:
        dot = abs(dot)
    dot = min(1.0, max(-1.0, dot))
    return degrees(acos(dot)) <= policy.angular_degrees


def _normalize(values: list[float]) -> list[float] | None:
    length = sqrt(sum(value * value for value in values))
    if length <= 1e-12:
        return None
    return [value / length for value in values]


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))
