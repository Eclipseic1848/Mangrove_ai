import { lazy } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth, isAdminish } from "@/lib/auth";
import { Layout } from "@/components/Layout";
import { Login } from "@/pages/Login";

const Dashboard = lazy(() => import("@/pages/Dashboard").then(module => ({ default: module.Dashboard })));
const Chat = lazy(() => import("@/pages/Chat").then(module => ({ default: module.Chat })));
const DataPrepPage = lazy(() => import("@/pages/DataPrepPage").then(module => ({ default: module.DataPrepPage })));
const Tasks = lazy(() => import("@/pages/Tasks").then(module => ({ default: module.Tasks })));
const Templates = lazy(() => import("@/pages/Templates").then(module => ({ default: module.Templates })));
const Memory = lazy(() => import("@/pages/Memory").then(module => ({ default: module.Memory })));
const Settings = lazy(() => import("@/pages/Settings").then(module => ({ default: module.Settings })));
const Admin = lazy(() => import("@/pages/Admin").then(module => ({ default: module.Admin })));
const Feedback = lazy(() => import("@/pages/Feedback").then(module => ({ default: module.Feedback })));
const Operations = lazy(() => import("@/pages/Operations").then(module => ({ default: module.Operations })));

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

// 仅管理员/超级管理员可进；其余重定向到概览
function AdminOnly({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  if (!isAdminish(user?.role)) return <Navigate to="/" replace />;
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
        <Route path="/operations" element={<Operations />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
