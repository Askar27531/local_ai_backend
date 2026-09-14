"""世界知识的 Milvus 向量召回、条件过滤和 CrossEncoder 重排。

Milvus 的 npc_id/unlock_level 过滤是第一层剧情边界，Intent Policy 来源过滤与敏感词过滤
是第二层；可选重排只在过滤后的候选上运行，最终结果还会进入 Context Compiler 复查。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

from npc_app.utils import contains_any, dialogue_config

_RETRIEVAL_CONFIG = dialogue_config()["retrieval"]

COLLECTION_NAME = os.getenv("MILVUS_COLLECTION_NAME", "guichao_island_chunks")
MILVUS_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
MILVUS_TOKEN = os.getenv("MILVUS_TOKEN", "root:Milvus")
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "BAAI/bge-small-zh-v1.5")
RERANK_MODEL_NAME = os.getenv(
    "RERANK_MODEL_NAME",
    "BAAI/bge-reranker-base",
)
RERANK_CANDIDATE_K = int(os.getenv("NPC_RERANK_CANDIDATE_K", "20"))
RERANK_MAX_LENGTH = int(os.getenv("NPC_RERANK_MAX_LENGTH", "128"))
RERANK_CONTENT_CHARS = int(os.getenv("NPC_RERANK_CONTENT_CHARS", "400"))
RERANK_VECTOR_WEIGHT = float(os.getenv("NPC_RERANK_VECTOR_WEIGHT", "0.45"))
RERANK_CROSS_WEIGHT = float(os.getenv("NPC_RERANK_CROSS_WEIGHT", "0.55"))
RERANK_RRF_K = int(os.getenv("NPC_RERANK_RRF_K", "10"))
RERANK_LOW_CONFIDENCE = float(os.getenv("NPC_RERANK_LOW_CONFIDENCE", "0.50"))

ALWAYS_RERANK_INTENTS = set(_RETRIEVAL_CONFIG["always_rerank_intents"])
SOURCE_PRIORITY_BY_INTENT = {
    intent: tuple(prefixes) for intent, prefixes in _RETRIEVAL_CONFIG["source_priority_by_intent"].items()
}


@dataclass
class RetrievedChunk:
    """一段可供 NPC 参考的世界知识及其检索元数据。

    score 初始为向量相似度，经过重排后会变成融合排名分；调用者只应比较同一阶段的
    score。content 用于 Prompt，snippet 是给 Unity 来源事件使用的短预览。
    """

    source_file: str
    section_title: str
    content: str
    npc_id: str
    unlock_level: int
    topics: str
    spoiler_level: str
    score: float

    @property
    def snippet(self) -> str:
        """返回去换行的短预览，避免 sources 事件暴露整段知识正文。"""
        return self.content[:240].replace("\n", " ").strip()


@lru_cache(maxsize=1)
def get_embedding_model():
    """延迟加载并缓存查询/记忆共用的中文向量模型。"""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBED_MODEL_NAME)


@lru_cache(maxsize=1)
def get_milvus_client():
    """延迟创建并缓存 Milvus Client；仅在配置 Token 时启用认证参数。"""
    from pymilvus import MilvusClient

    if MILVUS_TOKEN:
        return MilvusClient(uri=MILVUS_URI, token=MILVUS_TOKEN)
    return MilvusClient(uri=MILVUS_URI)


@lru_cache(maxsize=1)
def get_reranker_model():
    """延迟加载并缓存 CrossEncoder，避免无需重排的简单请求承担启动成本。"""
    from sentence_transformers import CrossEncoder

    return CrossEncoder(RERANK_MODEL_NAME, max_length=RERANK_MAX_LENGTH)


def retrieve_game_chunks(
    question: str,
    npc_id: str,
    unlocked_story_level: int,
    top_k: int = 5,
    candidate_k: int | None = None,
) -> list[RetrievedChunk]:
    """按 NPC 和剧情解锁等级执行世界知识向量召回。"""
    limit = top_k if candidate_k is None else candidate_k
    if top_k < 1 or limit < top_k:
        raise ValueError("retrieval requires candidate_k >= top_k >= 1")
    model = get_embedding_model()
    client = get_milvus_client()

    # 归一化向量与 Collection 的 COSINE 度量约定一致，使不同查询的相似度可稳定比较。
    query_vector = model.encode(
        [question],
        normalize_embeddings=True,
    )[0].tolist()

    # 使用表达式模板和 filter_params 绑定业务值，避免把客户端内容拼进 Milvus 过滤语句。
    filter_expr = "npc_id == {npc_id} and unlock_level <= {unlock_level}"

    results = client.search(
        collection_name=COLLECTION_NAME,
        data=[query_vector],
        filter=filter_expr,
        filter_params={"npc_id": npc_id, "unlock_level": unlocked_story_level},
        limit=limit,
        output_fields=[
            "source_file",
            "section_title",
            "content",
            "npc_id",
            "unlock_level",
            "topics",
            "spoiler_level",
        ],
    )

    hits = results[0] if results else []
    return [_hit_to_chunk(hit) for hit in hits]


def retrieve_and_rerank_game_chunks(
    question: str,
    npc_id: str,
    unlocked_story_level: int,
    top_k: int = 5,
    candidate_k: int = RERANK_CANDIDATE_K,
    intent: str = "unknown",
) -> list[RetrievedChunk]:
    """执行无额外 Policy 过滤的向量召回与强制重排，供诊断和专项调用使用。"""
    candidates = retrieve_game_chunks(
        question=question,
        npc_id=npc_id,
        unlocked_story_level=unlocked_story_level,
        top_k=top_k,
        candidate_k=candidate_k,
    )
    return rerank_game_chunks(
        question,
        candidates,
        top_k=top_k,
        preferred_source_prefixes=SOURCE_PRIORITY_BY_INTENT.get(intent, ()),
    )


def retrieve_conditional_game_chunks(
    question: str,
    npc_id: str,
    unlocked_story_level: int,
    intent: str,
    top_k: int = 5,
    candidate_k: int = RERANK_CANDIDATE_K,
    allowed_source_prefixes: tuple[str, ...] = (),
    forbidden_context_terms: tuple[str, ...] = (),
) -> list[RetrievedChunk]:
    """在向量召回后执行意图来源、剧透词和按需重排策略。"""
    candidates = retrieve_game_chunks(
        question=question,
        npc_id=npc_id,
        unlocked_story_level=unlocked_story_level,
        top_k=top_k,
        candidate_k=candidate_k,
    )
    # 先移除 Policy 不允许的来源和敏感内容，CrossEncoder 永远看不到越权候选。
    candidates = [
        chunk
        for chunk in candidates
        if (not allowed_source_prefixes or chunk.source_file.startswith(allowed_source_prefixes))
        and not contains_any(
            f"{chunk.source_file}\n{chunk.section_title}\n{chunk.topics}\n{chunk.content}",
            forbidden_context_terms,
        )
    ]
    # 来源优先级只是小幅同分修正，不能让低相关来源压过明显更相关的候选。
    preferred = SOURCE_PRIORITY_BY_INTENT.get(intent, ())
    if should_use_cross_encoder(intent, candidates):
        return rerank_game_chunks(
            question,
            candidates,
            top_k=top_k,
            preferred_source_prefixes=preferred,
        )
    return apply_source_priority(candidates, preferred, top_k=top_k)


def rerank_game_chunks(
    question: str,
    chunks: list[RetrievedChunk],
    top_k: int = 5,
    preferred_source_prefixes: tuple[str, ...] = (),
) -> list[RetrievedChunk]:
    """融合向量排名、CrossEncoder 排名和轻量来源优先级。"""
    if not chunks:
        return []
    model = get_reranker_model()
    # CrossEncoder 联合阅读“问题 + 候选”，比独立向量编码更精确但计算成本更高。
    pairs = [(question, _rerank_document(chunk)) for chunk in chunks]
    cross_scores = model.predict(pairs, show_progress_bar=False)
    cross_order = sorted(range(len(chunks)), key=lambda index: float(cross_scores[index]), reverse=True)
    cross_ranks = {index: rank for rank, index in enumerate(cross_order, start=1)}

    # 使用 RRF 融合两种排名，避免直接比较不同模型量纲不一致的原始分数。
    rescored: list[RetrievedChunk] = []
    for index, chunk in enumerate(chunks):
        vector_rank = index + 1
        cross_rank = cross_ranks[index]
        score = (
            RERANK_VECTOR_WEIGHT / (RERANK_RRF_K + vector_rank)
            + RERANK_CROSS_WEIGHT / (RERANK_RRF_K + cross_rank)
            + _source_priority_bonus(chunk.source_file, preferred_source_prefixes)
        )
        rescored.append(replace(chunk, score=score))
    return sorted(rescored, key=lambda chunk: chunk.score, reverse=True)[:top_k]


def should_use_cross_encoder(intent: str, chunks: list[RetrievedChunk]) -> bool:
    """高风险/低置信回合启用重排，简单且高置信的查询跳过额外模型。"""
    if not chunks:
        return False
    if intent in ALWAYS_RERANK_INTENTS:
        return True
    if intent in {"ask_location", "ask_current_task"} and chunks[0].score >= RERANK_LOW_CONFIDENCE:
        return False
    return chunks[0].score < RERANK_LOW_CONFIDENCE


def apply_source_priority(
    chunks: list[RetrievedChunk],
    preferred_source_prefixes: tuple[str, ...],
    top_k: int,
) -> list[RetrievedChunk]:
    """无需 CrossEncoder 时，仅施加轻量来源加分并截取 top_k。"""
    rescored = [
        replace(chunk, score=chunk.score + _source_priority_bonus(chunk.source_file, preferred_source_prefixes))
        for chunk in chunks
    ]
    return sorted(rescored, key=lambda chunk: chunk.score, reverse=True)[:top_k]


def _source_priority_bonus(source_file: str, prefixes: tuple[str, ...]) -> float:
    """按配置前缀顺序计算递减的小额来源加分。"""
    for index, prefix in enumerate(prefixes):
        if source_file.startswith(prefix):
            return 0.006 * (len(prefixes) - index) / len(prefixes)
    return 0.0


def _rerank_document(chunk: RetrievedChunk) -> str:
    """拼接标题、主题和截断正文，控制 CrossEncoder 单候选输入成本。"""
    return "\n".join(
        part
        for part in (
            chunk.section_title,
            chunk.topics,
            chunk.content[:RERANK_CONTENT_CHARS],
        )
        if part
    )


def _hit_to_chunk(hit: Any) -> RetrievedChunk:
    """兼容字典式与 SDK Hit 对象，并规范化为领域数据结构。"""
    entity = hit["entity"]
    score = hit["distance"]

    return RetrievedChunk(
        source_file=str(entity["source_file"]),
        section_title=str(entity["section_title"]),
        content=str(entity["content"]),
        npc_id=str(entity["npc_id"]),
        unlock_level=int(entity["unlock_level"]),
        topics=str(entity["topics"]),
        spoiler_level=str(entity["spoiler_level"]),
        score=float(score),
    )
