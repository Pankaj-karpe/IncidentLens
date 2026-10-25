"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, configureApi } from "./api";
import type { Role, User } from "./types";

const STORAGE_KEY = "incidentlens.token";
const MODIFY_ROLES: Role[] = ["developer", "devops", "sre"];
const REVIEW_ROLES: Role[] = ["devops", "sre"];

interface AuthValue {
  user: User | null;
  ready: boolean;
  canModify: boolean;
  canReview: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);
  const tokenRef = useRef<string | null>(null);

  const logout = useCallback(() => {
    tokenRef.current = null;
    sessionStorage.removeItem(STORAGE_KEY);
    setUser(null);
  }, []);

  useEffect(() => {
    configureApi({ getToken: () => tokenRef.current, onUnauthorized: logout });
    const saved = sessionStorage.getItem(STORAGE_KEY);
    if (!saved) {
      setReady(true);
      return;
    }
    tokenRef.current = saved;
    api
      .me()
      .then(setUser)
      .catch(logout)
      .finally(() => setReady(true));
  }, [logout]);

  const login = useCallback(async (username: string, password: string) => {
    const token = await api.login(username.trim(), password);
    tokenRef.current = token.access_token;
    sessionStorage.setItem(STORAGE_KEY, token.access_token);
    setUser(await api.me());
  }, []);

  const value = useMemo<AuthValue>(
    () => ({
      user,
      ready,
      login,
      logout,
      canModify: !!user && MODIFY_ROLES.includes(user.role),
      canReview: !!user && REVIEW_ROLES.includes(user.role),
    }),
    [user, ready, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}