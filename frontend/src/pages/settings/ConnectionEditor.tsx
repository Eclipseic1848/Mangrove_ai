import { useEffect, useId, useState } from "react";
import { api, ApiError, getSessionState } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface Configuration {
  display_name: string; base_url: string; model: string; models: string[];
  api_format: string; locality: string; thinking: "default" | "on" | "off";
  version: string; has_key: boolean; superseded: boolean;
}

export function ConnectionEditor({ connectionId, onSaved }: { connectionId: string; onSaved: () => void }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [original, setOriginal] = useState<Configuration | null>(null);
  const [draft, setDraft] = useState<Configuration | null>(null);
  const [key, setKey] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [operation, setOperation] = useState("");
  const [state, setState] = useState("idle");
  const [message, setMessage] = useState("");
  const path = `/api/model-connections/${encodeURIComponent(connectionId)}/configuration`;
  const pendingKey = `model-configuration-operation:${getSessionState().user?.user_id || "anonymous"}:${connectionId}`;
  const busy = state === "testing" || state === "saving";
  const endpointChanged = draft?.base_url.trim().replace(/\/+$/, "") !== original?.base_url.trim().replace(/\/+$/, "");
  useEffect(() => {
    if (!open) return;
    let active = true;
    api.get(path).then(async value => {
      if (!active) return;
      setOriginal(value); setDraft(value);
      const pending = sessionStorage.getItem(pendingKey);
      if (pending) {
        setOperation(pending); setState("unknown");
        const result = await api.get(`${path}/operations/${encodeURIComponent(pending)}`);
        if (!active) return;
        if (result.state === "applied") { sessionStorage.removeItem(pendingKey); setOpen(false); onSaved(); return; }
        setDraft({ ...value, ...result.configuration }); setState(result.state === "testing" ? "unknown" : result.state);
        setMessage(result.state === "verified" ? "此前验证已通过，可保存配置。" : "此前验证尚未完成或未通过，可核对记录；不会自动重复请求。");
      }
    })
      .catch(error => { if (active) setMessage(error.message); });
    return () => { active = false; };
  }, [open, path]);
  const update = (value: Partial<Configuration>) => { setDraft(current => current && { ...current, ...value }); setState("idle"); setOperation(""); sessionStorage.removeItem(pendingKey); setMessage(""); };
  async function test() {
    if (!draft || busy) return;
    if (state === "unknown" && operation) {
      try {
        const result = await api.get(`${path}/operations/${encodeURIComponent(operation)}`);
        setState(result.state === "testing" ? "unknown" : result.state);
        setMessage(result.state === "verified" ? "验证通过，可以保存配置。" : "结果仍未确认。可关闭编辑稍后核对，不会自动重试。请先核对服务商用量再决定重新验证。");
      } catch (error) { setMessage(error instanceof Error ? error.message : "无法核对验证记录"); }
      return;
    }
    const operationId = state === "failed" ? crypto.randomUUID() : operation || crypto.randomUUID();
    sessionStorage.setItem(pendingKey, operationId);
    setOperation(operationId); setState("testing"); setMessage("正在验证，不会修改当前配置…");
    try {
      const result = await api.post(`${path}/test`, {
        ...draft, expected_version: original?.version, operation_id: operationId, api_key: key || null, confirm_endpoint_change: confirmed,
      });
      setState(result.state === "testing" ? "unknown" : result.state);
      setMessage(result.state === "verified" ? "验证通过。点击保存后用于新任务，历史任务不变。" : result.state === "testing" || result.state === "unknown" ? "验证结果尚未确认，可能已产生用量。不会重复发起模型请求，请稍后核对。" : result.message || "验证未通过，原配置未修改。请检查地址、密钥及模型权限。");
    } catch (error) {
      const rejected = error instanceof ApiError && [400, 404, 409, 422].includes(error.status);
      setState(rejected ? "idle" : "unknown");
      if (rejected) { setOperation(""); sessionStorage.removeItem(pendingKey); }
      setMessage(error instanceof Error ? error.message : "结果未知，请核对后再操作");
    }
  }
  async function save() {
    if (state !== "verified") return;
    setState("saving");
    try { await api.post(`${path}/apply`, { operation_id: operation }); sessionStorage.removeItem(pendingKey); setKey(""); setOpen(false); onSaved(); }
    catch (error) { setState("verified"); setMessage(error instanceof Error ? error.message : "保存结果未知，可再次核对保存；不会重复验证"); }
  }
  if (!open) return <Button variant="outline" size="sm" onClick={() => setOpen(true)}>编辑配置</Button>;
  return <section className="w-full space-y-3 rounded-lg border bg-muted/20 p-4" aria-label="编辑模型配置">
    <h3 className="font-medium">编辑模型配置</h3>
    <p className="text-sm text-muted-foreground">修改 → 验证 → 保存。验证会发送极短测试请求，可能产生少量用量；不会发送任务资料。</p>
    {!draft ? <p role="status">{message || "正在加载配置…"}</p> : <>
      <fieldset disabled={busy || draft.superseded || state === "unknown" || state === "testing"} className="grid gap-3 sm:grid-cols-2">
        <label htmlFor={`${id}-name`} className="space-y-1 text-sm">名称<Input id={`${id}-name`} value={draft.display_name} onChange={e => update({ display_name: e.target.value })} /></label>
        <label htmlFor={`${id}-url`} className="space-y-1 text-sm">API 地址<Input id={`${id}-url`} value={draft.base_url} onChange={e => { update({ base_url: e.target.value }); setConfirmed(false); }} /></label>
        <label htmlFor={`${id}-key`} className="space-y-1 text-sm">API Key{original?.has_key ? "（已配置，留空保留）" : "（本地无鉴权可留空）"}<Input id={`${id}-key`} type="password" autoComplete="new-password" value={key} onChange={e => { setKey(e.target.value); update({}); }} /></label>
        <label htmlFor={`${id}-models`} className="space-y-1 text-sm">模型 ID（多个用逗号分隔）<Input id={`${id}-models`} value={draft.models.join(", ")} onChange={e => { const models = e.target.value.split(/[,，]/).map(v => v.trim()); update({ models, model: models.includes(draft.model) ? draft.model : models.find(Boolean) || "" }); }} /></label>
        <label htmlFor={`${id}-model`} className="space-y-1 text-sm">连接首选模型<select id={`${id}-model`} className="h-10 w-full rounded-md border bg-background px-3" value={draft.model} onChange={e => update({ model: e.target.value })}>{draft.models.map((model, index) => <option key={index} value={model}>{model}</option>)}</select></label>
        {draft.locality === "managed_private" && <label htmlFor={`${id}-thinking`} className="space-y-1 text-sm">思考模式<select id={`${id}-thinking`} className="h-10 w-full rounded-md border bg-background px-3" value={draft.thinking} onChange={e => update({ thinking: e.target.value as Configuration["thinking"] })}><option value="default">模型默认</option><option value="on">开启（本地 Qwen）</option><option value="off">关闭（本地 Qwen）</option></select></label>}
      </fieldset>
      {endpointChanged && <label className="flex items-start gap-2 text-sm"><input type="checkbox" checked={confirmed} disabled={busy} onChange={e => setConfirmed(e.target.checked)} />我确认将验证请求和密钥发送到上方新地址</label>}
      {message && <p role="status" className="text-sm">{message}</p>}
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void test()} disabled={busy || draft.superseded || (endpointChanged && !confirmed)}>{busy ? "处理中…" : state === "unknown" ? "核对验证结果" : "验证配置"}</Button>
        <Button onClick={() => void save()} disabled={state !== "verified"}>保存配置</Button>
        {state === "unknown" && <Button variant="outline" onClick={() => { if (window.confirm("此前请求可能已产生用量。确认已核对服务商记录，并放弃本次待确认结果？这不会自动发送新请求。")) update({}); }}>已核对，重新编辑</Button>}
        <Button variant="outline" disabled={busy} onClick={() => { setKey(""); setOpen(false); setDraft(null); if (state !== "unknown") { setOperation(""); setState("idle"); } }}>关闭编辑</Button>
      </div>
    </>}
  </section>;
}
