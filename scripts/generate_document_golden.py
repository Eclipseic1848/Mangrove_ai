#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 Phase 4A 固定合成黄金集：24 份文档，共 120 页。"""
from __future__ import annotations

import argparse
import io
import json
import random
from pathlib import Path

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = PROJECT_ROOT / "tests" / "fixtures" / "document_golden"


def _font_path() -> Path:
    candidates = [
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("未找到可复现的中文/Unicode 字体")


def _case(domain: str, number: int) -> dict:
    if domain == "contract":
        values = {
            "contract_no": f"HT-2026-{number:03d}",
            "delivery_days": f"{20 + number} days",
            "amount": f"CNY {100000 + number * 1234:,}.00",
        }
        title = "Synthetic Purchase Contract"
    elif domain == "bid":
        values = {
            "project_no": f"BID-2026-{number:03d}",
            "deadline": f"2026-09-{10 + number:02d} 17:00",
            "bond": f"CNY {5000 + number * 500:,}.00",
        }
        title = "Synthetic Tender Notice"
    else:
        values = {
            "invoice_no": f"INV-2026-{number:03d}",
            "total": f"CNY {1200 + number * 88:,}.50",
            "tax_id": f"91320000TEST{number:04d}",
        }
        title = "Synthetic Invoice Pack"
    return {"domain": domain, "number": number, "title": title, "values": values}


def _page_lines(case: dict, page_no: int) -> list[str]:
    values = case["values"]
    common = [
        case["title"],
        f"Synthetic sample {case['domain']}-{case['number']:02d} / page {page_no}",
        "This document contains no real person, company, account or transaction data.",
    ]
    if page_no == 2:
        common.extend(f"{key}: {value}" for key, value in values.items())
    elif page_no in (4, 5):
        common.append("Cross-page table continuation")
        common.extend(
            f"Row {page_no * 10 + i:02d} | Item-{i:02d} | Qty {i + 1} | Amount {100 * (i + 1)}"
            for i in range(1, 9)
        )
    else:
        common.extend(["Section: General terms", "Evidence must be traced to this page."])
    return common


def _scan_image(lines: list[str], font_path: Path, rng: random.Random) -> Image.Image:
    image = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.truetype(str(font_path), 42)
    body_font = ImageFont.truetype(str(font_path), 29)
    y = 100
    for index, line in enumerate(lines):
        draw.text((90, y), line, fill=(25, 25, 25), font=title_font if index == 0 else body_font)
        y += 78 if index == 0 else 55
    draw.ellipse((900, 1330, 1140, 1570), outline=(190, 30, 30), width=10)
    draw.text((945, 1420), "TEST", fill=(190, 30, 30), font=body_font)
    pixels = image.load()
    for _ in range(1600):
        x, y = rng.randrange(image.width), rng.randrange(image.height)
        shade = rng.randrange(180, 235)
        pixels[x, y] = (shade, shade, shade)
    angle = rng.choice((-1.0, -0.5, 0.5, 1.0))
    return image.rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor="white")


def _add_digital_page(pdf: FPDF, lines: list[str]) -> None:
    pdf.add_page()
    pdf.set_font("GoldenUnicode", size=14)
    for line in lines:
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 9, line)


def _add_scan_page(pdf: FPDF, lines: list[str], font_path: Path, rng: random.Random, *, mixed: bool) -> None:
    image = _scan_image(lines, font_path, rng)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=82, optimize=True)
    buffer.seek(0)
    pdf.add_page()
    if mixed:
        pdf.set_font("GoldenUnicode", size=10)
        pdf.cell(0, 6, "Machine-readable header / mixed page")
        pdf.image(buffer, x=8, y=18, w=194, h=270)
    else:
        pdf.image(buffer, x=0, y=0, w=210, h=297)


def _write_case(output: Path, case: dict, mode: str, font_path: Path) -> dict:
    rng = random.Random(f"{case['domain']}-{case['number']}-{mode}")
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_font("GoldenUnicode", fname=str(font_path))
    page_modes = []
    for page_no in range(1, 6):
        lines = _page_lines(case, page_no)
        if mode == "digital":
            page_mode = "digital"
            _add_digital_page(pdf, lines)
        elif mode == "scanned":
            page_mode = "scanned"
            _add_scan_page(pdf, lines, font_path, rng, mixed=False)
        else:
            page_mode = ("digital", "scanned", "mixed", "digital", "scanned")[page_no - 1]
            if page_mode == "digital":
                _add_digital_page(pdf, lines)
            else:
                _add_scan_page(pdf, lines, font_path, rng, mixed=page_mode == "mixed")
        page_modes.append(page_mode)

    filename = f"{case['domain']}_{case['number']:02d}_{mode}.pdf"
    path = output / filename
    path.write_bytes(bytes(pdf.output()))
    return {
        "id": path.stem,
        "file": filename,
        "domain": case["domain"],
        "mode": mode,
        "pages": 5,
        "page_modes": page_modes,
        "expected_fields": {
            name: {"value": value, "page": 2, "quote": f"{name}: {value}"}
            for name, value in case["values"].items()
        },
        "features": ["rotation", "noise", "stamp", "cross_page_table"] if mode != "digital" else ["cross_page_table"],
        "license": "CC0-1.0 synthetic",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    font_path = _font_path()

    layout = {
        "contract": ["digital"] * 4 + ["scanned"] * 4 + ["mixed"] * 4,
        "bid": ["digital"] * 3 + ["scanned"] * 2 + ["mixed"] * 2,
        "invoice": ["digital"] * 2 + ["scanned"] * 2 + ["mixed"],
    }
    documents = []
    for domain, modes in layout.items():
        for number, mode in enumerate(modes, start=1):
            documents.append(_write_case(output, _case(domain, number), mode, font_path))

    manifest = {
        "schema_version": "1",
        "generated_by": "scripts/generate_document_golden.py",
        "seed_strategy": "stable per domain-number-mode",
        "document_count": len(documents),
        "page_count": sum(item["pages"] for item in documents),
        "distribution": {"contract": 12, "bid": 7, "invoice": 5},
        "documents": documents,
    }
    (output / "expected.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已生成 {manifest['document_count']} 份文档 / {manifest['page_count']} 页：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
