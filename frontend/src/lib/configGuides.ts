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
  key: string;
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

/** 普通用户「我的凭证」的指南范围：只覆盖她能自助配置的模型 Key + 平台 Cookie。 */
export const CONFIG_GUIDE_SECTIONS: GuideSection[] = [
  { key: "model", title: "模型 API Key", entries: MODEL_ENTRIES },
  { key: "cookie", title: "平台 / 网站 Cookie", entries: COOKIE_ENTRIES },
];

// ──────────────────────────────────────────────────────────────────────────
// 以下为管理员「配置中心」专用：覆盖 REGISTRY 里全部配置项，分组与顺序对齐
// src/config/runtime_config.py 的 GROUPS。
// ──────────────────────────────────────────────────────────────────────────

const LLM_DEFAULT_ENTRIES: GuideEntry[] = [
  {
    key: "llm_default_provider",
    title: "默认供应商",
    steps: [
      { text: "先确定要用哪个模型供应商：DeepSeek（云端，性价比高）、阿里百炼 Qwen（云端，阿里生态）、本地端点（自建模型服务，数据不出内网）" },
      { text: "在下面对应分组（模型·DeepSeek / 模型·阿里百炼 Qwen / 模型·本地端点）里先配好该供应商的 API Key 或本地地址" },
      { text: "回到这里选择默认供应商并保存，之后未指定供应商的任务都会用它" },
    ],
  },
];

const LLM_DEEPSEEK_ENTRIES: GuideEntry[] = [
  MODEL_ENTRIES[0],
  {
    key: "deepseek_base_url",
    title: "DeepSeek 接口地址",
    steps: [
      { text: "官方默认地址已经填好：https://api.deepseek.com/v1，一般不需要修改" },
      { text: "仅在你要接入自建代理/网关转发 DeepSeek 请求时才需要改成该网关地址" },
    ],
  },
  {
    key: "deepseek_model",
    title: "DeepSeek 默认模型",
    steps: [
      { text: "下拉框直接选择即可，无需额外获取" },
      { text: "常见可选：deepseek-v4-pro（效果优先）、deepseek-v4-flash（速度与成本优先）" },
    ],
  },
];

const LLM_QWEN_ENTRIES: GuideEntry[] = [
  MODEL_ENTRIES[1],
  {
    key: "qwen_base_url",
    title: "百炼接口地址",
    steps: [
      { text: "官方默认地址已经填好：https://dashscope.aliyuncs.com/compatible-mode/v1，一般不需要修改" },
      { text: "仅在你要接入自建代理/网关转发请求时才需要改" },
    ],
  },
  {
    key: "qwen_model",
    title: "百炼默认模型",
    steps: [
      { text: "下拉框直接选择即可" },
      { text: "常见可选如 qwen-plus（默认，性价比均衡）、qwen-max（能力更强，价格更高）" },
    ],
  },
];

const LLM_LOCAL_ENTRIES: GuideEntry[] = [
  {
    key: "llm_base_url",
    title: "本地模型接口地址",
    steps: [
      { text: "先在服务器上部署好本地推理服务（如 vLLM / Ollama 等 OpenAI 兼容网关）" },
      { text: "把该服务对外暴露的地址填进来，格式如 http://127.0.0.1:8000/v1" },
      { text: "保存后可点击「验证」确认能连通" },
    ],
  },
  {
    key: "llm_model_name",
    title: "本地默认模型名",
    steps: [
      { text: "下拉框会读取本地服务已注册的模型列表，直接选择即可" },
      { text: "若下拉框为空，说明「本地模型接口地址」还没配好，先配好上一项" },
    ],
  },
  {
    key: "local_extra_models",
    title: "额外本地模型",
    steps: [
      { text: "用于同时接入多个本地模型端点（比如两台机器各跑一个模型）" },
      { text: "格式：名称@地址，多个用英文逗号分隔，如 qwen3-32b@http://192.168.1.10:8000/v1,glm4@http://192.168.1.11:8000/v1" },
    ],
  },
  {
    key: "local_enable_thinking",
    title: "本地模型思考模式",
    steps: [
      { text: "仅对支持「思考」的本地模型（如 Qwen3 系列）有意义" },
      { text: "选 False（默认）：关闭思考，响应更快；选 True：开启思考，回答更慢但可能更严谨" },
      { text: "不确定就保持默认 False，避免返回内容被思考过程占满导致空回复" },
    ],
  },
];

const SEARCH_ENTRIES: GuideEntry[] = [
  {
    key: "tavily_api_key",
    title: "Tavily API Key",
    steps: [
      { text: "打开 Tavily 官网注册账号（有免费额度）", link: { label: "tavily.com", url: "https://tavily.com" } },
      { text: "登录后在控制台找到 API Key 并复制" },
      { text: "回到本页粘贴保存；配置后搜索类任务会优先/兜底使用 Tavily" },
    ],
  },
  {
    key: "searxng_base_url",
    title: "SearXNG 地址",
    steps: [
      { text: "SearXNG 是开源免费的自建元搜索引擎，需要自己部署（如 docker），不是在线注册服务" },
      { text: "部署完成后把地址填进来，格式如 http://localhost:8080" },
      { text: "不部署也可以，留空则用其他搜索后端" },
    ],
  },
  {
    key: "anysearch_api_key",
    title: "AnySearch API Key",
    steps: [
      { text: "打开 AnySearch 控制台注册账号", link: { label: "anysearch.com", url: "https://anysearch.com/console/api-keys" } },
      { text: "控制台创建 API Key 并复制" },
      { text: "回到本页粘贴保存" },
    ],
  },
  {
    key: "firecrawl_base_url",
    title: "Firecrawl 地址",
    steps: [
      { text: "Firecrawl 通常自托管部署（见项目 docker/firecrawl/ 目录），用 docker 启动" },
      { text: "部署完成后把地址填进来，格式如 http://localhost:3002" },
      { text: "自托管通常不需要额外 API Key（下一项留空即可）" },
    ],
  },
  {
    key: "firecrawl_api_key",
    title: "Firecrawl API Key",
    steps: [
      { text: "自托管部署通常留空即可（无需鉴权）" },
      { text: "如果改用云端 Firecrawl 服务，在其控制台创建 Key 后填入" },
    ],
  },
  {
    key: "rsshub_base_url",
    title: "RSSHub 地址",
    steps: [
      { text: "自托管：docker run -p 1200:1200 diygod/rsshub，然后填 http://localhost:1200" },
      { text: "或直接用公共实例（有限流，仅建议轻量使用）", link: { label: "rsshub.app", url: "https://rsshub.app" } },
      { text: "留空则不启用该采集方式" },
    ],
  },
];

/** 邮箱厂商开启 SMTP 后会生成一个"授权码/应用专用密码"——面板里作为一张卡片展示整体流程。 */
function emailSteps(): GuideStep[] {
  return [
    { text: "常见邮箱：QQ邮箱/163邮箱在「设置-账户」里开启 SMTP 服务后会生成一个「授权码」（不是邮箱登录密码）；Gmail 需开启两步验证后生成「应用专用密码」" },
    { text: "服务器地址与端口按邮箱服务商填写，常见：QQ邮箱 smtp.qq.com:465(SSL)，163邮箱 smtp.163.com:465(SSL)，Gmail smtp.gmail.com:587(STARTTLS)" },
    { text: "「SMTP 账号」填完整邮箱地址，「SMTP 密码/授权码」填上一步生成的授权码/应用专用密码，不是登录密码" },
    { text: "「发件人」留空则自动用账号本身；「SSL」端口 465 选 True，端口 587(STARTTLS) 选 False" },
  ];
}

/** 编辑弹窗内嵌用：每个字段只展示对该字段本身有用的一句话，不重复整套 SMTP 流程。 */
const EMAIL_FIELD_ENTRIES: GuideEntry[] = [
  {
    key: "smtp_enabled", title: "启用邮件发送",
    steps: [{ text: "总开关：关闭后即使下面已配置服务器/账号/密码，任务里也不会真的发送邮件；凭证不会被清空，随时可以重新开启" }],
  },
  {
    key: "smtp_host", title: "SMTP 服务器",
    steps: [{ text: "按邮箱服务商填写，常见：QQ邮箱 smtp.qq.com、163邮箱 smtp.163.com、Gmail smtp.gmail.com" }],
  },
  {
    key: "smtp_port", title: "SMTP 端口",
    steps: [{ text: "和「SSL」开关配套：填 465 选 True（SSL）；填 587(STARTTLS) 选 False" }],
  },
  {
    key: "smtp_user", title: "SMTP 账号",
    steps: [{ text: "填完整邮箱地址，如 example@qq.com" }],
  },
  {
    key: "smtp_password", title: "SMTP 密码/授权码",
    steps: [{ text: "不是邮箱登录密码！QQ/163邮箱在「设置-账户」开启SMTP服务后生成「授权码」；Gmail 需开启两步验证后生成「应用专用密码」" }],
  },
  {
    key: "smtp_from", title: "发件人",
    steps: [{ text: "留空则自动使用「SMTP 账号」本身作为发件人地址" }],
  },
  {
    key: "smtp_use_ssl", title: "SSL 开关",
    steps: [{ text: "端口填 465 选 True（SSL）；端口填 587(STARTTLS) 选 False" }],
  },
];

/** 面板里合并成一张卡片：6 个字段本来就要一起配置，拆开逐条重复反而难读。 */
const EMAIL_GROUP_ENTRY: GuideEntry = {
  key: "email_group", title: "邮件 SMTP（服务器/端口/账号/密码/发件人/SSL 一起配置）", steps: emailSteps(),
};

/** 总开关单独一张卡片：这是"要不要用"的开关，和"怎么配置"是两件事，不合并进上面那张卡片。 */
const SMTP_ENABLE_ENTRY: GuideEntry = {
  key: "smtp_enabled", title: "启用邮件发送",
  steps: [{ text: "关闭后即使已配置服务器/账号/密码，任务里也不会真的发送邮件；凭证不会被清空，随时可以重新开启。设置页「连接器/增强」卡片里也有同一个开关" }],
};

const SLACK_ENTRIES: GuideEntry[] = [
  {
    key: "slack_enabled", title: "启用 Slack 通知",
    steps: [{ text: "关闭后即使已配置 Webhook，任务里也不会真的推送消息；Webhook 不会被清空，随时可以重新开启。设置页「连接器/增强」卡片里也有同一个开关" }],
  },
  {
    key: "slack_webhook_url",
    title: "Slack Webhook URL",
    steps: [
      { text: "打开 Slack API 网站创建一个 App（或使用已有 App）", link: { label: "api.slack.com/apps", url: "https://api.slack.com/apps" } },
      { text: "左侧菜单进入「Incoming Webhooks」，开启开关" },
      { text: "点击「Add New Webhook to Workspace」，选择要接收通知的频道并授权" },
      { text: "复制生成的 Webhook URL（形如 https://hooks.slack.com/services/...），回到本页粘贴保存" },
    ],
  },
];

function embeddingSteps(): GuideStep[] {
  return [
    { text: "用于模板自学习的语义召回匹配，不配置也能用（自动退化为关键词匹配），配置后召回更准" },
    { text: "若已自建本地 embedding 服务（如本地部署的 Qwen3-Embedding），把其 OpenAI 兼容地址填入「embedding 端点」" },
    { text: "本地服务的「embedding API Key」通常可随便填一个占位值（自建服务一般不校验）" },
    { text: "若不自建，留空「embedding 端点」，系统会自动改用阿里云百炼 DashScope 的 text-embedding-v4（需要先配好上面的百炼 API Key）" },
    { text: "「启用语义召回」开关打开后，上述配置才会生效" },
  ];
}

function rerankSteps(): GuideStep[] {
  return [
    { text: "精排是语义召回命中后的二次裁决，可选，不配就跳过精排直接用召回结果" },
    { text: "同样支持自建 vLLM 部署的 Cohere 兼容 /rerank 接口，把地址填入「rerank 端点」" },
    { text: "模型名示例：Qwen3-Reranker-8B" },
    { text: "本地服务的「rerank API Key」通常可随便填一个占位值" },
  ];
}

/** 编辑弹窗内嵌用：每个字段只讲该字段自己的用法，不重复整套 embedding/rerank 说明。 */
const SEMANTIC_FIELD_ENTRIES: GuideEntry[] = [
  {
    key: "embedding_enabled", title: "启用语义召回",
    steps: [{ text: "开启后下面的 embedding 配置才生效；不开也能用，自动退化为关键词匹配" }],
  },
  {
    key: "embedding_base_url", title: "embedding 端点",
    steps: [{ text: "若自建了本地 embedding 服务（如本地部署的 Qwen3-Embedding），填其 OpenAI 兼容地址；留空则自动改用阿里云百炼 DashScope" }],
  },
  {
    key: "embedding_api_key", title: "embedding API Key",
    steps: [{ text: "自建本地服务通常可随便填一个占位值；留空走「模型·阿里百炼」组里配置的百炼 API Key" }],
  },
  {
    key: "embedding_model", title: "embedding 模型名",
    steps: [{ text: "自建服务按其支持的模型名填；用百炼时默认 text-embedding-v4" }],
  },
  {
    key: "rerank_base_url", title: "rerank 端点",
    steps: [{ text: "可选的二次精排，支持自建 vLLM 部署的 Cohere 兼容 /rerank 接口；留空则跳过精排直接用召回结果" }],
  },
  {
    key: "rerank_api_key", title: "rerank API Key",
    steps: [{ text: "自建本地服务通常可随便填一个占位值" }],
  },
  {
    key: "rerank_model", title: "rerank 模型名",
    steps: [{ text: "示例：Qwen3-Reranker-8B" }],
  },
];

/** 面板里合并成两张卡片：embedding 4 字段一起配置、rerank 3 字段一起配置，避免逐条重复。 */
const EMBEDDING_GROUP_ENTRY: GuideEntry = {
  key: "embedding_group", title: "embedding 语义召回（启用/端点/Key/模型名 一起配置）", steps: embeddingSteps(),
};
const RERANK_GROUP_ENTRY: GuideEntry = {
  key: "rerank_group", title: "rerank 精排（端点/Key/模型名 一起配置）", steps: rerankSteps(),
};

function kdlSteps(): GuideStep[] {
  return [
    { text: "打开快代理官网注册账号", link: { label: "kuaidaili.com", url: "https://www.kuaidaili.com" } },
    { text: "购买套餐后在控制台的「密钥管理」里能看到 secret_id 和 signature" },
    { text: "「用户名/密码」是快代理账号的私密代理用户名密码，在订单详情里能看到" },
    { text: "四项都配完后，把「代理商」选为 kuaidaili" },
  ];
}

/** 编辑弹窗内嵌用：快代理 4 个字段各自只讲自己去哪找，不重复整套注册流程。 */
const KDL_FIELD_ENTRIES: GuideEntry[] = [
  {
    key: "mc_kdl_secret_id", title: "快代理 secret_id",
    steps: [{ text: "打开快代理控制台「密钥管理」页面复制", link: { label: "kuaidaili.com", url: "https://www.kuaidaili.com" } }],
  },
  {
    key: "mc_kdl_signature", title: "快代理 signature",
    steps: [{ text: "和 secret_id 配套，同样在快代理控制台「密钥管理」页面复制" }],
  },
  {
    key: "mc_kdl_user_name", title: "快代理用户名",
    steps: [{ text: "快代理账号的私密代理用户名，在订单详情页能看到（不是登录快代理网站的用户名）" }],
  },
  {
    key: "mc_kdl_user_pwd", title: "快代理密码",
    steps: [{ text: "对应私密代理用户名的密码，同样在订单详情页能看到" }],
  },
];

/** 面板里合并成一张卡片：4 个字段本来就要一起去快代理控制台配齐。 */
const KDL_GROUP_ENTRY: GuideEntry = {
  key: "kdl_group", title: "快代理（secret_id/signature/用户名/密码 一起配置）", steps: kdlSteps(),
};

const PROXY_ENTRIES: GuideEntry[] = [
  {
    key: "mc_enable_ip_proxy",
    title: "启用代理池",
    steps: [
      { text: "用于降低 MediaCrawler 采集时被平台风控的概率，一般站点不需要开" },
      { text: "开启前需先在下面选好「代理商」并配好对应凭证" },
    ],
  },
  {
    key: "mc_ip_proxy_provider",
    title: "代理商选择",
    steps: [
      { text: "kuaidaili（快代理）：需要下面 4 个快代理专属字段" },
      { text: "wandouhttp（豌豆HTTP）：只需要 app_key 一个字段" },
      { text: "static（静态/隧道代理）：只需要下面的代理 URL 一个字段" },
    ],
  },
  KDL_GROUP_ENTRY,
  {
    key: "mc_wandou_app_key",
    title: "豌豆HTTP app_key",
    steps: [
      { text: "打开豌豆HTTP官网注册账号并购买套餐", link: { label: "wandouhttp.com", url: "https://www.wandouhttp.com" } },
      { text: "控制台复制 app_key" },
      { text: "把「代理商」选为 wandouhttp" },
    ],
  },
  {
    key: "mc_static_proxy_url",
    title: "静态/隧道代理 URL",
    steps: [
      { text: "适用于你已经有现成的代理地址（自建或第三方隧道代理）" },
      { text: "格式：http://用户名:密码@主机:端口" },
      { text: "把「代理商」选为 static" },
    ],
  },
];

const MC_CDP_ENTRIES: GuideEntry[] = [
  {
    key: "mc_enable_cdp_mode",
    title: "启用 CDP 模式",
    steps: [
      { text: "开启后 MediaCrawler 会连接本机真实安装的 Chrome/Edge 采集，而不是自带的 Chromium" },
      { text: "真实浏览器指纹能明显降低抖音/B站这类设备指纹风控较重平台的登录态误判概率" },
      { text: "需要本机装了 Chrome 或 Edge，且能弹出可见浏览器窗口（纯无头服务器不适用）" },
      { text: "点\"验证\"可检测本机是否能找到 Chrome/Edge，不代表登录态一定有效" },
    ],
  },
];

function mysqlSteps(): GuideStep[] {
  return [
    { text: "默认用 sqlite（本地文件，无需配置），只有需要多机共享数据或更大并发时才需要切 MySQL" },
    { text: "若要切换，先联系你的 DBA 或自行部署一个 MySQL 实例，拿到主机地址、端口、账号、密码、库名" },
    { text: "库需要提前建好（本项目不会自动建库），字符集建议 utf8mb4" },
    { text: "把「入库后端」切为 mysql，并填好下面的连接信息，保存后立即生效" },
  ];
}

/** 编辑弹窗内嵌用：MySQL 6 个字段各自只讲自己该填什么，不重复整套切库说明。 */
const MYSQL_FIELD_ENTRIES: GuideEntry[] = [
  {
    key: "db_backend", title: "入库后端",
    steps: [{ text: "默认 sqlite 无需配置；需要多机共享数据或更大并发时才切 mysql，下面 5 项要配套填好" }],
  },
  {
    key: "mysql_host", title: "MySQL 主机",
    steps: [{ text: "MySQL 实例地址，找 DBA 要或自己部署，示例 127.0.0.1（本机）或内网 IP" }],
  },
  {
    key: "mysql_port", title: "MySQL 端口",
    steps: [{ text: "MySQL 默认端口 3306，一般不用改" }],
  },
  {
    key: "mysql_user", title: "MySQL 用户",
    steps: [{ text: "需要有权限访问目标库的账号" }],
  },
  {
    key: "mysql_password", title: "MySQL 密码",
    steps: [{ text: "对应账号的密码" }],
  },
  {
    key: "mysql_database", title: "MySQL 库名",
    steps: [{ text: "库需要提前建好（本项目不会自动建库），字符集建议 utf8mb4" }],
  },
];

/** 面板里合并成一张卡片：6 个字段本来就要一起切换/配置。 */
const MYSQL_GROUP_ENTRY: GuideEntry = {
  key: "mysql_group", title: "MySQL 入库（后端/主机/端口/账号/密码/库名 一起配置）", steps: mysqlSteps(),
};

const CHECKPOINT_ENTRIES: GuideEntry[] = [
  {
    key: "checkpoint_enabled",
    title: "启用断点续跑",
    steps: [
      { text: "开启后任务执行状态会持久化到本地 SQLite，中断后可用同一个任务 id 从断点恢复继续跑" },
      { text: "关闭则每次都从头执行；没有远程服务可连，这里只是一个开关" },
      { text: "设置页「连接器/增强」卡片里也有同一个开关" },
    ],
  },
];

const COOKIE_HEALTH_ENTRIES: GuideEntry[] = [
  {
    key: "cookie_health_scan_enabled",
    title: "启用定时巡检",
    steps: [
      { text: "开启后会按下面的间隔自动验证全部 10 个 Cookie（7 社媒 + 京东/淘宝/拼多多）并更新状态" },
      { text: "默认关闭：社媒平台的验证是真实浏览器自动化登录，定时反复跑有被平台风控识别的风险，建议按需开启" },
      { text: "设置页/配置中心里点\"验证\"（手动触发）不受这个开关影响，随时可以点" },
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

const LIBRARY_DEDUP_ENTRIES: GuideEntry[] = [
  {
    key: "library_dedup_scan_enabled",
    title: "启用知识库巡检",
    steps: [
      { text: "开启后会按下面的间隔自动扫描模板库/教训库：语义去重合并近似重复条目 + 清理长期停滞的草稿" },
      { text: "默认关闭：属于低频后台维护任务，按需开启即可，不影响任务实时召回逻辑" },
    ],
  },
  {
    key: "library_dedup_scan_interval_hours",
    title: "巡检间隔（小时）",
    steps: [{ text: "默认 24（每天一次）" }],
  },
  {
    key: "library_stale_draft_days",
    title: "停滞草稿清理阈值（天）",
    steps: [{ text: "草稿条目创建后超过这个天数仍未转正（累计命中不足），巡检时会自动删除；默认 30 天" }],
  },
  {
    key: "library_dedup_scan_max_merges_per_run",
    title: "每轮最大合并对数",
    steps: [{ text: "每轮巡检每个知识库最多处理的确认合并对数，避免历史积压一次性触发过多模型调用；默认 5" }],
  },
];

/** 管理员「配置中心」的指南范围：覆盖 REGISTRY 全部配置项。
 *  顺序对齐 ConfigCenter.tsx 的 GROUP_CATEGORIES 分类（模型 → 采集与反爬 → 通知集成 → 高级/基础设施），
 *  与面板改版后的分区保持一致，避免面板和指南顺序对不上。
 *  面板按"一起配置的字段"合并展示（如邮件/语义召回/快代理/MySQL），避免同一段说明反复出现好几遍；
 *  每个字段自己的编辑弹窗内嵌提示（GUIDE_BY_KEY）另有专属的单句说明，见各 *_FIELD_ENTRIES。 */
export const ADMIN_GUIDE_SECTIONS: GuideSection[] = [
  // 模型
  { key: "llm_default", title: "模型 · 默认选择", entries: LLM_DEFAULT_ENTRIES },
  { key: "llm_deepseek", title: "模型 · DeepSeek", entries: LLM_DEEPSEEK_ENTRIES },
  { key: "llm_qwen", title: "模型 · 阿里百炼 Qwen", entries: LLM_QWEN_ENTRIES },
  { key: "llm_local", title: "模型 · 本地端点", entries: LLM_LOCAL_ENTRIES },
  // 采集与反爬
  { key: "search", title: "搜索与采集服务", entries: SEARCH_ENTRIES },
  { key: "cookies", title: "平台 Cookie", entries: COOKIE_ENTRIES },
  { key: "cookie_health", title: "Cookie 健康巡检", entries: COOKIE_HEALTH_ENTRIES },
  { key: "proxy", title: "代理池", entries: PROXY_ENTRIES },
  { key: "mc_cdp", title: "MediaCrawler 反检测（CDP 模式）", entries: MC_CDP_ENTRIES },
  // 通知集成
  { key: "email", title: "邮件 SMTP", entries: [SMTP_ENABLE_ENTRY, EMAIL_GROUP_ENTRY] },
  { key: "slack", title: "Slack", entries: SLACK_ENTRIES },
  // 高级 / 基础设施
  { key: "semantic", title: "语义召回（embedding/rerank）", entries: [EMBEDDING_GROUP_ENTRY, RERANK_GROUP_ENTRY] },
  { key: "mysql", title: "MySQL 入库", entries: [MYSQL_GROUP_ENTRY] },
  { key: "checkpoint", title: "断点续跑", entries: CHECKPOINT_ENTRIES },
  { key: "library_dedup", title: "知识库巡检", entries: LIBRARY_DEDUP_ENTRIES },
];

const GUIDE_BY_KEY: Record<string, GuideEntry> = Object.fromEntries(
  [
    ...CONFIG_GUIDE_SECTIONS.flatMap((s) => s.entries),
    ...ADMIN_GUIDE_SECTIONS.flatMap((s) => s.entries),
    ...EMAIL_FIELD_ENTRIES,
    ...SEMANTIC_FIELD_ENTRIES,
    ...KDL_FIELD_ENTRIES,
    ...MYSQL_FIELD_ENTRIES,
  ].map((e) => [e.key, e]),
);

/** 按 config key 取该项的指南步骤；找不到返回 null。 */
export function getGuideForKey(key: string): GuideEntry | null {
  return GUIDE_BY_KEY[key] || null;
}
