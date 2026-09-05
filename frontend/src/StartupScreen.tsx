import { useState } from "react";
import type { ProjectState } from "./api";
import type { StartupMode } from "./store";
import { useAppStore } from "./store";
import { useT } from "./i18n";

type Props = {
  projects: ProjectState[];
  backendState: "connected" | "offline";
  startupMode: StartupMode;
  error: string;
  onCreate: () => void;
  onOpen: (projectId: string) => void;
  onDelete: (projectId: string) => void;
  onRename: (projectId: string, name: string) => void;
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
  onRename,
  onModeChange,
}: Props) {
  const t = useT();
  const language = useAppStore((state) => state.language);
  const setLanguage = useAppStore((state) => state.setLanguage);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const startRename = (project: ProjectState) => {
    setRenamingId(project.project_id);
    setRenameValue(project.name);
  };

  const submitRename = () => {
    if (renamingId && renameValue.trim()) {
      onRename(renamingId, renameValue.trim());
    }
    setRenamingId(null);
    setRenameValue("");
  };

  const cancelRename = () => {
    setRenamingId(null);
    setRenameValue("");
  };

  const handleDelete = (projectId: string, name: string) => {
    if (window.confirm(t("startup.delete.confirm", { name }))) {
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
          <strong>VAREN CAD</strong>
          <span>{t("startup.brand")}</span>
        </div>
        <div className="startup-header-status">
          <span className={backendState === "connected" ? "workspace-chip ok" : "workspace-chip danger"}>
            {backendState === "connected" ? t("startup.backend.connected") : t("startup.backend.offline")}
          </span>
        </div>
      </header>

      <main className="startup-main">
        <section className="startup-hero">
          <img className="startup-hero-logo" src="/varen-cad-logo.png" alt="Varen CAD logo" />
          <p className="eyebrow">FROM SKETCH TO STEP</p>
          <h1>{t("startup.title")}</h1>
          <p>{t("startup.subtitle")}</p>
          <button type="button" className="primary startup-create" onClick={onCreate} disabled={backendState === "offline"}>
            {t("startup.new")}
          </button>
          {error && <div className="status-banner error">{error}</div>}
        </section>

        <section className="recent-projects">
          <div className="recent-header">
            <h2>{t("startup.recent")}</h2>
            <span>{t("startup.count", { count: projects.length })}</span>
          </div>
          {projects.length === 0 ? (
            <div className="recent-empty">
              <strong>{t("startup.empty.title")}</strong>
              <span>{t("startup.empty.hint")}</span>
            </div>
          ) : (
            <div className="recent-list">
              {projects.map((project) => (
                <article className="recent-item" key={project.project_id}>
                  {renamingId === project.project_id ? (
                    <div className="rename-row">
                      <input
                        className="rename-input"
                        value={renameValue}
                        onChange={(event) => setRenameValue(event.target.value)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") submitRename();
                          if (event.key === "Escape") cancelRename();
                        }}
                        placeholder={t("startup.rename.prompt")}
                        autoFocus
                      />
                      <div className="rename-actions">
                        <button type="button" className="primary" onClick={submitRename} disabled={!renameValue.trim()}>
                          {t("startup.rename.save")}
                        </button>
                        <button type="button" onClick={cancelRename}>
                          {t("startup.rename.cancel")}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="recent-item-main">
                      <strong>{project.name}</strong>
                      <span>
                        {project.current.feature_plan?.part_family || t("startup.no_model")} ·{" "}
                        {new Date(project.updated_at).toLocaleString()}
                      </span>
                    </div>
                  )}
                  <div className="recent-item-actions">
                    <button type="button" className="primary" onClick={() => onOpen(project.project_id)}>
                      {t("startup.open")}
                    </button>
                    <button type="button" title={t("startup.rename.title")} onClick={() => startRename(project)}>
                      {t("startup.rename")}
                    </button>
                    <button
                      type="button"
                      className="danger-button"
                      title={t("startup.remove.title")}
                      onClick={() => handleDelete(project.project_id, project.name)}
                    >
                      {t("startup.delete")}
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
          <span>{t("startup.behavior")}</span>
          <select
            value={startupMode}
            onChange={(event) => onModeChange(event.target.value as StartupMode)}
          >
            <option value="always">{t("startup.mode.always")}</option>
            <option value="first">{t("startup.mode.first")}</option>
            <option value="off">{t("startup.mode.off")}</option>
          </select>
        </label>
        <label className="startup-mode-field">
          <span>{t("settings.language")}</span>
          <select value={language} onChange={(event) => setLanguage(event.target.value as "zh" | "en")}>
            <option value="zh">{t("settings.language.zh")}</option>
            <option value="en">{t("settings.language.en")}</option>
          </select>
        </label>
        <span className="startup-footnote">{t("startup.footnote")}</span>
      </footer>
    </div>
  );
}