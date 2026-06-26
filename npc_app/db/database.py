import os
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "npc_app" / "npc_chat.sqlite3"

NPC_DATABASE_URL = os.getenv(
    "NPC_DATABASE_URL",
    f"sqlite:///{DEFAULT_SQLITE_PATH.as_posix()}",
)

connect_args = {"check_same_thread": False} if NPC_DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    NPC_DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    connect_args=connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from npc_app.db import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite_schema()


def _migrate_sqlite_schema() -> None:
    if not NPC_DATABASE_URL.startswith("sqlite"):
        return

    inspector = inspect(engine)
    if "npc_chat_threads" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("npc_chat_threads")}
    if "npc_id" in columns:
        return

    with engine.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE npc_chat_threads "
                "ADD COLUMN npc_id VARCHAR(50) NOT NULL DEFAULT 'legacy'"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_npc_chat_threads_npc_id "
                "ON npc_chat_threads (npc_id)"
            )
        )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
