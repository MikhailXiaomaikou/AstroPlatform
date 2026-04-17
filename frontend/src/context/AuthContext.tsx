import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import axios from "axios";
import {
  getProfile,
  isAuthenticated,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
  setupKeyLogin as apiSetupKeyLogin,
  googleLogin as apiGoogleLogin,
  type UserProfile,
} from "../api/client";

interface AuthState {
  user: UserProfile | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  setupKeyLogin: (key: string) => Promise<void>;
  googleLogin: (credential: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  login: async () => {},
  register: async () => {},
  setupKeyLogin: async () => {},
  googleLogin: async () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (isAuthenticated()) {
      getProfile()
        .then(setUser)
        .catch((err: unknown) => {
          // H16: only clear the session when the server actually rejects
          // the token.  Transient errors (network drop, 5xx) must not log
          // the user out — previously any failure caused apiLogout() and
          // forced the user back to the landing page.
          const status = axios.isAxiosError(err) ? err.response?.status : undefined;
          if (status === 401 || status === 403) {
            apiLogout();
            setUser(null);
          }
          // For transient errors we leave the token in place; the next
          // authenticated request will retry via the axios client.
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = async (username: string, password: string) => {
    await apiLogin(username, password);
    const profile = await getProfile();
    setUser(profile);
  };

  const register = async (username: string, password: string) => {
    await apiRegister(username, password);
    const profile = await getProfile();
    setUser(profile);
  };

  const setupKeyLogin = async (key: string) => {
    await apiSetupKeyLogin(key);
    const profile = await getProfile();
    setUser(profile);
  };

  const googleLogin = async (credential: string) => {
    await apiGoogleLogin(credential);
    const profile = await getProfile();
    setUser(profile);
  };

  const logout = () => {
    apiLogout();
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, setupKeyLogin, googleLogin, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
