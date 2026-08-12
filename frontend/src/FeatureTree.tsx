import { useMemo, useState } from "react";
import { useT } from "./i18n";

type FeatureTreeProps = {
  features: any[];
  selectedFeatureId: string;
  onSelectFeature: (featureId: string) => void;
  evidenceCount?: number;
  intentSummary?: string;
  completenessScore?: number;
};

type GroupId = "base" | "remove" | "add" | "pattern" | "modify" | "other";

const REMOVE_TYPES = new Set(["through_hole", "blind_hole", "counterbore_hole", "rectangular_slot", "rectangular_pocket", "annular_groove", "internal_annular_groove"]);
const ADD_TYPES = new Set(["boss_cylinder", "rectangular_pad", "rib_box"]);
const PATTERN_TYPES = new Set(["linear_pattern", "circular_pattern"]);
const MODIFY_TYPES = new Set(["fillet", "chamfer"]);

function groupFor(feature: any): GroupId {
  if (feature?.operation === "base" || feature?.type === "box_base" || feature?.type === "cylinder_base" || feature?.type === "hollow_cylinder" || feature?.type === "link_plate") {
    return "base";
  }
  if (feature?.operation === "remove" || REMOVE_TYPES.has(feature?.type)) return "remove";
  if (feature?.operation === "pattern" || PATTERN_TYPES.has(feature?.type)) return "pattern";
  if (feature?.operation === "modify" || MODIFY_TYPES.has(feature?.type)) return "modify";
  if (feature?.operation === "add" || ADD_TYPES.has(feature?.type)) return "add";
  return "other";
}

function dependencyDepth(feature: any, features: any[]): number {
  const deps = feature?.depends_on || [];
  if (!deps.length) return 0;
  if (deps.includes("base") || deps.some((dep: string) => features.some((item) => item.id === dep && item.operation === "base"))) {
    return 1;
  }
  return 2;
}

function missingCount(feature: any): number {
  const unresolved = feature?.unresolved?.length || 0;
  const nullDims = Object.values(feature?.dimensions || {}).filter((dim: any) => dim?.value == null).length;
  return unresolved + nullDims;
}

function hasAssumption(feature: any): boolean {
  if ((feature?.assumptions || []).length > 0) return true;
  return Object.values(feature?.dimensions || {}).some((dim: any) => dim?.source === "assumption" || dim?.source === "derived");
}

const GROUP_IDS: GroupId[] = ["base", "remove", "add", "pattern", "modify", "other"];

export default function FeatureTree({ features, selectedFeatureId, onSelectFeature, evidenceCount, intentSummary, completenessScore }: FeatureTreeProps) {
  const t = useT();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const grouped = useMemo(() => {
    const result: Record<GroupId, any[]> = { base: [], remove: [], add: [], pattern: [], modify: [], other: [] };
    for (const feature of features || []) {
      result[groupFor(feature)].push(feature);
    }
    return result;
  }, [features]);

  const counts = useMemo(() => {
    let modeled = 0;
    let skipped = 0;
    let failed = 0;
    let unresolved = 0;
    let assumptions = 0;
    for (const feature of features || []) {
      if (feature.execution_status === "modeled") modeled += 1;
      else if (feature.execution_status === "skipped") skipped += 1;
      else if (feature.execution_status === "failed") failed += 1;
      unresolved += missingCount(feature);
      if (hasAssumption(feature)) assumptions += 1;
    }
    return { modeled, skipped, failed, unresolved, assumptions };
  }, [features]);

  const statusText = (feature: any) => {
    const status = feature.execution_status || "unresolved";
    return t(`tree.status.${status}`);
  };

  const toggleGroup = (groupId: GroupId) => setCollapsed((current) => ({ ...current, [groupId]: !current[groupId] }));

  return (
    <div className="feature-tree">
      {(evidenceCount != null || intentSummary || completenessScore != null) && (
        <div className="tree-pipeline-strip">
          {evidenceCount != null && <span>{t("tree.pipeline.evidence", { count: evidenceCount })}</span>}
          {intentSummary && <span className="tree-pipeline-intent">{t("tree.pipeline.intent", { value: intentSummary })}</span>}
          {completenessScore != null && <span>{t("tree.pipeline.acceptance", { score: completenessScore })}</span>}
        </div>
      )}
      {GROUP_IDS.map((groupId) => {
        const items = grouped[groupId];
        if (!items.length) return null;
        const isCollapsed = Boolean(collapsed[groupId]);
        return (
          <section className="feature-tree-group" key={groupId}>
            <button type="button" className="feature-tree-group-header" onClick={() => toggleGroup(groupId)}>
              <span className="feature-tree-group-toggle">{isCollapsed ? "▸" : "▾"}</span>
              <span className="feature-tree-group-name">{t(`tree.group.${groupId}`)}</span>
              <span className="feature-tree-group-count">{items.length}</span>
            </button>
            {!isCollapsed && (
              <div className="feature-tree-group-body">
                {items.map((feature) => {
                  const isSelected = feature.id === selectedFeatureId;
                  const globalIndex = features.findIndex((item) => item.id === feature.id);
                  const depth = dependencyDepth(feature, features);
                  const status = feature.execution_status || "unresolved";
                  const missing = missingCount(feature);
                  const assumption = hasAssumption(feature);
                  return (
                    <button
                      type="button"
                      key={feature.id}
                      className={isSelected ? "feature-node selected" : "feature-node"}
                      style={{ paddingLeft: `${10 + depth * 12}px` }}
                      onClick={() => onSelectFeature(feature.id)}
                    >
                      <span className="feature-index">{globalIndex === 0 ? t("manager.base") : `F${globalIndex}`}</span>
                      <span className="feature-main">
                        <span className="feature-name">{feature.id}</span>
                        <span className="feature-type">{feature.type}</span>
                      </span>
                      <span className={`feature-state ${status}`}>
                        {statusText(feature)}
                      </span>
                      <span className="feature-node-badges">
                        {missing > 0 && <span className="badge badge-warn" title={t("tree.node.missing", { count: missing })}>{missing}</span>}
                        {assumption && <span className="badge badge-assumption" title={t("tree.node.assumption")}>~</span>}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </section>
        );
      })}
      {!features.length && (
        <div className="empty-tree">
          <strong>{t("manager.empty_tree")}</strong>
          <p>{t("manager.empty_tree.hint")}</p>
        </div>
      )}
      <div className="feature-tree-summary">
        <span className="summary-chip summary-modeled">{t("tree.summary.modeled", { count: counts.modeled })}</span>
        <span className="summary-chip summary-skipped">{t("tree.summary.skipped", { count: counts.skipped })}</span>
        <span className="summary-chip summary-failed">{t("tree.summary.failed", { count: counts.failed })}</span>
        <span className="summary-chip summary-unresolved">{t("tree.summary.unresolved", { count: counts.unresolved })}</span>
        <span className="summary-chip summary-assumptions">{t("tree.summary.assumptions", { count: counts.assumptions })}</span>
      </div>
    </div>
  );
}
