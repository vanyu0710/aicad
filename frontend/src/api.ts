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
  smart_fill_policy: "suggest_only" | "limited_fill" | "aggressive_fill";
  force_real_api?: boolean;
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
      base_feature: any;
      features: any[];
      unresolved: { feature: string; reason: string }[];
      design_review: {
        warnings: string[];
        suggestions: string[];
        manufacturability: string[];
        standards: string[];
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
    questions: { id: string; text: string; feature_id?: string; options: string[] }[];
    report_markdown: string;
    logs: string[];
  };
  history: unknown[];
  redo_stack: unknown[];
};

const API_ROOT = "";

export async function createProject(name = "MechCAD Project") {
  const response = await fetch(`${API_ROOT}/api/projects`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  return (await response.json()) as { project_id: string; project: ProjectState };
}

export async function fetchProject(projectId: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}`);
  return (await response.json()) as ProjectState;
}

export async function generateProject(projectId: string, payload: any) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return (await response.json()) as ProjectState;
}

export async function chatProject(projectId: string, message: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  return (await response.json()) as ProjectState;
}

export async function patchFeature(projectId: string, featureId: string, payload: any) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/features/${featureId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return (await response.json()) as ProjectState;
}

export async function undo(projectId: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/undo`, { method: "POST" });
  return (await response.json()) as ProjectState;
}

export async function redo(projectId: string) {
  const response = await fetch(`${API_ROOT}/api/projects/${projectId}/redo`, { method: "POST" });
  return (await response.json()) as ProjectState;
}

export function artifactUrl(runId: string | undefined, kind: "step" | "stl" | "obj" | "report" | "execution_report") {
  if (!runId) {
    return "";
  }
  return `${API_ROOT}/api/artifacts/${runId}/${kind}`;
}
