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
import re
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

_VALUE_CLIP = 800
_NARRATIVE_CLIP = 40
_ARGS_PREVIEW_CLIP = 240


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
    # v0.12 多零件逐件交付：finish_part 归档清单 + design_calculate 调研转录
    parts: list[dict[str, Any]] = field(default_factory=list)
    design_calculations: list[dict[str, Any]] = field(default_factory=list)


def _compact_step_result(data: dict[str, Any]) -> dict[str, Any]:
    """给 LLM 的工具结果：保留决策所需字段，丢弃渲染/长叙事以控制上下文。"""
    compact = {
        "success": data.get("success"),
        "feature_id": data.get("feature_id"),
        "error_kind": data.get("error_kind"),
        "error": data.get("error"),
        "suggestion": data.get("suggestion"),
        "warning": data.get("warning"),
        "hint": data.get("hint"),
        "narrative": data.get("narrative"),
        "geometry_summary": data.get("geometry_summary"),
        "hints": data.get("hints") or [],
    }
    value = data.get("value")
    if value is not None:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
        if len(rendered) > _VALUE_CLIP:
            rendered = rendered[:_VALUE_CLIP] + "…(截断)"
        compact["value"] = rendered
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


def _normalize_bom(raw: Any) -> list[dict[str, Any]]:
    """规范化零件清单（BOM）：每项必须有 part 名；数量默认 1；参数/依赖容错。"""
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
        })
    return bom


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
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string",
                             "description": "Python 脚本（只用 k 与 math；必须产生几何变化）"},
                    "reason": {"type": "string",
                               "description": "本脚本建哪个零件/达成什么目标（过程记录，可选）"},
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
                            "特征契约（强烈建议提供）：断言零件应有 count 个指定半径的圆柱面"
                            "（如 Ø9 螺栓孔×4 → {radius_mm: 4.5, count: 4}；Ø25 轴承孔×2 → {radius_mm: 12.5, count: 2}）。"
                            "归档前系统用 select 实测圆柱面计数，与断言不符即拒绝——防止 undo 回滚掉特征后谎报完成。"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "radius_mm": {"type": "number"},
                                "count": {"type": "integer"},
                            },
                            "required": ["radius_mm", "count"],
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
        # 批准后计划的本地副本（无 session 时 finish_part 打勾/广播用）
        self._approved_plan: dict[str, Any] | None = None
        # v0.13 当前零件的来源标记：ops | script（finish_part 记账后复位 ops）
        self._part_built_via = "ops"
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
            if round.text:
                self.logs.append(f"模型输出: {round.text[:500]}")
                if not streamed["active"]:
                    # 非流式：整轮文字一次性播出（流式时已逐段回调，不重复）
                    self.emit("agent_text_delta", round.text, {"round": self._round_no, "done": not round.tool_calls})
            if not round.tool_calls:
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
                else:
                    self._remember(self._execute_tool_call(tool_call, result))
        if self.step_count >= self.max_steps:
            result.stopped = True
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
        if self._last_volume is not None and abs(float(volume) - self._last_volume) <= 1e-3:
            return
        self._last_volume = float(volume)
        stl_path = self.run_dir / "model.stl"
        try:
            mesh = self.worker.export_mesh(str(stl_path))
            if mesh.get("size"):
                result.artifacts["stl"] = str(stl_path)
                self.emit("artifact_ready", "几何已更新，3D 网格已导出", {
                    "kind": "stl",
                    "path": str(stl_path),
                    "size": mesh.get("size"),
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
                validation = self.worker.execute("validate_geometry", {"level": "standard"})
            except Exception as exc:  # noqa: BLE001
                self.logs.append(f"validate_geometry 失败: {type(exc).__name__}: {exc}")

        step_path = self.run_dir / "model.step"
        if result.volume:
            try:
                self.worker.export_step(str(step_path))
                result.artifacts["step"] = str(step_path)
            except Exception as exc:  # noqa: BLE001
                self.logs.append(f"STEP 导出失败: {type(exc).__name__}: {exc}")

        report = {
            # 多零件任务以归档件数计完成度（末件 finish_part 后会话已清空）
            "ok": bool(result.ok and not result.error) and bool(result.volume or result.parts),
            "engine": "mechkernel",
            "worker": "mechkernel-agent",
            "agent_stopped": result.stopped,
            "steps": result.steps,
            "final_text": result.final_text,
            "volume": result.volume,
            "parts": result.parts,
            "design_calculations": result.design_calculations,
            "feature_graph": result.feature_graph,
            "op_history": tree.get("op_history") or [],
            "narrative": (tree.get("narrative") or [])[-_NARRATIVE_CLIP:],
            "geometry_validation": validation.get("geometry_validation"),
            "logs": self.logs,
        }
        if result.error:
            report["error"] = result.error
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
            try:
                data = self.worker.run_script(code, name=reason[:40] or "script")
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
                payload["note"] = ("脚本执行失败，状态已回滚到执行前（无需清理）；"
                                   "按 traceback 修正后重跑 run_build_script。")
            result.logs.append(
                f"run_build_script: success={payload['success']} ops={value.get('ops_executed') if data.get('success') else '-'}")
        return tool_result_message(tool_call, json.dumps(payload, ensure_ascii=False, default=str),
                                   protocol=self.protocol)

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
            contract = args.get("feature_contract") if isinstance(args.get("feature_contract"), list) else None
            payload = self._finish_part_inner(part, note, result, contract)
        return tool_result_message(tool_call, json.dumps(payload, ensure_ascii=False, default=str),
                                   protocol=self.protocol)

    def _check_feature_contract(self, contract: list) -> list[str]:
        """特征契约校验：数圆柱面半径匹配 count。返回违规描述列表（空=通过）。"""
        try:
            sel = self.worker.execute("select", {"filter_type": "cylinder", "element_type": "face"})
        except Exception as exc:  # noqa: BLE001
            return [f"无法读取圆柱面（select 失败）: {type(exc).__name__}: {exc}"]
        if not sel.get("success"):
            return [f"无法读取圆柱面（select 未成功）: {sel.get('error')}"]
        faces = (sel.get("value") or {}).get("selected") or []
        violations: list[str] = []
        for item in contract[:16]:
            if not isinstance(item, dict):
                continue
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
        # 2) 导出归档（零件级 STEP + STL）
        idx = len(result.parts) + 1
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
        part_rec = {
            "part": part, "index": idx, "volume_mm3": round(volume, 2),
            "step": str(step_path), "stl": str(stl_path),
            "step_file": step_path.name, "stl_file": stl_path.name,
            "stl_size": mesh.get("size"), "note": note,
            "built_via": self._part_built_via,
        }
        self._part_built_via = "ops"  # 下一件默认原子 op
        result.parts.append(part_rec)
        result.artifacts[f"part_{idx:02d}_step"] = str(step_path)
        result.artifacts[f"part_{idx:02d}_stl"] = str(stl_path)
        result.volume = None
        self._last_volume = None
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
    )
    return loop.run()
