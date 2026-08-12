# 模板库权限收紧 + 巡检开关前端化设计

## 背景

C 阶段前两个子项目（教训库前端管理页面、定时巡检）已交付但未推送。用户审查这两块交付物后提出两个新需求，与 C 阶段第三个子项目（评测基线扩展）无关，是独立的收尾改动：

1. **权限问题**：模板库页面的「教训库」「巡检报告」两个Tab，普通用户和管理员看到的内容完全一样（无数据隔离，仅删除按钮区分权限）。教训库记录的是任务失败症状、巡检报告记录的是知识库运维动作，两者都偏内部/运维向内容，普通用户没有查看必要，需要收紧为仅管理员可见。
2. **巡检开关前端化**：定时巡检相关的4个配置项（`library_dedup_scan_enabled`/`library_dedup_scan_interval_hours`/`library_stale_draft_days`/`library_dedup_scan_max_merges_per_run`）目前只能改 `.env`，需要接入现有「配置中心」通用机制，做到admin可在前端直接改、改完即时生效。

## 目标

- 「教训库」「巡检报告」两个Tab（含其后端数据接口）对普通用户不可见/不可访问，仅管理员可用；「模板库」Tab权限模型不变（预览人人可见，删除仅admin）。
- 定时巡检的4个配置项接入「配置中心」（`src/config/runtime_config.py` 的 REGISTRY 驱动），管理员可在设置页直接切换开关/改数值，保存即热生效，不需要重启进程、不需要改 `.env`。

**不做**（本次范围外）：
- 不给这4个巡检配置项加验证按钮的连通性探测逻辑（它们是纯计数/开关型配置，跟 `smtp_host`/`mysql_host` 等同款字段一样没有"验证"这回事）。
- 不加数值范围校验（REGISTRY 现有字段本来就没有 min/max 校验框架，不为这次新增字段单独造轮子）。
- 不改「模板库」Tab本身的权限模型（预览人人可见、删除仅admin，维持现状）。
- 不做按用户隔离数据（模板库/教训库/巡检报告都是全局共享知识库，用户已确认不动这个设计前提，本次只收紧"整个Tab对谁可见"这一层）。

## 设计

### 1. 权限收紧

**前端** `frontend/src/pages/Templates.tsx`：
- Tab切换按钮区（`Templates.tsx:159-174`）：「教训库」「巡检报告」两个 `<Button>` 用 `{isAdmin && (...)}` 包裹，只有管理员能看到并点击进入这两个Tab。「模板库」按钮保持无条件渲染。
- 数据拉取的两个 `useEffect`（`loadLessons` 挂载于 `Templates.tsx:96`、`loadScanLog` 挂载于 `Templates.tsx:106`）改为仅在 `isAdmin` 为真时才在挂载时调用（`useEffect(() => { if (isAdmin) loadLessons(); }, [isAdmin])`，`loadScanLog` 同理），避免普通用户一进页面就发出会被后端403拒绝的请求。
- 由于Tab按钮已经不渲染，普通用户在UI上没有任何路径能把 `tab` state 切到 `"lessons"`/`"scanLog"`，因此不需要额外的"越权时强制切回模板库"防御逻辑——这条路径本来就不可达。

**后端**：
- `src/api/routes/lessons_routes.py`：`GET /api/lessons` 的依赖从 `get_current_user` 改为 `require_admin`（`DELETE /api/lessons/{slug}` 已经是 `require_admin`，本次是把读接口的收紧粒度对齐到和写接口一致）。
- `src/api/routes/library_dedup_routes.py`：`GET /api/library-dedup-log` 的依赖从 `get_current_user` 改为 `require_admin`。
- `src/api/routes/templates_routes.py` 不改动（`GET /api/templates` 保持 `get_current_user`，`DELETE /api/templates/{slug}` 保持 `require_admin`）。

这样即便普通用户绕过前端直接调用这两个接口，也会收到 403，做到前后端同步收紧（防御深度）。

### 2. 巡检开关前端化

**后端** `src/config/runtime_config.py`：在 `REGISTRY` 字典里新增4条，全部 `group: "library_dedup"`、`user: False`（仅admin可调、全局生效，不是per-user覆盖）：

```python
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
```

这4个字段类型（bool/int/int/int）与 `src/config/settings.py` 里对应 `Field` 的类型注解一致，`cast_value` 沿用现有的按类型注解转换逻辑，不需要新代码。保存后走既有链路：`PUT /api/config/{key}` → `store.config_set` 持久化到 `webui.db` → `setattr(settings, key, value)` 内存热更新；`src/api/library_dedup_scanner.py` 的 `_loop()` 已经确认是每次wake都热读 `settings.library_dedup_scan_*`，不需要额外改动即可拿到新值。

**前端** `frontend/src/components/ConfigCenter.tsx`：
- `GROUP_CATEGORIES`（`ConfigCenter.tsx:87-92`）新增一个独立大类：
  ```typescript
  { label: "知识库巡检", groups: ["library_dedup"] },
  ```
- 不需要新增任何组件——`AdminConfigCenter` 本身是通用的，读 `GET /api/config` 返回的 `groups` 渲染，新分组会自动出现在这个新大类下，编辑/保存/重置的交互（含 select 类型的开关渲染）全部复用现成代码路径。

**前端文案更新** `frontend/src/pages/Templates.tsx`：巡检报告Tab的空状态文案（约 `Templates.tsx:368-370` 附近）从"暂无巡检记录（巡检开关默认关闭，需在 `.env` 开启 `LIBRARY_DEDUP_SCAN_ENABLED` 后台生效）"改为"暂无巡检记录（巡检开关默认关闭，可在「设置 → 配置中心 → 知识库巡检」开启）"。

## 测试计划

- 后端单测（追加到现有测试文件或新建 `scripts/test_library_permission.py`）：
  - `GET /api/lessons` 用普通用户 token 请求 → 403；用 admin token 请求 → 200
  - `GET /api/library-dedup-log` 同上
  - REGISTRY 新增的4个key：`GET /api/config` 返回结果包含 `library_dedup` 分组且4项齐全；`PUT /api/config/library_dedup_scan_enabled` 设为 `"False"` 后 `settings.library_dedup_scan_enabled` 立即变为 `False`（内存热更新验证）
- 前端：`npm run build` 编译通过；手工验证：普通用户登录只看到模板库Tab；管理员登录三个Tab都在且能正常加载；配置中心能看到"知识库巡检"大类并能切换开关

## 验证

1. 单元测试全绿
2. `cd frontend && npm run build` 编译通过
3. 重启后端，手工验证：普通用户账号登录模板库页面，确认只有"模板库"一个Tab；用浏览器devtools或curl直接请求 `/api/lessons`、`/api/library-dedup-log`（带普通用户token）确认返回403；管理员账号登录，三个Tab齐全，能在配置中心「知识库巡检」分组里切换开关并确认 `webui.db` 里 `runtime_config` 表落库
