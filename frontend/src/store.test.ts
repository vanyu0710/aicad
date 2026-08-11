import { beforeEach, describe, expect, it } from "vitest";
import {
  DEFAULT_DESCRIPTION_EN,
  DEFAULT_DESCRIPTION_ZH,
  MAX_DRAWER_WIDTH,
  MIN_DRAWER_WIDTH,
  clampDrawerWidth,
  markStartupSeen,
  readLanguage,
  readStartupMode,
  shouldShowStartup,
  useAppStore,
  writeLanguage,
  writeStartupMode,
} from "./store";

beforeEach(() => {
  localStorage.clear();
  useAppStore.setState({
    language: "zh",
    description: DEFAULT_DESCRIPTION_ZH,
    events: [],
    ui: {
      leftTab: "feature",
      rightTab: "assistant",
      leftDrawerOpen: false,
      rightDrawerOpen: false,
      leftWidth: 380,
      rightWidth: 360,
      focusMode: false,
      settingsOpen: false,
      commandTab: "features",
    },
  });
});

describe("language helpers", () => {
  it("defaults to Chinese and persists the selected language", () => {
    expect(readLanguage()).toBe("zh");
    writeLanguage("en");
    expect(readLanguage()).toBe("en");
    expect(localStorage.getItem("mechcad_language")).toBe("en");
  });

  it("rejects unknown stored languages", () => {
    localStorage.setItem("mechcad_language", "fr");
    expect(readLanguage()).toBe("zh");
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
  it("switches language, persists it, and updates the default description", () => {
    useAppStore.getState().setLanguage("en");
    expect(useAppStore.getState().language).toBe("en");
    expect(useAppStore.getState().description).toBe(DEFAULT_DESCRIPTION_EN);
    expect(localStorage.getItem("mechcad_language")).toBe("en");
    useAppStore.getState().setLanguage("zh");
    expect(useAppStore.getState().description).toBe(DEFAULT_DESCRIPTION_ZH);
  });

  it("keeps a user-entered description when switching language", () => {
    useAppStore.getState().setDescription("自定义零件");
    useAppStore.getState().setLanguage("en");
    expect(useAppStore.getState().description).toBe("自定义零件");
  });

  it("persists drawer and focus ui state", () => {
    useAppStore.getState().setUi({ leftWidth: 520, leftDrawerOpen: true, rightDrawerOpen: true, focusMode: true, rightTab: "review" });
    const ui = useAppStore.getState().ui;
    expect(ui.leftWidth).toBe(520);
    expect(ui.leftDrawerOpen).toBe(true);
    expect(ui.rightDrawerOpen).toBe(true);
    expect(ui.focusMode).toBe(true);
    expect(ui.rightTab).toBe("review");
    const stored = JSON.parse(localStorage.getItem("mechcad_ui_persist") || "{}");
    expect(stored.leftWidth).toBe(520);
    expect(stored.focusMode).toBe(true);
  });

  it("clamps drawer widths to the supported range", () => {
    expect(clampDrawerWidth(10)).toBe(MIN_DRAWER_WIDTH);
    expect(clampDrawerWidth(800)).toBe(MAX_DRAWER_WIDTH);
    expect(clampDrawerWidth(420)).toBe(420);
  });

  it("prepends events and can clear them", () => {
    useAppStore.getState().addEvents(["first"]);
    useAppStore.getState().addEvents(["second"]);
    expect(useAppStore.getState().events).toEqual(["second", "first"]);
    useAppStore.getState().clearEvents();
    expect(useAppStore.getState().events).toEqual([]);
  });
});