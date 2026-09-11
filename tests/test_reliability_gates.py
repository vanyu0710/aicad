"""v2.16 可靠性硬门控测试（P0-1/P0-2/P0-4/P1-2）。

对应审查报告的"完成状态与几何验证绑定、工具结果结构化、几何更新指纹"。
复用 test_agent_loop 的 FakeWorker/FakeChat 桩，无网络无 kernel。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.agent.loop import _compact_step_result, _compact_value

from tests.test_agent_loop import FakeWorker, _round_with_call, _run
from backend.mechcad_ai.client import ToolCallRound


class CompletionGateTests(unittest.TestCase):
    """P0-1/P0-4：模型文字不再等于成功——必须有产物且验证通过。"""

    def test_final_text_without_geometry_cannot_succeed(self) -> None:
        """审查测试 1：无几何不能成功。"""
        worker = FakeWorker()
        chat = lambda *a, **k: ToolCallRound(text="完成", tool_calls=[])  # noqa: E731
        result = _run(worker, chat)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "NO_GEOMETRY")

    def test_invalid_geometry_forces_failure(self) -> None:
        """审查测试 2：strict 验证不过 → ok=false + GEOMETRY_INVALID。"""
        worker = FakeWorker([{"success": True, "geometry_summary": {"volume": 1000.0}}])
        worker.validation_result = {"success": True, "geometry_validation": {
            "valid": False, "status": "invalid", "reason_codes": ["INVALID_SHAPE"]}}
        calls = {"n": 0}

        def _chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("create_workplane", {"name": "base"})
            return ToolCallRound(text="完成", tool_calls=[])

        result = _run(worker, _chat)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "GEOMETRY_INVALID")

    def test_valid_geometry_single_part_succeeds(self) -> None:
        worker = FakeWorker([{"success": True, "geometry_summary": {"volume": 1000.0}}])
        calls = {"n": 0}

        def _chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("create_workplane", {"name": "base"})
            return ToolCallRound(text="完成", tool_calls=[])

        result = _run(worker, _chat)
        self.assertTrue(result.ok)
        self.assertIsNone(result.error_kind)

    def test_max_steps_sets_error_kind(self) -> None:
        """P0-4：撞上限显式 MAX_STEPS_REACHED，不得算成功。"""
        worker = FakeWorker()
        chat = lambda *a, **k: _round_with_call("create_workplane", {"name": "a"})  # noqa: E731
        result = _run(worker, chat, max_steps=2)
        self.assertFalse(result.ok)
        self.assertTrue(result.stopped)
        self.assertEqual(result.error_kind, "MAX_STEPS_REACHED")

    def test_report_reflects_gate(self) -> None:
        """报告 ok 字段与门控一致（污染面：UI/快照/评测）。"""
        worker = FakeWorker()
        chat = lambda *a, **k: ToolCallRound(text="完成", tool_calls=[])  # noqa: E731
        with tempfile.TemporaryDirectory() as td:
            result = _run(worker, chat, run_dir=Path(td))
            report = json.loads((Path(td) / "execution_report.json").read_text(encoding="utf-8"))
        self.assertFalse(result.ok)
        self.assertFalse(report["ok"])
        self.assertEqual(report.get("error_kind"), "NO_GEOMETRY")


class FinishPartStrictGateTests(unittest.TestCase):
    """P0-1：finish_part 必须过 strict 验证才归档。"""

    def test_finish_part_blocked_by_invalid_validation(self) -> None:
        worker = FakeWorker([
            {"success": True, "value": 8000.0},      # query volume
            {"success": True, "value": 1},           # query solid_count
        ])
        worker.validation_result = {"success": True, "geometry_validation": {
            "valid": False, "status": "invalid", "reason_codes": ["INVALID_TOPOLOGY"]}}
        seen: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("finish_part", {"part": "housing"})
            seen.append(json.loads([m for m in messages if m.get("role") == "tool"][-1]["content"]))
            return ToolCallRound(text="好吧", tool_calls=[])

        with tempfile.TemporaryDirectory() as td:
            _run(worker, chat, run_dir=Path(td), mode="auto")
        self.assertEqual(seen[0]["error_kind"], "GEOMETRY_INVALID")
        self.assertEqual(worker.reset_calls, 0)  # 未归档就不该清会话


class CompactResultTests(unittest.TestCase):
    """P0-2：工具结果结构化——不字符串化、不截断成非法 JSON、数组附 total。"""

    def _big_select(self) -> dict:
        return {"success": True,
                "geometry_validation": {"valid": True, "status": "valid", "reason_codes": []},
                "value": {"element_type": "edge", "revision": 12,
                          "selected": [{"ref": f"E{i:02d}", "radius_mm": 20.0} for i in range(200)]}}

    def test_value_stays_structured_json(self) -> None:
        compact = _compact_step_result(self._big_select())
        self.assertIsInstance(compact["value"], dict)
        self.assertIsInstance(compact["value"]["selected"], list)
        self.assertEqual(compact["value"]["selected"][0]["ref"], "E00")
        self.assertEqual(compact["value"]["selected_total"], 200)

    def test_geometry_validation_preserved(self) -> None:
        compact = _compact_step_result(self._big_select())
        self.assertEqual(compact["geometry_validation"]["status"], "valid")

    def test_render_not_forwarded_but_advertised(self) -> None:
        compact = _compact_step_result({"success": True, "render_base64": "AAAA"})
        self.assertNotIn("render_base64", compact)
        self.assertTrue(compact.get("render_available"))

    def test_leaf_string_truncated_without_breaking_json(self) -> None:
        value = {"note": "x" * 5000}
        compact = _compact_step_result({"success": True, "value": value})
        text = json.dumps(compact, ensure_ascii=False)
        json.loads(text)  # 必须仍是合法 JSON
        self.assertIn("截断", compact["value"]["note"])

    def test_compact_value_scalar_passthrough(self) -> None:
        self.assertEqual(_compact_value(42), 42)
        self.assertEqual(_compact_value(None), None)
        self.assertEqual(_compact_value([1, 2, 3]), [1, 2, 3])


class GeometryFingerprintTests(unittest.TestCase):
    """P1-2：体积相同但 bbox 变化必须重新导出（审查测试 5）。"""

    def test_same_volume_different_bbox_exports_twice(self) -> None:
        worker = FakeWorker()
        events: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("create_workplane", {"name": "base"})
            if calls["n"] == 2:
                return _round_with_call("delete_feature", {"feature_id": "F_0001"})
            return ToolCallRound(text="完成", tool_calls=[])

        # 两次 op 返回相同体积、不同 bbox
        worker.execute_results = [
            {"success": True, "geometry_summary": {"volume": 1000.0, "bounding_box": [0, 0, 0, 10, 10, 10]}},
            {"success": True, "geometry_summary": {"volume": 1000.0, "bounding_box": [0, 0, 0, 20, 5, 10]}},
        ]
        result = _run(worker, chat, emit=lambda ev, m, p: events.append((ev, m, p)))
        self.assertEqual(len(worker.exported_mesh), 2)
        self.assertEqual(len(worker.render_calls), 2)

    def test_identical_summary_does_not_reexport(self) -> None:
        worker = FakeWorker()
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("create_workplane", {"name": "base"})
            if calls["n"] == 2:
                return _round_with_call("create_workplane", {"name": "base2"})
            return ToolCallRound(text="完成", tool_calls=[])

        same = {"success": True, "geometry_summary": {"volume": 1000.0, "bounding_box": [0, 0, 0, 10, 10, 10]}}
        worker.execute_results = [dict(same), dict(same)]
        _run(worker, chat)
        self.assertEqual(len(worker.exported_mesh), 1)


if __name__ == "__main__":
    unittest.main()
