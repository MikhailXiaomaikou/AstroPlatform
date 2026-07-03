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

// Transient-5xx recovery.  Written for Render's free tier (dynos slept
// after 15 min idle); the backend now runs on a paid Standard instance
// that does not sleep, but deploys / proxy hiccups can still surface as
// brief 502 / 503 / 504 windows.  We retry the request once after a
// short wait, and expose a browser-level "waking up" signal
// (CustomEvent) so any on-screen banner can re-appear.
//
// Matches the symptom Bug 12 reviewer hit: "backend returning 502 —
// frontend otherwise correct".  This interceptor turns that into a
// transparent retry + user-visible "waking up" notice.
const COLD_START_STATUSES = new Set([502, 503, 504]);
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error?.config;
    const status = error?.response?.status;
    if (!config || !COLD_START_STATUSES.has(status)) {
      return Promise.reject(error);
    }
    // Avoid infinite loops — only retry once per original request.
    const retryKey = "__cold_start_retried__";
    if ((config as Record<string, unknown>)[retryKey]) {
      return Promise.reject(error);
    }
    (config as Record<string, unknown>)[retryKey] = true;
    // Clear the "backend_checked" flag so BackendBanner re-appears.
    try {
      sessionStorage.removeItem("astro_backend_checked");
      // Signal banner to show immediately.
      window.dispatchEvent(new CustomEvent("astro:backend-waking"));
    } catch {
      /* ignore */
    }
    // Wait 5s for the dyno to wake, then retry once.
    await new Promise((resolve) => setTimeout(resolve, 5000));
    try {
      return await api.request(config);
    } catch (retryErr) {
      // If the retry also fails, surface the second error so the caller
      // sees the real diagnostic.
      return Promise.reject(retryErr);
    }
  },
);

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
  if (!isLocalNoAuthEnabledByEnv()) {
    localStorage.removeItem("astro_local_no_auth");
  }
}

function isLocalNoAuthEnabledByEnv(): boolean {
  return String(import.meta.env.VITE_LOCAL_DEV_NO_AUTH || "").toLowerCase() === "1";
}

export function isLocalNoAuthEnabled(): boolean {
  return isLocalNoAuthEnabledByEnv() || localStorage.getItem("astro_local_no_auth") === "1";
}

export function isAuthenticated(): boolean {
  return !!localStorage.getItem("astro_token") || isLocalNoAuthEnabled();
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
        // H8: if the caller already aborted, do NOT retry — a cancelled
        // search should stay cancelled instead of spawning a second request
        // the user has no handle to.  Forward the original signal so the
        // retry itself is still cancellable if the caller aborts mid-retry.
        if (signal?.aborted) {
          throw err;
        }
        try {
          const { data } = await api.get<SearchResult[]>("/api/data/search", { params, signal });
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

// advancedSearch / getSpectralLines (Advanced Search) were removed
// 2026-07-03 with their request/response interfaces — dead since the M3
// Data-Browser trim (zero callers outside this file).

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

// analyzeSpectrum (Spectrum Analysis) was removed 2026-07-03 with its
// SpectrumAnalysis interface — dead since the M3 page trim (zero callers).

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

// The Pipeline API, Pipeline Version API, and pipeline WebSocket sections
// (getNodeTypes / getTemplates / runPipeline / saveTemplateVersion /
// getTemplateVersions / getTemplateVersion / getTemplateDiff /
// batchRunPipeline / getPipelineRun / getNodeResult / connectPipelineWS
// and the NodeType / PipelineTemplate / DagNode / DagEdge / RunResponse /
// VersionSummary / VersionDetail / DagDiffResult interfaces) were removed
// 2026-07-03 — dead since the M3 Pipeline-page deletion (zero callers
// outside this file).

// ── Workspace / Data Management API ──
// batchSearch / uploadBatchTargets (and the BatchTarget interface) plus
// exportData were removed 2026-07-03 — dead since the M3 Workspace-page
// deletion (zero callers outside this file).

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

// The Pipeline Export API (exportRunCSV / exportRunVOTable / exportRunPDF)
// was removed 2026-07-03 — dead since the M3 Pipeline-page deletion
// (zero callers outside this file).

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
  sessionId?: string | null,
): Promise<Blob> {
  // sessionId lets the backend retrieve the full figures from the chat_sessions DB
  // when they have been offloaded from localStorage, for export. Server-side messages are never pruned.
  const { data } = await api.post("/api/export/report/from-chat", {
    messages,
    title: title || "AI Research Chat",
    session_id: sessionId || null,
  }, { responseType: "blob" });
  return data;
}

// ── Chat → Notebook Export ──

export async function exportChatNotebook(
  messages: Array<{ role: string; content: string; actions?: unknown[] }>,
  title?: string,
  sessionId?: string | null,
): Promise<Blob> {
  const { data } = await api.post("/api/export/notebook/from-chat", {
    messages,
    title: title || "AI Research Session",
    session_id: sessionId || null,
  }, { responseType: "blob" });
  return data;
}

// ── Chat → HTML (self-contained) Export ──

/** Single-file HTML — double-click to open in browser; all figures base64-embedded; Ctrl+P prints to PDF. */
export async function exportChatHtml(
  messages: Array<{ role: string; content: string; actions?: unknown[] }>,
  title?: string,
  sessionId?: string | null,
): Promise<Blob> {
  const { data } = await api.post("/api/export/report/html-from-chat", {
    messages,
    title: title || "AI Research Session",
    session_id: sessionId || null,
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

// ── Integration API (removed) ──
// adqlQuery / listADQLServices were removed 2026-06-11 along with their
// backend routes — dead since the M3 ADQL-page trim (the query route was
// also unauthenticated). The chat path runs ADQL server-side via the
// run_adql tool, not through these endpoints.
// sampStatus / sampSend / convertToVOTable / exportJupyter, the WCS Grid
// API (getFITSWCS + WCSGrid* interfaces) and the Scheduler API
// (createSchedule / listSchedules / toggleSchedule / deleteSchedule +
// ScheduleItem) were removed 2026-07-03 — dead since the M3 page
// deletions (zero callers outside this file).

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

// Per-reply validation summary computed by the backend gate stack
// (app/services/agent_runtime/loop.py) and threaded onto the final SSE
// text frame / ChatResponse. Optional forever — old replies lack it and
// must render unchanged.
export type ValidationGateState =
  | "passed"
  | "regenerated"
  | "blocked"
  | "skipped_no_data"
  | "skipped"
  | "not_run";

export interface ValidationSummary {
  schema_version?: number;
  numeric_gate?: ValidationGateState | string;
  citation_gate?: ValidationGateState | string;
  regen_count?: number;
  blocked?: boolean;
  reason?: string;
  interventions?: Array<{ gate?: string; action?: string; reason?: string }>;
}

export interface ChatResponse {
  reply: string;
  actions: ChatAction[];
  // Optional: true when the agent loop exhausted its iteration budget —
  // the reply is a truncated workflow, not a complete answer.
  hit_iteration_cap?: boolean;
  // Optional: what the validation gate stack did for this reply.
  validation_summary?: ValidationSummary;
}

// Real-time "thinking process" events emitted by the backend during the
// agent loop.  Consumers can subscribe via sendChatMessage's onThinking
// callback to render a live timeline of what the model is doing.
export type ThinkingEvent =
  | { type: "agent_text"; agent?: string; content: string }
  | { type: "tool_call"; agent?: string; tool: string; input: unknown; iteration?: number; max_iterations?: number }
  | { type: "tool_progress"; agent?: string; tool: string; message: string; stage?: string; detail?: Record<string, unknown> }
  | { type: "tool_result"; agent?: string; tool: string; result: unknown }
  | { type: "status"; message: string }
  | { type: "workflow_budget"; agent?: string; mode: string; agent_loop_seconds: number; summary_reserve_seconds: number; max_iterations: number }
  | { type: "workflow_checkpoint"; agent?: string; summary?: unknown; step_idx?: number; tool_name?: string; status?: string; cache_refs?: string[]; checkpoint_summary?: string }
  // F3.2: emitted by chat.py when the model responds with a structured
  // abstention tag.  Frontend renders as HonestAbstentionCard.
  | { type: "honest_abstention"; payload: {
      failed_tools?: string;
      empty_tools?: string;
      rationale?: string;
      suggested_next_step?: string;
      reason?: string;
      agent?: string;
    } }
  // G3.5: emitted when a data-fetch tool has failed ≥ DISABLE_AFTER_FAILURES
  // times in the current turn and the agent loop removes it from the
  // `tools` parameter for subsequent LLM calls.  Frontend shows a
  // "🚫 <tool> disabled after N failures" chip in the thinking timeline.
  | { type: "tools_disabled"; agent?: string; disabled: string[]; iteration: number };

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
  session_id?: string;
  paper_json: Record<string, unknown>;
  latex_source: string;
  bibtex: string;
  validation: AnalysisValidationResult;
  journal_format?: string;
  is_public?: boolean;
  public_token?: string | null;
  public_url?: string | null;
  published_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export async function saveChatSession(
  messages: Array<{ role: string; content: string; actions?: unknown[] }>,
  sessionId?: string,
  title?: string,
): Promise<{ id: string; title?: string }> {
  const { data } = await api.post("/api/chat/sessions/save", {
    session_id: sessionId,
    title: title,  // backend auto-generates if omitted/default
    messages,
  });
  return data;
}

// F4.2: report which AI backends (Claude/OpenAI/DeepSeek/local) are
// configured so the chat UI can disable Send + show a setup banner when
// nothing is available.  Checks server env vars + the authenticated
// user's stored keys.  Never returns the key material itself.
export interface AIBackendStatus {
  configured_backends: string[];
  needs_setup: boolean;
  available_models?: AIModelProfile[];
  default_model_by_provider?: Record<string, AIModelProfile>;
  selected_model_status?: AIModelProfile;
}

export interface AIModelProfile {
  id: string;
  provider: string;
  model_id: string;
  display_name: string;
  api_ready: boolean;
  resolved_model_id: string;
  supports_tools: boolean;
  supports_reasoning: boolean;
  endpoint?: string;
  reasoning_effort?: string | null;
  note?: string | null;
}

export async function getAIBackendStatus(provider?: string | null, modelProfile?: string | null): Promise<AIBackendStatus> {
  const params: Record<string, string> = {};
  if (provider) params.api_provider = provider;
  if (modelProfile) params.model_profile = modelProfile;
  const { data } = await api.get("/api/chat/ai_backend_status", { params });
  return data;
}

export async function renameChatSession(
  sessionId: string,
  title: string,
): Promise<{ id: string; title: string }> {
  const { data } = await api.patch(`/api/chat/sessions/${sessionId}`, { title });
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
    // Messages are stored verbatim from /api/chat/sessions/save — assistant
    // entries may carry auto-executed tool actions (tool evidence) and the
    // optional validation summary. Old shares lack both and render unchanged.
    messages: Array<{
      role: string;
      content: string;
      actions?: unknown[];
      _validation?: ValidationSummary;
      _truncated?: boolean;
    }>;
    created_at: string | null;
    updated_at: string | null;
    paper_drafts?: Array<{
      id: string;
      journal_format: string;
      paper_json: Record<string, unknown>;
      latex_source: string;
      bibtex: string;
      validation: Record<string, unknown>;
      is_public?: boolean;
      public_url?: string | null;
      published_at?: string | null;
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

export async function listPaperDrafts(): Promise<PaperDraftResponse[]> {
  const { data } = await api.get<PaperDraftResponse[]>("/api/paper");
  return data;
}

export async function getPaperDraft(paperId: string): Promise<PaperDraftResponse> {
  const { data } = await api.get<PaperDraftResponse>(`/api/paper/${paperId}`);
  return data;
}

export async function getPublicPaperDraft(token: string): Promise<PaperDraftResponse> {
  const { data } = await api.get<PaperDraftResponse>(`/api/paper/public/${token}`);
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

export async function publishPaperDraft(paperId: string): Promise<PaperDraftResponse> {
  const { data } = await api.post<PaperDraftResponse>(`/api/paper/${paperId}/publish`);
  return data;
}

export async function unpublishPaperDraft(paperId: string): Promise<PaperDraftResponse> {
  const { data } = await api.delete<PaperDraftResponse>(`/api/paper/${paperId}/publish`);
  return data;
}

// M9: prefer sessionStorage for API key material so keys do not persist
// across browser restarts and shrink the XSS blast radius.  A user who
// explicitly opts into "Remember this browser" can flip the
// `astro_api_keys_persist` flag in localStorage; our helpers honour it by
// writing to localStorage as well.  Reads check session first, then
// localStorage, so existing deployments don't break — the first read after
// upgrading "migrates" material into sessionStorage.
const API_KEYS_STORAGE = "astro_api_keys";
const AI_PROVIDER_STORAGE = "astro_ai_provider";
const AI_MODEL_PROFILE_STORAGE = "astro_ai_model_profile";
const PERSIST_FLAG_STORAGE = "astro_api_keys_persist";

export const DEFAULT_AI_PROVIDER = isLocalNoAuthEnabledByEnv() ? "local" : "deepseek";

export const DEFAULT_AI_MODEL_BY_PROVIDER: Record<string, string> = {
  anthropic: "anthropic:default",
  openai: "openai:gpt-5.5",
  deepseek: "deepseek:v4-pro",
  local: isLocalNoAuthEnabledByEnv() ? "local:openai-cli" : "local:default",
};

export const AI_MODEL_OPTIONS: Record<string, Array<{ id: string; label: string; detail?: string }>> = {
  anthropic: [
    { id: "anthropic:default", label: "Claude default" },
  ],
  openai: [
    { id: "openai:gpt-5.5", label: "GPT-5.5", detail: "API soon; currently resolves to gpt-5.4 fallback" },
    { id: "openai:gpt-5.4", label: "GPT-5.4" },
  ],
  deepseek: [
    { id: "deepseek:v4-pro", label: "DeepSeek V4 Pro", detail: "Thinking enabled, high reasoning effort" },
    { id: "deepseek:v4-flash", label: "DeepSeek V4 Flash" },
  ],
  local: [
    { id: "local:openai-cli", label: "OpenAI CLI", detail: "Local only; uses your CLI subscription login with backend-executed network/database tools" },
    { id: "local:default", label: "OpenAI-compatible local server", detail: "Requires LOCAL_MODEL_ENABLED=1" },
  ],
};

function isLocalBrowserRuntime(): boolean {
  if (typeof window === "undefined") return false;
  const host = window.location?.hostname || "";
  return host === "localhost" || host === "127.0.0.1" || host === "::1" || host === "";
}

function _shouldPersist(): boolean {
  try {
    return localStorage.getItem(PERSIST_FLAG_STORAGE) === "1";
  } catch {
    return false;
  }
}

function _readFirstDefined(key: string): string | null {
  try {
    const s = sessionStorage.getItem(key);
    if (s !== null && s !== "") return s;
  } catch {
    /* ignore */
  }
  try {
    const l = localStorage.getItem(key);
    if (l !== null && l !== "") return l;
  } catch {
    /* ignore */
  }
  return null;
}

export function writeStoredApiKeys(keys: Record<string, string>): void {
  const payload = JSON.stringify(keys);
  try {
    sessionStorage.setItem(API_KEYS_STORAGE, payload);
  } catch {
    /* ignore */
  }
  if (_shouldPersist()) {
    try {
      localStorage.setItem(API_KEYS_STORAGE, payload);
    } catch {
      /* ignore */
    }
  } else {
    try {
      localStorage.removeItem(API_KEYS_STORAGE);
    } catch {
      /* ignore */
    }
  }
}

export function getStoredApiKey(provider = "anthropic"): string | null {
  const raw = _readFirstDefined(API_KEYS_STORAGE) || "{}";
  try {
    const keys = JSON.parse(raw);
    return (keys && typeof keys === "object" && keys[provider]) || null;
  } catch {
    return null;
  }
}

/**
 * Whether stored API keys are currently set to survive a browser restart.
 * False (default) means keys live in sessionStorage and clear on tab close.
 */
export function getApiKeysPersist(): boolean {
  return _shouldPersist();
}

/**
 * Toggle whether API keys are persisted to localStorage across browser
 * restarts. Existing keys are migrated to/from localStorage so the toggle
 * takes effect immediately on the current key set (no need to re-enter).
 */
export function setApiKeysPersist(persist: boolean): void {
  try {
    if (persist) {
      localStorage.setItem(PERSIST_FLAG_STORAGE, "1");
    } else {
      localStorage.removeItem(PERSIST_FLAG_STORAGE);
    }
  } catch {
    /* ignore */
  }
  // Re-write current keys with the new persistence preference so the
  // setting takes effect for already-saved keys, not just future writes.
  const raw = _readFirstDefined(API_KEYS_STORAGE);
  if (!raw) return;
  try {
    const keys = JSON.parse(raw);
    if (keys && typeof keys === "object") {
      writeStoredApiKeys(keys);
    }
  } catch {
    /* ignore */
  }
}

export function getStoredAiProvider(): string | null {
  const raw = _readFirstDefined(AI_PROVIDER_STORAGE);
  if (!raw) return null;
  const trimmed = raw.trim();
  return trimmed ? trimmed : null;
}

export function writeStoredAiProvider(provider: string): void {
  const value = provider.trim();
  if (!value) return;
  try {
    sessionStorage.setItem(AI_PROVIDER_STORAGE, value);
  } catch {
    /* ignore */
  }
  if (_shouldPersist()) {
    try {
      localStorage.setItem(AI_PROVIDER_STORAGE, value);
    } catch {
      /* ignore */
    }
  } else {
    try {
      localStorage.removeItem(AI_PROVIDER_STORAGE);
    } catch {
      /* ignore */
    }
  }
}

export function getStoredAiModelProfile(): string | null {
  const raw = _readFirstDefined(AI_MODEL_PROFILE_STORAGE);
  if (!raw) return null;
  const trimmed = raw.trim();
  return trimmed ? trimmed : null;
}

export function writeStoredAiModelProfile(modelProfile: string): void {
  const value = modelProfile.trim();
  if (!value) return;
  try {
    sessionStorage.setItem(AI_MODEL_PROFILE_STORAGE, value);
  } catch {
    /* ignore */
  }
  if (_shouldPersist()) {
    try {
      localStorage.setItem(AI_MODEL_PROFILE_STORAGE, value);
    } catch {
      /* ignore */
    }
  } else {
    try {
      localStorage.removeItem(AI_MODEL_PROFILE_STORAGE);
    } catch {
      /* ignore */
    }
  }
}

export function getPreferredAiModelProfile(provider?: string | null): string | null {
  const selectedProvider = provider || getPreferredAiProvider() || getStoredAiProvider() || DEFAULT_AI_PROVIDER;
  const stored = getStoredAiModelProfile();
  if (stored && stored.startsWith(`${selectedProvider}:`)) {
    return stored;
  }
  return DEFAULT_AI_MODEL_BY_PROVIDER[selectedProvider] || null;
}

export function getStoredApiKeys(): Record<string, string> {
  const raw = _readFirstDefined(API_KEYS_STORAGE) || "{}";
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") {
      return {};
    }
    const keys: Record<string, string> = {};
    for (const [key, value] of Object.entries(parsed)) {
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
  const allowedProviders = isLocalBrowserRuntime()
    ? ["anthropic", "openai", "deepseek", "local"]
    : ["anthropic", "openai", "deepseek"];
  if (storedProvider && allowedProviders.includes(storedProvider)) {
    return storedProvider;
  }
  for (const provider of [DEFAULT_AI_PROVIDER, "openai", "anthropic"]) {
    if (keys[provider]) {
      return provider;
    }
  }
  return DEFAULT_AI_PROVIDER;
}

// P1.3.c (2026-05-22): the SSE stream may drop mid-flight on Render
// free-tier idle / proxy timeouts / network blips. ``sendChatMessage``
// wraps the underlying single-attempt streamer and, on a stream-drop
// error, retries exactly once with ``resume_from_session=true`` so the
// backend can inject the workflow-checkpoint summary and the LLM can
// pick up where it left off. Returns whatever the retry produces;
// non-droppable failures (auth, model error, user abort) pass through
// untouched.
function _isResumableStreamDrop(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  // The streamer tags every mid-flight drop variant with
  // error_class = "stream_drop" (see the empty-stream guard in
  // _sendChatMessageOnce). Anything else is a deliberate failure (auth,
  // model error, etc). Keying on the structured flag — not the localized
  // display text — keeps checkpoint resume working in every locale.
  return (err as Error & { error_class?: string }).error_class === "stream_drop";
}

export async function sendChatMessage(
  messages: ChatMessage[],
  context?: Record<string, unknown>,
  onThinking?: (evt: ThinkingEvent) => void,
  signal?: AbortSignal,
  onActions?: (actions: ChatAction[]) => void,
): Promise<ChatResponse> {
  let firstErr: unknown;
  try {
    return await _sendChatMessageOnce(messages, context, onThinking, signal, onActions);
  } catch (err) {
    if (signal?.aborted) throw err;
    if (!_isResumableStreamDrop(err)) throw err;
    firstErr = err;
  }
  // Single retry with the resume hint so the backend re-injects the
  // workflow-checkpoint summary from the prior attempt. UI shows a
  // status pill so the user sees the reconnect rather than dead air.
  try {
    onThinking?.({ type: "status", message: "Reconnecting — resuming from last checkpoint…" });
  } catch {
    /* onThinking should never crash the retry */
  }
  try {
    return await _sendChatMessageOnce(
      messages,
      { ...(context ?? {}), resume_from_session: true },
      onThinking,
      signal,
      onActions,
    );
  } catch (retryErr) {
    // Preserve the original drop error so any caller / test that pattern-matches
    // on it keeps working — a flaky retry shouldn't swap a known-shape error
    // for an obscure "fetch returned undefined" TypeError. Log the retry
    // exception for diagnostics, then re-throw the original.
    if (signal?.aborted) throw retryErr;
    console.debug("sendChatMessage resume retry also failed", retryErr);
    throw firstErr;
  }
}

async function _sendChatMessageOnce(
  messages: ChatMessage[],
  context?: Record<string, unknown>,
  onThinking?: (evt: ThinkingEvent) => void,
  signal?: AbortSignal,
  onActions?: (actions: ChatAction[]) => void,
): Promise<ChatResponse> {
  const apiKeys = getStoredApiKeys();
  const apiProvider = getPreferredAiProvider();
  const modelProfile = getPreferredAiModelProfile(apiProvider);
  const body = {
    messages,
    context: {
      ...context,
      ...(Object.keys(apiKeys).length ? { api_keys: apiKeys } : {}),
      ...(apiProvider ? { api_provider: apiProvider } : {}),
      ...(modelProfile ? { model_profile: modelProfile } : {}),
    },
  };

  // Use SSE streaming endpoint to avoid proxy timeouts (Render kills idle
  // connections after ~30s; streaming keeps the connection alive).
  try {
    const streamDebug =
      context?.debug_stream === true
      || context?.debug_stream === "1"
      || (typeof window !== "undefined" && new URLSearchParams(window.location.search).get("debug_stream") === "1");
    const streamUrl = `${API_BASE_URL}/api/chat/message/stream${streamDebug ? "?debug_stream=1" : ""}`;
    const fetchStream = () => fetch(streamUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(localStorage.getItem("astro_token")
          ? { Authorization: `Bearer ${localStorage.getItem("astro_token")}` }
          : {}),
      },
      body: JSON.stringify(body),
      signal,  // R0d: user-triggered abort closes the fetch + stream
    });

    let resp = await fetchStream();
    if (!resp.ok && COLD_START_STATUSES.has(resp.status) && !signal?.aborted) {
      try {
        sessionStorage.removeItem("astro_backend_checked");
        window.dispatchEvent(new CustomEvent("astro:backend-waking"));
      } catch {
        /* ignore */
      }
      await new Promise((resolve) => setTimeout(resolve, 5000));
      resp = await fetchStream();
    }

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
    const streamedActions: ChatAction[] = [];
    let sawAnySseEvent = false;
    let sawDoneEvent = false;
    let sawToolActivity = false;
    // Optional flags riding the final text frame (backward compatible —
    // older backends simply never set them).
    let hitIterationCap: boolean | undefined;
    let validationSummary: ValidationSummary | undefined;

    const actionKey = (action: ChatAction, fallbackIndex: number) => {
      const toolCallId = action._tool_call_id;
      return typeof toolCallId === "string" && toolCallId
        ? `id:${toolCallId}`
        : `idx:${fallbackIndex}:${action.action}`;
    };

    const mergedVisibleActions = () => {
      const merged = [...streamedActions];
      const indexByKey = new Map<string, number>();
      merged.forEach((action, index) => {
        indexByKey.set(actionKey(action, index), index);
      });
      actions.forEach((action, index) => {
        const key = actionKey(action, index);
        const existingIndex = indexByKey.get(key);
        if (existingIndex == null) {
          indexByKey.set(key, merged.length);
          merged.push(action);
        } else {
          merged[existingIndex] = action;
        }
      });
      return merged;
    };
    let buffer = "";

    // H7: cancel the reader on any exit path so the browser can release the
    // underlying stream; previously an exception (malformed SSE line, network
    // drop, "error" event) would propagate without calling reader.cancel()
    // and leak a ReadableStream per failed chat.
    try {
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
          sawAnySseEvent = true;
          if (streamDebug) {
            console.debug("[astro:sse]", evt.type, evt);
          }

          if (evt.type === "text" && typeof evt.content === "string") {
            replyParts.push(evt.content);
            if (typeof evt.hit_iteration_cap === "boolean") {
              hitIterationCap = evt.hit_iteration_cap;
            }
            if (
              evt.validation_summary
              && typeof evt.validation_summary === "object"
              && !Array.isArray(evt.validation_summary)
            ) {
              validationSummary = evt.validation_summary as ValidationSummary;
            }
          } else if (evt.type === "tool_result") {
            sawToolActivity = true;
            const action = {
              action: String(evt.tool || ""),
              tool_result: evt.result,
              _auto_executed: true,
              ...(typeof evt.tool_call_id === "string" && evt.tool_call_id ? { _tool_call_id: evt.tool_call_id } : {}),
              ...(evt.live === true ? { _stream_preview: true } : {}),
            } as ChatAction;
            // Backend emits tool_result twice: once inline (live:true) during
            // the agent loop for thinking-timeline updates, once at the end
            // with the full consolidated actions list.  Route each to the
            // right sink so the actions array stays deduplicated.
            if (evt.live === true) {
              streamedActions.push(action);
              if (onActions) onActions(mergedVisibleActions());
              if (onThinking) {
                onThinking({
                  type: "tool_result",
                  agent: typeof evt.agent === "string" ? evt.agent : undefined,
                  tool: String(evt.tool || ""),
                  result: evt.result,
                });
              }
            } else {
              actions.push(action);
              if (onActions) onActions(mergedVisibleActions());
            }
          } else if (evt.type === "tool_progress" && typeof evt.tool === "string") {
            sawToolActivity = true;
            if (onThinking) {
              const detail = { ...evt };
              delete detail.type;
              delete detail.tool;
              delete detail.agent;
              delete detail.message;
              delete detail.stage;
              onThinking({
                type: "tool_progress",
                agent: typeof evt.agent === "string" ? evt.agent : undefined,
                tool: evt.tool,
                message: typeof evt.message === "string" ? evt.message : "Tool progress update",
                stage: typeof evt.stage === "string" ? evt.stage : undefined,
                detail,
              });
            }
          } else if (evt.type === "agent_text" && typeof evt.content === "string") {
            // Intermediate prose the LLM produced between tool calls —
            // does NOT go into replyParts; the final consolidated `text`
            // event still provides the canonical reply.
            if (onThinking) {
              onThinking({
                type: "agent_text",
                agent: typeof evt.agent === "string" ? evt.agent : undefined,
                content: evt.content,
              });
            }
          } else if (evt.type === "tool_call" && typeof evt.tool === "string") {
            sawToolActivity = true;
            if (onThinking) {
              onThinking({
                type: "tool_call",
                agent: typeof evt.agent === "string" ? evt.agent : undefined,
                tool: evt.tool,
                input: evt.input,
                iteration: typeof evt.iteration === "number" ? evt.iteration : undefined,
                max_iterations: typeof evt.max_iterations === "number" ? evt.max_iterations : undefined,
              });
            }
          } else if (evt.type === "status" && typeof evt.message === "string") {
            if (onThinking) {
              onThinking({ type: "status", message: evt.message });
            }
          } else if (evt.type === "workflow_budget" && typeof evt.mode === "string") {
            if (onThinking) {
              onThinking({
                type: "workflow_budget",
                agent: typeof evt.agent === "string" ? evt.agent : undefined,
                mode: evt.mode,
                agent_loop_seconds: typeof evt.agent_loop_seconds === "number" ? evt.agent_loop_seconds : 0,
                summary_reserve_seconds: typeof evt.summary_reserve_seconds === "number" ? evt.summary_reserve_seconds : 0,
                max_iterations: typeof evt.max_iterations === "number" ? evt.max_iterations : 0,
              });
            }
          } else if (evt.type === "workflow_checkpoint") {
            if (onThinking) {
              const cacheRefs = Array.isArray(evt.cache_refs)
                ? evt.cache_refs.filter((x): x is string => typeof x === "string")
                : undefined;
              onThinking({
                type: "workflow_checkpoint",
                agent: typeof evt.agent === "string" ? evt.agent : undefined,
                summary: evt.summary,
                step_idx: typeof evt.step_idx === "number" ? evt.step_idx : undefined,
                tool_name: typeof evt.tool_name === "string" ? evt.tool_name : undefined,
                status: typeof evt.status === "string" ? evt.status : undefined,
                cache_refs: cacheRefs,
                checkpoint_summary: typeof evt.summary === "string" ? evt.summary : undefined,
              });
            }
          } else if (evt.type === "stream_debug") {
            // Debug-only event; console logging above is the intended UI.
          } else if (evt.type === "honest_abstention" && evt.payload && typeof evt.payload === "object") {
            // F3.2: structured abstention card.  Payload keys match the
            // attributes of <tools_returned_nothing/> as parsed by the backend.
            if (onThinking) {
              const p = evt.payload as Record<string, unknown>;
              onThinking({
                type: "honest_abstention",
                payload: {
                  failed_tools: typeof p.failed_tools === "string" ? p.failed_tools : undefined,
                  empty_tools: typeof p.empty_tools === "string" ? p.empty_tools : undefined,
                  rationale: typeof p.rationale === "string" ? p.rationale : undefined,
                  suggested_next_step: typeof p.suggested_next_step === "string" ? p.suggested_next_step : undefined,
                  reason: typeof p.reason === "string" ? p.reason : undefined,
                  agent: typeof p.agent === "string" ? p.agent : undefined,
                },
              });
            }
          } else if (evt.type === "tools_disabled" && Array.isArray(evt.disabled)) {
            // G3.5: backend disabled one or more tools this turn after
            // repeated failures.  Forward to the thinking timeline so
            // users see which tools are no longer available + why.
            if (onThinking) {
              const disabled = (evt.disabled as unknown[])
                .filter((x): x is string => typeof x === "string");
              onThinking({
                type: "tools_disabled",
                agent: typeof evt.agent === "string" ? evt.agent : undefined,
                disabled,
                iteration: typeof evt.iteration === "number" ? evt.iteration : 0,
              });
            }
          } else if (evt.type === "error" && typeof evt.message === "string") {
            // R11-NEW-1: Expose error_class so the UI can show type-specific guidance
            // for errors like payload_too_large (e.g. a "Start new chat" button).
            const err = new Error(evt.message) as Error & { error_class?: string };
            if (typeof evt.error_class === "string") {
              err.error_class = evt.error_class;
            }
            throw err;
          } else if (evt.type === "done") {
            sawDoneEvent = true;
          }
        }
      }
    } finally {
      try {
        await reader.cancel();
      } catch {
        // best-effort; already closed
      }
    }

    // Defensive: stream ended with no text AND no tool_result AND no error
    // event.  This happens when an upstream proxy (Render free tier,
    // Cloudflare) drops the SSE connection before the backend finishes,
    // so the browser sees `done` without any payload.  Without this guard
    // the caller would render a blank assistant bubble with no explanation.
    if (replyParts.length === 0 && actions.length === 0 && streamedActions.length === 0) {
      // Every drop variant is localized via i18n and tagged with
      // error_class = "stream_drop" so _isResumableStreamDrop (and any
      // UI classification) keys on the structured flag, never on the
      // display text.
      const dropKey = sawAnySseEvent
        ? (sawDoneEvent
          ? "error.stream_drop_done_no_reply"
          : sawToolActivity
            ? "error.stream_drop_during_tools"
            : "error.stream_drop_status_only")
        : "error.stream_drop_no_bytes";
      const dropErr = new Error(t(dropKey)) as Error & { error_class?: string };
      dropErr.error_class = "stream_drop";
      throw dropErr;
    }

    return {
      reply: replyParts.join("\n\n"),
      actions: actions.length > 0 ? actions : streamedActions,
      ...(hitIterationCap !== undefined ? { hit_iteration_cap: hitIterationCap } : {}),
      ...(validationSummary !== undefined ? { validation_summary: validationSummary } : {}),
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
      // Tag genuine outages with a machine-readable class so the UI can
      // show outage guidance without matching locale-dependent text.
      const outageErr = new Error(t("error.backend_unreachable")) as Error & { error_class?: string };
      outageErr.error_class = "backend_unreachable";
      throw outageErr;
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

// fetchCitationGraph (Citation Graph API) and crossMatch (Cross-match API)
// were removed 2026-07-03 with their interfaces — dead since the M3 page
// deletions (zero callers outside this file).

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

// searchAlertsCone was removed 2026-07-03 — dead since the M3 page
// deletions (zero callers outside this file).

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


// ── Public comment section (below the Landing page) ──

export interface CommentPublicView {
  id: string;
  author_name: string;
  content: string;
  created_at: string;  // ISO 8601
}

export interface CommentListResponse {
  comments: CommentPublicView[];
  total: number;
  has_more: boolean;
}

export async function fetchComments(
  limit: number = 20,
  offset: number = 0,
): Promise<CommentListResponse> {
  const { data } = await api.get<CommentListResponse>(
    "/api/comments",
    { params: { limit, offset } },
  );
  return data;
}

export async function postComment(
  author_name: string,
  content: string,
): Promise<CommentPublicView> {
  const { data } = await api.post<CommentPublicView>(
    "/api/comments",
    { author_name, content },
  );
  return data;
}

export async function deleteComment(
  id: string,
  adminSecret: string,
): Promise<void> {
  await api.delete(`/api/comments/${id}`, {
    headers: { "X-Admin-Secret": adminSecret },
  });
}

export default api;
