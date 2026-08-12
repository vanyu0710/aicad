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
