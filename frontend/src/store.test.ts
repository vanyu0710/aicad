import { beforeEach, describe, expect, it } from "vitest";
import {
  markStartupSeen,
  readStartupMode,
  shouldShowStartup,
  useAppStore,
  writeStartupMode,
} from "./store";

beforeEach(() => {
  localStorage.clear();
  useAppStore.setState({
    events: [],
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

describe("startup mode helpers", () => {
  it("defaults to showing every launch", () => {
    expect(readStartupMode()).toBe("always");
    expect(shouldShowStartup()).toBe(true);
  });

  it("only shows once in first mode until marked seen", () => {
    writeStartupMode("first");
    expect(shouldShowStartup()).toBe(true);
    markStartupSeen();
    expect(shouldShowStartup()).toBe(false);
  });

  it("can be disabled entirely", () => {
    writeStartupMode("off");
    expect(shouldShowStartup()).toBe(false);
  });
});

describe("app store", () => {
  it("persists panel ui state", () => {
    useAppStore.getState().setUi({ leftWidth: 520, leftCollapsed: true, rightTab: "review" });
    const ui = useAppStore.getState().ui;
    expect(ui.leftWidth).toBe(520);
    expect(ui.leftCollapsed).toBe(true);
    expect(ui.rightTab).toBe("review");
  });

  it("prepends events and can clear them", () => {
    useAppStore.getState().addEvents(["first"]);
    useAppStore.getState().addEvents(["second"]);
    expect(useAppStore.getState().events).toEqual(["second", "first"]);
    useAppStore.getState().clearEvents();
    expect(useAppStore.getState().events).toEqual([]);
  });
});
