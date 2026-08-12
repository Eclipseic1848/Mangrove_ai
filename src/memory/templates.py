"""
学到的分析报告模板（阶段1：自演进技能的最小闭环）。

当任务走了通用兜底（没命中内置领域模板）时，可由用户确认后把"本次报告结构"沉淀为模板，
存到 data/templates/<slug>.md；下次同类任务自动命中复用，使 Agent 越用越强。

文件读写 + PyYAML 解析 frontmatter，mtime 缓存加速读，锁保护并发写。文件格式：

    ---
    title: 政策解读报告
    data_type: article
    keywords: [政策, 解读, 通知]
    ---
    <一段中文 system prompt：描述这类任务应输出的报告结构>
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

from src.config.settings import PROJECT_ROOT
from src.conductor.prompts import TEMPLATE_CURATOR_SYSTEM, TEMPLATE_DISTILL_SYSTEM
from src.conductor.task_spec import TaskSpec
from src.conductor.utils import parse_json_obj
from src.llm import achat
from src.memory._frontmatter import FrontmatterError, parse_frontmatter
from src.memory._io import atomic_write, MtimeCache

logger = logging.getLogger(__name__)

# 独立目录：与手写技能 skills/*.md 分开存放，两套范式（自学习/手写）不再共居一个文件夹
TEMPLATES_DIR = PROJECT_ROOT / "data" / "templates"

_templates_lock = threading.RLock()  # 可重入：_save_vectors 内部持锁，调用方也可持锁
_templates_cache = MtimeCache()
_vectors_cache = MtimeCache()


def _rebuild_templates_index() -> None:
    """方案 C：重建 data/templates/INDEX.md（一行一条模板概要）。"""
    temps = load_templates()
    lines = ["# 模板库索引\n\n"]
    if not temps:
        lines.append("（暂无模板）\n")
    else:
        for t in temps:
            lines.append(f"- [{t['slug']}] {t['title']} | {t['data_type'] or '通用'} | "
                         f"{t['status']} | 使用{t['uses']}次 | 质量{t['quality_avg']}\n")
    atomic_write(TEMPLATES_DIR / "INDEX.md", "".join(lines))


def _invalidate_and_rebuild_templates() -> None:
    """写操作后：失效缓存并重建 INDEX.md（方案 C）。"""
    _templates_cache.invalidate()
    _rebuild_templates_index()


def _slugify(text: str) -> str:
    """生成文件名 slug：保留中英文/数字，空白转连字符，限长。"""
    text = (text or "template").strip()
    text = re.sub(r"\s+", "-", text)
    # \w 在 str 默认 unicode 模式下已涵盖中文/字母/数字/下划线，再保留连字符即可
    text = re.sub(r"[^\w-]", "", text)
    return text[:40] or "template"


def _load_templates_from_disk() -> List[Dict]:
    """从磁盘全量加载模板（glob + read + frontmatter 解析）。"""
    out: List[Dict] = []
    if not TEMPLATES_DIR.exists():
        return out
    for p in sorted(TEMPLATES_DIR.glob("*.md")):
        try:
            raw = p.read_text(encoding="utf-8")
        except Exception:
            continue
        try:
            parsed = parse_frontmatter(raw)
        except FrontmatterError:
            logger.warning("模板 frontmatter 解析失败：%s", p.name)
            continue
        if parsed is None:
            continue  # 无 frontmatter（如 README.md）跳过
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
            "uses": int(meta.get("uses") or 0),
            "quality_avg": float(meta.get("quality_avg") or 0),
            "created_at": str(meta.get("created_at") or ""),
        })
    return out


def load_templates() -> List[Dict]:
    """加载 data/templates/*.md，mtime 缓存加速；无 frontmatter 或解析失败的跳过。"""
    cached = _templates_cache.get(TEMPLATES_DIR)
    if cached is not None:
        return cached
    result = _load_templates_from_disk()
    _templates_cache.set(TEMPLATES_DIR, result)
    return result


def _candidates(spec: TaskSpec) -> List[Dict]:
    """召回候选：非淘汰 且 data_type 一致（模板未标 data_type 时不限）。"""
    dt = spec.data_type.value
    return [
        t for t in load_templates()
        if t.get("status") != "retired" and (not t["data_type"] or t["data_type"] == dt)
    ]


def _match_keyword(spec: TaskSpec) -> Optional[Dict]:
    """关键词匹配：至少一个关键词出现在 intent/keywords，取命中数最多者；都不命中返回 None。"""
    haystack = ((spec.intent or "") + " " + " ".join(spec.keywords or [])).lower()
    best: Optional[Dict] = None
    best_score = 0
    for t in _candidates(spec):
        score = sum(1 for k in t["keywords"] if k.lower() in haystack)
        if score > best_score:
            best, best_score = t, score
    return best if best_score > 0 else None


def match_template(spec: TaskSpec) -> Optional[Dict]:
    """匹配已学模板：启用 embedding 时先走语义召回，命中即用；语义不可用或无命中再回退关键词匹配。
    已淘汰(retired)不参与；草稿(draft)仍可被召回（以积累统计、达标转正）。"""
    from src.config.settings import settings

    if settings.embedding_enabled:
        ok, result = _match_semantic(spec)
        if ok and result is not None:
            return result
        # 语义没命中或端点不可用 → 关键词兜底（不致瘫）
    return _match_keyword(spec)


# ---- 语义召回（embedding，方案A）----

def _vectors_path() -> Path:
    """模板向量缓存文件（随 data/templates/ 持久卷一起走）。"""
    return TEMPLATES_DIR / "_vectors.json"


def _template_text(t: Dict) -> str:
    """模板的语义表示：标题 + 关键词（代表"这类任务"）。"""
    return (t.get("title") or "") + " " + " ".join(t.get("keywords") or [])


def _text_hash(model: str, text: str) -> str:
    return hashlib.md5(f"{model}\n{text}".encode("utf-8")).hexdigest()


def _load_vectors() -> Dict[str, Dict]:
    """加载向量缓存，按 _vectors.json 的 mtime 缓存。"""
    p = _vectors_path()
    cached = _vectors_cache.get(p)
    if cached is not None:
        return cached
    if not p.exists():
        return {}
    try:
        result = json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        result = {}
    _vectors_cache.set(p, result)
    return result


def _save_vectors(cache: Dict[str, Dict]) -> None:
    """写入向量缓存，加锁 + 原子写。"""
    try:
        with _templates_lock:
            p = _vectors_path()
            atomic_write(p, json.dumps(cache, ensure_ascii=False))
            _vectors_cache.invalidate()
    except Exception:
        logger.warning("写入模板向量缓存失败")


def _match_semantic(spec: TaskSpec) -> Tuple[bool, Optional[Dict]]:
    """语义召回（召回 + 可选精排）。返回 (embedding是否可用, 命中模板或None)。

    embedding 后端级联（本地优先、云端备选）由 embeddings 模块处理；实际用哪个模型
    以本轮返回为准，缓存按「模型+文本」哈希隔离，后端切换时自动重算不混用。
    配置了 reranker 时，余弦召回 top 候选后交由 reranker 精排裁决命中。
    embedding 全部不可用时返回 (False, None) 以便调用方回退关键词。"""
    from . import embeddings as emb

    cands = _candidates(spec)
    if not cands:
        return True, None  # 没有候选，无需调 embedding

    query_text = ((spec.intent or "") + " " + " ".join(spec.keywords or [])).strip()
    # 先 embedding 查询——由返回确定本轮实际使用的模型（可能已切到备选后端）
    got = emb.embed_texts_with_model([query_text])
    if not got or not got[1]:
        return False, None  # 端点不可用 → 回退关键词
    model, qvec = got[0], got[1][0]

    cache = _load_vectors()
    # 找出需要（重新）embedding 的模板（无缓存 / 模型或文本变更）
    need: List[Tuple[str, str, str]] = []  # (slug, text, hash)
    for t in cands:
        txt = _template_text(t)
        h = _text_hash(model, txt)
        c = cache.get(t["slug"])
        if not (c and c.get("hash") == h and c.get("vector")):
            need.append((t["slug"], txt, h))
    if need:
        got2 = emb.embed_texts_with_model([n[1] for n in need])
        if not got2 or got2[0] != model or len(got2[1]) != len(need):
            return False, None  # 两次调用中途换了后端（向量空间不一致），本轮放弃语义
        for (slug, _txt, h), v in zip(need, got2[1]):
            cache[slug] = {"model": model, "hash": h, "vector": v}
        _save_vectors(cache)

    from src.config.settings import settings
    # 余弦召回：全部候选按相似度降序
    scored: List[Tuple[Dict, float]] = []
    for t in cands:
        c = cache.get(t["slug"]) or {}
        if c.get("hash") != _text_hash(model, _template_text(t)):
            continue  # 只用本轮模型的向量，避免跨模型比较
        sim = emb.cosine(qvec, c.get("vector") or [])
        if sim > 0:
            scored.append((t, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    if not scored:
        return True, None

    # 精排：reranker 对 top 候选裁决（分数≥阈值才命中，压误召回）；失败退回余弦
    if emb.is_rerank_configured():
        top = scored[:5]
        rscores = emb.rerank_scores(query_text, [_template_text(t) for t, _ in top])
        if rscores:
            best_i = max(range(len(rscores)), key=rscores.__getitem__)
            if rscores[best_i] >= settings.rerank_match_threshold:
                logger.info("语义召回命中模板 %s（rerank=%.3f, cos=%.3f）",
                            top[best_i][0]["slug"], rscores[best_i], top[best_i][1])
                return True, top[best_i][0]
            return True, None  # 精排认定无相关模板

    best, best_sim = scored[0]
    if best_sim >= settings.embedding_match_threshold:
        logger.info("语义召回命中模板 %s（cos=%.3f）", best["slug"], best_sim)
        return True, best
    return True, None


async def distill_template(
    intent: str,
    data_type: str,
    analysis: str,
    *,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[Dict]:
    """用 LLM 把一次报告蒸馏成可复用模板，返回 {title, keywords, body}；失败或无正文返回 None。

    前端「沉淀为模板」按钮与后端 Checker 自动沉淀共用此函数。
    """
    raw = await achat(
        [
            {"role": "system", "content": TEMPLATE_DISTILL_SYSTEM},
            {"role": "user", "content": (
                f"任务目标：{intent}\n数据类型：{data_type}\n\n已生成的报告：\n{(analysis or '')[:4000]}"
            )},
        ],
        provider=provider,
        model=model,
    )
    data = parse_json_obj(raw)
    body = (data.get("body") or "").strip()
    if not body:
        return None
    kws = data.get("keywords") or []
    if isinstance(kws, str):
        kws = [kws]
    return {
        "title": (data.get("title") or (intent or "")[:20] or "自定义模板").strip(),
        "keywords": [str(k).strip() for k in kws if str(k).strip()],
        "body": body,
    }


def _jaccard(a: List[str], b: List[str]) -> float:
    """两组关键词的 Jaccard 相似度（忽略大小写/空白）。"""
    sa = {k.strip().lower() for k in (a or []) if k and k.strip()}
    sb = {k.strip().lower() for k in (b or []) if k and k.strip()}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def find_duplicate(data_type: str, keywords: List[str]) -> Optional[Dict]:
    """查找同 data_type、关键词高度重叠（Jaccard≥阈值）的非淘汰模板，用于去重。命中返回该模板。"""
    from src.config.settings import settings

    dt = (data_type or "").lower()
    best: Optional[Dict] = None
    best_sim = 0.0
    for t in load_templates():
        if t.get("status") == "retired" or t["data_type"] != dt:
            continue
        sim = _jaccard(keywords, t["keywords"])
        if sim >= settings.template_dedup_threshold and sim > best_sim:
            best, best_sim = t, sim
    return best


# 去重场景的 rerank instruct：与召回场景（"是否适用于该任务"）不同，
# 去重要求判断"是否描述同一类任务"，判据更严格，专用独立阈值 template_dedup_rerank_threshold。
_DEDUP_RERANK_INSTRUCT = "判断这两段模板描述是否属于同一类分析任务（措辞不同但结构和适用场景相同即算同一类）"


def _semantic_candidates(
    data_type: str, keywords: List[str], title: str, top_k: int, min_cosine: float = 0.0,
) -> Optional[List[Dict]]:
    """按语义余弦相似度取 top_k 候选（≥min_cosine），供去重/Curator 共用。
    返回 None 表示 embedding 不可用（调用方应退回更简单机制）；返回列表（可能为空）表示 embedding 可用。
    """
    from src.config.settings import settings
    from . import embeddings as emb

    if not settings.embedding_enabled:
        return None

    dt = (data_type or "").lower()
    cands = [t for t in load_templates() if t.get("status") != "retired" and t["data_type"] == dt]
    if not cands:
        return []

    query_text = (title or "") + " " + " ".join(keywords or [])
    got = emb.embed_texts_with_model([query_text])
    if not got or not got[1]:
        return None
    model, qvec = got[0], got[1][0]

    cand_texts = [_template_text(t) for t in cands]
    got2 = emb.embed_texts_with_model(cand_texts)
    if not got2 or got2[0] != model or len(got2[1]) != len(cands):
        return None

    scored = sorted(
        ((t, emb.cosine(qvec, v)) for t, v in zip(cands, got2[1])),
        key=lambda x: x[1], reverse=True,
    )
    return [t for t, sim in scored if sim >= min_cosine][:top_k]


def find_duplicate_semantic(data_type: str, keywords: List[str], title: str) -> Optional[Dict]:
    """语义去重：用 embedding 余弦 + rerank 精判"是否同一类模板"，
    比 find_duplicate() 的关键词 Jaccard 更能识别"措辞不同、结构相同"的近重复。
    embedding 关闭 / 端点不可用 / rerank 未配置时，退回 find_duplicate() 的关键词 Jaccard（不致瘫）。
    """
    from src.config.settings import settings
    from . import embeddings as emb

    top = _semantic_candidates(data_type, keywords, title, top_k=5)
    if top is None:
        return find_duplicate(data_type, keywords)
    if not top:
        return None

    if not emb.is_rerank_configured():
        return find_duplicate(data_type, keywords)

    query_text = (title or "") + " " + " ".join(keywords or [])
    rscores = emb.rerank_scores(query_text, [_template_text(t) for t in top], instruct=_DEDUP_RERANK_INSTRUCT)
    if not rscores:
        return find_duplicate(data_type, keywords)

    best_i = max(range(len(rscores)), key=rscores.__getitem__)
    if rscores[best_i] >= settings.template_dedup_rerank_threshold:
        return top[best_i]
    return None


_CURATOR_TOP_K = 3


def _fallback_decision(data_type: str, keywords: List[str], title: str) -> Dict:
    """Curator 候选召回不可用 / LLM 调用或解析失败时的降级：退回 A 阶段的语义/关键词二元去重。
    命中即视为"复用"（不改动目标模板任何内容），未命中则新建。"""
    dup = find_duplicate_semantic(data_type, keywords, title)
    if dup:
        return {"decision": "reuse", "slug": dup["slug"]}
    return {"decision": "new"}


async def curate_template(
    title: str, data_type: str, keywords: List[str], body: str,
    *, provider: Optional[str] = None, model: Optional[str] = None,
) -> Dict:
    """Curator 裁决：新建(new) / 合并进已有模板(merge) / 丢弃(discard) / 复用不改动(reuse，仅降级路径产出)。

    候选为空（新库/新 data_type，或全部候选低于余弦下限）直接判 new，不调用 LLM。
    候选召回不可用、或 LLM 调用/输出解析失败，均退回 _fallback_decision（A 阶段二元逻辑），不致瘫。
    """
    from src.config.settings import settings

    candidates = _semantic_candidates(
        data_type, keywords, title,
        top_k=_CURATOR_TOP_K,
        min_cosine=settings.template_curator_candidate_min_cosine,
    )
    if candidates is None:
        return _fallback_decision(data_type, keywords, title)
    if not candidates:
        return {"decision": "new"}

    cand_desc = "\n\n".join(
        f"候选{i + 1}（slug={c['slug']}）：\n标题：{c['title']}\n关键词：{', '.join(c['keywords'])}\n"
        f"正文：{c['body']}\n（已使用{c['uses']}次，平均质量分{c['quality_avg']}，状态{c['status']}）"
        for i, c in enumerate(candidates)
    )
    user = (
        f"新内容：\n标题：{title}\n关键词：{', '.join(keywords or [])}\n正文：{body}\n\n"
        f"库内候选：\n{cand_desc}"
    )
    try:
        raw = await achat(
            [
                {"role": "system", "content": TEMPLATE_CURATOR_SYSTEM},
                {"role": "user", "content": user},
            ],
            provider=provider,
            model=model,
        )
    except Exception:
        logger.warning("Curator 调用失败，退回二元去重逻辑", exc_info=True)
        return _fallback_decision(data_type, keywords, title)

    data = parse_json_obj(raw)
    decision = str(data.get("decision") or "").strip().lower()
    if decision == "new":
        return {"decision": "new"}
    if decision == "discard":
        return {"decision": "discard"}
    if decision == "merge":
        slug = str(data.get("slug") or "").strip()
        new_body = str(data.get("body") or "").strip()
        valid_slugs = {c["slug"] for c in candidates}
        if slug in valid_slugs and new_body:
            kws = data.get("keywords") or []
            if isinstance(kws, str):
                kws = [kws]
            return {
                "decision": "merge",
                "slug": slug,
                "title": str(data.get("title") or "").strip(),
                "keywords": [str(k).strip() for k in kws if str(k).strip()],
                "body": new_body,
            }

    logger.warning("Curator 输出解析失败或字段不完整，退回二元去重逻辑：%r", data)
    return _fallback_decision(data_type, keywords, title)


async def save_template(title: str, data_type: str, keywords: List[str], body: str) -> Optional[str]:
    """经 Curator 裁决后保存一个学到的模板，返回其 slug；Curator 判定"丢弃"时返回 None。

    Curator 裁决（LLM）在锁外；merge/new 分支在锁内重读+原子写，保护 read-modify-write。
    合并时 updates 统计（uses/quality_avg/status）保持不变，只更新 title/keywords/body。
    """
    decision = await curate_template(title, data_type, keywords, body)  # 锁外 LLM
    kind = decision.get("decision")

    if kind == "discard":
        logger.info("Curator 判定新内容对现有模板库无增量信息，不沉淀")
        return None

    if kind == "reuse":
        slug = decision["slug"]
        logger.info("降级逻辑判定已存在近重复模板，复用不新建：%s", slug)
        return slug

    if kind == "merge":
        slug = decision["slug"]
        with _templates_lock:
            path = TEMPLATES_DIR / f"{slug}.md"
            try:
                raw = path.read_text(encoding="utf-8")
                parsed = parse_frontmatter(raw)
            except (OSError, FrontmatterError):
                parsed = None
            if parsed is None:
                logger.warning("Curator 判定合并的目标模板读取/解析失败，跳过本次沉淀：%s", slug)
                return None
            meta, _old_body = parsed
            meta["title"] = decision["title"] or meta.get("title")
            meta["keywords"] = decision["keywords"] or meta.get("keywords")
            front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
            atomic_write(path, f"---\n{front}\n---\n{decision['body'].strip()}\n")
            cache = _load_vectors()
            if slug in cache:
                cache.pop(slug, None)
                _save_vectors(cache)
            _invalidate_and_rebuild_templates()
            logger.info("Curator 裁决合并，已更新模板正文：%s", slug)
            return slug

    # kind == "new"
    with _templates_lock:
        TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
        slug = _slugify(title)
        path = TEMPLATES_DIR / f"{slug}.md"
        i = 2
        while path.exists():
            path = TEMPLATES_DIR / f"{slug}-{i}.md"
            i += 1
        meta = {
            "title": title,
            "data_type": (data_type or "").lower(),
            "keywords": [k for k in (keywords or []) if k],
            "status": "draft",
            "uses": 0,
            "quality_avg": 0,
            "created_at": datetime.now().isoformat(),
        }
        front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
        atomic_write(path, f"---\n{front}\n---\n{body.strip()}\n")
        _invalidate_and_rebuild_templates()
        logger.info("已沉淀分析模板（草稿）：%s", path.name)
        return path.stem


def delete_template(slug: str) -> bool:
    """删除一个已学模板：移除 data/templates/<slug>.md，并清理其向量缓存条目。
    供前端模板库管理使用。加锁保护。文件不存在返回 False。"""
    path = TEMPLATES_DIR / f"{slug}.md"
    with _templates_lock:
        if not path.is_file():
            return False
        try:
            path.unlink()
        except Exception:
            logger.warning("删除模板失败：%s", slug, exc_info=True)
            return False
        cache = _load_vectors()
        if slug in cache:
            cache.pop(slug, None)
            _save_vectors(cache)
        _invalidate_and_rebuild_templates()
        logger.info("已删除模板：%s", slug)
        return True


def record_template_use(slug: str, quality_score: Optional[int]) -> Optional[str]:
    """回写一次模板使用统计：uses+1、更新 quality_avg；据此质量门转正(draft→active)或淘汰(retired)。
    返回新的 status（用于日志/提示），模板不存在则返回 None。加锁保护 read-modify-write。"""
    from src.config.settings import settings

    path = TEMPLATES_DIR / f"{slug}.md"
    with _templates_lock:
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception:
            logger.warning("读取模板失败，跳过统计回写：%s", slug)
            return None
        try:
            parsed = parse_frontmatter(raw)
        except FrontmatterError:
            logger.warning("模板 frontmatter 解析失败，跳过统计回写：%s", slug)
            return None
        if parsed is None:
            return None
        meta, body = parsed

        uses = int(meta.get("uses") or 0) + 1
        old_avg = float(meta.get("quality_avg") or 0)
        status = str(meta.get("status") or "draft").strip().lower()
        if quality_score is not None:
            avg = (old_avg * (uses - 1) + float(quality_score)) / uses
        else:
            avg = old_avg

        if uses >= settings.template_promote_uses:
            if avg < settings.template_retire_quality:
                status = "retired"
            elif status == "draft" and avg >= settings.template_promote_quality:
                status = "active"
            elif status == "draft" and uses >= settings.template_dead_zone_uses:
                status = "retired"

        meta["uses"] = uses
        meta["quality_avg"] = round(avg, 1)
        meta["status"] = status
        front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
        try:
            atomic_write(path, f"---\n{front}\n---\n{body}\n")
            _invalidate_and_rebuild_templates()
        except Exception:
            logger.warning("写回模板统计失败：%s", slug)
            return None
        logger.info("模板使用统计 %s：uses=%d avg=%.1f status=%s", slug, uses, avg, status)
        return status


async def merge_template_pair(
    a: Dict, b: Dict, *, provider: Optional[str] = None, model: Optional[str] = None,
) -> Optional[Dict]:
    """定时巡检专用：给定两条已确认属于同一类的模板，直接融合成一份正文，不再判断
    "要不要合并"（巡检已用确定性的 rerank 阈值确认过是重复）。
    返回 {title, keywords, body} 或 None（LLM 调用失败/无正文）。"""
    from src.conductor.prompts import TEMPLATE_PAIR_MERGE_SYSTEM

    user = (
        f"模板A：\n标题：{a['title']}\n关键词：{', '.join(a['keywords'])}\n正文：{a['body']}\n\n"
        f"模板B：\n标题：{b['title']}\n关键词：{', '.join(b['keywords'])}\n正文：{b['body']}\n\n"
        "以上两条模板已确认属于同一类分析任务，请融合成一份完整重写的正文。"
    )
    try:
        raw = await achat(
            [
                {"role": "system", "content": TEMPLATE_PAIR_MERGE_SYSTEM},
                {"role": "user", "content": user},
            ],
            provider=provider,
            model=model,
        )
    except Exception:
        logger.warning("巡检模板融合 LLM 调用失败", exc_info=True)
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


def find_patrol_duplicate(entry: Dict) -> Optional[tuple[Dict, float]]:
    """定时巡检专用：在同 data_type 的其余模板里找与 entry 语义重复的一条（排除自身）。
    返回 (匹配条目, rerank 相似度分数) 或 None。
    embedding 不可用/rerank 未配置时返回 None——巡检是低频后台任务，宁可这轮不查也不要用
    较弱的兜底误判合并，不像 find_duplicate_semantic 那样退回关键词 Jaccard。"""
    from src.config.settings import settings
    from . import embeddings as emb

    if not settings.embedding_enabled:
        return None
    top = _semantic_candidates(entry["data_type"], entry["keywords"], entry["title"], top_k=6)
    if not top:
        return None
    top = [t for t in top if t["slug"] != entry["slug"]]
    if not top:
        return None
    if not emb.is_rerank_configured():
        return None
    query_text = (entry["title"] or "") + " " + " ".join(entry["keywords"] or [])
    rscores = emb.rerank_scores(query_text, [_template_text(t) for t in top], instruct=_DEDUP_RERANK_INSTRUCT)
    if not rscores:
        return None
    best_i = max(range(len(rscores)), key=rscores.__getitem__)
    if rscores[best_i] >= settings.template_dedup_rerank_threshold:
        return top[best_i], rscores[best_i]
    return None


def apply_patrol_merge(slug: str, merged: Dict) -> bool:
    """定时巡检专用：把融合结果写回目标模板文件，uses/quality_avg/status 保持不变
    （与 save_template 的 Curator merge 分支同样的"合并不清零历史统计"原则）。
    文件不存在/解析失败返回 False。加锁保护 read-modify-write。"""
    path = TEMPLATES_DIR / f"{slug}.md"
    with _templates_lock:
        try:
            raw = path.read_text(encoding="utf-8")
            parsed = parse_frontmatter(raw)
        except (OSError, FrontmatterError):
            parsed = None
        if parsed is None:
            logger.warning("巡检合并的目标模板读取/解析失败，跳过：%s", slug)
            return False
        meta, _old_body = parsed
        meta["title"] = merged["title"] or meta.get("title")
        meta["keywords"] = merged["keywords"] or meta.get("keywords")
        front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
        atomic_write(path, f"---\n{front}\n---\n{merged['body'].strip()}\n")
        cache = _load_vectors()
        if slug in cache:
            cache.pop(slug, None)
            _save_vectors(cache)
        _invalidate_and_rebuild_templates()
        logger.info("巡检去重合并：%s", slug)
        return True
