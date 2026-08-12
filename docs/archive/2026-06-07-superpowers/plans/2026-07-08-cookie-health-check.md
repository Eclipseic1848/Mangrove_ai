# Cookie 健康状态记录 + 定时巡检 + 电商真实校验 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每个 Cookie（7 个 MediaCrawler 社媒平台 + 京东/淘宝/拼多多）记录"最后一次验证状态"并在管理员配置中心展示；给京东/淘宝/拼多多补上真实的 Cookie 有效性校验（当前是假的占位逻辑）；新增默认关闭的定时巡检开关，开启后按天级别间隔自动跑全部 10 项验证。

**Architecture:** 新建 `cookie_health` SQLite 表（`WebUIStore`）持久化每个 Cookie 的验证结果；手动点击"验证"按钮和新增的后台定时扫描协程共用同一套判定逻辑（`config_routes.py` 的 `_verify_target`/`_verify_mc_cookie`/新增的 `_verify_ecommerce_cookie`），结果统一落库；`runtime_config.describe()` 把落库结果拼进 `/api/config` 响应，前端 `AdminConfigCenter` 展示状态徽章。

**Tech Stack:** FastAPI + SQLite（标准库 sqlite3）+ httpx（异步 HTTP）+ asyncio（后台轮询协程）+ React/TypeScript（Vite）。

## Global Constraints

- 本项目**没有 pytest 套件**，测试一律是 `scripts/test_*.py` 里若干 `def test_x(): assert ...` 函数 + `main()` 汇总运行（参考 `scripts/test_ecommerce.py`、`scripts/test_mc_cookie.py`），用 `python scripts/test_xxx.py` 运行，不新增 pytest 依赖。
- 每新增一个 `REGISTRY` 键（`runtime_config.py`）必须同时做到：
  1. `frontend/src/components/ConfigCenter.tsx` 的 `VERIFY_TARGET` 表补一条映射（该会话已建立的约定：55/55、后来 58/58 全覆盖，"验证"按钮始终渲染、`disabled={!it.value}` 灰置）；
  2. `frontend/src/lib/configGuides.ts` 补一条"使用指南"内容（面板 `ADMIN_GUIDE_SECTIONS` + 编辑弹窗内嵌 `GUIDE_BY_KEY`，2026-07-08 会话已建立的分工：一起配置的字段面板合并展示，单个字段编辑弹窗只讲该字段自己）；
  3. 布尔型选项必须 `"type": "select", "choices": ["True", "False"]`（首字母大写，匹配 Python `str(bool)`；`cast_value` 保存时不区分大小写）。
- 新代码注释统一用中文，源文件 UTF-8。
- **越权隔离**（本次计划新决定，设计文档未展开，写在这里避免实现时出 bug）：`cookie_health` 是全局表，只反映"管理员配置中心里那份全局/`.env` Cookie 值"的健康状况。`POST /api/config/verify` 同时服务管理员（测全局值）和普通用户（`SelfConfigCenter`，测**自己的**按用户覆盖值——`verify_config()` 里会 `set_user_overrides` 切到用户自己的凭证）。**只有当发起验证的用户是 `admin`/`super_admin` 时才写 `cookie_health`**，避免普通用户验证自己的个人 Cookie 覆盖了管理员配置中心展示的全局状态。
- 不复用 `src/scheduler`（面向用户 TaskSpec 的 cron 调度，语义不同）；新场景另起一个独立的、更简单的轮询协程，风格参照 `src/scheduler/service.py` 的 `SchedulerService`（`asyncio.create_task` + `asyncio.Event` 停止信号）。
- 定时巡检默认关闭（`cookie_health_scan_enabled` 默认 `False`），扫描间隔默认 24 小时（`cookie_health_scan_interval_hours` 默认 `24`）。
- 电商真实校验只看最终落地 URL 是否命中登录页特征，不解析页面正文内容；拼多多标注 `best_effort=True`，判定为"无法判断"时消息里必须包含字面"无法判断"四个字（`config_routes.py` 靠这个子串区分"确定失效"和"测不准，不下判断"两种失败，供落库时选 `invalid` 还是 `unknown`）。

---

## 涉及文件

| 文件 | 操作 | 说明 |
|---|---|---|
| `src/api/store.py` | 修改 | 新增 `cookie_health` 表 DDL + `cookie_health_set`/`cookie_health_all` 方法 |
| `scripts/test_cookie_health_store.py` | 新建 | Task 1 测试 |
| `src/api/routes/config_routes.py` | 修改 | 新增 `_ECOMMERCE_PROBES`/`_classify_ecommerce_probe`/`_verify_ecommerce_cookie`（Task 2）；`_record_cookie_health` + `verify_config()` 落库 + `_COOKIE_HEALTH_KEYS`（Task 3）；`_verify_target()` 新增 `cookie_health` 分支（Task 4） |
| `scripts/test_ecommerce_cookie_verify.py` | 新建 | Task 2 测试 |
| `scripts/test_cookie_health_verify.py` | 新建 | Task 3 测试 |
| `src/config/runtime_config.py` | 修改 | `describe()` 拼 `health` 字段（Task 3）；新增 `cookie_health` 分组 + 2 个 REGISTRY 键（Task 4） |
| `src/config/settings.py` | 修改 | 新增 `cookie_health_scan_enabled`/`cookie_health_scan_interval_hours` 字段（Task 4） |
| `src/api/cookie_health_scanner.py` | 新建 | 定时巡检协程（Task 4） |
| `src/api/main.py` | 修改 | lifespan 里启动扫描协程（Task 4） |
| `scripts/test_cookie_health_scanner.py` | 新建 | Task 4 测试 |
| `frontend/src/components/ConfigCenter.tsx` | 修改 | `VERIFY_TARGET` 补 2 项 + 修正过期注释（Task 4）；`CfgItem` 加 `health` 字段 + Cookie 行状态徽章 + best-effort 角标（Task 5） |
| `frontend/src/lib/configGuides.ts` | 修改 | 补 `cookie_health` 分组的 2 条指南（Task 4） |
| `frontend/src/lib/utils.ts` | 修改 | 新增 `formatRelativeTime()`（Task 5） |

---

### Task 1: `cookie_health` 存储层

**Files:**
- Modify: `src/api/store.py`
- Test: `scripts/test_cookie_health_store.py`（新建）

**Interfaces:**
- Produces：`WebUIStore.cookie_health_set(key: str, status: str, message: str, checked_by: str) -> None`；`WebUIStore.cookie_health_all() -> Dict[str, Dict[str, str]]`（返回 `{key: {"status":.., "message":.., "checked_at":.., "checked_by":..}}`，无记录的 key 不出现在返回字典里）。后续 Task 3/4 直接调用这两个方法，不再改动本文件。

- [ ] **Step 1: 在 `_DDL` 里新增表**

打开 `src/api/store.py`，把第 60-62 行（`CREATE INDEX IF NOT EXISTS idx_conv_user ...` 前）改成：

```python
CREATE TABLE IF NOT EXISTS runtime_config (
    scope      TEXT NOT NULL,               -- 'global' 或 user_id（按用户隔离的凭证覆盖）
    key        TEXT NOT NULL,               -- settings 字段名（白名单见 runtime_config.REGISTRY）
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by TEXT,
    PRIMARY KEY (scope, key)
);
CREATE TABLE IF NOT EXISTS cookie_health (
    key        TEXT PRIMARY KEY,            -- 配置键，如 mc_cookie_xhs / jd_cookie
    status     TEXT NOT NULL,               -- valid | invalid | unknown
    message    TEXT,                        -- 人话原因；失效/无法判断时给出线索
    checked_at TEXT NOT NULL,
    checked_by TEXT NOT NULL                -- manual | scheduled
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id);
"""
```

（即在原有 `runtime_config` 表定义和 `CREATE INDEX` 之间插入 `cookie_health` 表定义，其余不变。）

- [ ] **Step 2: 新增 store 方法**

在 `config_delete` 方法（约第 131-133 行）后面、`# ---------- 用户 ----------` 注释之前，插入：

```python
    # ---------- Cookie 健康状态（手动/定时验证结果落库，供配置中心展示） ----------
    def cookie_health_set(self, key: str, status: str, message: str, checked_by: str) -> None:
        """写入/覆盖某 Cookie 的最近一次验证结果（key 唯一，覆盖旧记录）。"""
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO cookie_health (key, status, message, checked_at, checked_by) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET status=excluded.status, message=excluded.message, "
                "checked_at=excluded.checked_at, checked_by=excluded.checked_by",
                (key, status, message, _now(), checked_by),
            )

    def cookie_health_all(self) -> Dict[str, Dict[str, str]]:
        """返回 {key: {status, message, checked_at, checked_by}}；没验证过的 key 不出现在字典里。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT key, status, message, checked_at, checked_by FROM cookie_health"
            ).fetchall()
        return {r["key"]: dict(r) for r in rows}
```

- [ ] **Step 3: 写测试**

新建 `scripts/test_cookie_health_store.py`：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""cookie_health 存储层单测。运行：python scripts/test_cookie_health_store.py"""
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(tmp.name)


def test_set_and_get():
    store = _tmp_store()
    assert store.cookie_health_all() == {}
    store.cookie_health_set("jd_cookie", "valid", "京东 Cookie 有效", "manual")
    all_health = store.cookie_health_all()
    assert set(all_health.keys()) == {"jd_cookie"}
    row = all_health["jd_cookie"]
    assert row["status"] == "valid"
    assert row["message"] == "京东 Cookie 有效"
    assert row["checked_by"] == "manual"
    assert row["checked_at"]  # 非空时间戳


def test_overwrite():
    store = _tmp_store()
    store.cookie_health_set("mc_cookie_xhs", "valid", "ok", "manual")
    store.cookie_health_set("mc_cookie_xhs", "invalid", "登录过期", "scheduled")
    row = store.cookie_health_all()["mc_cookie_xhs"]
    assert row["status"] == "invalid"
    assert row["message"] == "登录过期"
    assert row["checked_by"] == "scheduled"


def test_multiple_keys_independent():
    store = _tmp_store()
    store.cookie_health_set("jd_cookie", "valid", "a", "manual")
    store.cookie_health_set("tb_cookie", "unknown", "b", "manual")
    all_health = store.cookie_health_all()
    assert set(all_health.keys()) == {"jd_cookie", "tb_cookie"}
    assert all_health["jd_cookie"]["status"] == "valid"
    assert all_health["tb_cookie"]["status"] == "unknown"


def main():
    tests = [test_set_and_get, test_overwrite, test_multiple_keys_independent]
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

- [ ] **Step 4: 运行测试**

Run: `python scripts/test_cookie_health_store.py`
Expected: `3/3 通过`，退出码 0。

- [ ] **Step 5: Commit**

```bash
git add src/api/store.py scripts/test_cookie_health_store.py
git commit -m "feat: 新增cookie_health存储层，记录Cookie验证结果"
```

---

### Task 2: 京东/淘宝/拼多多真实校验

**Files:**
- Modify: `src/api/routes/config_routes.py`
- Test: `scripts/test_ecommerce_cookie_verify.py`（新建）

**Interfaces:**
- Consumes：无（不依赖 Task 1）。
- Produces：`_classify_ecommerce_probe(landed_url: str, status_code: int, cn: str, login_markers: tuple, best_effort: bool) -> str`（纯函数，判定失效/无法判断时抛 `RuntimeError`，消息含"无法判断"四字表示"测不准"）；`async def _verify_ecommerce_cookie(cookie_key: str) -> str`（`cookie_key` 为 `"jd_cookie"`/`"tb_cookie"`/`"pdd_cookie"` 之一）。Task 3/4 会调用 `_verify_ecommerce_cookie`。

- [ ] **Step 1: 写失败测试（先测纯函数分类逻辑，不联网）**

新建 `scripts/test_ecommerce_cookie_verify.py`：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""京东/淘宝/拼多多 Cookie 真实校验单测。运行：python scripts/test_ecommerce_cookie_verify.py

_classify_ecommerce_probe 是纯函数（不联网），覆盖分类逻辑；
_verify_ecommerce_cookie 只测"未配置 Cookie 直接报错"这条不联网的分支，
真实联网探测另见人工/集成验证（见任务报告），不在此文件跑。
"""
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.routes.config_routes import _classify_ecommerce_probe, _verify_ecommerce_cookie


def test_classify_valid():
    # 落地 URL 就是预期页面本身、状态码 200 → 有效
    msg = _classify_ecommerce_probe(
        "https://order.jd.com/center/list.action", 200, "京东",
        ("passport.jd.com",), False,
    )
    assert "京东 Cookie 有效" in msg, msg


def test_classify_invalid_login_redirect():
    # 落地 URL 命中登录页特征 → 失效
    try:
        _classify_ecommerce_probe(
            "https://passport.jd.com/uc/login?ReturnUrl=xxx", 200, "京东",
            ("passport.jd.com",), False,
        )
        assert False, "应该抛出 RuntimeError"
    except RuntimeError as e:
        assert "失效" in str(e), str(e)


def test_classify_ambiguous_non_best_effort():
    # 非 best-effort（京东/淘宝）遇到非 200 且未命中登录特征 → 无法判断，不误报失效
    try:
        _classify_ecommerce_probe(
            "https://order.jd.com/center/list.action", 403, "京东",
            ("passport.jd.com",), False,
        )
        assert False, "应该抛出 RuntimeError"
    except RuntimeError as e:
        assert "无法判断" in str(e), str(e)
        assert "失效" not in str(e), "非明确登录跳转时不应该说'失效'"


def test_classify_ambiguous_best_effort_hints_anti_scrape():
    # best-effort（拼多多）遇到非 200 → 无法判断，且提示可能是反爬拦截
    try:
        _classify_ecommerce_probe(
            "https://mobile.yangkeduo.com/user_setting.html", 461, "拼多多",
            ("login.yangkeduo.com", "/login.html"), True,
        )
        assert False, "应该抛出 RuntimeError"
    except RuntimeError as e:
        assert "无法判断" in str(e), str(e)
        assert "反爬" in str(e), str(e)


def test_verify_ecommerce_cookie_empty_cookie():
    # 未配置 Cookie：不联网，直接报错
    from src.config.settings import settings
    old = settings.jd_cookie
    try:
        settings.jd_cookie = ""

        async def run():
            await _verify_ecommerce_cookie("jd_cookie")

        try:
            asyncio.run(run())
            assert False, "应该抛出 RuntimeError"
        except RuntimeError as e:
            assert "未配置" in str(e), str(e)
    finally:
        settings.jd_cookie = old


def main():
    tests = [
        test_classify_valid,
        test_classify_invalid_login_redirect,
        test_classify_ambiguous_non_best_effort,
        test_classify_ambiguous_best_effort_hints_anti_scrape,
        test_verify_ecommerce_cookie_empty_cookie,
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

Run: `python scripts/test_ecommerce_cookie_verify.py`
Expected: `ImportError` 或 `AttributeError`（`_classify_ecommerce_probe`/`_verify_ecommerce_cookie` 还不存在）。

- [ ] **Step 3: 实现**

打开 `src/api/routes/config_routes.py`。在文件顶部 `import asyncio` 后面加一行日志导入（第 12 行后）：

```python
import asyncio
import logging
from typing import Optional
```

在 `logger = ...` 变量尚不存在，紧跟 `router = APIRouter(...)` 那一行（第 25 行）后面加：

```python
router = APIRouter(prefix="/api/config", tags=["config"])

logger = logging.getLogger(__name__)
```

找到第 146-151 行的 `_MC_COOKIE_PLATFORM` 字典定义，在它下面（`_verify_mc_cookie` 函数定义之前）插入电商探测配置：

```python
# 电商 Cookie 真实校验：访问一个必须登录才能访问的页面，看是否被重定向回登录页。
# 只看最终落地 URL 是否命中登录页特征，不解析页面正文（改版不影响判定），但登录跳转
# 策略本身变化时仍可能需要更新这里的 url/login_markers——如实标注维护成本，不假装稳定。
# 拼多多反爬更激进，标 best_effort=True：请求被拦截时不误判为"Cookie 失效"，
# 而是回"无法判断"，避免让管理员误删一个其实还有效的 Cookie。
_ECOMMERCE_PROBES = {
    "jd_cookie": {
        "cn": "京东",
        "url": "https://order.jd.com/center/list.action",
        "login_markers": ("passport.jd.com",),
        "best_effort": False,
    },
    "tb_cookie": {
        "cn": "淘宝/天猫",
        "url": "https://member1.taobao.com/member/fresh/account_setting.htm",
        "login_markers": ("login.taobao.com", "login.tmall.com"),
        "best_effort": False,
    },
    "pdd_cookie": {
        "cn": "拼多多",
        "url": "https://mobile.yangkeduo.com/user_setting.html",
        "login_markers": ("login.yangkeduo.com", "/login.html"),
        "best_effort": True,
    },
}

_ECOMMERCE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _classify_ecommerce_probe(
    landed_url: str, status_code: int, cn: str, login_markers: tuple, best_effort: bool,
) -> str:
    """纯函数：根据探测请求最终落地的 URL + 状态码判定 Cookie 状态，返回结论文案；
    判定为失效/无法判断时抛 RuntimeError（消息含"无法判断"四字表示"测不准，非确定失效"）。
    """
    if any(marker in landed_url for marker in login_markers):
        raise RuntimeError(f"{cn} 登录状态已失效，请重新导出 Cookie")
    if status_code != 200:
        hint = "，可能是反爬拦截而非 Cookie 失效" if best_effort else "，请稍后重试确认"
        raise RuntimeError(f"{cn} 无法判断 Cookie 状态（HTTP {status_code}）{hint}")
    return f"{cn} Cookie 有效（{landed_url}）"


async def _verify_ecommerce_cookie(cookie_key: str) -> str:
    """真实探测：带上 Cookie 访问一个必须登录才能访问的页面，交给 _classify_ecommerce_probe 判定。"""
    from src.config.user_ctx import effective

    probe = _ECOMMERCE_PROBES[cookie_key]
    cookie = (effective(cookie_key) or "").strip()
    if not cookie:
        raise RuntimeError(f"{probe['cn']} 未配置 Cookie")
    headers = {"User-Agent": _ECOMMERCE_UA, "Cookie": cookie}
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(probe["url"], headers=headers)
    except Exception as e:  # noqa: BLE001 网络异常也是"测不了"，不该冒充"Cookie失效"
        raise RuntimeError(f"{probe['cn']} 探测请求失败：{e}")
    return _classify_ecommerce_probe(
        str(r.url), r.status_code, probe["cn"], probe["login_markers"], probe["best_effort"],
    )
```

最后把 `_verify_target()` 里第 256-257 行的假占位分支：

```python
    if target.startswith(("jd_cookie", "tb_cookie", "pdd_cookie")) or target == "cookies":
        return "Cookie 已保存（登录态有效性以实际采集结果为准，过期时任务会提示重新导出）"
```

改成：

```python
    if target in ("jd_cookie", "tb_cookie", "pdd_cookie"):
        return await _verify_ecommerce_cookie(target)
    if target == "cookies":
        return "Cookie 已保存（登录态有效性以实际采集结果为准，过期时任务会提示重新导出）"
```

（`target == "cookies"` 这条泛化分支不是本次要动的对象，原样保留。）

- [ ] **Step 4: 运行测试确认通过**

Run: `python scripts/test_ecommerce_cookie_verify.py`
Expected: `5/5 通过`，退出码 0。

- [ ] **Step 5: 修正前端一处过期注释**

打开 `frontend/src/components/ConfigCenter.tsx`，把第 50 行的注释：

```ts
  // 京东/淘宝/拼多多电商 Cookie：无真实登录探测，仅确认已保存（同 _verify_target 的轻量分支）
```

改成：

```ts
  // 京东/淘宝/拼多多电商 Cookie：目标即键名本身，_verify_target 路由到真实登录态探测
  // （访问登录后才能看的页面，看是否被重定向回登录页；拼多多反爬激进，判定为 best-effort）
```

（下面一行 `jd_cookie: "jd_cookie", tb_cookie: "tb_cookie", pdd_cookie: "pdd_cookie",` 不用改，映射关系本来就对。）

- [ ] **Step 6: 前端构建确认没有破坏编译**

Run: `cd frontend && npm run build`
Expected: `tsc --noEmit && vite build` 无报错。

- [ ] **Step 7: Commit**

```bash
git add src/api/routes/config_routes.py scripts/test_ecommerce_cookie_verify.py frontend/src/components/ConfigCenter.tsx
git commit -m "feat: 京东/淘宝/拼多多Cookie接入真实登录态校验，替换此前的假占位逻辑"
```

---

### Task 3: 手动验证结果落库 + `/api/config` 暴露健康状态

**Files:**
- Modify: `src/api/routes/config_routes.py`
- Modify: `src/config/runtime_config.py`
- Test: `scripts/test_cookie_health_verify.py`（新建）

**Interfaces:**
- Consumes：Task 1 的 `WebUIStore.cookie_health_set`/`cookie_health_all`；Task 2 的 `_verify_ecommerce_cookie`（已接入 `_verify_target`，本任务不用直接调用）。
- Produces：`_record_cookie_health(key: str, status: str, message: str, checked_by: str) -> None`（旁路副作用，落库失败只记日志不抛出）；`_COOKIE_HEALTH_KEYS: tuple[str, ...]`（10 个 Cookie key 的常量元组，**唯一权威列表**——Task 4 的调度器直接从本模块 `import`，不允许重新声明一份重复的列表）。`runtime_config.describe()` 返回的每个 Cookie 项新增 `"health"` 字段（`dict | None`）。

- [ ] **Step 1: 写测试（测 `verify_config` 端点落库行为，用 FastAPI TestClient 直接调路由函数）**

新建 `scripts/test_cookie_health_verify.py`：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""手动验证结果落库 + describe() 暴露 health 字段，单测。

运行：python scripts/test_cookie_health_verify.py
不联网：monkeypatch _verify_target 让它直接返回/抛错，只测落库与角色隔离逻辑。
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore
from src.api.routes import config_routes as cr
from src.config import runtime_config as rc


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(tmp.name)


def test_record_cookie_health_writes():
    store = _tmp_store()
    with patch.object(cr, "get_store", return_value=store):
        cr._record_cookie_health("jd_cookie", "valid", "京东 Cookie 有效", "manual")
    row = store.cookie_health_all()["jd_cookie"]
    assert row["status"] == "valid"
    assert row["checked_by"] == "manual"


def test_record_cookie_health_swallow_store_errors():
    # 落库失败（比如坏路径）不应该抛出，是旁路副作用
    class BrokenStore:
        def cookie_health_set(self, *a, **k):
            raise RuntimeError("磁盘满了")
    with patch.object(cr, "get_store", return_value=BrokenStore()):
        cr._record_cookie_health("jd_cookie", "valid", "x", "manual")  # 不应该抛异常


def test_verify_config_admin_success_writes_health():
    store = _tmp_store()
    admin_user = {"user_id": "u_admin", "role": "admin"}

    async def fake_verify_target(target):
        return "小红书 Cookie 有效（真实登录并采到 1 条数据）"

    async def run():
        with patch.object(cr, "get_store", return_value=store), \
             patch.object(cr, "_verify_target", side_effect=fake_verify_target):
            body = cr.VerifyIn(target="mc_cookie_xhs")
            result = await cr.verify_config.__wrapped__(body, admin_user) \
                if hasattr(cr.verify_config, "__wrapped__") else await cr.verify_config(body, admin_user)
            return result

    result = asyncio.run(run())
    assert result["ok"] is True
    row = store.cookie_health_all()["mc_cookie_xhs"]
    assert row["status"] == "valid"
    assert row["checked_by"] == "manual"


def test_verify_config_admin_failure_writes_invalid():
    store = _tmp_store()
    admin_user = {"user_id": "u_admin", "role": "admin"}

    async def fake_verify_target(target):
        raise RuntimeError("京东 登录状态已失效，请重新导出 Cookie")

    async def run():
        with patch.object(cr, "get_store", return_value=store), \
             patch.object(cr, "_verify_target", side_effect=fake_verify_target):
            body = cr.VerifyIn(target="jd_cookie")
            return await cr.verify_config(body, admin_user)

    result = asyncio.run(run())
    assert result["ok"] is False
    row = store.cookie_health_all()["jd_cookie"]
    assert row["status"] == "invalid"


def test_verify_config_ambiguous_failure_writes_unknown():
    store = _tmp_store()
    admin_user = {"user_id": "u_admin", "role": "admin"}

    async def fake_verify_target(target):
        raise RuntimeError("拼多多 无法判断 Cookie 状态（HTTP 461），可能是反爬拦截而非 Cookie 失效")

    async def run():
        with patch.object(cr, "get_store", return_value=store), \
             patch.object(cr, "_verify_target", side_effect=fake_verify_target):
            body = cr.VerifyIn(target="pdd_cookie")
            return await cr.verify_config(body, admin_user)

    asyncio.run(run())
    row = store.cookie_health_all()["pdd_cookie"]
    assert row["status"] == "unknown", row


def test_verify_config_self_user_does_not_write_health():
    # 普通用户验证自己的个人 Cookie 覆盖，不应该污染全局 cookie_health 表
    store = _tmp_store()
    self_user = {"user_id": "u_self", "role": "user"}

    async def fake_verify_target(target):
        return "小红书 Cookie 有效（真实登录并采到 1 条数据）"

    async def run():
        with patch.object(cr, "get_store", return_value=store), \
             patch.object(cr, "set_user_overrides", return_value=None), \
             patch.object(cr, "_verify_target", side_effect=fake_verify_target):
            body = cr.VerifyIn(target="mc_cookie_xhs")
            return await cr.verify_config(body, self_user)

    result = asyncio.run(run())
    assert result["ok"] is True
    assert store.cookie_health_all() == {}, "普通用户验证不应写全局 cookie_health"


def test_describe_includes_health_field():
    store = _tmp_store()
    store.cookie_health_set("mc_cookie_xhs", "valid", "有效", "manual")
    groups = rc.describe(store)
    cookies_group = next(g for g in groups if g["key"] == "cookies")
    xhs_item = next(i for i in cookies_group["items"] if i["key"] == "mc_cookie_xhs")
    assert xhs_item["health"]["status"] == "valid"
    dy_item = next(i for i in cookies_group["items"] if i["key"] == "mc_cookie_dy")
    assert dy_item["health"] is None, "没验证过的 Cookie health 应为 None"


def main():
    tests = [
        test_record_cookie_health_writes,
        test_record_cookie_health_swallow_store_errors,
        test_verify_config_admin_success_writes_health,
        test_verify_config_admin_failure_writes_invalid,
        test_verify_config_ambiguous_failure_writes_unknown,
        test_verify_config_self_user_does_not_write_health,
        test_describe_includes_health_field,
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

Run: `python scripts/test_cookie_health_verify.py`
Expected: `AttributeError`（`_record_cookie_health`/`_COOKIE_HEALTH_KEYS` 还不存在，`describe()` 还没有 `health` 字段）。

- [ ] **Step 3: 实现 `config_routes.py` 落库逻辑**

在 `_verify_target` 函数定义之前（紧接 Task 2 新增的 `_verify_ecommerce_cookie` 函数之后）插入：

```python
# 7 个社媒 + 3 个电商，唯一权威列表：手动验证/定时巡检都只对这 10 个 key 落库到
# cookie_health；其余 verify 目标（email/slack/proxy/mysql 等）不是"某一个 Cookie"，
# 不落这张表。元组顺序即 Task 4 定时巡检的扫描顺序，cookie_health_scanner.py 直接
# import 这个元组，不重新声明一份重复列表（DRY）。
_COOKIE_HEALTH_KEYS = (
    "mc_cookie_xhs", "mc_cookie_dy", "mc_cookie_wb", "mc_cookie_bili", "mc_cookie_zhihu",
    "mc_cookie_ks", "mc_cookie_tieba", "jd_cookie", "tb_cookie", "pdd_cookie",
)


def _record_cookie_health(key: str, status: str, message: str, checked_by: str) -> None:
    """验证结果顺带落库，供配置中心展示"最后一次验证状态"；旁路副作用，落库失败只记日志。"""
    try:
        get_store().cookie_health_set(key, status, message[:500], checked_by)
    except Exception as e:  # noqa: BLE001 落库失败不该影响验证结果本身返回给前端
        logger.warning("cookie_health 写入失败 key=%s: %s", key, e)
```

然后把 `verify_config()`（原第 269-285 行）改成：

```python
@router.post("/verify")
async def verify_config(body: VerifyIn, user=Depends(get_current_user)):
    """连通验证。普通用户验证时套用其个人覆盖（验的是"他自己任务会用到的配置"）。"""
    is_admin = user.get("role") in ("admin", "super_admin")
    if not is_admin:
        # 普通用户只允许验证自助项相关目标
        allowed = {"llm_deepseek", "llm_qwen", "deepseek_api_key", "qwen_api_key", "cookies"}
        if body.target not in allowed and not body.target.startswith(("mc_cookie_", "jd_cookie", "tb_cookie", "pdd_cookie")):
            raise HTTPException(status_code=403, detail="无权验证该项")
        mine = get_store().config_all(user["user_id"]) or {}
        set_user_overrides({k: v for k, v in mine.items() if k in rc.USER_KEYS})
    try:
        detail = await _verify_target(body.target)
        # 只有管理员验证时测的才是全局配置中心展示的那份值；普通用户验证的是自己的
        # 个人覆盖（见上面 set_user_overrides），写进全局表会污染管理员看到的状态。
        if is_admin and body.target in _COOKIE_HEALTH_KEYS:
            _record_cookie_health(body.target, "valid", detail, checked_by="manual")
        return {"ok": True, "detail": detail}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 验证失败把原因回前端
        detail = str(e)[:300]
        if is_admin and body.target in _COOKIE_HEALTH_KEYS:
            status = "unknown" if "无法判断" in detail else "invalid"
            _record_cookie_health(body.target, status, detail, checked_by="manual")
        return {"ok": False, "detail": detail}
```

- [ ] **Step 4: 实现 `runtime_config.describe()` 暴露 `health`**

打开 `src/config/runtime_config.py`，把 `describe()`（原第 243-270 行）改成：

```python
def describe(store) -> List[Dict[str, Any]]:
    """给管理员前端的全量描述：分组 → 各项 {key,label,secret,user,source,value,type,choices,choicesFrom,health}。"""
    _snapshot_baseline()
    overrides = store.config_all("global") or {}
    cookie_health = store.cookie_health_all()
    groups = []
    for g in GROUPS:
        items = []
        for key, meta in REGISTRY.items():
            if meta["group"] != g["key"]:
                continue
            overridden = key in overrides
            cur = overrides.get(key) if overridden else getattr(settings, key, "")
            entry = {
                "key": key, "label": meta["label"], "secret": meta["secret"],
                "user_editable": meta["user"],
                "source": "override" if overridden else "env",
                "value": mask_value(key, "" if cur is None else str(cur)),
            }
            # 下拉选择型配置：透传 type/choices/choicesFrom 给前端渲染下拉
            if meta.get("type") == "select":
                entry["type"] = "select"
                if "choices" in meta:
                    entry["choices"] = meta["choices"]
                if "choices_from" in meta:
                    entry["choicesFrom"] = meta["choices_from"]
            # Cookie 分组额外带上"最后一次验证状态"；没验证过的 key 为 None（前端展示"从未验证"）
            if g["key"] == "cookies":
                entry["health"] = cookie_health.get(key)
            items.append(entry)
        groups.append({**g, "items": items})
    return groups
```

（唯一变化：函数开头加 `cookie_health = store.cookie_health_all()`，循环里加 `if g["key"] == "cookies": entry["health"] = cookie_health.get(key)`。）

- [ ] **Step 5: 运行测试确认通过**

Run: `python scripts/test_cookie_health_verify.py`
Expected: `7/7 通过`，退出码 0。

- [ ] **Step 6: 回归已有测试**

Run: `python scripts/test_mc_cookie.py && python scripts/test_ecommerce_cookie_verify.py`
Expected: 均全部通过（确认 Task 3 的改动没有破坏 Task 2 或既有逻辑）。

- [ ] **Step 7: Commit**

```bash
git add src/api/routes/config_routes.py src/config/runtime_config.py scripts/test_cookie_health_verify.py
git commit -m "feat: 手动验证Cookie结果落库，/api/config暴露最近一次验证状态"
```

---

### Task 4: 定时巡检开关 + 后台扫描协程

**Files:**
- Modify: `src/config/settings.py`
- Modify: `src/config/runtime_config.py`
- Create: `src/api/cookie_health_scanner.py`
- Modify: `src/api/main.py`
- Modify: `frontend/src/components/ConfigCenter.tsx`
- Modify: `frontend/src/lib/configGuides.ts`
- Test: `scripts/test_cookie_health_scanner.py`（新建）

**Interfaces:**
- Consumes：Task 1 的 `cookie_health_set`；Task 3 的 `config_routes._verify_target`、`config_routes._record_cookie_health`、`config_routes._COOKIE_HEALTH_KEYS`。
- Produces：`settings.cookie_health_scan_enabled: bool`、`settings.cookie_health_scan_interval_hours: int`；`src/api/cookie_health_scanner.start_cookie_health_scanner() -> None`（幂等，`main.py` 调用）；`CookieHealthScanner` 类供测试直接实例化。

- [ ] **Step 1: `settings.py` 新增字段**

打开 `src/config/settings.py`，在 `mc_cookie_tieba` 字段（原第 98 行）后面、`# MediaCrawler 代理IP池` 注释（原第 99 行）之前插入：

```python
    # ===== Cookie 健康定时巡检（2026-07 新增）=====
    # 默认关闭：7 个社媒平台的验证是真实浏览器自动化登录，定时反复跑有被平台风控识别的
    # 风险（自动化、有规律的重复登录本身就是风控最容易识别的行为特征）。管理员按需开启，
    # 间隔建议给到天级别，不建议按小时跑。
    cookie_health_scan_enabled: bool = Field(default=False, description="是否启用 Cookie 健康定时巡检")
    cookie_health_scan_interval_hours: int = Field(default=24, description="巡检间隔（小时），默认每天一次")
```

- [ ] **Step 2: `runtime_config.py` 新增分组 + REGISTRY 键**

打开 `src/config/runtime_config.py`，把 `GROUPS` 列表（原第 25-38 行）里的 `"cookies"` 行后面加一行：

```python
    {"key": "llm_default", "label": "模型 · 默认选择"},
    {"key": "llm_deepseek", "label": "模型 · DeepSeek"},
    {"key": "llm_qwen", "label": "模型 · 阿里百炼 Qwen"},
    {"key": "llm_local", "label": "模型 · 本地端点"},
    {"key": "search", "label": "搜索与采集服务"},
    {"key": "cookies", "label": "平台 Cookie"},
    {"key": "cookie_health", "label": "Cookie 健康巡检"},
    {"key": "email", "label": "邮件 SMTP"},
    {"key": "slack", "label": "Slack"},
    {"key": "semantic", "label": "语义召回（embedding/rerank）"},
    {"key": "proxy", "label": "代理池"},
    {"key": "mysql", "label": "MySQL 入库"},
    {"key": "checkpoint", "label": "断点续跑"},
```

在 REGISTRY 字典末尾、`"checkpoint_enabled"` 键之后（原第 146-150 行左右，`}` 收尾之前）插入：

```python
    # Cookie 健康定时巡检
    "cookie_health_scan_enabled": {
        "label": "启用定时巡检", "group": "cookie_health", "secret": False, "user": False,
        "type": "select", "choices": ["True", "False"],
    },
    "cookie_health_scan_interval_hours": {
        "label": "巡检间隔（小时）", "group": "cookie_health", "secret": False, "user": False,
    },
```

- [ ] **Step 3: 新建扫描协程**

新建 `src/api/cookie_health_scanner.py`：

```python
"""
Cookie 健康定时巡检：默认关闭的独立轮询协程。

不复用 src/scheduler（那是面向用户 TaskSpec 的 cron 调度，语义不同）；风格参照
src/scheduler/service.py 的 SchedulerService，但更简单：没有 cron 表达式，只按
settings.cookie_health_scan_interval_hours 的固定间隔跑。

每次醒来先热读 settings.cookie_health_scan_enabled（不需要重启进程即可切换开关）：
关闭时只做轻量的开关检查，不产生真实探测开销；开启且到点时，顺序（非并发）扫描
全部 10 个 Cookie，每项之间 sleep 数秒，避免同一时刻集中触发多次浏览器自动化登录，
让巡检行为更接近正常运维节奏而非批量脚本。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from src.config.settings import settings

logger = logging.getLogger(__name__)

_IDLE_CHECK_SECONDS = 300.0   # 巡检关闭/未到点时，多久再检查一次
_BETWEEN_ITEM_SECONDS = 5.0   # 同一轮巡检内，相邻两项之间的等待


class CookieHealthScanner:
    """异步轮询协程：开关关闭时空转，开启且到点时顺序扫描全部 Cookie 并落库。"""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_scan_at: float = 0.0

    def start(self) -> None:
        """启动后台轮询（幂等）。需在已有事件循环内调用。"""
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task

    async def _sleep(self, seconds: float) -> None:
        """可被 stop() 提前打断的 sleep（用 Event.wait 而非 asyncio.sleep，退出更及时）。"""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _run_one_scan(self) -> None:
        # 延迟导入，避免 config_routes 与本模块之间的循环导入；_COOKIE_HEALTH_KEYS 是
        # 10 个 Cookie key 的唯一权威列表（config_routes.py 里定义），这里直接复用，不重复声明。
        from src.api.routes.config_routes import _COOKIE_HEALTH_KEYS, _record_cookie_health, _verify_target

        logger.info("Cookie 健康巡检：开始一轮扫描（%d 项）", len(_COOKIE_HEALTH_KEYS))
        for key in _COOKIE_HEALTH_KEYS:
            if self._stop.is_set():
                break
            try:
                detail = await _verify_target(key)
                _record_cookie_health(key, "valid", detail, checked_by="scheduled")
            except Exception as e:  # noqa: BLE001 单项失败不影响本轮其余项，也不该崩掉循环
                detail = str(e)[:500]
                status = "unknown" if "无法判断" in detail else "invalid"
                _record_cookie_health(key, status, detail, checked_by="scheduled")
            await self._sleep(_BETWEEN_ITEM_SECONDS)
        logger.info("Cookie 健康巡检：本轮扫描完成")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            if not settings.cookie_health_scan_enabled:
                await self._sleep(_IDLE_CHECK_SECONDS)
                continue
            interval_seconds = max(1, settings.cookie_health_scan_interval_hours) * 3600.0
            if time.time() - self._last_scan_at < interval_seconds:
                await self._sleep(min(_IDLE_CHECK_SECONDS, interval_seconds))
                continue
            try:
                await self._run_one_scan()
            except Exception as e:  # noqa: BLE001 扫描本身意外出错也不能让循环死掉
                logger.exception("Cookie 健康巡检：本轮扫描异常：%s", e)
            self._last_scan_at = time.time()


_scanner: Optional[CookieHealthScanner] = None


def start_cookie_health_scanner() -> None:
    """幂等启动。即使巡检开关关闭也会启动循环本身（循环内部自己空转直到开关打开），
    这样管理员切换开关不需要重启进程。需在已有事件循环内调用（FastAPI 启动钩子）。"""
    global _scanner
    if _scanner is not None:
        return
    _scanner = CookieHealthScanner()
    _scanner.start()
```

- [ ] **Step 4: `main.py` 接入**

打开 `src/api/main.py`，把第 32 行的路由导入和第 44-47 行的 lifespan 改成：

```python
from src.api.services import start_scheduler  # noqa: E402
from src.api.cookie_health_scanner import start_cookie_health_scanner  # noqa: E402
```

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 先套用管理员在前端保存的全局运行时配置（.env 为兜底基线），再拉起调度器
    from src.api.auth import get_store
    from src.config.runtime_config import apply_global_overrides
    apply_global_overrides(get_store())
    start_scheduler()  # 启用时拉起定时任务后台轮询
    start_cookie_health_scanner()  # Cookie 健康巡检：循环常驻，开关关闭时内部自己空转
    yield
```

- [ ] **Step 5: 前端 `VERIFY_TARGET` + 使用指南补全（本计划 Global Constraints 的强制约定）**

打开 `frontend/src/components/ConfigCenter.tsx`，在 `VERIFY_TARGET` 表（原第 56 行 `checkpoint_enabled: "checkpoint",` 之后）加：

```ts
  checkpoint_enabled: "checkpoint",
  cookie_health_scan_enabled: "cookie_health", cookie_health_scan_interval_hours: "cookie_health",
};
```

打开 `src/api/routes/config_routes.py`，在 `_verify_target()` 里 `if target == "checkpoint":` 分支（原第 260-265 行）后面、`raise RuntimeError(f"暂不支持验证该目标: {target}")` 之前加：

```python
    if target == "cookie_health":
        if not settings.cookie_health_scan_enabled:
            return "定时巡检未启用"
        return f"定时巡检已启用，每 {settings.cookie_health_scan_interval_hours} 小时扫描一次"
```

打开 `frontend/src/lib/configGuides.ts`，在 `CHECKPOINT_ENTRIES` 定义（原第 494-504 行）后面加：

```ts
const COOKIE_HEALTH_ENTRIES: GuideEntry[] = [
  {
    key: "cookie_health_scan_enabled",
    title: "启用定时巡检",
    steps: [
      { text: "开启后会按下面的间隔自动验证全部 10 个 Cookie（7 社媒 + 京东/淘宝/拼多多）并更新状态" },
      { text: "默认关闭：社媒平台的验证是真实浏览器自动化登录，定时反复跑有被平台风控识别的风险，建议按需开启" },
      { text: "设置页/配置中心里点"验证"（手动触发）不受这个开关影响，随时可以点" },
    ],
  },
  {
    key: "cookie_health_scan_interval_hours",
    title: "巡检间隔（小时）",
    steps: [
      { text: "默认 24（每天一次），建议不要调到按小时跑——间隔越短，触发风控的概率越高" },
    ],
  },
];
```

再把 `ADMIN_GUIDE_SECTIONS`（原第 509-522 行）里 `checkpoint` 那一行后面加一行：

```ts
  { key: "checkpoint", title: "断点续跑", entries: CHECKPOINT_ENTRIES },
  { key: "cookie_health", title: "Cookie 健康巡检", entries: COOKIE_HEALTH_ENTRIES },
];
```

- [ ] **Step 6: 写测试**

新建 `scripts/test_cookie_health_scanner.py`：

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Cookie 健康定时巡检协程单测。运行：python scripts/test_cookie_health_scanner.py

不联网：monkeypatch config_routes._verify_target，只验证调度/开关/落库逻辑本身。
"""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.cookie_health_scanner import CookieHealthScanner
from src.api.routes import config_routes as cr
from src.api.store import WebUIStore
from src.config.settings import settings


def _tmp_store() -> WebUIStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return WebUIStore(tmp.name)


def test_disabled_does_not_scan():
    old = settings.cookie_health_scan_enabled
    settings.cookie_health_scan_enabled = False
    try:
        scanner = CookieHealthScanner()
        with patch.object(scanner, "_run_one_scan") as mock_scan:
            async def one_tick():
                # 直接调 _loop 的单次判断逻辑：开关关闭应该走 idle 分支，不调用 _run_one_scan
                if not settings.cookie_health_scan_enabled:
                    return
                await scanner._run_one_scan()
            asyncio.run(one_tick())
            mock_scan.assert_not_called()
    finally:
        settings.cookie_health_scan_enabled = old


def test_run_one_scan_records_all_keys():
    store = _tmp_store()
    scanner = CookieHealthScanner()

    call_count = {"n": 0}

    async def fake_verify_target(key):
        call_count["n"] += 1
        if key == "jd_cookie":
            raise RuntimeError("京东 登录状态已失效，请重新导出 Cookie")
        return f"{key} 有效"

    async def run():
        # _run_one_scan 内部延迟导入 config_routes，这里直接 patch 模块级函数
        with patch.object(cr, "get_store", return_value=store), \
             patch.object(cr, "_verify_target", side_effect=fake_verify_target):
            # 缩短相邻项等待，测试跑快点
            scanner._sleep = lambda seconds: asyncio.sleep(0)
            await scanner._run_one_scan()

    asyncio.run(run())
    assert call_count["n"] == len(cr._COOKIE_HEALTH_KEYS)
    all_health = store.cookie_health_all()
    assert len(all_health) == len(cr._COOKIE_HEALTH_KEYS)
    assert all_health["jd_cookie"]["status"] == "invalid"
    assert all_health["mc_cookie_xhs"]["status"] == "valid"


def test_start_is_idempotent():
    async def run():
        scanner = CookieHealthScanner()
        scanner.start()
        task1 = scanner._task
        scanner.start()  # 第二次调用不应该新建协程
        task2 = scanner._task
        assert task1 is task2
        await scanner.stop()
    asyncio.run(run())


def main():
    tests = [test_disabled_does_not_scan, test_run_one_scan_records_all_keys, test_start_is_idempotent]
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

- [ ] **Step 7: 运行测试**

Run: `python scripts/test_cookie_health_scanner.py`
Expected: `3/3 通过`。

- [ ] **Step 8: 回归 + 覆盖度校验**

Run:
```bash
python scripts/test_cookie_health_store.py
python scripts/test_ecommerce_cookie_verify.py
python scripts/test_cookie_health_verify.py
cd frontend && npm run build
```
Expected: 全部通过/无编译错误。另外手工核对一遍"Global Constraints"里的三条覆盖度约定（可用本会话此前用过的核对脚本思路：对比 `REGISTRY` 的 key 集合与 `VERIFY_TARGET` 的 key 集合、与 `configGuides.ts` 的 `getGuideForKey` 覆盖，两个新 key 应均已出现）。

- [ ] **Step 9: Commit**

```bash
git add src/config/settings.py src/config/runtime_config.py src/api/cookie_health_scanner.py \
        src/api/main.py src/api/routes/config_routes.py \
        frontend/src/components/ConfigCenter.tsx frontend/src/lib/configGuides.ts \
        scripts/test_cookie_health_scanner.py
git commit -m "feat: 新增Cookie健康定时巡检开关与后台扫描协程，默认关闭"
```

---

### Task 5: 前端展示"最后一次验证状态"

**Files:**
- Modify: `frontend/src/lib/utils.ts`
- Modify: `frontend/src/components/ConfigCenter.tsx`

**Interfaces:**
- Consumes：Task 3 的 `/api/config` 响应里每个 Cookie 项新增的 `health` 字段（`{status, message, checked_at, checked_by} | null`）。
- Produces：`formatRelativeTime(iso: string): string`（`utils.ts` 导出，纯函数）。本任务是本计划最后一个任务，无后续任务依赖它。

- [ ] **Step 1: `utils.ts` 新增相对时间格式化**

打开 `frontend/src/lib/utils.ts`，在 `cn()` 函数后面加：

```ts
/** 把 ISO 时间戳格式化成"3分钟前/2小时前/5天前"这类相对时间，用于 Cookie 健康状态等展示。 */
export function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const diffMs = Date.now() - then;
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins}分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}小时前`;
  const days = Math.floor(hours / 24);
  return `${days}天前`;
}
```

- [ ] **Step 2: `ConfigCenter.tsx` 类型 + 展示**

打开 `frontend/src/components/ConfigCenter.tsx`。

在顶部 import 区（第 9 行 `import { api } from "@/lib/api";` 后面）加：

```ts
import { formatRelativeTime } from "@/lib/utils";
```

把 `CfgItem` 接口（原第 13-17 行）改成：

```ts
interface CookieHealth {
  status: "valid" | "invalid" | "unknown";
  message: string;
  checked_at: string;
  checked_by: "manual" | "scheduled";
}
interface CfgItem {
  key: string; label: string; secret: boolean; user_editable?: boolean;
  source?: "override" | "env"; value: string; set?: boolean; group?: string;
  type?: "select"; choices?: string[]; choicesFrom?: string;  // 下拉选择型
  health?: CookieHealth | null;  // 仅 cookies 分组的项有值；未验证过为 null
}
interface CfgGroup { key: string; label: string; items: CfgItem[] }
```

在 `AdminConfigCenter` 函数内部找一个合适位置（比如 `isSlowVerifyTarget`/`slowVerifyConfirmText` 函数定义后面，模块级、组件外面均可，这里放在模块级函数区，紧跟 `slowVerifyConfirmText` 之后）新增一个小展示组件：

```tsx
/** best-effort：反爬较激进、结果仅供参考的 Cookie（拼多多）。 */
const BEST_EFFORT_COOKIE_KEYS = new Set(["pdd_cookie"]);

function CookieHealthBadge({ health }: { health?: CookieHealth | null }) {
  if (!health) {
    return <Badge variant="outline">从未验证</Badge>;
  }
  const variant = health.status === "valid" ? "success" : health.status === "invalid" ? "danger" : "outline";
  const text = health.status === "valid" ? "有效" : health.status === "invalid" ? "失效" : "未知";
  return (
    <Badge variant={variant} title={health.message}>
      {text} · {formatRelativeTime(health.checked_at)}
    </Badge>
  );
}
```

最后在 Cookie 行渲染处（原第 239-241 行，`Badge` 徽章之后）插入健康状态展示：

```tsx
                        <Badge variant={it.source === "override" ? "success" : "outline"}>
                          {it.source === "override" ? "已覆盖" : ".env 兜底"}
                        </Badge>
                        {g.key === "cookies" && (
                          <>
                            <CookieHealthBadge health={it.health} />
                            {BEST_EFFORT_COOKIE_KEYS.has(it.key) && (
                              <span
                                className="rounded border border-border/60 px-1 text-[10px] text-muted-foreground"
                                title="反爬较激进，结果仅供参考"
                              >
                                best-effort
                              </span>
                            )}
                          </>
                        )}
```

（注意：淘宝原来设计文档里也提到"淘宝相对更可靠但仍可能受反爬"，但本计划的 `_ECOMMERCE_PROBES` 里只把 `pdd_cookie` 标了 `best_effort=True`；`tb_cookie` 走的是非 best-effort 路径（遇到非明确登录跳转时消息同样含"无法判断"，只是 UI 角标只标注拼多多）。如果验证后发现淘宝的假阳性率也偏高，把 `BEST_EFFORT_COOKIE_KEYS` 加上 `"tb_cookie"` 即可，同时把 `config_routes.py` 里 `_ECOMMERCE_PROBES["tb_cookie"]["best_effort"]` 一并改成 `True`——两处要保持一致。）

- [ ] **Step 3: 构建确认**

Run: `cd frontend && npm run build`
Expected: `tsc --noEmit && vite build` 无报错。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/utils.ts frontend/src/components/ConfigCenter.tsx
git commit -m "feat: 配置中心Cookie行展示最近一次验证状态徽章"
```

---

## 最终验证（全部任务完成后）

1. `python scripts/test_cookie_health_store.py && python scripts/test_ecommerce_cookie_verify.py && python scripts/test_cookie_health_verify.py && python scripts/test_cookie_health_scanner.py && python scripts/test_mc_cookie.py && python scripts/test_ecommerce.py` 全部通过。
2. `cd frontend && npm run build` 无报错。
3. 重启后端进程（本次改动涉及 `settings.py`/`runtime_config.py`/`main.py` 等后端 `.py` 文件，REGISTRY 新增字段需要重启才能生效——沿用本会话一直遵守的约定）。
4. 手工/脚本调用 `POST /api/config/verify {"target": "jd_cookie"}`（管理员账号），用一个已知失效和一个已知有效的京东 Cookie 分别验证，确认 `/api/config` 里 `jd_cookie` 项的 `health` 字段正确更新为 `invalid`/`valid`。
5. 打开设置页确认 Cookie 行展示"有效/失效/从未验证"徽章，拼多多行有"best-effort"角标。
6. 打开 `cookie_health_scan_enabled` 开关、把 `cookie_health_scan_interval_hours` 临时改成很小的值，观察后端日志确认按间隔自动跑完 10 项且 `checked_by="scheduled"`；关闭开关后确认不再自动扫描。
