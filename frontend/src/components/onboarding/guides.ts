import { isAdminish } from "@/lib/auth";

type GuideStep = { target: string; title: string; content: string; managerOnly?: boolean; superOnly?: boolean };
type Guide = { title: string; managerOnly?: boolean; steps: GuideStep[] };
const step = (target: string, title: string, content: string): GuideStep => ({ target, title, content });
const header = "main header h1";

// 配置只描述已存在的操作，不代替路由和后端的权限校验，也不自动点击业务按钮。
const guides: Record<string, Guide> = {
  dashboard: { title: "概览", steps: [
    step(header, "先看这里", "概览汇总你的任务进展和平台服务状态。需要处理的任务，可从列表直接进入。"),
    step('[aria-label="任务与定时计划"]', "找到最近的工作", "切换最近任务或定时计划，按状态筛选；点击具体记录继续查看，不会自动重跑。"),
    step('[aria-labelledby="platform-services-title"]', "检查可用服务", "这里展示模型和采集服务的配置情况。已配置不代表本次调用一定成功，可进入对应设置查看。"),
    step('main header a[href="/data-prep"]', "开始一个新任务", "点击新建任务，用自然语言说出目标，并按需要添加资料。"),
  ] },
  "workspace.new": { title: "任务工作台", steps: [
    step(header, "把需求交给 Mangrove", "说清想处理什么资料、需要什么结果。可以处理网页信息、文件与表格，也可以安排定时任务。"),
    step('#task-list-toggle', "找回历史任务", "展开任务列表查看状态和历史结果。切换任务不会自动重跑，也不会删除已保存的结果。"),
    step('[aria-label="任务要求"]', "写清目标与范围", "填写任务要求，按需添加文件、选择模型和输出格式。向上箭头发送；执行时同一位置的方块用于停止。"),
    step('[data-guide="workspace-composer"]', "确认后再开始", "发送前确认资料、模型和要求。初稿生成后会等待你决定：直接接受，或继续核对；继续核对可能增加用量。"),
  ] },
  "workspace.task": { title: "任务进展与追问", steps: [
    step(header, "继续处理当前任务", "查看工作记录、资料和结果。不同版本独立保留，查看历史不会覆盖当前任务。"),
    step('[data-guide="task-timeline"]', "进度和用量", "工作记录说明当前进展；查看本次用量可了解模型消耗。初稿不等于已完成核对，请按实际需要决定是否继续。"),
    step('[aria-label="继续对话"]', "可以继续追问", "在这里询问进度或提出修改。涉及需求变化时，确认后才生成新版本。针对结果细节，请明确引用内容，避免只说“刚才那一点”。"),
    step('#task-list-toggle', "安全停止与历史", "停止按钮会请求结束当前执行，不会删除历史记录；已经发出的模型请求仍可能产生费用。"),
  ] },
  "tasks.scheduled": { title: "自动化任务", steps: [
    step(header, "安排重复的工作", "可以在工作台说出定时需求，也可以手动添加自动化。计划只会在启用且配置有效时触发。"),
    step('[data-guide="automation-add"]', "创建计划", "填写任务要求、执行时间、模型和需要的通知方式，再保存。时间按北京时间执行；保存不等于立即执行。"),
    step('[data-guide="automation-tabs"]', "查看计划和运行结果", "定时任务中可启停、编辑和查看历史；立即执行会真实发起任务。运行记录查看每次执行结果和失败原因。"),
  ] },
  "tasks.runs": { title: "自动化运行记录", steps: [
    step(header, "每次执行都有记录", "这里查看执行状态、时间和结果。失败时先查看原因，避免重复点击执行。"),
    step('[data-guide="automation-tabs"]', "计划与记录分开看", "回到定时任务可调整后续计划；历史结果不会随计划修改而改变。分页可切换每页10、20、50或100条。"),
  ] },
  "templates.templates": { title: "模板库", steps: [
    step(header, "积累可复用的方法", "同类任务会自动参考匹配的方法，不需要每次手动配置。本次明确提出的要求优先。"),
    step('[aria-label="模板库筛选与操作"]', "找到合适的模板", "用关键词、任务类型和状态筛选。管理入口可维护你的任务模板与个人记忆。"),
    step('[data-guide="library-tabs"]', "区分模板和教训", "模板保存可复用方法，教训记录应避免的问题。个人经验默认仅本人使用，共享前应确认不含业务隐私。"),
  ] },
  "templates.lessons": { title: "教训库", steps: [
    step(header, "让同类问题少发生", "教训会在相关任务中被参考。查看内容与适用范围，避免把特定任务的例外当成通用规则。"),
    step('[aria-label="模板库筛选与操作"]', "查找和管理经验", "按关键词、类型和状态查找。仅操作有权限维护的记录；共享通用副本前去掉个人业务信息。"),
  ] },
  "templates.scanLog": { title: "巡检报告", managerOnly: true, steps: [
    step(header, "了解经验库维护情况", "报告记录去重、合并和草稿清理的执行结果。查看报告不会启动巡检。"),
    step('[data-guide="library-tabs"]', "核对后再调整", "需要核对内容时返回模板库或教训库。巡检可能自动删除重复条目和超期草稿，开启前先确认策略。"),
  ] },
  "memory.personal": { title: "我的记忆", steps: [
    step(header, "记住你的常用偏好", "记忆用于后续相关任务，不需要反复说明。本次任务里的明确要求始终优先。"),
    step('[aria-label="个人记忆管理"] textarea', "添加一条偏好", "例如“报告优先用表格呈现”。每条表达一个完整偏好，点击添加后保存；不要填写密码或密钥。"),
    step('[aria-label="个人记忆管理"] input', "搜索、修改或删除", "在列表中找到记忆后可以编辑或删除。修改只影响后续任务，不会改写已创建的任务。"),
    step('[aria-label="记忆范围"]', "个人与全局分开", "我的记忆只用于你发起的任务；全局记忆由管理员维护，供所有用户参考。"),
  ] },
  "memory.global": { title: "全局记忆", steps: [
    step('#global-memory-title', "大家共同参考的偏好", "这里展示管理员维护的共同规范。新任务只参考相关内容，本次明确要求优先。"),
    { ...step('#global-memory-input', "谨慎添加全局偏好", "新增内容会影响所有用户的后续任务。只填写大家都适用的规则，不要加入个人业务资料或秘密。"), managerOnly: true },
    step('[aria-label="记忆范围"]', "个人习惯放在哪里？", "只适合自己的偏好请放到我的记忆，不要当作全局规则。"),
  ] },
  "settings.personal": { title: "我的设置", steps: [
    step(header, "管理自己的使用习惯", "设置默认任务模型、外观和账号安全。默认模型只作用于新任务，不改变已经执行的任务。"),
    step('[aria-label="设置分区"]', "按需要进入设置", "模型与连接管理可用模型，采集账号维护你自己的登录凭证。这里只展示当前角色有权访问的分区。"),
    step('[data-guide="settings-content"]', "保护账号", "可以修改密码或退出设备。退出登录不等于取消任务；涉及账号或凭证的操作请仔细核对提示。"),
  ] },
  "settings.models": { title: "模型与连接", steps: [
    step(header, "选择任务使用的模型", "平台提供的可用模型可以直接使用。你也可以添加自己的连接，密钥不要写进任务或记忆。"),
    step('[data-model-tour="default-connection"]', "确认默认模型", "新任务会使用这里的默认选项；已有任务保留创建时的模型，不会静默切换。"),
    step('[aria-label="模型连接范围"]', "分清共享与个人", "平台连接按授权使用，个人连接仅供自己的任务使用。验证连接会实际调用模型，可能产生少量用量。"),
    step('#model-connection-list', "查看连接状态", "连接无效时请先修复或重新选择。添加连接的详细操作可从帮助与高级操作中查看。"),
  ] },
  "settings.credentials": { title: "采集账号", steps: [
    step(header, "维护自己的采集凭证", "登录凭证只用于你授权的采集任务，不会变成平台所有人的共享账号。"),
    step('[data-guide="settings-content"]', "检查状态再使用", "按页面提示配置或更新凭证。过期时重新登录，删除或更新前注意对后续采集的影响。不要发送凭证给其他用户。"),
  ] },
  "settings.platform": { title: "平台配置", managerOnly: true, steps: [
    step(header, "管理共享服务", "这里的配置可能影响多位用户。先确认服务用途和影响范围，再修改并保存。"),
    step('[data-guide="settings-content"]', "保存、测试与真实执行不同", "敏感值按页面提示维护；连接测试可能访问外部服务。巡检和通知开关可能引发真实操作，请谨慎开启。"),
  ] },
  "settings.governance": { title: "扩展工具管理", managerOnly: true, steps: [
    step(header, "管理扩展工具的可用范围", "查看工具状态、验证记录及开放范围。工具已安装不代表已经获准给所有人使用。"),
    step('[data-guide="settings-content"]', "先核验证据，再变更状态", "审核和治理操作有独立权限。查看他人业务内容需要说明原因并留痕，不会因为是管理员就自动获得正文访问权。"),
  ] },
  "settings.diagnostics": { title: "运行与诊断", managerOnly: true, steps: [
    step(header, "检查服务状态", "遇到模型、采集或连接问题时，先看对应的状态和错误提示，不要反复发起相同任务。"),
    step('[data-guide="settings-content"]', "自检也可能真实调用", "主动自检可能访问配置的服务并产生用量。解除限制前确认原因，避免让相同错误持续发生。"),
  ] },
  feedback: { title: "反馈管理", managerOnly: true, steps: [
    step(header, "从真实反馈改进任务效果", "点赞用于了解满意度，无需处理；待处理数只统计尚未处理的点踩。"),
    step('[aria-label="处理状态"]', "先找到需要处理的点踩", "按处理状态、评价类型、原因、日期或用户筛选。上方统计为全平台累计，不随明细筛选变化。"),
    step('[data-guide="feedback-list"]', "先看原任务，再写结论", "打开反馈详情，说明查看原因后核对用户问了什么、智能体输出了什么，再填写处理结论。查看和处理会留痕。"),
  ] },
  admin: { title: "用户管理", managerOnly: true, steps: [
    step(header, "管理获准使用平台的账号", "搜索用户、查看账号状态，按权限创建、审批或维护账号。不要为测试随意修改真实用户。"),
    step('[aria-label="搜索用户名或昵称"]', "找到目标账号", "支持用户名、昵称及角色、状态筛选。进入详情核对身份后，再做密码、启停或删除操作。"),
    step('[aria-label="自助注册设置"]', "注册与审批", "开放注册后，新账号仍需管理员审批。停用账号会禁止新操作并影响相关执行，请先确认。"),
    { ...step(header, "超级管理员的角色权限", "可以管理低级角色并调整管理员身份。权限变更会影响用户可见模块；不要把临时需求变成长久授权。"), superOnly: true },
  ] },
  chat: { title: "历史对话", steps: [
    step('main header', "查看历史对话", "此入口保留历史会话。新任务建议从任务工作台开始；继续发送会实际调用模型。"),
    step('main textarea', "继续对话", "输入追问时说明所指的内容。查看结果和下载文件不会自动重跑任务；不要重复发送同一要求。"),
  ] },
};

const operations: Record<string, [string, string]> = {
  overview: ["运营总览", "汇总登录、访问和操作情况，点击指标可继续查看对应明细。"],
  login: ["登录与活跃", "区分成功与失败登录，结合时间和用户筛选定位异常。"],
  visit: ["访问与使用", "查看访问趋势和功能热度。访问次数不等于任务完成量。"],
  audit: ["操作审计", "追溯关键操作、执行结果和变更记录；查看详情不会重新执行该操作。"],
  users: ["用户洞察", "查看有权访问的用户使用情况，进入行为时间线了解具体活动。"],
  tokens: ["Token用量", "按用户和模型查看用量与参考成本。本地模型只统计Token；未知用量或未配置价格不会当作零费用。"],
};
for (const [key, [title, description]] of Object.entries(operations)) {
  guides[`operations.${key}`] = { title, managerOnly: true, steps: [
    step(header, title, description),
    step('[aria-label="快捷时间范围"]', "先选择统计时间", "可选今天、昨天、近7天、近30天或自定义日期。更换时间后再查看统计和导出，日期按北京时间理解。"),
    step('[aria-label="运营子页面"]', "按问题选择查看方向", "登录、访问、操作审计、用户和Token用量分别统计，不要把不同口径直接比较。"),
    step('.ops-scope', "只查看授权范围", "普通管理员查看本人及普通用户范围，超级管理员可查看全平台。业务正文仍受独立审计权限控制。"),
    ...(key === "tokens" ? [step('[aria-label="用户Token统计"]', "展开模型明细与导出", "按消耗、成本或请求次数排序，展开用户查看模型明细，可导出CSV。参考价格仅供估算，实际以官方账单为准。")]: []),
  ] };
}

export function getGuide(key: string, role: string): Guide | null {
  const guide = guides[key];
  if (!["user", "admin", "super_admin"].includes(role) || !guide || (guide.managerOnly && !isAdminish(role))) return null;
  return { ...guide, steps: guide.steps.filter(item => (!item.managerOnly || isAdminish(role)) && (!item.superOnly || role === "super_admin")) };
}
