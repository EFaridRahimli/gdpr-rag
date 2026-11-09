"""Hybrid retrieval: BM25 + vectors fused with RRF, optionally reranked."""

import re
from functools import cache

from . import llm, store

MODES = ("bm25", "vector", "hybrid", "llm-rerank", "local-rerank")
DEFAULT_MODE = "llm-rerank"  # bake-off winner, see eval/results/retrieval.md
LOCAL_RERANK_MODEL = "jinaai/jina-reranker-v1-tiny-en"
# models trained with a query instruction; the rest embed queries as-is
QUERY_PREFIX = {
    "qwen/qwen3-embedding-8b": "Instruct: Given a question about the GDPR, "
    "retrieve the passages that answer it\nQuery: ",
}


def passage(label: str, heading: str, text: str) -> str:
    """Text that gets embedded: the heading gives each chunk its context."""
    return f"{heading}\n{label}\n{text}"


_query_cache: dict[tuple[str, str], list[float]] = {}


def query_vectors(queries: list[str], model: str) -> list[list[float]]:
    """Embed queries in one batch call, caching per (query, model); the eval warms this up front."""
    missing = [q for q in dict.fromkeys(queries) if (q, model) not in _query_cache]
    if missing:
        prefix = QUERY_PREFIX.get(model, "")
        for q, v in zip(missing, llm.embed([prefix + q for q in missing], model), strict=True):
            _query_cache[(q, model)] = v
    return [_query_cache[(q, model)] for q in queries]


def rrf(rankings: list[list[int]], k: int = 60) -> list[int]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, i in enumerate(ranking, 1):
            scores[i] = scores.get(i, 0.0) + 1 / (k + rank)
    return sorted(scores, key=scores.__getitem__, reverse=True)


def _llm_rerank(db, query: str, ids: list[int], k: int) -> list[int]:
    rows = store.get(db, ids)
    listing = "\n\n".join(
        f"[{n}] {rows[i]['label']} | {rows[i]['heading']}\n{rows[i]['text'][:1200]}"
        for n, i in enumerate(ids)
    )
    prompt = (
        f"Question: {query}\n\nPassages:\n{listing}\n\n"
        f"Return the numbers of the {k} passages that best answer the question, most relevant "
        "first, as a JSON array of integers. Output only the array."
    )
    out = llm.chat(
        [{"role": "user", "content": prompt}],
        llm.fast_model(),
        temperature=0,
    )
    picked = [int(n) for n in re.findall(r"\d+", out)]  # tolerates code fences or prose
    order = [ids[n] for n in dict.fromkeys(picked) if 0 <= n < len(ids)]
    return (order + [i for i in ids if i not in order])[:k]  # pad if the model returned fewer


@cache
def _local_reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder  # optional extra: local-rerank

    return TextCrossEncoder(LOCAL_RERANK_MODEL)


def _local_rerank(db, query: str, ids: list[int], k: int) -> list[int]:
    rows = store.get(db, ids)
    texts = [passage(rows[i]["label"], rows[i]["heading"], rows[i]["text"]) for i in ids]
    scores = list(_local_reranker().rerank(query, texts))
    return [i for i, _ in sorted(zip(ids, scores), key=lambda p: p[1], reverse=True)[:k]]


def search(
    db, query: str, mode: str = DEFAULT_MODE, k: int = 6, embed_model: str = llm.EMBED_MODEL,
    candidates: int = 30, scope: str = "all",
) -> list[int]:
    if mode == "bm25":
        return store.bm25(db, query, k, scope)
    vec = query_vectors([query], embed_model)[0]
    if mode == "vector":
        return store.knn(db, embed_model, vec, k, scope)
    fused = rrf([
        store.bm25(db, query, candidates, scope),
        store.knn(db, embed_model, vec, candidates, scope),
    ])
    if not fused:
        return []
    if mode == "hybrid":
        return fused[:k]
    if mode == "llm-rerank":
        return _llm_rerank(db, query, fused[:candidates], k)
    return _local_rerank(db, query, fused[:candidates], k)
