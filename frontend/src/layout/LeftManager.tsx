import { useState } from "react";
import FeatureForm from "../FeatureForm";
import { useAppStore, type ManagerTab } from "../store";

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
  onDescriptionChange: (value: string) => void;
  onImageChange: (file: File | null) => void;
  onSelectFeature: (featureId: string) => void;
  onSaveFeature: (payload: any) => void;
  onOpenSettings: () => void;
};

const tabs: { id: ManagerTab; label: string }[] = [
  { id: "feature", label: "特征树" },
  { id: "property", label: "属性" },
  { id: "configuration", label: "配置" },
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
  onDescriptionChange,
  onImageChange,
  onSelectFeature,
  onSaveFeature,
  onOpenSettings,
}: Props) {
  const leftTab = useAppStore((state) => state.ui.leftTab);
  const leftCollapsed = useAppStore((state) => state.ui.leftCollapsed);
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
          <button type="button" className="collapse-button" onClick={() => setUi({ leftCollapsed: !leftCollapsed })}>
            {leftCollapsed ? "展开" : "折叠"}
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
            {tab.label}
          </button>
        ))}
      </div>

      <div className="manager-pane-body">
        {leftTab === "feature" && (
          <div className="manager-tab-page">
            <section className="input-section">
              <button type="button" className="input-toggle" onClick={() => setInputOpen((open) => !open)}>
                <span>项目输入</span>
                <span>{inputOpen ? "收起" : "展开"}</span>
              </button>
              {inputOpen && (
                <div className="input-body">
                  <label className="field">
                    <span className="field-label">
                      草图图片
                      <small>支持 PNG / JPG，先本地预处理再交给视觉模型。</small>
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
                        移除
                      </button>
                    </div>
                  )}
                  <label className="field">
                    <span className="field-label">
                      零件功能与已知尺寸
                      <small>写用途、材料、配合关系、标注尺寸；未知项交给系统提问。</small>
                    </span>
                    <textarea
                      value={description}
                      onChange={(event) => onDescriptionChange(event.target.value)}
                      rows={6}
                      placeholder="例如：铝制套筒，外径 50mm，内径 30mm，长度 80mm，顶部有环槽。"
                    />
                  </label>
                </div>
              )}
            </section>

            <section className="feature-manager-section">
              <div className="manager-section-title">
                <h3>FeatureManager</h3>
                <span>{features.length} 项</span>
              </div>
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
                      <span className={unresolved ? "feature-state warn" : feature.confirmed_by_user ? "feature-state ok" : "feature-state"}>
                        {unresolved ? "待确认" : feature.confirmed_by_user ? "已确认" : "草案"}
                      </span>
                    </button>
                  );
                })}
                {!features.length && (
                  <div className="empty-tree">
                    <strong>尚未生成特征树</strong>
                    <p>生成后这里会显示基体、减料、加料、阵列和修饰的建模顺序。</p>
                  </div>
                )}
              </div>
              <div className="project-meta">
                <span>零件族：{partFamily || "未识别"}</span>
                <span>未决项：{unresolvedCount}</span>
                <span>模式：{modeLabel}</span>
              </div>
            </section>
          </div>
        )}

        {leftTab === "property" && (
          <div className="manager-tab-page">
            <div className="manager-section-title">
              <h3>PropertyManager</h3>
              <span>{selectedFeature ? selectedFeature.type : "未选择"}</span>
            </div>
            {selectedFeature ? (
              <FeatureForm key={selectedFeature.id} feature={selectedFeature} busy={busy} onSave={onSaveFeature} />
            ) : (
              <div className="inspector-empty">
                <strong>没有可编辑特征</strong>
                <span>先生成 FeaturePlan，或从特征树选择一个特征。</span>
              </div>
            )}
          </div>
        )}

        {leftTab === "configuration" && (
          <div className="manager-tab-page">
            <div className="manager-section-title">
              <h3>ConfigurationManager</h3>
              <span>项目与模型</span>
            </div>
            <div className="config-summary">
              <div className="config-summary-item">
                <span>工作模式</span>
                <strong>{modeLabel}</strong>
              </div>
              <div className="config-summary-item">
                <span>零件族</span>
                <strong>{partFamily || "未识别"}</strong>
              </div>
              <div className="config-summary-item">
                <span>CAD 引擎</span>
                <strong>Build123d Worker</strong>
              </div>
            </div>
            <div className="configuration-actions">
              <p>
                视觉读图模型与建模规划模型、协议、Base URL、API Key、智能策略都在设置中心管理，避免工作台被底层配置占据。
              </p>
              <button type="button" className="primary" onClick={onOpenSettings}>
                打开设置中心
              </button>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
