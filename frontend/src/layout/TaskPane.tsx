import ClarificationPanel from "../ClarificationPanel";
import { artifactUrl } from "../api";
import { useAppStore, type TaskTab } from "../store";

type Props = {
  busy: boolean;
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
  runId?: string;
  engineLabel: string;
  onChatMessageChange: (value: string) => void;
  onClarificationContinue: (answers: string) => void;
  onSendChat: () => void;
};

const tabs: { id: TaskTab; label: string }[] = [
  { id: "assistant", label: "AI 助手" },
  { id: "review", label: "设计评审" },
  { id: "logs", label: "执行日志" },
  { id: "plan", label: "FeaturePlan" },
  { id: "export", label: "导出" },
];

export default function TaskPane({
  busy,
  chatMessage,
  events,
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
}: Props) {
  const rightTab = useAppStore((state) => state.ui.rightTab);
  const rightCollapsed = useAppStore((state) => state.ui.rightCollapsed);
  const setUi = useAppStore((state) => state.setUi);
  const unanswered = questions.filter((question) => question.required !== false && !question.answer).length;

  return (
    <aside className="task-pane-panel" aria-label="AI Task Pane">
      <div className="task-pane-header">
        <div>
          <span className="eyebrow">TASK PANE</span>
          <h2>AI 助手</h2>
        </div>
        <div className="manager-header-actions">
          <span className="workspace-chip">{engineLabel}</span>
          <button type="button" className="collapse-button" onClick={() => setUi({ rightCollapsed: !rightCollapsed })}>
            {rightCollapsed ? "展开" : "折叠"}
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
            {tab.label}
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
                  placeholder="例如：把中心孔改成 12mm；删除顶部槽；新增 4 个 M6 孔。"
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
              <p className="hint">自然语言修改会生成新的设计快照，可用顶部撤销 / 重做回到旧版本。</p>
            </div>
          </div>
        )}

        {rightTab === "review" && (
          <div className="task-pane-section review-pane">
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

        {rightTab === "logs" && (
          <div className="task-pane-section">
            <pre className="log-box">{events.join("\n") || "等待后端事件..."}</pre>
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
              <strong>导出产物</strong>
              <span>生成成功后，STEP、STL、OBJ 与执行报告可直接下载。</span>
            </div>
            <div className="export-list">
              <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "step")}>STEP</a>
              <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "stl")}>STL</a>
              <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "obj")}>OBJ</a>
              <a className={runId ? "" : "disabled"} href={artifactUrl(runId, "execution_report")}>执行报告</a>
            </div>
            <p className="hint">{engineLabel} 只执行校验过的 FeaturePlan，不执行任意 AI Python。</p>
          </div>
        )}
      </div>
    </aside>
  );
}

function ReviewColumn({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="review-column">
      <h3>{title}</h3>
      {items.length ? items.map((item, index) => <p key={index}>{item}</p>) : <span className="empty-note">暂无</span>}
    </section>
  );
}
