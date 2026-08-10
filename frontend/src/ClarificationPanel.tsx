import { useState } from "react";

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
  const [answers, setAnswers] = useState<Record<string, string>>({});
  if (!questions.length) return <p className="muted">暂无必答问题。若模型与实际不符，可在 AI 对话中描述要修改的尺寸或特征。</p>;

  const update = (id: string, value: string) => setAnswers((prev) => ({ ...prev, [id]: value }));
  const complete = questions.every((question) => question.required === false || answers[question.id]?.trim());

  return (
    <div className="clarification-list">
      {questions.map((question) => (
        <article className="clarification-card" key={question.id}>
          <strong>{question.text}</strong>
          {question.feature_id && <p><b>关联特征：</b>{question.feature_id}</p>}
          {question.dimension_refs?.length ? <p><b>需要补充：</b>{question.dimension_refs.join("、")}</p> : null}
          {question.reason && <p><b>为什么需要：</b>{question.reason}</p>}
          {question.impact && <p><b>对模型的影响：</b>{question.impact}</p>}
          {question.answer_type === "choice" && question.options.length ? (
            <select value={answers[question.id] || ""} onChange={(event) => update(question.id, event.target.value)}>
              <option value="">请选择</option>
              {question.options.map((option) => <option value={option} key={option}>{option}</option>)}
            </select>
          ) : (
            <div className="answer-input">
              <input
                type={question.answer_type === "number" ? "number" : "text"}
                value={answers[question.id] || ""}
                onChange={(event) => update(question.id, event.target.value)}
                placeholder={question.default_value ? `建议值：${question.default_value}` : "例如：槽宽 5mm，距端面 10mm，贯穿"}
              />
              {question.unit && <span>{question.unit}</span>}
            </div>
          )}
        </article>
      ))}
      <button className="primary" disabled={disabled || !complete} onClick={() => onContinue(Object.values(answers).join("；"))}>
        确认答案并继续建模
      </button>
    </div>
  );
}
