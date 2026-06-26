from __future__ import annotations

import os
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver

from app.db.database import DATABASE_URL

_checkpoint_cm: Any | None = None
_checkpointer: Any | None = None


def _to_psycopg_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return url.replace("postgresql+psycopg://", "postgresql://", 1)
    return url


def init_checkpointer() -> Any:
    global _checkpoint_cm, _checkpointer

    if _checkpointer is not None:
        return _checkpointer

    db_uri = os.getenv("CHECKPOINT_DATABASE_URL") or _to_psycopg_url(DATABASE_URL)

    # PostgresSaver.from_conn_string 是 contextmanager。
    # 这里把 context manager 存成全局变量，让连接生命周期跟 FastAPI 应用一致。
    _checkpoint_cm = PostgresSaver.from_conn_string(db_uri)
    _checkpointer = _checkpoint_cm.__enter__()
    _checkpointer.setup()
    return _checkpointer


def get_checkpointer() -> Any:
    if _checkpointer is None:
        return init_checkpointer()
    return _checkpointer


def close_checkpointer() -> None:
    global _checkpoint_cm, _checkpointer

    if _checkpoint_cm is not None:
        _checkpoint_cm.__exit__(None, None, None)

    _checkpoint_cm = None
    _checkpointer = None
