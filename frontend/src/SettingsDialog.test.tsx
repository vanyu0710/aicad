import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import SettingsDialog from "./SettingsDialog";
import { DEFAULT_SETTINGS } from "./store";

describe("SettingsDialog", () => {
  it("renders nothing when closed", () => {
    const { container } = render(
      <SettingsDialog
        open={false}
        settings={DEFAULT_SETTINGS}
        dirty={false}
        saving={false}
        notice=""
        startupMode="always"
        onClose={vi.fn()}
        onChange={vi.fn()}
        onApply={vi.fn()}
        onStartupModeChange={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows model configuration and startup behavior", () => {
    render(
      <SettingsDialog
        open
        settings={DEFAULT_SETTINGS}
        dirty={false}
        saving={false}
        notice=""
        startupMode="always"
        onClose={vi.fn()}
        onChange={vi.fn()}
        onApply={vi.fn()}
        onStartupModeChange={vi.fn()}
      />,
    );
    expect(screen.getByText("设置中心")).toBeInTheDocument();
    expect(screen.getByText("视觉读图模型")).toBeInTheDocument();
    expect(screen.getByText("建模规划模型")).toBeInTheDocument();
  });

  it("updates startup behavior", async () => {
    const user = userEvent.setup();
    const onStartupModeChange = vi.fn();
    render(
      <SettingsDialog
        open
        settings={DEFAULT_SETTINGS}
        dirty={false}
        saving={false}
        notice=""
        startupMode="always"
        onClose={vi.fn()}
        onChange={vi.fn()}
        onApply={vi.fn()}
        onStartupModeChange={onStartupModeChange}
      />,
    );
    const selects = screen.getAllByRole("combobox");
    await user.selectOptions(selects[0], "first");
    expect(onStartupModeChange).toHaveBeenCalledWith("first");
  });
});
