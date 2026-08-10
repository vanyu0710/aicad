import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import FeatureForm from "./FeatureForm";

const SAMPLE_FEATURE = {
  id: "base_plate",
  type: "box_base",
  operation: "base",
  dimensions: {
    length: { value: 60, unit: "mm", evidence: "图纸标注", source: "drawing", confirmed_by_user: false },
    width: { value: null, unit: "mm", evidence: "", source: "unknown", confirmed_by_user: false },
  },
  placement: { reference: "origin", x: 0, y: 0, z: 0, axis: "Z" },
  confirmed_by_user: false,
};

describe("FeatureForm", () => {
  it("renders a labeled input per dimension with units", () => {
    render(<FeatureForm feature={SAMPLE_FEATURE} onSave={vi.fn()} />);
    expect(screen.getByLabelText("长度")).toBeInTheDocument();
    expect(screen.getByLabelText("宽度")).toBeInTheDocument();
    expect(screen.getByLabelText("长度")).toHaveValue(60);
    expect(screen.getByText("未确认")).toBeInTheDocument();
    expect(screen.getAllByText("mm").length).toBeGreaterThan(0);
  });

  it("renders feature id and type", () => {
    render(<FeatureForm feature={SAMPLE_FEATURE} onSave={vi.fn()} />);
    expect(screen.getByText("base_plate")).toBeInTheDocument();
    expect(screen.getByText("box_base")).toBeInTheDocument();
  });

  it("edits a dimension and saves a user-confirmed payload", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(<FeatureForm feature={SAMPLE_FEATURE} onSave={onSave} />);

    const widthInput = screen.getByLabelText("宽度");
    await user.clear(widthInput);
    await user.type(widthInput, "30");

    const confirm = screen.getByRole("checkbox");
    await user.click(confirm);
    await user.click(screen.getByRole("button", { name: "保存并重新建模" }));

    expect(onSave).toHaveBeenCalledTimes(1);
    const payload = onSave.mock.calls[0][0];
    expect(payload.dimensions.length.value).toBe(60);
    expect(payload.dimensions.width).toMatchObject({ value: 30, unit: "mm", source: "user", confirmed_by_user: true });
    expect(payload.confirmed_by_user).toBe(true);
  });

  it("clearing a value sends null so the dimension goes back to unresolved", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(<FeatureForm feature={SAMPLE_FEATURE} onSave={onSave} />);

    await user.clear(screen.getByLabelText("长度"));
    await user.click(screen.getByRole("button", { name: "保存并重新建模" }));

    const payload = onSave.mock.calls[0][0];
    expect(payload.dimensions.length.value).toBeNull();
    expect(payload.dimensions.length.confirmed_by_user).toBe(false);
  });

  it("edits placement coordinates", async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(<FeatureForm feature={SAMPLE_FEATURE} onSave={onSave} />);

    await user.clear(screen.getByLabelText("X"));
    await user.type(screen.getByLabelText("X"), "12.5");
    await user.selectOptions(screen.getByLabelText("主轴"), "Y");
    await user.click(screen.getByRole("button", { name: "保存并重新建模" }));

    const payload = onSave.mock.calls[0][0];
    expect(payload.placement).toMatchObject({ x: 12.5, axis: "Y", reference: "origin" });
  });

  it("shows a warning when a required dimension is still empty", () => {
    render(<FeatureForm feature={SAMPLE_FEATURE} onSave={vi.fn()} />);
    expect(screen.getByText(/缺少：宽度/)).toBeInTheDocument();
  });

  it("does not warn when every dimension has a value", () => {
    const filled = {
      ...SAMPLE_FEATURE,
      dimensions: {
        length: { value: 60, unit: "mm", source: "drawing", confirmed_by_user: true },
        width: { value: 30, unit: "mm", source: "drawing", confirmed_by_user: true },
      },
    };
    render(<FeatureForm feature={filled} onSave={vi.fn()} />);
    expect(screen.queryByText(/缺少：/)).not.toBeInTheDocument();
  });
});
