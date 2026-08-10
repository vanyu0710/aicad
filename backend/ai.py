from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from PIL import Image

from backend.mechcad_ai import planner as ai_planner
from backend.mechcad_ai import vision as ai_vision
from backend.schemas import (
    ClarificationQuestion,
    DesignAssumption,
    DesignReview,
    DimensionV3,
    FeaturePlanV3,
    FeatureV3,
    GenerateRequest,
    PlacementV3,
)


_NUM = r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)?"
_SEP = r"\s*[:=：]?\s*"
_DIAMETER_PREFIX = r"(?:φ|Φ|Ø)?\s*"

# Keep a few mojibake aliases because old tests and historical user inputs
# contain text that was decoded with the wrong charset.
_DIM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?:槽宽|槽的宽度|slot\s*width){_SEP}{_NUM}", re.IGNORECASE), "slot_width"),
    (re.compile(rf"(?:槽长|槽的长度|slot\s*length){_SEP}{_NUM}", re.IGNORECASE), "slot_length"),
    (re.compile(rf"(?:槽深|槽的深度|slot\s*depth){_SEP}{_NUM}", re.IGNORECASE), "slot_depth"),
    (re.compile(rf"(?:槽底外径|槽径|groove\s*diameter){_SEP}{_DIAMETER_PREFIX}{_NUM}", re.IGNORECASE), "groove_diameter"),
    (re.compile(rf"(?:外径|外圆直径|澶栧緞|outer\s*diameter|od){_SEP}{_DIAMETER_PREFIX}{_NUM}", re.IGNORECASE), "outer_diameter"),
    (re.compile(rf"(?:内径|孔内径|鍐呭緞|inner\s*diameter|id|bore){_SEP}{_DIAMETER_PREFIX}{_NUM}", re.IGNORECASE), "inner_diameter"),
    (re.compile(rf"(?:孔径|孔直径|直径|鐩村緞|diameter){_SEP}{_DIAMETER_PREFIX}{_NUM}", re.IGNORECASE), "diameter"),
    (re.compile(rf"(?:长|长度|总长|闀|闀垮害|overall\s*length|length|L){_SEP}{_NUM}", re.IGNORECASE), "length"),
    (re.compile(rf"(?:宽|宽度|width|W){_SEP}{_NUM}", re.IGNORECASE), "width"),
    (re.compile(rf"(?:厚|厚度|高度|height|thickness|T){_SEP}{_NUM}", re.IGNORECASE), "height"),
    (re.compile(rf"(?:深度|depth){_SEP}{_NUM}", re.IGNORECASE), "depth"),
    (re.compile(rf"\bM\s*{_NUM}\b", re.IGNORECASE), "metric_thread"),
]

_BOX_TRIPLE_PATTERN = re.compile(rf"{_NUM}\s*[xX×]\s*{_NUM}\s*[xX×]\s*{_NUM}")


def build_initial_feature_plan(
    description: str, request: GenerateRequest, image: Image.Image | None = None, settings=None
) -> tuple[FeaturePlanV3, list[ClarificationQuestion]]:
    """Build the first FeaturePlanV3 for a generation request."""
    if settings is None:
        settings = request.settings
    if settings is not None:
        vision_json = ai_vision.analyze_sketch(image, description, settings)
        plan = ai_planner.generate_feature_plan(
            description,
            vision_json,
            settings,
            mode=settings.operation_mode or "strict",
            smart_fill_policy=settings.smart_fill_policy or "limited_fill",
        )
        if plan is not None:
            if settings.operation_mode == "smart":
                plan = _apply_smart_autonomy(plan, description, settings.smart_fill_policy or "limited_fill")
            return plan, questions_from_plan(plan)
    plan, questions = _build_stub_feature_plan(description, request)
    if settings is not None and settings.operation_mode == "smart":
        plan = _apply_smart_autonomy(plan, description, settings.smart_fill_policy or "limited_fill")
        questions = questions_from_plan(plan)
    return plan, questions


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
            ),
            placement=PlacementV3(reference="bottom_center", axis="Z"),
            evidence="identified as plate-like body",
        )
        _collect_missing(plan, "base_plate", plan.base_feature.dimensions, ["length", "width", "height"], "板件主尺寸缺失")
    elif part_family == "flange":
        plan.base_feature = FeatureV3(
            id="base_flange",
            type="cylinder_base",
            operation="base",
            dimensions=_dims(
                outer_diameter=parsed.get("outer_diameter") or parsed.get("diameter"),
                length=parsed.get("height") or parsed.get("thickness") or parsed.get("length"),
                evidence="flange outline from sketch + user description",
                source="drawing",
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
            ),
            placement=PlacementV3(reference="origin", axis="Z"),
            evidence="fallback generic base body",
        )
        _collect_missing(plan, "base_body", plan.base_feature.dimensions, ["length", "width", "height"], "主基体尺寸缺失")

    _add_stub_feature_candidates(plan, text, parsed)

    plan.design_review = DesignReview(
        warnings=["严格模式不会把推断尺寸写成可执行事实；未确认尺寸会停留在待确认问题或设计建议中。"],
        suggestions=[
            "先确认整体外形，再补孔、槽、台阶、凸台等局部特征。",
            "如果图纸上有尺寸标注，请优先回填标注值，不要让系统自行猜测。",
        ],
        manufacturability=["建议按：基体 -> 减料 -> 加料 -> 阵列 -> 圆角/倒角 的顺序建模。"],
        standards=["孔、槽、台阶、壁厚等应尽量绑定到图中可见标注或用户确认值。"],
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
    """Apply a natural-language edit, using the planner model when configured."""
    if settings is not None:
        ai_updated = ai_planner.chat_edit_feature_plan(plan, message, settings)
        if ai_updated is not None:
            if settings.operation_mode == "smart":
                ai_updated = _apply_smart_autonomy(ai_updated, message, settings.smart_fill_policy or "limited_fill")
            return ai_updated, questions_from_plan(ai_updated)

    updated = deepcopy(plan)
    text = message.strip()
    if not text:
        return updated, questions_from_plan(updated)

    lowered = text.lower()
    parsed = _extract_dimension_clues(text)
    if "删除" in text or "去掉" in text:
        updated.assumptions.append(f"用户请求删除或弱化特征：{text}")
    if any(word in lowered for word in ["孔", "hole", "槽", "slot", "台阶", "凸台", "圆角", "倒角"]):
        updated.design_review.suggestions.append(f"已记录修改请求：{text}")
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
                        confirmed_by_user=True,
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
        lower = reason.lower()
        dimension_refs = _missing_dimension_refs(reason)
        if "环槽" in reason or "groove" in lower or "annular" in lower:
            text = (
                f"{feature_id} 是环槽候选，但还不能建模。请补充槽宽、槽底外径或槽深，"
                "以及它从哪个端面开始多少 mm。"
            )
            options = ["补槽宽/槽径/位置", "先跳过槽", "重新上传清晰图"]
            impact = "未确认时只生成已确认的主体，环槽不会进入可执行模型。"
            answer_type = "text"
            unit = "mm"
        elif "孔" in reason or "hole" in lower:
            text = (
                f"{feature_id} 是孔候选，但还缺孔径、孔中心位置或是否贯穿。"
                "请用“孔径=6mm，距左端20mm，距中心线0mm，贯穿”这样的格式回答。"
            )
            options = ["补孔径和位置", "改为盲孔", "先跳过孔"]
            impact = "未确认时跳过该孔，避免生成位置或孔径错误的孔。"
            answer_type = "text"
            unit = "mm"
        elif "定位" in reason or "position" in lower:
            text = f"{feature_id} 的位置还不清楚。请说明它相对端面、中心线或基准面的毫米距离。"
            options = ["相对左/下端", "相对中心线", "先跳过"]
            impact = "未确认时不会执行该特征。"
            answer_type = "text"
            unit = "mm"
        elif "贯穿" in reason or "through" in lower or "extent" in lower:
            text = f"{feature_id} 要不要贯穿？如果不是贯穿，请给出深度 mm。"
            options = ["贯穿", "盲孔/盲槽", "先跳过"]
            impact = "贯穿会切穿实体；盲孔或盲槽需要额外深度。"
            answer_type = "choice"
        else:
            text = f"{feature_id} 现在还不能稳定建模：{reason}。请补充明确毫米尺寸或定位关系。"
            options = ["补充尺寸", "补充定位", "先跳过"]
            impact = "未确认时该特征会被跳过，并在报告中保留原因。"
            answer_type = "text"
            unit = "mm"
        questions.append(
            ClarificationQuestion(
                text=text,
                feature_id=feature_id,
                dimension_refs=dimension_refs,
                required=True,
                options=options,
                reason=reason,
                impact=impact,
                answer_type=answer_type,
                unit=unit,
            )
        )
    return questions


def apply_clarification_answers(
    plan: FeaturePlanV3, answers_text: str
) -> FeaturePlanV3:
    """Apply plain-language answers as user-confirmed values before CAD execution."""
    updated = deepcopy(plan)
    if not answers_text.strip():
        return updated
    parsed = _extract_dimension_clues(answers_text)
    lowered = answers_text.lower()
    for feature in _all_features(updated):
        unresolved_text = " ".join(feature.unresolved)
        relevant = feature.id in answers_text or feature.id in unresolved_text
        if not relevant and feature is not updated.base_feature:
            continue
        for key, value in parsed.items():
            if key in feature.dimensions:
                feature.dimensions[key] = DimensionV3(
                    value=value,
                    unit="mm",
                    evidence=f"用户回答澄清问题：{answers_text}",
                    source="user",
                    confirmed_by_user=True,
                )
                _clear_unresolved(updated, feature.id, key)
        if "贯穿" in answers_text or "through" in lowered:
            feature.extent = "through"
            feature.unresolved = [item for item in feature.unresolved if "through" not in item.lower() and "贯穿" not in item]
        if "跳过" in answers_text or "不建" in answers_text:
            feature.unresolved.append("用户选择暂不执行该特征")
    return updated


def _missing_dimension_refs(reason: str) -> list[str]:
    refs = []
    mapping = {
        "outer_diameter": "外径",
        "inner_diameter": "内径",
        "length": "长度",
        "width": "宽度",
        "height": "厚度/高度",
        "diameter": "孔径/直径",
        "axial_width": "槽宽",
        "reduced_outer_diameter": "槽底外径",
        "z_start": "槽起点位置",
        "hole position": "孔中心位置",
        "through/blind extent": "贯穿或深度",
    }
    for key, label in mapping.items():
        if key in reason:
            refs.append(label)
    return refs


def _add_stub_feature_candidates(plan: FeaturePlanV3, text: str, parsed: dict[str, float]) -> None:
    """Attach explicit feature intentions from the user's words without guessing."""
    base_id = plan.base_feature.id if plan.base_feature else "base"
    wants_slot = any(token in text for token in ["槽", "slot", "groove", "开口"])
    wants_hole = any(token in text for token in ["孔", "hole", "通孔", "螺栓孔", "m6", "m8"])

    if plan.part_family == "tube" and wants_slot:
        axial_width = parsed.get("slot_width") or parsed.get("slot_length")
        reduced_od = parsed.get("groove_diameter")
        length = parsed.get("length")
        z_start = None
        if length is not None and axial_width is not None and any(token in text for token in ["顶部", "顶端", "末端", "端部", "top", "end"]):
            z_start = max(0.0, length - axial_width)
        dims = {
            "axial_width": DimensionV3(value=axial_width, evidence="user/sketch mentions tube groove width", source="drawing"),
            "reduced_outer_diameter": DimensionV3(value=reduced_od, evidence="groove bottom diameter is needed for annular groove", source="unknown"),
            "z_start": DimensionV3(
                value=z_start,
                evidence="derived from length - groove width for end groove" if z_start is not None else "groove axial start is not confirmed",
                source="derived" if z_start is not None else "unknown",
            ),
        }
        feature = FeatureV3(
            id="top_groove" if z_start is not None else "annular_groove_candidate",
            type="annular_groove",
            operation="remove",
            dimensions=dims,
            placement=PlacementV3(reference="main_axis", axis="Z"),
            depends_on=[base_id],
            evidence="user/sketch indicates a groove on a tube-like part",
        )
        missing = [name for name, dim in dims.items() if dim.value is None]
        if missing:
            feature.unresolved.append("annular groove missing executable dimensions: " + ", ".join(missing))
            plan.unresolved.append({"feature": feature.id, "reason": "环槽还不能建模，需要补充: " + ", ".join(missing)})
        plan.features.append(feature)

    if plan.part_family in {"plate", "flange"} and wants_hole:
        diameter = parsed.get("diameter")
        if plan.part_family == "flange" and "中心" in text:
            feature_id = "center_hole"
            placement = PlacementV3(reference="flange_center", x=0, y=0, z=0, axis="Z")
        else:
            feature_id = "hole_candidate"
            placement = PlacementV3(reference="needs_position", axis="Z")
        feature = FeatureV3(
            id=feature_id,
            type="through_hole",
            operation="remove",
            dimensions={"diameter": DimensionV3(value=diameter, evidence="user/sketch mentions hole diameter", source="drawing" if diameter else "unknown")},
            placement=placement,
            extent="through" if "通孔" in text or "through" in text else None,
            depends_on=[base_id],
            evidence="user/sketch indicates a hole feature",
        )
        missing = []
        if diameter is None:
            missing.append("diameter")
        if placement.reference == "needs_position":
            missing.append("hole position")
        if feature.extent is None:
            missing.append("through/blind extent")
        if missing:
            feature.unresolved.append("through hole missing executable data: " + ", ".join(missing))
            plan.unresolved.append({"feature": feature.id, "reason": "孔特征还不能稳定建模，需要补充: " + ", ".join(missing)})
        plan.features.append(feature)


def _apply_smart_autonomy(plan: FeaturePlanV3, description: str, policy: str) -> FeaturePlanV3:
    """Turn a Smart plan into an executable concept model with explicit assumptions.

    Smart mode is allowed to design, but it must remain auditable. Every value
    created here is marked as an assumption and summarized in the plan review.
    Strict mode never calls this function.
    """
    updated = deepcopy(plan)
    if policy not in {"suggest_only", "limited_fill", "aggressive_fill", "full_autonomous"}:
        policy = "limited_fill"
    updated.autonomy_policy = policy
    updated.design_intent = updated.design_intent or _smart_design_intent(description, updated.part_family)
    if policy == "suggest_only":
        updated.assumptions.append("智能模式当前策略为只给建议，未将推断尺寸写入可执行模型。")
        updated.design_review.suggestions.append("切换为“工程自主设计，保守补全”后，系统才会自动生成概念模型。")
        updated.self_checks["smart_autonomy"] = {"policy": policy, "executed": False}
        return updated

    text = description.lower()
    parsed = _extract_dimension_clues(description)
    base = updated.base_feature
    if base is None:
        base = _smart_base(_detect_family(text), parsed)
        updated.base_feature = base
        updated.assumptions.append("智能模式根据零件功能和文字线索选择了一个可修改的主基体。")

    _complete_base_dimensions(base, parsed, updated)
    for feature in updated.features:
        _complete_smart_feature(feature, base, parsed, text, policy, updated)

    if policy == "full_autonomous":
        _add_full_autonomous_features(updated, text)

    _refresh_smart_resolution(updated)
    updated.assumptions.append(
        f"智能模式已执行自主设计策略：{policy}。所有新增尺寸都是工程假设，未被当作图纸事实。"
    )
    updated.design_review.requires_confirmation = True
    updated.design_review.warnings.append("当前模型包含智能模式假设尺寸；用于概念验证，不等同于最终生产图纸。")
    updated.design_review.suggestions.append("请在特征树或 AI 对话中确认关键外径、壁厚、孔位和槽尺寸后再用于制造。")
    updated.self_checks["smart_autonomy"] = {
        "policy": policy,
        "executed": True,
        "assumption_count": len(updated.assumptions),
        "remaining_unresolved": len(updated.unresolved),
    }
    _sync_assumption_details(updated)
    return updated


def _smart_design_intent(description: str, part_family: str) -> str:
    text = description.lower()
    if any(token in text for token in ["安装", "mount", "固定", "连接"]):
        return f"面向安装和连接功能的 {part_family} 概念设计"
    if any(token in text for token in ["支撑", "支架", "bracket", "support"]):
        return f"面向承载和支撑功能的 {part_family} 概念设计"
    if any(token in text for token in ["密封", "seal", "流体", "pipe", "tube", "管"]):
        return f"面向导流或密封功能的 {part_family} 概念设计"
    return f"基于用户功能描述的 {part_family} 概念机械设计"


def _add_full_autonomous_features(plan: FeaturePlanV3, text: str) -> None:
    """Add only explainable, supported concept features in full autonomy mode."""
    base = plan.base_feature
    if base is None:
        return
    existing_types = {feature.type for feature in plan.features}
    base_id = base.id
    if plan.part_family == "tube" and "annular_groove" not in existing_types:
        outer = _feature_value(base.dimensions, "outer_diameter") or 40.0
        length = _feature_value(base.dimensions, "length") or 80.0
        width = max(2.0, round(outer * 0.08, 1))
        feature = FeatureV3(
            id="autonomous_end_groove",
            type="annular_groove",
            operation="remove",
            dimensions={},
            placement=PlacementV3(reference="main_axis", axis="Z"),
            depends_on=[base_id],
            evidence="全自主模式按管件的密封/定位常见制造意图增加端部环槽",
        )
        _assume_dimension(feature, "axial_width", width, "按外径约 8% 选择端部环槽宽度")
        _assume_dimension(feature, "reduced_outer_diameter", max(1.0, outer - max(2.0, round(outer * 0.08, 1))), "按外径减少约 8% 形成槽底")
        _assume_dimension(feature, "z_start", max(0.0, length - width), "端部环槽贴近管件末端")
        plan.features.append(feature)
        plan.assumptions.append("全自主模式根据管件的定位/密封意图增加端部环槽；可在特征树中删除或修改。")

    support_words = ["支架", "支撑", "bracket", "support", "加强"]
    if any(token in text for token in support_words) and "rib_box" not in existing_types:
        base_length = _feature_value(base.dimensions, "length") or 80.0
        base_width = _feature_value(base.dimensions, "width") or 30.0
        feature = FeatureV3(
            id="autonomous_support_rib",
            type="rib_box",
            operation="add",
            dimensions={},
            placement=PlacementV3(reference="base_center", x=0.0, y=0.0, z=_feature_value(base.dimensions, "height") or 0.0, axis="Z"),
            depends_on=[base_id],
            evidence="全自主模式按支撑功能增加一条可制造的加强肋概念特征",
        )
        _assume_dimension(feature, "length", max(20.0, round(base_length * 0.45, 1)), "加强肋长度取基体长度约 45%")
        _assume_dimension(feature, "width", max(4.0, round(base_width * 0.15, 1)), "加强肋宽度取基体宽度约 15%")
        _assume_dimension(feature, "height", max(4.0, round((_feature_value(base.dimensions, "height") or 10.0) * 1.5, 1)), "加强肋高度按基体厚度的工程比例选择")
        plan.features.append(feature)
        plan.assumptions.append("全自主模式根据支撑意图增加加强肋；请在设计评审中确认受力方向。")

    if plan.features:
        plan.design_review.suggestions.append("全自主方案已主动补充可解释的机械特征；请逐项审查后再用于生产。")


def _smart_base(part_family: str, parsed: dict[str, float]) -> FeatureV3:
    if part_family == "tube":
        return FeatureV3(
            id="base_tube",
            type="hollow_cylinder",
            operation="base",
            dimensions={},
            placement=PlacementV3(reference="bottom_end_center", axis="Z"),
            evidence="智能模式根据管/轴/套筒功能选择回转基体",
        )
    if part_family == "flange":
        return FeatureV3(
            id="base_flange",
            type="cylinder_base",
            operation="base",
            dimensions={},
            placement=PlacementV3(reference="center", axis="Z"),
            evidence="智能模式根据法兰功能选择圆柱基体",
        )
    return FeatureV3(
        id="base_plate" if part_family == "plate" else "base_body",
        type="box_base",
        operation="base",
        dimensions={},
        placement=PlacementV3(reference="bottom_center", axis="Z"),
        evidence="智能模式根据板件/通用零件功能选择箱体基体",
    )


def _complete_base_dimensions(base: FeatureV3, parsed: dict[str, float], plan: FeaturePlanV3) -> None:
    dims = base.dimensions
    kind = base.type
    if kind == "hollow_cylinder":
        outer = _feature_value(dims, "outer_diameter") or parsed.get("outer_diameter") or parsed.get("diameter") or 40.0
        inner = _feature_value(dims, "inner_diameter") or parsed.get("inner_diameter") or max(outer * 0.6, outer - 10.0)
        length = _feature_value(dims, "length") or parsed.get("length") or max(80.0, outer * 2.0)
        inner = min(inner, max(outer - 1.0, outer * 0.9))
        _assume_dimension(base, "outer_diameter", outer, "外径缺失，采用管件概念外径 40mm 或用户已有外径线索")
        _assume_dimension(base, "inner_diameter", inner, "内径缺失，按约 60% 外径保留合理壁厚")
        _assume_dimension(base, "length", length, "长度缺失，按约 2 倍外径建立可修改概念长度")
        return
    if kind == "cylinder_base":
        outer = _feature_value(dims, "outer_diameter") or parsed.get("outer_diameter") or parsed.get("diameter") or 60.0
        length = _feature_value(dims, "length") or parsed.get("length") or parsed.get("height") or 10.0
        _assume_dimension(base, "outer_diameter", outer, "法兰/圆柱外径缺失，采用常见概念外径")
        _assume_dimension(base, "length", length, "法兰厚度或圆柱长度缺失，采用常见概念厚度")
        return

    length = _feature_value(dims, "length") or parsed.get("length") or 80.0
    width = _feature_value(dims, "width") or parsed.get("width") or max(30.0, round(length * 0.6, 1))
    height = _feature_value(dims, "height") or parsed.get("height") or parsed.get("thickness") or max(4.0, round(min(length, width) * 0.1, 1))
    _assume_dimension(base, "length", length, "板件长度缺失，采用 80mm 或按已有长度线索建立概念外形")
    _assume_dimension(base, "width", width, "板件宽度缺失，按长度约 60% 建立概念外形")
    _assume_dimension(base, "height", height, "板件厚度缺失，按短边约 10% 且不小于 4mm 建立概念外形")


def _complete_smart_feature(
    feature: FeatureV3,
    base: FeatureV3,
    parsed: dict[str, float],
    text: str,
    policy: str,
    plan: FeaturePlanV3,
) -> None:
    dims = feature.dimensions
    base_length = _feature_value(base.dimensions, "length", "height") or 80.0
    base_outer = _feature_value(base.dimensions, "outer_diameter") or 40.0
    base_width = _feature_value(base.dimensions, "width") or 30.0
    kind = feature.type

    if kind == "annular_groove":
        width = _feature_value(dims, "axial_width", "width") or parsed.get("slot_width") or max(2.0, round(base_outer * 0.08, 1))
        reduced = _feature_value(dims, "reduced_outer_diameter") or parsed.get("groove_diameter") or max(1.0, base_outer - max(2.0, round(base_outer * 0.08, 1)))
        z_start = _feature_value(dims, "z_start")
        if z_start is None:
            z_start = max(0.0, base_length - width) if any(token in text for token in ["顶部", "顶端", "末端", "端部", "top", "end"]) else max(0.0, base_length * 0.5)
        _assume_dimension(feature, "axial_width", width, "槽宽缺失，按外径约 8% 建立环槽")
        _assume_dimension(feature, "reduced_outer_diameter", reduced, "槽底外径缺失，按外径减少约 8% 建立环槽")
        _assume_dimension(feature, "z_start", z_start, "槽位置缺失，顶部槽默认贴近端面，否则放在长度中部")
        return

    if kind in {"through_hole", "blind_hole", "counterbore_hole"}:
        diameter = _feature_value(dims, "diameter", "hole_diameter") or parsed.get("diameter") or 6.0
        _assume_dimension(feature, "diameter", diameter, "孔径缺失，采用常见 6mm 概念孔径")
        if kind != "through_hole" and not _feature_value(dims, "depth"):
            _assume_dimension(feature, "depth", max(1.0, base_length * 0.5), "盲孔深度缺失，采用基体厚度/长度约 50%")
        if feature.extent is None:
            feature.extent = "through"
            feature.assumptions.append("未说明孔深，智能模式默认贯穿；可在属性面板改为盲孔。")
        if feature.placement.reference in {"needs_position", "origin"} and feature.placement.x is None and feature.placement.y is None:
            feature.placement.reference = "model_center"
            feature.placement.x = 0.0
            feature.placement.y = 0.0
            feature.assumptions.append("孔位缺失，智能模式默认放在主基准中心。")
        return

    if kind in {"rectangular_slot", "rectangular_pocket"}:
        length = _feature_value(dims, "length", "slot_length") or parsed.get("slot_length") or max(10.0, round(base_width * 0.5, 1))
        width = _feature_value(dims, "width", "slot_width") or parsed.get("slot_width") or max(3.0, round(base_width * 0.15, 1))
        depth = _feature_value(dims, "depth", "height") or (base_length if "贯穿" in text or "through" in text else max(1.0, base_length * 0.5))
        _assume_dimension(feature, "length", length, "槽长缺失，按基体宽度约 50% 建立概念槽")
        _assume_dimension(feature, "width", width, "槽宽缺失，按基体宽度约 15% 且不小于 3mm 建立概念槽")
        _assume_dimension(feature, "depth", depth, "槽深缺失，按用户是否提到贯穿决定")
        if feature.extent is None:
            feature.extent = "through" if depth >= base_length else "blind"
        return

    if policy == "aggressive_fill" and kind in {"boss_cylinder", "rectangular_pad", "rib_box"}:
        if kind == "boss_cylinder":
            _assume_dimension(feature, "diameter", _feature_value(dims, "diameter", "outer_diameter") or 12.0, "凸台直径缺失，采用常见 12mm 概念尺寸")
            _assume_dimension(feature, "height", _feature_value(dims, "height", "length") or max(3.0, base_length * 0.2), "凸台高度缺失，采用基体高度约 20%")
        else:
            _assume_dimension(feature, "length", _feature_value(dims, "length") or 20.0, "加料长度缺失，采用概念尺寸")
            _assume_dimension(feature, "width", _feature_value(dims, "width") or 10.0, "加料宽度缺失，采用概念尺寸")
            _assume_dimension(feature, "height", _feature_value(dims, "height", "depth") or 5.0, "加料高度缺失，采用概念尺寸")


def _assume_dimension(feature: FeatureV3, key: str, value: float, reason: str) -> None:
    existing = feature.dimensions.get(key)
    if existing is not None and existing.value is not None:
        return
    evidence = f"智能模式工程假设：{reason}"
    feature.dimensions[key] = DimensionV3(
        value=round(float(value), 3),
        unit="mm",
        evidence=evidence,
        source="assumption",
        confidence=0.55,
        confirmed_by_user=False,
    )
    feature.assumptions.append(evidence)


def _sync_assumption_details(plan: FeaturePlanV3) -> None:
    existing = {(item.feature_id, item.dimension) for item in plan.assumption_details}
    for feature in _all_features(plan):
        for name, dimension in feature.dimensions.items():
            if dimension.source != "assumption" or dimension.confirmed_by_user:
                continue
            if (feature.id, name) in existing:
                continue
            plan.assumption_details.append(
                DesignAssumption(
                    feature_id=feature.id,
                    dimension=name,
                    value=dimension.value,
                    reason=dimension.evidence or "AI 推断尺寸",
                    source="assumption",
                    confidence=dimension.confidence,
                    confirmed_by_user=False,
                )
            )


def _feature_value(dimensions: dict[str, DimensionV3], *keys: str) -> float | None:
    for key in keys:
        dimension = dimensions.get(key)
        if dimension is not None and dimension.value is not None:
            return float(dimension.value)
    return None


def _refresh_smart_resolution(plan: FeaturePlanV3) -> None:
    required = {
        "box_base": ("length", "width", "height"),
        "cylinder_base": ("outer_diameter", "length"),
        "hollow_cylinder": ("outer_diameter", "inner_diameter", "length"),
        "through_hole": ("diameter",),
        "blind_hole": ("diameter", "depth"),
        "counterbore_hole": ("diameter", "depth"),
        "rectangular_slot": ("length", "width", "depth"),
        "rectangular_pocket": ("length", "width", "depth"),
        "annular_groove": ("reduced_outer_diameter", "axial_width", "z_start"),
        "boss_cylinder": ("diameter", "height"),
        "rectangular_pad": ("length", "width", "height"),
        "rib_box": ("length", "width", "height"),
    }
    all_features = _all_features(plan)
    complete_ids: set[str] = set()
    for feature in all_features:
        names = required.get(feature.type)
        if names and all(_feature_value(feature.dimensions, name) is not None for name in names):
            complete_ids.add(feature.id)
            feature.unresolved = [item for item in feature.unresolved if "missing" not in item.lower() and "缺少" not in item]
    plan.unresolved = [item for item in plan.unresolved if str(item.get("feature")) not in complete_ids]


def _detect_family(text: str) -> str:
    if any(token in text for token in ["plate", "板", "板件", "平板", "连接板", "flat", "bracket"]):
        return "plate"
    if any(token in text for token in ["flange", "法兰"]):
        return "flange"
    if any(token in text for token in ["tube", "pipe", "管", "管件", "轴", "轴套", "套筒", "bushing", "杞", "濂"]):
        return "tube"
    return "unknown"


def _extract_dimension_clues(text: str) -> dict[str, float]:
    results: dict[str, float] = {}
    for pattern, key in _DIM_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                value = float(match.group(1))
                if key == "metric_thread":
                    results.setdefault("diameter", value)
                else:
                    results[key] = value
            except ValueError:
                continue
    triple = _BOX_TRIPLE_PATTERN.search(text)
    if triple:
        values = [float(item) for item in triple.groups()]
        results.setdefault("length", values[0])
        results.setdefault("width", values[1])
        results.setdefault("height", values[2])
    bare_diameter = re.search(r"(?:φ|Φ|Ø)\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)?", text)
    if bare_diameter:
        results.setdefault("diameter", float(bare_diameter.group(1)))
    return results


def _dims(**kwargs: Any) -> dict[str, DimensionV3]:
    evidence = str(kwargs.pop("evidence", ""))
    source = str(kwargs.pop("source", "unknown"))
    confirmed = bool(kwargs.pop("confirmed", False))
    dims: dict[str, DimensionV3] = {}
    for key, value in kwargs.items():
        dims[key] = DimensionV3(
            value=float(value) if value is not None else None,
            unit="mm",
            evidence=evidence,
            source=source,
            confirmed_by_user=confirmed,
        )
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


# Keep the public question builder at the end of the module so legacy plans
# with mojibake reasons still receive readable Chinese questions.
def questions_from_plan(plan: FeaturePlanV3) -> list[ClarificationQuestion]:
    questions: list[ClarificationQuestion] = []
    for item in plan.unresolved[:8]:
        feature_id = str(item.get("feature", "unknown_feature"))
        reason = str(item.get("reason", "缺少必要尺寸或定位信息"))
        feature = next((candidate for candidate in _all_features(plan) if candidate.id == feature_id), None)
        kind = feature.type if feature else ""
        missing = [name for name, dimension in (feature.dimensions.items() if feature else []) if dimension.value is None]
        if kind == "annular_groove" or "groove" in reason.lower() or "环槽" in reason:
            text = f"特征 {feature_id} 是环槽，但还缺少可执行尺寸。请填写槽宽、槽底外径和轴向位置。"
            options = ["跳过环槽", "重新上传带尺寸标注的图"]
            answer_type = "text"
            refs = ["槽宽", "槽底外径", "起始位置 Z"]
            impact = "未确认时只生成已确认的主体，环槽不会进入生产模型。"
        elif kind in {"through_hole", "blind_hole", "counterbore_hole"} or "hole" in reason.lower() or "孔" in reason:
            text = f"特征 {feature_id} 是孔，但还缺少孔径、中心位置或孔深。请按“孔径 6mm，X=20mm，Y=0mm，贯穿”回答。"
            options = ["贯穿孔", "盲孔", "跳过此孔"]
            answer_type = "text"
            refs = ["孔径", "X/Y 位置", "贯穿或深度"]
            impact = "未确认时跳过此孔，避免生成孔径或位置错误的模型。"
        elif "position" in reason.lower() or "定位" in reason:
            text = f"特征 {feature_id} 的定位还不明确。请说明它相对哪个基准面，以及 X/Y/Z 距离。"
            options = ["相对底面", "相对中心线", "跳过此特征"]
            answer_type = "text"
            refs = ["基准", "X/Y/Z 位置"]
            impact = "未确认时不会执行该特征。"
        else:
            missing_text = "、".join(missing) if missing else "尺寸或定位"
            text = f"特征 {feature_id} 还缺少 {missing_text}。请填写明确的毫米值，或选择跳过。"
            options = ["补充尺寸", "跳过此特征"]
            answer_type = "text"
            refs = missing or ["必要尺寸"]
            impact = "未确认时该特征会被跳过，并在执行报告中保留原因。"
        questions.append(
            ClarificationQuestion(
                text=text,
                feature_id=feature_id,
                dimension_refs=refs,
                required=True,
                options=options,
                reason=reason,
                impact=impact,
                answer_type=answer_type,
                unit="mm",
            )
        )
    return questions
