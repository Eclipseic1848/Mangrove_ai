export type TaskModelConnection = {
  connection_id: string; owner_scope: string; display_name: string; status: string;
  model: string; models?: Array<{ model_id: string; display_name: string; enabled: boolean; status: string; current_catalog?: boolean }>;
  locality?: string; preset_id?: string | null;
};

export function modelConnectionSource(connection: TaskModelConnection) {
  const suppliers: Record<string, string> = { qwen: "阿里百炼", deepseek: "DeepSeek", openai: "OpenAI", anthropic: "Anthropic", gemini: "Google" };
  const isLocal = connection.locality === "managed_private" || connection.locality === "local";
  return { group: isLocal ? "本地模型" : "云端模型", supplier: isLocal ? "" : suppliers[connection.preset_id ?? ""] || connection.display_name };
}

// 任务入口与默认设置共用相同过滤；预设目录不是可用连接，不能作为兜底选项。
export function taskModelChoices(
  local: Array<{ provider: string; model: string }>, connections: TaskModelConnection[], allowLocal: boolean,
  frozen?: { connectionId: string | null; model: string | null },
  preferred?: { connectionId: string | null; model: string | null },
  includeUnavailable = false,
) {
  const choices = [
    ...(allowLocal ? local.filter(item => item.provider === "local").map(item => ({ connectionId: "__local__", model: item.model, label: item.model, group: "本地模型", supplier: "", personal: false, connectionName: "平台直连" })) : []),
    // 管理清单仍展示异常型号；优先展示同型号可用连接，不改变任务选择器的默认规则。
    ...connections.filter(connection => includeUnavailable || connection.status === "verified").flatMap(connection =>
      // 目录退役只影响新选型，历史资料修订保留指定的冻结身份；权限与可用性检查不放宽。
      (connection.models ?? []).filter(item => (includeUnavailable || (item.enabled && item.status === "available")) && (item.current_catalog !== false
        || (connection.connection_id === frozen?.connectionId && item.model_id === frozen.model)))
        .map(item => {
          return { connectionId: connection.connection_id, model: item.model_id, label: item.display_name || item.model_id,
            ...modelConnectionSource(connection), personal: connection.owner_scope === "user_personal",
            connectionName: connection.display_name, available: connection.status === "verified" && item.enabled && item.status === "available" };
        })),
  ];
  // 平台同型号只显示一次；优先保留当前选择，去重不意味着替换默认或历史连接。
  const identity = frozen ?? preferred;
  const selected = choices.find(item => item.connectionId === identity?.connectionId && item.model === identity?.model);
  const ordered = selected ? [selected, ...choices.filter(item => item !== selected)] : choices;
  if (includeUnavailable) ordered.sort((a, b) => Number("available" in b && b.available) - Number("available" in a && a.available));
  const seen = new Set<string>();
  const unique = ordered.filter(item => {
    if (item.personal) return true;
    const key = JSON.stringify([item.group, item.supplier, item.model.trim().toLowerCase()]);
    if (seen.has(key)) return false;
    seen.add(key); return true;
  });
  const hasPersonal = unique.some(item => item.personal);
  return unique.sort((a, b) => (a.group === b.group ? 0 : a.group === "本地模型" ? -1 : 1)
    || a.supplier.localeCompare(b.supplier, "zh-CN") || b.model.localeCompare(a.model, "en", { numeric: true, sensitivity: "base" }))
    .map(choice => {
      // ponytail: 当前选项规模很小；连接数上千后再改为分组索引。
      const duplicates = unique.filter(other => other.personal === choice.personal && other.group === choice.group && other.supplier === choice.supplier && other.model === choice.model);
      const sameName = duplicates.filter(other => other.connectionName === choice.connectionName).length > 1;
      const suffix = `${hasPersonal ? choice.personal ? " · 我的" : " · 平台" : ""}${duplicates.length > 1 ? ` · ${choice.connectionName}${sameName ? ` · ${choice.connectionId}` : ""}` : ""}`;
      return { ...choice, label: `${choice.supplier ? `${choice.supplier} · ` : ""}${choice.label}${suffix}` };
    });
}
