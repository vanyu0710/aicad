import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SettingsDialog from "./SettingsDialog";
import { DEFAULT_SETTINGS, useAppStore } from "./store";

beforeEach(() => {
  localStorage.clear();
  useAppStore.setState({ language: "zh" });
});

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

  it("switches the interface language", async () => {
    const user = userEvent.setup();
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
    const selects = screen.getAllByRole("combobox");
    await user.selectOptions(selects[0], "en");
    expect(useAppStore.getState().language).toBe("en");
    expect(localStorage.getItem("mechcad_language")).toBe("en");
    expect(screen.getByText("Settings")).toBeInTheDocument();
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
    await user.selectOptions(selects[1], "first");
    expect(onStartupModeChange).toHaveBeenCalledWith("first");
  });

  it("changes operation mode", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <SettingsDialog
        open
        settings={DEFAULT_SETTINGS}
        dirty={false}
        saving={false}
        notice=""
        startupMode="always"
        onClose={vi.fn()}
        onChange={onChange}
        onApply={vi.fn()}
        onStartupModeChange={vi.fn()}
      />,
    );
    expect(screen.queryByText("????")).not.toBeInTheDocument();
    const selects = screen.getAllByRole("combobox");
    await user.selectOptions(selects[2], "smart");
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ operation_mode: "smart" }));
  });

  it("changes smart policy when smart mode is active", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <SettingsDialog
        open
        settings={{ ...DEFAULT_SETTINGS, operation_mode: "smart", smart_fill_policy: "limited_fill" }}
        dirty={false}
        saving={false}
        notice=""
        startupMode="always"
        onClose={vi.fn()}
        onChange={onChange}
        onApply={vi.fn()}
        onStartupModeChange={vi.fn()}
      />,
    );
    const selects = screen.getAllByRole("combobox");
    await user.selectOptions(selects[3], "aggressive_fill");
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ smart_fill_policy: "aggressive_fill" }));
  });
});