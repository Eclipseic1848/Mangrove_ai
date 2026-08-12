# 教训库前端管理页面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `data/lessons/`（B2 阶段新增的失败教训库）补一个和模板库对称的前端只读+删除管理页面，合并进现有 `Templates.tsx` 的 Tab 切换里。

**Architecture:** 后端新增 `delete_lesson()`（`src/memory/lessons.py`）+ 对称路由 `src/api/routes/lessons_routes.py`（`GET /api/lessons` 任意登录用户、`DELETE /api/lessons/{slug}` 仅管理员），挂载进 `main.py`；前端在 `Templates.tsx` 顶部加两个 Tab 按钮（"模板库"/"教训库"），教训 Tab 独立维护自己的 `items`/`loading`/`preview`/`pendingDel` 状态与卡片渲染（字段与模板卡片不同：`occurrences` 代替 `uses`/`quality_avg`，状态只有 draft/active 两档）。

**Tech Stack:** FastAPI（后端路由）、React + TypeScript（前端，复用现有 `Card`/`Badge`/`Button`/`Modal`/`Markdown` 组件与 `api` 请求封装）。

## Global Constraints

- 只做查看+删除，不做手动转正/退役、不做手动新建/编辑教训（与模板库页面现有能力对齐，brainstorming 阶段已确认）。
- 权限模型与模板库完全一致：`GET` 任意登录用户可读，`DELETE` 仅管理员（复用 `src/api/auth.py` 的 `get_current_user`/`require_admin`）。
- 教训删除不需要清理向量缓存（教训库没有 `_vectors.json` 这类持久化缓存，B2 设计已明确不引入）。
- 两个 Tab 的前端状态各自独立维护（`items`/`loading`/`preview`/`pendingDel`），不合并成一套通用 state——字段形状不同（模板有 `uses`/`quality_avg`，教训有 `occurrences`），分开更清晰，不做不必要的抽象。
- 后端改动需重启进程生效；前端改动需 `cd frontend && npm run build` 才能在用户实际访问的 8088 端口生效（Vite dev server 的 5173 不是用户使用的入口）。

---

## Task 1: 后端 — delete_lesson + lessons_routes.py

**Files:**
- Modify: `src/memory/lessons.py`（追加 `delete_lesson`）
- Modify: `src/memory/__init__.py`（导出 `delete_lesson`）
- Create: `src/api/routes/lessons_routes.py`
- Modify: `src/api/main.py:34-37,63-66`（导入并挂载新路由）
- Test: `scripts/test_lesson_learning.py`（追加 2 个测试）

**Interfaces:**
- Consumes：`src.memory.lessons.LESSONS_DIR`（已有模块级常量）、`src.api.auth.get_current_user`/`require_admin`（与 `templates_routes.py` 完全相同的用法）。
- Produces：`delete_lesson(slug: str) -> bool`（文件不存在返回 `False`，供 `lessons_routes.py` 使用）；`GET /api/lessons` 返回 `{"lessons": [...]}`；`DELETE /api/lessons/{slug}` 成功返回 `{"ok": True}`，不存在返回 404。

- [ ] **Step 1: 写失败的测试**

在 `scripts/test_lesson_learning.py` 的 `def main():` 之前追加（该文件已有 `_setup_tmp`/`_write_lesson` 辅助函数，直接复用）：

```python
def test_delete_lesson_removes_file_returns_true():
    d = _setup_tmp()
    _write_lesson(d, "existing", "旧教训", "comment", ["小众品牌"], "旧正文", status="active", occurrences=2)
    assert lesson.delete_lesson("existing") is True
    assert lesson.load_lessons() == []


def test_delete_lesson_missing_returns_false():
    _setup_tmp()
    assert lesson.delete_lesson("not-a-real-slug") is False
```

同时把 `main()` 里的 `tests` 列表追加这两项（保持现有列表其余项不变，直接在列表末尾加）：

```python
        test_delete_lesson_removes_file_returns_true,
        test_delete_lesson_missing_returns_false,
```

- [ ] **Step 2: 运行测试确认失败**

Run: `E:/python3.13/python.exe scripts/test_lesson_learning.py`
Expected: 新增的 2 项因 `AttributeError: module 'src.memory.lessons' has no attribute 'delete_lesson'` 失败，其余原有 13 项仍 PASS。

- [ ] **Step 3: 在 `src/memory/lessons.py` 末尾追加 `delete_lesson`**

```python
def delete_lesson(slug: str) -> bool:
    """删除一条已学教训：移除 data/lessons/<slug>.md。供前端教训库管理使用。
    文件不存在返回 False。教训库没有向量缓存文件，无需额外清理步骤。"""
    path = LESSONS_DIR / f"{slug}.md"
    if not path.is_file():
        return False
    try:
        path.unlink()
    except Exception:
        logger.warning("删除教训失败：%s", slug, exc_info=True)
        return False
    logger.info("已删除教训：%s", slug)
    return True
```

- [ ] **Step 4: 运行测试确认通过**

Run: `E:/python3.13/python.exe scripts/test_lesson_learning.py`
Expected: `15/15 通过`

- [ ] **Step 5: 更新 `src/memory/__init__.py` 导出**

把文件内容改为：

```python
"""记忆与技能层：跨会话用户偏好 + 个人记忆 + 任务技能复用 + 分析模板自学习 + 失败教训分流。"""
from .lessons import delete_lesson, lesson_for_analyze, lesson_for_planner, record_failure
from .loader import (
    add_preference,
    load_preferences,
    load_skills,
    personal_context,
    preferences_context,
    skill_for_analysis,
    skills_for_planner,
)
from .templates import (
    delete_template,
    distill_template,
    find_duplicate,
    load_templates,
    match_template,
    record_template_use,
    save_template,
)

__all__ = [
    "load_preferences",
    "preferences_context",
    "personal_context",
    "add_preference",
    "load_skills",
    "skill_for_analysis",
    "skills_for_planner",
    "load_templates",
    "match_template",
    "save_template",
    "distill_template",
    "record_template_use",
    "find_duplicate",
    "delete_template",
    "record_failure",
    "lesson_for_analyze",
    "lesson_for_planner",
    "delete_lesson",
]
```

- [ ] **Step 6: 新建 `src/api/routes/lessons_routes.py`**

```python
"""教训库路由：查看 / 删除已学失败教训（全局共享知识库，多用户不隔离）。

教训的产出走 checker.py 判定失败后自动蒸馏（record_failure），此处只做浏览与管理。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from src.memory import delete_lesson, load_lessons

from ..auth import get_current_user, require_admin

router = APIRouter(prefix="/api/lessons", tags=["lessons"])


@router.get("")
def list_lessons(user=Depends(get_current_user)):
    """返回全部已学教训（含 status/occurrences 等自学习字段）。所有登录用户可读。"""
    return {"lessons": load_lessons()}


@router.delete("/{slug}")
def remove_lesson(slug: str, admin=Depends(require_admin)):
    """删除一条教训文件。全局共享知识库，仅管理员可写。"""
    if not delete_lesson(slug):
        raise HTTPException(status_code=404, detail="教训不存在")
    return {"ok": True}
```

- [ ] **Step 7: 挂载新路由（`src/api/main.py`）**

第 34-37 行的导入：

```python
from src.api.routes import (  # noqa: E402
    admin_routes, auth_routes, chat, config_routes, confirm, conversations, downloads,
    memory_routes, models, overview, settings_routes, tasks, templates_routes,
)
```

改为：

```python
from src.api.routes import (  # noqa: E402
    admin_routes, auth_routes, chat, config_routes, confirm, conversations, downloads,
    lessons_routes, memory_routes, models, overview, settings_routes, tasks, templates_routes,
)
```

第 63-66 行：

```python
for r in (auth_routes, conversations, chat, confirm, tasks, models, downloads,
          memory_routes, overview, templates_routes, settings_routes, admin_routes,
          config_routes):
    app.include_router(r.router)
```

改为：

```python
for r in (auth_routes, conversations, chat, confirm, tasks, models, downloads,
          memory_routes, overview, templates_routes, lessons_routes, settings_routes,
          admin_routes, config_routes):
    app.include_router(r.router)
```

- [ ] **Step 8: Commit**

```bash
git add src/memory/lessons.py src/memory/__init__.py src/api/routes/lessons_routes.py \
        src/api/main.py scripts/test_lesson_learning.py
git commit -m "feat: 新增教训库管理接口 delete_lesson + GET/DELETE /api/lessons"
```

---

## Task 2: 前端 — Templates.tsx 加教训库 Tab

**Files:**
- Modify: `frontend/src/pages/Templates.tsx`（全量重写为 Tab 版本）

**Interfaces:**
- Consumes：Task 1 的 `GET /api/lessons`（返回 `{"lessons": [{slug,title,data_type,keywords,body,status,occurrences}]}`）、`DELETE /api/lessons/{slug}`；现有 `@/lib/api` 的 `api.get(path)`/`api.del(path)`（`Promise`，失败抛异常）；现有 `@/lib/auth` 的 `useAuth()`/`isAdminish(role)`；现有 UI 组件 `Card`/`CardContent`/`Badge`/`Button`/`Modal`/`Markdown`（均来自 `@/components/ui/*` 与 `@/components/Markdown`，签名与当前 `Templates.tsx` 里的用法一致，不新增依赖）。
- Produces：无（页面级组件，末端消费方）。

- [ ] **Step 1: 全量替换 `frontend/src/pages/Templates.tsx`**

```tsx
import { useEffect, useState } from "react";
import { Library, Trash2, RefreshCw, Eye, Tag, TrendingUp, Repeat } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
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
  status: string; // draft / active
  occurrences: number;
}

// 状态 -> 展示文案与徽标样式
const STATUS_META: Record<string, { label: string; variant: "success" | "warning" | "outline" }> = {
  active: { label: "已转正", variant: "success" },
  draft: { label: "草稿", variant: "warning" },
  retired: { label: "已淘汰", variant: "outline" },
};

const LESSON_STATUS_META: Record<string, { label: string; variant: "success" | "warning" }> = {
  active: { label: "已转正", variant: "success" },
  draft: { label: "草稿", variant: "warning" },
};

type TabKey = "templates" | "lessons";

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

  const load = () => {
    setLoading(true);
    api
      .get("/api/templates")
      .then((d) => setItems(d.templates || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const loadLessons = () => {
    setLessonLoading(true);
    api
      .get("/api/lessons")
      .then((d) => setLessonItems(d.lessons || []))
      .catch(() => {})
      .finally(() => setLessonLoading(false));
  };
  useEffect(loadLessons, []);

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

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-7 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">
            {tab === "templates" ? "模板库" : "教训库"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {tab === "templates"
              ? "自学习沉淀的分析模板（草稿达标转正、低质淘汰）· 全局共享"
              : "自学习沉淀的失败教训（累计命中2次转正、planner/analyze 自动注入提醒）· 全局共享"}
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
            <Button
              variant={tab === "lessons" ? "default" : "ghost"}
              size="sm"
              onClick={() => setTab("lessons")}
              className="h-7"
            >
              教训库
            </Button>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={tab === "templates" ? load : loadLessons}
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
              {items.map((t) => {
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
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-7 py-6">
          {lessonLoading ? (
            <p className="text-sm text-muted-foreground">加载中…</p>
          ) : !lessonItems.length ? (
            <div className="mx-auto max-w-md py-16 text-center">
              <Library className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                暂无教训记录。
                <br />
                当任务被判定采集失败时，Agent 会自动蒸馏教训，累计命中 2 次后转正开始生效。
              </p>
            </div>
          ) : (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {lessonItems.map((t) => {
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
    </>
  );
}
```

- [ ] **Step 2: 编译验证**

Run: `cd frontend && npm run build`
Expected: 编译通过，无 TypeScript 报错（`Button` 的 `variant` prop 已在现有代码里支持 `"default"`/`"ghost"`/`"outline"`/`"destructive"`，`Badge` 的 `variant` 已支持 `"success"`/`"warning"`/`"outline"`，均为复用现有组件已支持的取值，不新增组件 prop）。

- [ ] **Step 3: 手工验证**

1. 后端重启进程（`E:/python3.13/python.exe -m src.api.main`，或走 `scripts/start_web.ps1`）
2. 浏览器访问 8088，用管理员账号登录，进入模板库页面
3. 确认页面顶部出现"模板库"/"教训库" Tab 切换，默认停在"模板库"，原有功能（列表/预览/删除）不变
4. 切到"教训库" Tab：若 `data/lessons/` 下已有真实数据（B2 上线后应该有），确认卡片正确显示标题/`data_type`/关键词/状态徽标/命中次数；点击预览图标能看到正文；点击删除、确认后卡片消失
5. 若 `data/lessons/` 为空，确认显示空状态文案
6. 换非管理员账号登录，确认教训库 Tab 里看不到删除按钮（预览图标仍在）

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Templates.tsx
git commit -m "feat: 模板库页面新增教训库 Tab（查看+管理员删除）"
```
