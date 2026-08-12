import { Check, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

// 流水线 8 节点（与后端 _NODE_LABELS 顺序一致）
export const PIPELINE_NODES: { key: string; label: string }[] = [
  { key: "intent", label: "理解意图" },
  { key: "planner", label: "规划任务" },
  { key: "router", label: "选择采集引擎" },
  { key: "collect", label: "采集数据" },
  { key: "clean", label: "清洗数据" },
  { key: "analyze", label: "分析" },
  { key: "checker", label: "质量评估" },
  { key: "output", label: "生成产出" },
];

export type NodeState = "done" | "active" | "pending";

/**
 * 竖向流水线 Stepper：已完成 ✓ / 进行中 转圈 / 待执行 空心点。
 * done 为已完成节点集合，running 表示流程未结束（用于把"下一个"标为进行中）。
 */
export function PipelineTracker({ done, running }: { done: Set<string>; running: boolean }) {
  // 找到第一个未完成节点作为 active（仅当流程仍在跑时）
  const activeIdx = running ? PIPELINE_NODES.findIndex((n) => !done.has(n.key)) : -1;

  return (
    <ol className="relative ml-1.5 border-l border-border">
      {PIPELINE_NODES.map((n, i) => {
        const state: NodeState = done.has(n.key) ? "done" : i === activeIdx ? "active" : "pending";
        return (
          <li key={n.key} className="mb-4 ml-5 last:mb-0">
            <span
              className={cn(
                "absolute -left-[9px] flex h-[18px] w-[18px] items-center justify-center rounded-full border",
                state === "done" && "border-primary bg-primary text-primary-foreground",
                state === "active" && "border-primary bg-background text-primary",
                state === "pending" && "border-border bg-background text-transparent",
              )}
            >
              {state === "done" && <Check className="h-3 w-3" strokeWidth={3} />}
              {state === "active" && <Loader2 className="h-3 w-3 animate-spin" />}
              {state === "pending" && <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/40" />}
            </span>
            <span
              className={cn(
                "text-sm",
                state === "done" && "text-foreground",
                state === "active" && "font-medium text-primary",
                state === "pending" && "text-muted-foreground/60",
              )}
            >
              {n.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
