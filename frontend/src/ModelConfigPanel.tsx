import { useState } from "react";
import { testModelConnection, type ModelConfig, type ModelRole, type ModelTestResult } from "./api";

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
  const [results, setResults] = useState<Partial<Record<ModelRole, ModelTestResult>>>({});
  const [testing, setTesting] = useState<ModelRole | "">("");
  const [message, setMessage] = useState("");

  const update = (patch: Partial<ModelConfig>) => onChange({ ...value, ...patch });
  const test = async (role: ModelRole) => {
    setTesting(role);
    setMessage("");
    try {
      const result = await testModelConnection(role, value);
      setResults((previous) => ({ ...previous, [role]: result }));
      setMessage(`${role === "vision" ? "视觉读图模型" : "建模规划模型"}：${result.message}`);
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
          <span>视觉模型</span>
          <strong className={visionReady ? "ok" : ""}>{visionReady ? "已配置" : "使用 .env"}</strong>
        </div>
        <div className="config-summary-item">
          <span>规划模型</span>
          <strong className={plannerReady ? "ok" : ""}>{plannerReady ? "已配置" : "使用 .env"}</strong>
        </div>
        <div className="config-summary-item">
          <span>默认协议</span>
          <strong>{value.planner_protocol === "anthropic" ? "Anthropic" : "OpenAI"}</strong>
        </div>
      </div>

      <div className="preset-row">
        <label className="field compact">
          <span className="field-label">
            协议预设
            <small>只切换协议与 Provider，不覆盖已有 Key。</small>
          </span>
          <select
            value=""
            onChange={(event) => {
              if (event.target.value) update(presets[event.target.value]);
            }}
          >
            <option value="">选择预设</option>
            <option value="custom_openai">自定义 OpenAI 兼容</option>
            <option value="custom_anthropic">自定义 Anthropic 兼容</option>
          </select>
        </label>
        <label className="confirm-check config-check">
          <input
            type="checkbox"
            checked={Boolean(value.force_real_api)}
            onChange={(event) => update({ force_real_api: event.target.checked })}
          />
          <span>
            强制使用真实 API
            <small>关闭本地示例兜底；未配置模型时直接报错。</small>
          </span>
        </label>
        <button type="button" className="apply-settings" onClick={onApply} disabled={disabled || saving || !dirty}>
          {saving ? "保存中..." : dirty ? "应用配置" : "配置已应用"}
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
  const isVision = role === "vision";
  const label = isVision ? "视觉读图模型" : "建模规划模型";
  const subtitle = isVision
    ? "从草图读取视图、尺寸标注、基准、特征证据和不确定项。"
    : "把视觉读图结果转换成受控 FeaturePlan，再由 CAD Worker 执行。";
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
            Provider
            <small>服务商标识，如 custom、openai、minimax。</small>
          </span>
          <input
            value={String(value[field("provider")] || "")}
            onChange={(event) => onChange({ [field("provider")]: event.target.value })}
            placeholder="custom"
          />
        </label>

        <label className="field compact">
          <span className="field-label">
            协议
            <small>决定请求体与鉴权格式。</small>
          </span>
          <select value={protocol} onChange={(event) => onChange({ [field("protocol")]: event.target.value })}>
            <option value="openai">OpenAI 兼容</option>
            <option value="anthropic">Anthropic 兼容</option>
          </select>
        </label>

        <label className="field compact wide">
          <span className="field-label">
            Base URL
            <small>留空则使用 .env 默认值；OpenAI 兼容通常以 /v1 结尾。</small>
          </span>
          <input
            value={String(value[field("base_url")] || "")}
            onChange={(event) => onChange({ [field("base_url")]: event.target.value })}
            placeholder={protocol === "anthropic" ? "https://api.example.com" : "https://api.example.com/v1"}
          />
        </label>

        <label className="field compact">
          <span className="field-label">
            模型名称
            <small>填写完整模型 ID，例如 qwen2.5-vl-32b-instruct。</small>
          </span>
          <input
            value={String(value[field("model")] || "")}
            onChange={(event) => onChange({ [field("model")]: event.target.value })}
            placeholder={isVision ? "视觉模型 ID" : "规划模型 ID"}
          />
        </label>

        <label className="field compact wide">
          <span className="field-label">
            API Key
            <small>仅保存在当前项目；页面不回显已配置密钥。</small>
          </span>
          <input
            type="password"
            value={String(value[field("api_key")] || "")}
            onChange={(event) => onChange({ [field("api_key")]: event.target.value })}
            placeholder="留空使用 .env"
            autoComplete="off"
          />
        </label>
      </div>

      <div className="role-actions">
        <button type="button" onClick={() => onTest(role)} disabled={disabled || testing}>
          {testing ? "测试中..." : "测试连接"}
        </button>
        {result && (
          <span className={result.ok ? "test-ok" : "test-fail"}>
            {result.ok ? "连接成功" : `失败：${result.message}`}
          </span>
        )}
      </div>

      {result?.diagnostics && (
        <div className="test-diagnostics">
          <span>HTTP {result.diagnostics.status_code ?? "-"}</span>
          <span>{result.diagnostics.content_type || "无 Content-Type"}</span>
          <span>{result.diagnostics.used_env_fallback ? "使用了 .env" : "使用了项目配置"}</span>
        </div>
      )}

      <small className="help-text">测试只发送最小文本请求，不上传图片，也不会启动 CAD。</small>
    </div>
  );
}