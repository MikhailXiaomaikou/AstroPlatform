export interface WorkspaceCacheFile {
  id: string;
  source: string;
  object_id: string;
  fits_path: string;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
  local_only?: boolean;
}

export interface WorkspaceExportRegistration {
  id?: string;
  filename: string;
  storagePath: string;
  exportKind: "markdown" | "notebook" | "html" | "latex" | "bibtex";
  contentType: string;
  sizeBytes: number;
  localOnly?: boolean;
}

// Stage 3 Bug 2 修复: 给 workspace 文件缓存加 scope, 防止多账号共用同一台
// 电脑时, 上个用户的 FITS / export 列表泄漏到下个用户的 chat 上下文.
// scope 由 caller 提供 (一般是 `user:<id>` 或 `anon`), 跟 chat history scope 一致.
const WORKSPACE_CACHE_KEY_BASE = "astro_workspace_files";

function workspaceCacheKey(scope: string): string {
  return `${WORKSPACE_CACHE_KEY_BASE}:${scope}`;
}

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

export function readWorkspaceCache(scope: string): WorkspaceCacheFile[] {
  try {
    const raw = localStorage.getItem(workspaceCacheKey(scope));
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

export function writeWorkspaceCache(files: WorkspaceCacheFile[], scope: string): void {
  try {
    localStorage.setItem(workspaceCacheKey(scope), JSON.stringify(files));
  } catch {
    // ignore quota and storage errors
  }
}

export function mergeWorkspaceFiles(files: WorkspaceCacheFile[], scope: string): WorkspaceCacheFile[] {
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
  writeWorkspaceCache(mergedFiles, scope);
  return mergedFiles;
}

export function upsertWorkspaceFile(file: Partial<WorkspaceCacheFile> & Pick<WorkspaceCacheFile, "source" | "object_id" | "fits_path">, scope: string): WorkspaceCacheFile[] {
  const next = mergeWorkspaceFiles([normalizeWorkspaceFile(file), ...readWorkspaceCache(scope)], scope);
  return next;
}

export function findWorkspaceFile(source: string, objectId: string, scope: string): WorkspaceCacheFile | undefined {
  return readWorkspaceCache(scope).find((file) => file.source === source && file.object_id === objectId);
}

export function registerWorkspaceExport(exportFile: WorkspaceExportRegistration, scope: string): WorkspaceCacheFile[] {
  return upsertWorkspaceFile({
    id: exportFile.id,
    source: "export",
    object_id: exportFile.filename,
    fits_path: exportFile.storagePath,
    created_at: new Date().toISOString(),
    local_only: exportFile.localOnly,
    metadata: {
      workspace_kind: "generic",
      export_kind: exportFile.exportKind,
      original_filename: exportFile.filename,
      content_type: exportFile.contentType,
      size_bytes: exportFile.sizeBytes,
    },
  }, scope);
}

// buildPipelineDraft removed: M3 (2026-05-18) deleted /pipeline page,
// so this helper has 0 callers in frontend/src/. Only ChatPage.test.tsx
// mock referenced the export — the mock entry is removed in the same commit.
