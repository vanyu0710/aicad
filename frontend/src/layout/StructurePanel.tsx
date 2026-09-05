import { useState } from "react";
import KernelFeatureForm from "../KernelFeatureForm";
import KernelFeatureTree from "../KernelFeatureTree";
import {
  artifactUrl,
  type DesignIntentDetails,
  type EvidenceItem,
  type ExecutionReport,
  type KernelFeatureData,
  type KernelFeatureTree as KernelFeatureTreeType,
  type ProcessStep,
} from "../api";
import { useT } from "../i18n";

type Tab = "tree" | "property" | "review" | "process" | "export";

type Props = {
  busy: boolean;
  kernelTree: KernelFeatureTreeType;
  kernelSelectedFeature: KernelFeatureData | null;
  selectedFeatureId: string;
  partFamily?: string;
  modeLabel: string;
  processSteps: ProcessStep[];
  featurePlan: any;
  reportMarkdown?: string;
  executionReport?: ExecutionReport;
  evidence?: EvidenceItem[];
  evidenceConflicts?: string[];
  designIntent?: DesignIntentDetails;
  review?: {
    warnings?: string[];
    suggestions?: string[];
    manufacturability?: string[];
    standards?: string[];
    blocking?: string[];
    requires_confirmation?: boolean;
  };
  unresolved: { feature: string; reason: string }[];
  runId?: string;
  engineLabel: string;
  onSelectKernelFeature: (featureId: string) => void;
  onSaveKernelFeature: (featureId: string, newParams: Record<string, unknown>) => void;
  onDeleteKernelFeature: (featureId: string) => void;
  onSelectProcessStep?: (featureId: string) => void;
};

export default function StructurePanel(props: Props) {
  const t = useT();
  const [tab, setTab] = useState<Tab>("tree");
  const [collapsed, setCollapsed] = useState(false);
  const {
    busy,
    kernelTree,
    kernelSelectedFeature,
    selectedFeatureId,
    partFamily,
    modeLabel,
    processSteps,
    featurePlan,
    executionReport,
    evidence,
    evidenceConflicts,
    designIntent,
    review,
    unresolved,
    runId,
    engineLabel,
    onSelectKernelFeature,
    onSaveKernelFeature,
    onDeleteKernelFeature,
    onSelectProcessStep,
  } = props;

  const tabs: { id: Tab; labelKey: string }[] = [
    { id: "tree", labelKey: "manager.feature_tree" },
    { id: "property", labelKey: "manager.property" },
    { id: "review", labelKey: "task.review" },
    { id: "process", labelKey: "task.process" },
    { id: "export", labelKey: "task.export" },
  ];

  return (
    <section className={`structure-panel${collapsed ? " collapsed" : ""}`} aria-label={t("manager.feature_tree")}>
      <div className="structure-tabs" role="tablist">
        {tabs.map((item) => (
          <button
            type="button"
            key={item.id}
            role="tab"
            aria-selected={tab === item.id}
            className={tab === item.id ? "structure-tab active" : "structure-tab"}
            onClick={() => {
              setTab(item.id);
              setCollapsed(false);
            }}
          >
            {t(item.labelKey)}
          </button>
        ))}
        <button
          type="button"
          className="structure-collapse"
          title={collapsed ? t("structure.expand") : t("structure.collapse")}
          onClick={() => setCollapsed((v) => !v)}
        >
          {collapsed ? "▴" : "▾"}
        </button>
      </div>

      {!collapsed && (
        <div className="structure-body">
          {tab === "tree" && (
            <div className="structure-tree">
              <div className="structure-tree-head">
                <span>{t("manager.feature_count", { count: kernelTree.op_history.length })}</span>
                <span>{t("manager.part_family", { value: partFamily || t("manager.unrecognized") })} · {modeLabel}</span>
              </div>
              <KernelFeatureTree
                opHistory={kernelTree.op_history}
                nodes={kernelTree.graph?.nodes ?? {}}
                selectedFeatureId={selectedFeatureId}
                onSelectFeature={onSelectKernelFeature}
                onDeleteFeature={onDeleteKernelFeature}
              />
            </div>
          )}

          {tab === "property" && (
            <div className="structure-property">
              {kernelSelectedFeature ? (
                <KernelFeatureForm
                  key={kernelSelectedFeature.id}
                  feature={kernelSelectedFeature}
                  busy={busy}
                  onSave={onSaveKernelFeature}
                  onDelete={onDeleteKernelFeature}
                />
              ) : (
                <div className="inspector-empty">
                  <strong>{t("manager.no_feature")}</strong>
                  <span>{t("manager.no_feature.hint")}</span>
                </div>
              )}
            </div>
          )}

          {tab === "review" && (
            <div className="structure-review">
              {executionReport && (
                <div className="execution-report-box">
                  <strong>{t("task.review.execution")}</strong>
                  <div className="execution-grid">
                    <ReportChip ok={executionReport.execution_ok} label={t("task.review.execution_ok")} />
                    <ReportChip ok={executionReport.plan_complete} label={t("task.review.plan_complete")} />
                    <ReportChip ok={executionReport.geometry_valid} label={t("task.review.geometry_valid")} />
                    <ReportChip ok={executionReport.production_ready} label={t("task.review.production_ready")} />
                  </div>
                  <p className="execution-score">{t("task.review.score", { score: executionReport.completeness_score })}</p>
                  <p className="execution-meta">
                    {t("task.review.engine", { engine: executionReport.engine })}
                    {executionReport.fallback_used ? ` · ${t("task.review.fallback_used")}` : ""}
                    {` · ${t("task.review.assumption_count", { count: executionReport.assumption_count })}`}
                  </p>
                  {(executionReport.skipped_features?.length || 0) > 0 || (executionReport.failed_features?.length || 0) > 0 ? (
                    <ul className="execution-issues">
                      {executionReport.skipped_features.map((id) => <li key={`skipped-${id}`}>{t("task.review.skipped", { id })}</li>)}
                      {executionReport.failed_features.map((id) => <li key={`failed-${id}`}>{t("task.review.failed", { id })}</li>)}
                    </ul>
                  ) : null}
                  {executionReport.details?.map((detail, index) => <p className="execution-detail" key={index}>{detail}</p>)}
                </div>
              )}
              {designIntent && (
                <div className="intent-box">
                  <strong>{t("task.review.intent")}</strong>
                  <p>{designIntent.summary || designIntent.part_family || ""}</p>
                  {designIntent.function && <span>{t("task.review.intent_function", { value: designIntent.function })}</span>}
                  {designIntent.manufacturing_intent && <span>{t("task.review.intent_manufacturing", { value: designIntent.manufacturing_intent })}</span>}
                  {(designIntent.unsupported_requirements?.length || 0) > 0 && (
                    <ul className="intent-unsupported">
                      {designIntent.unsupported_requirements?.map((item, index) => <li key={index}>{t("task.review.unsupported", { value: item })}</li>)}
                    </ul>
                  )}
                </div>
              )}
              {!review && !unresolved.length && <p className="empty-note">{t("task.review.empty")}</p>}
              {unresolved.length > 0 && (
                <div className="unresolved-box">
                  <strong>{t("task.review.blockers")}</strong>
                  <ul>
                    {unresolved.map((item, index) => (
                      <li key={`${item.feature}-${index}`}>{`${item.feature}: ${item.reason}`}</li>
                    ))}
                  </ul>
                </div>
              )}
              {featurePlan?.assumptions?.length > 0 && (
                <div className="unresolved-box assumption-box">
                  <strong>{t("task.review.assumptions")}</strong>
                  <ul>
                    {featurePlan.assumptions.map((item: string, index: number) => <li key={index}>{item}</li>)}
                  </ul>
                </div>
              )}
              {review && (
                <div className="review-grid">
                  <ReviewColumn title={t("task.review.blocking")} items={review.blocking || []} />
                  <ReviewColumn title={t("task.review.warnings")} items={review.warnings || []} />
                  <ReviewColumn title={t("task.review.suggestions")} items={review.suggestions || []} />
                  <ReviewColumn title={t("task.review.manufacturability")} items={review.manufacturability || []} />
                  <ReviewColumn title={t("task.review.standards")} items={review.standards || []} />
                </div>
              )}
              {(evidence?.length || 0) > 0 && (
                <div className="evidence-strip">
                  <strong>{t("task.review.evidence")}</strong>
                  <div className="evidence-items">
                    {evidence!.map((item, index) => (
                      <span key={`${item.key}-${index}`} className={`evidence-item${item.source === "assumption" ? " assumption" : ""}`}>
                        <b>{item.key}</b>{item.value}{item.unit ? ` ${item.unit}` : ""}
                      </span>
                    ))}
                  </div>
                  {(evidenceConflicts?.length || 0) > 0 && evidenceConflicts!.map((c, i) => (
                    <span key={`conflict-${i}`} className="evidence-conflict">{c}</span>
                  ))}
                </div>
              )}
            </div>
          )}

          {tab === "process" && (
            <div className="structure-process">
              {processSteps.length ? (
                <div className="process-timeline">
                  {Array.from(new Set(processSteps.map((step) => step.stage))).map((stage) => (
                    <section className="process-stage-group" key={stage}>
                      <h4>{t(`process.stage.${stage}`)}</h4>
                      {processSteps
                        .filter((step) => step.stage === stage)
                        .map((step) => (
                          <button
                            type="button"
                            className={`process-step ${step.status}`}
                            key={step.id}
                            disabled={!step.feature_id}
                            onClick={() => step.feature_id && onSelectProcessStep?.(step.feature_id)}
                          >
                            <span className="process-step-row">
                              <span className="process-status">{t(`process.status.${step.status}`)}</span>
                              <span className="process-label">{step.label}</span>
                              {step.operation && <span className="process-operation">{step.operation}</span>}
                            </span>
                            {step.summary && <span className="process-summary">{step.summary}</span>}
                            {step.detail && <span className="process-detail">{step.detail}</span>}
                            {step.error && <span className="process-error">{step.error}</span>}
                          </button>
                        ))}
                    </section>
                  ))}
                </div>
              ) : (
                <p className="empty-note">{t("task.process.empty")}</p>
              )}
            </div>
          )}

          {tab === "export" && (
            <div className="structure-export">
              <div className="export-summary">
                <strong>{t("task.export.title")}</strong>
                <span>{t("task.export.hint")}</span>
              </div>
              <div className="export-list">
                <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "step")}>STEP</a>
                <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "stl")}>STL</a>
                <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "execution_report")}>{t("app.artifact.report")}</a>
              </div>
              <p className="hint">{t("task.export.safe", { engine: engineLabel })}</p>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function ReportChip({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className={`report-chip ${ok ? "ok" : "not-ok"}`}>
      <i className="report-dot" />
      {label}
    </span>
  );
}

function ReviewColumn({ title, items }: { title: string; items: string[] }) {
  const t = useT();
  return (
    <section className="review-column">
      <h3>{title}</h3>
      {items.length ? items.map((item, index) => <p key={index}>{item}</p>) : <span className="empty-note">{t("task.empty")}</span>}
    </section>
  );
}
