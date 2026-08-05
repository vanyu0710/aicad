import { useEffect, useState } from "react";

export type DimensionPatch = {
  value: number | null;
  unit: string;
  evidence: string;
  source: "drawing" | "user" | "assumption" | "derived" | "unknown";
  confirmed_by_user: boolean;
};

type PlacementForm = {
  reference: string;
  x: string;
  y: string;
  z: string;
  axis: string;
};

type Props = {
  feature: any;
  busy?: boolean;
  onSave: (payload: {
    dimensions: Record<string, DimensionPatch>;
    placement: {
      reference: string;
      x: number | null;
      y: number | null;
      z: number | null;
      axis: string;
    };
    confirmed_by_user: boolean;
  }) => void;
};

const DIM_LABELS: Record<string, string> = {
  length: "长",
  width: "宽",
  height: "高",
  thickness: "厚度",
  outer_diameter: "外径",
  inner_diameter: "内径",
  diameter: "直径",
  hole_diameter: "孔径",
  depth: "深度",
  axial_width: "轴向宽度",
  reduced_outer_diameter: "槽底外径",
  z_start: "起始高度 Z",
  count: "数量",
  spacing: "间距",
  pitch_radius: "分度圆半径",
  bolt_circle_radius: "螺栓圆半径",
  slot_length: "槽长",
  slot_width: "槽宽",
};

const SOURCE_TEXT: Record<string, string> = {
  drawing: "图纸",
  user: "用户",
  assumption: "推断",
  derived: "推导",
  unknown: "未知",
};

export default function FeatureForm({ feature, busy, onSave }: Props) {
  const [dimValues, setDimValues] = useState<Record<string, string>>({});
  const [placement, setPlacement] = useState<PlacementForm>({ reference: "origin", x: "", y: "", z: "", axis: "Z" });
  const [confirmed, setConfirmed] = useState(false);

  useEffect(() => {
    const dims: Record<string, string> = {};
    const rawDims: Record<string, any> = feature?.dimensions || {};
    for (const [key, dim] of Object.entries(rawDims)) {
      dims[key] = dim?.value != null ? String(dim.value) : "";
    }
    setDimValues(dims);
    const p = feature?.placement || {};
    setPlacement({
      reference: p.reference ?? "origin",
      x: p.x != null ? String(p.x) : "",
      y: p.y != null ? String(p.y) : "",
      z: p.z != null ? String(p.z) : "",
      axis: p.axis ?? "Z",
    });
    setConfirmed(Boolean(feature?.confirmed_by_user));
  }, [feature?.id, feature]);

  const setDim = (key: string, value: string) => setDimValues((prev) => ({ ...prev, [key]: value }));
  const setPos = (key: keyof PlacementForm, value: string) => setPlacement((prev) => ({ ...prev, [key]: value }));

  const handleSave = () => {
    const dimensions: Record<string, DimensionPatch> = {};
    for (const [key, raw] of Object.entries(dimValues)) {
      const original: any = feature?.dimensions?.[key] || {};
      const text = (raw ?? "").trim();
      const numeric = text === "" ? null : Number(text);
      const hasValue = numeric !== null && !Number.isNaN(numeric);
      dimensions[key] = {
        value: hasValue ? numeric : null,
        unit: original.unit || "mm",
        evidence: original.evidence || (hasValue ? "用户手动填写" : ""),
        source: hasValue ? "user" : original.source || "unknown",
        confirmed_by_user: hasValue,
      };
    }
    onSave({
      dimensions,
      placement: {
        reference: placement.reference || "origin",
        x: placement.x === "" ? null : Number(placement.x),
        y: placement.y === "" ? null : Number(placement.y),
        z: placement.z === "" ? null : Number(placement.z),
        axis: placement.axis,
      },
      confirmed_by_user: confirmed,
    });
  };

  const hasUnconfirmed = Object.values(dimValues).some((v) => (v ?? "").trim() === "");

  return (
    <div className="feat-form">
      <div className="feat-meta">
        <span className="feat-id">{feature?.id}</span>
        <span className="feat-type">{feature?.type}</span>
        <span className="feat-op">{feature?.operation}</span>
      </div>

      <h3 className="feat-section">尺寸参数</h3>
      <div className="dim-grid">
        {Object.entries(feature?.dimensions || {}).map(([key, dim]: [string, any]) => (
          <div className="dim-row" key={key}>
            <label className="dim-label" htmlFor={`dim-${key}`}>
              {DIM_LABELS[key] || key}
            </label>
            <input
              id={`dim-${key}`}
              className="dim-input"
              type="number"
              step="any"
              inputMode="decimal"
              value={dimValues[key] ?? ""}
              placeholder={dim?.value == null ? "待确认" : ""}
              onChange={(event) => setDim(key, event.target.value)}
            />
            <span className="dim-unit">{dim?.unit || "mm"}</span>
            {dim?.value == null && <span className="dim-pending" title="该尺寸尚无确认值">未确认</span>}
          </div>
        ))}
      </div>
      {!Object.keys(feature?.dimensions || {}).length && <p className="muted">该特征没有尺寸参数。</p>}

      <h3 className="feat-section">位置</h3>
      <div className="pos-grid">
        <div className="dim-row">
          <label className="dim-label" htmlFor="pos-ref">基准</label>
          <input id="pos-ref" className="dim-input" value={placement.reference} onChange={(event) => setPos("reference", event.target.value)} />
        </div>
        {(["x", "y", "z"] as const).map((axis) => (
          <div className="dim-row" key={axis}>
            <label className="dim-label" htmlFor={`pos-${axis}`}>{axis.toUpperCase()}</label>
            <input
              id={`pos-${axis}`}
              className="dim-input"
              type="number"
              step="any"
              inputMode="decimal"
              value={placement[axis]}
              onChange={(event) => setPos(axis, event.target.value)}
            />
            <span className="dim-unit">mm</span>
          </div>
        ))}
        <div className="dim-row">
          <label className="dim-label" htmlFor="pos-axis">主轴</label>
          <select id="pos-axis" value={placement.axis} onChange={(event) => setPos("axis", event.target.value)}>
            <option value="X">X</option>
            <option value="Y">Y</option>
            <option value="Z">Z</option>
          </select>
          <span className="dim-unit"></span>
        </div>
      </div>

      <div className="feat-actions">
        <label className="confirm-check">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          <span>这些尺寸已与用户确认</span>
        </label>
        <div className="action-buttons">
          <button onClick={handleSave} disabled={busy} className="primary">
            {busy ? "处理中" : "保存并重新建模"}
          </button>
        </div>
      </div>

      <div className="feat-notes">
        {hasUnconfirmed && <p className="note-warn">有未填写尺寸，保存后该特征可能无法完整建模，未确认项会进入待澄清问题。</p>}
        {feature?.evidence && <p className="note-evidence">证据：{feature.evidence}</p>}
        {feature?.unresolved?.length > 0 && (
          <ul className="note-unresolved">
            {(feature.unresolved as string[]).map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
