import ModelConfigPanel from "./ModelConfigPanel";
import type { ModelConfig } from "./api";
import type { StartupMode } from "./store";
import { useAppStore } from "./store";
import { useT } from "./i18n";

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
  const t = useT();
  const language = useAppStore((state) => state.language);
  const setLanguage = useAppStore((state) => state.setLanguage);

  if (!open) {
    return null;
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="settings-dialog" role="dialog" aria-modal="true" aria-label={t("settings.title")}>
        <header className="settings-header">
          <div>
            <p className="eyebrow">SETTINGS</p>
            <h2>{t("settings.title")}</h2>
          </div>
          <button type="button" className="icon-button" onClick={onClose} title={t("settings.close.title")}>
            {t("settings.close")}
          </button>
        </header>

        <div className="settings-body">
          <section className="settings-section">
            <div className="settings-section-title">
              <h3>{t("settings.general")}</h3>
              <span>{t("settings.general.hint")}</span>
            </div>
            <label className="settings-mode-field">
              <span>{t("settings.language")}</span>
              <select
                value={language}
                onChange={(event) => setLanguage(event.target.value as "zh" | "en")}
              >
                <option value="zh">{t("settings.language.zh")}</option>
                <option value="en">{t("settings.language.en")}</option>
              </select>
            </label>
            <label className="settings-mode-field">
              <span>{t("startup.behavior")}</span>
              <select value={startupMode} onChange={(event) => onStartupModeChange(event.target.value as StartupMode)}>
                <option value="always">{t("startup.mode.always")}</option>
                <option value="first">{t("startup.mode.first")}</option>
                <option value="off">{t("startup.mode.off")}</option>
              </select>
            </label>
            <label className="settings-mode-field">
              <span>{t("settings.operation_mode")}</span>
              <select
                value={settings.operation_mode}
                onChange={(event) =>
                  onChange({
                    ...settings,
                    operation_mode: event.target.value as "strict" | "smart",
                  })
                }
              >
                <option value="strict">{t("settings.mode.strict")}</option>
                <option value="smart">{t("settings.mode.smart")}</option>
              </select>
            </label>
            {settings.operation_mode === "smart" && (
              <label className="settings-mode-field">
                <span>{t("settings.smart_policy")}</span>
                <select
                  value={settings.smart_fill_policy}
                  onChange={(event) =>
                    onChange({
                      ...settings,
                      smart_fill_policy: event.target.value as ModelConfig["smart_fill_policy"],
                    })
                  }
                >
                  <option value="limited_fill">{t("settings.policy.limited_fill")}</option>
                  <option value="aggressive_fill">{t("settings.policy.aggressive_fill")}</option>
                  <option value="full_autonomous">{t("settings.policy.full_autonomous")}</option>
                </select>
              </label>
            )}

          </section>

          <section className="settings-section settings-model-section">
            <div className="settings-section-title">
              <h3>{t("settings.models")}</h3>
              <span>{t("settings.models.hint")}</span>
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
          <span>{dirty ? t("settings.dirty") : t("settings.saved")}</span>
          <button type="button" className="primary" onClick={onClose}>
            {t("settings.done")}
          </button>
        </footer>
      </section>
    </div>
  );
}