from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any
from uuid import uuid4

from PIL import Image

from backend.mechcad_ai import planner as ai_planner
from backend.mechcad_ai import vision as ai_vision
from backend.process import ProcessRecorder
from backend.capabilities import (
    CapabilityValidationError,
    validate_feature_edit_set,
    validate_feature_operation,
    validate_feature_patch,
)
from backend.validation import order_feature_plan
from backend.schemas import (
    ClarificationQuestion,
    DesignAssumption,
    DesignReview,
    DimensionV3,
    FeatureEditOperation,
    FeatureEditSet,
    FeaturePlanV3,
    FeatureV3,
    GenerateRequest,
    PlacementV3,
    ProcessStep,
)


_NUM = r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)?"
_SEP = r"\s*(?:[:=：]|(?:改为|改成|调为|设为|到|加宽到|加长到|加大到|to))?\s*"
_DIAMETER_PREFIX = r"(?:φ|Φ|Ø)?\s*"

# Keep a few mojibake aliases because old tests and historical user inputs
# contain text that was decoded with the wrong charset.
_DIM_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?:槽宽|槽的宽度|slot\s*width){_SEP}{_NUM}", re.IGNORECASE), "slot_width"),
    (re.compile(rf"(?:槽长|槽的长度|slot\s*length){_SEP}{_NUM}", re.IGNORECASE), "slot_length"),
    (re.compile(rf"(?:槽深|槽的深度|slot\s*depth){_SEP}{_NUM}", re.IGNORECASE), "slot_depth"),
    (re.compile(rf"(?:槽底外径|槽径|groove\s*diameter){_SEP}{_DIAMETER_PREFIX}{_NUM}", re.IGNORECASE), "groove_diameter"),
    (re.compile(rf"(?:外径|外圆直径|outer\s*diameter|od){_SEP}{_DIAMETER_PREFIX}{_NUM}", re.IGNORECASE), "outer_diameter"),
    (re.compile(rf"(?:内径|孔内径|inner\s*diameter|id|bore){_SEP}{_DIAMETER_PREFIX}{_NUM}", re.IGNORECASE), "inner_diameter"),
    (re.compile(rf"(?:中心孔径|中心孔直径|中心孔|孔径|孔直径|hole\s*diameter|center\s*(?:through\s*)?(?:hole|bore)){_SEP}{_DIAMETER_PREFIX}{_NUM}", re.IGNORECASE), "hole_diameter"),
    (re.compile(rf"{_NUM}\s*(?:中心孔|center\s*(?:through\s*)?(?:hole|bore))\s*[。.]?$", re.IGNORECASE), "hole_diameter"),
    (re.compile(rf"(?:直径|(?<!outer\s)(?<!inner\s)diameter){_SEP}{_DIAMETER_PREFIX}{_NUM}", re.IGNORECASE), "diameter"),
    (re.compile(rf"(?:长|长度|总长|overall\s*length|length|L){_SEP}{_NUM}", re.IGNORECASE), "length"),
    (re.compile(rf"(?:宽|宽度|width|W){_SEP}{_NUM}", re.IGNORECASE), "width"),
    (re.compile(rf"(?:厚|厚度|高度|height|thickness|T){_SEP}{_NUM}", re.IGNORECASE), "height"),
    (re.compile(rf"(?:深度|depth){_SEP}{_NUM}", re.IGNORECASE), "depth"),
    (re.compile(rf"\bM\s*{_NUM}\b", re.IGNORECASE), "metric_thread"),
]

_BOX_TRIPLE_PATTERN = re.compile(rf"{_NUM}\s*[xX×]\s*{_NUM}\s*[xX×]\s*{_NUM}")


def _loc(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh


def build_initial_feature_plan(
    description: str,
    request: GenerateRequest,
    image: Image.Image | None = None,
    settings=None,
    language: str = "zh",
    recorder=None,
) -> tuple[FeaturePlanV3, list[ClarificationQuestion]]:
    """Build the first FeaturePlanV3 for a generation request."""
    if settings is None:
        settings = request.settings
    if settings is not None:
        vision_json = None
        vision_step = None
        if recorder is not None:
            if image is None:
                vision_step = recorder.started(
                    "vision",
                    _loc(language, "视觉读图", "Vision analysis"),
                    summary=_loc(language, "没有可用图片，跳过视觉读图", "No image available; skipping vision analysis"),
                )
                recorder.skipped(vision_step, reason=_loc(language, "未提供图片", "No image provided"))
            else:
                vision_step = recorder.started(
                    "vision",
                    _loc(language, "视觉读图", "Vision analysis"),
                    summary=_loc(language, "正在分析手绘草图", "Analyzing hand-drawn sketch"),
                )
        if image is not None:
            try:
                vision_json = ai_vision.analyze_sketch(image, description, settings, language=language)
                if recorder is not None and vision_step is not None:
                    recorder.completed(vision_step, summary=_loc(language, "视觉读图完成", "Vision analysis completed"))
            except Exception as exc:
                if recorder is not None and vision_step is not None:
                    recorder.failed(vision_step, error=str(exc))

        plan_step = None
        if recorder is not None:
            plan_step = recorder.started(
                "planning",
                _loc(language, "特征规划", "Feature planning"),
                summary=_loc(language, "正在生成 FeaturePlanV3", "Generating FeaturePlanV3"),
            )
        try:
            plan = ai_planner.generate_feature_plan(
                description,
                vision_json,
                settings,
                mode=settings.operation_mode or "strict",
                smart_fill_policy=settings.smart_fill_policy or "limited_fill",
                language=language,
            )
        except Exception as exc:
            if recorder is not None and plan_step is not None:
                recorder.failed(plan_step, error=str(exc))
            plan = None
        if recorder is not None and plan_step is not None:
            if plan is not None:
                recorder.completed(plan_step, summary=_loc(language, "FeaturePlan 生成完成", "FeaturePlan generated"))
            else:
                recorder.skipped(plan_step, reason=_loc(language, "未配置模型或模型返回无效，使用本地确定性规划", "Model not configured or returned invalid output; using local deterministic planning"))
        if plan is not None:
            if settings.operation_mode == "smart":
                plan = _apply_smart_autonomy(plan, description, settings.smart_fill_policy or "limited_fill", language)
            order_feature_plan(plan)
            return plan, questions_from_plan(plan, language)
    if recorder is not None and image is None and settings is not None:
        pass
    plan, questions = _build_stub_feature_plan(description, request, language)
    if settings is not None and settings.operation_mode == "smart":
        plan = _apply_smart_autonomy(plan, description, settings.smart_fill_policy or "limited_fill", language)
        questions = questions_from_plan(plan, language)
    order_feature_plan(plan)
    return plan, questions


def _build_stub_feature_plan(description: str, request: GenerateRequest, language: str = "zh") -> tuple[FeaturePlanV3, list[ClarificationQuestion]]:
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
        _collect_missing(plan, "base_plate", plan.base_feature.dimensions, ["length", "width", "height"], _loc(language, "板件主尺寸缺失", "Plate main dimensions are missing"))
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
        _collect_missing(plan, "base_flange", plan.base_feature.dimensions, ["outer_diameter", "length"], _loc(language, "法兰主外形缺失", "Flange main outline is missing"))
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
        _collect_missing(plan, "base_tube", plan.base_feature.dimensions, ["outer_diameter", "inner_diameter", "length"], _loc(language, "管件主尺寸缺失", "Tube main dimensions are missing"))
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
        _collect_missing(plan, "base_body", plan.base_feature.dimensions, ["length", "width", "height"], _loc(language, "主基体尺寸缺失", "Main body dimensions are missing"))

    _add_stub_feature_candidates(plan, text, parsed, language)

    plan.design_review = DesignReview(
        warnings=[_loc(language, "严格模式不会把推断尺寸写成可执行事实；未确认尺寸会停留在待确认问题或设计建议中。", "Strict mode does not turn inferred dimensions into executable facts; unconfirmed dimensions remain in pending questions or design suggestions.")],
        suggestions=[
            _loc(language, "先确认整体外形，再补孔、槽、台阶、凸台等局部特征。", "Confirm the overall outline first, then add holes, slots, steps, bosses, and other local features."),
            _loc(language, "如果图纸上有尺寸标注，请优先回填标注值，不要让系统自行猜测。", "If the drawing has dimension annotations, enter them instead of letting the system guess."),
        ],
        manufacturability=[_loc(language, "建议按：基体 -> 减料 -> 加料 -> 阵列 -> 圆角/倒角 的顺序建模。", "Model in this order: body -> cuts -> adds -> patterns -> fillets/chamfers.")],
        standards=[_loc(language, "孔、槽、台阶、壁厚等应尽量绑定到图中可见标注或用户确认值。", "Holes, slots, steps, and wall thickness should be bound to visible annotations or user-confirmed values.")],
        requires_confirmation=bool(plan.unresolved),
    )

    if mode == "smart":
        plan.assumptions.append(_loc(language, "智能模式允许生成设计建议，但默认不把推断尺寸直接写入可执行模型。", "Smart mode may generate design suggestions, but inferred dimensions are not written into the executable model by default."))
        if not plan.unresolved:
            plan.design_review.suggestions.append(_loc(language, "当前信息足够进入受控建模。", "Current information is sufficient to enter controlled modeling."))

    return plan, questions_from_plan(plan, language)


def apply_chat_edit(
    plan: FeaturePlanV3, message: str, settings=None, language: str = "zh"
) -> tuple[FeaturePlanV3, list[ClarificationQuestion], FeatureEditSet, list[ProcessStep]]:
    """Apply a natural-language edit as feature-level operations.

    Returns the updated plan, derived questions, the edit set that was applied,
    and one ProcessStep per operation for the process timeline.
    """
    edit_set: FeatureEditSet | None = None
    if settings is not None:
        edit_set = ai_planner.chat_edit_operations(plan, message, settings, language=language)
    if edit_set is None:
        edit_set = _local_feature_edit(plan, message, language)

    updated, questions, steps = apply_feature_operations(plan, edit_set, settings, language=language)
    return updated, questions, edit_set, steps


def apply_feature_operations(
    plan: FeaturePlanV3,
    edit_set: FeatureEditSet,
    settings=None,
    language: str = "zh",
) -> tuple[FeaturePlanV3, list[ClarificationQuestion], list[ProcessStep]]:
    """Apply a validated FeatureEditSet without replacing the whole plan."""
    updated = deepcopy(plan)
    mode = getattr(settings, "operation_mode", "strict") if settings is not None else "strict"
    questions = [q for q in edit_set.questions]
    recorder = ProcessRecorder("local", language)
    validate_feature_edit_set(edit_set, plan)

    for op in edit_set.operations:
        step = ProcessStep(
            stage="chat_edit",
            status="running",
            label=_op_label(op, language),
            summary=_loc(language, "正在应用特征级修改", "Applying feature-level edit"),
            feature_id=op.feature_id,
            operation=op.op,
        )
        capability_issues = validate_feature_operation(op, plan)
        if capability_issues:
            reasons = "; ".join(f"{issue.error_code}: {issue.reason}" for issue in capability_issues)
            step.detail = json.dumps([issue.model_dump() for issue in capability_issues], ensure_ascii=False)
            recorder.blocked(
                step,
                reason=_loc(language, "能力校验未通过：", "Capability check failed: ") + reasons,
            )
            recorder.append(step)
            continue
        if op.op == "add":
            _apply_add_operation(updated, op, mode, language, recorder, step, questions)
        elif op.op == "update":
            _apply_update_operation(updated, op, mode, language, recorder, step)
        elif op.op == "delete":
            _apply_delete_operation(updated, op, language, recorder, step, questions)
        elif op.op == "change_type":
            _apply_change_type_operation(updated, op, mode, language, recorder, step)
        recorder.append(step)

    _sync_assumption_details(updated)
    _refresh_smart_resolution(updated)
    order_feature_plan(updated)
    combined = list(questions)
    existing = {q.id for q in combined}
    for q in questions_from_plan(updated, language):
        if q.id not in existing:
            combined.append(q)
    return updated, combined, recorder.steps


def _op_label(op: FeatureEditOperation, language: str) -> str:
    labels = {
        "add": _loc(language, "新增特征", "Add feature"),
        "update": _loc(language, "修改特征", "Modify feature"),
        "delete": _loc(language, "删除特征", "Delete feature"),
        "change_type": _loc(language, "转换特征类型", "Change feature type"),
    }
    return labels.get(op.op, op.op)


def _feature_snapshot(feature: FeatureV3 | None) -> dict[str, Any] | None:
    if feature is None:
        return None
    return {
        "id": feature.id,
        "type": feature.type,
        "dimensions": {key: dim.model_dump() for key, dim in feature.dimensions.items()},
        "placement": feature.placement.model_dump(),
        "extent": feature.extent,
        "depends_on": list(feature.depends_on),
    }


def _apply_add_operation(
    plan: FeaturePlanV3,
    op: FeatureEditOperation,
    mode: str,
    language: str,
    recorder: ProcessRecorder,
    step: ProcessStep,
    questions: list,
) -> None:
    if op.type is None or op.type in {"box_base", "cylinder_base", "hollow_cylinder", "revolved_axial_profile"}:
        recorder.blocked(step, reason=_loc(language, "不允许通过聊天新增主基体，请先确认整体外形", "Main body cannot be added via chat; confirm the overall outline first"))
        return
    if mode == "strict" and any(dim.source == "assumption" and not dim.confirmed_by_user for dim in op.dimensions.values()):
        recorder.blocked(step, reason=_loc(language, "严格模式禁止未确认的推断尺寸", "Strict mode rejects unconfirmed inferred dimensions"))
        return
    base_id = plan.base_feature.id if plan.base_feature else None
    depends = [dep for dep in op.depends_on if _find_feature(plan, dep) is not None] or ([base_id] if base_id else [])
    missing_deps = [dep for dep in op.depends_on if _find_feature(plan, dep) is None]
    if missing_deps:
        recorder.blocked(step, reason=_loc(language, f"依赖特征不存在：{', '.join(missing_deps)}", f"Missing dependency: {', '.join(missing_deps)}"))
        return

    feature_id = op.feature_id or f"{op.type}_{uuid4().hex[:6]}"
    if _find_feature(plan, feature_id) is not None:
        feature_id = f"{op.type}_{uuid4().hex[:6]}"
    feature = FeatureV3(
        id=feature_id,
        type=op.type,
        operation="add" if not op.type.startswith("base") else "base",
        dimensions=deepcopy(op.dimensions),
        placement=deepcopy(op.placement) if op.placement else PlacementV3(reference="model_center", axis="Z"),
        extent=op.extent,
        depends_on=depends,
        evidence=op.evidence or _loc(language, "用户通过聊天要求新增特征", "User requested this feature through chat"),
        confirmed_by_user=op.confirmed_by_user,
    )
    if op.source == "assumption" and not op.confirmed_by_user:
        feature.assumptions.append(_loc(language, "该特征由 AI 推断，未经用户确认", "This feature is AI-inferred and not user-confirmed"))
    plan.features.append(feature)
    _record_missing(plan, feature, language)
    recorder.completed(step, 
        summary=_loc(language, f"已新增特征 {feature_id}（{op.type}）", f"Added feature {feature_id} ({op.type})"),
        changed={"before": None, "after": _feature_snapshot(feature)},
        warnings=feature.unresolved,
    )


def _apply_update_operation(
    plan: FeaturePlanV3,
    op: FeatureEditOperation,
    mode: str,
    language: str,
    recorder: ProcessRecorder,
    step: ProcessStep,
) -> None:
    feature = _find_feature(plan, op.feature_id)
    if feature is None:
        recorder.failed(step, error=_loc(language, f"找不到特征 {op.feature_id}", f"Feature {op.feature_id} not found"))
        return
    before = _feature_snapshot(feature)
    changed_keys: list[str] = []
    for key, dim in op.dimensions.items():
        if mode == "strict" and dim.source == "assumption" and not dim.confirmed_by_user:
            continue
        feature.dimensions[key] = dim
        changed_keys.append(key)
        _clear_unresolved(plan, feature.id, key)
    if op.placement is not None:
        feature.placement = op.placement
        changed_keys.append("placement")
        _clear_unresolved(plan, feature.id, "position")
    if op.extent is not None:
        feature.extent = op.extent
        changed_keys.append("extent")
    after = _feature_snapshot(feature)
    if not changed_keys:
        recorder.blocked(step, reason=_loc(language, "严格模式下未确认推断不会被应用", "Unconfirmed inference was not applied in strict mode"))
        return
    _record_missing(plan, feature, language)
    recorder.completed(step, 
        summary=_loc(language, f"已更新特征 {feature.id}：{', '.join(changed_keys)}", f"Updated feature {feature.id}: {', '.join(changed_keys)}"),
        changed={"before": before, "after": after},
        warnings=feature.unresolved,
    )


def _apply_delete_operation(
    plan: FeaturePlanV3,
    op: FeatureEditOperation,
    language: str,
    recorder: ProcessRecorder,
    step: ProcessStep,
    questions: list,
) -> None:
    feature = _find_feature(plan, op.feature_id)
    if feature is None:
        recorder.failed(step, error=_loc(language, f"找不到特征 {op.feature_id}", f"Feature {op.feature_id} not found"))
        return
    if feature is plan.base_feature:
        recorder.blocked(step, reason=_loc(language, "主基体不能通过聊天删除，请新建项目或重新生成", "The main body cannot be deleted via chat; create a new project or regenerate"))
        return
    children = [candidate for candidate in plan.features if feature.id in candidate.depends_on]
    if children and not op.cascade:
        names = ", ".join(child.id for child in children)
        questions.append(
            ClarificationQuestion(
                text=_loc(language, f"删除 {feature.id} 会让 {names} 失去父实体。是否同时删除这些子特征？", f"Deleting {feature.id} would orphan {names}. Delete its children too?"),
                feature_id=feature.id,
                dimension_refs=[],
                required=True,
                options=_loc(language, ["同时删除子特征", "保留父特征"], ["Delete children too", "Keep parent feature"]),
                reason=_loc(language, "依赖关系需要确认", "Dependency graph requires confirmation"),
                impact=_loc(language, "不确认时不会执行删除", "Delete will not execute until confirmed"),
                answer_type="choice",
            )
        )
        recorder.blocked(step, reason=_loc(language, f"特征 {feature.id} 仍有子特征，等待用户确认", f"Feature {feature.id} still has children; waiting for user confirmation"))
        return
    to_remove = {feature.id}
    if op.cascade:
        changed = True
        while changed:
            changed = False
            for candidate in plan.features:
                if candidate.id in to_remove:
                    continue
                if any(dep in to_remove for dep in candidate.depends_on):
                    to_remove.add(candidate.id)
                    changed = True
    before = _feature_snapshot(feature)
    plan.features = [candidate for candidate in plan.features if candidate.id not in to_remove]
    plan.unresolved = [item for item in plan.unresolved if str(item.get("feature")) not in to_remove]
    recorder.completed(step, 
        summary=_loc(language, f"已删除特征 {feature.id}", f"Deleted feature {feature.id}"),
        detail=_loc(language, f"同时删除：{', '.join(sorted(to_remove - {feature.id}))}", f"Also deleted: {', '.join(sorted(to_remove - {feature.id}))}") if len(to_remove) > 1 else "",
        changed={"before": before, "after": None},
    )


def _apply_change_type_operation(
    plan: FeaturePlanV3,
    op: FeatureEditOperation,
    mode: str,
    language: str,
    recorder: ProcessRecorder,
    step: ProcessStep,
) -> None:
    feature = _find_feature(plan, op.feature_id)
    if feature is None:
        recorder.failed(step, error=_loc(language, f"找不到特征 {op.feature_id}", f"Feature {op.feature_id} not found"))
        return
    hole_family = {"through_hole", "blind_hole", "counterbore_hole"}
    slot_family = {"rectangular_pocket", "rectangular_slot"}
    allowed = (feature.type in hole_family and op.type in hole_family) or (feature.type in slot_family and op.type in slot_family)
    if not allowed or op.type is None:
        recorder.blocked(step, reason=_loc(language, f"不允许从 {feature.type} 转为 {op.type}；仅支持孔族或槽/腔族内转换", f"Cannot convert {feature.type} to {op.type}; only hole-family or pocket/slot-family conversions are allowed"))
        return
    before = _feature_snapshot(feature)
    feature.type = op.type
    for key, dim in op.dimensions.items():
        if mode == "strict" and dim.source == "assumption" and not dim.confirmed_by_user:
            continue
        feature.dimensions[key] = dim
        _clear_unresolved(plan, feature.id, key)
    if op.placement is not None:
        feature.placement = op.placement
    if op.extent is not None:
        feature.extent = op.extent
    _record_missing(plan, feature, language)
    recorder.completed(step, 
        summary=_loc(language, f"特征 {feature.id} 已转为 {op.type}", f"Feature {feature.id} converted to {op.type}"),
        changed={"before": before, "after": _feature_snapshot(feature)},
        warnings=feature.unresolved,
    )


def _find_feature(plan: FeaturePlanV3, feature_id: str | None) -> FeatureV3 | None:
    if not feature_id:
        return None
    return next((feature for feature in _all_features(plan) if feature.id == feature_id), None)


def _record_missing(plan: FeaturePlanV3, feature: FeatureV3, language: str) -> None:
    required = _required_dimensions(feature.type)
    missing = [name for name in required if feature.dimensions.get(name) is None or feature.dimensions[name].value is None]
    if not missing:
        return
    reason = _loc(language, f"{feature.type} 缺少可执行尺寸：{', '.join(missing)}", f"{feature.type} is missing executable dimensions: {', '.join(missing)}")
    feature.unresolved.append(reason)
    plan.unresolved.append({"feature": feature.id, "reason": reason})


def _required_dimensions(feature_type: str) -> list[str]:
    return {
        "box_base": ["length", "width", "height"],
        "cylinder_base": ["outer_diameter", "length"],
        "hollow_cylinder": ["outer_diameter", "inner_diameter", "length"],
        "through_hole": ["diameter"],
        "blind_hole": ["diameter", "depth"],
        "counterbore_hole": ["diameter", "depth"],
        "rectangular_slot": ["length", "width", "depth"],
        "rectangular_pocket": ["length", "width", "depth"],
        "annular_groove": ["reduced_outer_diameter", "axial_width", "z_start"],
        "boss_cylinder": ["diameter", "height"],
        "rectangular_pad": ["length", "width", "height"],
        "rib_box": ["length", "width", "height"],
        "linear_pattern": ["count", "spacing", "diameter"],
        "circular_pattern": ["count", "pitch_radius", "diameter"],
    }.get(feature_type, [])


def _local_feature_edit(plan: FeaturePlanV3, message: str, language: str = "zh") -> FeatureEditSet:
    """Deterministic fallback that turns common edits into feature operations."""
    text = message.strip()
    if not text:
        return FeatureEditSet()
    lowered = text.lower()
    operations: list[FeatureEditOperation] = []
    questions: list[ClarificationQuestion] = []
    is_delete = any(token in text for token in ["删除", "去掉", "移除", "remove", "delete"])
    is_add = any(token in text for token in ["新增", "添加", "增加", "add", "create"])
    is_move = "移动" in text or "move" in lowered
    is_update = any(token in text for token in ["改成", "改为", "修改", "调为", "设为", "调整", "变为"]) or any(token in lowered for token in ["change", "update", "modify", "edit"])


    if "删除" in text or "去掉" in text or "移除" in text:
        target = _pick_delete_target(plan, text, language)
        if target is None:
            questions.append(
                ClarificationQuestion(
                    text=_loc(language, "你想删除哪个特征？请说明特征名称或类型，例如“删除顶部槽”。", "Which feature do you want to delete? Name it, for example: delete the top groove."),
                    required=True,
                    reason=_loc(language, "删除目标不明确", "Delete target is ambiguous"),
                    impact=_loc(language, "未确认前不会执行删除", "No deletion will run before confirmation"),
                    answer_type="text",
                )
            )
        else:
            operations.append(FeatureEditOperation(op="delete", feature_id=target.id, reason=_loc(language, f"用户请求删除 {target.id}", f"User requested deletion of {target.id}")))

    parsed = _extract_dimension_clues(text)
    if not is_add and not is_delete and parsed:
        target = _pick_update_target(plan, text, parsed, language)
        if target is None:
            questions.append(
                ClarificationQuestion(
                    text=_loc(language, "你想修改哪个特征？请说明特征名称或类型。", "Which feature do you want to modify? Name it or its type."),
                    required=True,
                    reason=_loc(language, "修改目标不明确", "Modify target is ambiguous"),
                    impact=_loc(language, "未确认前不会修改", "No change will run before confirmation"),
                    answer_type="text",
                )
            )
        else:
            dimensions = _matching_dimensions(target, parsed, language)
            if dimensions:
                operations.append(
                    FeatureEditOperation(
                        op="update",
                        feature_id=target.id,
                        dimensions=dimensions,
                        evidence=_loc(language, f"用户聊天：{text}", f"User chat: {text}"),
                        source="user",
                        confirmed_by_user=True,
                    )
                )

    if is_move and not is_add:
        move_target = _pick_update_target(plan, text, parsed, language)
        move_op = _move_feature_operation(move_target, text, language)
        if move_op is not None:
            operations.append(move_op)
        else:
            questions.append(
                ClarificationQuestion(
                    text=_loc(language, "要移动的孔缺少当前中心位置或移动量。请说明“向内/向外移动多少 mm”。", "The hole to move lacks a current center position or distance. Say: move inward/outward by N mm."),
                    required=True,
                    feature_id=move_target.id if move_target else None,
                    reason=_loc(language, "孔位或移动量不明确", "Hole position or move distance is unclear"),
                    impact=_loc(language, "未确认前不会移动孔", "The hole will not move until confirmed"),
                    answer_type="text",
                    unit="mm",
                )
            )

    if is_add:
        operations.extend(_local_add_operations(plan, text, parsed, language))


    if not operations and not questions:
        questions.append(
            ClarificationQuestion(
                text=_loc(language, "你想修改哪个特征？请说明特征名称或类型。", "Which feature do you want to modify? Name it or its type."),
                reason=_loc(language, "修改目标不明确", "Modify target is ambiguous"),
                impact=_loc(language, "未确认前不会修改", "No change will run before confirmation"),
                answer_type="text",
            )
        )
    return FeatureEditSet(operations=operations, questions=questions, message=text)


def _pick_delete_target(plan: FeaturePlanV3, text: str, language: str) -> FeatureV3 | None:
    features = _all_features(plan)
    if any(token in text for token in ["槽", "groove", "slot"]):
        candidates = [f for f in features if f.type in {"annular_groove", "rectangular_slot", "rectangular_pocket"}]
    elif any(token in text for token in ["孔", "hole"]):
        candidates = [f for f in features if f.type in {"through_hole", "blind_hole", "counterbore_hole"}]
    elif any(token in text for token in ["凸台", "boss"]):
        candidates = [f for f in features if f.type == "boss_cylinder"]
    else:
        return None
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        for candidate in candidates:
            if candidate.id in text or any(hint in text for hint in [candidate.id, "顶部", "top"]):
                return candidate
    return None


def _pick_update_target(plan: FeaturePlanV3, text: str, parsed: dict[str, float], language: str) -> FeatureV3 | None:
    features = _all_features(plan)
    if any(token in text for token in ["槽", "groove", "slot"]):
        candidates = [f for f in features if f.type in {"annular_groove", "rectangular_slot", "rectangular_pocket"}]
        return candidates[0] if len(candidates) == 1 else (next((f for f in candidates if f.id in text), None))
    if any(token in text for token in ["孔", "hole"]):
        candidates = [f for f in features if f.type in {"through_hole", "blind_hole", "counterbore_hole", "circular_pattern", "linear_pattern"}]
        if "中心" in text or "center" in text.lower():
            center = next((f for f in candidates if "center" in f.id or "中心" in f.id), None)
            if center:
                return center
        return candidates[0] if len(candidates) == 1 else None
    if any(key in parsed for key in ("length", "width", "height", "outer_diameter", "inner_diameter")):
        return plan.base_feature
    if ("移动" in text or "move" in text.lower()) and any(token in text for token in ["孔", "hole"]):
        candidates = [f for f in features if f.type in {"through_hole", "blind_hole", "counterbore_hole"}]
        return candidates[0] if len(candidates) == 1 else None
    return None


def _matching_dimensions(feature: FeatureV3, parsed: dict[str, float], language: str) -> dict[str, DimensionV3]:
    aliases = {
        "length": ("length", "slot_length"),
        "width": ("width", "slot_width"),
        "height": ("height", "depth", "thickness"),
        "outer_diameter": ("outer_diameter", "diameter"),
        "inner_diameter": ("inner_diameter",),
        "diameter": ("diameter", "hole_diameter"),
        "hole_diameter": ("diameter",),
        "slot_width": ("axial_width", "width", "slot_width"),
        "slot_length": ("length", "slot_length"),
        "slot_depth": ("depth", "height"),
        "groove_diameter": ("reduced_outer_diameter", "groove_diameter"),
        "depth": ("depth",),
    }
    result: dict[str, DimensionV3] = {}
    if feature.type in {"through_hole", "blind_hole", "counterbore_hole"} and parsed.get("width") and "diameter" in feature.dimensions:
        result["diameter"] = DimensionV3(
            value=parsed["width"],
            unit="mm",
            evidence=_loc(language, "用户聊天中的明确尺寸", "Explicit dimension from user chat"),
            source="user",
            confirmed_by_user=True,
        )
    for parsed_key, value in parsed.items():
        for target_key in aliases.get(parsed_key, []):
            if target_key in feature.dimensions:
                result[target_key] = DimensionV3(
                    value=value,
                    unit="mm",
                    evidence=_loc(language, "用户聊天中的明确尺寸", "Explicit dimension from user chat"),
                    source="user",
                    confirmed_by_user=True,
                )
                break
    return result



def _move_feature_operation(feature: FeatureV3 | None, text: str, language: str) -> FeatureEditOperation | None:
    if feature is None:
        return None
    match = re.search(r"(?:移动|move)\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)?", text, re.IGNORECASE)
    if not match:
        return None
    distance = float(match.group(1))
    outward = any(token in text for token in ["向外", "outward", "外移"])
    x = feature.placement.x or 0.0
    y = feature.placement.y or 0.0
    z = feature.placement.z or 0.0
    if abs(x) >= abs(y) and x != 0:
        new_x = x + distance if outward else (x - distance if x > 0 else x + distance)
        new_y = y
    elif y != 0:
        new_x = x
        new_y = y + distance if outward else (y - distance if y > 0 else y + distance)
    else:
        return None
    return FeatureEditOperation(
        op="update",
        feature_id=feature.id,
        placement=PlacementV3(reference=feature.placement.reference, x=new_x, y=new_y, z=z, axis=feature.placement.axis),
        evidence=_loc(language, f"用户聊天：{text}", f"User chat: {text}"),
        source="user",
        confirmed_by_user=True,
    )


def _local_add_operations(plan: FeaturePlanV3, text: str, parsed: dict[str, float], language: str) -> list[FeatureEditOperation]:
    lowered = text.lower()
    diameter = parsed.get("hole_diameter") or parsed.get("diameter") or parsed.get("metric_thread")
    count = _extract_count(text)
    if "m6" in lowered:
        diameter = 6.0
    elif "m8" in lowered:
        diameter = 8.0
    elif "m10" in lowered:
        diameter = 10.0
    if not diameter:
        return []

    wants_pattern = (count is not None and count > 1) or "阵列" in text or "pattern" in lowered
    if wants_pattern:
        dimensions: dict[str, float] = {"diameter": diameter}
        if count is not None:
            dimensions["count"] = float(count)
        if "线性" in text or "linear" in lowered or (plan.part_family == "plate" and "圆周" not in text and "circular" not in lowered):
            feature_type = "linear_pattern"
        else:
            feature_type = "circular_pattern"
        return [FeatureEditOperation(op="add", type=feature_type, dimensions=_dimension_map(dimensions, language), evidence=_loc(language, f"用户请求新增阵列：{text}", f"User requested pattern: {text}"))]

    return [FeatureEditOperation(op="add", type="through_hole", dimensions=_dimension_map({"diameter": diameter}, language), evidence=_loc(language, f"用户请求新增孔：{text}", f"User requested hole: {text}"))]



def _extract_count(text: str) -> int | None:
    match = re.search(r"(\d+)\s*(?:个|只|处|holes?\b|x\s*(\d+))", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match = re.search(r"\b(\d+)\s*(?:个|只|处)", text)
    if match:
        return int(match.group(1))
    return None


def _dimension_map(values: dict[str, float], language: str) -> dict[str, DimensionV3]:
    return {
        key: DimensionV3(
            value=value,
            unit="mm",
            evidence=_loc(language, "用户聊天中的明确尺寸", "Explicit dimension from user chat"),
            source="user",
            confirmed_by_user=True,
        )
        for key, value in values.items()
    }


def patch_feature(plan: FeaturePlanV3, feature_id: str, patch: dict[str, Any]) -> FeaturePlanV3:
    updated = deepcopy(plan)
    target = _find_feature(updated, feature_id)
    if target is None:
        return updated
    capability_issues = validate_feature_patch(target.type, patch.get("dimensions"), patch.get("placement"))
    if capability_issues:
        raise CapabilityValidationError(capability_issues)
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
    order_feature_plan(updated)
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
    plan: FeaturePlanV3, answers_text: str, language: str = "zh"
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
                    evidence=_loc(language, f"用户回答澄清问题：{answers_text}", f"User answered clarification: {answers_text}"),
                    source="user",
                    confirmed_by_user=True,
                )
                _clear_unresolved(updated, feature.id, key)
        if "贯穿" in answers_text or "through" in lowered:
            feature.extent = "through"
            feature.unresolved = [item for item in feature.unresolved if "through" not in item.lower() and "贯穿" not in item]
        if "跳过" in answers_text or "不建" in answers_text:
            feature.unresolved.append(_loc(language, "用户选择暂不执行该特征", "User chose not to execute this feature"))
    return updated


def _missing_dimension_refs(reason: str, language: str = "zh") -> list[str]:
    refs = []
    mapping = {
        "outer_diameter": _loc(language, "外径", "outer diameter"),
        "inner_diameter": _loc(language, "内径", "inner diameter"),
        "length": _loc(language, "长度", "length"),
        "width": _loc(language, "宽度", "width"),
        "height": _loc(language, "厚度/高度", "thickness/height"),
        "diameter": _loc(language, "孔径/直径", "hole diameter"),
        "axial_width": _loc(language, "槽宽", "slot width"),
        "reduced_outer_diameter": _loc(language, "槽底外径", "groove root diameter"),
        "z_start": _loc(language, "槽起点位置", "slot start position"),
        "hole position": _loc(language, "孔中心位置", "hole center position"),
        "through/blind extent": _loc(language, "贯穿或深度", "through or depth"),
    }
    for key, label in mapping.items():
        if key in reason:
            refs.append(label)
    return refs


def _add_stub_feature_candidates(plan: FeaturePlanV3, text: str, parsed: dict[str, float], language: str = "zh") -> None:
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
            plan.unresolved.append(
                {
                    "feature": feature.id,
                    "reason": _loc(
                        language,
                        "环槽还不能建模，需要补充: " + ", ".join(missing),
                        "Annular groove cannot be modeled yet; provide: " + ", ".join(missing),
                    ),
                }
            )
        plan.features.append(feature)

    if plan.part_family in {"plate", "flange"} and wants_hole:
        is_center_hole = plan.part_family == "flange" and ("中心" in text or "center" in text.lower())
        diameter = parsed.get("hole_diameter") or parsed.get("metric_thread")
        if not is_center_hole and diameter is None:
            diameter = parsed.get("diameter")
        if is_center_hole:
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
            plan.unresolved.append(
                {
                    "feature": feature.id,
                    "reason": _loc(
                        language,
                        "孔特征还不能稳定建模，需要补充: " + ", ".join(missing),
                        "Hole feature cannot be modeled reliably yet; provide: " + ", ".join(missing),
                    ),
                }
            )
        plan.features.append(feature)


def _apply_smart_autonomy(plan: FeaturePlanV3, description: str, policy: str, language: str = "zh") -> FeaturePlanV3:
    """Turn a Smart plan into an executable concept model with explicit assumptions.

    Smart mode is allowed to design, but it must remain auditable. Every value
    created here is marked as an assumption and summarized in the plan review.
    Strict mode never calls this function.
    """
    updated = deepcopy(plan)
    if policy not in {"suggest_only", "limited_fill", "aggressive_fill", "full_autonomous"}:
        policy = "limited_fill"
    updated.autonomy_policy = policy
    updated.design_intent = updated.design_intent or _smart_design_intent(description, updated.part_family, language)
    if policy == "suggest_only":
        updated.assumptions.append(_loc(language, "智能模式当前策略为只给建议，未将推断尺寸写入可执行模型。", "Smart mode is currently set to suggestions only; inferred dimensions were not written into the executable model."))
        updated.design_review.suggestions.append(_loc(language, "切换为“工程自主设计，保守补全”后，系统才会自动生成概念模型。", "Switch to conservative autonomous design to automatically generate a concept model."))
        updated.self_checks["smart_autonomy"] = {"policy": policy, "executed": False}
        return updated

    text = description.lower()
    parsed = _extract_dimension_clues(description)
    base = updated.base_feature
    if base is None:
        base = _smart_base(_detect_family(text), parsed, language)
        updated.base_feature = base
        updated.assumptions.append(_loc(language, "智能模式根据零件功能和文字线索选择了一个可修改的主基体。", "Smart mode selected an editable main body from the part function and text clues."))

    _complete_base_dimensions(base, parsed, updated, language)
    marker = "智能模式工程假设" if language == "zh" else "Smart mode engineering assumption"
    if not any(marker in item for item in updated.assumptions):
        updated.assumptions.append(_loc(language, "智能模式工程假设：概念尺寸按机械常识补全，未经用户确认。", "Smart mode engineering assumption: concept dimensions were filled with mechanical common sense and are not user-confirmed."))
    for feature in updated.features:
        _complete_smart_feature(feature, base, parsed, text, policy, updated, language)

    if policy == "full_autonomous":
        _add_full_autonomous_features(updated, text, language)

    _refresh_smart_resolution(updated)
    updated.assumptions.append(
        _loc(
            language,
            f"智能模式已执行自主设计策略：{policy}。所有新增尺寸都是工程假设，未被当作图纸事实。",
            f"Smart mode executed autonomous design policy {policy}. All added dimensions are engineering assumptions, not drawing facts.",
        )
    )
    updated.design_review.requires_confirmation = True
    updated.design_review.warnings.append(_loc(language, "当前模型包含智能模式假设尺寸；用于概念验证，不等同于最终生产图纸。", "The model contains smart-mode assumed dimensions; use it for concept review, not as a final production drawing."))
    updated.design_review.suggestions.append(_loc(language, "请在特征树或 AI 对话中确认关键外径、壁厚、孔位和槽尺寸后再用于制造。", "Confirm critical outer diameters, wall thicknesses, hole positions, and slot dimensions in the feature tree or AI chat before manufacturing."))
    updated.self_checks["smart_autonomy"] = {
        "policy": policy,
        "executed": True,
        "assumption_count": len(updated.assumptions),
        "remaining_unresolved": len(updated.unresolved),
    }
    _sync_assumption_details(updated)
    return updated


def _smart_design_intent(description: str, part_family: str, language: str = "zh") -> str:
    text = description.lower()
    if any(token in text for token in ["安装", "mount", "固定", "连接"]):
        return _loc(language, f"面向安装和连接功能的 {part_family} 概念设计", f"Concept design of a {part_family} for mounting and connection")
    if any(token in text for token in ["支撑", "支架", "bracket", "support"]):
        return _loc(language, f"面向承载和支撑功能的 {part_family} 概念设计", f"Concept design of a {part_family} for load and support")
    if any(token in text for token in ["密封", "seal", "流体", "pipe", "tube", "管"]):
        return _loc(language, f"面向导流或密封功能的 {part_family} 概念设计", f"Concept design of a {part_family} for flow or sealing")
    return _loc(language, f"基于用户功能描述的 {part_family} 概念机械设计", f"Concept mechanical design of a {part_family} based on the user's functional description")


def _add_full_autonomous_features(plan: FeaturePlanV3, text: str, language: str = "zh") -> None:
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
            evidence=_loc(language, "全自主模式按管件的密封/定位常见制造意图增加端部环槽", "Full autonomous mode added an end groove for common tube sealing/positioning intent"),
        )
        _assume_dimension(feature, "axial_width", width, _loc(language, "按外径约 8% 选择端部环槽宽度", "End groove width chosen at about 8% of outer diameter"), language)
        _assume_dimension(feature, "reduced_outer_diameter", max(1.0, outer - max(2.0, round(outer * 0.08, 1))), _loc(language, "按外径减少约 8% 形成槽底", "Groove root formed by reducing outer diameter by about 8%"), language)
        _assume_dimension(feature, "z_start", max(0.0, length - width), _loc(language, "端部环槽贴近管件末端", "End groove placed near the tube end"), language)
        plan.features.append(feature)
        plan.assumptions.append(_loc(language, "全自主模式根据管件的定位/密封意图增加端部环槽；可在特征树中删除或修改。", "Full autonomous mode added an end groove for tube positioning/sealing intent; it can be deleted or edited in the feature tree."))

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
            evidence=_loc(language, "全自主模式按支撑功能增加一条可制造的加强肋概念特征", "Full autonomous mode added a manufacturable reinforcing rib for support"),
        )
        _assume_dimension(feature, "length", max(20.0, round(base_length * 0.45, 1)), _loc(language, "加强肋长度取基体长度约 45%", "Rib length chosen at about 45% of the base length"), language)
        _assume_dimension(feature, "width", max(4.0, round(base_width * 0.15, 1)), _loc(language, "加强肋宽度取基体宽度约 15%", "Rib width chosen at about 15% of the base width"), language)
        _assume_dimension(feature, "height", max(4.0, round((_feature_value(base.dimensions, "height") or 10.0) * 1.5, 1)), _loc(language, "加强肋高度按基体厚度的工程比例选择", "Rib height chosen from an engineering ratio of the base thickness"), language)
        plan.features.append(feature)
        plan.assumptions.append(_loc(language, "全自主模式根据支撑意图增加加强肋；请在设计评审中确认受力方向。", "Full autonomous mode added a reinforcing rib for support; confirm the load direction in design review."))

    if plan.features:
        plan.design_review.suggestions.append(_loc(language, "全自主方案已主动补充可解释的机械特征；请逐项审查后再用于生产。", "The full autonomous proposal added explainable mechanical features; review each one before production."))


def _smart_base(part_family: str, parsed: dict[str, float], language: str = "zh") -> FeatureV3:
    if part_family == "tube":
        return FeatureV3(
            id="base_tube",
            type="hollow_cylinder",
            operation="base",
            dimensions={},
            placement=PlacementV3(reference="bottom_end_center", axis="Z"),
            evidence=_loc(language, "智能模式根据管/轴/套筒功能选择回转基体", "Smart mode chose a revolved body for tube/shaft/bushing function"),
        )
    if part_family == "flange":
        return FeatureV3(
            id="base_flange",
            type="cylinder_base",
            operation="base",
            dimensions={},
            placement=PlacementV3(reference="center", axis="Z"),
            evidence=_loc(language, "智能模式根据法兰功能选择圆柱基体", "Smart mode chose a cylinder body for flange function"),
        )
    return FeatureV3(
        id="base_plate" if part_family == "plate" else "base_body",
        type="box_base",
        operation="base",
        dimensions={},
        placement=PlacementV3(reference="bottom_center", axis="Z"),
        evidence=_loc(language, "智能模式根据板件/通用零件功能选择箱体基体", "Smart mode chose a box body for plate/general part function"),
    )


def _complete_base_dimensions(base: FeatureV3, parsed: dict[str, float], plan: FeaturePlanV3, language: str = "zh") -> None:
    dims = base.dimensions
    kind = base.type
    if kind == "hollow_cylinder":
        outer = _feature_value(dims, "outer_diameter") or parsed.get("outer_diameter") or parsed.get("diameter") or 40.0
        inner = _feature_value(dims, "inner_diameter") or parsed.get("inner_diameter") or max(outer * 0.6, outer - 10.0)
        length = _feature_value(dims, "length") or parsed.get("length") or max(80.0, outer * 2.0)
        inner = min(inner, max(outer - 1.0, outer * 0.9))
        _assume_dimension(base, "outer_diameter", outer, _loc(language, "外径缺失，采用管件概念外径 40mm 或用户已有外径线索", "Outer diameter missing; using concept tube OD of 40mm or existing user clues"), language)
        _assume_dimension(base, "inner_diameter", inner, _loc(language, "内径缺失，按约 60% 外径保留合理壁厚", "Inner diameter missing; keeping a reasonable wall near 60% of outer diameter"), language)
        _assume_dimension(base, "length", length, _loc(language, "长度缺失，按约 2 倍外径建立可修改概念长度", "Length missing; building an editable concept length about twice the outer diameter"), language)
        return
    if kind == "cylinder_base":
        outer = _feature_value(dims, "outer_diameter") or parsed.get("outer_diameter") or parsed.get("diameter") or 60.0
        length = _feature_value(dims, "length") or parsed.get("length") or parsed.get("height") or 10.0
        _assume_dimension(base, "outer_diameter", outer, _loc(language, "法兰/圆柱外径缺失，采用常见概念外径", "Flange/cylinder outer diameter missing; using a common concept diameter"), language)
        _assume_dimension(base, "length", length, _loc(language, "法兰厚度或圆柱长度缺失，采用常见概念厚度", "Flange thickness or cylinder length missing; using a common concept thickness"), language)
        return

    length = _feature_value(dims, "length") or parsed.get("length") or 80.0
    width = _feature_value(dims, "width") or parsed.get("width") or max(30.0, round(length * 0.6, 1))
    height = _feature_value(dims, "height") or parsed.get("height") or parsed.get("thickness") or max(4.0, round(min(length, width) * 0.1, 1))
    _assume_dimension(base, "length", length, _loc(language, "板件长度缺失，采用 80mm 或按已有长度线索建立概念外形", "Plate length missing; using 80mm or existing length clues"), language)
    _assume_dimension(base, "width", width, _loc(language, "板件宽度缺失，按长度约 60% 建立概念外形", "Plate width missing; using about 60% of length"), language)
    _assume_dimension(base, "height", height, _loc(language, "板件厚度缺失，按短边约 10% 且不小于 4mm 建立概念外形", "Plate thickness missing; using about 10% of the short edge and at least 4mm"), language)


def _complete_smart_feature(
    feature: FeatureV3,
    base: FeatureV3,
    parsed: dict[str, float],
    text: str,
    policy: str,
    plan: FeaturePlanV3,
    language: str = "zh",
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
        _assume_dimension(feature, "axial_width", width, _loc(language, "槽宽缺失，按外径约 8% 建立环槽", "Slot width missing; using about 8% of outer diameter"), language)
        _assume_dimension(feature, "reduced_outer_diameter", reduced, _loc(language, "槽底外径缺失，按外径减少约 8% 建立环槽", "Groove root diameter missing; reducing outer diameter by about 8%"), language)
        _assume_dimension(feature, "z_start", z_start, _loc(language, "槽位置缺失，顶部槽默认贴近端面，否则放在长度中部", "Slot position missing; top slot defaults near the end face, otherwise at mid-length"), language)
        return

    if kind in {"through_hole", "blind_hole", "counterbore_hole"}:
        diameter = _feature_value(dims, "diameter", "hole_diameter") or parsed.get("hole_diameter") or parsed.get("diameter") or 6.0
        _assume_dimension(feature, "diameter", diameter, _loc(language, "孔径缺失，采用常见 6mm 概念孔径", "Hole diameter missing; using a common 6mm concept hole"), language)
        if kind != "through_hole" and not _feature_value(dims, "depth"):
            _assume_dimension(feature, "depth", max(1.0, base_length * 0.5), _loc(language, "盲孔深度缺失，采用基体厚度/长度约 50%", "Blind hole depth missing; using about 50% of the base thickness/length"), language)
        if feature.extent is None:
            feature.extent = "through"
            feature.assumptions.append(_loc(language, "未说明孔深，智能模式默认贯穿；可在属性面板改为盲孔。", "Hole depth not specified; smart mode defaults to through, editable to blind in the property panel."))
        if feature.placement.reference in {"needs_position", "origin"} and feature.placement.x is None and feature.placement.y is None:
            feature.placement.reference = "model_center"
            feature.placement.x = 0.0
            feature.placement.y = 0.0
            feature.assumptions.append(_loc(language, "孔位缺失，智能模式默认放在主基准中心。", "Hole position missing; smart mode defaults it to the main datum center."))
        return

    if kind in {"rectangular_slot", "rectangular_pocket"}:
        length = _feature_value(dims, "length", "slot_length") or parsed.get("slot_length") or max(10.0, round(base_width * 0.5, 1))
        width = _feature_value(dims, "width", "slot_width") or parsed.get("slot_width") or max(3.0, round(base_width * 0.15, 1))
        depth = _feature_value(dims, "depth", "height") or (base_length if "贯穿" in text or "through" in text else max(1.0, base_length * 0.5))
        _assume_dimension(feature, "length", length, _loc(language, "槽长缺失，按基体宽度约 50% 建立概念槽", "Slot length missing; using about 50% of the base width"), language)
        _assume_dimension(feature, "width", width, _loc(language, "槽宽缺失，按基体宽度约 15% 且不小于 3mm 建立概念槽", "Slot width missing; using about 15% of the base width and at least 3mm"), language)
        _assume_dimension(feature, "depth", depth, _loc(language, "槽深缺失，按用户是否提到贯穿决定", "Slot depth missing; decided by whether the user mentioned through"), language)
        if feature.extent is None:
            feature.extent = "through" if depth >= base_length else "blind"
        return

    if policy == "aggressive_fill" and kind in {"boss_cylinder", "rectangular_pad", "rib_box"}:
        if kind == "boss_cylinder":
            _assume_dimension(feature, "diameter", _feature_value(dims, "diameter", "outer_diameter") or 12.0, _loc(language, "凸台直径缺失，采用常见 12mm 概念尺寸", "Boss diameter missing; using a common 12mm concept size"), language)
            _assume_dimension(feature, "height", _feature_value(dims, "height", "length") or max(3.0, base_length * 0.2), _loc(language, "凸台高度缺失，采用基体高度约 20%", "Boss height missing; using about 20% of the base height"), language)
        else:
            _assume_dimension(feature, "length", _feature_value(dims, "length") or 20.0, _loc(language, "加料长度缺失，采用概念尺寸", "Additive length missing; using a concept size"), language)
            _assume_dimension(feature, "width", _feature_value(dims, "width") or 10.0, _loc(language, "加料宽度缺失，采用概念尺寸", "Additive width missing; using a concept size"), language)
            _assume_dimension(feature, "height", _feature_value(dims, "height", "depth") or 5.0, _loc(language, "加料高度缺失，采用概念尺寸", "Additive height missing; using a concept size"), language)


def _assume_dimension(feature: FeatureV3, key: str, value: float, reason: str, language: str = "zh") -> None:
    existing = feature.dimensions.get(key)
    if existing is not None and existing.value is not None:
        return
    evidence = _loc(language, f"智能模式工程假设：{reason}", f"Smart mode engineering assumption: {reason}")
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
                    reason=dimension.evidence or "AI inferred dimension",
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
    if any(token in text for token in ["tube", "pipe", "管", "管件", "轴", "轴套", "套筒", "bushing"]):
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
    if "hole_diameter" not in results and "diameter" in results and re.search(r"(?:中心孔|中心|center)", text, re.IGNORECASE):
        results["hole_diameter"] = results["diameter"]
        results.pop("diameter", None)
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
# with mojibake reasons still receive readable questions.
def questions_from_plan(plan: FeaturePlanV3, language: str = "zh") -> list[ClarificationQuestion]:
    questions: list[ClarificationQuestion] = []
    for item in plan.unresolved[:8]:
        feature_id = str(item.get("feature", "unknown_feature"))
        reason = str(item.get("reason", "缺少必要尺寸或定位信息"))
        feature = next((candidate for candidate in _all_features(plan) if candidate.id == feature_id), None)
        kind = feature.type if feature else ""
        missing = [name for name, dimension in (feature.dimensions.items() if feature else []) if dimension.value is None]
        if kind == "annular_groove" or "groove" in reason.lower() or "环槽" in reason:
            text = _loc(
                language,
                f"特征 {feature_id} 是环槽，但还缺少可执行尺寸。请填写槽宽、槽底外径和轴向位置。",
                f"Feature {feature_id} is an annular groove but is missing executable dimensions. Provide slot width, groove root diameter, and axial position.",
            )
            options = _loc(language, ["跳过环槽", "重新上传带尺寸标注的图"], ["Skip groove", "Re-upload drawing with dimensions"])
            answer_type = "text"
            refs = _loc(language, ["槽宽", "槽底外径", "起始位置 Z"], ["slot width", "groove root diameter", "start position Z"])
            impact = _loc(language, "未确认时只生成已确认的主体，环槽不会进入生产模型。", "Until confirmed, only the confirmed body is modeled; the groove is excluded from the production model.")
        elif kind in {"through_hole", "blind_hole", "counterbore_hole"} or "hole" in reason.lower() or "孔" in reason:
            text = _loc(
                language,
                f"特征 {feature_id} 是孔，但还缺少孔径、中心位置或孔深。请按“孔径 6mm，X=20mm，Y=0mm，贯穿”回答。",
                f"Feature {feature_id} is a hole but is missing diameter, center position, or depth. Answer like: hole diameter 6mm, X=20mm, Y=0mm, through.",
            )
            options = _loc(language, ["贯穿孔", "盲孔", "跳过此孔"], ["Through hole", "Blind hole", "Skip hole"])
            answer_type = "text"
            refs = _loc(language, ["孔径", "X/Y 位置", "贯穿或深度"], ["hole diameter", "X/Y position", "through or depth"])
            impact = _loc(language, "未确认时跳过此孔，避免生成孔径或位置错误的模型。", "Until confirmed, the hole is skipped to avoid an incorrect diameter or position.")
        elif "position" in reason.lower() or "定位" in reason:
            text = _loc(
                language,
                f"特征 {feature_id} 的定位还不明确。请说明它相对哪个基准面，以及 X/Y/Z 距离。",
                f"The position of feature {feature_id} is unclear. State which datum it references and the X/Y/Z distances.",
            )
            options = _loc(language, ["相对底面", "相对中心线", "跳过此特征"], ["Relative to bottom face", "Relative to centerline", "Skip feature"])
            answer_type = "text"
            refs = _loc(language, ["基准", "X/Y/Z 位置"], ["datum", "X/Y/Z position"])
            impact = _loc(language, "未确认时不会执行该特征。", "The feature will not be executed until confirmed.")
        else:
            missing_text = "、".join(missing) if missing else ("尺寸或定位" if language == "zh" else "dimensions or position")
            text = _loc(
                language,
                f"特征 {feature_id} 还缺少 {missing_text}。请填写明确的毫米值，或选择跳过。",
                f"Feature {feature_id} is missing {missing_text}. Provide clear mm values or choose to skip.",
            )
            options = _loc(language, ["补充尺寸", "跳过此特征"], ["Provide dimensions", "Skip feature"])
            answer_type = "text"
            refs = missing or _loc(language, ["必要尺寸"], ["required dimensions"])
            impact = _loc(language, "未确认时该特征会被跳过，并在执行报告中保留原因。", "Until confirmed, the feature is skipped and the reason is kept in the execution report.")
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
