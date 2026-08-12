"""
共享 frontmatter 解析：`skills/*.md`（手写技能）与 `data/templates/*.md`（自学习模板）
都用 `---\nYAML\n---\n正文` 这套格式，抽出来避免两处各自维护一份正则+解析逻辑。
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


class FrontmatterError(Exception):
    """有 frontmatter 分隔符但 YAML 解析失败（区别于"根本没有 frontmatter"，
    调用方按各自的日志级别处理这两种情况）。"""


def parse_frontmatter(raw: str) -> Optional[Tuple[dict, str]]:
    """解析 `---\nYAML\n---\n正文` 格式。

    返回 (meta, body)（body 已 strip）；完全没有 frontmatter（如纯文档）返回 None；
    有 frontmatter 但 YAML 解析失败抛 FrontmatterError。
    """
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return None
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except Exception as e:
        raise FrontmatterError(str(e)) from e
    return meta, m.group(2).strip()
