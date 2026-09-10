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

        def runner(project_id, text, image_data_url, language, max_steps, settings, stop_event, loop=None, approvals=None, session=None, mode=None):
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

        settings = main_module._project_or_404(self.project_id).settings
        stop_event = threading.Event()
        approvals = main_module.ApprovalBroker(timeout=0.2)
        # 清空环境变量，确保 has_configured_model 为 False
        with patch.dict("os.environ", {
            "MECHCAD_PLANNER_API_KEY": "",
            "MECHCAD_PLANNER_BASE_URL": "",
            "MECHCAD_PLANNER_MODEL": "",
        }):
            main_module._run_agent_thread(
                self.project_id, "测试", None, "zh", 30,
                settings, stop_event, FakeLoop(), approvals, session=None,
            )
        self.assertTrue(events, "应发布 agent_done 事件")


class AgentMessageSessionTests(unittest.TestCase):
    """v0.10 对话式会话端点：/agent/message、/agent/session、/agent/session/clear。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main_module.app)

    def setUp(self) -> None:
        # 会话注册表指向临时目录，避免污染仓库 work/
        self._registry_patch = patch.object(
            main_module, "_agent_sessions",
            main_module.SessionRegistry(Path(tempfile.mkdtemp(prefix="mechcad-sess-test-"))),
        )
        self._registry_patch.start()
        self.project_id = self.client.post("/api/projects", json={"name": "msg test"}).json()["project_id"]
        with main_module._agent_lock:
            main_module._agent_runs.clear()

    def tearDown(self) -> None:
        with main_module._agent_lock:
            runs = list(main_module._agent_runs.items())
            main_module._agent_runs.clear()
        for _, run in runs:
            run["stop"].set()
            run["thread"].join(timeout=2)
        self._registry_patch.stop()

    def _start_blocking_agent(self, release: threading.Event) -> None:
        with patch.object(main_module, "_run_agent_thread", side_effect=self._blocking_runner_pub(release)):
            resp = self.client.post(
                f"/api/projects/{self.project_id}/agent/message",
                json={"text": "做一个法兰"},
            )
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["started"])

    def _blocking_runner_pub(self, release: threading.Event):
        def runner(project_id, text, image_data_url, language, max_steps, settings, stop_event, loop=None, approvals=None, session=None, mode=None):
            release.wait(timeout=5)

        return runner

    def test_message_starts_when_idle_and_queues_when_running(self) -> None:
        release = threading.Event()
        try:
            self._start_blocking_agent(release)
            # 运行中再发消息 → 排队而非 409
            queued = self.client.post(
                f"/api/projects/{self.project_id}/agent/message",
                json={"text": "把孔改成 12mm"},
            )
            self.assertEqual(queued.status_code, 200)
            self.assertTrue(queued.json()["queued"])
            # 插话进入会话 pending 队列
            session = main_module._get_session(self.project_id)
            pending = session.drain_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]["role"], "user")
            self.assertIn("12mm", str(pending[0]["content"]))
        finally:
            release.set()

    def test_message_without_text_422(self) -> None:
        resp = self.client.post(f"/api/projects/{self.project_id}/agent/message", json={"text": "  "})
        self.assertEqual(resp.status_code, 422)

    def test_session_view_and_clear(self) -> None:
        session = main_module._get_session(self.project_id)
        session.append({"role": "user", "content": "做一个方块"})
        session.append({"role": "assistant", "content": "好的，开始建模。"})
        view = self.client.get(f"/api/projects/{self.project_id}/agent/session")
        self.assertEqual(view.status_code, 200)
        data = view.json()
        self.assertEqual(data["status"], "idle")
        self.assertEqual(len(data["messages"]), 2)
        self.assertEqual(data["messages"][0]["role"], "user")

        cleared = self.client.post(f"/api/projects/{self.project_id}/agent/session/clear")
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(main_module._get_session(self.project_id).llm_messages(), [])

    def test_session_clear_conflict_when_running(self) -> None:
        release = threading.Event()
        try:
            self._start_blocking_agent(release)
            resp = self.client.post(f"/api/projects/{self.project_id}/agent/session/clear")
            self.assertEqual(resp.status_code, 409)
        finally:
            release.set()

    def test_session_view_strips_injected_context(self) -> None:
        session = main_module._get_session(self.project_id)
        session.append({"role": "user", "content": "任务：做一个法兰\n当前没有特征（全新零件）。\n可用 op：create_workplane, extrude"})
        view = self.client.get(f"/api/projects/{self.project_id}/agent/session")
        messages = view.json()["messages"]
        self.assertEqual(messages[0]["text"], "任务：做一个法兰")
        self.assertNotIn("可用 op", messages[0]["text"])

    def test_session_view_hides_tool_messages_and_marks_image(self) -> None:
        session = main_module._get_session(self.project_id)
        session.append({"role": "user", "content": [
            {"type": "text", "text": "按这张图做"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]})
        session.append({"role": "assistant", "content": None})
        session.append({"role": "tool", "tool_call_id": "call-1", "content": "{}"})
        view = self.client.get(f"/api/projects/{self.project_id}/agent/session")
        messages = view.json()["messages"]
        # tool 消息与空文字 assistant 轮不进展示视图；user 消息带图片标记
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")
        self.assertTrue(messages[0]["has_image"])
        self.assertIn("按这张图做", messages[0]["text"])


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

        def blocking(project_id, text, image_data_url, language, max_steps, settings, stop_event, loop=None, approvals=None, session=None, mode=None):
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


class AutoPlanModeTests(unittest.TestCase):
    """v0.12：多零件/机构任务从 /agent/message 自动升级到 plan 模式。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main_module.app)

    def setUp(self) -> None:
        self._registry_patch = patch.object(
            main_module, "_agent_sessions",
            main_module.SessionRegistry(Path(tempfile.mkdtemp(prefix="mechcad-sess-test-"))),
        )
        self._registry_patch.start()
        self.project_id = self.client.post("/api/projects", json={"name": "auto plan"}).json()["project_id"]
        with main_module._agent_lock:
            main_module._agent_runs.clear()

    def tearDown(self) -> None:
        with main_module._agent_lock:
            runs = list(main_module._agent_runs.items())
            main_module._agent_runs.clear()
        for _, run in runs:
            run["stop"].set()
            run["thread"].join(timeout=2)
        self._registry_patch.stop()

    def _message_capturing_mode(self, text: str, explicit_mode: str | None = None) -> dict:
        captured: dict = {}

        def fake_thread(project_id, text_, image_data_url, language, max_steps, settings, stop_event,
                        loop=None, approvals=None, session=None, mode=None):
            captured["mode"] = mode

        payload = {"text": text}
        if explicit_mode:
            payload["mode"] = explicit_mode
        with patch.object(main_module, "_run_agent_thread", side_effect=fake_thread):
            resp = self.client.post(f"/api/projects/{self.project_id}/agent/message", json=payload)
        self.assertEqual(resp.status_code, 200)
        captured["response"] = resp.json()
        return captured

    def test_gearbox_text_upgrades_to_plan(self) -> None:
        for text in ("给我设计个 1:100 的变速箱", "设计一台两级减速器", "design a 2-stage gearbox"):
            captured = self._message_capturing_mode(text)
            self.assertEqual(captured["mode"], "plan", text)
            self.assertEqual(captured["response"]["mode"], "plan")

    def test_simple_part_stays_auto(self) -> None:
        captured = self._message_capturing_mode("做一块 120×120×12 的法兰，中心 Ø30 通孔")
        self.assertEqual(captured["mode"], "auto")

    def test_explicit_plan_respected_and_no_downgrade(self) -> None:
        captured = self._message_capturing_mode("做一块法兰板", explicit_mode="plan")
        self.assertEqual(captured["mode"], "plan")  # 用户显式 plan：不降级

    def test_heuristic_helper_directly(self) -> None:
        self.assertTrue(main_module._looks_multipart_task("装配体传动方案"))
        self.assertFalse(main_module._looks_multipart_task("改个倒角"))


class PartArtifactContractTests(unittest.TestCase):
    """v0.12：ArtifactSet.parts 契约 + agent_done payload 透传 + artifact kind 解析。"""

    def test_artifact_set_parts_serialization(self) -> None:
        from backend.schemas import ArtifactSet, PartArtifact

        parts = [
            PartArtifact(part="小齿轮", index=1, step="/abs/part_01_小齿轮.step",
                         stl="/abs/part_01_小齿轮.stl", step_file="part_01_小齿轮.step",
                         stl_file="part_01_小齿轮.stl", volume_mm3=15787.2, note="involute"),
            PartArtifact(part="箱体", index=2),
        ]
        artifacts = ArtifactSet(run_id="r1", parts=parts)
        dumped = artifacts.model_dump()
        self.assertEqual(len(dumped["parts"]), 2)
        self.assertEqual(dumped["parts"][0]["part"], "小齿轮")
        self.assertAlmostEqual(dumped["parts"][0]["volume_mm3"], 15787.2)
        self.assertIsNone(dumped["parts"][1]["step"])

    def test_storage_part_kind_path_and_traversal_guard(self) -> None:
        from backend.storage import artifact_path

        # 正常零件 kind 能解析（文件名即 kind）
        p = artifact_path("run1", "part_02_箱体.step")
        self.assertTrue(str(p).endswith("part_02_箱体.step"))
        q = artifact_path("run1", "part_02_箱体.stl")
        self.assertTrue(str(q).endswith(".stl"))
        # 目录穿越与非法后缀必须拒绝
        for bad in ("part_02_../../secret.step", "part_02_box.exe", "part_2_box.step", "model.step2"):
            with self.assertRaises(KeyError, msg=bad):
                artifact_path("run1", bad)

    def test_thread_runner_passes_parts_to_artifact_set(self) -> None:
        from backend.agent.loop import AgentLoopResult

        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            result = AgentLoopResult(ok=True, steps=5, final_text="完成")
            result.parts = [{"part": "g1", "index": 1, "step": str(run_dir / "part_01_g1.step"),
                             "stl": str(run_dir / "part_01_g1.stl"), "step_file": "part_01_g1.step",
                             "stl_file": "part_01_g1.stl", "volume_mm3": 1.5, "stl_size": 42}]
            result.artifacts["execution_report"] = str(run_dir / "execution_report.json")
            artifacts = main_module._result_artifact_set("runX", result)
            dumped = artifacts.model_dump()
            self.assertEqual(dumped["run_id"], "runX")
            self.assertEqual([p["part"] for p in dumped["parts"]], ["g1"])
            self.assertEqual(dumped["parts"][0]["step_file"], "part_01_g1.step")
            self.assertEqual(dumped["execution_report"], str(run_dir / "execution_report.json"))


class AssemblyApiTests(unittest.TestCase):
    """v0.14 F2a：零件库 REST + 快照 carry-over（手动改参不丢 parts）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main_module.app)

    def setUp(self) -> None:
        self.project_id = self.client.post("/api/projects", json={"name": "asm test"}).json()["project_id"]

    def test_manifest_endpoint_and_traversal_guard(self) -> None:
        resp = self.client.get(f"/api/projects/{self.project_id}/assembly/manifest")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["parts"], [])
        # 库文件下载：未知文件名 → 404（穿越防护）
        for bad in ("..%2F..%2Fetc", "secret.exe", "assembly_x.step"):
            r = self.client.get(f"/api/projects/{self.project_id}/assembly/artifacts/{bad}")
            self.assertEqual(r.status_code, 404, bad)

    def test_commit_snapshot_carries_parts_and_assembly(self) -> None:
        from backend.schemas import ArtifactSet, AssemblySummary, DesignSnapshot, PartArtifact
        from backend.session import SessionStore

        store = main_module.store
        snapshot = DesignSnapshot(artifacts=ArtifactSet(
            run_id="r1",
            parts=[PartArtifact(part="齿轮1", index=1, step_file="part_01.step")],
            assembly=AssemblySummary(parts_count=1),
        ))
        store.commit_snapshot(self.project_id, snapshot)

        class FakeWorker:
            def is_alive(self): return True
            def undo(self, steps=1): return {"success": True}
            def redo(self, steps=1): return {"success": True}
            def feature_tree(self): return {"graph": {"nodes": {}, "edges": {}}, "op_history": [], "narrative": []}
            def export_mesh(self, path): return {"size": 0}
            def export_step(self, path): return {"success": True}

        import backend.storage as storage_mod
        with patch.object(main_module, "_kernel_worker_or_none", return_value=FakeWorker()), \
             patch.object(storage_mod, "create_run_dir", return_value=("run2", Path(tempfile.mkdtemp()))):
            resp = self.client.post(f"/api/projects/{self.project_id}/undo")
            self.assertEqual(resp.status_code, 200)
        current = store.get_project(self.project_id).current.artifacts
        self.assertEqual([p.part for p in current.parts], ["齿轮1"])  # carry-over
        self.assertIsNotNone(current.assembly)


if __name__ == "__main__":
    unittest.main()
