import { useEffect, useState } from "react";
import { Loader2, ListChecks, ExternalLink, Check } from "lucide-react";
import type { ChatProgress } from "@/lib/api";

export function CollectionProgress({ pending, progress = [], steps = [], preparing = false }: {
  pending: boolean; progress?: ChatProgress[]; steps?: string[]; preparing?: boolean;
}) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    setSeconds(0);
    if (!pending) return;
    const timer = setInterval(() => setSeconds(value => value + 1), 1000);
    return () => clearInterval(timer);
  }, [pending]);
  const latest = progress[progress.length - 1];
  const stages = [...new Map(progress.map(item => [item.node, item])).values()];
  const sources = [...new Map(progress.flatMap(item => item.sources ?? []).filter(source => {
    try { const url = new URL(source.url); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password; } catch { return false; }
  }).map(source => [source.url, source])).values()];
  const sourceCard = (source: typeof sources[number]) => <a key={source.url} href={source.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer"
    className="flex min-w-0 items-start gap-3 rounded-lg border bg-background p-3 hover:border-primary/60 hover:bg-primary/5 focus-visible:ring-2 focus-visible:ring-ring">
    <ExternalLink aria-hidden="true" className="mt-1 h-4 w-4 shrink-0 text-primary" />
    <span className="min-w-0 flex-1"><span className="block break-words font-medium">{source.title || new URL(source.url).hostname}</span>
      <span className="mt-1 block break-all text-xs text-muted-foreground">{new URL(source.url).hostname} · {source.status === "received" ? "已取得资料" : "准备访问"}</span></span>
    <span className="shrink-0 text-xs text-primary">打开来源</span>
  </a>;
  return <section aria-label="采集执行记录" className="rounded-xl border border-primary/25 bg-primary/[0.04] p-4 text-sm">
    <div className="flex items-start gap-2">
      {pending ? <Loader2 aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none text-primary" /> : <ListChecks aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />}
      <div className="min-w-0 flex-1">
        <p role="status" aria-live="polite" className="font-medium">{pending ? latest?.summary || (preparing ? "正在理解你的需求，准备回复或执行…" : "正在启动采集分析…") : "本次执行记录"}</p>
        {pending && <p className="mt-1 text-xs text-muted-foreground">{preparing ? "正在等待所选模型响应" : "任务正在后台执行，刷新后会恢复结果"} · 本次查看已等待 {seconds} 秒</p>}
        {pending && seconds >= 30 && <p className="mt-2 text-xs text-muted-foreground">数据读取或模型分析可能较慢；新的执行进展会在这里更新，无需重复发送。</p>}
      </div>
    </div>
    {stages.length > 0 && <ol aria-label="执行阶段" className="mt-3 flex flex-wrap gap-2">{stages.map(stage => <li key={stage.node} className="inline-flex items-center gap-1 rounded-full border bg-background px-2 py-1 text-xs">
      {stage.status === "completed" ? <Check aria-hidden="true" className="h-3 w-3 text-primary" /> : pending && stage === latest ? <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin motion-reduce:animate-none" /> : null}{stage.label}
    </li>)}</ol>}
    {sources.length > 0 && <div aria-label="本次来源" className="mt-4 space-y-2">
      <p className="font-medium">本次来源 · {sources.length} 个</p>
      {sources.slice(0, 5).map(sourceCard)}
      {sources.length > 5 && <details><summary className="cursor-pointer py-2 text-xs text-primary">查看其余 {sources.length - 5} 个来源</summary><div className="space-y-2">{sources.slice(5).map(sourceCard)}</div></details>}
      <p className="text-xs text-muted-foreground">链接来自实际采集记录；取得资料可能仅含摘要，不代表已读取全文。点击在新标签页查看原始来源。</p>
    </div>}
    {(progress.length > 0 || steps.length > 0) && <details className="mt-3 border-t pt-2">
      <summary className="cursor-pointer text-xs text-muted-foreground">查看执行过程（{progress.length || steps.length} 条）</summary>
      <ol className="mt-3 space-y-3 border-l pl-3 text-xs">
        {progress.length ? progress.map(item => <li key={item.sequence} className={item.status === "warning" || item.status === "failed" ? "text-amber-700 dark:text-amber-300" : "text-muted-foreground"}>
          <span className="font-medium text-foreground">{item.label}</span><p className="mt-1">{item.summary}</p>
        </li>) : steps.map((step, index) => <li key={index}>{step}</li>)}
      </ol>
    </details>}
  </section>;
}
