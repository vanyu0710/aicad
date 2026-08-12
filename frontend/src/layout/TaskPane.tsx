import ClarificationPanel from "../ClarificationPanel";
import { artifactUrl, type ProcessStep } from "../api";
import { useAppStore, type TaskTab } from "../store";
import { useT } from "../i18n";

type Props = {
  busy: boolean;
  chatMessage: string;
  events: string[];
  processSteps: ProcessStep[];
  featurePlan: any;
  questions: any[];
  reportMarkdown?: string;
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
  onChatMessageChange: (value: string) => void;
  onClarificationContinue: (answers: string) => void;
  onSendChat: () => void;
  onSelectProcessStep?: (featureId: string) => void;
  onClose?: () => void;
};

const tabs: { id: TaskTab; labelKey: string }[] = [
  { id: "assistant", labelKey: "task.assistant" },
  { id: "review", labelKey: "task.review" },
  { id: "process", labelKey: "task.process" },
  { id: "logs", labelKey: "task.logs" },
  { id: "plan", labelKey: "task.plan" },
  { id: "export", labelKey: "task.export" },
];

export default function TaskPane({
  busy,
  chatMessage,
  events,
  processSteps,
  featurePlan,
  questions,
  reportMarkdown,
  review,
  unresolved,
  runId,
  engineLabel,
  onChatMessageChange,
  onClarificationContinue,
  onSendChat,
  onSelectProcessStep,
  onClose,
}: Props) {
  const t = useT();
  const rightTab = useAppStore((state) => state.ui.rightTab);
  const setUi = useAppStore((state) => state.setUi);
  const unanswered = questions.filter((question) => question.required !== false && !question.answer).length;

  return (
    <aside className="task-pane-panel" aria-label="AI Task Pane">
      <div className="task-pane-header">
        <div>
          <span className="eyebrow">TASK PANE</span>
          <h2>{t("task.assistant")}</h2>
        </div>
        <div className="manager-header-actions">
          <span className="workspace-chip">{engineLabel}</span>
          <button type="button" className="collapse-button drawer-close-button" title={t("manager.close.title")} onClick={() => onClose?.()}>
            {t("manager.close")}
          </button>
        </div>
      </div>

      <div className="task-tabs" role="tablist">
        {tabs.map((tab) => (
          <button
            type="button"
            key={tab.id}
            role="tab"
            aria-selected={rightTab === tab.id}
            className={rightTab === tab.id ? "task-tab active" : "task-tab"}
            onClick={() => setUi({ rightTab: tab.id })}
          >
            {t(tab.labelKey)}
            {tab.id === "assistant" && unanswered > 0 && <span>{unanswered}</span>}
            {tab.id === "review" && unresolved.length > 0 && <span>{unresolved.length}</span>}
          </button>
        ))}
      </div>

      <div className="task-pane-content">
        {rightTab === "assistant" && (
          <div className="task-pane-section">
            {questions.length > 0 && (
              <ClarificationPanel questions={questions} onContinue={onClarificationContinue} disabled={busy} />
            )}
            <div className="chat-box">
              <div className="chat-row">
                <input
                  value={chatMessage}
                  onChange={(event) => onChatMessageChange(event.target.value)}
                  placeholder={t("task.chat.placeholder")}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      onSendChat();
                    }
                  }}
                />
                <button type="button" onClick={onSendChat} disabled={busy || !chatMessage.trim()}>
                  {t("task.chat.send")}
                </button>
              </div>
              <p className="hint">{t("task.chat.hint")}</p>
            </div>
          </div>
        )}

        {rightTab === "review" && (
          <div className="task-pane-section review-pane">
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
            {featurePlan?.self_checks?.checks?.length > 0 && (
              <div className="unresolved-box self-check-box">
                <strong>{t("task.review.self_checks")}</strong>
                <ul className="self-check-list">
                  {featurePlan.self_checks.checks.map((check: any, index: number) => (
                    <li key={index} className={`self-check-item ${check.status}`}>
                      <span className="self-check-status">{t(`task.review.check.${check.status}`)}</span>
                      <span>{check.message}</span>
                    </li>
                  ))}
                </ul>
                {featurePlan.self_checks.order?.length > 0 && (
                  <small className="self-check-order">{t("task.review.self_order", { items: featurePlan.self_checks.order.join(" → ") })}</small>
                )}
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
          </div>
        )}

        {rightTab === "process" && (
          <div className="task-pane-section process-pane">
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
                          {step.changed && (
                            <details className="process-changed">
                              <summary>{t("process.changed")}</summary>
                              <pre>{JSON.stringify(step.changed, null, 2)}</pre>
                            </details>
                          )}
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

        {rightTab === "logs" && (
          <div className="task-pane-section">
            <pre className="log-box">{events.join("\n") || t("task.logs.waiting")}</pre>
          </div>
        )}

        {rightTab === "plan" && (
          <div className="task-pane-section plan-pane">
            {reportMarkdown && <pre className="report-box">{reportMarkdown}</pre>}
            <textarea
              className="code-box"
              readOnly
              value={featurePlan ? JSON.stringify(featurePlan, null, 2) : ""}
              rows={16}
            />
          </div>
        )}

        {rightTab === "export" && (
          <div className="task-pane-section export-pane">
            <div className="export-summary">
              <strong>{t("task.export.title")}</strong>
              <span>{t("task.export.hint")}</span>
            </div>
            <div className="export-list">
              <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "step")}>STEP</a>
              <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "stl")}>STL</a>
              <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "obj")}>OBJ</a>
              <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "execution_report")}>{t("app.artifact.report")}</a>
            </div>
            <p className="hint">{t("task.export.safe", { engine: engineLabel })}</p>
          </div>
        )}
      </div>
    </aside>
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
