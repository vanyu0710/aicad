# Capability Runtime (v0.5 Phase 1)

The capability layer is a static, machine-readable contract for feature edits. It answers one question before any edit is dispatched: **is this operation and these parameters structurally allowed for this feature type?**

It does not replace the existing engineering checks. Geometry, dependencies, strict/smart policy, manufacturing constraints, and CAD execution continue to live in `validate_feature_plan()` and the Worker.

## What the layer is

- A Pydantic registry in `backend/capabilities.py`.
- A single source of truth for editable parameters, allowed operations, and supported conversions.
- A pure validation function for `FeatureEditSet` operations.
- A pure validation function for property-panel patches.

## What the layer is not

- Not a CAD executor.
- Not a dependency resolver.
- Not a geometry or manufacturability checker.
- Not a replacement for `FeaturePlanV3`, `FeatureEditSet`, process recording, snapshots, undo/redo, WebSocket events, or the CAD Worker.
- Phase 2 features (reference resolver, impact analyzer, generic LLM edit planner, branching/version control) are intentionally out of scope.

## Registered feature types

| Family | Types | Operations |
| --- | --- | --- |
| Base | `box_base`, `cylinder_base`, `hollow_cylinder` | `update` |
| Holes | `through_hole`, `blind_hole`, `counterbore_hole` | `add`, `update`, `delete`, `change_type` (hole family only) |
| Slots/pockets | `rectangular_slot`, `rectangular_pocket` | `add`, `update`, `delete`, `change_type` (slot/pocket family only) |
| Groove | `annular_groove` | `add`, `update`, `delete` |
| Additive | `boss_cylinder`, `rectangular_pad`, `rib_box` | `add`, `update`, `delete` |
| Patterns | `linear_pattern`, `circular_pattern` | `add`, `update`, `delete` |

`fillet`, `chamfer`, `thread`, `gear`, and `sheet_metal` are not registered because they are not executable in the current Worker. Edits targeting them are blocked as unknown/unsupported rather than silently accepted.

## Error codes

- `CAPABILITY_UNKNOWN_FEATURE`: feature type is not registered or cannot be resolved.
- `CAPABILITY_OPERATION_NOT_ALLOWED`: operation is not allowed for the feature type, or a conversion target is outside the allowed family.
- `CAPABILITY_PARAMETER_UNKNOWN`: parameter is not declared for the feature type.
- `CAPABILITY_PARAMETER_NOT_EDITABLE`: parameter exists but is read-only (for example `id`).
- `CAPABILITY_PARAMETER_INVALID_VALUE`: value has the wrong type or is outside the declared range.

## Edit flow example

User asks: "把中心孔直径从 5 改成 6" / "change center hole diameter from 5 to 6".

1. AI or local parser produces `FeatureEditOperation(op="update", feature_id="hole_01", dimensions={"diameter": 6})`.
2. `validate_feature_edit_set()` checks `hole_01` against the plan, resolves its type (`through_hole`), verifies `update` is allowed, and verifies `diameter` is editable and positive.
3. The operation is dispatched to the existing apply path, which produces a `completed` ProcessStep and a before/after snapshot.
4. `validate_feature_plan()` still runs afterward for geometry, dependencies, and mode-specific policy before CAD.

If the model instead sends `dimensions={"radius": 6}` or `op="add"` for a base feature, the capability layer returns structured issues. In chat edits those operations become individual `blocked` ProcessSteps while valid operations in the same set continue. In property-panel patches the API returns `422` with the structured issue list.

## Responsibility boundary

| Concern | Owner |
| --- | --- |
| Structural edit contract | capability registry |
| Feature dependency order and cycles | `validate_feature_plan()` |
| Geometry contradictions and wall thickness | `validate_feature_plan()` |
| Strict/smart policy and unconfirmed assumptions | `validate_feature_plan()` + apply paths |
| CAD execution and per-feature status | CAD Worker |
| Process timeline and audit trail | `ProcessRecorder` |

## Registering a new feature type

1. Add a `FeatureCapability` to `_build_default_capabilities()` in `backend/capabilities.py`.
2. Declare editable parameters with `value_type`, `unit`, range, and `editable` flags.
3. Declare allowed operations and, for conversions, `convert_to_types`.
4. Register the corresponding CAD executor and required dimensions in the existing validation/Worker layers.
5. Add tests in `tests/test_capabilities.py` for lookup, operations, parameters, and integration.
