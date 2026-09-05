import { create } from "zustand";
import type { Approval, ModelConfig, ProcessStep, ProjectState } from "./api";

export type Lang = "zh" | "en";
export type ManagerTab = "feature" | "property" | "configuration";
export type TaskTab = "assistant" | "review" | "process" | "logs" | "plan" | "export";
export type StartupMode = "always" | "first" | "off";

/** agent 会话流里的工具调用卡片（对应 WS agent_step 事件）。 */
export type ChatToolCard = {
  step: number;
  op: string;
  argsPreview?: string;
  success?: boolean;
  summary?: string;
  autofix: boolean;
  message?: string;
};

/** 会话流消息：user/assistant 气泡，assistant 可携带工具卡并处于流式状态。 */
export type ChatEntry = {
  id: string;
  role: "user" | "assistant";
  text: string;
  hasImage: boolean;
  status: "streaming" | "done";
  tools: ChatToolCard[];
  /** 几何可视化快照（/api/artifacts/... 相对 URL），按到达顺序内嵌展示。 */
  snapshots: string[];
};

export const DEFAULT_DESCRIPTION_ZH =
  "一件带孔或带槽的机械零件，请按整体到细节规划。已知尺寸请直接写明，未知尺寸请留给系统提问。";
export const DEFAULT_DESCRIPTION_EN =
  "A mechanical part with holes or slots. Plan from the overall shape to details. Write known dimensions directly; leave unknown values for the system to ask.";
export const LANGUAGE_KEY = "mechcad_language";
export const STARTUP_KEY = "mechcad_startup_mode";
export const STARTUP_SEEN_KEY = "mechcad_startup_seen";
export const UI_PERSIST_KEY = "mechcad_ui_persist";
export const MIN_DRAWER_WIDTH = 300;
export const MAX_DRAWER_WIDTH = 520;

export function clampDrawerWidth(value: number) {
  return Math.min(MAX_DRAWER_WIDTH, Math.max(MIN_DRAWER_WIDTH, Math.round(value)));
}

export function readStoredUi(): Pick<UiState, "leftWidth" | "rightWidth" | "focusMode"> {
  try {
    const stored = JSON.parse(localStorage.getItem(UI_PERSIST_KEY) || "{}") as Partial<UiState>;
    return {
      leftWidth: clampDrawerWidth(stored.leftWidth || 380),
      rightWidth: clampDrawerWidth(stored.rightWidth || 360),
      focusMode: Boolean(stored.focusMode),
    };
  } catch {
    return { leftWidth: 380, rightWidth: 360, focusMode: false };
  }
}

function writeStoredUi(ui: UiState) {
  localStorage.setItem(UI_PERSIST_KEY, JSON.stringify({
    leftWidth: ui.leftWidth,
    rightWidth: ui.rightWidth,
    focusMode: ui.focusMode,
  }));
}

export const DEFAULT_SETTINGS: ModelConfig = {
  vision_provider: "custom",
  vision_model: "",
  vision_base_url: "",
  vision_api_key: "",
  vision_protocol: "openai",
  planner_provider: "custom",
  planner_model: "",
  planner_base_url: "",
  planner_api_key: "",
  planner_protocol: "openai",
  operation_mode: "strict",
  smart_fill_policy: "limited_fill",
};

export type UiState = {
  leftTab: ManagerTab;
  rightTab: TaskTab;
  leftDrawerOpen: boolean;
  rightDrawerOpen: boolean;
  leftWidth: number;
  rightWidth: number;
  focusMode: boolean;
  settingsOpen: boolean;
};

type AppState = {
  project: ProjectState | null;
  description: string;
  imageFile: File | null;
  settings: ModelConfig;
  language: Lang;
  selectedFeatureId: string;
  chatMessage: string;
  events: string[];
  processSteps: ProcessStep[];
  busy: boolean;
  error: string;
  backendState: "connected" | "offline";
  recentProjects: ProjectState[];
  startupMode: StartupMode;
  showStartup: boolean;
  settingsDirty: boolean;
  settingsSaving: boolean;
  settingsNotice: string;
  ui: UiState;
  agentRunning: boolean;
  agentSteps: number;
  agentLastOp: string;
  pendingApprovals: Approval[];
  chat: ChatEntry[];
  setProject: (project: ProjectState | null) => void;
  setDescription: (value: string) => void;
  setImageFile: (file: File | null) => void;
  setSettings: (settings: ModelConfig) => void;
  setLanguage: (language: Lang) => void;
  setSelectedFeatureId: (featureId: string) => void;
  setChatMessage: (value: string) => void;
  addEvents: (items: string[]) => void;
  clearEvents: () => void;
  addProcessStep: (step: ProcessStep) => void;
  setProcessSteps: (steps: ProcessStep[]) => void;
  setBusy: (busy: boolean) => void;
  setError: (error: string) => void;
  setBackendState: (state: "connected" | "offline") => void;
  setRecentProjects: (projects: ProjectState[]) => void;
  setStartupMode: (mode: StartupMode) => void;
  setShowStartup: (show: boolean) => void;
  setSettingsDirty: (dirty: boolean) => void;
  setSettingsSaving: (saving: boolean) => void;
  setSettingsNotice: (notice: string) => void;
  setUi: (patch: Partial<UiState>) => void;
  setAgentRunning: (running: boolean) => void;
  setAgentSteps: (steps: number) => void;
  setAgentLastOp: (op: string) => void;
  setPendingApprovals: (approvals: Approval[]) => void;
  setChat: (entries: ChatEntry[]) => void;
  appendChatUser: (text: string, hasImage?: boolean) => void;
  appendChatAssistantDelta: (chunk: string) => void;
  attachChatToolCard: (card: ChatToolCard) => void;
  attachChatSnapshot: (url: string) => void;
  finalizeChatAssistant: () => void;
};

let chatEntrySeq = 0;

function nextChatId(role: string) {
  chatEntrySeq += 1;
  return `chat-${role}-${Date.now()}-${chatEntrySeq}`;
}

export function readLanguage(): Lang {
  const stored = localStorage.getItem(LANGUAGE_KEY);
  return stored === "zh" || stored === "en" ? stored : "zh";
}

export function writeLanguage(language: Lang) {
  localStorage.setItem(LANGUAGE_KEY, language);
}

export function readStartupMode(): StartupMode {
  const stored = localStorage.getItem(STARTUP_KEY);
  if (stored === "always" || stored === "first" || stored === "off") {
    return stored;
  }
  return "always";
}

export function writeStartupMode(mode: StartupMode) {
  localStorage.setItem(STARTUP_KEY, mode);
}

export function shouldShowStartup(mode = readStartupMode()): boolean {
  if (mode === "off") {
    return false;
  }
  if (mode === "first") {
    return localStorage.getItem(STARTUP_SEEN_KEY) !== "1";
  }
  return true;
}

export function markStartupSeen() {
  localStorage.setItem(STARTUP_SEEN_KEY, "1");
}

export const useAppStore = create<AppState>((set) => ({
  project: null,
  description: readLanguage() === "en" ? DEFAULT_DESCRIPTION_EN : DEFAULT_DESCRIPTION_ZH,
  imageFile: null,
  settings: { ...DEFAULT_SETTINGS },
  selectedFeatureId: "",
  chatMessage: "",
  events: [],
  processSteps: [],
  busy: false,
  error: "",
  backendState: "connected",
  recentProjects: [],
  startupMode: readStartupMode(),
  language: readLanguage(),
  showStartup: shouldShowStartup(),
  settingsDirty: false,
  settingsSaving: false,
  settingsNotice: "",
  agentRunning: false,
  agentSteps: 0,
  agentLastOp: "",
  pendingApprovals: [],
  chat: [],
  ui: {
    leftTab: "feature",
    rightTab: "assistant",
    leftDrawerOpen: false,
    rightDrawerOpen: false,
    ...readStoredUi(),
    settingsOpen: false,
  },
  setProject: (project) => set({ project }),
  setDescription: (description) => set({ description }),
  setImageFile: (imageFile) => set({ imageFile }),
  setSettings: (settings) => set({ settings }),
  setLanguage: (language) => {
    writeLanguage(language);
    set((state) => ({
      language,
      description:
        state.description === DEFAULT_DESCRIPTION_ZH || state.description === DEFAULT_DESCRIPTION_EN
          ? language === "en"
            ? DEFAULT_DESCRIPTION_EN
            : DEFAULT_DESCRIPTION_ZH
          : state.description,
    }));
  },
  setSelectedFeatureId: (selectedFeatureId) => set({ selectedFeatureId }),
  setChatMessage: (chatMessage) => set({ chatMessage }),
  addEvents: (items) =>
    set((state) => ({ events: [...items, ...state.events].slice(0, 120) })),
  clearEvents: () => set({ events: [] }),
  addProcessStep: (step) =>
    set((state) => {
      const exists = state.processSteps.some((item) => item.id === step.id);
      if (exists) {
        return { processSteps: state.processSteps.map((item) => (item.id === step.id ? step : item)) };
      }
      return { processSteps: [...state.processSteps, step] };
    }),
  setProcessSteps: (processSteps) => set({ processSteps }),
  setBusy: (busy) => set({ busy }),
  setError: (error) => set({ error }),
  setBackendState: (backendState) => set({ backendState }),
  setRecentProjects: (recentProjects) => set({ recentProjects }),
  setStartupMode: (startupMode) => set({ startupMode }),
  setShowStartup: (showStartup) => set({ showStartup }),
  setSettingsDirty: (settingsDirty) => set({ settingsDirty }),
  setSettingsSaving: (settingsSaving) => set({ settingsSaving }),
  setSettingsNotice: (settingsNotice) => set({ settingsNotice }),
  setUi: (patch) =>
    set((state) => {
      const ui = { ...state.ui, ...patch };
      writeStoredUi(ui);
      return { ui };
    }),
  setAgentRunning: (agentRunning) => set({ agentRunning }),
  setAgentSteps: (agentSteps) => set({ agentSteps }),
  setAgentLastOp: (agentLastOp) => set({ agentLastOp }),
  setPendingApprovals: (pendingApprovals) => set({ pendingApprovals }),
  setChat: (chat) => set({ chat }),
  appendChatUser: (text, hasImage = false) =>
    set((state) => ({
      chat: [...state.chat, { id: nextChatId("user"), role: "user", text, hasImage, status: "done", tools: [], snapshots: [] }],
    })),
  appendChatAssistantDelta: (chunk) =>
    set((state) => {
      if (!chunk) {
        return {};
      }
      const last = state.chat[state.chat.length - 1];
      if (last && last.role === "assistant" && last.status === "streaming") {
        const updated = { ...last, text: last.text + chunk };
        return { chat: [...state.chat.slice(0, -1), updated] };
      }
      return {
        chat: [...state.chat, { id: nextChatId("assistant"), role: "assistant", text: chunk, hasImage: false, status: "streaming", tools: [], snapshots: [] }],
      };
    }),
  attachChatToolCard: (card) =>
    set((state) => {
      const last = state.chat[state.chat.length - 1];
      if (last && last.role === "assistant" && last.status === "streaming") {
        const updated = { ...last, tools: [...last.tools, card] };
        return { chat: [...state.chat.slice(0, -1), updated] };
      }
      return {
        chat: [...state.chat, { id: nextChatId("assistant"), role: "assistant", text: "", hasImage: false, status: "streaming", tools: [card], snapshots: [] }],
      };
    }),
  attachChatSnapshot: (url) =>
    set((state) => {
      if (!url) {
        return {};
      }
      const last = state.chat[state.chat.length - 1];
      if (last && last.role === "assistant" && last.status === "streaming") {
        const updated = { ...last, snapshots: [...last.snapshots, url] };
        return { chat: [...state.chat.slice(0, -1), updated] };
      }
      return {
        chat: [...state.chat, { id: nextChatId("assistant"), role: "assistant", text: "", hasImage: false, status: "streaming", tools: [], snapshots: [url] }],
      };
    }),
  finalizeChatAssistant: () =>
    set((state) => {
      const last = state.chat[state.chat.length - 1];
      if (!last || last.status === "done") {
        return {};
      }
      const updated = { ...last, status: "done" as const };
      return { chat: [...state.chat.slice(0, -1), updated] };
    }),
}));
