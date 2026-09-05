import { useState } from "react";
import { useAppStore } from "../store";
import { useT } from "../i18n";

type Props = {
  backendState: "connected" | "offline";
  busy: boolean;
  agentRunning: boolean;
  canRedo: boolean;
  canUndo: boolean;
  engineLabel: string;
  modeLabel: string;
  projectName: string;
  statusLabel: string;
  onGenerate: () => void;
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
  agentRunning,
  canRedo,
  canUndo,
  engineLabel,
  modeLabel,
  projectName,
  statusLabel,
  onGenerate,
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
  const leftDrawerOpen = useAppStore((state) => state.ui.leftDrawerOpen);
  const rightDrawerOpen = useAppStore((state) => state.ui.rightDrawerOpen);
  const leftTab = useAppStore((state) => state.ui.leftTab);
  const rightTab = useAppStore((state) => state.ui.rightTab);
  const [openMenu, setOpenMenu] = useState<string | null>(null);

  const run = (action: () => void) => {
    setOpenMenu(null);
    action();
  };

  const openLeft = (tab: typeof leftTab) => setUi({ leftTab: tab, leftDrawerOpen: true });
  const openRight = (tab: typeof rightTab) => setUi({ rightTab: tab, rightDrawerOpen: true });

  const menus: Record<string, { labelKey: string; action: () => void; disabled?: boolean }[]> = {
    file: [
      { labelKey: "menu.new_project", action: onNewProject },
      { labelKey: "menu.rename_project", action: onRenameProject },
      { labelKey: "menu.back_start", action: onBackToStart },
    ],
    edit: [
      { labelKey: "menu.undo", action: onUndo, disabled: !canUndo },
      { labelKey: "menu.redo", action: onRedo, disabled: !canRedo },
    ],
    view: [
      { labelKey: "menu.feature_tree", action: () => openLeft("feature") },
      { labelKey: "menu.assistant", action: () => openRight("assistant") },
      { labelKey: "menu.review", action: () => openRight("review") },
      { labelKey: "menu.export", action: () => openRight("export") },
    ],
    tools: [{ labelKey: "menu.settings", action: onOpenSettings }],
    help: [{ labelKey: "menu.about", action: onOpenSettings }],
  };

  const commands = [
    {
      id: "features",
      labelKey: "cmd.features",
      action: () => openLeft("feature"),
      active: leftDrawerOpen && leftTab === "feature",
    },
    {
      id: "sketch",
      labelKey: "cmd.sketch",
      action: () => openLeft("property"),
      active: leftDrawerOpen && leftTab === "property",
    },
    {
      id: "evaluate",
      labelKey: "cmd.evaluate",
      action: () => openRight("review"),
      active: rightDrawerOpen && rightTab === "review",
    },
    {
      id: "ai",
      labelKey: "cmd.ai",
      action: () => openRight("assistant"),
      active: rightDrawerOpen && rightTab === "assistant",
    },
  ];

  const menuLabels: Record<string, string> = {
    file: t("menu.file"),
    edit: t("menu.edit"),
    view: t("menu.view"),
    tools: t("menu.tools"),
    help: t("menu.help"),
  };

  return (
    <header className={`top-shell${focusMode ? " focus-mode" : ""}`}>
      <div className="title-bar compact-top">
        <div className="brand-block">
          <p className="eyebrow">VAREN CAD IDE</p>
          <h1>{t("top.brand")}</h1>
        </div>
        <div className="title-project">
          <strong>{projectName}</strong>
          <span>{engineLabel}</span>
        </div>
        <div className="menu-bar">
          {Object.entries(menus).map(([key, items]) => (
            <div className="menu-wrap" key={key}>
              <button
                type="button"
                className={openMenu === key ? "menu-button active" : "menu-button"}
                onClick={() => setOpenMenu((current) => (current === key ? null : key))}
                onBlur={() => setOpenMenu(null)}
              >
                {menuLabels[key]}
              </button>
              {openMenu === key && (
                <div className="menu-dropdown" onMouseDown={(event) => event.preventDefault()}>
                  {items.map((item) => (
                    <button
                      type="button"
                      key={item.labelKey}
                      disabled={item.disabled}
                      onClick={() => run(item.action)}
                    >
                      {t(item.labelKey)}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
          <div className="command-tabs" role="tablist">
            {commands.map((command) => (
              <button
                type="button"
                key={command.id}
                role="tab"
                aria-selected={command.active}
                className={command.active ? "command-tab active" : "command-tab"}
                onClick={command.action}
              >
                {t(command.labelKey)}
              </button>
            ))}
          </div>
        </div>
        <div className="title-status">
          <span className={backendState === "connected" ? "workspace-chip ok" : "workspace-chip danger"}>
            {backendState === "connected" ? t("startup.backend.connected") : t("startup.backend.offline")}
          </span>
          <span className="workspace-chip">{statusLabel}</span>
          <span className="workspace-chip">{modeLabel}</span>
        </div>
        <div className="quick-actions">
          <button
            type="button"
            title={t("top.focus.title")}
            className={focusMode ? "focus-toggle active" : "focus-toggle"}
            onClick={() => setUi({ focusMode: !focusMode })}
          >
            {t("top.focus")}
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
            className="primary"
            title={t("top.generate.title")}
            onClick={onGenerate}
            disabled={busy || agentRunning}
          >
            {busy ? t("top.busy") : agentRunning ? t("agent.running") : t("top.generate")}
          </button>
        </div>
      </div>
    </header>
  );
}
