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
      design_review: {
        warnings: string[];
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
    report_markdown: string;
    logs: string[];
  };
  history: unknown[];
  redo_stack: unknown[];
};

export const API_ROOT = (import.meta as any).env?.VITE_API_ROOT || "http://127.0.0.1:8001";

export async function createProject(name = "MechCAD Project") {
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

export async function chatProject(projectId: string, message: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  return parseResponse<ProjectState>(response);
}

export async function patchFeature(projectId: string, featureId: string, payload: any) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/features/${featureId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
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

export async function testModelConnection(role: ModelRole, config: ModelConfig) {
  const response = await fetch(`${API_ROOT}/api/model/test`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role, config }),
  });
  return parseResponse<ModelTestResult>(response);
}

async function parseResponse<T>(response: Response): Promise<T> {
  const raw = await response.text();
  let data: any;
  try {
    data = raw ? JSON.parse(raw) : {};
  } catch {
    throw new Error(`后端返回了非 JSON（HTTP ${response.status}）。请确认后端地址和服务状态。`);
  }
  if (!response.ok) {
    throw new Error(data?.detail || data?.message || `请求失败（HTTP ${response.status}）`);
  }
  return data as T;
}

export function artifactUrl(runId: string | undefined, kind: "step" | "stl" | "obj" | "report" | "execution_report") {
  if (!runId) {
    return "";
  }
  return `${API_ROOT}/api/artifacts/${runId}/${kind}`;
}
