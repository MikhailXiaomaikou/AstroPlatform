/**
 * sendChatMessage must surface the optional validation_summary and
 * hit_iteration_cap flags riding the final SSE text frame (2026-07-03
 * honesty surfacing). Frames without them keep the old shape — the fields
 * are simply absent from the result.
 */
import { describe, it, expect, beforeEach, vi } from "vitest";

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

function sseResponse(body: string) {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
  return { ok: true, body: stream };
}

describe("sendChatMessage validation_summary threading", () => {
  beforeEach(() => {
    localStorageMock.clear();
    vi.clearAllMocks();
  });

  it("returns validation_summary and hit_iteration_cap from the final text frame", async () => {
    const { sendChatMessage } = await import("../api/client");

    const summary = {
      schema_version: 1,
      numeric_gate: "blocked",
      citation_gate: "passed",
      regen_count: 2,
      blocked: true,
      interventions: [{ gate: "numeric_claims", action: "blocked", reason: "regen_exhausted" }],
    };
    const body = [
      `data: ${JSON.stringify({ type: "status", message: "working" })}`,
      "",
      `data: ${JSON.stringify({
        type: "text",
        content: "blocked banner",
        hit_iteration_cap: true,
        validation_summary: summary,
      })}`,
      "",
      `data: ${JSON.stringify({ type: "done" })}`,
      "",
      "",
    ].join("\n");

    const mockFetch = vi.fn().mockResolvedValueOnce(sseResponse(body));
    vi.stubGlobal("fetch", mockFetch);

    const result = await sendChatMessage([{ role: "user", content: "hello" }]);

    expect(result.reply).toBe("blocked banner");
    expect(result.hit_iteration_cap).toBe(true);
    expect(result.validation_summary).toEqual(summary);

    vi.unstubAllGlobals();
  });

  it("keeps the old shape when the backend does not send the new fields", async () => {
    const { sendChatMessage } = await import("../api/client");

    const body = [
      'data: {"type":"text","content":"plain old reply"}',
      "",
      'data: {"type":"done"}',
      "",
      "",
    ].join("\n");

    const mockFetch = vi.fn().mockResolvedValueOnce(sseResponse(body));
    vi.stubGlobal("fetch", mockFetch);

    const result = await sendChatMessage([{ role: "user", content: "hello" }]);

    expect(result.reply).toBe("plain old reply");
    expect(result.validation_summary).toBeUndefined();
    expect(result.hit_iteration_cap).toBeUndefined();
    expect("validation_summary" in result).toBe(false);

    vi.unstubAllGlobals();
  });

  it("ignores a malformed (non-object) validation_summary", async () => {
    const { sendChatMessage } = await import("../api/client");

    const body = [
      'data: {"type":"text","content":"reply","validation_summary":"passed"}',
      "",
      'data: {"type":"done"}',
      "",
      "",
    ].join("\n");

    const mockFetch = vi.fn().mockResolvedValueOnce(sseResponse(body));
    vi.stubGlobal("fetch", mockFetch);

    const result = await sendChatMessage([{ role: "user", content: "hello" }]);
    expect(result.reply).toBe("reply");
    expect(result.validation_summary).toBeUndefined();

    vi.unstubAllGlobals();
  });
});
