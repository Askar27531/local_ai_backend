from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from npc_app.db.database import Base


class NpcUser(Base):
    __tablename__ = "npc_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    threads: Mapped[list["NpcChatThread"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    records: Mapped[list["NpcChatRecord"]] = relationship(back_populates="user")


class NpcChatThread(Base):
    __tablename__ = "npc_chat_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("npc_users.id"),
        nullable=False,
        index=True,
    )

    checkpoint_thread_id: Mapped[str] = mapped_column(
        String(150),
        unique=True,
        index=True,
        nullable=False,
    )

    npc_id: Mapped[str] = mapped_column(String(50), default="legacy", index=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    user: Mapped["NpcUser"] = relationship(back_populates="threads")
    records: Mapped[list["NpcChatRecord"]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
    )
    memory: Mapped["NpcThreadMemory | None"] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
        uselist=False,
    )


class NpcThreadMemory(Base):
    __tablename__ = "npc_thread_memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("npc_users.id"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("npc_chat_threads.id"),
        unique=True,
        nullable=False,
        index=True,
    )
    npc_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    last_record_id: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    thread: Mapped["NpcChatThread"] = relationship(back_populates="memory")


class NpcMemoryItem(Base):
    __tablename__ = "npc_memory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("npc_users.id"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("npc_chat_threads.id"),
        nullable=False,
        index=True,
    )
    npc_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(String(50), default="fact", index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[str] = mapped_column(Text, default="")
    importance: Mapped[int] = mapped_column(Integer, default=3)
    source_record_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class NpcChatRecord(Base):
    __tablename__ = "npc_chat_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("npc_users.id"),
        nullable=False,
        index=True,
    )
    thread_id: Mapped[str] = mapped_column(
        ForeignKey("npc_chat_threads.id"),
        nullable=False,
        index=True,
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    used_search: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    user: Mapped["NpcUser"] = relationship(back_populates="records")
    thread: Mapped["NpcChatThread"] = relationship(back_populates="records")
    sources: Mapped[list["NpcSearchSource"]] = relationship(
        back_populates="chat_record",
        cascade="all, delete-orphan",
    )


class NpcSearchSource(Base):
    __tablename__ = "npc_search_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    chat_id: Mapped[int] = mapped_column(
        ForeignKey("npc_chat_records.id"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")

    chat_record: Mapped["NpcChatRecord"] = relationship(back_populates="sources")
