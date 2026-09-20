import { useEffect, useRef, useState } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import { Button } from '@/components/ui/button';
import { Pagination } from '@/components/ui/pagination';
import { authenticatedFetch, readAuthenticatedBlob, readAuthenticatedJson } from '@/lib/api';
import { beijingTime } from '@/lib/beijingTime';
import 'react-pdf/dist/Page/TextLayer.css';

pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString();

type FileItem = { id: string; name: string; kind: 'input' | 'output' };
type Context = { revision: number | null; total: number; messages: { id: string; role: string; created_at: string; evaluated: boolean }[]; files: FileItem[] };
type Preview = { kind?: string; text?: string; total: number; offset: number; sheets?: string[]; sheet?: number; row?: number; column?: number; total_rows?: number; total_columns?: number; cells?: { text: string }[][] };

export function FeedbackTaskContext({ feedbackId, eventId }: { feedbackId: number; eventId: string }) {
  const [open, setOpen] = useState(false);
  const [context, setContext] = useState<Context | null>(null);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(10);
  const [filePage, setFilePage] = useState(1);
  const [fileSize, setFileSize] = useState(10);
  const [selected, setSelected] = useState<{ file?: FileItem; message?: string; label: string } | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [pdf, setPdf] = useState('');
  const [pdfPage, setPdfPage] = useState(1);
  const [pdfCount, setPdfCount] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const mounted = useRef(true);
  const flight = useRef(0);
  const urls = useRef(new Set<string>());
  const previewHeading = useRef<HTMLHeadingElement>(null);
  const retry = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (!selected) return;
    previewHeading.current?.scrollIntoView({ block: 'start' });
    previewHeading.current?.focus({ preventScroll: true });
  }, [selected]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; flight.current++; urls.current.forEach(url => URL.revokeObjectURL(url)); urls.current.clear(); };
  }, []);
  function clearPreview() {
    setPreview(null); setPdf(''); setPdfPage(1); setPdfCount(0);
    urls.current.forEach(url => URL.revokeObjectURL(url)); urls.current.clear();
  }
  async function request(body: Record<string, unknown>, consume: (response: Response, current: () => boolean) => Promise<void>) {
    const id = ++flight.current;
    setBusy(true); setError('');
    try {
      const response = await authenticatedFetch(`/api/feedback/${feedbackId}/task-context`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ audit_event_id: eventId, ...body }),
      });
      if (!response.ok) {
        const data = await readAuthenticatedJson(response);
        throw new Error(typeof data.detail === 'string' ? data.detail : '原任务内容暂不可用，请重试');
      }
      // 关闭、切换反馈或账号后，晚到响应不得重新展示私人正文。
      if (!mounted.current || flight.current !== id) return;
      await consume(response, () => mounted.current && flight.current === id);
    } catch (e) {
      if (mounted.current && flight.current === id) setError(e instanceof Error ? e.message : '读取失败，请重试');
    } finally { if (mounted.current && flight.current === id) setBusy(false); }
  }
  async function load(nextPage = 1, nextSize = size) {
    retry.current = () => void load(nextPage, nextSize);
    setOpen(true); clearPreview(); setSelected(null);
    await request({ offset: (nextPage - 1) * nextSize, limit: nextSize }, async (response, current) => {
      const data: Context = await readAuthenticatedJson(response);
      if (!current()) return;
      setContext(data); setPage(nextPage); setSize(nextSize);
    });
  }
  async function show(target: NonNullable<typeof selected>, extra: Record<string, number> = {}) {
    retry.current = () => void show(target, extra);
    clearPreview(); setSelected(target);
    await request({ action: target.file ? 'preview' : 'message', file_id: target.file?.id, message_id: target.message, ...extra }, async (response, current) => {
      if (response.headers.get('content-type')?.includes('application/pdf')) {
        const blob = await readAuthenticatedBlob(response);
        if (!current()) return;
        const url = URL.createObjectURL(blob); urls.current.add(url); setPdf(url);
      } else {
        const data: Preview = await readAuthenticatedJson(response);
        if (current()) setPreview(data);
      }
    });
  }
  async function download(file: FileItem) {
    retry.current = () => void download(file);
    await request({ action: 'download', file_id: file.id }, async (response, current) => {
      const blob = await readAuthenticatedBlob(response);
      if (!current()) return;
      const url = URL.createObjectURL(blob); urls.current.add(url);
      const disposition = response.headers.get('content-disposition') || '';
      const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
      const quoted = /filename="([^"]+)"/i.exec(disposition)?.[1];
      let filename = quoted || file.name;
      if (encoded) { try { filename = decodeURIComponent(encoded); } catch { /* 无效编码保留后备名称。 */ } }
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click();
    });
  }
  function close() { flight.current++; retry.current = null; setOpen(false); setContext(null); setSelected(null); clearPreview(); setBusy(false); setError(''); }
  return <section className="min-w-0 rounded-lg border p-3" aria-label="原任务与实际结果">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h4 className="font-medium">原任务与实际结果</h4>
      {open ? <Button size="sm" variant="outline" onClick={close}>收起原任务</Button> : <Button size="sm" onClick={() => void load()}>查看原任务</Button>}
    </div>
    <p className="mt-1 text-xs text-muted-foreground">只读查看被评价版本的需求、相关对话、输入资料及实际结果。不会重新执行任务。</p>
    {open && <div className="mt-3 min-w-0 flex flex-col gap-3">
      {busy && <p role="status">正在读取，文件转换可能需要稍候…</p>}
      {error && <p role="alert" className="break-words text-destructive">{error}<Button variant="outline" size="sm" disabled={busy} onClick={() => retry.current?.()}>重试</Button></p>}
      {context && <>
        <h5 className="font-medium">原始需求与相关对话{context.revision ? ` · V${context.revision}` : ''}</h5>
        {!context.total && <p>没有可追溯的对话记录。</p>}
        {context.messages.map(message => <div key={message.id} className="flex flex-wrap items-center justify-between gap-2 rounded border p-2">
          <span>{message.id === 'objective' ? '原始需求' : message.role === 'user' ? '用户提问' : '智能体回答'}{message.evaluated ? ' · 被评价的回答' : ''}<span className="ml-2 text-xs text-muted-foreground">{beijingTime(message.created_at)}</span></span>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void show({ message: message.id, label: message.role === 'user' ? '用户原始内容' : '智能体实际回答' })}>查看内容</Button>
        </div>)}
        <Pagination page={page} totalPages={Math.max(1, Math.ceil(context.total / size))} total={context.total} pageSize={size} pageSizeOptions={[10, 20, 50, 100]} disabled={busy} onChange={p => void load(p)} onPageSizeChange={s => void load(1, s)} />
        <h5 className="font-medium">输入资料与结果文件</h5>
        {!context.files.length && <p className="text-muted-foreground">该记录没有保存可追溯的文件关联；上方可查看实际问答。</p>}
        {context.files.slice((filePage - 1) * fileSize, filePage * fileSize).map(file => <div key={file.id} className="flex flex-wrap items-center justify-between gap-2 rounded border p-2">
          <span className="min-w-0 flex-1 break-all">{file.kind === 'input' ? '输入资料' : '实际结果'} · {file.name}</span>
          <div className="flex gap-2"><Button size="sm" variant="outline" disabled={busy} onClick={() => void show({ file, label: file.name })}>预览</Button><Button size="sm" variant="outline" disabled={busy} onClick={() => void download(file)}>下载原件</Button></div>
        </div>)}
        {context.files.length > 0 && <Pagination page={filePage} totalPages={Math.ceil(context.files.length / fileSize)} total={context.files.length} pageSize={fileSize} pageSizeOptions={[10, 20, 50, 100]} disabled={busy} onChange={setFilePage} onPageSizeChange={size => { setFileSize(size); setFilePage(1); }} />}
      </>}
      {selected && <div className="order-first min-w-0 space-y-2 rounded border bg-muted/20 p-3" aria-label="内容预览">
        <h5 ref={previewHeading} tabIndex={-1} className="break-all font-medium outline-none">{selected.label}</h5>
        {selected.file && <Button size="sm" variant="outline" disabled={busy} onClick={() => void download(selected.file!)}>下载完整原件</Button>}
        {preview?.text !== undefined && <><pre className="whitespace-pre-wrap break-words font-sans text-sm">{preview.text}</pre><div className="flex flex-wrap items-center justify-end gap-2 text-xs text-muted-foreground"><span>第 {Math.floor(preview.offset / 16000) + 1} / {Math.max(1, Math.ceil(preview.total / 16000))} 页 · 共 {preview.total} 字符</span><Button size="sm" variant="outline" disabled={busy || preview.offset === 0} onClick={() => void show(selected, { offset: Math.max(0, preview.offset - 16000) })}>上一段</Button><Button size="sm" variant="outline" disabled={busy || preview.offset + 16000 >= preview.total} onClick={() => void show(selected, { offset: preview.offset + 16000 })}>下一段</Button></div></>}
        {preview?.kind === 'workbook' && <>
          <label>工作表 <select disabled={busy} value={preview.sheet} onChange={e => void show(selected, { sheet: Number(e.target.value) })} className="rounded border bg-background p-1">{preview.sheets?.map((s, i) => <option key={i} value={i}>{s}</option>)}</select></label>
          <p className="text-xs text-muted-foreground">共 {preview.total_rows} 行、{preview.total_columns} 列；当前从第 {(preview.row || 0) + 1} 行、第 {(preview.column || 0) + 1} 列开始。</p>
          <div className="max-h-96 overflow-auto"><table className="border-collapse text-xs"><thead><tr><th scope="col">行号</th>{preview.cells?.[0]?.map((_, c) => <th key={c} scope="col" className="border p-2">第 {(preview.column || 0) + c + 1} 列</th>)}</tr></thead><tbody>{preview.cells?.map((row, r) => <tr key={r}><th scope="row" className="border p-2">{(preview.row || 0) + r + 1}</th>{row.map((cell, c) => <td key={c} className="min-w-24 max-w-80 break-words border p-2">{cell.text}</td>)}</tr>)}</tbody></table></div>
          <div className="flex flex-wrap gap-2">{[['上100行', 'row', -100], ['下100行', 'row', 100], ['左30列', 'column', -30], ['右30列', 'column', 30]].map(([label, axis, step]) => {
            const key = axis as 'row' | 'column', next = (preview[key] || 0) + Number(step), total = key === 'row' ? preview.total_rows : preview.total_columns;
            return <Button key={label} size="sm" variant="outline" disabled={busy || next < 0 || next >= (total || 0)} onClick={() => void show(selected, { sheet: preview.sheet || 0, row: preview.row || 0, column: preview.column || 0, [key]: next })}>{label}</Button>;
          })}</div>
        </>}
        {pdf && <><div className="max-h-96 overflow-auto"><Document file={pdf} onLoadSuccess={({ numPages }) => setPdfCount(numPages)} onLoadError={() => setError('预览副本无法读取，请下载原件')}><Page pageNumber={pdfPage} width={Math.min(640, window.innerWidth - 100)} renderAnnotationLayer={false} renderTextLayer /></Document></div><Pagination page={pdfPage} totalPages={pdfCount || 1} total={pdfCount} onChange={setPdfPage} /></>}
      </div>}
    </div>}
  </section>;
}
