import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuth, isAdminish } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { taskModelChoices, type TaskModelConnection } from "@/lib/taskModelChoices";

export function TaskModelSettings({ refreshToken = 0, onSaved }: { refreshToken?: number; onSaved?: () => void } = {}) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [catalog, setCatalog] = useState<{ local: Array<{ provider: string; model: string }>; connections: TaskModelConnection[] }>({ local: [], connections: [] });
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const edited = useRef(false);
  const request = useRef(0);
  const key = (connectionId: string, model: string) => JSON.stringify([connectionId, model]);
  async function load() {
    const generation = ++request.current;
    setLoading(true); setError("");
    try {
      const [local, connections, preference] = await Promise.all([
        api.get("/api/models"), api.get("/api/model-connections"), api.get("/api/model-connections/preferences/default"),
      ]);
      if (generation !== request.current) return;
      const current = preference.preference ? key(preference.preference.connection_id, preference.preference.model_id) : "";
      setCatalog({ local: local.options ?? [], connections: connections.items ?? [] });
      setSaved(current);
      if (!edited.current) setValue(current);
    } catch {
      if (generation === request.current) setError("模型列表加载失败，已保留当前选择。请重新刷新。");
    } finally { if (generation === request.current) setLoading(false); }
  }
  useEffect(() => { void load(); return () => { request.current++; }; }, [user?.user_id, refreshToken]);
  // 去重随当前编辑身份推导；取消改选后，原保存身份也能从完整目录恢复。
  const [connectionId, model] = value ? JSON.parse(value) : [null, null];
  const choices = taskModelChoices(catalog.local, catalog.connections, isAdminish(user?.role), undefined, { connectionId, model });
  const selected = choices.find(item => key(item.connectionId, item.model) === value);
  const unavailable = Boolean(value && !selected);
  async function save() {
    if (saving || loading || error || unavailable || value === saved) return;
    const submitted = value;
    setSaving(true); setMessage(""); setError("");
    try {
      if (submitted) {
        const [connection_id, model_id] = JSON.parse(submitted);
        await api.put("/api/model-connections/preferences/default", { connection_id, model_id });
      } else await api.del("/api/model-connections/preferences/default");
      setSaved(submitted); edited.current = false;
      setMessage("已保存，仅对新任务生效。");
      void queryClient.invalidateQueries({ queryKey: ["model-connection-preference"] });
      onSaved?.();
    } catch { setError("保存未确认，请刷新核对当前默认模型；你的选择仍保留。"); }
    finally { setSaving(false); }
  }
  return <section aria-labelledby="task-model-title" className="rounded-lg border bg-card p-4 text-card-foreground">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h2 id="task-model-title" className="font-semibold">任务默认模型</h2>
      <Button variant="outline" size="sm" disabled={loading || saving} onClick={() => void load()}>刷新模型列表</Button>
    </div>
    <p className="mt-2 text-sm text-muted-foreground">用于新建任务；当前任务与历史任务保持原模型。任务中仍可临时切换。</p>
    <label htmlFor="task-default-model" className="mb-2 mt-4 block text-sm">默认任务模型</label>
    <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
      <select id="task-default-model" value={value} disabled={loading || saving} aria-describedby="task-model-help" aria-invalid={unavailable}
        onChange={event => { edited.current = true; setValue(event.target.value); setMessage(""); }}
        className="h-10 min-w-0 rounded-md border bg-background px-3 text-sm focus-visible:ring-2 focus-visible:ring-ring sm:flex-1">
        <option value="">自动选择可用模型</option>
        {unavailable && <option value={value} disabled>原默认模型已不可用，请重新选择</option>}
        {Array.from(new Set(choices.map(item => item.group))).map(group => <optgroup key={group} label={group}>
          {choices.filter(item => item.group === group).map(item => <option key={key(item.connectionId, item.model)} value={key(item.connectionId, item.model)}>{item.label}</option>)}
        </optgroup>)}
      </select>
      <Button disabled={loading || saving || Boolean(error) || unavailable || value === saved} onClick={() => void save()}>{saving ? "正在保存…" : "保存默认模型"}</Button>
      {value !== saved && <Button variant="outline" disabled={saving} onClick={() => { edited.current = false; setValue(saved); setMessage(""); }}>取消修改</Button>}
    </div>
    <p id="task-model-help" className="mt-2 text-xs text-muted-foreground">{unavailable ? "原默认模型已不可用，不会自动替换为其他模型。" : "只展示已配置、已启用且你有权使用的模型。"}</p>
    {loading && <p role="status" className="mt-2 text-sm">正在加载模型…</p>}
    {!loading && !choices.length && !error && <p className="mt-2 text-sm">暂无可用模型。请到“模型与连接”配置，或联系管理员。</p>}
    {selected && selected.connectionId !== "__local__" && <p className="mt-2 text-xs text-muted-foreground">资料将通过所选模型连接处理，任务执行前会展示对应的数据发送说明。</p>}
    {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
    {message && <p role="status" className="mt-2 text-sm text-primary">{message}</p>}
  </section>;
}
