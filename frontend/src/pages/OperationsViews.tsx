import { useState, type ReactNode } from "react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { AlertCircle, CheckCircle2, ChevronRight, Clock3, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Pagination } from "@/components/ui/pagination";
import type { Entry, Summary } from "./Operations";

export const resultLabels: Record<string, string> = { success: "成功", failure: "失败", unknown: "结果未知" };
export const sourceLabels: Record<string, string> = { direct: "直接访问", internal: "站内跳转", external: "外部来源", unknown: "来源未知" };
export const formatTime = (value: string | number) => new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", dateStyle: "short", timeStyle: "medium", hour12: false }).format(new Date(typeof value === "number" ? value * 1000 : value));

export function ResultStatus({ result }: { result: string }) {
  const Icon = result === "success" ? CheckCircle2 : result === "failure" ? AlertCircle : Clock3;
  return <span className={`ops-status ${result === "failure" ? "text-destructive" : result === "success" ? "text-primary" : "text-muted-foreground"}`}><Icon size={14} />{resultLabels[result] || "结果未知"}</span>;
}

export function Stat({ label, value, note, onClick, danger }: { label: string; value: ReactNode; note?: string; onClick?: () => void; danger?: boolean }) {
  const content = <><span>{label}</span><strong className={danger ? "text-destructive" : ""}>{value}</strong><small>{note || "所选时间范围"}</small></>;
  return onClick ? <button type="button" onClick={onClick} className="ops-stat">{content}</button> : <div className="ops-stat">{content}</div>;
}

export function Empty({ text = "没有符合条件的已采集记录" }: { text?: string }) {
  return <div className="ops-empty"><Search size={22} /><p>{text}</p></div>;
}

export function EventsTable({ items, kind, compact = false, onUser, onDetail }: { items: Entry[]; kind: string; compact?: boolean; onUser: (id: string, name: string) => void; onDetail: (id: string) => void }) {
  const headings = kind === "visit" ? ["时间", "用户", "页面 / 功能", "来源", "详情"] : kind === "login" ? ["登录时间", "用户", "登录方式", "IP 网段 / 设备", "登录结果", "详情"] : ["时间", "用户", "模块 / 操作", "操作对象", "结果", "详情"];
  return <div className={`ops-table-scroll ${compact ? "ops-table-compact" : ""}`} tabIndex={0} role="region" aria-label="日志表格，可滚动查看">
    <table className="ops-table"><caption className="sr-only">当前筛选范围内的运营日志</caption><thead><tr>{headings.map(name => <th scope="col" key={name}>{name}</th>)}</tr></thead>
      <tbody>{items.map(item => <tr key={item.event_id}>
        <td className="tabular-nums">{formatTime(item.occurred_at)}</td>
        <td><Button variant="link" className="ops-text" disabled={!item.actor_id} onClick={() => item.actor_id && onUser(item.actor_id, item.actor_name)}>{item.actor_name}</Button></td>
        <td>{kind === "login" ? item.action : kind === "visit" ? item.module : <><strong>{item.action}</strong><small>{item.module}</small></>}</td>
        <td>{kind === "visit" ? sourceLabels[item.source] || "来源未知" : kind === "login" ? <>{item.ip_mask || "未采集"}<small>{item.device}</small></> : <span className="ops-object">{item.object_ref || "未记录"}</span>}</td>
        {kind !== "visit" && <td><ResultStatus result={item.result} /></td>}
        <td><Button variant="link" className="ops-text" onClick={() => onDetail(item.event_id)}>查看详情<ChevronRight size={14} /></Button></td>
      </tr>)}</tbody>
    </table>{!items.length && <Empty />}
  </div>;
}

export function Trend({ rows, onDay }: { rows: Summary["trend"]; onDay: (bucket: string) => void }) {
  const [page, setPage] = useState(1);
  const [hover, setHover] = useState<string | null>(null);
  const totalPages = Math.max(1, Math.ceil(rows.length / 14));
  const current = Math.min(page, totalPages);
  const points = rows.slice((current - 1) * 14, current * 14);
  const max = Math.max(5, ...points.flatMap(point => [point.pv, point.uv]));
  const x = (i: number) => 36 + i * 624 / Math.max(1, points.length - 1);
  const y = (value: number) => 172 - value / max * 148;
  const selected = points.find(point => point.bucket === hover);
  return <>
    <div className="ops-legend"><span className="text-primary">● 访问用户 UV</span><span className="ops-series-pv">━ 页面访问 PV</span></div>
    {!points.length ? <Empty text="暂无已采集的访问记录" /> : <>
      <div className="ops-chart-readout" aria-live="polite">{selected ? `${selected.bucket} · PV ${selected.pv} · UV ${selected.uv}` : "悬停或聚焦日期查看数值，点击查看明细"}</div>
      <svg className="ops-trend" viewBox="0 0 680 190" preserveAspectRatio="none" role="img" aria-label="页面访问与访问用户趋势，数值可通过下方日期按钮查看">
        {[0, 1, 2, 3, 4].map(n => <g key={n}><line x1="36" x2="660" y1={y(max * n / 4)} y2={y(max * n / 4)} className="ops-gridline" /><text x="2" y={y(max * n / 4) + 4} className="ops-axis">{Math.round(max * n / 4)}</text></g>)}
        {(["pv", "uv"] as const).map(key => <g key={key} className={key === "pv" ? "ops-series-pv" : "text-primary"}><polyline fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray={key === "uv" ? "5 3" : undefined} points={points.map((point, i) => `${x(i)},${y(point[key])}`).join(" ")} />{points.map((point, i) => <circle key={point.bucket} cx={x(i)} cy={y(point[key])} r="3" fill="currentColor" />)}</g>)}
      </svg>
      <div className="ops-chart-dates">{points.map(point => <button type="button" key={point.bucket} aria-label={`${point.bucket}，PV ${point.pv}，UV ${point.uv}，查看明细`} onFocus={() => setHover(point.bucket)} onMouseEnter={() => setHover(point.bucket)} onClick={() => onDay(point.bucket)}>{point.bucket.length > 10 ? point.bucket.slice(11) : point.bucket.slice(5).replace("-", "/")}</button>)}</div>
    </>}
    {totalPages > 1 && <nav aria-label="趋势时间分页"><Pagination page={current} totalPages={totalPages} total={rows.length} onChange={setPage} /></nav>}
  </>;
}

export function Ranking({ items, onSelect }: { items: Summary["modules"]; onSelect: (module: string) => void }) {
  const max = Math.max(1, ...items.map(item => item.pv));
  return <section className="ops-panel"><div className="ops-panel-heading"><h2>功能使用热度</h2><span className="ops-muted">页面访问次数</span></div>
    {!items.length ? <Empty text="暂无已采集的功能访问数据" /> : <>
      <Tooltip.Provider delayDuration={150}><div className="ops-heatmap" role="group" aria-label="功能热度矩阵">
        {items.map(item => <Tooltip.Root key={item.module}><Tooltip.Trigger asChild>
          <button type="button" className="ops-heatmap-tile" data-heat={Math.ceil(item.pv / max * 4)} aria-label={`${item.module}，${item.pv} 次访问，${item.uv} 位用户，查看明细`} onClick={() => onSelect(item.module)}>
            <strong>{item.module}</strong><span><b>{item.pv.toLocaleString()}</b> 次</span>
          </button>
        </Tooltip.Trigger><Tooltip.Portal><Tooltip.Content side="top" sideOffset={6} className="z-50 rounded-md border bg-card px-3 py-2 text-xs text-foreground shadow-md">
          <strong>{item.module}</strong><p>访问次数：{item.pv.toLocaleString()} 次</p><p>访问用户：{item.uv.toLocaleString()} 人</p><p className="mt-1 text-muted-foreground">点击查看访问明细</p>
        </Tooltip.Content></Tooltip.Portal></Tooltip.Root>)}
      </div></Tooltip.Provider>
      <div className="ops-heatmap-legend"><span>访问热度</span><span>低</span><span className="ops-heatmap-scale" aria-hidden="true">{[0, 1, 2, 3, 4].map(level => <i key={level} data-heat={level} />)}</span><span>高</span><span className="ml-auto">点击色块查看明细</span></div>
    </>}
  </section>;
}

export function UserTable({ users, onUser }: { users: Summary["users"]; onUser: (id: string, name: string) => void }) {
  const [page, setPage] = useState(1), [size, setSize] = useState(10);
  const pages = Math.max(1, Math.ceil(users.length / size)), current = Math.min(page, pages);
  return <section className="ops-panel ops-flush"><div className="ops-panel-heading"><h2>用户使用明细</h2><span className="ops-muted">按访问次数展示前 100 位</span></div>
    <div key={`${current}:${size}`} className="ops-table-scroll" tabIndex={0} role="region" aria-label="用户明细，可滚动查看"><table className="ops-table"><caption className="sr-only">用户使用明细</caption><thead><tr>{["用户", "页面访问", "关键操作", "成功登录", "最近活动", "查看"].map(name => <th scope="col" key={name}>{name}</th>)}</tr></thead><tbody>{users.slice((current - 1) * size, current * size).map(user => <tr key={user.actor_id}><td><Button variant="link" className="ops-text" onClick={() => onUser(user.actor_id, user.actor_name)}>{user.actor_name}</Button></td><td>{user.pv}</td><td>{user.actions}</td><td>{user.logins}</td><td>{formatTime(user.last_active)}</td><td><Button variant="link" className="ops-text" onClick={() => onUser(user.actor_id, user.actor_name)}>行为时间线<ChevronRight size={14} /></Button></td></tr>)}</tbody></table>{!users.length && <Empty text="暂无已采集的用户活动" />}</div>
    <nav className="ops-pager" aria-label="用户明细分页"><Pagination pageSizeOptions={[10, 20, 50, 100]} page={current} totalPages={pages} total={users.length} pageSize={size} onChange={setPage} onPageSizeChange={value => { setSize(value); setPage(1); }} /></nav>
  </section>;
}
