// AI backend readiness: browser-stored key + server-side configured
// backends. State and effects moved verbatim from ChatPage.tsx.
import { useState, useEffect } from "react";
import {
  getPreferredAiProvider,
  getPreferredAiModelProfile,
  type AIModelProfile,
} from "../../api/client";
import { hasStoredAiKey } from "./chatHelpers";

export function useAiBackendStatus() {
  const [hasKey, setHasKey] = useState(() => hasStoredAiKey());

  // F4.1: in addition to the browser-side stored key, ask the backend
  // which server-side backends are configured (env vars + user's stored
  // server keys).  Either-or is enough to enable the Send button.
  const [serverBackendReady, setServerBackendReady] = useState<boolean | null>(null);
  const [serverBackendList, setServerBackendList] = useState<string[]>([]);
  const [selectedModelStatus, setSelectedModelStatus] = useState<AIModelProfile | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { getAIBackendStatus } = await import("../../api/client");
        const provider = getPreferredAiProvider();
        const modelProfile = getPreferredAiModelProfile(provider);
        const status = await getAIBackendStatus(provider, modelProfile);
        if (cancelled) return;
        setServerBackendReady(!status.needs_setup);
        setServerBackendList(status.configured_backends);
        setSelectedModelStatus(status.selected_model_status || null);
      } catch {
        if (cancelled) return;
        // Unknown should not hard-lock the chat UI.  The send request will
        // surface a real backend error if nothing is configured, but local
        // browser/CORS/proxy hiccups should not force the API-key prompt when
        // a server-side backend may already be available.
        setServerBackendReady(true);
      }
    })();
    return () => { cancelled = true; };
  }, []);
  const aiBackendReady = hasKey || serverBackendReady === true;

  // Re-check API key on mount (picks up keys set in Settings page).
  // PART Y Q3: intentionally re-runs every render to pick up cross-tab
  // localStorage changes; the early return guards against feedback loops.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!hasKey && hasStoredAiKey()) setHasKey(true);
  });

  return {
    hasKey,
    setHasKey,
    serverBackendReady,
    serverBackendList,
    selectedModelStatus,
    aiBackendReady,
  };
}
