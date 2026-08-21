# -*- coding: utf-8 -*-
"""独立盲集的最小源文件写入与逻辑表读取工具。"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _zip_writestr(archive: zipfile.ZipFile, name: str, data: str | bytes) -> None:
    """固定 ZIP 元数据，确保 DOCX/XLSX 来源可重复生成。"""

    info = zipfile.ZipInfo(name, date_time=(2026, 8, 20, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, data)


def _table(columns: list[str], rows: list[list[str]]) -> dict:
    return {
        "columns": [str(value) for value in columns],
        "rows": [
            {str(column): str(value) for column, value in zip(columns, row, strict=True)}
            for row in rows
        ],
    }


def write_csv_source(path: Path, tables: dict[str, dict]) -> None:
    if len(tables) != 1:
        raise ValueError("CSV 源只能包含一个逻辑表")
    table = next(iter(tables.values()))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=table["columns"])
        writer.writeheader()
        writer.writerows(table["rows"])


def read_csv_source(path: Path, table_name: str) -> dict[str, dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows = [{column: str(row.get(column, "")) for column in columns} for row in reader]
    return {table_name: {"columns": columns, "rows": rows}}


def _docx_paragraph(text: str) -> str:
    return f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"


def _docx_cell(text: str) -> str:
    return f"<w:tc><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:tc>"


def write_docx_source(path: Path, title: str, tables: dict[str, dict]) -> None:
    body = [_docx_paragraph(title)]
    for table_name, table in tables.items():
        body.append(_docx_paragraph(f"TABLE: {table_name}"))
        rows = [table["columns"]] + [
            [row[column] for column in table["columns"]] for row in table["rows"]
        ]
        body.append(
            "<w:tbl>"
            + "".join(
                "<w:tr>" + "".join(_docx_cell(value) for value in row) + "</w:tr>"
                for row in rows
            )
            + "</w:tbl>"
        )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        + "".join(body)
        + "<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{PKG_REL_NS}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _zip_writestr(archive, "[Content_Types].xml", content_types)
        _zip_writestr(archive, "_rels/.rels", relationships)
        _zip_writestr(archive, "word/document.xml", document)


def _node_text(node: ET.Element) -> str:
    return "".join(part.text or "" for part in node.iter(f"{{{W_NS}}}t"))


def read_docx_source(path: Path) -> dict[str, dict]:
    with zipfile.ZipFile(path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{{{W_NS}}}body")
    if body is None:
        raise ValueError(f"DOCX 缺少 body：{path}")
    table_name: str | None = None
    tables: dict[str, dict] = {}
    for child in body:
        if child.tag == f"{{{W_NS}}}p":
            text = _node_text(child)
            if text.startswith("TABLE: "):
                table_name = text[7:]
        elif child.tag == f"{{{W_NS}}}tbl":
            if not table_name:
                raise ValueError(f"DOCX 表格缺少 TABLE 标题：{path}")
            matrix = [
                [_node_text(cell) for cell in row.findall(f"{{{W_NS}}}tc")]
                for row in child.findall(f"{{{W_NS}}}tr")
            ]
            if not matrix:
                raise ValueError(f"DOCX 空表格：{path}")
            tables[table_name] = _table(matrix[0], matrix[1:])
            table_name = None
    return tables


def _column_name(index: int) -> str:
    name = ""
    current = index
    while current:
        current, remainder = divmod(current - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _sheet_xml(table: dict) -> str:
    matrix = [table["columns"]] + [
        [row[column] for column in table["columns"]] for row in table["rows"]
    ]
    xml_rows: list[str] = []
    for row_index, row in enumerate(matrix, start=1):
        cells = []
        for column_index, value in enumerate(row, start=1):
            reference = f"{_column_name(column_index)}{row_index}"
            cells.append(
                f'<c r="{reference}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'
            )
        xml_rows.append(f'<row r="{row_index}">' + "".join(cells) + "</row>")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{S_NS}"><sheetData>'
        + "".join(xml_rows)
        + "</sheetData></worksheet>"
    )


def write_xlsx_source(path: Path, tables: dict[str, dict]) -> None:
    sheet_names = list(tables)
    content_types = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
    ]
    for index in range(1, len(sheet_names) + 1):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    content_types.append("</Types>")
    root_relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{PKG_REL_NS}">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(sheet_names, start=1)
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{S_NS}" xmlns:r="{R_NS}"><sheets>{sheets}</sheets></workbook>'
    )
    workbook_relationships = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<Relationships xmlns="{PKG_REL_NS}">'
        + "".join(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
            for index in range(1, len(sheet_names) + 1)
        )
        + "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _zip_writestr(archive, "[Content_Types].xml", "".join(content_types))
        _zip_writestr(archive, "_rels/.rels", root_relationships)
        _zip_writestr(archive, "xl/workbook.xml", workbook)
        _zip_writestr(archive, "xl/_rels/workbook.xml.rels", workbook_relationships)
        for index, table in enumerate(tables.values(), start=1):
            _zip_writestr(archive, f"xl/worksheets/sheet{index}.xml", _sheet_xml(table))


def read_xlsx_source(path: Path) -> dict[str, dict]:
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            relationship.attrib["Id"]: relationship.attrib["Target"]
            for relationship in relationships.findall(f"{{{PKG_REL_NS}}}Relationship")
        }
        tables: dict[str, dict] = {}
        sheets = workbook.find(f"{{{S_NS}}}sheets")
        if sheets is None:
            raise ValueError(f"XLSX 缺少 sheets：{path}")
        for sheet in sheets:
            name = sheet.attrib["name"]
            relationship_id = sheet.attrib[f"{{{R_NS}}}id"]
            target = targets[relationship_id].lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            root = ET.fromstring(archive.read(target))
            matrix: list[list[str]] = []
            for row in root.iter(f"{{{S_NS}}}row"):
                values: list[str] = []
                for cell in row.findall(f"{{{S_NS}}}c"):
                    inline = cell.find(f"{{{S_NS}}}is")
                    if inline is not None:
                        value = "".join(text.text or "" for text in inline.iter(f"{{{S_NS}}}t"))
                    else:
                        value_node = cell.find(f"{{{S_NS}}}v")
                        value = value_node.text if value_node is not None else ""
                    values.append(value)
                matrix.append(values)
            if not matrix:
                raise ValueError(f"XLSX 空工作表：{path}#{name}")
            tables[name] = _table(matrix[0], matrix[1:])
    return tables


def _pdf_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_unescape(value: str) -> str:
    result: list[str] = []
    escaped = False
    for character in value:
        if escaped:
            result.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        else:
            result.append(character)
    if escaped:
        result.append("\\")
    return "".join(result)


def write_pdf_source(path: Path, title: str, tables: dict[str, dict]) -> None:
    lines = [title]
    for table_name, table in tables.items():
        lines.append(f"TABLE: {table_name}")
        lines.append("FIELDS: " + " | ".join(table["columns"]))
        for row in table["rows"]:
            lines.append("ROW: " + " | ".join(row[column] for column in table["columns"]))
    stream = (
        "BT /F1 9 Tf 40 770 Td 11 TL "
        + " ".join(f"({_pdf_escape(line)}) Tj T*" for line in lines)
        + " ET"
    ).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    output = bytearray(b"%PDF-1.4\n%independent-heldout\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(
            "ascii"
        )
    )
    path.write_bytes(output)


def read_pdf_source(path: Path) -> dict[str, dict]:
    raw = path.read_bytes().decode("ascii")
    lines = [_pdf_unescape(match) for match in re.findall(r"\(((?:\\.|[^\\)])*)\) Tj", raw)]
    tables: dict[str, dict] = {}
    table_name: str | None = None
    columns: list[str] | None = None
    rows: list[list[str]] = []

    def flush() -> None:
        nonlocal table_name, columns, rows
        if table_name is not None and columns is not None:
            tables[table_name] = _table(columns, rows)
        table_name = None
        columns = None
        rows = []

    for line in lines:
        if line.startswith("TABLE: "):
            flush()
            table_name = line[7:]
        elif line.startswith("FIELDS: "):
            columns = line[8:].split(" | ")
        elif line.startswith("ROW: "):
            rows.append(line[5:].split(" | "))
    flush()
    return tables


def read_source(path: Path, source_format: str, *, csv_table_name: str | None = None) -> dict[str, dict]:
    if source_format == "csv":
        if not csv_table_name:
            raise ValueError("读取 CSV 必须提供逻辑表名")
        return read_csv_source(path, csv_table_name)
    if source_format == "docx":
        return read_docx_source(path)
    if source_format == "xlsx":
        return read_xlsx_source(path)
    if source_format == "pdf":
        return read_pdf_source(path)
    raise ValueError(f"不支持的来源格式：{source_format}")


def canonical_table(columns: list[str], rows: list[list[str]]) -> dict:
    """供定义文件使用，确保所有源值都以 UTF-8 字符串冻结。"""

    return _table(columns, rows)
