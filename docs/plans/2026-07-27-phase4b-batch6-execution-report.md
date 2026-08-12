# Phase 4B 批次 6 输出、转换和下载闭环执行报告

> 日期：2026-07-27
>
> 分支：`v0.0.6`
>
> 开发基线：`10a5905`
> 状态：代码、测试和文档已完成并发布；功能提交 `ef7e26b`；未创建标签

## 1. 本批结论

批次 6 已把批次 5 的 `eligible_for_delivery` 后置状态升级为真实交付闭环：

```text
权威执行结果
→ 11 种格式 Renderer
→ 每个文件独立重开 QA
→ SHA-256 / 大小 / 格式指标
→ Manifest
→ staging 同卷原子发布
→ SQLite 用户归属登记
→ output_id 授权下载
```

支持的正式格式为：

- JSON
- JSONL
- CSV
- XLSX
- Parquet
- DOCX
- PDF
- HTML
- Markdown
- TXT
- PPTX

`TSV` 仍是内部枚举兼容项，但不属于用户确认的本批 11 种正式输出；请求 TSV 时会显式失败，
不会静默换成 CSV 或其他格式。

## 2. 工具选择

本批没有自研文件格式协议，复用了已经成熟且当前主环境可用的开源组件：

| 格式 | 默认 Renderer | 独立 QA |
|---|---|---|
| JSON / JSONL / CSV / HTML / Markdown / TXT | Python 标准库 | 重新解析或 UTF-8 重读 |
| XLSX | XlsxWriter `constant_memory` | openpyxl 只读重开 |
| Parquet | PyArrow | PyArrow metadata 重开 |
| DOCX | python-docx | python-docx 重开 |
| PDF | ReportLab；中文使用系统字体或内置 CID 字体 | pypdf 重开并统计页数 |
| PPTX | python-pptx | python-pptx 重开并统计幻灯片数 |

开发前同时探测了 Pandoc 容器、Gotenberg、WeasyPrint 和 PptxGenJS。它们保留为复杂版式或
高保真转换的候选 sidecar；本批默认链选择已安装的纯 Python 成熟组件，原因是：

1. 能在当前 Windows/Python 3.13 环境直接验证；
2. Linux/Docker 不需要桌面环境；
3. 不把未验证的重转换服务并入主进程；
4. 能先完成确定性格式重开、完整性和下载权限闭环。

这不代表候选 sidecar 已完成生产验收，也不代表复杂原版式一比一复刻已经解决。

## 3. 生产实现

### 3.1 交付契约

新增：

- `DeliveryStatus`
- `ArtifactQAReport`
- `DeliveryOutput`
- `DeliveryManifest`

Manifest 记录：

- run / plan 身份；
- 输入制品 SHA-256；
- 请求格式；
- 每个输出的不透明 `output_id`；
- 文件名、媒体类型、SHA-256、字节数；
- 重开 QA 指标；
- Renderer 及真实包版本；
- 权威内部结果的 provenance。

`user_id` 只用于服务端权限和持久化，不进入公开响应。

### 3.2 原子发布

每次交付先写入任务专属 `.staging` 目录。只有全部请求格式都完成 Renderer 和独立 QA，
才把目录同卷改名为正式 `delivery_id` 目录并登记数据库。

任一 Renderer、QA 或数据库登记失败时：

- 删除 staging；
- 若已经改名则回滚正式目录；
- 不登记任何 `output_id`；
- Harness 标记 `delivery_failed`；
- 不允许下载半成品。

### 3.3 持久化

SQLite 新增：

- `semantic_delivery_runs`
- `semantic_delivery_outputs`

持久化层只按 `(user_id, delivery_id)` 或 `(user_id, output_id)` 读取。文件系统绝对路径不向
API 调用者公开。

### 3.4 Harness 接入

表格和文档能力适配器会把已经过 Verify 的权威结果路径作为私有运行态传给 `deliver` 节点。
`deliver` 节点不再只设置 `eligible_for_delivery=true`：

- 成功：发布正式文件，事件为 `delivery_published`；
- 失败：状态为 `failed`，事件为 `delivery_failed`；
- 事件返回 `delivery_id`、`output_id`、格式和授权下载 URL；
- 相同 run 已有成功交付时返回既有 Manifest，避免重复发布。

### 3.5 下载安全

新增 API：

- `GET /api/semantic-deliveries/{delivery_id}`
- `GET /api/semantic-deliveries/runs/{run_id}/latest`
- `GET /api/semantic-deliveries/outputs/{output_id}`

下载前再次校验：

1. 当前用户拥有 output；
2. 文件仍存在；
3. 文件大小与登记值一致；
4. SHA-256 与登记值一致。

跨用户访问统一返回 404；文件被篡改或损坏返回 409。

## 4. 验证证据

### 4.1 批次 6 定向验证

```text
tests/test_semantic_delivery.py
tests/test_semantic_harness_loop.py
```

结果：

```text
10 passed, 0 failed
```

覆盖：

- 同一份中文表格生成 11 种格式；
- 11 种格式全部由独立读取器重开；
- QA SHA-256 与 Manifest 一致；
- staging 原子发布；
- TSV 显式拒绝；
- Renderer 失败不登记、不留半成品；
- Harness 真实生成正式下载；
- 跨用户 output_id 访问为 404；
- 文件篡改后下载为 409。

### 4.2 全仓回归

命令：

```powershell
$env:PYTHONIOENCODING='utf-8'
E:\python3.13\python.exe -X utf8 -m pytest -q --basetemp=.pytest-tmp/batch6-full-rerun
```

结果：

```text
928 passed, 4 skipped, 0 failed
```

四项跳过均为原有显式门禁：

- 2 项大规模性能测试需 `--run-performance`；
- 2 项真实 MySQL/PostgreSQL 容器测试需 `--run-db-live`。

前端生产构建：

```text
npm.cmd run build
✓ built
```

`pip check` 仍只有批次 6 开工前已有的两项：

- `pandas-stubs` 缺少 `types-pytz`；
- `crawl4ai 0.9.0` 要求 `lxml~=5.3`，当前为 `lxml 6.1.1`。

本批没有制造新的 `pip check` 冲突。

## 5. 变更范围

生产代码：

- `src/semantic_harness/delivery/`
- `src/semantic_harness/harness_adapters.py`
- `src/semantic_harness/harness_graph.py`
- `src/api/store.py`
- `src/api/routes/semantic_deliveries.py`
- `src/api/main.py`
- `requirements.txt`

测试：

- `tests/test_semantic_delivery.py`
- `tests/test_semantic_harness_loop.py`

文档：

- 批次 6 实施方案与本执行报告；
- Phase 4B 权威计划；
- ADR/知识基座/零上下文交接状态。

## 6. 已知边界

1. 批次 6 是后端正式交付闭环；前端下载中心、格式选择与 QA 展示仍属于批次 7。
2. 当前 DOCX/PDF/PPTX 是内容正确、可打开、可追溯的确定性交付，不承诺复杂原文档版式一比一复刻。
3. Gotenberg、Pandoc、WeasyPrint、PptxGenJS 尚未作为生产默认 sidecar；需要复杂版式真实样本
   A/B、资源限制和容器安全验收后才能切换。
4. 大规模性能门和真实数据库容器门本次未显式开启，不能把普通全仓回归冒充这两类证据。
5. 当前工作区仍混有用户/运行期改动；本批已按白名单发布，未混入这些内容。

## 7. 下一步

下一批为 Phase 4B 批次 7：

- 正式前端格式选择与交付状态；
- 下载列表、QA/失败原因展示；
- 处理中、需确认、失败、成功状态；
- 前端用户隔离和 E2E；
- 不改变本批后端交付契约。

批次 6 完成不代表 Phase 4B 封板；批次 8 的扩展评测、性能/容器证据和封板审计仍未完成。
