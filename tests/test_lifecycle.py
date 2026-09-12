"""v2.17 P1-7/P1-8：零件库生命周期与装配分级测试（审查测试 7）。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.mechcad_ai.client import ToolCallRound

from tests.test_agent_loop import FakeWorker, _round_with_call, _run


def _seed_lib(storage_root: Path, project: str, names: list[str]):
    from backend import storage
    storage.PROJECT_PARTS_ROOT = storage_root
    lib = storage_root / project
    lib.mkdir(parents=True, exist_ok=True)
    parts = []
    for n in names:
        (lib / f"v001_{n}.step").write_text("ISO-10303-21;", encoding="utf-8")
        parts.append({"name": n, "version": 1, "step_file": f"v001_{n}.step",
                      "stl_file": f"v001_{n}.stl", "pose": {"position": [0, 0, 0]},
                      "status": "active"})
    storage.write_manifest(project, {"parts": parts, "assembly": None})
    return lib


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        from backend import storage
        self._orig_root = storage.PROJECT_PARTS_ROOT

    def tearDown(self) -> None:
        from backend import storage
        storage.PROJECT_PARTS_ROOT = self._orig_root

    def _export_run(self, worker, names_in_plan, extra=None):
        from backend import storage
        from backend.agent.session import AgentSession

        seen: list = []
        calls = {"n": 0}

        def chat(messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return _round_with_call("export_assembly", extra or {"note": "交付"})
            tool_msgs = [m for m in messages if m.get("role") == "tool"]
            seen.append(json.loads(tool_msgs[-1]["content"]))
            return ToolCallRound(text="结束", tool_calls=[])

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "project_parts"
            _seed_lib(root, "proj", ["a", "b", "old_c"])
            storage.PROJECT_PARTS_ROOT = root
            session = AgentSession(project_id="p", path=Path(td) / "s.json")
            bom = [{"part": n, "key_params": {"d": "8"}} for n in names_in_plan]
            session.set_plan("两件", [], approved=True, bom=bom)
            result = _run(worker, chat, run_dir=Path(td), session=session,
                          project_id="proj", mode="plan")
            manifest_after = storage.read_manifest("proj")
        return seen[0], result, manifest_after

    def test_superseded_part_excluded_from_assembly(self) -> None:
        """审查测试 7：BOM 外历史残留件（old_c）不得进装配。"""
        worker = FakeWorker()
        worker.interfere_pair = False  # 本测试聚焦排除，干涉另测
        payload, result, manifest_after = self._export_run(worker, ["a", "b"])
        self.assertTrue(payload["success"], payload)
        self.assertEqual(payload["parts_count"], 2)
        self.assertEqual(payload["excluded_superseded"], ["old_c"])
        # export 只收到 2 件
        self.assertIn(("export", 2), worker.assembly_calls)
        # manifest 里 old_c 被标 superseded
        by_name = {p["name"]: p for p in manifest_after["parts"]}
        self.assertEqual(by_name["old_c"].get("status"), "superseded")
        self.assertEqual(by_name["a"].get("status"), "active")

    def test_unexempted_interference_blocks_export(self) -> None:
        """P1-8：未豁免硬碰撞 → 阻断，不产出装配 STEP。"""
        worker = FakeWorker()
        payload, result, manifest_after = self._export_run(worker, ["a", "b"])
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_kind"], "INTERFERENCE_BLOCKED")
        self.assertGreaterEqual(len(payload["hard_collisions"]), 1)
        self.assertNotIn("export", [c[0] for c in worker.assembly_calls])
        self.assertIsNone(result.assembly)

    def test_mesh_exemption_passes_and_categorized(self) -> None:
        worker = FakeWorker()
        payload, result, _ = self._export_run(worker, ["a", "b"], extra={
            "note": "交付",
            "expected_overlaps": [{"a": "a", "b": "b", "max_volume_mm3": 100,
                                   "category": "mesh", "reason": "啮合"}]})
        self.assertTrue(payload["success"], payload)
        self.assertEqual(payload["expected_mesh_count"], 1)
        self.assertEqual(payload["hard_collision_count"], 0)

    def test_bom_parts_missing_blocks_export(self) -> None:
        worker = FakeWorker()
        payload, _, _ = self._export_run(worker, ["a", "b", "c"])
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error_kind"], "BOM_PARTS_MISSING")


if __name__ == "__main__":
    unittest.main()
