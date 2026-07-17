import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import {
  getProfile,
  getPrivacyPreferences,
  isAuthenticated,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
  redeemInvitation as apiRedeemInvitation,
  setupKeyLogin as apiSetupKeyLogin,
  googleLogin as apiGoogleLogin,
  activateBrowserUser,
  type UserProfile,
} from "../api/client";

interface AuthState {
  user: UserProfile | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  setupKeyLogin: (key: string) => Promise<void>;
  redeemInvitation: (key: string, username: string, password: string) => Promise<void>;
  googleLogin: (credential: string) => Promise<void>;
  logout: (options?: { state?: unknown }) => void;
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  login: async () => {},
  register: async () => {},
  setupKeyLogin: async () => {},
  redeemInvitation: async () => {},
  googleLogin: async () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  const installProfile = useCallback((profile: UserProfile) => {
    activateBrowserUser(profile.id);
    setUser(profile);
    // Consent is server-owned. Refresh the account-scoped browser gate on
    // every authenticated session; failures remain fail-closed client-side.
    void getPrivacyPreferences().catch(() => {});
  }, []);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    // Q3: bootstrap user session from stored JWT on mount.  setState in
    // effect is correct here — it's syncing React state with an async
    // auth check, not a derived value.
    if (isAuthenticated()) {
      getProfile()
        .then(installProfile)
        .catch((err: unknown) => {
          // H16: only clear the session when the server actually rejects
          // the token.  Transient errors (network drop, 5xx) must not log
          // the user out — previously any failure caused apiLogout() and
          // forced the user back to the landing page.
          const status = axios.isAxiosError(err) ? err.response?.status : undefined;
          if (status === 401 || status === 403) {
            apiLogout();
            setUser(null);
            navigate("/auth", { replace: true });
          }
          // For transient errors we leave the token in place; the next
          // authenticated request will retry via the axios client.
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, [installProfile, navigate]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const login = async (username: string, password: string) => {
    await apiLogin(username, password);
    const profile = await getProfile();
    installProfile(profile);
  };

  const register = async (username: string, password: string) => {
    await apiRegister(username, password);
    const profile = await getProfile();
    installProfile(profile);
  };

  const setupKeyLogin = async (key: string) => {
    await apiSetupKeyLogin(key);
    const profile = await getProfile();
    installProfile(profile);
  };

  const redeemInvitation = async (key: string, username: string, password: string) => {
    await apiRedeemInvitation(key, username, password);
    const profile = await getProfile();
    installProfile(profile);
  };

  const googleLogin = async (credential: string) => {
    await apiGoogleLogin(credential);
    const profile = await getProfile();
    installProfile(profile);
  };

  const logout = (options?: { state?: unknown }) => {
    apiLogout();
    setUser(null);
    navigate("/auth", { replace: true, state: options?.state });
  };

  return (
    <AuthContext.Provider value={{
      user,
      loading,
      login,
      register,
      setupKeyLogin,
      redeemInvitation,
      googleLogin,
      logout,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

// PART Y Q3: keep `useAuth` in the same file as the Provider — classic
// React Context pattern; splitting would require every consumer to update
// import path. Fast-refresh limitation for this hook is acceptable.
// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  return useContext(AuthContext);
}
