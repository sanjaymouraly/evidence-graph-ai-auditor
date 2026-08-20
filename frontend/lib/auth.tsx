"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api, cachedUser, clearSession, getToken, setSession } from "./api";
import type { SessionUser, Workspace } from "./types";

type AuthState = {
  ready: boolean;
  user: SessionUser | null;
  workspace: Workspace | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

type MeResponse = { user: SessionUser; workspace: Workspace; permissions?: string[] };

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<SessionUser | null>(null);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const router = useRouter();
  const pathname = usePathname();

  const load = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setWorkspace(null);
      setReady(true);
      return;
    }
    try {
      const me = await api<MeResponse>("/api/auth/me");
      setUser(me.user);
      setWorkspace(me.workspace);
      window.localStorage.setItem("ega.user", JSON.stringify(me.user));
    } catch {
      setUser(null);
      setWorkspace(null);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    const cached = cachedUser<SessionUser>();
    if (cached) setUser(cached);
    void load();
  }, [load]);

  useEffect(() => {
    if (!ready) return;
    const isLogin = pathname?.startsWith("/login");
    if (!user && !isLogin) router.replace("/login/");
    if (user && isLogin) router.replace("/");
  }, [ready, user, pathname, router]);

  const login = useCallback(async (email: string, password: string) => {
    const response = await api<{ access_token: string; user: SessionUser; workspace: Workspace }>(
      "/api/auth/login",
      { method: "POST", body: { email, password } },
    );
    setSession(response.access_token, response.user);
    setUser(response.user);
    setWorkspace(response.workspace);
    setReady(true);
  }, []);

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
    setWorkspace(null);
    router.replace("/login/");
  }, [router]);

  const refresh = useCallback(async () => {
    await load();
  }, [load]);

  const value = useMemo(
    () => ({ ready, user, workspace, login, logout, refresh }),
    [ready, user, workspace, login, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

export { can } from "./permissions";
