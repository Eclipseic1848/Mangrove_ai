import { useEffect, useRef, useState } from "react";
import { PageGuide } from "@/components/onboarding/PageGuide";
import { TaskContextLibrary } from "@/components/workspace/TaskContextLibrary";
import { BadgeCheck, Library, Share2, Trash2, RefreshCw, Eye, Tag, TrendingUp, Repeat } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { Pagination } from "@/components/ui/pagination";
import { Markdown } from "@/components/Markdown";
import { api } from "@/lib/api";
import { useAuth, isAdminish, type User } from "@/lib/auth";

interface LibraryEntry {
  scope: "owner" | "platform";
  is_owner: boolean;
  can_delete: boolean;
  content_digest: string;
}

interface Template extends LibraryEntry {
  slug: string;
  title: string;
  data_type: string;
  keywords: string[];
  body: string;
  status: string; // active / draft / retired
  uses: number;
  quality_avg: number;
  verified_uses?: number;
}

interface Lesson extends LibraryEntry {
  slug: string;
  title: string;
  data_type: string;
  keywords: string[];
  body: string;
  status: string; // draft / active / retired
  occurrences: number;
  helped_avoid: number;
}

interface ScanLogRow {
  id: number;
  ran_at: string;
  templates_scanned: number;
  templates_merged: number;
  lessons_scanned: number;
  lessons_merged: number;
  stale_drafts_deleted: number;
}

// 状态 -> 展示文案与徽标样式
const STATUS_META: Record<string, { label: string; variant: "success" | "warning" | "outline" }> = {
  active: { label: "已转正", variant: "success" },
  draft: { label: "草稿", variant: "warning" },
  retired: { label: "已淘汰", variant: "outline" },
};

const LESSON_STATUS_META: Record<string, { label: string; variant: "success" | "warning" | "danger" }> = {
  active: { label: "已转正", variant: "success" },
  draft: { label: "草稿", variant: "warning" },
  retired: { label: "已退役", variant: "danger" },
};

type TabKey = "templates" | "lessons" | "scanLog";

// 分页：每页条数（三个 Tab 统一）
const PAGE_SIZE = 12;
const TYPE_LABELS: Record<string, string> = {
  workspace_document: "文档处理", workspace_table: "表格处理", workspace_web: "网页分析",
  workspace_mixed: "混合资料", workspace_file: "文件处理", article: "文章", generic: "通用",
};
type LibraryFilter = { query: string; dataType: string; status: string };
const emptyFilter: LibraryFilter = { query: "", dataType: "", status: "" };
function matchesFilter(entry: Template | Lesson, filter: LibraryFilter) {
  const query = filter.query.trim().toLocaleLowerCase();
  return (!filter.dataType || entry.data_type === filter.dataType)
    && (!filter.status || entry.status === filter.status)
    && (!query || [entry.title, ...entry.keywords, entry.body].join("\n").toLocaleLowerCase().includes(query));
}

export function Templates() {
  const { user } = useAuth();
  return user ? <TemplateLibrary key={`${user.user_id}:${user.role}`} user={user} /> : null;
}

function TemplateLibrary({ user }: { user: User }) {
  const isAdmin = isAdminish(user?.role);
  const [contextOpen, setContextOpen] = useState(false);
  const [tab, setTab] = useState<TabKey>("templates");
  const [filters, setFilters] = useState({ templates: emptyFilter, lessons: emptyFilter });
  const filter = filters[tab === "lessons" ? "lessons" : "templates"];
  const updateFilter = (next: LibraryFilter) => {
    if (tab === "scanLog") return;
    setFilters(previous => ({ ...previous, [tab]: next }));
    if (tab === "templates") setTplPage(1); else setLessonPage(1);
  };

  // 模板库状态
  const [items, setItems] = useState<Template[]>([]);
  const [loading, setLoading] = useState(true);
  const [preview, setPreview] = useState<Template | null>(null);
  const [pendingDel, setPendingDel] = useState<Template | null>(null);

  // 教训库状态（与模板库各自独立，字段形状不同）
  const [lessonItems, setLessonItems] = useState<Lesson[]>([]);
  const [lessonLoading, setLessonLoading] = useState(true);
  const [lessonPreview, setLessonPreview] = useState<Lesson | null>(null);
  const [pendingLessonDel, setPendingLessonDel] = useState<Lesson | null>(null);

  // 巡检报告状态（只读，无 preview/delete）
  const [scanLog, setScanLog] = useState<ScanLogRow[]>([]);
  const [scanLogLoading, setScanLogLoading] = useState(true);
  const [scanLogError, setScanLogError] = useState(false);
  const [shareTarget, setShareTarget] = useState<{ kind: "templates" | "lessons"; entry: Template | Lesson } | null>(null);
  const [shareTitle, setShareTitle] = useState("");
  const [shareKeywords, setShareKeywords] = useState("");
  const [shareBody, setShareBody] = useState("");
  const [shareConfirmed, setShareConfirmed] = useState(false);
  const [shareBusy, setShareBusy] = useState(false);
  const [shareError, setShareError] = useState("");
  const shareEpoch = useRef(0);
  const shareInFlight = useRef(false);
  const mounted = useRef(true);
  const loadEpoch = useRef({ templates: 0, lessons: 0, scan: 0 });
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; shareEpoch.current++; };
  }, []);

  // 分页页码（三个 Tab 各自独立，刷新回到第 1 页）
  const [tplPage, setTplPage] = useState(1);
  const [lessonPage, setLessonPage] = useState(1);
  const [scanPage, setScanPage] = useState(1);

  const load = () => {
    const epoch = ++loadEpoch.current.templates;
    setLoading(true);
    setTplPage(1);
    api
      .get("/api/templates")
      .then((d) => { if (mounted.current && epoch === loadEpoch.current.templates) setItems(d.templates || []); })
      .catch(() => { if (mounted.current && epoch === loadEpoch.current.templates) toast.error("模板加载失败，请重试"); })
      .finally(() => { if (mounted.current && epoch === loadEpoch.current.templates) setLoading(false); });
  };
  useEffect(load, []);

  const loadLessons = () => {
    const epoch = ++loadEpoch.current.lessons;
    setLessonLoading(true);
    setLessonPage(1);
    api
      .get("/api/lessons")
      .then((d) => { if (mounted.current && epoch === loadEpoch.current.lessons) setLessonItems(d.lessons || []); })
      .catch(() => { if (mounted.current && epoch === loadEpoch.current.lessons) toast.error("经验加载失败，请重试"); })
      .finally(() => { if (mounted.current && epoch === loadEpoch.current.lessons) setLessonLoading(false); });
  };
  useEffect(() => {
    loadLessons();
  }, []);

  const loadScanLog = () => {
    const epoch = ++loadEpoch.current.scan;
    setScanLogLoading(true);
    setScanLogError(false);
    setScanPage(1);
    api
      .get("/api/library-dedup-log")
      .then((d) => { if (mounted.current && epoch === loadEpoch.current.scan) setScanLog(d.log || []); })
      .catch(() => { if (mounted.current && epoch === loadEpoch.current.scan) setScanLogError(true); })
      .finally(() => { if (mounted.current && epoch === loadEpoch.current.scan) setScanLogLoading(false); });
  };
  useEffect(() => {
    if (isAdmin) loadScanLog();
  }, [isAdmin]);

  const doDelete = async () => {
    if (!pendingDel) return;
    const slug = pendingDel.slug;
    setPendingDel(null);
    try {
      await api.del(`/api/templates/${encodeURIComponent(slug)}`);
      toast.success("已删除模板");
      setItems((t) => t.filter((x) => x.slug !== slug));
    } catch (e: any) {
      toast.error(e.message || "删除失败");
    }
  };

  const doDeleteLesson = async () => {
    if (!pendingLessonDel) return;
    const slug = pendingLessonDel.slug;
    setPendingLessonDel(null);
    try {
      await api.del(`/api/lessons/${encodeURIComponent(slug)}`);
      toast.success("已删除教训");
      setLessonItems((t) => t.filter((x) => x.slug !== slug));
    } catch (e: any) {
      toast.error(e.message || "删除失败");
    }
  };

  const closeShare = () => {
    shareEpoch.current++;
    shareInFlight.current = false;
    setShareTarget(null);
    setShareTitle(""); setShareKeywords(""); setShareBody("");
    setShareConfirmed(false); setShareBusy(false); setShareError("");
  };

  const openShare = (kind: "templates" | "lessons", entry: Template | Lesson) => {
    closeShare();
    setShareTarget({ kind, entry });
    setShareTitle(entry.title); setShareKeywords(entry.keywords.join("，")); setShareBody(entry.body);
  };

  const confirmShare = async () => {
    if (!shareTarget || !shareConfirmed || shareInFlight.current) return;
    const epoch = shareEpoch.current;
    shareInFlight.current = true;
    setShareBusy(true); setShareError("");
    try {
      await api.post(`/api/${shareTarget.kind}/${encodeURIComponent(shareTarget.entry.slug)}/share`, {
        title: shareTitle.trim(), keywords: shareKeywords.split(/[,，]/).map(s => s.trim()).filter(Boolean),
        body: shareBody.trim(), data_type: shareTarget.entry.data_type,
        expected_source_digest: shareTarget.entry.content_digest, confirmed: true,
      });
      if (!mounted.current || epoch !== shareEpoch.current) return;
      const kind = shareTarget.kind;
      closeShare();
      toast.success("通用副本已共享，个人原件保持不变");
      if (kind === "templates") load(); else loadLessons();
    } catch {
      if (mounted.current && epoch === shareEpoch.current) setShareError("分享未确认。请刷新核对条目，再预览并明确重试。");
    } finally {
      if (mounted.current && epoch === shareEpoch.current) { shareInFlight.current = false; setShareBusy(false); }
    }
  };

  // 分页切片（页码超出总页数时自动 clamp 到有效范围，删除后不会越界）
  const filteredTemplates = items.filter(entry => matchesFilter(entry, filters.templates));
  const filteredLessons = lessonItems.filter(entry => matchesFilter(entry, filters.lessons));
  const dataTypes = [...new Set((tab === "lessons" ? lessonItems : items).map(entry => entry.data_type).filter(Boolean))].sort();
  const tplTotalPages = Math.max(1, Math.ceil(filteredTemplates.length / PAGE_SIZE));
  const tplPageClamped = Math.min(tplPage, tplTotalPages);
  const tplPaged = filteredTemplates.slice((tplPageClamped - 1) * PAGE_SIZE, tplPageClamped * PAGE_SIZE);
  const lessonTotalPages = Math.max(1, Math.ceil(filteredLessons.length / PAGE_SIZE));
  const lessonPageClamped = Math.min(lessonPage, lessonTotalPages);
  const lessonPaged = filteredLessons.slice((lessonPageClamped - 1) * PAGE_SIZE, lessonPageClamped * PAGE_SIZE);
  const scanTotalPages = Math.max(1, Math.ceil(scanLog.length / PAGE_SIZE));
  const scanPageClamped = Math.min(scanPage, scanTotalPages);
  const scanPaged = scanLog.slice((scanPageClamped - 1) * PAGE_SIZE, scanPageClamped * PAGE_SIZE);

  return (
    <>
      <TaskContextLibrary key={user.user_id} open={contextOpen} onClose={() => setContextOpen(false)} onChanged={() => undefined} />
      <header className="flex flex-col gap-4 border-b border-border px-7 py-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold tracking-tight">
            {tab === "templates" ? "模板库" : tab === "lessons" ? "教训库" : "巡检报告"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {tab === "templates"
              ? "同类任务自动匹配方法建议，当前要求优先。默认仅本人使用；确认通用副本后才共享。"
              : tab === "lessons"
              ? "经验默认仅本人使用，转正后可贡献通用副本；共享副本独立累计使用效果。"
              : "定时巡检最近记录（语义去重合并 + 长期停滞草稿清理，默认关闭）"}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <PageGuide page={`templates.${tab}`} ready={!(tab === "templates" ? loading : tab === "lessons" ? lessonLoading : scanLogLoading)} />
          <div data-guide="library-tabs" className="flex rounded-md border border-border p-0.5">
            <Button
              variant={tab === "templates" ? "default" : "ghost"}
              size="sm"
              onClick={() => setTab("templates")}
              className="h-7"
            >
              模板库
            </Button>
            {(
              <Button
                variant={tab === "lessons" ? "default" : "ghost"}
                size="sm"
                onClick={() => setTab("lessons")}
                className="h-7"
              >
                教训库
              </Button>
            )}
            {isAdmin && (
              <Button
                variant={tab === "scanLog" ? "default" : "ghost"}
                size="sm"
                onClick={() => setTab("scanLog")}
                className="h-7"
              >
                巡检报告
              </Button>
            )}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={tab === "templates" ? load : tab === "lessons" ? loadLessons : loadScanLog}
            className="gap-1.5"
          >
            <RefreshCw className="h-4 w-4" /> 刷新
          </Button>
        </div>
      </header>

      {tab !== "scanLog" && <section aria-label="模板库筛选与操作" className="flex flex-wrap items-end gap-3 border-b px-7 py-3">
        <label className="min-w-0 flex-1 basis-56 text-xs text-muted-foreground">搜索标题、关键词或正文
          <input type="search" value={filter.query} onChange={event => updateFilter({ ...filter, query: event.target.value })} className="mt-1 h-9 w-full rounded-md border bg-background px-3 text-sm text-foreground focus-visible:ring-2 focus-visible:ring-ring" />
        </label>
        <label className="text-xs text-muted-foreground">任务类型
          <select aria-label="任务类型" value={filter.dataType} onChange={event => updateFilter({ ...filter, dataType: event.target.value })} className="mt-1 block h-9 max-w-48 rounded-md border bg-background px-2 text-foreground focus-visible:ring-2 focus-visible:ring-ring">
            <option value="">全部类型</option>
            {dataTypes.map(value => <option key={value} value={value}>{TYPE_LABELS[value] || value}</option>)}
          </select>
        </label>
        <label className="text-xs text-muted-foreground">状态
          <select aria-label="状态" value={filter.status} onChange={event => updateFilter({ ...filter, status: event.target.value })} className="mt-1 block h-9 rounded-md border bg-background px-2 text-foreground focus-visible:ring-2 focus-visible:ring-ring">
            <option value="">全部状态</option><option value="draft">草稿</option><option value="active">已转正</option><option value="retired">已停用</option>
          </select>
        </label>
        <Button variant="ghost" size="sm" onClick={() => updateFilter(emptyFilter)}>清除筛选</Button>
        {tab === "templates" && <Button size="sm" className="h-9 shrink-0" onClick={() => setContextOpen(true)}>管理任务模板与个人记忆</Button>}
      </section>}

      {tab === "templates" ? (
        <div className="flex-1 overflow-y-auto px-7 py-6">
          {loading ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : !items.length ? (
            <div className="mx-auto max-w-md py-16 text-center">
              <Library className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                暂无已学模板。
                <br />
                这里汇集可复用的任务处理方法。Mangrove 会自动积累，并在同类任务中参考使用，无需手动配置。
              </p>
            </div>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {tplPaged.map((t) => {
                const sm = STATUS_META[t.status] || STATUS_META.active;
                return (
                  <Card key={t.slug} className="flex flex-col animate-fade-in">
                    <CardContent className="flex flex-1 flex-col gap-3 p-4">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate font-medium">{t.title}</div>
                          <span className="text-xs text-muted-foreground">{t.scope === "platform" ? "平台共享" : "仅本人"}</span>
                          <div className="truncate text-[11px] text-muted-foreground">
                            {t.data_type || "通用"} · {t.slug}
                          </div>
                        </div>
                        <Badge variant={sm.variant} className="shrink-0">
                          {sm.label}
                        </Badge>
                      </div>

                      {t.keywords.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {t.keywords.slice(0, 6).map((k) => (
                            <span
                              key={k}
                              className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
                            >
                              <Tag className="h-3 w-3" /> {k}
                            </span>
                          ))}
                        </div>
                      )}

                      <p className="line-clamp-3 flex-1 text-xs leading-relaxed text-muted-foreground">
                        {t.body}
                      </p>

                      <div className="flex items-center justify-between border-t border-border/60 pt-3">
                        <div className="flex gap-4 text-xs text-muted-foreground">
                          <span className="inline-flex items-center gap-1">
                            <Repeat className="h-3 w-3" /> 用 {t.uses} 次
                          </span>
                          <span className="inline-flex items-center gap-1">
                            {t.data_type.startsWith("workspace_")
                              ? <><BadgeCheck className="h-3 w-3" /> 已核验 {t.verified_uses ?? "—"} 次</>
                              : <><TrendingUp className="h-3 w-3" /> 均分 {t.quality_avg || "—"}</>}
                          </span>
                        </div>
                        <div className="flex gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setPreview(t)}
                            title="查看模板正文"
                            className="h-7 w-7 text-muted-foreground hover:text-foreground"
                          >
                            <Eye className="h-4 w-4" />
                          </Button>
                          {t.is_owner && t.scope === "owner" && (
                            <Button variant="ghost" size="icon" aria-label="共享通用副本" title="共享通用副本" onClick={() => openShare("templates", t)} className="h-7 w-7">
                              <Share2 className="h-4 w-4" />
                            </Button>
                          )}
                          {t.can_delete && (
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => setPendingDel(t)}
                              title="删除模板"
                              className="h-7 w-7 text-muted-foreground hover:text-destructive"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          )}
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
          {!loading && items.length > 0 && !filteredTemplates.length && <p role="status" className="py-8 text-center text-sm text-muted-foreground">没有匹配的记录，请调整筛选条件。</p>}
          {!loading && filteredTemplates.length > 0 && (
            <Pagination
              page={tplPageClamped}
              totalPages={tplTotalPages}
              total={filteredTemplates.length}
              onChange={setTplPage}
            />
          )}
        </div>
      ) : tab === "lessons" ? (
        <div className="flex-1 overflow-y-auto px-7 py-6">
          {lessonLoading ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : !lessonItems.length ? (
            <div className="mx-auto max-w-md py-16 text-center">
              <Library className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                暂无教训记录。
                <br />
                新工作台任务出现明确业务失败时，可自动积累教训草稿；连接故障或核验无结论不算业务教训。实际采用、对应风险通过独立核验并正式交付后，才计为有效；历史任务不补学。
              </p>
            </div>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {lessonPaged.map((t) => {
                const sm = LESSON_STATUS_META[t.status] || LESSON_STATUS_META.draft;
                return (
                  <Card key={t.slug} className="flex flex-col animate-fade-in">
                    <CardContent className="flex flex-1 flex-col gap-3 p-4">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate font-medium">{t.title}</div>
                          <span className="text-xs text-muted-foreground">{t.scope === "platform" ? "平台共享" : "仅本人"}</span>
                          <div className="truncate text-[11px] text-muted-foreground">
                            {t.data_type || "通用"} · {t.slug}
                          </div>
                        </div>
                        <Badge variant={sm.variant} className="shrink-0">
                          {sm.label}
                        </Badge>
                      </div>

                      {t.keywords.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {t.keywords.slice(0, 6).map((k) => (
                            <span
                              key={k}
                              className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
                            >
                              <Tag className="h-3 w-3" /> {k}
                            </span>
                          ))}
                        </div>
                      )}

                      <p className="line-clamp-3 flex-1 text-xs leading-relaxed text-muted-foreground">
                        {t.body}
                      </p>

                      <div className="flex items-center justify-between border-t border-border/60 pt-3">
                        <div className="flex gap-4 text-xs text-muted-foreground">
                          <span className="inline-flex items-center gap-1">
                            <Repeat className="h-3 w-3" /> 发生 {t.occurrences} 次
                          </span>
                          <span className="inline-flex items-center gap-1" title="该教训帮后续任务避免同类失败的次数">
                            <BadgeCheck className="h-3 w-3" /> 有效 {t.helped_avoid ?? 0} 次
                          </span>
                        </div>
                        <div className="flex gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setLessonPreview(t)}
                            title="查看教训正文"
                            className="h-7 w-7 text-muted-foreground hover:text-foreground"
                          >
                            <Eye className="h-4 w-4" />
                          </Button>
                          {t.is_owner && t.scope === "owner" && t.status === "active" && (
                            <Button variant="ghost" size="icon" aria-label="共享通用副本" title="共享通用副本" onClick={() => openShare("lessons", t)} className="h-7 w-7">
                              <Share2 className="h-4 w-4" />
                            </Button>
                          )}
                          {t.can_delete && (
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => setPendingLessonDel(t)}
                              title="删除教训"
                              className="h-7 w-7 text-muted-foreground hover:text-destructive"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          )}
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
          {!lessonLoading && lessonItems.length > 0 && !filteredLessons.length && <p role="status" className="py-8 text-center text-sm text-muted-foreground">没有匹配的记录，请调整筛选条件。</p>}
          {!lessonLoading && filteredLessons.length > 0 && (
            <Pagination
              page={lessonPageClamped}
              totalPages={lessonTotalPages}
              total={filteredLessons.length}
              onChange={setLessonPage}
            />
          )}
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-7 py-6">
          {scanLogLoading ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : scanLogError ? (
            <p role="alert" className="text-sm text-destructive">巡检报告加载失败，请刷新重试</p>
          ) : !scanLog.length ? (
            <div className="mx-auto max-w-md py-16 text-center">
              <Library className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                暂无巡检记录（巡检开关默认关闭，可在「设置 → 配置中心 → 知识库巡检」开启）。
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {scanPaged.map((row) => {
                return (
                  <Card key={row.id}>
                    <CardContent className="flex flex-wrap items-center gap-4 p-4 text-sm">
                      <span className="text-muted-foreground">{row.ran_at}</span>
                      <span>
                        模板：扫描 {row.templates_scanned} · 合并 {row.templates_merged}
                      </span>
                      <span>
                        教训：扫描 {row.lessons_scanned} · 合并 {row.lessons_merged}
                      </span>
                      <span>清理停滞草稿 {row.stale_drafts_deleted} 条</span>

                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
          {!scanLogLoading && !scanLogError && scanLog.length > 0 && (
            <Pagination
              page={scanPageClamped}
              totalPages={scanTotalPages}
              total={scanLog.length}
              onChange={setScanPage}
            />
          )}
        </div>
      )}

      {/* 模板正文预览 */}
      <Modal open={!!preview} onClose={() => setPreview(null)} title={preview?.title}>
        <div className="max-h-[60vh] overflow-y-auto text-sm">
          {preview && <Markdown>{preview.body}</Markdown>}
        </div>
        <div className="mt-4 flex justify-end">
          <Button variant="outline" size="sm" onClick={() => setPreview(null)}>
            关闭
          </Button>
        </div>
      </Modal>

      {/* 模板删除确认 */}
      <Modal open={!!pendingDel} onClose={() => setPendingDel(null)} title="删除模板">
        <p className="text-sm text-muted-foreground">
          确定删除模板「{pendingDel?.title}」？此操作不可撤销。
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setPendingDel(null)}>
            取消
          </Button>
          <Button variant="destructive" size="sm" onClick={doDelete}>
            删除
          </Button>
        </div>
      </Modal>

      {/* 教训正文预览 */}
      <Modal open={!!lessonPreview} onClose={() => setLessonPreview(null)} title={lessonPreview?.title}>
        <div className="max-h-[60vh] overflow-y-auto text-sm">
          {lessonPreview && <Markdown>{lessonPreview.body}</Markdown>}
        </div>
        <div className="mt-4 flex justify-end">
          <Button variant="outline" size="sm" onClick={() => setLessonPreview(null)}>
            关闭
          </Button>
        </div>
      </Modal>

      {/* 教训删除确认 */}
      <Modal open={!!pendingLessonDel} onClose={() => setPendingLessonDel(null)} title="删除教训">
        <p className="text-sm text-muted-foreground">
          确定删除教训「{pendingLessonDel?.title}」？此操作不可撤销。
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setPendingLessonDel(null)}>
            取消
          </Button>
          <Button variant="destructive" size="sm" onClick={doDeleteLesson}>
            删除
          </Button>
        </div>
      </Modal>

      {/* 只确认用户当前预览的通用副本；内容一旦修改须重新勾选。 */}
      <Modal open={!!shareTarget} onClose={closeShare} title="共享通用副本" wide>
        <div className="max-h-[65vh] overflow-y-auto space-y-3">
          <p className="text-sm text-muted-foreground">仅共享下面这份副本。请移除个人信息、凭据和业务专属内容；原件保持仅本人可见。</p>
          <label className="block text-sm">副本标题
            <input value={shareTitle} maxLength={200} disabled={shareBusy} onChange={e => { setShareTitle(e.target.value); setShareConfirmed(false); }} className="mt-1 w-full rounded border border-input bg-transparent px-3 py-2" />
          </label>
          <label className="block text-sm">副本关键词（逗号分隔）
            <input value={shareKeywords} disabled={shareBusy} onChange={e => { setShareKeywords(e.target.value); setShareConfirmed(false); }} className="mt-1 w-full rounded border border-input bg-transparent px-3 py-2" />
          </label>
          <label className="block text-sm">副本正文
            <textarea value={shareBody} maxLength={100000} disabled={shareBusy} onChange={e => { setShareBody(e.target.value); setShareConfirmed(false); }} className="mt-1 h-48 w-full rounded border border-input bg-transparent px-3 py-2" />
          </label>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={shareConfirmed} disabled={shareBusy} onChange={e => setShareConfirmed(e.target.checked)} className="mt-1" />
            我已移除个人信息、凭据和业务专属内容，同意共享此副本
          </label>
          {shareError && <p role="alert" className="text-sm text-destructive">{shareError}</p>}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={closeShare}>关闭</Button>
          <Button onClick={confirmShare} disabled={shareBusy || !shareConfirmed || !shareTitle.trim() || !shareBody.trim()}>{shareBusy ? "正在共享…" : "确认共享"}</Button>
        </div>
      </Modal>
    </>
  );
}
