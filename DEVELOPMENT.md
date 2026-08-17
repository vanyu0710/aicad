# MechCAD Development Guide

## Prerequisites

- Python 3.12 in `.venv`
- Node.js with npm for `frontend/`
- `.env` copied from `.env.example`; real API keys are optional because deterministic fallbacks exist

## Local Setup

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cd frontend
npm install
```

## Run

Development mode (Vite + FastAPI):

```powershell
.\start-mechcad.cmd
```

Product mode (single port + tray + desktop shortcut):

```powershell
.\start-mechcad-pro.cmd
```

Manual backend:

```powershell
.\.venv\Scripts\python.exe -m backend.main
```

Manual frontend:

```powershell
cd frontend
npm run dev
```

## Test

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
cd frontend
npm test
npm run build
```

Quality gates before committing:

```powershell
.\.venv\Scripts\python.exe -m compileall -q backend cad_worker
git diff --check
```

## Add a New Feature

1. Add a canonical `FeatureDefinition` in `backend/feature_definitions.py`; every schema, capability, edit, constraint, and verification contract derives from it.
2. Add free-form aliases in `backend/mechcad_ai/normalize.py` only when LLM output needs them; alias keys should point to registry parameter names.
3. Add validation constraints in `backend/validation.py`; keep validation pure.
4. Add a worker branch in `cad_worker/freecad_executor.py` only when you have a controlled CAD operation and a real geometry change check.
5. Add a geometry signature / evidence binding in `backend/geometry/signatures.py` before claiming correspondence. `AMBIGUOUS` must not auto-select.
6. Add a semantic verifier only when bound evidence can reliably prove the property. Otherwise keep the feature `UNSUPPORTED`; never fake `PASS`.
7. Add focused backend and frontend tests, then update `FEATURE_SUPPORT.md`.

## Environment

- `MECHCAD_*` variables are authoritative; legacy names are intentionally omitted from `.env.example`.
- `MECHCAD_PLANNER_MODEL=gpt-5.5`, `MECHCAD_VISION_MODEL=qwen3-vl-plus` are the current defaults.
- Model config is per-project and stored in project settings; API keys are never written to `localStorage`.

## Git Conventions

- Branch prefix: `codex/`
- One independent commit per v0.7 subphase or R1 deliverable.
- Do not push unless the user explicitly asks; when pushing from a proxied network use the repository's configured proxy command.
