# -*- coding: utf-8 -*-
"""稳定字节的 PDF、DOCX、XLSX、CSV 来源与结果 I/O。"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
import zipfile


FIELDS = ("unit_id", "label", "zone", "quota", "spent", "state", "rank", "version")
ZIP_TIME = (2024, 2, 2, 2, 2, 2)


def _esc(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _lines(sections: dict[str, list[dict[str, str]]]) -> list[str]:
    lines = []
    for name, rows in sections.items():
        lines.append(f"SECTION={name}")
        lines.extend("|".join(f"{field}={row[field]}" for field in FIELDS) for row in rows)
    return lines


def _parse_lines(lines: list[str]) -> dict[str, list[dict[str, str]]]:
    result, current = {}, ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("SECTION="):
            current = line[8:]
            result[current] = []
        else:
            row = dict(part.split("=", 1) for part in line.split("|"))
            if not current or tuple(row) != FIELDS:
                raise ValueError("来源区段或字段无效")
            result[current].append(row)
    return result


def _zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, entries[name])


def _docx(path: Path, sections: dict) -> None:
    body = "".join(f'<w:p><w:r><w:t>{_esc(line)}</w:t></w:r></w:p>' for line in _lines(sections))
    _zip(path, {
        "[Content_Types].xml": b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": (f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}<w:sectPr/></w:body></w:document>').encode(),
    })


def _xlsx_entries(sheets: list[tuple[str, list[list[str]]]]) -> dict[str, bytes]:
    sheet_entries, declarations, relationships, overrides = {}, [], [], []
    for idx, (name, rows) in enumerate(sheets, start=1):
        xml_rows = []
        for ridx, row in enumerate(rows, start=1):
            cells = "".join(f'<c r="{chr(64+cidx)}{ridx}" t="inlineStr"><is><t>{_esc(str(value))}</t></is></c>' for cidx, value in enumerate(row, start=1))
            xml_rows.append(f'<row r="{ridx}">{cells}</row>')
        path = f"xl/worksheets/sheet{idx}.xml"
        sheet_entries[path] = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + "</sheetData></worksheet>").encode()
        declarations.append(f'<sheet name="{_esc(name)}" sheetId="{idx}" r:id="rId{idx}"/>')
        relationships.append(f'<Relationship Id="rId{idx}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{idx}.xml"/>')
        overrides.append(f'<Override PartName="/{path}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
    return {
        "[Content_Types].xml": ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + "".join(overrides) + "</Types>").encode(),
        "_rels/.rels": b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": ('<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + "".join(declarations) + "</sheets></workbook>").encode(),
        "xl/_rels/workbook.xml.rels": ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(relationships) + "</Relationships>").encode(),
        **sheet_entries,
    }


def _xlsx(path: Path, sections: dict) -> None:
    sheets = [(name, [list(FIELDS), *[[row[field] for field in FIELDS] for row in rows]]) for name, rows in sections.items()]
    _zip(path, _xlsx_entries(sheets))


def _pdf(path: Path, sections: dict) -> None:
    commands = ["BT", "/F1 8 Tf", "35 805 Td"]
    for idx, line in enumerate(_lines(sections)):
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if idx:
            commands.append("0 -11 Td")
        commands.append(f"({safe}) Tj")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>", b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>", b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    out, offsets = io.BytesIO(), [0]
    out.write(b"%PDF-1.4\n")
    for idx, obj in enumerate(objs, start=1):
        offsets.append(out.tell()); out.write(f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell(); out.write(f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]: out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()); path.write_bytes(out.getvalue())


def write_source(path: Path, fmt: str, sections: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("section", *FIELDS)); writer.writeheader()
            for name, rows in sections.items():
                for row in rows: writer.writerow({"section": name, **row})
    elif fmt == "docx": _docx(path, sections)
    elif fmt == "xlsx": _xlsx(path, sections)
    elif fmt == "pdf": _pdf(path, sections)
    else: raise ValueError(fmt)


def read_source(path: Path, fmt: str) -> dict:
    if fmt == "csv":
        result = {}
        with path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                name = row.pop("section"); result.setdefault(name, []).append(row)
        return result
    if fmt == "docx":
        with zipfile.ZipFile(path) as z: root = ET.fromstring(z.read("word/document.xml"))
        return _parse_lines(["".join(node.itertext()) for node in root.iter() if node.tag.endswith("}p")])
    if fmt == "pdf":
        text = path.read_bytes().decode("ascii")
        return _parse_lines([x.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\") for x in re.findall(r"\((.*)\) Tj", text)])
    if fmt == "xlsx":
        with zipfile.ZipFile(path) as z:
            wb = ET.fromstring(z.read("xl/workbook.xml")); names = [n.attrib["name"] for n in wb.iter() if n.tag.endswith("}sheet")]
            result = {}
            for idx, name in enumerate(names, start=1):
                root = ET.fromstring(z.read(f"xl/worksheets/sheet{idx}.xml")); values = [["".join(c.itertext()) for c in row if c.tag.endswith("}c")] for row in root.iter() if row.tag.endswith("}row")]
                result[name] = [dict(zip(values[0], row)) for row in values[1:]]
            return result
    raise ValueError(fmt)


def write_result_xlsx(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    _zip(path, _xlsx_entries([("result", [columns, *[[row[c] for c in columns] for row in rows]])]))


def read_result_xlsx(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.startswith("xl/worksheets/") and n.endswith(".xml")]
        if len(names) != 1: raise ValueError("XLSX 必须只有一个工作表")
        root = ET.fromstring(z.read(names[0])); values = [["".join(c.itertext()) for c in row if c.tag.endswith("}c")] for row in root.iter() if row.tag.endswith("}row")]
    if not values: raise ValueError("XLSX 缺少表头")
    return values[0], [dict(zip(values[0], row)) for row in values[1:]]


def _strict_rows(rows: object, columns: list[str], *, require_key_order: bool) -> list[dict[str, str]]:
    if not isinstance(rows, list):
        raise ValueError("rows 必须是数组")
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(columns):
            raise ValueError("每行必须是键集合严格匹配 columns 的对象")
        if require_key_order and list(row) != columns:
            raise ValueError("records 每行键顺序必须严格匹配 exact_columns")
        normalized.append({key: "" if row[key] is None else str(row[key]) for key in columns})
    return normalized


def write_table(path: Path, fmt: str, columns: list[str], rows: list[dict[str, str]], json_shape: str | None = None) -> None:
    """按冻结表格契约写候选；JSON 两种形态都使用对象行。"""
    if fmt == "csv":
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader(); writer.writerows(rows)
        return
    if fmt == "xlsx":
        write_result_xlsx(path, columns, rows); return
    if fmt != "json":
        raise ValueError(f"不支持的候选格式：{fmt}")
    records = [{column: row[column] for column in columns} for row in rows]
    if json_shape == "records":
        payload = records
    elif json_shape == "columns_rows":
        payload = {"columns": columns, "rows": records}
    else:
        raise ValueError("JSON 缺少有效 json_shape")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_table(path: Path, fmt: str, columns: list[str], json_shape: str | None = None) -> tuple[list[str], list[dict[str, str]]]:
    """按同一公开契约重开 CSV/JSON/XLSX 候选。"""
    if path.suffix.lower() != "." + fmt:
        raise ValueError("候选扩展名与冻结格式不一致")
    if fmt == "csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle); actual = list(reader.fieldnames or []); rows = list(reader)
        return actual, _strict_rows(rows, actual, require_key_order=True)
    if fmt == "xlsx":
        actual, rows = read_result_xlsx(path); return actual, _strict_rows(rows, actual, require_key_order=True)
    if fmt != "json":
        raise ValueError(f"不支持的候选格式：{fmt}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if json_shape == "records":
        if not isinstance(payload, list): raise ValueError("JSON 必须是 records 对象数组")
        return columns, _strict_rows(payload, columns, require_key_order=True)
    if json_shape == "columns_rows":
        if not isinstance(payload, dict) or set(payload) != {"columns", "rows"}: raise ValueError("JSON 必须是键集合严格等于 columns、rows 的对象")
        actual = payload.get("columns")
        if not isinstance(actual, list) or any(not isinstance(item, str) for item in actual): raise ValueError("columns 必须是字符串数组")
        return actual, _strict_rows(payload.get("rows"), actual, require_key_order=False)
    raise ValueError("JSON 缺少有效 json_shape")
