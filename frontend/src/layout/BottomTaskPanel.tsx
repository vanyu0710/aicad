import ClarificationPanel from "../ClarificationPanel";

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

const tabs: { id: TaskTab; label: string }[] = [
  { id: "questions", label: "待确认" },
  { id: "chat", label: "AI 修改" },
  { id: "review", label: "设计评审" },
  { id: "logs", label: "执行日志" },
  { id: "plan", label: "FeaturePlan" },
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
  const unanswered = questions.filter((question) => question.required !== false && !question.answer).length;

  return (
    <section className="workspace-bottom task-dock" aria-label="任务面板">
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
            {tab.label}
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
                <strong>当前阻塞原因</strong>
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
                placeholder="例如：把中心孔改成 12mm；删除顶部槽；新增 4 个 M6 孔，分布在半径 30mm 的圆上。"
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    onSendChat();
                  }
                }}
              />
              <button type="button" onClick={onSendChat} disabled={busy || !chatMessage.trim()}>
                发送
              </button>
            </div>
            <p className="hint">AI 修改会生成新的设计快照；可以用顶部撤销 / 重做回到旧版本。</p>
          </div>
        )}

        {activeTab === "review" && (
          <div className="task-pane review-pane">
            {!review && !unresolved.length && <p className="empty-note">生成后这里会显示制造性、标准化和风险提示。</p>}
            {unresolved.length > 0 && (
              <div className="unresolved-box">
                <strong>必须先解决</strong>
                <ul>
                  {unresolved.map((item, index) => (
                    <li key={`${item.feature}-${index}`}>{`${item.feature}：${item.reason}`}</li>
                  ))}
                </ul>
              </div>
            )}
            {featurePlan?.assumptions?.length > 0 && (
              <div className="unresolved-box assumption-box">
                <strong>AI 设计假设</strong>
                <ul>
                  {featurePlan.assumptions.map((item: string, index: number) => <li key={index}>{item}</li>)}
                </ul>
              </div>
            )}
            {review && (
              <div className="review-grid">
                <ReviewColumn title="阻塞项" items={review.blocking || []} />
                <ReviewColumn title="警告" items={review.warnings || []} />
                <ReviewColumn title="建议" items={review.suggestions || []} />
                <ReviewColumn title="可制造性" items={review.manufacturability || []} />
                <ReviewColumn title="标准" items={review.standards || []} />
              </div>
            )}
          </div>
        )}

        {activeTab === "logs" && (
          <div className="task-pane">
            <pre className="log-box">{events.join("\n") || "等待后端事件..."}</pre>
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
  return (
    <section className="review-column">
      <h3>{title}</h3>
      {items.length ? (
        items.map((item, index) => <p key={index}>{item}</p>)
      ) : (
        <span className="empty-note">暂无</span>
      )}
    </section>
  );
}
