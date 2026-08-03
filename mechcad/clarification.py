from __future__ import annotations

from typing import Any


FEATURE_LABELS = {
    "two axial slots": "两条沿管子长度方向的槽/开口",
    "top reduced end step": "顶部缩径台阶",
    "additional r1 fillets": "其余 R1 圆角",
    "broken view length distribution": "断裂视图中的槽/台阶位置",
    "top_groove": "顶部槽",
    "bottom_groove": "底部槽",
    "slot": "槽/开口",
}


def build_clarification_questions(
    qwen_json: dict[str, Any],
    model_plan: dict[str, Any],
    *,
    max_questions: int = 5,
) -> list[str]:
    """Turn unresolved CAD facts into short questions a user can actually answer."""
    questions: list[str] = []
    seen: set[str] = set()

    def add(question: str) -> None:
        question = question.strip()
        if question and question not in seen and len(questions) < max_questions:
            seen.add(question)
            questions.append(question)

    visible = _visible_dimension_summary(qwen_json)

    for item in model_plan.get("unresolved", []) if isinstance(model_plan.get("unresolved"), list) else []:
        if not isinstance(item, dict):
            continue
        raw_feature = str(item.get("feature") or item.get("id") or "该特征")
        feature = _human_feature_name(raw_feature)
        reason = str(item.get("reason") or item.get("question") or "尺寸或定位信息不足")
        lower = reason.lower()
        if "slot" in lower or "槽" in feature or "开口" in feature:
            add(
                f"{feature}没有建出来，因为槽的宽度、起止位置和是否贯穿管壁还不确定。"
                f"{visible}请直接回答：有几条槽？每条槽宽多少 mm？从底端多少 mm 到多少 mm？"
                "是贯穿管壁切开，还是只在外表面浅槽？"
            )
        elif "41.2" in reason or "41.20" in reason or "bore" in lower or "diameter" in lower or "直径" in reason or "径" in reason:
            add(
                f"{feature}的直径含义冲突。{visible}请确认：图上的 `⌀41.20` 是顶部外径，"
                "还是管子的内孔直径？如果它是顶部外径，请再确认顶部这一小段的长度是多少 mm。"
            )
        elif "width" in lower or "宽" in reason or "axial" in lower or "轴向" in reason:
            add(f"{feature}还缺沿长度方向的尺寸。{visible}请说明它从哪个端面开始、长度多少 mm。")
        elif "position" in lower or "location" in lower or "定位" in reason or "位置" in reason:
            add(f"{feature}的位置还不明确。{visible}请说明它距离底端或顶端多少 mm，或给出起点/终点。")
        else:
            add(f"{feature}还不能可靠建模。{visible}请用普通话描述它在哪个位置、尺寸是多少、是否需要建出来。")

    uncertainties = qwen_json.get("uncertainties", [])
    if isinstance(uncertainties, list):
        for item in uncertainties:
            text = str(item).strip()
            if text and any(token in text for token in ("尺寸", "位置", "槽", "孔", "方向", "深度")):
                add(f"图纸中这项信息不够明确：{text}。请按你的设计意图确认。")

    checks = qwen_json.get("consistency_checks", {})
    if isinstance(checks, dict):
        missing = checks.get("missing_for_exact_model")
        if isinstance(missing, list):
            for item in missing:
                add(f"精确建模还缺少：{item}。请补充具体数值或选择‘按图中可见比例估计’。")

    return questions


def clarification_markdown(questions: list[str]) -> str:
    if not questions:
        return ""
    lines = ["### 需要你确认"]
    lines.extend(f"{index}. {question}" for index, question in enumerate(questions, 1))
    lines.append("\n你可以直接按自然语言回答，例如：`⌀41.20 是顶部外径；顶部台阶长 10mm；有 2 条贯穿槽，槽宽 2mm，从底端 10mm 到 290mm。`")
    return "\n".join(lines)


def dimension_ledger_markdown(qwen_json: dict[str, Any], *, max_items: int = 12) -> str:
    annotations = _raw_annotations(qwen_json)
    if not annotations:
        return ""
    lines = ["### 图上已识别尺寸"]
    for item in annotations[:max_items]:
        text = item.get("text")
        region = item.get("endpoints_or_region") or item.get("view") or "位置未确定"
        confidence = item.get("confidence")
        suffix = f"，置信度 {confidence:.2f}" if isinstance(confidence, (int, float)) else ""
        lines.append(f"- `{text}`：{region}{suffix}")
    if len(annotations) > max_items:
        lines.append(f"- 还有 {len(annotations) - max_items} 项标注，详见阶段 1 JSON。")
    return "\n".join(lines)


def _visible_dimension_summary(qwen_json: dict[str, Any]) -> str:
    annotations = _raw_annotations(qwen_json)
    if not annotations:
        return ""
    bits = []
    for item in annotations[:8]:
        text = item.get("text")
        region = item.get("endpoints_or_region")
        if text and region:
            bits.append(f"{text}（{region}）")
        elif text:
            bits.append(str(text))
    if not bits:
        return ""
    return "我已看到这些标注：" + "、".join(bits) + "。"


def _raw_annotations(qwen_json: dict[str, Any]) -> list[dict[str, Any]]:
    sketch = qwen_json.get("sketch", {})
    if not isinstance(sketch, dict):
        return []
    annotations = sketch.get("raw_annotations", [])
    if not isinstance(annotations, list):
        return []
    return [item for item in annotations if isinstance(item, dict) and item.get("text")]


def _human_feature_name(name: str) -> str:
    key = name.strip().lower()
    return FEATURE_LABELS.get(key, name.replace("_", " "))
