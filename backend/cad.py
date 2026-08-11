from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from backend.schemas import ArtifactSet, FeaturePlanV3
from backend.storage import create_run_dir


def run_freecad_worker(plan: FeaturePlanV3, timeout: int | None = None, language: str = "zh") -> tuple[ArtifactSet, list[str], bool]:
    if timeout is None:
        timeout = int(os.getenv("MECHCAD_CAD_TIMEOUT", "90"))
    run_id, run_dir = create_run_dir()
    plan_path = run_dir / "feature_plan.json"
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    worker = Path(__file__).resolve().parents[1] / "cad_worker" / "freecad_executor.py"
    command = [sys.executable, str(worker), "--plan", str(plan_path), "--out", str(run_dir), "--lang", language]
    engine = os.getenv("MECHCAD_CAD_ENGINE", "build123d").strip().lower() or "build123d"
    logs: list[str] = [f"Starting controlled {engine} CAD worker for run {run_id} (timeout={timeout}s)."]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_output(exc.stdout)
        stderr = _decode_output(exc.stderr)
        report = {
            "ok": False,
            "engine": engine,
            "error": f"{engine} worker timeout after {timeout}s",
            "stdout": stdout,
            "stderr": stderr,
        }
        (run_dir / "execution_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _remove_failed_model_artifacts(run_dir)
        return _artifacts(run_id, run_dir), logs + [report["error"]], False

    if completed.stdout:
        logs.append(completed.stdout.strip())
    if completed.stderr:
        logs.append(completed.stderr.strip())
    report = _read_execution_report(run_dir, language)
    ok = completed.returncode == 0 and bool(report.get("ok"))
    if report:
        if report.get("error"):
            logs.append(f"CAD error: {report['error']}")
        for item in report.get("skipped_features", []):
            if isinstance(item, dict):
                logs.append(f"Skipped feature {item.get('feature', 'unknown')}: {item.get('reason', 'unspecified')}")
        if report.get("warnings"):
            logs.extend(f"CAD warning: {warning}" for warning in report["warnings"])
    elif completed.returncode != 0:
        logs.append(f"CAD worker exited with code {completed.returncode} without an execution report.")
    if not ok:
        _remove_failed_model_artifacts(run_dir)
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


def _read_execution_report(run_dir: Path, language: str = "zh") -> dict:
    report_path = run_dir / "execution_report.json"
    if not report_path.exists():
        return {}
    try:
        value = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"Cannot read CAD execution report: {exc}" if language == "en" else f"无法读取 CAD 执行报告：{exc}"
        return {"ok": False, "error": msg}
    if isinstance(value, dict):
        return value
    msg = "CAD execution report is not a JSON object" if language == "en" else "CAD 执行报告不是 JSON 对象"
    return {"ok": False, "error": msg}


def _remove_failed_model_artifacts(run_dir: Path) -> None:
    """Prevent incomplete geometry from being exposed as a successful run."""
    for name in ("model.step", "model.stl", "model.obj"):
        path = run_dir / name
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
