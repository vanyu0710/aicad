"""Reliability probe: real Build123d BRep -> measurement -> evidence -> hole verification.

Run:
    .\\.venv\\Scripts\\python.exe scripts\\probe_hole_verification_reliability.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _purge_build123d() -> None:
    import sys

    for name in list(sys.modules):
        if name == "build123d" or name.startswith("build123d."):
            del sys.modules[name]


def _skip_font_glob() -> None:
    import glob

    original_glob = glob.glob

    def _safe_glob(pathname, *args, **kwargs):
        text = str(pathname).replace("\\", "/").lower()
        if text.endswith((".ttf", ".otf", ".ttc")) or text.endswith(("*ttf", "*otf", "*ttc")):
            return []
        return original_glob(pathname, *args, **kwargs)

    glob.glob = _safe_glob  # type: ignore[assignment]


def _try_import_build123d() -> str | None:
    try:
        import build123d  # noqa: F401
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def _patch_fonts_and_import() -> str | None:
    """Skip system font enumeration so a bad Windows font cannot block CAD import."""
    try:
        _skip_font_glob()
        _purge_build123d()
        import build123d  # noqa: F401
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"


@dataclass
class CaseResult:
    name: str
    ok: bool
    detail: dict


def _props(report, feature_id: str) -> dict:
    feature = next(item for item in report.features if item.feature_id == feature_id)
    return {
        "feature_status": feature.status,
        "properties": {item.property_name: item.status for item in feature.properties},
        "reasons": {item.property_name: item.reason for item in feature.properties if item.reason},
    }


def _evidence(report, feature_id: str) -> dict:
    item = next(row for row in report.features if row.feature_id == feature_id)
    return {
        "correspondence": item.correspondence.status,
        "selected": item.correspondence.selected_candidate_ids,
        "candidates": item.correspondence.candidate_ids,
    }


def run_cases() -> list[CaseResult]:
    from build123d import Align, Box, BuildPart, Cylinder, Locations, Mode

    from backend.geometry.measurement import measure_shape
    from backend.geometry.resolver import resolve_feature_geometry_evidence
    from backend.geometry.verification import verify_feature_plan
    from backend.schemas import FeaturePlanV3, FeatureV3, PlacementV3

    results: list[CaseResult] = []

    def hole(fid: str, x, y, diameter=6.0, kind="through_hole", depth=None):
        dims = {"diameter": {"value": diameter}}
        if depth is not None:
            dims["depth"] = {"value": depth}
        return FeatureV3(
            id=fid,
            type=kind,
            operation="remove",
            dimensions=dims,
            placement=PlacementV3(reference="origin", axis="Z", x=x, y=y, z=0.0),
        )

    def box_plan(children):
        return FeaturePlanV3(
            base_feature=FeatureV3(
                id="base_box",
                type="box_base",
                operation="base",
                dimensions={"length": {"value": 100}, "width": {"value": 50}, "height": {"value": 10}},
                placement=PlacementV3(axis="Z"),
            ),
            features=children,
        )

    def build_box_holes(cuts: list[tuple[float, float, float, float]]):
        with BuildPart() as part:
            Box(100, 50, 10, align=(Align.CENTER, Align.CENTER, Align.MIN))
            for x, y, diameter, height in cuts:
                with Locations((x, y, 0.0)):
                    Cylinder(
                        radius=diameter / 2.0,
                        height=height,
                        align=(Align.CENTER, Align.CENTER, Align.MIN),
                        mode=Mode.SUBTRACT,
                    )
        return part.part

    def evaluate(name: str, plan, shape, expect_hole: dict[str, dict[str, str]]):
        measurement = measure_shape(shape)
        evidence = resolve_feature_geometry_evidence(plan, measurement)
        verification = verify_feature_plan(plan, measurement, evidence_report=evidence)
        detail = {
            "measurement_status": measurement.status,
            "bbox_z": measurement.bounding_box.size_z if measurement.bounding_box else None,
            "cylinders": [
                {
                    "index": item.measurement_index,
                    "diameter": item.diameter,
                    "height": item.height,
                    "axis": item.axis,
                    "center": item.center,
                }
                for item in measurement.cylinders
            ],
            "evidence": {fid: _evidence(evidence, fid) for fid in expect_hole},
            "verification": {fid: _props(verification, fid) for fid in expect_hole},
        }
        ok = True
        failures = []
        for fid, expected_props in expect_hole.items():
            actual = detail["verification"][fid]["properties"]
            for prop, status in expected_props.items():
                if actual.get(prop) != status:
                    ok = False
                    failures.append(f"{fid}.{prop} expected {status} got {actual.get(prop)}")
        detail["failures"] = failures
        results.append(CaseResult(name, ok, detail))

    # Worker through-depth is host height + 2.
    evaluate(
        "real_brep_single_through_hole_worker_overcut",
        box_plan([hole("hole_a", 20.0, 10.0)]),
        build_box_holes([(20.0, 10.0, 6.0, 12.0)]),
        {"hole_a": {"existence": "PASS", "diameter": "PASS", "position": "PASS", "axis": "PASS", "depth": "PASS", "through": "PASS"}},
    )
    evaluate(
        "real_brep_two_through_holes_distinct_xy",
        box_plan([hole("hole_a", 20.0, 10.0), hole("hole_b", -20.0, 10.0)]),
        build_box_holes([(20.0, 10.0, 6.0, 12.0), (-20.0, 10.0, 6.0, 12.0)]),
        {
            "hole_a": {"existence": "PASS", "position": "PASS", "diameter": "PASS"},
            "hole_b": {"existence": "PASS", "position": "PASS", "diameter": "PASS"},
        },
    )
    evaluate(
        "real_brep_two_same_diameter_without_xy",
        box_plan([hole("hole_a", None, None), hole("hole_b", None, None)]),
        build_box_holes([(20.0, 10.0, 6.0, 12.0), (-20.0, 10.0, 6.0, 12.0)]),
        {
            "hole_a": {"existence": "UNKNOWN", "diameter": "UNKNOWN", "position": "UNKNOWN"},
            "hole_b": {"existence": "UNKNOWN", "diameter": "UNKNOWN", "position": "UNKNOWN"},
        },
    )
    evaluate(
        "real_brep_blind_hole_depth_4",
        box_plan([hole("hole_a", 20.0, 10.0, kind="blind_hole", depth=4.0)]),
        build_box_holes([(20.0, 10.0, 6.0, 4.0)]),
        {"hole_a": {"existence": "PASS", "diameter": "PASS", "depth": "PASS", "through": "SKIPPED"}},
    )
    evaluate(
        "real_brep_wrong_xy_unique_hole",
        box_plan([hole("hole_a", 0.0, 0.0)]),
        build_box_holes([(20.0, 10.0, 6.0, 12.0)]),
        {"hole_a": {"existence": "PASS", "position": "FAIL"}},
    )
    evaluate(
        "real_brep_hollow_confusion_same_inner_and_hole_diameter",
        FeaturePlanV3(
            base_feature=FeatureV3(
                id="base_tube",
                type="hollow_cylinder",
                operation="base",
                dimensions={
                    "outer_diameter": {"value": 40},
                    "inner_diameter": {"value": 6},
                    "length": {"value": 20},
                },
                placement=PlacementV3(axis="Z"),
            ),
            features=[hole("hole_a", 0.0, 0.0, diameter=6.0)],
        ),
        _hollow_with_same_bore(),
        {"hole_a": {"existence": "UNKNOWN"}},  # must not silently pick the inner bore
    )
    return results


def _hollow_with_same_bore():
    from build123d import Align, BuildPart, Cylinder, Mode

    with BuildPart() as part:
        Cylinder(radius=20.0, height=20.0, align=(Align.CENTER, Align.CENTER, Align.MIN))
        Cylinder(radius=3.0, height=22.0, align=(Align.CENTER, Align.CENTER, Align.MIN), mode=Mode.SUBTRACT)
    return part.part


def main() -> int:
    _skip_font_glob()
    err = _try_import_build123d()
    patched = False
    if err:
        patched_err = _patch_fonts_and_import()
        if patched_err:
            print(json.dumps({"import_ok": False, "error": err, "patched_error": patched_err}, indent=2))
            return 2
        patched = True

    results = run_cases()
    payload = {
        "import_ok": True,
        "font_patch_used": patched,
        "passed": sum(1 for item in results if item.ok),
        "failed": sum(1 for item in results if not item.ok),
        "cases": [{"name": item.name, "ok": item.ok, **item.detail} for item in results],
    }
    print(json.dumps(payload, indent=2, default=str))
    return 0 if all(item.ok for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
