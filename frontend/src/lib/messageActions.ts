import { toast } from "sonner";
import { beijingTime } from "./beijingTime";

export type TokenUsage = { prompt_tokens: number; completion_tokens: number; total_tokens: number; calls: number; incomplete?: boolean; missing_fields?: string[]; scope?: "execution_only"; no_model_call?: boolean };

export function formatTokenUsage(usage?: TokenUsage | null): string {
  if (usage?.no_model_call && usage.calls === 0 && usage.total_tokens === 0 && !usage.incomplete && !usage.missing_fields?.length) return "Token：0（未调用模型）";
  if (!usage || !usage.calls) return "Token：此记录未保存用量";
  if (usage.missing_fields?.length === 3) return "Token：模型未返回统计";
  const value = (field: "prompt_tokens" | "completion_tokens" | "total_tokens") => usage.missing_fields?.includes(field) ? "未知" : usage[field];
  return `Token：${value("prompt_tokens")} 输入 / ${value("completion_tokens")} 输出 · 共 ${value("total_tokens")}${usage.incomplete ? "（部分未知）" : ""}${usage.scope === "execution_only" ? "（执行阶段）" : ""}`;
}

export function fmtTime(iso?: string): string {
  if (!iso) return "";
  return beijingTime(iso);
}

export async function copyText(text: string) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      toast.success("已复制");
      return true;
    }
  } catch { /* 局域网浏览器可能拒绝剪贴板权限，继续尝试兼容方式。 */ }
  const previous = document.activeElement as HTMLElement | null;
  const input = document.createElement("textarea");
  input.value = text;
  input.style.cssText = "position:fixed;opacity:0";
  document.body.appendChild(input);
  input.select();
  try {
    if (!document.execCommand("copy")) throw new Error("copy failed");
    toast.success("已复制");
    return true;
  } catch { toast.error("复制失败，请手动选择文本复制"); return false; }
  finally { input.remove(); previous?.focus(); }
}
