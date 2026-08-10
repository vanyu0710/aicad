import { useEffect, useMemo, useRef, type MouseEvent as ReactMouseEvent } from "react";
import {
  API_ROOT,
  artifactUrl,
  chatProject,
  createProject,
  deleteProject,
  fetchProject,
  generateProject,
  listProjects,
  patchFeature,
  redo,
  updateProjectSettings,
  undo,
  type ModelConfig,
  type ProjectState,
} from "./api";
import LeftManager from "./layout/LeftManager";
import TaskPane from "./layout/TaskPane";
import TopCommandBar from "./layout/TopCommandBar";
import SettingsDialog from "./SettingsDialog";
import StartupScreen from "./StartupScreen";
import Viewport from "./Viewport";
import {
  markStartupSeen,
  readStartupMode,
  shouldShowStartup,
  useAppStore,
  writeStartupMode,
} from "./store";

const statusLabels: Record<string, string> = {
  empty: "等待输入",
  ready: "可以生成",
  analyzing: "正在分析",
  awaiting_questions: "等待确认尺寸",
  ready_to_review: "模型已生成",
  failed: "执行失败",
};

function getModeLabel(settings: ModelConfig) {
  return settings.operation_mode === "strict" ? "严格模式" : "智能模式";
}

export default function App() {
  const {
    project,
    description,
    imageFile,
    settings,
    selectedFeatureId,
    chatMessage,
    events,
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
    setDescription,
    setImageFile,
    setSettings,
    setSelectedFeatureId,
    setChatMessage,
    addEvents,
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
    setUi,
  } = useAppStore();
  const bootRef = useRef(false);

  const refreshProjects = async () => {
    try {
      const result = await listProjects();
      setRecentProjects(result.projects);
      setBackendState("connected");
    } catch (err) {
      setBackendState("offline");
      setError(`后端连接失败：${String(err)}。请确认 FastAPI 已在 8001 端口启动。`);
    }
  };

  const enterWorkspace = () => {
    if (startupMode === "first") {
      markStartupSeen();
    }
    setShowStartup(false);
  };

  const handleNewProject = async () => {
    setBusy(true);
    setError("");
    try {
      const { project: next } = await createProject("未命名 MechCAD 项目");
      setProject(next);
      setSettings(next.settings);
      setSettingsDirty(false);
      setSelectedFeatureId("");
      clearEvents();
      enterWorkspace();
      setBackendState("connected");
      await refreshProjects();
    } catch (err) {
      setBackendState("offline");
      setError(`创建项目失败：${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const handleOpenProject = async (projectId: string) => {
    setBusy(true);
    setError("");
    try {
      const next = await fetchProject(projectId);
      setProject(next);
      setSettings(next.settings);
      setSettingsDirty(false);
      setSelectedFeatureId("");
      enterWorkspace();
      setBackendState("connected");
      await refreshProjects();
    } catch (err) {
      setError(`打开项目失败：${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteProject = async (projectId: string) => {
    try {
      await deleteProject(projectId);
      if (project?.project_id === projectId) {
        setProject(null);
        setShowStartup(true);
      }
      await refreshProjects();
    } catch (err) {
      setError(`删除项目失败：${String(err)}`);
    }
  };

  const handleStartupModeChange = (mode: typeof startupMode) => {
    writeStartupMode(mode);
    setStartupMode(mode);
    if (mode === "off" && !project) {
      void handleNewProject();
    }
  };

  const startResize = (side: "left" | "right", event: ReactMouseEvent) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = side === "left" ? ui.leftWidth : ui.rightWidth;
    const onMove = (move: MouseEvent) => {
      const delta = move.clientX - startX;
      if (side === "left") {
        setUi({ leftWidth: Math.min(640, Math.max(320, startWidth + delta)) });
      } else {
        setUi({ rightWidth: Math.min(560, Math.max(300, startWidth - delta)) });
      }
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
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

    const wsRoot = (import.meta as any).env?.VITE_WS_ROOT || API_ROOT.replace(/^http/, "ws");
    const socket = new WebSocket(`${wsRoot}/ws/projects/${project.project_id}`);
    let socketConnected = false;

    socket.onopen = () => {
      socketConnected = true;
      addEvents(["实时事件连接已建立"]);
    };
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data);
      const payloadLogs = Array.isArray(event.payload?.logs) ? event.payload.logs : [];
      const detail = payloadLogs.map((item: unknown) => String(item));
      addEvents([`${event.stage}: ${event.message}`, ...detail]);
    };
    socket.onerror = () => {
      if (!socketConnected) {
        addEvents(["实时事件暂不可用，REST API 仍可继续操作"]);
      }
    };
    socket.onclose = () => {
      if (socketConnected) {
        addEvents(["实时事件连接已关闭，页面仍可继续操作"]);
      }
    };

    return () => socket.close();
  }, [project?.project_id, addEvents]);

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
      setUi({ rightTab: "assistant" });
    }
  }, [questions, setUi]);

  const runId = project?.current.artifacts.run_id;
  const hasModel = Boolean(project?.current.artifacts.stl || project?.current.artifacts.obj);
  const canUndo = Boolean(project?.history?.length);
  const canRedo = Boolean(project?.redo_stack?.length);
  const hasRequiredQuestions = questions.some((question) => question.required !== false && !question.answer);

  const status = !project
    ? "empty"
    : error && !busy
      ? "failed"
      : busy
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
      const next = await updateProjectSettings(project.project_id, nextSettings);
      replaceProject(next, nextSettings);
      setSettingsDirty(false);
      setSettingsNotice("配置已保存到当前项目。");
    } catch (err) {
      setSettingsDirty(true);
      setSettingsNotice(`配置保存失败：${String(err)}`);
    } finally {
      setSettingsSaving(false);
    }
  };

  const onGenerate = async () => {
    if (!project) {
      return;
    }
    setBusy(true);
    setError("");
    addEvents(["已提交生成任务"]);
    try {
      const imageDataUrl = imageFile ? await fileToDataUrl(imageFile) : null;
      const next = await generateProject(project.project_id, {
        description,
        operation_mode: settings.operation_mode,
        smart_fill_policy: settings.smart_fill_policy,
        model_config: settings,
        image_data_url: imageDataUrl,
        image_name: imageFile?.name,
      });
      replaceProject(next);
      setSettingsDirty(false);
      await refreshProjects();
    } catch (err) {
      setError(`生成失败：${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const onChat = async () => {
    if (!project || !chatMessage.trim()) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const next = await chatProject(project.project_id, chatMessage.trim());
      replaceProject(next);
      setChatMessage("");
      await refreshProjects();
    } catch (err) {
      setError(`修改失败：${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const onSaveFeature = async (payload: any) => {
    if (!project || !selectedFeature) {
      return;
    }
    setBusy(true);
    setError("");
    try {
      const next = await patchFeature(project.project_id, selectedFeature.id, payload);
      replaceProject(next);
      await refreshProjects();
    } catch (err) {
      setError(`保存特征失败：${String(err)}`);
    } finally {
      setBusy(false);
    }
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
      });
      replaceProject(next);
      setSettingsDirty(false);
      await refreshProjects();
    } catch (err) {
      setError(`确认问题失败：${String(err)}`);
    } finally {
      setBusy(false);
    }
  };

  const onUndo = () => {
    if (project && canUndo) {
      undo(project.project_id).then(replaceProject).catch((err) => setError(`撤销失败：${String(err)}`));
    }
  };

  const onRedo = () => {
    if (project && canRedo) {
      redo(project.project_id).then(replaceProject).catch((err) => setError(`重做失败：${String(err)}`));
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
        if (project) void onGenerate();
      } else if (mod && key === ",") {
        event.preventDefault();
        setUi({ settingsOpen: true });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // Re-register whenever capabilities change so shortcuts stay accurate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.project_id, canUndo, canRedo, settingsDirty, settings]);

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
        onModeChange={handleStartupModeChange}
      />
    );
  }

  return (
    <main className="app-shell ide-shell">
      <TopCommandBar
        backendState={backendState}
        busy={busy}
        canRedo={canRedo}
        canUndo={canUndo}
        engineLabel={plan ? "Build123d Worker（受控执行）" : "等待 FeaturePlan"}
        modeLabel={getModeLabel(settings)}
        projectName={project.name || "MechCAD IDE"}
        statusLabel={statusLabels[status]}
        onGenerate={() => void onGenerate()}
        onRedo={onRedo}
        onUndo={onUndo}
        onNewProject={() => void handleNewProject()}
        onBackToStart={() => setShowStartup(true)}
        onOpenSettings={() => setUi({ settingsOpen: true })}
      />

      {error && <div className="status-banner error">{error}</div>}

      <section
        className="workspace-grid"
        style={
          {
            "--left-width": `${ui.leftCollapsed ? 52 : ui.leftWidth}px`,
            "--right-width": `${ui.rightCollapsed ? 52 : ui.rightWidth}px`,
          } as any
        }
      >
        <LeftManager
          busy={busy}
          description={description}
          features={features}
          imageFile={imageFile}
          modeLabel={getModeLabel(settings)}
          partFamily={plan?.part_family}
          projectName={project.name || "未命名项目"}
          selectedFeature={selectedFeature}
          selectedFeatureId={selectedFeatureId}
          statusLabel={statusLabels[status]}
          unresolvedCount={unresolved.length}
          onDescriptionChange={setDescription}
          onImageChange={setImageFile}
          onSelectFeature={setSelectedFeatureId}
          onSaveFeature={(payload) => void onSaveFeature(payload)}
          onOpenSettings={() => setUi({ settingsOpen: true })}
        />
        <div className="panel-resizer lresize" title="拖动调整左侧面板宽度" onMouseDown={(event) => startResize("left", event)} />

        <section className="workspace-center" aria-label="3D 工作区">
          <div className="center-statusbar">
            <div>
              <strong>{hasModel ? "模型预览" : hasRequiredQuestions ? "等待参数确认" : "空视口"}</strong>
              <span>{plan ? `零件族：${plan.part_family}` : "上传草图或输入已知尺寸后开始"}</span>
            </div>
            <div className="center-statusbar-actions">
              <span className={hasModel ? "status-dot ok" : hasRequiredQuestions ? "status-dot warn" : "status-dot idle"} />
              <span>{statusLabels[status]}</span>
            </div>
          </div>
          <Viewport
            objUrl={artifactUrl(runId, "obj")}
            stlUrl={artifactUrl(runId, "stl")}
            breadcrumb={`草稿 / ${plan?.part_family || "FeaturePlan"} / ${selectedFeature?.id || "未选择"}`}
            statusLabel={statusLabels[status]}
          />
          <div className="artifact-row">
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "step")}>STEP</a>
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "stl")}>STL</a>
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "obj")}>OBJ</a>
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "execution_report")}>执行报告</a>
          </div>
        </section>
        <div className="panel-resizer rresize" title="拖动调整右侧面板宽度" onMouseDown={(event) => startResize("right", event)} />

        <TaskPane
          busy={busy}
          chatMessage={chatMessage}
          events={events}
          featurePlan={project.current.feature_plan}
          questions={questions}
          reportMarkdown={project.current.report_markdown}
          review={review}
          unresolved={unresolved}
          runId={runId}
          engineLabel={plan ? "Build123d Worker（受控执行）" : "等待 FeaturePlan"}
          onChatMessageChange={setChatMessage}
          onClarificationContinue={(answers) => void onClarificationContinue(answers)}
          onSendChat={() => void onChat()}
        />
      </section>

      <footer className="bottom-statusbar">
        <span>坐标：0.00, 0.00, 0.00</span>
        <span>{statusLabels[status]}</span>
        <span>Ctrl+G 生成 · Ctrl+Z 撤销 · Ctrl+, 设置</span>
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
    if (publicSettings[key] === SECRET_MASK && draftSettings[key] && draftSettings[key] !== SECRET_MASK) {
      merged[key] = draftSettings[key];
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
