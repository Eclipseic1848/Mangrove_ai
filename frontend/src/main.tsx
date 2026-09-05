import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { Toaster } from "sonner";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { AuthProvider, useAuth } from "@/lib/auth";
import { ThemeProvider } from "@/lib/theme";
import "./index.css";

function QueryScope({ children }: { children: React.ReactNode }) {
  const [queryClient] = React.useState(() => new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        retry: 1,
      },
    },
  }));
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

function OwnerQueryScope({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  // 换账号同时重建缓存与页面状态，旧请求只能回到旧账号的客户端。
  return <QueryScope key={user ? `owner:${user.user_id}` : "anonymous"}>{children}</QueryScope>;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <AuthProvider>
        <BrowserRouter>
          <OwnerQueryScope>
            <App />
            <Toaster position="top-center" richColors closeButton theme="system" />
          </OwnerQueryScope>
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
  </React.StrictMode>,
);
