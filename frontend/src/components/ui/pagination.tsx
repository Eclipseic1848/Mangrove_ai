import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "./button";

interface PaginationProps {
  page: number;
  totalPages: number;
  total: number;
  onChange: (page: number) => void;
  pageSize?: number;
  pageSizeOptions?: readonly number[];
  onPageSizeChange?: (size: number) => void;
  disabled?: boolean;
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

export function Pagination({ page, totalPages, total, onChange, pageSize, pageSizeOptions = [10, 20, 50], onPageSizeChange, disabled = false }: PaginationProps) {
  if (total === 0 && !pageSize) return null;
  // 仅一页时只显示总数，不渲染翻页控件
  if (totalPages <= 1 && !pageSize) {
    return (
      <div className="flex items-center justify-center py-4 text-xs text-muted-foreground">
        共 {total} 条
      </div>
    );
  }
  return (
    <div className="flex flex-wrap items-center justify-center gap-1 py-4">
      {pageSize && <span className="mr-auto text-xs text-muted-foreground">共 {total} 条 · {total ? `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)}` : "0"}</span>}
      {pageSize && onPageSizeChange && <label className="mr-2 flex items-center gap-2 text-xs text-muted-foreground">每页条数<select aria-label="每页条数" className="h-8 rounded-md border bg-background px-2 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring dark:[color-scheme:dark]" value={pageSize} disabled={disabled} onChange={event => onPageSizeChange(Number(event.target.value))}>{pageSizeOptions.map(size => <option key={size} value={size}>{size}</option>)}</select></label>}
      <Button
        variant="outline"
        size="sm"
        disabled={page <= 1}
        aria-disabled={disabled || page <= 1}
        onClick={() => { if (!disabled) onChange(page - 1); }}
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
            aria-current={p === page ? "page" : undefined}
            aria-disabled={disabled}
            variant={p === page ? "default" : "outline"}
            size="sm"
            onClick={() => { if (!disabled) onChange(p); }}
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
        aria-disabled={disabled || page >= totalPages}
        onClick={() => { if (!disabled) onChange(page + 1); }}
        className="h-7 gap-1"
      >
        下一页 <ChevronRight className="h-4 w-4" />
      </Button>
      <span className="ml-2 text-xs text-muted-foreground">{pageSize ? `${page} / ${totalPages} 页` : `共 ${total} 条`}</span>
    </div>
  );
}
