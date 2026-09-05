import { useEffect, useMemo, useRef, useState } from "react";
import {
  artifactUrl,
  createProject,
  deleteProject,
  deleteKernelFeature,
  fetchAgentSession,
  fetchKernelFeatureTree,
  fetchProject,
  generateProject,
  listProjects,
  redo,
  renameProject,
  resolveAgent,
  resolveWsRoot,
  sendAgentMessage,
  stopAgent,
  updateKernelFeature,
  updateProjectSettings,
  undo,
  type Approval,
  type KernelFeatureTree,
  type ModelConfig,
  type PlanStep,
  type ProcessStep,
  type ProjectState,
} from "./api";
import ApprovalPanel from "./ApprovalPanel";
import ChatColumn from "./layout/ChatColumn";
import StructurePanel from "./layout/StructurePanel";
import TopCommandBar from "./layout/TopCommandBar";
import SettingsDialog from "./SettingsDialog";
import StartupScreen from "./StartupScreen";
import Viewport from "./Viewport";
import { useT } from "./i18n";
import {
  DEFAULT_SETTINGS,
  markStartupSeen,
  readStartupMode,
  shouldShowStartup,
  useAppStore,
  writeStartupMode,
} from "./store";

const statusLabelKeys: Record<string, string> = {
  empty: "status.empty",
  ready: "status.ready",
  analyzing: "status.analyzing",
  awaiting_questions: "status.awaiting_questions",
  ready_to_review: "status.ready_to_review",
  failed: "status.failed",
};

export default function App() {
  const t = useT();
  const {
    project,
    description,
    imageFile,
    settings,
    selectedFeatureId,
    chatMessage,
    events,
    processSteps,
    busy,
    error,
    backendState,
    recentProjects,
    startupMode,
    showStartup,
    settingsDirty,
    settingsSaving,
    settingsNotice,
    ui,
    setProject,
    setImageFile,
    setSettings,
    setSelectedFeatureId,
    setChatMessage,
    addEvents,
    addProcessStep,
    clearEvents,
    setBusy,
    setError,
    setBackendState,
    setRecentProjects,
    setStartupMode,
    setShowStartup,
    setSettingsDirty,
    setSettingsSaving,
    setSettingsNotice,
    setProcessSteps,
    setUi,
    agentRunning,
    agentSteps,
    agentLastOp,
    pendingApprovals,
    chat,
    plan: agentPlan,
    setAgentRunning,
    setAgentSteps,
    setAgentLastOp,
    setPendingApprovals,
    setPlan,
    setChat,
    appendChatUser,
    appendChatAssistantDelta,
    attachChatToolCard,
    attachChatSnapshot,
    finalizeChatAssistant,
  } = useAppStore();
  const bootRef = useRef(false);
  const chatInputRef = useRef<HTMLInputElement>(null);
  const [planMode, setPlanMode] = useState(false);
  const language = useAppStore((state) => state.language);

  /** 统一发送入口：空闲=开新任务；运行中=插话。首条消息携带草图图片。 */
  const handleSendAgentMessage = async (text: string) => {
    if (!project?.project_id || !text.trim()) {
      return;
    }
    const attachImage = chat.length === 0 && imageFile ? await fileToDataUrl(imageFile) : null;
    setError("");
    appendChatUser(text.trim(), Boolean(attachImage));
    setAgentRunning(true);
    setAgentSteps(0);
    setAgentLastOp("");
    setPendingApprovals([]);
    try {
      await sendAgentMessage(project.project_id, {
        text: text.trim(),
        image_data_url: attachImage,
        language,
        mode: planMode ? "plan" : "auto",
      });
    } catch (err) {
      setAgentRunning(false);
      setError(t("app.agent.start_failed", { err: String(err) }));
    }
  };

  const handleAgentStop = async () => {
    if (!project?.project_id) {
      return;
    }
    try {
      await stopAgent(project.project_id);
    } catch {
      // 停止失败不打断 UI；agent 完成事件会自行收尾
    }
  };

  const handleAgentResolve = async (approval: Approval, action: "approve" | "reject" | "edit", argsOverride?: Record<string, unknown>) => {
    if (!project?.project_id) {
      return;
    }
    try {
      await resolveAgent(project.project_id, { approval_id: approval.approval_id, action, args_override: argsOverride });
      setPendingApprovals(pendingApprovals.filter((item) => item.approval_id !== approval.approval_id));
    } catch (err) {
      setError(t("app.agent.resolve_failed", { err: String(err) }));
    }
  };

  const [kernelTree, setKernelTree] = useState<KernelFeatureTree>({
    graph: { nodes: {}, edges: {} },
    op_history: [],
    narrative: [],
    node_count: 0,
  });

  const refreshKernelTree = async () => {
    if (!project?.project_id) {
      return;
    }
    try {
      const tree = await fetchKernelFeatureTree(project.project_id);
      setKernelTree(tree);
    } catch {
      // 无存活 worker 时为空树；静默
    }
  };

  useEffect(() => {
    void refreshKernelTree();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.project_id]);

  const kernelNodes = kernelTree.graph?.nodes ?? {};

  const onSaveKernelFeature = async (featureId: string, newParams: Record<string, unknown>) => {
    if (!project?.project_id) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const next = await updateKernelFeature(project.project_id, featureId, newParams);
      replaceProject(next.project);
      await refreshKernelTree();
      setSettingsDirty(false);
    } catch (err) {
      setError(t("app.kernel.edit_failed", { err: String(err) }));
    } finally {
      setBusy(false);
    }
  };

  const onDeleteKernelFeature = async (featureId: string) => {
    if (!project?.project_id) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const next = await deleteKernelFeature(project.project_id, featureId);
      replaceProject(next.project);
      await refreshKernelTree();
    } catch (err) {
      setError(t("app.kernel.delete_failed", { err: String(err) }));
    } finally {
      setBusy(false);
    }
  };

  const refreshProjects = async () => {
    try {
      const result = await listProjects();
      setRecentProjects(result.projects);
      setBackendState("connected");
    } catch (err) {
      setBackendState("offline");
      setError(t("app.backend.offline", { err: String(err) }));
    }
  };

  const enterWorkspace = () => {
    if (startupMode === "first") {
      markStartupSeen();
    }
    setShowStartup(false);
  };

  const handleNewProject = async () => {
    // 已有未提交工作（会话或特征）时先确认，避免误清当前项目
    if (project && !window.confirm(t("app.new.confirm"))) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const { project: next } = await createProject(t("app.project.untitled"));
      setProject(next);
      setSettings(next.settings);
      setSettingsDirty(false);
      setSelectedFeatureId("");
      clearEvents();
      setProcessSteps([]);
      setChat([]);
      setPlan(null);
      enterWorkspace();
      setBackendState("connected");
      await refreshProjects();
    } catch (err) {
      setBackendState("offline");
      setError(t("app.create.failed", { err: String(err) }));
    } finally {
      setBusy(false);
    }
  };

  const handleOpenProject = async (projectId: string) => {
    setBusy(true);
    setError("");
    try {
      const next = await fetchProject(projectId);
      const fallback = project?.project_id === projectId ? settings : { ...DEFAULT_SETTINGS };
      const mergedSettings = mergeSettings(next.settings, fallback);
      setProject({ ...next, settings: mergedSettings });
      setSettings(mergedSettings);
      setSettingsDirty(false);
      setSelectedFeatureId("");
      setProcessSteps([]);
      enterWorkspace();
      setBackendState("connected");
      await refreshProjects();
      // 恢复该项目的 agent 会话历史（失败静默——空会话即可）
      try {
        const sessionView = await fetchAgentSession(projectId);
        setChat(sessionView.messages.map((message, index) => ({
          id: `chat-history-${index}`,
          role: message.role,
          text: message.text,
          hasImage: message.has_image,
          status: "done" as const,
          tools: [],
          snapshots: [],
        })));
        setPlan(sessionView.plan?.steps?.length ? sessionView.plan : null);
      } catch {
        setChat([]);
        setPlan(null);
      }
    } catch (err) {
      setError(t("app.open.failed", { err: String(err) }));
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteProject = async (projectId: string) => {
    const name = project?.project_id === projectId ? project.name : projectId;
    if (!window.confirm(t("app.delete.confirm", { name }))) {
      return;
    }
    try {
      await deleteProject(projectId);
      if (project?.project_id === projectId) {
        setProject(null);
        setShowStartup(true);
      }
      await refreshProjects();
    } catch (err) {
      setError(t("app.delete.failed", { err: String(err) }));
    }
  };

  const handleRenameProject = async (projectId: string, name: string) => {
    const clean = name.trim();
    if (!clean) {
      setError(t("startup.rename.empty"));
      return;
    }
    setError("");
    try {
      const next = await renameProject(projectId, clean);
      if (project?.project_id === projectId) {
        setProject({ ...project, name: next.name, updated_at: next.updated_at });
      }
      await refreshProjects();
    } catch (err) {
      setError(t("app.rename.failed", { err: String(err) }));
    }
  };

  const handleStartupModeChange = (mode: typeof startupMode) => {
    writeStartupMode(mode);
    setStartupMode(mode);
    if (mode === "off" && !project) {
      void handleNewProject();
    }
  };

  useEffect(() => {
    const mode = readStartupMode();
    const shouldShow = shouldShowStartup(mode);
    setStartupMode(mode);
    setShowStartup(shouldShow);
    void refreshProjects();
    if (!shouldShow && !bootRef.current) {
      bootRef.current = true;
      void handleNewProject();
    }
    // Boot only once; project creation is guarded by bootRef.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!project?.project_id) {
      return;
    }

    const wsRoot = resolveWsRoot();
    const socket = new WebSocket(`${wsRoot}/ws/projects/${project.project_id}`);
    let socketConnected = false;

    socket.onopen = () => {
      socketConnected = true;
      addEvents([t("app.events.connected")]);
    };
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data);
      if (typeof event.type === "string" && event.type.startsWith("process_step_") && event.payload?.process_step) {
        addProcessStep(event.payload.process_step as ProcessStep);
      }
      if (event.type === "agent_text_delta") {
        appendChatAssistantDelta(String(event.message ?? event.payload?.text ?? ""));
      }
      if (event.type === "agent_step") {
        setAgentSteps(Number(event.payload?.step || 0));
        setAgentLastOp(String(event.payload?.op || event.message || ""));
        attachChatToolCard({
          step: Number(event.payload?.step || 0),
          op: String(event.payload?.op || event.message || ""),
          argsPreview: event.payload?.args_preview ? String(event.payload.args_preview) : undefined,
          success: typeof event.payload?.success === "boolean" ? event.payload.success : undefined,
          summary: event.payload?.summary ? String(event.payload.summary) : undefined,
          autofix: Boolean(event.payload?.autofix),
          message: event.payload?.message ? String(event.payload.message) : undefined,
        });
      }
      if (event.type === "agent_queued") {
        addEvents([`${event.stage}: ${event.message}`]);
      }
      if (event.type === "agent_snapshot") {
        const url = String(event.payload?.url || "");
        if (url) {
          attachChatSnapshot(url);
        }
      }
      if (event.type === "plan_updated") {
        setPlan({
          summary: event.payload?.summary ? String(event.payload.summary) : undefined,
          steps: Array.isArray(event.payload?.steps) ? (event.payload.steps as PlanStep[]) : [],
          approved: Boolean(event.payload?.approved),
        });
      }
      if (event.type === "approval_required") {
        const approval: Approval = {
          approval_id: String(event.payload?.approval_id ?? ""),
          kind: (event.payload?.kind as Approval["kind"]) ?? "ask_user",
          op: String(event.payload?.op ?? "ask_user"),
          args: (event.payload?.args as Record<string, unknown>) ?? {},
          message: String(event.message ?? ""),
          options: (event.payload?.options as Record<string, unknown>) ?? {},
          context: String(event.payload?.context ?? ""),
        };
        if (approval.approval_id && !pendingApprovals.some((item) => item.approval_id === approval.approval_id)) {
          setPendingApprovals([...pendingApprovals, approval]);
          setUi({ rightTab: "assistant", rightDrawerOpen: true });
        }
      }
      if (event.type === "agent_done") {
        setAgentRunning(false);
        finalizeChatAssistant();
        void (async () => {
          try {
            const next = await fetchProject(project.project_id);
            setProject({ ...next, settings: mergeSettings(next.settings, settings) });
            await refreshKernelTree();
          } catch {
            // 保留当前状态；事件流里已有错误信息
          }
        })();
      }
      const payloadLogs = Array.isArray(event.payload?.logs) ? event.payload.logs : [];
      const detail = payloadLogs.map((item: unknown) => String(item));
      addEvents([`${event.stage}: ${event.message}`, ...detail]);
    };
    socket.onerror = () => {
      if (!socketConnected) {
        addEvents([t("app.events.unavailable")]);
      }
    };
    socket.onclose = () => {
      if (socketConnected) {
        addEvents([t("app.events.closed")]);
      }
    };

    return () => socket.close();
  }, [project?.project_id, addEvents, language]);

  const plan = project?.current.feature_plan;
  const questions = project?.current.questions || [];
  const unresolved = plan?.unresolved || [];
  const review = plan?.design_review;

  const features = useMemo(() => {
    if (!plan) {
      return [];
    }
    return [plan.base_feature, ...(plan.features || [])].filter(Boolean);
  }, [plan]);

  const selectedFeature = useMemo(
    () => features.find((feature: any) => feature.id === selectedFeatureId) || features[0],
    [features, selectedFeatureId],
  );

  useEffect(() => {
    if (selectedFeature?.id) {
      setSelectedFeatureId(selectedFeature.id);
    } else if (!features.length) {
      setSelectedFeatureId("");
    }
  }, [features.length, selectedFeature?.id, project?.current.id, setSelectedFeatureId]);

  useEffect(() => {
    if (questions.some((question) => question.required !== false && !question.answer)) {
      setUi({ rightTab: "assistant", rightDrawerOpen: true });
    }
  }, [questions, setUi]);

  const executionReport = project?.current.execution_report;
  const evidence = plan?.evidence?.items || [];
  const evidenceConflicts = plan?.evidence?.conflicts || [];
  const designIntent = plan?.design_intent_details;
  const runId = project?.current.artifacts.run_id;
  // agent 路径只产出 stl/step（无 obj）；hasModel 只看 stl
  const hasModel = Boolean(project?.current.artifacts.stl);
  const canUndo = Boolean(project?.history?.length);
  const canRedo = Boolean(project?.redo_stack?.length);
  const hasRequiredQuestions = questions.some((question) => question.required !== false && !question.answer);
  const modeLabel = settings.operation_mode === "strict" ? t("mode.strict") : t("mode.smart");

  const status = !project
    ? "empty"
    : error && !busy
      ? "failed"
      : busy || agentRunning
        ? "analyzing"
        : hasRequiredQuestions
          ? "awaiting_questions"
          : hasModel
            ? "ready_to_review"
            : description.trim() || imageFile
              ? "ready"
              : "empty";

  const replaceProject = (next: ProjectState, draftSettings: ModelConfig = settings) => {
    const mergedSettings = mergeSettings(next.settings, draftSettings);
    setProject({ ...next, settings: mergedSettings });
    setSettings(mergedSettings);
    setProcessSteps(next.current?.process || []);
    setBackendState("connected");
  };

  const onSettingsChange = (next: ModelConfig) => {
    setSettings(next);
    setSettingsDirty(true);
    setSettingsNotice("");
  };

  const onApplySettings = async (nextSettings: ModelConfig = settings) => {
    if (!project || settingsSaving) {
      return;
    }
    setSettingsSaving(true);
    setError("");
    setSettingsNotice("");
    try {
      const sanitized = { ...nextSettings };
      for (const role of ["vision", "planner"] as const) {
        const key = `${role}_api_key` as "vision_api_key" | "planner_api_key";
        if (sanitized[key] === SECRET_MASK) sanitized[key] = "";
      }
      const next = await updateProjectSettings(project.project_id, sanitized);
      replaceProject(next, nextSettings);
      setSettingsDirty(false);
      setSettingsNotice(t("app.settings.saved"));
    } catch (err) {
      setSettingsDirty(true);
      setSettingsNotice(t("app.save.failed", { err: String(err) }));
    } finally {
      setSettingsSaving(false);
    }
  };

  const onChat = async () => {
    if (!project || !chatMessage.trim()) {
      return;
    }
    const text = chatMessage.trim();
    setChatMessage("");
    setError("");
    await handleSendAgentMessage(text);
  };

  const onClarificationContinue = async (answers: string) => {
    if (!project) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const imageDataUrl = imageFile ? await fileToDataUrl(imageFile) : null;
      const next = await generateProject(project.project_id, {
        description,
        operation_mode: settings.operation_mode,
        smart_fill_policy: settings.smart_fill_policy,
        model_config: settings,
        image_data_url: imageDataUrl,
        image_name: imageFile?.name,
        clarification_answers: answers,
        language,
      });
      replaceProject(next);
      setSettingsDirty(false);
      await refreshProjects();
    } catch (err) {
      setError(t("app.question.failed", { err: String(err) }));
    } finally {
      setBusy(false);
    }
  };

  const onUndo = () => {
    if (project && canUndo) {
      undo(project.project_id).then(replaceProject).catch((err) => setError(t("app.undo.failed", { err: String(err) })));
    }
  };

  const onRedo = () => {
    if (project && canRedo) {
      redo(project.project_id).then(replaceProject).catch((err) => setError(t("app.redo.failed", { err: String(err) })));
    }
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const mod = event.ctrlKey || event.metaKey;
      const key = event.key.toLowerCase();
      if (mod && key === "z") {
        event.preventDefault();
        if (event.shiftKey) onRedo();
        else onUndo();
      } else if (mod && key === "y") {
        event.preventDefault();
        onRedo();
      } else if (mod && key === "s") {
        event.preventDefault();
        if (project && settingsDirty) void onApplySettings();
      } else if (mod && key === "g") {
        event.preventDefault();
        chatInputRef.current?.focus();
      } else if (mod && key === ",") {
        event.preventDefault();
        setUi({ settingsOpen: true });
      } else if (event.key === "Escape") {
        if (ui.settingsOpen) {
          setUi({ settingsOpen: false });
        } else {
          setUi({ leftDrawerOpen: false, rightDrawerOpen: false });
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // Re-register whenever capabilities change so shortcuts stay accurate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.project_id, canUndo, canRedo, settingsDirty, settings, ui.settingsOpen]);

  if (showStartup || !project) {
    return (
      <StartupScreen
        projects={recentProjects}
        backendState={backendState}
        startupMode={startupMode}
        error={error}
        onCreate={() => void handleNewProject()}
        onOpen={(id) => void handleOpenProject(id)}
        onDelete={(id) => void handleDeleteProject(id)}
        onRename={(id, name) => void handleRenameProject(id, name)}
        onModeChange={handleStartupModeChange}
      />
    );
  }

  const renameCurrentProject = () => {
    if (!project) return;
    const name = window.prompt(t("startup.rename.prompt"), project.name);
    if (name && name.trim()) void handleRenameProject(project.project_id, name);
  };

  const engineLabel = plan ? t("app.engine.ready") : t("app.engine.waiting");

  return (
    <main className={`app-shell ide-shell${ui.focusMode ? " focus-mode" : ""}`}>
      <TopCommandBar
        backendState={backendState}
        busy={busy}
        canRedo={canRedo}
        canUndo={canUndo}
        engineLabel={engineLabel}
        projectName={project.name || "Varen CAD IDE"}
        statusLabel={t(statusLabelKeys[status])}
        onRedo={onRedo}
        onUndo={onUndo}
        onNewProject={() => void handleNewProject()}
        onRenameProject={renameCurrentProject}
        onBackToStart={() => setShowStartup(true)}
        onOpenSettings={() => setUi({ settingsOpen: true })}
      />

      {error && (
        <div className="status-banner error" role="alert">
          <span>{error}</span>
          <button type="button" className="status-banner-close" onClick={() => setError("")} aria-label={t("app.error.dismiss")}>
            ×
          </button>
        </div>
      )}

      <section className="workspace-shell">
        <div className="workspace-main">
          <section className="workspace-center" aria-label={t("app.viewport.aria")}>
            {agentRunning && (
              <div className="agent-runbar">
                <span className="agent-strip-step">{t("agent.step", { step: agentSteps })}</span>
                <span className="agent-strip-op" title={agentLastOp}>{agentLastOp}</span>
                <button type="button" className="agent-strip-stop" onClick={() => void handleAgentStop()}>
                  {t("agent.stop")}
                </button>
              </div>
            )}
          <Viewport
            objUrl={artifactUrl(runId, "obj")}
            stlUrl={artifactUrl(runId, "stl")}
            breadcrumb={t("app.breadcrumb", {
              partFamily: plan?.part_family || "FeaturePlan",
              feature: selectedFeature?.id || t("app.breadcrumb.none"),
            })}
            statusLabel={t(statusLabelKeys[status])}
          />
          <div className="artifact-row">
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "step")}>STEP</a>
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "stl")}>STL</a>
            {artifactUrl(runId, "obj") && (
              <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "obj")}>OBJ</a>
            )}
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "execution_report")}>{t("app.artifact.report")}</a>
          </div>
          </section>

          <StructurePanel
            busy={busy}
            kernelTree={kernelTree}
            kernelSelectedFeature={kernelNodes[selectedFeatureId] ?? null}
            selectedFeatureId={selectedFeatureId}
            partFamily={plan?.part_family}
            modeLabel={modeLabel}
            processSteps={processSteps}
            featurePlan={project.current.feature_plan}
            executionReport={executionReport}
            evidence={evidence}
            evidenceConflicts={evidenceConflicts}
            designIntent={designIntent}
            review={review}
            unresolved={unresolved}
            runId={runId}
            engineLabel={engineLabel}
            onSelectKernelFeature={setSelectedFeatureId}
            onSaveKernelFeature={(fid, params) => void onSaveKernelFeature(fid, params)}
            onDeleteKernelFeature={(fid) => void onDeleteKernelFeature(fid)}
            onSelectProcessStep={(featureId) => setSelectedFeatureId(featureId)}
          />
        </div>

        <ChatColumn
          chat={chat}
          chatMessage={chatMessage}
          busy={busy}
          engineLabel={engineLabel}
          pendingApprovals={pendingApprovals}
          questions={questions}
          imageFile={imageFile}
          plan={agentPlan}
          planMode={planMode}
          onPlanModeChange={setPlanMode}
          onResolveApproval={(approval, action, argsOverride) => void handleAgentResolve(approval, action, argsOverride)}
          onClarificationContinue={(answers) => void onClarificationContinue(answers)}
          onChatMessageChange={setChatMessage}
          onSendChat={() => void onChat()}
          onImageChange={setImageFile}
          inputRef={chatInputRef}
        />
      </section>

      <footer className="bottom-statusbar">
        <span>{t("app.statusbar.coords")}</span>
        <span>{t(statusLabelKeys[status])}</span>
        <span>{t("app.statusbar.shortcuts")}</span>
      </footer>

      <SettingsDialog
        open={ui.settingsOpen}
        settings={settings}
        dirty={settingsDirty}
        saving={settingsSaving}
        notice={settingsNotice}
        startupMode={startupMode}
        onClose={() => setUi({ settingsOpen: false })}
        onChange={onSettingsChange}
        onApply={() => void onApplySettings()}
        onStartupModeChange={handleStartupModeChange}
      />
    </main>
  );
}

const SECRET_MASK = "***configured***";

function mergeSettings(publicSettings: ModelConfig, draftSettings: ModelConfig): ModelConfig {
  const merged = { ...publicSettings };
  for (const role of ["vision", "planner"] as const) {
    const key = `${role}_api_key` as "vision_api_key" | "planner_api_key";
    if (publicSettings[key] === SECRET_MASK) {
      if (draftSettings[key] && draftSettings[key] !== SECRET_MASK) {
        merged[key] = draftSettings[key];
      } else {
        merged[key] = "";
      }
    }
  }
  return merged;
}

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
