"""agent start/stop 端点测试（mock 线程执行体，不触真实 LLM / kernel worker）。"""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

# Isolated store（与 test_api.py 同模式，且必须在 import backend.main 之前设置）
os.environ["MECHCAD_STORE_PATH"] = str(Path(tempfile.mkdtemp(prefix="mechcad-agent-test-")) / "projects.json")

import backend.main as main_module
from fastapi.testclient import TestClient


class AgentEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main_module.app)

    def setUp(self) -> None:
        self.project_id = self.client.post("/api/projects", json={"name": "agent test"}).json()["project_id"]
        # 清理上一个用例遗留的运行状态
        with main_module._agent_lock:
            main_module._agent_runs.clear()

    def tearDown(self) -> None:
        with main_module._agent_lock:
            runs = list(main_module._agent_runs.items())
            main_module._agent_runs.clear()
        for _, run in runs:
            run["stop"].set()
            run["thread"].join(timeout=2)

    def _blocking_runner(self, release: threading.Event):
        """把 agent 线程执行体替换为阻塞函数，模拟运行中的 agent。"""

        def runner(project_id, request, settings, stop_event, loop, approvals=None):
            release.wait(timeout=5)

        return runner

    def test_start_and_conflict_and_stop(self) -> None:
        release = threading.Event()
        with patch.object(main_module, "_run_agent_thread", side_effect=self._blocking_runner(release)):
            started = self.client.post(
                f"/api/projects/{self.project_id}/agent/start",
                json={"description": "120x120x12 法兰，中心 Ø30 通孔"},
            )
            self.assertEqual(started.status_code, 200)
            self.assertTrue(started.json()["started"])

            # 运行中再次启动 → 409
            conflict = self.client.post(
                f"/api/projects/{self.project_id}/agent/start",
                json={"description": "再来一个"},
            )
            self.assertEqual(conflict.status_code, 409)

            # stop → 设置停止标志
            stopped = self.client.post(f"/api/projects/{self.project_id}/agent/stop")
            self.assertEqual(stopped.status_code, 200)
            self.assertTrue(stopped.json()["stopped"])
            release.set()

    def test_start_without_description_422(self) -> None:
        response = self.client.post(
            f"/api/projects/{self.project_id}/agent/start",
            json={"description": "   "},
        )
        self.assertEqual(response.status_code, 422)

    def test_start_unknown_project_404(self) -> None:
        response = self.client.post(
            "/api/projects/nonexistent/agent/start",
            json={"description": "abc"},
        )
        self.assertEqual(response.status_code, 404)

    def test_stop_without_run_returns_false(self) -> None:
        response = self.client.post(f"/api/projects/{self.project_id}/agent/stop")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["stopped"])

    def test_thread_runner_reports_missing_model(self) -> None:
        """未配置模型时线程应发 agent_done(ok=False) 而不是抛异常。"""
        events: list = []

        class FakeLoop:
            def call_soon_threadsafe(self, fn, *args):
                events.append(args)

        request = main_module.AgentStartRequest(description="测试", language="zh")
        settings = main_module._project_or_404(self.project_id).settings
        stop_event = threading.Event()
        approvals = main_module.ApprovalBroker(timeout=0.2)
        # 清空环境变量，确保 has_configured_model 为 False
        with patch.dict("os.environ", {
            "MECHCAD_PLANNER_API_KEY": "",
            "MECHCAD_PLANNER_BASE_URL": "",
            "MECHCAD_PLANNER_MODEL": "",
        }):
            main_module._run_agent_thread(self.project_id, request, settings, stop_event, FakeLoop(), approvals)
        self.assertTrue(events, "应发布 agent_done 事件")


class AgentResolveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main_module.app)

    def setUp(self) -> None:
        self.project_id = self.client.post("/api/projects", json={"name": "resolve test"}).json()["project_id"]
        with main_module._agent_lock:
            run = main_module._agent_runs.pop(self.project_id, None)
        if run is not None:
            run["stop"].set()

    def tearDown(self) -> None:
        with main_module._agent_lock:
            runs = list(main_module._agent_runs.items())
            main_module._agent_runs.clear()
        for _, run in runs:
            run["stop"].set()
            run["thread"].join(timeout=2)

    def _make_running_run(self) -> tuple[main_module.ApprovalBroker, str]:
        """造一个带 ApprovalBroker 的持久 run，并同步注册一个待审批请求。

        返回 (broker, approval_id)。请求用后台线程阻塞在 broker.request 上，
        测试结束时通过 resolve 或超时清理。
        """
        broker = main_module.ApprovalBroker(timeout=30)
        release = threading.Event()

        def blocking(project_id, request, settings, stop_event, loop, approvals=None):
            release.wait(timeout=30)

        with patch.object(main_module, "_run_agent_thread", side_effect=blocking):
            resp = self.client.post(f"/api/projects/{self.project_id}/agent/start",
                                    json={"description": "blocking"})
            self.assertEqual(resp.status_code, 200)

        # 后台线程模拟 agent 调用 broker.request 等待用户
        result_box: dict = {}

        def requester() -> None:
            result_box["decision"] = broker.request(
                kind="ask_user", op="ask_user", args={"question": "q"}, message="q", options={})

        t = threading.Thread(target=requester)
        t.start()
        with broker._lock:
            aid = next(iter(broker._requests))
        with main_module._agent_lock:
            run = main_module._agent_runs[self.project_id]
            run["approvals"] = broker
            run["_release"] = release
            run["_requester"] = t
            run["_result_box"] = result_box
        return broker, aid

    def _cleanup_run(self) -> None:
        with main_module._agent_lock:
            runs = list(main_module._agent_runs.items())
            main_module._agent_runs.clear()
        for _, run in runs:
            run["stop"].set()
            release = run.get("_release")
            if release is not None:
                release.set()
            requester = run.get("_requester")
            if requester is not None:
                requester.join(timeout=3)

    def test_resolve_approve(self) -> None:
        broker, aid = self._make_running_run()
        try:
            resp = self.client.post(f"/api/projects/{self.project_id}/agent/resolve",
                                    json={"approval_id": aid, "action": "approve"})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["resolved"]["action"], "approve")
        finally:
            self._cleanup_run()

    def test_resolve_edit_overrides(self) -> None:
        broker, aid = self._make_running_run()
        try:
            resp = self.client.post(f"/api/projects/{self.project_id}/agent/resolve",
                                    json={"approval_id": aid, "action": "edit", "args_override": {"answer": "8mm"}})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["resolved"]["args"], {"answer": "8mm"})
        finally:
            self._cleanup_run()

    def test_resolve_unknown_approval_404(self) -> None:
        # 需要一个 run 才能走到 404 分支（run 存在但 approval 不存在）
        broker, _ = self._make_running_run()
        try:
            resp = self.client.post(f"/api/projects/{self.project_id}/agent/resolve",
                                    json={"approval_id": "approval-nope", "action": "approve"})
            self.assertEqual(resp.status_code, 404)
        finally:
            self._cleanup_run()

    def test_resolve_no_running_agent_409(self) -> None:
        resp = self.client.post(f"/api/projects/{self.project_id}/agent/resolve",
                                json={"approval_id": "approval-x", "action": "approve"})
        self.assertEqual(resp.status_code, 409)

    def test_resolve_without_project_404(self) -> None:
        resp = self.client.post("/api/projects/nope/agent/resolve",
                                json={"approval_id": "approval-x", "action": "approve"})
        self.assertEqual(resp.status_code, 404)


class KernelEndpointsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main_module.app)

    def setUp(self) -> None:
        self.project_id = self.client.post("/api/projects", json={"name": "kernel test"}).json()["project_id"]
        with main_module._agent_lock:
            main_module._agent_runs.clear()

    def tearDown(self) -> None:
        with main_module._agent_lock:
            main_module._agent_runs.clear()
        main_module.get_worker_manager().stop_all()

    def test_feature_tree_without_worker_returns_empty(self) -> None:
        resp = self.client.get(f"/api/projects/{self.project_id}/kernel/feature_tree")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["node_count"], 0)

    def test_update_feature_without_worker_409(self) -> None:
        resp = self.client.post(f"/api/projects/{self.project_id}/kernel/update_feature",
                                json={"feature_id": "F_0001", "new_params": {"depth": 12}})
        self.assertEqual(resp.status_code, 409)

    def test_undo_without_worker_falls_back_to_legacy(self) -> None:
        # 无 kernel worker → 回退 legacy 快照 undo（200，因为 store.undo 无历史时可能抛 KeyError）
        # 这里只断言不会 404/409——store.undo 对无历史项目可能异常，用 mock 规避
        import backend.session as session_mod

        with patch.object(main_module, "_kernel_worker_or_none", return_value=None), \
             patch.object(session_mod.SessionStore, "undo", return_value=main_module.store.get_project(self.project_id)):
            resp = self.client.post(f"/api/projects/{self.project_id}/undo")
            self.assertEqual(resp.status_code, 200)

    def test_undo_with_worker_routes_to_kernel(self) -> None:
        # 存活 worker → 走 kernel undo + 提交快照
        class FakeWorker:
            def is_alive(self) -> bool:
                return True

            def undo(self, steps=1) -> dict:
                return {"success": True}

            def redo(self, steps=1) -> dict:
                return {"success": True}

            def feature_tree(self) -> dict:
                return {"graph": {"nodes": {}, "edges": {}}, "op_history": [], "narrative": []}

            def export_mesh(self, path) -> dict:
                return {"size": 0}

            def export_step(self, path) -> dict:
                return {"success": True}

        import backend.storage as storage_mod

        with patch.object(main_module, "_kernel_worker_or_none", return_value=FakeWorker()), \
             patch.object(storage_mod, "create_run_dir", return_value=("run1", Path(tempfile.mkdtemp()))):
            resp = self.client.post(f"/api/projects/{self.project_id}/undo")
            self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
