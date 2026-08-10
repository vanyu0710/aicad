from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build123d import Align, BuildPart, Cylinder, Box, Locations, Mode, PolarLocations, export_step, export_stl

from backend.schemas import FeaturePlanV3


def main() -> int:
    parser = argparse.ArgumentParser(description="MechCAD CAD worker")
    parser.add_argument("--plan", required=True, help="Path to FeaturePlan JSON")
    parser.add_argument("--out", required=True, help="Output directory")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_raw = json.loads(Path(args.plan).read_text(encoding="utf-8"))

    report: dict[str, Any] = {
        "ok": False,
        "engine": "build123d",
        "worker": "controlled-cad-worker",
        "modeled_features": [],
        "skipped_features": [],
        "warnings": [],
        "artifacts": {},
    }

    try:
        plan = FeaturePlanV3.model_validate(plan_raw)
        part = _build_part(plan, report)
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


def _build_part(plan: FeaturePlanV3, report: dict[str, Any]) -> BuildPart:
    with BuildPart() as part:
        base = plan.base_feature
        if base is None:
            raise RuntimeError("FeaturePlan has no base_feature")
        _apply_base(base, report)
        for feature in plan.features:
            _apply_feature(feature, plan, report)
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

    if kind in {"cylinder_base", "hollow_cylinder"}:
        diameter = _value(dims, "outer_diameter")
        length = _value(dims, "length")
        if not _positive(diameter, length):
            raise RuntimeError("cylinder base missing outer_diameter/length")
        Cylinder(radius=diameter / 2.0, height=length, align=(Align.CENTER, Align.CENTER, Align.MIN))
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
        _skip(report, feature, "当前受控执行器仅支持 Z 轴特征")
        return False

    if kind in {"through_hole", "blind_hole", "counterbore_hole"}:
        diameter = _value(dims, "diameter", "hole_diameter")
        if not _positive(diameter):
            _skip(report, feature, "缺少有效孔径")
            return False
        depth = _value(dims, "depth")
        if kind == "through_hole":
            depth = _through_depth(plan)
        elif not _positive(depth):
            _skip(report, feature, "缺少有效孔深")
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
            _skip(report, feature, "缺少有效长度、宽度或深度")
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
            _skip(report, feature, "环槽缺少槽底外径、轴向宽度或起始位置")
            return False
        if z_start < 0 or z_start + width > (_value(plan.base_feature.dimensions, "length") or 0) + 1e-6:
            _skip(report, feature, "环槽位置超出基体长度，减料与主体不相交")
            return False
        with Locations((0.0, 0.0, z_start)):
            Cylinder(radius=outer / 2.0, height=width, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
            Cylinder(radius=reduced / 2.0, height=width, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.ADD)
        report["modeled_features"].append(feature.id)
        feature.execution_status = "modeled"
        return True

    if kind in {"boss_cylinder"}:
        diameter = _value(dims, "diameter", "outer_diameter")
        height = _value(dims, "height", "length")
        if not _positive(diameter, height):
            _skip(report, feature, "缺少有效凸台直径或高度")
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
            _skip(report, feature, "缺少有效长度、宽度或高度")
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
            _skip(report, feature, "线性阵列缺少有效数量、间距或孔径")
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
            _skip(report, feature, "圆周阵列缺少有效数量、节圆半径或孔径")
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
        _skip(report, feature, "边选择不明确，无法安全执行圆角或倒角")
        return False

    _skip(report, feature, f"不支持的特征类型：{kind}")
    return False


def _skip(report: dict[str, Any], feature, reason: str) -> None:
    feature.execution_status = "skipped"
    report["skipped_features"].append({"feature": feature.id, "reason": reason})


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
