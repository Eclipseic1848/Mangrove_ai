"""数据集输出器（plan.md 第 6.5 节，ADR-0005）。

同一份干净数据可一次生成多种格式；格式转换不重新执行清洗。
- JSONL：默认，流式，嵌套友好
- Parquet：默认，类型保真；pyarrow 缺失时优雅降级（告警跳过，ADR-0005）
- CSV/TSV：扁平化，附 Schema
- JSON：小规模单数组（超阈值拒绝，建议 JSONL，plan 6.5.1）
- XLSX：人工查看副本（openpyxl）

Phase 2 Task 2.5 阶段2：逐批导出，不持有完整记录集。
从 clean_batches（List[BatchReference]）逐批流式读取，两遍扫描：
第一遍累计字段并集 + 类型计数 + 总数（轻量，不存记录），
第二遍写各格式。峰值内存不随总记录数线性增长（plan 退出门禁）。

导出业务数据（RecordEnvelope.data），行级关联保留 _record_id（plan 6.3）。
lineage 由 clean_node 写出（手握完整 meta）；此处不重生成。
"""
from __future__ import annotations

import csv
import io
import json
import logging
from collections import Counter
from typing import Any, Dict, Iterator, List, Optional, Tuple

from src.config.settings import settings
from src.data_prep.batches import BatchReference
from src.data_prep.models import ManifestOutputEntry, OutputFormat

logger = logging.getLogger(__name__)

# JSON 单数组字节上限（plan 6.5.1：超大数据禁单数组 JSON，建议 JSONL）
_JSON_ARRAY_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


def _iter_clean_rows(store, batches: Optional[List[BatchReference]]) -> Iterator[Dict[str, Any]]:
    """逐批读 clean_batches 行（{_record_id, ...业务字段}），不全量物化。"""
    for ref in batches or []:
        for row in store.iter_jsonl(ref.path):
            yield row


def _flatten(row: Dict[str, Any]) -> Dict[str, Any]:
    """嵌套字段展开为 JSON 字符串（CSV/XLSX/Parquet 不能表达嵌套，plan 6.5.1）。"""
    out: Dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, (dict, list)):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = v
    return out


def _infer_pyarrow_type(types_set: set):
    """从 Python 类型名集合推断 pyarrow 类型（同质保真，混合降级 string）。"""
    import pyarrow as pa
    types = types_set - {"NoneType"}
    if not types:
        return pa.string()
    if types <= {"int"}:
        return pa.int64()
    if types <= {"int", "float"}:
        return pa.float64()
    if types <= {"bool"}:
        return pa.bool_()
    return pa.string()


def _cast_for_arrow(values: List[Any], arrow_type) -> List[Any]:
    """把 Python 值列表转为 pyarrow 类型安全的列表（None 保留为 null）。"""
    import pyarrow as pa
    if pa.types.is_int64(arrow_type):
        return [int(v) if v is not None else None for v in values]
    if pa.types.is_float64(arrow_type):
        return [float(v) if v is not None else None for v in values]
    if pa.types.is_boolean(arrow_type):
        return [bool(v) if v is not None else None for v in values]
    return [str(v) if v is not None else None for v in values]


def export_dataset(
    store,
    task_id: str,
    batches: Optional[List[BatchReference]],
    formats: List[OutputFormat],
) -> Tuple[List[ManifestOutputEntry], Dict[str, Any]]:
    """逐批导出数据集为多种格式，返回 (Manifest 条目, 推断 schema)。

    从 clean_batches 逐批流式读取（plan Phase 2 Task 2.5 阶段2），两遍扫描：
    第一遍累计字段并集 + 类型计数 + 总数；第二遍写各格式。不持有完整记录集。
    """
    formats = list(formats)
    clean_dir = store.task_dir(task_id) / "clean"
    clean_dir.mkdir(parents=True, exist_ok=True)

    # ---- 第一遍：累计字段并集 + 类型计数 + 总数（不存记录）----
    fields: List[str] = []
    seen_fields: set = set()
    type_counter: Dict[str, Counter] = {}
    total = 0
    for row in _iter_clean_rows(store, batches):
        total += 1
        for k, v in row.items():
            if k == "_record_id":
                continue
            if k not in seen_fields:
                seen_fields.add(k)
                fields.append(k)
                type_counter[k] = Counter()
            type_counter[k][type(v).__name__] += 1

    schema: Dict[str, Any] = {
        "fields": [{"name": k, "dtype": type_counter[k].most_common(1)[0][0]} for k in fields],
        "inferred": True,
        "record_count": total,
    }

    entries: List[ManifestOutputEntry] = []

    # ---- 第二遍：写各格式（逐批流式，一遍喂所有 writer）----
    jsonl_path = clean_dir / "data.jsonl"
    csv_path = clean_dir / "data.csv"
    tsv_path = clean_dir / "data.tsv"
    json_path = clean_dir / "data.json"
    parquet_path = clean_dir / "data.parquet"
    xlsx_path = clean_dir / "data.xlsx"

    jsonl_fh = None
    csv_fh = None
    csv_writer = None
    tsv_fh = None
    tsv_writer = None
    parquet_writer = None
    arrow_schema = None
    xlsx_wb = None
    xlsx_ws = None
    json_rows: List[Dict[str, Any]] = []
    json_bytes = 0
    json_exceeded = False

    try:
        if OutputFormat.JSONL in formats:
            jsonl_fh = jsonl_path.open("w", encoding="utf-8")
        if OutputFormat.CSV in formats:
            csv_fh = csv_path.open("w", encoding="utf-8-sig", newline="")
            csv_writer = csv.DictWriter(csv_fh, fieldnames=fields, delimiter=",", extrasaction="ignore")
            csv_writer.writeheader()
        if OutputFormat.TSV in formats:
            tsv_fh = tsv_path.open("w", encoding="utf-8-sig", newline="")
            tsv_writer = csv.DictWriter(tsv_fh, fieldnames=fields, delimiter="\t", extrasaction="ignore")
            tsv_writer.writeheader()
        if OutputFormat.PARQUET in formats:
            try:
                import pyarrow as pa
                import pyarrow.parquet as pq
                arrow_fields = [
                    pa.field(f, _infer_pyarrow_type(set(type_counter[f].keys())) if type_counter[f] else pa.string())
                    for f in fields
                ]
                arrow_schema = pa.schema(arrow_fields)
                parquet_writer = pq.ParquetWriter(parquet_path, arrow_schema, compression="snappy")
            except ImportError:
                logger.warning("pyarrow 不可用，Parquet 输出已跳过（ADR-0005 优雅降级）")
                parquet_writer = None
        if OutputFormat.XLSX in formats:
            try:
                from openpyxl import Workbook
                xlsx_wb = Workbook()
                xlsx_ws = xlsx_wb.active
                xlsx_ws.title = "data"
                xlsx_ws.append(fields)
            except ImportError:
                logger.warning("openpyxl 不可用，XLSX 输出已跳过")
                xlsx_wb = None
        # JSON 为缓冲模式，第二遍累计

        parquet_buffer: List[Dict[str, Any]] = []
        batch_size = max(1, settings.data_prep_batch_records)

        for row in _iter_clean_rows(store, batches):
            flat = _flatten(row)
            if jsonl_fh:
                jsonl_fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            if csv_writer:
                csv_writer.writerow({k: ("" if flat.get(k) is None else flat.get(k)) for k in fields})
            if tsv_writer:
                tsv_writer.writerow({k: ("" if flat.get(k) is None else flat.get(k)) for k in fields})
            if parquet_writer:
                parquet_buffer.append({f: flat.get(f) for f in fields})
                if len(parquet_buffer) >= batch_size:
                    _flush_parquet(parquet_writer, parquet_buffer, fields, arrow_schema)
                    parquet_buffer.clear()
            if OutputFormat.JSON in formats and not json_exceeded:
                line = json.dumps(row, ensure_ascii=False, default=str)
                json_bytes += len(line.encode("utf-8"))
                if json_bytes > _JSON_ARRAY_MAX_BYTES:
                    json_exceeded = True
                    json_rows = []  # 释放
                else:
                    json_rows.append(row)
            if xlsx_ws:
                xlsx_ws.append([flat.get(f) for f in fields])

        # flush Parquet 剩余
        if parquet_writer and parquet_buffer:
            _flush_parquet(parquet_writer, parquet_buffer, fields, arrow_schema)

        # ---- 关闭 writer 并收集 entries ----
        if jsonl_fh:
            jsonl_fh.close()
            jsonl_fh = None
            entries.append(_entry(OutputFormat.JSONL, _rel(store, jsonl_path), store, total))
        if csv_writer:
            csv_fh.close()
            csv_fh = None
            entries.append(_entry(OutputFormat.CSV, _rel(store, csv_path), store, total))
        if tsv_writer:
            tsv_fh.close()
            tsv_fh = None
            entries.append(_entry(OutputFormat.TSV, _rel(store, tsv_path), store, total))
        if parquet_writer:
            parquet_writer.close()
            parquet_writer = None
            entries.append(_entry(OutputFormat.PARQUET, _rel(store, parquet_path), store, total))
        if OutputFormat.JSON in formats:
            if json_exceeded:
                logger.warning(
                    "JSON 数组超过 %d 字节，已跳过（建议 JSONL，plan 6.5.1）",
                    _JSON_ARRAY_MAX_BYTES,
                )
            else:
                data = json.dumps(json_rows, ensure_ascii=False, indent=2).encode("utf-8")
                json_path.write_bytes(data)
                entries.append(_entry_raw(OutputFormat.JSON, _rel(store, json_path), store, total, data))
        if xlsx_wb is not None:
            buf = io.BytesIO()
            xlsx_wb.save(buf)
            data = buf.getvalue()
            xlsx_path.write_bytes(data)
            entries.append(_entry_raw(OutputFormat.XLSX, _rel(store, xlsx_path), store, total, data))
        if OutputFormat.SQLITE in formats:
            logger.info("SQLite 输出首版可选，暂跳过；Phase 2 补实现")
    finally:
        if jsonl_fh:
            jsonl_fh.close()
        if csv_fh:
            csv_fh.close()
        if tsv_fh:
            tsv_fh.close()
        if parquet_writer:
            parquet_writer.close()

    return entries, schema


def _flush_parquet(writer, batch_rows: List[Dict[str, Any]], fields: List[str], schema) -> None:
    """把一批行写入 ParquetWriter（类型安全转换，plan Phase 2 Task 2.5 阶段2）。"""
    import pyarrow as pa
    arrays = []
    for f in fields:
        arrow_type = schema.field(f).type
        vals = _cast_for_arrow([r.get(f) for r in batch_rows], arrow_type)
        arrays.append(pa.array(vals, type=arrow_type))
    table = pa.Table.from_arrays(arrays, schema=schema)
    writer.write_table(table)


def _rel(store, path) -> str:
    """绝对路径转相对 store.root 的 posix 路径（存入 Manifest 作引用）。"""
    return str(path.relative_to(store.root)).replace("\\", "/")


def _entry(fmt: OutputFormat, rel_path: str, store, records: int) -> ManifestOutputEntry:
    return ManifestOutputEntry(
        format=fmt, path=rel_path, sha256=store.file_sha256(rel_path), records=records
    )


def _entry_raw(fmt: OutputFormat, rel_path: str, store, records: int, data: bytes) -> ManifestOutputEntry:
    import hashlib
    return ManifestOutputEntry(
        format=fmt, path=rel_path, sha256=hashlib.sha256(data).hexdigest(), records=records
    )
