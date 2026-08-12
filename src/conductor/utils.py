"""Conductor 通用工具：稳健的 LLM JSON 解析等。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict

try:
    from json_repair import repair_json  # 容错解析，优先使用

    _HAS_REPAIR = True
except Exception:  # 未安装则退化为标准库 json
    repair_json = None  # type: ignore
    _HAS_REPAIR = False


def parse_json_obj(text: str) -> Dict[str, Any]:
    """
    从 LLM 文本输出中稳健地解析出 JSON 对象。

    处理常见情况：被 ```json 代码块包裹、前后有解释性文字、轻微语法错误。
    解析失败时返回空字典，调用方据此走兜底逻辑。
    """
    if not text:
        return {}

    # 去掉 ```json ... ``` 代码块围栏
    fenced = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text

    # 截取第一个 { 到最后一个 } 之间，去除前后噪声
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = candidate[start : end + 1]

    if _HAS_REPAIR:
        try:
            repaired = repair_json(candidate, return_objects=True)
            return repaired if isinstance(repaired, dict) else {}
        except Exception:
            return {}

    # 退化：标准库 json
    try:
        obj = json.loads(candidate)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}
