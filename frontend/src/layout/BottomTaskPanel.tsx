import ClarificationPanel from "../ClarificationPanel";
import { useT } from "../i18n";

export type TaskTab = "questions" | "chat" | "review" | "logs" | "plan";

type Props = {
  activeTab: TaskTab;
  busy?: boolean;
  chatMessage: string;
  events: string[];
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
  onChatMessageChange: (value: string) => void;
  onClarificationContinue: (answers: string) => void;
  onSendChat: () => void;
  onTabChange: (tab: TaskTab) => void;
};

const tabs: { id: TaskTab; labelKey: string }[] = [
  { id: "questions", labelKey: "legacy.pending" },
  { id: "chat", labelKey: "legacy.chat" },
  { id: "review", labelKey: "task.review" },
  { id: "logs", labelKey: "task.logs" },
  { id: "plan", labelKey: "task.plan" },
];

export default function BottomTaskPanel({
  activeTab,
  busy,
  chatMessage,
  events,
  featurePlan,
  questions,
  reportMarkdown,
  review,
  unresolved,
  onChatMessageChange,
  onClarificationContinue,
  onSendChat,
  onTabChange,
}: Props) {
  const t = useT();
  const unanswered = questions.filter((question) => question.required !== false && !question.answer).length;

  return (
    <section className="workspace-bottom task-dock" aria-label={t("legacy.task_pane")}>
      <div className="task-tabs" role="tablist">
        {tabs.map((tab) => (
          <button
            type="button"
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            className={activeTab === tab.id ? "task-tab active" : "task-tab"}
            onClick={() => onTabChange(tab.id)}
          >
            {t(tab.labelKey)}
            {tab.id === "questions" && unanswered > 0 && <span>{unanswered}</span>}
            {tab.id === "review" && unresolved.length > 0 && <span>{unresolved.length}</span>}
          </button>
        ))}
      </div>

      <div className="task-content">
        {activeTab === "questions" && (
          <div className="task-pane">
            <ClarificationPanel questions={questions} onContinue={onClarificationContinue} disabled={busy} />
            {unresolved.length > 0 && (
              <div className="unresolved-box">
                <strong>{t("legacy.blockers")}</strong>
                <ul>
                  {unresolved.map((item, index) => (
                    <li key={`${item.feature}-${index}`}>
                      <b>{item.feature}</b>：{item.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {activeTab === "chat" && (
          <div className="task-pane chat-pane">
            <div className="chat-row">
              <input
                value={chatMessage}
                onChange={(event) => onChatMessageChange(event.target.value)}
                placeholder={t("legacy.chat.placeholder")}
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
            <p className="hint">{t("legacy.chat.hint")}</p>
          </div>
        )}

        {activeTab === "review" && (
          <div className="task-pane review-pane">
            {!review && !unresolved.length && <p className="empty-note">{t("task.review.empty")}</p>}
            {unresolved.length > 0 && (
              <div className="unresolved-box">
                <strong>{t("task.review.blockers")}</strong>
                <ul>
                  {unresolved.map((item, index) => (
                    <li key={`${item.feature}-${index}`}>{`${item.feature}：${item.reason}`}</li>
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
          </div>
        )}

        {activeTab === "logs" && (
          <div className="task-pane">
            <pre className="log-box">{events.join("\n") || t("task.logs.waiting")}</pre>
          </div>
        )}

        {activeTab === "plan" && (
          <div className="task-pane plan-pane">
            {reportMarkdown && <pre className="report-box">{reportMarkdown}</pre>}
            <textarea className="code-box" readOnly value={featurePlan ? JSON.stringify(featurePlan, null, 2) : ""} rows={12} />
          </div>
        )}
      </div>
    </section>
  );
}

function ReviewColumn({ title, items }: { title: string; items: string[] }) {
  const t = useT();
  return (
    <section className="review-column">
      <h3>{title}</h3>
      {items.length ? (
        items.map((item, index) => <p key={index}>{item}</p>)
      ) : (
        <span className="empty-note">{t("task.empty")}</span>
      )}
    </section>
  );
}