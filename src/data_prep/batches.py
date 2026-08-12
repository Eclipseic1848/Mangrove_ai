# -*- coding: utf-8 -*-
"""数据准备批次引用契约。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class BatchReference(BaseModel):
    """落盘批次的不可变引用，state 仅保存该摘要而不保存完整记录。"""

    batch_id: str
    dataset: str
    part_no: int = Field(ge=0)
    path: str
    record_count: int = Field(ge=0)
    byte_count: int = Field(ge=0)
    sha256: str
