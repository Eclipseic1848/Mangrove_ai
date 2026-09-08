# -*- coding: utf-8 -*-
"""来源导出公共函数：原件身份、完整表示和缺口。"""
import hashlib
import io
import json
import zipfile

import pytest

from src.source_acquisition.export import write_source_entries


def _export(tmp_path, name, raw, *, table_format="none", provenance=None):
    path = tmp_path / name
    path.write_bytes(raw)
    source = dict(artifact_id="artifact-a", original_name=name, media_type="application/octet-stream",
                  sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw), provenance=provenance or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        entry = write_source_entries(archive, path=path, source=source, table_format=table_format)
    assert path.read_bytes() == raw
    archive = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    for file in entry["files"]:
        value = archive.read(file["path"])
        assert hashlib.sha256(value).hexdigest() == file["sha256"]
        assert len(value) == file["size_bytes"]
    return entry, archive


def test_json_export_preserves_root_types_null_and_nested_values(tmp_path):
    value = [{"名字": "甲", "空": None, "布尔": False, "数": 12.5, "嵌套": {"a": [1, None]}}, None, True, ["末尾"]]
    entry, archive = _export(tmp_path, "source.json", json.dumps(value, ensure_ascii=False).encode())
    representation = next(file for file in entry["files"] if file["format"] == "json" and file["role"] == "representation")
    assert json.loads(archive.read(representation["path"])) == value
    assert entry["parse"]["record_count"] == 4
    assert entry["parse"]["status"] == "complete"


def test_hash_drift_refuses_archive(tmp_path):
    path = tmp_path / "source.txt"
    path.write_bytes(b"changed")
    with zipfile.ZipFile(io.BytesIO(), "w") as archive:
        with pytest.raises(ValueError, match="identity"):
            write_source_entries(archive, path=path, source=dict(artifact_id="a", original_name="source.txt", media_type="text/plain", sha256="0"*64, size_bytes=7))


def test_html_keeps_full_readable_text_links_and_public_provenance(tmp_path):
    text = "完整中文正文" * 1100
    raw = f'<html><body><div>{text}</div><a href="/next?p=2">下一页</a><img src="/not-fetched.png"><script>秘密脚本</script></body></html>'.encode()
    entry, archive = _export(tmp_path, "page.html", raw, provenance={"final_url": "https://example.test/page", "read_at": "2026-09-08", "runtime_meta": {"path": "secret"}})
    value = json.loads(archive.read("sources/artifact-a/representations/representation.json"))
    markdown = archive.read("sources/artifact-a/representations/source.md").decode()
    assert text in markdown and text in value["text"]
    assert {item["url"] for item in value["links"]} == {"https://example.test/next?p=2", "https://example.test/not-fetched.png"}
    assert all(item["status"] == "not_saved" for item in value["links"])
    assert "![" not in markdown and "<img" not in markdown
    assert "runtime_meta" not in entry["provenance"]
    assert "秘密脚本" not in markdown


def test_jsonl_partial_preserves_valid_scalar_rows(tmp_path):
    entry, archive = _export(tmp_path, "rows.jsonl", b'{"x":null}\ninvalid\nfalse\n[1,2]\n')
    assert entry["parse"]["status"] == "partial"
    rows = [json.loads(line) for line in archive.read("sources/artifact-a/representations/records.jsonl").splitlines()]
    assert rows == [{"x": None}, False, [1, 2]]
    assert entry["parse"]["record_count"] == 3


@pytest.mark.parametrize("format", ["csv", "xlsx"])
def test_optional_table_is_formula_safe_and_json_retains_values(tmp_path, format):
    entry, archive = _export(tmp_path, "data.json", b'[{"name":"=1+1","nested":{"x":null},"missing":null}]', table_format=format)
    table = next(file for file in entry["files"] if file["format"] == format)
    raw = archive.read(table["path"])
    if format == "csv":
        import csv
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        assert rows[0]["name"] == "'=1+1"
    else:
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(raw), data_only=False)
        assert workbook.active["A2"].value == "=1+1"
        assert workbook.active["A2"].data_type == "s"
        workbook.close()


def test_image_and_oversized_input_keep_original_and_explicit_gap(tmp_path, monkeypatch):
    import src.source_acquisition.export as exporter
    for name in ("image.png", "source.txt"):
        monkeypatch.setattr(exporter, "_PARSE_BYTES", 2)
        entry, archive = _export(tmp_path, name, b"original")
        assert entry["parse"]["status"] == "unavailable"
        assert archive.read(entry["files"][0]["path"]) == b"original"


def test_local_text_and_scanned_pdf_do_not_call_ocr(tmp_path, monkeypatch):
    from PIL import Image
    from src.parsers import pdf
    # 在实际调用位置打补丁，避免首次导入缓存替身并污染后续任务。
    monkeypatch.setattr(pdf, "configured_document_parser_clients", lambda: (_ for _ in ()).throw(AssertionError("no OCR")))
    entry, archive = _export(tmp_path, "source.txt", "完整正文\n尾部内容".encode())
    assert entry["parse"]["status"] == "complete"
    assert "尾部内容" in archive.read("sources/artifact-a/representations/source.md").decode()
    buffer = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(buffer, format="PDF")
    entry, archive = _export(tmp_path, "scan.pdf", buffer.getvalue())
    assert entry["parse"]["status"] != "complete"
    assert entry["parse"]["gaps"]


def test_csv_duplicate_headers_and_ragged_rows_are_not_silent_success(tmp_path):
    entry, _ = _export(tmp_path, "rows.csv", b'x,x\n1,2\n3,4,5\n')
    assert entry["parse"]["status"] != "complete"
    assert entry["parse"]["gaps"]


def test_text_markdown_is_readable_without_element_metadata_or_duplicate_body(tmp_path):
    _, archive = _export(tmp_path, "notes.txt", "第一段正文\n第二段正文".encode())
    markdown = archive.read("sources/artifact-a/representations/source.md").decode()
    assert "第一段正文\n\n第二段正文" in markdown
    assert markdown.count("第一段正文") == 1
    assert "element_id" not in markdown
    assert "extractor" not in markdown


@pytest.mark.parametrize("raw,gap", [
    (b'{"x":1,"x":2}', "duplicate_json_keys_use_original"),
    (b'{"x":0.123456789012345678901}', "json_number_precision_use_original"),
])
def test_json_lossy_standard_conversion_is_explicit_partial(tmp_path, raw, gap):
    entry, archive = _export(tmp_path, "source.json", raw)
    assert entry["parse"]["status"] == "partial"
    assert gap in entry["parse"]["gaps"]
    assert archive.read(entry["files"][0]["path"]) == raw


def test_html_uses_first_base_href(tmp_path):
    _, archive = _export(tmp_path, "page.html", b'<base href="/assets/"><base href="/wrong/"><a href="next.html">next</a>', provenance={"final_url": "https://example.test/page"})
    value = json.loads(archive.read("sources/artifact-a/representations/representation.json"))
    assert value["links"] == [{"url": "https://example.test/assets/next.html", "status": "not_saved"}]


def test_csv_header_escape_collision_is_not_silent_data_loss(tmp_path):
    entry, archive = _export(tmp_path, "rows.json", json.dumps([{"=x": 1, "'=x": 2}]).encode(), table_format="csv")
    assert entry["parse"]["status"] == "partial"
    assert "csv_header_collision_use_json" in entry["parse"]["gaps"]
    assert not any(file["format"] == "csv" for file in entry["files"])
    assert json.loads(archive.read("sources/artifact-a/representations/representation.json")) == [{"=x": 1, "'=x": 2}]


@pytest.mark.parametrize("limit", ["_XLSX_ROWS", "_XLSX_COLUMNS"])
def test_xlsx_shape_limit_is_explicit(tmp_path, monkeypatch, limit):
    import src.source_acquisition.export as exporter
    monkeypatch.setattr(exporter, limit, 1, raising=False)
    entry, _ = _export(tmp_path, "rows.json", b'[{"a":1,"b":2},{"a":3,"b":4}]', table_format="xlsx")
    assert "xlsx_shape_limit_use_json" in entry["parse"]["gaps"]
    assert not any(file["format"] == "xlsx" for file in entry["files"])


def test_parquet_reuses_local_parser(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq
    buffer = io.BytesIO()
    pq.write_table(pa.table({"name": ["甲", "乙"], "value": [1, None]}), buffer)
    entry, archive = _export(tmp_path, "rows.parquet", buffer.getvalue())
    assert entry["parse"]["status"] == "complete"
    assert json.loads(archive.read("sources/artifact-a/representations/representation.json"))["records"] == [{"name": "甲", "value": 1}, {"name": "乙", "value": None}]


@pytest.mark.parametrize("values", [[b"binary"], [float("nan")]])
def test_unserializable_parquet_keeps_original_with_explicit_gap(tmp_path, values):
    import pyarrow as pa
    import pyarrow.parquet as pq
    buffer = io.BytesIO()
    pq.write_table(pa.table({"value": values}), buffer)
    raw = buffer.getvalue()
    entry, archive = _export(tmp_path, "unsupported.parquet", raw)
    assert entry["parse"]["status"] == "unavailable"
    assert entry["parse"]["record_count"] == 0
    assert "local_representation_not_serializable" in entry["parse"]["gaps"]
    assert archive.read(entry["files"][0]["path"]) == raw
    assert json.loads(archive.read("sources/artifact-a/representations/representation.json")) == {"records": []}
