# -*- coding: utf-8 -*-
"""确定性生成并重开 v2 盲集来源。"""
from __future__ import annotations

import csv
import io
from pathlib import Path
import re
from xml.etree import ElementTree as ET
import zipfile


FIELDS = ("record_id", "item", "segment", "amount", "used", "status", "date", "revision")
FIXED_ZIP_TIME = (2026, 8, 20, 12, 0, 0)


def _lines(tables: dict[str, list[dict[str, str]]]) -> list[str]:
    result: list[str] = []
    for name, rows in tables.items():
        result.append(f"TABLE={name}")
        for row in rows:
            result.append("|".join(f"{field}={row[field]}" for field in FIELDS))
    return result


def _parse_lines(lines: list[str]) -> dict[str, list[dict[str, str]]]:
    tables: dict[str, list[dict[str, str]]] = {}
    current = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("TABLE="):
            current = line.removeprefix("TABLE=")
            tables[current] = []
            continue
        if not current:
            raise ValueError("来源记录缺少 TABLE 标记")
        row = dict(part.split("=", 1) for part in line.split("|"))
        if tuple(row) != FIELDS:
            raise ValueError("来源字段或字段顺序无效")
        tables[current].append(row)
    return tables


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, entries[name])


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _write_docx(path: Path, tables: dict[str, list[dict[str, str]]]) -> None:
    paragraphs = "".join(
        f'<w:p><w:r><w:t>{_xml_escape(line)}</w:t></w:r></w:p>' for line in _lines(tables)
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}<w:sectPr/></w:body></w:document>"
    ).encode("utf-8")
    _write_zip(path, {
        "[Content_Types].xml": b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": document,
    })


def _write_xlsx(path: Path, tables: dict[str, list[dict[str, str]]]) -> None:
    sheets = []
    workbook_sheets = []
    rels = []
    for index, (name, rows) in enumerate(tables.items(), start=1):
        values = [list(FIELDS), *[[row[field] for field in FIELDS] for row in rows]]
        xml_rows = []
        for r_index, values_row in enumerate(values, start=1):
            cells = "".join(
                f'<c r="{chr(64 + c_index)}{r_index}" t="inlineStr"><is><t>{_xml_escape(str(value))}</t></is></c>'
                for c_index, value in enumerate(values_row, start=1)
            )
            xml_rows.append(f'<row r="{r_index}">{cells}</row>')
        sheets.append((f"xl/worksheets/sheet{index}.xml", ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + "</sheetData></worksheet>").encode("utf-8")))
        workbook_sheets.append(f'<sheet name="{_xml_escape(name)}" sheetId="{index}" r:id="rId{index}"/>')
        rels.append(f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>')
    entries = {
        "[Content_Types].xml": ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + "".join(f'<Override PartName="/{name}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for name, _ in sheets) + "</Types>").encode("utf-8"),
        "_rels/.rels": b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": ('<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + "".join(workbook_sheets) + "</sheets></workbook>").encode("utf-8"),
        "xl/_rels/workbook.xml.rels": ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(rels) + "</Relationships>").encode("utf-8"),
        **dict(sheets),
    }
    _write_zip(path, entries)


def write_output_xlsx(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    """写入一个只有 result 工作表的确定性表格。"""
    values = [columns, *[[row[column] for column in columns] for row in rows]]
    xml_rows = []
    for r_index, values_row in enumerate(values, start=1):
        cells = "".join(
            f'<c r="{chr(64 + c_index)}{r_index}" t="inlineStr"><is><t>{_xml_escape(str(value))}</t></is></c>'
            for c_index, value in enumerate(values_row, start=1)
        )
        xml_rows.append(f'<row r="{r_index}">{cells}</row>')
    sheet = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(xml_rows) + "</sheetData></worksheet>").encode("utf-8")
    _write_zip(path, {
        "[Content_Types].xml": b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": b'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="result" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
    })


def read_output_xlsx(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """重开单工作表 XLSX，并保留表头顺序。"""
    with zipfile.ZipFile(path) as archive:
        sheet_names = [name for name in archive.namelist() if name.startswith("xl/worksheets/") and name.endswith(".xml")]
        if len(sheet_names) != 1:
            raise ValueError("XLSX 必须且只能包含一个工作表")
        sheet = ET.fromstring(archive.read(sheet_names[0]))
    values = []
    for row_node in (node for node in sheet.iter() if node.tag.endswith("}row")):
        values.append(["".join(node.itertext()) for node in row_node if node.tag.endswith("}c")])
    if not values:
        raise ValueError("XLSX 缺少表头")
    columns = values[0]
    return columns, [dict(zip(columns, row)) for row in values[1:]]


def _write_pdf(path: Path, tables: dict[str, list[dict[str, str]]]) -> None:
    text = ["BT", "/F1 8 Tf", "40 800 Td"]
    for index, line in enumerate(_lines(tables)):
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if index:
            text.append("0 -12 Td")
        text.append(f"({escaped}) Tj")
    text.append("ET")
    stream = "\n".join(text).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode("ascii") + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects)+1}\n".encode("ascii"))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.write(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    path.write_bytes(out.getvalue())


def write_source(path: Path, fmt: str, tables: dict[str, list[dict[str, str]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=("table", *FIELDS))
            writer.writeheader()
            for table_name, rows in tables.items():
                for row in rows:
                    writer.writerow({"table": table_name, **row})
    elif fmt == "docx":
        _write_docx(path, tables)
    elif fmt == "xlsx":
        _write_xlsx(path, tables)
    elif fmt == "pdf":
        _write_pdf(path, tables)
    else:
        raise ValueError(f"不支持的来源格式：{fmt}")


def read_source(path: Path, fmt: str) -> dict[str, list[dict[str, str]]]:
    if fmt == "csv":
        with path.open("r", encoding="utf-8", newline="") as handle:
            result: dict[str, list[dict[str, str]]] = {}
            for row in csv.DictReader(handle):
                table_name = row.pop("table")
                result.setdefault(table_name, []).append(row)
            return result
    if fmt == "docx":
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        lines = ["".join(node.itertext()) for node in root.iter() if node.tag.endswith("}p")]
        return _parse_lines(lines)
    if fmt == "xlsx":
        with zipfile.ZipFile(path) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            names = [node.attrib["name"] for node in workbook.iter() if node.tag.endswith("}sheet")]
            result = {}
            for index, name in enumerate(names, start=1):
                sheet = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
                rows = []
                for row_node in (node for node in sheet.iter() if node.tag.endswith("}row")):
                    rows.append(["".join(node.itertext()) for node in row_node if node.tag.endswith("}c")])
                result[name] = [dict(zip(rows[0], row)) for row in rows[1:]]
            return result
    if fmt == "pdf":
        data = path.read_bytes().decode("ascii")
        lines = [match.replace("\\(", "(").replace("\\)", ")").replace("\\\\", "\\") for match in re.findall(r"\((.*)\) Tj", data)]
        return _parse_lines(lines)
    raise ValueError(f"不支持的来源格式：{fmt}")
