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

    def capabilities(self) -> dict:
        return self.capabilities_result

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


def _run(worker, chat, *, max_steps=10, stop_event=None, emit=None, run_dir=None, approvals=None, session=None):
    if run_dir is None:
        with tempfile.TemporaryDirectory() as td:
            return _run_in_dir(worker, chat, Path(td), max_steps=max_steps, stop_event=stop_event, emit=emit, approvals=approvals, session=session)
    return _run_in_dir(worker, chat, Path(run_dir), max_steps=max_steps, stop_event=stop_event, emit=emit, approvals=approvals, session=session)


def _run_in_dir(worker, chat, run_dir: Path, *, max_steps, stop_event, emit, approvals=None, session=None):
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

        def chat(messages, tools):
            tool_names = [t["function"]["name"] for t in tools]
            self.assertIn("ask_user", tool_names)  # 工具表含 ask_user
            if not any(m.get("role") == "tool" for m in messages):
                return _round_with_call("ask_user", {"question": "孔直径多少？", "options": ["6", "8"]})
            return ToolCallRound(text="收到", tool_calls=[])

        def resolve_after_time():
            import time as _t

            _t.sleep(0.15)
            with broker._lock:
                aid = next(iter(broker._requests))
            broker.resolve(aid, "edit", {"answer": "8mm"})

        t = threading.Thread(target=resolve_after_time)
        t.start()
        final_messages = []
        result = _run(worker, chat, approvals=broker,
                      emit=lambda ev, m, p: None)
        t.join(timeout=2)
        # ask_user 不执行 kernel op（worker.executed 为空），答案经 tool result 回喂模型
        self.assertEqual(worker.executed, [])
        self.assertTrue(result.ok)

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


if __name__ == "__main__":
    unittest.main()
