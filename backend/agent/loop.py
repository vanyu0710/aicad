"""MechKernel agent loop（P1 垂直切片）。

多轮原生 function-calling 循环：LLM 直接以 kernel op 名作为工具名调用，
loop 调 worker RPC 执行，把精简后的 StepResult 作为工具结果回喂，直到模型
停止调用工具（给出最终文字总结）或到达 max_steps。

设计边界（对齐 docs/mechkernel-harness-roadmap.md）：
- 执行层直接是 MechKernel op（D1），本模块不理解 FeaturePlanV3。
- 人在回路确认点（P2）与改动清单（P3）尚未实现，只发 ``agent_step`` 事件。
- 自修复：RECOVERABLE + suggestion.fix 时按 schema 过滤后自动重试一次。

本模块不 import CAD 库；几何只经 worker RPC 触达（D2）。
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.agent.approvals import ApprovalBroker
from backend.agent.tools import build_llm_tools, filter_args_to_schema
from backend.mechcad_ai.client import ApiCallError, ToolCall, ToolCallRound, tool_result_message

ToolFn = Callable[[list[dict[str, Any]], list[dict[str, Any]]], ToolCallRound]
EmitFn = Callable[[str, str, dict[str, Any]], None]

_VALUE_CLIP = 800
_NARRATIVE_CLIP = 40


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
    """合成工具：agent 在关键信息不明确时向用户提问。"""
    return {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "当关键尺寸/特征需要用户确认且无法从描述合理推断时，先调用它向用户提问。问题要具体、给出建议值，供用户选择或修正。",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "清楚的具体问题，包括上下文"},
                    "options": {"type": "array", "items": {"type": "string"}, "description": "建议的选项（如尺寸候选），可为空"},
                    "context": {"type": "string", "description": "为什么需要这一信息（如涉及主基体/孔位影响）"},
                },
                "required": ["question"],
            },
        },
    }


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


def _opening_context(task_description: str, worker, capabilities: dict[str, Any]) -> str:
    """首条用户消息：任务 + 当前特征图 + 可用公开展台 op。把"暂停→接管→交还"的连续性交给模型。"""
    parts: list[str] = [f"任务：{task_description}"]
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
    return "\n\n".join(parts)


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
        task_description: str,
        language: str = "zh",
        max_steps: int = 30,
        stop_event: threading.Event | None = None,
        approvals: ApprovalBroker | None = None,
    ) -> None:
        self.worker = worker
        self.chat_with_tools = chat_with_tools
        self.protocol = protocol
        self.emit = emit
        self.run_dir = Path(run_dir)
        self.capabilities = capabilities or worker.capabilities()
        self.tools = [*build_llm_tools(self.capabilities), _ask_user_tool()]
        self.approvals = approvals
        self.max_steps = max_steps
        self.stop_event = stop_event
        self.language = language
        self._context_descriptors: list[str] = []
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _opening_context(task_description, worker, self.capabilities)},
        ]
        self.logs: list[str] = []
        self.step_count = 0
        self._last_volume: float | None = None

    # ------------------------------------------------------------------ main
    def run(self) -> AgentLoopResult:
        result = AgentLoopResult(logs=self.logs)
        try:
            self._run_inner(result)
        except ApiCallError as exc:
            result.error = f"LLM 调用失败: {exc}"
            self.logs.append(result.error)
        except Exception as exc:  # noqa: BLE001 —— 汇总为失败结果，交由端点上报
            result.error = f"{type(exc).__name__}: {exc}"
            self.logs.append(result.error)
        try:
            self._finalize(result)
        except Exception as exc:  # noqa: BLE001
            self.logs.append(f"收尾导出失败: {type(exc).__name__}: {exc}")
        if result.error and not result.stopped:
            result.ok = False
        return result

    def _run_inner(self, result: AgentLoopResult) -> None:
        while self.step_count < self.max_steps:
            if self.stop_event is not None and self.stop_event.is_set():
                result.stopped = True
                self.logs.append("收到停止请求，agent 退出。")
                return
            round = self.chat_with_tools(self.messages, self.tools)
            if round.text:
                self.logs.append(f"模型输出: {round.text[:500]}")
            if not round.tool_calls:
                result.final_text = round.text
                result.ok = True
                return
            self.messages.append(round.raw_message)
            for tool_call in round.tool_calls:
                if self.step_count >= self.max_steps:
                    break
                if self.stop_event is not None and self.stop_event.is_set():
                    break
                if tool_call.name == "ask_user":
                    self.messages.append(self._handle_ask_user(tool_call))
                else:
                    self.messages.append(self._execute_tool_call(tool_call, result))
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
        self._emit_step(step_no, op, data, autofix=False)
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
                            "error": "用户拒绝了破坏性替换", "suggestion": decision.get("message", "")}
                    self._emit_step(self.step_count, op, data, autofix=True, message="破坏性修复被用户拒绝")
                elif decision["action"] == "timeout":
                    data = {"success": False, "error_kind": "SKIPPED",
                            "error": decision.get("message", "用户未响应，已跳过破坏性修复")}
                    self._emit_step(self.step_count, op, data, autofix=True, message="破坏性修复超时，已跳过")
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
        # 破坏性操作在执行前征求用户（P2 确认点）。skip_approval：该步已在审批流程内确认过。
        if self.approvals is not None and not skip_approval and _is_destructive(op, args):
            decision = self._request_approval("destructive_op", op, args, _destructive_message(op, args))
            if decision["action"] == "reject":
                return {"success": False, "error_kind": "REJECTED",
                        "error": "用户拒绝了该步骤", "suggestion": decision.get("message", "")}
            if decision["action"] == "timeout":
                return {"success": False, "error_kind": "SKIPPED",
                        "error": decision.get("message", "用户未响应，已跳过")}
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
            "ok": bool(result.ok and not result.error) and bool(result.volume),
            "engine": "mechkernel",
            "worker": "mechkernel-agent",
            "agent_stopped": result.stopped,
            "steps": result.steps,
            "final_text": result.final_text,
            "volume": result.volume,
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
        """请求用户审批并阻塞等待；把审批请求以 WS 事件广播。"""
        if self.approvals is None:
            return {"action": "approve", "args": dict(args)}
        request_info: dict[str, Any] = {"kind": kind, "op": op, "args": dict(args), "message": message, "options": options or {}}
        self.emit("approval_required", message, request_info)
        self.logs.append(f"审批等待: {message}")
        return self.approvals.request(kind=kind, op=op, args=args, message=message, options=options)

    def _handle_ask_user(self, tool_call: ToolCall) -> dict[str, Any]:
        """合成工具 ask_user：向用户提问，等答复，以工具结果回喂模型。"""
        self.step_count += 1
        self.emit("agent_step", "ask_user", {"step": self.step_count, "op": "ask_user"})
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        question = str(args.get("question") or args.get("context") or "（无问题文本）")
        decision = self._request_approval(
            "ask_user", "ask_user", args, question, options={"question": question, "context": args.get("context", "")},
        )
        if decision["action"] == "timeout":
            answer = decision.get("message", "用户未响应")
        elif decision["action"] == "reject":
            answer = decision.get("message", "用户拒绝回答")
        else:
            answer = str(decision.get("args", {}).get("answer") or "（用户未给出答案）")
        result = {"success": True, "user_answer": answer, "question": question}
        return tool_result_message(tool_call, json.dumps(result, ensure_ascii=False), protocol=self.protocol)

    # ---------------------------------------------------------------- events
    def _emit_step(self, step_no: int, op: str, data: dict[str, Any] | None, *, autofix: bool, message: str = "") -> None:
        payload: dict[str, Any] = {
            "step": step_no,
            "op": op,
            "autofix": autofix,
        }
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
    task_description: str,
    language: str = "zh",
    max_steps: int = 30,
    stop_event: threading.Event | None = None,
    capabilities: dict[str, Any] | None = None,
    approvals: ApprovalBroker | None = None,
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
    )
    return loop.run()
