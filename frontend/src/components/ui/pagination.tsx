import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "./button";

interface PaginationProps {
  page: number;
  totalPages: number;
  total: number;
  onChange: (page: number) => void;
}

// 生成页码序列：总页数 <=7 时全显示，否则首末页 + 当前页 ±1 + 省略号
function pageNumbers(current: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages: (number | "…")[] = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(total - 1, current + 1);
  if (start > 2) pages.push("…");
  for (let i = start; i <= end; i++) pages.push(i);
  if (end < total - 1) pages.push("…");
  pages.push(total);
  return pages;
}

export function Pagination({ page, totalPages, total, onChange }: PaginationProps) {
  if (total === 0) return null;
  // 仅一页时只显示总数，不渲染翻页控件
  if (totalPages <= 1) {
    return (
      <div className="flex items-center justify-center py-4 text-xs text-muted-foreground">
        共 {total} 条
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center justify-center gap-1 py-4">
      <Button
        variant="outline"
        size="sm"
        disabled={page <= 1}
        onClick={() => onChange(page - 1)}
        className="h-7 gap-1"
      >
        <ChevronLeft className="h-4 w-4" /> 上一页
      </Button>
      {pageNumbers(page, totalPages).map((p, i) =>
        p === "…" ? (
          <span key={`ellipsis-${i}`} className="px-1 text-xs text-muted-foreground">
            …
          </span>
        ) : (
          <Button
            key={p}
            variant={p === page ? "default" : "outline"}
            size="sm"
            onClick={() => onChange(p)}
            className="h-7 min-w-[1.75rem] px-2"
          >
            {p}
          </Button>
        ),
      )}
      <Button
        variant="outline"
        size="sm"
        disabled={page >= totalPages}
        onClick={() => onChange(page + 1)}
        className="h-7 gap-1"
      >
        下一页 <ChevronRight className="h-4 w-4" />
      </Button>
      <span className="ml-2 text-xs text-muted-foreground">共 {total} 条</span>
    </div>
  );
}
