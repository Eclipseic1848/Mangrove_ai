"""SourceConnector 统一契约（plan.md 第 7.1 节）。

设计原则：Connector 只负责获取，不承担业务清洗。
- probe：验证可达性/权限/能力，不拉全量
- discover：可选，发现表/Sheet/文件成员/字段/分页
- read：异步分批输出原始制品或记录批次
- checkpoint：记录分页游标/水位线/已处理制品
- capabilities：能力声明，供 Planner 选择与降级
- close：释放会话/连接/临时资源

连接器结果统一返回成功状态、计数、字节数、checkpoint、告警与可重试/不可重试错误。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from src.data_prep.checkpoints import Checkpoint
from src.data_prep.models import (
    ConnectorCapability,
    RawArtifact,
    RecordEnvelope,
    SourceSpec,
)

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    """probe 结果：可达性、权限、能力探测（不拉全量）。"""
    reachable: bool
    message: str = ""
    capabilities: Set[ConnectorCapability] = field(default_factory=set)
    sample: Optional[Dict[str, Any]] = None   # 小样本/元数据，供预览
    warnings: List[str] = field(default_factory=list)


@dataclass
class RecordBatch:
    """read 产出的单批数据：原始制品 + 记录信封 + 本批 checkpoint。

    大数据按批产出，state 只持有引用（artifact_id/batch path），不持数据本身。
    """
    artifacts: List[RawArtifact] = field(default_factory=list)
    records: List[RecordEnvelope] = field(default_factory=list)
    checkpoint: Checkpoint = field(default_factory=Checkpoint)
    byte_count: int = 0
    warnings: List[str] = field(default_factory=list)
    retryable_error: Optional[str] = None     # 可重试错误（网络/限流）
    fatal_error: Optional[str] = None         # 不可重试错误（凭证失效/权限不足）

    @property
    def has_data(self) -> bool:
        return bool(self.records) or bool(self.artifacts)


class SourceConnector(ABC):
    """连接器抽象基类。子类按来源类型实现 read。

    约定：
    - name：唯一标识（web / upload_file / http_api / sqlite / mysql / pg / media）
    - source_type：匹配的 SourceType
    - capabilities：声明能力，供 Planner 路由
    - probe/read 失败分类为 retryable/fatal，不静默吞错
    """

    name: str = "base"
    source_type: str = "web"

    @abstractmethod
    async def probe(self, spec: SourceSpec) -> ProbeResult:
        """验证可达性/权限/能力，不拉全量。"""
        raise NotImplementedError

    async def discover(self, spec: SourceSpec) -> Dict[str, Any]:
        """可选：发现表/Sheet/文件成员/字段/分页信息。默认无。"""
        return {}

    @abstractmethod
    def read(
        self, spec: SourceSpec, checkpoint: Optional[Checkpoint] = None
    ) -> AsyncIterator[RecordBatch]:
        """异步分批读取。大数据按批产出；末批 checkpoint.is_final=True。"""
        raise NotImplementedError

    def capabilities(self) -> Set[ConnectorCapability]:
        """能力声明，默认空集，子类覆盖。"""
        return set()

    async def close(self) -> None:
        """释放会话/连接/临时资源。默认无。"""
        return None
