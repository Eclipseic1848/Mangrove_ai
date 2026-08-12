# 教训库前端管理页面设计

## 背景

C 阶段（结构性升级）原概念范围包含三块：定时巡检、前端模板管理增强、评测基线扩展——三者交付物和消费方完全不同，brainstorming 前已跟用户确认拆成三个独立的 spec/plan 周期，本 spec 只覆盖第一个：**教训库前端管理页面**。

现状核查（brainstorming 阶段已确认，非假设）：模板库（`data/templates/`）其实**已经有**完整的前端管理页面 `frontend/src/pages/Templates.tsx`（卡片列表：标题/`data_type`/关键词/状态徽标 draft-草稿/active-已转正/retired-已淘汰/`uses`使用次数/`quality_avg`均分/正文预览/管理员删除），后端 `src/api/routes/templates_routes.py` 提供 `GET /api/templates`（任意登录用户）+ `DELETE /api/templates/{slug}`（仅管理员）。

B2 阶段新增的教训库（`data/lessons/`）目前**只有后端逻辑**（`src/memory/lessons.py`），既没有对应的管理路由，也没有前端页面——B2 设计时按 YAGNI 明确排除了这部分（"不做教训库的前端管理页面"），现在用户希望补齐，让教训库也能被看见、被管理。

## 目标

- 在现有 `Templates.tsx` 页面内加一个 Tab 切换（"模板库" / "教训库"），教训库 Tab 展示 `data/lessons/` 的全部条目：标题、`data_type`、关键词、状态（`draft`/`active`，无 `retired`）、`occurrences`（累计命中次数）、正文预览、管理员删除。
- 后端补齐对称的只读+删除接口（`GET /api/lessons`、`DELETE /api/lessons/{slug}`），复用现有鉴权模式（读不限、删仅管理员）。

**不做**（已在 brainstorming 阶段与用户确认）：
- 不做手动转正 draft→active 或手动退役 active 的干预能力——与模板库页面现有能力保持一致（模板库页面本身也只有查看+删除，没有转正/退役按钮）。
- 不做教训的手动新建/编辑——教训只能由 `record_failure()` 自动蒸馏产生。
- 不做定时巡检、不做评测基线扩展——留给 C 阶段后续两个独立 spec。

## 设计

### 1. 后端：`delete_lesson()`

`src/memory/lessons.py` 新增：

```python
def delete_lesson(slug: str) -> bool:
    """删除一条已学教训：移除 data/lessons/<slug>.md。供前端教训库管理使用。
    文件不存在返回 False。"""
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

与 `templates.py` 的 `delete_template()` 对称，但不需要清理向量缓存（教训库没有 `_vectors.json` 这类持久化缓存，B2 设计已明确不引入）。

### 2. 后端：`src/api/routes/lessons_routes.py`（新文件）

与 `templates_routes.py` 逐行对称：

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

`src/memory/__init__.py` 导出新增 `delete_lesson`。`src/api/main.py` 挂载 `lessons_routes.router`（照抄现有挂载 `templates_routes.router` 的那一行，改路由变量名）。

### 3. 前端：`Templates.tsx` 加 Tab

- 顶部新增 Tab 切换（"模板库" / "教训库"），复用现有 UI 组件库里的 tab/segmented-control 模式（若项目里已有现成 Tab 组件则直接用，没有则用简单的两个 Button 做 active/inactive 样式切换，不引入新依赖）。
- 新增 `Lesson` 接口与 `LESSON_STATUS_META`（只有 `draft`/`active` 两档，复用 `Badge` 的 `warning`/`success` variant）。
- 教训 Tab 的数据加载函数 `loadLessons()` 请求 `/api/lessons`，字段映射到卡片：标题、`data_type`、关键词标签（复用现有 `Tag` 图标渲染逻辑）、状态徽标、`occurrences`（复用 `Repeat` 图标，文案"命中 N 次"）、正文预览（复用现有 `Modal`+`Markdown`）、管理员删除（复用现有确认弹窗 `pendingDel` 模式，教训与模板各自独立一份 `pendingDel` 状态，不共用同一个 state 变量，避免误删）。
- 教训 Tab 空状态文案："暂无教训记录。当任务被判定采集失败时，Agent 会自动蒸馏教训，累计命中 2 次后转正开始生效。"
- 只做查看+删除，两个 Tab 各自独立维护 `items`/`loading`/`preview`/`pendingDel` 状态（不合并成一套通用 state，字段形状不同，分开更清晰）。
- 页面顶部标题/说明文案按当前 Tab 切换（"模板库" + 原有说明 / "教训库" + 新说明）。

### 4. 权限

与模板库完全一致：`GET` 任意登录用户可读，`DELETE` 仅管理员（`isAdminish(user?.role)` 判断是否显示删除按钮，与现有模板卡片逻辑一致）。

## 测试计划

- 后端：`scripts/test_lesson_learning.py` 新增 `test_delete_lesson_removes_file_returns_true` / `test_delete_lesson_missing_returns_false`（模式参照现有 `load_lessons`/`record_failure` 测试的临时目录隔离方式）。
- 后端路由手工验证：`GET /api/lessons`（登录态）返回列表；`DELETE /api/lessons/{slug}`（非管理员）应 403（复用现有 `require_admin` 依赖，不需要新写权限测试，与 `templates_routes.py` 的权限行为完全一致，已被现有集成方式覆盖）。
- 前端：`cd frontend && npm run build` 编译通过；手工验证 Tab 切换、教训空状态文案、正文预览弹窗、管理员可见删除按钮且能真实删除、非管理员看不到删除按钮。

## 验证

1. 单元测试全绿（`test_lesson_learning.py` 新增 2 项）
2. `npm run build` 编译通过
3. 手工验证：登录管理员账号，访问模板库页面，能看到"教训库" Tab；若 `data/lessons/` 里有真实教训数据（B2 上线后应该已经有），能看到卡片列表、点开正文预览、删除后列表刷新消失；用非管理员账号验证看不到删除按钮
