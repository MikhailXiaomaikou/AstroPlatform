// AI backend readiness is authoritative on the backend. `hasKey` remains a
// short-lived UI signal after the inline prompt saves a server-side key.
import { useState, useEffect } from "react";
import {
  getPreferredAiProvider,
  getPreferredAiModelProfile,
  type AIModelProfile,
} from "../../api/client";
import { hasStoredAiKey } from "./chatHelpers";

export function useAiBackendStatus() {
  const [hasKey, setHasKey] = useState(() => hasStoredAiKey());

  // Ask the backend which env/user backends are configured. Production's
  // getStoredApiKeys() returns no browser secrets; the initial helper call is
  // retained so existing isolated component tests can model a just-saved key.
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

  return {
    hasKey,
    setHasKey,
    serverBackendReady,
    serverBackendList,
    selectedModelStatus,
    aiBackendReady,
  };
}
