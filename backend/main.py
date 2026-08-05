from __future__ import annotations

import base64
import io
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image

from backend.ai import apply_chat_edit, build_initial_feature_plan, patch_feature
from backend.cad import run_freecad_worker
from backend.events import EventBus
from backend.schemas import (
    ChatEditRequest,
    CreateProjectRequest,
    CreateProjectResponse,
    DesignSnapshot,
    FeaturePatchRequest,
    GenerateRequest,
    ModelConfig,
    StageEvent,
)
from backend.session import SessionStore
from backend.storage import artifact_path


load_dotenv()

app = FastAPI(title="MechCAD IDE API", version="0.2.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = SessionStore()
events = EventBus()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "mechcad-ide-api"}


@app.post("/api/projects", response_model=CreateProjectResponse)
def create_project(request: CreateProjectRequest) -> CreateProjectResponse:
    project = store.create_project(request.name)
    return CreateProjectResponse(project_id=project.project_id, project=project)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    return _project_or_404(project_id)


@app.post("/api/projects/{project_id}/generate")
async def generate(project_id: str, request: GenerateRequest):
    project = _project_or_404(project_id)
    if request.settings is not None:
        store.update_config(project_id, request.settings)
    if request.operation_mode:
        project.settings.operation_mode = request.operation_mode
    if request.smart_fill_policy:
        project.settings.smart_fill_policy = request.smart_fill_policy

    image = _decode_image(request.image_data_url)
    await _emit(project_id, "stage_started", "upload", "已接收草图与描述")
    if image is not None:
        await _emit(project_id, "stage_done", "upload", f"图片已解析：{request.image_name or 'uploaded image'}")
    else:
        await _emit(project_id, "stage_progress", "upload", "未提供可用图片，正在只基于描述继续")

    await _emit(project_id, "stage_started", "planning", "正在生成 FeaturePlanV3")
    settings = request.settings if request.settings is not None else project.settings
    plan, questions = build_initial_feature_plan(request.description, request, image, settings=settings)
    await _emit(project_id, "question_required", "planning", "已生成澄清问题", {"questions": [q.model_dump() for q in questions]})

    await _emit(project_id, "stage_started", "cad", "正在启动受控 CAD Worker")
    artifacts, logs, ok = run_freecad_worker(plan)
    if ok:
        await _emit(project_id, "artifact_ready", "cad", "已生成 STEP/STL/OBJ", artifacts.model_dump())
    else:
        await _emit(project_id, "error", "cad", "CAD Worker 执行失败", {"artifacts": artifacts.model_dump(), "logs": logs})

    snapshot = DesignSnapshot(
        feature_plan=plan,
        artifacts=artifacts,
        questions=questions,
        design_review=plan.design_review,
        report_markdown=_build_report(project.project_id, ok, questions, logs),
        logs=logs,
    )
    store.commit_snapshot(project_id, snapshot)
    return store.get_project(project_id)


@app.post("/api/projects/{project_id}/chat")
async def chat_edit(project_id: str, request: ChatEditRequest):
    _project_or_404(project_id)
    await _emit(project_id, "stage_started", "chat_edit", "正在处理自然语言修改")
    project = store.get_project(project_id)
    plan, questions = apply_chat_edit(project.current.feature_plan, request.message, project.settings)
    artifacts, logs, ok = run_freecad_worker(plan)
    snapshot = DesignSnapshot(
        feature_plan=plan,
        artifacts=artifacts,
        questions=questions,
        design_review=plan.design_review,
        report_markdown=_build_report(project_id, ok, questions, logs),
        logs=project.current.logs + logs,
    )
    store.commit_snapshot(project_id, snapshot)
    await _emit(project_id, "stage_done", "chat_edit", "增量修改完成")
    return store.get_project(project_id)


@app.patch("/api/projects/{project_id}/features/{feature_id}")
async def patch_project_feature(project_id: str, feature_id: str, request: FeaturePatchRequest):
    project = _project_or_404(project_id)
    plan = patch_feature(project.current.feature_plan, feature_id, request.model_dump(exclude_none=True))
    artifacts, logs, ok = run_freecad_worker(plan)
    snapshot = DesignSnapshot(
        feature_plan=plan,
        artifacts=artifacts,
        questions=project.current.questions,
        design_review=plan.design_review,
        report_markdown=_build_report(project_id, ok, project.current.questions, logs),
        logs=project.current.logs + logs,
    )
    store.commit_snapshot(project_id, snapshot)
    await _emit(project_id, "stage_done", "feature_patch", f"特征 {feature_id} 已更新")
    return store.get_project(project_id)


@app.post("/api/projects/{project_id}/undo")
def undo(project_id: str):
    _project_or_404(project_id)
    return store.undo(project_id)


@app.post("/api/projects/{project_id}/redo")
def redo(project_id: str):
    _project_or_404(project_id)
    return store.redo(project_id)


@app.get("/api/artifacts/{run_id}/{kind}")
def get_artifact(run_id: str, kind: str):
    try:
        path = artifact_path(run_id, kind)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown artifact kind") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found")
    return FileResponse(path)


@app.websocket("/ws/projects/{project_id}")
async def websocket(project_id: str, websocket: WebSocket):
    await websocket.accept()
    queue = await events.subscribe(project_id)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event.model_dump())
    except WebSocketDisconnect:
        events.unsubscribe(project_id, queue)
    finally:
        events.unsubscribe(project_id, queue)


def _project_or_404(project_id: str):
    try:
        return store.get_project(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Project not found") from exc


async def _emit(project_id: str, event_type: str, stage: str, message: str, payload: dict | None = None) -> None:
    await events.publish(
        StageEvent(type=event_type, project_id=project_id, stage=stage, message=message, payload=payload or {})
    )


def _build_report(project_id: str, cad_ok: bool, questions, logs: list[str]) -> str:
    status = "CAD worker 成功" if cad_ok else "CAD worker 失败"
    question_text = "\n".join(f"- {q.text}" for q in questions) if questions else "- 暂无"
    log_text = "\n".join(f"- {line}" for line in logs[-6:]) if logs else "- 无日志"
    return (
        f"### MechCAD Run\n"
        f"- Project: `{project_id}`\n"
        f"- CAD: **{status}**\n\n"
        f"### 待确认问题\n{question_text}\n\n"
        f"### 日志\n{log_text}\n"
    )


def _decode_image(image_data_url: str | None) -> Image.Image | None:
    if not image_data_url:
        return None
    if "," not in image_data_url:
        return None
    _, payload = image_data_url.split(",", 1)
    try:
        raw = base64.b64decode(payload)
    except Exception:
        return None
    try:
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return None


if __name__ == "__main__":
    import uvicorn

    Path("work").mkdir(exist_ok=True)
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
