from __future__ import annotations

import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

# Use an isolated JSON store so API tests never touch work/projects.json.
os.environ["MECHCAD_STORE_PATH"] = str(Path(tempfile.mkdtemp(prefix="mechcad-test-")) / "projects.json")
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


def _fake_worker_ok(plan, timeout: int = 45, language: str = "zh", on_step=None):
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
        cls._chat_patch = patch.object(ai_module.ai_planner, "chat_edit_operations", return_value=None)
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

    def test_model_test_reports_missing_config_without_key_leak(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "MECHCAD_VISION_API_KEY": "",
                "MECHCAD_VISION_BASE_URL": "",
                "MECHCAD_VISION_MODEL": "",
                "MECHCAD_VISION_PROTOCOL": "",
            },
        ):
            response = self.client.post(
                "/api/model/test",
                json={
                    "role": "vision",
                    "config": {
                        "vision_provider": "custom",
                        "vision_protocol": "openai",
                        "vision_base_url": "",
                        "vision_model": "",
                        "vision_api_key": "",
                        "planner_provider": "custom",
                        "planner_protocol": "openai",
                        "planner_base_url": "",
                        "planner_model": "",
                        "planner_api_key": "",
                        "operation_mode": "strict",
                        "smart_fill_policy": "suggest_only",
                    },
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual(body["role"], "vision")
        self.assertIn("配置不完整", body["message"])
        self.assertTrue(body["diagnostics"]["used_env_fallback"])

    def test_model_test_english_message_has_no_chinese(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "MECHCAD_VISION_API_KEY": "",
                "MECHCAD_VISION_BASE_URL": "",
                "MECHCAD_VISION_MODEL": "",
                "MECHCAD_VISION_PROTOCOL": "",
            },
        ):
            response = self.client.post(
                "/api/model/test",
                json={
                    "role": "vision",
                    "language": "en",
                    "config": {
                        "vision_provider": "custom",
                        "vision_protocol": "openai",
                        "vision_base_url": "",
                        "vision_model": "",
                        "vision_api_key": "",
                        "planner_provider": "custom",
                        "planner_protocol": "openai",
                        "planner_base_url": "",
                        "planner_model": "",
                        "planner_api_key": "",
                        "operation_mode": "strict",
                        "smart_fill_policy": "suggest_only",
                    },
                },
            )
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertFalse(any("\u4e00" <= char <= "\u9fff" for char in body["message"]), body["message"])

    def test_project_response_masks_api_keys(self) -> None:
        project_id = self._create_project()
        secret = "sk-real-secret"
        payload = {
            "description": "60mm x 30mm x 4mm 的平板",
            "model_config": {
                "vision_provider": "custom",
                "vision_protocol": "openai",
                "vision_base_url": "https://api.example.test/v1",
                "vision_model": "vision-test",
                "vision_api_key": secret,
                "planner_provider": "custom",
                "planner_protocol": "openai",
                "planner_base_url": "https://api.example.test/v1",
                "planner_model": "planner-test",
                "planner_api_key": secret,
                "operation_mode": "strict",
                "smart_fill_policy": "suggest_only",
            },
        }
        with patch.object(main_module, "run_freecad_worker", side_effect=_fake_worker_ok):
            response = self.client.post(f"/api/projects/{project_id}/generate", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        settings = response.json()["settings"]
        self.assertEqual(settings["vision_api_key"], "***configured***")
        self.assertEqual(settings["planner_api_key"], "***configured***")
        self.assertNotIn(secret, response.text)

    def test_project_settings_can_be_saved_without_running_cad(self) -> None:
        project_id = self._create_project()
        secret = "sk-settings-secret"
        payload = {
            "vision_provider": "custom",
            "vision_protocol": "openai",
            "vision_base_url": "https://vision.example.test/v1",
            "vision_model": "vision-test",
            "vision_api_key": secret,
            "planner_provider": "custom",
            "planner_protocol": "anthropic",
            "planner_base_url": "https://planner.example.test",
            "planner_model": "planner-test",
            "planner_api_key": secret,
            "operation_mode": "smart",
            "smart_fill_policy": "limited_fill",
            "force_real_api": True,
        }
        response = self.client.patch(f"/api/projects/{project_id}/settings", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["settings"]["operation_mode"], "smart")
        self.assertEqual(response.json()["settings"]["smart_fill_policy"], "limited_fill")
        self.assertEqual(response.json()["settings"]["vision_api_key"], "***configured***")
        self.assertEqual(main_module.store.get_project(project_id).settings.vision_api_key, secret)

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

    def test_english_generate_returns_english_report_and_questions(self) -> None:
        project_id = self._create_project()
        with patch.object(main_module, "run_freecad_worker", side_effect=_fake_worker_ok):
            response = self.client.post(
                f"/api/projects/{project_id}/generate",
                json={
                    "description": "60mm x 30mm x 4mm plate with two 6mm through holes",
                    "language": "en",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        current = body["current"]
        combined = (
            current["report_markdown"]
            + "\n".join(current["logs"])
            + "\n".join(question["text"] for question in current["questions"])
        )
        self.assertFalse(any("\u4e00" <= char <= "\u9fff" for char in combined), combined)

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
        # Worker feature steps must survive ingestion into the snapshot timeline.
        process = response.json()["current"]["process"]
        cad_feature_steps = [step for step in process if step["stage"] == "cad" and step.get("feature_id")]
        self.assertTrue(cad_feature_steps)
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


    def test_list_projects_masks_api_keys(self) -> None:
        project_id = self._create_project()
        main_module.store.get_project(project_id).settings.vision_api_key = "sk-secret"
        main_module.store.get_project(project_id).settings.planner_api_key = "sk-secret"
        response = self.client.get("/api/projects")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreaterEqual(len(body["projects"]), 1)
        listed = next(item for item in body["projects"] if item["project_id"] == project_id)
        self.assertEqual(listed["settings"]["vision_api_key"], "***configured***")
        self.assertEqual(listed["settings"]["planner_api_key"], "***configured***")

    def test_delete_project(self) -> None:
        project_id = self._create_project()
        response = self.client.delete(f"/api/projects/{project_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f"/api/projects/{project_id}").status_code, 404)
        ids = [item["project_id"] for item in self.client.get("/api/projects").json()["projects"]]
        self.assertNotIn(project_id, ids)

    def test_rename_project(self) -> None:
        project_id = self._create_project()
        response = self.client.patch(f"/api/projects/{project_id}", json={"name": "Renamed API"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["name"], "Renamed API")
        fetched = self.client.get(f"/api/projects/{project_id}").json()
        self.assertEqual(fetched["name"], "Renamed API")

    def test_rename_project_rejects_blank_name(self) -> None:
        project_id = self._create_project()
        response = self.client.patch(f"/api/projects/{project_id}", json={"name": "   "})
        self.assertEqual(response.status_code, 422)

    def test_rename_missing_project_is_404(self) -> None:
        response = self.client.patch("/api/projects/does-not-exist", json={"name": "Nope"})
        self.assertEqual(response.status_code, 404)

    def test_settings_update_preserves_configured_api_keys_when_blank(self) -> None:
        project_id = self._create_project()
        project = main_module.store.get_project(project_id)
        project.settings.vision_api_key = "sk-vision-keep"
        project.settings.planner_api_key = "sk-planner-keep"
        payload = {
            "vision_provider": "custom",
            "vision_protocol": "openai",
            "vision_base_url": "https://vision.example.test/v1",
            "vision_model": "vision-test",
            "vision_api_key": "",
            "planner_provider": "custom",
            "planner_protocol": "openai",
            "planner_base_url": "https://planner.example.test/v1",
            "planner_model": "planner-test",
            "planner_api_key": "***configured***",
            "operation_mode": "smart",
            "smart_fill_policy": "limited_fill",
        }
        response = self.client.patch(f"/api/projects/{project_id}/settings", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        stored = main_module.store.get_project(project_id).settings
        self.assertEqual(stored.vision_api_key, "sk-vision-keep")
        self.assertEqual(stored.planner_api_key, "sk-planner-keep")


    def test_generate_records_full_process_timeline(self) -> None:
        project_id = self._create_project()
        with patch.object(main_module, "run_freecad_worker", side_effect=_fake_worker_ok):
            response = self.client.post(
                f"/api/projects/{project_id}/generate",
                json={"description": "60mm x 30mm x 4mm plate"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        process = response.json()["current"]["process"]
        stages = [step["stage"] for step in process]
        for expected in ("upload", "vision", "planning", "validation", "cad", "export"):
            self.assertIn(expected, stages)
        positions = [stages.index(expected) for expected in ("upload", "vision", "planning", "validation", "cad", "export")]
        self.assertEqual(positions, sorted(positions))

    def test_chat_edit_records_feature_level_process_step(self) -> None:
        project_id = self._create_project()
        with patch.object(main_module, "run_freecad_worker", side_effect=_fake_worker_ok):
            self.client.post(
                f"/api/projects/{project_id}/generate",
                json={"description": "60mm x 30mm x 4mm plate"},
            )
            response = self.client.post(f"/api/projects/{project_id}/chat", json={"message": "thickness=8mm"})
        self.assertEqual(response.status_code, 200, response.text)
        process = response.json()["current"]["process"]
        chat_steps = [step for step in process if step["stage"] == "chat_edit"]
        self.assertTrue(chat_steps)
        completed = [step for step in chat_steps if step["status"] == "completed"]
        self.assertTrue(completed)
        changed = completed[0]["changed"]
        self.assertIsNotNone(changed)
        self.assertEqual(changed["after"]["dimensions"]["height"]["value"], 8.0)


    def test_patch_feature_records_update_process_step(self) -> None:
        project_id = self._create_project()
        with patch.object(main_module, "run_freecad_worker", side_effect=_fake_worker_ok):
            generated = self.client.post(
                f"/api/projects/{project_id}/generate",
                json={"description": "60mm x 30mm x 4mm plate"},
            ).json()
            feature_id = generated["current"]["feature_plan"]["base_feature"]["id"]
            response = self.client.patch(
                f"/api/projects/{project_id}/features/{feature_id}",
                json={"dimensions": {"length": {"value": 25, "unit": "mm", "confirmed_by_user": True}}},
            )
        self.assertEqual(response.status_code, 200, response.text)
        process = response.json()["current"]["process"]
        edits = [step for step in process if step["stage"] == "chat_edit" and step["operation"] == "update"]
        self.assertTrue(edits)
        self.assertEqual(edits[0]["status"], "completed")
        self.assertEqual(edits[0]["changed"]["after"]["dimensions"]["length"]["value"], 25.0)

    def _create_project(self) -> str:
        response = self.client.post("/api/projects", json={"name": "api-test"})
        return response.json()["project_id"]


if __name__ == "__main__":
    unittest.main()
