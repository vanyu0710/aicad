import { useEffect, useState } from "react";
import { useT } from "./i18n";

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

const DIM_KEYS: Record<string, string> = {
  length: "feature.dim.length",
  width: "feature.dim.width",
  height: "feature.dim.height",
  thickness: "feature.dim.thickness",
  outer_diameter: "feature.dim.outer_diameter",
  inner_diameter: "feature.dim.inner_diameter",
  diameter: "feature.dim.diameter",
  hole_diameter: "feature.dim.hole_diameter",
  depth: "feature.dim.depth",
  axial_width: "feature.dim.axial_width",
  reduced_outer_diameter: "feature.dim.reduced_outer_diameter",
  z_start: "feature.dim.z_start",
  count: "feature.dim.count",
  spacing: "feature.dim.spacing",
  pitch_radius: "feature.dim.pitch_radius",
  bolt_circle_radius: "feature.dim.bolt_circle_radius",
  slot_length: "feature.dim.slot_length",
  slot_width: "feature.dim.slot_width",
};

const SOURCE_KEYS: Record<string, string> = {
  drawing: "feature.source.drawing",
  user: "feature.source.user",
  assumption: "feature.source.assumption",
  derived: "feature.source.derived",
  unknown: "feature.source.unknown",
};

export default function FeatureForm({ feature, busy, onSave }: Props) {
  const t = useT();
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
        evidence: original.evidence || (hasValue ? t("feature.evidence.manual") : ""),
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

  const missingKeys = Object.entries(feature?.dimensions || {})
    .filter(([, dim]: [string, any]) => dim?.value == null)
    .map(([key]) => DIM_KEYS[key] ? t(DIM_KEYS[key]) : key);

  return (
    <div className="feat-form">
      <div className="feat-meta">
        <span className="feat-id">{feature?.id}</span>
        <span className="feat-type">{feature?.type}</span>
        <span className="feat-op">{feature?.operation}</span>
      </div>

      {missingKeys.length > 0 && (
        <div className="note-warn">
          {t("feature.missing", { keys: missingKeys.join(t("clarification.dim_sep")) })}
        </div>
      )}

      <h3 className="feat-section">{t("feature.dim_section")}</h3>
      <div className="dim-grid">
        {Object.entries(feature?.dimensions || {}).map(([key, dim]: [string, any]) => (
          <div className="dim-row" key={key}>
            <label className="dim-label" htmlFor={`dim-${key}`}>
              {DIM_KEYS[key] ? t(DIM_KEYS[key]) : key}
            </label>
            <input
              id={`dim-${key}`}
              className="dim-input"
              type="number"
              step="any"
              inputMode="decimal"
              value={dimValues[key] ?? ""}
              placeholder={dim?.value == null ? t("feature.pending_placeholder") : ""}
              onChange={(event) => setDim(key, event.target.value)}
            />
            <span className="dim-unit">{dim?.unit || "mm"}</span>
            {dim?.value == null && <span className="dim-pending" title={t("feature.pending.title")}>{t("feature.pending")}</span>}
            {dim?.source && <small className="dim-source">{SOURCE_KEYS[dim.source] ? t(SOURCE_KEYS[dim.source]) : dim.source}</small>}
          </div>
        ))}
      </div>
      {!Object.keys(feature?.dimensions || {}).length && <p className="muted">{t("feature.no_dims")}</p>}

      <h3 className="feat-section">{t("feature.position")}</h3>
      <div className="pos-grid">
        <div className="dim-row">
          <label className="dim-label" htmlFor="pos-ref">{t("feature.reference")}</label>
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
          <label className="dim-label" htmlFor="pos-axis">{t("feature.axis")}</label>
          <select id="pos-axis" value={placement.axis} onChange={(event) => setPos("axis", event.target.value)}>
            <option value="X">X</option>
            <option value="Y">Y</option>
            <option value="Z">Z</option>
          </select>
          <span className="dim-unit" />
        </div>
      </div>

      <div className="feat-actions">
        <label className="confirm-check">
          <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
          <span>{t("feature.confirmed_label")}</span>
        </label>
        <div className="action-buttons">
          <button onClick={handleSave} disabled={busy} className="primary">
            {busy ? t("feature.processing") : t("feature.save")}
          </button>
        </div>
      </div>

      <div className="feat-notes">
        {feature?.evidence && <p className="note-evidence">{t("feature.evidence", { value: feature.evidence })}</p>}
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