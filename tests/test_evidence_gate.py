from __future__ import annotations

import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("MECHCAD_STORE_PATH", str(Path(tempfile.mkdtemp(prefix="mechcad-evidence-test-")) / "projects.json"))

from fastapi.testclient import TestClient

import backend.main as main_module
from backend import ai as ai_module
from backend.evidence_gate import apply_evidence_gate_result, apply_evidence_resolutions, evaluate_evidence_gate
from backend.generic_engine import build_evidence_set
from backend.schemas import (
    ArtifactSet,
    DesignSnapshot,
    DimensionV3,
    EvidenceResolution,
    FeaturePlanV3,
    FeatureV3,
    PlacementV3,
)
from backend.session import SessionStore


def _dim(value: float, source: str = "drawing", confirmed: bool = False) -> DimensionV3:
    return DimensionV3(value=value, unit="mm", source=source, confirmed_by_user=confirmed)


def _material_evidence():
    return build_evidence_set(
        "plate 60x30x4 with hole diameter 6",
        {"dimensions": [{"name": "hole_diameter", "value": 8, "feature_id": "hole_01", "dimension": "diameter"}]},
        True,
    )


def _plan(evidence=None, hole_diameter: float = 8.0) -> FeaturePlanV3:
    return FeaturePlanV3(
        part_family="plate",
        evidence=evidence or _material_evidence(),
        base_feature=FeatureV3(
            id="base_plate",
            type="box_base",
            operation="base",
            dimensions={
                "length": _dim(60, "user", True),
                "width": _dim(30, "user", True),
                "height": _dim(4, "user", True),
            },
            placement=PlacementV3(reference="origin"),
        ),
        features=[
            FeatureV3(
                id="hole_01",
                type="through_hole",
                operation="remove",
                dimensions={"diameter": _dim(hole_diameter)},
                placement=PlacementV3(reference="center"),
                depends_on=["base_plate"],
            )
        ],
    )


class EvidenceGateTests(unittest.TestCase):
    def test_no_conflict_allow(self):
        evidence = build_evidence_set("plate 60x30x4 with hole diameter 8", None, False)
        plan = _plan(evidence, 8.0)
        before = deepcopy(plan)
        result = evaluate_evidence_gate(plan, "strict")
        self.assertEqual(result.status, "ALLOW")
        self.assertFalse(result.blocking)
        self.assertEqual(plan, before)

    def test_non_material_conflict_allow_with_warning(self):
        evidence = build_evidence_set(
            "plate 60x30x4",
            {"dimensions": [{"name": "annotation_99", "value": 1}, {"name": "annotation_99", "value": 3}]},
            True,
        )
        plan = _plan(evidence, 8.0)
        result = evaluate_evidence_gate(plan, "strict")
        self.assertEqual(result.status, "ALLOW")
        self.assertFalse(result.blocking)
        self.assertTrue(result.warnings)
        self.assertEqual(result.conflicts[0].severity, "warning")

    def test_material_conflict_blocks_strict_and_smart(self):
        plan = _plan()
        strict = evaluate_evidence_gate(plan, "strict")
        smart = evaluate_evidence_gate(plan, "smart")
        self.assertEqual(strict.status, "BLOCK")
        self.assertTrue(strict.blocking)
        self.assertEqual(strict.affected_feature_ids, ["hole_01"])
        self.assertEqual(strict.affected_parameters, ["diameter"])
        self.assertEqual(smart.status, "REQUIRE_RESOLUTION")
        self.assertTrue(smart.blocking)
        self.assertTrue(smart.resolution_required)

    def test_explicit_resolution_allows_and_preserves_originals(self):
        plan = _plan()
        resolved = apply_evidence_resolutions(
            plan,
            [EvidenceResolution(key="hole_diameter", selected_value=6)],
            "zh",
        )
        result = evaluate_evidence_gate(resolved, "strict")
        self.assertEqual(result.status, "ALLOW")
        self.assertFalse(result.blocking)
        self.assertEqual(resolved.features[0].dimensions["diameter"].value, 6.0)
        self.assertEqual(resolved.features[0].dimensions["diameter"].source, "user")
        self.assertTrue(resolved.features[0].dimensions["diameter"].confirmed_by_user)
        self.assertEqual(len(resolved.evidence.items), 2)
        self.assertEqual(resolved.evidence.items[0].value, 6.0)
        self.assertEqual(resolved.evidence.items[1].value, 8.0)
        self.assertEqual(resolved.evidence.conflict_details[0].status, "resolved")
        self.assertEqual(resolved.evidence.conflict_details[0].resolved_value, 6.0)

    def test_resolution_survives_snapshot_undo_redo(self):
        store = SessionStore()
        project = store.create_project("evidence-snapshot")
        unresolved = _plan()
        resolved = apply_evidence_resolutions(
            unresolved,
            [EvidenceResolution(key="hole_diameter", selected_value=6)],
            "zh",
        )
        store.commit_snapshot(project.project_id, DesignSnapshot(feature_plan=unresolved))
        store.commit_snapshot(project.project_id, DesignSnapshot(feature_plan=resolved))
        store.undo(project.project_id)
        undone = store.get_project(project.project_id).current.feature_plan
        self.assertEqual(len(undone.evidence.items), 2)
        self.assertEqual(undone.evidence.conflict_details[0].status, "unresolved")
        self.assertEqual(undone.features[0].dimensions["diameter"].value, 8.0)
        store.redo(project.project_id)
        redone = store.get_project(project.project_id).current.feature_plan
        self.assertEqual(redone.evidence.conflict_details[0].status, "resolved")
        self.assertEqual(redone.features[0].dimensions["diameter"].value, 6.0)
        self.assertEqual(redone.evidence.items[0].value, 6.0)
        self.assertEqual(redone.evidence.items[1].value, 8.0)

    def test_unrelated_conflict_does_not_block_hole(self):
        evidence = build_evidence_set(
            "plate 60x30x4 with hole diameter 8",
            {
                "dimensions": [
                    {"name": "hole_diameter", "value": 8, "feature_id": "hole_01", "dimension": "diameter"},
                    {"name": "annotation_99", "value": 7},
                    {"name": "annotation_99", "value": 9},
                ]
            },
            True,
        )
        plan = _plan(evidence, 8.0)
        result = evaluate_evidence_gate(plan, "strict")
        self.assertEqual(result.status, "ALLOW")
        self.assertFalse(result.blocking)

    def test_apply_gate_result_is_explicit_and_idempotent(self):
        plan = _plan()
        result = evaluate_evidence_gate(plan, "strict")
        apply_evidence_gate_result(plan, result, "strict")
        first_checks = deepcopy(plan.self_checks["evidence_gate"])
        apply_evidence_gate_result(plan, result, "strict")
        self.assertEqual(plan.self_checks["evidence_gate"], first_checks)
        self.assertEqual(len([x for x in plan.design_review.blocking if x.startswith("[evidence]")]), 1)

    def test_gate_is_pure_and_repeatable(self):
        plan = _plan()
        before = deepcopy(plan)
        first = evaluate_evidence_gate(plan, "strict")
        second = evaluate_evidence_gate(plan, "strict")
        self.assertEqual(plan, before)
        self.assertEqual(first, second)

    def test_clarification_fallback_resolves_conflict(self):
        from backend.ai import apply_clarification_answers

        plan = _plan()
        resolved = apply_clarification_answers(plan, "\u5b54\u5f84=6", language="zh")
        result = evaluate_evidence_gate(resolved, "strict")
        self.assertEqual(result.status, "ALLOW")
        self.assertEqual(resolved.features[0].dimensions["diameter"].value, 6.0)

    def test_conflict_question_is_generated(self):
        from backend.ai import questions_from_plan

        plan = _plan()
        questions = questions_from_plan(plan, "zh")
        self.assertTrue(any(question.id.startswith("evidence_") for question in questions))


class EvidenceGateApiTests(unittest.TestCase):
    def setUp(self):
        self._old_store = main_module.store
        main_module.store = SessionStore()
        self.client = TestClient(main_module.app)

    def tearDown(self):
        main_module.store = self._old_store

    def test_blocked_conflict_never_invokes_cad_worker(self):
        plan = _plan()
        worker = MagicMock(return_value=(None, ["unexpected cad"], True))
        with patch.object(main_module, "build_initial_feature_plan", return_value=(plan, [])), patch.object(
            main_module, "run_freecad_worker", worker
        ):
            project_id = self.client.post("/api/projects", json={"name": "gate"}).json()["project_id"]
            response = self.client.post(
                f"/api/projects/{project_id}/generate",
                json={"description": "plate 60x30x4", "operation_mode": "strict"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["current"]["execution_report"]["execution_ok"])
        worker.assert_not_called()
        plan = response.json()["current"]["feature_plan"]
        self.assertEqual(plan["self_checks"]["evidence_gate"]["status"], "BLOCK")

    def test_resolution_via_generate_request_allows_cad(self):
        plan = _plan()
        worker = MagicMock(
            return_value=(
                ArtifactSet(
                    run_id="run1",
                    step="/tmp/model.step",
                    stl="/tmp/model.stl",
                    obj="/tmp/model.obj",
                    report="/tmp/model.md",
                    execution_report="/tmp/execution.json",
                ),
                ["worker ok"],
                True,
            )
        )
        with patch.object(main_module, "build_initial_feature_plan", return_value=(plan, [])), patch.object(
            main_module, "run_freecad_worker", worker
        ):
            project_id = self.client.post("/api/projects", json={"name": "resolved"}).json()["project_id"]
            response = self.client.post(
                f"/api/projects/{project_id}/generate",
                json={
                    "description": "plate 60x30x4",
                    "operation_mode": "strict",
                    "evidence_resolutions": [{"key": "hole_diameter", "selected_value": 6}],
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["current"]["execution_report"]["execution_ok"])
        worker.assert_called_once()


if __name__ == "__main__":
    unittest.main()
