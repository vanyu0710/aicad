## v0.7.0 Phase 1B - Pure Validation

- Added explicit `backend/normalization.py` with idempotent `normalize_feature_plan()`; auto IDs, duplicate/missing dependency bookkeeping, non-positive dimensions, assumption aggregation, and `requires_confirmation` are no longer hidden inside Pydantic model validation.
- Removed `FeaturePlanV3.validate_engineering_contract` and `model_validator`; model construction now only performs shape validation.
- `validate_feature_plan()` is now a pure function: it no longer writes `self_checks`, rewrites `design_review`, or reorders features.
- Added `compute_feature_order()` (read-only ordering) and explicit `order_feature_plan()`; validation uses the read-only form.
- Added `apply_validation_result()` so orchestration layers explicitly write validation checks into `self_checks` and `[validation]`-prefixed design review entries.
- All plan-producing paths now call `normalize_feature_plan()` before validation/execution: initial AI planning, local fallback, chat edits, property patches, clarification answers, template plans, restored snapshots, and CAD Worker loading.
- Existing API return keys (`checks / blocking / warnings / order / base_ready`) and strict/smart semantics are unchanged.
- Added explicit mutation-regression, double-validation stability, and normalized/validated snapshot undo/redo tests; repaired previously hidden SessionStore tests caused by a misplaced `_snapshot` helper.

## v0.7.0 Phase 1A - Canonical Feature Semantics

- Added `FeatureDefinition`, `ConstraintSpec`, and `VerificationContractSpec` to the schema layer as stateless feature metadata.
- Added `backend/feature_definitions.py` with `FeatureDefinitionRegistry`, `get_feature_definition()`, and canonical definitions for all 16 supported feature types plus 6 known unsupported types.
- `CapabilityRegistry` now derives `CAPABILITIES` from the canonical registry; `feature_semantics()` preserves the legacy `/api/capabilities` shape while reading from the registry.
- Legacy validation/AI/normalization dimension tables are marked with `TODO(v0.7)` migration notes and regression tests now assert they match the canonical registry.
- Fixed drift where AI required-dimension tables were missing internal annular grooves, link plates, and linear patterns.
- Fixed a latent missing `CapabilityRegistry.all()` method used by `GET /api/capabilities`.
- Added 11 backend tests for registry coverage, invalid type rejection, duplicate registration, derived capability/semantic equivalence, and legacy-table consistency.

## v0.6.0 - Generic Modeling Core and UI Acceptance Layer

- Added a generic input/evidence layer: `InputRouter` distinguishes text-only, image-only, and mixed inputs; pure text generation no longer needs a vision model.
- Added `EvidenceSet`, `DesignIntent`, `FeatureSemantics`, and `FamilyTemplate` models with deterministic clue extraction for Chinese/English dimensions.
- Added a data-driven `FamilyTemplateRegistry` covering flange, tube with internal groove, link/rocker plate, phone stand, and spur-gear blank templates.
- Spur gear teeth are explicitly unsupported; the template and capability registry mark `spur_gear_teeth` as unsupported instead of producing a fake gear.
- The planning fallback now builds template plans with evidence, intent, assumptions, and completeness; Smart mode fills auditable defaults and Strict mode keeps missing values unresolved.
- CAD Worker reports per-feature `modeled`, `skipped`, and `failed` statuses, including a real geometry acceptance check based on solid volume change.
- Non-intersecting cuts are now marked `failed` with a "geometry did not change" warning instead of being reported as modeled.
- Added `ExecutionReport` with four independent statuses: `execution_ok`, `plan_complete`, `geometry_valid`, and `production_ready`, plus fallback, assumptions, completeness score, skipped/failed features, and worker details.
- Added `GET /api/capabilities` exposing registered capabilities and feature semantics for UI and audit reuse.
- Frontend adds execution acceptance report, dimension evidence strip, design intent summary, and a feature-tree pipeline header (evidence -> intent -> features -> acceptance).
- Worker messages and validation messages were cleaned to UTF-8 Chinese/English pairs.
## v0.5.0 Phase 1 - Capability & Generic Edit Runtime Foundation

- Added static capability metadata (`ParameterSpec`, `FeatureCapability`, `CapabilityIssue`) to `backend/schemas.py`.
- Added `CapabilityRegistry` and a module-level `CAPABILITIES` singleton with 14 real feature types: 3 bases, 3 holes, 2 slots/pockets, 1 annular groove, 3 additive features, and 2 patterns.
- Chat edits, LLM edits, and property-panel patches now share one structural validation gate before the existing FeaturePlan checks.
- Invalid operations fail individually as blocked ProcessSteps; valid operations in the same edit set still execute.
- Property-panel patches that violate capability rules return HTTP 422 with structured `CapabilityIssue` data.
- Unknown features, disallowed operations, unknown/read-only parameters, negative values, and wrong value types use fixed error codes.
- `fillet`, `chamfer`, `thread`, `gear`, and `sheet_metal` are intentionally unregistered and are reported as unsupported instead of being silently accepted.
- Added `docs/capability_runtime.md` describing the registry, error codes, edit flow, responsibility boundary, and how to register new capabilities.
- Stabilized stub-quality AI tests so they stay deterministic when `.env` has real model credentials configured.

## v0.4.3 - Complex Part Validation and UTF-8 Regression Guard

- Added realistic complex part validation through the controlled Build123d Worker:
  - motor mounting plate with a pilot boss, central bore, and four mounting holes;
  - shaft with a center bore and two seal grooves;
  - flanged end cap with a center bore, face O-ring groove, and eight-hole bolt circle;
  - support bracket with a reinforcing rib and two bossed mounting bores.
- Every case passed deterministic self-checks and exported STEP/STL/OBJ with per-feature execution reports.
- Verified the Chinese description path end to end: Chinese tube/pipe and bracket descriptions were recognized and modeled without source-level encoding changes.
- Added an encoding regression test that guards key Chinese source strings and rejects `U+FFFD` replacement characters.

## v0.4.2 - Reliability, Validation, and Feature Tree

- Restored `gpt-5.5` planner, cleaned `.env` to `MECHCAD_*`, and added per-role timeout/retry settings.
- API retries recover from transient ReadTimeout/connection/5xx failures and report the final attempt count in the process timeline.
- CAD Worker writes `feature_statuses` back into the FeaturePlan; API and feature tree no longer show stale `unresolved` statuses.
- Flange center holes now use the independent `hole_diameter` dimension instead of misreading the outer diameter.
- Added deterministic engineering self-checks (`validate_feature_plan`) and staged dependency ordering (base -> remove -> add -> pattern -> modify).
- Strict mode blocks missing dimensions, unconfirmed assumptions, and blocking checks; smart mode keeps auditable assumptions while still blocking geometry contradictions and invalid dependencies.
- Missing X/Y placement is rejected in strict mode and skipped by the CAD Worker instead of silently defaulting to the origin.
- CAD Worker reorders features before execution; chat, property edits, generation, and undo/redo all refresh validation consistently.
- Frontend feature tree is now a read-only grouped view with status badges, missing/assumption markers, dependency indentation, and summary counts.
- Validation messages are bilingual (Chinese/English) and exported into `self_checks` and design review.

## v0.4.1 - Stability and Config Fixes

- Switched the planner back to `gpt-5.5`; `.env` now exposes only authoritative `MECHCAD_*` variables.
- Added per-role API timeouts and retries with real error propagation into process steps before local fallback.
- Fixed CAD execution status write-back for cylinder/hollow-cylinder bases.

# MechCAD IDE Changelog

## v0.4.0 - Process-First Timeline + Feature-Level Editing

- Added `ProcessStep` and `ProcessRecorder`; every run now records `upload -> vision -> planning -> validation -> chat_edit/cad -> export`.
- Chat edits now produce an auditable `FeatureEditSet` with `add`, `update`, `delete`, and `change_type` operations instead of replacing the whole `FeaturePlanV3`.
- Local deterministic editing covers center-hole diameter changes, hole moves, groove deletion, and M6 hole pattern creation.
- Strict mode blocks unconfirmed inferred dimensions; deleting a parent with children asks for confirmation instead of silently breaking dependencies.
- CAD Worker reports per-feature `running/completed/skipped/failed` steps through JSONL and WebSocket, with real error details in the execution report.
- Property-panel edits also generate an `update` process step with before/after snapshots.
- Process timelines persist with snapshots and are restored by undo/redo.
- Frontend adds a Process tab with grouped statuses, feature IDs, warnings, and failure reasons.
- Fixed duplicate Worker progress events and unawaited coroutine warnings in the event bridge.

## v0.3.0 - Productized UI

- SolidWorks-style three-panel layout with full-screen 3D viewport and drawer panels.
- Startup page, recent projects, settings center, and Chinese/English switching.
- Single-port production mode plus Windows tray launcher and desktop shortcut.
- API/model configuration moved into Settings.

## v0.2.0 - New Architecture Baseline

- React + TypeScript + Vite + Three.js frontend.
- FastAPI REST + WebSocket backend.
- Controlled CAD Worker subprocess with Build123d.
- FeaturePlanV3 validation, design review, clarification questions, and session persistence.

## v0.1.0 - Initial MVP

- Hand-drawn sketch to 3D model pipeline.
- OpenCV preprocessing, vision analysis, AI planning, sandboxed CAD execution.
- STEP/STL/OBJ export and Gradio UI.
