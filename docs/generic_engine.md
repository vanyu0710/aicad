# Generic Modeling Core (v0.6)

MechCAD v0.6 routes every input through the same pipeline:

1. Input routing: text-only, image-only, or mixed.
2. Evidence collection: `EvidenceSet` stores every dimension with source, confidence, and confirmation state.
3. Design intent: `DesignIntent` records part family, function, datum, axis, manufacturing intent, and unsupported requirements.
4. Feature semantics and family templates: a new part family is added as data, not as pipeline code.
5. Controlled CAD execution: Build123d Worker executes only validated `FeaturePlanV3` objects.
6. Geometry acceptance: each feature is checked for an actual solid volume change; no change means `failed`, not `modeled`.
7. Reporting: `ExecutionReport` separates `execution_ok`, `plan_complete`, `geometry_valid`, and `production_ready`.

## Adding a new part family

1. Add a `FamilyTemplate` to `DEFAULT_TEMPLATES` in `backend/generic_engine.py`, or register one on `FAMILY_TEMPLATES`.
2. Choose an existing registered base type (`box_base`, `cylinder_base`, `hollow_cylinder`, or `link_plate`).
3. Map base dimensions to clue names with `base_dimension_map`.
4. Add `TemplateFeatureSpec` entries using already registered feature types such as `through_hole`, `rectangular_pad`, `circular_pattern`, or `internal_annular_groove`.
5. Set `design_intent`, `required_capabilities`, and `unsupported_requirements`.
6. Add new clue extraction patterns in `extract_clues` only if the family introduces a new dimension vocabulary.
7. Add a new capability entry in `backend/capabilities.py` only if the family needs a feature type or parameter that is not already registered.

No changes to planning orchestration, validation, the CAD Worker, or UI are required for a family that uses existing capabilities.

## Strict vs Smart

- Strict mode only accepts confirmed evidence. Missing dimensions stay unresolved and are surfaced as clarification questions.
- Smart mode may fill auditable defaults. Every inferred value is marked `source="assumption"` and `confirmed_by_user=false`, appears in `assumption_details`, and makes `production_ready=false`.
- `full_autonomous` can add explainable features, but every added feature remains visible in the feature tree and execution report.

## Unsupported capabilities

A template can declare `unsupported_requirements`, for example `spur_gear_teeth`. The plan records the unsupported capability, the capability registry reports it as unsupported, and the UI shows it explicitly. The system never pretends to model features it cannot execute.

## API

`GET /api/capabilities` returns registered feature capabilities and feature semantics for UI and audit tooling.