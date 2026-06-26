from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from skill_app.config import PROJECT_ROOT

DATASET_ROOT = PROJECT_ROOT / "skill_app" / "evaluations" / "datasets"


def load_jsonl(name: str) -> list[dict[str, Any]]:
    path = DATASET_ROOT / name
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def dataset_digest(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
