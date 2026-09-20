import { beijingTime } from "@/lib/beijingTime";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { api, downloadFile } from "@/lib/api";
import { Button } from "@/components/ui/button";

export type Draft = {
  draft_id: string; revision: number; acceptance_pending?: boolean; review_waiting?: boolean; created_at?: string | null;
  files: Array<{ filename: string; download_url: string; preview?: string | null; preview_table?: string[][] | null;
    preview_truncated?: boolean | null; preview_scope?: string | null; page_preview_url?: string | null }>;
};

export function DraftResultPanel({ taskId, revision, ownerId, status, active, onAccepted, onPreview, actionsTarget, onModify }: {
  taskId: string; revision: number; ownerId?: string; status: string; active: boolean;
  onAccepted: (revision: number) => void;
  onPreview: (draft: Draft, open?: boolean) => void;
  actionsTarget?: HTMLElement | null;
  onModify?: () => void;
}) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [error, setError] = useState("");
  const [needsRecheck, setNeedsRecheck] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [confirmDraft, setConfirmDraft] = useState<Draft | null>(null);
  const [readingDraft, setReadingDraft] = useState<Draft | null>(null);
  const mounted = useRef(false);
  const accepting = useRef(false);
  const reviewPending = useRef(false);
  const acceptOrigin = useRef<HTMLElement | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const query = useQuery<{ draft: Draft | null }>({
    queryKey: ["workspace-draft", ownerId, taskId, revision],
    queryFn: () => api.get(`/api/semantic-workspace/tasks/${taskId}/draft?revision=${revision}`),
    refetchInterval: ["queued", "running", "cancelling"].includes(status) ? 2000 : false,
    retry: false,
  });
  useEffect(() => { void queryClient.invalidateQueries({ queryKey: ["workspace-draft", ownerId, taskId, revision] }); }, [status, ownerId, taskId, revision, queryClient]);
  // 确认期间冻结用户看到的那份初稿，轮询的新稿不能替换待接受身份。
  const draft = confirmDraft ?? readingDraft ?? query.data?.draft;
  useEffect(() => { if (!readingDraft && query.data?.draft) setReadingDraft(query.data.draft); }, [query.data?.draft, readingDraft]);
  const newerDraft = query.data?.draft && query.data.draft.draft_id !== draft?.draft_id ? query.data.draft : null;
  const reviewWaiting = active && status === "needs_input" && query.data?.draft?.review_waiting && query.data.draft.draft_id === draft?.draft_id;
  useEffect(() => { if (draft) onPreview(draft); }, [draft, onPreview]);
  if (!draft) return query.isError ? <section className="mx-auto max-w-4xl p-6 text-sm"><p role="alert">初稿读取失败，任务记录仍保留。</p><Button variant="outline" className="mt-2" disabled={query.isFetching} onClick={() => void query.refetch()}>重新读取初稿</Button></section> : null;

  async function refreshStatus() {
    try {
      const [fresh, current] = await Promise.all([
        query.refetch(), api.get(`/api/semantic-workspace/tasks/${taskId}`),
        queryClient.invalidateQueries({ queryKey: ["semantic-workspace-task", taskId] }),
        queryClient.invalidateQueries({ queryKey: ["semantic-workspace-tasks"] }),
      ]);
      if (mounted.current && !fresh.isError && fresh.data?.draft && current?.status) setNeedsRecheck(false);
    } catch {
      if (mounted.current) setError("处理状态仍无法确认，请稍后再检查；不会自动重发接受请求。");
    }
  }

  async function accept() {
    if (!draft || accepting.current || reviewPending.current) return;
    accepting.current = true;
    setBusy(true);
    // 请求发出后回到可阅读的页面；关闭确认框不等于取消发布。
    setConfirming(false);
    setError("");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30_000);
    try {
      const result = await api.post(`/api/semantic-workspace/tasks/${taskId}/draft/accept`, {
        expected_revision: draft.revision, draft_id: draft.draft_id, accept_unverified: true,
      }, {}, controller.signal);
      // 发布回执已经确认，后台刷新不能阻塞本次接受收尾。
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["semantic-workspace-task", taskId] }),
        queryClient.invalidateQueries({ queryKey: ["semantic-workspace-tasks"] }),
      ]);
      if (mounted.current) onAccepted(result.revision);
    } catch (cause) {
      if (mounted.current) {
        setNeedsRecheck(true);
        setError(controller.signal.aborted ? "等待响应超时，发布结果尚未确认。请先检查处理状态，不要重新运行任务。"
          : cause instanceof Error ? cause.message : "发布结果尚未确认，请先检查处理状态。");
      }
    } finally {
      window.clearTimeout(timeout);
      accepting.current = false;
      if (mounted.current) { setBusy(false); setConfirming(false); setConfirmDraft(null); }
    }
  }
  async function continueReview() {
    if (!draft || !reviewWaiting || reviewPending.current || accepting.current || needsRecheck) return;
    reviewPending.current = true;
    setReviewing(true); setError("");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30_000);
    try {
      await api.post(`/api/semantic-workspace/tasks/${taskId}/draft/verify`, {
        expected_revision: draft.revision, draft_id: draft.draft_id,
      }, {}, controller.signal);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["semantic-workspace-task", taskId] }),
        queryClient.invalidateQueries({ queryKey: ["semantic-workspace-tasks"] }),
        query.refetch(),
      ]);
    } catch (cause) {
      if (mounted.current) {
        setNeedsRecheck(true);
        setError(controller.signal.aborted ? "核对请求的结果尚未确认，请先检查处理状态；不会自动重发。" : cause instanceof Error ? cause.message : "核对请求未确认，请先检查处理状态。");
      }
    } finally {
      window.clearTimeout(timeout);
      reviewPending.current = false;
      if (mounted.current) setReviewing(false);
    }
  }
  const updateNotice = newerDraft && <p role="status" className="mt-2 text-sm">有新版初稿，当前阅读内容保持不变。<button type="button" disabled={busy || confirming} className="ml-2 rounded text-primary underline focus-visible:ring-2 focus-visible:ring-ring" onClick={() => { setReadingDraft(newerDraft); onPreview(newerDraft, true); }}>查看新版初稿</button></p>;
  const canAccept = active && !["cancelling", "completed", "pausing", "paused"].includes(status);
  const openConfirmation = (origin: HTMLElement) => {
    if (!canAccept || needsRecheck || query.isError || accepting.current || reviewPending.current) return;
    acceptOrigin.current = origin;
    setConfirmDraft(draft); setConfirming(true);
  };
  const feedback = <>
    {updateNotice}
    {reviewing && <p role="status" className="mt-2 text-sm">正在恢复核对，初稿仍可查看。</p>}
    {busy && <p role="status" className="mt-2 text-sm">{status === "cancelling" ? "正在等待验证停止。" : draft.acceptance_pending ? "正在保存正式结果。" : "已请求停止验证并保存正式结果。"}你可以继续查看内容，完成后自动更新。</p>}
    {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
    {query.isError && <p role="alert" className="mt-2 text-sm text-destructive">更新初稿失败，当前显示的是上次读取内容。</p>}
    {(busy || error || query.isError) && <Button variant="outline" size="sm" className="mt-2" disabled={query.isFetching} onClick={() => void refreshStatus()}>检查处理状态</Button>}
  </>;
  return (
    <section aria-label="初稿结果" className="mx-auto max-w-4xl border-b px-6 py-5">
      <h2 className="font-semibold">初稿已生成 · 尚未完成验证</h2>
      {!actionsTarget && feedback}
      <p className="mt-2 text-sm text-muted-foreground">可以先查看和下载。{reviewWaiting ? "已暂停后续核对，等待你的决定；" : ["queued", "running"].includes(status)
        ? "不操作将继续自动验证；" : draft.acceptance_pending ? "发布尚未完成，可继续发布已接受的初稿；" : "当前验证已停止；"}初稿不代表准确性已获确认。</p>
      <button type="button" className="my-3 mr-3 rounded-md border px-3 py-2 text-sm hover:bg-muted" onClick={() => onPreview(draft, true)}>查看初稿 · {draft.files.length} 个文件</button>
      {(canAccept || busy) && <AlertDialog.Root open={confirming} onOpenChange={value => {
        if (!busy) { setConfirmDraft(value ? draft : null); setConfirming(value); }
      }}>
        <button type="button" disabled={busy || reviewing || needsRecheck || query.isError} onClick={event => openConfirmation(event.currentTarget)} className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-ring">
          {busy ? "正在停止验证并发布…" : draft.acceptance_pending ? "继续发布已接受的初稿" : reviewWaiting ? "接受初稿" : "接受初稿并结束验证"}
        </button>
        <AlertDialog.Portal><AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
          <AlertDialog.Content onCloseAutoFocus={event => { event.preventDefault(); if (acceptOrigin.current?.isConnected) acceptOrigin.current.focus(); }} className="fixed left-1/2 top-1/2 z-50 max-h-[85dvh] w-[min(90vw,28rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border bg-background p-6 shadow-xl">
            <AlertDialog.Title className="font-semibold">将这份初稿转为正式结果？</AlertDialog.Title>
            <AlertDialog.Description className="my-4 text-sm text-muted-foreground">后续验证将停止，并创建新版本保存你看到的这份初稿。结果标记为“用户接受”，不表示系统验证通过；未完成检查和原任务记录继续保留。</AlertDialog.Description>
            <p className="text-sm text-muted-foreground">任务版本 V{draft.revision} · 初稿 {draft.draft_id.slice(0, 8)} · {draft.files.length} 个文件</p>
            {draft.created_at && <p className="mt-2 text-sm text-muted-foreground">生成于 {beijingTime(draft.created_at)}</p>}
            <ul className="my-3 max-h-32 overflow-y-auto text-sm">{draft.files.map(file => <li key={file.filename} className="break-words">{file.filename}</li>)}</ul>
            <div className="flex flex-wrap justify-end gap-3"><AlertDialog.Cancel asChild><Button variant="outline">返回查看</Button></AlertDialog.Cancel>
              <button type="button" disabled={busy} onClick={() => void accept()} className="rounded-md bg-primary px-3 py-2 text-primary-foreground disabled:opacity-50">{busy ? "正在处理…" : "确认接受并发布"}</button></div>
            {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
          </AlertDialog.Content></AlertDialog.Portal>
      </AlertDialog.Root>}
      {reviewWaiting && <Button variant="outline" className="ml-3" disabled={busy || reviewing || confirming || needsRecheck || query.isError} onClick={() => void continueReview()}>继续核对</Button>}
      {actionsTarget && createPortal(<div className="space-y-2">
        {feedback}
        <div className="flex flex-wrap gap-2">
          {(canAccept || busy) && <Button disabled={busy || reviewing || needsRecheck || query.isError} onClick={event => openConfirmation(event.currentTarget)}>采用这版初稿</Button>}
          {reviewWaiting && <Button variant="outline" disabled={busy || reviewing || confirming || needsRecheck || query.isError} onClick={() => void continueReview()}>继续核对</Button>}
          {onModify && <Button variant="outline" onClick={onModify}>提出修改</Button>}
        </div>
      </div>, actionsTarget)}
    </section>
  );
}

export type DraftViewState = { filename: string; positions: Record<string, { top: number; left: number; page?: number }> };
export const initialDraftView: DraftViewState = { filename: "", positions: {} };

export function DraftPreview({ draft, onClose, actionsRef, expanded, onToggleExpand, viewState, onViewStateChange }: {
  draft: Draft; onClose: () => void; actionsRef?: (element: HTMLDivElement | null) => void;
  expanded?: boolean; onToggleExpand?: () => void;
  viewState: DraftViewState; onViewStateChange: (state: DraftViewState) => void;
}) {
  const [error, setError] = useState("");
  const [downloading, setDownloading] = useState(false);
  const download = useRef<AbortController>();
  useEffect(() => () => download.current?.abort(), [draft.draft_id]);
  const file = draft.files.find(item => item.filename === viewState.filename) ?? draft.files[0];
  const positionKey = `${draft.draft_id}:${file?.filename}`;
  const page = viewState.positions[positionKey]?.page || 1;
  const pdf = useQuery<{ image: string; page: number; page_count: number }>({
    queryKey: ["draft-pdf-preview", file?.page_preview_url, page],
    queryFn: ({ signal }) => api.get(`${file!.page_preview_url}&page=${page}`, { signal }),
    enabled: Boolean(file?.page_preview_url), retry: false, gcTime: 0,
  });
  const scroller = useRef<HTMLDivElement>(null);
  const loadedImage = useRef("");
  const imageIdentity = `${positionKey}:${page}`;
  const restorePosition = () => {
    if (!scroller.current) return;
    scroller.current.scrollTop = viewState.positions[positionKey]?.top ?? 0;
    scroller.current.scrollLeft = viewState.positions[positionKey]?.left ?? 0;
  };
  useLayoutEffect(() => {
    if (!scroller.current || !file) return;
    loadedImage.current = "";
    // PDF 图片加载前没有可恢复的高度，不能让临时零位置覆盖阅读记录。
    if (!file.page_preview_url) restorePosition();
  }, [file?.filename, draft.draft_id, page]);
  if (!file) return null;
  let text = file.preview ?? "";
  if (file.filename.endsWith(".json")) {
    try { text = JSON.stringify(JSON.parse(text), null, 2); } catch { /* 截断预览保留原文，不伪造完整 JSON。 */ }
  }
  return <section aria-label="初稿预览" className="flex h-full min-w-0 flex-col bg-background text-foreground">
    <header className="space-y-3 border-b p-4">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="font-semibold">初稿预览</h2>
        <div className="flex flex-wrap gap-2">{onToggleExpand && <Button variant="outline" size="sm" aria-expanded={expanded} onClick={onToggleExpand}>{expanded ? "恢复分栏" : "展开预览"}</Button>}
        <Button variant="outline" size="sm" onClick={onClose}>关闭初稿预览</Button></div></div>
      <p className="text-sm text-muted-foreground">尚未完成验证 · V{draft.revision} · 初稿 {draft.draft_id.slice(0, 8)}</p>
      {draft.created_at && <p className="text-xs text-muted-foreground">生成于 {beijingTime(draft.created_at)}</p>}
      <select aria-label="初稿文件" value={file.filename} onChange={event => { onViewStateChange({ ...viewState, filename: event.target.value }); setError(""); }} className="w-full min-w-0 rounded-md border bg-background p-2 text-sm focus-visible:ring-2 focus-visible:ring-ring">
        {draft.files.map(item => <option key={item.filename}>{item.filename}</option>)}
      </select>
      <button type="button" disabled={downloading} className="rounded-md border px-3 py-2 text-xs hover:bg-muted disabled:opacity-50" onClick={async () => {
        const controller = new AbortController();
        download.current = controller;
        setDownloading(true); setError("");
        try { await downloadFile(file.download_url, file.filename, controller.signal); }
        catch (cause) { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "下载失败"); }
        finally { if (download.current === controller) setDownloading(false); }
      }}>下载初稿 {file.filename}</button>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {file.page_preview_url && <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" disabled={page <= 1 || pdf.isFetching} onClick={() => onViewStateChange({ ...viewState, positions: { ...viewState.positions, [positionKey]: { top: 0, left: 0, page: page - 1 } } })}>上一页</Button>
        <span className="text-sm">第 {page} / {pdf.data?.page_count ?? "—"} 页</span>
        <Button variant="outline" size="sm" disabled={!pdf.data || page >= pdf.data.page_count || pdf.isFetching} onClick={() => onViewStateChange({ ...viewState, positions: { ...viewState.positions, [positionKey]: { top: 0, left: 0, page: page + 1 } } })}>下一页</Button>
      </div>}
    </header>
    <div ref={scroller} tabIndex={0} aria-label="初稿内容" className="min-h-0 flex-1 overflow-auto p-4 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring" onScroll={event => {
      if (file.page_preview_url && loadedImage.current !== imageIdentity) return;
      onViewStateChange({ ...viewState, positions: { ...viewState.positions, [positionKey]: { ...viewState.positions[positionKey], top: event.currentTarget.scrollTop, left: event.currentTarget.scrollLeft } } });
    }}>
      {file.page_preview_url ? pdf.isError ? <div><p role="alert">该页读取失败，初稿仍保留。</p><Button variant="outline" className="mt-2" onClick={() => void pdf.refetch()}>重新读取该页</Button></div>
        : pdf.isPending ? <p role="status">正在读取第 {page} 页…</p>
          : <img key={imageIdentity} src={pdf.data.image} alt={`${file.filename} 第 ${page} 页`} onLoad={() => { loadedImage.current = imageIdentity; restorePosition(); }} className="h-auto w-full bg-white" />
        : file.preview_table?.length ? <table className="w-full border-collapse text-left text-sm"><thead className="sticky top-0 bg-muted"><tr>{file.preview_table[0].map((cell, index) => <th key={index} scope="col" className="min-w-28 whitespace-nowrap border p-2 font-medium">{cell}</th>)}</tr></thead>
        <tbody>{file.preview_table.slice(1).map((row, index) => <tr key={index}>{row.map((cell, column) => <td key={column} className="max-w-80 whitespace-pre-wrap break-words border p-2 align-top">{cell}</td>)}</tr>)}</tbody></table>
        : text ? <pre className="whitespace-pre-wrap break-words font-mono text-sm leading-relaxed">{text}</pre>
          : <p className="text-sm text-muted-foreground">此格式暂不支持内联预览，可以使用上方独立下载按钮查看原文件。</p>}
    </div>
    <div ref={actionsRef} className="shrink-0 border-t px-4 py-2" />
    <p className="border-t px-4 py-2 text-xs text-muted-foreground">{file.preview_scope}{file.page_preview_url ? "当前显示一页，可翻页核对。" : file.preview_truncated ? `仅展示部分内容${file.preview_table ? `（${Math.max(0, file.preview_table.length - 1)} 行）` : "（前 16000 字以内）"}，非全文；完整文件请下载。` : file.preview_truncated === false ? "已显示本文件可提取的文字或表格内容。" : "有界预览：最多 50 行表格或 16000 字；完整文件请下载。"}切换预览不会下载文件。</p>
  </section>;
}
