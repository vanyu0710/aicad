from __future__ import annotations

from typing import Any


def build_clarification_questions(qwen_json: dict[str, Any], model_plan: dict[str, Any]) -> list[str]:
    questions: list[str] = []
    seen: set[str] = set()
    for item in model_plan.get("unresolved", []) or []:
        feature = str(item.get("feature", "unknown_feature"))
        reason = str(item.get("reason", "缺少尺寸"))
        lowered = reason.lower()
        if "width" in lowered or "槽" in reason or "宽" in reason:
            text = f"{feature} 的槽宽多少 mm？槽长多少 mm？从底端多少 mm 到多少 mm？"
        elif "position" in lowered or "定位" in reason:
            text = f"{feature} 的位置还不够清楚，请告诉我相对基准面/中心线的距离。"
        else:
            text = f"{feature}: {reason}。请补充明确毫米尺寸和定位关系。"
        if text not in seen:
            seen.add(text)
            questions.append(text)
    for uncertainty in qwen_json.get("uncertainties", []) or []:
        text = str(uncertainty)
        if text not in seen:
            seen.add(text)
            questions.append(text)
    return questions


def dimension_ledger_markdown(qwen_json: dict[str, Any]) -> str:
    annotations = qwen_json.get("sketch", {}).get("raw_annotations", []) or []
    if not annotations:
        return ""
    lines = ["### 图上已识别尺寸"]
    for item in annotations:
        text = item.get("text", "")
        region = item.get("endpoints_or_region", "")
        confidence = item.get("confidence", "")
        lines.append(f"- {text} | {region} | {confidence}")
    return "\n".join(lines)


def clarification_markdown(questions: list[str]) -> str:
    if not questions:
        return ""
    return "\n".join(f"- {item}" for item in questions)
