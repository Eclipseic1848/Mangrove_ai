import { useId, useRef, useState } from "react";
import { ArrowRight, CalendarClock, ChevronDown, FileSearch, Globe, Table2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type TaskExample = { title: string; prompt: string; note: string };
const categories: { title: string; icon: typeof Globe; description: string; summary: string; examples: TaskExample[] }[] = [
  {
    title: "信息采集与分析", icon: Globe, description: "找信息、看口碑，把网页整理成报告。", summary: "口碑 · 站内检索 · 网页 · 新闻",
    examples: [
      { title: "用户口碑", prompt: "采集5条小米SU7的用户评价，分析用户口碑和主要槽点，生成 Markdown 报告。", note: "可修改产品和数量；部分平台可能需要有效登录态。" },
      { title: "站内检索", prompt: "去懂车帝搜集3条问界M9的资讯并总结要点。", note: "可替换车型；实际结果取决于网站可访问性。" },
      { title: "指定网页", prompt: "抓取这个网页的正文并总结今日财经要闻：https://finance.sina.com.cn/", note: "可直接替换为你想分析的公开网页地址。" },
      { title: "新闻汇总", prompt: "采集3条关于新能源汽车销量的最新新闻，生成汇总报告。", note: "可修改关注的话题和新闻数量。" },
    ],
  },
  {
    title: "文件解析与提取", icon: FileSearch, description: "读懂文档，提取关键字段和明细。", summary: "报销单 → JSON / CSV",
    examples: [{ title: "报销单提取", prompt: "请帮我整理第5份报销单中的部门、报销人、事由、报销明细、小计、合计和结算金额，输出 JSON 和 CSV 文件。", note: "需上传包含目标报销单的文件；可修改报销单序号。" }],
  },
  {
    title: "数据整理与治理", icon: Table2, description: "合并、去重、查冲突，让数据可用。", summary: "订单表 → 整理后的 Excel",
    examples: [{ title: "合并订单并去重", prompt: "合并上传的同结构订单表，按订单编号去重并保留原始字段；同编号内容有冲突时先列出并问我，最后输出 Excel。", note: "需上传 Excel 或 CSV；不会擅自覆盖原件或丢弃冲突数据。" }],
  },
  {
    title: "定时执行与邮件交付", icon: CalendarClock, description: "说出执行时间或收件邮箱，交给 Mangrove。", summary: "自然语言安排 · 完成后发送",
    examples: [
      { title: "周期性标讯", prompt: "每周一三五 9:30 搜集3条医疗设备招标公告，整理成标讯报告。", note: "用自然语言说明周期和时间；信息不明确时会继续询问。" },
      { title: "发送结果邮件", prompt: "整理上传的报销单，输出汇总报告和明细文件，完成后把报告正文和附件发送到我的邮箱；请先问我收件邮箱。", note: "可直接写明收件邮箱；完成后发送。也可在正式结果中点击“发送结果”。" },
    ],
  },
];

export function WorkspaceExamples({ hasPrompt, onFill }: { hasPrompt: boolean; onFill: (prompt: string) => void }) {
  const [categoryIndex, setCategoryIndex] = useState<number | null>(null);
  const [exampleIndex, setExampleIndex] = useState(0);
  const categoryButtons = useRef<(HTMLButtonElement | null)[]>([]);
  const detailId = useId();
  const category = categoryIndex === null ? null : categories[categoryIndex];
  const example = category?.examples[exampleIndex];
  return (
    <section aria-label="任务示例" className="mx-auto w-full max-w-3xl">
      <div className="mb-7 text-center">
        <img src="/logo.svg" alt="Mangrove" width={44} height={44} className="mx-auto h-11 w-11" />
        <h2 className="mt-4 text-2xl font-semibold tracking-tight">今天想完成什么？</h2>
        <p className="mt-2 text-sm text-muted-foreground">从信息到结果，直接说出你的需求。</p>
      </div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs">
        <p className="font-medium">选一个方向，看看任务示例</p><span className="text-muted-foreground">4 大类 · 8 个示例</span>
      </div>
      <div role="group" aria-label="任务方向" className="grid gap-3 sm:grid-cols-2">
        {categories.map((item, index) => (
          <button key={item.title} ref={el => { categoryButtons.current[index] = el; }} type="button" aria-label={item.title}
            aria-expanded={categoryIndex === index} aria-controls={categoryIndex === index ? detailId : undefined}
            onClick={() => { setCategoryIndex(categoryIndex === index ? null : index); setExampleIndex(0); }}
            className={cn("flex cursor-pointer gap-3 rounded-xl border p-4 text-left transition-colors hover:border-primary/50 hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", categoryIndex === index ? "border-primary bg-primary/5 shadow-[inset_3px_0_0_hsl(var(--primary))]" : "dark:bg-card")}>
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"><item.icon aria-hidden="true" className="h-5 w-5" /></span>
            <span className="min-w-0 flex-1">
              <span className="block text-base font-semibold">{item.title}</span>
              <span className="mt-1 block text-xs leading-5 text-foreground/75">{item.description}</span>
              <span className="mt-3 flex items-center justify-between gap-2 text-xs text-primary">{item.summary}<ChevronDown aria-hidden="true" className={cn("h-4 w-4 shrink-0", categoryIndex === index && "rotate-180")} /></span>
            </span>
          </button>
        ))}
      </div>
      {category && example && <div id={detailId} className="mt-3 rounded-xl border border-primary/25 bg-primary/5 p-4" role="region" aria-label={`${category.title}任务示例`}>
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">{category.title} · 任务示例</h3>
          <Button variant="ghost" size="sm" onClick={() => { categoryButtons.current[categoryIndex!]?.focus(); setCategoryIndex(null); }}>收起示例</Button>
        </div>
        {category.examples.length > 1 && <div className="mt-3 flex flex-wrap gap-2">
          {category.examples.map((item, index) => <Button key={item.title} variant={exampleIndex === index ? "secondary" : "outline"} size="sm" aria-pressed={exampleIndex === index} onClick={() => setExampleIndex(index)}>{item.title}</Button>)}
        </div>}
        <p className="mt-3 break-words text-sm leading-6">{example.prompt}</p>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <p className="flex-1 basis-60 text-xs leading-5 text-foreground/75">{example.note}</p>
          <Button size="sm" className="hover:bg-primary" onClick={() => onFill(example.prompt)}>{hasPrompt ? "追加到需求" : "填入需求"}<ArrowRight aria-hidden="true" /></Button>
        </div>
      </div>}
    </section>
  );
}
