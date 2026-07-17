import { useState, useEffect, useRef, useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { useI18n } from "../../i18n";
import { getRuntimeConfig, type RuntimeConfig } from "../../api/client";
import { consumeInvitationFromUrl } from "../../utils/invitation";

type AuthMode = "login" | "register" | "invitation";

// Google Identity Services type declarations
declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: {
            client_id: string;
            callback: (response: { credential: string }) => void;
            auto_select?: boolean;
          }) => void;
          renderButton: (
            parent: HTMLElement,
            options: {
              theme?: string;
              size?: string;
              width?: number;
              text?: string;
              shape?: string;
              logo_alignment?: string;
            },
          ) => void;
        };
      };
    };
  }
}

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID || "";

function GoogleSignInButton({ onSuccess, disabled }: { onSuccess: (credential: string) => void; disabled: boolean }) {
  const buttonRef = useRef<HTMLDivElement>(null);
  const [scriptLoaded, setScriptLoaded] = useState(false);
  const [scriptError, setScriptError] = useState(false);

  const handleCredentialResponse = useCallback(
    (response: { credential: string }) => {
      onSuccess(response.credential);
    },
    [onSuccess],
  );

  // Load Google Identity Services script
  useEffect(() => {
    if (!GOOGLE_CLIENT_ID) return;

    const existingScript = document.getElementById("google-gsi-script");
    if (existingScript) {
      // Q3: syncing React state with already-loaded external script.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setScriptLoaded(true);
      return;
    }

    const script = document.createElement("script");
    script.id = "google-gsi-script";
    script.src = "https://accounts.google.com/gsi/client";
    script.async = true;
    script.defer = true;
    script.crossOrigin = "anonymous";
    script.onload = () => setScriptLoaded(true);
    script.onerror = () => setScriptError(true);
    document.head.appendChild(script);

    // H19: clean up on unmount.  The script tag was previously leaked every
    // time the user visited the auth page; repeated nav would append
    // duplicate <script> tags and orphan callbacks.
    return () => {
      const stillMounted = document.getElementById("google-gsi-script");
      if (stillMounted) {
        stillMounted.remove();
      }
    };
  }, []);

  // Initialize and render button once script is loaded
  useEffect(() => {
    if (!scriptLoaded || !window.google || !buttonRef.current || !GOOGLE_CLIENT_ID) return;

    window.google.accounts.id.initialize({
      client_id: GOOGLE_CLIENT_ID,
      callback: handleCredentialResponse,
    });

    window.google.accounts.id.renderButton(buttonRef.current, {
      theme: "outline",
      size: "large",
      width: 320,
      text: "signin_with",
      shape: "rectangular",
      logo_alignment: "left",
    });
  }, [scriptLoaded, handleCredentialResponse]);

  if (!GOOGLE_CLIENT_ID) return null;
  if (scriptError) return null;

  return (
    <div
      ref={buttonRef}
      style={{
        display: "flex",
        justifyContent: "center",
        minHeight: 44,
        opacity: disabled ? 0.5 : 1,
        pointerEvents: disabled ? "none" : "auto",
      }}
    />
  );
}

export default function AuthPage() {
  const { t } = useI18n();
  // Consume the one-time secret before any effect can request /api/config;
  // otherwise the browser may put it in Referer headers, history, and logs.
  const [invitationFromUrl] = useState(consumeInvitationFromUrl);
  const [mode, setMode] = useState<AuthMode>(invitationFromUrl ? "invitation" : "login");
  const [signupMode, setSignupMode] = useState<RuntimeConfig["signup_mode"]>("invite_only");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [invitationKey, setInvitationKey] = useState(() => (
    invitationFromUrl
  ));
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { login, register, redeemInvitation, googleLogin } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [deletionReceipt] = useState(() => (
    location.state as {
      accountDeletionReceipt?: { receipt: string; backupExpiry: string; scheduled: boolean };
    } | null
  )?.accountDeletionReceipt);

  useEffect(() => {
    if (!deletionReceipt) return;
    // Keep the one-time receipt only in this mounted component. Remove it
    // immediately from browser history so reload/back cannot reveal it later.
    navigate(`${location.pathname}${location.search}${location.hash}`, {
      replace: true,
      state: null,
    });
  }, [deletionReceipt, location.hash, location.pathname, location.search, navigate]);

  useEffect(() => {
    let cancelled = false;
    void getRuntimeConfig()
      .then((config) => { if (!cancelled) setSignupMode(config.signup_mode); })
      .catch(() => { /* Fail closed: do not expose public registration. */ });
    return () => { cancelled = true; };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (mode === "invitation") {
        await redeemInvitation(invitationKey.trim(), username, password);
      } else if (mode === "login") {
        await login(username, password);
      } else {
        await register(username, password);
      }
      navigate("/");
    } catch (err: unknown) {
      if (err && typeof err === "object" && "response" in err) {
        const resp = (err as { response: { data: { detail: string } } }).response;
        setError(resp?.data?.detail || t("auth.failed"));
      } else {
        setError(t("auth.failed"));
      }
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleSuccess = async (credential: string) => {
    setError(null);
    setLoading(true);
    try {
      await googleLogin(credential);
      navigate("/");
    } catch (err: unknown) {
      if (err && typeof err === "object" && "response" in err) {
        const resp = (err as { response: { data: { detail: string } } }).response;
        setError(resp?.data?.detail || t("auth.google_failed"));
      } else {
        setError(t("auth.google_failed"));
      }
    } finally {
      setLoading(false);
    }
  };

  function switchMode(newMode: AuthMode) {
    setMode(newMode);
    setError(null);
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        {deletionReceipt && (
          <div className="settings-msg settings-msg-ok" role="status" style={{ marginBottom: 18 }}>
            <strong>Account disabled and deletion accepted.</strong>
            <p>Save this one-time receipt: <code>{deletionReceipt.receipt}</code></p>
            <p>
              Cleanup {deletionReceipt.scheduled ? "was queued" : "will be retried by reconciliation"}.
              Backup exclusion remains enforced by a restore-safe tombstone; backup expiry is {new Date(deletionReceipt.backupExpiry).toLocaleString()}.
            </p>
          </div>
        )}
        <h2>
          {mode === "login" && t("auth.sign_in")}
          {mode === "register" && t("auth.create_account")}
          {mode === "invitation" && "Redeem invitation"}
        </h2>
        <p className="auth-subtitle">
          {mode === "login" && t("auth.welcome")}
          {mode === "register" && t("auth.start")}
          {mode === "invitation" && "Choose your username and password. The invitation works once."}
        </p>
        {signupMode === "invite_only" && mode === "login" && (
          <p className="auth-subtitle">New accounts require a one-time invitation.</p>
        )}
        <p className="auth-subtitle">
          By using this service, review the hosted <a href="/privacy" target="_blank" rel="noreferrer">Privacy Notice / 隐私说明</a>,
          including the real operator and contact for this instance.
        </p>

        {error && <div className="error-banner">{error}</div>}

        {/* Google Sign-In (show on login and register modes) */}
        {mode !== "invitation" && (
          <>
            <GoogleSignInButton onSuccess={handleGoogleSuccess} disabled={loading} />
            {GOOGLE_CLIENT_ID && (
              <div className="auth-divider">
                <span>{t("auth.or")}</span>
              </div>
            )}
          </>
        )}

        <form onSubmit={handleSubmit} className="auth-form">
          {mode === "invitation" ? (
            <>
            <label className="auth-label">
              Invitation key
              <input
                type="text"
                value={invitationKey}
                onChange={(e) => setInvitationKey(e.target.value)}
                placeholder="ASTRO-INV-..."
                required
                className="auth-input auth-input-key"
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <label className="auth-label">
              {t("auth.username")}
              <input
                type="text"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="your-handle"
                required
                className="auth-input"
                autoComplete="username"
              />
            </label>
            <label className="auth-label">
              {t("auth.password")}
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={t("auth.min_8_chars")}
                required
                minLength={8}
                className="auth-input"
                autoComplete="new-password"
              />
            </label>
            </>
          ) : (
            <>
              <label className="auth-label">
                {t("auth.username")}
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="your-handle"
                  required
                  className="auth-input"
                  autoComplete="username"
                />
              </label>

              <label className="auth-label">
                {t("auth.password")}
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={mode === "login" ? t("auth.your_password") : t("auth.min_8_chars")}
                  required
                  minLength={8}
                  className="auth-input"
                />
              </label>
            </>
          )}

          <button type="submit" className="btn-primary auth-submit" disabled={loading}>
            {loading ? <span className="spinner" /> : null}
            {mode === "login" && t("auth.sign_in")}
            {mode === "register" && t("auth.create_account")}
            {mode === "invitation" && "Create account"}
          </button>
        </form>

        <div className="auth-toggles">
          {mode !== "invitation" && signupMode === "public" && (
            <button className="auth-toggle" onClick={() => switchMode(mode === "login" ? "register" : "login")}>
              {mode === "login" ? t("auth.no_account") : t("auth.has_account")}
            </button>
          )}

          <button
            className="auth-toggle auth-toggle-key"
            onClick={() => switchMode(mode === "invitation" ? "login" : "invitation")}
          >
            {mode === "invitation" ? t("auth.use_password") : "I have an invitation"}
          </button>
        </div>
      </div>
    </div>
  );
}
