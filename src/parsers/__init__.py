"""解析器层（plan.md 第 7.3 节）。

Parser 与 Connector 分离：根据魔数/MIME/扩展名/用户提示选择，优先信任内容探测。
- registry.py：ParserRegistry
- tabular.py / json_xml.py / pdf.py / office.py / archive.py / media.py：
  Phase 2-4 按格式实现
"""
from __future__ import annotations
