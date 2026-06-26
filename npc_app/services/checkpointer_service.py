from __future__ import annotations

import os
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from npc_app.db.database import NPC_DATABASE_URL

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

    db_uri = os.getenv("NPC_CHECKPOINT_DATABASE_URL") or NPC_DATABASE_URL

    if db_uri.startswith("postgresql"):
        from langgraph.checkpoint.postgres import PostgresSaver

        _checkpoint_cm = PostgresSaver.from_conn_string(_to_psycopg_url(db_uri))
        _checkpointer = _checkpoint_cm.__enter__()
        _checkpointer.setup()
    else:
        _checkpointer = MemorySaver()

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
