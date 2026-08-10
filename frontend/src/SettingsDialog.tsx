import ModelConfigPanel from "./ModelConfigPanel";
import type { ModelConfig } from "./api";
import type { StartupMode } from "./store";

type Props = {
  open: boolean;
  settings: ModelConfig;
  dirty: boolean;
  saving: boolean;
  notice: string;
  startupMode: StartupMode;
  onClose: () => void;
  onChange: (value: ModelConfig) => void;
  onApply: () => void;
  onStartupModeChange: (mode: StartupMode) => void;
};

export default function SettingsDialog({
  open,
  settings,
  dirty,
  saving,
  notice,
  startupMode,
  onClose,
  onChange,
  onApply,
  onStartupModeChange,
}: Props) {
  if (!open) {
    return null;
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="settings-dialog" role="dialog" aria-modal="true" aria-label="设置中心">
        <header className="settings-header">
          <div>
            <p className="eyebrow">SETTINGS</p>
            <h2>设置中心</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} title="关闭设置">
            关闭
          </button>
        </header>

        <div className="settings-body">
          <section className="settings-section">
            <div className="settings-section-title">
              <h3>通用</h3>
              <span>启动页与工作台偏好</span>
            </div>
            <label className="settings-mode-field">
              <span>启动页行为</span>
              <select value={startupMode} onChange={(event) => onStartupModeChange(event.target.value as StartupMode)}>
                <option value="always">每次启动显示</option>
                <option value="first">仅首次显示</option>
                <option value="off">关闭，直接进入工作台</option>
              </select>
            </label>
          </section>

          <section className="settings-section settings-model-section">
            <div className="settings-section-title">
              <h3>模型配置</h3>
              <span>视觉读图模型与建模规划模型，可分别配置协议、地址、模型名和密钥。</span>
            </div>
            <ModelConfigPanel
              value={settings}
              onChange={onChange}
              onApply={onApply}
              dirty={dirty}
              notice={notice}
              saving={saving}
            />
          </section>
        </div>

        <footer className="settings-footer">
          <span>{dirty ? "有未保存的配置修改" : "配置已保存"}</span>
          <button type="button" className="primary" onClick={onClose}>
            完成
          </button>
        </footer>
      </section>
    </div>
  );
}
