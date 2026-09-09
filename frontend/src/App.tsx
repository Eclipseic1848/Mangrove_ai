import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth, isAdminish } from "@/lib/auth";
import { Layout } from "@/components/Layout";
import { Login } from "@/pages/Login";
import { Dashboard } from "@/pages/Dashboard";
import { Chat } from "@/pages/Chat";
import { DataPrepPage } from "@/pages/DataPrepPage";
import { Tasks } from "@/pages/Tasks";
import { Templates } from "@/pages/Templates";
import { Memory } from "@/pages/Memory";
import { Settings } from "@/pages/Settings";
import { Admin } from "@/pages/Admin";
import { Feedback } from "@/pages/Feedback";

function Protected({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading)
    return (
      <div className="flex h-screen w-screen items-center justify-center text-sm text-muted-foreground">
        加载中…
      </div>
    );
  if (!user) return <Navigate to={`/login?returnTo=${encodeURIComponent(location.pathname + location.search + location.hash)}`} replace />;
  return <>{children}</>;
}

function RouteNotice({ forbidden = false }: { forbidden?: boolean }) {
  return <section className="mx-auto w-full max-w-2xl space-y-4 p-6">
    <p className="text-sm text-muted-foreground">{forbidden ? "403 · 权限不足" : "404 · 页面不存在"}</p>
    <h1 className="text-xl font-semibold">{forbidden ? "无权访问此页面" : "页面不存在"}</h1>
    <p className="text-sm text-muted-foreground">{forbidden ? "你的当前角色不能访问此页面，管理内容未显示。" : "请检查页面地址，或返回任务工作台继续。"}</p>
    <Link to="/data-prep" className="inline-block rounded-md px-2 py-1 text-primary underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">返回任务工作台</Link>
  </section>;
}

// 仅管理员/超级管理员可进；拒绝页不挂载管理组件或请求业务正文。
function AdminOnly({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  if (!isAdminish(user?.role)) return <RouteNotice forbidden />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        element={
          <Protected>
            <Layout />
          </Protected>
        }
      >
        <Route path="/" element={<Dashboard />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/data-prep" element={<DataPrepPage />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/templates" element={<Templates />} />
        <Route path="/memory" element={<Memory />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/admin" element={<AdminOnly><Admin /></AdminOnly>} />
        <Route path="/feedback" element={<AdminOnly><Feedback /></AdminOnly>} />
        <Route path="*" element={<RouteNotice />} />
      </Route>
    </Routes>
  );
}
