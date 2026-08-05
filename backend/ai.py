from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from PIL import Image

from backend.mechcad_ai import planner as ai_planner
from backend.mechcad_ai import vision as ai_vision
from backend.schemas import (
    ClarificationQuestion,
    DesignReview,
    DimensionV3,
    FeaturePlanV3,
    FeatureV3,
    GenerateRequest,
    PlacementV3,
)


_DIM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?:长|长度|length)\s*[:=]?\s*(\d+(?:\.\d+)?)"), "length"),
    (re.compile(r"(?:宽|宽度|width)\s*[:=]?\s*(\d+(?:\.\d+)?)"), "width"),
    (re.compile(r"(?:厚|厚度|height|thickness)\s*[:=]?\s*(\d+(?:\.\d+)?)"), "height"),
    (re.compile(r"(?:外径|outer diameter|od|φ)\s*[:=]?\s*(\d+(?:\.\d+)?)"), "outer_diameter"),
    (re.compile(r"(?:内径|inner diameter|id)\s*[:=]?\s*(\d+(?:\.\d+)?)"), "inner_diameter"),
    (re.compile(r"(?:孔径|diameter|直径)\s*[:=]?\s*(\d+(?:\.\d+)?)"), "diameter"),
    (re.compile(r"(?:深度|depth)\s*[:=]?\s*(\d+(?:\.\d+)?)"), "depth"),
    (re.compile(r"(?:槽宽|slot width|width)\s*[:=]?\s*(\d+(?:\.\d+)?)"), "slot_width"),
    (re.compile(r"(?:槽长|slot length|length)\s*[:=]?\s*(\d+(?:\.\d+)?)"), "slot_length"),
]


def build_initial_feature_plan(
    description: str, request: GenerateRequest, image: Image.Image | None = None, settings=None
) -> tuple[FeaturePlanV3, list[ClarificationQuestion]]:
    """Build the first FeaturePlanV3 for a generation request.

    When a vision model and/or planner model is configured, the real model chain
    runs first (vision read -> FeaturePlan planning). On missing config or model
    failure the deterministic local stub below takes over, so the IDE and CAD
    chain always keep working.
    """
    if settings is None:
        settings = request.settings
    if settings is not None:
        vision_json = ai_vision.analyze_sketch(image, description, settings)
        plan = ai_planner.generate_feature_plan(
            description,
            vision_json,
            settings,
            mode=settings.operation_mode or "strict",
        )
        if plan is not None:
            if settings.operation_mode == "smart":
                plan.assumptions.append("智能模式：由真实模型生成规划，建议需用户确认后生效。")
            return plan, questions_from_plan(plan)
    return _build_stub_feature_plan(description, request)


def _build_stub_feature_plan(description: str, request: GenerateRequest) -> tuple[FeaturePlanV3, list[ClarificationQuestion]]:
    text = description.lower()
    part_family = _detect_family(text)
    plan = FeaturePlanV3(part_family=part_family)
    parsed = _extract_dimension_clues(description)
    mode = request.operation_mode

    if part_family == "plate":
        plan.base_feature = FeatureV3(
            id="base_plate",
            type="box_base",
            operation="base",
            dimensions=_dims(
                length=parsed.get("length"),
                width=parsed.get("width"),
                height=parsed.get("height") or parsed.get("thickness"),
                evidence="plate outline from sketch + user description",
                source="drawing",
                confirmed=False,
            ),
            placement=PlacementV3(reference="bottom_center", axis="Z"),
            evidence="identified as plate-like body",
        )
        _collect_missing(plan, "base_plate", plan.base_feature.dimensions, ["length", "width", "height"], "板件外形尺寸缺失")
    elif part_family == "flange":
        plan.base_feature = FeatureV3(
            id="base_flange",
            type="cylinder_base",
            operation="base",
            dimensions=_dims(
                outer_diameter=parsed.get("outer_diameter"),
                length=parsed.get("height") or parsed.get("thickness"),
                evidence="flange outline from sketch + user description",
                source="drawing",
                confirmed=False,
            ),
            placement=PlacementV3(reference="center", axis="Z"),
            evidence="identified as flange-like body",
        )
        _collect_missing(plan, "base_flange", plan.base_feature.dimensions, ["outer_diameter", "length"], "法兰主外形缺失")
    elif part_family == "tube":
        plan.base_feature = FeatureV3(
            id="base_tube",
            type="hollow_cylinder",
            operation="base",
            dimensions=_dims(
                outer_diameter=parsed.get("outer_diameter") or parsed.get("diameter"),
                inner_diameter=parsed.get("inner_diameter"),
                length=parsed.get("length"),
                evidence="tube outline from sketch + user description",
                source="drawing",
                confirmed=False,
            ),
            placement=PlacementV3(reference="bottom_end_center", axis="Z"),
            evidence="identified as tube-like body",
        )
        _collect_missing(plan, "base_tube", plan.base_feature.dimensions, ["outer_diameter", "inner_diameter", "length"], "管件主尺寸缺失")
    else:
        plan.base_feature = FeatureV3(
            id="base_body",
            type="box_base",
            operation="base",
            dimensions=_dims(
                length=parsed.get("length"),
                width=parsed.get("width"),
                height=parsed.get("height") or parsed.get("thickness"),
                evidence="generic body from sketch + user description",
                source="drawing",
                confirmed=False,
            ),
            placement=PlacementV3(reference="origin", axis="Z"),
            evidence="fallback generic base body",
        )
        _collect_missing(plan, "base_body", plan.base_feature.dimensions, ["length", "width", "height"], "主基体尺寸缺失")

    plan.design_review = DesignReview(
        warnings=[
            "默认不会把推断尺寸写成可执行事实；未确认尺寸会停在提问或建议层。",
        ],
        suggestions=[
            "先确认整体外形，再补孔、槽、台阶、凸台等局部特征。",
            "如果图纸上有尺寸标注，请优先回填标注值，不要让系统自行猜测。",
        ],
        manufacturability=[
            "特征顺序建议按 基体 -> 减料 -> 加料 -> 阵列 -> 圆角倒角。",
        ],
        standards=[
            "孔、槽、台阶、壁厚等应尽量绑定到图中可见标注或用户确认值。",
        ],
        requires_confirmation=bool(plan.unresolved),
    )

    if mode == "smart":
        plan.assumptions.append("智能模式允许生成设计建议，但默认不把推断尺寸直接写入可执行模型。")
        if not plan.unresolved:
            plan.design_review.suggestions.append("当前信息足够进入受控建模。")

    return plan, questions_from_plan(plan)


def apply_chat_edit(
    plan: FeaturePlanV3, message: str, settings=None
) -> tuple[FeaturePlanV3, list[ClarificationQuestion]]:
    """Apply a natural-language edit. Uses the planner model when configured,
    otherwise falls back to the deterministic local edit rules."""
    if settings is not None:
        ai_updated = ai_planner.chat_edit_feature_plan(plan, message, settings)
        if ai_updated is not None:
            return ai_updated, questions_from_plan(ai_updated)
    updated = deepcopy(plan)
    text = message.strip()
    if not text:
        return updated, questions_from_plan(updated)

    lowered = text.lower()
    parsed = _extract_dimension_clues(text)
    if "删除" in text or "去掉" in text:
        updated.assumptions.append(f"用户请求删除或弱化特征: {text}")
    if any(word in lowered for word in ["孔", "hole", "槽", "slot", "台阶", "凸台", "圆角", "倒角"]):
        updated.design_review.suggestions.append(f"已记录修改请求: {text}")
    if parsed:
        target = updated.base_feature or (updated.features[0] if updated.features else None)
        if target is not None:
            for key, value in parsed.items():
                if key in target.dimensions:
                    target.dimensions[key] = DimensionV3(
                        value=value,
                        unit="mm",
                        evidence=f"user chat: {text}",
                        source="user",
                        confirmed_by_user=False,
                    )
                    _clear_unresolved(updated, target.id, key)

    return updated, questions_from_plan(updated)


def patch_feature(plan: FeaturePlanV3, feature_id: str, patch: dict[str, Any]) -> FeaturePlanV3:
    updated = deepcopy(plan)
    for feature in _all_features(updated):
        if feature.id != feature_id:
            continue
        if patch.get("dimensions"):
            for key, value in patch["dimensions"].items():
                feature.dimensions[key] = value if isinstance(value, DimensionV3) else DimensionV3.model_validate(value)
                _clear_unresolved(updated, feature.id, key)
        if patch.get("placement"):
            feature.placement = PlacementV3.model_validate(patch["placement"])
        if patch.get("confirmed_by_user") is not None:
            feature.confirmed_by_user = bool(patch["confirmed_by_user"])
        break
    return updated


def questions_from_plan(plan: FeaturePlanV3) -> list[ClarificationQuestion]:
    questions: list[ClarificationQuestion] = []
    for item in plan.unresolved[:8]:
        feature_id = str(item.get("feature", "unknown_feature"))
        reason = str(item.get("reason", "缺少必要尺寸或定位信息"))
        if "尺寸" in reason or "size" in reason.lower():
            text = f"{feature_id} 还差哪些尺寸？请直接告诉我可测量的毫米值。"
            options = ["我来补数", "先跳过这个特征", "让我重新看图确认"]
        elif "定位" in reason or "position" in reason.lower():
            text = f"{feature_id} 的位置还不够清楚。请告诉我它相对基准面/中心线/端面的距离。"
            options = ["相对左端", "相对右端", "相对中心"]
        elif "贯穿" in reason or "through" in reason.lower():
            text = f"{feature_id} 要不要贯穿？如果不是贯穿，请给我深度。"
            options = ["贯穿", "盲孔/盲槽", "暂不确定"]
        else:
            text = f"{feature_id} 现在还不能稳定建模：{reason}。请补充一个明确的毫米尺寸或定位关系。"
            options = ["补充尺寸", "补充定位", "暂时不建这个特征"]
        questions.append(
            ClarificationQuestion(
                text=text,
                feature_id=feature_id,
                dimension_refs=[],
                required=True,
                options=options,
            )
        )
    return questions


def _detect_family(text: str) -> str:
    if any(token in text for token in ["plate", "板", "板件", "flat", "bracket"]):
        return "plate"
    if any(token in text for token in ["flange", "法兰"]):
        return "flange"
    if any(token in text for token in ["tube", "pipe", "管", "轴", "套筒", "bushing"]):
        return "tube"
    return "unknown"


def _extract_dimension_clues(text: str) -> dict[str, float]:
    results: dict[str, float] = {}
    for pattern, key in _DIM_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                results[key] = float(match.group(1))
            except ValueError:
                continue
    return results


def _dims(**kwargs: Any) -> dict[str, DimensionV3]:
    evidence = str(kwargs.pop("evidence", ""))
    source = str(kwargs.pop("source", "unknown"))
    confirmed = bool(kwargs.pop("confirmed", False))
    dims: dict[str, DimensionV3] = {}
    for key, value in kwargs.items():
        if value is None:
            dims[key] = DimensionV3(value=None, evidence=evidence, source=source, confirmed_by_user=confirmed)
        else:
            dims[key] = DimensionV3(value=float(value), evidence=evidence, source=source, confirmed_by_user=confirmed)
    return dims


def _collect_missing(plan: FeaturePlanV3, feature_id: str, dimensions: dict[str, DimensionV3], required: list[str], label: str) -> None:
    missing = [name for name in required if dimensions.get(name) is None or dimensions[name].value is None]
    if missing:
        plan.unresolved.append({"feature": feature_id, "reason": f"{label}: {', '.join(missing)}"})


def _clear_unresolved(plan: FeaturePlanV3, feature_id: str, key: str) -> None:
    filtered: list[dict[str, Any]] = []
    for item in plan.unresolved:
        if item.get("feature") != feature_id:
            filtered.append(item)
            continue
        reason = str(item.get("reason", ""))
        if key not in reason:
            filtered.append(item)
    plan.unresolved = filtered


def _all_features(plan: FeaturePlanV3) -> list[FeatureV3]:
    features: list[FeatureV3] = []
    if plan.base_feature is not None:
        features.append(plan.base_feature)
    features.extend(plan.features)
    return features
