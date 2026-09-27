import { createContext, useContext, useEffect, useRef, useState } from "react";
import { EVENTS, useJoyride, type Step, type TooltipRenderProps } from "react-joyride";
import { CircleHelp, X } from "lucide-react";
import { useAuth } from "@/lib/auth";
import { getSessionState } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { getGuide } from "./guides";
import "./guide.css";

// 仅保存教程进度，不保存身份凭证或业务内容；本机不可写时退回当前页面会话。
const sessionProgress = new Map<string, string>();
function read(key: string) {
  try { return localStorage.getItem(key) || sessionProgress.get(key); }
  catch { return sessionProgress.get(key); }
}
function write(key: string, value: string) {
  sessionProgress.set(key, value);
  try { localStorage.setItem(key, value); return true; }
  catch { return false; }
}
const GuideActions = createContext({ skipAll: () => {}, temporary: false });

function GuideTooltip({ tooltipProps, step, index, size, backProps, primaryProps, skipProps, closeProps, isLastStep }: TooltipRenderProps) {
  const { skipAll, temporary } = useContext(GuideActions);
  return <section {...tooltipProps} role="dialog" aria-label="新手教程" aria-describedby="page-guide-description" className="page-guide-tooltip rounded-xl border bg-card p-4 text-foreground shadow-xl">
    <div className="mb-3 flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground" aria-live="polite">{index + 1} / {size}</span><button {...closeProps} type="button" className="rounded p-2 focus-visible:ring-2 focus-visible:ring-ring"><X aria-hidden className="size-4" /></button></div>
    <h2 className="text-base font-semibold">{step.title}</h2>
    <div id="page-guide-description" className="my-3 text-sm leading-6">{step.content}</div>
    <p className="text-xs text-muted-foreground">← → 切换步骤 · Esc 跳过本页</p>
    {temporary && <p role="status" className="mt-2 text-xs">浏览器未允许保存进度；刷新后可能再次显示。</p>}
    <div className="mt-4 flex flex-wrap items-center justify-end gap-2">
      <Button {...skipProps} variant="ghost" size="sm">跳过本页</Button>
      <Button {...backProps} variant="outline" size="sm" disabled={index === 0}>上一步</Button>
      <Button {...primaryProps} size="sm">{isLastStep ? "完成" : "下一步"}</Button>
    </div>
    <button type="button" onClick={skipAll} title="停止当前账号、当前角色的自动引导，仍可手动重播" className="mt-3 min-h-9 rounded px-2 text-xs text-muted-foreground underline underline-offset-4 focus-visible:ring-2 focus-visible:ring-ring">跳过全部引导</button>
  </section>;
}

export function PageGuide({ page, ready = true }: { page: string; ready?: boolean }) {
  const { user } = useAuth();
  if (!user || !getGuide(page, user.role)) return null;
  return <UserPageGuide key={JSON.stringify([user.user_id, user.role, page])} owner={user.user_id} role={user.role} page={page} ready={ready} />;
}

function UserPageGuide({ owner, role, page, ready }: { owner: string; role: string; page: string; ready: boolean }) {
  const guide = getGuide(page, role)!;
  const key = `onboarding_v1_${JSON.stringify([owner, role, page])}`;
  const allKey = `onboarding_all_${JSON.stringify([owner, role])}`;
  const button = useRef<HTMLButtonElement>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [running, setRunning] = useState(false);
  const [temporary, setTemporary] = useState(false);
  const [notice, setNotice] = useState("");
  const failed = useRef(false);
  const mounted = useRef(true);
  const current = () => mounted.current && getSessionState().user?.user_id === owner && getSessionState().user?.role === role;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const remember = (value: string, global = false) => {
    if (!current()) return;
    if (!write(global ? allKey : key, value)) setNotice("进度仅在当前页面会话保留，浏览器未允许本机存储。");
  };
  const finish = () => {
    setRunning(false);
    // 关闭后回到固定的重播入口，避免焦点落到业务执行按钮。
    requestAnimationFrame(() => { if (current()) button.current?.focus(); });
  };
  const { Tour, controls } = useJoyride({
    steps, run: running, continuous: true, scrollToFirstStep: true, tooltipComponent: GuideTooltip,
    // 高亮区域可能占满窗口，两轴避让才能防止气泡被挤出上下边界。
    floatingOptions: { shiftOptions: { crossAxis: true, boundary: [], rootBoundary: "viewport", padding: 12 } },
    locale: { back: "上一步", next: "下一步", last: "完成", skip: "跳过本页", close: "关闭引导", open: "打开引导" },
    options: {
      skipBeacon: true, overlayClickAction: false, blockTargetInteraction: true,
      closeButtonAction: "skip", dismissKeyAction: false, targetWaitTimeout: 1200,
      width: "min(360px, calc(100vw - 24px))", spotlightPadding: 6, spotlightRadius: 8,
      primaryColor: "hsl(var(--primary))", backgroundColor: "hsl(var(--card))", arrowColor: "hsl(var(--card))",
      textColor: "hsl(var(--foreground))", overlayColor: "rgba(0, 0, 0, .56)", zIndex: 180,
      scrollDuration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 220,
    },
    styles: { spotlight: { stroke: "hsl(var(--primary))", strokeWidth: 3 } },
    onEvent: event => {
      if (!current()) return;
      if (event.type === EVENTS.ERROR || event.type === EVENTS.TARGET_NOT_FOUND) failed.current = true;
      if (event.type === EVENTS.TOUR_END) {
        if (event.action === "skip") remember("skipped");
        else if (!failed.current) remember("completed");
        else setNotice("部分区域暂不可见，可以稍后重播查看。");
        finish();
      }
    },
  });
  const start = () => {
    if (!current() || document.querySelector('[role="dialog"], [role="alertdialog"]')) return false;
    if (!ready || document.querySelector('[data-guide-loading="true"]')) { setNotice("页面内容尚未就绪，请稍后重播。"); return false; }
    failed.current = false; setNotice("");
    const visible = guide.steps.flatMap(item => {
      const target = [...document.querySelectorAll<HTMLElement>(item.target)].find(element => element.checkVisibility({ checkVisibilityCSS: true }));
      return target ? [{ ...item, target }] : [];
    });
    if (!visible.length) { setNotice("页面正在准备，请稍后重试。"); return false; }
    try { localStorage.setItem(`${key}:check`, "1"); localStorage.removeItem(`${key}:check`); setTemporary(false); }
    catch { setTemporary(true); }
    controls.reset(); setSteps(visible); setRunning(true); return true;
  };
  useEffect(() => {
    if (!ready || read(key) || read(allKey)) return;
    // 等页面布局就绪；已有弹窗或用户正在输入时，不抢焦点。
    const tryStart = () => {
      if (read(key) || read(allKey) || document.querySelector('[role="dialog"], [role="alertdialog"], [data-guide-loading="true"]')) return;
      if (document.activeElement?.matches('input, textarea, [contenteditable="true"]')) return;
      if (start()) observer.disconnect();
    };
    const defer = () => { clearTimeout(timer); timer = window.setTimeout(tryStart, 400); };
    const observer = new MutationObserver(defer);
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-guide-loading"] });
    let timer = window.setTimeout(tryStart, 500);
    document.addEventListener("focusout", defer);
    return () => { clearTimeout(timer); observer.disconnect(); document.removeEventListener("focusout", defer); };
  }, [ready, key, allKey]);
  useEffect(() => {
    if (!running) return;
    // 后台状态变化可能打开确认窗；教程让位，不与业务弹窗争夺焦点。
    const yieldToDialog = () => {
      const other = [...document.querySelectorAll<HTMLElement>('[role="dialog"], [role="alertdialog"]')]
        .some(element => !element.classList.contains("page-guide-tooltip") && element.checkVisibility({ checkVisibilityCSS: true }));
      if (other) { controls.stop(); setRunning(false); }
    };
    const observer = new MutationObserver(yieldToDialog);
    observer.observe(document.body, { childList: true, subtree: true });
    const keyboard = (event: KeyboardEvent) => {
      if (!current() || !["Escape", "ArrowLeft", "ArrowRight"].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey) return;
      event.preventDefault(); event.stopImmediatePropagation();
      if (event.key === "Escape") controls.skip();
      else if (event.key === "ArrowLeft") controls.prev();
      else controls.next();
    };
    document.addEventListener("keydown", keyboard, true);
    return () => { observer.disconnect(); document.removeEventListener("keydown", keyboard, true); };
  }, [running, controls]);
  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if ((event.key === key || event.key === allKey) && event.newValue) { controls.stop(); finish(); }
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, [key, allKey, controls]);
  return <>
    <Button ref={button} type="button" variant="outline" size="sm" onClick={start} aria-label="新手教程" title={`重播${guide.title}引导`} className="shrink-0 gap-1.5"><CircleHelp aria-hidden className="size-4" />新手教程</Button>
    {notice && <span role="status" className="max-w-64 text-xs text-muted-foreground">{notice}</span>}
    <GuideActions.Provider value={{ temporary, skipAll: () => { remember("skipped", true); controls.skip(); } }}>{Tour}</GuideActions.Provider>
  </>;
}
