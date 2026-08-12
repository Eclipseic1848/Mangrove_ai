"""解析器注册表（plan.md 第 7.3 节）。

Parser 与 Connector 分离：根据魔数/MIME/扩展名/用户提示选择，优先信任内容探测。
Phase 1 仅 WebParser（互联网链路）；Phase 2+ 注册 tabular/json_xml/pdf/office 等。
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from src.data_prep.models import RawArtifact, RecordEnvelope

logger = logging.getLogger(__name__)


class Parser(ABC):
    """解析器基类：把原始制品转为统一记录信封，保留位置信息。"""

    name: str = "base"
    media_types: Tuple[str, ...] = ()
    extensions: Tuple[str, ...] = ()

    @abstractmethod
    def parse(self, artifact: RawArtifact, raw_bytes: bytes) -> Tuple[List[RecordEnvelope], List[Dict]]:
        """解析原始制品。

        返回 (records, parse_rejects)。parse_rejects 含原因与原始定位。
        """
        raise NotImplementedError


class WebParser(Parser):
    """网页解析器：读取 WebAdapter 存储的 collector 输出（JSON），转为记录信封。

    说明（plan 10.2 适配）：现有网页 collector 已内置 fetch+extract，输出 url/title/content。
    WebAdapter 把该输出存为 RawArtifact；本解析器读取并标准化为 RecordEnvelope。
    Phase 2+ 文件/API/DB 连接器会严格分离 fetch 与 parse。
    """

    name = "web"
    media_types = ("application/json", "text/html")
    extensions = ("json", "html")

    def parse(self, artifact: RawArtifact, raw_bytes: bytes) -> Tuple[List[RecordEnvelope], List[Dict]]:
        try:
            payload = json.loads(raw_bytes.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            reject = {
                "artifact_id": artifact.artifact_id,
                "reason": f"JSON 解析失败: {e}",
                "position": {},
            }
            return [], [reject]

        # 单条 collector 输出 -> 单条记录信封
        url = payload.get("url") or ""
        title = payload.get("title") or ""
        content = payload.get("content") or ""
        metadata = payload.get("metadata") or {}
        import hashlib
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        record_id = hashlib.sha256((url + content[:200]).encode("utf-8")).hexdigest()[:16] or content_hash[:16]

        env = RecordEnvelope(
            record_id=record_id,
            data={"url": url, "title": title, "content": content},
            meta={
                "source_id": artifact.source_id,
                "artifact_id": artifact.artifact_id,
                "observed_at": (artifact.fetched_at.isoformat() if artifact.fetched_at else None),
                "parser": "web@1",
                "content_hash": content_hash,
                "raw_metadata": metadata,
            },
        )
        return [env], []


class ParserRegistry:
    """解析器注册表。按 media_type/extension/hint 选择，优先内容探测。"""

    def __init__(self) -> None:
        self._by_name: Dict[str, Parser] = {}
        self._by_media: Dict[str, Parser] = {}
        self._by_ext: Dict[str, Parser] = {}

    def register(self, parser: Parser) -> None:
        self._by_name[parser.name] = parser
        for mt in parser.media_types:
            self._by_media[mt] = parser
        for ext in parser.extensions:
            self._by_ext[ext.lower()] = parser

    def select(
        self, media_type: Optional[str] = None, extension: Optional[str] = None, hint: Optional[str] = None
    ) -> Optional[Parser]:
        """选择解析器：hint > media_type > extension。"""
        if hint and hint in self._by_name:
            return self._by_name[hint]
        if media_type:
            mt = media_type.split(";")[0].strip().lower()
            if mt in self._by_media:
                return self._by_media[mt]
        if extension:
            ext = extension.lower().lstrip(".")
            if ext in self._by_ext:
                return self._by_ext[ext]
        return None

    def get(self, name: str) -> Optional[Parser]:
        return self._by_name.get(name)


# 默认注册表实例（web 已注册；Phase 2+ 解析器延迟注册避免循环导入）
_default_registry = ParserRegistry()
_default_registry.register(WebParser())

# Phase 2 解析器注册标记（首次 get_parser_registry 时延迟导入）
_phase2_registered = False


def _register_phase2_parsers() -> None:
    global _phase2_registered
    if _phase2_registered:
        return
    from src.parsers.tabular import TabularParser
    from src.parsers.json_xml import JsonXmlParser
    from src.parsers.text_html_xml import HtmlParser, TextParser, XmlParser
    from src.parsers.pdf import PdfParser
    from src.parsers.office import OfficeParser
    from src.parsers.presentation import PresentationParser
    from src.parsers.markdown import MarkdownParser
    from src.parsers.archive import ArchiveParser
    from src.parsers.image import ImageParser

    _default_registry.register(TabularParser())
    _default_registry.register(JsonXmlParser())
    _default_registry.register(TextParser())
    _default_registry.register(HtmlParser())
    _default_registry.register(XmlParser())
    _default_registry.register(PdfParser())
    _default_registry.register(OfficeParser())
    _default_registry.register(PresentationParser())
    _default_registry.register(MarkdownParser())
    _default_registry.register(ArchiveParser())
    _default_registry.register(ImageParser())
    _phase2_registered = True


def get_parser_registry() -> ParserRegistry:
    _register_phase2_parsers()
    return _default_registry
