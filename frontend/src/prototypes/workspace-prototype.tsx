import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as Dialog from "@radix-ui/react-dialog";
import { Group, Panel, Separator } from "react-resizable-panels";
import { ArrowDown, ArrowUp, BookOpen, ChevronRight, Clock3, Database, Download, FileText, FolderOpen, Leaf, Maximize2, Menu, MessageSquare, Minimize2, Moon, Plus, Search, Settings, ShieldCheck, Square, Sun, X } from "lucide-react";
import { Button } from "../components/ui/button";
import "../index.css";
import "./workspace-prototype.css";

const scenarios = [
  { name: "文件分析", title: "门店销售分析", prompt: "分析这份门店销售资料，找出值得关注的营收变化，生成一份复盘文档。", question: "资料包含营收和退货。你更想先看营收变化，还是退货原因？", answer: "先看营收变化，按门店对比" },
  { name: "无 URL 搜索", title: "社区零售观察", prompt: "帮我检索社区零售的近期变化，整理可核对的观察，不限定网址。", question: "你希望关注哪一地区？", answer: "关注华东地区" },
  { name: "混合资料", title: "门店与市场对照", prompt: "结合门店销售资料与公开行业信息，比较营收变化，生成报告，区分事实和推测。", question: "公开信息只作为背景。要以哪个月份为基准？", answer: "以八月为基准" },
  { name: "部分结果", title: "门店资料核对", prompt: "汇总三份门店资料。个别来源失败时，先给我可核对的部分结果。", question: "南岸店资料暂时不可用。是否先处理其余两家，并明确列出缺口？", answer: "先处理可用资料，保留缺口" },
  { name: "扫码失效 / 恢复", title: "登录来源读取", prompt: "读取我获准查看的来源资料，登录完成后继续当前任务。", question: "只读本次选定的资料，不获得发布或账号管理权限。确认读取这个范围吗？", answer: "确认本次只读范围" },
  { name: "关联删除", title: "整理任务与关联资料", prompt: "清理这个演示任务，先查看它引用的资料和其他受影响任务。", question: "销售原始资料还被「季度复盘」引用。先查看关联，再选择删除范围。", answer: "查看关联与删除范围" },
];
type Stage = "start" | "clarify" | "login" | "expired" | "running" | "stopping" | "stopped" | "unknown" | "done" | "deleted";
type Result = { version: number; scenario: number; title: string; summary: string; note: string; source: string; gap: string };
type ChatMessage = { role: "user" | "assistant"; text: string; kind?: "search" | "result"; version?: number; model?: string };
type PreviewFile = { id: string; name: string; kind: "sales" | "notes" | "web" | "result"; version?: number };
const stages: Record<Stage, string> = { start: "准备就绪", clarify: "等待你确认", login: "等待登录", expired: "登录示意已失效", running: "正在处理", stopping: "停止请求待确认", stopped: "演示已停止", unknown: "结果未知", done: "已完成", deleted: "演示任务已删除" };
const salesRows = [["青禾店", "128,000", "+12.3%", "客单价提高"], ["北湾店", "96,000", "−4.0%", "到店人数减少"]];
const columns = ["门店", "营收（元）", "环比", "待核对"];
const examples: PreviewFile[] = [{ id: "sales", name: "门店销售示例.csv", kind: "sales" }, { id: "notes", name: "门店访谈摘录.md", kind: "notes" }];
const webFile: PreviewFile = { id: "web", name: "社区零售观察.md", kind: "web" };
const auxiliary: Record<string, string> = {
  模板: "本人模板：门店月度复盘。评分、晋级、淘汰与通用副本确认由后续辅助页面接线；这里不发布模板。",
  个人记忆: "本人偏好：先给结论，再列来源。个人记忆只对本人可见；原型不保存或自动共享。",
  自动任务: "尚未安排自动运行。调度界面将在后续工单接线；本原型不会设置后台任务或向外部系统发送结果。",
  反馈: "可以在本地标记有帮助。真实评分、备注保存与审计由后续辅助页面接线；本次反馈不会上传。",
  设置: "当前可切换明暗主题。真实模型连接、账号与凭据管理由后续页面接线；此处不收集密钥。",
  管理: "普通用户不获得管理员权限。管理元数据与经审计业务正文保持不同读取边界；此处没有真实账号或管理数据。",
};

function WorkspacePrototype() {
  const [scenario, setScenario] = useState(0);
  const [stage, setStage] = useState<Stage>("start");
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [files, setFiles] = useState<PreviewFile[]>([]);
  const [selectedFile, setSelectedFile] = useState("");
  const [preview, setPreview] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [mobileView, setMobileView] = useState("conversation");
  const [nav, setNav] = useState(true);
  const [dark, setDark] = useState(false);
  const [modal, setModal] = useState<string | null>(null);
  const [results, setResults] = useState<Result[]>([]);
  const [selectedVersion, setSelectedVersion] = useState(1);
  const [sharedDelete, setSharedDelete] = useState(false);
  const [notice, setNotice] = useState("");
  const [sourceDeleted, setSourceDeleted] = useState(false);
  const [loginConfirmed, setLoginConfirmed] = useState(false);
  const [retryState, setRetryState] = useState<"idle" | "retrying" | "failed">("idle");
  const [model, setModel] = useState("澄明 · 快速");
  const [demoTools, setDemoTools] = useState(false);
  const [nearBottom, setNearBottom] = useState(true);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const conversationRef = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const previewTrigger = useRef<HTMLElement | null>(null);
  const pending = useRef({ scenario: 0, generate: false, supported: false, query: "", model: "澄明 · 快速" });
  const finishRef = useRef(() => {});
  const busy = ["running", "stopping", "login", "expired", "unknown", "deleted"].includes(stage);
  const attached = files.some((file) => file.kind === "sales" || file.kind === "notes");
  const activeFile = files.find((file) => file.id === selectedFile);
  const result = results.find((item) => item.version === selectedVersion);
  const title = messages[0]?.text || "新任务";
  const s = scenarios[scenario];

  useLayoutEffect(() => {
    if (!inputRef.current) return;
    inputRef.current.style.height = "auto";
    inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 144)}px`;
  }, [input]);
  useLayoutEffect(() => {
    // 只有仍在底部时跟随新反馈，保留用户上翻核对的位置。
    if (nearBottom) conversationRef.current?.scrollTo({ top: conversationRef.current.scrollHeight, behavior: "auto" });
  }, [messages, stage, retryState, nearBottom]);
  useEffect(() => {
    if (stage !== "running") return;
    const timer = window.setTimeout(() => finishRef.current(), 900);
    // 停止、重置与故障会撤销这次模拟完成，避免迟到结果覆盖新状态。
    return () => window.clearTimeout(timer);
  }, [stage]);
  const openModal = (name: string) => {
    if (!modal) previousFocus.current = document.activeElement as HTMLElement;
    setModal(name);
  };
  const openFile = (file: PreviewFile, automatic = false) => {
    setFiles((old) => old.some((item) => item.id === file.id) ? old : [...old, file]);
    // 自动生成不能抢走用户正在看的旧文件；主动点选才切换。
    if (!automatic || !preview) {
      previewTrigger.current = document.activeElement as HTMLElement;
      setSelectedFile(file.id);
      if (file.version) setSelectedVersion(file.version);
      setMobileView("preview");
    }
    setPreview(true);
  };
  const closePreview = () => {
    setPreview(false); setExpanded(false); setMobileView("conversation");
    requestAnimationFrame(() => (previewTrigger.current?.isConnected ? previewTrigger.current : inputRef.current)?.focus());
  };
  const reset = (next = 0, prepare = false) => {
    setScenario(next); setStage("start"); setInput(prepare ? scenarios[next].prompt : ""); setMessages([]); setError("");
    setFiles(prepare && [0, 2, 3].includes(next) ? [examples[0]] : []);
    setSelectedFile(prepare && [0, 2, 3].includes(next) ? "sales" : "");
    setPreview(prepare && [0, 2, 3].includes(next)); setMobileView("conversation");
    setResults([]); setSelectedVersion(1); setSourceDeleted(false); setNotice(""); setSharedDelete(false); setExpanded(false);
    setLoginConfirmed(false); setRetryState("idle"); setNearBottom(true);
  };
  const submit = (value = input, forcedScenario?: number) => {
    if (busy) return;
    const query = value.trim();
    if (!query) { setError("请先描述你想完成的事情。"); inputRef.current?.focus(); return; }
    const nextScenario = forcedScenario ?? (scenario >= 2 ? scenario : /搜索|检索|社区零售|行业/.test(query) ? 1 : /门店|销售|文件|附件/.test(query) ? 0 : scenario);
    // ponytail: 关键词只路由预置示例，真实意图理解留给后续模型接线。
    const supported = forcedScenario !== undefined || (stage === "start" && scenario >= 2) || /门店|销售|社区零售/.test(query) || ((attached || messages.some((message) => message.kind)) && /分析|汇总|生成|导出|文档|报告|修订|核对|原因|结论|继续|补充|资料|为什么|这些|它们/.test(query));
    const generate = supported && (nextScenario === 3 || nextScenario === 4 || /生成|导出|文档|报告|修订|补充.*核对/.test(query) || (attached && nextScenario !== 1 && results.length === 0));
    // 澄清与登录恢复沿用原任务，不重写文件生成、来源范围和模型快照。
    const resumingLogin = stage === "stopped" && scenario === 4 && !loginConfirmed;
    if (stage !== "clarify" && !resumingLogin) pending.current = { scenario: nextScenario, generate, supported, query, model };
    setScenario(nextScenario); setMessages((old) => [...old, { role: "user", text: query }]);
    setInput(""); setError(""); setNearBottom(true);
    if (stage === "clarify" && /取消|不要|不确认|不同意|暂不/.test(query)) {
      setStage("stopped"); return;
    }
    if (stage === "clarify" && nextScenario === 4 && !/确认.*只读/.test(query)) {
      setMessages((old) => [...old, { role: "assistant", model, text: "尚未确认读取范围。请明确确认本次只读范围，或取消读取。" }]); return;
    }
    if (stage === "start" && nextScenario >= 3) {
      setMessages((old) => [...old, { role: "assistant", text: scenarios[nextScenario].question, model }]); setStage("clarify");
    } else if (stage === "clarify") advance(false);
    else setStage(nextScenario === 4 && !loginConfirmed ? "login" : "running");
  };
  const advance = (addMessage = true) => {
    // 确认时采用当前选择；进入运行后沿用此快照，任务内容保持不变。
    pending.current = { ...pending.current, model };
    if (addMessage) setMessages((old) => [...old, { role: "user", text: s.answer }]);
    if (scenario === 4 && !loginConfirmed) setStage("login");
    else if (scenario === 5) openModal("关联删除");
    else setStage("running");
  };
  function finish() {
    const work = pending.current;
    const isSearch = work.scenario === 1;
    const summary = isSearch ? "示例资料中有两个值得继续核对的方向：更近的采购距离，以及更灵活的配送服务。它们是观察线索，还不能视为已经核实的市场趋势。" : "青禾店营收环比上升 12.3%，北湾店下降 4.0%。先核对客单价与到店人数，再判断变化原因。";
    if (!work.supported) {
      setMessages((old) => [...old, { role: "assistant", model: work.model, text: `收到你的问题「${work.query}」。当前是离线交互原型，只内置了门店销售与社区零售示例，无法真实检索或回答这个主题。你可以继续描述要求体验对话，或添加示例文件体验分析与预览。` }]);
    } else if (work.generate) {
      const version = results.length + 1;
      const next: Result = { version, scenario: work.scenario, title: isSearch ? "社区零售观察" : "门店观察", summary, source: isSearch ? "社区零售观察（虚构原文）" : work.scenario === 2 ? "门店销售示例.csv + 行业观察" : "门店销售示例.csv", note: version > 1 ? "新增核对事项：核对样本覆盖范围、时间口径及原始记录，保留原有结论。" : "", gap: work.scenario === 3 ? "缺口：南岸店资料不可用，结论仅覆盖两家门店。不能视为三店完整结果。" : "" };
      setResults((old) => [...old, next]);
      setMessages((old) => [...old, { role: "assistant", text: summary, kind: "result", version, model: work.model }]);
      openFile({ id: `result-${version}`, name: `${next.title} · v${version}.md`, kind: "result", version }, true);
      setNotice(`已生成版本 ${version}。${preview ? "当前预览保持不变，可在文件标签中打开新版本。" : "文件已在右侧打开。"}`);
    } else {
      setMessages((old) => [...old, { role: "assistant", text: summary, kind: isSearch ? "search" : undefined, model: work.model }]);
      setNotice("回复已完成，可以继续追问。");
    }
    setStage("done");
  }
  finishRef.current = finish;
  const download = (format: "md" | "csv" | "json") => {
    if (!result) return;
    const heading = `虚构演示数据 · ${result.title} · 版本 ${result.version}`;
    const mixedNote = result.scenario === 2 ? "文件原始行提供营收，网页原文仅提供行业背景，不能据此认定因果关系。" : "";
    const searchRows = [["社区小店", "采购距离", "待核对"], ["配送服务", "履约投入", "待核对"]];
    const exportRows = result.scenario === 1 ? searchRows : salesRows;
    const exportColumns = result.scenario === 1 ? ["主题", "观察", "证据状态"] : columns;
    const body = { ...result, notice: heading, mixedNote, columns: exportColumns, rows: exportRows };
    const data = format === "json" ? JSON.stringify(body, null, 2) : format === "csv" ? `\uFEFF${heading}\n来源：${result.source}（虚构）\n${result.gap}\n${mixedNote}\n${result.note}\n${exportColumns.join(",")}\n${exportRows.map((row) => row.map((v) => `"${v}"`).join(",")).join("\n")}` : `# ${heading}\n\n${result.summary}\n\n${result.note}\n\n${mixedNote}\n\n来源：${result.source}（虚构）\n${result.gap}\n\n仅为演示文件，不是正式交付。`;
    const url = URL.createObjectURL(new Blob([data], { type: format === "json" ? "application/json;charset=utf-8" : "text/plain;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `虚构演示-版本${result.version}.${format}`; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000); setNotice(`已下载版本 ${result.version} 的 ${format.toUpperCase()} 虚构演示文件。`);
  };
  const table = <div className="ux-table-wrap" role="region" aria-label="销售资料表格，可横向滚动" tabIndex={0}><table><caption>原始行定位 · 第 2—3 行 · 虚构数据</caption><thead><tr>{columns.map((column) => <th scope="col" key={column}>{column}</th>)}</tr></thead><tbody>{salesRows.map((row) => <tr key={row[0]}>{row.map((value) => <td key={value}>{value}</td>)}</tr>)}</tbody></table></div>;
  const navigation = <>
    <div className="ux-brand"><img src="/logo.svg" alt="" width="26" height="26" /><strong>Mangrove</strong></div>
    <button className="ux-new" onClick={() => { reset(); setModal(null); }}><Plus />新任务<span>＋</span></button>
    <p className="ux-caption">最近任务</p>
    <button className="ux-task active" onClick={() => { setMobileView("conversation"); setModal(null); }}><MessageSquare /><span>{messages.length ? s.title : "新任务"}<small>{stages[stage]}</small></span></button>
    <button className="ux-task" onClick={() => openModal("关联任务")}><MessageSquare /><span>季度复盘<small>{sourceDeleted ? "依赖已暂停 · 来源已删除" : "上次打开 · 昨天"}</small></span></button>
    <div className="ux-nav-bottom">{([["本人资料", FolderOpen], ["模板", BookOpen], ["个人记忆", Database], ["自动任务", Clock3], ["反馈", MessageSquare], ["设置", Settings], ["管理", ShieldCheck]] as const).map(([name, Icon]) => <button key={name} onClick={() => openModal(name)}><Icon />{name}</button>)}</div>
    <div className="ux-profile"><span>林</span><div>演示用户<small>个人工作空间</small></div><ShieldCheck /></div>
  </>;
  const searchContent = <div className="ux-search-results" aria-label="检索结果">
    <p className="ux-fine">2 条内置来源 · 虚构内容，未联网检索</p>
    <article><span className="ux-source-meta">社区商业观察 · 内置示例 · 2026-09-07</span><h3><button onClick={() => openFile(webFile)}>社区小店：采购半径与日常便利<ChevronRight /></button></h3><p>虚构原文提到就近采购；尚未核验样本是否具有代表性。</p></article>
    <article><span className="ux-source-meta">零售服务笔记 · 内置示例 · 2026-09-07</span><h3><button onClick={() => openFile(webFile)}>配送服务：便利背后的履约要求<ChevronRight /></button></h3><p>配送覆盖与时段存在差异，不能直接推断销售增长。</p></article>
  </div>;

  return <div className={`ux01 ux-root ${dark ? "dark" : ""}`}>
    <div className={`ux-shell ${nav ? "" : "ux-nav-collapsed"}`}>
      {nav && <aside className="ux-sidebar" aria-label="任务导航">{navigation}</aside>}
      <main className="ux-main">
        <header className="ux-header"><button className="ux-desktop-menu ux-icon" aria-label={nav ? "收起导航" : "展开导航"} onClick={() => setNav(!nav)}><Menu /></button><button className="ux-mobile-menu ux-icon" aria-label="打开任务导航" onClick={() => openModal("任务导航")}><Menu /></button><div className="ux-title"><h1 title={title}>{stage === "deleted" ? "任务已删除" : title}</h1><span>仅本人可见</span></div><span className="ux-prototype">交互原型 · 虚构数据</span><button className="ux-icon" aria-label={dark ? "切换浅色主题" : "切换深色主题"} onClick={() => setDark(!dark)}>{dark ? <Sun /> : <Moon />}</button><button className="ux-usage" aria-label="用量" onClick={() => openModal("用量")}><Clock3 /><span>用量</span></button>{files.length > 0 && <button className="ux-file-toggle" onClick={() => { if (preview) closePreview(); else { setPreview(true); setMobileView("preview"); } }} aria-label="打开文件预览" aria-pressed={preview}><FileText /><span>{files.length}</span></button>}</header>
        {preview && <nav className="ux-mobile-views" aria-label="工作区视图"><button aria-pressed={mobileView === "conversation"} onClick={() => { setMobileView("conversation"); setExpanded(false); }}>查看对话</button><button aria-pressed={mobileView === "preview"} onClick={() => setMobileView("preview")}>查看文件 · {files.length}</button></nav>}
        <Group orientation="horizontal" className={`ux-workspace ${preview ? "ux-has-preview" : ""} ${preview && expanded ? "ux-preview-expanded" : ""} ux-mobile-${mobileView}`}>
          <Panel id="conversation" minSize="32%" defaultSize="52%" className="ux-chat-panel">
            <section className="ux-conversation" aria-label="连续对话">
              <div className="ux-conversation-scroll" ref={conversationRef} onScroll={(e) => { const node = e.currentTarget; setNearBottom(node.scrollHeight - node.scrollTop - node.clientHeight < 90); }}>
                {messages.length === 0 && <div className="ux-intro"><span className="ux-intro-mark"><img src="/logo.svg" alt="" width="38" height="38" /></span><h2>今天，想把什么理清楚？</h2><p>从一个问题开始。需要时，再把资料放在一旁。</p><div className="ux-starters"><button onClick={() => submit(scenarios[1].prompt, 1)}><Search /><span>检索社区零售变化<small>在对话中阅读观察与来源</small></span><ChevronRight /></button><button onClick={() => { openFile(examples[0]); setInput(scenarios[0].prompt); inputRef.current?.focus(); }}><FileText /><span>分析示例文件<small>边聊，边核对原始资料</small></span><ChevronRight /></button></div></div>}
                {messages.map((message, index) => {
                  const messageResult = results.find((item) => item.version === message.version);
                  return <article className={`ux-message ux-message-${message.role}`} key={index} aria-label={message.role === "user" ? "你的消息" : "Mangrove 回复"}>
                    {message.role === "assistant" && <div className="ux-assistant"><Leaf /><span>Mangrove</span><small>{message.model}</small></div>}<p>{message.text}</p>
                    {message.kind === "search" && searchContent}
                    {messageResult && <><p>{messageResult.note}</p>{messageResult.scenario === 2 && <p>文件原始行提供门店营收；网页原始段落只提供行业背景，不能据此认定变化原因。</p>}{messageResult.gap && <p className="ux-warning">{messageResult.gap}</p>}<button className="ux-delivery" onClick={() => openFile({ id: `result-${messageResult.version}`, name: `${messageResult.title} · v${messageResult.version}.md`, kind: "result", version: messageResult.version })}><span className="ux-file-symbol"><FileText /></span><span><strong>{messageResult.title} · 版本 {messageResult.version}</strong><small>MD 文档 · 虚构演示结果</small></span><ChevronRight /></button><p className="ux-fine">演示结果不是正式交付；真实交付需通过完整性、QA 与任务内发布。</p></>}
                  </article>;
                })}
                {stage === "clarify" && <div className="ux-pending"><span>需要你确认</span><Button onClick={() => advance()}>{s.answer}</Button></div>}
                {(stage === "login" || stage === "expired") && <div className="ux-pending"><h3>{stage === "expired" ? "登录示意已失效" : "请完成来源登录"}</h3><div className="ux-login-placeholder"><ShieldCheck /><strong>不可扫码的登录示意</strong><small>没有二维码或真实登录链接</small></div><p>只读选定资料；不发布、不互动，不借用其他账号凭据。</p><div className="ux-actions">{stage === "expired" ? <Button onClick={() => setStage("login")}>刷新登录示意</Button> : <Button onClick={() => { setLoginConfirmed(true); setStage("running"); }}>模拟登录成功并恢复</Button>}<Button variant="ghost" onClick={() => setStage("stopped")}>取消本次读取</Button></div></div>}
                {stage === "running" && <div className="ux-working" role="status"><span className="ux-working-dot" />正在核对资料与目标<span>模拟处理中</span></div>}
                {stage === "stopping" && <div className="ux-pending"><h3>停止请求已发出（演示）</h3><p>尚未确认停止，不能把请求已发出当作执行已结束。</p><Button onClick={() => setStage("stopped")}>模拟确认已停止</Button></div>}
                {stage === "stopped" && <div className="ux-pending"><h3>演示已停止</h3><p>已有资料和结果保留。恢复会继续当前演示任务。</p><Button onClick={() => setStage(scenario === 4 && !loginConfirmed ? "login" : "running")}>恢复演示执行</Button></div>}
                {stage === "unknown" && <div className="ux-pending"><h3>连接中断，结果未知</h3><p>先查询已有结果，避免重复执行。这里模拟找回同一运行的结果。</p><Button onClick={finish}>查询状态后恢复结果</Button></div>}
                {stage === "done" && scenario === 3 && <div className="ux-retry"><Button variant="outline" onClick={() => setRetryState("retrying")}>重试缺失来源（演示）</Button>{retryState === "retrying" && <Button variant="outline" onClick={() => setRetryState("failed")}>模拟重试仍失败</Button>}{retryState === "failed" && <p role="status">重试仍失败：南岸店不可用，已保留两家门店结果与缺口。</p>}</div>}
                {stage === "deleted" && <div className="ux-pending"><h3>演示清理完成</h3><p>{sourceDeleted ? "依赖任务已模拟暂停，共享来源标记已删除；季度复盘及其独立结果仍保留。" : "已删除本演示任务；季度复盘引用的共享原始资料保留。"}</p><Button onClick={() => reset()}>重新开始演示</Button></div>}
                {messages.length > 0 && <details className="ux-record"><summary>工作记录 · {stages[stage]}</summary><p>本地接收要求 → {stages[stage]}。未执行真实工具或联网请求，耗时与用量为演示值。</p></details>}
              </div>
              {!nearBottom && <button className="ux-latest" onClick={() => setNearBottom(true)}><ArrowDown />回到最新</button>}
              <div className="ux-composer-wrap"><form className="ux-composer" noValidate onSubmit={(e) => { e.preventDefault(); submit(); }}>
                {attached && <div className="ux-attachments">{files.filter((file) => file.kind === "sales" || file.kind === "notes").map((file) => <button type="button" key={file.id} onClick={() => openFile(file)}><FileText />{file.name}</button>)}</div>}
                <label htmlFor="ux-request">{messages.length ? "继续提问或修订" : "你想完成什么？"}</label><textarea className="resize-none" id="ux-request" ref={inputRef} rows={2} value={input} disabled={stage === "deleted"} aria-invalid={!!error} aria-describedby="ux-input-help" placeholder="问一个问题，或描述你想完成的事…" onChange={(e) => { setInput(e.target.value); setError(""); }} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); submit(); } }} />
                <div className="ux-composer-bottom"><button className="ux-attach-button" type="button" onClick={() => openModal("添加示例文件")} aria-label="添加文件"><Plus /></button><div className="ux-model"><label htmlFor="ux-model">模型</label><select id="ux-model" aria-label="选择模型" value={model} disabled={busy} onChange={(e) => { setModel(e.target.value); setNotice(`已选择${e.target.value}（虚构模型），用于下一条消息。`); }}><option>澄明 · 快速</option><option>澄明 · 深入</option></select></div><span className="ux-model-demo">演示</span>{stage === "running" ? <Button type="button" size="icon" variant="outline" aria-label="停止演示" onClick={() => setStage("stopping")}><Square /></Button> : <Button className="ux-send" type="submit" size="icon" aria-label="发送要求" disabled={busy}><ArrowUp /></Button>}</div>
                <p id="ux-input-help" className={error ? "ux-error" : "ux-input-help"}>{error || (stage === "deleted" ? "任务已删除，请重新开始演示。" : busy ? "请先处理当前待办；草稿会保留。" : "Enter 发送 · Shift + Enter 换行")}</p>
              </form><p className="ux-status" role="status">{notice || "离线交互原型 · 不读取真实文件、不调用模型"}</p></div>
            </section>
          </Panel>
          {preview && <><Separator className="ux-resizer" aria-label="调整文件预览宽度" /><Panel id="preview" minSize="32%" defaultSize="48%" className="ux-preview-panel"><section className="ux-preview" aria-label="文件预览"><header><span><FolderOpen />文件预览</span><div className="ux-actions"><button className="ux-icon ux-expand-preview" aria-label={expanded ? "还原文件预览" : "展开文件预览"} onClick={() => setExpanded(!expanded)}>{expanded ? <Minimize2 /> : <Maximize2 />}</button><button className="ux-icon" aria-label="关闭文件预览" onClick={closePreview}><X /></button></div></header><div role="tablist" aria-label="打开的文件" className="ux-file-tabs">{files.map((file, index) => <button role="tab" id={`tab-${file.id}`} aria-controls="ux-file-panel" aria-selected={selectedFile === file.id} tabIndex={selectedFile === file.id ? 0 : -1} key={file.id} onClick={() => { setSelectedFile(file.id); if (file.version) setSelectedVersion(file.version); }} onKeyDown={(e) => { let next = index; if (e.key === "ArrowRight") next = (index + 1) % files.length; else if (e.key === "ArrowLeft") next = (index - 1 + files.length) % files.length; else if (e.key === "Home") next = 0; else if (e.key === "End") next = files.length - 1; else return; e.preventDefault(); const file = files[next]; setSelectedFile(file.id); if (file.version) setSelectedVersion(file.version); document.getElementById(`tab-${file.id}`)?.focus(); }}><FileText />{file.name}</button>)}</div>
              <div id="ux-file-panel" role="tabpanel" aria-labelledby={`tab-${selectedFile}`} tabIndex={0} className="ux-preview-scroll">
                {activeFile?.kind === "result" && result ? <><div className="ux-file-meta"><span>生成文件 · 演示</span><label>版本<select aria-label="结果版本" value={selectedVersion} onChange={(e) => { const version = Number(e.target.value); setSelectedVersion(version); setSelectedFile(`result-${version}`); }}>{results.map((item) => <option key={item.version} value={item.version}>版本 {item.version}</option>)}</select></label></div><article className="ux-paper"><span className="ux-paper-kicker">MANGROVE / 观察记录</span><h2>{result.title}</h2><p className="ux-paper-subtitle">2026 年 9 月 · 虚构演示资料 · 版本 {result.version}</p><hr /><h3>{result.scenario === 1 ? "从原文中提取两个主题" : "营收变化，先看结构"}</h3><p>{result.summary}</p>{result.scenario === 1 ? <><h3>就近采购与配送服务</h3><p>样本代表性、观察时间与履约投入尚待核对。观察线索不能当作事实趋势。</p></> : table}{result.gap && <p className="ux-warning">{result.gap}</p>}{result.note && <p className="ux-revision-note">{result.note}</p>}{result.scenario === 2 && <p>文件原始行提供营收；网页原始段落只提供行业背景，不能据此认定因果。</p>}<h3>下一步核对</h3><p>对齐统计口径，回到原始记录核验。先确认发生了什么，再解释为什么。</p><button className="ux-source-link" onClick={() => openFile(result.scenario === 1 ? webFile : examples[0])}>{result.scenario === 1 ? "查看来源原始段落" : "定位来源原始行 2—3"}<ChevronRight /></button>{result.scenario === 2 && <button className="ux-source-link" onClick={() => openFile(webFile)}>核对网页原始段落<ChevronRight /></button>}<footer>仅为虚构演示结果，不是正式交付。</footer></article><div className="ux-downloads"><span><Download />独立下载</span><div className="ux-actions">{(["md", "csv", "json"] as const).map((format) => <Button size="sm" variant="outline" key={format} onClick={() => download(format)}>下载 {format.toUpperCase()} 演示</Button>)}</div></div><Button variant="ghost" onClick={() => { setInput("请补充每家门店需要核对的问题，保留原有结论。"); setMobileView("conversation"); setExpanded(false); requestAnimationFrame(() => inputRef.current?.focus()); }}>继续修订此结果<ArrowUp /></Button></> : <><div className="ux-file-meta"><span>本人资料 · 原始数据 · 虚构</span><span>09:30 获取</span></div><article className="ux-paper"><span className="ux-paper-kicker">MANGROVE / 来源记录</span><h2>{activeFile?.name}</h2><p className="ux-paper-subtitle">本任务内的示例内容 · 未读取你的电脑文件</p><hr />{sourceDeleted ? <p>来源已删除，不能继续预览或复用。</p> : activeFile?.kind === "web" ? <article aria-label="网页原始段落"><h3>第 1 段 · 就近采购</h3><p>虚构原文：社区小店为附近居民提供日常采购。观察样本与时间范围尚未核验。</p><h3>第 2 段 · 配送服务</h3><p>虚构原文：部分社区零售门店增加配送服务。不同门店的配送覆盖、时段与履约投入存在差异。</p><p>此段仅作行业背景，不能作为销售变化的因果证据。</p></article> : activeFile?.kind === "notes" ? <><h3>门店访谈摘录</h3><p>青禾店：本月组合购买更多，需要与订单原始记录核对。</p><p>北湾店：工作日到店客流有所回落，暂未核验原因。</p><p>访谈内容是待核对的描述，不代表已证明的因果关系。</p></> : table}<footer>来源范围：本任务 · 仅本人可见</footer></article></>}
              </div>
            </section></Panel></>}
        </Group>
        <details className="ux-demo-tools" open={demoTools} onToggle={(e) => setDemoTools(e.currentTarget.open)}><summary>演示工具<span>场景与异常状态</span></summary><div className="ux-demo-controls"><label>演示场景<select aria-label="演示场景" value={scenario} onChange={(e) => reset(Number(e.target.value), true)}>{scenarios.map((item, index) => <option key={item.name} value={index}>{item.name}</option>)}</select></label><button onClick={() => reset(scenario, true)}>重新开始演示</button><button disabled={stage !== "running"} onClick={() => setStage("unknown")}>模拟结果未知</button><button disabled={stage !== "login"} onClick={() => setStage("expired")}>模拟登录失效</button><button disabled={stage !== "running"} onClick={finish}>推进到演示结果</button></div></details>
      </main>
    </div>
    <Dialog.Root open={modal !== null} onOpenChange={(open) => { if (!open) setModal(null); }}><Dialog.Portal><Dialog.Overlay className={`ux01 ux-overlay ${dark ? "dark" : ""}`} /><Dialog.Content className={`ux01 ux-dialog ${dark ? "dark" : ""} ${modal === "任务导航" ? "ux-drawer" : ""}`} onCloseAutoFocus={(e) => { e.preventDefault(); (previousFocus.current?.isConnected ? previousFocus.current : inputRef.current)?.focus(); }}><Dialog.Title>{modal}</Dialog.Title><Dialog.Description>交互原型 · 仅使用虚构数据，关闭后回到当前任务。</Dialog.Description><Dialog.Close className="ux-dialog-close ux-icon" aria-label="关闭弹层"><X /></Dialog.Close>
      {modal === "任务导航" ? <nav aria-label="抽屉任务导航">{navigation}</nav> : modal === "用量" ? <dl className="ux-usage-list"><dt>所选模型</dt><dd>{model}（虚构模型）</dd><dt>澄清阶段</dt><dd>12 秒 · 480 输入 / 120 输出 Token（示例）</dd><dt>执行阶段</dt><dd>24 秒 · 原生 Token 未知</dd><dt>总用量</dt><dd>未知；缺失不记作 0，不重复累计阶段用量</dd><dt>参考费用</dt><dd>未知：未匹配官方价，不是收费账单</dd></dl> : modal === "添加示例文件" ? <><p>选择一份内置资料，立即在侧边预览。</p><div className="ux-file-picker">{examples.map((file) => <button key={file.id} aria-label={`添加 ${file.name}`} onClick={() => { openFile(file); setModal(null); setNotice(`已添加 ${file.name}。未读取真实本地文件。`); }}><FileText /><span><strong>{file.name}</strong><small>{file.kind === "sales" ? "2 行销售记录 · 内置 CSV" : "2 段访谈摘录 · 内置 Markdown"}</small></span><Plus /></button>)}</div><p className="ux-fine">真实文件上传由后续实现接线。</p></> : modal === "本人资料" ? <><p>仅展示本人内置资料；原始数据与加工结果分别标识。</p><div className="ux-resource"><FileText /><div><strong>共享销售原始资料.csv</strong><p>原始数据 · 本人 · 2026-09-07 09:30</p><p>{sourceDeleted ? "来源已删除，无法复用" : "由当前任务与季度复盘共同引用"}</p></div></div><Button disabled={sourceDeleted} onClick={() => { openFile(examples[0]); setModal(null); setNotice("已复用本人原始资料，来源身份与获取时间保留。"); }}>复用本人原始资料</Button></> : modal === "关联删除" ? <><p>删除「{s.title}」及其演示结果。共享销售原始资料仍被以下任务引用：</p><div className="ux-resource"><FileText /><div><strong>季度复盘</strong><p>引用原始销售资料 · 独立结果将保留</p></div></div><fieldset className="ux-delete-choice"><legend>选择共享资料的处理方式</legend><label><input type="radio" name="delete-scope" checked={!sharedDelete} onChange={() => setSharedDelete(false)} />保留被其他任务引用的原始资料</label><label><input type="radio" name="delete-scope" checked={sharedDelete} onChange={() => setSharedDelete(true)} />也删除共享资料：先暂停依赖任务</label></fieldset><p>{sharedDelete ? "确认后模拟暂停季度复盘的依赖读取，再删除共享原始资料。季度复盘及其独立结果仍保留，来源标记已删除。" : "只删除当前任务及其独占结果，共享原始资料和季度复盘保留。"}</p><div className="ux-actions"><Button variant="outline" onClick={() => setModal(null)}>取消删除</Button><Button variant="destructive" onClick={() => { setSourceDeleted(sharedDelete); setStage("deleted"); setMessages([]); setResults([]); setFiles([]); setSelectedFile(""); setPreview(false); setMobileView("conversation"); setModal(null); setNotice("演示清理完成。重新开始可恢复虚构数据，不代表真实删除支持撤销。"); }}>确认模拟删除</Button></div></> : modal === "关联任务" ? <><h3>季度复盘 · 虚构任务</h3><p>{sourceDeleted ? "依赖读取已暂停，原始来源已删除。任务与独立复盘结果保留。" : "正在引用本人共享销售原始资料。任务与独立复盘结果均保留。"}</p><Button variant="outline" onClick={() => setModal(null)}>返回当前任务</Button></> : modal && auxiliary[modal] ? <><p>{auxiliary[modal]}</p>{modal === "模板" && <Button onClick={() => { setInput("请按月度复盘模板，比较变化、列出依据与待核对事项。"); setModal(null); }}>应用本人演示模板</Button>}{modal === "个人记忆" && <Button onClick={() => { setInput("请先给结论，再列来源。"); setModal(null); }}>用于当前输入</Button>}{modal === "反馈" && <Button onClick={() => { setNotice("已在本页标记有帮助（演示），未上传反馈。"); setModal(null); }}>标记有帮助（演示）</Button>}{modal === "设置" && <Button onClick={() => setDark(!dark)}>{dark ? "使用浅色主题" : "使用深色主题"}</Button>}</> : null}
    </Dialog.Content></Dialog.Portal></Dialog.Root>
  </div>;
}

createRoot(document.getElementById("root")!).render(<WorkspacePrototype />);
