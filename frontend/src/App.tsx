import { useEffect, useMemo, useState } from "react";
import {
  API_ROOT,
  artifactUrl,
  chatProject,
  createProject,
  generateProject,
  patchFeature,
  redo,
  updateProjectSettings,
  undo,
  type ModelConfig,
  type ProjectState,
} from "./api";
import BottomTaskPanel, { type TaskTab } from "./layout/BottomTaskPanel";
import LeftManager from "./layout/LeftManager";
import RightPropertyManager from "./layout/RightPropertyManager";
import TopCommandBar from "./layout/TopCommandBar";
import Viewport from "./Viewport";

const defaultSettings: ModelConfig = {
  vision_provider: "custom",
  vision_model: "",
  vision_base_url: "",
  vision_api_key: "",
  vision_protocol: "openai",
  planner_provider: "custom",
  planner_model: "",
  planner_base_url: "",
  planner_api_key: "",
  planner_protocol: "openai",
  operation_mode: "strict",
  smart_fill_policy: "limited_fill",
};

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
  const [project, setProject] = useState<ProjectState | null>(null);
  const [description, setDescription] = useState(
    "一件带孔或带槽的机械零件，请按整体到细节规划。已知尺寸请直接写明，未知尺寸请留给系统提问。",
  );
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [settings, setSettings] = useState<ModelConfig>(defaultSettings);
  const [selectedFeatureId, setSelectedFeatureId] = useState("");
  const [chatMessage, setChatMessage] = useState("");
  const [events, setEvents] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [backendState, setBackendState] = useState<"connected" | "offline">("connected");
  const [activeTaskTab, setActiveTaskTab] = useState<TaskTab>("questions");
  const [settingsDirty, setSettingsDirty] = useState(false);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsNotice, setSettingsNotice] = useState("");

  useEffect(() => {
    createProject("MechCAD IDE")
      .then(({ project }) => {
        setProject(project);
        setSettings(project.settings);
        setSettingsDirty(false);
        setBackendState("connected");
      })
      .catch((err) => {
        setBackendState("offline");
        setError(`后端连接失败：${String(err)}。请确认 FastAPI 已在 8001 端口启动。`);
      });
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
      setEvents((items) => ["实时事件连接已建立", ...items]);
    };
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data);
      const payloadLogs = Array.isArray(event.payload?.logs) ? event.payload.logs : [];
      const detail = payloadLogs.map((item: unknown) => String(item));
      setEvents((items) => [`${event.stage}: ${event.message}`, ...detail, ...items].slice(0, 100));
    };
    socket.onerror = () => {
      if (!socketConnected) {
        setEvents((items) => ["实时事件暂不可用，REST API 仍可继续操作", ...items]);
      }
    };
    socket.onclose = () => {
      if (socketConnected) {
        setEvents((items) => ["实时事件连接已关闭，页面仍可继续操作", ...items]);
      }
    };

    return () => socket.close();
  }, [project?.project_id]);

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
  }, [features.length, selectedFeature?.id, project?.current.id]);

  useEffect(() => {
    if (questions.some((question) => question.required !== false && !question.answer)) {
      setActiveTaskTab("questions");
    }
  }, [questions]);

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
      if (isMissingSettingsEndpoint(err)) {
        setSettingsDirty(true);
        setSettingsNotice("当前后端尚未支持即时保存；草稿会在下一次生成时自动应用。");
      } else {
        setError(`配置保存失败：${String(err)}`);
      }
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
    setEvents((items) => ["已提交生成任务", ...items]);
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

  return (
    <main className="app-shell">
      <TopCommandBar
        backendState={backendState}
        busy={busy}
        canRedo={canRedo}
        canUndo={canUndo}
        engineLabel={plan ? "Build123d Worker（受控执行）" : "等待 FeaturePlan"}
        modeLabel={getModeLabel(settings)}
        projectId={project?.project_id}
        projectName={project?.name || "MechCAD IDE"}
        statusLabel={statusLabels[status]}
        onGenerate={onGenerate}
        onRedo={onRedo}
        onUndo={onUndo}
      />

      {error && <div className="status-banner error">{error}</div>}

      <section className="workspace-grid">
        <LeftManager
          busy={busy}
          description={description}
          features={features}
          imageFile={imageFile}
          modeLabel={getModeLabel(settings)}
          partFamily={plan?.part_family}
          projectId={project?.project_id}
          projectName={project?.name || "未创建项目"}
          selectedFeatureId={selectedFeatureId}
        settings={settings}
        settingsDirty={settingsDirty}
        settingsNotice={settingsNotice}
        settingsSaving={settingsSaving}
        statusLabel={statusLabels[status]}
          unresolvedCount={unresolved.length}
          onDescriptionChange={setDescription}
          onImageChange={setImageFile}
          onSelectFeature={setSelectedFeatureId}
          onApplySettings={onApplySettings}
          onSettingsChange={onSettingsChange}
        />

        <section className="workspace-center" aria-label="3D 视口">
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
          <Viewport objUrl={artifactUrl(runId, "obj")} stlUrl={artifactUrl(runId, "stl")} />
          <div className="artifact-row">
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "step")}>
              STEP
            </a>
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "stl")}>
              STL
            </a>
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "obj")}>
              OBJ
            </a>
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "execution_report")}>
              执行报告
            </a>
          </div>
        </section>

        <RightPropertyManager
          busy={busy}
          review={review}
          selectedFeature={selectedFeature}
          unresolved={unresolved}
          onSaveFeature={onSaveFeature}
        />

        <BottomTaskPanel
          activeTab={activeTaskTab}
          busy={busy}
          chatMessage={chatMessage}
          events={events}
          featurePlan={project?.current.feature_plan}
          questions={questions}
          reportMarkdown={project?.current.report_markdown}
          review={review}
          unresolved={unresolved}
          onChatMessageChange={setChatMessage}
          onClarificationContinue={onClarificationContinue}
          onSendChat={onChat}
          onTabChange={setActiveTaskTab}
        />
      </section>
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

function isMissingSettingsEndpoint(error: unknown) {
  return /not found|http 404/i.test(String(error));
}

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
