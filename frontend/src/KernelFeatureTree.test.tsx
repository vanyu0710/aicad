import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import KernelFeatureTree from "./KernelFeatureTree";
import type { KernelFeatureData } from "./api";

const nodes: Record<string, KernelFeatureData> = {
  F_0001: { id: "F_0001", type: "create_workplane", name: "base", state: "COMPUTED" },
  F_0002: { id: "F_0002", type: "extrude", name: "main_body", state: "COMPUTED", parent_id: "F_0001" },
  F_0003: { id: "F_0003", type: "hole", name: "center_bore", state: "PENDING", parent_id: "F_0002" },
};

const opHistory = [
  { feature_id: "F_0001", op: "create_workplane" },
  { feature_id: "F_0002", op: "extrude" },
  { feature_id: "F_0003", op: "hole" },
];

function renderTree() {
  return render(
    <KernelFeatureTree
      opHistory={opHistory}
      nodes={nodes}
      selectedFeatureId="F_0002"
      onSelectFeature={vi.fn()}
      onDeleteFeature={vi.fn()}
    />,
  );
}

describe("KernelFeatureTree", () => {
  it("renders op_history rows in order with type + state", () => {
    renderTree();
    expect(screen.getByText("main_body")).toBeInTheDocument();
    expect(screen.getByText("center_bore")).toBeInTheDocument();
    // type·name 作为 detail 单条文本；用正则匹配
    expect(screen.getByText(/extrude/)).toBeInTheDocument();
    expect(screen.getAllByText("COMPUTED").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("PENDING")).toBeInTheDocument();
  });

  it("indents child features by parent depth", () => {
    renderTree();
    const buttons = screen.getAllByRole("button");
    const child = buttons.find((b) => b.textContent?.includes("center_bore"));
    // F_0003 深度应为 2 → padding-left = 8 + 2*14 = 36
    expect(child).toBeTruthy();
    expect(child).toHaveStyle({ paddingLeft: "36px" });
  });

  it("selects a feature", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(
      <KernelFeatureTree
        opHistory={opHistory}
        nodes={nodes}
        selectedFeatureId=""
        onSelectFeature={onSelect}
        onDeleteFeature={vi.fn()}
      />,
    );
    await user.click(screen.getByText("main_body"));
    expect(onSelect).toHaveBeenCalledWith("F_0002");
  });

  it("deletes a feature", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    render(
      <KernelFeatureTree
        opHistory={opHistory}
        nodes={nodes}
        selectedFeatureId="F_0001"
        onSelectFeature={vi.fn()}
        onDeleteFeature={onDelete}
      />,
    );
    const deleteButtons = screen.getAllByTitle("删除此特征");
    await user.click(deleteButtons[0]);
    expect(onDelete).toHaveBeenCalledWith("F_0001");
  });

  it("shows empty state when no history", () => {
    render(
      <KernelFeatureTree
        opHistory={[]}
        nodes={{}}
        selectedFeatureId=""
        onSelectFeature={vi.fn()}
        onDeleteFeature={vi.fn()}
      />,
    );
    expect(screen.getByText("尚无特征")).toBeInTheDocument();
  });
});
