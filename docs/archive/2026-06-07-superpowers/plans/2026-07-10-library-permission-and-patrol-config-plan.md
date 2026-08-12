# 模板库权限收紧 + 巡检开关前端化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 教训库Tab与巡检报告Tab（含其后端接口）收紧为仅管理员可见；定时巡检的4个配置项接入配置中心，管理员可在前端直接改并即时生效。

**Architecture:** 两个独立改动集，共用一个spec但分5个任务顺序交付：后端权限收紧（Task1）→前端权限收紧（Task2）→后端巡检开关注册（Task3）→前端巡检开关归类+文案（Task4）→文档同步（Task5）。Task1/2一组，Task3/4一组，彼此不互相依赖，但按此顺序做便于每步都能独立验证。

**Tech Stack:** FastAPI（后端路由/依赖注入）、React + TypeScript（前端）、既有 `src/config/runtime_config.py` REGISTRY 驱动的配置中心机制、既有 `src/api/auth.py` 的 `require_admin`/`get_current_user` RBAC 依赖。

## Global Constraints

- 教训库Tab、巡检报告Tab：前后端都收紧为仅管理员（`require_admin`），普通用户不可见/不可访问（403）。
- 模板库Tab权限模型不变：预览所有登录用户可见，删除仅管理员。
- 定时巡检4个配置项（`library_dedup_scan_enabled`/`library_dedup_scan_interval_hours`/`library_stale_draft_days`/`library_dedup_scan_max_merges_per_run`）均为全局配置（`user: False`），仅管理员可在配置中心调整，不做per-user覆盖。
- 不加验证按钮探测逻辑、不加数值范围校验（沿用REGISTRY现有做法）。
- 项目无前端自动化测试框架，前端改动的验收标准是 `npm run build` 编译通过 + 手工验证步骤（与既有任务一致的项目惯例）。
- 后端测试遵循项目既有惯例：无 pytest 框架，纯 `def test_x(): assert ...` + `main()` 收集 PASS/FAIL 打印 + `sys.exit(1 if failed else 0)`，用 `tempfile.NamedTemporaryFile` 建临时 `WebUIStore`。

---

## Task 1: 后端权限收紧 —— 教训库/巡检报告接口改为仅管理员

**Files:**
- Modify: `src/api/routes/lessons_routes.py`
- Modify: `src/api/routes/library_dedup_routes.py`
- Test: `scripts/test_library_route_permissions.py`（新建）

**Interfaces:**
- Consumes: `src.api.auth.require_admin`（已存在，签名 `require_admin(user: Dict = Depends(get_current_user)) -> Dict`，非管理员抛 `HTTPException(403)`）。
- Produces: 后续任务不依赖本任务产出的新符号（本任务只改路由依赖，不新增函数）。

- [ ] **Step 1: 写失败测试，验证两个路由函数的依赖已切到 require_admin**

创建 `scripts/test_library_route_permissions.py`：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""教训库/巡检报告只读接口权限收紧单测：验证路由函数的 FastAPI 依赖已切到 require_admin。

运行：python scripts/test_library_route_permissions.py
不起真实 HTTP 服务，直接内省路由函数签名里 Depends() 包的目标函数，
这是本项目既有测试风格（无 TestClient），比起 HTTP 层测试更快也不需要建用户/发 token。
"""
import inspect
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.auth import require_admin
from src.api.routes import lessons_routes, library_dedup_routes


def _depends_target(func, param_name: str):
    """取出某个路由函数指定参数上 Depends(...) 包的目标可调用对象。"""
    sig = inspect.signature(func)
    default = sig.parameters[param_name].default
    return default.dependency


def test_list_lessons_requires_admin():
    assert _depends_target(lessons_routes.list_lessons, "admin") is require_admin


def test_remove_lesson_still_requires_admin():
    # 既有的删除接口本来就是 require_admin，回归确认没被误改
    assert _depends_target(lessons_routes.remove_lesson, "admin") is require_admin


def test_list_scan_log_requires_admin():
    assert _depends_target(library_dedup_routes.list_scan_log, "admin") is require_admin


def main():
    tests = [
        test_list_lessons_requires_admin,
        test_remove_lesson_still_requires_admin,
        test_list_scan_log_requires_admin,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python scripts/test_library_route_permissions.py`
Expected: `test_list_lessons_requires_admin` 和 `test_list_scan_log_requires_admin` 两项 FAIL（因为此时这两个函数的参数名还是 `user`、依赖还是 `get_current_user`，`sig.parameters["admin"]` 会直接抛 `KeyError`）；`test_remove_lesson_still_requires_admin` 应该 PASS（这个接口本来就没变）。

- [ ] **Step 3: 修改 lessons_routes.py**

将 `src/api/routes/lessons_routes.py` 第 16-19 行：

```python
@router.get("")
def list_lessons(user=Depends(get_current_user)):
    """返回全部已学教训（含 status/occurrences 等自学习字段）。所有登录用户可读。"""
    return {"lessons": load_lessons()}
```

改为：

```python
@router.get("")
def list_lessons(admin=Depends(require_admin)):
    """返回全部已学教训（含 status/occurrences 等自学习字段）。仅管理员可读（内容偏内部/运维向）。"""
    return {"lessons": load_lessons()}
```

同文件第 11 行的 import 不用改（`get_current_user, require_admin` 已经都导入了），但 `get_current_user` 在本文件不再被使用，改为只导入 `require_admin`：

```python
from ..auth import require_admin
```

- [ ] **Step 4: 修改 library_dedup_routes.py**

将 `src/api/routes/library_dedup_routes.py` 全文改为：

```python
"""模板库/教训库定时巡检报告路由：只读展示最近若干轮巡检摘要。仅管理员可见（运维向内容）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import get_store, require_admin

router = APIRouter(prefix="/api/library-dedup-log", tags=["library-dedup-log"])


@router.get("")
def list_scan_log(admin=Depends(require_admin)):
    """返回最近 20 轮巡检记录（按时间倒序）。仅管理员可读。"""
    return {"log": get_store().library_dedup_scan_log_recent(limit=20)}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python scripts/test_library_route_permissions.py`
Expected: `3/3 通过`

- [ ] **Step 6: Commit**

```bash
git add src/api/routes/lessons_routes.py src/api/routes/library_dedup_routes.py scripts/test_library_route_permissions.py
git commit -m "feat: 教训库/巡检报告只读接口收紧为仅管理员可访问"
```

---

## Task 2: 前端权限收紧 —— 教训库/巡检报告Tab仅管理员可见

**Files:**
- Modify: `frontend/src/pages/Templates.tsx`

**Interfaces:**
- Consumes: `isAdmin`（`Templates.tsx:59`，已存在的 `const isAdmin = isAdminish(user?.role);`）；Task 1 产出的后端403行为（本任务不依赖具体符号，只依赖"非管理员调这两个接口会拿到403"这一行为，用于说明为什么要提前用 `isAdmin` 挡住请求）。
- Produces: 无新符号，后续任务不依赖本任务的产出。

- [ ] **Step 1: 修改数据拉取的 useEffect，非管理员不发起这两个请求**

将 `frontend/src/pages/Templates.tsx` 第 96 行：

```tsx
  useEffect(loadLessons, []);
```

改为：

```tsx
  useEffect(() => {
    if (isAdmin) loadLessons();
  }, [isAdmin]);
```

将同文件第 106 行：

```tsx
  useEffect(loadScanLog, []);
```

改为：

```tsx
  useEffect(() => {
    if (isAdmin) loadScanLog();
  }, [isAdmin]);
```

- [ ] **Step 2: 修改Tab按钮区，非管理员不渲染这两个Tab入口**

将 `frontend/src/pages/Templates.tsx` 第 159-174 行：

```tsx
            <Button
              variant={tab === "lessons" ? "default" : "ghost"}
              size="sm"
              onClick={() => setTab("lessons")}
              className="h-7"
            >
              教训库
            </Button>
            <Button
              variant={tab === "scanLog" ? "default" : "ghost"}
              size="sm"
              onClick={() => setTab("scanLog")}
              className="h-7"
            >
              巡检报告
            </Button>
```

改为：

```tsx
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
```

普通用户的 `tab` state 永远初始化为 `"templates"`（`Templates.tsx:60` 的 `useState<TabKey>("templates")`），且上面两个按钮不渲染后没有任何UI路径能把它切到 `"lessons"`/`"scanLog"`，所以不需要额外加"越权时强制切回模板库"的防御 `useEffect`——这条分支本来就不可达。

- [ ] **Step 3: 编译验证**

Run: `cd frontend && npm run build`
Expected: 编译通过，无 TypeScript 报错

- [ ] **Step 4: 手工验证**

启动前端+后端，分别用普通用户账号和管理员账号登录模板库页面，确认：普通用户只看到「模板库」一个Tab按钮；管理员看到三个Tab按钮且都能正常加载数据。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Templates.tsx
git commit -m "feat: 教训库/巡检报告Tab前端收紧为仅管理员可见"
```

---

## Task 3: 后端巡检开关接入配置中心 REGISTRY

**Files:**
- Modify: `src/config/runtime_config.py`
- Test: `scripts/test_library_dedup_runtime_config.py`（新建）

**Interfaces:**
- Consumes: `src.config.settings.settings` 上已存在的 4 个字段：`library_dedup_scan_enabled: bool`（默认 `False`）、`library_dedup_scan_interval_hours: int`（默认 `24`）、`library_stale_draft_days: int`（默认 `30`）、`library_dedup_scan_max_merges_per_run: int`（默认 `5`），均定义于 `src/config/settings.py:144-147`，本任务不改这个文件。
- Produces: `runtime_config.REGISTRY` 新增4个key、`runtime_config.GROUPS` 新增1个分组 `library_dedup`；Task 4（前端）依赖这个分组key存在，才能在 `GROUP_CATEGORIES` 里引用它。

- [ ] **Step 1: 写失败测试**

创建 `scripts/test_library_dedup_runtime_config.py`：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""巡检开关接入配置中心 REGISTRY 单测：分组可见性 + 热更新 + 坏值拒绝。

运行：python scripts/test_library_dedup_runtime_config.py
"""
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore
from src.config import runtime_config as rc
from src.config.settings import settings


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(tmp.name)


def test_describe_includes_library_dedup_group():
    store = _tmp_store()
    groups = rc.describe(store)
    group = next((g for g in groups if g["key"] == "library_dedup"), None)
    assert group is not None, "library_dedup 分组未出现在 describe() 结果里"
    keys = {it["key"] for it in group["items"]}
    assert keys == {
        "library_dedup_scan_enabled",
        "library_dedup_scan_interval_hours",
        "library_stale_draft_days",
        "library_dedup_scan_max_merges_per_run",
    }, keys


def test_describe_scan_enabled_is_select_type():
    store = _tmp_store()
    groups = rc.describe(store)
    group = next(g for g in groups if g["key"] == "library_dedup")
    item = next(it for it in group["items"] if it["key"] == "library_dedup_scan_enabled")
    assert item["type"] == "select"
    assert item["choices"] == ["True", "False"]


def test_set_global_hot_updates_scan_enabled():
    store = _tmp_store()
    original = settings.library_dedup_scan_enabled
    try:
        rc.set_global(store, "library_dedup_scan_enabled", "False", updated_by="u_admin")
        assert settings.library_dedup_scan_enabled is False
        rc.set_global(store, "library_dedup_scan_enabled", "True", updated_by="u_admin")
        assert settings.library_dedup_scan_enabled is True
    finally:
        settings.library_dedup_scan_enabled = original


def test_set_global_hot_updates_interval_hours():
    store = _tmp_store()
    original = settings.library_dedup_scan_interval_hours
    try:
        rc.set_global(store, "library_dedup_scan_interval_hours", "6", updated_by="u_admin")
        assert settings.library_dedup_scan_interval_hours == 6
    finally:
        settings.library_dedup_scan_interval_hours = original


def test_set_global_rejects_bad_bool():
    store = _tmp_store()
    try:
        rc.set_global(store, "library_dedup_scan_enabled", "not-a-bool", updated_by="u_admin")
        assert False, "应该抛 ValueError"
    except ValueError:
        pass


def main():
    tests = [
        test_describe_includes_library_dedup_group,
        test_describe_scan_enabled_is_select_type,
        test_set_global_hot_updates_scan_enabled,
        test_set_global_hot_updates_interval_hours,
        test_set_global_rejects_bad_bool,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
    print("=" * 50)
    print(f"{len(tests) - failed}/{len(tests)} 通过")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python scripts/test_library_dedup_runtime_config.py`
Expected: 全部 FAIL（`library_dedup` 分组此时还不存在，`describe()` 结果里找不到，`set_global` 会因为 `key not in REGISTRY` 抛 `KeyError` 而不是预期行为）

- [ ] **Step 3: 在 GROUPS 里新增分组**

将 `src/config/runtime_config.py` 第 25-40 行的 `GROUPS` 列表，在 `{"key": "checkpoint", "label": "断点续跑"},`（第 39 行）之后、闭合的 `]`（第 40 行）之前插入一行：

```python
    {"key": "checkpoint", "label": "断点续跑"},
    {"key": "library_dedup", "label": "知识库巡检"},
]
```

- [ ] **Step 4: 在 REGISTRY 里新增4个key**

将 `src/config/runtime_config.py` 第 154-158 行的 `checkpoint_enabled` 条目之后、闭合的 `}`（原第 167 行，即 `REGISTRY` 字典结尾）之前插入：

```python
    "checkpoint_enabled": {
        "label": "启用断点续跑", "group": "checkpoint", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    # 模板库/教训库定时巡检
    "library_dedup_scan_enabled": {
        "label": "启用知识库巡检", "group": "library_dedup", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    "library_dedup_scan_interval_hours": {
        "label": "巡检间隔（小时）", "group": "library_dedup", "secret": False, "user": False,
    },
    "library_stale_draft_days": {
        "label": "停滞草稿清理阈值（天）", "group": "library_dedup", "secret": False, "user": False,
    },
    "library_dedup_scan_max_merges_per_run": {
        "label": "每轮最大合并对数", "group": "library_dedup", "secret": False, "user": False,
    },
}
```

（保留原 `checkpoint_enabled` 条目不变，只在它之后追加新内容；最后一行 `}` 是原本 `REGISTRY` 字典的收尾，位置不变。）

- [ ] **Step 5: 运行测试确认通过**

Run: `python scripts/test_library_dedup_runtime_config.py`
Expected: `5/5 通过`

- [ ] **Step 6: 回归既有配置中心测试**

Run: `python scripts/test_cookie_health_verify.py`
Expected: `7/7 通过`（确认新增分组没有破坏 `describe()` 对其它分组的既有行为）

- [ ] **Step 7: Commit**

```bash
git add src/config/runtime_config.py scripts/test_library_dedup_runtime_config.py
git commit -m "feat: 巡检开关4项接入配置中心 REGISTRY，支持前端热切换"
```

---

## Task 4: 前端配置中心归类 + 巡检报告空状态文案更新

**Files:**
- Modify: `frontend/src/components/ConfigCenter.tsx`
- Modify: `frontend/src/pages/Templates.tsx`

**Interfaces:**
- Consumes: Task 3 产出的 `runtime_config.GROUPS` 里的 `library_dedup` 分组 key（前端通过 `GET /api/config` 拿到，本任务只需要知道这个 key 字符串是 `"library_dedup"`）。
- Produces: 无新符号。

- [ ] **Step 1: ConfigCenter.tsx 新增分类**

将 `frontend/src/components/ConfigCenter.tsx` 第 87-92 行：

```typescript
const GROUP_CATEGORIES: { label: string; groups: string[] }[] = [
  { label: "模型", groups: ["llm_default", "llm_deepseek", "llm_qwen", "llm_local"] },
  { label: "采集与反爬", groups: ["search", "cookies", "cookie_health", "proxy", "mc_cdp"] },
  { label: "通知集成", groups: ["email", "slack"] },
  { label: "高级 / 基础设施", groups: ["semantic", "mysql", "checkpoint"] },
];
```

改为：

```typescript
const GROUP_CATEGORIES: { label: string; groups: string[] }[] = [
  { label: "模型", groups: ["llm_default", "llm_deepseek", "llm_qwen", "llm_local"] },
  { label: "采集与反爬", groups: ["search", "cookies", "cookie_health", "proxy", "mc_cdp"] },
  { label: "通知集成", groups: ["email", "slack"] },
  { label: "高级 / 基础设施", groups: ["semantic", "mysql", "checkpoint"] },
  { label: "知识库巡检", groups: ["library_dedup"] },
];
```

- [ ] **Step 2: Templates.tsx 更新巡检报告Tab空状态文案**

Task 2 已经改过这个文件（Tab按钮区新增了 `isAdmin &&` 包裹），下面这段的行号会比 Task 2 之前的原始行号往后移几行——不要按行号定位，按下面这段唯一文本内容搜索定位（搜索关键字 `LIBRARY_DEDUP_SCAN_ENABLED` 即可精确找到）。原文：

```tsx
              <Library className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                暂无巡检记录（巡检开关默认关闭，需在 .env 开启
                <code className="mx-1 rounded bg-muted px-1">LIBRARY_DEDUP_SCAN_ENABLED</code>
                后台生效）。
              </p>
```

改为：

```tsx
              <Library className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                暂无巡检记录（巡检开关默认关闭，可在「设置 → 配置中心 → 知识库巡检」开启）。
              </p>
```

- [ ] **Step 3: 编译验证**

Run: `cd frontend && npm run build`
Expected: 编译通过，无 TypeScript 报错

- [ ] **Step 4: 手工验证**

启动前端+后端（需先完成 Task 1-3），管理员账号登录设置页，确认「配置中心」里出现「知识库巡检」大类，展开后能看到4项配置；把「启用知识库巡检」切到 `True` 并保存，确认保存成功提示、刷新页面后该项显示为"已覆盖"；检查 `webui.db` 的 `runtime_config` 表出现对应行。教训库Tab仍为空巡检报告页面时，确认空状态文案已更新为新文案。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ConfigCenter.tsx frontend/src/pages/Templates.tsx
git commit -m "feat: 配置中心新增知识库巡检分类，巡检报告空状态文案改引导前端开关"
```

---

## Task 5: 文档同步

**Files:**
- Modify: `README_AGENT.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: Task 1-4 全部产出（本任务只写文档，不改代码）。
- Produces: 无（终结任务）。

- [ ] **Step 1: README_AGENT.md 新增章节**

在 `README_AGENT.md` 现有的 `## 15.8 模板库/教训库定时巡检（2026-07-10 新增）` 章节之后，插入新章节 `## 15.9 模板库权限收紧 + 巡检开关前端化（2026-07-10 新增）`，内容需包含：
- 背景：教训库/巡检报告此前对所有登录用户可见（数据一样、仅删除按钮区分权限），审查后判定这两块偏运维/内部向内容，收紧为仅管理员可见+可访问；定时巡检4个配置项此前只能改 `.env`，现接入配置中心可前端热切换。
- 权限收紧的实现要点：`lessons_routes.py` 的 `GET /api/lessons`、`library_dedup_routes.py` 的 `GET /api/library-dedup-log` 依赖从 `get_current_user` 改为 `require_admin`；前端 `Templates.tsx` 对应两个Tab按钮与数据请求都加 `isAdmin` 门控，前后端同步收紧（防御深度：即使绕过前端直接调API也会被拒绝）。
- 巡检开关前端化的实现要点：`runtime_config.py` 的 `GROUPS`/`REGISTRY` 新增 `library_dedup` 分组（4项，均 `user: False` 全局配置），照搬 `cookie_health_scan_enabled` 的现成模式；`ConfigCenter.tsx` 的 `GROUP_CATEGORIES` 新增「知识库巡检」大类；保存后走既有 `PUT /api/config/{key}` 链路即时热生效，`library_dedup_scanner.py` 的 `_loop()` 本来就是每次wake热读，不需要额外改动即可拿到新值。
- 回归：新增 `scripts/test_library_route_permissions.py`（3项）、`scripts/test_library_dedup_runtime_config.py`（5项），`scripts/test_cookie_health_verify.py`（7项）回归通过，`npm run build` 通过。

具体文字表述可参考 `## 15.8` 章节的写作风格（背景→实现要点→回归结果的结构）。

- [ ] **Step 2: AGENTS.md 更新相关条目**

在 `AGENTS.md` 中找到当前教训库前端管理页面那一条描述（`- 教训库前端管理页面（GET/DELETE /api/lessons + Templates.tsx 加"教训库"Tab，与模板库页面权限模型一致）；`），把其中"与模板库页面权限模型一致"这个已过时的表述改掉（现状是教训库/巡检报告已收紧为仅管理员，权限模型不再与模板库一致），改为准确描述当前状态，例如：

```
- 教训库前端管理页面（GET/DELETE /api/lessons，仅管理员可访问；Templates.tsx 加"教训库"Tab，非管理员不渲染该Tab）；
```

同时在"模板库/教训库定时巡检"那一条描述后面，补充一句巡检开关已接入配置中心可前端热切换的说明。

- [ ] **Step 3: 校验没有遗留过时引用**

Run（PowerShell）: `Select-String -Path README_AGENT.md,AGENTS.md -Pattern "所有登录用户可读|与模板库页面权限模型一致|需在 \.env 开启"`
Expected: 无匹配，或匹配到的都是本次任务已经改掉的旧文案之外的、与本次改动无关的其它上下文（人工确认一下，避免误改了不相关的历史记录段落）。

- [ ] **Step 4: Commit**

```bash
git add README_AGENT.md AGENTS.md
git commit -m "docs: 模板库权限收紧+巡检开关前端化文档同步"
```

---

## 验证

1. `python scripts/test_library_route_permissions.py` → 3/3 通过
2. `python scripts/test_library_dedup_runtime_config.py` → 5/5 通过
3. `python scripts/test_cookie_health_verify.py` → 7/7 通过（回归无破坏）
4. `cd frontend && npm run build` → 编译通过
5. 重启后端，手工验证：普通用户登录模板库页面只见「模板库」一个Tab；curl 带普通用户 token 直接请求 `/api/lessons`、`/api/library-dedup-log` 均返回403；管理员登录三个Tab齐全；配置中心出现「知识库巡检」大类，切换「启用知识库巡检」开关后 `webui.db.runtime_config` 表落库、`settings.library_dedup_scan_enabled` 立即变化
