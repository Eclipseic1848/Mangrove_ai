import { lazy, Suspense, useState } from "react";
import { FileSearch, TableProperties } from "lucide-react";

import { StructuredDataPrepPage } from "@/pages/StructuredDataPrepPage";
import { cn } from "@/lib/utils";

type WorkspaceMode = "document" | "structured";
const SemanticWorkspacePage = lazy(async () => {
  const module = await import("@/pages/SemanticWorkspacePage");
  return { default: module.SemanticWorkspacePage };
});
const DocumentWorkspacePage = lazy(async () => {
  const module = await import("@/pages/DocumentWorkspacePage");
  return { default: module.DocumentWorkspacePage };
});

export function DataPrepPage() {
  const legacy = new URLSearchParams(window.location.search).get("legacy") === "1";
  if (!legacy) {
    return (
      <Suspense fallback={(
        <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
          正在加载数据工作台…
        </div>
      )}>
        <SemanticWorkspacePage />
      </Suspense>
    );
  }
  return <LegacyDataPrepPage />;
}

function LegacyDataPrepPage() {
  const [mode, setMode] = useState<WorkspaceMode>("document");

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center justify-between border-b bg-background px-5 py-3">
        <div>
          <h1 className="text-lg font-semibold">数据准备</h1>
          <p className="text-xs text-muted-foreground">
            先说明你想得到什么，再执行可核验的数据抽取
          </p>
        </div>
        <div className="flex rounded-lg border bg-muted/40 p-1" role="tablist" aria-label="数据准备模式">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "document"}
            onClick={() => setMode("document")}
            className={cn(
              "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors",
              mode === "document" ? "bg-background font-medium shadow-sm" : "text-muted-foreground",
            )}
          >
            <FileSearch className="h-4 w-4" />
            文档智能抽取
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "structured"}
            onClick={() => setMode("structured")}
            className={cn(
              "flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors",
              mode === "structured" ? "bg-background font-medium shadow-sm" : "text-muted-foreground",
            )}
          >
            <TableProperties className="h-4 w-4" />
            结构化数据准备
          </button>
        </div>
      </header>
      <div className="min-h-0 flex-1">
        {mode === "document" ? (
          <Suspense fallback={(
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              正在加载文档工作台…
            </div>
          )}>
            <DocumentWorkspacePage />
          </Suspense>
        ) : (
          <div className="h-full overflow-y-auto">
            <StructuredDataPrepPage />
          </div>
        )}
      </div>
    </div>
  );
}
