from __future__ import annotations

from npc_app.services.memory_milvus_service import (
    MEMORY_COLLECTION_NAME,
    create_memory_collection,
    load_memory_collection,
    validate_memory_collection,
)


def main() -> int:
    create_memory_collection()
    load_memory_collection()
    details = validate_memory_collection()
    print(f"Created and loaded {MEMORY_COLLECTION_NAME}: {details}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
