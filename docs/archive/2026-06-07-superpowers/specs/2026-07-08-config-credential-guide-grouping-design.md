# 凭证配置分类 + 使用指南 + 验证按钮可见性 设计文档

## 背景

普通用户在「设置」页的「我的凭证」（`SelfConfigCenter`，`frontend/src/components/ConfigCenter.tsx`）里配置自己的模型 API Key 和平台 Cookie。现状三个问题：

1. **列表未分类**：`GET /api/config/self` 按 key 字母序返回（`config_routes.py:105` `sorted(rc.USER_KEYS)`），导致「百炼 API Key」（`qwen_api_key`）排在倒数第二行，和「DeepSeek API Key」离得很远，用户看不出两者同属"模型"类。10 个平台 Cookie 也混杂在模型项之间。
2. **不懂技术的用户不知道怎么配置**：每一项只有一个输入框，没有任何"去哪里拿这个值""怎么操作"的说明。
3. **验证按钮"消失"**：`ConfigCenter.tsx:403-414` 的验证按钮包在 `{it.set && (...)}` 里，未配置的项完全不渲染验证按钮，容易被误认为功能缺失（本次即由此触发）。

超管/管理员的全局配置中心（`AdminConfigCenter`）已经按 `group` 分组（手风琴折叠，标题带"模型·DeepSeek"这类前缀），验证按钮也不受"是否已配置"限制——这两点不需要改；但同样缺少"怎么配置"的说明，本次一并补上入口（复用同一份内容，只覆盖模型 Key + Cookie 两类）。

## 目标

- 「我的凭证」列表按"模型 API Key" / "平台·网站 Cookie" 两个分区展示，分区内顺序固定（不再按字母序）。
- 两处配置中心（普通用户 `SelfConfigCenter`、管理员 `AdminConfigCenter`）标题栏都加「使用指南」入口，点开后按分类展示"怎么获取/怎么配置"。
- 每一项的编辑弹窗里，也展示这一项对应的获取步骤（不用去指南面板里再找）。
- 未配置的项，验证按钮改为"置灰不可点 + 悬浮提示"，而不是完全不显示。

## 非目标

- 不改动后端 API（`/api/config/self`、`/api/config/verify` 均不变）。
- 不给邮件 SMTP / Slack / MySQL / 代理池 / 语义召回等"给技术管理员用"的配置项写指南内容。
- 不改动 `AdminConfigCenter` 现有的手风琴分组结构、验证按钮逻辑（这两点本来就没问题）。

## 设计

### 1. 指南内容：`frontend/src/lib/configGuides.ts`（新建）

单一数据源，两处入口共用（顶部指南面板 + 每项编辑弹窗）。导出：

```ts
export interface GuideStep {
  text: string;              // 步骤文字
  link?: { label: string; url: string };  // 可选：本步骤对应的跳转链接
}
export interface GuideEntry {
  key: string;                // 对应 config key，如 "deepseek_api_key"
  title: string;              // 展示标题，如 "DeepSeek API Key"
  steps: GuideStep[];
}
export interface GuideSection {
  key: "model" | "cookie";
  title: string;               // "模型 API Key" | "平台 / 网站 Cookie"
  entries: GuideEntry[];
}

export const CONFIG_GUIDE_SECTIONS: GuideSection[];

/** 按 config key 取该项的指南步骤；找不到返回 null（不是所有 key 都有指南，如邮件/MySQL 等）。 */
export function getGuideForKey(key: string): GuideEntry | null;
```

内容（首版文案，允许后续微调措辞，但结构和链接域名以此为准）：

- **模型 API Key**
  - `deepseek_api_key`（DeepSeek API Key）：
    1. 打开 [platform.deepseek.com](https://platform.deepseek.com)，注册或登录账号
    2. 左侧菜单进入「API keys」
    3. 点击「创建 API key」，复制生成的 Key（只显示一次，务必当场复制）
    4. 回到本页粘贴保存
  - `qwen_api_key`（百炼 API Key）：
    1. 打开[阿里云百炼控制台](https://bailian.console.aliyun.com)，登录阿里云账号
    2. 进入「API-KEY 管理」
    3. 点击「创建新的 API-KEY」，复制生成的 Key
    4. 回到本页粘贴保存
- **平台 / 网站 Cookie**（10 项共享同一套通用步骤，每项附各自登录页链接）：
  1. 用浏览器打开该平台的网页版并登录你自己的账号（链接见下）
  2. 按 `F12` 打开浏览器开发者工具，切换到「网络 / Network」面板，刷新一次页面
  3. 任选一条请求，在右侧「标头 / Headers」里找到 `Cookie` 字段，复制完整内容
  4. 粘贴到本页对应输入框保存
  - 平台登录页链接：小红书 xiaohongshu.com、微博 weibo.com、抖音 douyin.com、B 站 bilibili.com、知乎 zhihu.com、快手 kuaishou.com、贴吧 tieba.baidu.com、京东 jd.com、淘宝/天猫 taobao.com、拼多多 pinduoduo.com（对应 key：`mc_cookie_xhs`/`mc_cookie_wb`/`mc_cookie_dy`/`mc_cookie_bili`/`mc_cookie_zhihu`/`mc_cookie_ks`/`mc_cookie_tieba`/`jd_cookie`/`tb_cookie`/`pdd_cookie`）

### 2. 指南面板组件：`frontend/src/components/ConfigGuideModal.tsx`（新建）

```tsx
export function ConfigGuideModal({ open, onClose }: { open: boolean; onClose: () => void }): JSX.Element
```

- 复用现有 `Modal` 组件（`@/components/ui/modal`），标题「凭证配置指南」。
- 内容：遍历 `CONFIG_GUIDE_SECTIONS`，每个 section 一个小标题 + 其下 `entries` 依次展示（标题 + 有序步骤列表），步骤里的 `link` 渲染为 `<a target="_blank" rel="noreferrer">`。
- 纯展示组件，不管理自己的开关状态（`open`/`onClose` 由调用方传入），两处调用方各自维护一个 `guideOpen` 的 `useState`。

### 3. `SelfConfigCenter` 改动（`frontend/src/components/ConfigCenter.tsx`）

**分类展示**：新增两个常量控制分组与顺序（不依赖后端返回顺序）：

```ts
const MODEL_KEY_ORDER = ["deepseek_api_key", "qwen_api_key"];
const COOKIE_KEY_ORDER = [
  "mc_cookie_xhs", "mc_cookie_wb", "mc_cookie_dy", "mc_cookie_bili", "mc_cookie_zhihu",
  "mc_cookie_ks", "mc_cookie_tieba", "jd_cookie", "tb_cookie", "pdd_cookie",
]; // 与 Dashboard.tsx 的 COOKIE_CN 顺序保持一致
```

`items`（接口返回）按上述两个顺序数组 `map` 出「模型 API Key」「平台 / 网站 Cookie」两个分区，各自渲染为一个小节（子标题 + 列表），不做手风琴折叠（沿用 `Dashboard.tsx` 里"搜索后端（N个）"子标题的既有写法，同一个 Card 内两个 `<div>` 分区）。

**标题栏加指南入口**：

```tsx
<CardHeader>
  <div className="flex items-center justify-between gap-2">
    <CardTitle className="text-base">我的凭证</CardTitle>
    <Button variant="outline" size="sm" className="h-7 gap-1.5" onClick={() => setGuideOpen(true)}>
      <BookOpen className="h-3.5 w-3.5" /> 使用指南
    </Button>
  </div>
  <p className="text-xs text-muted-foreground">...原文案...</p>
</CardHeader>
```

**验证按钮可见性**：把现有

```tsx
{it.set && (
  <>
    <Button ...清除.../>
    <Button ...验证.../>
  </>
)}
```

改为「清除」仍只在 `it.set` 时渲染，「验证」始终渲染但按 `it.set` 控制 `disabled` 与 `title`：

```tsx
{it.set && (
  <Button variant="ghost" size="sm" className="h-7 gap-1 px-2" onClick={() => del(it)}>
    <Trash2 className="h-3.5 w-3.5" /> 清除
  </Button>
)}
<Button
  variant="ghost" size="sm" className="h-7 gap-1 px-2"
  disabled={!it.set || verifying === target}
  title={!it.set ? "先配置才能验证" : undefined}
  onClick={() => runOrConfirm(target)}
>
  {verifying === target ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />} 验证
</Button>
```

**编辑弹窗加对应步骤**：在现有编辑 `Modal`（`edit?.set ? "修改" : "配置"`）的 `<Input>` 上方，用 `getGuideForKey(edit.key)` 取步骤，有则展示一个简短的有序列表（无则不展示任何东西，不留空白）。

### 4. `AdminConfigCenter` 改动

- 标题栏同样加「使用指南」按钮（复用同一个 `ConfigGuideModal`），位置在现有 `CardTitle` 同一行右侧，写法与 `SelfConfigCenter` 一致。
- 编辑弹窗（`edit?.key === "llm_default_provider"` 分支之外的"普通项"分支）同样用 `getGuideForKey(edit.key)` 在 `<Input>`/`<select>` 上方插入步骤展示。
- 分组手风琴结构、验证按钮逻辑均不改动。

## 验证

- `npm run build`（`tsc --noEmit && vite build`）通过。
- 手动验证（本地起服务，用普通用户账号登录设置页）：
  1. 「我的凭证」列表显示"模型 API Key"（DeepSeek、百炼两项，顺序固定）和"平台 / 网站 Cookie"（10 项，顺序与概览页一致）两个分区
  2. 未配置项：验证按钮灰色不可点，鼠标悬浮显示"先配置才能验证"
  3. 点击"配置"打开编辑弹窗，输入框上方能看到对应获取步骤（含可点击链接）
  4. 点击标题栏"使用指南"，弹窗展示完整分类内容
  5. 管理员账号登录设置页，确认全局配置中心标题栏也有"使用指南"入口，内容与普通用户一致（仅模型 Key + Cookie 两类）
