import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "http://localhost:8000",
  timeout: 120000,
});

// Attach JWT token to requests if available
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("astro_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
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
  email: string;
  subscription_tier: string;
  stripe_customer_id: string | null;
  display_name: string | null;
  avatar_url: string | null;
  google_linked: boolean;
}

export async function register(email: string, password: string): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>("/api/auth/register", { email, password });
  localStorage.setItem("astro_token", data.access_token);
  return data;
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const { data } = await api.post<TokenResponse>("/api/auth/login", { email, password });
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
  radius = 0.1
): Promise<SearchResult[]> {
  const params: Record<string, string | number> = { q, sources, radius };
  if (ra !== undefined) params.ra = ra;
  if (dec !== undefined) params.dec = dec;
  const { data } = await api.get<SearchResult[]>("/api/data/search", { params });
  return data;
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

export async function getFITSHeader(fitsPath: string): Promise<FITSHeader> {
  const { data } = await api.get<FITSHeader>("/api/data/fits-header", {
    params: { fits_path: fitsPath },
  });
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
  objectId?: string
): Promise<FITSFileInfo> {
  const form = new FormData();
  form.append("file", file);
  const params: Record<string, string> = {};
  if (objectId) params.object_id = objectId;
  const { data } = await api.post<FITSFileInfo>("/api/data/fits/upload", form, { params });
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
  const base = import.meta.env.VITE_API_URL || "http://localhost:8000";
  return `${base}/api/data/fits/download?fits_path=${encodeURIComponent(fitsPath)}`;
}

export async function uploadGeneralFile(file: File): Promise<{ id: string; filename: string; path: string; size_bytes: number }> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await api.post("/api/data/files/upload", form);
  return data;
}

export function downloadFileUrl(path: string): string {
  const base = import.meta.env.VITE_API_URL || "http://localhost:8000";
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

// ── WebSocket ──

export function connectPipelineWS(
  runId: string,
  onMessage: (data: Record<string, unknown>) => void,
  onClose?: () => void
): WebSocket {
  const baseUrl = import.meta.env.VITE_API_URL || "http://localhost:8000";
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

export function getStoredApiKey(provider = "anthropic"): string | null {
  try {
    const keys = JSON.parse(localStorage.getItem("astro_api_keys") || "{}");
    return keys[provider] || null;
  } catch {
    return null;
  }
}

export async function sendChatMessage(
  messages: ChatMessage[],
  context?: Record<string, unknown>
): Promise<ChatResponse> {
  // Pass API key from localStorage if available
  const apiKey = getStoredApiKey("anthropic");
  const { data } = await api.post<ChatResponse>("/api/chat/message", {
    messages,
    context: { ...context, ...(apiKey ? { api_key: apiKey } : {}) },
  });
  return data;
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

export async function getBibTeX(bibcode: string): Promise<string> {
  const { data } = await api.get<{ bibtex: string }>("/api/citations/bibtex", {
    params: { bibcode },
  });
  return data.bibtex;
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

export default api;
