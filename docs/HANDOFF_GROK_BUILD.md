# MechCAD Handoff for Grok Build

## 1. Read These First

- `ARCHITECTURE.md`: system map, source-of-truth rules, pipeline, API surface.
- `FEATURE_SUPPORT.md`: what is really implemented, partial, or unsupported.
- `DEVELOPMENT.md`: setup, tests, and how to add a feature safely.
- `docs/v0.7-r1-repository-audit.md`: KEEP / MIGRATE / DEPRECATE / DELETE audit.
- `docs/geometry_measurement.md`, `docs/geometry_verification.md`, and `docs/geometry_evidence.md`: measurement, evidence binding, and semantic verification boundaries.
- `docs/rfc-1d-2.1-feature-geometry-evidence-resolver.md`: approved 1D-2.1 design.
- `CHANGELOG.md`: what each version actually shipped.
- `README.md`: user-facing startup and quick overview.

## 2. Project Status

MechCAD is an AI CAD IDE under active development.

- Frontend: React + TypeScript + Vite + Three.js.
- Backend: FastAPI REST API + WebSocket events.
- CAD execution: controlled Build123d worker subprocess behind a FreeCAD-ready worker boundary.
- AI: vision model reads drawings, planner model emits `FeaturePlanV3`, deterministic fallback keeps the IDE usable without API keys.
- Current branch: `codex/mechcad-pro-ui`
- Last commit: 0.7.3-A Generic Boss Verification after the 0.7.2 adversarial gate
- Stable tag: `v0.7.0-stable-baseline` remains the pre-1D-2.1 baseline
- Remote: `https://github.com/vanyu0710/aicad.git`
- Last verified baseline: 199 backend tests, 48 frontend tests, frontend production build passed.

## 3. Architecture Map

- `backend/`: FastAPI, AI orchestration, evidence, validation, sessions, geometry verification.
  - `schemas.py`: Pydantic contracts for `FeaturePlanV3`, `DesignSnapshot`, evidence, capability metadata, reports.
  - `feature_definitions.py`: canonical, stateless feature registry.
  - `normalization.py`: explicit idempotent plan normalization.
  - `validation.py`: pure validation plus explicit write-back orchestration.
  - `evidence_gate.py`: pure evidence conflict gate.
  - `capabilities.py`: capability and edit validation derived from the canonical registry.
  - `generic_engine.py`: text routing, evidence extraction, design intent, family templates.
  - `ai.py`: AI orchestration and deterministic fallback.
  - `geometry/`: BRep measurement, feature-geometry evidence resolver, and semantic verification.
  - `process.py`: `ProcessRecorder` audit timeline.
  - `session.py` / `storage.py`: snapshot history, undo/redo, artifacts.
- `cad_worker/freecad_executor.py`: isolated subprocess worker. It runs Build123d; the file name preserves the future FreeCAD boundary.
- `frontend/`: IDE with full-screen Three.js viewport, drawer panels, feature tree, AI task pane, settings, startup page.
- `prompts/`: centralized YAML prompts.
- `tests/`: backend `unittest` suites.
- `legacy/gradio/`: preserved Gradio MVP.
- `mechcad/`: old compatibility shim used only by legacy regression tests, not by the new mainline.

## 4. Source-of-Truth Rules

- `FeaturePlanV3` is the only executable design truth.
- `FeatureDefinition` is the canonical source for feature parameters, operations, constraints, and verification contracts.
- `normalize_feature_plan()` is explicit bookkeeping; validation must stay pure.
- `validate_feature_plan()` must not mutate the plan; use `apply_validation_result()` only in orchestration.
- `evaluate_evidence_gate()` is pure and must never silently pick one conflicting value.
- Worker `execution_report.json` is the truth for what CAD actually did.
- `DesignSnapshot` is the immutable undo/redo state.
- Geometry verification is additive and conservative: `UNKNOWN` is never converted to `PASS`.

## 5. Feature Reality

The matrix in `FEATURE_SUPPORT.md` is the authoritative capability statement.

Highlights:

- Fully verified bases today: `box_base`, `cylinder_base`, `hollow_cylinder` only.
- Executable but simplified: `counterbore_hole`, `rectangular_slot`, `rectangular_pocket`, `rib_box`, `linear_pattern`, `circular_pattern`.
- Executable with normal worker semantics: holes, blind holes, annular grooves, internal annular grooves, boss, pad.
- Explicitly unsupported: `fillet`, `chamfer`, `spur_gear` teeth, `helical_gear`, `thread`, `sheet_metal`.
- Holes, grooves, bosses, patterns, ribs, fillets, and chamfers have no reliable semantic verifier yet.
- Strict mode blocks unconfirmed assumptions and unresolved material evidence conflicts.
- Smart mode can run audited assumptions as a concept preview, but `production_ready=false` until user confirmation.
- The current CAD engine is Build123d. Never label it FreeCAD in UI, docs, or reports until a real FreeCAD worker is wired in.

## 6. Workflow Discipline

- One independent commit per v0.7 subphase or R1 deliverable.
- After each subphase, run the full backend suite and stop; do not automatically continue into the next subphase.
- Never remove uncertain code. Use the audit categories KEEP / MIGRATE / DEPRECATE / DELETE, and ask the user before deleting.
- Prefer general capability improvements over one-off fixes for individual example parts.
- If a requested measurement cannot be reliably extracted from Build123d/OCCT, report the limitation instead of inventing an abstraction or faking a result.
- Keep all Python, TypeScript, and YAML files UTF-8; keep Chinese/English UI strings complete.

## 7. Development Commands

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd frontend
npm install
```

Run development mode:

```powershell
.\start-mechcad.cmd
```

Run product mode:

```powershell
.\start-mechcad-pro.cmd
```

Backend tests:

```powershell
$env:MECHCAD_CAD_TIMEOUT='300'
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Frontend tests and build:

```powershell
cd frontend
npm test
npm run build
```

Static checks:

```powershell
.\.venv\Scripts\python.exe -m compileall -q backend cad_worker
git diff --check
```

## 8. Git and Push

Current branch: `codex/mechcad-pro-ui`.

Normal push when the user explicitly asks and a proxy is active:

```powershell
git -c http.proxy=http://127.0.0.1:7897 push origin codex/mechcad-pro-ui
```

Do not push without explicit user permission. If the proxy port changes, ask the user for the current port.

## 9. Known Debt and Risks

- `backend/mechcad_ai/normalize.py` still owns LLM free-form alias tables with `TODO(v0.7)` migration notes.
- `backend/generic_engine.py` preserves a legacy subset order in `feature_semantics()`.
- Root `mechcad/` compatibility shim is used only by `test_clarification.py` and `test_feature_executor.py`; it should move under `legacy/` only when those tests are explicitly rehomed.
- `frontend/src/layout/BottomTaskPanel.tsx` and `frontend/src/layout/RightPropertyManager.tsx` are not imported by `App.tsx`; no test depends on them.
- `HANDOFF.md` is a historical document with outdated branch/test information; this file supersedes it.
- Counterbore, slot, rib, and pattern worker semantics are simplified and are not production-ready.
- Hole through-ness is V-span versus host thickness, not a topological both-ends-open proof.
- Patterns, fillets, and chamfers still have no semantic verifier. Additive vs subtractive cylinders are not distinguished in 1D-1 measurement.
- Frontend production bundle exceeds the default 500 kB chunk warning threshold; it is a warning, not a failure.

## 10. Recommended Next Work

The planned continuation is 0.7.3-C adversarial coverage or 0.7.4 Global Geometry Integrity:

- Hole, boss, and annular groove now share one GeometryEvidence pipeline.
- 0.7.3-C: more cross-feature False PASS cases if needed; do not add feature-specific evidence types.
- 0.7.4: model-level solidity, disconnected solids, empty booleans — not per-feature templates.
- Do not change `FeaturePlanV3`, Evidence Gate, worker execution policy, UI, or export behavior unless explicitly approved.

If the user approves a different direction, update this handoff with the new agreed scope before implementation.

## 11. First Actions for Grok Build

1. Fetch and check out `codex/mechcad-pro-ui`.
2. Read the five primary documents listed in section 1.
3. Run the full test baseline before changing anything.
4. Inspect `backend/feature_definitions.py`, `backend/geometry/*`, and `cad_worker/freecad_executor.py`.
5. Produce a short plan and ask the user to approve scope before implementing 0.7.4 global geometry integrity.
