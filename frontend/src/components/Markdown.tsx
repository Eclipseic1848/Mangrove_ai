import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** 统一的 Markdown 渲染（报告、回执）。样式见 index.css 的 .prose-mangrove。 */
export function Markdown({ children, safeResources = false }: { children: string; safeResources?: boolean }) {
  return (
    <div className="prose-mangrove">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={safeResources ? {
        // 任务正文不能自动向外站请求图片，只有用户主动打开链接才访问。
        img: ({ src, alt }) => src
          ? <a href={src} target="_blank" rel="noopener noreferrer">查看图片：{alt || "图片"}</a>
          : <span>{alt || "图片地址不可用"}</span>,
        a: ({ href, children: label }) => <a href={href} target="_blank" rel="noopener noreferrer">{label}</a>,
      } : undefined}>{children}</ReactMarkdown>
    </div>
  );
}
