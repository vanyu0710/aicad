"""backend/agent/loop.py 的 agent loop 测试（fake worker + fake LLM，无网络无 kernel）。"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from backend.agent import run_agent_loop
from backend.mechcad_ai.client import ApiCallError, ToolCall, ToolCallRound


CAPABILITIES = {
    "public": [
        {"name": "create_workplane", "category": "sketch", "description": "创建工作平面",
         "inputs": {"name": {"type": "string", "required": True},
                    "type": {"type": "enum", "required": False, "default": "XY",
                             "enum": ["XY", "YZ", "XZ"]}},
         "examples": []},
        {"name": "new_sketch", "category": "sketch", "description": "创建草图",
         "inputs": {"workplane_name": {"type": "string", "required": True},
                    "sketch_name": {"type": "string", "required": True}},
         "examples": []},
        {"name": "extrude", "category": "body", "description": "拉伸",
         "inputs": {"sketch_name": {"type": "string", "required": True},
                    "depth": {"type": "number", "required": True, "min": 0.001},
                    "mode": {"type": "enum", "required": False,
                             "enum": ["new_body", "add", "cut"]},
                    "confirm_replace": {"type": "boolean", "required": False}},
         "examples": []},
        {"name": "delete_feature", "category": "edit", "description": "删除特征",
         "inputs": {"feature_id": {"type": "string", "required": True}}, "examples": []},
    ],
    "experimental": [],
}


class FakeWorker:
    """脚本化 worker：execute 序列按序弹出，记录每次调用。"""

    def __init__(self, execute_results=None) -> None:
        self.capabilities_result = CAPABILITIES
        self.execute_results = list(execute_results or [])
        self.executed: list[tuple[str, dict]] = []
        self.exported_mesh: list[str] = []
        self.exported_step: list[str] = []
        self.render_calls: list[dict] = []
        self.render_png = "iVBORw0KGgo="  # 最小假 base64 PNG
        self.reset_calls = 0
        self.reset_error: Exception | None = None
        # v0.13 run_script 桩：按序弹出，缺省返回成功
        self.run_script_calls: list[tuple[str, str]] = []
        self.run_script_results: list = []

    def capabilities(self) -> dict:
        return self.capabilities_result

    def run_script(self, code: str, *, name: str = "") -> dict:
        self.run_script_calls.append((code, name))
        if self.run_script_results:
            result = self.run_script_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return {
            "success": True,
            "value": {"ops_executed": 3, "solids": 1, "volume": 5000.0,
                      "bounding_box": [0, 0, 0, 100, 50, 10], "stdout": "ok\n"},
            "geometry_summary": {"volume": 5000.0, "bounding_box": [0, 0, 0, 100, 50, 10]},
        }

    def execute(self, op: str, args: dict | None = None) -> dict:
        self.executed.append((op, dict(args or {})))
        if self.execute_results:
            result = self.execute_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return {"success": True, "geometry_summary": {"volume": 1000.0}}

    def feature_tree(self) -> dict:
        return {"graph": {"nodes": {"F_0001": {"id": "F_0001"}}, "edges": {}},
                "op_history": [{"op": op} for op, _ in self.executed],
                "narrative": ["n1", "n2"]}

    def export_mesh(self, path: str) -> dict:
        self.exported_mesh.append(path)
        Path(path).write_bytes(b"binary-stl")
        return {"path": path, "format": "stl", "size": 10}

    def export_step(self, path: str) -> dict:
        self.exported_step.append(path)
        Path(path).write_text("ISO-10303-21;")
        return {"success": True}

    def render_snapshot(self, *, views=None, size=480) -> dict:
        self.render_calls.append({"views": views, "size": size})
        return {"success": True, "render_base64": self.render_png}

    def reset(self) -> dict:
        # v0.12 逐件建模：finish_part 归档后清空内核会话
        self.reset_calls += 1
        if self.reset_error is not None:
            raise self.reset_error
        return {"reset": True}


def _success(volume=1000.0) -> dict:
    return {"success": True, "geometry_summary": {"volume": volume}}


class FakeChat:
    """脚本化 LLM 轮次。"""

    def __init__(self, rounds) -> None:
        self.rounds = list(rounds)
        self.calls: list[tuple[list, list]] = []

    def __call__(self, messages, tools) -> ToolCallRound:
        self.calls.append((list(messages), list(tools)))
        if not self.rounds:
            return ToolCallRound(text="done")
        return self.rounds.pop(0)


def _round_with_call(name: str, arguments: dict) -> ToolCallRound:
    return ToolCallRound(
        text="",
        tool_calls=[ToolCall(id="call-1", name=name, arguments=arguments)],
        raw_message={"role": "assistant", "content": None,
                     "tool_calls": [{"id": "call-1", "type": "function",
                                     "function": {"name": name, "arguments": json.dumps(arguments)}}]},
    )


def _run(worker, chat, *, max_steps=10, stop_event=None, emit=None, run_dir=None, approvals=None, session=None, mode="auto"):
    if run_dir is None:
        with tempfile.TemporaryDirectory() as td:
            return _run_in_dir(worker, chat, Path(td), max_steps=max_steps, stop_event=stop_event, emit=emit, approvals=approvals, session=session, mode=mode)
    return _run_in_dir(worker, chat, Path(run_dir), max_steps=max_steps, stop_event=stop_event, emit=emit, approvals=approvals, session=session, mode=mode)


def _run_in_dir(worker, chat, run_dir: Path, *, max_steps, stop_event, emit, approvals=None, session=None, mode="auto"):
    return run_agent_loop(
        worker=worker,
        chat_with_tools=chat,
        protocol="openai",
        emit=emit or (lambda *_args, **_kw: None),
        run_dir=run_dir,
        system_prompt="SYS",
        task_description="做一个方块",
        max_steps=max_steps,
        stop_event=stop_event,
        approvals=approvals,
        session=session,
        mode=mode,
    )


class AgentLoopHappyPathTests(unittest.TestCase):
    def test_text_to_3d_sequence(self) -> None:
        # 前两步无几何（volume=0），extrude 后体积变化 → 只导出一次 STL
        worker = FakeWorker([_success(0.0), _success(0.0), _success(120000.0)])
        chat = FakeChat([
            _round_with_call("create_workplane", {"name": "base"}),
            _round_with_call("new_sketch", {"workplane_name": "base", "sketch_name": "sk"}),
            _round_with_call("extrude", {"sketch_name": "sk", "depth": 10}),
            ToolCallRound(text="完成", tool_calls=[]),
        ])
        result = _run(worker, chat)
        self.assertTrue(result.ok)
        self.assertFalse(result.stopped)
        self.assertEqual(result.steps, 3)
        self.assertEqual(result.final_text, "完成")
        self.assertEqual(worker.executed[0], ("create_workplane", {"name": "base"}))
        self.assertEqual(worker.executed[2], ("extrude", {"sketch_name": "sk", "depth": 10}))
        self.assertEqual(result.volume, 120000.0)
        self.assertTrue(result.feature_graph.get("nodes"))
        # 体积变化触发 STL 导出 + 收尾导出 STEP
        self.assertEqual(len(worker.exported_mesh), 1)
        self.assertEqual(len(worker.exported_step), 1)

    def test_tool_results_appended_to_messages(self) -> None:
        worker = FakeWorker([_success()])
        chat = FakeChat([
            _round_with_call("create_workplane", {"name": "base"}),
            ToolCallRound(text="ok", tool_calls=[]),
        ])
        _run(worker, chat)
        final_messages = chat.calls[-1][0]
        roles = [m["role"] for m in final_messages]
        self.assertIn("assistant", roles)  # raw assistant message
        self.assertIn("tool", roles)      # openai tool result
        tool_message = next(m for m in final_messages if m["role"] == "tool")
        payload = json.loads(tool_message["content"])
        self.assertTrue(payload["success"])
        self.assertIn("geometry_summary", payload)

    def test_finish_writes_execution_report(self) -> None:
        worker = FakeWorker([_success(5000.0)])
        chat = FakeChat([
            _round_with_call("create_workplane", {"name": "base"}),
            ToolCallRound(text="好了", tool_calls=[]),
        ])
        with tempfile.TemporaryDirectory() as td:  # run_dir 由外部持有，断言时文件仍存在
            result = _run(worker, chat, run_dir=Path(td))
            report_path = result.artifacts.get("execution_report")
            self.assertTrue(report_path and Path(report_path).exists())
            report = json.loads(Path(report_path).read_text(encoding="utf-8"))
            self.assertEqual(report["engine"], "mechkernel")
            self.assertTrue(report["ok"])
            self.assertEqual(report["volume"], 5000.0)

    def test_emit_publishes_agent_step_events(self) -> None:
        worker = FakeWorker([_success()])
        chat = FakeChat([
            _round_with_call("create_workplane", {"name": "base"}),
            ToolCallRound(text="ok", tool_calls=[]),
        ])
        events: list[tuple] = []
        _run(worker, chat, emit=lambda event_type, message, payload: events.append((event_type, message, payload)))
        step_events = [e for e in events if e[0] == "agent_step"]
        self.assertEqual(len(step_events), 1)
        self.assertEqual(step_events[0][2]["op"], "create_workplane")
        self.assertTrue(step_events[0][2]["success"])

    def test_volume_change_renders_snapshot_and_emits_url(self) -> None:
        # 体积变化触发 STL 导出的同时渲染可视化快照
        worker = FakeWorker([_success(5000.0)])
        chat = FakeChat([
            _round_with_call("create_workplane", {"name": "base"}),
            ToolCallRound(text="好了", tool_calls=[]),
        ])
        events: list[tuple] = []
        with tempfile.TemporaryDirectory() as td:
            result = _run(worker, chat, run_dir=Path(td),
                          emit=lambda t, m, p: events.append((t, m, p)))
            self.assertEqual(len(worker.render_calls), 1)
            self.assertEqual(worker.render_calls[0]["size"], 480)
            snap_events = [e for e in events if e[0] == "agent_snapshot"]
            self.assertEqual(len(snap_events), 1)
            payload = snap_events[0][2]
            self.assertTrue(payload["url"].startswith("/api/artifacts/"))
            self.assertIn("snapshot_s", payload["url"])
            # PNG 已落盘且记录在 artifacts
            self.assertTrue(Path(payload["path"]).exists())
            self.assertTrue(any("snapshot_s" in key for key in result.artifacts))


class AgentLoopErrorPathTests(unittest.TestCase):
    def test_unknown_op_reports_failure_and_continues(self) -> None:
        worker = FakeWorker()
        chat = FakeChat([
            _round_with_call("not_an_op", {}),
            ToolCallRound(text="好的", tool_calls=[]),
        ])
        result = _run(worker, chat)
        self.assertTrue(result.ok)  # LLM 收到失败结果后正常收尾
        self.assertEqual(result.steps, 1)
        final_messages = chat.calls[-1][0]
        tool_message = next(m for m in final_messages if m["role"] == "tool")
        payload = json.loads(tool_message["content"])
        self.assertFalse(payload["success"])
        self.assertIn("未知 op", payload["error"])

    def test_recoverable_suggestion_triggers_autofix(self) -> None:
        worker = FakeWorker([
            {"success": False, "error_kind": "RECOVERABLE",
             "error": "new_body 会清空零件",
             "suggestion": {"action": "改 add", "fix": {"mode": "add"}, "reason_code": "new_body_conflict"},
             "geometry_summary": None},
            _success(8000.0),
        ])
        chat = FakeChat([
            _round_with_call("extrude", {"sketch_name": "sk", "depth": 10, "mode": "new_body"}),
            ToolCallRound(text="修复完成", tool_calls=[]),
        ])
        events: list[tuple] = []
        result = _run(worker, chat, emit=lambda t, m, p: events.append((t, m, p)))
        self.assertTrue(result.ok)
        # 第二次 execute 应合并 fix 并过滤未知字段
        second_op, second_args = worker.executed[1]
        self.assertEqual(second_op, "extrude")
        self.assertEqual(second_args.get("mode"), "add")
        autofix_events = [e for e in events if e[0] == "agent_step" and e[2].get("autofix")]
        self.assertTrue(autofix_events)

    def test_worker_exception_becomes_tool_failure(self) -> None:
        worker = FakeWorker([KernelDown("boom"), _success()])
        chat = FakeChat([
            _round_with_call("create_workplane", {"name": "base"}),
            ToolCallRound(text="ok", tool_calls=[]),
        ])
        result = _run(worker, chat)
        self.assertTrue(result.ok)
        final_messages = chat.calls[-1][0]
        tool_message = next(m for m in final_messages if m["role"] == "tool")
        payload = json.loads(tool_message["content"])
        self.assertEqual(payload["error_kind"], "WORKER_DEAD")

    def test_llm_api_error_stops_with_failure(self) -> None:
        worker = FakeWorker()
        chat = FakeChat([])

        def fail(messages, tools):
            raise ApiCallError("HTTP 500", retryable=True)

        result = _run(worker, fail)
        self.assertFalse(result.ok)
        self.assertIn("LLM 调用失败", result.error or "")

    def test_max_steps_reached(self) -> None:
        worker = FakeWorker()
        chat = FakeChat([_round_with_call("create_workplane", {"name": "a"}) for _ in range(5)])
        result = _run(worker, chat, max_steps=2)
        self.assertFalse(result.ok)
        self.assertTrue(result.stopped)
        self.assertEqual(result.steps, 2)

    def test_stop_event(self) -> None:
        worker = FakeWorker()
        chat = FakeChat([_round_with_call("create_workplane", {"name": "a"})])
        stop_event = threading.Event()
        stop_event.set()
        result = _run(worker, chat, stop_event=stop_event)
        self.assertTrue(result.stopped)
        self.assertEqual(result.steps, 0)


class KernelDown(Exception):
    pass


class AgentLoopApprovalTests(unittest.TestCase):
    """P2：破坏性操作审批、破坏性修复审批、ask_user、超时、开局上下文。"""

    def test_destructive_op_pauses_for_approval_then_continues(self) -> None:
        from backend.agent.approvals import ApprovalBroker

        broker = ApprovalBroker(timeout=5)
        worker = FakeWorker([_success()])
        events: list[tuple] = []

        def chat(messages, tools):
            # 第一轮请求 delete_feature；第二轮收尾
            if not any(m.get("role") == "tool" for m in messages):
                return _round_with_call("delete_feature", {"feature_id": "F_0001"})
            return ToolCallRound(text="已删", tool_calls=[])

        def emit(event_type, message, payload):
            events.append((event_type, message, payload))

        def resolve_after_time():
            # 等 broker 收到请求后模拟用户 approve
            import time as _t

            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "approve")

        t = threading.Thread(target=resolve_after_time)
        t.start()
        result = _run(worker, chat, approvals=broker, emit=emit)
        t.join(timeout=2)
        # LLM 请求了删除，但用户 approve 后执行了 delete_feature
        self.assertIn(("delete_feature", {"feature_id": "F_0001"}), worker.executed)
        # 审批事件已广播
        self.assertTrue(any(e[0] == "approval_required" for e in events))
        self.assertTrue(result.ok)

    def test_destructive_op_reject_skips_op(self) -> None:
        from backend.agent.approvals import ApprovalBroker

        broker = ApprovalBroker(timeout=5)
        worker = FakeWorker()

        def chat(messages, tools):
            if not any(m.get("role") == "tool" for m in messages):
                return _round_with_call("delete_feature", {"feature_id": "F_0001"})
            return ToolCallRound(text="好的", tool_calls=[])

        def resolve_after_time():
            import time as _t

            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "reject")

        t = threading.Thread(target=resolve_after_time)
        t.start()
        result = _run(worker, chat, approvals=broker)
        t.join(timeout=2)
        # 用户拒绝 → delete_feature 不被执行
        self.assertNotIn("delete_feature", [op for op, _ in worker.executed])
        self.assertTrue(result.ok)

    def test_confirm_replace_fix_goes_through_approval(self) -> None:
        from backend.agent.approvals import ApprovalBroker

        broker = ApprovalBroker(timeout=5)
        worker = FakeWorker([
            {"success": False, "error_kind": "RECOVERABLE", "error": "new_body 会清空",
             "suggestion": {"fix": {"confirm_replace": True, "mode": "new_body"}, "reason_code": "new_body_conflict"},
             "geometry_summary": None},
            _success(5000.0),
        ])
        events: list[tuple] = []

        def chat(messages, tools):
            if not any(m.get("role") == "tool" for m in messages):
                return _round_with_call("extrude", {"sketch_name": "sk", "depth": 10, "mode": "new_body"})
            return ToolCallRound(text="修好", tool_calls=[])

        def resolve_after_time():
            import time as _t

            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "approve")

        t = threading.Thread(target=resolve_after_time)
        t.start()
        result = _run(worker, chat, approvals=broker, emit=lambda ev, m, p: events.append((ev, m, p)))
        t.join(timeout=2)
        # confirm_replace 的 fix 走审批而非自动重试 → 有 approval_required 事件
        self.assertTrue(any(e[0] == "approval_required" for e in events))
        # 修复重试的 extrude 带 confirm_replace（收尾的 validate_geometry 会追加在最后）
        extrudes = [args for op, args in worker.executed if op == "extrude"]
        self.assertEqual(len(extrudes), 2)
        self.assertTrue(extrudes[-1].get("confirm_replace") is True)
        self.assertTrue(result.ok)

    def test_ask_user_calls_and_receives_answer(self) -> None:
        from backend.agent.approvals import ApprovalBroker

        broker = ApprovalBroker(timeout=5)
        worker = FakeWorker([_success()])
        questions = [{"id": "q1", "question": "孔直径多少？", "type": "single",
                      "options": [{"label": "6mm"}, {"label": "8mm"}]}]

        calls: list = []

        def chat(messages, tools):
            calls.append(list(messages))
            tool_names = [t["function"]["name"] for t in tools]
            self.assertIn("ask_user", tool_names)  # 工具表含 ask_user
            if not any(m.get("role") == "tool" for m in messages):
                return _round_with_call("ask_user", {"questions": questions})
            return ToolCallRound(text="收到", tool_calls=[])

        def resolve_after_time():
            import time as _t

            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "edit", {"answers": {"q1": "8mm"}})

        t = threading.Thread(target=resolve_after_time)
        t.start()
        result = _run(worker, chat, approvals=broker, emit=lambda ev, m, p: None)
        t.join(timeout=2)
        # ask_user 不执行 kernel op（worker.executed 为空）
        self.assertEqual(worker.executed, [])
        self.assertTrue(result.ok)
        # 答案以 Q/A 转录回喂模型
        tool_message = next(m for m in calls[-1] if m.get("role") == "tool")
        payload = json.loads(tool_message["content"])
        self.assertEqual(payload["answers"], {"q1": "8mm"})
        self.assertIn("Q: 孔直径多少？", payload["transcript"])
        self.assertIn("A: 8mm", payload["transcript"])

    def test_ask_user_multi_questions_transcript(self) -> None:
        from backend.agent.approvals import ApprovalBroker

        broker = ApprovalBroker(timeout=5)
        worker = FakeWorker()
        questions = [
            {"id": "thick", "question": "底板厚度？", "type": "single", "options": [{"label": "10mm"}, {"label": "12mm"}]},
            {"id": "holes", "question": "需要哪些孔？", "type": "multi", "options": [{"label": "中心孔"}, {"label": "螺栓孔"}]},
        ]

        calls: list = []

        def chat(messages, tools):
            calls.append(list(messages))
            if not any(m.get("role") == "tool" for m in messages):
                return _round_with_call("ask_user", {"questions": questions})
            return ToolCallRound(text="好", tool_calls=[])

        def resolve_after_time():
            import time as _t

            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "edit", {"answers": {"thick": "12mm", "holes": ["中心孔", "螺栓孔"]}})

        t = threading.Thread(target=resolve_after_time)
        t.start()
        result = _run(worker, chat, approvals=broker, emit=lambda *_: None)
        t.join(timeout=2)
        self.assertTrue(result.ok)
        tool_message = next(m for m in calls[-1] if m.get("role") == "tool")
        transcript = json.loads(tool_message["content"])["transcript"]
        self.assertIn("A: 12mm", transcript)
        self.assertIn("中心孔、螺栓孔", transcript)  # multi 列表拼接

    def test_ask_user_reject_marks_declined(self) -> None:
        from backend.agent.approvals import ApprovalBroker

        broker = ApprovalBroker(timeout=5)
        worker = FakeWorker()

        calls: list = []

        def chat(messages, tools):
            calls.append(list(messages))
            if not any(m.get("role") == "tool" for m in messages):
                return _round_with_call("ask_user", {"questions": [{"id": "q1", "question": "孔径？", "type": "text"}]})
            return ToolCallRound(text="继续", tool_calls=[])

        def resolve_after_time():
            import time as _t

            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "reject")

        t = threading.Thread(target=resolve_after_time)
        t.start()
        result = _run(worker, chat, approvals=broker, emit=lambda *_: None)
        t.join(timeout=2)
        self.assertTrue(result.ok)
        tool_message = next(m for m in calls[-1] if m.get("role") == "tool")
        payload = json.loads(tool_message["content"])
        self.assertTrue(payload["declined"])
        self.assertIn("跳过", payload["transcript"])

    def test_approval_timeout_returns_skipped(self) -> None:
        from backend.agent.approvals import ApprovalBroker

        # 超时 0.15s，broker.request 返回 timeout；agent 把该步标记失败但继续
        broker = ApprovalBroker(timeout=0.15)
        worker = FakeWorker([_success()])
        events: list[tuple] = []

        def chat(messages, tools):
            if not any(m.get("role") == "tool" for m in messages):
                return _round_with_call("delete_feature", {"feature_id": "F_0001"})
            return ToolCallRound(text="继续", tool_calls=[])

        result = _run(worker, chat, approvals=broker, emit=lambda ev, m, p: events.append((ev, m, p)))
        # 超时 → 跳过该步，agent 收到 SKIPPED 并继续到收尾文字
        self.assertTrue(result.ok)
        self.assertNotIn("delete_feature", [op for op, _ in worker.executed])

    def test_opening_context_includes_feature_tree(self) -> None:
        worker = FakeWorker()
        chat = FakeChat([ToolCallRound(text="done", tool_calls=[])])
        result = _run(worker, chat)
        # 开局消息应含特征上下文（F_0001 + 可用 op 列表）
        first_user = result.logs  # 记录在 messages 里；改从 chat.calls 拿到 messages
        user_messages = chat.calls[0][0]
        opening = [m for m in user_messages if m["role"] == "user"]
        self.assertTrue(any("F_0001" in str(m["content"]) or "op" in str(m["content"]) for m in opening))


class AgentLoopSessionTests(unittest.TestCase):
    """v0.10 会话模式：历史持久化、运行中插话、审批事件带 id、final_text 入会话。"""

    def _session(self, td: Path) -> "AgentSession":
        from backend.agent.session import AgentSession

        return AgentSession(project_id="p1", path=td / "agent_session.json")

    def test_session_messages_flow_through_llm_and_persist(self) -> None:
        worker = FakeWorker([_success()])
        chat = FakeChat([
            _round_with_call("create_workplane", {"name": "base"}),
            ToolCallRound(text="做完了", tool_calls=[]),
        ])
        with tempfile.TemporaryDirectory() as td:
            session = self._session(Path(td))
            result = _run(worker, chat, session=session, run_dir=Path(td))
            self.assertTrue(result.ok)
            # LLM 首轮消息 = system + 任务消息（含内核上下文）
            first_roles = [m["role"] for m in chat.calls[0][0]]
            self.assertEqual(first_roles[0], "system")
            self.assertEqual(first_roles[1], "user")
            # 收尾后 session 持久化：user 任务消息 + assistant 工具轮 + tool 结果 + 最终 assistant
            roles = [m["role"] for m in session.llm_messages()]
            self.assertEqual(roles[0], "user")
            self.assertIn("assistant", roles)
            self.assertIn("tool", roles)
            self.assertEqual(roles[-1], "assistant")
            # 落盘可恢复
            from backend.agent.session import AgentSession as AS

            restored = AS(project_id="p1", path=session.path)
            data = json.loads(session.path.read_text(encoding="utf-8"))
            restored.messages = list(data["messages"])
            self.assertEqual(len(restored.llm_messages()), len(session.llm_messages()))

    def test_pending_user_message_injected_next_round(self) -> None:
        worker = FakeWorker([_success()])
        chat = FakeChat([
            _round_with_call("create_workplane", {"name": "base"}),
            ToolCallRound(text="完成", tool_calls=[]),
        ])
        with tempfile.TemporaryDirectory() as td:
            session = self._session(Path(td))

            def chat_with_injection(messages, tools):
                round_result = chat(messages, tools)
                if len(chat.calls) == 1:
                    # 第一轮进行中用户插话 → 第二轮应看到
                    session.enqueue_user("把尺寸改大一点")
                return round_result

            result = _run(worker, chat_with_injection, session=session, run_dir=Path(td))
            self.assertTrue(result.ok)
            second_round_messages = chat.calls[1][0]
            injected = [m for m in second_round_messages if m["role"] == "user" and "改大" in str(m["content"])]
            self.assertEqual(len(injected), 1)

    def test_approval_event_carries_approval_id(self) -> None:
        from backend.agent.approvals import ApprovalBroker

        broker = ApprovalBroker(timeout=5)
        worker = FakeWorker([_success()])
        events: list[tuple] = []

        def chat(messages, tools):
            if not any(m.get("role") == "tool" for m in messages):
                return _round_with_call("delete_feature", {"feature_id": "F_0001"})
            return ToolCallRound(text="好的", tool_calls=[])

        def resolve_after_time():
            import time as _t

            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "reject")

        t = threading.Thread(target=resolve_after_time)
        t.start()
        result = _run(worker, chat, approvals=broker,
                      emit=lambda ev, m, p: events.append((ev, m, p)))
        t.join(timeout=2)
        approval_events = [e for e in events if e[0] == "approval_required"]
        self.assertEqual(len(approval_events), 1)
        payload = approval_events[0][2]
        self.assertTrue(payload.get("approval_id"), "审批事件必须带 approval_id（前端回复依赖它）")

    def test_session_status_transitions(self) -> None:
        worker = FakeWorker([_success()])
        chat = FakeChat([ToolCallRound(text="done", tool_calls=[])])
        with tempfile.TemporaryDirectory() as td:
            session = self._session(Path(td))
            _run(worker, chat, session=session, run_dir=Path(td))
            self.assertEqual(session.status, "idle")


class AgentLoopPlanModeTests(unittest.TestCase):
    """v0.11 计划模式：工具门控、propose_plan 审批、update_plan 进度、每轮一次。"""

    def test_plan_mode_gates_tools_then_approve_unlocks(self) -> None:
        from backend.agent.approvals import ApprovalBroker

        broker = ApprovalBroker(timeout=5)
        worker = FakeWorker([_success()])
        events: list = []
        tools_by_round: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            tools_by_round.append([t["function"]["name"] for t in tools])
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("propose_plan", {"summary": "底板+孔", "steps": [{"id": "s1", "title": "建底板", "op": "create_workplane"}]})
            if calls["n"] == 2:
                return _round_with_call("create_workplane", {"name": "base"})
            return ToolCallRound(text="完成", tool_calls=[])

        def resolve_after_time():
            import time as _t

            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "approve")

        t = threading.Thread(target=resolve_after_time)
        t.start()
        result = _run(worker, chat, approvals=broker, mode="plan", emit=lambda ev, m, p: events.append((ev, m, p)))
        t.join(timeout=2)
        # 批准前：工具表含 propose_plan 但不含建模 op create_workplane
        self.assertIn("propose_plan", tools_by_round[0])
        self.assertNotIn("create_workplane", tools_by_round[0])
        # 批准后：建模 op 解锁并真正执行
        self.assertIn("create_workplane", tools_by_round[1])
        self.assertIn(("create_workplane", {"name": "base"}), worker.executed)
        self.assertTrue(any(e[0] == "approval_required" and e[2].get("kind") == "plan_review" for e in events))
        self.assertTrue(any(e[0] == "plan_updated" for e in events))
        self.assertTrue(result.ok)

    def test_plan_mode_reject_keeps_planning(self) -> None:
        from backend.agent.approvals import ApprovalBroker

        broker = ApprovalBroker(timeout=5)
        worker = FakeWorker()
        tools_by_round: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            tools_by_round.append([t["function"]["name"] for t in tools])
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("propose_plan", {"summary": "方案", "steps": [{"id": "s1", "title": "建底板"}]})
            return ToolCallRound(text="好，我重新规划", tool_calls=[])

        def resolve_after_time():
            import time as _t

            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "reject", {"feedback": "先加个圆角"})

        t = threading.Thread(target=resolve_after_time)
        t.start()
        result = _run(worker, chat, approvals=broker, mode="plan", emit=lambda *_: None)
        t.join(timeout=2)
        # 拒绝后仍留在计划模式：第二轮工具表依旧不含建模 op，且从未执行建模
        self.assertNotIn("create_workplane", tools_by_round[1])
        self.assertEqual(worker.executed, [])
        self.assertTrue(result.ok)

    def test_update_plan_emits_progress_and_once_per_round(self) -> None:
        worker = FakeWorker()
        events: list = []
        calls = {"n": 0}

        def two_update_round() -> ToolCallRound:
            return ToolCallRound(
                text="",
                tool_calls=[
                    ToolCall(id="c1", name="update_plan", arguments={"todos": [{"id": "s1", "status": "in_progress"}]}),
                    ToolCall(id="c2", name="update_plan", arguments={"todos": [{"id": "s1", "status": "completed"}]}),
                ],
                raw_message={"role": "assistant", "content": None, "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "update_plan", "arguments": json.dumps({"todos": [{"id": "s1", "status": "in_progress"}]})}},
                    {"id": "c2", "type": "function", "function": {"name": "update_plan", "arguments": json.dumps({"todos": [{"id": "s1", "status": "completed"}]})}},
                ]},
            )

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return two_update_round()
            # 第二轮检查回喂：应有一条 error（每轮至多一次）
            err = [m for m in messages if m.get("role") == "tool" and "每轮至多一次" in str(m.get("content"))]
            self.assertTrue(err, "同轮第二次 update_plan 应被拒")
            return ToolCallRound(text="完成", tool_calls=[])

        result = _run(worker, chat, emit=lambda ev, m, p: events.append((ev, m, p)))
        plan_events = [e for e in events if e[0] == "plan_updated"]
        self.assertEqual(len(plan_events), 1)  # 只有第一次成功
        self.assertEqual(plan_events[0][2]["steps"][0]["status"], "in_progress")
        self.assertTrue(result.ok)


class AgentLoopDesignCalculateTests(unittest.TestCase):
    """v0.12 design_calculate：计划门控期可用、纯算术无几何副作用、custom 沙箱。"""

    def _tool_payload(self, messages: list) -> dict:
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        self.assertTrue(tool_msgs, "应有工具结果回喂")
        return json.loads(tool_msgs[-1]["content"])

    def test_ratio_split_available_before_approval_and_returns_schemes(self) -> None:
        worker = FakeWorker()
        events: list = []
        tools_by_round: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            tools_by_round.append([t["function"]["name"] for t in tools])
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call(
                    "design_calculate",
                    {"kind": "gear_ratio_split", "params": {"total_ratio": 100}, "reason": "1:100 怎么分级"},
                )
            self._tool_payload(messages)  # 至少要看到第一轮的调研结果
            return ToolCallRound(text="调研完成", tool_calls=[])

        result = _run(worker, chat, mode="plan",
                      emit=lambda ev, m, p: events.append((ev, m, p)))
        # 计划门控期间 design_calculate 可用、建模 op 不可用
        self.assertIn("design_calculate", tools_by_round[0])
        self.assertNotIn("create_workplane", tools_by_round[0])
        self.assertNotIn("finish_part", tools_by_round[0])  # 归档工具批准后才暴露
        # 工具没有走 worker（无几何副作用）
        self.assertEqual(worker.executed, [])
        # 会话流里有调研卡片
        self.assertTrue(any(e[0] == "agent_step" and e[2].get("op") == "design_calculate"
                            for e in events))
        # 结果与调研转录
        self.assertEqual(len(result.design_calculations), 1)
        self.assertTrue(result.design_calculations[0]["ok"])
        self.assertTrue(result.ok)

    def test_custom_sandbox_roundtrip_and_block(self) -> None:
        worker = FakeWorker()
        seen: list[dict] = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("design_calculate",
                                        {"kind": "custom",
                                         "code": "result = {\"d\": 2 * sqrt(a), \"ratio\": i1 * i2}",
                                         "variables": {"a": 16.0, "i1": 4.0, "i2": 5.0}})
            if calls["n"] == 2:
                seen.append(self._tool_payload(messages))
                return _round_with_call("design_calculate",
                                        {"kind": "custom", "code": "import os\nresult = os.getcwd()"})
            seen.append(self._tool_payload(messages))
            return ToolCallRound(text="懂", tool_calls=[])

        result = _run(worker, chat)
        self.assertTrue(seen[0]["success"], seen[0])
        self.assertAlmostEqual(seen[0]["result"]["result"]["d"], 8.0)
        self.assertAlmostEqual(seen[0]["result"]["result"]["ratio"], 20.0)
        # import 逃逸被沙箱拒绝，错误回喂但 agent 不死
        self.assertFalse(seen[1]["success"])
        self.assertEqual(seen[1]["error_kind"], "INVALID_REQUEST")
        self.assertIn("import", seen[1]["error"])
        self.assertTrue(result.ok)

    def test_unknown_kind_returns_error(self) -> None:
        worker = FakeWorker()
        seen: list[dict] = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("design_calculate", {"kind": "magic"})
            seen.append(self._tool_payload(messages))
            return ToolCallRound(text="end", tool_calls=[])

        _run(worker, chat)
        self.assertFalse(seen[0]["success"])
        self.assertIn("gear_ratio_split", seen[0]["error"])


class AgentLoopBomPlanTests(unittest.TestCase):
    """v0.12 BOM 计划：审批卡带零件清单、批准持久化、解锁 finish_part。"""

    def _bom(self) -> list:
        return [
            {"part": "小齿轮", "role": "高速级主动轮", "quantity": 1,
             "key_params": {"模数": "2", "齿数": "20"}},
            {"part": "箱体", "role": "壳体", "quantity": 1},
        ]

    def test_propose_plan_with_bom_flow(self) -> None:
        from backend.agent.approvals import ApprovalBroker

        broker = ApprovalBroker(timeout=5)
        worker = FakeWorker([_success()])
        events: list = []
        tools_by_round: list = []
        rounds_messages: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            tools_by_round.append([t["function"]["name"] for t in tools])
            rounds_messages.append(list(messages))
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("propose_plan", {
                    "summary": "两级减速，4 类零件",
                    "bom": self._bom(),
                    "steps": [
                        {"id": "s1", "title": "建小齿轮", "op": "make_gear", "part": "小齿轮"},
                        {"id": "s2", "title": "建箱体", "op": "extrude", "part": "箱体"},
                    ],
                })
            return ToolCallRound(text="开工", tool_calls=[])

        def resolve_after_time():
            import time as _t

            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "approve")

        t = threading.Thread(target=resolve_after_time)
        t.start()
        result = _run(worker, chat, approvals=broker, mode="plan",
                      emit=lambda ev, m, p: events.append((ev, m, p)))
        t.join(timeout=2)
        # 审批卡 payload 带 BOM
        plan_reviews = [e for e in events if e[0] == "approval_required" and e[2].get("kind") == "plan_review"]
        self.assertEqual(len(plan_reviews), 1)
        plan_payload = plan_reviews[0][2]["options"]["plan"]
        self.assertEqual([b["part"] for b in plan_payload["bom"]], ["小齿轮", "箱体"])
        self.assertEqual(plan_payload["steps"][0]["part"], "小齿轮")
        # plan_updated 事件带 bom；步骤保留 part 归属
        plan_events = [e for e in events if e[0] == "plan_updated"]
        self.assertTrue(plan_events and plan_events[0][2].get("bom"))
        # 批准后解锁 finish_part 与全部建模 op
        self.assertIn("finish_part", tools_by_round[1])
        self.assertIn("create_workplane", tools_by_round[1])
        # 回喂给模型的批准说明含逐件执行协议
        tool_msgs = [m for m in rounds_messages[1] if m.get("role") == "tool"]
        note = json.loads(tool_msgs[-1]["content"])
        self.assertIn("2 类零件", note["note"])
        self.assertIn("finish_part", note["note"])
        self.assertTrue(result.ok)

    def test_bom_normalization_tolerance(self) -> None:
        from backend.agent.loop import _normalize_bom

        bom = _normalize_bom([
            {"part": "轴"},                                   # quantity 缺省 1
            {"part": " 齿轮 "},                                # 名字两侧空白剔除
            {"role": "没有零件名"},                             # 无 part → 丢弃
            {"part": "轴承", "quantity": 200, "key_params": {"x" * 50: "y" * 200, "n": 3}},
            "not-a-dict",
        ])
        self.assertEqual(len(bom), 3)
        self.assertEqual(bom[0]["quantity"], 1)
        self.assertEqual(bom[1]["part"], "齿轮")
        self.assertEqual(bom[2]["quantity"], 99)               # 上限夹住
        self.assertEqual(bom[2]["key_params"]["n"], "3")       # 值转字符串
        self.assertLessEqual(len(bom[2]["key_params"]["x" * 40]), 60)  # 键/值截断
        self.assertEqual(_normalize_bom(None), [])


class AgentLoopFinishPartTests(unittest.TestCase):
    """v0.12 finish_part：导出归档 → reset → 计划打勾；各失败路径。"""

    def _bom_steps(self) -> tuple[list, list]:
        bom = [{"part": "g1", "role": "齿轮1"}, {"part": "g2", "role": "齿轮2"}]
        steps = [
            {"id": "s1", "title": "建 g1", "part": "g1"},
            {"id": "s2", "title": "建 g2", "part": "g2"},
        ]
        return bom, steps

    def test_two_parts_archive_reset_and_tick(self) -> None:
        from backend.agent.approvals import ApprovalBroker
        from backend.agent.session import AgentSession

        broker = ApprovalBroker(timeout=5)
        # 执行脚本：create_workplane → 成功；每次 finish_part 探针 = volume + solid_count
        worker = FakeWorker([
            _success(5000.0),
            {"success": True, "value": 12345.0},   # g1 volume
            {"success": True, "value": 1},         # g1 solid_count（复检门）
            {"success": True, "value": 5000.0},    # g2 volume
            {"success": True, "value": 1},         # g2 solid_count
        ])
        events: list = []
        bom, steps = self._bom_steps()
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("propose_plan", {"summary": "两件", "bom": bom, "steps": steps})
            if calls["n"] == 2:
                return _round_with_call("create_workplane", {"name": "base"})
            if calls["n"] == 3:
                return _round_with_call("finish_part", {"part": "g1"})
            if calls["n"] == 4:
                return _round_with_call("finish_part", {"part": "g2", "note": "末件"})
            return ToolCallRound(text="全部完成", tool_calls=[])

        def resolve_after_time():
            import time as _t

            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "approve")

        with tempfile.TemporaryDirectory() as td:
            session = AgentSession(project_id="p1", path=Path(td) / "s.json")
            t = threading.Thread(target=resolve_after_time)
            t.start()
            result = _run(worker, chat, approvals=broker, session=session,
                          run_dir=Path(td), mode="plan",
                          emit=lambda ev, m, p: events.append((ev, m, p)))
            t.join(timeout=2)
            report = json.loads(Path(result.artifacts["execution_report"]).read_text(encoding="utf-8"))

        self.assertTrue(result.ok, result.error)
        self.assertEqual(worker.reset_calls, 2)
        self.assertEqual(len(result.parts), 2)
        first, second = result.parts
        self.assertEqual(first["part"], "g1")
        self.assertEqual(first["volume_mm3"], 12345.0)
        self.assertTrue(first["step_file"].startswith("part_01_"))
        self.assertTrue(first["step_file"].endswith(".step"))
        self.assertTrue(first["stl_file"].startswith("part_01_") and first["stl_file"].endswith(".stl"))
        self.assertTrue(second["step_file"].startswith("part_02_"))
        # worker 导出：两件各一份 STEP；STL = 两件归档 + 建模期的 model.stl
        self.assertEqual(len(worker.exported_step), 2)
        self.assertTrue(all("part_0" in p for p in worker.exported_step))
        self.assertEqual(len([p for p in worker.exported_mesh if "part_0" in p]), 2)
        # artifact_ready 事件两次、kind=part
        part_events = [e for e in events if e[0] == "artifact_ready" and e[2].get("kind") == "part"]
        self.assertEqual([e[2]["part"] for e in part_events], ["g1", "g2"])
        # 计划按零件打勾（session 持久化）
        statuses = {s["id"]: s["status"] for s in session.plan["steps"]}
        self.assertEqual(statuses, {"s1": "completed", "s2": "completed"})
        # 归档后当前几何为空：最终 result.volume 是 None，但 parts 让报告成立
        self.assertIsNone(result.volume)
        self.assertEqual(len(report["parts"]), 2)
        self.assertTrue(report["ok"])

    def test_finish_part_before_plan_approval_blocked(self) -> None:
        worker = FakeWorker()
        seen: list[dict] = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("finish_part", {"part": "g1"})
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            seen.append(json.loads(tool_msgs[-1]["content"]))
            return ToolCallRound(text="好吧", tool_calls=[])

        # 计划模式未批准：finish_part 不在工具表（幻觉调用也被 handler 拒绝）
        result = _run(worker, chat, mode="plan")
        self.assertEqual(seen[0]["error_kind"], "PLAN_REQUIRED")
        self.assertEqual(worker.reset_calls, 0)
        self.assertTrue(result.ok)

    def test_finish_part_without_geometry(self) -> None:
        worker = FakeWorker([{"success": True, "value": None}])
        seen: list[dict] = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("finish_part", {"part": "g1"})
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            seen.append(json.loads(tool_msgs[-1]["content"]))
            return ToolCallRound(text="明白", tool_calls=[])

        # auto 模式下计划视为已批准，但当前无几何 → 拒绝且不 reset
        _run(worker, chat)
        self.assertFalse(seen[0]["success"])
        self.assertEqual(seen[0]["error_kind"], "INVALID_REQUEST")
        self.assertEqual(worker.reset_calls, 0)
        self.assertEqual(worker.exported_step, [])

    def test_finish_part_reset_failure_tells_model_to_stop(self) -> None:
        worker = FakeWorker([{"success": True, "value": 900.0}])
        worker.reset_error = RuntimeError("boom")
        seen: list[dict] = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("finish_part", {"part": "g1"})
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            seen.append(json.loads(tool_msgs[-1]["content"]))
            return ToolCallRound(text="报告用户", tool_calls=[])

        result = _run(worker, chat)
        self.assertFalse(seen[0]["success"])
        self.assertEqual(seen[0]["error_kind"], "WORKER_ERROR")
        self.assertIn("不要继续", seen[0]["error"].replace("请勿", "不要"))
        self.assertEqual(result.parts, [])  # reset 失败不算归档成功
        self.assertEqual(len(worker.exported_step), 1)  # 文件已导出（保留现场）


class AgentLoopRunBuildScriptTests(unittest.TestCase):
    """v0.13 run_build_script：建模脚本通道（合成工具、门控、回传、built_via）。"""

    def _last_tool_payload(self, messages: list) -> dict:
        tool_msgs = [m for m in messages if m.get("role") == "tool"]
        self.assertTrue(tool_msgs)
        return json.loads(tool_msgs[-1]["content"])

    def test_script_success_tracks_geometry_and_marks_built_via(self) -> None:
        worker = FakeWorker()
        events: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("run_build_script",
                                        {"code": "k.extrude(sketch_name='s', depth=10)", "reason": "建壳体"})
            payload = self._last_tool_payload(messages)
            self.assertTrue(payload["success"])
            self.assertEqual(payload["solids"], 1)
            self.assertEqual(payload["ops_executed"], 3)
            self.assertIn("ok", payload["stdout"])
            self.assertIn("可参数重放", payload["note"])
            return ToolCallRound(text="完成", tool_calls=[])

        result = _run(worker, chat, emit=lambda ev, m, p: events.append((ev, m, p)))
        self.assertEqual(worker.run_script_calls[0][0], "k.extrude(sketch_name='s', depth=10)")
        # 合成工具成功后手动触发 track：STL 导出 + 快照发生
        self.assertTrue(any(p.endswith("model.stl") for p in worker.exported_mesh))
        self.assertTrue(worker.render_calls)
        # 会话流有脚本卡片
        self.assertTrue(any(e[0] == "agent_step" and e[2].get("op") == "run_build_script" for e in events))
        self.assertTrue(result.ok)

    def test_script_failure_returns_traceback_and_keeps_state(self) -> None:
        worker = FakeWorker()
        worker.run_script_results = [{
            "success": False, "error_kind": "RECOVERABLE",
            "error": "脚本执行失败（状态已回滚到执行前，可安全改脚本重试）:\nValueError: boom",
        }]
        seen: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("run_build_script", {"code": "raise ValueError('boom')"})
            seen.append(self._last_tool_payload(messages))
            return ToolCallRound(text="改脚本", tool_calls=[])

        _run(worker, chat)
        self.assertFalse(seen[0]["success"])
        self.assertIn("boom", seen[0]["error"])
        self.assertIn("已回滚", seen[0]["note"])
        # 失败不触发导出/快照
        self.assertEqual(worker.exported_mesh, [])

    def test_script_hidden_before_plan_approval_and_blocked_as_hallucination(self) -> None:
        worker = FakeWorker()
        tools_by_round: list = []
        seen: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            tools_by_round.append([t["function"]["name"] for t in tools])
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("run_build_script", {"code": "k.extrude(sketch_name='s', depth=1)"})
            seen.append(self._last_tool_payload(messages))
            return ToolCallRound(text="好", tool_calls=[])

        # 计划模式未批准：工具表不含 run_build_script；幻觉调用也被 handler 拒绝
        _run(worker, chat, mode="plan")
        self.assertNotIn("run_build_script", tools_by_round[0])
        self.assertEqual(seen[0]["error_kind"], "PLAN_REQUIRED")
        self.assertEqual(worker.run_script_calls, [])

    def test_script_built_via_flows_into_finish_part_record(self) -> None:
        worker = FakeWorker([
            {"success": True, "value": 8000.0},   # volume 探针
            {"success": True, "value": 1},        # solid_count 探针
        ])
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("run_build_script", {"code": "k.extrude(sketch_name='s', depth=10)"})
            if calls["n"] == 2:
                return _round_with_call("finish_part", {"part": "housing"})
            return ToolCallRound(text="归档完成", tool_calls=[])

        # auto 模式：计划视为已批准，finish_part 可用
        result = _run(worker, chat)
        self.assertEqual(len(result.parts), 1)
        self.assertEqual(result.parts[0]["built_via"], "script")
        self.assertEqual(worker.reset_calls, 1)

    def test_finish_part_rejects_multi_solid_design_review_gate(self) -> None:
        """复检门：solid_count>1（悬浮特征）拒绝归档，不导出不清会话。"""
        worker = FakeWorker([
            {"success": True, "value": 8000.0},   # volume
            {"success": True, "value": 3},        # solid_count = 3 → 拒绝
        ])
        seen: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("finish_part", {"part": "housing"})
            seen.append(self._last_tool_payload(messages))
            return ToolCallRound(text="去修复", tool_calls=[])

        _run(worker, chat)
        self.assertFalse(seen[0]["success"])
        self.assertEqual(seen[0]["error_kind"], "GEOMETRY_FAILURE")
        self.assertIn("3 个独立实体", seen[0]["error"])
        self.assertEqual(worker.exported_step, [])
        self.assertEqual(worker.reset_calls, 0)

    def test_finish_part_feature_contract_mismatch_blocks_archiving(self) -> None:
        """v0.13 特征契约：实测圆柱面数量与断言不符 → FEATURE_CONTRACT_MISMATCH，拒绝归档。
        （真实 LLM 验收发现：undo 回滚掉 4 螺栓孔+1 轴承孔后模型仍谎报完成）"""
        worker = FakeWorker([
            {"success": True, "value": 8000.0},   # volume
            {"success": True, "value": 1},        # solid_count
            {"success": True, "value": {"selected": [  # select cylinder：只有 1×Ø25 + 2×Ø40
                {"ref": "F03", "type": "cylinder", "radius_mm": 12.5},
                {"ref": "F04", "type": "cylinder", "radius_mm": 20.0},
                {"ref": "F05", "type": "cylinder", "radius_mm": 20.0},
            ]}},
        ])
        seen: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("finish_part", {
                    "part": "housing",
                    "feature_contract": [
                        {"radius_mm": 4.5, "count": 4},    # 断言 4 螺栓孔
                        {"radius_mm": 12.5, "count": 2},   # 断言 2 轴承孔
                    ],
                })
            seen.append(self._last_tool_payload(messages))
            return ToolCallRound(text="补孔", tool_calls=[])

        _run(worker, chat)
        self.assertFalse(seen[0]["success"])
        self.assertEqual(seen[0]["error_kind"], "FEATURE_CONTRACT_MISMATCH")
        self.assertIn("4.5", seen[0]["error"])
        self.assertIn("12.5", seen[0]["error"])
        self.assertEqual(len(seen[0]["violations"]), 2)
        self.assertEqual(worker.exported_step, [])
        self.assertEqual(worker.reset_calls, 0)

    def test_finish_part_feature_contract_passes_when_matched(self) -> None:
        worker = FakeWorker([
            {"success": True, "value": 8000.0},
            {"success": True, "value": 1},
            {"success": True, "value": {"selected": [
                {"ref": "F03", "type": "cylinder", "radius_mm": 4.5},
                {"ref": "F04", "type": "cylinder", "radius_mm": 4.5},
                {"ref": "F05", "type": "cylinder", "radius_mm": 12.5},
            ]}},
        ])
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("finish_part", {
                    "part": "housing",
                    "feature_contract": [{"radius_mm": 4.5, "count": 2},
                                         {"radius_mm": 12.5, "count": 1}],
                })
            return ToolCallRound(text="完成", tool_calls=[])

        result = _run(worker, chat)
        self.assertEqual(len(result.parts), 1)
        self.assertEqual(worker.reset_calls, 1)




    def test_duplicate_part_archiving_rejected(self) -> None:
        """v0.13.2: 同名零件二次归档被拒（真实 LLM 曾把 11 件归档成 24 次）。"""
        worker = FakeWorker([
            {"success": True, "value": 8000.0}, {"success": True, "value": 1},
            {"success": True, "value": 8000.0}, {"success": True, "value": 1},
        ])
        seen: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("finish_part", {"part": "housing"})
            if calls["n"] == 2:
                return _round_with_call("finish_part", {"part": "housing"})  # 重复
            seen.append(self._last_tool_payload(messages))
            return ToolCallRound(text="换下一件", tool_calls=[])

        result = _run(worker, chat)
        self.assertEqual(len(result.parts), 1)          # 只归档一次
        self.assertFalse(seen[0]["success"])
        self.assertEqual(seen[0]["error_kind"], "DUPLICATE_PART")
        self.assertEqual(worker.reset_calls, 1)         # 第二次未触发 reset

    def test_incomplete_plan_gets_nagged_to_continue(self) -> None:
        """v0.13.2: 计划批准后模型停止但还有 pending 步骤 → 注入提醒一次并继续。"""
        from backend.agent.approvals import ApprovalBroker
        from backend.agent.session import AgentSession
        import tempfile as _tf

        broker = ApprovalBroker(timeout=5)
        worker = FakeWorker()
        rounds_msgs: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            rounds_msgs.append(list(messages))
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("propose_plan", {
                    "summary": "两件", "bom": [{"part": "a"}, {"part": "b"}],
                    "steps": [{"id": "s1", "title": "建 a", "part": "a"},
                              {"id": "s2", "title": "建 b", "part": "b"}]})
            if calls["n"] == 2:
                return ToolCallRound(text="我先归档 a 就收尾", tool_calls=[])  # 提前停
            # 被提醒后应继续
            return ToolCallRound(text="好的，继续", tool_calls=[])

        def resolve_after_time():
            import time as _t
            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "approve")

        import threading as _th
        with _tf.TemporaryDirectory() as td:
            session = AgentSession(project_id="p1", path=Path(td) / "s.json")
            t = _th.Thread(target=resolve_after_time); t.start()
            result = _run(worker, chat, approvals=broker, session=session,
                          run_dir=Path(td), mode="plan", emit=lambda *_: None)
            t.join(timeout=2)
        # 第三轮消息里应含提醒
        nag = [m for m in rounds_msgs[-1] if m.get("role") == "user" and "计划尚未完成" in str(m.get("content"))]
        self.assertTrue(nag, "应注入计划未完成提醒")
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
