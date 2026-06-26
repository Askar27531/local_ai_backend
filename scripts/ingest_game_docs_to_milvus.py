from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pymilvus import DataType, MilvusClient
from sentence_transformers import SentenceTransformer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOC_DIR = PROJECT_ROOT / "game_docs"

MANIFEST_PATH = DOC_DIR / "manifest_v03.json"
NPC_MATRIX_PATH = DOC_DIR / "npc_knowledge_matrix_v03.json"

COLLECTION_NAME = "guichao_island_chunks"

# Docker / Milvus Standalone 默认配置
MILVUS_URI = "http://localhost:19530"
MILVUS_TOKEN = "root:Milvus"

# 如果你用 Milvus Lite，改成下面这样：
# MILVUS_URI = str(PROJECT_ROOT / "milvus_guichao.db")
# MILVUS_TOKEN = ""

EMBED_MODEL_NAME = "BAAI/bge-small-zh-v1.5"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def stable_hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """
    解析 Markdown 顶部的简单 YAML frontmatter。
    这里故意只支持本项目正在使用的 key/value 与列表格式，避免给入库脚本引入额外复杂度。
    """
    metadata: dict[str, Any] = {}
    if not text.startswith("---"):
        return metadata, text

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.S)
    if not match:
        return metadata, text

    raw_meta = match.group(1)
    body = text[match.end():]
    current_key: str | None = None

    for line in raw_meta.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if re.match(r"^[A-Za-z_][\w-]*:", stripped):
            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()
            current_key = key

            if not value:
                metadata[key] = []
            elif value.isdigit():
                metadata[key] = int(value)
            elif value.lower() in {"true", "false"}:
                metadata[key] = value.lower() == "true"
            else:
                metadata[key] = value.strip('"').strip("'")
            continue

        if stripped.startswith("-") and current_key:
            if not isinstance(metadata.get(current_key), list):
                metadata[current_key] = []
            metadata[current_key].append(stripped.lstrip("-").strip())

    return metadata, body


def extract_first_h1(text: str) -> str | None:
    match = re.search(r"^#\s+(.+)$", text, flags=re.M)
    return match.group(1).strip() if match else None


def split_markdown_by_h2(text: str) -> list[dict[str, str]]:
    """
    按二级标题切块。
    每个 chunk 保留 section_title 和 content，content 会补上一级标题以增强语义。
    """
    text = text.strip()
    if not text:
        return []

    h1_title = extract_first_h1(text)
    if "## " not in text:
        return [{"section_title": h1_title or "全文", "content": text}]

    chunks: list[dict[str, str]] = []
    for part in re.split(r"(?=^##\s+)", text, flags=re.M):
        part = part.strip()
        if not part or part.startswith("# ") and "## " not in part:
            continue

        title_match = re.match(r"^##\s+(.+)$", part, flags=re.M)
        section_title = title_match.group(1).strip() if title_match else (h1_title or "未命名章节")
        content = part
        if h1_title and h1_title not in content[:100]:
            content = f"# {h1_title}\n\n{content}"

        if len(content) >= 40:
            chunks.append({"section_title": section_title, "content": content})

    return chunks


def parse_unlock_level(section_title: str, default_level: int) -> int:
    match = re.search(r"解锁\s*(\d+)", section_title)
    return int(match.group(1)) if match else int(default_level)


def guess_spoiler_level(unlock_level: int) -> str:
    if unlock_level <= 1:
        return "low"
    if unlock_level <= 3:
        return "mid"
    if unlock_level == 4:
        return "high"
    return "final"


def list_ingest_doc_files(manifest: dict[str, Any]) -> list[Path]:
    """
    入库文件以 manifest_v03.json 为准。
    文档是否应该入库是文档配置问题，不在本脚本里硬编码前缀规则。
    """
    recommended = manifest.get("ingest_recommended", [])
    if not isinstance(recommended, list):
        raise ValueError("manifest_v03.json 中的 ingest_recommended 必须是列表")

    return [DOC_DIR / str(file_name) for file_name in recommended]


def get_allowed_npcs_by_source(npc_matrix: dict[str, Any]) -> dict[str, list[str]]:
    allowed_by_source: dict[str, list[str]] = {}

    for npc_id, config in npc_matrix.items():
        allowed_sources = config.get("allowed_sources", [])
        if not isinstance(allowed_sources, list):
            continue

        for source_file in allowed_sources:
            allowed_by_source.setdefault(str(source_file), []).append(str(npc_id))

    return {
        source_file: sorted(set(npc_ids))
        for source_file, npc_ids in allowed_by_source.items()
    }


def build_base_chunks(
    manifest: dict[str, Any],
    npc_matrix: dict[str, Any],
) -> list[dict[str, Any]]:
    md_files = list_ingest_doc_files(manifest)
    allowed_by_source = get_allowed_npcs_by_source(npc_matrix)
    base_chunks: list[dict[str, Any]] = []

    print("准备入库的 Markdown 文件：")
    for path in md_files:
        print(" -", path.name)

    for path in md_files:
        if not path.exists():
            raise FileNotFoundError(f"manifest 指向不存在的文件：{path}")

        raw_text = read_text(path)
        meta, body = parse_frontmatter(raw_text)
        if meta.get("rag_ingest") is False:
            print(f"跳过 rag_ingest=false：{path.name}")
            continue

        default_unlock_level = int(meta.get("default_unlock_level", 0))
        topics = meta.get("recommended_topics", [])
        topics_str = ",".join(topics) if isinstance(topics, list) else str(topics)
        allowed_npcs = allowed_by_source.get(path.name, [])

        if not allowed_npcs:
            print(f"跳过没有 NPC 权限的文件：{path.name}")
            continue

        for index, chunk in enumerate(split_markdown_by_h2(body)):
            section_title = chunk["section_title"]
            unlock_level = parse_unlock_level(section_title, default_unlock_level)
            chunk_uid = stable_hash(f"{path.name}-{index}-{section_title}")

            base_chunks.append(
                {
                    "chunk_uid": chunk_uid,
                    "source_file": path.name,
                    "section_title": section_title,
                    "content": chunk["content"],
                    "unlock_level": unlock_level,
                    "topics": topics_str,
                    "allowed_npcs": allowed_npcs,
                }
            )

    print(f"基础 chunk 数量：{len(base_chunks)}")
    return base_chunks


def build_records(
    model: SentenceTransformer,
    manifest: dict[str, Any],
    npc_matrix: dict[str, Any],
) -> list[dict[str, Any]]:
    base_chunks = build_base_chunks(manifest, npc_matrix)
    if not base_chunks:
        return []

    texts = [chunk["content"] for chunk in base_chunks]

    print("开始生成 embedding...")
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=True,
    )

    records: list[dict[str, Any]] = []
    for chunk, vector in zip(base_chunks, vectors):
        for npc_id in chunk["allowed_npcs"]:
            records.append(
                {
                    "vector": vector.tolist(),
                    "chunk_uid": chunk["chunk_uid"],
                    "source_file": chunk["source_file"],
                    "section_title": chunk["section_title"],
                    "content": chunk["content"][:7900],
                    "npc_id": npc_id,
                    "unlock_level": int(chunk["unlock_level"]),
                    "topics": chunk["topics"][:500],
                    "spoiler_level": guess_spoiler_level(int(chunk["unlock_level"])),
                }
            )

    print(f"按 NPC 展开后的入库记录数量：{len(records)}")
    return records


def create_collection(client: MilvusClient, dim: int) -> None:
    if client.has_collection(COLLECTION_NAME):
        print(f"发现已有 collection：{COLLECTION_NAME}，删除后重建。")
        client.drop_collection(COLLECTION_NAME)

    schema = MilvusClient.create_schema(
        auto_id=True,
        enable_dynamic_field=False,
    )
    schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
    schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=dim)
    schema.add_field(field_name="chunk_uid", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="source_file", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="section_title", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=8192)
    schema.add_field(field_name="npc_id", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="unlock_level", datatype=DataType.INT64)
    schema.add_field(field_name="topics", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="spoiler_level", datatype=DataType.VARCHAR, max_length=64)

    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="vector",
        index_type="AUTOINDEX",
        metric_type="COSINE",
    )
    index_params.add_index(field_name="npc_id", index_type="AUTOINDEX")
    index_params.add_index(field_name="unlock_level", index_type="AUTOINDEX")

    client.create_collection(
        collection_name=COLLECTION_NAME,
        schema=schema,
        index_params=index_params,
    )
    print(f"已创建 collection：{COLLECTION_NAME}")


def insert_in_batches(
    client: MilvusClient,
    records: list[dict[str, Any]],
    batch_size: int = 64,
) -> None:
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        result = client.insert(collection_name=COLLECTION_NAME, data=batch)
        print(f"已插入 {start + len(batch)}/{len(records)}，结果：{result}")

    client.flush(collection_name=COLLECTION_NAME)
    client.load_collection(collection_name=COLLECTION_NAME)
    print("collection 已 flush 并加载。")


def main() -> None:
    if not DOC_DIR.exists():
        raise FileNotFoundError(f"找不到文档目录：{DOC_DIR}")
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"找不到 manifest：{MANIFEST_PATH}")
    if not NPC_MATRIX_PATH.exists():
        raise FileNotFoundError(f"找不到 NPC 知识矩阵：{NPC_MATRIX_PATH}")

    print("文档目录：", DOC_DIR)
    manifest = read_json(MANIFEST_PATH)
    npc_matrix = read_json(NPC_MATRIX_PATH)

    print("加载 embedding 模型：", EMBED_MODEL_NAME)
    model = SentenceTransformer(EMBED_MODEL_NAME)
    dim = model.get_sentence_embedding_dimension()
    print("embedding 维度：", dim)

    print("连接 Milvus：", MILVUS_URI)
    client = (
        MilvusClient(uri=MILVUS_URI, token=MILVUS_TOKEN)
        if MILVUS_TOKEN
        else MilvusClient(uri=MILVUS_URI)
    )

    create_collection(client, dim)
    records = build_records(model, manifest, npc_matrix)
    if not records:
        print("没有生成任何入库记录，请检查 manifest 和 npc_knowledge_matrix。")
        return

    insert_in_batches(client, records)
    stats = client.get_collection_stats(COLLECTION_NAME)
    print("\n入库完成。")
    print(f" - records: {len(records)}")
    print(f" - collection_stats: {stats}")
    print("下一步运行：python scripts/test_retrieve.py")


if __name__ == "__main__":
    main()
