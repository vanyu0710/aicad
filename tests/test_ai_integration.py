from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from backend.mechcad_ai.client import (
    ApiCallError,
    chat_completion,
    has_configured_model,
    parse_json_object,
    resolve_role_config,
    strip_json_fence,
)
from backend.mechcad_ai.normalize import normalize_ai_plan
from backend.mechcad_ai.prompts import get_prompt, load_prompts
from backend.schemas import FeaturePlanV3, ModelConfig

PLAN_JSON = {
    "schema_version": "3.0",
    "part_family": {"category": "flange_plate"},
    "base_feature": {
        "id": "BF1",
        "type": "cylinder_base",
        "operation": "base",
        "dimensions": {"diameter": 60, "thickness": 10},
        "evidence": [{"value": "60mm"}],
        "confirmed_by_user": True,
    },
    "features": [
        {
            "id": "F1",
            "type": "through_hole",
            "operation": "cut",
            "dimensions": {"diameter": 20},
            "placement": {"center": {"x": 0, "y": 0}, "axis": "Z"},
        },
        {
            "id": "F2",
            "type": "circular_pattern",
            "operation": "pattern",
            "dimensions": {"instance_count": 4},
            "placement": {"pitch_circle_diameter": 45, "axis": "Z"},
        },
    ],
    "design_review": {"requires_confirmation": ["M6 hole"], "warnings": "thin wall"},
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        if self.path.endswith("/v1/messages"):
            payload = {"content": [{"type": "text", "text": '{"ok": true, "source": "anthropic-mock"}'}]}
        else:
            payload = {"choices": [{"message": {"content": json.dumps(PLAN_JSON)}}]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class _MockServer:
    def __init__(self):
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        self.server.shutdown()


class AIClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mock = _MockServer()

    @classmethod
    def tearDownClass(cls):
        cls.mock.close()

    def test_openai_protocol(self):
        settings = ModelConfig(
            vision_api_key="k",
            vision_base_url=self.mock.url("/v1"),
            vision_model="mock-vl",
            vision_protocol="openai",
        )
        content = chat_completion(settings, "vision", [{"role": "user", "content": "hi"}], response_json=True)
        self.assertIn("flange_plate", content)

    def test_anthropic_protocol(self):
        settings = ModelConfig(
            planner_api_key="k",
            planner_base_url=self.mock.url(""),
            planner_model="mock-claude",
            planner_protocol="anthropic",
        )
        content = chat_completion(
            settings, "planner", [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        )
        parsed = parse_json_object(content)
        self.assertEqual(parsed["source"], "anthropic-mock")

    def test_unconfigured_returns_false(self):
        # Hermetic: .env / ambient MECHCAD_* vars must not leak into this test.
        env_keys = [
            "MECHCAD_VISION_API_KEY",
            "MECHCAD_VISION_BASE_URL",
            "MECHCAD_VISION_MODEL",
            "MECHCAD_VISION_PROTOCOL",
            "MECHCAD_PLANNER_API_KEY",
            "MECHCAD_PLANNER_BASE_URL",
            "MECHCAD_PLANNER_MODEL",
            "MECHCAD_PLANNER_PROTOCOL",
        ]
        with patch.dict(os.environ, {key: "" for key in env_keys}):
            self.assertFalse(has_configured_model(ModelConfig(), "vision"))
            self.assertFalse(has_configured_model(ModelConfig(), "planner"))

    def test_v1_retry_url_resolution(self):
        settings = ModelConfig(
            vision_api_key="k", vision_base_url=self.mock.url(""), vision_model="m", vision_protocol="openai"
        )
        config = resolve_role_config(settings, "vision")
        # The client appends /v1 only when calling chat_completion; config keeps
        # the raw base url but protocol is resolved to openai.
        self.assertEqual(config["protocol"], "openai")
        # A non-/v1 base url still succeeds via the retry path inside chat_completion.
        content = chat_completion(settings, "vision", [{"role": "user", "content": "hi"}], response_json=True)
        self.assertIn("flange_plate", content)

    def test_strip_json_fence(self):
        self.assertEqual(strip_json_fence("```json\n{\"a\": 1}\n```"), "{\"a\": 1}")
        self.assertEqual(strip_json_fence('{"a": 1}'), '{"a": 1}')


class NormalizeTests(unittest.TestCase):
    def test_normalize_freeform_plan(self):
        normalized = normalize_ai_plan(PLAN_JSON)
        plan = FeaturePlanV3.model_validate(normalized)
        self.assertEqual(plan.part_family, "flange_plate")
        self.assertEqual(plan.base_feature.id, "BF1")
        self.assertEqual(plan.base_feature.type, "cylinder_base")
        self.assertEqual(plan.base_feature.dimensions["outer_diameter"].value, 60.0)
        self.assertEqual(plan.base_feature.dimensions["length"].value, 10.0)
        self.assertEqual(plan.base_feature.operation, "base")
        self.assertEqual(len(plan.features), 2)
        self.assertEqual(plan.features[0].type, "through_hole")
        self.assertEqual(plan.features[0].dimensions["diameter"].value, 20.0)
        self.assertEqual(plan.features[1].type, "circular_pattern")
        self.assertEqual(plan.features[1].dimensions["count"].value, 4.0)
        self.assertEqual(plan.features[1].dimensions["pitch_radius"].value, 22.5)
        self.assertTrue(plan.design_review.requires_confirmation)

    def test_missing_required_dimensions_go_to_unresolved(self):
        normalized = normalize_ai_plan(PLAN_JSON)
        # circular_pattern lacks a seed hole diameter -> must surface as unresolved
        reasons = {item["feature"] for item in normalized["unresolved"]}
        self.assertIn("F2", reasons)

    def test_unsupported_feature_dropped(self):
        raw = {
            "base_feature": {"type": "box_base", "dimensions": {"length": 10, "width": 10, "height": 10}},
            "features": [{"id": "X1", "type": "helical_gear", "dimensions": {"module": 2}}],
        }
        normalized = normalize_ai_plan(raw)
        plan = FeaturePlanV3.model_validate(normalized)
        self.assertEqual(len(plan.features), 0)

    def test_plan_without_base_is_invalid(self):
        normalized = normalize_ai_plan({"features": []})
        plan = FeaturePlanV3.model_validate(normalized)
        self.assertIsNone(plan.base_feature)


class PromptTests(unittest.TestCase):
    def test_prompts_loaded(self):
        prompts = load_prompts()
        self.assertIn("vision_analysis", prompts)
        self.assertIn("feature_planning", prompts)
        self.assertIn("chat_edit", prompts)
        self.assertIn("design_review", prompts)

    def test_get_prompt_text(self):
        text = get_prompt("feature_planning")
        self.assertIn("FeaturePlanV3", text)


if __name__ == "__main__":
    unittest.main()
