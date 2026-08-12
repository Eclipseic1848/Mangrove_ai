# Cookie 健康状态记录 + 定时巡检 + 电商真实校验 设计文档

## 背景

管理员配置中心（`AdminConfigCenter`）的"验证"按钮目前是一次性的：点了才知道当次结果，不点不知道 Cookie 是否已经失效——尤其是 7 个 MediaCrawler 社媒平台（小红书/抖音/微博/B站/知乎/快手/贴吧）的 Cookie，失效往往是"某天悄悄过期"，管理员发现问题通常是等到真实采集任务失败之后。

此外，京东/淘宝/拼多多这 3 个电商 Cookie 目前的"验证"是假的：`src/api/routes/config_routes.py` 的 `_verify_target()` 里，`target.startswith(("jd_cookie", "tb_cookie", "pdd_cookie"))` 分支不管 Cookie 死活，一律返回"Cookie 已保存"——完全没有真实探测逻辑，是历史遗留的占位实现。

本次要解决三件事：
1. 每个 Cookie 记录"最后一次验证状态"（有效/失效/从未验证 + 时间），管理员在配置中心一眼可见，不用主动点验证才知道。
2. 给 7 个社媒平台加一个默认关闭的定时巡检开关，管理员可选择开启后按天级别间隔自动跑验证并更新状态。
3. 给京东/淘宝/拼多多补上真实的 Cookie 有效性校验（当前是假的），纳入同一套状态记录。

## 目标

- 新增 `cookie_health` 持久化表，记录 10 个 Cookie（7 社媒 + 3 电商）各自的 `status`（valid/invalid/unknown）、`message`、`checked_at`、`checked_by`（manual/scheduled）。
- 手动点击"验证"按钮时，结果顺带落库到 `cookie_health`——这一步对全部 10 个 Cookie 立即生效，不依赖定时巡检上线。
- 新增京东/淘宝/天猫/拼多多的真实 Cookie 有效性校验（当前是假的"已保存"占位逻辑），替换 `config_routes.py` 里对应分支。
- 新增定时巡检开关（`cookie_health_scan_enabled`，默认 `False`）+ 扫描间隔（`cookie_health_scan_interval_hours`，默认 24），开启后按间隔自动跑全部 10 个 Cookie 的验证并落库。开关走现有 REGISTRY + 启停开关的模式（本次会话已用过 3 次：邮件/Slack/断点续跑）。
- `AdminConfigCenter` 的 Cookie 行展示最近一次验证状态（有效/失效/从未验证 + 时间），淘宝/拼多多的校验结果额外标注"best-effort"。

## 非目标

- 不做失效后自动邮件/Slack 通知（现有的启停开关可以作为后续扩展点，本次不绑定）。
- 不改变"验证"按钮本身现有的交互（确认框、耗时提示、`isSlowVerifyTarget` 判定）——只是让验证结果多一步落库，其余行为不变。
- 不改进 `mc_cookie_*` 的验证方式本身（那 7 个已经是真实 MediaCrawler 浏览器登录探测，本次直接复用其结果，不重新设计探测逻辑）。
- 不复用 `src/scheduler`（那是面向用户 TaskSpec 的 cron 调度，语义与本次的"内部系统巡检"不同），单独实现一个更简单的轮询协程。
- 拼多多的校验是 best-effort，不承诺消除假阴性/假阳性；淘宝的校验相对更可靠但仍可能受反爬影响。

## 设计

### 1. 数据模型：`cookie_health` 表

落在 `webui.db`（复用 `WebUIStore`，与 `runtime_config` 表同库）：

```sql
CREATE TABLE IF NOT EXISTS cookie_health (
    key         TEXT PRIMARY KEY,   -- 配置键，如 mc_cookie_xhs / jd_cookie
    status      TEXT NOT NULL,      -- valid | invalid | unknown
    message     TEXT,               -- 人话原因；失效/无法判断时给出线索
    checked_at  TEXT NOT NULL,      -- ISO 时间戳
    checked_by  TEXT NOT NULL       -- manual | scheduled
);
```

`WebUIStore` 新增两个方法（风格对齐现有 `config_set`/`config_all`）：

```python
def cookie_health_set(self, key: str, status: str, message: str, checked_by: str) -> None:
    """写入/覆盖某 Cookie 的最近一次验证结果（INSERT OR REPLACE，key 唯一）。"""

def cookie_health_all(self) -> Dict[str, Dict[str, str]]:
    """返回 {key: {status, message, checked_at, checked_by}}，供 /api/config 拼装展示。"""
```

行不存在时（从未验证过）`cookie_health_all()` 返回的字典里没有该 key；调用方（`describe()`）据此展示"从未验证"（`status: "unknown"`）。

### 2. 触发路径

**手动**（本次改动的核心增量，立即对全部 10 个 Cookie 生效）：`config_routes.py` 的 `verify_config()` 端点，在 `_verify_target()` 成功返回或抛出 `RuntimeError` 后，若 `body.target` 精确等于 10 个 Cookie key 之一（`mc_cookie_xhs`/`mc_cookie_dy`/`mc_cookie_wb`/`mc_cookie_bili`/`mc_cookie_zhihu`/`mc_cookie_ks`/`mc_cookie_tieba`/`jd_cookie`/`tb_cookie`/`pdd_cookie`——这 10 个本来就是"目标即键名本身"，与 `email`/`proxy`/`mysql` 这类多对一的组级 target 不同），把结果写入 `cookie_health`（`checked_by="manual"`）。写入是"最佳努力"的旁路副作用——落库失败不应影响验证结果本身返回给前端。

**定时**：见第 4 节。`checked_by="scheduled"`。

两条路径共用同一张表、同一套状态含义，前端不需要关心是谁触发的（只在 `message`/`checked_by` 里保留追溯信息）。

### 3. 京东/淘宝/天猫/拼多多真实校验

新增 `_verify_ecommerce_cookie(platform: str) -> str`（`config_routes.py`），替换现有对 `jd_cookie`/`tb_cookie`/`pdd_cookie` 的假占位分支。原理：用 `httpx.AsyncClient`（`follow_redirects=True`）带上对应平台 Cookie 请求一个"必须登录才能访问"的页面，看最终落地 URL 的域名：

| 平台 | 探测 URL | 判定逻辑 |
|---|---|---|
| 京东（`jd_cookie`） | `https://order.jd.com/center/list.action` | 最终域名仍是 `order.jd.com` → `valid`；跳转到 `passport.jd.com`（登录页）→ `invalid`；其余异常 → 报错但不写 `invalid`（避免把网络问题误判成 Cookie 失效） |
| 淘宝/天猫（`tb_cookie`） | `https://member1.taobao.com/member/fresh/account_setting.htm` | 最终域名仍是 `member1.taobao.com` → `valid`；跳转到 `login.taobao.com` → `invalid` |
| 拼多多（`pdd_cookie`） | `https://mobile.yangkeduo.com/user_setting.html`（起始候选，若实现时发现该页面已失效/改版，换同类"账号设置类"页面，原理不变） | **best-effort**：只有明确重定向到登录域名时才判 `invalid`；请求被拦截/返回异常状态码/内容不含预期特征时，判定为 `unknown` 并在 `message` 里注明"无法判断，可能是反爬拦截而非 Cookie 失效"，不误报成 `invalid` |

不解析页面具体文本内容（改版即失效），只看最终落地域名——相对于内容解析更耐用，但仍可能随各平台的登录跳转策略变化而需要更新，属于已知的维护成本，在代码注释里明确写出（类似项目里对付反爬站的其它模块一样如实标注）。

`_MC_COOKIE_PLATFORM` 沿用现状不变（7 个社媒仍走 `_verify_mc_cookie` 的真实浏览器登录探测）。

### 4. 定时巡检调度器

新增 REGISTRY 键（沿用本次会话已用 3 次的"启停开关"模式，`runtime_config.py` 新分组 `cookie_health`）：

```python
{"key": "cookie_health", "label": "Cookie 健康巡检"},
```
```python
"cookie_health_scan_enabled": {
    "label": "启用定时巡检", "group": "cookie_health", "secret": False, "user": False,
    "type": "select", "choices": ["True", "False"],
},
"cookie_health_scan_interval_hours": {
    "label": "巡检间隔（小时）", "group": "cookie_health", "secret": False, "user": False,
},
```

`settings.py` 对应新增：
```python
cookie_health_scan_enabled: bool = Field(default=False, description="是否启用 Cookie 健康定时巡检")
cookie_health_scan_interval_hours: int = Field(default=24, description="巡检间隔（小时），默认每天一次")
```

新模块 `src/api/cookie_health_scanner.py`（命名与目录待 writing-plans 阶段按实际代码布局确认），风格对齐 `src/scheduler/service.py` 的 `SchedulerService`：一个 `CookieHealthScanner` 类，`start()`/`stop()`，内部 `asyncio.create_task` 跑一个 `while not self._stop.is_set()` 循环，每次醒来检查 `settings.cookie_health_scan_enabled`（热读，不需要重启即可生效开关切换）：
- 关闭：本轮跳过，按较短间隔（如 5 分钟）再检查一次开关状态，不做真实探测。
- 开启：距离上次全量扫描是否已超过 `cookie_health_scan_interval_hours`；到点则**顺序**（非并发）扫描全部 10 个 Cookie（7 社媒 + 3 电商），每项之间 sleep 5 秒，避免同一时刻集中触发多次浏览器自动化登录。每项复用 `_verify_mc_cookie`/`_verify_ecommerce_cookie` 的判定逻辑（提取成 `config_routes.py` 可供内部调用的函数，或适度抽到共享模块，供 API 路由和调度器共同引用，避免重复实现）。

`main.py` 的 `lifespan` 里新增 `start_cookie_health_scanner()`（与现有 `start_scheduler()` 并列调用），`settings.cookie_health_scan_enabled=False` 时循环仍然存在但只做轻量的开关检查，不产生真实探测开销。

### 5. API 与前端

`GET /api/config` 的 `describe()`（`runtime_config.py`）在返回每个 Cookie 项时，从 `cookie_health_all()` 查一下对应 key，追加：
```python
"health": {"status": "valid"|"invalid"|"unknown", "message": str, "checked_at": str, "checked_by": str} | None
```
无记录时为 `None`（从未验证）。

`AdminConfigCenter`（`ConfigCenter.tsx`）Cookie 行在"已覆盖/.env 兜底"徽章后追加一个状态徽章：
- `valid` → 绿色"有效 · {相对时间}"
- `invalid` → 红色"失效 · {相对时间}"
- `unknown`/无记录 → 灰色"从未验证"
- 淘宝/拼多多两项额外加一个小角标"best-effort"（`title` 提示"反爬较激进，结果仅供参考"）

`configGuides.ts` 的 Cookie 分组指南顺带补一句"定时巡检"开关的说明（沿用本次会话已建立的"面板全景合并展示、编辑弹窗内嵌单句说明"分工）。

### 6. 错误处理与隔离

- 单个 Cookie 的验证失败（无论手动还是定时）不应影响其它 Cookie 的验证——沿用现有 `try/except` 隔离风格（参考 `SchedulerService` 文档里"每个任务执行用 try/except 隔离"的原则）。
- `cookie_health` 表写入失败（如磁盘问题）不应导致验证请求本身报错给前端——旁路副作用，失败只记日志。
- 定时巡检的扫描循环本身若抛出未预期异常，需捕获并记日志后继续下一轮循环，不能让整个后台协程死掉。

## 验证

- 手动验证：管理员点击任意 Cookie 的"验证"，`GET /api/config` 里对应 key 的 `health` 字段应更新为本次结果，前端徽章刷新。
- 京东/淘宝/拼多多：用一个已知失效的 Cookie 手动验证，应正确判定为 `invalid`（而非之前的假"已保存"）；用有效 Cookie 验证应判定为 `valid`。
- 定时巡检：开启开关 + 把间隔调成很短的测试值，观察后台日志确认按间隔自动跑完 10 项且 `cookie_health` 表按 `checked_by="scheduled"` 更新；关闭开关后确认不再自动扫描。
- 拼多多 best-effort 场景：模拟请求被拦截（非明确登录跳转），确认判定落在 `unknown` 而非 `invalid`，且 `message` 说明是"无法判断"而非"已失效"。
