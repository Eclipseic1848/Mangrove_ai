# 用户管理：搜索 + 分页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户管理页在大量账号（未来预期上千+）场景下也能快速定位用户：后端支持关键词/角色/状态过滤 + 分页，前端提供对应的搜索框、筛选下拉与翻页控件。

**Architecture:** 后端 `WebUIStore.list_users()` 从"返回全表"改为"按 WHERE 子句过滤 + LIMIT/OFFSET 分页，返回 (当前页数据, 过滤后总数)"；`GET /api/admin/users` 路由透传 q/role/status/page/page_size 查询参数并把 total/pending_total 一并返回。前端 `Admin.tsx` 新增筛选状态（防抖搜索文本 + 角色 + 状态 + 页码），随状态变化重新请求，并渲染筛选栏与翻页条。

**Tech Stack:** FastAPI + 标准库 `sqlite3`（后端）；React + TypeScript + Tailwind，原生 `<select>`（前端，无新增依赖）。

## Global Constraints

- 代码注释使用中文；新建/修改的 Python、TSX 文件均为 UTF-8。
- 不引入新依赖（前端沿用原生 `<select>`；防抖用 `setTimeout`，不用外部库）。
- 每页固定 20 条（`PAGE_SIZE = 20`），不做用户可调页大小。
- 分页控件仅"上一页 / 下一页 / 第 X / Y 页"文字，不做页码按钮阵列。
- 不改动既有 RBAC 分级校验（`_assert_outranks`/`_assert_can_assign`）与单条用户操作（创建/改角色/禁用/改昵称/重置密码/删除/审批）的权限逻辑。
- 本仓库无 pytest，测试脚本走既有约定：`scripts/test_*.py`，纯 `assert` + 手写 `_check`/`main()` 汇总 PASS/FAIL 并以退出码收尾（参照 `scripts/test_scheduler.py`）。前端无测试框架，改动通过 `npm run build` + 手动浏览器验证。

---

### Task 1: 后端 — `WebUIStore.list_users` 过滤/分页改造 + 路由改造 + 自动化测试

**Files:**
- Modify: `src/api/store.py:170-177`（`list_users` 方法）
- Modify: `src/api/routes/admin_routes.py:36-38`（`GET /users` 路由）
- Test: `scripts/test_admin_users.py`（新建）

**Interfaces:**
- Produces: `WebUIStore.list_users(q: str = "", role: str = "", status: str = "", page: int = 1, page_size: int = 20) -> tuple[list[dict], int]` —— 返回 `(当前页用户列表, 过滤后总数)`。列表中每条 dict 字段与现状一致：`user_id/username/display_name/role/disabled/pending/created_at`。
- Produces: `GET /api/admin/users?q=&role=&status=&page=&page_size=` 返回 `{"users": [...], "total": int, "pending_total": int, "page": int, "page_size": int}`。
- Consumes：无（本任务是本特性的底层，不依赖其他任务）。

- [ ] **Step 1: 修改 `src/api/store.py` 顶部 import，补充 `Tuple`**

把第 20 行：
```python
from typing import Any, Dict, Iterator, List, Optional
```
改为：
```python
from typing import Any, Dict, Iterator, List, Optional, Tuple
```

- [ ] **Step 2: 重写 `src/api/store.py` 的 `list_users` 方法**

把现有第 170-177 行：
```python
    def list_users(self) -> List[Dict[str, Any]]:
        """列出全部用户（不含密码哈希），供管理员后台。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT user_id, username, display_name, role, disabled, pending, created_at "
                "FROM users ORDER BY created_at, rowid"
            ).fetchall()
        return [dict(r) for r in rows]
```
替换为：
```python
    def list_users(
        self, q: str = "", role: str = "", status: str = "",
        page: int = 1, page_size: int = 20,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """按关键词/角色/状态过滤 + 分页列出用户（不含密码哈希），供管理员后台。

        q: 匹配 username 或 display_name 的子串，空串不过滤。
        role: "" | super_admin | admin | user，空串不过滤。
        status: "" | normal | disabled | pending，空串不过滤。
        返回 (当前页用户列表, 过滤后总数)。
        """
        where: List[str] = []
        params: List[Any] = []
        if q:
            where.append("(username LIKE ? OR display_name LIKE ?)")
            like = f"%{q}%"
            params.extend([like, like])
        if role:
            where.append("role=?")
            params.append(role)
        if status == "normal":
            where.append("disabled=0 AND pending=0")
        elif status == "disabled":
            where.append("disabled=1")
        elif status == "pending":
            where.append("pending=1")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        offset = max(0, (page - 1) * page_size)
        with self._conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) AS c FROM users {clause}", params).fetchone()["c"]
            rows = conn.execute(
                f"SELECT user_id, username, display_name, role, disabled, pending, created_at "
                f"FROM users {clause} ORDER BY created_at, rowid LIMIT ? OFFSET ?",
                params + [page_size, offset],
            ).fetchall()
        return [dict(r) for r in rows], total
```

- [ ] **Step 3: 修改 `src/api/routes/admin_routes.py` 的 `GET /users` 路由**

把现有第 36-38 行：
```python
@router.get("/users")
def list_users(admin=Depends(require_admin)):
    return {"users": get_store().list_users()}
```
替换为：
```python
@router.get("/users")
def list_users(
    q: str = "", role: str = "", status: str = "",
    page: int = 1, page_size: int = 20,
    admin=Depends(require_admin),
):
    store = get_store()
    users, total = store.list_users(q=q, role=role, status=status, page=page, page_size=page_size)
    return {
        "users": users, "total": total,
        "pending_total": store.count_pending(),
        "page": page, "page_size": page_size,
    }
```

- [ ] **Step 4: 新建测试脚本 `scripts/test_admin_users.py`**

```python
#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用户管理搜索/分页测试（无 pytest，纯断言；失败抛异常并以非零退出）。

运行：python scripts/test_admin_users.py
覆盖：WebUIStore.list_users 的关键词/角色/状态过滤 + 分页边界，
以及 admin_routes.list_users 路由返回结构（直接调用路由函数，绕开 FastAPI 依赖注入）。
"""
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.store import WebUIStore


def _seed(store: WebUIStore):
    store.create_user("alice", "hash", "Alice Zhang", role="admin")
    store.create_user("bob", "hash", "Bob Li", role="user")
    store.create_user("carol", "hash", "Carol Wang", role="user", pending=True)
    store.create_user("dave", "hash", "Dave Chen", role="user")
    store.update_user(store.get_user_by_name("dave")["user_id"], disabled=True)


def test_search_by_username_or_display_name():
    with tempfile.TemporaryDirectory() as d:
        store = WebUIStore(str(Path(d) / "w.db"))
        _seed(store)
        users, total = store.list_users(q="ali")
        assert total == 1 and users[0]["username"] == "alice", (total, users)
        users2, total2 = store.list_users(q="Wang")
        assert total2 == 1 and users2[0]["username"] == "carol", (total2, users2)


def test_filter_by_role():
    with tempfile.TemporaryDirectory() as d:
        store = WebUIStore(str(Path(d) / "w.db"))
        _seed(store)
        users, total = store.list_users(role="admin")
        assert total == 1 and users[0]["username"] == "alice", (total, users)


def test_filter_by_status():
    with tempfile.TemporaryDirectory() as d:
        store = WebUIStore(str(Path(d) / "w.db"))
        _seed(store)
        _, pending_total = store.list_users(status="pending")
        assert pending_total == 1, pending_total
        _, disabled_total = store.list_users(status="disabled")
        assert disabled_total == 1, disabled_total
        _, normal_total = store.list_users(status="normal")
        assert normal_total == 2, normal_total  # alice + bob（dave 已禁用，carol 待审批）


def test_pagination_boundaries():
    with tempfile.TemporaryDirectory() as d:
        store = WebUIStore(str(Path(d) / "w.db"))
        for i in range(25):
            store.create_user(f"user{i:02d}", "hash", f"用户{i:02d}")
        page1, total = store.list_users(page=1, page_size=20)
        assert total == 25 and len(page1) == 20, (total, len(page1))
        page2, total2 = store.list_users(page=2, page_size=20)
        assert total2 == 25 and len(page2) == 5, (total2, len(page2))
        page3, total3 = store.list_users(page=3, page_size=20)
        assert total3 == 25 and len(page3) == 0, (total3, len(page3))  # 超出范围返回空列表而非报错


def test_route_response_shape():
    from src.api.routes import admin_routes
    import src.api.auth as auth_mod

    with tempfile.TemporaryDirectory() as d:
        store = WebUIStore(str(Path(d) / "w.db"))
        _seed(store)
        original = auth_mod._store
        auth_mod._store = store
        try:
            out = admin_routes.list_users(
                q="", role="", status="", page=1, page_size=20,
                admin={"role": "super_admin"},
            )
        finally:
            auth_mod._store = original
        assert out["total"] == 4, out["total"]
        assert out["pending_total"] == 1, out["pending_total"]
        assert out["page"] == 1 and out["page_size"] == 20, out
        assert len(out["users"]) == 4, out["users"]


def main():
    tests = [
        test_search_by_username_or_display_name,
        test_filter_by_role,
        test_filter_by_status,
        test_pagination_boundaries,
        test_route_response_shape,
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

- [ ] **Step 5: 运行测试脚本，确认全部通过**

Run: `python scripts/test_admin_users.py`
Expected:
```
PASS  test_search_by_username_or_display_name
PASS  test_filter_by_role
PASS  test_filter_by_status
PASS  test_pagination_boundaries
PASS  test_route_response_shape
==================================================
5/5 通过
```

- [ ] **Step 6: Commit**

```bash
git add src/api/store.py src/api/routes/admin_routes.py scripts/test_admin_users.py
git commit -m "feat: 用户管理接口支持关键词/角色/状态过滤与分页"
```

---

### Task 2: 前端 — 搜索框 + 角色/状态筛选下拉

**Files:**
- Modify: `frontend/src/pages/Admin.tsx`

**Interfaces:**
- Consumes: Task 1 产出的 `GET /api/admin/users?q=&role=&status=&page=&page_size=` → `{"users": AdminUser[], "total": number, "pending_total": number, "page": number, "page_size": number}`。
- Produces：组件内部状态 `q`/`debouncedQ`/`roleFilter`/`statusFilter`/`page`/`total`/`pendingTotal`，供 Task 3 的分页 UI 直接复用（`page`/`total`/`PAGE_SIZE` 已具备，无需再引入新状态）。

- [ ] **Step 1: 修改顶部 import，补充图标**

把 `frontend/src/pages/Admin.tsx` 第 2-4 行：
```tsx
import {
  Users, RefreshCw, Shield, ShieldOff, KeyRound, Trash2, UserPlus, Ban, CheckCircle2, UserCheck, Clock, Pencil,
} from "lucide-react";
```
改为：
```tsx
import {
  Users, RefreshCw, Shield, ShieldOff, KeyRound, Trash2, UserPlus, Ban, CheckCircle2, UserCheck, Clock, Pencil,
  Search, ChevronLeft, ChevronRight,
} from "lucide-react";
```

- [ ] **Step 2: 在 `AdminUser` 接口后新增筛选常量**

在第 22 行（`interface AdminUser {...}` 结束的 `}` 之后）新增：
```tsx

const PAGE_SIZE = 20;
const ROLE_FILTER_OPTIONS = ["", "super_admin", "admin", "user"] as const;
const STATUS_FILTER_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "normal", label: "正常" },
  { value: "disabled", label: "已禁用" },
  { value: "pending", label: "待审批" },
] as const;
```

- [ ] **Step 3: 新增筛选/分页状态，替换 `load` 与其触发时机**

把第 28-45 行：
```tsx
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [allowReg, setAllowReg] = useState<boolean | null>(null);
  // 弹窗状态
  const [pwdTarget, setPwdTarget] = useState<AdminUser | null>(null);
  const [newPwd, setNewPwd] = useState("");
  const [nameTarget, setNameTarget] = useState<AdminUser | null>(null);
  const [newName, setNewName] = useState("");
  const [delTarget, setDelTarget] = useState<AdminUser | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ username: "", password: "", display_name: "", role: "user" });

  const load = () => {
    setLoading(true);
    api.get("/api/admin/users").then((d) => setUsers(d.users || [])).catch(() => {}).finally(() => setLoading(false));
    api.get("/api/admin/registration").then((d) => setAllowReg(d.enabled)).catch(() => {});
  };
  useEffect(load, []);
```
改为：
```tsx
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [pendingTotal, setPendingTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [allowReg, setAllowReg] = useState<boolean | null>(null);
  // 搜索/筛选/分页
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [roleFilter, setRoleFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [page, setPage] = useState(1);
  // 弹窗状态
  const [pwdTarget, setPwdTarget] = useState<AdminUser | null>(null);
  const [newPwd, setNewPwd] = useState("");
  const [nameTarget, setNameTarget] = useState<AdminUser | null>(null);
  const [newName, setNewName] = useState("");
  const [delTarget, setDelTarget] = useState<AdminUser | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ username: "", password: "", display_name: "", role: "user" });

  // 搜索框输入防抖 300ms 再触发请求
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(t);
  }, [q]);

  // 筛选条件变化时回到第 1 页
  useEffect(() => {
    setPage(1);
  }, [debouncedQ, roleFilter, statusFilter]);

  const load = () => {
    setLoading(true);
    const params = new URLSearchParams({
      q: debouncedQ, role: roleFilter, status: statusFilter,
      page: String(page), page_size: String(PAGE_SIZE),
    });
    api.get(`/api/admin/users?${params}`)
      .then((d) => {
        setUsers(d.users || []);
        setTotal(d.total || 0);
        setPendingTotal(d.pending_total || 0);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  };
  useEffect(load, [debouncedQ, roleFilter, statusFilter, page]);
  useEffect(() => {
    api.get("/api/admin/registration").then((d) => setAllowReg(d.enabled)).catch(() => {});
  }, []);
```

- [ ] **Step 4: 用筛选栏替换用户 Card 的标题区**

把第 157-167 行：
```tsx
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Users className="h-4 w-4 text-primary" /> 用户（{users.length}）
                {users.some((u) => u.pending) && (
                  <Badge variant="warning">
                    <Clock className="h-3.5 w-3.5" /> {users.filter((u) => u.pending).length} 待审批
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
```
改为：
```tsx
          <Card>
            <CardHeader className="space-y-3">
              <CardTitle className="flex items-center gap-2 text-base">
                <Users className="h-4 w-4 text-primary" /> 用户（共 {total}）
                {pendingTotal > 0 && (
                  <Badge variant="warning">
                    <Clock className="h-3.5 w-3.5" /> {pendingTotal} 待审批
                  </Badge>
                )}
              </CardTitle>
              <div className="flex flex-wrap gap-2">
                <div className="relative min-w-[200px] flex-1">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                  <Input
                    className="pl-8"
                    placeholder="搜索用户名/昵称…"
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                  />
                </div>
                <select
                  className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={roleFilter}
                  onChange={(e) => setRoleFilter(e.target.value)}
                >
                  {ROLE_FILTER_OPTIONS.map((r) => (
                    <option key={r} value={r}>{r === "" ? "全部角色" : roleLabel(r)}</option>
                  ))}
                </select>
                <select
                  className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value)}
                >
                  {STATUS_FILTER_OPTIONS.map((s) => (
                    <option key={s.value} value={s.value}>{s.label}</option>
                  ))}
                </select>
              </div>
            </CardHeader>
```

- [ ] **Step 5: 空结果态文案**

把第 168-171 行：
```tsx
            <CardContent className="space-y-2">
              {loading ? (
                <p className="text-sm text-muted-foreground">加载中…</p>
              ) : (
```
改为：
```tsx
            <CardContent className="space-y-2">
              {loading ? (
                <p className="text-sm text-muted-foreground">加载中…</p>
              ) : users.length === 0 ? (
                <p className="text-sm text-muted-foreground">未找到匹配的用户</p>
              ) : (
```
（对应的 `)}` 闭合与 `users.map(...)` 内容保持不变，无需改动。）

- [ ] **Step 6: 构建验证**

Run: `cd frontend && npm run build`
Expected: `tsc --noEmit && vite build` 无报错，`dist/` 产出成功。

- [ ] **Step 7: 浏览器手动验证**

1. 启动前后端（或使用现有一键启停脚本），打开"用户管理"页。
2. 搜索框输入已知用户名子串，等待约 300ms，确认列表按 username/display_name 过滤。
3. 切换角色下拉为"管理员"，确认只剩管理员账号；切换状态下拉为"已禁用"，确认只剩禁用账号。
4. 确认切换任一筛选条件后列表从头（第 1 页）展示，标题"用户（共 N）"的 N 随过滤结果变化，"待审批"徽标数字不随筛选变化（后端 `pending_total` 全局值）。

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/Admin.tsx
git commit -m "feat: 用户管理页新增搜索框与角色/状态筛选"
```

---

### Task 3: 前端 — 分页控件 + 删除后刷新总数

**Files:**
- Modify: `frontend/src/pages/Admin.tsx`

**Interfaces:**
- Consumes: Task 2 产出的 `page`/`total`/`PAGE_SIZE`/`load()`。
- Produces：无（本任务是本特性的最后一层 UI，无后续任务依赖它）。

- [ ] **Step 1: 让删除操作后刷新列表（而非仅本地过滤），使 `total`/分页保持准确**

把 `doDelete`（当前约第 78-89 行）：
```tsx
  const doDelete = async () => {
    if (!delTarget) return;
    const id = delTarget.user_id;
    setDelTarget(null);
    try {
      await api.del(`/api/admin/users/${id}`);
      toast.success("已删除用户");
      setUsers((us) => us.filter((x) => x.user_id !== id));
    } catch (e: any) {
      toast.error(e.message || "删除失败");
    }
  };
```
改为：
```tsx
  const doDelete = async () => {
    if (!delTarget) return;
    const id = delTarget.user_id;
    setDelTarget(null);
    try {
      await api.del(`/api/admin/users/${id}`);
      toast.success("已删除用户");
      load();
    } catch (e: any) {
      toast.error(e.message || "删除失败");
    }
  };
```

- [ ] **Step 2: 在用户 Card 内、`CardContent` 之后新增分页条**

在 `</CardContent>` 与其后的 `</Card>` 之间（当前约第 262-263 行）插入：
```tsx
            </CardContent>
            {total > PAGE_SIZE && (
              <div className="flex items-center justify-center gap-3 border-t border-border/60 px-4 py-3 text-sm text-muted-foreground">
                <Button
                  variant="outline" size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  <ChevronLeft className="h-4 w-4" /> 上一页
                </Button>
                <span>第 {page} / {Math.max(1, Math.ceil(total / PAGE_SIZE))} 页</span>
                <Button
                  variant="outline" size="sm"
                  disabled={page >= Math.max(1, Math.ceil(total / PAGE_SIZE))}
                  onClick={() => setPage((p) => p + 1)}
                >
                  下一页 <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            )}
          </Card>
```
（原来紧跟在 `</CardContent>` 后面的 `</Card>` 被替换为上面这段末尾的 `</Card>`，其余结构不变。）

- [ ] **Step 3: 构建验证**

Run: `cd frontend && npm run build`
Expected: 无报错，构建成功。

- [ ] **Step 4: 浏览器手动验证**

1. 若当前测试环境用户数 ≤ 20，临时通过"新建用户"批量建到 21+ 个，确认分页条出现。
2. 确认"上一页"在第 1 页禁用、"下一页"在最后一页禁用，页码文字随翻页更新。
3. 删除一个用户后确认标题"用户（共 N）"的 N 减 1，且分页条页数同步更新。
4. 用户数 ≤ 20 时确认分页条不显示（`total > PAGE_SIZE` 为假）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Admin.tsx
git commit -m "feat: 用户管理页新增分页控件"
```
