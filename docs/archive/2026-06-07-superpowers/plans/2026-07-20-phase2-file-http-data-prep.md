# Phase 2 本地文件与通用 HTTP API Implementation Plan

> 归档状态更新（2026-07-22）：Phase 2 与后续 Phase 3 均已完成；本文只保留 Phase 2 原始实施与验收设计，不作为当前下一步清单。Phase 4A 后续分支已补固定文档黄金集、MinerU 坐标 OCR、PaddleOCR-VL API 预留和主备回退；这不反向改变下方 v0.0.3 历史发布边界。当前状态见根目录 `handoff.md`。

> **执行状态（最终，2026-07-21）**
>
> **集成状态**：Phase 2 的 v0.0.3 发布范围已完成；浏览器 E2E 已于 2026-07-21 验收通过。验证为 **206 passed + 1 skipped**、显式性能门禁 **2 passed**、前端构建通过。发布边界：固定黄金语料、扫描 PDF OCR、SQLite 导出、HTTP 高级配置 UI/服务端凭证注入和单独 500 MB 基准未完成；不得把这些写成已交付。
>
> | 任务 | 状态 | 交付证据 |
> |---|---|---|
> | Task 0：Phase 1 基线 | 已完成 | `4edb949`，Phase 1 收尾独立提交 |
> | Task 1：批次契约与流式存储 | 已完成 | `d5d1417`、`36083b6`、`b585547`、`de31082`；规格 PASS、质量 APPROVED |
> | Task 2：批次流水线 | 已完成（基础契约） | `3105d4d`、`8437418`；规格 PASS、质量 APPROVED |
> | Task 3：安全上传与 FileConnector | 已完成 | `a27fdb0`、`e7e132c`、`fb9e267`；UploadStore/FileConnector + 安全流式上传 API（filetype 魔数 + 用户隔离 + 配额 + SHA-256 + sidecar） |
> | Task 4：表格解析器 | 已完成 | `387709d`、`c877222`；CSV/TSV（csv）/Excel（openpyxl）/Parquet（pyarrow），流式分批 |
> | Task 5：JSON/文本解析器 | 已完成 | `30e527d`、`7b762de`；JSON/JSONL（json）/TXT/HTML（bs4+lxml）/XML（lxml） |
> | Task 6：PDF/DOCX | 已完成 | `34588c0`；pdfplumber 逐页 + python-docx，扫描页进 ocr_required rejects |
> | Task 7：安全 ZIP | 已完成 | `21c2791`；zipfile + ZIP Slip/炸弹/数量/总量多层校验 |
> | Task 8：HTTP SSRF + 分页 | 已完成 | http_security.py（标准库 ipaddress）+ pagination.py（四种策略统一 Protocol） |
> | Task 9：HttpApiConnector | 已完成 | `72ab5d9`；httpx + SSRF 每跳校验 + 429 重试 + 凭证脱敏 + checkpoint |
> | Task 2.5 阶段1 | 已完成 | `3bc4fb4`；profile/clean 逐批（ProfileAccumulator + iter_records + 跨批去重） |
> | Task 2.5 阶段2 | 已完成 | `e25719f`；validate/output 逐批（QualityAccumulator + export_dataset 两遍扫描 + 移除 `_read_records`） |
> | Task 10 | 已完成 | `0584a2f`；6 端点（preview/create/get/manifest/rerun + connections/test）+ 任务持久化 + graph 支持 upload_file |
> | Task 11 | 已完成 | `2d543df`；DataPrepPage 独立路由 /data-prep + 上传/预览/执行/下载闭环 |
> | Task 12 | 已完成（发布验收通过） | `ece6dbe` + 2026-07-21 浏览器 E2E；文件预览/执行/JSONL-Parquet-Manifest 下载、JSONL rejects、HTTP page 分页、刷新恢复、legacy_analysis 切换均 PASS |
>
> 当前全量测试：`E:\python3.13\python.exe -X utf8 -m pytest tests -q` → **206 passed + 1 skipped**；`E:\python3.13\python.exe -X utf8 -m pytest tests\test_phase2_performance.py -q --run-performance` → **2 passed**（含 100 万行）；`npm --prefix frontend run build` → 通过。浏览器 E2E 五项均通过。5 万行用例断言峰值内存 `<150 MB`，100 万行用例验证分批与账本；未单独实测 500 MB 文件。下方 checkbox 保留历史实施步骤，顶部状态与发布边界是当前权威结论。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不破坏 Phase 1 Web 数据准备链路和旧分析模式的前提下，交付安全、可预览、可分批处理的本地文件与通用 HTTP API 数据准备能力。

**Architecture:** 继续使用 LangGraph 作为控制面，state 只保存任务规格、批次引用、计数和摘要；文件与 API 原始响应立即写入不可变 RawArtifact，解析、清洗和导出通过批次文件传递。上传、HTTP 请求、解析器、质量门和输出器保持职责分离，所有外部输入先经过安全预检。

**Tech Stack:** Python 3.13、FastAPI、Pydantic v2、LangGraph、PyArrow、pandas、openpyxl、pdfplumber、pypdf、python-docx、BeautifulSoup、lxml、httpx、React、TypeScript、Vite、pytest。

---

## 1. 已确认约束

- 开发期运行于单机 Windows/FastAPI，接口和目录结构应可迁移到 Linux 多容器。
- 目标基线为 500 MB 或 100 万行，设计面向 GB/千万行；不得把完整数据集放入 LangGraph state。
- 原始制品不可变，默认保留 30 天，可配置。
- 敏感信息默认检测和告警，不自动脱敏。
- 默认输出 JSONL + Parquet，同时支持 CSV、TSV、JSON、XLSX；本阶段不新增外部 Sink。
- LLM 只生成字段映射和 Recipe 草稿；解析、转换、过滤、隔离和质量判定由确定性代码执行。
- HTTP 数据源只支持 GET 和显式声明为只读的 POST，支持常见分页。
- 新任务默认 `data_prep`，旧 `legacy_analysis` 显式保留。
- Phase 2 不实现数据库输入、通用多媒体输入、Recipe 管理中心、模糊去重人工确认、对象存储或分布式队列。

## 2. Phase 2 完成定义

只有以下条件全部满足才可标记 Phase 2 完成：

1. 上传文件按用户隔离，路径穿越、符号链接逃逸和越权访问均被拒绝。
2. CSV/TSV、JSON/JSONL、Excel、Parquet、TXT/HTML/XML、PDF、DOCX、ZIP 黄金样例通过。
3. 坏行、坏页和坏压缩成员进入 rejects，并记录来源位置和确定性原因。
4. HTTP API 的 page、offset、cursor、Link Header 分页无重复、无漏页并支持 checkpoint 恢复。
5. SSRF、重定向绕过、ZIP Slip、ZIP 炸弹、越权下载和明文凭证泄漏测试全部阻断。
6. JSONL 与 Parquet 默认产物均真实生成。
7. Manifest、Schema、Quality、Lineage、Rejects、Recipe 和 Trace 完整且不含明文凭证。
8. 500 MB/100 万行任务按批次处理，峰值内存不随总记录数线性增长。
9. 前端可完成文件/API 来源配置、预览、正式执行、质量查看和产物下载。
10. Phase 1 Web data_prep、legacy_analysis、会话重载、下载和前端构建无回归。

## 3. 文件结构

### 新建后端文件

```text
src/data_prep/batches.py                 # 批次引用、流式 JSONL 与批次账本
src/connectors/file_connector.py         # 已验证上传文件到 RawArtifact
src/connectors/http_api_connector.py     # 通用只读 HTTP API 获取
src/connectors/http_security.py          # URL、DNS、重定向和私网策略
src/connectors/pagination.py             # page/offset/cursor/Link Header 状态机
src/services/upload_store.py             # staging、用户隔离、哈希和配额
src/parsers/tabular.py                   # CSV/TSV/Excel/Parquet
src/parsers/json_xml.py                  # JSON/JSONL/TXT/HTML/XML
src/parsers/pdf.py                       # 页级 PDF 文本和表格
src/parsers/office.py                    # DOCX 段落、标题和表格
src/parsers/archive.py                   # 安全 ZIP 展开和成员路由
src/api/routes/data_sources.py           # 上传和连接测试 API
src/api/routes/data_tasks.py             # preview/create/status/manifest/rerun API
```

### 新建前端文件

```text
frontend/src/components/data-prep/SourceSelector.tsx
frontend/src/components/data-prep/UploadSourceForm.tsx
frontend/src/components/data-prep/HttpApiSourceForm.tsx
frontend/src/components/data-prep/ScopeForm.tsx
frontend/src/components/data-prep/PreviewPanel.tsx
frontend/src/components/data-prep/SchemaPreview.tsx
frontend/src/components/data-prep/RecipePreview.tsx
frontend/src/components/data-prep/QualityResultPanel.tsx
frontend/src/components/data-prep/ArtifactDownloads.tsx
frontend/src/lib/dataPrepApi.ts
frontend/src/types/dataPrep.ts
```

### 新建测试与样例

```text
tests/test_batch_pipeline.py
tests/test_checkpoint_resume.py
tests/test_file_upload_security.py
tests/test_file_connector.py
tests/test_tabular_parsers.py
tests/test_json_xml_parsers.py
tests/test_pdf_parser.py
tests/test_office_parser.py
tests/test_archive_security.py
tests/test_http_security.py
tests/test_http_pagination.py
tests/test_http_api_connector.py
tests/test_data_task_api.py
tests/test_phase2_performance.py
tests/fixtures/golden/{csv,tsv,json,jsonl,excel,parquet,text,html,xml,pdf,docx,zip,http}/
```

### 修改现有文件

```text
src/data_prep/models.py
src/data_prep/artifact_store.py
src/data_prep/checkpoints.py
src/data_prep/graph.py
src/data_prep/output.py
src/connectors/base.py
src/connectors/__init__.py
src/parsers/registry.py
src/cleaning/engine.py
src/quality/validators.py
src/api/main.py
src/api/schemas.py
src/api/routes/chat.py
src/api/routes/downloads.py
src/config/settings.py
src/config/runtime_config.py
frontend/src/pages/Chat.tsx
frontend/src/lib/api.ts
requirements.txt
.env.example
AGENTS.md
README_AGENT.md
plan.md
```

---

## Task 0：冻结 Phase 1 基线

**Files:**
- Verify: `tests/test_data_prep_contracts.py`
- Verify: `tests/test_cleaning_engine.py`
- Verify: `tests/test_pipeline_offline.py`
- Verify: `frontend/src/pages/Chat.tsx`
- Verify: `src/api/routes/chat.py`

- [ ] **Step 1：检查工作区并隔离无关改动**

Run:

```powershell
git status --short
git diff --stat
```

Expected：确认 `.claude/settings.local.json`、`NU_` 和无关 Markdown 删除不进入 Phase 1/2 提交。

- [ ] **Step 2：运行 Phase 1 核心回归**

Run:

```powershell
E:\python3.13\python.exe -X utf8 -m pytest `
  tests/test_data_prep_contracts.py `
  tests/test_cleaning_engine.py `
  tests/test_pipeline_offline.py -q
```

Expected：`16 passed`。

- [ ] **Step 3：构建前端**

Run:

```powershell
npm --prefix frontend run build
```

Expected：TypeScript 检查和 Vite 构建成功，仅允许已有 chunk size 警告。

- [ ] **Step 4：提交 Phase 1 收尾**

```powershell
git add AGENTS.md README_AGENT.md plan.md requirements.txt scripts/verify_phase0_deps.py start_all.bat stop_all.bat frontend/src/lib/api.ts frontend/src/pages/Chat.tsx src/api/routes/chat.py src/api/routes/downloads.py src/api/schemas.py src/connectors/web_adapter.py src/data_prep/graph.py
git commit -m "feat: 完成数据准备 Phase 1 收尾`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

Expected：提交只包含 Phase 1 收尾、依赖和文档，不包含本地配置或无关删除。

---

## Task 1：定义批次契约和流式 ArtifactStore

**Files:**
- Create: `src/data_prep/batches.py`
- Modify: `src/data_prep/artifact_store.py`
- Modify: `src/data_prep/checkpoints.py`
- Test: `tests/test_batch_pipeline.py`

- [ ] **Step 1：编写失败测试**

```python
# tests/test_batch_pipeline.py
from pathlib import Path

from src.data_prep.artifact_store import ArtifactStore
from src.data_prep.batches import BatchReference


def test_append_jsonl_batch_and_iterate(tmp_path: Path):
    store = ArtifactStore(root=tmp_path)
    ref = store.append_jsonl_batch(
        task_id="task-1",
        dataset="parsed",
        rows=[{"record_id": "r1"}, {"record_id": "r2"}],
        part_no=0,
    )

    assert isinstance(ref, BatchReference)
    assert ref.record_count == 2
    assert ref.sha256
    assert list(store.iter_jsonl(ref.path)) == [
        {"record_id": "r1"},
        {"record_id": "r2"},
    ]
```

- [ ] **Step 2：确认测试失败**

Run:

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_batch_pipeline.py -q
```

Expected：因 `src.data_prep.batches` 或 `append_jsonl_batch` 尚不存在而失败。

- [ ] **Step 3：实现最小批次契约**

```python
# src/data_prep/batches.py
from __future__ import annotations

from pydantic import BaseModel, Field


class BatchReference(BaseModel):
    batch_id: str
    dataset: str
    part_no: int = Field(ge=0)
    path: str
    record_count: int = Field(ge=0)
    byte_count: int = Field(ge=0)
    sha256: str
```

在 `ArtifactStore` 中增加：

```python
def append_jsonl_batch(
    self,
    task_id: str,
    dataset: str,
    rows: Iterable[dict],
    part_no: int,
) -> BatchReference:
    task_dir = self.task_dir(task_id)
    target_dir = task_dir / dataset
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"part-{part_no:05d}.jsonl"
    digest = hashlib.sha256()
    count = 0
    byte_count = 0
    with target.open("xb") as fh:
        for row in rows:
            line = (json.dumps(row, ensure_ascii=False, default=str) + "\n").encode("utf-8")
            fh.write(line)
            digest.update(line)
            count += 1
            byte_count += len(line)
    rel_path = target.relative_to(self.root).as_posix()
    return BatchReference(
        batch_id=f"{dataset}-{part_no:05d}-{digest.hexdigest()[:12]}",
        dataset=dataset,
        part_no=part_no,
        path=rel_path,
        record_count=count,
        byte_count=byte_count,
        sha256=digest.hexdigest(),
    )


def iter_jsonl(self, rel_path: str) -> Iterator[dict]:
    path = self.resolve_path(rel_path)
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)
```

- [ ] **Step 4：扩展 checkpoint**

`src/data_prep/checkpoints.py` 中保留现有字段并增加：

```python
completed_batch_ids: list[str] = field(default_factory=list)
next_part_no: int = 0
```

- [ ] **Step 5：运行测试**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_batch_pipeline.py -q
```

Expected：PASS。

- [ ] **Step 6：提交**

```powershell
git add src/data_prep/batches.py src/data_prep/artifact_store.py src/data_prep/checkpoints.py tests/test_batch_pipeline.py
git commit -m "feat: 增加数据准备批次引用与流式存储`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2：把解析、清洗、质检和导出改为批次流水线

**Files:**
- Modify: `src/data_prep/graph.py`
- Modify: `src/data_prep/output.py`
- Modify: `src/cleaning/engine.py`
- Modify: `src/quality/validators.py`
- Test: `tests/test_batch_pipeline.py`
- Test: `tests/test_checkpoint_resume.py`

- [ ] **Step 1：增加 state 不保存完整记录的断言**

```python
def test_pipeline_state_contains_only_batch_references(completed_state):
    assert completed_state["parsed_batches"]
    assert completed_state["clean_batches"]
    assert "records" not in completed_state
    assert "cleaned_dataset" not in completed_state
    assert all(ref.record_count > 0 for ref in completed_state["clean_batches"])
```

- [ ] **Step 2：增加 checkpoint 恢复测试**

```python
# tests/test_checkpoint_resume.py
async def test_resume_skips_completed_batches(fake_parser, task_spec):
    checkpoint = Checkpoint(
        completed_batch_ids=["parsed-00000-fixed"],
        next_part_no=1,
    )
    state = await run_parse_batches(task_spec, checkpoint=checkpoint, parser=fake_parser)
    assert "parsed-00000-fixed" not in state["processed_batch_ids"]
    assert state["next_part_no"] == 2
```

- [ ] **Step 3：确认新增测试失败**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_batch_pipeline.py tests/test_checkpoint_resume.py -q
```

Expected：因 `parsed_batches`、`clean_batches` 和恢复逻辑不存在而失败。

- [ ] **Step 4：修改 DataPrepState**

在 `src/data_prep/graph.py` 中用以下引用字段替换单一路径和全量集合语义：

```python
parsed_batches: list[BatchReference]
clean_batches: list[BatchReference]
parse_reject_batches: list[BatchReference]
clean_reject_batches: list[BatchReference]
lineage_batches: list[BatchReference]
record_counts: dict[str, int]
```

- [ ] **Step 5：逐批执行 Recipe**

在 `RecipeEngine` 增加不跨批持有数据的入口：

```python
def execute_batch(
    self,
    records: list[RecordEnvelope],
    recipe: Recipe,
    *,
    rule_params: dict[str, dict] | None = None,
) -> CleaningResult:
    return self.execute(records, recipe, rule_params=rule_params)
```

精确去重需要跨批状态时，只保存稳定键集合或磁盘索引，不保存完整记录。

- [ ] **Step 6：增量累计质量指标**

质量门接收批次账本：

```python
class QualityAccumulator:
    def __init__(self) -> None:
        self.raw = 0
        self.parsed = 0
        self.parse_rejects = 0
        self.clean = 0
        self.clean_rejects = 0
        self.merged = 0
        self.lineage_covered = 0

    def add(self, counts: dict[str, int]) -> None:
        for name in vars(self):
            setattr(self, name, getattr(self, name) + int(counts.get(name, 0)))
```

- [ ] **Step 7：流式导出 JSONL 和 Parquet**

`src/data_prep/output.py` 使用 `pyarrow.parquet.ParquetWriter` 逐批写入，JSONL 逐行复制；CSV/TSV 逐批追加且只在首批写表头。JSON 数组和 XLSX 超过配置阈值时拒绝生成并写入质量告警。

- [ ] **Step 8：运行回归**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_batch_pipeline.py tests/test_checkpoint_resume.py tests/test_pipeline_offline.py -q
```

Expected：全部通过，Phase 1 离线测试保持账本和血缘覆盖率。

- [ ] **Step 9：提交**

```powershell
git add src/data_prep/graph.py src/data_prep/output.py src/cleaning/engine.py src/quality/validators.py tests/test_batch_pipeline.py tests/test_checkpoint_resume.py tests/test_pipeline_offline.py
git commit -m "refactor: 数据准备流水线改为分批数据面`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3：安全上传存储与 FileConnector

**Files:**
- Create: `src/services/upload_store.py`
- Create: `src/connectors/file_connector.py`
- Create: `src/api/routes/data_sources.py`
- Modify: `src/data_prep/models.py`
- Modify: `src/api/main.py`
- Modify: `src/config/settings.py`
- Modify: `.env.example`
- Test: `tests/test_file_upload_security.py`
- Test: `tests/test_file_connector.py`

- [ ] **Step 1：编写路径和归属失败测试**

```python
from pathlib import Path
import pytest

from src.services.upload_store import UploadStore


def test_upload_id_never_uses_client_filename(tmp_path: Path):
    store = UploadStore(tmp_path)
    item = store.save_bytes("user-a", "../../secret.csv", b"id,name\n1,A\n")
    assert "secret.csv" not in item.storage_path
    assert Path(item.storage_path).resolve().is_relative_to(tmp_path.resolve())


def test_other_user_cannot_resolve_upload(tmp_path: Path):
    store = UploadStore(tmp_path)
    item = store.save_bytes("user-a", "data.csv", b"id\n1\n")
    with pytest.raises(PermissionError):
        store.resolve("user-b", item.upload_id)
```

- [ ] **Step 2：确认测试失败**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_file_upload_security.py -q
```

Expected：因 `UploadStore` 不存在而失败。

- [ ] **Step 3：实现上传元数据和存储**

```python
class UploadItem(BaseModel):
    upload_id: str
    user_id: str
    original_name: str
    storage_path: str
    media_type: str
    size_bytes: int
    sha256: str
```

`UploadStore.save_stream()` 必须：

1. 在 `<root>/<user_id>/staging/` 写入服务端随机文件名。
2. 边写边计算 SHA-256 和大小。
3. 超过 `DATA_PREP_MAX_UPLOAD_BYTES` 时删除 staging 文件并报 413。
4. 校验 MIME/魔数后原子移动到 `<root>/<user_id>/objects/`。
5. 元数据只保存经过 `Path(name).name` 清理的展示名。

- [ ] **Step 4：实现上传 API**

```python
@router.post("/uploads")
async def upload_source(
    file: UploadFile,
    user=Depends(get_current_user),
) -> dict:
    item = await get_upload_store().save_upload(user["user_id"], file)
    return item.model_dump(mode="json", exclude={"storage_path", "user_id"})
```

路由前缀：`/api/data-sources`。

- [ ] **Step 5：实现 FileConnector**

`probe()` 读取少量头部验证 MIME、大小和格式；`read()` 将已验证上传复制或登记为任务 RawArtifact，不直接解析业务内容。

- [ ] **Step 6：运行安全测试**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_file_upload_security.py tests/test_file_connector.py -q
```

Expected：路径逃逸、越权、超限和伪扩展名全部拒绝，正常文件产生 RawArtifact。

- [ ] **Step 7：提交**

```powershell
git add src/services/upload_store.py src/connectors/file_connector.py src/api/routes/data_sources.py src/data_prep/models.py src/api/main.py src/config/settings.py .env.example tests/test_file_upload_security.py tests/test_file_connector.py
git commit -m "feat: 增加安全文件上传与文件连接器`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4：CSV、TSV、Excel 与 Parquet 解析器

**Files:**
- Create: `src/parsers/tabular.py`
- Modify: `src/parsers/registry.py`
- Test: `tests/test_tabular_parsers.py`
- Add fixtures: `tests/fixtures/golden/{csv,tsv,excel,parquet}/`

- [ ] **Step 1：创建黄金样例**

样例至少覆盖：UTF-8、UTF-8 BOM、GBK、逗号/Tab/分号、引号换行、坏行、多 Sheet、空 Sheet、日期、公式结果、Parquet row group 和空值。

- [ ] **Step 2：编写 CSV 分块与坏行测试**

```python
def test_csv_parser_preserves_row_position_and_rejects_bad_row(csv_artifact):
    batches = list(TabularParser(batch_size=2).parse_stream(csv_artifact))
    records = [record for batch in batches for record in batch.records]
    rejects = [reject for batch in batches for reject in batch.rejects]
    assert records[0].meta["position"]["row"] == 2
    assert rejects[0]["reason"] == "csv_bad_row"
    assert len(records) + len(rejects) == 4
```

- [ ] **Step 3：确认测试失败**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_tabular_parsers.py -q
```

Expected：因 `TabularParser` 不存在而失败。

- [ ] **Step 4：实现格式分流**

```python
class TabularParser(Parser):
    name = "tabular"

    def supports(self, media_type: str, suffix: str) -> bool:
        return suffix.lower() in {".csv", ".tsv", ".xlsx", ".parquet"}

    def parse_stream(self, artifact: RawArtifact) -> Iterator[ParsedBatch]:
        suffix = Path(artifact.uri or artifact.storage_path).suffix.lower()
        if suffix in {".csv", ".tsv"}:
            yield from self._parse_delimited(artifact)
        elif suffix == ".xlsx":
            yield from self._parse_excel(artifact)
        elif suffix == ".parquet":
            yield from self._parse_parquet(artifact)
        else:
            raise UnsupportedFormatError(suffix)
```

CSV/TSV 使用显式 UTF-8/GBK 探测与分块读取；Excel 按 Sheet 和行批次；Parquet 按 row group。所有记录写入行号、Sheet 或 row group 位置。

- [ ] **Step 5：注册解析器并运行测试**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_tabular_parsers.py -q
```

Expected：格式矩阵通过，账本守恒。

- [ ] **Step 6：提交**

```powershell
git add src/parsers/tabular.py src/parsers/registry.py tests/test_tabular_parsers.py tests/fixtures/golden/csv tests/fixtures/golden/tsv tests/fixtures/golden/excel tests/fixtures/golden/parquet
git commit -m "feat: 增加结构化表格解析器`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5：JSON、JSONL、TXT、HTML 与 XML 解析器

**Files:**
- Create: `src/parsers/json_xml.py`
- Modify: `src/parsers/registry.py`
- Test: `tests/test_json_xml_parsers.py`
- Add fixtures: `tests/fixtures/golden/{json,jsonl,text,html,xml}/`

- [ ] **Step 1：编写 JSONL 隔离测试**

```python
def test_jsonl_invalid_line_is_isolated(jsonl_artifact):
    batches = list(JsonXmlParser(batch_size=2).parse_stream(jsonl_artifact))
    records = [r for b in batches for r in b.records]
    rejects = [r for b in batches for r in b.rejects]
    assert len(records) == 2
    assert rejects == [{"position": {"line": 2}, "reason": "invalid_json"}]
```

- [ ] **Step 2：编写超大 JSON 数组拒绝测试**

```python
def test_large_json_array_requires_jsonl(json_array_artifact):
    parser = JsonXmlParser(max_json_array_bytes=32)
    with pytest.raises(ParseLimitError, match="JSONL"):
        list(parser.parse_stream(json_array_artifact))
```

- [ ] **Step 3：实现确定性格式策略**

- JSONL 逐行解析，非法行进入 parse rejects。
- 小型 JSON 支持单对象和对象数组。
- 超过阈值的 JSON 数组拒绝并建议转换为 JSONL，不全量加载。
- TXT 支持 `mode=line` 和 `mode=document`。
- HTML 支持正文与表格模式，默认不执行脚本或外部资源。
- XML 必须提供或探测唯一记录节点；存在多个合理节点时返回澄清错误，不静默猜测。

- [ ] **Step 4：运行测试**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_json_xml_parsers.py -q
```

Expected：正常记录与 rejects 账本守恒，所有位置进入 meta。

- [ ] **Step 5：提交**

```powershell
git add src/parsers/json_xml.py src/parsers/registry.py tests/test_json_xml_parsers.py tests/fixtures/golden/json tests/fixtures/golden/jsonl tests/fixtures/golden/text tests/fixtures/golden/html tests/fixtures/golden/xml
git commit -m "feat: 增加 JSON 与文本类解析器`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 6：PDF 和 DOCX 解析器

**Files:**
- Create: `src/parsers/pdf.py`
- Create: `src/parsers/office.py`
- Modify: `src/parsers/registry.py`
- Test: `tests/test_pdf_parser.py`
- Test: `tests/test_office_parser.py`
- Add fixtures: `tests/fixtures/golden/{pdf,docx}/`

- [ ] **Step 1：编写 PDF 页级来源测试**

```python
def test_pdf_records_include_page_and_parser(pdf_artifact):
    batches = list(PdfParser().parse_stream(pdf_artifact))
    records = [r for b in batches for r in b.records]
    assert records
    assert records[0].meta["position"]["page"] == 1
    assert records[0].meta["parser"].startswith("pdf_")
```

- [ ] **Step 2：编写扫描页明确降级测试**

```python
def test_scanned_pdf_page_never_silently_disappears(scanned_pdf_artifact):
    batches = list(PdfParser(ocr=None).parse_stream(scanned_pdf_artifact))
    rejects = [r for b in batches for r in b.rejects]
    assert rejects[0]["position"]["page"] == 1
    assert rejects[0]["reason"] == "ocr_required"
```

- [ ] **Step 3：实现 PDF 链路**

1. 使用 pypdf 读取页数、加密状态和元数据。
2. 使用 pdfplumber 提取页级文本和表格。
3. 无数字文本页面标记 `ocr_required`。
4. 如果配置了现有 Qwen 视觉 OCR 适配器，输出带 `ocr=true` 和置信度的记录。
5. 加密、损坏和低置信度页面进入 rejects，不用标题或邻页内容补写。

- [ ] **Step 4：实现 DOCX 解析**

按标题、段落和表格输出 RecordEnvelope，meta 保留文档序号和元素类型；不执行宏、嵌入对象或外部链接。

- [ ] **Step 5：运行测试**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_pdf_parser.py tests/test_office_parser.py -q
```

Expected：数字 PDF、扫描 PDF、损坏 PDF、DOCX 段落和表格均有明确结果或 rejects。

- [ ] **Step 6：提交**

```powershell
git add src/parsers/pdf.py src/parsers/office.py src/parsers/registry.py tests/test_pdf_parser.py tests/test_office_parser.py tests/fixtures/golden/pdf tests/fixtures/golden/docx
git commit -m "feat: 增加 PDF 与 DOCX 解析器`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 7：安全 ZIP 展开与递归解析

**Files:**
- Create: `src/parsers/archive.py`
- Modify: `src/parsers/registry.py`
- Test: `tests/test_archive_security.py`
- Add fixtures: `tests/fixtures/golden/zip/`

- [ ] **Step 1：编写 ZIP Slip 测试**

```python
def test_zip_slip_member_is_rejected(zip_slip_artifact):
    result = ArchiveParser().extract(zip_slip_artifact)
    assert result.children == []
    assert result.rejects[0]["reason"] == "zip_path_escape"
```

- [ ] **Step 2：编写压缩炸弹限制测试**

```python
def test_zip_ratio_limit_is_enforced(zip_bomb_artifact):
    parser = ArchiveParser(max_ratio=20, max_total_bytes=1024 * 1024)
    result = parser.extract(zip_bomb_artifact)
    assert result.rejects[0]["reason"] in {"zip_ratio_exceeded", "zip_size_exceeded"}
```

- [ ] **Step 3：实现安全限制**

- 最大成员数：配置项 `DATA_PREP_ZIP_MAX_FILES`。
- 最大递归深度：`DATA_PREP_ZIP_MAX_DEPTH`。
- 最大展开总量：`DATA_PREP_ZIP_MAX_UNCOMPRESSED_BYTES`。
- 最大压缩比：`DATA_PREP_ZIP_MAX_RATIO`。
- 拒绝绝对路径、`..`、盘符路径和符号链接。
- 子制品设置 `parent_artifact_id`，再由 ParserRegistry 递归路由。

- [ ] **Step 4：运行安全测试**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_archive_security.py -q
```

Expected：正常 ZIP 展开，ZIP Slip、嵌套超限和高压缩比全部拒绝。

- [ ] **Step 5：提交**

```powershell
git add src/parsers/archive.py src/parsers/registry.py src/config/settings.py .env.example tests/test_archive_security.py tests/fixtures/golden/zip
git commit -m "feat: 增加安全 ZIP 解析链路`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 8：HTTP 安全预检与分页状态机

**Files:**
- Create: `src/connectors/http_security.py`
- Create: `src/connectors/pagination.py`
- Test: `tests/test_http_security.py`
- Test: `tests/test_http_pagination.py`

- [ ] **Step 1：编写 SSRF 测试**

```python
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]/",
])
def test_private_and_metadata_addresses_are_rejected(url):
    with pytest.raises(UnsafeUrlError):
        validate_http_target(url, allow_private_hosts=set())
```

- [ ] **Step 2：编写分页测试**

```python
def test_cursor_pagination_advances_without_repeating():
    pager = CursorPager(cursor_path="next_cursor")
    request_1 = pager.first_request()
    request_2 = pager.next_request({"items": [{"id": 1}], "next_cursor": "abc"})
    assert request_1 != request_2
    assert request_2.params["cursor"] == "abc"
```

- [ ] **Step 3：实现 URL 安全策略**

`validate_http_target()` 必须只允许 HTTP/HTTPS，拒绝 URL 明文用户信息，解析全部 A/AAAA 地址并拒绝 loopback、link-local、multicast、reserved 和未授权私网地址。每次重定向重新校验目标，不复用首次判定。

- [ ] **Step 4：实现四种分页器**

```python
class PaginationStrategy(Protocol):
    def first_request(self) -> PageRequest: ...
    def next_request(self, response: PageResponse) -> PageRequest | None: ...
    def checkpoint(self) -> dict[str, Any]: ...
```

实现 `PageNumberPager`、`OffsetPager`、`CursorPager`、`LinkHeaderPager`。每个分页器记录页号、next cursor/offset 和已见页面响应哈希；相同响应哈希重复出现时停止并告警。

- [ ] **Step 5：运行测试**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_http_security.py tests/test_http_pagination.py -q
```

Expected：SSRF 目标和重定向绕过被拒绝，四种分页器状态推进正确。

- [ ] **Step 6：提交**

```powershell
git add src/connectors/http_security.py src/connectors/pagination.py tests/test_http_security.py tests/test_http_pagination.py
git commit -m "feat: 增加 HTTP 安全预检与分页状态机`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 9：通用只读 HTTP API Connector

**Files:**
- Create: `src/connectors/http_api_connector.py`
- Modify: `src/connectors/base.py`
- Modify: `src/connectors/__init__.py`
- Modify: `src/data_prep/models.py`
- Test: `tests/test_http_api_connector.py`
- Add fixtures: `tests/fixtures/golden/http/`

- [ ] **Step 1：编写 page 分页和重试测试**

```python
@pytest.mark.asyncio
async def test_http_connector_retries_429_and_collects_all_pages(mock_transport, api_spec):
    mock_transport.queue(
        response(429, headers={"Retry-After": "0"}),
        response(200, json={"items": [{"id": 1}], "page": 1}),
        response(200, json={"items": [{"id": 2}], "page": 2}),
        response(200, json={"items": [], "page": 3}),
    )
    batches = [batch async for batch in HttpApiConnector(transport=mock_transport).read(api_spec)]
    assert sum(len(batch.artifacts) for batch in batches) == 2
```

- [ ] **Step 2：编写凭证脱敏测试**

```python
@pytest.mark.asyncio
async def test_http_credentials_never_enter_artifact_metadata(api_spec_with_token):
    batch = await first_batch(HttpApiConnector().read(api_spec_with_token))
    serialized = json.dumps([a.model_dump(mode="json") for a in batch.artifacts])
    assert "secret-token" not in serialized
```

- [ ] **Step 3：实现连接器能力**

- GET 和 `options.read_only_post=true` 的 POST。
- query/header/body 模板。
- `credential_ref` 通过服务端凭证解析器注入，不写回 SourceSpec。
- JSON、XML、CSV 和文件响应落为 RawArtifact。
- 429、502、503、504 按 Retry-After/指数退避重试。
- 单响应和任务累计字节上限。
- 每页立即落盘，checkpoint 在制品成功写入后推进。
- 跨页以指定主键或内容哈希精确去重。

- [ ] **Step 4：运行连接器测试**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_http_api_connector.py tests/test_http_security.py tests/test_http_pagination.py -q
```

Expected：分页完整、限流恢复、重复页中止、凭证不泄漏。

- [ ] **Step 5：提交**

```powershell
git add src/connectors/http_api_connector.py src/connectors/base.py src/connectors/__init__.py src/data_prep/models.py tests/test_http_api_connector.py tests/fixtures/golden/http
git commit -m "feat: 增加通用只读 HTTP API 连接器`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 10：来源预览和正式数据任务 API

**Files:**
- Create: `src/api/routes/data_tasks.py`
- Modify: `src/api/routes/data_sources.py`
- Modify: `src/api/schemas.py`
- Modify: `src/api/main.py`
- Modify: `src/data_prep/graph.py`
- Test: `tests/test_data_task_api.py`

- [ ] **Step 1：编写用户归属和预览限量测试**

```python
def test_preview_returns_bounded_sample(client, auth_headers, uploaded_source):
    response = client.post(
        "/api/data-tasks/preview",
        headers=auth_headers,
        json={"source": uploaded_source, "sample_records": 20},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["sample"]) <= 20
    assert body["schema"]["fields"]


def test_other_user_cannot_create_task_from_upload(other_client, uploaded_source):
    response = other_client.post("/api/data-tasks", json={"source": uploaded_source})
    assert response.status_code == 404
```

- [ ] **Step 2：定义 API**

```text
POST /api/data-sources/connections/test
POST /api/data-tasks/preview
POST /api/data-tasks
GET  /api/data-tasks/{task_id}
GET  /api/data-tasks/{task_id}/manifest
POST /api/data-tasks/{task_id}/rerun
```

- [ ] **Step 3：实现预览返回契约**

```python
class DataTaskPreviewOut(BaseModel):
    probe: dict
    sample: list[dict]
    schema_: dict = Field(alias="schema")
    parser_warnings: list[str]
    recipe: Recipe
    estimated_records: int | None
    estimated_bytes: int | None
    high_impact_rules: list[str]
```

预览只读取配置上限内的字节、页数或记录，不获取全量 API 分页。字段删除、强制类型转换、脱敏和模糊去重进入 `high_impact_rules`。

- [ ] **Step 4：实现正式任务和 rerun**

正式任务只接受已验证 upload ID 或通过连接测试的 API SourceSpec。`rerun` 支持 `reuse_raw=true` 复用 RawArtifact；重新获取路径保留给后续增量阶段，不在本任务扩张。

- [ ] **Step 5：运行 API 测试**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests/test_data_task_api.py -q
```

Expected：预览受限、归属校验正确、任务状态和 Manifest 可查询。

- [ ] **Step 6：提交**

```powershell
git add src/api/routes/data_tasks.py src/api/routes/data_sources.py src/api/schemas.py src/api/main.py src/data_prep/graph.py tests/test_data_task_api.py
git commit -m "feat: 增加数据源预览与正式任务 API`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 11：前端文件/API 最小闭环

**Files:**
- Create: `frontend/src/types/dataPrep.ts`
- Create: `frontend/src/lib/dataPrepApi.ts`
- Create: `frontend/src/components/data-prep/SourceSelector.tsx`
- Create: `frontend/src/components/data-prep/UploadSourceForm.tsx`
- Create: `frontend/src/components/data-prep/HttpApiSourceForm.tsx`
- Create: `frontend/src/components/data-prep/ScopeForm.tsx`
- Create: `frontend/src/components/data-prep/PreviewPanel.tsx`
- Create: `frontend/src/components/data-prep/SchemaPreview.tsx`
- Create: `frontend/src/components/data-prep/RecipePreview.tsx`
- Create: `frontend/src/components/data-prep/QualityResultPanel.tsx`
- Create: `frontend/src/components/data-prep/ArtifactDownloads.tsx`
- Modify: `frontend/src/pages/Chat.tsx`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1：定义前端类型**

```typescript
export type DataSourceKind = "web" | "upload" | "http_api";

export interface DataTaskPreview {
  probe: Record<string, unknown>;
  sample: Array<Record<string, unknown>>;
  schema: { fields: Array<{ name: string; dtype: string }> };
  parser_warnings: string[];
  recipe: Record<string, unknown>;
  estimated_records: number | null;
  estimated_bytes: number | null;
  high_impact_rules: string[];
}
```

- [ ] **Step 2：实现带鉴权上传客户端**

```typescript
export async function uploadDataSource(file: File) {
  const body = new FormData();
  body.append("file", file);
  const token = getToken();
  const response = await fetch("/api/data-sources/uploads", {
    method: "POST",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    body,
  });
  if (!response.ok) throw new ApiError(response.status, "文件上传失败");
  return response.json();
}
```

- [ ] **Step 3：实现结构化来源选择**

在 Chat 的 data_prep 模式显示 `SourceSelector`，支持 URL/关键词、上传文件、HTTP API。旧分析模式保持原输入区，不渲染这些组件。

- [ ] **Step 4：实现预览面板**

显示：来源探测、最多 20 条样本、Schema、解析告警、Recipe 影响和预计规模。存在高影响规则时正式执行按钮先显示确认状态。

- [ ] **Step 5：实现结果面板**

复用现有下载方法，按干净数据、rejects、lineage、schema、quality、manifest 分组；质量结论显示 pass/warn/fail，不只显示自由文本。

- [ ] **Step 6：构建前端**

```powershell
npm --prefix frontend run build
```

Expected：TypeScript 和 Vite 构建成功。

- [ ] **Step 7：提交**

```powershell
git add frontend/src/types/dataPrep.ts frontend/src/lib/dataPrepApi.ts frontend/src/components/data-prep frontend/src/pages/Chat.tsx frontend/src/lib/api.ts
git commit -m "feat: 增加文件与 API 数据准备前端闭环`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 12：安全、性能和最终回归

**Files:**
- Create: `tests/test_phase2_performance.py`
- Modify: `tests/test_file_upload_security.py`
- Modify: `tests/test_archive_security.py`
- Modify: `tests/test_http_security.py`
- Modify: `tests/test_data_task_api.py`
- Modify: `AGENTS.md`
- Modify: `README_AGENT.md`
- Modify: `plan.md`
- Modify: `.env.example`

- [ ] **Step 1：增加 100 万行流式测试**

```python
def test_million_row_jsonl_is_processed_in_batches(tmp_path):
    source = tmp_path / "million.jsonl"
    with source.open("w", encoding="utf-8") as fh:
        for index in range(1_000_000):
            fh.write(json.dumps({"id": index}, ensure_ascii=False) + "\n")

    result = run_file_pipeline(source, batch_size=10_000)
    assert result.record_counts["parsed"] == 1_000_000
    assert max(ref.record_count for ref in result.parsed_batches) <= 10_000
```

性能测试默认标记 `@pytest.mark.performance`，常规 CI 可跳过，但发布验收必须运行并记录峰值内存。

- [ ] **Step 2：运行完整安全矩阵**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest `
  tests/test_file_upload_security.py `
  tests/test_archive_security.py `
  tests/test_http_security.py `
  tests/test_data_task_api.py -q
```

Expected：路径穿越、越权、ZIP Slip、ZIP 炸弹、SSRF、重定向绕过和凭证泄漏全部阻断。

- [ ] **Step 3：运行 Phase 2 全量测试**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest `
  tests/test_batch_pipeline.py `
  tests/test_checkpoint_resume.py `
  tests/test_file_upload_security.py `
  tests/test_file_connector.py `
  tests/test_tabular_parsers.py `
  tests/test_json_xml_parsers.py `
  tests/test_pdf_parser.py `
  tests/test_office_parser.py `
  tests/test_archive_security.py `
  tests/test_http_security.py `
  tests/test_http_pagination.py `
  tests/test_http_api_connector.py `
  tests/test_data_task_api.py -q
```

Expected：全部通过。

- [ ] **Step 4：运行旧链路回归**

```powershell
E:\python3.13\python.exe -X utf8 -m pytest tests -q
npm --prefix frontend run build
git diff --check
```

Expected：全部测试和构建通过，差异无空白错误。

- [ ] **Step 5：浏览器端到端验收**

Run：

```powershell
.\start_all.bat
```

依次验证：

1. 上传 CSV，预览 Schema 和样本，执行后下载 JSONL/Parquet/Manifest。
2. 上传含坏行 JSONL，确认坏行出现在 parse rejects。
3. 配置本地测试 HTTP 服务的 page 分页，确认全部页面被采集且无重复。
4. 刷新会话，确认质量和下载入口仍存在。
5. 切换 `legacy_analysis`，确认旧分析链路仍可执行。

- [ ] **Step 6：同步文档**

文档明确：格式支持矩阵、限制、环境变量、上传/API 操作步骤、安全边界、质量产物、已知降级路径和 Phase 3 范围。

- [ ] **Step 7：提交验收与文档**

```powershell
git add tests/test_phase2_performance.py tests AGENTS.md README_AGENT.md plan.md .env.example
git commit -m "test: 完成数据准备 Phase 2 验收`n`nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 4. 配置项基线

在 `src/config/settings.py` 和 `.env.example` 增加并保持同名：

```dotenv
DATA_PREP_UPLOAD_ROOT=data/uploads
DATA_PREP_MAX_UPLOAD_BYTES=524288000
DATA_PREP_PREVIEW_MAX_BYTES=10485760
DATA_PREP_PREVIEW_MAX_RECORDS=20
DATA_PREP_BATCH_RECORDS=10000
DATA_PREP_JSON_ARRAY_MAX_BYTES=52428800
DATA_PREP_ZIP_MAX_FILES=1000
DATA_PREP_ZIP_MAX_DEPTH=3
DATA_PREP_ZIP_MAX_UNCOMPRESSED_BYTES=1073741824
DATA_PREP_ZIP_MAX_RATIO=100
DATA_PREP_HTTP_TIMEOUT_SECONDS=30
DATA_PREP_HTTP_MAX_REDIRECTS=5
DATA_PREP_HTTP_MAX_RESPONSE_BYTES=104857600
DATA_PREP_HTTP_MAX_PAGES=10000
DATA_PREP_HTTP_PRIVATE_HOST_ALLOWLIST=
```

生产环境应将上传目录和 downloads 目录放到持久化卷；私网 API 白名单默认留空。

## 5. 实施门禁

- Task 0 未完成，不得开始 Phase 2 业务实现。
- Task 1–2 未完成，不得宣称支持大文件。
- 上传 API 未完成归属与路径安全测试，不得接前端。
- HTTP Connector 未完成 SSRF 和重定向测试，不得开放给用户。
- ZIP 解析未通过炸弹与路径逃逸测试，不得注册到 ParserRegistry。
- 质量门为 fail 时，不得把输出标记为干净数据。
- 不得把 upload 绝对路径、Authorization、Cookie、Token 或数据库式凭证写入 state、Manifest、trace 或错误响应。
- 不得在本阶段加入数据库输入、媒体输入、S3、队列或 Recipe 管理等 Phase 3–5 内容。

## 6. 推荐执行批次

1. **基础批次**：Task 0–2，冻结 Phase 1 并完成批次数据面。
2. **文件批次**：Task 3–7，安全上传和文件格式矩阵。
3. **HTTP 批次**：Task 8–10，安全 HTTP、分页和任务 API。
4. **产品批次**：Task 11，前端最小闭环。
5. **验收批次**：Task 12，安全、性能、回归和文档。

每个批次结束都运行对应定向测试；进入下一批次前，不保留已知失败。

## 7. 风险与应对

| 风险 | 应对 |
|---|---|
| 500 MB 文件导致进程内存线性增长 | Task 1–2 先完成批次数据面，性能测试记录峰值内存 |
| PDF/OCR 结果不稳定 | 页级来源、置信度和 rejects；无证据不补写 |
| ZIP 炸弹或路径逃逸 | 成员数、深度、总量、压缩比和规范化路径多重限制 |
| HTTP SSRF | 每次请求和重定向重新做 DNS/IP 检查，私网默认拒绝 |
| API 分页重复或无限循环 | 页面哈希、最大页数、cursor/offset checkpoint 和重复页停止 |
| 凭证泄漏 | credential_ref 注入、统一 scrub、序列化测试 |
| 大 JSON 数组无法流式 | 超阈值明确拒绝并建议 JSONL，不做全量内存解析 |
| 前端范围膨胀 | 只做来源、预览、执行和结果，不做数据编辑器或 Recipe 管理中心 |

## 8. 最终验收记录模板

Phase 2 完成时在发布说明中记录：

```text
Python: 3.13.x
核心依赖 PoC: PASS
Phase 1 回归: PASS（测试数）
Phase 2 单元/集成: PASS（测试数）
前端构建: PASS
文件格式矩阵: CSV/TSV/JSON/JSONL/XLSX/Parquet/TXT/HTML/XML/PDF/DOCX/ZIP
HTTP 分页矩阵: page/offset/cursor/Link Header
安全矩阵: 路径穿越/越权/ZIP Slip/ZIP 炸弹/SSRF/重定向/凭证泄漏
性能样例: 文件大小、记录数、批次大小、峰值内存、总耗时
浏览器 E2E: 文件任务 PASS；HTTP API 任务 PASS；legacy_analysis 回归 PASS
```
