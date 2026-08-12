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
  renameProject,
  resolveWsRoot,
  updateProjectSettings,
  undo,
  type ModelConfig,
  type ProcessStep,
  type ProjectState,
} from "./api";
import LeftManager from "./layout/LeftManager";
import TaskPane from "./layout/TaskPane";
import TopCommandBar from "./layout/TopCommandBar";
import SettingsDialog from "./SettingsDialog";
import StartupScreen from "./StartupScreen";
import Viewport from "./Viewport";
import { useT } from "./i18n";
import {
  DEFAULT_SETTINGS,
  clampDrawerWidth,
  markStartupSeen,
  readStartupMode,
  shouldShowStartup,
  useAppStore,
  writeStartupMode,
  type ManagerTab,
  type TaskTab,
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
    setDescription,
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
  } = useAppStore();
  const bootRef = useRef(false);
  const language = useAppStore((state) => state.language);

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
    } catch (err) {
      setError(t("app.open.failed", { err: String(err) }));
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

  const toggleLeftDrawer = (tab: ManagerTab) => {
    setUi({
      leftTab: tab,
      leftDrawerOpen: ui.leftDrawerOpen && ui.leftTab === tab ? false : true,
    });
  };

  const toggleRightDrawer = (tab: TaskTab) => {
    setUi({
      rightTab: tab,
      rightDrawerOpen: ui.rightDrawerOpen && ui.rightTab === tab ? false : true,
    });
  };

  const startResize = (side: "left" | "right", event: ReactMouseEvent) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = side === "left" ? ui.leftWidth : ui.rightWidth;
    const onMove = (move: MouseEvent) => {
      const delta = move.clientX - startX;
      if (side === "left") {
        setUi({ leftWidth: clampDrawerWidth(startWidth + delta) });
      } else {
        setUi({ rightWidth: clampDrawerWidth(startWidth - delta) });
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

  const runId = project?.current.artifacts.run_id;
  const hasModel = Boolean(project?.current.artifacts.stl || project?.current.artifacts.obj);
  const canUndo = Boolean(project?.history?.length);
  const canRedo = Boolean(project?.redo_stack?.length);
  const hasRequiredQuestions = questions.some((question) => question.required !== false && !question.answer);
  const modeLabel = settings.operation_mode === "strict" ? t("mode.strict") : t("mode.smart");

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

  const onGenerate = async () => {
    if (!project) {
      return;
    }
    setBusy(true);
    setError("");
    addEvents([t("app.generate.submitted")]);
    try {
      const imageDataUrl = imageFile ? await fileToDataUrl(imageFile) : null;
      const next = await generateProject(project.project_id, {
        description,
        operation_mode: settings.operation_mode,
        smart_fill_policy: settings.smart_fill_policy,
        model_config: settings,
        image_data_url: imageDataUrl,
        image_name: imageFile?.name,
        language,
      });
      replaceProject(next);
      setSettingsDirty(false);
      await refreshProjects();
    } catch (err) {
      setError(t("app.generate.failed", { err: String(err) }));
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
      const next = await chatProject(project.project_id, chatMessage.trim(), language);
      replaceProject(next);
      setChatMessage("");
      await refreshProjects();
    } catch (err) {
      setError(t("app.chat.failed", { err: String(err) }));
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
      const next = await patchFeature(project.project_id, selectedFeature.id, payload, language);
      replaceProject(next);
      await refreshProjects();
    } catch (err) {
      setError(t("app.feature.failed", { err: String(err) }));
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
        if (project) void onGenerate();
      } else if (mod && key === ",") {
        event.preventDefault();
        setUi({ settingsOpen: true });
      } else if (event.key === "Escape") {
        setUi({ leftDrawerOpen: false, rightDrawerOpen: false });
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
        modeLabel={modeLabel}
        projectName={project.name || "MechCAD IDE"}
        statusLabel={t(statusLabelKeys[status])}
        onGenerate={() => void onGenerate()}
        onRedo={onRedo}
        onUndo={onUndo}
        onNewProject={() => void handleNewProject()}
        onRenameProject={renameCurrentProject}
        onBackToStart={() => setShowStartup(true)}
        onOpenSettings={() => setUi({ settingsOpen: true })}
      />

      {error && <div className="status-banner error">{error}</div>}

      <section
        className="workspace-grid"
        onMouseDown={(event) => {
          const target = event.target as HTMLElement;
          if (target.closest(".edge-drawer") || target.closest(".edge-rail")) {
            return;
          }
          setUi({ leftDrawerOpen: false, rightDrawerOpen: false });
        }}
      >
        <section className="workspace-center" aria-label={t("app.viewport.aria")}>
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
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "obj")}>OBJ</a>
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "execution_report")}>{t("app.artifact.report")}</a>
          </div>
        </section>

        <aside className="edge-rail edge-rail-left" aria-label={t("rail.left.label")}>
          <button
            type="button"
            className={ui.leftDrawerOpen && ui.leftTab === "feature" ? "edge-rail-button active" : "edge-rail-button"}
            title={t("manager.feature_tree")}
            onClick={() => toggleLeftDrawer("feature")}
          >
            <span className="rail-label">{t("rail.features")}</span>
          </button>
          <button
            type="button"
            className={ui.leftDrawerOpen && ui.leftTab === "property" ? "edge-rail-button active" : "edge-rail-button"}
            title={t("manager.property")}
            onClick={() => toggleLeftDrawer("property")}
          >
            <span className="rail-label">{t("rail.property")}</span>
          </button>
          <button
            type="button"
            className={ui.leftDrawerOpen && ui.leftTab === "configuration" ? "edge-rail-button active" : "edge-rail-button"}
            title={t("manager.configuration")}
            onClick={() => toggleLeftDrawer("configuration")}
          >
            <span className="rail-label">{t("rail.config")}</span>
          </button>
        </aside>

        <aside className="edge-rail edge-rail-right" aria-label={t("rail.right.label")}>
          <button
            type="button"
            className={ui.rightDrawerOpen && ui.rightTab === "assistant" ? "edge-rail-button active" : "edge-rail-button"}
            title={t("task.assistant")}
            onClick={() => toggleRightDrawer("assistant")}
          >
            <span className="rail-label">{t("rail.assistant")}</span>
          </button>
          <button
            type="button"
            className={ui.rightDrawerOpen && ui.rightTab === "review" ? "edge-rail-button active" : "edge-rail-button"}
            title={t("task.review")}
            onClick={() => toggleRightDrawer("review")}
          >
            <span className="rail-label">{t("rail.review")}</span>
          </button>
          <button
            type="button"
            className={ui.rightDrawerOpen && ui.rightTab === "process" ? "edge-rail-button active" : "edge-rail-button"}
            title={t("task.process")}
            onClick={() => toggleRightDrawer("process")}
          >
            <span className="rail-label">{t("rail.process")}</span>
          </button>
          <button
            type="button"
            className={ui.rightDrawerOpen && ui.rightTab === "logs" ? "edge-rail-button active" : "edge-rail-button"}
            title={t("task.logs")}
            onClick={() => toggleRightDrawer("logs")}
          >
            <span className="rail-label">{t("rail.logs")}</span>
          </button>
          <button
            type="button"
            className={ui.rightDrawerOpen && ui.rightTab === "export" ? "edge-rail-button active" : "edge-rail-button"}
            title={t("task.export")}
            onClick={() => toggleRightDrawer("export")}
          >
            <span className="rail-label">{t("rail.export")}</span>
          </button>
        </aside>

        {ui.leftDrawerOpen && (
          <div className="edge-drawer left-drawer" style={{ width: `${ui.leftWidth}px` }}>
            <div className="drawer-resizer drawer-resizer-left" title={t("app.left.resize")} onMouseDown={(event) => startResize("left", event)} />
            <LeftManager
              busy={busy}
              description={description}
              features={features}
              imageFile={imageFile}
              modeLabel={modeLabel}
              partFamily={plan?.part_family}
              projectName={project.name || t("app.project.untitled")}
              selectedFeature={selectedFeature}
              selectedFeatureId={selectedFeatureId}
              statusLabel={t(statusLabelKeys[status])}
              unresolvedCount={unresolved.length}
              onDescriptionChange={setDescription}
              onImageChange={setImageFile}
              onSelectFeature={setSelectedFeatureId}
              onSaveFeature={(payload) => void onSaveFeature(payload)}
              onOpenSettings={() => setUi({ settingsOpen: true })}
              onClose={() => setUi({ leftDrawerOpen: false })}
            />
          </div>
        )}

        {ui.rightDrawerOpen && (
          <div className="edge-drawer right-drawer" style={{ width: `${ui.rightWidth}px` }}>
            <div className="drawer-resizer drawer-resizer-right" title={t("app.right.resize")} onMouseDown={(event) => startResize("right", event)} />
            <TaskPane
              busy={busy}
              chatMessage={chatMessage}
              events={events}
              processSteps={processSteps}
              featurePlan={project.current.feature_plan}
              questions={questions}
              reportMarkdown={project.current.report_markdown}
              review={review}
              unresolved={unresolved}
              runId={runId}
              engineLabel={engineLabel}
              onChatMessageChange={setChatMessage}
              onClarificationContinue={(answers) => void onClarificationContinue(answers)}
              onSendChat={() => void onChat()}
              onSelectProcessStep={(featureId) => {
                setSelectedFeatureId(featureId);
                setUi({ leftDrawerOpen: true, leftTab: "feature" });
              }}
              onClose={() => setUi({ rightDrawerOpen: false })}
            />
          </div>
        )}
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
