import { useState } from "react";
import { SiOpenai } from "@icons-pack/react-simple-icons";
import { Cpu } from "lucide-react";
import { cn } from "@/lib/utils";

export const PROVIDERS: Record<string, { label: string; file?: string }> = {
  deepseek: { label: "DeepSeek", file: "deepseek.png" }, qwen: { label: "通义千问", file: "qwen.png" },
  openai: { label: "OpenAI" }, anthropic: { label: "Claude", file: "claude-color.png" },
  gemini: { label: "Gemini", file: "gemini-color.png" }, kimi: { label: "Kimi", file: "kimi-color.png" },
  zhipu: { label: "智谱", file: "zhipu-color.png" }, xai: { label: "Grok", file: "grok.png" },
};

export function OptionalBrandImage({ src, className }: { src: string; className?: string }) {
  const [failedSource, setFailedSource] = useState<string | null>(null);
  // 正常发布必须携带品牌图片；仅在请求异常时保留图标和名称，避免空白。
  return failedSource === src ? <Cpu aria-hidden className={className} />
    : <img src={src} alt="" className={className} onError={() => setFailedSource(src)} />;
}

export function ProviderLogo({ id, configured = true, className }: { id: string; configured?: boolean; className?: string }) {
  const provider = PROVIDERS[id];
  return <span aria-hidden="true" className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted/60", id === "kimi" && "bg-slate-900", className)}>
    {provider?.file ? <OptionalBrandImage src={`/overview-brands/${provider.file}`} className={cn("h-5 w-5 object-contain", id === "xai" && "dark:invert", !configured && "grayscale opacity-45")} /> : id === "openai" ? <SiOpenai aria-hidden className={cn("h-5 w-5", !configured && "text-muted-foreground")} /> : <Cpu className="h-5 w-5 text-muted-foreground" />}
  </span>;
}
