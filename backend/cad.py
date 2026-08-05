from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from backend.schemas import ArtifactSet, FeaturePlanV3
from backend.storage import create_run_dir


def run_freecad_worker(plan: FeaturePlanV3, timeout: int = 45) -> tuple[ArtifactSet, list[str], bool]:
    run_id, run_dir = create_run_dir()
    plan_path = run_dir / "feature_plan.json"
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    worker = Path(__file__).resolve().parents[1] / "cad_worker" / "freecad_executor.py"
    command = [sys.executable, str(worker), "--plan", str(plan_path), "--out", str(run_dir)]
    logs: list[str] = [f"Starting FreeCAD worker for run {run_id}."]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_output(exc.stdout)
        stderr = _decode_output(exc.stderr)
        report = {"ok": False, "error": f"FreeCAD worker timeout after {timeout}s", "stdout": stdout, "stderr": stderr}
        (run_dir / "execution_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return _artifacts(run_id, run_dir), logs + [report["error"]], False

    if completed.stdout:
        logs.append(completed.stdout.strip())
    if completed.stderr:
        logs.append(completed.stderr.strip())
    ok = completed.returncode == 0 and (run_dir / "execution_report.json").exists()
    return _artifacts(run_id, run_dir), logs, ok


def _decode_output(value: bytes | str | None) -> str:
    """TimeoutExpired carries bytes even when text=True; coerce to str for JSON."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _artifacts(run_id: str, run_dir: Path) -> ArtifactSet:
    def maybe(name: str) -> str | None:
        path = run_dir / name
        return str(path) if path.exists() else None

    return ArtifactSet(
        run_id=run_id,
        step=maybe("model.step"),
        stl=maybe("model.stl"),
        obj=maybe("model.obj"),
        report=maybe("report.md"),
        execution_report=maybe("execution_report.json"),
    )
