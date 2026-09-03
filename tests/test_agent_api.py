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

        def runner(project_id, request, settings, stop_event, loop):
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
        # 清空环境变量，确保 has_configured_model 为 False
        with patch.dict("os.environ", {
            "MECHCAD_PLANNER_API_KEY": "",
            "MECHCAD_PLANNER_BASE_URL": "",
            "MECHCAD_PLANNER_MODEL": "",
        }):
            main_module._run_agent_thread(self.project_id, request, settings, stop_event, FakeLoop())
        self.assertTrue(events, "应发布 agent_done 事件")


if __name__ == "__main__":
    unittest.main()
