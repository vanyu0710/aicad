from __future__ import annotations

import json
import os
from typing import Any

import requests

from mechcad.api_config import normalize_minimax_base_url, normalize_minimax_protocol, normalize_role_base_url, normalize_role_protocol
from mechcad.feature_plan import normalize_ai_feature_plan, qwen_to_seed_feature_plan


SYSTEM_PROMPT = """
You generate safe Build123d Python code for auditable mechanical MVP models.
Return only Python code. Use this exact style:

from build123d import *

with BuildPart() as part:
    ...

export_step(part.part, "model.step")
export_stl(part.part, "model.stl")

Rules:
- Do not use Part(), Mesh, Color, import_brep, ExportStep, ExportStl, or object.export_step().
- Do not use lowercase mode; use Mode.ADD or Mode.SUBTRACT if needed.
- Do not rely on undefined names such as Axis unless imported through `from build123d import *`.
- Prefer simple primitives: Box, Cylinder, Hole, Locations, PolarLocations, fillet, chamfer.
- Prefer only those simple primitives. Do not use BuildSketch, Polyline, make_face, revolve,
  revolution_arcs, edge_near, custom selectors, or ad-hoc topology queries.
- For through bores in tubes, prefer `Cylinder(radius=inner_diameter / 2, height=..., mode=Mode.SUBTRACT)`.
  Do not call `Hole(diameter=...)`; Build123d Hole takes a radius-style positional argument in this app.
- For annular grooves on tubes, use short coaxial `Cylinder(..., mode=Mode.SUBTRACT)` cuts or leave
  `# UNRESOLVED:` if the axial width is missing. Do not use BuildSketch/Rectangle/extrude for tube grooves.
- Keep geometry conservative and manufacturable.
- Use millimeters as numeric units.
- Do not use file IO except export_step/export_stl.
- Do not use networking, subprocess, eval, exec, dynamic imports, or environment variables.
- Treat every dimension and feature in the Qwen JSON as a requirements ledger. Model every supported
  feature; do not replace a diameter step, shoulder, ring, or groove with a fillet/chamfer.
- For axisymmetric parts, prefer a single ordered 2D axial cross-section revolved 360 degrees, or a
  sequence of coaxial cylinders with explicit axial positions. Cut the full bore after forming the exterior.
- Preserve decimal dimensions exactly. Derive a dimension only when the arithmetic is unambiguous.
- If a feature lacks enough dimensions, omit only that feature and add a Python comment beginning
  `# UNRESOLVED:` explaining the missing value. Do not guess silently.
- Never assign a numeric fallback to an unresolved dimension. Do not reinterpret the difference
  between two diameters as an axial width or position.
- `# UNRESOLVED:` means omit that operation entirely. Never add a placeholder, assumed, minimal,
  representative, or default numeric value after marking a dimension unresolved.
- For a coaxial tube along Z, build additive Cylinder primitives at explicit Z Locations and subtract
  coaxial Cylinder primitives with explicit heights. Keep the construction minimal so STEP/STL export
  completes within 30 seconds.
- Every bore, hole, groove, pocket, or other material-removal Cylinder must explicitly use
  `mode=Mode.SUBTRACT`. Verify this argument is present on every subtractive primitive.
- Do not call fillet/chamfer unless the target edge selection is simple and known to be non-empty.
  If selection is uncertain, omit it with `# UNRESOLVED:` instead of risking the whole model export.
- Add short comments mapping each modeled operation to the source dimension/feature.
- Follow this engineering construction method internally before writing code:
  1. Read the verified plan and feature specification; establish a right-handed CAD coordinate system.
  2. Select the simplest explicit base body (Box, Cylinder, or a touching union of those primitives)
     that matches the part's bounding form. Build it first.
  3. Apply explicit additive features next: bosses, flanges, tabs, ribs represented by supported,
     touching Box/Cylinder primitives at stated locations.
  4. Apply subtractive features in dependency order: through/blind holes, counterbores, pockets,
     rectangular slots, keyways, annular grooves. Use `mode=Mode.SUBTRACT` on each material removal.
  5. Create linear hole patterns with explicit `Locations`; create bolt circles with `PolarLocations`
     only when count and radius/angle are explicit. Do not infer pattern counts or spacing.
  6. Apply a fillet/chamfer only after the parent geometry exists and only with a reliable simple edge
     selector. A failed cosmetic edge treatment must never prevent export of the principal model.
  7. Check that each added body touches the main solid, every removal intersects it, all feature
     placements are referenced to an explicit datum, and final outer dimensions remain correct.
- Supported modeling patterns include: plates/blocks with holes, slots and pockets; L/U brackets
  made from touching boxes; flanges with bores and bolt circles; shafts/bushings/tubes with steps,
  bores and annular grooves; and simple bosses/ribs formed from boxes or cylinders. For any feature
  needing unsupported sketches, sweeps, splines, threads, gears, sheet-metal bends, or freeform
  surfaces, preserve the base model and emit a `# UNRESOLVED:` comment naming the feature and missing
  supported construction. Do not fake it with an unrelated primitive.
- Evidence priority: use numeric geometry from the verified model plan first. For features the plan
  does not yet deterministically support, you MAY use a non-null, named dimension inside
  `Qwen analysis JSON.sketch.features` only when that feature also has a type, placement/location,
  and drawing evidence. Raw annotations alone are not CAD dimensions. If plan and feature JSON
  conflict, model neither conflicting feature and add `# UNRESOLVED:`.
- Before returning, mentally check overall bounding dimensions, bore diameter, wall thickness, axial
  feature order, and that all additive solids touch the main body.
"""


FEATURE_PLAN_PROMPT = """
You are a mechanical CAD planning engineer. Convert visual analysis into a SolidWorks-style
feature tree JSON for a deterministic Build123d executor. Return JSON only; never return Python.

Goal:
- Build the part as a CAD feature tree: datum -> base feature -> additive features -> subtractive
  features -> patterns -> fillets/chamfers.
- The executor supports these feature types only:
  base_feature.type: box_base, cylinder_base, hollow_cylinder, revolved_axial_profile.
  features[].type: through_hole, blind_hole, counterbore_hole, rectangular_slot,
  rectangular_pocket, annular_groove, boss_cylinder, rectangular_pad, rib_box,
  linear_pattern, circular_pattern, fillet, chamfer.

Required JSON shape:
{
  "schema_version": "2.0",
  "units": "mm",
  "coordinate_system": {"origin": "...", "main_axis": "X|Y|Z", "datums": [...]},
  "part_family": "...",
  "base_feature": {
    "id": "base",
    "type": "box_base|cylinder_base|hollow_cylinder|revolved_axial_profile",
    "operation": "base",
    "dimensions": {
      "length": {"value": number|null, "unit": "mm", "evidence": "..."},
      "width": {"value": number|null, "unit": "mm", "evidence": "..."},
      "height": {"value": number|null, "unit": "mm", "evidence": "..."},
      "outer_diameter": {"value": number|null, "unit": "mm", "evidence": "..."},
      "inner_diameter": {"value": number|null, "unit": "mm", "evidence": "..."}
    },
    "placement": {"reference": "datum name", "x": 0, "y": 0, "z": 0, "axis": "Z"},
    "evidence": "which drawing marks prove the base"
  },
  "features": [
    {
      "id": "feature_id",
      "type": "supported feature type",
      "operation": "add|remove|modify|pattern",
      "dimensions": {"named_dimension": {"value": number|null, "unit": "mm", "evidence": "..."}},
      "placement": {"reference": "datum or parent feature", "x": number|null, "y": number|null, "z": number|null, "axis": "X|Y|Z"},
      "extent": "through|blind|symmetric|null",
      "pattern": null,
      "depends_on": ["base"],
      "evidence": "source marks",
      "unresolved": []
    }
  ],
  "unresolved": [{"feature": "...", "reason": "..."}],
  "self_checks": {"dimension_binding": "...", "feature_order": "...", "cad_ready": true|false}
}

Method:
1. Read all dimensions as a ledger. A visible number is not CAD geometry until you know what it
   measures. Never invent a number, fallback, nominal size, or representative placeholder.
2. Pick the simplest explicit base body. For tubes, shafts and bushings, prefer hollow_cylinder or
   revolved_axial_profile with the main axis on Z and origin at the bottom end center.
3. Decompose physical features in dependency order. A groove, shoulder, slot, hole, pocket, boss,
   rib or flange must be a separate feature with its own size, location and evidence.
4. For an annular groove or end step, provide z_start or axial_start_from_bottom, axial_width, and
   reduced_outer_diameter. If the feature is at top_end and has axial_width, set z_start =
   overall_length - axial_width only when overall_length is explicit.
5. For holes/slots/pockets, provide diameter or length/width/depth plus placement from a datum.
   Through holes must have axis and location. Patterns need count and radius/spacing.
6. Do not bind one drawing number to two conflicting dimensions. If a visible diameter might be a
   top outside diameter or an inner bore diameter, choose neither unless Qwen evidence proves one.
   Put a user-facing unresolved item that asks which interpretation is correct.
7. User wording is design intent, not dimension evidence. If the description says "slot" but Qwen
   does not provide slot width, start/end, angular position, and through/blind state, list the slot
   in unresolved with concrete questions. Do not create an executable slot from text alone.
8. If anything is missing, omit only that exact feature from executable certainty and add it to
   unresolved. Do not fake threads, gears, bends, splines or freeform details.
9. Self-check before returning: all dimensions are in mm, every modeled feature has evidence,
   subtractive features intersect the base, additive features touch the base, and the order is buildable.
""".strip()


def generate_build123d_code(
    image_base64: str,
    description: str,
    qwen_json: dict[str, Any],
    model_plan: dict[str, Any],
    settings,
) -> str:
    api_key = settings.api_config.planner_api_key or settings.api_config.minimax_api_key or os.getenv("MECHCAD_PLANNER_API_KEY") or os.getenv("MINIMAX_API_KEY")
    if api_key:
        try:
            protocol = normalize_role_protocol(
                settings.api_config.planner_protocol or settings.api_config.minimax_protocol or os.getenv("MECHCAD_PLANNER_PROTOCOL") or os.getenv("MINIMAX_API_PROTOCOL"),
                settings.api_config.planner_base_url or settings.api_config.minimax_base_url or os.getenv("MECHCAD_PLANNER_BASE_URL") or os.getenv("MINIMAX_BASE_URL"),
            )
            if protocol == "anthropic":
                return _call_minimax_anthropic(api_key, description, qwen_json, model_plan, settings)
            return _call_minimax_openai(api_key, image_base64, description, qwen_json, model_plan, settings)
        except Exception:
            if settings.force_real_api:
                raise
    return _local_template(description, qwen_json, settings.quality_mode)


def generate_feature_plan(
    image_base64: str,
    description: str,
    qwen_json: dict[str, Any],
    model_plan: dict[str, Any],
    settings,
) -> dict[str, Any]:
    api_key = settings.api_config.planner_api_key or settings.api_config.minimax_api_key or os.getenv("MECHCAD_PLANNER_API_KEY") or os.getenv("MINIMAX_API_KEY")
    if api_key:
        try:
            protocol = normalize_role_protocol(
                settings.api_config.planner_protocol or settings.api_config.minimax_protocol or os.getenv("MECHCAD_PLANNER_PROTOCOL") or os.getenv("MINIMAX_API_PROTOCOL"),
                settings.api_config.planner_base_url or settings.api_config.minimax_base_url or os.getenv("MECHCAD_PLANNER_BASE_URL") or os.getenv("MINIMAX_BASE_URL"),
            )
            if protocol == "anthropic":
                raw = _call_minimax_feature_plan_anthropic(api_key, description, qwen_json, model_plan, settings)
            else:
                raw = _call_minimax_feature_plan_openai(api_key, image_base64, description, qwen_json, model_plan, settings)
            plan = normalize_ai_feature_plan(raw)
            plan["source"] = "minimax_feature_plan"
            return plan
        except Exception:
            if settings.force_real_api:
                raise
    return qwen_to_seed_feature_plan(qwen_json, model_plan)


def _call_minimax_feature_plan_openai(
    api_key: str,
    image_base64: str,
    description: str,
    qwen_json: dict[str, Any],
    model_plan: dict[str, Any],
    settings,
) -> dict[str, Any]:
    raw_url = settings.api_config.planner_base_url or settings.api_config.minimax_base_url or os.getenv("MECHCAD_PLANNER_BASE_URL") or os.getenv("MINIMAX_BASE_URL")
    base_url = normalize_role_base_url(raw_url) or normalize_minimax_base_url(raw_url, "openai")
    model = settings.minimax_model or settings.api_config.planner_model or settings.api_config.minimax_model or os.getenv("MECHCAD_PLANNER_MODEL", os.getenv("MINIMAX_MODEL", "MiniMax-M3"))
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": FEATURE_PLAN_PROMPT},
            {
                "role": "user",
                "content": (
                    f"User function description:\n{description}\n\n"
                    f"Qwen analysis JSON:\n{json.dumps(qwen_json, ensure_ascii=False, indent=2)}\n\n"
                    "Conservative deterministic seed plan:\n"
                    f"{json.dumps(model_plan, ensure_ascii=False, indent=2)}"
                ),
            },
        ],
        "temperature": min(settings.temperature, 0.2),
        "response_format": {"type": "json_object"},
    }
    response = _post_openai_chat_with_v1_retry(base_url, api_key, payload, timeout=90)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    return json.loads(_strip_json_fence(content))


def _call_minimax_feature_plan_anthropic(
    api_key: str,
    description: str,
    qwen_json: dict[str, Any],
    model_plan: dict[str, Any],
    settings,
) -> dict[str, Any]:
    raw_url = settings.api_config.planner_base_url or settings.api_config.minimax_base_url or os.getenv("MECHCAD_PLANNER_BASE_URL") or os.getenv("MINIMAX_BASE_URL")
    base_url = normalize_role_base_url(raw_url) or normalize_minimax_base_url(raw_url, "anthropic")
    model = settings.minimax_model or settings.api_config.planner_model or settings.api_config.minimax_model or os.getenv("MECHCAD_PLANNER_MODEL", os.getenv("MINIMAX_MODEL", "MiniMax-M3"))
    payload = {
        "model": model,
        "system": FEATURE_PLAN_PROMPT + "\nReturn JSON only.",
        "messages": [
            {
                "role": "user",
                "content": (
                    f"User function description:\n{description}\n\n"
                    f"Qwen analysis JSON:\n{json.dumps(qwen_json, ensure_ascii=False, indent=2)}\n\n"
                    "Conservative deterministic seed plan:\n"
                    f"{json.dumps(model_plan, ensure_ascii=False, indent=2)}"
                ),
            }
        ],
        "temperature": min(settings.temperature, 0.2),
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
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    text = "\n".join(
        block.get("text", "")
        for block in data.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    return json.loads(_strip_json_fence(text))


def _call_minimax_openai(
    api_key: str,
    image_base64: str,
    description: str,
    qwen_json: dict[str, Any],
    model_plan: dict[str, Any],
    settings,
) -> str:
    raw_url = settings.api_config.planner_base_url or settings.api_config.minimax_base_url or os.getenv("MECHCAD_PLANNER_BASE_URL") or os.getenv("MINIMAX_BASE_URL")
    base_url = normalize_role_base_url(raw_url) or normalize_minimax_base_url(raw_url, "openai")
    model = settings.minimax_model or settings.api_config.planner_model or settings.api_config.minimax_model or os.getenv("MECHCAD_PLANNER_MODEL", os.getenv("MINIMAX_MODEL", "MiniMax-M2.7"))
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "User function description:\n"
                    f"{description}\n\nQwen analysis JSON:\n"
                    f"{json.dumps(qwen_json, ensure_ascii=False, indent=2)}\n\n"
                    "Verified model plan (highest-priority numeric geometry; use the Qwen feature "
                    "specification for additional fully evidenced features):\n"
                    f"{json.dumps(model_plan, ensure_ascii=False, indent=2)}"
                ),
            },
        ],
        "temperature": settings.temperature,
    }
    response = _post_openai_chat_with_v1_retry(base_url, api_key, payload, timeout=90)
    response.raise_for_status()
    return _strip_code_fence(response.json()["choices"][0]["message"]["content"])


def _call_minimax_anthropic(
    api_key: str,
    description: str,
    qwen_json: dict[str, Any],
    model_plan: dict[str, Any],
    settings,
) -> str:
    raw_url = settings.api_config.planner_base_url or settings.api_config.minimax_base_url or os.getenv("MECHCAD_PLANNER_BASE_URL") or os.getenv("MINIMAX_BASE_URL")
    base_url = normalize_role_base_url(raw_url) or normalize_minimax_base_url(raw_url, "anthropic")
    model = settings.minimax_model or settings.api_config.planner_model or settings.api_config.minimax_model or os.getenv("MECHCAD_PLANNER_MODEL", os.getenv("MINIMAX_MODEL", "MiniMax-M2.7"))
    payload = {
        "model": model,
        "system": SYSTEM_PROMPT + "\nReturn only Python code, no markdown.",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Generate Build123d code from the verified model plan and the Qwen feature specification "
                    "below. The plan is the highest-priority authority. You may additionally model a Qwen "
                    "feature only when it has an explicit type, location/placement, non-null named dimensions, "
                    "and drawing evidence. Never use an unbound raw annotation as geometry. Build the base body "
                    "first, then apply features in dependency order. Every unresolved feature must remain omitted "
                    "and be written as a # UNRESOLVED: comment. Return only the final code.\n\n"
                    "Note: The original image has already been analyzed by Qwen2.5-VL; "
                    "use the JSON as the visual source of truth.\n\n"
                    f"User function description:\n{description}\n\n"
                    f"Qwen analysis JSON:\n{json.dumps(qwen_json, ensure_ascii=False, indent=2)}"
                    f"\n\nVerified model plan JSON:\n{json.dumps(model_plan, ensure_ascii=False, indent=2)}"
                ),
            }
        ],
        "temperature": settings.temperature,
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
        timeout=90,
    )
    response.raise_for_status()
    data = response.json()
    text_blocks = [
        block.get("text", "")
        for block in data.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return _strip_code_fence("\n".join(text_blocks))


def _post_openai_chat_with_v1_retry(base_url: str, api_key: str, payload: dict[str, Any], timeout: int) -> requests.Response:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    urls = [f"{base_url}/chat/completions"]
    if not base_url.rstrip("/").endswith("/v1"):
        urls.append(f"{base_url}/v1/chat/completions")
    last_response: requests.Response | None = None
    for index, url in enumerate(urls):
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        last_response = response
        content_type = response.headers.get("content-type", "").lower()
        if response.ok and "json" in content_type:
            return response
        if response.ok and "text/html" in content_type and index == 0 and len(urls) > 1:
            continue
        return response
    assert last_response is not None
    return last_response


def _local_template(description: str, qwen_json: dict[str, Any], quality_mode: str) -> str:
    family = qwen_json.get("domain", {}).get("part_family", "连接板")
    if family == "支架":
        return _bracket_code()
    if family == "法兰":
        return _flange_code()
    if family == "轴套":
        return _bushing_code()
    if family == "夹具":
        return _clamp_code()
    return _plate_code()


def _plate_code() -> str:
    return '''
from build123d import *

with BuildPart() as part:
    Box(80, 45, 8)
    with Locations((-25, 0, 4), (25, 0, 4)):
        Hole(5)
    fillet(part.edges().filter_by(Axis.Z), radius=3)

export_step(part.part, "model.step")
export_stl(part.part, "model.stl")
'''


def _bracket_code() -> str:
    return '''
from build123d import *

with BuildPart() as part:
    Box(70, 12, 48, align=(Align.CENTER, Align.MIN, Align.MIN))
    Box(70, 48, 10, align=(Align.CENTER, Align.MIN, Align.MIN))
    with Locations((-22, 24, 10), (22, 24, 10)):
        Hole(4)
    with Locations((-22, 6, 32), (22, 6, 32)):
        Hole(4, depth=16)
    fillet(part.edges(), radius=1.5)

export_step(part.part, "model.step")
export_stl(part.part, "model.stl")
'''


def _flange_code() -> str:
    return '''
from build123d import *

with BuildPart() as part:
    Cylinder(radius=32, height=8)
    Hole(10)
    with PolarLocations(radius=22, count=6):
        Hole(3.5)
    fillet(part.edges(), radius=1)

export_step(part.part, "model.step")
export_stl(part.part, "model.stl")
'''


def _bushing_code() -> str:
    return '''
from build123d import *

with BuildPart() as part:
    Cylinder(radius=18, height=36)
    Hole(8)
    fillet(part.edges(), radius=1)

export_step(part.part, "model.step")
export_stl(part.part, "model.stl")
'''


def _clamp_code() -> str:
    return '''
from build123d import *

with BuildPart() as part:
    Box(68, 34, 14)
    Cylinder(radius=11, height=74, rotation=(0, 90, 0), mode=Mode.SUBTRACT)
    with Locations((-24, 0, 7), (24, 0, 7)):
        Hole(4)
    fillet(part.edges(), radius=1.5)

export_step(part.part, "model.step")
export_stl(part.part, "model.stl")
'''


def _strip_code_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    if text.startswith("python\n"):
        text = text.split("\n", 1)[1]
    return text.strip()


def _strip_json_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        text = text.rsplit("```", 1)[0]
    if text.startswith("json\n"):
        text = text.split("\n", 1)[1]
    return text.strip()
