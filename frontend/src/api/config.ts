// Public runtime config — Action 6 (Cosmology Focus, 2026-05-08).
//
// Tiny standalone module so it doesn't have to live inside client.ts
// (which is sometimes git-skip-worktree'd for local dev).  Reuses the
// same axios baseURL via the shared `api` instance.

import api from "./client";

export interface BackendConfig {
  /** "cosmology" | "all" — which research focus the backend is gating to. */
  focus: string;
}

export async function getBackendConfig(): Promise<BackendConfig> {
  const r = await api.get("/api/config");
  return r.data as BackendConfig;
}
