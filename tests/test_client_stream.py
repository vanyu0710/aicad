"""chat_completion_with_tools SSE 流式路径测试（mock HTTP，双协议）。"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.mechcad_ai.client import chat_completion_with_tools

SETTINGS = SimpleNamespace(
    planner_api_key="sk-test",
    planner_base_url="https://llm.example.com/v1",
    planner_model="test-model",
    planner_protocol="openai",
)

TOOLS = [
    {"type": "function", "function": {
        "name": "create_workplane",
        "description": "创建工作平面",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}}},
    }},
]


class FakeSSEResponse:
    """最小 SSE 响应：iter_lines 逐行返回预置行。"""

    def __init__(self, lines: list[str], status_code: int = 200) -> None:
        self._lines = lines
        self.ok = status_code < 400
        self.status_code = status_code
        self.headers = {"content-type": "text/event-stream"}
        self.text = ""

    def iter_lines(self, decode_unicode: bool = False) -> list[str]:
        return list(self._lines)


class OpenAIStreamTests(unittest.TestCase):
    def test_text_deltas_and_tool_call_assembly(self) -> None:
        lines = [
            'data: {"choices":[{"delta":{"role":"assistant","content":"你好"}}]}',
            "",
            'data: {"choices":[{"delta":{"content":"，先建基准面"}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1","function":{"name":"create_workplane","arguments":""}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"name\\":"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"base\\"}"}}]}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ]
        deltas: list[str] = []
        captured: dict = {}

        def fake_post(url, headers=None, json=None, timeout=None, stream=False):
            captured["payload"] = json
            return FakeSSEResponse(lines)

        with patch("backend.mechcad_ai.client.requests.post", side_effect=fake_post):
            round_result = chat_completion_with_tools(
                SETTINGS, "planner", [{"role": "user", "content": "hi"}], TOOLS,
                on_text_delta=deltas.append,
            )
        self.assertEqual(deltas, ["你好", "，先建基准面"])
        self.assertEqual(round_result.text, "你好，先建基准面")
        self.assertEqual(len(round_result.tool_calls), 1)
        call = round_result.tool_calls[0]
        self.assertEqual(call.id, "call-1")
        self.assertEqual(call.name, "create_workplane")
        self.assertEqual(call.arguments, {"name": "base"})
        # raw_message 保持 OpenAI 形状（arguments 为 JSON 字符串），可直接回喂
        raw = round_result.raw_message
        self.assertEqual(raw["role"], "assistant")
        self.assertEqual(raw["tool_calls"][0]["function"]["arguments"], json.dumps({"name": "base"}, ensure_ascii=False))
        self.assertEqual(captured["payload"].get("stream"), True)

    def test_no_on_text_delta_means_sync_path(self) -> None:
        body = {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}

        class JsonResponse(FakeSSEResponse):
            def __init__(self):
                super().__init__([])
                self.headers = {"content-type": "application/json"}

            def json(self):
                return body

        def fake_post(url, headers=None, json=None, timeout=None, stream=False):
            self.assertFalse(stream)
            return JsonResponse()

        with patch("backend.mechcad_ai.client.requests.post", side_effect=fake_post):
            round_result = chat_completion_with_tools(
                SETTINGS, "planner", [{"role": "user", "content": "hi"}], TOOLS,
            )
        self.assertEqual(round_result.text, "ok")


class AnthropicStreamTests(unittest.TestCase):
    def test_text_and_tool_use_from_sse_events(self) -> None:
        lines = [
            'event: message_start',
            'data: {"type":"message_start","message":{}}',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"先画草图"}}',
            'data: {"type":"content_block_stop","index":0}',
            'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu-1","name":"create_workplane"}}',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"name\\": "}}',
            'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"\\"base\\"}"}}',
            'data: {"type":"content_block_stop","index":1}',
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}',
            'data: {"type":"message_stop"}',
        ]
        deltas: list[str] = []
        settings = SimpleNamespace(
            planner_api_key="sk-test",
            planner_base_url="https://llm.example.com",
            planner_model="claude-test",
            planner_protocol="anthropic",
        )

        def fake_post(url, headers=None, json=None, timeout=None, stream=False):
            self.assertTrue(url.endswith("/v1/messages"))
            return FakeSSEResponse(lines)

        with patch("backend.mechcad_ai.client.requests.post", side_effect=fake_post):
            round_result = chat_completion_with_tools(
                settings, "planner", [{"role": "user", "content": "hi"}], TOOLS,
                on_text_delta=deltas.append,
            )
        self.assertEqual(deltas, ["先画草图"])
        self.assertEqual(round_result.text, "先画草图")
        self.assertEqual(round_result.finish_reason, "tool_use")
        self.assertEqual(len(round_result.tool_calls), 1)
        self.assertEqual(round_result.tool_calls[0].arguments, {"name": "base"})
        self.assertEqual(round_result.raw_message["content"][1]["type"], "tool_use")


class LoopStreamEmissionTests(unittest.TestCase):
    """loop 检测 chat_with_tools 支持增量回调时按流式发事件，且不重复发整轮文字。"""

    def test_loop_emits_stream_deltas_without_duplicate(self) -> None:
        import tempfile
        from pathlib import Path

        from tests.test_agent_loop import CAPABILITIES, FakeWorker, _round_with_call
        from backend.agent import run_agent_loop
        from backend.mechcad_ai.client import ToolCallRound

        events: list[tuple] = []
        worker = FakeWorker([])

        def chat(messages, tools, on_text_delta=None):
            self.assertIsNotNone(on_text_delta)
            on_text_delta("先建")
            on_text_delta("基准面")
            return ToolCallRound(text="先建基准面", tool_calls=[])

        with tempfile.TemporaryDirectory() as td:
            result = run_agent_loop(
                worker=worker,
                chat_with_tools=chat,
                protocol="openai",
                emit=lambda t, m, p: events.append((t, m, p)),
                run_dir=Path(td),
                system_prompt="SYS",
                task_description="测试",
            )
        self.assertFalse(result.ok)  # v2.16 硬门控：无产物收尾不得判成功（本测试主体是流式事件）
        self.assertEqual(result.error_kind, "NO_GEOMETRY")
        text_events = [e for e in events if e[0] == "agent_text_delta"]
        # 流式两段 + 不再补发整轮（否则 "先建基准面" 出现两次）
        joined = "".join(str(e[1]) for e in text_events)
        self.assertEqual(joined, "先建基准面")

    def test_loop_falls_back_for_legacy_fake(self) -> None:
        import tempfile
        from pathlib import Path

        from tests.test_agent_loop import FakeWorker
        from backend.agent import run_agent_loop
        from backend.mechcad_ai.client import ToolCallRound

        events: list[tuple] = []
        worker = FakeWorker([])

        def chat(messages, tools):  # 旧签名：不支持 on_text_delta
            return ToolCallRound(text="完成", tool_calls=[])

        with tempfile.TemporaryDirectory() as td:
            result = run_agent_loop(
                worker=worker,
                chat_with_tools=chat,
                protocol="openai",
                emit=lambda t, m, p: events.append((t, m, p)),
                run_dir=Path(td),
                system_prompt="SYS",
                task_description="测试",
            )
        self.assertFalse(result.ok)  # v2.16 硬门控：无产物收尾不得判成功（本测试主体是流式事件）
        self.assertEqual(result.error_kind, "NO_GEOMETRY")
        text_events = [e for e in events if e[0] == "agent_text_delta"]
        self.assertEqual(len(text_events), 1)
        self.assertEqual(text_events[0][2].get("done"), True)


if __name__ == "__main__":
    unittest.main()
