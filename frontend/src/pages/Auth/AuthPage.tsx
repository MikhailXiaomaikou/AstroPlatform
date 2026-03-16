import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";

type AuthMode = "login" | "register" | "setup-key";

export default function AuthPage() {
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [setupKey, setSetupKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const { login, register, setupKeyLogin } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      if (mode === "setup-key") {
        await setupKeyLogin(setupKey.trim());
      } else if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, password);
      }
      navigate("/");
    } catch (err: unknown) {
      if (err && typeof err === "object" && "response" in err) {
        const resp = (err as { response: { data: { detail: string } } }).response;
        setError(resp?.data?.detail || "Authentication failed");
      } else {
        setError("Authentication failed");
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
        <h2>
          {mode === "login" && "Sign In"}
          {mode === "register" && "Create Account"}
          {mode === "setup-key" && "Setup Key"}
        </h2>
        <p className="auth-subtitle">
          {mode === "login" && "Welcome back to Astro Platform"}
          {mode === "register" && "Start exploring the universe"}
          {mode === "setup-key" && "Enter your setup key to get started"}
        </p>

        {error && <div className="error-banner">{error}</div>}

        <form onSubmit={handleSubmit} className="auth-form">
          {mode === "setup-key" ? (
            <label className="auth-label">
              Setup Key
              <input
                type="text"
                value={setupKey}
                onChange={(e) => setSetupKey(e.target.value.toUpperCase())}
                placeholder="ASTRO-BETA-XXXXXX"
                required
                className="auth-input auth-input-key"
                autoComplete="off"
                spellCheck={false}
              />
            </label>
          ) : (
            <>
              <label className="auth-label">
                Email
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  required
                  className="auth-input"
                />
              </label>

              <label className="auth-label">
                Password
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder={mode === "login" ? "Your password" : "Min 8 characters"}
                  required
                  minLength={8}
                  className="auth-input"
                />
              </label>
            </>
          )}

          <button type="submit" className="btn-primary auth-submit" disabled={loading}>
            {loading ? <span className="spinner" /> : null}
            {mode === "login" && "Sign In"}
            {mode === "register" && "Create Account"}
            {mode === "setup-key" && "Activate"}
          </button>
        </form>

        <div className="auth-toggles">
          {mode !== "setup-key" && (
            <button className="auth-toggle" onClick={() => switchMode(mode === "login" ? "register" : "login")}>
              {mode === "login" ? "Don't have an account? Sign up" : "Already have an account? Sign in"}
            </button>
          )}

          <button
            className="auth-toggle auth-toggle-key"
            onClick={() => switchMode(mode === "setup-key" ? "login" : "setup-key")}
          >
            {mode === "setup-key" ? "Use email & password instead" : "Have a setup key?"}
          </button>
        </div>
      </div>
    </div>
  );
}
