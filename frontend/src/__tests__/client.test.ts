/**
 * Tests for the API client module (client.ts).
 *
 * These tests verify that axios is configured correctly and that
 * the auth token interceptor works as expected.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

// Mock localStorage before importing the module
const store: Record<string, string> = {};
const localStorageMock = {
  getItem: vi.fn((key: string) => store[key] ?? null),
  setItem: vi.fn((key: string, value: string) => {
    store[key] = value;
  }),
  removeItem: vi.fn((key: string) => {
    delete store[key];
  }),
  clear: vi.fn(() => {
    Object.keys(store).forEach((k) => delete store[k]);
  }),
  get length() {
    return Object.keys(store).length;
  },
  key: vi.fn((_i: number) => null),
};

Object.defineProperty(globalThis, "localStorage", { value: localStorageMock });

describe("API client configuration", () => {
  beforeEach(() => {
    localStorageMock.clear();
    vi.clearAllMocks();
  });

  it("creates axios instance with correct baseURL", async () => {
    const { default: api } = await import("../api/client");
    expect(api.defaults.baseURL).toBe("http://localhost:8000");
  });

  it("sets timeout to 300 seconds", async () => {
    const { default: api } = await import("../api/client");
    expect(api.defaults.timeout).toBe(300000);
  });

  it("attaches Authorization header when token is present", async () => {
    const { default: api } = await import("../api/client");

    store["astro_token"] = "test-jwt-token-123";

    // Simulate the interceptor by manually invoking it
    const interceptors = (api.interceptors.request as any).handlers;
    const interceptor = interceptors[interceptors.length - 1];
    const config = { headers: {} as Record<string, string> };
    const result = interceptor.fulfilled(config);

    expect(localStorageMock.getItem).toHaveBeenCalledWith("astro_token");
    expect(result.headers.Authorization).toBe("Bearer test-jwt-token-123");
  });

  it("does not attach Authorization header when no token", async () => {
    const { default: api } = await import("../api/client");

    // Ensure no token
    delete store["astro_token"];

    const interceptors = (api.interceptors.request as any).handlers;
    const interceptor = interceptors[interceptors.length - 1];
    const config = { headers: {} as Record<string, string> };
    const result = interceptor.fulfilled(config);

    expect(result.headers.Authorization).toBeUndefined();
  });
});

describe("Auth helper functions", () => {
  beforeEach(() => {
    localStorageMock.clear();
    vi.clearAllMocks();
  });

  it("isAuthenticated returns false when no token", async () => {
    const { isAuthenticated } = await import("../api/client");
    expect(isAuthenticated()).toBe(false);
  });

  it("isAuthenticated returns true when token exists", async () => {
    store["astro_token"] = "some-token";
    const { isAuthenticated } = await import("../api/client");
    expect(isAuthenticated()).toBe(true);
  });

  it("logout removes the token", async () => {
    store["astro_token"] = "some-token";
    const { logout } = await import("../api/client");
    logout();
    expect(localStorageMock.removeItem).toHaveBeenCalledWith("astro_token");
  });

  it("register stores token in localStorage", async () => {
    const { default: api, register } = await import("../api/client");

    // Mock api.post to return a fake token response
    const postSpy = vi.spyOn(api, "post").mockResolvedValueOnce({
      data: { access_token: "register-token-abc", token_type: "bearer" },
    });

    const result = await register("astro_user", "password123");

    expect(postSpy).toHaveBeenCalledWith("/api/auth/register", {
      username: "astro_user",
      password: "password123",
    });
    expect(localStorageMock.setItem).toHaveBeenCalledWith(
      "astro_token",
      "register-token-abc"
    );
    expect(result.access_token).toBe("register-token-abc");
    postSpy.mockRestore();
  });

  it("login stores token in localStorage", async () => {
    const { default: api, login } = await import("../api/client");

    const postSpy = vi.spyOn(api, "post").mockResolvedValueOnce({
      data: { access_token: "login-token-xyz", token_type: "bearer" },
    });

    const result = await login("astro_user", "pass456");

    expect(postSpy).toHaveBeenCalledWith("/api/auth/login", {
      username: "astro_user",
      password: "pass456",
    });
    expect(localStorageMock.setItem).toHaveBeenCalledWith(
      "astro_token",
      "login-token-xyz"
    );
    expect(result.access_token).toBe("login-token-xyz");
    postSpy.mockRestore();
  });

  it("sendChatMessage surfaces backend detail errors via SSE", async () => {
    const { sendChatMessage } = await import("../api/client");

    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: () => Promise.resolve({ detail: "AI assistant not configured" }),
    });
    vi.stubGlobal("fetch", mockFetch);

    await expect(
      sendChatMessage([{ role: "user", content: "hello" }])
    ).rejects.toThrow("AI assistant not configured");

    vi.unstubAllGlobals();
  });

  it("sendChatMessage retries one cold-start stream failure", async () => {
    vi.useFakeTimers();
    const { sendChatMessage } = await import("../api/client");

    const sseBody = 'data: {"type":"text","content":"awake"}\n\n';
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sseBody));
        controller.close();
      },
    });

    const waking = vi.fn();
    window.addEventListener("astro:backend-waking", waking);
    const mockFetch = vi.fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 503,
        statusText: "Service Unavailable",
        json: () => Promise.resolve({ detail: "warming" }),
      })
      .mockResolvedValueOnce({
        ok: true,
        body: stream,
      });
    vi.stubGlobal("fetch", mockFetch);

    const pending = sendChatMessage([{ role: "user", content: "hello" }]);
    await vi.advanceTimersByTimeAsync(5000);
    const result = await pending;

    expect(result.reply).toBe("awake");
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(waking).toHaveBeenCalledTimes(1);

    window.removeEventListener("astro:backend-waking", waking);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("sendChatMessage explains connectivity failures after fetch errors", async () => {
    const { default: api, sendChatMessage } = await import("../api/client");

    const mockFetch = vi.fn().mockRejectedValueOnce(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", mockFetch);
    const getSpy = vi.spyOn(api, "get").mockResolvedValueOnce({ data: { status: "ok" } });

    await expect(
      sendChatMessage([{ role: "user", content: "hello" }])
    ).rejects.toThrow("Connection to AI provider was interrupted");

    vi.unstubAllGlobals();
    getSpy.mockRestore();
  });

  it("sendChatMessage accumulates SSE text and tool_result events", async () => {
    store["astro_api_keys"] = JSON.stringify({
      openai: "sk-openai-test",
      anthropic: "sk-ant-test",
    });
    store["astro_ai_provider"] = "openai";

    const { sendChatMessage } = await import("../api/client");

    const sseBody = [
      'data: {"type":"text","content":"Hello"}\n\n',
      'data: {"type":"tool_result","tool":"search_objects","result":{"ok":true}}\n\n',
      'data: {"type":"done"}\n\n',
    ].join("");

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sseBody));
        controller.close();
      },
    });

    const mockFetch = vi.fn().mockResolvedValueOnce({
      ok: true,
      body: stream,
    });
    vi.stubGlobal("fetch", mockFetch);

    const result = await sendChatMessage([{ role: "user", content: "hello" }], { page: "chat" });

    expect(result.reply).toBe("Hello");
    expect(result.actions).toHaveLength(1);
    expect(result.actions[0].action).toBe("search_objects");

    vi.unstubAllGlobals();
  });

  it("getAlerts uses the canonical trailing-slash endpoint", async () => {
    const { default: api, getAlerts } = await import("../api/client");

    const getSpy = vi.spyOn(api, "get").mockResolvedValueOnce({
      data: { count: 1, alerts: [{ id: "1", source: "ztf", source_id: "ZTF24abc", ra: 1, dec: 2, discovery_date: null, magnitude: null, mag_band: null, classification: null, classification_confidence: null, redshift: null, host_galaxy: null }] },
    });

    const alerts = await getAlerts({ limit: 5 });

    expect(getSpy).toHaveBeenCalledWith("/api/alerts/", { params: { limit: 5 } });
    expect(alerts).toHaveLength(1);
    getSpy.mockRestore();
  });

  it("getAlerts explains alerts API failures after network errors", async () => {
    const { default: api, getAlerts } = await import("../api/client");

    const getSpy = vi.spyOn(api, "get")
      .mockRejectedValueOnce({ isAxiosError: true, message: "Network Error" })
      .mockResolvedValueOnce({ data: { status: "ok" } });

    await expect(getAlerts()).rejects.toThrow(
      "The backend did not return a valid alerts response. Check the alerts API route and server logs."
    );

    expect(getSpy).toHaveBeenNthCalledWith(1, "/api/alerts/", { params: undefined });
    expect(getSpy).toHaveBeenNthCalledWith(2, "/health", { timeout: 10000 });
    getSpy.mockRestore();
  });
});

describe("API function exports", () => {
  it("exports all expected API functions", async () => {
    const client = await import("../api/client");

    const expectedFunctions = [
      "searchData",
      "fetchObject",
      "getFITSHeader",
      "getFITSSpectrum",
      "getFITSWCS",
      "getNodeTypes",
      "getTemplates",
      "runPipeline",
      "saveTemplateVersion",
      "getTemplateVersions",
      "getTemplateDiff",
      "exportRunCSV",
      "exportRunVOTable",
      "exportRunPDF",
      "inviteTeamMember",
      "getTeamMembers",
      "sharePipeline",
      "getSharedPipelines",
      "addPipelineComment",
      "createSchedule",
      "listSchedules",
      "toggleSchedule",
      "deleteSchedule",
      "batchSearch",
      "sampStatus",
      "adqlQuery",
    ] as const;

    for (const fnName of expectedFunctions) {
      expect(typeof (client as any)[fnName]).toBe("function");
    }
  });
});

describe("SearchResult interface compatibility", () => {
  it("supports error_type field", async () => {
    // Create an object matching the SearchResult interface
    const result: import("../api/client").SearchResult = {
      source: "sdss",
      object_id: "SDSS-J001",
      name: "Test Star",
      ra: 180.0,
      dec: 45.0,
      object_type: "star",
      magnitude: 12.5,
      redshift: null,
      extra: {},
      error_type: "timeout",
      z_source: null,
      photo_z: null,
      photo_z_err: null,
    };

    expect(result.error_type).toBe("timeout");
    expect(result.source).toBe("sdss");
    expect(result.object_id).toBe("SDSS-J001");
  });

  it("allows null error_type", async () => {
    const result: import("../api/client").SearchResult = {
      source: "gaia",
      object_id: "Gaia-DR3-123",
      name: "Another Star",
      ra: 90.0,
      dec: -30.0,
      object_type: "galaxy",
      magnitude: null,
      redshift: 0.05,
      extra: { survey: "DR3" },
      error_type: null,
      z_source: "spectroscopic",
      photo_z: null,
      photo_z_err: null,
    };

    expect(result.error_type).toBeNull();
    expect("error_type" in result).toBe(true);
  });
});
