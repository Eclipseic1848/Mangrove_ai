import {
  AlertCircle,
  CheckCircle2,
  CircleStop,
  Clock3,
  HardDrive,
  Inbox,
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Trash2,
} from "lucide-react";
import { useLayoutEffect, useRef, type MutableRefObject } from "react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { beijingTime } from "@/lib/beijingTime";
import type {
  WorkspaceStorage,
  WorkspaceTask,
  WorkspaceTaskStatus,
} from "@/types/semanticWorkspace";

const STATUS: Record<
  WorkspaceTaskStatus,
  { label: string; icon: typeof Clock3; className: string }
> = {
  pausing: { label: "正在暂停", icon: Loader2, className: "text-amber-600" },
  paused: { label: "已暂停", icon: Clock3, className: "text-amber-600" },
  queued: {
    label: "排队中",
    icon: Clock3,
    className: "text-slate-500",
  },
  running: {
    label: "执行中",
    icon: Loader2,
    className: "text-primary",
  },
  needs_input: {
    label: "需要确认",
    icon: AlertCircle,
    className: "text-accent-foreground",
  },
  cancelling: {
    label: "正在停止",
    icon: Loader2,
    className: "text-slate-500",
  },
  cancelled: {
    label: "已停止",
    icon: CircleStop,
    className: "text-slate-500",
  },
  failed: {
    label: "失败",
    icon: AlertCircle,
    className: "text-destructive",
  },
  candidate_ready: {
    label: "候选待验证",
    icon: AlertCircle,
    className: "text-amber-600 dark:text-amber-400",
  },
  completed: {
    label: "已完成",
    icon: CheckCircle2,
    className: "text-accent-foreground",
  },
};

export const workspaceStatusLabel = (status: WorkspaceTaskStatus) =>
  STATUS[status].label;

function relativeTime(value: string) {
  if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(value)) return beijingTime(value);
  const diff = Date.now() - new Date(value).getTime();
  const minutes = Math.max(0, Math.round(diff / 60_000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.round(hours / 24)} 天前`;
}

function formatBytes(bytes: number) {
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`;
}

export function WorkspaceTaskSidebar({
  collapsed,
  onToggle,
  scrollPosition,
  tasks,
  activeTaskId,
  filter,
  recycleBin,
  storage,
  loading,
  initialLoading,
  error,
  lastReadAt,
  onRetry,
  hasMore,
  loadingMore,
  onLoadMore,
  onSelect,
  onRemove,
  onFilter,
  onNew,
  onToggleRecycleBin,
}: {
  collapsed: boolean;
  onToggle: () => void;
  scrollPosition: MutableRefObject<number>;
  tasks: Pick<WorkspaceTask, "task_id" | "title" | "status" | "updated_at">[];
  activeTaskId: string | null;
  filter: "all" | "active" | "needs_input" | "completed";
  recycleBin: boolean;
  storage?: WorkspaceStorage;
  loading?: boolean;
  initialLoading?: boolean;
  error?: string;
  lastReadAt?: number;
  onRetry: () => void;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore: () => void;
  onSelect: (taskId: string) => void;
  onRemove: (task: Pick<WorkspaceTask, "task_id" | "title" | "status">) => void;
  onFilter: (filter: "all" | "active" | "needs_input" | "completed") => void;
  onNew: () => void;
  onToggleRecycleBin: () => void;
}) {
  const listRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    // 窄屏抽屉会卸载列表，展开时恢复同一列表的阅读位置。
    if (!collapsed && listRef.current) listRef.current.scrollTop = scrollPosition.current;
  }, [collapsed, scrollPosition]);
  const filtered = tasks.filter((task) => {
    if (recycleBin) return true;
    if (filter === "active") {
      return ["queued", "running", "cancelling"].includes(task.status);
    }
    if (filter === "needs_input") {
      return ["needs_input", "candidate_ready"].includes(task.status);
    }
    if (filter === "completed") return task.status === "completed";
    return true;
  });
  const filters = [
    { key: "all" as const, label: "全部" },
    { key: "active" as const, label: "进行中" },
    { key: "needs_input" as const, label: "待确认" },
    { key: "completed" as const, label: "已完成" },
  ];

  return (
    <aside aria-label="任务列表" className={cn("flex h-full min-h-0 shrink-0 flex-col border-r bg-muted/20", collapsed ? "w-12" : "w-[280px]")}>
      <div className={cn("flex shrink-0 items-center", collapsed ? "flex-col gap-2 py-2" : "h-10 justify-between px-3")}>
        <h2 className={cn("text-sm font-semibold", collapsed && "order-2 mt-1 text-xs font-medium text-muted-foreground [writing-mode:vertical-rl] [text-orientation:upright]")}>任务列表</h2>
        <Tooltip.Provider delayDuration={200}>
          <Tooltip.Root>
            <Tooltip.Trigger asChild>
              <Button id="task-list-toggle" type="button" variant="outline" aria-label={collapsed ? "展开任务列表" : "收起任务列表"} aria-expanded={!collapsed} aria-controls="workspace-task-list-content" onClick={onToggle}
                className={cn("shrink-0 rounded-lg text-xs", collapsed ? "order-1 h-11 w-11 p-0" : "h-9 gap-1.5 px-2.5")}>
                {collapsed ? <PanelLeftOpen aria-hidden="true" /> : <PanelLeftClose aria-hidden="true" />}
                {!collapsed && <span>收起</span>}
              </Button>
            </Tooltip.Trigger>
            {collapsed && <Tooltip.Content side="right" sideOffset={8} className="z-50 rounded-md border bg-card px-2.5 py-1.5 text-xs text-foreground shadow-md">展开任务列表</Tooltip.Content>}
          </Tooltip.Root>
        </Tooltip.Provider>
      </div>
      <div id="workspace-task-list-content" hidden={collapsed} className={cn("min-h-0 flex-1 flex-col", !collapsed && "flex")}>
      <div className="p-3">
        <button
          type="button"
          onClick={onNew}
          className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-teal-700 text-sm font-medium text-white shadow-sm hover:bg-teal-800 dark:bg-teal-700 dark:hover:bg-teal-600"
        >
          <Plus className="h-4 w-4" />
          新建任务
        </button>
        <div className="mt-3 grid grid-cols-4 gap-1 rounded-lg bg-muted p-1">
          {filters.map((item) => (
            <button
              key={item.key}
              type="button"
              aria-pressed={!recycleBin && filter === item.key}
              onClick={() => onFilter(item.key)}
              className={cn(
                "rounded-md px-1 py-1.5 text-[11px] transition-colors",
                !recycleBin && filter === item.key
                  ? "bg-background font-medium text-foreground shadow-sm"
                  : "text-slate-700 hover:text-foreground dark:text-slate-300",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div ref={listRef} role="region" aria-label="任务记录" onScroll={event => { if (!collapsed) scrollPosition.current = event.currentTarget.scrollTop; }} className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {error && <div role="alert" className="space-y-2 px-2 py-3 text-xs text-red-700 dark:text-red-300">
          <p>{error}读取失败，已加载的任务仍保留。</p>
          {Boolean(lastReadAt) && <p>最近成功读取：{beijingTime(new Date(lastReadAt!).toISOString())}</p>}
          <button type="button" onClick={onRetry} className="rounded-md border px-3 py-2 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring">重试读取列表</button>
        </div>}
        {loading && !error && (initialLoading || !tasks.length) && <p role="status" className="p-3 text-xs text-muted-foreground">正在读取任务列表…</p>}
        {initialLoading && !error ? null : filtered.length ? (
          <div className="space-y-1">
            {filtered.map((task) => {
              const meta = STATUS[task.status];
              const Icon = meta.icon;
              const conversation = task.task_id.startsWith("conversation:");
              const removingBlocked = conversation && ["queued", "running", "cancelling", "pausing"].includes(task.status);
              return (
                <div
                  key={task.task_id}
                  role="group"
                  aria-label={task.title}
                  className={cn(
                    "flex items-center rounded-xl border border-transparent transition-colors",
                    activeTaskId === task.task_id
                      ? "border-border bg-background shadow-sm"
                      : "hover:bg-muted",
                  )}
                >
                  <button type="button" onClick={() => onSelect(task.task_id)} className="min-w-0 flex-1 rounded-xl px-3 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <div className="flex items-start gap-2">
                    <Icon
                      className={cn(
                        "mt-0.5 h-4 w-4 shrink-0",
                        meta.className,
                        ["running", "cancelling"].includes(task.status)
                          && "animate-spin"
                      )}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">{task.title}</p>
                      <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-muted-foreground">
                        <span className={meta.className}>{meta.label}</span>
                        <span>{relativeTime(task.updated_at)}</span>
                      </div>
                    </div>
                  </div>
                  </button>
                  {!recycleBin && <button type="button" aria-label={conversation ? "删除历史记录" : "移入回收站"} title={removingBlocked ? "请先停止执行，再删除历史记录" : `${conversation ? "删除历史记录" : "移入回收站"}：${task.title}`} disabled={removingBlocked} onClick={() => onRemove(task)} className="mr-1 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-40">
                    <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                  </button>}
                </div>
              );
            })}
          </div>
        ) : !loading && !error && (
          <div className="flex h-44 flex-col items-center justify-center px-5 text-center text-xs text-muted-foreground">
            <Inbox className="mb-2 h-6 w-6 opacity-50" />
            {recycleBin ? "回收站为空" : "当前筛选下没有任务"}
          </div>
        )}
        {!initialLoading && hasMore && <button type="button" disabled={loadingMore} onClick={onLoadMore} className="mt-3 w-full rounded-lg border px-3 py-2 text-xs hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">{loadingMore ? "正在加载…" : "加载更早任务"}</button>}
      </div>

      <div className="border-t p-3">
        {storage && (
          <div className="mb-2 flex items-center justify-between px-2 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1">
              <HardDrive className="h-3.5 w-3.5" />
              本地占用
            </span>
            <span>{formatBytes(storage.total_bytes)}</span>
          </div>
        )}
        <button
          type="button"
          aria-pressed={recycleBin}
          onClick={onToggleRecycleBin}
          className={cn(
            "flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-xs transition-colors",
            recycleBin
              ? "bg-muted font-medium text-foreground"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
          )}
        >
          <span className="flex items-center gap-2">
            <Trash2 className="h-4 w-4" />
            回收站
          </span>
          {storage && storage.recycle_bin_count > 0 && (
            <span className="rounded-full bg-muted-foreground/15 px-1.5 py-0.5 text-[10px]">
              {storage.recycle_bin_count}
            </span>
          )}
        </button>
      </div>
      </div>
    </aside>
  );
}
