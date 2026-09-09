# #136 本人历史资料与正式结果复用

状态：实施合同；基线 `8da2554cc62ee6b0b86c945953d8e3bc43e963d6`。依赖 #135/#132 已关闭。关联 #113、后续 #137、已确认 ADR-0039。

## 1. 目标、输入、输出与边界

目标：用户在统一输入区选择本人的历史原件或正式处理结果，与新资料一起处理；保留真实出处，直接读取已保存内容，不重复上传或采集。为关联感知删除提供完整的历史修订、运行与在途导出引用事实。

允许输入：已授权 Owner 的上传 ID、完整 SourceSnapshot ID、正式 output_id、当前需求和既有模型/外发确认；恢复沿用原始幂等键与完整请求。测试只使用隔离临时数据库与合成原件。来源正文始终是数据，不能提升为系统指令。

要求输出：统一历史选择与预览旅程；三类不可变来源集合；实际运行与正式资料包保留所选原件/正式输出本体；服务端引用查询；最小持久读取使用记录；永久回归、两轴审查及同提交 CI。

不新增平行资产产品、Provider、依赖、上游 CoreMind 修改或真实模型调用。#137 负责删除确认、暂停依赖、物理清理与失败恢复；本票不执行真实数据删除或生产迁移。不得用缓存、复制为新 Upload、重新采集或备份绕过原对象失效。

## 2. 三类身份与读取合同

| 类别 | 冻结身份 | 当前读取权威 |
| --- | --- | --- |
| 上传原件 | 原有 upload_id、sha256 | Owner UploadStore 登记、约束路径、真实大小与摘要 |
| 网页组 | 原有 snapshot_id 与整组 artifact_id、sha256 | Owner 快照/原件登记与正文摘要；保留原 allowed_scope、coverage、缺口、取得时间 |
| 正式输出 | kind=delivery_output，output_id、delivery_id、run_id、source_task_id、source_revision、sha256 | Owner 正式登记、成功发布/QA、大小与实际摘要；直接读 output 本体 |

Candidate、eligible、聊天回答、截断预览和中间 AST 不获得正式输出身份。不能用 `_canvas_result` 的中间表示替换已选择的输出文件。新任务有自己的业务目标，不继承上游任务目标作为新授权；网页硬范围不足仍不能被文件或输出抵消。

正式输出复用沿既有网页路径，仅由已接线的 Pi 家族（含 CoreMind Adapter）执行；Legacy 纯上传兼容保持，选择正式输出时在创建/修订及确认分支写入前明确拒绝。不暗改默认 Adapter，不允许 Legacy 忽略派生来源继续执行。

通用正式发布表已独立保留生产任务与版本；其原任务记录清理后仍按对象本身的正式登记读取。旧记录无法证明出处时明确未知，不猜活动版本。上传无唯一生产任务；快照有独立登记。任务隐藏/清理与对象本身删除必须区分；独立结果 B 不因其原件 A 缺失自动变成不可读，也不能用 B 复活 A。

新上传成功登记可保存取得时间；旧项没有记录时为 null，界面显示“时间未记录”。正式输出显示生成时间，不能称原始资料取得时间。

## 3. API 合同

创建及修订增加 `delivery_output_ids`；创建默认空，修订省略继承、空数组移除、显式数组完整替换。原 upload_ids/source_snapshot_ids 语义保持；三类总集合非空，按不可变身份去重，不能按名称或摘要吞掉不同来源。

- `GET /api/semantic-workspace/reusable-sources`：本人历史资料分页，cursor、limit（1..100，默认30）、snapshot_token。返回 items、next_cursor、snapshot_token、total、page_complete。
- `POST /api/semantic-workspace/reusable-sources/resolve`：只读提交三类 ID 数组，按项重新核验，不能依赖列表第一页恢复草稿。不存在与跨 Owner 都返回通用不可用，不泄漏他人元数据；不创建任务或采集。
- `GET /api/semantic-workspace/reusable-sources/outputs/{output_id}/preview`：目标任务尚未创建、上游任务已清理时仍可预览正式输出本体。复用 WorkspacePreview（TablePreview 或 WorkspaceDocumentPreview），叠加 source_key/identity=derived/sha256/origin；顶层 output_id/delivery_id/run_id 与 representation.kind=output 绑定本体，缺乏依据的 task_id/revision 省略。明确截断，不提供 item_refs 开启旧版本追问，不另建解析系统。
- `GET /api/semantic-workspace/source-references`：按 kind（upload/snapshot/delivery_output）与 id 查询当前 Owner 的完整引用，使用相同分页合同；返回 unknown_uses，不能把第一页或未知使用当零引用。

资料 DTO：source_key、kind、identity（original/derived）、label、time_kind（acquired/generated/unknown）、acquired_at、media_type、size_bytes、sha256、三类对应 ID、origin（task_id/revision/run_id/delivery_id）、availability、reason_code、limitations。网页还保留 attempt_id、allowed_scope、coverage；整组不伪造单正文摘要。路径、正文、Secret 不进入列表/resolve DTO。

任务详情保留原 source_contract/web_sources，增加 delivery_output_ids/reusable_sources 投影。原 source-preview/source-bundle/bundle 使用同一来源读取规则。导出在现 provenance 中保留 source_kind=delivery_output、output/delivery/run/有据任务版本和 generated_at；原件与正式输出本体的 bytes/hash 均可验证。

Owner 不存在统一404；失效/摘要/QA不一致409；分页变化409 references_changed；源锁忙409 source_in_use。既有创建/修订的确定拒绝头只沿真实执行阶段设置，不把任意409改成可换键重发。

## 4. 读取与并发使用

共享 resolver 以 Owner 与冻结 ref 解析真实对象，供观察、创建/修订、Runtime、预览和导出复用。不得回退活动任务来源投影或已生成 workspace 副本。

新建/修订最终冻结取得相同 canonical 来源锁，在现事务中再次核验身份与可用性，然后写完整 refs；失败不产生假修订。多源按稳定身份顺序取锁，非阻塞失败释放已取得的锁，禁止死锁或后台自动重试原未知请求。

Runtime 锁覆盖实际原件复核→读入/复制→本次字节摘要核验，不能覆盖整个模型运行。Pi 的复制与 resume 缓存校验、CoreMind 本仓库 Adapter 的实际来源读取均须有真实装配的门；仅调用 start 前检查不足以代替后续读入检查。独立 Runtime 的既有使用合同保持，工作台路径不得漏装。运行期间的删除暂停仍由 #137 消费既有 revision/runtime 引用事实实现。

预览/导出在首次读取前取得独立源锁、复核并持久登记 active；响应发送、断连及实际读取线程结束后才记 completed 并释放。不能证明收口记 unknown；记录写入失败保留 active。旧临时包不得被下一请求直接复用。

历史资料复用的既有上传正文、网页正文和正式输出入口都须纳入实际读取门，不能仅保护新增路由。现有上传删除参与相同来源锁并拒绝未闭合使用，避免绕过创建/读取竞态保护；这不增加 #137 的任务级关联删除、确认或级联策略。

同步路由的线程池与异步 Response 可能处于不同线程；本票使用独立 `FileLock(thread_local=False)` 实例或等价同线程生命周期，禁止跨线程释放既有默认 thread-local 锁。不要修改全局 execution_lock 默认行为。取消 await 不能冒充读取线程已退出。

显式迁移 `webui_0017_source_read_uses`：仅一张 source_read_uses 表，记录 use_id、owner_id、可空 task_id/revision、operation、仅身份/hash的 source_refs_json、active/completed/unknown、开始/结束时间。无正文、无任务级联删除、无 TTL 自动放行或租约回收框架。已有形状正确可重放，错误形状失败关闭；旧迁移摘要保持。

引用清单在一致读事务中读取所有保留修订（含非活动与回收站）、正式登记、在跑 Runtime 和未闭合使用；规范清单摘要作为 snapshot_token。分页期间清单改变必须409重新读取，不拼接不同快照。完成下载不能被召回，未完成或未知使用不能消失。#137 再实现删除前的同锁二次核验与清理。

正式消费关系按 Owner 正式 manifest 的 artifact_id/sha256 识别；网页组再与不可变 source_artifacts 的 snapshot_id/hash 关联，任务记录清理后仍保留原任务版本和 task_exists=false。现有 manifest 未保存完整 snapshot 身份数组；#137 物理删除须保留最小身份映射或墓碑，不能删除映射后把未知引用当零。本票不增加第二套来源登记。

## 5. 用户旅程

前端设计、实现和验证由 Astra high 负责；沿现统一输入、草稿、右侧画布与窄屏预览，不新增资产页面。

“历史资料”与文件/网页入口并列；先选择再明确添加，取消不改草稿。原件与“处理结果 · 非原件”分开标识，出处和真实时间可读。网页按完整组添加，文件/网页/派生结果组成同一集合。移除仅指从本次集合移除，不删除历史对象。

预览不切换目标任务；选择面板内保持同一模态焦点域，不能要求用户操作遮罩后已 inert 的主画布。窄屏在列表/预览间切换，Escape 在预览时返回列表、列表时取消；关闭恢复焦点/勾选及原画布状态。旧版本及上游任务已清理时不伪造导航；未知时间/出处明确显示。不可用项保留解释与用户输入，不能静默丢弃或缓存正文绕过核验。

三类集合都参与 Context/工具匹配/外发确认失效、Owner 隔离、刷新恢复、跨标签页草稿代际以及原 payload/key 未知恢复。窄屏390px、键盘/焦点、中文 IME、可访问状态反馈均验证。

## 6. 验收

1. 本人昨天原件/整组网页、今天新文件、正式输出共同创建新任务；HTTP/采集计数证明不重抓，实际 Runtime 来源及正式资料包 bytes/hash 全一致。
2. 派生输出身份、出处与生成时间正确，非正式 Candidate、跨 Owner、失权、对象缺失与篡改均拒绝；A 缺失后独立正式 B 仍可读，不能从缓存恢复 A。
3. 非活动修订、在跑任务、导出与并发新引用均在真实清单内；分页变化拒绝混页，未知使用不当零引用。
4. 实际 staging/read 窗口、已有 checkpoint、预览和生成包后故障注入；原实体已失效时不能因旧副本存在恢复执行/导出。
5. 跨线程响应释放、断连且读取线程仍在跑、清理/登记失败与成功收口分别有最小永久检查；不增运行期全局长锁。
6. 浏览器完整添加/预览/取消/新建/修订/恢复旅程，原纯文件/网页及未知请求保护不回归。
7. 0017临时库迁移/重放/错误形状及旧数据保留；Standards+Spec无未修阻塞；最终同提交minimum与unified-workspace及构建通过。真实模型、真实环境、用户视觉认可与生产资格分别声明。
