"""MechKernel agent loop（P1 垂直切片 → v0.10 对话式会话）。

多轮原生 function-calling 循环：LLM 直接以 kernel op 名作为工具名调用，
loop 调 worker RPC 执行，把精简后的 StepResult 作为工具结果回喂，直到模型
停止调用工具（给出最终文字总结）或到达 max_steps。

设计边界（对齐 docs/mechkernel-harness-roadmap.md）：
- 执行层直接是 MechKernel op（D1），本模块不理解 FeaturePlanV3。
- 人在回路确认点（P2）：破坏性操作/破坏性修复/ask_user 经 ApprovalBroker，
  WS 事件携带 approval_id（前端可回复）。
- v0.10：可选接入 AgentSession——对话历史持久化、运行中用户插话在轮间注入；
  每轮模型文字以 ``agent_text_delta`` 事件播出（流式接入见 Commit B）。
- 自修复：RECOVERABLE + suggestion.fix 时按 schema 过滤后自动重试一次。

本模块不 import CAD 库；几何只经 worker RPC 触达（D2）。
"""

from __future__ import annotations

import base64
import inspect
import json
import math
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.agent.approvals import ApprovalBroker
from backend.agent.session import AgentSession, build_user_message
from backend.agent.tools import build_llm_tools, filter_args_to_schema
from backend.mechcad_ai.client import ApiCallError, ToolCall, ToolCallRound, tool_result_message

ToolFn = Callable[..., ToolCallRound]
EmitFn = Callable[[str, str, dict[str, Any]], None]

_VALUE_MAX_ITEMS = 40
_VALUE_MAX_STR = 1500
_NARRATIVE_CLIP = 40
_ARGS_PREVIEW_CLIP = 240


def _compact_value(value: Any, *, max_items: int = _VALUE_MAX_ITEMS,
                   max_str: int = _VALUE_MAX_STR) -> Any:
    """结构化裁剪（v2.16 P0-2）：保持 JSON 形状，绝不字符串化、绝不按字符腰斩。

    - dict/list 递归；列表截到 max_items 并附 `<key>_total` 提示被裁掉的规模；
    - 只在**叶子字符串**上做长度截断（截断叶子不破坏 JSON 结构）；
    - 其余标量原样保留。
    这样 select/measure/query/feature_contract/bounding_box/solid_count/干涉报告
    等结构化结果模型能直接解析，而不是收到一段可能被截断成非法 JSON 的字符串。
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, list):
                out[key] = [_compact_value(v, max_items=max_items, max_str=max_str)
                            for v in item[:max_items]]
                if len(item) > max_items:
                    out[f"{key}_total"] = len(item)
            else:
                out[key] = _compact_value(item, max_items=max_items, max_str=max_str)
        return out
    if isinstance(value, list):
        return [_compact_value(v, max_items=max_items, max_str=max_str)
                for v in value[:max_items]]
    if isinstance(value, str):
        if len(value) > max_str:
            return value[:max_str] + f"…(截断，原长 {len(value)} 字符)"
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    return text[:max_str] + "…" if len(text) > max_str else text


@dataclass
class AgentLoopResult:
    ok: bool = False
    stopped: bool = False
    steps: int = 0
    final_text: str = ""
    volume: float | None = None
    feature_graph: dict[str, Any] = field(default_factory=dict)
    feature_tree: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    logs: list[str] = field(default_factory=list)
    error: str | None = None
    # v2.16 可靠性门控（P0-1/P0-4）：失败的结构化类型，供报告与 API 判定
    error_kind: str | None = None
    # v0.12 多零件逐件交付：finish_part 归档清单 + design_calculate 调研转录
    parts: list[dict[str, Any]] = field(default_factory=list)
    design_calculations: list[dict[str, Any]] = field(default_factory=list)
    # v0.14 F2a：export_assembly 的装配摘要（投影进 ArtifactSet）
    assembly: dict[str, Any] | None = None
    # v0.15 提示词工程（程序判态）：SUCCESS / PARTIAL / FAILED，由 _finalize 计算
    status: str = "FAILED"


def _compact_step_result(data: dict[str, Any]) -> dict[str, Any]:
    """给 LLM 的工具结果：保留决策所需字段，丢弃渲染/长叙事以控制上下文。

    v2.16（P0-2）：value 保持结构化 JSON（不再 json.dumps 成字符串、不再按字符
    截断成非法 JSON）；geometry_validation 必须保留；渲染图不回喂但给可用标记。
    """
    compact: dict[str, Any] = {
        "success": data.get("success"),
        "feature_id": data.get("feature_id"),
        "error_kind": data.get("error_kind"),
        "error": data.get("error"),
        "suggestion": _compact_value(data.get("suggestion")) if data.get("suggestion") is not None else None,
        "warning": data.get("warning"),
        "hint": data.get("hint"),
        "narrative": data.get("narrative"),
        "geometry_summary": data.get("geometry_summary"),
        "geometry_validation": data.get("geometry_validation"),
        "hints": data.get("hints") or [],
    }
    if data.get("value") is not None:
        compact["value"] = _compact_value(data["value"])
    if data.get("render_base64") or data.get("render_views_base64"):
        compact["render_available"] = True
        compact["render_hint"] = "如需视觉复核请调用 render/快照（图不随本结果回传）"
    return compact


def _ask_user_tool() -> dict[str, Any]:
    """合成工具：agent 在关键信息不明确时向用户提结构化问题（可多问、带选项）。"""
    return {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "需要向用户澄清关键信息时调用。一次可合并 1-4 个相关问题（不要连开多次）。"
                "每个问题 type：single=单选、multi=多选、text=自由输入；single/multi 必须给 2-4 个 options。"
                "UI 会自动追加“其他/自定义”输入，你不要自己写 Other 选项。"
                "不要用它问能从上下文推断的问题、或无关紧要的 trivial yes/no。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "description": "1-4 个待澄清问题",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "问题短标识，用于答案配对（如 q1、thickness）"},
                                "question": {"type": "string", "description": "清楚具体的问题文本"},
                                "header": {"type": "string", "description": "≤12 字短标签（chip），可选"},
                                "type": {"type": "string", "enum": ["single", "multi", "text"], "description": "单选/多选/自由文本"},
                                "options": {
                                    "type": "array",
                                    "description": "single/multi 必填 2-4 项；text 不要提供",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {"type": "string", "description": "选项文本"},
                                            "description": {"type": "string", "description": "选项补充说明，可选"},
                                        },
                                        "required": ["label"],
                                    },
                                },
                                "required": {"type": "boolean", "description": "是否必答，默认 true"},
                                "allowFreeText": {"type": "boolean", "description": "是否允许自由输入补充，默认 true"},
                            },
                            "required": ["id", "question", "type"],
                        },
                    },
                },
                "required": ["questions"],
            },
        },
    }


_QUESTION_TYPES = {"single", "multi", "text"}


def _normalize_questions(raw: Any) -> list[dict[str, Any]]:
    """校验并规范化 ask_user 的 questions（补默认、剔除非法项、限 1-4 问）。"""
    items = raw if isinstance(raw, list) else []
    cleaned: list[dict[str, Any]] = []
    for index, item in enumerate(items[:4]):
        if not isinstance(item, dict):
            continue
        qtype = str(item.get("type") or "text")
        if qtype not in _QUESTION_TYPES:
            qtype = "text"
        options: list[dict[str, str]] = []
        if qtype in ("single", "multi"):
            for opt in (item.get("options") or [])[:4]:
                if isinstance(opt, dict) and str(opt.get("label", "")).strip():
                    options.append({"label": str(opt["label"]).strip(), "description": str(opt.get("description", "") or "")})
            if len(options) < 2:
                qtype = "text"  # 选项不足退化为自由输入
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        cleaned.append({
            "id": str(item.get("id") or f"q{index + 1}"),
            "question": question,
            "header": str(item.get("header") or "")[:12],
            "type": qtype,
            "options": options,
            "required": bool(item.get("required", True)),
            "allowFreeText": bool(item.get("allowFreeText", True)),
        })
    return cleaned


def _format_ask_user_transcript(questions: list[dict[str, Any]], answers: dict[str, Any], action: str) -> str:
    """把用户答案拼成 Q/A 转录，作为 ask_user 工具结果回喂模型。"""
    if action == "reject":
        return "用户跳过了本次提问（未给出答案）。请基于合理工程假设继续，并在最终总结里标注这些假设。"
    if action == "timeout":
        return "用户未在时限内响应本次提问，视为未回答。请基于合理工程假设继续，并在最终总结里标注这些假设。"
    lines: list[str] = []
    for question in questions:
        value = answers.get(question["id"])
        if isinstance(value, list):
            answer = "、".join(str(v) for v in value) if value else "（未回答）"
        elif value is None or str(value).strip() == "":
            answer = "（未回答）"
        else:
            answer = str(value).strip()
        lines.append(f"Q: {question['question']}\nA: {answer}")
    return "\n".join(lines)


def _normalize_plan_steps(raw: Any) -> list[dict[str, Any]]:
    """规范化计划步骤：补 id、剔除无标题项、初始状态 pending；透传 part 归属。"""
    items = raw if isinstance(raw, list) else []
    steps: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        steps.append({
            "id": str(item.get("id") or f"s{index + 1}"),
            "title": title,
            "op": str(item.get("op") or "") or None,
            "rationale": str(item.get("rationale") or "") or None,
            "part": str(item.get("part") or "") or None,
            "status": str(item.get("status") or "pending"),
        })
    return steps


def _normalize_pose(raw: Any) -> dict[str, Any] | None:
    """BOM pose 规范化：position=[x,y,z] 有限数；rotation_deg=[angle,[ax,ay,az]]。

    非法 pose 返回 None（丢字段不整体拒绝，审批卡可见缺位姿）。"""
    if not isinstance(raw, dict):
        return None
    position = raw.get("position")
    if not (isinstance(position, (list, tuple)) and len(position) == 3):
        return None
    try:
        pos = [float(v) for v in position]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in pos):
        return None
    out: dict[str, Any] = {"position": pos}
    rotation = raw.get("rotation_deg")
    if isinstance(rotation, (list, tuple)) and len(rotation) == 2:
        try:
            angle = float(rotation[0])
            axis = [float(v) for v in rotation[1]]
        except (TypeError, ValueError, IndexError):
            return out
        if (math.isfinite(angle) and len(axis) == 3 and all(math.isfinite(v) for v in axis)
                and any(abs(v) > 1e-9 for v in axis)):
            out["rotation_deg"] = [angle, axis]
    return out


def _normalize_bom(raw: Any) -> list[dict[str, Any]]:
    """规范化零件清单（BOM）：每项必须有 part 名；数量默认 1；参数/依赖/位姿容错。"""
    items = raw if isinstance(raw, list) else []
    bom: list[dict[str, Any]] = []
    for index, item in enumerate(items[:24]):
        if not isinstance(item, dict):
            continue
        part = str(item.get("part") or "").strip()
        if not part:
            continue
        quantity = 1
        try:
            if item.get("quantity") is not None:
                quantity = max(1, min(99, int(item["quantity"])))
        except (TypeError, ValueError):
            quantity = 1
        key_params: dict[str, Any] = {}
        raw_params = item.get("key_params")
        if isinstance(raw_params, dict):
            for k, v in list(raw_params.items())[:16]:
                sv = str(v)
                key_params[str(k)[:40]] = sv[:60]
        elif isinstance(raw_params, str) and raw_params.strip():
            # 容错：模型/脚本把参数表写成一行字符串（"m=2 z=17 b=16"）也算有参数表
            key_params["params"] = raw_params.strip()[:200]
        depends_on = [
            str(d).strip()
            for d in (item.get("depends_on") or [])[:8]
            if isinstance(d, (str, int)) and str(d).strip()
        ]
        bom.append({
            "id": str(item.get("id") or f"p{index + 1}"),
            "part": part,
            "role": str(item.get("role") or "")[:120],
            "quantity": quantity,
            "key_params": key_params,
            "depends_on": depends_on,
            "pose": _normalize_pose(item.get("pose")),
        })
    return bom


def _export_assembly_tool() -> dict[str, Any]:
    """合成工具：装配导出（F2a）——零件库 + 位姿 manifest → 装配 STEP + 干涉 + 预览图 + 交付报告。"""
    return {
        "type": "function",
        "function": {
            "name": "export_assembly",
            "description": (
                "全部零件 finish_part 归档后调用：把项目零件库按各零件 BOM 位姿组装为装配交付物——"
                "多实体装配 STEP（每零件具名产品节点）、全对干涉检查（可传 expected_overlaps 豁免表："
                "[{a, b, max_volume_mm3, reason}]，如齿轮啮合区）、整装配四视角预览图、交付报告。"
                "不需要当前会话有几何（读的是已归档文件）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expected_overlaps": {
                        "type": "array",
                        "description": "预期重叠豁免（设计意图内的干涉），每项 {a, b, max_volume_mm3?, reason}",
                        "items": {"type": "object"},
                    },
                    "note": {"type": "string", "description": "本次装配导出的说明（可选）"},
                },
                "required": [],
            },
        },
    }


_BUILTIN_CALC_KINDS = ("gear_ratio_split", "gear_pair", "nearest_standard_module",
                       "shaft_diameter", "housing_wall")


def _design_calculate_tool() -> dict[str, Any]:
    """合成工具：设计调研计算（纯算术，无几何副作用，计划门控期间可用）。"""
    return {
        "type": "function",
        "function": {
            "name": "design_calculate",
            "description": (
                "工程调研计算（不动几何，随时可用）。内置 kind："
                "gear_ratio_split（总传动比多级拆分，params: {total_ratio, max_stages?, min_stage_ratio?, max_stage_ratio?}）、"
                "gear_pair（齿轮副几何+中心距+重合度，params: {module, z1, z2, pressure_angle_deg?}）、"
                "nearest_standard_module（params: {target}）、"
                "shaft_diameter（轴径初估，params: {power_kw, rpm} 或 {torque_nm}，可选 allowable_shear_mpa）、"
                "housing_wall（铸造箱体壁厚经验估算，params: {center_distance_mm | shaft_diameter_mm}）。"
                "kind=custom 时提交纯算术代码（code + variables），沙箱只允许数学函数与 for/if，"
                "无 import/属性访问/下标，必须以 result = ... 输出；5 秒超时。"
                "多零件任务（如变速箱）必须先用它调研：分级、齿数、模数、中心距、轴径，再 propose_plan。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string",
                             "enum": [*_BUILTIN_CALC_KINDS, "custom"],
                             "description": "计算类型"},
                    "params": {"type": "object",
                               "description": "内置 kind 的参数（custom 忽略）"},
                    "code": {"type": "string",
                             "description": "custom：纯算术代码，必须以 result = 输出"},
                    "variables": {"type": "object",
                                  "description": "custom：注入代码的数值变量 {名字: 数字}"},
                    "reason": {"type": "string",
                               "description": "本次计算要回答的问题（进过程记录，可选）"},
                },
                "required": ["kind"],
            },
        },
    }


def _run_build_script_tool() -> dict[str, Any]:
    """合成工具：建模脚本通道（DSH 式）——复杂零件一次脚本完成，几何仍只能走 kernel 公开 op。"""
    return {
        "type": "function",
        "function": {
            "name": "run_build_script",
            "description": (
                "建模脚本通道：复杂零件（箱体/阶梯轴/加强筋/孔阵列/多特征组合）用一段 Python 脚本一次完成，"
                "替代几十次原子 op 往返。脚本里只能用 `k`（kernel 公开 op 门面：k.create_workplane/k.new_sketch/"
                "k.add_rectangle/k.add_circle/k.close_sketch/k.extrude/k.hole/k.boolean/k.fillet/k.chamfer/"
                "k.shell/k.make_gear/k.select/k.measure…全量公开 op 同名直调，或 k.execute(op, **kw)）和 `math`；"
                "支持循环/变量/函数/条件；每个 op 返回 StepResult（用 r['success'] 判断，失败可 raise 中断）；"
                "print() 调试输出会回传。禁止 import build123d/文件/网络（几何主权归内核）。"
                "失败自动回滚到执行前状态并回传原始 traceback；成功执行的 op 进特征历史，"
                "与原子 op 一样可参数重放。零件建完后用 finish_part 归档。"
                "v2.16 起：脚本内任一 op 失败（r['success']=False）默认即中断整个脚本并回滚，"
                "返回 SCRIPT_OP_FAILED + failed_op（半成品绝不静默交付）；"
                "需要条件回退时用 try/except 包住单个 op，或显式 failure_policy=best_effort。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string",
                             "description": "Python 脚本（只用 k 与 math；必须产生几何变化）"},
                    "reason": {"type": "string",
                               "description": "本脚本建哪个零件/达成什么目标（过程记录，可选）"},
                    "failure_policy": {"type": "string", "enum": ["abort", "best_effort"],
                                       "description": "默认 abort：任一 op 失败即整体回滚；best_effort：跑完但失败 op 会显式上报（success=false）"},
                },
                "required": ["code"],
            },
        },
    }


def _finish_part_tool() -> dict[str, Any]:
    """合成工具：当前零件完成 → 归档导出 STEP/STL → 清空内核会话开始下一件。"""
    return {
        "type": "function",
        "function": {
            "name": "finish_part",
            "description": (
                "多零件计划（BOM）中一个零件建模完成时调用：把当前几何导出为 "
                "part_NN_<零件名>.step/.stl 归档、在计划里把该零件的步骤打勾，"
                "然后清空内核会话，让你开始下一个零件。"
                "需要计划已批准；调用前当前会话必须有几何。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "part": {"type": "string",
                             "description": "零件名（与计划 bom/步骤的 part 对应，如 '齿轮 z=20'）"},
                    "note": {"type": "string", "description": "本件完成说明（可选）"},
                    "feature_contract": {
                        "type": "array",
                        "description": (
                            "特征契约（强烈建议提供）：断言零件应有的特征。两种形式——"
                            "①圆柱面计数 {radius_mm, count}（如 Ø9 孔×4 → {radius_mm: 4.5, count: 4}）；"
                            "②**孔语义契约** {type, diameter_mm, count, positions?, tolerance_mm?}，"
                            "type ∈ through_hole|blind_hole|counterbore_hole|countersink_hole，"
                            "系统用 query what=holes 实测（外凸台不算孔；贯通/深度/位置都要吻合）。"
                            "归档前实测不符即拒绝——防止 undo 回滚掉特征或拿 boss 冒充 hole 后谎报完成。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "radius_mm": {"type": "number"},
                                "count": {"type": "integer"},
                                "type": {"type": "string",
                                         "enum": ["through_hole", "blind_hole",
                                                  "counterbore_hole", "countersink_hole"]},
                                "diameter_mm": {"type": "number"},
                                "positions": {"type": "array",
                                              "description": "孔位中心列表 [[x,y] 或 [x,y,z], ...]"},
                                "tolerance_mm": {"type": "number"},
                            },
                        },
                    },
                },
                "required": ["part"],
            },
        },
    }


def _propose_plan_tool() -> dict[str, Any]:
    """合成工具：计划模式下先产出建模计划，等待用户批准后才允许改几何。"""
    return {
        "type": "function",
        "function": {
            "name": "propose_plan",
            "description": (
                "计划模式：先只读研究（query/select/measure/render）并用 ask_user 澄清关键尺寸，"
                "然后调用本工具产出分步建模计划等待用户批准。批准前不得调用任何改变几何的 op。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "一句话总体方案"},
                    "bom": {
                        "type": "array",
                        "description": (
                            "多零件任务必填：零件清单（要几个零件、分别是什么、关键参数）。"
                            "单零件任务可省略。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "part": {"type": "string", "description": "零件名（唯一，步骤的 part 字段引用它）"},
                                "role": {"type": "string", "description": "功能说明（如：高速级小齿轮）"},
                                "quantity": {"type": "integer", "description": "数量，默认 1"},
                                "key_params": {"type": "object",
                                               "description": "关键设计参数（如 {\"模数\": \"2\", \"齿数\": \"20\", \"齿宽\": \"18\"}），应来自 design_calculate 调研结果"},
                                "depends_on": {"type": "array", "items": {"type": "string"},
                                               "description": "装配/设计依赖的其它零件名，可选"},
                                "pose": {
                                    "type": "object",
                                    "description": (
                                        "装配位姿（多零件任务必填）：position=[x,y,z] mm 世界坐标、"
                                        "rotation_deg=[角度,[ax,ay,az]] 可选；数值应来自 design_calculate "
                                        "调研（中心距/轴长/凸台位置），全部零件以此摆进装配。"
                                    ),
                                },
                            },
                            "required": ["part"],
                        },
                    },
                    "steps": {
                        "type": "array",
                        "description": "有序建模步骤（多零件任务按零件分组排列）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "description": "步骤标识（s1、s2…）"},
                                "title": {"type": "string", "description": "人话描述这步做什么"},
                                "op": {"type": "string", "description": "预计调用的 kernel op，可选"},
                                "rationale": {"type": "string", "description": "为什么这样安排，可选"},
                                "part": {"type": "string", "description": "本步所属零件名（对应 bom.part），多零件任务必填"},
                            },
                            "required": ["id", "title"],
                        },
                    },
                },
                "required": ["summary", "steps"],
            },
        },
    }


def _update_plan_tool() -> dict[str, Any]:
    """合成工具：执行期更新计划进度（整表替换），前端渲染为实时清单。"""
    return {
        "type": "function",
        "function": {
            "name": "update_plan",
            "description": (
                "执行计划时更新各步骤状态：开始某步前置 in_progress，成功后置 completed，"
                "整表一次提交。每轮至多调用一次。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                            },
                            "required": ["id", "status"],
                        },
                    },
                },
                "required": ["todos"],
            },
        },
    }


# 计划模式下、计划获批前允许调用的只读 op（研究用）；其余建模 op 一律拦截。
READONLY_OPS = frozenset({"query", "select", "measure", "render", "validate_geometry"})
# design_calculate 是纯算术调研工具（无几何副作用），计划门控期间必须可用；
# finish_part 涉及归档+清空，只在全量工具表（计划批准后）暴露。
_SYNTHETIC_TOOLS = frozenset({"ask_user", "propose_plan", "update_plan", "design_calculate"})


def _is_destructive(op: str, args: dict[str, Any]) -> bool:
    """判断某 op 是否为破坏性操作，需在确认点征求用户。"""
    if op == "delete_feature":
        return True
    if op in {"extrude", "revolve", "sweep", "boolean"} and args.get("confirm_replace") is True:
        return True
    if op == "shell":
        return True
    return False


def _destructive_message(op: str, args: dict[str, Any]) -> str:
    if op == "delete_feature":
        return f"即将删除特征 {args.get('feature_id', '?')}，其依赖者可能失效。是否继续？"
    if args.get("confirm_replace"):
        return f"op {op} 将替换当前零件（confirm_replace），现有几何会丢失。是否继续？"
    if op == "shell":
        return f"op {op} 将抽壳（移除部分材料）。是否继续？"
    return f"op {op} 是破坏性操作，是否继续？"


def _context_body(worker, capabilities: dict[str, Any]) -> str:
    """当前特征图 + 可用公开 op 的动态上下文（每次开新任务时取最新）。"""
    parts: list[str] = []
    try:
        tree = worker.feature_tree()
        graph = tree.get("graph") or {}
        nodes = graph.get("nodes") or {}
        history = tree.get("op_history") or []
        if nodes:
            lines = []
            for entry in history:
                feature_id = entry.get("feature_id") or entry.get("id")
                node = nodes.get(feature_id) or {}
                lines.append(f"- {feature_id}: {node.get('type', entry.get('op'))} ({node.get('state', '??')})")
            parts.append("当前特征历史（按序，最新在后）：\n" + "\n".join(line for line in lines if "- " in line))
        else:
            parts.append("当前没有特征（全新零件）。")
        public_ops = ", ".join(cap["name"] for cap in capabilities.get("public", []))
        parts.append(f"可用 op：{public_ops}")
    except Exception as exc:  # noqa: BLE001 —— 上下文失败不阻断
        parts.append(f"（读取当前特征上下文失败: {exc}）")
    return "\n".join(parts)


def _opening_context(task_description: str, worker, capabilities: dict[str, Any]) -> str:
    """首条用户消息文本：任务 + 当前特征上下文。把"暂停→接管→交还"的连续性交给模型。"""
    return f"任务：{task_description}\n{_context_body(worker, capabilities)}"


def build_task_message(
    task_description: str,
    worker,
    capabilities: dict[str, Any],
    image_data_url: str | None = None,
) -> dict[str, Any]:
    """开新任务的用户消息：任务文本 + 最新内核上下文，可携带草图图片。"""
    return build_user_message(
        f"任务：{task_description}\n{_context_body(worker, capabilities)}",
        image_data_url,
    )


def _args_preview(args: dict[str, Any], limit: int = _ARGS_PREVIEW_CLIP) -> str:
    """工具调用卡片的参数预览（截断）。"""
    try:
        rendered = json.dumps(args, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rendered = str(args)
    return rendered if len(rendered) <= limit else rendered[:limit] + "…"


def _part_slug(name: str) -> str:
    """零件名 → 安全文件名片段（保留中文，剔除 Windows 非法字符，限长 24）。"""
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", str(name)).strip("._")
    return (cleaned[:24] or "part")


class AgentLoop:
    def __init__(
        self,
        *,
        worker,
        chat_with_tools: ToolFn,
        protocol: str,
        emit: EmitFn,
        run_dir: Path,
        capabilities: dict[str, Any] | None = None,
        system_prompt: str,
        task_description: str = "",
        language: str = "zh",
        max_steps: int = 30,
        stop_event: threading.Event | None = None,
        approvals: ApprovalBroker | None = None,
        session: AgentSession | None = None,
        initial_user_message: dict[str, Any] | None = None,
        mode: str = "auto",
        project_id: str | None = None,
    ) -> None:
        self.worker = worker
        self.chat_with_tools = chat_with_tools
        self.protocol = protocol
        self.emit = emit
        # 绝对路径：kernel 子进程 cwd 在内核仓，相对路径会在那边解析失败
        self.run_dir = Path(run_dir).resolve()
        self.capabilities = capabilities or worker.capabilities()
        self.approvals = approvals
        self.max_steps = max_steps
        self.stop_event = stop_event
        self.language = language
        self.session = session
        # 计划模式：auto=直接建模；plan=先出计划待批准，批准前只允许只读 op
        self.mode = mode if mode in ("auto", "plan") else "auto"
        self._plan_approved = self.mode != "plan"
        self._all_tools = [
            *build_llm_tools(self.capabilities),
            _ask_user_tool(),
            _propose_plan_tool(),
            _update_plan_tool(),
            _design_calculate_tool(),
            _run_build_script_tool(),
            _finish_part_tool(),
            _export_assembly_tool(),
        ]
        self.tools = self._plan_mode_tools()
        self._context_descriptors: list[str] = []
        if session is not None:
            # 会话模式：历史取自 session（不含 system），新任务消息写入 session
            self.messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                *session.llm_messages(),
            ]
            task_message = initial_user_message or build_task_message(
                task_description, worker, self.capabilities,
            )
            self.messages.append(task_message)
            session.append(task_message)
            # v2.17：会话重启后恢复"计划已批准"执行态——否则续做装配/补件会被
            # PLAN_REQUIRED 卡死（计划与 BOM 已持久化在 session.plan，批准位必须一并恢复）。
            if isinstance(self.session.plan, dict) and self.session.plan.get("approved"):
                self._plan_approved = True
                self.tools = self._plan_mode_tools()
                self._approved_plan = dict(self.session.plan)
        else:
            self.messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _opening_context(task_description, worker, self.capabilities)},
            ]
            if initial_user_message is not None:
                self.messages.append(initial_user_message)
        self.logs: list[str] = []
        self.step_count = 0
        self._round_no = 0
        self._update_plan_this_round = False
        self._last_volume: float | None = None
        # v2.16（P1-2）：几何更新指纹——体积相同但拓扑/bbox 变化也必须重导出快照
        self._last_geometry_fingerprint: tuple | None = None
        # 批准后计划的本地副本（无 session 时 finish_part 打勾/广播用）
        self._approved_plan: dict[str, Any] | None = None
        # v0.13 当前零件的来源标记：ops | script（finish_part 记账后复位 ops）
        self._part_built_via = "ops"
        # v0.13.1 调研防打转：计划批准前 design_calculate 调用次数硬上限
        self._calc_calls = 0
        self._calc_budget = 8
        # v0.14 F2a：项目零件库归属（None = 不写库，仅 run 目录归档）
        self.project_id = str(project_id or "") or None
        # v0.13.2 计划未完成不得收工（只提醒一次，避免死循环）
        self._nagged_incomplete_plan = False
        # v0.14.1 空轮次瞬态重试计数
        self._empty_round_retries = 0
        # 旧注入（测试 fake 只接受 (messages, tools)）不支持增量回调时自动降级
        try:
            params = inspect.signature(chat_with_tools).parameters
            self._chat_supports_delta = "on_text_delta" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
        except (TypeError, ValueError):
            self._chat_supports_delta = False

    # ------------------------------------------------------------------ main
    def run(self) -> AgentLoopResult:
        result = AgentLoopResult(logs=self.logs)
        if self.session is not None:
            self.session.set_status("running")
        try:
            self._run_inner(result)
        except ApiCallError as exc:
            result.error = f"LLM 调用失败: {exc}"
            self.logs.append(result.error)
        except Exception as exc:  # noqa: BLE001 —— 汇总为失败结果，交由端点上报
            result.error = f"{type(exc).__name__}: {exc}"
            self.logs.append(result.error)
        finally:
            if self.session is not None:
                self.session.set_status("idle")
        try:
            self._finalize(result)
        except Exception as exc:  # noqa: BLE001
            self.logs.append(f"收尾导出失败: {type(exc).__name__}: {exc}")
        if result.error and not result.stopped:
            result.ok = False
        return result

    def _plan_has_pending(self) -> bool:
        """批准的计划里是否还有未完成步骤（用于"不得提前收工"门控）。"""
        steps: list = []
        if self.session is not None:
            steps = (self.session.plan or {}).get("steps") or []
        elif self._approved_plan:
            steps = self._approved_plan.get("steps") or []
        return any(s.get("status") != "completed" for s in steps if isinstance(s, dict))

    def _absorb_pending(self) -> None:
        """把用户在运行中插入的消息注入对话（轮间生效）。"""
        if self.session is None:
            return
        for message in self.session.drain_pending():
            self.messages.append(message)
            self.logs.append("已注入用户插话。")

    def _remember(self, message: dict[str, Any]) -> None:
        """追加进 LLM 对话；会话模式下同步写入 session 持久化。"""
        self.messages.append(message)
        if self.session is not None:
            self.session.append(message)

    def _plan_mode_tools(self) -> list[dict[str, Any]]:
        """计划未批准时只暴露只读 op + 合成工具；批准后放开全量工具表。"""
        if self._plan_approved:
            return self._all_tools
        public_names = {cap["name"] for cap in self.capabilities.get("public", [])}
        allowed = (READONLY_OPS & public_names) | _SYNTHETIC_TOOLS
        return [t for t in self._all_tools if t["function"]["name"] in allowed]

    def _handle_propose_plan(self, tool_call: ToolCall) -> dict[str, Any]:
        """计划审批：approve/edit 批准后进入执行；reject 留在计划模式并回喂反馈。

        v0.12：计划可携带 BOM（零件清单）；steps 每项可标注所属零件。多零件计划
        批准后回喂逐件执行协议（建模 → finish_part 归档 → 下一件）。
        """
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        summary = str(args.get("summary") or "（未提供方案摘要）")
        steps = _normalize_plan_steps(args.get("steps"))
        bom = _normalize_bom(args.get("bom"))
        # v0.15 提示词工程 §2 程序化：多零件计划的 BOM 参数表（key_params）是硬门——
        # 没有参数表的计划等于没有规划，直接打回，不允许"先批了再补"。
        if len(bom) >= 2:
            missing_params = [b.get("part") for b in bom if not b.get("key_params")]
            if missing_params:
                return tool_result_message(
                    tool_call,
                    json.dumps({
                        "success": False,
                        "error_kind": "BOM_MISSING_PARAMS",
                        "error": f"以下零件缺少 key_params 参数表: {missing_params}。"
                                 "多零件计划的每个零件必须先给出关键尺寸参数"
                                 "（来自 design_calculate 调研，含数值与单位），"
                                 "补齐后重新 propose_plan。",
                        "required_action": "add_key_params_then_replan",
                    }, ensure_ascii=False),
                    protocol=self.protocol,
                )
        plan_payload: dict[str, Any] = {"summary": summary, "steps": steps}
        if bom:
            plan_payload["bom"] = bom
        decision = self._request_approval(
            "plan_review", "propose_plan", {"summary": summary, "steps": steps, "bom": bom},
            summary, options={"plan": dict(plan_payload)},
        )
        action = str(decision.get("action"))
        if action in ("approve", "edit"):
            if action == "edit":
                revised = _normalize_plan_steps(decision.get("args", {}).get("steps"))
                if revised:
                    steps = revised
                revised_bom = _normalize_bom(decision.get("args", {}).get("bom"))
                if revised_bom:
                    bom = revised_bom
                plan_payload = {"summary": summary, "steps": steps}
                if bom:
                    plan_payload["bom"] = bom
            self._plan_approved = True
            self.tools = self._plan_mode_tools()
            self._approved_plan = {"summary": summary, "steps": steps, "approved": True}
            if bom:
                self._approved_plan["bom"] = bom
            if self.session is not None:
                self.session.set_plan(summary, steps, approved=True, bom=bom or None)
            self.emit("plan_updated", "计划已批准", plan_payload)
            note = "计划已批准。开始逐步执行：每步开始前用 update_plan 置 in_progress，成功后置 completed。"
            if bom:
                note += (
                    f"本计划含 {len(bom)} 类零件。逐件执行：建完一个零件调用 "
                    "finish_part(part=零件名) 归档导出并清空会话，再开始下一零件；"
                    "禁止把多个零件建在同一会话里互相融合。"
                )
            result = {
                "success": True,
                "approved": True,
                "note": note,
                "steps": steps,
            }
            if bom:
                result["bom"] = bom
        else:
            feedback = str(decision.get("message") or decision.get("args", {}).get("feedback") or "用户要求修改计划")
            result = {
                "success": True,
                "approved": False,
                "note": f"用户未批准计划：{feedback}。请据此调整后再次调用 propose_plan，期间仍只能只读研究、design_calculate 或 ask_user。",
            }
        return tool_result_message(tool_call, json.dumps(result, ensure_ascii=False), protocol=self.protocol)

    def _handle_update_plan(self, tool_call: ToolCall) -> dict[str, Any]:
        """执行期更新计划进度（整表替换，每轮至多一次）。"""
        if self._update_plan_this_round:
            return tool_result_message(
                tool_call,
                json.dumps({"success": False, "error": "本轮已调用过 update_plan，整表替换语义下每轮至多一次"}, ensure_ascii=False),
                protocol=self.protocol,
            )
        self._update_plan_this_round = True
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        todos = args.get("todos") if isinstance(args.get("todos"), list) else []
        statuses = {
            str(t.get("id")): str(t.get("status"))
            for t in todos
            if isinstance(t, dict) and t.get("id")
        }
        if self.session is not None:
            self.session.update_plan_status(statuses)
            plan = self.session.plan_dict()
        else:
            plan = {"summary": "", "steps": [{"id": k, "status": v} for k, v in statuses.items()]}
        self.emit("plan_updated", "计划进度更新", plan)
        return tool_result_message(
            tool_call,
            json.dumps({"success": True, "note": "计划进度已更新。"}, ensure_ascii=False),
            protocol=self.protocol,
        )

    def _run_inner(self, result: AgentLoopResult) -> None:
        while self.step_count < self.max_steps:
            if self.stop_event is not None and self.stop_event.is_set():
                result.stopped = True
                self.logs.append("收到停止请求，agent 退出。")
                return
            self._absorb_pending()
            self._round_no += 1
            self._update_plan_this_round = False
            streamed = {"active": False}

            def on_delta(chunk: str) -> None:
                streamed["active"] = True
                self.emit("agent_text_delta", chunk, {"round": self._round_no})

            if self._chat_supports_delta:
                round = self.chat_with_tools(self.messages, self.tools, on_text_delta=on_delta)
            else:
                round = self.chat_with_tools(self.messages, self.tools)
            if round.text or round.tool_calls:
                self._empty_round_retries = 0
            if round.text:
                self.logs.append(f"模型输出: {round.text[:500]}")
                if not streamed["active"]:
                    # 非流式：整轮文字一次性播出（流式时已逐段回调，不重复）
                    self.emit("agent_text_delta", round.text, {"round": self._round_no, "done": not round.tool_calls})
            if not round.tool_calls:
                # v0.14.1 推理模型偶发整轮空返回（无文字无 tool_calls）：这是 API 瞬态，
                # 不是任务完成——注入 nudge 重试（至多 2 次），避免 agent 静默"成功"收尾。
                if not (round.text or "").strip() and self._empty_round_retries < 2:
                    self._empty_round_retries += 1
                    self._remember({
                        "role": "user",
                        "content": ("你上一轮的回复为空。请继续任务：调研完成就调用 ask_user 或 "
                                    "propose_plan；全部完成就输出最终总结文字。"),
                    })
                    self.logs.append("模型返回空轮次，已注入提醒重试。")
                    continue
                # v0.13.2 计划未完成不得收工：模型停止时若批准的计划里还有未完成
                # 步骤/零件，注入提醒让它继续（防止"归档第一件就收尾"）。
                if self._plan_approved and self._plan_has_pending():
                    if not self._nagged_incomplete_plan:
                        self._nagged_incomplete_plan = True
                        self._remember({"role": "assistant", "content": round.text or ""})
                        self._remember({
                            "role": "user",
                            "content": ("计划尚未完成：还有未完成的零件/步骤。请继续逐件建模，"
                                        "直到计划中所有零件都已 finish_part 归档；不要现在写总结。"),
                        })
                        self.logs.append("计划未完成，已提醒模型继续。")
                        continue
                result.final_text = round.text
                result.ok = True
                if round.text:
                    self._remember({"role": "assistant", "content": round.text})
                return
            self._remember(round.raw_message)
            for tool_call in round.tool_calls:
                if self.step_count >= self.max_steps:
                    break
                if self.stop_event is not None and self.stop_event.is_set():
                    break
                if tool_call.name == "ask_user":
                    self._remember(self._handle_ask_user(tool_call))
                elif tool_call.name == "propose_plan":
                    self._remember(self._handle_propose_plan(tool_call))
                elif tool_call.name == "update_plan":
                    self._remember(self._handle_update_plan(tool_call))
                elif tool_call.name == "design_calculate":
                    self._remember(self._handle_design_calculate(tool_call, result))
                elif tool_call.name == "run_build_script":
                    self._remember(self._handle_run_build_script(tool_call, result))
                elif tool_call.name == "finish_part":
                    self._remember(self._handle_finish_part(tool_call, result))
                elif tool_call.name == "export_assembly":
                    self._remember(self._handle_export_assembly(tool_call, result))
                else:
                    self._remember(self._execute_tool_call(tool_call, result))
        if self.step_count >= self.max_steps:
            result.stopped = True
            # v2.16（P0-4）：撞上限绝不是成功——即使有部分产物也只算半成品。
            result.ok = False
            if result.error_kind is None:
                result.error_kind = "MAX_STEPS_REACHED"
                result.error = "达到步数上限，任务未完成（半成品不得判定为成功）。"
            self.logs.append("达到步数上限，停止执行。")
        if self.stop_event is not None and self.stop_event.is_set():
            result.stopped = True
            self.logs.append("收到停止请求，agent 退出。")

    # ------------------------------------------------------------- execution
    def _execute_tool_call(self, tool_call: ToolCall, result: AgentLoopResult) -> dict[str, Any]:
        self.step_count += 1
        step_no = self.step_count
        op = tool_call.name
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        data = self._call_op(op, args)
        self._emit_step(step_no, op, data, autofix=False, args=args)
        result.steps = self.step_count

        # 自修复：RECOVERABLE + fix → 按 schema 过滤后合并重试一次。
        # 破坏性修复（fix 要求 confirm_replace）需征求用户；其余自动重试。
        if (
            not data.get("success")
            and data.get("error_kind") == "RECOVERABLE"
            and isinstance((data.get("suggestion") or {}).get("fix"), dict)
            and self.step_count < self.max_steps
        ):
            fix = data["suggestion"]["fix"]
            if self.approvals is not None and fix.get("confirm_replace") is True:
                # 用户批准破坏性替换 → 用 fix 合并后的参数执行
                proposed_args = {**args, **fix}
                decision = self._request_approval(
                    "destructive_fix", op, proposed_args,
                    f"上一步 {op} 失败：需要替换当前零件（confirm_replace）。是否继续？",
                    options={"fix": fix},
                )
                if decision["action"] == "reject":
                    data = {"success": False, "error_kind": "REJECTED",
                            "error": "用户拒绝了破坏性替换。除非用户明确要求，否则不要重试。", "suggestion": decision.get("message", "")}
                    self._emit_step(self.step_count, op, data, autofix=True, message="破坏性修复被用户拒绝")
                elif decision["action"] == "timeout":
                    data = {"success": False, "error_kind": "REJECTED",
                            "error": decision.get("message", "用户未在时限内响应，视为拒绝破坏性替换。不要重试，可换方案或继续。")}
                    self._emit_step(self.step_count, op, data, autofix=True, message="破坏性修复超时，视为拒绝")
                else:
                    merged = filter_args_to_schema(op, dict(decision.get("args") or proposed_args), self.capabilities)
                    self.step_count += 1
                    self._emit_step(self.step_count, op, None, autofix=True,
                                    message=f"破坏性修复（用户批准）: {json.dumps(merged, ensure_ascii=False)}")
                    result.steps = self.step_count
                    # 已确认过破坏性，跳过二次审批
                    data = self._call_op(op, merged, skip_approval=True)
                    self._emit_step(self.step_count, op, data, autofix=True,
                                    message=f"修复结果: {'成功' if data.get('success') else '仍失败'}")
                    args = merged
            else:
                merged = filter_args_to_schema(op, {**args, **fix}, self.capabilities)
                if merged != filter_args_to_schema(op, args, self.capabilities):
                    self.step_count += 1
                    self._emit_step(self.step_count, op, None, autofix=True,
                                    message=f"自动修复重试（应用 fix: {json.dumps(fix, ensure_ascii=False)}）")
                    result.steps = self.step_count
                    data = self._call_op(op, merged)
                    self._emit_step(self.step_count, op, data, autofix=True,
                                    message=f"自动修复重试结果: {'成功' if data.get('success') else '仍失败'}")
                    args = merged

        self._track_geometry(data, result)
        tool_result = _compact_step_result(data)
        return tool_result_message(tool_call, json.dumps(tool_result, ensure_ascii=False), protocol=self.protocol)

    def _call_op(self, op: str, args: dict[str, Any], *, skip_approval: bool = False) -> dict[str, Any]:
        public_names = {cap["name"] for cap in self.capabilities.get("public", [])}
        if op not in public_names:
            return {
                "success": False,
                "error_kind": "INVALID_REQUEST",
                "error": f"未知 op: {op}（只允许 capabilities 里列出的公开 op）",
            }
        # 计划模式：计划获批前只允许只读 op，建模 op 一律拦下（工具表已过滤，此为兜底）。
        if not self._plan_approved and op not in READONLY_OPS:
            return {
                "success": False,
                "error_kind": "PLAN_REQUIRED",
                "error": "计划模式：请先调用 propose_plan 产出建模计划并获用户批准，再执行改变几何的 op。",
            }
        # 破坏性操作在执行前征求用户（P2 确认点）。skip_approval：该步已在审批流程内确认过。
        if self.approvals is not None and not skip_approval and _is_destructive(op, args):
            decision = self._request_approval("destructive_op", op, args, _destructive_message(op, args))
            if decision["action"] == "reject":
                return {"success": False, "error_kind": "REJECTED",
                        "error": "用户拒绝了该步骤。除非用户明确要求，否则不要重试该操作；可改用其它方案或跳过。",
                        "suggestion": decision.get("message", "")}
            if decision["action"] == "timeout":
                return {"success": False, "error_kind": "REJECTED",
                        "error": decision.get("message", "用户未在时限内响应，视为拒绝。不要重试该操作，可换方案或继续。")}
            if decision["action"] in ("approve", "edit"):
                args = dict(decision.get("args") or args)  # 用户可改参
        try:
            return self.worker.execute(op, args)
        except Exception as exc:  # noqa: BLE001 —— worker 崩溃/超时转为工具失败结果
            return {
                "success": False,
                "error_kind": "WORKER_DEAD",
                "error": f"worker 调用失败: {type(exc).__name__}: {exc}",
            }

    # ------------------------------------------------------------- geometry
    def _track_geometry(self, data: dict[str, Any], result: AgentLoopResult) -> None:
        summary = data.get("geometry_summary") or {}
        volume = summary.get("volume")
        if not data.get("success") or not volume:  # 无几何/空体积不触发导出
            return
        result.volume = float(volume)
        # v2.16（P1-2）：体积只是指纹的一个分量——孔位移动/零件变形/删一孔加一孔
        # 这类"体积不变但几何变了"的情况必须重新导出 STL + 快照，否则视觉反馈滞后。
        fingerprint = (
            round(float(volume), 6),
            tuple(round(float(v), 6) for v in (summary.get("bounding_box") or [])),
            summary.get("face_count"), summary.get("edge_count"),
            summary.get("vertex_count"), summary.get("feature_count"),
        )
        if self._last_geometry_fingerprint == fingerprint:
            return
        self._last_geometry_fingerprint = fingerprint
        self._last_volume = float(volume)
        stl_path = self.run_dir / "model.stl"
        try:
            mesh = self.worker.export_mesh(str(stl_path))
            if mesh.get("size"):
                result.artifacts["stl"] = str(stl_path)
                # run_id + url let the UI live-preview the mesh while the agent is
                # still building (the viewport otherwise stays empty until the run
                # commits its final artifact set).
                self.emit("artifact_ready", "geometry updated, 3D mesh exported", {
                    "kind": "stl",
                    "path": str(stl_path),
                    "size": mesh.get("size"),
                    "run_id": self.run_dir.name,
                    "url": f"/api/artifacts/{self.run_dir.name}/stl",
                })
        except Exception as exc:  # noqa: BLE001 —— 网格导出失败不阻断建模
            self.logs.append(f"STL 导出失败: {type(exc).__name__}: {exc}")
        self._render_snapshot(result)

    def _render_snapshot(self, result: AgentLoopResult) -> None:
        """几何变化后的可视化快照：kernel render → PNG 落盘 → WS 发 URL。

        base64 不回喂 LLM（会撑爆上下文），只落盘并经 ``agent_snapshot`` 事件
        给前端会话流内嵌展示。
        """
        try:
            data = self.worker.render_snapshot(size=480)
        except Exception as exc:  # noqa: BLE001 —— 渲染失败不阻断建模
            self.logs.append(f"快照渲染失败: {type(exc).__name__}: {exc}")
            return
        b64 = data.get("render_base64")
        if not b64:
            return
        try:
            step_no = max(self.step_count, 1)
            path = self.run_dir / f"snapshot_s{step_no}.png"
            path.write_bytes(base64.b64decode(b64))
        except Exception as exc:  # noqa: BLE001
            self.logs.append(f"快照落盘失败: {type(exc).__name__}: {exc}")
            return
        result.artifacts[f"snapshot_s{step_no}"] = str(path)
        self.emit("agent_snapshot", "几何快照已更新", {
            "step": step_no,
            "url": f"/api/artifacts/{self.run_dir.name}/snapshot_s{step_no}",
            "path": str(path),
        })

    # ---------------------------------------------------------------- finish
    def _finalize(self, result: AgentLoopResult) -> None:
        try:
            tree = self.worker.feature_tree()
        except Exception as exc:  # noqa: BLE001
            tree = {}
            self.logs.append(f"读取 feature_tree 失败: {type(exc).__name__}: {exc}")
        result.feature_tree = tree
        result.feature_graph = tree.get("graph") or {}

        validation: dict[str, Any] = {}
        if result.volume:
            try:
                validation = self.worker.execute("validate_geometry", {"level": "strict"})
            except Exception as exc:  # noqa: BLE001
                self.logs.append(f"validate_geometry 失败: {type(exc).__name__}: {exc}")

        # ---- v2.16 可靠性硬门控（P0-1/P0-4）----
        # "成功"不再由模型文字决定：必须同时满足有真实产物、计划完成、几何验证通过。
        # 半成品（有产物但未完成计划/验证不过）一律 ok=false。
        if result.ok:
            if result.stopped:
                result.ok = False
                result.error_kind = "MAX_STEPS_REACHED" if self.step_count >= self.max_steps else "STOPPED"
                result.error = "agent 未正常收尾（步数耗尽或被停止），不得判定为成功。"
            elif self._plan_approved and self._plan_has_pending():
                result.ok = False
                result.error_kind = "PLAN_INCOMPLETE"
                result.error = "批准的计划仍有未完成步骤/零件，不得判定为成功。"
            elif not result.volume and not result.parts and not result.assembly:
                result.ok = False
                result.error_kind = "NO_GEOMETRY"
                result.error = "会话结束但没有产生任何有效几何/归档零件/装配交付，不得判定为成功。"
            elif result.parts:
                bad = [p.get("part") for p in result.parts
                       if isinstance(p, dict) and not p.get("validation_passed")]
                if bad:
                    result.ok = False
                    result.error_kind = "PART_VALIDATION_FAILED"
                    result.error = f"以下零件未通过 strict 几何验证: {bad}"
            elif result.volume:
                # 单件任务：最终会话几何必须过 strict 验证
                gv = validation.get("geometry_validation") or {}
                if gv.get("valid") is not True or gv.get("status") != "valid":
                    result.ok = False
                    result.error_kind = "GEOMETRY_INVALID"
                    result.error = ("最终几何 strict 验证未通过，不得判定为成功: "
                                    f"{gv}")
            # 纯装配续做（本 run 只 export_assembly，零件在既往 run 已验证归档）：
            # 无会话几何可验证，assembly 即产物，不再叠加校验。

        # v0.15 程序判态（提示词工程 §8）：SUCCESS / PARTIAL / FAILED 由验证结果算出，
        # 不采信模型自述——PARTIAL（有产物但未全过）不得被说成 SUCCESS。
        has_artifact = bool(result.volume or result.parts or result.assembly)
        if result.ok and not result.error:
            result.status = "SUCCESS"
        elif has_artifact:
            result.status = "PARTIAL"
        else:
            result.status = "FAILED"

        step_path = self.run_dir / "model.step"
        if result.volume:
            try:
                self.worker.export_step(str(step_path))
                result.artifacts["step"] = str(step_path)
            except Exception as exc:  # noqa: BLE001
                self.logs.append(f"STEP 导出失败: {type(exc).__name__}: {exc}")
                if result.ok:
                    result.ok = False
                    result.error_kind = "EXPORT_FAILED"
                    result.error = f"最终 STEP 导出失败: {type(exc).__name__}: {exc}"

        report = {
            # 多零件任务以归档件数计完成度（末件 finish_part 后会话已清空）
            "ok": bool(result.ok and not result.error) and bool(result.volume or result.parts),
            "status": result.status,
            "engine": "mechkernel",
            "worker": "mechkernel-agent",
            "agent_stopped": result.stopped,
            "steps": result.steps,
            "final_text": result.final_text,
            "volume": result.volume,
            "parts": result.parts,
            "design_calculations": result.design_calculations,
            "assembly": result.assembly,
            "feature_graph": result.feature_graph,
            "op_history": tree.get("op_history") or [],
            "narrative": (tree.get("narrative") or [])[-_NARRATIVE_CLIP:],
            "geometry_validation": validation.get("geometry_validation"),
            "logs": self.logs,
        }
        if result.error:
            report["error"] = result.error
        if result.error_kind:
            report["error_kind"] = result.error_kind
        report_path = self.run_dir / "execution_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        result.artifacts["execution_report"] = str(report_path)

    # ------------------------------------------------------------- approvals
    def _request_approval(self, kind: str, op: str, args: dict[str, Any], message: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
        """请求用户审批并阻塞等待；先登记拿 approval_id 再广播（前端回复依赖 id）。"""
        if self.approvals is None:
            return {"action": "approve", "args": dict(args)}
        request = self.approvals.create(kind=kind, op=op, args=args, message=message, options=options)
        request_info: dict[str, Any] = {
            "approval_id": request.approval_id,
            "kind": kind,
            "op": op,
            "args": dict(args),
            "message": message,
            "options": options or {},
        }
        if self.session is not None:
            self.session.set_status("waiting_approval")
        self.emit("approval_required", message, request_info)
        self.logs.append(f"审批等待: {message}")
        decision = self.approvals.wait(request)
        if self.session is not None:
            self.session.set_status("running")
            # 审批等待期间用户可能插话，答复后立即注入
            self._absorb_pending()
        return decision

    def _handle_ask_user(self, tool_call: ToolCall) -> dict[str, Any]:
        """合成工具 ask_user：向用户提结构化问题，等答复，以 Q/A 转录回喂模型。"""
        self.step_count += 1
        self.emit("agent_step", "ask_user", {"step": self.step_count, "op": "ask_user"})
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        questions = _normalize_questions(args.get("questions"))
        if not questions:
            # 兼容旧单问形状（question/context）
            questions = _normalize_questions([
                {"id": "q1", "question": args.get("question") or args.get("context") or "（无问题文本）", "type": "text"},
            ])
        first = questions[0]["question"]
        message = first if len(questions) == 1 else f"{first}（共 {len(questions)} 个问题）"
        decision = self._request_approval(
            "ask_user", "ask_user", {"questions": questions}, message, options={"questions": questions},
        )
        action = str(decision.get("action"))
        answers: dict[str, Any] = {}
        if action in ("approve", "edit"):
            raw_answers = decision.get("args", {}).get("answers")
            if isinstance(raw_answers, dict):
                answers = raw_answers
        transcript = _format_ask_user_transcript(questions, answers, action)
        result = {"success": True, "declined": action in ("reject", "timeout"), "answers": answers, "transcript": transcript}
        return tool_result_message(tool_call, json.dumps(result, ensure_ascii=False), protocol=self.protocol)

    # ---------------------------------------------------- design research
    def _handle_design_calculate(self, tool_call: ToolCall, result: AgentLoopResult) -> dict[str, Any]:
        """合成工具 design_calculate：纯算术调研计算（内置 kind 或沙箱 custom）。"""
        from backend.agent import calc_sandbox
        from backend.agent.designcalc import CALCULATORS, CalcError

        self.step_count += 1
        result.steps = self.step_count
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        kind = str(args.get("kind") or "").strip()
        reason = str(args.get("reason") or "").strip()
        preview: dict[str, Any] = {"kind": kind}
        if kind == "custom":
            preview["code"] = str(args.get("code") or "")[:_ARGS_PREVIEW_CLIP]
        else:
            preview["params"] = args.get("params") or {}
        self.emit("agent_step", f"设计计算: {kind}", {
            "step": self.step_count,
            "op": "design_calculate",
            "args_preview": _args_preview(preview),
        })
        # v0.13.1 调研防打转硬门控：计划批准前累计超过预算即拒绝，强制推进到提问/计划。
        if not self._plan_approved:
            self._calc_calls += 1
            if self._calc_calls > self._calc_budget:
                return tool_result_message(
                    tool_call,
                    json.dumps({
                        "success": False,
                        "error_kind": "RESEARCH_BUDGET_EXCEEDED",
                        "error": f"调研预算已用完（{self._calc_budget} 次 design_calculate）。"
                                 "请立即停止计算：用已有结果写 2-3 个候选方案，"
                                 "然后 ask_user 澄清关键约束或直接 propose_plan 出 BOM 计划。",
                    }, ensure_ascii=False),
                    protocol=self.protocol,
                )
        entry: dict[str, Any] = {"step": self.step_count, "kind": kind}
        if reason:
            entry["reason"] = reason[:200]
        try:
            if kind == "custom":
                output = calc_sandbox.run_sandboxed(str(args.get("code") or ""), args.get("variables"))
            elif kind in CALCULATORS:
                calculator = CALCULATORS[kind]
                params = args.get("params") if isinstance(args.get("params"), dict) else {}
                output = calculator(**params)
            else:
                raise CalcError(
                    f"未知 kind: {kind!r}；可选 {sorted(CALCULATORS)} 或 'custom'"
                )
            payload: dict[str, Any] = {"success": True, "kind": kind, "result": output}
            entry["ok"] = True
        except (CalcError, calc_sandbox.SandboxError) as exc:
            payload = {"success": False, "error_kind": "INVALID_REQUEST",
                       "error": f"设计计算被拒绝: {exc}"}
            entry.update(ok=False, error=str(exc)[:300])
        except Exception as exc:  # noqa: BLE001 —— 计算崩溃不带走 agent
            payload = {"success": False, "error_kind": "CALC_ERROR",
                       "error": f"设计计算异常: {type(exc).__name__}: {exc}"}
            entry.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
        if reason:
            payload["reason"] = reason
        rendered = json.dumps(payload, ensure_ascii=False, default=str)
        if len(rendered) > 12000:  # 兜底截断，防个别参数组合撑爆上下文
            payload["result"] = "（结果过大已截断，请缩小 top_n 或只取关键值）"
            rendered = json.dumps(payload, ensure_ascii=False, default=str)
        result.design_calculations.append(entry)
        return tool_result_message(tool_call, rendered, protocol=self.protocol)

    # ---------------------------------------------------------- parts
    def _mark_part_steps_completed(self, part: str) -> list[str]:
        """finish_part 后把该零件名下的计划步骤打勾（session 优先，退到本地计划）。"""
        completed: list[str] = []
        if self.session is not None:
            steps = (self.session.plan or {}).get("steps") or []
            statuses = {s["id"]: "completed" for s in steps if s.get("part") == part}
            if statuses:
                self.session.update_plan_status(statuses)
                completed = sorted(statuses)
                self.emit("plan_updated", f"零件已归档: {part}", self.session.plan_dict())
            return completed
        plan = self._approved_plan
        if plan:
            for s in plan.get("steps") or []:
                if s.get("part") == part and s.get("status") != "completed":
                    s["status"] = "completed"
                    completed.append(str(s.get("id")))
            if completed:
                self.emit("plan_updated", f"零件已归档: {part}", dict(plan))
        return completed

    def _handle_run_build_script(self, tool_call: ToolCall, result: AgentLoopResult) -> dict[str, Any]:
        """合成工具 run_build_script：建模脚本通道（DSH 式原始 traceback + 检查点回滚）。"""
        self.step_count += 1
        result.steps = self.step_count
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        code = str(args.get("code") or "")
        reason = str(args.get("reason") or "").strip()
        self.emit("agent_step", f"建模脚本: {reason[:40] or 'script'}", {
            "step": self.step_count,
            "op": "run_build_script",
            "args_preview": _args_preview({"reason": reason, "code": code}),
        })
        if not self._plan_approved:
            payload: dict[str, Any] = {"success": False, "error_kind": "PLAN_REQUIRED",
                                       "error": "run_build_script 仅在计划获批准后使用。"}
        elif not code.strip():
            payload = {"success": False, "error_kind": "INVALID_REQUEST", "error": "code 不能为空。"}
        else:
            failure_policy = str(args.get("failure_policy") or "abort")
            if failure_policy not in ("abort", "best_effort"):
                failure_policy = "abort"
            try:
                data = self.worker.run_script(code, name=reason[:40] or "script",
                                              failure_policy=failure_policy)
            except Exception as exc:  # noqa: BLE001 —— worker 超时/崩溃（如脚本死循环）
                data = {"success": False, "error_kind": "WORKER_DEAD",
                        "error": f"worker 调用失败: {type(exc).__name__}: {exc}。"
                                 "若脚本含死循环会触发此路径；请简化循环规模后重试。"}
            payload = {
                "success": bool(data.get("success")),
                "error_kind": data.get("error_kind"),
                "error": (str(data.get("error") or ""))[:4000] or None,
                "warning": data.get("warning"),
            }
            value = data.get("value") if isinstance(data.get("value"), dict) else {}
            geometry = data.get("geometry_summary") if isinstance(data.get("geometry_summary"), dict) else {}
            if data.get("success"):
                payload.update(
                    solids=value.get("solids"),
                    volume=geometry.get("volume", value.get("volume")),
                    bounding_box=geometry.get("bounding_box", value.get("bounding_box")),
                    ops_executed=value.get("ops_executed"),
                    stdout=(str(value.get("stdout") or ""))[:2000] or None,
                    note="脚本执行成功，几何已更新（op 已进特征历史，可参数重放）。"
                         "请自检 solids/volume/bbox 是否符合预期；零件完成后 finish_part 归档。",
                )
                self._part_built_via = "script"
                # 合成工具不走 _execute_tool_call，手动触发 STL 导出 + 快照
                self._track_geometry(data, result)
            else:
                suggestion = data.get("suggestion") if isinstance(data.get("suggestion"), dict) else {}
                if isinstance(suggestion.get("failed_op"), dict):
                    payload["failed_op"] = suggestion["failed_op"]
                if isinstance(suggestion.get("failed_ops"), list):
                    payload["failed_ops"] = suggestion["failed_ops"]
                if data.get("error_kind") == "SCRIPT_OP_FAILED":
                    payload["note"] = ("脚本内有 op 失败（abort 策略已整体回滚，半成品不会交付）。"
                                       "按 failed_op 修正该步参数/顺序后重跑；"
                                       "需要条件回退时用 try/except 包住单个 op。")
                else:
                    payload["note"] = ("脚本执行失败，状态已回滚到执行前（无需清理）；"
                                       "按 traceback 修正后重跑 run_build_script。")
            result.logs.append(
                f"run_build_script: success={payload['success']} ops={value.get('ops_executed') if data.get('success') else '-'}")
        return tool_result_message(tool_call, json.dumps(payload, ensure_ascii=False, default=str),
                                   protocol=self.protocol)

    def _bom_entry(self, part: str) -> dict[str, Any] | None:
        """从批准计划（session 优先，退本地副本）里按零件名取 BOM 项。"""
        for item in self._bom_items():
            if isinstance(item, dict) and item.get("part") == part:
                return item
        return None

    def _bom_items(self) -> list[dict[str, Any]]:
        plan: dict[str, Any] = {}
        if self.session is not None:
            plan = self.session.plan or {}
        elif self._approved_plan:
            plan = self._approved_plan
        return [b for b in (plan.get("bom") or []) if isinstance(b, dict)]

    def _bom_part_names(self) -> set[str]:
        """批准 BOM 的零件名集合（空集=无 BOM，生命周期门控不生效）。"""
        return {str(b.get("part")) for b in self._bom_items() if b.get("part")}

    def _archive_to_parts_library(self, part: str, step_path: Path, stl_path: Path,
                                  volume: float) -> dict[str, Any] | None:
        """v0.14 F2a：把本次归档复制进项目零件库（版本递增）并更新 manifest + session 镜像。

        失败只记日志——run 目录归档仍是事实，装配导出会明确报缺件。"""
        if not self.project_id:
            return None
        from backend import storage

        try:
            manifest = storage.read_manifest(self.project_id)
            entries = [p for p in manifest.get("parts") or [] if isinstance(p, dict)]
            existing = next((p for p in entries if p.get("name") == part), None)
            version = int((existing or {}).get("version") or 0) + 1
            slug = _part_slug(part)
            lib_dir = storage.project_parts_dir(self.project_id)
            lib_dir.mkdir(parents=True, exist_ok=True)
            lib_step = f"v{version:03d}_{slug}.step"
            lib_stl = f"v{version:03d}_{slug}.stl"
            shutil.copyfile(step_path, lib_dir / lib_step)
            shutil.copyfile(stl_path, lib_dir / lib_stl)
            bom_item = self._bom_entry(part) or {}
            entry = {
                "name": part, "version": version, "run_id": self.run_dir.name,
                "step_file": lib_step, "stl_file": lib_stl,
                "built_via": self._part_built_via, "volume_mm3": round(float(volume), 2),
                "pose": bom_item.get("pose"),
                "contract_passed": True,
                "role": bom_item.get("role") or "",
                "depends_on": bom_item.get("depends_on") or [],
                # v2.17 P1-7 生命周期：active 才进装配；同名返工替换条目，
                # 被替换的旧版本文件保留在库目录（历史可回溯）但不再 active。
                "status": "active",
            }
            manifest["parts"] = [p for p in entries if p.get("name") != part] + [entry]
            storage.write_manifest(self.project_id, manifest)
            if self.session is not None:
                self.session.update_part_entry(entry)
            return {"version": version, "step_file": lib_step, "stl_file": lib_stl,
                    "pose": entry["pose"]}
        except Exception as exc:  # noqa: BLE001 —— 库写失败不阻断逐件交付
            self.logs.append(f"零件库写入失败（不影响 run 归档）: {type(exc).__name__}: {exc}")
            return None

    def _handle_export_assembly(self, tool_call: ToolCall, result: AgentLoopResult) -> dict[str, Any]:
        """合成工具 export_assembly：零件库 + 位姿 → 装配 STEP + 干涉 + 预览图 + 交付报告。"""
        self.step_count += 1
        result.steps = self.step_count
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        self.emit("agent_step", "装配导出", {
            "step": self.step_count, "op": "export_assembly", "args_preview": _args_preview(args),
        })
        if not self._plan_approved:
            payload: dict[str, Any] = {"success": False, "error_kind": "PLAN_REQUIRED",
                                       "error": "export_assembly 仅在计划获批准后使用。"}
        elif not self.project_id:
            payload = {"success": False, "error_kind": "INVALID_REQUEST",
                       "error": "项目零件库不可用（缺 project_id）。"}
        elif self._plan_has_pending():
            payload = {"success": False, "error_kind": "PLAN_INCOMPLETE",
                       "error": "计划中还有未归档零件：先完成全部 finish_part 再导出装配。"}
        else:
            payload = self._export_assembly_inner(args, result)
        return tool_result_message(tool_call, json.dumps(payload, ensure_ascii=False, default=str),
                                   protocol=self.protocol)

    def _classify_interference(self, interference: dict,
                               expected_overlaps: Any) -> dict[str, list[dict]]:
        """v2.17 P1-8：干涉分级 hard_collision / expected_fit / expected_mesh。

        豁免不再是一句"设计意图"就能盖住的口子：豁免项必须带 category
        （fit=轴孔配合 / mesh=齿轮啮合），未带时默认 fit。未豁免的干涉一律
        hard_collision → 阻断导出。历史残留件已在 P1-7 被排除出装配，
        不再可能以"豁免"名义混入。
        """
        cats: dict[str, list[dict]] = {"hard_collision": [], "expected_fit": [],
                                       "expected_mesh": [], "historical_artifact": []}
        overlaps = [o for o in (expected_overlaps or []) if isinstance(o, dict)]             if isinstance(expected_overlaps, list) else []

        def _category_for(a: str, b: str) -> str:
            for o in overlaps:
                names = {str(o.get("a") or ""), str(o.get("b") or "")}
                if names == {a, b}:
                    cat = str(o.get("category") or "fit").lower()
                    return cat if cat in ("fit", "mesh") else "fit"
            return "fit"

        for pair in interference.get("pairs") or []:
            if not isinstance(pair, dict) or not pair.get("interfering"):
                continue
            # 内核语义：pairs 只含未豁免对；豁免对在 interference["exempted"]
            cats["hard_collision"].append(pair)
        for pair in interference.get("exempted") or []:
            if not isinstance(pair, dict):
                continue
            key = "expected_mesh" if _category_for(str(pair.get("name_a") or ""),
                                                    str(pair.get("name_b") or "")) == "mesh"                 else "expected_fit"
            cats[key].append(pair)
        return cats

    def _export_assembly_inner(self, args: dict, result: AgentLoopResult) -> dict[str, Any]:
        from datetime import datetime

        from backend import storage

        manifest = storage.read_manifest(self.project_id or "")
        entries = [p for p in manifest.get("parts") or [] if isinstance(p, dict)]
        if not entries:
            return {"success": False, "error_kind": "EMPTY_LIBRARY",
                    "error": "项目零件库为空：先逐件 finish_part 归档。"}
        lib_dir = storage.project_parts_dir(self.project_id or "")
        # 绝对路径：worker 子进程 cwd 在内核仓，相对路径会在那边解析失败
        # （与 AgentLoop.__init__ resolve(run_dir) 同一教训）。
        lib_dir = lib_dir.resolve()

        # v2.17 P1-7：装配 = active ∩ 批准 BOM。superseded/BOM 外零件（如旧版
        # 箱体、改名重做的 输出轴IV-阶梯2）一律排除并标 superseded——历史残留
        # 不得再借"豁免"混进交付。
        bom_names = self._bom_part_names()
        active_entries: list[dict] = []
        excluded_entries: list[dict] = []
        for entry in entries:
            name = str(entry.get("name") or "")
            if entry.get("status") == "superseded" or (bom_names and name not in bom_names):
                if entry.get("status") != "superseded":
                    entry["status"] = "superseded"
                excluded_entries.append(entry)
                continue
            active_entries.append(entry)
        excluded = [str(e.get("name")) for e in excluded_entries]
        if bom_names:
            missing = sorted(bom_names - {str(e.get("name")) for e in active_entries})
            if missing:
                return {"success": False, "error_kind": "BOM_PARTS_MISSING",
                        "error": f"BOM 零件尚未归档，禁止导出半成品装配: {missing}"}
        entries = active_entries
        parts_payload = []
        for entry in entries:
            step_file = str(entry.get("step_file") or "")
            if not (lib_dir / step_file).exists():
                return {"success": False, "error_kind": "LIBRARY_BROKEN",
                        "error": f"零件 {entry.get('name')} 的库文件缺失: {step_file}"}
            parts_payload.append({
                "path": str(lib_dir / step_file),
                "name": str(entry.get("name")),
                "pose": entry.get("pose") or {"position": [0.0, 0.0, 0.0]},
            })
        seq = 1 + len(list(lib_dir.glob("assembly_*.step")))
        out_step = lib_dir / f"assembly_{seq:03d}.step"
        try:
            # v2.17 P1-8：干涉先行——存在未豁免硬碰撞直接阻断，不浪费导出/渲染，
            # 也不给半成品装配写 manifest。
            interference = self.worker.assembly_interference(
                parts_payload, expected_overlaps=args.get("expected_overlaps"))
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error_kind": "WORKER_ERROR",
                    "error": f"装配命令执行失败: {type(exc).__name__}: {exc}"}
        categories = self._classify_interference(interference, args.get("expected_overlaps"))
        hard = [p for p in categories["hard_collision"]]
        if hard:
            return {"success": False, "error_kind": "INTERFERENCE_BLOCKED",
                    "error": f"存在 {len(hard)} 对未豁免硬碰撞，装配导出被阻断："
                             + "；".join(f"{p['name_a']}×{p['name_b']} {round(p.get('volume_mm3') or 0, 1)}mm³"
                                          for p in hard[:10])
                             + "。修复几何或（仅设计意图内的配合/啮合）用 expected_overlaps 声明 category。",
                    "hard_collisions": hard[:20],
                    "interference_summary": {k: len(v) for k, v in categories.items()}}
        try:
            export = self.worker.export_assembly(parts_payload, str(out_step))
            render = self.worker.render_assembly(parts_payload)
        except Exception as exc:  # noqa: BLE001 —— worker 崩溃/超时
            return {"success": False, "error_kind": "WORKER_ERROR",
                    "error": f"装配命令执行失败: {type(exc).__name__}: {exc}"}
        render_file = report_file = None
        png = render.get("render_base64") if isinstance(render, dict) else None
        if png:
            try:
                import base64 as _b64
                render_file = f"assembly_{seq:03d}_render.png"
                (lib_dir / render_file).write_bytes(_b64.b64decode(png))
            except Exception:
                render_file = None
        exported_at = datetime.now().isoformat(timespec="seconds")
        report = {
            "exported_at": exported_at,
            "note": str(args.get("note") or ""),
            "parts": [{"name": e.get("name"), "version": e.get("version"),
                       "pose": e.get("pose")} for e in entries],
            "excluded_superseded": excluded,
            "export": {k: export.get(k) for k in ("parts", "solids", "volume", "bounding_box", "step_bytes")},
            "interference": interference,
            "interference_categories": {k: v for k, v in categories.items()},
        }
        try:
            report_file = f"assembly_{seq:03d}_report.json"
            (lib_dir / report_file).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            report_file = None
        summary = {
            "exported_at": exported_at,
            "step_file": out_step.name,
            "render_file": render_file,
            "report_file": report_file,
            "parts_count": len(entries),
            "total_pairs": interference.get("total_pairs", 0),
            "interfering_count": interference.get("interfering_count", 0),
            "exempted_count": interference.get("exempted_count", 0),
            "max_interference_volume_mm3": interference.get("max_interference_volume", 0.0),
            "pairs": (interference.get("pairs") or [])[:40],
            "excluded_superseded": excluded,
            "hard_collision_count": len(categories["hard_collision"]),
            "expected_fit_count": len(categories["expected_fit"]),
            "expected_mesh_count": len(categories["expected_mesh"]),
        }
        # active + 本轮标 superseded 的历史条目一起写回（不丢件、不覆盖标记）。
        manifest["parts"] = list(entries) + excluded_entries
        manifest["assembly"] = summary
        storage.write_manifest(self.project_id or "", manifest)
        if self.session is not None:
            self.session.set_assembly(summary)
        result.assembly = summary
        self.emit("artifact_ready", f"装配已导出: {out_step.name}", {
            "kind": "assembly", "step_file": out_step.name,
            "render_file": render_file, "report_file": report_file,
            "interfering_count": summary["interfering_count"],
            "exempted_count": summary["exempted_count"],
        })
        hard_brief = [
            f"{p['name_a']}×{p['name_b']} {round(p.get('volume_mm3') or 0, 1)}mm³"
            for p in categories["hard_collision"]][:10]
        return {
            "success": True,
            "step_file": out_step.name,
            "render_file": render_file,
            "report_file": report_file,
            "parts_count": len(entries),
            "total_pairs": summary["total_pairs"],
            "interfering_count": summary["interfering_count"],
            "exempted_count": summary["exempted_count"],
            "hard_collision_count": len(categories["hard_collision"]),
            "expected_fit_count": len(categories["expected_fit"]),
            "expected_mesh_count": len(categories["expected_mesh"]),
            "excluded_superseded": excluded,
            "interfering": hard_brief,
            "note": ("装配交付物已生成（STEP/干涉/预览/报告）。"
                     + (f"已排除 {len(excluded)} 个非 BOM/历史残留件。" if excluded else "")
                     + ("存在未豁免干涉对，请在总结中如实说明。" if hard_brief else "")),
        }

    def _handle_finish_part(self, tool_call: ToolCall, result: AgentLoopResult) -> dict[str, Any]:
        """合成工具 finish_part：当前零件导出归档 → 计划打勾 → 清空内核会话开下一件。"""
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        part = str(args.get("part") or "").strip()
        note = str(args.get("note") or "").strip()
        self.step_count += 1
        result.steps = self.step_count
        self.emit("agent_step", f"零件归档: {part or '（无名）'}", {
            "step": self.step_count, "op": "finish_part", "args_preview": _args_preview(args),
        })
        if not self._plan_approved:
            payload: dict[str, Any] = {"success": False, "error_kind": "PLAN_REQUIRED",
                                       "error": "finish_part 仅在计划获批准后使用。"}
        elif not part:
            payload = {"success": False, "error_kind": "INVALID_REQUEST",
                       "error": "缺少零件名 part。"}
        else:
            bom_names = self._bom_part_names()
            if bom_names and part not in bom_names:
                # v2.17 P1-7：发明新名字（箱体-v2）绕过重复检测会让旧版本以"active"
                # 混进装配。BOM 是零件名的事实来源：返工必须用原名同名归档（原子换版）。
                payload = {"success": False, "error_kind": "BOM_UNKNOWN_PART",
                           "error": f"零件「{part}」不在批准的 BOM 里（BOM: {sorted(bom_names)}）。"
                                    "如需返工已归档零件，请使用 BOM 中的原零件名——同名归档会"
                                    "原子替换为新版本；不要发明 -v2/副本 之类新名字。"
                                    "确属计划遗漏的新零件，先 update_plan/propose_plan 修订 BOM。"}
                return tool_result_message(tool_call, json.dumps(payload, ensure_ascii=False),
                                           protocol=self.protocol)
            if any(p.get("part") == part for p in result.parts):
                # v2.17 P1-7：同名返工从"拒绝"改为"允许并原子替换"——
                # run 记录原位替换（index 不变），零件库版本 +1，旧版标 superseded。
                self.logs.append(f"零件 {part} 返工重归档（替换 run 记录，库版本递增）")
            contract = args.get("feature_contract") if isinstance(args.get("feature_contract"), list) else None
            payload = self._finish_part_inner(part, note, result, contract)
        return tool_result_message(tool_call, json.dumps(payload, ensure_ascii=False, default=str),
                                   protocol=self.protocol)

    def _check_feature_contract(self, contract: list) -> list[str]:
        """特征契约校验。返回违规描述列表（空=通过）。

        两种契约项：
        - 旧形式 {radius_mm, count}：数圆柱面半径匹配（无法区分孔/凸台）；
        - v2.17 孔语义 {type, diameter_mm, count, positions?, tolerance_mm?}：
          经内核 query what=holes 实测——外凸台不算孔、贯通标志/深度/孔位都要吻合。
        """
        violations: list[str] = []
        legacy = [c for c in contract[:16]
                  if isinstance(c, dict) and not c.get("type") and c.get("radius_mm") is not None]
        typed = [c for c in contract[:16] if isinstance(c, dict) and c.get("type")]
        if legacy:
            try:
                sel = self.worker.execute("select", {"filter_type": "cylinder", "element_type": "face"})
            except Exception as exc:  # noqa: BLE001
                return [f"无法读取圆柱面（select 失败）: {type(exc).__name__}: {exc}"]
            if not sel.get("success"):
                return [f"无法读取圆柱面（select 未成功）: {sel.get('error')}"]
            faces = (sel.get("value") or {}).get("selected") or []
            for item in legacy:
                try:
                    radius = float(item.get("radius_mm"))
                    expect = int(item.get("count"))
                except (TypeError, ValueError):
                    violations.append(f"契约项非法（需 radius_mm/count）: {item}")
                    continue
                actual = sum(1 for f in faces
                             if f.get("radius_mm") is not None and abs(f["radius_mm"] - radius) < 0.05)
                if actual != expect:
                    violations.append(
                        f"半径 {radius}mm 圆柱面：断言 {expect} 个，实测 {actual} 个")
        if typed:
            try:
                holes_res = self.worker.execute("query", {"target": "_current_geometry", "what": "holes"})
            except Exception as exc:  # noqa: BLE001
                violations.append(f"孔语义分析不可用: {type(exc).__name__}: {exc}")
                return violations
            if not holes_res.get("success"):
                violations.append(f"孔语义分析失败: {holes_res.get('error')}")
                return violations
            holes = (holes_res.get("value") or {}).get("holes") or []
            for item in typed:
                kind = str(item.get("type"))
                try:
                    diameter = float(item.get("diameter_mm"))
                    expect = int(item.get("count"))
                except (TypeError, ValueError):
                    violations.append(f"孔契约项非法（需 type/diameter_mm/count）: {item}")
                    continue
                tol = float(item.get("tolerance_mm") or 0.05)
                matched = [h for h in holes
                           if h.get("kind") == kind
                           and abs(float(h.get("diameter_mm", 0)) - diameter) <= max(tol, 0.05)]
                positions = item.get("positions")
                if isinstance(positions, list) and positions:
                    pos_matched = []
                    for want in positions:
                        if not isinstance(want, (list, tuple)):
                            continue
                        hit = None
                        for h in matched:
                            center = h.get("center") or []
                            axis = h.get("axis") or [0, 0, 1]
                            if len(want) == 2:
                                # 2D 孔位：比较垂直于孔轴的两个分量（按主轴选平面）
                                dom = max(range(3), key=lambda i: abs(axis[i])) if len(axis) == 3 else 2
                                plane = [i for i in range(3) if i != dom]
                                if len(center) < 3:
                                    dist = 9e9
                                else:
                                    dist = max(abs(float(want[0]) - center[plane[0]]),
                                               abs(float(want[1]) - center[plane[1]]))
                            else:
                                dist = max((abs(float(a) - float(b)) for a, b in zip(want, center)),
                                           default=9e9)
                            if dist <= max(float(item.get("tolerance_mm") or 0.5), 0.5) and (hit is None or dist < hit[0]):
                                hit = (dist, id(h))
                        if hit is None:
                            violations.append(
                                f"孔契约 {kind} Ø{diameter}：断言孔位 {list(want)} 未实测到")
                        else:
                            pos_matched.append(hit[1])
                    if len(set(pos_matched)) != len(matched):
                        extra = len(matched) - len(set(pos_matched))
                        if extra > 0:
                            violations.append(
                                f"孔契约 {kind} Ø{diameter}：实测多出 {extra} 个未断言的同规格孔")
                if len(matched) != expect:
                    violations.append(
                        f"孔契约 {kind} Ø{diameter}：断言 {expect} 个，实测 {len(matched)} 个"
                        f"（外凸台不计为孔；贯通/盲以拓扑分类为准）")
        return violations

    def _finish_part_inner(self, part: str, note: str, result: AgentLoopResult,
                           contract: list | None = None) -> dict[str, Any]:
        # 1) 当前会话必须有几何
        try:
            probe = self.worker.execute("query", {"target": "_current_geometry", "what": "volume"})
        except Exception as exc:  # noqa: BLE001
            probe = {"success": False, "error": f"{type(exc).__name__}: {exc}"}
        volume: float | None = None
        if probe.get("success"):
            try:
                volume = float(probe.get("value"))
            except (TypeError, ValueError):
                volume = None
        if not volume:
            return {"success": False, "error_kind": "INVALID_REQUEST",
                    "error": "当前内核会话没有零件几何（上一次 finish_part 已清空会话）。"
                             "请先把该零件建模完成再调用 finish_part。"}
        # 1b) v0.13 设计复检门（DSH L1 review 的 CAD 化）：单实体契约。
        # "齿悬浮/特征未连接"这类设计性缺陷 validate_geometry 查不出，这里机器拦截。
        try:
            solid_probe = self.worker.execute("query", {"target": "_current_geometry", "what": "solid_count"})
            solids = solid_probe.get("value") if solid_probe.get("success") else None
        except Exception:  # noqa: BLE001 —— 老内核无 solid_count 查询时跳过复检
            solids = None
        if isinstance(solids, int) and solids != 1:
            return {"success": False, "error_kind": "GEOMETRY_FAILURE",
                    "error": f"设计复检未通过：当前零件有 {solids} 个独立实体（期望 1 个），"
                             "疑似特征未连接/存在悬浮体。请修复几何（fuse 或移除多余实体）"
                             "后再调用 finish_part。"}
        # 1c) v0.13 特征契约校验：断言的关键孔/圆柱面数量必须实测吻合，
        # 否则拒绝归档——防 undo 回滚掉特征后仍谎报完成。
        if contract:
            violations = self._check_feature_contract(contract)
            if violations:
                return {"success": False, "error_kind": "FEATURE_CONTRACT_MISMATCH",
                        "error": "特征契约校验未通过：" + "；".join(violations) +
                                 "。请补齐缺失特征（重新 hole/圆柱）或删除多余特征后重试 finish_part。",
                        "violations": violations}
        # 1d) v2.16（P0-1）：strict 几何验证门——体积/包围盒/拓扑有效性全过才准归档，
        # 结果记入零件档案（validation_passed），最终 _finalize 门控据此判任务成败。
        try:
            vres = self.worker.execute("validate_geometry", {"level": "strict"})
            validation_info = vres.get("geometry_validation") or {}
        except Exception as exc:  # noqa: BLE001 —— 验证通道异常按未通过处理（保守拒绝）
            validation_info = {"valid": False, "status": "unknown",
                               "reason_codes": [f"validator_error:{type(exc).__name__}"]}
        validation_passed = (validation_info.get("valid") is True
                             and validation_info.get("status") == "valid")
        if not validation_passed:
            return {"success": False, "error_kind": "GEOMETRY_INVALID",
                    "error": f"strict 几何验证未通过，拒绝归档: {validation_info}。"
                             "请修复几何（检查自交/空体积/无效拓扑）后重试 finish_part。",
                    "geometry_validation": validation_info}
        # 2) 导出归档（零件级 STEP + STL）
        # v2.17 P1-7：同名返工原位替换 run 记录（index 稳定），否则追加
        existing_idx = next((p.get("index") for p in result.parts if p.get("part") == part), None)
        idx = int(existing_idx) if existing_idx else len(result.parts) + 1
        slug = _part_slug(part)
        step_path = self.run_dir / f"part_{idx:02d}_{slug}.step"
        stl_path = self.run_dir / f"part_{idx:02d}_{slug}.stl"
        try:
            self.worker.export_step(str(step_path))
            mesh = self.worker.export_mesh(str(stl_path))
        except Exception as exc:  # noqa: BLE001 —— 导出失败不清会话，可修复重试
            self.logs.append(f"零件 {part} 导出失败: {type(exc).__name__}: {exc}")
            return {"success": False, "error_kind": "WORKER_ERROR",
                    "error": f"零件导出失败: {type(exc).__name__}: {exc}。当前几何仍在，可修正后重试。"}
        # 3) 清空内核会话（worker reset）
        try:
            self.worker.reset()
        except Exception as exc:  # noqa: BLE001 —— reset 失败必须叫停，防止零件互相融合
            self.logs.append(f"worker reset 失败: {type(exc).__name__}: {exc}")
            return {"success": False, "error_kind": "WORKER_ERROR",
                    "error": f"零件 {part} 已导出但会话清空失败: {exc}。"
                             "请勿继续建模（会把下一件融合进当前零件），直接向用户报告此问题。"}
        # 4) 记账 + 事件 + 计划打勾
        # v0.14 F2a：同步写项目零件库（版本递增 + manifest + session 镜像）
        library = self._archive_to_parts_library(part, step_path, stl_path, volume) or {}
        part_rec = {
            "part": part, "index": idx, "volume_mm3": round(volume, 2),
            "step": str(step_path), "stl": str(stl_path),
            "step_file": step_path.name, "stl_file": stl_path.name,
            "stl_size": mesh.get("size"), "note": note,
            "built_via": self._part_built_via,
            "validation_passed": True,
            "library_step_file": library.get("step_file"),
            "library_stl_file": library.get("stl_file"),
            "library_version": library.get("version"),
            "pose": library.get("pose"),
        }
        self._part_built_via = "ops"  # 下一件默认原子 op
        prior = next((i for i, p in enumerate(result.parts) if p.get("part") == part), None)
        if prior is None:
            result.parts.append(part_rec)
        else:
            result.parts[prior] = part_rec
        result.artifacts[f"part_{idx:02d}_step"] = str(step_path)
        result.artifacts[f"part_{idx:02d}_stl"] = str(stl_path)
        result.volume = None
        self._last_volume = None
        self._last_geometry_fingerprint = None
        self.emit("artifact_ready", f"零件已归档: {part}", {
            "kind": "part", "part": part, "index": idx,
            "step": str(step_path), "stl": str(stl_path),
            "step_file": step_path.name, "stl_file": stl_path.name,
            "size": mesh.get("size"),
        })
        completed = self._mark_part_steps_completed(part)
        return {
            "success": True, "archived": part_rec, "completed_steps": completed,
            "note": f"零件「{part}」已归档（STEP/STL），内核会话已清空。"
                    "请开始下一个零件（全新基体，无需 confirm_replace）；"
                    "如已是最后一个零件，直接写最终总结。",
        }

    # ---------------------------------------------------------------- events
    def _emit_step(self, step_no: int, op: str, data: dict[str, Any] | None, *, autofix: bool, message: str = "", args: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {
            "step": step_no,
            "op": op,
            "autofix": autofix,
        }
        if args is not None:
            payload["args_preview"] = _args_preview(args)
        if data is not None:
            payload["success"] = bool(data.get("success"))
            payload["error_kind"] = data.get("error_kind")
            payload["summary"] = data.get("narrative") or data.get("error") or ""
            payload["geometry_summary"] = data.get("geometry_summary")
            payload["feature_id"] = data.get("feature_id")
        if message:
            payload["message"] = message
        label = f"{op}{'(修复重试)' if autofix else ''}"
        self.emit("agent_step", label, payload)


def run_agent_loop(
    *,
    worker,
    chat_with_tools: ToolFn,
    protocol: str,
    emit: EmitFn,
    run_dir: Path,
    system_prompt: str,
    task_description: str = "",
    language: str = "zh",
    max_steps: int = 30,
    stop_event: threading.Event | None = None,
    capabilities: dict[str, Any] | None = None,
    approvals: ApprovalBroker | None = None,
    session: AgentSession | None = None,
    initial_user_message: dict[str, Any] | None = None,
    mode: str = "auto",
    project_id: str | None = None,
) -> AgentLoopResult:
    """函数式入口（main.py 用）；类入口便于测试注入。"""
    loop = AgentLoop(
        worker=worker,
        chat_with_tools=chat_with_tools,
        protocol=protocol,
        emit=emit,
        run_dir=run_dir,
        capabilities=capabilities,
        system_prompt=system_prompt,
        task_description=task_description,
        language=language,
        max_steps=max_steps,
        stop_event=stop_event,
        approvals=approvals,
        session=session,
        initial_user_message=initial_user_message,
        mode=mode,
        project_id=project_id,
    )
    return loop.run()
