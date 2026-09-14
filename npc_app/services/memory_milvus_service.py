"""长期记忆 Milvus Collection 的生命周期、写入适配与语义召回。

Collection 必须通过显式初始化脚本创建；Runtime 启动只校验并加载，普通流量只读写记录，
绝不隐式创建或修改 Schema。检索异常返回空集合，由上层回退到 PostgreSQL。
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

from npc_app.services.memory_service import RetrievedMemoryItem
from npc_app.services.milvus_retriever_service import MILVUS_TOKEN, MILVUS_URI, get_embedding_model

MEMORY_COLLECTION_NAME = os.getenv("NPC_MEMORY_MILVUS_COLLECTION", "npc_long_term_memories_v2")
MEMORY_VECTOR_DIM = int(os.getenv("NPC_MEMORY_VECTOR_DIM", "512"))
logger = logging.getLogger(__name__)
REQUIRED_MEMORY_FIELDS = {
    "id", "vector", "memory_item_id", "user_id", "thread_id", "npc_id",
    "memory_type", "content", "keywords", "importance",
}


@lru_cache(maxsize=1)
def get_memory_milvus_client():
    """延迟创建并缓存独立的记忆 Milvus Client。"""
    from pymilvus import MilvusClient

    if MILVUS_TOKEN:
        return MilvusClient(uri=MILVUS_URI, token=MILVUS_TOKEN)
    return MilvusClient(uri=MILVUS_URI)


def create_memory_collection() -> None:
    """显式创建带版本的记忆 Collection；Runtime 请求不会调用此函数。"""
    from pymilvus import DataType, MilvusClient

    client = get_memory_milvus_client()
    if client.has_collection(MEMORY_COLLECTION_NAME):
        raise RuntimeError(f"memory collection already exists: {MEMORY_COLLECTION_NAME}")
    # 关闭动态字段让部署 Schema 与写入适配器严格一致，错误字段不会被静默接受。
    schema = MilvusClient.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
    schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=_embedding_dim())
    schema.add_field(field_name="memory_item_id", datatype=DataType.INT64)
    schema.add_field(field_name="user_id", datatype=DataType.INT64)
    schema.add_field(field_name="thread_id", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="npc_id", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="memory_type", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=1024)
    schema.add_field(field_name="keywords", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="importance", datatype=DataType.INT64)
    # vector 用于相似度搜索；三个隔离键建立标量索引以加速严格过滤。
    index_params = client.prepare_index_params()
    index_params.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
    index_params.add_index(field_name="user_id", index_type="AUTOINDEX")
    index_params.add_index(field_name="thread_id", index_type="AUTOINDEX")
    index_params.add_index(field_name="npc_id", index_type="AUTOINDEX")
    client.create_collection(
        collection_name=MEMORY_COLLECTION_NAME,
        schema=schema,
        index_params=index_params,
    )


def validate_memory_collection() -> dict[str, Any]:
    """只读校验 Collection 元数据，不创建或加载任何资源。"""
    client = get_memory_milvus_client()
    if not client.has_collection(MEMORY_COLLECTION_NAME):
        raise RuntimeError(
            f"memory collection does not exist: {MEMORY_COLLECTION_NAME}; "
            "run python -m scripts.init_memory_milvus"
        )
    description = client.describe_collection(collection_name=MEMORY_COLLECTION_NAME)
    # 同时检查必需字段、向量维度和索引，避免“Collection 存在但与当前代码不兼容”。
    fields = description["fields"]
    field_names = {str(field["name"]) for field in fields}
    missing = sorted(REQUIRED_MEMORY_FIELDS - field_names)
    if missing:
        raise RuntimeError(f"memory collection schema is missing fields: {', '.join(missing)}")
    vector_field = next(field for field in fields if field["name"] == "vector")
    actual_dim = int(vector_field["params"]["dim"])
    expected_dim = MEMORY_VECTOR_DIM
    if actual_dim != expected_dim:
        raise RuntimeError(f"memory vector dimension mismatch: expected {expected_dim}, got {actual_dim}")
    indexes = client.list_indexes(collection_name=MEMORY_COLLECTION_NAME)
    if not indexes:
        raise RuntimeError("memory collection has no indexes")
    return {"collection": MEMORY_COLLECTION_NAME, "vector_dim": actual_dim}


def load_memory_collection() -> None:
    """应用启动时校验并加载预先创建的记忆 Collection。"""
    validate_memory_collection()
    get_memory_milvus_client().load_collection(collection_name=MEMORY_COLLECTION_NAME)


def upsert_memory_item_to_milvus(
    memory_item_id: int,
    user_id: int,
    thread_id: str,
    npc_id: str,
    memory_type: str,
    content: str,
    keywords: str,
    importance: int,
) -> bool:
    """用 PostgreSQL 记忆 ID 替换对应向量记录；同步失败不回滚业务事实。

    写入前验证所有 VARCHAR 长度和 importance，防止依赖 Milvus 的隐式截断。函数捕获
    连接、嵌入和写入异常并返回 False，因为 PostgreSQL 中的业务记录仍然有效，后续可重建索引。
    """
    _validate_memory_record(memory_type, content, keywords, importance)
    try:
        client = get_memory_milvus_client()
        vector = _embed_text(f"{content}\n{keywords}")
        # Milvus 无业务主键更新语义，先按稳定的 PostgreSQL ID 删除再插入最新向量。
        client.delete(
            collection_name=MEMORY_COLLECTION_NAME,
            filter="memory_item_id == {memory_item_id}",
            filter_params={"memory_item_id": memory_item_id},
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
                    "memory_type": memory_type,
                    "content": content,
                    "keywords": keywords,
                    "importance": importance,
                }
            ],
        )
        client.flush(collection_name=MEMORY_COLLECTION_NAME)
        return True
    except Exception:
        logger.exception("Failed to sync NPC long-term memory item %s to Milvus", memory_item_id)
        return False


def retrieve_memory_items_from_milvus(
    user_id: int,
    thread_id: str,
    npc_id: str,
    question: str,
    limit: int = 5,
) -> list[RetrievedMemoryItem]:
    """在用户、线程和 NPC 三重隔离条件内召回长期记忆。

    输入问题使用与世界知识相同的归一化嵌入模型。Milvus distance 作为当前问题的 score
    原样带回，后续 Context Compiler 还会结合类型、importance 和关键词重叠重新排序。
    """
    try:
        client = get_memory_milvus_client()
        query_vector = _embed_text(question)
        # 所有隔离键均通过 filter_params 绑定，避免跨用户或跨 NPC 召回记忆。
        filter_expr = "user_id == {user_id} and thread_id == {thread_id} and npc_id == {npc_id}"
        results = client.search(
            collection_name=MEMORY_COLLECTION_NAME,
            data=[query_vector],
            filter=filter_expr,
            filter_params={"user_id": user_id, "thread_id": thread_id, "npc_id": npc_id},
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
        # 语义索引故障按空结果降级，调用方随后使用 PostgreSQL 关键词检索兜底。
        logger.exception("Failed to retrieve NPC long-term memories from Milvus")
        return []


def _validate_memory_record(memory_type: str, content: str, keywords: str, importance: int) -> None:
    """在 Milvus 边界按实际 Schema 校验记录，禁止静默截断造成索引与业务库不一致。"""
    if not 1 <= len(memory_type) <= 64:
        raise ValueError("memory_type must contain 1-64 characters")
    if not 1 <= len(content) <= 1024:
        raise ValueError("memory content must contain 1-1024 characters")
    if len(keywords) > 512:
        raise ValueError("memory keywords must not exceed 512 characters")
    if not 1 <= importance <= 5:
        raise ValueError("memory importance must be between 1 and 5")


def _hit_to_memory_item(hit: Any) -> RetrievedMemoryItem:
    """兼容字典式与 SDK Hit 对象，转换为上层共用的记忆结构。"""
    entity = hit["entity"]
    score = hit["distance"]
    return RetrievedMemoryItem(
        memory_type=str(entity["memory_type"]),
        content=str(entity["content"]),
        keywords=str(entity["keywords"]),
        importance=int(entity["importance"]),
        score=float(score),
    )


def _embed_text(text: str) -> list[float]:
    """生成归一化向量，使记忆写入与查询采用一致的 COSINE 表示。"""
    model = get_embedding_model()
    return model.encode([text], normalize_embeddings=True)[0].tolist()


def _embedding_dim() -> int:
    """读取并验证模型真实维度，避免用错误维度创建 Collection。"""
    dim = get_embedding_model().get_sentence_embedding_dimension()
    if dim is None or int(dim) <= 0:
        raise RuntimeError("embedding model returned an invalid dimension")
    return int(dim)
