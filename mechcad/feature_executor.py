from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import json
import subprocess
import sys

from backend.schemas import FeaturePlanV3
from mechcad.feature_plan import dimension_evidence, dimension_value, validate_feature_plan


@dataclass(frozen=True)
class SandboxResult:
    ok: bool
    stdout: str
    stderr: str
    step_path: str | None
    stl_path: str | None


@dataclass(frozen=True)
class FeatureExecutionResult:
    ok: bool
    code: str
    sandbox: SandboxResult
    modeled_features: list[str] = field(default_factory=list)
    skipped_features: list[dict[str, str]] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)


def execute_feature_plan(raw_plan: dict, run_dir: Path, timeout: int) -> FeatureExecutionResult:
    plan, errors = validate_feature_plan(raw_plan)
    if plan is None:
        code = _failure_code(errors)
        return FeatureExecutionResult(
            ok=False,
            code=code,
            sandbox=SandboxResult(False, "", "FeaturePlan validation failed: " + "; ".join(errors), None, None),
            validation_errors=errors,
        )

    code, modeled, skipped = build_feature_plan_code(plan)
    plan_path = run_dir / "feature_plan.json"
    plan_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    worker = Path(__file__).resolve().parents[1] / "cad_worker" / "freecad_executor.py"
    command = [sys.executable, str(worker), "--plan", str(plan_path), "--out", str(run_dir)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=run_dir)
        report_path = run_dir / "execution_report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            sandbox = SandboxResult(
                ok=bool(report.get("ok")),
                stdout=completed.stdout,
                stderr=completed.stderr,
                step_path=str(run_dir / "model.step") if (run_dir / "model.step").exists() else None,
                stl_path=str(run_dir / "model.stl") if (run_dir / "model.stl").exists() else None,
            )
            return FeatureExecutionResult(
                ok=sandbox.ok,
                code=code,
                sandbox=sandbox,
                modeled_features=list(report.get("modeled_features", modeled)),
                skipped_features=list(report.get("skipped_features", skipped)),
                validation_errors=[],
            )
        sandbox = SandboxResult(completed.returncode == 0, completed.stdout, completed.stderr, None, None)
        return FeatureExecutionResult(
            ok=sandbox.ok,
            code=code,
            sandbox=sandbox,
            modeled_features=modeled,
            skipped_features=skipped,
            validation_errors=[],
        )
    except subprocess.TimeoutExpired as exc:
        sandbox = SandboxResult(False, exc.stdout or "", exc.stderr or f"Worker timeout after {timeout}s", None, None)
        return FeatureExecutionResult(ok=False, code=code, sandbox=sandbox, modeled_features=modeled, skipped_features=skipped, validation_errors=[])


def build_feature_plan_code(plan: FeaturePlan | dict) -> tuple[str, list[str], list[dict[str, str]]]:
    if isinstance(plan, dict):
        validated, errors = validate_feature_plan(plan)
        if validated is None:
            return _failure_code(errors), [], [{"feature": "FeaturePlan", "reason": "; ".join(errors)}]
        plan = validated

    modeled: list[str] = []
    skipped: list[dict[str, str]] = []
    lines = ["from build123d import *", "", "with BuildPart() as part:"]
    if plan.base_feature is None:
        lines.append('    raise RuntimeError("FeaturePlan has no base_feature")')
        return "\n".join(lines + _export_lines()), modeled, skipped

    base = plan.base_feature
    if base.type == "hollow_cylinder":
        outer = dimension_value(base.dimensions, "outer_diameter")
        inner = dimension_value(base.dimensions, "inner_diameter")
        length = dimension_value(base.dimensions, "length")
        if outer is None or inner is None or length is None:
            return _failure_code(["Missing tube base dimensions"]), modeled, [{"feature": base.id, "reason": "Missing tube base dimensions"}]
        lines.append(f"    Cylinder(radius={outer / 2:g}, height={length:g}, align=(Align.CENTER, Align.CENTER, Align.MIN))")
        lines.append(f"    Cylinder(radius={inner / 2:g}, height={length + 2:g}, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)")
        modeled.append(base.id)
    elif base.type == "box_base":
        length = dimension_value(base.dimensions, "length")
        width = dimension_value(base.dimensions, "width")
        height = dimension_value(base.dimensions, "height")
        if length is None or width is None or height is None:
            return _failure_code(["Missing plate base dimensions"]), modeled, [{"feature": base.id, "reason": "Missing plate base dimensions"}]
        lines.append(f"    Box({length:g}, {width:g}, {height:g}, align=(Align.CENTER, Align.CENTER, Align.MIN))")
        modeled.append(base.id)
    elif base.type == "cylinder_base":
        diameter = dimension_value(base.dimensions, "outer_diameter")
        length = dimension_value(base.dimensions, "length")
        if diameter is None or length is None:
            return _failure_code(["Missing flange base dimensions"]), modeled, [{"feature": base.id, "reason": "Missing flange base dimensions"}]
        lines.append(f"    Cylinder(radius={diameter / 2:g}, height={length:g}, align=(Align.CENTER, Align.CENTER, Align.MIN))")
        modeled.append(base.id)
    else:
        return _failure_code([f"Unsupported base_feature type: {base.type}"]), modeled, [{"feature": base.id, "reason": f"Unsupported base_feature type: {base.type}"}]

    for feature in plan.features:
        if feature.type == "annular_groove":
            width = dimension_value(feature.dimensions, "axial_width", "width")
            reduced = dimension_value(feature.dimensions, "reduced_outer_diameter")
            z_start = dimension_value(feature.dimensions, "z_start")
            if width is None or reduced is None or z_start is None:
                skipped.append({"feature": feature.id, "reason": "Missing groove dimensions"})
                lines.append(f"    # UNRESOLVED: {feature.id} skipped by executor: Missing groove dimensions")
                continue
            outer = dimension_value(base.dimensions, "outer_diameter") or 0.0
            lines.append(f"    with Locations((0, 0, {z_start:g})):")
            lines.append(f"        Cylinder(radius={outer / 2:g}, height={width:g}, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)")
            lines.append(f"        Cylinder(radius={reduced / 2:g}, height={width:g}, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.ADD)")
            modeled.append(feature.id)
        elif feature.type == "through_hole":
            diameter = dimension_value(feature.dimensions, "diameter", "hole_diameter")
            if diameter is None:
                skipped.append({"feature": feature.id, "reason": "Missing hole diameter"})
                lines.append(f"    # UNRESOLVED: {feature.id} skipped by executor: Missing hole diameter")
                continue
            lines.append(f"    with Locations(({feature.placement.x or 0:g}, {feature.placement.y or 0:g}, {feature.placement.z or 0:g})):")
            lines.append(f"        Cylinder(radius={diameter / 2:g}, height=1000, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)")
            modeled.append(feature.id)
        elif feature.type == "circular_pattern":
            count = int(dimension_value(feature.dimensions, "count") or 0)
            radius = dimension_value(feature.dimensions, "pitch_radius", "bolt_circle_radius")
            diameter = dimension_value(feature.dimensions, "diameter", "hole_diameter")
            if count < 2 or radius is None or diameter is None:
                skipped.append({"feature": feature.id, "reason": "Missing circular pattern dimensions"})
                lines.append(f"    # UNRESOLVED: {feature.id} skipped by executor: Missing circular pattern dimensions")
                continue
            lines.append(f"    with Locations(({feature.placement.x or 0:g}, {feature.placement.y or 0:g}, {feature.placement.z or 0:g})):")
            lines.append(f"        with PolarLocations(radius={radius:g}, count={count}):")
            lines.append(f"            Cylinder(radius={diameter / 2:g}, height=1000, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)")
            modeled.append(feature.id)
        else:
            skipped.append({"feature": feature.id, "reason": f"Unsupported feature type: {feature.type}"})
            lines.append(f"    # UNRESOLVED: {feature.id} skipped by executor: Unsupported feature type: {feature.type}")

    lines.extend(_export_lines())
    return "\n".join(lines), modeled, skipped


def _export_lines() -> list[str]:
    return ["", 'export_step(part.part, "model.step")', 'export_stl(part.part, "model.stl")', ""]


def _failure_code(errors: list[str]) -> str:
    message = "; ".join(errors).replace('"', "'")
    return f'from build123d import *\n\nraise RuntimeError("FeaturePlan validation failed: {message}")\n'

