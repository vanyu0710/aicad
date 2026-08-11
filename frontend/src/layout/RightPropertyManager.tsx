import FeatureForm from "../FeatureForm";
import { useT } from "../i18n";

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
  const t = useT();
  return (
    <aside className="workspace-inspector right-manager" aria-label={t("legacy.property_manager")}>
      <section className="manager-section inspector-header">
        <div className="section-title-row">
          <div>
            <h2>{t("legacy.property_manager")}</h2>
            <p>{selectedFeature ? selectedFeature.id : t("legacy.no_feature")}</p>
          </div>
          {selectedFeature && <span className="workspace-chip">{selectedFeature.type}</span>}
        </div>
      </section>

      <section className="manager-section property-editor">
        {selectedFeature ? (
          <FeatureForm key={selectedFeature.id} feature={selectedFeature} busy={busy} onSave={onSaveFeature} />
        ) : (
          <div className="inspector-empty">
            <strong>{t("manager.no_feature")}</strong>
            <span>{t("manager.no_feature.hint")}</span>
          </div>
        )}
      </section>

      <section className="manager-section review-summary">
        <div className="section-title-row">
          <h2>{t("legacy.design_check")}</h2>
          {review?.requires_confirmation && <span className="workspace-chip danger">{t("legacy.requires_confirmation")}</span>}
        </div>
        {unresolved.length > 0 && (
          <div className="mini-list warn-list">
            {unresolved.slice(0, 4).map((item, index) => (
              <p key={`${item.feature}-${index}`}>
                <b>{item.feature}</b>: {item.reason}
              </p>
            ))}
          </div>
        )}
        {!unresolved.length && !review && <p className="empty-note">{t("legacy.review.empty")}</p>}
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