# MechCAD IDE

MechCAD is being upgraded from a Gradio MVP into an AI CAD IDE.

## User Guide

New users should start with [docs/USER_GUIDE.md](docs/USER_GUIDE.md).

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md): how the system works and where each truth lives.
- [FEATURE_SUPPORT.md](FEATURE_SUPPORT.md): what is really implemented, partial, or unsupported.
- [DEVELOPMENT.md](DEVELOPMENT.md): setup, tests, and how to add a feature safely.
- [docs/v0.7-r1-repository-audit.md](docs/v0.7-r1-repository-audit.md): R1 audit and KEEP/MIGRATE/DEPRECATE/DELETE classification.
- [docs/rfc-1d-2.1-feature-geometry-evidence-resolver.md](docs/rfc-1d-2.1-feature-geometry-evidence-resolver.md): 1D-2.1 evidence resolver design.
- [docs/geometry_evidence.md](docs/geometry_evidence.md): Feature-to-BRep correspondence boundary.

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

### 产品模式（Windows 桌面启动）

首次运行会自动构建前端，并在桌面创建 `MechCAD IDE` 快捷方式：

```powershell
.\start-mechcad-pro.cmd
```

之后双击桌面快捷方式即可。FastAPI 会同时托管前端、API 和 WebSocket，访问 `http://127.0.0.1:8001/`；系统托盘提供“打开界面 / 重启服务 / 打开日志 / 退出”。

可选参数：

```powershell
.\start-mechcad-pro.cmd --no-browser
.\start-mechcad-pro.cmd --port 8080
.\start-mechcad-pro.cmd --skip-shortcut
```

环境变量：`MECHCAD_PORT`、`MECHCAD_OPEN_BROWSER`、`MECHCAD_LOG_DIR`。

### 开发模式

`start-mechcad.cmd` 保留开发双进程模式（Vite + FastAPI），访问 `http://127.0.0.1:5173/`。

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

## MechKernel Agent（主路径，v0.9.0）

MechKernel agent（harness）现在是 aicad 的**默认建模路径**：LLM 通过原生 function calling
直接驱动 MechKernel 参数化内核（`mechcad-kernel` 仓）的 33 个公开 op，逐步自主建模；
用户在视口上方的 Agent 运行条里启动/停止、看步数；特征树与属性面板直接渲染内核的
`feature_graph`，可改参数（`update_feature` → 参数化重放）与撤销/重做。

- 执行层直接是 MechKernel op；`feature_graph` / `_op_history` 是特征树与参数化重放的唯一来源（D1）。
- Worker 是常驻子进程 `mech_kernel/server.py`（stdio JSON-lines RPC），backend 永不 import CAD 库（D2）。
- **人机协作确认点（P2）**：破坏性操作（delete_feature / confirm_replace / shell）、破坏性修复、
  `ask_user` 提问都会暂停等待用户在助手面板审批（批准/改参/拒绝）；超时默认 600s
  （`MECHCAD_AGENT_APPROVAL_TIMEOUT`）自动跳过。支持"暂停接管 → 手动编辑 → 交还继续"。
- `RECOVERABLE` 失败按 capability schema 过滤 `suggestion.fix` 自动重试；体积变化时导 STL，收尾导 STEP。
- 配置见 `.env.example` 的 `MECHCAD_KERNEL_REPO` / `MECHCAD_KERNEL_PYTHON` / `MECHCAD_KERNEL_TIMEOUT`；agent LLM 复用 planner 角色配置。
- 路线图：`G:\lfy design\ai cad\mechcad-kernel\docs\mechkernel-harness-roadmap.md`（P3 改动清单 / P4 打磨）。

> **FeaturePlanV3 链路已冻结**：前端默认不暴露旧入口（/generate、/chat、PATCH features 仍在 API 层可用）。
> 旧的 AI 规划 → 校验 → 受控 build123d worker 链路及其测试全部保留，未删除，可经 git 历史或用
> 旧版 UI 切回。

## API

- `POST /api/projects`: create project.
- `GET /api/projects/{project_id}`: read current state.
- `POST /api/projects/{project_id}/generate`: create a new FeaturePlan and run the CAD worker (frozen legacy).
- `POST /api/projects/{project_id}/chat`: apply a natural language edit (frozen legacy).
- `POST /api/projects/{project_id}/agent/start`: start the MechKernel agent loop (default path).
- `POST /api/projects/{project_id}/agent/stop`: request a cooperative stop between agent steps.
- `POST /api/projects/{project_id}/agent/resolve`: answer a pending approval (approve/reject/edit).
- `GET /api/projects/{project_id}/kernel/feature_tree`: current kernel feature_graph + op_history.
- `POST /api/projects/{project_id}/kernel/update_feature`: parametric rebuild after a parameter change.
- `POST /api/projects/{project_id}/kernel/delete_feature`: delete a feature (kernel replay).
- `POST /api/projects/{project_id}/kernel/undo` / `.../kernel/redo`: undo/redo inside the kernel worker.
- `PATCH /api/projects/{project_id}/features/{feature_id}`: edit one feature (frozen legacy).
- `POST /api/projects/{project_id}/undo`: undo (kernel worker when alive, else legacy snapshot).
- `POST /api/projects/{project_id}/redo`: redo (kernel worker when alive, else legacy snapshot).
- `GET /api/artifacts/{run_id}/{kind}`: download `step`, `stl`, `obj`, `report`, or `execution_report`.
- `WS /ws/projects/{project_id}`: receive agent step / approval / artifact events.

## Modeling Contract

The default path is no longer "AI writes arbitrary Python". The intended chain is:

1. Vision model reads sketch evidence.
2. Planner model outputs `FeaturePlanV3`.
3. Pydantic validates the plan.
4. CAD worker maps the validated feature tree to safe CAD operations.
5. Frontend shows feature tree, 3D preview, questions, logs, and artifacts.

Strict mode uses only explicit drawing or user-confirmed data and blocks CAD when required values are unresolved. Smart mode has three policies: `limited_fill` for conservative engineering completion, `aggressive_fill` for concept generation, and `full_autonomous` for active feature and manufacturing-intent design. Smart assumptions may execute for concept preview, but every inferred dimension is recorded in `assumptions`, `assumption_details`, and `design_review` with evidence and confirmation state.

The current local worker is a controlled Build123d subprocess. Set `MECHCAD_CAD_ENGINE` to keep the runtime label explicit; do not describe this backend as FreeCAD until a FreeCAD executable is actually wired in.

## Legacy Gradio

The previous MVP lives under `legacy/gradio/`. To run it as a reference, start it from that directory and ensure its imports are available on `PYTHONPATH`.
