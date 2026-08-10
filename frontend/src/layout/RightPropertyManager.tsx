import FeatureForm from "../FeatureForm";

type Props = {
  busy?: boolean;
  review?: {
    warnings?: string[];
    suggestions?: string[];
    manufacturability?: string[];
    standards?: string[];
    requires_confirmation?: boolean;
  };
  selectedFeature: any;
  unresolved: { feature: string; reason: string }[];
  onSaveFeature: (payload: any) => void;
};

export default function RightPropertyManager({ busy, review, selectedFeature, unresolved, onSaveFeature }: Props) {
  return (
    <aside className="workspace-inspector right-manager" aria-label="属性管理器">
      <section className="manager-section inspector-header">
        <div className="section-title-row">
          <div>
            <h2>属性管理器</h2>
            <p>{selectedFeature ? selectedFeature.id : "未选择特征"}</p>
          </div>
          {selectedFeature && <span className="workspace-chip">{selectedFeature.type}</span>}
        </div>
      </section>

      <section className="manager-section property-editor">
        {selectedFeature ? (
          <FeatureForm key={selectedFeature.id} feature={selectedFeature} busy={busy} onSave={onSaveFeature} />
        ) : (
          <div className="inspector-empty">
            <strong>没有可编辑特征</strong>
            <span>先生成 FeaturePlan，或从左侧特征树选择一个特征。</span>
          </div>
        )}
      </section>

      <section className="manager-section review-summary">
        <div className="section-title-row">
          <h2>设计检查</h2>
          {review?.requires_confirmation && <span className="workspace-chip danger">需确认</span>}
        </div>
        {unresolved.length > 0 && (
          <div className="mini-list warn-list">
            {unresolved.slice(0, 4).map((item, index) => (
              <p key={`${item.feature}-${index}`}>
                <b>{item.feature}</b>：{item.reason}
              </p>
            ))}
          </div>
        )}
        {!unresolved.length && !review && <p className="empty-note">生成后这里会显示设计警告、制造建议和标准化提示。</p>}
        {review && (
          <div className="mini-list">
            {[...(review.warnings || []), ...(review.suggestions || []), ...(review.manufacturability || []), ...(review.standards || [])]
              .slice(0, 5)
              .map((item, index) => (
                <p key={index}>{item}</p>
              ))}
          </div>
        )}
      </section>
    </aside>
  );
}
