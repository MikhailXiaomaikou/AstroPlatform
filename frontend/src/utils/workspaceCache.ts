export interface WorkspaceCacheFile {
  id: string;
  source: string;
  object_id: string;
  fits_path: string;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
  local_only?: boolean;
}

const WORKSPACE_CACHE_KEY = "astro_workspace_files";

function normalizeWorkspaceFile(file: Partial<WorkspaceCacheFile> & Pick<WorkspaceCacheFile, "source" | "object_id" | "fits_path">): WorkspaceCacheFile {
  const createdAt = file.created_at || new Date().toISOString();
  return {
    id: file.id || `local:${file.source}:${file.object_id}:${file.fits_path}`,
    source: file.source,
    object_id: file.object_id,
    fits_path: file.fits_path,
    metadata: file.metadata || null,
    created_at: createdAt,
    local_only: file.local_only ?? !file.id,
  };
}

export function readWorkspaceCache(): WorkspaceCacheFile[] {
  try {
    const raw = localStorage.getItem(WORKSPACE_CACHE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is WorkspaceCacheFile => !!item && typeof item === "object")
      .filter((item) => item.source && item.object_id && item.fits_path)
      .map((item) => normalizeWorkspaceFile(item));
  } catch {
    return [];
  }
}

export function writeWorkspaceCache(files: WorkspaceCacheFile[]): void {
  try {
    localStorage.setItem(WORKSPACE_CACHE_KEY, JSON.stringify(files));
  } catch {
    // ignore quota and storage errors
  }
}

export function mergeWorkspaceFiles(files: WorkspaceCacheFile[]): WorkspaceCacheFile[] {
  const merged = new Map<string, WorkspaceCacheFile>();
  for (const file of files) {
    const normalized = normalizeWorkspaceFile(file);
    const key = normalized.fits_path || `${normalized.source}:${normalized.object_id}`;
    const existing = merged.get(key);
    if (!existing) {
      merged.set(key, normalized);
      continue;
    }
    merged.set(key, {
      ...existing,
      ...normalized,
      metadata: normalized.metadata || existing.metadata,
      created_at: normalized.created_at || existing.created_at,
      local_only: existing.local_only && normalized.local_only,
    });
  }
  const mergedFiles = [...merged.values()].sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
  writeWorkspaceCache(mergedFiles);
  return mergedFiles;
}

export function upsertWorkspaceFile(file: Partial<WorkspaceCacheFile> & Pick<WorkspaceCacheFile, "source" | "object_id" | "fits_path">): WorkspaceCacheFile[] {
  const next = mergeWorkspaceFiles([normalizeWorkspaceFile(file), ...readWorkspaceCache()]);
  return next;
}

export function findWorkspaceFile(source: string, objectId: string): WorkspaceCacheFile | undefined {
  return readWorkspaceCache().find((file) => file.source === source && file.object_id === objectId);
}

export function buildPipelineDraft(inputDataId: string) {
  return {
    nodes: [
      { id: "n1", type: "LoadData", position: { x: 0, y: 150 }, data: { label: "Load Data", params: { fits_path: inputDataId }, nodeType: "LoadData" } },
      { id: "n2", type: "Denoise", position: { x: 300, y: 150 }, data: { label: "Denoise", params: { sigma: 3 }, nodeType: "Denoise" } },
      { id: "n3", type: "InteractivePlot", position: { x: 600, y: 150 }, data: { label: "Plot", params: {}, nodeType: "InteractivePlot" } },
    ],
    edges: [
      { id: "e1-2", source: "n1", target: "n2" },
      { id: "e2-3", source: "n2", target: "n3" },
    ],
    inputDataId,
  };
}
