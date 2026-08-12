import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import FeatureTree from "./FeatureTree";

const features = [
  {
    id: "base_plate",
    type: "box_base",
    operation: "base",
    execution_status: "modeled",
    dimensions: { length: { value: 60 }, width: { value: 30 }, height: { value: 4 } },
    depends_on: [],
    unresolved: [],
  },
  {
    id: "hole_left",
    type: "through_hole",
    operation: "remove",
    execution_status: "skipped",
    dimensions: { diameter: { value: null, source: "unknown" } },
    depends_on: ["base_plate"],
    unresolved: ["missing diameter"],
  },
  {
    id: "boss_top",
    type: "boss_cylinder",
    operation: "add",
    execution_status: "modeled",
    dimensions: { diameter: { value: 12, source: "assumption", confirmed_by_user: false }, height: { value: 8 } },
    depends_on: ["base_plate"],
    unresolved: [],
    assumptions: ["concept size"],
  },
];

describe("FeatureTree", () => {
  it("groups features by manufacturing stage and shows statuses", () => {
    render(<FeatureTree features={features} selectedFeatureId="" onSelectFeature={vi.fn()} />);
    expect(screen.getByText("主基体")).toBeInTheDocument();
    expect(screen.getByText("减料特征")).toBeInTheDocument();
    expect(screen.getByText("加料特征")).toBeInTheDocument();
    expect(screen.getAllByText("已建模").length).toBeGreaterThan(0);
    expect(screen.getByText("F1")).toBeInTheDocument();
    expect(screen.getAllByText("已跳过").length).toBeGreaterThan(0);
    expect(screen.getByText("未解决 2")).toBeInTheDocument();
  });

  it("collapses and expands groups", async () => {
    const user = userEvent.setup();
    render(<FeatureTree features={features} selectedFeatureId="" onSelectFeature={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: /减料特征/ }));
    expect(screen.queryByText("hole_left")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /减料特征/ }));
    expect(screen.getByText("hole_left")).toBeInTheDocument();
  });

  it("selects a feature when clicked", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<FeatureTree features={features} selectedFeatureId="" onSelectFeature={onSelect} />);
    await user.click(screen.getByText("hole_left"));
    expect(onSelect).toHaveBeenCalledWith("hole_left");
  });
});
