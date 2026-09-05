from __future__ import annotations

import base64
from copy import deepcopy
import asyncio
import io
import json
import os
import threading
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
from backend.agent import AgentLoopResult, ApprovalBroker, run_agent_loop
from backend.agent.loop import build_task_message
from backend.agent.session import SessionRegistry
from backend.cad import run_freecad_worker
from backend.capabilities import CAPABILITIES, CapabilityValidationError
from backend.evidence_gate import apply_evidence_gate_result, apply_evidence_resolutions, evaluate_evidence_gate
from backend.generic_engine import build_execution_report, feature_semantics
from backend.events import EventBus
from backend.kernel_worker import get_worker_manager
from backend.process import ProcessRecorder
from backend.schemas import (
    AgentMessageRequest,
    AgentResolveRequest,
    AgentSessionView,
    AgentStartRequest,
    ArtifactSet,
    ChatEditRequest,
    CreateProjectRequest,
    CreateProjectResponse,
    DesignSnapshot,
    ExecutionReport,
    FeaturePatchRequest,
    FeaturePlanV3,
    GenerateRequest,
    KernelDeleteFeatureRequest,
    KernelUpdateFeatureRequest,
    ModelTestRequest,
    ModelTestResponse,
    ModelTestDiagnostics,
    ProjectSettingsRequest,
    ProcessStep,
    RenameProjectRequest,
    StageEvent,
)
from backend.mechcad_ai.client import (
    chat_completion_with_tools,
    has_configured_model,
    resolve_role_config,
    test_model_connection,
)
from backend.mechcad_ai.prompts import get_prompt
from backend.normalization import normalize_feature_plan
from backend.session import SessionStore
from backend.storage import artifact_path, create_run_dir
from backend.validation import apply_validation_result, validate_feature_plan
from backend.static_assets import mount_frontend


load_dotenv()

app = FastAPI(title="MechCAD IDE API", version="0.6.0")
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

# MechKernel agent loop（P1）：project_id → 运行状态。旧 FeaturePlanV3 链路完全不动。
_agent_runs: dict[str, dict] = {}
_agent_lock = threading.Lock()

# v0.10 对话式会话：每个项目一条持久 agent 会话（历史落盘 work/agent_sessions/）。
_agent_sessions = SessionRegistry(
    os.getenv("MECHCAD_SESSION_DIR") or (Path(__file__).resolve().parent.parent / "work" / "agent_sessions")
)


def _get_session(project_id: str):
    return _agent_sessions.get(project_id)


def _schedule_publish(event: StageEvent) -> None:
    asyncio.ensure_future(events.publish(event))


def _emit_from_thread_factory(project_id: str, loop: asyncio.AbstractEventLoop):
    """agent 线程用：把事件安全投递回主事件循环的 EventBus。"""

    def emit(event_type: str, message: str, payload: dict | None = None) -> None:
        event = StageEvent(type=event_type, project_id=project_id, stage="agent", message=message, payload=payload or {})
        try:
            loop.call_soon_threadsafe(_schedule_publish, event)
        except RuntimeError:
            pass  # 主循环已关闭（应用退出）

    return emit


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "mechcad-ide-api"}


@app.get("/api/capabilities")
def capabilities():
    return {
        "features": [capability.model_dump() for capability in CAPABILITIES.all()],
        "semantics": [semantics.model_dump() for semantics in feature_semantics()],
    }


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
    _stop_agent_run(project_id)
    get_worker_manager().stop(project_id)
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
    recorder = ProcessRecorder(project_id, language, publish=_publish_process_step)
    upload_step = recorder.started(
        "upload",
        _loc(language, "接收输入", "Receive input"),
        summary=_loc(language, "接收草图与功能描述", "Receiving sketch and description"),
    )
    recorder.completed(
        upload_step,
        summary=_loc(language, "草图与描述已接收", "Sketch and description received"),
        warnings=[] if image is not None else [_loc(language, "无图片", "No image")],
    )
    await _emit(project_id, "stage_started", "upload", _loc(language, "已接收草图与描述", "Sketch and description received"))
    if image is not None:
        await _emit(project_id, "stage_done", "upload", _loc(language, f"图片已解析：{request.image_name or 'uploaded image'}", f"Image parsed: {request.image_name or 'uploaded image'}"))
    else:
        await _emit(project_id, "stage_progress", "upload", _loc(language, "未提供可用图片，正在仅基于描述继续", "No usable image provided; continuing with the description only"))

    await _emit(project_id, "stage_started", "planning", _loc(language, "正在生成 FeaturePlanV3", "Generating FeaturePlanV3"))
    plan, questions = build_initial_feature_plan(request.description, request, image, settings=settings, language=language, recorder=recorder)
    plan = apply_evidence_resolutions(plan, request.evidence_resolutions, language=language)
    plan = apply_clarification_answers(plan, request.clarification_answers, language=language)
    questions = questions_from_plan(plan, language)
    await _emit(project_id, "question_required", "planning", _loc(language, "已生成澄清问题", "Clarification questions generated"), {"questions": [q.model_dump() for q in questions]})

    validation_step = recorder.started(
        "validation",
        _loc(language, "执行前校验", "Pre-execution validation"),
        summary=_loc(language, "检查尺寸、定位、依赖与模式约束", "Checking dimensions, placement, dependencies, and mode constraints"),
    )
    blocking_questions = [q for q in questions if q.required and not q.answer]
    executable, gate_logs = _execution_gate(plan, settings, blocking_questions, language, recorder, validation_step)
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
        cad_step = recorder.started(
            "cad",
            _loc(language, "CAD 建模", "CAD modeling"),
            summary=_loc(language, "受控 Worker 逐个执行特征", "Controlled worker executing features one by one"),
        )

        def on_worker_step(payload):
            try:
                recorder.ingest(ProcessStep.model_validate(payload))
            except Exception:
                pass

        artifacts, logs, ok = run_freecad_worker(plan, language=language, on_step=on_worker_step)
        if settings.operation_mode == "smart":
            logs.insert(0, _loc(language, f"智能模式已执行自主设计，策略：{settings.smart_fill_policy}。", f"Smart mode executed autonomous design; policy: {settings.smart_fill_policy}."))
            if plan.assumptions:
                logs.append(_loc(language, f"智能假设数量：{len(plan.assumptions)}，请在设计评审中确认。", f"Smart assumption count: {len(plan.assumptions)}; review them in Design Review."))
        if ok:
            recorder.completed(cad_step, summary=_loc(language, "CAD Worker 建模完成", "CAD Worker finished modeling"))
        else:
            recorder.failed(cad_step, error="; ".join(logs) or _loc(language, "CAD Worker 执行失败", "CAD Worker execution failed"))
    if ok:
        recorder.completed(validation_step, summary=_loc(language, "FeaturePlan 已通过执行前检查", "FeaturePlan passed the pre-execution checks"))
    elif blocking_questions:
        if validation_step.status != "blocked":
            recorder.blocked(validation_step, reason=_loc(language, "等待用户确认必要尺寸或定位", "Waiting for user confirmation of required dimensions or placement"))
    else:
        if validation_step.status != "blocked":
            recorder.failed(validation_step, error="; ".join(logs) or _loc(language, "FeaturePlan 未通过执行前检查", "FeaturePlan did not pass the pre-execution checks"))

    if ok:
        await _emit(project_id, "artifact_ready", "cad", _loc(language, "已生成 STEP/STL/OBJ", "STEP/STL/OBJ generated"), artifacts.model_dump())
    else:
        await _emit(project_id, "error", "cad", _loc(language, "CAD Worker 执行失败", "CAD Worker execution failed"), {"artifacts": artifacts.model_dump(), "logs": logs})

    export_step = recorder.started(
        "export",
        _loc(language, "导出", "Export"),
        summary=_loc(language, "整理 STEP/STL/OBJ 产物", "Preparing STEP/STL/OBJ artifacts"),
    )
    if ok:
        recorder.completed(export_step, summary=_loc(language, "导出完成", "Export completed"))
    else:
        recorder.failed(export_step, error=_loc(language, "没有可导出的模型产物", "No model artifacts to export"))

    snapshot = DesignSnapshot(
        feature_plan=plan,
        artifacts=artifacts,
        questions=questions,
        design_review=plan.design_review,
        execution_report=_current_execution_report(
            plan,
            artifacts,
            ok,
            settings.operation_mode or "strict",
            plan.self_checks.get("planning_source") == "local_fallback",
        ),
        report_markdown=_build_report(project.project_id, ok, questions, logs, language),
        logs=logs,
        process=recorder.steps,
    )
    store.commit_snapshot(project_id, snapshot)
    return _public_project(store.get_project(project_id))


@app.post("/api/projects/{project_id}/chat")
async def chat_edit(project_id: str, request: ChatEditRequest):
    _project_or_404(project_id)
    language = request.language
    await _emit(project_id, "stage_started", "chat_edit", _loc(language, "正在处理自然语言修改", "Processing natural-language edit"))
    project = store.get_project(project_id)
    recorder = ProcessRecorder(project_id, language, publish=_publish_process_step)
    plan, questions, edit_set, edit_steps = apply_chat_edit(project.current.feature_plan, request.message, project.settings, language=language)
    for step in edit_steps:
        recorder.ingest(step)
    validation_step = recorder.started(
        "validation",
        _loc(language, "执行前校验", "Pre-execution validation"),
        summary=_loc(language, "检查修改后的尺寸、定位与依赖", "Checking updated dimensions, placement, and dependencies"),
    )
    executable, gate_logs = _execution_gate(plan, project.settings, questions, language, recorder, validation_step)
    if executable:
        recorder.completed(validation_step, summary=_loc(language, "修改后的 FeaturePlan 已通过校验", "Updated FeaturePlan passed validation"))
        cad_step = recorder.started(
            "cad",
            _loc(language, "CAD 重建", "CAD rebuild"),
            summary=_loc(language, "重新执行修改后的特征树", "Re-executing the edited feature tree"),
        )

        def on_worker_step(payload):
            try:
                recorder.ingest(ProcessStep.model_validate(payload))
            except Exception:
                pass

        artifacts, logs, ok = run_freecad_worker(plan, language=language, on_step=on_worker_step)
        if ok:
            recorder.completed(cad_step, summary=_loc(language, "CAD 重建完成", "CAD rebuild completed"))
        else:
            recorder.failed(cad_step, error="; ".join(logs) or _loc(language, "CAD Worker 执行失败", "CAD Worker execution failed"))
    else:
        if validation_step.status != "blocked":
            recorder.blocked(validation_step, reason=_loc(language, "修改结果缺少可执行数据或存在阻塞问题", "The edit is missing executable data or has blocking issues"))
        artifacts, logs, ok = DesignSnapshot().artifacts, gate_logs, False
    export_step = recorder.started(
        "export",
        _loc(language, "导出", "Export"),
        summary=_loc(language, "整理修改后的模型产物", "Preparing edited model artifacts"),
    )
    if ok:
        recorder.completed(export_step, summary=_loc(language, "导出完成", "Export completed"))
    else:
        recorder.failed(export_step, error=_loc(language, "没有可导出的模型产物", "No model artifacts to export"))

    snapshot = DesignSnapshot(
        feature_plan=plan,
        artifacts=artifacts,
        questions=questions,
        design_review=plan.design_review,
        execution_report=_current_execution_report(
            plan,
            artifacts,
            ok,
            project.settings.operation_mode or "strict",
            plan.self_checks.get("planning_source") == "local_fallback",
        ),
        report_markdown=_build_report(project_id, ok, questions, logs, language),
        logs=project.current.logs + logs,
        process=recorder.steps,
    )
    store.commit_snapshot(project_id, snapshot)
    await _emit(project_id, "stage_done", "chat_edit", _loc(language, "增量修改完成", "Incremental edit completed"))
    return _public_project(store.get_project(project_id))


@app.patch("/api/projects/{project_id}/features/{feature_id}")
async def patch_project_feature(project_id: str, feature_id: str, request: FeaturePatchRequest):
    project = _project_or_404(project_id)
    language = request.language
    recorder = ProcessRecorder(project_id, language, publish=_publish_process_step)
    before = _feature_snapshot_from_plan(project.current.feature_plan, feature_id)
    try:
        plan = patch_feature(project.current.feature_plan, feature_id, request.model_dump(exclude_none=True))
    except CapabilityValidationError as exc:
        issues = [issue.model_dump() for issue in exc.issues]
        await _emit(
            project_id,
            "error",
            "chat_edit",
            _loc(language, "属性修改未通过能力校验", "Property edit failed capability validation"),
            {"capability_issues": issues},
        )
        raise HTTPException(
            status_code=422,
            detail={
                "message": _loc(language, "属性修改未通过能力校验", "Property edit failed capability validation"),
                "issues": issues,
            },
        )
    after = _feature_snapshot_from_plan(plan, feature_id)
    edit_step = recorder.started(
        "chat_edit",
        _loc(language, "属性修改", "Property edit"),
        summary=_loc(language, "正在修改特征参数", "Editing feature parameters"),
        feature_id=feature_id,
        operation="update",
    )
    if after is None:
        recorder.failed(edit_step, error=_loc(language, "找不到特征", "Feature not found"))
    else:
        recorder.completed(
            edit_step,
            summary=_loc(language, "属性修改已应用", "Property edit applied"),
            changed={"before": before, "after": after},
        )
    questions = questions_from_plan(plan, language)
    validation_step = recorder.started(
        "validation",
        _loc(language, "执行前校验", "Pre-execution validation"),
        summary=_loc(language, "检查属性修改后的尺寸与定位", "Checking the property edit"),
    )
    executable, gate_logs = _execution_gate(plan, project.settings, questions, language, recorder, validation_step)
    if executable:
        recorder.completed(validation_step, summary=_loc(language, "属性修改已通过校验", "Property edit passed validation"))
        cad_step = recorder.started(
            "cad",
            _loc(language, "CAD 重建", "CAD rebuild"),
            summary=_loc(language, "重新执行特征树", "Re-executing the feature tree"),
        )

        def on_worker_step(payload):
            try:
                recorder.ingest(ProcessStep.model_validate(payload))
            except Exception:
                pass

        artifacts, logs, ok = run_freecad_worker(plan, language=language, on_step=on_worker_step)
        if ok:
            recorder.completed(cad_step, summary=_loc(language, "CAD 重建完成", "CAD rebuild completed"))
        else:
            recorder.failed(cad_step, error="; ".join(logs) or _loc(language, "CAD Worker 执行失败", "CAD Worker execution failed"))
    else:
        if validation_step.status != "blocked":
            recorder.blocked(validation_step, reason=_loc(language, "属性修改缺少可执行数据或存在阻塞问题", "The property edit is missing executable data or has blocking issues"))
        artifacts, logs, ok = DesignSnapshot().artifacts, gate_logs, False
    export_step = recorder.started(
        "export",
        _loc(language, "导出", "Export"),
        summary=_loc(language, "整理模型产物", "Preparing model artifacts"),
    )
    if ok:
        recorder.completed(export_step, summary=_loc(language, "导出完成", "Export completed"))
    else:
        recorder.failed(export_step, error=_loc(language, "没有可导出的模型产物", "No model artifacts to export"))

    snapshot = DesignSnapshot(
        feature_plan=plan,
        artifacts=artifacts,
        questions=questions,
        design_review=plan.design_review,
        execution_report=_current_execution_report(
            plan,
            artifacts,
            ok,
            project.settings.operation_mode or "strict",
            plan.self_checks.get("planning_source") == "local_fallback",
        ),
        report_markdown=_build_report(project_id, ok, questions, logs, language),
        logs=project.current.logs + logs,
        process=recorder.steps,
    )
    store.commit_snapshot(project_id, snapshot)
    await _emit(project_id, "stage_done", "feature_patch", _loc(language, f"特征 {feature_id} 已更新", f"Feature {feature_id} updated"))
    return _public_project(store.get_project(project_id))


def _stop_agent_run(project_id: str) -> bool:
    with _agent_lock:
        run = _agent_runs.pop(project_id, None)
    if run is None:
        return False
    run["stop"].set()
    return True


def _launch_agent_run(project_id: str, *, text: str, image_data_url: str | None, language: str, max_steps: int, mode: str = "auto") -> None:
    """共享启动器：起 agent 线程（会话模式）。调用方需确认当前无运行中 agent。"""
    stop_event = threading.Event()
    approvals = ApprovalBroker()
    session = _get_session(project_id)
    thread = threading.Thread(
        target=_run_agent_thread,
        args=(
            project_id,
            text,
            image_data_url,
            language,
            max_steps,
            deepcopy(_project_or_404(project_id).settings),
            stop_event,
            asyncio.get_running_loop(),
            approvals,
            session,
            mode,
        ),
        daemon=True,
    )
    _agent_runs[project_id] = {"stop": stop_event, "thread": thread, "approvals": approvals}
    thread.start()


def _agent_is_running(project_id: str) -> bool:
    with _agent_lock:
        run = _agent_runs.get(project_id)
        return run is not None and run["thread"].is_alive()


@app.post("/api/projects/{project_id}/agent/message")
async def agent_message(project_id: str, request: AgentMessageRequest):
    """v0.10 对话式入口：空闲=开新任务；运行中=插话（轮间注入）。"""
    _project_or_404(project_id)
    if not request.text.strip():
        raise HTTPException(status_code=422, detail="text is required")
    if _agent_is_running(project_id):
        session = _get_session(project_id)
        session.enqueue_user(request.text, request.image_data_url)
        await _emit(
            project_id, "agent_queued", "agent",
            _loc(request.language, "消息已加入队列，将在当前步骤结束后生效。", "Message queued; takes effect after the current step."),
            {"queued": True},
        )
        return {"ok": True, "queued": True, "project_id": project_id}
    _launch_agent_run(
        project_id,
        text=request.text,
        image_data_url=request.image_data_url,
        language=request.language,
        max_steps=request.max_steps,
        mode=request.mode,
    )
    return {"ok": True, "started": True, "project_id": project_id}


@app.get("/api/projects/{project_id}/agent/session")
async def agent_session_view(project_id: str):
    _project_or_404(project_id)
    session = _get_session(project_id)
    return AgentSessionView(status=session.status, messages=session.view(), plan=session.plan_dict()).model_dump()


@app.post("/api/projects/{project_id}/agent/session/clear")
async def agent_session_clear(project_id: str):
    _project_or_404(project_id)
    if _agent_is_running(project_id):
        raise HTTPException(status_code=409, detail="Agent is running; stop it before clearing the session")
    _get_session(project_id).clear()
    return {"ok": True, "cleared": True}


@app.post("/api/projects/{project_id}/agent/start")
async def agent_start(project_id: str, request: AgentStartRequest):
    """兼容薄壳：等价于 agent/message(description)（v0.10 前的入口）。"""
    _project_or_404(project_id)
    if not request.description.strip():
        raise HTTPException(status_code=422, detail="description is required")
    with _agent_lock:
        existing = _agent_runs.get(project_id)
        if existing is not None and existing["thread"].is_alive():
            raise HTTPException(status_code=409, detail="Agent is already running for this project")
        _launch_agent_run(
            project_id,
            text=request.description,
            image_data_url=None,
            language=request.language,
            max_steps=request.max_steps,
        )
    return {"ok": True, "started": True, "project_id": project_id}


@app.post("/api/projects/{project_id}/agent/stop")
async def agent_stop(project_id: str):
    _project_or_404(project_id)
    stopped = _stop_agent_run(project_id)
    return {"ok": True, "stopped": stopped}


@app.post("/api/projects/{project_id}/agent/resolve")
async def agent_resolve(project_id: str, request: AgentResolveRequest):
    _project_or_404(project_id)
    with _agent_lock:
        run = _agent_runs.get(project_id)
    if run is None:
        raise HTTPException(status_code=409, detail="No running agent for this project")
    try:
        result = run["approvals"].resolve(request.approval_id, request.action, request.args_override)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Approval not found or already resolved: {request.approval_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "resolved": result}


def _run_agent_thread(
    project_id: str,
    text: str,
    image_data_url: str | None,
    language: str,
    max_steps: int,
    settings,
    stop_event: threading.Event,
    loop: asyncio.AbstractEventLoop,
    approvals: ApprovalBroker,
    session,
    mode: str = "auto",
) -> None:
    """后台线程：worker RPC + agent loop + 快照提交。事件经 EventBus 回主循环。"""
    emit = _emit_from_thread_factory(project_id, loop)
    try:
        if not has_configured_model(settings, "planner"):
            emit("agent_done", _loc(language, "未配置模型：请先在设置里配置 planner 模型。", "No model configured: set up the planner model in settings first."), {"ok": False, "error": "model_not_configured"})
            return
        worker = get_worker_manager().get_or_start(project_id)
        run_id, run_dir = create_run_dir()
        config = resolve_role_config(settings, "planner")

        def chat_with_tools(messages, tools, on_text_delta=None):
            return chat_completion_with_tools(settings, "planner", messages, tools, max_tokens=8192, on_text_delta=on_text_delta)

        task_message = build_task_message(text, worker, worker.capabilities(), image_data_url)
        result = run_agent_loop(
            worker=worker,
            chat_with_tools=chat_with_tools,
            protocol=config["protocol"],
            emit=emit,
            run_dir=run_dir,
            system_prompt=get_prompt("agent_modeling", language),
            language=language,
            max_steps=max(1, int(max_steps)),
            stop_event=stop_event,
            approvals=approvals,
            session=session,
            initial_user_message=task_message,
            mode=mode,
        )
        artifacts = ArtifactSet(
            run_id=run_id,
            step=result.artifacts.get("step"),
            stl=result.artifacts.get("stl"),
            execution_report=result.artifacts.get("execution_report"),
        )
        snapshot = DesignSnapshot(
            feature_plan=FeaturePlanV3(
                design_intent=text,
                part_family="agent_modeled",
                self_checks={"planning_source": "mechkernel_agent"},
            ),
            artifacts=artifacts,
            execution_report=_agent_execution_report(result),
            report_markdown=_build_agent_report(project_id, result, language),
            logs=result.logs,
        )
        store.commit_snapshot(project_id, snapshot)
        if result.ok:
            message = _loc(language, f"agent 建模完成（{result.steps} 步）。", f"Agent finished modeling in {result.steps} steps.")
        elif result.stopped:
            message = _loc(language, "agent 已按用户请求停止，当前状态已保存。", "Agent stopped by user; current state was saved.")
        else:
            message = _loc(language, f"agent 失败：{result.error}", f"Agent failed: {result.error}")
        emit("agent_done", message, {
            "ok": result.ok,
            "stopped": result.stopped,
            "steps": result.steps,
            "error": result.error,
            "artifacts": artifacts.model_dump(),
        })
    except Exception as exc:  # noqa: BLE001 —— 线程内兜底，事件上报
        emit("agent_done", _loc(language, f"agent 异常退出：{exc}", f"Agent crashed: {exc}"), {"ok": False, "error": str(exc)})
    finally:
        with _agent_lock:
            run = _agent_runs.get(project_id)
            if run is not None and run["stop"] is stop_event:
                _agent_runs.pop(project_id, None)


def _agent_execution_report(result: AgentLoopResult) -> ExecutionReport:
    """agent 快照的执行报告：FeaturePlanV3 不再承载执行语义（D1）。"""
    return ExecutionReport(
        execution_ok=bool(result.ok and not result.error),
        plan_complete=bool(result.ok and not result.stopped),
        geometry_valid=bool(result.volume),
        production_ready=False,
        fallback_used=False,
        engine="mechkernel",
        details=[line for line in result.logs if line][-20:],
        feature_graph=result.feature_graph,
        feature_tree=result.feature_tree,
        agent_steps=result.steps,
        agent_final_text=result.final_text,
        agent_stopped=result.stopped,
        volume=result.volume,
    )


def _build_agent_report(project_id: str, result: AgentLoopResult, language: str) -> str:
    status = _loc(language, "成功", "succeeded") if result.ok else (_loc(language, "已停止", "stopped") if result.stopped else _loc(language, "失败", "failed"))
    log_text = "\n".join(f"- {line}" for line in result.logs[-12:]) or _loc(language, "- 无日志", "- No logs")
    return (
        f"### MechKernel Agent Run\n"
        f"- Project: `{project_id}`\n"
        f"- Engine: **mechkernel**\n"
        f"- Steps: {result.steps}\n"
        f"- Status: **{status}**\n\n"
        f"### {_loc(language, '日志', 'Logs')}\n{log_text}\n"
    )


def _kernel_worker_or_none(project_id: str):
    """取项目存活 kernel worker；没有或已死则返回 None（触发 legacy 回退）。"""
    worker = get_worker_manager().get(project_id)
    return worker if worker is not None and worker.is_alive() else None


@app.get("/api/projects/{project_id}/kernel/feature_tree")
def kernel_feature_tree(project_id: str):
    _project_or_404(project_id)
    worker = _kernel_worker_or_none(project_id)
    if worker is None:
        return {"graph": {"nodes": {}, "edges": {}}, "op_history": [], "narrative": [], "node_count": 0}
    data = worker.feature_tree()
    data["node_count"] = len(data.get("graph", {}).get("nodes", {}))
    return data


@app.post("/api/projects/{project_id}/kernel/update_feature")
async def kernel_update_feature(project_id: str, request: KernelUpdateFeatureRequest):
    return await _kernel_edit(project_id, lambda worker: worker.update_feature(request.feature_id, request.new_params))


@app.post("/api/projects/{project_id}/kernel/delete_feature")
async def kernel_delete_feature(project_id: str, request: KernelDeleteFeatureRequest):
    return await _kernel_edit(project_id, lambda worker: worker.delete_feature(request.feature_id))


@app.post("/api/projects/{project_id}/kernel/undo")
async def kernel_undo(project_id: str):
    return await _kernel_edit(project_id, lambda worker: worker.undo(1))


@app.post("/api/projects/{project_id}/kernel/redo")
async def kernel_redo(project_id: str):
    return await _kernel_edit(project_id, lambda worker: worker.redo(1))


async def _kernel_edit(project_id: str, action) -> dict:
    """kernel 编辑通用收尾：执行 op → 重导 STL/STEP → 提交快照 → 发 artifact_ready。"""
    _project_or_404(project_id)
    worker = _kernel_worker_or_none(project_id)
    if worker is None:
        raise HTTPException(status_code=409, detail="No alive kernel worker for this project; run the agent first")
    try:
        result = action(worker)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"kernel op failed: {exc}") from exc

    _commit_kernel_state_snapshot(project_id, worker)
    tree = worker.feature_tree()
    artifacts = store.get_project(project_id).current.artifacts
    await _emit(project_id, "artifact_ready", "cad", "kernel 手动编辑完成，已重导模型", artifacts.model_dump())
    return {"ok": True, "result": result, "artifacts": artifacts.model_dump(), "feature_tree": tree, "project": _public_project(store.get_project(project_id))}


@app.post("/api/projects/{project_id}/undo")
def undo(project_id: str):
    _project_or_404(project_id)
    # 存活 kernel worker → 走内核 undo（重放历史）；否则回退 legacy 快照 undo。
    worker = _kernel_worker_or_none(project_id)
    if worker is not None:
        worker.undo(1)
        _commit_kernel_state_snapshot(project_id, worker)
        return _public_project(store.get_project(project_id))
    project = store.undo(project_id)
    return _public_project(_refresh_restored_validation(project))


@app.post("/api/projects/{project_id}/redo")
def redo(project_id: str):
    _project_or_404(project_id)
    worker = _kernel_worker_or_none(project_id)
    if worker is not None:
        worker.redo(1)
        _commit_kernel_state_snapshot(project_id, worker)
        return _public_project(store.get_project(project_id))
    project = store.redo(project_id)
    return _public_project(_refresh_restored_validation(project))


def _commit_kernel_state_snapshot(project_id: str, worker) -> None:
    """把内核当前状态导出为快照（undo/redo 或编辑后提交），供前端/回溯使用。"""
    run_id, run_dir = create_run_dir()
    stl_path = run_dir / "model.stl"
    step_path = run_dir / "model.step"
    stl = None
    step = None
    try:
        worker.export_mesh(str(stl_path))
        stl = str(stl_path)
    except Exception:  # noqa: BLE001
        pass
    try:
        worker.export_step(str(step_path))
        step = str(step_path)
    except Exception:  # noqa: BLE001
        pass
    tree = worker.feature_tree()
    artifacts = ArtifactSet(run_id=run_id, stl=stl, step=step, execution_report=str(run_dir / "execution_report.json"))
    op_history = tree.get("op_history") or []
    (run_dir / "execution_report.json").write_text(json.dumps({
        "ok": True, "engine": "mechkernel", "worker": "mechkernel-agent",
        "feature_graph": tree.get("graph") or {},
        "op_history": op_history,
        "narrative": tree.get("narrative") or [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    snapshot = DesignSnapshot(
        feature_plan=FeaturePlanV3(part_family="agent_modeled", self_checks={"planning_source": "mechkernel_agent"}),
        artifacts=artifacts,
        execution_report=ExecutionReport(
            execution_ok=True,
            plan_complete=len(op_history) > 0,
            geometry_valid=bool(stl or step),
            production_ready=False,
            fallback_used=False,
            engine="mechkernel",
            details=["kernel 状态快照"],
            feature_graph=tree.get("graph") or {},
            feature_tree=tree,
            agent_steps=len(op_history),
            agent_stopped=False,
        ),
        report_markdown=f"### MechKernel State Snapshot\n- Project: `{project_id}`\n- Run: `{run_id}`\n",
        logs=["kernel 状态已提交快照"],
    )
    store.commit_snapshot(project_id, snapshot)


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


def _refresh_restored_validation(project):
    """Re-run deterministic checks on a restored snapshot without rebuilding CAD."""
    if project.current.feature_plan is not None:
        mode = project.settings.operation_mode or "strict"
        normalize_feature_plan(project.current.feature_plan)
        result = validate_feature_plan(project.current.feature_plan, mode, "zh")
        apply_validation_result(project.current.feature_plan, result, mode)
        evidence_result = evaluate_evidence_gate(project.current.feature_plan, mode)
        apply_evidence_gate_result(project.current.feature_plan, evidence_result, mode)
        store.save_project(project.project_id)
    return project


def _execution_gate(
    plan,
    settings,
    questions,
    language: str = "zh",
    recorder=None,
    validation_step=None,
) -> tuple[bool, list[str]]:
    """Centralize deterministic validation and evidence policy before CAD."""
    mode = settings.operation_mode or "strict"
    normalize_feature_plan(plan)
    result = validate_feature_plan(plan, mode, language)
    apply_validation_result(plan, result, mode)
    evidence_result = evaluate_evidence_gate(plan, mode)
    apply_evidence_gate_result(plan, evidence_result, mode)
    logs: list[str] = []
    if evidence_result.blocking:
        logs.append(
            _loc(
                language,
                f"证据冲突阻止执行：{evidence_result.reason}",
                f"Evidence conflict blocks execution: {evidence_result.reason}",
            )
        )
        if recorder is not None and validation_step is not None:
            recorder.blocked(
                validation_step,
                reason=evidence_result.reason,
                detail=json.dumps(evidence_result.model_dump(), ensure_ascii=False),
            )
        return False, logs
    if result["blocking"]:
        logs.extend(_loc(language, f"自检阻塞：{item}", f"Validation blocked: {item}") for item in result["blocking"])
    unresolved = [q for q in questions if q.required and not q.answer]
    if mode == "strict" and unresolved:
        logs.append(_loc(language, f"严格模式：存在 {len(unresolved)} 个必答问题，未确认数据不会写入模型。", f"Strict mode: {len(unresolved)} required questions pending; unconfirmed data will not be written to the model."))
    if mode == "strict":
        if logs:
            return False, logs
        return True, logs or [_loc(language, "FeaturePlan 已通过 CAD 执行前检查。", "FeaturePlan passed the pre-CAD execution checks.")]
    if not result["base_ready"]:
        logs.append(_loc(language, "智能策略未能补全主基体，仍无法执行 CAD。", "The smart policy could not complete the main body; CAD was not executed."))
        return False, logs
    if result["blocking"]:
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


def _feature_snapshot_from_plan(plan, feature_id: str) -> dict | None:
    candidates = [plan.base_feature] if plan.base_feature else []
    candidates.extend(plan.features)
    feature = next((candidate for candidate in candidates if candidate.id == feature_id), None)
    return feature.model_dump() if feature else None


async def _publish_process_step(project_id: str, event_type: str, step: ProcessStep) -> None:
    payload = {"process_step": step.model_dump()}
    if step.detail:
        payload["logs"] = [step.detail]
    await _emit(project_id, event_type, step.stage, step.label, payload)


def _worker_report(artifacts) -> dict:
    path = getattr(artifacts, "execution_report", None)
    if path and Path(path).exists():
        try:
            value = json.loads(Path(path).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    return {}


def _current_execution_report(plan, artifacts, ok, mode, fallback_used) -> ExecutionReport:
    return build_execution_report(
        plan,
        _worker_report(artifacts),
        execution_ok=ok,
        fallback_used=fallback_used,
        mode=mode,
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
