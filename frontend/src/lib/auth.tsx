import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { bootstrapSession, getSessionState, sessionCommand, subscribeSession } from "./api";

export interface User {
  user_id: string;
  username: string;
  display_name: string;
  role: string; // super_admin | admin | user
}

// RBAC 角色分级（与后端 ROLE_LEVEL 一致）：数值越大权限越高
export const ROLE_LEVEL: Record<string, number> = { super_admin: 3, admin: 2, user: 1 };
export const roleLevel = (r?: string) => ROLE_LEVEL[r ?? "user"] ?? 1;
export const isAdminish = (r?: string) => roleLevel(r) >= ROLE_LEVEL.admin;
export const roleLabel = (r?: string) =>
  r === "super_admin" ? "超级管理员" : r === "admin" ? "管理员" : "普通用户";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (
    username: string,
    password: string,
    displayName?: string,
  ) => Promise<{ pending?: boolean; message?: string }>;
  message: string | null;
  logout: () => Promise<void>;
  logoutAll: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
}

const AuthCtx = createContext<AuthState>(null as unknown as AuthState);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState(getSessionState);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    const unsubscribe = subscribeSession(() => { if (active) setSession(getSessionState()); });
    void bootstrapSession().catch((error) => {
      if (active) setSession((current) => ({ ...current, message: String(error.message || error) }));
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; unsubscribe(); };
  }, []);
  return (
    <AuthCtx.Provider value={{
      ...session, loading,
      login: async (username, password) => {
        await sessionCommand("/api/auth/login", { username, password });
        setLoading(false);
      },
      register: async (username, password, display_name) => {
        return sessionCommand("/api/auth/register", { username, password, display_name });
      },
      logout: async () => { await sessionCommand("/api/auth/logout"); },
      logoutAll: async () => { await sessionCommand("/api/auth/logout-all"); },
      changePassword: async (current_password, new_password) => {
        await sessionCommand("/api/auth/password", { current_password, new_password });
      },
    }}>
      {children}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => useContext(AuthCtx);
