import { useAppStore } from "../store";
import { useT } from "../i18n";

type Props = {
  backendState: "connected" | "offline";
  busy: boolean;
  canRedo: boolean;
  canUndo: boolean;
  engineLabel: string;
  projectName: string;
  statusLabel: string;
  onRedo: () => void;
  onUndo: () => void;
  onNewProject: () => void;
  onRenameProject: () => void;
  onBackToStart: () => void;
  onOpenSettings: () => void;
};

export default function TopCommandBar({
  backendState,
  busy,
  canRedo,
  canUndo,
  engineLabel,
  projectName,
  statusLabel,
  onRedo,
  onUndo,
  onNewProject,
  onRenameProject,
  onBackToStart,
  onOpenSettings,
}: Props) {
  const t = useT();
  const setUi = useAppStore((state) => state.setUi);
  const focusMode = useAppStore((state) => state.ui.focusMode);

  return (
    <header className={`top-shell${focusMode ? " focus-mode" : ""}`}>
      <div className="title-bar compact-top">
        <button
          type="button"
          className="brand-block brand-button"
          title={t("top.home.title")}
          onClick={onBackToStart}
        >
          <p className="eyebrow">VAREN CAD IDE</p>
          <h1>{t("top.brand")}</h1>
        </button>
        <div className="title-project">
          <strong>{projectName}</strong>
          <span>{engineLabel}</span>
        </div>
        <div className="title-status">
          <span className={backendState === "connected" ? "workspace-chip ok" : "workspace-chip danger"}>
            {backendState === "connected" ? t("startup.backend.connected") : t("startup.backend.offline")}
          </span>
          <span className="workspace-chip">{statusLabel}</span>
        </div>
        <div className="quick-actions">
          <button type="button" title={t("menu.new_project")} onClick={onNewProject}>
            {t("top.new")}
          </button>
          <button type="button" title={t("menu.rename_project")} onClick={onRenameProject}>
            {t("top.rename")}
          </button>
          <button type="button" title={t("top.settings.title")} onClick={onOpenSettings}>
            {t("top.settings")}
          </button>
          <button type="button" title={t("top.undo.title")} onClick={onUndo} disabled={busy || !canUndo}>
            {t("menu.undo")}
          </button>
          <button type="button" title={t("top.redo.title")} onClick={onRedo} disabled={busy || !canRedo}>
            {t("menu.redo")}
          </button>
          <button
            type="button"
            title={t("top.focus.title")}
            className={focusMode ? "focus-toggle active" : "focus-toggle"}
            onClick={() => setUi({ focusMode: !focusMode })}
          >
            {t("top.focus")}
          </button>
        </div>
      </div>
    </header>
  );
}
