import { create } from "zustand";
import type { ModelConfig, ProjectState } from "./api";

export type ManagerTab = "feature" | "property" | "configuration";
export type TaskTab = "assistant" | "review" | "logs" | "plan" | "export";
export type StartupMode = "always" | "first" | "off";

export const STARTUP_KEY = "mechcad_startup_mode";
export const STARTUP_SEEN_KEY = "mechcad_startup_seen";

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
  leftCollapsed: boolean;
  rightCollapsed: boolean;
  leftWidth: number;
  rightWidth: number;
  settingsOpen: boolean;
  commandTab: string;
};

type AppState = {
  project: ProjectState | null;
  description: string;
  imageFile: File | null;
  settings: ModelConfig;
  selectedFeatureId: string;
  chatMessage: string;
  events: string[];
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
  setProject: (project: ProjectState | null) => void;
  setDescription: (value: string) => void;
  setImageFile: (file: File | null) => void;
  setSettings: (settings: ModelConfig) => void;
  setSelectedFeatureId: (featureId: string) => void;
  setChatMessage: (value: string) => void;
  addEvents: (items: string[]) => void;
  clearEvents: () => void;
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
};

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
  description: "一件带孔或带槽的机械零件，请按整体到细节规划。已知尺寸请直接写明，未知尺寸请留给系统提问。",
  imageFile: null,
  settings: { ...DEFAULT_SETTINGS },
  selectedFeatureId: "",
  chatMessage: "",
  events: [],
  busy: false,
  error: "",
  backendState: "connected",
  recentProjects: [],
  startupMode: readStartupMode(),
  showStartup: shouldShowStartup(),
  settingsDirty: false,
  settingsSaving: false,
  settingsNotice: "",
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
  setProject: (project) => set({ project }),
  setDescription: (description) => set({ description }),
  setImageFile: (imageFile) => set({ imageFile }),
  setSettings: (settings) => set({ settings }),
  setSelectedFeatureId: (selectedFeatureId) => set({ selectedFeatureId }),
  setChatMessage: (chatMessage) => set({ chatMessage }),
  addEvents: (items) =>
    set((state) => ({ events: [...items, ...state.events].slice(0, 120) })),
  clearEvents: () => set({ events: [] }),
  setBusy: (busy) => set({ busy }),
  setError: (error) => set({ error }),
  setBackendState: (backendState) => set({ backendState }),
  setRecentProjects: (recentProjects) => set({ recentProjects }),
  setStartupMode: (startupMode) => set({ startupMode }),
  setShowStartup: (showStartup) => set({ showStartup }),
  setSettingsDirty: (settingsDirty) => set({ settingsDirty }),
  setSettingsSaving: (settingsSaving) => set({ settingsSaving }),
  setSettingsNotice: (settingsNotice) => set({ settingsNotice }),
  setUi: (patch) => set((state) => ({ ui: { ...state.ui, ...patch } })),
}));
