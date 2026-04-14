import axios from "axios";
import { t } from "../i18n";

const DEFAULT_PRODUCTION_API_URL = "https://astro-backend-h4x1.onrender.com";
export const API_BASE_URL =
  import.meta.env.VITE_API_URL ||
  (import.meta.env.DEV || import.meta.env.MODE === "test" ? "http://localhost:8000" : DEFAULT_PRODUCTION_API_URL);

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000,
});

// Attach JWT token to requests if available
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("astro_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  if (typeof sessionStorage !== "undefined") {
    const trackingSession = sessionStorage.getItem("astro_tracking_session_id");
    const pageName = sessionStorage.getItem("astro_current_page");
    if (trackingSession) {
      config.headers["X-Tracking-Session"] = trackingSession;
    }
    if (pageName) {
      config.headers["X-Page-Name"] = pageName;
    }
  }
  return config;
});

// ── Auth API ──

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export interface UserProfile {
  id: string;
  username: string;
  email: string;
  subscription_tier: string;
  stripe_customer_id: string | null;
  display_name: string | null;
  avatar_url: string | null;
  google_linked: boolean;
}

export async function register(username: string, password: string): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>("/api/auth/register", { username, password });
  localStorage.setItem("astro_token", data.access_token);
  return data;
}

export async function login(username: string, password: string): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>("/api/auth/login", { username, password });
  localStorage.setItem("astro_token", data.access_token);
  return data;
}

export async function setupKeyLogin(setupKey: string): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>("/api/auth/setup-key-login", { setup_key: setupKey });
  localStorage.setItem("astro_token", data.access_token);
  return data;
}

export async function googleLogin(credential: string): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>("/api/auth/google", { credential });
  localStorage.setItem("astro_token", data.access_token);
  return data;
}

export function logout() {
  localStorage.removeItem("astro_token");
}

export function isAuthenticated(): boolean {
  return !!localStorage.getItem("astro_token");
}

export async function trackEvent(
  eventType: string,
  eventData: Record<string, unknown> = {},
  options?: { sessionId?: string; page?: string; durationMs?: number }
): Promise<void> {
  await api.post("/api/events/track", {
    event_type: eventType,
    event_data: eventData,
    session_id: options?.sessionId,
    page: options?.page,
    duration_ms: options?.durationMs,
  });
}

export async function getProfile(): Promise<UserProfile> {
  const { data } = await api.get<UserProfile>("/api/auth/me");
  return data;
}

export async function subscribe(tier: string): Promise<{ status: string; tier: string }> {
  const { data } = await api.post("/api/auth/subscribe", { tier });
  return data;
}

export interface UsageStats {
  runs_this_month: number;
  runs_limit: number | null;
  storage_used_gb: number;
  storage_limit: number | null;
}

export async function getUsageStats(): Promise<UsageStats> {
  const { data } = await api.get<UsageStats>("/api/auth/usage");
  return data;
}

// ── Data API ──

export interface SearchResult {
  source: string;
  object_id: string;
  name: string;
  ra: number;
  dec: number;
  object_type: string;
  magnitude: number | null;
  redshift: number | null;
  extra: Record<string, unknown>;
  error_type: string | null;
  z_source: string | null;
  photo_z: number | null;
  photo_z_err: number | null;
}

export interface FetchResult {
  source: string;
  object_id: string;
  fits_path: string;
  filename: string;
  file_id: string | null;
}

export interface FITSHeader {
  fits_path: string;
  headers: Array<{
    hdu_index: number;
    cards: Array<{ key: string; value: string; comment: string }>;
  }>;
  hdus: Array<{
    index: number;
    name: string;
    type: string;
    shape?: number[];
    dtype?: string;
    columns?: Array<{ name: string; format: string }>;
  }>;
}

export interface FITSSpectrum {
  type: string;
  columns: string[];
  data: Record<string, number[]>;
  shape?: number[];
  min?: number;
  max?: number;
  mean?: number;
  thumbnail?: number[][];
}

export async function searchData(
  q: string,
  sources = "sdss,gaia,simbad",
  ra?: number,
  dec?: number,
  radius = 0.1,
  signal?: AbortSignal,
): Promise<SearchResult[]> {
  const params: Record<string, string | number> = { q, sources, radius };
  if (ra !== undefined) params.ra = ra;
  if (dec !== undefined) params.dec = dec;
  try {
    const { data } = await api.get<SearchResult[]>("/api/data/search", { params, signal });
    return data;
  } catch (err: unknown) {
    if (axios.isAxiosError(err)) {
      if (err.response?.data && typeof err.response.data === "object" && "detail" in err.response.data) {
        throw new Error(String(err.response.data.detail));
      }
      if (err.code === "ECONNABORTED") {
        throw new Error(t("error.request_timed_out"));
      }
      if (err.message === "Network Error") {
        try {
          const { data } = await api.get<SearchResult[]>("/api/data/search", { params });
          return data;
        } catch {
          const sourceList = sources.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
          if (sourceList.length > 0 && sourceList.every((s) => s === "mast" || s === "jwst")) {
            throw new Error(t("error.mast_jwst_failed"));
          }
          throw new Error(t("error.search_failed"));
        }
      }
    }
    throw err;
  }
}

// ── Advanced Search ──

export interface AdvancedSearchRequest {
  ra?: number;
  dec?: number;
  radius?: number;
  redshift_min?: number;
  redshift_max?: number;
  spectral_line?: string;
  wavelength_min?: number;
  wavelength_max?: number;
  observation_type?: string;
  object_type?: string;
  instrument?: string;
  sources?: string[];
  natural_query?: string;
}

export interface AdvancedSearchMeta {
  parsed_filters: Record<string, unknown>;
  suggested_sources: string[];
  matched_keywords: string[];
  observed_freq_min_ghz: number | null;
  observed_freq_max_ghz: number | null;
  observed_wavelength_min_um: number | null;
  observed_wavelength_max_um: number | null;
}

export interface AdvancedSearchResponse {
  results: SearchResult[];
  meta: AdvancedSearchMeta;
}

export interface SpectralLineInfo {
  key: string;
  name: string;
  rest_wavelength_um: number;
  rest_freq_ghz?: number;
}

export async function advancedSearch(
  req: AdvancedSearchRequest
): Promise<AdvancedSearchResponse> {
  const { data } = await api.post<AdvancedSearchResponse>(
    "/api/data/advanced-search",
    req
  );
  return data;
}

export async function getSpectralLines(): Promise<SpectralLineInfo[]> {
  const { data } = await api.get<SpectralLineInfo[]>("/api/data/spectral-lines");
  return data;
}

export async function fetchObject(
  source: string,
  objectId: string
): Promise<FetchResult> {
  const { data } = await api.get<FetchResult>(
    `/api/data/${source}/${encodeURIComponent(objectId)}`
  );
  return data;
}

export async function getWorkspace(): Promise<Record<string, unknown>[]> {
  const { data } = await api.get("/api/data/workspace");
  return data;
}

export async function getFITSHeader(fitsPath: string, hdu?: number): Promise<FITSHeader> {
  const params: Record<string, string | number> = { fits_path: fitsPath };
  if (hdu !== undefined) params.hdu = hdu;
  const { data } = await api.get<FITSHeader>("/api/data/fits-header", { params });
  return data;
}

export async function getFITSSpectrum(fitsPath: string, maxPoints = 2000): Promise<FITSSpectrum> {
  const { data } = await api.get<FITSSpectrum>("/api/data/fits-spectrum", {
    params: { fits_path: fitsPath, max_points: maxPoints },
  });
  return data;
}

// ── Object Detail API ──

export interface ObjectDetail {
  name: string;
  ra: number;
  dec: number;
  object_type: string;
  object_type_long: string;
  redshift: number | null;
  radial_velocity: number | null;
  spectral_type: string | null;
  morphology: string | null;
  parallax: number | null;
  proper_motion_ra: number | null;
  proper_motion_dec: number | null;
  cross_ids: Array<{ name: string }>;
  surveys: Array<{ source: string; has_data: boolean; count: number }>;
  references: Array<{ bibcode: string; title: string; authors: string[]; year: string }>;
  all_data: Record<string, SearchResult[]>;
}

export async function getObjectDetail(name: string, ra?: number, dec?: number): Promise<ObjectDetail> {
  const params: Record<string, string | number> = { name };
  if (ra !== undefined) params.ra = ra;
  if (dec !== undefined) params.dec = dec;
  const { data } = await api.get<ObjectDetail>("/api/data/object-detail", { params });
  return data;
}

// ── Spectrum Analysis API ──

export interface SpectrumAnalysis {
  peaks: Array<{ wavelength: number; flux: number; snr: number; is_emission: boolean }>;
  redshift_auto: { best_z: number; z_error: number; confidence: number; matched_lines: Array<{ line: string; rest_wavelength: number; observed_wavelength: number }> } | null;
  continuum_shape: string;
  ai_classification: string;
  ai_confidence: string;
  ai_redshift: { value: number; uncertainty: number } | null;
  ai_lines: Array<{ name: string; rest_wavelength: number; observed_wavelength: number; type: string; strength: string; ew: number | null }>;
  ai_special_features: string[];
  ai_summary: string;
  ai_narrative: string;
  ai_next_steps: string[];
}

export async function analyzeSpectrum(fitsPath: string, apiKey?: string): Promise<SpectrumAnalysis> {
  const body: Record<string, unknown> = { fits_path: fitsPath };
  if (apiKey) body.api_key = apiKey;
  const { data } = await api.post<SpectrumAnalysis>("/api/data/fits/analyze", body);
  return data;
}

// ── FITS Upload & Browse API ──

export interface FITSFileInfo {
  id: string;
  filename: string;
  fits_path: string;
  size_bytes: number;
  source: string;
  object_id: string;
  created_at: string | null;
  metadata: Record<string, unknown>;
}

export async function uploadFITS(
  file: File,
  objectId?: string,
  onProgress?: (percent: number) => void,
): Promise<FITSFileInfo> {
  const form = new FormData();
  form.append("file", file);
  const params: Record<string, string> = {};
  if (objectId) params.object_id = objectId;
  const { data } = await api.post<FITSFileInfo>("/api/data/fits/upload", form, {
    params,
    onUploadProgress: (e) => {
      if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100));
    },
  });
  return data;
}

export async function browseFITS(source?: string): Promise<FITSFileInfo[]> {
  const params: Record<string, string> = {};
  if (source) params.source = source;
  const { data } = await api.get<FITSFileInfo[]>("/api/data/fits/browse", { params });
  return data;
}

export async function deleteFITS(fileId: string): Promise<void> {
  await api.delete(`/api/data/fits/${fileId}`);
}

export function downloadFITSUrl(fitsPath: string): string {
  const base = API_BASE_URL;
  return `${base}/api/data/fits/download?fits_path=${encodeURIComponent(fitsPath)}`;
}

export async function uploadGeneralFile(file: File): Promise<{ id: string; filename: string; path: string; size_bytes: number }> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post("/api/data/files/upload", form);
  return data;
}

export function downloadFileUrl(path: string): string {
  const base = API_BASE_URL;
  return `${base}/api/data/files/download?path=${encodeURIComponent(path)}`;
}

// ── Pipeline API ──

export interface NodeType {
  type: string;
  label: string;
  description: string;
  inputs: number;
  outputs: number;
}

export interface PipelineTemplate {
  id: string;
  name: string;
  description: string;
  dag: { nodes: DagNode[]; edges: DagEdge[] };
}

export interface DagNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: Record<string, unknown>;
}

export interface DagEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string;
}

export interface RunResponse {
  run_id: string;
  status: string;
  results?: Record<string, unknown>;
}

export async function getNodeTypes(): Promise<NodeType[]> {
  const { data } = await api.get<NodeType[]>("/api/pipeline/nodes/types");
  return data;
}

export async function getTemplates(): Promise<PipelineTemplate[]> {
  const { data } = await api.get<PipelineTemplate[]>("/api/pipeline/templates");
  return data;
}

export async function runPipeline(
  dag: { nodes: DagNode[]; edges: DagEdge[] },
  inputDataId: string,
  asyncMode = true
): Promise<RunResponse> {
  const { data } = await api.post<RunResponse>("/api/pipeline/run", {
    dag,
    input_data_id: inputDataId,
  }, {
    params: { async_mode: asyncMode },
  });
  return data;
}

// ── Pipeline Version API ──

export interface VersionSummary {
  id: string;
  version: number;
  change_note: string;
  created_at: string;
}

export interface VersionDetail {
  id: string;
  version: number;
  change_note: string;
  dag: { nodes: DagNode[]; edges: DagEdge[] };
  created_at: string;
}

export interface DagDiffResult {
  added_nodes: DagNode[];
  removed_nodes: DagNode[];
  modified_nodes: Array<{ id: string; old: DagNode; new: DagNode }>;
  added_edges: DagEdge[];
  removed_edges: DagEdge[];
}

export async function saveTemplateVersion(
  templateId: string,
  dag: { nodes: DagNode[]; edges: DagEdge[] },
  changeNote: string
): Promise<VersionSummary> {
  const { data } = await api.post<VersionSummary>(
    `/api/pipeline/templates/${templateId}/versions`,
    { dag, change_note: changeNote }
  );
  return data;
}

export async function getTemplateVersions(
  templateId: string
): Promise<VersionSummary[]> {
  const { data } = await api.get<VersionSummary[]>(
    `/api/pipeline/templates/${templateId}/versions`
  );
  return data;
}

export async function getTemplateVersion(
  templateId: string,
  versionId: string
): Promise<VersionDetail> {
  const { data } = await api.get<VersionDetail>(
    `/api/pipeline/templates/${templateId}/versions/${versionId}`
  );
  return data;
}

export async function getTemplateDiff(
  templateId: string,
  v1: string,
  v2: string
): Promise<DagDiffResult> {
  const { data } = await api.get<DagDiffResult>(
    `/api/pipeline/templates/${templateId}/diff`,
    { params: { v1, v2 } }
  );
  return data;
}

export async function batchRunPipeline(
  dag: { nodes: DagNode[]; edges: DagEdge[] },
  inputDataIds: string[],
): Promise<{ results: Array<Record<string, unknown>>; total: number; succeeded: number; failed: number }> {
  const { data } = await api.post("/api/pipeline/batch-run", { dag, input_data_ids: inputDataIds });
  return data;
}

export async function getPipelineRun(runId: string): Promise<Record<string, unknown>> {
  const { data } = await api.get(`/api/pipeline/${runId}`);
  return data;
}

export async function getNodeResult(runId: string, nodeId: string): Promise<Record<string, unknown>> {
  const { data } = await api.get(`/api/pipeline/runs/${runId}/nodes/${nodeId}`);
  return data;
}

// ── WebSocket ──

export function connectPipelineWS(
  runId: string,
  onMessage: (data: Record<string, unknown>) => void,
  onClose?: () => void
): WebSocket {
  const baseUrl = API_BASE_URL;
  const wsUrl = baseUrl.replace(/^http/, "ws");
  const ws = new WebSocket(`${wsUrl}/ws/pipeline/${runId}`);

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onMessage(data);
    } catch {
      // ignore parse errors
    }
  };

  // Keep alive with pings
  const pingInterval = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send("ping");
    } else {
      clearInterval(pingInterval);
    }
  }, 30000);

  ws.onclose = () => {
    clearInterval(pingInterval);
    onClose?.();
  };

  return ws;
}

// ── Workspace / Data Management API ──

export interface BatchTarget {
  name: string;
  ra?: number;
  dec?: number;
}

export async function batchSearch(
  targets: BatchTarget[],
  sources = ["sdss", "gaia", "simbad"],
  radius = 0.1
): Promise<Record<string, SearchResult[]>> {
  const { data } = await api.post("/api/workspace/batch-search", {
    targets,
    sources,
    radius,
  });
  return data.results;
}

export async function uploadBatchTargets(file: File): Promise<BatchTarget[]> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post("/api/workspace/batch-upload", form);
  return data.targets;
}

export async function addTag(fileId: string, tag: string): Promise<{ id: string; tag: string }> {
  const { data } = await api.post(`/api/workspace/files/${fileId}/tags`, { tag });
  return data;
}

export async function getTags(fileId: string): Promise<Array<{ id: string; tag: string }>> {
  const { data } = await api.get(`/api/workspace/files/${fileId}/tags`);
  return data;
}

export async function deleteTag(fileId: string, tagId: string): Promise<void> {
  await api.delete(`/api/workspace/files/${fileId}/tags/${tagId}`);
}

export async function addNote(fileId: string, content: string): Promise<{ id: string }> {
  const { data } = await api.post(`/api/workspace/files/${fileId}/notes`, { content });
  return data;
}

export async function getNotes(
  fileId: string
): Promise<Array<{ id: string; content: string; created_at: string }>> {
  const { data } = await api.get(`/api/workspace/files/${fileId}/notes`);
  return data;
}

export async function exportData(
  fileId: string,
  format: "csv" | "votable" | "latex" = "csv"
): Promise<Blob> {
  const { data } = await api.get(`/api/workspace/export/${fileId}`, {
    params: { format },
    responseType: "blob",
  });
  return data;
}

// ── Pipeline Export API ──

export async function exportRunCSV(runId: string): Promise<Blob> {
  const { data } = await api.get(`/api/export/run/${runId}/csv`, {
    responseType: "blob",
  });
  return data;
}

export async function exportRunVOTable(runId: string): Promise<Blob> {
  const { data } = await api.get(`/api/export/run/${runId}/votable`, {
    responseType: "blob",
  });
  return data;
}

export async function exportRunPDF(runId: string): Promise<Blob> {
  const { data } = await api.get(`/api/export/run/${runId}/pdf`, {
    responseType: "blob",
  });
  return data;
}

// ── Saved Objects / Bookmarks ──

export interface SavedObjectInfo {
  id: string;
  name: string;
  ra: number;
  dec: number;
  object_type: string;
  source: string;
  redshift: number | null;
  notes: string | null;
  project: string;
  created_at: string | null;
}

export async function saveObject(obj: {
  name: string; ra?: number; dec?: number; object_type?: string;
  source?: string; redshift?: number; notes?: string; project?: string;
}): Promise<{ id: string }> {
  const { data } = await api.post("/api/data/saved-objects", obj);
  return data;
}

export async function listSavedObjects(project?: string): Promise<SavedObjectInfo[]> {
  const params: Record<string, string> = {};
  if (project) params.project = project;
  const { data } = await api.get<SavedObjectInfo[]>("/api/data/saved-objects", { params });
  return data;
}

export async function listProjects(): Promise<string[]> {
  const { data } = await api.get<string[]>("/api/data/saved-objects/projects");
  return data;
}

export async function deleteSavedObject(id: string): Promise<void> {
  await api.delete(`/api/data/saved-objects/${id}`);
}

export async function batchLookup(names: string[]): Promise<{
  results: Array<Record<string, unknown>>;
  total: number;
  found: number;
}> {
  const { data } = await api.post("/api/data/batch-lookup", { names });
  return data;
}

// ── Analysis Report Export ──

export async function exportAnalysisMarkdown(
  analysis: Record<string, unknown>,
  objectName?: string,
  fitsPath?: string,
): Promise<Blob> {
  const { data } = await api.post("/api/export/report/markdown", {
    analysis,
    object_name: objectName || "",
    fits_path: fitsPath || "",
  }, { responseType: "blob" });
  return data;
}

export async function exportSearchNotebook(
  query: string,
  results: Array<Record<string, unknown>>,
): Promise<Blob> {
  const { data } = await api.post("/api/export/notebook/from-search", {
    query,
    results,
  }, { responseType: "blob" });
  return data;
}

// ── Chat → Markdown Export ──

export async function exportChatMarkdown(
  messages: Array<{ role: string; content: string; actions?: unknown[] }>,
  title?: string,
): Promise<Blob> {
  const { data } = await api.post("/api/export/report/from-chat", {
    messages,
    title: title || "AI Research Chat",
  }, { responseType: "blob" });
  return data;
}

// ── Chat → Notebook Export ──

export async function exportChatNotebook(
  messages: Array<{ role: string; content: string; actions?: unknown[] }>,
  title?: string,
): Promise<Blob> {
  const { data } = await api.post("/api/export/notebook/from-chat", {
    messages,
    title: title || "AI Research Session",
  }, { responseType: "blob" });
  return data;
}

// ── Chat → LaTeX Export ──

export async function exportChatLatex(
  messages: Array<{ role: string; content: string; actions?: unknown[] }>,
  title?: string,
  abstract?: string,
  author?: string,
): Promise<Blob> {
  const { data } = await api.post("/api/export/report/latex", {
    messages,
    title: title || "Standard Astro Research Report",
    abstract: abstract || "",
    author: author || "Standard Astro User",
  }, { responseType: "blob" });
  return data;
}

// ── Chat → BibTeX Export ──

export async function exportChatBibTeX(
  messages: Array<{ role: string; content: string; actions?: unknown[] }>,
): Promise<Blob> {
  const { data } = await api.post("/api/export/report/bibtex", {
    messages,
  }, { responseType: "blob" });
  return data;
}

// ── Workflow Export ──

export async function exportWorkflowPython(
  toolCalls: Array<{ tool: string; input: Record<string, unknown>; result?: unknown }>,
  title?: string,
): Promise<Blob> {
  const { data } = await api.post("/api/export/workflow/python", {
    tool_calls: toolCalls,
    title: title || "AI Research Workflow",
  }, { responseType: "blob" });
  return data;
}

// ── Integration API ──

export async function sampStatus(): Promise<{
  connected: boolean;
  hub_url: string | null;
  registered_clients: string[];
}> {
  const { data } = await api.get("/api/integration/samp/status");
  return data;
}

export async function sampSend(
  fitsPath: string,
  messageType = "table.load.fits"
): Promise<{ sent: boolean }> {
  const { data } = await api.post("/api/integration/samp/send", {
    fits_path: fitsPath,
    message_type: messageType,
  });
  return data;
}

export async function convertToVOTable(fitsPath: string): Promise<Blob> {
  const { data } = await api.get("/api/integration/votable/convert", {
    params: { fits_path: fitsPath },
    responseType: "blob",
  });
  return data;
}

export async function exportJupyter(
  templateId?: string,
  runId?: string
): Promise<Blob> {
  const { data } = await api.post(
    "/api/integration/jupyter/export",
    { template_id: templateId, run_id: runId },
    { responseType: "blob" }
  );
  return data;
}

export interface ADQLResult {
  columns: string[];
  data: Record<string, (number | string | null)[]>;
  row_count: number;
  service: string;
}

export async function adqlQuery(
  query: string,
  service = "gaia"
): Promise<ADQLResult> {
  const { data } = await api.post<ADQLResult>("/api/integration/adql/query", {
    query,
    service,
  });
  return data;
}

export async function listADQLServices(): Promise<
  Array<{ id: string; name: string; url: string; description: string }>
> {
  const { data } = await api.get("/api/integration/adql/services");
  return data;
}

// ── WCS Grid API ──

export interface WCSGridLine {
  type: "ra" | "dec";
  value: number;
  points: [number, number][];
}

export interface WCSGridLabel {
  text: string;
  x: number;
  y: number;
  type: "ra" | "dec";
}

export interface WCSGridData {
  has_wcs: boolean;
  ra_range?: [number, number];
  dec_range?: [number, number];
  image_shape?: [number, number];
  grid_lines: WCSGridLine[];
  labels: WCSGridLabel[];
}

export async function getFITSWCS(fitsPath: string, gridSteps = 10): Promise<WCSGridData> {
  const { data } = await api.get<WCSGridData>("/api/data/fits-wcs", {
    params: { fits_path: fitsPath, grid_steps: gridSteps },
  });
  return data;
}

// ── Scheduler API ──

export interface ScheduleItem {
  id: string;
  name: string;
  cron_expr: string;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string | null;
}

export async function createSchedule(
  name: string,
  dag: { nodes: DagNode[]; edges: DagEdge[] },
  inputDataId: string,
  cronExpr: string
): Promise<ScheduleItem> {
  const { data } = await api.post<ScheduleItem>("/api/scheduler/schedules", {
    name,
    dag,
    input_data_id: inputDataId,
    cron_expr: cronExpr,
  });
  return data;
}

export async function listSchedules(): Promise<ScheduleItem[]> {
  const { data } = await api.get<ScheduleItem[]>("/api/scheduler/schedules");
  return data;
}

export async function toggleSchedule(scheduleId: string): Promise<{ id: string; enabled: boolean }> {
  const { data } = await api.patch(`/api/scheduler/schedules/${scheduleId}`);
  return data;
}

export async function deleteSchedule(scheduleId: string): Promise<void> {
  await api.delete(`/api/scheduler/schedules/${scheduleId}`);
}

// ── Team API ──

export interface TeamMember {
  id: string;
  user_id: string;
  email: string;
  role: string;
  created_at: string | null;
}

export interface SharedPipelineItem {
  id: string;
  template_id: string;
  template_name: string;
  shared_by: string;
  shared_by_email: string;
  permission: string;
  created_at: string | null;
}

export interface PipelineCommentItem {
  id: string;
  user_id: string;
  email: string;
  content: string;
  created_at: string | null;
}

export interface SharedDatasetItem {
  id: string;
  data_file_id: string;
  source: string;
  object_id: string;
  shared_by: string;
  shared_by_email: string;
  created_at: string | null;
}

export async function inviteTeamMember(
  email: string,
  role: string = "member"
): Promise<TeamMember> {
  const { data } = await api.post<TeamMember>("/api/team/invite", { email, role });
  return data;
}

export async function getTeamMembers(): Promise<TeamMember[]> {
  const { data } = await api.get<TeamMember[]>("/api/team/members");
  return data;
}

export async function removeTeamMember(memberId: string): Promise<void> {
  await api.delete(`/api/team/members/${memberId}`);
}

export async function updateMemberRole(
  memberId: string,
  role: string
): Promise<TeamMember> {
  const { data } = await api.patch<TeamMember>(`/api/team/members/${memberId}`, { role });
  return data;
}

export async function sharePipeline(
  templateId: string,
  userId: string,
  permission: string = "view"
): Promise<SharedPipelineItem> {
  const { data } = await api.post<SharedPipelineItem>(
    `/api/team/pipelines/${templateId}/share`,
    { user_id: userId, permission }
  );
  return data;
}

export async function getSharedPipelines(): Promise<SharedPipelineItem[]> {
  const { data } = await api.get<SharedPipelineItem[]>("/api/team/pipelines/shared");
  return data;
}

export async function addPipelineComment(
  templateId: string,
  content: string
): Promise<PipelineCommentItem> {
  const { data } = await api.post<PipelineCommentItem>(
    `/api/team/pipelines/${templateId}/comments`,
    { content }
  );
  return data;
}

export async function getPipelineComments(
  templateId: string
): Promise<PipelineCommentItem[]> {
  const { data } = await api.get<PipelineCommentItem[]>(
    `/api/team/pipelines/${templateId}/comments`
  );
  return data;
}

export async function shareDataset(
  fileId: string,
  userId: string
): Promise<SharedDatasetItem> {
  const { data } = await api.post<SharedDatasetItem>(
    `/api/team/datasets/${fileId}/share`,
    { user_id: userId }
  );
  return data;
}

export async function getSharedDatasets(): Promise<SharedDatasetItem[]> {
  const { data } = await api.get<SharedDatasetItem[]>("/api/team/datasets/shared");
  return data;
}

// ── Friends API ──

export interface FriendItem {
  id: string;
  user_id: string;
  email: string;
  status: string;
  direction: string;
  created_at: string | null;
}

export async function sendFriendRequest(email: string): Promise<FriendItem> {
  const { data } = await api.post<FriendItem>("/api/team/friends/request", { email });
  return data;
}

export async function getFriends(): Promise<FriendItem[]> {
  const { data } = await api.get<FriendItem[]>("/api/team/friends");
  return data;
}

export async function acceptFriend(friendshipId: string): Promise<FriendItem> {
  const { data } = await api.post<FriendItem>(`/api/team/friends/${friendshipId}/accept`);
  return data;
}

export async function rejectFriend(friendshipId: string): Promise<void> {
  await api.post(`/api/team/friends/${friendshipId}/reject`);
}

export async function removeFriend(friendshipId: string): Promise<void> {
  await api.delete(`/api/team/friends/${friendshipId}`);
}

// ── Search History API ──

export interface SearchHistoryItem {
  id: string;
  query: string;
  sources: string;
  result_count: number;
  params: Record<string, unknown> | null;
  created_at: string | null;
}

export async function getSearchHistory(): Promise<SearchHistoryItem[]> {
  const { data } = await api.get<SearchHistoryItem[]>("/api/team/search-history");
  return data;
}

export async function deleteSearchHistoryItem(entryId: string): Promise<void> {
  await api.delete(`/api/team/search-history/${entryId}`);
}

export async function clearSearchHistory(): Promise<void> {
  await api.delete("/api/team/search-history");
}

// ── Shared Results API ──

export interface SharedResultItem {
  id: string;
  team_id: string;
  shared_by: string;
  shared_by_email: string;
  title: string;
  objects: Record<string, unknown>[];
  created_at: string | null;
}

export async function shareResults(
  teamId: string,
  title: string,
  objects: Record<string, unknown>[]
): Promise<SharedResultItem> {
  const { data } = await api.post<SharedResultItem>(
    `/api/team/${teamId}/shared-results`,
    { title, objects }
  );
  return data;
}

export async function getSharedResults(teamId: string): Promise<SharedResultItem[]> {
  const { data } = await api.get<SharedResultItem[]>(
    `/api/team/${teamId}/shared-results`
  );
  return data;
}

// ── Shared Notebooks API ──

export interface SharedNotebookItem {
  id: string;
  team_id: string;
  shared_by: string;
  shared_by_email: string;
  title: string;
  content: string;
  created_at: string | null;
}

export async function shareNotebook(
  teamId: string,
  title: string,
  content: string
): Promise<SharedNotebookItem> {
  const { data } = await api.post<SharedNotebookItem>(
    `/api/team/${teamId}/shared-notebooks`,
    { title, content }
  );
  return data;
}

export async function getSharedNotebooks(teamId: string): Promise<SharedNotebookItem[]> {
  const { data } = await api.get<SharedNotebookItem[]>(
    `/api/team/${teamId}/shared-notebooks`
  );
  return data;
}

// ── Team Activity API ──

export interface ActivityItem {
  id: string;
  user_id: string;
  user_email: string;
  action: string;
  summary: string;
  created_at: string | null;
}

export async function getTeamActivity(teamId: string): Promise<ActivityItem[]> {
  const { data } = await api.get<ActivityItem[]>(
    `/api/team/${teamId}/activity`
  );
  return data;
}

// ── Settings API ──

export interface KeyInfo {
  provider: string;
  name: string;
  masked_key: string;
}

export interface ProviderMeta {
  name: string;
  prefix: string;
}

export interface AllKeysResponse {
  keys: KeyInfo[];
  providers: Record<string, ProviderMeta>;
}

export async function getApiKeys(): Promise<AllKeysResponse> {
  const { data } = await api.get<AllKeysResponse>("/api/settings/api-keys");
  return data;
}

export async function saveApiKey(provider: string, key: string): Promise<{ saved: boolean; masked_key: string }> {
  const { data } = await api.put("/api/settings/api-keys", { provider, key });
  return data;
}

export async function deleteApiKey(provider: string): Promise<void> {
  await api.delete("/api/settings/api-keys", { data: { provider } });
}

// ── AI Chat API ──

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  actions?: unknown[];
}

export interface ChatAction {
  action: string;
  [key: string]: unknown;
}

export interface ChatResponse {
  reply: string;
  actions: ChatAction[];
}

// ── Chat Session Persistence ──

export interface ChatSessionSummary {
  id: string;
  title: string;
  message_count: number;
  updated_at: string;
}

export interface AnalysisValidationCheck {
  name: string;
  status: "PASS" | "WARN" | "FAIL";
  details: string;
  recommendation: string;
}

export interface AnalysisValidationResult {
  overall_status: "PASS" | "WARN" | "FAIL";
  score: number;
  checks: AnalysisValidationCheck[];
}

export interface PaperDraftResponse {
  id: string;
  paper_json: Record<string, unknown>;
  latex_source: string;
  bibtex: string;
  validation: AnalysisValidationResult;
}

export async function saveChatSession(
  messages: Array<{ role: string; content: string; actions?: unknown[] }>,
  sessionId?: string,
  title?: string,
): Promise<{ id: string }> {
  const { data } = await api.post("/api/chat/sessions/save", {
    session_id: sessionId,
    title: title || "New Chat",
    messages,
  });
  return data;
}

export async function listChatSessions(): Promise<ChatSessionSummary[]> {
  const { data } = await api.get<ChatSessionSummary[]>("/api/chat/sessions");
  return data;
}

export async function loadChatSession(sessionId: string): Promise<{
  id: string;
  title: string;
  messages: Array<{ role: string; content: string; actions?: unknown[] }>;
}> {
  const { data } = await api.get(`/api/chat/sessions/${sessionId}`);
  return data;
}

export async function deleteChatSession(sessionId: string): Promise<void> {
  await api.delete(`/api/chat/sessions/${sessionId}`);
}

export async function importChatSession(data: {
  title?: string;
  messages: Array<{ role: string; content: string }>;
}): Promise<{ id: string; title: string; message_count: number }> {
  const res = await api.post("/api/chat/sessions/import", data);
  return res.data;
}

export interface SessionShareItem {
  id: string;
  share_token: string;
  access_level: "view" | "fork" | "comment";
  expires_at: string | null;
  created_at: string | null;
}

export interface SessionCommentItem {
  id: string;
  user_id: string;
  target_type: string;
  target_id: string | null;
  content: string;
  parent_id: string | null;
  created_at: string | null;
  can_delete?: boolean;
}

export interface SessionSnapshotItem {
  id: string;
  name: string;
  created_at: string | null;
}

export interface SharedSessionPayload {
  share: {
    access_level: "view" | "fork" | "comment";
    expires_at: string | null;
  };
  session: {
    schema_version?: number;
    id: string;
    title: string;
    messages: Array<{ role: string; content: string; actions?: unknown[] }>;
    created_at: string | null;
    updated_at: string | null;
    paper_drafts?: Array<{
      id: string;
      journal_format: string;
      paper_json: Record<string, unknown>;
      latex_source: string;
      bibtex: string;
      validation: Record<string, unknown>;
      created_at: string | null;
      updated_at: string | null;
    }>;
    artifact_summary?: {
      message_count: number;
      python_blocks: number;
      pipeline_actions: number;
      figure_count: number;
    };
  };
  comments: SessionCommentItem[];
  can_fork: boolean;
  can_comment: boolean;
}

export interface SessionSnapshotDiff {
  added_messages: number;
  removed_messages: number;
  updated_title: boolean;
  titles: { a: string; b: string };
}

export async function createSessionShare(
  sessionId: string,
  accessLevel: "view" | "fork" | "comment" = "view",
  expiresHours?: number,
): Promise<{ id: string; share_url: string; share_token: string; access_level: string; expires_at: string | null }> {
  const { data } = await api.post(`/api/sessions/${sessionId}/share`, {
    access_level: accessLevel,
    expires_hours: expiresHours ?? null,
  });
  return data;
}

export async function listSessionShares(sessionId: string): Promise<SessionShareItem[]> {
  const { data } = await api.get<SessionShareItem[]>(`/api/sessions/${sessionId}/shares`);
  return data;
}

export async function revokeSessionShare(sessionId: string, shareId: string): Promise<void> {
  await api.delete(`/api/sessions/${sessionId}/share/${shareId}`);
}

export async function createSessionSnapshot(sessionId: string, name: string): Promise<{ id: string; name: string }> {
  const { data } = await api.post(`/api/sessions/${sessionId}/snapshots`, { name });
  return data;
}

export async function listSessionSnapshots(sessionId: string): Promise<SessionSnapshotItem[]> {
  const { data } = await api.get<SessionSnapshotItem[]>(`/api/sessions/${sessionId}/snapshots`);
  return data;
}

export async function restoreSessionSnapshot(sessionId: string, snapshotId: string): Promise<{ restored: boolean }> {
  const { data } = await api.post(`/api/sessions/${sessionId}/snapshots/${snapshotId}/restore`);
  return data;
}

export async function diffSessionSnapshots(sessionId: string, a: string, b: string): Promise<SessionSnapshotDiff> {
  const { data } = await api.get<SessionSnapshotDiff>(`/api/sessions/${sessionId}/snapshots/diff`, { params: { a, b } });
  return data;
}

export async function getSharedSession(token: string): Promise<SharedSessionPayload> {
  const { data } = await api.get<SharedSessionPayload>(`/api/shared/${token}`);
  return data;
}

export async function forkSharedSession(token: string): Promise<{ id: string; forked_from: string }> {
  const { data } = await api.post(`/api/shared/${token}/fork`);
  return data;
}

export async function addSharedSessionComment(
  token: string,
  payload: { target_type?: string; target_id?: string | null; content: string; parent_id?: string | null },
): Promise<{ id: string }> {
  const { data } = await api.post(`/api/shared/${token}/comments`, payload);
  return data;
}

export async function deleteSharedSessionComment(token: string, commentId: string): Promise<void> {
  await api.delete(`/api/shared/${token}/comments/${commentId}`);
}

export interface ResearchProfile {
  id: string;
  user_id: string;
  memory_enabled: boolean;
  frequently_queried_objects: Array<{ name: string; count: number }>;
  preferred_databases: string[];
  preferred_analysis_methods: string[];
  research_interests: string[];
  expertise_level: string;
  past_hypotheses: Array<Record<string, unknown>>;
  preferred_plotting_style: Record<string, unknown>;
}

export interface ResearchHistoryItem {
  id: string;
  session_id: string;
  summary: string;
  objects: string[];
  methods: string[];
  findings: string[];
  created_at: string | null;
}

export async function getResearchProfile(): Promise<ResearchProfile> {
  const { data } = await api.get<ResearchProfile>("/api/research/profile");
  return data;
}

export async function updateResearchProfile(payload: Partial<Pick<ResearchProfile, "memory_enabled" | "research_interests" | "expertise_level" | "preferred_plotting_style">>): Promise<{ saved: boolean }> {
  const { data } = await api.put("/api/research/profile", payload);
  return data;
}

export async function refreshResearchProfile(sessionId?: string): Promise<{ refreshed: boolean }> {
  const { data } = await api.post("/api/research/profile/refresh", null, {
    params: sessionId ? { session_id: sessionId } : undefined,
  });
  return data;
}

export async function listResearchHistory(query?: string): Promise<ResearchHistoryItem[]> {
  const { data } = await api.get<ResearchHistoryItem[]>("/api/research/history", {
    params: query ? { q: query } : undefined,
  });
  return data;
}

export async function deleteResearchMemory(): Promise<{ deleted: boolean }> {
  const { data } = await api.delete("/api/research/memory");
  return data;
}

export async function validatePaperSession(sessionId: string): Promise<AnalysisValidationResult> {
  const { data } = await api.post<AnalysisValidationResult>(`/api/paper/validate/${sessionId}`);
  return data;
}

export async function generatePaperDraft(
  sessionId: string,
  journalFormat = "aastex",
  overrideValidation = false,
): Promise<PaperDraftResponse> {
  const { data } = await api.post<PaperDraftResponse>("/api/paper/generate", {
    session_id: sessionId,
    journal_format: journalFormat,
    override_validation: overrideValidation,
  });
  return data;
}

export async function updatePaperDraft(
  paperId: string,
  paperJson: Record<string, unknown>,
): Promise<PaperDraftResponse> {
  const { data } = await api.put<PaperDraftResponse>(`/api/paper/${paperId}`, {
    paper_json: paperJson,
  });
  return data;
}

export function getStoredApiKey(provider = "anthropic"): string | null {
  try {
    const keys = JSON.parse(localStorage.getItem("astro_api_keys") || "{}");
    return keys[provider] || null;
  } catch {
    return null;
  }
}

export function getStoredAiProvider(): string | null {
  try {
    const provider = localStorage.getItem("astro_ai_provider");
    return provider && provider.trim() ? provider.trim() : null;
  } catch {
    return null;
  }
}

export function getStoredApiKeys(): Record<string, string> {
  try {
    const raw = JSON.parse(localStorage.getItem("astro_api_keys") || "{}");
    if (!raw || typeof raw !== "object") {
      return {};
    }
    const keys: Record<string, string> = {};
    for (const [key, value] of Object.entries(raw)) {
      if (typeof key === "string" && typeof value === "string" && value.trim().length > 0) {
        keys[key] = value;
      }
    }
    return keys;
  } catch {
    return {};
  }
}

export function getPreferredAiProvider(): string | null {
  const storedProvider = getStoredAiProvider();
  const keys = getStoredApiKeys();
  if (storedProvider === "local") {
    return "local";
  }
  if (storedProvider && keys[storedProvider]) {
    return storedProvider;
  }
  for (const provider of ["anthropic", "openai", "deepseek"]) {
    if (keys[provider]) {
      return provider;
    }
  }
  return null;
}

export async function sendChatMessage(
  messages: ChatMessage[],
  context?: Record<string, unknown>
): Promise<ChatResponse> {
  const apiKeys = getStoredApiKeys();
  const apiProvider = getPreferredAiProvider();
  const body = {
    messages,
    context: {
      ...context,
      ...(Object.keys(apiKeys).length ? { api_keys: apiKeys } : {}),
      ...(apiProvider ? { api_provider: apiProvider } : {}),
    },
  };

  // Use SSE streaming endpoint to avoid proxy timeouts (Render kills idle
  // connections after ~30s; streaming keeps the connection alive).
  try {
    const resp = await fetch(`${API_BASE_URL}/api/chat/message/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(localStorage.getItem("astro_token")
          ? { Authorization: `Bearer ${localStorage.getItem("astro_token")}` }
          : {}),
      },
      body: JSON.stringify(body),
    });

    if (!resp.ok) {
      const errBody = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(
        typeof errBody === "object" && errBody && "detail" in errBody
          ? String(errBody.detail)
          : `AI request failed (${resp.status})`
      );
    }

    const reader = resp.body?.getReader();
    if (!reader) throw new Error(t("error.streaming_unsupported"));

    const decoder = new TextDecoder();
    const replyParts: string[] = [];
    const actions: ChatAction[] = [];
    let buffer = "";

    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const raw = line.slice(6).trim();
        if (!raw) continue;
        let evt: Record<string, unknown>;
        try {
          evt = JSON.parse(raw);
        } catch {
          continue;
        }

        if (evt.type === "text" && typeof evt.content === "string") {
          replyParts.push(evt.content);
        } else if (evt.type === "tool_result") {
          actions.push({
            action: String(evt.tool || ""),
            tool_result: evt.result,
            _auto_executed: true,
          } as ChatAction);
        } else if (evt.type === "error" && typeof evt.message === "string") {
          throw new Error(evt.message);
        }
        // "status" and "done" events are ignored (no UI for them yet)
      }
    }

    return {
      reply: replyParts.join("\n\n"),
      actions,
    };
  } catch (err: unknown) {
    if (err instanceof TypeError && (err.message === "Failed to fetch" || err.message === "NetworkError when attempting to fetch resource.")) {
      let backendReachable = false;
      try {
        await api.get("/health", { timeout: 10000 });
        backendReachable = true;
      } catch {
        backendReachable = false;
      }
      if (backendReachable) {
        throw new Error(t("error.ai_connection_interrupted"));
      }
      throw new Error(t("error.backend_unreachable"));
    }
    throw err;
  }
}

export async function executeChatAction(
  action: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const { data } = await api.post("/api/chat/execute-action", action);
  return data;
}

// ── Visualization API ──

export interface ChartTemplate {
  name: string;
  description: string;
  required_keys: string[];
}

export async function getVizTemplates(): Promise<Record<string, ChartTemplate>> {
  const { data } = await api.get("/api/viz/templates");
  return data;
}

export async function generateViz(
  chartType: string,
  vizData: Record<string, unknown>,
  params?: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const { data } = await api.post("/api/viz/generate", {
    chart_type: chartType,
    data: vizData,
    params: params || {},
  });
  return data;
}

export async function exportViz(
  plotJson: Record<string, unknown>,
  format: "png" | "pdf" = "png",
  width = 1200,
  height = 800
): Promise<Blob> {
  const { data } = await api.post("/api/viz/export", {
    plot_json: plotJson,
    format,
    width,
    height,
  }, { responseType: "blob" });
  return data;
}

// ── Citation / ADS API ──

export interface ADSReference {
  bibcode: string;
  title: string;
  authors: string[];
  year: string;
  doi: string | null;
}

export async function searchADS(objectName: string): Promise<ADSReference[]> {
  const { data } = await api.get<ADSReference[]>("/api/citations/ads", {
    params: { object_name: objectName },
  });
  return data;
}

export interface LiteratureResult {
  bibcode: string;
  title: string;
  authors: string[];
  year: string;
  doi: string | null;
  abstract: string;
  pub: string;
  arxiv_url?: string;
}

export interface LiteratureSearchResponse {
  results: LiteratureResult[];
  source: string;
  query: string;
}

export async function searchLiterature(
  query: string,
  maxResults = 20
): Promise<LiteratureSearchResponse> {
  const { data } = await api.get<LiteratureSearchResponse>("/api/citations/search", {
    params: { q: query, max_results: maxResults },
  });
  return data;
}

export async function getBibTeX(bibcode: string): Promise<string> {
  const { data } = await api.get<{ bibtex: string }>("/api/citations/bibtex", {
    params: { bibcode },
  });
  return data.bibtex;
}

// ── Citation Graph API ──

export interface CitationGraphNode {
  id: string;
  title: string;
  authors: string;
  year: number;
  citations: number;
  in_original_set: boolean;
}

export interface CitationGraphEdge {
  source: string;
  target: string;
  type: string;
}

export interface CitationGraphResponse {
  nodes: CitationGraphNode[];
  edges: CitationGraphEdge[];
  stats: { total_nodes: number; total_edges: number };
  info?: string;
}

export async function fetchCitationGraph(
  bibcodes: string[],
  depth = 1
): Promise<CitationGraphResponse> {
  const { data } = await api.post<CitationGraphResponse>(
    "/api/literature/citation-graph",
    { bibcodes, depth }
  );
  return data;
}

// ── Cross-match API ──

export interface CrossMatchItem {
  ra: number;
  dec: number;
  name: string;
}

export interface CrossMatchResult {
  a_name: string;
  b_name: string;
  a_ra: number;
  a_dec: number;
  b_ra: number;
  b_dec: number;
  separation_arcsec: number;
}

export async function crossMatch(
  listA: CrossMatchItem[],
  listB: CrossMatchItem[],
  radiusArcsec = 3.0
): Promise<CrossMatchResult[]> {
  const { data } = await api.post<CrossMatchResult[]>("/api/crossmatch", {
    list_a: listA,
    list_b: listB,
    radius_arcsec: radiusArcsec,
  });
  return data;
}

// ── Operation Log (Feature 6) ──

interface OperationLogEntry {
  timestamp: string;
  type: string;
  detail: string;
}

export function logOperation(type: string, detail: string): void {
  try {
    const raw = localStorage.getItem("astro_operation_log");
    const log: OperationLogEntry[] = raw ? JSON.parse(raw) : [];
    log.push({
      timestamp: new Date().toISOString(),
      type,
      detail,
    });
    // Keep last 200 entries
    const trimmed = log.slice(-200);
    localStorage.setItem("astro_operation_log", JSON.stringify(trimmed));
  } catch {
    // storage full or unavailable
  }
}

export function getOperationLog(): OperationLogEntry[] {
  try {
    const raw = localStorage.getItem("astro_operation_log");
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function clearOperationLog(): void {
  localStorage.removeItem("astro_operation_log");
}

// ── Alerts API ──

export interface TransientAlert {
  id: string;
  source: string;
  source_id: string;
  ra: number;
  dec: number;
  discovery_date: string | null;
  magnitude: number | null;
  mag_band: string | null;
  classification: string | null;
  classification_confidence: number | null;
  redshift: number | null;
  host_galaxy: string | null;
}

export interface AlertStats {
  total: number;
  by_classification: Record<string, number>;
  by_source: Record<string, number>;
  latest_ingestion: string | null;
}

async function normalizeAlertApiError(err: unknown, fallback: string): Promise<never> {
  if (axios.isAxiosError(err)) {
    if (err.response?.data && typeof err.response.data === "object" && "detail" in err.response.data) {
      throw new Error(String(err.response.data.detail));
    }
    if (err.code === "ECONNABORTED") {
      throw new Error(t("error.alert_timed_out"));
    }
    if (err.message === "Network Error") {
      let backendReachable = false;
      try {
        await api.get("/health", { timeout: 10000 });
        backendReachable = true;
      } catch {
        backendReachable = false;
      }
      if (backendReachable) {
        throw new Error(t("error.alert_bad_response"));
      }
      throw new Error(t("error.alert_backend_unreachable"));
    }
    throw new Error(err.message || fallback);
  }
  throw err;
}

export async function getAlerts(params?: { days?: number; classification?: string; limit?: number }): Promise<TransientAlert[]> {
  try {
    const { data } = await api.get<{ count: number; alerts: TransientAlert[] }>("/api/alerts/", { params });
    return data.alerts ?? [];
  } catch (err: unknown) {
    return await normalizeAlertApiError(err, "Failed to fetch alerts");
  }
}

export async function getAlertStats(): Promise<AlertStats> {
  try {
    const { data } = await api.get<AlertStats>("/api/alerts/stats");
    return data;
  } catch (err: unknown) {
    return await normalizeAlertApiError(err, "Failed to fetch alert stats");
  }
}

export async function searchAlertsCone(ra: number, dec: number, radius_arcsec: number): Promise<TransientAlert[]> {
  try {
    const { data } = await api.get<{ alerts?: TransientAlert[] }>("/api/alerts/cone", {
      params: { ra, dec, radius_arcsec },
    });
    return data.alerts ?? [];
  } catch (err: unknown) {
    return await normalizeAlertApiError(err, "Failed to search alerts by cone");
  }
}

// ── Anomaly Explorer API ──

export interface AnomalyItem {
  id: string;
  object_name: string;
  ra: number;
  dec: number;
  anomaly_score: number;
  source: "Query" | "Alert" | "Cross-wavelength";
  unusual_features: string;
  detection_methods: string[];
  timestamp: string;
}

export interface AnomalyFeedResponse {
  items: AnomalyItem[];
  page: number;
  per_page: number;
  total: number;
}

export interface AnomalyStats {
  total: number;
  high_confidence: number;
}

export async function getAnomalyFeed(
  page = 1,
  filters?: {
    min_score?: number;
    source?: string;
    sort?: string;
    if_contamination?: number;
    ae_threshold_percentile?: number;
    dbscan_min_samples?: number;
    voting_threshold?: number;
  },
): Promise<AnomalyFeedResponse> {
  const params: Record<string, string | number> = { page };
  if (filters?.min_score !== undefined) params.min_score = filters.min_score;
  if (filters?.source) params.source = filters.source;
  if (filters?.sort) params.sort = filters.sort;
  if (filters?.if_contamination !== undefined) params.if_contamination = filters.if_contamination;
  if (filters?.ae_threshold_percentile !== undefined) params.ae_threshold_percentile = filters.ae_threshold_percentile;
  if (filters?.dbscan_min_samples !== undefined) params.dbscan_min_samples = filters.dbscan_min_samples;
  if (filters?.voting_threshold !== undefined) params.voting_threshold = filters.voting_threshold;
  const { data } = await api.get<AnomalyFeedResponse>("/api/anomalies/feed", { params });
  return data;
}

export async function getAnomalyStats(): Promise<AnomalyStats> {
  const { data } = await api.get<AnomalyStats>("/api/anomalies/stats");
  return data;
}

export async function dismissAnomaly(id: string): Promise<{ id: string; dismissed: boolean }> {
  const { data } = await api.patch<{ id: string; dismissed: boolean }>(`/api/anomalies/${id}/dismiss`);
  return data;
}

export default api;
