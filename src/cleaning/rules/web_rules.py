"""网页清洗规则集（plan.md 第 8.2 节，plan 13.5 改造清单）。

从 src/conductor/nodes/clean.py 迁移为版本化 Recipe 规则。
行为严格保持兼容：clean.py 的 6 个单测用例必须不回归（plan 14 Phase 1 退出标准）。

迁移策略：常量与判定函数自包含复制（不 import 旧节点），便于旧 clean.py
按 plan 13.6/13.8 门禁下线后规则独立存活。原实现见 clean.py（仍保留作兼容）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from src.data_prep.models import RecordEnvelope, RecipeStage

from ..models import RejectRecord
from .base import Rule

# ---- 以下常量与函数与 clean.py 保持一致（行为兼容基线）----

_MIN_CONTENT_LEN = 12
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t　]+")
_BLANK_RE = re.compile(r"\n{3,}")

_CAPTCHA_SIGNALS = (
    "验证码", "安全验证", "人机验证", "访问异常", "访问受限", "请开启JavaScript",
    "请启用JavaScript", "系统繁忙", "拒绝访问", "Access Denied", "403 Forbidden",
    "404 Not Found", "Just a moment", "Checking your browser", "点击继续访问",
    "异常流量", "滑动验证",
)
_CAPTCHA_MAX_LEN = 400
_GARBLED_RATIO = 0.15


def _denoise(text: str) -> str:
    """去 HTML 标签、折叠空白、清理 markdown 残留（与 clean.py 一致）。"""
    if not text:
        return ""
    t = _TAG_RE.sub(" ", text)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    t = _WS_RE.sub(" ", t)
    t = _BLANK_RE.sub("\n\n", t)
    t = "\n".join(line.strip() for line in t.splitlines())
    return t.strip()


def _is_boilerplate(text: str) -> bool:
    if len(text) >= _MIN_CONTENT_LEN:
        return False
    return not any(p in text for p in "。！？.!?，,")


def _is_captcha_page(text: str) -> bool:
    if len(text) > _CAPTCHA_MAX_LEN:
        return False
    return any(s in text for s in _CAPTCHA_SIGNALS)


def _is_garbled(text: str) -> bool:
    if not text:
        return False
    bad = sum(1 for ch in text if ch == "�" or (ord(ch) < 32 and ch not in "\n\r\t"))
    return bad / len(text) > _GARBLED_RATIO


# ---- 规则实现 ----

class WebDenoiseRule(Rule):
    """HTML 去噪 + 空白折叠。转换类规则（可逆：原始内容在 RawArtifact 中保留）。"""

    rule_id = "web_denoise"
    stage = RecipeStage.CONTENT_CLEAN
    high_impact = False
    reversible = True

    def apply(
        self, records: List[RecordEnvelope], params: Dict[str, Any]
    ) -> Tuple[List[RecordEnvelope], List[RejectRecord]]:
        kept: List[RecordEnvelope] = []
        for r in records:
            content = (r.data.get("content") or "")
            r.data["content"] = _denoise(content)
            kept.append(r)
        return kept, []


class WebDropEmptyRule(Rule):
    """去噪后正文为空 -> 隔离（原因：空内容）。"""

    rule_id = "web_drop_empty"
    stage = RecipeStage.CONTENT_CLEAN

    def apply(
        self, records: List[RecordEnvelope], params: Dict[str, Any]
    ) -> Tuple[List[RecordEnvelope], List[RejectRecord]]:
        kept: List[RecordEnvelope] = []
        rejects: List[RejectRecord] = []
        for r in records:
            content = (r.data.get("content") or "").strip()
            if not content:
                rejects.append(RejectRecord(record=r, reason="空内容", rule_id=self.rule_id, stage=self.stage))
            else:
                kept.append(r)
        return kept, rejects


class WebDropBoilerplateRule(Rule):
    """超短且无句读 -> 隔离（原因：样板噪声）。"""

    rule_id = "web_drop_boilerplate"
    stage = RecipeStage.CONTENT_CLEAN

    def apply(
        self, records: List[RecordEnvelope], params: Dict[str, Any]
    ) -> Tuple[List[RecordEnvelope], List[RejectRecord]]:
        kept: List[RecordEnvelope] = []
        rejects: List[RejectRecord] = []
        for r in records:
            content = (r.data.get("content") or "").strip()
            if _is_boilerplate(content):
                rejects.append(RejectRecord(record=r, reason="样板噪声", rule_id=self.rule_id, stage=self.stage))
            else:
                kept.append(r)
        return kept, rejects


class WebDropCaptchaRule(Rule):
    """短正文 + 命中反爬验证页特征词 -> 隔离（原因：验证页）。长文不误杀。"""

    rule_id = "web_drop_captcha_page"
    stage = RecipeStage.CONTENT_CLEAN

    def apply(
        self, records: List[RecordEnvelope], params: Dict[str, Any]
    ) -> Tuple[List[RecordEnvelope], List[RejectRecord]]:
        kept: List[RecordEnvelope] = []
        rejects: List[RejectRecord] = []
        for r in records:
            content = (r.data.get("content") or "").strip()
            if _is_captcha_page(content):
                rejects.append(RejectRecord(record=r, reason="验证页", rule_id=self.rule_id, stage=self.stage))
            else:
                kept.append(r)
        return kept, rejects


class WebDropGarbledRule(Rule):
    """替换符/不可打印字符占比过高 -> 隔离（原因：乱码）。"""

    rule_id = "web_drop_garbled"
    stage = RecipeStage.CONTENT_CLEAN

    def apply(
        self, records: List[RecordEnvelope], params: Dict[str, Any]
    ) -> Tuple[List[RecordEnvelope], List[RejectRecord]]:
        kept: List[RecordEnvelope] = []
        rejects: List[RejectRecord] = []
        for r in records:
            content = (r.data.get("content") or "")
            if _is_garbled(content):
                rejects.append(RejectRecord(record=r, reason="乱码", rule_id=self.rule_id, stage=self.stage))
            else:
                kept.append(r)
        return kept, rejects


class WebDedupRule(Rule):
    """按 url + 正文前 200 字去重（与 clean.py 一致）。不可逆。"""

    rule_id = "web_dedup_url_prefix"
    stage = RecipeStage.DEDUP
    reversible = False

    def apply(
        self, records: List[RecordEnvelope], params: Dict[str, Any]
    ) -> Tuple[List[RecordEnvelope], List[RejectRecord]]:
        kept: List[RecordEnvelope] = []
        rejects: List[RejectRecord] = []
        seen: set[Tuple[str, str]] = set()
        for r in records:
            url = (r.data.get("url") or "")
            content = (r.data.get("content") or "")
            key = (url, content[:200])
            if key in seen:
                rejects.append(RejectRecord(record=r, reason="重复", rule_id=self.rule_id, stage=self.stage))
            else:
                seen.add(key)
                kept.append(r)
        return kept, rejects


class WebCapRule(Rule):
    """封顶保留 max_items 条（与 clean.py 的 max_items 截断一致）。"""

    rule_id = "web_cap_max_items"
    stage = RecipeStage.ANOMALY_ISOLATION

    def apply(
        self, records: List[RecordEnvelope], params: Dict[str, Any]
    ) -> Tuple[List[RecordEnvelope], List[RejectRecord]]:
        max_items = int(params.get("max_items", 0) or 0)
        if max_items <= 0 or len(records) <= max_items:
            return records, []
        kept = records[:max_items]
        rejects = [
            RejectRecord(record=r, reason="超出上限", rule_id=self.rule_id, stage=self.stage)
            for r in records[max_items:]
        ]
        return kept, rejects


# 默认网页清洗 Recipe 规则顺序（与 clean.py 执行顺序一致；cap 在末位，max_items<=0 时无操作）
DEFAULT_WEB_RULES: List[Rule] = [
    WebDenoiseRule(),
    WebDropEmptyRule(),
    WebDropBoilerplateRule(),
    WebDropCaptchaRule(),
    WebDropGarbledRule(),
    WebDedupRule(),
    WebCapRule(),
]

# 规则注册表：rule_id -> Rule 类（供引擎按 RecipeRule.rule_id 实例化）
WEB_RULE_REGISTRY: Dict[str, type] = {
    "web_denoise": WebDenoiseRule,
    "web_drop_empty": WebDropEmptyRule,
    "web_drop_boilerplate": WebDropBoilerplateRule,
    "web_drop_captcha_page": WebDropCaptchaRule,
    "web_drop_garbled": WebDropGarbledRule,
    "web_dedup_url_prefix": WebDedupRule,
    "web_cap_max_items": WebCapRule,
}
