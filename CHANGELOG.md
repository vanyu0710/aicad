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
