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
describe("agent chat stream store (v0.10)", () => {
  beforeEach(() => {
    useAppStore.setState({ chat: [], agentRunning: false });
  });

  it("appends user entries with image flag", () => {
    useAppStore.getState().appendChatUser("做一个法兰", true);
    const chat = useAppStore.getState().chat;
    expect(chat).toHaveLength(1);
    expect(chat[0].role).toBe("user");
    expect(chat[0].hasImage).toBe(true);
    expect(chat[0].status).toBe("done");
  });

  it("streams assistant deltas into one bubble then finalizes", () => {
    useAppStore.getState().appendChatUser("任务");
    useAppStore.getState().appendChatAssistantDelta("先建");
    useAppStore.getState().appendChatAssistantDelta("基准面");
    let chat = useAppStore.getState().chat;
    expect(chat).toHaveLength(2);
    expect(chat[1].text).toBe("先建基准面");
    expect(chat[1].status).toBe("streaming");
    useAppStore.getState().attachChatToolCard({ step: 1, op: "create_workplane", autofix: false });
    useAppStore.getState().finalizeChatAssistant();
    chat = useAppStore.getState().chat;
    expect(chat[1].tools).toHaveLength(1);
    expect(chat[1].tools[0].op).toBe("create_workplane");
    expect(chat[1].status).toBe("done");
  });

  it("starts a new assistant bubble for tool-only rounds", () => {
    useAppStore.getState().attachChatToolCard({ step: 1, op: "extrude", autofix: false });
    const chat = useAppStore.getState().chat;
    expect(chat).toHaveLength(1);
    expect(chat[0].role).toBe("assistant");
    expect(chat[0].text).toBe("");
    expect(chat[0].tools[0].op).toBe("extrude");
  });

  it("replaces the whole stream on session load", () => {
    useAppStore.getState().appendChatUser("旧的");
    useAppStore.getState().setChat([
      { id: "h1", role: "user", text: "历史消息", hasImage: false, status: "done", tools: [] },
    ]);
    expect(useAppStore.getState().chat.map((entry) => entry.text)).toEqual(["历史消息"]);
  });
});
