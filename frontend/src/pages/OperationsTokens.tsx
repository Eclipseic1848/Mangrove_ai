import { Fragment, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Pagination } from '@/components/ui/pagination';
import { api, authenticatedFetch, getSessionState, readAuthenticatedBlob } from '@/lib/api';
import { Stat, formatTime } from './OperationsViews';

type Counts = { requests: number; local_requests?: number; input_tokens: number | null; output_tokens: number | null; total_tokens: number | null; cost: string | null; priced_requests: number; unknown_requests: number; last_used: string };
type Model = Counts & { model: string; api_format: string };
type User = Counts & { user_id: string; username: string; name: string; models: Model[] };
type Price = { model: string; currency: string; input: string; output: string; source: string; note?: string };
type Data = { items: User[]; total: number; page: number; page_size: number; currency: string; summary: Counts & { users: number }; coverage_note: string; pricing: { version: string; usd_to_cny: string; exchange_note: string; notice: string; models: Price[]; unpriced_models?: string[] } };
const control = 'h-10 min-w-0 rounded-md border bg-background px-3 text-sm dark:[color-scheme:dark]';
const number = (value: number | null) => value == null ? '未知' : value.toLocaleString('zh-CN');

export function OperationsTokens({ start, end, invalid, revision, actorId, actorRole }: { start: string; end: string; invalid: boolean; revision: number; actorId: string; actorRole: string }) {
  const [filters, setFilters] = useState({ search: '', model: '', api_format: '', sort: 'total_tokens', direction: 'desc', currency: 'CNY', page: 1, page_size: 10 });
  const [search, setSearch] = useState(''), [composing, setComposing] = useState(false);
  const [data, setData] = useState<Data | null>(null), [loading, setLoading] = useState(true), [error, setError] = useState('');
  const [expanded, setExpanded] = useState(''), [format, setFormat] = useState('csv'), [exporting, setExporting] = useState(false);
  const exportRequest = useRef<AbortController | null>(null);
  const current = () => getSessionState().user?.user_id === actorId && getSessionState().user?.role === actorRole;
  const query = JSON.stringify({ start, end, ...filters });
  useEffect(() => { setFilters(value => ({ ...value, page: 1 })); }, [start, end]);
  useEffect(() => {
    if (composing || search === filters.search) return;
    const timer = setTimeout(() => setFilters(value => ({ ...value, search, page: 1 })), 300);
    return () => clearTimeout(timer);
  }, [search, composing, filters.search]);
  useEffect(() => {
    if (invalid) { setLoading(false); setData(null); return; }
    const controller = new AbortController();
    setLoading(true); setError(''); setData(null); setExpanded('');
    const timer = setTimeout(() => { controller.abort(); if (current()) { setError('统计请求超时，请缩短时间范围或刷新重试。'); setLoading(false); } }, 20000);
    api.post('/api/operations/tokens/query', JSON.parse(query), {}, controller.signal)
      .then((result: Data) => { if (!controller.signal.aborted && current()) setData(result); })
      .catch(() => { if (!controller.signal.aborted && current()) setError('统计暂不可用，请检查权限、价格配置或缩小筛选范围后重试。'); })
      .finally(() => { clearTimeout(timer); if (!controller.signal.aborted && current()) setLoading(false); });
    return () => { clearTimeout(timer); controller.abort(); };
  }, [query, revision, invalid]);
  useEffect(() => () => exportRequest.current?.abort(), []);
  const change = (key: string, value: string) => setFilters(previous => ({ ...previous, [key]: value, page: 1 }));
  const cost = (row: Counts) => row.requests > 0 && row.local_requests === row.requests ? '无需计价' : row.cost == null ? '未估算' : `${filters.currency === 'CNY' ? '¥' : '$'}${row.cost}`;
  const coverage = (row: Counts) => `云端已估价 ${row.priced_requests}/${row.requests - (row.local_requests || 0)} 次；本地 ${row.local_requests || 0} 次仅计Token`;
  const download = async () => {
    if (exportRequest.current || invalid) return;
    const controller = new AbortController(); exportRequest.current = controller; setExporting(true); setError('');
    const timer = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await authenticatedFetch('/api/operations/tokens/export', { method: 'POST', signal: controller.signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ filters: JSON.parse(query), format }) });
      if (!response.ok) throw new Error('export');
      const blob = await readAuthenticatedBlob(response);
      if (!controller.signal.aborted && current()) {
        const url = URL.createObjectURL(blob), link = document.createElement('a');
        link.href = url; link.download = `Mangrove-Token-${start}-${end}.${format}`; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch { if (current()) setError('导出未完成，请稍后重试；数据不会被修改。'); }
    finally { clearTimeout(timer); exportRequest.current = null; if (current()) setExporting(false); }
  };
  return <section data-guide-loading={loading || !data ? "true" : undefined} aria-label="Token统计" className="min-w-0">
    <div className="ops-panel-heading"><h2>分用户Token消耗</h2><span className="ops-muted">仅统计已记录的模型调用</span></div>
    <div className="ops-filters">
      <label>用户<input aria-label="搜索用户" className={control} placeholder="用户名、昵称或ID" value={search} maxLength={80} onChange={event => setSearch(event.target.value)} onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)} /></label>
      <label>模型<input aria-label="筛选模型" className={control} placeholder="完整模型名称" maxLength={200} value={filters.model} onChange={event => change('model', event.target.value)} /></label>
      <label>接口<input aria-label="筛选接口" className={control} placeholder="全部接口" maxLength={80} value={filters.api_format} onChange={event => change('api_format', event.target.value)} /></label>
      <label>排序<select className={control} value={filters.sort} onChange={event => change('sort', event.target.value)}><option value="total_tokens">总Token</option><option value="cost">参考成本</option><option value="requests">请求次数</option><option value="last_used">最后使用时间</option></select></label>
      <label>顺序<select className={control} value={filters.direction} onChange={event => change('direction', event.target.value)}><option value="desc">从高到低</option><option value="asc">从低到高</option></select></label>
      <label>币种<select className={control} value={filters.currency} onChange={event => change('currency', event.target.value)}><option value="CNY">人民币</option><option value="USD">美元</option></select></label>
      <label>导出格式<select className={control} value={format} onChange={event => setFormat(event.target.value)}><option value="csv">CSV</option><option value="xlsx">Excel</option></select></label>
      <Button disabled={loading || exporting || !data} onClick={download}>{exporting ? '正在导出…' : '导出用量'}</Button>
    </div>
    {error && <p role="alert" className="my-3 text-sm text-destructive">{error}</p>}
    {loading && <p role="status" className="py-10 text-center text-sm">正在读取Token统计…</p>}
    {data && <>
      <div className="ops-stats"><Stat label="使用用户数" value={number(data.summary.users)} /><Stat label="已记录Token" value={number(data.summary.total_tokens)} note={`${data.summary.unknown_requests} 次请求未记录完整用量`} /><Stat label="参考成本" value={cost(data.summary)} note={coverage(data.summary)} /><Stat label="总请求次数" value={number(data.summary.requests)} /></div>
      <p className="ops-muted my-3">{data.coverage_note} {data.pricing.notice}</p>
      <div className="ops-panel ops-flush"><div className="ops-table-scroll" role="region" aria-label="用户用量，可横向滚动" tabIndex={0}><table className="ops-table" aria-label="用户Token统计"><thead><tr>{['用户', '请求次数', '输入Token', '输出Token', '总Token', '参考成本', '最后使用时间', '明细'].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead><tbody>{data.items.map(user => <Fragment key={user.user_id}>
        <tr><td><strong>{user.name}</strong><small>{user.username} · {user.user_id}</small></td><td>{number(user.requests)}</td><td>{number(user.input_tokens)}</td><td>{number(user.output_tokens)}</td><td>{number(user.total_tokens)}<small>{user.unknown_requests ? `${user.unknown_requests} 次未知` : ''}</small></td><td>{cost(user)}<small>{coverage(user)}</small></td><td>{formatTime(user.last_used)}</td><td><Button variant="link" aria-expanded={expanded === user.user_id} aria-label={`查看${user.name}的模型明细`} onClick={() => setExpanded(expanded === user.user_id ? '' : user.user_id)}>模型明细</Button></td></tr>
        {expanded === user.user_id && <tr><td colSpan={8}><table className="ops-table" aria-label="模型消耗明细"><thead><tr>{['模型 / 接口', '请求次数', '输入Token', '输出Token', '总Token', '参考成本'].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead><tbody>{user.models.map((model, index) => <tr key={index}><td>{model.model}<small>{model.api_format}</small></td><td>{model.requests}</td><td>{number(model.input_tokens)}</td><td>{number(model.output_tokens)}</td><td>{number(model.total_tokens)}</td><td>{cost(model)}</td></tr>)}</tbody></table></td></tr>}
      </Fragment>)}</tbody></table>{!data.items.length && <p className="py-12 text-center text-sm text-muted-foreground">当前范围暂无已记录用量</p>}</div>
      <nav className="ops-pager" aria-label="Token统计分页"><Pagination page={data.page} pageSize={filters.page_size} pageSizeOptions={[10, 20, 50, 100]} total={data.total} totalPages={Math.max(1, Math.ceil(data.total / filters.page_size))} onChange={page => setFilters(previous => ({ ...previous, page }))} onPageSizeChange={page_size => setFilters(previous => ({ ...previous, page_size, page: 1 }))} /></nav></div>
      <details className="ops-panel mt-4 text-sm"><summary className="cursor-pointer">参考价格与统计说明</summary><p className="my-3">价格版本 {data.pricing.version} · 1 USD = {data.pricing.usd_to_cny} CNY。{data.pricing.exchange_note}</p><p className="my-3">本地模型仅统计Token，无需计价。云端模型缺少报价或输入／输出用量时不估价，不代表免费；报价仅限平台支持的型号。</p><div className="overflow-x-auto"><table className="ops-table"><thead><tr><th>模型</th><th>每百万输入</th><th>每百万输出</th><th>说明</th></tr></thead><tbody>{data.pricing.models.map((price, i) => <tr key={i}><td><a className="text-primary underline" href={price.source} target="_blank" rel="noreferrer">{price.model}</a></td><td>{price.input} {price.currency}</td><td>{price.output} {price.currency}</td><td>{price.note || '标准按量参考价'}</td></tr>)}</tbody></table></div>{!!data.pricing.unpriced_models?.length && <p className="my-3 break-words">以下平台型号尚未配置参考价格：{data.pricing.unpriced_models.join('、')}。不套用其他型号价格。</p>}</details>
    </>}
  </section>;
}
