from __future__ import annotations

"""Normalize free-form planner model output into a strict FeaturePlanV3.

Planner models often return engineer-style JSON: bare numeric dimensions, alternate
key names (diameter/radius/height), nested placement objects and object-array
unresolved items. This module maps those into the exact shape the Pydantic schema
and the CAD worker expect, without inventing values. Anything missing stays in
``unresolved`` so the user is asked instead of the model guessing.
"""

from typing import Any

# Base feature type -> supported dimension key aliases.
_BASE_KEY_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "box_base": {
        "length": ("length",),
        "width": ("width",),
        "height": ("height", "thickness"),
    },
    "cylinder_base": {
        "outer_diameter": ("outer_diameter", "diameter"),
        "length": ("length", "height", "thickness"),
    },
    "hollow_cylinder": {
        "outer_diameter": ("outer_diameter", "diameter"),
        "inner_diameter": ("inner_diameter", "bore_diameter", "id"),
        "length": ("length", "height", "thickness"),
    },
}

# Feature type -> supported dimension key aliases.
_FEATURE_KEY_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "through_hole": {"diameter": ("diameter", "hole_diameter", "actual_cut_diameter")},
    "blind_hole": {"diameter": ("diameter", "hole_diameter", "actual_cut_diameter"), "depth": ("depth",)},
    "counterbore_hole": {
        "diameter": ("diameter", "hole_diameter", "actual_cut_diameter"),
        "depth": ("depth",),
    },
    "rectangular_slot": {"length": ("length", "slot_length"), "width": ("width", "slot_width"), "depth": ("depth", "height")},
    "rectangular_pocket": {"length": ("length", "slot_length"), "width": ("width", "slot_width"), "depth": ("depth", "height")},
    "annular_groove": {
        "reduced_outer_diameter": ("reduced_outer_diameter", "groove_diameter", "diameter"),
        "axial_width": ("axial_width", "width"),
        "z_start": ("z_start", "axial_start_from_bottom"),
    },
    "boss_cylinder": {"diameter": ("diameter", "outer_diameter"), "height": ("height", "length")},
    "rectangular_pad": {"length": ("length",), "width": ("width",), "height": ("height", "depth")},
    "rib_box": {"length": ("length",), "width": ("width",), "height": ("height", "depth")},
    "linear_pattern": {"count": ("count", "instance_count"), "spacing": ("spacing", "pitch", "spacing_deg"), "diameter": ("diameter", "hole_diameter")},
    "circular_pattern": {"count": ("count", "instance_count"), "pitch_radius": ("pitch_radius", "bolt_circle_radius", "radius"), "diameter": ("diameter", "hole_diameter")},
}

_OPERATION_MAP = {"add": "add", "cut": "remove", "remove": "remove", "subtract": "remove", "pattern": "pattern", "base": "base"}


def normalize_ai_plan(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a planner model JSON object into a strict FeaturePlanV3-compatible dict."""
    if not isinstance(raw, dict):
        raise ValueError("planner output is not an object")

    part_family = raw.get("part_family")
    if isinstance(part_family, dict):
        part_family = part_family.get("category") or part_family.get("part_family") or "unknown"
    if not isinstance(part_family, str):
        part_family = "unknown"

    base_raw = raw.get("base_feature")
    base = _normalize_base(base_raw) if isinstance(base_raw, dict) else None

    features: list[dict[str, Any]] = []
    for item in raw.get("features") or []:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_feature(item)
        if normalized is not None:
            features.append(normalized)

    plan: dict[str, Any] = {
        "schema_version": "3.0",
        "units": "mm",
        "part_family": part_family,
        "base_feature": base,
        "features": features,
        "assumptions": _normalize_strings(raw.get("assumptions")),
        "unresolved": _normalize_unresolved(raw.get("unresolved"), features),
        "self_checks": raw.get("self_checks") if isinstance(raw.get("self_checks"), dict) else {},
    }
    review = raw.get("design_review")
    if isinstance(review, dict):
        review = dict(review)
        confirmation = review.get("requires_confirmation")
        if not isinstance(confirmation, bool):
            review["requires_confirmation"] = bool(confirmation)
        for key in ("warnings", "suggestions", "manufacturability", "standards"):
            value = review.get(key)
            if not isinstance(value, list):
                review[key] = [str(value)] if value else []
        plan["design_review"] = review
    return plan


def _normalize_base(raw: dict[str, Any]) -> dict[str, Any] | None:
    feature_type = str(raw.get("type") or "box_base")
    if feature_type not in _BASE_KEY_ALIASES:
        # Fall back to a box when the model invented an unsupported base kind.
        feature_type = "box_base"
    dimensions: dict[str, Any] = {}
    for target, aliases in _BASE_KEY_ALIASES[feature_type].items():
        value = _pick_value(raw.get("dimensions"), aliases)
        if value is not None:
            dimensions[target] = _to_dimension(value, raw.get("evidence"))
    if "outer_diameter" not in dimensions and feature_type in {"cylinder_base", "hollow_cylinder"}:
        radius = _pick_value(raw.get("dimensions"), ("radius",))
        if radius is not None:
            dimensions["outer_diameter"] = _to_dimension(radius * 2.0, raw.get("evidence"))
    if feature_type == "box_base" and "height" not in dimensions:
        thickness = _pick_value(raw.get("dimensions"), ("thickness", "depth"))
        if thickness is not None:
            dimensions["height"] = _to_dimension(thickness, raw.get("evidence"))
    return {
        "id": str(raw.get("id") or "base"),
        "type": feature_type,
        "operation": "base",
        "dimensions": dimensions,
        "placement": _normalize_placement(raw.get("placement")),
        "extent": _normalize_extent(raw.get("extent")),
        "depends_on": _normalize_strings(raw.get("depends_on")),
        "evidence": _normalize_evidence(raw.get("evidence")),
        "unresolved": _normalize_strings(raw.get("unresolved")),
        "assumptions": _normalize_strings(raw.get("assumptions")),
        "confirmed_by_user": bool(raw.get("confirmed_by_user", False)),
    }


def _normalize_feature(raw: dict[str, Any]) -> dict[str, Any] | None:
    feature_type = str(raw.get("type") or "").strip()
    if feature_type not in _FEATURE_KEY_ALIASES:
        return None

    dimensions: dict[str, Any] = {}
    for target, aliases in _FEATURE_KEY_ALIASES[feature_type].items():
        value = _pick_value(raw.get("dimensions"), aliases)
        if value is not None:
            dimensions[target] = _to_dimension(value, raw.get("evidence"))

    # Pitch circle from placement when the pattern dimensions omit the radius.
    if feature_type == "circular_pattern" and "pitch_radius" not in dimensions:
        radius = _placement_radius(raw.get("placement"))
        if radius is not None:
            dimensions["pitch_radius"] = _to_dimension(radius, raw.get("evidence"))

    operation = _OPERATION_MAP.get(str(raw.get("operation") or "").lower(), "add")
    return {
        "id": str(raw.get("id") or f"feature_{hash(feature_type) & 0xFFFF}"),
        "type": feature_type,
        "operation": operation,
        "dimensions": dimensions,
        "placement": _normalize_placement(raw.get("placement")),
        "extent": _normalize_extent(raw.get("extent")),
        "depends_on": _normalize_strings(raw.get("depends_on")) or ["base"],
        "evidence": _normalize_evidence(raw.get("evidence")),
        "unresolved": _normalize_strings(raw.get("unresolved")),
        "assumptions": _normalize_strings(raw.get("assumptions")),
        "confirmed_by_user": bool(raw.get("confirmed_by_user", False)),
    }


def _normalize_placement(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"reference": "origin", "x": None, "y": None, "z": None, "axis": "Z"}
    center = raw.get("center")
    x = _number(center.get("x") if isinstance(center, dict) else None) or _number(raw.get("x"))
    y = _number(center.get("y") if isinstance(center, dict) else None) or _number(raw.get("y"))
    z = _number(raw.get("z")) or _number(raw.get("z_start"))
    axis = str(raw.get("axis") or "Z").upper()
    if axis not in {"X", "Y", "Z"}:
        axis = "Z"
    return {
        "reference": str(raw.get("reference") or raw.get("parent") or "origin"),
        "x": x,
        "y": y,
        "z": z,
        "axis": axis,
    }


def _normalize_extent(raw: Any) -> str | None:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        extent_type = str(raw.get("type") or "")
        if "through" in extent_type:
            return "through"
        if extent_type == "blind":
            return "blind"
        return extent_type or None
    return None


def _placement_radius(raw: Any) -> float | None:
    if not isinstance(raw, dict):
        return None
    radius = _number(raw.get("pitch_circle_radius")) or _number(raw.get("radius"))
    if radius is not None:
        return radius
    diameter = _number(raw.get("pitch_circle_diameter")) or _number(raw.get("diameter"))
    if diameter is not None:
        return diameter / 2.0
    return None


def _pick_value(dimensions: Any, aliases: tuple[str, ...]) -> float | None:
    if not isinstance(dimensions, dict):
        return None
    for alias in aliases:
        raw = dimensions.get(alias)
        if raw is None:
            continue
        value = _number(raw)
        if value is not None:
            return value
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, dict):
        for key in ("value", "numeric_value"):
            if key in value:
                return _number(value[key])
    return None


def _to_dimension(value: float, evidence: Any) -> dict[str, Any]:
    return {
        "value": value,
        "unit": "mm",
        "evidence": _normalize_evidence(evidence) or "model output",
        "source": "assumption",
        "confirmed_by_user": False,
    }


def _normalize_evidence(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts = []
        for item in raw:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("value") or item.get("dimension") or item.get("source") or ""
                parts.append(str(value))
        return " | ".join(part for part in parts if part)
    if isinstance(raw, dict):
        return str(raw.get("value") or raw.get("dimension") or raw.get("source") or "")
    return ""


def _normalize_strings(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    result: list[str] = []
    for item in raw:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            value = item.get("item") or item.get("text") or item.get("reason") or item.get("value")
            if value is not None:
                result.append(str(value))
    return result


def _normalize_unresolved(raw: Any, features: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Convert heterogeneous unresolved items into [{feature, reason}]."""
    result: list[dict[str, str]] = []
    if isinstance(raw, str):
        result.append({"feature": "plan", "reason": raw})
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                result.append({"feature": "plan", "reason": item})
            elif isinstance(item, dict):
                feature = str(item.get("feature") or item.get("item") or item.get("feature_id") or "plan")
                reason = str(item.get("reason") or item.get("text") or item.get("status") or "")
                if reason:
                    result.append({"feature": feature, "reason": reason})
    # Features whose executable dimensions are missing must surface to the user
    # instead of being silently dropped by the worker.
    required = {
        "through_hole": ("diameter",),
        "blind_hole": ("diameter", "depth"),
        "counterbore_hole": ("diameter", "depth"),
        "rectangular_slot": ("length", "width", "depth"),
        "rectangular_pocket": ("length", "width", "depth"),
        "annular_groove": ("reduced_outer_diameter", "axial_width", "z_start"),
        "boss_cylinder": ("diameter", "height"),
        "rectangular_pad": ("length", "width", "height"),
        "rib_box": ("length", "width", "height"),
        "linear_pattern": ("count", "spacing", "diameter"),
        "circular_pattern": ("count", "pitch_radius", "diameter"),
    }
    for feature in features:
        feature_type = feature.get("type")
        if feature_type not in required:
            continue
        missing = [name for name in required[feature_type] if name not in (feature.get("dimensions") or {})]
        if missing:
            result.append(
                {
                    "feature": feature.get("id", "feature"),
                    "reason": f"{feature_type} 缺少可执行尺寸: {', '.join(missing)};特征已保留但未建模,请补充尺寸",
                }
            )
    return result
