import { useEffect, useRef, type RefObject } from "react";
import ApprovalPanel from "../ApprovalPanel";
import ClarificationPanel from "../ClarificationPanel";
import { API_ROOT as apiRoot, type Approval } from "../api";
import { useAppStore, type ChatEntry } from "../store";
import { useT } from "../i18n";

type Props = {
  chat: ChatEntry[];
  chatMessage: string;
  busy: boolean;
  engineLabel: string;
  pendingApprovals?: Approval[];
  questions: any[];
  imageFile: File | null;
  onResolveApproval?: (approval: Approval, action: "approve" | "reject" | "edit", argsOverride?: Record<string, unknown>) => void;
  onClarificationContinue: (answers: string) => void;
  onChatMessageChange: (value: string) => void;
  onSendChat: () => void;
  onImageChange: (file: File | null) => void;
  inputRef?: RefObject<HTMLInputElement>;
};

export default function ChatColumn({
  chat,
  chatMessage,
  busy,
  engineLabel,
  pendingApprovals,
  questions,
  imageFile,
  onResolveApproval,
  onClarificationContinue,
  onChatMessageChange,
  onSendChat,
  onImageChange,
  inputRef,
}: Props) {
  const t = useT();
  const agentRunning = useAppStore((state) => state.agentRunning);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const streamEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    streamEndRef.current?.scrollIntoView?.({ block: "end" });
  }, [chat]);

  return (
    <aside className="chat-column" aria-label={t("task.assistant")}>
      <div className="chat-column-header">
        <span className="eyebrow">AGENT</span>
        <strong>{t("task.assistant")}</strong>
        <span className="workspace-chip">{engineLabel}</span>
      </div>

      {pendingApprovals && pendingApprovals.length > 0 && onResolveApproval && (
        <div className="chat-column-approvals">
          <ApprovalPanel approvals={pendingApprovals} busy={busy} onResolve={onResolveApproval} />
        </div>
      )}
      {questions.length > 0 && (
        <div className="chat-column-approvals">
          <ClarificationPanel questions={questions} onContinue={onClarificationContinue} disabled={busy} />
        </div>
      )}

      <div className="chat-stream">
        {chat.length === 0 && <p className="empty-note">{t("task.chat.empty")}</p>}
        {chat.map((entry) => (
          <div key={entry.id} className={`chat-entry ${entry.role}`}>
            {entry.role === "user" ? (
              <div className="chat-bubble user">
                {entry.hasImage && <span className="chat-image-chip">{t("task.chat.image_attached")}</span>}
                <span className="chat-text">{entry.text}</span>
              </div>
            ) : (
              <div className="chat-bubble assistant">
                {entry.text && <span className="chat-text">{entry.text}</span>}
                {entry.status === "streaming" && <span className="chat-caret" aria-hidden="true" />}
                {entry.tools.map((card, index) => (
                  <div
                    key={`${card.step}-${index}`}
                    className={`chat-tool-card${card.success === false ? " failed" : ""}${card.autofix ? " autofix" : ""}`}
                  >
                    <span className="chat-tool-op">{card.autofix ? `${card.op} ·fix` : card.op}</span>
                    {card.argsPreview && <code className="chat-tool-args">{card.argsPreview}</code>}
                    {(card.summary || card.message) && (
                      <span className="chat-tool-summary">{card.summary || card.message}</span>
                    )}
                  </div>
                ))}
                {entry.snapshots.map((url, index) => (
                  <a key={`snap-${index}`} className="chat-snapshot-link" href={apiRoot + url} target="_blank" rel="noreferrer">
                    <img className="chat-snapshot" src={apiRoot + url} alt={t("task.chat.snapshot")} loading="lazy" />
                  </a>
                ))}
              </div>
            )}
          </div>
        ))}
        <div ref={streamEndRef} />
      </div>

      <div className="chat-box">
        {imageFile && (
          <div className="chat-attach-bar">
            <span className="chat-image-chip">{t("chat.attached", { name: imageFile.name })}</span>
            <button type="button" className="chat-attach-remove" onClick={() => onImageChange(null)}>
              {t("chat.remove_image")}
            </button>
          </div>
        )}
        <div className="chat-row">
          <span className="chat-prompt" aria-hidden="true">❯</span>
          <input
            ref={inputRef}
            className="chat-input"
            value={chatMessage}
            onChange={(event) => onChatMessageChange(event.target.value)}
            placeholder={t("task.chat.placeholder")}
            aria-label={t("task.chat.placeholder")}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                onSendChat();
              }
            }}
          />
          <button
            type="button"
            className="chat-attach"
            title={t("chat.attach.title")}
            onClick={() => fileInputRef.current?.click()}
          >
            ＋
          </button>
          <button type="button" onClick={onSendChat} disabled={!chatMessage.trim()}>
            {t("task.chat.send")}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg"
            style={{ display: "none" }}
            onChange={(event) => onImageChange(event.target.files?.[0] || null)}
          />
        </div>
        <p className="hint">{agentRunning ? t("task.chat.queued_hint") : t("task.chat.hint")}</p>
      </div>
    </aside>
  );
}
