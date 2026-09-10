import { useMemo } from "react";
import type { KernelFeatureData } from "./api";
import { useT } from "./i18n";

type Props = {
  opHistory: Array<Record<string, unknown>>;
  nodes: Record<string, KernelFeatureData>;
  selectedFeatureId: string;
  onSelectFeature: (id: string) => void;
  onDeleteFeature?: (id: string) => void;
};

type Row = { id: string; label: string; detail: string; state: string; depth: number };

/**
 * 渲染 MechKernel feature_graph 的特征历史列表（按 op_history 顺序，最新在后）。
 * 取代旧 FeatureTree 的 FeatureV3 数据源；旧组件保留但不再使用。
 */
export default function KernelFeatureTree({ opHistory, nodes, selectedFeatureId, onSelectFeature, onDeleteFeature }: Props) {
  const t = useT();
  const rows = useMemo<Row[]>(() => {
    const parentOf: Record<string, string | null> = {};
    for (const [id, node] of Object.entries(nodes)) {
      parentOf[id] = node.parent_id ?? null;
    }
    const depthOf = (id: string): number => {
      let depth = 0;
      let cur = parentOf[id];
      const seen = new Set<string>();
      while (cur && !seen.has(cur)) {
        seen.add(cur);
        depth += 1;
        cur = parentOf[cur];
      }
      return depth;
    };
    return opHistory
      .map((entry) => {
        const featureId = String(entry.feature_id ?? entry.id ?? "");
        const node = nodes[featureId] ?? {};
        const type = String(node.type ?? entry.op ?? entry.type ?? "");
        const name = String(node.name ?? entry.name ?? "");
        const state = String(node.state ?? "COMPUTED");
        const detail = [type, name].filter(Boolean).join(" · ");
        return { id: featureId, label: name || type || featureId, detail, state, depth: depthOf(featureId) };
      })
      .filter((row) => row.id);
  }, [opHistory, nodes]);

  if (rows.length === 0) {
    return <div className="kernel-tree-empty">{t("tree.empty")}</div>;
  }

  return (
    <ul className="kernel-tree">
      {rows.map((row) => (
        <li key={row.id} className={`kernel-tree-row ${row.id === selectedFeatureId ? "selected" : ""} state-${row.state.toLowerCase()}`}>
          <button
            type="button"
            className="kernel-tree-select"
            style={{ paddingLeft: 8 + row.depth * 14 }}
            onClick={() => onSelectFeature(row.id)}
          >
            <span className="kernel-tree-label">{row.label}</span>
            <span className="kernel-tree-detail">{row.detail}</span>
            <span className="kernel-tree-state">{row.state}</span>
          </button>
          {onDeleteFeature && (
            <button type="button" className="kernel-tree-delete" title={t("kernel.tree.delete")} onClick={() => onDeleteFeature(row.id)}>
              ×
            </button>
          )}
        </li>
      ))}
    </ul>
  );
}
