import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { api } from "@/lib/api";

export function ResultNotification({ taskId, revision, outputs, onSent }: {
  taskId: string; revision: number;
  outputs: { output_id: string; filename: string }[];
  onSent: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [channel, setChannel] = useState("email");
  const [recipients, setRecipients] = useState("");
  const [body, setBody] = useState(true);
  const [attachments, setAttachments] = useState(true);
  const [selected, setSelected] = useState(outputs.map(item => item.output_id));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [submitted, setSubmitted] = useState("");
  const selectionKey = JSON.stringify([channel, recipients, body, attachments, selected]);
  const pending = useRef(false);
  async function send() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true); setMessage("正在发送，请勿重复提交…");
    try {
      const result = await api.post(`/api/semantic-workspace/tasks/${taskId}/notify`, {
        expected_revision: revision, channel, recipients: channel === "email" ? recipients.split(/[,;，；\s]+/).filter(Boolean) : [],
        include_body: body, include_attachments: attachments, output_ids: selected,
      });
      setMessage(result.message);
      if (result.status === "sent" || result.status === "unknown") setSubmitted(selectionKey);
      onSent();
    } catch {
      setMessage("未确认发送成功，请查看工作记录并向接收方核对；不要直接重复发送。");
      setSubmitted(selectionKey);
    } finally { pending.current = false; setBusy(false); }
  }
  return <>
    <Button variant="outline" size="sm" onClick={() => setOpen(true)}>发送结果</Button>
    <Modal open={open} onClose={() => { if (!busy) setOpen(false); }} title="发送正式结果">
      <div className="space-y-4">
        <label className="block text-sm">发送方式<select className="mt-1 w-full rounded border bg-background p-2" value={channel} onChange={event => setChannel(event.target.value)} disabled={busy}>
          <option value="email">邮件</option><option value="slack">Slack 已配置频道</option>
        </select></label>
        {channel === "email" && <label className="block text-sm">收件邮箱<Input className="mt-1" value={recipients} onChange={event => setRecipients(event.target.value)} placeholder="多个邮箱用逗号分隔" disabled={busy} /></label>}
        <div className="flex gap-4 text-sm">
          <label><input type="checkbox" checked={body} onChange={event => setBody(event.target.checked)} disabled={busy} /> 报告正文</label>
          <label><input type="checkbox" checked={attachments} onChange={event => setAttachments(event.target.checked)} disabled={busy} /> 文件附件</label>
        </div>
        <fieldset className="max-h-48 space-y-2 overflow-auto rounded border p-3 text-sm"><legend>选择结果文件</legend>{outputs.map(item => <label key={item.output_id} className="flex items-center gap-2 break-all">
          <input type="checkbox" disabled={busy} checked={selected.includes(item.output_id)} onChange={event => setSelected(current => event.target.checked ? [...current, item.output_id] : current.filter(id => id !== item.output_id))} />{item.filename}
        </label>)}</fieldset>
        <p className="text-xs text-muted-foreground">正文取自所选文本报告；PDF、Word 或 Excel 文件请选“文件附件”。</p>
        <p className="text-xs text-muted-foreground">点击发送即授权将所选结果交给接收方。同一次发送重复提交不会重发；服务器接收不代表收件人已阅读。</p>
        {message && <p role="status" className="text-sm">{message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>关闭</Button>
          <Button className="hover:bg-primary" disabled={busy || submitted === selectionKey || !selected.length || (!body && !attachments) || (channel === "email" && !recipients.trim())} onClick={() => void send()}>{busy ? "发送中…" : "确认发送"}</Button>
        </div>
      </div>
    </Modal>
  </>;
}
