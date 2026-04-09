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

  it("sets timeout to 120 seconds", async () => {
    const { default: api } = await import("../api/client");
    expect(api.defaults.timeout).toBe(120000);
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

    const result = await register("user@example.com", "password123");

    expect(postSpy).toHaveBeenCalledWith("/api/auth/register", {
      email: "user@example.com",
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

    const result = await login("user@example.com", "pass456");

    expect(postSpy).toHaveBeenCalledWith("/api/auth/login", {
      email: "user@example.com",
      password: "pass456",
    });
    expect(localStorageMock.setItem).toHaveBeenCalledWith(
      "astro_token",
      "login-token-xyz"
    );
    expect(result.access_token).toBe("login-token-xyz");
    postSpy.mockRestore();
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
    };

    expect(result.error_type).toBeNull();
    expect("error_type" in result).toBe(true);
  });
});
