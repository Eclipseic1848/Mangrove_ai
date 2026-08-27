---
version: 1
name: "Mangrove"
description: "面向数据任务的可信工作台，以红树林青和证据接收单表达可追溯、克制而可靠的操作感。"
colors:
  primary: "#0E7C6F"
  accent: "#E5FAF8"
  background: "#FCFDFD"
  foreground: "#1D262A"
  danger: "#D32222"
  dark-background: "#0E1316"
  dark-primary: "#20C5AF"
typography:
  sans:
    fontFamily: "ui-sans-serif, system-ui, sans-serif"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"
rounded:
  DEFAULT: "0.65rem"
  sm: "0.25rem"
  md: "0.5rem"
  lg: "0.75rem"
spacing:
  section-gap: "3rem"
  page-max: "64rem"
components:
  button: {}
  input: {}
  dialog: {}
  source-intake: {}
---

# Mangrove Design System

## Overview

### Creative North Star

Mangrove 像一张清楚填写、逐项盖章的资料接收单：用户先看见来源、范围和用途，再看到读取时间、
内容摘要与结果。表达来自可核验的工作痕迹，而不是装饰性插画、渐变或成排仪表盘卡片。

### Product context and register

- **Audience and primary job：** 需要从文件、网页和其他数据中得到可核验结果的中文用户。
- **Target market(s) and evidence：** 当前产品与仓库文案面向中文业务用户；不据此推断更细市场。
- **Locale(s) and language policy：** 产品界面使用简体中文；技术身份、网址、摘要和格式名保留原文。
- **Usage scene：** 桌面端承担高密度资料核对；窄屏仍必须能完成来源输入、状态查看与恢复。
- **Register：** 工作台优先，品牌表达只用于主色、标识和少量关键状态。
- **Memorable signature：** 来源接收单把“允许范围—用途—冻结证据”组成一条连续阅读节奏。
- **Restraint：** 表单、任务状态、失败恢复和正式交付遵循熟悉控件，不追求新奇交互。
- **Anti-references：** 不做通用 SaaS 卡片墙、AI 落地页、彩虹渐变或全屏 conversation-first 布局。
- **Token ownership/runtime mapping：** 本文件镜像而不生成运行时 Token；权威实现位于
  `frontend/src/index.css` 的 HSL 变量，并由 Tailwind 配置和 `frontend/src/components/ui/` 消费。

## Colors

红树林青 `primary` 是唯一品牌主色，浅青 `accent` 只承载选择和低强度提示。背景保持近白或冷黑，
错误使用独立的 `danger`。成功、警告和错误必须同时有文字或图标，不能只靠颜色。焦点环使用
运行时 `--ring`，深色主题映射到 `dark-primary`，不为单页复制一套色值。

## Typography

沿用项目现有、覆盖中文字符的 sans 栈；本阶段不引入网络字体或新字体依赖。正文保持自然句式，
按钮使用动词。网址、SHA-256 和协议身份使用 mono 栈；不对中文使用全大写、斜体或过密字距。

## Layout

主工作台内容最大宽度沿用现有 `max-w-5xl`，关键输入收敛到 `max-w-3xl`。来源、用途、范围、状态
和预览按纵向证据顺序排列。桌面保留全局导航与任务侧栏；`/data-prep` 在小于 `md` 时隐藏两层
侧栏，使当前任务内容占满视口。三列引导与示例在窄屏变为单列。异步切换不得改变主要按钮宽度。

## Elevation & Depth

默认以边框、背景层级和留白分组。仅主要输入容器使用现有低强度长阴影；状态回执、预览和元数据
通过分隔线组织，不额外套多层卡片。弹窗才使用遮罩和高层阴影。

## Shapes

控件和局部分组使用 8–12px 圆角，主输入容器使用现有 16px 圆角。胶囊形只用于短标签或筛选，
不用于长按钮和内容容器。Lucide 图标保持细描边，图标底只用于关键来源或状态。

## Components

### Foundational visual states

所有可操作控件具有 `focus-visible` 环；disabled 同时改变鼠标和透明度；busy 保持原几何并显示
Lucide Loader，且在 `prefers-reduced-motion` 下停止旋转。成功、失败、取消和恢复状态必须有明确
标题、原因与下一步，刷新后从持久事实恢复。

### Buttons and actions

每个区域只有一个主操作。次要操作使用边框或文本样式；破坏性操作使用 danger 且与主操作分开。
图标放在文字左侧，纯图标按钮必须有可访问名称。

### Navigation and data display

来源类型使用原生 radio 语义配合可见标签；当前选择用浅青底和主色文字表达。任务列表保持现有
侧栏所有权。来源元数据用 definition list，正文预览使用 article，不把事实拆成卡片网格。

### Forms and overlays

输入必须有常驻 label；placeholder 只提供格式示例。URL 在提交前显示规范化结果、范围和用途。
错误在相关区域内说明，并可由 Toast 补充操作反馈。Dialog 继续由 Radix 作为规范所有者。

### Iconography

统一使用 Lucide 16–20px 描边图标。图标只帮助扫描来源、范围和状态；关键操作始终保留文字。

### Motion

交互反馈以 150–300ms 的颜色、焦点和轻量旋转为主，只解释状态变化。不使用入场编舞、悬浮位移
堆叠或无意义循环动效；所有旋转支持 reduced motion。

### Content and data visualization

文案先说事实，再说限制和后续动作。对未知、失败和零结果使用具体原因，不用“出错了”等模糊
措辞。时间按用户区域显示，字节按可读单位显示，完整 SHA-256 保留在可访问 title 或详情中。

## Do's and Don'ts

- **Do：** 启动前把来源、范围、用途与可能外发内容放在同一阅读路径。
- **Do：** 保留文件、预览、任务列表和正式交付的既有入口与组件所有权。
- **Don't：** 用 Agent 成功、Candidate 或来源获取成功冒充正式交付。
- **Don't：** 为单一来源增加新 UI 依赖、独立主题、通用卡片墙或平行报告页。
