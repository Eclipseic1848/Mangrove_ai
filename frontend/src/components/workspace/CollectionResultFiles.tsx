import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, downloadFile } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { WorkbookPreview } from "./WorkbookPreview";
import { CollectionCountSummary, type CollectionCounts } from "./CollectionProgress";

export type CollectionFile = { name: string; url: string; inProgress?: boolean };
type JsonPreview = {
  kind: "json"; text: string; truncated: boolean;
  in_progress?: boolean;
  benefit_table: { rows: Array<Record<string, unknown>>; total_rows: number; truncated: boolean } | null;
  candidate_counts?: CollectionCounts;
  candidate_summary?: { rows: Array<{ index: number; source_title: string; note_url: string | null; status: string; reason: string; reason_code?: string; explanation?: string | null; evidence?: Array<{ source_id: string; quote: string }> }>; total_rows: number; truncated: boolean };
  outcome: Record<string, unknown> | null;
};

function sourceUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try { const url = new URL(value); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
}

function candidateReason(value: string): string {
  const reasons: Record<string, string> = {
    audience_mismatch: "适用客群与任务要求不符", expired_rule: "原文权益规则已过期", credit_card_only: "仅涉及信用卡权益",
    publication_outside_scope: "发布时间不在指定范围内", publication_unknown: "发布时间尚未确认",
    content_type_mismatch: "笔记类型与任务要求不符", content_type_unknown: "笔记类型尚未确认",
    unrelated: "与任务主题无关", relevance_unknown: "与任务主题的相关性尚未确认",
    discovery_outcome_unknown: "本批检索结果尚未确认", interrupted_discovery_outcome_unknown: "中断前的检索结果尚未确认",
    screen_outcome_unknown: "筛选结果尚未确认", extract_outcome_unknown: "权益提取结果尚未确认",
    images_outcome_unknown: "图片读取结果尚未确认", comments_outcome_unknown: "评论读取结果尚未确认",
    comments_incomplete: "评论尚未读取完整", image_incomplete: "图片证据尚未完整读取",
    interrupted_operation_outcome_unknown: "中断前的处理结果尚未确认",
  };
  return reasons[value] ?? (/^[a-z][a-z0-9_]*$/.test(value) ? "筛选原因尚待核对，详细记录可下载 JSON" : value);
}

function previewUrl(file: CollectionFile): string | null {
  const match = /^\/api\/downloads\/([^/?#]+)\/([^?#]+)(?:\?revision=([1-9]\d{0,8}))?$/.exec(file.url);
  if (!match) return null;
  try {
    const taskId = decodeURIComponent(match[1]), path = decodeURIComponent(match[2]);
    if (!taskId || /[\\/\0]/.test(taskId) || !path || /[\\\0]/.test(path) || [taskId, ...path.split("/")].some(part => !part || part === "." || part === "..")) return null;
    const params = new URLSearchParams({ preview: path });
    if (match[3]) params.set("revision", match[3]);
    return `/api/downloads/${encodeURIComponent(taskId)}?${params}`;
  } catch { return null; }
}

export function hasCollectionPreview(files: CollectionFile[]) {
  return files.some(file => /\.(json|xlsx)$/i.test(file.name) && previewUrl(file));
}

function valueText(value: unknown): string {
  return (typeof value === "string" && value.trim()) || typeof value === "number" ? String(value) : "未提供";
}

function Fields({ row, fields }: { row: Record<string, unknown>; fields: Array<[string, string]> }) {
  return <dl className="space-y-1">{fields.map(([key, label]) => <div key={key}><dt className="inline font-medium">{label}：</dt><dd className="inline">{valueText(row[key])}</dd></div>)}</dl>;
}

export function CollectionResultFiles({ files }: { files: CollectionFile[] }) {
  const [error, setError] = useState("");
  const button = "rounded-lg border px-3 py-2 text-xs hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring";
  return <section aria-label="已采集资料下载" className="min-w-0 space-y-3">
    <div className="flex flex-wrap gap-2">{files.filter(file => previewUrl(file)).map(file => <button key={file.url} type="button" className={button} onClick={() => void downloadFile(file.url, file.name).catch(reason => setError(reason instanceof Error ? reason.message : "下载失败"))}>下载 {file.name}</button>)}</div>
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
  </section>;
}

export function CollectionResultPreview({ files, ownerId, onClose, expanded, onToggleExpand }: { files: CollectionFile[]; ownerId: string; onClose: () => void; expanded?: boolean; onToggleExpand?: () => void }) {
  const usable = files.map(file => ({ ...file, previewUrl: previewUrl(file) })).filter(file => file.previewUrl);
  const json = usable.find(file => /\.json$/i.test(file.name));
  const xlsx = usable.find(file => /\.xlsx$/i.test(file.name));
  const [view, setView] = useState<"benefits" | "json" | "xlsx" | null>(null);
  const result = useQuery<JsonPreview>({
    queryKey: ["collection-file-preview", ownerId, json?.previewUrl],
    queryFn: ({ signal }) => api.get(json!.previewUrl!, { signal }),
    enabled: Boolean(json), retry: false, staleTime: Infinity,
    refetchOnWindowFocus: false, refetchOnReconnect: false,
    // 同一资料的新快照保留上一版；跨账号或文件不能借用旧正文。
    placeholderData: (previousData, previousQuery) => previousQuery?.queryKey[1] === ownerId && String(previousQuery.queryKey[2]).split("&revision=")[0] === json?.previewUrl?.split("&revision=")[0] ? previousData : undefined,
  });
  const table = result.data?.benefit_table;
  const candidates = result.data?.candidate_summary;
  const partialCount = table?.rows.filter(row => row.partial_evidence === true || row.source_status === "review_required").length ?? 0;
  const selectedView = view ?? (table ? "benefits" : json ? "json" : "xlsx");
  const button = "rounded-lg border px-3 py-2 text-xs hover:bg-muted aria-pressed:border-primary/60 aria-pressed:bg-primary/10 aria-pressed:text-primary focus-visible:ring-2 focus-visible:ring-ring";
  const text = result.data?.text ?? "";
  const inProgress = json?.inProgress ?? (result.data?.in_progress === true || json?.name === "data-in-progress.json");
  const savedPartial = json?.name === "data-in-progress.json" && json.inProgress === false;
  return <section aria-label="已采集资料预览" className="flex h-full min-h-0 min-w-0 flex-col">
    <div className="flex shrink-0 flex-wrap items-center gap-2 border-b p-3">
      <h2 className="mr-auto text-sm font-semibold">{inProgress ? "采集中资料" : "已采集资料"}</h2>
      {onToggleExpand && <button type="button" className={button} aria-expanded={expanded} onClick={onToggleExpand}>{expanded ? "恢复分栏" : "展开预览"}</button>}
      <button type="button" className={button} onClick={onClose}>关闭资料预览</button>
    </div>
    <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto p-3">
      <div className="mb-3 shrink-0"><CollectionResultFiles files={files} /></div>
      <p className="mb-3 shrink-0 text-xs text-muted-foreground">{inProgress ? "已保存部分资料 · 数量和证据继续更新，尚非正式交付。" : savedPartial ? "已保存的处理中资料 · 本次执行已停止或未完成，尚非正式交付。" : "已采集资料快照 · 内容与证据仍待核对，尚非正式交付。"}预览不会重新采集或接受初稿。</p>
      {result.isFetching && result.data && <p role="status" className="mb-2 shrink-0 text-xs text-muted-foreground">正在更新已保存资料…</p>}
      {result.data?.candidate_counts && <div className="mb-3 shrink-0"><CollectionCountSummary counts={result.data.candidate_counts} /></div>}
      {partialCount > 0 && <p className="mb-3 shrink-0 text-xs text-amber-800 dark:text-amber-200">待核对资料仅展示已有证据，不计入完整入选篇数；缺失图片或字段仍需核对。</p>}
      {candidates && <details className="mb-3 shrink-0 rounded-lg border">
        <summary className="cursor-pointer px-3 py-2 text-xs font-medium hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring">候选笔记与筛选说明 · {candidates.total_rows} 篇</summary>
        <div aria-label="候选笔记与筛选说明" tabIndex={0} className="max-h-52 overflow-auto border-t px-3 focus-visible:ring-2 focus-visible:ring-ring">
          <ol className="divide-y text-xs">{candidates.rows.map(row => {
            const url = sourceUrl(row.note_url);
            const label = ({ selected: "完整入选", excluded: "已排除", review_required: "待核对", unprocessed: "未完成" } as Record<string, string>)[row.status] ?? "未完成";
            return <li key={row.index} className="min-w-0 py-3">
              <div className="mb-1 flex flex-wrap items-center gap-2"><Badge variant={row.status === "review_required" ? "warning" : row.status === "selected" ? "default" : "outline"} className={row.status === "review_required" ? "text-amber-800 dark:text-amber-200" : undefined}>{label}</Badge>
                {url ? <a href={url} target="_blank" rel="noopener noreferrer" className="min-w-0 break-words text-primary underline focus-visible:ring-2 focus-visible:ring-ring">{row.source_title || "查看原文"}</a> : <span className="min-w-0 break-words">{row.source_title || `候选笔记 ${row.index}`}</span>}</div>
              <p className="whitespace-pre-wrap break-words text-muted-foreground">{row.reason ? candidateReason(row.reason) : row.status === "selected" ? "已完成范围与证据检查" : "尚未提供筛选说明"}</p>
              {row.explanation && <p className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">{row.explanation}</p>}
              {row.evidence?.length ? <details className="mt-2">
                <summary className="cursor-pointer text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring">查看判断依据</summary>
                <div className="mt-2 max-h-40 space-y-2 overflow-auto rounded bg-muted/40 p-2">{row.evidence.map((item, index) => <blockquote key={index} className="whitespace-pre-wrap [overflow-wrap:anywhere]">{item.quote}</blockquote>)}</div>
              </details> : null}
            </li>;
          })}</ol>
          {!candidates.rows.length && <p className="py-3 text-muted-foreground">尚未找到候选笔记；可以下载资料查看执行缺口。</p>}
          {candidates.truncated && <p className="py-2 text-muted-foreground">仅展示前 {candidates.rows.length} 篇，完整候选与筛选说明请下载 JSON。</p>}
        </div>
      </details>}
      <div role="group" aria-label="资料预览方式" className="mb-3 flex shrink-0 flex-wrap gap-2">
        {table && <button type="button" className={button} aria-pressed={selectedView === "benefits"} onClick={() => setView("benefits")}>权益表</button>}
        {json && <button type="button" className={button} aria-pressed={selectedView === "json"} onClick={() => setView("json")}>JSON 原始数据</button>}
        {xlsx && <button type="button" className={button} aria-pressed={selectedView === "xlsx"} onClick={() => setView("xlsx")}>Excel 工作表</button>}
      </div>
      {selectedView === "xlsx" && xlsx ? <div className="min-h-0 min-w-0 flex-1"><WorkbookPreview key={`${ownerId}:${xlsx.previewUrl}`} previewUrl={xlsx.previewUrl!} /></div>
        : result.isError ? <div role="alert" className="text-sm text-destructive">{result.error.message}<button type="button" className={`${button} ml-2`} onClick={() => void result.refetch()}>重新读取预览</button></div>
          : !result.data ? <p role="status" className="text-sm">正在读取已采集资料…</p>
            : selectedView === "benefits" && table ? <>
              <p className="mb-2 shrink-0 text-xs text-muted-foreground">{partialCount > 0 ? "从完整入选及待核对笔记已有证据提取的权益" : "从已入选笔记提取的权益"}，共 {table.total_rows} 条；缺失字段显示“未提供”，不能据此推断无条件或永久有效。</p>
              <div tabIndex={0} aria-label="权益表内容" className="min-h-0 flex-1 overflow-auto focus-visible:ring-2 focus-visible:ring-ring">
                <table className="w-full min-w-[40rem] table-fixed border-separate border-spacing-0 text-left text-xs"><thead><tr>{["权益内容", "条件", "期限", "原文链接"].map(label => <th key={label} scope="col" className="sticky top-0 z-10 border bg-muted p-2">{label}</th>)}</tr></thead>
                  <tbody>{table.rows.map((row, index) => <tr key={`${String(row.note_id ?? "note")}:${index}`}>
                    <td className="whitespace-pre-wrap border p-2 align-top [overflow-wrap:anywhere]">{(row.partial_evidence === true || row.source_status === "review_required") && <Badge variant="warning" className="mb-2 text-amber-800 dark:text-amber-200">部分证据 · 待核对</Badge>}<Fields row={row} fields={[["benefit_name", "权益"], ["bank", "银行"], ["benefit_category", "类别"]]} /></td>
                    <td className="whitespace-pre-wrap border p-2 align-top [overflow-wrap:anywhere]"><Fields row={row} fields={[["customer_segment", "适用客群"], ["quota", "额度/次数"], ["threshold", "门槛"], ["point_system", "积分体系"], ["point_cost", "兑换积分"], ["point_earn_rule", "积分获取规则"], ["app_path", "办理路径"]]} /></td>
                    <td className="whitespace-pre-wrap border p-2 align-top [overflow-wrap:anywhere]"><Fields row={row} fields={[["activity_period", "活动期"], ["registration_period", "报名期"], ["redemption_period", "领取/兑换期"], ["usage_period", "使用期"], ["valid_from", "有效开始"], ["valid_to", "有效结束"]]} /></td>
                    <td className="border p-2 align-top [overflow-wrap:anywhere]">{sourceUrl(row.note_url) ? <a href={sourceUrl(row.note_url)!} target="_blank" rel="noopener noreferrer" className="text-primary underline">{valueText(row.source_title) === "未提供" ? "查看原文" : valueText(row.source_title)}</a> : "未提供"}<p className="mt-2 whitespace-pre-wrap text-muted-foreground">{typeof row.review_reason === "string" && row.review_reason.trim() ? candidateReason(row.review_reason) : "原文与提取字段仍待核对"}</p></td>
                  </tr>)}</tbody>
                </table>
                {!table.rows.length && <p className="p-3 text-sm">入选笔记未提供可展示的结构化权益；可以查看 JSON 或 Excel 中的原始资料。</p>}
              </div>
              {table.truncated && <p className="mt-2 shrink-0 text-xs text-muted-foreground">仅展示前 {table.rows.length} 条权益，完整资料请下载。</p>}
            </> : <>
              <pre tabIndex={0} aria-label="JSON 资料内容" className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-3 font-mono text-xs focus-visible:ring-2 focus-visible:ring-ring">{text}</pre>
              <p className="mt-2 shrink-0 text-xs text-muted-foreground">{result.data.truncated ? "仅展示前 16000 字以内，非全文；完整资料请下载。" : "已显示本文件 JSON 内容。"}</p>
            </>}
    </div>
  </section>;
}
