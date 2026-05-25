import { API_BASE_URL } from "./client";

export interface UserToolStep {
  tool_name: string;
  input: Record<string, unknown>;
  continue_on_error?: boolean;
}

export interface UserToolDefinition {
  tool_id: string;
  display_name: string;
  description: string;
  input_schema: Record<string, unknown>;
  steps: UserToolStep[];
  implementation_type?: string;
  created_at?: string;
  updated_at?: string;
  enabled?: boolean;
}

function authHeaders(): HeadersInit {
  const token = localStorage.getItem("astro_token");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const detail = typeof payload?.detail === "string" ? payload.detail : response.statusText;
    throw new Error(detail || `Request failed with ${response.status}`);
  }
  return payload as T;
}

export async function listUserTools(): Promise<UserToolDefinition[]> {
  const response = await fetch(`${API_BASE_URL}/api/user-tools`, {
    headers: authHeaders(),
  });
  const data = await parseJsonResponse<{ tools: UserToolDefinition[] }>(response);
  return data.tools || [];
}

export async function createUserTool(payload: {
  tool_id: string;
  display_name: string;
  description: string;
  input_schema: Record<string, unknown>;
  steps: UserToolStep[];
}): Promise<UserToolDefinition> {
  const response = await fetch(`${API_BASE_URL}/api/user-tools`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(payload),
  });
  return parseJsonResponse<UserToolDefinition>(response);
}

export async function deleteUserTool(toolId: string): Promise<{ deleted: boolean }> {
  const response = await fetch(`${API_BASE_URL}/api/user-tools/${encodeURIComponent(toolId)}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  return parseJsonResponse<{ deleted: boolean }>(response);
}
