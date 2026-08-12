import { useEffect, useRef, useState } from "react";
import { Check, Loader2, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { PIPELINE_NODES } from "@/components/PipelineTracker";

export interface NodeEntry { node: string; label: string; view?: any }

const LABEL: Record<string, string> = Object.fromEntries(
  PIPELINE_NODES.map((n) => [n.key, n.label]),
);

/** 打字机逐字动画组件：文本逐字出现，模拟流式输出效果。 */
function TypewriterText({
  text,
  active,
  speed = 55,
}: {
  text: string;
  active: boolean;
  speed?: number;
}) {
  const [revealed, setRevealed] = useState(0);
  const textRef = useRef(text);
  const activeRef = useRef(active);

  // 文本变化时重置
  useEffect(() => {
    if (textRef.current !== text) {
      textRef.current = text;
      setRevealed(0);
    }
  }, [text]);

  // 同步 active 状态到 ref（避免闭包过期）
  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  // 打字机动画：逐字推进直到全文展示完毕
  useEffect(() => {
    if (revealed >= text.length) return;
    const interval = 1000 / speed;
    const timer = setInterval(() => {
      setRevealed((prev) => {
        const next = prev + 1;
        if (next >= text.length) {
          clearInterval(timer);
        }
        return next;
      });
    }, interval);
    return () => clearInterval(timer);
  }, [revealed, text.length, speed]);

  // 当 active 变为 false 且已展示超过一半时，直接跳到末尾（加快节奏）
  useEffect(() => {
    if (!active && revealed > 0 && revealed < text.length && revealed > text.length * 0.4) {
      setRevealed(text.length);
    }
  }, [active]);

  const displayed = text.slice(0, revealed);
  const isTyping = revealed < text.length;

  return (
    <span className="whitespace-pre-wrap">
      {displayed}
      {isTyping && (
        <span className="inline-block w-[1px] h-[1em] bg-primary animate-pulse align-middle ml-0.5" />
      )}
    </span>
  );
}

/** 渲染单个节点的 view 内容。新格式为自然语言字符串（打字机效果），旧格式为 JSON 对象（兜底）。 */
function ViewBody({ node, view, active }: { node: string; view: any; active: boolean }) {
  if (!view || (typeof view === "object" && Object.keys(view).length === 0))
    return <p className="text-xs text-muted-foreground">（无更多详情）</p>;

  // 新格式：自然语言字符串 → 打字机逐字展示
  if (typeof view === "string") {
    return (
      <div className="text-xs leading-relaxed text-foreground/85">
        <TypewriterText text={view} active={active} />
      </div>
    );
  }

  // === 以下为旧格式 JSON 兜底（向后兼容） ===
  if (node === "intent" && view.understanding)
    return <Pre obj={view.understanding} />;
  if (node === "analyze")
    return (
      <div className="space-y-1 text-xs">
        {view.source && <div className="text-muted-foreground">模板来源：{view.source}</div>}
        {view.analysis && <div className="line-clamp-6 whitespace-pre-wrap text-foreground/80">{view.analysis}</div>}
      </div>
    );
  return <Pre obj={view} />;
}

function Pre({ obj }: { obj: any }) {
  return (
    <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted/50 p-2 text-[11px] leading-relaxed text-foreground/80">
      {JSON.stringify(obj, null, 2)}
    </pre>
  );
}

/** 单张可折叠节点卡片：运行中全部展开（方便看打字机动画），运行结束后统一折叠。 */
function NodeCard({ entry, active, running }: { entry: NodeEntry; active: boolean; running: boolean }) {
  const [open, setOpen] = useState(active);
  // 运行期间：当前及过往卡片都保持展开（打字机动画可见）
  // 运行结束：统一折叠，用户可手动点击展开
  useEffect(() => {
    if (running) {
      // 一旦出现过就展开（打字机需要可见）
      if (entry.view && Object.keys(entry.view).length > 0) setOpen(true);
    } else {
      setOpen(false);
    }
  }, [active, running, entry.view]);
  const expanded = open;
  return (
    <div className="rounded-lg border border-border bg-card/60">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm"
      >
        {active ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
        ) : (
          <Check className="h-3.5 w-3.5 shrink-0 text-primary" strokeWidth={3} />
        )}
        <span className="flex-1 font-medium">{entry.label || LABEL[entry.node] || entry.node}</span>
        {expanded ? <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                  : <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />}
      </button>
      {expanded && (
        <div className="border-t border-border px-3 py-2">
          <ViewBody node={entry.node} view={entry.view} active={active} />
        </div>
      )}
    </div>
  );
}

/** 下一个待执行节点的占位卡片：节点运行期间后端不推事件，界面容易显得"卡住"，用它顶住这段空档。 */
function PendingNodeCard({ label }: { label: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border/70 bg-card/30 px-3 py-2 text-sm text-muted-foreground">
      <div className="flex items-center gap-2">
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
        <span className="flex-1 font-medium">{label}</span>
      </div>
      <div className="mt-1 pl-5 text-xs">AI 正在处理中，请稍候…</div>
    </div>
  );
}

/**
 * 节点白盒卡片序列；activeNode 为当前进行中的节点 key（null 表示已结束），running 表示流水线是否运行中。
 * doneNodes 用于推算"下一个待执行节点"，运行期间在真实卡片末尾追加占位卡片，避免节点耗时较久时界面空白。
 */
export function NodeStream({
  entries, activeNode, running, doneNodes,
}: {
  entries: NodeEntry[]; activeNode: string | null; running: boolean; doneNodes: Set<string>;
}) {
  const nextNode = running ? PIPELINE_NODES.find((n) => !doneNodes.has(n.key)) : undefined;
  if (!entries.length && !nextNode) return null;
  return (
    <div className={cn("space-y-2")}>
      {entries.map((e) => (
        <NodeCard key={e.node} entry={e} active={activeNode === e.node} running={running} />
      ))}
      {nextNode && <PendingNodeCard label={nextNode.label} />}
    </div>
  );
}
