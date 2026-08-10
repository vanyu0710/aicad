type Props = {
  backendState: "connected" | "offline";
  busy: boolean;
  canRedo: boolean;
  canUndo: boolean;
  engineLabel: string;
  modeLabel: string;
  projectId?: string;
  projectName: string;
  statusLabel: string;
  onGenerate: () => void;
  onRedo: () => void;
  onUndo: () => void;
};

export default function TopCommandBar({
  backendState,
  busy,
  canRedo,
  canUndo,
  engineLabel,
  modeLabel,
  projectId,
  projectName,
  statusLabel,
  onGenerate,
  onRedo,
  onUndo,
}: Props) {
  const displayedEngine = engineLabel.includes("CAD Worker") ? "Build123d Worker（受控执行）" : engineLabel;
  return (
    <header className="top-command-bar">
      <div className="brand-block">
        <p className="eyebrow">MECHCAD AI CAD IDE</p>
        <h1>栖云</h1>
        <span>{projectName}</span>
      </div>

      <div className="command-status" aria-label="项目状态">
        <span className={backendState === "connected" ? "workspace-chip ok" : "workspace-chip danger"}>
          {backendState === "connected" ? "后端已连接" : "后端离线"}
        </span>
        <span className="workspace-chip">{statusLabel}</span>
        <span className="workspace-chip">{modeLabel}</span>
        <span className="workspace-chip muted-chip">{displayedEngine}</span>
        {projectId && <span className="workspace-chip id-chip">ID {projectId.slice(0, 8)}</span>}
      </div>

      <div className="command-actions">
        <button type="button" title="撤销上一次设计快照" onClick={onUndo} disabled={busy || !canUndo}>
          撤销
        </button>
        <button type="button" title="重做已撤销的设计快照" onClick={onRedo} disabled={busy || !canRedo}>
          重做
        </button>
        <button type="button" className="primary" onClick={onGenerate} disabled={busy}>
          {busy ? "处理中" : "生成 / 重算"}
        </button>
      </div>
    </header>
  );
}
