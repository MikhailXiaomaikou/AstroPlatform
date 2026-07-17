import { describe, expect, it } from "vitest";

import { consumeInvitationFromUrl } from "../utils/invitation";

describe("Auth invitation URL handling", () => {
  it("removes the raw invitation before later requests can leak it", () => {
    window.history.replaceState(
      {},
      "",
      "/auth?next=welcome#invite=ASTRO-INV-secret-value",
    );

    expect(consumeInvitationFromUrl()).toBe("ASTRO-INV-secret-value");
    expect(window.location.pathname).toBe("/auth");
    expect(window.location.search).toBe("?next=welcome");
    expect(window.location.hash).toBe("");
    expect(window.location.href).not.toContain("ASTRO-INV-secret-value");
  });

  it("rejects unsafe query-string invitations after removing them", () => {
    window.history.replaceState({}, "", "/auth?invite=ASTRO-INV-leaky&next=welcome");

    expect(consumeInvitationFromUrl()).toBe("");
    expect(window.location.search).toBe("?next=welcome");
    expect(window.location.href).not.toContain("ASTRO-INV-leaky");
  });
});
