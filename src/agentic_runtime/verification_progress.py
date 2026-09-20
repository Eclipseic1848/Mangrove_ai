"""任务无关的验证补救预算，只接受宿主提供的证据状态。"""
from __future__ import annotations

import json


class VerificationProgress:
    """连续两轮未增加证据或减少缺口时，停止重复补救。"""

    def __init__(self) -> None:
        self._evidence: set[str] = set()
        self._best_gaps: int | None = None
        self._counts: dict[str, int] = {}
        self._unchanged = 0

    def observe(self, *, gaps: list[str], evidence: dict) -> bool:
        if not gaps:
            self._unchanged = 0
            return True
        improved = self._best_gaps is None or len(gaps) < self._best_gaps
        self._best_gaps = min(self._best_gaps or len(gaps), len(gaps))
        # 顺序、换措辞、缓存次数、仅更换解析器版本均不算补救进展。
        for key, value in evidence.items():
            if key in {"cache_hits", "parser_versions"}:
                continue
            if key in {"low_quality_units", "unknown_units"}:
                count = len(value or ())
                improved |= key in self._counts and count < self._counts[key]
                self._counts[key] = min(self._counts.get(key, count), count)
            elif isinstance(value, list):
                facts = {key + ":" + json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value}
                improved |= bool(facts - self._evidence)
                self._evidence.update(facts)
            elif isinstance(value, int):
                improved |= value > self._counts.get(key, 0)
                self._counts[key] = max(self._counts.get(key, 0), value)
        self._unchanged = 0 if improved else self._unchanged + 1
        return self._unchanged < 2
