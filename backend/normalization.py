from __future__ import annotations

"""Explicit, idempotent normalization for FeaturePlanV3.

Normalization is separate from validation. Callers that produce or load a plan
invoke normalize_feature_plan() before validation or CAD execution; validation
itself never writes back into the plan.
"""

from backend.schemas import DesignAssumption, FeaturePlanV3


def _append_plan_unresolved(plan: FeaturePlanV3, feature_id: str, reason: str) -> None:
    if not any(item.get("feature") == feature_id and item.get("reason") == reason for item in plan.unresolved):
        plan.unresolved.append({"feature": feature_id, "reason": reason})


def _append_feature_unresolved(feature, message: str) -> None:
    if message not in feature.unresolved:
        feature.unresolved.append(message)


def normalize_feature_plan(plan: FeaturePlanV3) -> FeaturePlanV3:
    """Normalize engineering bookkeeping without geometric validation.

    The function mutates the supplied plan and returns it so callers can treat
    it as an explicit pipeline step. Repeated calls do not duplicate state.
    """
    ordered_features = [plan.base_feature] if plan.base_feature else []
    ordered_features.extend(plan.features)
    seen_ids: set[str] = set()

    for index, feature in enumerate(ordered_features):
        if feature is None:
            continue
        if not feature.id:
            feature.id = "base" if index == 0 else f"feature_{index}"
        if feature.id in seen_ids:
            _append_plan_unresolved(plan, feature.id, "Duplicate feature id")
        seen_ids.add(feature.id)

    for feature in ordered_features:
        if feature is None:
            continue
        for dep in feature.depends_on:
            if dep and dep not in seen_ids:
                _append_plan_unresolved(plan, feature.id, f"Missing dependency: {dep}")
        for name, dimension in feature.dimensions.items():
            if dimension.value is not None and dimension.value <= 0 and name not in {"x", "y", "z"}:
                _append_feature_unresolved(feature, f"Invalid non-positive dimension: {name}")
            if dimension.source == "assumption" and not dimension.confirmed_by_user:
                plan.design_review.requires_confirmation = True
                evidence = dimension.evidence or f"{feature.id}.{name}"
                if evidence not in plan.assumptions:
                    plan.assumptions.append(evidence)
                if not any(
                    item.feature_id == feature.id and item.dimension == name
                    for item in plan.assumption_details
                ):
                    plan.assumption_details.append(
                        DesignAssumption(
                            feature_id=feature.id,
                            dimension=name,
                            value=dimension.value,
                            reason=dimension.evidence or "AI inferred dimension",
                            confidence=dimension.confidence,
                        )
                    )
    return plan
