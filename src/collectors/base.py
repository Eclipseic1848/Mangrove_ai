"""
采集器基类与统一数据结构。

所有采集器实现 BaseCollector，对外暴露统一的 collect(spec) -> CollectResult，
使上层 Router 可以无差别地选择、降级、并存多个采集引擎。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.conductor.task_spec import TaskSpec

from ._common import is_challenge_page


@dataclass
class CollectedItem:
    """单条采集结果（统一的最小数据单元）。"""
    url: str = ""
    title: str = ""
    content: str = ""                       # 正文（Markdown 或纯文本）
    metadata: Dict[str, Any] = field(default_factory=dict)
    access_handle: str | None = field(default=None, repr=False)  # 仅适配器解密，不进入公开字段

    @property
    def is_error_page(self) -> bool:
        """复用短验证页边界；空正文的视频清单不属于错误页。"""
        status = self.metadata.get("status_code", self.metadata.get("status"))
        return (isinstance(status, int) and status >= 400) or is_challenge_page(self.content or "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "metadata": self.metadata,
        }


@dataclass
class CollectResult:
    """一次采集的结果汇总。"""
    success: bool
    collector: str
    items: List[CollectedItem] = field(default_factory=list)
    message: str = ""
    coverage: Dict[str, Any] = field(default_factory=dict)
    authentication: Dict[str, Any] = field(default_factory=dict)

    def discard_error_pages(self) -> None:
        """在路由认定成功前排除错误/验证页，避免截断后续反爬升级。"""
        usable = [item for item in self.items if not item.is_error_page]
        if len(usable) != len(self.items):
            self.items = usable
            self.message = f"已排除错误或验证页，保留 {len(usable)} 条可用资料"
            if not usable:
                self.success = False

    @property
    def has_data(self) -> bool:
        return self.success and len(self.items) > 0


class BaseCollector(ABC):
    """采集器抽象基类。

    约定：
    - name：唯一标识。
    - tier：路由优先级，数值越小越优先（专用适配器用小值，通用引擎用大值，兜底最大）。
    - is_available()：依赖/配置是否就绪（缺失则路由跳过）。
    - matches(spec)：是否适用该任务（专用采集器据此匹配平台）。
    - collect(spec)：执行采集。
    """

    name: str = "base"
    tier: int = 50

    def is_available(self) -> bool:
        """依赖与配置是否就绪。默认 True，子类按需覆盖。"""
        return True

    def matches(self, spec: TaskSpec) -> bool:
        """是否适用该任务。通用采集器返回 True，专用采集器按平台判断。"""
        return True

    @abstractmethod
    async def collect(self, spec: TaskSpec) -> CollectResult:
        """执行采集（异步）。"""
        raise NotImplementedError
