from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from npc_app.services.memory_service import RetrievedMemoryItem
from npc_app.services.milvus_retriever_service import MILVUS_TOKEN, MILVUS_URI, get_embedding_model


MEMORY_COLLECTION_NAME = os.getenv("NPC_MEMORY_MILVUS_COLLECTION", "npc_long_term_memories")
MEMORY_VECTOR_DIM = int(os.getenv("NPC_MEMORY_VECTOR_DIM", "384"))


@lru_cache(maxsize=1)
def get_memory_milvus_client():
    from pymilvus import MilvusClient

    if MILVUS_TOKEN:
        return MilvusClient(uri=MILVUS_URI, token=MILVUS_TOKEN)
    return MilvusClient(uri=MILVUS_URI)


def ensure_memory_collection() -> bool:
    try:
        from pymilvus import DataType, MilvusClient

        client = get_memory_milvus_client()
        if client.has_collection(MEMORY_COLLECTION_NAME):
            client.load_collection(collection_name=MEMORY_COLLECTION_NAME)
            return True

        dim = _embedding_dim()
        schema = MilvusClient.create_schema(
            auto_id=True,
            enable_dynamic_field=False,
        )
        schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)
        schema.add_field(field_name="memory_item_id", datatype=DataType.INT64)
        schema.add_field(field_name="user_id", datatype=DataType.INT64)
        schema.add_field(field_name="thread_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="npc_id", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="memory_type", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=1024)
        schema.add_field(field_name="keywords", datatype=DataType.VARCHAR, max_length=512)
        schema.add_field(field_name="importance", datatype=DataType.INT64)
        schema.add_field(field_name="source_record_id", datatype=DataType.INT64)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="vector",
            index_type="AUTOINDEX",
            metric_type="COSINE",
        )
        index_params.add_index(field_name="user_id", index_type="AUTOINDEX")
        index_params.add_index(field_name="thread_id", index_type="AUTOINDEX")
        index_params.add_index(field_name="npc_id", index_type="AUTOINDEX")

        client.create_collection(
            collection_name=MEMORY_COLLECTION_NAME,
            schema=schema,
            index_params=index_params,
        )
        client.load_collection(collection_name=MEMORY_COLLECTION_NAME)
        return True
    except Exception:
        return False


def upsert_memory_item_to_milvus(
    memory_item_id: int,
    user_id: int,
    thread_id: str,
    npc_id: str,
    memory_type: str,
    content: str,
    keywords: str,
    importance: int,
    source_record_id: int,
) -> bool:
    if not ensure_memory_collection():
        return False

    try:
        client = get_memory_milvus_client()
        vector = _embed_text(f"{content}\n{keywords}")
        client.delete(
            collection_name=MEMORY_COLLECTION_NAME,
            filter=f"memory_item_id == {int(memory_item_id)}",
        )
        client.insert(
            collection_name=MEMORY_COLLECTION_NAME,
            data=[
                {
                    "vector": vector,
                    "memory_item_id": int(memory_item_id),
                    "user_id": int(user_id),
                    "thread_id": thread_id,
                    "npc_id": npc_id,
                    "memory_type": memory_type[:64],
                    "content": content[:1024],
                    "keywords": keywords[:512],
                    "importance": int(importance),
                    "source_record_id": int(source_record_id),
                }
            ],
        )
        client.flush(collection_name=MEMORY_COLLECTION_NAME)
        return True
    except Exception:
        return False


def retrieve_memory_items_from_milvus(
    user_id: int,
    thread_id: str,
    npc_id: str,
    question: str,
    limit: int = 5,
) -> list[RetrievedMemoryItem]:
    if not ensure_memory_collection():
        return []

    try:
        client = get_memory_milvus_client()
        query_vector = _embed_text(question)
        escaped_thread_id = thread_id.replace('"', '\\"')
        escaped_npc_id = npc_id.replace('"', '\\"')
        filter_expr = (
            f'user_id == {int(user_id)} '
            f'and thread_id == "{escaped_thread_id}" '
            f'and npc_id == "{escaped_npc_id}"'
        )
        results = client.search(
            collection_name=MEMORY_COLLECTION_NAME,
            data=[query_vector],
            filter=filter_expr,
            limit=int(limit),
            output_fields=[
                "memory_type",
                "content",
                "keywords",
                "importance",
            ],
        )
        hits = results[0] if results else []
        return [_hit_to_memory_item(hit) for hit in hits]
    except Exception:
        return []


def _hit_to_memory_item(hit: Any) -> RetrievedMemoryItem:
    entity = hit.get("entity", {}) if isinstance(hit, dict) else hit["entity"]
    score = hit.get("distance", 0.0) if isinstance(hit, dict) else hit["distance"]
    return RetrievedMemoryItem(
        memory_type=str(entity.get("memory_type", "")),
        content=str(entity.get("content", "")),
        keywords=str(entity.get("keywords", "")),
        importance=int(entity.get("importance", 3)),
        score=float(score),
    )


def _embed_text(text: str) -> list[float]:
    model = get_embedding_model()
    return model.encode([text], normalize_embeddings=True)[0].tolist()


def _embedding_dim() -> int:
    try:
        model = get_embedding_model()
        dim = getattr(model, "get_sentence_embedding_dimension", lambda: MEMORY_VECTOR_DIM)()
        return int(dim or MEMORY_VECTOR_DIM)
    except Exception:
        return MEMORY_VECTOR_DIM
