# AC-07 能力验证、成熟度与平台发布增量规格

> 日期：2026-08-06
> 状态：`ac07_03_engineering_verified_pending_code_review_production_migration_and_user_acceptance`
> 前置：AC-06 工程门与用户工作台灰度验收已通过
> 决策：[ADR-0029](https://github.com/Eclipseic1848/Mangrove_platform/blob/v0.0.8/docs/adr/0029-capability-validation-lifecycle-and-platform-publication.md)
> 上游：[Agentic 能力获取、SOP 与对话上下文规格](https://github.com/Eclipseic1848/Mangrove_platform/blob/v0.0.8/docs/plans/2026-08-02-agentic-capability-sop-context-spec.md)
> 官方工具刷新：[AC-07 供应链工具官方事实刷新](https://github.com/Eclipseic1848/Mangrove_platform/blob/v0.0.8/docs/research/2026-08-06-ac07-supply-chain-tooling-official-refresh.md)
> GitHub 规格工单：[Issue #32](https://github.com/Eclipseic1848/Mangrove_platform/issues/32)
> 边界：本文和 Issue #32 本身不授权实施；后续用户已单独授权 #33 完整收口、#34 工程实现与
> 生产迁移，以及 #35 工程实现。#34 真实能力灰度闭环、#35 code-review/生产迁移/用户验收、
> #36～#44、平台发布或普通用户开放仍需各自人工控制。

## Problem Statement

Mangrove 已能让管理员把冻结的 Python Tool 与本地 MCP 装载进任务级 Capability Host，但当前
目录仍把“能力可以运行”“一次任务成功”“能力值得信任”和“能力可以共享给其他用户”混在一起。
平台缺少严格绑定 digest 的验证运行、供应链扫描、SBOM、签名、版本并行、紧急隔离、撤销和
管理员发布审计。一次成功如果直接改变全局状态，会让未证明失败关闭、可能携带个人数据或后来
出现漏洞的能力污染平台目录；反过来，完全依赖人工命令又无法形成普通管理员可操作、可解释、
可恢复的产品闭环。

AC-07 还必须保持现有任务可用：无能力任务不能新增启动负担，AC-06 管理员灰度不能在迁移期间
突然失效，历史 TaskRevision 不能随目录升级改变，普通用户权限也不能因平台发布功能上线而
被静默扩大。

## Solution

建立一个以 CapabilityGovernance 为主 Seam 的深 Module，把验证编排、三轴治理状态、供应链
证据、平台快照、管理员动作和审计隐藏在统一 Interface 后。Owner 主动验证自己的精确能力
digest；系统只有在合成 Smoke、授权真实任务重放、失败关闭与权限测试、来源一致性、Trivy
扫描和 Syft SBOM 全部通过后，才把该 digest 的个人成熟度投影为 verified。

管理员可以在现有设置区域查看脱敏审核证据，将 verified 个人能力复制为独立平台快照，重新
计算 digest、重测并用 Cosign 本地密钥签名。平台快照默认只进入 admin_gray；面向普通用户
开放是另一个显式动作。生命周期和运行资格与成熟度分离，使普通弃用、紧急撤销和自动安全
隔离具有不同效果。所有治理命令幂等、可审计，历史记录与冻结任务引用不可改写。

## User Stories

1. As an 能力 Owner, I want 一次任务成功后能力仍保持草稿, so that 偶然成功不会被误认为已经安全可靠。
2. As an 能力 Owner, I want 主动发起能力验证, so that 系统不会在我不知情时重放个人任务。
3. As an 能力 Owner, I want 看到验证需要哪些证据及当前缺口, so that 我知道为什么能力尚未 verified。
4. As an 能力 Owner, I want 在授权仍有效时复用冻结任务重放, so that 升级能力时不必重复上传相同来源。
5. As an 能力 Owner, I want 新 digest 重新验证, so that 旧版本的安全结论不会掩盖新依赖或新代码风险。
6. As an 能力 Owner, I want 验证失败只留下失败记录并保持草稿, so that 历史能力版本不会被失败运行覆盖。
7. As an 能力 Owner, I want 看到 Trivy 扫描和 SBOM 的用户可理解摘要, so that 我能决定升级、修复或停止使用。
8. As an 能力 Owner, I want 只能把 verified 版本提交平台审核, so that 不完整候选不会进入管理员发布队列。
9. As an 管理员, I want 查看跨 Owner 的任务管理信息, so that 我能定位验证失败、资源异常和平台风险。
10. As an 管理员, I want 审核时默认只看到脱敏证据, so that 日常治理不需要暴露个人业务正文。
11. As an 管理员, I want 在确有排障或审核需要时显式审计查看业务内容, so that 我能完成调查并留下读取原因和对象。
12. As an 管理员, I want 查看来源、版本、digest、权限、网络、资源、扫描、SBOM 和三类验证证据, so that 发布决定有完整依据。
13. As an 管理员, I want 从 verified 个人版本生成独立平台快照, so that 个人后续修改或删除不会改变平台资产。
14. As an 管理员, I want 发布前重新验证脱敏平台快照, so that 脱敏和重打包没有改变能力行为或引入风险。
15. As an 管理员, I want 使用平台本地密钥签名确切 digest, so that 装载时能发现内容被替换。
16. As an 管理员, I want 平台发布默认只进入管理员灰度, so that 发布能力不会同时扩大普通用户权限。
17. As an 管理员, I want 单独开放或关闭普通用户受众, so that 权限扩大是明确、可审计的动作。
18. As an 管理员, I want 弃用被新版替代但仍安全的版本, so that 新任务不再推荐旧版而历史任务仍可恢复。
19. As an 管理员, I want 撤销存在严重安全问题的版本, so that 新任务、重试和恢复不能继续执行危险 digest。
20. As an 管理员, I want 把新任务推荐版本回滚到仍安全的旧版本, so that 回滚不会修改历史 TaskRevision。
21. As an 管理员, I want 对无修复且路径不可达的 High 漏洞作限期风险接受, so that 可控灰度不会被上游暂时无法修复的问题永久阻塞。
22. As an 管理员, I want 风险接受到期后自动重新隔离, so that 临时例外不会演变成永久忽略。
23. As an 管理员, I want 扫描器失败或漏洞库过期时禁止新晋级和发布, so that 缺少安全证据不会被当成扫描通过。
24. As an 管理员, I want 漏洞库暂时过期时已有灰度任务仍可运行, so that 网络中断不会无故破坏当前本地任务。
25. As an 管理员, I want 新 Critical、Secret、签名失效或越权证据自动触发隔离, so that 平台可以先止损再等待治理决定。
26. As an 管理员, I want 所有发布、弃用、回滚、撤销、恢复和风险接受都记录原因, so that 后续可以解释每次信任变化。
27. As an 普通用户, I want 看不到管理员能力治理入口, so that 我不能越权操作平台能力。
28. As an 普通用户, I want 只能选择明确开放给普通用户的平台能力, so that admin_gray 不会泄漏到个人任务。
29. As an 任务 Owner, I want 已冻结的能力 digest 不因平台升级而改变, so that 历史结果、恢复和审计保持可重放。
30. As an 任务 Owner, I want 被安全撤销的能力在重试或恢复前明确失败, so that 系统不会静默换成另一个版本并改变结果语义。
31. As an 平台维护者, I want 无能力任务完全绕过供应链治理运行路径, so that AC-07 不降低现有正常任务的可靠性和启动速度。
32. As an 平台维护者, I want AC-06 灰度在 AC-07 切换前继续使用旧路径, so that 两项真实能力完成新验证前不会出现服务中断。
33. As an 平台维护者, I want Python Tool 与 Everything MCP 各走一条真实闭环, so that 普通命令能力和协议型常驻能力都得到治理证据。
34. As an 平台维护者, I want Node、CLI 和 Skill 复用统一治理契约回归, so that 不重复建设 AC-06 已完成的 Adapter 验收。
35. As an 审计人员, I want 历史验证、SBOM、扫描、签名和治理事件不可覆盖, so that 当前投影可以追溯到每一次事实变化。

## Implementation Decisions

### 1. 主 Module 与 Interface

- 新建 CapabilityGovernance 深 Module，作为验证、成熟度、生命周期、运行资格、平台快照和
  治理审计的唯一写入口；调用者不直接组合扫描器、签名器、Repository 或 OCI 命令。
- 主 Interface 提供四类命令：请求/读取验证、生成/发布平台快照、改变治理状态、读取审核投影。
  命令必须携带 Actor、精确 PackRef、预期当前状态、原因和幂等键；结果返回不可变记录及当前投影。
- CapabilityCatalog 继续负责目录身份、Owner 可见性和 TaskRevision 冻结；它不能自行晋级或
  发布。CapabilityGovernance 通过内部发布 Adapter 写入平台快照，普通登记入口继续拒绝平台写入。
- AutomationProcedure 复用三轴状态和验证引用的 Schema，但 AC-07 不开放其验证、发布或选择
  命令；相关产品行为留给 AC-08。

### 2. 不可变身份与三轴状态

- Pack 内容、版本和 OCI digest 不可覆盖。现有嵌入 Pack 的旧 maturity 只作为迁移输入；新
  运行门读取按 digest 聚合的治理投影，不能把修改 Pack JSON 当成状态迁移。
- 成熟度只允许 `draft | verified`，生命周期只允许 `active | deprecated | revoked`，运行资格
  只允许 `eligible | quarantined`。
- `deprecated` 不进入新任务推荐，但历史冻结任务和恢复任务可以继续使用；`revoked` 禁止新任务、
  重试和恢复。`quarantined` 是自动或人工安全刹车，不伪造管理员撤销。
- 推荐版本是独立指针。回滚只原子改变新任务推荐指针，目标必须仍为 verified、active、eligible
  且满足受众和签名门；历史 TaskRevision 不变。

### 3. CapabilityValidationRun

- 每次验证运行严格绑定 Owner、Pack ID、version、digest、触发 Actor、创建时间和幂等键。
- 验证运行分别保存：合成 Smoke、Owner 真实 TaskRef 重放、失败关闭与权限测试、来源与锁文件
  一致性、Trivy 扫描、Syft SBOM、Verifier 摘要和清理结果。证据只保存受控引用和摘要，不复制
  个人业务正文、Secret、宿主路径或原始工具日志。
- Owner 必须显式发起真实任务重放。TaskRef 的 Owner、来源快照、输入 hash、任务 revision、
  Candidate/Delivery hash 和能力 digest 全部复核；授权撤销、来源缺失或无法重开时失败关闭。
- 单次业务成功只可以成为一条真实任务证据，不自动创建 verified 投影。全部硬门通过后，
  CapabilityGovernance 确定性晋级同一 digest；失败只形成不可变失败运行。
- 相同幂等键重复请求返回同一运行；同 digest 的并发验证由持久化 Lease 合并，服务恢复后从已
  完成步骤继续，不能重复发布或覆盖证据。

### 4. 供应链扫描、SBOM 与安全策略

- AC-07 PoC 固定 Trivy `0.70.0`、Syft `1.50.0`、Cosign `3.0.6` 和 ORAS `1.3.2`；实现
  必须通过官方 checksum/签名建立工具锁定清单，禁止 `latest`、移动 tag、动态安装脚本或
  未验证的社区分发替代。扫描器自身的来源证据不能弱于普通能力包。
- Trivy 直接扫描最终能力目录或镜像；不能只扫描 Syft 生成的 SBOM。扫描记录固定工具版本、
  漏洞库版本/更新时间、配置、目标 digest、结果 JSON hash 和摘要。
- Secret、Critical、签名失效、未声明权限、路径逃逸、Docker Socket、跨 Owner 读取和隔离突破
  是不可例外硬门。已有修复版本的 High 必须升级。
- 无修复且经失败关闭证据证明风险路径不可达的 High，可以由管理员在 admin_gray 对确切 digest
  作限期风险接受；默认 30 天、最长 90 天，不能自动续期，到期转为 quarantined。
- Medium、Low 和 Unknown 留证但不自动阻断。扫描器异常或数据库不可判定不能视为通过。
- Trivy 漏洞库有效期为 7 天，并以数据库内容的 `UpdatedAt` 而非搬运时间 `DownloadedAt`
  计算。过期时既有 eligible admin_gray 任务可继续运行，但新验证不能晋级、平台快照不能
  发布、隔离不能解除。数据库刷新只在治理环境使用批准网络，不进入业务 Sidecar。
- verified 和平台候选必须由 Syft 生成标准 SBOM，并保存格式、工具版本、主体 digest 和 SBOM
  hash。内部保留完整 Syft JSON，并生成固定 CycloneDX JSON 1.6 作为可移植 SBOM；SBOM 是
  资产清单，不替代 Trivy 直接扫描和运行验证。
- 已发布平台 digest 在漏洞库刷新后进入有界重扫。新硬门命中先形成 quarantined 投影；活动任务
  在下一次能力调用或安全原子步骤处失败关闭，不发布未验证结果，管理员再决定修复、撤销或允许
  符合条件的限期 High 例外。

### 5. 平台快照与签名

- 只有 verified、active、eligible 的个人 Pack 才能提交平台候选。平台候选必须删除 Owner、
  个人 TaskRef、原文件名、业务字段值、宿主路径、Token、连接和个人配置，再生成独立 OCI 内容
  与新 digest。
- 脱敏后重新执行合成 Smoke、失败关闭、安全扫描和装载探针；不能复用个人 digest 的签名或
  假定脱敏不会改变行为。
- Cosign 首版使用项目目录和数据库之外的本地加密私钥。私钥不进入 Git、数据库、任务、日志、
  Prompt、事件或容器 argv；发布 Adapter 只返回签名引用、公钥身份和验证摘要。
- 官方资料未证明 Cosign 可以直接把标准 image signature 写入任意本地 OCI Layout。首选待验证
  路径是：ORAS 把主体复制到只监听 `127.0.0.1` 的短期 Registry，Cosign 按 digest 签名/验证，
  再由 ORAS 递归复制主体与 referrers 回独立平台 Layout。Registry 使用独立存储并在事务结束、
  取消或恢复时清理，不能升级为常驻服务或开放 LAN。
- 若上述 PoC 失败，只能暂停并报告；`cosign sign-blob` 是语义不同的 manifest blob 签名，未经
  用户确认不得替代标准 OCI image signature，也不得冒充同一验收结果。
- 装载平台 Pack 前验证签名和目标 digest。私钥缺失只阻止新发布，公钥/签名缺失或校验失败则
  阻止该平台 digest 装载并触发隔离。
- 平台快照初始受众固定为 admin_gray。面向普通用户开放是独立治理命令，必须重新检查当前扫描、
  签名、生命周期和运行资格；不自动设为推荐版本。

### 6. 管理员权限与审计查看

- 普通用户只能查看和验证自己拥有的个人 Pack；不能查看其他 Owner 的 Pack、ValidationRun、
  TaskRef、审核投影或治理事件。
- 管理员和超级管理员沿用同一能力治理权限。管理员默认可查看与能力候选相关的跨 Owner 任务
  管理信息，包括任务身份、Owner、状态、时间、输入/输出类型与数量、资源和验证摘要。
- 读取个人 Prompt、来源正文、Candidate 或 Delivery 内容必须调用独立审计查看命令，填写非空
  原因，并记录 actor、时间、任务、对象、用途和结果；列表 Interface 不返回业务正文，也不提供
  批量导出。
- 发布、弃用、回滚、撤销、恢复、风险接受和普通用户开放都要求原因并写不可变审计事件。
  Agent、任务成功、后台扫描和普通用户不能代替管理员执行这些治理动作。

### 7. 产品 Interface 与设置体验

- 在现有管理员设置分区增加“能力治理”，不新增一级导航。普通用户既看不到入口，直接调用
  管理员 Interface 也必须得到 403。
- 页面按“待验证个人能力、平台候选、管理员灰度、已弃用/撤销”组织，默认显示结论和缺口；
  来源、权限、网络、SBOM、扫描、签名、真实重放和审计历史渐进展开。
- 高风险命令使用明确动作名称和影响摘要，要求填写原因；风险接受还要求到期时间和范围。页面
  不使用 Emoji，状态不能只依赖颜色，键盘、屏幕阅读器、深浅主题和 reduced-motion 纳入验收。
- AC-07 只提供与验证证据关联的任务管理信息和审计查看，不建设通用跨用户任务管理中心；完整
  自动化方案库和审核队列留给 AC-09。
- 产品 Interface 使用独立 Owner 与管理员路由组，至少覆盖：目录/详情、发起和读取验证、提交
  平台候选、读取审核投影、发布、弃用、回滚、撤销、恢复、风险接受、受众变更和审计查看。
  写命令要求幂等键与预期状态，状态冲突返回 409，不以最后写入覆盖并发决策。

### 8. Runtime 强制门

- CapabilityMountResolver 是运行时唯一装载 Seam。个人 Pack 必须 verified、active、eligible
  且属于当前 Owner；平台 Pack 还必须满足受众和 Cosign 签名。
- 创建、重试、恢复和每次原生能力调用都不能绕过运行门。运行中被隔离或撤销时，当前原子调用
  完成后停止后续调用；若当前调用已违反硬门，立即取消并禁止发布结果。
- deprecated 只允许已经冻结该 digest 的历史任务继续装载，不能出现在新任务选择列表。
- 无能力选择的任务不创建治理运行、不调用扫描器、不创建 Sidecar；AC-07 不改变现有默认入口、
  Pi Capability Host 默认开关或普通用户权限。

### 9. 迁移与兼容

- 数据库只新增治理事件、当前投影、验证运行/步骤、供应链证据、平台快照、签名引用、风险接受、
  推荐指针、审计查看和持久化 Lease 所需结构；迁移幂等、执行前备份，不物理删除旧行。
- 两项 AC-06 管理员灰度 Pack 不因用户验收自动成为 AC-07 verified。切换期间继续使用旧灰度
  选择路径；待两项 Pack 各自通过 AC-07 真实验证并形成治理投影后，再切换管理员灰度读取，
  因此迁移不得造成现有能力任务停机。
- 旧 Pack payload 中的 maturity 保留用于读取兼容，AC-07 运行资格以新治理投影为权威。迁移
  不修改旧 digest、TaskRevision Selection、OCI 制品或历史任务。
- 任何迁移执行、工具下载、密钥生成、真实平台发布和普通用户开放都必须在实施阶段单独展示
  目标与验证计划后执行。
- 本机 PoC 必须验证四个工具发布物、两类真实 SBOM/扫描、DB 时间、回环 Registry 签名闭环、
  私钥与业务数据零泄漏，以及崩溃/取消/重复执行后的 Registry、容器、网络和临时挂载零残留。

### 10. 真实纵切面

- Python 表格汇总 Tool 和 Everything MCP 各完成：个人草稿、三类验证、verified、并行升级、
  平台脱敏快照、Trivy、Syft、Cosign、admin_gray 发布、历史冻结、弃用、回滚、隔离、撤销、
  跨用户拒绝和资源清理。
- Node、CLI、无脚本 Skill 和协议差异通过同一 CapabilityGovernance 与 Runtime Interface 的
  冻结契约回归，不重新建设或下载一套 AC-06 Adapter 样本。

## Testing Decisions

### 测试原则

- 只断言 Interface 的外部可观察结果：状态、证据、权限、审计、装载、任务结果和资源清理；
  不测试私有函数、具体命令顺序、Prompt 文案、内部表行数或缓存目录布局。
- 业务期望来自冻结夹具、AC-06 真实样本、Owner 授权任务和独立 Verifier；实现不能用自己生成
  的摘要证明自己正确。
- 所有权限、安全、签名、digest 和状态冲突失败关闭；错误不能降级为旧灰度路径或静默换版本。

### S1：CapabilityGovernance 主 Seam

- 覆盖单次成功不晋级、证据缺失、digest 变化、任务授权撤销、成功晋级和失败不可变记录。
- 覆盖三轴状态的合法/非法转换、自动隔离与人工治理分离、限期风险接受和到期重新隔离。
- 覆盖脱敏平台快照、新 digest、签名、发布、受众、弃用、回滚、撤销、并发幂等和跨 Owner 拒绝。
- 使用内存、SQLite 及真实工具 Adapter 至少两种实现证明 Repository/工具 Seam 真实存在。

### S2：CapabilityMountResolver 运行门

- 覆盖 draft、quarantined、revoked、签名失败、受众不匹配和跨 Owner 全部拒绝。
- 覆盖 deprecated 历史任务可重放、新任务不可选择；推荐指针回滚不改变旧 TaskRevision。
- 覆盖运行中隔离后的下一次能力调用失败关闭、Candidate 不发布以及 Sidecar 清理。
- 覆盖无能力任务和 AC-06 未切换路径的零回归。

### S3：HTTP 产品 Interface

- 覆盖 Owner、普通用户、管理员、超级管理员和跨 Owner 权限矩阵。
- 覆盖审计查看必须原因、业务正文不进入列表、所有治理写命令的 actor/reason/time/scope 审计。
- 覆盖幂等键重放、预期状态冲突、服务重启恢复和数据库迁移前后兼容。

### S4：浏览器体验

- 覆盖管理员能力治理入口、渐进披露、缺口解释、危险动作确认、审计查看和受众状态。
- 覆盖普通用户不可见、直接路由保护、键盘/屏幕阅读器名称、深浅主题、1366 宽度和
  reduced-motion。
- 使用 Python Tool 与 Everything MCP 从设置到真实任务装载完成两条闭环；不得只检查页面
  能打开或按钮可点击。

### 风险相称的验证门

- 每个纵向子工单先写对应 Seam 的失败测试，再做最小实现；聚焦回归通过后运行全部已完成
  CapabilityCatalog、Acquisition、Adapter、Host、Pi Runtime、工作台和设置权限集合。
- 迁移必须在生产库副本执行备份、前向迁移、重复迁移、旧数据零改写和恢复演练。
- 真实工具门必须记录版本、digest、扫描数据库时间、SBOM hash、签名验证、耗时和残留资源；
  最终用户验收仍由管理员在 8088 完成。

## Out of Scope

- AutomationProcedure 的蒸馏、组合、选择、失败学习和实际发布；属于 AC-08。
- 完整自动化方案一级导航、平台方案库、审核队列和新手引导；属于 AC-09。
- 面向普通用户自动开放、默认推荐或默认入口切换。
- 双人审批、团队角色、组织/团队作用域、计费、评分、市场和自动更新。
- 远程 MCP、OAuth、外部 Provider Secret、新外发目标和 MCP Registry 自动执行。
- Cosign keyless、云 KMS、Fulcio、Rekor、TUF、完整 in-toto Layout、SLSA 等级声明和复杂
  Rego/CUE/VEX 策略引擎。
- 跨主机 OCI Registry、远程 BuildKit、Kubernetes admission、服务器并发和 8B 部署门。
- 物理删除历史版本、验证、SBOM、签名、审计或能力缓存。
- 通用跨用户任务管理中心和业务数据批量导出。
- 除已创建的权威规格 Issue #32 外，其他 GitHub Issue 写入、提交、推送、版本、标签、
  Release、外部内容发布和普通用户权限扩大。

## Further Notes

- AC-07 的“平台发布”是 Mangrove 内部信任跃迁，不等于正式业务 Delivery，也不绕过
  ADR-0019 的 Candidate → Verifier → Delivery Publisher。
- `verified` 只表示该精确 digest 在记录的工具、漏洞库、环境和证据下通过，不声称能力绝对
  安全，也不代表 SLSA 合规。
- 当前工作树包含与本专项无关的本地配置和运行时 lessons/templates 变化；实施必须使用精确
  白名单，不得覆盖、删除或提交这些文件。
- GitHub Issue #32 是 AC-07 权威规格工单；#8 是历史 Skill Draft 治理，#12 是决策地图，均不能
  替代后续 `to-tickets` 生成的有界实施票。
- `to-tickets` 已按依赖顺序生成 #33～#44；#33 已完成并关闭，#34、#35 的依赖已解除，#36
  Cosign 本地 OCI 签名路径 PoC 仍是未开始的独立无阻塞票。拆票不授权实现、工具下载、密钥
  生成或平台发布。
- #33 已完成 TDD 工程实现、独立 code-review 修复和完整自动化门，证据见
[AC-07-01 执行报告](2026-08-06-agentic-capability-ac07-01-execution-report.md)。用户已于 2026-08-07
完成 8088 验收并授权完成带备份生产迁移；功能提交 `4dd40e9d` 已推送，Issue #33 已关闭。
这不授权自动进入 #34/#35/#36。

- 用户随后已明确授权 #34。可恢复 ValidationRun、真实 Pi 冻结任务重放、独立 Verifier、
  持久化幂等与 Lease、取消/恢复和失败关闭清理已完成工程实现与双轴 code-review；全仓后端
  1243 passed/4 skipped、完整 Playwright 54 passed、生产构建通过。带备份生产迁移已完成；
  8088 用户灰度、提交推送和 Issue 关闭尚未执行，不得把该状态升级为能力已 `verified`。证据见
  [AC-07-02 执行报告](2026-08-07-agentic-capability-ac07-02-execution-report.md)。

- 用户于 2026-08-07 授权继续 #35。固定 Trivy 0.70.0 与 Syft 1.50.0 的最终目录扫描、
  双格式 SBOM、七天漏洞库门、不可变证据 Repository、Owner/管理员脱敏读取和设置页摘要已完成
  工程实现；两份 AC-06 冻结能力包真实扫描通过。状态为
  `engineering_verified_pending_code_review_production_migration_and_user_acceptance`，生产库未迁移、
  能力未晋级、平台未签名。证据见
  [AC-07-03 执行报告](2026-08-07-agentic-capability-ac07-03-execution-report.md)。
