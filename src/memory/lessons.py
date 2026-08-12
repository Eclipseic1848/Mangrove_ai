"""
失败教训的自学习沉淀（阶段B2：教训分流 lesson channel）。

当任务被判定为"采集失败/数据不足"时（见 checker.py 的 _looks_like_collection_failure），
不再是死胡同——把这类失败的教训（这类任务容易踩的坑+应对建议）蒸馏沉淀到 data/lessons/，
下次同类任务在 planner/analyze 节点提前注入提醒，帮助系统调整策略/约束报告措辞。

与 data/templates/（报告结构自学习）彻底分开维护：教训描述的是"失败模式"，不是"报告结构"。
存储格式（一条一文件）：

    ---
    title: 抖音小众品牌评论采集不足
    data_type: comment
    keywords: [小众品牌, 抖音]
    status: draft
    occurrences: 1
    ---
    <正文：通用化的应对建议>

不引入模板库那样的向量缓存文件——教训数量级远小于模板，每次现算 embedding 即可。
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from src.config.settings import PROJECT_ROOT, settings
from src.conductor.prompts import LESSON_DISTILL_SYSTEM
from src.conductor.utils import parse_json_obj
from src.llm import achat
from src.memory._frontmatter import FrontmatterError, parse_frontmatter
from src.memory._io import atomic_write, MtimeCache

logger = logging.getLogger(__name__)

LESSONS_DIR = PROJECT_ROOT / "data" / "lessons"

# 教训判重（创建/合并侧，find_similar_lesson）的 rerank instruct："两条教训是否描述同一类失败场景"
# ——教训→教训的同类性判断，与模板去重同构（判据更严格，配 template_dedup_rerank_threshold）。
_LESSON_RERANK_INSTRUCT = "判断这两条教训是否描述同一类容易采集失败的任务场景（措辞不同但触发条件相同即算同一类）"

# 教训召回（消费侧，find_active_lessons）的 rerank instruct："这条教训对当前任务是否有参考价值"
# ——任务→教训的适用性判断，与上面的判重 instruct 语义不同，不能共用（E1 修复：曾误用判重 instruct
# 导致消费侧比对两侧不同质，rerank 分数被去重阈值压低，库里明明有相关教训却召不回）。
_LESSON_RECALL_INSTRUCT = "判断这条历史教训是否对完成当前任务有参考价值（提醒需要规避的坑或应对方式）"

_lessons_lock = threading.Lock()
_lessons_cache = MtimeCache()


def _invalidate_and_rebuild_index() -> None:
    """写操作后：失效缓存并重建 INDEX.md（方案 C）。"""
    _lessons_cache.invalidate()
    _rebuild_lessons_index()


def _slugify(text: str) -> str:
    """生成文件名 slug：保留中英文/数字，空白转连字符，限长。"""
    text = (text or "lesson").strip()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^\w-]", "", text)
    return text[:40] or "lesson"


def _load_lessons_from_disk() -> List[Dict]:
    """从磁盘全量加载教训（glob + read + frontmatter 解析）。"""
    out: List[Dict] = []
    if not LESSONS_DIR.exists():
        return out
    for p in sorted(LESSONS_DIR.glob("*.md")):
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception:
            continue
        try:
            parsed = parse_frontmatter(raw)
        except FrontmatterError:
            logger.warning("教训 frontmatter 解析失败：%s", p.name)
            continue
        if parsed is None:
            continue
        meta, body = parsed
        if not body:
            continue
        kws = meta.get("keywords") or []
        if isinstance(kws, str):
            kws = [kws]
        out.append({
            "slug": p.stem,
            "title": str(meta.get("title") or p.stem),
            "data_type": str(meta.get("data_type") or "").strip().lower(),
            "keywords": [str(k).strip() for k in kws if str(k).strip()],
            "body": body,
            "status": str(meta.get("status") or "draft").strip().lower(),
            "occurrences": int(meta.get("occurrences") or 0),
            "helped_avoid": int(meta.get("helped_avoid") or 0),
            "created_at": str(meta.get("created_at") or ""),
        })
    return out


def load_lessons() -> List[Dict]:
    """加载 data/lessons/*.md，mtime 缓存加速；无 frontmatter 或解析失败的跳过。"""
    cached = _lessons_cache.get(LESSONS_DIR)
    if cached is not None:
        return cached
    result = _load_lessons_from_disk()
    _lessons_cache.set(LESSONS_DIR, result)
    return result


def _lesson_text(t: Dict) -> str:
    """教训的语义表示：标题 + 关键词。"""
    return (t.get("title") or "") + " " + " ".join(t.get("keywords") or [])


def _jaccard(a: List[str], b: List[str]) -> float:
    """两组关键词的 Jaccard 相似度（忽略大小写/空白）。"""
    sa = {k.strip().lower() for k in (a or []) if k and k.strip()}
    sb = {k.strip().lower() for k in (b or []) if k and k.strip()}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _match_keyword(data_type: Optional[str], keywords: List[str], statuses: Tuple[str, ...]) -> Optional[Dict]:
    """关键词 Jaccard 兜底：同 data_type（data_type 为空则不限）、关键词重叠度最高且达阈值者命中。"""
    from src.config.settings import settings

    dt = (data_type or "").lower()
    best: Optional[Dict] = None
    best_sim = 0.0
    for t in load_lessons():
        if t["status"] not in statuses or (dt and t["data_type"] != dt):
            continue
        sim = _jaccard(keywords, t["keywords"])
        if sim >= settings.template_dedup_threshold and sim > best_sim:
            best, best_sim = t, sim
    return best


def _semantic_match(
    data_type: Optional[str], keywords: List[str], intent: str, statuses: Tuple[str, ...],
) -> Tuple[bool, Optional[Dict]]:
    """语义召回单条最相似的教训。返回 (是否完成了确定性的语义判断, 命中或None)。

    第一个返回值为 False 表示"语义端点不可用/rerank 未配置/调用失败"——此时无法给出确定性判断，
    调用方 find_similar_lesson 据此退回关键词 Jaccard 兜底。消费侧召回改用 find_active_lessons
    （方案 C，多条+有效性排序，降级到关键词匹配而非直接放弃）不再走这个函数。
    第一个返回值为 True 时，第二个值就是最终结论（含"确实没有相关教训"的 None）。
    """
    from src.config.settings import settings
    from . import embeddings as emb

    if not settings.embedding_enabled:
        return False, None

    dt = (data_type or "").lower()
    cands = [t for t in load_lessons() if t["status"] in statuses and (not dt or t["data_type"] == dt)]
    if not cands:
        return True, None  # 无候选，无需调用 embedding 也能确定"没有"

    query_text = (intent or "") + " " + " ".join(keywords or [])
    got = emb.embed_texts_with_model([query_text])
    if not got or not got[1]:
        return False, None
    model, qvec = got[0], got[1][0]

    got2 = emb.embed_texts_with_model([_lesson_text(t) for t in cands])
    if not got2 or got2[0] != model or len(got2[1]) != len(cands):
        return False, None

    scored = sorted(
        ((t, emb.cosine(qvec, v)) for t, v in zip(cands, got2[1])),
        key=lambda x: x[1], reverse=True,
    )
    top = [t for t, sim in scored if sim >= settings.lesson_candidate_min_cosine][:3]
    if not top:
        return True, None

    if not emb.is_rerank_configured():
        return False, None  # 无 rerank 精判能力，交给调用方的降级策略

    rscores = emb.rerank_scores(query_text, [_lesson_text(t) for t in top], instruct=_LESSON_RERANK_INSTRUCT)
    if not rscores:
        return False, None

    best_i = max(range(len(rscores)), key=rscores.__getitem__)
    if rscores[best_i] >= settings.template_dedup_rerank_threshold:
        return True, top[best_i]
    return True, None


def find_similar_lesson(data_type: Optional[str], keywords: List[str], intent: str) -> Optional[Dict]:
    """创建/合并判重用（checker.py 的 record_failure 调用）：候选含 draft+active。
    语义不可用时退回关键词 Jaccard 判重，不致瘫。"""
    statuses: Tuple[str, ...] = ("draft", "active")
    ok, result = _semantic_match(data_type, keywords, intent, statuses)
    if ok:
        return result
    return _match_keyword(data_type, keywords, statuses)


def _effectiveness(t: Dict) -> float:
    """教训有效性评分：helped_avoid / occurrences（0~1，0 表示从未帮到）。"""
    occ = max(t.get("occurrences", 1), 1)
    return t.get("helped_avoid", 0) / occ


def find_active_lessons(data_type: Optional[str], keywords: List[str], intent: str, top_k: int = 3) -> Tuple[List[Dict], str]:
    """方案 C：返回多条匹配的 active 教训，按有效性(helped_avoid/occurrences)降序排列。
    语义不可用时退回关键词匹配（单条）；语义可用时用 _semantic_match_top_k 取候选再按有效性排序。
    返回 (教训列表, 降级路径：semantic/keyword/none)。"""
    from src.config.settings import settings
    from . import embeddings as emb

    dt = (data_type or "").lower()
    statuses: Tuple[str, ...] = ("active",)
    cands = [t for t in load_lessons() if t["status"] in statuses and (not dt or t["data_type"] == dt)]
    if not cands:
        return [], "none"

    if not settings.embedding_enabled:
        t = _match_keyword(data_type, keywords, statuses)
        return ([t] if t else [], "keyword") if t else ([], "none")

    # 语义召回：先取 rerank top-(k*2) 候选，再按有效性降序截断
    query_text = (intent or "") + " " + " ".join(keywords or [])
    got = emb.embed_texts_with_model([query_text])
    if not got or not got[1]:
        t = _match_keyword(data_type, keywords, statuses)
        return ([t] if t else [], "keyword") if t else ([], "none")
    model, qvec = got[0], got[1][0]

    got2 = emb.embed_texts_with_model([_lesson_text(t) for t in cands])
    if not got2 or got2[0] != model or len(got2[1]) != len(cands):
        t = _match_keyword(data_type, keywords, statuses)
        return ([t] if t else [], "keyword") if t else ([], "none")

    scored = sorted(
        ((t, emb.cosine(qvec, v)) for t, v in zip(cands, got2[1])),
        key=lambda x: x[1], reverse=True,
    )
    top = [t for t, sim in scored if sim >= settings.lesson_candidate_min_cosine][:top_k * 2]
    if not top:
        return [], "none"

    if emb.is_rerank_configured():
        # E1 修复：消费侧用"任务→教训是否有帮助"的独立 instruct + 独立阈值（更宽松），
        # 不能复用创建侧判重的 instruct/阈值（那是"教训→教训是否同类"，判据更严格）。
        rscores = emb.rerank_scores(query_text, [_lesson_text(t) for t in top], instruct=_LESSON_RECALL_INSTRUCT)
        if rscores:
            filtered = [t for i, t in enumerate(top) if rscores[i] >= settings.lesson_recall_rerank_threshold]
            filtered.sort(key=lambda t: _effectiveness(t), reverse=True)
            return filtered[:top_k], "semantic"

    # 无 rerank：余弦相似度排序，按有效性降序
    top.sort(key=lambda t: _effectiveness(t), reverse=True)
    return top[:top_k], "semantic"


async def distill_lesson(
    intent: str,
    data_type: str,
    keywords: List[str],
    failure_signal: str,
    *,
    existing: Optional[Dict] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[Dict]:
    """用 LLM 把一次失败蒸馏成教训，返回 {title, keywords, body}；调用失败或无正文返回 None。

    existing 传入命中的旧教训（含 title/body）时，提示词要求融合新旧信息产出更完整的一份，
    而不是简单拼接（合并语义与 B1 阶段 Curator merge 一致）。
    """
    user = (
        f"任务目标：{intent}\n数据类型：{data_type}\n失败现象：\n{(failure_signal or '')[:2000]}"
    )
    if existing:
        user += f"\n\n已有的旧教训：\n标题：{existing['title']}\n正文：{existing['body']}"
    try:
        raw = await achat(
            [
                {"role": "system", "content": LESSON_DISTILL_SYSTEM},
                {"role": "user", "content": user},
            ],
            provider=provider,
            model=model,
        )
    except Exception:
        logger.warning("教训蒸馏 LLM 调用失败", exc_info=True)
        return None
    data = parse_json_obj(raw)
    body = (data.get("body") or "").strip()
    if not body:
        return None
    kws = data.get("keywords") or []
    if isinstance(kws, str):
        kws = [kws]
    return {
        "title": (data.get("title") or (intent or "")[:20] or "失败教训").strip(),
        "keywords": [str(k).strip() for k in kws if str(k).strip()],
        "body": body,
    }


def _build_lesson_meta(title: str, data_type: str, keywords: List[str], status: str, occurrences: int,
                      *, helped_avoid: int = 0, created_at: str = "") -> str:
    """构造 frontmatter YAML 字符串（不含分隔符）。"""
    meta = {
        "title": title,
        "data_type": (data_type or "").lower(),
        "keywords": [k for k in (keywords or []) if k],
        "status": status,
        "occurrences": occurrences,
        "helped_avoid": helped_avoid,
    }
    if created_at:
        meta["created_at"] = created_at
    return yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()


def _read_lesson_by_slug(slug: str) -> Optional[Dict]:
    """锁内用：从磁盘重读单条教训，拿最新状态（不走缓存）。"""
    path = LESSONS_DIR / f"{slug}.md"
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        parsed = parse_frontmatter(raw)
    except FrontmatterError:
        return None
    if parsed is None:
        return None
    meta, body = parsed
    if not body:
        return None
    kws = meta.get("keywords") or []
    if isinstance(kws, str):
        kws = [kws]
    return {
        "slug": slug,
        "title": str(meta.get("title") or slug),
        "data_type": str(meta.get("data_type") or "").strip().lower(),
        "keywords": [str(k).strip() for k in kws if str(k).strip()],
        "body": body,
        "status": str(meta.get("status") or "draft").strip().lower(),
        "occurrences": int(meta.get("occurrences") or 0),
        "helped_avoid": int(meta.get("helped_avoid") or 0),
        "created_at": str(meta.get("created_at") or ""),
    }


async def record_failure(
    intent: str,
    data_type: str,
    keywords: List[str],
    failure_signal: str,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> None:
    """checker.py 判定"采集失败"后的唯一调用入口。

    先无 existing 蒸馏一次，得到这次失败自己的通用化描述（title/keywords/body）——
    这一步是修复"用原始任务 intent 去比对已有教训"的语义错配 bug 所必需的。

    LLM 调用全在锁外（避免长时间持锁阻塞事件循环）；锁内只做"重读最新状态 + 计算 + 原子写"。
    新建分支锁内重判发现 existing 时选择放弃本次（同类失败会重复发生，下次再合并）。
    """
    # ---- 锁外：LLM 蒸馏（不含 existing，拿到"这次失败"的通用化描述）----
    fresh = await distill_lesson(
        intent, data_type, keywords, failure_signal,
        existing=None, provider=provider, model=model,
    )
    if not fresh:
        logger.warning("教训蒸馏无有效正文，本次跳过")
        return

    existing_snap = find_similar_lesson(data_type, fresh["keywords"] or keywords, fresh["title"])

    # ---- 新建分支：锁内重判 + 原子写 ----
    if not existing_snap:
        with _lessons_lock:
            existing = find_similar_lesson(data_type, fresh["keywords"] or keywords, fresh["title"])
            if existing:
                # 并发期间他人已创建同类教训：锁内不能 await LLM merge，
                # 直接 occurrences+1、body 保持现有（下次失败会走合并分支融合 body）
                existing = _read_lesson_by_slug(existing["slug"])
                if not existing:
                    # 极端情况：被巡检删了，降级新建
                    LESSONS_DIR.mkdir(parents=True, exist_ok=True)
                    slug = _slugify(fresh["title"])
                    path = LESSONS_DIR / f"{slug}.md"
                    i = 2
                    while path.exists():
                        path = LESSONS_DIR / f"{slug}-{i}.md"
                        i += 1
                    meta = _build_lesson_meta(fresh["title"], data_type, fresh["keywords"] or keywords, "draft", 1, created_at=datetime.now().isoformat())
                    atomic_write(path, f"---\n{meta}\n---\n{fresh['body'].strip()}\n")
                    _invalidate_and_rebuild_index()
                    logger.info("教训已被删除，降级新建：%s", path.stem)
                    return
                path = LESSONS_DIR / f"{existing['slug']}.md"
                occurrences = existing["occurrences"] + 1
                status = existing["status"]
                meta = _build_lesson_meta(
                    existing["title"], data_type, existing["keywords"], status, occurrences,
                    helped_avoid=existing["helped_avoid"],
                )
                atomic_write(path, f"---\n{meta}\n---\n{existing['body'].strip()}\n")
                _invalidate_and_rebuild_index()
                logger.info("并发新建转为合并（不调LLM）：%s occurrences=%d", existing["slug"], occurrences)
                return
            LESSONS_DIR.mkdir(parents=True, exist_ok=True)
            slug = _slugify(fresh["title"])
            path = LESSONS_DIR / f"{slug}.md"
            i = 2
            while path.exists():
                path = LESSONS_DIR / f"{slug}-{i}.md"
                i += 1
            meta = _build_lesson_meta(fresh["title"], data_type, fresh["keywords"] or keywords, "draft", 1, created_at=datetime.now().isoformat())
            atomic_write(path, f"---\n{meta}\n---\n{fresh['body'].strip()}\n")
            _invalidate_and_rebuild_index()
            logger.info("已沉淀新教训（草稿）：%s", path.stem)
        return

    # ---- 锁外：LLM 合并蒸馏（用快照 existing_snap）----
    merged = await distill_lesson(
        intent, data_type, keywords, failure_signal,
        existing=existing_snap, provider=provider, model=model,
    )
    if not merged:
        logger.warning("教训合并蒸馏无有效正文，本次跳过")
        return

    # ---- 锁内：重读最新 existing + 计算 occurrences + 原子写 ----
    with _lessons_lock:
        existing = _read_lesson_by_slug(existing_snap["slug"])
        if not existing:
            # 被巡检删了，降级新建
            LESSONS_DIR.mkdir(parents=True, exist_ok=True)
            slug = _slugify(merged["title"] or fresh["title"])
            path = LESSONS_DIR / f"{slug}.md"
            i = 2
            while path.exists():
                path = LESSONS_DIR / f"{slug}-{i}.md"
                i += 1
            meta = _build_lesson_meta(
                merged["title"] or fresh["title"], data_type, merged["keywords"] or keywords,
                "draft", 1, created_at=datetime.now().isoformat(),
            )
            atomic_write(path, f"---\n{meta}\n---\n{merged['body'].strip()}\n")
            _invalidate_and_rebuild_index()
            logger.info("教训已被删除，降级新建：%s", path.stem)
            return

        path = LESSONS_DIR / f"{existing['slug']}.md"
        occurrences = existing["occurrences"] + 1
        status = existing["status"]  # B：不再自动转正，需 helped_avoid≥1 由 record_lesson_helped 控制
        meta = _build_lesson_meta(
            merged["title"] or existing["title"], data_type, merged["keywords"] or existing["keywords"],
            status, occurrences, helped_avoid=existing["helped_avoid"],
        )
        atomic_write(path, f"---\n{meta}\n---\n{merged['body'].strip()}\n")
        _invalidate_and_rebuild_index()
        logger.info("教训合并更新：%s（occurrences=%d, status=%s）", existing["slug"], occurrences, status)


def _log_lesson_recall(store, task_id: str, lessons: List[Dict], degrade_path: str) -> None:
    """方案 D/E3：无论命中与否都写一条埋点，未命中也记录 degrade_path 用于诊断
    "库中有内容但注入不出去"——区分 none（无候选，教训库本身空/无同类型）与
    semantic（语义召回执行过、候选被 rerank/阈值筛空，属于召回判据问题）。"""
    if store is None:
        return
    try:
        if lessons:
            store.memory_hit_log_add("lesson", lessons[0]["slug"], 0.0, degrade_path, task_id, hit=True)
        else:
            store.memory_hit_log_add("lesson", "", 0.0, degrade_path, task_id, hit=False)
    except Exception:
        logger.warning("教训召回埋点失败（不影响产出）", exc_info=True)


def lesson_for_analyze(spec, *, store=None, task_id: str = "") -> Tuple[str, Optional[str]]:
    """analyze 节点用：召回多条 active 教训，按有效性降序取 top-3 拼接提醒。
    未命中/语义不可用返回 ("", None)。返回 (提醒文本, 最优 slug 或 None)。
    方案 D/E3：传入 store 时无论命中与否都写一条埋点（供概览页聚合命中率）。"""
    lessons, degrade_path = find_active_lessons(spec.data_type.value, spec.keywords, spec.intent, top_k=3)
    _log_lesson_recall(store, task_id, lessons, degrade_path)
    if not lessons:
        return "", None
    parts = [f"\n\n# 历史教训提醒（{len(lessons)} 条）\n"]
    for t in lessons:
        parts.append(f"## {t['title']}\n{t['body']}\n")
    return "".join(parts), lessons[0]["slug"]


def lesson_for_planner(user_input: str, *, store=None, task_id: str = "") -> str:
    """planner 节点用：此时尚无 TaskSpec，只有原始用户输入，因此不按 data_type 过滤，直接
    用原始文本做语义比对。召回多条 active 教训，按有效性降序取 top-3 拼接提醒；
    未命中/语义不可用返回空串。方案 D/E3：传入 store 时无论命中与否都写一条埋点。"""
    lessons, degrade_path = find_active_lessons(None, [], user_input, top_k=3)
    _log_lesson_recall(store, task_id, lessons, degrade_path)
    if not lessons:
        return ""
    parts = [f"\n\n# 历史教训提醒（{len(lessons)} 条）\n"]
    for t in lessons:
        parts.append(f"## {t['title']}\n{t['body']}\n")
    return "".join(parts)


def _rebuild_lessons_index() -> None:
    """方案 C：重建 data/lessons/INDEX.md（一行一条教训概要，供管理员快速浏览）。"""
    lessons = load_lessons()
    lines = ["# 教训库索引\n\n"]
    if not lessons:
        lines.append("（暂无教训）\n")
    else:
        for t in lessons:
            lines.append(f"- [{t['slug']}] {t['title']} | {t['data_type'] or '通用'} | "
                         f"{t['status']} | 命中{t['occurrences']}次 | 有效{t['helped_avoid']}次\n")
    atomic_write(LESSONS_DIR / "INDEX.md", "".join(lines))


def delete_lesson(slug: str) -> bool:
    """删除一条已学教训：移除 data/lessons/<slug>.md。供前端教训库管理使用。
    文件不存在返回 False。加锁保护，同步失效缓存+重建索引。"""
    path = LESSONS_DIR / f"{slug}.md"
    with _lessons_lock:
        if not path.is_file():
            return False
        try:
            path.unlink()
        except Exception:
            logger.warning("删除教训失败：%s", slug, exc_info=True)
            return False
        _invalidate_and_rebuild_index()
        logger.info("已删除教训：%s", slug)
        return True


def record_lesson_helped(slug: str) -> bool:
    """标记一条教训在本轮任务中帮到了（避免同类型失败）。

    当某次任务命中 active 教训且 Checker 判定通过时调用此函数：
    使教训的 helped_avoid +1。如果满足转正条件（occurrences≥2 且 helped_avoid≥1）
    自动转正 active；如果满足退役条件（active 且 occurrences≥10 且 helped_avoid==0）
    自动退役 retired。文件不存在返回 False。
    """
    path = LESSONS_DIR / f"{slug}.md"
    with _lessons_lock:
        if not path.is_file():
            return False
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = parse_frontmatter(raw)
        except (OSError, FrontmatterError):
            parsed = None
        if parsed is None:
            return False
        meta, old_body = parsed
        occurrences = int(meta.get("occurrences") or 0)
        helped_before = int(meta.get("helped_avoid") or 0)
        status = str(meta.get("status") or "draft").strip().lower()
        # 退役：active 教训多次失败（occurrences≥10）却从未帮到过（helped_avoid==0）
        if status == "active" and occurrences >= 10 and helped_before == 0:
            status = "retired"
        # 转正：draft 教训满足"多次失败+验证有效"双条件
        if status == "draft" and occurrences >= 2 and helped_before + 1 >= 1:
            status = "active"
        meta["status"] = status
        meta["helped_avoid"] = helped_before + 1
        front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
        atomic_write(path, f"---\n{front}\n---\n{old_body}\n")
        _invalidate_and_rebuild_index()
        logger.info("教训 helped_avoid +1：%s（helped_avoid=%d, status=%s）", slug, meta["helped_avoid"], status)
        return True


async def merge_lesson_pair(
    a: Dict, b: Dict, *, provider: Optional[str] = None, model: Optional[str] = None,
) -> Optional[Dict]:
    """定时巡检专用：给定两条已确认描述同一类失败场景的教训，直接融合成一份正文。
    返回 {title, keywords, body} 或 None（LLM 调用失败/无正文）。"""
    from src.conductor.prompts import LESSON_PAIR_MERGE_SYSTEM

    user = (
        f"教训A：\n标题：{a['title']}\n关键词：{', '.join(a['keywords'])}\n正文：{a['body']}\n\n"
        f"教训B：\n标题：{b['title']}\n关键词：{', '.join(b['keywords'])}\n正文：{b['body']}\n\n"
        "以上两条教训已确认描述同一类失败场景，请融合成一份更完整的教训。"
    )
    try:
        raw = await achat(
            [
                {"role": "system", "content": LESSON_PAIR_MERGE_SYSTEM},
                {"role": "user", "content": user},
            ],
            provider=provider,
            model=model,
        )
    except Exception:
        logger.warning("巡检教训融合 LLM 调用失败", exc_info=True)
        return None
    data = parse_json_obj(raw)
    body = (data.get("body") or "").strip()
    if not body:
        return None
    kws = data.get("keywords") or list({*a["keywords"], *b["keywords"]})
    if isinstance(kws, str):
        kws = [kws]
    return {
        "title": (data.get("title") or a["title"]).strip(),
        "keywords": [str(k).strip() for k in kws if str(k).strip()],
        "body": body,
    }


def find_patrol_duplicate_lesson(entry: Dict) -> Optional[tuple[Dict, float]]:
    """定时巡检专用：在同 data_type 的其余教训里找与 entry 语义重复的一条（排除自身）。
    返回 (匹配条目, rerank 相似度分数) 或 None。
    embedding 不可用/rerank 未配置时返回 None（同模板库巡检哲学：宁可不查也不要弱兜底误判）。"""
    from src.config.settings import settings
    from . import embeddings as emb

    if not settings.embedding_enabled:
        return None
    dt = (entry["data_type"] or "").lower()
    cands = [t for t in load_lessons() if t["slug"] != entry["slug"] and (not dt or t["data_type"] == dt)]
    if not cands:
        return None
    if not emb.is_rerank_configured():
        return None
    query_text = (entry["title"] or "") + " " + " ".join(entry["keywords"] or [])
    got = emb.embed_texts_with_model([query_text])
    if not got or not got[1]:
        return None
    model, qvec = got[0], got[1][0]
    got2 = emb.embed_texts_with_model([_lesson_text(t) for t in cands])
    if not got2 or got2[0] != model or len(got2[1]) != len(cands):
        return None
    scored = sorted(
        ((t, emb.cosine(qvec, v)) for t, v in zip(cands, got2[1])),
        key=lambda x: x[1], reverse=True,
    )
    top = [t for t, sim in scored if sim >= settings.lesson_candidate_min_cosine][:3]
    if not top:
        return None
    rscores = emb.rerank_scores(query_text, [_lesson_text(t) for t in top], instruct=_LESSON_RERANK_INSTRUCT)
    if not rscores:
        return None
    best_i = max(range(len(rscores)), key=rscores.__getitem__)
    if rscores[best_i] >= settings.template_dedup_rerank_threshold:
        return top[best_i], rscores[best_i]
    return None


def apply_patrol_merge_lesson(slug: str, merged: Dict) -> bool:
    """定时巡检专用：把融合结果写回目标教训文件，status/occurrences 保持不变。
    文件不存在/解析失败返回 False。加锁保护 read-modify-write。"""
    path = LESSONS_DIR / f"{slug}.md"
    with _lessons_lock:
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = parse_frontmatter(raw)
        except (OSError, FrontmatterError):
            parsed = None
        if parsed is None:
            logger.warning("巡检合并的目标教训读取/解析失败，跳过：%s", slug)
            return False
        meta, _old_body = parsed
        meta["title"] = merged["title"] or meta.get("title")
        meta["keywords"] = merged["keywords"] or meta.get("keywords")
        front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
        atomic_write(path, f"---\n{front}\n---\n{merged['body'].strip()}\n")
        _invalidate_and_rebuild_index()
        logger.info("巡检去重合并：%s", slug)
        return True
