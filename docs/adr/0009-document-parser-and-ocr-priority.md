# ADR-0009：文档解析按页分流，坐标证据优先于视觉模型候选

- 状态：已采纳，Phase 4A 基础层已实现
- 日期：2026-07-22
- 决策来源：Phase 4A 固定黄金集与隔离工具 PoC

## 背景

数字 PDF、扫描 PDF 和混合 PDF 的最佳处理方式不同。单独依赖视觉大模型虽然能快速得到字段候选，但其 bbox 不稳定，不能独立满足字段级证据定位；把完整 Docling/PaddleOCR 依赖直接并入主环境，又会显著扩大依赖、模型和冷启动成本。

## PoC 证据

- 固定合成黄金集已提交：合同 12、招投标 7、发票 5，共 24 份 PDF / 120 页，覆盖数字、扫描、混合、轻微旋转、噪声、测试印章和跨页表格。
- `pdfplumber + pypdfium2` 基线：页类型路由 120/120，数字页目标字段精确覆盖 27/27；PDF 渲染使用 `pypdfium2==5.12.1`。
- 本地 `Qwen3.6-35B-A3B`：`/v1/models` 和图片输入实测可用；3 份扫描样例目标字段覆盖 9/9，合计评测耗时 7.568 秒。必须设置 `chat_template_kwargs.enable_thinking=false`，否则小 token 预算可能只有 reasoning、正文为 null。
- Docling 2.114.0：隔离 Python 3.13 环境安装成功，但默认转换在 10 分钟内未产出首份代表样例，判为冷启动 PoC 超时。
- PaddlePaddle 3.3.1 + PaddleOCR 3.7.0 `doc-parser`：隔离 Python 3.12 环境安装成功；默认模型源探测失败，按官方方式改为 BOS 后，首张 PP-StructureV3 页面仍在 10 分钟内未产出。生产必须预下载并固定模型卷，禁止任务运行时下载。
- MinerU HTTP 服务：2026-07-23 升级后 `/health` 实测版本 3.4.4、协议版本 2；`pipeline + parse_method=ocr` 对 5 页扫描发票返回 26 个带坐标文本块，覆盖 5/5 页并命中发票号和总额，耗时 5.065 秒。完整 PdfParser 链路继续兼容；`hybrid-auto-engine` 的旧 409 结论未在本次升级后复测，启用前仍需单独验收。
- PaddleOCR-VL 地址 `192.168.1.21:18080` 实测为 vLLM 0.10.2 的 VLM 推理子服务，模型为 `PaddleOCR-VL-1.6-0.9B`，只提供 OpenAI 兼容接口，不提供 `POST /layout-parsing`。按官方定义，它不等价于包含 PP-DocLayoutV2 的完整 PaddleOCR-VL Pipeline，不能独立产出本项目要求的版面 bbox。
- 本地全链门禁：明确锁定 local / `Qwen3.6-35B-A3B`，对扫描/混合合同、招投标和全部发票共 17 份 PDF / 85 页 / 51 字段首跑 110.447 秒；字段精确值、证据完整、页码、原文、bbox 和证据绑定均为 51/51，页面解析 85/85，0 reject、0 运行错误、0 跨文档引用。坐标修复后的缓存复跑为 51.868 秒，结果不变。
- JiWER 诊断只比较 51 条预期证据短句：49/51 严格文本相等，平均 CER 0.06017、WER 0.47386，差异主要来自空格和分词。该指标用于定位证据文本差异，不等同于全页 OCR CER/WER，不能替代完整 Paddle Pipeline 同集 A/B。

## 状态演进

本 ADR 记录的是 2026-07-22 的首轮选型。2026-07-23 已补齐 Paddle
`18081/layout-parsing` 完整 Pipeline 并完成 17 份/85 页同集 A/B，正式路由由
ADR-0011 取代本 ADR 中“完整 Pipeline 尚未接入”的历史状态。2026-07-26 又验证了
MinerU `hybrid-http-client`：medium 可调用但表格质量不足，high 返回空结果；
这些变化仍不改变 MinerU pipeline 主用、Paddle 表格增强/缺页回退的决策。

## 决策

1. PDF 按页分类，不按整个文件粗暴选择单一引擎。
2. 数字页优先结构解析；当前确定性降级使用 `pdfplumber` 文本与 bbox。Docling 保持隔离可选，冷启动和模型部署达标后再进入默认路径。
3. 扫描页优先使用能稳定输出坐标、置信度和版本的 OCR 服务。当前本机使用 `MinerU 3.4.4 + pipeline`；`model_output` 返回的图像像素 bbox 必须依据 `page_info` 归一化为 `normalized_1000`，第三方原始响应保持不可变。PaddleOCR-VL 1.6 的 VLM 子服务地址单独记录为 `PADDLEOCR_VL_VLM_BASE_URL`，只有补齐并配置提供 `POST /layout-parsing` 的完整 Pipeline 网关后才能启用。主备服务可配置，首选故障或缺页时才调用备用；任何后端不可用或缺页时，都不能把扫描页伪装成解析成功。
4. 混合页分别处理数字文本与图像区域，再按文档元素合并。
5. `pypdfium2` 是默认 PDF 页渲染器。PyMuPDF 为 AGPL/商业双许可，未完成许可证审批前不进入商业默认链。
6. 本地 Qwen 负责扫描页语义候选、低置信度复核和跨结果校验。Qwen 候选缺少确定性 bbox 时必须 `review_required=true`，不能独立生成 `found` 字段。
7. Qwen 局域网调用固定 `trust_env=false`、600 秒超时、温度 0、关闭思考模式；不得使用系统代理访问局域网端点。
8. MinerU/Docling/PaddleOCR 均保持独立环境或服务，不写入主 requirements。生产启用前必须固定包版本、模型版本、模型卷和离线健康检查；服务类型、主备顺序、地址、端点与超时均通过 `.env` 配置，不硬编码到 PDF 主链。
9. DOCX 继续使用主环境已有的 `python-docx`，上传后立即生成段落/表格结构化预览和稳定元素 ID。DOCX 不具备可信的原页视觉坐标时，以段落号、表格号和行号作为确定性证据位置写入 `EvidenceRef.location`；不得安装 LibreOffice 作为必需运行依赖，也不得伪造 bbox。
10. 数字 PDF 的原表结构使用 `pdfplumber.find_tables()`，逐行生成带 `table_columns`、`table_row` 和真实行级 `pdf_points` bbox 的 `DocumentElement`；扫描/混合页表格消费 MinerU `content_list.table_body`。两者均进入统一 `ExtractedTable`，不再把表格任务退化为散乱单词。
11. Windows 下第三方解析缓存路径必须在同一绝对根目录下做边界校验与相对化；`ArtifactStore` 初始化即解析根目录，避免远程解析已经成功却在缓存引用返回阶段失败。

## 后果

- 数字 PDF 已获得稳定页级文本坐标和可复现渲染，现有 v2 记录格式继续兼容。
- DOCX 已形成上传预览、统一元素、字段抽取和结构位置证据闭环；其预览是结构化内容视图，不承诺 Word 原版式像素级还原。
- 扫描 PDF 已接通 MinerU 坐标 OCR 和缓存，图像像素坐标已统一归一化；服务不可用、结果缺页时仍进入 `ocr_required`。扫描/混合/发票字段与证据门禁已通过。后续 `18081` 完整 Pipeline 已接入并完成同集 A/B；`18080` 仍是 VLM 子服务，不能冒充 `/layout-parsing`，但可作为 MinerU `hybrid-http-client` 的远程 VLM 端点。
- 15 份真实工作量核算 PDF 回归中，13 份数字文件由 pdfplumber 识别 14 张表，2 份扫描/混合文件由 MinerU 恢复 2 张 HTML 表；最终按明确合并契约交付 1 张表、143 行、20 列，0 reject。
- 依赖体积和许可证风险被隔离；代价是要单独建设 OCR 模型镜像/卷和服务健康检查。

## 相关

- [ADR-0007：语义抽取必须绑定原始证据](0007-evidence-bound-semantic-extraction.md)
- [Phase 4A 实施计划](../plans/2026-07-22-phase4a-document-evidence-plan.md)
- [基线评测结果](../plans/phase4a-baseline-results.json)
- [Qwen 扫描探针结果](../plans/phase4a-qwen-probe-results.json)
- [MinerU 实机探针结果](../plans/phase4a-mineru-probe-results.json)
- [PaddleOCR-VL VLM 子服务探针结果](../plans/phase4a-paddle-vlm-probe-results.json)
- [本地 Qwen3.6 全链门禁结果](../plans/phase4a-local-qwen-gate-results.json)
- [ADR-0011：MinerU 主解析、Paddle 表格增强与失败回退](0011-mineru-paddle-parser-routing.md)
