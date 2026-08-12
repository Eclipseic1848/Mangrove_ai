# Phase 2 续作执行方案：Task 2.5 与后续任务顺序（已归档）

> **用途**：本文件保留 Phase 2 的原始实施排序、技术方案、验收门禁和测试计划，供审计与回溯；不再作为新任务的执行清单。
>
> **最终状态（v0.0.3，2026-07-21）**：Phase 2 发布范围已完成。全量测试 **206 passed + 1 skipped**，显式性能门禁 **2 passed**（含 100 万行），前端构建及浏览器 E2E 五项均通过；Task 0–12 + Task 2.5 全部完成。固定黄金语料、扫描 PDF OCR、SQLite 导出、HTTP 高级配置 UI/凭证注入和单独 500 MB 基准属于未完成边界；新开发从 Phase 3 开始。
>
> **后续开发状态更新（2026-07-22）**：Phase 3 数据库安全只读已经完成；Phase 4A 已完成固定文档黄金集、基础证据契约、MinerU 坐标 OCR、PaddleOCR-VL API 预留和主备回退，字段抽取闭环仍在继续。本文中“下一步”“未开始”“当前基线”等文字是 Phase 2 当时的历史描述，均以根目录 `handoff.md` 为准。

---

## 历史结论：当时继续 Phase 2，不进入 Phase 3

当前 Phase 2 状态：

| 任务 | 状态 | 交付证据 |
|---|---|---|
| Task 0 | 完成 | `4edb949`，Phase 1 收尾独立提交 |
| Task 1 | 完成 | `d5d1417`→`de31082`，规格 PASS、质量 APPROVED |
| Task 2 | 完成基础批次契约 | `3105d4d`、`8437418`；但 clean/profile/validate/output 仍全量物化 |
| Task 3 | 部分完成 | `a27fdb0`、`e7e132c`；UploadStore/FileConnector 核心完成，上传 API 与安全预检未完成 |
| Task 4–12 | 未开始 | 文件解析、HTTP、前端、性能与最终验收待执行 |

下一步顺序：

```text
Task 2.5：补完真正的分批数据面
-> Task 3：完成安全上传 API
-> Task 4：表格解析器
-> Task 5：JSON/文本解析器
-> Task 6：PDF/DOCX
-> Task 7：安全 ZIP
-> Task 8：HTTP 安全与分页
-> Task 9：HTTP API Connector
-> Task 10：预览与任务 API
-> Task 11：前端结构化闭环
-> Task 12：性能、安全、浏览器 E2E 和最终验收
```

---

## 一、最高优先级：Task 2.5 真正完成批次数据面

### 为什么需要先补 Task 2.5

当前 Task 2 已完成：

- LangGraph state 不再保存完整记录
- state 使用 `parsed_batches`、`clean_batches`
- parse 节点分批落盘
- checkpoint 可以跳过已处理 artifact

但目前以下节点仍会调用 `_read_records()`，把所有批次重新读入一个 Python 列表：

- `profile_node`
- `clean_node`
- `validate_node`
- `output_node`

即：

> 当前只是“磁盘分批存储”，还不是“端到端分批处理”。

如果现在直接接入 500 MB CSV 或百万行 JSONL，clean/profile/output 仍可能出现内存线性增长。

### 实施目标

```text
RawArtifact
  -> ParsedBatch
  -> ProfileAccumulator
  -> CleanBatch
  -> QualityAccumulator
  -> StreamingExporter
```

任何节点都不持有完整任务的数据集。

### 修改文件

- `src/data_prep/graph.py`
- `src/data_prep/output.py`
- `src/cleaning/engine.py`
- `src/cleaning/profiler.py`
- `src/quality/validators.py`
- `src/data_prep/batches.py`
- `tests/test_batch_pipeline.py`
- `tests/test_checkpoint_resume.py`

### 具体方案

#### 1. 将 `_read_records()` 改为生成器

```python
def iter_records(
    store: ArtifactStore,
    batches: list[BatchReference],
) -> Iterator[RecordEnvelope]:
    for batch in batches:
        for row in store.iter_jsonl(batch.path):
            yield RecordEnvelope.model_validate(row)
```

禁止再返回完整 `list[RecordEnvelope]`。

#### 2. Profile 使用累计器

新增：

```python
class ProfileAccumulator:
    def add_batch(self, records: Iterable[RecordEnvelope]) -> None: ...
    def finalize(self) -> ProfileReport: ...
```

累计：

- 总记录数
- 字段集合
- 空值数
- 类型计数
- 精确重复键
- 最小/最大值
- 有界样本

不能保存全部记录。

#### 3. 清洗逐批执行

每个 parsed batch：

```text
读取一个批次
-> execute_batch
-> 写 clean batch
-> 写 rejects batch
-> 写 lineage batch
-> 释放该批内存
```

跨批精确去重只保存：

- 主键集合
- 内容哈希集合
- 或磁盘索引

不能保存完整记录。

#### 4. Quality 使用累计指标

新增或完善：

```python
class QualityAccumulator:
    raw: int
    parsed: int
    parse_rejects: int
    clean: int
    clean_rejects: int
    merged: int
    lineage_covered: int
```

最终由累计值生成 `QualityReport`。

#### 5. Exporter 逐批写入

- JSONL：逐行追加
- CSV/TSV：首批写表头，后续批次追加
- Parquet：使用 `pyarrow.parquet.ParquetWriter`
- JSON：超过阈值拒绝
- XLSX：超过行数/内存阈值拒绝并告警

### 测试

新增：

- 多批次清洗账本守恒
- 跨批精确去重
- Profile 累计结果与小数据全量结果一致
- Parquet 多批写入
- JSON/XLSX 超限拒绝
- 中断恢复后不重写已完成批次

### 退出门禁

只有满足以下条件，才能开始正式的大文件解析器：

- `_read_records()` 不再返回完整列表
- clean/profile/validate/output 均逐批处理
- 10 万行测试能产生多个批次
- 峰值内存不会随总记录数线性增长
- 现有 Web 数据准备测试不回归

---

## 二、完成 Task 3：安全上传 API

Task 3 当前只完成了内部核心：

- UploadStore
- FileConnector
- 用户目录隔离
- 服务端生成文件名
- 配额
- SHA-256
- 元数据 sidecar
- FileConnector probe/read

下一步要补齐产品入口和安全预检。

### 修改/新增文件

- `src/services/upload_store.py`
- `src/connectors/file_connector.py`
- 新增 `src/api/routes/data_sources.py`
- `src/api/main.py`
- `src/config/settings.py`
- `.env.example`
- `tests/test_file_upload_security.py`
- `tests/test_file_connector.py`
- 新增 `tests/test_data_source_upload_api.py`

### 具体方案

#### 1. 真正流式上传

当前 `save_bytes()` 适合单元测试，但正式 API 不能先把完整文件读进内存。

增加：

```python
async def save_upload(
    self,
    user_id: str,
    file: UploadFile,
) -> UploadItem:
```

行为：

- 按 1 MB 或固定块读取
- 边写 staging
- 边计算 SHA-256
- 边检查累计大小
- 超限立即停止并删除 staging
- 校验通过后原子迁移到 objects

#### 2. MIME 与魔数联合校验

不能只信：

- 文件扩展名
- 浏览器上传的 Content-Type

需要联合判断：

```text
原始文件名后缀
+ UploadFile.content_type
+ 文件头魔数
```

首批允许格式白名单：

- CSV/TSV/TXT
- JSON/JSONL/XML/HTML
- XLSX
- Parquet
- PDF
- DOCX
- ZIP

格式不一致时：

- 明确拒绝，或
- 记录告警后按魔数识别的格式路由

不得按伪扩展名执行解析。

#### 3. 路径和符号链接防护

必须验证：

- 最终 object 路径属于上传根目录
- staging 不是符号链接
- objects 不是符号链接
- 不允许用户控制任何实际磁盘路径
- API 只接收 `upload_id`

#### 4. 上传 API

```text
POST   /api/data-sources/uploads
GET    /api/data-sources/uploads/{upload_id}
DELETE /api/data-sources/uploads/{upload_id}
```

响应不得包含：

- storage_path
- user_id
- 服务器绝对路径

#### 5. 用户归属

所有接口使用：

- `get_current_user`
- 服务端 user_id
- 不能接受请求体中的 user_id

注意：当前内部测试会把 `user_id` 放在 `SourceSpec.options`，正式 API 应由服务端注入，不应信任前端传值。

### 测试门禁

- 用户 A 不能读取/删除用户 B 的上传
- 文件名 `../../x.csv` 不影响存储路径
- 伪造 PDF/ZIP 后缀被拒绝
- 超限文件无 staging 残留
- 上传 API 响应不含绝对路径
- 删除只删除当前用户自己的上传
- 上传后可由 FileConnector 读取
- 上传后的 SHA-256 与源字节一致

### 推荐提交

```text
feat: 增加安全流式文件上传 API
```

---

## 三、Task 4：结构化表格解析器

完成真正流式上传和批次数据面后，优先实现价值最高、风险最低的表格格式。

### 文件

- 新增 `src/parsers/tabular.py`
- 修改 `src/parsers/registry.py`
- 新增 `tests/test_tabular_parsers.py`
- 新增黄金样例：
  - `tests/fixtures/golden/csv/`
  - `tests/fixtures/golden/tsv/`
  - `tests/fixtures/golden/excel/`
  - `tests/fixtures/golden/parquet/`

### 实施顺序

#### 4.1 CSV/TSV

支持：

- UTF-8
- UTF-8 BOM
- GBK
- 逗号
- Tab
- 分号
- 引号内换行
- 坏行隔离
- 表头探测
- 行号血缘

#### 4.2 Parquet

支持：

- row group 分批读取
- 保留物理 Schema
- 时间和空值类型
- 不经过 pandas 全量 DataFrame

#### 4.3 Excel

支持：

- 多 Sheet
- 空 Sheet
- 日期类型
- 公式结果策略
- Sheet + 行号血缘
- 只读模式 `read_only=True`

### 退出门禁

- 每种格式至少一套正常和异常黄金样例
- 原始记录数等于成功解析加 rejects
- CSV/TSV 不全量读入内存
- Parquet 按 row group
- Excel 按 Sheet/行迭代
- 所有记录有 source_id、artifact_id 和 position

---

## 四、Task 5：JSON 与文本类解析器

### 文件

- 新增 `src/parsers/json_xml.py`
- 修改 `src/parsers/registry.py`
- 新增 `tests/test_json_xml_parsers.py`

### 格式顺序

#### 5.1 JSONL

最先实现：

- 一行一条记录
- 非法行进入 parse rejects
- 保留行号
- 不因单行错误终止全文件

#### 5.2 JSON

支持：

- 单对象
- 小型对象数组

限制：

- 大型数组超过阈值时拒绝
- 明确提示用户改用 JSONL
- 不一次性加载超大数组

#### 5.3 TXT

支持：

- `line`
- `document`

#### 5.4 HTML

支持：

- 正文模式
- 表格模式
- 不执行 script
- 不加载外部资源

#### 5.5 XML

必须：

- 使用明确 record path
- 或只在候选唯一时自动选择
- 多个合理候选时返回澄清错误

### 退出门禁

- JSONL 坏行隔离
- 超大 JSON 数组不全量加载
- HTML/XML 不执行外部内容
- 记录位置完整
- 编码异常有明确 rejects

---

## 五、Task 6：PDF 与 DOCX

这部分应在结构化格式稳定后实施。

### PDF

处理链：

```text
pypdf 预检
-> pdfplumber 页级文本/表格
-> 空文本页判断为扫描页
-> 可选 OCR
-> 无 OCR 时进入 ocr_required rejects
```

必须保留：

- 页码
- 区块/表格位置
- parser 名称
- 是否 OCR
- OCR 置信度
- 原始 artifact_id

不得：

- 用标题补写正文
- 用邻页文本补写失败页
- 无证据时输出“成功”

### DOCX

支持：

- 标题
- 段落
- 表格
- 图片引用

禁止：

- 宏执行
- 嵌入对象执行
- 外部链接自动访问

---

## 六、Task 7：安全 ZIP

ZIP 应在各子解析器可用后实现，否则递归展开后没有完整路由能力。

### 安全限制

- 最大文件数
- 最大递归深度
- 最大展开字节
- 最大压缩比
- 拒绝 `../`
- 拒绝绝对路径
- 拒绝盘符路径
- 拒绝符号链接

### 数据关系

每个 ZIP 成员：

```text
parent_artifact_id = ZIP RawArtifact
```

成员再进入 ParserRegistry。

### 门禁

ZIP Slip 和 ZIP 炸弹测试未通过前，不得注册到正式 ParserRegistry。

---

## 七、Task 8：HTTP 安全预检与分页状态机

文件链路稳定后，再开始 HTTP。

### 8.1 SSRF 防护

每次请求和每次重定向都验证：

- 只允许 HTTP/HTTPS
- 拒绝 URL 用户名密码
- 解析全部 A/AAAA
- 拒绝 loopback
- 拒绝 link-local
- 拒绝云元数据地址
- 拒绝 reserved/multicast
- 私网默认拒绝
- 私网只允许管理员白名单

### 8.2 分页策略

分别实现：

- `PageNumberPager`
- `OffsetPager`
- `CursorPager`
- `LinkHeaderPager`

统一接口：

```python
class PaginationStrategy(Protocol):
    def first_request(self) -> PageRequest: ...
    def next_request(self, response: PageResponse) -> PageRequest | None: ...
    def checkpoint(self) -> dict[str, Any]: ...
```

### 门禁

- 重复响应哈希时停止
- 最大页数强制限制
- cursor 不推进时停止
- 重定向每跳重新校验
- checkpoint 可序列化和恢复

---

## 八、Task 9：通用 HTTP API Connector

### 支持范围

- GET
- 明确标记为只读的 POST
- JSON
- XML
- CSV
- 文件响应
- 429/502/503/504 重试
- Retry-After
- 分页
- 每页立即落 RawArtifact
- checkpoint

### 凭证策略

只接受：

```text
credential_ref
```

实际凭证由服务端注入。

不得写入：

- SourceSpec 对外序列化
- RawArtifact.request_snapshot
- Manifest
- Trace
- 错误响应

### 退出门禁

- 四种分页无漏页、无重复
- 429 可恢复
- 重复页停止
- 凭证泄漏为 0
- SSRF 测试全部阻断

---

## 九、Task 10：预览与正式任务 API

API 顺序：

```text
POST /api/data-sources/connections/test
POST /api/data-tasks/preview
POST /api/data-tasks
GET  /api/data-tasks/{task_id}
GET  /api/data-tasks/{task_id}/manifest
POST /api/data-tasks/{task_id}/rerun
```

### Preview 返回

- probe 信息
- 有界样本
- Schema
- parser warnings
- Recipe 草稿
- 预计记录数
- 预计字节数
- 高影响规则

### 高影响规则

以下必须等待确认：

- 字段删除
- 强制类型转换
- 脱敏
- 模糊去重

---

## 十、Task 11：前端结构化闭环

前端必须等 Task 3、4、5、8、9、10 后端契约稳定后再接，避免反复改接口。

### 最小范围

- 来源选择
- 文件上传
- HTTP API 配置
- 范围配置
- 样本预览
- Schema 展示
- Recipe 影响
- 正式执行
- 质量结论
- 产物下载

不建设：

- 浏览器内大数据编辑器
- Recipe 管理中心
- 可视化 ETL 画布
- 数据库连接 UI
- 多媒体输入 UI

---

## 十一、Task 12：最终验收

### 自动测试

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests -q
npm --prefix frontend run build
git diff --check
```

### 性能测试

至少验证：

- 100 万行 JSONL
- 500 MB CSV，可使用本地生成样例
- 批次大小 10,000
- 峰值内存
- 总耗时
- checkpoint 恢复时间

### 安全测试

- 路径穿越
- 越权上传
- 越权下载
- MIME 欺骗
- ZIP Slip
- ZIP 炸弹
- SSRF
- 重定向绕过
- 凭证泄漏
- HTML/XML 提示注入

### 浏览器 E2E

1. 上传 CSV
2. 预览样本和 Schema
3. 正式执行
4. 下载 JSONL/Parquet/Manifest
5. 上传坏行 JSONL，确认 rejects
6. 配置分页 API，确认无重复/漏页
7. 刷新会话，确认结果仍存在
8. 切换 legacy_analysis，确认旧链路正常

---

## 推荐执行批次

### 批次 A：必须最先完成

```text
Task 2.5 真正流式数据面
Task 3 安全上传 API
```

验收后才能开始对外宣称支持大文件上传。

### 批次 B：高频文件格式

```text
Task 4 表格解析
Task 5 JSON/文本解析
```

完成后可以形成首个真正可用的文件数据准备版本。

### 批次 C：文档和压缩包

```text
Task 6 PDF/DOCX
Task 7 安全 ZIP
```

### 批次 D：HTTP API

```text
Task 8 HTTP 安全与分页
Task 9 HTTP Connector
Task 10 Preview/Task API
```

### 批次 E：产品和验收

```text
Task 11 前端
Task 12 性能、安全、回归和发布验收
```

---

## 当前下一步的具体建议

应立即执行：

```text
Phase 2 · Task 2.5
```

其次：

```text
Phase 2 · Task 3 剩余部分
```

不要现在进入：

- Phase 3 数据库
- Phase 4 多媒体
- Phase 5 高级清洗和分布式工程化

因为文件/API 这一阶段尚未形成完整端到端产品闭环。

历史执行曾使用 `worktree-phase2-file-http-data-prep`，该 worktree 和分支均已删除。当前项目明确禁止创建或使用任何 worktree；所有后续工作只能在主体工作区进行。
