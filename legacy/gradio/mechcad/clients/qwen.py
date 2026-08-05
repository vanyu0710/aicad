from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests

from mechcad.api_config import normalize_dashscope_base_url, normalize_role_base_url, normalize_role_protocol


def analyze_sketch(
    image_path: Path,
    image_base64: str,
    description: str,
    settings,
    image_metrics: dict[str, Any],
) -> dict[str, Any]:
    api_key = (
        settings.api_config.vision_api_key
        or settings.api_config.dashscope_api_key
        or os.getenv("MECHCAD_VISION_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
    )
    if api_key:
        try:
            return _call_qwen(api_key, image_base64, description, settings)
        except Exception as exc:
            if settings.force_real_api:
                raise
            fallback = _local_analysis(description, settings.part_family, image_metrics)
            fallback["api_error"] = str(exc)
            return fallback
    if settings.force_real_api:
        raise RuntimeError("A vision model API key is required when force_real_api is enabled.")
    return _local_analysis(description, settings.part_family, image_metrics)


def _call_qwen(api_key: str, image_base64: str, description: str, settings) -> dict[str, Any]:
    configured_url = settings.api_config.vision_base_url or os.getenv("MECHCAD_VISION_BASE_URL")
    base_url = normalize_role_base_url(configured_url) or normalize_dashscope_base_url(
        settings.api_config.dashscope_base_url or os.getenv("DASHSCOPE_BASE_URL")
    )
    model = settings.qwen_model or settings.api_config.vision_model or settings.api_config.qwen_model or os.getenv(
        "MECHCAD_VISION_MODEL", os.getenv("QWEN_MODEL", "qwen2.5-vl-32b-instruct")
    )
    protocol = normalize_role_protocol(
        settings.api_config.vision_protocol or os.getenv("MECHCAD_VISION_PROTOCOL"), configured_url
    )
    prompt = """
You are the inspection engineer between a mechanical drawing and a CAD system.
Create an auditable, CAD-actionable feature specification; do not describe the drawing loosely.
Inspect every view, section, leader, dimension line, extension line, diameter/radius symbol, note,
tolerance, profile line, and small dimension. Return exactly one JSON object with exactly these
top-level keys: view_identification, sketch, domain, uncertainties, consistency_checks.

Use millimeters. A dimension object is {"value": number|null, "unit": "mm", "evidence": string,
"view": string, "confidence": number}. When a value is uncertain, retain its visible text and use
null for its semantic role. Never invent values.

`view_identification` must have `views` (name, type, purpose) and `primary_modeling_view`.
`sketch` must have these fields:
- `shape`: concise engineering description; `part_family`: axisymmetric_tube|plate|bracket|other.
- `raw_annotations`: every visible annotation BEFORE interpretation. Each item has text,
  numeric_value, unit, symbol (diameter|radius|linear|tolerance|none), dimension_line_axis
  (axial|radial_or_diametral|unknown), endpoints_or_region, view, confidence.
- `dimensions`: named base dimensions, including overall_length, outer_diameter, inner_diameter
  whenever visible. Preserve each as a dimension object.
- `features`: a list, one object per physical feature. Every feature has id, type
  (annular_end_step|annular_groove|through_bore|shoulder|fillet|chamfer|other), location
  (top_end|bottom_end|middle|through|unknown), axial_start_from_bottom, axial_length,
  outer_diameter, inner_diameter, fillet_radius, supporting_dimensions, profile_interpretation,
  evidence, confidence, unresolved. The dimension fields are dimension objects or null.
- `axial_profile`: a bottom-to-top list. Each item has order_from_bottom, z_start, z_end,
  outer_diameter, inner_diameter, source_feature_id. Its dimension fields are dimension objects or null.
- `relationships`: concentric, symmetric, through, tangent, etc.
`domain` must include part_family, component_type, manufacturing_intent.
`consistency_checks` must include dimension_binding, missing_for_exact_model, conflicts, cad_ready.

General mechanical-reading method (apply this before choosing a part family):
1. Register the views. Identify front/top/side/section/detail views, match common centerlines and
   projected edges, and use the section view to determine material interior. Do not merge dimensions
   from unrelated views without naming the feature and view that connect them.
2. Establish a CAD datum system. State the natural origin, primary axes, principal symmetry/rotation
   axes, and the faces/centerlines used as placement references. Prefer bottom face and part center
   planes unless the drawing establishes another datum.
3. Find the base body first: rectangular plate/block, cylindrical shaft/tube, revolved profile,
   extrusion, flange, U/L bracket, or compound casting. Record its bounding dimensions separately
   from later features. For ambiguous freehand sketches, choose the simplest directly evidenced base.
4. Decompose the remaining geometry into independent manufacturing features, in dependency order:
   additive bosses/ribs/flanges; subtractive through holes/blind holes/counterbores/countersinks;
   pockets/slots/keyways; end steps/annular grooves; patterns; then fillets/chamfers. A visual edge
   is not itself a feature until its material operation and dimensions are established.
5. For each feature, use `type` plus these additional generic fields when applicable: `operation`
   (add|remove|modify), `geometry` (box|cylinder|hole|slot|pocket|rib|revolved_profile|pattern|other),
   `placement` (reference, x, y, z, axis, rotation), `dimensions` (named dimension objects),
   `extent` (through|blind|symmetric), `pattern` (count, spacing, angle), and `depends_on` (feature ids).
   Keep the axisymmetric-specific fields too when the feature is a tube/shaft end profile.
6. Distinguish dimensions by function: overall/bounding, location from datum, size, depth, diameter,
   radius, angle, thread/tolerance, and repeated-pattern spacing. A diameter difference is radial
   geometry, not an axial width. A dimension is CAD-actionable only when its value AND the two things
   it measures are known.
7. Treat hidden lines, hatching, centerlines, section arrows, leaders, and notes as evidence. Detect
   through versus blind holes from both views and section hatching. Preserve tolerances, threads and
   surface notes as manufacturing metadata even when this MVP cannot model them.
8. Build a constraint ledger: check that each feature has enough size, location, direction and depth
   information. Flag missing datum, mirrored-side ambiguity, unclear depth, conflicting dimensions,
   non-manifold overlap risk, and impossible wall thickness. Never fill gaps with typical dimensions.
9. Use the function description only to disambiguate a visible choice (for example, mounting holes or
   a through bore). The drawing remains numeric authority. If text conflicts with a dimension, report
   the conflict rather than changing the dimension.
10. Separate drawing facts from user intent. If the user says "slot" but the sketch does not clearly
   show slot extension lines, slot width, angular position and through/blind state, record the user's
   wording as intent only; do not create a confident slot feature from text alone.
11. Never use one visible diameter for two incompatible roles. A diameter callout can be outer diameter,
   inner diameter, counterbore diameter or reduced step diameter only after its leader/extension lines
   prove that role. If a value such as ⌀41.20 may be either top outside diameter or bore diameter,
   put both interpretations in unresolved and leave the semantic dimension null.

Cover these common feature families when visible: plates with hole patterns and slots; L/U brackets
with bends/ribs; flanges with bolt circles; shafts, bushings and tubes with steps/bores/keyways;
rectangular blocks with pockets/counterbores; housings with cavities; simple revolved parts; and
linear/circular repeated features. For complex splines, threads, gears, welds, or unclear freeform
surfaces, identify them accurately and mark exact geometry unresolved rather than approximating them.

Critical procedure for a sectioned axisymmetric tube:
1. Transcribe ALL visible labels first, including small labels such as 1, 1.4, and 10.
2. Identify the main axis. A dimension parallel to the tube's long axis is axial. A horizontal
   dimension spanning a rotational profile is radial_or_diametral.
3. A value marked with a diameter symbol is a diameter. A horizontal value spanning a rotational
   profile is normally a diameter even if the symbol is faint. Never name 43.84 or 41.03 a slot
   width just because it is near a groove: state exactly which two extension lines or surfaces it spans.
4. Create separate features for the top and bottom ends. Bind an end feature's axial_length to the
   small axial dimension whose extension lines span that end region, for example 10. Do not infer an
   axial length from the difference between two diameters.
5. Construct the `axial_profile` mentally in this order: bottom end feature, central tube, top end
   feature. State whether each step adds exterior material or removes exterior material.
6. Do not assume both ends are symmetric. Mark symmetry only when the drawing explicitly establishes it.
7. A leader such as R1 is a fillet requirement at the indicated corner, not a groove width.
8. If a legible dimension has an ambiguous role, put it in raw_annotations and supporting_dimensions,
   leave the corresponding semantic field null, and explain competing interpretations in unresolved
   and uncertainties. Never silently omit it.
9. For hollow tubes, do not assume an inner diameter merely because a smaller diameter is visible near
   an end step. Bind inner_diameter only when the annotation, section hatching or visible bore edges
   prove it measures the hole/opening.

Before responding, verify every visible number appears in raw_annotations, every feature has a
location, and every non-null feature dimension cites drawing evidence. Output JSON only.
""".strip()
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt + "\nUser description: " + description},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                ],
            }
        ],
        "temperature": 0.1,
    }
    if protocol == "anthropic":
        payload = {
            "model": model,
            "system": prompt,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "User description: " + description},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_base64}},
            ]}],
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        response = requests.post(
            f"{base_url}/v1/messages",
            headers={
                "X-Api-Key": api_key,
                "Authorization": f"Bearer {api_key}",
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        content = "\n".join(
            block.get("text", "") for block in response.json().get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        )
    else:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(_strip_json_fence(content))
    if isinstance(parsed, str):
        parsed = json.loads(_strip_json_fence(parsed))
    if not isinstance(parsed, dict):
        raise ValueError("Qwen response is valid JSON but not a JSON object.")
    parsed = _normalize_qwen_schema(parsed)
    parsed["source"] = "api"
    return parsed


def _local_analysis(description: str, part_family: str, metrics: dict[str, Any]) -> dict[str, Any]:
    inferred = _infer_family(description, part_family)
    return {
        "source": "local_fallback",
        "view_identification": {
            "likely_view": "single orthographic or perspective sketch",
            "confidence": 0.45,
        },
        "sketch": {
            "detected_primitives": ["outer block/profile", "possible holes", "rounded edges"],
            "image_metrics": metrics,
        },
        "domain": {
            "part_family": inferred,
            "manufacturing_intent": "concept CAD / 3D printing / early mechanical mockup",
        },
        "uncertainties": [
            "尺寸未标注，使用默认毫米级比例",
            "孔径和厚度根据零件类型估算",
            "草图视角可能导致部分特征缺失",
        ],
        "consistency_checks": {
            "has_user_function": bool(description.strip()),
            "safe_for_template_generation": True,
            "needs_user_review": True,
        },
    }


def _infer_family(description: str, part_family: str) -> str:
    if part_family and part_family != "自动判断":
        return part_family
    text = description.lower()
    mapping = {
        "bracket": "支架",
        "支架": "支架",
        "孔": "带孔板",
        "plate": "连接板",
        "连接": "连接板",
        "法兰": "法兰",
        "flange": "法兰",
        "夹": "夹具",
        "clamp": "夹具",
        "轴": "轴套",
        "bushing": "轴套",
    }
    for key, value in mapping.items():
        if key in text:
            return value
    return "连接板"


def _strip_json_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _normalize_qwen_schema(parsed: dict[str, Any]) -> dict[str, Any]:
    domain = parsed.get("domain")
    if isinstance(domain, str):
        parsed["domain"] = {"part_family": domain}
    elif not isinstance(domain, dict):
        parsed["domain"] = {"part_family": "未知"}

    uncertainties = parsed.get("uncertainties")
    if isinstance(uncertainties, str):
        parsed["uncertainties"] = [uncertainties]
    elif not isinstance(uncertainties, list):
        parsed["uncertainties"] = []

    checks = parsed.get("consistency_checks")
    if isinstance(checks, str):
        parsed["consistency_checks"] = {"summary": checks}
    elif not isinstance(checks, dict):
        parsed["consistency_checks"] = {}

    if not isinstance(parsed.get("view_identification"), dict):
        parsed["view_identification"] = {"summary": str(parsed.get("view_identification", ""))}
    if not isinstance(parsed.get("sketch"), dict):
        parsed["sketch"] = {"summary": str(parsed.get("sketch", ""))}
    sketch = parsed["sketch"]
    for key in ("raw_annotations", "features", "axial_profile", "relationships"):
        if not isinstance(sketch.get(key), list):
            sketch[key] = []
    if not isinstance(sketch.get("dimensions"), dict):
        sketch["dimensions"] = {}
    return parsed
