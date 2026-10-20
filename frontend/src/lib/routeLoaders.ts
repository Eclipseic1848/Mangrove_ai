/// <reference types="vite/client" />
// 懒加载页晚于 BrowserRouter 执行；提前注册，避免路由先卸载记忆草稿。
// 处理器仅在记忆页挂载时激活，不读取业务数据或阻拦其他页面。
let memoryPop: ((event: PopStateEvent) => void) | null = null;
const dispatchMemoryPop = (event: PopStateEvent) => memoryPop?.(event);
window.addEventListener("popstate", dispatchMemoryPop, true);
if (import.meta.hot) import.meta.hot.dispose(() => window.removeEventListener("popstate", dispatchMemoryPop, true));

export function guardMemoryHistory(handler: (event: PopStateEvent) => void) {
  memoryPop = handler;
  return () => { if (memoryPop === handler) memoryPop = null; };
}

export const routeLoaders = {
  '/': () => import('@/pages/Dashboard').then(module => ({ default: module.Dashboard })),
  '/chat': () => import('@/pages/Chat').then(module => ({ default: module.Chat })),
  '/data-prep': () => import('@/pages/DataPrepPage').then(module => ({ default: module.DataPrepPage })),
  '/tasks': () => import('@/pages/Tasks').then(module => ({ default: module.Tasks })),
  '/templates': () => import('@/pages/Templates').then(module => ({ default: module.Templates })),
  '/memory': () => import('@/pages/Memory').then(module => ({ default: module.Memory })),
  '/settings': () => import('@/pages/Settings').then(module => ({ default: module.Settings })),
  '/admin': () => import('@/pages/Admin').then(module => ({ default: module.Admin })),
  '/feedback': () => import('@/pages/Feedback').then(module => ({ default: module.Feedback })),
  '/operations': () => import('@/pages/Operations').then(module => ({ default: module.Operations })),
};

export function preloadRoute(target: string) {
  const path = target.split(/[?#]/)[0];
  const loader = routeLoaders[path as keyof typeof routeLoaders];
  // 这里只预载代码；真正进入页面时仍由原加载边界报告失败，不触发业务读取。
  void loader?.().catch(() => {});
  // 默认工作台有第二层懒加载，并行预载才能避免进入后再串行等待。
  if (path === "/data-prep" && new URL(target, window.location.origin).searchParams.get("legacy") !== "1")
    void import("@/pages/SemanticWorkspacePage").catch(() => {});
}
