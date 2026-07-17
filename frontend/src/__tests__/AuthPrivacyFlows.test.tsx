import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";

const api = vi.hoisted(() => ({
  activateBrowserUser: vi.fn(),
  getPrivacyPreferences: vi.fn(),
  getProfile: vi.fn(),
  getRuntimeConfig: vi.fn(),
  googleLogin: vi.fn(),
  isAuthenticated: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  redeemInvitation: vi.fn(),
  register: vi.fn(),
  setupKeyLogin: vi.fn(),
}));

vi.mock("../api/client", () => api);

import { AuthProvider, useAuth } from "../context/AuthContext";
import { I18nProvider } from "../i18n";
import AuthPage from "../pages/Auth/AuthPage";
import PrivacyPage from "../pages/Privacy/PrivacyPage";

const profile = {
  id: "user-1",
  username: "alpha-user",
  email: "alpha@example.invalid",
  subscription_tier: "starter",
  stripe_customer_id: null,
  display_name: null,
  avatar_url: null,
  google_linked: false,
};

function AuthHarness() {
  const { user, login, logout } = useAuth();
  const location = useLocation();
  return (
    <>
      <div data-testid="path">{location.pathname}</div>
      {user && <div data-testid="sensitive">masked-key-and-research-state</div>}
      <button onClick={() => { void login("alpha-user", "password123"); }}>Log in</button>
      <button onClick={() => logout()}>Log out</button>
    </>
  );
}

function LocationStateProbe() {
  const location = useLocation();
  return <div data-testid="location-state">{location.state ? "state-present" : "state-cleared"}</div>;
}

function renderAuthPage(initialEntry: string | { pathname: string; state?: unknown } = "/auth") {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[initialEntry]}>
        <AuthProvider>
          <AuthPage />
          <LocationStateProbe />
        </AuthProvider>
      </MemoryRouter>
    </I18nProvider>,
  );
}

describe("authenticated privacy boundaries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.history.replaceState({}, "", "/auth");
    api.isAuthenticated.mockReturnValue(false);
    api.getProfile.mockResolvedValue(profile);
    api.getPrivacyPreferences.mockResolvedValue({
      analytics_enabled: false,
      consented_at: null,
      retention_days: 30,
      research_records_retained_until_user_deletion: true,
    });
    api.getRuntimeConfig.mockResolvedValue({
      focus: "cosmology",
      signup_mode: "invite_only",
      claim_audit_enabled: false,
      analytics_requires_consent: true,
      privacy_notice: {
        operator_name: "Example Observatory",
        contact: "privacy@example.invalid",
        jurisdiction: "Example jurisdiction",
        notice_url: "/privacy",
      },
    });
  });

  it("ordinary logout navigates away and unmounts protected in-memory state", async () => {
    render(
      <MemoryRouter initialEntries={["/account"]}>
        <AuthProvider><AuthHarness /></AuthProvider>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByTestId("sensitive")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Log out" }));
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/auth"));
    expect(screen.queryByTestId("sensitive")).not.toBeInTheDocument();
    expect(api.logout).toHaveBeenCalledOnce();
  });

  it("keeps a fragment invitation in the auth tab while privacy opens separately", async () => {
    window.history.replaceState({}, "", "/auth#invite=ASTRO-INV-one-time-secret");
    renderAuthPage();

    const invitation = await screen.findByLabelText("Invitation key");
    expect(invitation).toHaveValue("ASTRO-INV-one-time-secret");
    expect(window.location.hash).toBe("");
    const privacyLink = screen.getByRole("link", { name: /Privacy Notice/ });
    expect(privacyLink).toHaveAttribute("target", "_blank");
    expect(privacyLink).toHaveAttribute("rel", "noreferrer");
    expect(invitation).toHaveValue("ASTRO-INV-one-time-secret");
  });

  it("shows the deletion receipt once and removes it from browser history state", async () => {
    renderAuthPage({
      pathname: "/auth",
      state: {
        accountDeletionReceipt: {
          receipt: "delete-once-receipt",
          backupExpiry: "2026-08-16T00:00:00Z",
          scheduled: true,
        },
      },
    });

    expect(await screen.findByText("delete-once-receipt")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("location-state")).toHaveTextContent("state-cleared"));
    expect(screen.getByText("delete-once-receipt")).toBeInTheDocument();
  });

  it("fails closed when hosted operator identity is blank", async () => {
    api.getRuntimeConfig.mockResolvedValueOnce({
      privacy_notice: {
        operator_name: " ",
        contact: "",
        jurisdiction: "",
        notice_url: "/privacy",
      },
    });
    render(
      <MemoryRouter><PrivacyPage /></MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "did not publish its required operator details",
    );
  });
});
