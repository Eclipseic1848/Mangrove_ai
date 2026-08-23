"""
环境配置和设置模块
包含所有应用程序配置项的集中管理
"""
from typing import Any, Dict, Optional
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field

# 项目根目录（从当前文件路径计算）
# settings.py 位于 src/config/settings.py，需要向上3级到达项目根目录
_current_file = Path(__file__).resolve()
PROJECT_ROOT = _current_file.parent.parent.parent

# Chrome DevTools MCP 本地编译版本路径
CHROME_DEVTOOLS_MCP_INDEX_JS = PROJECT_ROOT / "external" / "chrome-devtools-howso" / "chrome-devtools-mcp" / "build" / "src" / "index.js"

# 任务级日志时间戳格式（统一用于目录名和日志文件，确保同一任务下时间一致）
# 示例：2026-03-06_090332
LOG_TIMESTAMP_FORMAT = "%Y-%m-%d_%H%M%S"


class Settings(BaseSettings):
    """应用配置类，使用 pydantic-settings 进行配置管理"""
    
    # LLM 模型配置



    # 本地模型（作为新 Provider 的 "local" profile；公网模型见下方 deepseek/qwen）
    llm_model_name: str = Field(default="Qwen3.6-35B-A3B", description="LLM 模型名称")
    llm_base_url: str = Field(default="http://192.168.1.20:6012/v1", description="LLM API 地址")
    llm_api_key: str = Field(default="local", description="LLM API 密钥")
    llm_temperature: float = Field(default=0.8, description="LLM 温度参数")
    llm_max_tokens: int = Field(default=4096, description="最大 token 数")
    llm_timeout: int = Field(default=120, description="LLM 请求超时时间(秒)")
    # 额外本地模型（按模型分端口部署时使用）：格式 "名称@URL,名称@URL"，多个用逗号分隔。
    # 例：Qwen3.6-35B-A3B@http://192.168.1.20:6012/v1
    # 这些会作为 local 供应商下的可选模型出现在前端，各走自己的 base_url。留空则只有上面的默认本地模型。
    local_extra_models: str = Field(default="", description="额外本地模型，格式 名称@URL，逗号分隔")

    # ===== 多模型 Provider 配置（新增）=====
    # 说明：上面的 llm_* 字段被新 Provider 视为 "local" profile（保持现有 browser agent / VOC 不受影响）。
    # 新 Provider（src/llm/provider.py）按 llm_default_provider 选择默认模型，所有 Key 从 .env 读取。
    llm_default_provider: str = Field(
        default="deepseek",
        description="默认模型供应商：deepseek | qwen | local",
    )
    # DeepSeek（公网 API，OpenAI 兼容）
    deepseek_api_key: str = Field(default="", description="DeepSeek API Key（放 .env，勿硬编码）")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1", description="DeepSeek OpenAI 兼容地址"
    )
    deepseek_model: str = Field(default="deepseek-v4-pro", description="DeepSeek 模型名")
    # 阿里云百炼 Qwen（OpenAI 兼容）
    qwen_api_key: str = Field(default="", description="百炼 API Key（放 .env，勿硬编码）")
    qwen_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="百炼 OpenAI 兼容地址",
    )
    qwen_model: str = Field(default="qwen-plus", description="百炼模型名")
    qwen_enable_thinking: Optional[bool] = Field(
        default=False,
        description="Qwen3 思考模型在非流式下需设为 False 才返回 content；非思考模型可置空以去掉该参数",
    )
    document_extraction_model: str = Field(
        default="local::Qwen3.6-35B-A3B",
        description="文档抽取默认模型，格式 provider::model；用户可在设置页覆盖",
    )
    # 本地 Qwen3 思考模型（vLLM/SGLang）：非流式下需经 chat_template_kwargs.enable_thinking=False 关思考，
    # 否则会一直"思考"耗尽 max_tokens 返回空 content。False=关思考（默认）；None=不发该参数（非思考模型）。
    local_enable_thinking: Optional[bool] = Field(
        default=False, description="本地 Qwen3 模型是否开启思考（非流式建议 False，否则返回空）"
    )
    # 本地模型专用输出上限：开思考时思考会吃 token，需给足以免被截断返回空；
    # 与全局 llm_max_tokens 分开，避免误把公网模型的请求也调到本地思考模型所需的大预算。
    local_max_tokens: int = Field(default=32768, description="本地模型输出 token 上限（开思考时建议给足）")

    # ===== 采集引擎配置（新增）=====
    # Firecrawl 自托管：留空则该采集器不可用（路由自动跳过）。自托管见 docker/firecrawl/。
    firecrawl_base_url: str = Field(default="", description="Firecrawl 自托管地址，如 http://localhost:3002")
    firecrawl_api_key: str = Field(default="", description="Firecrawl API Key（自托管通常留空）")
    # MediaCrawler 社媒采集：克隆到该路径并配好后才可用。许可证为非商业，商业化前需替换。
    mediacrawler_path: str = Field(default="", description="MediaCrawler 仓库本地路径，如 external/MediaCrawler")
    mediacrawler_python: str = Field(default="", description="运行 MediaCrawler 的 Python 解释器，留空用当前解释器")
    # 京东电商评论采集（期4）：京东已对免登录请求全面风控，免登录多返回验证页/系统繁忙。
    # 填入浏览器登录后的京东 Cookie（整段 Cookie 头）即可让搜索/评论接口正常返回；留空则走免登录尽力而为。
    jd_cookie: str = Field(default="", description="京东 Cookie（登录态，放 .env）；空则免登录尽力而为")
    # 淘宝/天猫/拼多多：ecommerce_collector 代码里早已预留 tb_cookie/pdd_cookie 读取逻辑，
    # 但此前 settings 未声明这两个字段（getattr 兜底永远拿到空串），前端"登录态"卡片显示的
    # 淘宝/拼多多状态实为摆设。这里补上真实字段，使其成为可用配置（诚实呈现，非承诺高可靠）。
    tb_cookie: str = Field(default="", description="淘宝/天猫 Cookie（登录态，放 .env）；空则免登录尽力而为")
    pdd_cookie: str = Field(default="", description="拼多多 Cookie（登录态，放 .env）；空则免登录尽力而为")
    # MediaCrawler 按平台 Cookie 登录（登录态，放 .env）：填入则该平台用 cookie 登录（--lt cookie），
    # 留空则回退 base_config 的扫码+会话保存。每个值为浏览器登录后复制的整段 Cookie 头。
    mc_cookie_dy: str = Field(default="", description="抖音 Cookie（MediaCrawler 登录态）；空则走扫码")
    mc_cookie_xhs: str = Field(default="", description="小红书 Cookie（MediaCrawler 登录态）；空则走扫码")
    mc_cookie_wb: str = Field(default="", description="微博 Cookie（MediaCrawler 登录态）；空则走扫码")
    mc_cookie_bili: str = Field(default="", description="B站 Cookie（MediaCrawler 登录态）；空则走扫码")
    mc_cookie_zhihu: str = Field(default="", description="知乎 Cookie（MediaCrawler 登录态）；空则走扫码")
    mc_cookie_ks: str = Field(default="", description="快手 Cookie（MediaCrawler 登录态）；空则走扫码")
    mc_cookie_tieba: str = Field(default="", description="贴吧 Cookie（MediaCrawler 登录态）；空则走扫码")
    # ===== Cookie 健康定时巡检（2026-07 新增）=====
    # 默认关闭：7 个社媒平台的验证是真实浏览器自动化登录，定时反复跑有被平台风控识别的
    # 风险（自动化、有规律的重复登录本身就是风控最容易识别的行为特征）。管理员按需开启，
    # 间隔建议给到天级别，不建议按小时跑。
    cookie_health_scan_enabled: bool = Field(default=False, description="是否启用 Cookie 健康定时巡检")
    cookie_health_scan_interval_hours: int = Field(default=24, description="巡检间隔（小时），默认每天一次")
    # MediaCrawler 代理IP池（反风控，国内住宅/机房代理；优于服务端挂境外 VPN，且不影响本地服务）：
    # 启动子进程时把开关注入 base_config（经 MC_* 环境变量），凭证按 MediaCrawler 约定的环境变量名注入。
    # provider=static 用 mc_static_proxy_url（隧道/固定代理，最通用）；kuaidaili/wandouhttp 用各自凭证。
    mc_enable_ip_proxy: bool = Field(default=False, description="是否启用 MediaCrawler 代理IP池")
    mc_ip_proxy_provider: str = Field(default="kuaidaili", description="代理商：kuaidaili | wandouhttp | static")
    mc_ip_proxy_pool_count: int = Field(default=2, description="代理IP池数量")
    mc_static_proxy_url: str = Field(
        default="", description="静态/隧道代理URL：http://user:pwd@host:port（provider=static 时用）"
    )
    mc_kdl_secret_id: str = Field(default="", description="快代理 secret_id（provider=kuaidaili，放 .env）")
    mc_kdl_signature: str = Field(default="", description="快代理 signature（provider=kuaidaili，放 .env）")
    mc_kdl_user_name: str = Field(default="", description="快代理 用户名（provider=kuaidaili，放 .env）")
    mc_kdl_user_pwd: str = Field(default="", description="快代理 密码（provider=kuaidaili，放 .env）")
    mc_wandou_app_key: str = Field(default="", description="豌豆HTTP app_key（provider=wandouhttp，放 .env）")
    # MediaCrawler CDP 模式（反检测）：连接本机真实安装的 Chrome/Edge 采集，而不是 Playwright
    # 自带的裸 Chromium。抖音/B站设备指纹风控较重，真实浏览器指纹能显著降低登录态被判失效的概率。
    # 需要本机装了 Chrome/Edge，且能弹出可见浏览器窗口（非纯无头服务器）。
    mc_enable_cdp_mode: bool = Field(default=False, description="是否启用 MediaCrawler CDP 模式（连接本机真实浏览器采集）")

    # ===== 分析模板自学习（阶段1：自演进技能）=====
    # 任务走通用兜底时，允许用户确认后把"本次报告结构"沉淀为模板（data/templates/），下次同类任务自动复用。
    template_learning_enabled: bool = Field(default=True, description="是否启用分析报告模板的自学习沉淀")
    # 模板自学习增强（草稿区/使用统计/质量门转正与淘汰/去重）。新模板先存草稿，达标转正、不达标淘汰。
    template_promote_uses: int = Field(default=3, description="草稿模板转正所需的最少使用次数")
    template_promote_quality: int = Field(default=70, description="草稿转正所需的平均质量分阈值")
    template_retire_quality: int = Field(default=50, description="低于此平均质量分（达最少次数后）则淘汰模板")
    template_dedup_threshold: float = Field(default=0.6, description="保存时关键词 Jaccard≥此值视为重复，复用旧模板不新建")
    template_dedup_rerank_threshold: float = Field(default=0.7, description="语义去重 rerank 相关性分数阈值（≥则判定为同一类模板，比召回阈值更严格）")
    template_min_data_count: int = Field(default=3, description="沉淀模板所需的最少采集数据条数，低于此值视为采集失败，不沉淀模板")
    template_dead_zone_uses: int = Field(default=10, description="草稿模板使用次数达此值仍未转正则强制淘汰，防止永久卡在质量门死区（须大于 template_promote_uses）")
    template_curator_candidate_min_cosine: float = Field(default=0.3, description="Curator 候选召回的余弦相似度下限，低于此值的候选不纳入裁决（避免明显不相关内容干扰判断）")
    # ===== 失败教训分流（阶段B2：lesson channel）=====
    # 任务被判定"采集失败"时（见 checker.py 的 _looks_like_collection_failure），不再是死胡同——
    # 蒸馏成通用化的教训存到 data/lessons/，下次同类任务在 planner/analyze 节点提前注入提醒。
    lesson_learning_enabled: bool = Field(default=True, description="是否启用失败教训的自学习沉淀")
    lesson_promote_occurrences: int = Field(default=2, description="草稿教训累计命中该次数后转正(active)，开始被 planner/analyze 注入")
    lesson_candidate_min_cosine: float = Field(default=0.3, description="教训候选召回的余弦相似度下限，低于此值不纳入精判（避免明显不相关内容干扰）")
    # 消费侧召回（find_active_lessons，任务→教训的"是否有帮助"判据）与创建侧判重
    # （find_similar_lesson/template_dedup_rerank_threshold，教训→教训的"是否同一场景"判据）
    # 是两种不同的语义判断，不能共用阈值——去重阈值(0.7)偏严格，用于召回会导致有效教训召不回。
    # 与模板库 rerank_match_threshold（召回，0.35）同构，独立于教训判重阈值。
    lesson_recall_rerank_threshold: float = Field(default=0.35, description="消费侧教训召回 rerank 相关性阈值（≥则命中，独立于教训去重判重阈值，更宽松以提高召回率）")
    # ===== 模板库/教训库定时巡检（C阶段：结构性升级）=====
    # 独立巡检器，与下方 cookie_health 巡检完全独立（业务不相关），风格仿照其异步轮询+开关热读。
    library_dedup_scan_enabled: bool = Field(default=False, description="是否启用模板库/教训库定时巡检（语义去重扫描+停滞草稿清理）")
    library_dedup_scan_interval_hours: int = Field(default=24, description="巡检间隔（小时），默认每天一次")
    library_stale_draft_days: int = Field(default=30, description="草稿条目创建后超过此天数仍未转正则视为长期停滞，巡检时自动清理（模板库/教训库公用）")
    library_dedup_scan_max_merges_per_run: int = Field(default=5, description="每轮巡检每个知识库最多处理的确认合并对数，防止历史积压一次性触发过多LLM调用")
    # 模板 embedding 语义召回（方案A：HTTP 调 OpenAI 兼容 /embeddings；默认关，配好才开）。
    # 后端级联：配了 embedding_base_url（如本地 vLLM）则优先本地；失败自动切 qwen（DashScope
    # text-embedding-v4）备选；两者都不可用时回退关键词匹配（不致瘫）。
    embedding_enabled: bool = Field(default=False, description="是否启用模板 embedding 语义召回")
    embedding_base_url: str = Field(default="", description="首选 embedding 端点（本地 vLLM 等；留空直接用 qwen）")
    embedding_api_key: str = Field(default="", description="首选端点 API Key（本地 vLLM 可填任意值）")
    embedding_model: str = Field(default="text-embedding-v4", description="首选端点的 embedding 模型名")
    embedding_match_threshold: float = Field(default=0.5, description="语义召回余弦相似度阈值（≥则命中）")
    # 重排序精排（可选）：召回 top 候选后交给 reranker 裁决，进一步压误召回。留空 base_url 即禁用。
    rerank_base_url: str = Field(default="", description="rerank 端点（vLLM /v1，Cohere 兼容 /rerank）")
    rerank_api_key: str = Field(default="", description="rerank API Key（本地 vLLM 可填任意值）")
    rerank_model: str = Field(default="", description="rerank 模型名，如 Qwen3-Reranker-8B")
    rerank_match_threshold: float = Field(default=0.35, description="rerank 相关性分数阈值（≥则命中）")

    # ===== Checker 质量评估（Phase 3，maker≠checker）=====
    # 分析产出后由独立视角打质量分；不达标时带问题清单自动重跑分析 1 次（P1-2 闭环，限 1 次防死循环）。
    # 通过且走了通用兜底时，自动沉淀模板（自学习阶段2，替代人工确认）。
    checker_enabled: bool = Field(default=True, description="是否启用 Checker 质量评估")
    checker_pass_threshold: int = Field(default=70, description="质量分通过阈值（0-100，>= 视为通过）")
    checker_rerun_enabled: bool = Field(default=True, description="质检不达标时是否带问题清单自动重跑分析（限1次）")

    # ===== 上下文工程（Phase 3 C，控制 token / 防上下文膨胀）=====
    # 对话历史压缩：超过 max 条则把较早消息 LLM 摘要成一条，仅保留最近 keep 条。
    context_max_messages: int = Field(default=12, description="对话历史超过该条数则压缩")
    context_keep_recent: int = Field(default=6, description="历史压缩时保留的最近消息条数")
    # 各节点喂给 LLM 的截断预算（集中管理，便于按模型上下文窗口调整）
    clean_max_item_chars: int = Field(default=4000, description="清洗：单条正文字符上限")
    analyze_max_blob_chars: int = Field(default=12000, description="分析：喂模型的总字符上限")
    checker_max_report_chars: int = Field(default=6000, description="质检：报告截断字符上限")

    # ===== VOC 分析桥接（新增）=====
    # 开启后，VOC 分析优先走 src/services/voc_processor（依赖其本地模型）；失败或关闭则回退到可切换模型的 LLM VOC 分析。
    voc_use_processor: bool = Field(default=False, description="VOC 分析是否优先使用 voc_processor 引擎")

    # ===== 搜索发现（关键词→真实结果链接）=====
    # 免密钥的 DuckDuckGo 易被反爬(202)；为可靠的"站定向检索"建议配 Tavily（为 AI 代理设计，
    # 有免费额度，直接返回链接+正文，可 include_domains 限定到指定站）。留空则退回 DDG 尽力而为。
    # auto 优先级：AnySearch(配了Key最先,内联正文) → SearXNG(自托管免费) → ddgs(免费) → Tavily(兜底，省额度)
    search_provider: str = Field(default="auto", description="搜索后端：auto | anysearch | searxng | ddgs | tavily")
    # SearXNG 自托管元搜索（免费无限、开源）。见 docker/searxng/。留空则不用。
    searxng_base_url: str = Field(default="", description="SearXNG 地址，如 http://localhost:8080")
    tavily_api_key: str = Field(default="", description="Tavily API Key（放 .env）；空则不用 Tavily")
    tavily_base_url: str = Field(default="https://api.tavily.com", description="Tavily API 根地址")
    # Tavily 正文优先：auto 模式下先用 Tavily（直接返回内联正文，对知乎/汽车之家等强反爬站最可靠，
    # 不必先发现链接再硬抓——后者在反爬站几乎必失败），不足时再退 SearXNG/DDG 发现+抓取。
    # 关掉则回到“免费优先、Tavily 兜底”的旧顺序（更省 Tavily 额度，但反爬站正文常拿不到）。
    search_tavily_first: bool = Field(default=True, description="auto 模式下是否 Tavily 正文优先")
    # AnySearch：统一实时搜索 API（anysearch.ai），为 AI Agent 设计，支持 16 个垂直领域 + 批量搜索 + 页面提取。
    # 注册 https://anysearch.com/console/api-keys；留空则不用 AnySearch。
    anysearch_api_key: str = Field(default="", description="AnySearch API Key（放 .env）；空则不用 AnySearch")
    # RSSHub 自托管（开源 MIT）：把无官方 RSS 的国内站（微博/B站/知乎/公众号/新闻/什么值得买等）
    # 转成标准 RSS，供 rss 采集器轻量拉取（反爬弱、对站友好）。留空则不启用。
    # 自托管：docker run -p 1200:1200 diygod/rsshub，再填 http://localhost:1200。公共实例 https://rsshub.app（有限流）。
    rsshub_base_url: str = Field(default="", description="RSSHub 地址，如 http://localhost:1200；空则不启用")

    # ===== 采集反爬加固（期2，无代理）=====
    # 同域名请求间隔在 [min, max] 区间内每次随机取值，模拟真人不规律节奏、规避频次风控；
    # min<=0 关闭限速。max<=min 时退化为固定间隔。与 mediacrawler 的 mc_crawl_*_sleep_sec 策略一致。
    collector_min_interval_seconds: float = Field(default=1.0, description="同域名请求间隔下限(秒)")
    collector_max_interval_seconds: float = Field(default=3.0, description="同域名请求间隔上限(秒)，每次在[min,max]随机")
    # 发现型采集器（search/firecrawl/crawl4ai/scrapling/simple_http、mediacrawler 帖子数）一次最多处理的条数上限：
    # 取 min(spec.max_items, 此值) 生效，防止 LLM 把 max_items 设得过大拖垮抓取/超时。要采更多就调大此项。
    # （browser 交互兜底因极慢，单独写死 3 不受此项影响。）
    collector_max_items: int = Field(default=30, ge=1, le=500, description="单采集器一次最多处理条数（含社媒帖子数）")
    collector_rotate_ua: bool = Field(default=True, description="是否轮换 User-Agent/Header")
    # scrapling 隐身兜底：Fetcher(HTTP 指纹)取空/失败时，用 StealthyFetcher(Camoufox 隐身浏览器)
    # 重抓，过知乎/汽车之家/Cloudflare 等强反爬站。需先跑 `scrapling install` 下载 Camoufox 内核；
    # 内核不可用时自动降级回 Fetcher（首次失败后不再重试，不拖慢流水线）。Camoufox 启动慢、占资源，
    # 可关。仅对 Fetcher 拿不到的少数 URL 触发。
    scrapling_stealth_enabled: bool = Field(default=True, description="scrapling 取空时用 Camoufox 隐身浏览器兜底")
    collector_max_retries: int = Field(default=2, description="采集失败重试次数（指数退避）")
    collector_retry_base_seconds: float = Field(default=1.0, description="重试退避基数(秒)")
    collector_retry_max_seconds: float = Field(default=30.0, description="重试退避上限(秒)")
    # 单个采集器调用的整体超时（秒）：超时视为该采集器失败并降级到下一个，防止卡死冻住流水线。
    collect_timeout_seconds: float = Field(default=90.0, description="单采集器调用超时(秒)，超时降级")
    # 正文提取级联（_extract.py）：单级提取结果短于此字符数时视为疑似失败（如只抓到 JS 空壳页的
    # 导航栏文字），继续尝试下一级；末级仍恒返回，不因短小丢弃数据，最终取三级中最长的结果兜底。
    extract_min_chars: int = Field(default=80, description="正文提取：单级结果最短字符数，短于此值继续尝试下一级")
    # mediacrawler 走真实浏览器自动化（登录+搜索+逐笔记取评论），耗时远超普通 HTTP 采集，
    # 故单独给更长超时；不设此项时该采集器会在 90s 处被误判超时而降级。
    collect_timeout_mediacrawler_seconds: float = Field(
        default=600.0, description="mediacrawler 浏览器采集超时(秒)，独立于通用超时"
    )
    # mediacrawler 浏览器采集的“动态间隔区间”：每次翻页/取笔记/取评论的 sleep 从
    # [min, max] 随机取值，模拟真人不规律节奏、规避频次风控。启动子进程时以环境变量
    # MC_CRAWL_MIN_SLEEP_SEC / MC_CRAWL_MAX_SLEEP_SEC 注入，由 MediaCrawler 的 config 读取。
    mc_crawl_min_sleep_sec: float = Field(default=2.0, description="mediacrawler 采集间隔下限(秒)")
    mc_crawl_max_sleep_sec: float = Field(default=6.0, description="mediacrawler 采集间隔上限(秒)")

    # ===== 显式视频证据链 =====
    # 默认开启但仅对明确的视频链接生效；常规网页采集路径不会进入本链路。
    video_evidence_enabled: bool = Field(default=True, description="是否为明确视频链接提取可验证证据")
    video_min_evidence_chars: int = Field(default=80, description="视频证据最少字符数，低于此值不允许生成内容结论")
    video_max_download_mb: int = Field(default=500, description="视频下载大小上限（MB）")
    video_slice_seconds: int = Field(default=300, description="视频视觉分析切片时长（秒），0 表示不切片")
    video_asr_base_url: str = Field(default="", description="可选的 OpenAI 兼容语音识别服务地址")
    video_asr_api_key: str = Field(default="", description="语音识别服务 API Key")
    video_asr_model: str = Field(default="whisper-1", description="远程语音识别模型名")
    video_asr_local_model: str = Field(default="", description="本地 faster-whisper 模型名；留空则不启用")

    # ===== 定时任务调度（期2）=====
    # 开启后网关（src/api）启动时拉起后台轮询循环；识别到 once@/cron@ 的请求会安排为定时任务。
    scheduler_enabled: bool = Field(default=True, description="是否启用定时任务调度器")
    scheduler_poll_seconds: float = Field(default=30.0, description="调度器轮询间隔(秒)")
    scheduler_db_path: str = Field(default="data/scheduler.db", description="定时任务 SQLite 路径")
    # 单个定时任务执行的整体超时（秒）：超时记为失败（cron 下轮重试），防止卡死冻住调度循环。
    scheduler_task_timeout_seconds: float = Field(default=600.0, description="单定时任务执行超时(秒)")

    # ===== 断点续跑 checkpoint（期4 增量）=====
    # 开启后给 LangGraph 挂持久化 checkpointer（AsyncSqliteSaver），按 thread_id=task_id 落盘各节点状态；
    # 任务中断/出错后用同一 task_id 重跑即从断点恢复，不必从头再来。默认关闭（不改变现有运行行为）。
    checkpoint_enabled: bool = Field(default=False, description="是否启用 LangGraph 断点续跑检查点")
    checkpoint_db_path: str = Field(default="data/checkpoints.sqlite", description="检查点 SQLite 路径")

    # ===== 数据准备模式（v2，plan 数据获取与清洗专项）=====
    # 6B 决策：新任务默认走 data_prep；关闭则回退旧分析图（兼容回退用）。
    data_prep_mode_enabled: bool = Field(
        default=True,
        description="新任务是否默认走数据准备模式（data_prep）；关闭回退旧分析图",
    )
    # 原始制品保留天数（4B：默认 30 天可配置）
    data_prep_raw_retention_days: int = Field(default=30, ge=0, description="原始制品保留天数")
    # 单任务上传/抽取上限（2C 规模防护；GB 级需 Phase 5 外部队列）
    data_prep_max_task_bytes: int = Field(default=500 * 1024 * 1024, description="单任务最大字节数")
    data_prep_max_task_records: int = Field(default=1_000_000, description="单任务最大记录数")
    # 批次数据面：解析/清洗/导出按此记录数分批，避免全量加载（Phase 2 Task 2）
    data_prep_batch_records: int = Field(default=10_000, ge=1, description="单批次最大记录数")
    # 文件上传根目录与配额（Phase 2 Task 3）
    data_prep_upload_root: str = Field(default="data/uploads", description="上传文件存储根目录")
    semantic_execution_root: str = Field(
        default="data/semantic-executions",
        description="Phase 4B 确定性执行结果和血缘的服务端存储根目录",
    )
    data_prep_artifact_root: str = Field(
        default="downloads",
        description="数据准备任务产物和正式下载的存储根目录",
    )
    capability_oci_layout_path: str = Field(
        default="data/capabilities/oci",
        description="冻结能力包的本机 OCI Image Layout",
    )
    capability_platform_oci_layout_path: str = Field(
        default="data/capabilities/oci-platform",
        description="脱敏平台快照的独立 OCI Image Layout（与个人 Layout 分离）",
    )
    capability_platform_signing_private_key: str = Field(
        default="",
        description="平台签名私钥路径（加密 Sigstore 格式，位于项目与数据库之外）",
    )
    capability_platform_signing_public_key: str = Field(
        default="",
        description="平台签名公钥路径（与私钥同目录，.pub）",
    )
    capability_mount_cache_path: str = Field(
        default="data/capabilities/mounts",
        description="按 OCI digest 物化的业务只读能力目录",
    )
    capability_supply_chain_tool_root: str = Field(
        default="data/platform-tools/supply-chain",
        description="固定 Trivy 与 Syft 可执行文件根目录",
    )
    capability_supply_chain_evidence_root: str = Field(
        default="data/capability-governance/evidence",
        description="供应链原始受控证据目录",
    )
    capability_supply_chain_cache_root: str = Field(
        default="data/platform-tools/supply-chain/cache",
        description="供应链工具离线数据库与缓存目录",
    )
    capability_supply_chain_lock_path: str = Field(
        default="config/supply-chain-tools.lock.json",
        description="供应链工具版本与来源锁文件",
    )
    # Agentic Runtime vNext 灰度：Legacy 仍为工作台默认；管理员控制总开关，
    # 普通用户只有显式选择自己的或平台发布的连接后才能进入标准任务 Runtime。
    pi_runtime_enabled: bool = Field(
        default=True,
        description="是否允许创建 Pi Agentic Runtime 灰度任务",
    )
    pi_runtime_relay_base_url: str = Field(
        default="",
        description=(
            "任务容器访问内部模型 Relay 的根地址；为空时从 "
            "host.docker.internal 自动冻结一个私网 IPv4"
        ),
    )
    pi_runtime_document_relay_base_url: str = Field(
        default="",
        description=(
            "任务容器访问内部文档工具 Relay 的根地址；为空时从 "
            "host.docker.internal 自动冻结一个私网 IPv4"
        ),
    )
    pi_runtime_image: str = Field(
        default="mangrove/pi-coding-agent:0.80.10",
        description="任务级 Pi Runtime 固定镜像",
    )
    pi_capability_host_enabled: bool = Field(
        default=False,
        description="是否灰度启用任务级原生能力 Sidecar；关闭时现有 Pi 路径零变化",
    )
    pi_capability_host_image: str = Field(
        default="mangrove/pi-coding-agent:0.80.10",
        description="Capability Host 固定镜像",
    )
    pi_runtime_egress_image: str = Field(
        default="mangrove/smokescreen:da4840c9",
        description="Pi 任务级强制出站代理固定镜像",
    )
    pi_runtime_timeout_seconds: int = Field(
        default=1800,
        ge=60,
        description="单次 Pi Agent 执行硬超时秒数",
    )
    pi_runtime_memory: str = Field(
        default="8g",
        description="单个 Pi 任务容器内存上限（Docker 格式）",
    )
    pi_runtime_cpus: float = Field(
        default=4.0,
        gt=0,
        description="单个 Pi 任务容器 CPU 上限",
    )
    pi_runtime_context_window: int = Field(
        default=32768,
        ge=8192,
        description="提供给 Pi 的本地模型上下文窗口声明",
    )
    pi_runtime_max_tokens: int = Field(
        default=8192,
        ge=1024,
        description="Pi 单次本地模型最大输出 token",
    )
    semantic_compiler_timeout_seconds: int = Field(
        default=120,
        ge=10,
        description="语义计划单次模型调用超时秒数",
    )
    semantic_compiler_max_tokens: int = Field(
        default=8192,
        ge=1024,
        description="语义计划结构化输出 token 上限",
    )
    workspace_telemetry_enabled: bool = Field(
        default=False,
        description="是否把统一工作台低敏轨迹发送到 OTLP 接收端",
    )
    workspace_otlp_endpoint: str = Field(
        default="http://127.0.0.1:6006/v1/traces",
        description="统一工作台 OTLP HTTP traces 接收地址",
    )
    data_prep_max_upload_bytes: int = Field(
        default=500 * 1024 * 1024, ge=1, description="单个上传文件最大字节数"
    )
    # 预览限量（Phase 2 Task 10）：预览只读小样本，不拉全量
    data_prep_preview_max_bytes: int = Field(
        default=10 * 1024 * 1024, ge=1, description="预览文件最大字节数，超过返回 413"
    )
    data_prep_preview_max_records: int = Field(
        default=20, ge=1, description="预览返回的样本记录上限"
    )
    data_prep_http_private_host_allowlist: str = Field(
        default="",
        description="允许 HTTP API 访问的私网主机名/IP，逗号分隔；loopback 等硬黑名单仍拒绝",
    )
    # 扫描/混合 PDF 坐标型 OCR（Phase 4A）。当前本机开发环境使用 MinerU 3.4.4 HTTP 服务；
    # 数字 PDF 不调用该服务，服务失败时保留 ocr_required，不伪造解析成功。
    mineru_enabled: bool = Field(default=True, description="是否启用 MinerU 扫描 PDF 解析")
    mineru_base_url: str = Field(
        default="http://192.168.1.21:8000",
        description="MinerU HTTP 服务根地址",
    )
    mineru_backend: str = Field(
        default="pipeline",
        description="MinerU 解析后端；当前已验证 pipeline",
    )
    mineru_timeout_seconds: int = Field(
        default=600,
        ge=1,
        description="MinerU 单文档解析超时秒数",
    )
    # 文档解析服务主备顺序。PaddleOCR-VL 的 VLM 推理端点不是完整解析服务；
    # 只有提供 /layout-parsing 的 Pipeline 网关才能加入此主备链。
    document_parser_primary: str = Field(
        default="mineru",
        description="扫描文档首选服务：mineru | paddleocr_vl",
    )
    document_parser_secondary: str = Field(
        default="",
        description="扫描文档失败回退服务：空 | mineru | paddleocr_vl",
    )
    document_parser_fallback_enabled: bool = Field(
        default=True,
        description="首选文档服务失败或缺页时是否尝试备用服务",
    )
    document_parser_max_concurrency: int = Field(
        default=2,
        ge=1,
        le=16,
        description="单进程内 MinerU/Paddle 文档解析请求并发上限",
    )
    document_parser_page_retries: int = Field(
        default=1,
        ge=0,
        le=3,
        description="整份解析失败或缺页后，单页解析的重试次数",
    )
    document_parser_retry_backoff_seconds: float = Field(
        default=0.5,
        ge=0,
        le=30,
        description="单页解析重试的退避基数秒数",
    )
    # PaddleOCR-VL 官方完整产线服务；VLM 子服务地址单独记录，不能混填。
    paddleocr_vl_enabled: bool = Field(
        default=False,
        description="是否启用 PaddleOCR-VL 完整文档服务",
    )
    paddleocr_vl_base_url: str = Field(
        default="",
        description="PaddleOCR-VL 完整 Pipeline API 根地址，必须提供 /layout-parsing",
    )
    paddleocr_vl_vlm_base_url: str = Field(
        default="http://192.168.1.21:18080/v1",
        description="PaddleOCR-VL VLM 推理子服务地址，仅供官方 Pipeline 调用",
    )
    paddleocr_vl_endpoint: str = Field(
        default="/layout-parsing",
        description="PaddleOCR-VL 官方版面解析端点",
    )
    paddleocr_vl_model_version: str = Field(
        default="PaddleOCR-VL-1.6-0.9B",
        description="PaddleOCR-VL 服务实际加载的模型版本",
    )
    paddleocr_vl_api_key: str = Field(
        default="",
        description="PaddleOCR-VL Bearer Token；局域网无鉴权时留空",
    )
    paddleocr_vl_timeout_seconds: int = Field(
        default=600,
        ge=1,
        description="PaddleOCR-VL 单文档解析超时秒数",
    )
    # 数据库安全只读取数（Phase 3）：用户自有数据库的连接与抽取配置
    data_prep_db_batch_size: int = Field(
        default=5_000, ge=1, description="数据库读取每批记录数"
    )
    data_prep_db_query_timeout_seconds: int = Field(
        default=60, ge=1, description="每条 SELECT 语句的最大执行秒数"
    )
    data_prep_db_connect_timeout_seconds: int = Field(
        default=10, ge=1, description="数据库连接建立超时秒数"
    )
    data_prep_db_max_connections: int = Field(
        default=4, ge=1, description="并发数据库连接数上限"
    )
    data_prep_db_max_retries: int = Field(
        default=3, ge=0, description="断连重试上限；0 不重试"
    )
    data_prep_db_max_cell_bytes: int = Field(
        default=1 * 1024 * 1024, ge=1, description="单个单元格最大字节数，超限截断并告警"
    )
    data_prep_db_max_sample_rows: int = Field(
        default=20, ge=1, description="Schema 发现时返回的样本行数上限"
    )
    data_prep_db_max_discovery_tables: int = Field(
        default=500, ge=1, description="introspection 发现的最大表数，超限截断并告警"
    )
    # 私网数据库访问控制（Phase 3 安全接口）
    data_prep_db_host_allowlist: str = Field(
        default="",
        description="允许连接的数据库主机/IP，逗号分隔；非空时仅放行清单（生产加固开关）",
    )
    data_prep_db_allowed_ports: str = Field(
        default="3306,5432",
        description="允许连接的数据库端口，逗号分隔；防止把驱动当内网端口探测工具",
    )
    # 数据库 sqlite 来源路径限制（Phase 3 安全接口）
    data_prep_db_sqlite_root: str = Field(
        default="data/db_sources",
        description="sqlite 数据源根目录，仅允许该目录的相对路径，防止平台自用库被当源读",
    )
    # 密钥与受控 SQL（Phase 3 安全接口）
    data_prep_db_secret_key: str = Field(
        default="",
        description="数据库连接密码 Fernet 加密密钥；空则派生自 jwt_secret + 启动告警",
    )
    data_prep_db_custom_sql_enabled: bool = Field(
        default=False,
        description="是否允许受控自定义 SQL（mode=sql）；默认关闭，仅提供表级抽取",
    )

    # ===== 入库后端（新增）=====
    # db_backend: sqlite（默认，本地）| mysql（用 mysql_* 连接，PyMySQL）
    db_backend: str = Field(default="sqlite", description="入库后端：sqlite | mysql")
    mysql_host: str = Field(default="127.0.0.1", description="MySQL 主机")
    mysql_port: int = Field(default=3306, description="MySQL 端口")
    mysql_user: str = Field(default="root", description="MySQL 用户名")
    mysql_password: str = Field(default="", description="MySQL 密码（放 .env）")
    mysql_database: str = Field(default="mangrove", description="MySQL 数据库名")

    # ===== 邮件连接器（期4）：报告生成后可发邮件，走 HITL 确认 =====
    # 用 SMTP 发送；未配 smtp_host/smtp_user 时邮件产出不可用（output 会提示未配置）。
    smtp_enabled: bool = Field(default=True, description="是否启用邮件发送；关闭后即使已配置凭证也不会真的发送")
    smtp_host: str = Field(default="", description="SMTP 服务器地址，如 smtp.qq.com / smtp.gmail.com")
    smtp_port: int = Field(default=465, description="SMTP 端口：SSL 用 465，STARTTLS 用 587")
    smtp_user: str = Field(default="", description="SMTP 登录账号（通常即发件邮箱）")
    smtp_password: str = Field(default="", description="SMTP 密码或授权码（放 .env，勿硬编码）")
    smtp_from: str = Field(default="", description="发件人地址，留空则用 smtp_user")
    smtp_use_ssl: bool = Field(default=True, description="True 用 SSL(465)；False 用 STARTTLS(587)")

    # Slack 连接器（期4）：报告推送到 Slack 频道，走 HITL 确认。
    # 用 Incoming Webhook（频道由 Webhook 本身绑定）；留空则 Slack 产出不可用。
    slack_enabled: bool = Field(default=True, description="是否启用 Slack 通知；关闭后即使已配置 Webhook 也不会真的发送")
    slack_webhook_url: str = Field(default="", description="Slack Incoming Webhook URL（放 .env）；空则不用")

    # Agent 配置
    agent_max_iterations: int = Field(default=50, description="Agent 最大迭代次数")
    browser_use_max_iterations: int = Field(
        default=20,
        description="browser_use 模式最大迭代次数，防止无限循环；0 表示使用 agent_max_iterations"
    )
    agent_retry_count: int = Field(default=3, description="Agent 重试次数")
    agent_retry_delay: float = Field(default=1.0, description="重试延迟时间(秒)")
    
    # API 服务配置
    api_host: str = Field(default="0.0.0.0", description="API 服务主机")
    api_port: int = Field(default=8088, description="API 服务端口（Web 网关固定端口）")
    api_debug: bool = Field(default=False, description="调试模式")

    # ===== Web UI 网关（React 前端 + FastAPI 网关，多用户 + JWT 登录）=====
    # webui_db_path：用户与会话历史的 SQLite（与业务 app.db / scheduler.db 分开）。
    webui_db_path: str = Field(default="data/webui.db", description="Web UI 用户/会话 SQLite 路径")
    # jwt_secret：JWT 签名密钥。务必在 .env 覆盖为随机长串，否则用开发默认值（仅本地）。
    jwt_secret: str = Field(default="mangrove-dev-secret-change-me-in-production-please", description="JWT 签名密钥（放 .env）")
    jwt_expire_hours: int = Field(default=168, description="登录令牌有效期(小时)，默认 7 天")
    # 允许自助注册：False 时仅管理员可建账号（v1 默认开放，便于试用）。
    webui_allow_register: bool = Field(default=True, description="是否允许前端自助注册账号")
    # 前端开发服务器源（Vite dev 默认 5173），用于 CORS 放行；生产由网关同源托管时可忽略。
    webui_cors_origins: str = Field(default="http://localhost:5173,http://127.0.0.1:5173", description="允许的前端源，逗号分隔")
    
    # 文件资源服务：用于生成下载/截图等静态文件的绝对 URL
    # 留空时从请求中自动推断；部署时可设置如 https://your-domain.com
    file_resource_base_url: Optional[str] = Field(
        default=None,
        description="文件资源服务的根 URL，用于生成绝对下载地址。如 http://localhost:8000",
    )
    
    # 数据库配置
    database_url: str = Field(default="sqlite:///./data/app.db", description="数据库连接字符串")
    
    # 日志配置
    log_level: str = Field(default="INFO", description="日志级别")
    log_format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="日志格式"
    )
    log_file: Optional[str] = Field(default="logs/app.log", description="日志文件路径")
    logs_dir: str = Field(default="logs", description="工具结果保存目录")
    
    # 视觉点击（GUI-Owl）：根据截图由大模型识别点击坐标，再调用 click_at 点击；主要用途：点击页面广告
    vision_click_enabled: bool = Field(default=True, description="是否启用视觉点击（截图+大模型识别坐标+click_at）")
    vision_click_use_gui_owl: bool = Field(
        default=True,
        description="为 True 时视觉点击与弹窗检测使用下方 vision_click_* 专用端点（GUI-Owl）；为 False 时复用 llm_base_url / llm_model_name / llm_api_key / llm_timeout / llm_max_tokens，便于多模态主模型走同一网关",
    )
    vision_click_base_url: str = Field(
        default="http://192.168.1.21:4243/v1",
        description="视觉点击模型 API 根地址（OpenAI 兼容 /v1/chat/completions）",
    )
    vision_click_model: str = Field(
        default="/home/howso/model/GUI-Owl",
        description="视觉点击模型名称或路径，如 GUI-Owl",
    )
    vision_click_timeout: int = Field(default=60, description="视觉点击模型请求超时(秒)")
    vision_click_max_tokens: int = Field(default=512, description="视觉点击模型最大返回 token 数")
    vision_click_resize_before_send: bool = Field(
        default=True,
        description="发送前对截图做 smart_resize（Mobile-Agent 风格），减少 token、提升稳定性",
    )

    # 弹窗视觉检测：每步截图后调用视觉模型判断是否有遮挡式弹窗，复用 vision_click API
    popup_vision_detect_enabled: bool = Field(
        default=True,
        description="是否启用视觉弹窗检测（截图+大模型判断是否有弹窗），禁用则不再注入 popup_hint",
    )

    # 截图：MCP take_screenshot 最大等待时间（秒），超时则跳过本次截图（页面过大或 Chrome 忙时可调大或设为 0 不超时）
    screenshot_mcp_timeout: int = Field(default=45, description="截图 MCP 调用超时(秒)，0 表示不超时")
    # VL 模型专用：是否同时保存 PNG 截图（与步骤记录用的 JPEG 意义不同，供视觉语言模型读图用）
    screenshot_vl_png_enabled: bool = Field(default=False, description="为 VL 模型同时保存 PNG 截图(与步骤 JPEG 分开)")
    
    # 速率限制配置
    rate_limit_requests: int = Field(default=100, description="速率限制请求数")
    rate_limit_window: int = Field(default=60, description="速率限制时间窗口(秒)")
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# 全局配置实例
settings = Settings()


def resolve_vision_model_api_settings() -> Dict[str, Any]:
    """
    视觉点击与弹窗检测共用的 OpenAI 兼容 API 配置。
    vision_click_use_gui_owl 为 True 时使用 vision_click_*；否则复用主 LLM。
    """
    s = settings
    if s.vision_click_use_gui_owl:
        return {
            "base_url": (s.vision_click_base_url or "").rstrip("/"),
            "model": s.vision_click_model,
            "api_key": "",
            "timeout": s.vision_click_timeout,
            "max_tokens": s.vision_click_max_tokens,
        }
    return {
        "base_url": (s.llm_base_url or "").rstrip("/"),
        "model": s.llm_model_name,
        "api_key": (s.llm_api_key or ""),
        "timeout": s.llm_timeout,
        "max_tokens": s.llm_max_tokens,
    }

