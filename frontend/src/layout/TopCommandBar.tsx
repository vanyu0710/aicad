import { useState } from "react";
import { useAppStore } from "../store";

type Props = {
  backendState: "connected" | "offline";
  busy: boolean;
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
  onBackToStart: () => void;
  onOpenSettings: () => void;
};

export default function TopCommandBar({
  backendState,
  busy,
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
  onBackToStart,
  onOpenSettings,
}: Props) {
  const setUi = useAppStore((state) => state.setUi);
  const [openMenu, setOpenMenu] = useState<string | null>(null);

  const run = (action: () => void) => {
    setOpenMenu(null);
    action();
  };

  const menus: Record<string, { label: string; action: () => void; disabled?: boolean }[]> = {
    file: [
      { label: "新建项目", action: onNewProject },
      { label: "回到启动页", action: onBackToStart },
    ],
    edit: [
      { label: "撤销", action: onUndo, disabled: !canUndo },
      { label: "重做", action: onRedo, disabled: !canRedo },
    ],
    view: [
      { label: "特征树", action: () => setUi({ leftTab: "feature" }) },
      { label: "AI 助手", action: () => setUi({ rightTab: "assistant" }) },
      { label: "设计评审", action: () => setUi({ rightTab: "review" }) },
      { label: "导出", action: () => setUi({ rightTab: "export" }) },
    ],
    tools: [{ label: "设置中心", action: onOpenSettings }],
    help: [
      { label: "关于栖云", action: () => setUi({ commandTab: "ai" }) },
    ],
  };

  const commands = [
    { id: "features", label: "特征", action: () => setUi({ leftTab: "feature" }) },
    { id: "sketch", label: "属性", action: () => setUi({ leftTab: "property" }) },
    { id: "evaluate", label: "评估", action: () => setUi({ rightTab: "review" }) },
    { id: "ai", label: "AI 助手", action: () => setUi({ rightTab: "assistant" }) },
  ];

  return (
    <header className="top-shell">
      <div className="title-bar">
        <div className="brand-block">
          <p className="eyebrow">MECHCAD AI CAD IDE</p>
          <h1>栖云</h1>
        </div>
        <div className="title-project">
          <strong>{projectName}</strong>
          <span>{engineLabel}</span>
        </div>
        <div className="title-status">
          <span className={backendState === "connected" ? "workspace-chip ok" : "workspace-chip danger"}>
            {backendState === "connected" ? "后端已连接" : "后端离线"}
          </span>
          <span className="workspace-chip">{statusLabel}</span>
          <span className="workspace-chip">{modeLabel}</span>
        </div>
        <div className="quick-actions">
          <button type="button" title="设置中心 (Ctrl+,)" onClick={onOpenSettings}>
            设置
          </button>
          <button type="button" title="撤销 (Ctrl+Z)" onClick={onUndo} disabled={busy || !canUndo}>
            撤销
          </button>
          <button type="button" title="重做 (Ctrl+Y)" onClick={onRedo} disabled={busy || !canRedo}>
            重做
          </button>
          <button type="button" className="primary" title="生成或重新计算模型 (Ctrl+G)" onClick={onGenerate} disabled={busy}>
            {busy ? "处理中" : "生成 / 重算"}
          </button>
        </div>
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
              {key === "file" ? "文件" : key === "edit" ? "编辑" : key === "view" ? "视图" : key === "tools" ? "工具" : "帮助"}
            </button>
            {openMenu === key && (
              <div className="menu-dropdown" onMouseDown={(event) => event.preventDefault()}>
                {items.map((item) => (
                  <button
                    type="button"
                    key={item.label}
                    disabled={item.disabled}
                    onClick={() => run(item.action)}
                  >
                    {item.label}
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
              className="command-tab"
              onClick={command.action}
            >
              {command.label}
            </button>
          ))}
        </div>
      </div>
    </header>
  );
}
