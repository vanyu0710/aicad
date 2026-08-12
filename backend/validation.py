from __future__ import annotations

"""Deterministic engineering checks and feature ordering for FeaturePlanV3.

The AI planner may return an artistically ordered feature list; this module
reorders features by manufacturing stage and validates every executable
dimension before the CAD worker is started. Strict mode blocks unconfirmed
assumptions and missing child dimensions; smart mode keeps those as auditable
warnings while still blocking true geometry contradictions.
"""

from typing import Any

from backend.schemas import FeaturePlanV3, FeatureV3


def _msg(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh


_GROUP_ORDER = {"base": 0, "remove": 1, "add": 2, "pattern": 3, "modify": 4}
_REMOVE_TYPES = {"through_hole", "blind_hole", "counterbore_hole", "rectangular_slot", "rectangular_pocket", "annular_groove", "internal_annular_groove"}
_ADD_TYPES = {"boss_cylinder", "rectangular_pad", "rib_box"}
_PATTERN_TYPES = {"linear_pattern", "circular_pattern"}
_MODIFY_TYPES = {"fillet", "chamfer"}
_UNSUPPORTED_TYPES = {"fillet", "chamfer", "helical_gear", "spur_gear", "thread", "sheet_metal"}
_NEEDS_XY_TYPES = {"through_hole", "blind_hole", "counterbore_hole", "rectangular_slot", "rectangular_pocket", "boss_cylinder", "rectangular_pad", "rib_box", "linear_pattern", "circular_pattern"}
_CENTERED_PLACEMENTS = {"main_axis", "origin", "center", "flange_center", "model_center", "bottom_center", "bottom_end_center", "base_center", "top_center"}

_REQUIRED_DIMS = {
    "box_base": ("length", "width", "height"),
    "cylinder_base": ("outer_diameter", "length"),
    "hollow_cylinder": ("outer_diameter", "inner_diameter", "length"),
    "through_hole": ("diameter",),
    "blind_hole": ("diameter", "depth"),
    "counterbore_hole": ("diameter", "depth"),
    "rectangular_slot": ("length", "width", "depth"),
    "rectangular_pocket": ("length", "width", "depth"),
    "annular_groove": ("reduced_outer_diameter", "axial_width", "z_start"),
    "internal_annular_groove": ("axial_width", "groove_depth", "z_start"),
    "link_plate": ("length", "width", "height", "end_diameter_1", "end_diameter_2"),
    "boss_cylinder": ("diameter", "height"),
    "rectangular_pad": ("length", "width", "height"),
    "rib_box": ("length", "width", "height"),
    "linear_pattern": ("count", "spacing", "diameter"),
    "circular_pattern": ("count", "pitch_radius", "diameter"),
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


def order_feature_plan(plan: FeaturePlanV3) -> tuple[list[str], list[str]]:
    """Reorder features by dependency and manufacturing stage.

    Returns (ordered_feature_ids_including_base, cyclic_feature_ids).
    """
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

    if ordered != [feature.id for feature in plan.features]:
        plan.features = [feature for feature in plan.features if feature.id in ordered] + [feature for feature in plan.features if feature.id in cycles]
    return [plan.base_feature.id, *ordered], cycles


def validate_feature_plan(plan: FeaturePlanV3, mode: str = "strict", language: str = "zh") -> dict[str, Any]:
    """Run deterministic checks and write structured self_checks into the plan."""
    checks: list[dict[str, Any]] = []
    blocking: list[str] = []
    warnings: list[str] = []

    def add_check(check_id: str, feature_id: str | None, status: str, message: str) -> None:
        checks.append({"id": check_id, "feature_id": feature_id, "status": status, "message": message})
        target = blocking if status == "block" else warnings
        target.append(message)

    order, cycles = order_feature_plan(plan)
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
            centered = placement.reference in _CENTERED_PLACEMENTS
            if placement.reference == "needs_position" or (not positioned and not centered):
                status = "block" if mode == "strict" else "warning"
                add_check("missing_placement", feature.id, status, _msg(language, f"特征 {feature.id} 缺少定位: 需要 X/Y 位置或明确基准", f"Feature {feature.id} is missing placement: X/Y position or a clear datum is required"))

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
            if base is not None and base.type == "box_base" and x is not None and y is not None:
                half_length = (_value(base, "length") or 0.0) / 2.0
                half_width = (_value(base, "width") or 0.0) / 2.0
                if abs(x) > half_length + 0.5 or abs(y) > half_width + 0.5:
                    add_check("hole_outside_base", feature.id, "block", _msg(language, f"孔中心 ({x},{y}) 超出板件边界", f"Hole center ({x},{y}) is outside the plate boundary"))

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

        unconfirmed = [
            name for name, dimension in feature.dimensions.items()
            if dimension.source == "assumption" and not dimension.confirmed_by_user
        ]
        if unconfirmed:
            status = "block" if mode == "strict" else "warning"
            add_check("unconfirmed_assumption", feature.id, status, _msg(language, f"特征 {feature.id} 含未确认假设尺寸: {', '.join(unconfirmed)}", f"Feature {feature.id} has unconfirmed assumed dimensions: {', '.join(unconfirmed)}"))

    plan.self_checks = {
        "engine": "build123d",
        "mode": mode,
        "order": order,
        "checks": checks,
        "summary": {
            "pass": sum(1 for item in checks if item["status"] == "pass"),
            "warning": sum(1 for item in checks if item["status"] == "warning"),
            "block": sum(1 for item in checks if item["status"] == "block"),
        },
    }

    def sync_review(plan: FeaturePlanV3, key: str, items: list[str]) -> None:
        review = plan.design_review
        current = getattr(review, key)
        filtered = [item for item in current if not item.startswith("[validation]")]
        setattr(review, key, filtered + [f"[validation] {item}" for item in items])

    sync_review(plan, "blocking", blocking)
    sync_review(plan, "warnings", warnings)
    return {"checks": checks, "blocking": blocking, "warnings": warnings, "order": order, "base_ready": base is not None}
