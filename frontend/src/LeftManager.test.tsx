import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LeftManager from "./layout/LeftManager";
import { useAppStore } from "./store";

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
      features={[feature]}
      imageFile={null}
      modeLabel="严格模式"
      partFamily="plate"
      projectName="测试项目"
      selectedFeature={feature}
      selectedFeatureId="base_plate"
      statusLabel="可以生成"
      unresolvedCount={0}
      onDescriptionChange={vi.fn()}
      onImageChange={vi.fn()}
      onSelectFeature={vi.fn()}
      onSaveFeature={vi.fn()}
      onOpenSettings={vi.fn()}
    />,
  );
}

beforeEach(() => {
  useAppStore.setState({
    ui: {
      leftTab: "feature",
      rightTab: "assistant",
      leftCollapsed: false,
      rightCollapsed: false,
      leftWidth: 420,
      rightWidth: 380,
      settingsOpen: false,
      commandTab: "features",
    },
  });
});

describe("LeftManager", () => {
  it("shows the feature tree by default", () => {
    renderManager();
    expect(screen.getByText("base_plate")).toBeInTheDocument();
    expect(screen.getByText("box_base")).toBeInTheDocument();
  });

  it("switches to the property tab", async () => {
    const user = userEvent.setup();
    renderManager();
    await user.click(screen.getByRole("tab", { name: "属性" }));
    expect(screen.getByText("PropertyManager")).toBeInTheDocument();
    expect(screen.getByText("尺寸参数")).toBeInTheDocument();
  });

  it("switches to configuration and opens settings", async () => {
    const user = userEvent.setup();
    const onOpenSettings = vi.fn();
    render(
      <LeftManager
        busy={false}
        description="测试描述"
        features={[feature]}
        imageFile={null}
        modeLabel="严格模式"
        partFamily="plate"
        projectName="测试项目"
        selectedFeature={feature}
        selectedFeatureId="base_plate"
        statusLabel="可以生成"
        unresolvedCount={0}
        onDescriptionChange={vi.fn()}
        onImageChange={vi.fn()}
        onSelectFeature={vi.fn()}
        onSaveFeature={vi.fn()}
        onOpenSettings={onOpenSettings}
      />,
    );
    await user.click(screen.getByRole("tab", { name: "配置" }));
    expect(screen.getByText("ConfigurationManager")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开设置中心" }));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });
});
