from __future__ import annotations

from typing import Any

from backend.schemas import DimensionV3 as Dimension
from backend.schemas import FeaturePlanV3 as FeaturePlan


def validate_feature_plan(raw_plan: dict[str, Any]):
    try:
        return FeaturePlan.model_validate(raw_plan), []
    except Exception as exc:  # pragma: no cover - compatibility shim
        return None, [str(exc)]


def dimension_value(dimensions: dict[str, Any], *names: str):
    for name in names:
        dim = dimensions.get(name)
        if dim is None:
            continue
        value = getattr(dim, "value", None) if not isinstance(dim, dict) else dim.get("value")
        if value is not None:
            return float(value)
    return None


def dimension_evidence(dimensions: dict[str, Any], *names: str) -> str:
    for name in names:
        dim = dimensions.get(name)
        if dim is None:
            continue
        evidence = getattr(dim, "evidence", None) if not isinstance(dim, dict) else dim.get("evidence")
        if evidence:
            return str(evidence)
    return ""

