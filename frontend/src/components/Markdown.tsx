import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** 统一的 Markdown 渲染（报告、回执）。样式见 index.css 的 .prose-mangrove。 */
export function Markdown({ children, safeResources = false, externalImagesAsLinks = false }: { children: string; safeResources?: boolean; externalImagesAsLinks?: boolean }) {
  return (
    <div className="prose-mangrove">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={safeResources || externalImagesAsLinks ? {
        // 任务正文不能自动向外站请求图片，只有用户主动打开链接才访问。
        img: ({ node: _node, ...props }) => {
          const { src, alt } = props;
          let external = false;
          if (src && externalImagesAsLinks) {
            try {
              const url = new URL(src, window.location.href);
              // 仅外站图片需主动查看；保留同源图片和原URL净化边界。
              external = ["http:", "https:"].includes(url.protocol) && url.origin !== window.location.origin;
            } catch { return <span>{alt || "图片地址不可用"}</span>; }
          }
          return safeResources || external
            ? src ? <a href={src} target="_blank" rel="noopener noreferrer">查看图片：{alt || "图片"}</a> : <span>{alt || "图片地址不可用"}</span>
            : <img {...props} />;
        },
        ...(safeResources ? { a: ({ href, children: label }: { href?: string; children?: React.ReactNode }) => <a href={href} target="_blank" rel="noopener noreferrer">{label}</a> } : {}),
      } : undefined}>{children}</ReactMarkdown>
    </div>
  );
}
