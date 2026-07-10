import { describe, expect, it } from "vitest";

import {
  deserializeDisplayMessage,
  serializeDisplayMessage,
} from "../pages/Chat/chatHelpers";

describe("chat evidence persistence", () => {
  it("preserves validation and truncation across every shared decoder", () => {
    const decoded = deserializeDisplayMessage({
      role: "assistant",
      content: "preliminary result",
      actions: [{ action: "fit_cosmology_mcmc", tool_result: { success: true } }],
      _validation: {
        numeric_gate: "passed",
        citation_gate: "passed",
        overall: "passed",
      },
      _truncated: true,
    });

    expect(decoded._validation?.numeric_gate).toBe("passed");
    expect(decoded._truncated).toBe(true);
    expect(decoded.actions?.[0].action).toBe("fit_cosmology_mcmc");

    const encoded = serializeDisplayMessage(decoded);
    expect(encoded._validation).toEqual(decoded._validation);
    expect(encoded._truncated).toBe(true);
  });

  it("filters malformed action evidence instead of casting it as trusted", () => {
    const decoded = deserializeDisplayMessage({
      role: "assistant",
      content: "message",
      actions: [{ tool_result: { answer: 42 } }, null, { action: 7 }],
    });

    expect(decoded.actions).toBeUndefined();
  });
});
