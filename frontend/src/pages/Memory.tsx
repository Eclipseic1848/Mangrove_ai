/// <reference types="vite/client" />
import { PageGuide } from "@/components/onboarding/PageGuide";
import { useCallback, useEffect, useRef, useState } from "react";
import { Brain, Plus, RefreshCw, Users } from "lucide-react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/components/Markdown";
import { PersonalMemoryPanel } from "@/components/memory/PersonalMemoryPanel";
import { MemoryConfirm, MemoryTextarea } from "@/components/memory/MemoryControls";
import { api } from "@/lib/api";
import { useAuth, isAdminish } from "@/lib/auth";

// 必须早于 BrowserRouter 注册：其同步路由更新会卸载页面并移除页面内的监听。
// 只有记忆页挂载时设置处理器，其他页面的导航不受影响。
let memoryPop: ((event: PopStateEvent) => void) | null = null;
const dispatchMemoryPop = (event: PopStateEvent) => memoryPop?.(event);
window.addEventListener("popstate", dispatchMemoryPop, true);
if (import.meta.hot) import.meta.hot.dispose(() => window.removeEventListener("popstate", dispatchMemoryPop, true));

export function Memory() {
  const { user } = useAuth();
  const location = useLocation();
  // 确认跳到另一条记忆页历史时也重建草稿；被拦截的导航不会改变路由 key。
  return user ? <MemoryContent key={`${user.user_id}:${location.key}`} /> : null;
}

function MemoryContent() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const isAdmin = isAdminish(user?.role);
  const [tab, setTab] = useState<"personal" | "global">("personal");
  const [refresh, setRefresh] = useState(0);
  const [personalState, setPersonalState] = useState({ dirty: false, busy: false });
  const [pref, setPref] = useState("");
  const [digest, setDigest] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [text, setText] = useState("");
  const [edit, setEdit] = useState<{ original: string; text: string; digest: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [confirm, setConfirm] = useState<"save" | "discard" | null>(null);
  const [destination, setDestination] = useState<string | number | null>(null);
  const [restoringHistory, setRestoringHistory] = useState(false);
  const historyGuard = useRef({ blocked: false, index: window.history.state?.idx as number | undefined, restoring: false, allowed: false });
  const sequence = useRef(0);
  const flight = useRef(false);
  const globalEditor = useRef<HTMLDivElement>(null);
  const dirty = personalState.dirty || Boolean(text.trim() || edit && edit.text !== edit.original);
  const inFlight = busy || personalState.busy;
  historyGuard.current.blocked = dirty || inFlight;
  useEffect(() => {
    const pop = (event: PopStateEvent) => {
      const guard = historyGuard.current;
      const next = event.state?.idx;
      if (guard.allowed) { guard.allowed = false; guard.index = next; return; }
      if (!guard.blocked && !guard.restoring) { guard.index = next; return; }
      // BrowserRouter 的同文档历史使用 idx。先还原历史位置，阻止路由卸载草稿，确认后再重放。
      // 跨文档离开由 beforeunload 保护，不创建额外历史条目或持久保存个人正文。
      if (!Number.isInteger(next) || !Number.isInteger(guard.index)) return;
      event.stopImmediatePropagation();
      if (next === guard.index) { guard.restoring = false; setRestoringHistory(false); return; }
      const delta = next - guard.index!;
      if (!guard.restoring) setDestination(delta);
      guard.restoring = true; setRestoringHistory(true);
      window.history.go(-delta);
    };
    memoryPop = pop;
    return () => { if (memoryPop === pop) memoryPop = null; };
  }, []);
  const load = useCallback(async () => {
    const current = ++sequence.current;
    setLoading(true); setLoadError(false);
    try {
      const result = await api.get("/api/memory?page=1&page_size=10", { signal: AbortSignal.timeout(15000) });
      if (current !== sequence.current) return;
      setPref(result.preferences ?? ""); setDigest(result.preferences_digest ?? ""); setLoaded(true);
    } catch { if (current === sequence.current) setLoadError(true); }
    finally { if (current === sequence.current) setLoading(false); }
  }, []);
  useEffect(() => { void load(); return () => { sequence.current++; }; }, [load, refresh]);
  useEffect(() => { if (edit) globalEditor.current?.querySelector("textarea")?.focus(); }, [Boolean(edit)]);
  useEffect(() => {
    if (!dirty && !inFlight) return;
    // 同窗口导航先核对草稿；私人正文不写入浏览器持久存储。
    const click = (event: MouseEvent) => {
      const link = (event.target as Element)?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!link || link.target === "_blank" || event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
      const url = new URL(link.href);
      if (url.origin !== location.origin || url.pathname === location.pathname && url.search === location.search) return;
      event.preventDefault(); event.stopPropagation(); setDestination(url.pathname + url.search + url.hash);
    };
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    document.addEventListener("click", click, true); window.addEventListener("beforeunload", unload);
    return () => { document.removeEventListener("click", click, true); window.removeEventListener("beforeunload", unload); };
  }, [dirty, inFlight]);
  const save = async (replace: boolean) => {
    if (flight.current || !isAdmin || replace && !edit || !replace && !text.trim()) return;
    flight.current = true; setBusy(true); setError(""); setNotice("");
    try {
      if (replace) await api.patch("/api/memory", { text: edit!.text, expected_digest: edit!.digest });
      else await api.post("/api/memory", { text: text.trim() });
      setEdit(null); if (!replace) setText(""); setConfirm(null);
      setNotice("已保存全局记忆，所有用户的后续相关任务会参考这些偏好。");
      await load();
    } catch (reason) { setConfirm(null); setError(reason instanceof Error ? reason.message : "保存未完成，输入已保留，请核对后重试。"); }
    finally { flight.current = false; setBusy(false); }
  };
  return <>
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-7 py-4">
      <div><h1 className="text-lg font-semibold tracking-tight">记忆</h1><p className="text-sm text-muted-foreground">记住常用偏好，减少重复说明</p></div>
      <div className="flex flex-wrap gap-2"><PageGuide page={`memory.${tab}`} ready={!loading} /><Button variant="outline" size="sm" disabled={inFlight || loading} onClick={() => setRefresh(value => value + 1)}><RefreshCw className="size-4" />刷新</Button></div>
    </header>
    <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-7 sm:py-6">
      <div className="mx-auto max-w-4xl space-y-4">
        <div role="tablist" aria-label="记忆范围" className="flex gap-1 border-b">
          {(["personal", "global"] as const).map(value => <button key={value} id={`memory-${value}-tab`} role="tab" type="button" aria-selected={tab === value} aria-controls={`memory-${value}-panel`} tabIndex={tab === value ? 0 : -1}
            className={`inline-flex items-center gap-2 border-b-2 px-4 py-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${tab === value ? "border-primary text-foreground" : "border-transparent text-muted-foreground hover:bg-muted"}`}
            onClick={() => setTab(value)} onKeyDown={event => {
              if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
                event.preventDefault(); const next = event.key === "Home" ? "personal" : event.key === "End" ? "global" : tab === "personal" ? "global" : "personal";
                setTab(next); document.getElementById(`memory-${next}-tab`)?.focus();
              }
            }}>{value === "personal" ? <Brain className="size-4" /> : <Users className="size-4" />}{value === "personal" ? "我的记忆" : "全局记忆"}</button>)}
        </div>
        <details className="rounded-lg border bg-muted/20 px-4 py-3 text-sm">
          <summary className="cursor-pointer font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">记忆如何使用？</summary>
          <div className="mt-3 space-y-2 leading-6 text-muted-foreground"><p>新任务会参考相关记忆，不会应用全部记忆；本次明确提出的要求优先。</p><p>你也可以在工作台独立发送“记住：完整偏好”或“忘记：完整记忆”。删除和修改只影响后续任务，不改写已创建的任务。</p><p>请勿在记忆中保存密码、密钥等敏感信息。</p></div>
        </details>
        <div id="memory-personal-panel" role="tabpanel" aria-labelledby="memory-personal-tab" hidden={tab !== "personal"}>
          <div className="rounded-xl border bg-card p-4 sm:p-5"><h2 className="text-base font-semibold">我的记忆</h2><p className="mb-5 mt-1 text-sm text-muted-foreground">仅用于你发起的后续任务；本次明确提出的要求优先。</p><PersonalMemoryPanel refreshKey={refresh} onStateChange={setPersonalState} /></div>
        </div>
        <div id="memory-global-panel" role="tabpanel" aria-labelledby="memory-global-tab" hidden={tab !== "global"}>
          <section aria-labelledby="global-memory-title" className="rounded-xl border bg-card p-4 sm:p-5">
            <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex flex-wrap items-center gap-2"><h2 id="global-memory-title" className="text-base font-semibold">全局记忆</h2><span className="rounded-md bg-muted px-2 py-1 text-xs text-foreground">所有用户共享</span></div>
              {isAdmin && !edit && <Button variant="outline" size="sm" disabled={loading || loadError || busy || !digest} onClick={() => { setEdit({ original: pref, text: pref, digest }); setError(""); }}>编辑全局记忆</Button>}
            </div>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">管理员维护的共同偏好，供所有用户的后续任务参考。本次明确提出的要求优先。</p>
            <div className="min-h-7 pt-2 text-xs text-muted-foreground" role="status">{loading ? "正在更新全局记忆…" : ""}</div>
            {loadError && <div role="alert" className="mb-3 flex flex-wrap items-center gap-2 text-sm text-destructive"><span>{loaded ? "更新失败，当前显示上次读取的全局记忆。" : "全局记忆加载失败，请重试。"}</span><Button variant="outline" size="sm" onClick={() => void load()}>重试</Button></div>}
            {notice && <p role="status" className="mb-4 rounded-md border bg-muted/40 p-3 text-sm">{notice}</p>}
            {error && <p role="alert" className="mb-3 text-sm text-destructive">{error}</p>}
            {isAdmin && !edit && <div className="mb-5 space-y-2 rounded-lg bg-muted/30 p-3"><label htmlFor="global-memory-input" className="block text-sm font-medium">添加全局记忆</label><MemoryTextarea id="global-memory-input" value={text} maxLength={4000} disabled={busy || !loaded || loadError} onChange={event => setText(event.target.value)} placeholder="例如：所有报告都标明数据来源" /><div className="flex justify-end"><Button disabled={busy || !loaded || loadError || !text.trim()} onClick={() => void save(false)}><Plus />添加</Button></div></div>}
            {isAdmin && edit ? <div ref={globalEditor} className="space-y-3"><label htmlFor="global-memory-editor" className="block text-sm font-medium">全局记忆内容</label><MemoryTextarea id="global-memory-editor" value={edit.text} maxLength={40000} disabled={busy} onChange={event => setEdit({ ...edit, text: event.target.value })} /><p className="text-xs text-muted-foreground">可使用普通文字或 Markdown。保存后影响所有用户的后续任务，已有任务不变。</p>
              <details className="rounded-md border p-3"><summary className="cursor-pointer text-sm">预览修改后的内容</summary><div className="mt-3"><Markdown safeResources>{edit.text || "暂无内容"}</Markdown></div></details>
              <div className="flex flex-wrap gap-2"><Button disabled={busy || edit.text.length > 40000 || edit.text === edit.original} onClick={() => setConfirm("save")}>保存全局记忆</Button><Button variant="outline" disabled={busy} onClick={() => { if (edit.text !== edit.original) setConfirm("discard"); else setEdit(null); }}>取消编辑</Button></div>
            </div> : loaded && (pref ? <Markdown safeResources>{pref}</Markdown> : <p className="py-8 text-center text-sm text-muted-foreground">{isAdmin ? "添加一条大家共用的偏好，减少重复说明。" : "还没有全局记忆，管理员添加后可在这里查看。"}</p>)}
          </section>
        </div>
      </div>
    </div>
    <MemoryConfirm open={confirm !== null} title={confirm === "save" ? "保存全局记忆？" : "放弃未保存的修改？"} description={confirm === "save" ? `${edit?.text.trim() ? "将更新所有用户共同参考的偏好。" : "将清空全局记忆。"}只影响后续任务，已创建的任务不变。` : "取消可继续编辑，放弃后保留原来的全局记忆。"} action={confirm === "save" ? "确认保存" : "放弃修改"} danger={confirm === "save" && !edit?.text.trim()} busy={busy} onCancel={() => setConfirm(null)} onConfirm={() => { if (confirm === "save") void save(true); else { setEdit(null); setConfirm(null); } }} />
    <MemoryConfirm open={destination !== null} title={inFlight ? "操作尚未完成" : "离开未保存的记忆？"} description={inFlight ? "请等待保存结果再离开，避免重复操作。" : "当前输入或修改尚未保存，离开后将丢失。取消可继续编辑。"} action="放弃并离开" busy={inFlight || restoringHistory} onCancel={() => setDestination(null)} onConfirm={() => {
      if (destination === null || inFlight || restoringHistory) return;
      setDestination(null);
      if (typeof destination === "number") { historyGuard.current.allowed = true; window.history.go(destination); }
      else navigate(destination);
    }} />
  </>;
}
