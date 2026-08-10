import type { ProjectState } from "./api";
import type { StartupMode } from "./store";

type Props = {
  projects: ProjectState[];
  backendState: "connected" | "offline";
  startupMode: StartupMode;
  error: string;
  onCreate: () => void;
  onOpen: (projectId: string) => void;
  onDelete: (projectId: string) => void;
  onModeChange: (mode: StartupMode) => void;
};

export default function StartupScreen({
  projects,
  backendState,
  startupMode,
  error,
  onCreate,
  onOpen,
  onDelete,
  onModeChange,
}: Props) {
  const handleDelete = (projectId: string, name: string) => {
    if (window.confirm(`删除项目“${name}”？此操作不会删除已导出的 STEP/STL 文件。`)) {
      onDelete(projectId);
    }
  };

  return (
    <div className="startup-screen">
      <div className="startup-cloud startup-cloud-far" aria-hidden="true" />
      <div className="startup-cloud startup-cloud-mid" aria-hidden="true" />
      <div className="startup-cloud startup-cloud-near" aria-hidden="true" />

      <header className="startup-header">
        <div className="startup-brand">
          <strong>MECHCAD</strong>
          <span>栖云 · AI CAD IDE</span>
        </div>
        <div className="startup-header-status">
          <span className={backendState === "connected" ? "workspace-chip ok" : "workspace-chip danger"}>
            {backendState === "connected" ? "后端已连接" : "后端离线"}
          </span>
        </div>
      </header>

      <main className="startup-main">
        <section className="startup-hero">
          <p className="eyebrow">FROM SKETCH TO STEP</p>
          <h1>把草图变成可制造的三维模型</h1>
          <p>上传手绘零件图，AI 负责读图、规划特征树，并在受控 CAD Worker 中生成 STEP / STL。</p>
          <button type="button" className="primary startup-create" onClick={onCreate} disabled={backendState === "offline"}>
            新建项目
          </button>
          {error && <div className="status-banner error">{error}</div>}
        </section>

        <section className="recent-projects">
          <div className="recent-header">
            <h2>最近项目</h2>
            <span>{projects.length} 项</span>
          </div>
          {projects.length === 0 ? (
            <div className="recent-empty">
              <strong>还没有项目</strong>
              <span>点击“新建项目”开始第一次 AI 建模。</span>
            </div>
          ) : (
            <div className="recent-list">
              {projects.map((project) => (
                <article className="recent-item" key={project.project_id}>
                  <div className="recent-item-main">
                    <strong>{project.name}</strong>
                    <span>
                      {project.current.feature_plan?.part_family || "未生成模型"} ·{" "}
                      {new Date(project.updated_at).toLocaleString()}
                    </span>
                  </div>
                  <div className="recent-item-actions">
                    <button type="button" className="primary" onClick={() => onOpen(project.project_id)}>
                      打开
                    </button>
                    <button
                      type="button"
                      className="danger-button"
                      title="从最近项目列表移除"
                      onClick={() => handleDelete(project.project_id, project.name)}
                    >
                      删除
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </main>

      <footer className="startup-footer">
        <label className="startup-mode-field">
          <span>启动页行为</span>
          <select
            value={startupMode}
            onChange={(event) => onModeChange(event.target.value as StartupMode)}
          >
            <option value="always">每次启动显示</option>
            <option value="first">仅首次显示</option>
            <option value="off">关闭，直接进入工作台</option>
          </select>
        </label>
        <span className="startup-footnote">浅色水墨主题 · API 配置在工作台的设置中心</span>
      </footer>
    </div>
  );
}
