"""v2.17 孔语义契约测试（审查测试 6：外部圆柱不能通过通孔契约）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.mechcad_ai.client import ToolCallRound

from tests.test_agent_loop import FakeWorker, _round_with_call, _run


def _through(d=9.0, x=15.0, y=10.0):
    return {"diameter_mm": d, "center": [x, y, 10.0], "axis": [0, 0, -1],
            "depth_mm": 10.0, "through": True, "kind": "through_hole"}


def _blind(d=8.0, x=0.0, y=0.0, depth=6.0):
    return {"diameter_mm": d, "center": [x, y, 10.0], "axis": [0, 0, -1],
            "depth_mm": depth, "through": False, "kind": "blind_hole"}


class HoleContractTests(unittest.TestCase):
    def _run_finish(self, worker, contract, extra_results=()):
        # 每次调用重置桩（上一轮 finish_part 会清空会话与 execute_results）
        worker.execute_results = [
            {"success": True, "value": 8000.0}, {"success": True, "value": 1},
            *extra_results,
        ]
        seen: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("finish_part", {
                    "part": "housing", "feature_contract": contract})
            seen.append(json.loads([m for m in messages if m.get("role") == "tool"][-1]["content"]))
            return ToolCallRound(text="结束", tool_calls=[])

        with tempfile.TemporaryDirectory() as td:
            _run(worker, chat, run_dir=Path(td))
        return seen[0]

    def test_typed_contract_passes_on_real_holes(self) -> None:
        worker = FakeWorker([
            {"success": True, "value": 8000.0}, {"success": True, "value": 1},
        ])
        worker.holes_result = {"success": True,
                               "value": {"holes": [_through(), _through(x=-15.0)], "count": 2}}
        payload = self._run_finish(worker, [{"type": "through_hole", "diameter_mm": 9, "count": 2}])
        self.assertTrue(payload["success"], payload)

    def test_boss_cannot_pass_through_hole_contract(self) -> None:
        """审查测试 6：只有外凸台（boss）时，通孔契约必须拒绝。"""
        worker = FakeWorker([
            {"success": True, "value": 8000.0}, {"success": True, "value": 1},
        ])
        # 内核分类器不把 boss 计为孔 → holes 为空
        worker.holes_result = {"success": True, "value": {"holes": [], "count": 0}}
        payload = self._run_finish(worker, [{"type": "through_hole", "diameter_mm": 9, "count": 1}])
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_kind"], "FEATURE_CONTRACT_MISMATCH")
        self.assertIn("through_hole", payload["error"])

    def test_blind_vs_through_are_distinguished(self) -> None:
        worker = FakeWorker([
            {"success": True, "value": 8000.0}, {"success": True, "value": 1},
        ])
        worker.holes_result = {"success": True, "value": {"holes": [_blind()], "count": 1}}
        # 断言通孔但实测是盲孔 → 拒
        payload = self._run_finish(worker, [{"type": "through_hole", "diameter_mm": 8, "count": 1}])
        self.assertFalse(payload["success"])
        # 断言盲孔 → 过
        payload = self._run_finish(worker, [{"type": "blind_hole", "diameter_mm": 8, "count": 1}])
        self.assertTrue(payload["success"], payload)

    def test_positions_must_match(self) -> None:
        worker = FakeWorker([
            {"success": True, "value": 8000.0}, {"success": True, "value": 1},
        ])
        worker.holes_result = {"success": True, "value": {"holes": [_through(x=15.0, y=10.0)], "count": 1}}
        payload = self._run_finish(worker, [{
            "type": "through_hole", "diameter_mm": 9, "count": 1,
            "positions": [[15.0, 10.0]]}])
        self.assertTrue(payload["success"], payload)
        payload = self._run_finish(worker, [{
            "type": "through_hole", "diameter_mm": 9, "count": 1,
            "positions": [[-15.0, 10.0]]}])
        self.assertFalse(payload["success"])
        self.assertIn("孔位", payload["error"])

    def test_legacy_radius_contract_still_works(self) -> None:
        worker = FakeWorker()
        payload = self._run_finish(
            worker, [{"radius_mm": 4.5, "count": 2}],
            extra_results=[{"success": True,
                            "value": {"selected": [{"radius_mm": 4.5}, {"radius_mm": 4.5}]}}])
        self.assertTrue(payload["success"], payload)


if __name__ == "__main__":
    unittest.main()
