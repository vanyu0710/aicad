import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import StartupScreen from "./StartupScreen";
import { useAppStore } from "./store";

const sampleProject = {
  project_id: "p1",
  name: "Demo 项目",
  created_at: "2026-08-10T00:00:00+00:00",
  updated_at: "2026-08-10T01:00:00+00:00",
  settings: {},
  current: { feature_plan: { part_family: "plate" } },
  history: [],
  redo_stack: [],
} as any;

const baseProps = {
  backendState: "connected" as const,
  startupMode: "always" as const,
  error: "",
  onCreate: vi.fn(),
  onOpen: vi.fn(),
  onDelete: vi.fn(),
  onRename: vi.fn(),
  onModeChange: vi.fn(),
};

beforeEach(() => {
  localStorage.clear();
  useAppStore.setState({ language: "zh" });
});

describe("StartupScreen", () => {
  it("renders recent projects and opens one", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(<StartupScreen projects={[sampleProject]} {...baseProps} onOpen={onOpen} />);

    expect(screen.getByText("Demo 项目")).toBeInTheDocument();
    expect(screen.getByText(/plate/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开" }));
    expect(onOpen).toHaveBeenCalledWith("p1");
  });

  it("creates a project from the hero button", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();
    render(<StartupScreen projects={[]} {...baseProps} onCreate={onCreate} />);

    await user.click(screen.getByRole("button", { name: "新建项目" }));
    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it("deletes a project after confirmation", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<StartupScreen projects={[sampleProject]} {...baseProps} onDelete={onDelete} />);

    await user.click(screen.getByRole("button", { name: "删除" }));
    expect(onDelete).toHaveBeenCalledWith("p1");
  });

  it("renames a project inline", async () => {
    const user = userEvent.setup();
    const onRename = vi.fn();
    render(<StartupScreen projects={[sampleProject]} {...baseProps} onRename={onRename} />);

    await user.click(screen.getByRole("button", { name: "重命名" }));
    const input = screen.getByDisplayValue("Demo 项目");
    await user.clear(input);
    await user.type(input, "新项目名");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(onRename).toHaveBeenCalledWith("p1", "新项目名");
  });

  it("updates startup behavior", async () => {
    const user = userEvent.setup();
    const onModeChange = vi.fn();
    render(<StartupScreen projects={[]} {...baseProps} onModeChange={onModeChange} />);

    await user.selectOptions(screen.getAllByRole("combobox")[0], "off");
    expect(onModeChange).toHaveBeenCalledWith("off");
  });

  it("switches the interface language", async () => {
    const user = userEvent.setup();
    render(<StartupScreen projects={[]} {...baseProps} />);

    await user.selectOptions(screen.getAllByRole("combobox")[1], "en");
    expect(useAppStore.getState().language).toBe("en");
    expect(localStorage.getItem("mechcad_language")).toBe("en");
    expect(screen.getByText("Turn sketches into manufacturable 3D models")).toBeInTheDocument();
  });
});