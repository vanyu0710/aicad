import { useEffect, useMemo, useState } from "react";
import {
  artifactUrl,
  chatProject,
  createProject,
  generateProject,
  patchFeature,
  redo,
  undo,
  type ModelConfig,
  type ProjectState,
} from "./api";
import FeatureForm from "./FeatureForm";
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
  smart_fill_policy: "suggest_only",
};

export default function App() {
  const [project, setProject] = useState<ProjectState | null>(null);
  const [description, setDescription] = useState("一件带孔或带槽的机械零件，请先按整体到细节规划。");
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [settings, setSettings] = useState<ModelConfig>(defaultSettings);
  const [selectedFeatureId, setSelectedFeatureId] = useState("");
  const [chatMessage, setChatMessage] = useState("");
  const [events, setEvents] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    createProject("MechCAD IDE").then(({ project }) => {
      setProject(project);
      setSettings(project.settings);
    });
  }, []);

  useEffect(() => {
    if (!project?.project_id) {
      return;
    }
    const wsProtocol = window.location.protocol === "https:" ? "wss" : "ws";
    const socket = new WebSocket(`${wsProtocol}://${window.location.host}/ws/projects/${project.project_id}`);
    socket.onmessage = (message) => {
      const event = JSON.parse(message.data);
      setEvents((items) => [`${event.stage}: ${event.message}`, ...items].slice(0, 80));
    };
    socket.onerror = () => setEvents((items) => ["WebSocket 连接异常，REST API 仍可用", ...items]);
    return () => socket.close();
  }, [project?.project_id]);

  const features = useMemo(() => {
    const plan = project?.current.feature_plan;
    if (!plan) {
      return [];
    }
    return [plan.base_feature, ...(plan.features || [])].filter(Boolean);
  }, [project]);

  const selectedFeature = useMemo(
    () => features.find((feature: any) => feature.id === selectedFeatureId) || features[0],
    [features, selectedFeatureId],
  );

  useEffect(() => {
    if (selectedFeature) {
      setSelectedFeatureId(selectedFeature.id);
    }
  }, [selectedFeature?.id, project?.current.id]);

  const runId = project?.current.artifacts.run_id;
  const stlUrl = artifactUrl(runId, "stl");
  const objUrl = artifactUrl(runId, "obj");

  const onGenerate = async () => {
    if (!project) {
      return;
    }
    setBusy(true);
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
      setProject(next);
    } finally {
      setBusy(false);
    }
  };

  const onChat = async () => {
    if (!project || !chatMessage.trim()) {
      return;
    }
    setBusy(true);
    try {
      const next = await chatProject(project.project_id, chatMessage.trim());
      setProject(next);
      setChatMessage("");
    } finally {
      setBusy(false);
    }
  };

  const onSaveFeature = async (payload: any) => {
    if (!project || !selectedFeature) {
      return;
    }
    setBusy(true);
    try {
      const next = await patchFeature(project.project_id, selectedFeature.id, payload);
      setProject(next);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">MECHCAD IDE</p>
          <h1>栖云</h1>
        </div>
        <div className="top-actions">
          <button onClick={() => project && undo(project.project_id).then(setProject)} disabled={!project || busy}>
            撤销
          </button>
          <button onClick={() => project && redo(project.project_id).then(setProject)} disabled={!project || busy}>
            重做
          </button>
          <button className="primary" onClick={onGenerate} disabled={!project || busy}>
            {busy ? "处理中" : "生成模型"}
          </button>
        </div>
      </header>

      <section className="ide-grid">
        <aside className="panel left-pane">
          <h2>项目输入</h2>
          <label className="field">
            <span>草图图片</span>
            <input type="file" accept="image/png,image/jpeg" onChange={(event) => setImageFile(event.target.files?.[0] || null)} />
          </label>
          <label className="field">
            <span>零件功能描述</span>
            <textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={4} />
          </label>

          <h2>模型配置</h2>
          <label className="field">
            <span>工作模式</span>
            <select value={settings.operation_mode} onChange={(event) => setSettings({ ...settings, operation_mode: event.target.value as any })}>
              <option value="strict">严格模式：不猜尺寸</option>
              <option value="smart">智能模式：先建议再确认</option>
            </select>
          </label>
          <label className="field">
            <span>视觉模型</span>
            <input value={settings.vision_model} onChange={(event) => setSettings({ ...settings, vision_model: event.target.value })} placeholder="例如 qwen-vl-plus / gpt-4o" />
          </label>
          <label className="field">
            <span>建模规划模型</span>
            <input value={settings.planner_model} onChange={(event) => setSettings({ ...settings, planner_model: event.target.value })} placeholder="例如 MiniMax-M3 / Claude / GPT" />
          </label>

          <h2>特征树</h2>
          <div className="feature-tree">
            {features.map((feature: any) => (
              <button
                key={feature.id}
                className={feature.id === selectedFeatureId ? "selected" : ""}
                onClick={() => setSelectedFeatureId(feature.id)}
              >
                <strong>{feature.id}</strong>
                <span>{feature.type}</span>
              </button>
            ))}
            {!features.length && <p className="muted">生成后这里会显示 SW 风格特征树。</p>}
          </div>
        </aside>

        <section className="center-pane">
          <Viewport objUrl={objUrl} stlUrl={stlUrl} />
          <div className="artifact-row">
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "step")}>STEP</a>
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "stl")}>STL</a>
            <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "execution_report")}>执行报告</a>
          </div>
        </section>

        <aside className="panel right-pane">
          <h2>属性面板</h2>
          {selectedFeature ? (
            <FeatureForm
              key={selectedFeature.id}
              feature={selectedFeature}
              busy={busy}
              onSave={onSaveFeature}
            />
          ) : (
            <p className="muted">请选择一个特征。</p>
          )}
        </aside>

        <section className="bottom-pane">
          <div className="panel chat-panel">
            <h2>AI 对话修改</h2>
            <div className="chat-row">
              <input
                value={chatMessage}
                onChange={(event) => setChatMessage(event.target.value)}
                placeholder="例如：把中心孔改成 12mm；删除顶部槽；新增 4 个 M6 孔"
              />
              <button onClick={onChat} disabled={busy}>发送</button>
            </div>
            <div className="question-list">
              {(project?.current.questions || []).map((question) => (
                <article key={question.id}>
                  <p>{question.text}</p>
                  <span>{question.options.join(" / ")}</span>
                </article>
              ))}
            </div>
          </div>

          <div className="panel log-panel">
            <h2>日志与 FeaturePlan</h2>
            <pre>{events.join("\n") || "等待后端事件..."}</pre>
            <textarea
              className="code-box"
              readOnly
              value={project ? JSON.stringify(project.current.feature_plan, null, 2) : ""}
              rows={10}
            />
          </div>
        </section>
      </section>
    </main>
  );
}

function fileToDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}
