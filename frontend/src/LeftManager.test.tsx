import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LeftManager from "./layout/LeftManager";
import { DEFAULT_SETTINGS, useAppStore } from "./store";

const feature = {
  id: "base_plate",
  type: "box_base",
  operation: "base",
  dimensions: {
    length: { value: 60, unit: "mm", source: "drawing", confirmed_by_user: true },
    width: { value: 30, unit: "mm", source: "drawing", confirmed_by_user: true },
  },
  placement: { reference: "origin", x: 0, y: 0, z: 0, axis: "Z" },
  confirmed_by_user: true,
};

function renderManager() {
  return render(
    <LeftManager
      busy={false}
      description="测试描述"
      imageFile={null}
      modeLabel="严格模式"
      partFamily="plate"
      projectName="测试项目"
      selectedFeatureId="base_plate"
      statusLabel="可以生成"
      onDescriptionChange={vi.fn()}
      onImageChange={vi.fn()}
      onOpenSettings={vi.fn()}
    />,
  );
}

beforeEach(() => {
  useAppStore.setState({
    ui: {
      leftTab: "feature",
      rightTab: "assistant",
      leftDrawerOpen: false,
      rightDrawerOpen: false,
      leftWidth: 380,
      rightWidth: 360,
      focusMode: false,
      settingsOpen: false,
    },
  });
});

describe("LeftManager", () => {
  it("shows the kernel feature tree (empty) by default", () => {
    renderManager();
    // 主路径已切 kernel feature_graph；默认无 kernelTree 时显示空态
    expect(screen.getByText("尚无特征")).toBeInTheDocument();
  });

  it("switches to the property tab (kernel form empty)", async () => {
    const user = userEvent.setup();
    renderManager();
    await user.click(screen.getByRole("tab", { name: "属性" }));
    // kernel 路径 property 面板显示 no-feature 空态
    expect(screen.getByText("没有可编辑特征")).toBeInTheDocument();
  });

  it("closes the drawer from the header close button", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(
      <LeftManager
        busy={false}
        description="测试描述"
        imageFile={null}
        modeLabel="严格模式"
        partFamily="plate"
        projectName="测试项目"
        selectedFeatureId="base_plate"
        statusLabel="可以生成"
        onDescriptionChange={vi.fn()}
        onImageChange={vi.fn()}
        onOpenSettings={vi.fn()}
        onClose={onClose}
      />,
    );
    await user.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("switches to configuration and opens settings", async () => {
    const user = userEvent.setup();
    const onOpenSettings = vi.fn();
    render(
      <LeftManager
        busy={false}
        description="测试描述"
        imageFile={null}
        modeLabel="严格模式"
        partFamily="plate"
        projectName="测试项目"
        selectedFeatureId="base_plate"
        statusLabel="可以生成"
        onDescriptionChange={vi.fn()}
        onImageChange={vi.fn()}
        onOpenSettings={onOpenSettings}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "配置" }));
    expect(screen.getByText("ConfigurationManager")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开设置中心" }));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it("changes work mode from the configuration page", async () => {
    const user = userEvent.setup();
    const onSettingsChange = vi.fn();
    const onApplySettings = vi.fn();
    render(
      <LeftManager
        busy={false}
        description="????"
        imageFile={null}
        modeLabel="????"
        partFamily="plate"
        projectName="????"
        selectedFeatureId="base_plate"
        statusLabel="????"
        settings={{ ...DEFAULT_SETTINGS, operation_mode: "smart", smart_fill_policy: "aggressive_fill" }}
        onSettingsChange={onSettingsChange}
        onApplySettings={onApplySettings}
        onDescriptionChange={vi.fn()}
        onImageChange={vi.fn()}
        onOpenSettings={vi.fn()}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "配置" }));
    const selects = screen.getAllByRole("combobox");
    await user.selectOptions(selects[0], "strict");
    expect(onSettingsChange).toHaveBeenCalledWith(expect.objectContaining({ operation_mode: "strict" }));
    expect(onApplySettings).toHaveBeenCalledWith(expect.objectContaining({ operation_mode: "strict" }));
  });
});
