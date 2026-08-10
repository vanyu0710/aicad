import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import StartupScreen from "./StartupScreen";

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

describe("StartupScreen", () => {
  it("renders recent projects and opens one", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    render(
      <StartupScreen
        projects={[sampleProject]}
        backendState="connected"
        startupMode="always"
        error=""
        onCreate={vi.fn()}
        onOpen={onOpen}
        onDelete={vi.fn()}
        onModeChange={vi.fn()}
      />,
    );

    expect(screen.getByText("Demo 项目")).toBeInTheDocument();
    expect(screen.getByText(/plate/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "打开" }));
    expect(onOpen).toHaveBeenCalledWith("p1");
  });

  it("creates a project from the hero button", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();
    render(
      <StartupScreen
        projects={[]}
        backendState="connected"
        startupMode="always"
        error=""
        onCreate={onCreate}
        onOpen={vi.fn()}
        onDelete={vi.fn()}
        onModeChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "新建项目" }));
    expect(onCreate).toHaveBeenCalledTimes(1);
  });

  it("deletes a project after confirmation", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(
      <StartupScreen
        projects={[sampleProject]}
        backendState="connected"
        startupMode="always"
        error=""
        onCreate={vi.fn()}
        onOpen={vi.fn()}
        onDelete={onDelete}
        onModeChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "删除" }));
    expect(onDelete).toHaveBeenCalledWith("p1");
  });

  it("updates startup behavior", async () => {
    const user = userEvent.setup();
    const onModeChange = vi.fn();
    render(
      <StartupScreen
        projects={[]}
        backendState="connected"
        startupMode="always"
        error=""
        onCreate={vi.fn()}
        onOpen={vi.fn()}
        onDelete={vi.fn()}
        onModeChange={onModeChange}
      />,
    );

    await user.selectOptions(screen.getByRole("combobox"), "off");
    expect(onModeChange).toHaveBeenCalledWith("off");
  });
});
