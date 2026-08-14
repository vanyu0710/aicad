from __future__ import annotations

"""Deterministic evidence conflict gate.

The gate is a pure function: it reads FeaturePlanV3 evidence and returns an
EvidenceGateResult without mutating the plan. Explicit orchestration helpers
write gate results or user resolutions back into plan bookkeeping.
"""

from copy import deepcopy
from typing import Any

from backend.feature_definitions import get_feature_definition
from backend.normalization import normalize_feature_plan
from backend.schemas import (
    DimensionV3,
    EvidenceConflict,
    EvidenceConflictSource,
    EvidenceGateResult,
    EvidenceItem,
    EvidenceResolution,
    EvidenceSet,
    FeaturePlanV3,
    FeatureV3,
    now_iso,
)

_CONFLICT_TOLERANCE_MM = 0.01

_DIMENSION_ALIASES: dict[str, set[str]] = {
    "outer_diameter": {"od", "outside_diameter"},
    "inner_diameter": {"id", "bore"},
    "length": {"overall_length", "total_length"},
    "width": {"width"},
    "height": {"thickness", "thick"},
    "depth": {"hole_depth", "slot_depth", "groove_depth"},
    "diameter": {"hole_diameter", "metric_thread", "bolt_hole_diameter", "screw_hole_diameter", "center_hole_diameter"},
    "axial_width": {"groove_width", "slot_width", "groove_axial_width"},
    "reduced_outer_diameter": {"groove_diameter", "groove_bottom_diameter"},
    "z_start": {"groove_distance", "distance_from_end", "distance_from_face"},
    "count": {"pattern_count", "hole_count", "bolt_hole_count"},
    "pitch_radius": {"pitch_circle_diameter", "bolt_circle_radius"},
    "spacing": {"pitch", "pitch_distance"},
    "end_diameter_1": {"end_1_diameter", "first_end_diameter"},
    "end_diameter_2": {"end_2_diameter", "second_end_diameter"},
}


def _numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _all_features(plan: FeaturePlanV3) -> list[FeatureV3]:
    return ([plan.base_feature] if plan.base_feature else []) + list(plan.features)


def _candidate_dimension_names(key: str) -> set[str]:
    candidates = {key}
    for dimension, aliases in _DIMENSION_ALIASES.items():
        if key in aliases or key == dimension:
            candidates.add(dimension)
    return candidates


def _keys_alias_equivalent(left: str, right: str) -> bool:
    if left == right:
        return True
    left_candidates = _candidate_dimension_names(left)
    right_candidates = _candidate_dimension_names(right)
    if left_candidates.intersection(right_candidates):
        return True
    if right in _DIMENSION_ALIASES.get(left, set()) or left in _DIMENSION_ALIASES.get(right, set()):
        return True
    return False


def _source_value(item: EvidenceItem) -> tuple[Any, str]:
    return (item.source, str(item.value), item.unit)


def _find_existing_conflict(
    details: list[EvidenceConflict], key: str, left: EvidenceItem, right: EvidenceItem
) -> EvidenceConflict | None:
    left_key = _source_value(left)
    right_key = _source_value(right)
    for conflict in details:
        if conflict.key != key:
            continue
        a = (conflict.source_a.source, str(conflict.source_a.value), conflict.source_a.unit)
        b = (conflict.source_b.source, str(conflict.source_b.value), conflict.source_b.unit)
        if {a, b} == {left_key, right_key}:
            return conflict
    return None


def _derive_conflicts(evidence: EvidenceSet) -> list[EvidenceConflict]:
    by_key: dict[str, list[EvidenceItem]] = {}
    for item in evidence.items:
        by_key.setdefault(item.key, []).append(item)
    conflicts: list[EvidenceConflict] = []
    index = 0
    for key, items in by_key.items():
        if len(items) < 2:
            continue
        first = items[0]
        for other in items[1:]:
            left_value = _numeric(first.value)
            right_value = _numeric(other.value)
            if left_value is None or right_value is None:
                continue
            if abs(left_value - right_value) <= _CONFLICT_TOLERANCE_MM:
                continue
            source_a = _conflict_source(first)
            source_b = _conflict_source(other)
            existing = _find_existing_conflict(evidence.conflict_details, key, first, other)
            if existing is not None:
                conflict = existing.model_copy(
                    update={
                        "key": key,
                        "feature_id": existing.feature_id or first.feature_id or other.feature_id,
                        "parameter": existing.parameter or first.dimension or other.dimension,
                        "source_a": source_a,
                        "source_b": source_b,
                    }
                )
            else:
                conflict = EvidenceConflict(
                    id=f"evidence_conflict_{index}",
                    key=key,
                    feature_id=first.feature_id or other.feature_id,
                    parameter=first.dimension or other.dimension,
                    source_a=source_a,
                    source_b=source_b,
                    reason=f"{key} numeric disagreement between {first.source} and {other.source}",
                )
            conflicts.append(conflict)
            index += 1
    return conflicts


def _conflict_source(item: EvidenceItem) -> EvidenceConflictSource:
    label = {
        "user": "text/user description",
        "drawing": "drawing/vision",
        "assumption": "assumed value",
        "derived": "derived value",
    }.get(item.source, item.source)
    return EvidenceConflictSource(
        source=item.source,
        value=item.value,
        unit=item.unit,
        confirmed_by_user=item.confirmed_by_user,
        detail=label,
    )


def _feature_matches(plan: FeaturePlanV3, feature: FeatureV3, conflict: EvidenceConflict) -> list[str]:
    """Return executable dimension names on this feature bound to the conflict."""
    dims = set(feature.dimensions)
    if conflict.feature_id and feature.id != conflict.feature_id:
        return []
    if conflict.parameter:
        if conflict.parameter in dims:
            return [conflict.parameter]
        candidates = _candidate_dimension_names(conflict.parameter)
        hits = sorted(candidates.intersection(dims))
        if hits:
            return hits
    if conflict.key in dims:
        return [conflict.key]
    hits = sorted(_candidate_dimension_names(conflict.key).intersection(dims))
    if hits:
        return hits
    definition = get_feature_definition(feature.type)
    if definition:
        for parameter in definition.parameters:
            if conflict.key == parameter.name or conflict.key in parameter.aliases:
                if parameter.name in dims:
                    return [parameter.name]
    return []


def _match_conflict_to_plan(plan: FeaturePlanV3, conflict: EvidenceConflict) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    for feature in _all_features(plan):
        for dimension in _feature_matches(plan, feature, conflict):
            matches.append((feature.id, dimension))
    return matches


def evaluate_evidence_gate(plan: FeaturePlanV3, mode: str = "strict") -> EvidenceGateResult:
    """Pure, deterministic evidence gate. Never mutates the plan."""
    result = EvidenceGateResult(status="ALLOW", blocking=False)
    conflicts = _derive_conflicts(plan.evidence)
    material: list[EvidenceConflict] = []
    affected_ids: set[str] = set()
    affected_parameters: set[str] = set()

    for conflict in conflicts:
        matches = _match_conflict_to_plan(plan, conflict)
        if not matches:
            warning = conflict.model_copy(update={"severity": "warning", "affected_feature_ids": []})
            result.conflicts.append(warning)
            result.warnings.append(
                f"{conflict.key} evidence conflict does not affect current FeaturePlan geometry"
            )
            continue
        feature_ids = sorted({feature_id for feature_id, _ in matches})
        parameters = sorted({parameter for _, parameter in matches})
        enriched = conflict.model_copy(
            update={
                "feature_id": conflict.feature_id or feature_ids[0],
                "parameter": conflict.parameter or parameters[0],
                "affected_feature_ids": feature_ids,
                "ambiguous": len(feature_ids) > 1,
                "severity": "blocking",
            }
        )
        result.conflicts.append(enriched)
        if enriched.status == "resolved":
            continue
        material.append(enriched)
        affected_ids.update(feature_ids)
        affected_parameters.update(parameters)

    if material:
        result.blocking = True
        result.resolution_required = True
        result.status = "BLOCK" if mode == "strict" else "REQUIRE_RESOLUTION"
        result.affected_feature_ids = sorted(affected_ids)
        result.affected_parameters = sorted(affected_parameters)
        result.reason = (
            f"Material evidence conflicts require user resolution before CAD: "
            f"{', '.join(conflict.key for conflict in material)}"
        )
    else:
        result.reason = "No unresolved material evidence conflict"
    return result


def apply_evidence_gate_result(
    plan: FeaturePlanV3, result: EvidenceGateResult, mode: str = "strict"
) -> FeaturePlanV3:
    """Explicitly persist a gate result into plan bookkeeping."""
    plan.self_checks["evidence_gate"] = {
        "status": result.status,
        "blocking": result.blocking,
        "reason": result.reason,
        "conflicts": [conflict.model_dump() for conflict in result.conflicts],
    }
    for conflict in result.conflicts:
        _upsert_conflict_detail(plan.evidence, conflict)

    review = plan.design_review
    review.blocking = [item for item in review.blocking if not item.startswith("[evidence]")]
    review.warnings = [item for item in review.warnings if not item.startswith("[evidence]")]
    if result.blocking:
        review.blocking.append(f"[evidence] {result.reason}")
    else:
        review.warnings.extend(f"[evidence] {warning}" for warning in result.warnings)
    return plan


def _upsert_conflict_detail(evidence: EvidenceSet, conflict: EvidenceConflict) -> None:
    for index, existing in enumerate(evidence.conflict_details):
        if existing.id == conflict.id:
            evidence.conflict_details[index] = conflict
            return
    evidence.conflict_details.append(conflict)


def apply_evidence_resolutions(
    plan: FeaturePlanV3, resolutions: list[EvidenceResolution], language: str = "zh"
) -> FeaturePlanV3:
    """Apply explicit user resolutions without deleting original evidence."""
    updated = deepcopy(plan)
    conflicts = _derive_conflicts(updated.evidence)
    for resolution in resolutions:
        selected = _numeric(resolution.selected_value)
        if selected is None:
            continue
        targets = [
            conflict
            for conflict in conflicts
            if conflict.status == "unresolved"
            and (
                (resolution.conflict_id and conflict.id == resolution.conflict_id)
                or (
                    not resolution.conflict_id
                    and resolution.key
                    and _keys_alias_equivalent(conflict.key, resolution.key)
                    and (not resolution.feature_id or conflict.feature_id == resolution.feature_id)
                )
            )
        ]
        for conflict in targets:
            matches = _match_conflict_to_plan(updated, conflict)
            feature_ids = sorted({feature_id for feature_id, _ in matches})
            if resolution.feature_id:
                if resolution.feature_id not in feature_ids:
                    continue
                feature_ids = [resolution.feature_id]
            elif len(feature_ids) > 1:
                continue
            if not feature_ids:
                continue
            conflict.status = "resolved"
            conflict.resolved_value = selected
            conflict.resolved_by = "user"
            conflict.resolved_at = now_iso()
            conflict.affected_feature_ids = feature_ids
            conflict.ambiguous = len(feature_ids) > 1
            _upsert_conflict_detail(updated.evidence, conflict)
            for feature_id in feature_ids:
                feature = next((item for item in _all_features(updated) if item.id == feature_id), None)
                if feature is None:
                    continue
                parameter = _parameter_for_feature(feature, conflict)
                if parameter is None:
                    continue
                evidence_text = (
                    f"User resolved evidence conflict {conflict.key} with {selected}mm"
                    if language == "en"
                    else f"用户已裁决证据冲突 {conflict.key}，采用 {selected}mm"
                )
                feature.dimensions[parameter] = DimensionV3(
                    value=selected,
                    unit=resolution.unit or "mm",
                    evidence=evidence_text,
                    source="user",
                    confirmed_by_user=True,
                )
                _clear_feature_unresolved(updated, feature.id, conflict.key, parameter)
    return normalize_feature_plan(updated)


def _parameter_for_feature(feature: FeatureV3, conflict: EvidenceConflict) -> str | None:
    if conflict.parameter and conflict.parameter in feature.dimensions:
        return conflict.parameter
    for name in sorted(_candidate_dimension_names(conflict.key)):
        if name in feature.dimensions:
            return name
    if conflict.key in feature.dimensions:
        return conflict.key
    return None


def _clear_feature_unresolved(plan: FeaturePlanV3, feature_id: str, key: str, parameter: str) -> None:
    feature = next((item for item in _all_features(plan) if item.id == feature_id), None)
    if feature is None:
        return
    feature.unresolved = [
        item
        for item in feature.unresolved
        if key not in item and parameter not in item
    ]
    plan.unresolved = [
        item
        for item in plan.unresolved
        if item.get("feature") != feature_id or (key not in str(item.get("reason")) and parameter not in str(item.get("reason")))
    ]
