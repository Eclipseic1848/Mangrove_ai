import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Document, Page } from "react-pdf";
import { authenticatedFetch, readAuthenticatedBlob } from "@/lib/api";

export function OfficeFilePreview({ uploadId, slides }: { uploadId: string; slides: boolean }) {
  const [page, setPage] = useState(1);
  const [count, setCount] = useState(0);
  const [width, setWidth] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState(false);
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const viewport = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const result = useQuery({ queryKey: ["office-original-preview", uploadId], retry: false,
    queryFn: async ({ signal }) => {
      const response = await authenticatedFetch(`/api/data-sources/uploads/${encodeURIComponent(uploadId)}/office-preview`, { signal });
      if (!response.ok) throw new Error("Office 预览暂不可用：文件可能加密、超限或转换服务未就绪，请重试或下载原件。");
      return readAuthenticatedBlob(response);
    },
  });
  useEffect(() => {
    if (!result.data) return;
    const next = URL.createObjectURL(result.data); setUrl(next); setError("");
    return () => URL.revokeObjectURL(next);
  }, [result.data]);
  useEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setWidth(Math.max(120, element.clientWidth-24)));
    observer.observe(element); return () => observer.disconnect();
  }, []);
  const button = "rounded border px-2 py-1 text-xs disabled:opacity-40";
  return <section aria-label={slides ? "幻灯片预览" : "Word 原版式预览"} className="flex h-full min-h-0 flex-col gap-2">
    <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs">
      <button type="button" className={button} disabled={page <= 1} onClick={() => setPage(page-1)}>上一页</button>
      <span>{page} / {count || "—"}</span>
      <button type="button" className={button} disabled={page >= count} onClick={() => setPage(page+1)}>下一页</button>
      <button type="button" className={button} disabled={zoom <= .5} onClick={() => setZoom(Math.max(.5, zoom-.25))}>缩小</button>
      <span>{Math.round(zoom*100)}%</span>
      <button type="button" className={button} disabled={zoom >= 3} onClick={() => setZoom(Math.min(3, zoom+.25))}>放大</button>
      <button type="button" className={button} onClick={() => setZoom(1)}>适应宽度</button>
      <button type="button" className={button} aria-pressed={pan} onClick={() => setPan(!pan)}>{pan ? "拖动查看" : "选择文字"}</button>
    </div>
    {(result.error || error) && <p role="alert" className="text-sm text-destructive">{result.error?.message || error}<button type="button" className={button} onClick={() => { setError(""); void result.refetch(); }}>重试</button></p>}
    <div ref={viewport} tabIndex={0} aria-label="Office 页面内容" className="min-h-0 flex-1 overflow-auto bg-slate-100 p-2" style={{ cursor: pan ? "grab" : undefined }}
      onPointerDown={e => { if (!pan || e.button !== 0 || e.pointerType !== "mouse") return; const el = e.currentTarget, rect = el.getBoundingClientRect(); if (e.clientX-rect.left >= el.clientWidth || e.clientY-rect.top >= el.clientHeight) return; drag.current = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop }; el.setPointerCapture(e.pointerId); e.preventDefault(); }}
      onPointerMove={e => { if (drag.current) { e.currentTarget.scrollLeft = drag.current.left + drag.current.x-e.clientX; e.currentTarget.scrollTop = drag.current.top + drag.current.y-e.clientY; } }}
      onPointerUp={e => { drag.current = null; if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId); }} onPointerCancel={() => { drag.current = null; }} onLostPointerCapture={() => { drag.current = null; }}>
      {result.isFetching ? <p role="status">正在生成隔离预览，最长约 90 秒；原文件不变…</p> : url && width > 0 && !error && <Document file={url} onLoadSuccess={({ numPages }) => setCount(numPages)} onLoadError={() => setError("预览副本无法读取") }>
        <Page pageNumber={page} width={Math.round(width*zoom)} renderAnnotationLayer={false} onRenderError={() => setError("页面渲染失败")} />
      </Document>}
    </div>
    <p className="shrink-0 text-[11px] text-muted-foreground">{slides ? "静态幻灯片，不播放动画或视频。" : "原版式预览副本；字体替换可能影响排版。"}</p>
  </section>;
}
