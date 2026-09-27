export interface ScheduledTask {
  time_zone?: string | null; execution_state?: string; blocked_reason?: string; can_recreate?: boolean;
  provider?: string | null; model?: string | null; model_connection_id?: string | null;
  task_id: string; name?: string | null; source?: string; status?: string;
  user_input: string; trigger_type: string; cron_expr?: string;
  interval_seconds?: number | null; run_at?: string; next_run_at?: string;
  start_date?: string | null; end_date?: string | null; run_count: number;
  last_success?: number | null; last_run_at?: string; last_result?: string; last_error?: string;
}

/** 只解释确定的简单规则；复杂 cron 保留原表达式，避免误报执行时间。 */
export function describeTrigger(t: Pick<ScheduledTask, "trigger_type" | "cron_expr" | "interval_seconds" | "run_at">): string {
  if (t.trigger_type === "once") return `单次 ${(t.run_at || "").replace("T", " ").slice(0, 16)}`;
  if (t.trigger_type === "interval") {
    const seconds = t.interval_seconds || 0;
    if (seconds <= 0) return "间隔未设置";
    if (seconds % 3600 === 0) return `每 ${seconds / 3600} 小时`;
    if (seconds % 60 === 0) return `每 ${seconds / 60} 分钟`;
    return `每 ${seconds} 秒`;
  }
  const expr = (t.cron_expr || "").trim();
  const [mm, hh, dom, mon, dow, extra] = expr.split(/\s+/);
  if (!extra && /^\d+$/.test(mm) && /^\d+$/.test(hh) && Number(mm) < 60 && Number(hh) < 24) {
    const time = `${hh.padStart(2, "0")}:${mm.padStart(2, "0")}`;
    if (dom === "*" && mon === "*" && dow === "*") return `每天 ${time}`;
    if (dom === "*" && mon === "*" && /^[0-7](,[0-7])*$/.test(dow)) {
      return `每周${dow.split(",").map(day => "日一二三四五六"[Number(day) % 7]).join("、")} ${time}`;
    }
    if (/^\d+$/.test(dom) && Number(dom) >= 1 && Number(dom) <= 31 && mon === "*" && dow === "*") return `每月 ${dom} 号 ${time}`;
  }
  return expr ? `Cron ${expr}` : "规则未设置";
}
