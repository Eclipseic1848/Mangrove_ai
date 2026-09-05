import {
  AlertCircle,
  CheckCircle2,
  CircleStop,
  Clock3,
  HardDrive,
  Inbox,
  Loader2,
  Plus,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type {
  WorkspaceStorage,
  WorkspaceTask,
  WorkspaceTaskStatus,
} from "@/types/semanticWorkspace";

const STATUS: Record<
  WorkspaceTaskStatus,
  { label: string; icon: typeof Clock3; className: string }
> = {
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
    className: "text-amber-600 dark:text-amber-400",
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
    className: "text-emerald-600 dark:text-emerald-400",
  },
};

export const workspaceStatusLabel = (status: WorkspaceTaskStatus) =>
  STATUS[status].label;

function relativeTime(value: string) {
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
  tasks,
  activeTaskId,
  filter,
  recycleBin,
  storage,
  onSelect,
  onFilter,
  onNew,
  onToggleRecycleBin,
}: {
  tasks: WorkspaceTask[];
  activeTaskId: string | null;
  filter: "all" | "active" | "needs_input" | "completed";
  recycleBin: boolean;
  storage?: WorkspaceStorage;
  onSelect: (taskId: string) => void;
  onFilter: (filter: "all" | "active" | "needs_input" | "completed") => void;
  onNew: () => void;
  onToggleRecycleBin: () => void;
}) {
  const filtered = tasks.filter((task) => {
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
    <aside className="flex h-full min-h-0 w-[280px] shrink-0 flex-col border-r bg-muted/20">
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

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {filtered.length ? (
          <div className="space-y-1">
            {filtered.map((task) => {
              const meta = STATUS[task.status];
              const Icon = meta.icon;
              return (
                <button
                  key={task.task_id}
                  type="button"
                  onClick={() => onSelect(task.task_id)}
                  className={cn(
                    "w-full rounded-xl border border-transparent px-3 py-3 text-left transition-colors",
                    activeTaskId === task.task_id
                      ? "border-border bg-background shadow-sm"
                      : "hover:bg-muted",
                  )}
                >
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
              );
            })}
          </div>
        ) : (
          <div className="flex h-44 flex-col items-center justify-center px-5 text-center text-xs text-muted-foreground">
            <Inbox className="mb-2 h-6 w-6 opacity-50" />
            {recycleBin ? "回收站为空" : "当前筛选下没有任务"}
          </div>
        )}
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
    </aside>
  );
}
