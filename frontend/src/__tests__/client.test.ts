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
    try {
      sessionStorage.clear();
    } catch {
      /* ignore */
    }
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
    const interceptors = (api.interceptors.request as unknown as {
      handlers: Array<{ fulfilled: (c: { headers: Record<string, string> }) => { headers: Record<string, string> } }>;
    }).handlers;
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

    const interceptors = (api.interceptors.request as unknown as {
      handlers: Array<{ fulfilled: (c: { headers: Record<string, string> }) => { headers: Record<string, string> } }>;
    }).handlers;
    const interceptor = interceptors[interceptors.length - 1];
    const config = { headers: {} as Record<string, string> };
    const result = interceptor.fulfilled(config);

    expect(result.headers.Authorization).toBeUndefined();
  });
});

describe("Linked research Bot API helpers", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("uses only the stable automation endpoints", async () => {
    const {
      default: api,
      chatWithAutomationBot,
      getAutomationResearchReport,
      getAutomationResearchStatus,
      triggerAutomationResearch,
    } = await import("../api/client");
    const postSpy = vi.spyOn(api, "post")
      .mockResolvedValueOnce({ data: { reply: "answer", model: "gpt-5.6-sol" } })
      .mockResolvedValueOnce({ data: { submitted: true, status: "pending" } });
    const getSpy = vi.spyOn(api, "get")
      .mockResolvedValueOnce({ data: { week_id: "2026-W28", status: "pending" } })
      .mockResolvedValueOnce({ data: { week_id: "2026-W28", markdown: "report" } });
    const messages = [{ role: "user" as const, content: "hello" }];

    await chatWithAutomationBot(messages);
    await getAutomationResearchStatus();
    await getAutomationResearchReport();
    await triggerAutomationResearch();

    expect(postSpy).toHaveBeenNthCalledWith(1, "/api/automation/bot/chat", { messages });
    expect(getSpy).toHaveBeenNthCalledWith(1, "/api/automation/research/status");
    expect(getSpy).toHaveBeenNthCalledWith(2, "/api/automation/research/report");
    expect(postSpy).toHaveBeenNthCalledWith(2, "/api/automation/research/trigger");

    postSpy.mockRestore();
    getSpy.mockRestore();
  });
});

describe("Workflow Foundry API helpers", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("reads only the formal workflow catalog for registered execution", async () => {
    const { default: api, listRegisteredWorkflows } = await import("../api/client");
    const item = {
      workflow_id: "union3_flat_lcdm_sn_only_v1",
      workflow_version: "1.0.0",
      status: "REGISTERED",
      risk_level: "R3",
      claim_scope: "reproduction_of_published_constraint",
      model: "flat_lcdm",
      dataset_key: "union3",
      compatibility: {
        source_profile_keys: ["union3_arxiv_v1"],
        candidate_types: ["parameter_interval_report"],
        model_scopes: ["flat_lcdm"],
        data_scopes: ["union3_sn_only"],
      },
    };
    const getSpy = vi.spyOn(api, "get").mockResolvedValueOnce({ data: { items: [item], total: 1 } });

    await expect(listRegisteredWorkflows()).resolves.toEqual({ items: [item], total: 1 });
    expect(getSpy).toHaveBeenCalledWith("/api/research/workflows");
    getSpy.mockRestore();
  });

  it("reads the current user's Foundry roles from the self-access endpoint", async () => {
    const { default: api, getFoundrySelfAccess } = await import("../api/client");
    const access = { can_administer: false, can_review: true };
    const getSpy = vi.spyOn(api, "get").mockResolvedValueOnce({ data: access });

    await expect(getFoundrySelfAccess()).resolves.toEqual(access);
    expect(getSpy).toHaveBeenCalledWith("/api/research/foundry-access");
    getSpy.mockRestore();
  });

  it("queues validation with only an immutable version binding", async () => {
    const { default: api, validateAdminFoundryCandidate } = await import("../api/client");
    const binding = {
      candidate_version_id: "version-1",
      candidate_version_hash: "a".repeat(64),
    };
    const receipt = {
      validation_run_id: "validation-1",
      status: "QUEUED",
      candidate_id: "candidate-1",
      ...binding,
      created_at: "2026-07-21T00:00:00Z",
    };
    const postSpy = vi.spyOn(api, "post").mockResolvedValueOnce({ data: receipt });

    await expect(validateAdminFoundryCandidate("candidate-1", binding)).resolves.toEqual(receipt);
    expect(postSpy).toHaveBeenCalledWith(
      "/api/admin/foundry/candidates/candidate-1/validate",
      binding,
    );
    expect(postSpy.mock.calls[0][1]).not.toHaveProperty("status");
    expect(postSpy.mock.calls[0][1]).not.toHaveProperty("results");
    postSpy.mockRestore();
  });

  it("binds formal registration to the exact candidate and verified build receipt", async () => {
    const { default: api, registerAdminFoundryCandidate } = await import("../api/client");
    const versionHash = "a".repeat(64);
    const buildAttestationId = "build-attestation-1";
    const postSpy = vi.spyOn(api, "post").mockResolvedValueOnce({ data: { id: "candidate-1" } });

    await registerAdminFoundryCandidate(
      "candidate-1",
      "version-1",
      versionHash,
      buildAttestationId,
    );
    expect(postSpy).toHaveBeenCalledWith(
      "/api/admin/foundry/candidates/candidate-1/register",
      {
        candidate_version_id: "version-1",
        candidate_version_hash: versionHash,
        build_attestation_id: buildAttestationId,
      },
    );
    postSpy.mockRestore();
  });
});

describe("Auth helper functions", () => {
  beforeEach(() => {
    localStorageMock.clear();
    try {
      sessionStorage.clear();
    } catch {
      /* ignore */
    }
    vi.clearAllMocks();
  });

  it("purges legacy browser API keys instead of persisting new clear-text keys", async () => {
    store["astro_api_keys"] = "legacy-local-secret";
    store["astro_api_keys_persist"] = "1";
    sessionStorage.setItem("astro_api_keys", "legacy-session-secret");
    const { getStoredApiKeys, writeStoredApiKeys } = await import("../api/client");

    writeStoredApiKeys({ anthropic: "sk-ant-new-secret" });

    expect(getStoredApiKeys()).toEqual({});
    expect(sessionStorage.getItem("astro_api_keys")).toBeNull();
    expect(store["astro_api_keys"]).toBeUndefined();
    expect(store["astro_api_keys_persist"]).toBeUndefined();
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

  it("isAuthenticated returns true in local no-auth mode", async () => {
    store["astro_local_no_auth"] = "1";
    const { isAuthenticated, isLocalNoAuthEnabled } = await import("../api/client");
    expect(isLocalNoAuthEnabled()).toBe(true);
    expect(isAuthenticated()).toBe(true);
  });

  it("logout removes the token", async () => {
    store["astro_token"] = "some-token";
    const { logout } = await import("../api/client");
    logout();
    expect(localStorageMock.removeItem).toHaveBeenCalledWith("astro_token");
  });

  it("isolates analytics consent and tracking sessions between browser accounts", async () => {
    const {
      activateBrowserUser,
      default: api,
      getPrivacyPreferences,
      isBrowserAnalyticsEnabled,
    } = await import("../api/client");

    activateBrowserUser("user-a");
    sessionStorage.setItem("astro_tracking_session_id", "session-a");
    let resolveUserA: ((value: { data: { analytics_enabled: boolean; consented_at: null; retention_days: number; research_records_retained_until_user_deletion: boolean } }) => void) | undefined;
    const userAResponse = new Promise<{ data: { analytics_enabled: boolean; consented_at: null; retention_days: number; research_records_retained_until_user_deletion: boolean } }>((resolve) => {
      resolveUserA = resolve;
    });
    const getSpy = vi.spyOn(api, "get").mockReturnValueOnce(userAResponse);
    const staleRequest = getPrivacyPreferences();

    activateBrowserUser("user-b");
    expect(sessionStorage.getItem("astro_tracking_session_id")).toBeNull();
    resolveUserA?.({
      data: {
        analytics_enabled: true,
        consented_at: null,
        retention_days: 30,
        research_records_retained_until_user_deletion: true,
      },
    });
    await staleRequest;

    expect(isBrowserAnalyticsEnabled()).toBe(false);
    expect(store["astro_analytics_enabled:user-b"]).toBeUndefined();

    getSpy.mockResolvedValueOnce({
      data: {
        analytics_enabled: true,
        consented_at: null,
        retention_days: 30,
        research_records_retained_until_user_deletion: true,
      },
    });
    await getPrivacyPreferences();
    expect(isBrowserAnalyticsEnabled()).toBe(true);
    expect(store["astro_analytics_enabled:user-b"]).toBe("1");
    getSpy.mockRestore();
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

  it("sendChatMessage tags genuine outages with error_class backend_unreachable", async () => {
    // Regression: the backend-down error must carry a machine-readable
    // error_class so the UI never has to substring-match display text
    // (which broke on non-English locales and misclassified real outages
    // as payload-too-large).
    const { default: api, sendChatMessage } = await import("../api/client");

    const mockFetch = vi.fn().mockRejectedValueOnce(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", mockFetch);
    // Health probe also fails → the backend is truly down.
    const getSpy = vi.spyOn(api, "get").mockRejectedValueOnce(new Error("health down"));

    const err = await sendChatMessage([{ role: "user", content: "hello" }])
      .then(() => null, (e: unknown) => e as Error & { error_class?: string });

    expect(err).toBeInstanceOf(Error);
    expect(err?.message).toContain("Could not reach the backend server");
    expect(err?.error_class).toBe("backend_unreachable");

    vi.unstubAllGlobals();
    getSpy.mockRestore();
  });

  it("sendChatMessage raises localized stream-drop errors with error_class stream_drop and still resumes once", async () => {
    // Regression: stream-drop errors were hardcoded Chinese for every
    // locale, and the checkpoint-resume retry pattern-matched the Chinese
    // prefix "AI 回复中断". The retry must key on error_class instead, and
    // the default (en) locale must see an English message.
    const { sendChatMessage } = await import("../api/client");

    const statusOnlyBody = 'data: {"type":"status","message":"Thinking..."}\n\n';
    const encoder = new TextEncoder();
    const makeStream = () =>
      new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode(statusOnlyBody));
          controller.close();
        },
      });

    // Both the first attempt and the resume retry drop.
    const mockFetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, body: makeStream() })
      .mockResolvedValueOnce({ ok: true, body: makeStream() });
    vi.stubGlobal("fetch", mockFetch);

    const err = await sendChatMessage([{ role: "user", content: "hello" }])
      .then(() => null, (e: unknown) => e as Error & { error_class?: string });

    expect(err).toBeInstanceOf(Error);
    expect(err?.error_class).toBe("stream_drop");
    // jsdom default locale is en — the user-facing text must be English.
    expect(err?.message).toMatch(/AI reply interrupted/);
    // The single resume retry still fires, with the resume hint attached.
    expect(mockFetch).toHaveBeenCalledTimes(2);
    const retryBody = JSON.parse(String(mockFetch.mock.calls[1][1]?.body || "{}"));
    expect(retryBody.context.resume_from_session).toBe(true);

    vi.unstubAllGlobals();
  });

  it("sendChatMessage distinguishes status-only stream breaks from no-byte breaks", async () => {
    const { sendChatMessage } = await import("../api/client");

    const sseBody = [
      'data: {"type":"status","message":"Thinking..."}\n\n',
      'data: {"type":"status","message":"still thinking... (6s)"}\n\n',
    ].join("");
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sseBody));
        controller.close();
      },
    });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({
      ok: true,
      body: stream,
    }));

    await expect(
      sendChatMessage([{ role: "user", content: "status-only stream regression" }])
    ).rejects.toThrow("only returned status updates");

    vi.unstubAllGlobals();
  });

  it("sendChatMessage accumulates SSE text and tool_result events", async () => {
    store["astro_api_keys"] = JSON.stringify({
      openai: "sk-openai-test",
      anthropic: "sk-ant-test",
    });
    store["astro_ai_provider"] = "openai";
    store["astro_ai_model_profile"] = "openai:gpt-5.5";

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
    const requestBody = JSON.parse(String(mockFetch.mock.calls[0][1]?.body || "{}"));
    expect(requestBody.context.api_provider).toBe("openai");
    expect(requestBody.context.model_profile).toBe("openai:gpt-5.5");
    expect(requestBody.context.api_keys).toBeUndefined();

    vi.unstubAllGlobals();
  });

  it("getPreferredAiModelProfile defaults by provider and rejects cross-provider storage", async () => {
    store["astro_ai_provider"] = "deepseek";
    store["astro_ai_model_profile"] = "openai:gpt-5.5";
    const { getPreferredAiModelProfile } = await import("../api/client");

    expect(getPreferredAiModelProfile("deepseek")).toBe("deepseek:v4-pro");
    expect(getPreferredAiModelProfile("openai")).toBe("openai:gpt-5.5");
    expect(getPreferredAiModelProfile("local")).toBe("local:default");
  });

  it("defaults chat provider to DeepSeek when the user has not selected one", async () => {
    const { getPreferredAiProvider, getPreferredAiModelProfile } = await import("../api/client");

    expect(getPreferredAiProvider()).toBe("deepseek");
    expect(getPreferredAiModelProfile()).toBe("deepseek:v4-pro");
  });

  it("sendChatMessage streams live tool_result actions before final response", async () => {
    const { sendChatMessage } = await import("../api/client");

    const sseBody = [
      'data: {"type":"tool_result","live":true,"tool":"run_adql","result":{"row_count":3}}\n\n',
      'data: {"type":"text","content":"Done"}\n\n',
      'data: {"type":"tool_result","tool":"run_adql","result":{"row_count":3,"rows":[{"id":1}]}}\n\n',
      'data: {"type":"done"}\n\n',
    ].join("");

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sseBody));
        controller.close();
      },
    });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({
      ok: true,
      body: stream,
    }));

    const onActions = vi.fn();
    const result = await sendChatMessage(
      [{ role: "user", content: "hello" }],
      undefined,
      undefined,
      undefined,
      onActions,
    );

    expect(onActions).toHaveBeenCalledTimes(2);
    expect(onActions.mock.calls[0][0]).toEqual([
      expect.objectContaining({
        action: "run_adql",
        _stream_preview: true,
        tool_result: { row_count: 3 },
      }),
    ]);
    expect(onActions.mock.calls[1][0]).toEqual([
      expect.objectContaining({
        action: "run_adql",
        tool_result: { row_count: 3, rows: [{ id: 1 }] },
      }),
    ]);
    expect(result.reply).toBe("Done");
    expect(result.actions).toHaveLength(1);

    vi.unstubAllGlobals();
  });

  it("sendChatMessage keeps later live ADQL success visible while final events replay", async () => {
    const { sendChatMessage } = await import("../api/client");

    const sseBody = [
      'data: {"type":"tool_result","live":true,"tool":"run_adql","tool_call_id":"adql-1","result":{"success":false,"error":"timeout"}}\n\n',
      'data: {"type":"tool_result","live":true,"tool":"run_adql","tool_call_id":"adql-2","result":{"row_count":1000,"columns":["ra"]}}\n\n',
      'data: {"type":"text","content":"Done"}\n\n',
      'data: {"type":"tool_result","tool":"run_adql","tool_call_id":"adql-1","result":{"success":false,"error":"timeout","__tool_status__":"FAILED"}}\n\n',
      'data: {"type":"tool_result","tool":"run_adql","tool_call_id":"adql-2","result":{"row_count":1000,"columns":["ra"],"attempt_log":[{"stage":"mirror_success","message":"fallback succeeded"}]}}\n\n',
      'data: {"type":"done"}\n\n',
    ].join("");

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sseBody));
        controller.close();
      },
    });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({
      ok: true,
      body: stream,
    }));

    const onActions = vi.fn();
    const result = await sendChatMessage(
      [{ role: "user", content: "hello" }],
      undefined,
      undefined,
      undefined,
      onActions,
    );

    const afterFirstFinal = onActions.mock.calls[2][0];
    expect(afterFirstFinal).toHaveLength(2);
    expect(afterFirstFinal[1]).toEqual(expect.objectContaining({
      action: "run_adql",
      _tool_call_id: "adql-2",
      tool_result: expect.objectContaining({ row_count: 1000 }),
    }));
    expect(result.actions).toHaveLength(2);
    expect(result.actions[1].tool_result).toEqual(expect.objectContaining({
      attempt_log: [expect.objectContaining({ stage: "mirror_success" })],
    }));

    vi.unstubAllGlobals();
  });

  it("sendChatMessage forwards ADQL tool_progress events to thinking subscribers", async () => {
    const { sendChatMessage } = await import("../api/client");

    const sseBody = [
      'data: {"type":"tool_progress","tool":"run_adql","stage":"mirror_attempt","message":"Trying VizieR mirror 1/4","mirror_index":1}\n\n',
      'data: {"type":"text","content":"Done"}\n\n',
      'data: {"type":"done"}\n\n',
    ].join("");

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sseBody));
        controller.close();
      },
    });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({
      ok: true,
      body: stream,
    }));

    const onThinking = vi.fn();
    await sendChatMessage(
      [{ role: "user", content: "hello" }],
      undefined,
      onThinking,
    );

    expect(onThinking).toHaveBeenCalledWith(expect.objectContaining({
      type: "tool_progress",
      tool: "run_adql",
      stage: "mirror_attempt",
      message: "Trying VizieR mirror 1/4",
    }));

    vi.unstubAllGlobals();
  });

  it("sendChatMessage enables debug_stream and forwards workflow events", async () => {
    const { sendChatMessage } = await import("../api/client");

    const sseBody = [
      'data: {"type":"stream_debug","stage":"stream_open","elapsed_ms":0}\n\n',
      'data: {"type":"workflow_budget","mode":"long","agent_loop_seconds":900,"summary_reserve_seconds":90,"max_iterations":18}\n\n',
      'data: {"type":"workflow_checkpoint","tool_name":"run_adql","status":"completed","cache_refs":["latest_adql"],"summary":"3 rows"}\n\n',
      'data: {"type":"text","content":"Done"}\n\n',
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
    const consoleSpy = vi.spyOn(console, "debug").mockImplementation(() => undefined);

    const onThinking = vi.fn();
    const result = await sendChatMessage(
      [{ role: "user", content: "debug" }],
      { debug_stream: true },
      onThinking,
    );

    expect(String(mockFetch.mock.calls[0][0])).toContain("debug_stream=1");
    expect(consoleSpy).toHaveBeenCalledWith("[astro:sse]", "stream_debug", expect.any(Object));
    expect(onThinking).toHaveBeenCalledWith(expect.objectContaining({
      type: "workflow_budget",
      mode: "long",
      agent_loop_seconds: 900,
    }));
    expect(onThinking).toHaveBeenCalledWith(expect.objectContaining({
      type: "workflow_checkpoint",
      tool_name: "run_adql",
      status: "completed",
      cache_refs: ["latest_adql"],
    }));
    expect(result.reply).toBe("Done");

    consoleSpy.mockRestore();
    vi.unstubAllGlobals();
  });

  it("sendChatMessage emits live tool_result actions before an SSE error", async () => {
    const { sendChatMessage } = await import("../api/client");

    const sseBody = [
      'data: {"type":"tool_result","live":true,"tool":"run_adql","result":{"row_count":3}}\n\n',
      'data: {"type":"error","message":"AI workflow timed out after 420s"}\n\n',
    ].join("");

    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(sseBody));
        controller.close();
      },
    });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce({
      ok: true,
      body: stream,
    }));

    const onActions = vi.fn();
    await expect(sendChatMessage(
      [{ role: "user", content: "hello" }],
      undefined,
      undefined,
      undefined,
      onActions,
    )).rejects.toThrow("AI workflow timed out after 420s");

    expect(onActions).toHaveBeenCalledWith([
      expect.objectContaining({
        action: "run_adql",
        _stream_preview: true,
        tool_result: { row_count: 3 },
      }),
    ]);

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

    // 2026-07-03: the M3-dead exports (pipeline / scheduler / SAMP /
    // batch-search / WCS-grid...) were deleted from client.ts; only
    // functions with live callers are pinned here.
    const expectedFunctions = [
      "searchData",
      "fetchObject",
      "getFITSHeader",
      "getFITSSpectrum",
      "inviteTeamMember",
      "getTeamMembers",
      "sharePipeline",
      "getSharedPipelines",
      "addPipelineComment",
    ] as const;

    for (const fnName of expectedFunctions) {
      expect(typeof (client as unknown as Record<string, unknown>)[fnName]).toBe("function");
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
