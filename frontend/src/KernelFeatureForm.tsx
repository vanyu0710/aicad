import { useEffect, useMemo, useState } from "react";
import type { KernelFeatureData } from "./api";

type Props = {
  feature: KernelFeatureData | null;
  busy: boolean;
  onSave: (featureId: string, newParams: Record<string, unknown>) => void;
  onDelete?: (featureId: string) => void;
};

/**
 * 展示/编辑 MechKernel feature_graph 节点参数。数值字段可改（>0 校验），
 * 保存调用 kernel/update_feature（内核参数化重放）。取代旧 FeatureForm 的 FeatureV3 逻辑。
 */
export default function KernelFeatureForm({ feature, busy, onSave, onDelete }: Props) {
  const [draft, setDraft] = useState<Record<string, unknown>>({});
  const [error, setError] = useState("");

  useEffect(() => {
    setDraft({ ...((feature?.parameters as Record<string, unknown>) ?? {}) });
    setError("");
  }, [feature?.id]);

  const editableEntries = useMemo(() => {
    return Object.entries(draft).filter(([key]) => typeof key === "string" && key !== "name" && key !== "id");
  }, [draft]);

  if (!feature) {
    return <div className="kernel-form-empty">请选择特征</div>;
  }

  const type = feature.type ?? "unknown";
  const name = feature.name ?? "";

  const handleSave = () => {
    // 过滤空值与非数值
    const params: Record<string, unknown> = {};
    for (const [key, value] of editableEntries) {
      if (value === "" || value === null || value === undefined) {
        continue;
      }
      if (typeof value === "number") {
        if (value <= 0 && key !== "angle_deg") {
          setError(`参数 ${key} 必须大于 0`);
          return;
        }
        params[key] = value;
      } else if (typeof value === "string" && value.trim() !== "" && value !== "None") {
        const num = Number(value);
        if (Number.isFinite(num)) {
          if (num <= 0 && key !== "angle_deg") {
            setError(`参数 ${key} 必须大于 0`);
            return;
          }
          params[key] = num;
        } else {
          params[key] = value;
        }
      } else if (typeof value === "boolean") {
        params[key] = value;
      }
    }
    setError("");
    onSave(feature.id, params);
  };

  return (
    <div className="kernel-form">
      <div className="kernel-form-head">
        <span className="kernel-form-id">{feature.id}</span>
        <span className="kernel-form-type">{type}</span>
        {name && <span className="kernel-form-name">{name}</span>}
      </div>

      {editableEntries.length === 0 ? (
        <div className="kernel-form-empty">此特征无可编辑参数</div>
      ) : (
        <div className="kernel-form-fields">
          {editableEntries.map(([key, value]) => (
            <label className="kernel-form-field" key={key}>
              <span className="kernel-form-key">{key}</span>
              <input
                type="text"
                value={value as string}
                disabled={busy}
                onChange={(event) => setDraft((d) => ({ ...d, [key]: event.target.value }))}
              />
            </label>
          ))}
        </div>
      )}

      {error && <div className="kernel-form-error">{error}</div>}

      <div className="kernel-form-actions">
        <button type="button" className="kernel-form-save" disabled={busy} onClick={handleSave}>
          保存参数
        </button>
        {onDelete && (
          <button type="button" className="kernel-form-delete" disabled={busy} onClick={() => onDelete(feature.id)}>
            删除特征
          </button>
        )}
      </div>
    </div>
  );
}
