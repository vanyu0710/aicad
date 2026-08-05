from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.cad import run_freecad_worker
from backend.schemas import FeaturePlanV3

OUTPUT_ROOT = Path("work") / "new_arch_runs"


def _plan() -> FeaturePlanV3:
    return FeaturePlanV3.model_validate(
        {
            "part_family": "plate",
            "base_feature": {
                "id": "base_plate",
                "type": "box_base",
                "operation": "base",
                "dimensions": {
                    "length": {"value": 60, "unit": "mm", "confirmed_by_user": True},
                    "width": {"value": 30, "unit": "mm", "confirmed_by_user": True},
                    "height": {"value": 4, "unit": "mm", "confirmed_by_user": True},
                },
            },
        }
    )


class CADWorkerTests(unittest.TestCase):
    def test_timeout_reports_failure_without_raising(self) -> None:
        timeout = subprocess.TimeoutExpired(cmd="worker", timeout=1, output=b"", stderr=b"")
        with patch("backend.cad.subprocess.run", side_effect=timeout):
            artifacts, logs, ok = run_freecad_worker(_plan(), timeout=1)
        self.assertFalse(ok)
        self.assertTrue(any("timeout" in line.lower() for line in logs), logs)
        report = Path(artifacts.execution_report).read_text(encoding="utf-8")
        self.assertIn("timeout", report.lower())
        # No model artifacts should exist for a failed run.
        self.assertIsNone(artifacts.step)
        self.assertIsNone(artifacts.stl)

    def test_nonzero_exit_reports_stderr_in_logs(self) -> None:
        completed = subprocess.CompletedProcess(args=["worker"], returncode=1, stdout="", stderr="boom: bad plan")
        with patch("backend.cad.subprocess.run", return_value=completed):
            artifacts, logs, ok = run_freecad_worker(_plan())
        self.assertFalse(ok)
        self.assertTrue(any("boom: bad plan" in line for line in logs), logs)

    def test_real_worker_success(self) -> None:
        artifacts, logs, ok = run_freecad_worker(_plan())
        self.assertTrue(ok, logs)
        self.assertEqual(Path(artifacts.step).suffix, ".step")
        self.assertEqual(Path(artifacts.stl).suffix, ".stl")
        self.assertEqual(Path(artifacts.obj).suffix, ".obj")

    def test_real_worker_rejects_plan_without_base(self) -> None:
        plan = FeaturePlanV3(part_family="unknown")
        artifacts, logs, ok = run_freecad_worker(plan)
        self.assertFalse(ok)
        self.assertTrue(artifacts.execution_report is not None)
        report = Path(artifacts.execution_report).read_text(encoding="utf-8")
        self.assertIn("no base_feature", report.lower())


if __name__ == "__main__":
    unittest.main()
