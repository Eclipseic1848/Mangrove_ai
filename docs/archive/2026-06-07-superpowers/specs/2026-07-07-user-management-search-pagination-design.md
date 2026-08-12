# 用户管理页：搜索 + 分页设计

日期：2026-07-07

## 背景与目标

用户管理页（`frontend/src/pages/Admin.tsx`）当前一次性拉取全量用户列表渲染，无搜索、无分页。
用户量级预期未来可能达到上千甚至更多，纯前端全量拉取+本地过滤会随规模退化（首屏体积、渲染卡顿）。
目标：让超级管理员/管理员在大量用户下也能快速定位并操作目标账号。

## 范围

- 后端 `GET /api/admin/users` 增加过滤（关键词 + 角色 + 状态）与分页参数，返回总数。
- 前端用户管理页增加搜索框（防抖）+ 角色/状态下拉筛选 + 上一页/下一页分页控件。
- 不改动 RBAC 分级校验、创建/更新/删除用户的既有逻辑。
- 不做用户可调的每页条数、不做页码按钮阵列（YAGNI，量级到几千页时价值不大）。

## 后端设计

### `src/api/store.py` — `WebUIStore`

新增/改造两个方法：

```python
def list_users(
    self, q: str = "", role: str = "", status: str = "",
    page: int = 1, page_size: int = 20,
) -> tuple[list[dict], int]:
    """按条件过滤 + 分页列出用户，返回 (当前页数据, 过滤后总数)。

    q: 子串匹配 username OR display_name（SQL LIKE，两端加 %）。
    role: "" | super_admin | admin | user，精确匹配 role 列。
    status: "" | normal | disabled | pending：
        normal   → disabled=0 AND pending=0
        disabled → disabled=1
        pending  → pending=1
    排序沿用现有 ORDER BY created_at, rowid。
    """

```

实现要点：
- WHERE 子句按传入的 q/role/status 动态拼接（均为可选，空字符串表示不过滤）。
- 复用同一 WHERE 子句先后执行 `COUNT(*)` 和 `SELECT ... LIMIT ? OFFSET ?` 两次查询得到 `(rows, total)`。
- 页头"N 待审批"徽标复用既有 `count_pending()` 方法（本就不受 q/role/status 过滤影响），无需新增方法。

### `src/api/routes/admin_routes.py` — `GET /api/admin/users`

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

不改动 `create_user`/`update_user`/`delete_user`/RBAC 分级校验（`_assert_outranks`/`_assert_can_assign`）。

## 前端设计

### 状态（`Admin.tsx`）

新增 state：`q`（搜索文本，即时值）、`debouncedQ`（300ms 防抖后触发请求的值）、`roleFilter`、`statusFilter`、`page`、`total`、`pendingTotal`。

### 请求

`load()` 改为拼接查询串：
```
/api/admin/users?q=<debouncedQ>&role=<roleFilter>&status=<statusFilter>&page=<page>&page_size=20
```
响应写入 `users`/`total`/`pendingTotal`。

触发时机：
- `debouncedQ`/`roleFilter`/`statusFilter` 任一变化 → `page` 重置为 1，随后触发 `load()`。
- `page` 变化 → 直接触发 `load()`。
- 顶部"刷新"按钮 → 用当前筛选+当前页重新 `load()`（行为不变，仅参数变化）。

### 布局

筛选栏位于"用户（共 N）"标题下方、列表上方，Card 内新增一行：
搜索框（图标+placeholder"搜索用户名/昵称…"）+ 角色下拉（全部/超级管理员/管理员/普通用户）+ 状态下拉（全部/正常/已禁用/待审批）。

列表下方新增分页条：`‹ 上一页    第 X / Y 页    下一页 ›`，`Y = Math.max(1, Math.ceil(total / 20))`，首页禁用"上一页"，末页禁用"下一页"。

标题处的"N 待审批"徽标改为读取 `pendingTotal`（全局值），不受当前搜索/筛选影响，语义与目前一致（仅提示，不做筛选联动）。

空结果：列表区域展示"未找到匹配的用户"文案，替代当前的用户行渲染。

### 不改动

- 单条用户的操作按钮（角色切换/禁用/改昵称/重置密码/删除/通过审批）逻辑和权限判断完全不变。
- 新建用户弹窗、自助注册开关卡片不受影响。

## 验证

- 后端：手动构造多个测试用户，验证 q/role/status 组合过滤 + 分页边界（第 1 页、最后一页、超出范围页）返回正确 `total`。
- 前端：`npm run build` 通过；本地启动后在浏览器验证搜索防抖生效、筛选切换重置到第 1 页、分页按钮边界禁用状态正确、待审批徽标不随筛选变化。
