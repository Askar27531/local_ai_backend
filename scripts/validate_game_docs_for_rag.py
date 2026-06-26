from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOC_DIR = PROJECT_ROOT / "game_docs"
MANIFEST_PATH = DOC_DIR / "manifest_v03.json"
NPC_MATRIX_PATH = DOC_DIR / "npc_knowledge_matrix_v03.json"

REQUIRE_UNLOCK_HEADING_PREFIXES = (
    "01_",
    "02_",
    "03_",
    "04_",
    "05_",
    "06_",
    "08_",
    "10_",
    "13_",
    "14_",
    "15_",
    "16_",
    "17_",
    "18_",
    "19_",
    "20_",
    "21_",
    "22_",
    "23_",
    "24_",
)

DEVELOPER_SECTION_KEYWORDS = (
    "RAG 使用建议",
    "使用建议",
    "metadata",
    "元数据",
    "Prompt",
    "prompt",
    "开发者",
    "Unity 实现建议",
)

SENSITIVE_TERMS = (
    "Subject 07",
    "低温休眠",
    "保护仓",
    "父母权限",
    "主共振核心",
    "最终真相",
)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
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


def split_markdown_by_h2(text: str) -> list[dict[str, str]]:
    text = text.strip()
    if not text:
        return []

    if "## " not in text:
        title_match = re.search(r"^#\s+(.+)$", text, flags=re.M)
        return [
            {
                "section_title": title_match.group(1).strip() if title_match else "全文",
                "content": text,
            }
        ]

    chunks: list[dict[str, str]] = []
    for part in re.split(r"(?=^##\s+)", text, flags=re.M):
        part = part.strip()
        if not part or part.startswith("# ") and "## " not in part:
            continue
        title_match = re.match(r"^##\s+(.+)$", part, flags=re.M)
        chunks.append(
            {
                "section_title": title_match.group(1).strip() if title_match else "未命名章节",
                "content": part,
            }
        )
    return chunks


def parse_unlock_level(section_title: str, default_level: int) -> int:
    match = re.search(r"解锁\s*(\d+)", section_title)
    return int(match.group(1)) if match else int(default_level)


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    existing_md_files = {
        path.relative_to(DOC_DIR).as_posix()
        for path in DOC_DIR.rglob("*.md")
    }
    ingest_files = set(map(str, manifest.get("ingest_recommended", [])))
    blocked_files = set(map(str, manifest.get("do_not_ingest", [])))

    for file_name in sorted(ingest_files - existing_md_files):
        warnings.append(f"manifest ingest_recommended 指向不存在文件: {file_name}")

    for file_name in sorted(blocked_files - existing_md_files):
        warnings.append(f"manifest do_not_ingest 指向不存在文件: {file_name}")

    for file_name in sorted(ingest_files & blocked_files):
        warnings.append(f"manifest 同时标记入库和不入库: {file_name}")

    return warnings


def validate_npc_matrix(manifest: dict[str, Any], npc_matrix: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    existing_md_files = {path.name for path in DOC_DIR.rglob("*.md")}
    ingest_files = {
        Path(str(file_name)).name
        for file_name in manifest.get("ingest_recommended", [])
    }
    blocked_files = {
        Path(str(file_name)).name
        for file_name in manifest.get("do_not_ingest", [])
    }

    for npc_id, config in npc_matrix.items():
        allowed_sources = config.get("allowed_sources", [])
        if not isinstance(allowed_sources, list):
            warnings.append(f"{npc_id} allowed_sources 不是列表")
            continue

        for source_file in allowed_sources:
            source_file = str(source_file)
            if source_file not in existing_md_files:
                warnings.append(f"{npc_id} allowed_sources 指向不存在文件: {source_file}")
            if source_file in blocked_files:
                warnings.append(f"{npc_id} allowed_sources 包含 do_not_ingest 文件: {source_file}")
            if source_file not in ingest_files:
                warnings.append(f"{npc_id} allowed_sources 包含 manifest 未推荐入库文件: {source_file}")

    return warnings


def validate_markdown_chunks(manifest: dict[str, Any]) -> tuple[list[str], Counter[str], Counter[int]]:
    warnings: list[str] = []
    file_chunk_counts: Counter[str] = Counter()
    unlock_counts: Counter[int] = Counter()

    for file_name in map(str, manifest.get("ingest_recommended", [])):
        path = DOC_DIR / file_name
        if not path.exists():
            continue

        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        if meta.get("rag_ingest") is False:
            warnings.append(f"{file_name} 在 manifest 推荐入库，但 frontmatter rag_ingest=false")
            continue

        default_unlock_level = int(meta.get("default_unlock_level", 0))
        chunks = split_markdown_by_h2(body)
        file_chunk_counts[file_name] = len(chunks)

        for chunk in chunks:
            section_title = chunk["section_title"]
            unlock_level = parse_unlock_level(section_title, default_unlock_level)
            unlock_counts[unlock_level] += 1

            source_name = Path(file_name).name
            if source_name.startswith(REQUIRE_UNLOCK_HEADING_PREFIXES) and not re.search(r"解锁\s*\d+", section_title):
                warnings.append(f"{file_name} / {section_title} 缺少“解锁N”标题标注")

            if any(keyword in section_title for keyword in DEVELOPER_SECTION_KEYWORDS):
                warnings.append(f"{file_name} / {section_title} 像开发说明章节，不建议入库")

            if unlock_level <= 2 and any(term in chunk["content"] for term in SENSITIVE_TERMS):
                warnings.append(f"{file_name} / {section_title} 低解锁章节包含敏感词")

    return warnings, file_chunk_counts, unlock_counts


def print_warnings(title: str, warnings: list[str]) -> None:
    print(f"\n{title}")
    if not warnings:
        print(" - OK")
        return
    for warning in warnings:
        print(f" - WARN: {warning}")


def print_counter(title: str, counter: Counter[Any]) -> None:
    print(f"\n{title}")
    if not counter:
        print(" - 无")
        return
    for key, value in sorted(counter.items(), key=lambda item: str(item[0])):
        print(f" - {key}: {value}")


def main() -> int:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"找不到 manifest：{MANIFEST_PATH}")
    if not NPC_MATRIX_PATH.exists():
        raise FileNotFoundError(f"找不到 NPC 知识矩阵：{NPC_MATRIX_PATH}")

    manifest = read_json(MANIFEST_PATH)
    npc_matrix = read_json(NPC_MATRIX_PATH)

    manifest_warnings = validate_manifest(manifest)
    matrix_warnings = validate_npc_matrix(manifest, npc_matrix)
    chunk_warnings, file_chunk_counts, unlock_counts = validate_markdown_chunks(manifest)

    print_warnings("manifest 检查", manifest_warnings)
    print_warnings("NPC 知识矩阵检查", matrix_warnings)
    print_warnings("Markdown chunk 检查", chunk_warnings)
    print_counter("每个文件的 H2 chunk 数", file_chunk_counts)
    print_counter("unlock_level 分布", unlock_counts)

    total_warnings = len(manifest_warnings) + len(matrix_warnings) + len(chunk_warnings)
    print(f"\nRAG 文档检查完成：warnings={total_warnings}")
    if "--strict" in sys.argv and total_warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
