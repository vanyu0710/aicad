import { readLanguage } from "./store";
import { translate } from "./i18n";

export type ModelConfig = {
  vision_provider: string;
  vision_model: string;
  vision_base_url: string;
  vision_api_key: string;
  vision_protocol: string;
  planner_provider: string;
  planner_model: string;
  planner_base_url: string;
  planner_api_key: string;
  planner_protocol: string;
  operation_mode: "strict" | "smart";
  smart_fill_policy: "suggest_only" | "limited_fill" | "aggressive_fill" | "full_autonomous";
  force_real_api?: boolean;
};

export type ModelRole = "vision" | "planner";

export type AskOption = {
  label: string;
  description?: string;
};

export type AskQuestion = {
  id: string;
  question: string;
  header?: string;
  type: "single" | "multi" | "text";
  options?: AskOption[];
  required?: boolean;
  allowFreeText?: boolean;
};

export type Approval = {
  approval_id: string;
  kind: "destructive_op" | "destructive_fix" | "ask_user" | "plan_review";
  op: string;
  args: Record<string, unknown>;
  message: string;
  options?: Record<string, unknown>;
  context?: string;
};

export type KernelFeatureData = {
  id: string;
  type: string;
  name?: string;
  state?: string;
  parameters?: Record<string, unknown>;
  parent_id?: string | null;
  error?: string | null;
};

export type KernelFeatureTree = {
  graph: { nodes: Record<string, KernelFeatureData>; edges: Record<string, string[]> };
  op_history: Array<Record<string, unknown>>;
  narrative: string[];
  node_count: number;
};

export type ProcessStep = {
  id: string;
  stage: "upload" | "vision" | "planning" | "validation" | "chat_edit" | "cad" | "export";
  status: "pending" | "running" | "completed" | "failed" | "skipped" | "blocked";
  label: string;
  summary: string;
  detail: string;
  feature_id?: string | null;
  operation?: string | null;
  changed?: {
    before?: any;
    after?: any;
  } | null;
  started_at: string;
  completed_at?: string | null;
  error?: string | null;
  warnings: string[];
};

export type EvidenceItem = {
  key: string;
  value?: number | string | null;
  unit?: string;
  source?: string;
  feature_id?: string | null;
  dimension?: string | null;
  confirmed_by_user?: boolean;
  confidence?: number | null;
  conflict_with?: string[];
};

export type EvidenceConflictSource = {
  source?: string;
  value?: number | string | null;
  unit?: string;
  confirmed_by_user?: boolean;
  detail?: string;
};

export type EvidenceConflict = {
  id: string;
  key: string;
  feature_id?: string | null;
  parameter?: string | null;
  source_a: EvidenceConflictSource;
  source_b: EvidenceConflictSource;
  severity?: "blocking" | "warning";
  status?: "unresolved" | "resolved";
  resolved_value?: number | string | null;
  resolved_by?: "user" | "system" | null;
  resolved_at?: string | null;
  reason?: string;
  ambiguous?: boolean;
  affected_feature_ids?: string[];
};

export type EvidenceResolution = {
  conflict_id?: string | null;
  key?: string | null;
  feature_id?: string | null;
  selected_value: number | string;
  unit?: string;
};

export type EvidenceSet = {
  input_kind?: "text_only" | "image_only" | "mixed";
  items?: EvidenceItem[];
  conflicts?: string[];
  conflict_details?: EvidenceConflict[];
};

export type DesignIntentDetails = {
  part_family?: string;
  confidence?: number | null;
  function?: string;
  main_datum?: string;
  main_axis?: string;
  manufacturing_intent?: string;
  required_capabilities?: string[];
  unsupported_requirements?: string[];
  summary?: string;
};

export type ExecutionReport = {
  execution_ok: boolean;
  plan_complete: boolean;
  geometry_valid: boolean;
  production_ready: boolean;
  fallback_used: boolean;
  skipped_features: string[];
  failed_features: string[];
  assumption_count: number;
  completeness_score: number;
  engine: string;
  details: string[];
};

export type ModelTestResult = {
  ok: boolean;
  role: ModelRole;
  provider: string;
  protocol: string;
  model: string;
  message: string;
  diagnostics: {
    status_code?: number;
    content_type?: string;
    endpoint?: string;
    used_env_fallback: boolean;
  };
};

export type ProjectState = {
  project_id: string;
  name: string;
  created_at: string;
  updated_at: string;
  settings: ModelConfig;
  current: {
    id: string;
    feature_plan: {
      part_family: string;
      autonomy_policy?: ModelConfig["smart_fill_policy"];
      design_intent?: string;
      assumptions?: string[];
      assumption_details?: {
        feature_id: string;
        dimension: string;
        value?: number | string | null;
        reason: string;
        confidence?: number;
        confirmed_by_user: boolean;
      }[];
      base_feature: any;
      features: any[];
      unresolved: { feature: string; reason: string }[];
      self_checks?: {
        engine?: string;
        mode?: string;
        order?: string[];
        checks?: {
          id: string;
          feature_id?: string | null;
          status: "pass" | "warning" | "block";
          message: string;
        }[];
        summary?: { pass: number; warning: number; block: number };
      };
      design_intent_details?: DesignIntentDetails;
      evidence?: EvidenceSet;
      completeness?: Record<string, number | boolean | string>;
      design_review: {        warnings: string[];
        suggestions: string[];
        manufacturability: string[];
        standards: string[];
        blocking?: string[];
        requires_confirmation: boolean;
      };
    };
    artifacts: {
      run_id?: string;
      step?: string;
      stl?: string;
      obj?: string;
      report?: string;
      execution_report?: string;
    };
    questions: {
      id: string;
      text: string;
      feature_id?: string;
      dimension_refs?: string[];
      options: string[];
      required?: boolean;
      reason?: string;
      impact?: string;
      answer_type?: "text" | "number" | "choice";
      unit?: string;
      answer?: string;
    }[];
    execution_report?: ExecutionReport;
    report_markdown: string;    logs: string[];
    process: ProcessStep[];
  };
  history: unknown[];
  redo_stack: unknown[];
};

export function resolveApiRoot(env: any = (import.meta as any).env): string {
  return env?.VITE_API_ROOT || "";
}

export const API_ROOT = resolveApiRoot();

export function resolveWsRoot(env: any = (import.meta as any).env, apiRoot: string = API_ROOT): string {
  const override = env?.VITE_WS_ROOT;
  if (override) return override;
  if (apiRoot) return apiRoot.replace(/^http/, "ws");
  return `${window.location.protocol === "https:" ? "wss" : "ws"}://${window.location.host}`;
}

export async function createProject(name = "Varen CAD Project") {
  const response = await fetch(`${API_ROOT}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  return parseResponse<{ project_id: string; project: ProjectState }>(response);
}


export async function listProjects() {
  const response = await fetch(`${API_ROOT}/api/projects`);
  return parseResponse<{ projects: ProjectState[] }>(response);
}

export async function renameProject(projectId: string, name: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  return parseResponse<ProjectState>(response);
}

export async function deleteProject(projectId: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}`, { method: "DELETE" });
  return parseResponse<{ ok: boolean }>(response);
}
export async function fetchProject(projectId: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}`);
  return parseResponse<ProjectState>(response);
}

export async function updateProjectSettings(projectId: string, settings: ModelConfig) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  return parseResponse<ProjectState>(response);
}

export async function generateProject(projectId: string, payload: any) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseResponse<ProjectState>(response);
}

export async function patchFeature(projectId: string, featureId: string, payload: any, language: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/features/${featureId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, language }),
  });
  return parseResponse<ProjectState>(response);
}

export async function undo(projectId: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/undo`, { method: "POST" });
  return parseResponse<ProjectState>(response);
}

export async function redo(projectId: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/redo`, { method: "POST" });
  return parseResponse<ProjectState>(response);
}

export async function stopAgent(projectId: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/agent/stop`, { method: "POST" });
  return parseResponse<{ ok: boolean; stopped: boolean }>(response);
}

export async function resolveAgent(
  projectId: string,
  payload: { approval_id: string; action: "approve" | "reject" | "edit"; args_override?: Record<string, unknown> },
) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/agent/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseResponse<{ ok: boolean; resolved: { action: string; args?: Record<string, unknown> } }>(response);
}

export type AgentSessionMessage = {
  role: "user" | "assistant";
  text: string;
  has_image: boolean;
};

export type AgentSessionView = {
  status: "idle" | "running" | "waiting_approval";
  messages: AgentSessionMessage[];
};

export async function sendAgentMessage(
  projectId: string,
  payload: { text: string; image_data_url?: string | null; language: string; max_steps?: number },
) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/agent/message`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return parseResponse<{ ok: boolean; started?: boolean; queued?: boolean; project_id: string }>(response);
}

export async function fetchAgentSession(projectId: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/agent/session`);
  return parseResponse<AgentSessionView>(response);
}

export async function fetchKernelFeatureTree(projectId: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/kernel/feature_tree`);
  return parseResponse<KernelFeatureTree>(response);
}

export async function updateKernelFeature(projectId: string, featureId: string, newParams: Record<string, unknown>) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/kernel/update_feature`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ feature_id: featureId, new_params: newParams }),
  });
  return parseResponse<{ ok: boolean; project: ProjectState }>(response);
}

export async function deleteKernelFeature(projectId: string, featureId: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/kernel/delete_feature`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ feature_id: featureId }),
  });
  return parseResponse<{ ok: boolean; project: ProjectState }>(response);
}

export async function testModelConnection(role: ModelRole, config: ModelConfig, language: string) {
  const response = await fetch(`${API_ROOT}/api/model/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, config, language }),
  });
  return parseResponse<ModelTestResult>(response);
}

async function parseResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();
  let data: any;
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    throw new Error(translate(readLanguage(), "api.error.non_json", { status: response.status }));
  }
  if (!response.ok) {
    throw new Error(data?.detail || data?.message || translate(readLanguage(), "api.error.request", { status: response.status }));
  }
  return data as T;
}

export function artifactUrl(runId: string | undefined, kind: "step" | "stl" | "obj" | "report" | "execution_report") {
  if (!runId) {
    return "";
  }
  return `${API_ROOT}/api/artifacts/${runId}/${kind}`;
}
