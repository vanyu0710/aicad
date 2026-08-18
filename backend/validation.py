from __future__ import annotations

"""Deterministic engineering checks and feature ordering for FeaturePlanV3.

The AI planner may return an artistically ordered feature list; this module
reorders features by manufacturing stage and validates every executable
dimension before the CAD worker is started. Strict mode blocks unconfirmed
assumptions and missing child dimensions; smart mode keeps those as auditable
warnings while still blocking true geometry contradictions.
"""

from typing import Any

from backend.feature_definitions import FEATURE_DEFINITIONS
from backend.schemas import FeaturePlanV3, FeatureV3


def _msg(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh


_GROUP_ORDER = {"base": 0, "remove": 1, "add": 2, "pattern": 3, "modify": 4}
_REMOVE_TYPES = {definition.feature_type for definition in FEATURE_DEFINITIONS.list() if definition.geometry_effect == "remove"}
_ADD_TYPES = {definition.feature_type for definition in FEATURE_DEFINITIONS.list() if definition.geometry_effect == "add"}
_PATTERN_TYPES = {definition.feature_type for definition in FEATURE_DEFINITIONS.list() if definition.geometry_effect == "pattern"}
_MODIFY_TYPES = {definition.feature_type for definition in FEATURE_DEFINITIONS.list() if definition.geometry_effect == "modify"}
_UNSUPPORTED_TYPES = {definition.feature_type for definition in FEATURE_DEFINITIONS.list() if definition.implementation_status == "unsupported"}
_NEEDS_XY_TYPES = {"through_hole", "blind_hole", "counterbore_hole", "rectangular_slot", "rectangular_pocket", "boss_cylinder", "rectangular_pad", "rib_box", "linear_pattern", "circular_pattern"}
_REQUIRED_DIMS = {
    definition.feature_type: tuple(definition.required_dimensions)
    for definition in FEATURE_DEFINITIONS.list()
    if definition.implementation_status == "supported"
}


def _group_index(feature: FeatureV3) -> int:
    if feature.operation == "base":
        return 0
    if feature.operation == "remove" or feature.type in _REMOVE_TYPES:
        return 1
    if feature.operation == "pattern" or feature.type in _PATTERN_TYPES:
        return 3
    if feature.operation == "modify" or feature.type in _MODIFY_TYPES:
        return 4
    return 2


def _value(feature: FeatureV3 | None, *names: str) -> float | None:
    if feature is None:
        return None
    for name in names:
        dimension = feature.dimensions.get(name)
        if dimension is not None and dimension.value is not None:
            try:
                return float(dimension.value)
            except (TypeError, ValueError):
                continue
    return None


def _has(feature: FeatureV3 | None, name: str) -> bool:
    return _value(feature, name) is not None


def _host_inplane_limit(base: FeatureV3 | None) -> float | None:
    """Largest in-plane radius a child can occupy on the host, or None if unknown."""
    if base is None:
        return None
    outer = _value(base, "outer_diameter")
    if outer is not None and outer > 0:
        return outer / 2.0
    length = _value(base, "length")
    width = _value(base, "width")
    if length is not None and width is not None and length > 0 and width > 0:
        return min(length, width) / 2.0
    return None


def _outside_host_xy(base: FeatureV3, x: float, y: float, child_radius: float) -> bool:
    from math import hypot

    limit = _host_inplane_limit(base)
    if limit is None:
        return False
    return hypot(x, y) + child_radius > limit + 0.5


def _circular_pattern_outside_host(base: FeatureV3, pitch_radius: float, instance_radius: float) -> bool:
    limit = _host_inplane_limit(base)
    if limit is None:
        return False
    return pitch_radius + instance_radius > limit + 0.5


def compute_feature_order(plan: FeaturePlanV3) -> tuple[list[str], list[str]]:
    """Return dependency/stage order without mutating the plan."""
    if plan.base_feature is None:
        return [], [feature.id for feature in plan.features]
    all_ids = {plan.base_feature.id, *(feature.id for feature in plan.features)}
    done = {plan.base_feature.id}
    remaining = list(plan.features)
    ordered: list[str] = []
    cycles: list[str] = []

    while remaining:
        ready = [
            (index, feature)
            for index, feature in enumerate(remaining)
            if all(dep in done for dep in feature.depends_on if dep in all_ids)
        ]
        if not ready:
            cycles = [feature.id for feature in remaining]
            break
        _, feature = min(ready, key=lambda item: (_group_index(item[1]), item[0]))
        remaining.remove(feature)
        ordered.append(feature.id)
        done.add(feature.id)

    return [plan.base_feature.id, *ordered], cycles


def order_feature_plan(plan: FeaturePlanV3) -> tuple[list[str], list[str]]:
    """Explicitly reorder features by dependency and manufacturing stage."""
    ordered, cycles = compute_feature_order(plan)
    if plan.base_feature is not None and ordered != [plan.base_feature.id, *[feature.id for feature in plan.features]]:
        plan.features = [feature for feature in plan.features if feature.id in ordered] + [feature for feature in plan.features if feature.id in cycles]
    return ordered, cycles


def validate_feature_plan(plan: FeaturePlanV3, mode: str = "strict", language: str = "zh") -> dict[str, Any]:
    """Run deterministic checks without mutating the plan."""
    checks: list[dict[str, Any]] = []
    blocking: list[str] = []
    warnings: list[str] = []

    def add_check(check_id: str, feature_id: str | None, status: str, message: str) -> None:
        checks.append({"id": check_id, "feature_id": feature_id, "status": status, "message": message})
        target = blocking if status == "block" else warnings
        target.append(message)

    order, cycles = compute_feature_order(plan)
    if cycles:
        add_check("dependency_cycle", None, "block", _msg(language, "依赖环: " + ", ".join(cycles), "Dependency cycle: " + ", ".join(cycles)))

    all_features = ([plan.base_feature] if plan.base_feature else []) + list(plan.features)
    seen: set[str] = set()
    for feature in all_features:
        if feature.id in seen:
            add_check("duplicate_id", feature.id, "block", _msg(language, f"重复特征 ID: {feature.id}", f"Duplicate feature ID: {feature.id}"))
        seen.add(feature.id)

    base = plan.base_feature
    if base is None:
        add_check("base_missing", None, "block", _msg(language, "缺少主基体 base_feature", "Missing main body base_feature"))
    else:
        required = _REQUIRED_DIMS.get(base.type, ())
        missing = [name for name in required if not _has(base, name)]
        if missing:
            add_check("base_missing_dimensions", base.id, "block", _msg(language, f"主基体 {base.id} 缺少: {', '.join(missing)}", f"Main body {base.id} is missing: {', '.join(missing)}"))
        for name, dimension in base.dimensions.items():
            if dimension.value is not None and dimension.value <= 0 and name not in {"x", "y", "z"}:
                add_check("non_positive_dimension", base.id, "block", _msg(language, f"{base.id}.{name} 必须大于 0", f"{base.id}.{name} must be greater than 0"))
        outer = _value(base, "outer_diameter")
        inner = _value(base, "inner_diameter")
        if base.type == "hollow_cylinder" and outer is not None and inner is not None:
            if inner >= outer:
                add_check("inner_diameter_geometry", base.id, "block", _msg(language, "内径必须小于外径", "Inner diameter must be smaller than outer diameter"))
            elif outer - inner < 0.5:
                add_check("wall_thickness", base.id, "block", _msg(language, "壁厚必须大于 0.5mm", "Wall thickness must be greater than 0.5mm"))
            elif outer - inner < 2.0:
                add_check("wall_thickness_warning", base.id, "warning", _msg(language, "壁厚偏薄，建议不小于 2mm", "Wall thickness is thin; recommend at least 2mm"))

    for feature in plan.features:
        required = _REQUIRED_DIMS.get(feature.type, ())
        missing = [name for name in required if not _has(feature, name)]
        if missing:
            status = "block" if mode == "strict" else "warning"
            add_check("feature_missing_dimensions", feature.id, status, _msg(language, f"特征 {feature.id} 缺少: {', '.join(missing)}", f"Feature {feature.id} is missing: {', '.join(missing)}"))
        for name, dimension in feature.dimensions.items():
            if dimension.value is not None and dimension.value <= 0 and name not in {"x", "y", "z"}:
                add_check("non_positive_dimension", feature.id, "block", _msg(language, f"{feature.id}.{name} 必须大于 0", f"{feature.id}.{name} must be greater than 0"))
        if feature.type in _NEEDS_XY_TYPES:
            placement = feature.placement
            positioned = placement.x is not None and placement.y is not None
            if placement.reference == "needs_position" or not positioned:
                status = "block" if mode == "strict" else "warning"
                add_check("missing_placement", feature.id, status, _msg(language, f"特征 {feature.id} 缺少定位: 需要明确的 X/Y，不能用基准名代替坐标", f"Feature {feature.id} is missing placement: explicit X/Y is required; a datum name is not a coordinate"))

        if feature.type in _UNSUPPORTED_TYPES:
            add_check("unsupported_feature", feature.id, "warning", _msg(language, f"特征 {feature.id} 类型暂不支持: {feature.type}", f"Feature {feature.id} type is not supported yet: {feature.type}"))

        deps = [dep for dep in feature.depends_on if dep and dep not in seen]
        if deps:
            add_check("missing_dependency", feature.id, "block", _msg(language, f"特征 {feature.id} 依赖不存在: {', '.join(deps)}", f"Feature {feature.id} has a missing dependency: {', '.join(deps)}"))

        if feature.type in {"through_hole", "blind_hole", "counterbore_hole"}:
            diameter = _value(feature, "diameter", "hole_diameter")
            if diameter is not None and base is not None:
                limit = _value(base, "outer_diameter") or _value(base, "width") or _value(base, "length")
                if limit is not None and diameter >= limit:
                    add_check("hole_larger_than_base", feature.id, "block", _msg(language, f"孔径 {diameter}mm 不小于基体外形 {limit}mm", f"Hole diameter {diameter}mm is not smaller than body size {limit}mm"))
            x = feature.placement.x
            y = feature.placement.y
            if base is not None and x is not None and y is not None and _outside_host_xy(base, float(x), float(y), (diameter or 0.0) / 2.0):
                add_check("hole_outside_base", feature.id, "block", _msg(language, f"孔中心 ({x},{y}) 超出基体边界", f"Hole center ({x},{y}) is outside the host boundary"))

        if feature.type == "annular_groove":
            reduced = _value(feature, "reduced_outer_diameter")
            inner = _value(base, "inner_diameter") if base else None
            if reduced is not None and inner is not None and reduced <= inner:
                add_check("groove_root_too_small", feature.id, "block", _msg(language, "槽底外径必须大于内径", "Groove root diameter must be greater than inner diameter"))
            z_start = _value(feature, "z_start")
            axial_width = _value(feature, "axial_width")
            length = _value(base, "length") if base else None
            if z_start is not None and axial_width is not None and length is not None and z_start + axial_width > length + 0.5:
                add_check("groove_outside_base", feature.id, "block", _msg(language, "环槽位置超出基体长度", "Groove position exceeds the body length"))

        if feature.type == "internal_annular_groove":
            inner = _value(base, "inner_diameter") if base else None
            outer = _value(base, "outer_diameter") if base else None
            depth = _value(feature, "groove_depth", "depth")
            reduced_inner = inner + 2 * depth if inner is not None and depth is not None else None
            if reduced_inner is not None and outer is not None and reduced_inner >= outer:
                add_check("internal_groove_root_too_large", feature.id, "block", _msg(language, f"\u5185\u58c1\u69fd\u69fd\u5e95\u76f4\u5f84 {reduced_inner}mm \u5fc5\u987b\u5c0f\u4e8e\u5916\u5f84 {outer}mm", f"Internal groove root diameter {reduced_inner}mm must be smaller than outer diameter {outer}mm"))
            z_start = _value(feature, "z_start")
            axial_width = _value(feature, "axial_width")
            length = _value(base, "length") if base else None
            if z_start is not None and axial_width is not None and length is not None and z_start + axial_width > length + 0.5:
                add_check("internal_groove_outside_base", feature.id, "block", _msg(language, "\u5185\u58c1\u69fd\u4f4d\u7f6e\u8d85\u51fa\u57fa\u4f53\u957f\u5ea6", "Internal groove position exceeds the body length"))

        if feature.type in {"linear_pattern", "circular_pattern"}:
            count = _value(feature, "count")
            if count is not None and (count < 2 or count > 100):
                add_check("pattern_count", feature.id, "block", _msg(language, f"阵列数量 {count} 超出 2-100", f"Pattern count {count} is outside 2-100"))
            if feature.type == "circular_pattern":
                radius = _value(feature, "pitch_radius", "bolt_circle_radius")
                if radius is not None and radius <= 0:
                    add_check("pattern_radius", feature.id, "block", _msg(language, "阵列半径必须大于 0", "Pattern radius must be greater than 0"))
                instance_radius = (_value(feature, "diameter", "hole_diameter") or 0.0) / 2.0
                if radius is not None and base is not None and _circular_pattern_outside_host(base, radius, instance_radius):
                    add_check(
                        "pattern_outside_host",
                        feature.id,
                        "block",
                        _msg(language, f"圆周阵列半径 {radius}mm 超出基体轮廓", f"Circular pattern radius {radius}mm exceeds the host outline"),
                    )

        unconfirmed = [
            name for name, dimension in feature.dimensions.items()
            if dimension.source == "assumption" and not dimension.confirmed_by_user
        ]
        if unconfirmed:
            status = "block" if mode == "strict" else "warning"
            add_check("unconfirmed_assumption", feature.id, status, _msg(language, f"特征 {feature.id} 含未确认假设尺寸: {', '.join(unconfirmed)}", f"Feature {feature.id} has unconfirmed assumed dimensions: {', '.join(unconfirmed)}"))

    return {"checks": checks, "blocking": blocking, "warnings": warnings, "order": order, "base_ready": base is not None}


def apply_validation_result(
    plan: FeaturePlanV3,
    result: dict[str, Any],
    mode: str = "strict",
) -> FeaturePlanV3:
    """Explicitly write a validation result back into plan bookkeeping."""
    checks = result.get("checks") or []
    blocking = result.get("blocking") or []
    warnings = result.get("warnings") or []
    plan.self_checks = {
        "engine": "build123d",
        "mode": mode,
        "order": result.get("order") or [],
        "checks": checks,
        "summary": {
            "pass": sum(1 for item in checks if item.get("status") == "pass"),
            "warning": sum(1 for item in checks if item.get("status") == "warning"),
            "block": sum(1 for item in checks if item.get("status") == "block"),
        },
    }

    def sync_review(key: str, items: list[str]) -> None:
        review = plan.design_review
        current = getattr(review, key)
        filtered = [item for item in current if not item.startswith("[validation]")]
        setattr(review, key, filtered + [f"[validation] {item}" for item in items])

    sync_review("blocking", blocking)
    sync_review("warnings", warnings)
    return plan
