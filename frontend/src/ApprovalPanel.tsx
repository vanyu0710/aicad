import { useState } from "react";
import type { Approval, AskQuestion, PlanStep } from "./api";
import { useT } from "./i18n";

type Props = {
  approvals: Approval[];
  busy: boolean;
  onResolve: (approval: Approval, action: "approve" | "reject" | "edit", argsOverride?: Record<string, unknown>) => void;
};

/**
 * 人机协作审批卡：破坏性操作/破坏性修复走通用 approve/edit/reject；
 * ask_user 渲染结构化问题卡片（单选/多选/自由文本 + 自动"其他"）。
 */
export default function ApprovalPanel({ approvals, busy, onResolve }: Props) {
  const t = useT();
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
        if (approval.kind === "ask_user") {
          return <AskUserCard key={approval.approval_id} approval={approval} busy={busy} onResolve={onResolve} />;
        }
        if (approval.kind === "plan_review") {
          return <PlanReviewCard key={approval.approval_id} approval={approval} busy={busy} onResolve={onResolve} />;
        }
        const kindLabel =
          approval.kind === "destructive_fix"
            ? t("approval.kind.fix")
            : t("approval.kind.destructive");
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

type Answers = Record<string, string | string[]>;

function normalizeQuestions(approval: Approval): AskQuestion[] {
  const raw = (approval.options?.questions ?? approval.args?.questions) as AskQuestion[] | undefined;
  if (Array.isArray(raw) && raw.length) {
    return raw;
  }
  const legacy = String(approval.message || "");
  return [{ id: "q1", question: legacy || "（请回答）", type: "text", allowFreeText: true, required: true }];
}

function AskUserCard({
  approval,
  busy,
  onResolve,
}: {
  approval: Approval;
  busy: boolean;
  onResolve: Props["onResolve"];
}) {
  const t = useT();
  const questions = normalizeQuestions(approval);
  const [answers, setAnswers] = useState<Answers>({});
  const [free, setFree] = useState<Record<string, string>>({});

  const setSingle = (id: string, label: string) => setAnswers((a) => ({ ...a, [id]: label }));
  const toggleMulti = (id: string, label: string) =>
    setAnswers((a) => {
      const current = Array.isArray(a[id]) ? (a[id] as string[]) : [];
      const next = current.includes(label) ? current.filter((v) => v !== label) : [...current, label];
      return { ...a, [id]: next };
    });

  const finalAnswers = (): Answers => {
    const out: Answers = {};
    for (const q of questions) {
      const freeText = (free[q.id] || "").trim();
      const base = answers[q.id];
      if (q.type === "multi") {
        const list = Array.isArray(base) ? [...base] : [];
        if (freeText) list.push(freeText);
        out[q.id] = list;
      } else if (freeText) {
        out[q.id] = freeText;
      } else if (base != null) {
        out[q.id] = base;
      }
    }
    return out;
  };

  const allRequiredAnswered = questions.every((q) => {
    if (q.required === false) return true;
    const val = finalAnswers()[q.id];
    return Array.isArray(val) ? val.length > 0 : Boolean(val && String(val).trim());
  });

  return (
    <div className="approval-card ask-card">
      <div className="approval-head">
        <span className="approval-kind">{t("approval.kind.ask")}</span>
      </div>
      {questions.map((q) => {
        const allowFree = q.allowFreeText !== false;
        return (
          <div className="ask-question" key={q.id}>
            <div className="ask-question-head">
              {q.header && <span className="ask-chip">{q.header}</span>}
              <span className="ask-question-text">{q.question}</span>
              <span className="ask-required-tag">{q.required === false ? t("approval.question.optional") : t("approval.question.required")}</span>
            </div>
            {q.type === "text" ? (
              <textarea
                className="ask-text"
                rows={2}
                value={String(answers[q.id] ?? "")}
                onChange={(e) => setAnswers((a) => ({ ...a, [q.id]: e.target.value }))}
              />
            ) : (
              <div className="ask-options">
                {(q.options || []).map((opt) => {
                  const checked =
                    q.type === "multi"
                      ? Array.isArray(answers[q.id]) && (answers[q.id] as string[]).includes(opt.label)
                      : answers[q.id] === opt.label;
                  return (
                    <label className={`ask-option${checked ? " checked" : ""}`} key={opt.label}>
                      <input
                        type={q.type === "multi" ? "checkbox" : "radio"}
                        name={q.id}
                        checked={checked}
                        onChange={() => (q.type === "multi" ? toggleMulti(q.id, opt.label) : setSingle(q.id, opt.label))}
                      />
                      <span className="ask-option-label">
                        {opt.label}
                        {opt.description && <em className="ask-option-desc">{opt.description}</em>}
                      </span>
                    </label>
                  );
                })}
                {allowFree && (
                  <label className="ask-option ask-other">
                    <span className="ask-option-label">{t("approval.question.other")}</span>
                    <input
                      type="text"
                      className="ask-other-input"
                      aria-label={t("approval.question.other")}
                      value={free[q.id] ?? ""}
                      onChange={(e) => setFree((f) => ({ ...f, [q.id]: e.target.value }))}
                    />
                  </label>
                )}
              </div>
            )}
          </div>
        );
      })}
      <div className="approval-actions">
        <button
          type="button"
          className="approval-approve"
          disabled={busy || !allRequiredAnswered}
          onClick={() => onResolve(approval, "edit", { answers: finalAnswers() })}
        >
          {t("approval.question.submit")}
        </button>
        <button type="button" className="approval-reject" disabled={busy} onClick={() => onResolve(approval, "reject")}>
          {t("approval.question.skip")}
        </button>
      </div>
    </div>
  );
}

function PlanReviewCard({
  approval,
  busy,
  onResolve,
}: {
  approval: Approval;
  busy: boolean;
  onResolve: Props["onResolve"];
}) {
  const t = useT();
  const [feedback, setFeedback] = useState("");
  const plan = (approval.options?.plan ?? { steps: [] }) as { summary?: string; steps?: PlanStep[] };
  const steps = Array.isArray(plan.steps) ? plan.steps : [];
  return (
    <div className="approval-card plan-card-review">
      <div className="approval-head">
        <span className="approval-kind">{t("approval.kind.plan")}</span>
      </div>
      {plan.summary && <div className="approval-message">{plan.summary}</div>}
      <ol className="plan-steps">
        {steps.map((step) => (
          <li key={step.id} className="plan-step pending">
            <span className="plan-step-mark" aria-hidden="true">○</span>
            <span className="plan-step-title">{step.title}</span>
            {step.op && <code className="plan-step-op">{step.op}</code>}
          </li>
        ))}
      </ol>
      <textarea
        className="ask-text"
        rows={2}
        placeholder={t("approval.plan.feedback_placeholder")}
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
      />
      <div className="approval-actions">
        <button type="button" className="approval-approve" disabled={busy} onClick={() => onResolve(approval, "approve")}>
          {t("approval.approve")}
        </button>
        <button
          type="button"
          className="approval-reject"
          disabled={busy}
          onClick={() => onResolve(approval, "reject", { feedback: feedback.trim() || undefined })}
        >
          {t("approval.plan.request_changes")}
        </button>
      </div>
    </div>
  );
}
