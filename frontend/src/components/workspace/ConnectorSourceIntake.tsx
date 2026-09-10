import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ApiError } from "@/lib/api";
import { acquireConnectorSource, cancelConnectorAcquisition, getConnectorAcquisition, resolveConnectorSource, type ConnectorScope, type ConnectorRequest, type ConnectorAttempt } from "@/lib/connectorSourceApi";
import { getConnectionSchema, listOwnerConnections, type ConnectorSource, type ConnectionSchema, type OwnerConnection } from "@/lib/connectorSourceApi";
import { ConnectorScopeFacts } from "./ConnectorScopeFacts";
import type { SourceSnapshot } from "@/types/semanticWorkspace";

type StoredRead = { key: string; payload: ConnectorRequest; scope: ConnectorScope; attemptId?: string };
const ERROR_LABELS: Record<string, string> = {
  authorization_expired: "来源授权已过期，请通过现有连接配置更新授权后重新选择。",
  permission_denied: "来源拒绝读取，请核对连接的只读权限。",
  no_results: "本次范围内没有记录，请调整表或筛选范围。",
  local_parse: "本地无法解析来源内容，仅支持JSON或JSONL。",
  unsupported_media: "来源不是支持的JSON或JSONL数据。",
  source_network: "来源网络或超时失败，请稍后重新选择范围。",
  source_rejected: "来源服务拒绝本次读取，请核对权限和读取范围。",
  source_scope_denied: "地址不在允许读取范围内，请核对公开地址。",
  source_redirect_denied: "来源发生未允许的跳转，请核对最终公开地址。",
  source_response_limit: "来源超过本次大小或数量上限，请缩小读取范围。",
  pagination_repeated: "分页返回重复内容，已停止；请核对页码参数后重新选择。",
};
function restore(key: string): StoredRead | null {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    return value && typeof value.key === "string" && typeof value.payload?.purpose === "string" && /^[a-f0-9]{64}$/.test(value.payload?.expected_connection_version) && ["http_api", "database"].includes(value.payload?.source?.source_type) && value.scope?.kind === "connector" ? value : null;
  } catch { return null; }
}

export function ConnectorSourceIntake({ open, onOpenChange, ownerId, storageScope, purpose, onSelected }: { open: boolean; onOpenChange: (open: boolean) => void; ownerId: string; storageScope: string; purpose: string; onSelected: (snapshot: SourceSnapshot) => boolean }) {
  const storageKey = `mangrove_connector_attempt_${ownerId}_${storageScope}`;
  const [saved] = useState(() => restore(storageKey));
  const stored = useRef<string | null>(null);
  const [storageReady] = useState(() => { try { stored.current = localStorage.getItem(storageKey); return true; } catch { return false; } });
  const [obsolete, setObsolete] = useState(false);
  const [url, setUrl] = useState(saved?.payload.source.url || "");
  const [readPurpose, setReadPurpose] = useState(saved?.payload.purpose || purpose.trim() || "读取连接资料供当前任务使用");
  const [paginated, setPaginated] = useState(Boolean(saved?.payload.source.pagination));
  const [pageOptions, setPageOptions] = useState(saved?.payload.source.pagination?.options || { page_param: "page", per_page_param: "per_page", per_page: 100, start_page: 1, max_pages: 10 });
  const [mode, setMode] = useState<ConnectorSource["source_type"]>(saved?.payload.source.source_type || "http_api");
  const [connections, setConnections] = useState<OwnerConnection[]>([]);
  const [connectionId, setConnectionId] = useState(saved?.payload.source.connection_id || "");
  const [schema, setSchema] = useState<ConnectionSchema | null>(null);
  const [table, setTable] = useState(saved?.payload.source.table || "");
  const [columns, setColumns] = useState<string[]>(saved?.payload.source.fields || []);
  const [filterField, setFilterField] = useState(saved?.payload.source.filters?.[0]?.field || "");
  const [filterValue, setFilterValue] = useState(saved?.payload.source.filters?.[0]?.value || "");
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const [catalogRevision, setCatalogRevision] = useState(0);
  const [scope, setScope] = useState<ConnectorScope | null>(saved?.scope || null);
  const [snapshot, setSnapshot] = useState<SourceSnapshot | null>(null);
  const [attempt, setAttempt] = useState<ConnectorAttempt | null>(null);
  const frozen = useRef<StoredRead | null>(saved);
  const [unknown, setUnknown] = useState(Boolean(saved));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef(0);
  const inFlight = useRef(false);
  const stopping = useRef(false);
  const returnFocus = useRef<HTMLElement | null>(null);
  const [stopBusy, setStopBusy] = useState(false);
  useEffect(() => () => { request.current++; }, []);
  useEffect(() => { if (open && !frozen.current) setReadPurpose(purpose.trim() || "读取连接资料供当前任务使用"); }, [open]);
  useEffect(() => {
    if (!open || mode !== "database") return;
    let current = true;
    setCatalogError(""); setCatalogLoading(true);
    void Promise.all([listOwnerConnections(), connectionId ? getConnectionSchema(connectionId) : Promise.resolve(null)])
      .then(([items, detail]) => { if (current) { setConnections(items); setSchema(detail); } })
      .catch(() => { if (current) setCatalogError("无法读取连接或表清单，请重新加载；没有发起数据读取。"); })
      .finally(() => { if (current) setCatalogLoading(false); });
    return () => { current = false; };
  }, [open, mode, ownerId, connectionId, catalogRevision]);
  const tables = schema?.tables.filter(item => !item.schema || item.schema === schema.default_schema || (!schema.default_schema && item.schema === "main")) ?? [];
  const selectedTable = tables.find(item => item.name === table);
  function changed() { request.current++; setScope(null); setSnapshot(null); setError(""); }
  function selection(): ConnectorSource {
    return mode === "http_api" ? { source_type: "http_api", url: url.trim(), ...(paginated ? { pagination: { strategy: "page" as const, options: pageOptions } } : {}) } : { source_type: "database", connection_id: connectionId, table, ...(columns.length ? { fields: columns } : {}), ...(filterField ? { filters: [{ field: filterField, op: "eq", value: filterValue }] } : {}) };
  }
  async function run(action: (identity: number) => Promise<void>) {
    if (inFlight.current || stopping.current) return;
    const identity = ++request.current;
    inFlight.current = true; setBusy(true); setError("");
    try { assertStored(); await action(identity); } catch (reason) { if (identity === request.current) { if (frozen.current) setUnknown(true); setError(reason instanceof Error ? reason.message : "读取尚未确认"); } }
    finally { if (identity === request.current) { inFlight.current = false; setBusy(false); } }
  }
  function assertStored() {
    if (!storageReady) throw new Error("浏览器存储不可用，无法保存恢复身份；本次未发起新的读取。");
    if (localStorage.getItem(storageKey) !== stored.current) { setObsolete(true); setSnapshot(null); throw new Error("另一页面已更新此读取，请刷新页面查看最新请求；本页不会覆盖或加入旧资料。"); }
  }
  function persist(value = frozen.current) {
    // 只保存Owner绑定的冻结选择和请求身份，不保存原件或预览正文。
    assertStored();
    const raw = value ? JSON.stringify(value) : null;
    if (raw) localStorage.setItem(storageKey, raw); else localStorage.removeItem(storageKey);
    stored.current = raw;
  }
  function reset() {
    try { persist(null); } catch (reason) { setError(reason instanceof Error ? reason.message : "恢复身份未能更新，请保留当前页面。"); return false; }
    request.current++; frozen.current = null;
    setAttempt(null); setSnapshot(null); setScope(null); setUnknown(false); setError("");
    return true;
  }
  function accept(response: ConnectorAttempt) {
    const original = frozen.current;
    const matches = (value: ConnectorScope) => value?.kind === "connector" && value.connection_id === original?.scope.connection_id && value.connection_version === original?.scope.connection_version && value.configuration_version === original?.scope.configuration_version && value.selection_sha256 === original?.scope.selection_sha256;
    // 原件必须属于原请求和冻结范围，不能只凭成功状态把别次资料放进当前任务。
    if (!original || response.idempotency_key !== original.key || !response.attempt_id || (original.attemptId && response.attempt_id !== original.attemptId) || !matches(response.allowed_scope)
      || (response.status === "succeeded" && (!response.snapshot || response.snapshot.source_kind !== "connector" || response.snapshot.attempt_id !== response.attempt_id || response.snapshot.snapshot_id !== response.snapshot_id || !response.snapshot.artifacts.length || !matches(response.snapshot.allowed_scope)))) throw new Error("来源响应身份不一致，请保留原请求并核实");
    original.attemptId = response.attempt_id; persist();
    setAttempt(response); setUnknown(false);
    if (response.status === "succeeded" && response.snapshot) setSnapshot(response.snapshot);
    else if (response.status === "failed") setError(ERROR_LABELS[response.error_code || ""] || "本次读取未形成可用资料，请核对来源后重新选择。");
    else if (response.status === "acquiring" && response.error_code !== "connector_checkpoint_ready") { setUnknown(true); setError("读取仍在进行或清理未确认，请查询原状态"); }
  }
  async function read(identity: number, resume = false) {
    if (!scope || !readPurpose.trim() || readPurpose.length > 500 || (resume && unknown)) return;
    const firstRequest = !frozen.current;
    // 断点只能继续同一冻结选择，不能重新生成请求身份。
    frozen.current ??= { key: crypto.randomUUID(), scope, payload: { source: scope.selection, purpose: readPurpose.trim(), expected_connection_version: scope.connection_version, resume_checkpoint: false } };
    try { persist(); } catch (reason) { if (firstRequest) frozen.current = null; throw reason; }
    let response: ConnectorAttempt;
    try { response = await acquireConnectorSource({ ...frozen.current.payload, resume_checkpoint: resume }, frozen.current.key); }
    catch (reason) {
      // 此精确409在服务端领取attempt之前拒绝；已经未知的旧次不能据此清除。
      if (identity === request.current && firstRequest && reason instanceof ApiError && reason.status === 409 && reason.message === "连接版本变化，请重新确认") {
        persist(null); frozen.current = null; setScope(null); setUnknown(false); setError("连接版本变化，请重新核对范围；本次尚未开始读取。");
        return;
      }
      throw reason;
    }
    if (identity !== request.current) return;
    accept(response);
  }
  async function stop() {
    if (!attempt || stopping.current) return;
    // 停止不能排在读取响应之后；先隔离旧响应，再独立请求服务端确认。
    const identity = ++request.current;
    stopping.current = true; setStopBusy(true); inFlight.current = false; setBusy(false); setError("");
    try { assertStored(); const value = await cancelConnectorAcquisition(attempt.attempt_id); if (identity === request.current) accept(value); }
    catch (reason) { if (identity === request.current) { setUnknown(true); setError(reason instanceof Error ? reason.message : "停止结果尚未确认，请查询原状态；本次资料不能加入任务。"); } }
    finally { stopping.current = false; if (identity === request.current) setStopBusy(false); }
  }
  const fields = "mt-1 w-full rounded-lg border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  const validPagination = !paginated || (pageOptions.page_param.trim().length > 0 && pageOptions.per_page_param.trim().length > 0 && pageOptions.page_param !== pageOptions.per_page_param && [pageOptions.per_page, pageOptions.start_page, pageOptions.max_pages].every(value => Number.isSafeInteger(value) && value >= 1) && pageOptions.per_page <= 10000 && pageOptions.max_pages <= 100);
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" /><Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[85vh] w-[min(94vw,640px)] -translate-x-1/2 -translate-y-1/2 space-y-4 overflow-auto rounded-xl border bg-background p-5 shadow-xl" onEscapeKeyDown={event => { if (event.isComposing) event.preventDefault(); }} onOpenAutoFocus={() => { returnFocus.current = document.activeElement as HTMLElement; }} onCloseAutoFocus={event => { event.preventDefault(); if (returnFocus.current?.isConnected) returnFocus.current.focus(); }}>
    <Dialog.Title asChild><h2 className="text-lg font-semibold">从已有连接读取</h2></Dialog.Title>
    <Dialog.Description className="text-sm text-muted-foreground">先核对范围，再读取并保存本次资料。复用本人已登记连接，不填写密码、Cookie 或 SQL；有界读取不保证整个来源完整。</Dialog.Description>
    <fieldset disabled={busy || obsolete || Boolean(frozen.current)} className="space-y-3"><label className="block text-sm font-medium">资料来源<select className={fields} value={mode} onChange={event => { changed(); setMode(event.target.value as ConnectorSource["source_type"]); }}><option value="http_api">公开 HTTP 数据（GET）</option><option value="database">已登记数据库</option></select></label>
    {mode === "http_api" ? <><label className="block text-sm font-medium">公开数据地址<input className={fields} type="url" value={url} onChange={event => { changed(); setUrl(event.target.value); }} placeholder="https://example.com/records.json" /></label>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={paginated} onChange={event => { changed(); setPaginated(event.target.checked); }} />按页码读取</label>
      {paginated && <div className="grid grid-cols-2 gap-3">{([['page_param', '页码参数名'], ['per_page_param', '每页条数参数名']] as const).map(([key, label]) => <label key={key} className="text-sm">{label}<input className={fields} maxLength={100} value={pageOptions[key]} onChange={event => { changed(); setPageOptions(value => ({ ...value, [key]: event.target.value })); }} /></label>)}{([['per_page', '每页条数', 10000], ['start_page', '起始页码', undefined], ['max_pages', '最多读取页数', 100]] as const).map(([key, label, max]) => <label key={key} className="text-sm">{label}<input className={fields} type="number" min={1} max={max} step={1} value={pageOptions[key]} onChange={event => { changed(); setPageOptions(value => ({ ...value, [key]: Number(event.target.value) })); }} /></label>)}<p className="col-span-2 text-xs text-muted-foreground">参数名按来源说明填写；每段最多10批，达到上限不代表读完整个来源。</p></div>}
    </> : <>
      {catalogLoading && <p role="status">正在读取本人连接与表清单…</p>}
      {catalogError && <p role="alert">{catalogError}<button className="ml-2 underline" onClick={() => setCatalogRevision(value => value + 1)}>重新加载连接</button></p>}
      {!catalogLoading && !catalogError && !connections.length && <p role="status">还没有已登记连接。请先按现有连接流程配置只读账号，此处不新建连接或保存凭据。</p>}
      <label className="block text-sm font-medium">已登记数据库连接<select className={fields} value={connectionId} onChange={event => { changed(); setConnectionId(event.target.value); setSchema(null); setTable(""); setColumns([]); setFilterField(""); }}><option value="">请选择本人连接</option>{connections.map(item => <option key={item.connection_id} value={item.connection_id}>{item.name} · {item.dialect}</option>)}</select></label>
      {schema && <><label className="block text-sm font-medium">读取哪张表<select className={fields} value={table} onChange={event => { changed(); setTable(event.target.value); setColumns([]); setFilterField(""); }}><option value="">请选择表</option>{tables.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label><p className="text-xs text-muted-foreground">当前选择默认分区内有主键的表，按主键分批读取；不支持任意查询或写操作。</p></>}
      {selectedTable && <><fieldset className="space-y-2"><legend className="text-sm">读取字段（不勾选表示全部）</legend>{selectedTable.columns.map(item => <label key={item.name} className="mr-3 inline-flex items-center gap-2 text-sm"><input type="checkbox" aria-label={`读取字段 ${item.name}`} checked={columns.includes(item.name)} onChange={event => { changed(); setColumns(value => event.target.checked ? [...value, item.name] : value.filter(name => name !== item.name)); }} />{item.name}</label>)}</fieldset><label className="block text-sm">筛选字段<select className={fields} value={filterField} onChange={event => { changed(); setFilterField(event.target.value); }}><option value="">不筛选</option>{selectedTable.columns.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label>{filterField && <label className="block text-sm">等于这个值<input className={fields} value={filterValue} onChange={event => { changed(); setFilterValue(event.target.value); }} /></label>}{!selectedTable.primary_key.length && <p role="alert">此表没有主键，当前界面无法安全分批读取，请选择其他表。</p>}</>}
    </>}
    <label className="block text-sm">本次读取用途<textarea className={fields} maxLength={500} value={readPurpose} onChange={event => setReadPurpose(event.target.value)} /></label><p className="text-xs text-muted-foreground">仅说明本次读取用途，最多500字；完整任务要求保留在原任务中。当前 {readPurpose.length} 字。</p>
    </fieldset>
    {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    {!scope && <button className="rounded-lg border px-3 py-2 text-sm" disabled={busy || (mode === "http_api" ? !url.trim() || !validPagination : catalogLoading || Boolean(catalogError) || !selectedTable?.primary_key.length)} onClick={() => void run(async identity => { const resolved = await resolveConnectorSource(selection()); if (identity === request.current) setScope(resolved); })}>{busy ? "正在核对范围…" : "核对读取范围"}</button>}
    {scope && <div className="space-y-2 text-sm"><p>{unknown ? "沿用上次核对版本，结果待确认" : "连接版本已锁定"}</p><ConnectorScopeFacts scope={scope} /><p className="text-xs text-muted-foreground">每段最多10批，总上限100原件或16 MiB；仅JSON/JSONL，超时10秒。</p>
      {unknown && <><p role="status">读取结果尚未确认；不会创建第二个请求。</p><button className="rounded-lg border px-3 py-2" disabled={busy || stopBusy} onClick={() => void run(async identity => { if (frozen.current?.attemptId) { const value = await getConnectorAcquisition(frozen.current.attemptId); if (identity === request.current) accept(value); } else await read(identity); })}>确认原读取结果</button></>}
      {!snapshot && !attempt && !unknown && <button className="rounded-lg bg-primary px-3 py-2 text-primary-foreground" disabled={busy || stopBusy || !readPurpose.trim() || readPurpose.length > 500} onClick={() => void run(identity => read(identity))}>{busy ? "正在读取资料…" : "读取并冻结资料"}</button>}
      {!unknown && attempt?.status === "acquiring" && attempt.error_code === "connector_checkpoint_ready" && <><p role="status">已暂停在安全断点</p><p>本段已保存，继续会沿同一连接版本读取余下资料，不会重复加入已读批次。</p><button className="rounded-lg border px-3 py-2" disabled={busy || stopBusy} onClick={() => void run(identity => read(identity, true))}>继续本次读取</button></>}
      {!unknown && attempt?.status === "acquiring" && <button className="rounded-lg border px-3 py-2" disabled={stopBusy} onClick={() => void stop()}>停止本次读取</button>}
      {attempt?.status === "cancelling" && <><p role="status">停止尚未确认</p><p>连接仍在收尾，本次资料不能加入任务。</p><button className="rounded-lg border px-3 py-2" disabled={busy || stopBusy} onClick={() => void run(async identity => { const value = await getConnectorAcquisition(attempt.attempt_id); if (identity === request.current) accept(value); })}>检查停止状态</button></>}
      {attempt?.status === "canceled" && <p role="status">已停止读取</p>}
      {(attempt?.status === "canceled" || attempt?.status === "failed") && <button className="rounded-lg border px-3 py-2" onClick={reset}>重新选择范围</button>}
    </div>}
    {snapshot && <section aria-label="连接资料预览" className="space-y-3 text-sm"><p>已冻结 {snapshot.artifacts.length} 个原件；不保证整个数据库一致快照。</p>{snapshot.artifacts.map(item => <article key={item.artifact_id}><p>{item.title}</p><p className="break-all text-xs text-muted-foreground">{item.read_at} · SHA256 {item.content_sha256}</p><pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded border p-2">{item.text_preview}</pre></article>)}<button className="rounded-lg bg-primary px-3 py-2 text-primary-foreground" onClick={() => { try { assertStored(); if (onSelected(snapshot) && reset()) onOpenChange(false); } catch (reason) { setError(reason instanceof Error ? reason.message : "资料尚未加入，请保留原请求。"); } }}>加入当前任务</button></section>}
    <Dialog.Close className="rounded-lg border px-3 py-2 text-sm">返回原任务</Dialog.Close>
  </Dialog.Content></Dialog.Portal></Dialog.Root>;
}
