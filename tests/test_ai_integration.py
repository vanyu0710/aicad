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
from backend.ai import build_initial_feature_plan
from backend.schemas import FeaturePlanV3, GenerateRequest, ModelConfig

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

    def test_missing_base_dimensions_go_to_unresolved(self):
        normalized = normalize_ai_plan(
            {
                "base_feature": {
                    "id": "base_plate",
                    "type": "box_base",
                    "dimensions": {"length": 80},
                },
                "features": [],
            }
        )
        reasons = {item["feature"] for item in normalized["unresolved"]}
        self.assertIn("base_plate", reasons)


class StubQualityTests(unittest.TestCase):
    def test_tube_top_groove_becomes_candidate_with_targeted_question(self):
        plan, questions = build_initial_feature_plan(
            "tube outer diameter 50 inner diameter 38 length 300 top groove slot width 10",
            GenerateRequest(description="tube outer diameter 50 inner diameter 38 length 300 top groove slot width 10"),
        )
        self.assertEqual(plan.part_family, "tube")
        self.assertEqual(plan.base_feature.dimensions["outer_diameter"].value, 50.0)
        self.assertEqual(plan.base_feature.dimensions["inner_diameter"].value, 38.0)
        self.assertEqual(plan.base_feature.dimensions["length"].value, 300.0)
        self.assertEqual(plan.features[0].type, "annular_groove")
        self.assertEqual(plan.features[0].dimensions["axial_width"].value, 10.0)
        self.assertEqual(plan.features[0].dimensions["z_start"].value, 290.0)
        self.assertTrue(any(question.feature_id == "top_groove" for question in questions))

    def test_smart_mode_autonomously_completes_a_tube_concept(self):
        settings = ModelConfig(operation_mode="smart", smart_fill_policy="limited_fill")
        request = GenerateRequest(
            description="一根顶部有环槽的管件",
            operation_mode="smart",
            smart_fill_policy="limited_fill",
        )
        plan, questions = build_initial_feature_plan(request.description, request, settings=settings)
        self.assertEqual(plan.base_feature.type, "hollow_cylinder")
        self.assertIsNotNone(plan.base_feature.dimensions["outer_diameter"].value)
        self.assertIsNotNone(plan.base_feature.dimensions["inner_diameter"].value)
        self.assertIsNotNone(plan.base_feature.dimensions["length"].value)
        self.assertEqual(plan.features[0].type, "annular_groove")
        self.assertTrue(all(d.value is not None for d in plan.features[0].dimensions.values()))
        self.assertTrue(plan.self_checks["smart_autonomy"]["executed"])
        self.assertTrue(any("智能模式工程假设" in item for item in plan.assumptions + plan.features[0].assumptions))


    def test_full_autonomous_mode_adds_explainable_support_feature(self):
        settings = ModelConfig(operation_mode="smart", smart_fill_policy="full_autonomous")
        request = GenerateRequest(
            description="support bracket for mounting, no final dimensions provided",
            operation_mode="smart",
            smart_fill_policy="full_autonomous",
        )
        plan, _ = build_initial_feature_plan(request.description, request, settings=settings)
        self.assertEqual(plan.autonomy_policy, "full_autonomous")
        self.assertTrue(plan.design_intent)
        rib = next(feature for feature in plan.features if feature.id == "autonomous_support_rib")
        self.assertEqual(rib.type, "rib_box")
        self.assertEqual(rib.depends_on, [plan.base_feature.id])
        self.assertTrue(any(item.feature_id == rib.id for item in plan.assumption_details))


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


class BilingualTests(unittest.TestCase):
    @staticmethod
    def _has_cjk(value: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in value)

    def test_english_prompt_variant_loads(self):
        text = get_prompt("feature_planning", "en")
        self.assertIn("FeaturePlanV3", text)
        self.assertNotIn("\u4e00", text)

    def test_english_stub_plan_and_questions_have_no_chinese(self):
        request = GenerateRequest(
            description="tube outer diameter 50 inner diameter 38 length 300 top groove slot width 10",
            language="en",
        )
        plan, questions = build_initial_feature_plan(request.description, request, language="en")
        plan_text = (
            " ".join(plan.assumptions)
            + " "
            + " ".join(plan.design_review.warnings + plan.design_review.suggestions)
            + " "
            + " ".join(item["reason"] for item in plan.unresolved)
        )
        self.assertFalse(self._has_cjk(plan_text), plan_text)
        for question in questions:
            combined = " ".join(
                [question.text, question.impact or "", question.reason or "", *question.options]
            )
            self.assertFalse(self._has_cjk(combined), combined)


if __name__ == "__main__":
    unittest.main()
