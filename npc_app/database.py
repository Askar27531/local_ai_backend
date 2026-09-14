"""NPC Runtime 的 PostgreSQL 连接、Session 生命周期与 ORM 模型。

PostgreSQL 是账号、线程、完整问答和长期记忆的业务事实来源；Milvus 仅保存可重建的
语义检索索引。该边界保证向量服务异常时不会丢失已确认业务数据。
"""

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 项目根目录的 .env 是本地 Runtime 配置入口，加载发生在创建 Engine 之前。
load_dotenv(PROJECT_ROOT / ".env")

NPC_DATABASE_URL = os.getenv("NPC_DATABASE_URL", "")
# 生产主线明确只支持 PostgreSQL；启动即失败比静默回退到另一数据库更容易发现配置错误。
if not NPC_DATABASE_URL:
    raise RuntimeError("NPC_DATABASE_URL is required in .env")
if make_url(NPC_DATABASE_URL).get_backend_name() != "postgresql":
    raise RuntimeError("NPC_DATABASE_URL must use PostgreSQL")

engine = create_engine(
    NPC_DATABASE_URL,
    echo=False,
    # 从连接池取出连接时先探测有效性，降低数据库重启后复用失效连接的概率。
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """所有 NPC ORM 模型共享的 SQLAlchemy 声明式基类。"""

    pass


def init_db() -> None:
    """创建缺失的数据表；不会删除、重建或迁移已有表结构。"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """为一次 FastAPI 请求提供 Session，并在请求结束时保证关闭连接。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class NpcUser(Base):
    """认证账号；只保存唯一用户名和不可逆密码哈希。"""

    __tablename__ = "npc_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)


class NpcChatThread(Base):
    """某个用户与某个 NPC 的连续对话边界。"""

    __tablename__ = "npc_chat_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("npc_users.id"), nullable=False, index=True)
    npc_id: Mapped[str] = mapped_column(String(50), index=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class NpcThreadMemory(Base):
    """每条线程唯一的滚动摘要及其已消费到的问答记录位置。"""

    __tablename__ = "npc_thread_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("npc_chat_threads.id"), unique=True, nullable=False, index=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    last_record_id: Mapped[int] = mapped_column(Integer, default=0)


class NpcMemoryItem(Base):
    """从结构化状态或对话中提取的长期记忆业务记录。"""

    __tablename__ = "npc_memory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("npc_chat_threads.id"), nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(String(50), default="fact", index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[str] = mapped_column(Text, default="")
    importance: Mapped[int] = mapped_column(Integer, default=3)


class NpcChatRecord(Base):
    """成功完成并通过答案守卫的完整玩家问题与 NPC 回答。"""

    __tablename__ = "npc_chat_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("npc_chat_threads.id"), nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
