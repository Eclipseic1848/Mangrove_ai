import { useEffect, useRef } from "react";
import { useLocation } from "react-router-dom";
import { api } from "@/lib/api";
import { isAdminish, useAuth } from "@/lib/auth";

const pages = new Set(["/", "/data-prep", "/chat", "/tasks", "/templates", "/memory", "/settings", "/feedback", "/admin", "/operations"]);

/** 仅采集路径分类与前台心跳，不发送查询串、标题、正文或原始来源地址。 */
export function OperationsActivity() {
  const { pathname } = useLocation();
  const { user } = useAuth();
  const previousPath = useRef("");
  useEffect(() => {
    if (!user || !pages.has(pathname) || (!isAdminish(user.role) && ["/operations", "/admin", "/feedback"].includes(pathname))) return;
    const controller = new AbortController();
    const eventId = crypto.randomUUID();
    let visited = false, inFlight = false;
    const source = previousPath.current ? "internal" : document.referrer ? (new URL(document.referrer).origin === location.origin ? "internal" : "external") : "direct";
    const tick = async () => {
      if (document.visibilityState !== "visible" || inFlight || controller.signal.aborted) return;
      inFlight = true;
      const attempt = new AbortController();
      const stop = () => attempt.abort();
      controller.signal.addEventListener("abort", stop, { once: true });
      const timeout = setTimeout(stop, 20000);
      try {
        if (!visited) {
          await api.post("/api/operations/visits", { event_id: eventId, page: pathname, source }, {}, attempt.signal);
          visited = true; previousPath.current = pathname;
        }
        await api.post("/api/operations/heartbeat", {}, {}, attempt.signal);
      } catch { /* 采集失败不阻断用户操作；下次前台心跳用同一事件编号重试。 */ }
      finally { clearTimeout(timeout); controller.signal.removeEventListener("abort", stop); inFlight = false; }
    };
    const initial = setTimeout(tick, 0);
    const timer = setInterval(tick, 30000);
    document.addEventListener("visibilitychange", tick);
    return () => { clearTimeout(initial); clearInterval(timer); controller.abort(); document.removeEventListener("visibilitychange", tick); };
  }, [pathname, user?.user_id, user?.role]);
  return null;
}
