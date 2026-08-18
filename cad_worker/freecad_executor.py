from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cad_worker.font_guard import install as _install_font_guard

_install_font_guard()

from build123d import Align, BuildPart, Cylinder, Box, Locations, Mode, PolarLocations, export_step, export_stl

from backend.geometry.measurement import measure_shape
from backend.geometry.resolver import resolve_feature_geometry_evidence
from backend.geometry.verification import verify_feature_plan
from backend.normalization import normalize_feature_plan
from backend.schemas import FeaturePlanV3
from backend.validation import order_feature_plan

_LANG = "zh"

_NEEDS_XY_TYPES = {"through_hole", "blind_hole", "counterbore_hole", "rectangular_slot", "rectangular_pocket", "boss_cylinder", "rectangular_pad", "rib_box", "linear_pattern", "circular_pattern"}


def _msg(zh: str, en: str) -> str:
    return en if _LANG == "en" else zh


def _emit_cad_step(
    report: dict[str, Any],
    out_dir: Path,
    status: str,
    label: str,
    *,
    feature_id: str | None = None,
    operation: str | None = None,
    summary: str = "",
    detail: str = "",
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    step = {
        "id": f"cad_{len(report['process_steps']) + 1}",
        "stage": "cad",
        "status": status,
        "label": label,
        "summary": summary or label,
        "detail": detail,
        "feature_id": feature_id,
        "operation": operation,
        "started_at": now,
        "completed_at": now if status != "running" else None,
        "error": error,
        "warnings": [],
    }
    report["process_steps"].append(step)
    step_path = out_dir / "process_steps.jsonl"
    with step_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(step, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="MechCAD CAD worker")
    parser.add_argument("--plan", required=True, help="Path to FeaturePlan JSON")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--lang", default="zh", help="Output language (zh or en)")
    args = parser.parse_args()
    global _LANG
    _LANG = args.lang if args.lang in {"zh", "en"} else "zh"

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_raw = json.loads(Path(args.plan).read_text(encoding="utf-8"))

    report: dict[str, Any] = {
        "ok": False,
        "engine": "build123d",
        "worker": "controlled-cad-worker",
        "modeled_features": [],
        "skipped_features": [],
        "failed_features": [],
        "warnings": [],
        "artifacts": {},
        "process_steps": [],
    }

    plan = None
    try:
        plan = FeaturePlanV3.model_validate(plan_raw)
        normalize_feature_plan(plan)
        part = _build_part(plan, report, out_dir)
        measurement = measure_shape(part.part)
        evidence = resolve_feature_geometry_evidence(plan, measurement)
        report["geometry_measurement"] = measurement.model_dump(mode="json")
        report["geometry_evidence"] = evidence.model_dump(mode="json")
        report["geometry_verification"] = verify_feature_plan(plan, measurement, evidence_report=evidence).model_dump(mode="json")
        step_path = out_dir / "model.step"
        stl_path = out_dir / "model.stl"
        obj_path = out_dir / "model.obj"

        export_step(part.part, str(step_path))
        export_stl(part.part, str(stl_path))
        _stl_to_obj(stl_path, obj_path)

        report["ok"] = True
        report["artifacts"] = {
            "step": str(step_path),
            "stl": str(stl_path),
            "obj": str(obj_path),
        }
    except Exception as exc:
        report["error"] = str(exc)
        report["warnings"].append(str(exc))
    finally:
        if plan is not None:
            statuses = {}
            for feature in [plan.base_feature, *plan.features]:
                if feature is not None:
                    statuses[feature.id] = feature.execution_status
            report["feature_statuses"] = statuses
        (out_dir / "execution_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report_md = out_dir / "report.md"
        report_md.write_text(
            "# MechCAD Worker Report\n\n"
            f"- ok: {report['ok']}\n"
            f"- modeled: {', '.join(report['modeled_features']) or 'none'}\n"
            f"- skipped: {json.dumps(report['skipped_features'], ensure_ascii=False)}\n"
            f"- warnings: {json.dumps(report['warnings'], ensure_ascii=False)}\n",
            encoding="utf-8",
        )

    return 0 if report["ok"] else 1


def _build_part(plan: FeaturePlanV3, report: dict[str, Any], out_dir: Path) -> BuildPart:
    with BuildPart() as part:
        base = plan.base_feature
        if base is None:
            raise RuntimeError("FeaturePlan has no base_feature")
        _emit_cad_step(report, out_dir, "running", _msg("主基体", "Main body"), feature_id=base.id, operation="base", summary=_msg(f"开始建模主基体 {base.id}", f"Start modeling main body {base.id}"))
        try:
            _apply_base(base, report)
            _emit_cad_step(report, out_dir, "completed", _msg("主基体", "Main body"), feature_id=base.id, operation="base", summary=_msg(f"主基体 {base.id} 已完成", f"Main body {base.id} completed"))
        except Exception as exc:
            _emit_cad_step(report, out_dir, "failed", _msg("主基体", "Main body"), feature_id=base.id, operation="base", summary=_msg("主基体建模失败", "Main body modeling failed"), error=str(exc))
            raise
        order_feature_plan(plan)
        for feature in plan.features:
            _emit_cad_step(report, out_dir, "running", feature.type, feature_id=feature.id, operation=feature.operation, summary=_msg(f"开始执行 {feature.id}", f"Start executing {feature.id}"))
            before_volume = _safe_volume(part)
            modeled = _apply_feature(feature, plan, report)
            after_volume = _safe_volume(part)
            if modeled and before_volume is not None and after_volume is not None and abs(after_volume - before_volume) < 1e-6:
                feature.execution_status = "failed"
                report["failed_features"].append(feature.id)
                report["warnings"].append(f"Feature {feature.id} reported modeled but geometry did not change (volume {before_volume:.6f} -> {after_volume:.6f})")
                modeled = False
            if modeled:
                _emit_cad_step(report, out_dir, "completed", feature.type, feature_id=feature.id, operation=feature.operation, summary=_msg(f"特征 {feature.id} 已建模", f"Feature {feature.id} modeled"))
            elif feature.execution_status == "skipped":
                reason = next((item.get("reason", "") for item in reversed(report["skipped_features"]) if item.get("feature") == feature.id), "")
                _emit_cad_step(report, out_dir, "skipped", feature.type, feature_id=feature.id, operation=feature.operation, summary=_msg(f"特征 {feature.id} 已跳过", f"Feature {feature.id} skipped"), detail=reason, error=reason)
            else:
                _emit_cad_step(report, out_dir, "failed", feature.type, feature_id=feature.id, operation=feature.operation, summary=_msg(f"特征 {feature.id} 执行失败", f"Feature {feature.id} failed"))
    return part


def _apply_base(feature, report: dict[str, Any]) -> None:
    kind = feature.type
    dims = feature.dimensions
    if kind == "box_base":
        length = _value(dims, "length")
        width = _value(dims, "width")
        height = _value(dims, "height")
        if not _positive(length, width, height):
            raise RuntimeError("box_base missing length/width/height")
        Box(length, width, height, align=(Align.CENTER, Align.CENTER, Align.MIN))
        feature.execution_status = "modeled"
        report["modeled_features"].append(feature.id)
        feature.execution_status = "modeled"
        return

    if kind == "link_plate":
        length = _value(dims, "length")
        width = _value(dims, "width")
        height = _value(dims, "height")
        end_d1 = _value(dims, "end_diameter_1") or width
        end_d2 = _value(dims, "end_diameter_2") or width
        if not _positive(length, width, height, end_d1, end_d2):
            raise RuntimeError("link_plate missing length/width/height/end diameters")
        Box(length, width, height, align=(Align.CENTER, Align.CENTER, Align.MIN))
        with Locations((-length / 2.0, 0.0, 0.0)):
            Cylinder(radius=end_d1 / 2.0, height=height, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.ADD)
        with Locations((length / 2.0, 0.0, 0.0)):
            Cylinder(radius=end_d2 / 2.0, height=height, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.ADD)
        feature.execution_status = "modeled"
        report["modeled_features"].append(feature.id)
        return

    if kind in {"cylinder_base", "hollow_cylinder"}:
        diameter = _value(dims, "outer_diameter")
        length = _value(dims, "length")
        if not _positive(diameter, length):
            raise RuntimeError("cylinder base missing outer_diameter/length")
        Cylinder(radius=diameter / 2.0, height=length, align=(Align.CENTER, Align.CENTER, Align.MIN))
        feature.execution_status = "modeled"
        if kind == "hollow_cylinder":
            inner = _value(dims, "inner_diameter")
            if _positive(inner) and inner < diameter:
                Cylinder(
                    radius=inner / 2.0,
                    height=length + 2.0,
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                    mode=Mode.SUBTRACT,
                )
        report["modeled_features"].append(feature.id)
        return

    raise RuntimeError(f"Unsupported base feature type: {kind}")


def _apply_feature(feature, plan: FeaturePlanV3, report: dict[str, Any]) -> bool:
    kind = feature.type
    dims = feature.dimensions
    axis = feature.placement.axis
    if axis != "Z":
        _skip(report, feature, _msg("当前受控执行器仅支持 Z 轴特征", "The controlled executor currently supports Z-axis features only"))
        return False
    if kind in _NEEDS_XY_TYPES and not _placement_ready(feature):
        _skip(report, feature, _msg("缺少特征定位，X/Y 未确认", "Missing feature placement; X/Y position is not confirmed"))
        return False


    if kind in {"through_hole", "blind_hole", "counterbore_hole"}:
        diameter = _value(dims, "diameter", "hole_diameter")
        if not _positive(diameter):
            _skip(report, feature, _msg("缺少有效孔径", "Missing a valid hole diameter"))
            return False
        depth = _value(dims, "depth")
        if kind == "through_hole":
            depth = _through_depth(plan)
        elif not _positive(depth):
            _skip(report, feature, _msg("缺少有效孔深", "Missing a valid hole depth"))
            return False
        x, y, z = _placement(feature)
        with Locations((x, y, z)):
            Cylinder(radius=diameter / 2.0, height=depth, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
        report["modeled_features"].append(feature.id)
        feature.execution_status = "modeled"
        return True

    if kind in {"rectangular_slot", "rectangular_pocket"}:
        length = _value(dims, "length", "slot_length")
        width = _value(dims, "width", "slot_width")
        depth = _value(dims, "height", "depth")
        if not _positive(length, width, depth):
            _skip(report, feature, _msg("缺少有效长度、宽度或深度", "Missing valid length, width, or depth"))
            return False
        x, y, z = _placement(feature)
        with Locations((x, y, z)):
            Box(length, width, depth, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
        report["modeled_features"].append(feature.id)
        feature.execution_status = "modeled"
        return True

    if kind == "annular_groove":
        outer = _value(plan.base_feature.dimensions, "outer_diameter")
        reduced = _value(dims, "reduced_outer_diameter")
        width = _value(dims, "axial_width", "width")
        z_start = _value(dims, "z_start")
        if not _positive(outer, reduced, width) or z_start is None:
            _skip(report, feature, _msg("环槽缺少槽底外径、轴向宽度或起始位置", "Groove is missing root diameter, axial width, or start position"))
            return False
        if z_start < 0 or z_start + width > (_value(plan.base_feature.dimensions, "length") or 0) + 1e-6:
            _skip(report, feature, _msg("环槽位置超出基体长度，减料与主体不相交", "Groove position exceeds the base length; the cut does not intersect the body"))
            return False
        with Locations((0.0, 0.0, z_start)):
            Cylinder(radius=outer / 2.0, height=width, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
            Cylinder(radius=reduced / 2.0, height=width, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.ADD)
        report["modeled_features"].append(feature.id)
        feature.execution_status = "modeled"
        return True

    if kind == "internal_annular_groove":
        base = plan.base_feature
        inner = _value(base.dimensions, "inner_diameter") if base is not None else None
        outer = _value(base.dimensions, "outer_diameter") if base is not None else None
        width = _value(dims, "axial_width", "width")
        depth = _value(dims, "groove_depth", "depth")
        z_start = _value(dims, "z_start")
        if not _positive(inner, outer, width, depth) or z_start is None:
            _skip(report, feature, _msg("\u5185\u58c1\u69fd\u7f3a\u5c11\u5185\u5f84\u3001\u5916\u5f84\u3001\u69fd\u5bbd\u3001\u69fd\u6df1\u6216\u8d77\u59cb\u4f4d\u7f6e", "Internal groove is missing inner/outer diameter, width, depth, or start position"))
            return False
        reduced_inner = inner + 2.0 * depth
        if reduced_inner >= outer:
            _skip(report, feature, _msg("\u5185\u58c1\u69fd\u69fd\u5e95\u76f4\u5f84\u8d85\u8fc7\u5916\u5f84", "Internal groove root diameter exceeds the outer diameter"))
            return False
        length = _value(base.dimensions, "length") if base is not None else None
        if length is None or z_start < 0 or z_start + width > length + 1e-6:
            _skip(report, feature, _msg("\u5185\u58c1\u69fd\u4f4d\u7f6e\u8d85\u51fa\u57fa\u4f53\u957f\u5ea6", "Internal groove position exceeds the base length"))
            return False
        with Locations((0.0, 0.0, z_start)):
            Cylinder(radius=reduced_inner / 2.0, height=width, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
            Cylinder(radius=inner / 2.0, height=width, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.ADD)
        report["modeled_features"].append(feature.id)
        feature.execution_status = "modeled"
        return True

    if kind in {"boss_cylinder"}:
        diameter = _value(dims, "diameter", "outer_diameter")
        height = _value(dims, "height", "length")
        if not _positive(diameter, height):
            _skip(report, feature, _msg("缺少有效凸台直径或高度", "Missing a valid boss diameter or height"))
            return False
        x, y, z = _placement(feature)
        with Locations((x, y, z)):
            Cylinder(radius=diameter / 2.0, height=height, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.ADD)
        report["modeled_features"].append(feature.id)
        feature.execution_status = "modeled"
        return True

    if kind in {"rectangular_pad", "rib_box"}:
        length = _value(dims, "length")
        width = _value(dims, "width")
        height = _value(dims, "height", "depth")
        if not _positive(length, width, height):
            _skip(report, feature, _msg("缺少有效长度、宽度或高度", "Missing valid length, width, or height"))
            return False
        x, y, z = _placement(feature)
        with Locations((x, y, z)):
            Box(length, width, height, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.ADD)
        report["modeled_features"].append(feature.id)
        feature.execution_status = "modeled"
        return True

    if kind == "linear_pattern":
        count = int(_value(dims, "count") or 0)
        spacing = _value(dims, "spacing", "pitch")
        diameter = _value(dims, "diameter", "hole_diameter")
        if count < 2 or not _positive(spacing, diameter):
            _skip(report, feature, _msg("线性阵列缺少有效数量、间距或孔径", "Linear pattern is missing valid count, spacing, or hole diameter"))
            return False
        depth = _through_depth(plan)
        x, y, z = _placement(feature)
        start = -spacing * (count - 1) / 2.0
        with Locations(tuple((x + start + spacing * i, y, z) for i in range(count))):
            Cylinder(radius=diameter / 2.0, height=depth, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
        report["modeled_features"].append(feature.id)
        feature.execution_status = "modeled"
        return True

    if kind == "circular_pattern":
        count = int(_value(dims, "count") or 0)
        radius = _value(dims, "pitch_radius", "bolt_circle_radius")
        diameter = _value(dims, "diameter", "hole_diameter")
        if count < 2 or not _positive(radius, diameter):
            _skip(report, feature, _msg("圆周阵列缺少有效数量、节圆半径或孔径", "Circular pattern is missing valid count, pitch radius, or hole diameter"))
            return False
        depth = _through_depth(plan)
        x, y, z = _placement(feature)
        with Locations((x, y, z)):
            with PolarLocations(radius=radius, count=count):
                Cylinder(radius=diameter / 2.0, height=depth, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
        report["modeled_features"].append(feature.id)
        feature.execution_status = "modeled"
        return True

    if kind in {"fillet", "chamfer"}:
        _skip(report, feature, _msg("边选择不明确，无法安全执行圆角或倒角", "Edge selection is unclear; fillet/chamfer cannot be executed safely"))
        return False

    _skip(report, feature, _msg(f"不支持的特征类型：{kind}", f"Unsupported feature type: {kind}"))
    return False


def _safe_volume(part: BuildPart) -> float | None:
    try:
        target = getattr(part, "part", part)
        value = getattr(target, "volume", None)
        if callable(value):
            value = value()
        return float(value) if value is not None else None
    except Exception:
        return None


def _skip(report: dict[str, Any], feature, reason: str) -> None:
    feature.execution_status = "skipped"
    report["skipped_features"].append({"feature": feature.id, "reason": reason})


def _placement_ready(feature) -> bool:
    placement = feature.placement
    if placement.reference == "needs_position":
        return False
    # Centered labels are frames, not coordinates. Do not invent (0, 0).
    return placement.x is not None and placement.y is not None


def _placement(feature) -> tuple[float, float, float]:
    placement = feature.placement
    return (float(placement.x or 0.0), float(placement.y or 0.0), float(placement.z or 0.0))


def _value(dimensions: dict[str, Any], *names: str) -> float | None:
    for name in names:
        dim = dimensions.get(name)
        if dim is None:
            continue
        value = getattr(dim, "value", None) if not isinstance(dim, dict) else dim.get("value")
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _positive(*values: float | None) -> bool:
    return all(value is not None and value > 0 for value in values)


def _through_depth(plan: FeaturePlanV3) -> float:
    base = plan.base_feature
    if base is None:
        return 10.0
    if "length" in base.dimensions and base.dimensions["length"].value:
        return float(base.dimensions["length"].value) + 2.0
    if "height" in base.dimensions and base.dimensions["height"].value:
        return float(base.dimensions["height"].value) + 2.0
    return 10.0


def _stl_to_obj(stl_path: Path, obj_path: Path) -> Path:
    data = stl_path.read_bytes()
    if len(data) < 84:
        obj_path.write_text("# empty STL\n", encoding="utf-8")
        return obj_path

    tri_count = struct.unpack_from("<I", data, 80)[0]
    offset = 84
    vertices: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    for _ in range(tri_count):
        if offset + 50 > len(data):
            break
        offset += 12  # normal
        tri = []
        for _ in range(3):
            x, y, z = struct.unpack_from("<fff", data, offset)
            tri.append((x, y, z))
            offset += 12
        faces.append((len(vertices) + 1, len(vertices) + 2, len(vertices) + 3))
        vertices.extend(tri)
        offset += 2  # attribute byte count

    with obj_path.open("w", encoding="utf-8") as fh:
        fh.write("# MechCAD OBJ conversion\n")
        for x, y, z in vertices:
            fh.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
        for a, b, c in faces:
            fh.write(f"f {a} {b} {c}\n")
    return obj_path


if __name__ == "__main__":
    raise SystemExit(main())
