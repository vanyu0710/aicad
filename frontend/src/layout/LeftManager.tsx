import { useState, type ReactNode } from "react";
import ModelConfigPanel from "../ModelConfigPanel";
import type { ModelConfig } from "../api";

type Props = {
  busy?: boolean;
  description: string;
  features: any[];
  imageFile: File | null;
  modeLabel: string;
  partFamily?: string;
  projectId?: string;
  projectName: string;
  selectedFeatureId: string;
  settings: ModelConfig;
  settingsDirty: boolean;
  settingsNotice: string;
  settingsSaving: boolean;
  statusLabel: string;
  unresolvedCount: number;
  onDescriptionChange: (value: string) => void;
  onImageChange: (file: File | null) => void;
  onSelectFeature: (featureId: string) => void;
  onApplySettings: (value?: ModelConfig) => void;
  onSettingsChange: (value: ModelConfig) => void;
};

const policyHelp: Record<string, { label: string; detail: string }> = {
  limited_fill: {
    label: "工程保守设计",
    detail: "自动识别基体和特征顺序，补全低风险概念尺寸；关键孔位、槽位、壁厚保持待确认。",
  },
  aggressive_fill: {
    label: "概念快速生成",
    detail: "根据功能、图形比例和机械常识补全主要尺寸，直接生成概念 STEP/STL，所有推断可追溯。",
  },
  full_autonomous: {
    label: "全自主方案设计",
    detail: "主动选择零件族、主基体、特征树和制造意图，可增加孔、槽、台阶、凸台、加强肋等必要特征。",
  },
  suggest_only: {
    label: "仅给建议",
    detail: "只输出设计建议，不把推断尺寸写入可执行模型；适合先评审再建模。",
  },
};

export default function LeftManager({
  busy,
  description,
  features,
  imageFile,
  modeLabel,
  partFamily,
  projectId,
  projectName,
  selectedFeatureId,
  settings,
  settingsDirty,
  settingsNotice,
  settingsSaving,
  statusLabel,
  unresolvedCount,
  onDescriptionChange,
  onImageChange,
  onSelectFeature,
  onApplySettings,
  onSettingsChange,
}: Props) {
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({
    input: true,
    mode: true,
    models: true,
    features: true,
  });
  const toggleSection = (key: string) =>
    setOpenSections((previous) => ({ ...previous, [key]: !previous[key] }));

  const updateSettings = (patch: Partial<ModelConfig>) => onSettingsChange({ ...settings, ...patch });
  const updateMode = (operation_mode: ModelConfig["operation_mode"]) => {
    const next = { ...settings, operation_mode };
    onSettingsChange(next);
    onApplySettings(next);
  };
  const policy = policyHelp[settings.smart_fill_policy] || policyHelp.limited_fill;

  return (
    <aside className="workspace-sidebar left-manager left-task-panel" aria-label="左侧任务栏">
      <div className="left-panel-header">
        <div>
          <span className="eyebrow">FEATURE MANAGER</span>
          <h2>{projectName}</h2>
        </div>
        <span className={`workspace-chip ${statusLabel === "执行失败" ? "danger" : ""}`}>{statusLabel}</span>
      </div>

      <div className="left-task-scroll">
        <TaskSection
          step="01"
          title="项目输入"
          badge={imageFile ? "已上传" : "未上传"}
          open={openSections.input}
          onToggle={() => toggleSection("input")}
        >
          <label className="field">
            <span className="field-label">
              草图图片
              <small>支持 PNG / JPG；图片会先经过本地预处理，再交给视觉模型读图。</small>
            </span>
            <input type="file" accept="image/png,image/jpeg" onChange={(event) => onImageChange(event.target.files?.[0] || null)} />
          </label>
          {imageFile && (
            <div className="file-summary">
              <strong>{imageFile.name}</strong>
              <small>{Math.round(imageFile.size / 1024)} KB</small>
              <button type="button" onClick={() => onImageChange(null)}>
                移除图片
              </button>
            </div>
          )}
          <label className="field">
            <span className="field-label">
              零件功能与已知尺寸
              <small>写用途、材料、配合关系、标注尺寸；不确定项写“未知”，系统会继续提问。</small>
            </span>
            <textarea
              value={description}
              onChange={(event) => onDescriptionChange(event.target.value)}
              rows={6}
              placeholder="例如：铝制套筒，外径 50mm，内径 30mm，长度 80mm，顶部有环槽，槽宽未知。"
            />
          </label>
        </TaskSection>

        <TaskSection
          step="02"
          title="工作模式"
          badge={modeLabel}
          open={openSections.mode}
          onToggle={() => toggleSection("mode")}
        >
          <div className="mode-segment">
            <button
              type="button"
              className={settings.operation_mode === "strict" ? "active" : ""}
              onClick={() => updateMode("strict")}
            >
              严格模式
            </button>
            <button
              type="button"
              className={settings.operation_mode === "smart" ? "active" : ""}
              onClick={() => updateMode("smart")}
            >
              智能模式
            </button>
          </div>

          <div className="mode-explanation">
            {settings.operation_mode === "strict" ? (
              <p>
                只使用图纸或用户确认的数据。缺失尺寸、定位或孔深时，建模会暂停并进入“待确认”问题区，AI 不猜数据。
              </p>
            ) : (
              <p>
                AI 会根据功能意图自主选择基体、补全概念尺寸并尝试建模。所有补全都会标记为智能假设，可在设计评审中逐项检查。
              </p>
            )}
          </div>

          {settings.operation_mode === "smart" && (
            <div className="field-group">
              <span className="field-label">
                自主设计策略
                <small>三档策略控制 AI 补全尺度，切换后需要点击“应用配置”才会保存到项目。</small>
              </span>
              <select
                value={settings.smart_fill_policy}
                onChange={(event) =>
                  updateSettings({ smart_fill_policy: event.target.value as ModelConfig["smart_fill_policy"] })
                }
              >
                <option value="limited_fill">工程保守设计，补全低风险尺寸</option>
                <option value="aggressive_fill">概念快速生成，积极补全并预览</option>
                <option value="full_autonomous">全自主方案设计，主动增加必要特征</option>
                <option value="suggest_only">仅给建议，不自动建模</option>
              </select>
              <div className="policy-detail">
                <strong>{policy.label}</strong>
                <p>{policy.detail}</p>
              </div>
            </div>
          )}
        </TaskSection>

        <TaskSection
          step="03"
          title="模型配置"
          badge={settingsDirty ? "未保存" : "已应用"}
          open={openSections.models}
          onToggle={() => toggleSection("models")}
        >
          <ModelConfigPanel
            value={settings}
            onChange={onSettingsChange}
            onApply={onApplySettings}
            dirty={settingsDirty}
            notice={settingsNotice}
            saving={settingsSaving}
            disabled={busy}
          />
        </TaskSection>

        <TaskSection
          step="04"
          title="特征树"
          badge={`${features.length} 项`}
          open={openSections.features}
          onToggle={() => toggleSection("features")}
        >
          <div className="feature-tree">
            {features.map((feature, index) => {
              const unresolved = feature?.unresolved?.length || 0;
              const isSelected = feature.id === selectedFeatureId;
              return (
                <button
                  type="button"
                  key={feature.id}
                  className={isSelected ? "feature-node selected" : "feature-node"}
                  onClick={() => onSelectFeature(feature.id)}
                >
                  <span className="feature-index">{index === 0 ? "基体" : `F${index}`}</span>
                  <span className="feature-name">{feature.id}</span>
                  <span className="feature-type">{feature.type}</span>
                  <span
                    className={
                      unresolved
                        ? "feature-state warn"
                        : feature.confirmed_by_user
                          ? "feature-state ok"
                          : "feature-state"
                    }
                  >
                    {unresolved ? "待确认" : feature.confirmed_by_user ? "已确认" : "草案"}
                  </span>
                </button>
              );
            })}
            {!features.length && (
              <div className="empty-tree">
                <strong>尚未生成特征树</strong>
                <p>生成后这里会显示类似 SolidWorks 的建模顺序：基体、减料、加料、阵列和修饰。</p>
              </div>
            )}
          </div>
          <div className="project-meta">
            <span>零件族：{partFamily || "未识别"}</span>
            <span>未决项：{unresolvedCount}</span>
            <span>项目 ID：{projectId ? projectId.slice(0, 8) : "未创建"}</span>
          </div>
        </TaskSection>
      </div>
    </aside>
  );
}

function TaskSection({
  step,
  title,
  badge,
  open,
  onToggle,
  children,
}: {
  step: string;
  title: string;
  badge?: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className={`task-section ${open ? "open" : ""}`}>
      <button type="button" className="task-section-header" aria-expanded={open} onClick={onToggle}>
        <span className="section-step">{step}</span>
        <span className="section-title">{title}</span>
        {badge && <span className="section-badge">{badge}</span>}
        <span className="section-chevron" aria-hidden="true">{open ? "收起" : "展开"}</span>
      </button>
      {open && <div className="task-section-body">{children}</div>}
    </section>
  );
}