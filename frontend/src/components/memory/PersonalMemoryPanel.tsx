import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Plus, Search, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Pagination } from "@/components/ui/pagination";
import { api } from "@/lib/api";
import { beijingTime } from "@/lib/beijingTime";
import { formatRelativeTime } from "@/lib/utils";
import { MemoryConfirm, MemoryTextarea } from "./MemoryControls";

type Memory = { id: number; text: string; created_at?: string };
export function PersonalMemoryPanel({ refreshKey = 0, onChanged, onStateChange }: {
  refreshKey?: number; onChanged?: () => void; onStateChange?: (state: { dirty: boolean; busy: boolean }) => void;
}) {
  const id = useId();
  const [rows, setRows] = useState<Memory[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [composing, setComposing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [text, setText] = useState("");
  const [editing, setEditing] = useState<Memory | null>(null);
  const [editText, setEditText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saved, setSaved] = useState<Memory | null>(null);
  const [deleting, setDeleting] = useState<Memory | null>(null);
  const [discard, setDiscard] = useState<(() => void) | null>(null);
  const sequence = useRef(0);
  const flight = useRef(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const editor = useRef<HTMLDivElement>(null);
  const dirty = Boolean(text.trim() || (editing && editText !== editing.text));
  useEffect(() => { onStateChange?.({ dirty, busy }); }, [dirty, busy, onStateChange]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  useEffect(() => {
    if (composing || search === query) return;
    const timer = window.setTimeout(() => { setQuery(search); setPage(1); }, search ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [search, query, composing]);
  const load = useCallback(async () => {
    const current = ++sequence.current;
    setLoading(true);
    setLoadError(false);
    try {
      const data = await api.get(`/api/memory?${new URLSearchParams({ page: String(page), page_size: String(pageSize), q: query })}`, { signal: AbortSignal.timeout(15000) });
      if (current !== sequence.current) return;
      setRows(data.personal ?? []); setTotal(data.total ?? data.personal?.length ?? 0);
      if (data.page) setPage(data.page);
      setLoaded(true);
    } catch { if (current === sequence.current) setLoadError(true); }
    finally { if (current === sequence.current) setLoading(false); }
  }, [page, pageSize, query]);
  const latestLoad = useRef(load);
  latestLoad.current = load;
  useEffect(() => { void load(); return () => { sequence.current++; }; }, [load, refreshKey]);
  useEffect(() => { editor.current?.querySelector("textarea")?.focus(); }, [editing?.id]);
  const changeEditor = (next: Memory | null) => {
    const apply = () => { setEditing(next); setEditText(next?.text ?? ""); setError(""); };
    if (editing && editText !== editing.text) setDiscard(() => apply);
    else apply();
  };
  const mutate = async (kind: "add" | "edit" | "delete") => {
    if (flight.current) return;
    const value = (kind === "add" ? text : editText).trim();
    if (kind !== "delete" && (!value || value.length > 4000)) return;
    if (kind === "edit" && !editing || kind === "delete" && !deleting) return;
    flight.current = true; setBusy(true); setError(""); setNotice("");
    try {
      if (kind === "delete") {
        await api.del(`/api/memory/self/${deleting!.id}`);
        if (editing?.id === deleting!.id) setEditing(null);
        if (saved?.id === deleting!.id) setSaved(null);
        setDeleting(null); setNotice("已删除，后续任务将不再参考这条记忆。");
      } else {
        const result = kind === "add"
          ? await api.post("/api/memory/self", { text: value })
          : await api.patch(`/api/memory/self/${editing!.id}`, { text: value, expected_text: editing!.text });
        setSaved(result.item ?? (kind === "edit" ? { ...editing!, text: value } : { id: -1, text: value }));
        if (kind === "add") setText(""); else setEditing(null);
        setNotice("已保存，后续相关任务会参考这条记忆。");
      }
      onChanged?.();
      // 保存期间允许继续搜索，回读必须使用此刻的筛选，不得复活提交时的旧页。
      await latestLoad.current();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "操作未完成，请核对后重试，输入已保留。"); }
    finally { flight.current = false; setBusy(false); }
  };
  const pinned = editing && !rows.some(row => row.id === editing.id);
  const visible = pinned ? [editing!, ...rows] : rows;
  return <section aria-label="个人记忆管理" className="space-y-4">
    <div className="space-y-2">
      <label htmlFor={`${id}-add`} className="block text-sm font-medium">添加个人记忆</label>
      <MemoryTextarea id={`${id}-add`} value={text} maxLength={4000} disabled={busy} placeholder="例如：报告先写结论，再列关键数据" onChange={event => setText(event.target.value)} />
      <div className="flex items-center justify-between gap-3"><span className="text-xs text-muted-foreground">{text.length > 3600 ? `${text.length} / 4000 字` : "一次记录一条偏好，便于后续查找和修改。"}</span><Button disabled={busy || !text.trim()} onClick={() => void mutate("add")}><Plus />记住</Button></div>
    </div>
    {notice && <div role="status" className="rounded-md border bg-muted/40 p-3 text-sm">{notice}
      {saved && !rows.some(row => row.id === saved.id || row.text === saved.text) && <div className="mt-2"><span>当前列表未显示新保存的记忆。</span><Button variant="link" size="sm" onClick={() => { setSearch(saved.text.slice(0, 200)); setQuery(saved.text.slice(0, 200)); setPage(1); }}>查看已保存记忆</Button></div>}
    </div>}
    {error && !deleting && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {(total > 0 || query || search || loaded && rows.length > 0) && <div className="space-y-1">
      <label htmlFor={`${id}-search`} className="text-sm">搜索个人记忆</label>
      <div className="relative"><Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" />
        <Input ref={searchRef} id={`${id}-search`} className="pl-9 pr-10" value={search} maxLength={200} placeholder="输入关键词" onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)} onChange={event => setSearch(event.target.value)} onKeyDown={event => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { setQuery(search); setPage(1); } }} />
        {search && <Button variant="ghost" size="icon" className="absolute right-0 top-0" aria-label="清除搜索" onClick={() => { setSearch(""); setQuery(""); setPage(1); searchRef.current?.focus(); }}><X /></Button>}
      </div>
    </div>}
    <div className="min-h-5 text-xs text-muted-foreground" role="status">{loading ? loaded ? "正在更新列表…" : "正在读取记忆…" : ""}</div>
    {loadError && <div role="alert" className="flex flex-wrap items-center gap-2 text-sm text-destructive"><span>{loaded ? "更新失败，当前显示上次读取的记忆。" : "记忆加载失败，请刷新重试。"}</span><Button variant="outline" size="sm" onClick={() => void load()}>重试</Button></div>}
    <div className="space-y-2" aria-busy={loading}>
      {visible.map(row => <article key={row.id} aria-label={`记忆：${row.text.slice(0, 40)}`} className={`rounded-lg border p-3 ${saved?.id === row.id ? "border-primary bg-primary/5" : "border-border"}`}>
        {pinned && editing?.id === row.id && <p className="mb-2 text-xs text-muted-foreground">这条记忆不在当前页或筛选结果中，未保存的编辑已保留。</p>}
        {editing?.id === row.id ? <div ref={editor} className="space-y-2">
          <label htmlFor={`${id}-edit`} className="text-sm font-medium">编辑内容</label>
          <MemoryTextarea id={`${id}-edit`} value={editText} maxLength={4000} disabled={busy} onChange={event => setEditText(event.target.value)} />
          {editText.length > 3600 && <p className="text-xs text-muted-foreground">{editText.length} / 4000 字{editText.length > 4000 ? "，请精简后保存" : ""}</p>}
          <div className="flex gap-2"><Button disabled={busy || !editText.trim() || editText.length > 4000} onClick={() => void mutate("edit")}>保存修改</Button><Button variant="outline" disabled={busy} onClick={() => changeEditor(null)}>取消编辑</Button></div>
        </div> : <>
          <p className="whitespace-pre-wrap break-words text-sm leading-6 [overflow-wrap:anywhere]">{row.text}</p>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2"><span className="text-xs text-muted-foreground" title={row.created_at ? beijingTime(row.created_at) : undefined}>{row.created_at ? formatRelativeTime(row.created_at) : ""}</span>
            <div className="flex gap-1"><Button variant="outline" size="sm" disabled={busy} aria-label={`编辑记忆：${row.text.slice(0, 40)}`} onClick={() => changeEditor(row)}>编辑</Button><Button variant="ghost" size="sm" disabled={busy} aria-label={`删除记忆：${row.text.slice(0, 40)}`} onClick={() => { setError(""); setDeleting(row); }}><Trash2 />删除</Button></div>
          </div>
        </>}
      </article>)}
      {loaded && !loading && !loadError && !rows.length && <div className="py-6 text-center text-sm text-muted-foreground"><p>{query ? "没有匹配的个人记忆" : "添加一条常用偏好，下次不用重复说明。"}</p>
        {!query && !text && <div className="mt-3 flex flex-wrap justify-center gap-2">{["报告先写结论，再列关键数据", "金额保留两位小数"].map(example => <Button key={example} variant="outline" size="sm" onClick={() => { setText(example); document.getElementById(`${id}-add`)?.focus(); }}>{example}</Button>)}</div>}
      </div>}
    </div>
    {total > 0 && <Pagination page={page} totalPages={Math.max(1, Math.ceil(total / pageSize))} total={total} pageSize={pageSize} pageSizeOptions={[10, 20, 50, 100]} disabled={loading || busy} onChange={setPage} onPageSizeChange={size => { setPageSize(size); setPage(1); }} />}
    <MemoryConfirm open={Boolean(deleting)} title="删除个人记忆？" description={`“${deleting?.text.slice(0, 160) ?? ""}”\n后续任务将不再参考它，已创建的任务不变。`} action="删除记忆" danger busy={busy} error={error} onCancel={() => { setDeleting(null); setError(""); }} onConfirm={() => void mutate("delete")} />
    <MemoryConfirm open={Boolean(discard)} title="放弃未保存的修改？" description="当前修改尚未保存。取消可继续编辑，放弃后保留原记忆。" action="放弃修改" onCancel={() => setDiscard(null)} onConfirm={() => { discard?.(); setDiscard(null); }} />
  </section>;
}
