import { useState } from "react";
import { useT } from "./i18n";

type Question = {
  id: string;
  text: string;
  feature_id?: string;
  dimension_refs?: string[];
  options: string[];
  required?: boolean;
  reason?: string;
  impact?: string;
  answer_type?: "text" | "number" | "choice";
  unit?: string;
  default_value?: string;
};

export default function ClarificationPanel({
  questions,
  onContinue,
  disabled,
}: {
  questions: Question[];
  onContinue: (answers: string) => void;
  disabled?: boolean;
}) {
  const t = useT();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  if (!questions.length) return <p className="muted">{t("clarification.empty")}</p>;

  const update = (id: string, value: string) => setAnswers((prev) => ({ ...prev, [id]: value }));
  const complete = questions.every((question) => question.required === false || answers[question.id]?.trim());

  return (
    <div className="clarification-list">
      {questions.map((question) => (
        <article className="clarification-card" key={question.id}>
          <strong>{question.text}</strong>
          {question.feature_id && <p><b>{t("clarification.feature")}</b>{question.feature_id}</p>}
          {question.dimension_refs?.length ? <p><b>{t("clarification.dimensions")}</b>{question.dimension_refs.join(t("clarification.dim_sep"))}</p> : null}
          {question.reason && <p><b>{t("clarification.reason")}</b>{question.reason}</p>}
          {question.impact && <p><b>{t("clarification.impact")}</b>{question.impact}</p>}
          {question.answer_type === "choice" && question.options.length ? (
            <select value={answers[question.id] || ""} onChange={(event) => update(question.id, event.target.value)}>
              <option value="">{t("clarification.select")}</option>
              {question.options.map((option) => <option value={option} key={option}>{option}</option>)}
            </select>
          ) : (
            <div className="answer-input">
              <input
                type={question.answer_type === "number" ? "number" : "text"}
                value={answers[question.id] || ""}
                onChange={(event) => update(question.id, event.target.value)}
                placeholder={question.default_value ? t("clarification.default", { value: question.default_value }) : t("clarification.placeholder")}
              />
              {question.unit && <span>{question.unit}</span>}
            </div>
          )}
        </article>
      ))}
      <button className="primary" disabled={disabled || !complete} onClick={() => onContinue(Object.values(answers).join("; "))}>
        {t("clarification.confirm")}
      </button>
    </div>
  );
}