import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, clearToken, getToken, setToken } from "./api";

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
  logout: () => void;
}

const AuthCtx = createContext<AuthState>(null as unknown as AuthState);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      if (getToken()) {
        try {
          const me = await api.get("/api/auth/me");
          setToken(me.access_token);
          setUser({ user_id: me.user_id, username: me.username, display_name: me.display_name, role: me.role });
        } catch {
          clearToken();
        }
      }
      setLoading(false);
    })();
  }, []);

  const apply = (r: any) => {
    setToken(r.access_token);
    setUser({ user_id: r.user_id, username: r.username, display_name: r.display_name, role: r.role });
  };

  return (
    <AuthCtx.Provider
      value={{
        user,
        loading,
        login: async (username, password) => apply(await api.post("/api/auth/login", { username, password })),
        register: async (username, password, display_name) => {
          const r = await api.post("/api/auth/register", { username, password, display_name });
          if (r.access_token) apply(r); // 兼容已签发令牌的响应；待审批注册保持未登录。
          return r;
        },
        logout: () => {
          clearToken();
          setUser(null);
        },
      }}
    >
      {children}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => useContext(AuthCtx);
