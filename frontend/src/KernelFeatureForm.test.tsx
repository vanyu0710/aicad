import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import KernelFeatureForm from "./KernelFeatureForm";
import type { KernelFeatureData } from "./api";

const feature: KernelFeatureData = {
  id: "F_0001",
  type: "extrude",
  name: "main_body",
  state: "COMPUTED",
  parameters: { sketch_name: "sk", depth: 12, mode: "new_body" },
};

function renderForm(f: KernelFeatureData = feature, onSave = vi.fn(), onDelete = vi.fn()) {
  return render(<KernelFeatureForm feature={f} busy={false} onSave={onSave} onDelete={onDelete} />);
}

describe("KernelFeatureForm", () => {
  it("renders feature id, type, name and editable params", () => {
    renderForm();
    expect(screen.getByText("F_0001")).toBeInTheDocument();
    expect(screen.getByText("extrude")).toBeInTheDocument();
    expect(screen.getByText("main_body")).toBeInTheDocument();
    // depth 可编辑
    expect(screen.getByDisplayValue("12")).toBeInTheDocument();
  });

  it("saves numeric params on save", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    renderForm(feature, onSave);
    const depthInput = screen.getByDisplayValue("12");
    await user.clear(depthInput);
    await user.type(depthInput, "20");
    await user.click(screen.getByRole("button", { name: "保存参数" }));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave.mock.calls[0][0]).toBe("F_0001");
    expect(onSave.mock.calls[0][1].depth).toBe(20);
  });

  it("blocks non-positive numeric param", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    renderForm(feature, onSave);
    const depthInput = screen.getByDisplayValue("12");
    await user.clear(depthInput);
    await user.type(depthInput, "-5");
    await user.click(screen.getByRole("button", { name: "保存参数" }));
    // 不调用保存，显示错误
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(/必须大于 0/)).toBeInTheDocument();
  });

  it("calls onDelete", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    renderForm(feature, vi.fn(), onDelete);
    await user.click(screen.getByRole("button", { name: "删除特征" }));
    expect(onDelete).toHaveBeenCalledWith("F_0001");
  });

  it("shows empty state when no feature", () => {
    render(<KernelFeatureForm feature={null} busy={false} onSave={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByText("请选择特征")).toBeInTheDocument();
  });
});
