import { useState } from "react";
import FeatureForm from "../FeatureForm";
import FeatureTree from "../FeatureTree";
import type { ModelConfig } from "../api";
import { DEFAULT_SETTINGS, useAppStore, type ManagerTab } from "../store";
import { useT } from "../i18n";

type Props = {
  busy: boolean;
  description: string;
  features: any[];
  imageFile: File | null;
  modeLabel: string;
  partFamily?: string;
  projectName: string;
  selectedFeature: any;
  selectedFeatureId: string;
  statusLabel: string;
  unresolvedCount: number;
  evidenceCount?: number;
  intentSummary?: string;
  completenessScore?: number;
  settings?: ModelConfig;
  onSettingsChange?: (value: ModelConfig) => void;
  onApplySettings?: (value: ModelConfig) => void;
  onDescriptionChange: (value: string) => void;
  onImageChange: (file: File | null) => void;
  onSelectFeature: (featureId: string) => void;
  onSaveFeature: (payload: any) => void;
  onOpenSettings: () => void;
  onClose?: () => void;
};

const tabs: { id: ManagerTab; labelKey: string }[] = [
  { id: "feature", labelKey: "manager.feature_tree" },
  { id: "property", labelKey: "manager.property" },
  { id: "configuration", labelKey: "manager.configuration" },
];

export default function LeftManager({
  busy,
  description,
  features,
  imageFile,
  modeLabel,
  partFamily,
  projectName,
  selectedFeature,
  selectedFeatureId,
  statusLabel,
  unresolvedCount,
  evidenceCount,
  intentSummary,
  completenessScore,
  settings = DEFAULT_SETTINGS,
  onSettingsChange,
  onApplySettings,
  onDescriptionChange,
  onImageChange,
  onSelectFeature,
  onSaveFeature,
  onOpenSettings,
  onClose,
}: Props) {
  const t = useT();
  const leftTab = useAppStore((state) => state.ui.leftTab);
  const setUi = useAppStore((state) => state.setUi);
  const [inputOpen, setInputOpen] = useState(true);

  return (
    <aside className="manager-pane" aria-label="Manager Pane">
      <div className="manager-pane-header">
        <div>
          <span className="eyebrow">MANAGER</span>
          <h2>{projectName}</h2>
        </div>
        <div className="manager-header-actions">
          <span className="workspace-chip">{statusLabel}</span>
          <button type="button" className="collapse-button drawer-close-button" title={t("manager.close.title")} onClick={() => onClose?.()}>
            {t("manager.close")}
          </button>
        </div>
      </div>

      <div className="manager-tabs" role="tablist">
        {tabs.map((tab) => (
          <button
            type="button"
            key={tab.id}
            role="tab"
            aria-selected={leftTab === tab.id}
            className={leftTab === tab.id ? "manager-tab active" : "manager-tab"}
            onClick={() => setUi({ leftTab: tab.id })}
          >
            {t(tab.labelKey)}
          </button>
        ))}
      </div>

      <div className="manager-pane-body">
        {leftTab === "feature" && (
          <div className="manager-tab-page">
            <section className="input-section">
              <button type="button" className="input-toggle" onClick={() => setInputOpen((open) => !open)}>
                <span>{t("manager.input")}</span>
                <span>{inputOpen ? t("manager.input.close") : t("manager.input.open")}</span>
              </button>
              {inputOpen && (
                <div className="input-body">
                  <label className="field">
                    <span className="field-label">
                      {t("manager.image")}
                      <small>{t("manager.image.hint")}</small>
                    </span>
                    <input
                      type="file"
                      accept="image/png,image/jpeg"
                      onChange={(event) => onImageChange(event.target.files?.[0] || null)}
                    />
                  </label>
                  {imageFile && (
                    <div className="file-summary">
                      <strong>{imageFile.name}</strong>
                      <small>{Math.round(imageFile.size / 1024)} KB</small>
                      <button type="button" onClick={() => onImageChange(null)}>
                        {t("manager.image.remove")}
                      </button>
                    </div>
                  )}
                  <label className="field">
                    <span className="field-label">
                      {t("manager.description")}
                      <small>{t("manager.description.hint")}</small>
                    </span>
                    <textarea
                      value={description}
                      onChange={(event) => onDescriptionChange(event.target.value)}
                      rows={6}
                      placeholder={t("manager.description.placeholder")}
                    />
                  </label>
                </div>
              )}
            </section>

            <section className="feature-manager-section">
              <div className="manager-section-title">
                <h3>{t("manager.feature_title")}</h3>
                <span>{t("manager.feature_count", { count: features.length })}</span>
              </div>
              <FeatureTree
                features={features}
                selectedFeatureId={selectedFeatureId}
                onSelectFeature={onSelectFeature}
                evidenceCount={evidenceCount}
                intentSummary={intentSummary}
                completenessScore={completenessScore}
              />
                            <div className="project-meta">
                <span>{t("manager.part_family", { value: partFamily || t("manager.unrecognized") })}</span>
                <span>{t("manager.unresolved", { count: unresolvedCount })}</span>
                <span>{t("manager.mode", { value: modeLabel })}</span>
              </div>
            </section>
          </div>
        )}

        {leftTab === "property" && (
          <div className="manager-tab-page">
            <div className="manager-section-title">
              <h3>{t("manager.property_title")}</h3>
              <span>{selectedFeature ? selectedFeature.type : t("manager.none_selected")}</span>
            </div>
            {selectedFeature ? (
              <FeatureForm key={selectedFeature.id} feature={selectedFeature} busy={busy} onSave={onSaveFeature} />
            ) : (
              <div className="inspector-empty">
                <strong>{t("manager.no_feature")}</strong>
                <span>{t("manager.no_feature.hint")}</span>
              </div>
            )}
          </div>
        )}

        {leftTab === "configuration" && (
          <div className="manager-tab-page">
            <div className="manager-section-title">
              <h3>{t("manager.config_title")}</h3>
              <span>{t("manager.config_subtitle")}</span>
            </div>
            <div className="config-summary">
              <div className="config-summary-item">
                <span>{t("manager.part_family_label")}</span>
                <strong>{partFamily || t("manager.unrecognized")}</strong>
              </div>
              <div className="config-summary-item">
                <span>{t("manager.cad_engine")}</span>
                <strong>Build123d Worker</strong>
              </div>
              <div className="config-summary-item">
                <span>{t("manager.work_mode")}</span>
                <strong>{modeLabel}</strong>
              </div>
            </div>
            <div className="config-editor">
              <label className="field compact">
                <span className="field-label">
                  {t("settings.operation_mode")}
                  <small>{t("settings.operation_mode.hint")}</small>
                </span>
                <select
                  value={settings.operation_mode}
                  onChange={(event) => {
                    const next = {
                      ...settings,
                      operation_mode: event.target.value as "strict" | "smart",
                    };
                    onSettingsChange?.(next);
                    onApplySettings?.(next);
                  }}
                >
                  <option value="strict">{t("settings.mode.strict")}</option>
                  <option value="smart">{t("settings.mode.smart")}</option>
                </select>
              </label>
              {settings.operation_mode === "smart" && (
                <label className="field compact">
                  <span className="field-label">
                    {t("settings.smart_policy")}
                    <small>{t("settings.smart_policy.hint")}</small>
                  </span>
                  <select
                    value={settings.smart_fill_policy}
                    onChange={(event) => {
                      const next = {
                        ...settings,
                        smart_fill_policy: event.target.value as ModelConfig["smart_fill_policy"],
                      };
                      onSettingsChange?.(next);
                      onApplySettings?.(next);
                    }}
                  >
                    <option value="limited_fill">{t("settings.policy.limited_fill")}</option>
                    <option value="aggressive_fill">{t("settings.policy.aggressive_fill")}</option>
                    <option value="full_autonomous">{t("settings.policy.full_autonomous")}</option>
                  </select>
                </label>
              )}
            </div>
            <div className="configuration-actions">
              <p>{t("manager.config.hint")}</p>
              <button type="button" className="primary" onClick={onOpenSettings}>
                {t("manager.open_settings")}
              </button>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}