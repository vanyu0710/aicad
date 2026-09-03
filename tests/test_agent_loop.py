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
                             "enum": ["new_body", "add", "cut"]}},
         "examples": []},
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


def _run(worker, chat, *, max_steps=10, stop_event=None, emit=None, run_dir=None):
    if run_dir is None:
        with tempfile.TemporaryDirectory() as td:
            return _run_in_dir(worker, chat, Path(td), max_steps=max_steps, stop_event=stop_event, emit=emit)
    return _run_in_dir(worker, chat, Path(run_dir), max_steps=max_steps, stop_event=stop_event, emit=emit)


def _run_in_dir(worker, chat, run_dir: Path, *, max_steps, stop_event, emit):
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


if __name__ == "__main__":
    unittest.main()
