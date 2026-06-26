import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


# =========================
# 日志配置
# =========================

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


def write_log(event_type: str, data: Dict[str, Any]) -> None:
    """
    最小日志系统：写入 JSONL。
    后续可以替换为 SQLite、PostgreSQL 或专业日志系统。
    """
    log_path = LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.jsonl"

    record = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "event_type": event_type,
        "data": data,
    }

    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
