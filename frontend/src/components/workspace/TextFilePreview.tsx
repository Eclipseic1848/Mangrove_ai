import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import DOMPurify from "dompurify";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getUploadFile } from "@/lib/dataPrepApi";
import type { UploadItem } from "@/types/dataPrep";

function safeDocument(raw: string) {
  const clean = DOMPurify.sanitize(raw, { WHOLE_DOCUMENT: true, USE_PROFILES: { html: true },
    FORBID_TAGS: ["script", "iframe", "object", "embed", "form", "base", "meta", "link", "audio", "video"],
    FORBID_ATTR: ["srcdoc", "srcset", "action", "formaction"] });
  const doc = new DOMParser().parseFromString(clean, "text/html");
  doc.querySelectorAll("a").forEach(link => { link.removeAttribute("href"); link.removeAttribute("ping"); });
  doc.querySelectorAll("img").forEach(img => {
    if (!/^data:image\/(png|jpeg|gif|webp);base64,/i.test(img.getAttribute("src") ?? "")) { img.removeAttribute("src"); img.alt ||= "外部图片未加载"; }
  });
  const csp = doc.createElement("meta"); csp.httpEquiv = "Content-Security-Policy";
  csp.content = "default-src 'none'; script-src 'none'; connect-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
  doc.head.prepend(csp);
  return `<!doctype html>${doc.documentElement.outerHTML}`;
}

// 沙箱网页的事件不冒泡到父窗口；必须绑定其自身的滚动容器。
export function bindPan(scroller: HTMLElement, surface: HTMLElement | Document) {
  const doc = scroller.ownerDocument;
  let start: { id: number; x: number; y: number; left: number; top: number } | null = null;
  const down = (event: Event) => {
    const e = event as PointerEvent;
    if (e.button !== 0 || e.pointerType !== "mouse") return;
    if ((e.target as Element | null)?.closest("a,button,input,textarea,select,[contenteditable]")) return;
    const rect = surface === doc ? { left: 0, top: 0 } : scroller.getBoundingClientRect();
    if (e.clientX-rect.left >= scroller.clientWidth || e.clientY-rect.top >= scroller.clientHeight) return;
    start = { id: e.pointerId, x: e.clientX, y: e.clientY, left: scroller.scrollLeft, top: scroller.scrollTop };
    scroller.setPointerCapture(e.pointerId); e.preventDefault();
  };
  const move = (event: Event) => {
    const e = event as PointerEvent;
    if (!start || start.id !== e.pointerId) return;
    scroller.scrollLeft = start.left + start.x-e.clientX;
    scroller.scrollTop = start.top + start.y-e.clientY;
  };
  const stop = () => { const id = start?.id; start = null; if (id !== undefined && scroller.hasPointerCapture(id)) scroller.releasePointerCapture(id); };
  surface.addEventListener("pointerdown", down); doc.addEventListener("pointermove", move);
  doc.addEventListener("pointerup", stop); doc.addEventListener("pointercancel", stop); scroller.addEventListener("lostpointercapture", stop);
  return () => { stop(); surface.removeEventListener("pointerdown", down); doc.removeEventListener("pointermove", move); doc.removeEventListener("pointerup", stop); doc.removeEventListener("pointercancel", stop); scroller.removeEventListener("lostpointercapture", stop); };
}

export function TextFilePreview({ upload }: { upload: UploadItem }) {
  const [encoding, setEncoding] = useState("utf-8");
  const [wrap, setWrap] = useState(false);
  const [pan, setPan] = useState(false);
  const [loaded, setLoaded] = useState(0);
  const viewport = useRef<HTMLDivElement>(null);
  const iframe = useRef<HTMLIFrameElement>(null);
  const extension = upload.original_name.split(".").pop()?.toLowerCase();
  const html = extension === "html" || extension === "htm";
  const markdown = extension === "md" || extension === "markdown";
  const result = useQuery({ queryKey: ["upload-text", upload.upload_id, encoding], retry: false,
    queryFn: async () => {
      // 明确拒绝超限，不把截断文本冒充完整原件。
      if (upload.size_bytes > 4 * 1024 * 1024) throw new Error("文本超过 4 MB 在线预览上限，请下载原件查看");
      const file = await getUploadFile(upload);
      if (file.size > 4 * 1024 * 1024) throw new Error("文本超过在线预览上限");
      try { return new TextDecoder(encoding, { fatal: true }).decode(await file.arrayBuffer()); }
      catch { throw new Error("文本编码无法识别，请切换编码后重试"); }
    },
  });
  const safeHtml = useMemo(() => html && result.data !== undefined ? safeDocument(result.data) : "", [html, result.data]);
  useEffect(() => {
    if (!pan) return;
    const doc = iframe.current?.contentDocument;
    const scroller = html ? doc?.scrollingElement as HTMLElement | null : viewport.current;
    if (scroller) return bindPan(scroller, html ? doc! : scroller);
  }, [pan, html, loaded, result.data]);
  return <section className="flex h-full min-h-0 min-w-0 flex-col gap-2" aria-label="文本文件预览">
    <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs">
      <button type="button" className="rounded border px-2 py-1.5" aria-pressed={pan} onClick={() => setPan(!pan)}>{pan ? "拖动查看" : "选择文字"}</button>
      <label>编码 <select aria-label="文本编码" className="rounded border bg-background p-1.5" value={encoding} onChange={e => setEncoding(e.target.value)}><option value="utf-8">UTF-8</option><option value="gb18030">GB18030</option><option value="utf-16le">UTF-16 LE</option></select></label>
      {!html && !markdown && <label><input type="checkbox" checked={wrap} onChange={e => setWrap(e.target.checked)} /> 自动换行</label>}
      {html && <span className="text-muted-foreground">静态安全预览 · 脚本和外部资源已禁用</span>}
    </div>
    {result.isError ? <p role="alert" className="text-sm text-destructive">{result.error.message}<button type="button" className="ml-2 rounded border px-2 py-1" onClick={() => void result.refetch()}>重试</button></p>
      : result.data === undefined ? <p role="status">正在读取文件…</p>
      : html ? <iframe ref={iframe} title="HTML 安全预览" sandbox="allow-same-origin" referrerPolicy="no-referrer" srcDoc={safeHtml} onLoad={() => setLoaded(v => v+1)} className="min-h-0 w-full flex-1 border bg-white" />
      : <div ref={viewport} tabIndex={0} aria-label="文本内容" className="min-h-0 flex-1 overflow-auto rounded border bg-background p-4" style={{ cursor: pan ? "grab" : undefined }}>
        {markdown ? <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ img: () => <span>图片未加载</span>, a: ({ children }) => <span>{children}</span> }}>{result.data}</ReactMarkdown>
          : <pre aria-label="纯文本原件" style={{ whiteSpace: wrap ? "pre-wrap" : "pre", overflowWrap: wrap ? "anywhere" : undefined, tabSize: 4 }}>{result.data}</pre>}
      </div>}
  </section>;
}
