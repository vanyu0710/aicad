from __future__ import annotations

import base64
from copy import deepcopy
import io
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image

from backend.ai import (
    apply_chat_edit,
    apply_clarification_answers,
    build_initial_feature_plan,
    patch_feature,
    questions_from_plan,
)
from backend.cad import run_freecad_worker
from backend.events import EventBus
from backend.schemas import (
    ChatEditRequest,
    CreateProjectRequest,
    CreateProjectResponse,
    DesignSnapshot,
    FeaturePatchRequest,
    GenerateRequest,
    ModelTestRequest,
    ModelTestResponse,
    ModelTestDiagnostics,
    ProjectSettingsRequest,
    RenameProjectRequest,
    StageEvent,
)
from backend.mechcad_ai.client import resolve_role_config, test_model_connection
from backend.session import SessionStore
from backend.storage import artifact_path
from backend.static_assets import mount_frontend


load_dotenv()

app = FastAPI(title="MechCAD IDE API", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8001", "http://127.0.0.1:8001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SECRET_MASK = "***configured***"


def _loc(language: str, zh: str, en: str) -> str:
    return en if language == "en" else zh

store = SessionStore(os.getenv("MECHCAD_STORE_PATH") or (Path(__file__).resolve().parent.parent / "work" / "projects.json"))
events = EventBus()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "mechcad-ide-api"}


@app.post("/api/model/test", response_model=ModelTestResponse)
def test_model(request: ModelTestRequest) -> ModelTestResponse:
    config = resolve_role_config(request.config, request.role)
    result = test_model_connection(request.config, request.role, language=request.language)
    return ModelTestResponse(
        ok=bool(result.get("ok", False)),
        role=request.role,
        provider=getattr(request.config, f"{request.role}_provider"),
        protocol=config["protocol"],
        model=config["model"],
        message=str(result.get("message", _loc(request.language, "未知连接结果", "Unknown connection result"))),
        diagnostics=ModelTestDiagnostics(
            status_code=result.get("status_code"),
            content_type=result.get("content_type"),
            endpoint=result.get("endpoint"),
            used_env_fallback=bool(result.get("used_env_fallback")),
        ),
    )


@app.post("/api/projects", response_model=CreateProjectResponse)
def create_project(request: CreateProjectRequest) -> CreateProjectResponse:
    project = store.create_project(request.name)
    return CreateProjectResponse(project_id=project.project_id, project=_public_project(project))


@app.get("/api/projects")
def list_projects():
    return {"projects": [_public_project(p) for p in store.list_projects()]}
@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    return _public_project(_project_or_404(project_id))


@app.patch("/api/projects/{project_id}")
def rename_project(project_id: str, request: RenameProjectRequest):
    _project_or_404(project_id)
    project = store.rename_project(project_id, request.name)
    return _public_project(project)


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    _project_or_404(project_id)
    store.delete_project(project_id)
    return {"ok": True}
@app.patch("/api/projects/{project_id}/settings")
def update_project_settings(project_id: str, request: ProjectSettingsRequest):
    project = _project_or_404(project_id)
    for role in ("vision", "planner"):
        key = f"{role}_api_key"
        incoming = getattr(request, key, "")
        existing = getattr(project.settings, key, "")
        if incoming in ("", SECRET_MASK) and existing:
            setattr(request, key, existing)
    project = store.update_config(project_id, request)
    return _public_project(project)


@app.post("/api/projects/{project_id}/generate")
async def generate(project_id: str, request: GenerateRequest):
    project = _project_or_404(project_id)
    language = request.language
    settings = deepcopy(request.settings) if request.settings is not None else deepcopy(project.settings)
    if request.operation_mode is not None:
        settings.operation_mode = request.operation_mode
    if request.smart_fill_policy is not None:
        settings.smart_fill_policy = request.smart_fill_policy
    store.update_config(project_id, settings)
    project = store.get_project(project_id)

    image = _decode_image(request.image_data_url)
    await _emit(project_id, "stage_started", "upload", _loc(language, "已接收草图与描述", "Sketch and description received"))
    if image is not None:
        await _emit(project_id, "stage_done", "upload", _loc(language, f"图片已解析：{request.image_name or 'uploaded image'}", f"Image parsed: {request.image_name or 'uploaded image'}"))
    else:
        await _emit(project_id, "stage_progress", "upload", _loc(language, "未提供可用图片，正在仅基于描述继续", "No usable image provided; continuing with the description only"))

    await _emit(project_id, "stage_started", "planning", _loc(language, "正在生成 FeaturePlanV3", "Generating FeaturePlanV3"))
    plan, questions = build_initial_feature_plan(request.description, request, image, settings=settings, language=language)
    plan = apply_clarification_answers(plan, request.clarification_answers, language=language)
    questions = questions_from_plan(plan, language)
    await _emit(project_id, "question_required", "planning", _loc(language, "已生成澄清问题", "Clarification questions generated"), {"questions": [q.model_dump() for q in questions]})

    blocking_questions = [q for q in questions if q.required and not q.answer]
    executable, gate_logs = _execution_gate(plan, settings, blocking_questions, language)
    if not executable and blocking_questions and settings.operation_mode == "strict":
        logs = gate_logs
        artifacts = DesignSnapshot().artifacts
        ok = False
        await _emit(project_id, "question_required", "planning", _loc(language, "严格模式等待用户确认，未执行 CAD", "Strict mode is waiting for user confirmation; CAD was not executed"), {})
    elif not executable:
        logs = gate_logs
        artifacts = DesignSnapshot().artifacts
        ok = False
        await _emit(project_id, "error", "planning", _loc(language, "FeaturePlan 未通过执行前检查，未执行 CAD", "FeaturePlan did not pass the pre-execution checks; CAD was not executed"), {"logs": logs})
    else:
        await _emit(project_id, "stage_started", "cad", _loc(language, "正在启动受控 CAD Worker", "Starting controlled CAD Worker"))
        artifacts, logs, ok = run_freecad_worker(plan, language=language)
        if settings.operation_mode == "smart":
            logs.insert(0, _loc(language, f"智能模式已执行自主设计，策略：{settings.smart_fill_policy}。", f"Smart mode executed autonomous design; policy: {settings.smart_fill_policy}."))
            if plan.assumptions:
                logs.append(_loc(language, f"智能假设数量：{len(plan.assumptions)}，请在设计评审中确认。", f"Smart assumption count: {len(plan.assumptions)}; review them in Design Review."))
    if ok:
        await _emit(project_id, "artifact_ready", "cad", _loc(language, "已生成 STEP/STL/OBJ", "STEP/STL/OBJ generated"), artifacts.model_dump())
    else:
        await _emit(project_id, "error", "cad", _loc(language, "CAD Worker 执行失败", "CAD Worker execution failed"), {"artifacts": artifacts.model_dump(), "logs": logs})

    snapshot = DesignSnapshot(
        feature_plan=plan,
        artifacts=artifacts,
        questions=questions,
        design_review=plan.design_review,
        report_markdown=_build_report(project.project_id, ok, questions, logs, language),
        logs=logs,
    )
    store.commit_snapshot(project_id, snapshot)
    return _public_project(store.get_project(project_id))


@app.post("/api/projects/{project_id}/chat")
async def chat_edit(project_id: str, request: ChatEditRequest):
    _project_or_404(project_id)
    language = request.language
    await _emit(project_id, "stage_started", "chat_edit", _loc(language, "正在处理自然语言修改", "Processing natural-language edit"))
    project = store.get_project(project_id)
    plan, questions = apply_chat_edit(project.current.feature_plan, request.message, project.settings, language=language)
    executable, gate_logs = _execution_gate(plan, project.settings, questions, language)
    if executable:
        artifacts, logs, ok = run_freecad_worker(plan, language=language)
    else:
        artifacts, logs, ok = DesignSnapshot().artifacts, gate_logs, False
    snapshot = DesignSnapshot(
        feature_plan=plan,
        artifacts=artifacts,
        questions=questions,
        design_review=plan.design_review,
        report_markdown=_build_report(project_id, ok, questions, logs, language),
        logs=project.current.logs + logs,
    )
    store.commit_snapshot(project_id, snapshot)
    await _emit(project_id, "stage_done", "chat_edit", _loc(language, "增量修改完成", "Incremental edit completed"))
    return _public_project(store.get_project(project_id))


@app.patch("/api/projects/{project_id}/features/{feature_id}")
async def patch_project_feature(project_id: str, feature_id: str, request: FeaturePatchRequest):
    project = _project_or_404(project_id)
    language = request.language
    plan = patch_feature(project.current.feature_plan, feature_id, request.model_dump(exclude_none=True))
    questions = questions_from_plan(plan, language)
    executable, gate_logs = _execution_gate(plan, project.settings, questions, language)
    if executable:
        artifacts, logs, ok = run_freecad_worker(plan, language=language)
    else:
        artifacts, logs, ok = DesignSnapshot().artifacts, gate_logs, False
    snapshot = DesignSnapshot(
        feature_plan=plan,
        artifacts=artifacts,
        questions=questions,
        design_review=plan.design_review,
        report_markdown=_build_report(project_id, ok, questions, logs, language),
        logs=project.current.logs + logs,
    )
    store.commit_snapshot(project_id, snapshot)
    await _emit(project_id, "stage_done", "feature_patch", _loc(language, f"特征 {feature_id} 已更新", f"Feature {feature_id} updated"))
    return _public_project(store.get_project(project_id))


@app.post("/api/projects/{project_id}/undo")
def undo(project_id: str):
    _project_or_404(project_id)
    return _public_project(store.undo(project_id))


@app.post("/api/projects/{project_id}/redo")
def redo(project_id: str):
    _project_or_404(project_id)
    return _public_project(store.redo(project_id))


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


def _base_feature_ready(plan) -> tuple[bool, list[str]]:
    base = plan.base_feature
    if base is None:
        return False, ["base_feature"]
    required = {
        "box_base": ("length", "width", "height"),
        "cylinder_base": ("outer_diameter", "length"),
        "hollow_cylinder": ("outer_diameter", "inner_diameter", "length"),
    }.get(base.type)
    if required is None:
        return False, [f"unsupported base type: {base.type}"]
    missing = [name for name in required if base.dimensions.get(name) is None or base.dimensions[name].value is None]
    return not missing, missing


def _execution_gate(plan, settings, questions, language: str = "zh") -> tuple[bool, list[str]]:
    """Centralize policy and geometry checks before starting a CAD subprocess."""
    logs: list[str] = []
    base_ready, missing_base = _base_feature_ready(plan)
    if not base_ready:
        logs.append(_loc(language, f"CAD Worker 未启动：主基体不完整，缺少 {', '.join(missing_base)}。", f"CAD Worker did not start: main body is incomplete; missing {', '.join(missing_base)}."))

    if plan.design_review.blocking:
        logs.extend(_loc(language, f"阻塞问题：{item}", f"Blocking issue: {item}") for item in plan.design_review.blocking)

    unresolved = [q for q in questions if q.required and not q.answer]
    if settings.operation_mode == "strict" and unresolved:
        logs.append(_loc(language, f"严格模式：存在 {len(unresolved)} 个必答问题，未确认数据不会写入模型。", f"Strict mode: {len(unresolved)} required questions pending; unconfirmed data will not be written to the model."))

    assumption_dimensions = [
        f"{feature.id}.{name}"
        for feature in ([plan.base_feature] if plan.base_feature else []) + list(plan.features)
        for name, dimension in feature.dimensions.items()
        if dimension.source == "assumption" and not dimension.confirmed_by_user
    ]
    if settings.operation_mode == "strict" and assumption_dimensions:
        logs.append(_loc(language, "严格模式：检测到未确认推断尺寸：" + ", ".join(assumption_dimensions), "Strict mode: detected unconfirmed inferred dimensions: " + ", ".join(assumption_dimensions)))

    if logs and settings.operation_mode == "strict":
        return False, logs
    if not base_ready:
        if settings.operation_mode == "smart" and settings.smart_fill_policy in {"limited_fill", "aggressive_fill", "full_autonomous"}:
            logs.append(_loc(language, "智能策略未能补全主基体，仍未执行 CAD。", "The smart policy could not complete the main body; CAD was not executed."))
        return False, logs
    if plan.design_review.blocking:
        return False, logs
    return True, logs or [_loc(language, "FeaturePlan 已通过 CAD 执行前检查。", "FeaturePlan passed the pre-CAD execution checks.")]


def _public_project(project):
    """Never send API credentials back to the browser or logs."""
    safe = deepcopy(project)
    for role in ("vision", "planner"):
        key = f"{role}_api_key"
        raw = getattr(safe.settings, key, "")
        setattr(safe.settings, key, "***configured***" if raw else "")
    return safe


async def _emit(project_id: str, event_type: str, stage: str, message: str, payload: dict | None = None) -> None:
    await events.publish(
        StageEvent(type=event_type, project_id=project_id, stage=stage, message=message, payload=payload or {})
    )


def _build_report(project_id: str, cad_ok: bool, questions, logs: list[str], language: str = "zh") -> str:
    status = _loc(language, "CAD worker 成功", "CAD worker succeeded") if cad_ok else _loc(language, "CAD worker 失败", "CAD worker failed")
    question_text = "\n".join(f"- {q.text}" for q in questions) if questions else _loc(language, "- 暂无", "- None")
    log_text = "\n".join(f"- {line}" for line in logs[-6:]) if logs else _loc(language, "- 无日志", "- No logs")
    return (
        f"### MechCAD Run\n"
        f"- Project: `{project_id}`\n"
        f"- CAD: **{status}**\n\n"
        f"### {_loc(language, '待确认问题', 'Pending Questions')}\n{question_text}\n\n"
        f"### {_loc(language, '日志', 'Logs')}\n{log_text}\n"
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



FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
mount_frontend(app, FRONTEND_DIST)

if __name__ == "__main__":
    import uvicorn

    Path("work").mkdir(exist_ok=True)
    reload_enabled = os.getenv("MECHCAD_RELOAD", "0") == "1"
    host = os.getenv("MECHCAD_HOST", "127.0.0.1")
    port = int(os.getenv("MECHCAD_PORT", "8001"))
    uvicorn.run("backend.main:app", host=host, port=port, reload=reload_enabled)
