import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** 统一的 Markdown 渲染（报告、回执）。样式见 index.css 的 .prose-mangrove。 */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="prose-mangrove">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
