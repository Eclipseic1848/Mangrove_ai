const aliases: Record<string, string> = { excel: "xlsx", word: "docx", ppt: "pptx", md: "markdown" };
const token = "(?:JSONL|JSON|XLSX|EXCEL|CSV|PARQUET|DOCX|WORD|PDF|PPTX|PPT|HTML|MARKDOWN|MD|TXT)(?![A-Za-z0-9_.])";
const list = `${token}(?:\\s*(?:、|,|，|和|与|及|以及|或|/|&|\\+)\\s*${token})*`;
const instruction = new RegExp(`(?:(不要|不需要|无需|不用|不必|不)\\s*)?(?:(只(?:需要|要)?|仅)\\s*)?(输出|导出|生成|保存|改为|改成|换成|提供|不要|不需要|无需|不用|不必)(?:的)?(?:文件)?(?:格式)?(?:为|成|是)?\\s*(${list})`, "gi");

export function outputIntent(text: string) {
  const requested: string[] = [], excluded: string[] = [];
  let only = false;
  // ponytail: 只识别明确格式指令，不把正文提及当要求；复杂表述仍由用户在输出选项中指定。
  const objective = text.split("当前用户要求（更正以此为准）：\n").pop()!.replace(/```[\s\S]*?```/g, "");
  for (const match of objective.matchAll(instruction)) {
    const values = [...match[4].matchAll(new RegExp(token, "gi"))].map(value => aliases[value[0].toLowerCase()] ?? value[0].toLowerCase());
    const negative = Boolean(match[1]) || /不要|不需要|无需|不用|不必/.test(match[3]);
    if (!negative && (match[2] || /改|换/.test(match[3]))) { requested.length = 0; only = true; }
    for (const value of values) {
      const target = negative ? excluded : requested, other = negative ? requested : excluded;
      const index = other.indexOf(value);
      if (index !== -1) other.splice(index, 1);
      if (!target.includes(value)) target.push(value);
    }
  }
  return { requested, excluded, only };
}

export function outputSelection(formats?: string[], selection?: "auto" | "manual") {
  // 旧草稿没有选择来源；只把已知旧默认组合迁回自动，保留其余已存选择。
  const oldDefault = !formats?.length || ["xlsx", "markdown", "docx,pdf"].includes([...formats].sort().join(","));
  return selection ?? (oldDefault ? "auto" : "manual");
}

export function resolveOutputFormats(text: string, manual: string[] | null = null, history: Array<{ role: string; content: string }> = []) {
  let intent = outputIntent(text);
  if (!intent.requested.length) {
    // 仅继承本会话用户的明确格式，不把助手建议当成用户要求。
    for (const message of [...history].reverse()) {
      if (message.role !== "user") continue;
      const previous = outputIntent(message.content);
      intent.excluded = [...new Set([...intent.excluded, ...previous.excluded])];
      if (previous.requested.length) {
        intent = { ...intent, only: intent.only || previous.only, requested: previous.requested.filter(value => !intent.excluded.includes(value)) };
        break;
      }
    }
  }
  // 自动模式按任务目标推荐一种格式，不再根据上传文件后缀固定勾选。
  const recommended = /表格|明细|合并|去重|筛选|分组|汇总数据/.test(text) ? "xlsx" : "markdown";
  const formats = manual ?? (intent.requested.length ? intent.requested
    : [[recommended], ["markdown"], ["txt"]].find(values => values.every(value => !intent.excluded.includes(value))) ?? []);
  const conflict = manual !== null && (intent.requested.some(value => !manual.includes(value))
    || intent.excluded.some(value => manual.includes(value))
    || (intent.only && manual.some(value => !intent.requested.includes(value))));
  return { formats, conflict, explicit: intent.requested.length > 0 };
}
