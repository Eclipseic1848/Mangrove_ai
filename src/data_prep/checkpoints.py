"""断点续跑 checkpoint（plan.md 第 5.2/7.1 节）。

连接器分页/水位线游标的统一表示与序列化。Phase 1 互联网链路单批即终；
Phase 3 数据库/Phase 2 API 增量时 checkpoint 承载真实游标，支持断点恢复。

归属说明：plan 13.1 把 checkpoints.py 放在 src/data_prep/ 下（数据准备内核资产），
src/connectors/base.py 的 SourceConnector 依赖它。data_prep.checkpoints 不反向导入
connectors，故无循环依赖。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set


@dataclass
class Checkpoint:
    """断点续跑游标：分页位置/水位线值/已处理 artifact_id 集合。"""
    cursor: Optional[str] = None              # 下一页游标（page token / offset）
    watermark: Optional[str] = None           # 增量水位线值（如 updated_at）
    processed_artifact_ids: Set[str] = field(default_factory=set)
    processed_record_keys: Set[str] = field(default_factory=set)
    page: int = 0
    completed_batch_ids: list[str] = field(default_factory=list)
    next_part_no: int = 0
    is_final: bool = False                    # 是否已到末尾，无更多数据

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cursor": self.cursor,
            "watermark": self.watermark,
            "processed_artifact_ids": sorted(self.processed_artifact_ids),
            "processed_record_keys": sorted(self.processed_record_keys),
            "page": self.page,
            "completed_batch_ids": list(self.completed_batch_ids),
            "next_part_no": self.next_part_no,
            "is_final": self.is_final,
        }


__all__ = ["Checkpoint"]
