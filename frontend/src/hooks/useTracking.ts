import { useCallback, useMemo } from "react";

const SESSION_KEY = "astro_tracking_session_id";
const PAGE_KEY = "astro_current_page";
const EVENT_COUNT_KEY = "astro_tracking_event_count";
const API_BASE =
  import.meta.env.VITE_API_URL ||
  (import.meta.env.DEV || import.meta.env.MODE === "test" ? "http://localhost:8000" : "");

function getOrCreateSessionId(): string {
  const existing = sessionStorage.getItem(SESSION_KEY);
  if (existing) return existing;
  const created = crypto.randomUUID();
  sessionStorage.setItem(SESSION_KEY, created);
  sessionStorage.setItem(EVENT_COUNT_KEY, "0");
  return created;
}

function incrementEventCount(): void {
  const current = Number(sessionStorage.getItem(EVENT_COUNT_KEY) || "0");
  sessionStorage.setItem(EVENT_COUNT_KEY, String(current + 1));
}

function buildPayload(eventType: string, eventData: Record<string, unknown>, sessionId: string): string {
  return JSON.stringify({
    event_type: eventType,
    event_data: eventData,
    session_id: sessionId,
    page: sessionStorage.getItem(PAGE_KEY) || undefined,
  });
}

export function useTracking() {
  const sessionId = useMemo(() => getOrCreateSessionId(), []);

  const setCurrentPage = useCallback((pageName: string) => {
    sessionStorage.setItem(PAGE_KEY, pageName);
  }, []);

  const getEventCount = useCallback(() => Number(sessionStorage.getItem(EVENT_COUNT_KEY) || "0"), []);

  const track = useCallback((eventType: string, data: Record<string, unknown> = {}) => {
    const payload = buildPayload(eventType, data, sessionId);
    incrementEventCount();

    fetch(`${API_BASE}/api/events/track`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(localStorage.getItem("astro_token")
          ? { Authorization: `Bearer ${localStorage.getItem("astro_token")}` }
          : {}),
      },
      body: payload,
      keepalive: true,
    }).catch(() => {});
  }, [sessionId]);

  return { track, sessionId, setCurrentPage, getEventCount };
}
