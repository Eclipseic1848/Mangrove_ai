"""
记忆与技能加载（loops Engineering 的 Memory/Skills 层）。

- 记忆：读取 memory/user-preferences.md，把跨会话的用户偏好注入意图理解提示词；
  并支持在会话中追加新偏好（持久化到该文件）。
- 技能：读取 skills/*.md（每个文件必须带 YAML frontmatter 声明 inject/trigger），
  按 TaskSpec（analyze 侧）或恒触发（planner 侧）匹配后注入对应节点提示词。

文件读写，mtime 缓存加速读，锁保护并发写，写后即时生效。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict

from src.config.settings import PROJECT_ROOT
from src.conductor.task_spec import TaskSpec
from src.memory._frontmatter import FrontmatterError, parse_frontmatter
from src.memory._io import atomic_write, MtimeCache

logger = logging.getLogger(__name__)

MEMORY_DIR = PROJECT_ROOT / "memory"
SKILLS_DIR = PROJECT_ROOT / "skills"
_PREF_FILE = "user-preferences.md"
_PREF_SECTION = "## 用户偏好"

_preferences_lock = threading.Lock()
_preferences_cache = MtimeCache()
_skills_cache = MtimeCache()


# ---- 记忆：用户偏好 ----
def load_preferences() -> str:
    """读取用户偏好记忆全文，mtime 缓存加速；不存在或失败返回空串。"""
    f = MEMORY_DIR / _PREF_FILE
    cached = _preferences_cache.get(f)
    if cached is not None:
        return cached
    try:
        result = f.read_text(encoding="utf-8").strip() if f.exists() else ""
    except Exception:
        logger.warning("读取用户偏好失败", exc_info=True)
        result = ""
    _preferences_cache.set(f, result)
    return result


def preferences_context() -> str:
    """构造注入意图提示词的偏好上下文（无偏好则空串）。"""
    pref = load_preferences()
    if not pref:
        return ""
    return (
        "\n\n# 用户偏好（记忆，跨会话）\n"
        f"{pref}\n"
        "在不与用户本轮明确指令冲突时，请遵循上述偏好。"
    )


def personal_context() -> str:
    """构造注入意图提示词的个人记忆上下文（当前用户自己写的，按用户隔离，见 src/config/user_ctx.py）。
    无个人记忆则返回空串。最多注入最近 20 条，防止长期堆积挤占 intent prompt。"""
    from src.config.user_ctx import get_user_memories

    items = get_user_memories()
    if not items:
        return ""
    capped = items[:20]
    lines = "\n".join(f"- {t}" for t in capped)
    return (
        "\n\n# 我的偏好（当前用户的个人记忆）\n"
        f"{lines}\n"
        "个人偏好与全局偏好冲突时，以个人偏好为准；与用户本轮明确指令冲突时以指令为准。"
    )


def add_preference(text: str) -> bool:
    """把一条偏好追加到 user-preferences.md 的「## 用户偏好」小节；加锁保护，成功返回 True。"""
    text = (text or "").strip().lstrip("-").strip()
    if not text:
        return False
    with _preferences_lock:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        f = MEMORY_DIR / _PREF_FILE
        content = f.read_text(encoding="utf-8") if f.exists() else (
            "# 记忆：用户偏好与任务模板\n\n" + _PREF_SECTION + "\n"
        )
        line = f"- {text}\n"
        if _PREF_SECTION in content:
            idx = content.index(_PREF_SECTION)
            insert_at = content.index("\n", idx) + 1
            content = content[:insert_at] + line + content[insert_at:]
        else:
            content += f"\n{_PREF_SECTION}\n{line}"
        atomic_write(f, content)
        _preferences_cache.invalidate()
        return True


# ---- 技能：声明式加载与匹配 ----
def load_skills() -> Dict[str, Dict[str, Any]]:
    """加载 skills/*.md，mtime 缓存加速；返回 {name: {name, title, body, inject, trigger}}。

    README.md 按文件名排除。其余文件若无 frontmatter / YAML 解析失败 / 正文为空，
    视为"本该是技能却没写对"的真实失误信号，跳过并打 info 日志留痕。
    """
    cached = _skills_cache.get(SKILLS_DIR)
    if cached is not None:
        return cached
    out: Dict[str, Dict[str, Any]] = {}
    if not SKILLS_DIR.exists():
        _skills_cache.set(SKILLS_DIR, out)
        return out
    for p in sorted(SKILLS_DIR.glob("*.md")):
        if p.stem.lower() == "readme":
            continue
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception:
            continue
        try:
            parsed = parse_frontmatter(raw)
        except FrontmatterError as e:
            logger.info("技能文件 frontmatter 解析失败，跳过：%s（%s）", p.name, e)
            continue
        if parsed is None:
            logger.info("技能文件无 frontmatter，跳过：%s", p.name)
            continue
        meta, body = parsed
        if not body:
            logger.info("技能文件正文为空，跳过：%s", p.name)
            continue
        out[p.stem] = {
            "name": p.stem,
            "title": str(meta.get("title") or p.stem),
            "body": body,
            "inject": str(meta.get("inject") or "").strip().lower(),
            "trigger": meta.get("trigger") or {},
        }
    _skills_cache.set(SKILLS_DIR, out)
    return out


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _skill_matches(trigger: Dict[str, Any], spec: TaskSpec) -> bool:
    """按 trigger 字段判断某技能是否命中当前 TaskSpec。各键之间 AND，
    列表型键内部 OR。trigger 为空视为永不命中（防止空 trigger 误伤）。"""
    if not trigger:
        return False
    if trigger.get("always"):
        return True
    if "analysis_type" in trigger:
        if spec.analysis_type.value != str(trigger["analysis_type"]).strip().lower():
            return False
    if "data_type" in trigger:
        allowed = {str(a).strip().lower() for a in _as_list(trigger["data_type"])}
        if spec.data_type.value not in allowed:
            return False
    if trigger.get("time_range_required") and not (spec.time_range or "").strip():
        return False
    if "intent_keywords" in trigger:
        haystack = ((spec.intent or "") + " " + " ".join(spec.keywords or [])).lower()
        kws = [str(k).strip().lower() for k in _as_list(trigger["intent_keywords"]) if str(k).strip()]
        if not any(k in haystack for k in kws):
            return False
    return True


def skill_for_analysis(spec: TaskSpec) -> str:
    """分析节点用：把 frontmatter 声明 inject: analyze 且 trigger 命中的技能正文
    依次追加进分析 system prompt；都不命中返回空串。"""
    parts = []
    for sk in load_skills().values():
        if sk["inject"] != "analyze":
            continue
        if _skill_matches(sk["trigger"], spec):
            parts.append(f"\n\n# 参考技能：{sk['title']}\n{sk['body']}")
    return "".join(parts)


def skills_for_planner() -> str:
    """规划节点用：把 frontmatter 声明 inject: planner 且 trigger.always 的技能正文
    追加进规划 system prompt。规划阶段 TaskSpec 尚未生成，暂只支持恒触发（见 Global Constraints）。"""
    parts = []
    for sk in load_skills().values():
        if sk["inject"] != "planner":
            continue
        if sk["trigger"].get("always"):
            parts.append(f"\n\n# 参考技能：{sk['title']}\n{sk['body']}")
    return "".join(parts)
