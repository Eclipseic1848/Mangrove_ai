"""清洗引擎（plan.md 第 8 节）。

原则：原始层永不修改；默认只做低风险标准化；有争议规则必须显式启用；
同样的 RawArtifact + Recipe + 引擎版本必须产生一致输出。
- models.py：清洗内部模型（规则执行结果、批次账本）
- engine.py：Recipe 引擎，逐规则逐批次执行
- profiler.py：清洗前数据剖析（记录数/空值/类型/分布/重复/异常）
- rules/：规则实现；首批迁移现有网页清洗规则
"""
from __future__ import annotations
