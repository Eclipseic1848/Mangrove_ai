# -*- coding: utf-8 -*-
"""文档元素稳定标识与证据辅助函数。"""
from __future__ import annotations

import hashlib

from src.data_prep.document_models import ElementType


def stable_element_id(
    artifact_id: str,
    page: int,
    element_type: ElementType,
    reading_order: int,
    text: str,
) -> str:
    """由文档范围、页、类型、顺序和内容生成稳定 ID。"""
    normalized = " ".join((text or "").split())
    seed = f"{artifact_id}\n{page}\n{element_type.value}\n{reading_order}\n{normalized}"
    return "el_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
