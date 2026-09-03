import { useState } from "react";
import type { Approval } from "./api";
import { useT } from "./i18n";

type Props = {
  approvals: Approval[];
  busy: boolean;
  onResolve: (approval: Approval, action: "approve" | "reject" | "edit", argsOverride?: Record<string, unknown>) => void;
};

/**
 * P2 人机协作审批卡：展示 agent 发出的确认点（破坏性操作 / 破坏性修复 / 提问），
 * 提供 approve / edit（改参后 approve）/ reject 三种动作。
 */
export default function ApprovalPanel({ approvals, busy, onResolve }: Props) {
  const t = useT();
  // 编辑态：approval_id → args 草稿
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({});

  if (approvals.length === 0) {
    return null;
  }

  const startEdit = (approval: Approval) => {
    const draft: Record<string, string> = {};
    for (const [key, value] of Object.entries(approval.args ?? {})) {
      draft[key] = String(value ?? "");
    }
    setDrafts((s) => ({ ...s, [approval.approval_id]: draft }));
  };

  return (
    <div className="approval-list">
      {approvals.map((approval) => {
        const kindLabel =
          approval.kind === "ask_user" ? t("approval.kind.ask") : approval.kind === "destructive_fix" ? t("approval.kind.fix") : t("approval.kind.destructive");
        const draft = drafts[approval.approval_id];
        const isEditing = Boolean(draft);
        const argText = JSON.stringify(approval.args ?? {}, null, 0);
        return (
          <div className="approval-card" key={approval.approval_id}>
            <div className="approval-head">
              <span className="approval-kind">{kindLabel}</span>
              <span className="approval-op">{approval.op}</span>
            </div>
            <div className="approval-message">{approval.message}</div>
            <details className="approval-args">
              <summary>{t("approval.arguments")}</summary>
              <pre>{argText}</pre>
            </details>
            {isEditing && draft ? (
              <div className="approval-edit">
                {Object.entries(draft).map(([key, value]) => (
                  <label className="approval-edit-field" key={key}>
                    <span>{key}</span>
                    <input
                      type="text"
                      value={value}
                      onChange={(e) => setDrafts((s) => ({ ...s, [approval.approval_id]: { ...draft, [key]: e.target.value } }))}
                    />
                  </label>
                ))}
                <div className="approval-edit-actions">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      const override: Record<string, unknown> = {};
                      for (const [key, value] of Object.entries(draft)) {
                        const num = Number(value);
                        override[key] = value.trim() === "" ? undefined : Number.isFinite(num) ? num : value;
                      }
                      onResolve(approval, "edit", override);
                      setDrafts((s) => {
                        const next = { ...s };
                        delete next[approval.approval_id];
                        return next;
                      });
                    }}
                  >
                    {t("approval.confirm_edit")}
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      setDrafts((s) => {
                        const next = { ...s };
                        delete next[approval.approval_id];
                        return next;
                      })
                    }
                  >
                    {t("common.cancel")}
                  </button>
                </div>
              </div>
            ) : (
              <div className="approval-actions">
                <button type="button" className="approval-approve" disabled={busy} onClick={() => onResolve(approval, "approve")}>
                  {t("approval.approve")}
                </button>
                <button type="button" className="approval-edit-toggle" disabled={busy} onClick={() => startEdit(approval)}>
                  {t("approval.edit")}
                </button>
                <button type="button" className="approval-reject" disabled={busy} onClick={() => onResolve(approval, "reject")}>
                  {t("approval.reject")}
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
