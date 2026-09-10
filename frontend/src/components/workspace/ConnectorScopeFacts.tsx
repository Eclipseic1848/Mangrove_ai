import type { ConnectorSourceScope } from "@/types/semanticWorkspace";

export function ConnectorScopeFacts({ scope }: { scope: ConnectorSourceScope }) {
  const selection = scope.selection;
  return <div className="space-y-1 break-all text-xs text-muted-foreground">
    {scope.protocol === "database" ? <><p>数据库：{scope.connection_id} · 表：{selection.table}</p><p>字段：{selection.fields?.join("、") || "全部"}</p><p>筛选：{selection.filters?.map(item => `${item.field} = ${item.value}`).join("；") || "不筛选"}</p></> : <><p>公开 HTTP：{selection.url}</p><p>{selection.pagination ? `分页：${selection.pagination.options.page_param}，最多 ${selection.pagination.options.max_pages} 页` : "单次读取，不自动翻页"}</p></>}
    <p>连接版本：{scope.connection_version}</p>
    <p>本次保留已读取原件，不保证整个来源完整或跨批次一致。</p>
  </div>;
}
