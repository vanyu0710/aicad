# MechCAD Architecture

## Status

- Current branch: `codex/mechcad-pro-ui`
- Latest milestone: v0.7.2 Complete Hole Verification (1D-2.2)
- CAD engine: controlled Build123d worker behind a FreeCAD-ready subprocess boundary
- UI: React + TypeScript + Three.js, SolidWorks-style three-panel IDE with full-screen viewport drawers

## Repository Map

- `backend/`: FastAPI REST API, WebSocket events, AI orchestration, evidence, validation, sessions, geometry verification.
  - `schemas.py`: Pydantic data contracts for `FeaturePlanV3`, snapshots, evidence, capability metadata, reports.
  - `feature_definitions.py`: canonical, stateless feature registry. This is the source of truth for feature metadata.
  - `normalization.py`: explicit idempotent `normalize_feature_plan()` bookkeeping.
  - `validation.py`: pure `validate_feature_plan()` plus explicit `apply_validation_result()`.
  - `evidence_gate.py`: pure evidence conflict gate and user-resolution application.
  - `capabilities.py`: capability/editing registry derived from `feature_definitions.py`.
  - `generic_engine.py`: text routing, evidence extraction, design intent, family templates.
  - `ai.py`: AI orchestration and deterministic fallback.
  - `geometry/`: read-only BRep measurement (`measurement.py`), feature-geometry evidence (`signatures.py`, `references.py`, `candidates.py`, `resolver.py`), and semantic verification (`verification.py`, `verification_registry.py`, `correspondence.py`).
  - `process.py`: `ProcessRecorder` for auditable process steps.
  - `session.py` / `storage.py`: snapshot history, undo/redo, run artifacts.
  - `static_assets.py`: single-port production frontend mounting.
- `cad_worker/`: isolated subprocess CAD executor. `freecad_executor.py` is the worker boundary name; it currently runs Build123d.
- `frontend/`: React + TypeScript + Vite + Three.js IDE.
- `prompts/`: centralized YAML prompts for vision, planning, chat edits, and review.
- `tests/`: backend `unittest` suites.
- `legacy/gradio/`: preserved Gradio MVP reference.
- `mechcad/`: old compatibility shim used only by legacy regression tests, not by the new mainline.

## Source of Truth

- `FeaturePlanV3` is the only executable design truth.
- `FeatureDefinition` is the canonical source for feature parameters, operations, constraints, and verification contracts.
- `ExecutionReport` is the source of truth for what the worker actually did.
- `DesignSnapshot` is the immutable project state used by undo/redo.
- `ProcessStep` is the audit trail for every pipeline stage.
- Geometry measurement, feature-geometry evidence, and verification are additive facts; they never become the only success signal.
- `AMBIGUOUS` geometry correspondence never auto-selects a candidate.

## Runtime Pipeline

1. `InputRouter` decides text-only, image-only, or mixed input; pure text never calls a vision model.
2. Evidence and design intent are collected into `EvidenceSet` / `DesignIntent`.
3. Planner or family template produces `FeaturePlanV3`.
4. `normalize_feature_plan()` -> `validate_feature_plan()` -> `apply_validation_result()`.
5. `evaluate_evidence_gate()` blocks unresolved material conflicts before CAD.
6. Controlled CAD worker maps features to Build123d operations and emits per-feature process steps.
7. Final BRep is measured (`measurement.py`), bound to features by the 1D-2.1 evidence resolver, then semantically verified (`verification.py`, including 1D-2.2 hole checks). The Worker writes additive `geometry_measurement`, `geometry_evidence`, and `geometry_verification`.
8. STEP/STL/OBJ plus `execution_report.json` are exported and the next snapshot is committed.

## API / WebSocket

- REST: project create/list/get/rename/delete, settings, generate, chat, feature patch, undo/redo, capabilities, model test, artifact download.
- WebSocket: `WS /ws/projects/{project_id}` streams stage and process-step events.
- Product mode mounts the built frontend on the same FastAPI port; unknown `/api/*` paths still return JSON 404.

## Boundaries

- Backend never imports Build123d/FreeCAD; only the worker subprocess does.
- Worker never executes arbitrary AI Python; it only maps validated `FeaturePlanV3` data.
- Validation and evidence gates are pure; they must not mutate plans or snapshots implicitly.
- Verification is additive and conservative: `UNKNOWN` is never converted to `PASS`.
- UI only consumes REST/WebSocket contracts and does not contain CAD logic.

## Known Limitations

- Feature geometry evidence can bind box/cylinder bases and hole/boss cylinders. Semantic verification covers isolated bases plus `through_hole` / `blind_hole`.
- Holes, grooves, bosses, patterns, ribs, fillets, chamfers, and relations remain `UNSUPPORTED` in the verification registry.
- Counterbores, slots, ribs, and patterns have partial worker semantics; they are not full production feature implementations.
- Spur gear teeth, threads, sheet metal, fillets, and chamfers are intentionally unsupported and never faked.
- Strict mode blocks unconfirmed assumptions; Smart mode allows audited assumptions but keeps `production_ready=false`.
