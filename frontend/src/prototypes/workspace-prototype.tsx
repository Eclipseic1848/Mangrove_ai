import { useLayoutEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowDown, ArrowUp, BookOpen, Check, ChevronRight, Clock3, Database, FileText, FolderOpen, Leaf, Maximize2, Menu, MessageSquare, Moon, Paperclip, Plus, Search, Settings, ShieldCheck, Square, Sun, X } from "lucide-react";
import { Button } from "../components/ui/button";
import "../index.css";
import "./workspace-prototype.css";

const scenarios = [
  { name: "文件分析", title: "找出门店销售变化的原因", prompt: "分析这份门店销售资料，找出最值得关注的变化，并给出行动建议。", question: "资料包含营收和退货。你更想先看营收变化，还是退货原因？", answer: "先看营收变化，按门店对比", source: "门店销售示例.csv" },
  { name: "无 URL 搜索", title: "了解社区零售的新变化", prompt: "帮我检索社区零售的近期变化，整理可核对的观察，不限定网址。", question: "你希望关注哪一地区？这会影响检索范围。", answer: "关注华东地区", source: "社区零售观察（虚构原文）" },
  { name: "混合资料", title: "把门店表现放回市场中看", prompt: "结合我的门店销售资料与公开行业信息，比较营收变化，区分事实和推测。", question: "公开信息只作为背景。要以销售资料中的哪个月份为基准？", answer: "以八月为基准", source: "门店销售示例.csv + 行业观察" },
  { name: "部分结果", title: "先核对已经取得的资料", prompt: "汇总三份门店资料。个别来源失败时，先给我可核对的部分结果。", question: "南岸店资料暂时不可用。是否先处理其余两家，并明确列出缺口？", answer: "先处理可用资料，保留缺口", source: "两份可用销售资料" },
  { name: "扫码失效 / 恢复", title: "继续读取需要登录的来源", prompt: "读取我获准查看的来源资料，登录完成后继续当前任务。", question: "将只读本次选定的资料，不获得发布或账号管理权限。确认读取这个范围吗？", answer: "确认本次只读范围", source: "登录来源示例" },
  { name: "关联删除", title: "整理任务与关联资料", prompt: "我想清理这个演示任务，先让我看清它引用的资料和其他受影响任务。", question: "销售原始资料还被「季度复盘」引用。先查看关联，再选择删除范围。", answer: "查看关联与删除范围", source: "共享销售原始资料.csv" },
];
type Stage = "start" | "clarify" | "login" | "expired" | "running" | "stopping" | "stopped" | "unknown" | "done" | "deleted";
type View = "conversation" | "sources" | "result";
const stages: Record<Stage, string> = { start: "准备开始", clarify: "等待补充", login: "等待登录", expired: "登录示意已失效", running: "演示执行中", stopping: "停止请求待确认", stopped: "演示已停止", unknown: "结果未知", done: "演示结果可查看", deleted: "演示任务已删除" };
const salesRows = [["青禾店", "128,000", "+12.3%", "客单价提高"], ["北湾店", "96,000", "−4.0%", "到店人数减少"]];
const auxiliary: Record<string, string> = {
  "模板": "本人模板：门店月度复盘。应用后回到当前任务输入。评分、晋级、淘汰与确切通用副本预览共享由后续辅助页面工单接线；这里不发布模板。",
  "个人记忆": "本人偏好：先给结论，再列来源。个人记忆只对本人可见；原型不保存或自动共享。完整编辑与调用证据待后续工单接线。",
  "自动任务": "尚未安排自动运行。调度界面将在后续工单接线；本原型不会设置后台任务，也不会向外部系统发送结果。",
  "反馈": "可在本地演示标记“有帮助”。真实评分、备注保存与审计由后续辅助页面工单接线；本次反馈不会上传。",
  "设置": "当前仅演示明暗主题。模型连接、账号设置和凭据管理沿用既有设置职责，待统一导航工单接线；此处不收集密钥。",
  "管理": "管理功能待后续统一导航工单接线。普通用户不获得管理员权限；管理元数据与经审计业务正文保持不同读取边界。这里没有真实账号或管理数据。",
};

function WorkspacePrototype() {
  const [scenario, setScenario] = useState(0);
  const [stage, setStage] = useState<Stage>("start");
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [messages, setMessages] = useState<string[]>([]);
  const [attached, setAttached] = useState(false);
  const [view, setView] = useState<View>("conversation");
  const [focused, setFocused] = useState(false);
  const [nav, setNav] = useState(true);
  const [dark, setDark] = useState(false);
  const [modal, setModal] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const [selectedVersion, setSelectedVersion] = useState(0);
  const [sharedDelete, setSharedDelete] = useState(false);
  const [notice, setNotice] = useState("");
  const [sourceDeleted, setSourceDeleted] = useState(false);
  const [loginConfirmed, setLoginConfirmed] = useState(false);
  const [mixedSource, setMixedSource] = useState("file");
  const [retryState, setRetryState] = useState<"idle" | "retrying" | "failed">("idle");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const conversationRef = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const canvasTrigger = useRef<HTMLElement | null>(null);
  const s = scenarios[scenario];
  const rows = scenario === 1 ? [["社区小店", "便利性", "待核对", "缩短采购距离"], ["即时零售", "配送服务", "待核对", "增加履约要求"]] : salesRows;
  const columns = scenario === 1 ? ["观察对象", "主题", "证据状态", "原文要点"] : ["门店", "营收", "变化", "待核对"];
  const summary = scenario === 1 ? "虚构原文提到就近采购与配送服务两个主题；这些观察不是已核实的市场趋势，需进一步查证代表性。" : "青禾店营收上升，北湾店有所回落。现有资料支持这个方向，但变化原因仍需要进一步核对。";
  const resultTitle = scenario === 1 ? "社区零售观察" : "门店观察";
  const revisionNote = selectedVersion > 1 ? "新增核对事项：核对观察样本的覆盖范围、时间口径及原始记录，再判断变化原因。" : "";
  const mixedNote = scenario === 2 ? "文件原始行提供门店营收；网页原始段落只提供配送服务的行业背景，不能据此认定门店营收变化的原因。" : "";
  useLayoutEffect(() => {
    if (!inputRef.current) return;
    inputRef.current.style.height = "auto";
    inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 140)}px`;
  }, [input]);
  useLayoutEffect(() => {
    // 演示阶段只由显式操作推进；不监听输入或用户滚动，避免打断主动上翻。
    conversationRef.current?.scrollTo({ top: stage === "start" ? 0 : conversationRef.current.scrollHeight, behavior: "auto" });
  }, [stage, retryState]);
  const openModal = (name: string) => {
    // 抽屉内切换弹层时保留外部触发器，避免恢复到已经卸载的按钮。
    if (!modal) previousFocus.current = document.activeElement as HTMLElement;
    setModal(name);
  };
  const reset = (next = scenario) => {
    setScenario(next); setStage("start"); setInput(""); setMessages([]); setError(""); setAttached(false);
    setRevision(0); setSelectedVersion(0); setView("conversation"); setFocused(false); setSourceDeleted(false); setNotice(""); setSharedDelete(false);
    setLoginConfirmed(false); setMixedSource("file"); setRetryState("idle");
  };
  const showView = (next: View, focusInput = false) => {
    if (view === "conversation" && next !== "conversation") canvasTrigger.current = document.activeElement as HTMLElement;
    setView(next); setFocused(false);
    if (next === "conversation") requestAnimationFrame(() => (focusInput ? inputRef.current : canvasTrigger.current)?.focus());
  };
  const submit = () => {
    if (!input.trim()) { setError("请先描述你想完成的事情。"); inputRef.current?.focus(); return; }
    setMessages((old) => [...old, input.trim()]); setInput(""); setError(""); setView("conversation");
    if (stage === "start" || stage === "deleted") setStage("clarify");
    else if (stage === "clarify") {
      if (scenario === 4) setStage("login");
      else if (scenario === 5) openModal("关联删除");
      else setStage("running");
    } else setStage(scenario === 4 && !loginConfirmed ? "login" : "running");
  };
  const advance = () => {
    setMessages((old) => [...old, s.answer]);
    if (scenario === 4) setStage("login");
    else if (scenario === 5) openModal("关联删除");
    else setStage("running");
  };
  const finish = () => { const next = revision + 1; setRevision(next); setSelectedVersion(next); setStage("done"); setNotice("已生成虚构演示结果，可核对来源或继续修订。"); };
  const download = (format: "md" | "csv" | "json") => {
    const heading = `虚构演示数据 · ${s.title} · 版本 ${selectedVersion}`;
    const gap = scenario === 3 ? "缺口：南岸店资料不可用，仅覆盖两家门店。" : "仅用于交互演示，不构成业务事实。";
    const data = format === "csv" ? `\uFEFF${heading}\n来源：${s.source}（虚构）\n${gap}\n${mixedNote}\n${revisionNote}\n${columns.join(",")}\n${rows.map((row) => row.map((v) => `"${v}"`).join(",")).join("\n")}` : format === "json" ? JSON.stringify({ notice: heading, source: s.source, version: selectedVersion, gap, summary, mixedNote, revisionNote, columns, rows }, null, 2) : `# ${heading}\n\n${summary}\n\n${mixedNote}\n\n${revisionNote}\n\n来源：${s.source}（虚构）\n${gap}`;
    const url = URL.createObjectURL(new Blob([data], { type: format === "json" ? "application/json;charset=utf-8" : "text/plain;charset=utf-8" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `虚构演示-版本${selectedVersion}.${format}`; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000); setNotice(`已下载版本 ${selectedVersion} 的 ${format.toUpperCase()} 虚构演示文件。`);
  };
  const navigation = <>
    <div className="ux-brand"><img src="/logo.svg" alt="" width="24" height="24" /><strong>Mangrove</strong><span>howso</span></div>
    <Button variant="outline" className="ux-new" onClick={() => { reset(); setModal(null); }}><Plus />新任务</Button>
    <p className="ux-caption">任务案头</p>
    <button className="ux-task active" onClick={() => { showView("conversation"); setModal(null); }}><MessageSquare /><span>{s.title}<small>当前演示 · 仅本人</small></span></button>
    <button className="ux-task" onClick={() => openModal("关联任务")}><FileText /><span>季度复盘<small>{sourceDeleted ? "依赖已暂停 · 来源已删除" : "演示关联任务"}</small></span></button>
    <div className="ux-nav-bottom">
      <button onClick={() => openModal("本人资料")}><FolderOpen />本人资料</button>
      <button onClick={() => openModal("模板")}><BookOpen />模板</button>
      <button onClick={() => openModal("个人记忆")}><Database />个人记忆</button>
      <button onClick={() => openModal("自动任务")}><Clock3 />自动任务</button>
      <button onClick={() => openModal("反馈")}><MessageSquare />反馈</button>
      <button onClick={() => openModal("设置")}><Settings />设置</button>
      <button onClick={() => openModal("管理")}><ShieldCheck />管理</button>
    </div>
    <div className="ux-profile"><span>林</span><div>演示用户<small>个人工作空间</small></div></div>
  </>;

  return <div className={`ux01 ux-root ${dark ? "dark" : ""}`}>
    <div className="ux-demo"><span>交互原型 · 虚构数据，不连接真实服务</span><label>演示场景<select aria-label="演示场景" value={scenario} onChange={(e) => reset(Number(e.target.value))}>{scenarios.map((item, i) => <option key={item.name} value={i}>{item.name}</option>)}</select></label><button onClick={() => reset()}>重新开始演示</button></div>
    <div className={`ux-shell ${nav ? "" : "ux-nav-collapsed"}`}>
      {nav && <aside className="ux-sidebar" aria-label="任务导航">{navigation}</aside>}
      <main className="ux-main">
        <header className="ux-header"><button className="ux-desktop-menu" aria-label={nav ? "收起导航" : "展开导航"} onClick={() => setNav(!nav)}><Menu /></button><button className="ux-mobile-menu" aria-label="打开任务导航" onClick={() => openModal("任务导航")}><Menu /></button><div className="ux-title"><span>工作空间 / 当前任务</span><h1>{stage === "deleted" ? "任务已删除" : s.title}</h1></div><button aria-label={dark ? "切换浅色主题" : "切换深色主题"} onClick={() => setDark(!dark)}>{dark ? <Sun /> : <Moon />}</button><button className="ux-usage" aria-label="模型与用量" onClick={() => openModal("模型与用量")}><Clock3 /><span>用量</span></button></header>
        <div className="ux-context"><span><i />{stages[stage]}</span><button onClick={() => openModal("模型与用量")}>演示模型 · 本人连接<ChevronRight /></button><span className="ux-context-right">仅本人可见</span></div>
        <nav className="ux-views" aria-label="当前任务视图">{([["conversation", "对话"], ["sources", "资料"], ["result", "结果"]] as const).map(([key, label]) => <button key={key} aria-pressed={view === key} onClick={() => showView(key)}>{label}{key === "sources" && attached ? " · 1" : ""}{key === "result" && revision > 0 ? ` · ${revision}` : ""}</button>)}</nav>
        <div className={`ux-workspace ${view !== "conversation" ? "ux-has-canvas" : ""} ${focused ? "ux-focused" : ""}`}>
          <section className={`ux-conversation ${view !== "conversation" ? "ux-mobile-hide" : ""}`} aria-label="连续对话">
            <div className="ux-conversation-scroll" ref={conversationRef}>
              {messages.length === 0 && <div className="ux-intro"><span className="ux-eyebrow">从一个问题开始</span><h2>把资料带来，<br />把事情理清。</h2><p>说说你想知道什么。我们一起核对资料、<br className="ux-wide-only" />澄清问题，再把结果留在这张案头上。</p></div>}
              {messages.length === 0 && <button className="ux-example" onClick={() => { setInput(s.prompt); inputRef.current?.focus(); }}><span>{scenario === 1 ? <Search /> : <FileText />}{s.prompt}</span><ArrowUp /></button>}
              {messages.map((text, index) => <article className="ux-message" key={index}><p className="ux-message-author">你 <span>本地演示</span></p><p>{text}</p>{index === 0 && <div className="ux-reply"><span className="ux-assistant"><Leaf />Mangrove</span><p>{s.question}</p></div>}</article>)}
              {stage === "clarify" && <div className="ux-pending"><span>需要你补充</span><p>{s.question}</p><Button onClick={advance}>{s.answer}</Button></div>}
              {(stage === "login" || stage === "expired") && <div className="ux-pending"><span>当前任务需要登录来源</span><h3>{stage === "expired" ? "登录示意已失效" : "请完成来源登录"}</h3><div className="ux-login-placeholder"><ShieldCheck /><strong>不可扫码的登录示意</strong><small>没有二维码或真实登录链接</small></div><p>只读选定资料；不发布、不互动，不借用其他账号凭据。</p><div className="ux-actions">{stage === "expired" ? <Button onClick={() => setStage("login")}>刷新登录示意</Button> : <><Button onClick={() => { setLoginConfirmed(true); setStage("running"); }}>模拟登录成功并恢复</Button><Button variant="outline" onClick={() => setStage("expired")}>模拟登录失效</Button></>}<Button variant="ghost" onClick={() => setStage("stopped")}>取消本次读取</Button></div></div>}
              {stage === "running" && <div className="ux-pending"><span>演示执行中</span><h3>正在核对资料与目标</h3><p>这里只推进预设演示，不会读取外部来源或调用模型。</p><div className="ux-actions"><Button onClick={finish}>推进到演示结果</Button><Button variant="outline" onClick={() => setStage("unknown")}>模拟结果未知</Button></div></div>}
              {stage === "stopping" && <div className="ux-pending"><h3>停止请求已发出（演示）</h3><p>尚未确认停止，不能把请求已发出当作执行已结束。</p><Button onClick={() => setStage("stopped")}>模拟确认已停止</Button></div>}
              {stage === "stopped" && <div className="ux-pending"><h3>演示已停止</h3><p>已有资料和结果保留。恢复会继续当前演示任务。</p><Button onClick={() => setStage(scenario === 4 && !loginConfirmed ? "login" : "running")}>恢复演示执行</Button></div>}
              {stage === "unknown" && <div className="ux-pending"><h3>连接中断，结果未知</h3><p>先查询已有结果，避免重复执行。这里模拟找回同一运行的结果。</p><Button onClick={finish}>查询状态后恢复结果</Button></div>}
              {stage === "done" && <article className="ux-result-summary"><span className="ux-assistant"><Leaf />Mangrove</span><h3>{scenario === 3 ? "两份资料已整理，仍有一处缺口。" : "先关注这两个变化。"}</h3><p>{summary}</p>{mixedNote && <p>{mixedNote}</p>}{revisionNote && <p>{revisionNote}</p>}{scenario === 3 && <p className="ux-warning">缺口：南岸店资料不可用，结论仅覆盖两家门店。不能视为三店完整结果。</p>}
              {scenario === 3 && <div className="ux-actions"><Button variant="outline" onClick={() => setRetryState("retrying")}>重试缺失来源（演示）</Button>{retryState === "retrying" && <Button variant="outline" onClick={() => setRetryState("failed")}>模拟重试仍失败</Button>}{retryState === "failed" && <p role="status">重试仍失败：南岸店不可用，已保留两家门店结果与缺口。</p>}</div>}<button className="ux-delivery" onClick={() => showView("result")}><FileText /><span>{resultTitle} · 版本 {revision}<small>虚构演示结果 · 文档与表格</small></span><ChevronRight /></button><button className="ux-source-link" onClick={() => showView("sources")}>查看依据：{s.source}</button><p className="ux-fine">演示结果不是正式交付。真实结果需独立通过完整性、QA 与任务内发布。</p></article>}
              {stage === "deleted" && <div className="ux-pending"><h3>演示清理完成</h3><p>{sourceDeleted ? "依赖任务已模拟暂停，共享来源标记已删除；季度复盘及其独立结果仍保留。" : "已删除本演示任务；季度复盘引用的共享原始资料保留。"}</p><Button onClick={() => reset()}>重新开始演示</Button></div>}
              {stage !== "start" && <details className="ux-record"><summary>工作记录 · 演示动作</summary><p>本地接收要求 → 等待补充 → {stages[stage]}。未执行真实工具或联网请求；耗时与用量为演示值。</p></details>}
            </div>
            <button className="ux-latest" onClick={() => conversationRef.current?.scrollTo({ top: conversationRef.current.scrollHeight, behavior: "auto" })}><ArrowDown />回到最新</button>
          </section>
          {view !== "conversation" && <section className="ux-canvas" aria-label={view === "sources" ? "资料画布" : "结果画布"}>
            <header><div><span className="ux-eyebrow">{view === "sources" ? "资料 / 来源核对" : "结果 / 版本核对"}</span><h2>{view === "sources" ? "把依据放在眼前" : resultTitle}</h2></div><div className="ux-actions"><button aria-label={focused ? "退出结果专注" : "专注当前画布"} onClick={() => setFocused(!focused)}><Maximize2 /></button><button aria-label="返回对话" onClick={() => showView("conversation")}><X /></button></div></header>
            {view === "sources" ? <><div className="ux-paper"><span className="ux-tag">本人资料 · 原始数据 · 虚构</span><h3>{scenario === 2 ? (mixedSource === "file" ? "门店销售示例.csv" : "行业观察（虚构网页原文）") : s.source}</h3>{scenario === 2 && <div className="ux-actions"><Button variant="outline" aria-pressed={mixedSource === "file"} onClick={() => setMixedSource("file")}>核对文件原始行</Button><Button variant="outline" aria-pressed={mixedSource === "web"} onClick={() => setMixedSource("web")}>核对网页原始段落</Button></div>}<p className="ux-fine">演示获取时间：2026-09-07 09:30 · 来源范围：本任务</p>{sourceDeleted ? <p>来源已删除，不能继续预览或复用。</p> : <><p>以下为内置示例原始内容，不是搜索摘要，也不来自真实门店。</p>{scenario === 2 && mixedSource === "web" ? <article aria-label="网页原始段落"><h3>第 2 段 · 配送服务</h3><p>虚构原文：部分社区零售门店增加配送服务。不同门店的配送覆盖、时段与履约投入存在差异。</p><p>此段仅作行业背景，未核验样本代表性，不能作为销售变化的因果证据。</p></article> : <div className="ux-table-wrap"><table><caption>原始行定位 · 第 2—3 行</caption><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={row[0]}>{row.map((v) => <td key={v}>{v}</td>)}</tr>)}</tbody></table></div>}<Button variant="outline" onClick={() => { setAttached(true); setNotice("已载入本人虚构原始资料，未读取本地文件。"); }}>用于当前任务</Button></> }</div><p className="ux-fine">真实文件上传、来源读取和权限验证待后续工单接线。</p></> : revision === 0 ? <div className="ux-empty"><FileText /><h3>结果会留在这里</h3><p>先回到对话，补充要求并推进演示执行。</p><Button variant="outline" onClick={() => showView("conversation")}>回到对话继续</Button></div> : <><div className="ux-paper"><div className="ux-result-toolbar"><span className="ux-tag">虚构演示结果</span><label>版本<select aria-label="结果版本" value={selectedVersion} onChange={(e) => setSelectedVersion(Number(e.target.value))}>{Array.from({ length: revision }, (_, i) => <option key={i} value={i + 1}>版本 {i + 1}</option>)}</select></label></div><h3>{scenario === 1 ? "从原文中提取两个主题" : "营收变化，先看结构"}</h3><p>{summary}</p>{mixedNote && <p>{mixedNote}</p>}{revisionNote && <p>{revisionNote}</p>}{scenario === 3 && <p className="ux-warning">部分结果：缺少南岸店，不得推断完整区域表现。</p>}<div className="ux-table-wrap"><table><caption>虚构结果对比 · 版本 {selectedVersion}</caption><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead><tbody>{rows.map((row) => <tr key={row[0]}>{row.map((v) => <td key={v}>{v}</td>)}</tr>)}</tbody></table></div><button className="ux-source-link" onClick={() => showView("sources")}>定位来源原始行 2—3</button><hr /><p className="ux-fine">内容与文件版本一致。仅作交互演示，不是正式交付。</p><div className="ux-actions">{(["md", "csv", "json"] as const).map((format) => <Button variant="outline" key={format} onClick={() => download(format)}>下载 {format.toUpperCase()} 演示</Button>)}</div></div><Button variant="outline" onClick={() => { showView("conversation", true); setInput("请补充每家门店需要核对的问题，保留原有结论。"); }}>继续修订此结果</Button></>}
          </section>}
        </div>
        <form className="ux-composer" noValidate onSubmit={(e) => { e.preventDefault(); submit(); }}><label htmlFor="ux-request">{revision ? "继续提问或修订" : "你想完成什么？"}</label><textarea className="resize-none" id="ux-request" ref={inputRef} rows={2} value={input} aria-invalid={!!error} aria-describedby="ux-input-help" placeholder="描述目标，或把需要核对的问题告诉我…" onChange={(e) => { setInput(e.target.value); setError(""); }} onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); if (!["running", "stopping", "login", "expired", "unknown"].includes(stage)) submit(); } }} /><div className="ux-composer-bottom"><div className="ux-actions"><button type="button" onClick={() => { setAttached(true); setNotice("已载入内置虚构文件；不读取你电脑中的文件。"); }}><Paperclip />载入示例文件</button><button type="button" aria-label="选择本人资料" onClick={() => openModal("本人资料")}><FolderOpen /></button>{attached && <button type="button" className="ux-attachment" onClick={() => showView("sources")}><Check />示例资料 · 预览</button>}</div>{stage === "running" ? <Button type="button" variant="outline" onClick={() => setStage("stopping")}><Square />停止演示</Button> : <Button type="submit" disabled={["stopping", "login", "expired", "unknown"].includes(stage)}><ArrowUp />发送要求</Button>}</div><p id="ux-input-help" className={error ? "ux-error" : "ux-fine"}>{error || (["stopping", "login", "expired", "unknown"].includes(stage) ? "请先在对话中处理当前待办；草稿会保留。" : "Enter 发送 · Shift + Enter 换行 · 中文输入法组合期间不提交")}</p></form>
        <p className="ux-status" role="status">{notice || "演示内容仅保留在当前页面，刷新即可清空。"}</p>
      </main>
    </div>
    <Dialog.Root open={modal !== null} onOpenChange={(open) => { if (!open) setModal(null); }}><Dialog.Portal><Dialog.Overlay className={`ux01 ux-overlay ${dark ? "dark" : ""}`} /><Dialog.Content className={`ux01 ux-dialog ${dark ? "dark" : ""} ${modal === "任务导航" ? "ux-drawer" : ""}`} onCloseAutoFocus={(e) => { e.preventDefault(); (previousFocus.current?.isConnected ? previousFocus.current : inputRef.current)?.focus(); }}><Dialog.Title>{modal}</Dialog.Title><Dialog.Description>交互原型 · 仅使用虚构数据，关闭后回到当前任务。</Dialog.Description><Dialog.Close className="ux-dialog-close" aria-label="关闭弹层"><X /></Dialog.Close>
      {modal === "任务导航" ? <nav aria-label="抽屉任务导航">{navigation}</nav> : modal === "模型与用量" ? <><dl className="ux-usage-list"><dt>模型 / 连接</dt><dd>演示模型 / 本人连接（不含凭据）</dd><dt>时间戳</dt><dd>2026-09-07 09:30（虚构）</dd><dt>澄清阶段</dt><dd>12 秒 · 480 输入 / 120 输出 Token（示例）</dd><dt>执行阶段</dt><dd>24 秒 · 原生 Token 未知</dd><dt>总用量</dt><dd>未知；缺失不记作 0，不重复累计阶段用量</dd><dt>参考费用</dt><dd>未知：未匹配官方价，不是收费账单</dd></dl></> : modal === "本人资料" ? <><p>仅展示本人内置演示资料；原始数据与加工结果分别标识。</p><div className="ux-resource"><FileText /><div><strong>共享销售原始资料.csv</strong><p>原始数据 · 本人 · 2026-09-07 09:30</p><p>{sourceDeleted ? "来源已删除，无法复用" : "由当前任务与季度复盘共同引用"}</p></div></div><Button disabled={sourceDeleted} onClick={() => { setAttached(true); setModal(null); setNotice("已复用本人原始资料，来源身份与获取时间保留。"); }}>复用本人原始资料</Button></> : modal === "关联删除" ? <><p>删除「{s.title}」及其演示结果。共享销售原始资料仍被以下任务引用：</p><div className="ux-resource"><FileText /><div><strong>季度复盘</strong><p>引用原始销售资料 · 独立结果将保留</p></div></div><fieldset className="ux-delete-choice"><legend>选择共享资料的处理方式</legend><label><input type="radio" name="delete-scope" checked={!sharedDelete} onChange={() => setSharedDelete(false)} />保留被其他任务引用的原始资料</label><label><input type="radio" name="delete-scope" checked={sharedDelete} onChange={() => setSharedDelete(true)} />也删除共享资料：先暂停依赖任务</label></fieldset><p>{sharedDelete ? "确认后模拟暂停季度复盘的依赖读取，再删除共享原始资料。季度复盘及其独立结果仍保留，来源标记已删除。" : "只删除当前任务及其独占结果，共享原始资料和季度复盘保留。"}</p><div className="ux-actions"><Button variant="outline" onClick={() => setModal(null)}>取消删除</Button><Button variant="destructive" onClick={() => { setSourceDeleted(sharedDelete); setStage("deleted"); setRevision(0); setSelectedVersion(0); setAttached(false); showView("conversation"); setModal(null); setNotice("演示清理完成。重新开始演示可恢复虚构数据，不代表真实删除支持撤销。"); }}>确认模拟删除</Button></div></> : modal === "关联任务" ? <><h3>季度复盘 · 虚构任务</h3><p>{sourceDeleted ? "依赖读取已暂停，原始来源已删除。任务与独立复盘结果保留。" : "正在引用本人共享销售原始资料。任务与独立复盘结果均保留。"}</p><Button variant="outline" onClick={() => setModal(null)}>返回当前任务</Button></> : modal && auxiliary[modal] ? <><p>{auxiliary[modal]}</p>{modal === "模板" && <Button onClick={() => { setInput("请按月度复盘模板，比较变化、列出依据与待核对事项。"); setModal(null); }}>应用本人演示模板</Button>}{modal === "个人记忆" && <Button onClick={() => { setInput("请先给结论，再列来源。"); setModal(null); }}>用于当前输入</Button>}{modal === "反馈" && <Button onClick={() => { setNotice("已在本页标记有帮助（演示），未上传反馈。"); setModal(null); }}>标记有帮助（演示）</Button>}{modal === "设置" && <Button onClick={() => setDark(!dark)}>{dark ? "使用浅色主题" : "使用深色主题"}</Button>}</> : null}
    </Dialog.Content></Dialog.Portal></Dialog.Root>
  </div>;
}

createRoot(document.getElementById("root")!).render(<WorkspacePrototype />);
