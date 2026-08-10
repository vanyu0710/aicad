from __future__ import annotations

"""Planner model integration: turn vision JSON + description into FeaturePlanV3."""

import json
from typing import Any

from backend.mechcad_ai.client import ApiCallError, chat_completion, has_configured_model, parse_json_object
from backend.mechcad_ai.normalize import normalize_ai_plan
from backend.mechcad_ai.prompts import get_prompt
from backend.schemas import ClarificationQuestion, FeaturePlanV3


def generate_feature_plan(
    description: str,
    vision_json: dict[str, Any] | None,
    settings,
    mode: str = "strict",
    smart_fill_policy: str = "limited_fill",
) -> FeaturePlanV3 | None:
    """Ask the planner model for a FeaturePlanV3 JSON.

    Returns None when the model is not configured, fails, or returns an invalid
    plan, so the caller can fall back to the deterministic local planner.
    """
    if not has_configured_model(settings, "planner"):
        return None

    prompt = get_prompt("feature_planning")
    seed = _stub_seed_from_description(description)
    context = {
        "用户功能描述": description,
        "视觉读图 JSON": vision_json or {},
        "保守种子计划，仅作参考，可扩展": seed,
        "模式": mode,
        "智能补全策略": smart_fill_policy,
    }
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(context, ensure_ascii=False, indent=2)
            + "\n\n请输出 FeaturePlanV3 JSON（schema_version=3.0）。不要输出 Markdown 或解释性散文。",
        },
    ]
    try:
        content = chat_completion(
            settings,
            "planner",
            messages,
            max_tokens=4096,
            temperature=0.1,
            response_json=True,
        )
    except ApiCallError:
        return None

    try:
        parsed = parse_json_object(content)
        plan = FeaturePlanV3.model_validate(normalize_ai_plan(parsed))
    except Exception:
        return None
    if plan.base_feature is None:
        return None
    plan.assumptions.append("FeaturePlan 由 planner 模型生成，尚未经过用户逐项确认。")
    return plan


def chat_edit_feature_plan(
    plan: FeaturePlanV3,
    message: str,
    settings,
) -> FeaturePlanV3 | None:
    """Ask the planner model for an incremental edit of the current plan."""
    if not has_configured_model(settings, "planner"):
        return None

    prompt = get_prompt("chat_edit")
    context = {
        "当前 FeaturePlanV3": plan.model_dump(),
        "用户自然语言修改": message,
        "模式": getattr(settings, "operation_mode", "strict"),
        "智能补全策略": getattr(settings, "smart_fill_policy", "limited_fill"),
    }
    messages = [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(context, ensure_ascii=False, indent=2)
            + "\n\n请输出修改后的完整 FeaturePlanV3 JSON。不要输出 diff、Markdown 或解释性散文。",
        },
    ]
    try:
        content = chat_completion(
            settings,
            "planner",
            messages,
            max_tokens=4096,
            temperature=0.1,
            response_json=True,
        )
    except ApiCallError:
        return None

    try:
        parsed = parse_json_object(content)
        updated = FeaturePlanV3.model_validate(normalize_ai_plan(parsed))
    except Exception:
        return None
    if updated.base_feature is None:
        return None
    return updated


def _stub_seed_from_description(description: str) -> dict[str, Any]:
    """A minimal, honest seed that never invents executable dimensions."""
    return {
        "schema_version": "3.0",
        "units": "mm",
        "part_family": "unknown",
        "base_feature": None,
        "features": [],
        "unresolved": [{"feature": "base_body", "reason": "等待视觉读图或用户确认尺寸"}],
        "description": description,
    }


def questions_from_plan(plan: FeaturePlanV3) -> list[ClarificationQuestion]:
    """Convert unresolved plan items into user-facing clarification questions."""
    from backend.ai import questions_from_plan as _local_questions

    return _local_questions(plan)
