import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import {
  getProfile,
  isAuthenticated,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
  setupKeyLogin as apiSetupKeyLogin,
  type UserProfile,
} from "../api/client";

interface AuthState {
  user: UserProfile | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  setupKeyLogin: (key: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState>({
  user: null,
  loading: true,
  login: async () => {},
  register: async () => {},
  setupKeyLogin: async () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (isAuthenticated()) {
      getProfile()
        .then(setUser)
        .catch(() => {
          apiLogout();
          setUser(null);
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = async (email: string, password: string) => {
    await apiLogin(email, password);
    const profile = await getProfile();
    setUser(profile);
  };

  const register = async (email: string, password: string) => {
    await apiRegister(email, password);
    const profile = await getProfile();
    setUser(profile);
  };

  const setupKeyLogin = async (key: string) => {
    await apiSetupKeyLogin(key);
    const profile = await getProfile();
    setUser(profile);
  };

  const logout = () => {
    apiLogout();
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, register, setupKeyLogin, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
