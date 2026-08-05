from __future__ import annotations

import base64
import io
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import backend.main as main_module
from backend.schemas import ArtifactSet

# Deterministic tests: force the local stub planner instead of the real model
# chain (which reads MECHCAD_* env vars from .env).
from backend import ai as ai_module

OK_ARTIFACTS = ArtifactSet(
    run_id="testrun",
    step="/tmp/model.step",
    stl="/tmp/model.stl",
    obj="/tmp/model.obj",
    report="/tmp/report.md",
    execution_report="/tmp/execution_report.json",
)


def _fake_worker_ok(plan, timeout: int = 45):
    return OK_ARTIFACTS, [f"worker ok for {plan.part_family}"], True


def _tiny_png_data_url() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 200, 200)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class MechCADApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(main_module.app)
        # Never call the real vision/planner models during API tests.
        cls._vision_patch = patch.object(ai_module.ai_vision, "analyze_sketch", return_value=None)
        cls._planner_patch = patch.object(ai_module.ai_planner, "generate_feature_plan", return_value=None)
        cls._chat_patch = patch.object(ai_module.ai_planner, "chat_edit_feature_plan", return_value=None)
        cls._vision_patch.start()
        cls._planner_patch.start()
        cls._chat_patch.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._vision_patch.stop()
        cls._planner_patch.stop()
        cls._chat_patch.stop()

    def test_health(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_create_and_get_project(self) -> None:
        created = self.client.post("/api/projects", json={"name": "API test"}).json()
        project_id = created["project_id"]
        fetched = self.client.get(f"/api/projects/{project_id}")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["name"], "API test")

    def test_get_missing_project_is_404(self) -> None:
        response = self.client.get("/api/projects/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_generate_commits_snapshot(self) -> None:
        project_id = self._create_project()
        with patch.object(main_module, "run_freecad_worker", side_effect=_fake_worker_ok):
            response = self.client.post(
                f"/api/projects/{project_id}/generate",
                json={"description": "80mm x 40mm x 5mm 的平板，两个直径 6mm 通孔"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        project = response.json()
        plan = project["current"]["feature_plan"]
        self.assertEqual(plan["part_family"], "plate")
        self.assertIsNotNone(plan["base_feature"])
        # The empty initial snapshot is preserved in history.
        self.assertEqual(len(project["history"]), 1)
        self.assertEqual(len(project["redo_stack"]), 0)

    def test_generate_with_image_and_config(self) -> None:
        project_id = self._create_project()
        payload = {
            "description": "法兰盘",
            "image_data_url": _tiny_png_data_url(),
            "image_name": "sketch.png",
            "model_config": {
                "operation_mode": "strict",
                "smart_fill_policy": "suggest_only",
            },
        }
        with patch.object(main_module, "run_freecad_worker", side_effect=_fake_worker_ok):
            response = self.client.post(f"/api/projects/{project_id}/generate", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        settings = response.json()["settings"]
        self.assertEqual(settings["operation_mode"], "strict")

    def test_chat_edit_creates_new_snapshot(self) -> None:
        project_id = self._create_project()
        with patch.object(main_module, "run_freecad_worker", side_effect=_fake_worker_ok):
            self.client.post(
                f"/api/projects/{project_id}/generate",
                json={"description": "60mm x 30mm x 4mm 的平板"},
            )
            response = self.client.post(f"/api/projects/{project_id}/chat", json={"message": "厚度=8mm"})
        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()["current"]["feature_plan"]
        height = plan["base_feature"]["dimensions"]["height"]["value"]
        self.assertEqual(height, 8.0)
        self.assertEqual(len(response.json()["history"]), 2)

    def test_patch_feature_marks_dimension_user_confirmed(self) -> None:
        project_id = self._create_project()
        with patch.object(main_module, "run_freecad_worker", side_effect=_fake_worker_ok):
            generated = self.client.post(
                f"/api/projects/{project_id}/generate",
                json={"description": "直径 50mm 厚 10mm 的轴套"},
            ).json()
            feature_id = generated["current"]["feature_plan"]["base_feature"]["id"]
            response = self.client.patch(
                f"/api/projects/{project_id}/features/{feature_id}",
                json={
                    "dimensions": {"length": {"value": 25, "unit": "mm", "confirmed_by_user": True}},
                    "confirmed_by_user": True,
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        dim = response.json()["current"]["feature_plan"]["base_feature"]["dimensions"]["length"]
        self.assertEqual(dim["value"], 25.0)
        self.assertTrue(dim["confirmed_by_user"])
        self.assertEqual(len(response.json()["history"]), 2)

    def test_patch_missing_feature_is_graceful(self) -> None:
        project_id = self._create_project()
        with patch.object(main_module, "run_freecad_worker", side_effect=_fake_worker_ok):
            self.client.post(
                f"/api/projects/{project_id}/generate",
                json={"description": "60mm x 30mm x 4mm 的平板"},
            )
            response = self.client.patch(
                f"/api/projects/{project_id}/features/no-such-feature",
                json={"confirmed_by_user": True},
            )
        self.assertEqual(response.status_code, 200, response.text)

    def test_undo_redo_round_trip(self) -> None:
        project_id = self._create_project()
        with patch.object(main_module, "run_freecad_worker", side_effect=_fake_worker_ok):
            self.client.post(f"/api/projects/{project_id}/generate", json={"description": "平板 60x30x4"})
            self.client.post(f"/api/projects/{project_id}/generate", json={"description": "轴套 直径50 长20"})

        after_two = self.client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(after_two["current"]["feature_plan"]["part_family"], "tube")

        undone = self.client.post(f"/api/projects/{project_id}/undo").json()
        self.assertEqual(undone["current"]["feature_plan"]["part_family"], "plate")
        self.assertEqual(len(undone["redo_stack"]), 1)

        redone = self.client.post(f"/api/projects/{project_id}/redo").json()
        self.assertEqual(redone["current"]["feature_plan"]["part_family"], "tube")
        self.assertEqual(len(redone["redo_stack"]), 0)

    def test_undo_on_fresh_project_is_noop(self) -> None:
        project_id = self._create_project()
        response = self.client.post(f"/api/projects/{project_id}/undo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["history"]), 0)

    def test_artifact_unknown_run_and_kind_are_404(self) -> None:
        self.assertEqual(self.client.get("/api/artifacts/nope/step").status_code, 404)
        self.assertEqual(self.client.get("/api/artifacts/abc/unknown_kind").status_code, 404)

    def test_generate_real_worker_creates_artifacts(self) -> None:
        """End-to-end through the real build123d worker subprocess."""
        project_id = self._create_project()
        response = self.client.post(
            f"/api/projects/{project_id}/generate",
            json={"description": "直径 40mm 长 8mm 内径 20mm 的轴套"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        artifacts = response.json()["current"]["artifacts"]
        self.assertIsNotNone(artifacts["step"])
        self.assertIsNotNone(artifacts["stl"])
        self.assertIsNotNone(artifacts["obj"])
        self.assertIsNotNone(artifacts["execution_report"])
        # The exported artifact must be downloadable.
        run_id = artifacts["run_id"]
        download = self.client.get(f"/api/artifacts/{run_id}/step")
        self.assertEqual(download.status_code, 200)

    def test_websocket_accepts_connection(self) -> None:
        """The WS route accepts connections; event plumbing is covered by
        test_events.py (TestClient runs WS and HTTP on separate loops, so a
        full in-process event bridge test would deadlock)."""
        project_id = self._create_project()
        with self.client.websocket_connect(f"/ws/projects/{project_id}"):
            pass

    def _create_project(self) -> str:
        response = self.client.post("/api/projects", json={"name": "api-test"})
        return response.json()["project_id"]


if __name__ == "__main__":
    unittest.main()
