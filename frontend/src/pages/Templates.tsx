import { useEffect, useState } from "react";
import { BadgeCheck, Library, Merge, Trash2, RefreshCw, Eye, Tag, TrendingUp, Repeat } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { Pagination } from "@/components/ui/pagination";
import { Markdown } from "@/components/Markdown";
import { api } from "@/lib/api";
import { useAuth, isAdminish } from "@/lib/auth";

interface Template {
  slug: string;
  title: string;
  data_type: string;
  keywords: string[];
  body: string;
  status: string; // active / draft / retired
  uses: number;
  quality_avg: number;
}

interface Lesson {
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
  details: string; // 该轮每步操作的 JSON 数组，空串表示无明细
}

// 巡检操作明细（后端 JSON 反序列化）
interface ScanDetail {
  action: "merge_template" | "merge_lesson" | "stale_delete_template" | "stale_delete_lesson";
  data_type?: string;
  // 合并类字段
  survivor_slug?: string;
  survivor_title?: string;
  loser_slug?: string;
  loser_title?: string;
  score?: number;
  threshold?: number;
  // 停滞清理类字段
  slug?: string;
  title?: string;
  stale_days?: number;
  threshold_days?: number;
}

// 操作类型 -> 展示文案与图标
const ACTION_META: Record<string, { label: string; icon: typeof Eye; tone: string }> = {
  merge_template: { label: "模板合并", icon: Merge, tone: "text-blue-500" },
  merge_lesson: { label: "教训合并", icon: Merge, tone: "text-blue-500" },
  stale_delete_template: { label: "清理停滞模板", icon: Trash2, tone: "text-amber-500" },
  stale_delete_lesson: { label: "清理停滞教训", icon: Trash2, tone: "text-amber-500" },
};

function parseScanDetails(raw: string): ScanDetail[] {
  if (!raw) return [];
  try {
    return JSON.parse(raw);
  } catch {
    return [];
  }
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

export function Templates() {
  const { user } = useAuth();
  const isAdmin = isAdminish(user?.role);
  const [tab, setTab] = useState<TabKey>("templates");

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
  const [scanDetail, setScanDetail] = useState<ScanLogRow | null>(null);

  // 分页页码（三个 Tab 各自独立，刷新回到第 1 页）
  const [tplPage, setTplPage] = useState(1);
  const [lessonPage, setLessonPage] = useState(1);
  const [scanPage, setScanPage] = useState(1);

  const load = () => {
    setLoading(true);
    setTplPage(1);
    api
      .get("/api/templates")
      .then((d) => setItems(d.templates || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const loadLessons = () => {
    setLessonLoading(true);
    setLessonPage(1);
    api
      .get("/api/lessons")
      .then((d) => setLessonItems(d.lessons || []))
      .catch(() => {})
      .finally(() => setLessonLoading(false));
  };
  useEffect(() => {
    if (isAdmin) loadLessons();
  }, [isAdmin]);

  const loadScanLog = () => {
    setScanLogLoading(true);
    setScanPage(1);
    api
      .get("/api/library-dedup-log")
      .then((d) => setScanLog(d.log || []))
      .catch(() => {})
      .finally(() => setScanLogLoading(false));
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

  // 分页切片（页码超出总页数时自动 clamp 到有效范围，删除后不会越界）
  const tplTotalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const tplPageClamped = Math.min(tplPage, tplTotalPages);
  const tplPaged = items.slice((tplPageClamped - 1) * PAGE_SIZE, tplPageClamped * PAGE_SIZE);
  const lessonTotalPages = Math.max(1, Math.ceil(lessonItems.length / PAGE_SIZE));
  const lessonPageClamped = Math.min(lessonPage, lessonTotalPages);
  const lessonPaged = lessonItems.slice((lessonPageClamped - 1) * PAGE_SIZE, lessonPageClamped * PAGE_SIZE);
  const scanTotalPages = Math.max(1, Math.ceil(scanLog.length / PAGE_SIZE));
  const scanPageClamped = Math.min(scanPage, scanTotalPages);
  const scanPaged = scanLog.slice((scanPageClamped - 1) * PAGE_SIZE, scanPageClamped * PAGE_SIZE);

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-7 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">
            {tab === "templates" ? "模板库" : tab === "lessons" ? "教训库" : "巡检报告"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {tab === "templates"
              ? "自学习沉淀的分析模板（草稿达标转正、低质淘汰）· 全局共享"
              : tab === "lessons"
              ? "自学习沉淀的失败教训（失败2次+至少1次有效→转正；10次失败从无效→退役）· 全局共享"
              : "定时巡检最近记录（语义去重合并 + 长期停滞草稿清理，默认关闭）"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border border-border p-0.5">
            <Button
              variant={tab === "templates" ? "default" : "ghost"}
              size="sm"
              onClick={() => setTab("templates")}
              className="h-7"
            >
              模板库
            </Button>
            {isAdmin && (
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
                在对话工作区跑一个未命中内置领域的任务，完成后确认「沉淀为模板」即可积累。
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
                            <TrendingUp className="h-3 w-3" /> 均分 {t.quality_avg || "—"}
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
                          {isAdmin && (
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
          {!loading && items.length > 0 && (
            <Pagination
              page={tplPageClamped}
              totalPages={tplTotalPages}
              total={items.length}
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
                当任务被判定采集失败时，Agent 会自动蒸馏教训；累计 2 次失败且该教训至少 1 次帮到后续任务再转正。无效教训（10 次失败从未帮到）自动退役。
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
                            <Repeat className="h-3 w-3" /> 命中 {t.occurrences} 次
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
                          {isAdmin && (
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
          {!lessonLoading && lessonItems.length > 0 && (
            <Pagination
              page={lessonPageClamped}
              totalPages={lessonTotalPages}
              total={lessonItems.length}
              onChange={setLessonPage}
            />
          )}
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-7 py-6">
          {scanLogLoading ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
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
                const hasDetails = row.templates_merged + row.lessons_merged + row.stale_drafts_deleted > 0;
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
                      {hasDetails && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setScanDetail(row)}
                          className="ml-auto gap-1.5 text-muted-foreground hover:text-foreground"
                        >
                          <Eye className="h-4 w-4" /> 查看详情
                        </Button>
                      )}
                    </CardContent>
                  </Card>
                );
              })}
            </div>
          )}
          {!scanLogLoading && scanLog.length > 0 && (
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

      {/* 巡检详情 */}
      <Modal
        open={!!scanDetail}
        onClose={() => setScanDetail(null)}
        title={`巡检详情 · ${scanDetail?.ran_at ?? ""}`}
      >
        {(() => {
          const details = parseScanDetails(scanDetail?.details ?? "");
          return (
            <>
              <div className="max-h-[60vh] space-y-3 overflow-y-auto">
                {details.length ? (
                  details.map((d, i) => {
                    const meta = ACTION_META[d.action] || { label: d.action, icon: Eye, tone: "" };
                    const Icon = meta.icon;
                    const isMerge = d.action === "merge_template" || d.action === "merge_lesson";
                    return (
                      <div key={i} className="rounded-md border border-border/60 p-3 text-sm">
                        <div className="flex items-center gap-2">
                          <Icon className={`h-4 w-4 ${meta.tone}`} />
                          <span className="font-medium">{meta.label}</span>
                        </div>
                        {isMerge ? (
                          <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                            <div>
                              保留：<span className="text-foreground">{d.survivor_title}</span>
                              <span className="ml-1 text-muted-foreground/70">({d.survivor_slug})</span>
                            </div>
                            <div>
                              合并：<span className="text-foreground">{d.loser_title}</span>
                              <span className="ml-1 text-muted-foreground/70">({d.loser_slug})</span>
                            </div>
                            <div>
                              类型：{d.data_type || "通用"} · 相似度 {d.score?.toFixed(2)} ≥ 阈值{" "}
                              {d.threshold?.toFixed(2)}
                            </div>
                          </div>
                        ) : (
                          <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                            <div>
                              清理：<span className="text-foreground">{d.title}</span>
                              <span className="ml-1 text-muted-foreground/70">({d.slug})</span>
                            </div>
                            <div>
                              类型：{d.data_type || "通用"} · 停滞 {d.stale_days} 天 ≥ 阈值{" "}
                              {d.threshold_days} 天
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })
                ) : (
                  <p className="text-sm text-muted-foreground">本轮无实际操作（仅扫描计数）。</p>
                )}
              </div>
              <div className="mt-4 flex justify-end">
                <Button variant="outline" size="sm" onClick={() => setScanDetail(null)}>
                  关闭
                </Button>
              </div>
            </>
          );
        })()}
      </Modal>
    </>
  );
}
