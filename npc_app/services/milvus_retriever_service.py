from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, List


COLLECTION_NAME = os.getenv("MILVUS_COLLECTION_NAME", "guichao_island_chunks")
MILVUS_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
MILVUS_TOKEN = os.getenv("MILVUS_TOKEN", "root:Milvus")
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "BAAI/bge-small-zh-v1.5")


@dataclass
class RetrievedChunk:
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
        return self.content[:240].replace("\n", " ").strip()


@lru_cache(maxsize=1)
def get_embedding_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBED_MODEL_NAME)


@lru_cache(maxsize=1)
def get_milvus_client():
    from pymilvus import MilvusClient

    if MILVUS_TOKEN:
        return MilvusClient(uri=MILVUS_URI, token=MILVUS_TOKEN)
    return MilvusClient(uri=MILVUS_URI)


def retrieve_game_chunks(
    question: str,
    npc_id: str,
    unlocked_story_level: int,
    top_k: int = 5,
    candidate_k: int | None = None,
) -> List[RetrievedChunk]:
    model = get_embedding_model()
    client = get_milvus_client()

    query_vector = model.encode(
        [question],
        normalize_embeddings=True,
    )[0].tolist()

    filter_expr = f'npc_id == "{npc_id}" and unlock_level <= {int(unlocked_story_level)}'

    results = client.search(
        collection_name=COLLECTION_NAME,
        data=[query_vector],
        filter=filter_expr,
        limit=int(candidate_k or top_k),
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


def _hit_to_chunk(hit: Any) -> RetrievedChunk:
    entity = hit.get("entity", {}) if isinstance(hit, dict) else hit["entity"]
    score = hit.get("distance", 0.0) if isinstance(hit, dict) else hit["distance"]

    return RetrievedChunk(
        source_file=str(entity.get("source_file", "")),
        section_title=str(entity.get("section_title", "")),
        content=str(entity.get("content", "")),
        npc_id=str(entity.get("npc_id", "")),
        unlock_level=int(entity.get("unlock_level", 0)),
        topics=str(entity.get("topics", "")),
        spoiler_level=str(entity.get("spoiler_level", "")),
        score=float(score),
    )
