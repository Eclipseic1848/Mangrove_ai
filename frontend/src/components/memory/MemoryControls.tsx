import { useLayoutEffect, useRef, type TextareaHTMLAttributes } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** 两个记忆入口共用限高输入，长内容不撑开整个页面。 */
export function MemoryTextarea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const input = ref.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(240, Math.max(80, input.scrollHeight))}px`;
  }, [props.value]);
  return <textarea {...props} ref={ref} rows={2} className={cn("block w-full resize-none overflow-y-auto rounded-md border bg-background px-3 py-2 text-sm leading-6 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50", props.className)} />;
}

/** 确认与未保存提示共用 Radix 焦点域，不调用浏览器原生确认框。 */
export function MemoryConfirm({ open, title, description, action, busy = false, error, danger = false, onCancel, onConfirm }: {
  open: boolean; title: string; description: string; action: string; busy?: boolean; error?: string;
  danger?: boolean; onCancel: () => void; onConfirm: () => void;
}) {
  const trigger = useRef<HTMLElement | null>(null);
  return <AlertDialog.Root open={open} onOpenChange={value => { if (!value && !busy) onCancel(); }}><AlertDialog.Portal>
    <AlertDialog.Overlay className="fixed inset-0 z-[60] bg-black/40" />
    <AlertDialog.Content className="fixed left-1/2 top-1/2 z-[60] max-h-[85dvh] w-[calc(100%_-_2rem)] max-w-md -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border bg-background p-5 shadow-xl"
      onOpenAutoFocus={() => { trigger.current = document.activeElement as HTMLElement; }}
      onCloseAutoFocus={event => { event.preventDefault(); if (trigger.current?.isConnected) trigger.current.focus(); }}
      onEscapeKeyDown={event => { if (busy) event.preventDefault(); }}>
      <AlertDialog.Title className="text-base font-semibold">{title}</AlertDialog.Title>
      <AlertDialog.Description className="mt-3 whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">{description}</AlertDialog.Description>
      {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <AlertDialog.Cancel asChild><Button variant="outline" disabled={busy} onClick={onCancel}>取消</Button></AlertDialog.Cancel>
        <Button variant={danger ? "destructive" : "default"} disabled={busy} aria-busy={busy} onClick={onConfirm}>{busy ? "处理中…" : action}</Button>
      </div>
    </AlertDialog.Content>
  </AlertDialog.Portal></AlertDialog.Root>;
}
