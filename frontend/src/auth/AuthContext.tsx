import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { get, getToken, post, setToken } from "../api/client";
import type { Me, Organization } from "../api/types";

interface AuthState {
  me: Me | null;
  org: Organization | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, organizationName: string) => Promise<void>;
  logout: () => void;
  selectOrg: (orgId: number) => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [orgId, setOrgId] = useState<number | null>(null);
  const [loading, setLoading] = useState(Boolean(getToken()));

  const loadMe = useCallback(async () => {
    const data = await get<Me>("/auth/me");
    setMe(data);
    setOrgId((current) => current ?? data.organizations[0]?.id ?? null);
  }, []);

  useEffect(() => {
    if (!getToken()) return;
    loadMe()
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
  }, [loadMe]);

  const login = async (email: string, password: string) => {
    const { access_token } = await post<{ access_token: string }>("/auth/login", {
      email,
      password,
    });
    setToken(access_token);
    await loadMe();
  };

  const signup = async (email: string, password: string, organizationName: string) => {
    await post("/auth/signup", { email, password, organization_name: organizationName });
    await login(email, password);
  };

  const logout = () => {
    setToken(null);
    setMe(null);
    setOrgId(null);
  };

  const org = me?.organizations.find((o) => o.id === orgId) ?? null;
  return (
    <AuthContext.Provider
      value={{ me, org, loading, login, signup, logout, selectOrg: setOrgId }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}

/** The current organization's id; pages only render once one is selected. */
export function useOrgId(): number {
  const { org } = useAuth();
  if (!org) throw new Error("No organization selected");
  return org.id;
}
