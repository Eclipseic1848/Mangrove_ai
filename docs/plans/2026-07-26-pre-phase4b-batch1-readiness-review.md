# Phase 4B 批次 1 开发前问题复盘与就绪性评审

> 评审日期：2026-07-26（MinerU 任务时间为服务端 UTC 2026-07-27）
>
> 取证基线：`feature/phase4b-semantic-harness` /
> `b6f78ea9c352964b0236aa677199b2f567c61673`
>
> 当前发布状态：用户已明确授权从文档复盘提交 `b43c948` 建立 `v0.0.6`
> 开发版本分支；尚未创建同名标签
>
> 结论：**可以进入批次 1，但 Phase 4B 仍只是契约与评测基础，不是生产 Harness。**

## 1. 总结

当前没有阻止批次 1 开发的 P0 回归。Phase 4A 的稳定生产路线仍然成立：

- 数字 PDF/DOCX 使用确定性解析；
- 扫描/混合 PDF 默认使用 MinerU 3.4.4 `pipeline`；
- PaddleOCR-VL 1.6 完整 Pipeline 负责表格增强和失败/缺页回退；
- 本地/LAN 为默认，外部 OpenAPI 必须经用户确认。

批次 -1/0 已完成控制面契约、公开脱敏 Golden、评测 Graph 和工具/模型 A/B，但
`src/semantic_harness/` 当前只有强类型模型，没有自然语言编译器、Source Binder、
Physical Plan、生产工具注册表、执行/验证/修复 Graph 或前端。因此下一阶段必须从
批次 1 的 STP 编译与版本化开始，不能把评测代码直接接成生产执行器。

## 2. 必须修复或在近期实现的问题

| 优先级 | 问题 | 当前证据与影响 | 建议处理 | 是否阻塞批次 1 |
|---|---|---|---|---|
| P1 | STP 生产编译链尚未实现 | 只有 `models.py` 和 JSON Schema；用户语义仍不能确定性表达过滤、投影、行粒度、合并和聚合 | 按批次 1 实现 Logical Plan 编译、静态校验、用户摘要、不可变 revision 与 hash | 是，这是批次 1 本身 |
| P1 | Source Binder/Physical Plan 尚未实现 | 评测 Graph 使用固定夹具和固定绑定，不能代表真实文件 Schema 绑定 | 批次 1 只冻结逻辑计划；批次 2 再检查真实来源并绑定，禁止模型在看不到 Schema 时猜列 | 不阻塞编译器，但阻塞生产执行 |
| P1 | MinerU Hyper high 出现“完成但空结果” | `hybrid-http-client + high` 对 3 份/15 页返回 0 页、0 块、综合分 0；任务结果 `29ac7b7d-d659-4614-9c4a-a2039496db5a` 的 `model_output` 为 5 个空数组 | 服务端检查 high 模式 VLM/协议和日志；客户端增加“完成但无内容/缺页”的显式失败分类并触发备用解析器 | 否，保持 pipeline 默认即可 |
| P1 | MinerU Hyper 运行配置和生产适配不完整 | `MinerUDocumentClient` 只发送通用 pipeline 参数，没有 Hyper 的 `effort`、`server_url`、`image_analysis` 等受控配置 | Hyper 继续作为实验能力；若接入，新增独立配置、能力清单、健康探针、空结果门和缓存身份 | 否 |
| P1 | Python 依赖树仍有 3 项冲突 | `pip check`：缺 `types-pytz`；crawl4ai 要求 `lxml~=5.3`，当前 6.1.1；spider-dcd 要求 `httpx<0.28`，当前 0.28.1 | 批次 1 新增依赖前建立锁定/隔离方案；不要为了消警盲目全局降级，先做受影响采集器回归 | 否，但应在继续扩依赖前治理 |
| 已解决 | Git 发布目标曾存在版本边界冲突 | `v0.0.4` 分支和封板标签均指向 `14d81eb`；当前 HEAD 已包含 v0.0.5 与 Phase 4B | 用户已明确授权新建并推送 `platform/v0.0.6`；保持 `v0.0.4` 不动，本次不创建标签 | 否 |
| P2 | GitHub 默认分支仍为 `v0.0.1` | `gh repo view Eclipseic1848/Mangrove_platform` 返回默认分支 `v0.0.1`，容易让访问者看到过期代码 | 在不移动标签、不改写分支历史的前提下，单独确认后把仓库默认分支切到稳定 `v0.0.4` | 否 |

## 3. 已恢复但暂不应升级为默认的能力

2026-07-26 重新实测三个 LAN 服务：

| 服务 | 健康状态 |
|---|---|
| MinerU `192.168.1.21:8000/health` | HTTP 200，3.4.4，协议 2 |
| Paddle Pipeline `192.168.1.21:18081/health` | HTTP 200，Healthy |
| Paddle VLM `192.168.1.21:18080/v1/models` | HTTP 200，`PaddleOCR-VL-1.6-0.9B` |

同一 3 份扫描/混合文档试点结果：

| 模式 | 成功 | 页覆盖 | 字段召回 | 行召回 | 表格行召回 | bbox | 综合分 | 耗时 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Paddle `/layout-parsing` | 3/3 | 15/15 | 88.9% | 95.8% | 100% | 100% | 0.950895 | 66.499 秒 |
| MinerU `hybrid-http-client` medium | 3/3 | 15/15 | 100% | 60% | 0% | 100% | 0.740017 | 36.910 秒 |
| MinerU `hybrid-http-client` high | 接口 3/3 完成，但内容为空 | 0/15 | 0% | 0% | 0% | 0% | 0 | 38.037 秒 |
| MinerU 本地 `hybrid-engine` medium/high | 0/6 | 0 | 0 | 0 | 0 | 0 | 0 | 立即失败 |

结论：

1. Paddle 已达到当前备用链要求，尤其适合表格增强。
2. Hyper medium 已从“不可调用”变成“可调用的实验候选”，但表格和正文完整性明显弱于
   当前默认链，不能仅凭字段命中率升级为默认。
3. Hyper high 不是客户端解析遗漏：服务原始响应本身为空，必须修复服务端并增加空结果门。
4. 本地 `hybrid-engine` 仍报 `Device string must not be empty`；若服务端计划本机推理，
   需要配置有效 CUDA device。若只使用远程 VLM，则不应误选该后端。
5. 修复后先重跑同一试点，再扩到 17 份/85 页全量；未通过前继续使用
   MinerU pipeline + Paddle fallback。

## 4. 不阻塞批次 1、但需要登记的问题

| 优先级 | 待办 | 处理阶段 |
|---|---|---|
| P2 | 完整中文/英文/多语种全页 OCR CER/WER、阅读顺序、复杂无框表格和低清旋转样本仍未完成 | 独立版本化解析器评测 |
| P2 | Tika、LibreOffice、Pandoc、WeasyPrint、PptxGenJS 尚未完成服务器受控 sidecar PoC | 批次 6/8 |
| P2 | 只记录过 RSS 前后差值，缺少独立进程峰值 RAM、临时磁盘、缓存命中和并发吞吐 | 批次 8 |
| P2 | 当前固定 Golden 重点覆盖文档专项，CSV/Web/Office/ZIP/HTTP 的统一固定语料仍不完整 | 批次 8/Phase 5B |
| P2 | 真实 500 MB 文件、10–20 用户压力、4×L20 GPU 租约和故障注入尚未实测 | 服务器验收/Phase 5B |
| P2 | SQLite 输出枚举存在但实现仍明确跳过 | Phase 5B 实现或正式移除 |
| P2 | HTTP offset/cursor/Link/readonly POST 和 credential_ref 后端能力尚无完整前端入口 | Phase 5B |
| P2 | 定向测试仍出现 RequestsDependencyWarning 与 pynvml 弃用警告 | 依赖治理时校准 requests/chardet，并把 pynvml 迁移为 nvidia-ml-py；不为消警盲目升级整棵依赖树 |
| P2 | 仓库登记了 7 个额外 `.claude/worktrees`，并残留多条 `worktree-agent-*` 分支 | 先核对归属和差异，再经用户确认清理 |
| P3 | Label Studio 外部联调 | 可选适配器，不作为阶段门禁 |

## 5. 下一步执行顺序

1. 保持当前解析默认链，不把 Hyper medium/high 接入生产默认。
2. 开发批次 1：STP 编译、静态校验、可读摘要、revision/hash 和 Phase 4A 适配器。
3. 批次 1 开发期间并行处理依赖锁定方案，并在 MinerU 服务端修复 high/device 后重跑解析试点。
4. 批次 2 完成真实来源检查与 Binder 后，才允许计划进入物理执行。
5. 每批继续只提交该批实现、测试、Schema、评测证据和必要文档；不提交本地设置、
   lessons/templates、测试产物或真实业务文件。

## 6. 本次复核证据

- 三个 LAN 端点实时健康检查均为 HTTP 200；
- MinerU high 原始任务结果重新读取，5 页 `model_output` 仍为空数组；
- Phase 4B 契约/夹具/批次 0 定向测试：18 passed；
- `pip check` 复现 3 项冲突；
- Markdown 本地链接和 UTF-8 严格解码检查通过；
- 本次只更新说明文档，没有修改生产代码、配置或运行库存。

## 7. 发布边界

`v0.0.4` 是 Phase 4A 稳定封板标签和稳定分支，不能用当前 Phase 4B 文档提交覆盖或移动。
用户已在 2026-07-26 明确授权从 `b43c948` 创建并推送 `platform/v0.0.6`，该分支承载
本评审及后续 Phase 4B 工作，但当前未封板，也不创建 `v0.0.6` 标签。以后只有用户明确要求
新建版本时才能创建新的版本分支或标签；未明确要求封板/标签时，只创建版本分支。任何后续状态
都不能反向写成 v0.0.4 已交付能力。
