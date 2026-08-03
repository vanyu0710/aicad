from __future__ import annotations

"""Turn visual evidence into a conservative, auditable CAD plan.

The planner intentionally does not infer missing axial dimensions from diameter
differences.  It only emits deterministic geometry when every required value is
traceable to the visual-analysis JSON.
"""

from typing import Any


def build_model_plan(qwen_json: dict[str, Any], description: str, requested_family: str = "") -> dict[str, Any]:
    """Create a model plan that is safe for a code generator to follow."""
    sketch = qwen_json.get("sketch") if isinstance(qwen_json.get("sketch"), dict) else {}
    dimensions = sketch.get("dimensions") if isinstance(sketch.get("dimensions"), dict) else {}
    evidence = _dimension_ledger(dimensions, sketch.get("raw_annotations", []))
    text = " ".join((str(description), str(qwen_json.get("domain", {})), str(sketch)))

    outer = _pick(evidence, ("outer_diameter", "outside_diameter", "od"), prefer="max")
    inner = _pick(evidence, ("inner_diameter", "inside_diameter", "bore_diameter", "id"), prefer="min")
    length = _pick(evidence, ("overall_length", "total_length", "total_height", "length", "height"), prefer="max")
    is_tube = _looks_like_tube(text, outer, inner, length)
    unresolved: list[dict[str, str]] = []

    if not is_tube:
        return _unsupported_plan(evidence, requested_family, unresolved)

    missing = []
    if outer is None:
        missing.append("outer_diameter")
    if inner is None:
        missing.append("inner_diameter")
    if length is None:
        missing.append("overall_length")
    if outer and inner and inner["value"] >= outer["value"]:
        missing.append("inner_diameter must be smaller than outer_diameter")

    if missing:
        unresolved.extend({"feature": "base_body", "reason": item} for item in missing)
        return {
            "schema_version": "1.0",
            "status": "needs_clarification",
            "part_family": "axisymmetric_tube",
            "coordinate_system": {"main_axis": "Z", "origin": "center of the first end face", "units": "mm"},
            "dimension_ledger": evidence,
            "base_body": None,
            "features": [],
            "unresolved": unresolved,
            "execution": {"deterministic_build123d_available": False},
        }

    features, feature_unresolved = _tube_features(sketch, evidence, length["value"])
    unresolved.extend(feature_unresolved)
    return {
        "schema_version": "1.0",
        "status": "ready_with_unresolved_features" if unresolved else "ready",
        "part_family": "axisymmetric_tube",
        "coordinate_system": {"main_axis": "Z", "origin": "center of the first end face", "units": "mm"},
        "dimension_ledger": evidence,
        "base_body": {
            "type": "hollow_cylinder",
            "outer_diameter": outer,
            "inner_diameter": inner,
            "length": length,
            "construction_order": ["create exterior", "apply axial features in order", "cut through bore"],
        },
        "features": features,
        "unresolved": unresolved,
        "execution": {"deterministic_build123d_available": True},
    }


def deterministic_build123d_code(model_plan: dict[str, Any]) -> str | None:
    """Generate only the small, well-understood axisymmetric subset locally."""
    body = model_plan.get("base_body")
    if model_plan.get("part_family") != "axisymmetric_tube" or not isinstance(body, dict):
        return None
    try:
        outer = float(body["outer_diameter"]["value"])
        inner = float(body["inner_diameter"]["value"])
        length = float(body["length"]["value"])
    except (KeyError, TypeError, ValueError):
        return None

    lines = [
        "from build123d import *",
        "",
        "with BuildPart() as part:",
        f"    # Base exterior from source outer diameter {outer:g} mm.",
        f"    Cylinder(radius={outer / 2:g}, height={length:g})",
    ]
    for feature in model_plan.get("features", []):
        if feature.get("type") != "annular_groove":
            continue
        try:
            diameter = float(feature["reduced_outer_diameter"]["value"])
            z_start = float(feature["z_start"]["value"])
            width = float(feature["axial_width"]["value"])
        except (KeyError, TypeError, ValueError):
            continue
        lines.extend(
            [
                f"    # {feature.get('location', 'annular groove')} from explicit axial dimensions.",
                f"    with Locations((0, 0, {z_start:g})):",
                f"        Cylinder(radius={outer / 2:g}, height={width:g}, mode=Mode.SUBTRACT)",
                f"        Cylinder(radius={diameter / 2:g}, height={width:g}, mode=Mode.ADD)",
            ]
        )
    lines.extend(
        [
            f"    # Through bore from source inner diameter {inner:g} mm.",
            f"    Cylinder(radius={inner / 2:g}, height={length + 2:g}, mode=Mode.SUBTRACT)",
            "",
            'export_step(part.part, "model.step")',
            'export_stl(part.part, "model.stl")',
            "",
        ]
    )
    return "\n".join(lines)


def _unsupported_plan(evidence: list[dict[str, Any]], requested_family: str, unresolved: list[dict[str, str]]) -> dict[str, Any]:
    unresolved.append({"feature": "part_family", "reason": "Only axisymmetric tube planning is deterministic in this MVP."})
    return {
        "schema_version": "1.0",
        "status": "needs_clarification",
        "part_family": "unsupported",
        "requested_family": requested_family,
        "coordinate_system": {"main_axis": None, "origin": None, "units": "mm"},
        "dimension_ledger": evidence,
        "base_body": None,
        "features": [],
        "unresolved": unresolved,
        "execution": {"deterministic_build123d_available": False},
    }


def _dimension_ledger(dimensions: dict[str, Any], annotations: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for name, raw in dimensions.items():
        for value in _numbers(raw):
            entries.append({"name": str(name), "value": value, "unit": "mm", "source": f"sketch.dimensions.{name}"})
    if isinstance(annotations, list):
        for index, annotation in enumerate(annotations):
            if not isinstance(annotation, dict):
                continue
            for value in _numbers(annotation.get("numeric_value", annotation.get("value"))):
                entries.append(
                    {
                        "name": "raw_annotation",
                        "value": value,
                        "unit": annotation.get("unit") or "mm",
                        "source": f"sketch.raw_annotations[{index}]",
                        "annotation": annotation.get("text", ""),
                        "axis": annotation.get("dimension_line_axis", "unknown"),
                    }
                )
    return entries


def _pick(entries: list[dict[str, Any]], names: tuple[str, ...], prefer: str) -> dict[str, Any] | None:
    matches = [entry for entry in entries if str(entry["name"]).lower() in names]
    if not matches:
        return None
    return max(matches, key=lambda entry: entry["value"]) if prefer == "max" else min(matches, key=lambda entry: entry["value"])


def _tube_features(sketch: dict[str, Any], entries: list[dict[str, Any]], length: float) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    features = sketch.get("features") if isinstance(sketch.get("features"), list) else []
    planned, unresolved = _feature_level_tube_features(features, length)
    if planned or unresolved:
        return planned, unresolved

    text = str(sketch).lower()
    mentions_groove = any(word in text for word in ("groove", "annular", "slot", "槽", "环槽")) or any("groove" in str(item).lower() for item in features)
    if not mentions_groove:
        return [], []

    width = _pick(entries, ("groove_width", "annular_groove_width", "axial_groove_width"), "max")
    reduced = _pick(entries, ("groove_diameter", "reduced_outer_diameter", "step_diameter"), "min")
    if width is None or reduced is None:
        missing = []
        if reduced is None:
            missing.append("reduced outer diameter")
        if width is None:
            missing.append("axial groove width")
        return [], [{"feature": "annular_groove", "reason": "Missing explicit " + " and ".join(missing) + "; feature intentionally omitted."}]

    # A single unlocated groove cannot be placed honestly.  Qwen must supply a named end/location.
    location = _pick(entries, ("groove_position", "feature_position"), "min")
    if location is None:
        return [], [{"feature": "annular_groove", "reason": "Groove location along Z is not explicitly identified; feature intentionally omitted."}]
    z_start = location
    if z_start["value"] < 0 or z_start["value"] + width["value"] > length:
        return [], [{"feature": "annular_groove", "reason": "Groove position lies outside the part length; feature intentionally omitted."}]
    return (
        [{"type": "annular_groove", "location": "axial feature", "z_start": z_start, "axial_width": width, "reduced_outer_diameter": reduced}],
        [],
    )


def _feature_level_tube_features(features: list[Any], length: float) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Map only fully bound end-profile features into deterministic operations."""
    planned: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    supported = {"annular_end_step", "annular_groove", "shoulder"}
    for index, raw_feature in enumerate(features):
        if not isinstance(raw_feature, dict):
            continue
        feature_type = str(raw_feature.get("type", "")).lower()
        if feature_type not in supported:
            continue
        label = str(raw_feature.get("id") or f"feature_{index + 1}")
        location = str(raw_feature.get("location", "unknown")).lower()
        width = _feature_dimension(raw_feature, "axial_length", label)
        diameter = _feature_dimension(raw_feature, "outer_diameter", label)
        z_start = _feature_dimension(raw_feature, "axial_start_from_bottom", label)
        if z_start is None and width is not None:
            if location == "bottom_end":
                z_start = {**width, "value": 0.0, "source": f"sketch.features[{label}].location"}
            elif location == "top_end":
                z_start = {
                    **width,
                    "value": length - width["value"],
                    "source": f"sketch.features[{label}].location + axial_length",
                }
        missing = []
        if width is None:
            missing.append("axial_length")
        if diameter is None:
            missing.append("outer_diameter")
        if z_start is None:
            missing.append("location or axial_start_from_bottom")
        if missing:
            unresolved.append({"feature": label, "reason": "Missing explicit " + ", ".join(missing) + "; feature intentionally omitted."})
            continue
        if width["value"] <= 0 or diameter["value"] <= 0 or z_start["value"] < 0 or z_start["value"] + width["value"] > length:
            unresolved.append({"feature": label, "reason": "Feature dimensions lie outside the base tube; feature intentionally omitted."})
            continue
        planned.append(
            {
                "type": "annular_groove",
                "source_type": feature_type,
                "location": location,
                "z_start": z_start,
                "axial_width": width,
                "reduced_outer_diameter": diameter,
                "source_feature_id": label,
            }
        )
    return planned, unresolved


def _feature_dimension(feature: dict[str, Any], name: str, label: str) -> dict[str, Any] | None:
    raw = feature.get(name)
    values = _numbers(raw)
    if not values:
        return None
    unit = raw.get("unit", "mm") if isinstance(raw, dict) else "mm"
    evidence = raw.get("evidence", "") if isinstance(raw, dict) else ""
    return {
        "name": name,
        "value": values[0],
        "unit": unit,
        "source": f"sketch.features[{label}].{name}",
        "evidence": evidence,
    }


def _looks_like_tube(text: str, outer: Any, inner: Any, length: Any) -> bool:
    # Named OD/ID dimensions are conventional evidence of a coaxial tube even
    # when the text description is sparse; no unlabelled numeric values qualify.
    has_coaxial_dimension_set = outer is not None and inner is not None and length is not None
    return has_coaxial_dimension_set


def _numbers(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        if "value" in value:
            return _numbers(value["value"])
        if "numeric_value" in value:
            return _numbers(value["numeric_value"])
        result: list[float] = []
        for item in value.values():
            result.extend(_numbers(item))
        return result
    if isinstance(value, list):
        result: list[float] = []
        for item in value:
            result.extend(_numbers(item))
        return result
    return []
