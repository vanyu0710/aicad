# MechCAD IDE

MechCAD is being upgraded from a Gradio MVP into an AI CAD IDE.

The old Gradio implementation is preserved in `legacy/gradio/`. The new mainline is:

- `frontend/`: React + TypeScript + Vite + Three.js.
- `backend/`: FastAPI REST API + WebSocket event stream.
- `cad_worker/`: isolated CAD worker subprocess. It currently uses a controlled Build123d backend while keeping the FreeCAD worker boundary intact.
- `prompts/prompts.yaml`: centralized prompts for vision analysis, FeaturePlan planning, chat edits, and design review.

## Local Development

Backend:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\.venv\Scripts\python.exe -m backend.main
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

## Tests

Backend (unit + API integration, uses `unittest`):

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests
```

Frontend (Vitest + Testing Library):

```powershell
cd frontend
npm test
```

Coverage:

- `tests/test_session.py` – snapshot history, undo/redo semantics.
- `tests/test_api.py` – FastAPI endpoints: generate / chat / patch / undo / redo, artifacts, WebSocket accept.
- `tests/test_cad_worker.py` – worker success, timeout and failure reporting.
- `tests/test_events.py` – EventBus publish/subscribe isolation.
- `frontend/src/FeatureForm.test.tsx` – property panel interaction tests.


## AI Integration

`backend/mechcad_ai/` is the real-model layer:

- `client.py` – OpenAI / Anthropic compatible HTTP client (auto `/v1` retry).
- `prompts.py` – loads the centralized prompts from `prompts/prompts.yaml`.
- `vision.py` – vision model reads the sketch into a structured vision JSON.
- `planner.py` – planner model turns vision JSON + description into FeaturePlanV3.
- `normalize.py` – maps free-form model output onto the strict FeaturePlanV3 shape
  without inventing values; missing dimensions stay in `unresolved`.

Model config comes from `ModelConfig` (per-project `settings`), falling back to
environment variables `MECHCAD_VISION_*` / `MECHCAD_PLANNER_*` (see `.env`).
If no model is configured or a call fails, the deterministic local stub in
`backend/ai.py` takes over so the IDE and CAD chain keep working.

## API

- `POST /api/projects`: create project.
- `GET /api/projects/{project_id}`: read current state.
- `POST /api/projects/{project_id}/generate`: create a new FeaturePlan and run the CAD worker.
- `POST /api/projects/{project_id}/chat`: apply a natural language edit.
- `PATCH /api/projects/{project_id}/features/{feature_id}`: edit one feature.
- `POST /api/projects/{project_id}/undo`: undo to previous snapshot.
- `POST /api/projects/{project_id}/redo`: redo snapshot.
- `GET /api/artifacts/{run_id}/{kind}`: download `step`, `stl`, `obj`, `report`, or `execution_report`.
- `WS /ws/projects/{project_id}`: receive generation progress events.

## Modeling Contract

The default path is no longer "AI writes arbitrary Python". The intended chain is:

1. Vision model reads sketch evidence.
2. Planner model outputs `FeaturePlanV3`.
3. Pydantic validates the plan.
4. CAD worker maps the validated feature tree to safe CAD operations.
5. Frontend shows feature tree, 3D preview, questions, logs, and artifacts.

Strict mode uses only explicit drawing or user-confirmed data. Smart mode can suggest assumptions, but unconfirmed inferred dimensions must stay in `assumptions` or `design_review` instead of executable `dimensions`.

## Legacy Gradio

The previous MVP lives under `legacy/gradio/`. To run it as a reference, start it from that directory and ensure its imports are available on `PYTHONPATH`.
