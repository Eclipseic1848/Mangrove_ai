"""
文本向量（embedding）+ 重排序（rerank）—— 方案 A：HTTP 调 OpenAI 兼容端点。

用于模板的语义召回（见 templates.match_template）。设计取向：可移植、零本地依赖。
- 不引入本地 embedding 库（torch/sentence-transformers），只用 httpx 调远端；
- 后端级联：EMBEDDING_BASE_URL（如本地 vLLM Qwen3-Embedding）优先，整批失败
  自动切 qwen（DashScope text-embedding-v4）备选——同一批文本只会用同一个模型，
  不会出现向量维度/空间混用；
- rerank 走 vLLM 的 Cohere 兼容 /rerank（RERANK_BASE_URL 留空即禁用）；
- 端点全部不可用/出错时返回 None，由调用方回退到关键词匹配（不致瘫）；
- 模板量小（数十条），相似度用 Python 余弦即可，不引入向量库；
- 局域网端点（192.168.* 等）绕过系统代理（Clash 等会拦截 LAN 请求导致 502/超时）。

上 Linux 服务器：只要能网络可达端点即可；切换端点仅改 .env 的 EMBEDDING_*/RERANK_*，代码不变。
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Tuple

import httpx

from src.config.settings import settings

logger = logging.getLogger(__name__)

# DashScope 等端点单次 input 条数有限，分批发送更稳
_BATCH = 10

_LAN_MARKS = ("localhost", "127.", "192.168.", "10.", "172.16.", "//0.0.0.0")


def is_embedding_enabled() -> bool:
    return bool(settings.embedding_enabled)


def embedding_model() -> str:
    """首选后端的模型名（自检页展示用；实际用哪个以 embed_texts_with_model 返回为准）。"""
    backends = _backends()
    return backends[0]["model"] if backends else (settings.embedding_model or "text-embedding-v4")


def _is_lan(url: str) -> bool:
    return any(m in url for m in _LAN_MARKS)


def _client(url: str) -> httpx.Client:
    """局域网端点绕系统代理，公网端点（DashScope）走默认代理设置。"""
    return httpx.Client(timeout=30.0, trust_env=not _is_lan(url))


def _backends() -> List[Dict[str, str]]:
    """embedding 后端级联表：首选（EMBEDDING_BASE_URL，通常为本地）→ qwen（DashScope）备选。"""
    out: List[Dict[str, str]] = []
    primary = (settings.embedding_base_url or "").rstrip("/")
    if primary:
        out.append({
            "label": "本地" if _is_lan(primary) else "首选",
            "base": primary,
            "key": settings.embedding_api_key or "local",
            "model": settings.embedding_model or "text-embedding-v4",
        })
    qwen = (settings.qwen_base_url or "").rstrip("/")
    if qwen and settings.qwen_api_key and qwen != primary:
        out.append({
            "label": "云端备选",
            "base": qwen,
            "key": settings.qwen_api_key,
            # 备选固定用 DashScope 的 embedding 模型（首选的模型名在云端不存在）
            "model": "text-embedding-v4",
        })
    return out


def _embed_via(backend: Dict[str, str], texts: List[str]) -> Optional[List[List[float]]]:
    """经单个后端做整批 embedding；任一批失败返回 None（由级联切下一个后端）。"""
    url = f"{backend['base']}/embeddings"
    headers = {"Authorization": f"Bearer {backend['key']}", "Content-Type": "application/json"}
    out: List[List[float]] = []
    with _client(url) as client:
        for i in range(0, len(texts), _BATCH):
            chunk = texts[i:i + _BATCH]
            resp = client.post(url, headers=headers,
                               json={"model": backend["model"], "input": chunk})
            resp.raise_for_status()
            data = resp.json().get("data") or []
            if len(data) != len(chunk):
                logger.warning("embedding 返回条数不符：期望 %d 得 %d", len(chunk), len(data))
                return None
            # 按 index 排序，保证与输入顺序一致
            data.sort(key=lambda d: d.get("index", 0))
            out.extend([d.get("embedding") or [] for d in data])
    if any(not v for v in out):
        return None
    return out


def embed_texts_with_model(texts: List[str]) -> Optional[Tuple[str, List[List[float]]]]:
    """把多段文本转成向量列表（顺序对应），返回 (实际使用的模型名, 向量列表)。

    按后端级联表逐个尝试，整批成功才返回——保证同一批向量出自同一模型。
    全部失败返回 None（调用方回退关键词匹配）。
    """
    if not texts:
        return "", []
    backends = _backends()
    if not backends:
        logger.warning("embedding 未配置任何端点（EMBEDDING_BASE_URL 与 qwen 均空），跳过语义召回")
        return None
    for backend in backends:
        try:
            vecs = _embed_via(backend, texts)
            if vecs is not None:
                return backend["model"], vecs
        except Exception as e:
            logger.warning("embedding %s端点(%s)失败，尝试下一后端：%s",
                           backend["label"], backend["base"], repr(e)[:200])
    logger.warning("embedding 所有后端均不可用，回退关键词匹配")
    return None


def embed_texts(texts: List[str]) -> Optional[List[List[float]]]:
    """兼容旧接口：只要向量不关心模型名（设置页自检等使用）。"""
    got = embed_texts_with_model(texts)
    return got[1] if got else None


# ---- 重排序（rerank，精排）----

# Qwen3-Reranker 是「yes/no 判断式」重排器，query/document 必须按官方模板包裹，
# 裸文本喂入分数无区分度（实测裸文本排序错乱，包裹后相关 0.97 vs 无关 0.001）。
_QWEN3_RERANK_PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements '
                        'based on the Query and the Instruct provided. Note that the answer can '
                        'only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
_QWEN3_RERANK_SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
_QWEN3_RERANK_INSTRUCT = "判断该分析报告模板是否适用于给定的数据分析任务"


def is_rerank_configured() -> bool:
    return bool(settings.rerank_base_url and settings.rerank_model)


def rerank_scores(query: str, documents: List[str], instruct: Optional[str] = None) -> Optional[List[float]]:
    """对候选文档按 query 相关性打分，返回与 documents 同序的分数列表；失败返回 None。

    走 vLLM 的 Cohere 兼容 /rerank：results 里每项带原始 index 与 relevance_score。
    instruct 为空时用默认的"是否适用于该任务"判据（召回场景）；传入自定义 instruct
    可复用同一个 rerank 端点做别的判断（如模板去重场景的"是否同一类任务"）。
    """
    if not is_rerank_configured() or not documents:
        return None
    base = settings.rerank_base_url.rstrip("/")
    url = f"{base}/rerank"
    headers = {"Authorization": f"Bearer {settings.rerank_api_key or 'local'}",
               "Content-Type": "application/json"}
    q, docs = query, documents
    if "qwen3-reranker" in settings.rerank_model.lower():
        used_instruct = instruct or _QWEN3_RERANK_INSTRUCT
        q = f"{_QWEN3_RERANK_PREFIX}<Instruct>: {used_instruct}\n<Query>: {query}\n"
        docs = [f"<Document>: {d}{_QWEN3_RERANK_SUFFIX}" for d in documents]
    try:
        with _client(url) as client:
            resp = client.post(url, headers=headers, json={
                "model": settings.rerank_model, "query": q, "documents": docs,
            })
            resp.raise_for_status()
            results = resp.json().get("results") or []
        scores = [0.0] * len(documents)
        for r in results:
            idx = r.get("index")
            if isinstance(idx, int) and 0 <= idx < len(documents):
                scores[idx] = float(r.get("relevance_score") or 0.0)
        return scores
    except Exception as e:
        logger.warning("rerank 调用失败，退回余弦排序：%s", repr(e)[:200])
        return None


def cosine(a: List[float], b: List[float]) -> float:
    """余弦相似度；任一为空或零向量返回 0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
