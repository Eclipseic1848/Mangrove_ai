# 凭证配置分类 + 使用指南 + 验证按钮可见性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 「我的凭证」（普通用户）按"模型 API Key"/"平台 Cookie"分类展示，两处配置中心（普通用户 + 管理员）都加「使用指南」入口，未配置项的验证按钮从"完全隐藏"改为"置灰+悬浮提示"。

**Architecture:** 新建一份纯数据文件（指南内容 + 分类顺序），新建一个纯展示组件（指南弹窗 + 编辑弹窗内嵌步骤），两处配置中心分别接入这两个新模块并调整自身的分组/按钮逻辑。后端 API 不变。

**Tech Stack:** React 18 + TypeScript + Tailwind（现有 `frontend/` 工程，沿用现有 `Modal`/`Button`/`Card` 基础组件）。

## Global Constraints

- 不改动后端：`src/api/routes/config_routes.py`、`src/config/runtime_config.py` 均不动，`/api/config/self`、`/api/config/verify` 接口不变。
- 指南内容只覆盖 `deepseek_api_key`、`qwen_api_key` 及 10 个平台 Cookie 键（`mc_cookie_xhs`/`mc_cookie_wb`/`mc_cookie_dy`/`mc_cookie_bili`/`mc_cookie_zhihu`/`mc_cookie_ks`/`mc_cookie_tieba`/`jd_cookie`/`tb_cookie`/`pdd_cookie`）；其余 key（邮件/Slack/MySQL/代理池/语义召回/搜索服务等）不写指南，`getGuideForKey` 对这些 key 返回 `null` 时调用方不渲染任何占位内容。
- 不改动 `AdminConfigCenter` 现有的手风琴分组结构（按 `group` 折叠）与验证按钮显示逻辑（本来就不依赖"是否已配置"）——只加"使用指南"入口和编辑弹窗内嵌步骤。
- 指南里的链接域名固定为：`platform.deepseek.com`、`bailian.console.aliyun.com`，以及 10 个平台的官方域名（小红书 `xiaohongshu.com`、微博 `weibo.com`、抖音 `douyin.com`、B 站 `bilibili.com`、知乎 `zhihu.com`、快手 `kuaishou.com`、贴吧 `tieba.baidu.com`、京东 `jd.com`、淘宝 `taobao.com`、拼多多 `pinduoduo.com`）。文案措辞可以微调，但域名以此为准。
- 不新增 npm 依赖（现有 `lucide-react` 图标库已够用）。
- 中文注释，文件 UTF-8 保存（项目全局约定）。
- 本项目前端没有单元测试框架（`frontend/package.json` 无 vitest/jest），验证手段统一为 `npm run build`（`tsc --noEmit && vite build`）+ 手动浏览器验证，不要求也不要新增测试框架。

---

### Task 1: 指南内容数据文件

**Files:**
- Create: `frontend/src/lib/configGuides.ts`

**Interfaces:**
- Produces:
  - `interface GuideStep { text: string; link?: { label: string; url: string } }`
  - `interface GuideEntry { key: string; title: string; steps: GuideStep[] }`
  - `interface GuideSection { key: "model" | "cookie"; title: string; entries: GuideEntry[] }`
  - `export const CONFIG_GUIDE_SECTIONS: GuideSection[]`
  - `export const MODEL_KEY_ORDER: string[]`（固定为 `["deepseek_api_key", "qwen_api_key"]`）
  - `export const COOKIE_KEY_ORDER: string[]`（固定为 10 个 Cookie key，顺序见下）
  - `export function getGuideForKey(key: string): GuideEntry | null`

- [ ] **Step 1: 创建 `frontend/src/lib/configGuides.ts`**

```ts
/** 凭证配置指南内容：普通用户「我的凭证」与管理员「配置中心」的"使用指南"共用同一份数据。 */

export interface GuideStep {
  text: string;
  link?: { label: string; url: string };
}

export interface GuideEntry {
  key: string;
  title: string;
  steps: GuideStep[];
}

export interface GuideSection {
  key: "model" | "cookie";
  title: string;
  entries: GuideEntry[];
}

/** 模型 API Key 分类固定顺序（不按字母序，DeepSeek 与百炼放一起）。 */
export const MODEL_KEY_ORDER = ["deepseek_api_key", "qwen_api_key"];

/** 平台 Cookie 分类固定顺序，与 Dashboard.tsx 的 COOKIE_CN 顺序保持一致。 */
export const COOKIE_KEY_ORDER = [
  "mc_cookie_xhs", "mc_cookie_wb", "mc_cookie_dy", "mc_cookie_bili", "mc_cookie_zhihu",
  "mc_cookie_ks", "mc_cookie_tieba", "jd_cookie", "tb_cookie", "pdd_cookie",
];

const COOKIE_LOGIN_LINKS: Record<string, { label: string; url: string }> = {
  mc_cookie_xhs: { label: "小红书网页版", url: "https://www.xiaohongshu.com" },
  mc_cookie_wb: { label: "微博网页版", url: "https://weibo.com" },
  mc_cookie_dy: { label: "抖音网页版", url: "https://www.douyin.com" },
  mc_cookie_bili: { label: "B站", url: "https://www.bilibili.com" },
  mc_cookie_zhihu: { label: "知乎", url: "https://www.zhihu.com" },
  mc_cookie_ks: { label: "快手网页版", url: "https://www.kuaishou.com" },
  mc_cookie_tieba: { label: "百度贴吧", url: "https://tieba.baidu.com" },
  jd_cookie: { label: "京东", url: "https://www.jd.com" },
  tb_cookie: { label: "淘宝", url: "https://www.taobao.com" },
  pdd_cookie: { label: "拼多多", url: "https://www.pinduoduo.com" },
};

const COOKIE_LABELS: Record<string, string> = {
  mc_cookie_xhs: "小红书 Cookie", mc_cookie_wb: "微博 Cookie", mc_cookie_dy: "抖音 Cookie",
  mc_cookie_bili: "B站 Cookie", mc_cookie_zhihu: "知乎 Cookie", mc_cookie_ks: "快手 Cookie",
  mc_cookie_tieba: "贴吧 Cookie", jd_cookie: "京东 Cookie", tb_cookie: "淘宝/天猫 Cookie",
  pdd_cookie: "拼多多 Cookie",
};

/** 10 个平台共用同一套通用获取步骤，仅登录链接不同。 */
function cookieSteps(key: string): GuideStep[] {
  return [
    { text: "用浏览器打开该平台网页版，登录你自己的账号", link: COOKIE_LOGIN_LINKS[key] },
    { text: "按 F12 打开浏览器开发者工具，切换到「网络 / Network」面板，刷新一次页面" },
    { text: "任选一条请求，在右侧「标头 / Headers」里找到 Cookie 字段，复制完整内容" },
    { text: "粘贴到本页对应输入框保存" },
  ];
}

const MODEL_ENTRIES: GuideEntry[] = [
  {
    key: "deepseek_api_key",
    title: "DeepSeek API Key",
    steps: [
      {
        text: "打开 DeepSeek 开放平台，注册或登录账号",
        link: { label: "platform.deepseek.com", url: "https://platform.deepseek.com" },
      },
      { text: "左侧菜单进入「API keys」" },
      { text: "点击「创建 API key」，复制生成的 Key（只显示一次，务必当场复制）" },
      { text: "回到本页粘贴保存" },
    ],
  },
  {
    key: "qwen_api_key",
    title: "百炼 API Key",
    steps: [
      {
        text: "打开阿里云百炼控制台，登录阿里云账号",
        link: { label: "bailian.console.aliyun.com", url: "https://bailian.console.aliyun.com" },
      },
      { text: "进入「API-KEY 管理」" },
      { text: "点击「创建新的 API-KEY」，复制生成的 Key" },
      { text: "回到本页粘贴保存" },
    ],
  },
];

const COOKIE_ENTRIES: GuideEntry[] = COOKIE_KEY_ORDER.map((key) => ({
  key,
  title: COOKIE_LABELS[key],
  steps: cookieSteps(key),
}));

export const CONFIG_GUIDE_SECTIONS: GuideSection[] = [
  { key: "model", title: "模型 API Key", entries: MODEL_ENTRIES },
  { key: "cookie", title: "平台 / 网站 Cookie", entries: COOKIE_ENTRIES },
];

const GUIDE_BY_KEY: Record<string, GuideEntry> = Object.fromEntries(
  CONFIG_GUIDE_SECTIONS.flatMap((s) => s.entries).map((e) => [e.key, e]),
);

/** 按 config key 取该项的指南步骤；找不到返回 null（不是所有 key 都有指南，如邮件/MySQL 等）。 */
export function getGuideForKey(key: string): GuideEntry | null {
  return GUIDE_BY_KEY[key] || null;
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无报错（此时该文件尚未被任何地方引用，仅检查自身语法/类型正确）

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/configGuides.ts
git commit -m "feat: 新增凭证配置指南内容数据（模型Key+平台Cookie获取步骤）"
```

---

### Task 2: 指南展示组件

**Files:**
- Create: `frontend/src/components/ConfigGuideModal.tsx`

**Interfaces:**
- Consumes: `CONFIG_GUIDE_SECTIONS`, `GuideEntry`, `GuideStep`, `getGuideForKey` from `@/lib/configGuides`（Task 1 产出）；`Modal` from `@/components/ui/modal`
- Produces:
  - `export function ConfigGuideModal({ open, onClose }: { open: boolean; onClose: () => void }): JSX.Element`
  - `export function GuideStepsInline({ configKey }: { configKey: string }): JSX.Element | null`

- [ ] **Step 1: 创建 `frontend/src/components/ConfigGuideModal.tsx`**

```tsx
import { Modal } from "@/components/ui/modal";
import { CONFIG_GUIDE_SECTIONS, getGuideForKey, type GuideStep } from "@/lib/configGuides";

/** 渲染一组有序步骤；每步可带一个跳转链接（如平台登录页/官网）。 */
function GuideStepsList({ steps }: { steps: GuideStep[] }) {
  return (
    <ol className="list-decimal space-y-1 pl-4 text-xs text-muted-foreground">
      {steps.map((step, i) => (
        <li key={i}>
          {step.text}
          {step.link && (
            <>
              {"："}
              <a
                href={step.link.url}
                target="_blank"
                rel="noreferrer"
                className="text-primary underline underline-offset-2"
              >
                {step.link.label}
              </a>
            </>
          )}
        </li>
      ))}
    </ol>
  );
}

/** 凭证配置指南面板：按"模型 API Key"/"平台 Cookie"分类展示怎么获取，普通用户与管理员两处配置中心共用。 */
export function ConfigGuideModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="凭证配置指南" wide>
      <div className="max-h-[70vh] space-y-5 overflow-y-auto pr-1">
        {CONFIG_GUIDE_SECTIONS.map((section) => (
          <div key={section.key}>
            <h4 className="mb-2 text-sm font-semibold text-foreground">{section.title}</h4>
            <div className="space-y-3">
              {section.entries.map((entry) => (
                <div key={entry.key} className="rounded-md border border-border/60 p-3">
                  <div className="mb-1.5 text-sm font-medium">{entry.title}</div>
                  <GuideStepsList steps={entry.steps} />
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}

/** 编辑弹窗内嵌的精简步骤展示；该 key 没有对应指南时返回 null，不占位。 */
export function GuideStepsInline({ configKey }: { configKey: string }) {
  const entry = getGuideForKey(configKey);
  if (!entry) return null;
  return (
    <div className="mb-3 rounded-md border border-border/60 bg-muted/40 p-3">
      <GuideStepsList steps={entry.steps} />
    </div>
  );
}
```

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无报错

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ConfigGuideModal.tsx
git commit -m "feat: 新增凭证配置指南展示组件（面板+编辑弹窗内嵌步骤）"
```

---

### Task 3: 「我的凭证」（SelfConfigCenter）分类展示 + 验证按钮 + 指南入口

**Files:**
- Modify: `frontend/src/components/ConfigCenter.tsx`（`SelfConfigCenter` 函数，约第 344-446 行）

**Interfaces:**
- Consumes: `MODEL_KEY_ORDER`, `COOKIE_KEY_ORDER` from `@/lib/configGuides`；`ConfigGuideModal`, `GuideStepsInline` from `@/components/ConfigGuideModal`（均为 Task 1/2 产出）
- Produces: 无新增导出（`SelfConfigCenter` 对外签名不变）

- [ ] **Step 1: 修改顶部 import**

在文件顶部的 lucide-react 导入行里加入 `BookOpen`：

```tsx
import { ChevronDown, ChevronRight, Loader2, Pencil, Play, RotateCcw, Trash2 } from "lucide-react";
```
改为：
```tsx
import { BookOpen, ChevronDown, ChevronRight, Loader2, Pencil, Play, RotateCcw, Trash2 } from "lucide-react";
```

在 `import { api } from "@/lib/api";` 之后新增两行：

```tsx
import { ConfigGuideModal, GuideStepsInline } from "@/components/ConfigGuideModal";
import { COOKIE_KEY_ORDER, MODEL_KEY_ORDER } from "@/lib/configGuides";
```

- [ ] **Step 2: `SelfConfigCenter` 函数体改造**

把整个 `export function SelfConfigCenter() { ... }`（当前第 344-446 行）替换为：

```tsx
/** 普通用户：我的凭证（模型 API Key / 平台 Cookie，仅对自己的任务生效）。 */
export function SelfConfigCenter() {
  const [items, setItems] = useState<CfgItem[]>([]);
  const [edit, setEdit] = useState<CfgItem | null>(null);
  const [val, setVal] = useState("");
  const [guideOpen, setGuideOpen] = useState(false);
  const { verifying, run } = useVerify();
  const [confirmTarget, setConfirmTarget] = useState<string | null>(null);

  const runOrConfirm = (target: string) => {
    if (isSlowVerifyTarget(target)) setConfirmTarget(target);
    else run(target);
  };

  const load = () => api.get("/api/config/self").then((d) => setItems(d.items || [])).catch(() => {});
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!edit) return;
    try {
      await api.put(`/api/config/self/${edit.key}`, { value: val });
      toast.success(`${edit.label} 已保存，之后你的任务将优先使用它`);
      setEdit(null);
      load();
    } catch (e: any) {
      toast.error(e.message || "保存失败");
    }
  };

  const del = async (it: CfgItem) => {
    try {
      await api.del(`/api/config/self/${it.key}`);
      toast.success(`${it.label} 已清除，回落系统默认配置`);
      load();
    } catch (e: any) {
      toast.error(e.message || "清除失败");
    }
  };

  /** 按 key 分类固定顺序取出对应项；用户尚未加载完成或该 key 不在返回列表里时自然跳过。 */
  const byKey = new Map(items.map((it) => [it.key, it]));
  const pick = (order: string[]) =>
    order.map((k) => byKey.get(k)).filter((it): it is CfgItem => !!it);
  const modelItems = pick(MODEL_KEY_ORDER);
  const cookieItems = pick(COOKIE_KEY_ORDER);

  const renderItem = (it: CfgItem) => {
    const target = VERIFY_TARGET[it.key] || it.key;
    return (
      <div key={it.key} className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1 text-sm">
        <span className="min-w-[150px]">{it.label}</span>
        <code className="max-w-[220px] truncate rounded bg-muted px-1.5 py-0.5 text-xs">
          {it.set ? it.value : "（未配置，用系统默认）"}
        </code>
        <span className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" className="h-7 gap-1 px-2"
            onClick={() => { setEdit(it); setVal(""); }}>
            <Pencil className="h-3.5 w-3.5" /> {it.set ? "修改" : "配置"}
          </Button>
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
            {verifying === target
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Play className="h-3.5 w-3.5" />} 验证
          </Button>
        </span>
      </div>
    );
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">我的凭证</CardTitle>
          <Button variant="outline" size="sm" className="h-7 gap-1.5" onClick={() => setGuideOpen(true)}>
            <BookOpen className="h-3.5 w-3.5" /> 使用指南
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          自己的模型 API Key 与平台 Cookie（有账号的话），只对你发起的任务生效；未配置时用系统默认。
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <div className="mb-1 text-xs text-muted-foreground">模型 API Key</div>
          <div className="space-y-1">{modelItems.map(renderItem)}</div>
        </div>
        <div className="border-t border-border/40 pt-3">
          <div className="mb-1 text-xs text-muted-foreground">平台 / 网站 Cookie</div>
          <div className="space-y-1">{cookieItems.map(renderItem)}</div>
        </div>
      </CardContent>

      <Modal open={!!edit} onClose={() => setEdit(null)} title={`${edit?.set ? "修改" : "配置"} · ${edit?.label ?? ""}`}>
        {edit && <GuideStepsInline configKey={edit.key} />}
        <Input placeholder={edit?.key.includes("cookie") ? "粘贴从浏览器导出的 Cookie" : "输入 API Key"}
          value={val} onChange={(e) => setVal(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()} />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setEdit(null)}>取消</Button>
          <Button size="sm" disabled={!val.trim()} onClick={save}>保存</Button>
        </div>
      </Modal>

      {/* 慢速/副作用验证确认（Cookie 真实登录探测等） */}
      <Modal open={!!confirmTarget} onClose={() => setConfirmTarget(null)} title="确认验证">
        <p className="text-sm text-muted-foreground">
          {confirmTarget && slowVerifyConfirmText(confirmTarget)} 确定继续？
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setConfirmTarget(null)}>取消</Button>
          <Button size="sm" onClick={() => { const t = confirmTarget; setConfirmTarget(null); if (t) run(t); }}>
            开始验证
          </Button>
        </div>
      </Modal>

      <ConfigGuideModal open={guideOpen} onClose={() => setGuideOpen(false)} />
    </Card>
  );
}
```

- [ ] **Step 3: 类型检查 + 构建**

Run: `cd frontend && npm run build`
Expected: `tsc --noEmit && vite build` 无报错，`dist/` 正常产出

- [ ] **Step 4: 手动验证**

启动前端 dev server（`npm run dev`）或使用刚才的构建产物，用普通用户账号登录，进入「设置」页确认：
1. 「我的凭证」显示"模型 API Key"（DeepSeek、百炼两项）与"平台 / 网站 Cookie"（10 项，顺序为 小红书→微博→抖音→B站→知乎→快手→贴吧→京东→淘宝/天猫→拼多多）两个分区
2. 未配置项的「验证」按钮呈灰色不可点，鼠标悬浮显示"先配置才能验证"
3. 点击标题栏「使用指南」，弹窗展示模型 Key + Cookie 两类获取步骤，链接可点击跳转
4. 点击任意一项「配置」，弹窗输入框上方展示该项对应的获取步骤

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ConfigCenter.tsx
git commit -m "feat: 我的凭证按模型Key/平台Cookie分类展示+验证按钮置灰+接入使用指南"
```

---

### Task 4: 管理员配置中心（AdminConfigCenter）接入使用指南

**Files:**
- Modify: `frontend/src/components/ConfigCenter.tsx`（`AdminConfigCenter` 函数，约第 86-341 行，此时因 Task 3 的改动行号已下移，以代码内容匹配为准）

**Interfaces:**
- Consumes: `ConfigGuideModal`, `GuideStepsInline` from `@/components/ConfigGuideModal`（Task 2 产出，Task 3 已在同一文件顶部导入，本任务直接复用该 import，无需重复添加）
- Produces: 无新增导出（`AdminConfigCenter` 对外签名不变）

- [ ] **Step 1: 新增 `guideOpen` state**

在 `AdminConfigCenter` 函数体内，`const [confirmTarget, setConfirmTarget] = useState<string | null>(null);`（待确认的慢速/副作用验证目标）这一行之后新增：

```tsx
const [guideOpen, setGuideOpen] = useState(false);
```

- [ ] **Step 2: 标题栏加「使用指南」按钮**

把：

```tsx
      <CardHeader>
        <CardTitle className="text-base">配置中心（全局，改完即时生效）</CardTitle>
        <p className="text-xs text-muted-foreground">
          密钥/Cookie/端点等运行时可改；「已覆盖」为前端保存的值，「.env 兜底」为服务器默认。重置即恢复默认，不影响 Agent 运行。
        </p>
      </CardHeader>
```

改为：

```tsx
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">配置中心（全局，改完即时生效）</CardTitle>
          <Button variant="outline" size="sm" className="h-7 gap-1.5" onClick={() => setGuideOpen(true)}>
            <BookOpen className="h-3.5 w-3.5" /> 使用指南
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          密钥/Cookie/端点等运行时可改；「已覆盖」为前端保存的值，「.env 兜底」为服务器默认。重置即恢复默认，不影响 Agent 运行。
        </p>
      </CardHeader>
```

- [ ] **Step 3: 编辑弹窗内嵌步骤**

把"普通项 / 单一下拉项"分支（`llm_default_provider` 级联分支的 `else` 部分）：

```tsx
        ) : (
          /* ──── 普通项 / 单一下拉项 ──── */
          <>
            <p className="mb-2 text-xs text-muted-foreground">
              当前值：{edit?.value || "（未配置）"}（{edit?.source === "override" ? "前端覆盖" : ".env 兜底"}）
              {edit?.secret && "；密钥仅显示尾 4 位，输入新值将完整替换"}
            </p>
            {edit?.type === "select" ? (
```

改为：

```tsx
        ) : (
          /* ──── 普通项 / 单一下拉项 ──── */
          <>
            <p className="mb-2 text-xs text-muted-foreground">
              当前值：{edit?.value || "（未配置）"}（{edit?.source === "override" ? "前端覆盖" : ".env 兜底"}）
              {edit?.secret && "；密钥仅显示尾 4 位，输入新值将完整替换"}
            </p>
            {edit && <GuideStepsInline configKey={edit.key} />}
            {edit?.type === "select" ? (
```

- [ ] **Step 4: 挂载指南面板**

在 `AdminConfigCenter` 返回的 JSX 末尾——即「慢速/副作用验证确认」`<Modal>`（`title="确认验证"`）之后、`</Card>` 之前——新增：

```tsx
      <ConfigGuideModal open={guideOpen} onClose={() => setGuideOpen(false)} />
```

即该函数末尾变为：

```tsx
      {/* 慢速/副作用验证确认（Slack 发消息、Cookie 真实登录探测） */}
      <Modal open={!!confirmTarget} onClose={() => setConfirmTarget(null)} title="确认验证">
        <p className="text-sm text-muted-foreground">
          {confirmTarget && slowVerifyConfirmText(confirmTarget)} 确定继续？
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setConfirmTarget(null)}>取消</Button>
          <Button size="sm" onClick={() => { const t = confirmTarget; setConfirmTarget(null); if (t) run(t); }}>
            开始验证
          </Button>
        </div>
      </Modal>

      <ConfigGuideModal open={guideOpen} onClose={() => setGuideOpen(false)} />
    </Card>
  );
}
```

- [ ] **Step 5: 类型检查 + 构建**

Run: `cd frontend && npm run build`
Expected: `tsc --noEmit && vite build` 无报错，`dist/` 正常产出

- [ ] **Step 6: 手动验证**

用超管或管理员账号登录，进入「设置」页确认：
1. 「配置中心（全局，改完即时生效）」标题栏出现「使用指南」按钮，点击后弹窗内容与普通用户看到的一致（模型 Key + Cookie 两类，不含邮件/Slack/MySQL 等）
2. 展开任意分组（如"模型 · DeepSeek"），点击某一项「修改」，弹窗输入框上方能看到对应获取步骤（若该项不在指南范围内，如 SMTP 相关项，则不显示任何步骤区块，也不留空白）
3. 现有分组折叠、验证按钮行为均无变化

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ConfigCenter.tsx
git commit -m "feat: 管理员配置中心接入使用指南入口"
```
