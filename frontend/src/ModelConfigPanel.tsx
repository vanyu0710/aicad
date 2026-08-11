import { useState } from "react";
import { testModelConnection, type ModelConfig, type ModelRole, type ModelTestResult } from "./api";
import { useT } from "./i18n";
import { useAppStore } from "./store";

type Props = {
  value: ModelConfig;
  onChange: (value: ModelConfig) => void;
  onApply: () => void;
  dirty: boolean;
  notice: string;
  saving: boolean;
  disabled?: boolean;
};

const presets: Record<string, Partial<ModelConfig>> = {
  custom_openai: {
    vision_provider: "custom",
    planner_provider: "custom",
    vision_protocol: "openai",
    planner_protocol: "openai",
  },
  custom_anthropic: {
    vision_provider: "custom",
    planner_provider: "custom",
    vision_protocol: "anthropic",
    planner_protocol: "anthropic",
  },
};

export default function ModelConfigPanel({ value, onChange, onApply, dirty, notice, saving, disabled }: Props) {
  const t = useT();
  const language = useAppStore((state) => state.language);
  const [results, setResults] = useState<Partial<Record<ModelRole, ModelTestResult>>>({});
  const [testing, setTesting] = useState<ModelRole | "">("");
  const [message, setMessage] = useState("");

  const update = (patch: Partial<ModelConfig>) => onChange({ ...value, ...patch });
  const test = async (role: ModelRole) => {
    setTesting(role);
    setMessage("");
    try {
      const result = await testModelConnection(role, value, language);
      setResults((previous) => ({ ...previous, [role]: result }));
      const roleLabel = role === "vision" ? t("model.vision.role") : t("model.planner.role");
      setMessage(t("model.test.message", { role: roleLabel, message: result.message }));
    } catch (error) {
      setMessage(String(error));
    } finally {
      setTesting("");
    }
  };

  const visionReady = Boolean(value.vision_model && value.vision_base_url && value.vision_api_key);
  const plannerReady = Boolean(value.planner_model && value.planner_base_url && value.planner_api_key);

  return (
    <section className="detailed-config">
      <div className="config-summary">
        <div className="config-summary-item">
          <span>{t("model.vision")}</span>
          <strong className={visionReady ? "ok" : ""}>{visionReady ? t("model.vision.ready") : t("model.use_env")}</strong>
        </div>
        <div className="config-summary-item">
          <span>{t("model.planner")}</span>
          <strong className={plannerReady ? "ok" : ""}>{plannerReady ? t("model.planner.ready") : t("model.use_env")}</strong>
        </div>
        <div className="config-summary-item">
          <span>{t("model.protocol.default")}</span>
          <strong>{value.planner_protocol === "anthropic" ? "Anthropic" : "OpenAI"}</strong>
        </div>
      </div>

      <div className="preset-row">
        <label className="field compact">
          <span className="field-label">
            {t("model.preset")}
            <small>{t("model.preset.hint")}</small>
          </span>
          <select
            value=""
            onChange={(event) => {
              if (event.target.value) update(presets[event.target.value]);
            }}
          >
            <option value="">{t("model.preset.select")}</option>
            <option value="custom_openai">{t("model.preset.openai")}</option>
            <option value="custom_anthropic">{t("model.preset.anthropic")}</option>
          </select>
        </label>
        <label className="confirm-check config-check">
          <input
            type="checkbox"
            checked={Boolean(value.force_real_api)}
            onChange={(event) => update({ force_real_api: event.target.checked })}
          />
          <span>
            {t("model.force_real")}
            <small>{t("model.force_real.hint")}</small>
          </span>
        </label>
        <button type="button" className="apply-settings" onClick={onApply} disabled={disabled || saving || !dirty}>
          {saving ? t("model.saving") : dirty ? t("model.apply") : t("model.applied")}
        </button>
      </div>

      <RoleCard
        role="vision"
        value={value}
        onChange={update}
        result={results.vision}
        testing={testing === "vision"}
        onTest={test}
        disabled={disabled}
      />
      <RoleCard
        role="planner"
        value={value}
        onChange={update}
        result={results.planner}
        testing={testing === "planner"}
        onTest={test}
        disabled={disabled}
      />

      {message && <div className="connection-summary">{message}</div>}
      {notice && <div className="settings-notice">{notice}</div>}
    </section>
  );
}

function RoleCard({
  role,
  value,
  onChange,
  result,
  testing,
  onTest,
  disabled,
}: {
  role: ModelRole;
  value: ModelConfig;
  onChange: (patch: Partial<ModelConfig>) => void;
  result?: ModelTestResult;
  testing: boolean;
  onTest: (role: ModelRole) => void;
  disabled?: boolean;
}) {
  const t = useT();
  const isVision = role === "vision";
  const label = isVision ? t("model.vision.role") : t("model.planner.role");
  const subtitle = isVision ? t("model.vision.subtitle") : t("model.planner.subtitle");
  const field = (name: string) => `${role}_${name}` as keyof ModelConfig;
  const protocol = String(value[field("protocol")] || "openai");

  return (
    <div className="role-card">
      <div className="role-card-title">
        <div>
          <strong>{label}</strong>
          <small>{subtitle}</small>
        </div>
        <span className={`role-state ${protocol === "anthropic" ? "anthropic" : "openai"}`}>
          {protocol === "anthropic" ? "Anthropic" : "OpenAI"}
        </span>
      </div>

      <div className="config-grid">
        <label className="field compact">
          <span className="field-label">
            {t("model.provider")}
            <small>{t("model.provider.hint")}</small>
          </span>
          <input
            value={String(value[field("provider")] || "")}
            onChange={(event) => onChange({ [field("provider")]: event.target.value })}
            placeholder="custom"
          />
        </label>

        <label className="field compact">
          <span className="field-label">
            {t("model.protocol")}
            <small>{t("model.protocol.hint")}</small>
          </span>
          <select value={protocol} onChange={(event) => onChange({ [field("protocol")]: event.target.value })}>
            <option value="openai">{t("model.protocol.openai")}</option>
            <option value="anthropic">{t("model.protocol.anthropic")}</option>
          </select>
        </label>

        <label className="field compact wide">
          <span className="field-label">
            {t("model.base_url")}
            <small>{t("model.base_url.hint")}</small>
          </span>
          <input
            value={String(value[field("base_url")] || "")}
            onChange={(event) => onChange({ [field("base_url")]: event.target.value })}
            placeholder={protocol === "anthropic" ? "https://api.example.com" : "https://api.example.com/v1"}
          />
        </label>

        <label className="field compact">
          <span className="field-label">
            {t("model.name")}
            <small>{t("model.name.hint")}</small>
          </span>
          <input
            value={String(value[field("model")] || "")}
            onChange={(event) => onChange({ [field("model")]: event.target.value })}
            placeholder={isVision ? t("model.name.vision") : t("model.name.planner")}
          />
        </label>

        <label className="field compact wide">
          <span className="field-label">
            {t("model.api_key")}
            <small>{t("model.api_key.hint")}</small>
          </span>
          <input
            type="password"
            value={String(value[field("api_key")] || "")}
            onChange={(event) => onChange({ [field("api_key")]: event.target.value })}
            placeholder={t("model.api_key.placeholder")}
            autoComplete="off"
          />
        </label>
      </div>

      <div className="role-actions">
        <button type="button" onClick={() => onTest(role)} disabled={disabled || testing}>
          {testing ? t("model.testing") : t("model.test")}
        </button>
        {result && (
          <span className={result.ok ? "test-ok" : "test-fail"}>
            {result.ok ? t("model.test.ok") : t("model.test.fail", { message: result.message })}
          </span>
        )}
      </div>

      {result?.diagnostics && (
        <div className="test-diagnostics">
          <span>HTTP {result.diagnostics.status_code ?? "-"}</span>
          <span>{result.diagnostics.content_type || t("model.diag.no_content_type")}</span>
          <span>{result.diagnostics.used_env_fallback ? t("model.diag.env") : t("model.diag.project")}</span>
        </div>
      )}

      <small className="help-text">{t("model.help")}</small>
    </div>
  );
}
