from __future__ import annotations

"""Generic modeling core: evidence, intent, family templates, and reports."""

import re
from typing import Any

from backend.schemas import (
    DesignIntent,
    DimensionV3,
    EvidenceItem,
    EvidenceSet,
    ExecutionReport,
    FamilyTemplate,
    FeaturePlanV3,
    FeatureSemantics,
    FeatureV3,
    PlacementV3,
    TemplateFeatureSpec,
)
from backend.feature_definitions import FEATURE_DEFINITIONS, to_semantics
from backend.normalization import normalize_feature_plan


def detect_input_kind(image_present: bool, description: str) -> str:
    has_text = bool((description or "").strip())
    if image_present and has_text:
        return "mixed"
    if image_present:
        return "image_only"
    return "text_only"


def _clue(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_clues(description: str) -> dict[str, float]:
    text = description.lower()
    result: dict[str, float] = {}
    patterns = {
        "outer_diameter": r"(?:\u5916\u5f84|\u5916\u5706\u76f4\u5f84|outer\s*diameter|od)\s*(?:[:=]|\u4e3a|\u662f)?\s*(?:\u03c6|\u03a6|\u00d8)?\s*(\d+(?:\.\d+)?)",
        "inner_diameter": r"(?:\u5185\u5f84|\u5185\u5706\u76f4\u5f84|inner\s*diameter|id|bore)\s*(?:[:=]|\u4e3a|\u662f)?\s*(?:\u03c6|\u03a6|\u00d8)?\s*(\d+(?:\.\d+)?)",
        "length": r"(?:\u957f\u5ea6|\u603b\u957f|overall\s*length|length|L)\s*(?:[:=]|\u4e3a|\u662f)?\s*(\d+(?:\.\d+)?)",
        "width": r"(?:\u5bbd\u5ea6|\u5bbd|width|W)\s*(?:[:=]|\u4e3a|\u662f)?\s*(\d+(?:\.\d+)?)",
        "height": r"(?:\u9ad8\u5ea6|\u539a|\u539a\u5ea6|height|thickness|T)\s*(?:[:=]|\u4e3a|\u662f)?\s*(\d+(?:\.\d+)?)",
        "depth": r"(?:\u6df1\u5ea6|\u6df1|depth)\s*(?:[:=]|\u4e3a|\u662f)?\s*(\d+(?:\.\d+)?)",
        "hole_diameter": r"(?:\u4e2d\u5fc3\u5b54|\u4e2d\u5fc3\u901a\u5b54|\u901a\u5b54|\u5b54\u76f4\u5f84|\u5b54\u5f84|center\s*(?:through\s*)?(?:hole|bore)|hole\s*diameter)\s*(?:[:=]|\u4e3a|\u662f)?\s*(?:\u03c6|\u03a6|\u00d8)?\s*(\d+(?:\.\d+)?)",
        "pitch_circle_diameter": r"(?:\u87ba\u6813\u5706|\u8282\u5706|pitch\s*(?:circle)?|bolt\s*(?:circle)?)\s*(?:\u76f4\u5f84|diameter)?\s*(?:[:=]|\u4e3a|\u662f)?\s*(?:\u03c6|\u03a6|\u00d8)?\s*(\d+(?:\.\d+)?)",
        "module": r"(?:\u6a21\u6570|module)\s*(?:[:=]|\u4e3a|\u662f)?\s*(\d+(?:\.\d+)?)",
        "tooth_count": r"(?:\u9f7f\u6570|tooth\s*count|teeth|z)\s*(?:[:=]|\u4e3a|\u662f)?\s*(\d+(?:\.\d+)?)",
        "hub_outer_diameter": r"(?:\u8f6e\u6bc2\u5916\u5f84|hub\s*(?:outer\s*)?(?:diameter|od))\s*(?:[:=]|\u4e3a|\u662f)?\s*(?:\u03c6|\u03a6|\u00d8)?\s*(\d+(?:\.\d+)?)",
        "groove_width": r"(?:\u69fd\u5bbd|\u73af\u5f62\u69fd\u5bbd|groove\s*width|slot\s*width)\s*(?:[:=]|\u4e3a|\u662f)?\s*(\d+(?:\.\d+)?)",
        "groove_depth": r"(?:\u69fd\u6df1|groove\s*depth|slot\s*depth)\s*(?:[:=]|\u4e3a|\u662f)?\s*(\d+(?:\.\d+)?)",
        "groove_distance": r"(?:\u8ddd\u7aef\u9762|\u8ddd\s*\u7aef\u9762|distance\s*from\s*(?:face|end)|from\s*end)\s*(?:[:=]|\u4e3a|\u662f)?\s*(\d+(?:\.\d+)?)",
        "end_diameter_1": r"(?:\u4e00\u7aef|\u7b2c\u4e00\u7aef|first\s*end|end\s*1)\s*(?:\u76f4\u5f84|diameter)?\s*(?:[:=]|\u4e3a|\u662f)?\s*(?:\u03c6|\u03a6|\u00d8)?\s*(\d+(?:\.\d+)?)",
        "end_diameter_2": r"(?:\u53e6\u4e00\u7aef|\u7b2c\u4e8c\u7aef|second\s*end|end\s*2)\s*(?:\u76f4\u5f84|diameter)?\s*(?:[:=]|\u4e3a|\u662f)?\s*(?:\u03c6|\u03a6|\u00d8)?\s*(\d+(?:\.\d+)?)",
        "center_distance": r"(?:\u4e24\u5b54\u4e2d\u5fc3\u8ddd|\u4e2d\u5fc3\u8ddd|center\s*distance)\s*(?:[:=]|\u4e3a|\u662f)?\s*(\d+(?:\.\d+)?)",
        "support_height": r"(?:\u7acb\u677f\u9ad8|\u652f\u6491\u677f\u9ad8|vertical\s*support\s*height|support\s*height)\s*(?:[:=]|\u4e3a|\u662f)?\s*(\d+(?:\.\d+)?)",
        "support_thickness": r"(?:\u7acb\u677f\u539a|\u652f\u6491\u677f\u539a|support\s*thickness)\s*(?:[:=]|\u4e3a|\u662f)?\s*(\d+(?:\.\d+)?)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            value = next((group for group in match.groups() if group), None)
            if value:
                result[key] = float(value)

    m6 = re.search(r"M\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m6:
        result["metric_thread"] = float(m6.group(1))
    count = re.search(r"(\d+)\s*(?:holes|bolt\s*holes)", text, re.IGNORECASE)
    if count:
        result["pattern_count"] = float(count.group(1))

    hole_before = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:mm|\u6beb\u7c73)?\s*(?:center\s*(?:through\s*)?(?:hole|bore)|\u4e2d\u5fc3\u5b54|\u4e2d\u5fc3\u901a\u5b54|\u901a\u5b54)",
        text,
        re.IGNORECASE,
    )
    if hole_before:
        result.setdefault("hole_diameter", float(hole_before.group(1)))

    if not any("diameter" in key for key in result):
        bare = re.search(r"(?:diameter)\s*(?:[:=]|is)?\s*(?:\u03c6|\u03a6|\u00d8)?\s*(\d+(?:\.\d+)?)", text)
        if bare:
            result["diameter"] = float(bare.group(1))
    return result


def build_evidence_set(
    description: str,
    vision_json: dict[str, Any] | None,
    image_present: bool,
    language: str = "zh",
) -> EvidenceSet:
    evidence = EvidenceSet(input_kind=detect_input_kind(image_present, description))
    for key, value in extract_clues(description).items():
        evidence.add(key, value, source="user", confirmed_by_user=True, confidence=1.0)
    if vision_json:
        for item in _iter_vision_evidence(vision_json):
            evidence.items.append(item)
        for left, right in _find_evidence_conflicts(evidence):
            evidence.conflicts.append(
                f"{left.key}: {left.value} conflicts with {right.key}: {right.value}"
            )
    return evidence


def _iter_vision_evidence(vision_json: dict[str, Any]):
    for container in ("dimensions", "evidence", "annotations"):
        for item in vision_json.get(container, []) or []:
            if isinstance(item, dict):
                key = str(item.get("name") or item.get("key") or "")
                value = item.get("value")
                if key and value is not None:
                    yield EvidenceItem(
                        key=key,
                        value=_clue(value),
                        unit=str(item.get("unit") or "mm"),
                        source="drawing",
                        feature_id=item.get("feature_id"),
                        dimension=item.get("dimension"),
                        confidence=_clue(item.get("confidence")),
                    )


def _find_evidence_conflicts(evidence: EvidenceSet):
    by_key: dict[str, list[EvidenceItem]] = {}
    for item in evidence.items:
        by_key.setdefault(item.key, []).append(item)
    conflicts = []
    for key, items in by_key.items():
        if len(items) < 2:
            continue
        first = items[0]
        for other in items[1:]:
            if first.value is not None and other.value is not None and float(first.value) != float(other.value):
                conflicts.append((first, other))
    return conflicts


def infer_design_intent(
    description: str,
    part_family: str,
    required_capabilities: list[str],
    unsupported_requirements: list[str],
    language: str = "zh",
) -> DesignIntent:
    text = description.lower()
    function = "mounting and connection" if "mount" in text or "installation" in text else "mechanical part"
    if language == "zh":
        function = "mounting and connection" if "mount" in text else "mechanical part"
    return DesignIntent(
        part_family=part_family,
        confidence=0.8 if part_family != "unknown" else None,
        function=function,
        main_datum="XY",
        main_axis="Z",
        manufacturing_intent="machined or printed as one part",
        required_capabilities=required_capabilities,
        unsupported_requirements=unsupported_requirements,
        summary=f"Concept intent for {part_family}: {function}",
    )


def feature_semantics() -> list[FeatureSemantics]:
    # Legacy endpoint shape is preserved; all feature types live in the registry.
    legacy_order = [
        "box_base",
        "cylinder_base",
        "hollow_cylinder",
        "link_plate",
        "through_hole",
        "internal_annular_groove",
        "circular_pattern",
        "rectangular_pad",
        "rib_box",
    ]
    return [to_semantics(definition) for definition in (FEATURE_DEFINITIONS.get(name) for name in legacy_order) if definition is not None]


def _dimension(value: float | None, evidence: str, source: str = "drawing", confirmed: bool = True) -> DimensionV3:
    return DimensionV3(value=value, unit="mm", evidence=evidence, source=source, confirmed_by_user=confirmed)


def _template_base(template: FamilyTemplate, parsed: dict[str, float], language: str) -> tuple[FeatureV3 | None, list[str]]:
    dimensions: dict[str, DimensionV3] = {}
    missing: list[str] = []
    for target, clue in template.base_dimension_map.items():
        value = parsed.get(clue)
        if value is None and clue == "diameter":
            value = parsed.get("diameter")
        dimensions[target] = _dimension(value, f"template base {template.family}", "user" if value is not None else "unknown", confirmed=value is not None)
        if value is None:
            missing.append(target)
    if not dimensions:
        return None, missing
    return FeatureV3(id=f"base_{template.family}", type=template.base_type, operation="base", dimensions=dimensions, placement=PlacementV3(reference="origin" if template.base_type in {"box_base", "link_plate"} else "center", axis="Z"), evidence=f"family template {template.family}"), missing


def _template_features(template: FamilyTemplate, parsed: dict[str, float], base_id: str, language: str) -> tuple[list[FeatureV3], list[dict[str, Any]]]:
    features: list[FeatureV3] = []
    unresolved: list[dict[str, Any]] = []
    for spec in template.feature_specs:
        dimensions: dict[str, DimensionV3] = {}
        for target, clue in spec.dimension_map.items():
            value = parsed.get(clue)
            if value is None and clue == "diameter":
                value = parsed.get("diameter")
            dimensions[target] = _dimension(value, spec.evidence or f"template feature {spec.id}", "user" if value is not None else "unknown", confirmed=value is not None)
        for target, value in spec.defaults.items():
            if target not in dimensions or dimensions[target].value is None:
                dimensions[target] = _dimension(value, f"template default for {spec.id}", "assumption", confirmed=False)
        if spec.type == "circular_pattern" and (dimensions.get("pitch_radius") is None or dimensions["pitch_radius"].value is None):
            circle = parsed.get("pitch_circle_diameter")
            if circle:
                dimensions["pitch_radius"] = _dimension(circle / 2.0, "derived from bolt circle diameter", "derived", confirmed=True)
        placement = _placement_for_rule(spec, parsed)
        feature = FeatureV3(
            id=spec.id,
            type=spec.type,
            operation=spec.operation,
            dimensions=dimensions,
            placement=placement,
            extent=spec.extent,
            depends_on=[base_id, *spec.depends_on] if spec.depends_on else [base_id],
            evidence=spec.evidence or f"family template {template.family}",
        )
        missing = [name for name, dim in dimensions.items() if dim.value is None]
        if missing and spec.required:
            unresolved.append({"feature": spec.id, "reason": f"missing executable dimensions: {', '.join(missing)}"})
            feature.unresolved.append("missing executable dimensions: " + ", ".join(missing))
        features.append(feature)
    return features, unresolved


def _placement_for_rule(spec: TemplateFeatureSpec, parsed: dict[str, float]) -> PlacementV3:
    if spec.placement is not None:
        return spec.placement
    rule = spec.placement_rule
    if rule == "center":
        return PlacementV3(reference="model_center", x=0.0, y=0.0, axis="Z")
    if rule == "bolt_circle":
        radius = (parsed.get("pitch_circle_diameter") or 0.0) / 2.0
        return PlacementV3(reference="model_center", x=radius if radius else None, y=0.0, axis="Z")
    if rule == "vertical_support":
        return PlacementV3(reference="base_center", x=0.0, y=0.0, z=float(parsed.get("height") or 0.0), axis="Z")
    if rule == "front_lip":
        return PlacementV3(reference="base_center", x=float(parsed.get("length") or 0.0) / 2.0 - float(parsed.get("lip_width") or 5.0) / 2.0, y=0.0, z=float(parsed.get("height") or 0.0), axis="Z")
    if rule == "mounting_hole":
        return PlacementV3(reference="base_center", x=float(parsed.get("length") or 0.0) / 2.0 - 10.0, y=0.0, axis="Z")
    if rule == "top_rib":
        return PlacementV3(reference="base_center", x=0.0, y=0.0, z=float(parsed.get("height") or 0.0), axis="Z")
    if rule == "end_hole_left":
        center_distance = parsed.get("center_distance")
        half = float(center_distance / 2.0 if center_distance else ((parsed.get("length") or 0.0) / 2.0))
        return PlacementV3(reference="model_center", x=-half, y=0.0, axis="Z")
    if rule == "end_hole_right":
        center_distance = parsed.get("center_distance")
        half = float(center_distance / 2.0 if center_distance else ((parsed.get("length") or 0.0) / 2.0))
        return PlacementV3(reference="model_center", x=half, y=0.0, axis="Z")
    return PlacementV3(reference="origin", axis="Z")


DEFAULT_TEMPLATES: list[FamilyTemplate] = [
    FamilyTemplate(
        family="flange",
        base_type="cylinder_base",
        base_dimension_map={"outer_diameter": "outer_diameter", "length": "height"},
        feature_specs=[
            TemplateFeatureSpec(id="center_hole", type="through_hole", operation="remove", dimension_map={"diameter": "hole_diameter"}, placement_rule="center", extent="through", evidence="flange center through hole"),
            TemplateFeatureSpec(id="bolt_hole_seed", type="through_hole", operation="remove", dimension_map={"diameter": "metric_thread"}, placement_rule="bolt_circle", extent="through", evidence="bolt hole seed on bolt circle"),
            TemplateFeatureSpec(id="bolt_hole_circular_pattern", type="circular_pattern", operation="pattern", depends_on=["bolt_hole_seed"], dimension_map={"count": "pattern_count", "pitch_radius": "pitch_radius", "diameter": "metric_thread"}, placement_rule="center", evidence="bolt hole circular pattern"),
        ],
        design_intent="flange with center bore and bolt circle",
    ),
    FamilyTemplate(
        family="tube",
        base_type="hollow_cylinder",
        base_dimension_map={"outer_diameter": "outer_diameter", "inner_diameter": "inner_diameter", "length": "length"},
        feature_specs=[
            TemplateFeatureSpec(id="internal_groove", type="internal_annular_groove", operation="remove", dimension_map={"axial_width": "groove_width", "groove_depth": "groove_depth", "z_start": "groove_distance"}, placement_rule="center", evidence="internal wall annular groove"),
        ],
        design_intent="tube with internal annular groove",
    ),
    FamilyTemplate(
        family="link_plate",
        base_type="link_plate",
        base_dimension_map={"length": "length", "width": "width", "height": "height"},
        feature_specs=[
            TemplateFeatureSpec(id="hole_end_1", type="through_hole", operation="remove", dimension_map={"diameter": "end_diameter_1"}, placement_rule="end_hole_left", extent="through", evidence="rocker end hole 1"),
            TemplateFeatureSpec(id="hole_end_2", type="through_hole", operation="remove", dimension_map={"diameter": "end_diameter_2"}, placement_rule="end_hole_right", extent="through", evidence="rocker end hole 2"),
        ],
        design_intent="link/rocker plate with two end holes",
    ),
    FamilyTemplate(
        family="phone_stand",
        base_type="box_base",
        base_dimension_map={"length": "length", "width": "width", "height": "height"},
        feature_specs=[
            TemplateFeatureSpec(id="vertical_support", type="rectangular_pad", operation="add", dimension_map={"length": "support_thickness", "width": "width", "height": "support_height"}, placement_rule="vertical_support", evidence="vertical support plate"),
            TemplateFeatureSpec(id="mounting_hole_1", type="through_hole", operation="remove", dimension_map={"diameter": "hole_diameter"}, placement_rule="mounting_hole", extent="through", evidence="mounting hole"),
            TemplateFeatureSpec(id="mounting_hole_2", type="through_hole", operation="remove", dimension_map={"diameter": "hole_diameter"}, placement=PlacementV3(reference="base_center", x=30.0, y=0.0, z=0.0, axis="Z"), extent="through", evidence="mounting hole 2"),
            TemplateFeatureSpec(id="anti_slip_rib", type="rib_box", operation="add", defaults={"length": 8.0, "width": 6.0, "height": 3.0}, placement_rule="top_rib", evidence="anti-slip rib"),
        ],
        design_intent="one-piece phone stand with base, vertical support, mounting holes",
    ),
    FamilyTemplate(
        family="spur_gear",
        base_type="cylinder_base",
        base_dimension_map={"outer_diameter": "outer_diameter", "length": "height"},
        feature_specs=[
            TemplateFeatureSpec(id="center_bore", type="through_hole", operation="remove", dimension_map={"diameter": "hole_diameter"}, placement_rule="center", extent="through", evidence="gear center bore"),
            TemplateFeatureSpec(id="hub_reference", type="boss_cylinder", operation="add", dimension_map={"diameter": "hub_outer_diameter", "height": "height"}, placement_rule="center", evidence="gear hub"),
        ],
        design_intent="spur gear blank with bore and hub; gear teeth are not supported",
        unsupported_requirements=["spur_gear_teeth"],
    ),
]


class FamilyTemplateRegistry:
    def __init__(self, templates: list[FamilyTemplate] | None = None) -> None:
        self._templates: dict[str, FamilyTemplate] = {}
        for template in templates or DEFAULT_TEMPLATES:
            self.register(template)

    def register(self, template: FamilyTemplate) -> None:
        if template.family in self._templates:
            raise ValueError(f"Family template already registered: {template.family}")
        self._templates[template.family] = template

    def get(self, family: str) -> FamilyTemplate | None:
        return self._templates.get(family)

    def all(self) -> list[FamilyTemplate]:
        return list(self._templates.values())


FAMILY_TEMPLATES = FamilyTemplateRegistry()


def detect_family(description: str) -> str:
    text = description.lower()
    if "\u9f7f\u8f6e" in text or "gear" in text:
        return "spur_gear"
    if "\u6cd5\u5170" in text or "flange" in text:
        return "flange"
    if "\u7ba1" in text or "pipe" in text or "tube" in text:
        return "tube"
    if "\u6447\u6746" in text or "rocker" in text or "\u8fde\u6746" in text or "link" in text:
        return "link_plate"
    if "\u624b\u673a\u652f\u67b6" in text or "phone stand" in text or "\u652f\u67b6" in text or "stand" in text:
        return "phone_stand"
    return "unknown"


def build_template_plan(
    description: str,
    parsed: dict[str, float] | None = None,
    language: str = "zh",
    mode: str = "strict",
    smart_fill_policy: str = "limited_fill",
) -> FeaturePlanV3 | None:
    family = detect_family(description)
    template = FAMILY_TEMPLATES.get(family)
    if template is None:
        return None
    clues = parsed or extract_clues(description)
    base, missing = _template_base(template, clues, language)
    if base is None:
        return None
    features, unresolved = _template_features(template, clues, base.id, language)
    plan = FeaturePlanV3(part_family=family, base_feature=base, features=features, autonomy_policy=smart_fill_policy if mode == "smart" else None)
    plan.unresolved.extend(unresolved)
    plan.design_intent = template.design_intent
    plan.design_intent_details = infer_design_intent(description, family, template.required_capabilities, template.unsupported_requirements, language)
    plan.evidence = build_evidence_set(description, None, False, language)
    if mode == "smart":
        _fill_smart_defaults(plan, clues, language)
    normalize_feature_plan(plan)
    if template.unsupported_requirements:
        plan.unresolved.append({"feature": family, "reason": "unsupported capability: " + ", ".join(template.unsupported_requirements)})
    plan.completeness = _completeness(plan)
    return plan


def _fill_smart_defaults(plan: FeaturePlanV3, clues: dict[str, float], language: str) -> None:
    for feature in plan.features:
        for name, dim in feature.dimensions.items():
            if dim.value is not None:
                continue
            default = _smart_default(plan.part_family, feature.type, name, clues)
            if default is not None:
                feature.dimensions[name] = _dimension(default, f"smart default for {feature.id}.{name}", "assumption", False)
    if plan.part_family == "spur_gear" and plan.base_feature is not None:
        module = clues.get("module")
        teeth = clues.get("tooth_count")
        if module and teeth:
            pitch = module * teeth
            outer = pitch + 2 * module
            if plan.base_feature.dimensions.get("outer_diameter") is None or plan.base_feature.dimensions["outer_diameter"].value is None:
                plan.base_feature.dimensions["outer_diameter"] = _dimension(outer, "derived from module and tooth count", "derived", False)
    if plan.part_family == "tube" and plan.base_feature is not None:
        inner = plan.base_feature.dimensions.get("inner_diameter")
        groove = next((f for f in plan.features if f.type == "internal_annular_groove"), None)
        if groove is not None and inner and inner.value:
            if groove.dimensions.get("axial_width") is None or groove.dimensions["axial_width"].value is None:
                groove.dimensions["axial_width"] = _dimension(4.0, "smart default: 4mm groove width", "assumption", False)
            if groove.dimensions.get("groove_depth") is None or groove.dimensions["groove_depth"].value is None:
                groove.dimensions["groove_depth"] = _dimension(2.0, "smart default: 2mm groove depth", "assumption", False)
            if groove.dimensions.get("z_start") is None or groove.dimensions["z_start"].value is None:
                groove.dimensions["z_start"] = _dimension(20.0, "smart default: 20mm from face", "assumption", False)


def fill_smart_defaults(plan: FeaturePlanV3, clues: dict[str, float], language: str = "zh") -> None:
    _fill_smart_defaults(plan, clues, language)

def _smart_default(family: str, feature_type: str, name: str, clues: dict[str, float]) -> float | None:
    if family == "flange" and feature_type == "circular_pattern" and name == "pitch_radius":
        return clues.get("pitch_circle_diameter", 80.0) / 2.0 if clues.get("pitch_circle_diameter") else 40.0
    if feature_type == "through_hole" and name == "diameter":
        return clues.get("metric_thread", 6.0) if clues.get("metric_thread") else 6.0
    if family == "link_plate" and name in {"end_diameter_1", "end_diameter_2"}:
        return 8.0 if name.endswith("2") else 12.0
    return None


def _completeness(plan: FeaturePlanV3) -> dict[str, Any]:
    all_features = ([plan.base_feature] if plan.base_feature else []) + list(plan.features)
    modeled = sum(1 for f in all_features if f.execution_status == "modeled")
    skipped = sum(1 for f in all_features if f.execution_status == "skipped")
    failed = sum(1 for f in all_features if f.execution_status == "failed")
    unresolved = len(plan.unresolved) + sum(len(f.unresolved) for f in plan.features)
    total = len(all_features)
    score = round((modeled / total) * 100.0, 1) if total else 0.0
    return {
        "total_features": total,
        "modeled": modeled,
        "skipped": skipped,
        "failed": failed,
        "unresolved": unresolved,
        "score": score,
        "production_ready": total > 0 and modeled == total and unresolved == 0,
    }


def build_execution_report(
    plan: FeaturePlanV3,
    worker_report: dict[str, Any] | None,
    *,
    execution_ok: bool,
    fallback_used: bool,
    mode: str = "strict",
    language: str = "zh",
) -> ExecutionReport:
    worker_report = worker_report or {}
    all_features = ([plan.base_feature] if plan.base_feature else []) + list(plan.features)
    modeled = sum(1 for f in all_features if f.execution_status == "modeled")
    skipped = [f.id for f in all_features if f.execution_status == "skipped"]
    failed = [f.id for f in all_features if f.execution_status == "failed"]
    unresolved = len(plan.unresolved) + sum(len(f.unresolved) for f in plan.features)
    plan_complete = unresolved == 0 and not failed
    geometry_valid = execution_ok and not failed
    production_ready = (
        execution_ok and plan_complete and geometry_valid and not skipped
        and (mode == "strict" or not plan.assumptions)
    )
    completeness = _completeness(plan)
    details = [str(item) for item in worker_report.get("warnings", [])] + [
        str(item) for item in worker_report.get("skipped_features", [])
    ]
    return ExecutionReport(
        execution_ok=execution_ok,
        plan_complete=plan_complete,
        geometry_valid=geometry_valid,
        production_ready=production_ready,
        fallback_used=fallback_used,
        skipped_features=skipped,
        failed_features=failed,
        assumption_count=len(plan.assumption_details),
        completeness_score=float(completeness.get("score", 0.0)),
        engine=str(worker_report.get("engine") or "build123d"),
        details=details,
    )
